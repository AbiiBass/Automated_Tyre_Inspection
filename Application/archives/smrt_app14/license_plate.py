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
"""

from paddleocr import PaddleOCR, TextRecognition
import numpy as np
import cv2
import re
import binascii
import base64
import os

# Set these before importing PaddleOCR/PaddleX.
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"


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
recognizer = TextRecognition()


def decode_base64_image(base64_string: str) -> bytes:
    """Convert a raw Base64 string or data URL into image bytes."""
    base64_string = base64_string.strip()

    if "," in base64_string:
        base64_string = base64_string.split(",", 1)[1]

    # Remove whitespace/newlines that may have been introduced while copying.
    base64_string = re.sub(r"\s+", "", base64_string)

    # Restore optional Base64 padding.
    missing_padding = len(base64_string) % 4
    if missing_padding:
        base64_string += "=" * (4 - missing_padding)

    try:
        return base64.b64decode(base64_string, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid Base64 image string: {exc}") from exc


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


def candidate_tier(cleaned: str):
    """
    Score how "plate-shaped" a cleaned string looks.

      2 = matches the classic plate layout: 1-3 letters, 1-4 digits, then a single trailing check letter (e.g. SLN4444L, SLK6291A).
      1 = generic 6-8 character mix of letters and digits, but not in that exact shape.
      None = doesn't look like a plate at all (rejected).
    """
    if not (6 <= len(cleaned) <= 8):
        return None

    has_letters = bool(re.search(r"[A-Z]", cleaned))
    has_digits = bool(re.search(r"\d", cleaned))
    if not (has_letters and has_digits):
        return None

    if re.fullmatch(r"[A-Z]{1,3}[0-9]{1,4}[A-Z]", cleaned):
        return 2
    return 1


def cluster_rows(texts, scores, boxes, y_overlap_ratio=0.4):
    """
    Group detected text boxes into rows based on vertical overlap, so a plate split across multiple boxes (e.g. SLN 4444 L) is
    treated as one line, while unrelated text elsewhere in the frame stays in its own cluster and doesn't get glued on.

    Returns a list of (merged_text, average_score) tuples, one per row.
    """
    items = []
    for text, score, box in zip(texts, scores, boxes):
        if box is None or len(box) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        items.append(
            {
                "text": text,
                "score": float(score),
                "x1": x1,
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
    for row in rows:
        if len(row) < 2:
            continue  # a lone box is already covered by the per-text pass
        row.sort(key=lambda it: it["x1"])
        merged_text = "".join(it["text"] for it in row)
        avg_score = sum(it["score"] for it in row) / len(row)
        row_candidates.append((merged_text, avg_score))

    return row_candidates


def gather_candidates(texts, scores, boxes=None):
    """Return a list of (tier, score, cleaned_text) plate candidates."""
    candidates = []

    for text, score in zip(texts, scores):
        cleaned = clean_text(text)
        tier = candidate_tier(cleaned)
        if tier is not None:
            candidates.append((tier, float(score), cleaned))

    if boxes and len(boxes) == len(texts):
        for merged_text, avg_score in cluster_rows(texts, scores, boxes):
            cleaned = clean_text(merged_text)
            tier = candidate_tier(cleaned)
            if tier is not None:
                candidates.append((tier, avg_score, cleaned))

    return candidates


def run_ocr_pass(padded_image_bgr):
    """
    Run detection+recognition (with recognition-only fallback) on a single already-resized/padded image and return (texts, scores, boxes).
    Boxes is [] when the recognition-only fallback was used.
    """
    result = ocr.predict(padded_image_bgr)
    texts, scores, boxes = parse_pipeline_result(result)

    if not texts:
        fallback_result = recognizer.predict(padded_image_bgr)
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

    resized = resize_to_long_side(image)

    variants = [enhance_image_grayscale(resized)]
    if TRY_MULTIPLE_VARIANTS:
        variants.append(light_enhance(resized))

    all_candidates = []
    all_texts = []

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

    if all_candidates:
        all_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return all_candidates[0][2]

    # Nothing looked plate-shaped anywhere. Try to salvage a plate-shaped
    # *substring* from whatever text was found, rather than returning
    # every stray character (icons, watermarks, reflections, etc.)
    # verbatim.
    combined_text = clean_text(" ".join(all_texts))
    match = re.search(r"[A-Z]{1,3}[0-9]{1,4}[A-Z]", combined_text)
    if match:
        return match.group(0)

    return combined_text


def main():
    base64_string = input("Enter Base64 image string: ").strip()

    try:
        prediction = predict_license_plate(base64_string)
        print(prediction)
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
