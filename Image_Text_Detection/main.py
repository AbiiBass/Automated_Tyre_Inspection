import datetime
import os
import re
import time
import base64
from zoneinfo import ZoneInfo
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import cv2
import numpy as np
from paddleocr import PaddleOCR, TextRecognition
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# must be set before paddleocr/paddlex is imported
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"
# ^ Tune these to your actual machine: os.cpu_count() tells you how many cores
# you have. Pinning to 4 on an 8+ core box leaves half your CPU idle; pinning
# to 4 on a 2-core box causes thread contention that can make things *slower*.
# Try `os.cpu_count()` as the value and compare.

app = FastAPI()

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./depot_data.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

SGT = ZoneInfo("Asia/Singapore")


class ScanRecord(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True, index=True)
    scan_type = Column(String)  # 'plate' or 'odometer'
    extracted_text = Column(String)
    confidence = Column(Float)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(
        SGT).replace(tzinfo=None))


Base.metadata.create_all(bind=engine)


def save_scan_to_db(scan_type, text, confidence):
    db = SessionLocal()
    try:
        record = ScanRecord(
            scan_type=scan_type, extracted_text=str(text), confidence=confidence
        )
        db.add(record)
        db.commit()
    finally:
        db.close()


# --- Preprocessing / model tuning knobs ---
BLUR_THRESHOLD = 100.0
MIN_ODOMETER_DIGITS = 4
MAX_ODOMETER_DIGITS = 8

# How large an image the *detector* actually works on, independent of how
# large the image you hand to `.predict()` is. PP-OCRv5's detectors cap the
# long side at 960px internally regardless of input size -- so this is the
# real accuracy/speed dial, not the image resize below. Lower = faster,
# higher = better chance of finding small/low-contrast text, at real cost.
TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

# Full pipeline: detection + recognition, both explicitly pinned to the
# "mobile" (lightweight) PP-OCRv5 variants. Leaving `lang="en"` alone here
# would silently select PP-OCRv5_server_det -- a noticeably heavier detector
# -- paired with the mobile recognizer, which is a big chunk of the >10s
# latency. Mobile+mobile trades some accuracy on very cluttered/low-contrast
# scenes for a substantial, verified speed win; if accuracy regresses on your
# fleet's photos, PP-OCRv5_server_det is the one dial to revert.
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_textline_orientation=False,
    enable_mkldnn=False,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
)
# NOTE on enable_mkldnn: this has been False since your very first version of
# this file, which suggests it may have been disabled to work around a crash
# on this machine. MKL-DNN is typically a large (often 2-4x) CPU speedup on
# Intel hardware, so it's worth trying `enable_mkldnn=True` once, by itself,
# and watching for the original instability before combining it with anything
# else below -- but I'm not flipping it for you blind, since I can't verify
# it's safe on your actual hardware from here.

# Recognition-only model fallback for tight crops (no detection step)
recognizer = TextRecognition()


def _parse_pipeline_result(result):
    """Also returns each segment's bounding box (left, top, right, bottom) in
    the padded image's pixel coordinates -- needed so the odometer selector
    can use *position*, not just text content, to find the real reading."""
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


def _parse_recognition_only_result(result):
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
    """Gentle, detector-safe enhancement -- denoise, boost local contrast,
    mildly sharpen. Stays 3-channel color on purpose: PaddleOCR's models are
    trained on natural color crops, and converting to grayscale (as a prior
    version of this file did) throws away color information the recognizer
    may be using, on top of not measurably helping detection."""
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
    """Resize so the LONGEST side hits the target, preserving aspect ratio --
    not a fixed width. A fixed-width resize silently produces a very tall
    image for portrait photos (phones default to portrait), whose long side
    (height) then exceeds the detector's own internal cap by an amount that
    depends on the photo's aspect ratio -- unpredictable, and it wastes
    resolution the detector immediately downsamples away again. Resizing by
    the long side directly controls what the detector actually sees,
    regardless of orientation.
    """
    h, w = img_bgr.shape[:2]
    long_side = max(h, w)
    scale = target_long_side / float(long_side)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interp), scale


def resize_crop_for_zoom(crop_bgr, min_long_side, max_long_side):
    """Used on the small, cropped region of interest for the second
    (zoomed) odometer pass: upscale if the crop's long side is smaller than
    `min_long_side` (real pixels for the recognizer to work with -- this is
    the whole point of the second pass), or downscale if it somehow exceeds
    `max_long_side` (keeps pass-2 compute bounded and comfortably under the
    detector's own cap, so our sizing choice is what controls the outcome,
    not an internal downsample we don't see)."""
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


def select_plate_reading(texts, scores):
    """Filters OCR outputs to find a valid 6-8 character alphanumeric license
    plate, ignoring pure-text watermarks."""
    candidates = []
    for text, score in zip(texts, scores):
        cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())
        if 6 <= len(cleaned) <= 8:
            has_letters = bool(re.search(r"[A-Z]", cleaned))
            has_digits = bool(re.search(r"\d", cleaned))
            if has_letters and has_digits:
                candidates.append((score, cleaned))

    if not candidates:
        return None, 0.0

    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1], candidates[0][0]


# ---------------------------------------------------------------------------
# Odometer reading selection
#
# Rather than a fixed set of if/else rules tuned to a few example photos,
# every numeric candidate detected in the frame is scored on several
# independent, weighted signals and the highest-scoring one wins. This
# degrades gracefully across dashboard variations instead of breaking
# outright when one assumption doesn't hold (e.g. a digit-count rule breaks
# on a low-mileage vehicle; a same-row rule breaks on a layout where the
# label sits above its value instead of beside it).
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


def _box_center(box):
    left, top, right, bottom = box
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def _normalize_label(text):
    return re.sub(r"[^a-z0-9]+$", "", text.strip().lower())


def _is_positive_label(text):
    """A standalone 'ODO'/'ODOMETER' label -- tolerant of the O/0 mixups
    these dashboard fonts and OCR both commonly produce, and of trailing
    punctuation like 'ODO:'. Deliberately does NOT match 'ODOTRIP' -- that's
    the trip meter, not the main odometer."""
    return re.fullmatch(r"[o0]d[o0](meter)?", _normalize_label(text)) is not None


def _is_negative_label(text):
    """Labels for readings that are never the main odometer -- trip meter,
    average fuel consumption, range/distance-to-empty -- used as 'negative
    anchors': a number sitting closer to one of these than to any ODO label
    is probably that other reading, even if it also carries a km/mi unit
    (a 'range to empty' readout is a common real-world example of this)."""
    if _is_positive_label(text):
        return False
    return re.search(r"trip|avg\.?|disp|range|dte|remain", text.strip().lower()) is not None


def _is_temperature_like(text):
    """A short number immediately followed by a Celsius/Fahrenheit unit, e.g.
    '31C', '31\u00b0C'. Excluded outright (not just down-scored) because it is
    essentially never the odometer, regardless of where it sits in frame."""
    return re.fullmatch(r"-?\d{1,3}\s*[\u00b0]?\s*[cf]", text.strip(), re.IGNORECASE) is not None


def _looks_like_clock(text):
    """An actual HH:MM(:SS) pattern, e.g. '9:36'. Deliberately narrower than
    'contains a colon' -- a merged label like 'ODO:449284km' also contains a
    colon but is not a clock reading, and a bare colon check would wrongly
    exclude it."""
    return re.search(r"\d{1,2}:\d{2}(:\d{2})?", text) is not None


def _looks_garbled(text):
    """A digit run with stray letters or internal whitespace that AREN'T
    explained by a recognized unit/label (km, mi, ODO) -- e.g. '851 82h5'.
    This is the signature of a merged/garbled misdetection (commonly:
    several unrelated small numbers -- dial tick marks, scattered labels --
    accidentally grouped into one detected box at low resolution) rather
    than a genuine reading of one real display. A real reading, even
    misread digit-by-digit, doesn't usually pick up a random letter or an
    internal gap in the middle of the digits; a low-resolution merge of
    several unrelated text fragments does."""
    stripped = re.sub(
        r"km\.?|kms\.?|mi\.?|miles?|[o0]d[o0](meter)?", "", text, flags=re.IGNORECASE)
    if not re.search(r"\d", stripped):
        return False
    has_stray_letter = re.search(r"[a-zA-Z]", stripped) is not None
    has_internal_gap = re.search(r"\d\s+\d", stripped) is not None
    return has_stray_letter or has_internal_gap


def _is_excluded_reading(text):
    """Hard exclusions: content patterns with a near-zero chance of being the
    real odometer regardless of position, so they're removed outright rather
    than just down-scored -- a clock, temperature, or garbled reading should
    never be able to out-score a real value just because a label happens to
    be missing or far away in a given photo."""
    if _looks_like_clock(text):
        return True
    if _is_temperature_like(text):
        return True
    if _looks_garbled(text):
        return True
    lower = text.lower()
    return any(marker in lower for marker in ("trip", "avg", "disp", "/100", "l/1", "/h", "range", "dte"))


def _clean_numeric(text):
    """Digits with AT MOST ONE preserved decimal point, e.g. '634991.7' stays
    '634991.7' (not '6349917' -- stripping the point would silently change
    the actual value, not just its formatting). A comma used as a decimal
    separator is normalized to a period first. Some odometers show a tenths
    digit, some don't -- both need to come out correct, not just 'a string
    of digits'."""
    normalized = text.replace(",", ".")
    kept = re.sub(r"[^0-9.]", "", normalized)
    if not kept:
        return ""
    if "." in kept:
        first, _, rest = kept.partition(".")
        rest = rest.replace(".", "")
        kept = f"{first}.{rest}" if rest else first
    return kept.strip(".")


def _is_unit_only_label(text):
    """A standalone unit token with no digits of its own, e.g. 'km', 'mi' --
    used to help locate the odometer's rough position even when it's a
    separate box from the digits themselves."""
    return re.fullmatch(r"km\.?|kms\.?|mi\.?|miles?", text.strip(), re.IGNORECASE) is not None


def _has_unit_suffix(text):
    """A km/mi unit merged directly onto this segment's own digits, e.g.
    '229802km', '87654mi'."""
    return re.search(r"\d\s*(km|mi|miles?)\b", text.lower()) is not None


def _has_merged_positive_label(text):
    """True if this single segment already merges an odo-like marker with
    its own digits, e.g. 'ODO229802', 'ODO:449284'."""
    alnum = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(re.search(r"[o0]d[o0]", alnum)) and bool(re.search(r"\d", alnum))


def select_odometer_reading(texts, scores, boxes=None):
    """Returns (value_string, confidence, box, is_strong), or
    (None, 0.0, None, False) if nothing plausible was found. `value_string`
    preserves a decimal point when the source text had one (e.g.
    '634991.7'), and is a plain digit string otherwise (e.g. '229802') --
    see module docstring above for the scoring approach. `is_strong` is True
    when the winning candidate carries a direct content signal (a merged
    'ODO'/unit token, not just digit-length or proximity) at good
    confidence -- a caller can use this to skip a second, more expensive
    verification pass when pass 1 already looks trustworthy on its own. The
    returned box (in the same coordinate space as the input) lets a caller
    crop back to just this region for a closer look."""
    n = len(texts)
    boxes = list(boxes) if boxes is not None else [None] * n

    positive_anchors = [
        _box_center(box) for text, box in zip(texts, boxes)
        if box is not None and _is_positive_label(text)
    ]
    negative_anchors = [
        _box_center(box) for text, box in zip(texts, boxes)
        if box is not None and _is_negative_label(text)
    ]

    candidates = []
    for text, score, box in zip(texts, scores, boxes):
        if _is_excluded_reading(text) or _is_positive_label(text) or _is_negative_label(text):
            continue
        value = _clean_numeric(text)
        if not value:
            continue
        integer_digit_count = len(value.split(".")[0])

        points = score * CONFIDENCE_WEIGHT
        has_support = False
        has_direct_signal = False

        if _has_merged_positive_label(text):
            points += MERGED_LABEL_BONUS
            has_support = True
            has_direct_signal = True
        if _has_unit_suffix(text):
            points += UNIT_SUFFIX_BONUS
            has_support = True
            has_direct_signal = True
        if MIN_ODOMETER_DIGITS <= integer_digit_count <= MAX_ODOMETER_DIGITS:
            points += DIGIT_LENGTH_BONUS
            has_support = True

        if box is not None:
            cx, cy = _box_center(box)
            if positive_anchors:
                nearest = min(((cx - ox) ** 2 + (cy - oy) ** 2)
                              ** 0.5 for ox, oy in positive_anchors)
                proximity_bonus = max(
                    0.0, 1.0 - nearest / ODO_LABEL_PROXIMITY_RADIUS) * ODO_LABEL_PROXIMITY_WEIGHT
                if proximity_bonus > 0:
                    has_support = True
                points += proximity_bonus
            if negative_anchors:
                nearest_neg = min(((cx - ox) ** 2 + (cy - oy) ** 2)
                                  ** 0.5 for ox, oy in negative_anchors)
                points -= max(0.0, 1.0 - nearest_neg /
                              NEGATIVE_LABEL_PROXIMITY_RADIUS) * NEGATIVE_LABEL_PENALTY_WEIGHT

        candidates.append(
            (points, score, value, has_support, has_direct_signal, box))

    if not candidates:
        return None, 0.0, None, False

    # A lone candidate with no competing decoys is accepted on OCR confidence
    # alone (typical of a tight crop with nothing else in frame). With more
    # than one candidate, the winner must have at least one real supporting
    # signal beyond its own confidence -- otherwise a short, contextless
    # number (a speedometer tick, say) could out-score nothing and get
    # returned as "the odometer" just for having decent OCR confidence.
    if len(candidates) == 1:
        _, best_score, best_value, _, best_direct, best_box = candidates[0]
        is_strong = (best_direct and best_score >= 0.7) or best_score >= 0.85
        return best_value, best_score, best_box, is_strong

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_points, best_score, best_value, best_has_support, best_direct, best_box = candidates[0]
    if not best_has_support or best_points < MIN_PLAUSIBLE_SCORE:
        return None, 0.0, None, False
    is_strong = best_direct and best_score >= 0.7
    return best_value, best_score, best_box, is_strong


def find_odometer_roi(texts, scores, boxes, padding_frac=0.8, min_padding_px=20):
    """From a fast, low-resolution first pass over the whole dashboard, find
    a bounding box likely to contain the odometer's full text, to hand to a
    second, focused pass at much higher effective resolution.

    Always unions TWO things when available: any 'ODO'/'km' label/unit box,
    AND the current best-scoring numeric candidate's own box. A label is
    often a small, separate token some distance from the actual digit run
    (e.g. 'ODO' printed in a corner above/beside a much wider digit strip) --
    anchoring on the label alone can crop out most of the digits it's
    labeling. Unioning both, then padding generously, keeps the whole
    reading in frame regardless of which one is more reliable in a given
    photo.

    Returns (left, top, right, bottom) in the same coordinate space as the
    input boxes, or None if there was nothing at all to anchor on.
    """
    anchor_boxes = [
        box for text, box in zip(texts, boxes)
        if box is not None and (
            _is_positive_label(text) or _is_unit_only_label(text)
            or _has_unit_suffix(text) or _has_merged_positive_label(text)
        )
    ]

    _, _, candidate_box, _ = select_odometer_reading(texts, scores, boxes)
    if candidate_box is not None:
        anchor_boxes.append(candidate_box)

    if not anchor_boxes:
        return None

    lefts, tops, rights, bottoms = zip(*anchor_boxes)
    left, top, right, bottom = min(lefts), min(tops), max(rights), max(bottoms)
    pad_x = max((right - left) * padding_frac, min_padding_px)
    pad_y = max((bottom - top) * padding_frac, min_padding_px)
    return (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)


def process_plate(image_bytes):
    timing = {}
    t_start = time.perf_counter()
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return {"error": "Invalid image format or empty file decoded."}

        t0 = time.perf_counter()
        img_resized, _scale = resize_to_long_side(img, 800)

        gray_for_blur = cv2.cvtColor(img_resized, cv2.COLOR_BGR2GRAY)
        blur_score = assess_blur(gray_for_blur)
        is_blurry = blur_score < BLUR_THRESHOLD

        enhanced_img = enhance_image(img_resized)
        padded_img = cv2.copyMakeBorder(
            enhanced_img, top=40, bottom=40, left=40, right=40,
            borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
        )
        timing["preprocess_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        result = ocr.predict(padded_img)
        texts, scores, _ = _parse_pipeline_result(result)
        timing["detect_recognize_ms"] = round(
            (time.perf_counter() - t0) * 1000, 1)

        if not texts:
            t0 = time.perf_counter()
            rec_result = recognizer.predict(padded_img)
            texts, scores = _parse_recognition_only_result(rec_result)
            timing["recognition_fallback_ms"] = round(
                (time.perf_counter() - t0) * 1000, 1)

        full_text = " ".join(texts)
        overall_avg_confidence = (sum(scores) / len(scores)) if scores else 0.0

        warnings = []
        if is_blurry:
            warnings.append(
                f"Image may be too blurry (sharpness score {blur_score:.1f}).")

        plate_text, plate_confidence = select_plate_reading(texts, scores)
        if plate_text is not None:
            cleaned_text = plate_text
            confidence = plate_confidence
        else:
            cleaned_text = re.sub(r"[^A-Z0-9]", "", full_text.upper())
            confidence = overall_avg_confidence
            warnings.append(
                "Could not isolate a standard 6-8 character alphanumeric license plate pattern.")

        save_scan_to_db("plate", cleaned_text, round(float(confidence), 4))
        timing["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)

        response = {
            "extracted_text": cleaned_text,
            "confidence": round(float(confidence), 4),
            "timing_ms": timing,
        }
        if warnings:
            response["warning"] = " ".join(warnings)
        return response

    except Exception as e:
        return {"error": f"Processing Exception: {str(e)}"}


def process_odometer(image_bytes, debug=False):
    timing = {}
    t_start = time.perf_counter()
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return {"error": "Invalid image format or empty file decoded."}

        # ---- Pass 1: fast, whole-frame scan just to LOCATE the odometer.
        # On a full dashboard photo the odometer's LCD is often a small
        # fraction of the frame -- at any resolution that keeps the whole
        # dashboard under the detector's cap, its digits may only be a
        # handful of pixels tall, which no amount of sharpening fixes. Pass 1
        # doesn't need to read those digits correctly, only find roughly
        # where they are; pass 2 below re-reads them at real resolution.
        t0 = time.perf_counter()
        pass1_img, pass1_scale = resize_to_long_side(img, 900)

        gray_for_blur = cv2.cvtColor(pass1_img, cv2.COLOR_BGR2GRAY)
        blur_score = assess_blur(gray_for_blur)
        is_blurry = blur_score < BLUR_THRESHOLD

        pass1_enhanced = enhance_image(pass1_img)
        pass1_pad = 40
        pass1_padded = cv2.copyMakeBorder(
            pass1_enhanced, top=pass1_pad, bottom=pass1_pad, left=pass1_pad, right=pass1_pad,
            borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
        )
        timing["pass1_preprocess_ms"] = round(
            (time.perf_counter() - t0) * 1000, 1)

        t0 = time.perf_counter()
        result = ocr.predict(pass1_padded)
        texts, scores, boxes = _parse_pipeline_result(result)
        timing["pass1_detect_recognize_ms"] = round(
            (time.perf_counter() - t0) * 1000, 1)

        if not texts:
            t0 = time.perf_counter()
            rec_result = recognizer.predict(pass1_padded)
            texts, scores = _parse_recognition_only_result(rec_result)
            boxes = [None] * len(texts)
            timing["recognition_fallback_ms"] = round(
                (time.perf_counter() - t0) * 1000, 1)

        if not texts:
            t0 = time.perf_counter()
            binarized = last_resort_binarize(pass1_padded)
            rec_result = recognizer.predict(binarized)
            texts, scores = _parse_recognition_only_result(rec_result)
            boxes = [None] * len(texts)
            timing["binarized_fallback_ms"] = round(
                (time.perf_counter() - t0) * 1000, 1)

        # ---- Decide whether pass 1 is already good enough on its own.
        # Every predict() call carries a large, roughly-fixed cost on this
        # kind of hardware (confirmed: ~2s+ even on a near-blank test image
        # with nothing to detect), so the biggest lever isn't image size --
        # it's how many of these calls we make. If pass 1 already found a
        # direct, unambiguous signal (e.g. a merged 'ODO229802km' token, or
        # the only numeric candidate in frame at high confidence), a second
        # verification pass is unlikely to change the answer and isn't worth
        # its fixed cost. Pass 2 is reserved for when pass 1's answer is
        # genuinely uncertain -- a proximity-based guess among competing
        # decoys, or a low-confidence read -- where a closer look at real
        # resolution can actually change the outcome.
        value1, conf1, box1, is_strong = select_odometer_reading(
            texts, scores, boxes)

        value, digit_confidence = None, 0.0
        used_pass2 = False
        texts2, scores2 = [], []
        roi = None

        if is_strong:
            value, digit_confidence = value1, conf1
        else:
            # ---- Pass 2: crop the region of interest out of the ORIGINAL
            # full-resolution photo (not the small pass-1 copy) and re-read
            # it at real resolution. Only worth its fixed cost when pass 1
            # wasn't already confident on its own.
            roi = find_odometer_roi(texts, scores, boxes)

            if roi is not None:
                t0 = time.perf_counter()
                orig_h, orig_w = img.shape[:2]
                l, t, r, b = roi
                # Undo pass-1's padding offset, then undo its resize scale,
                # to land back in the ORIGINAL photo's pixel coordinates.
                orig_l = max(0, (l - pass1_pad) / pass1_scale)
                orig_t = max(0, (t - pass1_pad) / pass1_scale)
                orig_r = min(orig_w, (r - pass1_pad) / pass1_scale)
                orig_b = min(orig_h, (b - pass1_pad) / pass1_scale)

                if orig_r - orig_l >= 4 and orig_b - orig_t >= 4:
                    crop = img[int(orig_t):int(orig_b),
                               int(orig_l):int(orig_r)]
                    crop = resize_crop_for_zoom(
                        crop, min_long_side=650, max_long_side=900)
                    crop_enhanced = enhance_image(crop)
                    crop_padded = cv2.copyMakeBorder(
                        crop_enhanced, top=30, bottom=30, left=30, right=30,
                        borderType=cv2.BORDER_CONSTANT, value=[255, 255, 255],
                    )
                    result2 = ocr.predict(crop_padded)
                    texts2, scores2, boxes2 = _parse_pipeline_result(result2)
                    if texts2:
                        value2, conf2, _box2, _strong2 = select_odometer_reading(
                            texts2, scores2, boxes2)
                        if value2 is not None:
                            value, digit_confidence = value2, conf2
                            used_pass2 = True
                timing["pass2_zoom_ms"] = round(
                    (time.perf_counter() - t0) * 1000, 1)

            if value is None:
                # Zoomed pass either wasn't triggered or didn't pan out --
                # fall back to whatever pass 1 itself found, even if it
                # wasn't "strong".
                value, digit_confidence = value1, conf1

        warnings = []
        if is_blurry:
            warnings.append(
                f"Image may be too blurry (sharpness score {blur_score:.1f}).")

        if value is not None:
            cleaned_text = float(value) if "." in value else int(value)
            confidence = digit_confidence
        else:
            # Do NOT fall back to concatenating every digit in the frame --
            # that silently reproduces the original giant-garbage-number bug,
            # just truncated so it looks plausible instead of obviously wrong.
            # Reporting 0 with a clear warning is safer than a confident guess.
            cleaned_text = 0
            confidence = 0.0
            warnings.append(
                "No text matching a plausible odometer reading was found. "
                "Crop the photo to just the odometer display for best accuracy."
            )

        save_scan_to_db("odometer", str(cleaned_text),
                        round(float(confidence), 4))
        timing["pass1_was_strong_enough"] = is_strong
        timing["used_zoomed_pass"] = used_pass2
        timing["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)

        response = {
            "extracted_text": cleaned_text,
            "confidence": round(float(confidence), 4),
            "timing_ms": timing,
        }
        if warnings:
            response["warning"] = " ".join(warnings)
        if debug:
            response["debug"] = {
                "pass1_texts": list(zip(texts, [round(s, 3) for s in scores])),
                "pass2_texts": list(zip(texts2, [round(s, 3) for s in scores2])),
                "roi_pass1_coords": roi,
            }
        return response

    except Exception as e:
        return {"error": f"Processing Exception: {str(e)}"}


# --- Pydantic Models for Base64 Requests ---
class Base64ImageRequest(BaseModel):
    image_base64: str


def decode_base64_image(base64_str: str) -> bytes:
    try:
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]
        return base64.b64decode(base64_str)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid base64 string: {str(e)}")


# --- File Upload Endpoints ---
@app.post("/api/ocr/plate")
async def extract_plate(file: UploadFile = File(...)):
    image_bytes = await file.read()
    return process_plate(image_bytes)


@app.post("/api/ocr/odometer")
async def extract_odometer(file: UploadFile = File(...), debug: bool = False):
    image_bytes = await file.read()
    return process_odometer(image_bytes, debug=debug)


@app.post("/api/ocr/batch")
async def extract_batch(plate_file: UploadFile = File(...), odo_file: UploadFile = File(...), debug: bool = False):
    plate_bytes = await plate_file.read()
    odo_bytes = await odo_file.read()

    plate_res = process_plate(plate_bytes)
    odo_res = process_odometer(odo_bytes, debug=debug)

    return {
        "plate": plate_res,
        "odometer": odo_res
    }


# --- Base64 Input Endpoints ---
@app.post("/api/ocr/plate/base64")
async def extract_plate_base64(payload: Base64ImageRequest):
    image_bytes = decode_base64_image(payload.image_base64)
    return process_plate(image_bytes)


@app.post("/api/ocr/odometer/base64")
async def extract_odometer_base64(payload: Base64ImageRequest, debug: bool = False):
    image_bytes = decode_base64_image(payload.image_base64)
    return process_odometer(image_bytes, debug=debug)


@app.get("/api/scans")
def get_scans():
    db = SessionLocal()
    try:
        records = (
            db.query(ScanRecord).order_by(ScanRecord.timestamp.desc()).all()
        )
        return [
            {
                "id": r.id,
                "type": r.scan_type,
                "text": r.extracted_text,
                "confidence": r.confidence,
                "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S SGT"),
            }
            for r in records
        ]
    finally:
        db.close()


class DeleteBatch(BaseModel):
    ids: List[int]


@app.delete("/api/scans/batch")
def delete_batch_scans(payload: DeleteBatch):
    db = SessionLocal()
    try:
        db.query(ScanRecord).filter(ScanRecord.id.in_(
            payload.ids)).delete(synchronize_session=False)
        db.commit()
        return {"message": f"Deleted {len(payload.ids)} records successfully."}
    finally:
        db.close()


@app.delete("/api/scans")
def clear_all_scans():
    db = SessionLocal()
    try:
        db.query(ScanRecord).delete()
        db.commit()
        return {"message": "All scan history cleared successfully."}
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head><title>SMRT Local OCR Pipeline</title></head>
        <body style="font-family: Arial; padding: 30px;">
            <h2>SMRT Depot Local Batch OCR System Active (SGT Timezone)</h2>
            <p>Your optimized local FastAPI server, OpenCV, SQLite, and separated PaddleOCR pipelines are running successfully with Base64 support.</p>
        </body>
    </html>
    """
