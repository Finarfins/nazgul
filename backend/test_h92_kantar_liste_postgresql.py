"""PostgreSQL ikizi: H92 "KANTAR LISTE" sayfalaması ve tarama tavanı gerçek PG'de.

GÖÇ YOK. SQLite ikizi `tests/test_h92_kantar_liste_sayfalama.py` davranışı
ve mutasyon tablosunu taşıyor; bu dosya AYNI iki senaryoyu gerçek
PostgreSQL'de koşturur.

--- BU İKİZ NEDEN VAR ------------------------------------------------------

Sayfalama `makbuz_listesi`nin `ORDER BY id DESC LIMIT :limit OFFSET :offset`
metnine yaslanır ve tavan son sayfada `LIMIT`i KIRPAR. Paylaşık bir PG
şemasında `id` serisi başka koşuların satırlarıyla araya girer (SQLite'ın
boş dosyasındaki gibi bitişik DEĞİLDİR); sıra, `company_id`/`supplier_id`
süzgeci ve tavanın satır sayısı yine de AYNI olmalıdır. Satırlar KENDİ
firma önekiyle yazılır ve yalnız onlar silinir (`test_f10_1c` ikizinin
kuralı).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR (paylaşık şema; WA2 ikizinin gerekçesi).
KOSU = uuid4().hex[:8]
TAVAN = 500

_NO = iter(range(1, 1_000_000))


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H92 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — FİRMA ÖNEKİYLE, tabloyu süpürmeden."""
    firma_alt = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        for tablo in (
            "producer_receipt_items",
            "producer_receipts",
            "field_harvest_ticket_deductions",
            "field_harvest_tickets",
            "field_harvests",
            "crop_seasons",
            "farm_parcels",
            "farms",
            "suppliers",
        ):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firma_alt}"),
                {"o": KOSU + "%"},
            )
        b.execute(
            text(
                "DELETE FROM companies WHERE name LIKE :o"
                " AND id NOT IN (SELECT company_id FROM activity_logs)"
            ),
            {"o": KOSU + "%"},
        )


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        acilisa_cek(engine)
        engine.dispose()


@pytest.fixture()
def dunya(motor):
    """BİR firma, İKİ tedarikçi (çiftçi + başkası), BİR hasat zinciri."""
    an = datetime.now(timezone.utc)
    with motor.begin() as b:

        def ekle(sql: str, **p) -> int:
            return int(b.execute(text(sql + " RETURNING id"), p).scalar_one())

        firma = ekle(
            "INSERT INTO companies(name,is_active,created_at) VALUES(:n,TRUE,:t)",
            n=KOSU + "-firma",
            t=an,
        )

        def tedarikci(ad: str) -> int:
            return ekle(
                "INSERT INTO suppliers(name,is_active,company_id,"
                "opening_balance,risk_limit,payment_term_days)"
                " VALUES(:a,TRUE,:c,0,0,0)",
                a=KOSU + ad,
                c=firma,
            )

        ciftci, baskasi = tedarikci("-ciftci"), tedarikci("-baskasi")
        ciftlik = ekle(
            "INSERT INTO farms(company_id,code,name,status,created_at,updated_at)"
            " VALUES(:c,'K1','H92 Ciftlik','ACTIVE',:s,:s)",
            c=firma,
            s=an,
        )
        parsel = ekle(
            "INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,"
            "status,created_at,updated_at)"
            " VALUES(:c,:f,'KP','H92 Parsel',10,'ACTIVE',:s,:s)",
            c=firma,
            f=ciftlik,
            s=an,
        )
        sezon = ekle(
            "INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,status,"
            "created_at,updated_at) VALUES(:c,:p,2026,'Bugday','ACTIVE',:s,:s)",
            c=firma,
            p=parsel,
            s=an,
        )
        hasat = ekle(
            "INSERT INTO field_harvests(company_id,season_id,harvested_on,quantity,"
            "unit,status,created_at,updated_at)"
            " VALUES(:c,:sz,'2026-09-10',1000,'KG','RECORDED',:s,:s)",
            c=firma,
            sz=sezon,
            s=an,
        )
    return {"firma": firma, "ciftci": ciftci, "baskasi": baskasi, "hasat": hasat}


def _fis(motor, dunya, ticket_no: str) -> int:
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        return int(
            b.execute(
                text(
                    "INSERT INTO field_harvest_tickets(company_id,harvest_id,"
                    "ticket_no,gross_entered_quantity,entered_unit,entered_factor,"
                    "base_quantity,created_at,updated_at)"
                    " VALUES(:c,:h,:no,1000,'KG',1,1000,:t,:t) RETURNING id"
                ),
                {"c": dunya["firma"], "h": dunya["hasat"], "no": ticket_no, "t": an},
            ).scalar_one()
        )


def _makbuzlar(motor, dunya, fisler: list[int], *, sid: int | None = None) -> None:
    """Verilen SIRAYLA kesilmiş makbuz yazar (sonraki = daha büyük `id`)."""
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        for fis in fisler:
            b.execute(
                text(
                    "INSERT INTO producer_receipts(company_id,supplier_id,ticket_id,"
                    "receipt_no,issued_at,gross_amount,withholding_total,"
                    "social_security_total,net_payable,status,created_at,updated_at)"
                    " VALUES(:c,:s,:f,:no,:t,100,2,1,97,'issued',:t,:t)"
                ),
                {
                    "c": dunya["firma"],
                    "s": sid if sid is not None else dunya["ciftci"],
                    "f": fis,
                    "no": f"MM-{KOSU}-{next(_NO):06d}",
                    "t": an,
                },
            )


@pytest.fixture()
def sayfalar(monkeypatch) -> list[int]:
    """`makbuz_listesi`nin döndürdüğü satır sayıları (sayfa başına); TEK sarmal."""
    from app import mustahsil_okuma

    gercek = mustahsil_okuma.makbuz_listesi
    olcum: list[int] = []

    def sarmal(*a, **k):
        satirlar = gercek(*a, **k)
        olcum.append(len(satirlar))
        return satirlar

    monkeypatch.setattr(mustahsil_okuma, "makbuz_listesi", sarmal)
    return olcum


def _kantar(motor, dunya) -> list[str]:
    from app.whatsapp.ciftci_yurutucu import ciftci_kantar
    from app.whatsapp.taraf import TarafKimlik

    kimlik = TarafKimlik(
        company_id=dunya["firma"], party_type="SUPPLIER", party_id=dunya["ciftci"]
    )
    with sessionmaker(bind=motor)() as db:
        veri = ciftci_kantar(db, kimlik, {"liste": True})
    return [f["ticket_no"] for f in veri["fisler"]]


def test_BES_FARKLI_FIS_EN_YENI_20_MAKBUZ_UC_FIS_KAPSASA_BILE_PG(
    motor, dunya, sayfalar
):
    """Brief'in senaryosu gerçek PG'de: liste F6, F5, F4, F3, F2."""
    f = {ad: _fis(motor, dunya, ad) for ad in ("F1", "F2", "F3", "F4", "F5", "F6")}
    _makbuzlar(motor, dunya, [f["F1"], f["F2"], f["F3"]])
    # Başka çiftçinin makbuzu araya girer: `supplier_id` süzgeci PG'de de tutar.
    _makbuzlar(motor, dunya, [f["F1"]] * 5, sid=dunya["baskasi"])
    _makbuzlar(motor, dunya, [f[("F4", "F5", "F6")[k % 3]] for k in range(21)])

    assert _kantar(motor, dunya) == [
        "F6", "F5", "F4", "F3", "F2",
    ]
    assert sayfalar == [20, 4], sayfalar


def test_TARAMA_TAVANI_KESIN_PG(motor, dunya, sayfalar):
    """Tavan gerçek PG'de: 500. satırdaki eski fiş bulunur, 501.'deki bulunmaz."""
    eski = _fis(motor, dunya, "ESKI")
    sicak = _fis(motor, dunya, "SICAK")
    _makbuzlar(motor, dunya, [eski])
    _makbuzlar(motor, dunya, [sicak] * (TAVAN - 1))

    assert _kantar(motor, dunya) == ["SICAK", "ESKI"]
    assert sum(sayfalar) == TAVAN, sayfalar

    _makbuzlar(motor, dunya, [sicak])
    sayfalar.clear()
    assert _kantar(motor, dunya) == ["SICAK"]
    assert sum(sayfalar) == TAVAN, sayfalar
    assert max(sayfalar) <= 20, sayfalar
