"""OCR extraction for scanned PDF documents.

Uses Tesseract OCR to extract text from scanned/image-based PDFs
when native text extraction fails.

Requirements:
    - System: tesseract, poppler (brew install tesseract poppler)
    - Python: pytesseract, pdf2image, Pillow
"""
import logging
import tempfile
import os
from dataclasses import dataclass
from typing import Optional, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy imports for optional dependencies
_pytesseract = None
_pdf2image = None
_Image = None


def _get_pytesseract():
    """Lazy import of pytesseract."""
    global _pytesseract
    if _pytesseract is None:
        try:
            import pytesseract
            _pytesseract = pytesseract
        except ImportError:
            logger.warning("pytesseract not installed. OCR will not be available.")
        except Exception as e:
            logger.warning(f"pytesseract import failed: {e}. OCR will not be available.")
    return _pytesseract


def _get_pdf2image():
    """Lazy import of pdf2image."""
    global _pdf2image
    if _pdf2image is None:
        try:
            from pdf2image import convert_from_path
            _pdf2image = convert_from_path
        except ImportError:
            logger.warning("pdf2image not installed. OCR will not be available.")
    return _pdf2image


def _get_pil():
    """Lazy import of PIL Image."""
    global _Image
    if _Image is None:
        try:
            from PIL import Image
            _Image = Image
        except ImportError:
            logger.warning("Pillow not installed. OCR will not be available.")
    return _Image


@dataclass
class OCRResult:
    """Result of OCR extraction."""
    text: str
    confidence: float  # 0.0 to 1.0
    pages_processed: int
    method: str  # 'tesseract'
    errors: Optional[List[str]] = None

    def is_high_quality(self, threshold: float = 0.7) -> bool:
        """Check if OCR result is high quality.

        Args:
            threshold: Minimum confidence threshold

        Returns:
            True if confidence meets threshold
        """
        return self.confidence >= threshold


class OCRExtractor:
    """OCR extraction for scanned PDFs using Tesseract."""

    # Minimum characters per page to consider text extraction successful
    MIN_CHARS_PER_PAGE = 100

    # Tesseract configuration for legal documents
    TESSERACT_CONFIG = '--oem 3 --psm 6'  # LSTM engine, assume uniform block of text

    def __init__(self, dpi: int = 300, language: str = 'eng'):
        """Initialize OCR extractor.

        Args:
            dpi: DPI for PDF to image conversion (higher = better quality but slower)
            language: Tesseract language code
        """
        self.dpi = dpi
        self.language = language

    def is_available(self) -> bool:
        """Check if OCR dependencies are available.

        Returns:
            True if all dependencies are installed
        """
        return all([
            _get_pytesseract() is not None,
            _get_pdf2image() is not None,
            _get_pil() is not None,
        ])

    def is_scanned_pdf(self, pdf_path: str, sample_pages: int = 3) -> bool:
        """Detect if a PDF is likely scanned/image-based.

        Checks if native text extraction returns minimal text,
        which suggests the PDF contains scanned images.

        Args:
            pdf_path: Path to PDF file
            sample_pages: Number of pages to sample

        Returns:
            True if PDF appears to be scanned
        """
        try:
            # Try pdfplumber first
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    total_chars = 0
                    pages_checked = min(len(pdf.pages), sample_pages)

                    for i in range(pages_checked):
                        text = pdf.pages[i].extract_text() or ''
                        total_chars += len(text.strip())

                    chars_per_page = total_chars / pages_checked if pages_checked > 0 else 0
                    is_scanned = chars_per_page < self.MIN_CHARS_PER_PAGE

                    logger.debug(
                        f"PDF scan detection: {chars_per_page:.0f} chars/page "
                        f"(threshold={self.MIN_CHARS_PER_PAGE}), scanned={is_scanned}"
                    )
                    return is_scanned
            except ImportError:
                pass

            # Try PyMuPDF as fallback
            try:
                import fitz
                doc = fitz.open(pdf_path)
                total_chars = 0
                pages_checked = min(len(doc), sample_pages)

                for i in range(pages_checked):
                    text = doc[i].get_text()
                    total_chars += len(text.strip())

                doc.close()
                chars_per_page = total_chars / pages_checked if pages_checked > 0 else 0
                return chars_per_page < self.MIN_CHARS_PER_PAGE
            except ImportError:
                pass

            # If no PDF library available, assume not scanned
            logger.warning("No PDF library available for scan detection")
            return False

        except Exception as e:
            logger.error(f"Error detecting scanned PDF: {e}")
            return False

    def extract_with_tesseract(
        self,
        pdf_path: str,
        max_pages: Optional[int] = None
    ) -> OCRResult:
        """Extract text from PDF using Tesseract OCR.

        Converts PDF pages to images and runs Tesseract on each.

        Args:
            pdf_path: Path to PDF file
            max_pages: Maximum pages to process (None = all)

        Returns:
            OCRResult with extracted text and confidence
        """
        pytesseract = _get_pytesseract()
        convert_from_path = _get_pdf2image()

        if pytesseract is None or convert_from_path is None:
            return OCRResult(
                text='',
                confidence=0.0,
                pages_processed=0,
                method='tesseract',
                errors=['OCR dependencies not available'],
            )

        errors = []
        all_text = []
        confidences = []
        pages_processed = 0

        try:
            # Convert PDF to images
            logger.info(f"Converting PDF to images at {self.dpi} DPI...")
            images = convert_from_path(
                pdf_path,
                dpi=self.dpi,
                first_page=1,
                last_page=max_pages,
            )

            for i, image in enumerate(images):
                try:
                    # Run OCR on this page
                    page_data = pytesseract.image_to_data(
                        image,
                        lang=self.language,
                        config=self.TESSERACT_CONFIG,
                        output_type=pytesseract.Output.DICT
                    )

                    # Extract text and calculate confidence
                    page_text = pytesseract.image_to_string(
                        image,
                        lang=self.language,
                        config=self.TESSERACT_CONFIG
                    )

                    # Calculate average confidence for this page
                    page_confs = [
                        int(c) for c in page_data['conf']
                        if c != '-1' and str(c).isdigit()
                    ]
                    if page_confs:
                        page_confidence = sum(page_confs) / len(page_confs) / 100.0
                        confidences.append(page_confidence)

                    all_text.append(page_text)
                    pages_processed += 1

                    logger.debug(
                        f"Page {i+1}: {len(page_text)} chars, "
                        f"confidence={page_confidence:.2f}" if page_confs else f"Page {i+1}: {len(page_text)} chars"
                    )

                except Exception as e:
                    error_msg = f"Error on page {i+1}: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

            # Calculate overall confidence
            overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            combined_text = '\n\n'.join(all_text)

            logger.info(
                f"OCR complete: {pages_processed} pages, "
                f"{len(combined_text)} chars, confidence={overall_confidence:.2f}"
            )

            return OCRResult(
                text=combined_text,
                confidence=overall_confidence,
                pages_processed=pages_processed,
                method='tesseract',
                errors=errors if errors else None,
            )

        except Exception as e:
            logger.error(f"OCR extraction failed: {e}", exc_info=True)
            return OCRResult(
                text='',
                confidence=0.0,
                pages_processed=pages_processed,
                method='tesseract',
                errors=[str(e)],
            )

    def extract(
        self,
        pdf_path: str,
        force_ocr: bool = False,
        max_pages: Optional[int] = None
    ) -> OCRResult:
        """Extract text from PDF, using OCR if needed.

        First attempts native text extraction. If that fails or returns
        minimal text, falls back to OCR.

        Args:
            pdf_path: Path to PDF file
            force_ocr: Always use OCR even if native extraction works
            max_pages: Maximum pages to OCR (None = all)

        Returns:
            OCRResult with extracted text
        """
        if not self.is_available():
            return OCRResult(
                text='',
                confidence=0.0,
                pages_processed=0,
                method='none',
                errors=['OCR dependencies not available'],
            )

        # Check if OCR is needed
        if not force_ocr and not self.is_scanned_pdf(pdf_path):
            logger.debug(f"PDF has native text, OCR not needed: {pdf_path}")
            return OCRResult(
                text='',
                confidence=0.0,
                pages_processed=0,
                method='native',
                errors=['PDF has native text, OCR not used'],
            )

        # Run OCR
        logger.info(f"Running OCR on {'forced' if force_ocr else 'scanned'} PDF: {pdf_path}")
        return self.extract_with_tesseract(pdf_path, max_pages)


# Module-level singleton
_extractor: Optional[OCRExtractor] = None


def get_ocr_extractor() -> OCRExtractor:
    """Get or create the OCR extractor singleton."""
    global _extractor
    if _extractor is None:
        _extractor = OCRExtractor()
    return _extractor


def extract_text_ocr(pdf_path: str, force: bool = False) -> Tuple[str, dict]:
    """Convenience function to extract text with OCR.

    Args:
        pdf_path: Path to PDF file
        force: Force OCR even if native text exists

    Returns:
        Tuple of (text, metadata dict with ocr_used, ocr_confidence, etc.)
    """
    extractor = get_ocr_extractor()
    result = extractor.extract(pdf_path, force_ocr=force)

    metadata = {
        'ocr_used': result.method == 'tesseract' and result.pages_processed > 0,
        'ocr_confidence': result.confidence,
        'ocr_method': result.method,
        'ocr_pages_processed': result.pages_processed,
    }

    if result.errors:
        metadata['ocr_errors'] = result.errors

    return result.text, metadata
