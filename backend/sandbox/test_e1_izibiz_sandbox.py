"""GERÇEK İzibiz sandbox'ına çıkan testler. CI'da KOŞMAZ, elle koşulur.

    cd backend && python -m pytest sandbox/test_e1_izibiz_sandbox.py -q -s

--- BU DOSYA CI'YI NEDEN BOZMUYOR (ÖLÇÜLDÜ, VARSAYILMADI) -----------------

Kanonik koşucu (`run_isolated_tests.py::discover_active_test_files`) TAM
OLARAK iki yere bakıyor ve ikisi de ÖZYİNELEMESİZ:

    BACKEND.glob("test_*.py")          -> backend/test_*.py
    (BACKEND / "tests").glob("test_*.py") -> backend/tests/test_*.py

`backend/sandbox/` İKİSİNİN DE DIŞINDA. Aynı iki glob eksiksizlik kapısında
da kullanılıyor (`tests/test_karantina_listesi_pini.py::
_kesfedilen_test_dosyalari`), yani bu dosya:

* kanonik koşuda TOPLANMIYOR,
* karantina/aktif sayımına GİRMİYOR (hiçbir pin kımıldamıyor),
* `tests/pins/pg_twins.txt`e GİRMİYOR — PG ikizi DEĞİL, ağ ikizi.

Ve ikinci bir emniyet var: kimlik yoksa dosya TOPLANMA anında atlanıyor
(`pytest.skip(allow_module_level=True)`). Yani biri `pytest` komutunu çıplak
koşturup dizine girse bile sonuç YEŞİL bir SKIP'tir, kırmızı bir hata değil.

--- KİMLİK ----------------------------------------------------------------

`backend/.env.izibiz.local` (gitignore'da) ya da ortam değişkenleri
(`IZIBIZ_USER`/`IZIBIZ_PASS`/`IZIBIZ_VKN`), ya da `IZIBIZ_ENV_FILE` ile
gösterilen bir yol. Şablon: `backend/.env.izibiz.local.example`.

SIRLAR EKRANA BASILMAZ: `izibiz_smoke.scrub()` bilinen her sırrı yanıt
gövdelerinden siler ve bu dosya kimlik değerlerini HİÇBİR assert mesajına
koymaz.

--- BU DOSYA NE KANITLIYOR ------------------------------------------------

Mock taşımalı ikiz (`test_e1_efatura_sertlestirme.py`) "kodumuz doğru baytı
üretiyor" diyor. Fixture'lar KAYDEDİLMİŞ yanıtlar, yani "sağlayıcı bunu
KABUL ETTİ" iddiasının kanıtı DEĞİL — kayıt alındığı GÜN kabul ettiğinin
kanıtı. Bu dosya o iddiayı BUGÜN yeniden ölçüyor ve e-Fatura (B2B) kapısını
açacak KANITI da burada üretiyor.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sandbox.izibiz_smoke import SmokeError, load_credentials, mask, scrub  # noqa: E402

from app.einvoice import (  # noqa: E402
    PENDING,
    EInvoiceError,
    IzibizEInvoiceProvider,
    build_einvoice_payload,
)
from app.einvoice import endpoints as wire  # noqa: E402


try:
    KIMLIK = load_credentials()
except SmokeError as hata:
    pytest.skip(
        f"İzibiz sandbox kimliği yok, atlanıyor: {hata}",
        allow_module_level=True,
    )


#: Sandbox'ın KENDİ posta kutusu etiketi. B2B denemesi buraya gider: gerçek
#: bir alıcıya belge kesmemek için alıcı, sağlayıcının kendi test kutusudur.
SANDBOX_PK = "urn:mail:defaultpk@izibiz.com.tr"

#: Her koşu KENDİ fatura numarasını üretir. Aynı numarayı ikinci kez göndermek
#: sağlayıcıda DUPLICATE'e düşer ve testin ölçtüğü şeyi gizlerdi.
#:
#: SADECE RAKAM — ÖLÇÜLDÜ: ilk hâl `uuid4().hex` kullanıyordu ve numaraya
#: HARF karışıyordu (`SNG20263BCA97661`). İzibiz bunu `ERROR_CODE=10003
#: "SCHEMATRON KONTROL SONUCU HATALI"` ile reddetti: UBL-TR fatura numarası
#: 3 HARF + 4 HANE YIL + 9 HANE'dir ve son dokuzu RAKAM olmak ZORUNDADIR.
KOSU = f"{uuid4().int % 100_000_000:08d}"


def _ayarlar() -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username=KIMLIK["IZIBIZ_USER"],
        einvoice_password=KIMLIK["IZIBIZ_PASS"],
        # ÖLÇÜLDÜ: `IZIBIZ_BASE_URL` BİLEREK BOŞTUR (üretim kanalı kapalı,
        # `wire.is_configured("")` False). Sandbox konağı AÇIKÇA verilir ve
        # `IZIBIZ_TEST_HOST_ALLOWLIST` yalnız bunu tanır.
        einvoice_base_url=wire.IZIBIZ_TEST_BASE_URL,
        einvoice_sender_vkn=KIMLIK["IZIBIZ_VKN"],
        einvoice_api_key=None,
        einvoice_endpoints_verified=True,
        izibiz_env="test",
    )


@pytest.fixture(scope="module")
def saglayici() -> IzibizEInvoiceProvider:
    # Taşıma verilmiyor: adaptör KENDİ varsayılan HTTP taşımasını kurar —
    # gerçek ağ yolu tam olarak üretimdeki yoldur.
    return IzibizEInvoiceProvider(_ayarlar())


def _fatura(*, efatura: bool, numara: str, birim: str = "kg", oran: str = "20") -> dict:
    """Tek kalemli gerçek bir belge. `efatura=True` -> TICARIFATURA (B2B)."""
    tutar = Decimal("120.00") if oran == "20" else Decimal("100.00")
    vergi = Decimal("20.00") if oran == "20" else Decimal("0.00")
    return build_einvoice_payload(
        {
            "invoice_number": numara,
            "currency": "TRY",
            "company_id": 1,
            "invoice_id": numara,
            # BUGÜNÜN TARİHİ — SABİT DEĞİL. ÖLÇÜLDÜ: sabit bir tarih yazmak
            # belgeyi GİB Schematron'una takıyordu:
            #   ERROR_CODE=10003 "Geçersiz cbc:IssueDate değeri : '2026-09-11'
            #   cbc:IssueDate alanı günün tarihinden ileri bir tarih olamaz"
            # Gelecek tarihli fatura kesilemez; sandbox bunu şema seviyesinde
            # reddediyor.
            "issued_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z"),
            "is_efatura_user": efatura,
            "company": {
                "id": 1,
                "name": "Sungur Tarim A.S.",
                "tax_number": KIMLIK["IZIBIZ_VKN"],
                "tax_office": "Kadikoy",
            },
            "customer": {
                # B2B'de alıcı da MÜKELLEF olmalı: sandbox'ın kendi VKN'si.
                "name": "Izibiz Test Alici" if efatura else "Deneme Musteri",
                "tax_number": KIMLIK["IZIBIZ_VKN"] if efatura else "11111111111",
                "tax_office": "Kadikoy" if efatura else None,
            },
            "totals": {"tax": vergi, "grand_total": tutar},
            "items": [
                {
                    "description": "Bugday",
                    "qty": Decimal("1"),
                    "unit_code": birim,
                    "unit_price": tutar,
                    "tax_rate": Decimal(oran),
                    "total": tutar,
                }
            ],
        }
    )


def _numara(sira: int) -> str:
    """UBL-TR fatura numarası: 3 harf + 4 hane yıl + 9 HANE = 16 karakter."""
    numara = f"SNG2026{KOSU}{sira}"
    assert len(numara) == 16, numara
    assert numara[7:].isdigit(), numara  # son dokuz RAKAM olmak zorunda
    return numara


# --- 1. e-Arşiv: gönderim -> WEB_KEY -> PDF -------------------------------
@pytest.fixture(scope="module")
def earsiv_gonderimi(saglayici) -> object:
    numara = _numara(1)
    sonuc = saglayici.submit(_fatura(efatura=False, numara=numara))
    print(f"\n[EARSIV] submit({numara}) -> status={sonuc.status} "
          f"belge={sonuc.external_id} web_key={'VAR' if sonuc.web_key else 'YOK'} "
          f"hata={scrub(str(sonuc.error))}")
    return sonuc


def test_EARSIV_GONDERIMI_SANDBOXTA_KABUL_EDILIYOR(earsiv_gonderimi) -> None:
    assert earsiv_gonderimi.status == PENDING, scrub(str(earsiv_gonderimi.error))
    assert earsiv_gonderimi.external_id, "sağlayıcı belge kimliği vermedi"


def test_EARSIV_WEB_KEY_GERCEKTEN_DONUYOR(earsiv_gonderimi) -> None:
    """Göç `20260911_0081`in var olma sebebi: bu değer GERÇEKTEN geliyor.

    Anahtar YALNIZ bu yanıtta bir kez döner; saklanmazsa e-Arşiv PDF'i
    KALICI olarak erişilemez olur.
    """
    assert earsiv_gonderimi.web_key, "WEB_KEY gelmedi — göçün dayanağı çürür"
    assert "webValidationKey" in earsiv_gonderimi.web_key, earsiv_gonderimi.web_key
    # Göçte ilan edilen 255 hane gerçek değeri ALIYOR mu — ölçüldü.
    assert len(earsiv_gonderimi.web_key) <= 255, len(earsiv_gonderimi.web_key)


def test_EARSIV_PDF_WEB_KEY_ILE_GERCEKTEN_INIYOR(saglayici, earsiv_gonderimi) -> None:
    """Kapanan boşluk: bu yol önce `EARSIV_WEB_KEY_YOK` ile HİÇ kurulmuyordu.

    XFAIL KALDIRILDI (E2b) — ve kaldırılması bu satırın KENDİ TALİMATIYDI:
    "sandbox bunu çözer hâle gelirse test XPASS olur ve bu satır GÖZDEN
    GEÇİRİLİR." XPASS oldu, gözden geçirildi ve sebebi ÖLÇÜLDÜ: kusur
    sandboxta değil BİZDEYDİ. `GetEArchiveInvoice` PDF DEĞİL, UBL XML taşıyan
    bir ZIP döndürüyor; PDF `GetEArchiveInvoiceList` + `HEADER_ONLY=N` +
    `CONTENT_TYPE=PDF` ile geliyor (`docs/izibiz-sandbox-bulgular.md` §10.1).
    Eski gerekçedeki "İzibiz kendi kimliğiyle anahtarlıyor olabilir" tahmini
    de ÇÜRÜTÜLDÜ (§9.2).
    """
    if not earsiv_gonderimi.web_key:
        pytest.skip("WEB_KEY yok; PDF yolu ölçülemez")
    icerik = saglayici.fetch_pdf(
        earsiv_gonderimi.external_id,
        channel="EARSIV",
        web_key=earsiv_gonderimi.web_key,
    )
    print(f"[EARSIV] fetch_pdf -> {len(icerik)} bayt")
    assert icerik.startswith(b"%PDF-"), icerik[:20]
    assert len(icerik) > 1000, len(icerik)


def test_EARSIV_DURUM_SORGUSU_HAM_KOD_TASIYOR(saglayici, earsiv_gonderimi) -> None:
    """XFAIL KALDIRILDI (E2b) — sebep ÖLÇÜLDÜ, sandbox değişmedi.

    "BOŞ dönüyor" sanılan yanıt DOLUYDU: sağlayıcı bizim ETTN'imizle
    `STATUS=100` (KUYRUĞA EKLENDİ) cevabını veriyor ve bunu gönderimden SIFIR
    saniye sonra bile veriyor. `IZIBIZ_STATUS_ALIASES`te `100` yoktu, o yüzden
    `query_status` cevabı yere düşürüp `UNRESOLVED` diyordu
    (`docs/izibiz-sandbox-bulgular.md` §10.2).
    """
    sonuc = saglayici.query_status(
        earsiv_gonderimi.external_id, channel="EARSIV", uuid=earsiv_gonderimi.uuid
    )
    print(f"[EARSIV] query_status -> status={sonuc.status} gib={sonuc.gib_status_code}")
    assert sonuc.status != "NONE", scrub(str(sonuc.error))
    assert sonuc.gib_status_code, "ham GİB kodu gelmedi"


# --- 2. Birim kodu ve %0 istisna: TEL ÜSTÜNDE ------------------------------
def test_UNECE_BIRIM_KODU_SANDBOXTA_KABUL_EDILIYOR(saglayici) -> None:
    """"kg" -> KGM eşlemesi doğruysa sağlayıcı belgeyi REDDETMEZ."""
    numara = _numara(2)
    sonuc = saglayici.submit(_fatura(efatura=False, numara=numara, birim="kg"))
    print(f"[BIRIM] KGM submit({numara}) -> {sonuc.status} {scrub(str(sonuc.error))}")
    assert sonuc.status == PENDING, scrub(str(sonuc.error))


def test_SIFIR_KDV_ISTISNA_KODU_SANDBOXTA_KABUL_EDILIYOR(saglayici) -> None:
    """%0 + `TaxExemptionReasonCode=351` gerçekten geçiyor mu — ölçüldü."""
    numara = _numara(3)
    sonuc = saglayici.submit(_fatura(efatura=False, numara=numara, oran="0"))
    print(f"[ISTISNA] %0 submit({numara}) -> {sonuc.status} {scrub(str(sonuc.error))}")
    assert sonuc.status == PENDING, scrub(str(sonuc.error))


# --- 3. B2B (e-Fatura) KAPISI ---------------------------------------------
@pytest.mark.skipif(
    not wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED,
    reason=(
        "e-Fatura gönderimi DOĞRULANMADI (IZIBIZ_EFATURA_SUBMIT_VERIFIED=False). "
        "Kapıyı açmak için önce `test_B2B_KAPI_DENEMESI_KANIT_URETIR` elle "
        "koşulup KANIT üretilmeli; kanıt `docs/izibiz-sandbox-bulgular.md`e girer."
    ),
)
def test_B2B_GONDERIMI_SANDBOXTA_KABUL_EDILIYOR(saglayici) -> None:
    """Kapı AÇIKSA gönderim gerçekten çalışmalı — açık kapı boş kalmaz."""
    numara = _numara(4)
    sonuc = saglayici.submit(_fatura(efatura=True, numara=numara))
    assert sonuc.status == PENDING, scrub(str(sonuc.error))
    assert sonuc.external_id


def test_B2B_KAPI_DENEMESI_KANIT_URETIR(saglayici) -> None:
    """B2B kapısını AÇMAYA yetecek kanıtı üretir; kendisi kapıyı AÇMAZ.

    Kapı `IZIBIZ_EFATURA_SUBMIT_VERIFIED=False` iken adaptör gönderimi
    ÖNCEDEN reddeder ve ağa HİÇ çıkmaz — bu test o REDDİN gerçekten
    çalıştığını ölçer. Kapının kendisi bir KOD DEĞİŞİKLİĞİDİR ve ancak
    burada basılan kanıt (belge kimliği + GetInvoiceStatus cevabı) kayda
    geçtikten sonra `True` yapılabilir.

    NEDEN OTOMATİK AÇILMIYOR: doğrulanmamış bir e-Fatura gönderimi CANLI BİR
    BELGE üretebilir ve okuma yollarının aksine burada hata GERİ ALINAMAZ.
    Bayrağı bir testin kendi kendine çevirmesi, kapının var olma sebebini
    ortadan kaldırırdı.
    """
    numara = _numara(5)
    payload = _fatura(efatura=True, numara=numara)
    assert payload["profile_id"] == "TICARIFATURA", payload["profile_id"]
    assert payload["channel"] == "EFATURA", payload["channel"]

    sonuc = saglayici.submit(payload)
    print(
        f"\n[B2B] KAPI={wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED} "
        f"submit({numara}) alici={SANDBOX_PK} -> status={sonuc.status} "
        f"belge={sonuc.external_id} hata={scrub(str(sonuc.error))}"
    )

    if not wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED:
        # Kapı KAPALI: gönderim ağa çıkmadan reddedilmeli.
        assert sonuc.status != PENDING, "kapalı kapı gönderimi geçirdi"
        assert sonuc.external_id is None, "kapalı kapı belge kimliği üretti"
        assert wire.IZIBIZ_EFATURA_SUBMIT_ERROR in str(sonuc.error), scrub(str(sonuc.error))
        return

    # Kapı AÇIK: gönderim gerçek olmalı ve durum sorgulanabilmeli.
    assert sonuc.status == PENDING, scrub(str(sonuc.error))
    durum = saglayici.query_status(sonuc.external_id, channel="EFATURA", uuid=sonuc.uuid)
    print(f"[B2B] GetInvoiceStatus({sonuc.external_id}) -> "
          f"status={durum.status} gib={durum.gib_status_code}")
    assert durum.status != "NONE", scrub(str(durum.error))
