"""Depo transferi SATIRI kiracıya bağlı — ve bağ veritabanında.

Konu: satır tablolarının kiracı sözleşmesi, dilim 3 (``stock_transfer_items``).

``stock_transfer_items`` bugüne kadar ``company_id`` taşımıyordu ve
``stock_transfers`` ile arasında yabancı anahtar yoktu. İzolasyon tamamen "her
sorgu ebeveyni filtrelemeyi hatırlasın" konvansiyonuna dayanıyordu. Tek bir
unutulmuş JOIN, B firmasının satırını A firmasının belgesine bağlardı ve bu
hiçbir yerde hata üretmezdi — sessizce yanlış veri olurdu.

Dört kapı, dördü de ayrı bir şeyi ölçüyor:

1. **Çapraz kiracı satır REDDEDİLİR.** Bileşik yabancı anahtar
   ``(company_id, transfer_id) -> stock_transfers(company_id, id)``. Tek
   sütunluk bir FK bunu yakalamazdı: ``transfer_id`` geçerli olurdu, sadece
   yanlış şirkete ait olurdu.

2. **``company_id``siz satır REDDEDİLİR.** NOT NULL. Sütunun var olması
   yetmez; opsiyonel bir tenant sütunu, unutulduğunda sessizce NULL kalır ve
   hiçbir filtreye takılmaz.

3. **Mevcut satırlar migration'dan DOĞRU şirketle çıkar.** Değer uydurulmuyor,
   ebeveynden türetiliyor. Boş tablo varsayılmıyor — bu testte veri VAR.

4. **SQLite yeniden kurulumu mevcut indeksleri ve birincil anahtarları
   TAŞIR.** 1. ve 2. maddeyi eklemek SQLite'ta tabloyu yeniden kurdurur.
   Yeniden kurulum sessizce bir indeksi düşürürse şema "çalışır" görünür ve
   kayıp yalnızca üretimde, tarama olarak fark edilir. Ölçülen taban:
   ``stock_transfers`` -> PK(id) + ix_stock_transfers_company_date +
   ix_stock_transfers_company_id; ``stock_transfer_items`` -> PK(id) +
   ix_stock_transfer_items_product + ix_stock_transfer_items_transfer_id.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _kos(script: str, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def run_transfer_tenancy_smoke(database_url: str) -> None:
    """Kapı 1, 2 ve 4: taze şema üzerinde kısıtlar ve indeksler."""
    _kos(_SMOKE, database_url)


def run_transfer_tenancy_migration(database_url: str) -> None:
    """Kapı 3 ve 4: VERİSİ OLAN bir 0053 şemasını yükselt."""
    _kos(_MIGRATION, database_url)


def test_transfer_item_tenancy_sqlite(tmp_path: Path) -> None:
    run_transfer_tenancy_smoke(f"sqlite:///{(tmp_path / 'transfer-tenancy.db').as_posix()}")


def test_transfer_item_migration_preserves_rows_sqlite(tmp_path: Path) -> None:
    run_transfer_tenancy_migration(f"sqlite:///{(tmp_path / 'transfer-migration.db').as_posix()}")


# --------------------------------------------------------------------------
# Ortak yardımcılar — iki script de aynı beklenen tabanı kullanır.
# --------------------------------------------------------------------------
_ORTAK = r'''
import os
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, inspect, text

import importlib.util as _iu

# Ortak yapısal iz. ADLARI değil SÜTUN DEMETLERİNİ karşılaştırır — bu dosyanın
# ilk sürümü adları karşılaştırıyordu ve aynı adla yeniden kurulan bir
# indeksin sütun SIRASI değişse fark etmezdi. Açık 2. dilimde kapatıldı ve
# bu tablo da geriye dönük aynı kapıya bağlandı.
_snap_spec = _iu.spec_from_file_location("snap", "tests/tenant_schema_snapshot.py")
snap = _iu.module_from_spec(_snap_spec)
_snap_spec.loader.exec_module(snap)

# BAĞIMSIZ ÇAPA, sütun demeti olarak (ad değil). 0055 var olmadan ÖNCE
# develop'tan ``inspect()`` ile sayıldı, yani test edilen kod üretemez.
TABAN_INDEKS = {
    "stock_transfers": {
        (("company_id", "transfer_date"), False),
        (("company_id",), False),
    },
    "stock_transfer_items": {
        (("product_id", "transfer_id"), False),
        (("transfer_id",), False),
    },
}


#: 0055'in BEYAN ETTİĞİ eklemeler. Yükseltme sonrası taban bunlarla genişler;
#: beyan edilmemiş bir indeks hâlâ hatadır. PostgreSQL'de UNIQUE kısıtı
#: arkasında indeks yaratır, SQLite'ta yaratmaz — ölçülen fark.
def _eklenenler(pg: bool):
    return {
        "stock_transfers": ({(("company_id", "id"), True)} if pg else set()),
        "stock_transfer_items": {(("company_id", "transfer_id"), False)},
    }


def indeksleri_dogrula(denetci, asama="yeniden kurulum", yukseltildi=False):
    """Kapı 4: ORTAK çapa mekanizması + birincil anahtar.

    Çapa ``snap.capa_dogrula``ya devredildi: aynı fikrin iki uygulaması
    olmasın. TABAN_INDEKS, 0055 var olmadan ÖNCE develop'tan ölçüldü.
    Karşılaştırma TAM EŞİTLİK: alt küme kontrolü, migration'ın yukarı çıkarken
    yarattığı FAZLADAN bir indeksi göremezdi (ölçüldü).
    """
    ekler = _eklenenler(motor.dialect.name == "postgresql") if yukseltildi else {}
    for tablo, beklenen in TABAN_INDEKS.items():
        snap.capa_dogrula(denetci, tablo, beklenen, asama, ekler.get(tablo))
        assert snap.yapisal_iz(denetci, tablo)["pk"] == ("id",), (
            f"{tablo}: birincil anahtar kayboldu/değişti")


PARENT_CHECK = "ck_stock_transfers_source_not_target"


def reddedilmeli(motor, fn, aciklama):
    """Her deneme KENDİ işleminde koşar.

    İkisi de daha önce ölçüldü: PostgreSQL'de başarısız bir statement işlemin
    tamamını iptal eder, ve SQLite yabancı anahtarları ``PRAGMA
    foreign_keys=ON`` olmadan ZORLAMAZ — pragma BAĞLANTI başınadır.
    """
    try:
        with motor.begin() as ayri:
            if motor.dialect.name == "sqlite":
                ayri.execute(text("PRAGMA foreign_keys=ON"))
            fn(ayri)
    except Exception:
        return
    raise AssertionError(f"REDDEDİLMELİYDİ: {aciklama}")


def checki_dogrula(motor, denetci, asama):
    """Adlı CHECK yeniden kurulumdan sağ çıktı MI ve HÂLÂ ısırıyor mu.

    Yalnız PostgreSQL: 0020 bu CHECK'i SQLite'ta bilinçli olarak kurmuyor
    (canlı transfer defterini yeniden kurmamak için). Varlığını doğrulamak
    yetmez — kısıt adı durup davranışı kaybolabilir, o yüzden ihlal eden bir
    satır da denenmeli.
    """
    if motor.dialect.name != "postgresql":
        return
    adlar = {c["name"] for c in denetci.get_check_constraints("stock_transfers")}
    assert PARENT_CHECK in adlar, (
        f"{asama}: yeniden kurulum adlı CHECK'i DÜŞÜRDÜ: {PARENT_CHECK} "
        f"(mevcut: {sorted(adlar)})"
    )
    reddedilmeli(motor, lambda c: c.execute(text(
        "INSERT INTO stock_transfers(company_id,transfer_date,source_warehouse_id,"
        "target_warehouse_id,status,created_at) "
        "VALUES(9301,'2026-08-11',7,7,'completed',:n)"),
        {"n": datetime(2026, 8, 11, tzinfo=timezone.utc)}),
        f"{asama}: kaynak=hedef transfer kabul edildi (CHECK artik isirmiyor)")


def kimlik_uretimi_dogrula(motor, asama, *, cocuk_icin=None):
    """Birincil anahtarlar HÂLÂ kendiliğinden üretiliyor mu.

    Yeniden kurulum sırasında dizi (sequence) bağı kopabilir ve tablo
    "çalışır" görünmeye devam eder — kayıp yalnız açık ``id`` vermeyen ilk
    gerçek INSERT'te ortaya çıkar. Bu yüzden burada ``id`` ASLA verilmiyor.

    Kayıp, ham bir ``NotNullViolation`` olarak da yüzeye çıkar; o hâliyle
    kırmızıdır ama hangi AŞAMADA ve NEDEN kırıldığını söylemez. Sürücü hatası
    burada yakalanıp aşama adıyla yeniden atılıyor: kazara kırılan bir kapı,
    kasten kırılan bir kapıdan daha kötü okunur.
    """
    with motor.begin() as c:
        if motor.dialect.name == "sqlite":
            c.execute(text("PRAGMA foreign_keys=ON"))
        try:
            ust = c.execute(text(
                "INSERT INTO stock_transfers(company_id,transfer_date,source_warehouse_id,"
                "target_warehouse_id,status,created_at) "
                "VALUES(9301,'2026-08-11',1,2,'completed',:n) RETURNING id"),
                {"n": datetime(2026, 8, 11, tzinfo=timezone.utc)}).scalar_one()
        except Exception as hata:
            raise AssertionError(
                f"{asama}: stock_transfers.id KENDILIGINDEN URETILMEDI — yeniden "
                f"kurulum dizi (sequence) bagini kopardi: {type(hata).__name__}: {hata}"
            ) from hata
        assert ust is not None, f"{asama}: stock_transfers.id üretilmedi"
        sutunlar = ("company_id,transfer_id,product_id,quantity" if cocuk_icin == "company_id"
                    else "transfer_id,product_id,quantity")
        degerler = "9301,:t,1,1" if cocuk_icin == "company_id" else ":t,1,1"
        try:
            satir = c.execute(text(
                f"INSERT INTO stock_transfer_items({sutunlar}) "
                f"VALUES({degerler}) RETURNING id"), {"t": ust}).scalar_one()
        except Exception as hata:
            raise AssertionError(
                f"{asama}: stock_transfer_items.id KENDILIGINDEN URETILMEDI — yeniden "
                f"kurulum dizi (sequence) bagini kopardi: {type(hata).__name__}: {hata}"
            ) from hata
        assert satir is not None, f"{asama}: stock_transfer_items.id üretilmedi"
        # Temizle: bu satırlar ölçüm içindi, kapı 3'ün saydığı veriye karışmasın.
        c.execute(text("DELETE FROM stock_transfer_items WHERE id=:i"), {"i": satir})
        c.execute(text("DELETE FROM stock_transfers WHERE id=:i"), {"i": ust})


def firma(baglanti, fid, ad):
    baglanti.execute(text(
        "INSERT INTO companies(id,name,is_active,negative_stock_policy,"
        "credit_limit_policy,farm_area_override_policy,farm_early_harvest_policy,"
        "farm_spraying_dose_required,created_at) VALUES(:i,:a,:aktif,'block','block',"
        "'require_reason','require_reason',:doz,:t)"),
        {"i": fid, "a": ad, "aktif": True, "doz": True, "t": datetime(2026, 8, 11, tzinfo=timezone.utc)})


def transfer(baglanti, fid, tid):
    baglanti.execute(text(
        "INSERT INTO stock_transfers(id,company_id,transfer_date,source_warehouse_id,"
        "target_warehouse_id,status,created_at) "
        "VALUES(:tid,:c,'2026-08-11',1,2,'completed',:n)"),
        {"tid": tid, "c": fid, "n": datetime(2026, 8, 11, tzinfo=timezone.utc)})
'''


_SMOKE = _ORTAK + r'''
import app.main  # noqa: F401  (import migration'ları koşturur)

motor = create_engine(os.environ["DATABASE_URL"])
denetci = inspect(motor)

# --- Şema iddiaları -------------------------------------------------------
sutunlar = {c["name"]: c for c in denetci.get_columns("stock_transfer_items")}
assert "company_id" in sutunlar, "stock_transfer_items company_id taşımıyor"
assert sutunlar["company_id"]["nullable"] is False, "company_id NULL kabul ediyor"

fk_adlari = {fk["name"] for fk in denetci.get_foreign_keys("stock_transfer_items")}
assert "fk_stock_transfer_items_transfer_same_company" in fk_adlari, fk_adlari

# Bileşik OLMASI şart: tek sütunluk bir FK yanlış şirketi yakalamaz.
bilesik = [
    fk for fk in denetci.get_foreign_keys("stock_transfer_items")
    if fk["name"] == "fk_stock_transfer_items_transfer_same_company"
][0]
assert set(bilesik["constrained_columns"]) == {"company_id", "transfer_id"}, bilesik
assert set(bilesik["referred_columns"]) == {"company_id", "id"}, bilesik

benzersiz = {u["name"] for u in denetci.get_unique_constraints("stock_transfers")}
assert "uq_stock_transfers_company_id" in benzersiz, benzersiz

indeksleri_dogrula(denetci, "taze şema", yukseltildi=True)   # KAPI 4 (taze şema)

# --- Davranış kapıları ----------------------------------------------------
with motor.begin() as baglanti:
    firma(baglanti, 9101, "Transfer A")
    firma(baglanti, 9102, "Transfer B")
    transfer(baglanti, 9101, 71001)   # A firmasının belgesi

def satir(baglanti, cid, tid):
    baglanti.execute(text(
        "INSERT INTO stock_transfer_items(company_id,transfer_id,product_id,quantity) "
        "VALUES(:c,:t,1,5)"), {"c": cid, "t": tid})

# KAPI 1: B firması, A firmasının belgesine satır yazamaz.
reddedilmeli(motor, lambda c: satir(c, 9102, 71001),
             "B firmasinin satiri A firmasinin transferine baglandi")

# KAPI 2: company_id'siz satır reddedilir.
reddedilmeli(motor, lambda c: c.execute(text(
    "INSERT INTO stock_transfer_items(transfer_id,product_id,quantity) "
    "VALUES(71001,1,5)")),
    "company_id'siz satir kabul edildi")

# Kendi belgesine yazmak ÇALIŞMALI — kapı geçerli işi engellemiyor.
with motor.begin() as baglanti:
    if motor.dialect.name == "sqlite":
        baglanti.execute(text("PRAGMA foreign_keys=ON"))
    satir(baglanti, 9101, 71001)

print("SMOKE OK")
'''


_MIGRATION = _ORTAK + r'''
# DEĞİŞİKLİK ÖNCESİ ŞEKİL ELLE YAZILMIYOR — GERÇEĞİNDEN TÜRETİLİYOR.
#
# Elle yazılmış bir "eski şema" ilk denemede tam olarak bu testi vakuma
# düşürmüştü: PostgreSQL'de gerçek ebeveyn ``ck_stock_transfers_source_not_target``
# taşır, iki birincil anahtar da DİZİ (sequence) destekli, ``created_at``
# ``timestamp with time zone``. Elle kurulan şekilde bunların HİÇBİRİ yoktu ve
# her INSERT açık ``id`` veriyordu — yani "CHECK yeniden kurulumdan sağ çıktı"
# iddiası kanıtlanmamış, kimlik üretiminin kaybı ise ALGILANAMAZ durumdaydı.
#
# Bunun yerine: gerçek şema migration zinciriyle kuruluyor, sonra BU
# migration'ın downgrade'i ile değişiklik öncesi şekle indiriliyor. Böylece
# fixture'ın her özelliği (dizi, CHECK, tip, nullability, indeks) gerçeğin
# kendisi oluyor.
#
# Fixture'ın kendisi de DOĞRULANIYOR: downgrade sessizce CHECK'i veya diziyi
# düşürseydi, sonraki "sağ çıktı" iddiaları yine vakuma düşerdi. O yüzden
# yükseltmeden ÖNCE eski şeklin gerçekten o özellikleri taşıdığı iddia ediliyor.
import importlib.util

from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.main  # noqa: F401  (gerçek şemayı migration zinciriyle kurar)

motor = create_engine(os.environ["DATABASE_URL"])
pg = motor.dialect.name == "postgresql"

_spec = importlib.util.spec_from_file_location(
    "gecis", "alembic/versions/20260811_0055_stock_transfer_items_tenant.py")
gecis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gecis)


def gecisi_kostur(asama):
    with motor.begin() as baglanti:
        onceki_op = gecis.op
        gecis.op = Operations(MigrationContext.configure(baglanti))
        try:
            getattr(gecis, asama)()
        finally:
            gecis.op = onceki_op


# --- Gerçekten türetilmiş değişiklik öncesi şekil -------------------------
gecisi_kostur("downgrade")

denetci = inspect(motor)
sutunlar = {c["name"] for c in denetci.get_columns("stock_transfer_items")}
assert "company_id" not in sutunlar, (
    "türetilen eski şekil hâlâ company_id taşıyor; test yükseltmeyi ölçemez"
)
# FIXTURE DOĞRULAMASI: aşağıdakiler eski şekilde GERÇEKTEN varsa, sonraki
# "sağ çıktı" iddiaları anlam taşır. Yoksa test vakuma düşerdi — o yüzden
# burada yüksek sesle patlıyor.
indeksleri_dogrula(denetci)
checki_dogrula(motor, denetci, "değişiklik öncesi")
kimlik_uretimi_dogrula(motor, "değişiklik öncesi")
if pg:
    with motor.connect() as baglanti:
        tip = baglanti.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='stock_transfers' AND column_name='created_at'"
        )).scalar_one()
    assert tip == "timestamp with time zone", tip

# --- Veri: açık id VERİLMİYOR, kimlikler üretiliyor -----------------------
# PostgreSQL işinde şema dosya BAŞINA sıfırlanıyor, test başına değil; aynı
# dosyadaki taze-şema testinin satırları burada duruyor olur. Kapı 3 "MEVCUT
# satırlar doğru şirketle çıkıyor mu" sorusu ve cevabın ölçülebilmesi için
# hangi satırların ölçüldüğü belirli olmalı. Boş tablo VARSAYILMIYOR — tam
# tersine, hemen aşağıda üç satır yazılıyor ve migration onların üzerinde
# koşuyor; burada yalnız ölçümün başlangıç noktası belirli hâle getiriliyor.
with motor.begin() as baglanti:
    baglanti.execute(text("DELETE FROM stock_transfer_items"))
    baglanti.execute(text("DELETE FROM stock_transfers"))

with motor.begin() as baglanti:
    ust = {}
    for etiket, fid, kaynak, hedef in (("A", 9201, 1, 2), ("B", 9202, 3, 4)):
        ust[etiket] = baglanti.execute(text(
            "INSERT INTO stock_transfers(company_id,transfer_date,source_warehouse_id,"
            "target_warehouse_id,status,created_at) "
            "VALUES(:c,'2026-08-11',:s,:h,'completed',:n) RETURNING id"),
            {"c": fid, "s": kaynak, "h": hedef,
             "n": datetime(2026, 8, 11, tzinfo=timezone.utc)}).scalar_one()
    # A'nın belgesine iki satır, B'nin belgesine bir satır. Boş tablo YOK.
    for etiket, urun in (("A", 11), ("A", 12), ("B", 21)):
        baglanti.execute(text(
            "INSERT INTO stock_transfer_items(transfer_id,product_id,quantity) "
            "VALUES(:t,:p,3)"), {"t": ust[etiket], "p": urun})

beklenen_satirlar = [
    (11, 9201, ust["A"]), (12, 9201, ust["A"]), (21, 9202, ust["B"]),
]

# --- Yükselt --------------------------------------------------------------
gecisi_kostur("upgrade")
denetci = inspect(motor)

# KAPI 3: mevcut satırlar DOĞRU şirketle sağ çıktı.
with motor.connect() as baglanti:
    satirlar = baglanti.execute(text(
        "SELECT product_id, company_id, transfer_id FROM stock_transfer_items "
        "ORDER BY product_id")).all()
assert satirlar == beklenen_satirlar, (satirlar, beklenen_satirlar)

# KAPI 4: indeksler, birincil anahtarlar, adlı CHECK ve kimlik üretimi.
indeksleri_dogrula(denetci, "yükseltme sonrası", yukseltildi=True)
checki_dogrula(motor, denetci, "yükseltme sonrası")
kimlik_uretimi_dogrula(motor, "yükseltme sonrası", cocuk_icin="company_id")

# Yükseltilmiş şema kısıtları gerçekten uyguluyor.
reddedilmeli(motor, lambda c: c.execute(text(
    "INSERT INTO stock_transfer_items(company_id,transfer_id,product_id,quantity) "
    "VALUES(:c,:t,1,5)"), {"c": 9202, "t": ust["A"]}),
    "yukseltilmis semada capraz kiraci satir kabul edildi")
reddedilmeli(motor, lambda c: c.execute(text(
    "INSERT INTO stock_transfer_items(transfer_id,product_id,quantity) "
    "VALUES(:t,1,5)"), {"t": ust["A"]}),
    "yukseltilmis semada company_id'siz satir kabul edildi")

# --- Geri alma da yeniden kurulum yapar; o da hiçbir şey düşürmemeli -------
gecisi_kostur("downgrade")
denetci = inspect(motor)
sutunlar = {c["name"] for c in denetci.get_columns("stock_transfer_items")}
assert "company_id" not in sutunlar, "downgrade company_id'yi bırakmış"
assert not denetci.get_unique_constraints("stock_transfers"), \
    "downgrade ebeveyn UNIQUE'ini bırakmış"
indeksleri_dogrula(denetci)
checki_dogrula(motor, denetci, "geri alma sonrası")
kimlik_uretimi_dogrula(motor, "geri alma sonrası")
with motor.connect() as baglanti:
    kalan = baglanti.execute(text(
        "SELECT product_id,transfer_id FROM stock_transfer_items ORDER BY product_id")).all()
assert kalan == [(11, ust["A"]), (12, ust["A"]), (21, ust["B"])], kalan

# Şemayı head'e geri getir: dosya içindeki sıradan bağımsız tutarlı kalsın.
gecisi_kostur("upgrade")

print("MIGRATION OK")
'''
