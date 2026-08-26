"""Cari kartındaki belge geçmişinin TAMAMINA erişim.

Kart listesi bilinçli olarak kısa bir önizlemedir (``_PREVIEW_LIMIT``). Tek
kaynak o olduğu sürece eski geçmiş uygulamada hiçbir yerde görünmüyordu:
gerçek kurulumda 30 satışı olan bir caride yalnız en yeni 8'i (hepsi 2026)
listeleniyor, 2024–2025 belgeleri kullanıcı için yok hükmünde kalıyordu.
``entity_documents`` o geçmişi sayfalayarak verir.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import entity_detail as entity_detail_module
from app.core_schema import customers, metadata, orders, suppliers, purchases


def _request_with_company(cid: int):
    return SimpleNamespace(state=SimpleNamespace(company_id=cid))


def _seed(yillar: dict[str, int]) -> tuple[Session, int]:
    """Verilen yıllara dağılmış satışları olan bir cari kurar.

    Satırlar KASITLI olarak eskiden yeniye eklenir: böylece id sırası tarih
    sırasıyla örtüşür ve testin doğruladığı şey sıralama değil, KAPSAM olur.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine) as db:
        result = db.execute(
            customers.insert().values(
                name="Geçmişi Uzun Cari", opening_balance=Decimal("0"), company_id=1
            )
        )
        customer_id = int(result.inserted_primary_key[0])
        satirlar = []
        for yil, adet in sorted(yillar.items()):
            for sira in range(adet):
                satirlar.append(
                    {
                        "company_id": 1,
                        "customer_id": customer_id,
                        "order_date": f"{yil}-{(sira % 12) + 1:02d}-15",
                        "final_total": Decimal("100.00"),
                        "status": "completed",
                        "document_no": f"S-{yil}-{sira:02d}",
                        "note": None,
                    }
                )
        db.execute(orders.insert(), satirlar)
        db.commit()
    return Session(engine), customer_id


def test_paging_reaches_every_year_the_preview_hides():
    # Gerçek olayın birebir şekli: 2026'da 8, öncesinde çok daha fazlası.
    db, customer_id = _seed({"2024": 13, "2025": 9, "2026": 8})
    try:
        ilk = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db, offset=0, limit=10
        )
        assert ilk["total"] == 30
        assert len(ilk["items"]) == 10
        assert ilk["has_more"] is True

        toplanan = list(ilk["items"])
        offset = len(toplanan)
        while True:
            sayfa = entity_detail_module.entity_documents(
                "customer", customer_id, _request_with_company(1), db,
                offset=offset, limit=10,
            )
            toplanan.extend(sayfa["items"])
            offset += len(sayfa["items"])
            if not sayfa["has_more"]:
                break

        assert len(toplanan) == 30
        yillar = {row["transaction_date"][:4] for row in toplanan}
        # Asıl iddia: önizlemenin GİZLEDİĞİ yıllar da erişilebilir olmalı.
        assert yillar == {"2024", "2025", "2026"}
        # Mükerrer sayfa/atlanan satır olmamalı.
        assert len({row["id"] for row in toplanan}) == 30
    finally:
        db.close()


def test_preview_alone_would_miss_the_older_years():
    """Önizlemenin tek başına yetmediğini kilitler.

    Bu test kırmızıya dönerse ``_PREVIEW_LIMIT`` büyümüş demektir; o zaman
    sayfalamanın gerekçesi de değişmiştir ve yeniden düşünülmelidir.
    """
    db, customer_id = _seed({"2024": 13, "2025": 9, "2026": 8})
    try:
        onizleme = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db,
            offset=0, limit=entity_detail_module._PREVIEW_LIMIT,
        )
        yillar = {row["transaction_date"][:4] for row in onizleme["items"]}
        assert yillar == {"2026"}
        assert onizleme["has_more"] is True
    finally:
        db.close()


def test_ordering_matches_the_card_preview():
    """Sekme ile önizleme AYNI sırayı kullanmalı.

    Ayrışırsa önizlemenin ilk satırı ile sekmenin ilk satırı farklı belge olur
    ve liste kullanıcıya "atlıyor" gibi görünür.
    """
    db, customer_id = _seed({"2024": 3, "2025": 3, "2026": 3})
    try:
        sayfa = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db, offset=0, limit=50
        )
        tarihler = [row["transaction_date"] for row in sayfa["items"]]
        assert tarihler == sorted(tarihler, reverse=True)
    finally:
        db.close()


def test_other_company_cannot_read_documents():
    db, customer_id = _seed({"2026": 3})
    try:
        with pytest.raises(Exception) as hata:
            entity_detail_module.entity_documents(
                "customer", customer_id, _request_with_company(2), db
            )
        # Başka firmanın carisi "yok" görünmeli; belge sayısı bile sızmamalı.
        assert getattr(hata.value, "status_code", None) == 404
    finally:
        db.close()


def test_unknown_customer_is_404():
    db, _ = _seed({"2026": 1})
    try:
        with pytest.raises(Exception) as hata:
            entity_detail_module.entity_documents(
                "customer", 999_999, _request_with_company(1), db
            )
        assert getattr(hata.value, "status_code", None) == 404
    finally:
        db.close()


def test_limit_is_clamped_to_the_page_maximum():
    db, customer_id = _seed({"2026": 3})
    try:
        sayfa = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db,
            offset=0, limit=10_000,
        )
        # İstemcinin verdiği sınır tek başına sorgu boyutunu belirleyemez.
        assert sayfa["limit"] == entity_detail_module._DOCUMENT_PAGE_MAX
    finally:
        db.close()


def test_year_summary_covers_every_year_and_matches_totals():
    db, customer_id = _seed({"2024": 13, "2025": 9, "2026": 8})
    try:
        sayfa = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db, offset=0, limit=1
        )
        ozet = {y["year"]: y for y in sayfa["years"]}
        assert list(ozet) == ["2026", "2025", "2024"], sayfa["years"]
        assert [ozet[y]["count"] for y in ("2026", "2025", "2024")] == [8, 9, 13]
        # Her satır 100.00 TL; yıl toplamı adet × 100 olmalı.
        assert ozet["2024"]["total"] == Decimal("1300.00")
        # Özet TÜM geçmişi kapsar; sayfa boyutu (limit=1) onu daraltmaz.
        assert len(sayfa["items"]) == 1
    finally:
        db.close()


def test_year_filter_returns_only_that_year():
    db, customer_id = _seed({"2024": 13, "2025": 9, "2026": 8})
    try:
        sayfa = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db,
            offset=0, limit=100, year="2025",
        )
        assert sayfa["year"] == "2025"
        # ``total`` süzgeçli sayıdır: "Daha fazla" düğmesi 30 değil 9 üzerinden
        # karar vermeli, yoksa açılan yılda olmayan sayfa istenir.
        assert sayfa["total"] == 9
        assert len(sayfa["items"]) == 9
        assert {row["transaction_date"][:4] for row in sayfa["items"]} == {"2025"}
        assert sayfa["has_more"] is False
        # Yıl başlıkları süzgeçten ETKİLENMEZ; aksi hâlde bir yıl açılınca
        # diğer yılların başlıkları listeden kaybolurdu.
        assert {y["year"] for y in sayfa["years"]} == {"2024", "2025", "2026"}
    finally:
        db.close()


def test_year_filter_paginates_inside_the_year():
    db, customer_id = _seed({"2024": 13, "2025": 9, "2026": 8})
    try:
        ilk = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db,
            offset=0, limit=5, year="2024",
        )
        assert ilk["total"] == 13 and len(ilk["items"]) == 5 and ilk["has_more"] is True
        ikinci = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db,
            offset=5, limit=50, year="2024",
        )
        assert len(ikinci["items"]) == 8 and ikinci["has_more"] is False
        hepsi = [r["id"] for r in ilk["items"]] + [r["id"] for r in ikinci["items"]]
        assert len(set(hepsi)) == 13
    finally:
        db.close()


def test_unknown_year_is_empty_but_keeps_the_headings():
    db, customer_id = _seed({"2026": 3})
    try:
        sayfa = entity_detail_module.entity_documents(
            "customer", customer_id, _request_with_company(1), db, year="1999"
        )
        assert sayfa["items"] == [] and sayfa["total"] == 0
        assert [y["year"] for y in sayfa["years"]] == ["2026"]
    finally:
        db.close()


def test_supplier_side_uses_the_same_projection():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine) as db:
        result = db.execute(
            suppliers.insert().values(
                name="Tedarikçi", opening_balance=Decimal("0"), company_id=1
            )
        )
        supplier_id = int(result.inserted_primary_key[0])
        db.execute(
            purchases.insert(),
            [
                {
                    "company_id": 1,
                    "supplier_id": supplier_id,
                    "purchase_date": f"202{yil}-05-05",
                    "final_total": Decimal("50.00"),
                    "status": "completed",
                    "document_no": f"A-{yil}",
                }
                for yil in (4, 5, 6)
            ],
        )
        db.commit()
    db = Session(engine)
    try:
        sayfa = entity_detail_module.entity_documents(
            "supplier", supplier_id, _request_with_company(1), db
        )
        assert sayfa["total"] == 3
        assert [row["document_no"] for row in sayfa["items"]] == ["A-6", "A-5", "A-4"]
    finally:
        db.close()
