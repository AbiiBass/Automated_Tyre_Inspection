"""
license_plate.py

Standalone license-plate OCR from a Base64-encoded image.

Input:
    A Base64 image string, either:
      - raw Base64, or
      - a data URL such as: data:image/jpeg;base64,...

Output:
    The predicted license plate string only.

Required packages:
    pip install paddleocr paddlepaddle opencv-python numpy

Revision history:
    rev1 (2026-10-26)
      - Env vars are now set BEFORE paddleocr is imported (previously they
        were set after the import, so they had no effect).
      - Two-row plates (e.g. "SJD" over "6534Y") are merged top-to-bottom.
      - Adaptive smaller-scale retry: very large characters break the text
        detector at 960 px, so if no confident plate is found the image is
        re-read at RETRY_LONG_SIDES (e.g. 480 px).
      - Singapore plate checksum is used to rank candidates (a checksum-valid
        reading beats an invalid one), and digit/letter look-alikes in the
        wrong position (8<->B, 0<->O/D, 1<->I, 5<->S, 2<->Z, 6<->G) are
        corrected when the correction yields a checksum-valid plate.
      - Identical readings from different variants/scales are pooled (votes).
      - Upside-down photos: if no checksum-valid plate is found, the image is
        re-read rotated 180 deg (ROTATION_FALLBACK_ANGLES). Costs time only
        on images that would otherwise fail.
      - Stacked rows are ordered by row centre, so tilted two-row plates no
        longer come out bottom-line-first ("6534YSJD").
      - Nothing plate-shaped found -> returns "" instead of stray characters.
      - Recognition-only fallback model is loaded lazily (only if needed).
      - URL-safe Base64 (-, _) is accepted, empty payloads are rejected.
    Public interface unchanged: predict_license_plate(base64_string) -> str
"""

import os

# Set these before importing PaddleOCR/PaddleX (they are read at import time).
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"

from paddleocr import PaddleOCR, TextRecognition
import numpy as np
import cv2
import re
import binascii
import base64


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

# Images are resized so their long side is this many pixels before padding and OCR. Matched to TEXT_DET_LIMIT_SIDE_LEN above.
# Small images are upscaled.
RESIZE_TARGET_LONG_SIDE = 960
# Run OCR on more than one preprocessed version of the image and pool the results, instead of trusting a single pass. Costs roughly 2x the inference time.
TRY_MULTIPLE_VARIANTS = True

# rev1: extra long-side sizes tried ONLY when the main pass finds no confident
# plate. Large characters (close-up photos, plate filling the frame) are
# missed by the detector at 960 px but read perfectly at ~480 px.
RETRY_LONG_SIDES = (480,)

# rev1: a reading at or above this score that also passes the Singapore
# checksum is accepted without running the retry passes.
CONFIDENT_SCORE = 0.85

# rev1: use the Singapore vehicle-plate checksum letter to rank candidates.
USE_SG_CHECKSUM = True

# rev1: if no checksum-valid plate is found, also try the image rotated by
# these angles (180 = upside-down photo). Only costs time on failures.
ROTATION_FALLBACK_ANGLES = (180,)

# rev1: final answer must score at least this, else "" is returned.
MIN_ACCEPT_SCORE = 0.30

SG_CHECKSUM_WEIGHTS = (9, 4, 5, 4, 3, 2)
SG_CHECKSUM_LETTERS = "AZYXUTSRPMLKJHGEDCB"

# Look-alike characters, used to fix a digit read in the letter prefix or a
# letter read in the number part (e.g. "TI8762S" -> "TIB762S").
DIGIT_TO_LETTER = {"8": "B", "0": "O", "1": "I", "5": "S", "2": "Z", "6": "G", "4": "A", "7": "T"}
LETTER_TO_DIGIT = {"B": "8", "O": "0", "D": "0", "Q": "0", "I": "1", "L": "1",
                   "S": "5", "Z": "2", "G": "6", "T": "7", "A": "4"}


# Detection + recognition pipeline.
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
)

# Recognition-only fallback, used when detection finds no text at all.
# rev1: loaded lazily - it is a separate (large) model that is rarely needed.
_recognizer = None


def get_recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = TextRecognition()
    return _recognizer


def decode_base64_image(base64_string: str) -> bytes:
    """Convert a raw Base64 string or data URL into image bytes."""
    if not isinstance(base64_string, str):
        raise ValueError("Invalid Base64 image string: expected a str.")
    base64_string = base64_string.strip()

    if "," in base64_string:
        base64_string = base64_string.split(",", 1)[1]

    # Remove whitespace/newlines that may have been introduced while copying.
    base64_string = re.sub(r"\s+", "", base64_string)
    # rev1: accept URL-safe Base64 too.
    base64_string = base64_string.replace("-", "+").replace("_", "/")

    # Restore optional Base64 padding.
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
    """
    Pull texts/scores/boxes out of the PaddleOCR pipeline result.

    `rec_boxes` comes back as a numpy array, so it must never be combined
    with `or []` (truth-testing a multi-element numpy array raises
    ValueError) - it's handled with an explicit None check instead.
    """
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

    # keep boxes aligned with texts, or drop them entirely if something upstream ever returns a mismatched count.
    if len(boxes) != len(texts):
        boxes = []

    return texts, scores, boxes


def parse_recognition_only_result(result):
    """Recognition-only fallback has no detection boxes."""
    texts = []
    scores = []

    for page in result:
        text = page.get("rec_text", "")
        if text:
            texts.append(text)
            scores.append(page.get("rec_score", 0.0))

    return texts, scores


def enhance_image_grayscale(img_bgr):
    """
    Convert to grayscale, denoise, boost local contrast, and mildly sharpen.

    Plate text is usually monochrome but photos of it can carry heavy rainbow-colored moire/chromatic noise that lives almost entirely in the color channels. 
    Converting to grayscale before denoising/contrast removes most of that noise in one step, instead of fighting it on the color image.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    denoised = cv2.fastNlMeansDenoising(
        gray,
        h=12,
        templateWindowSize=7,
        searchWindowSize=21,
    )

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)

    gaussian = cv2.GaussianBlur(contrast, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(contrast, 1.5, gaussian, -0.5, 0)

    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def light_enhance(img_bgr):
    """
    A much gentler pass used as the second ensemble variant: mild
    denoising only. Keeps a close-to-original option in the mix in case an image is already clean
    enough that heavier processing isnt needed or slightly hurts it.
    """
    return cv2.bilateralFilter(img_bgr, d=7, sigmaColor=50, sigmaSpace=50)


def resize_to_long_side(img_bgr, target_long_side=RESIZE_TARGET_LONG_SIDE):
    """Resize while preserving aspect ratio. Upscales small images too."""
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
    """
    rev1: Singapore plate checksum. The last two prefix letters (A=1..Z=26,
    a single-letter prefix is padded with 0) and the number padded to 4 digits
    are weighted by 9,4,5,4,3,2; sum mod 19 indexes AZYXUTSRPMLKJHGEDCB.
    e.g. SBS3306A: B=2,S=19 -> 18+76+15+12+0+12 = 133, 133 % 19 = 0 -> "A".
    """
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
    """
    rev1: Return checksum-valid plates obtained by swapping look-alike
    characters that sit in the wrong section (digit in the letter prefix,
    letter in the number, digit as the check letter). Only corrections that
    produce a VALID checksum are returned, so a correct-but-unusual reading
    is never "fixed" into something else at random.
    """
    out = set()
    n = len(cleaned)
    if not (3 <= n <= 8):
        return out
    for p in range(1, 4):
        num_len = n - p - 1
        if not (1 <= num_len <= 4):
            continue
        prefix, number, check = cleaned[:p], cleaned[p:-1], cleaned[-1]
        new_prefix = "".join(DIGIT_TO_LETTER.get(c, c) if c.isdigit() else c for c in prefix)
        new_number = "".join(LETTER_TO_DIGIT.get(c, c) if c.isalpha() else c for c in number)
        new_check = DIGIT_TO_LETTER.get(check, check) if check.isdigit() else check
        fixed = new_prefix + new_number + new_check
        if fixed != cleaned and re.fullmatch(PLATE_PATTERN, fixed) and sg_checksum_valid(fixed):
            out.add(fixed)
    return out


def candidate_tier(cleaned: str):
    """
    Score how "plate-shaped" a cleaned string looks.

      3 = (rev1) plate layout AND the Singapore checksum letter is correct.
      2 = matches the classic plate layout: 1-3 letters, 1-4 digits, then a single trailing check letter (e.g. SLN4444L, SLK6291A).
      1 = generic 6-8 character mix of letters and digits, but not in that exact shape.
      None = doesn't look like a plate at all (rejected).
    """
    # rev1: short plates such as "SG1D" / "E12A" are legal, so the plate
    # layout is checked before the generic 6-8 length rule.
    if re.fullmatch(PLATE_PATTERN, cleaned):
        if USE_SG_CHECKSUM and sg_checksum_valid(cleaned):
            return 3
        return 2 if len(cleaned) >= 4 else None

    if not (6 <= len(cleaned) <= 8):
        return None

    has_letters = bool(re.search(r"[A-Z]", cleaned))
    has_digits = bool(re.search(r"\d", cleaned))
    if not (has_letters and has_digits):
        return None
    return 1


def cluster_rows(texts, scores, boxes, y_overlap_ratio=0.4):
    """
    Group detected text boxes into rows based on vertical overlap, so a plate split across multiple boxes (e.g. SLN 4444 L) is
    treated as one line, while unrelated text elsewhere in the frame stays in its own cluster and doesn't get glued on.

    Returns a list of (merged_text, average_score) tuples, one per row.

    rev1: also returns the top+bottom join of vertically STACKED rows, for
    two-line plates such as "SJD" over "6534Y" (rear/square plates).
    """
    items = []
    for text, score, box in zip(texts, scores, boxes):
        if box is None or len(box) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        if not clean_text(text):
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
            if overlap > y_overlap_ratio * min_h:
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
            continue  # a lone box is already covered by the per-text pass
        row_candidates.append((merged_text, avg_score))
        # rev1: on a tilted two-line plate both lines can land in one "row";
        # also offer the top-to-bottom order (the checksum picks the right one).
        by_height = sorted(row, key=lambda it: it["cy"])
        if by_height != row:
            row_candidates.append(("".join(it["text"] for it in by_height), avg_score))

    # rev1: stacked rows -> two-line plate. The two rows must overlap
    # horizontally, have similar letter heights and a small vertical gap.
    # Ordered by row CENTRE so a tilted plate still reads top line first.
    row_info.sort(key=lambda r: (r["y1"] + r["y2"]) / 2.0)
    for top, bottom in zip(row_info, row_info[1:]):
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
    """Return a list of (tier, score, cleaned_text) plate candidates."""
    raw = [(clean_text(t), float(s)) for t, s in zip(texts, scores)]

    if boxes is not None and len(boxes) and len(boxes) == len(texts):
        for merged_text, avg_score in cluster_rows(texts, scores, boxes):
            raw.append((clean_text(merged_text), float(avg_score)))

    candidates = []
    for cleaned, score in raw:
        tier = candidate_tier(cleaned)
        if tier is not None:
            candidates.append((tier, score, cleaned))
        # rev1: look-alike correction (only kept if the checksum then passes)
        if USE_SG_CHECKSUM and tier != 3:
            for fixed in lookalike_corrections(cleaned):
                candidates.append((3, score * 0.95, fixed))

    return candidates


def rank_candidates(candidates):
    """
    rev1: pool identical readings (from different variants/scales). Returns a
    list of (tier, pooled_score, text) sorted best first. Each extra vote
    adds a small bonus on top of the best single score.
    """
    pooled = {}
    for tier, score, text in candidates:
        best_tier, best_score, votes = pooled.get(text, (tier, score, 0))
        pooled[text] = (max(best_tier, tier), max(best_score, score), votes + 1)
    ranked = [
        (tier, best + 0.02 * (votes - 1), text)
        for text, (tier, best, votes) in pooled.items()
    ]
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked


def run_ocr_pass(padded_image_bgr):
    """
    Run detection+recognition (with recognition-only fallback) on a single already-resized/padded image and return (texts, scores, boxes).
    Boxes is [] when the recognition-only fallback was used.
    """
    result = ocr.predict(padded_image_bgr)
    texts, scores, boxes = parse_pipeline_result(result)

    if not texts:
        fallback_result = get_recognizer().predict(padded_image_bgr)
        texts, scores = parse_recognition_only_result(fallback_result)
        boxes = []

    return texts, scores, boxes


def predict_license_plate(base64_string: str) -> str:
    """
    Take a Base64 image string and return the predicted license plate.
    """
    image_bytes = decode_base64_image(base64_string)

    image_array = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError(
            "The Base64 data was decoded, but it is not a valid supported image."
        )

    all_candidates = []
    all_texts = []

    # rev1: main size first; smaller sizes only if no confident plate yet;
    # then (only if still no checksum-valid plate) the rotated image.
    plan = [(0, RESIZE_TARGET_LONG_SIDE)] + [(0, s) for s in RETRY_LONG_SIDES]
    plan += [(angle, RESIZE_TARGET_LONG_SIDE) for angle in ROTATION_FALLBACK_ANGLES]

    for angle, long_side in plan:
        if angle:
            ranked = rank_candidates(all_candidates)
            if ranked and ranked[0][0] == 3:
                break
        source = image
        if angle == 180:
            source = cv2.rotate(image, cv2.ROTATE_180)
        elif angle == 90:
            source = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif angle == 270:
            source = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        resized = resize_to_long_side(source, long_side)

        variants = [enhance_image_grayscale(resized)]
        if TRY_MULTIPLE_VARIANTS:
            variants.append(light_enhance(resized))

        for variant in variants:
            padded = cv2.copyMakeBorder(
                variant,
                top=40,
                bottom=40,
                left=40,
                right=40,
                borderType=cv2.BORDER_CONSTANT,
                value=[255, 255, 255],
            )
            texts, scores, boxes = run_ocr_pass(padded)
            all_texts.extend(texts)
            all_candidates.extend(gather_candidates(texts, scores, boxes))

        ranked = rank_candidates(all_candidates)
        if ranked and ranked[0][0] == 3 and ranked[0][1] >= CONFIDENT_SCORE:
            break

    if all_candidates:
        best_tier, best_score, best_text = rank_candidates(all_candidates)[0]
        if best_score >= MIN_ACCEPT_SCORE:
            return best_text

    # Nothing looked plate-shaped anywhere. Try to salvage a plate-shaped
    # *substring* from whatever text was found, rather than returning
    # every stray character (icons, watermarks, reflections, etc.)
    # verbatim.
    combined_text = clean_text(" ".join(all_texts))
    match = re.search(PLATE_PATTERN, combined_text)
    if match and len(match.group(0)) >= 4:
        return match.group(0)

    # rev1: return "" (no plate) instead of the stray characters, e.g. a
    # blank wall used to return "E".
    return ""


def main():
    base64_string = input("Enter Base64 image string: ").strip()

    try:
        prediction = predict_license_plate(base64_string)
        print(prediction)
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
