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

import os

# Set these before importing PaddleOCR/PaddleX.
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"

import base64
import binascii
import re

import cv2
import numpy as np
from paddleocr import PaddleOCR, TextRecognition


TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

# Detection + recognition pipeline, matching the working license-plate code.
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_textline_orientation=False,
    enable_mkldnn=False,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
)

# Recognition-only fallback.
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
    texts = []
    scores = []

    for page in result:
        page_texts = page.get("rec_texts", []) or []
        page_scores = page.get("rec_scores", []) or []
        texts.extend(page_texts)
        scores.extend(page_scores)

    return texts, scores


def parse_recognition_only_result(result):
    texts = []
    scores = []

    for page in result:
        text = page.get("rec_text", "")
        if text:
            texts.append(text)
            scores.append(page.get("rec_score", 0.0))

    return texts, scores


def enhance_image(img_bgr):
    """Denoise, improve local contrast, and mildly sharpen the image."""
    denoised = cv2.bilateralFilter(
        img_bgr,
        d=9,
        sigmaColor=75,
        sigmaSpace=75,
    )

    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(
        clipLimit=3.0,
        tileGridSize=(8, 8),
    )
    l_enhanced = clahe.apply(l_channel)

    enhanced = cv2.cvtColor(
        cv2.merge((l_enhanced, a_channel, b_channel)),
        cv2.COLOR_LAB2BGR,
    )

    gaussian = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=3)
    return cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)


def resize_to_long_side(img_bgr, target_long_side=800):
    """Resize while preserving aspect ratio."""
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


def select_plate_reading(texts, scores):
    """
    Select the highest-confidence OCR result that looks like a
    6-8 character alphanumeric license plate.
    """
    candidates = []

    for text, score in zip(texts, scores):
        cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())

        if 6 <= len(cleaned) <= 8:
            has_letters = bool(re.search(r"[A-Z]", cleaned))
            has_digits = bool(re.search(r"\d", cleaned))

            if has_letters and has_digits:
                candidates.append((float(score), cleaned))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


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

    image = resize_to_long_side(image, 800)
    image = enhance_image(image)

    padded_image = cv2.copyMakeBorder(
        image,
        top=40,
        bottom=40,
        left=40,
        right=40,
        borderType=cv2.BORDER_CONSTANT,
        value=[255, 255, 255],
    )

    result = ocr.predict(padded_image)
    texts, scores = parse_pipeline_result(result)

    # Same recognition-only fallback used by the original working program.
    if not texts:
        fallback_result = recognizer.predict(padded_image)
        texts, scores = parse_recognition_only_result(fallback_result)

    plate = select_plate_reading(texts, scores)

    if plate is not None:
        return plate

    # Preserve the original fallback behaviour if OCR sees text but cannot
    # isolate a standard 6-8 character plate candidate.
    full_text = " ".join(texts)
    return re.sub(r"[^A-Z0-9]", "", full_text.upper())


def main():
    base64_string = input("Enter Base64 image string: ").strip()

    try:
        prediction = predict_license_plate(base64_string)
        print(prediction)
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
