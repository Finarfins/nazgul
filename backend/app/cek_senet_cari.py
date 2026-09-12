"""ÇEK/SENET ↔ CARİ muhasebesi (CS2, göç ``20260914_0086``).

Kaynak: ``docs/cek-senet-kesif-2026-09-10.md`` §2.1 (karar 1: Seçenek A —
çek ALINDIĞINDA cari düşer), §3 (entegrasyon noktaları) ve şefin CS2 kararı.
CS1 durum makinesi (``cek_senet_engine``) SAF kalır; burası geçişlerin
muhasebe YAN ETKİLERİDİR ve uç katmanı onları durum yazımıyla AYNI işlemde
çağırır.

--- KÖPRÜ ------------------------------------------------------------------

Bir çek/senet ile tahsilat (ya da tedarikçiye ödeme) ``payments`` satırıdır ve
cariyi o an düşürür (Seçenek A). Aynı işlemde bir ``cek_senetler`` satırı
(``portfoyde``) ``payment_id`` ile ona bağlanır. Köprünün iki kapısı var:
``POST /api/payments`` (yöntem ``check``/``promissory_note``) ve
``POST /api/cek-senetler`` (``payment_olustur: true``); ikisi de
:func:`cek_ekle` üzerinden yazar.

--- GEÇİŞLERİN YAN ETKİLERİ -----------------------------------------------

``tahsil_edildi``
    ``finance_transactions``a ``tahsil_hesap_id`` hesabına bir satır
    (alınan: ``in``, verilen: ``out``). ``sync_payment_finance``in anlamı:
    ÖDEMENİN kendi finans satırı YOKTUR (çek yöntemi hesap tipine eşlenmez,
    ``METHOD_ACCOUNT_TYPES``), yani para kasaya/bankaya BU satırla, bir kez
    girer. ``reference_type='cek_senet'``.

``karsiliksiz`` / ``iade`` (alınan, köprüden doğmuş)
    Tahsis KORUNUR (§3.2 seçenek b) ve müşteriye ``receivable_charge_documents``
    ``bounced_check`` borç belgesi açılır: tutar = evrak tutarı, vade = evrak
    vadesi, masraf/ceza YOK (karar 5 açık). Cari, çek hiç alınmamış gibi eski
    bakiyesine döner. İADE için ölçülen mevcut mekanizma BUDUR: ``payments``
    tablosunun ters kaydı YOKTUR (ödeme yalnız tahsissizken silinir,
    ``payment_allocation_engine.delete_unallocated_payment``); borcu geri
    yazmanın depodaki tek değişmez yolu borç belgesidir (0041'in servis borcu
    da, 0028'in vade farkı da aynı tabloda). ``karsiliksiz -> iade``da belge
    ZATEN açıktır; ikincisi açılmaz.

    Köprüden DOĞMAMIŞ (``payment_id`` NULL, CS1 kaydı) evrak cariyi hiç
    düşürmedi; ona borç yazmak müşteriyi İKİ KEZ borçlandırırdı. Belge
    açılmaz.

``ciro_edildi``
    ``companies.ciro_tedarikci_odemesi`` AÇIKSA ciro edilen tedarikçiye
    ``payments`` (``supplier``, yöntem ``check``) açılır; KAPALIYSA (varsayılan)
    hiçbir şey yazılmaz. Karar 3 açık.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from .business_time import business_today
from .cek_senet_engine import (
    CIRO_EDILDI,
    IADE,
    KARSILIKSIZ,
    PORTFOYDE,
    TAHSIL_EDILDI,
    TAHSILE_VERILDI,
)
from .cek_senet_schema import cek_senetler
from .finance_engine import finance_transactions, sync_payment_finance
from .money import money
from .tenancy import companies

#: ``receivable_charge_documents.charge_type`` — göç 0086 ``TURLER``in üçüncüsü.
KARSILIKSIZ_CEK: Final = "bounced_check"

#: Ödeme yöntemi <-> evrak türü. Köprünün tek sözlüğü.
YONTEM_TUR: Final[dict[str, str]] = {"check": "cek", "promissory_note": "senet"}
TUR_YONTEM: Final[dict[str, str]] = {v: k for k, v in YONTEM_TUR.items()}
CEK_YONTEMLERI: Final[frozenset[str]] = frozenset(YONTEM_TUR)

#: Ekstre ibaresi (§3.3): "Tahsilat (Çek - Portföyde)".
DURUM_ETIKETLERI: Final[dict[str, str]] = {
    PORTFOYDE: "Portföyde",
    TAHSILE_VERILDI: "Tahsilde",
    TAHSIL_EDILDI: "Tahsil Edildi",
    CIRO_EDILDI: "Ciro Edildi",
    KARSILIKSIZ: "Karşılıksız",
    IADE: "İade",
}

#: Borç belgesinin sebebi -> ekstre ibaresi.
DEKONT_ETIKETLERI: Final[dict[str, str]] = {
    KARSILIKSIZ: "Karşılıksız Çek Dekontu",
    IADE: "İade Çek Dekontu",
}


def cek_ekle(
    db: Session,
    cid: int,
    uid: int | None,
    *,
    tur: str,
    yon: str,
    customer_id: int | None,
    supplier_id: int | None,
    tutar: Decimal,
    vade: date,
    seri_no: str,
    keside_tarihi: date | None = None,
    banka_adi: str | None = None,
    sube_adi: str | None = None,
    hesap_no: str | None = None,
    kesideci: str | None = None,
    notlar: str | None = None,
    payment_id: int | None = None,
) -> int:
    """Portföye yeni evrak — HER evrak ``portfoyde`` doğar."""
    yeni_id = db.execute(
        insert(cek_senetler)
        .values(
            company_id=cid,
            tur=tur,
            yon=yon,
            portfoy_durumu=PORTFOYDE,
            customer_id=customer_id,
            supplier_id=supplier_id,
            tutar=money(tutar),
            vade=vade,
            keside_tarihi=keside_tarihi,
            banka_adi=banka_adi,
            sube_adi=sube_adi,
            hesap_no=hesap_no,
            seri_no=seri_no,
            kesideci=kesideci,
            notlar=notlar,
            payment_id=payment_id,
            created_at=datetime.now(timezone.utc),
            created_by=uid,
        )
        .returning(cek_senetler.c.id)
    ).scalar_one()
    return int(yeni_id)


def odemenin_evraki(db: Session, cid: int, payment_id: int) -> int | None:
    """Ödemeye bağlı evrakın kimliği (yoksa ``None``)."""
    satir = db.execute(
        select(cek_senetler.c.id)
        .where(cek_senetler.c.company_id == cid, cek_senetler.c.payment_id == payment_id)
        .order_by(cek_senetler.c.id)
        .limit(1)
    ).first()
    return int(satir[0]) if satir else None


def ciro_anahtari_acik(db: Session, cid: int) -> bool:
    deger = db.execute(
        select(companies.c.ciro_tedarikci_odemesi).where(companies.c.id == cid)
    ).scalar_one_or_none()
    return bool(deger)


def tahsil_finans_hareketi(
    db: Session, cid: int, evrak: dict[str, Any], hesap_id: int, tarih: date
) -> int:
    alinan = evrak["yon"] == "alinan"
    tur = "Çek" if evrak["tur"] == "cek" else "Senet"
    sonuc = db.execute(
        insert(finance_transactions).values(
            company_id=cid,
            account_id=hesap_id,
            txn_date=tarih.isoformat(),
            direction="in" if alinan else "out",
            amount=money(evrak["tutar"]),
            category="collection" if alinan else "payment",
            payment_method=TUR_YONTEM[str(evrak["tur"])],
            description=f"{tur} tahsili: {evrak['seri_no']}",
            reference_type="cek_senet",
            reference_id=int(evrak["id"]),
            transfer_group=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    return int(sonuc.inserted_primary_key[0])


def borc_belgesi_gerekir_mi(evrak: dict[str, Any]) -> bool:
    """Karşılıksız/iade borç belgesinin ÜÇ koşulu (modül başlığı)."""
    return (
        evrak["yon"] == "alinan"
        and evrak["payment_id"] is not None
        and evrak["charge_document_id"] is None
    )


def karsiliksiz_borc_belgesi(
    db: Session, cid: int, uid: int | None, evrak: dict[str, Any], sebep: str
) -> int:
    """``bounced_check`` borç belgesi — ``posted`` doğar, masraf/ceza YOK."""
    olay = business_today()
    vade = evrak["vade"]
    if not isinstance(vade, date):
        vade = date.fromisoformat(str(vade)[:10])
    tutar = money(evrak["tutar"])
    anlik = {
        "kaynak": "cek_senet",
        "cek_senet_id": int(evrak["id"]),
        "payment_id": int(evrak["payment_id"]),
        "seri_no": str(evrak["seri_no"]),
        "sebep": sebep,
        "vade": vade.isoformat(),
        "olay_tarihi": olay.isoformat(),
        "tutar": format(tutar, ".2f"),
    }
    metin = json.dumps(anlik, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revizyon = int(
        db.execute(
            text(
                """SELECT COALESCE(MAX(revision_no),0)+1
                FROM receivable_charge_documents
                WHERE company_id=:cid AND cek_senet_id=:cek"""
            ),
            {"cid": cid, "cek": int(evrak["id"])},
        ).scalar_one()
    )
    simdi = datetime.now(timezone.utc)
    return int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id,cek_senet_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,
                    calculation_snapshot,gross_amount,status,
                    calculation_fingerprint,revision_no,
                    currency,exchange_rate,created_by,posted_by,created_at,posted_at
                ) VALUES(
                    :cid,:cek,:customer_id,'bounced_check',
                    :olay,:olay,:vade,
                    :anlik,:tutar,'posted',
                    :parmak_izi,:revizyon,
                    'TRY',1,:uid,:uid,:simdi,:simdi
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "cek": int(evrak["id"]),
                "customer_id": int(evrak["customer_id"]),
                "olay": olay,
                "vade": vade,
                "anlik": metin,
                "tutar": tutar,
                "parmak_izi": hashlib.sha256(metin.encode("utf-8")).hexdigest(),
                "revizyon": revizyon,
                "uid": uid,
                "simdi": simdi,
            },
        ).scalar_one()
    )


def ciro_tedarikci_odemesi(
    db: Session, cid: int, evrak: dict[str, Any], tedarikci_id: int, tarih: date
) -> int:
    """Ciro edilen tedarikçiye ödeme — yalnız firma anahtarı AÇIKKEN çağrılır.

    ``POST /api/payments``in motorsuz yazımıyla AYNI iki adım: satır +
    ``sync_payment_finance``. Çek yöntemi hesap tipine eşlenmediği için finans
    satırı DOĞMAZ — para kasadan çıkmadı, elden bir evrak devredildi.
    """
    tur = "Çek" if evrak["tur"] == "cek" else "Senet"
    not_metni = f"{tur} cirosu: {evrak['seri_no']} (evrak #{int(evrak['id'])})"
    yontem = TUR_YONTEM[str(evrak["tur"])]
    payment_id = int(
        db.execute(
            text(
                """INSERT INTO payments(entity_type,entity_id,amount,payment_date,note,
                company_id,payment_method,account_id)
                VALUES('supplier',:tedarikci,:tutar,:tarih,:not_metni,:cid,:yontem,NULL)
                RETURNING id"""
            ),
            {
                "tedarikci": tedarikci_id,
                "tutar": money(evrak["tutar"]),
                "tarih": tarih.isoformat(),
                "not_metni": not_metni,
                "cid": cid,
                "yontem": yontem,
            },
        ).scalar_one()
    )
    sync_payment_finance(
        db, cid, payment_id, "supplier", money(evrak["tutar"]), tarih.isoformat(),
        yontem, not_metni, None,
    )
    return payment_id


def tahsilat_etiketi(temel: str, yontem_etiketi: str, durum: str | None) -> str:
    """Ekstre satırı: ``Tahsilat (Çek)`` ya da ``Tahsilat (Çek - Portföyde)``."""
    if durum is None:
        return f"{temel} ({yontem_etiketi})"
    return f"{temel} ({yontem_etiketi} - {DURUM_ETIKETLERI.get(durum, durum)})"


def dekont_etiketi(anlik: str | None) -> str:
    try:
        sebep = json.loads(anlik or "{}").get("sebep")
    except ValueError:
        sebep = None
    return DEKONT_ETIKETLERI.get(sebep or KARSILIKSIZ, DEKONT_ETIKETLERI[KARSILIKSIZ])
