"""E4a e-IRSALIYE DUZ SEVK: `despatch_notes` defteri.

Revision ID: 20260913_0083
Revises: 20260912_0082

--- KAPSAM: BIR FATURA -> BIR IRSALIYE ------------------------------------

Bu goc E4a'nin (DUZ SEVK) semasidir: butun miktar tek seferde, tek soforle,
tek aracla gidiyor. KISMI SEVK, bir faturaya BIRDEN COK irsaliye ve
ReceiptAdvice (ticari yanit) akisi E4b'nin isidir ve bu goc onlari
ENGELLEMEZ, yalnizca BUGUN kurmaz:

* Baglanti `despatch_notes.invoice_id` UZERINDEN kurulur, ters yonde
  `invoices` tablosuna bir `despatch_id` sutunu EKLENMEZ. Ters sutun
  1:1'i SEMAYA CIVILERDI; E4b'de ikinci bir irsaliye acmak o sutunu
  dusurmeyi gerektirirdi.
* `UNIQUE(company_id, invoice_id)` E4a'nin "bir fatura bir irsaliye"
  kuralini VERITABANI SEVIYESINDE tutar ve E4b'de TEK BIR `op.drop_
  constraint` ile kalkar. Kural uygulama katmaninda birakilsaydi, iki
  es zamanli POST ayni faturaya IKI irsaliye acabilirdi.
* Satir tablosu (`despatch_lines`) BU GOCTE YOK ve bu bir eksiklik degil
  bir KAPSAM karari: duz sevkte irsaliye satirlari fatura satirlarinin
  BIREBIR kopyasidir ve kopyalanan bir sey ayri bir defter gerektirmez.
  Kismi sevk (satir basina TAHSIS) geldiginde o defter E4b'de acilir;
  bugun acilsaydi her satiri fatura satiriyla ayni tutmak zorunda olan,
  hicbir seyi olcmeyen bir ikiz olurdu.

--- `despatch_uuid`: SABIT ETTN, CIFT BELGENIN PANZEHIRI ------------------

ETTN belge OLUSTURULURKEN uretilir ve gonderim denemeleri boyunca
DEGISMEZ. Gerekce olculdu ve kesif raporunun P1 riskiyle birebir
(`docs/e4-eirsaliye-kesif-2026-09-09.md` §6): gonderim TIMEOUT'a
dustugunde belgenin saglayiciya inip inmedigi BILINMEZ. Her denemede taze
bir UUID uretilseydi ikinci deneme saglayici gozunde YENI BIR BELGE olur
ve ayni sevk icin IKI e-Irsaliye kesilirdi. Sutun bu yuzden gonderimden
DEGIL, satirin dogusundan gelir.

UNIQUE KURESELDIR (`company_id` anahtarda YOK) ve bu 0080'in
`islem_anahtari` kararinin ayni gerekcesidir: deger bir UUID'dir,
carpismasi pratikte imkansizdir ve kiraci sutunu eklemek tekilligi
ZAYIFLATIR (ayni ETTN iki firmada yasayabilirdi). ETTN GIB nezdinde de
kureseldir; sutunun tekilligi o gercegi taklit eder.

36 KARAKTER: kanonik tireli UUID metni (`str(uuid.uuid4())`). Ham 32
haneli bicim DEGIL, cunku UBL `cbc:UUID` ve SOAP `@UUID` tireli bicimi
tasir; ikisini birbirine cevirmek zorunda kalmamak icin depoda da tireli
durur.

--- LOJISTIK KOLONLARI: CHECK VAR, NOT NULL YOK --------------------------

GIB e-Irsaliye kilavuzu tasima bilgisini IKI DALLI duzenliyor (kesif §3.2,
[G §9-10]): ya PLAKA + SOFOR, ya KARGO/LOJISTIK FIRMASI. Dordunu birden
NOT NULL yapmak kilavuzun IZIN VERDIGI bir sevki semada IMKANSIZ kilardi.
Dordunu birden serbest birakmak ise HICBIR tasima bilgisi tasimayan bir
irsaliye uretirdi.

Bu yuzden dordu de NULL kabul eder ama `ck_despatch_notes_tasima` iki
DALDAN BIRININ TAM olmasini ister. E4a'nin kendi (DAHA DAR) kurali —
plaka + sofor ZORUNLU — uc katmanindadir (`app/routers/despatch_notes.py`),
cunku o bir KAPSAM karari, bir mevzuat kurali degil: kargo dali E4b'de
acildiginda semaya DOKUNULMADAN acilabilsin.

`driver_national_id` 11 hane: TCKN sabit uzunluktur. METIN'dir, sayi
degil — bas sifir kaybolmaz ve aritmetik yapilamaz (kesif §3.3'un
`tax_id:String(11)` onerisiyle ayni gerekce).
`carrier_tax_number` 60: kesif §3.3 VKN/TCKN icin metin onerdi; 60 hane
yabanci tasiyicinin vergi kimligini de alir ve bir hata GOVDESININ buraya
sigmasini engeller (0080 `fail_reason`, 0081 `gib_status_code` ile ayni
gerekce).

--- ZAMAN: DUZENLEME TARIHI ILE FIILI SEVK AYRI --------------------------

`issue_date` (Date) belgenin DUZENLENDIGI gun; `actual_shipment_at`
(DateTime, timezone=True) malin FIILEN yola ciktigi an. Kesif §3.2 bunu
acikca ayiriyor: "Duzenleme zamani ile fiili sevk zamani ayri tutulmali."
Tek sutuna indirilseydi UBL'in `cbc:IssueDate` ile
`cac:Despatch/cbc:ActualDespatchDate`+`ActualDespatchTime` ikilisi ayni
degerden turer ve gec duzenlenen bir irsaliye sevki de gec gostererek
tevsiki bozardi.

`actual_shipment_at` TEK bir tz-farkindali damgadir, ayri Date+Time DEGIL:
depoda tek an, UBL'de ikiye BOLUNUR (`edespatch.py::_sevkiyat`). Ters
yonde birlestirmek (iki sutundan tek an uretmek) saat dilimi bilgisi
olmadan IMKANSIZDIR.

--- YABANCI ANAHTARLAR: BIRI BILESIK, BIRI DEGIL -------------------------

`(company_id, delivery_customer_id) -> customers(company_id, id)`
BILESIKTIR (0062'nin kurali). Hedef `uq_customers_company_id` 0026'da
ZATEN kurulmus; bedava geliyor ve bir firmanin irsaliyesinin BASKA
firmanin musterisine teslim gorunmesini VERITABANI SEVIYESINDE imkansiz
kiliyor.

`invoice_id -> invoices.id` ise CIPLAK, ve bu bir ihmal degil OLCULMUS bir
karar: `invoices` uzerinde `uq_invoices_company_id` YOKTUR (olculdu —
`alembic/versions/` altinda o adda hicbir kisit yok; tablo 0012'de
`uq_invoices_company_number` ve `uq_invoices_work_order_type` ile
aciliyor). Bilesik anahtari kurmak once o UNIQUE'i eklemeyi, o da
SQLite'ta `batch_alter_table("invoices")` ile TABLONUN YENIDEN
INSASINI gerektirirdi. Semadaki EN MERKEZI mali tabloyu bir ozellik
gocunde yeniden insa etmenin yaricapi, bu ozelligin kazandirdigindan
buyuktur.

KAYBEDILEN KORUMA YERINE KONDU ve iki katmanda olculuyor:
`UNIQUE(company_id, invoice_id)` ayni firma icinde tekilligi zaten
tutuyor, ve uc katmani faturayi HER ZAMAN `WHERE id=:id AND
company_id=:cid` ile cozuyor (kapi:
`tests/test_e4a_despatch_notes.py::test_CAPRAZ_KIRACI_faturasi_404`).
Iki koruma AYNI seyi olcmuyor: biri tekilligi, oteki kapsami.

--- `edespatch_*` ONEKI: 0081 ILE AYNI AYRIM -----------------------------

`edespatch_status` BIZIM ic durumumuzdur (`app/einvoice/edespatch.py`
icindeki durum makinesi), `edespatch_gib_status_code` ise saglayicinin
HAM kodudur. Ikisi AYRI sutundur cunku eslemede kod KAYBOLUR: 102 de 103
de tek bir ic duruma duser ve operatorun "saglayici tam olarak ne dedi"
sorusunun cevabi yalniz ham sutunda kalir. 0081'in
`einvoice_gib_status_code` gerekcesiyle BIREBIR ayni; genislik de ayni
(10).

`edespatch_provider_uuid` (64) saglayicinin KENDI belge kimligidir
(`SendDespatchAdviceResponse/DESPATCH_ID`), bizim ETTN'imiz DEGIL. Kesif
§5 ikisini ayiriyor ve 0081'in `external_id != ETTN` dersi burada da
gecerli: tek sutuna indirmek, sorgu anahtarini yanlis secmeye yol acardi.

--- ACILIS SEMASI DENETLENDI (VARSAYILMADI) ------------------------------

`despatch_notes` hicbir `metadata.create_all` kapsaminda DEGILDIR:
`app/core_schema.py`, `app/tenancy.py`, `app/inventory.py`,
`app/finance_engine.py`, `app/workflow.py` ve `app/auth.py` icindeki
`Table(...)` bildirimleri tarandi ve bu ad hicbirinde YOK (kapi:
`tests/test_e4a_despatch_notes.py::test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR`).
Yani 0072'de olculen kusur — acilis DDL'i tabloyu gocten ONCE kurar, goc
onu VAR bulup ATLAR — burada URETILEMEZ.

--- KIRACI TABLOSU: 120 -> 121 -------------------------------------------

Tablo `company_id` tasir, yani `TENANT_TABLES` envanterine (goc edilmis
semadan OTOMATIK turuyor) GIRER ve her sorgusundan `company_id=:cid`
yuklemi istenir. Karsiligi VARDIR: `app/routers/despatch_notes.py`teki
HER sorgu o yuklemi ACIKCA tasir.

Sayi 120 -> 121. Civi yerleri: `tests/test_sec6_ip_limitleri.py`,
`tests/test_wa1_ingress.py`, `tests/test_wa2_eslestirme.py`,
`tests/test_wa4_bekleyen.py` ve `tests/test_kiraci_disa_aktarim.py`
(sonuncusunda UC AYRI satirda DORT sayi var — dosya bir de ndjson
uretimini sayiyor).

--- GERI ALINABILIR ------------------------------------------------------

`downgrade()` tabloyu dusurur. Kayip BILINCLI ve DAR: silinen sey FATURA
degil, faturaya asili SEVK KAYDIDIR. Saglayiciya gonderilmis bir belge
GIB'de KALIR — geri alma onu iptal ETMEZ, yalnizca bizim kaydimizi siler.
up->down->up hem SQLite'ta hem GERCEK PostgreSQL 16'da kosuldu (PG ikizi:
`backend/test_e4a_despatch_notes_postgresql.py`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260913_0083"
down_revision = "20260912_0082"
branch_labels = None
depends_on = None

IRSALIYE = "despatch_notes"

#: Kapali kume. `app/einvoice/edespatch.py::BILINEN` ile BIREBIR ayni ve o
#: esitlik bir kapidir (`test_DURUM_KUMESI_goc_ile_modul_ayni`).
DURUMLAR = (
    "NONE",
    "QUEUED",
    "PROCESSING",
    "SIGNED",
    "SENT",
    "DELIVERED",
    "FAILED",
    "UNKNOWN",
)

FIRMA_KIMLIK = "uq_despatch_notes_company_id"
FATURA_TEKIL = "uq_despatch_notes_company_invoice"
ETTN_TEKIL = "uq_despatch_notes_uuid"
TASIMA_CHECK = "ck_despatch_notes_tasima"
DURUM_CHECK = "ck_despatch_notes_durum"
LISTE_INDEKS = "ix_despatch_notes_company_issue"


def _liste(degerler: tuple[str, ...]) -> str:
    return ",".join("'" + d + "'" for d in degerler)


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(IRSALIYE):
        return

    op.create_table(
        IRSALIYE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        # SABIT ETTN — gerekce baslikta. Satirin dogusunda uretilir.
        sa.Column("despatch_uuid", sa.String(length=36), nullable=False),
        # Belge numarasi (UBL `cbc:ID`). Saglayici `ID_ASSIGN_FLAG` ile kendi
        # numarasini da atayabildigi icin NULL kabul eder: numarasiz bir
        # taslak gecerli bir ara durumdur.
        sa.Column("despatch_number", sa.String(length=40), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("actual_shipment_at", sa.DateTime(timezone=True), nullable=False),
        # --- tasima: iki dal, CHECK ile (baslik) ---
        sa.Column("carrier_name", sa.String(length=200), nullable=True),
        sa.Column("carrier_tax_number", sa.String(length=60), nullable=True),
        sa.Column("driver_name", sa.String(length=120), nullable=True),
        sa.Column("driver_national_id", sa.String(length=11), nullable=True),
        sa.Column("vehicle_plate", sa.String(length=20), nullable=True),
        sa.Column("trailer_plate", sa.String(length=20), nullable=True),
        # --- teslimat ---
        sa.Column("delivery_address", sa.Text(), nullable=False),
        # NULL = faturanin musterisi. Kesif §3.2: teslim adresi/tarafi
        # faturadan FARKLI olabilir; ayni oldugunda ikinci bir satir
        # yazmak yerine sutun BOS birakilir ve okuma faturaya duser.
        sa.Column("delivery_customer_id", sa.Integer(), nullable=True),
        # --- saglayici tarafi ---
        sa.Column("edespatch_status", sa.String(length=20), nullable=False),
        sa.Column("edespatch_gib_status_code", sa.String(length=10), nullable=True),
        sa.Column("edespatch_provider_uuid", sa.String(length=64), nullable=True),
        sa.Column("edespatch_last_error", sa.Text(), nullable=True),
        sa.Column("edespatch_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edespatch_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        # CIPLAK ve gerekcesi baslikta: `invoices`ta `uq_invoices_company_id`
        # YOK ve onu eklemek SQLite'ta o tablonun yeniden insasini gerektirir.
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        # 0062'nin kurali. Hedef `uq_customers_company_id` 0026'da kuruldu.
        sa.ForeignKeyConstraint(
            ["company_id", "delivery_customer_id"],
            ["customers.company_id", "customers.id"],
            name="fk_despatch_notes_delivery_customer",
        ),
        # E4a'nin "bir fatura bir irsaliye" kurali. E4b bunu TEK bir
        # `drop_constraint` ile kaldirir.
        sa.UniqueConstraint("company_id", "invoice_id", name=FATURA_TEKIL),
        sa.UniqueConstraint("despatch_uuid", name=ETTN_TEKIL),
        # 0062'nin kurali, TERSTEN: gelecekteki bir `despatch_lines` bu
        # tabloya bilesik anahtarla baglanabilsin.
        sa.UniqueConstraint("company_id", "id", name=FIRMA_KIMLIK),
        sa.CheckConstraint(
            "(driver_name IS NOT NULL AND driver_national_id IS NOT NULL "
            "AND vehicle_plate IS NOT NULL) OR "
            "(carrier_name IS NOT NULL AND carrier_tax_number IS NOT NULL)",
            name=TASIMA_CHECK,
        ),
        sa.CheckConstraint(
            "edespatch_status IN (%s)" % _liste(DURUMLAR), name=DURUM_CHECK
        ),
    )
    # Liste ucunun TEK siralamasi `issue_date DESC, id DESC` ve kok yuklemi
    # `company_id=:cid`. Indeks o sorgunun sekli.
    op.create_index(LISTE_INDEKS, IRSALIYE, ["company_id", "issue_date"])


def downgrade() -> None:
    """Simetrik ve KOSULLU. Gerekce ve kaybin sinirlari baslikta."""
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(IRSALIYE):
        return
    op.drop_index(LISTE_INDEKS, table_name=IRSALIYE)
    op.drop_table(IRSALIYE)
