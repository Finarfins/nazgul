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
        "ÖLÇÜLDÜ 2026-09-11 (E2 koşusu): sağlayıcı iptali `ERROR_CODE=10008 "
        "\"Belirtilen kritere uygun kayıt bulunamamıştır. Belge ETTN : <ETTN>\"` "
        "ile reddediyor. HATA BİZDE DEĞİL, ANAHTARDA: istek şemaya birebir uyuyor "
        "(`?xsd=5`ten okundu) ve sağlayıcı gönderdiğimiz ETTN'i GERİ YANKILAYIP "
        "'böyle bir kayıt yok' diyor — yani İzibiz e-Arşiv belgesini bizim uuid5 "
        "ile türettiğimiz istemci ETTN'iyle ANAHTARLAMIYOR. Bu, E1'in §7.3'te "
        "VARSAYMADAN açık bıraktığı sorunun cevabıdır ve iptal, durum sorgusunun "
        "söyleyemediğini söyledi: boş bir yanıt hiçbir şey ayırt ettirmezken bu "
        "hata aradığı anahtarı yankılıyor. Soru entegrasyon@izibiz.com.tr'ye "
        "sorulmak üzere belge kimlikleriyle birlikte yazıldı: "
        "`docs/izibiz-sandbox-bulgular.md` §8.2. strict=False: doğru anahtar "
        "öğrenilip düzeltilince test XPASS olur ve bu satır ile spec §9.4 "
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


@pytest.mark.xfail(
    reason=(
        "İptalin KENDİSİ başarısız (yukarıdaki xfail: kayıt ETTN ile "
        "bulunamıyor), dolayısıyla 'iptal sonrası durum' diye ölçülecek bir şey "
        "de yok. Ölçülen: `UNRESOLVED`, ham GİB kodu YOK — E1'in §7.3 "
        "bulgusuyla (aynı belge, aynı anahtar, boş yanıt) BİREBİR tutarlı ve "
        "aynı kök sebebe bağlı. strict=False."
    ),
    strict=False,
)
def test_IPTAL_SONRASI_DURUM_SORGUSU(saglayici, iptal_edilecek) -> None:
    """İptalden sonra GİB ham kodu ne diyor?

    Beklenti ÖLÇÜLMEMİŞTİR, o yüzden test bir DEĞER dayatmıyor: yalnız sorgunun
    çözümlenebildiğini ve ham kodun geldiğini ölçüyor. `IZIBIZ_STATUS_ALIASES`
    içinde `RAPORLANDI IPTAL -> REJECTED` eşlemesi VAR ama o eşlemenin iptal
    sonrası GERÇEKTEN geldiği GÖRÜLMEDİ; görülürse eşleme (ve `CANCELLED` ile
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


@pytest.mark.xfail(
    reason=(
        "AÇIK SORU — entegrasyon@izibiz.com.tr'ye sorulacak; metni ve SORULACAK "
        "BELGE KİMLİKLERİ `docs/izibiz-sandbox-bulgular.md` §8.2'de. "
        "ÖLÇÜLDÜ 2026-09-08 (E1), 2026-09-11'de (E2) YİNELENDİ: TAZE bir e-Arşiv belgesi için "
        "`GetEArchiveInvoice` PDF vermiyor (`PDF_YOK`), 0/3/8/15/30/45 sn "
        "beklenerek ~100 sn yoklandı — ZAMANLAMA DEĞİL. E2 bu ölçümü "
        "DEĞİŞTİRMEDİ; değiştirdiği tek şey, PDF'i isteyen bir UCUN artık VAR "
        "olması (`GET /invoices/{id}/einvoice/download?format=pdf`). "
        "strict=False: sandbox bunu çözer hâle gelirse test XPASS olur."
    ),
    strict=False,
)
def test_INDIRME_UCUNUN_ISTEDIGI_PDF_GERCEKTEN_INIYOR(saglayici, iptal_edilecek) -> None:
    """Ucun sağlayıcıdan istediği baytların TA KENDİSİ — aynı argümanlarla.

    Uç ``provider.fetch_pdf(ext, channel=..., web_key=...)`` çağırıyor; burada
    da BİREBİR o çağrı yapılıyor. Farklı bir çağrı yazmak, ucun kullanmadığı
    bir yolu ölçmek olurdu.
    """
    if not iptal_edilecek.web_key:
        pytest.skip("WEB_KEY yok; PDF yolu ölçülemez")
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
