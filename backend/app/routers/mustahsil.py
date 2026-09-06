"""Müstahsil makbuzu uçları (D1) — `/api/producer-receipts`.

Çiftçiden yapılan alımın KAĞIDI: brüt tutar, gelir vergisi STOPAJI, sosyal
güvenlik (Bağ-Kur) kesintisi ve net ödenecek.

--- BU DİLİM DEFTERE HİÇBİR ŞEY YAZMAZ (SINANABİLİR İDDİA) -----------------

Bu modülün kaynağında `stock_movements`, `warehouse_stocks`,
`field_integration_events` ve `payments` ADI GEÇMEZ. Stok defteri ve ödeme
defteri bugünküyle BİREBİR AYNI kalır. `test_mustahsil_makbuzu.py` bunu
kaynak metni üzerinden çiviliyor — iddia bir yorum değil, bir SINAMADIR.

Net ödenecek tutarın çiftçiye ÖDENMESİ bu dilimin işi DEĞİLDİR (avansla
birlikte D2). Makbuz kesmek bir BORÇ DOĞURUR; borcun kapanması ayrı bir
olaydır ve ikisini aynı uca bindirmek, makbuzu iptal etmeyi ödemeyi geri
almak zorunda bırakırdı.

--- STOPAJ YÜKÜMLÜLÜĞÜ `finance_transactions`A YAZILAMADI --------------------

Tasarım, `issue` anında stopaj + SGK toplamını yeni bir `withholding`
kategorisiyle `finance_transactions`a yazmayı öngörüyordu. YAZILMADI ve
sebebi ÖLÇÜLDÜ:

  * `finance_transactions.account_id` NOT NULL'dır
    (`app/finance_engine.py`, tablo tanımı).
  * `ACCOUNT_TYPES` = {`cash`, `bank`, `pos`} — üçü de PARANIN DURDUĞU
    yerlerdir; vergi dairesine olan bir BORCU temsil eden tür YOKTUR.
  * Tablonun okuma yolu satırı gerçek bir hesaba İÇ BİRLEŞTİRİR
    (`routers/finance.py`, `/finance/transactions`) ve satır o hesabın
    hareket listesinde ve gösterge panelinde GÖRÜNÜR.

Yani yükümlülüğü mevcut bir kasa/banka hesabına yazmak, o hesabın
bakiyesini ve hareket dökümünü GERÇEKLEŞMEMİŞ bir para hareketiyle
kirletirdi. Yeni bir hesap TÜRÜ uydurmak ise bu PR'ın yetkisi dışındadır
(sahip kararı gerektirir: `ACCOUNT_TYPES` finans modülünün her doğrulama
kapısında geçiyor).

Bu yüzden D1 yükümlülüğü YALNIZ makbuzun kendi sütunlarında tutuyor
(`withholding_total`, `social_security_total`) ve deftere hiçbir satır
yazmıyor. Seçenekler PR gövdesinde; karar sahibinindir.

--- NUMARA `issue` İLE GELİR, TASLAKTA YOKTUR ------------------------------

`document_sequences` üzerinden, önek "MM". Numarayı taslakta atamak, hiç
kesilmeyen makbuzlar için seride DELİK bırakırdı. `issue` İDEMPOTENT
DEĞİLDİR ama TEKRARI REDDEDER: ikinci çağrı 409 verir ve İKİNCİ BİR NUMARA
ÜRETMEZ — sessizce geçseydi bir makbuz iki numara taşırdı.

Eşzamanlı iki `issue` aynı taslağa çarptığında da TEK numara tüketilir:
önce `draft` → `issuing` compare-and-set (kazanan rowcount==1), sonra
`next_document_no`, sonra `issuing` → `issued` + `receipt_no`. Kaybeden
409 `MAKBUZ_TASLAK_DEGIL` alır ve seriyi İLERLETMEZ. Hepsi tek işlemde;
istisnada geri alınır ve taslak geri gelir.

--- SİLME UCU YOKTUR -------------------------------------------------------

Kesilmiş bir makbuz vergi belgesidir; `cancel` onu `cancelled` yapar ve
SATIRLAR DURUR. Silme ucu, numarası verilmiş bir belgeyi kayıttan
kaldırabilirdi.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..document_engine import next_document_no
from ..mustahsil import MustahsilHatasi, makbuz_topla, satir_hesapla
from ..mustahsil_schemas import ProducerReceiptWrite
from ..tenancy import company_id
from ..units import BirimCozulemedi, resolve as birim_coz
# Kantar fişinin türetilen neti KOPYALANMAZ, İTHAL EDİLİR. Formülün ikinci
# bir kopyası, bileşim kuralı (toplamsal/sıralı) bir gün düzeltildiğinde
# makbuzu fişten AYIRIRDI ve hangisinin doğru olduğu sorulamazdı.
from .farm import _turetilmis_net
# Avans mahsubu, vergi yükümlülüğü ve iptal engelleri D2'nin ÇEKİRDEĞİNDEN
# gelir (`app/avans_engine.py`), buraya KOPYALANMAZ: iki kopya, biri
# düzeltildiğinde ötekini sessizce eski hâlinde bırakırdı.
from ..avans_engine import iptal_engelleri, makbuz_kesildi, makbuz_tescili

router = APIRouter(tags=["producer-receipts"])

MAKBUZ_ONEK = "MM"
_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)

_MAKBUZ_SUTUNLARI = (
    "id,supplier_id,purchase_id,ticket_id,receipt_no,issued_at,gross_amount,"
    "withholding_total,social_security_total,net_payable,advance_applied_total,"
    "status,note"
)
_KALEM_SUTUNLARI = (
    "id,product_id,description,entered_quantity,entered_unit,entered_factor,"
    "base_quantity,ticket_net_snapshot,unit_price,line_gross,withholding_rate,"
    "withholding_amount,social_security_rate,social_security_amount,line_net"
)


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _tarih_suzgeci(deger: str | None, alan: str) -> datetime | None:
    """Süzgeç tarihini ÇÖZER; çözülemezse 422 — 500 DEĞİL.

    ÖLÇÜLDÜ (PostgreSQL 16.13): ham dizgiyi `timestamptz` sütunuyla
    karşılaştırmak, çözülemeyen bir değerde sürücü seviyesinde
    `psycopg2.errors.InvalidDatetimeFormat` atıyor ve uç 500 döndürüyordu —
    yani KULLANICI HATASI SUNUCU HATASI gibi görünüyordu. SQLite'ta aynı
    sorgu sessizce BOŞ liste veriyordu, yani kusur üretim diyalektinde
    500, geliştirmede GÖRÜNMEZDİ.

    `date` de kabul edilir (`2026-09-05`): gün başına normalize edilir ve
    UTC varsayılır. Saat dilimi taşımayan bir değere yerel saat UYDURMAK,
    aynı sorguyu sunucunun bulunduğu yere göre farklı cevaplatırdı.
    """
    if deger is None:
        return None
    metin = deger.strip()
    if metin.endswith("Z"):
        metin = metin[:-1] + "+00:00"
    try:
        cozulen = datetime.fromisoformat(metin)
    except ValueError as exc:
        raise HTTPException(
            422,
            {
                "code": "TARIH_COZULEMEDI",
                "alan": alan,
                "message": f"{alan} ISO-8601 bir tarih/zaman olmalı: {deger!r}",
            },
        ) from exc
    if cozulen.tzinfo is None:
        cozulen = cozulen.replace(tzinfo=timezone.utc)
    return cozulen


# Sütun ölçekleri, ADIYLA: para NUMERIC(18,2), miktar NUMERIC(18,4).
_TUTAR_OLCEGI = Decimal("0.01")
_MIKTAR_OLCEGI = Decimal("0.0001")


def _metin(value: Any, olcek: Decimal) -> str | None:
    """Decimal'i SABİT ÖLÇEKLİ metne çevirir; JSON `number`a ASLA.

    Bir tutarı JSON sayısına çevirmek onu ikili kayan noktadan geçirirdi ve
    0.01'lik bir makbuz kalemi istemcide 0.009999… olarak görünebilirdi.

    ÖLÇEK BURADA ZORLANIYOR ÇÜNKÜ DİYALEKTLER AYRIŞIYOR: PostgreSQL
    NUMERIC(18,2)'yi `Decimal("12500.00")` olarak geri verir, SQLite ise
    ölçeği TAŞIMAZ ve aynı satır `12500` olarak okunur. Ölçeği okuma yolunda
    sabitlemek, aynı makbuzun iki veritabanında iki farklı metin üretmesini
    engeller — ÖLÇÜLDÜ: bu satır olmadan SQLite `"12500"`, PG `"12500.00"`
    döndürüyordu ve ikizler AYRI iddialar yazmak zorunda kalırdı.

    Yuvarlama DEĞİL, ölçek: değerler zaten yazılırken yuvarlanmıştı
    (`mustahsil.satir_hesapla`), burada yalnız TEMSİL sabitleniyor.
    """
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(olcek), "f")


def _makbuz_satiri(db: Session, cid: int, makbuz_id: int) -> dict[str, Any]:
    """Makbuzu KİRACI YÜKLEMİYLE okur; yoksa 404.

    Başka firmanın makbuzu 404 verir, 403 DEĞİL: 403 belgenin VAR OLDUĞUNU
    söylerdi ve bu, kiracı sınırının sızdırdığı bir bilgidir.
    """
    satir = db.execute(
        text(
            f"SELECT {_MAKBUZ_SUTUNLARI} FROM producer_receipts "
            "WHERE company_id=:cid AND id=:rid"
        ),
        {"cid": cid, "rid": makbuz_id},
    ).mappings().first()
    if satir is None:
        raise HTTPException(404, "Müstahsil makbuzu bulunamadı")
    return dict(satir)


def _kalemler(db: Session, cid: int, makbuz_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.execute(
            text(
                f"SELECT {_KALEM_SUTUNLARI} FROM producer_receipt_items "
                "WHERE company_id=:cid AND receipt_id=:rid ORDER BY id"
            ),
            {"cid": cid, "rid": makbuz_id},
        ).mappings().all()
    ]


def _gorunum(
    satir: dict[str, Any],
    kalemler: list[dict[str, Any]],
    tescil: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Makbuz görünümü. `cash_due` TÜRETİLİR, SÜTUN DEĞİLDİR.

    `cash_due = net_payable − advance_applied_total`: çiftçiye hâlâ nakit
    olarak ödenecek olan. Sütun açmak, iki sayının bir gün AYRIŞMASINA izin
    verirdi (biri güncellenir, öteki unutulur); türetilmiş hâli hep tutar.
    """
    mahsup = satir.get("advance_applied_total") or 0
    nakit = Decimal(str(satir["net_payable"] or 0)) - Decimal(str(mahsup))
    return {
        "id": satir["id"],
        "supplier_id": satir["supplier_id"],
        "purchase_id": satir["purchase_id"],
        "ticket_id": satir["ticket_id"],
        "receipt_no": satir["receipt_no"],
        "issued_at": satir["issued_at"],
        "gross_amount": _metin(satir["gross_amount"], _TUTAR_OLCEGI),
        "withholding_total": _metin(satir["withholding_total"], _TUTAR_OLCEGI),
        "social_security_total": _metin(satir["social_security_total"], _TUTAR_OLCEGI),
        "net_payable": _metin(satir["net_payable"], _TUTAR_OLCEGI),
        "advance_applied_total": _metin(mahsup, _TUTAR_OLCEGI),
        "cash_due": _metin(nakit, _TUTAR_OLCEGI),
        "status": satir["status"],
        "note": satir["note"],
        "exchange_registration": tescil,
        "items": [
            {
                "id": k["id"],
                "product_id": k["product_id"],
                "description": k["description"],
                "entered_quantity": _metin(k["entered_quantity"], _MIKTAR_OLCEGI),
                "entered_unit": k["entered_unit"],
                # Katsayı YUVARLANMADAN döner: o gün NEYE İNANILDIĞININ tek
                # kanıtıdır (`units.py`, sahip kararı 1).
                "entered_factor": str(k["entered_factor"]),
                "base_quantity": _metin(k["base_quantity"], _MIKTAR_OLCEGI),
                "ticket_net_snapshot": _metin(k["ticket_net_snapshot"], _MIKTAR_OLCEGI),
                "unit_price": _metin(k["unit_price"], _TUTAR_OLCEGI),
                "line_gross": _metin(k["line_gross"], _TUTAR_OLCEGI),
                "withholding_rate": _metin(k["withholding_rate"], _MIKTAR_OLCEGI),
                "withholding_amount": _metin(k["withholding_amount"], _TUTAR_OLCEGI),
                "social_security_rate": _metin(k["social_security_rate"], _MIKTAR_OLCEGI),
                "social_security_amount": _metin(k["social_security_amount"], _TUTAR_OLCEGI),
                "line_net": _metin(k["line_net"], _TUTAR_OLCEGI),
            }
            for k in kalemler
        ],
    }


def _tedarikci_var(db: Session, cid: int, supplier_id: int) -> None:
    """Tedarikçi BU KİRACIDA görünmüyorsa 404.

    Veritabanındaki bileşik yabancı anahtar son savunmadır; bu ilk. İkisi de
    gerekli: FK PostgreSQL'de kesin ama hata mesajı kullanıcıya
    anlaşılmazdır, uygulama kapısı ise anlaşılır ama tek başına delinebilir.
    """
    varmi = db.execute(
        text("SELECT 1 FROM suppliers WHERE company_id=:cid AND id=:sid"),
        {"cid": cid, "sid": supplier_id},
    ).first()
    if varmi is None:
        raise HTTPException(404, "Tedarikçi bulunamadı")


def _urun_taban_birimi(db: Session, cid: int, product_id: int | None) -> str | None:
    if product_id is None:
        return None
    return db.execute(
        text("SELECT base_unit FROM products WHERE company_id=:cid AND id=:pid"),
        {"cid": cid, "pid": product_id},
    ).scalar()


def _fis_neti(db: Session, cid: int, ticket_id: int) -> Decimal:
    """Kantar fişinin TÜRETİLEN neti — fişin kendi kağıt netinden DEĞİL.

    `farm._turetilmis_net` İTHAL EDİLİYOR (kopyalanmıyor): bileşim kuralı
    tek yerde durmalı.
    """
    satir = db.execute(
        text(
            "SELECT gross_entered_quantity FROM field_harvest_tickets "
            "WHERE company_id=:cid AND id=:tid"
        ),
        {"cid": cid, "tid": ticket_id},
    ).mappings().first()
    if satir is None:
        raise HTTPException(404, "Kantar fişi bulunamadı")
    kesintiler = [
        dict(r)
        for r in db.execute(
            text(
                "SELECT rate_percent FROM field_harvest_ticket_deductions "
                "WHERE company_id=:cid AND ticket_id=:tid"
            ),
            {"cid": cid, "tid": ticket_id},
        ).mappings().all()
    ]
    return _turetilmis_net(
        Decimal(str(satir["gross_entered_quantity"])), kesintiler
    )


@router.post("/producer-receipts", status_code=201)
def create_producer_receipt(
    request: Request,
    payload: ProducerReceiptWrite,
    db: Session = Depends(get_db),
):
    """Makbuzu ve kalemlerini TEK İŞLEMDE yazar. HER ZAMAN `draft`.

    BİRİM ÇÖZÜMÜ VE ARİTMETİK HER SQL'DEN ÖNCE: `units.resolve` ya da
    `mustahsil.satir_hesapla` reddederse hiçbir satır yazılmamış olur.

    Taban birim bildirilmemişse buradan bir varsayılan UYDURULMAZ — girileni
    taban SAYMAK bir olgu uydurmak olurdu (`units.py`, sahip kararı 2). Bu
    yüzden ürün kartı OLMAYAN ya da `base_unit`i boş olan bir kalem 422 ile
    reddedilir; `product_id` sütununun NULL kabul etmesi FK'nın isteğe bağlı
    olmasındandır, yazma yolunun taban birimden VAZGEÇMESİNDEN değil.
    """
    cid = company_id(request)
    _tedarikci_var(db, cid, payload.supplier_id)

    fis_net: Decimal | None = None
    if payload.ticket_id is not None:
        fis_net = _fis_neti(db, cid, payload.ticket_id)

    hazir: list[dict[str, Any]] = []
    for kalem in payload.items:
        taban_birim = _urun_taban_birimi(db, cid, kalem.product_id)
        try:
            cozulen, katsayi = birim_coz(
                kalem.entered_quantity, kalem.entered_unit, taban_birim
            )
        except BirimCozulemedi as exc:
            # AİLE İÇİ RED, 4xx. `sebep` gövdede duruyor çünkü çağıranın
            # yapacağı şey sebebe göre DEĞİŞİR.
            raise HTTPException(
                422,
                {
                    "code": "BIRIM_COZULEMEDI",
                    "sebep": exc.sebep,
                    "message": str(exc),
                },
            ) from exc

        # TABAN MİKTARIN KAYNAĞI, ÖNCELİK SIRASIYLA:
        #   1. kullanıcının açık ezmesi (`base_quantity_override`),
        #   2. fişin TÜRETİLEN neti (fiş bağlıysa),
        #   3. girilen miktarın birim çözümü.
        # Fiş bağlıyken bile kullanıcı ezebilir; o zaman İKİSİ DE saklanır
        # (`ticket_net_snapshot`), çünkü "fiş ne diyordu" ile "kağıda ne
        # yazıldı" AYRI iki olgudur ve ayrıştıkları yer GÖRÜNÜR olmalıdır.
        if kalem.base_quantity_override is not None:
            taban_miktar = kalem.base_quantity_override
        elif fis_net is not None:
            taban_miktar = fis_net
        else:
            taban_miktar = cozulen

        try:
            sonuc = satir_hesapla(
                taban_miktar,
                kalem.unit_price,
                kalem.withholding_rate,
                kalem.social_security_rate,
            )
        except MustahsilHatasi as exc:
            raise HTTPException(
                422,
                {
                    "code": "MUSTAHSIL_HESAPLANAMADI",
                    "sebep": exc.sebep,
                    "message": str(exc),
                },
            ) from exc

        hazir.append(
            {
                "product_id": kalem.product_id,
                "description": kalem.description,
                "entered_quantity": kalem.entered_quantity,
                "entered_unit": kalem.entered_unit,
                "entered_factor": katsayi,
                "base_quantity": taban_miktar,
                "ticket_net_snapshot": fis_net,
                "unit_price": kalem.unit_price,
                "line_gross": sonuc.line_gross,
                "withholding_rate": kalem.withholding_rate,
                "withholding_amount": sonuc.withholding_amount,
                "social_security_rate": kalem.social_security_rate,
                "social_security_amount": sonuc.social_security_amount,
                "line_net": sonuc.line_net,
                "_sonuc": sonuc,
            }
        )

    toplam = makbuz_topla([h["_sonuc"] for h in hazir])
    now = _simdi()
    makbuz_id = db.execute(
        text(
            """INSERT INTO producer_receipts(company_id,supplier_id,purchase_id,
            ticket_id,receipt_no,issued_at,gross_amount,withholding_total,
            social_security_total,net_payable,status,note,created_at,updated_at)
            VALUES(:cid,:supplier_id,:purchase_id,:ticket_id,NULL,NULL,
            :gross_amount,:withholding_total,:social_security_total,
            :net_payable,'draft',:note,:now,:now) RETURNING id"""
        ),
        {
            "cid": cid,
            "supplier_id": payload.supplier_id,
            "purchase_id": payload.purchase_id,
            "ticket_id": payload.ticket_id,
            "gross_amount": toplam.gross_amount,
            "withholding_total": toplam.withholding_total,
            "social_security_total": toplam.social_security_total,
            "net_payable": toplam.net_payable,
            "note": payload.note,
            "now": now,
        },
    ).scalar_one()

    for h in hazir:
        h.pop("_sonuc")
        db.execute(
            text(
                """INSERT INTO producer_receipt_items(company_id,receipt_id,
                product_id,description,entered_quantity,entered_unit,
                entered_factor,base_quantity,ticket_net_snapshot,unit_price,
                line_gross,withholding_rate,withholding_amount,
                social_security_rate,social_security_amount,line_net,
                created_at,updated_at)
                VALUES(:cid,:rid,:product_id,:description,:entered_quantity,
                :entered_unit,:entered_factor,:base_quantity,
                :ticket_net_snapshot,:unit_price,:line_gross,:withholding_rate,
                :withholding_amount,:social_security_rate,
                :social_security_amount,:line_net,:now,:now)"""
            ),
            {"cid": cid, "rid": makbuz_id, "now": now, **h},
        )

    db.commit()
    return _gorunum(_makbuz_satiri(db, cid, makbuz_id), _kalemler(db, cid, makbuz_id))


@router.get("/producer-receipts")
def list_producer_receipts(
    request: Request,
    supplier_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, max_length=20),
    date_from: str | None = Query(default=None, max_length=40),
    date_to: str | None = Query(default=None, max_length=40),
    limit: int = _SAYFA,
    offset: int = _ATLA,
    db: Session = Depends(get_db),
):
    """Makbuz listesi. Tarih aralığı `issued_at` üzerindedir.

    Taslakların `issued_at`i NULL'dur, yani bir tarih aralığı verildiğinde
    taslaklar DÜŞER. Bu bilinçli: "şu iki tarih arasında kesilen makbuzlar"
    sorusunun cevabında hiç kesilmemiş bir kağıt olamaz.
    """
    cid = company_id(request)
    sql = (
        f"SELECT {_MAKBUZ_SUTUNLARI} FROM producer_receipts WHERE company_id=:cid"
    )
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if supplier_id is not None:
        sql += " AND supplier_id=:sid"
        params["sid"] = supplier_id
    if status is not None:
        sql += " AND status=:status"
        params["status"] = status
    baslangic = _tarih_suzgeci(date_from, "date_from")
    if baslangic is not None:
        sql += " AND issued_at IS NOT NULL AND issued_at>=:df"
        params["df"] = baslangic
    bitis = _tarih_suzgeci(date_to, "date_to")
    if bitis is not None:
        sql += " AND issued_at IS NOT NULL AND issued_at<=:dt"
        params["dt"] = bitis
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    satirlar = [dict(r) for r in db.execute(text(sql), params).mappings().all()]
    return [_gorunum(s, _kalemler(db, cid, s["id"])) for s in satirlar]


@router.get("/producer-receipts/{receipt_id}")
def get_producer_receipt(
    request: Request, receipt_id: int, db: Session = Depends(get_db)
):
    cid = company_id(request)
    # Borsa tescili TEKİL görünüme gömülür, LİSTEYE GÖMÜLMEZ: listede her
    # satır için ayrı bir sorgu N+1 üretirdi ve liste zaten belgenin
    # kimliğini veriyor.
    return _gorunum(
        _makbuz_satiri(db, cid, receipt_id),
        _kalemler(db, cid, receipt_id),
        makbuz_tescili(db, cid, receipt_id),
    )


def _taslak_degil_409(db: Session, cid: int, receipt_id: int) -> None:
    """CAS kaybı / tekrar `issue`: 409; durum gövdesinde (varsa) gösterilir."""
    satir = db.execute(
        text(
            "SELECT status FROM producer_receipts "
            "WHERE company_id=:cid AND id=:rid"
        ),
        {"cid": cid, "rid": receipt_id},
    ).mappings().first()
    raise HTTPException(
        409,
        {
            "code": "MAKBUZ_TASLAK_DEGIL",
            "status": None if satir is None else satir["status"],
            "message": "Yalnızca taslak makbuz kesilebilir.",
        },
    )


@router.post("/producer-receipts/{receipt_id}/issue")
def issue_producer_receipt(
    request: Request, receipt_id: int, db: Session = Depends(get_db)
):
    """`draft` -> `issuing` -> `issued`; numarayı `document_sequences`ten ALIR.

    TEKRAR REDDEDİLİR, SESSİZCE GEÇİLMEZ: zaten kesilmiş bir makbuza ikinci
    `issue` 409 verir ve İKİNCİ BİR NUMARA ÜRETMEZ. Sessizce geçseydi (ya da
    "idempotent" diye ilk numarayı geri verseydi) seriden bir numara
    harcanmış ama hiçbir belgeye yazılmamış olurdu — seride açıklanamayan
    bir delik.

    EŞZAMANLI İKİ `issue` AYNI TASLAĞA: okuma-sonra-yazma YETMEZ. Önce
    `status='issuing' WHERE status='draft'` compare-and-set (kazanan
    rowcount==1); numara YALNIZ kazanan tarafından tüketilir; sonra
    `status='issued', receipt_no=... WHERE status='issuing'`. Kaybeden
    409 `MAKBUZ_TASLAK_DEGIL` alır. Hepsi tek işlem; istisnada geri alınır
    ve taslak geri gelir.

    KALEMSİZ MAKBUZ KESİLEMEZ: kalemsiz bir kağıt sıfır tutarlı bir vergi
    belgesi olurdu.
    """
    cid = company_id(request)
    # 404 kapısı: yoksa CAS'a girmeden reddet (başka firmanın makbuzu da 404).
    _makbuz_satiri(db, cid, receipt_id)
    try:
        now = _simdi()
        claim = db.execute(
            text(
                "UPDATE producer_receipts SET status='issuing',updated_at=:now "
                "WHERE id=:rid AND company_id=:cid AND status='draft'"
            ),
            {"now": now, "cid": cid, "rid": receipt_id},
        )
        if claim.rowcount != 1:
            db.rollback()
            _taslak_degil_409(db, cid, receipt_id)

        if not _kalemler(db, cid, receipt_id):
            db.rollback()
            raise HTTPException(
                422,
                {
                    "code": "MAKBUZ_KALEMSIZ",
                    "message": "Kalemsiz makbuz kesilemez.",
                },
            )

        # Numara YALNIZ CAS kazananında tüketilir — kaybeden buraya gelmez.
        numara = next_document_no(db, "producer_receipts", cid, MAKBUZ_ONEK)
        now = _simdi()
        final = db.execute(
            text(
                "UPDATE producer_receipts SET status='issued',receipt_no=:no,"
                "issued_at=:now,updated_at=:now "
                "WHERE id=:rid AND company_id=:cid AND status='issuing'"
            ),
            {"no": numara, "now": now, "cid": cid, "rid": receipt_id},
        )
        if final.rowcount != 1:
            db.rollback()
            _taslak_degil_409(db, cid, receipt_id)

        # D2 KANCASI — AYNI İŞLEMİN İÇİNDE, `issued` YAZILDIKTAN SONRA.
        # Vergi yükümlülüğü satırları ve avans mahsubu buradan doğar.
        # Ayrı bir işleme alınsaydı, numarası verilmiş ama stopajı
        # defterde olmayan bir makbuz ARA DURUM olarak var olabilirdi.
        makbuz_kesildi(db, cid, receipt_id, _makbuz_satiri(db, cid, receipt_id))
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    return _gorunum(
        _makbuz_satiri(db, cid, receipt_id),
        _kalemler(db, cid, receipt_id),
        makbuz_tescili(db, cid, receipt_id),
    )


@router.post("/producer-receipts/{receipt_id}/cancel")
def cancel_producer_receipt(
    request: Request, receipt_id: int, db: Session = Depends(get_db)
):
    """`issued` -> `cancelled`. SATIRLAR DURUR, numara KORUNUR.

    Numara silinmez: iptal edilmiş bir belge de seride YERİNİ TUTAR, yoksa
    seri açıklanamaz biçimde atlardı. Taslak iptal EDİLEMEZ — hiç
    kesilmemiş bir kağıdın iptali yoktur.

    Compare-and-set: `WHERE status='issued'`; kaybeden (zaten iptal /
    taslak / eşzamanlı ikinci cancel) 409 `MAKBUZ_KESILMEMIS`.
    """
    cid = company_id(request)
    _makbuz_satiri(db, cid, receipt_id)
    try:
        result = db.execute(
            text(
                "UPDATE producer_receipts SET status='cancelled',updated_at=:now "
                "WHERE id=:rid AND company_id=:cid AND status='issued'"
            ),
            {"now": _simdi(), "cid": cid, "rid": receipt_id},
        )
        if result.rowcount != 1:
            db.rollback()
            satir = db.execute(
                text(
                    "SELECT status FROM producer_receipts "
                    "WHERE company_id=:cid AND id=:rid"
                ),
                {"cid": cid, "rid": receipt_id},
            ).mappings().first()
            raise HTTPException(
                409,
                {
                    "code": "MAKBUZ_KESILMEMIS",
                    "status": None if satir is None else satir["status"],
                    "message": "Yalnızca kesilmiş makbuz iptal edilebilir.",
                },
            )

        # ENGELLER CAS'TAN SONRA, COMMIT'TEN ÖNCE: iptal ancak dış dünyaya
        # çıkmış hiçbir iz yoksa geçer, ve geçerken KAPANMAMIŞ vergi
        # yükümlülüklerini AYNI İŞLEMDE siler. Engel takılırsa aşağıdaki
        # `rollback` makbuzu `issued` hâline geri getirir.
        iptal_engelleri(db, cid, receipt_id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _gorunum(
        _makbuz_satiri(db, cid, receipt_id),
        _kalemler(db, cid, receipt_id),
        makbuz_tescili(db, cid, receipt_id),
    )
