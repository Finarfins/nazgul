"""Testler için DONMUŞ iş saati — ölçüm aleti, uygulama kodu DEĞİL.

`NAZGUL_DONMUS_GUN=YYYY-MM-DD` ortam değişkeni verildiğinde `uygula()`
`app.business_time.business_now` fonksiyonunu o günün İstanbul 12:00'ına
sabitler. `business_today` onu modül global'inden çağırdığı için tek yama
ikisini de kapsar; `from ..business_time import business_today` ile ADINI
bağlayan her rota da aynı sabit günü okur.

Neden var: `test_v3_harman_vadeli` (ve PostgreSQL ikizi) vadeleri sunucunun
saatinden türetir. "Donmuş saatte yeşil" iddiası ancak aleti depoda olan bir
ölçümle tekrarlanabilir; depoda olmayan bir aletle alınmış ölçüm ÖLÇÜM
SAYILMAZ. Bu dosya o alettir ve `_SMOKE` metninin İLK satırında çağrılır —
yani SQLite testinin PYTHONPATH'i yeniden yazan alt sürecinde de, PG
ikizinin aynı süreçteki `exec`'inde de aynı alet çalışır.

Sessiz başarısızlık YASAK: değişken verilmiş ama yama uygulanamıyorsa
(`app` paketi erişilemez, değer bozuk, ya da yamadan sonra `business_today`
hâlâ donmuş günü döndürmüyorsa) `CIKIS_KODU` (97) ile ÇIKAR ve ölümü
stderr'e yazar. Kullanıcı kodu koşmadan ölür; "donmuş saatte yeşil" ancak
bu yüzden delildir. Değişken yoksa hiçbir şey yapmaz ve `None` döner.

Uygulama kodu (`app/`) bu modülü BİLMEZ ve import ETMEZ.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

ORTAM_ANAHTARI = "NAZGUL_DONMUS_GUN"
CIKIS_KODU = 97
ETKIN_ISARETI = "DONMUS_SAAT_ETKIN"
OLUMCUL_ISARETI = "DONMUS_SAAT_OLUMCUL"


def _olumcul(neden: str) -> None:
    sys.stderr.write(
        f"{OLUMCUL_ISARETI}: {neden}; hiçbir kullanıcı kodu koşmadan "
        f"{CIKIS_KODU} ile çıkılıyor (cwd={os.getcwd()!r})\n"
    )
    sys.stderr.flush()
    os._exit(CIKIS_KODU)


def uygula() -> date | None:
    """Ortam değişkeni varsa saati dondurur ve donmuş günü döndürür."""

    deger = os.environ.get(ORTAM_ANAHTARI)
    if not deger:
        return None
    try:
        import app.business_time as business_time
    except Exception as exc:  # noqa: BLE001 - ne olursa olsun gürültülü öl
        _olumcul(f"app.business_time yüklenemedi ({exc!r})")
    try:
        yil, ay, gun = (int(parca) for parca in deger.split("-"))
        donmus = datetime(yil, ay, gun, 12, 0, 0, tzinfo=business_time.ISTANBUL)
    except (ValueError, OverflowError) as exc:
        _olumcul(f"{ORTAM_ANAHTARI}={deger!r} çözülemedi ({exc!r})")

    def business_now() -> datetime:
        return donmus

    business_time.business_now = business_now
    if business_time.business_today() != donmus.date():
        _olumcul("yama uygulandı ama business_today donmuş günü döndürmüyor")
    sys.stderr.write(
        f"{ETKIN_ISARETI} pid={os.getpid()} business_now -> {donmus.isoformat()}\n"
    )
    sys.stderr.flush()
    return donmus.date()
