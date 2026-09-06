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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..activity_log import actor, log_activity
from ..auth import utcnow
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
    #: Kapalı bir demet; `failed_only` filtresi VE yeniden kuyruklama
    #: yükleminin İZİN VERİLEN KÜMESİ yalnız buradan kurulur.
    basarisiz_kovalar: tuple[str, ...]
    #: Bu yüzeyin YENİDEN KUYRUKLAMA ucu açılsın mı (açılış koşulu 3).
    #:
    #: NEDEN BAYRAK, NEDEN HERKESE AÇIK DEĞİL. Yeniden kuyruklama, okumanın
    #: aksine, bir TÜKETİCİNİN varlığına dayanır: `PENDING`e döndürülen satırı
    #: alıp işleyecek biri yoksa uç bir olayı düzeltmiş DEĞİL, yalnız durumunu
    #: değiştirmiş olur — ve okuma yüzeyi onu artık "taze iş" diye gösterir,
    #: yani bir başarısızlığı GİZLEMİŞ olurdu. `herd_integration_events`in
    #: bugün ne yazıcısı ne okuyucusu var (bkz. modül başlığı); ikinci yüzey
    #: eklendiği gün bu bayrak False ile gelir ve tüketicisi yazıldığında
    #: BİLEREK açılır.
    yeniden_kuyruklanabilir: bool = False


#: Tüketicinin (`app/field_stok_tuketici.py`) YAZDIĞI terminal durumlar.
#: `SENT` başarıdır; kalan DÖRDÜ başarısızlıktır. `PENDING` ve `CLAIMED`
#: terminal DEĞİLDİR ve bu demette YOKTUR — `CLAIMED` zaten bir işlemin
#: dışında hiç görünmez.
#:
#: `SKIPPED_TABAN_BILDIRILMEMIS` C2'de eklendi: ürün BELLİ ama taban birimi
#: bildirilmemiş. Kümeye GİRMESİ şart — girmeseydi o olay `failed_only`
#: ekranında GÖRÜNMEZ olurdu ve düzeltilmesi gereken ürün kartı hiç
#: bulunmazdı; yani olay yeşil bir kapıdan geçerek kaybolurdu.
_TARLA_BASARISIZ = (
    "SKIPPED_SOURCE_NOT_VISIBLE",
    "SKIPPED_NO_PRODUCT",
    "SKIPPED_TABAN_BILDIRILMEMIS",
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
    # Tüketici `app/field_stok_tuketici.py`de VAR ve `PENDING` seçiyor.
    yeniden_kuyruklanabilir=True,
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
#: (`app/field_stok_tuketici.py`). Tüketicinin YAZDIĞI diğer her gerekçe ELDE
#: yazılmış Türkçe bir cümledir. Ayrım bu yüzden bir SEZGİ değil, KENDİ
#: kodumuzun bıraktığı bir İŞARETTİR — içeriğe bakan bir kara liste
#: (bkz. `notifications/content_gate.py` başlığı) burada gereksizdir.
#:
#: DÜZELTME — İSTİSNA `str()`İ SÜTUNA İKİ YERDEN GİRER, BİRİNDEN DEĞİL.
#: Burada önce "tek yazım yeri" yazıyordu ve bu YANLIŞTI. Sayıldı, İKİ yer var:
#:
#:   1. `field_stok_tuketici.py` beklenmeyen istisna kolu — mesajı BU ÖNEKLE
#:      kurar. Arındırmanın gördüğü ve kestiği yer burasıdır.
#:   2. `field_stok_tuketici._bir_olayi_isle` içinde `default_warehouse`
#:      çağrısını saran `except RuntimeError` kolu (bu yazının yazıldığı gün
#:      831. satır); `str(hata)`yı ÖNEKSİZ yazar.
#:
#: İKİNCİSİ NEDEN GÜVENLİ, VE NEDEN ÖLÇÜLÜYOR. O kolun sardığı tek çağrı
#: `inventory.default_warehouse`tır ve o fonksiyonun TEK `raise`i sabit bir
#: yazılı metindir: `RuntimeError("Aktif depo bulunamadı")` — 21 karakter,
#: SQL yok, kısıt adı yok, satır değeri yok. SQLAlchemy/psycopg hataları
#: `RuntimeError` DEĞİLDİR, yani o kola HİÇ düşmez; onlar aşağıdaki geniş
#: `except`e gider ve ÖNEĞİ alır.
#:
#: Ama bu, BAŞKA bir modüldeki bir olguya dayanan bir güvenlik savıdır ve
#: sınanmadan yazılmamalıdır. `tests/test_entegrasyon_olaylari_depo_yolu.py`
#: o yolu GERÇEKTEN koşturur ve sunulan metnin TAM OLARAK o sabit cümle
#: olduğunu ölçer: `default_warehouse` bir gün daha zengin bir `RuntimeError`
#: atarsa o kapı KIRMIZI olur ve bu paragraf yeniden yazılmak zorunda kalır.
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


# --------------------------------------------------------------------------
# AÇILIŞ KOŞULU 3 — YENİDEN KUYRUKLAMA
# --------------------------------------------------------------------------
#
# Belgenin üçüncü koşulu: "Tüketici yalnız `PENDING` seçer; `SKIPPED_*`/`DEAD`
# yazılan satır bir daha ASLA seçilmez ve onu `PENDING`e döndüren hiçbir
# mekanizma yok." Aşağıdaki uç o mekanizmadır.
#
# --- GÖÇ YOK, ÖLÇÜLDÜ ------------------------------------------------------
#
# `field_integration_events` (göç 0044; `status` genişliği göç 0061) TAM
# OLARAK şu sütunları taşır: id, company_id, source_type, source_id, target,
# idempotency_key, status, attempts, last_error, processed_at, created_at,
# updated_at. `next_attempt_at` YOKTUR — yani "bir sonraki denemeyi ileri al"
# diye yazılacak bir sütun da yoktur, üstelik tüketicinin seçicisi böyle bir
# sütuna zaten BAKMAZ (`WHERE status = 'PENDING'`, `ORDER BY id`).
# `requeued_at`/`requeued_by` da YOKTUR; bu dilime göç eklenmediği için KİMİN
# yeniden kuyrukladığı bir `activity_logs` satırına yazılır (aşağıda).
#
# --- `attempts` SIFIRLANIR. BU BİR KARARDIR VE BEDELİ AÇIKÇA YAZILIYOR ------
#
# İlk niyet `attempts`i KORUMAKTI ("deneme geçmişini silme"). ÖLÇÜLDÜ; o niyet
# mekanizmayı ATEŞLENEMEZ kılıyor:
#
#   `_bir_olayi_isle` deneme sayısını `attempts + 1` diye hesaplar ve
#   `deneme > AZAMI_DENEME` (3) ise olayı ANINDA `DEAD` yazar — kaynağa hiç
#   bakmadan. Tavanı doldurarak ölen satırın `attempts` değeri 4'tür
#   (`_olayi_sonlandir` tavan kolunda `attempts = :deneme` MUTLAK yazar).
#   `attempts` korunarak `PENDING`e döndürülen böyle bir satır bir sonraki
#   döngüde 5 > 3 ile YENİDEN `DEAD` olur. Yani uç, koşulun VAR OLMA SEBEBİ
#   olan sınıfta (deneme hakkı bitmiş olay) hiçbir şey yapmayan, yalnız
#   `updated_at`i kımıldatan bir gürültü olurdu.
#
# BEDEL: kaç denemede kapandığı bilgisi SATIRDAN silinir. Kaybolmaz, YER
# DEĞİŞTİRİR ve iki yerde durmaya devam eder:
#   1. `last_error` KORUNUR ve tüketicinin kendi cümlesini taşır — "deneme
#      tavanı aşıldı (3)".
#   2. `activity_logs` satırının `details` alanı ÖNCEKİ durumu ve ÖNCEKİ
#      `attempts` değerini saklar; o defter append-only'dur.
#
# --- `last_error` ve `processed_at` KORUNUR --------------------------------
#
# `last_error` silinseydi ekranı okuyan kişi olayın NEDEN geri alındığını
# göremezdi; tüketici bir sonraki sonlandırmada onu zaten üzerine yazar.
#
# `processed_at` korunur ve bu, sıfırlanan `attempts`in İSTENEN karşılığıdır:
# `attempts = 0` yazıldıktan sonra yeniden kuyruklanmış bir satırı HİÇ
# denenmemiş bir satırdan ayıran TEK sütun `processed_at`tır. Belgenin kendi
# şikâyeti tam buydu ("hiç denenmemiş bir olayla ... AYIRT EDİLEMEZ");
# `processed_at`i de temizlemek o şikâyeti BÜYÜTÜRDÜ.
#
# --- İZİN: `farm.manage`. ÖLÇÜLDÜ, VARSAYILMADI ---------------------------
#
# Yol `/api/field-integration-events` önekinin altında ve o önek
# `auth._FARM_PATH_PREFIXES` listesinde; güvenli olmayan yöntem (`POST`)
# `required_permission` ile `farm.manage`e çözülür — okuma yüzeyinin YAZMA
# karşılığı, yani okumanın `farm.view`inden DAHA DAR. Okuma yüzeyinin kendi
# notu bunu zaten şart koşuyordu: "yeniden kuyruklama ... geldiği gün kendi
# YAZMA iznini gerektirir — `farm.view` ona yetmez."
#
# --- YARIŞ: KARARI VEREN ŞEY KOŞULLU UPDATE'İN ROWCOUNT'UDUR ---------------
#
# Aşağıda ÖNCE bir SELECT var ama karar ONUN DEĞİL. SELECT yalnızca HATAYI
# SINIFLANDIRIR (404 mü, "zaten gönderilmiş" mi, "terminal değil" mi); satırı
# gerçekten kımıldatan ifade `status IN (<terminal, `SENT` hariç>)` yüklemli
# koşullu UPDATE'tir ve yetki ONDADIR. Aradaki pencerede satır değişirse
# UPDATE 0 satır eşler ve uç 409 döner:
#   * tüketici olayı bu arada talep ettiyse (`CLAIMED`) — DOKUNULMAZ;
#   * ikinci bir yeniden kuyruklama önce davrandıysa (`PENDING`) — DOKUNULMAZ.
# Bu, tüketicinin `_talep_et`indeki desenin AYNISIDIR ve aynı sebeple
# seçilmiştir: "kazandım mı" sorusunun cevabı veritabanındadır, uygulamanın
# bir an önce okuduğu anlık görüntüde değil.
#
# --- `SENT` NEDEN 409, NEDEN SESSİZ BİR NO-OP DEĞİL -----------------------
#
# `SENT` olayın stok hareketi YAZILMIŞ hâlidir ve tüketici `stock_movements`
# satırlarını hiçbir yolda UPDATE/DELETE ETMEZ. Yeniden göndermek "tekrar
# denemek" değil, İKİNCİ BİR HAREKET yazmayı denemek olurdu. İkinci savunma
# hattı veritabanındadır (göç 0060, kısmi benzersiz indeks: olay + ürün başına
# EN FAZLA BİR hareket) ama ona GÜVENMİYORUZ: kısıt bir `IntegrityError`
# üretir, uç ise okunur bir cevap borçludur.

#: Tüketicinin BAŞLANGIÇ durumu. Yüzeyin diğer durum adları gibi burada
#: BAĞIMSIZ yazılı; `tests/test_outbox_requeue.py` onu tüketicideki
#: `DURUM_BEKLIYOR` ile EŞİT olmaya zorlar.
_BEKLIYOR = "PENDING"

_REQUEUE_KOD_GONDERILMIS = "EVENT_ALREADY_SENT"
_REQUEUE_KOD_TERMINAL_DEGIL = "EVENT_NOT_TERMINAL"


def _gonderilmis_durum() -> str:
    """`SENT`in adı BURADA yazılı DEĞİL, TÜKETİCİDEN gelir.

    İki yerde yazılsaydı tüketici onu yeniden adlandırdığında bu kapı
    SESSİZCE açılırdı: `onceki == "SENT"` karşılaştırması hiçbir zaman
    tutmaz, olay `basarisiz_kovalar` kontrolüne düşer, orada da bulunmaz ve
    "terminal değil" 409'u ile reddedilirdi — doğru sonuç, YANLIŞ gerekçe.
    Import fonksiyonun İÇİNDE: modül düzeyinde olsaydı yönlendirici
    tüketiciyi (ve onun üzerinden zamanlayıcı yüzeyini) import zincirine
    sokardı; bu modülün başlığı tüketiciye HİÇ dokunmadığını söylüyor.
    """
    from ..field_stok_tuketici import DURUM_UYGULANDI

    return DURUM_UYGULANDI


def kaydet_yeniden_kuyrukla(yuzey: OlayYuzeyi) -> None:
    """Bir yüzeyin yeniden kuyruklama ucunu kaydeder (açılış koşulu 3)."""
    # `attempts` sütunu OLMAYAN bir yüzey bu ucu ALAMAZ: sıfırlanacak sayaç
    # yoksa yukarıdaki tavan gerekçesi de tutmaz. Bugün tek yeniden
    # kuyruklanabilir yüzey `TARLA` ve sütunu var; koşul burada duruyor ki
    # ikinci yüzey bayrağı sessizce açmasın.
    if yuzey.deneme_sutunu is None:
        raise ValueError(
            "yeniden kuyruklanabilir yüzeyin `deneme_sutunu` olmak ZORUNDA: "
            f"{yuzey.alan}"
        )

    @router.post(
        f"{yuzey.yol}/{{olay_id}}/requeue",
        tags=[yuzey.etiket],
        operation_id=f"{yuzey.alan}_integration_event_requeue",
    )
    def _yeniden_kuyrukla(
        request: Request, olay_id: int, db: Session = Depends(get_db)
    ) -> dict[str, Any]:
        """Terminal (ve `SENT` OLMAYAN) bir olayı `PENDING`e döndürür."""
        cid = company_id(request)
        satir = db.execute(
            text(
                f"""SELECT id, status, {yuzey.deneme_sutunu} AS attempts
                FROM {yuzey.tablo} WHERE company_id=:cid AND id=:id"""
            ),
            {"cid": cid, "id": olay_id},
        ).mappings().first()
        # KİRACI: başka firmanın olayı 403 DEĞİL 404 döner — varlığını
        # sızdırmamak için. Okuma yüzeyi de aynı şeyi yapar (o uç satırı hiç
        # görmez); iki yüzeyin aynı olay için farklı cevap vermesi, kimliği
        # tek başına bir sızıntıya çevirirdi.
        if satir is None:
            raise HTTPException(404, "Olay bulunamadı")
        onceki = str(satir["status"])
        if onceki == _gonderilmis_durum():
            raise HTTPException(
                409,
                {
                    "code": _REQUEUE_KOD_GONDERILMIS,
                    "message": (
                        "Bu olay GÖNDERİLDİ: stok hareketi yazılmış durumda. "
                        "Yeniden kuyruklamak ikinci bir hareket yazmayı "
                        "denemek olurdu."
                    ),
                },
            )
        if onceki not in yuzey.basarisiz_kovalar:
            raise HTTPException(
                409,
                {
                    "code": _REQUEUE_KOD_TERMINAL_DEGIL,
                    "message": (
                        "Olay terminal değil (%s): zaten kuyrukta ya da "
                        "işlenmek üzere talep edilmiş." % onceki
                    ),
                },
            )

        params: dict[str, Any] = {
            "cid": cid,
            "id": olay_id,
            "bekliyor": _BEKLIYOR,
            "simdi": utcnow(),
        }
        # İZİN VERİLEN KÜME TEK KAYNAKTAN. `_kosul(..., failed_only=True)`
        # `failed_only` EKRANINI kuran ifadenin AYNISINI kurar. İki ayrı yerde
        # yazılsaydı, tüketici yeni bir terminal kova eklediğinde (C2
        # `SKIPPED_TABAN_BILDIRILMEMIS`i ekledi) ekran onu gösterir ama uç
        # yeniden kuyruklayamazdı — yani düzeltilebilir olduğu söylenen bir
        # olay düzeltilemez kalırdı.
        kosul = _kosul(yuzey, params, None, None, True)
        etkilenen = db.execute(
            text(
                f"""UPDATE {yuzey.tablo}
                SET status=:bekliyor, {yuzey.deneme_sutunu}=0,
                    updated_at=:simdi
                WHERE company_id=:cid AND id=:id{kosul}"""
            ),
            params,
        ).rowcount
        if etkilenen != 1:
            db.rollback()
            raise HTTPException(
                409,
                {
                    "code": _REQUEUE_KOD_TERMINAL_DEGIL,
                    "message": (
                        "Olay bu istek sürerken değişti; yeniden kuyruklanmadı."
                    ),
                },
            )

        # DENETİM KAYDI. `requeued_by` SÜTUNU YOK (bu dilimde göç yok), ama
        # kimin hangi olayı HANGİ durumdan geri aldığı kaybolmuyor: bu satır
        # `activity_logs`ta durur ve o defter append-only'dur — yani sütunun
        # bir ikamesi değil, ondan DAHA GÜÇLÜ bir izdir (silinemez, kullanıcıya
        # bağlı, zaman damgalı, korelasyon kimlikli).
        #
        # `log_activity` ÇAĞIRANIN oturumunda yazar ve COMMIT ETMEZ: aşağıdaki
        # tek commit ya durum değişikliğini ve denetim satırını BİRLİKTE kalıcı
        # yapar ya da hiçbirini. Denetimsiz bir yeniden kuyruklama OLUŞAMAZ.
        kullanici, korelasyon = actor(request)
        log_activity(
            db,
            cid,
            kullanici,
            "field_event.requeued",
            "field_integration_event",
            int(olay_id),
            "Entegrasyon olayı yeniden kuyruklandı (#%d, %s -> %s)"
            % (int(olay_id), onceki, _BEKLIYOR),
            {
                "source": yuzey.alan,
                "previous_status": onceki,
                "previous_attempts": satir["attempts"],
                "new_status": _BEKLIYOR,
            },
            correlation_id=korelasyon,
        )
        db.commit()
        return {
            "source": yuzey.alan,
            "id": int(olay_id),
            "previous_status": onceki,
            "status": _BEKLIYOR,
        }


for _yuzey in _YUZEYLER.values():
    if _yuzey.yeniden_kuyruklanabilir:
        kaydet_yeniden_kuyrukla(_yuzey)

