"""Tarla Yönetimi V1 — FAZ 1 sözleşmesi: veri modeli, kiracı izolasyonu, RBAC.

Konu: mobil-erp#2. Faz 1'in kapısı "şema doğru mu" değil, **şema yanlış
kullanımı ENGELLİYOR mu**. Bu yüzden testlerin çoğu bir şeyi yapmayı DENER ve
reddedilmesini bekler.

Buradaki en kritik iddia şu: farklı firmaların kayıtları birbirine
BAĞLANAMAZ — ve bu, uygulama katmanının iyi niyetine değil, veritabanı
kısıtlarına dayanır. Uygulama katmanı kontrolü unutulabilir; bileşik yabancı
anahtar unutulamaz.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_farm_permissions_match_the_locked_authorisation_model() -> None:
    """mobil-erp#2'deki yetki tablosu birebir uygulanmış olmalı.

    Veritabanı istemiyor ama değeri yüksek: yetki tablosu üründe "kim ne
    görebilir" sorusunun tek kaynağı ve sessizce genişlemesi en kolay yer.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS

    farm = {rol: {i for i in izin if i.startswith("farm.")} for rol, izin in ROLE_PERMISSIONS.items()}

    assert farm["yonetici"] == {"farm.view", "farm.manage", "farm.inputs"}
    # Depo girdi bağlar ama çiftlik/parsel/sezon YÖNETEMEZ.
    assert farm["depo"] == {"farm.view", "farm.inputs"}
    # Muhasebe/satış/rapor YALNIZ okur.
    assert farm["muhasebe"] == {"farm.view"}
    assert farm["satis"] == {"farm.view"}
    assert farm["rapor"] == {"farm.view"}
    # admin joker taşır; ayrıca farm.* verilmemeli (çift kaynak olmasın).
    assert ROLE_PERMISSIONS["admin"] == {"*"}
    assert farm["admin"] == set()

    # Hiçbir role yazma izni KAZARA sızmamış olmalı.
    yazabilen = {rol for rol, izin in farm.items() if "farm.manage" in izin}
    assert yazabilen == {"yonetici"}, f"farm.manage beklenmedik rollerde: {yazabilen}"


def run_farm_foundation_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_farm_foundation_sqlite(tmp_path: Path) -> None:
    run_farm_foundation_smoke(f"sqlite:///{(tmp_path / 'farm.db').as_posix()}")


_SMOKE = r'''
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import engine
import app.main  # migration'lari calistirir

SIMDI = datetime.now(timezone.utc)


def firma_ac(conn, ad):
    var = conn.execute(text("SELECT id FROM companies WHERE name=:n"), {"n": ad}).scalar()
    if var:
        return int(var)
    return int(conn.execute(text(
        "INSERT INTO companies(name,is_active,created_at) VALUES(:n,TRUE,:t) RETURNING id"),
        {"n": ad, "t": SIMDI}).scalar())


def ciftlik_ac(conn, cid, kod):
    return int(conn.execute(text(
        """INSERT INTO farms(company_id,code,name,status,created_at,updated_at)
        VALUES(:c,:k,:k,'ACTIVE',:t,:t) RETURNING id"""),
        {"c": cid, "k": kod, "t": SIMDI}).scalar())


def reddedilmeli(sql, params, mesaj):
    """Beklenen ihlali KENDI isleminde dener.

    PostgreSQL'de basarisiz bir ifade tum islemi iptal eder; ayni blokta
    devam etmek "current transaction is aborted" verir ve daha kotusu, o ana
    kadar yazilanlar da geri alinir. SQLite bunu affediyordu — bu yuzden
    SQLite'ta gecen bir test PG'de duserdi. Her ihlal denemesi ayri islem.
    """
    try:
        with engine.begin() as c2:
            if engine.dialect.name == "sqlite":
                c2.execute(text("PRAGMA foreign_keys=ON"))
            c2.execute(text(sql), params)
    except IntegrityError:
        return
    raise AssertionError(mesaj)


def parsel_ac(conn, cid, ciftlik_id, kod, alan="12.5000"):
    return int(conn.execute(text(
        """INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,status,created_at,updated_at)
        VALUES(:c,:f,:k,:k,:a,'ACTIVE',:t,:t) RETURNING id"""),
        {"c": cid, "f": ciftlik_id, "k": kod, "a": alan, "t": SIMDI}).scalar())


with engine.begin() as conn:
    # SQLite'ta yabanci anahtarlar VARSAYILAN OLARAK KAPALI; acmadan test
    # capraz-kiraci korumasini sinamis olmaz, bosuna gecerdi.
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA foreign_keys=ON"))
    a = firma_ac(conn, "Tarla A")
    b = firma_ac(conn, "Tarla B")

    ca = ciftlik_ac(conn, a, "MERKEZ")
    cb = ciftlik_ac(conn, b, "MERKEZ")   # AYNI kod, farkli firma

    # --- 1) Tekillik KİRACI KAPSAMINDA -------------------------------------
    # Iki firma ayni ciftlik kodunu kullanabilmeli; yukaridaki iki INSERT de
    # gectiyse kural dogru.
    assert ca != cb

ayni_firma = a

# Ayni firmada ayni kod REDDEDILMELI.
reddedilmeli(
    """INSERT INTO farms(company_id,code,name,status,created_at,updated_at)
    VALUES(:c,'MERKEZ','MERKEZ','ACTIVE',:t,:t)""",
    {"c": ayni_firma, "t": SIMDI},
    "ayni firmada tekrar eden ciftlik kodu kabul edildi")

with engine.begin() as conn:
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA foreign_keys=ON"))
    a = firma_ac(conn, "Tarla A"); b = firma_ac(conn, "Tarla B")
    ca = int(conn.execute(text("SELECT id FROM farms WHERE company_id=:c"), {"c": a}).scalar())
    cb = int(conn.execute(text("SELECT id FROM farms WHERE company_id=:c"), {"c": b}).scalar())

    # --- 2) ÇAPRAZ KİRACI BAĞ VERİTABANI SEVİYESİNDE İMKÂNSIZ --------------
    # Bu testin varlik sebebi: uygulama katmanindaki kontrol unutulabilir.
    # A firmasinin parselini B firmasinin ciftligine baglamaya calisiyoruz.
    pass

reddedilmeli(
    """INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,status,created_at,updated_at)
    VALUES(:c,:f,'CAPRAZ','Capraz','5','ACTIVE',:t,:t)""",
    {"c": a, "f": cb, "t": SIMDI},
    "BASKA firmanin ciftligine parsel baglanabildi")

with engine.begin() as conn:
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA foreign_keys=ON"))
    a = firma_ac(conn, "Tarla A")
    ca = int(conn.execute(text("SELECT id FROM farms WHERE company_id=:c"), {"c": a}).scalar())
    pa = parsel_ac(conn, a, ca, "P-1")

    # --- 3) ALAN SIFIR/NEGATİF OLAMAZ --------------------------------------
    pass

for gecersiz in ("0", "-3.5"):
    reddedilmeli(
        """INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,status,created_at,updated_at)
        VALUES(:c,:f,:k,:k,:a,'ACTIVE',:t,:t)""",
        {"c": a, "f": ca, "k": "P-BAD-" + gecersiz.replace("-", "M").replace(".", ""),
         "a": gecersiz, "t": SIMDI},
        f"alan={gecersiz} kabul edildi")

with engine.begin() as conn:
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA foreign_keys=ON"))
    a = firma_ac(conn, "Tarla A")
    pa = int(conn.execute(text("SELECT id FROM farm_parcels WHERE company_id=:c AND code='P-1'"), {"c": a}).scalar())
    sezon = int(conn.execute(text(
        """INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,status,started_on,created_at,updated_at)
        VALUES(:c,:p,2026,'Bugday','ACTIVE','2026-03-01',:t,:t) RETURNING id"""),
        {"c": a, "p": pa, "t": SIMDI}).scalar())

    pass

# --- 4) SEZON BİTİŞİ BAŞLANGIÇTAN ÖNCE OLAMAZ ------------------------------
reddedilmeli(
    """INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,status,started_on,ended_on,created_at,updated_at)
    VALUES(:c,:p,2026,'Arpa','ACTIVE','2026-06-01','2026-03-01',:t,:t)""",
    {"c": a, "p": pa, "t": SIMDI},
    "bitis tarihi baslangictan once kabul edildi")

# --- 5) İLAÇLAMA GÜVENLİK ARALIĞI NEGATİF OLAMAZ ---------------------------
# Hasat bekleme suresi YASAL bir kisit; yanlis kayit gercek risk.
reddedilmeli(
    """INSERT INTO field_activities(company_id,season_id,activity_type,performed_at,
    preharvest_interval_days,status,created_at,updated_at)
    VALUES(:c,:s,'SPRAYING',:t,-1,'RECORDED',:t,:t)""",
    {"c": a, "s": sezon, "t": SIMDI},
    "negatif hasat bekleme suresi kabul edildi")

# --- 6) NEM YÜZDESİ 0–100 ARALIĞINDA ---------------------------------------
for nem in ("-1", "101"):
    reddedilmeli(
        """INSERT INTO field_harvests(company_id,season_id,harvested_on,quantity,unit,
        moisture_percent,status,created_at,updated_at)
        VALUES(:c,:s,'2026-08-15','1000','KG',:n,'RECORDED',:t,:t)""",
        {"c": a, "s": sezon, "n": nem, "t": SIMDI},
        f"nem={nem} kabul edildi")

with engine.begin() as conn:
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA foreign_keys=ON"))
    a = firma_ac(conn, "Tarla A")
    # --- 7) ENTEGRASYON OLAYI TEKRAR YAZILAMAZ -----------------------------
    # FAZ 4'te tuketici yazildiginda ayni olayin iki fis uretmemesini saglayan
    # kisit; simdiden sinaniyor cunku sonradan eklemek gecmis veriyle catisir.
    conn.execute(text(
        """INSERT INTO field_integration_events(company_id,source_type,source_id,target,
        idempotency_key,status,attempts,created_at,updated_at)
        VALUES(:c,'field_activity',1,'stock','ANAHTAR-1','PENDING',0,:t,:t)"""),
        {"c": a, "t": SIMDI})
    pass

reddedilmeli(
    """INSERT INTO field_integration_events(company_id,source_type,source_id,target,
    idempotency_key,status,attempts,created_at,updated_at)
    VALUES(:c,'field_activity',1,'stock','ANAHTAR-1','PENDING',0,:t,:t)""",
    {"c": a, "t": SIMDI},
    "ayni idempotency anahtari ikinci kez yazildi")

with engine.begin() as conn:
    a = firma_ac(conn, "Tarla A")
    b = firma_ac(conn, "Tarla B")
    # Ayni anahtar BASKA firmada serbest olmali (tekillik kiraci kapsaminda).
    conn.execute(text(
        """INSERT INTO field_integration_events(company_id,source_type,source_id,target,
        idempotency_key,status,attempts,created_at,updated_at)
        VALUES(:c,'field_activity',1,'stock','ANAHTAR-1','PENDING',0,:t,:t)"""),
        {"c": b, "t": SIMDI})

with engine.connect() as conn:
    # --- 8) ALAN DEĞERİ KAYMADAN SAKLANIR ----------------------------------
    # Dekar dogrudan verim ve maliyet bolmelerine giriyor; sapma dekar basina
    # maliyeti kaydirir.
    #
    # TİP BEKLENTİSİ DİYALEKTE GÖRE DEĞİŞİR ve bu bilincli:
    # PostgreSQL (URETIM) NUMERIC'i Decimal olarak dondurur; SQLite REAL olarak
    # saklar ve float dondurur. Deponun kurali zaten "girdiler Decimal(str(...))
    # ile donusturulur" (docs/financial-rounding-policy.md) — yani uygulama
    # sinirda donusturur. Bu yuzden SQLite'ta DEGERIN kaymadigi, PostgreSQL'de
    # ayrica TIPIN Decimal oldugu sinaniyor.
    a = conn.execute(text("SELECT id FROM companies WHERE name='Tarla A'")).scalar()
    alan = conn.execute(text(
        "SELECT area_decare FROM farm_parcels WHERE company_id=:c AND code='P-1'"), {"c": a}).scalar()
    assert Decimal(str(alan)) == Decimal("12.5"), f"alan kaymis: {alan!r}"
    if engine.dialect.name == "postgresql":
        assert isinstance(alan, Decimal), f"PG'de alan Decimal degil: {type(alan)}"

print("TARLA FAZ-1 SOZLESMESI TAMAM")
'''
