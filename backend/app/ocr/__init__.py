from app.ocr import tesseract_processor, vision_processor


def extract_text_best_effort(image_bytes: bytes) -> dict:
    """Try Google Vision first (if configured), fall back to Tesseract."""
    if vision_processor.is_available():
        result = vision_processor.extract_text(image_bytes)
        if result.get("confidence", 0) >= 0.6:
            return result

    return tesseract_processor.extract_text(image_bytes)
