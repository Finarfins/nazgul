"""SINIRIN KENDİSİ: her BİLDİRİLEN durum, ŞEMANIN `status` sütununa SIĞMALI.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

`field_integration_events.status` 0044'te ``VARCHAR(20)`` açıldı. FAZ 4
tüketicisi kovaları adlandırdı; ``SKIPPED_SOURCE_NOT_VISIBLE`` 26 karakter.
Gerçek PG 16.4: ``StringDataRightTruncation`` → işlem geri alınır → olay
``PENDING`` kalır → o kiracının kuyruğu SONRAKİ HER DÖNGÜDE aynı yerde
çöker. SQLite `VARCHAR` uzunluğunu YOK SAYDIĞI için mevcut testlerin hepsi
yeşildi; yeşil, kodun değil ORTAMIN özelliğiydi.

--- BU DOSYA NEYİ DONDURUR ---------------------------------------------------

Örneği DEĞİL, SINIFI. İki taraf da TÜRETİLİR:

* **Bildirimler**: `field_stok_tuketici` modülünün `DURUM_` ile başlayan HER
  metin sabiti — elle yazılmış liste YOK.
* **Şema**: göç zincirinden TAZE kurulan veritabanının yansıması — göç
  dosyasının metni değil, kurulan sütunun kendisi.

Sonuç: sütuna sığmayan yeni bir durum sabiti eklemek, kimse bu dosyayı
düzenlemeden, bu testi KIRMIZI yapar ve SUÇLU SABİTİN ADINI söyler.

Bilinen kapsam sınırı: keşif `DURUM_` ön ekine dayanır. Bu yüzden ikinci bir
iddia daha var — `TERMINAL_DURUMLAR` üyelerinin HEPSİ keşfedilmiş olmalı;
ön ek alışkanlığından sapan bir kova ekleme, o iddiayı kırar.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

_SEMA_PROBU = r'''
import json, os, sys
sys.path.insert(0, os.environ["BACKEND"])
import app.main  # göç zincirini koşturur
from sqlalchemy import create_engine, inspect

motor = create_engine(os.environ["DATABASE_URL"])
sutunlar = inspect(motor).get_columns("field_integration_events")
status = [s for s in sutunlar if s["name"] == "status"]
assert status, "SEMA PROBU: `status` sutunu yok"
print("SEMA " + json.dumps({"uzunluk": getattr(status[0]["type"], "length", None)}))
'''


def _sema_uzunlugu(db_yolu: Path) -> int:
    """Göç zincirinden TAZE kurulan şemadaki `status` genişliği."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _SEMA_PROBU], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    satir = [s for s in tamam.stdout.splitlines() if s.startswith("SEMA ")]
    assert satir, tamam.stdout + "\n" + tamam.stderr
    uzunluk = json.loads(satir[-1][len("SEMA "):])["uzunluk"]
    assert isinstance(uzunluk, int), (
        "Şema `status` için bir uzunluk BİLDİRMİYOR; bu testin ölçtüğü sınır "
        f"kaybolmuş olur: {uzunluk!r}"
    )
    return uzunluk


def _bildirilen_durumlar() -> dict[str, str]:
    """Modülün `DURUM_` ile başlayan HER metin sabiti. Elle liste YOK."""
    from app import field_stok_tuketici as tuketici

    return {
        ad: deger
        for ad, deger in vars(tuketici).items()
        if ad.startswith("DURUM_") and isinstance(deger, str)
    }


def test_kesif_TERMINAL_kovalarin_HEPSINI_buluyor() -> None:
    """Ön ek alışkanlığından sapan bir kova, aşağıdaki kapıyı GÖRÜNMEZ yapar."""
    from app.field_stok_tuketici import TERMINAL_DURUMLAR

    bulunan = set(_bildirilen_durumlar().values())
    eksik = [d for d in TERMINAL_DURUMLAR if d not in bulunan]
    assert not eksik, (
        "Bu dosyanın keşfi `DURUM_` ön ekine dayanıyor ve bir terminal kova "
        f"ön ekten sapmış: {eksik!r}. Genişlik kapısı o kovayı ÖLÇMEZ."
    )


def test_her_BILDIRILEN_durum_SEMADAKI_sutuna_SIGIYOR(tmp_path: Path) -> None:
    """`girdi == kova` gibi bu da bir ASSERT: sığmayan durum = kalıcı kuyruk durması."""
    uzunluk = _sema_uzunlugu(tmp_path / "durum-genisligi.db")
    bildirilen = _bildirilen_durumlar()
    assert bildirilen, "Hiç durum sabiti bulunamadı; bu koşum sınamıyor."

    tasanlar = sorted(
        (ad, deger, len(deger))
        for ad, deger in bildirilen.items()
        if len(deger) > uzunluk
    )
    assert not tasanlar, (
        "DURUM SABİTİ ŞEMAYA SIĞMIYOR: field_integration_events.status "
        f"VARCHAR({uzunluk}) ve şu sabit(ler) taşıyor: "
        + ", ".join(f"{ad}={deger!r} ({n} karakter)" for ad, deger, n in tasanlar)
        + ". PostgreSQL'de bu StringDataRightTruncation verir; olay PENDING "
        "kalır ve o kiracının kuyruğu KALICI olarak durur."
    )
