"""Çiftçi niyet çözücüsü: mesaj → İKİ okuma aracından biri (F10-1b).

`niyet.py` PERSONELİN yedi aracını çözer; bu modül ÇİFTÇİNİN ikisini
(`ciftci_ekstre`, `ciftci_avans`) çözer. İkisi AYRI dosyada yaşar ve bu
ölçülmüş bir karardır — keşif
`docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §5.1: *"İki araç kümesi
ASLA aynı sözlüğü paylaşmaz."*

--- NEDEN AYRI MODÜL, `niyet.py`E İKİ KÖK DEĞİL ---------------------------

`niyet.coz` bir `Niyet` döndürür ve o nesne `niyet.dene` üzerinden
`ARAC_BEYAZ_LISTESI`ne karşı koşar. Çiftçi köklerini o tabloya eklemek,
tek bir kod yolunun hem personel hem çiftçi aracı üretebilmesi demekti;
o gün "hangi beyaz liste?" sorusunun cevabı ÇAĞRI BAĞLAMINA bağlı olurdu
ve bir mutasyon (yanlış listeyle çağırmak) hiçbir DAVRANIŞ testini
kırmazdı. Ayrı modül + ayrı frozenset, kapıyı TÜRE taşır.

--- SERBEST METİNDEN CARİ ADI ÇIKARILMAZ ---------------------------------

`niyet._terim_cikar` bu dosyada ÇAĞRILMAZ ve çağrılamaz (içe aktarılmıyor).
Gerekçe keşif §4: çiftçi YALNIZ kendi satırını görür ve hangi satır
olduğunu `TarafKimlik` söyler — mesaj DEĞİL. Bir terim çıkarımı, çiftçinin
yazdığı adın sorguya girebileceği tek kapıyı açardı.

--- `HESAP` TEK BAŞINA KÖK DEĞİLDİR --------------------------------------

Keşif §4a ölçtü: `niyet.SORU_SOZLUGU` "hesap" kelimesini DURAK olarak
taşıyor (`niyet.py:80`). Kök yapmak onu hem durak hem kök yapardı. Bu
yüzden `HESAP` yalnız `OZET` ile BİTİŞİK çiftte tanınır ("hesap özeti");
tek başına gelen "hesap" kapsam mesajına düşer.

--- AY ADI ÇÖZÜMÜ BURADA, `niyet._donem_bul`da DEĞİL ---------------------

`niyet._donem_bul` YALNIZ GÖRELİ etiketler üretir ("bu_ay", "gecen_ay") ve
ay adlarını BİLEREK tanımaz: `niyet.py:72-76` gerekçesini yazıyor — *"AY
ADLARI BİLEREK YOK: Nisan, Kasım, Ekim aynı zamanda kişi adıdır"*. O
gerekçe PERSONEL yolunda doğrudur çünkü orada bir CARİ ADI aranıyor ve ay
adı o adla karışırdı.

Çiftçi yolunda CARİ ADI ARANMIYOR (yukarıdaki bölüm), yani karışacak bir
şey YOK. Keşif §4a'nın cevap şablonu ise ay adını AÇIKÇA öneriyor
(*"Detay için 'EKSTRE EYLÜL' yazabilirsiniz"*). Bu yüzden ay çözümü
BURADA, on iki adlık kapalı bir sözlükle yapılır; `niyet`e dokunulmaz.

YIL SEÇİMİ DETERMİNİSTİK: adı geçen ay bu yıl HENÜZ BAŞLAMADIYSA ÖNCEKİ
yılın o ayıdır. "Mart'ta ARALIK yazan çiftçi geçen aralığı sorar" —
gelecekteki bir ayın ekstresi BOŞ dönerdi ve kullanıcı nedenini hiç
öğrenemezdi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .niyet import CARI_KOKLER, _kok_eslesir, tr_katla

# ---------------------------------------------------------------------------
# Kökler — katlanmış (BÜYÜK ASCII) biçimde; ek toleransı `_kok_eslesir`de
# ---------------------------------------------------------------------------

#: Ekstre/bakiye kökleri. `CARI_KOKLER` (BORC, BAKIYE, CARI, VERESIYE)
#: YENİDEN KULLANILIR — keşif §4a: kopya bir sözlük, personel tarafında
#: eklenen bir kökün çiftçi tarafında eksik kaldığı güne kadar sessiz
#: kalırdı.
EKSTRE_KOKLER: frozenset[str] = frozenset({"EKSTRE"}) | CARI_KOKLER

#: Avans kökleri (keşif §4b).
AVANS_KOKLER: frozenset[str] = frozenset({"AVANS", "KAPORA", "PESINAT"})

#: "HESAP ÖZETİ" — YALNIZ BİTİŞİK ÇİFT (başlık).
_HESAP_OZETI_CIFTI: tuple[str, str] = ("HESAP", "OZET")

# ---------------------------------------------------------------------------
# TAM EŞLEŞME KOMUTLARI — `eslestirme.bagla_ayristir`ın sınıfı
# ---------------------------------------------------------------------------
# Serbest cümleden ÇIKARILMAZ (keşif §5.4). "Durumu iptal etmek istemiyorum"
# cümlesi bir `İPTAL` komutu DEĞİLDİR; rızayı geri çeken bir komutun
# çıkarımla tetiklenmesi, kullanıcının vermediği bir kararı onun defterine
# yazmak olurdu. Türkçe "İ/ı" toleransı regex'ten ÖNCE uygulanır
# (`_turkce_duzelt`), `baglam._turkce_buyut` ile AYNI gerekçe.
_DUR_RE = re.compile(r"^\s*(DUR|IPTAL)\s*$", re.IGNORECASE)
_EVET_RE = re.compile(r"^\s*EVET\s*$", re.IGNORECASE)
_HAYIR_RE = re.compile(r"^\s*HAYIR\s*$", re.IGNORECASE)
#: Çok firmalı çiftçinin sıra öneki: "1 EKSTRE". Sıra 1 tabanlıdır ve
#: `taraf.taraf_coz`un DETERMİNİSTİK sırasıyla aynıdır.
_SIRA_ONEKI_RE = re.compile(r"^\s*(\d{1,2})\s*[).\-]?\s+(.*)$", re.DOTALL)
#: Çiftçi tarafının `FİRMA LİSTELE`si — aynı sözdizimi, AYRI gövde
#: (`baglam.firma_komutu` `eslestirme.kimlik_coz`a bakar, yani PERSONEL
#: adaylarını listeler ve çiftçiye HER ZAMAN boş döner).
_LISTELE_RE = re.compile(r"^\s*F[İI]RMA\s+L[İI]STELE\s*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Ay adları — KAPALI sözlük, on iki üye (başlık)
# ---------------------------------------------------------------------------
_AYLAR: dict[str, int] = {
    "OCAK": 1, "SUBAT": 2, "MART": 3, "NISAN": 4, "MAYIS": 5, "HAZIRAN": 6,
    "TEMMUZ": 7, "AGUSTOS": 8, "EYLUL": 9, "EKIM": 10, "KASIM": 11,
    "ARALIK": 12,
}

_KELIME = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü_-]+")


@dataclass(frozen=True, slots=True)
class CiftciNiyeti:
    """Çiftçi mesajının deterministik çözümü.

    ``arac`` doluysa `ciftci_yurutucu` onu koşturur; ``mesaj`` doluysa
    araç koşulmaz ve metin olduğu gibi kullanıcıya gider. `niyet.Niyet` ile
    AYNI sözleşme, AYRI tip: iki çözümün sonucu birbirinin yerine
    GEÇEMESİN.
    """

    arac: str | None = None
    argumanlar: dict[str, Any] = field(default_factory=dict)
    mesaj: str | None = None


# ---------------------------------------------------------------------------
# Kullanıcıya giden sabit metinler
# ---------------------------------------------------------------------------

#: Keşif §5.2'nin metni. `niyet.KAPSAM_MESAJI` çiftçiye YANLIŞ bir söz
#: verir ("stok", "tahsilat"): o yetenekler personelindir ve çiftçi onlara
#: HİÇBİR KOŞULDA erişemez.
CIFTCI_KAPSAM_MESAJI = (
    "Bu kanaldan şunları sorabilirsiniz: bakiye/ekstre, avans durumu, "
    "kantar fişi ve müstahsil makbuzu. Çıkmak için DUR yazın."
)


def kvkk_metni(firma_adi: str) -> str:
    """Rıza sorusu. Firmayı ADIYLA anar, ne gönderileceğini SAYAR, çıkışı yazar.

    TEK YER: metin bir sabit değil bir fonksiyondur çünkü firma adı
    taşıyor; iki ayrı yerde kurulsaydı biri güncellenip öteki kalırdı.
    """
    return (
        f"{firma_adi} adına bu numaraya bakiye, ekstre ve avans bilgilerinizi "
        "göndermemiz için onayınız gerekiyor (KVKK). Onaylıyorsanız EVET, "
        "istemiyorsanız HAYIR yazın. Dilediğiniz zaman DUR yazarak "
        "durdurabilirsiniz."
    )


RIZA_ALINDI_MESAJI = (
    "Onayınız alındı. Artık bakiye, ekstre ve avans bilgilerinizi "
    "sorabilirsiniz. Çıkmak için DUR yazın."
)
RIZA_REDDEDILDI_MESAJI = (
    "Anlaşıldı, bu numaraya bilgi göndermeyeceğiz. Fikrinizi değiştirirseniz "
    "EVET yazmanız yeterli."
)
#: `REVOKED` / `RECIPIENT_CHANGED` / `RECIPIENT_INVALID` — ÜÇÜ DE AYNI
#: metni alır. Ayırt edilemezlik `eslestirme.RED_MESAJI`nin sınıfındadır:
#: farklı metin, dışarıdaki birine defterin içini anlatırdı.
RIZA_KAPALI_MESAJI = (
    "Bu numaraya bilgi gönderemiyoruz. Bilgi almak isterseniz EVET yazın "
    "ya da alım merkezinizle görüşün."
)
DUR_MESAJI = (
    "Kaydınız kapatıldı ve bu numaraya bilgi göndermeyeceğiz. "
    "Yeniden bağlanmak için alım merkezinizden eşleştirme kodu isteyin."
)


def firma_secin_mesaji(adlar: list[str]) -> str:
    """Çok firmalı çiftçiye sorulan seçim. RASTGELE SEÇİM YOKTUR.

    `baglam.FIRMA_SECIN_MESAJI` ile AYNI sözleşme, AYRI gövde ve AYRI
    sözdizimi — ve bu fark ÖLÇÜLMÜŞ bir zorunluluktur, tercih değil:

    `FİRMA SEÇ <n>` seçimi `whatsapp_context`e yazar ve o tablonun
    `user_id` sütunu NOT NULL'dur, üstelik `app_users.id`ye GERÇEK bir
    yabancı anahtarla bağlıdır (göç `20260910_0079`). Çiftçinin bir
    `app_users` satırı YOKTUR — keşif §5.5 bunu aktör sorunu olarak zaten
    ölçmüştü. Yani o tablo çiftçi seçimini TAŞIYAMAZ.

    İkinci bir "taraf bağlamı" tablosu açmak ikinci bir göç demekti ve
    keşif §6.2 bu PR'a TEK göç veriyor (`whatsapp_message_attempts`).

    Seçilen yol DURUMSUZDUR: çiftçi sorusunun BAŞINA firma numarasını
    yazar ("1 EKSTRE"). Sıra `taraf.taraf_coz`un determinist sırasıdır.
    Durumsuz olmasının iki yan kazancı var: süresi dolan bir seçimin
    yarattığı belirsizlik YOK, ve "hangi firmadayım" sorusunun bağlantı
    defterinden BAŞKA bir cevabı olabileceği ikinci bir yer YOK.
    """
    satirlar = [f"{i}. {ad}" for i, ad in enumerate(adlar, start=1)]
    return (
        "Kaydınız birden çok firmada bulunuyor:\n"
        + "\n".join(satirlar)
        + "\nSorunuzun başına firma numarasını yazın — örn. \"1 EKSTRE\"."
    )


# ---------------------------------------------------------------------------
# Ayrıştırma
# ---------------------------------------------------------------------------


def _turkce_duzelt(metin: str) -> str:
    """Türkçe "İ/ı"yı regex'ten ÖNCE düzler (`baglam._turkce_buyut` gerekçesi)."""
    return (metin or "").replace("İ", "I").replace("ı", "i")


def dur_mu(metin: str) -> bool:
    """``DUR`` / ``İPTAL`` — TAM eşleşme (başlık)."""
    return _DUR_RE.match(_turkce_duzelt(metin)) is not None


def evet_mi(metin: str) -> bool:
    return _EVET_RE.match(_turkce_duzelt(metin)) is not None


def hayir_mi(metin: str) -> bool:
    return _HAYIR_RE.match(_turkce_duzelt(metin)) is not None


def listele_mi(metin: str) -> bool:
    return _LISTELE_RE.match(_turkce_duzelt(metin)) is not None


def sira_oneki_ayir(metin: str) -> tuple[int | None, str]:
    """``"1 EKSTRE"`` → ``(1, "EKSTRE")``; önek yoksa ``(None, metin)``.

    Önek YALNIZ baştaki 1-2 basamaklı sayıdır ve ardından EN AZ BİR boşluk
    gelmek zorundadır: "2024 ekstre" bir sıra öneki DEĞİLDİR (dört
    basamak), "1EKSTRE" de değildir (boşluk yok). Dar tutmak bilinçli —
    geniş bir desen, sayı içeren normal bir soruyu sessizce başka firmaya
    yönlendirebilirdi.
    """
    esles = _SIRA_ONEKI_RE.match(metin or "")
    if not esles:
        return None, metin or ""
    return int(esles.group(1)), esles.group(2)


def _jetonlar(metin: str) -> list[str]:
    """Katlanmış (BÜYÜK ASCII) kelimeler. `niyet._jetonla`nın sadeleşmişi.

    Ham parça SAKLANMAZ çünkü bu dalda ham kelimeye ihtiyaç duyan tek şey
    terim çıkarımıydı ve o YOK (başlık).
    """
    return [
        tr_katla(esles.group(0).strip("-_"))
        for esles in _KELIME.finditer(metin or "")
        if esles.group(0).strip("-_")
    ]


def ay_araligi(metin: str, bugun: date) -> tuple[date, date] | None:
    """Mesajda AÇIKÇA yazılı ay adı → o ayın (ilk gün, son gün); yoksa ``None``.

    Yıl seçimi başlıkta: adı geçen ay bu yıl HENÜZ BAŞLAMADIYSA önceki yıl.
    """
    for jeton in _jetonlar(metin):
        ay = _AYLAR.get(jeton)
        if ay is None:
            continue
        yil = bugun.year if ay <= bugun.month else bugun.year - 1
        ilk = date(yil, ay, 1)
        sonraki = date(yil + 1, 1, 1) if ay == 12 else date(yil, ay + 1, 1)
        return ilk, date.fromordinal(sonraki.toordinal() - 1)
    return None


def _hesap_ozeti(jetonlar: list[str]) -> bool:
    """BİTİŞİK "HESAP ÖZET*" çifti (başlık: `HESAP` tek başına kök DEĞİL)."""
    ilk, ikinci = _HESAP_OZETI_CIFTI
    for i in range(len(jetonlar) - 1):
        if jetonlar[i] == ilk and _kok_eslesir(jetonlar[i + 1], frozenset({ikinci})):
            return True
    return False


def coz(metin: str, *, bugun: date) -> CiftciNiyeti:
    """Mesajı İKİ araçtan birine ya da bir MESAJA çevirir. Fail-closed.

    Taraf tipi BURAYA GİRMEZ: hangi aracın hangi tarafta anlamlı olduğu
    bir YÜRÜTME sorusudur ve cevabı `ciftci_yurutucu`dadır. Burada
    sorulsaydı `CUSTOMER` tarafının "avans" sorusu kapsam mesajına düşerdi
    ve kapsam mesajı ile "kaydınız yok" cevabı AYIRT EDİLEBİLİR olurdu —
    yani taraf tipi sızardı (keşif §4b).
    """
    jetonlar = _jetonlar(metin)
    if not jetonlar:
        return CiftciNiyeti(mesaj=CIFTCI_KAPSAM_MESAJI)

    # SIRA SÖZLEŞMEDİR: avans ekstreden ÖNCE denenir. "avans bakiyem" hem
    # AVANS hem BAKIYE kökü taşır; çiftçinin sorduğu şey avanstır ve
    # ekstre cevabı o soruyu sessizce yutardı.
    if any(_kok_eslesir(jeton, AVANS_KOKLER) for jeton in jetonlar):
        return CiftciNiyeti(arac="ciftci_avans", argumanlar={})

    if any(_kok_eslesir(jeton, EKSTRE_KOKLER) for jeton in jetonlar) or _hesap_ozeti(
        jetonlar
    ):
        aralik = ay_araligi(metin, bugun)
        argumanlar: dict[str, Any] = {}
        if aralik is not None:
            argumanlar = {"date_from": aralik[0], "date_to": aralik[1]}
        return CiftciNiyeti(arac="ciftci_ekstre", argumanlar=argumanlar)

    return CiftciNiyeti(mesaj=CIFTCI_KAPSAM_MESAJI)


__all__ = [
    "AVANS_KOKLER",
    "CIFTCI_KAPSAM_MESAJI",
    "CiftciNiyeti",
    "DUR_MESAJI",
    "EKSTRE_KOKLER",
    "RIZA_ALINDI_MESAJI",
    "RIZA_KAPALI_MESAJI",
    "RIZA_REDDEDILDI_MESAJI",
    "ay_araligi",
    "coz",
    "dur_mu",
    "evet_mi",
    "firma_secin_mesaji",
    "hayir_mi",
    "kvkk_metni",
    "listele_mi",
    "sira_oneki_ayir",
]
