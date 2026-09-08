"""e-Fatura/e-Arşiv sertleştirmesi: WEB_KEY, GİB durum kodu, PK etiketi.

Revision ID: 20260911_0081
Revises: 20260910_0080

--- ÜÇ SÜTUN, ÜÇ ÖLÇÜLMÜŞ BOŞLUK -------------------------------------------

Üçü de `invoices` üzerinde ve üçü de NULLABLE. Nullable olmaları bir kolaylık
değil, DOĞRU tiptir: bu depoda ZATEN kesilmiş faturalar var ve onların hiçbiri
bu üç değeri TAŞIYAMAZ (sağlayıcıya hiç gitmediler ya da gittiklerinde bu
alanlar okunmuyordu). NOT NULL + varsayılan seçilseydi, o eski satırlar
"değeri boş" ile "değeri bilinmiyor"u AYIRT EDİLEMEZ hâle getirirdi ve
`fetch_pdf` boş bir WEB_KEY'i geçerli sanıp sağlayıcıya anlamsız bir çağrı
yapardı.

1. `einvoice_web_key` VARCHAR(255)
   ÖLÇÜLDÜ: `app/einvoice/provider.py` içindeki e-Arşiv `fetch_pdf` yolu bugün
   ÇAĞRIYI HİÇ KURMUYOR, `EARSIV_WEB_KEY_YOK` ile gürültülü hata veriyor ve
   kendi yorumu sebebi yazıyor: "Bu anahtar gönderim yanıtında (``WEB_KEY``)
   geliyor ama bugün saklanmıyor". `GetEArchiveInvoice` UUID değil
   `WEB_VALIDATION_KEY` ister; anahtar YALNIZ `WriteToArchiveExtended`
   yanıtında bir kez döner ve bir daha sorulamaz. Saklanmazsa e-Arşiv PDF'i
   KALICI olarak erişilemez. 255: İzibiz şeması bir uzunluk sınırı ilan
   etmiyor; gözlenen anahtarlar 32-64 karakter, 255 rahat bir tavan.

2. `einvoice_gib_status_code` VARCHAR(10)
   ÖLÇÜLDÜ: `einvoice_status` bizim İÇ durumumuzdur (`status.py`in altı
   değeri) ve sağlayıcının kodu ona EŞLENİRKEN KAYBOLUR — `map_provider_status`
   `105`i de `130`u da tek bir iç duruma çevirir. Operatör "GİB tam olarak ne
   dedi" sorusunu bugün cevaplayamıyor. Kodun kendisi ayrı tutulur; iç durumun
   YERİNE değil YANINA. 10: gözlenen kodlar 3 haneli sayılar ve kısa harf
   kodları; 10 hane bunların tamamını alır ve bir hata GÖVDESİNİN buraya
   sığmasını da engeller (aynı gerekçe 0080'in `fail_reason` sütununda).

3. `einvoice_pk_alias` VARCHAR(120)
   ÖLÇÜLDÜ: e-Fatura (B2B) gönderiminde alıcı bir GİB POSTA KUTUSU ETİKETİYLE
   adreslenir (`urn:mail:...`). Bugün depoda bu etiketi tutan HİÇBİR sütun yok;
   `check_taxpayer` yalnız "mükellef mi" sorusunun EVET/HAYIR'ını döndürüyor ve
   alıcının HANGİ etikete sahip olduğu cevabın içinde kalıp atılıyor. 120:
   `urn:mail:` öneki + alan adlı bir e-posta; 120 hane gözlenen en uzun
   etiketin üç katından fazla.

--- İKİ DİYALEKTE DE AÇIKÇA ------------------------------------------------

`sa.String(n)` SQLite'ta da PostgreSQL'de de yazılıyor; PG'de VARCHAR(n),
SQLite'ta VARCHAR(n) (SQLite uzunluğu ZORLAMAZ ama tipi kaydeder). Uzunluk
PG'de GERÇEK bir kısıttır ve bu KASITLIDIR: sınırı aşan bir sağlayıcı değeri
sessizce kırpılmak yerine PG'de GÜRÜLTÜLÜ düşer.

--- AÇILIŞ ŞEMASI DENETLENDİ (VARSAYILMADI) --------------------------------

`invoices` tablosu `app/core_schema.py`in `metadata`sında YOKTUR — ölçüldü
(CPython 3.12, `'invoices' in metadata.tables` -> False; metadata 16 tablo
taşıyor ve hiçbiri bu değil). Yani tablo YALNIZ Alembic'ten doğuyor ve
0072'de ölçülen kusurun (açılış DDL'i tabloyu göçten ÖNCE kurar, göç onu VAR
bulup atlar) bu göçte KARŞILIĞI YOKTUR. Bu yüzden `create_all` tarafında
ikinci bir çivi yeri AÇILMADI: açılsaydı, var olmayan bir tabloya sütun
eklemeye çalışan ölü bir kod olurdu.

--- GERİ ALINABİLİR --------------------------------------------------------

`downgrade()` üç sütunu da düşürür. up->down->up GERÇEK PostgreSQL 16'da
koşuldu (bkz. `backend/test_e1_efatura_sertlestirme_postgresql.py`): PG'de
`DROP COLUMN` ve yeniden `ADD COLUMN` sütunu ilk hâliyle geri getirir, çünkü
sütunların hiçbirinde varsayılan, indeks ya da kısıt YOKTUR.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260911_0081"
down_revision = "20260910_0080"
branch_labels = None
depends_on = None

FATURA = "invoices"

#: (ad, üretici). Sıra `downgrade`de TERSİNE yürünür.
_SUTUNLAR: tuple[tuple[str, object], ...] = (
    ("einvoice_web_key", lambda: sa.Column("einvoice_web_key", sa.String(255), nullable=True)),
    (
        "einvoice_gib_status_code",
        lambda: sa.Column("einvoice_gib_status_code", sa.String(10), nullable=True),
    ),
    ("einvoice_pk_alias", lambda: sa.Column("einvoice_pk_alias", sa.String(120), nullable=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = {c["name"] for c in inspector.get_columns(FATURA)}
    for ad, uret in _SUTUNLAR:
        if ad not in mevcut:
            op.add_column(FATURA, uret())


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = {c["name"] for c in inspector.get_columns(FATURA)}
    for ad, _ in reversed(_SUTUNLAR):
        if ad in mevcut:
            op.drop_column(FATURA, ad)
