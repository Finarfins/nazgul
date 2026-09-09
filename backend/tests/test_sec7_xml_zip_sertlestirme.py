"""SEC-7 — XML ayrıştırma sertleştirmesi + ZIP'te GERÇEK bayt sayacı.

İki güvenilmez XML yolu ölçülüyor; ikisi de bizim üretmediğimiz gövdeler:

* **SOAP yanıtları** (İzibiz/NES) — ``_parse_xml``. Stdlib ``ElementTree`` dış
  varlık ÇÖZMEZ, ama **varlık genişletmesi (billion-laughs)** gerçek bir
  bellek/CPU tüketimi saldırısıdır. Artık ``defusedxml`` DTD'yi baştan reddeder.
* **TCMB kur XML'i** — ``_parse_tcmb``. Aynı kapı, farklı uç.

Ayrıca ``_pdf_from_zip`` ZIP bombasına karşı sertleştirildi: üye sayısı,
bildirilen boyutlar ve sıkıştırma oranı AÇMADAN ÖNCE denetlenir; üyeler parça
parça okunur ve okunan bayt SAYILIR.

BU DOSYADAKİ HİÇBİR TEST SOKET AÇMAZ ve bu varsayılmıyor, ZORLANIYOR —
``_soket_yok`` tuzağı ``test_e2_ebelge_yasam_dongusu.py``den devralındı ve
nöbetçinin kendisi ayrıca ölçülüyor.

Bu dosya ALT SÜREÇ ÇAĞIRMAZ ve SQL ÇALIŞTIRMAZ; tamamen saf fonksiyon ölçümü.
"""

from __future__ import annotations

import io
import logging
import os
import socket
import struct
import time
import zipfile
from types import SimpleNamespace

import pytest

from app.einvoice import FAILED, IzibizEInvoiceProvider
from app.einvoice import provider as saglayici_modulu
from app.einvoice.transport import HttpResponse
from app.exchange_rates import _parse_tcmb


BASE_URL = "https://efaturatest.izibiz.com.tr"


# ==========================================================================
# Ağ kapısı — bu dosyada soket YOKTUR
# ==========================================================================
@pytest.fixture(autouse=True)
def _soket_yok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Soketin KENDİSİNE kurulan kapı; hangi HTTP kütüphanesi olduğundan bağımsız."""

    def _patla(*args: object, **kwargs: object) -> None:
        raise AssertionError("Bu testte ağ çağrısı YASAK — soket açılmaya çalışıldı")

    monkeypatch.setattr(socket, "socket", _patla)
    monkeypatch.setattr(socket, "create_connection", _patla)


def test_SOKET_NOBETCISI_GERCEKTEN_KURULU() -> None:
    """Nöbetçinin kendisi ölçülür; kurulmamış bir nöbetçi dosyayı sessizce açardı."""
    with pytest.raises(AssertionError):
        socket.socket()
    with pytest.raises(AssertionError):
        socket.create_connection((BASE_URL, 443))


class SahteTasima:
    """Soket AÇMAYAN taşıma; bu dosyada yalnız sağlayıcıyı kurmaya yarıyor."""

    def request(self, method: str, url: str, **kwargs: object) -> HttpResponse:
        raise AssertionError("Bu testte HTTP çağrısı beklenmiyor")


def _ayarlar() -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_base_url=BASE_URL,
        einvoice_username="sandbox-kullanici",
        einvoice_password="sandbox-parola",
        einvoice_api_key=None,
        izibiz_env="test",
        einvoice_endpoints_verified=True,
    )


def _saglayici() -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(_ayarlar(), SahteTasima(), company_id=1)


# ==========================================================================
# Bombalar
# ==========================================================================
#: Klasik "milyar kahkaha": gövde küçücük, genişlemesi astronomik.
BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b"<!DOCTYPE lolz [\n"
    b'  <!ENTITY lol "lol">\n'
    b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
    b'  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
    b'  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
    b'  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">\n'
    b'  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">\n'
    b'  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">\n'
    b'  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">\n'
    b'  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">\n'
    b"]>\n"
    b"<lolz>&lol9;</lolz>"
)

#: Aynı saldırı, TCMB kök elemanıyla.
TCMB_BOMBA = (
    b'<?xml version="1.0"?>\n'
    b"<!DOCTYPE Tarih_Date [\n"
    b'  <!ENTITY a "aaaaaaaaaa">\n'
    b'  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    b'  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    b'  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n'
    b'  <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">\n'
    b'  <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">\n'
    b'  <!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;">\n'
    b'  <!ENTITY h "&g;&g;&g;&g;&g;&g;&g;&g;&g;&g;">\n'
    b"]>\n"
    b'<Tarih_Date><Currency CurrencyCode="USD"><ForexSelling>&h;</ForexSelling>'
    b"</Currency></Tarih_Date>"
)


# ==========================================================================
# (a) SOAP gövdesi — billion-laughs
# ==========================================================================
def test_billion_laughs_parse_xml_none_doner() -> None:
    """Bomba ÇÖZÜLMEZ ve İSTİSNA da SIZDIRMAZ — ``None`` döner.

    İkisi birden önemli: ``defusedxml``in ``EntitiesForbidden``ı bir
    ``ParseError`` DEĞİL, bir ``ValueError``dur. Yalnız ``ParseError``
    yakalayan eski ``except`` bu gövdede ``None`` döndürmez, İSTİSNA
    fırlatırdı — yani çağıran fail-closed yoluna hiç varamazdı.
    """
    basla = time.perf_counter()
    assert saglayici_modulu._parse_xml(BILLION_LAUGHS) is None
    assert time.perf_counter() - basla < 2.0


def test_billion_laughs_FAILED_uretir_cokmez() -> None:
    """Bomba gövdesi, üretimdeki zincirin sonunda FAILED verir — çökme değil.

    Zincir gerçek: ``_business_failure`` fail-closed kararı üretir,
    ``_fail`` onu ``EInvoiceResult(status=FAILED)``a çevirir.
    """
    adaptor = _saglayici()
    sonuc = adaptor._business_failure(HttpResponse(200, BILLION_LAUGHS))

    assert sonuc is not None, "bomba gövdesi SESSİZCE BAŞARI sayıldı"
    kod, mesaj = sonuc
    assert isinstance(mesaj, str) and mesaj

    kayit = adaptor._fail("submit", kod, message=mesaj)
    assert kayit.status == FAILED
    assert kayit.external_id is None, "başarısız gönderim ETTN uyduramaz"


def test_iyi_huylu_soap_hala_ayristiriliyor() -> None:
    """OLUMLU KONTROL: sertleştirme meşru gövdeyi bozmadı.

    ``defusedxml`` stdlib ``Element`` döndürdüğü için
    ``_first_xml_element``/``_child_text`` yardımcıları DEĞİŞMEDEN çalışır.
    """
    kok = saglayici_modulu._parse_xml(b"<Zarf><ERROR_CODE>10007</ERROR_CODE></Zarf>")
    assert kok is not None
    assert saglayici_modulu._child_text(kok, "ERROR_CODE") == "10007"


def test_bozuk_xml_hala_none_doner() -> None:
    """``ParseError`` yolu da korunuyor — sertleştirme onu yutmadı."""
    assert saglayici_modulu._parse_xml(b"<Zarf><kapanmayan") is None
    assert saglayici_modulu._parse_xml(b"") is None


# ==========================================================================
# (b) TCMB — varlık genişletmesi: denetimli başarısızlık, ASILMA YOK
# ==========================================================================
def test_tcmb_bombasi_denetimli_basarisizlik_ve_asilmaz() -> None:
    """Bomba ANINDA reddedilir; genişletme YAPILMAZ.

    ``_parse_tcmb``in sözleşmesi "ayrıştırma hatasında fırlat"; çağıran
    ``/api/exchange-rates/refresh`` bunu yakalayıp MANUAL_OVERRIDE'a düşüyor.
    ``EntitiesForbidden`` bir ``ValueError``dur, yani denetimli bir hata.
    """
    basla = time.perf_counter()
    with pytest.raises(ValueError):
        _parse_tcmb(TCMB_BOMBA)
    gecen = time.perf_counter() - basla
    assert gecen < 2.0, f"TCMB bombası {gecen:.2f}s sürdü — genişletme yapılıyor olabilir"


def test_tcmb_iyi_huylu_xml_hala_calisiyor() -> None:
    """OLUMLU KONTROL: gerçek TCMB gövdesi hâlâ ayrıştırılıyor."""
    xml = (
        b"<Tarih_Date>"
        b'<Currency CurrencyCode="USD"><ForexSelling>32,0000</ForexSelling></Currency>'
        b"</Tarih_Date>"
    )
    assert "USD" in _parse_tcmb(xml)


# ==========================================================================
# (c) ZIP bombası
# ==========================================================================
def _zip_yap(uyeler: list[tuple[str, bytes]]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for ad, veri in uyeler:
            z.writestr(ad, veri)
    return tampon.getvalue()


def _uyarilar(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [k.getMessage() for k in caplog.records if k.levelno == logging.WARNING]


def test_zip_bombasi_oran_reddedilir_ve_uyari_basilir(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sıkıştırma oranı bombası: ~24 KB tel, ~24 MiB açılım.

    Bu bomba üye sayısı, üye boyutu ve toplam boyut kapılarının ÜÇÜNÜ DE
    geçer; onu durduran TEK ŞEY oran kapısıdır — bkz.
    ``test_MUTASYON_oran_kapisi_sokulurse_bomba_gecer``.
    """
    bomba = _zip_yap([("f.pdf", b"%PDF-" + b"\x00" * (24 * 1024 * 1024))])
    bilgi = zipfile.ZipFile(io.BytesIO(bomba)).infolist()[0]

    # Bombanın gerçekten "diğer kapıları geçen" cinsten olduğunu ÖLÇ.
    assert bilgi.file_size <= saglayici_modulu._ZIP_MAX_BAYT
    assert bilgi.file_size / max(bilgi.compress_size, 1) > saglayici_modulu._ZIP_MAX_ORAN
    assert len(bomba) < 128 * 1024, "tel üzerindeki gövde küçük olmalı ki bomba olsun"

    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(bomba) == b""

    uyarilar = _uyarilar(caplog)
    assert len(uyarilar) == 1, f"tam olarak bir UYARI bekleniyordu, {len(uyarilar)} geldi"
    assert "sıkıştırma oranı" in uyarilar[0]
    assert "%PDF" not in uyarilar[0], "gövde ASLA loglanmaz"


def test_zip_dokuz_uye_reddedilir(caplog: pytest.LogCaptureFixture) -> None:
    """9 üye > 8 sınırı → BOŞ ve tek uyarı."""
    dokuz = _zip_yap([(f"d{i}.pdf", b"%PDF-" + os.urandom(2048)) for i in range(9)])
    assert len(zipfile.ZipFile(io.BytesIO(dokuz)).infolist()) == 9

    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(dokuz) == b""

    uyarilar = _uyarilar(caplog)
    assert len(uyarilar) == 1
    assert "üye sayısı" in uyarilar[0]


def test_zip_bildirilen_toplam_boyut_reddedilir(caplog: pytest.LogCaptureFixture) -> None:
    """Tek tek meşru, TOPLAMDA sınırı aşan üyeler de reddedilir.

    8 üye x 5 MiB sıkışmaz veri = 40 MiB > 25 MiB. Her üye tek başına
    sınırın altında; yakalayan şey TOPLAM kapısı.
    """
    uyeler = [(f"d{i}.bin", os.urandom(5 * 1024 * 1024)) for i in range(8)]
    paket = _zip_yap(uyeler)
    bilgiler = zipfile.ZipFile(io.BytesIO(paket)).infolist()
    assert all(b.file_size <= saglayici_modulu._ZIP_MAX_BAYT for b in bilgiler)
    assert sum(b.file_size for b in bilgiler) > saglayici_modulu._ZIP_MAX_BAYT

    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(paket) == b""

    uyarilar = _uyarilar(caplog)
    assert len(uyarilar) == 1
    assert "toplam boyut" in uyarilar[0]


def test_mesru_zip_icindeki_pdf_dondurulur(caplog: pytest.LogCaptureFixture) -> None:
    """OLUMLU KONTROL: 100 KB'lık meşru bir PDF taşıyan ZIP hâlâ ÇALIŞIR.

    Sertleştirme "her şeyi reddet" değildir; bu test sınırların meşru bir
    e-Arşiv paketini KESMEDİĞİNİ ölçer — ve tek bir uyarı bile basılmaz.
    """
    pdf = b"%PDF-1.4\n" + os.urandom(100 * 1024) + b"\n%%EOF\n"
    paket = _zip_yap([("fatura.pdf", pdf)])

    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(paket) == pdf

    assert _uyarilar(caplog) == []


def test_eski_sozlesme_korunuyor() -> None:
    """Düz PDF aynen geçer, ZIP olmayan BOŞ döner — SEC-7 bunları değiştirmedi."""
    assert saglayici_modulu._pdf_from_zip(b"%PDF-1.7 govde") == b"%PDF-1.7 govde"
    assert saglayici_modulu._pdf_from_zip(b"<xml/>") == b""
    assert saglayici_modulu._pdf_from_zip(b"") == b""


def test_zip_icindeki_xml_hala_bos_doner(caplog: pytest.LogCaptureFixture) -> None:
    """``GetEArchiveInvoice``in XML'i PDF sayılmaz — eski kural yerinde.

    Bu meşru bir ZIP olduğu için UYARI da basılmaz; BOŞ dönüşün sebebi
    sınır ihlali değil, "içinde PDF yok".
    """
    paket = _zip_yap([("fatura.xml", b"<Invoice/>")])
    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(paket) == b""
    assert _uyarilar(caplog) == []


# ==========================================================================
# (d) MUTASYON — kapı sökülünce KIRMIZI olması gereken testler
# ==========================================================================
def test_MUTASYON_oran_kapisi_sokulurse_bomba_gecer() -> None:
    """MUTASYON KANITI — kırmızıya dönmesi gereken test:
    ``test_zip_bombasi_oran_reddedilir_ve_uyari_basilir``.

    Burada üretim kodundaki oran kapısını ATLAYAN, yani "yalnız başlıktaki
    sayı ve boyutlara bakan" bir sürüm YERİNDE canlandırılıyor. O sürümün
    bombayı GEÇİRDİĞİ ölçülüyor; dolayısıyla yukarıdaki testin yeşilliği
    tesadüf değil, oran kapısının eseridir.
    """
    bomba = _zip_yap([("f.pdf", b"%PDF-" + b"\x00" * (24 * 1024 * 1024))])

    with zipfile.ZipFile(io.BytesIO(bomba)) as arsiv:
        uyeler = arsiv.infolist()
        # Mutant kapılar: sayı + BİLDİRİLEN boyutlar. Üçü de bombayı GEÇİRİR.
        assert len(uyeler) <= saglayici_modulu._ZIP_MAX_UYE
        assert sum(b.file_size for b in uyeler) <= saglayici_modulu._ZIP_MAX_BAYT
        assert all(b.file_size <= saglayici_modulu._ZIP_MAX_BAYT for b in uyeler)

    # Gerçek kod ORANI gördüğü için REDDEDER.
    assert saglayici_modulu._pdf_from_zip(bomba) == b""


def test_MUTASYON_yalan_baslikli_zip_fail_closed(caplog: pytest.LogCaptureFixture) -> None:
    """Başlık BOYUT YALANI söylerse sonuç yine BOŞ olur (fail-closed).

    ÖLÇÜLEN GERÇEK: CPython'ın ``ZipExtFile``ı üyeyi bildirilen
    ``file_size``ta KESER ve tutmayan CRC'de ``BadZipFile`` fırlatır. Bu
    yüzden "bildirilen boyuttan FAZLA bayt akıtan" bir ZIP bu çalışma
    zamanında ÜRETİLEMEZ — bayt sayacı savunmanın son halkasıdır, tek
    halkası değil. Sözleşme açısından önemli olan: yalan başlık sessizce
    PDF üretmez.
    """
    dogru = _zip_yap([("f.pdf", b"%PDF-" + b"A" * (1024 * 1024))])
    yalan = dogru.replace(struct.pack("<I", 1024 * 1024 + 5), struct.pack("<I", 512))
    assert yalan != dogru, "yalan başlık kurulamadı"

    with caplog.at_level(logging.WARNING, logger=saglayici_modulu.__name__):
        assert saglayici_modulu._pdf_from_zip(yalan) == b""


def test_sinirlar_modul_sabiti() -> None:
    """Sınırlar MODÜL SABİTİ olarak duruyor — gömülü sihirli sayı değil."""
    assert saglayici_modulu._ZIP_MAX_UYE == 8
    assert saglayici_modulu._ZIP_MAX_BAYT == 25 * 1024 * 1024
    assert saglayici_modulu._ZIP_MAX_ORAN == 100
    assert saglayici_modulu._ZIP_PARCA == 64 * 1024
