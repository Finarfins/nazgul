"""SEC-9: `payment_idempotency` defteri KULLANICIYA kapsamlanıyor (`user_id`).

Revision ID: 20260914_0084
Revises: 20260913_0083

--- ÖLÇÜLEN AÇIK ----------------------------------------------------------

0025'in kurduğu `payment_idempotency` tekilliği YALNIZ firmayı taşıyor:

    UNIQUE(company_id, operation_type, resource_type, resource_id,
           idempotency_key)      -- `uq_payment_idempotency_operation`

`payment_allocation_engine._claim_payment_create` bu defteri
`operation_type='create_payment'`, `resource_type='payment'` ve SABİT
`resource_id='create'` ile yazıyor. Yani BİR FİRMADAKİ TÜM KULLANICILARIN
ödeme oluşturma istekleri TEK bir anahtar uzayını paylaşıyor. İki kullanıcı
aynı `Idempotency-Key`i seçtiğinde ne olduğu 28c9dd0 üzerinde ÖLÇÜLDÜ
(SQLite, `PAYMENT_ALLOCATION_ENGINE_ENABLED=true`):

  * GÖVDELER FARKLIYSA — B kullanıcısı HTTP 409 "Idempotency anahtarı farklı
    bir ödeme için kullanılmış" alıyor. B'nin MEŞRU ödemesi hiç yazılmıyor:
    A'nın anahtar seçimi B'ye hizmet reddi uyguluyor. Mesajın kendisi de bir
    kâhin: B, o anahtarın firmada BAŞKASI tarafından yakıldığını öğreniyor.

  * GÖVDELER AYNIYSA — B kullanıcısı A'nın `result_snapshot`ını alıyor
    (A'nın yazdığı `payments.id`). B'nin isteği HİÇ yürütülmüyor; ölçümde
    DÖRT ayrı kullanıcı isteğine karşılık `payments` tablosunda İKİ satır
    kaldı. B, yazmadığı bir kaydı kendi yanıtı olarak alıyor.

Yani kusur iki uçlu: ÇAPRAZ KULLANICI TEKRAR OYNATMA (snapshot sızıntısı) ve
ÇAPRAZ KULLANICI ANAHTAR ÇAKIŞMASI (hizmet reddi + varlık kâhini).

--- SÖZLEŞME 0076 İLE HİZALANIYOR ------------------------------------------

Genel `idempotency_keys` defteri (göç 0076) ZATEN kullanıcı kapsamlı:
`uq_idempotency_keys_company_user_key`, bkz. `app/idempotency.py`. SEC-9
`payment_idempotency`i AYNI sözleşmeye getiriyor; yeni bir fikir icat
etmiyor, mevcut olanı eksik kalmış deftere uyguluyor.

--- `user_id` NOT NULL, ESKİ SATIRLAR İÇİN 0 NÖBETÇİSİ ---------------------

Sütun NOT NULL'dır çünkü kapsamsız bir satır tam da kapatılan açığın
kendisidir: NULL kabul edilseydi `NULL = NULL` yanlış olduğu için PG'de
tekillik ESKİ satırlar arasında ÇALIŞMAZDI ve açık NULL taşıyan her satırda
açık kalırdı.

Var olan satırların YETKİLİ sahibi YOKTUR — defter kimi yazdığını hiç
kaydetmedi, geriye dönük türetilebileceği bir sütun da yok (`result_snapshot`
ödemenin gövdesidir, yazarı değil). Bu yüzden geri doldurma `0` NÖBETÇİSİYLE
yapılır: "kullanıcısı BİLİNMİYOR (göç öncesi)" anlamına gelen, `app_users`ta
KARŞILIĞI OLMAYAN bir değer. Yalan bir sahiplik uydurmaz; eski satırları tek
bir bilinmeyen-kullanıcı kovasında toplar. `app_users.id` 1'den başlayan bir
dizidir, yani `0` hiçbir gerçek kullanıcıyla ÇAKIŞAMAZ.

`user_id` FOREIGN KEY DEĞİLDİR ve bu kasıtlıdır: `0` nöbetçisi hiçbir
`app_users` satırına işaret etmez, FK konulsaydı geri doldurmanın kendisi
düşerdi. Aynı gerekçe `kiraci_geri_yukleme.KULLANICI_SUTUNLARI`nda da
geçerlidir — orada da bu sütun "kullanıcı kimliği, satır referansı değil"
olarak sınıflanır ve geri yüklemede OLDUĞU GİBİ korunur.

--- TEKİLLİK DEĞİŞTİRİLİYOR (EKLENMİYOR) -----------------------------------

Eski `uq_payment_idempotency_operation` DÜŞÜRÜLÜR, yerine

    UNIQUE(company_id, user_id, operation_type, resource_type, resource_id,
           idempotency_key)   -- `uq_payment_idempotency_kullanici_islem`

kurulur. Eski kısıt BIRAKILSAYDI açık AYNEN kalırdı: iki kullanıcının aynı
anahtarı eski kısıtta çakışmaya devam ederdi.

--- YENİ İNDEKS AÇILMADI (ÖLÇÜLDÜ) -----------------------------------------

0025'in indeks şekli `ix_payment_idempotency_company_resource`
(company_id, resource_type, resource_id) — `idempotency_key` TAŞIMIYOR, yani
"anahtar araması için indeks" diye AYNALANACAK bir şey YOK. Motorun okuma
yolu tam anahtar demetiyle sorguluyor ve onu YENİ TEKİLLİĞİN kendi indeksi
zaten karşılıyor. Var olan indeks olduğu gibi bırakılır: kullanıcıdan
BAĞIMSIZ kaynak taraması (rapor/denetim) hâlâ doğru yoldur.

--- TTL/TEMİZLİK YOK (ÖLÇÜLDÜ) ---------------------------------------------

`payment_idempotency` için tamamlanan satırları süpüren bir TTL/temizlik
yolu depoda YOKTUR (`grep -rn "DELETE FROM payment_idempotency" backend/app`
BOŞ). Süpürme YALNIZ genel `idempotency_keys` defterinde var
(`app/idempotency.py:296,324`) ve o tabloya bu göç DOKUNMUYOR. Yani yeni
sütunun bozacağı bir temizlik yolu yok.

--- İKİ DİYALEKT -----------------------------------------------------------

PostgreSQL: `ADD COLUMN` + `UPDATE` + `ALTER COLUMN SET NOT NULL` +
`DROP CONSTRAINT`/`ADD CONSTRAINT` yerinde yürür.

SQLite: NOT NULL'a çekmek de kısıt takası da tabloyu YENİDEN KURMAYI
gerektirir. `batch_alter_table` KULLANILIR ama `copy_from` ile AÇIKÇA
beslenir, yansıtmaya BIRAKILMAZ: SQLite'ın yabancı anahtarı ADSIZ yansıyor
(ölçüldü: `get_foreign_keys(...)[0]["name"] is None`) ve yansıtmaya dayanan
bir yeniden kurma CHECK/FK'yi sessizce kaybedebilir. `copy_from` tablonun
0025'teki tam yapısını verir; yeniden kurulan tablo CHECK'i ve FK'yi AYNEN
taşır.

--- GERİ ALINABİLİR (ÇAKIŞAN SATIRLAR DÜŞER) -------------------------------

`downgrade()` yeni tekilliği düşürür, eskisini geri kurar ve sütunu atar.

Eski şema, aynı (firma, işlem, kaynak, anahtar) demetini İKİ kullanıcı için
TUTAMAZ — bu satırların eski şemada bir YERİ yoktur. Bu yüzden downgrade,
her demette EN KÜÇÜK `id`yi tutup ötekileri SİLER. Bu bir veri kaybı DEĞİL
koruma kaybıdır: silinen satırlar defter kayıtlarıdır, ödeme/tahsis satırları
(`payments`, `payment_allocations`) AYRI tablolardadır ve DOKUNULMAZ. Kaybolan
tek şey, o anahtarların tekrar korumasıdır — ki eski şemada zaten YANLIŞ
kullanıcıya karşı "koruyorlardı".

Alternatif "çakışma varsa gürültülü düş" seçilmedi: downgrade tam da kötü bir
dağıtımı geri sarmak için koşulur ve o anda yürümeyen bir downgrade,
operatörü elle SQL'e iter — daha kötüsü.

up->down->up hem SQLite'ta hem GERÇEK PostgreSQL 16'da koşuldu; bkz.
`backend/test_sec9_payment_idempotency_user.py` ve
`backend/test_sec9_payment_idempotency_postgresql.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260914_0084"
down_revision = "20260913_0083"
branch_labels = None
depends_on = None

TABLO = "payment_idempotency"
SUTUN = "user_id"

#: Göç ÖNCESİ satırların sahibi bilinmiyor; `app_users.id` 1'den başlar, bu
#: yüzden 0 hiçbir gerçek kullanıcıyla çakışmaz.
ESKI_SATIR_KULLANICISI = 0

ESKI_TEKIL = "uq_payment_idempotency_operation"
YENI_TEKIL = "uq_payment_idempotency_kullanici_islem"

ESKI_TEKIL_SUTUNLAR = (
    "company_id",
    "operation_type",
    "resource_type",
    "resource_id",
    "idempotency_key",
)
YENI_TEKIL_SUTUNLAR = (
    "company_id",
    SUTUN,
    "operation_type",
    "resource_type",
    "resource_id",
    "idempotency_key",
)


def _tablo(*, tekil: str, tekil_sutunlar: tuple[str, ...]) -> sa.Table:
    """0025'teki yapının AÇIK kopyası + `user_id` (SQLite `copy_from` için).

    Yansıtma KULLANILMAZ: SQLite'ta FK adsız yansır ve yansıtmaya dayanan bir
    yeniden kurma CHECK/FK'yi sessizce kaybedebilir.
    """

    metadata = sa.MetaData()
    return sa.Table(
        TABLO,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(SUTUN, sa.Integer(), nullable=True),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_snapshot", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('processing','completed','failed')",
            name="ck_payment_idempotency_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.UniqueConstraint(*tekil_sutunlar, name=tekil),
        # 0025'in indeksi BURADA İLAN EDİLMEK ZORUNDA. Ölçüldü: ilan
        # edilmediğinde SQLite yeniden kurması indeksi DÜŞÜRÜYOR ve bir daha
        # kurmuyor (`get_indexes(...) == []`), yani `copy_from` eksik verilirse
        # göç sessizce indeks kaybettiriyor.
        sa.Index(
            "ix_payment_idempotency_company_resource",
            "company_id",
            "resource_type",
            "resource_id",
        ),
    )


def _tekiller(bind) -> set[str]:
    return {
        kisit["name"]
        for kisit in sa.inspect(bind).get_unique_constraints(TABLO)
        if kisit.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLO):
        return

    sutunlar = {c["name"] for c in inspector.get_columns(TABLO)}
    if SUTUN not in sutunlar:
        op.add_column(TABLO, sa.Column(SUTUN, sa.Integer(), nullable=True))
        op.execute(
            sa.text(
                "UPDATE " + TABLO + " SET " + SUTUN + "=:eski WHERE " + SUTUN + " IS NULL"
            ).bindparams(eski=ESKI_SATIR_KULLANICISI)
        )

    tekiller = _tekiller(bind)
    if YENI_TEKIL in tekiller:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            TABLO,
            copy_from=_tablo(tekil=ESKI_TEKIL, tekil_sutunlar=ESKI_TEKIL_SUTUNLAR),
        ) as batch:
            batch.alter_column(SUTUN, existing_type=sa.Integer(), nullable=False)
            if ESKI_TEKIL in tekiller:
                batch.drop_constraint(ESKI_TEKIL, type_="unique")
            batch.create_unique_constraint(YENI_TEKIL, list(YENI_TEKIL_SUTUNLAR))
    else:
        op.alter_column(TABLO, SUTUN, existing_type=sa.Integer(), nullable=False)
        if ESKI_TEKIL in tekiller:
            op.drop_constraint(ESKI_TEKIL, TABLO, type_="unique")
        op.create_unique_constraint(YENI_TEKIL, TABLO, list(YENI_TEKIL_SUTUNLAR))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLO):
        return
    if SUTUN not in {c["name"] for c in inspector.get_columns(TABLO)}:
        return

    # Eski şema aynı demeti İKİ kullanıcı için TUTAMAZ; her demette en küçük
    # `id` kalır. Silinen satırlar DEFTER kaydıdır — `payments` ve
    # `payment_allocations` DOKUNULMAZ (docstring: "GERİ ALINABİLİR").
    op.execute(
        sa.text(
            "DELETE FROM " + TABLO + " WHERE id NOT IN ("
            "SELECT MIN(id) FROM " + TABLO + " GROUP BY "
            + ", ".join(ESKI_TEKIL_SUTUNLAR)
            + ")"
        )
    )

    tekiller = _tekiller(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            TABLO,
            copy_from=_tablo(tekil=YENI_TEKIL, tekil_sutunlar=YENI_TEKIL_SUTUNLAR),
        ) as batch:
            if YENI_TEKIL in tekiller:
                batch.drop_constraint(YENI_TEKIL, type_="unique")
            if ESKI_TEKIL not in tekiller:
                batch.create_unique_constraint(ESKI_TEKIL, list(ESKI_TEKIL_SUTUNLAR))
            batch.drop_column(SUTUN)
    else:
        if YENI_TEKIL in tekiller:
            op.drop_constraint(YENI_TEKIL, TABLO, type_="unique")
        if ESKI_TEKIL not in tekiller:
            op.create_unique_constraint(ESKI_TEKIL, TABLO, list(ESKI_TEKIL_SUTUNLAR))
        op.drop_column(TABLO, SUTUN)
