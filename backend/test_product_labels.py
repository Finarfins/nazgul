"""Barkod etiketi: Code128 içeriği, font, fiyat toggle'ı, biçimler ve tenant.

Yerleşim testleri saf ``app.labels`` üzerinde koşar (DB gerekmez). Uçtan uca
senaryo, ``test_cari_statement.py`` ile aynı desende ayrı bir süreçte izole bir
SQLite üzerinde çalışır: ``app.config.Settings`` modül düzeyinde tek örnektir,
bu yüzden veritabanı seçimi süreç başlamadan yapılmalıdır.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
import sys

from reportlab.lib.units import mm

from app.labels import (
    A4_LABEL_H,
    A4_LABEL_W,
    A4_PER_PAGE,
    LabelOptions,
    _barcode,
    barcode_payload,
    label_plan,
    render_labels,
)
from app.pdf_fonts import PDF_FONT, PDF_FONT_BOLD

BACKEND = Path(__file__).resolve().parent

STAMP = datetime(2026, 7, 26, 14, 5)

PRODUCT = {
    "id": 1,
    "name": "Şanzıman Conta Takımı",
    "product_code": "CL-0295",
    "barcode": "8690123456789",
    "oem_number": "NH-8451",
    "brand": "New Holland",
    "unit": "Adet",
    "sale_price": "1200.00",
    "vat_rate": "20",
}


def _pages(pdf: bytes) -> int:
    """PDF'teki sayfa sayısı (ReportLab sayfa sözlüklerini sıkıştırmaz)."""
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def _media_boxes(pdf: bytes) -> set[bytes]:
    return set(re.findall(rb"/MediaBox\s*\[[^\]]*\]", pdf))


def _texts(plan) -> list[str]:
    return [run.text for run in plan.runs]


def test_barcode_value_falls_back_to_product_code() -> None:
    """Barkod alanı boşsa ürün kodu taranır; ikisi de boşsa barkod basılmaz."""
    assert barcode_payload(PRODUCT) == "8690123456789"
    assert barcode_payload(dict(PRODUCT, barcode="")) == "CL-0295"
    assert barcode_payload(dict(PRODUCT, barcode=None, product_code=None)) is None
    # Code128 yalnızca ASCII taşır: Türkçe harfler ayıklanır, kalan basılır.
    assert barcode_payload(dict(PRODUCT, barcode="ÇĞİ-77")) == "-77"
    # Geriye taranabilir hiçbir şey kalmazsa ürün koduna düşülür.
    assert barcode_payload(dict(PRODUCT, barcode="ÇĞİ")) == "CL-0295"


def test_label_without_any_code_is_text_only_and_does_not_raise() -> None:
    """Kodsuz ürün hata vermeden, barkodsuz bir etiket üretir."""
    bare = dict(PRODUCT, barcode=None, product_code=None)
    plan = label_plan(bare, LabelOptions(printed_at=STAMP), 40 * mm, 30 * mm)
    assert plan.barcode_value is None
    assert any("Şanzıman" in text for text in _texts(plan))

    pdf = render_labels([bare], LabelOptions(printed_at=STAMP))
    assert pdf.startswith(b"%PDF")
    assert _pages(pdf) == 1


def test_every_text_run_uses_the_unicode_font() -> None:
    """Etiketteki HİÇBİR metin Helvetica'ya düşmemelidir.

    ReportLab tuvali metin çizilmese bile PDF'e bir Helvetica kaynağı yazar
    (``b"Helvetica" not in pdf`` bu yüzden geçersiz bir kontroldür), o hâlde
    gerileme çizim planındaki font adları üzerinden yakalanır. Helvetica'nın
    WinAnsi kodlaması Ş/ı/ğ/İ gliflerini hiç taşımaz (bkz. app/pdf_fonts.py,
    #146): bu test o kutucuk basma hatasının etiketlerdeki nöbetçisidir.
    """
    for options in (
        LabelOptions(printed_at=STAMP),
        LabelOptions(printed_at=STAMP, show_price=False),
        LabelOptions(printed_at=STAMP, price_vat="excl"),
        LabelOptions(label_format="a4", printed_at=STAMP),
    ):
        plan = label_plan(PRODUCT, options, 40 * mm, 30 * mm)
        fonts = {run.font for run in plan.runs}
        assert fonts <= {PDF_FONT, PDF_FONT_BOLD}, fonts
        assert PDF_FONT_BOLD in fonts, "ürün adı kalın Unicode fontta olmalı"

    # Türkçe adın kendisi de etikete kırpılmadan/bozulmadan girmelidir.
    plan = label_plan(PRODUCT, LabelOptions(printed_at=STAMP), 70 * mm, 37 * mm)
    assert "Şanzıman Conta Takımı" in _texts(plan)

    pdf = render_labels([PRODUCT], LabelOptions(printed_at=STAMP))
    assert b"DejaVuSans" in pdf, "Unicode TTF gömülmemiş"


def test_label_carries_code_oem_brand_unit_and_timestamp() -> None:
    plan = label_plan(PRODUCT, LabelOptions(printed_at=STAMP), 70 * mm, 37 * mm)
    texts = _texts(plan)
    assert "Kod: CL-0295 · OEM: NH-8451" in texts
    assert "New Holland · Adet" in texts
    assert "26.07.2026 14:05" in texts
    assert plan.barcode_value == "8690123456789"


def test_price_toggle_and_vat_mode() -> None:
    """Fiyat kapalıyken basılmaz; KDV hariç seçilince matrah yazılır.

    Birim fiyatlar bu projede KDV DAHİL saklanır (app/money.py), dolayısıyla
    %20 KDV'li 1.200,00 TL'nin matrahı 1.000,00 TL'dir.
    """
    off = label_plan(
        PRODUCT, LabelOptions(printed_at=STAMP, show_price=False), 40 * mm, 30 * mm
    )
    assert not any("TL" in text for text in _texts(off))

    incl = label_plan(PRODUCT, LabelOptions(printed_at=STAMP), 40 * mm, 30 * mm)
    assert "1.200,00 TL" in _texts(incl)

    excl = label_plan(
        PRODUCT, LabelOptions(printed_at=STAMP, price_vat="excl"), 40 * mm, 30 * mm
    )
    assert "1.000,00 TL + KDV" in _texts(excl)

    # KDV oranı 0 ise ayrıştırılacak bir şey yoktur, tutar aynı kalır.
    zero = label_plan(
        dict(PRODUCT, vat_rate="0"),
        LabelOptions(printed_at=STAMP, price_vat="excl"),
        40 * mm,
        30 * mm,
    )
    assert "1.200,00 TL + KDV" in _texts(zero)


def test_barcode_is_scaled_to_stay_inside_the_label() -> None:
    """Uzun kod bile etiket genişliğini aşmamalıdır.

    Gerileme koruması: ReportLab'in varsayılan sessiz bölgesi (``quiet=True``)
    çubuk genişliğiyle orantılı DEĞİLDİR, her iki yana sabit 18 punto ekler.
    Ölçekleme bu sabiti hesaba katmazsa uzun kodlar etiketin dışına taşar.
    """
    for width, height in ((40 * mm, 30 * mm), (A4_LABEL_W, A4_LABEL_H)):
        for value in ("CL-1", "NH-8451-UZUN-PARCA-KODU-2026-XL"):
            plan = label_plan(
                dict(PRODUCT, barcode=value),
                LabelOptions(printed_at=STAMP),
                width,
                height,
            )
            drawn = _barcode(
                plan.barcode_value, plan.barcode_height, plan.barcode_bar_width
            ).width
            assert plan.barcode_x >= 0, (value, width)
            assert plan.barcode_x + drawn <= width + 0.01, (value, width, drawn / mm)
            # Sessiz bölge: çubukların iki yanında en az 10 modül boşluk kalmalı.
            assert plan.barcode_x >= 10 * plan.barcode_bar_width, (value, width)


def test_thermal_page_equals_label_size() -> None:
    """Termal biçimde sayfa boyutu = etiket boyutu, her sayfada tek etiket."""
    pdf = render_labels([PRODUCT], LabelOptions(printed_at=STAMP))
    assert pdf.startswith(b"%PDF")
    assert _pages(pdf) == 1
    boxes = _media_boxes(pdf)
    assert len(boxes) == 1
    numbers = [float(value) for value in re.findall(rb"[\d.]+", boxes.pop())]
    assert abs(numbers[2] - 40 * mm) < 0.5, numbers
    assert abs(numbers[3] - 30 * mm) < 0.5, numbers

    # Ölçü parametreliyse sayfa da onunla büyür (50x30 yaygın TR ölçüsü).
    wide = render_labels(
        [PRODUCT], LabelOptions(printed_at=STAMP, width_mm=50, height_mm=30)
    )
    numbers = [float(v) for v in re.findall(rb"[\d.]+", _media_boxes(wide).pop())]
    assert abs(numbers[2] - 50 * mm) < 0.5, numbers


def test_a4_grid_overflows_to_a_new_page() -> None:
    """A4'te ızgara dolunca yeni sayfa açılır, sayfa boyutu hep A4 kalır."""
    options = LabelOptions(label_format="a4", printed_at=STAMP)
    assert A4_PER_PAGE == 24

    one_page = render_labels([PRODUCT] * A4_PER_PAGE, options)
    assert _pages(one_page) == 1

    overflow = render_labels([PRODUCT] * (A4_PER_PAGE + 1), options)
    assert _pages(overflow) == 2

    numbers = [float(v) for v in re.findall(rb"[\d.]+", _media_boxes(overflow).pop())]
    assert abs(numbers[2] - 595.27) < 1 and abs(numbers[3] - 841.89) < 1, numbers


def test_copies_multiply_the_label_count() -> None:
    """Kopya sayısı N ise her üründen N etiket basılır."""
    thermal = render_labels([PRODUCT], LabelOptions(printed_at=STAMP, copies=5))
    assert _pages(thermal) == 5

    # A4'te 24'lük ızgarayı taşıracak kadar kopya iki sayfaya yayılır.
    grid = render_labels(
        [PRODUCT, PRODUCT],
        LabelOptions(label_format="a4", printed_at=STAMP, copies=13),
    )
    assert _pages(grid) == 2


def test_legacy_label_endpoint_stays_option_free() -> None:
    """Eski QR ucu parametresizdir ve öyle kalmalıdır.

    Sözleşmeyi bozmanın en sessiz yolu uca seçenek eklemektir: bir sorgu
    parametresi eklenip varsayılanı kaydığında saha çıktısı kimse fark etmeden
    değişir. Code128 seçenekleri ``product_barcode_label`` ucuna aittir.
    """
    import inspect

    from app.routers import outputs

    assert list(inspect.signature(outputs.product_label).parameters) == [
        "product_id",
        "request",
        "db",
    ]
    # Barkod ucu ise seçenekleri taşır.
    barcode_params = set(inspect.signature(outputs.product_barcode_label).parameters)
    assert {
        "format",
        "show_price",
        "price_vat",
        "copies",
        "width_mm",
        "height_mm",
    } <= barcode_params


def test_label_endpoints_end_to_end(tmp_path: Path) -> None:
    """Uçlar: tekli, toplu, kopya, fiyat toggle'ı ve tenant izolasyonu."""
    database = tmp_path / "barkod-etiket.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    # Senaryo Türkçe metin basar; Windows'ta alt sürecin varsayılan konsol
    # kodlaması UTF-8 değildir ve çıktı okunamaz hâle gelir (stdout None döner).
    env["PYTHONIOENCODING"] = "utf-8"

    scenario = r'''
import re

# Eski QR etiketinde fiyatın BASILDIĞINI kanıtlamak için çizilen metinleri
# yakala: TTF alt kümesi kullanıldığında içerik akışında metin ASCII olarak
# aranamaz, bu yüzden bayt taraması yerine çizim çağrısı gözlenir.
import reportlab.pdfgen.canvas as rl_canvas
DRAWN = []
_original_draw_string = rl_canvas.Canvas.drawString


def _spy_draw_string(self, x, y, text, *args, **kwargs):
    DRAWN.append(text)
    return _original_draw_string(self, x, y, text, *args, **kwargs)


rl_canvas.Canvas.drawString = _spy_draw_string

from fastapi.testclient import TestClient
from app.main import app


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None


def pages(pdf):
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text
payload = login.json()
company_id = int(payload["companies"][0]["id"])
headers = {
    "Authorization": "Bearer " + payload["access_token"],
    "X-Company-ID": str(company_id),
}
if payload["user"].get("must_change_password"):
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "Etiket-Guvenli!2026"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]


def make_product(name, code, barcode):
    return ok(client.post("/api/products", headers=headers, json={
        "name": name, "product_code": code, "barcode": barcode,
        "sale_price": "1200.00", "purchase_price": "800.00", "vat_rate": 20,
        "stock": "10", "unit": "Adet", "oem_number": "NH-8451",
        "brand": "New Holland",
    }))


first = make_product("Şanzıman Conta Takımı", "CL-0295", "8690123456789")
second = make_product("Hidrolik Filtre Ağır", "NH-8451", "")
third = make_product("Yağ Keçesi İnce", "", "")

# ---- KİLİTLEYİCİ REGRESYON: eski QR etiketinin çıktı sözleşmesi ----
# Paramsız label.pdf sahadaki yazıcı/şablon ayarlarına bağlıdır. Bu blok
# sözleşmenin bir daha SESSİZCE değişmesini engeller: QR, 70x40 mm, fiyat açık.
DRAWN.clear()
legacy = client.get(f"/api/products/{first['id']}/label.pdf", headers=headers)
assert legacy.status_code == 200, (legacy.status_code, legacy.text[:300])
assert "application/pdf" in legacy.headers.get("content-type", "")
assert legacy.content.startswith(b"%PDF"), legacy.content[:20]
assert pages(legacy.content) == 1, pages(legacy.content)
# 70x40 mm = 198.4252 x 113.3858 punto.
boxes = re.findall(rb"/MediaBox\s*\[([^\]]*)\]", legacy.content)
assert len(boxes) == 1, boxes
sides = [float(value) for value in boxes[0].split()]
assert abs(sides[2] - 198.4252) < 0.5, sides
assert abs(sides[3] - 113.3858) < 0.5, sides
# QR bir GÖRÜNTÜ olarak gömülür; Code128 vektör çizgidir. Ayırt edici budur.
assert re.search(rb"/Subtype\s*/Image", legacy.content), "QR gorseli yok"
# Fiyat bu uçta HER ZAMAN basılır (kapatma seçeneği yoktur).
assert any("TL" in str(text) for text in DRAWN), DRAWN

# Yeni barkod ucu ayrıdır: 40x30 mm ve görüntü değil, vektör Code128.
barcode = client.get(f"/api/products/{first['id']}/barcode-label.pdf", headers=headers)
assert barcode.status_code == 200, barcode.text[:300]
sides = [float(v) for v in re.findall(rb"/MediaBox\s*\[([^\]]*)\]", barcode.content)[0].split()]
assert abs(sides[2] - 113.3858) < 0.5, sides
assert abs(sides[3] - 85.0394) < 0.5, sides
assert not re.search(rb"/Subtype\s*/Image", barcode.content), "Code128 etiketi gorsel icermemeli"

# Tekli: %PDF başlıklı, Unicode fontu gömülü tek sayfa.
single = client.get(f"/api/products/{first['id']}/barcode-label.pdf", headers=headers)
assert single.status_code == 200, (single.status_code, single.text[:300])
assert "application/pdf" in single.headers.get("content-type", "")
assert single.content.startswith(b"%PDF"), single.content[:20]
assert b"DejaVuSans" in single.content, "Unicode font gomulmemis"
assert pages(single.content) == 1, pages(single.content)

# Barkodsuz ve kodsuz ürünler de hatasız etiket üretir.
for product in (second, third):
    response = client.get(f"/api/products/{product['id']}/barcode-label.pdf", headers=headers)
    assert response.status_code == 200, (product["id"], response.status_code, response.text[:300])
    assert response.content.startswith(b"%PDF")

# Fiyat toggle'ı ve KDV kipi uçtan geçer.
for query in ("show_price=false", "show_price=true&price_vat=excl", "format=a4"):
    response = client.get(
        f"/api/products/{first['id']}/barcode-label.pdf?{query}", headers=headers)
    assert response.status_code == 200, (query, response.status_code, response.text[:300])
    assert response.content.startswith(b"%PDF"), query

# Kopya sayısı N -> termal biçimde N sayfa.
copies = client.get(
    f"/api/products/{first['id']}/barcode-label.pdf?copies=4", headers=headers)
assert copies.status_code == 200, copies.text[:300]
assert pages(copies.content) == 4, pages(copies.content)

# Toplu: 3 ürün tek PDF'te, termal biçimde 3 sayfa.
bulk = client.post("/api/products/labels.pdf", headers=headers, json={
    "product_ids": [first["id"], second["id"], third["id"]]})
assert bulk.status_code == 200, (bulk.status_code, bulk.text[:300])
assert bulk.content.startswith(b"%PDF")
assert pages(bulk.content) == 3, pages(bulk.content)

# Toplu + A4: 3 ürün x 2 kopya tek sayfaya sığar.
bulk_a4 = client.post("/api/products/labels.pdf", headers=headers, json={
    "product_ids": [first["id"], second["id"], third["id"]],
    "format": "a4", "copies": 2})
assert bulk_a4.status_code == 200, bulk_a4.text[:300]
assert pages(bulk_a4.content) == 1, pages(bulk_a4.content)

# Sınırlar: kopya üst sınırı ve boş liste doğrulama hatası verir.
assert client.get(
    f"/api/products/{first['id']}/barcode-label.pdf?copies=999", headers=headers
).status_code == 422
assert client.post(
    "/api/products/labels.pdf", headers=headers, json={"product_ids": []}
).status_code == 422
# Toplam etiket tavanı (2000) aşılırsa 400 ile reddedilir: 21 x 100 = 2100.
flood = client.post("/api/products/labels.pdf", headers=headers, json={
    "product_ids": [first["id"]] * 21, "copies": 100})
assert flood.status_code == 400, (flood.status_code, flood.text[:200])
# Tavanın hemen altı hâlâ üretilir (20 x 100 = 2000).
edge = client.post("/api/products/labels.pdf", headers=headers, json={
    "product_ids": [first["id"]] * 20, "copies": 100, "format": "a4"})
assert edge.status_code == 200, (edge.status_code, edge.text[:200])

# Tenant izolasyonu: B firmasından A firmasının ürününe etiket üretilemez.
other = ok(client.post("/api/companies", headers=headers,
                       json={"name": "İkinci Firma", "tax_number": "9999999999"}))
other_headers = dict(headers, **{"X-Company-ID": str(other["id"])})
for path in ("label.pdf", "barcode-label.pdf"):
    leak = client.get(f"/api/products/{first['id']}/{path}", headers=other_headers)
    assert leak.status_code == 404, (path, leak.status_code, leak.text[:200])
bulk_leak = client.post("/api/products/labels.pdf", headers=other_headers,
                        json={"product_ids": [first["id"]]})
assert bulk_leak.status_code == 404, (bulk_leak.status_code, bulk_leak.text[:200])
# Karışık liste: tek bir yabancı kimlik bile tüm isteği düşürür.
mixed = client.post("/api/products/labels.pdf", headers=other_headers,
                    json={"product_ids": [first["id"], second["id"]]})
assert mixed.status_code == 404, (mixed.status_code, mixed.text[:200])

client.close()
print("LABEL_ENDPOINT_SCENARIO_OK")
'''

    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "LABEL_ENDPOINT_SCENARIO_OK" in result.stdout, result.stdout
