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
sayı sessizce yanlış olur. Sütun adı `gross_quantity`dir çünkü kesintilerin
BRÜTÜDÜR; aracın brütü değil.

Bu göç bunu ŞEMADA ZORLAYAMAZ — veritabanı bir tonun nereden geldiğini
bilemez. Zorlayabildiği tek şey `> 0` ve ölçek; gerisi bu başlık, uçtaki
alan adı ve arayüzün etiketidir.

--- KESİNTİ BİLEŞİMİ: TOPLAMSAL, BRÜT ÜZERİNDEN (OWNER KARARI) --------------

    net = brüt − Σ(brüt × oran / 100)

SIRALI (ardışık) DEĞİL: ikinci kesinti birinciden ARTAN miktara değil, yine
BRÜTE uygulanır. İki bileşim %2 + %3'te farklı sonuç verir (toplamsal
0.9500·brüt, sıralı 0.9506·brüt) ve hangisinin doğru olduğu ALICIYA bağlıdır.

Owner kararı, gerekçesiyle: gerçek bir kantar fişi görülmedi. Bu yüzden sunucu
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
hangi satırın hangi kuralla yazıldığı kayıttan okunamaz. Aynı posture:
`_tutar` (saat × donmuş oran) ve `gross_margin`.

--- DEFTERE HİÇBİR ŞEY YAZILMIYOR (BU GÖÇÜN SINANABİLİR İDDİASI) ------------

Bu dilim `field_integration_events`e olay YAZMAZ ve `field_stok_tuketici`ye
DOKUNMAZ. Yani stok defteri bugünküyle BİREBİR AYNI kalır: hareket miktarı
hâlâ `field_harvests.quantity`dir, fişin brütü ya da neti DEĞİL.

Niye fiş defteri BUGÜN oynatamaz — ölçülmüş bir SIRA sorunu: hasat olayı,
hasat YAZILIRKEN aynı işlemde üretilir ve tüketici onu ilk döngüsünde
tüketir. Kantar fişi ise kamyon depoya VARDIKTAN sonra girilir. Yani fiş
geldiğinde olay çoktan terminal (`SENT`) olmuştur. `_hasat_kalemleri`ye bir
LEFT JOIN eklemek bu yüzden YANLIŞ olurdu: birleştirme çalıştığı anda fiş
HENÜZ YOKTUR, sorgu her seferinde NULL görür ve "fişi varsa netini kullan"
kuralı hiçbir zaman ateşlenmez — kod doğru görünür, davranış hiç değişmez.
Defteri fişe bağlamak, olayın ne zaman üretildiğini değiştirmeyi ya da
düzeltici bir ikinci olay üretmeyi gerektirir; ikisi de AYRI bir iştir.

--- BİLEŞİK YABANCI ANAHTARLAR, ÇIPLAK OLANLAR DEĞİL ------------------------

Desen 20260812_0058'de kuruldu, 20260827_0062'de beşinci ebeveyne uygulandı;
burada altıncı ve yedincisine uygulanıyor:

    (company_id, harvest_id) -> field_harvests(company_id, id)
    (company_id, ticket_id)  -> field_harvest_tickets(company_id, id)

Çıplak `harvest_id -> field_harvests.id`, bir kiracının fişinin BAŞKA
kiracının hasadını işaret etmesini engellemez. Hedeflerin tekil olması için
`field_harvests`e `UNIQUE(company_id, id)` ekleniyor; yeni fiş tablosu kendi
UNIQUE'ini doğuşta taşıyor.

--- KAĞIDIN KİMLİĞİ: `UNIQUE(company_id, harvest_id, ticket_no)` ------------

Bu dilim çevrimdışı kuyruk kimliği (`operation_id`) TAŞIMIYOR ve bu bir
karardır: yeni bir kuyruk türü `ck_farm_operations_kind` CHECK'ini yeniden
yazmayı (SQLite'ta tablo yeniden kurmayı) gerektirirdi ve kantar fişi SAHA
değil DEPO yolundan girilir.

Yerine kağıdın KENDİ kimliği kullanılıyor: aynı hasada aynı fiş numarası İKİ
KEZ giremez. **BEDELİ ADI KONMUŞTUR:** numarası olmayan bir fiş iki kez
girilebilir — SQL'de NULL'lar birbiriyle çakışmaz, ve kağıtta numara yoksa
tekilleştirilecek bir kimlik de yoktur. Uydurulmuş bir kimlik (zaman damgası,
brüt+plaka özeti) iki AYRI kamyonun aynı dakikada aynı yükü getirmesini
"tekrar" sayardı; bu, sessiz bir VERİ KAYBI olurdu.

--- BİR HASAT, BİRDEN ÇOK FİŞ ----------------------------------------------

`harvest_id` üzerinde UNIQUE YOK: bir günün hasadı üç kamyonla gider ve üç fiş
üretir. Okuma yüzeyi bu yüzden hasat başına TOPLAR (Σ brüt, Σ türetilmiş net)
ve `sold_exceeds_net` bu toplama bakar.

--- BİRİM SÜTUNU YOK -------------------------------------------------------

Fişin miktarları HASADIN biriminde (`field_harvests.unit`) kabul edilir.
Kantarın kağıda KG basıp hasadın TON kaydedilmiş olması GERÇEK bir risktir ve
bu göç onu ÇÖZMÜYOR; ölçmüyor da. Bir `unit` sütunu koyup karşılaştırmamak,
birimin taşındığı izlenimini verirdi — `_hasat_kalemleri`nin
`field_harvests.unit`i okumama gerekçesiyle aynı. Bilinen ve KAYDEDİLMİŞ bir
boşluktur.

--- SQLite: TABLO YENİDEN KURULUR, PRAGMA KAPATILMAK ZORUNDA ---------------

`field_harvests`e UNIQUE eklemek SQLite'ta tabloyu YENİDEN KURAR (kopyala,
DROP, RENAME) ve `app/db.py` her bağlantıda `PRAGMA foreign_keys=ON` yapar.
0062'de ÖLÇÜLDÜ: pragma açıkken `DROP TABLE` yabancı anahtar hatası verir ve
`defer_foreign_keys` bunu kurtarmaz. Yordam 0062'nin kurduğu yordamdır ve
BİREBİR tekrarlanıyor: pragma'yı KAPAT, kur, `PRAGMA foreign_key_check` ile
kırık referans KALMADIĞINI ölç, pragma'yı geri aç.

Geri açmanın ASIL güvencesi burada değil `app/db.py`nin `checkout` kancasında
— açık bir işlem içinde `PRAGMA foreign_keys` NO-OP'tur (0062'de ölçüldü).
Buradaki `finally` ikinci katmandır.

--- GERİ ALMA ---------------------------------------------------------------

`downgrade` iki tabloyu ve `field_harvests` üzerindeki UNIQUE'i düşürür. VERİ
KAYBI VARDIR ve adı konmuştur: girilmiş her kantar fişi ve kesinti satırı
silinir. Defter ETKİLENMEZ — zaten hiç etkilenmemişti; bu göçün geri alınması
stok hareketlerinde HİÇBİR şeyi değiştirmez.

--- NUMARALANDIRMA ---------------------------------------------------------

Bu göç 0062'ye zincirlenir. 0063 develop'ta BOŞTUR ama BKÜ dalında ALINMIŞTIR;
aynı id'yi ilan eden iki dal #56/#60'ta ölçüldü ve yalnız BİRLEŞMEDE görünür.
Bu yüzden burada 0064 alındı. İki dal da 0062'nin çocuğu olduğu için SONRA
inen dalın `down_revision`ı yeniden işaret etmek ZORUNDADIR; bu göç henüz
develop'ta OLMAYAN bir revizyona zincirlenmiyor, çünkü o hâlde develop'ta
KOŞTURULAMAZDI.

Revision ID: 20260901_0064
Revises: 20260827_0062
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0064"
down_revision = "20260827_0062"
branch_labels = None
depends_on = None

HASAT = "field_harvests"
FIS = "field_harvest_tickets"
KESINTI = "field_harvest_ticket_deductions"

UQ_HASAT = "uq_field_harvests_company_id"
UQ_FIS = "uq_field_harvest_tickets_company_id"

MIKTAR = sa.Numeric(18, 4)
ORAN = sa.Numeric(7, 4)


def _tablolar(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _tekiller(inspector, tablo: str) -> set[str]:
    return {item["name"] for item in inspector.get_unique_constraints(tablo)}


class _YenidenKurulumKapisi:
    """SQLite'ta yabancı anahtar denetimini KAPATIR, ölçer ve GERİ AÇAR.

    0062'nin yordamının BİREBİR aynısı. Kopyalanmış olması bilinçli: göçler
    birbirinden ithal etmez, her biri kendi başına koşabilmelidir.
    """

    def __init__(self, bind) -> None:
        self._bind = bind
        self._sqlite = bind.dialect.name == "sqlite"

    def __enter__(self) -> "_YenidenKurulumKapisi":
        if self._sqlite:
            self._bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        return self

    def __exit__(self, tip, deger, iz) -> bool:
        if not self._sqlite:
            return False
        try:
            # Yalnız gövde SORUNSUZ bittiyse ölç: zaten patlamış bir göçün
            # üstüne ikinci bir hata koymak, ilkini gizler.
            if tip is None:
                kirik = self._bind.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if kirik:
                    raise RuntimeError(
                        "SQLite yeniden kurulumu KIRIK REFERANS bıraktı; göç "
                        f"durdu. PRAGMA foreign_key_check: {kirik[:5]!r}"
                    )
        finally:
            # İKİNCİ KATMAN, BİRİNCİSİ DEĞİL — bkz. başlık ve 0062.
            self._bind.exec_driver_sql("PRAGMA foreign_keys=ON")
        return False


def upgrade() -> None:
    bind = op.get_bind()

    with _YenidenKurulumKapisi(bind):
        # --- 1. Ebeveyn tekilliği: bileşik yabancı anahtarın hedefi ---------
        inspector = sa.inspect(bind)
        if UQ_HASAT not in _tekiller(inspector, HASAT):
            with op.batch_alter_table(HASAT) as batch_op:
                batch_op.create_unique_constraint(UQ_HASAT, ["company_id", "id"])

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

    with _YenidenKurulumKapisi(bind):
        inspector = sa.inspect(bind)
        if UQ_HASAT in _tekiller(inspector, HASAT):
            with op.batch_alter_table(HASAT) as batch_op:
                batch_op.drop_constraint(UQ_HASAT, type_="unique")
