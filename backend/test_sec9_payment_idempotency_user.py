"""SEC-9: `payment_idempotency` KULLANICI kapsamı (göç 20260914_0084).

Kapının konusu TEK cümlede: bir firmadaki iki ayrı kullanıcının AYNI
`Idempotency-Key`i, birbirinin defter satırına DOKUNAMAZ.

--- 28c9dd0 ÜZERİNDE ÖLÇÜLEN (KIRMIZI) ------------------------------------

`_claim_payment_create` defteri SABİT `resource_id='create'` ile yazıyor, yani
firmadaki TÜM kullanıcılar TEK anahtar uzayını paylaşıyordu. Ölçüm (SQLite,
`PAYMENT_ALLOCATION_ENGINE_ENABLED=true`):

  * ayrı gövde, aynı anahtar -> B kullanıcısı HTTP 409 "Idempotency anahtarı
    farklı bir ödeme için kullanılmış" aldı; MEŞRU ödemesi hiç yazılmadı.
  * aynı gövde, aynı anahtar -> B kullanıcısı A'nın `result_snapshot`ını aldı
    (A'nın `payments.id`si). DÖRT kullanıcı isteğine karşılık `payments`
    tablosunda İKİ satır kaldı.

Göç + motor yamasından sonra AYNI ölçüm: dört istek -> DÖRT satır, B her
seferinde KENDİ ödemesini alıyor.

--- BU DOSYA NEYİ PİNLİYOR -------------------------------------------------

1. Çapraz kullanıcı — yukarıdaki iki ölçümün YEŞİL hâli.
2. Aynı kullanıcı + aynı anahtar + aynı gövde — idempotens BOZULMADI, hâlâ
   TEKRAR OYNATIYOR (yeni satır yazmıyor).
3. Aynı kullanıcı + aynı anahtar + farklı gövde — hâlâ 409. Bu davranış SEC-9
   ÖNCESİ ölçüldü ve AYNEN korunuyor; kapı onu METNİYLE çiviliyor ki kapsam
   eklenirken sessizce gevşemesin.
4. Şema — sütun, NOT NULL'lığı, YENİ tekillik, ESKİ tekilliğin YOKLUĞU ve
   0025'in indeksinin HÂLÂ DURUYOR olması.
5. Göçün up->down->up turu ve geri doldurmanın 0 nöbetçisi.

PostgreSQL ikizi: `backend/test_sec9_payment_idempotency_postgresql.py`
(`pins/pg_twins.txt` 125 -> 126). İkiz bu dosyanın koşucularını İTHAL EDER —
`test_payment_allocation_foundation_postgresql.py`nin kalıbı; iki dosya da bu
yüzden `backend/tests/` altında değil `backend/` kökünde durur.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent

#: Parmak izi uyuşmazlığında motorun verdiği metin. SEC-9 ÖNCESİ ölçüldü ve
#: DEĞİŞMEDİ; kaynak: `payment_allocation_engine._claim_payment_create`.
PARMAK_IZI_MESAJI = "Idempotency anahtarı farklı bir ödeme için kullanılmış"


def run_sec9_smoke(database_url: str) -> None:
    """Motor yolunu GERÇEK uygulama açılışıyla koşar (ikiz bunu paylaşır)."""

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
    env["PAYMENT_ALLOCATION_CLOSED_THROUGH"] = "2026-07-15"
    env["SEC9_PARMAK_IZI_MESAJI"] = PARMAK_IZI_MESAJI
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SEC9_OK" in completed.stdout, completed.stdout + completed.stderr


def run_sec9_goc_turu(database_url: str) -> None:
    """0083 -> 0084 -> 0083 -> 0084; şema ve geri doldurma ölçülür."""

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _GOC],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SEC9_GOC_OK" in completed.stdout, completed.stdout + completed.stderr


def test_sec9_payment_idempotency_user_sqlite(tmp_path: Path) -> None:
    run_sec9_smoke(f"sqlite:///{(tmp_path / 'sec9-kullanici.db').as_posix()}")


def test_sec9_goc_up_down_up_sqlite(tmp_path: Path) -> None:
    run_sec9_goc_turu(f"sqlite:///{(tmp_path / 'sec9-goc.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal
import os
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import SessionLocal, engine
from app.payment_allocation_engine import create_payment_with_allocation
from app.main import app

TABLO = "payment_idempotency"
ESKI_TEKIL = "uq_payment_idempotency_operation"
YENI_TEKIL = "uq_payment_idempotency_kullanici_islem"
PARMAK_IZI_MESAJI = os.environ["SEC9_PARMAK_IZI_MESAJI"]

# Ikiz PAYLASILAN veritabaninda kosabilir: anahtarlar ve kullanici adlari
# KOSUYA OZEL, yoksa ikinci kosu kendi defterini tekrar oynatir.
EK = uuid.uuid4().hex[:8]


def dec(value):
    return Decimal(str(value))


def kullanici_ac(db, cid, ad):
    uid = int(db.execute(
        text(
            """INSERT INTO app_users(
                username,email,display_name,password_hash,role,is_active,
                created_at,must_change_password,email_verified
            ) VALUES(
                :u,:e,:d,'x','user',TRUE,CURRENT_TIMESTAMP,FALSE,TRUE
            ) RETURNING id"""
        ),
        {"u": ad, "e": ad + "@ornek.test", "d": ad},
    ).scalar_one())
    db.execute(
        text(
            """INSERT INTO user_company_memberships(
                user_id,company_id,is_default,created_at
            ) VALUES(:uid,:cid,FALSE,CURRENT_TIMESTAMP)"""
        ),
        {"uid": uid, "cid": cid},
    )
    return uid


def musteri_ac(db, cid, ad, acik="2000"):
    mid = int(db.execute(
        text("INSERT INTO customers(name,company_id) VALUES(:ad,:cid) RETURNING id"),
        {"ad": ad, "cid": cid},
    ).scalar_one())
    db.execute(
        text(
            """INSERT INTO orders(
                customer_id,order_date,due_date,final_total,status,paid_amount,
                payment_term,payment_method,company_id
            ) VALUES(
                :m,'2026-01-01','2026-06-01',:t,'completed',0,
                'HARMAN_VADELI','credit',:cid
            )"""
        ),
        {"m": mid, "t": acik, "cid": cid},
    )
    return mid


def govde(musteri_id, tutar):
    return {
        "entity_type": "customer",
        "entity_id": musteri_id,
        "amount": dec(tutar),
        "payment_date": "2026-07-01",
        "note": None,
        "payment_method": "cash",
        "account_id": None,
        "reference_type": None,
        "reference_id": None,
    }


def defter_sayisi(db, anahtar):
    return int(db.execute(
        text(
            "SELECT COUNT(*) FROM " + TABLO
            + " WHERE idempotency_key=:k AND operation_type='create_payment'"
        ),
        {"k": anahtar},
    ).scalar_one())


with TestClient(app):
    # ------------------------------------------------------------- 4. SEMA --
    inspector = inspect(engine)
    sutunlar = {c["name"]: c for c in inspector.get_columns(TABLO)}
    assert "user_id" in sutunlar, sorted(sutunlar)
    assert sutunlar["user_id"]["nullable"] is False, sutunlar["user_id"]

    tekiller = {
        u["name"]: tuple(u["column_names"])
        for u in inspector.get_unique_constraints(TABLO)
    }
    assert YENI_TEKIL in tekiller, tekiller
    assert tekiller[YENI_TEKIL] == (
        "company_id", "user_id", "operation_type",
        "resource_type", "resource_id", "idempotency_key",
    ), tekiller[YENI_TEKIL]
    # ESKI tekillik DUSMUS olmali: birakilsaydi acik AYNEN kalirdi.
    assert ESKI_TEKIL not in tekiller, tekiller

    # 0025'in indeksi SQLite yeniden kurmasinda KAYBOLMAMALI (olculdu: goc
    # `copy_from`a indeksi ilan etmezse indeks sessizce dusuyor).
    indeksler = {x["name"] for x in inspector.get_indexes(TABLO)}
    assert "ix_payment_idempotency_company_resource" in indeksler, indeksler

    with SessionLocal() as db:
        cid = int(db.execute(
            text("SELECT id FROM companies ORDER BY id LIMIT 1")
        ).scalar_one())
        uid_a = kullanici_ac(db, cid, "sec9_a_" + EK)
        uid_b = kullanici_ac(db, cid, "sec9_b_" + EK)
        assert uid_a != uid_b, (uid_a, uid_b)
        must_a = musteri_ac(db, cid, "SEC9 A " + EK)
        must_b = musteri_ac(db, cid, "SEC9 B " + EK)
        db.commit()

    # -------------------------------------------- 1. CAPRAZ KULLANICI (a) --
    # AYNI anahtar, FARKLI govde. 28c9dd0'da B 409 aliyor ve ODEMESI YAZILMIYOR.
    K1 = "sec9-k1-" + EK
    with SessionLocal() as db:
        a1 = create_payment_with_allocation(
            db, cid, govde(must_a, "100"), K1, created_by=uid_a
        )
    with SessionLocal() as db:
        b1 = create_payment_with_allocation(
            db, cid, govde(must_b, "250"), K1, created_by=uid_b
        )
    assert b1["id"] != a1["id"], (a1["id"], b1["id"])
    assert int(b1["entity_id"]) == must_b, b1
    assert dec(b1["amount"]) == dec("250"), b1

    # -------------------------------------------- 1. CAPRAZ KULLANICI (b) --
    # AYNI anahtar, AYNI govde. 28c9dd0'da B, A'nin snapshot'ini ALIYOR.
    K2 = "sec9-k2-" + EK
    with SessionLocal() as db:
        a2 = create_payment_with_allocation(
            db, cid, govde(must_a, "70"), K2, created_by=uid_a
        )
    with SessionLocal() as db:
        b2 = create_payment_with_allocation(
            db, cid, govde(must_a, "70"), K2, created_by=uid_b
        )
    assert b2["id"] != a2["id"], (a2["id"], b2["id"])

    # Iki kullanici AYNI anahtar icin AYRI defter satiri tasiyor.
    with SessionLocal() as db:
        assert defter_sayisi(db, K1) == 2, defter_sayisi(db, K1)
        assert defter_sayisi(db, K2) == 2, defter_sayisi(db, K2)
        sahipler = sorted(
            int(r) for r in db.execute(
                text(
                    "SELECT user_id FROM " + TABLO
                    + " WHERE idempotency_key=:k"
                    + " AND operation_type='create_payment'"
                ),
                {"k": K2},
            ).scalars()
        )
        assert sahipler == sorted([uid_a, uid_b]), sahipler

    # ------------------------------- 2. AYNI KULLANICI: TEKRAR OYNATMA ------
    K3 = "sec9-k3-" + EK
    with SessionLocal() as db:
        ilk = create_payment_with_allocation(
            db, cid, govde(must_a, "40"), K3, created_by=uid_a
        )
    with SessionLocal() as db:
        tekrar = create_payment_with_allocation(
            db, cid, govde(must_a, "40"), K3, created_by=uid_a
        )
    assert tekrar["id"] == ilk["id"], (ilk["id"], tekrar["id"])
    with SessionLocal() as db:
        assert defter_sayisi(db, K3) == 1, defter_sayisi(db, K3)

    # ------------------------ 3. AYNI KULLANICI: PARMAK IZI UYUSMAZLIGI -----
    hata = None
    with SessionLocal() as db:
        try:
            create_payment_with_allocation(
                db, cid, govde(must_a, "41"), K3, created_by=uid_a
            )
        except HTTPException as exc:
            hata = exc
    assert hata is not None, "ayni kullanici + ayni anahtar + farkli govde GECTI"
    assert hata.status_code == 409, hata.status_code
    assert hata.detail == PARMAK_IZI_MESAJI, hata.detail
    with SessionLocal() as db:
        assert defter_sayisi(db, K3) == 1, defter_sayisi(db, K3)

print("SEC9_OK")
'''


_GOC = r'''
import os

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

TABLO = "payment_idempotency"
ONCE = "20260913_0083"
SONRA = "20260914_0084"
ESKI_TEKIL = "uq_payment_idempotency_operation"
YENI_TEKIL = "uq_payment_idempotency_kullanici_islem"
INDEKS = "ix_payment_idempotency_company_resource"

url = os.environ["DATABASE_URL"]
cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", url)
motor = sa.create_engine(url)


def sema():
    i = sa.inspect(motor)
    return {
        "sutunlar": {c["name"]: c["nullable"] for c in i.get_columns(TABLO)},
        "tekiller": {
            u["name"]: tuple(u["column_names"])
            for u in i.get_unique_constraints(TABLO)
        },
        "indeksler": {x["name"] for x in i.get_indexes(TABLO)},
        "checkler": {c["name"] for c in i.get_check_constraints(TABLO)},
        "fkler": sorted(
            (tuple(f["constrained_columns"]), f["referred_table"])
            for f in i.get_foreign_keys(TABLO)
        ),
    }


def firma_ac(baglanti):
    kimlik = baglanti.execute(
        sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")
    ).scalar()
    if kimlik is not None:
        return kimlik
    var = {c["name"]: c for c in sa.inspect(motor).get_columns("companies")}
    alan = {"name": "SEC9 Firma"}
    ornek = {
        "slug": "sec9",
        "created_at": "2026-01-01 00:00:00",
        "is_active": True,
        "tax_number": "1111111111",
    }
    for ad, deger in ornek.items():
        if ad in var and not var[ad]["nullable"] and var[ad].get("default") is None:
            alan[ad] = deger
    kolon = ",".join(alan)
    yer = ",".join(":" + k for k in alan)
    return baglanti.execute(
        sa.text(
            "INSERT INTO companies(" + kolon + ") VALUES(" + yer + ") RETURNING id"
        ),
        alan,
    ).scalar_one()


command.upgrade(cfg, ONCE)

once = sema()
assert "user_id" not in once["sutunlar"], once["sutunlar"]
assert ESKI_TEKIL in once["tekiller"], once["tekiller"]
assert INDEKS in once["indeksler"], once["indeksler"]

# GOC ONCESI iki satir: geri doldurma NOBETCIYI yazmali.
ESKI_ANAHTARLAR = ("sec9-eski-a", "sec9-eski-b")
with motor.begin() as baglanti:
    cid = firma_ac(baglanti)
    baglanti.execute(
        sa.text(
            "DELETE FROM " + TABLO + " WHERE idempotency_key IN (:a,:b)"
        ),
        {"a": ESKI_ANAHTARLAR[0], "b": ESKI_ANAHTARLAR[1]},
    )
    for anahtar in ESKI_ANAHTARLAR:
        baglanti.execute(
            sa.text(
                "INSERT INTO " + TABLO + "("
                "company_id,operation_type,resource_type,resource_id,"
                "idempotency_key,status,request_fingerprint,created_at"
                ") VALUES("
                ":cid,'create_payment','payment','create',"
                ":k,'completed','fp',CURRENT_TIMESTAMP)"
            ),
            {"cid": cid, "k": anahtar},
        )

command.upgrade(cfg, SONRA)

sonra = sema()
assert sonra["sutunlar"].get("user_id") is False, sonra["sutunlar"]
assert YENI_TEKIL in sonra["tekiller"], sonra["tekiller"]
assert sonra["tekiller"][YENI_TEKIL] == (
    "company_id", "user_id", "operation_type",
    "resource_type", "resource_id", "idempotency_key",
), sonra["tekiller"][YENI_TEKIL]
assert ESKI_TEKIL not in sonra["tekiller"], sonra["tekiller"]
# SQLite yeniden kurmasi bunlari DUSURMEMELI (olculdu: `copy_from` eksikse
# indeks sessizce kayboluyordu).
assert INDEKS in sonra["indeksler"], sonra["indeksler"]
assert once["checkler"] == sonra["checkler"], (once["checkler"], sonra["checkler"])
assert once["fkler"] == sonra["fkler"], (once["fkler"], sonra["fkler"])

with motor.connect() as baglanti:
    sahipler = sorted(
        int(r)
        for r in baglanti.execute(
            sa.text(
                "SELECT user_id FROM " + TABLO
                + " WHERE idempotency_key IN (:a,:b)"
            ),
            {"a": ESKI_ANAHTARLAR[0], "b": ESKI_ANAHTARLAR[1]},
        ).scalars()
    )
assert sahipler == [0, 0], sahipler

command.downgrade(cfg, ONCE)
geri = sema()
assert "user_id" not in geri["sutunlar"], geri["sutunlar"]
assert ESKI_TEKIL in geri["tekiller"], geri["tekiller"]
assert YENI_TEKIL not in geri["tekiller"], geri["tekiller"]
assert INDEKS in geri["indeksler"], geri["indeksler"]
assert geri["checkler"] == once["checkler"], (geri["checkler"], once["checkler"])
assert geri["fkler"] == once["fkler"], (geri["fkler"], once["fkler"])

command.upgrade(cfg, SONRA)
tekrar = sema()
assert tekrar["sutunlar"].get("user_id") is False, tekrar["sutunlar"]
assert YENI_TEKIL in tekrar["tekiller"], tekrar["tekiller"]
assert INDEKS in tekrar["indeksler"], tekrar["indeksler"]

motor.dispose()
print("SEC9_GOC_OK")
'''
