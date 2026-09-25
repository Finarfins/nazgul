"""F9-5-fix (K8) servis faturası KDV senaryosu — SQLite testi ve PG ikizi ORTAK.

Senaryo YALNIZ HTTP uçlarından geçer (SQL yok): iş emri → parça → tamamla →
`POST /api/invoices/generate`. İki dosya da aynı faturayı keser; SQLite testi
bunu geçici bir veritabanında alt süreçte, PG ikizi gerçek PostgreSQL'de
süreç içinde yapar.

Kurgu `test_v3_invoice_global_discount.py`nin kurgusudur (2 saat × 150 işçilik,
P1 2 × 100 %10 iskonto %20 KDV, P2 3 × 7 %10 KDV, %30 kısmi garanti), çünkü
o dosyanın rakamları zaten elle doğrulanmış durumda.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

#: Faturanın PARA görünümü: kimlik, zaman, numara ve e-belge alanları (ETTN
#: fatura kimliğinden, tarih kesim anından türer) karşılaştırmadan çıkar.
KALEM_ALANLARI = (
    "item_type", "description", "quantity", "unit_price", "original_price",
    "discount_type", "discount_value", "discount_amount", "tax_rate",
    "tax_exempt", "tax_amount", "total", "warranty_percent",
    "customer_payable", "company_payable",
)
BASLIK_ALANLARI = ("status", "invoice_type", "currency", "exchange_rate", "totals", "tax", "warranty")

KURUS = Decimal("0.01")


def D(value: Any) -> Decimal:
    return Decimal(str(value))


def kurus(value: Decimal) -> Decimal:
    from app.money import money

    return money(value)


def kanonik(value: Any) -> Any:
    """Sayıyı LEHÇEDEN BAĞIMSIZ metne çevir.

    Aynı `invoice_items` satırı SQLite'ta `float`/`int` (64.8, 0), PG'de
    `Decimal` (64.80, False) döner; ikiz aynı altın görünümü kullanabilsin
    diye sayı normalleştirilmiş ondalık metne, `bool` tamsayıya iner.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, Decimal)):
        return format(D(value).normalize(), "f")
    return value


def para_gorunumu(fatura: dict) -> dict:
    """Karşılaştırılabilir, sıralı, kimliksiz görünüm (JSON'a dökülebilir)."""
    baslik = {k: fatura[k] for k in BASLIK_ALANLARI}
    baslik["exchange_rate"] = kanonik(baslik["exchange_rate"])
    return {
        "baslik": baslik,
        "kalemler": [{k: kanonik(kalem[k]) for k in KALEM_ALANLARI} for kalem in fatura["items"]],
    }


def oturum(c, parola: str) -> dict:
    login = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(login["companies"][0]["id"])}
    yeni = c.post(
        "/api/auth/change-password", headers=h,
        json={"current_password": "admin123", "new_password": parola},
    ).json()
    h["Authorization"] = "Bearer " + yeni["access_token"]
    h["_uid"] = str(login["user"]["id"])
    return h


def dunya(c, h: dict, ek: str = "") -> dict:
    basliklar = {k: v for k, v in h.items() if not k.startswith("_")}
    musteri = c.post("/api/customers", headers=basliklar, json={"name": "K8 Müşteri"}).json()
    makine = c.post("/api/machines", headers=basliklar, json={
        "customer_id": musteri["id"], "brand": "S", "model": "K8", "serial_number": f"K8-M-{ek}",
    }).json()
    depo = c.get("/api/warehouses", headers=basliklar).json()[0]
    p1 = c.post("/api/products", headers=basliklar, json={
        "name": "K8 P1", "purchase_price": "1", "sale_price": "100", "vat_rate": "20", "stock": "50", "unit": "Adet",
    }).json()
    p2 = c.post("/api/products", headers=basliklar, json={
        "name": "K8 P2", "purchase_price": "1", "sale_price": "7", "vat_rate": "10", "stock": "50", "unit": "Adet",
    }).json()
    return {"h": basliklar, "uid": int(h["_uid"]), "musteri": musteri, "makine": makine, "depo": depo, "p1": p1, "p2": p2}


def tamamlanmis_is_emri(c, w: dict, garanti: str = "PARTIAL", yuzde: str = "30") -> int:
    h = w["h"]
    wo = c.post("/api/work-orders", headers=h, json={
        "machine_id": w["makine"]["id"], "customer_id": w["musteri"]["id"], "technician_id": w["uid"],
        "actual_hours": "2", "labor_rate": "150", "warranty_type": garanti, "warranty_percent": yuzde,
    }).json()
    for urun, miktar, fiyat, iskonto, kdv in ((w["p1"], "2", "100", "10", "20"), (w["p2"], "3", "7", "0", "10")):
        r = c.post(f"/api/work-orders/{wo['id']}/parts", headers=h, json={
            "product_id": urun["id"], "warehouse_id": w["depo"]["id"], "quantity": miktar,
            "unit_price": fiyat, "discount": iskonto, "tax_rate": kdv,
        })
        assert r.status_code in (200, 201), r.text
    for durum in ("IN_PROGRESS", "COMPLETED"):
        r = c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={"status": durum})
        assert r.status_code == 200, r.text
    return int(wo["id"])


def fatura_kes(c, w: dict, is_emri: int, **iskonto) -> dict:
    r = c.post("/api/invoices/generate", headers=w["h"], json={"work_order_id": is_emri, **iskonto})
    assert r.status_code == 201, r.text
    return r.json()


def kalem_matrahi(kalem: dict) -> Decimal:
    """Satırın KDV matrahı = ham tutar − satır iskontosu (belge payı dahil)."""
    ham = kurus(D(kalem["quantity"]) * D(kalem["unit_price"]))
    return kurus(ham - D(kalem["discount_amount"]))


def kdv_ozdesligi(fatura: dict) -> None:
    """H80 özdeşlikleri — her satır ve başlık için, kuruşuna kadar."""
    kalemler = fatura["items"]
    totals = fatura["totals"]
    for kalem in kalemler:
        matrah = kalem_matrahi(kalem)
        # Satır özdeşliği: total == ham − discount_amount + tax_amount.
        assert D(kalem["total"]) == matrah + D(kalem["tax_amount"]), ("satır özdeşliği", kalem)
        # KDV İSKONTOLU MATRAHTAN: tax_amount == round(oran × matrah).
        assert D(kalem["tax_amount"]) == kurus(matrah * D(kalem["tax_rate"]) / 100), ("KDV matrahı", kalem)
    assert sum((D(k["total"]) for k in kalemler), Decimal("0")) == D(totals["grand_total"])
    assert sum((D(k["tax_amount"]) for k in kalemler), Decimal("0")) == D(totals["tax"])
    assert sum((D(k["customer_payable"]) for k in kalemler), Decimal("0")) == D(totals["customer_amount"])
    assert sum((D(k["company_payable"]) for k in kalemler), Decimal("0")) == D(totals["warranty_amount"])


#: TABAN (develop 09219db, bu dilimden ÖNCE) iskontosuz faturanın para
#: görünümü — aynı senaryo değişmemiş kodla koşturulup yakalandı.
TABAN_ISKONTOSUZ = {
    "baslik": {
        "currency": "TRY", "exchange_rate": "1", "invoice_type": "INVOICE", "status": "ISSUED",
        "tax": {"parts_before_tax": "201.00", "parts_discount": "20.00", "parts_tax": "38.10", "parts_total": "239.10"},
        "totals": {
            "customer_amount": "377.37", "discount": "20.00", "global_discount": "0.00", "grand_total": "539.10",
            "labor": "300.00", "labor_line_ids": [], "labor_source": "header", "parts": "239.10", "tax": "38.10",
            "warranty_amount": "161.73",
        },
        "warranty": {
            "company_cost": "161.73", "coverage_percent": "30.0000", "customer_amount": "377.37",
            "type": "PARTIAL", "warranty_amount": "161.73",
        },
    },
    "kalemler": [
        {"company_payable": "90", "customer_payable": "210", "description": "Servis İşçiliği", "discount_amount": "0",
         "discount_type": "PERCENT", "discount_value": "0", "item_type": "LABOR", "original_price": "150",
         "quantity": "2", "tax_amount": "0", "tax_exempt": "0", "tax_rate": "0", "total": "300", "unit_price": "150",
         "warranty_percent": "30"},
        {"company_payable": "64.8", "customer_payable": "151.2", "description": "K8 P1", "discount_amount": "20",
         "discount_type": "PERCENT", "discount_value": "10", "item_type": "PART", "original_price": "100",
         "quantity": "2", "tax_amount": "36", "tax_exempt": "0", "tax_rate": "20", "total": "216", "unit_price": "100",
         "warranty_percent": "30"},
        {"company_payable": "6.93", "customer_payable": "16.17", "description": "K8 P2", "discount_amount": "0",
         "discount_type": "PERCENT", "discount_value": "0", "item_type": "PART", "original_price": "7",
         "quantity": "3", "tax_amount": "2.1", "tax_exempt": "0", "tax_rate": "10", "total": "23.1", "unit_price": "7",
         "warranty_percent": "30"},
    ],
}

#: İskontosuz yolda DEĞİŞMESİNE İZİN VERİLEN yollar — hepsi işçilik KDV'sinin
#: (H79) kendisi ya da ondan türeyen başlık toplamı. Başka HER yol bayt-aynı.
IZINLI_DEGISIMLER = {
    ("kalemler", 0, "tax_rate"): ("0", "20"),
    ("kalemler", 0, "tax_amount"): ("0", "60"),
    ("kalemler", 0, "total"): ("300", "360"),
    ("kalemler", 0, "customer_payable"): ("210", "252"),
    ("kalemler", 0, "company_payable"): ("90", "108"),
    ("baslik", "totals", "tax"): ("38.10", "98.10"),
    ("baslik", "totals", "grand_total"): ("539.10", "599.10"),
    ("baslik", "totals", "customer_amount"): ("377.37", "419.37"),
    ("baslik", "totals", "warranty_amount"): ("161.73", "179.73"),
}


def duzlestir(deger, yol=()):
    """İç içe görünümü `(yol, yaprak)` çiftlerine aç (kalem listesi indeksle)."""
    if isinstance(deger, dict):
        for k, v in deger.items():
            yield from duzlestir(v, yol + (k,))
    elif isinstance(deger, list) and deger and isinstance(deger[0], dict):
        for i, v in enumerate(deger):
            yield from duzlestir(v, yol + (i,))
    else:
        yield yol, deger
