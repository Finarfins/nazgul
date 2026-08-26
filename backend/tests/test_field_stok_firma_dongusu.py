"""FİRMA DÖNGÜSÜ KORUMASI: neyi yutar, neyi YUTMAZ.

`tum_firmalari_isle` içindeki firma başına koruma bir DENGE kurar:

* ÇALIŞMA ZAMANI ARIZASI (ölü oturum, kopan bağlantı, kilit) YUTULUR — ama
  sessizce değil: `logger.exception` ile tam iz yazılır VE `COMPANY_FAILED`
  olarak SAYILIR. Aksi hâlde id'si büyük her firma o döngüde işlenmeden
  kalırdı. Bu yolun GERÇEK bir ölü oturumla kanıtı PostgreSQL ikizindedir
  (`test_bir_firma_DUSERSE_...`); burada ucuz ve dialect'siz ikizi durur.

* KORUNUM İHLALİ (`AssertionError`) YUTULMAZ. Bu bir çalışma zamanı arızası
  değil KOD hatasıdır; yakalamak, bu modülün bütün amacı olan invaryantı
  gürültülü bir çöküşten SESSİZ bir sayıya çevirirdi. Bilerek YUKARI bırakılır.

İkinci madde bu dosyanın asıl sebebidir: bir korumanın en tehlikeli tarafı
yuttuğu şey değil, YUTMAMASI gerekeni de yutmaya başlamasıdır ve bu geri dönüş
tanımı gereği SESSİZDİR.
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


_PROB = r'''
from sqlalchemy import text

import app.main  # goc zincirini kosturur
from app import field_stok_tuketici as consumer
from app.db import SessionLocal

# IKINCI FIRMA: dusen firmanin ARDINDAKI firma islenmeli.
with SessionLocal.begin() as db:
    db.execute(text(
        "INSERT INTO companies (id,name,is_active,created_at) "
        "VALUES (2,'Ikinci Firma',1,'2026-08-01T00:00:00')"))

gercek = consumer.olaylari_isle

# --- 1) CALISMA ZAMANI ARIZASI: yutulur, SAYILIR, dongu YASAR --------------
def _arizali(db, firma, **kw):
    if int(firma) == 1:
        raise RuntimeError("olu oturum taklidi")
    return gercek(db, firma, **kw)

consumer.olaylari_isle = _arizali
with SessionLocal() as db:
    try:
        s = consumer.tum_firmalari_isle(db)
        print("ARIZA_SAYAC %d" % s.get("COMPANY_FAILED", -1))
        print("ARIZA_GIRDI %d" % s["girdi"])
    except Exception as e:
        print("ARIZA_KACTI %s" % type(e).__name__)

# --- 2) KORUNUM IHLALI: YUTULMAZ ------------------------------------------
def _ihlal(db, firma, **kw):
    raise AssertionError("KORUNUM IHLALI taklidi")

consumer.olaylari_isle = _ihlal
with SessionLocal() as db:
    try:
        s = consumer.tum_firmalari_isle(db)
        print("IHLAL_YUTULDU %d" % s.get("COMPANY_FAILED", -1))
    except AssertionError:
        print("IHLAL_KACTI")
    except Exception as e:
        print("IHLAL_BASKA %s" % type(e).__name__)

consumer.olaylari_isle = gercek
'''


def test_firma_dongusu_arizayi_SAYAR_korunum_ihlalini_YUTMAZ(tmp_path: Path) -> None:
    result = _run_isolated(_PROB, tmp_path / "firma-dongusu.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    cikti = result.stdout

    assert "ARIZA_KACTI" not in cikti, (
        "FİRMA DÖNGÜSÜ KORUMASIZ: bir firmanın arızası tüm-firmalar "
        f"döngüsünden dışarı kaçtı. çıktı={cikti!r}"
    )
    assert "ARIZA_SAYAC 1" in cikti, (
        "DÜŞÜŞ SAYILMADI: aç kalan döngü çıkarılabilir değil SAYILABİLİR "
        f"olmalı. çıktı={cikti!r}"
    )
    assert "ARIZA_GIRDI 0" in cikti, (
        "Düşen firma denklemin `girdi` yanına katkı vermemeli; ikinci firmanın "
        f"kuyruğu boş olduğu için toplam girdi 0 beklenir. çıktı={cikti!r}"
    )
    assert "IHLAL_KACTI" in cikti, (
        "KORUNUM İHLALİ YUTULDU. `AssertionError` bir çalışma zamanı arızası "
        "değil KOD hatasıdır; `COMPANY_FAILED` olarak sayılması, bu modülün "
        "bütün amacı olan invaryantı GÜRÜLTÜLÜ bir çöküşten SESSİZ bir sayıya "
        "çevirir ve korunum denklemi bir daha hiçbir şeyi koruyamaz. "
        f"çıktı={cikti!r}"
    )
    print(cikti.strip())
