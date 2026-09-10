"""E4a — e-İRSALİYE DÜZ SEVK: göç 0083, durum makinesi, UBL, uçlar.

Konu: göç `20260913_0083`, `app/einvoice/edespatch.py`,
`app/einvoice/provider.py` (üç yeni İzibiz metodu),
`app/routers/despatch_notes.py`, `app/auth.py`in `/api/despatch-notes`
önek kuralı.

Keşif raporu: `docs/e4-eirsaliye-kesif-2026-09-09.md`.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `edespatch.SAGLAYICI_KODLARI`ndaki `"100"`ı `QUEUED`a çevirmek
    (e-Arşiv eşlemesini ödünç almak)
                                    -> `test_100_e_arsivin_anlamini_ALMIYOR` KIRMIZI
  * `"106"`yı herhangi bir duruma eşlemek
                                    -> `test_106_CELISKILI_oldugu_icin_ESLENMIYOR` KIRMIZI
  * `TERMINAL`e `SENT` eklemek      -> `test_TERMINAL_yalniz_DELIVERED` KIRMIZI
  * `GONDERIM_KAPALI`dan `UNKNOWN`ı çıkarmak
                                    -> `test_ETTN_denemeler_boyunca_SABIT` KIRMIZI
  * `durumu_ilerlet`teki `UNKNOWN` dalını rank karşılaştırmasına indirmek
                                    -> `test_UNKNOWN_canli_belgeyi_GERI_CEKMIYOR` KIRMIZI
  * `bilinmeyeni_yok_say`ı `durumu_ilerlet`in içine gömmek
                                    -> `test_BELGE_YOK_cevabi_UNKNOWNi_FAILEDe_cevirir` KIRMIZI
  * Göçten `UNIQUE(company_id, invoice_id)` kısıtını düşürmek
                                    -> `test_IKINCI_irsaliye_409` KIRMIZI
  * `_fatura`dan `company_id=:cid` yüklemini düşürmek
                                    -> `test_CAPRAZ_KIRACI_faturasi_404` KIRMIZI
  * `auth.py`deki `/api/despatch-notes` önek kuralını silmek
                                    -> ROL MATRİSİ KIRMIZI (GET'ler `read`e,
                                       POST'lar `__admin_only__`a düşerdi)
  * `download`un `pdf` dalını 501 yerine bir sağlayıcı çağrısına çevirmek
                                    -> `test_PDF_501_fail_closed` KIRMIZI
  * `submit_despatch`in `TransportError` dalını `FAILED`e çevirmek
                                    -> `test_TIMEOUT_UNKNOWN_uretir` ve
                                       `test_ETTN_denemeler_boyunca_SABIT` KIRMIZI
  * `despatch_uuid`i gönderim anında yeniden üretmek
                                    -> `test_ETTN_denemeler_boyunca_SABIT` KIRMIZI
  * `_ubl_payload`daki miktarı `float`a çevirmek
                                    -> `test_UBL_miktarlar_fatura_ile_ESIT` KIRMIZI
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260913_0083_eirsaliye.py"
MODUL = BACKEND / "app" / "einvoice" / "edespatch.py"
UC = BACKEND / "app" / "routers" / "despatch_notes.py"

_CALISMA_ALANI = tempfile.mkdtemp(prefix="e4a-despatch-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "e4a.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "E4aYonetim!2026"
ROL_PAROLASI = "E4aRolleri!2026"

#: Sevk edilmiş roller (`admin` HARİÇ — o `*` taşır ve matriste ORAKUL
#: olarak kullanılıyor, satır olarak DEĞİL). `test_sec3_read_daraltma.py`
#: ile AYNI liste.
ROLLER = ("yonetici", "muhasebe", "satis", "depo", "rapor")

#: SENTETİK kimlik/plaka — gerçek bir TCKN ya da plaka DEĞİL, yalnız biçim.
SOFOR_TCKN = "11111111110"
PLAKA = "34ABC123"
DORSE = "34XY456"

_CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
_CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


def _kok(xml: bytes):
    return ElementTree.fromstring(xml.decode("utf-8"))


# ==========================================================================
# 1. STATİK KAPILAR — göç ve modül, uygulama ayağa kalkmadan
# ==========================================================================

def test_goc_ZINCIRE_dogru_yerden_bagli() -> None:
    """0083, 0082'nin ARDINDAN gelir ve ikinci bir baş açmaz."""
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260913_0083"' in kaynak
    assert 'down_revision = "20260912_0082"' in kaynak
    assert "branch_labels = None" in kaynak


def test_DURUM_KUMESI_goc_ile_modul_ayni() -> None:
    """Göçün CHECK'i ile modülün sözlüğü BİREBİR aynı olmalı.

    MUTASYON: modüle yeni bir durum eklemek (göce eklemeden) bunu KIRMIZI
    yapar — ve davranışta çok daha kötüsünü: uygulamanın yazabildiği bir
    değeri veritabanı REDDEDERDİ, yani gönderim sonrası UPDATE patlar ve
    saklanmamış bir belge sağlayıcıda canlı kalırdı.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("goc0083", GOC)
    goc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(goc)
    from app.einvoice import edespatch

    assert set(goc.DURUMLAR) == set(edespatch.BILINEN)
    assert len(goc.DURUMLAR) == len(edespatch.BILINEN) == 8


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """`despatch_notes` hiçbir `Table()` bildiriminde OLMAMALI.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu): bir modül tabloyu
    `Table()` olarak bildirirse uygulamanın AÇILIŞI onu alembic'ten ÖNCE
    kurabiliyor; göç tabloyu VAR bulup `return` ediyor ve KISITLAR
    (UNIQUE, CHECK, FK) HİÇ KURULMUYOR. Göç yeşil biter, şema eksiktir.
    """
    import re

    acilis = ""
    for modul in (
        "tenancy.py", "core_schema.py", "auth.py", "inventory.py",
        "finance_engine.py", "workflow.py",
    ):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))
    # Kapının DAYANDIĞI OLGU da doğrulanıyor: tarayıcı gerçekten bir şey
    # buluyor mu? Bulmasaydı bu test boş bir kümede yeşil yanardı.
    assert "companies" in bildirilen, "tarayıcı hiçbir Table() görmüyor"
    assert "despatch_notes" not in bildirilen


def test_UC_dosyasinda_DINAMIK_sql_yok() -> None:
    """Bu uç `DYNAMIC_SQL_FILE_ALLOWLIST`e GİRMEMELİ.

    MUTASYON: `irsaliye_listesi`in süzgecini bir f-string parçasına
    çevirmek bunu KIRMIZI yapar — ve `test_tenant_scoping_guard.py`nin
    parmak izi kapısını da, çünkü dosya o listeye girmek zorunda kalırdı.
    """
    import ast

    agac = ast.parse(UC.read_text(encoding="utf-8"))
    dinamik = []
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
        if ad != "text" or not dugum.args:
            continue
        if not isinstance(dugum.args[0], ast.Constant):
            dinamik.append(ast.dump(dugum.args[0])[:80])
    assert not dinamik, f"dinamik text() bulundu: {dinamik}"


def test_IPTAL_UCU_YOK() -> None:
    """WSDL'de `CancelDocument` operasyonu YOK (keşif §2.4); uç da olmamalı.

    MUTASYON: `Mark*` operasyonlarını bir "iptal" ucuna bağlamak — keşifin
    açıkça yasakladığı şey — bunu KIRMIZI yapar.
    """
    from app.einvoice import endpoints as wire
    from app.routers import despatch_notes

    # KAYNAK METNI DEGIL ROTA LISTESI olculuyor: gerekce docstring'lerde
    # "cancel" kelimesi GECIYOR ve bir metin taramasi onu bir UC sanardi.
    yollar = {r.path for r in despatch_notes.router.routes}
    assert not [y for y in yollar if y.endswith("/cancel")], yollar
    assert not [
        ad for ad in dir(wire) if ad.startswith("IZIBIZ_OP_CANCEL_DESPATCH")
    ], "e-İrsaliye iptali için bir operasyon sabiti UYDURULMUŞ"
    # Ailenin TAM rota kumesi de civili: sessizce bir sekizinci uc eklenemez.
    assert len(yollar) == 6 and len(despatch_notes.router.routes) == 7


def test_TARIH_kodda_SABITLENMEMIS() -> None:
    """"1 Temmuz 2026" hiçbir yere GÖMÜLMEMELİ (keşif §4.1).

    Yasal geçiş tarihi MÜKELLEF KOŞULUNA bağlıdır: ciro, hesap dönemi ve
    sektör. Tek bir tarihi koda yazmak, koşulu sağlamayan bir kiracıya
    yanlış bir zorunluluk göstermek olurdu.
    """
    for yol in (GOC, MODUL, UC, BACKEND / "app" / "einvoice" / "endpoints.py"):
        metin = yol.read_text(encoding="utf-8")
        for yasak in ("2026-07-01", "01.07.2026", "1 Temmuz 2026", "01/07/2026"):
            assert yasak not in metin, f"{yol.name}: sabit geçiş tarihi {yasak}"


def test_PRODUCTION_ADRESI_yazilmamis() -> None:
    """Keşif §2.1: iki production adayı da **DOĞRULANMADI** (404 / 502).

    Yol yalnız TABAN adrese eklenir; canlı allowlist boş olduğu için
    e-İrsaliye canlı kanalı da mevcut ortam kilidiyle kapalıdır.
    """
    from app.einvoice import endpoints as wire

    assert wire.IZIBIZ_EDESPATCH_PATH == "/EIrsaliyeWS/EIrsaliye"
    assert wire.IZIBIZ_LIVE_HOST_ALLOWLIST == ()
    metin = (BACKEND / "app" / "einvoice" / "endpoints.py").read_text(encoding="utf-8")
    assert "irsaliye.izibiz.com.tr" not in metin
    # Canlı uçta ortam kilidi e-İrsaliye için de devrede.
    assert wire.izibiz_endpoint_violation(
        "https://efatura.izibiz.com.tr/EIrsaliyeWS/EIrsaliye", "test"
    ) is not None


# ==========================================================================
# 2. DURUM MAKİNESİ — saf, veritabanı yok
# ==========================================================================

def test_101_QUEUED_ve_bilinmeyen_kod_UNKNOWN() -> None:
    from app.einvoice import edespatch

    assert edespatch.kodu_coz("101") == edespatch.QUEUED
    assert edespatch.kodu_coz("999") == edespatch.UNKNOWN
    assert edespatch.kodu_coz("") == edespatch.UNKNOWN
    assert edespatch.kodu_coz(None) == edespatch.UNKNOWN
    # Sağlayıcının BÜTÜN bilinen kodları (keşif §5) tek tek.
    assert edespatch.kodu_coz("102") == edespatch.PROCESSING
    assert edespatch.kodu_coz("103") == edespatch.PROCESSING
    assert edespatch.kodu_coz("104") == edespatch.PROCESSING
    assert edespatch.kodu_coz("105") == edespatch.FAILED
    assert edespatch.kodu_coz("107") == edespatch.SIGNED
    assert edespatch.kodu_coz("133") == edespatch.DELIVERED
    assert edespatch.kodu_coz("134") == edespatch.UNKNOWN
    assert edespatch.kodu_coz("135") == edespatch.PROCESSING
    assert edespatch.kodu_coz("136") == edespatch.FAILED
    assert edespatch.kodu_coz("137") == edespatch.SENT


def test_100_e_arsivin_anlamini_ALMIYOR() -> None:
    """Keşif §1'in P0'ı: e-Arşiv eşlemesi TAŞINMAMALI.

    `endpoints.IZIBIZ_STATUS_ALIASES` içinde `"100"` -> `PENDING` ve orada
    DOĞRUDUR (e-Arşiv'de 100 "KUYRUĞA EKLENDİ"). e-İrsaliye'de AYNI SAYI
    "durum güncellenmedi" demek.

    MUTASYON: `edespatch.SAGLAYICI_KODLARI["100"]`ı `QUEUED` yapmak bunu
    KIRMIZI yapar — ve davranışta hiçbir şey olmamış bir belgeyi "kuyruğa
    alındı" diye gösterirdi.
    """
    from app.einvoice import edespatch
    from app.einvoice.endpoints import IZIBIZ_STATUS_ALIASES

    assert edespatch.kodu_coz("100") == edespatch.UNKNOWN
    # e-Arşiv tablosu KIMILDAMADI — bu dilim ona DOKUNMADI.
    assert IZIBIZ_STATUS_ALIASES["100"] == "PENDING"
    # İki tablo AYRI nesneler; biri ötekinin takma adı DEĞİL.
    assert edespatch.SAGLAYICI_KODLARI is not IZIBIZ_STATUS_ALIASES


def test_106_CELISKILI_oldugu_icin_ESLENMIYOR() -> None:
    """Keşif §5: 106 tabloda `SIGN_PROCESSING` VE `SIGN_FAILED` olarak geçiyor.

    Çelişkili bir kodun doğru eşlemesi YOKTUR. Birini seçmek, imzalanmış
    bir belgeyi "imza başarısız" (ya da tersini) saymak olurdu.
    """
    from app.einvoice import edespatch

    assert "106" not in edespatch.SAGLAYICI_KODLARI
    assert edespatch.kodu_coz("106") == edespatch.UNKNOWN


def test_TERMINAL_yalniz_DELIVERED() -> None:
    """Tek terminal. `REJECTED` bu kümede YOK ve olmamalı (E4b'nin işi)."""
    from app.einvoice import edespatch

    assert edespatch.TERMINAL == frozenset({edespatch.DELIVERED})
    assert "REJECTED" not in edespatch.BILINEN
    # Terminal gerçekten terminal: hiçbir bildirim onu kımıldatmaz.
    for gelen in (
        edespatch.QUEUED, edespatch.SENT, edespatch.FAILED, edespatch.UNKNOWN,
    ):
        assert edespatch.durumu_ilerlet(edespatch.DELIVERED, gelen) == edespatch.DELIVERED


def test_durum_ILERI_YONLU() -> None:
    """Geç gelen bir cevap ilerlemiş bir belgeyi geri çekemez."""
    from app.einvoice import edespatch

    assert edespatch.durumu_ilerlet(edespatch.NONE, edespatch.QUEUED) == edespatch.QUEUED
    assert edespatch.durumu_ilerlet(edespatch.SENT, edespatch.QUEUED) == edespatch.SENT
    assert edespatch.durumu_ilerlet(edespatch.QUEUED, edespatch.SENT) == edespatch.SENT
    assert edespatch.durumu_ilerlet(edespatch.SIGNED, edespatch.PROCESSING) == edespatch.SIGNED


def test_UNKNOWN_canli_belgeyi_GERI_CEKMIYOR() -> None:
    """`UNKNOWN` GELEN olarak: canlı belge varsa bir GEÇİŞ DEĞİLDİR.

    MUTASYON: `durumu_ilerlet`teki `UNKNOWN` dalını silip rank
    karşılaştırmasına bırakmak bunu KIRMIZI yapar — cevaplanamayan bir
    sorgu, kuyrukta bekleyen bir belgeyi "bilinmiyor"a düşürürdü.
    """
    from app.einvoice import edespatch

    assert edespatch.durumu_ilerlet(edespatch.QUEUED, edespatch.UNKNOWN) == edespatch.QUEUED
    assert edespatch.durumu_ilerlet(edespatch.SENT, edespatch.UNKNOWN) == edespatch.SENT
    # Ama hiçbir şey bilmiyorsak SAKLANIR: gönderim TIMEOUT'u tam o boşluk.
    assert edespatch.durumu_ilerlet(edespatch.NONE, edespatch.UNKNOWN) == edespatch.UNKNOWN
    assert edespatch.durumu_ilerlet(edespatch.FAILED, edespatch.UNKNOWN) == edespatch.UNKNOWN
    # Ve `FAILED` `UNKNOWN`ı EZEMEZ: bir TIMEOUT'tan sonra gelen "gönderim
    # inmedi" iddiası bilgimizi geri alamaz.
    assert edespatch.durumu_ilerlet(edespatch.UNKNOWN, edespatch.FAILED) == edespatch.UNKNOWN


def test_BELGE_YOK_cevabi_UNKNOWNi_FAILEDe_cevirir() -> None:
    """`UNKNOWN`ın TEK çıkış kapısı ve yalnız o durumda çalışır.

    MUTASYON: `bilinmeyeni_yok_say`ı `durumu_ilerlet`in içine gömmek bunu
    KIRMIZI yapar — rastgele bir başarısız sorgu da bilgimizi silerdi.
    """
    from app.einvoice import edespatch

    assert edespatch.bilinmeyeni_yok_say(edespatch.UNKNOWN) == edespatch.FAILED
    for durum in (
        edespatch.NONE, edespatch.QUEUED, edespatch.SENT, edespatch.DELIVERED,
        edespatch.FAILED,
    ):
        assert edespatch.bilinmeyeni_yok_say(durum) == durum


def test_GONDERIM_KAPALI_kumesi() -> None:
    """Gönderim YALNIZ `NONE` ve `FAILED`ten açıktır."""
    from app.einvoice import edespatch

    acik = edespatch.BILINEN - edespatch.GONDERIM_KAPALI
    assert acik == {edespatch.NONE, edespatch.FAILED}
    assert edespatch.UNKNOWN in edespatch.GONDERIM_KAPALI


# ==========================================================================
# 3. UBL — saf dönüşüm, veritabanı yok
# ==========================================================================

def _ornek_payload(**degisiklikler):
    temel = {
        "despatch_number": "IRS-FTR2026000000001",
        "uuid": "11111111-2222-3333-4444-555555555555",
        "issue_date": "2026-09-13",
        "invoice_number": "FTR2026000000001",
        "invoice_issue_date": "2026-09-12",
        "supplier": {
            "vkn": "1234567890", "name": "Sungur Tarim A.S.",
            "address": "Merkez Mah. 1", "tax_office": "Kadikoy",
        },
        "customer": {
            "vkn_tckn": "9876543210", "name": "Alici Ltd.",
            "address": "Sanayi Cad. 5", "tax_office": "Besiktas",
        },
        "shipment": {
            "actual_shipment_at": datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc),
            "vehicle_plate": PLAKA,
            "trailer_plate": DORSE,
            "driver_name": "Ahmet Yilmaz",
            "driver_national_id": SOFOR_TCKN,
            "delivery_address": "Depo Yolu 7",
        },
        "lines": [
            {"id": 1, "name": "Bugday", "quantity": Decimal("2.5000")},
            {"id": 2, "name": "Arpa", "quantity": Decimal("10.0000")},
        ],
    }
    temel.update(degisiklikler)
    return temel


def test_UBL_kok_ve_zorunlu_alanlar() -> None:
    """OASIS kökü + TR kılavuzunun istediği profil/UUID/saat/tip (keşif §3.1)."""
    from app.einvoice.edespatch import build_despatch_xml

    kok = _kok(build_despatch_xml(_ornek_payload()))
    assert kok.tag == (
        "{urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2}DespatchAdvice"
    )
    assert kok.findtext(f"{_CBC}UBLVersionID") == "2.1"
    assert kok.findtext(f"{_CBC}CustomizationID") == "TR1.2.1"
    assert kok.findtext(f"{_CBC}ProfileID") == "TEMELIRSALIYE"
    assert kok.findtext(f"{_CBC}DespatchAdviceTypeCode") == "SEVK"
    assert kok.findtext(f"{_CBC}ID") == "IRS-FTR2026000000001"
    assert kok.findtext(f"{_CBC}UUID") == "11111111-2222-3333-4444-555555555555"
    assert kok.findtext(f"{_CBC}IssueDate") == "2026-09-13"
    assert kok.findtext(f"{_CBC}LineCountNumeric") == "2"


def test_UBL_iki_taraf_da_KIMLIKLI() -> None:
    """`DespatchSupplierParty` ve `DeliveryCustomerParty` — OASIS zorunlu."""
    from app.einvoice.edespatch import build_despatch_xml

    kok = _kok(build_despatch_xml(_ornek_payload()))
    gonderen = kok.find(f"{_CAC}DespatchSupplierParty")
    alici = kok.find(f"{_CAC}DeliveryCustomerParty")
    assert gonderen is not None and alici is not None
    for taraf, numara in ((gonderen, "1234567890"), (alici, "9876543210")):
        kimlik = taraf.find(f"{_CAC}Party/{_CAC}PartyIdentification/{_CBC}ID")
        assert kimlik is not None and kimlik.text == numara
        assert kimlik.get("schemeID") == "VKN"


def test_UBL_TCKNli_alici_GERCEK_KISI_olur() -> None:
    """11 hane ⇒ TCKN ⇒ `Person`; 10 hane ⇒ VKN ⇒ `PartyName`.

    MUTASYON: `_kimlik_semasi`yi "bilinmiyorsa VKN" yapmak bunu KIRMIZI
    yapar — gerçek kişiyi tüzel kişi olarak beyan etmek olurdu.
    """
    from app.einvoice.edespatch import build_despatch_xml
    from app.einvoice.errors import UblBuildError

    p = _ornek_payload()
    p["customer"] = {**p["customer"], "vkn_tckn": SOFOR_TCKN, "name": "Ayse Kaya"}
    kok = _kok(build_despatch_xml(p))
    alici = kok.find(f"{_CAC}DeliveryCustomerParty/{_CAC}Party")
    assert alici.find(f"{_CAC}PartyIdentification/{_CBC}ID").get("schemeID") == "TCKN"
    assert alici.findtext(f"{_CAC}Person/{_CBC}FirstName") == "Ayse"
    assert alici.findtext(f"{_CAC}Person/{_CBC}FamilyName") == "Kaya"

    p["customer"] = {**p["customer"], "vkn_tckn": "123"}
    with pytest.raises(UblBuildError):
        build_despatch_xml(p)


def test_UBL_Shipment_sofor_ve_araci_TASIYOR() -> None:
    """Keşif §3.2'nin plaka/şoför/dorse yolları — birebir."""
    from app.einvoice.edespatch import build_despatch_xml

    kok = _kok(build_despatch_xml(_ornek_payload()))
    sevk = kok.find(f"{_CAC}Shipment")
    assert sevk is not None
    # Shipment varsa ID XSD'de ZORUNLU.
    assert sevk.findtext(f"{_CBC}ID") == "11111111-2222-3333-4444-555555555555"

    plaka = sevk.find(
        f"{_CAC}ShipmentStage/{_CAC}TransportMeans/{_CAC}RoadTransport/{_CBC}LicensePlateID"
    )
    assert plaka is not None and plaka.text == PLAKA
    assert plaka.get("schemeID") == "PLAKA"

    sofor = sevk.find(f"{_CAC}ShipmentStage/{_CAC}DriverPerson")
    assert sofor.findtext(f"{_CBC}FirstName") == "Ahmet"
    assert sofor.findtext(f"{_CBC}FamilyName") == "Yilmaz"
    # Keşif §3.2: TCKN ÖRNEKTE bu alanda — adı yanıltıcı ama ölçülen yer bu.
    assert sofor.findtext(f"{_CBC}NationalityID") == SOFOR_TCKN

    dorse = sevk.find(f"{_CAC}TransportHandlingUnit/{_CAC}TransportEquipment/{_CBC}ID")
    assert dorse is not None and dorse.text == DORSE
    assert dorse.get("schemeID") == "DORSEPLAKA"


def test_UBL_FIILI_SEVK_duzenlemeden_AYRI() -> None:
    """`ActualDespatchDate/Time`, `IssueDate`ten AYRI alanlardır (keşif §3.2).

    MUTASYON: fiili sevk anını `issue_date`ten türetmek bunu KIRMIZI
    yapar — geç düzenlenen bir irsaliye sevki de geç gösterirdi.
    """
    from app.einvoice.edespatch import build_despatch_xml

    kok = _kok(build_despatch_xml(_ornek_payload(issue_date="2026-09-15")))
    teslim = kok.find(f"{_CAC}Shipment/{_CAC}Delivery/{_CAC}Despatch")
    assert kok.findtext(f"{_CBC}IssueDate") == "2026-09-15"
    assert teslim.findtext(f"{_CBC}ActualDespatchDate") == "2026-09-13"
    # 08:30 UTC = 11:30 TÜRKİYE YEREL SAATİ. UBL-TR `ActualDespatchTime`
    # bir YEREL saattir: denetçi malın Türkiye saatiyle kaçta yola
    # çıktığını görür. Çevirim `edespatch.BELGE_UTC_OFFSET` ile SABİTTİR
    # ve sunucunun saat dilimine BAĞLI DEĞİLDİR — o bağımlılık PG ikizinde
    # ÖLÇÜLEN gerçek bir kusurdu (aynı satır SQLite'ta 08:30, PG'de 11:30
    # üretiyordu). Bu satır o çevirimin BURADA da uygulandığını çiviliyor.
    assert teslim.findtext(f"{_CBC}ActualDespatchTime") == "11:30:00"
    # DÜZENLEME SAATİ de aynı andan gelir, yani AYNI yerel saat.
    assert kok.findtext(f"{_CBC}IssueTime") == "11:30:00"


def test_SAAT_cevirimi_SABIT_ve_girdi_OFFSETINDEN_BAGIMSIZ() -> None:
    """AYNI AN, hangi offset'le verilirse verilsin AYNI saati beyan eder.

    ÖLÇÜLEN KUSUR (PG ikizi yakaladı): `_an_coz` gelen `datetime`ı olduğu
    gibi bırakıp `strftime` uyguluyordu. PostgreSQL aynı anı OTURUM SAAT
    DİLİMİNDE döndürdüğü için (`SHOW TimeZone` -> `Europe/Istanbul`)
    `08:30+00:00` yazılan satır `11:30+03:00` olarak geri geliyor ve
    belge SQLite'ta `08:30:00`, PG'de `11:30:00` diyordu — yani belgenin
    İÇERİĞİ veritabanı ayarına bağlıydı.

    MUTASYON: `_an_coz`un son satırındaki `astimezone(BELGE_UTC_OFFSET)`i
    kaldırmak bunu KIRMIZI yapar.
    """
    from datetime import timedelta

    from app.einvoice.edespatch import BELGE_UTC_OFFSET, build_despatch_xml

    an_utc = datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc)
    esdeger = [
        an_utc,                                             # +00:00
        an_utc.astimezone(timezone(timedelta(hours=3))),    # +03:00
        an_utc.astimezone(timezone(timedelta(hours=-5))),   # -05:00
        datetime(2026, 9, 13, 8, 30),                       # naive (SQLite)
    ]
    saatler = set()
    for an in esdeger:
        p = _ornek_payload()
        p["shipment"] = {**p["shipment"], "actual_shipment_at": an}
        kok = _kok(build_despatch_xml(p))
        teslim = kok.find(f"{_CAC}Shipment/{_CAC}Delivery/{_CAC}Despatch")
        saatler.add(
            (
                teslim.findtext(f"{_CBC}ActualDespatchDate"),
                teslim.findtext(f"{_CBC}ActualDespatchTime"),
            )
        )
    assert saatler == {("2026-09-13", "11:30:00")}, saatler
    assert BELGE_UTC_OFFSET.utcoffset(None) == timedelta(hours=3)


def test_UBL_TASIYICI_dali_plakasiz_da_calisir() -> None:
    """Keşif §3.2: plaka+şoför VEYA kargo firması. İkisi birden zorunlu DEĞİL."""
    from app.einvoice.edespatch import build_despatch_xml

    p = _ornek_payload()
    p["shipment"] = {
        "actual_shipment_at": p["shipment"]["actual_shipment_at"],
        "delivery_address": "Depo Yolu 7",
        "carrier_name": "Hizli Kargo A.S.",
        "carrier_tax_number": "5555555550",
    }
    kok = _kok(build_despatch_xml(p))
    tasiyici = kok.find(f"{_CAC}Shipment/{_CAC}Delivery/{_CAC}CarrierParty")
    assert tasiyici.findtext(f"{_CAC}PartyName/{_CBC}Name") == "Hizli Kargo A.S."
    # Şoför/araç dalı HİÇ yazılmadı — boş bir `ShipmentStage` üretilmedi.
    assert kok.find(f"{_CAC}Shipment/{_CAC}ShipmentStage") is None


def test_UBL_her_fatura_satiri_icin_BIR_DespatchLine() -> None:
    """DÜZ SEVK: satır sayısı ve miktarlar faturanınkiyle BİREBİR."""
    from app.einvoice.edespatch import build_despatch_xml

    kok = _kok(build_despatch_xml(_ornek_payload()))
    satirlar = kok.findall(f"{_CAC}DespatchLine")
    assert len(satirlar) == 2
    assert [s.findtext(f"{_CBC}DeliveredQuantity") for s in satirlar] == ["2.5000", "10.0000"]
    for satir in satirlar:
        assert satir.find(f"{_CBC}DeliveredQuantity").get("unitCode") == "C62"
        # OASIS `OrderLineReference` 1+ — uydurma sipariş numarası YOK,
        # düz sevkte satır sırası referansın kendisidir.
        assert satir.findtext(f"{_CAC}OrderLineReference/{_CBC}LineID") is not None
    assert [s.findtext(f"{_CAC}Item/{_CBC}Name") for s in satirlar] == ["Bugday", "Arpa"]


def test_UBL_MIKTAR_float_REDDEDILIYOR() -> None:
    """`float` bir miktar 2.9999999 olabilir; iki belgenin eşitliği bozulurdu."""
    from app.einvoice.edespatch import build_despatch_xml
    from app.einvoice.errors import UblBuildError

    p = _ornek_payload()
    p["lines"] = [{"id": 1, "name": "Bugday", "quantity": 2.5}]
    with pytest.raises(UblBuildError):
        build_despatch_xml(p)


def test_UBL_EKSIK_alanda_yarim_belge_URETMIYOR() -> None:
    from app.einvoice.edespatch import build_despatch_xml
    from app.einvoice.errors import UblBuildError

    for eksik in ("despatch_number", "uuid"):
        p = _ornek_payload()
        p[eksik] = ""
        with pytest.raises(UblBuildError):
            build_despatch_xml(p)
    p = _ornek_payload()
    p["lines"] = []
    with pytest.raises(UblBuildError):
        build_despatch_xml(p)


def test_PAKET_DETERMINISTIK() -> None:
    """Aynı irsaliye her seferinde BAYT BAYT aynı ZIP'i üretir.

    Sabit `despatch_uuid` ile birlikte çift belgeye karşı ikinci savunma:
    idempotent bir yeniden gönderim sağlayıcıya FARKLI bir belge gibi
    görünmez.
    """
    from app.einvoice.edespatch import package_despatch

    assert package_despatch(_ornek_payload()) == package_despatch(_ornek_payload())


# ==========================================================================
# 4. SAĞLAYICI — sahte taşıma, soket yok
# ==========================================================================

class _SahteTasima:
    """Sıradaki yanıtı veren, isteği KAYDEDEN taşıma ikizi."""

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


def _login_yaniti():
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


def test_saglayici_GONDERIM_101_QUEUED() -> None:
    """Başarı yolu: `DESPATCH_ID` yakalanır, `101` `QUEUED`a çözülür."""
    from app.einvoice import edespatch

    saglayici, tasima = _saglayici([
        _login_yaniti(),
        _yanit(
            '<s:Envelope xmlns:s="x"><s:Body><SendDespatchAdviceResponse>'
            "<DESPATCH_ID>SAGLAYICI-42</DESPATCH_ID><STATUS_CODE>101</STATUS_CODE>"
            "</SendDespatchAdviceResponse></s:Body></s:Envelope>"
        ),
    ])
    sonuc = saglayici.submit_despatch(_ornek_payload())
    assert sonuc.status == edespatch.QUEUED
    assert sonuc.external_id == "SAGLAYICI-42"
    assert sonuc.gib_status_code == "101"
    assert sonuc.uuid == "11111111-2222-3333-4444-555555555555"
    # DOĞRU SERVİSE gitti: e-Fatura ya da e-Arşiv yoluna DEĞİL.
    assert tasima.istekler[-1]["url"].endswith("/EIrsaliyeWS/EIrsaliye")
    # ETTN gövdede ÖZNİTELİK olarak taşınıyor (şemadan).
    assert b'UUID="11111111-2222-3333-4444-555555555555"' in tasima.istekler[-1]["body"]


def test_TIMEOUT_UNKNOWN_uretir() -> None:
    """Zaman aşımı `FAILED` DEĞİL `UNKNOWN`dır.

    MUTASYON: `submit_despatch`in `TransportError` dalını `FAILED`e
    çevirmek bunu KIRMIZI yapar — ve davranışta çift irsaliye kapısını
    açardı (uç testi `test_ETTN_denemeler_boyunca_SABIT` tam bunu ölçüyor).
    """
    from app.einvoice import edespatch
    from app.einvoice.transport import TransportError

    saglayici, _ = _saglayici([_login_yaniti(), TransportError("read timeout")])
    sonuc = saglayici.submit_despatch(_ornek_payload())
    assert sonuc.status == edespatch.UNKNOWN
    assert sonuc.uuid == "11111111-2222-3333-4444-555555555555"


def test_GONDERIM_TEKRARLANMIYOR() -> None:
    """`submit_despatch` idempotent DEĞİL: oturum düşse bile TEKRARLANMAZ."""
    from app.einvoice.provider import IzibizEInvoiceProvider

    assert "submit_despatch" not in IzibizEInvoiceProvider._RELOGIN_REPEATABLE
    assert "despatch_status" in IzibizEInvoiceProvider._RELOGIN_REPEATABLE
    assert "fetch_despatch_xml" in IzibizEInvoiceProvider._RELOGIN_REPEATABLE


def test_saglayici_DURUM_sorgusu_ETTN_ister() -> None:
    """Şema YALNIZ `UUID` kabul eder (keşif §2.2); boş ETTN ağa ÇIKMAZ."""
    from app.einvoice import edespatch

    saglayici, tasima = _saglayici([])
    sonuc = saglayici.despatch_status("")
    assert sonuc.status == edespatch.UNKNOWN
    assert tasima.istekler == [], "boş ETTN ile ağa çıkıldı"


def test_saglayici_DURUM_137_SENT() -> None:
    from app.einvoice import edespatch

    saglayici, _ = _saglayici([
        _login_yaniti(),
        _yanit(
            '<s:Envelope xmlns:s="x"><s:Body><GetDespatchAdviceStatusResponse>'
            "<DESPATCHADVICE_STATUS><STATUS_CODE>137</STATUS_CODE></DESPATCHADVICE_STATUS>"
            "</GetDespatchAdviceStatusResponse></s:Body></s:Envelope>"
        ),
    ])
    sonuc = saglayici.despatch_status("11111111-2222-3333-4444-555555555555")
    assert sonuc.status == edespatch.SENT
    assert sonuc.gib_status_code == "137"


def test_saglayici_10008_BELGE_YOK_bayragi() -> None:
    """`ERROR_CODE=10008` = "kayıt bulunamadı" ⇒ `raw["belge_yok"]`.

    Bu bayrak `UNKNOWN`dan çıkışın kanıtıdır ve KARARI adaptör vermez:
    yerel durumu değiştirmek uç katmanının işi.
    """
    from app.einvoice import edespatch

    saglayici, _ = _saglayici([
        _login_yaniti(),
        _yanit(
            '<s:Envelope xmlns:s="x"><s:Body><GetDespatchAdviceStatusResponse>'
            "<ERROR_TYPE><INTL_TXN_ID>1</INTL_TXN_ID><ERROR_CODE>10008</ERROR_CODE>"
            "<ERROR_SHORT_DES>Kayit bulunamadi</ERROR_SHORT_DES></ERROR_TYPE>"
            "</GetDespatchAdviceStatusResponse></s:Body></s:Envelope>"
        ),
    ])
    sonuc = saglayici.despatch_status("11111111-2222-3333-4444-555555555555")
    assert sonuc.status == edespatch.UNKNOWN
    assert (sonuc.raw or {}).get("belge_yok") is True


def test_saglayici_XML_suretini_cozuyor() -> None:
    """`CONTENT` base64'tür; ham XML, base64 XML ve base64-ZIP — üçü de."""
    import base64

    from app.einvoice.provider import _decode_despatch_xml
    from app.einvoice.ubl_xml import zip_single

    xml = b"<DespatchAdvice/>"
    assert _decode_despatch_xml(xml) == xml
    assert _decode_despatch_xml(base64.b64encode(xml)) == xml
    assert _decode_despatch_xml(base64.b64encode(zip_single("a.xml", xml))) == xml
    # XML DEĞİLSE sessizce sunulmuyor.
    assert _decode_despatch_xml(b"%PDF-1.7 bu XML degil") == b""


def test_ZIP_BOMBA_tavanlari_XML_yolunda_da_gecerli() -> None:
    """PDF ve XML yolları AYNI sınır denetimlerini paylaşır.

    MUTASYON: `_xml_from_zip`i `_zip_uyeleri` yerine çıplak `zipfile` ile
    yazmak bunu KIRMIZI yapar — bomba, kopyalanmamış kapıdan girerdi.
    """
    import io
    import zipfile

    from app.einvoice import provider as saglayici_modulu

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as arsiv:
        for i in range(saglayici_modulu._ZIP_MAX_UYE + 1):
            arsiv.writestr(f"{i}.xml", b"<a/>")
    assert saglayici_modulu._xml_from_zip(tampon.getvalue()) == b""
    assert saglayici_modulu._pdf_from_zip(tampon.getvalue()) == b""


def test_NOOP_saglayici_e_irsaliyeyi_REDDEDIYOR() -> None:
    """Fail-closed: e-İrsaliye bilmeyen bir sağlayıcı sessizce başarı DEMEZ."""
    from app.einvoice import edespatch
    from app.einvoice.errors import EInvoiceError
    from app.einvoice.provider import NoOpEInvoiceProvider

    noop = NoOpEInvoiceProvider()
    assert noop.supports_despatch is False
    assert noop.submit_despatch({}).status == edespatch.FAILED
    assert noop.despatch_status("x").status == edespatch.UNKNOWN
    with pytest.raises(EInvoiceError):
        noop.fetch_despatch_xml("x")


# ==========================================================================
# 5. UÇLAR — GERÇEK uygulama, GERÇEK göç, GERÇEK HTTP
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


@pytest.fixture(scope="module")
def tohum(istemci, admin_basliklari):
    """GERÇEK bir fatura — iş emrinden üretilmiş, İKİ kalemli.

    `test_sec3_read_daraltma.py`nin tohumlama zincirinin AYNISI: müşteri →
    makine → iş emri → parçalar → COMPLETED → `POST /api/invoices/generate`.
    Fatura kalemleri iş emrinin PARÇALARINDAN doğuyor, elle yazılmıyor.

    İKİ KALEM ZORUNLU: "her fatura satırı için bir DespatchLine" iddiası
    tek kalemde bir SAYMA hatasını yakalayamazdı.
    """
    h = admin_basliklari

    def olustur(yol, govde):
        yanit = istemci.post(yol, headers=h, json=govde)
        assert yanit.status_code in (200, 201), (yol, yanit.status_code, yanit.text)
        return yanit.json()

    # VKN'LER ZORUNLU ve bu tohumlamanin bir susu DEGIL: UBL hem
    # `DespatchSupplierParty` hem `DeliveryCustomerParty` icin 10/11 haneli
    # bir kimlik ISTER ve `_kimlik_semasi` eksik kimlikte belge URETMEZ
    # (OLCULDU: vergi numarasiz bir firmada indirme 409 "Taraf VKN/TCKN 10
    # veya 11 haneli olmali: 0 hane" veriyor). Faturanin DONMUS goruntusu
    # bu degerleri gonderim aninda saklar, o yuzden fatura URETILMEDEN
    # ONCE yaziliyorlar.
    musteri = olustur(
        "/api/customers", {"name": "E4a Musteri", "tax_number": "9876543210"}
    )
    tedarikci = olustur("/api/suppliers", {"name": "E4a Tedarikci"})
    # YENI BIR DEPO ACILMIYOR, ACILISIN VARSAYILAN DEPOSU kullaniliyor —
    # ve bu OLCULMUS bir karar: `POST /api/purchases` stogu belge
    # BASLIGININ `warehouse_id`sine yaziyor, kalem satirindakine DEGIL
    # (olculdu: yeni bir depo acip kalemde onu verince stok VARSAYILAN
    # depoya dustu ve is emri parcasi "yeterli stok yok" ile 409 verdi).
    # Tohumlamanin konusu depo secimi degil; ayni depoyu her iki adimda da
    # kullanmak iddiayi degistirmiyor.
    depolar = istemci.get("/api/warehouses", headers=h)
    assert depolar.status_code == 200, depolar.text
    _liste = depolar.json()
    depo = (_liste["items"] if isinstance(_liste, dict) else _liste)[0]
    urun_a = olustur("/api/products", {"name": "E4a Bugday", "sku": "E4A-1"})
    urun_b = olustur("/api/products", {"name": "E4a Arpa", "sku": "E4A-2"})
    # STOK ONCE GIRER. Is emri parcasi rezervasyon yapiyor ve stoksuz depoda
    # 409 veriyor ("Depoda yeterli kullanilabilir stok yok") — OLCULDU.
    # Tohumlamayi bu ALIS belgesi ayakta tutuyor; olmasaydi fatura HIC
    # dogmaz ve butun uc testleri sessizce ERROR olurdu.
    olustur(
        "/api/purchases",
        {
            "entity_id": tedarikci["id"], "transaction_date": "2026-09-09",
            "items": [
                {
                    "product_id": urun["id"], "quantity": "50", "unit_price": "80",
                    "discount": "0", "vat_rate": "0", "warehouse_id": depo["id"],
                }
                for urun in (urun_a, urun_b)
            ],
        },
    )
    makine = olustur(
        "/api/machines", {"brand": "E4a", "model": "M1", "customer_id": musteri["id"]}
    )
    # Firmanin VKN'si — acilis firmasi vergi numarasiz doguyor (olculdu).
    from sqlalchemy import text as _text

    from app.db import SessionLocal

    with SessionLocal() as db:
        db.execute(
            _text("UPDATE companies SET tax_number=:v WHERE id=:c"),
            {"v": "1234567890", "c": int(h["X-Company-ID"])},
        )
        db.commit()
    is_emri = olustur(
        "/api/work-orders",
        {
            "customer_id": musteri["id"], "machine_id": makine["id"],
            "technician_id": 1, "title": "E4a Is Emri", "description": "E4a",
            "priority": "NORMAL",
        },
    )
    for sira, urun in enumerate((urun_a, urun_b), start=1):
        parca = istemci.post(
            f"/api/work-orders/{is_emri['id']}/parts",
            headers=h,
            json={
                "product_id": urun["id"], "quantity": str(sira),
                "warehouse_id": depo["id"], "unit_price": "100",
            },
        )
        assert parca.status_code in (200, 201), parca.text
    for durum in ("IN_PROGRESS", "COMPLETED"):
        gecis = istemci.patch(
            f"/api/work-orders/{is_emri['id']}/status", headers=h, json={"status": durum}
        )
        assert gecis.status_code == 200, (durum, gecis.text)
    fatura = olustur("/api/invoices/generate", {"work_order_id": is_emri["id"]})
    return {
        "company_id": int(h["X-Company-ID"]),
        "invoice_id": fatura["id"],
        "customer_id": musteri["id"],
    }


def _irsaliye_govdesi(fatura_id: int, **degisiklikler) -> dict:
    govde = {
        "invoice_id": fatura_id,
        "actual_shipment_at": "2026-09-13T08:30:00+00:00",
        "driver_name": "Ahmet Yilmaz",
        "driver_national_id": SOFOR_TCKN,
        "vehicle_plate": PLAKA,
        "trailer_plate": DORSE,
        "delivery_address": "Depo Yolu 7, Kadikoy",
    }
    govde.update(degisiklikler)
    return govde


@pytest.fixture(scope="module")
def irsaliye(istemci, admin_basliklari, tohum):
    yanit = istemci.post(
        "/api/despatch-notes",
        headers=admin_basliklari,
        json=_irsaliye_govdesi(tohum["invoice_id"]),
    )
    assert yanit.status_code == 201, yanit.text
    return yanit.json()


def test_OLUSTURMA_ETTN_uretir_ve_durum_NONE(irsaliye) -> None:
    """Satır DOĞARKEN ETTN alır — gönderimden ÖNCE.

    MUTASYON: `despatch_uuid`i gönderim anında üretmeye taşımak bunu
    KIRMIZI yapar ve TIMEOUT penceresini açardı: kayıt yazılamadan dönen
    bir gönderimin ETTN'i kalmazdı.
    """
    from app.einvoice import edespatch

    assert len(irsaliye["despatch_uuid"]) == 36
    assert irsaliye["despatch_uuid"].count("-") == 4
    assert irsaliye["edespatch_status"] == edespatch.NONE
    assert irsaliye["edespatch_provider_uuid"] is None
    assert irsaliye["driver_national_id"] == SOFOR_TCKN
    assert irsaliye["vehicle_plate"] == PLAKA
    assert irsaliye["despatch_number"].startswith("IRS-")


def test_IKINCI_irsaliye_409(istemci, admin_basliklari, tohum, irsaliye) -> None:
    """E4a: bir fatura, bir irsaliye. HAKEM VERİTABANIDIR.

    MUTASYON: göçten `UNIQUE(company_id, invoice_id)` kısıtını düşürmek
    bunu KIRMIZI yapar — ve iki eşzamanlı POST aynı faturaya İKİ irsaliye
    açardı.
    """
    ikinci = istemci.post(
        "/api/despatch-notes",
        headers=admin_basliklari,
        json=_irsaliye_govdesi(tohum["invoice_id"]),
    )
    assert ikinci.status_code == 409, ikinci.text
    assert "zaten var" in ikinci.json()["detail"]


def test_CAPRAZ_KIRACI_faturasi_404(istemci, admin_basliklari, tohum) -> None:
    """Başka firmanın faturası 404 — 403 DEĞİL (varlık bilgisi sızmaz).

    MUTASYON: `_fatura`dan `company_id=:cid` yüklemini düşürmek bunu
    KIRMIZI yapar ve bir firmanın irsaliyesini BAŞKA firmanın faturasına
    asardı.
    """
    from sqlalchemy import text as _text

    from app.db import SessionLocal

    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        yabanci_firma = db.execute(
            _text(
                "INSERT INTO companies(name,is_active,created_at)"
                " VALUES(:a,1,:t) RETURNING id"
            ),
            {"a": "E4a Yabanci Firma", "t": an},
        ).scalar_one()
        yabanci_fatura = db.execute(
            _text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,"
                "currency,exchange_rate,customer_snapshot,machine_snapshot,"
                "work_order_snapshot,company_snapshot,technician_snapshot,"
                "warranty_snapshot,totals_snapshot,tax_snapshot,created_by,"
                "created_at,updated_at) "
                "VALUES(:c,'YABANCI-1','INVOICE','ISSUED','TRY',1,'{}','{}','{}','{}',"
                "'{}','{}','{}','{}',1,:t,:t) RETURNING id"
            ),
            {"c": yabanci_firma, "t": an},
        ).scalar_one()
        db.commit()

    yanit = istemci.post(
        "/api/despatch-notes",
        headers=admin_basliklari,
        json=_irsaliye_govdesi(int(yabanci_fatura)),
    )
    assert yanit.status_code == 404, yanit.text


def test_IPTAL_EDILMIS_faturaya_irsaliye_YOK(istemci, admin_basliklari, tohum) -> None:
    """İptal edilmiş bir faturanın malını sevk etmek beyanla çelişir."""
    from sqlalchemy import text as _text

    from app.db import SessionLocal

    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        iptal_id = db.execute(
            _text(
                "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,"
                "currency,exchange_rate,customer_snapshot,machine_snapshot,"
                "work_order_snapshot,company_snapshot,technician_snapshot,"
                "warranty_snapshot,totals_snapshot,tax_snapshot,created_by,"
                "created_at,updated_at,cancelled_at) "
                "VALUES(:c,'E4A-IPTAL-1','INVOICE','CANCELLED','TRY',1,'{}','{}','{}',"
                "'{}','{}','{}','{}','{}',1,:t,:t,:t) RETURNING id"
            ),
            {"c": tohum["company_id"], "t": an},
        ).scalar_one()
        db.commit()
    yanit = istemci.post(
        "/api/despatch-notes",
        headers=admin_basliklari,
        json=_irsaliye_govdesi(int(iptal_id)),
    )
    assert yanit.status_code == 409, yanit.text
    assert "ptal" in yanit.json()["detail"]


def test_LISTE_ve_DETAY(istemci, admin_basliklari, tohum, irsaliye) -> None:
    liste = istemci.get(
        f"/api/despatch-notes?invoice_id={tohum['invoice_id']}", headers=admin_basliklari
    )
    assert liste.status_code == 200, liste.text
    govde = liste.json()
    assert govde["total"] == 1
    assert govde["items"][0]["id"] == irsaliye["id"]

    detay = istemci.get(f"/api/despatch-notes/{irsaliye['id']}", headers=admin_basliklari)
    assert detay.status_code == 200
    assert detay.json()["despatch_uuid"] == irsaliye["despatch_uuid"]

    assert istemci.get(
        "/api/despatch-notes/999999", headers=admin_basliklari
    ).status_code == 404


def test_PDF_501_fail_closed(istemci, admin_basliklari, irsaliye) -> None:
    """Keşif §2.4/§5: PDF sözleşmesi DOĞRULANMADI ⇒ tahmin YOK, 501.

    MUTASYON: bu dalı bir sağlayıcı çağrısına (`CONTENT_TYPE=PDF`)
    çevirmek bunu KIRMIZI yapar.
    """
    yanit = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/download?format=pdf",
        headers=admin_basliklari,
    )
    assert yanit.status_code == 501, yanit.text
    assert yanit.json()["detail"] == "e-İrsaliye PDF sağlayıcı tarafında doğrulanmadı"


def test_XML_indirmesi_belgeyi_YENIDEN_URETIYOR(
    istemci, admin_basliklari, irsaliye
) -> None:
    """`format=xml` AĞA ÇIKMAZ; saklanan satırdan üretilir."""
    yanit = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/download",
        headers=admin_basliklari,
    )
    assert yanit.status_code == 200, yanit.text
    assert yanit.headers["content-type"].startswith("application/xml")
    kok = _kok(yanit.content)
    assert kok.findtext(f"{_CBC}UUID") == irsaliye["despatch_uuid"]
    assert kok.findtext(f"{_CBC}ProfileID") == "TEMELIRSALIYE"
    # Şoför ve araç GERÇEKTEN belgede.
    sevk = kok.find(f"{_CAC}Shipment")
    assert sevk.findtext(
        f"{_CAC}ShipmentStage/{_CAC}DriverPerson/{_CBC}NationalityID"
    ) == SOFOR_TCKN
    assert sevk.findtext(
        f"{_CAC}ShipmentStage/{_CAC}TransportMeans/{_CAC}RoadTransport/"
        f"{_CBC}LicensePlateID"
    ) == PLAKA


def test_UBL_miktarlar_fatura_ile_ESIT(
    istemci, admin_basliklari, tohum, irsaliye
) -> None:
    """DÜZ SEVKİN TEK İDDİASI: her fatura satırı için bir DespatchLine, EŞİT miktar.

    MUTASYON: `_ubl_payload`daki miktarı `float`a çevirmek ya da satırları
    süzmek bunu KIRMIZI yapar.
    """
    fatura = istemci.get(
        f"/api/invoices/{tohum['invoice_id']}", headers=admin_basliklari
    ).json()
    beklenen = [str(k["quantity"]) for k in fatura["items"]]
    assert len(beklenen) >= 2, "tohumlama iki kalem üretmedi; iddia zayıflar"

    xml = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/download",
        headers=admin_basliklari,
    ).content
    kok = _kok(xml)
    satirlar = kok.findall(f"{_CAC}DespatchLine")
    assert len(satirlar) == len(beklenen)
    olculen = [s.findtext(f"{_CBC}DeliveredQuantity") for s in satirlar]
    assert [Decimal(x) for x in olculen] == [Decimal(x) for x in beklenen]
    assert kok.findtext(f"{_CBC}LineCountNumeric") == str(len(beklenen))


def test_GECERSIZ_bicim_400(istemci, admin_basliklari, irsaliye) -> None:
    yanit = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/download?format=docx",
        headers=admin_basliklari,
    )
    assert yanit.status_code == 400


def test_YAPILANDIRMA_YOKSA_gonderim_503(istemci, admin_basliklari, irsaliye) -> None:
    """Sağlayıcı yoksa 503 ve DB'ye HİÇ yazılmaz — sessiz 200 YOK.

    Varsayılan sağlayıcı `noop`, yani bu koşu yapılandırılmamış.
    """
    from app.einvoice import edespatch

    yanit = istemci.post(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/submit", headers=admin_basliklari
    )
    assert yanit.status_code == 503, yanit.text
    sonra = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=admin_basliklari
    ).json()
    assert sonra["edespatch_status"] == edespatch.NONE
    assert sonra["edespatch_last_error"] is None


def test_ETTN_denemeler_boyunca_SABIT(
    istemci, admin_basliklari, irsaliye, monkeypatch
) -> None:
    """TIMEOUT → UNKNOWN → ikinci gönderim KAPALI, ve ETTN KIMILDAMADI.

    E4a'nın en önemli davranışı, uçtan uca:

    1. Gönderim zaman aşımına düşer ⇒ durum ``UNKNOWN`` (``FAILED`` DEĞİL).
    2. İkinci gönderim denemesi **409** alır — ``UNKNOWN``
       ``GONDERIM_KAPALI`` kümesindedir ve SAĞLAYICIYA HİÇ GİDİLMEZ.
    3. ``despatch_uuid`` iki denemede de AYNIDIR.

    MUTASYON: `submit_despatch`in `TransportError` dalını `FAILED`e
    çevirmek ya da `GONDERIM_KAPALI`dan `UNKNOWN`ı çıkarmak bunu KIRMIZI
    yapar.
    """
    from app.einvoice import edespatch
    from app.einvoice.provider import EInvoiceConfiguration, EInvoiceResult
    from app.routers import despatch_notes as uc_modulu

    class _SahteSaglayici:
        cagri = 0

        def submit_despatch(self, payload):
            type(self).cagri += 1
            return EInvoiceResult(
                status=edespatch.UNKNOWN, uuid=payload.get("uuid"), error="zaman asimi"
            )

    monkeypatch.setattr(
        uc_modulu, "einvoice_configuration",
        lambda _s: EInvoiceConfiguration(configured=True, provider="izibiz", reason=""),
    )
    monkeypatch.setattr(
        uc_modulu, "get_einvoice_provider", lambda *a, **k: _SahteSaglayici()
    )

    ilk = istemci.post(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/submit", headers=admin_basliklari
    )
    assert ilk.status_code == 200, ilk.text
    assert ilk.json()["edespatch_status"] == edespatch.UNKNOWN
    assert ilk.json()["despatch_uuid"] == irsaliye["despatch_uuid"]

    ikinci = istemci.post(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/submit", headers=admin_basliklari
    )
    assert ikinci.status_code == 409, ikinci.text
    assert "durum sorgusu" in ikinci.json()["detail"]
    # SAĞLAYICIYA İKİNCİ KEZ GİDİLMEDİ — çift belge kapısı burada kapanıyor.
    assert _SahteSaglayici.cagri == 1
    guncel = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=admin_basliklari
    ).json()
    assert guncel["despatch_uuid"] == irsaliye["despatch_uuid"]


def test_TIMEOUT_sonrasi_BELGE_YOK_gonderimi_ACIYOR(
    istemci, admin_basliklari, irsaliye, monkeypatch
) -> None:
    """`UNKNOWN` bir çıkmaz sokak DEĞİL: sync "belge yok" derse `FAILED`.

    ÖNCEKİ TESTİN DEVAMI ve ona BAĞIMLI (satır `UNKNOWN` durumunda).
    MUTASYON: `bilinmeyeni_yok_say` çağrısını `sync`ten kaldırmak bunu
    KIRMIZI yapar ve zaman aşımına uğramış bir irsaliye SONSUZA DEK
    gönderilemez kalırdı.
    """
    from app.einvoice import edespatch
    from app.einvoice.provider import EInvoiceConfiguration, EInvoiceResult
    from app.routers import despatch_notes as uc_modulu

    class _SahteSaglayici:
        def despatch_status(self, uuid):
            return EInvoiceResult(
                status=edespatch.UNKNOWN, uuid=uuid, error="kayit bulunamadi",
                raw={"belge_yok": True},
            )

    monkeypatch.setattr(
        uc_modulu, "einvoice_configuration",
        lambda _s: EInvoiceConfiguration(configured=True, provider="izibiz", reason=""),
    )
    monkeypatch.setattr(
        uc_modulu, "get_einvoice_provider", lambda *a, **k: _SahteSaglayici()
    )
    onceki = istemci.get(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/status", headers=admin_basliklari
    ).json()
    assert onceki["edespatch_status"] == edespatch.UNKNOWN, (
        "bu test bir önceki testin bıraktığı UNKNOWN durumuna dayanıyor"
    )
    yanit = istemci.post(
        f"/api/despatch-notes/{irsaliye['id']}/edespatch/sync", headers=admin_basliklari
    )
    assert yanit.status_code == 200, yanit.text
    assert yanit.json()["edespatch_status"] == edespatch.FAILED
    assert yanit.json()["despatch_uuid"] == irsaliye["despatch_uuid"]


# ==========================================================================
# 6. ROL MATRİSİ — GERÇEK giriş, GERÇEK 403
# ==========================================================================

#: Yedi ucun ALTISI burada (yedincisi — `POST /api/despatch-notes` — gövde
#: gerektirdiği için AYRI ölçülüyor; gövdesiz bir POST 422 döner ve 422 ile
#: 403'ü karıştırmak kapıyı ölçmemek olurdu).
UCLAR: tuple[tuple[str, str], ...] = (
    ("GET", "/api/despatch-notes"),
    ("GET", "/api/despatch-notes/{id}"),
    ("GET", "/api/despatch-notes/{id}/edespatch/status"),
    ("GET", "/api/despatch-notes/{id}/edespatch/download"),
    ("POST", "/api/despatch-notes/{id}/edespatch/submit"),
    ("POST", "/api/despatch-notes/{id}/edespatch/sync"),
)


@pytest.fixture(scope="module")
def rol_basliklari(istemci, admin_basliklari, tohum):
    """Beş rol için GERÇEK kullanıcı + GERÇEK giriş."""
    from sqlalchemy import text as _text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = tohum["company_id"]
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for rol in ROLLER:
            uid = db.execute(
                _text(
                    "INSERT INTO app_users(username,email,display_name,password_hash,"
                    "role,is_active,must_change_password,email_verified,created_at)"
                    " VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id"
                ),
                {
                    "k": f"e4a_{rol}", "e": f"e4a_{rol}@ornek.test", "d": f"E4a {rol}",
                    "p": hash_password(ROL_PAROLASI), "r": rol, "a": True, "m": False,
                    "v": True, "t": an,
                },
            ).scalar_one()
            db.execute(
                _text(
                    "INSERT INTO user_company_memberships(user_id,company_id,"
                    "is_default,created_at) VALUES(:u,:c,1,:t)"
                ),
                {"u": uid, "c": cid, "t": an},
            )
        db.commit()

    basliklar = {}
    for rol in ROLLER:
        giris = istemci.post(
            "/api/auth/login", json={"username": f"e4a_{rol}", "password": ROL_PAROLASI}
        )
        assert giris.status_code == 200, (rol, giris.text)
        basliklar[rol] = {
            "Authorization": "Bearer " + giris.json()["access_token"],
            "X-Company-ID": str(cid),
        }
    return basliklar


def test_ROL_MATRISI_sales_tasiyan_gecer_otekiler_403(
    istemci, rol_basliklari, irsaliye
) -> None:
    """Beş rol × altı uç. `sales` taşımayan HER hücre 403.

    403 iddiası TOHUMLAMADAN BAĞIMSIZDIR: yetki kapısı middleware'dedir ve
    handler'a HİÇ girilmez, yani kaynak var da olsa yok da olsa sonuç
    403'tür. Geçen hücrelerde `403 DEĞİL` diyoruz — durum kodunun kendisi
    (200/409/503) sağlayıcı yapılandırmasına bağlı ve bu testin konusu o
    değil; ölçtüğümüz şey YETKİ KAPISININ AÇILDIĞIDIR.
    """
    from app.auth import ROLE_PERMISSIONS

    matris: dict[str, dict[str, int]] = {}
    for rol, basliklar in rol_basliklari.items():
        matris[rol] = {}
        for metot, sablon in UCLAR:
            yol = sablon.replace("{id}", str(irsaliye["id"]))
            yanit = (
                istemci.get(yol, headers=basliklar)
                if metot == "GET"
                else istemci.post(yol, headers=basliklar)
            )
            matris[rol][f"{metot} {sablon}"] = yanit.status_code

    for rol, satir in matris.items():
        izinli = "sales" in ROLE_PERMISSIONS[rol]
        for hucre, kod in satir.items():
            if izinli:
                assert kod != 403, (rol, hucre, kod, "sales taşıyan rol 403 aldı")
            else:
                assert kod == 403, (rol, hucre, kod, "sales taşımayan rol geçti")

    # KURULUMUN KENDİSİ doğrulanıyor: boş bir matris sahte biçimde yeşil olurdu.
    assert len(matris) == 5
    assert all(len(satir) == 6 for satir in matris.values())
    # ÖLÇÜLDÜ, VARSAYILMADI. İlk yazımda burada `{"yonetici","satis"}`
    # duruyordu ve ÖLÇÜM onu ÇÜRÜTTÜ: `muhasebe` rolü de `sales` taşıyor
    # (`ROLE_PERMISSIONS`, `app/auth.py`). Beklentiyi değil SATIRI
    # düzelttik — ve sonuç DOĞRUDUR: `muhasebe` faturaya zaten erişiyor
    # (`/api/invoices` SEC-3'ten beri `sales`), irsaliye de aynı ticari
    # belgenin sevk yüzüdür. DÜŞENLER `depo` ve `rapor`dur ve bu kuralın
    # AMACI tam olarak odur: şoför TCKN'si + müşteri VKN'si o iki role
    # kapalı.
    izinliler = {r for r in ROLLER if "sales" in ROLE_PERMISSIONS[r]}
    assert izinliler == {"yonetici", "muhasebe", "satis"}, izinliler
    dusenler = set(ROLLER) - izinliler
    assert dusenler == {"depo", "rapor"}, dusenler


def test_OLUSTURMA_ucu_de_sales_istiyor(istemci, rol_basliklari, tohum) -> None:
    """`POST /api/despatch-notes` — GERÇEK gövdeyle, `depo` yine 403.

    Matris bu ucu atlıyor (gövdesiz POST 422 verir); burada gövde GERÇEK,
    yani 403 gerçekten YETKİ kapısından geliyor, doğrulamadan değil.
    """
    yanit = istemci.post(
        "/api/despatch-notes",
        headers=rol_basliklari["depo"],
        json=_irsaliye_govdesi(tohum["invoice_id"]),
    )
    assert yanit.status_code == 403, yanit.text
