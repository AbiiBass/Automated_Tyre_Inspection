import os

os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
OCR_THREADS = 4
os.environ["OMP_NUM_THREADS"] = str(OCR_THREADS)
os.environ["MKL_NUM_THREADS"] = str(OCR_THREADS)
os.environ["OPENBLAS_NUM_THREADS"] = str(OCR_THREADS)

from paddleocr import PaddleOCR, TextRecognition
import numpy as np
import cv2
import re
import io
import itertools
import time
import binascii
import base64


TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

RESIZE_TARGET_LONG_SIDE = 960
TRY_MULTIPLE_VARIANTS = True


PASS_ORDER = (
    (0, 960, "heavy"),
    (0, 480, "heavy"),
    (0, 960, "light"),
    (0, 480, "light"),
)

CONFIDENT_SCORE = 0.85

USE_SG_CHECKSUM = True

ROTATION_FALLBACK_ANGLES = (180,)

INVALID_PLATE = "Invalid License Plate"

MIN_SCORE_CHECKSUM_VALID = 0.30
MIN_SCORE_FORMAT_ONLY = 0.97
MIN_SCORE_REPAIRED = 0.50
MIN_LEN_REPAIRED = 6
MIN_SCORE_LETTER_SWAP = 0.90

STRICT_LTA_CHECKSUM = False

ACCEPT_SPECIAL_FORMATS = True
SPECIAL_PLATE_PATTERNS = (
    r"S[1-9][0-9]{0,3}(CD|CC|TC)",
    r"[0-9]{4,5}MID",
)

TIME_BUDGET_S = 4.5

CHECKSUM_BONUS = 0.25

SG_CHECKSUM_WEIGHTS = (9, 4, 5, 4, 3, 2)
SG_CHECKSUM_LETTERS = "AZYXUTSRPMLKJHGEDCB"

DIGIT_TO_LETTER = {"8": "B", "0": "O", "1": "I", "5": "S", "2": "Z", "6": "G", "4": "A", "7": "T"}
PREFIX_DIGIT_TO_LETTERS = {"0": "QD", "8": "B", "1": "I", "5": "SB", "2": "Z",
                           "6": "G", "4": "A", "7": "T"}
CHECK_DIGIT_TO_LETTERS = {"1": "YTIJL", "7": "TY", "8": "B", "0": "DQ",
                          "5": "S", "2": "Z", "6": "G", "4": "A"}

PREFIX_LETTER_SWAPS = {"O": "QD"}
LETTER_TO_DIGIT = {"B": "8", "O": "0", "D": "0", "Q": "0", "I": "1", "L": "1",
                   "S": "5", "Z": "2", "G": "6", "T": "7", "A": "4"}

PREFIX_CONFUSION_GROUPS = ("MNWH", "BRPEF", "DQ", "UV", "KX", "CG")

ZOOM_ENABLED = True
ZOOM_MAX_REGIONS = 3
ZOOM_LONG_SIDE = 960
ZOOM_VARIANT = "light"
ZOOM_MIN_BOX_SCORE = 0.30
ZOOM_MAX_AREA_FRACTION = 0.5


ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
    cpu_threads=OCR_THREADS,
)

_recognizer = TextRecognition(cpu_threads=OCR_THREADS)


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

    missing_padding = len(base64_string) % 4
    if missing_padding:
        base64_string += "=" * (4 - missing_padding)

    try:
        image_bytes = base64.b64decode(base64_string, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid Base64 image string: {exc}") from exc

    if not image_bytes:
        raise ValueError("Invalid Base64 image string: decoded to zero bytes.")
    return image_bytes


def parse_pipeline_result(result):
    texts = []
    scores = []
    boxes = []

    for page in result:
        page_texts = page.get("rec_texts", []) or []
        page_scores = page.get("rec_scores", []) or []

        page_boxes = page.get("rec_boxes", None)
        if page_boxes is None:
            page_boxes = []

        texts.extend(page_texts)
        scores.extend(page_scores)
        boxes.extend(list(page_boxes))

    if len(boxes) != len(texts):
        boxes = []

    return texts, scores, boxes


def parse_recognition_only_result(result):
    texts = []
    scores = []

    for page in result:
        text = page.get("rec_text", "")
        if text:
            texts.append(text)
            scores.append(page.get("rec_score", 0.0))

    return texts, scores


def enhance_image_grayscale(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    denoised = cv2.fastNlMeansDenoising(
        gray,
        h=12,
        templateWindowSize=7,
        searchWindowSize=11,
    )

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)

    gaussian = cv2.GaussianBlur(contrast, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(contrast, 1.5, gaussian, -0.5, 0)

    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def light_enhance(img_bgr):
    return cv2.bilateralFilter(img_bgr, d=7, sigmaColor=50, sigmaSpace=50)


def resize_to_long_side(img_bgr, target_long_side=RESIZE_TARGET_LONG_SIDE):
    height, width = img_bgr.shape[:2]
    long_side = max(height, width)

    scale = target_long_side / float(long_side)
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC

    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))

    return cv2.resize(
        img_bgr,
        (new_width, new_height),
        interpolation=interpolation,
    )


def clean_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


PLATE_PATTERN = r"([A-Z]{1,3})([0-9]{1,4})([A-Z])"


def sg_checksum_valid(cleaned: str) -> bool:
    m = re.fullmatch(PLATE_PATTERN, cleaned)
    if not m:
        return False
    prefix, number, check = m.groups()
    prefix = prefix[-2:]
    values = [0] * (2 - len(prefix)) + [ord(c) - 64 for c in prefix]
    values += [int(d) for d in number.zfill(4)]
    total = sum(v * w for v, w in zip(values, SG_CHECKSUM_WEIGHTS))
    return SG_CHECKSUM_LETTERS[total % 19] == check


def lookalike_corrections(cleaned: str):
    out = set()
    n = len(cleaned)
    if not (3 <= n <= 8):
        return out
    for p in range(1, 4):
        num_len = n - p - 1
        if not (1 <= num_len <= 4):
            continue
        prefix, number, check = cleaned[:p], cleaned[p:-1], cleaned[-1]
        prefix_options = [PREFIX_DIGIT_TO_LETTERS.get(c, DIGIT_TO_LETTER.get(c, c))
                          if c.isdigit() else c for c in prefix]
        new_prefixes = {"".join(combo) for combo in itertools.product(*prefix_options)}
        new_number = "".join(LETTER_TO_DIGIT.get(c, c) if c.isalpha() else c for c in number)
        if check.isdigit():
            check_options = CHECK_DIGIT_TO_LETTERS.get(check, DIGIT_TO_LETTER.get(check, check))
        else:
            check_options = check
        for new_prefix in new_prefixes:
            for new_check in check_options:
                fixed = new_prefix + new_number + new_check
                if fixed != cleaned and candidate_tier(fixed) == 3:
                    out.add(fixed)
    return out


def extra_corrections(cleaned: str):
    out = set()
    m = re.fullmatch(PLATE_PATTERN, cleaned)
    if m:
        prefix = m.group(1)
        for i, c in enumerate(prefix):
            for alt in PREFIX_LETTER_SWAPS.get(c, ""):
                fixed = cleaned[:i] + alt + cleaned[i + 1:]
                if candidate_tier(fixed) == 3:
                    out.add(fixed)
        for i, c in enumerate(prefix):
            if c == "O" and len(prefix) > 1:
                fixed = cleaned[:i] + cleaned[i + 1:]
                if candidate_tier(fixed) == 3:
                    out.add(fixed)
    return out


def confusion_corrections(cleaned: str, score: float = 1.0):
    out = set()
    m = re.fullmatch(PLATE_PATTERN, cleaned)
    if not m:
        return out
    prefix, number, _ = m.groups()
    swaps = set()
    swap_positions = range(1, 3) if (len(prefix) == 3 and score >= MIN_SCORE_LETTER_SWAP) else ()
    for i in swap_positions:
        for group in PREFIX_CONFUSION_GROUPS:
            if prefix[i] not in group:
                continue
            for alt in group:
                if alt != prefix[i]:
                    fixed = cleaned[:i] + alt + cleaned[i + 1:]
                    if candidate_tier(fixed) == 3:
                        swaps.add(fixed)
    if len(swaps) == 1:
        out |= swaps
    return out


def candidate_tier(cleaned: str):
    if ACCEPT_SPECIAL_FORMATS and any(
        re.fullmatch(p, cleaned) for p in SPECIAL_PLATE_PATTERNS
    ):
        return 3

    m = re.fullmatch(PLATE_PATTERN, cleaned)
    if not m:
        return None
    if m.group(2).startswith("0"):
        return None
    if len(cleaned) < 4:
        return None
    if "O" in m.group(1):
        return None
    if USE_SG_CHECKSUM and sg_checksum_valid(cleaned):
        return 3
    if STRICT_LTA_CHECKSUM:
        return None
    return 2 if len(cleaned) >= 4 else None


def is_acceptable(tier, score):
    if tier == 3:
        return score >= MIN_SCORE_CHECKSUM_VALID
    if tier == 2:
        return score >= MIN_SCORE_FORMAT_ONLY
    return False


def cluster_rows(texts, scores, boxes, y_overlap_ratio=0.4):
    items = []
    for text, score, box in zip(texts, scores, boxes):
        if box is None or len(box) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        if not clean_text(text):
            continue
        if len(clean_text(text)) > 8:
            continue
        items.append(
            {
                "text": text,
                "score": float(score),
                "x1": x1,
                "x2": x2,
                "y1": y1,
                "y2": y2,
                "h": max(y2 - y1, 1.0),
                "cy": (y1 + y2) / 2.0,
            }
        )

    items.sort(key=lambda it: it["cy"])

    rows = []
    for item in items:
        placed = False
        for row in rows:
            ref = row[-1]
            overlap = min(item["y2"], ref["y2"]) - max(item["y1"], ref["y1"])
            min_h = min(item["h"], ref["h"])
            similar = 0.5 <= item["h"] / ref["h"] <= 2.0
            if similar and overlap > y_overlap_ratio * min_h:
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])

    row_candidates = []
    row_info = []
    for row in rows:
        row.sort(key=lambda it: it["x1"])
        merged_text = "".join(it["text"] for it in row)
        avg_score = sum(it["score"] for it in row) / len(row)
        row_info.append({
            "text": merged_text, "score": avg_score,
            "x1": min(it["x1"] for it in row), "x2": max(it["x2"] for it in row),
            "y1": min(it["y1"] for it in row), "y2": max(it["y2"] for it in row),
        })
        if len(row) < 2:
            continue
        row_candidates.append((merged_text, avg_score))
        deduped = row[0]["text"]
        for left, right in zip(row, row[1:]):
            lt, rt = left["text"].rstrip(), right["text"].lstrip()
            if (right["x1"] < left["x2"] and lt and rt
                    and lt[-1].upper() == rt[0].upper()):
                deduped = deduped.rstrip() + rt[1:]
            else:
                deduped += right["text"]
        if clean_text(deduped) != clean_text(merged_text):
            row_candidates.append((deduped, avg_score))
        by_height = sorted(row, key=lambda it: it["cy"])
        if by_height != row:
            row_candidates.append(("".join(it["text"] for it in by_height), avg_score))

    row_info.sort(key=lambda r: (r["y1"] + r["y2"]) / 2.0)
    pairs = [(row_info[i], row_info[j]) for i in range(len(row_info))
             for j in range(i + 1, len(row_info))]
    for top, bottom in pairs:
        h_top = top["y2"] - top["y1"]
        h_bot = bottom["y2"] - bottom["y1"]
        x_overlap = min(top["x2"], bottom["x2"]) - max(top["x1"], bottom["x1"])
        min_w = min(top["x2"] - top["x1"], bottom["x2"] - bottom["x1"])
        gap = bottom["y1"] - top["y2"]
        if (
            x_overlap > 0.3 * min_w
            and 0.5 <= h_top / max(h_bot, 1.0) <= 2.0
            and gap < 1.0 * max(h_top, h_bot)
        ):
            row_candidates.append(
                (top["text"] + bottom["text"], (top["score"] + bottom["score"]) / 2.0)
            )

    return row_candidates


def gather_candidates(texts, scores, boxes=None):
    raw = [(clean_text(t), float(s)) for t, s in zip(texts, scores)]

    if boxes is not None and len(boxes) and len(boxes) == len(texts):
        for merged_text, avg_score in cluster_rows(texts, scores, boxes):
            raw.append((clean_text(merged_text), float(avg_score)))

    candidates = []
    for cleaned, score in raw:
        tier = candidate_tier(cleaned)
        if tier is not None:
            candidates.append((tier, score, cleaned))
        if not USE_SG_CHECKSUM or tier == 3:
            continue
        repaired = []
        repaired += [(score * 0.95, f) for f in lookalike_corrections(cleaned)]
        repaired += [(score * 0.90, f) for f in extra_corrections(cleaned)]
        if tier == 2:
            repaired += [(score * 0.90, f) for f in confusion_corrections(cleaned, score)]
        candidates += [(3, s, f) for s, f in repaired
                       if s >= MIN_SCORE_REPAIRED and len(f) >= MIN_LEN_REPAIRED]

    return candidates


def rank_candidates(candidates):
    pooled = {}
    for tier, score, text in candidates:
        best_tier, best_score, votes = pooled.get(text, (tier, score, 0))
        pooled[text] = (max(best_tier, tier), max(best_score, score), votes + 1)
    ranked = [
        (tier, best + 0.02 * (votes - 1), text)
        for text, (tier, best, votes) in pooled.items()
    ]
    ranked.sort(key=lambda item: item[1] + (CHECKSUM_BONUS if item[0] == 3 else 0.0),
                reverse=True)
    return ranked


def has_acceptable(ranked):
    return any(is_acceptable(tier, score) for tier, score, _ in ranked)


def has_confident_checksum_plate(ranked):
    return any(tier == 3 and score >= CONFIDENT_SCORE for tier, score, _ in ranked)


def run_ocr_pass(padded_image_bgr):
    result = ocr.predict(padded_image_bgr)
    texts, scores, boxes = parse_pipeline_result(result)

    if not texts:
        fallback_result = _recognizer.predict(padded_image_bgr)
        texts, scores = parse_recognition_only_result(fallback_result)
        boxes = []

    return texts, scores, boxes


PAD_PX = 40


def plate_regions(texts, scores, boxes, scale, image_shape, offset=(0, 0)):
    items = []
    for text, score, box in zip(texts, scores, boxes):
        cleaned = clean_text(text)
        if not (1 <= len(cleaned) <= 8) or float(score) < ZOOM_MIN_BOX_SCORE:
            continue
        if box is None or len(box) < 4:
            continue
        x1, y1, x2, y2 = ((float(v) - PAD_PX) / scale for v in box[:4])
        x1, x2 = x1 + offset[0], x2 + offset[0]
        y1, y2 = y1 + offset[1], y2 + offset[1]
        items.append({"text": cleaned, "score": float(score),
                      "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                      "h": max(y2 - y1, 1.0)})
    if not items:
        return []

    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def grown(it):
        g = 0.8 * it["h"]
        return it["x1"] - g, it["y1"] - g, it["x2"] + g, it["y2"] + g

    for i in range(len(items)):
        a = grown(items[i])
        for j in range(i + 1, len(items)):
            b = grown(items[j])
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                parent[find(i)] = find(j)

    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)

    img_h, img_w = image_shape[:2]
    regions = []
    for group in groups.values():
        joined = "".join(it["text"] for it in group)
        if len(joined) > 10:
            continue
        has_letter = any(c.isalpha() for c in joined)
        has_digit = any(c.isdigit() for c in joined)
        rank = sum(it["score"] * len(it["text"]) for it in group)
        if has_letter and has_digit:
            rank *= 2.0
        x1 = min(it["x1"] for it in group)
        y1 = min(it["y1"] for it in group)
        x2 = max(it["x2"] for it in group)
        y2 = max(it["y2"] for it in group)
        char_h = max(it["h"] for it in group)
        mx = max(3.0 * char_h, 0.5 * (x2 - x1))
        my = 1.6 * char_h
        rect = (max(0, int(x1 - mx)), max(0, int(y1 - my)),
                min(img_w, int(x2 + mx)), min(img_h, int(y2 + my)))
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if (rect[2] - rect[0] < 16 or rect[3] - rect[1] < 16
                or area > ZOOM_MAX_AREA_FRACTION * img_w * img_h):
            continue
        regions.append((rank, rect))

    regions.sort(key=lambda r: r[0], reverse=True)
    return [rect for _, rect in regions]


def _overlaps(a, b, thresh=0.5):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return union > 0 and inter / union > thresh


def predict_license_plate(base64_string: str) -> str:
    t_start = time.perf_counter()
    image_bytes = decode_base64_image(base64_string)

    image = decode_image(image_bytes)

    if image is None:
        raise ValueError(
            "The Base64 data was decoded, but it is not a valid supported image."
        )

    if min(image.shape[:2]) < 16:
        return INVALID_PLATE

    all_candidates = []
    all_texts = []
    passes_done = 0
    slowest_pass = 0.0
    slowest_zoom = 0.0


    plan = list(PASS_ORDER)
    if not TRY_MULTIPLE_VARIANTS:
        plan = [p for p in plan if p[2] == "heavy"]
    plan += [(angle, RESIZE_TARGET_LONG_SIDE, v)
             for angle in ROTATION_FALLBACK_ANGLES
             for v in (("heavy", "light") if TRY_MULTIPLE_VARIANTS else ("heavy",))]

    plan = [("full",) + p for p in plan]
    zoomed = []

    resized_cache = {}
    while plan:
        task = plan.pop(0)
        if task[0] == "full":
            _, angle, long_side, variant_name = task
        else:
            _, rect, variant_name = task
            angle = 0
        if angle and has_acceptable(rank_candidates(all_candidates)):
            break
        if passes_done:
            elapsed = time.perf_counter() - t_start
            expected = slowest_pass if task[0] == "full" else (slowest_zoom or slowest_pass)
            if elapsed + expected > TIME_BUDGET_S:
                if task[0] == "full":
                    break
                continue

        if task[0] == "full":
            key = (angle, long_side)
            if key not in resized_cache:
                source = image
                if angle == 180:
                    source = cv2.rotate(image, cv2.ROTATE_180)
                elif angle == 90:
                    source = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                elif angle == 270:
                    source = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
                resized_cache[key] = resize_to_long_side(source, long_side)
            resized = resized_cache[key]
        else:
            x1, y1, x2, y2 = rect
            resized = resize_to_long_side(image[y1:y2, x1:x2], ZOOM_LONG_SIDE)
        variant = enhance_image_grayscale(resized) if variant_name == "heavy" else light_enhance(resized)

        padded = cv2.copyMakeBorder(
            variant,
            top=PAD_PX,
            bottom=PAD_PX,
            left=PAD_PX,
            right=PAD_PX,
            borderType=cv2.BORDER_CONSTANT,
            value=[255, 255, 255],
        )
        t_pass = time.perf_counter()
        texts, scores, boxes = run_ocr_pass(padded)
        took = time.perf_counter() - t_pass
        if task[0] == "full":
            slowest_pass = max(slowest_pass, took)
        else:
            slowest_zoom = max(slowest_zoom, took)
        all_texts.extend(zip(texts, scores))
        all_candidates.extend(gather_candidates(texts, scores, boxes))
        passes_done += 1

        if (ZOOM_ENABLED and angle == 0 and len(boxes)
                and len(zoomed) < ZOOM_MAX_REGIONS
                and not has_confident_checksum_plate(rank_candidates(all_candidates))):
            if task[0] == "full":
                scale = long_side / float(max(image.shape[:2]))
                offset = (0, 0)
            else:
                scale = ZOOM_LONG_SIDE / float(max(rect[2] - rect[0], rect[3] - rect[1]))
                offset = (rect[0], rect[1])
            for region in plate_regions(texts, scores, boxes, scale, image.shape, offset):
                if not any(_overlaps(region, z) for z in zoomed):
                    zoomed.append(region)
                    plan.insert(0, ("zoom", region, ZOOM_VARIANT))
                    break

        if has_confident_checksum_plate(rank_candidates(all_candidates)):
            break

    for tier, score, text in rank_candidates(all_candidates):
        if is_acceptable(tier, score):
            return text

    for source_text, source_score in all_texts:
        if float(source_score) < MIN_SCORE_FORMAT_ONLY:
            continue
        cleaned = clean_text(source_text)
        for start in range(len(cleaned)):
            match = re.match(PLATE_PATTERN, cleaned[start:])
            if match and candidate_tier(match.group(0)) == 3:
                return match.group(0)

    return INVALID_PLATE
