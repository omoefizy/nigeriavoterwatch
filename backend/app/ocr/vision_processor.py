"""
Google Cloud Vision OCR processor — higher accuracy fallback for poor-quality images.
Only used when GOOGLE_APPLICATION_CREDENTIALS is configured.
"""
import structlog
from app.config import settings

logger = structlog.get_logger()


def extract_text(image_bytes: bytes) -> dict:
    """
    Run Google Cloud Vision document_text_detection on image bytes.
    Returns {"text": str, "confidence": float, "engine": "google_vision"}.
    """
    if not settings.google_application_credentials:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS not configured")

    try:
        from google.cloud import vision

        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.document_text_detection(image=image)

        if response.error.message:
            raise RuntimeError(f"Vision API error: {response.error.message}")

        full_text = response.full_text_annotation.text

        # Compute average confidence from page blocks
        confidences = []
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                confidences.append(block.confidence)

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": full_text,
            "confidence": round(avg_confidence, 4),
            "engine": "google_vision",
        }
    except Exception as e:
        logger.error("Google Vision OCR failed", error=str(e))
        return {"text": "", "confidence": 0.0, "engine": "google_vision", "error": str(e)}


def is_available() -> bool:
    return bool(settings.google_application_credentials)
