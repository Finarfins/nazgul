"""POS sistem carisi: company_id'nin sessizce KİRACI UYDURMASI kapatıldı.

ÖLÇÜLEN KUSUR (her iki lehçede de doğrulandı, migrate edilmiş şema üzerinde):

``pos_system_customers.company_id`` kiracı anahtarıdır ve tablonun BİRİNCİL
ANAHTARI'dır. ``20260724_0021`` tabloyu ``sa.Column("company_id", sa.Integer())``
+ ``PrimaryKeyConstraint("company_id")`` ile yarattı. Tek sütunlu tamsayı bir
birincil anahtar her iki lehçede de OTOMATİK ARTAN olur:

* PostgreSQL: sütun SERIAL'e dönüşür,
  ``company_id DEFAULT nextval('pos_system_customers_company_id_seq')``.
* SQLite: ``company_id INTEGER`` + tablo düzeyinde ``PRIMARY KEY (company_id)``
  sütunu ``rowid`` TAKMA ADI yapar; değer verilmezse rowid atanır.

Sonuç ikisinde de aynı: ``company_id`` VERİLMEDEN yapılan bir insert HATA
VERMEZ, olmayan bir kiracı UYDURUR. Ölçüm (PostgreSQL, tek gerçek firma id=2):

    INSERT INTO pos_system_customers (customer_id,created_at) VALUES (1,...);
    INSERT INTO pos_system_customers (customer_id,created_at) VALUES (2,...);

    fabricated_tenant | customer_id | company_exists
                    2 |           1 | t
                    3 |           2 | f      <-- firma 3 YOK

Bu, #43'ün baseline tablolardan ``server_default="1"`` değerini kaldırırken
temizlediği kusurun aynısıdır. Bu tablo o süpürmenin DIŞINDA kaldı, çünkü
buradaki varsayılan sabit bir değer değil bir DİZİ; sabit değer arayan tarama
onu görmedi.

YAPILAN: değer üretimi kaldırılır, ``NOT NULL`` yerinde kalır. Böylece
``company_id``'siz insert sessiz yanlış yazma olmaktan çıkıp SERT HATA olur.

* PostgreSQL: ``DROP DEFAULT`` + artık sahipsiz kalan diziyi düşür.
* SQLite: sütun ``BIGINT`` olarak yeniden bildirilir. SQLite'ta rowid takma adı
  YALNIZ bildirilen tip tam olarak ``INTEGER`` ise oluşur; ``BIGINT`` tamsayı
  ilgisini (``typeof`` = integer) korur ama takma ad ÜRETMEZ. Ölçülerek seçildi.

GERİ DÖNÜŞ SÖZLEŞMESİ. Her özellik ya GERİ GELİR ya da GERİ GELMEDİĞİ AÇIKÇA
bildirilir; İKİSİ DE test tarafından ÇİVİLENİR. Ne geri gelen ne çivilenen bir
özellik, bilinen farkın kimse görmeden büyümesi demektir::

    sahiplik (OWNED BY)      GERİ GELİR      eşitlik iddia edilir
    data_type                GERİ GELİR      eşitlik iddia edilir
    start_value              GERİ GELİR      eşitlik iddia edilir
    min_value                GERİ GELİR      eşitlik iddia edilir
    max_value                GERİ GELİR      eşitlik iddia edilir
    increment_by             GERİ GELİR      eşitlik iddia edilir
    cache_size               GERİ GELİR      eşitlik iddia edilir
    cycle                    GERİ GELİR      eşitlik iddia edilir
    (last_value, is_called)  GERİ GELMEZ     SONUÇ durumu doğrudan iddia edilir

``data_type`` özel not ister: SERIAL'in ürettiği özgün dizi ``integer``'dır,
çıplak ``CREATE SEQUENCE`` ise ``bigint`` üretir. İlk yazımda ``AS integer``
yoktu ve geri dönüş diziyi yapıca FARKLI geri getiriyordu; zayıf iddia bunu
göremiyordu, dizi sözleşmesi kapısı ilk koşuda yakaladı.

Geri gelMEYEN tek şey dizinin ÖNCEKİ KONUMUDUR: upgrade diziyi düşürür, o
bilgi hiçbir yerde durmaz. Bunun kabul edilebilir olmasının sebebi, dizinin
TEK tüketicisinin kapatılan kusurun kendisi olması; uygulama yolu company_id'yi
her zaman açıkça verir. Beyan edilen SONUÇ durumu doğrudan iddia edilir:
boş tabloda TAM OLARAK ``(1, False)`` (taze dizi davranışı), dolu tabloda
TAM OLARAK ``(MAX, True)`` yani sıradaki değer MAX+1. "Eski değer değil" gibi
bir vekil YETMEZ — (1, true) durumu böyle bir vekilden geçer ve bu beyanı
çelerdi.

KAPSAM DIŞI (ayrı sıralanıyor, bilerek dokunulmadı): bu tablonun
``companies``'e yabancı anahtarı yok (35 bağlanmamış tablodan biri) ve
``uq_pos_system_customers_customer`` kiracıya göre kapsanmış değil. Bu göç
YALNIZ sessiz değer üretimini kapatır.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260812_0057"
# #56 (20260812_0056) bu daldan ÖNCE indi; zincir onun üzerine kurulur,
# yoksa alembic iki başlı kalırdı.
down_revision = "20260812_0056"
branch_labels = None
depends_on = None

TABLE = "pos_system_customers"

# SQLite'ta tip değişimi tabloyu yeniden yaratmayı gerektirir. Kısıt ADLARI
# elle yazılır: batch reflection bunları yeniden üretirken adsız bırakabiliyor
# ve adlar downgrade ile uygulama kodunun beklediği sözleşmenin parçası.
_SQLITE_RECREATE = """
CREATE TABLE {new} (
    company_id {company_type} NOT NULL,
    customer_id INTEGER NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    CONSTRAINT pk_pos_system_customers PRIMARY KEY (company_id),
    CONSTRAINT uq_pos_system_customers_customer UNIQUE (customer_id)
)
"""


# PostgreSQL geri dönüş DDL'i; testin MUTASYONLA sınayabilmesi için modül
# düzeyinde. Sabitler göçün KENDİ SQL'i olduğu için mutasyon taklidi değil,
# gerçek yolu bozar.
# ``AS integer`` ŞART: SERIAL'in ürettiği özgün dizi integer'dır, oysa çıplak
# CREATE SEQUENCE bigint üretir. Bunu atlamak diziyi yapıca FARKLI geri
# getirirdi; zayıf iddia bunu görmüyordu, dizi sözleşmesi kapısı yakaladı.
_PG_CREATE_SEQUENCE = (
    "CREATE SEQUENCE IF NOT EXISTS {seq} AS integer OWNED BY {table}.company_id"
)
_PG_SET_DEFAULT = (
    "ALTER TABLE {table} ALTER COLUMN company_id SET DEFAULT nextval('{seq}')"
)
# is_called ÜÇÜNCÜ argümanla veriliyor: boş tabloda (1, false) -> sıradaki 1,
# dolu tabloda (MAX, true) -> sıradaki MAX+1.
_PG_POSITION_SEQUENCE = (
    "SELECT setval('{seq}', COALESCE((SELECT MAX(company_id) FROM {table}), 1), "
    "EXISTS(SELECT 1 FROM {table}))"
)


def _sqlite_swap(company_type: str) -> None:
    new = f"{TABLE}_new"
    op.execute(_SQLITE_RECREATE.format(new=new, company_type=company_type))
    op.execute(
        f"INSERT INTO {new} (company_id, customer_id, created_at) "
        f"SELECT company_id, customer_id, created_at FROM {TABLE}"
    )
    op.execute(f"DROP TABLE {TABLE}")
    op.execute(f"ALTER TABLE {new} RENAME TO {TABLE}")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return

    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN company_id DROP DEFAULT")
        # SERIAL'in ürettiği dizi sütuna OWNED BY bağlıdır; varsayılan
        # düşürüldükten sonra artık kimse kullanmıyor, geride bırakılmaz.
        op.execute(f"DROP SEQUENCE IF EXISTS {TABLE}_company_id_seq")
    elif bind.dialect.name == "sqlite":
        _sqlite_swap("BIGINT")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return

    if bind.dialect.name == "postgresql":
        sequence = f"{TABLE}_company_id_seq"
        op.execute(_PG_CREATE_SEQUENCE.format(seq=sequence, table=TABLE))
        op.execute(_PG_SET_DEFAULT.format(seq=sequence, table=TABLE))
        # Diziyi konumla. İki hâl AYRI ele alınır, çünkü setval'in üçüncü
        # argümanı (is_called) bir sonraki değeri belirler:
        #   - tablo BOŞ  -> setval(...,1,false): sıradaki değer 1. Tek argümanlı
        #     biçim diziyi "çağrılmış" işaretler ve sıradakini 2 yapardı; taze
        #     bir dizinin davranışı bu DEĞİLDİR.
        #   - tablo DOLU -> setval(...,MAX,true): sıradaki değer MAX+1, yani
        #     geri dönüşteki ilk insert var olan bir satırla çakışmaz.
        op.execute(_PG_POSITION_SEQUENCE.format(seq=sequence, table=TABLE))
    elif bind.dialect.name == "sqlite":
        _sqlite_swap("INTEGER")
