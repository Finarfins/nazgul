"""PARTİ DEFTERİ DEPOYA BAĞLANIR ve ALIŞ KALEMİ PARTİ AÇAR (1B-A).

Konu: PARTİ + SKT + FEFO, FAZ 1B dilim A — 0067'nin ÇAĞIRANSIZ deposuna İLK
ÇAĞIRANI bağlamak. Bu göç ŞEMAYI hazırlar; davranış `app/routers/
transactions.py`nin alış yolundadır.

--- ÖLÇÜLDÜ: `product_lots` ÜRETİMDE BOŞTUR ------------------------------

0067 tabloyu kurdu ve HİÇBİR ŞEYE BAĞLAMADI (`app/parti.py`nin çağıranı
yoktu; iki ayrı kapı bunu adıyla çiviliyordu). Yani üretimde bu tabloya
satır yazan bir yol İNŞA GEREĞİ yoktur ve tablo BOŞTUR.

Bu göç o cümleyi VARSAYMIYOR, `upgrade` başında ÖLÇÜYOR: satır varsa
`RuntimeError` ile GÜRÜLTÜLÜ ÖLÜR. Sebebi şudur — aşağıdaki üç değişikliğin
(NOT NULL `warehouse_id`, tekil kısıtın DEĞİŞMESİ, bileşik yabancı anahtar)
HİÇBİRİ mevcut satırlar için anlamlı bir varsayılan ÜRETEMEZ: bir partinin
hangi depoda durduğu bilinemez ve "varsayılan depo" yazmak defteri YALAN
SÖYLETİRDİ. Sessiz bir tahmin, geri çağırma kaydını sorgulanamaz yapar.

--- `warehouses` ÜZERİNDE `UNIQUE(company_id, id)` YOKTU: ÖLÇÜLDÜ ---------

0062'nin kuralı: bileşik yabancı anahtarın HEDEFİ TEKİL OLMALIDIR. 0067 aynı
kuralı `products` için uyguladı (`uq_products_company_id`).

`warehouses` için o tekil KURULMAMIŞTI — ne `app/inventory.py`nin `Table`
bildiriminde ne de bir göçte (ölçüldü: `uq_warehouses` literali depoda
GEÇMİYOR). Bu yüzden burada kuruluyor; VARSAYILMIYOR, sorularak kuruluyor,
çünkü ileride başka bir göç onu koymuş olabilir.

--- YENİ TEKİL: (company_id, product_id, lot_code, warehouse_id) ----------

0067'nin `uq_product_lots_company_product_code`u (company, product, code)
diyordu ve bu, parti defteri DEPOSUZ olduğu sürece doğruydu.

Depo girince YANLIŞ olur: aynı parti kodlu mal İKİ AYRI DEPODA durabilir ve
bu normaldir (üretici bir partiyi iki şubeye böler). Eski tekil o gerçeği
TEK SATIRA sıkıştırırdı — miktar iki depoda toplanır, "hangi depoda ne
kadar var" sorusu SORULAMAZ hale gelirdi. FEFO seçicisi bir gün depo
bazında çağrıldığında (dilim B) yanlış cevap verirdi.

Eski tekil DÜŞÜRÜLÜR, yenisi KURULUR. İkisi birlikte olmazsa ya eski kısıt
meşru ikinci depoyu REDDEDER ya da hiç kısıt kalmaz.

--- DİYALEKT AYRIMI AÇIK YAZILDI: 0071 ve 0072'nin DERSLERİ ---------------

0072'de ÖLÇÜLDÜ: `batch_alter_table` içindeki `create_check_constraint`
PostgreSQL'de SESSİZCE hiçbir DDL üretmiyor (alembic 1.19.2 /
SQLAlchemy 2.0.52) — batch yalnız tabloyu YENİDEN KURAN diyalektte (SQLite)
kısıtı `CREATE TABLE` metnine alıyor. Aynı sessizlik `create_unique_
constraint` ve `create_foreign_key` için de geçerlidir.

0071'in dersi ise tersidir: SQLite'ta var olan bir tabloyu İKİ AYRI batch ile
değiştirmek onu İKİ KEZ yeniden kurar ve arada yansıtılan kısıt henüz
olmayan/az önce düşen bir sütunu adıyla anarsa `OperationalError` verir.

İkisi birlikte şunu dayatıyor ve bu göç öyle yazıldı:
  * SQLite  -> sütun + eski tekilin DÜŞMESİ + yeni tekil + yabancı anahtar
               HEPSİ TEK `batch_alter_table` içinde,
  * ötekiler -> `op.add_column` / `op.drop_constraint` /
               `op.create_unique_constraint` / `op.create_foreign_key` AÇIK.

--- `purchase_items`: İKİ NULL SÜTUN, GERİYE DOLDURMA YOK -----------------

`lot_code VARCHAR(80) NULL` ve `expiry_date DATE NULL`.

NULL "bu kalem parti taşımıyor" der ve GEÇMİŞ HER KALEM için DOĞRUDUR: eski
alışların hangi partiden geldiği bilinmiyor ve bilinemez. Boş dizgiyle
doldurmak "parti kodu boş bir parti var" derdi ki bu BAŞKA bir cümledir.

`lot_code` burada `VARCHAR(80)`, `product_lots.lot_code` ise `Text`: kalem
bir FORM ALANIDIR ve sınırsız metin kabul eden bir form alanı, defteri
saçmalıkla doldurmanın yoludur. Defterdeki sütun 0067'de serbest bırakıldı
çünkü orada saklanan şey ÜRETİCİNİN bastığı etikettir; sınırı GİRİŞTE
koymak, sakladığı şeyi daraltmaktan farklıdır.

--- `core_schema.py` DEĞİŞTİRİLDİ Mİ: ÖLÇÜM AŞAĞIDA ----------------------

0067 `core_schema.stock_movements`e DOKUNMADI ve gerekçesi ŞUYDU: bildirim
SAYISAL MANİFESTODA bir varlık farkı üretir ve sayısal göç mutabakat
kapısını kırar (0066'nın dersi). O gerekçe bir NUMERIC sütun içindi.

Bu turda ÖLÇÜLDÜ, varsayılmadı: `lot_code` ve `expiry_date` NE `MONEY`
NE `QUANTITY` ailesindendir, yani `app/numeric_manifest.py`nin iki
sözlüğünden HİÇBİRİNE girmezler ve `capture_numeric_snapshot` onları
GÖRMEZ. Bildirim bu yüzden manifestoda varlık farkı ÜRETMİYOR.

Ölçümün ikinci yarısı: `20260712_0000_schema_baseline` `core_schema.
metadata.create_all` çağırır, yani TAZE veritabanı sütunları 0000'da
alır ve bu göç onları VAR bulup atlar; MEVCUT veritabanı ise 0000'ı çoktan
geçmiştir ve sütunları BURADA alır. İki yol AYNI şemada buluşur — koşul
`_sutunlar` ile sorulduğu için.

--- MİKTARIN ÖLÇEĞİ: HAREKETİN ÖLÇEĞİYLE AYNI ---------------------------

Alış yolu bugün `units.resolve` ÇAĞIRMIYOR: `purchase_items.quantity`
kullanıcının girdiği HAM birimdedir ve `stock_movements.quantity` de aynı
sayıyı taşır. Parti defteri bu dilimde HAREKETLE AYNI ÖLÇEKTE yazılır.

Bu bir KARAR değil bir ÖLÇÜMÜN kabulüdür ve dilimin dışındadır: birim
çözümünü buraya sokmak, alış yolunun bütün ölçeğini değiştirmek demekti ve
o iş bu dilimin kapsamında DEĞİL. Yazıldığı yer `transactions.py`nin
belge dizgisidir ve orada ADIYLA duruyor.

--- GERİ ALMA -------------------------------------------------------------

`downgrade` DA tabloyu BOŞ İSTER ve boş değilse gürültülü ölür.

Sebep simetrik değil, DAHA SERT: eski tekil (company, product, code) iki
farklı depodaki aynı kodu ARTIK REDDEDER. Satırları silerek yer açmak, geri
çağırma defterini geri alma adına YOK ETMEK olurdu; hangi satırın
silineceğini seçmek ise imkânsızdır.

`uq_warehouses_company_id` DÜŞÜRÜLMEZ: 0067'nin `uq_products_company_id`
için verdiği gerekçenin aynısı — onu bu göç yaratmış OLMAYABİLİR ve
düşürmek başka bir bileşik anahtarın hedefini elinden alırdı.

Revision ID: 20260908_0073
Revises: 20260907_0072
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0073"
down_revision = "20260907_0072"
branch_labels = None
depends_on = None

PARTI = "product_lots"
DEPO = "warehouses"
ALIS_KALEMI = "purchase_items"

UQ_DEPO = "uq_warehouses_company_id"
UQ_PARTI_KODU_ESKI = "uq_product_lots_company_product_code"
UQ_PARTI_KODU = "uq_product_lots_company_product_code_warehouse"
FK_PARTI_DEPO = "fk_product_lots_warehouse_same_company"
INDEKS_PARTI_DEPO = "ix_product_lots_company_warehouse"

# `purchase_items.lot_code` GİRİŞ sınırıdır; defterdeki `product_lots.lot_code`
# 0067'de bilinçli olarak `Text` bırakıldı. Bkz. başlık.
KALEM_KODU_UZUNLUK = 80


def _sutunlar(inspector, tablo: str) -> set[str]:
    return {sutun["name"] for sutun in inspector.get_columns(tablo)}


def _tekiller(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(tablo)}


def _parti_bos_olmali(bind, yon: str) -> None:
    """Tablo BOŞ değilse GÜRÜLTÜLÜ ÖL. Sessiz tahmin defteri yalan söyletir.

    `yon` yalnız hata metnindedir ve iki yönün gerekçesi FARKLIDIR — başlıkta
    ayrı ayrı yazılı.
    """
    adet = bind.execute(sa.text(f"SELECT count(*) FROM {PARTI}")).scalar_one()
    if int(adet) != 0:
        raise RuntimeError(
            f"20260908_0073 {yon}: `{PARTI}` BOŞ DEĞİL ({adet} satır). Bu göç "
            "tablonun boş olduğu ölçümüne dayanır (0067 hiçbir yazıcıya "
            "bağlanmamıştı). Satırların hangi depoda durduğu BİLİNEMEZ ve "
            "varsayılan depo yazmak defteri yalan söyletirdi. Veriyi elle "
            "taşıyın ya da bu göçü uygulamadan önce defteri boşaltın."
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. Bileşik yabancı anahtarın hedefi tekil OLMALI (0062'nin kuralı) --
    if UQ_DEPO not in _tekiller(inspector, DEPO):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(DEPO) as batch:
                batch.create_unique_constraint(UQ_DEPO, ["company_id", "id"])
        else:
            op.create_unique_constraint(UQ_DEPO, DEPO, ["company_id", "id"])

    # --- 2. Parti defteri depoya bağlanır -----------------------------------
    inspector = sa.inspect(bind)
    if "warehouse_id" not in _sutunlar(inspector, PARTI):
        _parti_bos_olmali(bind, "upgrade")
        # NOT NULL ve server_default YOK: tablo BOŞ olduğu için varsayılan
        # gerekmez, ve bir varsayılan koymak "depo 1" gibi bir yalanı
        # gelecekteki satırlara da dayatırdı.
        sutun = sa.Column("warehouse_id", sa.Integer(), nullable=False)
        if bind.dialect.name == "sqlite":
            # TEK BATCH: 0071'in dersi. İki ayrı batch tabloyu iki kez yeniden
            # kurar ve arada yansıtılan eski tekil, henüz olmayan sütunla
            # birlikte anılırsa `OperationalError` verir.
            with op.batch_alter_table(PARTI) as batch:
                batch.add_column(sutun)
                batch.drop_constraint(UQ_PARTI_KODU_ESKI, type_="unique")
                batch.create_unique_constraint(
                    UQ_PARTI_KODU,
                    ["company_id", "product_id", "lot_code", "warehouse_id"],
                )
                batch.create_foreign_key(
                    FK_PARTI_DEPO,
                    DEPO,
                    ["company_id", "warehouse_id"],
                    ["company_id", "id"],
                )
        else:
            # AÇIK DDL: 0072'de ölçüldü — batch, yeniden kurulum YAPILMAYAN
            # diyalektte kısıt DDL'ini SESSİZCE üretmiyor.
            op.add_column(PARTI, sutun)
            op.drop_constraint(UQ_PARTI_KODU_ESKI, PARTI, type_="unique")
            op.create_unique_constraint(
                UQ_PARTI_KODU,
                PARTI,
                ["company_id", "product_id", "lot_code", "warehouse_id"],
            )
            op.create_foreign_key(
                FK_PARTI_DEPO,
                PARTI,
                DEPO,
                ["company_id", "warehouse_id"],
                ["company_id", "id"],
            )
        op.create_index(INDEKS_PARTI_DEPO, PARTI, ["company_id", "warehouse_id"])

    # --- 3. Alış kalemi parti kodunu ve SKT'yi TAŞIR ------------------------
    # Kalem partiyi AÇAN girdidir; defterdeki satır ondan doğar. Kalemde
    # saklanması, belgenin ne söylediğinin belgede kalması içindir — defter
    # sonradan düzeltilse bile alış fişi ne yazdığını söyleyebilmelidir.
    inspector = sa.inspect(bind)
    kalem = _sutunlar(inspector, ALIS_KALEMI)
    eksikler = [
        sutun
        for sutun in (
            sa.Column("lot_code", sa.String(length=KALEM_KODU_UZUNLUK), nullable=True),
            sa.Column("expiry_date", sa.Date(), nullable=True),
        )
        if sutun.name not in kalem
    ]
    if eksikler:
        with op.batch_alter_table(ALIS_KALEMI) as batch:
            for sutun in eksikler:
                batch.add_column(sutun)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    kalem = _sutunlar(inspector, ALIS_KALEMI)
    dusecekler = [ad for ad in ("lot_code", "expiry_date") if ad in kalem]
    if dusecekler:
        with op.batch_alter_table(ALIS_KALEMI) as batch:
            for ad in dusecekler:
                batch.drop_column(ad)

    inspector = sa.inspect(bind)
    if "warehouse_id" in _sutunlar(inspector, PARTI):
        # Başlıktaki gerekçe: eski tekil iki depodaki aynı kodu REDDEDER ve
        # yer açmak için satır silmek defteri yok etmek olurdu.
        _parti_bos_olmali(bind, "downgrade")
        op.drop_index(INDEKS_PARTI_DEPO, table_name=PARTI)
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(PARTI) as batch:
                batch.drop_constraint(FK_PARTI_DEPO, type_="foreignkey")
                batch.drop_constraint(UQ_PARTI_KODU, type_="unique")
                batch.create_unique_constraint(
                    UQ_PARTI_KODU_ESKI, ["company_id", "product_id", "lot_code"]
                )
                batch.drop_column("warehouse_id")
        else:
            op.drop_constraint(FK_PARTI_DEPO, PARTI, type_="foreignkey")
            op.drop_constraint(UQ_PARTI_KODU, PARTI, type_="unique")
            op.create_unique_constraint(
                UQ_PARTI_KODU_ESKI, PARTI, ["company_id", "product_id", "lot_code"]
            )
            op.drop_column(PARTI, "warehouse_id")

    # `uq_warehouses_company_id` DÜŞÜRÜLMEZ — 0067'nin gerekçesi, bkz. başlık.
