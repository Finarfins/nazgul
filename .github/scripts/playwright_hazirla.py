#!/usr/bin/env python3
"""Playwright tarayıcısını HAZIRLAR: önce bakar, gerekirse SINIRLI kurar.

--- KUSUR ----------------------------------------------------------------------

CI'da ``e2e`` işinin 9. adımı şuydu::

    npx playwright install --with-deps chromium

Adımın kendi zaman sınırı YOKTU; yalnız işin ``timeout-minutes: 20`` sınırı
vardı. Ölçüldü (koşu 32250851322, develop 53aab22d): adım **19 dk 5 sn** asılı
kaldı, iş İPTAL oldu ve 10-11. adımlar — testlerin koştuğu adımlar — ATLANDI.
O head'de **sıfır** e2e testi koştu ve koşu "cancelled" göründü.

Bu, #82'nin ``apt`` için kapattığı kusurun AYNI SINIFI. Sebep de birebir aynı:
``--with-deps`` kök kullanıcıya geçip ``apt-get update`` koşturuyor. Log'da
görülüyor::

    Installing dependencies...
    Switching to root user to install dependencies...
    Get:1 file:/etc/apt/apt-mirrors.txt Mirrorlist [144 B]

--- ÖLÇÜM ----------------------------------------------------------------------

Başarılı bir koşuda (32249926793, e2e işi 96058436961) adım 46 saniye sürdü ve
şu üç şey ölçüldü:

1. **``--with-deps`` YALNIZ FONT kuruyor.** Çıktı ``0 upgraded, 9 newly
   installed`` diyor ve dokuzunun hepsi font paketi: ``fonts-wqy-zenhei``,
   ``fonts-freefont-ttf``, ``fonts-tlwg-loma-otf``, ``xfonts-encodings``,
   ``fonts-ipafont-gothic``, ``fonts-unifont``, ``xfonts-utils``,
   ``xfonts-cyrillic``, ``xfonts-scalable``. Chromium'un ÇALIŞMA ZAMANI
   kütüphaneleri (libnss3, libatk… ) tek satır bile kurulmadı — koşucu
   imajında ZATEN varlar. #82'deki metapaket bulgusunun aynısı.
2. **Tarayıcının kendisi apt'ten GELMİYOR.** Chrome for Testing,
   ``cdn.playwright.dev``den iniyor ve ölçülen süre **5 saniye**
   (11:56:26.888 -> 11:56:31.939). Yani 46 saniyenin ~35'i apt'e, 5'i
   tarayıcıya gidiyor. Asılan kısım apt.
3. **Tarayıcı önbelleği YOK.** İş ``npm`` ve ``pip`` önbelleği tanımlıyor ama
   ``~/.cache/ms-playwright`` için bir önbellek yok; tarayıcı her koşuda
   yeniden iniyor. Bu betik önbellek EKLEMİYOR (bkz. BİLEREK YAPILMAYAN).

--- BU BETİK NE YAPIYOR --------------------------------------------------------

İki AYRI aşama, iki AYRI sınır — çünkü ikisi farklı şeyler ve biri diğerinin
bütçesini yiyemez:

* **Tarayıcı**: ``~/.cache/ms-playwright`` altında çalıştırılabilir varsa
  hiçbir ağ işlemi YAPMAZ. Yoksa ``playwright install chromium`` ile
  ``PW_TARAYICI_SINIR_SN`` saniyelik KENDİ sınırı içinde indirir.
* **Font bağımlılıkları**: dokuz paketin hepsi ``dpkg`` ile kuruluysa apt'e
  HİÇ dokunmaz. Eksik varsa ``--with-deps`` yalnız o zaman ve
  ``PW_BAGIMLILIK_SINIR_SN`` saniyelik KENDİ sınırı içinde koşar.

Sınırların ikisi de işin ``timeout-minutes``ından BAĞIMSIZDIR: kurulumda zaman
tüketmek, testlerin başarısız olmasından ayırt edilebilir olsun diye.

**Üç durum, üç okunur sinyal** — "kurulum düştü", "testler düştü" ve "hiçbir
şey koşmadı" birbirine karışmasın diye:

* ``hazir``   — ağa çıkılmadı, her şey yerinde (çıkış 0)
* ``kuruldu`` — eksik olan sınır içinde kuruldu (çıkış 0)
* ``YOK``     — hazırlanamadı; ``::error::`` ile ve HANGİ aşamada (sınır mı,
  komut mu, komut geçti ama araç yok mu) olduğunu söyleyerek (çıkış 1)

--- SINIRIN GERÇEKTEN KESTİĞİ ÖLÇÜLDÜ ------------------------------------------

``subprocess.run(..., shell=True, timeout=N)`` kabuğu öldürür, TORUNU değil:
``npx`` altındaki ``apt`` yaşamaya devam eder ve sınır rapor edilir ama
BAĞLAMAZ. Bu yüzden burada kabuk yok, ``start_new_session=True`` ile ayrı bir
süreç grubu açılıyor ve zaman aşımında ``os.killpg`` ile GRUBUN tamamı
öldürülüyor. ``tests/test_ci_playwright_hazirlik.py`` bunu ölçüyor: sınırı
aşan bir komut, sınıra yakın bir sürede kesilmeli.

--- BİLEREK YAPILMAYAN ---------------------------------------------------------

``~/.cache/ms-playwright`` için önbellek EKLENMEDİ. Eklenebilirdi ve tarayıcı
indirmesini de kaldırırdı, ama ölçüm o indirmenin 5 saniye olduğunu söylüyor;
asılan kısım apt. Önbellek, çözülen sorunu çözmez ve bayat tarayıcı sürümünü
sessizce taşıma riski getirir. Ayrı bir karar olarak bırakıldı.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

#: ``--with-deps``in kurduğu dokuz paket. Ölçümle alındı (bkz. başlık).
FONT_PAKETLERI = (
    "fonts-wqy-zenhei",
    "fonts-freefont-ttf",
    "fonts-tlwg-loma-otf",
    "xfonts-encodings",
    "fonts-ipafont-gothic",
    "fonts-unifont",
    "xfonts-utils",
    "xfonts-cyrillic",
    "xfonts-scalable",
)

ONBELLEK = Path.home() / ".cache" / "ms-playwright"


def _yaz(mesaj: str) -> None:
    print(mesaj, flush=True)


def _hata(mesaj: str) -> None:
    print(f"::error::{mesaj}", file=sys.stderr, flush=True)


def tarayici_var_mi() -> Path | None:
    """Önbellekte çalıştırılabilir bir chromium var mı."""
    if not ONBELLEK.is_dir():
        return None
    for dizin in sorted(ONBELLEK.glob("chromium*")):
        for ad in ("chrome", "headless_shell"):
            for yol in dizin.rglob(ad):
                if yol.is_file() and os.access(yol, os.X_OK):
                    return yol
    return None


def eksik_fontlar() -> list[str]:
    """``dpkg`` ile kurulu OLMAYAN font paketleri. dpkg yoksa hepsi eksik sayılır."""
    if shutil.which("dpkg-query") is None:
        return list(FONT_PAKETLERI)
    eksik: list[str] = []
    for paket in FONT_PAKETLERI:
        sonuc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", paket],
            capture_output=True, text=True,
        )
        if sonuc.returncode != 0 or "install ok installed" not in sonuc.stdout:
            eksik.append(paket)
    return eksik


def sinirli_kostur(komut: list[str], sinir_sn: int, calisma_dizini: str | None = None):
    """Komutu KENDİ süreç grubunda koşturur ve sınırda GRUBU öldürür.

    ``shell=True`` KULLANILMIYOR: kabuğu öldürmek torunu öldürmez ve sınır
    bağlamaz. Ayrı oturum + ``killpg`` bunu gerçekten keser.
    """
    baslangic = time.monotonic()
    # POSIX'te ayrı oturum; Windows'ta karşılığı yok ve CI Linux — ama yerel
    # kapı testinin de koşabilmesi için ikisi de destekleniyor.
    ek = {"start_new_session": True} if hasattr(os, "killpg") else {}
    try:
        surec = subprocess.Popen(
            komut,
            cwd=calisma_dizini,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **ek,
        )
    except FileNotFoundError as hata:
        return None, 0.0, f"komut bulunamadı: {hata}"

    try:
        cikti, _ = surec.communicate(timeout=sinir_sn)
        return surec.returncode, time.monotonic() - baslangic, cikti
    except subprocess.TimeoutExpired:
        try:
            # GRUBU öldür, yalnız çocuğu değil: torun (apt) hayatta kalırsa
            # sınır rapor edilir ama BAĞLAMAZ.
            os.killpg(os.getpgid(surec.pid), signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError, OSError):
            surec.kill()
        try:
            cikti, _ = surec.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            cikti = ""
        return None, time.monotonic() - baslangic, cikti


def main() -> int:
    tarayici_sinir = int(os.environ.get("PW_TARAYICI_SINIR_SN", "300"))
    bagimlilik_sinir = int(os.environ.get("PW_BAGIMLILIK_SINIR_SN", "300"))
    calisma = os.environ.get("PW_CALISMA_DIZINI", "frontend")
    tarayici_komut = os.environ.get(
        "PW_TARAYICI_KOMUT", "npx playwright install chromium"
    )
    tarayici_komut = shlex.split(tarayici_komut)
    bagimlilik_komut = os.environ.get(
        "PW_BAGIMLILIK_KOMUT", "npx playwright install-deps chromium"
    )
    bagimlilik_komut = shlex.split(bagimlilik_komut)

    durumlar: list[str] = []

    # --- 1. AŞAMA: tarayıcı --------------------------------------------------
    mevcut = tarayici_var_mi()
    if mevcut is not None:
        _yaz(f"tarayıcı hazir: {mevcut}")
        durumlar.append("tarayici=hazir")
    else:
        _yaz(f"tarayıcı yok; sınır {tarayici_sinir} sn ile kuruluyor")
        kod, sure, cikti = sinirli_kostur(tarayici_komut, tarayici_sinir, calisma)
        if kod is None:
            _hata(
                f"tarayıcı YOK: kurulum {sure:.1f} sn'de SINIRI aştı "
                f"(PW_TARAYICI_SINIR_SN={tarayici_sinir}). Testler koşmadı; bu bir "
                "kurulum arızasıdır, test başarısızlığı değildir."
            )
            _yaz((cikti or "")[-2000:])
            return 1
        if kod != 0:
            _hata(
                f"tarayıcı YOK: kurulum komutu {kod} koduyla düştü ({sure:.1f} sn)."
            )
            _yaz((cikti or "")[-2000:])
            return 1
        if tarayici_var_mi() is None:
            _hata(
                "tarayıcı YOK: komut BAŞARILI döndü ama önbellekte çalıştırılabilir "
                "bulunamadı. Sessiz başarı, başarı değildir."
            )
            return 1
        _yaz(f"tarayıcı kuruldu ({sure:.1f} sn)")
        durumlar.append("tarayici=kuruldu")

    # --- 2. AŞAMA: font bağımlılıkları --------------------------------------
    eksik = eksik_fontlar()
    if not eksik:
        _yaz("font bağımlılıkları hazir; apt'e DOKUNULMADI")
        durumlar.append("fontlar=hazir")
    else:
        _yaz(f"eksik font ({len(eksik)}): {' '.join(eksik)}")
        _yaz(f"sınır {bagimlilik_sinir} sn ile kuruluyor")
        kod, sure, cikti = sinirli_kostur(bagimlilik_komut, bagimlilik_sinir, calisma)
        if kod is None:
            _hata(
                f"font bağımlılıkları YOK: kurulum {sure:.1f} sn'de SINIRI aştı "
                f"(PW_BAGIMLILIK_SINIR_SN={bagimlilik_sinir}). Asılan yer apt'tir; "
                "testler koşmadı."
            )
            _yaz((cikti or "")[-2000:])
            return 1
        if kod != 0:
            _hata(f"font bağımlılıkları YOK: komut {kod} koduyla düştü ({sure:.1f} sn).")
            _yaz((cikti or "")[-2000:])
            return 1
        _yaz(f"font bağımlılıkları kuruldu ({sure:.1f} sn)")
        durumlar.append("fontlar=kuruldu")

    _yaz("PLAYWRIGHT_HAZIRLIK=" + ",".join(durumlar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
