"""Tarladan gelen yazma isteklerinin bir kez uygulanmasını garanti eden kayıt.

Revision ID: 20260807_0045
Revises: 20260807_0044

Tarla kayıtları SAHADA giriliyor: parselin ortasında, çoğu zaman kapsama
alanının dışında. Faaliyet, girdi ve hasat girişleri cihazda kuyruğa yazılıp
şebeke gelince gönderiliyor. Bu kuyruk tekrar göndermeye AÇIKTIR — istek
sunucuya ulaşıp cevabı yolda kaybolursa istemci başarısız saymış olur ve aynı
işlemi yeniden yollar.

NEDEN AYRI TABLO — `field_operations` KULLANILAMADI. O tablonun
``work_order_id`` sütunu NOT NULL ve iş emrine yabancı anahtarla bağlı; tarla
faaliyetinin iş emri yok. Sütunu nullable yapmak, saha defterinin "her satır
bir iş emrine aittir" güvencesini bozardı ve iki alan adı tek tabloda
karışırdı.

BU DEFTER `field_operations`'TAN BİR NOKTADA AYRILIYOR: SONUÇ SAKLANIYOR.

Saha defterinde sonuç bilerek saklanmıyor; oradaki işlemler DURUM DEĞİŞİKLİĞİ
ve tekrar gönderimde istemcinin istediği şey iş emrinin GÜNCEL hâli — kayıt
yeniden okunup o hâli dönülüyor.

Burada işlemler KAYIT OLUŞTURMA. Tekrar gönderimde "güncel gerçeği oku" diye
bir şey yok: istemcinin ihtiyacı, ilk gönderimde oluşan satırın kimliği. Onu
saklamazsak ya ikinci bir satır oluşur (mükerrer faaliyet, mükerrer maliyet)
ya da istemci hangi kaydı oluşturduğunu asla öğrenemez. Bu yüzden
``target_id`` tutuluyor ve tekrar gönderimde O satır okunup dönülüyor —
saklanan şey sonucun fotoğrafı değil, sonuca giden kimlik; satırın kendisi her
seferinde tazeden okunuyor.

``company_id`` kiracı kapsamı için şart: ``operation_id`` istemcide üretiliyor,
iki firmanın aynı kimliği üretmesi teorik olarak mümkün ve benzersizlik firma
İÇİNDE tanımlanmalı.

Benzersizlik kısıtı ``create_table`` içinde tanımlanıyor: SQLite sonradan
ALTER ile kısıt ekleyemiyor ve testler SQLite üzerinde de koşuyor.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260807_0045"
down_revision = "20260807_0044"
branch_labels = None
depends_on = None

# Defterin kabul ettiği işlem türleri. Serbest metin bırakmak, istemci
# tarafındaki bir yazım hatasının sessizce yeni bir "tür" uydurmasına ve
# tekrar korumasının o tür için hiç çalışmamasına yol açardı.
KINDS = ("activity", "activity_input", "harvest")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("farm_operations"):
        return
    op.create_table(
        "farm_operations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # Kim kuyruğa aldı. Denetim izi; kullanıcı silinse de geçmiş bozulmasın
        # diye app_users'a bağlı.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        # İlk gönderimde oluşan satırın kimliği (bkz. modül başlığı).
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_farm_operations_company"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], name="fk_farm_operations_user"),
        sa.CheckConstraint(
            "kind IN ('" + "','".join(KINDS) + "')",
            name="ck_farm_operations_kind",
        ),
        # ASIL KORUMA. Uçtaki ön kontrol yarış durumunda ikinci isteği
        # kaçırabilir; bu kısıt kaçırırsa veritabanı reddeder ve uç bunu
        # "zaten uygulanmış" olarak karşılar.
        sa.UniqueConstraint("company_id", "operation_id", name="uq_farm_operations_operation"),
    )
    op.create_index(
        "ix_farm_operations_company_created",
        "farm_operations",
        ["company_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_farm_operations_company_created", table_name="farm_operations")
    op.drop_table("farm_operations")
