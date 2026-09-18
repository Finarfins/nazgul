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

--- E4b-1 KISMİ SEVK (göç `20260915_0087`) --------------------------------

E4a'nın bıraktığı iki dikiş söküldü: göçteki `UNIQUE(company_id,
invoice_id)` düştü ve `POST /api/despatch-notes`in "zaten var" 409 dalı
KALKTI (kısıt yokken o dal ölü kod olurdu). Yerine:

* Her irsaliye KENDİ satırlarını `despatch_lines`ta taşır; UBL satırları
  artık fatura kalemlerinden değil O defterden üretilir.
* Kural (keşif §2): bir fatura kalemi için sevk edilen TOPLAM faturalanan
  miktarı aşamaz. Aşan istek 422 `SEVK_MIKTAR_ASIMI`; her kalemin kalanı
  sıfırsa 409 `IRSALIYE_TAMAMLANDI` (istek geçerli, çakışan şey KAYNAĞIN
  DURUMU — E4a'nın 409 gerekçesiyle aynı).
* Hizmet kalemleri (`item_type='LABOR'`) sevk satırı OLAMAZ (Şef kararı):
  gövdesiz istek onları atlar, gövdede adı geçen bir LABOR kalemi 422
  `HIZMET_SATIRI_SEVK_EDILMEZ`.
* HAKEM FATURANIN SATIR KİLİDİDİR: kalan miktar okunmadan ÖNCE fatura
  satırına boş bir UPDATE atılır (`_faturayi_kilitle`). İki eşzamanlı
  kısmi sevk aynı kalanı okuyup İKİSİ DE yazamasın diye — bir SUM kısıtı
  şemada ifade edilemez, kilit edilebilir.

YENİ KOD CORE İLE YAZILDI (`app/despatch_schema.py`); E4a'nın sabit metinli
`text()` sorguları olduğu gibi duruyor ve kiracı yüklemlerini taşımaya
devam ediyor.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_modulu
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Integer, and_, bindparam, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..config import settings
from ..db import get_db
from ..despatch_schema import (
    despatch_lines,
    despatch_notes,
    despatch_response_lines,
    despatch_responses,
    invoice_items,
    invoices,
)
from ..document_engine import next_sequence_value
from ..einvoice import EInvoiceError, UblBuildError, einvoice_configuration, get_einvoice_provider
from ..einvoice import edespatch
from ..einvoice.endpoints import IZIBIZ_EDESPATCH_PDF_UNVERIFIED
from ..invoice_service import log_invoice_action
from ..sinirlar import INT4_UST
from ..tenancy import company_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/despatch-notes", tags=["despatch-notes"])
#: `GET /api/invoices/{id}/despatchable-items`. Ayrı bir yönlendirici çünkü
#: yol fatura ailesinde; izni `app/auth.py`nin SEC-3 kuralından gelir
#: (`/api/invoices` altındaki her güvenli metot `sales`) — irsaliye uçlarıyla
#: AYNI izin, yeni bir kural yazmadan.
fatura_router = APIRouter(prefix="/invoices", tags=["despatch-notes"])

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
    "delivery_postal_code",
    "delivery_customer_id",
    "edespatch_status",
    "edespatch_gib_status_code",
    "edespatch_provider_uuid",
    "edespatch_last_error",
    "edespatch_submitted_at",
    "edespatch_synced_at",
    # E4b-2 (göç 0089): ticari yanıt özeti. `raw_xml` gibi yanıt gövdesi
    # BURADA YOK — yanıtın kendisi `GET .../response`tadır.
    "response_status",
    "response_received_at",
    "created_at",
    "updated_at",
)

EBELGE_YAPILANDIRILMAMIS = "e-Belge entegrasyonu yapılandırılmamış"
IRSALIYE_YOK = "İrsaliye bulunamadı"
FATURA_YOK = "Fatura bulunamadı"
INDIRME_FORMATLARI = ("xml", "pdf")

#: Sevk satırı OLAMAYAN kalem türü (keşif §2, Şef kararı). Göç 0087'nin
#: `HIZMET_TURU` sabitiyle AYNI değer.
HIZMET_TURU = "LABOR"
#: Miktar ölçeği — `despatch_lines.quantity` ve `invoice_items.quantity`
#: ikisi de Numeric(18, 4). Karşılaştırma bu ölçekte yapılır; SQLite'ın
#: REAL depolaması `0.1 + 0.2` gibi toplamlarda ikili kalıntı bırakırdı.
MIKTAR_OLCEGI = Decimal("0.0001")

#: Kısmi sevkin adı konmuş hataları. Gövde `{"code", "message", ...}`
#: (`routers/avans.py`nin `MAKBUZ_*` biçimi); istemci metne değil KODA bakar.
#:
#: 409 vs 422 ayrımı E4a'nın ölçüsüyle aynı: 409 isteğin GEÇERLİ olduğu ama
#: kaynağın durumunun izin vermediği hâl (fatura tamamen sevk edildi); 422
#: isteğin KENDİSİNİN kurala aykırı olduğu hâl (fazla miktar, hizmet satırı).
IRSALIYE_TAMAMLANDI = "IRSALIYE_TAMAMLANDI"
SEVK_MIKTAR_ASIMI = "SEVK_MIKTAR_ASIMI"
HIZMET_SATIRI_SEVK_EDILMEZ = "HIZMET_SATIRI_SEVK_EDILMEZ"
SEVK_KALEMI_YOK = "SEVK_KALEMI_YOK"
FATURA_KALEMI_YOK = "FATURA_KALEMI_YOK"
SEVK_SATIRI_TEKRAR = "SEVK_SATIRI_TEKRAR"
#: Belge numarası hataları (H52/H53). TÜKENDİ ve TEKRAR 409: istek biçimce
#: geçerli, firmanın mevcut numaraları izin vermiyor. YIL_UYUMSUZ 422: istek
#: KENDİ İÇİNDE çelişkili (numaranın yılı ≠ `issue_date`in yılı).
IRSALIYE_NUMARA_TUKENDI = "IRSALIYE_NUMARA_TUKENDI"
IRSALIYE_NO_TEKRAR = "IRSALIYE_NO_TEKRAR"
IRSALIYE_NO_YIL_UYUMSUZ = "IRSALIYE_NO_YIL_UYUMSUZ"

#: `UNKNOWN` durumundaki bir belge yeniden GÖNDERİLMEZ; önce sorulur.
#: Cümle uca özeldir çünkü operatörün yapacağı iş burada BELLİDİR.
GONDERIM_KAPALI_MESAJI = {
    edespatch.UNKNOWN: (
        "Önceki gönderimin sonucu bilinmiyor. Çift irsaliye riskine karşı "
        "önce durum sorgusu (sync) çalıştırın."
    ),
}
GONDERIM_KAPALI_VARSAYILAN = "İrsaliye zaten gönderilmiş; durumu: {durum}"


class SevkSatiri(BaseModel):
    """Kısmi sevkin bir satırı: HANGİ fatura kaleminden NE KADAR.

    Ölçek Numeric(18, 4) ile AYNI: dört haneden fazla kesir PG'de sessizce
    yuvarlanır, SQLite'ta olduğu gibi kalırdı — iki diyalekt iki farklı
    kalan hesaplardı. Reddetmek ikisini eşitler.
    """

    invoice_item_id: int
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


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
    #: GIB sematronu zorunlu tutuyor (goc 0083 basliginda olcum).
    delivery_postal_code: str = Field(min_length=4, max_length=10)
    delivery_customer_id: int | None = None
    despatch_number: str | None = Field(default=None, max_length=40)
    #: YOK -> faturanın LABOR olmayan HER kaleminin KALANI (E4a'nın düz
    #: sevki, eksi hizmet). VAR -> yalnız listelenen kalemler, verilen
    #: miktarla. Boş liste bir anlam taşımaz, o yüzden 422.
    lines: list[SevkSatiri] | None = Field(default=None, min_length=1, max_length=500)


def _gorunum(satir: dict) -> dict:
    """Satırı yanıt gövdesine çevir; zaman/tarih alanları ISO-8601 METİN olur.

    H66: `*_at` alanları UTC'ye çekilip `isoformat()` ile yazılır
    (`2026-09-16T10:00:00+00:00`) — `GET .../response`un
    `response_received_at`i ve `implicit_accept_due_at` ile AYNI biçim. Eski
    `str()` iki lehçede iki ayrı dize veriyordu: PG `datetime` döndürür
    (`"2026-09-16 10:00:00+00:00"`, boşluklu), SQLite ham `text()` okumasında
    naive bir METİN (`"2026-09-16 10:00:00"`, offset'siz). `_utc` ikisini de
    kabul eder, ikisini de aynı ana çevirir. Tarih (`issue_date`) `YYYY-MM-DD`
    kalır. `invoices.py::_einvoice_view` hâlâ `str()` kullanıyor; o fatura
    sözleşmesi bu dilimin kapsamında değil.
    """
    cikti: dict = {}
    for ad in GORUNEN_ALANLAR:
        deger = satir.get(ad)
        if deger is not None and ad.endswith("_at"):
            deger = _utc(deger).isoformat()
        elif isinstance(deger, date):
            deger = deger.isoformat()
        cikti[ad] = deger
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


def _belge_numarasi(db: Session, cid: int, yil: int, verilen: str | None) -> str:
    """İrsaliye belge numarası — GİB BİÇİMİNDE (3 harf + yıl + 9 hane).

    BİÇİM SANDBOX'TA ÖLÇÜLDÜ, VARSAYILMADI. İlk yazımda değer faturanın
    numarasından türetiliyordu (`IRS-<fatura no>`) ve okunabilir olduğu
    için doğru görünüyordu; İzibiz test ortamına yapılan GERÇEK gönderim
    onu REDDETTİ: `ERROR_CODE=10003`, `"Geçersiz ID elemanı değeri. ID
    elemanı 'ABC2009123456789' formatında olmalıdır."` Yani okunabilirlik
    bir biçim kuralının yerine geçmiyor.

    SIRA ARTIK SAYAÇTAN GELİYOR (E4b-1). E4a sırayı FATURA KİMLİĞİNDEN
    türetiyordu ve tekilliği `UNIQUE(company_id, invoice_id)`den bedava
    alıyordu; göç 0087 o kısıtı düşürdü ve bir faturanın ikinci irsaliyesi
    AYNI numarayı alırdı. Kaynak `document_sequences`tır (ikinci bir sayaç
    tablosu DEĞİL), anahtar `despatch_notes:IRS<yıl>` — yıl anahtarda,
    çünkü GİB numarası yılı taşır ve her yıl kendi sırasıdır.

    TOHUM, VERİLMİŞ NUMARALARIN EN BÜYÜĞÜ: sayaç satırı ilk kez doğarken
    firmanın o yıl verdiği en büyük numaranın sırasından başlar. E4a'nın
    fatura kimliğinden türettiği numaralar (ör. `IRS2026000000042`) ve
    operatörün elle verdikleri böylece ÇAKIŞMAZ. Aynı desen sabit 16
    karakter olduğu için metin MAX'ı sayısal MAX'la aynıdır. Sayaç ilk
    değerden sonra yine de alınmış bir numaraya denk gelirse (sonradan elle
    verilmiş bir numara) bir sonrakine geçilir — `next_document_no`nun
    döngüsüyle aynı.

    Operatörün verdiği değer de AYNI desenden geçer — geçmezse 400. Kendi
    numarasını veren biri onu geçerli bir belge sanmamalı.

    H53 — ELLE NUMARA: büyük harfe çevrilmiş hâli (a) `issue_date`in yılını
    taşımalı, yoksa 422 `IRSALIYE_NO_YIL_UYUMSUZ` (GİB numarası yılı taşır;
    2027 tarihli bir belgeye `IRS2026...` vermek iki yılın sırasını karıştırır),
    (b) firmada TEKİL olmalı, yoksa 409 `IRSALIYE_NO_TEKRAR`. Şemada
    `(company_id, despatch_number)` UNIQUE'i YOK (0083/0087 ölçüldü; yalnız
    `uq_despatch_notes_uuid` ve `uq_despatch_notes_company_id`), yani hakem bu
    ön okumadır; karşılaştırma `UPPER()` ile — eski bir küçük harfli kayıt da
    çakışma sayılır.

    H52 — TÜKENME: o yılın `IRS<yıl>999999999`u verilmişse sayaç 9 haneyi
    aşar ve `belge_numarasi_uret` `UblBuildError` atar. Bu bir kusur değil,
    firmanın durumudur: 409 `IRSALIYE_NUMARA_TUKENDI` (eskiden yakalanmayan
    istisna, 500). İstek düştüğü için sayacın ilerlemesi de geri alınır.
    """
    elle = (verilen or "").strip().upper()
    if elle:
        if not edespatch.GIB_BELGE_NO_DESENI.match(elle):
            raise HTTPException(
                400,
                "Belge numarası GİB biçimine uymalı: 3 harf + 4 haneli yıl + "
                "9 hane (örn. IRS2026000000001)",
            )
        if int(elle[3:7]) != int(yil):
            raise _hata(
                422, IRSALIYE_NO_YIL_UYUMSUZ,
                f"Belge numarasının yılı ({elle[3:7]}) düzenleme tarihinin yılıyla "
                f"({int(yil):04d}) uyuşmuyor",
                despatch_number=elle,
            )
        tekrar = db.execute(
            select(despatch_notes.c.id).where(
                despatch_notes.c.company_id == cid,
                func.upper(despatch_notes.c.despatch_number) == elle,
            )
        ).first()
        if tekrar:
            raise _hata(
                409, IRSALIYE_NO_TEKRAR,
                f"{elle} numaralı bir irsaliye bu firmada zaten var",
                despatch_number=elle,
            )
        return elle
    onek = f"{edespatch.BELGE_SERI_ONEKI}{int(yil):04d}"
    en_buyuk = db.execute(
        select(func.max(despatch_notes.c.despatch_number))
        .select_from(despatch_notes)
        .where(
            despatch_notes.c.company_id == cid,
            despatch_notes.c.despatch_number.like(onek + "%"),
        )
    ).scalar()
    tohum = (
        int(en_buyuk[len(onek):])
        if en_buyuk and edespatch.GIB_BELGE_NO_DESENI.match(en_buyuk)
        else 0
    )
    for _ in range(1000):
        sira = next_sequence_value(db, "despatch_notes", cid, onek, tohum)
        try:
            numara = edespatch.belge_numarasi_uret(yil, sira)
        except UblBuildError:
            raise _hata(
                409, IRSALIYE_NUMARA_TUKENDI,
                f"{int(yil):04d} yılı için otomatik irsaliye numarası tükendi "
                f"({onek}999999999 kullanılmış); numarayı elle verin",
            ) from None
        alinmis = db.execute(
            select(despatch_notes.c.id).where(
                despatch_notes.c.company_id == cid,
                despatch_notes.c.despatch_number == numara,
            )
        ).first()
        if not alinmis:
            return numara
    raise RuntimeError("İrsaliye numarası üretilemedi; sayaç olağandışı biçimde çakışıyor")


def _faturayi_kilitle(db: Session, cid: int, fatura_id: int) -> None:
    """Faturanın satırını bu işlemin sonuna dek KİLİTLE — kalan hesabının hakemi.

    Boş bir UPDATE (`updated_at = updated_at`): PostgreSQL'de satır kilidi
    alır, SQLite'ta veritabanı yazma kilidini. İkisinde de eşzamanlı ikinci
    bir kısmi sevk BURADA bekler ve kalan miktarı birincinin satırları
    yazıldıktan SONRA okur. Kilit OKUMADAN ÖNCE alınmak zorunda: önce okuyup
    sonra kilitlemek, iki isteğin aynı kalanı görmesine izin verirdi.
    `SELECT ... FOR UPDATE` yerine UPDATE, çünkü SQLite `FOR UPDATE`i
    sessizce yok sayar — kilit yalnız bir lehçede var olurdu.
    """
    db.execute(
        update(invoices)
        .where(invoices.c.id == fatura_id, invoices.c.company_id == cid)
        .values(updated_at=invoices.c.updated_at)
    )


def _miktar(deger) -> Decimal:
    return Decimal(str(deger if deger is not None else 0)).quantize(MIKTAR_OLCEGI)


def _sevk_durumu(db: Session, cid: int, fatura_id: int) -> list[dict]:
    """Faturanın HER kalemi: faturalanan, sevk edilen, kalan — kiracı kapsamında.

    `sira` kalemin FATURA İÇİNDEKİ sırasıdır (kimlik sırasıyla 1..N, hizmet
    kalemleri DAHİL) — UBL'deki `OrderLineReference/LineID` odur. Toplam
    Python'da alınıyor, SQL `SUM`da DEĞİL: SQLite Numeric'i REAL olarak
    toplar ve dört haneli ölçekte ikili kalıntı bırakır; tek tek okunan
    değerler ise sütun tipinin ölçeğinde `Decimal` gelir.
    """
    kalemler = db.execute(
        select(
            invoice_items.c.id,
            invoice_items.c.item_type,
            invoice_items.c.description,
            invoice_items.c.quantity,
            invoice_items.c.source_snapshot,
        )
        .where(
            invoice_items.c.company_id == cid,
            invoice_items.c.invoice_id == fatura_id,
        )
        .order_by(invoice_items.c.id)
    ).mappings().all()
    sevk_edilen: dict[int, Decimal] = {}
    if kalemler:
        for satir in db.execute(
            select(despatch_lines.c.invoice_item_id, despatch_lines.c.quantity).where(
                despatch_lines.c.company_id == cid,
                despatch_lines.c.invoice_item_id.in_([int(k["id"]) for k in kalemler]),
            )
        ).mappings():
            anahtar = int(satir["invoice_item_id"])
            sevk_edilen[anahtar] = sevk_edilen.get(anahtar, Decimal(0)) + _miktar(
                satir["quantity"]
            )
    durum = []
    for sira, kalem in enumerate(kalemler, start=1):
        faturalanan = _miktar(kalem["quantity"])
        gonderilen = sevk_edilen.get(int(kalem["id"]), Decimal(0)).quantize(MIKTAR_OLCEGI)
        durum.append(
            {
                "id": int(kalem["id"]),
                "sira": sira,
                "item_type": str(kalem["item_type"] or "").upper(),
                "description": kalem["description"],
                "product_id": _urun_kimligi(kalem["source_snapshot"]),
                "faturalanan": faturalanan,
                "sevk_edilen": gonderilen,
                "kalan": max(faturalanan - gonderilen, Decimal(0)).quantize(MIKTAR_OLCEGI),
            }
        )
    return durum


def _urun_kimligi(kaynak) -> int | None:
    """Parça kalemi `work_order_parts` satırını (`product_id` dâhil)
    `source_snapshot`a yazar (`invoice_service.generate_invoice`). Hizmet
    kaleminde ürün yoktur; bozuk ya da eksik anlık görüntü `None` verir."""
    try:
        deger = json.loads(kaynak or "{}").get("product_id")
    except (ValueError, AttributeError):
        return None
    return int(deger) if isinstance(deger, int) and not isinstance(deger, bool) else None


def _hata(kod: int, code: str, mesaj: str, **ek) -> HTTPException:
    return HTTPException(kod, {"code": code, "message": mesaj, **ek})


def _tahsis(durum: list[dict], istek: list[SevkSatiri] | None) -> list[tuple[dict, Decimal]]:
    """İstekten satır tahsisini kur ve kuralı uygula (keşif §2).

    Sonuç FATURA SIRASIYLA döner — istek sırası değil: aynı tahsis her
    zaman aynı satır numaralarını üretir.
    """
    mallar = [d for d in durum if d["item_type"] != HIZMET_TURU]
    if not mallar:
        raise _hata(422, SEVK_KALEMI_YOK, "Faturada sevk edilecek mal kalemi bulunmuyor")
    if all(d["kalan"] <= 0 for d in mallar):
        raise _hata(
            409, IRSALIYE_TAMAMLANDI, "Faturanın bütün mal kalemleri sevk edildi"
        )
    if istek is None:
        return [(d, d["kalan"]) for d in mallar if d["kalan"] > 0]

    kimlikle = {d["id"]: d for d in durum}
    istenen: dict[int, Decimal] = {}
    for satir in istek:
        kalem = kimlikle.get(int(satir.invoice_item_id))
        if kalem is None:
            # Başka faturanın ya da başka firmanın kalemi de BURAYA düşer:
            # durum listesi zaten kiracı ve fatura kapsamında kuruldu.
            raise _hata(
                422, FATURA_KALEMI_YOK, "Kalem bu faturaya ait değil",
                invoice_item_id=satir.invoice_item_id,
            )
        if kalem["id"] in istenen:
            raise _hata(
                422, SEVK_SATIRI_TEKRAR, "Aynı fatura kalemi bir irsaliyede iki kez yazılamaz",
                invoice_item_id=kalem["id"],
            )
        if kalem["item_type"] == HIZMET_TURU:
            raise _hata(
                422, HIZMET_SATIRI_SEVK_EDILMEZ, "Hizmet kalemi e-İrsaliyeyle sevk edilmez",
                invoice_item_id=kalem["id"],
            )
        miktar = Decimal(satir.quantity).quantize(MIKTAR_OLCEGI)
        if miktar > kalem["kalan"]:
            raise _hata(
                422, SEVK_MIKTAR_ASIMI, "Sevk miktarı kalan miktarı aşıyor",
                invoice_item_id=kalem["id"], remaining=str(kalem["kalan"]),
                requested=str(miktar),
            )
        istenen[kalem["id"]] = miktar
    return [(d, istenen[d["id"]]) for d in durum if d["id"] in istenen]


def _irsaliye_satirlari(db: Session, cid: int, irsaliye_id: int) -> list[dict]:
    return [
        dict(x)
        for x in db.execute(
            select(
                despatch_lines.c.line_no,
                despatch_lines.c.invoice_item_id,
                despatch_lines.c.product_id,
                despatch_lines.c.item_name,
                despatch_lines.c.quantity,
                despatch_lines.c.unit_code,
            )
            .where(
                despatch_lines.c.company_id == cid,
                despatch_lines.c.despatch_id == irsaliye_id,
            )
            .order_by(despatch_lines.c.line_no)
        ).mappings().all()
    ]


def _satir_gorunumu(satirlar: list[dict]) -> list[dict]:
    """Miktar METİN — `float` yok (`test_v2_9_decimal_contract`)."""
    return [
        {
            "line_no": int(s["line_no"]),
            "invoice_item_id": int(s["invoice_item_id"]),
            "product_id": s["product_id"],
            "item_name": s["item_name"],
            "quantity": str(_miktar(s["quantity"])),
            "unit_code": s["unit_code"],
        }
        for s in satirlar
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
    # KAYNAK İRSALİYENİN KENDİ SATIRLARI (E4b-1), fatura kalemleri DEĞİL:
    # kısmi sevkte ikisi farklıdır. Göç 0087 eski irsaliyeleri de geri
    # doldurdu, yani satırsız bir irsaliye yalnız mal kalemi hiç olmayan
    # (geri doldurmanın `satirsiz_irsaliye` saydığı) eski bir kayıttır.
    satirlar = _irsaliye_satirlari(db, cid, int(irsaliye["id"]))
    if not satirlar:
        raise UblBuildError("İrsaliyenin sevk satırı yok; irsaliye üretilemez")
    fatura_sirasi = {
        d["id"]: d["sira"] for d in _sevk_durumu(db, cid, int(irsaliye["invoice_id"]))
    }

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
            "delivery_postal_code": irsaliye.get("delivery_postal_code"),
        },
        "lines": [
            {
                "id": int(satir["line_no"]),
                "name": satir["item_name"],
                # Miktar SEVK SATIRININ miktarı ve `Decimal` olarak
                # taşınıyor — `edespatch._miktar` `float`u REDDEDER, çünkü
                # yuvarlanmış bir miktar sevk edileni faturalanandan
                # ayırırdı.
                "quantity": _miktar(satir["quantity"]),
                "unit_code": satir["unit_code"],
                "order_line_id": fatura_sirasi.get(int(satir["invoice_item_id"])),
            }
            for satir in satirlar
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

    # KİLİT, KALAN OKUNMADAN ÖNCE — gerekçe `_faturayi_kilitle`de.
    _faturayi_kilitle(db, cid, payload.invoice_id)
    tahsis = _tahsis(_sevk_durumu(db, cid, payload.invoice_id), payload.lines)

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
        "despatch_number": _belge_numarasi(
            db, cid, (payload.issue_date or simdi.date()).year, payload.despatch_number
        ),
        "issue_date": payload.issue_date or simdi.date(),
        "actual_shipment_at": sevk_ani,
        "carrier_name": (payload.carrier_name or "").strip() or None,
        "carrier_tax_number": (payload.carrier_tax_number or "").strip() or None,
        "driver_name": payload.driver_name.strip(),
        "driver_national_id": payload.driver_national_id.strip(),
        "vehicle_plate": payload.vehicle_plate.strip(),
        "trailer_plate": (payload.trailer_plate or "").strip() or None,
        "delivery_address": payload.delivery_address.strip(),
        "delivery_postal_code": payload.delivery_postal_code.strip(),
        "delivery_customer_id": payload.delivery_customer_id,
        "status": edespatch.NONE,
        "now": simdi,
    }
    # `IntegrityError` YAKALANMIYOR. E4a burada `UNIQUE(company_id,
    # invoice_id)` ihlalini 409 "zaten var"a çeviriyordu; göç 0087 o kısıtı
    # düşürdü ve "fatura tamamlandı" artık yukarıda, kilit altında, adıyla
    # veriliyor. Kalan her ihlal (ör. unutulmuş bir NOT NULL) bir KUSURDUR
    # ve 500 olarak görünmeli — E4a'nın ölçtüğü ders: tanımadığımız bir
    # ihlali 409'a çevirmek operatörü var olmayan bir kaydı aramaya gönderir.
    yeni_id = int(
        db.execute(
            text(
                "INSERT INTO despatch_notes(company_id,invoice_id,despatch_uuid,despatch_number,"
                "issue_date,actual_shipment_at,carrier_name,carrier_tax_number,driver_name,"
                "driver_national_id,vehicle_plate,trailer_plate,delivery_address,"
                "delivery_postal_code,"
                "delivery_customer_id,edespatch_status,created_at,updated_at) "
                "VALUES(:cid,:invoice_id,:despatch_uuid,:despatch_number,:issue_date,"
                ":actual_shipment_at,:carrier_name,:carrier_tax_number,:driver_name,"
                ":driver_national_id,:vehicle_plate,:trailer_plate,:delivery_address,"
                ":delivery_postal_code,"
                ":delivery_customer_id,:status,:now,:now) RETURNING id"
            ),
            parametreler,
        ).scalar_one()
    )
    for satir_no, (kalem, miktar) in enumerate(tahsis, start=1):
        db.execute(
            insert(despatch_lines).values(
                company_id=cid,
                despatch_id=yeni_id,
                invoice_item_id=kalem["id"],
                line_no=satir_no,
                product_id=kalem["product_id"],
                item_name=kalem["description"],
                quantity=miktar,
                unit_code=edespatch.DEFAULT_UNIT_CODE,
                created_at=simdi,
                updated_at=simdi,
            )
        )
    log_invoice_action(
        db, request, cid, payload.invoice_id, "EDESPATCH_CREATE",
        metadata={"despatch_id": yeni_id, "lines": len(tahsis)},
    )
    db.commit()
    return _detay_gorunumu(db, cid, yeni_id)


#: H51: `invoice_id` ve `offset` tavanı `INT4_UST` — tek tavan, her yerde (H54
#: kararı); 2^63-1 sürücünün sınırıydı, ürünün değil. ÖLÇÜLDÜ (PG 16): tipli
#: `::INTEGER` bağı int4 dışındaki `invoice_id`yi (2147483648, -2147483649,
#: 2^63) `NumericValueOutOfRange: integer out of range` ile 500 yapıyordu.
#: `ge=1`: kimlikler 1'den başlar; kardeş uçların 404'üyle aynı ret, daha erken.
@router.get("")
def irsaliye_listesi(
    request: Request,
    invoice_id: int | None = Query(None, ge=1, le=INT4_UST),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=INT4_UST),
    db: Session = Depends(get_db),
):
    """Firmanın irsaliyeleri. SABİT METİN — dinamik SQL yok.

    `invoice_id` süzgeci bir f-string parçasıyla DEĞİL, her zaman bağlı
    olan bir parametreyle kuruluyor: `(:invoice_id IS NULL OR
    invoice_id=:invoice_id)`. Böylece bu dosya
    `DYNAMIC_SQL_FILE_ALLOWLIST`e HİÇ girmiyor — girmeyen bir dosyanın
    parmak izi de kaymaz.
    Sınırlar uçta (`INT4_UST`), çünkü `::INTEGER` dönüşümü aralık taşmasını 500 yapar.

    Her öğe `lines_count` taşır (H68): irsaliyenin sevk satırı sayısı. Satırların
    kendisi detaydadır; liste yalnız sayıyı verir, ekranın irsaliye başına detay
    isteği atmasına gerek kalmaz.
    """
    # H51: parametre TİPLİ bağlanıyor. psycopg3 `None`ı tipsiz gönderir ve
    # PG `:invoice_id IS NULL` içindeki `$2`nin tipini çıkaramaz
    # (`AmbiguousParameter`, süzgeçsiz her istek 500 — SQLite'ta görünmez).
    # `Integer` tipi psycopg lehçesinde `::INTEGER` dönüşümü olarak basılır;
    # SQL metni yine TEK bir sabit, dosya allowlist'e girmiyor.
    tipli_fatura = bindparam("invoice_id", type_=Integer)
    cid = company_id(request)
    ortak = {"cid": cid, "invoice_id": invoice_id}
    toplam = db.execute(
        text(
            "SELECT COUNT(*) FROM despatch_notes WHERE company_id=:cid "
            "AND (:invoice_id IS NULL OR invoice_id=:invoice_id)"
        ).bindparams(tipli_fatura),
        ortak,
    ).scalar_one()
    # H68: `lines_count` AYNI sorgudan, ilişkili bir alt sorguyla — sayfa
    # başına tek sorgu, irsaliye başına ikinci bir okuma (N+1) YOK. Alt sorgu
    # `company_id`yi de eşler: satırın kiracısı irsaliyeninkiyle aynı olmalı.
    satirlar = db.execute(
        text(
            "SELECT d.*, (SELECT COUNT(*) FROM despatch_lines l "
            "WHERE l.company_id=d.company_id AND l.despatch_id=d.id) AS lines_count "
            "FROM despatch_notes d WHERE d.company_id=:cid "
            "AND (:invoice_id IS NULL OR d.invoice_id=:invoice_id) "
            "ORDER BY d.issue_date DESC, d.id DESC LIMIT :limit OFFSET :offset"
        ).bindparams(tipli_fatura),
        {**ortak, "limit": limit, "offset": offset},
    ).mappings().all()
    return {
        "items": [
            {**_gorunum(dict(x)), "lines_count": int(x["lines_count"])} for x in satirlar
        ],
        "total": int(toplam),
    }


def _detay_gorunumu(db: Session, cid: int, irsaliye_id: int) -> dict:
    """Tek irsaliye + SATIRLARI. Liste ucu satır taşımaz (sayfa başına N+1
    sorgu olurdu); satırlar detayda ve oluşturma yanıtında."""
    satir = _irsaliye(db, cid, irsaliye_id)
    govde = _gorunum(satir)
    govde["lines"] = _satir_gorunumu(_irsaliye_satirlari(db, cid, irsaliye_id))
    # E4b-2: 7 gün zımni kabul — GÖSTERİLİR, uygulanmaz (`_zimni_kabul_tarihi`).
    govde["implicit_accept_due_at"] = _zimni_kabul_tarihi(satir)
    return govde


@router.get("/{despatch_id}")
def irsaliye_detay(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    return _detay_gorunumu(db, company_id(request), despatch_id)


@fatura_router.get("/{invoice_id}/despatchable-items")
def sevk_edilebilir_kalemler(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Faturanın sevk edilebilir kalemleri: faturalanan / sevk edilen / kalan.

    YALNIZ OKUR, kilit ALMAZ: gösterilen kalan bir BİLGİDİR, rezervasyon
    değil — hakem yine `POST /api/despatch-notes`in kilidi altındaki
    hesaptır. Hizmet kalemleri (LABOR) listede YOK: sevk edilemeyen bir
    satırı göstermek, arayüzün ona miktar yazdırmasına davetiye olurdu.
    Başka firmanın faturası 404 (`_fatura`, varlık bilgisi sızmaz).

    Miktarlar METİN: `float` yok ve dört haneli ölçek korunur.
    """
    cid = company_id(request)
    _fatura(db, cid, invoice_id)
    mallar = [
        d for d in _sevk_durumu(db, cid, invoice_id) if d["item_type"] != HIZMET_TURU
    ]
    return {
        "invoice_id": invoice_id,
        "items": [
            {
                "invoice_item_id": d["id"],
                "invoice_line_no": d["sira"],
                "description": d["description"],
                "product_id": d["product_id"],
                "invoiced": str(d["faturalanan"]),
                "despatched": str(d["sevk_edilen"]),
                "remaining": str(d["kalan"]),
            }
            for d in mallar
        ],
        "complete": bool(mallar) and all(d["kalan"] <= 0 for d in mallar),
    }


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


#: Yanıtı okunacak durumlar: teslim edilmiş ya da zaten yanıt almış belge.
#: `DELIVERED`dan önce alıcı yanıtı gelemez (`edespatch` başlığı) ve
#: sağlayıcıya sormak boşa bir çağrı olurdu.
YANIT_SORULAN_DURUMLAR = frozenset({edespatch.DELIVERED}) | edespatch.YANIT_DURUMLARI

#: Zımni kabul süresi (keşif §3, 2020 GİB kılavuzu §12; güncel sürümde
#: değişmezliği DOĞRULANMADI). OTOMATİK UYGULANMAZ — yalnız gösterilir.
ZIMNI_KABUL_SURESI = timedelta(days=7)

#: Eşleme hataları — `edespatch.ReceiptAdviceError` kodlarına ek, ucun
#: kendi karşılaştırmasından doğanlar.
YANIT_SATIRI_ESLESMEDI = "YANIT_SATIRI_ESLESMEDI"
YANIT_BELGE_REFERANSI_UYUSMUYOR = "YANIT_BELGE_REFERANSI_UYUSMUYOR"


def _utc(deger) -> datetime | None:
    """SQLite naive döndürür (yazarken UTC'ye normalleştirilmişti), PG oturum
    diliminde döndürür; ikisi de UTC'ye çekilir."""
    if deger is None:
        return None
    if not isinstance(deger, datetime):
        deger = datetime.fromisoformat(str(deger).replace("Z", "+00:00"))
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(timezone.utc)


def _zimni_kabul_tarihi(irsaliye: dict) -> str | None:
    """`actual_shipment_at + 7 gün`, YALNIZ yanıt beklenirken (`DELIVERED`).

    TABAN FİİLİ SEVK ANIDIR, "teslim anı" DEĞİL: şemada teslim anı sütunu yok
    ve keşif §3'ün aktardığı kural süreyi "fiili sevkten itibaren" sayar.
    Yanıt gelmişse (terminal) ya da belge henüz teslim edilmemişse sürenin
    bir anlamı yoktur; `None`.
    """
    if str(irsaliye.get("edespatch_status") or "").upper() != edespatch.DELIVERED:
        return None
    an = _utc(irsaliye.get("actual_shipment_at"))
    return (an + ZIMNI_KABUL_SURESI).isoformat() if an else None


def _bizim_irsaliyemiz_mi(belge: edespatch.YanitBelgesi, irsaliye: dict) -> bool:
    """Yanıt belgesinin kök `DespatchDocumentReference`ı bu irsaliye mi?

    ETTN varsa ETTN karşılaştırılır (tekildir), yoksa belge numarası. İkisi
    de yoksa belge BİZİM DEĞİL sayılır ve ATLANIR: `GetReceiptAdvice`in
    `SEARCH_KEY/UUID` süzgeci DOĞRULANMADI (`endpoints.py`), yani sağlayıcının
    döndürdüğü her belge bu irsaliyeye ait olmayabilir. Referanssız bir
    yanıtı bir irsaliyeye yazmak, başka bir sevkin reddini bu sevke asmak
    olabilirdi. Atlanan belgeler sync yanıtında `skipped` olarak sayılır.
    """
    if belge.irsaliye_uuid:
        return (
            belge.irsaliye_uuid.strip().lower()
            == str(irsaliye.get("despatch_uuid") or "").strip().lower()
        )
    if belge.irsaliye_no:
        return (
            belge.irsaliye_no.strip().upper()
            == str(irsaliye.get("despatch_number") or "").strip().upper()
        )
    return False


def _sevk_satiri_haritasi(db: Session, cid: int, irsaliye_id: int) -> dict[int, int]:
    """`line_no -> despatch_lines.id` — yanıt satırının eşleme anahtarı
    (`edespatch` bölüm 3 başlığı: `DespatchLineReference/LineID`)."""
    return {
        int(r["line_no"]): int(r["id"])
        for r in db.execute(
            select(despatch_lines.c.id, despatch_lines.c.line_no).where(
                despatch_lines.c.company_id == cid,
                despatch_lines.c.despatch_id == irsaliye_id,
            )
        ).mappings()
    }


def _yaniti_esle(
    belge: edespatch.YanitBelgesi, harita: dict[int, int], irsaliye: dict
) -> list[tuple[edespatch.YanitSatiri, int]]:
    """Her yanıt satırını bir sevk satırına bağla; bağlanamayan her satır HATA.

    Kısmen eşleşen bir belgeyi kısmen yazmak YOK: eşleşmeyen satır bir reddi
    taşıyor olabilir ve onu düşürüp kalanını `KABUL` diye yazmak tam olarak
    yanlış olurdu.
    """
    numara = str(irsaliye.get("despatch_number") or "").strip().upper()
    eslesen = []
    for satir in belge.satirlar:
        if satir.belge_no and satir.belge_no.strip().upper() != numara:
            raise edespatch.ReceiptAdviceError(
                YANIT_BELGE_REFERANSI_UYUSMUYOR,
                "Yanıt satırı başka bir irsaliyeyi gösteriyor",
                line=satir.satir_no,
            )
        kimlik = harita.get(satir.satir_no)
        if kimlik is None:
            raise edespatch.ReceiptAdviceError(
                YANIT_SATIRI_ESLESMEDI,
                "Yanıt satırı bu irsaliyenin hiçbir satırına karşılık gelmiyor",
                line=satir.satir_no,
            )
        eslesen.append((satir, kimlik))
    return eslesen


def yaniti_kaydet(
    db: Session,
    cid: int,
    irsaliye_id: int,
    belge: edespatch.YanitBelgesi,
    ham_xml: bytes | str,
    eslesen: list[tuple[edespatch.YanitSatiri, int]],
    simdi: datetime,
    *,
    ek_not: str | None = None,
) -> bool:
    """Yanıt belgesini BİR KEZ yaz. Yazdıysa `True`, zaten varsa `False`.

    İDEMPOTENS İKİ KATLI ve ikisi de gerekli:

    1. ÖNCE OKU. Aynı belge her sync'te yeniden gelir; ikinci çağrı satırı
       görür ve hiçbir şey yazmaz.
    2. YARIŞI UNIQUE ÇÖZER. İki eş zamanlı çağrı ikisi de "yok" okuyabilir;
       ikincinin INSERT'i `uq_despatch_responses_uuid`e çarpar. İhlal bir
       KAYIT NOKTASI içinde yakalanır, yalnız o yazım geri alınır ve cevap
       `False`tur — çağıranın işlemi (durum güncellemesi) ayakta kalır. PG
       ikizi bunu iki oturumla ölçer.

    Başlık ve satırlar AYNI kayıt noktasındadır: satırsız bir başlık kalamaz.
    """
    mevcut = db.execute(
        select(despatch_responses.c.id).where(
            despatch_responses.c.company_id == cid,
            despatch_responses.c.response_uuid == belge.uuid,
        )
    ).first()
    if mevcut:
        return False
    notlar = "\n".join(n for n in (ek_not, belge.notlar) if n) or None
    ham = ham_xml.decode("utf-8", errors="replace") if isinstance(ham_xml, bytes) else str(ham_xml)
    try:
        with db.begin_nested():
            yanit_id = db.execute(
                insert(despatch_responses)
                .values(
                    company_id=cid,
                    despatch_id=irsaliye_id,
                    response_uuid=belge.uuid,
                    response_number=belge.numara,
                    response_type=belge.tur,
                    issue_date=belge.duzenleme,
                    notes=notlar,
                    raw_xml=ham,
                    created_at=simdi,
                )
                .returning(despatch_responses.c.id)
            ).scalar_one()
            for satir, sevk_satiri_id in eslesen:
                db.execute(
                    insert(despatch_response_lines).values(
                        company_id=cid,
                        response_id=yanit_id,
                        despatch_line_id=sevk_satiri_id,
                        received_quantity=satir.alinan,
                        rejected_quantity=satir.reddedilen,
                        reject_reason=satir.gerekce,
                    )
                )
    except IntegrityError:
        tekrar = db.execute(
            select(despatch_responses.c.id).where(
                despatch_responses.c.company_id == cid,
                despatch_responses.c.response_uuid == belge.uuid,
            )
        ).first()
        if tekrar is None:
            # Çarpılan kısıt ETTN değil — bu bir yarış değil bir KUSURDUR ve
            # yutulmamalı (E4a'nın "tanımadığımız ihlali çevirme" dersi).
            raise
        return False
    return True


def _irsaliyeyi_kilitle(db: Session, cid: int, irsaliye_id: int) -> None:
    """Sync'in yazım bölümünü SERİLEŞTİR: boş bir UPDATE (`_faturayi_kilitle`
    ile aynı gerekçe ve aynı diyalekt kararı). Ağ çağrıları kilitten ÖNCE
    yapılır; kilit yalnız yazım süresince tutulur."""
    db.execute(
        update(despatch_notes)
        .where(despatch_notes.c.id == irsaliye_id, despatch_notes.c.company_id == cid)
        .values(updated_at=despatch_notes.c.updated_at)
    )


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

    E4b-2 — TİCARİ YANIT. Durum ``DELIVERED`` ya da daha ilerisiyse
    ``get_receipt_advice`` da sorulur ve bizim irsaliyemize ait her yanıt
    belgesi ``(company_id, response_uuid)`` ile BİR KEZ yazılır
    (:func:`yaniti_kaydet`). Üç kural:

    1. ÖNCE AĞ, SONRA YAZIM. İki sağlayıcı çağrısı da hiçbir şey yazmadan
       yapılır. Yanıt sorgusu düşerse 502 ve HİÇBİR ŞEY yazılmaz — durum
       sorgusunun sonucu da.
    2. AYRIŞTIR VE EŞLE, SONRA YAZ. Okunamayan ya da eşlenemeyen bir belge
       422 (adı konmuş kod) döner; hata ``edespatch_last_error``a YAZILIR,
       durum sorgusunun sonucu saklanır, hiçbir yanıt satırı yazılmaz. 500
       DEĞİL: kusur bizde değil belgede.
    3. TEK İŞLEM, SATIR KİLİDİ ALTINDA. Kilit alındıktan sonra satır YENİDEN
       okunur ve geçiş o güncel değere uygulanır — eş zamanlı iki sync
       birbirinin yazdığı yanıt durumunu eski bir değerle EZEMEZ.

    Yanıt türü durumu :func:`~app.einvoice.edespatch.durumu_ilerlet` ile
    ilerletir; terminal bir belge kımıldamaz. İkinci sync aynı belgeyle
    ``changed: false`` döner.
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

    # --- 1. AĞ: durum, sonra (gerekirse) yanıtlar. Hiçbir şey yazılmıyor. ---
    sonuc = saglayici.despatch_status(ettn)
    belge_yok = bool((sonuc.raw or {}).get("belge_yok"))
    if belge_yok:
        ag_durumu = edespatch.bilinmeyeni_yok_say(irsaliye.get("edespatch_status"))
    else:
        ag_durumu = edespatch.durumu_ilerlet(irsaliye.get("edespatch_status"), sonuc.status)
    belgeler: list[bytes] = []
    yanit_soruldu = ag_durumu in YANIT_SORULAN_DURUMLAR
    if yanit_soruldu:
        try:
            belgeler = list(saglayici.get_receipt_advice(ettn))
        except EInvoiceError as exc:
            # `get_receipt_advice` boş liste ile hatayı KARIŞTIRMAZ; burada da
            # karıştırılmaz. `fetch_pdf`in 502 kalıbı: hata BİZDE değil.
            # `EInvoiceError.message` sağlayıcı katmanında temizlenmiş SABİT
            # cümledir, yansıtılabilir.
            db.rollback()
            logger.warning("e-İrsaliye yanıt sorgusu başarısız (sınıf=%s)", exc.code)
            raise HTTPException(502, f"e-İrsaliye yanıtı alınamadı: {exc.message[:400]}") from None
        except Exception as exc:
            # Sağlayıcı katmanının HİÇ görmediği bir istisna: metni temizlenmemiş,
            # istemciye GİTMEZ.
            db.rollback()
            logger.warning("e-İrsaliye yanıt sorgusu istisnayla düştü: %s", type(exc).__name__)
            raise HTTPException(502, "e-İrsaliye yanıtı alınamadı") from None

    # --- 2. AYRIŞTIR + EŞLE (saf; hata olursa hiçbir yanıt yazılmaz) --------
    harita = _sevk_satiri_haritasi(db, cid, despatch_id) if belgeler else {}
    cozulen: list[tuple[edespatch.YanitBelgesi, bytes, list]] = []
    atlanan = 0
    esleme_hatasi: edespatch.ReceiptAdviceError | None = None
    try:
        for ham in belgeler:
            belge = edespatch.receipt_advice_coz(ham)
            if not _bizim_irsaliyemiz_mi(belge, irsaliye):
                atlanan += 1
                continue
            cozulen.append((belge, ham, _yaniti_esle(belge, harita, irsaliye)))
    except edespatch.ReceiptAdviceError as exc:
        esleme_hatasi = exc
        cozulen = []
    # En eski belge önce: durumu İLK yanıt belirler, sonrakiler kayıt içindir.
    cozulen.sort(key=lambda x: (x[0].duzenleme, x[0].numara, x[0].uuid))

    # --- 3. YAZIM — satır kilidi altında, güncel değer üzerinden ------------
    _irsaliyeyi_kilitle(db, cid, despatch_id)
    guncel = _irsaliye(db, cid, despatch_id)
    onceki_durum = str(guncel.get("edespatch_status") or edespatch.NONE).upper()
    if belge_yok:
        durum = edespatch.bilinmeyeni_yok_say(onceki_durum)
    else:
        durum = edespatch.durumu_ilerlet(onceki_durum, sonuc.status)
    yanit_ozeti = guncel.get("response_status")
    yanit_ani = guncel.get("response_received_at")
    simdi = utcnow()
    kaydedilen = 0
    for belge, ham, eslesen in cozulen:
        hedef = edespatch.yanit_durumu(belge.tur)
        ek_not = (
            edespatch.TESLIM_SONRASI_RED_NOTU
            if belge.tur == edespatch.RED and durum == edespatch.DELIVERED
            else None
        )
        if not yaniti_kaydet(db, cid, despatch_id, belge, ham, eslesen, simdi, ek_not=ek_not):
            continue
        kaydedilen += 1
        yeni = edespatch.durumu_ilerlet(durum, hedef)
        if yeni != durum and yeni == hedef:
            durum = yeni
            yanit_ozeti = belge.tur
            yanit_ani = simdi

    son_hata = (
        f"{esleme_hatasi.code}: {esleme_hatasi.message}"
        if esleme_hatasi is not None
        else (sonuc.error or None)
    )
    db.execute(
        text(
            "UPDATE despatch_notes SET edespatch_status=:s,edespatch_gib_status_code=:gsc,"
            "edespatch_last_error=:err,edespatch_synced_at=:now,updated_at=:now,"
            "response_status=:rs,response_received_at=:ra "
            "WHERE id=:id AND company_id=:cid"
        ),
        {
            "s": durum,
            # Ham sağlayıcı kodu KORUNUR: yanıt durumu onun yerine geçmez
            # (teslim sonrası RED dahil — Şef kararı).
            "gsc": sonuc.gib_status_code or guncel.get("edespatch_gib_status_code"),
            "err": son_hata, "now": simdi,
            "rs": yanit_ozeti, "ra": yanit_ani,
            "id": despatch_id, "cid": cid,
        },
    )
    degisti = (
        kaydedilen > 0
        or durum != onceki_durum
        or yanit_ozeti != guncel.get("response_status")
    )
    ozet = {
        "asked": yanit_soruldu,
        "found": len(belgeler),
        "recorded": kaydedilen,
        "skipped": atlanan,
    }
    log_invoice_action(
        db, request, cid, int(irsaliye["invoice_id"]), "EDESPATCH_SYNC",
        metadata={
            "despatch_id": despatch_id, "status": durum, "receipt_advice": ozet,
            **({"error_code": esleme_hatasi.code} if esleme_hatasi is not None else {}),
        },
    )
    db.commit()
    if esleme_hatasi is not None:
        raise _hata(422, esleme_hatasi.code, esleme_hatasi.message, **esleme_hatasi.ek)
    govde = _gorunum(_irsaliye(db, cid, despatch_id))
    govde["changed"] = degisti
    govde["receipt_advice"] = ozet
    return govde


@router.get("/{despatch_id}/response")
def irsaliye_yaniti(despatch_id: int, request: Request, db: Session = Depends(get_db)):
    """İrsaliyenin ticari yanıtı: başlık + sevk satırlarıyla birleşmiş satırlar.

    YALNIZ OKUR, ağa çıkmaz. İzin ``sales`` — `/api/despatch-notes` önek
    kuralından (`app/auth.py`), YENİ KURAL YOK: yanıt satırları ürün adı ve
    miktarı taşır, irsaliyenin kendisiyle aynı ticari veridir. Başka firmanın
    irsaliyesi 404 (`_irsaliye`, varlık bilgisi sızmaz).

    ETKİLİ YANIT İLK KAYDEDİLENDİR (kimlik sırası): durumu o belirledi ve
    durum makinesi terminalden kımıldamaz. Sonraki belgeler saklanır;
    ``responses_count`` onların varlığını söyler.

    ``raw_xml`` DÖNMEZ (göç 0089 başlığı). Yanıt yoksa ``response`` ``null``
    ve ``lines`` boş — 404 DEĞİL: irsaliye var, yanıtı henüz yok.
    Miktarlar METİN (`float` yok).
    """
    cid = company_id(request)
    irsaliye = _irsaliye(db, cid, despatch_id)
    yanitlar = db.execute(
        select(
            despatch_responses.c.id,
            despatch_responses.c.response_uuid,
            despatch_responses.c.response_number,
            despatch_responses.c.response_type,
            despatch_responses.c.issue_date,
            despatch_responses.c.notes,
            despatch_responses.c.created_at,
        )
        .where(
            despatch_responses.c.company_id == cid,
            despatch_responses.c.despatch_id == despatch_id,
        )
        .order_by(despatch_responses.c.id)
    ).mappings().all()
    alindi = _utc(irsaliye.get("response_received_at"))
    govde: dict = {
        "despatch_id": despatch_id,
        "edespatch_status": irsaliye.get("edespatch_status"),
        "response_status": irsaliye.get("response_status"),
        "response_received_at": alindi.isoformat() if alindi else None,
        "implicit_accept_due_at": _zimni_kabul_tarihi(irsaliye),
        "responses_count": len(yanitlar),
        "response": None,
        "lines": [],
    }
    if not yanitlar:
        return govde
    etkili = yanitlar[0]
    govde["response"] = {
        "response_uuid": etkili["response_uuid"],
        "response_number": etkili["response_number"],
        "response_type": etkili["response_type"],
        "issue_date": str(etkili["issue_date"])[:10],
        "notes": etkili["notes"],
        "created_at": _utc(etkili["created_at"]).isoformat(),
    }
    satirlar = db.execute(
        select(
            despatch_response_lines.c.despatch_line_id,
            despatch_lines.c.line_no,
            despatch_lines.c.product_id,
            despatch_lines.c.item_name,
            despatch_lines.c.quantity,
            despatch_response_lines.c.received_quantity,
            despatch_response_lines.c.rejected_quantity,
            despatch_response_lines.c.reject_reason,
        )
        .select_from(
            despatch_response_lines.join(
                despatch_lines,
                and_(
                    despatch_lines.c.company_id == despatch_response_lines.c.company_id,
                    despatch_lines.c.id == despatch_response_lines.c.despatch_line_id,
                ),
            )
        )
        .where(
            despatch_response_lines.c.company_id == cid,
            despatch_response_lines.c.response_id == etkili["id"],
        )
        .order_by(despatch_lines.c.line_no)
    ).mappings().all()
    govde["lines"] = [
        {
            "despatch_line_id": int(s["despatch_line_id"]),
            "line_no": int(s["line_no"]),
            "product_id": s["product_id"],
            "item_name": s["item_name"],
            "despatched_quantity": str(_miktar(s["quantity"])),
            "received_quantity": str(_miktar(s["received_quantity"])),
            "rejected_quantity": str(_miktar(s["rejected_quantity"])),
            "reject_reason": s["reject_reason"],
        }
        for s in satirlar
    ]
    return govde


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
