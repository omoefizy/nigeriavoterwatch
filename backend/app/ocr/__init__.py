from app.ocr import pdf_converter, tesseract_processor, vision_processor


def _extract_from_image(image_bytes: bytes) -> dict:
    """Try Google Vision first (if configured), fall back to Tesseract."""
    if vision_processor.is_available():
        result = vision_processor.extract_text(image_bytes)
        if result.get("confidence", 0) >= 0.6:
            return result

    return tesseract_processor.extract_text(image_bytes)


def extract_text_best_effort(file_bytes: bytes) -> dict:
    """
    OCR a result sheet given as an image (JPEG/PNG) or a PDF.
    PDFs are rendered page by page with pdf2image/poppler first; page texts are
    joined and confidence is the mean across pages.
    """
    pages = pdf_converter.to_ocr_images(file_bytes)
    if len(pages) == 1:
        return _extract_from_image(pages[0])

    results = [_extract_from_image(p) for p in pages]
    return {
        "text": "\n\f\n".join(r.get("text", "") for r in results),
        "confidence": round(sum(r.get("confidence", 0.0) for r in results) / len(results), 4),
        "engine": ",".join(sorted({r.get("engine", "") for r in results})),
        "page_count": len(results),
    }
