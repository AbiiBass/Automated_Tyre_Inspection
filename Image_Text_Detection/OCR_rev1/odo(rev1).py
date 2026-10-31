"""
odometer.py

Standalone odometer-reading OCR from a Base64-encoded image.

Input:
    A Base64 image string, either:
      - raw Base64, or
      - a data URL such as: data:image/jpeg;base64,...
    (or, from the command line, a path to an image file)

Output:
    The predicted odometer reading as text, e.g. "12345.6", or an empty
    string if no plausible reading was found.

Required packages:
    pip install paddleocr paddlepaddle opencv-python numpy

Usage:
    python odometer.py                 # prompts for a Base64 string
    python odometer.py photo.jpg       # reads an image file directly
    ODOMETER_DEBUG=1 python odometer.py photo.jpg

Revision history:
    rev1 (2026-10-26)
      - Env vars are now set BEFORE paddleocr is imported (previously they
        were set after the import, so they had no effect).
      - Decimal point is no longer invented for short readings. A tenths
        digit is only implied when 7 digits are read with no visible point
        (e.g. "3346467" -> "334646.7"); 4-6 digits with no point are returned
        as a whole number ("72485" stays "72485", was "7248.5" = 10x wrong).
        Controlled by IMPLIED_TENTHS_MIN_DIGITS.
      - Speedometer / gauge scale labels ("140 160", "50"+"60"+"70") are no
        longer treated as odometer readings (22.jpg returned "14016.0").
      - Short readings (1-3 digits, e.g. a new bus at "22 km") are accepted
        only with direct evidence: an ODO label on the same row or a km/mi
        unit right next to it. Controlled by ALLOW_SHORT_READINGS.
      - Numeric fragments are only merged if their heights are similar, and
        gauge labels are never glued onto stray digits ("160"+"4").
      - Early readings with small digits are re-checked by the zoomed pass,
        and every early reading is verified with a "lit" filter that keeps
        only bright segments (fixes 1->8 misreads caused by faint unlit
        LCD segments, e.g. 091308 read as 098308).
      - Images smaller than MIN_IMAGE_SIDE_PX return "" instead of crashing.
    Public interface unchanged: predict_odometer_reading(base64_string) -> str
"""

import os

# Set these before importing PaddleOCR/PaddleX (they are read at import time).
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

CPU_THREADS = int(os.environ.get("ODOMETER_THREADS", "4"))
os.environ["OMP_NUM_THREADS"] = str(CPU_THREADS)
os.environ["MKL_NUM_THREADS"] = str(CPU_THREADS)
os.environ["OPENBLAS_NUM_THREADS"] = str(CPU_THREADS)

from paddleocr import PaddleOCR, TextRecognition
import numpy as np
import cv2
import re
import sys
import time
import binascii
import base64


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BLUR_THRESHOLD = 100.0

MIN_ODOMETER_DIGITS = 4
MAX_ODOMETER_DIGITS = 7

# rev1: when NO decimal point is read, a tenths digit is assumed only if the
# reading has at least this many digits. Bus odometers top out at 6 whole
# digits, so 7 digits without a point = 6 digits + tenths. Set to 4 to get
# the old behaviour (always insert a point before the last digit).
IMPLIED_TENTHS_MIN_DIGITS = 7

# rev1: accept 1-3 digit readings (new vehicles) when an ODO label or a
# km/mi unit sits right next to them on the same row.
ALLOW_SHORT_READINGS = True
SHORT_READING_MIN_SCORE = 0.80

TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

# Pass 1: whole-frame scan, used to LOCATE the odometer.
PASS1_TARGET = 900
PASS1_PAD = 30

# Whole-frame scan plan, cheapest and most-often-successful first. Scanning
# stops at the first STRONG reading, and results from all scans so far are
# pooled otherwise. Measured on real photos: seven-segment digits read best
# when they are ~25-60 px tall. A close-up (digits 120-150 px in a ~950 px
# photo) reads perfectly at 240-500 px but fails at 900 (and a noisy 900 px
# scan can take 9s+); a wide dashboard shot (digits ~30 px in a 2048 px photo)
# needs 900. So: small scales first, 900 only if they fail.
# Modes: "strong", "mild", "none", "smooth" (grayscale + blur, removes moire
# and colour fringing), "smooth_inv" (same, inverted to dark-on-light).
SCAN_PLAN = [
    (400, "mild"),
    (300, "none"),
    (500, "smooth"),
    (900, "strong"),
    (600, "smooth_inv"),
    (400, "smooth_inv"),
]

# Quality gates. A wrong answer is worse than an empty one.
MIN_TOKEN_SCORE = 0.15        # tokens below this are dropped as detector noise
MIN_CANDIDATE_SCORE = 0.30    # a reading must be at least this confident to compete
MIN_ACCEPT_CONFIDENCE = 0.50  # final answer below this is returned as ""
MIN_DIGIT_HEIGHT_PX = 10.0    # ignore digit-height estimates below this
MIN_IMAGE_SIDE_PX = 16        # rev1: smaller images return "" immediately

# Pass 2: crop around the odometer and rescale so digits are about this tall.
TARGET_DIGIT_HEIGHT = 45.0
ZOOM_MIN_LONG_SIDE = 300
ZOOM_MAX_LONG_SIDE = 900
ZOOM_PAD = 30

# Tile scan (last resort). Tiles are never upscaled beyond this size.
TILE_TARGET_SIDE = 900
TILE_PAD = 30

# Row merging: join digit fragments such as "64" + "1447" + "7".
MERGE_MAX_GAP_FRAC = 1.5     # max horizontal gap, as a multiple of box height
MERGE_MAX_DY_FRAC = 0.4      # max vertical center offset, same units
MERGE_MAX_HEIGHT_RATIO = 1.5  # rev1: fragments must have similar heights

# rev1: an early "strong" reading whose digits are shorter than this (in the
# scanned image) is re-read with the zoomed pass 2 before being accepted.
# Small seven-segment digits are where 1<->8 / 0<->8 misreads happen.
MIN_TRUSTED_DIGIT_PX = 24.0

# rev1: "lit" verification of early readings (see verify_with_lit).
VERIFY_WITH_LIT = True
LIT_PERCENTILE = 85          # keep the brightest 15% of pixels
LIT_MIN_CONFIDENCE = 0.85

# Set ODOMETER_DEBUG=1 to print per-pass diagnostics and per-call timings.
ODOMETER_DEBUG = os.environ.get("ODOMETER_DEBUG") == "1"

ODO_LABEL_PROXIMITY_WEIGHT = 50.0
ODO_LABEL_PROXIMITY_RADIUS = 220.0   # pixels, in the padded processing image
NEGATIVE_LABEL_PENALTY_WEIGHT = 50.0
NEGATIVE_LABEL_PROXIMITY_RADIUS = 220.0
UNIT_SUFFIX_BONUS = 25.0
MERGED_LABEL_BONUS = 40.0
DIGIT_LENGTH_BONUS = 12.0
DIGIT_COUNT_BONUS = 3.0              # per digit; prefers fuller readings
CONFIDENCE_WEIGHT = 20.0
MIN_PLAUSIBLE_SCORE = 0.0


def dbg(msg):
    if ODOMETER_DEBUG:
        print(f"[odometer debug] {msg}")


def fmt_pairs(texts, scores):
    return list(zip(texts, [round(float(s), 3) for s in scores]))


# Detection + recognition pipeline.
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
    cpu_threads=CPU_THREADS,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
)

# Wrap predict() so ODOMETER_DEBUG=1 shows how long each call takes.
_raw_predict = ocr.predict


def _timed_predict(*args, **kwargs):
    t0 = time.perf_counter()
    out = _raw_predict(*args, **kwargs)
    dbg(f"predict took {time.perf_counter() - t0:.2f}s")
    return out


ocr.predict = _timed_predict


_recognizer = None


def get_recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = TextRecognition()
    return _recognizer


def decode_base64_image(base64_string: str) -> bytes:
    """Convert a raw Base64 string or data URL into image bytes."""
    base64_string = base64_string.strip()

    if "," in base64_string:
        base64_string = base64_string.split(",", 1)[1]

    base64_string = re.sub(r"\s+", "", base64_string)
    base64_string = base64_string.replace("-", "+").replace("_", "/")
    base64_string += "=" * (-len(base64_string) % 4)

    try:
        image_bytes = base64.b64decode(base64_string)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid Base64 image string: {exc}") from exc

    if not image_bytes:
        raise ValueError("Invalid Base64 image string: decoded to zero bytes.")

    return image_bytes


def parse_pipeline_result(result):
    texts, scores, boxes = [], [], []
    for page in result:
        page_texts = page.get("rec_texts", []) or []
        page_scores = page.get("rec_scores", []) or []
        page_boxes = page.get("rec_boxes", None)
        if page_boxes is None or len(page_boxes) == 0:
            page_boxes = [None] * len(page_texts)
        texts.extend(page_texts)
        scores.extend(page_scores)
        boxes.extend([tuple(float(v) for v in b)
                     if b is not None else None for b in page_boxes])
    return texts, scores, boxes


def parse_recognition_only_result(result):
    texts, scores = [], []
    for page in result:
        text = page.get("rec_text", "")
        if text:
            texts.append(text)
            scores.append(page.get("rec_score", 0.0))
    return texts, scores


def assess_blur(gray_img):
    return cv2.Laplacian(gray_img, cv2.CV_64F).var()


def enhance_image(img_bgr, mode="strong"):
    """
    mode:
      "strong" - original behavior (bilateral + CLAHE 3.0 + unsharp mask)
      "mild"   - light denoise + gentle CLAHE, no sharpening. Better for
                 already-sharp, grainy images where strong enhancement
                 amplifies texture to the same contrast as the digits.
      "none"   - return the image untouched
    """
    if mode == "none":
        return img_bgr

    if mode == "lit":
        # rev1: keep only the brightest pixels (lit LCD/LED segments) and
        # draw them dark-on-white. Removes faint unlit "ghost" segments that
        # make a 1 read as 8 on seven-segment displays.
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        thr = np.percentile(gray, LIT_PERCENTILE)
        lit = np.where(gray > thr, 0, 255).astype(np.uint8)
        return cv2.cvtColor(lit, cv2.COLOR_GRAY2BGR)

    if mode in ("smooth", "smooth_inv"):
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.2)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(4, 4))
        gray = clahe.apply(gray)
        if mode == "smooth_inv":
            gray = 255 - gray
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    if mode == "mild":
        denoised = cv2.bilateralFilter(
            img_bgr, d=5, sigmaColor=50, sigmaSpace=50)
        lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l_channel)
        return cv2.cvtColor(
            cv2.merge((l_enhanced, a_channel, b_channel)), cv2.COLOR_LAB2BGR)

    denoised = cv2.bilateralFilter(img_bgr, d=9, sigmaColor=75, sigmaSpace=75)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)
    enhanced = cv2.cvtColor(
        cv2.merge((l_enhanced, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
    gaussian = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
    return sharpened


def last_resort_binarize(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=31, C=15,
    )
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    return cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGR)


def resize_to_long_side(img_bgr, target_long_side):
    h, w = img_bgr.shape[:2]
    long_side = max(h, w)
    scale = target_long_side / float(long_side)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interp), scale


def resize_crop_for_zoom(crop_bgr, min_long_side, max_long_side):
    """Fallback zoom rule used when digit height is unknown."""
    h, w = crop_bgr.shape[:2]
    long_side = max(h, w)
    if long_side < min_long_side:
        scale = min_long_side / float(long_side)
        interp = cv2.INTER_CUBIC
    elif long_side > max_long_side:
        scale = max_long_side / float(long_side)
        interp = cv2.INTER_AREA
    else:
        return crop_bgr
    return cv2.resize(crop_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interp)


def pad_white(img, pad):
    return cv2.copyMakeBorder(
        img, pad, pad, pad, pad,
        borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
    )


def box_center(box):
    left, top, right, bottom = box
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def normalize_label(text):
    return re.sub(r"[^a-z0-9]+$", "", text.strip().lower())


def is_positive_label(text):
    return re.fullmatch(r"[o0]d[o0](meter)?", normalize_label(text)) is not None


def is_negative_label(text):
    if is_positive_label(text):
        return False
    return re.search(r"trip|avg\.?|disp|range|dte|remain", text.strip().lower()) is not None


def is_temperature_like(text):
    return re.fullmatch(r"-?\d{1,3}\s*[\u00b0]?\s*[cf]", text.strip(), re.IGNORECASE) is not None


def looks_like_clock(text):
    return re.search(r"\d{1,2}:\d{2}(:\d{2})?", text) is not None


def looks_garbled(text):
    stripped = re.sub(
        r"km\.?|kms\.?|mi\.?|miles?|[o0]d[o0](meter)?", "", text, flags=re.IGNORECASE)
    if not re.search(r"\d", stripped):
        return False
    return re.search(r"[a-zA-Z]", stripped) is not None


def is_gauge_fragment(text):
    """rev1: a 2-3 digit number ending in 0, like speedometer labels 20..180."""
    return re.fullmatch(r"\d{1,2}0", text.strip()) is not None


def looks_like_gauge_scale(text):
    """
    rev1: two or more separate numbers that are all gauge labels, e.g.
    "140 160" or "180.110". These come from the speedometer dial, not the
    odometer, and used to be read as "14016.0".
    """
    groups = re.findall(r"\d+", text)
    return len(groups) >= 2 and all(is_gauge_fragment(g) for g in groups)


def is_excluded_reading(text):
    if looks_like_clock(text):
        return True
    if is_temperature_like(text):
        return True
    if looks_like_gauge_scale(text):
        return True
    if looks_garbled(text):
        return True
    lower = text.lower()
    return any(marker in lower for marker in ("trip", "avg", "disp", "/100", "l/1", "/h", "range", "dte"))


def normalize_odometer_reading(text):
    stripped = re.sub(r"[o0]d[o0](meter)?", "", text, flags=re.IGNORECASE)
    kept = re.sub(r"[^0-9.]", "", stripped.replace(",", ".")).strip(".")
    digits = kept.replace(".", "")
    if not (MIN_ODOMETER_DIGITS <= len(digits) <= MAX_ODOMETER_DIGITS):
        return None
    if "." in kept:
        whole, _, tenths = kept.rpartition(".")
        # rev1: odometers show at most ONE decimal digit; a point with 2+
        # digits after it is a misread separator -> treat as no point.
        if len(tenths) == 1:
            return f"{whole.replace('.', '')}.{tenths}"
    # rev1: only imply a tenths digit for long readings (see config).
    if len(digits) >= IMPLIED_TENTHS_MIN_DIGITS:
        return f"{digits[:-1]}.{digits[-1]}"
    return digits


def is_unit_only_label(text):
    return re.fullmatch(r"km\.?|kms\.?|mi\.?|miles?", text.strip(), re.IGNORECASE) is not None


def has_unit_suffix(text):
    # "125km/h" is a speed, not an odometer unit
    return re.search(r"\d\s*(km|mi|miles?)\b(?!\s*/)", text.lower()) is not None


def has_merged_positive_label(text):
    alnum = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(re.search(r"[o0]d[o0]", alnum)) and bool(re.search(r"\d", alnum))


def select_odometer_reading(texts, scores, boxes=None):
    n = len(texts)
    boxes = list(boxes) if boxes is not None else [None] * n

    positive_anchors = [
        box_center(box) for text, box in zip(texts, boxes)
        if box is not None and is_positive_label(text)
    ]
    negative_anchors = [
        box_center(box) for text, box in zip(texts, boxes)
        if box is not None and is_negative_label(text)
    ]

    candidates = []
    for text, score, box in zip(texts, scores, boxes):
        if is_excluded_reading(text) or is_positive_label(text) or is_negative_label(text):
            continue
        value = normalize_odometer_reading(text)
        if value is None:
            # not 4-7 digits (decimal point not counted)
            continue
        if score < MIN_CANDIDATE_SCORE:
            continue

        n_digits = len(value.replace(".", ""))
        points = score * CONFIDENCE_WEIGHT + \
            DIGIT_LENGTH_BONUS + DIGIT_COUNT_BONUS * n_digits
        has_direct_signal = False

        if has_merged_positive_label(text):
            points += MERGED_LABEL_BONUS
            has_direct_signal = True
        if has_unit_suffix(text):
            points += UNIT_SUFFIX_BONUS
            has_direct_signal = True

        if box is not None:
            cx, cy = box_center(box)
            if positive_anchors:
                nearest = min(((cx - ox) ** 2 + (cy - oy) ** 2)
                              ** 0.5 for ox, oy in positive_anchors)
                points += max(
                    0.0, 1.0 - nearest / ODO_LABEL_PROXIMITY_RADIUS) * ODO_LABEL_PROXIMITY_WEIGHT
            if negative_anchors:
                nearest_neg = min(((cx - ox) ** 2 + (cy - oy) ** 2)
                                  ** 0.5 for ox, oy in negative_anchors)
                points -= max(0.0, 1.0 - nearest_neg /
                              NEGATIVE_LABEL_PROXIMITY_RADIUS) * NEGATIVE_LABEL_PENALTY_WEIGHT

        candidates.append((points, score, value, has_direct_signal, box))

    # rev1: no 4-7 digit reading at all -> look for a short (1-3 digit)
    # reading with direct ODO / unit evidence, e.g. a new bus at "22 km".
    # Never "strong", so the scan plan keeps looking for a fuller reading.
    if not candidates and ALLOW_SHORT_READINGS:
        short = select_short_reading(texts, scores, boxes, negative_anchors)
        if short is not None:
            value, score, box = short
            return value, score, box, False

    if not candidates:
        return None, 0.0, None, False

    if len(candidates) == 1:
        _, best_score, best_value, best_direct, best_box = candidates[0]
        is_strong = (best_direct and best_score >= 0.7) or best_score >= 0.85
        return best_value, best_score, best_box, is_strong

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_points, best_score, best_value, best_direct, best_box = candidates[0]
    if best_points < MIN_PLAUSIBLE_SCORE:
        return None, 0.0, None, False
    is_strong = best_direct and best_score >= 0.7
    return best_value, best_score, best_box, is_strong


def select_short_reading(texts, scores, boxes, negative_anchors):
    """
    rev1: return (value, score, box) for a 1-3 digit odometer reading that
    has direct evidence, or None. Evidence is one of:
      - the token itself carries the label/unit ("ODO 22", "22km"), or
      - an ODO label or km/mi unit box on the same row, close by.
    A reading near a TRIP/AVG/DTE label is never accepted.
    """
    best = None
    for text, score, box in zip(texts, scores, boxes):
        if score < SHORT_READING_MIN_SCORE or is_excluded_reading(text):
            continue
        if is_negative_label(text):
            continue
        core = re.sub(r"[o0]d[o0](meter)?|kms?\.?|mi(les?)?\.?", "", text, flags=re.IGNORECASE)
        core = core.strip().strip(".")
        if not re.fullmatch(r"\d{1,3}(\.\d)?", core):
            continue

        evidence = has_merged_positive_label(text) or has_unit_suffix(text)
        if box is not None and not evidence:
            cx, cy = box_center(box)
            h = max(box[3] - box[1], 1.0)
            for t2, b2 in zip(texts, boxes):
                if b2 is None or b2 is box:
                    continue
                if not (is_positive_label(t2) or is_unit_only_label(t2)):
                    continue
                cx2, cy2 = box_center(b2)
                if abs(cy2 - cy) <= 0.6 * h and abs(cx2 - cx) <= 8.0 * h:
                    evidence = True
                    break
        if not evidence:
            continue

        # A TRIP/AVG/DTE label on the SAME row means this is not the odometer
        # (labels on the row above/below, e.g. "AVG." over "ODO 22km", are ok).
        if box is not None and negative_anchors:
            cx, cy = box_center(box)
            h = max(box[3] - box[1], 1.0)
            if any(abs(oy - cy) <= 0.6 * h and abs(ox - cx) <= 8.0 * h
                   for ox, oy in negative_anchors):
                continue

        if best is None or score > best[1]:
            best = (core, float(score), box)
    return best


def is_numeric_fragment(text):
    t = text.strip()
    return re.fullmatch(r"[0-9.,\s]+", t) is not None and re.search(r"\d", t) is not None


def merge_row_tokens(texts, scores, boxes):
    """
    Join numeric fragments that sit on the same row with small gaps, e.g.
    "64" + "1447" + "7" -> "6414477". The merged tokens are APPENDED; the
    original tokens are kept too. Call this once per OCR run (never on
    results pooled from several runs, or the same digits could be joined
    twice).
    """
    idx = [
        i for i, (t, b) in enumerate(zip(texts, boxes))
        if b is not None and is_numeric_fragment(t)
    ]
    idx.sort(key=lambda i: boxes[i][0])

    groups = []
    for i in idx:
        b = boxes[i]
        placed = False
        for g in groups:
            lb = boxes[g[-1]]
            h = max(lb[3] - lb[1], b[3] - b[1])
            if h <= 0:
                continue
            dy = abs(box_center(lb)[1] - box_center(b)[1])
            gap = b[0] - lb[2]
            # rev1: odometer digits share one font size; fragments whose box
            # heights differ a lot come from different parts of the dash.
            h_small = min(lb[3] - lb[1], b[3] - b[1])
            similar = h_small > 0 and h / h_small <= MERGE_MAX_HEIGHT_RATIO
            if similar and dy <= MERGE_MAX_DY_FRAC * h and -0.5 * h <= gap <= MERGE_MAX_GAP_FRAC * h:
                g.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])

    out_t, out_s, out_b = list(texts), list(scores), list(boxes)
    for g in groups:
        if len(g) < 2:
            continue
        # rev1: never glue speedometer labels together ("50"+"60"+"70"), and
        # never glue a gauge label onto a stray digit ("160"+"4" -> "1604").
        if all(is_gauge_fragment(texts[i]) for i in g):
            continue
        n_digits = sum(len(re.sub(r"\D", "", texts[i])) for i in g)
        if n_digits < 6 and any(is_gauge_fragment(texts[i]) for i in g):
            continue
        merged_text = "".join(texts[i].strip() for i in g)
        merged_score = float(sum(scores[i] for i in g)) / len(g)
        lefts, tops, rights, bottoms = zip(*(boxes[i] for i in g))
        out_t.append(merged_text)
        out_s.append(merged_score)
        out_b.append((min(lefts), min(tops), max(rights), max(bottoms)))
    return out_t, out_s, out_b


def is_near_miss_reading(text):
    if looks_like_clock(text) or is_temperature_like(text):
        return False
    lower = text.lower()
    if any(m in lower for m in ("trip", "avg", "disp", "/100", "l/1", "/h", "range", "dte")):
        return False
    digits = re.sub(r"\D", "", text)
    return 4 <= len(digits) <= 8


def find_odometer_roi(texts, scores, boxes, padding_frac=0.8, min_padding_px=20):
    anchor_boxes = [
        box for text, box in zip(texts, boxes)
        if box is not None and (
            is_positive_label(text) or is_unit_only_label(text)
            or has_unit_suffix(text) or has_merged_positive_label(text)
        )
    ]

    _, _, candidate_box, _ = select_odometer_reading(texts, scores, boxes)
    if candidate_box is not None:
        anchor_boxes.append(candidate_box)

    # Fallback 1: zoom on any digit-heavy token that was rejected as a value.
    # Junk tokens (very low confidence) are ignored.
    if not anchor_boxes:
        anchor_boxes = [
            box for text, score, box in zip(texts, scores, boxes)
            if box is not None and score >= MIN_CANDIDATE_SCORE
            and is_near_miss_reading(text)
        ]

    # Fallback 2: the single most confident box containing a digit (e.g. a
    # lone "3" read at 0.74). Digits of an odometer sit side by side, so the
    # crop is stretched far to the left and right of that box.
    if not anchor_boxes:
        digit_boxes = [
            (score, box) for text, score, box in zip(texts, scores, boxes)
            if box is not None and score >= MIN_CANDIDATE_SCORE
            and re.search(r"\d", text)
            and not looks_like_clock(text) and not is_temperature_like(text)
        ]
        if not digit_boxes:
            return None
        _, (l, t, r, b) = max(digit_boxes, key=lambda x: x[0])
        pad_x = 8.0 * max(r - l, (b - t) * 0.5)
        pad_y = max((b - t) * 0.8, min_padding_px)
        return (l - pad_x, t - pad_y, r + pad_x, b + pad_y)

    if not anchor_boxes:
        return None

    lefts, tops, rights, bottoms = zip(*anchor_boxes)
    left, top, right, bottom = min(lefts), min(tops), max(rights), max(bottoms)
    pad_x = max((right - left) * padding_frac, min_padding_px)
    pad_y = max((bottom - top) * padding_frac, min_padding_px)
    return (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)


def estimate_digit_height(texts, boxes, scale):
    """
    Median height (in ORIGINAL-image pixels) of boxes that contain at least
    two digits, or None if there are none. `scale` converts original pixels
    to the coordinate space the boxes live in.
    """
    heights = []
    for text, box in zip(texts, boxes):
        if box is None:
            continue
        if len(re.sub(r"\D", "", text)) < 2:
            continue
        if is_temperature_like(text) or looks_like_clock(text):
            continue
        heights.append(box[3] - box[1])
    if not heights:
        return None
    est = float(np.median(heights)) / scale
    return est if est >= MIN_DIGIT_HEIGHT_PX else None


def ocr_full_frame(img, target, mode, pass1_scale, allow_recognizer_fallback=False):
    """
    Run OCR on the whole frame resized to `target` long side. Boxes are mapped
    into the canonical coordinate space (the PASS1_TARGET-sized image plus
    PASS1_PAD padding), so results from different scales can be pooled and
    fed to find_odometer_roi.
    """
    resized, sc = resize_to_long_side(img, target)
    padded = pad_white(enhance_image(resized, mode), PASS1_PAD)

    texts, scores, boxes = parse_pipeline_result(ocr.predict(padded))

    if allow_recognizer_fallback:
        if not texts:
            rec_result = get_recognizer().predict(padded)
            texts, scores = parse_recognition_only_result(rec_result)
            boxes = [None] * len(texts)
        if not texts:
            binarized = last_resort_binarize(padded)
            rec_result = get_recognizer().predict(binarized)
            texts, scores = parse_recognition_only_result(rec_result)
            boxes = [None] * len(texts)

    k = pass1_scale / sc
    mapped = []
    for box in boxes:
        if box is None:
            mapped.append(None)
        else:
            mapped.append(tuple((v - PASS1_PAD) * k + PASS1_PAD for v in box))

    keep = [i for i, (t, s) in enumerate(zip(texts, scores))
            if t.strip() and s >= MIN_TOKEN_SCORE]
    if len(keep) != len(texts):
        dbg(f"dropped {len(texts) - len(keep)} junk tokens (score < {MIN_TOKEN_SCORE})")
    texts = [texts[i] for i in keep]
    scores = [scores[i] for i in keep]
    mapped = [mapped[i] for i in keep]

    texts, scores, mapped = merge_row_tokens(texts, scores, mapped)
    dbg(f"scan @{target}px/{mode}: {fmt_pairs(texts, scores)}")
    return texts, scores, mapped


def scan_tiles(img, pass1_scale, pass1_pad, tile_frac=0.5, overlap=0.4):
    h, w = img.shape[:2]
    tile = int(max(h, w) * tile_frac)
    tile_w, tile_h = min(tile, w), min(tile, h)

    def starts(full, size):
        step = max(1, int(size * (1 - overlap)))
        pos = list(range(0, full - size + 1, step))
        if pos[-1] != full - size:
            pos.append(full - size)
        return pos

    cx, cy = w / 2.0, h / 2.0
    origins = sorted(
        ((x0, y0) for y0 in starts(h, tile_h) for x0 in starts(w, tile_w)),
        key=lambda o: (o[0] + tile_w / 2.0 - cx) ** 2 +
        (o[1] + tile_h / 2.0 - cy) ** 2,
    )

    all_texts, all_scores, all_boxes = [], [], []
    votes = {}
    for x0, y0 in origins:
        crop = img[y0:y0 + tile_h, x0:x0 + tile_w]
        # Never upscale a tile: upscaling is what makes seven-segment digits
        # fall apart. Only shrink when the tile is larger than the target.
        target = min(TILE_TARGET_SIDE, max(crop.shape[:2]))
        crop, s = resize_to_long_side(crop, target)
        padded = pad_white(enhance_image(crop, "mild"), TILE_PAD)
        texts, scores, boxes = parse_pipeline_result(ocr.predict(padded))
        mapped = []
        for box in boxes:
            if box is not None:
                l, t, r, b = box
                box = (
                    ((l - TILE_PAD) / s + x0) * pass1_scale + pass1_pad,
                    ((t - TILE_PAD) / s + y0) * pass1_scale + pass1_pad,
                    ((r - TILE_PAD) / s + x0) * pass1_scale + pass1_pad,
                    ((b - TILE_PAD) / s + y0) * pass1_scale + pass1_pad,
                )
            mapped.append(box)

        texts, scores, mapped = merge_row_tokens(texts, scores, mapped)
        all_texts.extend(texts)
        all_scores.extend(scores)
        all_boxes.extend(mapped)

        value, _, _, _ = select_odometer_reading(texts, scores, mapped)
        if value is not None:
            votes[value] = votes.get(value, 0) + 1
            if votes[value] >= 2:   # two independent tiles agree -> stop
                break
    return all_texts, all_scores, all_boxes


def zoom_targets_for_crop(crop, digit_height_orig):
    """Candidate long-side sizes for the ROI crop, best guess first."""
    long_side = max(crop.shape[:2])
    if digit_height_orig and digit_height_orig > 0:
        primary = long_side * TARGET_DIGIT_HEIGHT / digit_height_orig
        primary = int(
            min(max(primary, ZOOM_MIN_LONG_SIDE), ZOOM_MAX_LONG_SIDE))
        alternate = int(max(primary * 0.6, 250))
        return [primary] if alternate >= primary else [primary, alternate]
    # unknown digit size: original 650-900 rule, plus a smaller alternative
    fallback = int(min(max(long_side, 650), ZOOM_MAX_LONG_SIDE))
    return [fallback, 400]


def run_pass2(img, roi, pass1_scale, texts, boxes):
    orig_h, orig_w = img.shape[:2]
    l, t, r, b = roi
    orig_l = max(0, (l - PASS1_PAD) / pass1_scale)
    orig_t = max(0, (t - PASS1_PAD) / pass1_scale)
    orig_r = min(orig_w, (r - PASS1_PAD) / pass1_scale)
    orig_b = min(orig_h, (b - PASS1_PAD) / pass1_scale)

    if orig_r - orig_l < 4 or orig_b - orig_t < 4:
        return None, 0.0

    crop = img[int(orig_t):int(orig_b), int(orig_l):int(orig_r)]
    digit_h = estimate_digit_height(texts, boxes, pass1_scale)
    dbg(f"pass2 crop={crop.shape[1]}x{crop.shape[0]} est. digit height={digit_h}")

    for target in zoom_targets_for_crop(crop, digit_h):
        zoomed, _ = resize_to_long_side(crop, target)
        # mild first (strong sharpening just amplifies grain); then the
        # smoothed + inverted variant for moire / colour-fringed displays
        for mode in ("mild", "smooth_inv"):
            padded = pad_white(enhance_image(zoomed, mode), ZOOM_PAD)
            texts2, scores2, boxes2 = parse_pipeline_result(
                ocr.predict(padded))
            texts2, scores2, boxes2 = merge_row_tokens(texts2, scores2, boxes2)
            dbg(f"pass2 @{target}px/{mode}: {fmt_pairs(texts2, scores2)}")
            if not texts2:
                continue
            value2, conf2, _box2, _strong2 = select_odometer_reading(
                texts2, scores2, boxes2)
            if value2 is not None:
                return value2, conf2
    return None, 0.0


def verify_with_lit(img, box, pass1_scale, value):
    """
    rev1: re-read the area around an early reading with the "lit" filter.
    Returns (value, confidence) to use instead, or None to keep the original.
    The lit reading only wins if it is confident AND has the same number of
    digits (it fixes look-alike digits; it never adds/drops digits).
    """
    if box is None or value is None:
        return None
    h_img, w_img = img.shape[:2]
    l, t, r, b = ((v - PASS1_PAD) / pass1_scale for v in box)
    dh = max(b - t, 1.0)
    l, r = max(0, int(l - 0.6 * dh)), min(w_img, int(r + 0.6 * dh))
    t, b = max(0, int(t - 0.5 * dh)), min(h_img, int(b + 0.5 * dh))
    if r - l < 8 or b - t < 8:
        return None
    crop = img[t:b, l:r]
    target = max(crop.shape[:2]) * TARGET_DIGIT_HEIGHT / dh
    target = int(min(max(target, 250), ZOOM_MAX_LONG_SIDE))
    zoomed, _ = resize_to_long_side(crop, target)
    padded = pad_white(enhance_image(zoomed, "lit"), ZOOM_PAD)
    t2, s2, b2 = parse_pipeline_result(ocr.predict(padded))
    t2, s2, b2 = merge_row_tokens(t2, s2, b2)
    dbg(f"lit verify @{target}px: {fmt_pairs(t2, s2)}")
    v2, c2, _, _ = select_odometer_reading(t2, s2, b2)
    if v2 is None or v2 == value or c2 < LIT_MIN_CONFIDENCE:
        return None
    if len(v2.replace(".", "")) != len(value.replace(".", "")):
        return None
    return v2, c2


def predict_odometer_reading(base64_string: str) -> str:
    image_bytes = decode_base64_image(base64_string)
    return predict_from_bytes(image_bytes)


def predict_from_bytes(image_bytes: bytes) -> str:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(
            "The image data was read, but it is not a valid supported image."
        )

    h, w = img.shape[:2]
    # rev1: images too small to hold a readable display (e.g. 1x1) used to
    # crash with ZeroDivisionError further down.
    if min(h, w) < MIN_IMAGE_SIDE_PX:
        dbg(f"image too small ({w}x{h}) -> no reading")
        return ""
    pass1_scale = PASS1_TARGET / float(max(h, w))

    if ODOMETER_DEBUG:
        pass1_img, _ = resize_to_long_side(img, PASS1_TARGET)
        blur_score = assess_blur(cv2.cvtColor(pass1_img, cv2.COLOR_BGR2GRAY))
        dbg(f"blur score: {blur_score:.1f} "
            f"({'BLURRY' if blur_score < BLUR_THRESHOLD else 'ok'})")

    # Pass 1: whole-frame scans (see SCAN_PLAN), stopping at the first strong
    # reading and pooling everything seen so far otherwise.
    texts, scores, boxes = [], [], []
    value1, conf1, box1, is_strong = None, 0.0, None, False
    used_scan = None
    for target, mode in SCAN_PLAN:
        t_x, s_x, b_x = ocr_full_frame(
            img, target, mode, pass1_scale,
            allow_recognizer_fallback=(target == PASS1_TARGET))
        texts += t_x
        scores += s_x
        boxes += b_x
        value1, conf1, box1, is_strong = select_odometer_reading(
            texts, scores, boxes)
        if is_strong:
            # rev1: small digits -> stop scanning but verify with pass 2.
            if box1 is not None:
                digit_px = (box1[3] - box1[1]) * target / float(PASS1_TARGET)
                if digit_px < MIN_TRUSTED_DIGIT_PX:
                    dbg(f"strong reading {value1!r} has small digits "
                        f"({digit_px:.1f}px @{target}) -> verify with pass2")
                    is_strong = False
                    break
            used_scan = (target, mode)
            break
    if not is_strong:
        dbg(
            f"no strong reading after scan plan (value={value1!r}, conf={conf1:.3f})")

    value, confidence = None, 0.0
    used_pass2 = False
    used_pass3 = False

    if is_strong:
        value, confidence = value1, conf1
        if VERIFY_WITH_LIT:
            fixed = verify_with_lit(img, box1, pass1_scale, value1)
            if fixed is not None:
                dbg(f"lit verify replaced {value1!r} with {fixed[0]!r}")
                value, confidence = fixed
    else:
        roi = find_odometer_roi(texts, scores, boxes)
        dbg(
            f"pooled result not strong (value={value1!r}, conf={conf1:.3f}); roi={roi}")

        if roi is not None:
            value2, conf2 = run_pass2(img, roi, pass1_scale, texts, boxes)
            if value2 is not None:
                value, confidence = value2, conf2
                used_pass2 = True

        if value is None:
            value, confidence = value1, conf1

        if value is None:
            texts3, scores3, boxes3 = scan_tiles(img, pass1_scale, PASS1_PAD)
            dbg(f"pass3 (tiles) texts: {fmt_pairs(texts3, scores3)}")
            value3, conf3, _box3, _strong3 = select_odometer_reading(
                texts3, scores3, boxes3)
            if value3 is not None:
                value, confidence = value3, conf3
                used_pass3 = True

    dbg(f"scan={used_scan} used_pass2={used_pass2} "
        f"used_pass3={used_pass3} final value={value!r} confidence={confidence:.3f}")
    if value is not None and confidence < MIN_ACCEPT_CONFIDENCE:
        dbg(f"rejecting low-confidence reading {value!r} "
            f"({confidence:.3f} < {MIN_ACCEPT_CONFIDENCE})")
        return ""
    return value if value is not None else ""


def main():
    try:
        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as f:
                prediction = predict_from_bytes(f.read())
        else:
            base64_string = input("Enter Base64 image string: ").strip()
            prediction = predict_odometer_reading(base64_string)
        print(prediction)
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
