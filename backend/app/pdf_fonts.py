"""Shared Unicode PDF fonts for every ReportLab generator.

The bundled DejaVu Sans family (backend/app/fonts/, see DEJAVU-LICENSE.txt)
carries full Turkish glyph coverage (Ş İ ı ç ğ ö ü Ğ ş). The previous fonts
could not guarantee that: outputs.py used the built-in Type1 Helvetica whose
WinAnsi encoding lacks Ğ ğ İ ı Ş ş entirely (rendered as boxes), and
invoice_pdf.py used ReportLab's bundled Bitstream Vera. Register once here and
reference PDF_FONT / PDF_FONT_BOLD everywhere instead of hardcoded names.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
PDF_FONT = "AppSans"
PDF_FONT_BOLD = "AppSansBold"


def register_pdf_fonts() -> None:
    if PDF_FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(PDF_FONT, str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD, str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(PDF_FONT, normal=PDF_FONT, bold=PDF_FONT_BOLD)


register_pdf_fonts()
