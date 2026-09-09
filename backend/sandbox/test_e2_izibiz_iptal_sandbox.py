"""E2 — GERÇEK İzibiz sandbox'ında e-Arşiv İPTALİ ve belge SURETİ.

    cd backend && python -m pytest sandbox/test_e2_izibiz_iptal_sandbox.py -q -s

CI'DA KOŞMAZ. Gerekçe ``sandbox/test_e1_izibiz_sandbox.py``in başında ayrıntılı
yazılı ve burada da geçerli: kanonik koşucu (``run_isolated_tests.py``) yalnız
``backend/test_*.py`` ve ``backend/tests/test_*.py`` glob'larına bakıyor,
ikisi de ÖZYİNELEMESİZ, ``backend/sandbox/`` ikisinin de DIŞINDA. Kimlik yoksa
dosya TOPLANMA anında atlanıyor, yani çıplak bir ``pytest`` bile kırmızı değil
YEŞİL bir SKIP üretir.

BU DOSYA NE KANITLIYOR — ve neyi kanıtlamıyor.

``test_e2_ebelge_yasam_dongusu.py`` (mock taşıma) "kodumuz şemaya uygun baytı
üretiyor" diyor. Şemanın kendisi de tahmin değil: canlı ``?wsdl`` -> ``?xsd=5``
üzerinden okundu. Ama **okunmuş bir şema, kabul edilmiş bir istek değildir.**
Bu dosya asıl soruyu bugün soruyor: sağlayıcı bu iptali GERÇEKTEN kabul ediyor
mu?

SIRLAR EKRANA BASILMAZ: ``izibiz_smoke.scrub()`` bilinen her sırrı gövdelerden
siler; bu dosya kimlik değerlerini hiçbir assert mesajına koymaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sandbox.izibiz_smoke import scrub  # noqa: E402

# E1 dosyasının koşum takımı YENİDEN KULLANILIYOR, kopyalanmıyor: kimlik
# yükleme, ayarlar, fatura kurucusu ve numara üreticisi orada ölçülmüş
# hâlleriyle duruyor. Kopyalamak, iki dosyanın sessizce ayrışacağı bir gelecek
# üretirdi (ör. UBL-TR numara kuralı yalnız birinde düzeltilirdi). İçe aktarma
# ayrıca modül düzeyindeki kimlik SKIP'ini de miras alıyor.
from sandbox.test_e1_izibiz_sandbox import (  # noqa: E402
    _ayarlar,
    _fatura,
    _numara,
)

from app.einvoice import (  # noqa: E402
    CANCELLED,
    PENDING,
    IzibizEInvoiceProvider,
)


@pytest.fixture(scope="module")
def saglayici() -> IzibizEInvoiceProvider:
    # Taşıma verilmiyor: adaptör KENDİ HTTP taşımasını kurar — gerçek ağ yolu.
    return IzibizEInvoiceProvider(_ayarlar())


@pytest.fixture(scope="module")
def iptal_edilecek(saglayici) -> object:
    """İptal denemesi için TAZE bir e-Arşiv belgesi.

    E1'in ``earsiv_gonderimi`` fixture'ı YENİDEN KULLANILMIYOR ve bu KASITLI:
    o belge orada PDF ve durum sorgusuyla zaten yoklanıyor; aynı belgeyi bir de
    İPTAL etmek, iki dosyanın koşum SIRASINA bağlı hâle gelmesi demekti.
    """
    numara = _numara(7)
    sonuc = saglayici.submit(_fatura(efatura=False, numara=numara))
    print(
        f"\n[E2/EARSIV] submit({numara}) -> status={sonuc.status} "
        f"belge={sonuc.external_id} ettn={sonuc.uuid} "
        f"web_key={'VAR' if sonuc.web_key else 'YOK'} hata={scrub(str(sonuc.error))}"
    )
    return sonuc


def test_IPTAL_ICIN_GONDERIM_ONCE_KABUL_EDILIYOR(iptal_edilecek) -> None:
    """İptal ölçümünün ön koşulu: ortada gerçekten gönderilmiş bir belge var."""
    assert iptal_edilecek.status == PENDING, scrub(str(iptal_edilecek.error))
    assert iptal_edilecek.external_id, "sağlayıcı belge kimliği vermedi"
    assert iptal_edilecek.uuid, "istemci ETTN yok — iptal ETTN ile kurulur"


@pytest.mark.xfail(
    reason=(
        "ÖLÇÜLDÜ 2026-09-09: sağlayıcı iptali `ERROR_CODE=10008 "
        "\"Belirtilen kritere uygun kayıt bulunamamıştır. Belge ETTN : <ETTN>\"` "
        "ile reddediyor. GEREKÇE E2b'DE DÜZELTİLDİ — bu satır önce 'İzibiz "
        "belgeyi bizim ETTN'imizle anahtarlamıyor' diyordu ve O ÇÜRÜTÜLDÜ "
        "(§9.2: sağlayıcının tuttuğu UUID gönderdiğimizin TA KENDİSİ ve "
        "AYNI anahtarla yapılan durum sorgusu AYNI oturumda CEVAP VERİYOR). "
        "Geriye kalan tek aday: iptal, `STATUS=100` (KUYRUĞA EKLENDİ) bir "
        "belgeyi iptal edilebilir saymıyor olabilir — ADAY, ÖLÇÜM DEĞİL, çünkü "
        "sandbox imzalayıcısı hiçbir belgeyi bitirmiyor ve raporlanmış KENDİ "
        "belgemiz YOK. Sağlayıcıya sorulacak yeni soru: "
        "`docs/izibiz-sandbox-bulgular.md` §10.4. strict=False: sandbox belgeyi "
        "imzalar hâle gelirse test XPASS olur ve bu satır ile spec §9.4 "
        "GÖZDEN GEÇİRİLİR."
    ),
    strict=False,
)
def test_EARSIV_IPTALI_SANDBOXTA_KABUL_EDILIYOR(saglayici, iptal_edilecek) -> None:
    """Asıl ölçüm: entegratör bu iptali kabul ediyor mu?"""
    if not iptal_edilecek.uuid:
        pytest.skip("ETTN yok; iptal yolu ölçülemez")
    sonuc = saglayici.cancel(
        iptal_edilecek.external_id, channel="EARSIV", uuid=iptal_edilecek.uuid
    )
    print(f"[E2/EARSIV] cancel -> status={sonuc.status} hata={scrub(str(sonuc.error))}")
    assert sonuc.status == CANCELLED, scrub(str(sonuc.error))


def test_IPTAL_SONRASI_DURUM_SORGUSU(saglayici, iptal_edilecek) -> None:
    """İptalden sonra GİB ham kodu ne diyor?

    XFAIL KALKTI (E2b). Önce "iptal başarısız olduğu için ölçülecek bir şey
    yok, sonuç `UNRESOLVED`" diyordu; §10.2 ölçtü ki `UNRESOLVED`ın sebebi
    iptal DEĞİL, `IZIBIZ_STATUS_ALIASES`teki eksik `100` satırıydı. Eşleme
    düzeltildi ve durum sorgusu artık iptalden BAĞIMSIZ olarak çözülüyor —
    bu test o düzeltmenin GERÇEK ağ yolundaki kapısıdır.

    Beklenti bir DEĞER dayatmıyor: yalnız sorgunun çözümlenebildiğini ve ham
    kodun geldiğini ölçüyor. `IZIBIZ_STATUS_ALIASES` içinde
    `RAPORLANDI IPTAL -> REJECTED` eşlemesi VAR ama o eşlemenin iptal sonrası
    GERÇEKTEN geldiği GÖRÜLMEDİ; görülürse eşleme (ve `CANCELLED` ile
    ilişkisi) gözden geçirilmelidir.
    """
    sonuc = saglayici.query_status(
        iptal_edilecek.external_id, channel="EARSIV", uuid=iptal_edilecek.uuid
    )
    print(
        f"[E2/EARSIV] iptal sonrasi query_status -> status={sonuc.status} "
        f"gib={sonuc.gib_status_code} hata={scrub(str(sonuc.error))}"
    )
    assert sonuc.status != "UNRESOLVED", scrub(str(sonuc.error))
    assert sonuc.gib_status_code, "ham GİB kodu gelmedi"


def test_INDIRME_UCUNUN_ISTEDIGI_PDF_GERCEKTEN_INIYOR(saglayici, iptal_edilecek) -> None:
    """Ucun sağlayıcıdan istediği baytların TA KENDİSİ — aynı argümanlarla.

    XFAIL KALKTI (E2b) ve sebebi ÖLÇÜLDÜ: E1/E2'de `PDF_YOK` alınıyordu çünkü
    `GetEArchiveInvoice` PDF DEĞİL, UBL XML taşıyan bir ZIP döndürüyor —
    zamanlama da anahtar da değil, OPERASYON SEÇİMİ yanlıştı (§10.1). PDF
    `GetEArchiveInvoiceList` + `HEADER_ONLY=N` + `CONTENT_TYPE=PDF` ile
    geliyor ve `STATUS=100` bir belgede DE çalışıyor.

    Uç ``provider.fetch_pdf(ext, channel=..., web_key=...)`` çağırıyor; burada
    da BİREBİR o çağrı yapılıyor. Farklı bir çağrı yazmak, ucun kullanmadığı
    bir yolu ölçmek olurdu. `web_key` ARTIK KULLANILMIYOR ama imzada duruyor
    ve BİLEREK geçiliyor: ucun çağrısı bu, ve anahtar geçen bir çağrının hâlâ
    çalıştığı da bir sözleşmedir.
    """
    icerik = saglayici.fetch_pdf(
        iptal_edilecek.external_id,
        channel="EARSIV",
        web_key=iptal_edilecek.web_key,
    )
    print(f"[E2/EARSIV] fetch_pdf -> {len(icerik)} bayt")
    assert icerik.startswith(b"%PDF-"), icerik[:20]
    assert len(icerik) > 1000, len(icerik)


def test_IPTAL_ETTNSIZ_AGA_HIC_CIKMIYOR(saglayici) -> None:
    """Gerçek adaptörle de ölçülüyor: ETTN'siz iptal SOKET AÇMAZ.

    Mock taşımalı ikizi var ama bu ayrı bir şey ölçüyor: orada taşıma
    sahteydi, burada GERÇEK. Önkoşul yanlış yere konsaydı bu çağrı sağlayıcıda
    karşılığı olmayan bir oturum açardı ve bu, ancak gerçek ağ yolunda
    görülebilirdi.
    """
    sonuc = saglayici.cancel("SNG0000000000000", channel="EARSIV", uuid="")
    print(f"[E2/EARSIV] ETTN'siz cancel -> status={sonuc.status}")
    assert sonuc.status == "FAILED"
    assert "ETTN" in (sonuc.error or ""), scrub(str(sonuc.error))
