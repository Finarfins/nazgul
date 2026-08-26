"""KURTARMA YAZIMI BAĞIL MI? Kaybolan artış TEK SÜREÇTE ölçülür.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

`_denemeyi_kaydet` bir zamanlar `attempts = :deneme` yazıyordu ve o `deneme`,
döngünün başındaki ANLIK GÖRÜNTÜDEN (`olay["attempts"] + 1`) türüyordu.
Zamanlayıcı lifespan'dan başlatıldığı için `--workers N` N tane tüketici
demektir: iki işçi aynı olayın anlık görüntüsünü okur, biri önce başarısız olup
`attempts`i KALICI olarak artırır, diğeri ise KENDİ eski görüntüsünden
hesapladığı değeri MUTLAK yazıp o artışı SESSİZCE SİLER. Sayaç geri yürür,
`AZAMI_DENEME` tavanına HİÇ varılmaz ve zehirli olay sonsuza kadar denenir.

--- NEDEN BU TESTİN VAR OLMASI GEREKİYORDU -----------------------------------

Bu düzeltmenin KAPISI YOKTU. Gerekçe olarak "sadık bir test İKİ GERÇEK SÜREÇ
yarışı ister" bildirilmişti. İSTEMİYOR: kusur bir KAYIP GÜNCELLEMEDİR ve kayıp
güncelleme İFADENİN özelliğidir, süreç sayısının değil. Gereken tek şey
araya giren bir COMMIT'tir; onu tek süreçte İKİ OTURUM belirlenimci olarak
verir. Paralellik YOK, yarış YOK, zamanlama YOK — bu yüzden SQLite'ta da koşar.

`sonraki_deneme` A'nın ESKİ görüntüsünden hesaplanır ve B araya girdikten
SONRA kullanılır; MUTLAK yazan bir sürüm B'nin artışını tam olarak burada
siler.

Yazım `_kurtar` üzerinden yapılır, çünkü üretimdeki TEK çağıran odur ve
`_denemeyi_kaydet`e ancak o karar verir. Bu, testi imza değişikliklerine karşı
da doğru yerden bağlar: MUTLAK sürüme dönüş `_kurtar`ın gövdesindedir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _run_isolated(code: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENVIRONMENT"] = "test"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=180,
    )


def test_araya_giren_artis_kurtarma_yaziminda_KAYBOLMAZ(tmp_path: Path) -> None:
    """A eski görüntüsüyle yazarken B'nin KALICI artışı silinmemeli."""
    result = _run_isolated(
        r'''
from sqlalchemy import text

import app.main  # goc zincirini kosturur
from app import field_stok_tuketici as consumer
from app.db import SessionLocal

OLAY = 88001
Z = '2026-08-01T00:00:00'

with SessionLocal.begin() as db:
    firma = db.execute(
        text("SELECT id FROM companies ORDER BY id LIMIT 1")
    ).scalar_one()
    db.execute(text("""
        INSERT INTO field_integration_events
            (id, company_id, source_type, source_id, target, idempotency_key,
             status, attempts, created_at, updated_at)
        VALUES (:i, :c, 'field_activity', 99999, 'stock', 'bagil-olcum:88001',
                'PENDING', 0, :z, :z)
    """), {"i": OLAY, "c": firma, "z": Z})


def _deneme():
    with SessionLocal() as db:
        return int(db.execute(
            text("SELECT attempts FROM field_integration_events WHERE id = :i"),
            {"i": OLAY},
        ).scalar_one())


# --- A: ANLIK GORUNTU. `_bir_olayi_isle` ne yapiyorsa aynisi. ---------------
oturum_a = SessionLocal()
anlik = int(oturum_a.execute(
    text("SELECT attempts FROM field_integration_events WHERE id = :i"),
    {"i": OLAY},
).scalar_one())
sonraki_deneme = anlik + 1          # A'nin ESKI goruntusu: 1
print("ANLIK %d" % anlik)

# --- B: BASKA bir oturum denemeyi KALICI artirir ve COMMIT eder. ------------
with SessionLocal.begin() as oturum_b:
    oturum_b.execute(text(
        "UPDATE field_integration_events SET attempts = attempts + 1 "
        "WHERE id = :i"), {"i": OLAY})
print("ARA %d" % _deneme())         # 1

# --- A: ESKI goruntusuyle kurtarma yazimini yapar. -------------------------
# `_kurtar` once rollback eder (A'nin okuma islemi kapanir), sonra
# `_denemeyi_kaydet` cagirir. `azami_deneme` yuksek: DEAD koluna sapmasin.
sonuc = consumer._kurtar(
    oturum_a, firma, OLAY, sonraki_deneme, 99, "bagil-olcum")
oturum_a.close()

print("SONUC %s" % sonuc)
print("SON %d" % _deneme())
with SessionLocal() as db:
    print("DURUM %s" % db.execute(
        text("SELECT status FROM field_integration_events WHERE id = :i"),
        {"i": OLAY},
    ).scalar_one())
''',
        tmp_path / "bagil-deneme.db",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    cikti = result.stdout

    assert "ANLIK 0" in cikti, f"KURULUM: olay attempts=0 ile baslamali: {cikti!r}"
    assert "ARA 1" in cikti, (
        "KURULUM: araya giren oturum `attempts`i KALICI olarak 1 yapmaliydi; "
        f"yapmadiysa bu kosum kayip guncellemeyi hic zorlamaz. cikti={cikti!r}"
    )
    assert "SONUC RETRY_SCHEDULED" in cikti, (
        "KURULUM: `_kurtar` kurtarma yazimi koluna girmeliydi (DEAD ya da "
        f"RECOVERY_FAILED degil). cikti={cikti!r}"
    )
    assert "DURUM PENDING" in cikti, (
        f"Kurtarma yazimi olayi PENDING birakmaliydi. cikti={cikti!r}"
    )
    assert "SON 2" in cikti, (
        "KAYIP GUNCELLEME: kurtarma yazimi MUTLAK. Araya giren oturumun "
        "KALICI artisi silindi — `attempts` 2 yerine A'nin ESKI goruntusunden "
        "hesapladigi degere geri yuruedu. `--workers N` altinda bu, "
        "`AZAMI_DENEME` tavanina HIC varilmamasi demektir: zehirli olay DEAD "
        "olmaz, her dongude yeniden denenir ve kuyrugun onunu sonsuza kadar "
        f"tikar. Artis BAGIL (`attempts + 1`) olmali. cikti={cikti!r}"
    )
    print(cikti.strip())
