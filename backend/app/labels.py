"""Yazdırılabilir ürün barkod etiketi (Code128) üretimi.

Sembolojisi **Code128**'dir: bu projedeki ürün kodları alfanümeriktir
(``CL-0295``, ``NH-8451``) ve EAN-13'e sığmaz. ReportLab'in yerleşik
``reportlab.graphics.barcode.code128`` çizicisi kullanılır (``invoice_pdf.py``
de aynı çiziciyi kullanır), yeni bağımlılık eklenmez.

Modül bilinçli olarak ``routers/outputs.py``den ayrıdır: yerleşim hesabı saf ve
test edilebilir kalsın diye metinler önce :func:`label_plan` ile bir çizim
planına (``LabelRun`` listesi) dönüştürülür, çizim ondan sonra yapılır. Font
gerilemesi (bkz. ``app/pdf_fonts.py`` ve #146) böylece PDF baytlarını
ayrıştırmadan doğrulanabilir — ReportLab tuvali hiç metin çizilmese bile PDF'e
gömülü bir Helvetica kaynağı yazar, yani üretilen baytlarda o adı aramak
geçerli bir kontrol DEĞİLDİR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from numbers import Real
from typing import Literal, Sequence

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .business_time import business_now
from .money import HUNDRED, money
from .pdf_fonts import PDF_FONT, PDF_FONT_BOLD, register_pdf_fonts

register_pdf_fonts()

LabelFormat = Literal["thermal", "a4"]
PriceVat = Literal["incl", "excl"]
RunAlign = Literal["left", "center", "right"]

# Sayfa geometrisi PDF puntosu cinsindedir ve PARA DEĞİLDİR; yine de bu depoda
# ``float`` adı hiçbir yerde geçmez (bkz. test_v2_9_decimal_contract.py: ikili
# kayan nokta finansal kodda yasaktır). Geometri için soyut ``Real`` kullanılır,
# tutarlar her zaman Decimal'dir.
Points = Real

# Termal etiket varsayılanı ve sınırları. 40x30 ve 50x30 Türkiye'de en yaygın
# termal etiket ölçüleridir; sınırlar makul bir aralığı zorlar.
DEFAULT_WIDTH_MM = 40
DEFAULT_HEIGHT_MM = 30
MIN_SIDE_MM = 20
MAX_SIDE_MM = 200

# A4 ızgara: piyasadaki 24'lü (3 sütun x 8 satır, 70x37 mm) standart etiket
# sayfasıyla örtüşür. 3*70 = 210 mm sayfa genişliğine tam oturur, 8*37 = 296 mm
# ise 297 mm yüksekliğe 0,5 mm payla sığar. Hücre içeriği ayrıca _padding()
# kadar içeriden başlar, böylece yazıcının basamadığı kenar boşluğuna taşmaz.
A4_COLS = 3
A4_ROWS = 8
A4_LABEL_W = 70.0 * mm
A4_LABEL_H = 37.0 * mm
A4_PER_PAGE = A4_COLS * A4_ROWS

# Kaynak koruması: tek istekte üretilebilecek etiket sayısı sınırlıdır.
MAX_COPIES = 100
MAX_PRODUCTS = 500
MAX_LABELS = 2000

# Code128 modül (en ince çubuk) genişliği üst sınırı: kısa kodların etiketi
# baştan sona kaplamasını engeller.
MAX_BAR_WIDTH = 0.42 * mm

# Code128 standardının çubukların iki yanında istediği sessiz bölge, modül
# cinsinden. Tarayıcı barkodun nerede başlayıp bittiğini bununla ayırt eder.
QUIET_MODULES = 10


def format_money_tr(value: object) -> str:
    """``1234.5`` -> ``1.234,50 TL`` (Türkçe binlik/ondalık ayırıcı)."""
    return (
        f"{money(value):,.2f} TL"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


@dataclass(frozen=True)
class LabelOptions:
    """Etiket çıktısının biçim ve içerik seçenekleri."""

    label_format: LabelFormat = "thermal"
    show_price: bool = True
    price_vat: PriceVat = "incl"
    copies: int = 1
    width_mm: int = DEFAULT_WIDTH_MM
    height_mm: int = DEFAULT_HEIGHT_MM
    printed_at: datetime | None = None

    def stamp(self) -> str:
        moment = self.printed_at or business_now()
        return moment.strftime("%d.%m.%Y %H:%M")


@dataclass(frozen=True)
class LabelRun:
    """Etiket üzerine basılacak tek bir metin parçası.

    Yerleşim ve font seçimi çizimden ayrıldığı için testler ``font`` alanına
    bakarak hiçbir metnin Helvetica'ya düşmediğini doğrulayabilir.
    """

    x: Points
    y: Points
    text: str
    font: str
    size: Points
    align: RunAlign = "left"


@dataclass(frozen=True)
class LabelPlan:
    """Bir etiketin tam çizim planı: metinler + (varsa) barkod."""

    runs: list[LabelRun] = field(default_factory=list)
    barcode_value: str | None = None
    barcode_x: Points = 0.0
    barcode_y: Points = 0.0
    barcode_height: Points = 0.0
    barcode_bar_width: Points = 0.0


def barcode_payload(product: dict) -> str | None:
    """Taranacak Code128 değerini seç: ``barcode`` yoksa ``product_code``.

    Code128 yalnızca ASCII taşır; Türkçe harf içeren bir kod tarayıcıda
    bozulacağı için ayıklanır. Geriye taranabilir bir şey kalmazsa ``None``
    döner ve etiket barkodsuz, yalnız metin olarak üretilir (hata verilmez).
    """
    for key in ("barcode", "product_code"):
        raw = str(product.get(key) or "").strip()
        cleaned = "".join(ch for ch in raw if 32 <= ord(ch) <= 126).strip()
        if cleaned:
            return cleaned
    return None


def _fit(text: object, font: str, size: Points, max_width: Points) -> str:
    """Metni verilen genişliğe kırp; kırpıldıysa sonuna ``…`` koy."""
    value = str(text or "")
    if not value:
        return ""
    if pdfmetrics.stringWidth(value, font, size) <= max_width:
        return value
    ellipsis = "…"
    while value and pdfmetrics.stringWidth(value + ellipsis, font, size) > max_width:
        value = value[:-1]
    return (value + ellipsis) if value else ""


def _price_text(product: dict, options: LabelOptions) -> str | None:
    """Satış fiyatını KDV dahil/hariç biçimle. Toggle kapalıysa ``None``.

    Bu projede birim fiyatlar KDV DAHİL saklanır (bkz.
    ``app/money.py::apply_global_discount``), bu yüzden ``incl`` alanı olduğu
    gibi basar, ``excl`` KDV'yi ayrıştırır.
    """
    if not options.show_price:
        return None
    gross = money(product.get("sale_price"))
    if options.price_vat == "excl":
        rate = Decimal(str(product.get("vat_rate") or 0))
        net = money(gross / (Decimal("1") + rate / HUNDRED)) if rate else gross
        return f"{format_money_tr(net)} + KDV"
    return format_money_tr(gross)


def _padding(width: Points, height: Points) -> Points:
    """Etiket kenar boşluğu: küçük termal etikette daralır."""
    return max(1.5 * mm, min(width, height) * 0.06)


def _barcode(value: str, bar_height: Points, bar_width: Points) -> code128.Code128:
    """Code128 çizici. ``quiet=False`` kasıtlıdır (bkz. :func:`label_plan`)."""
    return code128.Code128(
        value,
        barHeight=bar_height,
        barWidth=bar_width,
        humanReadable=False,
        quiet=False,
    )


def label_plan(
    product: dict,
    options: LabelOptions,
    width: Points,
    height: Points,
) -> LabelPlan:
    """Tek bir etiketin yerleşimini hesapla (çizim yapmadan).

    Yukarıdan aşağıya: ürün adı, kod/OEM, marka/birim, Code128 barkod (+
    okunabilir değeri), en altta fiyat ve yazdırma zamanı. Ölçüler etiket
    boyutuna oranlanır, böylece 40x30 mm termal etiket ile 70x37 mm A4 hücresi
    aynı şablonu paylaşır.
    """
    pad = _padding(width, height)
    inner = width - 2 * pad
    runs: list[LabelRun] = []

    name_size = max(5.5, min(9.0, height * 0.075 / mm))
    meta_size = max(4.5, min(7.0, name_size - 1.5))
    stamp_size = max(3.8, min(5.5, meta_size - 1.2))
    price_size = max(7.0, min(13.0, height * 0.11 / mm))

    cursor = height - pad - name_size
    runs.append(
        LabelRun(
            x=pad,
            y=cursor,
            text=_fit(product.get("name"), PDF_FONT_BOLD, name_size, inner),
            font=PDF_FONT_BOLD,
            size=name_size,
        )
    )

    code = str(product.get("product_code") or "").strip()
    oem = str(product.get("oem_number") or "").strip()
    code_line = " · ".join(
        part
        for part in (f"Kod: {code}" if code else "", f"OEM: {oem}" if oem else "")
        if part
    ) or "Kod: -"
    cursor -= meta_size + 1.2
    runs.append(
        LabelRun(
            x=pad,
            y=cursor,
            text=_fit(code_line, PDF_FONT, meta_size, inner),
            font=PDF_FONT,
            size=meta_size,
        )
    )

    brand = str(product.get("brand") or "").strip()
    unit = str(product.get("unit") or "").strip()
    brand_line = " · ".join(part for part in (brand, unit) if part) or "-"
    cursor -= meta_size + 1.2
    runs.append(
        LabelRun(
            x=pad,
            y=cursor,
            text=_fit(brand_line, PDF_FONT, meta_size, inner),
            font=PDF_FONT,
            size=meta_size,
        )
    )

    # Alt şerit: yazdırma zamanı sağ alt köşede, fiyat (varsa) onun üstünde.
    runs.append(
        LabelRun(
            x=width - pad,
            y=pad,
            text=options.stamp(),
            font=PDF_FONT,
            size=stamp_size,
            align="right",
        )
    )
    bottom = pad + stamp_size + 1.0

    price = _price_text(product, options)
    if price:
        runs.append(
            LabelRun(
                x=pad,
                y=bottom,
                text=_fit(price, PDF_FONT_BOLD, price_size, inner),
                font=PDF_FONT_BOLD,
                size=price_size,
            )
        )
        bottom += price_size + 1.0

    value = barcode_payload(product)
    if not value:
        return LabelPlan(runs=runs)

    # Barkod, meta metinleriyle alt şerit arasında kalan boşluğa yerleşir;
    # okunabilir değeri hemen çubukların altına basılır.
    hr_size = max(4.2, min(6.5, meta_size))
    band = (cursor - 1.5) - bottom - hr_size - 1.0
    bar_height = max(4.0 * mm, band)

    # Code128 genişliği YALNIZCA quiet=False iken modül sayısıyla doğru
    # orantılıdır: ReportLab varsayılan sessiz bölge olarak her iki yana
    # ``max(0.25 inch, 10*barWidth)`` ekler ve bu, küçük çubuk genişliklerinde
    # sabit 18 puntoya sabitlenip oranı bozar (uzun kod etiketin dışına taşardı).
    # Sessiz bölgeyi kapatıp genişliği kendimiz hesaplıyoruz: standardın istediği
    # 10 modüllük sessiz bölge iki yana pay olarak eklendiği için bölme
    # ``modules + 20`` iledir. Böylece çubuklar da, sessiz bölge de etikete sığar.
    probe = _barcode(value, bar_height, 1.0)
    modules = probe.width or 1.0
    bar_width = min(MAX_BAR_WIDTH, inner / (modules + 2 * QUIET_MODULES))
    drawn_width = modules * bar_width

    runs.append(
        LabelRun(
            x=width / 2,
            y=bottom,
            text=_fit(value, PDF_FONT, hr_size, inner),
            font=PDF_FONT,
            size=hr_size,
            align="center",
        )
    )

    return LabelPlan(
        runs=runs,
        barcode_value=value,
        barcode_x=(width - drawn_width) / 2,
        barcode_y=bottom + hr_size + 1.0,
        barcode_height=bar_height,
        barcode_bar_width=bar_width,
    )


def _draw(pdf: canvas.Canvas, origin_x: Points, origin_y: Points, plan: LabelPlan) -> None:
    """Hazır planı tuvale bas."""
    for run in plan.runs:
        if not run.text:
            continue
        pdf.setFont(run.font, run.size)
        x = origin_x + run.x
        y = origin_y + run.y
        if run.align == "right":
            pdf.drawRightString(x, y, run.text)
        elif run.align == "center":
            pdf.drawCentredString(x, y, run.text)
        else:
            pdf.drawString(x, y, run.text)
    if plan.barcode_value:
        _barcode(
            plan.barcode_value, plan.barcode_height, plan.barcode_bar_width
        ).drawOn(pdf, origin_x + plan.barcode_x, origin_y + plan.barcode_y)


def expand(products: Sequence[dict], options: LabelOptions) -> list[dict]:
    """Her ürünü ``copies`` kadar tekrarla (etiket basımında yaygın istek)."""
    repeated: list[dict] = []
    for product in products:
        repeated.extend([product] * options.copies)
    return repeated


def render_labels(products: Sequence[dict], options: LabelOptions) -> bytes:
    """Ürün listesini tek bir PDF'e bas.

    ``thermal``: her sayfa tek etiket, sayfa boyutu = etiket boyutu.
    ``a4``: 3x8 ızgara; ızgara dolunca yeni sayfa açılır.
    """
    labels = expand(products, options)
    out = BytesIO()

    if options.label_format == "a4":
        pdf = canvas.Canvas(out, pagesize=A4)
        page_width, page_height = A4
        left = (page_width - A4_COLS * A4_LABEL_W) / 2
        top = (page_height - A4_ROWS * A4_LABEL_H) / 2
        for index, product in enumerate(labels):
            slot = index % A4_PER_PAGE
            if slot == 0 and index:
                pdf.showPage()
            row, column = divmod(slot, A4_COLS)
            origin_x = left + column * A4_LABEL_W
            # Izgara soldan sağa, YUKARIDAN aşağıya dolar; PDF ekseni aşağıdan
            # yukarı olduğu için satır indeksi tepeden çıkarılır.
            origin_y = top + (A4_ROWS - 1 - row) * A4_LABEL_H
            _draw(
                pdf,
                origin_x,
                origin_y,
                label_plan(product, options, A4_LABEL_W, A4_LABEL_H),
            )
        pdf.showPage()
    else:
        width = options.width_mm * mm
        height = options.height_mm * mm
        pdf = canvas.Canvas(out, pagesize=(width, height))
        for product in labels:
            _draw(pdf, 0.0, 0.0, label_plan(product, options, width, height))
            pdf.showPage()

    pdf.save()
    return out.getvalue()
