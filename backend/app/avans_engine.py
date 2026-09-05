"""Avans mahsubu, vergi yükümlülüğü ve iptal engelleri (D2) — SQL çekirdeği.

Bu modül UÇ TANIMLAMAZ. `routers/avans.py` (D2 uçları) ve
`routers/mustahsil.py` (D1'in `issue`/`cancel` yolları) ORTAK KULLANIR;
mantığın ikinci bir kopyası, biri düzeltildiğinde ötekini SESSİZCE eski
hâlinde bırakırdı — `mustahsil.py`in `farm._turetilmis_net`i İTHAL
ETMESİNİN gerekçesiyle AYNI gerekçe.

--- BÜTÜN SQL BURADA SABİT METİNDİR -----------------------------------------

Hiçbir `text()` çağrısı f-string ya da birleştirme ALMAZ; kiracı yüklemi
(`company_id=:cid`) her sorguda LİTERAL olarak duruyor ve bütün değerler
BAĞLI PARAMETREDİR. Bu, `test_tenant_scoping_guard.py`in dinamik SQL
envanterine bu modülden HİÇBİR giriş düşmemesi demektir — kapı, sayının
kımıldamamasıyla bunu ölçüyor.

--- FIFO MAHSUP: EN ESKİ AVANS ÖNCE -----------------------------------------

Açık avanslar `id` sırasına göre (en eski önce) tüketilir. Sıra bir TERCİH
değil, MUHASEBE KURALIDIR: çiftçiye önce verilen para önce kapanır, yoksa
"hangi avans hâlâ açık" sorusunun cevabı ödeme sırasına göre DEĞİŞİRDİ.

`ORDER BY id`, `created_at` yerine BİLEREK: aynı saniyede yazılmış iki
avansta `created_at` BERABERE kalır ve sıra diyalektin iç sırasına düşerdi;
`id` iki diyalektte de TOPLAM SIRALIDIR.

--- KISMİ MAHSUP AVANSI AÇIK BIRAKIR (BİLİNEN SINIR) ------------------------

Avans netten BÜYÜKSE kalanı düşer ve avans AÇIK kalır; `receipt_id` NULL
DURUR. Yani "bu avansın şu kadarı şu makbuza gitti" olgusu SATIR BAZINDA
okunamaz — makbuz tarafında yalnız TOPLAM durur. Göç 0071'in başlığı bunu
adıyla yazıyor; tam kayıt bir MAHSUP SATIRI tablosu ister ve D2'nin
kapsamı DIŞINDADIR.

--- İPTAL: SİLİNMEYENLER VE SİLİNENLER --------------------------------------

`cancel` DIŞ DÜNYAYA ÇIKMIŞ hiçbir şeyin üstünü çizemez. Bu yüzden ödeme,
borsa tescili, mahsup edilmiş avans ya da KAPANMIŞ bir yükümlülük varsa
iptal 409 ile REDDEDİLİR — geri alınması gereken şey önce KENDİ ucundan
geri alınmalıdır.

KAPANMAMIŞ yükümlülük satırları ise iptalle birlikte SİLİNİR ve bu bir
istisna DEĞİL, kuralın kendisidir: o satırlar makbuzun `issue`ı tarafından
TÜRETİLMİŞTİ ve hiçbir yere beyan edilmemişlerdi. Kalsalardı iptal edilmiş
bir belgeden doğan bir vergi borcu defterde ASILI kalırdı.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import money

# Göç 0071'in `ck_tax_liabilities_kind` CHECK'iyle AYNI iki değer, aynı
# sırayla. Makbuzun iki kesintisi dışında bir tür YOKTUR.
VERGI_TURLERI: tuple[tuple[str, str], ...] = (
    ("withholding", "withholding_total"),
    ("social_security", "social_security_total"),
)

SIFIR = Decimal("0")


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _donem(issued_at: Any) -> str:
    """`issued_at` -> 'YYYY-MM'. Beyanname dönemi makbuzun KESİLDİĞİ aydır.

    `datetime` için `strftime` KULLANILIYOR; dizgeye çevirip ilk yedi
    karakteri kesmek, saat dilimi kaymasını GÖRÜNMEZ kılardı. Sürücü bir
    dizgi verdiyse (SQLite'ta olabiliyor) ISO-8601'in ilk yedi karakteri
    zaten 'YYYY-MM'dir.
    """
    if isinstance(issued_at, datetime):
        return issued_at.strftime("%Y-%m")
    return str(issued_at)[:7]


def makbuz_kesildi(
    db: Session, cid: int, receipt_id: int, satir: dict[str, Any]
) -> dict[str, Decimal]:
    """`issue`ın CAS işlemi İÇİNDE çağrılır: yükümlülük yaz + avans mahsup et.

    KENDİ İŞLEMİNİ AÇMAZ VE COMMIT ETMEZ. Çağıran (`issue`) tek bir
    işlemin sahibidir; buradan bir `commit` atmak, numarası atanmış ama
    yükümlülüğü yazılmamış bir makbuz bırakabilirdi.

    Döner: `{'advance_applied_total': ..., 'cash_due': ...}`.
    """
    now = _simdi()
    donem = _donem(satir.get("issued_at"))

    # --- 1. Vergi yükümlülükleri --------------------------------------------
    # SIFIR TUTARLI SATIR YAZILMAZ: kesinti yoksa yükümlülük de YOKTUR ve
    # 0 tutarlı bir satır beyannamede olmayan bir borç gösterirdi. Göçün
    # `amount > 0` CHECK'i de zaten reddederdi.
    for kind, sutun in VERGI_TURLERI:
        tutar = money(satir.get(sutun) or 0)
        if tutar <= SIFIR:
            continue
        db.execute(
            text(
                "INSERT INTO tax_liabilities"
                "(company_id,kind,receipt_id,amount,due_period,created_at) "
                "VALUES(:cid,:kind,:rid,:amount,:donem,:now)"
            ),
            {
                "cid": cid,
                "kind": kind,
                "rid": receipt_id,
                "amount": tutar,
                "donem": donem,
                "now": now,
            },
        )

    # --- 2. Avans mahsubu (FIFO) --------------------------------------------
    net = money(satir.get("net_payable") or 0)
    mahsup = _avanslari_mahsup_et(
        db, cid, int(satir["supplier_id"]), receipt_id, net, now
    )

    db.execute(
        text(
            "UPDATE producer_receipts SET advance_applied_total=:mahsup,"
            "updated_at=:now WHERE id=:rid AND company_id=:cid"
        ),
        {"mahsup": mahsup, "now": now, "cid": cid, "rid": receipt_id},
    )
    return {"advance_applied_total": mahsup, "cash_due": money(net - mahsup)}


def _avanslari_mahsup_et(
    db: Session,
    cid: int,
    supplier_id: int,
    receipt_id: int,
    net: Decimal,
    now: datetime,
) -> Decimal:
    """Açık avansları EN ESKİDEN başlayarak `net`e kadar tüketir."""
    if net <= SIFIR:
        return SIFIR
    acik = db.execute(
        text(
            "SELECT id,remaining_amount FROM supplier_advances "
            "WHERE company_id=:cid AND supplier_id=:sid AND remaining_amount>0 "
            "ORDER BY id"
        ),
        {"cid": cid, "sid": supplier_id},
    ).mappings().all()

    kalan_net = net
    toplam = SIFIR
    for avans in acik:
        if kalan_net <= SIFIR:
            break
        kalan_avans = money(avans["remaining_amount"])
        uygulanan = kalan_avans if kalan_avans <= kalan_net else kalan_net
        yeni_kalan = money(kalan_avans - uygulanan)
        if yeni_kalan <= SIFIR:
            # TAM tüketildi: avans BU makbuza bağlanır ve kapanır.
            db.execute(
                text(
                    "UPDATE supplier_advances SET remaining_amount=0,"
                    "receipt_id=:rid,applied_at=:now,updated_at=:now "
                    "WHERE id=:aid AND company_id=:cid"
                ),
                {"rid": receipt_id, "now": now, "aid": avans["id"], "cid": cid},
            )
        else:
            # KISMİ: avans AÇIK kalır, `receipt_id` NULL DURUR (bilinen
            # sınır — bkz. başlık).
            db.execute(
                text(
                    "UPDATE supplier_advances SET remaining_amount=:kalan,"
                    "updated_at=:now WHERE id=:aid AND company_id=:cid"
                ),
                {
                    "kalan": yeni_kalan,
                    "now": now,
                    "aid": avans["id"],
                    "cid": cid,
                },
            )
        toplam = money(toplam + uygulanan)
        kalan_net = money(kalan_net - uygulanan)
    return toplam


def makbuz_odenen(db: Session, cid: int, receipt_id: int) -> Decimal:
    """Makbuza şimdiye dek yapılan ödemelerin TOPLAMI."""
    toplam = db.execute(
        text(
            "SELECT COALESCE(SUM(amount),0) FROM payments "
            "WHERE company_id=:cid AND reference_type='producer_receipt' "
            "AND reference_id=:rid"
        ),
        {"cid": cid, "rid": receipt_id},
    ).scalar()
    return money(toplam or 0)


def makbuz_tescili(
    db: Session, cid: int, receipt_id: int
) -> dict[str, Any] | None:
    """Makbuzun borsa tescili (varsa). Makbuz görünümüne GÖMÜLÜR."""
    satir = db.execute(
        text(
            "SELECT id,registration_no,exchange_name,registered_on,fee_amount,"
            "note FROM producer_receipt_exchange_registrations "
            "WHERE company_id=:cid AND receipt_id=:rid"
        ),
        {"cid": cid, "rid": receipt_id},
    ).mappings().first()
    return None if satir is None else dict(satir)


def iptal_engelleri(db: Session, cid: int, receipt_id: int) -> None:
    """İptali REDDEDER ya da geçirir; geçirirse KAPANMAMIŞ yükümlülükleri siler.

    KENDİ İŞLEMİNİ AÇMAZ VE COMMIT ETMEZ — `cancel`ın CAS işlemi içinde
    çağrılır, böylece "iptal oldu ama yükümlülük durdu" ara hâli OLUŞAMAZ.

    Dört engel, dördü de DIŞ DÜNYAYA ÇIKMIŞ bir olgunun izidir.
    """
    if makbuz_odenen(db, cid, receipt_id) > SIFIR:
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_ODENMIS",
                "message": (
                    "Ödemesi yapılmış makbuz iptal edilemez; önce ödemeyi "
                    "geri alın."
                ),
            },
        )

    if makbuz_tescili(db, cid, receipt_id) is not None:
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_TESCILLI",
                "message": (
                    "Borsaya tescil edilmiş makbuz iptal edilemez; önce "
                    "tescili kaldırın."
                ),
            },
        )

    mahsuplu = db.execute(
        text(
            "SELECT COUNT(*) FROM supplier_advances "
            "WHERE company_id=:cid AND receipt_id=:rid"
        ),
        {"cid": cid, "rid": receipt_id},
    ).scalar()
    if int(mahsuplu or 0) > 0:
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_AVANS_MAHSUPLU",
                "message": (
                    "Avans mahsup edilmiş makbuz iptal edilemez; mahsup "
                    "geri alınmadan belge kaldırılamaz."
                ),
            },
        )

    kapanmis = db.execute(
        text(
            "SELECT COUNT(*) FROM tax_liabilities WHERE company_id=:cid "
            "AND receipt_id=:rid AND settled_at IS NOT NULL"
        ),
        {"cid": cid, "rid": receipt_id},
    ).scalar()
    if int(kapanmis or 0) > 0:
        raise HTTPException(
            409,
            {
                "code": "MAKBUZ_VERGI_KAPANMIS",
                "message": (
                    "Vergi yükümlülüğü kapatılmış makbuz iptal edilemez."
                ),
            },
        )

    # KAPANMAMIŞ yükümlülükler SİLİNİR: `issue` onları TÜRETMİŞTİ ve hiçbir
    # yere beyan edilmemişlerdi (bkz. başlık).
    db.execute(
        text(
            "DELETE FROM tax_liabilities WHERE company_id=:cid "
            "AND receipt_id=:rid AND settled_at IS NULL"
        ),
        {"cid": cid, "rid": receipt_id},
    )
