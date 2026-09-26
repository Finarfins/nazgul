"""H92: "KANTAR LISTE" beş FARKLI fişe ulaşır; tarama bir TAVANLA sınırlı.

GÖÇ YOK, ROTA YOK. Konu `app/whatsapp/ciftci_yurutucu.py`nin
`ciftci_kantar`ı. Okuma `app/mustahsil_okuma.makbuz_listesi` (router ile
ORTAK) üzerinden kalır; YALNIZ kesilmiş (`issued`) makbuz kuralı ve fiş
netinin `farm._turetilmis_net`ten gelmesi DEĞİŞMEZ.
PostgreSQL ikizi: `test_h92_kantar_liste_postgresql.py`.

--- ÖLÇÜM (brief'in senaryosu) ---------------------------------------------

Altı farklı fiş (F1..F6). En eski üç makbuz F1, F2, F3'e birer tane; en
yeni YİRMİ BİR kesilmiş makbuz F4/F5/F6 arasında döner. En yeni 20 makbuz
yalnız ÜÇ fişi kapsar. Beklenen liste F6, F5, F4, F3, F2 (makbuz sırası).

Develop'un (78b9ad0) kodu bu senaryoda ZATEN beş fiş döndürüyordu:
`ciftci_kantar` F10-1c'den beri makbuz sayfalarını (`_FIS_SAYFASI`) offset
ile geziyor ve fişsiz makbuzlar SQL'de (`yalniz_fisli`) eleniyor. Kapı
yine de burada durur: sayfalamayı tek sayfaya indiren bir mutasyon onu
kırmızıya çevirir.

GERÇEK kusur tavansızlıktı: döngü "beş fiş ya da satırlar bitene kadar"
dönüyordu. Tek bir fişe binlerce makbuz bağlayan bir geçmiş, WhatsApp
işleyicisini çiftçinin makbuz tablosunun TAMAMINI gezmeye zorlardı.
Tavan `_TARAMA_TAVANI = 500` satırdır ve KESİNDİR: 499 sıcak makbuzun
arkasındaki eski fiş (500. satır) bulunur, 500'ün arkasındaki (501. satır)
bulunmaz.

--- MUTASYON TABLOSU --------------------------------------------------------

  * Sayfalamayı tek sayfaya indirmek     -> BEŞ FARKLI FİŞ kapısı KIRMIZI
  * Tavanı kaldırmak                     -> TAVAN kapısı KIRMIZI (501 satır)
  * Tavanı bir sayfa kaydırmak (520)     -> TAVAN kapısının 501. satır dalı KIRMIZI
  * `status='issued'` süzgecini düşürmek -> TASLAK/İPTAL kapısı KIRMIZI
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

NUMARA = "905321119292"

# Ortam UYGULAMA İÇE AKTARILMADAN ÖNCE kurulur (`test_f10_1c` başlığı).
_CALISMA = Path(tempfile.mkdtemp(prefix="h92-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "h92.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"

sys.path.insert(0, str(BACKEND))

#: Brief'in önerdiği tavan; sabit ÖLÇÜLÜR (kapı 4), kopyalanmaz.
TAVAN = 500


@pytest.fixture(scope="module")
def uygulama():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))
_WAMID = iter(range(1, 1_000_000))
_MAKBUZ_NO = iter(range(1, 1_000_000))


def _temizle(db) -> None:
    from sqlalchemy import text

    for tablo in (
        "whatsapp_party_links",
        "whatsapp_context",
        "whatsapp_message_attempts",
        "whatsapp_inbound",
        "notification_consent_events",
        "notification_consents",
        "producer_receipt_items",
        "producer_receipts",
        "field_harvest_ticket_deductions",
        "field_harvest_tickets",
    ):
        db.execute(text(f"DELETE FROM {tablo}"))
    db.commit()


@pytest.fixture()
def dunya(uygulama):
    """BİR firma, BİR çiftçi (tedarikçi), BİR hasat (fişin bileşik FK hedefi)."""
    from sqlalchemy import text

    from app.db import SessionLocal

    n = next(_SAYAC)
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        _temizle(db)

        def ekle(sql: str, **p) -> int:
            return int(db.execute(text(sql + " RETURNING id"), p).scalar_one())

        firma = ekle(
            "INSERT INTO companies(name,is_active,created_at) VALUES(:a,1,:t)",
            a=f"H92 Alim Merkezi {n}",
            t=an,
        )
        ciftci = ekle(
            "INSERT INTO suppliers(name,is_active,company_id,opening_balance,"
            "risk_limit,payment_term_days) VALUES(:a,1,:c,0,0,0)",
            a=f"H92 Ciftci {n}",
            c=firma,
        )
        ciftlik = ekle(
            "INSERT INTO farms(company_id,code,name,status,created_at,updated_at)"
            " VALUES(:c,:k,'H92 Ciftlik','ACTIVE',:s,:s)",
            c=firma,
            k=f"K{n}",
            s=an,
        )
        parsel = ekle(
            "INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,"
            "status,created_at,updated_at)"
            " VALUES(:c,:f,:k,'H92 Parsel',10,'ACTIVE',:s,:s)",
            c=firma,
            f=ciftlik,
            k=f"P{n}",
            s=an,
        )
        sezon = ekle(
            "INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,"
            "status,created_at,updated_at)"
            " VALUES(:c,:p,2026,'Bugday','ACTIVE',:s,:s)",
            c=firma,
            p=parsel,
            s=an,
        )
        hasat = ekle(
            "INSERT INTO field_harvests(company_id,season_id,harvested_on,"
            "quantity,unit,status,created_at,updated_at)"
            " VALUES(:c,:sz,'2026-09-10',1000,'KG','RECORDED',:s,:s)",
            c=firma,
            sz=sezon,
            s=an,
        )
        db.commit()

    yield {"firma": firma, "ciftci": ciftci, "hasat": hasat}
    with SessionLocal() as db:
        _temizle(db)


@pytest.fixture()
def oturum(dunya):
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


def _fis(db, dunya, ticket_no: str) -> int:
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    fis = int(
        db.execute(
            text(
                "INSERT INTO field_harvest_tickets(company_id,harvest_id,ticket_no,"
                "weighed_at,gross_entered_quantity,entered_unit,entered_factor,"
                "base_quantity,created_at,updated_at)"
                " VALUES(:c,:h,:no,NULL,1000,'KG',1,1000,:t,:t) RETURNING id"
            ),
            {"c": dunya["firma"], "h": dunya["hasat"], "no": ticket_no, "t": an},
        ).scalar_one()
    )
    db.commit()
    return fis


def _makbuzlar(db, dunya, fisler: list[int | None], durum: str = "issued") -> None:
    """Verilen SIRAYLA makbuz yazar (sonraki = daha büyük `id` = daha yeni).

    Makbuzlar DOĞRUDAN yazılır: uç akışı `test_f10_1c`de gerçek HTTP ile
    ölçülüyor; burada ölçülen şey okuma yolunun sayfalaması ve tavanıdır.
    """
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    # Numara DURUMLA hareket eder (`ck_producer_receipts_no_follows_status`):
    # kesilmiş ve iptal edilmiş makbuzda VAR, taslakta YOK.
    numarali = durum in ("issued", "cancelled")
    db.execute(
        text(
            "INSERT INTO producer_receipts(company_id,supplier_id,ticket_id,"
            "receipt_no,issued_at,gross_amount,withholding_total,"
            "social_security_total,net_payable,status,created_at,updated_at)"
            " VALUES(:c,:s,:f,:no,:k,100,2,1,97,:d,:t,:t)"
        ),
        [
            {
                "c": dunya["firma"],
                "s": dunya["ciftci"],
                "f": fis,
                "no": f"MM-H92-{next(_MAKBUZ_NO):06d}" if numarali else None,
                "k": an if numarali else None,
                "d": durum,
                "t": an,
            }
            for fis in fisler
        ],
    )
    db.commit()


def _kantar(db, dunya, *, liste: bool = True) -> list[str]:
    from app.whatsapp.ciftci_yurutucu import ciftci_kantar
    from app.whatsapp.taraf import TarafKimlik

    kimlik = TarafKimlik(
        company_id=dunya["firma"], party_type="SUPPLIER", party_id=dunya["ciftci"]
    )
    veri = ciftci_kantar(db, kimlik, {"liste": liste})
    return [f["ticket_no"] for f in veri["fisler"]]


def _casus(monkeypatch) -> list[int]:
    """`makbuz_listesi`nin döndürdüğü satır sayılarını (sayfa başına) toplar."""
    from app import mustahsil_okuma

    gercek = mustahsil_okuma.makbuz_listesi
    sayfalar: list[int] = []

    def sarmal(*a, **k):
        satirlar = gercek(*a, **k)
        sayfalar.append(len(satirlar))
        return satirlar

    monkeypatch.setattr(mustahsil_okuma, "makbuz_listesi", sarmal)
    return sayfalar


def _brief_senaryosu(db, dunya) -> dict[str, int]:
    """Altı fiş; en yeni 20 kesilmiş makbuz yalnız F4/F5/F6'yı kapsar."""
    f = {ad: _fis(db, dunya, ad) for ad in ("F1", "F2", "F3", "F4", "F5", "F6")}
    _makbuzlar(db, dunya, [f["F1"], f["F2"], f["F3"]])
    # 21 makbuz: F4, F5, F6, F4, ... → en yenisi (k=20) F6'ya düşer.
    _makbuzlar(db, dunya, [f[("F4", "F5", "F6")[k % 3]] for k in range(21)])
    return f


# ---------------------------------------------------------------- testler ---


def test_BES_FARKLI_FISE_ULASIR_EN_YENI_20_MAKBUZ_UC_FIS_KAPSASA_BILE(
    oturum, dunya, monkeypatch
):
    """Kapı 1 (brief'in ölçümü): liste F6, F5, F4, F3, F2 — beş FARKLI fiş."""
    _brief_senaryosu(oturum, dunya)

    from app.mustahsil_okuma import makbuz_listesi

    en_yeni_20 = makbuz_listesi(
        oturum,
        dunya["firma"],
        supplier_id=dunya["ciftci"],
        status="issued",
        yalniz_fisli=True,
        limit=20,
    )
    assert len({m["ticket_id"] for m in en_yeni_20}) == 3, "senaryo ÖLÇMÜYOR"

    sayfalar = _casus(monkeypatch)
    assert _kantar(oturum, dunya) == ["F6", "F5", "F4", "F3", "F2"]
    assert sum(sayfalar) == 24, sayfalar
    # Tekil görünüm tek fiş: en son KESİLEN makbuzun fişi.
    assert _kantar(oturum, dunya, liste=False) == ["F6"]


def test_TASLAK_IPTAL_ve_FISSIZ_MAKBUZ_SAYILMAZ_ve_TARAMAYI_TUKETMEZ(
    oturum, dunya
):
    """Kapı 3: kesilmemiş makbuzun fişi GÖRÜNMEZ; fişsiz makbuz sayfa YEMEZ."""
    f = _brief_senaryosu(oturum, dunya)
    gizli = {ad: _fis(oturum, dunya, ad) for ad in ("TASLAK", "IPTAL")}
    _makbuzlar(oturum, dunya, [gizli["TASLAK"]] * 3, durum="draft")
    _makbuzlar(oturum, dunya, [gizli["IPTAL"]] * 3, durum="cancelled")
    # TAVAN kadar fişsiz kesilmiş makbuz: SQL'de elenir, tavandan düşmez.
    _makbuzlar(oturum, dunya, [None] * TAVAN)
    assert f["F1"]  # altı fiş gerçekten yazıldı
    assert _kantar(oturum, dunya) == ["F6", "F5", "F4", "F3", "F2"]


def test_TARAMA_TAVANI_KESIN_499_BULUR_500_BULMAZ(oturum, dunya, monkeypatch):
    """Kapı 2: tek fişe bağlı sıcak geçmiş; tavan satır sayısında KESİN.

    ESKİ fiş en eskide. Önünde 499 (TAVAN-1) sıcak makbuz varken eski fiş
    TAVANIN İÇİNDEDİR (500. satır) ve bulunur; 500. sıcak makbuzla DIŞINA
    düşer (501. satır). Hiçbir durumda okunan makbuz satırı TAVANI aşmaz.
    """
    eski = _fis(oturum, dunya, "ESKI")
    sicak = _fis(oturum, dunya, "SICAK")
    _makbuzlar(oturum, dunya, [eski])
    _makbuzlar(oturum, dunya, [sicak] * (TAVAN - 2))

    sayfalar = _casus(monkeypatch)
    assert _kantar(oturum, dunya) == ["SICAK", "ESKI"]
    assert sum(sayfalar) == TAVAN - 1, sayfalar

    _makbuzlar(oturum, dunya, [sicak])
    sayfalar.clear()
    assert _kantar(oturum, dunya) == ["SICAK", "ESKI"]
    assert sum(sayfalar) == TAVAN, sayfalar

    # Şimdi eski fişin ÖNÜNDE tam TAVAN sıcak makbuz var: 501. satır.
    _makbuzlar(oturum, dunya, [sicak])
    sayfalar.clear()
    assert _kantar(oturum, dunya) == ["SICAK"]
    assert sum(sayfalar) == TAVAN, sayfalar


def test_TAVAN_SABITI_BRIEFTEKI_DEGER() -> None:
    """Kapı 4: tavan modülde AD taşır; değeri raporlanan değerdir."""
    from app.whatsapp import ciftci_yurutucu

    assert ciftci_yurutucu._TARAMA_TAVANI == TAVAN
    assert ciftci_yurutucu._TARAMA_TAVANI % ciftci_yurutucu._FIS_SAYFASI == 0
