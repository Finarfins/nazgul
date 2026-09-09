"""WA5: mesaj kimliği `external_id`ye, medya İNDİRİLİYOR, fatura ÖZETLENİYOR.

Konu: `app/whatsapp/saglayici.py` (mesaj kimliği + `medya_indir`),
`app/notifications/provider.py` (WHATSAPP adaptörünün `external_id`si),
`app/whatsapp/fatura.py` (YENİ) ve `app/whatsapp/service.py`nin medya dalı.

GÖÇ YOK, ROTA YOK. İkisi de ADIYLA ölçülüyor.

--- BU DİLİMİN EN ÖNEMLİ SINIRI: KAYIT AÇILMIYOR -------------------------

Kaynak akış (nazgul_website `fatura.py`) faturayı okuyup ONAY isteyip
TASLAK ALIŞ BELGESİ açıyordu. O son adım `whatsapp_pending_actions`a
`action_type='FATURA_TASLAK'` yazar; o sütun `ck_wpa_action_type` CHECK'i
ile `IN ('TAHSILAT')` diye çivili, yani yeni tür BİR GÖÇ demektir. Bu
dilim göçsüz olduğu için tür EKLENMEDİ ve yazma yolu HİÇ AÇILMADI.

Bu bir "yapılmadı" notu değil ÖLÇÜLEN bir sözleşmedir: aşağıda hem
YAPISAL (imzada `db` yok, modülde yazma çağrısı yok), hem DAVRANIŞSAL
(gerçek şemada medya mesajı işleniyor, tablo BOŞ kalıyor) kapı var.

--- KAYNAK TESTLERİNDEN NE TAŞINDI ---------------------------------------

`nazgul_website/backend/test_whatsapp_fatura.py` — 10 test:
  * TAŞINDI (6): `_kalem_kararlari`ın üç altın vakası (biri 6 parametreli),
    `_ozet_metni`nin iki vakası, NoOp'un medya indirmemesi.
  * TERSİNE ÇEVRİLDİ (1): `test_islem_turu_kapali_kumede_kayitli` kaynakta
    türün kümede OLDUĞUNU çiviliyordu; burada OLMADIĞINI çiviliyor
    (`test_FATURA_TASLAK_TURU_bu_turda_TANIMLI_DEGIL`). Aynı kapı, ters
    yön — çünkü sözleşme ters.
  * ZATEN KAPSANIYOR (3): medya ayrıştırma vakaları (`parse_media_messages`
    kaynakta ayrı fonksiyondu; burada `cloud_api.gelen_mesajlari_coz` tek
    giriştir ve `test_wa1_ingress.py` onu ölçüyor — `media_id`/`media_mime`
    iddiaları orada).

`nazgul_website/backend/test_whatsapp_fatura_akisi.py` — 28 test: TAMAMI
DÜŞTÜ. Her biri ya model çağrısına (`llm.belge_oku`), ya bekleyen-işlem
yazımına, ya da `belge_olustur`a bağlı; üçü de bu turda YOK. Düşenler
"unutuldu" değil KAPSAM DIŞI: göç geldiğinde geri gelecek dosya odur.

Özet: 6 taşındı, 1 tersine çevrildi, 3 zaten kapsanıyor, 28 düştü.

--- MUTASYON TABLOSU -----------------------------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `MetaBulutSaglayici.metin_gonder`in `return mesaj_kimligi(govde)`
    satırını `return None` yapmak (ya da `mesaj_kimligi`i düşürmek)
                        -> `test_MESAJ_KIMLIGI_2xx_GOVDESINDEN_external_ide`
                           KIRMIZI. Adaptör `SENT` yazmayı sürdürür ama
                           denetim satırı kimliksiz kalır.
  * `mesaj_kimligi`i kimlik yokken istisna atacak biçimde SERTLEŞTİRMEK
                        -> `test_KIMLIK_YOKSA_2xx_HALA_SENT_ve_external_id_BOS`
                           KIRMIZI. Bu mutant "daha güvenli" görünür ve
                           DEĞİLDİR: `supports_idempotency=False` olduğu
                           için gitmiş mesaj yeniden gönderilirdi.
  * `medya_indir`in İKİNCİ isteğinden `Authorization` başlığını düşürmek
                        -> `test_MEDYA_IKI_ADIMDA_ve_IKISINDE_DE_JETON`
                           KIRMIZI (Meta ikinci adımda 401 verir).
  * `medya_indir`den `https://` doğrulamasını düşürmek
                        -> `test_MEDYA_ADRESI_https_DEGILSE_REDDEDILIYOR`
                           KIRMIZI (jetonlu istek başka yere sürüklenir).
  * `_kalem_kararlari`dan `eslesme_durumu != "eslesti"` yüklemini düşürüp
    faturadaki adla ürün açmak
                        -> `test_eslesmeyen_satir_ATLANIR_yeni_urun_ACILMAZ`
                           KIRMIZI.
  * `_ozet_metni`den atlanan sayısını gizlemek
                        -> `test_ozet_ATLANANI_ACIKCA_soyluyor` KIRMIZI.
  * `_ozet_metni`ye kaynaktaki "ONAY yazın" davetini geri koymak / sınır
    cümlesini düşürmek
                        -> `test_OZET_KAYIT_ACILMADIGINI_SOYLUYOR` KIRMIZI.
  * Medyayı ayrıştırıp SONUCU YAZMAK (ör. `fatura`ya `db` geçirip
    `bekleyen.taslak_olustur` çağırmak)
                        -> `test_FATURA_MODULU_VERITABANINA_DOKUNMUYOR` VE
                           `test_DAVRANIS_MEDYA_MESAJI_HICBIR_ISLEM_YAZMIYOR`
                           KIRMIZI. İlki yapısal (imza + AST), ikincisi
                           gerçek şemada satır sayıyor.
  * `.gitattributes`tan `*.md text eol=lf` ya da `docs/whatsapp/*.md`
    satırını silmek
                        -> `test_MD_SATIR_SONU_CIVISI_ve_FIXTURE_KAPSAMI`
                           KIRMIZI.
"""
from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DEPO = BACKEND.parent
FATURA_KAYNAK = BACKEND / "app" / "whatsapp" / "fatura.py"
GITATTRIBUTES = DEPO / ".gitattributes"

NUMARA = "905405995959"
TABLO = "whatsapp_pending_actions"


# ---------------------------------------------------------------------------
# 1) Mesaj kimliği: 2xx gövdesinden `external_id`ye
# ---------------------------------------------------------------------------


class _Yanit:
    """`urlopen` bağlam yöneticisinin en küçük taklidi."""

    def __init__(self, govde: bytes) -> None:
        self._govde = govde

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return self._govde


def _meta_kur(monkeypatch, govde: bytes) -> list:
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings

    istekler: list = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda istek, timeout=None: (istekler.append(istek) or _Yanit(govde)),
    )
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("JETON123"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "5550001")
    monkeypatch.setattr(settings, "whatsapp_graph_base_url", "https://graph.invalid")
    monkeypatch.setattr(settings, "whatsapp_graph_version", "v21.0")
    return istekler


#: Meta'nın gerçek `messages` yanıtının biçimi.
_GERCEK_GOVDE = json.dumps(
    {
        "messaging_product": "whatsapp",
        "contacts": [{"input": NUMARA, "wa_id": NUMARA}],
        "messages": [{"id": "wamid.HBgMOTA1NDA1OTk1OTU5FQIAERgSMEEyQkM="}],
    }
).encode("utf-8")


def test_MESAJ_KIMLIGI_2xx_GOVDESINDEN_external_ide(monkeypatch) -> None:
    """Taşıyıcı `messages[0].id`yi döner, adaptör onu `external_id`ye yazar.

    WA4 bu alanı BİLEREK boş bırakmıştı ("WA3'ün sözleşmesini WA4 uğruna
    genişletmek olurdu"). WA5'te taşıyıcının İKİNCİ bir çağıranı doğdu ve
    genişletme yapıldı; burada UÇTAN UCA ölçülüyor.
    """
    from app.notifications.provider import WhatsAppNotificationProvider
    from app.whatsapp import saglayici as sag

    _meta_kur(monkeypatch, _GERCEK_GOVDE)

    # Taşıyıcı katmanı
    assert sag.saglayici_al().metin_gonder(NUMARA, "cevap") == (
        "wamid.HBgMOTA1NDA1OTk1OTU5FQIAERgSMEEyQkM="
    )

    # Adaptör katmanı — `SENT` VE dolu kimlik
    sonuc = WhatsAppNotificationProvider(settings=None).send(
        {"recipient": NUMARA, "payload": {"body": "cevap"}}
    )
    assert sonuc.status == "SENT"
    assert sonuc.external_id == "wamid.HBgMOTA1NDA1OTk1OTU5FQIAERgSMEEyQkM="


@pytest.mark.parametrize(
    "govde",
    [
        b"{}",                                  # kimlik alanı YOK
        b'{"messages": []}',                    # liste BOŞ
        b'{"messages": [{}]}',                  # eleman var, `id` yok
        b'{"messages": [{"id": "   "}]}',       # boşluktan ibaret
        b'{"messages": "wamid.X"}',             # liste değil
        b'{"messages": ["wamid.X"]}',           # eleman sözlük değil
        b"bu json degil",                       # ayrıştırılamaz
        b"\xff\xfe gecersiz utf8",              # çözülemez
        b"[1, 2, 3]",                           # sözlük değil
    ],
)
def test_KIMLIK_YOKSA_2xx_HALA_SENT_ve_external_id_BOS(monkeypatch, govde) -> None:
    """Kimlik yoksa `None` — ama gönderim BAŞARILI sayılır.

    Kimliği ŞART koşmak "daha sıkı" görünür ve YANLIŞTIR: Meta gövde
    biçimini değiştirdiği gün GERÇEKTEN GİTMİŞ mesajlar hata sayılır ve
    `supports_idempotency = False` olduğu için outbox aynı mesajı
    kullanıcıya İKİNCİ KEZ gönderirdi.
    """
    from app.notifications.provider import WhatsAppNotificationProvider
    from app.whatsapp import saglayici as sag

    _meta_kur(monkeypatch, govde)

    assert sag.saglayici_al().metin_gonder(NUMARA, "cevap") is None
    sonuc = WhatsAppNotificationProvider(settings=None).send(
        {"recipient": NUMARA, "payload": {"body": "cevap"}}
    )
    assert sonuc.status == "SENT" and sonuc.external_id is None


def test_DONUSU_YOK_SAYAN_CAGIRAN_ETKILENMIYOR(monkeypatch) -> None:
    """GERİYE UYUMLULUK: WA3 işçisi dönüşü kullanmıyor ve kullanmamalı.

    Sözleşme genişledi ama DARALMADI: `metin_gonder`in başarı ölçütü hâlâ
    istisna ATILMAMASIDIR. İşçi dönüşü hiç okumaz — kaynakta o çağrının
    bir atamaya bağlanmadığı AST ile ölçülüyor.
    """
    kaynak = (BACKEND / "app" / "whatsapp" / "service.py").read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    cagrilar = [
        d for d in ast.walk(agac)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "metin_gonder"
    ]
    assert cagrilar, "işçi taşıyıcıyı çağırmalı"
    # Her çağrı bir `Expr` (ifade-deyim) olarak durmalı: atanan bir dönüş,
    # işçinin kimliğe BAĞIMLI hale geldiği anlamına gelirdi.
    atanmis = [
        d for d in ast.walk(agac)
        if isinstance(d, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(a, ast.Call)
            and isinstance(a.func, ast.Attribute)
            and a.func.attr == "metin_gonder"
            for a in ast.walk(d)
        )
    ]
    assert atanmis == [], "işçi dönüşü kullanmamalı (geriye uyumluluk)"


# ---------------------------------------------------------------------------
# 2) Medya indirme — sahte `urlopen`
# ---------------------------------------------------------------------------


_PDF = b"%PDF-1.4 sahte fatura"


def test_MEDYA_IKI_ADIMDA_ve_IKISINDE_DE_JETON(monkeypatch) -> None:
    """Meta indirilebilir bağlantı vermez: önce `/{id}`, sonra dönen URL.

    İKİNCİ istekte `Authorization` DÜŞERSE Meta 401 verir; bu yüzden
    başlık İKİ istekte de ölçülüyor.
    """
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings
    from app.whatsapp import saglayici as sag

    istekler: list = []

    def sahte(istek, timeout=None):
        istekler.append(istek)
        if len(istekler) == 1:
            return _Yanit(json.dumps({"url": "https://lookaside.invalid/x"}).encode())
        return _Yanit(_PDF)

    monkeypatch.setattr(urllib.request, "urlopen", sahte)
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("JETON123"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "5550001")
    monkeypatch.setattr(settings, "whatsapp_graph_base_url", "https://graph.invalid")
    monkeypatch.setattr(settings, "whatsapp_graph_version", "v21.0")

    assert sag.MetaBulutSaglayici().medya_indir("MEDIA-1") == _PDF

    ustveri, indirme = istekler
    assert ustveri.full_url == "https://graph.invalid/v21.0/MEDIA-1"
    assert indirme.full_url == "https://lookaside.invalid/x"
    assert ustveri.get_header("Authorization") == "Bearer JETON123"
    assert indirme.get_header("Authorization") == "Bearer JETON123"


@pytest.mark.parametrize(
    "ustveri",
    [
        b'{"url": "http://lookaside.invalid/x"}',   # https DEĞİL
        b'{"url": "file:///etc/passwd"}',           # yerel dosya
        b'{"url": 42}',                             # metin değil
        b"{}",                                      # alan yok
        b"json degil",                              # ayrıştırılamaz
    ],
)
def test_MEDYA_ADRESI_https_DEGILSE_REDDEDILIYOR(monkeypatch, ustveri) -> None:
    """İkinci adresi Meta'nın GÖVDESİ söyler ve o gövde bizim denetimimizde
    değildir. `https://` şartı, jetonlu bir isteğin başka yere
    sürüklenmesini engeller — ikinci istek HİÇ kurulmamalı."""
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings
    from app.whatsapp import saglayici as sag

    istekler: list = []

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda istek, timeout=None: (istekler.append(istek) or _Yanit(ustveri)),
    )
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("J"))
    monkeypatch.setattr(settings, "whatsapp_graph_base_url", "https://graph.invalid")
    monkeypatch.setattr(settings, "whatsapp_graph_version", "v21.0")

    with pytest.raises(sag.KaliciGonderimHatasi):
        sag.MetaBulutSaglayici().medya_indir("MEDIA-1")
    assert len(istekler) == 1, "geçersiz adrese İKİNCİ istek KURULMAMALI"


def test_MEDYA_BOYUT_SINIRI_OKURKEN_UYGULANIYOR(monkeypatch) -> None:
    """`Content-Length` başlığına güvenmek yetmez: sunucu yalan söyleyebilir."""
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings
    from app.whatsapp import saglayici as sag

    def sahte(istek, timeout=None):
        if "MEDIA" in istek.full_url:
            return _Yanit(json.dumps({"url": "https://lookaside.invalid/x"}).encode())
        return _Yanit(b"x" * (sag.MEDYA_MAKS_BAYT + 1))

    monkeypatch.setattr(urllib.request, "urlopen", sahte)
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("J"))
    monkeypatch.setattr(settings, "whatsapp_graph_base_url", "https://graph.invalid")
    monkeypatch.setattr(settings, "whatsapp_graph_version", "v21.0")

    with pytest.raises(sag.KaliciGonderimHatasi):
        sag.MetaBulutSaglayici().medya_indir("MEDIA-1")


def test_noop_saglayici_MEDYA_INDIRMEZ() -> None:
    """Kaynaktan TAŞINDI. Yapılandırılmamış kurulum ağa ÇIKMAZ."""
    from app.whatsapp import saglayici as sag

    with pytest.raises(sag.KaliciGonderimHatasi):
        sag.NoOpSaglayici().medya_indir("MEDIA-1")


# ---------------------------------------------------------------------------
# 3) Belirlenimci karar katmanı — kaynağın altın vakaları
# ---------------------------------------------------------------------------


def _satir(**degisiklik):
    """Katalogla EŞLEŞMİŞ, geçerli bir okunmuş fatura satırı (kaynaktan)."""
    temel = {
        "faturadaki_ad": "Yağ filtresi",
        "miktar": "4",
        "birim": "Adet",
        "birim_fiyat": "250.00",
        "iskonto_yuzdesi": "0",
        "kdv_orani": 20,
        "eslesme_durumu": "eslesti",
        "eslesen_urun": {"id": 7, "ad": "Yağ filtresi", "kod": "84993120"},
    }
    temel.update(degisiklik)
    return temel


def test_eslesen_satir_KALEME_giriyor() -> None:
    from app.whatsapp import fatura

    kalemler, atlanan = fatura._kalem_kararlari([_satir()])
    assert atlanan == 0 and len(kalemler) == 1
    k = kalemler[0]
    assert k["karar"] == "mevcut" and k["urun_id"] == 7
    assert k["miktar"] == Decimal("4")
    assert k["birim_fiyat"] == Decimal("250.00")
    # Para alanları `Decimal` KALIR: bu turda yazılan bir yük yok, ama
    # `float`a düşen bir ara katman göç geldiğinde ondalık sözleşmesini
    # SESSİZCE ihlal ederdi.
    assert isinstance(k["miktar"], Decimal)
    assert isinstance(k["birim_fiyat"], Decimal)


def test_eslesmeyen_satir_ATLANIR_yeni_urun_ACILMAZ() -> None:
    """Faturadaki adla ürün yaratmak katalogda mükerrer kayıt üretir.

    Tedarikçi bizim adımızı kullanmaz; aynı parça her faturada başka
    yazılır. Eksik özet vermek, sessizce yanlış ürün açmaktan iyidir.
    """
    from app.whatsapp import fatura

    kalemler, atlanan = fatura._kalem_kararlari([
        _satir(),
        _satir(eslesme_durumu="eslesmedi", eslesen_urun=None),
        _satir(eslesme_durumu="belirsiz", eslesen_urun=None),
    ])
    assert atlanan == 2 and len(kalemler) == 1
    assert all(k["karar"] == "mevcut" for k in kalemler)


@pytest.mark.parametrize(
    "bozuk",
    [
        {"miktar": None},
        {"miktar": "0"},
        {"miktar": "-1"},
        {"birim_fiyat": None},
        {"birim_fiyat": "-5"},
        {"miktar": "abc"},
    ],
)
def test_okunamayan_sayi_satiri_ATLIYOR(bozuk) -> None:
    """Bir alan okunamadıysa satır ATLANIR; tahmin EDİLMEZ."""
    from app.whatsapp import fatura

    kalemler, atlanan = fatura._kalem_kararlari([_satir(**bozuk)])
    assert kalemler == [] and atlanan == 1


def test_ozet_ATLANANI_ACIKCA_soyluyor() -> None:
    """Sessiz eksiltme, tam özet sanılır."""
    from app.whatsapp import fatura

    metin = fatura._ozet_metni(
        {"tedarikci": "X Otomotiv", "genel_toplam": "54.000,00", "tarih": "14.08.2026"},
        kalem_sayisi=4,
        atlanan=3,
    )
    assert "X Otomotiv" in metin
    assert "Okunan kalem: 4" in metin
    assert "3 kalem ATLANDI" in metin


def test_ozet_atlanan_YOKKEN_uyari_YAZMIYOR() -> None:
    from app.whatsapp import fatura

    assert "ATLANDI" not in fatura._ozet_metni({"tedarikci": "X"}, 2, 0)


def test_OZET_KAYIT_ACILMADIGINI_SOYLUYOR() -> None:
    """Kaynaktan AYRILAN tek yer, ve ayrılma gerekçesi ölçülüyor.

    Kaynak "TASLAK alış oluşturmak için ONAY ... yazın" diyordu. Bu turda
    ONAY'ı işleyecek bekleyen-işlem satırı AÇILAMIYOR (CHECK göçü), yani
    o davet karşılıksız kalırdı: kullanıcı ONAY yazar, mesaj işlenmeden
    düşer ve kullanıcı faturayı GİRİLMİŞ sanırdı.
    """
    from app.whatsapp import fatura

    metin = fatura._ozet_metni({"tedarikci": "X"}, 2, 0)
    assert fatura.SINIR_CUMLESI in metin
    assert "kayıt OLUŞTURULMADI" in metin
    # Karşılıksız davet KALMAMALI.
    assert "ONAY" not in metin and "İPTAL" not in metin


# ---------------------------------------------------------------------------
# 4) Akış: indir → çöz → özetle. HER DAL bir METİN döner.
# ---------------------------------------------------------------------------


class _SahteSaglayici:
    def __init__(self, icerik=b"pdf", hata=None) -> None:
        self.icerik, self.hata, self.cagrilar = icerik, hata, []

    def medya_indir(self, medya_kimligi: str) -> bytes:
        self.cagrilar.append(medya_kimligi)
        if self.hata is not None:
            raise self.hata
        return self.icerik

    def metin_gonder(self, alici: str, metin: str):
        return None


_MEDYA_SATIRI = {"media_id": "MEDIA-1", "media_mime": "application/pdf"}


def test_COZUCU_NoOp_INDIRIYOR_ama_OKUMUYOR() -> None:
    """Bu turun çözücüsü DAİMA `None`; indirme yine de KOŞUYOR.

    Sıra tersine çevrilseydi ("çözücü kapalıysa indirme"), sağlayıcının
    gerçek ağ yolu yeniden ÖLÇÜLMEZ olurdu.
    """
    from app.whatsapp import fatura

    sag = _SahteSaglayici()
    assert fatura.medya_ozeti(sag, _MEDYA_SATIRI) == fatura.COZUCU_KAPALI_MESAJI
    assert sag.cagrilar == ["MEDIA-1"], "indirme KOŞMALI"
    assert isinstance(fatura.cozucu_al(), fatura.NoOpCozucu)


def test_INDIRME_HATASI_CAGIRANA_SIZMIYOR() -> None:
    """İstisna sızsaydı `service._mesaj_isle` satırı DEAD yapardı.

    İndirilemeyen bir fotoğraf ise kullanıcının tekrar gönderebileceği
    SIRADAN bir durumdur.
    """
    from app.whatsapp import fatura
    from app.whatsapp import saglayici as sag

    for hata in (sag.GonderimHatasi("x"), sag.KaliciGonderimHatasi("x"),
                 sag.KotaHatasi("x"), OSError("x")):
        cevap = fatura.medya_ozeti(_SahteSaglayici(hata=hata), _MEDYA_SATIRI)
        assert cevap == fatura.INDIRME_HATASI_MESAJI


def test_COZUCU_OKUYUNCA_OZET_URETILIYOR() -> None:
    """Model yolu geldiğinde değişmeyecek olan kısım: çözüm → özet."""
    from app.whatsapp import fatura

    class _Cozucu(fatura.BelgeCozucu):
        def oku(self, icerik, mime):
            assert icerik == b"pdf" and mime == "application/pdf"
            return {
                "tedarikci": "X Otomotiv",
                "genel_toplam": "54.000,00",
                "satirlar": [_satir(), _satir(eslesme_durumu="eslesmedi",
                                             eslesen_urun=None)],
            }

    cevap = fatura.medya_ozeti(_SahteSaglayici(), _MEDYA_SATIRI, cozucu=_Cozucu())
    assert "X Otomotiv" in cevap
    assert "Okunan kalem: 1" in cevap
    assert "1 kalem ATLANDI" in cevap
    assert fatura.SINIR_CUMLESI in cevap


# ---------------------------------------------------------------------------
# 5) "YAZMIYORUZ" — YAPISAL kapı
# ---------------------------------------------------------------------------


def test_FATURA_TASLAK_TURU_bu_turda_TANIMLI_DEGIL() -> None:
    """Kaynak testinin TERSİ. Kaynak türün kümede OLDUĞUNU çiviliyordu.

    Tür eklemek `ck_wpa_action_type` CHECK'ini değiştirmek, yani BİR GÖÇ
    demektir; bu dilim göçsüzdür. Kapalı küme ile CHECK'in birbirini
    tutması AYRI bir kapıda (`test_wa4_bekleyen.py`) ölçülüyor.
    """
    from app.whatsapp import fatura, schema

    assert schema.ISLEM_TURLERI == frozenset({schema.TAHSILAT})
    assert "FATURA_TASLAK" not in schema.ISLEM_TURLERI
    # Modül bir işlem türü sabiti TANIMLAMAMALI: tanımlamak, onu kümeye
    # eklemek isteyen bir sonraki eli davet ederdi.
    assert not hasattr(fatura, "ISLEM_TURU")


def test_FATURA_MODULU_VERITABANINA_DOKUNMUYOR() -> None:
    """"Yazmıyoruz" bir yorumla değil, YAZACAK NESNENİN YOKLUĞUYLA garanti.

    İki katman: (a) genel API'nin imzasında `db`/`Session` YOK,
    (b) modülün AST'inde yazma/sorgu çağrısı YOK.
    """
    import inspect

    from app.whatsapp import fatura

    imza = inspect.signature(fatura.medya_ozeti)
    assert "db" not in imza.parameters
    assert not any(
        "Session" in str(p.annotation) for p in imza.parameters.values()
    )

    kaynak = FATURA_KAYNAK.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)

    # Modül `sqlalchemy`yi ya da kardeş yazma modüllerini İÇE AKTARMAMALI.
    ice = {
        d.module for d in ast.walk(agac)
        if isinstance(d, ast.ImportFrom) and d.module
    } | {
        a.name for d in ast.walk(agac)
        if isinstance(d, ast.Import) for a in d.names
    }
    yasak = {"sqlalchemy", "sqlalchemy.orm", "bekleyen", ".bekleyen", "app.db"}
    assert not (ice & yasak), f"yazma yoluna açılan içe aktarma: {ice & yasak}"

    # Çağrı ADLARI: `execute`/`commit`/`insert`/`update`/`delete` HİÇ geçmez.
    adlar = {
        d.func.attr for d in ast.walk(agac)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    }
    assert not (adlar & {"execute", "commit", "add", "flush", "insert",
                         "update", "delete", "taslak_olustur"})


# ---------------------------------------------------------------------------
# 6) `.gitattributes` — satır sonu çivisi
# ---------------------------------------------------------------------------


def test_MD_SATIR_SONU_CIVISI_ve_FIXTURE_KAPSAMI() -> None:
    """İki satır da DURMALI: genel `*.md` ve fixture dizini.

    `docs/whatsapp/*.md` ayrıca yazılı çünkü o dosyalar yalnız okunmuyor,
    AYRIŞTIRILIYOR: `test_wa3_kopru.py` KOPRU_SOZLESMESI.md içindeki
    ```json bloğunu düzenli ifadeyle çıkarıp ÜÇ ALTIN VEKTÖRÜ oradan
    okuyor. Genel satır bugün onu zaten kapsıyor; ayrı satır, birinin
    `*.md`yi daralttığı gün fixture'ın kapsam DIŞINDA kalmamasını
    sağlıyor.
    """
    metin = GITATTRIBUTES.read_text(encoding="utf-8")
    satirlar = [s.split("#")[0].split() for s in metin.splitlines()]
    kurallar = {s[0]: s[1:] for s in satirlar if len(s) >= 2}

    assert kurallar.get("*.md") == ["text", "eol=lf"]
    assert kurallar.get("docs/whatsapp/*.md") == ["text", "eol=lf"]


def test_MD_CIVISI_GITTE_GERCEKTEN_UYGULANIYOR() -> None:
    """Dosyada yazması yetmez — git'in onu UYGULADIĞI ölçülüyor.

    `git check-attr` niteliği git'in kendi çözümleyicisinden okur; bir
    sonraki satırın kuralı gölgelemesi ya da dosyanın hiç okunmaması bu
    kapıya YAKALANIR.
    """
    hedef = "docs/whatsapp/KOPRU_SOZLESMESI.md"
    try:
        cikti = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", hedef],
            cwd=DEPO, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git yok")
    if cikti.returncode != 0:  # pragma: no cover
        pytest.skip("git check-attr calismadi")
    assert f"{hedef}: text: set" in cikti.stdout
    assert f"{hedef}: eol: lf" in cikti.stdout


# ---------------------------------------------------------------------------
# 7) DAVRANIŞ — gerçek şema: medya cevaplanıyor, HİÇBİR işlem yazılmıyor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def uygulama():
    """Gerçek şemalı uygulama; `TestClient` bağlamı göçleri koşturur."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))


@pytest.fixture()
def dunya(uygulama):
    """TEK firma + TEK üyelik: firma seçimi gerekmesin, kimlik çözülsün."""
    from sqlalchemy import text

    from app.db import SessionLocal

    def temizle(db):
        for tablo in (TABLO, "whatsapp_inbound", "whatsapp_links",
                      "whatsapp_context"):
            try:
                db.execute(text(f"DELETE FROM {tablo}"))
            except Exception:
                db.rollback()
        db.commit()

    # KOŞUYA ÖZGÜ son ek: `app_users`/`companies` TEMİZLENMİYOR (başka
    # dosyaların kurulumlarını silmemek için), dolayısıyla artan bir sayaç
    # ikinci koşuda `uq_app_users_email_lower`a çarpardı.
    n = uuid4().hex[:12]
    with SessionLocal() as db:
        temizle(db)
        an = datetime.now(timezone.utc)
        firma = db.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,1,:t) RETURNING id"),
            {"a": f"WA5 {n} A.S.", "t": an},
        ).scalar_one()
        kul = db.execute(
            text("INSERT INTO app_users(username,email,email_verified,"
                 "display_name,password_hash,role,is_active,"
                 "must_change_password,created_at)"
                 " VALUES(:k,:e,1,:k,'x','admin',1,0,:t) RETURNING id"),
            {"k": f"wa5-{n}", "e": f"wa5-{n}@wa5.invalid", "t": an},
        ).scalar_one()
        db.execute(
            text("INSERT INTO user_company_memberships"
                 "(user_id,company_id,is_default,created_at)"
                 " VALUES(:u,:c,1,:t)"),
            {"u": kul, "c": firma, "t": an},
        )
        db.execute(
            text("INSERT INTO whatsapp_links(company_id,user_id,phone,"
                 "is_active,created_at,updated_at)"
                 " VALUES(:c,:u,:p,1,:t,:t)"),
            {"c": firma, "u": kul, "p": NUMARA, "t": an},
        )
        db.commit()
    yield {"firma": int(firma), "kul": int(kul)}
    with SessionLocal() as db:
        temizle(db)


def test_DAVRANIS_MEDYA_MESAJI_HICBIR_ISLEM_YAZMIYOR(dunya, monkeypatch) -> None:
    """Gerçek şemada uçtan uca: medya satırı CEVAPLANIYOR, tablo BOŞ KALIYOR.

    Yapısal kapı (`test_FATURA_MODULU_VERITABANINA_DOKUNMUYOR`) modülü
    ölçüyor; bu kapı AKIŞI ölçüyor — biri yeşilken öteki kırmızı olabilir
    (ör. `service.py` özeti aldıktan SONRA kendisi bir satır yazsaydı).
    """
    from sqlalchemy import text

    from app.config import settings
    from app.db import SessionLocal
    from app.whatsapp import saglayici as sag
    from app.whatsapp import service

    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "BIZIM")

    with SessionLocal() as db:
        db.execute(
            text("INSERT INTO whatsapp_inbound(wamid,sender_phone,"
                 "phone_number_id,text,media_id,media_mime,status,"
                 "attempt_count,received_at)"
                 " VALUES(:w,:s,'BIZIM','','MEDIA-1','application/pdf',"
                 ":st,0,:t)"),
            {"w": f"wamid.wa5.{next(_SAYAC)}", "s": NUMARA,
             "st": service.schema.RECEIVED,
             "t": datetime.now(timezone.utc)},
        )
        db.commit()

    gonderici = sag.NoOpSaglayici()
    # ALTYAZISIZ fotoğraf: `text` BOŞ STRING (sütun NOT NULL ve
    # `cloud_api._medya_mesaji` altyazı yokken `""` yazıyor). Boş string
    # `not metin`i DOĞRU yapar; medya yükleminin düşmesi bu satırı
    # "boş mesaj" sayıp IGNORED ile kapatırdı ve fatura yolu HİÇ koşmazdı.
    with SessionLocal() as db:
        assert service.bekleyenleri_isle(db, saglayici=gonderici) == 1

    with SessionLocal() as db:
        durum = db.execute(
            text("SELECT status FROM whatsapp_inbound WHERE media_id='MEDIA-1'")
        ).scalar_one()
        islem_sayisi = db.execute(
            text(f"SELECT COUNT(*) FROM {TABLO}")
        ).scalar_one()

    assert durum == service.schema.ANSWERED, "medya artık CEVAPLANIYOR"
    assert int(islem_sayisi) == 0, "HİÇBİR bekleyen işlem YAZILMAMALI"

    # NoOp indirmeyi reddeder -> kullanıcı indirme hatası mesajı alır.
    # Ölçülen şey mesajın İÇERİĞİ değil, akışın istisna SIZDIRMADAN
    # terminal duruma ulaşmasıdır.
    (alici, cevap), = gonderici.gonderilenler
    assert alici == NUMARA and cevap
