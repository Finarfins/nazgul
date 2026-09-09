"""E2c — e-Arşiv PDF'i ve KUYRUK durumu. Mock taşıma, soket YOK.

§8 "İzibiz belgeyi bizim ETTN'imizle anahtarlamıyor" demişti; §9 (E2b) onu
ÇÜRÜTTÜ ama istek tarafına bakıp durdu ve İKİ KUSUR AÇIK KALDI. §10 (E2c)
ikisini de ölçtü; bu dosya ikisini de ADIYLA kapatıyor.

--- SEBEP 1: `STATUS=100` EŞLEME TABLOSUNDA YOKTU --------------------------

Sağlayıcı durum sorgusuna DOĞRU cevap veriyordu: `INVOICE_ID`, bizim
ETTN'imiz, `STATUS=100`, `STATUS_DESC=KUYRUĞA EKLENDİ`, `WEB_KEY`.
`IZIBIZ_STATUS_ALIASES` 105/120/130 biliyordu ama **100'ü bilmiyordu**;
`map_provider_status` `None` dönüyor, `query_status` cevabı YERE DÜŞÜRÜP
`UNRESOLVED` diyordu. "Boş yanıt" sanılan şey DOLU bir yanıttı.

BU KUSURUN TESTLERDEN KAÇMASININ SEBEBİ DE ÖLÇÜLDÜ: elimizdeki fixture
(`GetEArchiveInvoiceStatus.200.xml`) `SUB_STATUS=DRAFT` ile gönderilmiş bir
belgeden alınmıştı ve `STATUS=105` taşıyor — tablonun BİLDİĞİ bir kod. Yani
birim testi yeşil, üretim yolu kırmızıydı: GERÇEK gönderim (`SUB_STATUS=NEW`)
HER ZAMAN önce 100'e düşer. Yeni fixture
(`GetEArchiveInvoiceStatus-queued.200.xml`) o yolu kapatıyor.

--- SEBEP 2: PDF OPERASYONU YANLIŞTI --------------------------------------

`GetEArchiveInvoice` + `WEB_VALIDATION_KEY` PDF DEĞİL, UBL XML'ini taşıyan
bir ZIP döndürüyor. `_decode_pdf` onu HAKLI OLARAK reddediyordu ve sonuç
kalıcı `PDF_YOK`tu. PDF `GetEArchiveInvoiceList` + `HEADER_ONLY=N` +
`CONTENT_TYPE=PDF` ile geliyor ve BU OTURUMDAN ÖNCE gönderilmiş belgelerde de
ölçüldü (§10.1). (Kapılar `test_e1_efatura_sertlestirme.py` içinde,
düzeltilen testlerin yanında.)

--- SEBEP 3: `10008` SINIFSIZDI -------------------------------------------

İptal hâlâ `ERROR_CODE=10008` diyor — ve bu ARTIK anahtar sorunu DEĞİL,
çünkü AYNI oturumda AYNI ETTN ile durum sorgusu cevap veriyor. Sebep
ÖLÇÜLEMEDİ (sandbox hiçbir belgeyi imzalamıyor; `STATUS=100`in ötesine geçen
kendi belgemiz YOK) ama sınıfı artık doğru: `NOT_FOUND`.

--- MUTASYON TABLOSU ------------------------------------------------------

  * `IZIBIZ_STATUS_ALIASES`ten `"100"`u silmek
        -> `test_KUYRUKTAKI_BELGE_PENDING_OLARAK_OKUNUYOR` KIRMIZI
           (`query_status` yine `UNRESOLVED` derdi — E2'nin hatası geri gelir)
  * `"KUYRUGAEKLENDI"` metin anahtarını silmek
        -> `test_DURUM_METNI_DE_ESLENIYOR_sayi_gelmese_de` KIRMIZI
  * `"100"`u `SENT`/`ACCEPTED` yapmak
        -> `test_KUYRUKTAKI_BELGE_PENDING_OLARAK_OKUNUYOR`,
           `test_DURUM_METNI_DE_ESLENIYOR_sayi_gelmese_de` ve
           `test_ESKI_FIXTURE_NEDEN_KACIRDI_105_DRAFT_100_GERCEK` KIRMIZI
           (kuyruğa alınmış belge GÖNDERİLMİŞ sayılırdı — GİB'e raporlanmamış
           bir belgeyi "kabul edildi" diye göstermek en ağır sessiz yanlış)
  * `IZIBIZ_ERROR_CODE_CLASSES`ten `"10008"`i silmek ya da `VALIDATION` yapmak
        -> `test_10008_NOT_FOUND_olarak_siniflaniyor`,
           `test_IPTAL_10008_ALDIGINDA_YEREL_IPTAL_YOK` ve (E2 dosyasında)
           `test_IPTAL_2XX_ICINDEKI_IS_HATASINDA_BASARISIZ` KIRMIZI.
           `test_10008_KULLANICIYA_FATURANIZ_HATALI_DEMIYOR` bu mutantı
           GÖRMEZ ve bu KASITLI: o kapı `message_for(NOT_FOUND)` cümlesini
           ölçer, kod eşlemesini değil — ikisi AYRI sözleşmedir ve tek bir
           testin ikisini de tutması, hangisinin bozulduğunu gizlerdi.
  * `EInvoiceNotFoundError`ı `EInvoiceError`dan TÜRETMEMEK
        -> `test_NOT_FOUND_HATASI_ALT_SINIF_eski_yakalayicilar_calisiyor`
           KIRMIZI (var olan her `except EInvoiceError` sessizce kırılırdı)
  * `build_client_ettn`i rastgele/`uuid4` yapmak
        -> `test_ISTEMCI_ETTNI_KARARLI_ve_KUCUK_HARF` KIRMIZI
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import EInvoiceError, IzibizEInvoiceProvider, build_client_ettn
from app.einvoice import endpoints as wire
from app.einvoice.errors import NOT_FOUND, EInvoiceNotFoundError, message_for
from app.einvoice.status import PENDING, UNRESOLVED, map_provider_status
from app.einvoice.transport import HttpResponse

BACKEND = Path(__file__).resolve().parent
FIXTURES = BACKEND / "tests" / "fixtures" / "izibiz"

#: §9.2/§10.1'de ÖLÇÜLDÜ: `GetEArchiveInvoiceList` ID ile sorulduğunda İzibiz'in
#: geri verdiği UUID. Bu satırlar bir VARSAYIM değil bir KAYIT: sağlayıcı
#: belgeyi TAM OLARAK bu değerlerle tutuyor ve bunlar bizim ürettiğimiz
#: istemci ETTN'lerinin AYNISI.
IZIBIZ_ANAHTARLARI = {
    "SNG2026518354588": "405acba6-ce9c-59c7-bc79-c37e2f141939",
    "SNG2026471382557": "5266133a-7bf6-5e58-85ec-6a9854169dee",
    "SNG2026589807117": "70dfc021-3013-5443-81e7-53e081fbcc45",
}


def fixture(name: str) -> HttpResponse:
    path = FIXTURES / name
    return HttpResponse(int(path.name.rsplit(".", 2)[-2]), path.read_bytes())


class SahteTasima:
    """Senaryo oynatan taşıma. Soket açmaz."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs):
        self.calls.append(SimpleNamespace(method=method, url=url, body=body))
        item = self.script.pop(0) if self.script else HttpResponse(200, b"<Response/>")
        if isinstance(item, Exception):
            raise item
        return item

    def bodies(self) -> list[str]:
        return [(c.body or b"").decode("utf-8", errors="replace") for c in self.calls]


def _saglayici(tasima: SahteTasima) -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(
        SimpleNamespace(
            einvoice_provider="izibiz",
            einvoice_username="sandbox-kullanici",
            einvoice_password="sandbox-parola",
            einvoice_base_url="https://efaturatest.izibiz.com.tr",
            einvoice_sender_vkn="1111111111",
            einvoice_api_key=None,
            einvoice_endpoints_verified=False,
        ),
        tasima,
    )


# ==========================================================================
# SEBEP 1 — `STATUS=100`
# ==========================================================================

def test_KUYRUKTAKI_BELGE_PENDING_OLARAK_OKUNUYOR() -> None:
    """GERÇEK sandbox yanıtı (`STATUS=100`) artık iç duruma eşleniyor.

    Fixture, gönderimden SIFIR saniye sonra alınmış GERÇEK bir yanıttır
    (`docs/izibiz-sandbox-bulgular.md` §10.3, taze belge sondası): belge daha
    ilk sorguda görünüyor, yani ortada bir GECİKME de YOK.
    """
    tasima = SahteTasima(
        [fixture("Login.200.xml"), fixture("GetEArchiveInvoiceStatus-queued.200.xml")]
    )
    sonuc = _saglayici(tasima).query_status(
        "SNG2026252113200",
        channel="EARSIV",
        uuid="cdd91245-91f4-5b14-aab2-b03bb2ffb5b7",
    )

    assert sonuc.status == PENDING, sonuc
    assert sonuc.status != UNRESOLVED, "E2'nin kusuru geri geldi"
    # HAM kod, eşlenmiş durumun YANINDA durmayı sürdürüyor.
    assert sonuc.gib_status_code == "100", sonuc.gib_status_code
    # İSTEK ŞEKLİ DEĞİŞMEDİ: kusur istekte değil EŞLEMEDEYDİ.
    gonderilen = tasima.bodies()[-1]
    assert "<UUID>cdd91245-91f4-5b14-aab2-b03bb2ffb5b7</UUID>" in gonderilen


def test_DURUM_METNI_DE_ESLENIYOR_sayi_gelmese_de() -> None:
    """Sağlayıcı sayı yerine metin döndürürse de anlaşılmalı.

    `IZIBIZ_FIELD_STATUS` önce `STATUS`a bakar; o alan boş gelen bir yanıtta
    sözleşme METNE düşer. İki anahtardan yalnız birini yazmak, yanıtın
    biçimi değiştiği gün sessizce `UNRESOLVED`a dönmek demekti.
    """
    assert map_provider_status("KUYRUĞA EKLENDİ", wire.IZIBIZ_STATUS_ALIASES) == PENDING
    assert map_provider_status("100", wire.IZIBIZ_STATUS_ALIASES) == PENDING


def test_ESKI_FIXTURE_NEDEN_KACIRDI_105_DRAFT_100_GERCEK() -> None:
    """İki fixture İKİ FARKLI kod taşıyor — kusurun testten kaçış yolu buydu.

    MUTANT: yeni fixture'ı silip eskisine geri dönmek ⇒ bu test KIRMIZI ve
    `test_KUYRUKTAKI_BELGE_PENDING_OLARAK_OKUNUYOR` dayanaksız kalır.
    """
    eski = (FIXTURES / "GetEArchiveInvoiceStatus.200.xml").read_text(encoding="utf-8")
    yeni = (FIXTURES / "GetEArchiveInvoiceStatus-queued.200.xml").read_text(encoding="utf-8")
    assert "<STATUS>105</STATUS>" in eski
    assert "<STATUS>100</STATUS>" in yeni
    assert "KUYRUĞA EKLENDİ" in yeni
    # İKİSİ DE eşleniyor: eski yolu kırmadan yenisi eklendi.
    for kod in ("100", "105"):
        assert wire.IZIBIZ_STATUS_ALIASES[kod] == PENDING


# ==========================================================================
# SEBEP 3 — `10008` sınıfı
# ==========================================================================

def test_10008_NOT_FOUND_olarak_siniflaniyor() -> None:
    assert wire.IZIBIZ_ERROR_CODE_CLASSES["10008"] == NOT_FOUND


def test_10008_KULLANICIYA_FATURANIZ_HATALI_DEMIYOR() -> None:
    """`VALIDATION` YANLIŞ olurdu: istek geçerliydi, kayıt bulunamadı.

    Mesaj "henüz" diyor ve YENİDEN GÖNDERMEYİ ÖNERMİYOR — belge sağlayıcıya
    inmiştir (gönderim `RETURN_CODE=0` döndü ve durum sorgusu belgeyi
    GÖRÜYOR); yeniden göndermek bir KOPYA üretirdi.
    """
    metin = message_for(NOT_FOUND)
    assert "henüz bulunamadı" in metin
    assert "tekrar deneyin" in metin
    assert "hatalı" not in metin
    assert "uymuyor" not in metin
    # Ham sağlayıcı cümlesi ve kodu kullanıcı metnine GİRMİYOR.
    assert "10008" not in metin
    assert "bulunamamıştır" not in metin


def test_NOT_FOUND_HATASI_ALT_SINIF_eski_yakalayicilar_calisiyor() -> None:
    """Yeni tür eklenirken hiçbir çağıran sessizce kırılmadı."""
    hata = EInvoiceNotFoundError("bulunamadı", raw={"a": 1})
    assert isinstance(hata, EInvoiceError)
    assert hata.code == NOT_FOUND
    with pytest.raises(EInvoiceError):
        raise EInvoiceNotFoundError("bulunamadı")


def test_IPTAL_10008_ALDIGINDA_YEREL_IPTAL_YOK() -> None:
    """GERÇEK 10008 yanıtı: sonuç FAILED, `CANCELLED` DEĞİL.

    Sınıf değişti ama SÖZLEŞME DEĞİŞMEDİ: entegratör iptali kabul etmediği
    sürece ERP faturayı iptal ETMİYOR. `NOT_FOUND`u "önemsiz hata" sayıp
    yerel iptale devam etmek, E2'nin kapattığı ayrışmayı geri açardı.
    """
    tasima = SahteTasima(
        [fixture("Login.200.xml"), fixture("CancelEArchiveInvoice-10008.200.xml")]
    )
    sonuc = _saglayici(tasima).cancel(
        "SNG2026252113200",
        channel="EARSIV",
        uuid="cdd91245-91f4-5b14-aab2-b03bb2ffb5b7",
    )
    assert sonuc.status == "FAILED", sonuc
    assert sonuc.error == message_for(NOT_FOUND), sonuc.error
    assert "10008" in str(sonuc.raw), sonuc.raw


# ==========================================================================
# H1 ÇÜRÜTÜLDÜ — istemci ETTN'i DOĞRU anahtardı
# ==========================================================================

def test_ISTEMCI_ETTNI_KARARLI_ve_KUCUK_HARF() -> None:
    """§9.2: İzibiz belgeyi TAM OLARAK bu değerle tutuyor.

    İki özellik de ölçümün dayanağı: (a) KARARLI — aynı (firma, fatura) çifti
    her zaman aynı UUID'i verir, yoksa gönderdiğimizle sorduğumuz ayrışırdı;
    (b) KÜÇÜK HARF — sağlayıcının geri verdiği değer küçük harfli ve
    karşılaştırma büyük/küçük harfe duyarsız YAPILMADI, çünkü duyarsız bir
    karşılaştırma gerçek bir ayrışmayı gizlerdi.
    """
    ilk = build_client_ettn(7, 42)
    assert ilk == build_client_ettn(7, 42)
    assert ilk != build_client_ettn(7, 43)
    assert ilk == ilk.lower()
    # UUID sürüm 5 (ad tabanlı) — rastgele DEĞİL.
    assert ilk[14] == "5", ilk
    assert build_client_ettn(None, 42) is None


def test_OLCULEN_ANAHTARLAR_KUCUK_HARF_ve_UUID5() -> None:
    """§9.2/§10.1'de sağlayıcıdan GERİ OKUNAN değerlerin biçimi çivileniyor.

    Bu kapı, ölçümün kendisini belgeler: bu üç değer bizim ürettiğimiz
    biçimdedir (küçük harf, uuid5) ve İzibiz onları AYNEN geri verdi.
    """
    for numara, ettn in IZIBIZ_ANAHTARLARI.items():
        assert numara.startswith("SNG")
        assert ettn == ettn.lower(), ettn
        assert len(ettn) == 36 and ettn.count("-") == 4, ettn
        assert ettn[14] == "5", ettn
