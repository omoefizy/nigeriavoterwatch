"""
PDF -> image conversion for EC8A result sheets (pdf2image + poppler).

IReV serves most EC8A forms as PDFs that wrap one scanned page. The OCR engines
only take raster images, so each page is rendered to PNG before OCR.
Needs poppler's `pdftoppm` on PATH (brew install poppler / apt-get install poppler-utils).
"""
import io
import re

from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from PIL import Image

PDF_MAGIC = b"%PDF"
MAX_PAGES = 5               # EC8A is a single page; cap protects against odd uploads
# IReV PDFs are usually one phone photo on a page sized 1pt = 1px (72 DPI), so a
# fixed 300 DPI render would just upsample 4x (a 4032x3024 photo -> 211 MP, which
# trips Pillow's decompression-bomb guard). Instead scale the page so its long side
# is at least MIN_LONG_SIDE px (small photos get some help for Tesseract) and the
# total stays under MAX_PIXELS (keeps OCR time sane; Vision's limit is 75 MP).
MIN_LONG_SIDE = 3000
MAX_PIXELS = 25_000_000
MAX_DPI = 300


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(PDF_MAGIC)


def _limit_size(img: Image.Image) -> Image.Image:
    pixels = img.width * img.height
    if pixels <= MAX_PIXELS:
        return img
    scale = (MAX_PIXELS / pixels) ** 0.5
    return img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)


def _pick_dpi(pdf_bytes: bytes) -> int:
    """Choose a render DPI from the first page's size in points (see MIN_LONG_SIDE)."""
    info = pdfinfo_from_bytes(pdf_bytes)
    m = re.match(r"\s*([\d.]+)\s*x\s*([\d.]+)", str(info.get("Page size", "")))
    if not m:
        return 150
    w_in, h_in = float(m.group(1)) / 72, float(m.group(2)) / 72
    dpi = max(MIN_LONG_SIDE / max(w_in, h_in), 72)
    dpi = min(dpi, (MAX_PIXELS / (w_in * h_in)) ** 0.5, MAX_DPI)
    return max(int(dpi), 1)


def pdf_to_images(pdf_bytes: bytes, dpi: int | None = None) -> list[Image.Image]:
    """Render up to MAX_PAGES pages of a PDF to RGB PIL images."""
    dpi = dpi or _pick_dpi(pdf_bytes)
    pages = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=MAX_PAGES, fmt="png")
    return [_limit_size(p.convert("RGB")) for p in pages]


def pdf_to_png_bytes(pdf_bytes: bytes, dpi: int | None = None) -> list[bytes]:
    """Same as pdf_to_images but returns PNG-encoded bytes, ready for the OCR processors."""
    out = []
    for img in pdf_to_images(pdf_bytes, dpi=dpi):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def to_ocr_images(data: bytes, dpi: int | None = None) -> list[bytes]:
    """Turn a PDF into PNG page images, or return the image as is (JPEG/PNG)."""
    return pdf_to_png_bytes(data, dpi=dpi) if is_pdf(data) else [data]
