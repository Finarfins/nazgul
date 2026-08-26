from __future__ import annotations

import inspect

TURKISH = "ŞİıçğöüĞş"


def test_pdf_fonts_cover_turkish_glyphs() -> None:
    """Every registered PDF font must map each Turkish glyph to a real glyph id."""
    from reportlab.pdfbase import pdfmetrics

    from app.pdf_fonts import PDF_FONT, PDF_FONT_BOLD, register_pdf_fonts

    register_pdf_fonts()
    for name in (PDF_FONT, PDF_FONT_BOLD):
        face = pdfmetrics.getFont(name).face
        missing = [ch for ch in TURKISH if not face.charToGlyph.get(ord(ch))]
        assert not missing, f"{name} is missing Turkish glyphs: {missing}"


def test_generated_pdf_embeds_unicode_font() -> None:
    """A rendered document with Turkish text must embed the DejaVu font."""
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    from app.pdf_fonts import PDF_FONT, register_pdf_fonts

    register_pdf_fonts()
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = PDF_FONT
    out = BytesIO()
    SimpleDocTemplate(out, pagesize=A4).build([
        Paragraph(f"Sipariş Özeti — {TURKISH} <b>Ağırlık: 15 kg müşteri</b>", styles["BodyText"]),
    ])
    payload = out.getvalue()
    assert payload.startswith(b"%PDF"), "output is not a PDF"
    assert b"DejaVuSans" in payload, "Unicode TTF is not embedded in the PDF"


def test_pdf_generators_do_not_use_legacy_fonts() -> None:
    """Regression tripwire: no generator may fall back to Helvetica or Vera."""
    from app import invoice_pdf, labels
    from app.routers import outputs

    invoice_source = inspect.getsource(invoice_pdf)
    outputs_source = inspect.getsource(outputs)
    labels_source = inspect.getsource(labels)
    assert "Vera.ttf" not in invoice_source
    assert '"Helvetica' not in outputs_source
    assert '"Helvetica' not in labels_source
    for source in (invoice_source, outputs_source, labels_source):
        assert "PDF_FONT" in source


def test_barcode_label_draws_every_text_in_the_unicode_font() -> None:
    """Etiket şablonundaki her metin parçası AppSans ailesinde olmalıdır.

    ``canvas.setFont`` çağrıları dağınık olduğu için yerleşim, çizimden ayrı bir
    "plan" olarak üretilir; gerileme burada, PDF baytlarını ayrıştırmadan
    yakalanır. Baytlara bakmak işe yaramaz: ReportLab tuvali hiç metin
    çizilmese bile PDF'e bir ``/BaseFont /Helvetica`` kaynağı yazar.
    """
    from reportlab.lib.units import mm

    from app.labels import A4_LABEL_H, A4_LABEL_W, LabelOptions, label_plan
    from app.pdf_fonts import PDF_FONT, PDF_FONT_BOLD

    product = {
        "id": 1,
        "name": f"Şanzıman {TURKISH}",
        "product_code": "CL-0295",
        "barcode": "8690123456789",
        "oem_number": "NH-8451",
        "brand": "New Holland",
        "unit": "Adet",
        "sale_price": "1200.00",
        "vat_rate": "20",
    }
    for width, height in ((40 * mm, 30 * mm), (A4_LABEL_W, A4_LABEL_H)):
        plan = label_plan(product, LabelOptions(), width, height)
        assert plan.runs, "etiket boş olamaz"
        for run in plan.runs:
            assert run.font in (PDF_FONT, PDF_FONT_BOLD), (run.text, run.font)
