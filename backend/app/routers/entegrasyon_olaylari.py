"""Outbox olayları için OKUMA YÜZEYİ (açılış koşulu 2).

`FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md` ikinci koşulu şöyle yazıyor:
"Uygulamada `field_integration_events` tablosunu okuyan hiçbir ekran/uç yok;
kovalar yalnız süreç günlüğünde görünür." Bu modül o boşluğu kapatır ve
BAŞKA HİÇBİR ŞEY YAPMAZ: tüketicinin davranışına, bayrağa ve bayrağın
varsayılanına dokunulmadı; yeniden kuyruklama (koşul 3) ve canlılık sinyali
(koşul 4) bu modülün kapsamı DEĞİLDİR.

YÜZEY İKİ SORUYU CEVAPLAR — belgenin sorduğu iki soru:

1. **Kuyruk şu anda ne durumda?** `<yol>/summary`, kaynak tipi × durum
   kırılımında sayar. Belgenin "açmadan önce birikmiş kuyruğu ÖLÇ" adımı
   (`SELECT company_id, count(*) ... WHERE status = 'PENDING'`) böylece SQL
   erişimi olmadan yapılabilir; `oldest_created_at` ise birikimin YAŞINI
   verir — sayı tek başına "10 olay" der, yaş "10 olay ve en eskisi 40 gün"
   der; bakım penceresi kararı ikincisiyle verilir.
2. **Hangi olaylar başarısız oldu ve neden?** `<yol>` listesi; `last_error`
   metni tüketicinin YAZDIĞI gerekçedir, burada yeniden üretilmez.

--- BU YÜZEYİN GÖREMEDİĞİ ŞEY, BİLEREK YAZILIYOR --------------------------

`RECOVERY_FAILED` kovası VERİTABANINDA İZ BIRAKMAZ (belgenin kendi ölçümü):
o olayda `status` `PENDING` kalır, `attempts` değişmez, `last_error` yazılmaz.
Yani bu yüzey onu TAZE İŞ olarak gösterir ve gösterecektir. Okuma yüzeyi bu
sınıfı KAPATMAZ; belge de kapatmadığını söylüyor. Burada yazılı olmasının
sebebi, ekranı okuyanın "PENDING = hiç denenmemiş" diye okumasını
engellemek.

--- İKİNCİ OUTBOX TABLOSU: EKLEME, YENİDEN YAZMA DEĞİL --------------------

Depoda İKİ outbox tablosu var: `field_integration_events` (göç 0044) ve
`herd_integration_events` (göç 0049). İkincisinin bugün yazıcısı da okuyucusu
da YOK. Bu modül alanı bir PARAMETRE yapar: rota yolu, tablo adı ve sütun
sözleşmesi `OlayYuzeyi` betimleyicisinden gelir; sorgu ve yanıt biçimi
alandan BAĞIMSIZDIR. Hayvancılık yüzeyi eklemek bir betimleyici, bir
`kaydet()` çağrısı ve `auth._HERD_PATH_PREFIXES`e bir satırdır.

**ÖLÇÜLDÜ — SÜTUN SÖZLEŞMESİ AYNI DEĞİL.** İki tablo "aynı" sanılıyor; göç
metinleri öyle demiyor:

    field_integration_events: last_error, attempts, processed_at,
                              status VARCHAR(64) (göç 0061 genişletti),
                              source_type/target VARCHAR(40)
    herd_integration_events:  error, (attempts YOK), (processed_at YOK),
                              status VARCHAR(20),
                              source_type/target VARCHAR(60)

Bu yüzden betimleyici sütun ADLARINI taşır ve OLMAYAN sütunu `None` diye
bildirir; yanıt anahtarları HER İKİ alanda da aynıdır (`last_error`,
`attempts`, `processed_at`) ve olmayan sütun `null` döner. Farkı yanıt
biçimine sızdırmak istemciyi alan başına dallanmaya zorlardı — yani
"eklenebilir" olma özelliğini ilk gün kaybederdik.

--- TABLO ADI NEDEN F-STRING ----------------------------------------------

Statik kiracı nöbetçisi (`tests/test_tenant_scoping_guard.py`) çalışma
zamanında kurulan SQL'i işaretler. Buradaki tek dinamik parça MODÜL İÇİ
`_YUZEYLER` kümesinden gelen tablo/sütun adlarıdır — istek verisi SQL metnine
ASLA girmez, her değer bağlı parametredir ve her kök `company_id = :cid`
yüklemini taşır. Aynı desen `routers/herd.py` içinde zaten gözden geçirilmiş
ve nöbetçinin ALLOWLIST'inde gerekçesiyle duruyor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..tenancy import company_id

router = APIRouter()

# Tarla listeleriyle AYNI sayfalama sözleşmesi (`routers/farm.py`). Yeni bir
# sınır uydurmak, aynı modülün iki ekranında iki farklı davranış demek olurdu.
_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)


@dataclass(frozen=True)
class OlayYuzeyi:
    """Bir outbox tablosunun okuma sözleşmesi. ALAN BURADA PARAMETREDİR."""

    #: Yanıtta dönen alan adı; istemci hangi kuyruğa baktığını buradan bilir.
    alan: str
    #: OpenAPI etiketi. İzin önek kapıları rotaları ETİKETE göre topluyor,
    #: bu yüzden etiket betimleyicinin parçası: yanlış etiket, kapının o
    #: rotayı hiç görmemesi demek.
    etiket: str
    #: Rota yolu (`/api` öneki `main.py`de ekleniyor).
    yol: str
    #: Tablo adı. YALNIZ bu modülden gelir, istekten DEĞİL.
    tablo: str
    #: Gerekçe sütunu: tarlada `last_error`, sürüde `error`.
    hata_sutunu: str
    #: Deneme sayacı. Sürü tablosunda YOK — o zaman `None`.
    deneme_sutunu: str | None
    #: İşlenme damgası. Sürü tablosunda YOK — o zaman `None`.
    islenme_sutunu: str | None
    #: BAŞARISIZLIK kovaları: terminal olan ve `SENT` OLMAYAN durumlar.
    #: Kapalı bir demet; `failed_only` filtresi yalnız buradan kurulur.
    basarisiz_kovalar: tuple[str, ...]


#: Tüketicinin (`app/field_stok_tuketici.py`) YAZDIĞI terminal durumlar.
#: `SENT` başarıdır; kalan üçü başarısızlıktır. `PENDING` ve `CLAIMED`
#: terminal DEĞİLDİR ve bu demette YOKTUR — `CLAIMED` zaten bir işlemin
#: dışında hiç görünmez.
_TARLA_BASARISIZ = (
    "SKIPPED_SOURCE_NOT_VISIBLE",
    "SKIPPED_NO_PRODUCT",
    "DEAD",
)

TARLA = OlayYuzeyi(
    alan="field",
    etiket="farm",
    yol="/field-integration-events",
    tablo="field_integration_events",
    hata_sutunu="last_error",
    deneme_sutunu="attempts",
    islenme_sutunu="processed_at",
    basarisiz_kovalar=_TARLA_BASARISIZ,
)

#: Bugün tek yüzey var. Küme yine de MODÜL İÇİ ve kapalı: tablo adının
#: istekten gelmediğini okuyanın tek bakışta görmesi için.
_YUZEYLER: dict[str, OlayYuzeyi] = {TARLA.alan: TARLA}


def _projeksiyon(yuzey: OlayYuzeyi) -> str:
    """Yanıt anahtarları alandan BAĞIMSIZ; olmayan sütun NULL seçilir."""
    deneme = (
        f"{yuzey.deneme_sutunu} AS attempts"
        if yuzey.deneme_sutunu
        else "NULL AS attempts"
    )
    islenme = (
        f"{yuzey.islenme_sutunu} AS processed_at"
        if yuzey.islenme_sutunu
        else "NULL AS processed_at"
    )
    return (
        "id, source_type, source_id, target, status, "
        f"{deneme}, {yuzey.hata_sutunu} AS last_error, "
        f"created_at, updated_at, {islenme}"
    )


#: Tüketicinin BEKLENMEYEN bir istisnayı `last_error`e yazarken koyduğu ÖNEK
#: (`app/field_stok_tuketici.py`, tek yazım yeri). Tüketicinin YAZDIĞI diğer
#: her gerekçe ELDE yazılmış Türkçe bir cümledir; ham bir istisnanın `str()`i
#: sütuna YALNIZ bu önekten sonra girer. Ayrım bu yüzden bir SEZGİ değil,
#: KENDİ kodumuzun bıraktığı bir İŞARETTİR — içeriğe bakan bir kara liste
#: (bkz. `notifications/content_gate.py` başlığı) burada gereksizdir.
_HAM_ISTISNA_ONEKI = "beklenmeyen hata: "

#: Önekten SONRAKİ her şeyin yerine geçen SABİT metin. Deponun canlı
#: istisnalar için zaten uyguladığı desen budur: ham metin ATILIR, yerine
#: sabit bir Türkçe cümle konur (`routers/products.py` PRODUCT_FAILED_MESSAGE,
#: `routers/transactions.py`). Burada tek fark, istisnanın CANLI değil
#: SAKLANMIŞ olması.
_HAM_ISTISNA_YERINE = (
    "beklenmeyen bir hata (ayrıntı yalnız sunucu günlüğünde ve veritabanında)"
)


def _gerekceyi_arindir(hata: Any) -> Any:
    """SAKLANMIŞ ham istisna metnini yanıttan ÇIKARIR — KÜRATE METNE DOKUNMAZ.

    NEDEN OKUMA ZAMANINDA, YAZMA ZAMANINDA DEĞİL. İki gerekçe:

    1. **Yazma zamanı, ZATEN YAZILMIŞ satırı kurtarmaz.** Tüketiciyi
       düzeltmek bugünkü tablodaki metni değiştirmez; bu uç onları
       DEĞİŞMEDEN sunmaya devam ederdi. Sızıntı sınıfı ancak metnin
       ÇIKTIĞI yerde kapanır.
    2. **Adli değer veritabanında KALIR.** Ham metin operatör için
       teşhisin kendisidir; SQL erişimi olan onu görmeye devam eder.
       Kaybedilen tek şey, `farm.view` taşıyan SALT OKUR rollerin
       (ölçüldü: rapor, muhasebe, depo dahil altı rol) o metne HTTP
       üzerinden erişmesidir — kapatılmak istenen tam olarak budur.

    KÜRATE GEREKÇE AYNEN GEÇER. Bu yüzeyin bütün değeri o metinde:
    "sezonun ürünü bildirilmemiş; hasat stok taşıyamaz (field_harvests ->
    crop_seasons.product_id NULL)" okuyana HANGİ KAYDI düzelteceğini söyler.
    Toptan bir karartma, ucu kova adından ibaret bırakır ve ekranı
    değersizleştirirdi.

    Önekten ÖNCEKİ parça da KORUNUR: orası tüketicinin kendi cümlesidir
    ("deneme tavani asildi (3): "), yani kaçıncı denemede kapandığı bilgisi
    kaybolmaz.
    """
    if not isinstance(hata, str):
        return hata
    yer = hata.find(_HAM_ISTISNA_ONEKI)
    if yer < 0:
        return hata
    return hata[:yer] + _HAM_ISTISNA_YERINE


def _kosul(
    yuzey: OlayYuzeyi,
    params: dict[str, Any],
    status: str | None,
    source_type: str | None,
    failed_only: bool,
) -> str:
    """Filtre parçası KAPALI kümeden kurulur; DEĞERLER bağlı parametredir."""
    kosul = ""
    if status:
        kosul += " AND status=:status"
        params["status"] = status.strip().upper()
    if source_type:
        kosul += " AND source_type=:source_type"
        params["source_type"] = source_type.strip()
    if failed_only:
        # Yer tutucular demetin UZUNLUĞUNDAN türer, İÇERİĞİNDEN değil;
        # kova adları bağlı parametre olarak gider.
        yer = ",".join(f":kova{i}" for i in range(len(yuzey.basarisiz_kovalar)))
        kosul += f" AND status IN ({yer})"
        for i, kova in enumerate(yuzey.basarisiz_kovalar):
            params[f"kova{i}"] = kova
    return kosul


def kaydet(yuzey: OlayYuzeyi) -> None:
    """Bir yüzeyin iki rotasını kaydeder. İKİNCİ ALAN = İKİNCİ ÇAĞRI."""

    # `operation_id` ALANDAN türetiliyor. Kapanış fonksiyonlarının adı
    # (`_ozet`/`_liste`) her yüzeyde AYNI olduğu için FastAPI'nin ad
    # türetmesine bırakılsaydı ikinci yüzey ÇAKIŞIRDI; üstelik üretilen
    # istemci tipleri `_ozet_api_...` gibi okunmaz adlar alırdı.
    @router.get(
        f"{yuzey.yol}/summary",
        tags=[yuzey.etiket],
        operation_id=f"{yuzey.alan}_integration_events_summary",
    )
    def _ozet(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
        """Kuyruk durumu: kaynak tipi × durum kırılımı, firma kapsamlı."""
        cid = company_id(request)
        rows = db.execute(
            text(
                f"""SELECT source_type, status, COUNT(*) AS count,
                MIN(created_at) AS oldest_created_at
                FROM {yuzey.tablo} WHERE company_id=:cid
                GROUP BY source_type, status
                ORDER BY source_type, status"""
            ),
            {"cid": cid},
        ).mappings().all()
        kovalar = [dict(satir) for satir in rows]
        for kova in kovalar:
            kova["count"] = int(kova["count"] or 0)
        return {
            "source": yuzey.alan,
            "buckets": kovalar,
            "total": sum(kova["count"] for kova in kovalar),
            # Belgenin açılış öncesi ölçümü: birikmiş PENDING kuyruğu.
            "pending_total": sum(
                kova["count"] for kova in kovalar if kova["status"] == "PENDING"
            ),
            "failed_total": sum(
                kova["count"]
                for kova in kovalar
                if kova["status"] in yuzey.basarisiz_kovalar
            ),
        }

    @router.get(
        yuzey.yol,
        tags=[yuzey.etiket],
        operation_id=f"{yuzey.alan}_integration_events_list",
    )
    def _liste(
        request: Request,
        limit: int = _SAYFA,
        offset: int = _ATLA,
        status: str | None = None,
        source_type: str | None = None,
        failed_only: bool = False,
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        """Olay listesi: gerekçesiyle birlikte, firma kapsamlı."""
        cid = company_id(request)
        params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
        kosul = _kosul(yuzey, params, status, source_type, failed_only)
        toplam = db.execute(
            text(f"SELECT COUNT(*) FROM {yuzey.tablo} WHERE company_id=:cid{kosul}"),
            params,
        ).scalar()
        rows = db.execute(
            text(
                f"""SELECT {_projeksiyon(yuzey)}
                FROM {yuzey.tablo} WHERE company_id=:cid{kosul}
                ORDER BY id DESC LIMIT :limit OFFSET :offset"""
            ),
            params,
        ).mappings().all()
        # ARINDIRMA BURADA, projeksiyonun HEMEN ardında: yanıta giden TEK
        # yol bu. Sorgu ham metni okumaya devam eder; dışarı çıkan çıkmaz.
        kalemler = []
        for satir in rows:
            kalem = dict(satir)
            kalem["last_error"] = _gerekceyi_arindir(kalem.get("last_error"))
            kalemler.append(kalem)
        return {
            "source": yuzey.alan,
            "items": kalemler,
            "total": int(toplam or 0),
            "limit": limit,
            "offset": offset,
        }


for _yuzey in _YUZEYLER.values():
    kaydet(_yuzey)
