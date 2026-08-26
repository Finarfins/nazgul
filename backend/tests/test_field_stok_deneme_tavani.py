"""DENEME TAVANI ULAŞILABİLİR Mİ? Zehirli bir olayla İKİ YÖNDE ölçülür.

Ölçülen kusur: `AZAMI_DENEME` `field_stok_tuketici.py` içinde uygulanıyordu
ama TETİKLENEMİYORDU. `attempts`ın tek artıranı `_talep_et`ti ve o artış,
olayı TERMİNAL bir duruma yazan commit ile AYNI işlemdeydi; seçici ise yalnız
`status = PENDING` okuyor. Beklenmeyen bir istisnada işlem geri alınıyor,
artış da onunla gidiyordu: olay PENDING'e `attempts=0` ile dönüyor ve 30
saniyede bir SONSUZA KADAR yeniden deneniyordu. `ORDER BY id` yüzünden aynı
olay kendi firmasının kuyruğunda hep BİRİNCİ kalıyor, istisna tüm-firmalar
döngüsünü de kestiği için SIRADAKİ HER FİRMA işlenmiyordu.

Bir kapı ateşlenemiyorsa kapı değildir; burada ateşlendiği DE, erken
ateşlenmediği DE ölçülür.
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


def test_poison_event_reaches_dead_after_the_bound_and_not_before(
    tmp_path: Path,
) -> None:
    result = _run_isolated(
        r'''
from sqlalchemy import text

import app.main  # göç zincirini koşturur
from app import field_stok_tuketici as consumer
from app.db import SessionLocal

ZEHIR = 77001   # kaynak okuması PATLAR
SAGLAM = 77002  # kaynağı görünmeyen NORMAL olay: kuyruğun devam ettiğini kanıtlar

with SessionLocal.begin() as db:
    company_id = db.execute(
        text("SELECT id FROM companies ORDER BY id LIMIT 1")
    ).scalar_one()
    for kaynak_id, anahtar in ((ZEHIR, "zehir"), (SAGLAM, "saglam")):
        db.execute(text("""
            INSERT INTO field_integration_events
                (company_id, source_type, source_id, target, idempotency_key,
                 status, attempts, created_at, updated_at)
            VALUES (:c,'field_activity',:s,'stock',:k,'PENDING',0,
                    '2026-08-24T00:00:00','2026-08-24T00:00:00')
        """), {"c": company_id, "s": kaynak_id, "k": "%s:%d:stock" % (anahtar, kaynak_id)})

# ZEHİR: kaynak okuması BEKLENMEYEN bir istisna atar. Bu, tüketicinin adı
# konmuş kovalarından hiçbirine karşılık gelmez — tam olarak geri alınan yol.
tablo, yon, oku = consumer._KAYNAK["field_activity"]

def zehirli_oku(db, firma, kaynak_id):
    if int(kaynak_id) == ZEHIR:
        raise ValueError("poison-source-read")
    return oku(db, firma, kaynak_id)

consumer._KAYNAK = dict(consumer._KAYNAK)
consumer._KAYNAK["field_activity"] = (tablo, yon, zehirli_oku)


def olay_durumu(anahtar):
    with SessionLocal() as db:
        return db.execute(text(
            "SELECT status, attempts FROM field_integration_events "
            "WHERE idempotency_key = :k"
        ), {"k": anahtar}).one()


AZAMI = 3
gozlem = []
for tur in range(1, AZAMI + 2):
    with SessionLocal() as db:
        sayac = consumer.olaylari_isle(db, company_id, azami_deneme=AZAMI)
    zehir = olay_durumu("zehir:%d:stock" % ZEHIR)
    gozlem.append((tur, dict(sayac), zehir.status, zehir.attempts))
    print("TUR %d sayac=%r zehir=%s attempts=%s"
          % (tur, dict(sayac), zehir.status, zehir.attempts))

# --- YÖN 1: TAVANDAN ÖNCE ÖLMEZ -------------------------------------------
for tur, sayac, durum, deneme in gozlem[: AZAMI - 1]:
    assert durum == "PENDING", (
        "RETRY BOUND FIRED EARLY: tur %d sonunda olay %s (attempts=%s); "
        "tavan %d iken daha once olmemeliydi" % (tur, durum, deneme, AZAMI))
    assert sayac["DEAD"] == 0, (tur, sayac)
    assert sayac[consumer.SONUC_YENIDEN] == 1, (tur, sayac)
    assert deneme == tur, (
        "ATTEMPTS DID NOT PERSIST: tur %d sonunda attempts=%s bekleniyordu %d "
        "-- artis geri alinmis demektir, tavan asla dolmaz" % (tur, deneme, tur))

# --- YÖN 2: TAVANDA ÖLÜR ---------------------------------------------------
tur, sayac, durum, deneme = gozlem[AZAMI - 1]
assert durum == "DEAD", (
    "RETRY BOUND UNREACHABLE: %d denemeden sonra olay hala %s (attempts=%s)"
    % (AZAMI, durum, deneme))
assert sayac["DEAD"] == 1, sayac
assert deneme == AZAMI, (deneme, AZAMI)

# --- TERMİNAL: SEÇİCİ ONU BİR DAHA GÖRMEZ ---------------------------------
son_tur, son_sayac, son_durum, _ = gozlem[AZAMI]
assert son_durum == "DEAD", son_durum
assert son_sayac["girdi"] == 0, (
    "STALLED QUEUE: olu olay hala seciliyor: %r" % (son_sayac,))

# --- KUYRUK TIKANMADI: zehir ilk sirada olmasina ragmen sonraki olay islendi
ilk_tur_sayac = gozlem[0][1]
assert ilk_tur_sayac["girdi"] == 2, ilk_tur_sayac
assert ilk_tur_sayac["SKIPPED_SOURCE_NOT_VISIBLE"] == 1, (
    "QUEUE STALLED BEHIND POISON: zehirli olay ilk sirada iken sonraki olay "
    "AYNI dongude islenmeliydi: %r" % (ilk_tur_sayac,))
saglam = olay_durumu("saglam:%d:stock" % SAGLAM)
assert saglam.status == "SKIPPED_SOURCE_NOT_VISIBLE", saglam

# --- last_error GORUNUR KALDI ---------------------------------------------
with SessionLocal() as db:
    hata = db.execute(text(
        "SELECT last_error FROM field_integration_events "
        "WHERE idempotency_key = :k"
    ), {"k": "zehir:%d:stock" % ZEHIR}).scalar_one()
assert "poison-source-read" in (hata or ""), hata

print("RETRY_BOUND_OK not_dead_before=%d dead_at=%d last_error_kept=1"
      % (AZAMI - 1, AZAMI))
''',
        tmp_path / "retry-bound.db",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "RETRY_BOUND_OK not_dead_before=2 dead_at=3" in result.stdout
    print(result.stdout.strip())
