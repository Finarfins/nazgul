"""E4b-1 e-IRSALIYE KISMI SEVK: `despatch_lines` defteri, 1:1 kuralinin kalkmasi.

Revision ID: 20260915_0087
Revises: 20260914_0086 (CS2 cek/senet cari)

Kaynak: `docs/e4b-kismi-sevk-kesif-2026-09-10.md` §1 (secenek a), §2 (miktar
kurallari), §6 (PR E4b-1). Sef kararlari: secenek (a); hizmet kalemleri
(`invoice_items.item_type = 'LABOR'`) sevk satiri OLAMAZ.

--- NE DEGISIYOR ---------------------------------------------------------

1. `uq_despatch_notes_company_invoice` DUSER. 0083 bu kurali "E4b'de TEK bir
   `drop_constraint` ile kalkar" diye yazmisti; kalkiyor. Bir faturaya artik
   N irsaliye acilabilir. `uq_despatch_notes_company_id` (bilesik FK hedefi)
   ve `uq_despatch_notes_uuid` (kiraci kapsamli ETTN) KALIR.
2. `despatch_lines` acilir: her irsaliye KENDI satirlarini tasir ve her satir
   bir fatura kalemine baglidir. "Bir fatura kalemi icin sevk edilen toplam
   faturalanan miktari asamaz" kurali uc katmanindadir (bir SUM kisiti
   semada ifade edilemez); hakemi faturanin satir kilidi
   (`app/routers/despatch_notes.py::_faturayi_kilitle`).

--- YABANCI ANAHTARLAR: IKISI DE BILESIK (0044 / 0062 KURALI) -------------

`(company_id, despatch_id) -> despatch_notes(company_id, id)`: hedef
`uq_despatch_notes_company_id` 0083'te tam da bunun icin kurulmustu.

`(company_id, invoice_item_id) -> invoice_items(company_id, id)`: hedef
`uq_invoice_items_company_id` BU GOCTE kurulur. OLCULDU: `invoice_items`
0012'de yalniz PK ve `ix_invoice_items_invoice` ile aciliyor ve sonraki
hicbir goc ona kisit eklemiyordu. Ilk yazimda FK CIPLAK birakilmisti
(gerekce: SQLite'ta tablonun yeniden insasi); Sef bunu REDDETTI
(2026-09-11) ve kalip 0085'in `finance_accounts` / 0071'in hedefidir:
varlik denetimli `batch_alter_table` ile UNIQUE eklenir, downgrade KOSULLU
dusurur. SQLite yeniden insasinin `ix_invoice_items_invoice`i ve
`invoice_id` FK'sinin `ON DELETE CASCADE`ini korudugu OLCULDU
(`tests/test_e4b1_kismi_sevk.py`). Boylece bir firmanin satiri BASKA
firmanin irsaliyesine de, BASKA firmanin fatura kalemine de asilamaz —
veritabani seviyesinde (PG ikizi ikisini de reddettirir).

`product_id` FK'SIZ (kesif §1: "urun karti varsa"). 5.1c siniflandiricisi
onu `DOGRUDAN_HEDEFLER`de `products`a esler.

--- GERI DOLDURMA --------------------------------------------------------

Var olan HER irsaliye, faturasinin LABOR OLMAYAN kalemlerinden TAM miktarla
satir alir (line_no 1..N, kalem kimligi sirasiyla). E4a'nin duz sevki
tanimi geregi faturanin TUM mallarini tasiyordu; geri doldurma o gercegi
yeni defterde yazar, boylece eski bir irsaliyenin XML sureti yeniden
uretildiginde satirsiz kalmaz ve kalan miktar hesabi eski sevki GORUR.
Miktari sifir ya da negatif kalem ATLANIR (CHECK `quantity > 0`); sayilari
`GERI_DOLDURMA_SAYACI`na yazilir ve testler olcer.

--- GERI ALINABILIR, AMA KOSULLU ----------------------------------------

`downgrade()` tabloyu dusurur ve 1:1 kisitini GERI KURAR. Bir faturanin
birden fazla irsaliyesi varsa kisit KURULAMAZ; o durumda goc SESSIZCE
kisitsiz birakmaz ya da satir SILMEZ, adi konmus bir hatayla DURUR —
operatorun karari (hangi irsaliye kalacak) bir gocun verebilecegi bir karar
degildir.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "20260915_0087"
down_revision = "20260914_0086"
branch_labels = None
depends_on = None

IRSALIYE = "despatch_notes"
SATIR = "despatch_lines"

FATURA_TEKIL = "uq_despatch_notes_company_invoice"
FK_IRSALIYE = "fk_despatch_lines_despatch_same_company"
FK_KALEM = "fk_despatch_lines_invoice_item_same_company"
KALEM = "invoice_items"
UQ_KALEM = "uq_invoice_items_company_id"
UQ_FIRMA_KIMLIK = "uq_despatch_lines_company_id"
UQ_SATIR_NO = "uq_despatch_lines_despatch_line"
CK_MIKTAR = "ck_despatch_lines_quantity_positive"
IX_FIRMA_IRSALIYE = "ix_despatch_lines_company_despatch"

#: Sevk satiri OLAMAYAN kalem turu (kesif §2, Sef karari).
HIZMET_TURU = "LABOR"

#: Son `upgrade()`in geri doldurma olcumu. Testler okur.
GERI_DOLDURMA_SAYACI: dict[str, int] = {}


def _unique_adlari(inspector, tablo: str) -> set[str]:
    return {u.get("name") for u in inspector.get_unique_constraints(tablo) if u.get("name")}


def _urun_kimligi(kaynak: str | None) -> int | None:
    """Kalemin donmus kaynagindan urun kimligi. Parca kalemleri
    `work_order_parts` satirini (`product_id` dahil) `source_snapshot`a yazar."""
    try:
        deger = json.loads(kaynak or "{}").get("product_id")
    except (ValueError, AttributeError):
        return None
    return int(deger) if isinstance(deger, int) and not isinstance(deger, bool) else None


#: HAFIF tablo tanimlari — yansitma (`autoload_with`) DEGIL. Yansitma FK'ler
#: uzerinden `app_users`a kadar yuruyor ve ifade tabanli indeksi icin uyari
#: basiyordu (olculdu); goc yalniz asagidaki sutunlara dokunuyor.
_irsaliye = sa.table(
    IRSALIYE,
    sa.column("id", sa.Integer()),
    sa.column("company_id", sa.Integer()),
    sa.column("invoice_id", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_kalem = sa.table(
    "invoice_items",
    sa.column("id", sa.Integer()),
    sa.column("company_id", sa.Integer()),
    sa.column("invoice_id", sa.Integer()),
    sa.column("item_type", sa.String(20)),
    sa.column("description", sa.String(500)),
    sa.column("quantity", sa.Numeric(18, 4)),
    sa.column("source_snapshot", sa.Text()),
)
_satir = sa.table(
    SATIR,
    sa.column("company_id", sa.Integer()),
    sa.column("despatch_id", sa.Integer()),
    sa.column("invoice_item_id", sa.Integer()),
    sa.column("line_no", sa.Integer()),
    sa.column("product_id", sa.Integer()),
    sa.column("item_name", sa.Text()),
    sa.column("quantity", sa.Numeric(18, 4)),
    sa.column("unit_code", sa.String(16)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _geri_doldur(bind) -> None:
    irsaliye, kalem, satir = _irsaliye, _kalem, _satir

    sayac = {"irsaliye": 0, "satir": 0, "atlanan_kalem": 0, "satirsiz_irsaliye": 0}
    irsaliyeler = bind.execute(
        sa.select(
            irsaliye.c.id, irsaliye.c.company_id, irsaliye.c.invoice_id, irsaliye.c.created_at
        ).order_by(irsaliye.c.id)
    ).all()
    for irs in irsaliyeler:
        sayac["irsaliye"] += 1
        kalemler = bind.execute(
            sa.select(
                kalem.c.id, kalem.c.description, kalem.c.quantity, kalem.c.source_snapshot
            )
            .where(
                kalem.c.company_id == irs.company_id,
                kalem.c.invoice_id == irs.invoice_id,
                kalem.c.item_type != HIZMET_TURU,
            )
            .order_by(kalem.c.id)
        ).all()
        satir_no = 0
        for k in kalemler:
            if k.quantity is None or k.quantity <= 0:
                sayac["atlanan_kalem"] += 1
                continue
            satir_no += 1
            bind.execute(
                satir.insert().values(
                    company_id=irs.company_id,
                    despatch_id=irs.id,
                    invoice_item_id=k.id,
                    line_no=satir_no,
                    product_id=_urun_kimligi(k.source_snapshot),
                    item_name=k.description,
                    quantity=k.quantity,
                    unit_code="C62",
                    created_at=irs.created_at,
                    updated_at=irs.created_at,
                )
            )
            sayac["satir"] += 1
        if satir_no == 0:
            sayac["satirsiz_irsaliye"] += 1
    GERI_DOLDURMA_SAYACI.clear()
    GERI_DOLDURMA_SAYACI.update(sayac)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. 1:1 kurali kalkar -------------------------------------------------
    if FATURA_TEKIL in _unique_adlari(inspector, IRSALIYE):
        with op.batch_alter_table(IRSALIYE) as batch_op:
            batch_op.drop_constraint(FATURA_TEKIL, type_="unique")

    # --- 2. Eksik bilesik FK hedefi: invoice_items(company_id, id) -----------
    inspector = sa.inspect(bind)
    if inspector.has_table(KALEM) and UQ_KALEM not in _unique_adlari(inspector, KALEM):
        with op.batch_alter_table(KALEM) as batch_op:
            batch_op.create_unique_constraint(UQ_KALEM, ["company_id", "id"])

    # --- 3. Satir defteri -----------------------------------------------------
    inspector = sa.inspect(bind)
    if inspector.has_table(SATIR):
        return

    op.create_table(
        SATIR,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("despatch_id", sa.Integer(), nullable=False),
        sa.Column("invoice_item_id", sa.Integer(), nullable=False),
        # Irsaliye ici 1..N sira; UBL `DespatchLine/ID`.
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        # Sevk anindaki ad — fatura kaleminin aciklamasi degisse de belge
        # o gunku adi gosterir.
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_code", sa.String(length=16), nullable=False, server_default="C62"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "despatch_id"],
            ["despatch_notes.company_id", "despatch_notes.id"],
            name=FK_IRSALIYE,
        ),
        # BILESIK — hedef yukarida (adim 2) kuruldu; gerekce baslikta.
        sa.ForeignKeyConstraint(
            ["company_id", "invoice_item_id"],
            ["invoice_items.company_id", "invoice_items.id"],
            name=FK_KALEM,
        ),
        sa.UniqueConstraint("company_id", "id", name=UQ_FIRMA_KIMLIK),
        sa.UniqueConstraint("despatch_id", "line_no", name=UQ_SATIR_NO),
        sa.CheckConstraint("quantity > 0", name=CK_MIKTAR),
    )
    op.create_index(IX_FIRMA_IRSALIYE, SATIR, ["company_id", "despatch_id"])

    # --- 4. Eski irsaliyeler satir alir --------------------------------------
    _geri_doldur(bind)


def downgrade() -> None:
    """Kosullu ve SESSIZ DEGIL — gerekce baslikta."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(IRSALIYE):
        return
    if FATURA_TEKIL not in _unique_adlari(inspector, IRSALIYE):
        irsaliye = _irsaliye
        coklu = bind.execute(
            sa.select(irsaliye.c.company_id, irsaliye.c.invoice_id)
            .group_by(irsaliye.c.company_id, irsaliye.c.invoice_id)
            .having(sa.func.count() > 1)
            .limit(5)
        ).all()
        if coklu:
            ornek = ", ".join(f"firma {r[0]} fatura {r[1]}" for r in coklu)
            raise RuntimeError(
                "20260915_0087 geri alinamiyor: birden fazla e-Irsaliyesi olan "
                f"fatura var ({ornek}). 1:1 kisiti (uq_despatch_notes_company_invoice) "
                "kurulamaz; hangi irsaliyenin kalacagi operatorun kararidir."
            )
    if inspector.has_table(SATIR):
        op.drop_index(IX_FIRMA_IRSALIYE, table_name=SATIR)
        op.drop_table(SATIR)
    inspector = sa.inspect(bind)
    if FATURA_TEKIL not in _unique_adlari(inspector, IRSALIYE):
        with op.batch_alter_table(IRSALIYE) as batch_op:
            batch_op.create_unique_constraint(FATURA_TEKIL, ["company_id", "invoice_id"])
    # Hedef KOSULLU duser (0085'in `finance_accounts` kalibi): onu isteyen tek
    # tablo yukarida dustu; adi yoksa dokunulmaz.
    inspector = sa.inspect(bind)
    if inspector.has_table(KALEM) and UQ_KALEM in _unique_adlari(inspector, KALEM):
        with op.batch_alter_table(KALEM) as batch_op:
            batch_op.drop_constraint(UQ_KALEM, type_="unique")
