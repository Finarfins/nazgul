"""PARTİ MUTABAKATI — `warehouse_stocks` ile `product_lots` ARASINDAKİ FARKI OKUR.

Konu: 1B-G. GÖÇ YOKTUR ve bu dilim hiçbir şey YAZMAZ: 0067'nin parti tablosunu,
0073'ün deposunu ve `warehouse_stocks`u OLDUKLARI GİBİ okur.

--- HANGİ SAYI DOĞRU: STOK ------------------------------------------------

Bu deponun stok OTORİTESİ `warehouse_stocks.quantity`dir, (depo, ürün) başına
tek satır. `products.stock` ONUN TOPLAMIDIR ve `inventory.sync_product_stock`
tarafından TEK bir SQL ifadesiyle türetilir — yani üçüncü bir sayı değildir.

`product_lots` ise ÜÇÜNCÜ BİR DEFTER DEĞİL, İKİNCİ BİR KIRILIMDIR ve stoktan
AYRILABİLİR. Bu ayrılma bir kusur DEĞİL, 0067'nin TASARIM KARARIDIR: parti
defteri 1B-A ile açıldı ve stoğa dokunan yolların YALNIZ BİR KISMI ona bağlandı.
Geri kalanı (aşağıda ADIYLA sayılı) partisiz yazmaya DEVAM EDER.

Bu modül o farkı ÖLÇER ve KİMİN kusuru olduğunu SÖYLER. Ölçmeseydi fark yine
var olurdu; tek değişen, kimsenin GÖREMEYECEK olmasıydı.

--- NEDEN BİR VERİTABANI KISITI DEĞİL ------------------------------------

"Madem iki sayı eşit olmalı, bir `CHECK` koy" sorusunun cevabı ÜÇ KATLIDIR ve
üçü de ölçüldü (`docs/PARTI_MUTABAKAT.md` uzun halini taşır):

  1. KISIT İKİ TABLOYU BİRDEN OKUYAMAZ. `warehouse_stocks.quantity` ile
     `SUM(product_lots.quantity)` AYRI tablolardadır; satır içi bir `CHECK`
     başka bir tabloya bakamaz. Kalan tek araç TETİKLEYİCİDİR ve bu depoda
     tetikleyici YOKTUR — göç zinciri saf DDL'dir.
  2. SQLite EŞLİĞİ. Kısıt PostgreSQL'de kurulup SQLite'ta kurulamasaydı, iki
     diyalekt İKİ FARKLI ŞEMA olurdu ve testlerin yeşili üretimin yeşilini
     TEMSİL ETMEZDİ. Bu depo o eşliği ayrı bir kapıyla koruyor.
  3. KISIT YANLIŞ CEVABI VERİRDİ. Aşağıdaki lot-suz yollar KUSUR DEĞİLDİR;
     bir kısıt onları REDDEDERDİ ve reddettiği şey ÇALIŞAN bir iş akışı
     olurdu. Doğru cevap "yaz" ile "yazma" arasında değil, GÖRÜNÜR KILMAK ile
     GİZLEMEK arasındadır.

Yani bu modül bir kısıtın YERİNE GEÇMİYOR; kısıtın KURULAMAYACAĞI yerde
kurulabilecek TEK şeyi kuruyor: ÖLÇÜLEBİLİR bir rapor.

--- ÜÇ KOVA VE NEDEN ÜÇ (İKİ DEĞİL) --------------------------------------

İki kova (EŞİT / EŞİT DEĞİL) yazmak KOLAYDI ve tam bu yüzden REDDEDİLDİ:
bugün stoğa partisiz dokunan yolların HEPSİ "eşit değil" kovasına düşerdi,
kova ANINDA yüzlerce satırla dolardı ve içindeki TEK gerçek kusur o gürültüde
GÖRÜNMEZ olurdu. Gürültüyle dolu bir uyarı, uyarı DEĞİLDİR.

  * ``ESIT``            — parti satırı VAR ve toplamı stoğa TAM eşit.
                          Defterin bu çift için TAM olduğu tek durum.
  * ``LOTSUZ_TASARIM``  — bu çift için HİÇ parti satırı AÇILMAMIŞ. Fark
                          BEKLENENDİR (aşağıdaki yollar), kusur DEĞİLDİR.
  * ``SAPMA``           — parti satırı VAR ama toplam TUTMUYOR; ya da parti
                          toplamı stoğu AŞIYOR. İNCELENMESİ GEREKEN tek kova.

--- `LOTSUZ_TASARIM` "parti toplamı 0" DEĞİL, "PARTİ SATIRI YOK"tur --------

BU AYRIM SESSİZ BIRAKILAMAZDI ve mekanizması `parti_toplami == 0` OLSAYDI
kovanın ADI YALAN SÖYLERDİ.

Sebep ölçülebilir: 1B-B bir partiyi SONUNA KADAR tükettiğinde satırı SİLMEZ,
`quantity`sini 0'a çeker — ve satırın kalması 0067'nin KARARIDIR, çünkü o satır
geri çağırmanın KANITIDIR. Yani tükenmiş bir parti için `parti_toplami == 0` ve
`stok == 0`dır. Mekanizma "toplam 0" olsaydı bu çift `LOTSUZ_TASARIM`a düşerdi
ve kova "bu çift için hiç parti açılmadı" DERKEN, açılmış VE düzgün kapanmış bir
partiyi gösterirdi. Doğru cevap ``ESIT``tir: iki defter de 0 diyor, ikisi de
DOĞRU ve UYUŞUYOR.

Ayrım bu yüzden SATIR SAYISINDADIR (`COUNT(*)`), toplamda değil. `SAPMA`nın
tanımı zaten "parti VARSA" diyor; `LOTSUZ_TASARIM`ı aynı yüklemin diğer yüzüne
bağlamak, üç kovayı TEK bir olguya (satır var mı) oturtur.

--- `parti_toplami > stok` KOŞULSUZDUR ve bu bir kaçak kapatmadır ---------

`SAPMA`nın ikinci dalı `LOTSUZ_TASARIM`ın İÇİNDEN de ısırabilir ve ISIRMASI
GEREKİR: parti satırı yokken toplam 0'dır, yani `0 > stok` ANCAK stok NEGATİFSE
doğrudur. Negatif stok bu depoda POLİTİKAYLA serbest bırakılabilir
(`negative_stock_policy='allow'`), ama "eksi stok" ile "bu çift bilinçli olarak
partisizdir" AYNI CÜMLE DEĞİLDİR. Koşulu `LOTSUZ_TASARIM`ın arkasına koymak,
eksi bakiyeyi "tasarım" diye etiketleyip GÖMERDİ.

Sıra bu yüzden ZORUNLUDUR ve üslup değildir:

    ÖNCE `SAPMA` denetimi  ->  SONRA `LOTSUZ_TASARIM`  ->  KALAN `ESIT`

--- BUGÜN PARTİSİZ YAZAN YOLLAR (TASARIM, KUSUR DEĞİL) --------------------

`docs/PARTI_MUTABAKAT.md` bunları 1B-H için ADIYLA listeler. Özet: faaliyet
girdileri, kantar fişi farkları, iş emri parçaları, partisiz transfer/sayım/
iade/alış/satış ve KAYNAKSIZ satış iadesi. Hepsi `warehouse_stocks`a yazar,
hiçbiri `product_lots`a dokunmaz.

--- SORGU TEK VE DİYALEKT DALI YOK ----------------------------------------

Aynı metin İKİ diyalektte de koşar ve bu ÖLÇÜLDÜ (`tests/test_1b_g_mutabakat.py`
metnin içinde diyalekt adı ARAMIYOR, `test_1b_g_mutabakat_postgresql.py` aynı
davranışı GERÇEK PostgreSQL'de tekrar ölçüyor).

İki tuzak ADIYLA atlatıldı:

  * `FULL OUTER JOIN` YAZILMADI. SQLite onu 3.39'dan önce TANIMIYOR ve sürüm
    koşusu koşan bir kapı, koştuğu makineye göre yeşil olurdu. Yerine
    sürücü küme bir `UNION`dur — iki tarafın çiftleri toplanır, `UNION` (ALL
    DEĞİL) tekrarı düşürür, sonra İKİ TARAFA da `LEFT JOIN` atılır.
  * `GROUP BY` alt sorguda SEÇİLEN her sütunu SAYIYOR. PostgreSQL'in işlevsel
    bağımlılık kuralı, SQLite'ın gevşekliğinden DAR'dır; dar olana göre yazmak
    iki diyalekti de karşılar, tersi karşılamaz.

--- KOVA PYTHON'DA HESAPLANIR, SQL'DE `CASE` İLE DEĞİL --------------------

`app/parti.py`nin NULL-son kuralıyla AYNI gerekçe: kural SQL'e bırakılsaydı iki
diyalekt onu iki farklı yerde taşırdı ve sayısal karşılaştırma (`stok !=
parti_toplami`) diyalektin döndürdüğü TİPE bağlı olurdu — PostgreSQL `NUMERIC`i
`Decimal` verir, SQLite aynı sütun için `float` verebilir ve `0.1+0.2 != 0.3`
SESSİZ bir `SAPMA` uydururdu.

Bu yüzden her iki sayı da karşılaştırmadan ÖNCE `money.quantity` ile AYNI
kuantuma (`NUMERIC(18,4)`) çekilir. Kuantum burada YENİDEN TANIMLANMADI, ithal
edildi — ikinci bir kopya ayrışırdı.

--- SAYIMLAR SAYFANIN DEĞİL KİRACININ TAMAMINDANDIR ----------------------

Uç SAYFALIDIR ama `sayimlar` sayfaya DEĞİL, kiracının TAMAMINA aittir ve bu
bilinçli bir maliyet: kova kuralı Python'da durduğu için sayabilmek TÜM çiftleri
okumayı gerektirir. Alternatif, kuralı bir de SQL `CASE`i olarak yazmaktı — yani
kuralı İKİ YERE koymak. İki kopya ayrıştığı gün rapor kendi sayacıyla çelişirdi
ve hangisinin doğru olduğu SORULAMAZDI. Tek kaynak, ölçülebilir bir bedelle
korundu; bedel burada YAZILI, gizli değil.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import quantity

__all__ = [
    "ESIT",
    "KOVALAR",
    "LOTSUZ_TASARIM",
    "SAPMA",
    "Mutabakat",
    "Satir",
    "kova_sec",
    "mutabakat",
]

#: Parti satırı VAR ve toplamı stoğa TAM eşit.
ESIT = "ESIT"
#: Bu çift için HİÇ parti satırı açılmamış; fark BEKLENENDİR.
LOTSUZ_TASARIM = "LOTSUZ_TASARIM"
#: Parti satırı var ama toplam tutmuyor; ya da parti toplamı stoğu aşıyor.
SAPMA = "SAPMA"

#: Kova adları SABİT SIRADA. Sıra raporun okunuşudur (iyiden kötüye) ve
#: `sayimlar` sözlüğü HER ÇAĞRIDA ÜÇÜNÜ DE taşır — eksik anahtar, sıfır sayıyı
#: "ölçülmedi"den ayırt edilemez yapardı.
KOVALAR = (ESIT, LOTSUZ_TASARIM, SAPMA)


class Satir(NamedTuple):
    """Bir (ürün, depo) çiftinin İKİ defterdeki hali, yan yana.

    `fark` TÜRETİLMİŞTİR (`stok - parti_toplami`) ve yine de TAŞINIR: çağıranın
    onu yeniden hesaplaması, işaret yönünü (hangisinden hangisi çıkarılıyor)
    ikinci bir yerde tanımlamak olurdu ve iki tanım ayrıştığı gün rapor kendi
    işaretiyle çelişirdi.

    İŞARET YÖNÜ: `fark > 0` stok partiden FAZLA demektir (partisiz bir giriş
    olmuştur — çoğunlukla `LOTSUZ_TASARIM`). `fark < 0` parti stoktan fazla
    demektir ve TEHLİKELİ olan yön BUDUR: defter, elde olmayan malı VAR
    gösteriyor.
    """

    product_id: int
    warehouse_id: int
    stok: Decimal
    parti_toplami: Decimal
    fark: Decimal
    kova: str


class Mutabakat(NamedTuple):
    """Sayfa + KİRACININ TAMAMINA ait sayımlar.

    `sayimlar` sayfadan türetilemez ve türetilmemelidir: ikinci sayfadaki tek
    `SAPMA`yı ilk sayfaya bakan bir operatör GÖRMEZDİ ve "sapma yok" diye
    okurdu. Sayım bu yüzden sayfadan BAĞIMSIZ ölçülür.
    """

    satirlar: tuple[Satir, ...]
    sayimlar: dict[str, int]
    toplam: int
    has_more: bool


# Sürücü küme `UNION`dur, `FULL OUTER JOIN` DEĞİL (SQLite 3.39 öncesi onu
# tanımıyor — bkz. başlık). `UNION ALL` DEĞİL: iki tarafta da bulunan çift İKİ
# KEZ gelirdi ve aynı çift raporda iki satır olurdu.
#
# Kiracı yüklemi ÜÇ YERDE de LİTERAL: iki `UNION` kolunda ve parti toplamı alt
# sorgusunda. `LEFT JOIN`ler kiracıyı sürücü kümeden DEVRALIR
# (`ws.company_id=p.company_id`), yani yüklem birleştirmenin İÇİNDE kurulur —
# `WHERE`e bırakılan bir dış-birleştirme yüklemi, birleştirmeyi sessizce İÇ
# birleştirmeye çevirirdi.
_MUTABAKAT_SQL = """SELECT p.product_id, p.warehouse_id,
    COALESCE(ws.quantity, 0) AS stok,
    COALESCE(lt.toplam, 0) AS parti_toplami,
    COALESCE(lt.satir_sayisi, 0) AS parti_satir_sayisi
FROM (
    SELECT company_id, product_id, warehouse_id
    FROM warehouse_stocks WHERE company_id = :cid
    UNION
    SELECT company_id, product_id, warehouse_id
    FROM product_lots WHERE company_id = :cid
) p
LEFT JOIN warehouse_stocks ws
    ON ws.company_id = p.company_id
   AND ws.product_id = p.product_id
   AND ws.warehouse_id = p.warehouse_id
LEFT JOIN (
    SELECT company_id, product_id, warehouse_id,
           SUM(quantity) AS toplam, COUNT(*) AS satir_sayisi
    FROM product_lots WHERE company_id = :cid
    GROUP BY company_id, product_id, warehouse_id
) lt
    ON lt.company_id = p.company_id
   AND lt.product_id = p.product_id
   AND lt.warehouse_id = p.warehouse_id
ORDER BY p.product_id, p.warehouse_id"""


def kova_sec(stok: Decimal, parti_toplami: Decimal, parti_satir_sayisi: int) -> str:
    """Üç kovadan BİRİNİ seçer. SIRA ZORUNLUDUR (bkz. başlık).

    `SAPMA` ÖNCE denetlenir çünkü ikinci dalı (`parti_toplami > stok`)
    `LOTSUZ_TASARIM`ın İÇİNDEN de ısırabilir: parti satırı yokken toplam 0'dır
    ve `0 > stok` ancak NEGATİF stokta doğrudur. Sıra ters olsaydı eksi bakiye
    "tasarım" etiketiyle GÖMÜLÜRDÜ.

    `LOTSUZ_TASARIM` SATIR SAYISINA bakar, toplama DEĞİL: tükenmiş (0'a inmiş
    ama SİLİNMEMİŞ) bir parti `ESIT`tir, "hiç parti açılmadı" DEĞİL.
    """
    if (parti_satir_sayisi > 0 and stok != parti_toplami) or parti_toplami > stok:
        return SAPMA
    if parti_satir_sayisi == 0:
        return LOTSUZ_TASARIM
    return ESIT


def mutabakat(
    db: Session,
    company_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
) -> Mutabakat:
    """Kiracının HER (ürün, depo) çifti için iki defteri karşılaştırır.

    SAF OKUMADIR: hiçbir `INSERT`/`UPDATE`/`DELETE` yoktur ve olmaması
    kapıdadır — `product_lots`un yazma tekeli `app/parti_defteri.py`dedir ve
    bu modülün oraya ikinci bir kapı açması, 1B-A'nın AST kapısını kırardı.

    Sayfa `limit`/`offset` ile kesilir; `sayimlar` ve `toplam` SAYFAYA DEĞİL
    kiracının TAMAMINA aittir (gerekçesi `Mutabakat`ta).

    `bugun` OKUNMAZ ve SKT'ye BAKILMAZ: süresi geçmiş parti hâlâ ELDEKİ maldır
    ve stokta da öyle sayılır. Mutabakat "mal var mı" sorusunu sorar, "mal
    kullanılabilir mi" sorusunu DEĞİL — ikincisi `app/parti.py`nin işidir ve
    ikisini burada karıştırmak, süresi geçmiş her partiyi sahte bir `SAPMA`
    yapardı.
    """
    if limit < 1:
        raise ValueError("limit en az 1 olmalı")
    if offset < 0:
        raise ValueError("offset negatif olamaz")

    satirlar: list[Satir] = []
    sayimlar = {kova: 0 for kova in KOVALAR}
    for ham in db.execute(text(_MUTABAKAT_SQL), {"cid": int(company_id)}).mappings():
        # HER İKİ SAYI DA karşılaştırmadan ÖNCE aynı kuantuma çekilir; SQLite
        # aynı sütun için `float` döndürebilir ve ham karşılaştırma sessiz bir
        # `SAPMA` uydururdu (bkz. başlık).
        stok = quantity(ham["stok"])
        parti_toplami = quantity(ham["parti_toplami"])
        kova = kova_sec(stok, parti_toplami, int(ham["parti_satir_sayisi"]))
        sayimlar[kova] += 1
        satirlar.append(
            Satir(
                product_id=int(ham["product_id"]),
                warehouse_id=int(ham["warehouse_id"]),
                stok=stok,
                parti_toplami=parti_toplami,
                fark=stok - parti_toplami,
                kova=kova,
            )
        )

    sayfa = satirlar[offset : offset + limit]
    return Mutabakat(
        satirlar=tuple(sayfa),
        sayimlar=sayimlar,
        toplam=len(satirlar),
        has_more=offset + len(sayfa) < len(satirlar),
    )
