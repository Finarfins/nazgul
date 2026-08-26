"""``.github/scripts/playwright_hazirla.py`` kapısı.

Betiğin işi, bir ALTYAPI YAVAŞLAMASININ e2e kapsamını sessizce sıfıra
indirmesini engellemek. Bu dosya üç şeyi ölçüyor:

1. **Üç durum AYIRT EDİLEBİLİR.** ``hazir`` / ``kuruldu`` / ``YOK`` farklı
   çıkış kodu ve farklı çıktı üretmeli. "Kurulum düştü" ile "testler düştü"
   ile "hiçbir şey koşmadı" aynı görünürse kusur yine görünmez olur — asıl
   kusur zaten buydu.
2. **SINIR GERÇEKTEN KESİYOR.** Bildirilen bir sınır, bağlayan bir sınır
   değildir. ``subprocess.run(shell=True, timeout=N)`` kabuğu öldürür ama
   torunu öldürmez; ölçülmüş bir vakada 2 sn'lik sınır 30.1 sn sürmüştü. Bu
   yüzden burada süre ÖLÇÜLÜYOR, sınırın varlığı okunmuyor.
3. **İş tanımı betiği çağırıyor.** Betik mükemmel olsa da ``ci.yml`` hâlâ
   sınırsız komutu çağırıyorsa hiçbir şey değişmemiş olur.
"""
from __future__ import annotations

import importlib.util as _iu
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[2]
BETIK = KOK / ".github" / "scripts" / "playwright_hazirla.py"
CI = KOK / ".github" / "workflows" / "ci.yml"


def _modul():
    spec = _iu.spec_from_file_location("pw_hazirla", BETIK)
    modul = _iu.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_betik_var_ve_yuklenebiliyor() -> None:
    assert BETIK.is_file(), f"betik yok: {BETIK}"
    assert _modul() is not None


# ---------------------------------------------------------------------------
# 2. SINIR GERÇEKTEN KESİYOR MU
# ---------------------------------------------------------------------------

def test_sinir_gercekten_kesiyor() -> None:
    """Sınırı aşan komut, sınıra YAKIN bir sürede kesilmeli.

    Bildirilen sınır ile bağlayan sınır aynı şey değil. Ölçülen vaka:
    ``shell=True`` ile 2 sn'lik sınır 30.1 sn sürdü, çünkü kabuk öldü ama
    torun yaşadı. Bu test SÜREYİ ölçüyor.
    """
    modul = _modul()
    uyutan = [sys.executable, "-c", "import time; time.sleep(60)"]
    baslangic = time.monotonic()
    kod, sure, _ = modul.sinirli_kostur(uyutan, 2)
    gecen = time.monotonic() - baslangic

    assert kod is None, "sınırı aşan komut None döndürmeli (zaman aşımı işareti)"
    assert gecen < 20, (
        f"SINIR BAĞLAMADI: 2 sn'lik sınır {gecen:.1f} sn sürdü. Süreç grubu "
        "öldürülmüyor olabilir; kabuğu öldürmek torunu öldürmez."
    )
    assert sure >= 2, f"sınırdan önce dönmüş olamaz: {sure:.1f} sn"


def test_sinir_icinde_biten_komut_kesilmiyor() -> None:
    """KARŞI YÖN: sınırın altındaki komut normal sonucunu vermeli.

    Yalnız 'kesiyor' tarafını ölçmek yetmez; her şeyi kesen bir sınır da
    kapıyı yeşil gösterirdi.
    """
    modul = _modul()
    kod, sure, cikti = modul.sinirli_kostur(
        [sys.executable, "-c", "print('bitti')"], 30
    )
    assert kod == 0, f"normal komut 0 dönmeliydi, {kod} döndü"
    assert "bitti" in (cikti or "")
    assert sure < 30


# ---------------------------------------------------------------------------
# 3. ÜÇ DURUM AYIRT EDİLEBİLİR Mİ
# ---------------------------------------------------------------------------

def _kostur(env_ek: dict[str, str]) -> subprocess.CompletedProcess:
    # ÇOCUĞUN ve EBEVEYNİN kodlaması AÇIKÇA eşitlenir; İKİ YARI DA GEREKLİ.
    # Betiğin mesajları Türkçe: `düştü`, `SINIRI aştı`. Çocuk yerel ayarın
    # kodlamasında yazarsa (Windows'ta cp1254) ve ebeveyn utf-8 çözerse
    # harfler `�`ye dönüşür, `in` denetimleri sessizce başarısız olur ve
    # test "mesaj yanlış" der — oysa mesaj doğrudur, ÖLÇÜM bozuktur.
    # `errors="replace"` bilerek YOK: bozulmayı sessizce yutar.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_ek)
    return subprocess.run(
        [sys.executable, str(BETIK)],
        capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=str(KOK), timeout=180,
    )


def test_durum_kuruldu_sifir_dondurur(tmp_path) -> None:
    """Eksik olan sınır içinde kurulursa: çıkış 0 ve 'kuruldu'."""
    sonuc = _kostur({
        "PW_TARAYICI_KOMUT": f'"{sys.executable}" -c "pass"',
        "PW_BAGIMLILIK_KOMUT": f'"{sys.executable}" -c "pass"',
        "PW_CALISMA_DIZINI": str(KOK),
        # Tarayıcı zaten varsa 'hazir' yolundan geçer; iki yol da 0 döner.
    })
    # Bu ortamda tarayıcı yoksa sahte komut hiçbir şey kurmaz ve betik
    # "komut geçti ama araç yok" demeli — sessiz başarıyı reddetmeli.
    if sonuc.returncode == 1:
        assert "sessiz başarı" in sonuc.stderr or "bulunamadı" in sonuc.stderr, sonuc.stderr
    else:
        assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr


def test_durum_yok_sinir_asiminda_bir_dondurur() -> None:
    """Kurulum sınırı aşarsa: çıkış 1, ``::error::`` ve SINIR gerekçesi."""
    sonuc = _kostur({
        "PW_TARAYICI_KOMUT": f'"{sys.executable}" -c "import time; time.sleep(60)"',
        "PW_TARAYICI_SINIR_SN": "2",
        "PW_CALISMA_DIZINI": str(KOK),
    })
    if "tarayıcı hazir" in sonuc.stdout:
        pytest.skip("bu makinede tarayıcı önbellekte var; sınır yolu tetiklenmiyor")
    assert sonuc.returncode == 1, sonuc.stdout + sonuc.stderr
    assert "::error::" in sonuc.stderr
    assert "SINIRI aştı" in sonuc.stderr, sonuc.stderr


def test_durum_yok_komut_dusunce_bir_dondurur() -> None:
    """Kurulum komutu düşerse: çıkış 1 ve KOMUT gerekçesi — sınırdan AYRI."""
    sonuc = _kostur({
        "PW_TARAYICI_KOMUT": f'"{sys.executable}" -c "raise SystemExit(3)"',
        "PW_CALISMA_DIZINI": str(KOK),
    })
    if "tarayıcı hazir" in sonuc.stdout:
        pytest.skip("bu makinede tarayıcı önbellekte var; komut yolu tetiklenmiyor")
    assert sonuc.returncode == 1
    assert "::error::" in sonuc.stderr
    assert "koduyla düştü" in sonuc.stderr, sonuc.stderr


def test_iki_yok_gerekcesi_birbirinden_ayirt_ediliyor() -> None:
    """Sınır aşımı ile komut düşmesi AYNI metni vermemeli."""
    sinir = _kostur({
        "PW_TARAYICI_KOMUT": f'"{sys.executable}" -c "import time; time.sleep(60)"',
        "PW_TARAYICI_SINIR_SN": "2", "PW_CALISMA_DIZINI": str(KOK),
    })
    komut = _kostur({
        "PW_TARAYICI_KOMUT": f'"{sys.executable}" -c "raise SystemExit(3)"',
        "PW_CALISMA_DIZINI": str(KOK),
    })
    if "tarayıcı hazir" in sinir.stdout:
        pytest.skip("tarayıcı önbellekte; iki YOK yolu da tetiklenmiyor")
    assert sinir.stderr != komut.stderr, (
        "iki farklı arıza aynı mesajı veriyor; ayırt edilemez"
    )


# ---------------------------------------------------------------------------
# 4. İŞ TANIMI BETİĞİ ÇAĞIRIYOR MU
# ---------------------------------------------------------------------------

def test_ci_sinirsiz_kurulumu_artik_cagirmiyor() -> None:
    """``--with-deps`` doğrudan çağrısı KALKMIŞ olmalı.

    Betik yazılıp iş tanımı eski komutu çağırmaya devam ederse hiçbir şey
    değişmemiş olur; kusur aynen kalır.
    """
    # YORUM SATIRLARI ELENİR. Eski komut, neden kaldırıldığını anlatan
    # açıklamada geçiyor; metin taraması onu GERÇEK ÇAĞRI sanardı — kod
    # biçimli ama icra edilmeyen metni saymak bu depoda daha önce de yanılttı.
    metin = "\n".join(
        satir for satir in CI.read_text(encoding="utf-8").splitlines()
        if not satir.lstrip().startswith("#")
    )
    assert "playwright install --with-deps" not in metin, (
        "ci.yml hâlâ sınırsız `playwright install --with-deps` çağırıyor"
    )
    assert "playwright_hazirla.py" in metin, (
        "ci.yml hazırlık betiğini çağırmıyor"
    )


def test_hazirlik_sonucu_ayri_adimda_kirmiziya_donuyor() -> None:
    """Sonuç KAYDEDİLİP ayrı bir adımda kırmızıya dönmeli.

    K1 kapısı yayın bariyerindeki işlerde ``continue-on-error`` reddediyor ve
    haklı: hazırlık düşerse iş kırmızı olmalı, sessizce devam etmemeli.
    """
    metin = CI.read_text(encoding="utf-8")
    e2e = metin[metin.index("\n  e2e:"):]
    e2e = e2e[: e2e.index("\n  ", 1) if "\n  " in e2e[1:] else len(e2e)]
    assert "continue-on-error" not in e2e, (
        "e2e işinde continue-on-error var; hazırlık arızası sessizleşir"
    )


# ---------------------------------------------------------------------------
# 5. ÇIKIŞ KODU KORUNUYOR MU — YAZIM DEĞİL, ÖZELLİK
# ---------------------------------------------------------------------------
#
# İLK HÂLİ YANLIŞTI ve inceleme haklı olarak reddetti. Kapı şöyleydi: "bu
# satırda `|` olmasın". Bu, `tee` YAZIMINI kapatıyordu; korunması gereken
# ÖZELLİĞİ değil. Aynı arızayı üreten başka yazımlar açıkta kalıyordu ve
# ikisi o head'de CANLI birer yanlış-yeşil yoluydu:
#
#     python … || true                       -> çıkış 0   YUTULDU
#     set -e; python … | cat                 -> çıkış 0   YUTULDU
#     python … ; exit 0                      -> çıkış 0   YUTULDU
#     if python …; then :; fi                -> çıkış 0   YUTULDU
#
# Yazım saymak BLOCKLIST'tir ve blocklist yapısı gereği fail-open: listede
# olmayan her yeni yazım sessizce geçer. Bu depo bu ölçütü beş kez reddetti.
#
# KORUNMASI GEREKEN ÖZELLİK: **adımın çıkış kodu, betiğin çıkış kodudur.**
#
# Kabuk gramerinin tamamını denetlemek mümkün değil, ama gerek de yok:
# adımın İZİN VERİLEN biçimi tek bir şeye daraltılabilir. Bu bir ALLOWLIST'tir
# ve fail-closed'dır — tanımadığı her şeyi reddeder, dolayısıyla YARIN icat
# edilecek bir yazım da otomatik olarak reddedilir.

HAZIRLIK_KOMUTU = re.compile(
    r"^python \.\./\.github/scripts/playwright_hazirla\.py$"
)


def _hazirlik_adimi() -> dict:
    """``ci.yml``den ``pw_hazirlik`` adımını YAML olarak okur.

    Metin taraması değil ayrıştırma: yorumdaki bir komut ile gerçek bir
    komutu ayırt edebilmek için (bu dosyada bir kez yanılttı).
    """
    yaml = pytest.importorskip("yaml")
    tanim = yaml.safe_load(CI.read_text(encoding="utf-8"))
    for adim in tanim["jobs"]["e2e"]["steps"]:
        if adim.get("id") == "pw_hazirlik":
            return adim
    raise AssertionError("e2e işinde `id: pw_hazirlik` adımı yok")


def _mantiksal_satirlar(kabuk: str) -> list[str]:
    """Yorum ve boş satırlar atılmış, kırpılmış satırlar."""
    return [
        satir.strip()
        for satir in kabuk.splitlines()
        if satir.strip() and not satir.strip().startswith("#")
    ]


def test_hazirlik_adiminin_cikis_kodu_betigin_cikis_kodudur() -> None:
    """Adım YALNIZ betiği çağırır; başka hiçbir mantıksal komut olamaz.

    ALLOWLIST, blocklist değil. "Şu yazımlar yasak" demek yerine "yalnız şu
    biçim serbest" deniyor; böylece `|| true`, `; exit 0`, borular, `if`
    sarmalayıcıları ve HENÜZ İCAT EDİLMEMİŞ yazımlar tek kuralla düşer.

    Bedeli bilinçli: çıkış kodunu KORUYAN bazı biçimler de reddediliyor
    (ör. `python … > log.txt` ya da `set -eo pipefail; python … | cat`).
    Bkz. DEKLARE EDİLMİŞ SINIR.
    """
    adim = _hazirlik_adimi()
    kabuk = adim.get("run", "")
    satirlar = _mantiksal_satirlar(kabuk)

    assert len(satirlar) == 1, (
        "hazırlık adımı TEK mantıksal komut olmalı; betiğin çıkış kodunu "
        "değiştirebilecek başka bir komut bulunamaz.\n"
        f"  bulunan {len(satirlar)} satır: {satirlar}"
    )
    assert HAZIRLIK_KOMUTU.match(satirlar[0]), (
        "hazırlık adımı YALNIZ betiğin doğrudan çağrısı olabilir; kabuk "
        "denetim akışı (`||`, `&&`, `;`, `|`, `if`, yönlendirme) adımın "
        "çıkış kodunu betiğinkinden AYIRABİLİR ve arıza sessizce yeşil "
        "görünür.\n"
        f"  bulunan: {satirlar[0]!r}\n"
        f"  beklenen desen: {HAZIRLIK_KOMUTU.pattern}"
    )


#: Sonuç adımının `if:` ifadesi — TEK elemanlı allowlist, boşluk normalize.
#: Değerlendirici yazmak yerine TANIMAK. Yeni bir biçim gerekirse buraya
#: bilinçli eklenir ve mutasyon tablosuna hem KABUL hem RED satırı girer;
#: sessizce genişlemesin diye küme burada, gözün önünde duruyor.
SONUC_KOSULU_IZINLI = frozenset({
    "always() && steps.pw_hazirlik.outcome != 'success'",
})

#: Gramerlerin ZEMİNİ. Beyan yokluğu da serbesttir (GitHub'ın Linux
#: varsayılanı `bash -e`). Başka her yorumlayıcı reddedilir: gramerler bash
#: semantiğine dayanıyor ve zemin kayarsa yapısal garanti yok olur.
KABUK_IZINLI = frozenset({"bash"})

#: Sonuç adımının SON satırı: sıfır olmayan SABİT kodla çıkış.
#: `exit $KOD` bilerek dışarıda — değişkenin 0 ya da boş olmadığını
#: okuyarak kanıtlayamayız, ve kanıtlanamayan şey fail-closed bir kapıda
#: geçemez.
SONUC_CIKISI = re.compile(r"exit [1-9][0-9]*")

#: Çıkıştan önceki satırlar: YALNIZ tam tırnaklı `echo`. Tırnak içinde
#: `$`, backtick ve ters bölü YASAK — komut ikamesi ya da genişleme, dışarıdan
#: gelen bir değerin akışı saptırmasına yol açabilirdi. `;` tırnak İÇİNDE
#: serbesttir çünkü orada düz metindir (mevcut mesaj bir tane taşıyor).
SONUC_MESAJI = re.compile(r'echo "[^"$`\\]*"')


def _sonuc_adimi() -> dict:
    """`pw_hazirlik`e bakan TEK adım. Birden çoksa hata.

    Eski hâli ilk eşleşeni döndürüyordu. İkinci bir adım eklenseydi bütün
    kapılar birinciyi ölçmeye devam eder, ikincisi hiç denetlenmezdi —
    yani kapı, denetlediğini sandığı yüzeyin bir parçasını sessizce
    dışarıda bırakırdı.
    """
    yaml = pytest.importorskip("yaml")
    tanim = yaml.safe_load(CI.read_text(encoding="utf-8"))
    eslesen = [
        adim for adim in tanim["jobs"]["e2e"]["steps"]
        if "pw_hazirlik" in str(adim.get("if", ""))
    ]
    assert eslesen, "hazırlık sonucuna bakan bir adım yok"
    assert len(eslesen) == 1, (
        "`pw_hazirlik`e bakan BİRDEN ÇOK adım var; kapılar yalnız birini "
        f"ölçer ve diğeri denetimsiz kalır. bulunan: {len(eslesen)}"
    )
    return eslesen[0]


def test_hazirlik_sonucu_adimi_sonuca_bagli() -> None:
    """BAĞIMLILIK + ERİŞİLEBİLİRLİK: koşul TAM olarak izinli biçimlerden biri.

    ÖNCEKİ HÂLİ FAIL-OPEN'DI ve inceleme (4980721272) haklı olarak reddetti:
    kapı yalnız koşulun ``outcome`` ve ``success`` KELİMELERİNİ taşıdığını
    sınıyordu — yani JETONUN VARLIĞINI. Jeton, hiç doğru olamayacak bir
    ifadede de bulunur::

        always() && false && steps.pw_hazirlik.outcome != 'success'

    Bu, eski iddiayı GEÇERDİ; adım ise hiç koşmazdı. Zorlama kapısı `run:`
    bloğunu kusursuz çivilese bile o blok hiç icra edilmediği için iki kapı
    arasında FAIL-OPEN bir dikiş kalırdı.

    MERDİVENİN DÖRDÜNCÜ BASAMAĞI. Üçüncüsü `exit 1`in yazılı ama erişilemez
    olmasıydı; bu, `if:` ifadesinin doğru jetonu taşıyıp yine erişilemez
    olması. Aynı varlık-özellik hatası, bir katman yukarıda.

    GitHub İFADE DEĞERLENDİRİCİSİ YAZILMADI: izin verilen ifade kümesi TEK
    elemana daraltıldı. Değerlendirmek yerine tanımak.

    `always()` ZORUNLU ve bu kozmetik değil: onsuz adımın örtük koşulu
    ``success()``tür, yani önceki adım düştüğünde adım ATLANIR. Hazırlığın
    düşmesi tam olarak o durumdur — `always()` olmadan zorlama adımı, ihtiyaç
    duyulan TEK ANDA hiç koşmaz. Mutasyon tablosunda kendi satırı var.
    """
    kosul = " ".join(str(_sonuc_adimi()["if"]).split())
    assert kosul in SONUC_KOSULU_IZINLI, (
        "sonuç adımının koşulu TAM olarak izinli biçimlerden biri olmalı. "
        "Jeton araması yetmez: doğru jetonu taşıyan ama hiç doğru olamayan "
        "bir ifade (`… && false && …`) ya da `always()` taşımayan bir ifade "
        "kapıyı geçer, adım hiç koşmaz ve zorlama vakuma düşer.\n"
        f"  bulunan: {kosul!r}\n"
        f"  izinli:  {sorted(SONUC_KOSULU_IZINLI)}"
    )


def _kabuk_beyanlari() -> list[tuple[str, str]]:
    """Sonuç adımının kabuğunu belirleyebilecek TÜM beyanlar.

    Üç katman var ve üçü de zemini değiştirebilir: iş akışı geneli, işin
    kendisi, adımın kendisi. Yalnız adıma bakmak YETMEZ — iş düzeyinde
    ``defaults.run.shell`` eklemek, adıma hiç dokunmadan yorumlayıcıyı
    değiştirir ve adım-düzeyi bir denetim bunu göremez.
    """
    yaml = pytest.importorskip("yaml")
    tanim = yaml.safe_load(CI.read_text(encoding="utf-8"))
    bulunan: list[tuple[str, str]] = []

    def _varsayilan(kap, ad):
        kabuk = ((kap or {}).get("defaults") or {}).get("run", {}).get("shell")
        if kabuk is not None:
            bulunan.append((ad, str(kabuk)))

    _varsayilan(tanim, "iş akışı defaults.run.shell")
    _varsayilan(tanim["jobs"]["e2e"], "e2e işi defaults.run.shell")
    for ad, adim in (("pw_hazirlik adımı shell:", _hazirlik_adimi()),
                     ("sonuç adımı shell:", _sonuc_adimi())):
        if adim.get("shell") is not None:
            bulunan.append((ad, str(adim["shell"])))
    return bulunan


def test_gramerin_zemini_bash_olarak_civilenmis() -> None:
    """ZEMİN: gramerlerin dayandığı yorumlayıcı BAŞKA bir şey olamaz.

    İnceleme (4980721272) bunu CANLI bir kaçış olarak ölçtü: sonuç adımına
    ``shell: python`` enjekte edildi, kapı KABUL ETTİ, çıkış 0, yeşil.

    Her iki gramer de bash semantiğine dayanıyor — sonuç adımında "dallanmasız
    ``echo``* + ``exit N``, dolayısıyla son satır her yolda koşar"; hazırlık
    adımında "tek komut, adımın çıkış kodu betiğinkidir". Yorumlayıcı
    değişirse bu YAPISAL GARANTİ ORTADAN KALKAR ve gramerler YEŞİL KALARAK
    vakuma düşer.

    Bunu deklare edilmiş sınır olarak bırakmak YETMEZDİ, ve ayrım şu:
    kapının ÇALIŞMAYA DEVAM ETTİĞİ bir bedeli deklare etmek meşrudur; kapıyı
    VAKUMA DÜŞÜREN bir yolu deklare etmek değildir. Birincisi MALİYET,
    ikincisi DELİK. Delik not edilmez, kapatılır.

    Yine daraltma: bash ANALİZ EDİLMİYOR, başka zemin KABUL EDİLMİYOR.

    BEYAN YOKLUĞU DA REDDEDİLİR — ve bu, bu turda deklare edilecek sınırları
    tararken bulunan İKİNCİ bir delikti. İlk hâl "beyan yoksa serbest"
    diyordu, gerekçesi GitHub'ın Linux varsayılanının ``bash -e`` olmasıydı.
    Ama o varsayılan BU DEPONUN DIŞINDA bir karardır: sağlayıcı bir gün onu
    değiştirirse hiçbir dosya değişmeden zemin kayar ve kapı YEŞİL KALIR.
    Tam olarak vakum koşulu. Bir maliyet değil, delik — dolayısıyla not
    edilmedi, kapatıldı: iki adım da artık ``shell: bash`` yazmak ZORUNDA,
    ve bu sayede garanti sağlayıcının varsayılanına HİÇ dayanmıyor.

    ``bash -e``nin kendisi garantiyi zayıflatmaz, GÜÇLENDİRİR: `echo` düşse
    betik yine sıfır olmayan kodla biter, yani adım yine kırmızıdır.
    """
    beyanlar = _kabuk_beyanlari()
    for nerede, kabuk in beyanlar:
        assert kabuk in KABUK_IZINLI, (
            "gramerlerin zemini bash olmalı; başka bir yorumlayıcı seçilirse "
            "yapısal garanti ortadan kalkar ve kapı YEŞİL KALARAK vakuma "
            "düşer (ölçüldü: `shell: python` kabul ediliyordu).\n"
            f"  beyan yeri: {nerede}\n"
            f"  bulunan:    {kabuk!r}\n"
            f"  izinli:     {sorted(KABUK_IZINLI)}"
        )
    # AÇIK beyan ZORUNLU: sessiz varsayılana yaslanmak, zemini sağlayıcının
    # kararına bırakır ve o karar değişirse kapı bunu göremez.
    beyan_yerleri = {nerede for nerede, _ in beyanlar}
    for gerekli in ("pw_hazirlik adımı shell:", "sonuç adımı shell:"):
        assert gerekli in beyan_yerleri, (
            f"{gerekli} AÇIKÇA beyan edilmemiş. Varsayılana yaslanmak zemini "
            "GitHub'ın kararına bırakır; sağlayıcı varsayılanı değiştirirse "
            "hiçbir dosya değişmeden gramerlerin dayanağı yok olur ve bu kapı "
            "bunu göremez.\n"
            f"  bulunan beyanlar: {sorted(beyan_yerleri)}"
        )


def test_hazirlik_arizasi_isi_kirmiziya_dondurur() -> None:
    """ZORLAMA: hazırlık düşerse İŞ KIRMIZI olmalı.

    ÖNCEKİ HÂLİ VAKUMDU ve inceleme (4973113270) haklı olarak reddetti:
    kapı yalnız `if:` koşulunun `steps.pw_hazirlik.outcome`a değindiğini
    sınıyordu. Sonuç adımının `run:` bloğundan `exit 1` SİLİNSE kapı yine
    yeşil kalıyordu — yani KOŞULU ölçüyordu, ÖZELLİĞİ değil. O hâlde
    hazırlık düşer, adım yalnız bir mesaj basar, iş YEŞİL biter ve e2e
    testleri hazırlanmamış ortamda koşar; PR'ın önlemeyi iddia ettiği
    sözleşme ihlalinin ta kendisi.

    Bu, bir katman yukarıda kendi düzelttiğim dersin aynısı: MUTASYONU
    GEÇEN BİR KAPI, DOĞRU ÖZELLİĞİ ÖLÇTÜĞÜNÜ KANITLAMAZ. Mutasyon "bu test
    bir şey ölçüyor mu" der; "doğru şeyi mi ölçüyor" demez.

    KARDEŞ PR: #82'nin süiti bu özelliği zaten çiviliyor
    (``test_install_failure_has_its_own_named_red``). Yani bu yeni bir
    tasarım sorunu değil, iki kardeş PR arasındaki BOŞLUKTU; aynı özellik
    burada da çivileniyor.

    İKİNCİ HÂLİ DE REDDEDİLDİ (inceleme 4980545213) ve haklıydı: kapı
    ``exit [1-9][0-9]*`` desenini satır satır arıyordu, yani VARLIK
    ölçüyordu, ERİŞİLEBİLİRLİK değil. Ölçülmüş iki karşı örnek::

        if false; then
          exit 1
        fi                                  -> kapı GEÇİRİYORDU

        if [ "$NEVER" = "1" ]; then
          exit 1
        fi                                  -> kapı GEÇİRİYORDU

    İkisinde de `exit 1` yazılıdır ama HİÇ KOŞMAZ; hazırlık düşer, adım
    sessizce 0 döner, iş yeşil biter.

    Bu, bu PR'da üçüncü kez tırmanılan aynı merdiven: yazım -> özellik,
    bağımlılık -> zorlama, ve şimdi VARLIK -> ERİŞİLEBİLİRLİK. Her
    basamakta vekil ölçüt kendi mutasyonunu geçmişti.

    ÇÖZÜM: bash erişilebilirlik analizi YAZILMADI. Hazırlık adımında işe
    yarayan araç burada da yetiyor — KARMAŞIKLIĞI ANALİZ ETMEK YERİNE
    REDDETMEK. İzin verilen tek şekil::

        echo "..."          (sıfır ya da daha çok, tamamı tırnaklı)
        exit <sıfır olmayan>   (SON mantıksal satır)

    Bu şekilde erişilebilirlik ANALİZ EDİLMEZ, YAPISAL OLARAK GARANTİDİR:
    blokta dallanma yoktur, `echo` betiği ne durdurabilir ne saptırabilir,
    dolayısıyla son satır her yolda koşar. Tek yol varsa erişilebilirlik
    sorusu ortadan kalkar.

    `if`/`fi`, `&&`, `||`, `;` ile zincirleme, `set -e`, alt kabuk, fonksiyon,
    `exit $DEGISKEN`, önce gelen bir `exit 0` — hepsi TANIMSIZ olduğu için
    düşer; henüz icat edilmemiş sarmalayıcılar da öyle.
    """
    adim = _sonuc_adimi()
    satirlar = _mantiksal_satirlar(str(adim.get("run", "")))

    assert satirlar, "sonuç adımının çalıştırdığı bir şey yok"

    # SON satır: sıfır olmayan sabit kodla çıkış. `exit $KOD` bilerek
    # reddedilir — değişken boş ya da 0 olabilir ve bunu okuyarak bilemeyiz.
    assert SONUC_CIKISI.fullmatch(satirlar[-1]), (
        "sonuç adımının SON mantıksal satırı sıfır olmayan sabit bir kodla "
        "çıkış olmalı. Aksi hâlde `exit 1` erişilemez bir dalda durabilir "
        "(ölçülmüş: `if false; then exit 1; fi`) — yazılıdır ama koşmaz, "
        "hazırlık düşse bile iş YEŞİL biter.\n"
        f"  bulunan son satır: {satirlar[-1]!r}\n"
        f"  beklenen desen:    {SONUC_CIKISI.pattern}"
    )
    # Öncesindeki her şey YALNIZ tırnaklı mesaj olabilir. Genişleme,
    # yönlendirme ve denetim akışı taşımadıkları için son satıra giden yolu
    # ne saptırabilir ne kesebilirler.
    for satir in satirlar[:-1]:
        assert SONUC_MESAJI.fullmatch(satir), (
            "sonuç adımında çıkıştan önce YALNIZ tırnaklı `echo` olabilir; "
            "denetim akışı son satırı erişilemez kılabilir ve arıza sessizce "
            "yeşile döner.\n"
            f"  bulunan: {satir!r}\n"
            f"  beklenen desen: {SONUC_MESAJI.pattern}"
        )


# --- DEKLARE EDİLMİŞ SINIR (hazırlık adımı grameri) ------------------------
#
# ÖLÇMEZ: kabuğun genelinde çıkış kodunun korunup korunmadığını. Denetlenen
# şey `pw_hazirlik` adımının `run:` bloğudur; başka bir adım ya da başka bir
# iş aynı hatayı yapabilir ve bu kapı görmez.
#
# BEDELİ BİLİNÇLİ: gramer, çıkış kodunu KORUYAN bazı biçimleri de reddeder.
# Ölçüldü — ikisi de kırmızı olur, oysa ikisi de güvenlidir:
#     set -eo pipefail; python … | cat     -> çıkış 1 korunur, yine de RED
#     python … > out.log                   -> çıkış 1 korunur, yine de RED
# Bu, allowlist'in doğasıdır ve kabul edilmiştir: fail-closed bir kapı
# tanımadığını reddeder. Blocklist tersini yapardı — tanımadığını GEÇİRİRDİ,
# ve bu kapının ilk hâli tam olarak öyle olduğu için reddedildi.
#
# AÇMA KOŞULU: adımın gerçekten yönlendirme ya da pipefail'lı bir boruya
# ihtiyacı olursa, gramer o biçimi KAPSAYACAK şekilde bilinçli genişletilir
# ve bu mutasyon tablosuna yeni bir satır eklenir. Genişletme sessiz
# olmamalı; kapının gevşediği diff'te görünmelidir.


# --- DEKLARE EDİLMİŞ SINIRLAR — HER BİRİ MALİYET Mİ DELİK Mİ ---------------
#
# AYRIM: kapının ÇALIŞMAYA DEVAM ETTİĞİ bir kısıt MALİYETTİR ve deklare
# edilebilir. Kapıyı YEŞİL KALARAK VAKUMA DÜŞÜREN bir yol DELİKTİR ve
# deklare edilemez — kapatılır. Bu turda iki delik bulundu ve ikisi de
# kapatıldı; aşağıdaki listede yalnız maliyetler kaldı.
#
# KAPATILAN DELİKLER (bu turda):
#   * ZEMİN BEYANSIZDI. `shell:` yokken gramerlerin dayandığı bash,
#     GitHub'ın varsayılanıydı — bu deponun DIŞINDA bir karar. Sağlayıcı onu
#     değiştirse hiçbir dosya değişmeden garanti yok olur, kapı yeşil kalırdı.
#     Kapatıldı: iki adım da AÇIK `shell: bash` yazmak zorunda; üç katmanda
#     da (iş akışı / iş / adım) başka yorumlayıcı reddediliyor.
#   * `if:` JETON ARAMASIYDI. `… && false && …` jetonu taşır, hiç doğru
#     olamaz; zorlama adımı hiç koşmaz ve iki kapı arasında fail-open dikiş
#     kalırdı. Kapatıldı: ifade TEK elemanlı allowlist'e daraltıldı.
#
# KALAN SINIRLAR — HEPSİ MALİYET:
#
# 1. MALİYET — KAPSAM: yalnız `e2e` işindeki `pw_hazirlik` ve ona bakan tek
#    adım denetlenir. Başka bir iş aynı hatayı yapabilir ve bu kapı görmez.
#    Kapı kendi yüzeyinde çalışmaya devam eder; iddiası da o yüzeyle sınırlı.
#    (Bulucu belirsizliği artık delik değil: `pw_hazirlik`e bakan İKİNCİ bir
#    adım eklenirse kapı kırmızı olur — biri denetimsiz kalamaz.)
#
# 2. MALİYET — ALLOWLIST DARLIĞI: çıkış kodunu KORUYAN bazı biçimler de
#    reddedilir (`printf` ile mesaj, `exit $KOD`, `;` ile zincirleme,
#    `set -eo pipefail; … | cat`, `… > log`). Fail-closed bir kapı
#    tanımadığını reddeder; yanlış yönde hata yapar, tehlikeli yönde değil.
#
# 3. MALİYET — TEK İFADE: `if:` için tek bir biçim serbest. Meşru bir
#    ihtiyaç doğarsa küme bilinçli genişletilir ve mutasyon tablosuna hem
#    KABUL hem RED satırı girer. Genişleme diff'te görünür; sessiz olamaz.
#
# 4. MALİYET — `always()` ANLAMI: `always()`ün "önceki adım düşse de koş"
#    demek olduğu GitHub'ın belgelenmiş davranışıdır ve bu kapı onu
#    DOĞRULAMAZ, VARSAYAR. Ama artık ifadeyi bir bütün olarak çivilediği
#    için varsayım sabittir: ifade değişirse kapı kırmızı olur.
#
# 5. MALİYET — GÖZLEM EKSİĞİ (kapının değil, kanıtın sınırı): "sınır
#    aşıldığında iş kırmızı olur" iddiası yerel mutasyonla ve kapı testiyle
#    kanıtlı; GERÇEK bir asılmada henüz GÖZLENMEDİ. Doğal bir asılma
#    beklenemez. Bu bir vakum değil, eksik gözlemdir.
#
# AÇMA KOŞULU (hepsi için): daraltmanın gevşetilmesi gerekiyorsa gevşeme
# bilinçli yapılır, mutasyon tablosuna İKİ YÖNLÜ satır eklenir, ve gevşemenin
# kaldırdığı garanti ayrıca kanıtlanır.
#
# --- (aşağısı: sonuç adımı gramerinin ayrıntısı) ---------------------------
#
# Bu gramer bash ERİŞİLEBİLİRLİK ANALİZİ YAPMAZ. İhtiyaç duymaması, izin
# verdiği şeklin dallanmasız olmasındandır: `echo`* + `exit <sabit>`. Tek yol
# varsa "bu satır koşar mı" sorusu sorulmaz. Analiz edilmedi çünkü GEREKMEDİ;
# gerekseydi bu blok onu deklare etmek zorunda kalırdı.
#
# ÖLÇMEZ — kapsamı bilerek dar:
#   * YALNIZ `pw_hazirlik`e bakan adımı denetler. Başka bir adım ya da başka
#     bir iş aynı hatayı yapabilir; bu kapı görmez.
#   * `if:` İFADESİNİN doğruluğunu ölçmez. Koşul yanlış yazılıp adım hiç
#     koşmazsa gramer yine yeşildir; bağlılığı ayrı bir kapı çiviliyor
#     (``test_hazirlik_sonucu_adimi_sonuca_bagli``), ama o da ifadenin
#     MANTIĞINI değil, `outcome`a DEĞDİĞİNİ ölçer.
#   * Runner'ın adımı `bash` ile koşturduğunu VARSAYAR. `shell:` anahtarı
#     eklenip başka bir yorumlayıcı seçilirse gramer bunu fark etmez.
#
# BEDELİ BİLİNÇLİ: çıkış kodunu KORUYAN bazı biçimler de reddedilir —
# `printf` ile mesaj, `exit $KOD`, birden çok komutu `;` ile zincirleme.
# Allowlist'in doğası budur. Blocklist tersini yapardı: tanımadığını
# GEÇİRİRDİ, ve bu kapının ilk iki hâli tam olarak öyle olduğu için
# reddedildi (yazım sayan blocklist, sonra varlık arayan desen).
#
# AÇMA KOŞULU: adımın gerçekten dallanmaya ihtiyacı olursa gramer bilinçli
# genişletilir VE erişilebilirlik o noktada ayrıca kanıtlanmak zorundadır —
# çünkü genişletmenin kaldırdığı şey tam olarak yapısal garantidir.
# Genişletme sessiz olmamalı; kapının gevşediği diff'te görünmelidir.
