"""PostgreSQL ikizi: F10-1c çiftçi kantar/makbuz cevaplarının GERÇEĞİ.

GÖÇ YOK. SQLite ikizi `tests/test_f10_1c_kantar_makbuz.py` akışın
DAVRANIŞINI ölçüyor (kapsam, kiracı yalıtımı, taraf tipi, rıza, önek, hız
sınırı, SQL kopyası yok); bu dosya yalnız GELİŞTİRME DİYALEKTİNDE
GÖRÜNMEYEN şeyleri ölçer.

--- BU İKİZ NEDEN VAR — ÜÇ GEREKÇE, ÜÇÜ DE YALNIZ BURADA GÖRÜNÜR ---------

1. **`_fis_neti`NİN `NUMERIC` ARİTMETİĞİ (keşif §6.3, ZORUNLU).** Fişin
   brütü `NUMERIC(18,4)`tür. SQLite o sütunu kayan noktaya çözer ve 15-17
   anlamlı basamaktan sonrasını KAYBEDER; PostgreSQL `Decimal` döndürür.
   Tohum, İKİ yolun gram düzeyinde AYRIŞTIĞI bir brüt taşır (18 anlamlı
   basamak) — ayrışma testin İÇİNDE ölçülür, yani tohum bir gün
   "zararsız" bir sayıya çevrilirse kapı bunu söyler. Sonra çiftçinin
   dağıtıcıdan aldığı cevaptaki net, router'ın KENDİ `_fis_neti`siyle
   BİREBİR karşılaştırılır.

2. **`TIMESTAMPTZ` → İSTANBUL GÜNÜ.** PG `issued_at`i `datetime` olarak
   döndürür (SQLite metin); cevabın günü İstanbul'dadır. 22:30 UTC'de
   kesilen makbuz ERTESİ günün tarihini taşımalıdır.

3. **`LOWER(receipt_no)=LOWER(:rno)` ve rıza/dağıtım akışı gerçek PG'de.**
   Numara araması harf büyüklüğüne duyarsız; başkasının numarası ve taslak
   "kayıt yok" ile BAYT BAYT aynı cevabı alır.

--- KAPSAM DIŞI, BİLEREK -------------------------------------------------

`service.bekleyenleri_isle` KOŞTURULMUYOR: paylaşık bir şemada kuyruğun
TAMAMINI işler. Her adım yalnız KENDİ satırını kiralar (`_claim`) ve
işler (`_mesaj_isle`) — F10-1b ikizinin kuralı. Makbuzlar uçtan değil
DOĞRUDAN yazılır: uç akışı SQLite ikizinde gerçek HTTP ile ölçülüyor ve
burada ölçülen şey okuma yolunun diyalekt davranışıdır.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
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
#: YALNIZ RAKAM, ON İKİ HANE — F10-1b ikizinin gerekçesi (`normalize_phone`
#: rakam dışını atar ve anahtar eşleşmez).
_SAYI = f"{uuid4().int % 10**8:08d}"
NUMARA = "9055" + _SAYI

#: 18 ANLAMLI BASAMAK: `NUMERIC(18,4)`ün tamamı. `float` bunu taşıyamaz.
BRUT = Decimal("98765432109876.5432")
ORANLAR = (Decimal("2.50"), Decimal("0.35"))


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F10-1c ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — NUMARAYLA ve FİRMA ÖNEKİYLE, tabloyu süpürmeden."""
    onek = KOSU + "%"
    firma_alt = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        b.execute(
            text("DELETE FROM whatsapp_message_attempts WHERE phone=:p"), {"p": NUMARA}
        )
        b.execute(
            text("DELETE FROM whatsapp_inbound WHERE sender_phone=:p"), {"p": NUMARA}
        )
        for tablo in (
            "producer_receipt_items",
            "producer_receipts",
            "field_harvest_ticket_deductions",
            "field_harvest_tickets",
            "field_harvests",
            "crop_seasons",
            "farm_parcels",
            "farms",
            "whatsapp_party_links",
            "notification_consent_events",
            "notification_consents",
            "suppliers",
        ):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firma_alt}"),
                {"o": onek},
            )
        # `activity_logs` YALNIZ-EKLEMEDIR (göç `20260727_0030`): rıza olayı
        # denetim satırı yazar, o firma SİLİNEMEZ ve pasife alınır (F10-1b
        # ikizinin kuralı).
        b.execute(
            text(
                "UPDATE companies SET is_active = FALSE WHERE name LIKE :o"
                " AND id IN (SELECT company_id FROM activity_logs)"
            ),
            {"o": onek},
        )
        b.execute(
            text(
                "DELETE FROM companies WHERE name LIKE :o"
                " AND id NOT IN (SELECT company_id FROM activity_logs)"
            ),
            {"o": onek},
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
            " VALUES(:c,'K1','Ikiz Ciftlik','ACTIVE',:s,:s)",
            c=firma,
            s=an,
        )
        parsel = ekle(
            "INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,"
            "status,created_at,updated_at)"
            " VALUES(:c,:f,'KP','Ikiz Parsel',10,'ACTIVE',:s,:s)",
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


def _fis(motor, dunya, brut: Decimal, oranlar=(), *, ticket_no=None, tartildi=None):
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        fis = int(
            b.execute(
                text(
                    "INSERT INTO field_harvest_tickets(company_id,harvest_id,"
                    "ticket_no,buyer_name,weighed_at,gross_entered_quantity,"
                    "entered_unit,entered_factor,base_quantity,created_at,updated_at)"
                    " VALUES(:c,:h,:no,'ALICI-GIZLI',:w,:b,'KG',1,:b,:t,:t)"
                    " RETURNING id"
                ),
                {
                    "c": dunya["firma"],
                    "h": dunya["hasat"],
                    "no": ticket_no,
                    "w": tartildi,
                    "b": brut,
                    "t": an,
                },
            ).scalar_one()
        )
        for sira, oran in enumerate(oranlar):
            b.execute(
                text(
                    "INSERT INTO field_harvest_ticket_deductions(company_id,"
                    "ticket_id,label,rate_percent,created_at,updated_at)"
                    " VALUES(:c,:f,:l,:o,:t,:t)"
                ),
                {"c": dunya["firma"], "f": fis, "l": f"k{sira}", "o": oran, "t": an},
            )
    return fis


def _makbuz(
    motor,
    dunya,
    sid: int,
    *,
    fis: int | None,
    no: str | None,
    durum: str,
    kesildi: str | None,
    brut: str = "306481.50",
    stopaj: str = "6129.63",
    sgk: str = "61.30",
    net: str = "300290.57",
    fiyat: str = "12.50",
) -> int:
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        makbuz = int(
            b.execute(
                text(
                    "INSERT INTO producer_receipts(company_id,supplier_id,ticket_id,"
                    "receipt_no,issued_at,gross_amount,withholding_total,"
                    "social_security_total,net_payable,status,note,created_at,"
                    "updated_at) VALUES(:c,:s,:f,:no,:k,:g,:w,:ss,:n,:d,"
                    "'MAKBUZ-GIZLI-NOT',:t,:t) RETURNING id"
                ),
                {
                    "c": dunya["firma"],
                    "s": sid,
                    "f": fis,
                    "no": no,
                    "k": kesildi,
                    "g": brut,
                    "w": stopaj,
                    "ss": sgk,
                    "n": net,
                    "d": durum,
                    "t": an,
                },
            ).scalar_one()
        )
        b.execute(
            text(
                "INSERT INTO producer_receipt_items(company_id,receipt_id,"
                "entered_quantity,entered_unit,entered_factor,base_quantity,"
                "unit_price,line_gross,withholding_rate,withholding_amount,"
                "social_security_rate,social_security_amount,line_net,created_at,"
                "updated_at) VALUES(:c,:r,1,'KG',1,1,:p,:g,2,:w,1,:ss,:n,:t,:t)"
            ),
            {
                "c": dunya["firma"],
                "r": makbuz,
                "p": fiyat,
                "g": brut,
                "w": stopaj,
                "ss": sgk,
                "n": net,
                "t": an,
            },
        )
    return makbuz


def _konusucu(motor):
    """Yalnız KENDİ satırını kiralayıp işleyen dağıtıcı adımı (F10-1b ikizi)."""
    from app.config import settings
    from app.whatsapp import service

    class _Sahte:
        def __init__(self) -> None:
            self.gonderilenler: list[str] = []

        def metin_gonder(self, alici: str, metin: str) -> None:
            self.gonderilenler.append(metin)

    Oturum = sessionmaker(bind=motor)
    saglayici = _Sahte()

    def konus(metin: str) -> str:
        with Oturum() as db:
            satir = db.execute(
                text(
                    "INSERT INTO whatsapp_inbound(wamid,sender_phone,"
                    "phone_number_id,text,status,attempt_count,received_at)"
                    " VALUES(:w,:p,:n,:m,'RECEIVED',0,:t) RETURNING id"
                ),
                {
                    "w": f"{KOSU}-{uuid4().hex}",
                    "p": NUMARA,
                    "n": settings.whatsapp_phone_number_id or "",
                    "m": metin,
                    "t": datetime.now(timezone.utc),
                },
            ).scalar_one()
            db.commit()
            jeton = service._claim(db, int(satir))
            assert jeton is not None
            once = len(saglayici.gonderilenler)
            service._mesaj_isle(db, int(satir), jeton, saglayici)
            assert len(saglayici.gonderilenler) == once + 1, metin
            return saglayici.gonderilenler[-1]

    return konus


def _baglanti(motor, dunya) -> None:
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        b.execute(
            text(
                "INSERT INTO whatsapp_party_links(company_id,party_type,party_id,"
                "phone,is_active,created_at,updated_at)"
                " VALUES(:c,'SUPPLIER',:s,:p,TRUE,:t,:t)"
            ),
            {"c": dunya["firma"], "s": dunya["ciftci"], "p": NUMARA, "t": an},
        )


# ---------------------------------------------------------------- testler ---


def test_FIS_NETI_NUMERIC_GERCEK_PGde_CEVAP_ROUTERIN_NETIYLE_AYNI(motor, dunya):
    """Gerekçe 1: 18 basamaklı brüt; cevaptaki net = router'ın `_fis_neti`si.

    ÜÇ ADIM:
      1. PG brütü `Decimal` olarak BİREBİR geri veriyor (kayan nokta yok).
      2. Aynı brüt `float`tan geçseydi (SQLite'ın okuması) türetilen net
         GRAM düzeyinde AYRIŞIRDI — tohumun ayırt edici olduğu burada
         ÖLÇÜLÜYOR, varsayılmıyor.
      3. Çiftçinin DAĞITICIDAN aldığı cevaptaki net, router'ın
         `_fis_neti`siyle ve toplamsal kuralın elle yazılmış hâliyle AYNI.
    """
    from app.mustahsil_okuma import fis_ozeti
    from app.routers.farm import _turetilmis_net
    from app.routers.mustahsil import _fis_neti

    fis = _fis(motor, dunya, BRUT, ORANLAR, ticket_no="PG-4412")
    _makbuz(
        motor, dunya, dunya["ciftci"], fis=fis, no=f"MM-{_SAYI}",
        durum="issued", kesildi="2026-09-11T08:00:00+00:00",
    )

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        ozet = fis_ozeti(db, dunya["firma"], fis)
        router_neti = _fis_neti(db, dunya["firma"], fis)
    assert isinstance(ozet["brut"], Decimal) and ozet["brut"] == BRUT
    yuzde = Decimal("100")
    elle = (BRUT - sum((BRUT * o / yuzde for o in ORANLAR), Decimal("0"))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    assert router_neti == ozet["net"] == elle

    kesintiler = [{"rate_percent": o} for o in ORANLAR]
    float_neti = _turetilmis_net(Decimal(repr(float(BRUT))), kesintiler)
    assert float_neti != router_neti, "tohum SQLite/PG ayrımını artık ÖLÇMÜYOR"

    _baglanti(motor, dunya)
    konus = _konusucu(motor)
    konus("KANTAR")  # KVKK sorusu
    assert "Onayınız alındı" in konus("EVET")
    cevap = konus("KANTAR")
    net_satiri = next(s for s in cevap.splitlines() if s.startswith("NET: "))
    okunan = Decimal(net_satiri[5:].split(" ")[0].replace(".", "").replace(",", "."))
    assert okunan == router_neti, cevap
    assert "ALICI-GIZLI" not in cevap


def test_DAGITICIDAN_MAKBUZ_ISTANBUL_GUNU_HARF_DUYARSIZ_NUMARA_ve_NOTR_CEVAP(
    motor, dunya
):
    """Gerekçe 2 + 3: `TIMESTAMPTZ` İstanbul gününe, `LOWER` gerçek PG'de, nötr bayt.

    Çiftçinin KESİLMİŞ makbuzu 22:30 UTC'de (İstanbul'da ERTESİ gün) kesildi;
    TASLAĞI ve BAŞKA çiftçinin kesilmiş makbuzu da var. Taslak ve başkasının
    numarası "kayıt yok" ile AYNI baytları alır.
    """
    from app.whatsapp.ciftci_yurutucu import MAKBUZ_YOK_MESAJI

    kendi_no = f"MM-{_SAYI}"
    baskasi_no = f"MM-9{_SAYI}"
    _makbuz(
        motor, dunya, dunya["ciftci"], fis=None, no=None, durum="draft", kesildi=None,
    )
    _makbuz(
        motor, dunya, dunya["baskasi"], fis=None, no=baskasi_no, durum="issued",
        kesildi="2026-09-10T10:00:00+00:00",
    )

    _baglanti(motor, dunya)
    konus = _konusucu(motor)
    konus("MAKBUZ")
    konus("EVET")
    hiclik = konus("MAKBUZ")
    assert hiclik == MAKBUZ_YOK_MESAJI
    assert konus(f"MAKBUZ {baskasi_no}").encode("utf-8") == hiclik.encode("utf-8")

    _makbuz(
        motor, dunya, dunya["ciftci"], fis=None, no=kendi_no, durum="issued",
        kesildi="2026-09-11T22:30:00+00:00",
    )
    cevap = konus(f"makbuz {kendi_no.lower()}")
    satirlar = cevap.splitlines()
    assert satirlar[0] == f"Müstahsil makbuzunuz {kendi_no} (12.09.2026, kesildi):"
    assert satirlar[1] == "Brüt 306.481,50 TL · Birim fiyat 12,50 TL"
    assert satirlar[2] == "Stopaj 6.129,63 TL · Bağ-Kur 61,30 TL"
    assert satirlar[3] == "NET ÖDENECEK: 300.290,57 TL"
    assert "MAKBUZ-GIZLI-NOT" not in cevap
    assert konus(f"MAKBUZ {baskasi_no}").encode("utf-8") == hiclik.encode("utf-8")
