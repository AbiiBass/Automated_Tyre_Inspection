import base64
import os
import re
import time
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from paddleocr import PaddleOCR, TextRecognition

os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"

app = FastAPI()

BLUR_THRESHOLD = 100.0
TEXT_DET_LIMIT_SIDE_LEN = 960
TEXT_DET_LIMIT_TYPE = "max"

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
    use_textline_orientation=False,
    enable_mkldnn=False,
    text_det_limit_side_len=TEXT_DET_LIMIT_SIDE_LEN,
    text_det_limit_type=TEXT_DET_LIMIT_TYPE,
)
recognizer = TextRecognition()

def _parse_pipeline_result(result):
    texts, scores, boxes = [], [], []
    for page in result:
        page_texts = page.get("rec_texts", []) or []
        page_scores = page.get("rec_scores", []) or []
        page_boxes = page.get("rec_boxes", None)
        if page_boxes is None or len(page_boxes) == 0:
            page_boxes = [None] * len(page_texts)
        texts.extend(page_texts)
        scores.extend(page_scores)
        boxes.extend([tuple(float(v) for v in b) if b is not None else None for b in page_boxes])
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
    denoised = cv2.bilateralFilter(img_bgr, d=9, sigmaColor=75, sigmaSpace=75)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)
    enhanced = cv2.cvtColor(cv2.merge((l_enhanced, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
    gaussian = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
    return sharpened

def select_plate_reading(texts, scores):
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

def process_plate(image_bytes):
    timing = {}
    t_start = time.perf_counter()
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return {"error": "Invalid image format or empty file decoded."}

        t0 = time.perf_counter()
        height, width = img.shape[:2]
        target_width = 800
        scale = target_width / float(width)
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        img_resized = cv2.resize(img, (target_width, int(height * scale)), interpolation=interp)

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
        timing["detect_recognize_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if not texts:
            t0 = time.perf_counter()
            rec_result = recognizer.predict(padded_img)
            texts, scores = _parse_recognition_only_result(rec_result)
            timing["recognition_fallback_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        full_text = " ".join(texts)
        overall_avg_confidence = (sum(scores) / len(scores)) if scores else 0.0

        warnings = []
        if is_blurry:
            warnings.append(f"Image may be too blurry (sharpness score {blur_score:.1f}).")

        plate_text, plate_confidence = select_plate_reading(texts, scores)
        if plate_text is not None:
            cleaned_text = plate_text
            confidence = plate_confidence
        else:
            cleaned_text = re.sub(r"[^A-Z0-9]", "", full_text.upper())
            confidence = overall_avg_confidence
            warnings.append("Could not isolate a standard 6-8 character alphanumeric license plate pattern.")

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

class Base64ImageRequest(BaseModel):
    image_base64: str

def decode_base64_image(base64_str: str) -> bytes:
    try:
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]
        return base64.b64decode(base64_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 string: {str(e)}")

@app.post("/api/ocr/plate/base64")
async def extract_plate_base64(payload: Base64ImageRequest):
    image_bytes = decode_base64_image(payload.image_base64)
    return process_plate(image_bytes)