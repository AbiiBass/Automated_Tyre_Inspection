import os

os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

CPU_THREADS = 4
os.environ["OMP_NUM_THREADS"] = str(CPU_THREADS)
os.environ["MKL_NUM_THREADS"] = str(CPU_THREADS)
os.environ["OPENBLAS_NUM_THREADS"] = str(CPU_THREADS)

from paddleocr import PaddleOCR
import numpy as np
import cv2
import re
import time
import binascii
import base64
import io


INVALID_MILEAGE = "Invalid Mileage"

STRIP_LEADING_ZEROS = True

MIN_ODOMETER_DIGITS = 4
MAX_ODOMETER_DIGITS = 7

IMPLIED_TENTHS_MIN_DIGITS = 7

ALLOW_SHORT_READINGS = True
SHORT_READING_MIN_SCORE = 0.80

TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

PASS1_TARGET = 900
PASS1_PAD = 30

SCAN_PLAN = [
    (400, "mild"),
    (300, "none"),
    (500, "smooth"),
    (900, "strong"),
    (600, "smooth_inv"),
    (400, "smooth_inv"),
]

MIN_TOKEN_SCORE = 0.15
MIN_CANDIDATE_SCORE = 0.30
MIN_ACCEPT_CONFIDENCE = 0.50
MIN_DIGIT_HEIGHT_PX = 10.0
MIN_IMAGE_SIDE_PX = 16

TARGET_DIGIT_HEIGHT = 45.0
ZOOM_MIN_LONG_SIDE = 300
ZOOM_MAX_LONG_SIDE = 900
ZOOM_PAD = 30

TILE_TARGET_SIDE = 900
TILE_PAD = 30

MERGE_MAX_GAP_FRAC = 1.5
MERGE_MAX_DY_FRAC = 0.4
MERGE_MAX_HEIGHT_RATIO = 1.5

MIN_TRUSTED_DIGIT_PX = 24.0

VERIFY_WITH_LIT = True
LIT_PERCENTILE = 85
LIT_MIN_CONFIDENCE = 0.85

SEVEN_DIGIT_MIN_SCORE = 0.90
MIN_GAUGE_LABELS = 3
DASHBOARD_WORDS = ("km/h", "kmh", "mph", "rpm", "x1000", "x100", "×100",
                   "l/100", "kilometer", "odo", "trip", "avg")

LONGEST_NUMBER_EVIDENCE = True
LONGEST_MIN_DIGITS = 5
LONGEST_MIN_SCORE = 0.95

TIME_BUDGET_S = 30.0
EARLY_STOP_CONFIDENCE = 0.95

TRIP_READING_PENALTY = 30.0


ODO_LABEL_PROXIMITY_WEIGHT = 50.0
ODO_LABEL_PROXIMITY_RADIUS = 220.0
NEGATIVE_LABEL_PENALTY_WEIGHT = 50.0
NEGATIVE_LABEL_PROXIMITY_RADIUS = 220.0
UNIT_SUFFIX_BONUS = 25.0
MERGED_LABEL_BONUS = 40.0
DIGIT_LENGTH_BONUS = 12.0
DIGIT_COUNT_BONUS = 3.0
CONFIDENCE_WEIGHT = 20.0
MIN_PLAUSIBLE_SCORE = 0.0


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


def decode_image(image_bytes: bytes):
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is not None:
        return image
    try:
        from PIL import Image
        try:
            import pillow_avif
        except ImportError:
            pass
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def decode_base64_image(base64_string: str) -> bytes:
    if not isinstance(base64_string, str):
        raise ValueError("Invalid Base64 image string: expected a str.")
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


def enhance_image(img_bgr, mode="strong"):
    if mode == "none":
        return img_bgr

    if mode == "lit":
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


def resize_to_long_side(img_bgr, target_long_side):
    h, w = img_bgr.shape[:2]
    long_side = max(h, w)
    scale = target_long_side / float(long_side)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interp), scale


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
    lab = normalize_label(text)
    return (re.fullmatch(r"[o0d][o0d][o0](meter)?", lab) is not None
            and "d" in lab[:3])


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
    return re.fullmatch(r"\d{1,2}0", text.strip()) is not None


def looks_like_gauge_scale(text):
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
    if "/" in text:
        return True
    lower = text.lower()
    return any(marker in lower for marker in ("trip", "avg", "disp", "/100", "l/1", "/h", "range", "dte"))


def is_merged_gauge_labels(text, texts):
    t = text.strip()
    if not re.fullmatch(r"\d{4,6}", t):
        return False
    singles = {x.strip() for x in texts if is_gauge_label(x)}
    for i in range(1, len(t)):
        a, b = t[:i], t[i:]
        if is_gauge_label(a) and is_gauge_label(b) and a in singles and b in singles:
            return True
    return False


def normalize_odometer_reading(text):
    stripped = re.sub(r"[o0]d[o0](meter)?", "", text, flags=re.IGNORECASE)
    kept = re.sub(r"[^0-9.]", "", stripped.replace(",", ".")).strip(".")
    digits = kept.replace(".", "")
    if not (MIN_ODOMETER_DIGITS <= len(digits) <= MAX_ODOMETER_DIGITS):
        return None
    if "." in kept:
        whole, _, tenths = kept.rpartition(".")
        if len(tenths) == 1:
            return f"{whole.replace('.', '')}.{tenths}"
    if len(digits) >= IMPLIED_TENTHS_MIN_DIGITS:
        return f"{digits[:-1]}.{digits[-1]}"
    return digits


def strip_odo_zeros(text):
    t = text.strip()
    digits = re.sub(r"[^0-9]", "", t)
    if len(digits) > MAX_ODOMETER_DIGITS and re.fullmatch(r"0{2,3}[1-9][0-9.\s]*", t):
        rest = t.lstrip("0").strip()
        if MIN_ODOMETER_DIGITS <= len(re.sub(r"\D", "", rest)) <= MAX_ODOMETER_DIGITS:
            return rest, True
    return text, False


def is_unit_only_label(text):
    return re.fullmatch(r"km\.?|kms\.?|mi\.?|miles?", text.strip(), re.IGNORECASE) is not None


def has_unit_suffix(text):
    return re.search(r"\d\s*(km|mi|miles?)\b(?!\s*/)", text.lower()) is not None


def has_merged_positive_label(text):
    alnum = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(re.search(r"[o0]d[o0]", alnum)) and bool(re.search(r"\d", alnum))


def is_gauge_label(text):
    t = text.strip()
    if not re.fullmatch(r"[1-9]\d{0,2}", t):
        return False
    v = int(t)
    return 5 <= v <= 260 and v % 5 == 0


def has_frame_context(texts):
    gauge = set()
    for t in texts:
        for part in re.split(r"[\s.]+", t.strip()):
            if is_gauge_label(part):
                gauge.add(int(part))
        compact = re.sub(r"\s+", "", t.lower())
        if any(w in compact for w in DASHBOARD_WORDS):
            return True
        if is_positive_label(t):
            return True
    return len(gauge) >= MIN_GAUGE_LABELS


def is_trip_reading(text):
    return re.fullmatch(r"\d{1,4}[.,]\d", text.strip()) is not None


def has_stacked_trip(box, texts, boxes, n_digits=None):
    if box is None:
        return False
    l, t, r, b = box
    h = max(b - t, 1.0)
    for t2, b2 in zip(texts, boxes):
        if b2 is None or b2 is box:
            continue
        d2 = re.sub(r"\D", "", t2)
        shorter_number = (n_digits is not None and re.fullmatch(r"\d+", t2.strip())
                          and 2 <= len(d2) < n_digits - 1
                          and not is_gauge_label(t2))
        if not (is_trip_reading(t2) or shorter_number):
            continue
        l2, tt2, r2, bb2 = b2
        overlap = min(r, r2) - max(l, l2)
        if overlap <= 0.3 * min(r - l, r2 - l2):
            continue
        gap = max(tt2 - b, t - bb2)
        max_gap = 1.0 * h if shorter_number and not is_trip_reading(t2) else 1.5 * h
        if -0.3 * h <= gap <= max_gap:
            return True
    return False


def is_longest_number(text, score, box, texts, boxes):
    if not LONGEST_NUMBER_EVIDENCE or box is None or score < LONGEST_MIN_SCORE:
        return False
    t = re.sub(r"[^0-9A-Za-z ]+$", "", text.strip()).strip()
    if not re.fullmatch(r"[0-9 ]+", t):
        return False
    digits = t.replace(" ", "")
    if not (LONGEST_MIN_DIGITS <= len(digits) <= MAX_ODOMETER_DIGITS):
        return False
    h = max(box[3] - box[1], 1.0)
    for t2, b2 in zip(texts, boxes):
        if b2 is None or b2 is box:
            continue
        d2 = re.sub(r"\D", "", t2)
        if len(d2) >= len(digits) and digits not in d2 and d2 not in digits:
            return False
        if re.search(r"[A-Za-z]", t2):
            cx2, cy2 = box_center(b2)
            side_by_side = b2[2] <= box[0] + 0.3 * h or b2[0] >= box[2] - 0.3 * h
            near = (box[0] - 3.0 * h) <= cx2 <= (box[2] + 3.0 * h)
            if side_by_side and near and box[1] <= cy2 <= box[3]:
                return False
    return True


def has_unit_or_label_beside(box, texts, boxes):
    if box is None:
        return False
    cx, cy = box_center(box)
    h = max(box[3] - box[1], 1.0)
    w = max(box[2] - box[0], 1.0)
    for t2, b2 in zip(texts, boxes):
        if b2 is None or b2 is box:
            continue
        if not (is_positive_label(t2) or is_unit_only_label(t2)):
            continue
        cx2, cy2 = box_center(b2)
        if abs(cy2 - cy) <= 1.0 * h and abs(cx2 - cx) <= w / 2 + 4.0 * h:
            return True
    return False


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
        if is_merged_gauge_labels(text, texts):
            continue
        text, odo_zeros = strip_odo_zeros(text)
        value = normalize_odometer_reading(text)
        if value is None:
            continue
        if score < MIN_CANDIDATE_SCORE:
            continue

        n_digits = len(value.replace(".", ""))
        points = score * CONFIDENCE_WEIGHT + \
            DIGIT_LENGTH_BONUS + DIGIT_COUNT_BONUS * n_digits
        has_direct_signal = False

        if has_merged_positive_label(text) or odo_zeros:
            points += MERGED_LABEL_BONUS
            has_direct_signal = True
        if is_trip_reading(text):
            points -= TRIP_READING_PENALTY
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

        evidence = has_direct_signal
        if not evidence and box is not None:
            if positive_anchors:
                cx, cy = box_center(box)
                evidence = min(((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5
                               for ox, oy in positive_anchors) <= ODO_LABEL_PROXIMITY_RADIUS
            evidence = (evidence or has_unit_or_label_beside(box, texts, boxes)
                        or has_stacked_trip(box, texts, boxes, n_digits))
        evidence = 2 if evidence else 0
        if not evidence and n_digits == 7 and score >= SEVEN_DIGIT_MIN_SCORE:
            evidence = 1
        if not evidence and is_longest_number(text, score, box, texts, boxes):
            evidence = 1

        candidates.append((points, score, value, has_direct_signal, box, evidence))

    if not candidates and ALLOW_SHORT_READINGS:
        short = select_short_reading(texts, scores, boxes, negative_anchors)
        if short is not None:
            value, score, box = short
            return value, score, box, False, 2

    if not candidates:
        return None, 0.0, None, False, 0

    if len(candidates) == 1:
        _, best_score, best_value, best_direct, best_box, best_ev = candidates[0]
        is_strong = (best_direct and best_score >= 0.7) or best_score >= 0.95
        return best_value, best_score, best_box, is_strong, best_ev

    candidates.sort(key=lambda c: (c[5] > 0, c[0]), reverse=True)
    best_points, best_score, best_value, best_direct, best_box, best_ev = candidates[0]
    if best_points < MIN_PLAUSIBLE_SCORE:
        return None, 0.0, None, False, 0
    is_strong = best_direct and best_score >= 0.7
    return best_value, best_score, best_box, is_strong, best_ev


def select_short_reading(texts, scores, boxes, negative_anchors):
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

    _, _, candidate_box, _, _ = select_odometer_reading(texts, scores, boxes)
    if candidate_box is not None:
        anchor_boxes.append(candidate_box)

    if not anchor_boxes:
        anchor_boxes = [
            box for text, score, box in zip(texts, scores, boxes)
            if box is not None and score >= MIN_CANDIDATE_SCORE
            and is_near_miss_reading(text)
        ]

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


def ocr_full_frame(img, target, mode, pass1_scale):
    resized, sc = resize_to_long_side(img, target)
    padded = pad_white(enhance_image(resized, mode), PASS1_PAD)

    texts, scores, boxes = parse_pipeline_result(ocr.predict(padded))

    k = pass1_scale / sc
    mapped = []
    for box in boxes:
        if box is None:
            mapped.append(None)
        else:
            mapped.append(tuple((v - PASS1_PAD) * k + PASS1_PAD for v in box))

    keep = [i for i, (t, s) in enumerate(zip(texts, scores))
            if t.strip() and s >= MIN_TOKEN_SCORE]
    texts = [texts[i] for i in keep]
    scores = [scores[i] for i in keep]
    mapped = [mapped[i] for i in keep]

    texts, scores, mapped = merge_row_tokens(texts, scores, mapped)
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

        value, _, _, _, _ = select_odometer_reading(texts, scores, mapped)
        if value is not None:
            votes[value] = votes.get(value, 0) + 1
            if votes[value] >= 2:
                break
    return all_texts, all_scores, all_boxes


def zoom_targets_for_crop(crop, digit_height_orig):
    long_side = max(crop.shape[:2])
    if digit_height_orig and digit_height_orig > 0:
        primary = long_side * TARGET_DIGIT_HEIGHT / digit_height_orig
        primary = int(
            min(max(primary, ZOOM_MIN_LONG_SIDE), ZOOM_MAX_LONG_SIDE))
        alternate = int(max(primary * 0.6, 250))
        return [primary] if alternate >= primary else [primary, alternate]
    fallback = int(min(max(long_side, 650), ZOOM_MAX_LONG_SIDE))
    return [fallback, 400]


def run_pass2(img, roi, pass1_scale, texts, boxes, seen_texts):
    orig_h, orig_w = img.shape[:2]
    l, t, r, b = roi
    orig_l = max(0, (l - PASS1_PAD) / pass1_scale)
    orig_t = max(0, (t - PASS1_PAD) / pass1_scale)
    orig_r = min(orig_w, (r - PASS1_PAD) / pass1_scale)
    orig_b = min(orig_h, (b - PASS1_PAD) / pass1_scale)

    if orig_r - orig_l < 4 or orig_b - orig_t < 4:
        return None, 0.0, 0

    crop = img[int(orig_t):int(orig_b), int(orig_l):int(orig_r)]
    digit_h = estimate_digit_height(texts, boxes, pass1_scale)

    for target in zoom_targets_for_crop(crop, digit_h):
        zoomed, _ = resize_to_long_side(crop, target)
        for mode in ("mild", "smooth_inv"):
            padded = pad_white(enhance_image(zoomed, mode), ZOOM_PAD)
            texts2, scores2, boxes2 = parse_pipeline_result(
                ocr.predict(padded))
            texts2, scores2, boxes2 = merge_row_tokens(texts2, scores2, boxes2)
            seen_texts.extend(texts2)
            if not texts2:
                continue
            value2, conf2, _box2, _strong2, ev2 = select_odometer_reading(
                texts2, scores2, boxes2)
            if value2 is not None:
                return value2, conf2, ev2
    return None, 0.0, 0


def verify_with_lit(img, box, pass1_scale, value, value_conf=0.0):
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
    v2, c2, _, _, _ = select_odometer_reading(t2, s2, b2)
    if v2 is None or v2 == value or c2 < LIT_MIN_CONFIDENCE:
        return None
    if c2 <= value_conf:
        return None
    if len(v2.replace(".", "")) != len(value.replace(".", "")):
        return None
    return v2, c2


def format_mileage(value):
    if not STRIP_LEADING_ZEROS:
        return value
    whole, dot, tenths = value.partition(".")
    return (whole.lstrip("0") or "0") + dot + tenths


def predict_odometer_reading(base64_string: str) -> str:
    image_bytes = decode_base64_image(base64_string)
    img = decode_image(image_bytes)
    if img is None:
        raise ValueError(
            "The Base64 data was decoded, but it is not a valid supported image."
        )

    h, w = img.shape[:2]
    if min(h, w) < MIN_IMAGE_SIDE_PX:
        return INVALID_MILEAGE
    pass1_scale = PASS1_TARGET / float(max(h, w))

    texts, scores, boxes = [], [], []
    value1, conf1, box1, is_strong, ev1 = None, 0.0, None, False, 0
    early_stop = False
    t_start = time.perf_counter()
    last_scan_s = 0.0
    for i_scan, (target, mode) in enumerate(SCAN_PLAN):
        elapsed = time.perf_counter() - t_start
        if i_scan > 0 and elapsed + last_scan_s > TIME_BUDGET_S:
            break
        t_scan = time.perf_counter()
        t_x, s_x, b_x = ocr_full_frame(img, target, mode, pass1_scale)
        last_scan_s = time.perf_counter() - t_scan
        texts += t_x
        scores += s_x
        boxes += b_x
        value1, conf1, box1, is_strong, ev1 = select_odometer_reading(
            texts, scores, boxes)
        if is_strong and not (ev1 or has_frame_context(texts)):
            is_strong = False
        if is_strong and ev1 < 2:
            is_strong = False
            early_stop = True
            break
        if is_strong:
            if box1 is not None:
                digit_px = (box1[3] - box1[1]) * target / float(PASS1_TARGET)
                if digit_px < MIN_TRUSTED_DIGIT_PX:
                    is_strong = False
                    break
            break
        if (value1 is not None and conf1 >= EARLY_STOP_CONFIDENCE
                and len(value1.replace(".", "")) >= MIN_ODOMETER_DIGITS
                and (ev1 or has_frame_context(texts))):
            early_stop = True
            break

    seen_texts = list(texts)
    value, confidence, evidence = None, 0.0, 0

    if is_strong:
        value, confidence, evidence = value1, conf1, ev1
        if VERIFY_WITH_LIT:
            fixed = verify_with_lit(img, box1, pass1_scale, value1, conf1)
            if fixed is not None:
                value, confidence = fixed
    else:
        roi = find_odometer_roi(texts, scores, boxes)

        if roi is not None:
            value2, conf2, ev2 = run_pass2(img, roi, pass1_scale, texts, boxes, seen_texts)
            if value2 is not None:
                d1 = (value1 or "").replace(".", "")
                d2 = value2.replace(".", "")
                keep_pass1 = value1 is not None and ev1 == 2 and (
                    ev2 < 2 or (len(d2) < len(d1) and d2 in d1))
                if early_stop and conf2 < conf1:
                    keep_pass1 = True
                if value1 is not None and ev1 > ev2:
                    keep_pass1 = True
                if value1 is not None and len(d2) > len(d1) and d1 in d2 and conf2 >= 0.85:
                    keep_pass1 = False
                if keep_pass1:
                    value, confidence, evidence = value1, conf1, ev1
                    if VERIFY_WITH_LIT:
                        fixed = verify_with_lit(img, box1, pass1_scale, value1, conf1)
                        if fixed is not None:
                            value, confidence = fixed
                else:
                    value, confidence, evidence = value2, conf2, ev2

        if value is None:
            value, confidence, evidence = value1, conf1, ev1

        if (value is None and has_frame_context(seen_texts)
                and time.perf_counter() - t_start < TIME_BUDGET_S):
            texts3, scores3, boxes3 = scan_tiles(img, pass1_scale, PASS1_PAD)
            seen_texts.extend(texts3)
            value3, conf3, _box3, _strong3, ev3 = select_odometer_reading(
                texts3, scores3, boxes3)
            if value3 is not None:
                value, confidence, evidence = value3, conf3, ev3

    context = has_frame_context(seen_texts)

    if value is None:
        return INVALID_MILEAGE
    if confidence < MIN_ACCEPT_CONFIDENCE:
        return INVALID_MILEAGE
    if not (evidence or context):
        return INVALID_MILEAGE
    return format_mileage(value)
