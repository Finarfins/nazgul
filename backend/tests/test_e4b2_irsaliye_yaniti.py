"""E4b-2 — e-İRSALİYE YANITI (ReceiptAdvice): göç 0089, durum makinesi, sync, yanıt ucu.

Konu: göç `20260915_0089`, `app/einvoice/edespatch.py` (durum makinesi +
`receipt_advice_coz`), `app/einvoice/provider.py::get_receipt_advice`,
`app/routers/despatch_notes.py` (sync, `GET .../response`),
`app/despatch_schema.py`.

Keşif: `docs/e4b-kismi-sevk-kesif-2026-09-10.md` §3, §5, §6.

Test belgeleri SENTETİKTİR (`tests/fixtures/e4b2/`): satırlı yanıt okuma ve
KISMI_KABUL DOĞRULANMADI; sandbox bu PR'ın parçası değil.

--- MUTASYON TABLOSU -------------------------------------------------------

  * `yaniti_kaydet`in ön okumasını VE göçteki `uq_despatch_responses_uuid`i
    birlikte kaldırmak
                  -> `test_SYNC_KISMI_KABUL_yazar_ve_IKINCI_sync_IDEMPOTENT` KIRMIZI
  * yalnız ön okumayı kaldırmak -> YEŞİL KALIR (UNIQUE + kayıt noktası
    yarışı zaten yakalıyor; savunma iki katlı) — ölçüldü, bilinçli
  * yalnız UNIQUE'i kaldırmak -> SQLite YEŞİL, PG ikizinin iki oturumlu
    yarış testi KIRMIZI
  * ön okumayı ETTN yerine `despatch_id` ile anahtarlamak
                  -> `test_TERMINAL_ikinci_farkli_belge_KAYDEDILIR_durum_KIMILDAMAZ` KIRMIZI
  * `durumu_ilerlet`teki "yanıt durumuna yalnız DELIVERED'dan" dalını silmek
                  -> `test_DURUM_MAKINESI_her_cift` KIRMIZI
  * `_yaniti_esle`de bilinmeyen satırı atlamak (hata yerine)
                  -> `test_SYNC_bilinmeyen_satir_422_KAYDEDILIR_yanit_YAZILMAZ` KIRMIZI
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import uuid as uuid_modulu
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260915_0089_despatch_responses.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "e4b2"

_CALISMA_ALANI = tempfile.mkdtemp(prefix="e4b2-yanit-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "e4b2.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "E4b2Yonetim!2026"
ROL_PAROLASI = "E4b2Rolleri!2026"
ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")

#: SENTETİK kimlik/plaka — yalnız biçim.
SOFOR_TCKN = "11111111110"
PLAKA = "34ABC123"

_CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
_CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


def _goc_modulu():
    import importlib.util

    spec = importlib.util.spec_from_file_location("goc0089", GOC)
    goc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(goc)
    return goc


def _belge(ad: str, *, irsaliye_no: str, irsaliye_ettn: str, yanit_ettn: str | None = None) -> bytes:
    metin = (FIXTURES / f"{ad}.xml").read_text(encoding="utf-8")
    return (
        metin.replace("{{YANIT_ETTN}}", yanit_ettn or str(uuid_modulu.uuid4()))
        .replace("{{IRSALIYE_NO}}", irsaliye_no)
        .replace("{{IRSALIYE_ETTN}}", irsaliye_ettn)
        .encode("utf-8")
    )


# ==========================================================================
# 1. STATİK
# ==========================================================================

def test_goc_ZINCIRE_dogru_yerden_bagli() -> None:
    """0089, H17'nin 0088'inin ardından gelir."""
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260915_0089"' in kaynak
    assert 'down_revision = "20260915_0088"' in kaynak
    assert "branch_labels = None" in kaynak


def test_DURUM_KUMESI_goc_ile_modul_ayni() -> None:
    """Göçün CHECK'i ile modülün sözlüğü BİREBİR aynı (E4a kapısının devamı).

    MUTASYON: modüle bir durum eklemek (göce eklemeden) bunu KIRMIZI yapar —
    uygulamanın yazabildiği bir değeri veritabanı reddederdi.
    """
    from app.einvoice import edespatch

    goc = _goc_modulu()
    assert set(goc.DURUMLAR) == set(edespatch.BILINEN)
    assert len(goc.DURUMLAR) == len(edespatch.BILINEN) == 11
    assert set(goc.YANIT_DURUMLARI) == set(edespatch.YANIT_DURUMLARI)
    assert tuple(goc.YANIT_TURLERI) == edespatch.YANIT_TURLERI


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """Yeni iki tablo açılışın `create_all` ettiği hiçbir modülde yok (0072 kusuru)."""
    import re

    acilis = "".join(
        (BACKEND / "app" / m).read_text(encoding="utf-8")
        for m in (
            "tenancy.py", "core_schema.py", "auth.py", "inventory.py",
            "finance_engine.py", "workflow.py",
        )
    )
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))
    assert "companies" in bildirilen, "tarayıcı hiçbir Table() görmüyor"
    assert not {"despatch_responses", "despatch_response_lines"} & bildirilen


def test_SEMA_MODULU_goc_ile_AYNI_SUTUNLAR() -> None:
    """Core tanımı göçün sütunlarını BİREBİR taşır (ad + Numeric ölçeği)."""
    import ast

    from app import despatch_schema

    agac = ast.parse(GOC.read_text(encoding="utf-8"))
    goc_sutunlari: dict[str, set[str]] = {}
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Call) and getattr(dugum.func, "attr", None) == "create_table":
            tablo = dugum.args[0].id
            goc_sutunlari[tablo] = {
                arg.args[0].value
                for arg in dugum.args[1:]
                if isinstance(arg, ast.Call) and getattr(arg.func, "attr", None) == "Column"
            }
    goc = _goc_modulu()
    assert set(despatch_schema.despatch_responses.c.keys()) == goc_sutunlari["YANIT"]
    assert set(despatch_schema.despatch_response_lines.c.keys()) == goc_sutunlari["YANIT_SATIR"]
    assert goc.YANIT == "despatch_responses" and goc.YANIT_SATIR == "despatch_response_lines"
    for sutun in ("received_quantity", "rejected_quantity"):
        tip = despatch_schema.despatch_response_lines.c[sutun].type
        assert (tip.precision, tip.scale) == (18, 4)


def test_FIXTURE_belgeleri_SENTETIK_basligi_tasiyor() -> None:
    """Brifing kuralı: her test belgesi SENTETİK olduğunu başlığında söyler."""
    dosyalar = sorted(FIXTURES.glob("*.xml"))
    assert [d.stem for d in dosyalar] == [
        "bilinmeyen_satir", "eksik_ret_miktari", "kabul", "kismi_kabul", "red",
    ]
    for dosya in dosyalar:
        bas = dosya.read_text(encoding="utf-8")[:600]
        assert "SENTETİK BELGE" in bas and "UBL-TR 1.2.1" in bas and "sandbox kaydı DEĞİLDİR" in bas


# ==========================================================================
# 2. AYRIŞTIRICI — saf, veritabanı yok
# ==========================================================================

_NO = "IRS2026000000001"
_ETTN = "bbbbbbbb-0000-4000-8000-000000000001"


@pytest.mark.parametrize(
    ("ad", "tur", "satirlar"),
    [
        ("kabul", "KABUL", [(1, "10.0000", "0.0000", None), (2, "5.0000", "0.0000", None)]),
        ("red", "RED", [(1, "0.0000", "10.0000", "Yanlış alıcı"), (2, "0.0000", "5.0000", "Yanlış alıcı")]),
        ("kismi_kabul", "KISMI_KABUL", [(1, "10.0000", "0.0000", None), (2, "3.7500", "1.2500", "Hasarlı ambalaj")]),
        # EKSİK `RejectedQuantity` = 0 (tolerans): tam kabul eden alıcı yazmayabilir.
        ("eksik_ret_miktari", "KABUL", [(1, "10.0000", "0.0000", None), (2, "5.0000", "0.0000", None)]),
    ],
)
def test_AYRISTIRICI_fixture_turleri(ad, tur, satirlar) -> None:
    from app.einvoice import edespatch

    yanit_ettn = "aaaaaaaa-0000-4000-8000-00000000000a"
    belge = edespatch.receipt_advice_coz(
        _belge(ad, irsaliye_no=_NO, irsaliye_ettn=_ETTN, yanit_ettn=yanit_ettn)
    )
    assert belge.tur == tur
    assert belge.uuid == yanit_ettn
    assert belge.irsaliye_no == _NO and belge.irsaliye_uuid == _ETTN
    assert str(belge.duzenleme) == "2026-09-16"
    assert [
        (s.satir_no, str(s.alinan), str(s.reddedilen), s.gerekce) for s in belge.satirlar
    ] == satirlar
    assert all(s.belge_no == _NO for s in belge.satirlar)


def test_AYRISTIRICI_tur_kurali() -> None:
    """KABUL = hiç ret yok; RED = hiç alınan yok; gerisi KISMI_KABUL."""
    from app.einvoice import edespatch as e

    def s(a, r):
        return e.YanitSatiri(1, None, Decimal(a), Decimal(r), None)

    assert e.yanit_turu_hesapla([s("1", "0"), s("2", "0")]) == e.KABUL
    assert e.yanit_turu_hesapla([s("0", "1"), s("0", "2")]) == e.RED
    assert e.yanit_turu_hesapla([s("1", "0"), s("0", "2")]) == e.KISMI_KABUL
    assert e.yanit_turu_hesapla([s("1", "1")]) == e.KISMI_KABUL
    with pytest.raises(e.ReceiptAdviceError) as hata:
        e.yanit_turu_hesapla([])
    assert hata.value.code == "YANIT_SATIRSIZ"
    assert {e.yanit_durumu(t) for t in e.YANIT_TURLERI} == set(e.YANIT_DURUMLARI)


def _bozuk(kok_ekleri: str = "", satirlar: str | None = None, *, kimlik=True) -> bytes:
    varsayilan = (
        "<cac:ReceiptLine><cbc:ReceivedQuantity>1</cbc:ReceivedQuantity>"
        "<cac:DespatchLineReference><cbc:LineID>1</cbc:LineID></cac:DespatchLineReference>"
        "</cac:ReceiptLine>"
    )
    kimlik_xml = (
        "<cbc:ID>ALC2026000000001</cbc:ID><cbc:UUID>aaaaaaaa-0000-4000-8000-000000000001</cbc:UUID>"
        "<cbc:IssueDate>2026-09-16</cbc:IssueDate>"
        if kimlik
        else ""
    )
    return (
        '<ReceiptAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:ReceiptAdvice-2" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        f"{kimlik_xml}{kok_ekleri}{varsayilan if satirlar is None else satirlar}</ReceiptAdvice>"
    ).encode("utf-8")


def _satir(no="1", alinan="1", red=None) -> str:
    red_xml = f"<cbc:RejectedQuantity>{red}</cbc:RejectedQuantity>" if red is not None else ""
    ref = (
        f"<cac:DespatchLineReference><cbc:LineID>{no}</cbc:LineID></cac:DespatchLineReference>"
        if no is not None
        else ""
    )
    return f"<cac:ReceiptLine><cbc:ReceivedQuantity>{alinan}</cbc:ReceivedQuantity>{red_xml}{ref}</cac:ReceiptLine>"


_KATI_ORNEKLER = {
    "XML_DEGIL": b"bu xml degil",
    "KOK_YANLIS": b"<DespatchAdvice/>",
    # Varlık bombası: defusedxml REDDEDER, istisna olarak kaçmaz.
    "VARLIK_BOMBASI": (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b"<ReceiptAdvice>&b;</ReceiptAdvice>"
    ),
    "KIMLIKSIZ": _bozuk(kimlik=False),
    "SATIRSIZ": _bozuk(satirlar=""),
    "REFSIZ": _bozuk(satirlar=_satir(no=None)),
    "TEKRAR": _bozuk(satirlar=_satir() + _satir()),
    "NEGATIF": _bozuk(satirlar=_satir(alinan="-1")),
    "BES_HANE": _bozuk(satirlar=_satir(alinan="1.00001")),
    "SAYI_DEGIL": _bozuk(satirlar=_satir(alinan="bir")),
    "IKISI_SIFIR": _bozuk(satirlar=_satir(alinan="0", red="0")),
}


@pytest.mark.parametrize(
    ("ornek", "kod"),
    [
        ("XML_DEGIL", "YANIT_AYRISTIRILAMADI"),
        ("KOK_YANLIS", "YANIT_AYRISTIRILAMADI"),
        ("VARLIK_BOMBASI", "YANIT_AYRISTIRILAMADI"),
        ("KIMLIKSIZ", "YANIT_KIMLIK_EKSIK"),
        ("SATIRSIZ", "YANIT_SATIRSIZ"),
        ("REFSIZ", "YANIT_SATIR_REFERANSI_YOK"),
        ("TEKRAR", "YANIT_SATIR_TEKRAR"),
        ("NEGATIF", "YANIT_MIKTAR_GECERSIZ"),
        ("BES_HANE", "YANIT_MIKTAR_GECERSIZ"),
        ("SAYI_DEGIL", "YANIT_MIKTAR_GECERSIZ"),
        ("IKISI_SIFIR", "YANIT_SATIR_BOS"),
    ],
)
def test_AYRISTIRICI_KATI_dallar_ADLI_hata(ornek, kod) -> None:
    """Kimlik, satır referansı ve miktar TAHMİN EDİLMEZ; adı konmuş hata verir."""
    from app.einvoice import edespatch

    with pytest.raises(edespatch.ReceiptAdviceError) as hata:
        edespatch.receipt_advice_coz(_KATI_ORNEKLER[ornek])
    assert hata.value.code == kod


def test_ESLEME_ANAHTARI_bizim_DespatchLine_IDmiz() -> None:
    """Yanıt satırı `DespatchLineReference/LineID` ile eşlenir ve o değer BİZİM
    DespatchAdvice'ımızın `DespatchLine/cbc:ID`sidir = `despatch_lines.line_no`.

    `OrderLineReference/LineID` DEĞİL: E4b-1'de o faturanın satır sırasıdır ve
    bu testte bilerek FARKLI (7, 3). Biri ötekiyle karıştırılırsa bu KIRMIZI.
    """
    from app.einvoice import edespatch
    from tests.test_e4a_despatch_notes import _ornek_payload

    payload = _ornek_payload(
        lines=[
            {"id": 1, "name": "Bugday", "quantity": Decimal("10"), "order_line_id": 7},
            {"id": 2, "name": "Arpa", "quantity": Decimal("5"), "order_line_id": 3},
        ]
    )
    kok = ElementTree.fromstring(edespatch.build_despatch_xml(payload))
    satirlar = kok.findall(f"{_CAC}DespatchLine")
    assert [s.findtext(f"{_CBC}ID") for s in satirlar] == ["1", "2"]
    assert [s.findtext(f"{_CAC}OrderLineReference/{_CBC}LineID") for s in satirlar] == ["7", "3"]
    yanit = edespatch.receipt_advice_coz(
        _belge("kismi_kabul", irsaliye_no=_NO, irsaliye_ettn=_ETTN)
    )
    assert [s.satir_no for s in yanit.satirlar] == [1, 2]


# ==========================================================================
# 3. DURUM MAKİNESİ — HER ÇİFT
# ==========================================================================

#: BAĞIMSIZ yazılmış beklenti: her durumdan HANGİ gelen durum bir GEÇİŞTİR.
#: Listede olmayan her (mevcut, gelen) çifti `mevcut`ta KALIR. Uygulamanın
#: rank tablosundan TÜRETİLMEDİ — türetilseydi hiçbir şeyi ölçmezdi.
GECISLER: dict[str, set[str]] = {
    "NONE": {"FAILED", "UNKNOWN", "QUEUED", "PROCESSING", "SIGNED", "SENT", "DELIVERED"},
    "FAILED": {"NONE", "UNKNOWN", "QUEUED", "PROCESSING", "SIGNED", "SENT", "DELIVERED"},
    "UNKNOWN": {"QUEUED", "PROCESSING", "SIGNED", "SENT", "DELIVERED"},
    "QUEUED": {"PROCESSING", "SIGNED", "SENT", "DELIVERED"},
    "PROCESSING": {"SIGNED", "SENT", "DELIVERED"},
    "SIGNED": {"SENT", "DELIVERED"},
    "SENT": {"DELIVERED"},
    # E4b-2: DELIVERED artık terminal DEĞİL; üç yanıt durumuna açılır.
    "DELIVERED": {"ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED"},
    "ACCEPTED": set(),
    "PARTIALLY_ACCEPTED": set(),
    "REJECTED": set(),
}


def test_DURUM_MAKINESI_her_cift() -> None:
    """11 × 11 = 121 çift. Özellikle: `SENT -> ACCEPTED` GEÇİŞ DEĞİL (yanıt
    yalnız teslim edilmiş belgeye gelir), `DELIVERED -> REJECTED` AÇIK
    (Şef kararı), terminal üçlü kımıldamaz."""
    from app.einvoice import edespatch

    assert set(GECISLER) == set(edespatch.BILINEN)
    for mevcut in sorted(edespatch.BILINEN):
        for gelen in sorted(edespatch.BILINEN):
            beklenen = gelen if gelen in GECISLER[mevcut] else mevcut
            assert edespatch.durumu_ilerlet(mevcut, gelen) == beklenen, (mevcut, gelen)


def test_TERMINAL_ve_GONDERIM_KAPALI_kumeleri() -> None:
    """E4a'nın `test_TERMINAL_yalniz_DELIVERED`i (TERMINAL == {DELIVERED},
    "REJECTED BILINEN'de yok") E4b-2 ile BİLİNÇLİ olarak tersine döndü."""
    from app.einvoice import edespatch

    assert edespatch.TERMINAL == frozenset({"ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED"})
    assert edespatch.DELIVERED not in edespatch.TERMINAL
    assert edespatch.BILINEN - edespatch.GONDERIM_KAPALI == {edespatch.NONE, edespatch.FAILED}


def test_SAGLAYICI_KODU_yanit_durumu_URETEMEZ() -> None:
    """Durum SORGUSU ticari yanıt taşımaz: iç adlar bile `UNKNOWN`a düşer."""
    from app.einvoice import edespatch

    for ad in ("ACCEPTED", "partially_accepted", "REJECTED"):
        assert edespatch.kodu_coz(ad) == edespatch.UNKNOWN
    assert not set(edespatch.SAGLAYICI_KODLARI.values()) & edespatch.YANIT_DURUMLARI
    assert edespatch.kodu_coz("133") == edespatch.DELIVERED


# ==========================================================================
# 4. SAĞLAYICI — sahte taşıma
# ==========================================================================

class _SahteTasima:
    def __init__(self, yanitlar):
        self._yanitlar = list(yanitlar)
        self.istekler: list[dict] = []

    def request(self, method, url, *, body=None, headers=None, max_bytes=None):
        self.istekler.append({"url": url, "body": body})
        if not self._yanitlar:
            raise AssertionError("beklenmeyen fazladan çağrı: " + url)
        sonraki = self._yanitlar.pop(0)
        if isinstance(sonraki, Exception):
            raise sonraki
        return sonraki


def _yanit(govde: str, durum: int = 200):
    from app.einvoice.transport import HttpResponse

    return HttpResponse(durum, govde.encode("utf-8"))


def _login():
    return _yanit(
        '<s:Envelope xmlns:s="x"><s:Body><LoginResponse>'
        "<SESSION_ID>OTURUM</SESSION_ID></LoginResponse></s:Body></s:Envelope>"
    )


def _saglayici(yanitlar):
    from app.einvoice.provider import IzibizEInvoiceProvider

    class _Ayarlar:
        einvoice_base_url = "https://efaturatest.izibiz.com.tr"
        einvoice_username = "kullanici"
        einvoice_password = "parola"
        einvoice_endpoints_verified = True
        izibiz_env = "test"

    tasima = _SahteTasima(yanitlar)
    return IzibizEInvoiceProvider(_Ayarlar(), tasima, company_id=1), tasima


def test_saglayici_GetReceiptAdvice_istegi_ve_ICERIK_cozumu() -> None:
    """İstek: e-İrsaliye servisi, `DIRECTION=IN`, `UUID`=bizim ETTN'imiz.
    Yanıt: base64 XML ve base64 ZIP içeriklerinin İKİSİ de çözülür."""
    from app.einvoice.ubl_xml import zip_single

    xml = _belge("kabul", irsaliye_no=_NO, irsaliye_ettn=_ETTN)
    govde = (
        '<s:Envelope xmlns:s="x"><s:Body><GetReceiptAdviceResponse>'
        f'<RECEIPTADVICE ID="A" UUID="1"><CONTENT>{base64.b64encode(xml).decode()}</CONTENT></RECEIPTADVICE>'
        f'<RECEIPTADVICE ID="B" UUID="2"><CONTENT>{base64.b64encode(zip_single("b.xml", xml)).decode()}'
        "</CONTENT></RECEIPTADVICE>"
        "</GetReceiptAdviceResponse></s:Body></s:Envelope>"
    )
    saglayici, tasima = _saglayici([_login(), _yanit(govde)])
    belgeler = saglayici.get_receipt_advice(_ETTN)
    assert belgeler == [xml, xml]
    istek = tasima.istekler[-1]
    assert istek["url"].endswith("/EIrsaliyeWS/EIrsaliye")
    assert b"GetReceiptAdviceRequest" in istek["body"]
    assert f"<UUID>{_ETTN}</UUID>".encode() in istek["body"]
    assert b"<DIRECTION>IN</DIRECTION>" in istek["body"]


@pytest.mark.parametrize(
    "govde",
    [
        '<s:Envelope xmlns:s="x"><s:Body><GetReceiptAdviceResponse/></s:Body></s:Envelope>',
        '<s:Envelope xmlns:s="x"><s:Body><GetReceiptAdviceResponse><ERROR_TYPE>'
        "<ERROR_CODE>10008</ERROR_CODE><ERROR_SHORT_DES>Kayit yok</ERROR_SHORT_DES>"
        "</ERROR_TYPE></GetReceiptAdviceResponse></s:Body></s:Envelope>",
    ],
)
def test_saglayici_YANIT_YOK_bos_liste(govde) -> None:
    saglayici, _ = _saglayici([_login(), _yanit(govde)])
    assert saglayici.get_receipt_advice(_ETTN) == []


def test_saglayici_HATA_bos_listeyle_KARISMAZ() -> None:
    """Taşıma hatası, iş hatası ve ÇÖZÜLEMEYEN içerik FIRLATIR — boş liste
    "yanıt yok" demektir ve gelmiş bir reddi saklayamaz."""
    from app.einvoice.errors import EInvoiceError
    from app.einvoice.transport import TransportError

    saglayici, _ = _saglayici([_login()] + [TransportError("zaman asimi")] * 3)
    with pytest.raises(EInvoiceError):
        saglayici.get_receipt_advice(_ETTN)
    is_hatasi = (
        '<s:Envelope xmlns:s="x"><s:Body><GetReceiptAdviceResponse><ERROR_TYPE>'
        "<ERROR_CODE>10003</ERROR_CODE><ERROR_SHORT_DES>Gecersiz</ERROR_SHORT_DES>"
        "</ERROR_TYPE></GetReceiptAdviceResponse></s:Body></s:Envelope>"
    )
    saglayici, _ = _saglayici([_login(), _yanit(is_hatasi)])
    with pytest.raises(EInvoiceError):
        saglayici.get_receipt_advice(_ETTN)
    bozuk = (
        '<s:Envelope xmlns:s="x"><s:Body><GetReceiptAdviceResponse>'
        "<RECEIPTADVICE><CONTENT>JVBERi0xLjcgYnUgeG1sIGRlZ2ls</CONTENT></RECEIPTADVICE>"
        "</GetReceiptAdviceResponse></s:Body></s:Envelope>"
    )
    saglayici, _ = _saglayici([_login(), _yanit(bozuk)])
    with pytest.raises(EInvoiceError):
        saglayici.get_receipt_advice(_ETTN)


def test_saglayici_TEKRARLANABILIR_ve_NOOP_REDDEDER() -> None:
    from app.einvoice.errors import EInvoiceError
    from app.einvoice.provider import IzibizEInvoiceProvider, NoOpEInvoiceProvider

    assert "get_receipt_advice" in IzibizEInvoiceProvider._RELOGIN_REPEATABLE
    with pytest.raises(EInvoiceError):
        NoOpEInvoiceProvider().get_receipt_advice(_ETTN)
    saglayici, tasima = _saglayici([])
    with pytest.raises(EInvoiceError):
        saglayici.get_receipt_advice("")
    assert tasima.istekler == [], "boş ETTN ile ağa çıkıldı"


# ==========================================================================
# 5. GÖÇ TURU — ayrı SQLite dosyası, alt süreç
# ==========================================================================

def _alembic(url: str, *argumanlar: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *argumanlar],
        cwd=str(BACKEND),
        env={**os.environ, "DATABASE_URL": url, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _sema_izi(motor, tablo: str) -> dict:
    from sqlalchemy import inspect

    d = inspect(motor)
    return {
        "check": sorted((c["name"], c["sqltext"]) for c in d.get_check_constraints(tablo)),
        "unique": sorted(
            (u["name"], tuple(u["column_names"])) for u in d.get_unique_constraints(tablo)
        ),
        "fk": sorted(
            (
                f.get("name") or "",
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in d.get_foreign_keys(tablo)
        ),
        "index": sorted((i["name"], tuple(i["column_names"])) for i in d.get_indexes(tablo)),
        "sutun": [c["name"] for c in d.get_columns(tablo)],
    }


def test_GOC_up_down_up_KORUNAN_kisitlar_ve_GERI_ALMA_reddi() -> None:
    """SQLite batch yeniden inşası 0083/0087'nin kısıtlarını KAYBETMEZ.

    Ölçü: 0088'de `despatch_notes` yansıması -> 0089'da aynısı + iki sütun +
    genişleyen durum CHECK'i + özet CHECK'i -> geri alınca 0088'inkiyle
    BİREBİR. Yanıt durumunda bir irsaliye varken geri alma ADIYLA durur.
    """
    from sqlalchemy import create_engine, inspect, text

    dosya = os.path.join(_CALISMA_ALANI, "goc_turu.db").replace(os.sep, "/")
    url = f"sqlite:///{dosya}"
    sonuc = _alembic(url, "upgrade", "20260915_0088")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        once = _sema_izi(motor, "despatch_notes")
        satir_once = _sema_izi(motor, "despatch_lines")
    finally:
        motor.dispose()
    assert {c[0] for c in once["check"]} == {"ck_despatch_notes_tasima", "ck_despatch_notes_durum"}

    sonuc = _alembic(url, "upgrade", "head")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        sonra = _sema_izi(motor, "despatch_notes")
        assert sonra["unique"] == once["unique"]
        assert sonra["fk"] == once["fk"]
        assert sonra["index"] == once["index"]
        assert sonra["sutun"] == once["sutun"] + ["response_status", "response_received_at"]
        checkler = dict(sonra["check"])
        assert set(checkler) == {
            "ck_despatch_notes_tasima",
            "ck_despatch_notes_durum",
            "ck_despatch_notes_response_status",
        }
        assert checkler["ck_despatch_notes_tasima"] == dict(once["check"])["ck_despatch_notes_tasima"]
        for durum in ("ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED", "DELIVERED"):
            assert f"'{durum}'" in checkler["ck_despatch_notes_durum"]
        # 0087'nin tablosu yeniden inşadan etkilenmedi.
        assert _sema_izi(motor, "despatch_lines") == satir_once
        yanit = _sema_izi(motor, "despatch_responses")
        assert ("uq_despatch_responses_uuid", ("company_id", "response_uuid")) in yanit["unique"]
        assert {f[1:] for f in yanit["fk"]} == {
            (("company_id",), "companies", ("id",)),
            (("company_id", "despatch_id"), "despatch_notes", ("company_id", "id")),
        }
        yanit_satir = _sema_izi(motor, "despatch_response_lines")
        assert {f[1:] for f in yanit_satir["fk"]} == {
            (("company_id",), "companies", ("id",)),
            (("company_id", "response_id"), "despatch_responses", ("company_id", "id")),
            (("company_id", "despatch_line_id"), "despatch_lines", ("company_id", "id")),
        }
        assert {c[0] for c in yanit_satir["check"]} == {
            "ck_despatch_response_lines_received_nonneg",
            "ck_despatch_response_lines_rejected_nonneg",
            "ck_despatch_response_lines_total_positive",
        }

        # Yanıt durumunda bir irsaliye: geri alma ADIYLA durur. (Ham motor
        # FK zorlamaz; satır yalnız durum sütunu için var.)
        an = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
        with motor.begin() as b:
            b.execute(
                text(
                    "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                    "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                    "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                    "VALUES(1,1,'00000000-0000-4000-8000-0000000e4b2a','IRS2026000000001',"
                    "'2026-09-15',:t,'A',:tc,:p,'Adres','34000','ACCEPTED',:t,:t)"
                ),
                {"t": an, "tc": SOFOR_TCKN, "p": PLAKA},
            )
    finally:
        motor.dispose()
    sonuc = _alembic(url, "downgrade", "20260915_0088")
    assert sonuc.returncode != 0
    assert "ticari yanit durumunda e-Irsaliye var" in sonuc.stderr, sonuc.stderr[-2000:]
    assert "(ACCEPTED)" in sonuc.stderr

    motor = create_engine(url)
    try:
        with motor.begin() as b:
            b.execute(text("UPDATE despatch_notes SET edespatch_status='DELIVERED'"))
    finally:
        motor.dispose()
    sonuc = _alembic(url, "downgrade", "20260915_0088")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]
    motor = create_engine(url)
    try:
        assert _sema_izi(motor, "despatch_notes") == once
        assert not inspect(motor).has_table("despatch_responses")
        assert not inspect(motor).has_table("despatch_response_lines")
    finally:
        motor.dispose()
    sonuc = _alembic(url, "upgrade", "head")
    assert sonuc.returncode == 0, sonuc.stderr[-2000:]


# ==========================================================================
# 6. UÇLAR — GERÇEK uygulama, GERÇEK HTTP
# ==========================================================================

@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin_basliklari(istemci):
    giris = istemci.post(
        "/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI}
    )
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    basliklar = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password",
        headers=basliklar,
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


_SAYAC = {"n": 0}


def _sql_fatura(cid: int, kalemler: list[tuple[str, str, str]]) -> dict:
    """Doğrudan SQL ile fatura + kalemler (E4b-1 testinin yardımcısıyla aynı)."""
    from sqlalchemy import text

    from app.db import SessionLocal

    _SAYAC["n"] += 1
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        fatura = db.execute(
            text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,"
                "exchange_rate,customer_snapshot,machine_snapshot,work_order_snapshot,"
                "company_snapshot,technician_snapshot,warranty_snapshot,totals_snapshot,"
                "tax_snapshot,created_by,created_at,updated_at) VALUES(:c,:n,'INVOICE',"
                "'ISSUED','TRY',1,:musteri,'{}','{}',:firma,'{}','{}','{}','{}',1,:t,:t) "
                "RETURNING id"
            ),
            {
                "c": cid, "n": f"E4B2-{_SAYAC['n']}-{uuid_modulu.uuid4().hex[:6]}", "t": an,
                "musteri": json.dumps({"name": "E4b2 Alici", "tax_number": "9876543210", "address": "B"}),
                "firma": json.dumps({"name": "E4b2 Firma", "tax_number": "1234567890", "address": "A"}),
            },
        ).scalar_one()
        kimlikler = []
        for tur, ad, miktar in kalemler:
            kimlikler.append(
                db.execute(
                    text(
                        "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                        "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                        "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                        "VALUES(:c,:f,:tur,:ad,:m,1,1,0,0,0,1,0,1,0,:k) RETURNING id"
                    ),
                    {
                        "c": cid, "f": fatura, "tur": tur, "ad": ad, "m": miktar,
                        "k": json.dumps({"product_id": 900 + len(kimlikler)} if tur == "PART" else {}),
                    },
                ).scalar_one()
            )
        db.commit()
    return {"id": int(fatura), "kalemler": [int(k) for k in kimlikler]}


def _durum_yaz(despatch_id: int, durum: str, gib_kodu: str | None = None) -> None:
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        db.execute(
            text(
                "UPDATE despatch_notes SET edespatch_status=:s,edespatch_gib_status_code=:g,"
                "edespatch_last_error=NULL WHERE id=:id"
            ),
            {"s": durum, "g": gib_kodu, "id": despatch_id},
        )
        db.commit()


def _satir_sayilari(despatch_id: int) -> tuple[int, int]:
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        yanit = db.execute(
            text("SELECT COUNT(*) FROM despatch_responses WHERE despatch_id=:d"), {"d": despatch_id}
        ).scalar_one()
        satir = db.execute(
            text(
                "SELECT COUNT(*) FROM despatch_response_lines WHERE response_id IN "
                "(SELECT id FROM despatch_responses WHERE despatch_id=:d)"
            ),
            {"d": despatch_id},
        ).scalar_one()
    return int(yanit), int(satir)


@pytest.fixture()
def irsaliye(istemci, admin_basliklari):
    """İki mal satırlı (Bugday 10, Arpa 5) taze bir irsaliye, `DELIVERED`.
    Her test KENDİ irsaliyesini alır — sıra bağımlılığı yok."""
    fatura = _sql_fatura(
        int(admin_basliklari["X-Company-ID"]),
        [("LABOR", "Servis İşçiliği", "1"), ("PART", "Bugday", "10"), ("PART", "Arpa", "5")],
    )
    yanit = istemci.post(
        "/api/despatch-notes",
        headers=admin_basliklari,
        json={
            "invoice_id": fatura["id"],
            "actual_shipment_at": "2026-09-15T08:30:00+00:00",
            "driver_name": "Ahmet Yilmaz",
            "driver_national_id": SOFOR_TCKN,
            "vehicle_plate": PLAKA,
            "delivery_address": "Depo Yolu 7",
            "delivery_postal_code": "34710",
        },
    )
    assert yanit.status_code == 201, yanit.text
    govde = yanit.json()
    assert [s["line_no"] for s in govde["lines"]] == [1, 2]
    _durum_yaz(govde["id"], "DELIVERED", "133")
    return govde


class _Saglayici:
    """Sahte sağlayıcı: durum kodu + yanıt belgeleri. Yanıt çağrılarını sayar."""

    def __init__(self, durum="DELIVERED", kod="133", belgeler=(), hata=None):
        self.durum, self.kod, self.belgeler, self.hata = durum, kod, list(belgeler), hata
        self.yanit_cagrisi = 0

    def despatch_status(self, uuid):
        from app.einvoice.provider import EInvoiceResult

        return EInvoiceResult(status=self.durum, uuid=uuid, gib_status_code=self.kod)

    def get_receipt_advice(self, uuid):
        self.yanit_cagrisi += 1
        if self.hata is not None:
            raise self.hata
        return list(self.belgeler)


@pytest.fixture()
def saglayici_kur(monkeypatch):
    from app.einvoice.provider import EInvoiceConfiguration
    from app.routers import despatch_notes as uc

    monkeypatch.setattr(
        uc, "einvoice_configuration",
        lambda _s: EInvoiceConfiguration(configured=True, provider="izibiz", reason=""),
    )

    def kur(saglayici):
        monkeypatch.setattr(uc, "get_einvoice_provider", lambda *a, **k: saglayici)
        return saglayici

    return kur


def _sync(istemci, h, irsaliye_id):
    return istemci.post(f"/api/despatch-notes/{irsaliye_id}/edespatch/sync", headers=h)


def test_SYNC_KISMI_KABUL_yazar_ve_IKINCI_sync_IDEMPOTENT(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    """İlk sync: 1 yanıt + 2 satır, durum PARTIALLY_ACCEPTED, `changed: true`.
    İkinci sync AYNI belgeyle: satır sayıları DEĞİŞMEDİ, `changed: false`."""
    h = admin_basliklari
    belge = _belge(
        "kismi_kabul", irsaliye_no=irsaliye["despatch_number"], irsaliye_ettn=irsaliye["despatch_uuid"]
    )
    saglayici_kur(_Saglayici(belgeler=[belge]))

    ilk = _sync(istemci, h, irsaliye["id"])
    assert ilk.status_code == 200, ilk.text
    govde = ilk.json()
    assert govde["changed"] is True
    assert govde["edespatch_status"] == "PARTIALLY_ACCEPTED"
    assert govde["response_status"] == "KISMI_KABUL"
    assert govde["response_received_at"]
    assert govde["receipt_advice"] == {"asked": True, "found": 1, "recorded": 1, "skipped": 0}
    # Ham sağlayıcı kodu KORUNDU.
    assert govde["edespatch_gib_status_code"] == "133"
    assert "raw_xml" not in json.dumps(govde)
    assert _satir_sayilari(irsaliye["id"]) == (1, 2)

    ikinci = _sync(istemci, h, irsaliye["id"])
    assert ikinci.status_code == 200, ikinci.text
    assert ikinci.json()["changed"] is False
    assert ikinci.json()["receipt_advice"] == {"asked": True, "found": 1, "recorded": 0, "skipped": 0}
    assert ikinci.json()["edespatch_status"] == "PARTIALLY_ACCEPTED"
    assert ikinci.json()["response_received_at"] == govde["response_received_at"]
    assert _satir_sayilari(irsaliye["id"]) == (1, 2)

    oku = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/response", headers=h)
    assert oku.status_code == 200, oku.text
    yanit = oku.json()
    assert "raw_xml" not in json.dumps(yanit)
    assert yanit["responses_count"] == 1
    assert yanit["response"]["response_type"] == "KISMI_KABUL"
    assert yanit["response"]["response_number"] == "ALC2026000000042"
    assert yanit["response"]["issue_date"] == "2026-09-16"
    assert yanit["implicit_accept_due_at"] is None  # yanıt geldi, süre anlamsız
    assert [
        (s["line_no"], s["item_name"], s["despatched_quantity"], s["received_quantity"],
         s["rejected_quantity"], s["reject_reason"])
        for s in yanit["lines"]
    ] == [
        (1, "Bugday", "10.0000", "10.0000", "0.0000", None),
        (2, "Arpa", "5.0000", "3.7500", "1.2500", "Hasarlı ambalaj"),
    ]
    assert yanit["lines"][0]["product_id"] == 901


@pytest.mark.parametrize(
    ("ad", "durum", "tur"),
    [
        ("kabul", "ACCEPTED", "KABUL"),
        ("eksik_ret_miktari", "ACCEPTED", "KABUL"),
        ("red", "REJECTED", "RED"),
    ],
)
def test_SYNC_KABUL_ve_TESLIM_SONRASI_RED(
    istemci, admin_basliklari, irsaliye, saglayici_kur, ad, durum, tur
) -> None:
    """RED teslimden SONRA geldi: saklanır (Şef), nota mevzuat satırı düşer,
    sağlayıcının ham kodu (133) ezilmez."""
    from app.einvoice import edespatch

    h = admin_basliklari
    saglayici_kur(_Saglayici(belgeler=[
        _belge(ad, irsaliye_no=irsaliye["despatch_number"], irsaliye_ettn=irsaliye["despatch_uuid"])
    ]))
    yanit = _sync(istemci, h, irsaliye["id"])
    assert yanit.status_code == 200, yanit.text
    assert yanit.json()["edespatch_status"] == durum
    assert yanit.json()["response_status"] == tur
    assert yanit.json()["edespatch_gib_status_code"] == "133"
    notlar = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/response", headers=h
    ).json()["response"]["notes"]
    if tur == "RED":
        assert notlar.splitlines()[0] == edespatch.TESLIM_SONRASI_RED_NOTU
    else:
        assert edespatch.TESLIM_SONRASI_RED_NOTU not in (notlar or "")


def test_SYNC_bilinmeyen_satir_422_KAYDEDILIR_yanit_YAZILMAZ(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    """Eşlenemeyen satır 500 DEĞİL, adlı 422; hata `edespatch_last_error`a
    YAZILIR, HİÇBİR yanıt satırı yazılmaz, durum sorgusunun sonucu saklanır."""
    h = admin_basliklari
    _durum_yaz(irsaliye["id"], "SENT", "137")
    saglayici_kur(_Saglayici(belgeler=[
        _belge(
            "bilinmeyen_satir",
            irsaliye_no=irsaliye["despatch_number"],
            irsaliye_ettn=irsaliye["despatch_uuid"],
        )
    ]))
    yanit = _sync(istemci, h, irsaliye["id"])
    assert yanit.status_code == 422, yanit.text
    assert yanit.json()["detail"]["code"] == "YANIT_SATIRI_ESLESMEDI"
    assert yanit.json()["detail"]["line"] == 99
    assert _satir_sayilari(irsaliye["id"]) == (0, 0)
    durum = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=h).json()
    # Durum sorgusunun sonucu (SENT -> DELIVERED) SAKLANDI; yanıt yazılmadı.
    assert durum["edespatch_status"] == "DELIVERED"
    assert durum["response_status"] is None
    assert durum["edespatch_last_error"].startswith("YANIT_SATIRI_ESLESMEDI:")


def test_SYNC_saglayici_hatasi_502_HICBIR_SEY_yazilmaz(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    """Yanıt sorgusu düşerse durum sorgusunun sonucu DA yazılmaz."""
    from app.einvoice.errors import EInvoiceError

    h = admin_basliklari
    _durum_yaz(irsaliye["id"], "SENT", "137")
    once = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=h).json()
    saglayici = saglayici_kur(_Saglayici(hata=EInvoiceError("NETWORK", "Sağlayıcıya ulaşılamadı")))
    yanit = _sync(istemci, h, irsaliye["id"])
    assert yanit.status_code == 502, yanit.text
    assert saglayici.yanit_cagrisi == 1
    sonra = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=h).json()
    assert sonra == once
    assert sonra["edespatch_status"] == "SENT"
    assert _satir_sayilari(irsaliye["id"]) == (0, 0)


def test_SYNC_DELIVERED_oncesi_yanit_SORULMAZ(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    h = admin_basliklari
    _durum_yaz(irsaliye["id"], "QUEUED", "101")
    saglayici = saglayici_kur(_Saglayici(durum="SENT", kod="137"))
    yanit = _sync(istemci, h, irsaliye["id"])
    assert yanit.status_code == 200, yanit.text
    assert saglayici.yanit_cagrisi == 0
    assert yanit.json()["edespatch_status"] == "SENT"
    assert yanit.json()["receipt_advice"]["asked"] is False
    assert yanit.json()["changed"] is True


def test_SYNC_BASKA_irsaliyenin_yaniti_ATLANIR(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    """`SEARCH_KEY/UUID` süzgeci DOĞRULANMADI: başka irsaliyeye ait belge yazılmaz."""
    h = admin_basliklari
    saglayici_kur(_Saglayici(belgeler=[
        _belge("red", irsaliye_no="IRS2026999999999", irsaliye_ettn=str(uuid_modulu.uuid4()))
    ]))
    yanit = _sync(istemci, h, irsaliye["id"])
    assert yanit.status_code == 200, yanit.text
    assert yanit.json()["receipt_advice"] == {"asked": True, "found": 1, "recorded": 0, "skipped": 1}
    assert yanit.json()["edespatch_status"] == "DELIVERED"
    assert _satir_sayilari(irsaliye["id"]) == (0, 0)


def test_TERMINAL_ikinci_farkli_belge_KAYDEDILIR_durum_KIMILDAMAZ(
    istemci, admin_basliklari, irsaliye, saglayici_kur
) -> None:
    """KABUL'den sonra gelen FARKLI ETTN'li bir RED: denetim için saklanır,
    ama terminal durum ve özet değişmez; etkili yanıt İLKİ."""
    h = admin_basliklari
    kw = {"irsaliye_no": irsaliye["despatch_number"], "irsaliye_ettn": irsaliye["despatch_uuid"]}
    saglayici_kur(_Saglayici(belgeler=[_belge("kabul", **kw)]))
    assert _sync(istemci, h, irsaliye["id"]).json()["edespatch_status"] == "ACCEPTED"
    saglayici_kur(_Saglayici(belgeler=[_belge("red", **kw)]))
    ikinci = _sync(istemci, h, irsaliye["id"])
    assert ikinci.status_code == 200, ikinci.text
    assert ikinci.json()["receipt_advice"]["recorded"] == 1
    assert ikinci.json()["edespatch_status"] == "ACCEPTED"
    assert ikinci.json()["response_status"] == "KABUL"
    assert _satir_sayilari(irsaliye["id"]) == (2, 4)
    oku = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/response", headers=h).json()
    assert oku["responses_count"] == 2
    assert oku["response"]["response_type"] == "KABUL"


def test_YANIT_UCU_yanitsiz_ve_ZIMNI_KABUL_tarihi(istemci, admin_basliklari, irsaliye) -> None:
    """Yanıt yoksa 200 + `response: null`. Zımni kabul = fiili sevk + 7 gün,
    YALNIZ `DELIVERED`dayken; detay görünümü de aynı değeri taşır."""
    h = admin_basliklari
    yanit = istemci.get(f"/api/despatch-notes/{irsaliye['id']}/response", headers=h)
    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    assert govde["response"] is None and govde["lines"] == [] and govde["responses_count"] == 0
    beklenen = (datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc) + timedelta(days=7)).isoformat()
    assert govde["implicit_accept_due_at"] == beklenen
    detay = istemci.get(f"/api/despatch-notes/{irsaliye['id']}", headers=h).json()
    assert detay["implicit_accept_due_at"] == beklenen
    assert detay["response_status"] is None
    _durum_yaz(irsaliye["id"], "SENT", "137")
    assert istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/response", headers=h
    ).json()["implicit_accept_due_at"] is None


def test_CAPRAZ_KIRACI_yanit_ucu_404_ve_FK_reddi(istemci, admin_basliklari, irsaliye) -> None:
    """Başka firmanın irsaliyesi 404. Bileşik FK: bir firmanın yanıtı başka
    firmanın irsaliyesine, yanıt satırı başka firmanın sevk satırına YAZILAMAZ
    (SQLite `foreign_keys=ON` — `app/db.py`; PG ikizi aynısını ölçer)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.db import SessionLocal

    h = admin_basliklari
    cid = int(h["X-Company-ID"])
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        yabanci = int(db.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES('E4b2 Yabanci',1,:t) RETURNING id"),
            {"t": an},
        ).scalar_one())
        db.commit()
    assert istemci.get("/api/despatch-notes/999999/response", headers=h).status_code == 404
    # Başka firmanın irsaliye kimliği bizim kapsamımızda da 404 (varlık sızmaz).
    with SessionLocal() as db:
        yabanci_fatura = _sql_fatura(yabanci, [("PART", "Yabanci", "1")])["id"]
        yabanci_irsaliye = db.execute(
            text(
                "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                "issue_date,actual_shipment_at,driver_name,driver_national_id,vehicle_plate,"
                "delivery_address,delivery_postal_code,edespatch_status,created_at,updated_at) "
                "VALUES(:c,:f,:u,'IRS2026000999999','2026-09-15',:t,'A',:tc,:p,'Adres','34000',"
                "'DELIVERED',:t,:t) RETURNING id"
            ),
            {"c": yabanci, "f": yabanci_fatura, "u": str(uuid_modulu.uuid4()), "t": an, "tc": SOFOR_TCKN, "p": PLAKA},
        ).scalar_one()
        db.commit()
    assert istemci.get(f"/api/despatch-notes/{yabanci_irsaliye}/response", headers=h).status_code == 404

    with SessionLocal() as db:
        satir_id = db.execute(
            text("SELECT id FROM despatch_lines WHERE despatch_id=:d ORDER BY line_no LIMIT 1"),
            {"d": irsaliye["id"]},
        ).scalar_one()
    ekle_yanit = (
        "INSERT INTO despatch_responses(company_id,despatch_id,response_uuid,response_number,"
        "response_type,issue_date,raw_xml,created_at) VALUES(:c,:d,:u,'ALC2026000000099','KABUL',"
        "'2026-09-16','<x/>',:t) RETURNING id"
    )
    # Yanıt: yabancı firma + BİZİM irsaliyemiz -> bileşik FK reddi.
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            db.execute(text(ekle_yanit), {"c": yabanci, "d": irsaliye["id"], "u": str(uuid_modulu.uuid4()), "t": an})
        db.rollback()
    with SessionLocal() as db:
        bizim = db.execute(
            text(ekle_yanit), {"c": cid, "d": irsaliye["id"], "u": str(uuid_modulu.uuid4()), "t": an}
        ).scalar_one()
        db.commit()
    satir_ekle = (
        "INSERT INTO despatch_response_lines(company_id,response_id,despatch_line_id,"
        "received_quantity,rejected_quantity) VALUES(:c,:r,:s,:a,:b)"
    )
    # Satır: yabancı firma + bizim yanıt + bizim sevk satırı -> iki FK de reddeder.
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            db.execute(text(satir_ekle), {"c": yabanci, "r": bizim, "s": satir_id, "a": "1", "b": "0"})
        db.rollback()
    # CHECK: ikisi sıfır ya da negatif miktar.
    for alinan, reddedilen in (("0", "0"), ("-1", "2")):
        with SessionLocal() as db:
            with pytest.raises(IntegrityError):
                db.execute(text(satir_ekle), {"c": cid, "r": bizim, "s": satir_id, "a": alinan, "b": reddedilen})
            db.rollback()
    with SessionLocal() as db:
        db.execute(text("DELETE FROM despatch_responses WHERE id=:r"), {"r": bizim})
        db.commit()


# ==========================================================================
# 7. ROL MATRİSİ — yeni GET ucu
# ==========================================================================

@pytest.fixture(scope="module")
def rol_basliklari(istemci, admin_basliklari):
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = int(admin_basliklari["X-Company-ID"])
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for rol in ROLLER:
            uid = db.execute(
                text(
                    "INSERT INTO app_users(username,email,display_name,password_hash,"
                    "role,is_active,must_change_password,email_verified,created_at)"
                    " VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id"
                ),
                {
                    "k": f"e4b2_{rol}", "e": f"e4b2_{rol}@ornek.test", "d": f"E4b2 {rol}",
                    "p": hash_password(ROL_PAROLASI), "r": rol, "a": True, "m": False,
                    "v": True, "t": an,
                },
            ).scalar_one()
            db.execute(
                text(
                    "INSERT INTO user_company_memberships(user_id,company_id,"
                    "is_default,created_at) VALUES(:u,:c,1,:t)"
                ),
                {"u": uid, "c": cid, "t": an},
            )
        db.commit()
    basliklar = {}
    for rol in ROLLER:
        giris = istemci.post(
            "/api/auth/login", json={"username": f"e4b2_{rol}", "password": ROL_PAROLASI}
        )
        assert giris.status_code == 200, (rol, giris.text)
        basliklar[rol] = {
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(cid),
        }
    return basliklar


def test_ROL_MATRISI_yanit_ucu(istemci, rol_basliklari, irsaliye) -> None:
    """ÖLÇÜLDÜ: `sales` taşıyanlar (yonetici, muhasebe, satis) 200; `depo` ve
    `rapor` 403 — E4a/E4b-1 irsaliye uçlarıyla AYNI matris, yeni kural YOK."""
    from app.auth import ROLE_PERMISSIONS, required_permission

    assert required_permission("GET", f"/api/despatch-notes/{irsaliye['id']}/response") == "sales"
    matris = {
        rol: istemci.get(f"/api/despatch-notes/{irsaliye['id']}/response", headers=b).status_code
        for rol, b in rol_basliklari.items()
    }
    assert matris == {
        "yonetici": 200, "muhasebe": 200, "satis": 200, "depo": 403, "rapor": 403,
    }, matris
    assert {r for r in ROLLER if "sales" in ROLE_PERMISSIONS[r]} == {"yonetici", "muhasebe", "satis"}
