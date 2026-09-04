"""Firma profilleri: `companies.profiller`.

Konu: bir firmanın HANGİ İŞLERİ yaptığının kümesi. Bu göç SÜTUNU AÇAR;
hiçbir kapı, hiçbir yetki kararı ve hiçbir menü bu turda ona BAKMAZ.

--- ÇOĞUL, ÇÜNKÜ AYIRICI DEĞİL --------------------------------------------

Sütun adı `profil` DEĞİL `profiller`dir ve bu bir yazım tercihi değil bir
ŞEMA İDDİASIDIR: bir firma aynı anda birden fazla profil taşır ve karma
olması İSTİSNA DEĞİL KURALDIR (sahibin kendi işi: bayi + servis + tarla +
sürü). Tekil bir ayırıcı sütun (`profil TEXT` + tek değer) bu firmayı
KAYDEDEMEZDİ; kaydedebilmek için ya bir değeri "asıl" ilan etmek ya da
geri kalanını atmak gerekirdi ve ikisi de ölçülmemiş bir olgu uydurmaktır.

Değer kümesi KAPALIDIR: `ciftci`, `pazarci`, `tuccar`, `veteriner`.
Bir firma bunların HERHANGİ BİR ALT KÜMESİNİ taşıyabilir.

--- `TEXT NOT NULL DEFAULT ''`, JSONB DEĞİL -------------------------------

İçerik: ayıklanmış + TEKİLLEŞTİRİLMİŞ + ALFABETİK SIRALI alt kümenin
virgülle birleştirilmiş hâli (`app/firma_profilleri.py`, `profilleri_coz`).

`''` "HENÜZ SEÇİLMEDİ" DEMEKTİR VE BİR OLGU UYDURMAZ. Boş küme, "bu firma
hiçbir profil bildirmedi" cümlesinin KENDİSİDİR — 0066'nın `base_unit`i
için reddedilen şey (`base_unit = unit` kopyalamak) burada YAPILMIYOR:
mevcut satırlara bir profil ATANMIYOR, boş küme yazılıyor ve boş küme
gerçekten de bugün her firmanın durumudur, zira sütun bu göçten önce
YOKTU ve hiçbir firma profil bildirmedi.

NULL YERİNE `''` SEÇİLDİ, gerekçesiyle: NULL üç değerli mantığı her
çağırana taşırdı (`x is None` ile `x == ''` ayrımını doğru yapmak
zorunluluğu) ve bu kümenin hiçbir yerinde "bilinmiyor" ile "boş" ARASINDA
bir fark YOKTUR — ikisi de "seçilmedi"dir. Ayrım bir gün gerekirse ikinci
bir sütun ister ve o sütunu bugün kimse dolduramaz.

`Text`, `String(N)` DEĞİL: üye sayısı dörttür ama alt küme sayısı 16'dır
ve kanonik dizginin uzunluğu üyelerin adlarına bağlıdır. Sabit bir üst
sınır seçmek, kümeye beşinci bir üye eklendiği gün SESSİZ bir kırpma ya da
diyalekte bağlı bir hata üretirdi.

JSONB DEĞİL: bu depo SQLite<->PostgreSQL 16 EŞLİĞİNİ koruyor ve diyalektin
JSON tiplemesi bu depoyu daha önce ISIRDI — göç 0016'nın `einvoice_payload`
gerekçesinin AYNISI, orada da TEXT-olarak-JSON seçildi.

--- KISIT ŞEMADA DEĞİL, UÇTA ---------------------------------------------

Belirteçlerin geçerliliği `CHECK` ile DAYATILMIYOR. Gerekçe ölçülebilir:
bir CSV alt kümesini `CHECK` ile doğrulamak, diyalekte bağlı dizgi
işlemleri (`regexp_split_to_array` PostgreSQL'de VAR, SQLite'ta YOK)
gerektirirdi ve iki motorda İKİ FARKLI kısıt yazmak, birinin diğerini
yalanladığı günü GÖRÜNMEZ yapardı — 0067'nin `NaN` kısıtının diyalekte
bağlı olması ORADA zorunluydu çünkü tek bir sayı deneniyordu; burada
denenen şey bir DİLBİLGİSİDİR ve dilbilgisi uçta, tek bir Python
fonksiyonunda duruyor.

Bunun bedeli ADIYLA konuyor: doğrudan SQL ile yazan biri geçersiz bir
belirteç sokabilir ve şema onu DURDURMAZ. Bugün böyle bir yazan YOKTUR
(iki yazma yolu da `profilleri_coz`dan geçer ve ikisi de test ediliyor);
bir gün olursa kısıt o günün kararıdır.

Revision ID: 20260904_0068
Revises: 20260903_0067
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260904_0068"
down_revision = "20260903_0067"
branch_labels = None
depends_on = None


TABLO = "companies"
SUTUN = "profiller"


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {sutun["name"] for sutun in inspector.get_columns(tablo)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLO not in inspector.get_table_names():
        # Şema henüz kurulmamışsa yapacak bir şey yok; bootstrap DDL
        # (`app/tenancy.py`) sütunu zaten taşıyor.
        return
    if SUTUN in _sutunlar(inspector, TABLO):
        return
    # `server_default=''` MEVCUT SATIRLAR İÇİN ZORUNLUDUR: `nullable=False`
    # bir sütunu varsayılansız eklemek dolu bir tabloda İKİ diyalektte de
    # düşer. Varsayılan bir olgu uydurmuyor — boş küme bugün her firmanın
    # gerçek durumudur (sütun yoktu, kimse profil bildirmedi).
    op.add_column(
        TABLO,
        sa.Column(SUTUN, sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLO not in inspector.get_table_names():
        return
    if SUTUN not in _sutunlar(inspector, TABLO):
        return
    op.drop_column(TABLO, SUTUN)
