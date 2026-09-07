"""PARTİ DEFTERİ — `product_lots`a YAZAN TEK MODÜL ve FEFO seçicisinin TEK ÇAĞIRANI.

Konu: göç `20260903_0067` (tablo + `app/parti.py` seçicisi), `20260908_0073`
(depo + alış kalemi). 1B-A alış yolunu, 1B-B satış/çıkış yolunu getirdi.

--- BU MODÜL NEDEN VAR -----------------------------------------------------

1B-A'da bu iki yazıcı (`_parti_ac`, `_parti_geri_al`) `routers/transactions.py`
İÇİNDEYDİ ve kapı "defterin TEK yazıcısı transactions.py'dir" diyordu. O sınır
1B-B ile ARTIK TUTMUYOR: satış yolu `routers/workflow.py`den de (irsaliye,
alış iadesi) parti tüketiyor. İki seçenek vardı ve İKİNCİSİ seçildi:

  (a) Kapıyı GENİŞLET: "yazıcılar transactions.py VE workflow.py". Bu, defteri
      İKİ dosyadan yazılabilir yapardı ve `app/parti.py`nin başlığında ADIYLA
      reddedilen kusurun ta kendisidir: "iki farklı yerden çıkan mal iki farklı
      partiden düşerse geri çağırma kaydı YALAN SÖYLER".
  (b) Yazıcıları TEK BİR MODÜLE topla, yönlendiriciler onu ÇAĞIRSIN. Kapı
      DARALIR, genişlemez: yazıcı bir dosyadır ve o dosya bir yönlendirici
      DEĞİLDİR, yani bir uç eklemek defteri kendiliğinden açmaz.

`fefo_sec` de BURADAN ve YALNIZ buradan çağrılır. Seçiciyi iki yönlendiriciden
ayrı ayrı çağırmak, sıralamanın iki çağıranın ihtiyacına göre AYRIŞMASINA yol
açardı — `app/parti.py` bu dosyadan önce yazılmasının gerekçesini tam olarak
bu ayrışma üzerine kuruyor.

--- SINIR TESTTE ÇİVİLİ ----------------------------------------------------

`tests/test_1b_a_alis_lot.py`:
  * `test_product_lots_YAZICISI_YALNIZ_parti_defteri_py` — `product_lots`a
    YAZAN çalıştırılabilir metin yalnız burada.
  * `test_fefo_sec_CAGIRANI_YALNIZ_parti_defteri_py` — seçicinin tek çağıranı.
  * Defteri ANAN dosyaların kümesi KAPALIDIR (yazıcı + okuma uçları).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import NamedTuple

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth import utcnow
from .money import quantity
from .parti import Parti, ParticiYetersiz, PartiSecilemedi, Secim, fefo_sec

__all__ = [
    "DEFTER_BOSALDI_DAMGASI",
    "SKT_SORULMADI",
    "SURESI_GECMIS_DAMGASI",
    "PartiSatiri",
    "Tuketim",
    "_hareket_notu",
    "_parti_ac",
    "_parti_bul",
    "_parti_dus",
    "_parti_geri_al",
    "_parti_tuket",
]


class PartiSatiri(NamedTuple):
    """`_parti_bul`un cevabı: KİMLİK ve ELDEKİ MİKTAR, birlikte.

    İkisi AYRI iki çağrıyla alınabilirdi ve ALINMADI: sayım yolu "bu partide
    sistemde ne yazıyor" sorusunu sorar ve cevabı kimlikten AYRI okusaydı,
    iki okuma arasında satır değişebilirdi. Tek okuma tek cevaptır.
    """

    id: int
    quantity: Decimal


class _SktSorulmadi:
    """`None`DAN AYRI bir yokluk. Tekil ve karşılaştırması KİMLİKLEDİR."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - yalnız hata ayıklama
        return "SKT_SORULMADI"


#: Çağıranın SKT hakkında HİÇBİR ŞEY söylemediği anlamına gelir. `None` ise
#: "SKT'si YOKTUR" BEYANIDIR ve var olan TARİHLİ bir partiyle ÇELİŞİR.
#:
#: AYRIM 1B-C'DE ÖLÇÜLDÜ VE ZORUNLUDUR: sayım yolu bir parti kodu sayar ama
#: SKT'yi SORMAZ, yani `None` göndermesi "bu partinin son kullanma tarihi
#: yoktur" DİYE OKUNURDU ve tarihli bir partiyi saymak 422 `LOT_SKT_CELISKI`
#: ile REDDEDİLİRDİ — sayım, saydığı malı reddetmiş olurdu. Varsayılan bu
#: sentineldir; `None` göndermek AÇIK bir beyandır ve 1B-A'nın çatışma kuralı
#: onun için AYNEN geçerlidir.
SKT_SORULMADI = _SktSorulmadi()


# ---------------------------------------------------------------------------
# PARTİ DEFTERİ — 1B-A: ALIŞ KALEMİ LOT AÇAR
#
# `product_lots` 0067'de kuruldu ve ÇAĞIRANI YOKTU. Bu modüldeki fonksiyonlar
# o defterin TEK yazıcısıdır ve bu DOSYANIN DIŞINDA bir yazıcı YOKTUR —
# `tests/test_1b_a_alis_lot.py` bunu AST ile çiviliyor. Okuma tarafı
# (`GET /api/products/{id}/lots`) defteri yalnız SEÇER.
#
# --- ÖLÇEK: PARTİ MİKTARI = HAREKET MİKTARI -------------------------------
#
# `units.resolve` BURADA ÇAĞRILMIYOR ve bu bir eksiklik değil, KAPSAM
# kararıdır. Alış yolu bugün ham birimle çalışır: `purchase_items.quantity`
# kullanıcının girdiği sayıdır ve `stock_movements.quantity` de aynı sayıyı
# taşır. Parti defterine ÜÇÜNCÜ bir ölçek sokmak, üç sayının hangisinin
# doğru olduğunu sorulamaz yapardı.
#
# Yani bu dilimde ÇİVİ ŞUDUR: parti miktarının ölçeği HAREKETİN ölçeğidir.
# Alış yolu bir gün taban birime çekilirse (ÖLÇÜLMEDİ), parti defteri o
# değişikliğin İÇİNDE kalır çünkü aynı sayıyı yazıyor.
# ---------------------------------------------------------------------------
def _parti_ac(
    db: Session,
    cid: int,
    *,
    product_id: int,
    warehouse_id: int,
    lot_code: str,
    expiry_date: "str | None | _SktSorulmadi" = SKT_SORULMADI,
    miktar,
) -> int:
    """Partiyi AÇ ya da VAR OLANA EKLE; kimliğini döndür.

    Tekillik `(company_id, product_id, lot_code, warehouse_id)`tır (göç
    20260908_0073). Aynı kod BAŞKA bir depoda AYRI satırdır ve bu kasıtlıdır:
    üretici bir partiyi iki şubeye bölebilir ve "hangi depoda ne kadar var"
    sorusu sorulabilir kalmalıdır.

    SKT ÇATIŞMASI 422'dir, sessiz kabul DEĞİL: var olan bir partinin SKT'si
    ile yeni girdinin söylediği SKT ayrışıyorsa iki cümleden biri YALANDIR ve
    hangisi olduğunu depo bilemez. Var olanı ezmek geçmiş hareketleri
    yeniden yorumlardı; yeni geleni yok saymak ise operatöre yazdığı şeyin
    kaydedilmediğini SÖYLEMEZDİ.

    `expiry_date` VERİLMEZSE (`SKT_SORULMADI`, VARSAYILAN) çatışma denetimi
    HİÇ ÇALIŞMAZ ve YENİ satır SKT'siz açılır. Sentinelin `None`dan ayrı
    durmasının gerekçesi sabitin yanındadır; özeti: `None` bir BEYANDIR
    ("SKT'si yoktur") ve tarihli bir partiyle ÇELİŞİR, oysa sayım yolu SKT
    hakkında hiçbir şey söylemez. Alış yolu (1B-A) argümanı AÇIKÇA yazar,
    yani onun için çatışma kuralı BİREBİR eskisi gibidir.
    """
    varolan = db.execute(
        text(
            "SELECT id,expiry_date FROM product_lots "
            "WHERE company_id=:cid AND product_id=:pid AND lot_code=:kod "
            "AND warehouse_id=:wid"
        ),
        {"cid": cid, "pid": product_id, "kod": lot_code, "wid": warehouse_id},
    ).mappings().first()
    # SENTİNEL BURADA `None`A DÜŞÜYOR: sütun NULL kabul ediyor ve "sorulmadı"
    # ile "yoktur" DEFTERDE aynı satırı üretir. Ayrım YAZMADA değil
    # KARŞILAŞTIRMADA anlamlıdır (aşağıdaki dal), yani sentinel veritabanına
    # HİÇ inmez ve üçüncü bir durum uydurulmaz.
    beyan = None if isinstance(expiry_date, _SktSorulmadi) else expiry_date
    if varolan is None:
        yeni = db.execute(
            text(
                "INSERT INTO product_lots("
                "company_id,product_id,lot_code,expiry_date,quantity,"
                "warehouse_id,created_at) "
                "VALUES(:cid,:pid,:kod,:skt,:miktar,:wid,:now) RETURNING id"
            ),
            {
                "cid": cid,
                "pid": product_id,
                "kod": lot_code,
                "skt": beyan,
                "miktar": miktar,
                "wid": warehouse_id,
                "now": utcnow(),
            },
        )
        return int(yeni.scalar_one())

    if not isinstance(expiry_date, _SktSorulmadi):
        # KARŞILAŞTIRMA METİN ÜZERİNDE: `expiry_date` PostgreSQL'de `date`,
        # SQLite'ta `str` olarak geri gelir ve ikisi doğrudan karşılaştırılamaz.
        # ÖLÇÜLDÜ — iki diyalekt aynı satırda iki farklı tip verir.
        mevcut_skt = varolan["expiry_date"]
        mevcut_metin = (
            mevcut_skt.isoformat() if hasattr(mevcut_skt, "isoformat") else mevcut_skt
        )
        if (mevcut_metin or None) != (beyan or None):
            raise HTTPException(
                422,
                {
                    "code": "LOT_SKT_CELISKI",
                    "message": (
                        f"`{lot_code}` partisi bu depoda "
                        f"{mevcut_metin or 'SKT’siz'} olarak kayıtlı; girdi "
                        f"{beyan or 'SKT’siz'} diyor. İki tarihten hangisinin "
                        "doğru olduğunu depo bilemez — partiyi ya da girdiyi "
                        "düzeltin."
                    ),
                },
            )
    db.execute(
        text(
            "UPDATE product_lots SET quantity=quantity+:miktar "
            "WHERE company_id=:cid AND id=:id"
        ),
        {"miktar": miktar, "cid": cid, "id": int(varolan["id"])},
    )
    return int(varolan["id"])


def _parti_bul(
    db: Session,
    cid: int,
    *,
    product_id: int,
    warehouse_id: int,
    lot_code: str,
) -> PartiSatiri | None:
    """Partiyi BUL: kimliği ve eldeki miktarı; yoksa `None`. YAZMAZ.

    `_parti_ac` BU İŞİ GÖREMEZ ve ayrı durmasının sebebi budur: `_parti_ac`
    bulamadığı partiyi AÇAR. Eksi yönlü bir ayarlama ise var OLMAYAN bir
    partiden mal düşemez ve o durumda 409 vermelidir — `_parti_ac` ile
    sorulsaydı, olmayan parti SESSİZCE açılır ve ardından sıfırdan düşülmeye
    çalışılırdı.

    Tekillik `(company_id, product_id, lot_code, warehouse_id)`tır (göç
    20260908_0073), yani bu sorgu EN FAZLA bir satır görür.

    `None` ile SIFIR MİKTARLI SATIR AYNI ŞEY DEĞİLDİR: birincisi "böyle bir
    parti bu depoda hiç açılmadı", ikincisi "açıldı ve tükendi" der. Sayım
    yolu ikisini de sıfır sayar ama fark defterde durur.
    """
    satir = db.execute(
        text(
            "SELECT id,quantity FROM product_lots "
            "WHERE company_id=:cid AND product_id=:pid AND lot_code=:kod "
            "AND warehouse_id=:wid"
        ),
        {"cid": cid, "pid": product_id, "kod": lot_code, "wid": warehouse_id},
    ).mappings().first()
    if satir is None:
        return None
    return PartiSatiri(int(satir["id"]), quantity(satir["quantity"]))


def _parti_dus(db: Session, cid: int, *, lot_id: int, miktar, care: str) -> None:
    """BELLİ BİR partiden `miktar` kadar DÜŞ. EKSİYE DÜŞÜRMEZ, 409 ATAR.

    `_parti_tuket`TEN AYRI DURUR VE AYRILMASI KARARDIR: o FEFO ile hangi
    partiden düşüleceğine KARAR VERİR, bu ise partiyi ÇAĞIRANDAN alır.
    Ayarlama ve sayım yollarında parti SEÇİLMEZ, operatör tarafından ADIYLA
    YAZILIR — oraya bir seçici koymak, operatörün yazdığı koddan BAŞKA bir
    partiyi düşürebilirdi.

    KORUMA YAZMANIN KENDİ `WHERE`ÜNDE ve bu `_parti_tuket`in ölçülmüş
    dersidir: okuma ile yazma arasındaki aralıkta başka bir işlem aynı
    partiyi tüketmiş olabilir, yani "önce SELECT, sonra UPDATE" iki eşzamanlı
    ayarlamayı ayırt EDEMEZ. Koşul `WHERE`e girmeseydi bu satır partiyi
    eksiye indirirdi: PostgreSQL'de 0067'nin `CHECK`i ile `IntegrityError`,
    SQLite'ta SESSİZCE.

    OKUMA YALNIZ MESAJ İÇİNDİR ve YAZMADAN SONRA yapılır: veritabanının reddi
    operatöre NE YAPACAĞINI söylemez, `IntegrityError` söyler. `care`
    ÇAĞIRANDAN gelir çünkü çare çağırana göre değişir (belgeyi geri alan önce
    tüketimi geri almalı; ayarlama yapan sayıyı düzeltmeli). Kuralın KENDİSİ
    ve hata KODU çağırana göre DEĞİŞMEZ.
    """
    dusecek = quantity(miktar)
    sonuc = db.execute(
        text(
            "UPDATE product_lots SET quantity=quantity-:miktar "
            "WHERE company_id=:cid AND id=:id AND quantity>=:miktar"
        ),
        {"miktar": dusecek, "cid": cid, "id": lot_id},
    )
    if sonuc.rowcount == 1:
        return
    kalan = db.execute(
        text("SELECT quantity FROM product_lots WHERE company_id=:cid AND id=:id"),
        {"cid": cid, "id": lot_id},
    ).scalar_one_or_none()
    raise HTTPException(
        409,
        {
            "code": "LOT_MIKTARI_EKSIYE_DUSER",
            "message": (
                f"#{lot_id} partisinde düşülecek {dusecek} birim YOK "
                f"(elde {quantity(kalan) if kalan is not None else 0}). "
                + care
            ),
        },
    )


def _parti_geri_al(db: Session, cid: int, *, reference_type: str, reference_id: int) -> None:
    """Silinmek ÜZERE olan hareketlerin parti borcunu defterden DÜŞ.

    ÇAĞRILDIĞI YER ZORUNLUDUR: `DELETE FROM stock_movements`ten ÖNCE. Sonra
    çağrılsaydı okunacak satır kalmazdı ve defter, artık var olmayan bir
    hareketin miktarını sonsuza kadar taşırdı — stok geri alınır, parti
    defteri alınmazdı ve ikisi SESSİZCE ayrışırdı.

    EKSİYE DÜŞÜRMEZ, 409 ATAR. Parti sıfırın altına inecekse anlamı şudur:
    o parti BAŞKA bir yerde tüketilmiştir (1B-B satış yolunu getirdi) ve alışı
    geri almak tüketimi de geri almak demektir. Depo bunu kendi başına
    yapamaz; belgeyi geri alan insan önce tüketimi geri almalıdır.

    --- YÖN HAREKETİN İŞARETİNDEN GELİR, `kind`TEN DEĞİL (1B-B ÖLÇÜMÜ) ------

    Bu fonksiyon 1B-B'de İKİNCİ BİR YÖN İÇİN ÇOĞALTILMADI ve gerekçesi
    ölçüldü, iddia edilmedi. `stock_movements.quantity` İŞARETLİ yazılıyor
    (`stock_sign * miktar`; alışta `+`, satış/irsaliye/alış iadesinde `-`) ve
    parti defterine uygulanan delta HER İKİ yolda da o işaretli sayının
    KENDİSİDİR:

        alış   : parti += miktar    (hareket +miktar)
        satış  : parti -= miktar    (hareket -miktar)

    Yani "belgeyi geri al" TEK bir cümledir — `parti -= hareket.quantity` —
    ve aşağıdaki tek `quantity=quantity-:miktar` ifadesi İKİSİNİ DE doğru
    çözer: satışta `dusecek` NEGATİFTİR ve çıkarma EKLEMEYE döner.

    İkinci bir `_parti_iade` yazmak bu tek cümleyi İKİ yere bölerdi ve iki
    yer yönü belgenin türünden yeniden TÜRETMEK zorunda kalırdı — yanlış
    türetildiği gün defter stoktan SESSİZCE ayrışırdı.

    Bedeli, sözleşmenin ÖRTÜK olmasıdır: hareket miktarının işareti bir gün
    pozitife çevrilip yön `movement_type`a taşınırsa BURASI ters çalışır ve
    hiçbir şey bağırmaz. O yüzden sözleşme testte ADIYLA çivilendi
    (`test_parti_geri_alma_YONU_HAREKETIN_ISARETINDEN_gelir`).

    Aşağıdaki 409 kapısı bu yüzden YALNIZ pozitif `dusecek` için ısırır:
    negatif bir `dusecek` (satış geri alma) partiyi BÜYÜTÜR, eksiye düşüremez.
    Kapı orada ölü değil, KAPSAM DIŞIDIR.
    """
    hareketler = db.execute(
        text(
            "SELECT lot_id,quantity FROM stock_movements "
            "WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid "
            "AND lot_id IS NOT NULL"
        ),
        {"cid": cid, "rt": reference_type, "rid": reference_id},
    ).mappings().all()
    for hareket in hareketler:
        lot_id = int(hareket["lot_id"])
        dusecek = quantity(hareket["quantity"])
        kalan = db.execute(
            text(
                "SELECT quantity FROM product_lots WHERE company_id=:cid AND id=:id"
            ),
            {"cid": cid, "id": lot_id},
        ).scalar_one_or_none()
        if kalan is None or quantity(kalan) < dusecek:
            raise HTTPException(
                409,
                {
                    "code": "LOT_MIKTARI_EKSIYE_DUSER",
                    "message": (
                        f"#{lot_id} partisinde geri alınacak {dusecek} birim "
                        "YOK — parti başka bir yerde tüketilmiş. Belgeyi geri "
                        "almadan önce o tüketimi geri alın."
                    ),
                },
            )
        db.execute(
            text(
                "UPDATE product_lots SET quantity=quantity-:miktar "
                "WHERE company_id=:cid AND id=:id"
            ),
            {"miktar": dusecek, "cid": cid, "id": lot_id},
        )


class Tuketim(NamedTuple):
    """`_parti_tuket`in cevabı: DAĞITIM ve o dağıtımın SÜRESİ GEÇMİŞ payları.

    `dagitim` `Secim.dagitim`ın kendisidir (FEFO sırasında `(lot_id, pay)`).
    `suresi_gecmis_kimlikler` ise DAĞITIMA GİREN süresi geçmiş partilerin
    kimlikleridir — `Secim.suresi_gecmis`in TAMAMI değil, KESİŞİMİ.

    Fark önemlidir ve bir kayıt kararıdır: hareket notuna "süresi geçmiş"
    damgasını basacak olan çağıran, o damgayı YALNIZ gerçekten süresi geçmiş
    maldan düşülen satıra basmalıdır. `Secim.suresi_gecmis`i olduğu gibi
    kullanmak, depoda duran ama SEÇİLMEYEN bir partinin yüzünden temiz bir
    hareketi damgalardı ve damga o gün anlamını yitirirdi.

    `dagitim` BOŞ olmasının İKİ AYRI SEBEBİ var ve `defter_bosaldi` onları
    AYIRIR. Ayırmak zorunlu, çünkü ikisi farklı cümleler söylüyor:

      * `defter_bosaldi=False` — bu ürünün bu depoda HİÇ parti satırı yok.
        Ürün parti defterinden ÖNCE geliyor (0067) ve bugünkü davranışını
        AYNEN koruyor: tek bir `lot_id`si NULL hareket. Doğru cevap budur;
        olmayan bir partiyi UYDURMAK geri çağırma kaydını yalan söyletirdi.
      * `defter_bosaldi=True` — parti satırları VAR ama hepsi TÜKENMİŞ
        (`quantity=0`). Satış yine geçer (negatif stok politikası SERBESTSE)
        ama artık STOK ile PARTİ DEFTERİ AYRIŞIYOR: stok -1 diyor, defter
        0 diyor ve o -1'in hangi partiden geldiği SORULAMAZ.

    İKİNCİSİ SESSİZ BIRAKILMADI. Ayrışmanın kendisi bu dilimin kapsam
    sınırıdır (tüketim YALNIZ `quantity>0` satırlardan seçer) ama sessiz bir
    ayrışma tam olarak bu deponun kovaladığı kusur şeklidir. Çağıran bu
    bayrağı hareket NOTUNA damgalıyor (`_hareket_notu`), yani boşluk defterde
    GÖRÜNÜR kalıyor ve "bu -1 nereden geldi" sorusu sorulabilir oluyor.
    """

    dagitim: tuple[tuple[int, Decimal], ...]
    suresi_gecmis_kimlikler: frozenset[int]
    defter_bosaldi: bool = False


def _skt(deger: object) -> date | None:
    """`expiry_date` sütununu `date`e çevir — İKİ DİYALEKT İKİ TİP döndürür.

    PostgreSQL `datetime.date`, SQLite `str` verir. `_parti_ac` aynı ayrımı
    METİN tarafında çözüyor (karşılaştırma orada `isoformat()` üzerinden); bu
    fonksiyon TERS yönü çözer, çünkü `fefo_sec` sıralama için GERÇEK bir
    `date` ister. İkisinden biri atlanırsa sıra bir diyalektte alfabetik,
    ötekinde takvimsel olurdu ve ikisi SESSİZCE ayrışırdı.
    """
    if deger is None:
        return None
    if isinstance(deger, datetime):
        return deger.date()
    if isinstance(deger, date):
        return deger
    return date.fromisoformat(str(deger)[:10])


def _damga(deger: object) -> datetime:
    """`created_at`i `datetime`a çevir — `_skt` ile AYNI diyalekt gerekçesi.

    `created_at` FEFO'nun ÜÇÜNCÜ anahtarıdır (aynı SKT'li iki parti arasında
    ÖNCE GİRENİ seç). SQLite'ta dizgi geldiği için `sorted` onu alfabetik
    sıralardı; ISO-8601 damgalarda alfabetik sıra ÇOĞU ZAMAN takvimsel sırayla
    aynıdır ve tam bu yüzden tehlikelidir — ayrıştığı gün (saat dilimi soneki,
    mikrosaniye basamağı) kimse fark etmez.

    KARŞILAŞTIRILABİLİRLİK ZORUNLUDUR: PostgreSQL `timestamptz` sütununu
    tz-AWARE, SQLite ise tz-NAIVE verir ve Python ikisini karşılaştırırken
    `TypeError` atar. Tek bir koşuda tipler karışmaz (bir diyalekt, bir tip),
    ama karışmadığı VARSAYILMIYOR: aware damgalar UTC'ye çekilip naive'e
    indiriliyor, böylece sıra anahtarı HER İKİ diyalektte de TEK tiptir.
    """
    if isinstance(deger, datetime):
        damga = deger
    else:
        damga = datetime.fromisoformat(str(deger))
    if damga.tzinfo is not None:
        damga = damga.astimezone(timezone.utc).replace(tzinfo=None)
    return damga


def _parti_tuket(
    db: Session,
    cid: int,
    *,
    product_id: int,
    warehouse_id: int,
    miktar,
    bugun: date,
    suresi_gecmise_izin: bool = False,
) -> Tuketim:
    """Stoktan ÇIKAN mal için FEFO dağıtımını yap ve defteri DÜŞ.

    --- EŞZAMANLILIK: KORUYAN ŞEY KİLİT DEĞİL, KORUMALI UPDATE ------------

    ÖLÇÜLDÜ VE İLK VARSAYIM YANLIŞ ÇIKTI. `adjust_warehouse_stock` `FOR
    UPDATE` KULLANMIYOR (`app/inventory.py`de `with_for_update` YOK);
    kullandığı şey KORUMALI ATOMİK UPDATE'tir: koşul (`quantity + delta >= 0`)
    yazmanın KENDİ `WHERE`ündedir ve satır dönmezse çağıran 409 üretir. Yani
    "oku-sonra-yaz" hiç yapılmıyor.

    Bu fonksiyon AYNI ŞEKLİ kullanıyor ve bu bir taklit değil, ZORUNLULUKTUR:
    parti okuması (`SELECT ... quantity>0`) ile düşme (`UPDATE`) arasında bir
    aralık VARDIR ve o aralıkta başka bir işlem aynı partiyi tüketebilir. O
    yüzden düşme koşulu (`quantity>=:pay`) YAZMANIN KENDİ `WHERE`ündedir ve
    satır sayısı SORULUR — eşleşmezse 409 `PARTI_YARISTA_TUKENDI`.

    İKİ KORUMA BİRBİRİNİN YEDEĞİ DEĞİL, İKİ AYRI DURUM İÇİNDİR:

      * Negatif stok BLOKLU iken `warehouse_stocks` UPDATE'i eşzamanlı ikinci
        isteği zaten reddeder (ve PostgreSQL o satırda SATIR KİLİDİ tuttuğu
        için ikinci işlem BEKLER, sonra İŞLENMİŞ değer üzerinde yeniden
        değerlendirir) — parti tüketimine sıra GELMEZ.
      * Negatif stok SERBEST iken o koruma KAPALIDIR ve ikinci istek parti
        katmanına ULAŞIR. Orada tek koruma BURADAKİ `quantity>=:pay`
        yüklemidir; olmasaydı iki istek aynı partiyi iki kez düşer, parti
        eksiye inerdi (PostgreSQL'de `CHECK` ile 500, SQLite'ta SESSİZCE).

    İkisi de PG ikizinde AYRI AYRI ölçülüyor
    (`test_ESZAMANLI_*_BLOKLU_stokta`, `test_ESZAMANLI_*_SERBEST_stokta`).

    ÇAĞIRANIN SORUMLULUĞU — SIRA: bu fonksiyon `adjust_warehouse_stock`tan
    SONRA çağrılmalıdır. Önce çağrılsaydı, stok koruması reddetmiş olsa bile
    parti ZATEN düşülmüş olurdu ve belge geri alınana kadar defter yalan
    söylerdi.

    PARTİSİZ ÜRÜN BUGÜNKÜ DAVRANIŞINI KORUR: bu ürünün bu depoda `quantity>0`
    olan HİÇ parti satırı yoksa boş bir `Tuketim` döner ve çağıran tek bir
    `lot_id`si NULL hareket yazar. Bu bir eksiklik değil KAPSAM sınırıdır —
    parti defteri 0067'de kuruldu, ondan önceki ürünlerin partisi YOKTUR ve
    onlara bir parti UYDURMAK geri çağırma kaydını yalan söyletirdi.

    DEPO YÜKLEMİ SORGUDA, SEÇİCİDE DEĞİL: `fefo_sec` `company_id` gibi
    `warehouse_id`yi de BİLMEZ (bkz. `Parti` belgesi). Kapsam ÇAĞIRANIN
    sorgusunda kurulur ve iki katmanda birden kurulması ikisinin
    ayrışabileceği bir yer açardı. Yüklem düşerse başka bir deponun partisi
    bu depodan çıkmış görünür — testte ADIYLA çivili.

    İKİ AYRI RED, İKİ AYRI ÇARE:

      * `409 PARTI_YETERSIZ` — uygun parti toplamı isteneni karşılamıyor ve
        bunun sebebi süresi geçmiş mal DEĞİL. Çare: mal almak.
      * `422 PARTI_SURESI_GECMIS` — istenen karşılanamadı AMA depoda süresi
        geçmiş parti VAR; yani "mal yok" DEĞİL, "mal var ama süresi geçmiş".
        Çare imha/iade ya da AÇIK izindir (`allow_expired_lots`).

    SIRA BU YÖNDEDİR VE KARARDIR. Süresi geçmiş parti VARLIĞI tek başına
    satışı DURDURMAZ: taze partiler isteneni karşılıyorsa satış geçer ve
    süresi geçmiş maldan HİÇBİR ŞEY çıkmaz. Tersi (her süresi geçmiş parti
    422 üretsin) rafında tek bir bozuk parti duran ürünün BÜTÜN satışlarını
    kilitlerdi ve `ParticiYetersiz.suresi_gecmis` alanını ULAŞILAMAZ yapardı
    — o alan tam olarak "yetmedi ÇÜNKÜ var olan mal süresi geçmiş" cümlesini
    söylemek için var (`app/parti.py` başlığı). Korunan şey bozulmadan durur:
    süresi geçmiş mal `suresi_gecmise_izin` AÇIKÇA yazılmadan ÇIKAMAZ.
    """
    # SORGU `quantity>0` SÜZGECİNİ TAŞIMIYOR ve bu KASITLIDIR: tükenmiş
    # satır seçime GİRMEZ ama VARLIĞI bir bilgidir. Süzgeç SQL'de kalsaydı
    # "hiç parti yok" ile "partiler tükenmiş" AYNI boş sonuca düşerdi ve
    # ikincisi — stok ile defterin ayrıştığı durum — GÖRÜNMEZ olurdu.
    # Ayrım Python'da yapılıyor, ikinci bir sorguyla DEĞİL: iki okuma arasında
    # defter kayabilir ve o zaman bayrak kendi okuduğu satırları anlatmazdı.
    satirlar = db.execute(
        text(
            "SELECT id,quantity,expiry_date,created_at FROM product_lots "
            "WHERE company_id=:cid AND product_id=:pid AND warehouse_id=:wid"
        ),
        {"cid": cid, "pid": product_id, "wid": warehouse_id},
    ).mappings().all()
    if not satirlar:
        return Tuketim((), frozenset(), defter_bosaldi=False)

    # SIFIR MİKTARLI PARTİ SEÇİME GİRMEZ. `fefo_sec` onu zaten dağıtımdan
    # düşürür ama TOPLAMA katardı (`0`), yani sonucu değiştirmez; burada
    # düşürülmesinin sebebi başka: aşağıdaki "hepsi tükenmiş" dalı ancak
    # UYGUN kümesi boşken kurulabilir.
    uygun = [satir for satir in satirlar if quantity(satir["quantity"]) > 0]
    if not uygun:
        return Tuketim((), frozenset(), defter_bosaldi=True)

    partiler = [
        Parti(
            id=int(satir["id"]),
            quantity=quantity(satir["quantity"]),
            expiry_date=_skt(satir["expiry_date"]),
            created_at=_damga(satir["created_at"]),
        )
        for satir in uygun
    ]
    try:
        secim: Secim = fefo_sec(
            partiler,
            quantity(miktar),
            bugun=bugun,
            izin_ver_suresi_gecmis=suresi_gecmise_izin,
        )
    except ParticiYetersiz as hata:
        if hata.suresi_gecmis and not suresi_gecmise_izin:
            raise HTTPException(
                422,
                {
                    "code": "PARTI_SURESI_GECMIS",
                    "message": (
                        f"Istenen {hata.istenen} birim icin SURESI GECMEMIS "
                        f"partilerde {hata.mevcut} var; {hata.eksik} eksik. "
                        "Depoda suresi gecmis parti VAR ve secime GIRMEDI - "
                        "'mal yok' ile 'mal var ama suresi gecmis' ayni sey "
                        "degildir. Cikarmak bilincli bir karardir: istege "
                        "`allow_expired_lots: true` ekleyin ya da partiyi "
                        "imha/iade edin."
                    ),
                    "istenen": str(hata.istenen),
                    "mevcut": str(hata.mevcut),
                    "eksik": str(hata.eksik),
                    "suresi_gecmis": [
                        {
                            "lot_id": parti.id,
                            "quantity": str(parti.quantity),
                            "expiry_date": (
                                parti.expiry_date.isoformat()
                                if parti.expiry_date
                                else None
                            ),
                        }
                        for parti in hata.suresi_gecmis
                    ],
                },
            ) from hata
        raise HTTPException(
            409,
            {
                "code": "PARTI_YETERSIZ",
                "message": (
                    f"Istenen {hata.istenen} birim icin partilerde "
                    f"{hata.mevcut} var, {hata.eksik} eksik."
                ),
                "istenen": str(hata.istenen),
                "mevcut": str(hata.mevcut),
                "eksik": str(hata.eksik),
            },
        ) from hata
    except PartiSecilemedi as hata:
        # GİRDİ kusuru (`ISTENEN_GECERSIZ`) ile DEFTER kusuru
        # (`PARTI_MIKTARI_GECERSIZ`) ayrımı `sebep` üzerinden AYNEN geçiyor;
        # ikisini tek gövdeye katlamak iki farklı çareyi (yeniden gir /
        # defteri düzelt) tek cümleye indirirdi.
        raise HTTPException(
            422, {"code": hata.sebep, "message": str(hata)}
        ) from hata

    for lot_id, pay in secim.dagitim:
        sonuc = db.execute(
            text(
                "UPDATE product_lots SET quantity=quantity-:pay "
                "WHERE company_id=:cid AND id=:id AND quantity>=:pay"
            ),
            {"pay": pay, "cid": cid, "id": lot_id},
        )
        # KORUMA YAZMANIN KENDİ `WHERE`ÜNDE — ve bu bir kemer-askı DEĞİL,
        # negatif stok SERBEST iken TEK korumadır (bkz. başlıktaki ölçüm).
        # `fefo_sec` payın YUKARIDA OKUNAN miktarı aşmayacağını garanti eder,
        # ama okuma ile yazma arasındaki aralıkta başka bir işlem aynı
        # partiyi tüketmiş olabilir. Koşul `WHERE`e girmeseydi bu satır
        # partiyi eksiye indirirdi: PostgreSQL'de `CHECK` ile 500, SQLite'ta
        # SESSİZCE. Satır sayısı ayrıca sorulur çünkü eşleşmeyen bir UPDATE
        # hata ATMAZ, yalnız hiçbir şey yapmaz — sessiz bir kayıp.
        if sonuc.rowcount != 1:
            raise HTTPException(
                409,
                {
                    "code": "PARTI_YARISTA_TUKENDI",
                    "message": (
                        f"#{lot_id} partisinden {pay} birim dusulemedi - satir "
                        "okunduktan sonra baska bir islem onu tuketmis. Islemi "
                        "yeniden deneyin."
                    ),
                },
            )

    dagitilan = {lot_id for lot_id, _ in secim.dagitim}
    return Tuketim(
        secim.dagitim,
        frozenset(
            parti.id for parti in secim.suresi_gecmis if parti.id in dagitilan
        ),
        defter_bosaldi=False,
    )


#: `stock_movements.note` alanına basılan damga. Sabit MODÜL DÜZEYİNDEDİR ki
#: testler ve okuyucular onu tek yerden görsün; iki kopya iki farklı damgaya
#: ayrışırdı ve o gün "hangi hareket izinliydi" sorusu sorulamaz olurdu.
SURESI_GECMIS_DAMGASI = "SURESI GECMIS PARTI (acik izin)"
#: Parti defteri VAR ama TÜKENMİŞ: bu hareket hiçbir partiden düşmedi.
#: Damga bir hata değil, bir AYRIŞMA KAYDIDIR — bkz. `Tuketim` belgesi.
DEFTER_BOSALDI_DAMGASI = "PARTI DEFTERI KARSILAMADI (partiler tukenmis)"


def _hareket_notu(
    kind: str,
    belge_id: int,
    suresi_gecmis: bool,
    defter_bosaldi: bool = False,
) -> str:
    """Hareket notu; süresi geçmiş partiden düşüldüyse DAMGALI.

    --- BAYRAK NEREYE KAYDEDİLİR: ÖLÇÜLDÜ, VARSAYILMADI -------------------

    `allow_expired_lots` operatörün AÇIK bir kararıdır ve bir karar kayıt
    bırakmalıdır. Üç aday ölçüldü:

      1. `policy_override_logs` (`record_policy_overrides`). REDDEDİLDİ:
         o yol bir POLİTİKA ihlali sözleşmesidir ve `context.requested`
         ile yönetici gerekçesi ZORUNLU kılar (`company_policies.py`:
         "Bu işlem yönetici onayı ve gerekçe gerektirir"). Süresi geçmiş
         parti için firma ayarlarında bir politika sütunu YOKTUR (ölçüldü),
         yani bayrağı oraya yazmak var olmayan bir politikayı uydurur ve
         her isteğe yönetici başlığı şart koşardı.
      2. Aktivite paneli (`log_request_activity`). REDDEDİLDİ: satış
         yolunda ZATEN bir kayıt var ve oraya alan eklemek KAPALI aktivite
         kataloğunu (ACTION_TYPES) değiştirmeden yeni bir tip getiremezdi;
         ayrıca kayıt BELGE düzeyindedir, oysa izin PARTİ düzeyinde bir
         olgudur — hangi satırın izinli olduğunu söyleyemezdi.
      3. `stock_movements.note` — SEÇİLDİ. Sütun `Text`tir (sınırsız,
         `core_schema.py`), hareketin KENDİSİYLE aynı satırda durur ve tam
         olarak izinli payın yanındadır. Belge silinirse damga da gider,
         ki doğrusu budur: damga hareketin bir niteliğidir, ayrı bir defter
         değil.

    Damga YALNIZ dağıtıma GİREN süresi geçmiş partilere basılır (bkz.
    `Tuketim`); depoda duran ama seçilmeyen bir parti temiz bir hareketi
    damgalasaydı damga anlamını yitirirdi.

    İKİNCİ DAMGA (`defter_bosaldi`) BAŞKA BİR ŞEY SÖYLER ve ikisi AYNI
    hareketin üzerinde BULUŞAMAZ: biri "süresi geçmiş bir partiden düştüm",
    öteki "HİÇBİR partiden düşemedim". İkincisi `lot_id`si NULL olan bir
    hareketin üzerinde durur ve defterdeki boşluğu GÖRÜNÜR kılar — o boşluk
    olmasaydı stok -1 ile defterin 0'ı sessizce ayrışırdı.
    """
    temel = f"{kind} #{belge_id}"
    if defter_bosaldi:
        return f"{temel} - {DEFTER_BOSALDI_DAMGASI}"
    return f"{temel} - {SURESI_GECMIS_DAMGASI}" if suresi_gecmis else temel
