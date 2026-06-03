"""
Tesseract OCR processor for INEC result sheet images.
Preprocesses images with Pillow before OCR to improve accuracy.
"""
import pytesseract
from PIL import Image, ImageFilter, ImageEnhance
import io
import structlog
from app.config import settings

logger = structlog.get_logger()

pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

# Tesseract config optimized for printed election forms
TESS_CONFIG = r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz /-:"


def preprocess_image(image: Image.Image) -> Image.Image:
    """Enhance image quality for better OCR accuracy."""
    img = image.convert("L")                         # grayscale
    img = img.filter(ImageFilter.SHARPEN)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    img = img.point(lambda x: 0 if x < 128 else 255, "1")  # binarize
    return img


def extract_text(image_bytes: bytes) -> dict:
    """
    Run Tesseract on image bytes.
    Returns {"text": str, "confidence": float, "engine": "tesseract"}.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        processed = preprocess_image(image)

        data = pytesseract.image_to_data(
            processed,
            lang=settings.ocr_language,
            config=TESS_CONFIG,
            output_type=pytesseract.Output.DICT,
        )

        words = [
            w for w, c in zip(data["text"], data["conf"])
            if w.strip() and int(c) > 0
        ]
        confidences = [int(c) for c in data["conf"] if int(c) > 0]

        text = pytesseract.image_to_string(
            processed, lang=settings.ocr_language, config=TESS_CONFIG
        )
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": text,
            "confidence": round(avg_confidence / 100, 4),
            "engine": "tesseract",
            "word_count": len(words),
        }
    except Exception as e:
        logger.error("Tesseract OCR failed", error=str(e))
        return {"text": "", "confidence": 0.0, "engine": "tesseract", "error": str(e)}
