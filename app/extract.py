"""Content extraction utilities."""
from __future__ import annotations

import csv
import io
import logging
import shutil
import sys
import warnings
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pdfminer.high_level import extract_text as pdf_extract_text
from PIL import Image
import pytesseract

from .scan import FileMeta, detect_encoding

# Придушити всі попередження від pdfminer.six про невалідні кольори та обмеження PDF
logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="pdfminer")
warnings.filterwarnings("ignore", message=".*invalid float value.*")
warnings.filterwarnings("ignore", message=".*should not allow text extraction.*")


@dataclass
class ExtractionResult:
    text: str
    source: str
    quality: float


def ensure_tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def extract_text(meta: FileMeta, ocr_lang: str = "ukr+eng") -> ExtractionResult:
    ext = meta.ext
    if ext in {".txt", ".md", ".log", ".csv"}:
        encoding = detect_encoding(meta.path) or "utf-8"
        with meta.path.open("r", encoding=encoding, errors="ignore") as f:
            if ext == ".csv":
                reader = csv.reader(f)
                text = "\n".join(",".join(row) for row in reader)
            else:
                text = f.read()
        return ExtractionResult(text=text, source="parser", quality=1.0)
    if ext == ".docx":
        doc = Document(str(meta.path))
        text = "\n".join(par.text for par in doc.paragraphs)
        return ExtractionResult(text=text, source="parser", quality=0.9)
    if ext == ".pdf":
        try:
            # Витягти текст з PDF, придушуючи всі попередження pdfminer
            # Створюємо буфер для перехоплення stderr
            stderr_buffer = io.StringIO()
            with redirect_stderr(stderr_buffer):
                text = pdf_extract_text(str(meta.path))
        except Exception as e:
            # Логуємо тільки серйозні помилки, не технічні попередження
            if "password" in str(e).lower():
                # PDF захищений паролем
                return ExtractionResult(text="", source="password_protected", quality=0.0)
            text = ""

        if text and text.strip():
            return ExtractionResult(text=text, source="parser", quality=0.7)

        # Якщо текст не вилучено, спробувати OCR
        if ensure_tesseract_available():
            return ExtractionResult(text="", source="needs_ocr", quality=0.0)

        return ExtractionResult(text="", source="unsupported", quality=0.0)
    if ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"} and ensure_tesseract_available():
        image = Image.open(meta.path)
        text = pytesseract.image_to_string(image, lang=ocr_lang)
        return ExtractionResult(text=text, source="ocr", quality=0.6)
    return ExtractionResult(text="", source="unsupported", quality=0.0)

