"""Kantar fişi: `field_harvest_tickets` + `field_harvest_ticket_deductions`.

Konu: hasadın DEPODA tartılan hâli. Bu göç kağıdı KAYDEDER; deftere hiçbir şey
yazmaz ve hiçbir defter satırını değiştirmez.

--- `gross_quantity` NEDİR: ÜRÜNÜN AĞIRLIĞI, ARACIN DEĞİL --------------------

**`gross_quantity`, KALİTE KESİNTİLERİNDEN ÖNCEKİ ÜRÜN AĞIRLIĞIDIR.**
Kantarın "brüt" hanesi (dolu araç) DEĞİLDİR ve dara (boş araç) ile ilgisi
YOKTUR. Kağıtta genellikle üç sayı vardır:

    dolu araç  −  boş araç  =  ÜRÜN            <- bu sütun BUDUR
    ÜRÜN       −  kalite kesintileri = net     <- bu sütun türetilir

Bu satır başlıkta duruyor çünkü karıştırılması ERROR ÜRETMEZ, CEVAP üretir:
biri buraya araç+yük ağırlığını yazarsa gross birkaç ton şişer, türetilen net
de şişer, `net_mismatch` (aşağıda) kağıdın netini gördüğü sürece bunu yakalar
— ama kağıdın neti girilmemişse HİÇBİR ŞEY yakalamaz ve ondan sonraki her
sayı sessizce yanlış olur. Sütun adı `gross_quantity`dir çünkü KESİNTİLERİN
brütüdür; aracın brütü değil.

Bu göç bunu ŞEMADA ZORLAYAMAZ — veritabanı bir tonun nereden geldiğini
bilemez. Zorlayabildiği tek şey `> 0` ve ölçek; gerisi bu başlık, uçtaki
alan adı ve arayüzün etiketidir.

--- KESİNTİ BİLEŞİMİ: TOPLAMSAL, BRÜT ÜZERİNDEN (SAHİP KARARI) --------------

    net = brüt − Σ(brüt × oran / 100)

SIRALI (ardışık) DEĞİL: ikinci kesinti birinciden ARTAN miktara değil, yine
BRÜTE uygulanır. İki bileşim %2 + %3'te farklı sonuç verir (toplamsal
0.9500·brüt, sıralı 0.9506·brüt) ve hangisinin doğru olduğu ALICIYA bağlıdır.

Sahip kararı, gerekçesiyle: gerçek bir kantar fişi görülmedi. Bu yüzden sunucu
bir formül SEÇER ama onu DOĞRU İLAN ETMEZ — kağıdın kendi neti de
`ticket_net_quantity` olarak saklanır ve ikisi ayrıştığında okuma yolunda
`net_mismatch` türetilir. Alıcı sıralı hesaplıyorsa bu bayrak İLK FİŞTE yanar;
sessizce sürüklenmez.

**`ticket_net_quantity` BİR GİRDİ DEĞİL, BİR TANIKTIR.** Sunucu netini ondan
türetmez; brütten ve oranlardan türetir. Kağıdın neti yalnız KARŞILAŞTIRILIR.
Türetimi ona bağlamak, istemcinin gönderdiği bir sayıyı hesabın kaynağı
yapardı — bu modülün `total_cost`u istemciden almama kuralının aynısı.

--- TÜRETİLEN HİÇBİR ŞEY SAKLANMIYOR ----------------------------------------

`derived_net_quantity`, `deduction_rate_total`, `net_mismatch` ve
`sold_exceeds_net` SÜTUN DEĞİLDİR; her okumada brütten, oranlardan ve
hasadın `sold_quantity`sinden yeniden türetilir. Gerekçe: saklanan bir türev,
bileşim kuralı düzeltildiği gün ESKİ kuralla hesaplanmış satırlar bırakır ve
hangi satırın hangi kuralla yazıldığı kayıttan okunamaz. Aynı duruş:
`_tutar` (saat × donmuş oran) ve `gross_margin`.

--- DEFTERE HİÇBİR ŞEY YAZILMIYOR (BU GÖÇÜN SINANABİLİR İDDİASI) ------------

Bu dilim `field_integration_events`e olay YAZMAZ ve `field_stok_tuketici`ye
DOKUNMAZ. Yani stok defteri bugünküyle BİREBİR AYNI kalır: hareket miktarı
hâlâ `field_harvests.quantity`dir, fişin brütü ya da neti DEĞİL.

--- EBEVEYN TEKİLLİĞİ ZATEN VAR: 0044'ÜN KURDUĞU KISIT KULLANILIYOR ---------

Bileşik yabancı anahtarın hedefi `field_harvests(company_id, id)` üzerinde
bir UNIQUE ister. Bu kısıt BU GÖÇTE KURULMUYOR çünkü ZATEN VAR: göç
`20260807_0044` `field_harvests`i yaratırken
`UniqueConstraint("company_id", "id", name="uq_field_harvests_company_id")`
satırını `create_table`ın İÇİNE koymuş.

ÖLÇÜLDÜ, VARSAYILMADI: `alembic upgrade head` sonrası temiz bir SQLite
şemasında `PRAGMA index_list('field_harvests')` tek bir benzersiz indeks
veriyor — `sqlite_autoindex_field_harvests_1` (`company_id`, `id`). Ad
diyalekte bağlıdır (PostgreSQL'de `uq_field_harvests_company_id`), ama
bileşik yabancı anahtarın istediği şey AD değil KISITTIR ve kısıt yerinde.

Bunun bedeli düşürüldü: kısıtı burada bir kez daha kurmak `batch_alter_table`
isterdi, o da SQLite'ta tabloyu YENİDEN KURAR (yabancı anahtar denetimini
kapatıp açan bir kapı ile birlikte). Var olan bir kısıt için tabloyu yeniden
kurmak, kazanç olmadan risk almaktır.

Revision ID: 20260904_0069
Revises: 20260904_0068
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0069"
down_revision = "20260904_0068"
branch_labels = None
depends_on = None


HASAT = "field_harvests"
FIS = "field_harvest_tickets"
KESINTI = "field_harvest_ticket_deductions"

UQ_FIS = "uq_field_harvest_tickets_company_id"

MIKTAR = sa.Numeric(18, 4)
ORAN = sa.Numeric(7, 4)


def _tablolar(inspector) -> set[str]:
    return set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    if FIS not in _tablolar(inspector):
        op.create_table(
            FIS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("harvest_id", sa.Integer(), nullable=False),
            # Kağıdın kendi numarası. NULL KABUL EDER: numarasız fiş vardır ve
            # numara UYDURMAK, tekrar korumasını yanlış bir kimliğe bağlardı.
            sa.Column("ticket_no", sa.String(length=60), nullable=True),
            sa.Column("buyer_name", sa.String(length=180), nullable=True),
            sa.Column("plate", sa.String(length=20), nullable=True),
            sa.Column("weighed_at", sa.DateTime(timezone=True), nullable=True),
            # KALİTE KESİNTİLERİNDEN ÖNCEKİ ÜRÜN AĞIRLIĞI — araç+yük DEĞİL.
            # Bkz. başlıktaki ilk bölüm; bu satır tek başına yetmez.
            sa.Column("gross_quantity", MIKTAR, nullable=False),
            # Brütün BİRİMİ ve o birimin ürünün taban birimine çevrilmesinde
            # KULLANILAN KATSAYI. İkisi de YAZILDIĞI GİBİ saklanır ve
            # YUVARLANMAZ: katsayı, o gün NEYE İNANILDIĞININ tek kanıtıdır
            # (`app/units.py`, sahip kararı 1). Yanlış çıkan bir katsayı
            # ASLA yeniden hesaplanmaz; düzeltme YENİ BİR SATIRDIR.
            sa.Column("entered_unit", sa.String(length=40), nullable=False),
            sa.Column("unit_factor", sa.Numeric(24, 10), nullable=False),
            # Brütün ÜRÜNÜN TABAN BİRİMİNDEKİ karşılığı. TÜRETİLMİŞ ama
            # SAKLANAN tek sayı, ve gerekçesi türevlerinkinin TERSİDİR:
            # bunu üreten katsayı da satırda duruyor, yani satır KENDİ
            # KENDİNİ doğrular. Kural değişse bile bu satırın hangi katsayıyla
            # yazıldığı kayıttan OKUNUR.
            sa.Column("base_quantity", MIKTAR, nullable=False),
            # KAĞIDIN NETİ — TANIK, GİRDİ DEĞİL. Sunucu netini bundan
            # TÜRETMEZ; yalnız karşılaştırır (`net_mismatch`).
            sa.Column("ticket_net_quantity", MIKTAR, nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_field_harvest_tickets_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "harvest_id"], [f"{HASAT}.company_id", f"{HASAT}.id"],
                name="fk_field_harvest_tickets_harvest_same_company",
            ),
            sa.CheckConstraint(
                "gross_quantity > 0", name="ck_field_harvest_tickets_gross_positive"
            ),
            sa.CheckConstraint(
                "base_quantity > 0", name="ck_field_harvest_tickets_base_positive"
            ),
            # Katsayı POZİTİF olmalı: sıfır katsayı ürünü yok eder, negatif
            # katsayı ise ölçülmüş bir olgu değildir.
            sa.CheckConstraint(
                "unit_factor > 0", name="ck_field_harvest_tickets_factor_positive"
            ),
            sa.CheckConstraint(
                "ticket_net_quantity IS NULL OR ticket_net_quantity >= 0",
                name="ck_field_harvest_tickets_net_nonnegative",
            ),
            # Kağıdın kimliği. NULL'lar çakışmadığı için numarasız fiş iki kez
            # girilebilir; bedel başlıkta adı konmuş hâliyle kabul edilmiştir.
            sa.UniqueConstraint(
                "company_id", "harvest_id", "ticket_no",
                name="uq_field_harvest_tickets_paper",
            ),
            # Kesinti satırlarının bileşik yabancı anahtarının hedefi.
            sa.UniqueConstraint("company_id", "id", name=UQ_FIS),
        )
        op.create_index(
            "ix_field_harvest_tickets_company_harvest", FIS,
            ["company_id", "harvest_id"],
        )

    inspector = sa.inspect(bind)
    if KESINTI not in _tablolar(inspector):
        op.create_table(
            KESINTI,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("ticket_id", sa.Integer(), nullable=False),
            # SERBEST METİN, KAPALI KÜME DEĞİL. Kesinti adları alıcıdan alıcıya
            # değişiyor (rutubet, yabancı madde, kırık…); bir enum uydurmak,
            # kağıtta yazan adı kaybedip yerine BİZİM sınıflandırmamızı
            # koyardı. Sınıflandırma sonradan eklenebilir; kaybolan ad geri
            # gelmez.
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("rate_percent", ORAN, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["company_id"], ["companies.id"],
                name="fk_field_harvest_ticket_deductions_company",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "ticket_id"], [f"{FIS}.company_id", f"{FIS}.id"],
                name="fk_field_harvest_ticket_deductions_ticket_same_company",
            ),
            # TEK SATIRIN sınırı. TOPLAMIN <= 100 olması SATIRLAR ARASI bir
            # kuraldır ve CHECK ile ifade EDİLEMEZ; uç onu yazmadan önce
            # doğrular (fiş ve kesintileri TEK istekte gelir, tam da bu yüzden).
            sa.CheckConstraint(
                "rate_percent >= 0 AND rate_percent <= 100",
                name="ck_field_harvest_ticket_deductions_rate_range",
            ),
        )
        op.create_index(
            "ix_field_harvest_ticket_deductions_company_ticket", KESINTI,
            ["company_id", "ticket_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    if KESINTI in _tablolar(inspector):
        op.drop_index(
            "ix_field_harvest_ticket_deductions_company_ticket", table_name=KESINTI
        )
        op.drop_table(KESINTI)

    inspector = sa.inspect(bind)
    if FIS in _tablolar(inspector):
        op.drop_index("ix_field_harvest_tickets_company_harvest", table_name=FIS)
        op.drop_table(FIS)
    # `uq_field_harvests_company_id` BU GÖÇÜN ESERİ DEĞİLDİR (0044 kurdu),
    # bu yüzden burada DÜŞÜRÜLMEZ. Düşürmek, bu göçün açmadığı bir kısıtı
    # kapatmak olurdu ve `crop_seasons` gibi ona bağlı başka bileşik yabancı
    # anahtarları kırardı.
