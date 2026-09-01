"""BKÜ katalog satırının KÖKENİ: elle mi yazıldı, dosyadan mı geldi.

Revision ID: 20260901_0064
Revises: 20260901_0063

--- NEDEN ------------------------------------------------------------------

Göç 0063 kataloğu açtı ve PHI gün sayısını faaliyet dışında kalıcı bir kayda
bağladı. O göçün kendi gerekçesi şuydu: "operatör katalogdaki 21 günü 7 yaptı"
denetimde GÖRÜNÜR bir olay olmalı, SESSİZ bir üstüne yazma değil.

Aynı soru bir adım GERİYE de sorulur ve 0063 onu cevaplayamıyordu:

    "Katalogdaki 21 nereden geldi?"

0063'te bu sorunun tek cevabı vardı ve o cevap kayıtta DEĞİLDİ: bir insan
formda 21 yazdı. Bu göçün ardından cevap iki tane oluyor — biri insan, biri
dosya — ve ikisi ayırt edilemez olursa denetim zinciri tam burada kopar.
Ayrım şu yüzden ÖNEMLİ: elle yazılan değeri bir kişi ETİKETE BAKARAK yazdı;
dosyadan gelen değeri kimse tek tek okumadı, bir liste taşıdı. İkisinin
güvenilirliği aynı değil ve denetçinin hangisi olduğunu görme hakkı var.

--- İKİ SÜTUN, İKİ AYRI OLGU ----------------------------------------------

``origin``            NASIL geldi   — ``MANUAL`` | ``IMPORT``
``origin_reference``  NEREDEN geldi — ``"<dosya adı>:<satır no>"``

Tek sütunda birleştirmek (örn. yalnız ``origin_reference`` tutup boşluğu
"elle" saymak) 0048'in ve 0063'ün kaçındığı hatanın aynısı olurdu: BOŞ o
zaman iki şeyi birden söylerdi — "elle girildi" ve "içe aktarıldı ama dosya
adı kaydedilmedi". Ayrı sütunla ``origin`` HER ZAMAN doludur ve
``origin_reference`` yalnız ``IMPORT`` satırlarda anlam taşır.

Dosya adı + satır numarası, "bu 21 nereden geldi" sorusunu firmanın KENDİ
dosyasına kadar götürüyor. Depo o dosyayı SAKLAMIYOR (0063'ün duruşu:
depo hiçbir PHI rakamı iddia etmez, dolayısıyla kaynağın kendisi de firmanın
elindedir); sakladığı, firmanın kendi arşivinde satırı bulmasına yetecek
İŞARETTİR.

--- ``server_default='MANUAL'`` BİR GERİYE DOLDURMA DEĞİLDİR ---------------

0063 geriye doldurmayı REDDETMİŞTİ ve gerekçesi şuydu: bugün temiz kaydedilen
bir hasat yarın da temiz kaydedilmelidir. Buradaki varsayılan o kuralı
ÇİĞNEMİYOR, çünkü uydurma değil ÖLÇÜLEBİLİR bir olgu: bu göçten önce katalog
satırı yaratmanın TEK yolu ``create_ppp`` ucuydu (ekrandaki form), yani
mevcut her satır GERÇEKTEN elle yazılmıştır. Varsayılan geçmişe bir iddia
eklemiyor, geçmişte zaten doğru olanı yazıya geçiriyor.

``origin_reference`` bu satırlarda NULL kalır ve NULL burada "elle girilenin
dosyası yoktur" demektir — eksik veri değil, olmayan veri.

--- DÜZENLEME KÖKENİ DEĞİŞTİRMEZ ------------------------------------------

``update_ppp``in ``SET`` listesinde bu iki sütun YOKTUR ve bu bilinçlidir.
Köken satırın NEREDEN GELDİĞİdir; bir insanın sonradan değeri düzeltmesi onu
"elle girilmiş" yapmaz, tarihçesini değiştirmez. Düzeltmenin kendisi
``updated_at`` ile zaten görünür.

Ters tercih (düzenlemede ``origin``i ``MANUAL``a çevirmek) denetçiden bilgi
SAKLARDI: satırın bir listeden geldiği gerçeği ilk düzeltmede silinirdi.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0064"
down_revision = "20260901_0063"
branch_labels = None
depends_on = None

KATALOG = "plant_protection_products"


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if KATALOG not in set(inspector.get_table_names()):
        # 0063 tabloyu açmadıysa burada açacak bir şey yok; zincir sırası
        # bunu garanti ediyor, koşul yalnız kısmi/onarılmış veritabanları için.
        return
    mevcut = _sutunlar(inspector, KATALOG)
    if "origin" in mevcut and "origin_reference" in mevcut:
        return
    with op.batch_alter_table(KATALOG) as batch:
        if "origin" not in mevcut:
            batch.add_column(
                sa.Column(
                    "origin",
                    sa.String(length=20),
                    nullable=False,
                    # Gerekçe başlıkta: bu bir geriye doldurma DEĞİL, mevcut
                    # satırlar hakkında ölçülebilir bir olgunun yazıya
                    # geçirilmesi.
                    server_default="MANUAL",
                )
            )
        if "origin_reference" not in mevcut:
            batch.add_column(
                sa.Column("origin_reference", sa.String(length=255), nullable=True)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if KATALOG not in set(inspector.get_table_names()):
        return
    mevcut = _sutunlar(inspector, KATALOG)
    if "origin" not in mevcut and "origin_reference" not in mevcut:
        return
    with op.batch_alter_table(KATALOG) as batch:
        if "origin_reference" in mevcut:
            batch.drop_column("origin_reference")
        if "origin" in mevcut:
            batch.drop_column("origin")
