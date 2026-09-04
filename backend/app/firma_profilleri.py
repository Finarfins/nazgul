"""Firma profilleri: bir firmanın hangi işleri yaptığının KÜMESİ.

Konu: `companies.profiller` (göç `20260904_0068`). Bu sütun bir AYIRICI
DEĞİLDİR — bir firma aynı anda birden fazla profil taşır ve KARMA olması
İSTİSNA DEĞİL KURALDIR (sahibin kendi işi: bayi + servis + tarla + sürü).
Bu yüzden alan tekil `profil` değil ÇOĞUL `profiller`dir ve içeriği bir
alt kümedir.

--- DEPOLAMA: TEXT, VİRGÜLLE BİRLEŞTİRİLMİŞ, KANONİK ----------------------

`TEXT NOT NULL DEFAULT ''`, içeriği ayıklanmış + TEKİLLEŞTİRİLMİŞ +
SIRALANMIŞ alt kümenin virgülle birleştirilmiş hâli. `''` "HENÜZ
SEÇİLMEDİ" demektir ve bu bir olgu UYDURMAZ: boş küme, "bu firma hiçbir
profil BİLDİRMEDİ" cümlesinin kendisidir, "bilinmiyor"un yerine geçen bir
varsayılan değildir. NULL yerine `''` seçilmesi üç değerli mantığı
çağıranlardan uzak tutar — okuyan her yerde `x is None` ile `x == ""`
ayrımını doğru yapmak zorunda kalmak, bu kümenin hiçbir yerde
kazandırmadığı bir yüktür.

JSONB DEĞİL, TEXT: bu depo SQLite<->PostgreSQL 16 EŞLİĞİNİ koruyor ve
diyalektin JSON tiplemesi bu depoyu daha önce ISIRDI — göç 0016'nın
`einvoice_payload` gerekçesinin aynısı, orada da TEXT-olarak-JSON seçildi.

KANONİK BİÇİM SAKLANIR, GİRİLDİĞİ GİBİ DEĞİL. Gerekçe: aynı kümenin iki
yazılışı (`"tuccar,ciftci"` ve `"ciftci,tuccar"`) aynı olgudur ve ikisini
de saklamak, eşitliği SORULAMAZ yapardı — sonraki okuyucu iki satırı
karşılaştırdığında farklı sanırdı. Sıralama alfabetiktir ve
BELİRLENİMCİDİR; küme sırasına bırakmak (Python `set` yineleme sırası)
aynı girdiye farklı satırlar yazdırırdı.

--- BÜYÜK/KÜÇÜK HARF KATLAMASI YOKTUR VE BU BİLİNÇLİDİR -------------------

Belirteçler TAM olarak eşleşmek zorundadır; `"CIFTCI"` ve `"Ciftci"`
REDDEDİLİR. Katlama EKLENMEDİ çünkü bu depoda Türkçe katlama ÜÇ KATMANDA
BİRBİRİNDEN AYRIŞTI ve ayrışmanın maliyeti ölçüldü: `app/units.py`in
`turkce_katla`sı YUKARI katlar (`.replace("i","İ")` + `.upper()`),
`app/routers/farm.py`in `_bitki_katla`sı AŞAĞI katlar (`.lower()`), ve
ikisi AYNI DENKLİĞİ ÜRETMEZ (bkz. `farm.py`de `_bitki_katla`nın üstündeki
"BİRLEŞTİRİLMEMELİDİR" bloğu; ölçülmüş karşı örnekler orada).

Bu küme MAKİNEYE bakan KAPALI bir kümedir, serbest metin değildir: değerleri
arayüz üretir, insan yazmaz. Üçüncü bir katlama kopyası eklemek, bugün
kazandırmadığı bir esneklik için o ayrışmayı bir katman daha büyütürdü.
Esneklik bir gün istenirse, katlamanın HANGİSİ olduğu KENDİ kararıdır.

--- BU PR'DA HİÇBİR ŞEY BU SÜTUNA GÖRE DALLANMIYOR ------------------------

Modül anahtarları SONRAKİ iştir. Bu dilim sütunu AÇAR ve YAZAN İKİ YOLU
bağlar (kayıt ve firma ayarları); okuyan hiçbir kapı, hiçbir yetki kararı,
hiçbir menü bu değere BAKMAZ. #31'in `app/parti.py` duruşunun aynısı: bir
alanın KANONİK biçimi, onu unutacak bir çağıran ortaya çıkmadan ÖNCE
çivilenmelidir.
"""

from __future__ import annotations

from typing import Final

#: Geçerli profil belirteçleri. KAPALI küme; bilinmeyen belirteç reddedilir.
#: Sıralı demet, hata metninin BELİRLENİMCİ olması için — `frozenset`
#: yinelemesi çalıştırmadan çalıştırmaya değişir ve hata metni değişirse
#: onu bekleyen test kararsız olurdu.
GECERLI_PROFILLER: Final[tuple[str, ...]] = (
    "ciftci",
    "pazarci",
    "tuccar",
    "veteriner",
)

_GECERLI: Final[frozenset[str]] = frozenset(GECERLI_PROFILLER)

#: Ayraç. Tek karakter ve boşluk ayıklanıyor, yani `"a, b"` ile `"a,b"`
#: AYNI kümedir.
AYRAC: Final = ","


def profilleri_coz(value: str | None) -> str:
    """Serbest CSV metnini KANONİK profil dizgisine indirger.

    Döner: ayıklanmış, TEKİLLEŞTİRİLMİŞ, ALFABETİK SIRALI ve virgülle
    birleştirilmiş dizgi. `None`, boş metin ve yalnız ayraç/boşluk içeren
    metin `""` verir — üçü de "HENÜZ SEÇİLMEDİ"dir.

    BİLİNMEYEN BELİRTEÇ `ValueError` ATAR. `HTTPException` DEĞİL: iki
    çağıranın İKİSİ DE Pydantic doğrulayıcısıdır ve Pydantic `ValueError`ı
    422'ye çevirir — yani red AİLE İÇİNDE kalır ve bu modül FastAPI'ye
    bağımlı olmaz. `validate_tax_number`ın `validate_vkn` içindeki kalıbının
    aynısı.

    Boş girdi REDDEDİLMEZ: profil bildirmemek geçerli bir durumdur ve
    `''`ün var olma sebebi tam olarak budur. Reddedilen tek şey, kapalı
    kümede OLMAYAN bir belirteçtir.
    """
    if value is None:
        return ""
    parcalar = [parca.strip() for parca in value.split(AYRAC)]
    secilen = {parca for parca in parcalar if parca}
    if not secilen:
        return ""
    bilinmeyen = sorted(secilen - _GECERLI)
    if bilinmeyen:
        raise ValueError(
            f"Bilinmeyen firma profili: {', '.join(bilinmeyen)}. "
            f"Geçerli profiller: {', '.join(GECERLI_PROFILLER)}"
        )
    return AYRAC.join(sorted(secilen))
