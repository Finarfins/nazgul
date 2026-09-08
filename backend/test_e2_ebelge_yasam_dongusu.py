"""E2 — e-belge YAŞAM DÖNGÜSÜ: sureti indir, e-Arşiv'i iptal et.

E1 belgeyi GÖNDERMEYİ ve durumunu SORMAYI sertleştirdi. Geriye belgenin
hayatının iki ucu kaldı ve ikisi de bu dosyanın konusu:

1. **Suret** — ``GET /api/invoices/{id}/einvoice/download?format=pdf|xml``.
   Gönderilmiş bir belgenin resmî görüntüsünü (sağlayıcı PDF'i) ya da
   gönderilen UBL'in kendisini indirir. Bu, ``GET /invoices/{id}/pdf`` ile
   AYNI ŞEY DEĞİLDİR: o bizim iç faturamızı basar.
2. **İptal** — ``POST /api/invoices/{id}/cancel`` artık yerel bir kayıt
   değişikliği değil. Belgenin entegratörde bir hayatı varsa YEREL İPTAL
   ENTEGRATÖRÜ GEÇEMEZ.

BU DOSYADAKİ HİÇBİR TEST SOKET AÇMAZ ve bu VARSAYILMIYOR, ZORLANIYOR:
:func:`_soket_yok` ``socket.socket`` ve ``socket.create_connection``'ı patlayan
birer tuzakla değiştiriyor. ``urlopen``ı taklit etmek YETMEZDİ — adaptör
``urllib`` KULLANMIYOR, ``http.client`` kullanıyor; yalnız ``urlopen``a bakan
bir nöbetçi bu dosyadaki her çağrının ağa çıkmasına izin verirdi. Nöbetçinin
gerçekten kurulu olduğu ayrıca ölçülüyor
(:func:`test_SOKET_NOBETCISI_GERCEKTEN_KURULU`).
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import (
    ACCEPTED,
    CANCELLABLE,
    CANCELLED,
    FAILED,
    NONE,
    PENDING,
    REJECTED,
    SENT,
    TERMINAL,
    IzibizEInvoiceProvider,
    NesEInvoiceProvider,
    advance_status,
)
from app.einvoice import endpoints as wire
from app.einvoice.transport import HttpResponse, TransportError


BACKEND = Path(__file__).resolve().parent
ROUTER = BACKEND / "app" / "routers" / "invoices.py"

BASE_URL = "https://efaturatest.izibiz.com.tr"
ETTN = "11111111-2222-3333-4444-555555555555"
BELGE_KIMLIGI = "SNG2026210141633"


# ==========================================================================
# Ortak koşum takımı
# ==========================================================================
@pytest.fixture(autouse=True)
def _soket_yok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bu dosyada ağ YOKTUR — ve bu bir temenni değil, bir kapı.

    ``urlopen``ı sahtelemek YANLIŞ NÖBETÇİ olurdu: ``app.einvoice.transport``
    ``http.client.HTTPSConnection`` kullanıyor, ``urllib.request`` değil. Kapı
    bu yüzden soketin KENDİSİNE kuruluyor; hangi HTTP kütüphanesinin
    kullanıldığından bağımsız.
    """

    def _patla(*args: object, **kwargs: object) -> None:
        raise AssertionError("Bu testte ağ çağrısı YASAK — soket açılmaya çalışıldı")

    monkeypatch.setattr(socket, "socket", _patla)
    monkeypatch.setattr(socket, "create_connection", _patla)


def test_SOKET_NOBETCISI_GERCEKTEN_KURULU() -> None:
    """Nöbetçinin kendisi ölçülür; kurulmamış bir nöbetçi dosyayı sessizce açardı."""
    with pytest.raises(AssertionError):
        socket.socket()
    with pytest.raises(AssertionError):
        socket.create_connection(("efaturatest.izibiz.com.tr", 443))


class SahteTasima:
    """Senaryo oynatan, çağrı sayan taşıma. Soket AÇMAZ."""

    def __init__(self, script: list[object] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        self.calls.append(SimpleNamespace(method=method, url=url, body=body, headers=headers or {}))
        item = self.script.pop(0) if self.script else HttpResponse(200, b"<Response/>")
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, HttpResponse)
        return item

    def son_govde(self) -> str:
        return (self.calls[-1].body or b"").decode("utf-8")


def _ayarlar() -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_base_url=BASE_URL,
        einvoice_username="sandbox-kullanici",
        einvoice_password="sandbox-parola",
        einvoice_api_key=None,
        izibiz_env="test",
        einvoice_endpoints_verified=True,
    )


def saglayici(transport: SahteTasima) -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(_ayarlar(), transport, company_id=1)


def _zarf(govde: str) -> HttpResponse:
    return HttpResponse(
        200,
        (
            "<?xml version='1.0' encoding='UTF-8'?>"
            '<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
            + govde
            + "</S:Body></S:Envelope>"
        ).encode("utf-8"),
    )


LOGIN_OK = _zarf(
    '<ns3:LoginResponse xmlns:ns3="http://schemas.i2i.com/ei/wsdl">'
    "<SESSION_ID>oturum-jetonu</SESSION_ID></ns3:LoginResponse>"
)

#: BAŞARI gövdesi ŞEMADAN türetildi (``?xsd=5``), KAYDEDİLMİŞ DEĞİL — ve bu
#: fark burada açıkça yazılıyor çünkü bu depoda ``tests/fixtures/izibiz/``
#: altındaki her gövde GERÇEK bir yanıttır. Sandbox bu iptali BUGÜN kabul
#: etmiyor (``ERROR_CODE=10008``, bkz. ``CANCEL_RED``), dolayısıyla kaydedilecek
#: bir başarı yanıtı YOK; uydurup fixture dizinine koymak, o dizinin
#: sözleşmesini bozardı. Şemadan bilinen: yanıtta DURUM ALANI YOKTUR, yalnız
#: ``REQUEST_RETURN`` ve ``ERROR_TYPE``; başarının tek işareti ``ERROR_TYPE``ın
#: YOKLUĞU ve ``RETURN_CODE``un sıfır olmasıdır.
CANCEL_OK = _zarf(
    '<CancelEArchiveInvoiceResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
    '<REQUEST_RETURN xmlns=""><INTL_TXN_ID>65986709</INTL_TXN_ID>'
    "<RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN>"
    "</CancelEArchiveInvoiceResponse>"
)

#: RED yanıtı UYDURMA DEĞİL: 2026-09-11'de gerçek sandbox'tan kaydedildi
#: (``tests/fixtures/izibiz/CancelEArchiveInvoice-fault.200.xml``). Başarı
#: gövdesinin aksine bu KAYITLIDIR, çünkü sağlayıcı bu cevabı gerçekten verdi.
#: Ölçümün kendisi ve sağlayıcıya sorulacak soru:
#: ``docs/izibiz-sandbox-bulgular.md`` §8.
FIXTURES = BACKEND / "tests" / "fixtures" / "izibiz"
CANCEL_RED = HttpResponse(
    200, (FIXTURES / "CancelEArchiveInvoice-fault.200.xml").read_bytes()
)


# ==========================================================================
# 1) DURUM MAKİNESİ — CANLI ÜÇ DURUMDAN, BAŞKA HİÇBİR YERDEN
# ==========================================================================
def test_CANCELLED_YALNIZ_UC_CANLI_DURUMDAN_KABUL_EDILIR() -> None:
    """``advance_status`` ölçüldü: iptal yalnız PENDING/SENT/ACCEPTED'ten gelir.

    Üçünün DIŞINDA kalan üç durumun her biri AYRI bir gerekçeyle dışarıda ve
    testte de ayrı ayrı ölçülüyor — tek bir "diğerleri" satırı, üçünden
    birinin sessizce içeri sızmasını göremezdi.
    """
    assert sorted(CANCELLABLE) == [ACCEPTED, PENDING, SENT]

    for canli in (PENDING, SENT, ACCEPTED):
        assert advance_status(canli, CANCELLED) == CANCELLED, canli

    # NONE: gönderilmemiş — entegratörde iptal edilecek bir şey YOK.
    assert advance_status(NONE, CANCELLED) == NONE
    # FAILED: gönderim hiç inmedi, ETTN yok — aynı çelişki, artı yeniden
    # gönderim yolunu kapatırdı.
    assert advance_status(FAILED, CANCELLED) == FAILED
    # REJECTED: GİB zaten reddetti; iptal yazmak GİB'in kararını ÜZERİNE yazardı.
    assert advance_status(REJECTED, CANCELLED) == REJECTED


def test_CANCELLED_TERMINALDIR_VE_GERIYE_YURUNMEZ() -> None:
    """İptal edilmiş bir belge hiçbir sağlayıcı yanıtıyla geri dönmez."""
    assert CANCELLED in TERMINAL
    for gelen in (PENDING, SENT, ACCEPTED, REJECTED, FAILED, NONE, "UNRESOLVED"):
        assert advance_status(CANCELLED, gelen) == CANCELLED, gelen


def test_DURUM_SORGUSU_CANCELLED_URETEMEZ() -> None:
    """``CANCELLED`` bir SORGU cevabı değildir — yalnız kendi iptalimizden doğar.

    ``QUERYABLE`` bir durum sorgusunun verebileceği cevapları sınırlar. İptal
    orada OLSAYDI, geç gelen bir yoklama belgeyi bizim hiç istemediğimiz bir
    iptalle işaretleyebilirdi.
    """
    from app.einvoice.status import QUERYABLE

    assert CANCELLED not in QUERYABLE


def test_SAGLAYICI_CANCELLED_DESE_BILE_BELGE_IPTAL_OLMAZ() -> None:
    """Sorgu yanıtında ÇIPLAK "CANCELLED" jetonu belgeyi iptal ETMEZ.

    Bu test, `CANCELLED`ın `KNOWN` kümesine EKLENMESİNİN açtığı deliği
    kapatıyor ve ÖLÇÜLDÜ: `map_provider_status` "bilinen bir jeton mu"
    diye `KNOWN`a bakıyor, dolayısıyla jeton ARTIK eşleniyor (eskiden
    `None`a düşerdi). Eşlenmesi bir sorun DEĞİL — sorun, oradan bir
    GEÇİŞ doğsaydı olurdu: sağlayıcının kelime tercihi, hiç yapmadığımız
    bir iptali yerel satıra yazabilirdi.

    İki kapı birden kapalı ve ikisi de burada ölçülüyor: `QUERYABLE`
    jetonu sorgu cevabı olarak kabul etmiyor (uç `UNRESOLVED`a düşüyor),
    ve `UNRESOLVED` bir geçiş sayılmadığı için belge OLDUĞU YERDE kalıyor.
    """
    from app.einvoice.status import QUERYABLE, UNRESOLVED, map_provider_status

    # 1) Jeton artık eşleniyor (KNOWN büyüdü) — bu ölçülüyor, varsayılmıyor.
    assert map_provider_status("CANCELLED") == CANCELLED
    # 2) Ama bir SORGU cevabı olamaz; uç bunu UNRESOLVED'a çevirir.
    assert CANCELLED not in QUERYABLE
    # 3) Ve UNRESOLVED bir geçiş değildir: belge nerede duruyorsa orada kalır.
    for durum in (PENDING, SENT, ACCEPTED):
        assert advance_status(durum, UNRESOLVED) == durum, durum


# ==========================================================================
# 2) SAĞLAYICI: e-ARŞİV İPTALİ
# ==========================================================================
def test_IPTAL_ISTEGI_SEMAYA_UYGUN_KURULUR() -> None:
    """Gövde ``?xsd=5``ten okunan şemayla birebir: iki eleman, fazlası yok."""
    transport = SahteTasima([LOGIN_OK, CANCEL_OK])
    sonuc = saglayici(transport).cancel(BELGE_KIMLIGI, channel="EARSIV", uuid=ETTN)

    assert sonuc.status == CANCELLED, sonuc.error
    govde = transport.son_govde()
    # Kök eleman ve ad alanı — türetilmedi, WSDL'den okundu.
    assert "CancelEArchiveInvoiceRequest" in govde
    assert "http://schemas.i2i.com/ei/wsdl/archive" in govde
    # Sarmalayıcının adı "EArsiv", operasyonunki "EArchive". Aynı istekte İKİ
    # FARKLI yazım; biri diğerinden TÜRETİLEMEZ.
    assert "<CancelEArsivInvoiceContent>" in govde
    assert "<FATURA_UUID>" + ETTN + "</FATURA_UUID>" in govde
    # Ölçülmemiş opsiyonel alanların HİÇBİRİ gönderilmiyor: her biri sağlayıcı
    # tarafında FARKLI bir işlem anlamına gelir.
    for alan in ("DELETE_FLAG", "INVOICE_CONTENT", "IPTAL_TARIHI", "UPLOAD_FLAG", "FATURA_ID"):
        assert alan not in govde, alan
    # İstek e-Arşiv SERVİSİNE gitti, e-Fatura servisine değil.
    assert transport.calls[-1].url.endswith(wire.IZIBIZ_EARCHIVE_PATH)


def test_IPTAL_2XX_ICINDEKI_IS_HATASINDA_BASARISIZ() -> None:
    """İzibiz reddi HTTP 200 gövdesinde bildirir; ``ok`` bayrağı yeterli değil.

    Bu, iptal yolunun en tehlikeli sessiz yanlışı olurdu: ``response.ok``a
    güvenen bir uygulama, entegratörün REDDETTİĞİ bir iptal için faturayı
    yerelde iptal ederdi.
    """
    transport = SahteTasima([LOGIN_OK, CANCEL_RED])
    sonuc = saglayici(transport).cancel(BELGE_KIMLIGI, channel="EARSIV", uuid=ETTN)

    assert sonuc.status == FAILED
    assert sonuc.status != CANCELLED
    # Sağlayıcının KENDİ gerekçesi kullanıcı mesajına taşınıyor; ham hata kodu
    # (`10013`) `raw` içinde denetim için duruyor, kullanıcı cümlesinde değil.
    # ÖLÇÜLDÜ, ve sonuç bugünün SINIRINI da gösteriyor: `10008` mevcut hata
    # sınıflarının HİÇBİRİNE eşlenmiyor (kümede "kayıt bulunamadı" YOK), o
    # yüzden `UNKNOWN` şablonuna düşüyor ve o şablon yalnız KODU taşıyor —
    # sağlayıcının kendi cümlesi ("...bulunamamıştır") kullanıcı mesajına
    # GİRMİYOR, `raw` içinde denetime kalıyor.
    #
    # Bu, testin kabullendiği bir eksiklik değil KAYDETTİĞİ bir eksiklik:
    # `NOT_FOUND` sınıfı eklemek spec §6 mesaj tablosunu değiştirir ve AYRI
    # BİR DİLİMİN işidir (`docs/izibiz-sandbox-bulgular.md` §8.3). Buradaki
    # iddia, o dilim geldiğinde KIMILDAYACAK ve gözden geçirilmeye zorlayacak.
    assert "10008" in (sonuc.error or ""), sonuc.error
    assert "bulunamamıştır" not in (sonuc.error or ""), sonuc.error
    assert "bulunamamıştır" in str(sonuc.raw), sonuc.raw


def test_IPTAL_OKUNAMAYAN_GOVDEDE_DE_BASARISIZ() -> None:
    """"Anlamadım" cevabı "iptal edildi" cevabı DEĞİLDİR (fail-closed).

    ``CancelEArchiveInvoiceResponse``ta bir DURUM ALANI olmadığı için başarının
    tek işareti hata zarfının YOKLUĞUDUR — ve bu, okunamayan bir gövdeyi
    sessizce başarı sayma riskini doğurur. ``_business_failure`` fail-closed
    olduğu için üç bozuk gövdenin üçü de başarısız sayılıyor.
    """
    for bozuk in (
        HttpResponse(200, b""),  # boş
        HttpResponse(200, b"<html>502 Bad Gateway</html>"),  # proxy sayfası
        HttpResponse(200, b"<Envelope><Body><Bilinmeyen/></Body></Envelope>"),  # zarf yok
    ):
        transport = SahteTasima([LOGIN_OK, bozuk])
        sonuc = saglayici(transport).cancel(BELGE_KIMLIGI, channel="EARSIV", uuid=ETTN)
        assert sonuc.status == FAILED, bozuk.body


def test_IPTAL_ETTNSIZ_LOGIN_BILE_ACMAZ() -> None:
    """Kurulamayan bir istek için oturum açılmaz — kapı ağdan ÖNCE.

    ``CancelEArchiveInvoiceRequest``ta zorunlu tek anahtar ``FATURA_UUID``.
    ETTN yoksa istek KURULAMAZ; bunu login açtıktan sonra fark etmek,
    sağlayıcıda karşılığı olmayan bir oturum bırakırdı.
    """
    transport = SahteTasima([LOGIN_OK, CANCEL_OK])
    sonuc = saglayici(transport).cancel(BELGE_KIMLIGI, channel="EARSIV", uuid="")

    assert sonuc.status == FAILED
    assert transport.calls == [], "ETTN'siz iptal HİÇBİR çağrı yapmamalı"
    assert wire.IZIBIZ_EARCHIVE_CANCEL_NEEDS_ETTN in (sonuc.error or "")


def test_IPTAL_OTOMATIK_TEKRARLANMAZ() -> None:
    """Tek deneme. Tekrar, kabul edilmiş bir iptali "başarısız" bildirebilir.

    Zaman aşımı senaryosu: retry açık olsaydı 3. denemede ``FAILED`` denirdi
    ve entegratör 1. denemeyi çoktan kabul etmiş olabilirdi — zarf gitmiş,
    yerel fatura ISSUED kalmış olurdu.
    """
    transport = SahteTasima([LOGIN_OK, TransportError("kesildi")])
    sonuc = saglayici(transport).cancel(BELGE_KIMLIGI, channel="EARSIV", uuid=ETTN)

    assert sonuc.status == FAILED
    # login + TEK iptal denemesi. Üç olsaydı retry açıktı demektir.
    assert len(transport.calls) == 2, [c.url for c in transport.calls]


def test_NES_IPTALI_AGA_CIKMADAN_REDDEDER() -> None:
    """Nes'te iptal ucu BİLİNMİYOR — ve kapı ``_call``da değil, ondan önce.

    ÖLÇÜLDÜ: ``NesEInvoiceProvider._call`` bir if-zinciridir ve son dalı
    ``check_taxpayer``dır; tanınmayan bir operasyon oraya düşer ve
    ``kwargs["vkn"]`` ile ``KeyError`` verirdi. "Nes zaten yapılandırılmamış"
    bir güvence değil: ``NES_BASE_URL`` dolduğu gün kapı kendiliğinden açılırdı.
    """
    transport = SahteTasima([LOGIN_OK, CANCEL_OK])
    nes = NesEInvoiceProvider(
        SimpleNamespace(
            einvoice_base_url="https://ornek.gecersiz",
            einvoice_username="k",
            einvoice_password="p",
            einvoice_api_key=None,
            einvoice_endpoints_verified=True,
        ),
        transport,
        company_id=1,
    )
    sonuc = nes.cancel(BELGE_KIMLIGI, channel="EARSIV", uuid=ETTN)

    assert sonuc.status == FAILED
    assert transport.calls == []
    assert wire.NES_CANCEL_UNVERIFIED_ERROR in (sonuc.error or "")


# ==========================================================================
# 3) STATİK SÖZLEŞME — sıra, yetki, kiracı yüklemi
# ==========================================================================
def test_YEREL_IPTAL_ENTEGRATORDEN_SONRA_YAZILIR() -> None:
    """Kaynak SIRASI ölçülür: e-belge kapısı, compare-and-set UPDATE'ten ÖNCE.

    Sıra bu ucun SÖZLEŞMESİDİR, üslup değil: aşağıdaki UPDATE bir kez
    koştuğunda fatura YERELDE iptal olmuştur ve sağlayıcı reddederse geri
    alınacak bir şey kalmaz. MUTANT: iki satır yer değiştirir ⇒ bu test kırmızı.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    govde = kaynak.split('@router.post("/{invoice_id}/cancel")', 1)[1]

    kapi = govde.find("_einvoice_cancel_gate(")
    yerel = govde.find("UPDATE invoices SET status='CANCELLED'")
    assert kapi != -1, "e-belge iptal kapısı çağrılmıyor"
    assert yerel != -1, "yerel iptal UPDATE'i bulunamadı"
    assert kapi < yerel, "YEREL İPTAL ENTEGRATÖRÜ GEÇMİŞ — sıra bozuk"


def test_IPTAL_KAPISI_YAZMASI_KIRACI_YUKLEMI_TASIYOR() -> None:
    """Kapının UPDATE'i ``company_id=:cid`` TAŞIR — okuma kapsamının YANINA."""
    kaynak = ROUTER.read_text(encoding="utf-8")
    govde = kaynak.split("def _einvoice_cancel_gate", 1)[1].split("@router.post", 1)[0]

    guncelleme = re.search(r'UPDATE invoices SET einvoice_status=.*?"""', govde, re.S)
    assert guncelleme, "kapının UPDATE ifadesi bulunamadı"
    metin = guncelleme.group(0)
    assert "company_id=:cid" in metin, metin
    assert "WHERE id=:id" in metin, metin


def test_INDIRME_UCU_GET_VE_YETKISI_SALES() -> None:
    """Uç GET'tir ama izni ``read`` DEĞİL — ve bu ÖLÇÜLDÜ.

    ``auth.py``deki açık kural olmasaydı uç, dosyanın genel güvenli-metot
    kuralından ``read``e düşerdi. MUTANT: kural silinir ⇒ bu test kırmızı.
    """
    from app.auth import required_permission

    kaynak = ROUTER.read_text(encoding="utf-8")
    assert '@router.get("/{invoice_id}/einvoice/download")' in kaynak

    assert required_permission("GET", "/api/invoices/{invoice_id}/einvoice/download") == "sales"
    assert required_permission("GET", "/api/invoices/7/einvoice/download") == "sales"
    # Komşular KIMILDAMADI: kural önek+sonek birlikte yazıldığı için iç PDF ve
    # liste hâlâ ``read``.
    assert required_permission("GET", "/api/invoices/{invoice_id}/pdf") == "read"
    assert required_permission("GET", "/api/invoices") == "read"
    assert required_permission("GET", "/api/invoices/{invoice_id}/einvoice/status") == "read"


def test_INDIRME_XML_YOLU_AGA_HIC_CIKMAZ() -> None:
    """``format=xml`` sağlayıcıya SORMAZ: belge saklanan payload'dan üretilir.

    Kaynakta ölçülüyor çünkü mesele bir davranış değil bir BAĞIMLILIK: XML
    dalı ``fetch_pdf``ten ÖNCE dönmeli, yoksa yapılandırma kapısı ve sağlayıcı
    çağrısı XML için de koşardı.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    govde = kaynak.split("def einvoice_download", 1)[1].split("@router.post", 1)[0]

    xml_dali = govde.find('if bicim=="xml"')
    xml_donusu = govde.find('media_type="application/xml"')
    saglayici_kurulumu = govde.find("get_einvoice_provider(")
    saglayici_cagrisi = govde.find("provider.fetch_pdf(")
    assert -1 not in (xml_dali, xml_donusu, saglayici_kurulumu, saglayici_cagrisi)

    # XML dalı, sağlayıcı HİÇ KURULMADAN dönüyor: kurulum ve çağrı, XML'in
    # `return`ünden SONRA. Ölçüm konum üzerinden yapılıyor çünkü mesele bir
    # davranış değil bir BAĞIMLILIK — XML yolu sağlayıcıya bağlı OLMAMALI.
    assert xml_dali < xml_donusu < saglayici_kurulumu < saglayici_cagrisi, (
        "XML dalı sağlayıcı kurulumundan SONRA dönüyor"
    )
    assert "build_invoice_xml(" in govde[xml_dali:xml_donusu]
    # Yapılandırma kapısı (503) da XML için koşmuyor: XML yolu yapılandırma
    # olmadan da çalışır, çünkü ağa çıkmıyor.
    assert govde.find("configuration.configured") > xml_donusu


def test_EFATURA_REDDI_SEKIZ_GUN_KURALINI_TASIYOR() -> None:
    """B2B reddi bir "yapamazsın" değil, bir YOL TARİFİ olmalı.

    Giden bir ticari e-Fatura tek taraflı iptal edilemez: alıcı TTK 18/3
    uyarınca belgenin kendisine ulaşmasından itibaren SEKİZ GÜN içinde itiraz
    eder ve iptal o sürecin sonucudur. Bu artışta uygulama yanıtı
    (``ApplicationResponse``) YOK, bu yüzden metin operatöre asıl yapması
    gerekeni söylemek zorunda — yoksa uç, sebebini söylemeden reddeden bir
    duvara dönerdi.
    """
    from app.routers.invoices import EFATURA_CANCEL_REFUSED

    metin = EFATURA_CANCEL_REFUSED
    assert "SEKİZ GÜN" in metin, metin
    assert "18/3" in metin, metin
    assert "itiraz" in metin, metin
    # Bu sürümün SINIRI da yazılı: uygulama yanıtı göndermiyoruz.
    assert "ApplicationResponse" in metin, metin
