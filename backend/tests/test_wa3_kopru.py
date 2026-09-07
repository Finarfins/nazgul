"""WA3-core KÖPRÜ: imza sözleşmesi ve fail-closed istemci (`app/whatsapp/kopru.py`).

Konu: `docs/whatsapp/KOPRU_SOZLESMESI.md` (FIXTURE — üç altın vektör buradan
OKUNUR, koda kopyalanmaz), `app/whatsapp/kopru.py` ve `app/config.py`ye
eklenen üç ayar (`harman_kopru_url`, `harman_kopru_sirri`,
`harman_kopru_sirri_ikincil`).

ÖLÇÜLEN EKSİK: kaynak (`nazgul_website` danisman.py) sırrı DÜZ METİN başlıkta
taşıyordu; erişim logu ya da proxy sırrı görürdü. Burada sır tel üzerinden
geçmez: istek zaman damgası + nonce + gövde özeti üzerinden HMAC ile
imzalanır ve web ucu aynı hesabı yansıtır.

BU PR'DA KÖPRÜ HİÇBİR YERE BAĞLI DEĞİL: rota yok, tablo yok, main.py
kablosu yok; varsayılan ayar boş ve boş ayar ağa çıkmaz.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `imza_metni`nden gövde özetini düşürmek (imza gövdeyi KAPSAMAZ)
                                       -> ALTIN VEKTÖR kapısı (üç vektörün
                                          üçü) ve GÖVDE DEĞİŞTİ kapısı KIRMIZI
  * `dogrula`dan zaman kayması denetimini kaldırmak (kayma SINIRSIZ)
                                       -> KAYMA 301 kapısı KIRMIZI (iki yön)
  * `dogrula`da yalnız ilk sırrı denemek (İKİNCİL SIR YOK SAYILIR)
                                       -> İKİNCİL SIR kapısı KIRMIZI
  * `imzala`yı ikincil sırla imzalatmak
                                       -> BİRİNCİLLE İMZA kapısı KIRMIZI
  * `dogrula`da `hmac.compare_digest` yerine `==`
                                       -> SABİT SÜRELİ kapısı (AST) KIRMIZI
  * `KopruIstemcisi.sor`dan `acik` denetimini kaldırmak
                                       -> AĞ YOK kapısı KIRMIZI (sahte
                                          gönderici çağrılır)
  * `Settings`te sırları `str` yapmak
                                       -> SECRETSTR kapısı KIRMIZI
  * Nonce'u 8 bayta indirmek
                                       -> NONCE BİÇİMİ kapısı KIRMIZI

BİLEREK ÖLÇÜLMEYEN: nonce TEKRARI. Sözleşme bunu ALICIYA (web ucu) verir;
`test_nonce_tekrari_BURADA_denetlenmez` bu yokluğu adıyla sabitler.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.whatsapp import kopru
from app.whatsapp.kopru import (
    BASLIK_IMZA,
    BASLIK_NONCE,
    BASLIK_ZAMAN,
    ImzaGecersiz,
    KopruAyari,
    KopruIstemcisi,
    KopruKapali,
    dogrula,
    imza_uret,
    imzala,
    nonce_uret,
)

DEPO = Path(__file__).resolve().parents[2]
SOZLESME = DEPO / "docs" / "whatsapp" / "KOPRU_SOZLESMESI.md"
KOPRU_KAYNAK = DEPO / "backend" / "app" / "whatsapp" / "kopru.py"

BIRINCIL = "harman-birincil-sir-2026"
IKINCIL = "harman-ikincil-sir-2026"
NONCE = "0123456789abcdef0123456789abcdef"
SIMDI = 1_757_203_200


def _vektorler() -> list[dict]:
    metin = SOZLESME.read_text(encoding="utf-8")
    blok = re.search(r"```json\n(.*?)\n```", metin, re.S)
    assert blok, "sözleşmede ```json bloğu yok"
    vektorler = json.loads(blok.group(1))
    assert len(vektorler) == 3, "sözleşme TAM ÜÇ altın vektör taşır"
    return vektorler


# ---------------------------------------------------------------------------
# ALTIN VEKTÖRLER — belge fixture'dır, kod ona uyar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sira", [0, 1, 2])
def test_altin_vektor_bayt_bayt(sira: int) -> None:
    v = _vektorler()[sira]
    govde = v["govde"].encode("utf-8")
    # Gövde baytları belgede hex olarak da yazılı: JSON serileştirme farkı
    # imza farkından ÖNCE burada görünür.
    assert govde.hex() == v["govde_utf8_hex"], v["ad"]
    assert hashlib.sha256(govde).hexdigest() == v["govde_sha256"], v["ad"]
    assert kopru.imza_metni(v["timestamp"], v["nonce"], govde).hex() == v["imza_metni_hex"], v["ad"]
    assert imza_uret(v["sir"], v["timestamp"], v["nonce"], govde) == v["imza"], v["ad"]
    # Aynı vektör dogrula'dan da geçer (sır sırası: belgedeki sır birincil).
    assert (
        dogrula(
            timestamp=str(v["timestamp"]), nonce=v["nonce"], imza=v["imza"],
            govde=govde, sirlar=[v["sir"]], simdi=v["timestamp"],
        )
        == "birincil"
    )


def test_altin_vektorler_BAGIMSIZ_hesapla_uyusuyor() -> None:
    """Belgedeki imza, modül İÇE AKTARILMADAN yalnız hashlib/hmac ile de çıkar.

    Modül ve belge birlikte yanlış olsaydı (örn. ayraç "\\n" yerine "|"),
    parametrik kapı yine yeşil kalırdı; bu kapı sözleşmenin METNİNİ ölçer.
    """
    for v in _vektorler():
        govde = v["govde"].encode("utf-8")
        metin = f"{v['timestamp']}\n{v['nonce']}\n{hashlib.sha256(govde).hexdigest()}".encode()
        assert hmac.new(v["sir"].encode(), metin, hashlib.sha256).hexdigest() == v["imza"], v["ad"]


def test_sozlesme_basliklari_ve_tolerans_belgeyle_ayni() -> None:
    metin = SOZLESME.read_text(encoding="utf-8")
    for baslik in (BASLIK_ZAMAN, BASLIK_NONCE, BASLIK_IMZA):
        assert f"`{baslik}`" in metin, baslik
    assert kopru.ZAMAN_TOLERANSI_SANIYE == 300 and "300 s" in metin
    assert kopru.NONCE_EN_AZ_BAYT == 16 and "**16 bayt**" in metin
    # Eski düz-metin sır başlığı kodda ÜRETİLMİYOR.
    assert "X-Harman-Kopru-Sirri" not in KOPRU_KAYNAK.read_text(encoding="utf-8").split('"""', 2)[2]


# ---------------------------------------------------------------------------
# ÇİFT SIR
# ---------------------------------------------------------------------------


def test_ikincil_sir_dogrular() -> None:
    govde = b'{"numara": "905405995959", "soru": "x"}'
    basliklar = imzala(govde, sir=IKINCIL, simdi=SIMDI, nonce=NONCE)
    sonuc = dogrula(
        timestamp=basliklar[BASLIK_ZAMAN], nonce=basliklar[BASLIK_NONCE],
        imza=basliklar[BASLIK_IMZA], govde=govde, sirlar=[BIRINCIL, IKINCIL], simdi=SIMDI,
    )
    assert sonuc == "ikincil"


def test_yanlis_sir_duser() -> None:
    govde = b"{}"
    basliklar = imzala(govde, sir="baska-bir-sir", simdi=SIMDI, nonce=NONCE)
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(
            timestamp=basliklar[BASLIK_ZAMAN], nonce=basliklar[BASLIK_NONCE],
            imza=basliklar[BASLIK_IMZA], govde=govde, sirlar=[BIRINCIL, IKINCIL], simdi=SIMDI,
        )
    assert hata.value.sebep == "imza_uyusmuyor"


def test_imzala_BIRINCIL_sirla_imzalar() -> None:
    """Sır döndürme penceresinde bile imza yeni (birincil) sırla atılır."""
    ayar = KopruAyari(url="https://kopru.example/api", sir=BIRINCIL, ikincil_sir=IKINCIL)
    yakalanan: dict = {}

    def gonderici(url, govde, basliklar, zaman_asimi):
        yakalanan.update(url=url, govde=govde, basliklar=basliklar)
        return b'{"durum": "ok", "metin": "cevap"}'

    assert KopruIstemcisi(ayar, gonderici).sor("905405995959", "soru") == "cevap"
    b = yakalanan["basliklar"]
    assert dogrula(
        timestamp=b[BASLIK_ZAMAN], nonce=b[BASLIK_NONCE], imza=b[BASLIK_IMZA],
        govde=yakalanan["govde"], sirlar=[BIRINCIL], simdi=int(b[BASLIK_ZAMAN]),
    ) == "birincil"
    with pytest.raises(ImzaGecersiz):
        dogrula(
            timestamp=b[BASLIK_ZAMAN], nonce=b[BASLIK_NONCE], imza=b[BASLIK_IMZA],
            govde=yakalanan["govde"], sirlar=[IKINCIL], simdi=int(b[BASLIK_ZAMAN]),
        )
    assert ayar.dogrulama_sirlari == (BIRINCIL, IKINCIL)
    assert json.loads(yakalanan["govde"]) == {"numara": "905405995959", "soru": "soru"}
    assert yakalanan["basliklar"]["Content-Type"] == "application/json"
    assert "X-Harman-Kopru-Sirri" not in yakalanan["basliklar"]


def test_bos_sir_aday_degil() -> None:
    govde = b"{}"
    basliklar = imzala(govde, sir=BIRINCIL, simdi=SIMDI, nonce=NONCE)
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(
            timestamp=basliklar[BASLIK_ZAMAN], nonce=NONCE, imza=basliklar[BASLIK_IMZA],
            govde=govde, sirlar=["", "  "], simdi=SIMDI,
        )
    assert hata.value.sebep == "sir_yok"


# ---------------------------------------------------------------------------
# ZAMAN KAYMASI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("yon", [+1, -1])
def test_kayma_300_gecer_301_duser(yon: int) -> None:
    govde = b"{}"
    basliklar = imzala(govde, sir=BIRINCIL, simdi=SIMDI, nonce=NONCE)
    ortak = dict(
        timestamp=basliklar[BASLIK_ZAMAN], nonce=NONCE, imza=basliklar[BASLIK_IMZA],
        govde=govde, sirlar=[BIRINCIL],
    )
    assert dogrula(simdi=SIMDI + 300 * yon, **ortak) == "birincil"
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(simdi=SIMDI + 301 * yon, **ortak)
    assert hata.value.sebep == "zaman_kaymasi"


def test_zaman_bicimi_bozuksa_duser() -> None:
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(timestamp="dun", nonce=NONCE, imza="00", govde=b"{}", sirlar=[BIRINCIL], simdi=SIMDI)
    assert hata.value.sebep == "zaman_bicimi"


# ---------------------------------------------------------------------------
# GÖVDE, NONCE
# ---------------------------------------------------------------------------


def test_govde_degisince_ayni_basliklar_dogrulanmaz() -> None:
    govde = b'{"numara": "905405995959", "soru": "bakiye"}'
    basliklar = imzala(govde, sir=BIRINCIL, simdi=SIMDI, nonce=NONCE)
    sahte = b'{"numara": "905405995959", "soru": "bakiye!"}'
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(
            timestamp=basliklar[BASLIK_ZAMAN], nonce=NONCE, imza=basliklar[BASLIK_IMZA],
            govde=sahte, sirlar=[BIRINCIL], simdi=SIMDI,
        )
    assert hata.value.sebep == "imza_uyusmuyor"
    assert imza_uret(BIRINCIL, SIMDI, NONCE, govde) != imza_uret(BIRINCIL, SIMDI, NONCE, sahte)


@pytest.mark.parametrize(
    "nonce",
    [
        "0123456789abcdef0123456789abcde",     # 31 karakter (tek uzunluk)
        "0123456789abcdef01234567",            # 12 bayt
        "0123456789ABCDEF0123456789ABCDEF",    # büyük harf
        "0123456789abcdefg123456789abcdef",    # hex dışı
        "",
    ],
)
def test_nonce_bicimi_kapisi(nonce: str) -> None:
    govde = b"{}"
    imza = imza_uret(BIRINCIL, SIMDI, nonce, govde)
    with pytest.raises(ImzaGecersiz) as hata:
        dogrula(timestamp=SIMDI, nonce=nonce, imza=imza, govde=govde, sirlar=[BIRINCIL], simdi=SIMDI)
    assert hata.value.sebep == "nonce_bicimi"


def test_nonce_uret_16_bayt_kucuk_hex_ve_tekrarsiz() -> None:
    uretilen = {nonce_uret() for _ in range(64)}
    assert len(uretilen) == 64
    for n in uretilen:
        assert len(n) == 32 and n == n.lower() and bytes.fromhex(n)


def test_nonce_tekrari_BURADA_denetlenmez() -> None:
    """Sözleşme nonce önbelleğini ALICIYA verir; backend yalnız üretir.

    Aynı başlıklar iki kez doğrulanır ve İKİSİ DE GEÇER. Bu bir kusur değil,
    belgelenmiş sınırdır: web ucu tekrarı reddeder. Bir gün burada bir
    önbellek açılırsa bu test kırmızı yanar ve belge de değişmelidir.
    """
    govde = b"{}"
    basliklar = imzala(govde, sir=BIRINCIL, simdi=SIMDI, nonce=NONCE)
    for _ in range(2):
        assert dogrula(
            timestamp=basliklar[BASLIK_ZAMAN], nonce=NONCE, imza=basliklar[BASLIK_IMZA],
            govde=govde, sirlar=[BIRINCIL], simdi=SIMDI,
        ) == "birincil"
    metin = SOZLESME.read_text(encoding="utf-8")
    assert "Nonce tekrarı" in metin and "ALICI" in metin


# ---------------------------------------------------------------------------
# FAIL-CLOSED: URL boş → KopruKapali, AĞ YOK
# ---------------------------------------------------------------------------


def _agi_yasakla(monkeypatch: pytest.MonkeyPatch) -> None:
    def yasak(*a, **k):
        raise AssertionError("ağa çıkıldı")

    monkeypatch.setattr(urllib.request, "urlopen", yasak)
    # httpx bu deponun çalışma zamanı bağımlılığı DEĞİL (yalnız dev); yine
    # de kurulu olduğu ortamda hiçbir yolunun tetiklenmediği ölçülür.
    try:
        import httpx
    except ModuleNotFoundError:  # pragma: no cover - dev dışı ortam
        return
    monkeypatch.setattr(httpx, "post", yasak)
    monkeypatch.setattr(httpx, "request", yasak)
    monkeypatch.setattr(httpx.Client, "__init__", yasak)


@pytest.mark.parametrize(
    "ayar",
    [
        KopruAyari(url="", sir=BIRINCIL),
        KopruAyari(url="   ", sir=BIRINCIL, ikincil_sir=IKINCIL),
        KopruAyari(url="https://kopru.example/api", sir=""),
        KopruAyari(url="https://kopru.example/api", sir="", ikincil_sir=IKINCIL),
    ],
)
def test_url_ya_da_sir_bossa_KopruKapali_ve_ag_yok(monkeypatch, ayar: KopruAyari) -> None:
    _agi_yasakla(monkeypatch)
    cagrildi = []

    def gonderici(*a, **k):
        cagrildi.append(a)
        return b"{}"

    assert not ayar.acik
    with pytest.raises(KopruKapali):
        KopruIstemcisi(ayar, gonderici).sor("905405995959", "soru")
    assert cagrildi == []
    # Varsayılan gönderici (urllib) yolu da aynı: yasaklı urlopen'a varılmaz.
    with pytest.raises(KopruKapali):
        KopruIstemcisi(ayar).sor("905405995959", "soru")


def test_varsayilan_ayar_KAPALI(monkeypatch) -> None:
    """Hiçbir ortam değişkeni olmadan köprü kapalıdır — bu PR hiçbir şeyi açmaz."""
    from app.config import settings

    for ad in ("harman_kopru_url", "harman_kopru_sirri", "harman_kopru_sirri_ikincil"):
        monkeypatch.setattr(settings, ad, None)
    assert kopru.acik_mi() is False
    assert KopruAyari.ayardan() == KopruAyari(url="", sir="", ikincil_sir="")
    _agi_yasakla(monkeypatch)
    with pytest.raises(KopruKapali):
        KopruIstemcisi().sor("905405995959", "soru")


def test_ayardan_okuma_SecretStr_uzerinden(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "harman_kopru_url", " https://kopru.example/api ")
    monkeypatch.setattr(settings, "harman_kopru_sirri", SecretStr(" a "))
    monkeypatch.setattr(settings, "harman_kopru_sirri_ikincil", SecretStr("b"))
    ayar = KopruAyari.ayardan()
    assert ayar == KopruAyari(url="https://kopru.example/api", sir="a", ikincil_sir="b")
    assert kopru.acik_mi() is True


# ---------------------------------------------------------------------------
# İSTEMCİ DAVRANIŞI (kaynak danisman.sor ile aynı: hata → None)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "yanit",
    [b"", b"bozuk", b'{"metin": ""}', b'{"metin": 5}', b"[]", b'{"durum": "ok"}'],
)
def test_bozuk_yanit_None(yanit: bytes) -> None:
    ayar = KopruAyari(url="https://kopru.example/api", sir=BIRINCIL)
    assert KopruIstemcisi(ayar, lambda *a: yanit).sor("905405995959", "soru") is None


def test_http_ve_ag_hatasi_None_istisna_sizmaz() -> None:
    import urllib.error

    ayar = KopruAyari(url="https://kopru.example/api", sir=BIRINCIL)

    def http_hatasi(*a):
        raise urllib.error.HTTPError("https://kopru.example/api", 503, "kapalı", {}, None)

    def ag_hatasi(*a):
        raise urllib.error.URLError("dns")

    assert KopruIstemcisi(ayar, http_hatasi).sor("905405995959", "soru") is None
    assert KopruIstemcisi(ayar, ag_hatasi).sor("905405995959", "soru") is None
    assert KopruIstemcisi(ayar, lambda *a: (_ for _ in ()).throw(TimeoutError())).sor("9", "s") is None


def test_soru_500_karakterde_kirpilir() -> None:
    ayar = KopruAyari(url="https://kopru.example/api", sir=BIRINCIL)
    yakalanan = {}

    def gonderici(url, govde, basliklar, zaman_asimi):
        yakalanan["govde"] = govde
        return b'{"metin": "x"}'

    KopruIstemcisi(ayar, gonderici).sor("905405995959", "ş" * 600)
    assert len(json.loads(yakalanan["govde"])["soru"]) == kopru.SORU_MAKS == 500


# ---------------------------------------------------------------------------
# STATİK KAPILAR
# ---------------------------------------------------------------------------


def test_config_sirlari_SecretStr() -> None:
    from app.config import Settings

    alanlar = Settings.model_fields
    for ad in ("harman_kopru_sirri", "harman_kopru_sirri_ikincil"):
        assert alanlar[ad].default is None
        assert "SecretStr" in str(alanlar[ad].annotation), ad
    assert alanlar["harman_kopru_url"].default is None


def test_dogrula_sabit_sureli_karsilastirir() -> None:
    """`dogrula` içinde imza `==` ile DEĞİL `hmac.compare_digest` ile kıyaslanır."""
    agac = ast.parse(KOPRU_KAYNAK.read_text(encoding="utf-8"))
    fn = next(n for n in agac.body if isinstance(n, ast.FunctionDef) and n.name == "dogrula")
    kaynak = ast.get_source_segment(KOPRU_KAYNAK.read_text(encoding="utf-8"), fn)
    assert "hmac.compare_digest(" in kaynak
    for dugum in ast.walk(fn):
        if isinstance(dugum, ast.Compare):
            for op in dugum.ops:
                assert not isinstance(op, ast.Eq) or not any(
                    isinstance(t, ast.Name) and t.id in {"beklenen", "verilen"}
                    for t in [dugum.left, *dugum.comparators]
                ), "imza `==` ile kıyaslanmış"


def test_kopru_yalniz_config_ve_stdlib_ceker() -> None:
    """Modül DB'ye, rotalara, main'e DOKUNMAZ; tek uygulama importu `..config`."""
    agac = ast.parse(KOPRU_KAYNAK.read_text(encoding="utf-8"))
    uygulama = []
    for n in agac.body:
        if isinstance(n, ast.ImportFrom) and n.level:
            uygulama.append(n.module)
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app"):
            uygulama.append(n.module)
        if isinstance(n, ast.Import):
            for a in n.names:
                assert not a.name.startswith(("app", "sqlalchemy", "httpx", "fastapi")), a.name
    assert uygulama == ["config"]
