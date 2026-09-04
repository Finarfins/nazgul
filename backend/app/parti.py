"""FEFO seçicisi: hangi partiden ne kadar düşüleceğini söyler. ÇAĞIRANI YOKTUR.

Konu: PARTİ + SKT + FEFO, PR 1 — seçici ve deposu (göç `20260903_0067`),
hiçbir şeye bağlanmadan.

--- BU DOSYA NEDEN ÇAĞIRANDAN ÖNCE YAZILDI --------------------------------

`app/units.py`nin gerekçesinin AYNISI ve tekrar edilmesi bilinçlidir, çünkü
burada bir kat daha serttir: bir seçicinin SIRALAMASI, onu çağıran ilk çıkış
yolu yazılmadan ÖNCE çivilenmelidir.

Ters sıranın bilinen sonucu: çağıran önce gelirse sıralama o çağıranın
ihtiyacına göre şekillenir (çoğu zaman "en kolay bulduğum satır"), ikinci
çağıran geldiğinde sıra zaten bir yerde donmuştur ve ikisi SESSİZCE ayrışır.
İki farklı yerden çıkan mal iki farklı partiden düşerse geri çağırma kaydı
YALAN SÖYLER ve yalanı yakalayacak hiçbir kırmızı yoktur.

--- SAF: SAAT OKUNMAZ, VERİTABANI OKUNMAZ ---------------------------------

`bugun` ZORUNLU BİR ARGÜMANDIR ve `date.today()` BU DOSYADA GEÇMEZ.

Gerekçe ölçülebilir, üslup değil: `today()` çağıran bir fonksiyon aynı girdi
için farklı gün farklı cevap verir, yani "süresi geçmiş mi" sorusunun cevabı
TESTTE ÇİVİLENEMEZ — bir SKT testi yazıldığı gün yeşil, altı ay sonra
kırmızı olurdu ve kırmızılığı kusuru DEĞİL takvimi gösterirdi. Bu depo donmuş
saat konusunda kendi dersini ayrıca aldı (`donmus_saat.py`).

İkinci sebep: iş günü bu depoda İstanbul'a göre tanımlıdır
(`app/business_time.py`) ve UTC'ye göre DEĞİLDİR. Hangi günün "bugün"
olduğunu SEÇMEK çağıranın işidir; seçiciye ait olsaydı, seçici sessizce
yanlış saat dilimini dayatırdı.

--- SIRA: EN ERKEN SKT ÖNCE, NULL EN SONA, SONRA `created_at`, SONRA `id` --

FEFO = First Expired, First Out. Sıra dört anahtarlıdır ve HER BİRİNİN AYRI
gerekçesi var:

1. `expiry_date` ARTAN. Tanımın kendisi: bozulmaya en yakın olan önce çıkar.

2. SKT'si OLMAYAN (NULL) PARTİ EN SONA. Bu, sahip kararı 2'nin (SKT isteğe
   bağlı) bedelinin düştüğü yerdir ve tersi CİDDİ BİR KUSUR olurdu:
   NULL'u başa koymak bozulabilir malı rafta BEKLETİR ve göçün düzeltmek
   için var olduğu kusuru ÜRETİRDİ.

   Kural burada, PYTHON'DA duruyor ve `ORDER BY`a BIRAKILMADI. Sebep ölçülmüş
   bir diyalekt farkıdır: PostgreSQL artan sırada NULL'ları SONA koyar
   (`NULLS LAST` varsayılan), SQLite ise BAŞA koyar. Yani sırayı veritabanına
   bırakmak İKİ DİYALEKTTE İKİ FARKLI CEVAP verirdi — ve bu PR'ın SQLite
   testleri ile PostgreSQL ikizi o farkı GÖRMEZDİ, çünkü her biri kendi
   diyalektinin sırasında yeşil kalırdı. Kuralın tek bir yerde, ölçülebilir
   biçimde durması bu yüzden.

3. `created_at` ARTAN. Aynı SKT'li iki parti arasında ÖNCE GİRENİ seç: FIFO,
   FEFO'nun içindeki eşitlik bozucusu olarak. Rafta daha uzun duran mal önce
   çıkmalıdır.

4. `id` ARTAN. Son çare, ve VARLIK SEBEBİ BELİRLENİMCİLİKTİR: `created_at`
   de eşit olabilir (toplu içe aktarma aynı damgayı basar) ve o noktada
   sıra KARARSIZ kalırsa aynı girdi aynı cevabı vermez. Kararsız bir seçici,
   geri çağırma kaydını sorgulanamaz yapar.

--- SÜRESİ GEÇMİŞ: SESSİZCE SEÇİLMEZ, AMA GİZLENMEZ DE -------------------

Sahip kararı: seçici süresi geçmiş partiyi VARSAYILAN OLARAK REDDEDER
(`izin_ver_suresi_gecmis=False`), AMA onları `Secim.suresi_gecmis` içinde
GERİ DÖNDÜRÜR.

İkisi birlikte olmak zorundadır ve sebebi şudur: yalnız reddetmek "elde mal
yok" der, oysa GERÇEK "elde mal VAR ama süresi geçmiş"tir. Bu iki cümle
operatör için TAMAMEN farklı iki iştir — biri satın alma, öteki imha/iade.
Reddi sessiz bir yokluğa çevirmek, bilgiyi red anında YOK EDERDİ.

`ParticiYetersiz` de bu yüzden `suresi_gecmis`i ÜZERİNDE TAŞIR: red bir kayıt
olayıdır ve kanıtı yanında gitmelidir (`units.UrunTemsilEdilemez`in girdiyi
ve katsayıyı taşımasının aynı gerekçesi).

`izin_ver_suresi_gecmis=True` VARSAYILAN DEĞİLDİR ve olmaması karardır:
varsayılan olsaydı, kimse o bayrağı yazmadığı için süresi geçmiş mal
SESSİZCE çıkardı ve bu göçün ölçtüğü kusurun TA KENDİSİ olurdu. Bayrak
AÇIKÇA yazılmak zorundadır; yazan kişi ne yaptığını beyan etmiş olur.

SKT'si BUGÜN OLAN PARTİ SÜRESİ GEÇMİŞ SAYILMAZ (`expiry_date < bugun`,
`<=` DEĞİL). "Son kullanma tarihi" SON KULLANILABİLİR GÜNDÜR; o günü dışarıda
bırakmak, kullanılabilir malı bir gün erken imhaya yollardı. Sınır dar ve
testte ADIYLA çivili.

--- SONLU OLMAYAN SAYILAR: KAPI KARŞILAŞTIRMADAN ÖNCE --------------------

BU DERS #27'DE ÖLÇÜLDÜ VE BURADA TEKRARLANMIYOR, UYGULANIYOR:

Python'da `Decimal("NaN") <= 0` KARŞILAŞTIRMANIN KENDİSİ
`decimal.InvalidOperation` ATAR. Yani bir sonluluk denetimi `< 0`
karşılaştırmasından SONRA konursa NaN ona HİÇ ULAŞAMAZ ve aile DIŞINDA bir
istisna sızar. `except PartiSecilemedi:` yazan çağıran onu yakalayamaz;
gerçek bir girdinin kaçtığı bir aile SÖZLEŞME DEĞİLDİR.

Bu yüzden her sayısal girdi için sıra ZORUNLUDUR ve üslup değildir:
    tip kapısı  ->  `is_finite()` kapısı  ->  ANCAK SONRA karşılaştırma

--- İKİ AYRI SEBEP: GİRDİ KUSURU / DEFTER KUSURU -------------------------

`units.py`nin `MIKTAR_SONLU_DEGIL` ile `KATSAYI_GECERSIZ` ayrımının aynısı ve
aynı gerekçeyle:

  * `needed` bozuksa -> `ISTENEN_GECERSIZ`. Bu bir GİRDİ kusurudur: sayıyı o
    an bir insan ya da bir istek gövdesi yazdı. Çaresi YENİDEN GİRMEKTİR.
  * Bir partinin `quantity`si bozuksa -> `PARTI_MIKTARI_GECERSIZ`. Bu bir
    DEFTER kusurudur: `product_lots` satırı bozuktur ve operatörün yeniden
    girmesi onu DÜZELTMEZ; birinin DEFTERİ düzeltmesi gerekir.

İki farklı çare, iki farklı sebep. Çağıran `sebep` üzerinden yönlendirir.

--- NEDEN İSTİSNA, NEDEN SENTINEL DEĞİL ----------------------------------

`units.py` ile aynı: red bir sentinel ile dönseydi (boş liste, `None`) çağıran
onu "hiç mal yok, sorun değil" diye SESSİZCE geçebilirdi. Boş liste burada
ÖZELLİKLE tehlikelidir çünkü `needed == 0` için MEŞRU bir cevaptır — yani
sentinel, geçerli bir sonuçtan AYIRT EDİLEMEZDİ.

--- MİKTAR BU DOSYADA YUVARLANMAZ ----------------------------------------

Seçici yalnız BÖLÜŞTÜRÜR: her dağıtım payı ya bir partinin miktarının
KENDİSİDİR ya da kalan ihtiyacın kendisidir. Hiçbir yerde ÇARPMA yoktur, bu
yüzden yeni ondalık basamak DOĞMAZ ve yuvarlamaya gerek KALMAZ.

Ölçek çağıranındır: `units.resolve` girdiyi zaten `URUN_KUANTUM`a çeker ve
`product_lots.quantity` ile `products.stock` aynı `NUMERIC(18,4)` ailesidir.
`URUN_KUANTUM` BURADA YENİDEN TANIMLANMADI, `units`ten İTHAL EDİLDİ — #27
ikinci bir kopyanın nasıl ayrıştığını Türkçe katlama üzerinden ölçtü; aynı
hatayı bir sayısal sabit için tekrarlamak daha da kötü olurdu.

Dağıtımın toplamı `needed`e TAM OLARAK eşittir (yuvarlama farkı YOKTUR) ve
bu, testte ADIYLA çivilidir.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple, Sequence

from app.units import URUN_KUANTUM

__all__ = [
    "URUN_KUANTUM",
    "Parti",
    "ParticiYetersiz",
    "PartiSecilemedi",
    "Secim",
    "fefo_sec",
]


class Parti(NamedTuple):
    """`product_lots` satırının seçiciye GEREKEN kısmı, o kadar.

    `lot_code` BİLEREK YOKTUR: seçici kodu OKUMAZ, çünkü sıralamada hiçbir
    rolü yoktur ve buraya konsaydı ilk okuyucu onu bir eşitlik bozucu sanardı
    ("alfabetik" bir FEFO, FEFO değildir). `company_id` de yoktur: kiracı
    kapsamı ÇAĞIRANIN sorgusunda kurulur ve iki katmanda birden kurulması
    ikisinin ayrışabileceği bir yer açardı.
    """

    id: int
    quantity: Decimal
    expiry_date: date | None
    created_at: datetime


class Secim(NamedTuple):
    """Seçicinin cevabı: DAĞITIM ve SÜRESİ GEÇMİŞLER, ayrı ayrı.

    `dagitim` FEFO SIRASINDADIR — bu bir küme değil DİZİDİR ve sırası
    anlamlıdır: hareketleri o sırada yazan çağıran, geri çağırma kaydını da
    o sırada üretir.

    `suresi_gecmis` `dagitim`ın İÇİNDE OLABİLİR DE OLMAYABİLİR DE:
    `izin_ver_suresi_gecmis=False` iken dağıtımın DIŞINDADIR (dışlandılar),
    `True` iken İÇİNDE OLABİLİR (kullanıldılar). Her iki durumda da RAPOR
    EDİLİR — çağıran hangi durumda olduğunu bayraktan zaten bilir, ama
    "hangi partiler" sorusunun cevabını yalnız buradan alabilir.
    """

    dagitim: tuple[tuple[int, Decimal], ...]
    suresi_gecmis: tuple[Parti, ...]


class PartiSecilemedi(Exception):
    """Seçici bir olgu UYDURMAK yerine durdu. `sebep` hangisi olduğunu söyler.

    `units.BirimCozulemedi` ile aynı sözleşme: sebepler çağıranlar için AYIRT
    EDİCİDİR ve ailenin DIŞINA hiçbir istisna sızmaz (özellikle
    `decimal.InvalidOperation`).
    """

    ISTENEN_GECERSIZ = "ISTENEN_GECERSIZ"
    PARTI_MIKTARI_GECERSIZ = "PARTI_MIKTARI_GECERSIZ"
    PARTI_YINELENEN_KIMLIK = "PARTI_YINELENEN_KIMLIK"
    PARTI_YETERSIZ = "PARTI_YETERSIZ"

    def __init__(self, sebep: str, mesaj: str) -> None:
        super().__init__(f"{sebep}: {mesaj}")
        self.sebep = sebep


class ParticiYetersiz(PartiSecilemedi):
    """Eldeki UYGUN parti toplamı isteneni KARŞILAMIYOR.

    `PartiSecilemedi` ailesindedir (aynı `except` onu da yakalar) ama KENDİ
    ADI vardır, çünkü "girdi bozuk" ile "girdi doğru, mal yetmiyor" farklı
    hatalardır: birincisi bir HATA, ikincisi bir İŞ DURUMUDUR ve çağıran
    ikincisini büyük olasılıkla operatöre GÖSTERECEKTİR.

    KANIT ÜZERİNDE TAŞINIR — `istenen`, `mevcut`, `eksik` ve
    `suresi_gecmis`. Sonuncusu en önemlisidir: "yetmedi" cümlesi ile "yetmedi
    ÇÜNKÜ var olan mal SÜRESİ GEÇMİŞ" cümlesi iki farklı işe yollar (satın
    alma / imha), ve ikinciyi yalnız bu alan söyleyebilir.
    """

    def __init__(
        self,
        istenen: Decimal,
        mevcut: Decimal,
        suresi_gecmis: tuple[Parti, ...],
    ) -> None:
        eksik = istenen - mevcut
        gecmis_notu = (
            ""
            if not suresi_gecmis
            else (
                f" Ayrıca SÜRESİ GEÇMİŞ {len(suresi_gecmis)} parti var "
                f"(toplam {sum((p.quantity for p in suresi_gecmis), Decimal('0'))})"
                " ve bunlar seçime GİRMEDİ; 'mal yok' ile 'mal var ama süresi "
                "geçmiş' aynı şey değildir."
            )
        )
        super().__init__(
            self.PARTI_YETERSIZ,
            f"istenen {istenen}, uygun partilerde {mevcut} var, {eksik} eksik."
            + gecmis_notu,
        )
        self.istenen = istenen
        self.mevcut = mevcut
        self.eksik = eksik
        self.suresi_gecmis = suresi_gecmis


def _sonlu_miktar(deger: object, sebep: str, ad: str) -> Decimal:
    """Tip kapısı, SONRA sonluluk kapısı, ANCAK SONRA karşılaştırma.

    SIRA ZORUNLUDUR ve üslup değildir — bkz. başlık. `Decimal("NaN") < 0`
    KARŞILAŞTIRMANIN KENDİSİ `decimal.InvalidOperation` atar ve o istisna
    `PartiSecilemedi` ailesinin DIŞINDADIR (#27'de ölçüldü). Sonluluk
    denetimi `< 0`dan SONRA konsaydı NaN ona HİÇ ULAŞAMAZDI.
    """
    if not isinstance(deger, Decimal):
        raise PartiSecilemedi(
            sebep,
            f"{ad} Decimal olmalı, {type(deger).__name__} verildi. Bu seçici "
            "float KABUL ETMEZ; ikili kayan nokta bir stok sayısına giremez.",
        )
    if not deger.is_finite():
        raise PartiSecilemedi(
            sebep,
            f"{ad} SONLU değil: {deger}. NaN ve sonsuzluk ÖLÇÜLMEMİŞ "
            "sayılardır; bir stok miktarı olamazlar.",
        )
    if deger < 0:
        raise PartiSecilemedi(sebep, f"{ad} negatif: {deger}")
    return deger


def _sira_anahtari(parti: Parti) -> tuple[int, date, datetime, int]:
    """FEFO sırası: (SKT VAR MI, SKT, `created_at`, `id`).

    İLK ELEMAN NULL-SON KURALIDIR ve bir sıralama HİLESİ DEĞİL, kararın
    kendisidir: `expiry_date` NULL olan parti `1` alır, olan `0` alır, yani
    tarihi olanların HEPSİ tarihi olmayanların ÖNÜNDE gelir — tarihi ne kadar
    uzak olursa olsun.

    `None` ile `date`i doğrudan karşılaştırmak Python'da `TypeError`dır;
    o yüzden NULL için `date.min` yer tutucu KONUR, AMA sıralamadaki yerini
    o değil ÖNÜNDEKİ `1` belirler. Yer tutucuyu `date.max` yapmak da AYNI
    sonucu verirdi ve tam da bu yüzden ona GÜVENİLMEDİ: iki farklı mekanizma
    aynı cevabı verdiğinde, birini bozan mutasyon sessiz kalır. `date.min`
    seçilerek NULL-son kuralı YALNIZ ilk elemana bağlandı ve o eleman
    silindiğinde sıra GÖRÜNÜR biçimde ters döner (testte çivili).
    """
    if parti.expiry_date is None:
        return (1, date.min, parti.created_at, parti.id)
    return (0, parti.expiry_date, parti.created_at, parti.id)


def fefo_sec(
    lots: Sequence[Parti],
    needed: Decimal,
    *,
    bugun: date,
    izin_ver_suresi_gecmis: bool = False,
) -> Secim:
    """`needed` kadar malı FEFO sırasıyla partilere BÖLÜŞTÜRÜR.

    Döner: ``Secim(dagitim, suresi_gecmis)``. ``dagitim`` FEFO SIRASINDA
    ``(lot_id, miktar)`` çiftleridir ve toplamı ``needed``e TAM OLARAK
    eşittir — bu dosya hiçbir yerde çarpmaz, yalnız bölüştürür.

    ``bugun`` ZORUNLUDUR: bu fonksiyon SAAT OKUMAZ (bkz. başlık).

    SÜRESİ GEÇMİŞ partiler ``izin_ver_suresi_gecmis=False`` (VARSAYILAN) iken
    dağıtıma GİRMEZ ama ``Secim.suresi_gecmis`` içinde RAPOR EDİLİR. "Mal
    yok" ile "mal var ama süresi geçmiş" aynı şey değildir ve reddin bu
    bilgiyi yok etmesine izin verilmez.

    ``needed == 0`` BOŞ bir dağıtım verir ve REDDEDİLMEZ — gerçek bir sıfır
    gerçek bir istektir (``units.resolve``ın sıfır girdisiyle aynı duruş).

    Miktarı SIFIR olan partiler dağıtıma GİRMEZ: sıfırlık bir pay, yazılacak
    hiçbir şeyi olmayan bir hareket satırı üretirdi. Satırın KENDİSİ silinmez
    (göç 0067, ``CHECK quantity >= 0``), yalnız seçimden düşer.

    SONLU OLMAYAN ve NEGATİF girdiler REDDEDİLİR; reddin TAMAMI
    ``PartiSecilemedi`` ailesinin İÇİNDEDİR — ``decimal.InvalidOperation``
    DIŞARI SIZMAZ. ``needed`` için sebep ``ISTENEN_GECERSIZ`` (GİRDİ kusuru),
    bir partinin miktarı için ``PARTI_MIKTARI_GECERSIZ`` (DEFTER kusuru);
    ayrımın gerekçesi başlıktadır.

    Yeterli uygun mal yoksa ``ParticiYetersiz`` atılır ve kanıt (istenen,
    mevcut, eksik, süresi geçmişler) istisnanın ÜZERİNDE taşınır.
    """
    istenen = _sonlu_miktar(needed, PartiSecilemedi.ISTENEN_GECERSIZ, "needed")

    gorulen: set[int] = set()
    for parti in lots:
        if parti.id in gorulen:
            raise PartiSecilemedi(
                PartiSecilemedi.PARTI_YINELENEN_KIMLIK,
                f"parti kimliği {parti.id} listede İKİ KEZ geçiyor. Aynı "
                "satırın iki kopyası, ondan İKİ KEZ düşülmesine yol açardı ve "
                "toplam elde olandan fazla çıkardı.",
            )
        gorulen.add(parti.id)
        _sonlu_miktar(
            parti.quantity,
            PartiSecilemedi.PARTI_MIKTARI_GECERSIZ,
            f"parti {parti.id} miktarı",
        )

    # --- SÜRESİ GEÇMİŞ AYRIMI ---------------------------------------------
    # `< bugun`, `<=` DEĞİL: SKT SON KULLANILABİLİR GÜNDÜR. Bkz. başlık.
    suresi_gecmis = tuple(
        parti
        for parti in sorted(lots, key=_sira_anahtari)
        if parti.expiry_date is not None and parti.expiry_date < bugun
    )

    if izin_ver_suresi_gecmis:
        uygun = list(lots)
    else:
        gecmis_kimlikler = {parti.id for parti in suresi_gecmis}
        uygun = [parti for parti in lots if parti.id not in gecmis_kimlikler]

    mevcut = sum((parti.quantity for parti in uygun), Decimal("0"))
    if mevcut < istenen:
        raise ParticiYetersiz(istenen, mevcut, suresi_gecmis)

    dagitim: list[tuple[int, Decimal]] = []
    kalan = istenen
    for parti in sorted(uygun, key=_sira_anahtari):
        if kalan == 0:
            break
        # Sıfır miktarlı parti yazılacak hiçbir şeyi olmayan bir satır üretir.
        if parti.quantity == 0:
            continue
        pay = parti.quantity if parti.quantity < kalan else kalan
        dagitim.append((parti.id, pay))
        kalan -= pay

    # Yeterlilik YUKARIDA denetlendi; buraya sıfır olmayan bir kalanla
    # gelinmesi seçicinin KENDİ hatası olurdu ve sessiz kalması, eksik mal
    # düşülmesi demek olurdu. Sessizlik yerine çökme.
    assert kalan == 0, f"bölüştürme {kalan} artık bıraktı (istenen {istenen})"

    return Secim(tuple(dagitim), suresi_gecmis)
