"""
odometer.py

Standalone odometer-reading OCR from a Base64-encoded image.

Input:
    A Base64 image string, either:
      - raw Base64, or
      - a data URL such as: data:image/jpeg;base64,...

Output:
    The predicted odometer reading as text, e.g. "12345.6", or an empty
    string if no plausible reading was found.

Required packages:
    pip install paddleocr paddlepaddle opencv-python numpy
"""

from paddleocr import PaddleOCR, TextRecognition
import numpy as np
import cv2
import time
import re
import binascii
import base64
import os

# must be set before paddleocr/paddlex is imported
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

# Number of CPU threads used for inference.
CPU_THREADS = int(os.environ.get("ODOMETER_THREADS", "4"))
os.environ["OMP_NUM_THREADS"] = str(CPU_THREADS)
os.environ["MKL_NUM_THREADS"] = str(CPU_THREADS)
os.environ["OPENBLAS_NUM_THREADS"] = str(CPU_THREADS)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BLUR_THRESHOLD = 100.0

MIN_ODOMETER_DIGITS = 4
MAX_ODOMETER_DIGITS = 7

TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

TILE_TARGET_SIDE = 900
TILE_PAD = 30

# Set ODOMETER_DEBUG=1 to print per-pass diagnostics and per-call timings.
ODOMETER_DEBUG = os.environ.get("ODOMETER_DEBUG") == "1"


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
    if ODOMETER_DEBUG:
        print(f"[odometer debug] predict took {time.perf_counter() - t0:.2f}s")
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


def enhance_image(img_bgr):
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


# ---------------------------------------------------------------------------
# Odometer reading selection
# ---------------------------------------------------------------------------

ODO_LABEL_PROXIMITY_WEIGHT = 50.0
ODO_LABEL_PROXIMITY_RADIUS = 220.0   # pixels, in the padded processing image
NEGATIVE_LABEL_PENALTY_WEIGHT = 50.0
NEGATIVE_LABEL_PROXIMITY_RADIUS = 220.0
UNIT_SUFFIX_BONUS = 25.0
MERGED_LABEL_BONUS = 40.0
DIGIT_LENGTH_BONUS = 12.0
CONFIDENCE_WEIGHT = 20.0
MIN_PLAUSIBLE_SCORE = 0.0


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


def is_excluded_reading(text):
    if looks_like_clock(text):
        return True
    if is_temperature_like(text):
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
        return f"{whole.replace('.', '')}.{tenths}"
    return f"{digits[:-1]}.{digits[-1]}"


def is_unit_only_label(text):
    return re.fullmatch(r"km\.?|kms\.?|mi\.?|miles?", text.strip(), re.IGNORECASE) is not None


def has_unit_suffix(text):
    return re.search(r"\d\s*(km|mi|miles?)\b", text.lower()) is not None


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

        points = score * CONFIDENCE_WEIGHT + DIGIT_LENGTH_BONUS
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

    # Fallback: no label, unit, or valid candidate was found. Zoom in on any
    # digit-heavy token that was rejected as a value, so pass 2 can re-read it.
    if not anchor_boxes:
        anchor_boxes = [
            box for text, box in zip(texts, boxes)
            if box is not None and is_near_miss_reading(text)
        ]

    if not anchor_boxes:
        return None

    lefts, tops, rights, bottoms = zip(*anchor_boxes)
    left, top, right, bottom = min(lefts), min(tops), max(rights), max(bottoms)
    pad_x = max((right - left) * padding_frac, min_padding_px)
    pad_y = max((bottom - top) * padding_frac, min_padding_px)
    return (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)


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
        crop, s = resize_to_long_side(crop, TILE_TARGET_SIDE)
        padded = cv2.copyMakeBorder(
            enhance_image(crop), TILE_PAD, TILE_PAD, TILE_PAD, TILE_PAD,
            borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
        )
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
        all_texts.extend(texts)
        all_scores.extend(scores)
        all_boxes.extend(mapped)

        value, _, _, _ = select_odometer_reading(texts, scores, mapped)
        if value is not None:
            votes[value] = votes.get(value, 0) + 1
            if votes[value] >= 2:   # two independent tiles agree -> stop
                break
    return all_texts, all_scores, all_boxes


# ---------------------------------------------------------------------------
# Main prediction
# ---------------------------------------------------------------------------

def predict_odometer_reading(base64_string: str) -> str:
    image_bytes = decode_base64_image(base64_string)
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(
            "The Base64 data was decoded, but it is not a valid supported image."
        )

    # Pass 1: fast whole-frame scan just to LOCATE the odometer. Pass 1
    # doesn't need to read those digits correctly, only find roughly
    # where they are; pass 2 below re-reads them at real resolution.
    pass1_img, pass1_scale = resize_to_long_side(img, 900)

    if ODOMETER_DEBUG:
        gray_for_blur = cv2.cvtColor(pass1_img, cv2.COLOR_BGR2GRAY)
        blur_score = assess_blur(gray_for_blur)
        print(
            f"[odometer debug] blur score: {blur_score:.1f} "
            f"({'BLURRY' if blur_score < BLUR_THRESHOLD else 'ok'})"
        )

    pass1_enhanced = enhance_image(pass1_img)
    pass1_pad = 40
    pass1_padded = cv2.copyMakeBorder(
        pass1_enhanced, top=pass1_pad, bottom=pass1_pad, left=pass1_pad, right=pass1_pad,
        borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
    )

    result = ocr.predict(pass1_padded)
    texts, scores, boxes = parse_pipeline_result(result)

    if not texts:
        rec_result = get_recognizer().predict(pass1_padded)
        texts, scores = parse_recognition_only_result(rec_result)
        boxes = [None] * len(texts)

    if not texts:
        binarized = last_resort_binarize(pass1_padded)
        rec_result = get_recognizer().predict(binarized)
        texts, scores = parse_recognition_only_result(rec_result)
        boxes = [None] * len(texts)

    if ODOMETER_DEBUG:
        print(
            f"[odometer debug] pass1 texts: "
            f"{list(zip(texts, [round(s, 3) for s in scores]))}"
        )

    # ---- Decide whether pass 1 is already good enough on its own.
    # Every predict() call carries a large, roughly-fixed cost on this
    # kind of hardware, so the biggest lever isn't image size -- it's how
    # many of these calls we make. If pass 1 already found a direct,
    # unambiguous signal (e.g. a merged 'ODO229802km' token, or the only
    # numeric candidate in frame at high confidence), a second
    # verification pass is unlikely to change the answer and isn't worth
    # its fixed cost. Pass 2 is reserved for when pass 1's answer is
    # genuinely uncertain -- a proximity-based guess among competing
    # decoys, or a low-confidence read -- where a closer look at real
    # resolution can actually change the outcome.
    value1, conf1, box1, is_strong = select_odometer_reading(
        texts, scores, boxes)

    value, confidence = None, 0.0
    used_pass2 = False
    used_pass3 = False

    if is_strong:
        value, confidence = value1, conf1
    else:
        roi = find_odometer_roi(texts, scores, boxes)

        if ODOMETER_DEBUG:
            print(
                f"[odometer debug] pass1 not strong enough "
                f"(value={value1!r}, conf={conf1:.3f}); roi={roi}"
            )

        if roi is not None:
            orig_h, orig_w = img.shape[:2]
            l, t, r, b = roi
            orig_l = max(0, (l - pass1_pad) / pass1_scale)
            orig_t = max(0, (t - pass1_pad) / pass1_scale)
            orig_r = min(orig_w, (r - pass1_pad) / pass1_scale)
            orig_b = min(orig_h, (b - pass1_pad) / pass1_scale)

            if orig_r - orig_l >= 4 and orig_b - orig_t >= 4:
                crop = img[int(orig_t):int(orig_b), int(orig_l):int(orig_r)]
                crop = resize_crop_for_zoom(
                    crop, min_long_side=650, max_long_side=900)
                crop_enhanced = enhance_image(crop)
                crop_padded = cv2.copyMakeBorder(
                    crop_enhanced, top=30, bottom=30, left=30, right=30,
                    borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
                )
                result2 = ocr.predict(crop_padded)
                texts2, scores2, boxes2 = parse_pipeline_result(result2)

                if ODOMETER_DEBUG:
                    print(
                        f"[odometer debug] pass2 (zoomed) texts: "
                        f"{list(zip(texts2, [round(s, 3) for s in scores2]))}"
                    )

                if texts2:
                    value2, conf2, _box2, _strong2 = select_odometer_reading(
                        texts2, scores2, boxes2
                    )
                    if value2 is not None:
                        value, confidence = value2, conf2
                        used_pass2 = True

        if value is None:
            value, confidence = value1, conf1

        if value is None:
            texts3, scores3, boxes3 = scan_tiles(img, pass1_scale, pass1_pad)
            if ODOMETER_DEBUG:
                print(
                    f"[odometer debug] pass3 (tiles) texts: "
                    f"{list(zip(texts3, [round(sc, 3) for sc in scores3]))}"
                )
            value3, conf3, _box3, _strong3 = select_odometer_reading(
                texts3, scores3, boxes3
            )
            if value3 is not None:
                value, confidence = value3, conf3
                used_pass3 = True

    if ODOMETER_DEBUG:
        print(
            f"[odometer debug] used_pass2={used_pass2} used_pass3={used_pass3} "
            f"final value={value!r} confidence={confidence:.3f}"
        )
    return value if value is not None else ""


def main():
    base64_string = input("Enter Base64 image string: ").strip()

    try:
        prediction = predict_odometer_reading(base64_string)
        print(prediction)
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
