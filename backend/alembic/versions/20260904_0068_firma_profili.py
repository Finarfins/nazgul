"""Firma PROFİLİ: kayıt anında seçilen iş kolları. HİÇBİR DAVRANIŞ BUNA BAĞLI DEĞİL.

Konu: Faz 5.2 — sahibin kararını SAKLAMAK. Bu göçün açtığı sütunu okuyup bir
modülü açan ya da kapatan KOD BU PR'DA YOKTUR; sütun yazılır ve okunur, ama
hiçbir yetki, menü ya da uç ona BAKMAZ. 0067'nin duruşunun aynısı: veri,
onu unutacak bir çağıran ortaya çıkmadan ÖNCE çivileniyor.

--- ÖLÇÜLEN KUSUR ----------------------------------------------------------

`companies` bugün ON İKİ sütun taşıyor (`app/tenancy.py`de ölçüldü: id, name,
tax_number, is_active, negative_stock_policy, credit_limit_policy ve beş
`farm_*` politikası, created_at) ve HİÇBİRİ firmanın NE İŞ YAPTIĞINI
söylemez. Kendi kendine kayıt olan bir kiracı (`POST /api/auth/register`)
firma adını, e-postasını ve telefonunu bırakır; iş kolunu bırakacağı yer
YOKTUR.

Sonuç bugün görünmezdir çünkü bütün kiracılar aynı ekranı görüyor. Modül
anahtarları geldiği gün görünür olur ve o gün GEÇMİŞ VERİ OLMAYACAKTIR —
kimse hangi kiracının veteriner, hangisinin pazarcı olduğunu geriye dönük
bilemez. Depo bu yüzden sorudan ÖNCE açılıyor.

--- KÜME, ENUM DEĞİL: KARIŞMAK KURALDIR, İSTİSNA DEĞİL --------------------

Sahip kararı: dört profil (`ciftci`, `tuccar`, `veteriner`, `pazarci`) ve
bunlar MODÜL ANAHTARIDIR, KİLİT DEĞİL.

Tek değerli bir `profil` sütunu (yani ENUM) ölçülebilir biçimde YANLIŞ
olurdu ve kanıtı sahibin KENDİ işletmesidir: aynı işletme hem bayilik
yapıyor (tüccar), hem servis veriyor, hem tarla işliyor (çiftçi), hem sürü
tutuyor (veteriner). Tek değer bu kiracıyı DÖRT kere yanlış sınıflandırırdı
ve hangi üçünü kaybettiği SORULAMAZDI.

Bu yüzden sütun bir KÜME saklıyor. Karışmak burada kenar durum değil, ilk
kullanıcının NORMAL hâlidir.

--- `TEXT NOT NULL DEFAULT ''`, JSONB DEĞİL -------------------------------

Depo biçimi ÖLÇÜLEREK seçildi, tercihle değil. 0016 (`einvoice_seam`) bu
depoda kuralı zaten yazmış:

    ``einvoice_payload`` is TEXT holding a JSON string (NOT JSONB): the
    codebase keeps SQLite<->PostgreSQL16 parity and has been bitten by
    dialect JSON typing before.

`sa.JSON()` bu depoda YALNIZ tedarikçi fiyat köprüsünün (0037/0038) yutma
tablolarındadır; oralarda saklanan şey SATICIDAN gelen, şekli bilinmeyen
ham yüktür. Burada saklanan şey DÖRT elemanlı, kapalı, doğrulanmış bir
kümedir — dialect JSON tiplemesinin riskini almak için sebep yok.

Virgülle birleştirme de bu depoda YENİ DEĞİLDİR: `app/routers/seasonal_plan.py`
`_parse_months` tam bu şekli zaten uyguluyor — virgülle böl, kırp, KÜMEYE al
(yineleneni düşür), `sorted()` ile sırala, tanınmayan değerde 422. 0068 aynı
şekli firma profiline uyguluyor.

SIRALAMA SAKLANIRKEN UYGULANIR ve bu bilinçlidir: `pazarci,ciftci` ile
`ciftci,pazarci` AYNI kümedir ve iki farklı dizgi olarak saklanırlarsa
eşitlik karşılaştırması dizgi düzeyinde YANLIŞ cevap verir. Normalleştirme
tek yerdedir (`app/firma_profilleri.py`).

--- BOŞ DİZGİ "SEÇİLMEDİ" DEMEKTİR, "HİÇBİRİ" DEMEZ -----------------------

Varsayılan `''` ve BACKFILL YOKTUR. Mevcut kiracıların hepsi `''` ile
kalır ve bu DOĞRU veridir: onlara bu soru hiç sorulmadı, dolayısıyla
cevaplarını uydurmak ölçülmemiş bir olguyu ölçülmüş gibi kaydetmek olurdu
(0067'nin `expiry_date` gerekçesinin aynısı, 0066'nın `base_unit = unit`
kopyalamasını reddetme gerekçesinin de aynısı).

`NOT NULL` + `''`, `NULL` yerine SEÇİLDİ: iki ayrı "yok" durumu (NULL ve
`''`) yaratmak, okuyan her yerde iki dallı kontrol isterdi ve o dallardan
biri er geç unutulurdu.

--- BU PR'DA HİÇBİR DAVRANIŞ BUNA BAĞLI DEĞİL -----------------------------

Modül anahtarları AYRI bir iştir. Bu göç yalnız kararı SAKLIYOR; hiçbir
yetki kontrolü, menü görünürlüğü ya da uç `profiller` sütununa BAKMIYOR.
Bağlama geldiği gün, veri zaten orada olacak.

Revision ID: 20260904_0068
Revises: 20260903_0067
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0068"
down_revision = "20260903_0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    mevcut = {c["name"] for c in inspector.get_columns("companies")}
    if "profiller" not in mevcut:
        # `server_default=''` MEVCUT SATIRLAR İÇİN ZORUNLU: NOT NULL bir
        # sütun varsayılansız eklenemez, çünkü eski satırlara yazılacak
        # değer yoktur. Backfill BUNDAN İBARETTİR ve bilinçlidir.
        op.add_column(
            "companies",
            sa.Column(
                "profiller",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )


def downgrade() -> None:
    op.drop_column("companies", "profiller")
