"""F10-4a — PARTİ İZİ: "bu partiyi kim aldı?" sorusunun SQL tarafı. YAZMAZ.

Keşif: `docs/f10-4-lot-geri-cagirma-kesif-2026-09-27.md` §1–§3, §5.1. Bu modül
yalnız OKUR; birleştirme (iade düşme, perakende ayrımı, müşteri toplamı)
`app/lot_izi_ozet.py`dedir ve o modül SQL görmez. Uç
`routers/lots.py` (`GET /api/lots/{lot_id}/recall-preview`).

--- ÜÇ HOP -------------------------------------------------------------------

  HOP 1  kök parti -> KARDEŞ satırlar: `(company_id, product_id, lot_code)`
         eşitliği. Transfer hedef depoda AYRI bir parti satırı açar
         (tekillik depoyu içerir); tek `id` üzerinden yürüyen bir iz,
         transferden SONRA öteki depodan yapılan satışı KAÇIRIR (§1.1).
  HOP 2  kardeşler -> `stock_movements`, belge (`reference_type`,
         `reference_id`) + hareket türü başına net miktar.
  HOP 3  belge başlıkları (`orders`, `delivery_notes`, `returns`) -> cari.

Ayrıca PARTİSİZ hareketler (aynı ürün, aynı depolar, partinin ilk
hareketinden sonra, `lot_id` NULL) okunur: rapor "izlenemeyen N birim var"
diyebilmeli (§8 risk 1). Kaynaksız satış iadesi (G2) ve tarla faaliyeti
girdisi (G5) oradan `gaps` olur.

--- BİÇİM: TAKMA ADSIZ, DÜZ `select`LER (keşif §2.3 biçim 2) -------------------

Her sorgu TEK tabloya dokunur ve ilk yüklemi `company_id == cid`dir. Takma ad,
yerel yeniden bağlama ya da `union_all` YOK: biçim (1) Core kapısının
`test_no_unsupported_expressions` / `test_no_unresolved_targets` iddialarını
kırıyordu (ölçüldü). JOIN de yok — her hop bir öncekinin kimlik kümesini
`IN` ile taşır.

--- PARTİ TABLOSU NEDEN BURADA BİLDİRİLİYOR ---------------------------------

Tablo Core'da hiç bildirilmemişti (bütün erişim `parti_defteri.py`nin ham
`text()`i). `core_schema.metadata`ya eklemek 1B-C'de `stock_movements.lot_id`
için ölçülen tuzağı doğururdu: `20260712_0000`ın `create_all`ı taze
veritabanında tabloyu açar ve 0067'nin koşullu DDL'i atlanır. Bu yüzden
bildirim MODÜL-YEREL bir `MetaData`dadır; hiçbir `create_all` onu görmez ve
yalnız OKUNAN sütunları taşır. Defterin TEK YAZICISI hâlâ
`app/parti_defteri.py`dir (`tests/test_1b_a_alis_lot.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table, func, select
from sqlalchemy.orm import Session

from .core_schema import customers, orders, products, returns, stock_movements
from .inventory import warehouses
from .workflow import delivery_notes

#: MODÜL-YEREL şema. `core_schema.metadata` DEĞİL — gerekçe modül başlığında.
_yerel_sema = MetaData()

product_lots = Table(
    "product_lots",
    _yerel_sema,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, nullable=False),
    Column("product_id", Integer, nullable=False),
    Column("warehouse_id", Integer, nullable=False),
    Column("lot_code", String),
    # SQLite `str`, PostgreSQL `date` döndürür; `lot_izi_ozet.tarih_metni`
    # ikisini de ISO metne indirir (`parti_defteri._skt` dersinin aynısı).
    Column("expiry_date", String),
    Column("quantity", Numeric(18, 4)),
)


@dataclass(frozen=True)
class PartiIzi:
    """HOP 1–3'ün HAM çıktısı. Birleştirme `lot_izi_ozet.ozetle`dedir."""

    kok: dict[str, Any]
    urun: dict[str, Any]
    kardesler: list[dict[str, Any]]
    depolar: dict[int, str]
    hareketler: list[dict[str, Any]]
    partisiz: list[dict[str, Any]]
    siparisler: dict[int, dict[str, Any]]
    irsaliyeler: dict[int, dict[str, Any]]
    iadeler: dict[int, dict[str, Any]]
    cariler: dict[int, dict[str, Any]]


def _kimlikler(satirlar: list[dict[str, Any]], tur: str) -> list[int]:
    return sorted(
        {
            int(s["reference_id"])
            for s in satirlar
            if s["reference_type"] == tur and s["reference_id"] is not None
        }
    )


def parti_izi_oku(db: Session, cid: int, lot_id: int) -> PartiIzi | None:
    """`lot_id`nin partisini üç hopta okur. Başka firmanın kimliği -> `None`.

    `None` çağırana 404 demektir; kök sorgunun İLK yüklemi `company_id`dir,
    yani komşu firmanın parti kimliği VARLIĞINI da sızdırmaz.
    """
    kok = db.execute(
        select(
            product_lots.c.id,
            product_lots.c.product_id,
            product_lots.c.lot_code,
            product_lots.c.expiry_date,
        ).where(product_lots.c.company_id == cid, product_lots.c.id == lot_id)
    ).mappings().first()
    if kok is None:
        return None
    urun_id = int(kok["product_id"])

    urun = db.execute(
        select(products.c.id, products.c.name, products.c.unit).where(
            products.c.company_id == cid, products.c.id == urun_id
        )
    ).mappings().first()

    # HOP 1 — kardeş satırlar (kök DAHİL). Tükenen parti silinmez
    # (`quantity=0`), yani sıfır satır da burada görünür.
    kardesler = [
        dict(s)
        for s in db.execute(
            select(
                product_lots.c.id,
                product_lots.c.warehouse_id,
                product_lots.c.lot_code,
                product_lots.c.expiry_date,
                product_lots.c.quantity,
            )
            .where(
                product_lots.c.company_id == cid,
                product_lots.c.product_id == urun_id,
                product_lots.c.lot_code == kok["lot_code"],
            )
            .order_by(product_lots.c.warehouse_id, product_lots.c.id)
        ).mappings()
    ]
    kardes_ids = [int(s["id"]) for s in kardesler]
    depo_ids = sorted({int(s["warehouse_id"]) for s in kardesler})

    depolar = {
        int(s["id"]): s["name"]
        for s in db.execute(
            select(warehouses.c.id, warehouses.c.name).where(
                warehouses.c.company_id == cid, warehouses.c.id.in_(depo_ids)
            )
        ).mappings()
    }

    # HOP 2 — partili hareketler, belge + tür + depo + kardeş başına net.
    hareketler = [
        dict(s)
        for s in db.execute(
            select(
                stock_movements.c.reference_type,
                stock_movements.c.reference_id,
                stock_movements.c.movement_type,
                stock_movements.c.warehouse_id,
                stock_movements.c.lot_id,
                func.sum(stock_movements.c.quantity).label("quantity"),
                func.min(stock_movements.c.movement_date).label("movement_date"),
                func.min(stock_movements.c.id).label("first_id"),
            )
            .where(
                stock_movements.c.company_id == cid,
                stock_movements.c.lot_id.in_(kardes_ids),
            )
            .group_by(
                stock_movements.c.reference_type,
                stock_movements.c.reference_id,
                stock_movements.c.movement_type,
                stock_movements.c.warehouse_id,
                stock_movements.c.lot_id,
            )
            .order_by(func.min(stock_movements.c.id))
        ).mappings()
    ]

    # PARTİSİZ hareketler: aynı ürün, kardeşlerin depoları, partinin İLK
    # hareketinden SONRA. Önceki partisiz stok (parti açılmadan önceki
    # açılış/alış) bu partiyle İLGİSİZDİR ve boşluk sayılmaz.
    partisiz: list[dict[str, Any]] = []
    if hareketler:
        ilk_id = min(int(h["first_id"]) for h in hareketler)
        partisiz = [
            dict(s)
            for s in db.execute(
                select(
                    stock_movements.c.reference_type,
                    stock_movements.c.movement_type,
                    func.count(stock_movements.c.id).label("movement_count"),
                    func.sum(stock_movements.c.quantity).label("quantity"),
                )
                .where(
                    stock_movements.c.company_id == cid,
                    stock_movements.c.product_id == urun_id,
                    stock_movements.c.warehouse_id.in_(depo_ids),
                    stock_movements.c.lot_id.is_(None),
                    stock_movements.c.id > ilk_id,
                )
                .group_by(
                    stock_movements.c.reference_type,
                    stock_movements.c.movement_type,
                )
                .order_by(
                    stock_movements.c.reference_type,
                    stock_movements.c.movement_type,
                )
            ).mappings()
        ]

    # HOP 3 — belge başlıkları.
    irsaliye_ids = _kimlikler(hareketler, "delivery_notes")
    iade_ids = _kimlikler(hareketler, "returns")

    iadeler = {
        int(s["id"]): dict(s)
        for s in db.execute(
            select(
                returns.c.id,
                returns.c.return_type,
                returns.c.entity_id,
                returns.c.return_date,
                returns.c.document_no,
                returns.c.source_type,
                returns.c.source_id,
            ).where(returns.c.company_id == cid, returns.c.id.in_(iade_ids))
        ).mappings()
    }
    # Satışa bağlı iadenin KAYNAK siparişi de okunur: iade alıcının satırından
    # düşülür ve o satır sipariş başlığından kurulur.
    siparis_ids = sorted(
        set(_kimlikler(hareketler, "orders"))
        | {
            int(i["source_id"])
            for i in iadeler.values()
            if i["source_type"] == "order" and i["source_id"] is not None
        }
    )
    siparisler = {
        int(s["id"]): dict(s)
        for s in db.execute(
            select(
                orders.c.id,
                orders.c.customer_id,
                orders.c.order_date,
                orders.c.document_no,
            ).where(orders.c.company_id == cid, orders.c.id.in_(siparis_ids))
        ).mappings()
    }
    irsaliyeler = {
        int(s["id"]): dict(s)
        for s in db.execute(
            select(
                delivery_notes.c.id,
                delivery_notes.c.customer_id,
                delivery_notes.c.delivery_date,
                delivery_notes.c.document_no,
            ).where(
                delivery_notes.c.company_id == cid,
                delivery_notes.c.id.in_(irsaliye_ids),
            )
        ).mappings()
    }

    cari_ids = sorted(
        {int(s["customer_id"]) for s in siparisler.values()}
        | {int(s["customer_id"]) for s in irsaliyeler.values()}
    )
    cariler = {
        int(s["id"]): dict(s)
        for s in db.execute(
            select(customers.c.id, customers.c.name, customers.c.phone).where(
                customers.c.company_id == cid, customers.c.id.in_(cari_ids)
            )
        ).mappings()
    }

    return PartiIzi(
        kok=dict(kok),
        urun=dict(urun) if urun is not None else {"id": urun_id, "name": None, "unit": None},
        kardesler=kardesler,
        depolar=depolar,
        hareketler=hareketler,
        partisiz=partisiz,
        siparisler=siparisler,
        irsaliyeler=irsaliyeler,
        iadeler=iadeler,
        cariler=cariler,
    )
