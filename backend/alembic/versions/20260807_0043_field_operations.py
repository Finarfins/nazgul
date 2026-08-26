"""Sahadan gelen yazma isteklerinin bir kez uygulanmasını garanti eden kayıt.

Revision ID: 20260807_0043
Revises: 20260805_0042

Saha uygulaması çevrimdışı çalışır: teknisyen kapsama alanı dışındayken yaptığı
değişiklik cihazda bir kuyruğa yazılır ve şebeke gelince gönderilir. Bu kuyruk
tekrar göndermeye AÇIKTIR — istek sunucuya ulaşıp cevabı yolda kaybolursa
istemci başarısız saymış olur ve aynı işlemi yeniden yollar.

O yüzden "aynı işlem ikinci kez geldi mi" sorusunun cevabı sunucuda durmak
zorunda; istemcinin iyi niyetine bırakılamaz. ``operation_id`` istemcide
üretilen bir kimliktir ve bu tablo onun izini tutar: uç, işi yapmadan önce
buraya bakar ve kayıt varsa işlemi tekrar uygulamak yerine iş emrinin güncel
hâlini döndürür.

Benzersizlik kısıtının rolü İKİNCİL — ölçüldü (PG16). Uçtaki ön kontrol devre
dışı bırakıldığında tekrar gönderim kısıta hiç ulaşmıyor: iş emri satırı
``FOR UPDATE`` ile kilitli olduğu için ikinci istek bekliyor, kilidi alınca
ilerlemiş ``updated_at``i görüyor ve iyimser sürüm kontrolüne takılıp 409
dönüyor. Yani PostgreSQL'de tekrar gönderimi karşılayan şey ön kontroldür.
Kısıt, satır kilidi olmayan diyalektler (testler SQLite üzerinde de koşuyor) ve
derinlemesine savunma için var — doğruluğun dialect davranışına bağlı kalmaması
için.

Sonucun kendisi BİLEREK saklanmıyor. Saklamak, istemciye kaydın o anki
fotoğrafını döndürmek olurdu; oysa istemcinin istediği şey güncel gerçek.
Tekrar gönderimde iş emri okunup o hâli dönülüyor, böylece arada başka bir
değişiklik olduysa istemci onu da görüyor.

``company_id`` sütunu kiracı kapsamı için şart: operation_id istemcide
üretildiği için iki firmanın aynı kimliği üretmesi teorik olarak mümkündür ve
benzersizlik firma içinde tanımlanmalıdır.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260807_0043"
down_revision = "20260805_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("field_operations"):
        return
    op.create_table(
        "field_operations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        # Kimi işleten: hem denetim izi hem de "bu işlemi kim kuyruğa aldı"
        # sorusunun cevabı. Teknisyen değişirse geçmiş kayıt bozulmasın diye
        # app_users'a bağlı.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        # İstemcide üretilen kimlik. Uzunluk UUID'yi rahatça alır; uç ayrıca
        # biçim doğrulaması yapar.
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        # Şimdilik tek tür var ("status"), ama parça/işçilik/fotoğraf uçları
        # aynı tabloyu kullanacak. Türü baştan ayırmak, sonradan tabloyu
        # bölmekten ucuz.
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        # Emniyet kemeri, asıl koruyucu değil (gerekçesi yukarıdaki başlıkta).
        # Uygulama katmanındaki ön kontrol satır kilidi olmayan bir diyalektte
        # yarışa açıktır; o durumda ikinciyi bu kısıt durdurur.
        #
        # `create_table` İÇİNDE tanımlı, ayrı bir `create_unique_constraint`
        # çağrısıyla değil: SQLite kısıt eklemek için ALTER desteklemiyor
        # ("No support for ALTER of constraints in SQLite dialect") ve testler
        # SQLite üzerinde de koşuyor.
        sa.UniqueConstraint(
            "company_id", "operation_id", name="uq_field_operations_company_operation"
        ),
    )
    op.create_index(
        "ix_field_operations_company_work_order",
        "field_operations",
        ["company_id", "work_order_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("field_operations"):
        return
    op.drop_index("ix_field_operations_company_work_order", table_name="field_operations")
    # Benzersizlik kısıtı tablonun parçası; ayrıca düşürülmüyor (SQLite zaten
    # ALTER ile düşüremezdi). Tablo gidince o da gidiyor.
    op.drop_table("field_operations")
