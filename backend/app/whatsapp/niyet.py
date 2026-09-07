"""WhatsApp Faz 1: deterministik niyet çözümü ve şablonlu cevap — SAF KOD.

MUTLAK GİZLİLİK SÖZLEŞMESİ: WhatsApp kullanıcısının orijinal mesajı HİÇBİR
dış LLM çağrısına girmez — maskeli bile girmez, çünkü Faz 1'de LLM çağrısı
YOKTUR. Üç okuma aracı (cari_durum, parca_stok, donem_ozeti) dar bir alandır:
niyet anahtar kelimeyle, arama terimi eleme yöntemiyle, cevap şablonla
uygulama tarafında üretilir. Belirsiz niyet modele SORULMAZ; güvenli kapsam
mesajı döner (fail-closed).

Bedeli dürüstçe: model esnekliği yok. "yağ filtresi" tek başına anlaşılmaz;
"yağ filtresi stok" anlaşılır. Karşılığı, kişi adının ("Şabo", "Su", "Al",
"Ahmet2", "Toplam") hangi yazımla olursa olsun dışarı SIZAMAMASIDIR — çünkü
gidecek bir dış istek yoktur.

TAŞIMA NOTU (WA3-core)
----------------------
Kaynak: ``nazgul_website/backend/app/whatsapp/niyet.py`` (830 satır).
ÖLÇÜLDÜ: kaynak modül veritabanına DOKUNMUYOR — ``sqlalchemy`` ya da
``Session`` importu yok; tek dış bağımlılığı ``assistant.masking``
(``SORU_SOZLUGU``, ``_EK_TOLERANSI``, ``_YUMUSAMA``, ``_kok_eslesir``) ve
``assistant.tools.tr_katla`` idi. Bu depoda ``assistant`` paketi YOKTUR;
o beş sembol aşağıya BİREBİR taşındı ("Metin katlama" bölümü).

Kaynakta niyeti KOŞTURAN döngü (``service.py::arac_kos``, deneme
argümanlarını sırayla dener) DB'ye bağlıydı ve taşınmadı; yalnız döngünün
kendisi :func:`dene` olarak, koşturucuyu :class:`NiyetYurutucu` arayüzüyle
dışarıdan alacak biçimde kaldırıldı. :class:`NoOpYurutucu` hiçbir şey
bilmez ve hep boş döner — bu PR'da HİÇBİR koşturucu bağlanmadı.

Bu modül ``app.config``i, ``app.db``yi ya da başka bir uygulama modülünü
İÇE AKTARMAZ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Metin katlama — kaynak: assistant/tools.py (tr_katla) ve
# assistant/masking.py (SORU_SOZLUGU, _EK_TOLERANSI, _YUMUSAMA, _kok_eslesir)
# ---------------------------------------------------------------------------

# Türkçe harfler ASCII karşılığına indirilir, sonra büyütülür. "İ"->"I" ve
# "ı"->"i" ÇİFTİ bilerek ayrı: "i".upper() == "I" ama "İ".upper() "İ" kalır
# ve "ı".upper() == "I" — noktalı/noktasız ayrımı katlamadan ÖNCE düzlenir.
_TR_ESLESME = (
    ("İ", "I"), ("ı", "i"), ("Ş", "S"), ("ş", "s"), ("Ğ", "G"), ("ğ", "g"),
    ("Ü", "U"), ("ü", "u"), ("Ö", "O"), ("ö", "o"), ("Ç", "C"), ("ç", "c"),
)


def tr_katla(metin: str) -> str:
    """Türkçe harfleri ASCII'ye indirip büyük harfe çevirir (arama biçimi)."""
    for kaynak, hedef in _TR_ESLESME:
        metin = metin.replace(kaynak, hedef)
    return metin.upper()


# Sözlükte olmayan kelime bir ÜRÜN adı da olabilir, bir KİŞİ adı da — ayrımı
# tahminle yapmıyoruz (NER/LLM yasak). Sözlük okunaklılık için küçük harf
# yazılır; karşılaştırma tr_katla'nın ürettiği BÜYÜK/ASCII biçimde yapılır.
_SORU_SOZLUGU_HAM: frozenset[str] = frozenset({
    # soru kalıpları
    "ne", "nedir", "kac", "kaca", "kacar", "hangi", "hangisi", "nasil",
    "kim", "kimin", "kime", "neden", "nerede", "nereye",
    "mi", "mu", "midir", "var", "yok", "kadar", "misin", "lutfen", "acaba",
    "goster", "soyle", "ver", "bak", "listele", "getir", "bul", "ara",
    # zaman / dönem — AY ADLARI BİLEREK YOK: Nisan, Kasım, Ekim aynı zamanda
    # kişi adıdır ve dönem parametresi zaten yalnız "bu_ay/geçen yıl" gibi
    # göreli etiketleri tanır; ay adının açık geçmesi hiçbir sorguya
    # yaramaz ama adı sızdırırdı.
    "bu", "gecen", "onceki", "son", "ay", "ayki", "hafta", "yil", "yilki",
    "gun", "bugun", "dun", "sezon", "donem", "ceyrek", "toplam",
    # finans / cari
    "borc", "borcu", "alacak", "bakiye", "cari", "ciro", "tahsilat",
    "odeme", "vade", "vadesi", "gecmis", "geciken", "tutar", "para",
    "lira", "kurus", "fatura", "hesap", "musteri", "tedarikci", "kalan",
    # stok / parça
    "stok", "adet", "tane", "urun", "parca", "kod", "kodu", "numara",
    "numarasi", "raf", "depo", "kritik", "seviye", "mevcut", "kaldi",
    "bitiyor", "tukendi", "envanter", "barkod", "marka", "model",
    "fiyat", "liste", "birim", "kdv", "iskonto", "rapor", "ozet",
    "durum", "detay", "bilgi", "tarih",
    # zamir / bağlaç / yardımcı
    "ben", "benim", "biz", "bizim", "sen", "senin", "onun", "bunun",
    "sunun", "ve", "ile", "icin", "ama", "veya", "de", "da", "ki", "en",
    "cok", "az", "daha", "diger", "ayni", "ilk", "ikinci", "ucuncu",
    "birinci", "sonuncu", "once", "sonra", "simdi", "yeni", "eski",
    # yaygın fiil kökleri
    "sat", "satis", "satti", "sattim", "al", "alis", "aldi", "aldim",
    "gel", "geldi", "git", "gitti", "kazan", "harca", "ode", "odedi",
    "yap", "yapti", "et", "olan", "olarak", "oldu", "olur", "ise",
})

SORU_SOZLUGU: frozenset[str] = frozenset(k.upper() for k in _SORU_SOZLUGU_HAM)

# Ek toleransı: kökten sonra en çok bu kadar harf ("stok" → "stogundan").
_EK_TOLERANSI = 6

# Türkçe ünsüz yumuşaması, katlanmış (BÜYÜK ASCII) uzayda: "stok" → "stoğu"
# katlanınca STOK → STOGU olur; gövde STOG'un son harfi sertleştirilip STOK
# denenir. Eşleşme kümesini yalnız BÜYÜTEN, belirlenimci bir dönüşümdür.
_YUMUSAMA = {"G": "K", "B": "P", "D": "T"}


def _kok_eslesir(
    kelime: str, kokler: frozenset[str] | set[str], ek_toleransi: int = _EK_TOLERANSI
) -> bool:
    if kelime in kokler:
        return True
    # Ek toleransı: en uzun aday kökten başla; kök ≥3 harf olmalı ki "de"
    # köküne her kelime yaslanamasın.
    for uzunluk in range(len(kelime) - 1, 2, -1):
        if len(kelime) - uzunluk > ek_toleransi:
            continue
        govde = kelime[:uzunluk]
        if govde in kokler:
            return True
        sert = _YUMUSAMA.get(govde[-1])
        if sert is not None and govde[:-1] + sert in kokler:
            return True
    return False


# ---------------------------------------------------------------------------
# Niyet tablosu — katlanmış (BÜYÜK ASCII) kökler; ek toleransı _kok_eslesir'de
# ---------------------------------------------------------------------------

CARI_KOKLER: frozenset[str] = frozenset({"BORC", "BAKIYE", "CARI", "VERESIYE"})
STOK_KOKLER: frozenset[str] = frozenset({
    "STOK", "URUN", "PARCA", "ADET", "TANE", "KOD", "NUMARA",
    "OEM", "BARKOD", "ENVANTER", "RAF",
})
DONEM_KOKLER: frozenset[str] = frozenset({"CIRO", "TAHSILAT", "DONEM", "OZET"})

# Aşağıdaki üç grup, yukarıdakilerle KELİME PAYLAŞTIĞI için ayrı tutulur ve
# genel grup ayrımından ÖNCE denenir. "kritik stok" içinde STOK, "en çok
# satılan ürün" içinde ÜRÜN, "kaç adet satılmış" içinde ADET geçiyor; genel
# ayrıma bırakılsalar stok aramasına düşerlerdi.
# Kökler bilerek uzun ve sayılı: ek toleransı 6 olduğu için "SAT" gibi kısa
# bir kök "satın" (alış!) dâhil çok şeyi yutardı. Her çekim ayrı yazılır.
SATIS_KOKLER: frozenset[str] = frozenset({
    "SATIS", "SATIL", "SATTI", "SATIM", "SATAN",
})
ALACAK_KOKLER: frozenset[str] = frozenset({"ALACAK", "VADE", "GECIKMIS"})

# Emir kipleri: "kritik stoğu 5 YAP" bir YAZMA denemesidir, sorgu değil.
# Bu kanalda tek yazma yolu tahsilattır ve onun kendi onay akışı vardır;
# başka hiçbir emir araca ULAŞMAMALI. Kelime varsa dar niyetler devreden
# çıkar ve mesaj genel akışta kapsam mesajına düşer.
YAZMA_EMRI_KOKLERI: frozenset[str] = frozenset({
    "YAPSA", "AYARLA", "GUNCELLE", "DEGISTIR", "SIL", "EKLE", "KAYDET",
    "OLUSTUR", "AZALT", "ARTIR", "DUZELT", "TANIMLA", "GIRIS",
})
KRITIK_KOKLER: frozenset[str] = frozenset({
    "KRITIK", "AZALAN", "AZALI", "TUKENEN", "TUKENI", "BITEN", "BITI",
})

_TUM_NIYET_KOKLERI = (
    CARI_KOKLER | STOK_KOKLER | DONEM_KOKLER
    | SATIS_KOKLER | ALACAK_KOKLER | KRITIK_KOKLER
)

# Dönem etiketleri; ikili (bigram) eşleşme jeton bazlıdır ("bu ayki" → AYKI
# kökü AY'a yaslanır).
_DONEM_IKILI: tuple[tuple[str, str, str], ...] = (
    ("GECEN", "AY", "gecen_ay"),
    ("GECEN", "YIL", "gecen_yil"),
    ("ONCEKI", "AY", "gecen_ay"),
    ("BU", "HAFTA", "bu_hafta"),
    ("BU", "CEYREK", "bu_ceyrek"),
    ("BU", "YIL", "bu_yil"),
    ("BU", "AY", "bu_ay"),
    ("SON", "7", "son_7_gun"),
    ("SON", "30", "son_30_gun"),
    ("SON", "90", "son_90_gun"),
)
_DONEM_TEKLI: tuple[tuple[str, str], ...] = (("BUGUN", "bugun"), ("DUN", "dun"))

_KELIME = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü_-]+")
_APOSTROF = ("'", "’", "ʼ", "`")

# Türkçe ad/hal eki kırpma — yalnız İKİNCİ deneme içindir: ilk arama
# kullanıcının yazdığı terimle koşar; boş dönerse ekler kırpılıp BİR kez
# daha denenir ("filtresinden" → FILTRE, "Korkmazın" → KORKMAZ). LIKE
# araması alt dize eşlediği için kökün bir-iki harf kısa kalması zararsızdır.
# Tek sesli harf ekleri ("A", "I") bilerek YOK: "HAVA"nın son harfi ek
# değildir; kırpmak kökü bozar. İki+ harfli ekler güvenlidir.
_EKLER: tuple[str, ...] = (
    "SINDEN", "SINDAN", "SUNDEN", "SUNDAN", "INDEN", "INDAN", "UNDEN", "UNDAN",
    "SINE", "SINA", "SUNE", "SUNA", "INDE", "INDA", "UNDE", "UNDA",
    "NDEN", "NDAN", "DEN", "DAN", "TEN", "TAN", "NIN", "NUN",
    "IN", "UN", "SI", "SU", "YI", "YU", "YE", "YA",
)
_EK_KIRP_ASGARI_KOK = 3


@dataclass(frozen=True, slots=True)
class Niyet:
    """Deterministik çözümün sonucu.

    * ``arac`` doluysa ``deneme_argumanlari`` sırayla denenir (ilk eleman
      kullanıcının yazdığı terim, sonraki ek-kırpılmış yedek).
    * ``mesaj`` doluysa araç koşulmaz; mesaj olduğu gibi kullanıcıya gider
      (eksik terim rehberliği ya da kapsam mesajı).
    """

    arac: str | None = None
    deneme_argumanlari: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    mesaj: str | None = None


KAPSAM_MESAJI = (
    "Bu soruyu bu kanaldan yanıtlayamıyorum. Şunları sorabilirsiniz: "
    "cari bakiye (örn. \"Şaban Korkmaz borç\"), stok "
    "(örn. \"84993120 stok\" ya da \"yağ filtresi stok\"), dönem özeti "
    "(örn. \"bu ay ciro\") ve tahsilat "
    "(örn. \"Şaban Korkmaz 20.000 nakit tahsilat\")."
)

# Mesajda tutar var ama hiçbir niyet kökü tanınmadı. Genel kapsam mesajı
# burada yetersiz kalıyordu: kullanıcı bir tutar yazdığına göre büyük
# olasılıkla tahsilat girmek istiyor, ama doğru kalıbı öğrenmesinin yolu
# yoktu. Tanınan tutar geri okunur ki yazım biçimi de doğrulanmış olsun.
def tutarli_rehber(tutar_metni: str) -> str:
    return (
        f"{tutar_metni} TL anladım ama ne yapmak istediğinizi anlamadım. "
        "Tahsilat için müşteri, tutar ve yöntemi birlikte yazın — örn. "
        f"\"Şaban Korkmaz {tutar_metni} nakit tahsilat\"."
    )


def _jetonla(metin: str) -> list[tuple[str, str]]:
    """(ham, katlanmış) çiftleri; ham parça apostrof öncesinde kesilir."""
    ciftler: list[tuple[str, str]] = []
    for kesit in metin.split():
        for ap in _APOSTROF:
            if ap in kesit:
                kesit = kesit.split(ap, 1)[0]
        for esles in _KELIME.finditer(kesit):
            ham = esles.group(0).strip("-_")
            if ham:
                ciftler.append((ham, tr_katla(ham)))
    return ciftler


def _grup_eslesir(katli: str, kokler: frozenset[str]) -> bool:
    return _kok_eslesir(katli, kokler)


def _ek_kirp(katli: str) -> str:
    for ek in _EKLER:
        govde = katli[: -len(ek)] if katli.endswith(ek) else None
        if govde and len(govde) >= _EK_KIRP_ASGARI_KOK:
            return govde
    return katli


def _terim_cikar(ciftler: list[tuple[str, str]]) -> tuple[str, str]:
    """(kullanıcının yazdığı terim, ek-kırpılmış yedek terim) döndürür.

    Genel soru sözlüğüne ya da herhangi bir niyet grubuna yaslanan jetonlar
    elenir; kalan, arama terimidir. İçinde rakam olan jetonlar (kod/barkod)
    elenmez.
    """
    ham_parcalar: list[str] = []
    yedek_parcalar: list[str] = []
    for ham, katli in ciftler:
        if len(katli) <= 1:
            continue
        if not any(ch.isdigit() for ch in katli):
            if _kok_eslesir(katli, SORU_SOZLUGU) or _grup_eslesir(katli, _TUM_NIYET_KOKLERI):
                continue
        ham_parcalar.append(ham)
        yedek_parcalar.append(_ek_kirp(katli) if not any(ch.isdigit() for ch in katli) else katli)
    return " ".join(ham_parcalar), " ".join(yedek_parcalar)


def _donem_acik(ciftler: list[tuple[str, str]]) -> str | None:
    """Mesajda AÇIKÇA yazılmış dönem etiketi; yoksa ``None``.

    Varsayılanı çağıran seçer: dönem özeti için "bu ay" doğru, satış geçmişi
    için "bu yıl" doğru. Tek bir varsayılan ikisine birden uymuyordu.
    """
    katlilar = [k for _, k in ciftler]
    for ilk, ikinci, etiket in _DONEM_IKILI:
        for i in range(len(katlilar) - 1):
            if katlilar[i] == ilk and (
                katlilar[i + 1] == ikinci or _kok_eslesir(katlilar[i + 1], frozenset({ikinci}))
            ):
                return etiket
    for tek, etiket in _DONEM_TEKLI:
        if any(_kok_eslesir(k, frozenset({tek})) for k in katlilar):
            return etiket
    return None


def _donem_bul(ciftler: list[tuple[str, str]]) -> str:
    return _donem_acik(ciftler) or "bu_ay"


def _ozel_niyet(ciftler: list[tuple[str, str]]) -> Niyet | None:
    """Genel grup ayrımından ÖNCE denenen dar niyetler.

    Hepsi OKUMA. Buradaki sıra rastgele değil: her kural, kendinden sonra
    gelenlerin kelimelerini de içerebilecek cümleleri önce yakalar.
    """
    katlilar = [k for _, k in ciftler]

    def var(kokler: frozenset[str]) -> bool:
        return any(_grup_eslesir(k, kokler) for k in katlilar)

    # 0) Emir kipi varsa hiçbir dar niyet çalışmaz. "kritik stoğu 5 yap"
    # sorgu değil yazma denemesidir; kelime örtüşmesine bakıp araca
    # göndermek, kullanıcıya yapmadığı işi yapılmış gibi gösterir.
    # "yap" tek başına çok geniş ("ne yapmalıyım"), o yüzden çekimli.
    if any(k == "YAP" or _grup_eslesir(k, YAZMA_EMRI_KOKLERI) for k in katlilar):
        return None

    # 1) Alacak yaşlandırma — "kim borçlu", "vadesi geçen alacaklar".
    # CARI_KOKLER'den önce gelir: "alacak" cümlesinde "borç" da geçebilir ve
    # o zaman istenen tek müşteri değil, listenin tamamıdır.
    if var(ALACAK_KOKLER):
        return Niyet(arac="alacak_yaslandirma", deneme_argumanlari=({},))

    # 2) Kritik stok — "neyin stoğu bitiyor", "ne sipariş etmeliyim".
    if var(KRITIK_KOKLER):
        return Niyet(arac="kritik_stok", deneme_argumanlari=({},))

    if not var(SATIS_KOKLER):
        return None

    # 3) "en çok satan" — parça terimi ARANMAZ, sıralama istenir.
    en_cok = any(
        katlilar[i] == "EN" and _kok_eslesir(katlilar[i + 1], frozenset({"COK"}))
        for i in range(len(katlilar) - 1)
    )
    if en_cok:
        return Niyet(
            arac="en_cok_satan_parcalar",
            deneme_argumanlari=({"donem": _donem_acik(ciftler) or "bu_ay"},),
        )

    # 4) Müşteri bağlamı varsa satış sorusu cariye aittir; genel akış çözsün.
    if var(CARI_KOKLER):
        return None

    # 5) Belirli bir parçanın satış geçmişi — "bıçak kaç adet satılmış".
    terim, yedek = _terim_cikar(ciftler)
    donem = _donem_acik(ciftler) or "bu_yil"
    if len(terim) >= 2:
        denemeler: list[dict[str, Any]] = [{"arama": terim, "donem": donem}]
        if yedek and yedek != tr_katla(terim):
            denemeler.append({"arama": yedek, "donem": donem})
        return Niyet(arac="parca_satis_gecmisi", deneme_argumanlari=tuple(denemeler))

    # 6) Terimsiz satış sorusu firma geneli demektir ("bu ay satış ne kadar").
    return Niyet(
        arac="donem_ozeti",
        deneme_argumanlari=({"donem": _donem_acik(ciftler) or "bu_ay"},),
    )


def coz(metin: str) -> Niyet:
    """Mesajı deterministik niyete çevirir; belirsizlikte fail-closed."""
    ciftler = _jetonla(metin or "")
    if not ciftler:
        return Niyet(mesaj=KAPSAM_MESAJI)

    ozel = _ozel_niyet(ciftler)
    if ozel is not None:
        return ozel

    gruplar = set()
    for _, katli in ciftler:
        if _grup_eslesir(katli, CARI_KOKLER):
            gruplar.add("cari")
        if _grup_eslesir(katli, STOK_KOKLER):
            gruplar.add("stok")
        if _grup_eslesir(katli, DONEM_KOKLER):
            gruplar.add("donem")

    if len(gruplar) != 1:
        # Hiç niyet ya da çapraz niyet: modele SORULMAZ, güvenli mesaj döner.
        # Tek istisna rehberliktir: mesajda geçerli TEK bir tutar varsa
        # kullanıcı büyük olasılıkla tahsilat girmeye çalışıyordur ve genel
        # kapsam mesajı doğru kalıbı öğretmez. Çapraz niyette bu tahmin
        # yürütülmez — orada asıl sorun tutar değil, belirsizliktir.
        if not gruplar:
            tutar, hata, _ = tutar_ayikla(metin or "")
            if hata is None and tutar is not None:
                return Niyet(mesaj=tutarli_rehber(tutar_yaz(tutar)))
        return Niyet(mesaj=KAPSAM_MESAJI)

    grup = gruplar.pop()
    terim, yedek = _terim_cikar(ciftler)

    if grup == "cari":
        if len(terim) < 3:
            return Niyet(mesaj=(
                "Hangi müşteri? Adını yazmanız yeterli — örn. "
                "\"Şaban Korkmaz borç\"."
            ))
        denemeler: list[dict[str, Any]] = [{"musteri": terim}]
        if yedek and yedek != tr_katla(terim):
            denemeler.append({"musteri": yedek})
        return Niyet(arac="cari_durum", deneme_argumanlari=tuple(denemeler))

    if grup == "stok":
        if len(terim) < 2:
            return Niyet(mesaj=(
                "Hangi parça? Parça adı ya da numarası yazın — örn. "
                "\"84993120 stok\"."
            ))
        denemeler = [{"arama": terim}]
        if yedek and yedek != tr_katla(terim):
            denemeler.append({"arama": yedek})
        return Niyet(arac="parca_stok", deneme_argumanlari=tuple(denemeler))

    # donem: araç terim almaz. Elenmemiş yabancı kelime kaldıysa soru büyük
    # olasılıkla firma özetinden fazlasını istiyor ("Şaban'ın cirosu") —
    # yanlış veriyle cevaplamak yerine kapsam mesajı döner. "son 30 gün"
    # gibi dönem kalıbının rakamları kalıntı sayılmaz.
    kalinti = [
        p for p in terim.split() if not (p.isdigit() and p in {"7", "30", "90"})
    ]
    if kalinti:
        return Niyet(mesaj=KAPSAM_MESAJI)
    return Niyet(arac="donem_ozeti", deneme_argumanlari=({"donem": _donem_bul(ciftler)},))


def sonuc_bos_mu(arac: str, veri: dict[str, Any]) -> bool:
    if arac == "cari_durum":
        return veri.get("bulunan") == 0
    if arac == "parca_stok":
        return veri.get("bulunan_kayit_sayisi") == 0
    if arac == "parca_satis_gecmisi":
        # Boş sonuçta ek-kırpılmış yedek terim denensin ("bıçağı" → BICAK).
        return not veri.get("satilan_adet")
    return False


# ---------------------------------------------------------------------------
# Koşturma dikişi — kaynak: service.py::ARAC_BEYAZ_LISTESI (birebir) ve
# service.py::arac_kos'un deneme döngüsü (koşturucu dışarı alınarak)
# ---------------------------------------------------------------------------

# Kanaldan çağrılabilecek araçlar. HEPSİ OKUMADIR: yazma yolu (tahsilat)
# buradan geçmez, kendi onay akışı vardır. Yeni araç eklerken niyet.cevap_yaz
# içinde şablonu da yazılmalı, yoksa KeyError'a düşer.
ARAC_BEYAZ_LISTESI: frozenset[str] = frozenset({
    "cari_durum",
    "parca_stok",
    "donem_ozeti",
    "alacak_yaslandirma",
    "kritik_stok",
    "en_cok_satan_parcalar",
    "parca_satis_gecmisi",
})


class NiyetYurutucu(Protocol):
    """Bir aracı verilen argümanlarla koşturur ve ham sonucu döner.

    Bu PR'da tek gerçekleştirim :class:`NoOpYurutucu`. Veritabanına bakan
    gerçekleştirim sonraki dilimin işi; arayüz şimdiden burada ki
    :func:`dene` döngüsü ve şablonlar ona karşı ölçülebilsin.
    """

    def kos(self, arac: str, argumanlar: dict[str, Any]) -> dict[str, Any]: ...


#: :func:`sonuc_bos_mu`'nun HER araç için "boş" saydığı biçim. ``{}`` boş
#: DEĞİLDİR: kaynak ``veri.get("bulunan_kayit_sayisi") == 0`` diye sorar ve
#: eksik anahtar ``None == 0`` → False verir — yani ``{}`` "dolu" okunur ve
#: deneme döngüsü ilk adımda dururdu (ÖLÇÜLDÜ, bu yüzden açık yazıldı).
BOS_SONUC: dict[str, Any] = {"bulunan": 0, "bulunan_kayit_sayisi": 0, "satilan_adet": 0}


class NoOpYurutucu:
    """Hiçbir şey bilmeyen koşturucu: her aracı BOŞ sonuçla döner.

    Yaptığı tek şey çağrıları kaydetmektir; testler deneme sırasını buradan
    okur. Veritabanı yok, ağ yok, yan etki yok. Döndürdüğü :data:`BOS_SONUC`
    her şablonda "bulunamadı" cümlesine çevrilir.
    """

    def __init__(self) -> None:
        self.cagrilar: list[tuple[str, dict[str, Any]]] = []

    def kos(self, arac: str, argumanlar: dict[str, Any]) -> dict[str, Any]:
        self.cagrilar.append((arac, dict(argumanlar)))
        return dict(BOS_SONUC, aranan=" ".join(str(v) for v in argumanlar.values()))


def dene(niyet: Niyet, yurutucu: NiyetYurutucu) -> dict[str, Any]:
    """Deneme argümanlarını sırayla koşturur; ilk DOLU sonuçta durur.

    İlk deneme kullanıcının yazdığı terimle koşar; boş dönerse ek-kırpılmış
    yedek terim BİR kez denenir ("filtresinden" → FILTRE). Hepsi boşsa son
    denemenin sonucu döner. Beyaz liste dışı araç ``KeyError``.
    """
    if niyet.arac is None:
        raise ValueError("koşturulacak araç yok; niyet bir mesaj taşıyor")
    if niyet.arac not in ARAC_BEYAZ_LISTESI:
        raise KeyError(niyet.arac)
    veri: dict[str, Any] = {}
    for argumanlar in niyet.deneme_argumanlari:
        veri = yurutucu.kos(niyet.arac, argumanlar)
        if not sonuc_bos_mu(niyet.arac, veri):
            break
    return veri


# ---------------------------------------------------------------------------
# Şablonlu cevap — dış model YOK, rakamlar Decimal ile biçimlenir
# ---------------------------------------------------------------------------


_TL_TAKAS = str.maketrans(",.", ".,")


def _tl(deger: Any) -> str | None:
    if deger is None:
        return None
    try:
        d = Decimal(str(deger)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    govde = f"{d:,.2f}"  # 1,234,567.89 -> 1.234.567,89
    return govde.translate(_TL_TAKAS)


def _adet(deger: Any) -> str:
    if deger is None:
        return "0"
    try:
        d = Decimal(str(deger))
    except (InvalidOperation, ValueError):
        return str(deger)
    if d == d.to_integral_value():
        return f"{int(d):,}".replace(",", ".")
    return _tl(d) or str(deger)


def _cari_yaz(veri: dict[str, Any]) -> str:
    if veri.get("bulunan") == 0:
        return f"'{veri.get('aranan', '')}' adına uyan müşteri bulunamadı."
    satirlar = [str(veri.get("musteri") or "Müşteri")]
    bakiye = _tl(veri.get("acik_bakiye"))
    satirlar.append(f"Açık bakiye: {bakiye or '0,00'} TL")
    vadesi = _tl(veri.get("vadesi_gecen"))
    if vadesi and vadesi != "0,00":
        satirlar.append(f"Vadesi geçen: {vadesi} TL")
    tahsilat = _tl(veri.get("toplam_tahsilat"))
    if tahsilat is not None:
        satirlar.append(f"Toplam tahsilat: {tahsilat} TL")
    if veri.get("son_alisveris_tarihi"):
        satirlar.append(f"Son alışveriş: {veri['son_alisveris_tarihi']}")
    return "\n".join(satirlar)


def _parca_yaz(veri: dict[str, Any]) -> str:
    parcalar = veri.get("parcalar") or []
    if not parcalar:
        return (
            f"'{veri.get('aranan', '')}' katalogda bulunamadı. Parça katalogda "
            "tanımlı değilse stok sıfır anlamına gelmez."
        )
    p = parcalar[0]
    baslik = str(p.get("ad") or p.get("parca_no") or "Parça")
    if p.get("parca_no") and p.get("ad"):
        baslik = f"{p['ad']} ({p['parca_no']})"
    satirlar = [baslik]
    stok = _adet(p.get("stok"))
    birim = str(p.get("birim") or "").strip()
    satirlar.append(f"Stok: {stok}{(' ' + birim) if birim else ''}")
    if p.get("raf"):
        satirlar.append(f"Raf: {p['raf']}")
    if p.get("kritik_seviye") is not None:
        satirlar.append(f"Kritik seviye: {_adet(p['kritik_seviye'])}")
    return "\n".join(satirlar)


def _donem_yaz(veri: dict[str, Any]) -> str:
    aralik = veri.get("tarih_araligi") or {}
    baslik = str(veri.get("donem") or "Dönem")
    if aralik.get("baslangic") and aralik.get("bitis"):
        baslik = f"{baslik} ({aralik['baslangic']} – {aralik['bitis']})"
    satirlar = [baslik]
    satirlar.append(
        f"Ciro: {_tl(veri.get('ciro')) or '0,00'} TL"
        f" ({_adet(veri.get('satis_adedi'))} satış)"
    )
    alis = _tl(veri.get("alis_toplami"))
    if alis is not None:
        satirlar.append(f"Alış: {alis} TL ({_adet(veri.get('alis_adedi'))} alış)")
    kar = _tl(veri.get("kaba_kar"))
    if kar is not None:
        satirlar.append(f"Kaba kâr: {kar} TL")
    tahsilat = _tl(veri.get("tahsilat"))
    if tahsilat is not None:
        satirlar.append(f"Tahsilat: {tahsilat} TL")
    odeme = _tl(veri.get("odeme"))
    if odeme is not None:
        satirlar.append(f"Ödeme: {odeme} TL")
    return "\n".join(satirlar)


# WhatsApp'ta uzun mesaj okunmuyor: listeler kısa tutulur ve kaç kaydın
# gösterildiği açıkça yazılır ki eksik liste tam sanılmasın.
_WA_LISTE_SINIRI = 5

_ALACAK_KOVA_ADLARI: tuple[tuple[str, str], ...] = (
    ("not_due", "Vadesi gelmemiş"),
    ("days_1_30", "1-30 gün"),
    ("days_31_60", "31-60 gün"),
    ("days_61_90", "61-90 gün"),
    ("days_90_plus", "90+ gün"),
)


def _alacak_yaz(veri: dict[str, Any]) -> str:
    satirlar = ["Alacak yaşlandırma"]
    toplam = _tl(veri.get("toplam_alacak"))
    musteri_sayisi = veri.get("musteri_sayisi") or 0
    if toplam is not None:
        satirlar.append(f"Toplam: {toplam} TL — {musteri_sayisi} müşteri")

    kovalar = veri.get("yas_gruplari") or {}
    for anahtar, ad in _ALACAK_KOVA_ADLARI:
        deger = _tl(kovalar.get(anahtar))
        # Sıfır kovayı yazmak listeyi şişiriyor; boş olan atlanır.
        if deger is not None and deger not in {"0,00", "0"}:
            satirlar.append(f"  {ad}: {deger} TL")

    musteriler = veri.get("en_yuksek_bakiyeli_musteriler") or []
    gosterilen = musteriler[:_WA_LISTE_SINIRI]
    if gosterilen:
        satirlar.append("En yüksek bakiyeliler:")
        for m in gosterilen:
            ad = m.get("customer_name") or m.get("musteri") or "?"
            tutar = _tl(m.get("total")) or "?"
            satirlar.append(f"  {ad}: {tutar} TL")
        # Sayı GÖSTERİLENden gelir: araç zaten ilk 5'i döndürüyor, sabit "5"
        # yazmak 2 satır listede "ilk 5" diyip yanlış izlenim veriyordu.
        if musteri_sayisi > len(gosterilen):
            satirlar.append(
                f"(en yüksek {len(gosterilen)} tanesi; tamamı için web paneli)"
            )
    return "\n".join(satirlar)


def _kritik_yaz(veri: dict[str, Any]) -> str:
    urunler = veri.get("urunler") or []
    esik_yok = veri.get("liste_olcutu") == "stok_sifir_ve_alti"

    if esik_yok:
        tukenen = veri.get("stogu_tukenmis_urun_sayisi") or 0
        if not urunler:
            return "Stoğu tükenmiş ürün yok."
        # Eşik tanımlı değilken "kritik" demek yanıltıcı olur: ölçüt farklı.
        satirlar = [f"Stoğu tükenmiş {tukenen} ürün var:"]
    else:
        sayi = veri.get("kritik_seviyedeki_urun_sayisi") or 0
        if not urunler:
            return "Kritik seviyenin altında ürün yok."
        satirlar = [f"Kritik seviyede {sayi} ürün:"]

    for u in urunler[:_WA_LISTE_SINIRI]:
        kod = u.get("kod")
        birim = u.get("birim") or ""
        etiket = f"{u.get('ad')}" + (f" ({kod})" if kod else "")
        satirlar.append(
            f"  {etiket}: {u.get('mevcut_stok')} {birim}".rstrip()
            + (f" / kritik {u.get('kritik_seviye')}" if not esik_yok else "")
        )
    if len(urunler) > _WA_LISTE_SINIRI:
        satirlar.append(f"(ilk {_WA_LISTE_SINIRI}; tamamı için web paneli)")
    if esik_yok:
        satirlar.append("Not: ürünlere kritik eşik girilmemiş, ölçüt stoğun kendisi.")
    return "\n".join(satirlar)


def _en_cok_satan_yaz(veri: dict[str, Any]) -> str:
    urunler = veri.get("urunler") or []
    donem = veri.get("donem") or "bu dönem"
    if not urunler:
        return f"{donem} hiç satış kaydı yok."
    satirlar = [f"{donem} en çok satanlar:"]
    for sira, u in enumerate(urunler[:_WA_LISTE_SINIRI], start=1):
        ad = u.get("product_name") or u.get("ad") or "?"
        adet = u.get("quantity")
        tutar = _tl(u.get("total"))
        parca = f"{sira}. {ad}: {adet} adet"
        if tutar is not None:
            parca += f", {tutar} TL"
        satirlar.append(parca)
    return "\n".join(satirlar)


def _parca_satis_yaz(veri: dict[str, Any]) -> str:
    aranan = veri.get("aranan") or "?"
    donem = veri.get("donem") or "bu dönem"
    adet = veri.get("satilan_adet")
    # Satış yokluğu stok yokluğu DEĞİLDİR; araç bunu ayrıca not düşüyor ve
    # cevapta da söylenmezse kullanıcı "elimde yok" diye okuyor.
    if not adet:
        return (
            f"\"{aranan}\" — {donem} satış kaydı yok. "
            "(Bu, stokta olmadığı anlamına gelmez; stok için \"{0} stok\" yazın.)"
            .format(aranan)
        )
    satirlar = [f"\"{aranan}\" — {donem}"]
    satirlar.append(f"Satılan: {adet} adet")
    tutar = _tl(veri.get("toplam_tutar"))
    if tutar is not None:
        satirlar.append(f"Tutar: {tutar} TL")
    belge = veri.get("belge_sayisi")
    if belge:
        satirlar.append(f"Belge sayısı: {belge}")
    son = veri.get("son_satis_tarihi")
    if son:
        satirlar.append(f"Son satış: {son}")
    return "\n".join(satirlar)


def cevap_yaz(arac: str, veri: dict[str, Any]) -> str:
    """Araç sonucunu kullanıcı cümlesine çevirir — tamamı uygulama tarafında."""
    if arac == "cari_durum":
        return _cari_yaz(veri)
    if arac == "parca_stok":
        return _parca_yaz(veri)
    if arac == "donem_ozeti":
        return _donem_yaz(veri)
    if arac == "alacak_yaslandirma":
        return _alacak_yaz(veri)
    if arac == "kritik_stok":
        return _kritik_yaz(veri)
    if arac == "en_cok_satan_parcalar":
        return _en_cok_satan_yaz(veri)
    if arac == "parca_satis_gecmisi":
        return _parca_satis_yaz(veri)
    raise KeyError(arac)


# ---------------------------------------------------------------------------
# Faz 2: deterministik TAHSİLAT (yazma) niyeti — LLM YOK
# ---------------------------------------------------------------------------
#
# Yazma niyeti okuma niyetlerinden ÖNCE denenir ve yalnız şu üçü birlikteyse
# taslağa gider: tahsilat kökü + tek ve geçerli tutar + zorunlu yöntem
# kelimesi. Eksik/belirsiz her durum rehberlik ya da red mesajıdır; hiçbir
# finansal kayıt bu aşamada OLUŞMAZ (taslağı da çağıran açar).

TAHSILAT_KOKLER: frozenset[str] = frozenset({"TAHSILAT", "TAHSIL"})

# Günlük konuşma karşılıkları. Kullanıcı "tahsilat" kelimesini nadiren yazıyor;
# "Şaban Korkmaz 175.000 verdi" doğal olan. Bunlar TAHSILAT_KOKLER'den AYRI
# tutulur çünkü tetikleme koşulları farklı (aşağıdaki stok koruması).
#
# Kökler bilerek TAM çekimli: "VER" kökü "veresiye"yi (CARI_KOKLER) yutardı.
# "GETIR" alınmadı — "3 tane getirdi" gibi stok cümlelerini yakalar.
TAHSILAT_FIIL_KOKLERI: frozenset[str] = frozenset({
    "VERDI", "ODEDI", "ODEME", "YATIRDI",
})

# Yöntem mesajda ZORUNLU; hesap, yöntemden deterministik seçilir ve onay
# özetinde adıyla gösterilir (sessiz varsayım yok).
YONTEM_SOZLUGU: dict[str, str] = {
    "NAKIT": "cash",
    "HAVALE": "bank_transfer",
    "EFT": "bank_transfer",
    "KART": "card",
}
_YONTEM_TR = {"cash": "nakit", "bank_transfer": "havale/EFT", "card": "kart"}

# Geçmiş tarih ve TRY dışı para birimi ilk sürümde reddedilir.
_GECMIS_TARIH_KOKLERI = frozenset({"DUN", "GECEN", "ONCEKI", "EVVELSI"})
_YABANCI_PARA = frozenset({"USD", "DOLAR", "EUR", "EURO", "STERLIN", "GBP", "POUND"})
_TARIH_DESENI = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")

# Tutar: "20.000", "20.000,50", "20000", "20000,5". Nokta binlik, virgül
# ondalıktır. 10+ haneli saf rakam telefon sayılır, tutar adayı DEĞİLDİR.
_TUTAR_DESENI = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?(?![\d.,])"
)
_CARPANLAR = {"BIN": Decimal("1000"), "MILYON": Decimal("1000000")}
TUTAR_UST_SINIR = Decimal("10000000")  # 10 milyon TL; üstü yanlış girdi sayılır
_PARA_BIRIMI_KELIMELERI = frozenset({"TL", "LIRA", "TRY", "KURUS"})


@dataclass(frozen=True, slots=True)
class TahsilatNiyeti:
    """Deterministik yazma niyeti: taslak açmaya yetecek üç bilgi + terim."""

    tutar: Decimal
    yontem: str          # cash | bank_transfer | card
    musteri_terimi: str  # eleme yönteminden kalan; boş olabilir (rehberlik)


def tutar_ayikla(metin: str) -> tuple[Decimal | None, str | None, str]:
    """(tutar, hata_mesajı, tutar_dışı_metin) döndürür.

    Eksi işaretli / sıfır / üst sınır üstü / birden çok tutar RED; sözel
    çarpan yalnız kapalı sözlükten (bin, milyon) ve tutarı izleyen ilk
    kelimeyse uygulanır. Kalan metin müşteri terimi çıkarımı içindir.
    """
    adaylar: list[tuple[int, int, Decimal]] = []
    for esles in _TUTAR_DESENI.finditer(metin):
        govde, kurus = esles.group(1), esles.group(2)
        duz = govde.replace(".", "")
        if len(duz) >= 10 and kurus is None:
            continue  # telefon görünümlü; tutar adayı değil
        if esles.start() > 0 and metin[esles.start() - 1] == "-":
            return None, "Negatif tutar kabul edilmez.", metin
        deger = Decimal(duz + (("." + kurus) if kurus else ""))
        adaylar.append((esles.start(), esles.end(), deger))

    if not adaylar:
        return None, None, metin
    if len(adaylar) > 1:
        return None, (
            "Mesajda birden fazla tutar var; tek tutar yazın "
            "(örn. \"20.000 TL\")."
        ), metin

    bas, son, deger = adaylar[0]
    kalan_son = metin[son:]

    kelimeler = kalan_son.split()
    if kelimeler:
        ilk = tr_katla(kelimeler[0])
        for kok, carpan in _CARPANLAR.items():
            if _kok_eslesir(ilk, frozenset({kok})):
                deger = deger * carpan
                kalan_son = " ".join(kelimeler[1:])
                break

    if deger <= 0:
        return None, "Tutar sıfırdan büyük olmalı.", metin
    if deger > TUTAR_UST_SINIR:
        return None, (
            "Tutar üst sınırı aşıyor (en çok 10.000.000 TL); "
            "büyük tahsilatları web panelinden girin."
        ), metin
    return deger, None, (metin[:bas] + " " + kalan_son)


def tahsilat_coz(metin: str) -> TahsilatNiyeti | Niyet | None:
    """Yazma niyetini çözer.

    Dönüş: ``None`` (yazma niyeti değil — okuma akışı denesin),
    :class:`TahsilatNiyeti` (taslak açılabilir) ya da rehberlik/red mesajı
    taşıyan :class:`Niyet`.
    """
    ciftler = _jetonla(metin or "")
    katlilar = [k for _, k in ciftler]
    acik_kok = any(_kok_eslesir(k, TAHSILAT_KOKLER) for k in katlilar)
    # Fiil yalnız stok bağlamı YOKKEN tahsilat sayılır: "5 tane verdi" bir
    # parça teslimidir, tahsilat değil. Açık "tahsilat" kelimesi yazıldığında
    # bu koruma aranmaz — kullanıcı niyetini zaten söylemiştir.
    fiil_kok = any(
        _kok_eslesir(k, TAHSILAT_FIIL_KOKLERI) for k in katlilar
    ) and not any(_grup_eslesir(k, STOK_KOKLER) for k in katlilar)
    if not (acik_kok or fiil_kok):
        return None

    tutar, hata, kalan = tutar_ayikla(metin)
    if hata:
        return Niyet(mesaj=hata)
    if tutar is None:
        # Tutar yoksa bu bir OKUMA sorusudur ("geçen ay tahsilat ne kadar").
        return None

    # Geçmiş tarih / TRY dışı para birimi: ilk sürümde açık red.
    if any(k in _GECMIS_TARIH_KOKLERI for k in katlilar) or _TARIH_DESENI.search(metin):
        return Niyet(mesaj=(
            "Geçmiş tarihli tahsilat WhatsApp'tan girilemez; web panelini "
            "kullanın. (WhatsApp tahsilatı bugünün tarihiyle kaydedilir.)"
        ))
    if any(k in _YABANCI_PARA for k in katlilar):
        return Niyet(mesaj="WhatsApp tahsilatı yalnız TL kabul eder.")

    yontem: str | None = None
    for k in katlilar:
        if k in YONTEM_SOZLUGU:
            if yontem is not None and YONTEM_SOZLUGU[k] != yontem:
                return Niyet(
                    mesaj="Tek bir tahsilat yöntemi yazın: nakit / havale / kart."
                )
            yontem = YONTEM_SOZLUGU[k]
    if yontem is None:
        return Niyet(mesaj=(
            "Tahsilat yöntemi gerekli: nakit / havale / kart. Örn. "
            "\"Şaban Korkmaz 20.000 TL nakit tahsilat\"."
        ))

    # Müşteri terimi: tutar dışı metinden, sözlük/niyet/yöntem/para-birimi
    # kelimeleri elenerek çıkarılır. Kök eşleşmesi burada ≥4 harfle sınırlı:
    # "Yılmaz" soyadı 3 harflik "yıl" köküne yaslanıp elenmemeli — yazma
    # akışında fazla eleme, müşteri terimini yutar (fazla bırakmak yalnız
    # "bulunamadı"ya yol açar; güvenli yön budur).
    def _elenir(katli: str) -> bool:
        if (
            katli in SORU_SOZLUGU
            or katli in _TUM_NIYET_KOKLERI
            or katli in TAHSILAT_KOKLER
            or katli in TAHSILAT_FIIL_KOKLERI
            or katli in YONTEM_SOZLUGU
            or katli in _PARA_BIRIMI_KELIMELERI
        ):
            return True
        for uzunluk in range(len(katli) - 1, 3, -1):  # kök ≥4 harf
            if len(katli) - uzunluk > _EK_TOLERANSI:
                continue
            govde = katli[:uzunluk]
            if (
                govde in SORU_SOZLUGU
                or govde in TAHSILAT_KOKLER
                or govde in TAHSILAT_FIIL_KOKLERI
            ):
                return True
            sert = _YUMUSAMA.get(govde[-1])
            if sert is not None and govde[:-1] + sert in SORU_SOZLUGU:
                return True
        return False

    terim_parcalari: list[str] = []
    for ham, katli in _jetonla(kalan):
        if len(katli) <= 1:
            continue
        if not any(ch.isdigit() for ch in katli) and _elenir(katli):
            continue
        terim_parcalari.append(ham)
    return TahsilatNiyeti(
        tutar=tutar, yontem=yontem, musteri_terimi=" ".join(terim_parcalari)
    )


def tutar_yaz(deger: Decimal) -> str:
    """Rehberlik mesajı için tutar: tam sayıysa kuruş yazılmaz (175.000).

    Rehberlik metni kullanıcıya örnek cümle veriyor; oraya ``175.000,00``
    koymak yazmayacağı bir biçimi öğretir.
    """
    metin = para_tr(deger)
    return metin[:-3] if metin.endswith(",00") else metin


def para_tr(deger: Decimal) -> str:
    """Onay/başarı mesajları için Türkçe biçim (1.234,56)."""
    return f"{deger.quantize(Decimal('0.01')):,.2f}".translate(_TL_TAKAS)


def tahsilat_ozeti(
    musteri_adi: str, tutar: Decimal, yontem: str, hesap_adi: str, bakiye: Decimal
) -> str:
    return (
        f"{musteri_adi} için {para_tr(tutar)} TL {_YONTEM_TR[yontem]} tahsilat "
        "kaydedilecek.\n"
        f"Hesap: {hesap_adi}\n"
        f"Mevcut açık bakiye: {para_tr(bakiye)} TL\n"
        f"İşlem sonrası bakiye: {para_tr(bakiye - tutar)} TL\n"
        "Onaylıyor musunuz? ONAY / İPTAL"
    )


def tahsilat_basarili(payment_id: int, yeni_bakiye: Decimal) -> str:
    return (
        "Tahsilat kaydedildi.\n"
        f"İşlem no: {payment_id}\n"
        f"Yeni açık bakiye: {para_tr(yeni_bakiye)} TL"
    )


__all__ = [
    "ARAC_BEYAZ_LISTESI",
    "CARI_KOKLER",
    "DONEM_KOKLER",
    "KAPSAM_MESAJI",
    "Niyet",
    "NiyetYurutucu",
    "NoOpYurutucu",
    "SORU_SOZLUGU",
    "STOK_KOKLER",
    "TAHSILAT_FIIL_KOKLERI",
    "TAHSILAT_KOKLER",
    "TUTAR_UST_SINIR",
    "TahsilatNiyeti",
    "YONTEM_SOZLUGU",
    "cevap_yaz",
    "coz",
    "dene",
    "para_tr",
    "sonuc_bos_mu",
    "tahsilat_basarili",
    "tahsilat_coz",
    "tahsilat_ozeti",
    "tr_katla",
    "tutar_ayikla",
    "tutar_yaz",
    "tutarli_rehber",
]
