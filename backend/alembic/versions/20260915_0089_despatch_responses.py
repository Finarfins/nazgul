"""E4b-2 e-IRSALIYE YANITI (ReceiptAdvice): `despatch_responses`, satirlari, durum CHECK'i.

Revision ID: 20260915_0089
Revises: 20260915_0088 (H17 auth_rate_limits indeksi)

Kaynak: `docs/e4b-kismi-sevk-kesif-2026-09-10.md` §3 (ReceiptAdvice ve durum
makinesi), §6 (PR E4b-2).

--- NE DEGISIYOR ---------------------------------------------------------

1. `despatch_responses`: alicinin BIZIM irsaliyemize gonderdigi ticari yanit
   belgesi (UBL `ReceiptAdvice`). Bir irsaliyenin birden cok yanit belgesi
   OLABILIR (alici duzeltilmis bir belge gonderebilir); her biri kendi
   ETTN'iyle BIR KEZ yazilir.
2. `despatch_response_lines`: yanitin satirlari — HANGI sevk satirindan NE
   KADAR alindi, NE KADAR reddedildi, NEDEN.
3. `despatch_notes` iki OZET sutun kazanir (`response_status`,
   `response_received_at`) ve `ck_despatch_notes_durum` uc yeni durumla
   genisler: ACCEPTED, PARTIALLY_ACCEPTED, REJECTED
   (`app/einvoice/edespatch.py::BILINEN` ile BIREBIR; kapi
   `tests/test_e4b2_irsaliye_yaniti.py::test_DURUM_KUMESI_goc_ile_modul_ayni`).

--- IDEMPOTENS ANAHTARI: (company_id, response_uuid) ---------------------

Ayni yanit belgesi her `sync`te yeniden gelir. Ikinci yazim bir KOPYA
olurdu; `uq_despatch_responses_uuid` bunu veritabani seviyesinde imkansiz
kilar. Uc katmani ONCE okur, sonra yazar; yaris halinde (iki es zamanli
sync) UNIQUE ihlalini "zaten yazilmis" olarak ele alir — PG ikizi iki
oturumla olcer. KIRACI KAPSAMLI, kuresel DEGIL: 0083'un
`uq_despatch_notes_uuid` gerekcesiyle AYNI (5.1c'nin "yeni firma" geri
yuklemesi ayni ETTN'i ikinci firmaya bilincli olarak kopyalar).

--- YABANCI ANAHTARLAR: HEPSI BILESIK (0044 / 0062 KURALI) ----------------

* `(company_id, despatch_id) -> despatch_notes(company_id, id)`; hedef
  `uq_despatch_notes_company_id` 0083'ten beri var.
* `(company_id, response_id) -> despatch_responses(company_id, id)`; hedef
  `uq_despatch_responses_company_id` BU GOCTE, tablonun kendisiyle kurulur.
* `(company_id, despatch_line_id) -> despatch_lines(company_id, id)`; hedef
  `uq_despatch_lines_company_id` 0087'den beri var.

Uc bag da FK oldugu icin 5.1c siniflandiricisi onlari yansitilan FK'lerden
kendisi esler; `kiraci_geri_yukleme.py`ye kayit GEREKMEZ.

--- `raw_xml`: DENETIM ICIN, ISTEMCIYE DEGIL -----------------------------

Alinan belge OLDUGU GIBI saklanir: satir ayristirmasi (kesif §3'te
DOGRULANMADI) ileride duzeltilirse eski yanitlar yeniden ayristirilabilsin.
Okuma ucu bu sutunu DONDURMEZ.

--- `despatch_notes` BATCH YENIDEN INSASI: KORUNANLAR OLCULDU ------------

SQLite'ta CHECK degistirmek tablonun yeniden insasidir. 0083'un iki CHECK'i
(`ck_despatch_notes_tasima`, genisleyen `ck_despatch_notes_durum`), uc FK'si
(`companies`, `invoices`, `fk_despatch_notes_delivery_customer`), iki kalan
UNIQUE'i (`uq_despatch_notes_uuid`, `uq_despatch_notes_company_id`) ve
`ix_despatch_notes_company_issue` yeniden insadan SONRA da yerindedir;
`tests/test_e4b2_irsaliye_yaniti.py::test_GOC_up_down_up_KORUNAN_kisitlar`
yansitmayi once/sonra karsilastirir. `copy_from` KULLANILMAZ: verilmeyen
indeksleri sessizce dusurur.

--- GERI ALINABILIR, AMA KOSULLU ----------------------------------------

`downgrade()` iki tabloyu ve iki sutunu dusurur, CHECK'i 0083'un kumesine
geri daraltir. Bir irsaliye uc yeni durumdan birindeyse daraltilmis CHECK o
satiri REDDEDER; goc o durumda satiri sessizce baska bir duruma cekmez,
ADIYLA durur — ticari yaniti silmek operatorun kararidir.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260915_0089"
down_revision = "20260915_0088"
branch_labels = None
depends_on = None

IRSALIYE = "despatch_notes"
YANIT = "despatch_responses"
YANIT_SATIR = "despatch_response_lines"

#: 0083'un kumesi — downgrade'in geri kurdugu CHECK.
ESKI_DURUMLAR = (
    "NONE",
    "QUEUED",
    "PROCESSING",
    "SIGNED",
    "SENT",
    "DELIVERED",
    "FAILED",
    "UNKNOWN",
)
#: Ticari yanittan dogan uc durum. Saglayici durum kodlari bunlari URETMEZ.
YANIT_DURUMLARI = ("ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED")
#: Kapali kume. `app/einvoice/edespatch.py::BILINEN` ile BIREBIR ayni.
DURUMLAR = ESKI_DURUMLAR + YANIT_DURUMLARI

#: Yanit belgesinin turu. KISMI_KABUL kodu DOGRULANMADI (kesif §3: saglayici
#: XSD'si yalniz KABUL/RED tanimlar); tur satirlardan TURETILIR.
YANIT_TURLERI = ("KABUL", "RED", "KISMI_KABUL")

DURUM_CHECK = "ck_despatch_notes_durum"
YANIT_OZET_CHECK = "ck_despatch_notes_response_status"

FK_YANIT_IRSALIYE = "fk_despatch_responses_despatch_same_company"
UQ_YANIT_ETTN = "uq_despatch_responses_uuid"
UQ_YANIT_FIRMA_KIMLIK = "uq_despatch_responses_company_id"
CK_YANIT_TURU = "ck_despatch_responses_type"
IX_YANIT_FIRMA_IRSALIYE = "ix_despatch_responses_company_despatch"

FK_SATIR_YANIT = "fk_despatch_response_lines_response_same_company"
FK_SATIR_SEVK = "fk_despatch_response_lines_despatch_line_same_company"
UQ_SATIR = "uq_despatch_response_lines_response_line"
CK_ALINAN = "ck_despatch_response_lines_received_nonneg"
CK_REDDEDILEN = "ck_despatch_response_lines_rejected_nonneg"
CK_TOPLAM = "ck_despatch_response_lines_total_positive"


def _liste(degerler: tuple[str, ...]) -> str:
    return ",".join("'" + d + "'" for d in degerler)


def _check_adlari(inspector, tablo: str) -> set[str]:
    return {c.get("name") for c in inspector.get_check_constraints(tablo) if c.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. despatch_notes: ozet sutunlar + genisleyen durum CHECK'i ----------
    sutunlar = {c["name"] for c in inspector.get_columns(IRSALIYE)}
    if "response_status" not in sutunlar:
        checkler = _check_adlari(inspector, IRSALIYE)
        with op.batch_alter_table(IRSALIYE) as batch_op:
            batch_op.add_column(sa.Column("response_status", sa.String(length=20), nullable=True))
            batch_op.add_column(
                sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=True)
            )
            if DURUM_CHECK in checkler:
                batch_op.drop_constraint(DURUM_CHECK, type_="check")
            batch_op.create_check_constraint(
                DURUM_CHECK, "edespatch_status IN (%s)" % _liste(DURUMLAR)
            )
            batch_op.create_check_constraint(
                YANIT_OZET_CHECK,
                "response_status IS NULL OR response_status IN (%s)" % _liste(YANIT_TURLERI),
            )

    # --- 2. Yanit belgeleri ---------------------------------------------------
    inspector = sa.inspect(bind)
    if not inspector.has_table(YANIT):
        op.create_table(
            YANIT,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("despatch_id", sa.Integer(), nullable=False),
            # Yanit belgesinin KENDI ETTN'i (UBL `cbc:UUID`) — irsaliyeninki DEGIL.
            sa.Column("response_uuid", sa.String(length=36), nullable=False),
            # UBL `cbc:ID`, GIB bicimi 16 karakter.
            sa.Column("response_number", sa.String(length=16), nullable=False),
            sa.Column("response_type", sa.String(length=20), nullable=False),
            sa.Column("issue_date", sa.Date(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            # Alinan belge, denetim icin (baslik). Istemciye DONMEZ.
            sa.Column("raw_xml", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "despatch_id"],
                ["despatch_notes.company_id", "despatch_notes.id"],
                name=FK_YANIT_IRSALIYE,
            ),
            sa.UniqueConstraint("company_id", "response_uuid", name=UQ_YANIT_ETTN),
            sa.UniqueConstraint("company_id", "id", name=UQ_YANIT_FIRMA_KIMLIK),
            sa.CheckConstraint(
                "response_type IN (%s)" % _liste(YANIT_TURLERI), name=CK_YANIT_TURU
            ),
        )
        op.create_index(IX_YANIT_FIRMA_IRSALIYE, YANIT, ["company_id", "despatch_id"])

    # --- 3. Yanit satirlari ---------------------------------------------------
    inspector = sa.inspect(bind)
    if not inspector.has_table(YANIT_SATIR):
        op.create_table(
            YANIT_SATIR,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("response_id", sa.Integer(), nullable=False),
            sa.Column("despatch_line_id", sa.Integer(), nullable=False),
            sa.Column("received_quantity", sa.Numeric(18, 4), nullable=False),
            sa.Column("rejected_quantity", sa.Numeric(18, 4), nullable=False),
            sa.Column("reject_reason", sa.String(length=200), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "response_id"],
                ["despatch_responses.company_id", "despatch_responses.id"],
                name=FK_SATIR_YANIT,
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "despatch_line_id"],
                ["despatch_lines.company_id", "despatch_lines.id"],
                name=FK_SATIR_SEVK,
            ),
            sa.UniqueConstraint("response_id", "despatch_line_id", name=UQ_SATIR),
            sa.CheckConstraint("received_quantity >= 0", name=CK_ALINAN),
            sa.CheckConstraint("rejected_quantity >= 0", name=CK_REDDEDILEN),
            sa.CheckConstraint("received_quantity + rejected_quantity > 0", name=CK_TOPLAM),
        )


def downgrade() -> None:
    """Kosullu ve SESSIZ DEGIL — gerekce baslikta."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(IRSALIYE):
        return
    irsaliye = sa.table(
        IRSALIYE,
        sa.column("id", sa.Integer()),
        sa.column("company_id", sa.Integer()),
        sa.column("edespatch_status", sa.String(20)),
    )
    yanitli = bind.execute(
        sa.select(irsaliye.c.company_id, irsaliye.c.id, irsaliye.c.edespatch_status)
        .where(irsaliye.c.edespatch_status.in_(YANIT_DURUMLARI))
        .order_by(irsaliye.c.id)
        .limit(5)
    ).all()
    if yanitli:
        ornek = ", ".join(f"firma {r[0]} irsaliye {r[1]} ({r[2]})" for r in yanitli)
        raise RuntimeError(
            "20260915_0089 geri alinamiyor: ticari yanit durumunda e-Irsaliye var "
            f"({ornek}). Daraltilmis ck_despatch_notes_durum bu satirlari reddeder; "
            "yaniti silmek operatorun kararidir."
        )

    if inspector.has_table(YANIT_SATIR):
        op.drop_table(YANIT_SATIR)
    if inspector.has_table(YANIT):
        op.drop_index(IX_YANIT_FIRMA_IRSALIYE, table_name=YANIT)
        op.drop_table(YANIT)

    inspector = sa.inspect(bind)
    sutunlar = {c["name"] for c in inspector.get_columns(IRSALIYE)}
    if "response_status" in sutunlar:
        checkler = _check_adlari(inspector, IRSALIYE)
        with op.batch_alter_table(IRSALIYE) as batch_op:
            if YANIT_OZET_CHECK in checkler:
                batch_op.drop_constraint(YANIT_OZET_CHECK, type_="check")
            if DURUM_CHECK in checkler:
                batch_op.drop_constraint(DURUM_CHECK, type_="check")
            batch_op.create_check_constraint(
                DURUM_CHECK, "edespatch_status IN (%s)" % _liste(ESKI_DURUMLAR)
            )
            batch_op.drop_column("response_received_at")
            batch_op.drop_column("response_status")
