"""PostgreSQL istemci hazırlığı: ÜÇ DURUM birbirinden ayırt edilebilir olmalı.

--- KUSUR ----------------------------------------------------------------------

``backend-postgresql`` işinin 7. adımı sınırsızdı::

    sudo apt-get update && sudo apt-get install -y postgresql-client

Paket deposu yavaşlayınca adım 30 dakika asılı kaldı, iş ``timeout-minutes: 30``
sınırına çarpıp İPTAL oldu ve 8. adım — testlerin koştuğu adım — ATLANDI.
O head'de **sıfır** PostgreSQL testi koştu; koşu dışarıdan "cancelled" göründü.
Kırmızı yok, uyarı yok.

Ölçüldü: üç dalda (#77, #80, #81) altı shard, hepsi 29d31s–29d45s arasında.
Sabit olan SHARD değil ADIM. Aynı koşuda (32233412985, deneme 2) shard 1 ve 2
aynı adımı **32 ve 68 saniyede** bitirdi; shard 0 ve 3 otuz dakika asılı kaldı.

--- ASIL KUSUR: ÜÇ DURUM DIŞARIDAN AYNI GÖRÜNÜYORDU ---------------------------

    kurulum başarısız   ->  koşu "cancelled"
    testler başarısız   ->  koşu "cancelled" (test adımı hiç koşmadıysa)
    hiçbir şey koşmadı  ->  koşu "cancelled"

Üçü de aynı işareti veriyordu. Bir kapının YOKLUĞU kesinti gibi okunuyorsa, o
kapı kırmızı verebilen bir kapıdan daha kötüdür: kimse bakmaz.

--- BU DOSYA ÜÇÜNÜ DE ISMARLAMA ÜRETTİRİR --------------------------------------

Aşağıdaki testler ``pg_istemci_hazirla.py``yi sahte bir PATH ve sahte bir
kurulum komutuyla çalıştırıp beş ayrı sonucu ölçüyor: ``hazir``, ``kuruldu``,
kurulum komutu başarısız, kurulum SINIRI aşıldı, kurulum geçti ama araç yok.
Ayrıca ci.yml'nin yapısı ölçülüyor: kurulumun kendi sınırı var mı, testler
kurulumun başarısına BAĞLI mı, kurulum başarısızlığının kendi adı var mı ve
"hiç dosya koşulmadı" kendi başına kırmızı mı.

--- ÖLÇÜLEN SINIR --------------------------------------------------------------

Bu dosya betiğin KARAR MANTIĞINI ölçer, gerçek ``apt-get``i değil. Gerçek paket
deposunun yavaşlaması burada üretilemez; üretilen şey, yavaşlamanın betiğe
GÖRÜNDÜĞÜ hâl (zaman aşımı). Bu bilinçli: kapının işi altyapıyı hızlandırmak
değil, altyapı yavaşladığında sonucun okunabilir kalmasını sağlamak.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

KOK = Path(__file__).resolve().parents[2]
BETIK = KOK / ".github" / "scripts" / "pg_istemci_hazirla.py"
CI = KOK / ".github" / "workflows" / "ci.yml"

#: Betiğin gerçekten aradığı araçlar.
ARACLAR = ("pg_dump", "pg_restore")


def _sahte_arac(dizin: Path, ad: str) -> None:
    """PATH'te bulunabilen çalıştırılabilir bir kabuk üretir (iki platform)."""
    dizin.mkdir(parents=True, exist_ok=True)
    unix = dizin / ad
    unix.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unix.chmod(0o755)
    # Windows'ta shutil.which PATHEXT arar; .bat olmadan bulunmaz.
    (dizin / f"{ad}.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")


def _kos(tmp_path: Path, *, arac_var: bool, kurulum: str, sinir: str = "60"):
    """Betiği IZOLE bir PATH ile çalıştırır; gerçek pg_dump'a ulaşamaz."""
    sahte = tmp_path / "bin"
    sahte.mkdir(parents=True, exist_ok=True)
    if arac_var:
        for ad in ARACLAR:
            _sahte_arac(sahte, ad)

    ortam = os.environ.copy()
    # PATH'i BİLEREK daraltıyoruz: makinede kurulu gerçek istemci ölçümü
    # kirletmesin. Python'un kendisi tam yolla çağrılıyor.
    ortam["PATH"] = str(sahte)
    ortam["PG_ISTEMCI_KURULUM_KOMUTU"] = kurulum
    ortam["PG_ISTEMCI_SINIR_SN"] = sinir
    ortam["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(BETIK)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=ortam, timeout=120,
    )


def _python_komutu(govde: str) -> str:
    """Sahte kurulum komutu — iki platformda da çalışır."""
    return f'"{sys.executable}" -c "{govde}"'


# ---------------------------------------------------------------------------
# DURUM 1 — araçlar zaten var: AĞA HİÇ ÇIKILMAZ
# ---------------------------------------------------------------------------
def test_state_ready_skips_the_install_entirely(tmp_path: Path) -> None:
    """Sıcak yol: kurulum komutu ÇAĞRILMAMALI.

    Ölçüm bunu gerektiriyor: koşucu imajında pg_dump zaten var; 30 dakikayı
    yiyen apt-get update yalnız 11.6 kB'lik bir metapaket içindi.
    """
    isaret = tmp_path / "kurulum-cagrildi.txt"
    govde = f"open(r'{isaret}','w').write('x')"
    sonuc = _kos(tmp_path, arac_var=True, kurulum=_python_komutu(govde))
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "PG_ISTEMCI=hazir" in sonuc.stdout, sonuc.stdout
    assert not isaret.exists(), (
        "araçlar PATH'te olmasına rağmen kurulum komutu çalıştırıldı; "
        "sıcak yolda gereksiz ağ işlemi var"
    )


# ---------------------------------------------------------------------------
# DURUM 2 — araçlar yok, kurulum başarılı
# ---------------------------------------------------------------------------
def test_state_installed_reports_itself_distinctly(tmp_path: Path) -> None:
    sahte = tmp_path / "bin"
    # ÇALIŞTIRMA BİTİ ŞART — ve bu satır bir CI kırmızısıyla öğrenildi. İlk hâl
    # chmod etmiyordu: Windows'ta ``.bat`` uzantısı yüzünden yerelde YANLIŞ
    # SEBEPLE geçti, Linux'ta ``shutil.which`` dosyayı bulamayınca betik DOĞRU
    # davranıp "kurulum bitti ama araç yok" dedi. Kusur betikte değil bu
    # sahtedeydi; yerel Windows koşusu POSIX davranışını doğrulayamaz.
    govde = (
        f"import pathlib,os;d=pathlib.Path(r'{sahte}');d.mkdir(parents=True,exist_ok=True);"
        "[ (d/n).write_text('#!/bin/sh\\nexit 0\\n') for n in ('pg_dump','pg_restore') ];"
        "[ os.chmod(d/n, 0o755) for n in ('pg_dump','pg_restore') ];"
        "[ (d/(n+'.bat')).write_text('@echo off\\r\\nexit /b 0\\r\\n') for n in ('pg_dump','pg_restore') ]"
    )
    sonuc = _kos(tmp_path, arac_var=False, kurulum=_python_komutu(govde))
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "PG_ISTEMCI=kuruldu" in sonuc.stdout, sonuc.stdout


# ---------------------------------------------------------------------------
# DURUM 3 — kurulum BAŞARISIZ: kendi adıyla, kendi çıkış koduyla
# ---------------------------------------------------------------------------
def test_state_install_failed_is_named_and_red(tmp_path: Path) -> None:
    sonuc = _kos(tmp_path, arac_var=False,
                 kurulum=_python_komutu("import sys;sys.exit(7)"))
    assert sonuc.returncode == 1, sonuc.stdout + sonuc.stderr
    assert "PG_ISTEMCI=YOK" in sonuc.stderr, sonuc.stderr
    assert "kurulum komutu 7 ile başarısız" in sonuc.stderr, sonuc.stderr


# ---------------------------------------------------------------------------
# DURUM 4 — kurulum SINIRI aşıyor: işin bütçesini yemez, kendi sınırı var
# ---------------------------------------------------------------------------
def test_state_install_timeout_is_bounded_independently(tmp_path: Path) -> None:
    """Asıl kusurun doğrudan karşılığı: takılan kurulum işi öldürmemeli."""
    import time as _t

    basladi = _t.monotonic()
    sonuc = _kos(tmp_path, arac_var=False,
                 kurulum=_python_komutu("import time;time.sleep(30)"), sinir="2")
    gecen = _t.monotonic() - basladi
    assert sonuc.returncode == 1, sonuc.stdout + sonuc.stderr
    assert "SINIRINI aştı" in sonuc.stderr, sonuc.stderr
    assert "ALTYAPI" in sonuc.stderr, sonuc.stderr
    assert gecen < 25, (
        f"kurulum kendi sınırıyla kesilmedi ({gecen:.1f}s); iş bütçesini yiyebilir"
    )


# ---------------------------------------------------------------------------
# DURUM 5 — kurulum "başarılı" ama araç yok: sessizce geçmemeli
# ---------------------------------------------------------------------------
def test_state_install_succeeded_but_tools_missing(tmp_path: Path) -> None:
    sonuc = _kos(tmp_path, arac_var=False,
                 kurulum=_python_komutu("pass"))
    assert sonuc.returncode == 1, sonuc.stdout + sonuc.stderr
    assert "araçlar hâlâ yok" in sonuc.stderr, sonuc.stderr


# ---------------------------------------------------------------------------
# ci.yml YAPISI — üç durumun AYRI işaret vermesi workflow'da da kurulu mu
# ---------------------------------------------------------------------------
def _pg_isi() -> list[dict]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["backend-postgresql"]["steps"]


def _adim(ad_parcasi: str) -> dict:
    for adim in _pg_isi():
        if ad_parcasi in str(adim.get("name", "")):
            return adim
    raise AssertionError(f"'{ad_parcasi}' adımı ci.yml'de yok")


def test_install_step_is_bounded_independently_of_the_job() -> None:
    adim = _adim("Install PostgreSQL backup client")
    assert "timeout-minutes" in adim, (
        "kurulum adımının KENDİ sınırı yok; işin timeout-minutes'ını tüketip "
        "testleri atlatabilir — kusurun tam olarak yaşandığı hâl budur"
    )
    assert int(adim["timeout-minutes"]) < 30, adim["timeout-minutes"]


def test_install_failure_does_not_zero_out_coverage() -> None:
    """Kurulum başarısızlığı testleri ATLATMAMALI — ama işi de fail-open yapmamalı.

    İlk tasarım ``continue-on-error: true`` kullanıyordu. Mevcut K1 kapısı onu
    reddetti ve HAKLIYDI: yayın bariyerindeki bir işte adımın işi kırmızıya
    çevirmeden başarısız olabilmesi ``publish-image``ı fail-open yapardı.
    Doğru biçim, başarısızlığı YUTUP KAYDETMEK ve testlerden SONRA ayrı bir
    adımda işi kırmızıya çevirmek: kapsam sıfıra inmez, bariyer fail-closed
    kalır.
    """
    adim = _adim("Install PostgreSQL backup client")
    assert "continue-on-error" not in adim, (
        "continue-on-error yayın bariyerini fail-open yapar (K1); sonuç "
        "KAYDEDİLMELİ, yutulmamalı"
    )
    betik = str(adim.get("run", ""))
    assert "durum=hazir" in betik and "durum=YOK" in betik, (
        "kurulum sonucu kaydedilmiyor; başarısızlık ya işi öldürür ya "
        "sessizce kaybolur"
    )
    assert "GITHUB_OUTPUT" in betik, betik
    testler = _adim("Run PostgreSQL integration gates")
    assert "if" not in testler, (
        "test adımı bir koşula bağlanmış; kurulumdan bağımsız koşmalı"
    )


def test_install_failure_has_its_own_named_red() -> None:
    adim = _adim("PostgreSQL istemci kurulumu sonucu")
    kosul = str(adim.get("if", ""))
    assert "pg_istemci" in kosul and "durum" in kosul, kosul
    assert "exit 1" in str(adim.get("run", "")), adim


def test_nothing_ran_is_its_own_named_red() -> None:
    testler = _adim("Run PostgreSQL integration gates")
    betik = str(testler.get("run", ""))
    assert "HİÇBİR PostgreSQL dosyası koşulmadı" in betik, (
        "'hiçbir şey koşmadı' hâli kendi adıyla kırmızı olmuyor; dışarıdan "
        "iptal edilmiş bir koşudan ayırt edilemez"
    )
    assert "PG_KOSULAN_DOSYA=" in betik, "koşulan dosya sayısı raporlanmıyor"
