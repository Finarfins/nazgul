"""Müstahsil makbuzu + kantar fişi — TEK okuma yolu (F10-1c).

`routers/mustahsil.py` (personel uçları) ve
`whatsapp/ciftci_yurutucu.py::ciftci_kantar` / `ciftci_makbuz` (çiftçi
araçları) AYNI satırları buradan okur. `avans_servis.py`nin gerekçesi
AYNEN: iki yüzeyin ayrı SQL kopyası taşıması, biri değiştiğinde ötekini
SESSİZCE eski hâlinde bırakırdı ve aynı çiftçi iki yüzeyde farklı rakam
görürdü (F10-1b düzeltme 1'in dersi). Eşitlik bir kapıyla ölçülüyor:
`test_KANTAR_CEVABI_UCUN_KENDI_MAKBUZU_VE_FIS_NETI_ILE_AYNI`.

--- FİŞİN NETİ KOPYALANMAZ, İTHAL EDİLİR -----------------------------------

`fis_ozeti` neti `routers.farm._turetilmis_net` ile TÜRETİR (bileşim kuralı
TEK yerde: toplamsal, yuvarlama en sonda). Router'ın `_fis_neti`si ve
çiftçinin kantar aracı İKİSİ DE bu fonksiyondan geçer — yani makbuza
yazılan fiş neti ile çiftçiye söylenen fiş neti AYNI çağrının sonucudur.
`routers.farm` içe aktarılıyor çünkü formül orada yaşıyor ve
`field_stok_tuketici.py` onu zaten aynı yoldan ithal ediyor.

Bu modül `fastapi`yi DOĞRUDAN içe aktarmaz; `routers.farm` üzerinden
dolaylı olarak çeker. Bu yüzden `app/whatsapp/` onu İŞLEV GÖVDESİNDE içe
aktarır (`ciftci_ekstre`nin `statement` kuralı).

--- HATA ÇEVİRİSİ BURADA DEĞİL --------------------------------------------

Bulunamayan satır `None` döner; 404'e çevirmek router'ın işidir
(`_makbuz_satiri`, `_fis_neti`). Çiftçi yolu ise `None`ı NÖTR "kayıt
bulunmuyor" metnine çevirir — başka bir firmanın ya da başka bir çiftçinin
satırının VAR OLDUĞUNU söyleyen hiçbir ayrım yoktur.

--- DİNAMİK SQL: ÜÇ ÇAĞRI, ROUTER'DAN TAŞINDI ------------------------------

`makbuz_satiri`, `makbuz_kalemleri` ve `makbuz_listesi`nin metinleri
router'dan BİREBİR taşındı (dinamik `text()` sayısı router'da 3 → 0, burada
0 → 3). Kiracı yüklemi (`company_id=:cid`) üçünde de LİTERAL durur;
interpolasyon yalnız modül düzeyindeki sütun listeleri ve sabit süzgeç
parçalarıdır, süzgeç DEĞERLERİ hep bağlı parametredir. Liste süzgecine
eklenen iki dal (`receipt_no`, `yalniz_fisli`) aynı sınıftandır ve
yalnız çiftçi aracı tarafından kullanılır.

Fiş okumaları SABİT metindir.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Bileşim kuralı ve oran toplamı İTHAL EDİLİR (başlık).
from .routers.farm import _kesinti_orani_toplami, _turetilmis_net

MAKBUZ_SUTUNLARI = (
    "id,supplier_id,purchase_id,ticket_id,receipt_no,issued_at,gross_amount,"
    "withholding_total,social_security_total,net_payable,advance_applied_total,"
    "status,note"
)
KALEM_SUTUNLARI = (
    "id,product_id,description,entered_quantity,entered_unit,entered_factor,"
    "base_quantity,ticket_net_snapshot,unit_price,line_gross,withholding_rate,"
    "withholding_amount,social_security_rate,social_security_amount,line_net"
)

_FIS_SQL = text(
    "SELECT id,ticket_no,weighed_at,gross_entered_quantity,entered_unit"
    " FROM field_harvest_tickets WHERE company_id=:cid AND id=:tid"
)
_KESINTI_SQL = text(
    "SELECT rate_percent FROM field_harvest_ticket_deductions"
    " WHERE company_id=:cid AND ticket_id=:tid"
)


def makbuz_satiri(db: Session, cid: int, makbuz_id: int) -> dict[str, Any] | None:
    """Makbuz başlığı, KİRACI YÜKLEMİYLE; yoksa ``None``."""
    satir = db.execute(
        text(
            f"SELECT {MAKBUZ_SUTUNLARI} FROM producer_receipts "
            "WHERE company_id=:cid AND id=:rid"
        ),
        {"cid": cid, "rid": makbuz_id},
    ).mappings().first()
    return None if satir is None else dict(satir)


def makbuz_kalemleri(db: Session, cid: int, makbuz_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                f"SELECT {KALEM_SUTUNLARI} FROM producer_receipt_items "
                "WHERE company_id=:cid AND receipt_id=:rid ORDER BY id"
            ),
            {"cid": cid, "rid": makbuz_id},
        ).mappings().all()
    ]


def makbuz_listesi(
    db: Session,
    cid: int,
    *,
    supplier_id: int | None = None,
    status: str | None = None,
    baslangic: datetime | None = None,
    bitis: datetime | None = None,
    receipt_no: str | None = None,
    yalniz_fisli: bool = False,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Makbuz başlıkları, ``id`` AZALAN sırayla (en yeni önce).

    ``receipt_no`` BÜYÜK/küçük harf DUYARSIZ karşılaştırılır:
    `document_engine.next_document_no` tekilliği `LOWER(...)` ile denetliyor,
    yani iki numara yalnız harf büyüklüğüyle AYRIŞAMAZ.

    ``yalniz_fisli`` bir BOOL'dur; metne değeri değil hangi SABİT parçanın
    ekleneceği girer.
    """
    sql = f"SELECT {MAKBUZ_SUTUNLARI} FROM producer_receipts WHERE company_id=:cid"
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if supplier_id is not None:
        sql += " AND supplier_id=:sid"
        params["sid"] = supplier_id
    if status is not None:
        sql += " AND status=:status"
        params["status"] = status
    if baslangic is not None:
        sql += " AND issued_at IS NOT NULL AND issued_at>=:df"
        params["df"] = baslangic
    if bitis is not None:
        sql += " AND issued_at IS NOT NULL AND issued_at<=:dt"
        params["dt"] = bitis
    if receipt_no is not None:
        sql += " AND LOWER(receipt_no)=LOWER(:rno)"
        params["rno"] = receipt_no
    if yalniz_fisli:
        sql += " AND ticket_id IS NOT NULL"
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def fis_ozeti(db: Session, cid: int, ticket_id: int) -> dict[str, Any] | None:
    """Kantar fişinin başlığı + TÜRETİLEN neti; yoksa ``None``.

    Net fişin KENDİ kağıt netinden (`ticket_net_quantity`) DEĞİL, brüt ve
    kesinti oranlarından türetilir (router'ın `_fis_neti` sözleşmesi).
    `buyer_name` ve `notes` SEÇİLMEZ: bu fonksiyonun hiçbir çağıranı onlara
    ihtiyaç duymuyor ve çiftçi yolunda kırpılmaları gerekiyor (keşif §4c).
    """
    satir = db.execute(_FIS_SQL, {"cid": cid, "tid": ticket_id}).mappings().first()
    if satir is None:
        return None
    kesintiler = [
        dict(r)
        for r in db.execute(
            _KESINTI_SQL, {"cid": cid, "tid": ticket_id}
        ).mappings().all()
    ]
    brut = Decimal(str(satir["gross_entered_quantity"]))
    return {
        "id": satir["id"],
        "ticket_no": satir["ticket_no"],
        "weighed_at": satir["weighed_at"],
        "entered_unit": satir["entered_unit"],
        "brut": brut,
        "kesinti_orani": _kesinti_orani_toplami(kesintiler),
        "net": _turetilmis_net(brut, kesintiler),
    }


def fis_neti(db: Session, cid: int, ticket_id: int) -> Decimal | None:
    """Fişin TÜRETİLEN neti; fiş yoksa ``None``. `fis_ozeti`nin netidir."""
    ozet = fis_ozeti(db, cid, ticket_id)
    return None if ozet is None else ozet["net"]


__all__ = [
    "KALEM_SUTUNLARI",
    "MAKBUZ_SUTUNLARI",
    "fis_neti",
    "fis_ozeti",
    "makbuz_kalemleri",
    "makbuz_listesi",
    "makbuz_satiri",
]
