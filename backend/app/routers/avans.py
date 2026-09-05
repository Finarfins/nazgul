"""Avans, makbuz ödemesi, borsa tescili ve vergi defteri uçları (D2).

D1 (`routers/mustahsil.py`) makbuzu KESER ve bir BORÇ doğurur; bu modül o
borcun PARA TARAFIDIR. Ayrılık bir dosya tercihi değil, KAPSAM ÇİZGİSİDİR:
`mustahsil.py` belgenin YAŞAM DÖNGÜSÜNÜ (taslak → kesildi → iptal)
yönetir, burası ise DEFTERE düşen her şeyi.

--- AVANS MAKBUZA DEĞİL, TEDARİKÇİYE BAĞLANIR ------------------------------

Uç `/suppliers/{id}/advances`tir, `/producer-receipts/{id}/advances` DEĞİL.
Çiftçiye avans verildiğinde ORTADA HENÜZ MAKBUZ YOKTUR — avansın sebebi
zaten makbuzun HENÜZ KESİLMEMİŞ olmasıdır. Avansı makbuzun altına asmak,
kaydı ancak belge kesildikten sonra girilebilir yapardı ve o sırada para
çoktan kasadan çıkmış olurdu.

Hangi makbuza mahsup edileceği `issue` anında FIFO ile BULUNUR
(`avans_engine.makbuz_kesildi`), kullanıcıdan SORULMAZ: mahsup sırasını
elle seçtirmek, "hangi avans hâlâ açık" sorusunun cevabını kullanıcının
tıklama sırasına bırakırdı.

--- AVANS BİR `payments` SATIRIDIR: YAZMA SIRASI ÖLÇÜLDÜ -------------------

Bağ çift yönlüdür (`payments.reference_id` -> avans,
`supplier_advances.payment_id` -> ödeme) ve `payment_id` NOT NULL'dır.
Görünüşte bir TAVUK-YUMURTA var; ÖLÇÜLDÜ, YOK: `payments.reference_id`
bir yabancı anahtar TAŞIMAZ (`app/core_schema.py`), yani sıra şudur ve
ÜÇÜ DE TEK İŞLEMDEDİR:

    1. INSERT payments (reference_type='supplier_advance', reference_id NULL)
    2. INSERT supplier_advances (payment_id = 1'in kimliği)
    3. UPDATE payments SET reference_id = 2'nin kimliği

Ara adımda `reference_id`nin NULL olması bir TUTARSIZLIK DEĞİL: aynı
işlemin dışından hiçbir okuyucu o hâli GÖRMEZ.

`0` gibi bir yer tutucu YAZILMADI ve yazılamazdı: `reference_id=0` var
olmayan bir avansı gösterirdi ve o satır bir daha ASLA "hiç bağlanmamış"
olarak ayırt edilemezdi.

--- BU UÇLAR `/api/payments`TEN GEÇEMEZ (ÖLÇÜLDÜ) --------------------------

Genel ödeme ucu bu iki türü KABUL ETMEZ ve bu bir engel değil, KORUMADIR:

  * `finance.py:_validate_payment` `reference_type`i `entity_type`e göre
    yalnız `order`/`purchase`e izin verir — `supplier_advance` ve
    `producer_receipt` oradan YAZILAMAZ.
  * `finance.py`in `update_payment` ve `delete` yolları `reference_type`i
    NULL OLMAYAN her ödemeyi 409 ile REDDEDER.

Yani D2'nin yazdığı ödeme satırları genel uçtan DÜZENLENEMEZ ve
SİLİNEMEZ: bir avans ancak kendi belgesi üzerinden geri alınabilir. Bu
davranış MEVCUTTU, D2 ona YASLANIYOR — yeniden yazmıyor.

--- YÜKÜMLÜLÜK KAPATMA UCU YOKTUR ------------------------------------------

`/tax-liabilities` YALNIZ OKUR. `settled_at` NULL doğar ve D2'de
kapanmaz; kapatma bir ödemeyi vergi dairesine bağlamayı ister ve
`payments.entity_type` kapalı kümesinde böyle bir cari YOKTUR
(`cash`/`bank`/`pos` hesap türleri de vergi borcunu temsil etmez — D1'in
ölçtüğü engelin AYNISI). Uydurmak yerine ÖLÇÜLMEDİ olarak yazıldı.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..avans_engine import makbuz_odenen, makbuz_tescili
from ..avans_schemas import (
    ExchangeRegistrationWrite,
    ProducerReceiptPaymentWrite,
    SupplierAdvanceWrite,
)
from ..db import get_db
from ..document_engine import PAYMENT_METHODS
from ..finance_engine import sync_payment_finance, validate_payment_account
from ..money import money
from ..tenancy import company_id

router = APIRouter(tags=["producer-advances"])

_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)

# Ölçek D1'in `_metin`iyle AYNI gerekçeyle sabitleniyor (bkz. `_tutar`):
# PostgreSQL NUMERIC(18,2)'yi "12500.00", SQLite "12500" verir.
KURUS = Decimal("0.01")


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _tutar(value: Any) -> str | None:
    """Decimal'i SABİT ÖLÇEKLİ metne çevirir; JSON `number`a ASLA.

    Gerekçenin tamamı `routers/mustahsil.py:_metin`de yazılı ve BURADA
    TEKRARLANMIYOR; tek fark, D2'nin okuduğu her sütunun ölçeğinin `0.01`
    olması (miktar sütunu YOK), yani ölçek parametre DEĞİL sabittir.
    """
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(KURUS), "f")


def _tedarikci_var(db: Session, cid: int, supplier_id: int) -> None:
    """Tedarikçiyi KİRACI YÜKLEMİYLE doğrular; yoksa 404 (403 DEĞİL).

    403 tedarikçinin VAR OLDUĞUNU söylerdi; kiracı sınırı bunu sızdırmaz.
    D1'in `_tedarikci_var`ıyla aynı duruş.
    """
    if not db.execute(
        text("SELECT id FROM suppliers WHERE id=:sid AND company_id=:cid"),
        {"sid": supplier_id, "cid": cid},
    ).first():
        raise HTTPException(404, "Tedarikçi bulunamadı")


def _kesilmis_makbuz(db: Session, cid: int, receipt_id: int) -> dict[str, Any]:
    """Makbuzu okur ve `issued` OLMASINI ŞART KOŞAR.

    Taslağa ödeme yapılamaz: henüz bir BORÇ doğmamıştır. İptal edilmişe de
    yapılamaz: iptal, borcun ORTADAN KALKMASIDIR. İkisi de 409 alır ve
    gövde durumu GÖSTERİR, çünkü "neden olmadı" sorusunun cevabı durumdur.
    """
    satir = db.execute(
        text(
            "SELECT id,supplier_id,status,net_payable,advance_applied_total "
            "FROM producer_receipts WHERE company_id=:cid AND id=:rid"
        ),
        {"cid": cid, "rid": receipt_id},
    ).mappings().first()
    if satir is None:
        raise HTTPException(404, "Müstahsil makbuzu bulunamadı")
    if satir["status"] != "issued":
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_KESILMEMIS",
                "status": satir["status"],
                "message": (
                    "Yalnızca kesilmiş makbuz üzerinde işlem yapılabilir."
                ),
            },
        )
    return dict(satir)


def _odeme_yaz(
    db: Session,
    cid: int,
    supplier_id: int,
    payload: Any,
    reference_type: str,
    reference_id: int | None,
) -> int:
    """`payments` satırını yazar ve kimliğini döner. COMMIT ETMEZ.

    `reference_id` NULL geçilebilir ÇÜNKÜ avans yazımında henüz yoktur
    (bkz. başlıktaki üç adımlı sıra); çağıran onu aynı işlemde günceller.
    """
    if payload.payment_method not in PAYMENT_METHODS:
        raise HTTPException(400, "Geçersiz ödeme yöntemi")
    validate_payment_account(db, cid, payload.payment_method, payload.account_id)
    sonuc = db.execute(
        text(
            "INSERT INTO payments(entity_type,entity_id,amount,payment_date,"
            "note,company_id,payment_method,account_id,reference_type,"
            "reference_id) VALUES('supplier',:sid,:amount,:pdate,:note,:cid,"
            ":method,:aid,:rtype,:rid) RETURNING id"
        ),
        {
            "sid": supplier_id,
            "amount": money(payload.amount),
            "pdate": payload.payment_date,
            "note": payload.note,
            "cid": cid,
            "method": payload.payment_method,
            "aid": payload.account_id,
            "rtype": reference_type,
            "rid": reference_id,
        },
    )
    return int(sonuc.scalar_one())


def _avans_gorunumu(db: Session, cid: int, advance_id: int) -> dict[str, Any]:
    satir = db.execute(
        text(
            "SELECT id,supplier_id,payment_id,amount,remaining_amount,"
            "receipt_id,applied_at,note FROM supplier_advances "
            "WHERE company_id=:cid AND id=:aid"
        ),
        {"cid": cid, "aid": advance_id},
    ).mappings().first()
    if satir is None:
        raise HTTPException(404, "Avans bulunamadı")
    return {
        "id": satir["id"],
        "supplier_id": satir["supplier_id"],
        "payment_id": satir["payment_id"],
        "amount": _tutar(satir["amount"]),
        "remaining_amount": _tutar(satir["remaining_amount"]),
        "receipt_id": satir["receipt_id"],
        "applied_at": satir["applied_at"],
        "note": satir["note"],
    }


# ---------------------------------------------------------------------------
# AVANS
# ---------------------------------------------------------------------------


@router.post("/suppliers/{supplier_id}/advances", status_code=201)
def create_supplier_advance(
    request: Request,
    supplier_id: int,
    payload: SupplierAdvanceWrite,
    db: Session = Depends(get_db),
):
    """Çiftçiye avans: `payments` satırı + `supplier_advances` satırı.

    `remaining_amount` avansın TAMAMI olarak doğar: henüz hiçbir makbuza
    mahsup edilmemiştir. Mahsup `issue` anında FIFO ile olur.
    """
    cid = company_id(request)
    _tedarikci_var(db, cid, supplier_id)
    try:
        # 1 -> 2 -> 3: bkz. başlıktaki "YAZMA SIRASI ÖLÇÜLDÜ".
        payment_id = _odeme_yaz(
            db, cid, supplier_id, payload, "supplier_advance", None
        )
        now = _simdi()
        sonuc = db.execute(
            text(
                "INSERT INTO supplier_advances(company_id,supplier_id,"
                "payment_id,amount,remaining_amount,note,created_at,"
                "updated_at) VALUES(:cid,:sid,:pid,:amount,:amount,:note,"
                ":now,:now) RETURNING id"
            ),
            {
                "cid": cid,
                "sid": supplier_id,
                "pid": payment_id,
                "amount": money(payload.amount),
                "note": payload.note,
                "now": now,
            },
        )
        advance_id = int(sonuc.scalar_one())
        db.execute(
            text(
                "UPDATE payments SET reference_id=:aid "
                "WHERE id=:pid AND company_id=:cid"
            ),
            {"aid": advance_id, "pid": payment_id, "cid": cid},
        )
        sync_payment_finance(
            db, cid, payment_id, "supplier", payload.amount,
            payload.payment_date, payload.payment_method, payload.note,
            payload.account_id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _avans_gorunumu(db, cid, advance_id)


@router.get("/suppliers/{supplier_id}/advances")
def list_supplier_advances(
    request: Request,
    supplier_id: int,
    open_only: bool = False,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    db: Session = Depends(get_db),
):
    """Tedarikçinin avansları; `open_only` yalnız KALANI OLANLARI verir.

    Süzgeç iki SABİT metin arasından SEÇİLİR, birleştirilerek KURULMAZ:
    kullanıcı girdisi hiçbir yolla SQL'e giremez.
    """
    cid = company_id(request)
    _tedarikci_var(db, cid, supplier_id)
    if open_only:
        sorgu = text(
            "SELECT id,supplier_id,payment_id,amount,remaining_amount,"
            "receipt_id,applied_at,note FROM supplier_advances "
            "WHERE company_id=:cid AND supplier_id=:sid AND remaining_amount>0 "
            "ORDER BY id LIMIT :limit OFFSET :offset"
        )
    else:
        sorgu = text(
            "SELECT id,supplier_id,payment_id,amount,remaining_amount,"
            "receipt_id,applied_at,note FROM supplier_advances "
            "WHERE company_id=:cid AND supplier_id=:sid "
            "ORDER BY id LIMIT :limit OFFSET :offset"
        )
    satirlar = db.execute(
        sorgu,
        {"cid": cid, "sid": supplier_id, "limit": limit, "offset": offset},
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "supplier_id": r["supplier_id"],
                "payment_id": r["payment_id"],
                "amount": _tutar(r["amount"]),
                "remaining_amount": _tutar(r["remaining_amount"]),
                "receipt_id": r["receipt_id"],
                "applied_at": r["applied_at"],
                "note": r["note"],
            }
            for r in satirlar
        ]
    }


# ---------------------------------------------------------------------------
# MAKBUZ ÖDEMESİ
# ---------------------------------------------------------------------------


@router.post("/producer-receipts/{receipt_id}/pay")
def pay_producer_receipt(
    request: Request,
    receipt_id: int,
    payload: ProducerReceiptPaymentWrite,
    db: Session = Depends(get_db),
):
    """Makbuzun NAKİT BORCUNA ödeme. Avans mahsubu ZATEN DÜŞÜLMÜŞTÜR.

    Tavan `net_payable − advance_applied_total − o ana kadar ödenen`dir.
    AŞMA 422 İLE REDDEDİLİR, kırpılarak KABUL EDİLMEZ: sessizce kırpmak,
    kullanıcının yazdığından farklı bir tutarı onun adına yazmak olurdu ve
    kasa mutabakatı iki taraftan da doğrulanamaz hâle gelirdi.
    """
    cid = company_id(request)
    makbuz = _kesilmis_makbuz(db, cid, receipt_id)
    net = money(makbuz["net_payable"] or 0)
    mahsup = money(makbuz["advance_applied_total"] or 0)
    nakit_borc = money(net - mahsup)
    odenen = makbuz_odenen(db, cid, receipt_id)
    kalan = money(nakit_borc - odenen)
    if money(payload.amount) > kalan:
        raise HTTPException(
            422,
            {
                "code": "MAKBUZ_ODEME_ASIYOR",
                "cash_due": _tutar(nakit_borc),
                "already_paid": _tutar(odenen),
                "remaining": _tutar(kalan),
                "message": "Ödeme, makbuzun kalan nakit borcunu aşamaz.",
            },
        )
    try:
        payment_id = _odeme_yaz(
            db, cid, int(makbuz["supplier_id"]), payload,
            "producer_receipt", receipt_id,
        )
        sync_payment_finance(
            db, cid, payment_id, "supplier", payload.amount,
            payload.payment_date, payload.payment_method, payload.note,
            payload.account_id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    yeni_odenen = makbuz_odenen(db, cid, receipt_id)
    return {
        "payment_id": payment_id,
        "receipt_id": receipt_id,
        "net_payable": _tutar(net),
        "advance_applied_total": _tutar(mahsup),
        "cash_due": _tutar(nakit_borc),
        "paid_total": _tutar(yeni_odenen),
        "remaining": _tutar(money(nakit_borc - yeni_odenen)),
    }


# ---------------------------------------------------------------------------
# BORSA TESCİLİ
# ---------------------------------------------------------------------------


@router.post(
    "/producer-receipts/{receipt_id}/exchange-registration", status_code=201
)
def register_producer_receipt_on_exchange(
    request: Request,
    receipt_id: int,
    payload: ExchangeRegistrationWrite,
    db: Session = Depends(get_db),
):
    """Makbuzun borsa tescili. BİR makbuz BİR kez; ikincisi 409.

    Tekillik UYGULAMADA DA, ŞEMADA DA duruyor
    (`uq_receipt_exchange_registrations_receipt`): uygulama kontrolü iki
    eşzamanlı isteği ayırt edemez, şema kısıtı ise kullanıcıya ANLAŞILIR
    bir hata veremez. İkisi birlikte gerekiyor.
    """
    cid = company_id(request)
    _kesilmis_makbuz(db, cid, receipt_id)
    if makbuz_tescili(db, cid, receipt_id) is not None:
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_ZATEN_TESCILLI",
                "message": "Bu makbuz borsaya zaten tescil edilmiş.",
            },
        )
    try:
        sonuc = db.execute(
            text(
                "INSERT INTO producer_receipt_exchange_registrations"
                "(company_id,receipt_id,registration_no,exchange_name,"
                "registered_on,fee_amount,note,created_at) "
                "VALUES(:cid,:rid,:no,:exchange,:on_date,:fee,:note,:now) "
                "RETURNING id"
            ),
            {
                "cid": cid,
                "rid": receipt_id,
                "no": payload.registration_no,
                "exchange": payload.exchange_name,
                "on_date": payload.registered_on,
                "fee": money(payload.fee_amount),
                "note": payload.note,
                "now": _simdi(),
            },
        )
        registration_id = int(sonuc.scalar_one())
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    tescil = makbuz_tescili(db, cid, receipt_id) or {}
    return {
        "id": registration_id,
        "receipt_id": receipt_id,
        "registration_no": tescil.get("registration_no"),
        "exchange_name": tescil.get("exchange_name"),
        "registered_on": tescil.get("registered_on"),
        "fee_amount": _tutar(tescil.get("fee_amount")),
        "note": tescil.get("note"),
    }


# ---------------------------------------------------------------------------
# VERGİ YÜKÜMLÜLÜĞÜ DEFTERİ (YALNIZ OKUMA)
# ---------------------------------------------------------------------------


@router.get("/tax-liabilities")
def list_tax_liabilities(
    request: Request,
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    limit: int = _SAYFA,
    offset: int = _ATLA,
    db: Session = Depends(get_db),
):
    """Dönem bazlı yükümlülük listesi. YAZMA UCU YOKTUR (bkz. başlık).

    `period` düzeni ŞEMADA zorlanıyor, sorguda değil: çözülemeyen bir dönem
    422 alır ve `due_period` sütunuyla hiç KARŞILAŞTIRILMAZ. Süzgeç iki
    SABİT metin arasından seçilir, birleştirilerek KURULMAZ.
    """
    cid = company_id(request)
    if period is None:
        sorgu = text(
            "SELECT id,kind,receipt_id,amount,due_period,settled_at,"
            "settlement_payment_id FROM tax_liabilities WHERE company_id=:cid "
            "ORDER BY due_period DESC,id LIMIT :limit OFFSET :offset"
        )
    else:
        sorgu = text(
            "SELECT id,kind,receipt_id,amount,due_period,settled_at,"
            "settlement_payment_id FROM tax_liabilities WHERE company_id=:cid "
            "AND due_period=:period "
            "ORDER BY due_period DESC,id LIMIT :limit OFFSET :offset"
        )
    satirlar = db.execute(
        sorgu,
        {"cid": cid, "period": period, "limit": limit, "offset": offset},
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "kind": r["kind"],
                "receipt_id": r["receipt_id"],
                "amount": _tutar(r["amount"]),
                "due_period": r["due_period"],
                "settled_at": r["settled_at"],
                "settlement_payment_id": r["settlement_payment_id"],
            }
            for r in satirlar
        ]
    }
