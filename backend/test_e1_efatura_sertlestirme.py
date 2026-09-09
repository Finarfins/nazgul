"""E1 — e-Fatura/e-Arşiv sertleştirmesi. Mock taşıma, soket YOK.

Beş ölçülmüş boşluk kapandı; bu dosya beşini de ADIYLA ölçer:

1. ``WEB_KEY`` gönderim yanıtında geliyordu ve ATILIYORDU → e-Arşiv PDF'i
   kalıcı olarak erişilemezdi. Artık saklanıyor (göç ``20260911_0081``).
2. e-Arşiv ``fetch_pdf`` çağrıyı HİÇ KURMUYORDU (``EARSIV_WEB_KEY_YOK``).
   E1'de anahtar varsa kurmaya başladı; E2b'de ÖLÇÜLDÜ ki o çağrı PDF DEĞİL
   UBL XML'i döndürüyor ve OPERASYON DEĞİŞTİ (``GetEArchiveInvoiceList`` +
   ``CONTENT_TYPE=PDF``), anahtar da GEREKMİYOR — bkz.
   ``docs/izibiz-sandbox-bulgular.md`` §10 ve ``test_e2c_earsiv_pdf.py``.
3. ``GET .../einvoice/status`` YALNIZ yerel veritabanını okuyor; sağlayıcının
   cevabını alacak bir yol YOKTU → ``POST .../einvoice/sync``.
4. Birim kodları UN/ECE Rec.20 değildi ("kg" tel üstünde geçersiz).
5. %0 KDV satırı ``TaxExemptionReasonCode`` taşımıyordu.

MUTASYON TABLOSU — her satır ADIYLA kırmızı olur:

===========================================  ==================================
mutant                                       öldüren test
===========================================  ==================================
``_submit_web_key`` -> ``return None``       ``test_WEB_KEY_gonderim_yanitindan_SAKLANIYOR``
``CONTENT_TYPE=PDF``/``HEADER_ONLY=N`` düşer   ``test_EARSIV_fetch_pdf_LISTE_OPERASYONUNDAN_GERCEK_PDF_DONDURUYOR``
ZIP'ten ilk dosya körlemesine döner           ``test_EARSIV_fetch_pdf_ZIP_ICINDE_XML_VARSA_PDF_YOK``
``resolve_unit_code`` -> ``return "C62"``    ``test_BILINMEYEN_BIRIM_SESSIZCE_C62_OLMUYOR``
%0'da istisna kodu yazılmaz                  ``test_SIFIR_KDV_ISTISNA_KODU_OLMADAN_URETILMIYOR``
===========================================  ==================================

``sync`` yazmasının kiracı yüklemini düşüren mutant BU DOSYADA ÖLMEZ (burada
HTTP katmanı yok); onu ``test_e1_efatura_sertlestirme_uc.py`` öldürüyor.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import (
    PENDING,
    EInvoiceError,
    IzibizEInvoiceProvider,
    build_einvoice_payload,
)
from app.einvoice import transport as transport_module
from app.einvoice.errors import UblBuildError
from app.einvoice.provider import _earchive_validation_key
from app.einvoice.transport import HttpResponse
from app.einvoice.ubl import UNECE_UNIT_CODES, resolve_unit_code
from app.einvoice.ubl_xml import build_invoice_xml


BACKEND = Path(__file__).resolve().parent
FIXTURES = BACKEND / "tests" / "fixtures" / "izibiz"
GOC = BACKEND / "alembic" / "versions" / "20260911_0081_einvoice_sertlestirme.py"

#: Fixture'daki gerçek WEB_KEY URL'inin taşıdığı anahtar.
BEKLENEN_ANAHTAR = "web-anahtari"


def fixture(name: str) -> HttpResponse:
    path = FIXTURES / name
    return HttpResponse(int(path.name.rsplit(".", 2)[-2]), path.read_bytes())


class FakeTransport:
    """Senaryo oynatan taşıma. Soket açmaz."""

    def __init__(self, script: list[object] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        self.calls.append(SimpleNamespace(method=method, url=url, body=body))
        item = self.script.pop(0) if self.script else HttpResponse(200, b"<Response/>")
        if isinstance(item, Exception):
            raise item
        return item

    def bodies(self) -> list[str]:
        return [(c.body or b"").decode("utf-8", errors="replace") for c in self.calls]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username="sandbox-kullanici",
        einvoice_password="sandbox-parola",
        einvoice_base_url="https://efaturatest.izibiz.com.tr",
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,
    )


def _provider(transport: FakeTransport) -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(_settings(), transport)


def _fatura(**degisiklikler) -> dict:
    """%20 KDV'li, tek kalemli e-Arşiv faturası."""
    kaynak = {
        "invoice_number": "SNG2026000000001",
        "currency": "TRY",
        "company_id": 7,
        "invoice_id": 42,
        "issued_at": "2026-09-11 14:04:04+03:00",
        "is_efatura_user": False,
        "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
        "customer": {"name": "Deneme Müşteri", "tax_number": "11111111111"},
        "totals": {"tax": Decimal("20.00"), "grand_total": Decimal("120.00")},
        "items": [
            {
                "description": "Kalem",
                "qty": Decimal("1"),
                "unit_code": "adet",
                "unit_price": Decimal("100"),
                "tax_rate": Decimal("20"),
                "total": Decimal("120.00"),
            }
        ],
    }
    kaynak.update(degisiklikler)
    return build_einvoice_payload(kaynak)


def _sifir_kdv_fatura(birim: str = "ton") -> dict:
    return _fatura(
        totals={"tax": Decimal("0.00"), "grand_total": Decimal("100.00")},
        items=[
            {
                "description": "Bugday",
                "qty": Decimal("3"),
                "unit_code": birim,
                "unit_price": Decimal("33.33"),
                "tax_rate": Decimal("0"),
                "total": Decimal("100.00"),
            }
        ],
    )


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "_sleep", lambda seconds: None)


# --- 1. WEB_KEY saklanıyor -------------------------------------------------
def test_WEB_KEY_gonderim_yanitindan_SAKLANIYOR() -> None:
    """Gönderim sonucu ``web_key`` TAŞIR — yanıt bir daha gelmez.

    MUTANT: ``_submit_web_key`` -> ``return None`` ⇒ bu test kırmızı.
    """
    transport = FakeTransport([fixture("Login.200.xml"), fixture("WriteToArchiveExtended.200.xml")])
    sonuc = _provider(transport).submit(_fatura())

    assert sonuc.status == PENDING, sonuc.error
    assert sonuc.web_key, "WEB_KEY yakalanmadı — e-Arşiv PDF'i kalıcı erişilemez olurdu"
    assert BEKLENEN_ANAHTAR in sonuc.web_key
    # Ham değer OLDUĞU GİBİ taşınır (portal URL'i); anahtar çağrı anında çıkarılır.
    assert sonuc.web_key.startswith("https://"), sonuc.web_key


def test_WEB_KEY_URL_ICINDEN_ANAHTARI_CIKARIYOR() -> None:
    """``WEB_KEY`` çıplak anahtar DEĞİL bir URL; asıl anahtar parametresidir."""
    url = "https://portaltest.izibiz.com.tr/x.xhtml?webValidationKey=abc123&viewType=PDF"
    assert _earchive_validation_key(url) == "abc123"
    assert _earchive_validation_key("abc123") == "abc123"
    assert _earchive_validation_key(None) == ""


# --- 2. e-Arşiv fetch_pdf açıldı ------------------------------------------
def test_EARSIV_fetch_pdf_LISTE_OPERASYONUNDAN_GERCEK_PDF_DONDURUYOR() -> None:
    """PDF, GERÇEK sandbox yanıtından çıkarılıyor (fixture, uydurma değil).

    E2c ÖLÇÜMÜ (`docs/izibiz-sandbox-bulgular.md` §10.1): e-Arşiv PDF'i
    `GetEArchiveInvoice` + `WEB_VALIDATION_KEY` ile GELMEZ — o operasyon UBL
    XML'ini taşıyan bir ZIP döner. PDF `GetEArchiveInvoiceList`ten
    `HEADER_ONLY=N` + `CONTENT_TYPE=PDF` ile gelir ve içerik yine bir ZIP'tir,
    bu kez `<belge>.pdf` taşır.

    MUTANT: `CONTENT_TYPE=PDF` satırını düşürmek ⇒ sağlayıcı XML döner ve
    `_pdf_from_zip` onu reddeder ⇒ KIRMIZI. `HEADER_ONLY=N`i düşürmek ⇒ yanıt
    içerik düğümü HİÇ taşımaz ⇒ KIRMIZI. Operasyonu eski
    `GetEArchiveInvoice`a geri almak ⇒ istek iddiası KIRMIZI.
    """
    transport = FakeTransport(
        [fixture("Login.200.xml"), fixture("GetEArchiveInvoiceList-pdf.200.xml")]
    )
    icerik = _provider(transport).fetch_pdf("SNG2026252113200", channel="EARSIV")

    # GERÇEK PDF: ZIP'in içinden çıkan bayt dizisi `%PDF-` ile başlıyor.
    assert icerik.startswith(b"%PDF-"), icerik[:20]
    assert len(icerik) > 1000, len(icerik)

    gonderilen = transport.bodies()[-1]
    assert "GetEArchiveInvoiceListRequest" in gonderilen
    assert "<ID>SNG2026252113200</ID>" in gonderilen
    assert "<HEADER_ONLY>N</HEADER_ONLY>" in gonderilen
    assert "<CONTENT_TYPE>PDF</CONTENT_TYPE>" in gonderilen
    # ESKİ YOL ARTIK KURULMUYOR — anahtar alanı isteğe HİÇ girmiyor.
    assert "WEB_VALIDATION_KEY" not in gonderilen


def test_EARSIV_fetch_pdf_ARTIK_WEB_KEY_ISTEMIYOR() -> None:
    """Anahtarsız çağrı ARTIK KAPALI DEĞİL — ve bu bir gevşetme değil DÜZELTME.

    E1'de `EARSIV_WEB_KEY_YOK` kapısı "anahtarsız istek boş yanıt üretir"
    gerekçesiyle konmuştu. §10.1 ölçtü ki o gerekçe YANLIŞ TEŞHİSTİ: anahtar
    doğruydu, çağrı kuruluyordu, dönen şey PDF DEĞİLDİ. Yeni operasyon belgeyi
    `ID` ile arıyor, yani anahtar GEREKMİYOR — ve bu, gönderim anındaki
    `WEB_KEY` yakalaması kaçmış ESKİ faturaların PDF'ini de erişilebilir
    kılıyor.

    MUTANT: `web_key` ön koşulunu geri koymak ⇒ bu test KIRMIZI.
    """
    transport = FakeTransport(
        [fixture("Login.200.xml"), fixture("GetEArchiveInvoiceList-pdf.200.xml")]
    )
    icerik = _provider(transport).fetch_pdf("SNG2026252113200", channel="EARSIV", web_key=None)
    assert icerik.startswith(b"%PDF-")


def test_EARSIV_fetch_pdf_ZIP_ICINDE_XML_VARSA_PDF_YOK() -> None:
    """`GetEArchiveInvoice`in GERÇEK yanıtı: ZIP içinde XML. PDF SAYILMAZ.

    Bu, düzeltmenin en önemli negatif kapısıdır: ZIP'i açıp içinden ÇIKAN İLK
    dosyayı PDF diye sunmak, kullanıcıya `.pdf` adıyla bir UBL XML'i indirtirdi
    — sessiz yanlış. `_pdf_from_zip` yalnız `%PDF-` ile başlayan girdiyi kabul
    eder ve `fetch_pdf` gürültülü biçimde `PDF_YOK` der.

    MUTANT: `_pdf_from_zip`teki `%PDF-` denetimini düşürüp ilk dosyayı
    döndürmek ⇒ bu test KIRMIZI.
    """
    import base64 as _b64
    import io as _io
    import zipfile as _zip

    tampon = _io.BytesIO()
    with _zip.ZipFile(tampon, "w") as arsiv:
        arsiv.writestr("SNG1.xml", b"<Invoice/>")
    govde = (
        b"<?xml version='1.0'?><GetEArchiveInvoiceResponse "
        b'xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
        b'<REQUEST_RETURN xmlns=""><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN>'
        b'<INVOICE xmlns="">' + _b64.b64encode(tampon.getvalue()) + b"</INVOICE>"
        b"</GetEArchiveInvoiceResponse>"
    )
    transport = FakeTransport([fixture("Login.200.xml"), HttpResponse(200, govde)])
    with pytest.raises(EInvoiceError) as hata:
        _provider(transport).fetch_pdf("SNG1", channel="EARSIV")
    assert "PDF_YOK" in str(hata.value.message)


# --- 3. GİB ham durum kodu ------------------------------------------------
def test_SORGU_HAM_GIB_KODUNU_AYRICA_TASIYOR() -> None:
    """İç durum eşlemesi kodu YUTUYORDU; ham kod artık yanında duruyor."""
    transport = FakeTransport(
        [fixture("Login.200.xml"), fixture("GetEArchiveInvoiceStatus.200.xml")]
    )
    sonuc = _provider(transport).query_status(
        "SNG2026210141633", channel="EARSIV", uuid="034EE590-0D2F-4291-9B71-4AA1060FFA7E"
    )
    # Fixture'daki ham değer 105; iç durum ONA EŞLENİR ama kod kaybolmaz.
    assert sonuc.gib_status_code == "105", sonuc.gib_status_code
    assert sonuc.status != "105", "ham kod iç durumun YERİNE geçmemeli"


def test_SORGU_WEB_KEYI_IKINCI_SANS_OLARAK_TASIYOR() -> None:
    """Durum yanıtı da ``WEB_KEY`` veriyor; gönderimde kaçtıysa PDF kurtulur."""
    transport = FakeTransport(
        [fixture("Login.200.xml"), fixture("GetEArchiveInvoiceStatus.200.xml")]
    )
    sonuc = _provider(transport).query_status(
        "SNG2026210141633", channel="EARSIV", uuid="034EE590-0D2F-4291-9B71-4AA1060FFA7E"
    )
    assert sonuc.web_key and BEKLENEN_ANAHTAR in sonuc.web_key


# --- 4. UN/ECE birim kodları ----------------------------------------------
@pytest.mark.parametrize(
    "birim,kod",
    [("adet", "C62"), ("kg", "KGM"), ("lt", "LTR"), ("ton", "TNE"), ("m", "MTR"), ("paket", "PA")],
)
def test_BIRIM_UNECE_KODUNA_CEVRILIYOR(birim: str, kod: str) -> None:
    assert resolve_unit_code(birim) == kod
    assert resolve_unit_code(birim.upper()) == kod


def test_BILINMEYEN_BIRIM_SESSIZCE_C62_OLMUYOR() -> None:
    """Bilinmeyen birim HATA verir; "adet" varsayılmaz.

    MUTANT: ``resolve_unit_code`` -> ``return "C62"`` ⇒ bu test kırmızı.
    GEREKÇE: 3 ton buğdayı ``C62`` ile göndermek GİB'e "3 adet" demektir ve
    belge SESSİZCE yanlış olur — reddedilmez, kabul edilir ve yanlış kalır.
    """
    with pytest.raises(UblBuildError) as hata:
        resolve_unit_code("kutu")
    assert "kutu" in str(hata.value)

    # Ve bu, BELGENİN TAMAMINI üretilemez yapar — yarım belge çıkmaz.
    with pytest.raises(UblBuildError):
        _sifir_kdv_fatura(birim="kutu")


def test_ZATEN_KOD_OLAN_BIRIM_OLDUGU_GIBI_GECER() -> None:
    for kod in sorted(set(UNECE_UNIT_CODES.values())):
        assert resolve_unit_code(kod) == kod


def test_BIRIM_XMLE_KOD_OLARAK_CIKIYOR() -> None:
    xml = build_invoice_xml(_sifir_kdv_fatura()).decode("utf-8")
    assert 'unitCode="TNE"' in xml
    assert 'unitCode="ton"' not in xml


# --- 5. %0 KDV istisna kodu -----------------------------------------------
def test_SIFIR_KDV_ISTISNA_KODU_URETIYOR() -> None:
    """%0 satır ``TaxExemptionReasonCode`` + ``Reason`` taşır."""
    payload = _sifir_kdv_fatura()
    kova = payload["tax_subtotals"][0]
    assert kova["tax_exemption_reason_code"] == "351", kova
    assert kova["tax_exemption_reason"]

    xml = build_invoice_xml(payload).decode("utf-8")
    assert "<cbc:TaxExemptionReasonCode>351</cbc:TaxExemptionReasonCode>" in xml
    # HEM belge kovasında HEM satır kovasında (ikisi de %0).
    assert xml.count("<cbc:TaxExemptionReasonCode>") == 2


def test_SIFIR_KDV_ISTISNA_KODU_OLMADAN_URETILMIYOR() -> None:
    """Kod düşürülürse belge ÜRETİLMEZ.

    MUTANT: %0'da istisna alanları yazılmaz ⇒ bu test kırmızı. Reddedilecek
    bir belgeyi "gönderildi" saymaktansa hata.
    """
    payload = _sifir_kdv_fatura()
    for kova in payload["tax_subtotals"]:
        kova.pop("tax_exemption_reason_code", None)
    with pytest.raises(UblBuildError):
        build_invoice_xml(payload)


def test_SIFIRDAN_FARKLI_ORAN_ISTISNA_TASIMIYOR() -> None:
    """İstisna kodu taşıyan %20'lik bir satır da aynı ölçüde yanlıştır."""
    xml = build_invoice_xml(_fatura()).decode("utf-8")
    assert "TaxExemptionReason" not in xml


# --- 6. Alıcı vergi dairesi -----------------------------------------------
def test_ALICI_VERGI_DAIRESI_TASINIYOR() -> None:
    """Önce YALNIZ satıcı tarafı geçiyordu; tüzel kişi alıcıda "-" kalıyordu."""
    payload = _fatura(
        is_efatura_user=True,
        company={"id": 7, "name": "S", "tax_number": "1111111111", "tax_office": "Kadıköy"},
        # 10 hane ⇒ VKN ⇒ PartyTaxScheme yazılır (11 hane TCKN'de yazılmaz)
        customer={"name": "Alıcı Ltd", "tax_number": "2222222222", "tax_office": "Beşiktaş"},
    )
    assert payload["customer"]["tax_office"] == "Beşiktaş"
    xml = build_invoice_xml(payload).decode("utf-8")
    assert "Beşiktaş" in xml, "alıcı vergi dairesi XML'e çıkmadı"
    assert "Kadıköy" in xml


# --- 7. Adres: ÖLÇÜLDÜ, kaynakta il YOK -----------------------------------
def test_ADRES_ILI_KAYNAKTA_YOK_OLCULDU() -> None:
    """``City``/``CitySubdivision`` HÂLÂ "-" ve bu EKSİK DEĞİL, ÖLÇÜMDÜR.

    ÖLÇÜLDÜ (``app/core_schema.py``): ``customers`` tablosunda YALNIZ
    ``address`` (Text) var; ``city``/``district`` sütunu YOK. Kaynakta il/ilçe
    olmadığı için UBL'e yazılacak bir değer de yoktur ve adresi ayrıştırmaya
    çalışmak (virgülden bölmek gibi) UYDURMAK olurdu — yanlış bir il, boş bir
    ilden daha kötüdür.

    TODO (AYRI DİLİM, GÖÇ GEREKTİRİR): ``customers``a ``city``/``district``
    açılınca burası gerçek değeri yazmalı ve bu test o zaman DEĞİŞMELİ. Bu
    satırlar o değişikliğin ÇİVİSİDİR: sütun açıldığı anda kırmızı olur.
    """
    from app.core_schema import customers

    sutunlar = {c.name for c in customers.columns}
    assert "city" not in sutunlar, "il sütunu açıldı — UBL adresi de güncellenmeli"
    assert "district" not in sutunlar, "ilçe sütunu açıldı — UBL adresi de güncellenmeli"
    assert "address" in sutunlar

    xml = build_invoice_xml(_fatura()).decode("utf-8")
    assert "<cbc:CityName>-</cbc:CityName>" in xml


# --- 8. Göç sözleşmesi ----------------------------------------------------
def test_GOC_UC_SUTUN_NULLABLE_VE_GERI_ALINABILIR() -> None:
    kaynak = GOC.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    atamalar = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in agac.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    assert atamalar["revision"] == "20260911_0081"
    assert atamalar["down_revision"] == "20260910_0080"

    for sutun, uzunluk in (
        ("einvoice_web_key", "255"),
        ("einvoice_gib_status_code", "10"),
        ("einvoice_pk_alias", "120"),
    ):
        assert sutun in kaynak, sutun
        assert f"sa.String({uzunluk})" in kaynak, (sutun, uzunluk)
    # Üçü de NULLABLE: eski satırlar bu değerleri TAŞIYAMAZ ve "boş" ile
    # "bilinmiyor" ayırt edilebilir kalmalı.
    assert kaynak.count("nullable=True") == 3, kaynak.count("nullable=True")
    assert "def downgrade" in kaynak and "op.drop_column" in kaynak
