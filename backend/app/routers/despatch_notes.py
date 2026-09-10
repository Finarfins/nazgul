"""e-İrsaliye uçları — E4a DÜZ SEVK (göç `20260913_0083`).

Şekli `app/routers/invoices.py`in e-belge uçlarıyla BİREBİR aynı ve bu
bilinçli: `submit` / `status` / `sync` / `download` dörtlüsü orada
ölçülmüş bir sözleşmedir (hangisi ağa çıkar, hangisi yalnız yerel okur,
hangisi POST olmak zorundadır) ve o sözleşmeyi ikinci bir belge türü için
yeniden ICAT etmek, iki yerde iki farklı davranış üretirdi.

--- DÖRT UÇ, DÖRT FARKLI GEREKÇE -----------------------------------------

* ``GET  .../edespatch/status``   — YALNIZ yerel satırı okur. Ağa ÇIKMAZ.
* ``POST .../edespatch/submit``   — sağlayıcıya belge gönderir. Tekrarlanmaz.
* ``POST .../edespatch/sync``     — sağlayıcıya SORAR, göndermez. GET DEĞİL
  çünkü DIŞ BİR YAN ETKİ üretiyor (oturum açar, kota tüketir) ve YEREL
  SATIRI YAZIYOR; GET'in envanterdeki anlamı "read"tir ve orada yazan bir
  uç o sözleşmeyi bozardı (`einvoice/sync`in gerekçesiyle AYNI).
* ``GET  .../edespatch/download`` — ``format=xml`` gönderilen belgeyi
  YENİDEN ÜRETİR (ağa çıkmaz, saf dönüşüm); ``format=pdf`` **501**.

--- ``format=pdf`` NEDEN 501 -------------------------------------------

Keşif §2.4 ve §5 ölçtü: WSDL'de AYRI BİR PDF OPERASYONU YOK, ve
`SEARCH_KEY/CONTENT_TYPE` bir `xs:string` olduğu için `PDF` değerinin
desteklendiği YALNIZ şemadan ÇIKARILAMAZ. Canlı PDF dönüşü
**DOĞRULANMADI**.

Üç seçenek vardı ve ikisi yanlıştı: (a) `CONTENT_TYPE=PDF` tahmin edip
göndermek — doğrulanmamış bir sözleşmeyle canlı çağrı; (b) boş gövde ya
da iç PDF'i döndürmek — resmî mali belge diye başka bir şey sunmak.
Seçilen (c): FAIL-CLOSED, adı konmuş **501 Not Implemented**.

**501, 404 ya da 503 DEĞİL ve fark ölçülebilir.** 404 "bu belgenin sureti
yok" der (oysa XML sureti VAR); 503 "şimdi olmuyor, sonra dene" der (oysa
beklemek bunu düzeltmez). 501 doğru olanı söyler: SUNUCU BU BİÇİMİ
UYGULAMIYOR. Sağlayıcı sözleşmesi doğrulandığında burası bir dal
kazanacak; o güne kadar arayüz butonu göstermiyor ve API yalan söylemiyor.

--- İPTAL UCU YOK --------------------------------------------------------

`invoices.py`de `POST .../cancel` var; burada YOK. Gerekçe keşif §2.4'te
ölçülü: `CancelDocumentRequest` şemada var ama WSDL portType/binding'de
bir operasyon DEĞİL, yani bu uç üzerinden iptal desteklendiği sonucu
ÇIKARILAMAZ. `Mark*` işlemlerini iptal saymak — keşif'in açıkça
yasakladığı şey — olmayan bir yeteneği varmış gibi göstermek olurdu.

--- KİRACI YÜKLEMİ: HER SORGUDA, AÇIKÇA ----------------------------------

`despatch_notes` bir KİRACI TABLOSUDUR (`company_id` taşır, `TENANT_TABLES`
120 -> 121). Bu dosyadaki HER `text()` sabit metindir ve kökü
`company_id=:cid` taşır — okuma yardımcısı zaten kapsam denetliyor olsa
bile YAZMALAR kendi yüklemlerini AYRICA taşır. İki koruma aynı şeyi
ölçmüyor: biri "satır senin mi", öteki "yazma senin satırına mı gitti"
(0080'in `_sahip_mi` kaydıyla ve `einvoice_sync`in yorumuyla AYNI gerekçe).

--- E4b İÇİN BIRAKILAN DİKİŞLER ------------------------------------------

Sorgular faturaya `despatch_id` üzerinden DEĞİL, irsaliyeden faturaya
`invoice_id` ile bakıyor ve HİÇBİR yerde "bu faturanın TEK irsaliyesi"
varsayan bir JOIN yok: `_irsaliye` satırı kendi kimliğiyle çözüyor,
liste ucu irsaliyeleri kendi tablosundan sayfalıyor. Kısmi sevk geldiğinde
değişecek tek şey göçteki `UNIQUE(company_id, invoice_id)` kısıtı ve
`POST /api/despatch-notes`in 409 dalıdır.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_modulu
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..config import settings
from ..db import get_db
from ..einvoice import UblBuildError, einvoice_configuration, get_einvoice_provider
from ..einvoice import edespatch
from ..einvoice.endpoints import IZIBIZ_EDESPATCH_PDF_UNVERIFIED
from ..invoice_service import log_invoice_action
from ..tenancy import company_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/despatch-notes", tags=["despatch-notes"])

#: Yanıt gövdesinde görünen sütunlar. `SELECT *` DEĞİL ve bu bilinçli:
#: yıldızlı bir okuma, göçle eklenecek her sütunu istemciye sessizce
#: sızdırırdı (`invoices.py`nin `EINVOICE_FIELDS` listesiyle aynı gerekçe).
GORUNEN_ALANLAR = (
    "id",
    "invoice_id",
    "despatch_uuid",
    "despatch_number",
    "issue_date",
    "actual_shipment_at",
    "carrier_name",
    "carrier_tax_number",
    "driver_name",
    "driver_national_id",
    "vehicle_plate",
    "trailer_plate",
    "delivery_address",
    "delivery_customer_id",
    "edespatch_status",
    "edespatch_gib_status_code",
    "edespatch_provider_uuid",
    "edespatch_last_error",
    "edespatch_submitted_at",
    "edespatch_synced_at",
    "created_at",
    "updated_at",
)

EBELGE_YAPILANDIRILMAMIS = "e-Belge entegrasyonu yapılandırılmamış"
IRSALIYE_YOK = "İrsaliye bulunamadı"
FATURA_YOK = "Fatura bulunamadı"
#: E4a'nın "bir fatura bir irsaliye" kuralı. 409, 400 DEĞİL: istek
#: GEÇERLİDİR, çakışan şey KAYNAĞIN MEVCUT DURUMUDUR.
IRSALIYE_ZATEN_VAR = "Bu faturanın e-İrsaliyesi zaten var"
INDIRME_FORMATLARI = ("xml", "pdf")

#: `UNKNOWN` durumundaki bir belge yeniden GÖNDERİLMEZ; önce sorulur.
#: Cümle uca özeldir çünkü operatörün yapacağı iş burada BELLİDİR.
GONDERIM_KAPALI_MESAJI = {
    edespatch.UNKNOWN: (
        "Önceki gönderimin sonucu bilinmiyor. Çift irsaliye riskine karşı "
        "önce durum sorgusu (sync) çalıştırın."
    ),
}
GONDERIM_KAPALI_VARSAYILAN = "İrsaliye zaten gönderilmiş; durumu: {durum}"


class IrsaliyeOlustur(BaseModel):
    """`POST /api/despatch-notes` gövdesi.

    ŞOFÖR VE PLAKA ZORUNLU, taşıyıcı opsiyonel — ve bu göçteki CHECK'ten
    DAHA DAR bir kural. Göç iki dala da (plaka+şoför **veya** kargo
    firması) izin verir çünkü GİB kılavuzu öyle diyor; E4a'nın KAPSAMI
    ise "tek şoför, tek araç"tır. Daha dar kuralın şemada değil BURADA
    olmasının sebebi tam olarak budur: kargo dalı E4b'de açıldığında göçe
    dokunulmayacak.
    """

    invoice_id: int
    issue_date: date | None = None
    actual_shipment_at: datetime
    driver_name: str = Field(min_length=1, max_length=120)
    driver_national_id: str = Field(min_length=11, max_length=11)
    vehicle_plate: str = Field(min_length=1, max_length=20)
    trailer_plate: str | None = Field(default=None, max_length=20)
    carrier_name: str | None = Field(default=None, max_length=200)
    carrier_tax_number: str | None = Field(default=None, max_length=60)
    delivery_address: str = Field(min_length=1)
    delivery_customer_id: int | None = None
    despatch_number: str | None = Field(default=None, max_length=40)


def _gorunum(satir: dict) -> dict:
    """Satırı yanıt gövdesine çevir; zaman/tarih alanları METİN olur.

    `str()` KASITLI: `invoices.py::_einvoice_view` ile aynı davranış, ve
    iki diyalekt arasındaki farkı yüzeyde eşitliyor (PG offset'li verir,
    SQLite naive) — istemci her iki durumda da bir dize görür.
    """
    cikti: dict = {}
    for ad in GORUNEN_ALANLAR:
        deger = satir.get(ad)
        cikti[ad] = (
            str(deger) if deger is not None and isinstance(deger, (datetime, date)) else deger
        )
    cikti["edespatch_configured"] = einvoice_configuration(settings).configured
    return cikti


def _irsaliye(db: Session, cid: int, irsaliye_id: int) -> dict:
    satir = db.execute(
        text("SELECT * FROM despatch_notes WHERE id=:id AND company_id=:cid"),
        {"id": irsaliye_id, "cid": cid},
    ).mappings().first()
    if not satir:
        raise HTTPException(404, IRSALIYE_YOK)
    return dict(satir)


def _fatura(db: Session, cid: int, fatura_id: int) -> dict:
    """Faturayı KİRACI KAPSAMINDA çöz. Başka firmanınki 404, 403 DEĞİL.

    403 "var ama giremezsin" der ve bir VARLIK BİLGİSİ sızdırır: sızmış
    bir fatura kimliğini deneyen biri, hangi kimliklerin GERÇEK olduğunu
    yanıt kodundan öğrenirdi. 404 hiçbir şey söylemez.
    """
    satir = db.execute(
        text("SELECT * FROM invoices WHERE id=:id AND company_id=:cid"),
        {"id": fatura_id, "cid": cid},
    ).mappings().first()
    if not satir:
        raise HTTPException(404, FATURA_YOK)
    return dict(satir)


def _belge_numarasi(fatura: dict, verilen: str | None) -> str:
    """İrsaliye belge numarası: operatör verdiyse onunki, yoksa faturadan.

    UYDURULMUYOR: türetilen değer faturanın KENDİ numarasının önüne bir
    ayraç koyar (`IRS-<fatura no>`), yani iki belge birbirine bakarak
    izlenebilir. Ayrı bir sayaç açmak (`document_sequences`) E4b'nin işi:
    kısmi sevkte bir faturanın BİRDEN ÇOK irsaliyesi olacak ve o zaman
    türetme yetmeyecek. Bugün türetme yeter ve tekilliği göçteki
    `UNIQUE(company_id, invoice_id)` zaten garanti ediyor.
    """
    elle = (verilen or "").strip()
    if elle:
        return elle[:40]
    return f"IRS-{fatura.get('invoice_number')}"[:40]


def _satirlar(db: Session, cid: int, fatura_id: int) -> list[dict]:
    """Fatura kalemleri — DÜZ SEVKTE irsaliye satırlarının BİREBİR kaynağı."""
    return [
        dict(x)
        for x in db.execute(
            text(
                "SELECT id, description, quantity FROM invoice_items "
                "WHERE invoice_id=:id AND company_id=:cid ORDER BY id"
            ),
            {"id": fatura_id, "cid": cid},
        ).mappings().all()
    ]


def _ubl_payload(db: Session, cid: int, irsaliye: dict) -> dict:
    """UBL üreticisinin beklediği sözlüğü depodan kur.

    KAYNAK SEÇİMİ ÖNEMLİ: taraflar faturanın DONMUŞ anlık görüntülerinden
    (`customer_snapshot`, `company_snapshot`) okunuyor, canlı `customers`
    satırından DEĞİL. Gerekçe faturanınkiyle aynı: sevk edilen mal o
    faturanın malıdır ve müşteri unvanı/adresi sonradan değişse bile
    irsaliye o günkü hâli göstermelidir.
    """
    fatura = _fatura(db, cid, int(irsaliye["invoice_id"]))
    musteri = json.loads(fatura.get("customer_snapshot") or "{}")
    firma = json.loads(fatura.get("company_snapshot") or "{}")
    kalemler = _satirlar(db, cid, int(irsaliye["invoice_id"]))
    if not kalemler:
        raise UblBuildError("Faturada kalem yok; irsaliye üretilemez")

    if irsaliye.get("delivery_customer_id"):
        # Teslim tarafı faturadan FARKLI. Adı canlı tablodan okunuyor
        # (anlık görüntüsü yok) ama KİRACI KAPSAMINDA — bileşik yabancı
        # anahtar bunu şemada da imkânsız kılıyor, yüklem ikinci kattır.
        alici = db.execute(
            text("SELECT name FROM customers WHERE id=:id AND company_id=:cid"),
            {"id": irsaliye["delivery_customer_id"], "cid": cid},
        ).mappings().first()
        if alici:
            musteri = {**musteri, "name": alici["name"]}
    return {
        "despatch_number": irsaliye.get("despatch_number"),
        "uuid": irsaliye.get("despatch_uuid"),
        "issue_date": irsaliye.get("issue_date"),
        "invoice_number": fatura.get("invoice_number"),
        "invoice_issue_date": fatura.get("created_at"),
        "supplier": {
            "vkn": firma.get("tax_number"),
            "name": firma.get("name"),
            "address": firma.get("address"),
            "tax_office": firma.get("tax_office"),
        },
        "customer": {
            "vkn_tckn": musteri.get("vkn_tckn") or musteri.get("tax_number"),
            "name": musteri.get("name"),
            "address": musteri.get("address"),
            "tax_office": musteri.get("tax_office"),
        },
        "shipment": {
            "actual_shipment_at": irsaliye.get("actual_shipment_at"),
            "vehicle_plate": irsaliye.get("vehicle_plate"),
            "trailer_plate": irsaliye.get("trailer_plate"),
            "driver_name": irsaliye.get("driver_name"),
            "driver_national_id": irsaliye.get("driver_national_id"),
            "carrier_name": irsaliye.get("carrier_name"),
            "carrier_tax_number": irsaliye.get("carrier_tax_number"),
            "delivery_address": irsaliye.get("delivery_address"),
        },
        "lines": [
            {
                "id": sira,
                "name": kalem.get("description"),
                # DÜZ SEVK: miktar faturadakinin BİREBİR aynısı ve
                # `Decimal` olarak taşınıyor — `edespatch._miktar` `float`u
                # REDDEDER, çünkü yuvarlanmış bir miktar iki belgenin
                # eşitliğini bozardı ve E4a'nın TEK iddiası o eşitliktir.
                "quantity": kalem.get("quantity"),
            }
            for sira, kalem in enumerate(kalemler, start=1)
        ],
    }


def _saglayici_kapisi() -> None:
    """Yapılandırma yoksa 503 — sessiz 200 YOK (`einvoice_submit` ile aynı).

    Gerekçe yalnız log'a gider; istemciye yapılandırma detayı SIZMAZ.
    """
    yapilandirma = einvoice_configuration(settings)
    if not yapilandirma.configured:
        logger.warning(
            "e-İrsaliye çağrısı reddedildi: yapılandırma eksik (sebep=%s)", yapilandirma.reason
        )
        raise HTTPException(503, EBELGE_YAPILANDIRILMAMIS)


# --------------------------------------------------------------------- CRUD

@router.post("", status_code=201)
def irsaliye_olustur(payload: IrsaliyeOlustur, request: Request, db: Session = Depends(get_db)):
    """Faturadan bir e-İrsaliye kaydı aç. HENÜZ GÖNDERMEZ.

    Oluşturma ile gönderim AYRI iki adımdır ve bu bir kolaylık değil bir
    güvenlik kararı: sabit ``despatch_uuid`` satırın DOĞUŞUNDA üretilir,
    yani gönderim denemesinden ÖNCE var olur. Tek adımlı bir uçta bir
    TIMEOUT, kaydı hiç yazamadan dönerdi ve elimizde sağlayıcıya gitmiş
    olabilecek bir belgenin ETTN'i KALMAZDI — sorgulanamaz, tekrarlanamaz,
    yalnız tahmin edilebilir bir belge.

    İPTAL EDİLMİŞ FATURAYA İRSALİYE KESİLMEZ: iptal edilmiş bir faturanın
    malını sevk etmek beyanla çelişir.
    """
    cid = company_id(request)
    fatura = _fatura(db, cid, payload.invoice_id)
    if fatura.get("cancelled_at") or str(fatura.get("status") or "").upper() == "CANCELLED":
        raise HTTPException(409, "İptal edilmiş faturaya e-İrsaliye kesilemez")
    if payload.delivery_customer_id is not None:
        # Bileşik yabancı anahtar bunu şemada da tutuyor; yüklem yine de
        # AÇIK, çünkü FK ihlali 500 verirdi ve kullanıcı 404 hak ediyor.
        var_mi = db.execute(
            text("SELECT 1 FROM customers WHERE id=:id AND company_id=:cid"),
            {"id": payload.delivery_customer_id, "cid": cid},
        ).first()
        if not var_mi:
            raise HTTPException(404, "Teslim müşterisi bulunamadı")

    simdi = utcnow()
    sevk_ani = payload.actual_shipment_at
    if sevk_ani.tzinfo is None:
        # Naive gelen bir an UTC sayılır. Reddetmek daha saf olurdu ama
        # `datetime-local` girdisi olan bir arayüz offset göndermez ve
        # kullanıcıyı bir biçim hatasıyla durdurmak bu dilimin işi değil.
        sevk_ani = sevk_ani.replace(tzinfo=timezone.utc)
    parametreler = {
        "cid": cid,
        "invoice_id": payload.invoice_id,
        # SABİT ETTN — göç 0083'ün başlığında gerekçe. Gönderim denemeleri
        # boyunca DEĞİŞMEZ; çift belgeye karşı ilk savunma.
        "despatch_uuid": str(uuid_modulu.uuid4()),
        "despatch_number": _belge_numarasi(fatura, payload.despatch_number),
        "issue_date": payload.issue_date or simdi.date(),
        "actual_shipment_at": sevk_ani,
        "carrier_name": (payload.carrier_name or "").strip() or None,
        "carrier_tax_number": (payload.carrier_tax_number or "").strip() or None,
        "driver_name": payload.driver_name.strip(),
        "driver_national_id": payload.driver_national_id.strip(),
        "vehicle_plate": payload.vehicle_plate.strip(),
        "trailer_plate": (payload.trailer_plate or "").strip() or None,
        "delivery_address": payload.delivery_address.strip(),
        "delivery_customer_id": payload.delivery_customer_id,
        "status": edespatch.NONE,
        "now": simdi,
    }
    try:
        yeni_id = db.execute(
            text(
                "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                "issue_date,actual_shipment_at,carrier_name,carrier_tax_number,driver_name,"
                "driver_national_id,vehicle_plate,trailer_plate,delivery_address,"
                "delivery_customer_id,edespatch_status,created_at,updated_at) "
                "VALUES(:cid,:invoice_id,:despatch_uuid,:despatch_number,:issue_date,"
                ":actual_shipment_at,:carrier_name,:carrier_tax_number,:driver_name,"
                ":driver_national_id,:vehicle_plate,:trailer_plate,:delivery_address,"
                ":delivery_customer_id,:status,:now,:now) RETURNING id"
            ),
            parametreler,
        ).scalar_one()
    except IntegrityError:
        # HAKEM VERİTABANIDIR, ÖN SORGU DEĞİL. İki eşzamanlı POST'ta bir
        # `SELECT ... WHERE invoice_id=?` kontrolü İKİSİNİ DE geçirir ve
        # aynı faturaya iki irsaliye açılırdı. `UNIQUE(company_id,
        # invoice_id)` ikinciyi reddeder ve burada 409'a çevrilir.
        db.rollback()
        raise HTTPException(409, IRSALIYE_ZATEN_VAR) from None
    log_invoice_action(
        db, request, cid, payload.invoice_id, "EDESPATCH_CREATE",
        metadata={"despatch_id": yeni_id},
    )
    db.commit()
    return _gorunum(_irsaliye(db, cid, int(yeni_id)))


@router.get("")
def irsaliye_listesi(
    request: Request,
    invoice_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Firmanın irsaliyeleri. SABİT METİN — dinamik SQL yok.

    `invoice_id` süzgeci bir f-string parçasıyla DEĞİL, her zaman bağlı
    olan bir parametreyle kuruluyor: `(:invoice_id IS NULL OR
    invoice_id=:invoice_id)`. Böylece bu dosya
    `DYNAMIC_SQL_FILE_ALLOWLIST`e HİÇ girmiyor — girmeyen bir dosyanın
    parmak izi de kaymaz.
    """
    cid = company_id(request)
    ortak = {"cid": cid, "invoice_id": invoice_id}
    toplam = db.execute(
        text(
            "SELECT COUNT(*) FROM despatch_notes WHERE company_id=:cid "
            "AND (:invoice_id IS NULL OR invoice_id=:invoice_id)"
        ),
        ortak,
    ).scalar_one()
    satirlar = db.execute(
        text(
            "SELECT * FROM despatch_notes WHERE company_id=:cid "
            "AND (:invoice_id IS NULL OR invoice_id=:invoice_id) "
            "ORDER BY issue_date DESC, id DESC LIMIT :limit OFFSET :offset"
        ),
        {**ortak, "limit": limit, "offset": offset},
    ).mappings().all()
    return {"items": [_gorunum(dict(x)) for x in satirlar], "total": int(toplam)}


@router.get("/{despatch_id}")
def irsaliye_detay(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    return _gorunum(_irsaliye(db, company_id(request), despatch_id))


# ------------------------------------------------------------------ e-belge

@router.post("/{despatch_id}/edespatch/submit")
def edespatch_submit(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    """İrsaliyeyi sağlayıcıya gönder. DURUMLA KAPILI, otomatik tekrar YOK.

    İki kapı ve ikisi de farklı bir şeyi ölçüyor:

    1. :data:`~app.einvoice.edespatch.GONDERIM_KAPALI` — canlı ya da
       BİLİNMEYEN bir belge yeniden gönderilmez. ``UNKNOWN`` bu kümede
       ve gerekçesi ``edespatch`` başlığında: bilmediğimiz bir belgeyi
       tekrar göndermek, ilki inmişse ikinci bir irsaliye keser.
    2. Sağlayıcı yapılandırması — yoksa 503 ve DB'ye HİÇ yazılmaz.

    ETTN BURADA ÜRETİLMEZ; satır oluşturulurken üretildi ve DEĞİŞMEZ. Bu
    uç onu yalnız OKUR — çift belgeye karşı korumanın tamamı bu tek
    cümleye dayanıyor.
    """
    cid = company_id(request)
    irsaliye = _irsaliye(db, cid, despatch_id)
    durum = str(irsaliye.get("edespatch_status") or edespatch.NONE).upper()
    if durum in edespatch.GONDERIM_KAPALI:
        raise HTTPException(
            409,
            GONDERIM_KAPALI_MESAJI.get(durum, GONDERIM_KAPALI_VARSAYILAN.format(durum=durum)),
        )
    _saglayici_kapisi()
    saglayici = get_einvoice_provider(settings, company_id=cid)
    try:
        gonderim = _ubl_payload(db, cid, irsaliye)
    except UblBuildError as exc:
        # Belge KURULAMIYOR. Ağa çıkılmadı, satır kirletilmedi: 409, çünkü
        # yapılacak bir iş var (eksik alanı tamamla), 500 değil.
        raise HTTPException(409, f"İrsaliye UBL'i üretilemedi: {str(exc)[:300]}") from None

    sonuc = saglayici.submit_despatch(gonderim)
    yeni_durum = edespatch.durumu_ilerlet(irsaliye.get("edespatch_status"), sonuc.status)
    simdi = utcnow()
    # `or` KASITLI: başarısız bir yeniden gönderim, önceki başarılı
    # gönderimin sakladığı sağlayıcı kimliğini SİLMEMELİ (0081'in
    # `web_key` kararıyla AYNI gerekçe).
    saglayici_uuid = sonuc.external_id or irsaliye.get("edespatch_provider_uuid")
    gib_kodu = sonuc.gib_status_code or irsaliye.get("edespatch_gib_status_code")
    gonderildi = (
        simdi
        if yeni_durum not in (edespatch.NONE, edespatch.FAILED)
        else irsaliye.get("edespatch_submitted_at")
    )
    db.execute(
        text(
            "UPDATE despatch_notes SET edespatch_status=:s,edespatch_provider_uuid=:pu,"
            "edespatch_gib_status_code=:gsc,edespatch_last_error=:err,"
            "edespatch_submitted_at=:sub,updated_at=:now WHERE id=:id AND company_id=:cid"
        ),
        {
            "s": yeni_durum, "pu": saglayici_uuid, "gsc": gib_kodu,
            "err": (sonuc.error or None), "sub": gonderildi, "now": simdi,
            "id": despatch_id, "cid": cid,
        },
    )
    log_invoice_action(
        db, request, cid, int(irsaliye["invoice_id"]), "EDESPATCH_SUBMIT",
        metadata={"despatch_id": despatch_id, "status": yeni_durum},
    )
    db.commit()
    return _gorunum(_irsaliye(db, cid, despatch_id))


@router.get("/{despatch_id}/edespatch/status")
def edespatch_status(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    """YEREL durumu oku. Sağlayıcıya HİÇ gitmez (`einvoice/status` ile aynı)."""
    return _gorunum(_irsaliye(db, company_id(request), despatch_id))


@router.post("/{despatch_id}/edespatch/sync")
def edespatch_sync(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    """Sağlayıcıya SOR ve yerel durumu tazele. Göndermez.

    ``UNKNOWN``DAN ÇIKIŞIN TEK YOLU BURASI. Sağlayıcı belgenin
    VARLIĞINI olumsuzlarsa (``raw["belge_yok"]``)
    :func:`~app.einvoice.edespatch.bilinmeyeni_yok_say` durumu ``FAILED``e
    çevirir ve gönderim yeniden açılır. Bu karar ADAPTÖRDE DEĞİL BURADA
    veriliyor: yerel durumu değiştirmek uç katmanının işidir, ve
    adaptörün "bulamadım"ı ile ucun "gönderim inmemiş" sonucu AYNI ŞEY
    DEĞİLDİR — ikincisi birincisinden ÇIKARILIR.
    """
    cid = company_id(request)
    irsaliye = _irsaliye(db, cid, despatch_id)
    _saglayici_kapisi()
    ettn = str(irsaliye.get("despatch_uuid") or "").strip()
    if not ettn or str(irsaliye.get("edespatch_status") or "").upper() == edespatch.NONE:
        # Hiç gönderilmemiş bir belgenin sorulacak bir durumu YOKTUR.
        # Sağlayıcıya gitmek boş bir yanıt üretir ve bu "belge yok" gibi
        # okunur — sessiz yanlış yerine gürültülü hata.
        raise HTTPException(409, "İrsaliye henüz gönderilmedi; sorgulanacak durum yok.")
    saglayici = get_einvoice_provider(settings, company_id=cid)
    sonuc = saglayici.despatch_status(ettn)
    if (sonuc.raw or {}).get("belge_yok"):
        yeni_durum = edespatch.bilinmeyeni_yok_say(irsaliye.get("edespatch_status"))
    else:
        yeni_durum = edespatch.durumu_ilerlet(irsaliye.get("edespatch_status"), sonuc.status)
    simdi = utcnow()
    db.execute(
        text(
            "UPDATE despatch_notes SET edespatch_status=:s,edespatch_gib_status_code=:gsc,"
            "edespatch_last_error=:err,edespatch_synced_at=:now,updated_at=:now "
            "WHERE id=:id AND company_id=:cid"
        ),
        {
            "s": yeni_durum,
            "gsc": sonuc.gib_status_code or irsaliye.get("edespatch_gib_status_code"),
            "err": (sonuc.error or None), "now": simdi,
            "id": despatch_id, "cid": cid,
        },
    )
    log_invoice_action(
        db, request, cid, int(irsaliye["invoice_id"]), "EDESPATCH_SYNC",
        metadata={"despatch_id": despatch_id, "status": yeni_durum},
    )
    db.commit()
    return _gorunum(_irsaliye(db, cid, despatch_id))


@router.get("/{despatch_id}/edespatch/download")
def edespatch_download(
    despatch_id: int,
    request: Request,
    format: str = Query("xml"),
    db: Session = Depends(get_db),
):
    """Gönderilen e-İrsaliyenin sureti.

    VARSAYILAN ``xml``, ``pdf`` DEĞİL — ve bu `invoices.py`den bilinçli
    olarak AYRILIYOR (orada varsayılan `pdf`). Gerekçe: burada `pdf`
    çalışmıyor (501, modül başlığında ölçüm), yani onu varsayılan yapmak
    parametresiz her çağrıyı bir hataya sürüklerdi.

    ``xml`` AĞA ÇIKMAZ: belge saklanan satırdan ve faturanın donmuş anlık
    görüntüsünden YENİDEN ÜRETİLİR (`build_despatch_xml` saf bir
    dönüşümdür). Sağlayıcıdan XML istemek, elimizde ZATEN olan veriyi
    ikinci bir kaynaktan sormak olurdu.
    """
    bicim = str(format or "").strip().lower()
    if bicim not in INDIRME_FORMATLARI:
        raise HTTPException(400, f"Geçersiz biçim: yalnız {' veya '.join(INDIRME_FORMATLARI)}")
    cid = company_id(request)
    irsaliye = _irsaliye(db, cid, despatch_id)
    if bicim == "pdf":
        # FAIL-CLOSED. Gerekçe ve neden 501 (404/503 değil): modül başlığı.
        raise HTTPException(501, IZIBIZ_EDESPATCH_PDF_UNVERIFIED)
    try:
        icerik = edespatch.build_despatch_xml(_ubl_payload(db, cid, irsaliye))
    except UblBuildError as exc:
        # Saklanan veriden belge YENİDEN ÜRETİLEMİYOR. Yarım bir XML
        # döndürmek, mali belge diye eksik bir dosya vermek olurdu.
        # `UblBuildError`in metni BİZİM alan adlarımızdır, sağlayıcı
        # gövdesi değil — o yüzden yansıtılabilir.
        raise HTTPException(409, f"İrsaliye UBL'i yeniden üretilemedi: {str(exc)[:300]}") from None
    log_invoice_action(
        db, request, cid, int(irsaliye["invoice_id"]), "EDESPATCH_DOWNLOAD",
        metadata={"despatch_id": despatch_id, "format": "xml"},
    )
    db.commit()
    return Response(
        icerik,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{irsaliye["despatch_number"]}.xml"'
        },
    )
