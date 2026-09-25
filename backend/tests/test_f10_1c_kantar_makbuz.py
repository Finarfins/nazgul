"""ÇİFTÇİ KANTAR FİŞİ + MÜSTAHSİL MAKBUZU (F10-1c). GÖÇ YOK, ROTA YOK.

Konu: `app/mustahsil_okuma.py` (router ile ORTAK okuma yolu),
`app/whatsapp/ciftci_niyet.py`nin iki yeni kök kümesi,
`app/whatsapp/ciftci_yurutucu.py`nin iki yeni aracı (`ciftci_kantar`,
`ciftci_makbuz`) ve `app/routers/mustahsil.py`nin ortak modüle bağlanması.
PostgreSQL ikizi: `test_f10_1c_kantar_makbuz_postgresql.py` (`_fis_neti`nin
`NUMERIC` aritmetiği ve rıza/dağıtım akışı gerçek PG'de).

Keşif `docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §4c, §4d, §4e, K5.

--- KAPSAM: FİŞ YALNIZ KESİLMİŞ MAKBUZ ÜZERİNDEN ---------------------------

Kantar fişinin taraf sütunu YOKTUR. Çiftçi YALNIZ `producer_receipts.ticket_id`
üzerinden KENDİ (`supplier_id`) ve KESİLMİŞ (`issued`) makbuzuna bağlı
fişleri görür. Sahipsiz fiş, taslağa/iptale/başka çiftçiye bağlı fiş ve
`buyer_name`i çiftçinin adını taşıyan fiş GÖRÜNMEZ.

Bütün davranış kapıları GERÇEK dağıtıcıdan geçer (`service.bekleyenleri_isle`
+ sahte sağlayıcı): kiralama, rıza kapısı, firma öneki, hız sınırı ve
commit sınırı dahil. Makbuzlar GERÇEK uçlardan kesilir (`POST
/api/producer-receipts` + `/issue`), yani cevabın karşılaştırıldığı sayılar
ucun KENDİ sayılarıdır.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

  * `ciftci_kantar`/`ciftci_makbuz`dan `status='issued'` süzgecini düşürmek
                                    -> BAĞLI OLMAYAN FİŞ ve TASLAK GİZLİ
                                       kapıları KIRMIZI
  * `ciftci_kantar`/`ciftci_makbuz`dan `party_type` denetimini düşürmek
                                    -> MÜŞTERİ TARAFI kapısı KIRMIZI
  * `makbuz_listesi`nde `supplier_id` süzgecini düşürmek (çiftçi dalı)
                                    -> BAĞLI OLMAYAN FİŞ ve BAŞKASININ
                                       NUMARASI kapıları KIRMIZI
  * Kantar cevabında neti `_sayi_tr` yerine `para_tr` ile (iki basamağa
    yuvarlayarak) yazmak            -> UÇLA AYNI kapısı KIRMIZI (gram)
  * Çiftçi araçlarında SQL yazmak / neti yeniden hesaplamak
                                    -> SQL KOPYASI YOK kapısı KIRMIZI
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
NIYET = APP / "whatsapp" / "ciftci_niyet.py"
YURUTUCU = APP / "whatsapp" / "ciftci_yurutucu.py"
ORTAK = APP / "mustahsil_okuma.py"
ROUTER = APP / "routers" / "mustahsil.py"

BAGLANTI_TABLO = "whatsapp_party_links"
SAYAC_TABLO = "whatsapp_message_attempts"

NUMARA = "905321114455"

# Ortam UYGULAMA İÇE AKTARILMADAN ÖNCE kurulur (`test_f10_1b` başlığı).
_CALISMA = Path(tempfile.mkdtemp(prefix="f10c-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "f10c.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"

sys.path.insert(0, str(BACKEND))

#: Makbuz tablolarının SQL'deki adları — "app/whatsapp altında SQL yok"
#: kapısının aradığı dizgiler.
_MAKBUZ_FIS_TABLOLARI = (
    "producer_receipts",
    "producer_receipt_items",
    "field_harvest_tickets",
    "field_harvest_ticket_deductions",
)


# --------------------------------------------------------------- statik ---


def _dize_sabitleri(yol: Path) -> list[str]:
    """Belge dizgileri HARİÇ bütün dize sabitleri (F10-1b kapısının yardımcısı)."""
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    belge = set()
    for dugum in ast.walk(agac):
        if isinstance(
            dugum, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            ilk = dugum.body[0] if dugum.body else None
            if isinstance(ilk, ast.Expr) and isinstance(ilk.value, ast.Constant):
                belge.add(id(ilk.value))
    return [
        d.value
        for d in ast.walk(agac)
        if isinstance(d, ast.Constant) and isinstance(d.value, str) and id(d) not in belge
    ]


def _cagrilan_adlar(yol: Path) -> set[str]:
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    adlar: set[str] = set()
    for d in ast.walk(agac):
        if isinstance(d, ast.Call):
            if isinstance(d.func, ast.Name):
                adlar.add(d.func.id)
            elif isinstance(d.func, ast.Attribute):
                adlar.add(d.func.attr)
    return adlar


def test_KANTAR_MAKBUZ_SQL_KOPYASI_YOK_FIS_NETI_ORTAK() -> None:
    """Makbuz/fiş SQL'i ve fiş neti formülü YALNIZ ortak modüldedir (kapı 10).

    ÜÇ İDDİA:
      1. `app/whatsapp/` altında makbuz/fiş tablolarının adını taşıyan HİÇBİR
         dize sabiti yok (belge dizgileri hariç — onlar SQL değil). F10-1b'nin
         `supplier_advances` kapısının AYNISI, bu dilimin tablolarıyla.
      2. İki yüzey ortak fonksiyonları ÇAĞIRIYOR: router `fis_neti`,
         `makbuz_satiri`, `makbuz_kalemleri`, `makbuz_listesi`; çiftçi
         aracı `fis_ozeti`, `makbuz_listesi`, `makbuz_kalemleri`.
      3. `_turetilmis_net` app/ altında TEK yerde TANIMLI (`routers/farm.py`)
         ve çiftçi modüllerinde BÖLME yok — neti yeniden hesaplayan bir
         kopya ya ikinci bir tanım ya da bir `/` üretirdi.
    """
    sizan = [
        (yol.name, dize)
        for yol in sorted((APP / "whatsapp").glob("*.py"))
        for dize in _dize_sabitleri(yol)
        for tablo in _MAKBUZ_FIS_TABLOLARI
        if tablo in dize
    ]
    assert sizan == []

    assert {"fis_ozeti", "makbuz_listesi", "makbuz_kalemleri"} <= _cagrilan_adlar(
        YURUTUCU
    )
    assert {
        "fis_neti",
        "makbuz_satiri",
        "makbuz_kalemleri",
        "makbuz_listesi",
    } <= _cagrilan_adlar(ROUTER)
    # Router'da makbuz/fiş SELECT'i kalmadı: okuma metinleri ORTAK modülde.
    assert not [
        d
        for d in _dize_sabitleri(ROUTER)
        if "FROM producer_receipt_items" in d or "FROM field_harvest_ticket" in d
    ]
    assert "_turetilmis_net" in _cagrilan_adlar(ORTAK)

    tanimlar = [
        yol.relative_to(APP).as_posix()
        for yol in APP.rglob("*.py")
        for d in ast.walk(ast.parse(yol.read_text(encoding="utf-8")))
        if isinstance(d, ast.FunctionDef) and d.name == "_turetilmis_net"
    ]
    assert tanimlar == ["routers/farm.py"]
    for yol in (NIYET, YURUTUCU):
        bolmeler = [
            d
            for d in ast.walk(ast.parse(yol.read_text(encoding="utf-8")))
            if isinstance(d, ast.BinOp) and isinstance(d.op, ast.Div)
        ]
        assert bolmeler == [], yol.name


def test_TERIM_CIKARIMI_ve_TAHSILAT_YENI_KODDA_DA_YOK() -> None:
    """`_terim_cikar` ve `tahsilat_coz` çiftçi modüllerinde VE ortak modülde yok.

    F10-1b kapısı iki çiftçi modülünü tarıyordu; bu dilim bir modül daha
    ekledi ve çiftçi yolu ona uğruyor. Makbuz numarası mesajdan alınıyor ama
    o bir BELGE NUMARASI biçimidir, cari adı DEĞİL (`ciftci_niyet` başlığı).
    """
    for yol in (NIYET, YURUTUCU, ORTAK):
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        adlar = {d.attr for d in ast.walk(agac) if isinstance(d, ast.Attribute)} | {
            d.id for d in ast.walk(agac) if isinstance(d, ast.Name)
        }
        assert "_terim_cikar" not in adlar, yol.name
        assert "tahsilat_coz" not in adlar, yol.name


def test_KOKLER_KESIFTEKI_GIBI_ve_YASAKLI_KOKLER_KAPSAM_MESAJINA_DUSUYOR() -> None:
    """Kökler §4c/§4d'nin listesi; `FIS`, `KILO`, `ADET`, `FATURA` kök DEĞİL."""
    from datetime import date

    from app.whatsapp.ciftci_niyet import (
        CIFTCI_KAPSAM_MESAJI,
        KANTAR_KOKLER,
        MAKBUZ_KOKLER,
        coz,
    )

    assert KANTAR_KOKLER == {"KANTAR", "TARTI", "TONAJ"}
    assert MAKBUZ_KOKLER == {"MAKBUZ", "MUSTAHSIL"}
    bugun = date(2026, 9, 25)
    for yasak in ("fiş", "fis", "kilo", "adet", "fatura"):
        assert coz(yasak, bugun=bugun).mesaj == CIFTCI_KAPSAM_MESAJI, yasak

    assert coz("kantar", bugun=bugun).arac == "ciftci_kantar"
    assert coz("tartı", bugun=bugun).arac == "ciftci_kantar"
    assert coz("tonajım", bugun=bugun).arac == "ciftci_kantar"
    assert coz("KANTAR LİSTE", bugun=bugun).argumanlar == {"liste": True}
    assert coz("müstahsil", bugun=bugun).arac == "ciftci_makbuz"
    assert coz("makbuz mm-000318", bugun=bugun).argumanlar == {
        "liste": False,
        "receipt_no": "MM-000318",
    }
    assert coz("MAKBUZ MM-2026-000318", bugun=bugun).argumanlar[
        "receipt_no"
    ] == "MM-2026-000318"
    # Numara biçiminde OLMAYAN kelime numara SAYILMAZ.
    assert coz("makbuz Ahmet", bugun=bugun).argumanlar["receipt_no"] is None
    # Sıra: avans > kantar > makbuz > ekstre.
    assert coz("avans makbuzu", bugun=bugun).arac == "ciftci_avans"
    assert coz("kantar makbuzu", bugun=bugun).arac == "ciftci_kantar"
    assert coz("makbuz borcum", bugun=bugun).arac == "ciftci_makbuz"


def test_KVKK_METNI_KANTAR_VE_MAKBUZU_ADIYLA_SAYIYOR() -> None:
    """Rıza metni gönderilecek veriyi SAYAR; yeni iki tür de sayılmalı.

    Çiftçi "bakiye, ekstre ve avans" için onay verip kantar/makbuz rakamı
    alsaydı, onay metni gönderilen veriyi EKSİK anlatmış olurdu.
    """
    from app.whatsapp.ciftci_niyet import RIZA_ALINDI_MESAJI, kvkk_metni

    for metin in (kvkk_metni("Deneme AS"), RIZA_ALINDI_MESAJI):
        assert "kantar fişi" in metin
        assert "müstahsil makbuzu" in metin


# ------------------------------------------------------------- fixtures ---


@pytest.fixture(scope="module")
def uygulama():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))
_WAMID = iter(range(1, 1_000_000))


def _temizle(db) -> None:
    from sqlalchemy import text

    for tablo in (
        "whatsapp_party_pairing_codes",
        BAGLANTI_TABLO,
        "whatsapp_pairing_codes",
        "whatsapp_links",
        "whatsapp_context",
        "whatsapp_pairing_attempts",
        SAYAC_TABLO,
        "whatsapp_inbound",
        "notification_consent_events",
        "notification_consents",
        "tax_liabilities",
        "producer_receipt_items",
        "producer_receipts",
        "field_harvest_ticket_deductions",
        "field_harvest_tickets",
    ):
        db.execute(text(f"DELETE FROM {tablo}"))
    db.commit()


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma; her birinde AYNI ADLI çiftçi + birinci firmada İKİNCİ bir çiftçi.

    Her firmada bir ürün (taban birim KG, `sale_price` 99.99 — cevapta
    ASLA görünmemesi gereken firma fiyatı) ve bir hasat (fişin bileşik
    FK hedefi).
    """
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    n = next(_SAYAC)
    parola = "F10cTest!12345"
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        _temizle(db)

        def ekle(sql: str, **p) -> int:
            return int(db.execute(text(sql + " RETURNING id"), p).scalar_one())

        def firma(ad: str) -> int:
            return ekle(
                "INSERT INTO companies(name,is_active,created_at) VALUES(:a,1,:t)",
                a=ad,
                t=an,
            )

        firma_a = firma(f"F10c Alim Merkezi {n}")
        firma_b = firma(f"F10c Komsu Merkez {n}")
        personel = ekle(
            "INSERT INTO app_users(username,email,email_verified,display_name,"
            "password_hash,role,is_active,must_change_password,created_at)"
            " VALUES(:k,:e,1,:k,:h,'admin',1,0,:t)",
            k=f"f10c-admin-{n}",
            e=f"f10c-admin-{n}@f10c.invalid",
            h=hash_password(parola),
            t=an,
        )
        for cid in (firma_a, firma_b):
            db.execute(
                text(
                    "INSERT INTO user_company_memberships"
                    "(user_id,company_id,is_default,created_at) VALUES(:u,:c,0,:t)"
                ),
                {"u": personel, "c": cid, "t": an},
            )

        def cari(tablo: str, cid: int, ad: str, kimlik: int | None = None) -> int:
            if kimlik is None:
                return ekle(
                    f"INSERT INTO {tablo}(name,is_active,company_id,"
                    "opening_balance,risk_limit,payment_term_days)"
                    " VALUES(:a,1,:c,0,0,0)",
                    a=ad,
                    c=cid,
                )
            return ekle(
                f"INSERT INTO {tablo}(id,name,is_active,company_id,"
                "opening_balance,risk_limit,payment_term_days)"
                " VALUES(:i,:a,1,:c,0,0,0)",
                i=kimlik,
                a=ad,
                c=cid,
            )

        ad = f"Ciftci Sungur {n}"
        tedarikci_a = cari("suppliers", firma_a, ad)
        tedarikci_b = cari("suppliers", firma_b, ad)
        tedarikci_a2 = cari("suppliers", firma_a, f"Baska Ciftci {n}")
        # MÜŞTERİ ve TEDARİKÇİ AYNI SAYISAL kimlikte (kapı 5): `party_type`
        # denetimi düşerse müşteri kimliği tedarikçinin makbuzlarını bulurdu.
        # AZALAN sayı: SQLite açık kimlikten SONRA `max+1`den devam eder, yani
        # artan bir seri sonraki fixture'ın otomatik kimliğiyle ÇAKIŞIRDI.
        ortak_kimlik = 800_000 - n
        musteri_a = cari("customers", firma_a, ad, ortak_kimlik)
        tedarikci_ikiz = cari("suppliers", firma_a, f"Ikiz Ciftci {n}", ortak_kimlik)

        def urun(cid: int) -> int:
            return ekle(
                "INSERT INTO products(name,purchase_price,sale_price,vat_rate,"
                "stock,unit,price_per,active,critical_stock,minimum_stock,"
                "company_id,base_unit) VALUES(:n,0,99.99,0,0,'KG',1,1,0,0,:c,'KG')",
                n=f"F10c urun {n}-{cid}",
                c=cid,
            )

        def hasat(cid: int) -> int:
            ciftlik = ekle(
                "INSERT INTO farms(company_id,code,name,status,created_at,updated_at)"
                " VALUES(:c,:k,'F10c Ciftlik','ACTIVE',:s,:s)",
                c=cid,
                k=f"K{n}",
                s=an,
            )
            parsel = ekle(
                "INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,"
                "status,created_at,updated_at)"
                " VALUES(:c,:f,:k,'F10c Parsel',10,'ACTIVE',:s,:s)",
                c=cid,
                f=ciftlik,
                k=f"P{n}",
                s=an,
            )
            sezon = ekle(
                "INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,"
                "status,created_at,updated_at)"
                " VALUES(:c,:p,2026,'Bugday','ACTIVE',:s,:s)",
                c=cid,
                p=parsel,
                s=an,
            )
            return ekle(
                "INSERT INTO field_harvests(company_id,season_id,harvested_on,"
                "quantity,unit,status,created_at,updated_at)"
                " VALUES(:c,:sz,'2026-09-10',1000,'KG','RECORDED',:s,:s)",
                c=cid,
                sz=sezon,
                s=an,
            )

        veri = {
            "firma_a": firma_a,
            "firma_b": firma_b,
            "personel": personel,
            "tedarikci_a": tedarikci_a,
            "tedarikci_b": tedarikci_b,
            "tedarikci_a2": tedarikci_a2,
            "tedarikci_ikiz": tedarikci_ikiz,
            "musteri_a": musteri_a,
            "urun_a": urun(firma_a),
            "urun_b": urun(firma_b),
            "hasat_a": hasat(firma_a),
            "hasat_b": hasat(firma_b),
            "kad": f"f10c-admin-{n}",
            "parola": parola,
            "ad": ad,
            "firma_a_adi": f"F10c Alim Merkezi {n}",
        }
        db.commit()

    yield veri
    with SessionLocal() as db:
        _temizle(db)


@pytest.fixture()
def oturum(dunya):
    """`dunya`YA BAĞLI — fixture sırası teardown kilidini önlüyor (WA2 notu)."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


@pytest.fixture()
def personel_istemcisi(uygulama, dunya):
    """Gerçek uçları çağıran personel oturumu: `(firma_id) -> başlıklar`."""
    r = uygulama.post(
        "/api/auth/login", json={"username": dunya["kad"], "password": dunya["parola"]}
    )
    assert r.status_code == 200, r.text
    jeton = r.json()["access_token"]

    def basliklar(cid: int) -> dict[str, str]:
        return {"Authorization": "Bearer " + jeton, "X-Company-ID": str(cid)}

    return basliklar


class _SahteSaglayici:
    def __init__(self) -> None:
        self.gonderilenler: list[tuple[str, str]] = []

    def metin_gonder(self, alici: str, metin: str) -> None:
        self.gonderilenler.append((alici, metin))


def _baglanti_ac(db, cid: int, party_type: str, party_id: int, telefon: str = NUMARA):
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    kimlik = db.execute(
        text(
            f"INSERT INTO {BAGLANTI_TABLO}(company_id,party_type,party_id,phone,"
            "is_active,created_at,updated_at) VALUES(:c,:pt,:pi,:p,1,:t,:t)"
            " RETURNING id"
        ),
        {"c": cid, "pt": party_type, "pi": party_id, "p": telefon, "t": an},
    ).scalar_one()
    db.commit()
    return int(kimlik)


def _riza_ver(db, cid: int, party_type: str, party_id: int, telefon: str = NUMARA):
    from app.notifications import consents

    consents.set_consent(
        db,
        company_id=cid,
        party_type=party_type,
        party_id=party_id,
        channel="WHATSAPP",
        granted=True,
        source="PHONE",
        source_ref=None,
        recipient=telefon,
        user_id=None,
    )
    db.commit()


def _ciftci(db, cid: int, sid: int, party_type: str = "SUPPLIER") -> None:
    """Bağlantı + rıza: ERP verisi almaya HAZIR çiftçi."""
    _baglanti_ac(db, cid, party_type, sid)
    _riza_ver(db, cid, party_type, sid)


def _konus(db, metin: str, *, telefon: str = NUMARA):
    """GERÇEK dağıtıcıyı koşturur ve (gönderilenler, satır) döner (`test_f10_1b`)."""
    from sqlalchemy import text

    from app.config import settings
    from app.whatsapp import service

    satir_id = int(
        db.execute(
            text(
                "INSERT INTO whatsapp_inbound(wamid,sender_phone,phone_number_id,"
                "text,media_id,status,attempt_count,received_at)"
                " VALUES(:w,:p,:n,:m,NULL,'RECEIVED',0,:t) RETURNING id"
            ),
            {
                "w": f"f10c-{next(_WAMID)}",
                "p": telefon,
                "n": settings.whatsapp_phone_number_id or "",
                "m": metin,
                "t": datetime.now(timezone.utc),
            },
        ).scalar_one()
    )
    db.commit()
    saglayici = _SahteSaglayici()
    service.bekleyenleri_isle(db, saglayici=saglayici)
    satir = (
        db.execute(
            text("SELECT status,last_error FROM whatsapp_inbound WHERE id=:i"),
            {"i": satir_id},
        )
        .mappings()
        .one()
    )
    return saglayici.gonderilenler, satir


def _cevap(db, metin: str, **kw) -> str:
    gonderilen, satir = _konus(db, metin, **kw)
    assert satir["status"] == "ANSWERED", (metin, satir)
    return gonderilen[-1][1]


def _fis_yaz(
    db,
    cid: int,
    hasat_id: int,
    brut: str,
    oranlar: tuple[str, ...] = (),
    *,
    ticket_no: str | None = None,
    tartildi: str | None = None,
    alici: str | None = None,
    notlar: str | None = None,
) -> int:
    """Fiş + kesintileri DOĞRUDAN yazar (fiş ucu `test_kantar_fisi_*`te ölçülüyor)."""
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    fis = int(
        db.execute(
            text(
                "INSERT INTO field_harvest_tickets(company_id,harvest_id,ticket_no,"
                "buyer_name,weighed_at,gross_entered_quantity,entered_unit,"
                "entered_factor,base_quantity,notes,created_at,updated_at)"
                " VALUES(:c,:h,:no,:al,:w,:b,'KG',1,:b,:nt,:t,:t) RETURNING id"
            ),
            {
                "c": cid,
                "h": hasat_id,
                "no": ticket_no,
                "al": alici,
                "w": tartildi,
                "b": brut,
                "nt": notlar,
                "t": an,
            },
        ).scalar_one()
    )
    for sira, oran in enumerate(oranlar):
        db.execute(
            text(
                "INSERT INTO field_harvest_ticket_deductions(company_id,ticket_id,"
                "label,rate_percent,created_at,updated_at)"
                " VALUES(:c,:f,:l,:o,:t,:t)"
            ),
            {"c": cid, "f": fis, "l": f"kesinti-{sira}", "o": oran, "t": an},
        )
    db.commit()
    return fis


def _makbuz(
    uygulama,
    basliklar: dict[str, str],
    sid: int,
    urun: int,
    *,
    fis: int | None = None,
    fiyat: str = "12.50",
    kes: bool = True,
    iptal: bool = False,
    note: str | None = None,
) -> dict:
    """Makbuzu GERÇEK uçlardan açar (ve isteğe bağlı keser/iptal eder)."""
    govde = {
        "supplier_id": sid,
        "items": [
            {
                "product_id": urun,
                "entered_quantity": "1000",
                "entered_unit": "KG",
                "unit_price": fiyat,
                "withholding_rate": "2",
                "social_security_rate": "1",
            }
        ],
    }
    if fis is not None:
        govde["ticket_id"] = fis
    if note is not None:
        govde["note"] = note
    r = uygulama.post("/api/producer-receipts", headers=basliklar, json=govde)
    assert r.status_code == 201, r.text
    makbuz = r.json()
    if kes:
        r = uygulama.post(
            f"/api/producer-receipts/{makbuz['id']}/issue", headers=basliklar
        )
        assert r.status_code == 200, r.text
        makbuz = r.json()
    if iptal:
        r = uygulama.post(
            f"/api/producer-receipts/{makbuz['id']}/cancel", headers=basliklar
        )
        assert r.status_code == 200, r.text
        makbuz = r.json()
    return makbuz


def _net_oku(metin: str) -> Decimal:
    """Cevaptaki ``"NET: 23.491,2295 kg"`` satırını Decimal'e çevirir."""
    esles = re.search(r"NET: ([0-9.,]+) ", metin)
    assert esles, metin
    return Decimal(esles.group(1).replace(".", "").replace(",", "."))


def _tl(deger: str) -> str:
    """Ucun ``"293640.37"`` metni → cevabın ``"293.640,37"`` biçimi."""
    from app.whatsapp.niyet import para_tr

    return para_tr(Decimal(deger))


# ------------------------------------------------------------- davranış ---


def test_KANTAR_CEVABI_UCUN_KENDI_MAKBUZU_VE_FIS_NETI_ILE_AYNI(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 1: iki makbuzlu çiftçi; cevap `GET /producer-receipts/{id}` + `_fis_neti`.

    Net GRAM altı basamağa kadar karşılaştırılır: 24180.37 × (2.5 + 0.35)%
    düşümü 23491.229455 verir, `_turetilmis_net` onu 23491.2295'e yuvarlar.
    Cevap iki basamağa yuvarlasaydı (`para_tr`) 23.491,23 derdi ve bu kapı
    KIRMIZI olurdu.
    """
    from app.routers.mustahsil import _fis_neti

    cid, sid = dunya["firma_a"], dunya["tedarikci_a"]
    h = personel_istemcisi(cid)
    eski_fis = _fis_yaz(oturum, cid, dunya["hasat_a"], "1000", ("2",), ticket_no="4400")
    yeni_fis = _fis_yaz(
        oturum,
        cid,
        dunya["hasat_a"],
        "24180.37",
        ("2.5", "0.35"),
        ticket_no="4412",
        tartildi="2026-09-11T08:00:00+00:00",
        alici="ALICI-GIZLI-AD",
        notlar="FIS-GIZLI-NOT",
    )
    eski = _makbuz(uygulama, h, sid, dunya["urun_a"], fis=eski_fis)
    yeni = _makbuz(
        uygulama, h, sid, dunya["urun_a"], fis=yeni_fis, note="MAKBUZ-GIZLI-NOT"
    )
    uc = uygulama.get(f"/api/producer-receipts/{yeni['id']}", headers=h)
    assert uc.status_code == 200, uc.text
    uc = uc.json()
    router_neti = _fis_neti(oturum, cid, yeni_fis)
    assert router_neti == Decimal("23491.2295")
    assert Decimal(uc["items"][0]["ticket_net_snapshot"]) == router_neti

    _ciftci(oturum, cid, sid)
    kantar = _cevap(oturum, "KANTAR")
    satirlar = kantar.splitlines()
    assert len(satirlar) <= 5
    assert satirlar[0] == "Son kantar fişiniz (#4412, 11.09.2026):"
    assert satirlar[1] == "Brüt 24.180,37 kg · Kesinti %2,85"
    assert _net_oku(kantar) == router_neti
    assert satirlar[2] == "NET: 23.491,2295 kg"
    assert satirlar[3] == f"Makbuz: {uc['receipt_no']} (kesildi)"
    for gizli in ("ALICI-GIZLI-AD", "FIS-GIZLI-NOT", "MAKBUZ-GIZLI-NOT"):
        assert gizli not in kantar

    makbuz = _cevap(oturum, "makbuzum")
    satirlar = makbuz.splitlines()
    assert len(satirlar) <= 5
    assert satirlar[0].startswith(f"Müstahsil makbuzunuz {uc['receipt_no']} (")
    assert satirlar[0].endswith(", kesildi):")
    # Rakamlar ucun KENDİ metinlerinden (K3: çiftçinin KENDİ fiyatı görünür).
    assert satirlar[1] == (
        f"Brüt {_tl(uc['gross_amount'])} TL · Birim fiyat "
        f"{_tl(uc['items'][0]['unit_price'])} TL"
    )
    assert satirlar[2] == (
        f"Stopaj {_tl(uc['withholding_total'])} TL · "
        f"Bağ-Kur {_tl(uc['social_security_total'])} TL"
    )
    assert satirlar[3] == f"NET ÖDENECEK: {_tl(uc['net_payable'])} TL"
    assert "MAKBUZ-GIZLI-NOT" not in makbuz
    assert "99,99" not in makbuz  # firmanın satış fiyatı ASLA

    # Numarayla ESKİ makbuz; liste en yeniden eskiye.
    assert eski["receipt_no"] in _cevap(oturum, f"MAKBUZ {eski['receipt_no']}")
    liste = _cevap(oturum, "KANTAR LISTE").splitlines()
    assert len(liste) == 2
    assert liste[0].startswith("#4412") and yeni["receipt_no"] in liste[0]
    assert liste[1].startswith("#4400") and "NET 980,00 kg" in liste[1]
    makbuz_listesi = _cevap(oturum, "MAKBUZ LISTE").splitlines()
    assert [s.split(" · ")[0] for s in makbuz_listesi] == [
        yeni["receipt_no"],
        eski["receipt_no"],
    ]


def test_BAGLI_OLMAYAN_FIS_ASLA_GORUNMUYOR_ALICI_ADI_ESLESSE_BILE(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 2: yalnız KENDİ KESİLMİŞ makbuzuna bağlı fiş görünür (K5).

    Dört tuzak fiş, dördü de çiftçinin ADINI `buyer_name`de taşıyor:
    sahipsiz, çiftçinin TASLAK makbuzuna bağlı, çiftçinin İPTAL edilmiş
    makbuzuna bağlı, aynı firmadaki BAŞKA çiftçinin kesilmiş makbuzuna
    bağlı. Hiçbiri görünmez; sonra çiftçinin kendi kesilmiş makbuzu gelir
    ve liste TAM BİR satır olur.
    """
    from app.whatsapp.ciftci_yurutucu import KANTAR_YOK_MESAJI, MAKBUZ_YOK_MESAJI

    cid, sid, ad = dunya["firma_a"], dunya["tedarikci_a"], dunya["ad"]
    h = personel_istemcisi(cid)
    hasat, urun = dunya["hasat_a"], dunya["urun_a"]

    _fis_yaz(oturum, cid, hasat, "5000", alici=ad, ticket_no="T-SAHIPSIZ")
    taslak_fis = _fis_yaz(oturum, cid, hasat, "5100", alici=ad, ticket_no="T-TASLAK")
    _makbuz(uygulama, h, sid, urun, fis=taslak_fis, kes=False)
    iptal_fis = _fis_yaz(oturum, cid, hasat, "5200", alici=ad, ticket_no="T-IPTAL")
    _makbuz(uygulama, h, sid, urun, fis=iptal_fis, iptal=True)
    baskasi_fis = _fis_yaz(oturum, cid, hasat, "5300", alici=ad, ticket_no="T-BASKA")
    _makbuz(uygulama, h, dunya["tedarikci_a2"], urun, fis=baskasi_fis)

    _ciftci(oturum, cid, sid)
    assert _cevap(oturum, "kantar") == KANTAR_YOK_MESAJI
    assert _cevap(oturum, "kantar liste") == KANTAR_YOK_MESAJI
    assert _cevap(oturum, "makbuz") == MAKBUZ_YOK_MESAJI

    kendi_fis = _fis_yaz(oturum, cid, hasat, "700", ticket_no="T-KENDI")
    kendi = _makbuz(uygulama, h, sid, urun, fis=kendi_fis)
    liste = _cevap(oturum, "kantar liste")
    assert liste.splitlines() == [
        f"#T-KENDI · NET 700,00 kg · {kendi['receipt_no']}"
    ]
    for tuzak in ("T-SAHIPSIZ", "T-TASLAK", "T-IPTAL", "T-BASKA"):
        assert tuzak not in liste


def test_KIRACI_YALITIMI_KOMSU_FIRMANIN_AYNI_ADLI_CIFTCISI(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 3: komşu firmadaki AYNI ADLI çiftçinin fişi/makbuzu SIZMAZ.

    İki firmanın makbuz serisi AYRIDIR ve ikisi de `MM-000001` üretebilir;
    bu yüzden ayrım NUMARA ile değil RAKAMLA ölçülür (7.000 kg komşunun).
    """
    cid_a, cid_b = dunya["firma_a"], dunya["firma_b"]
    fis_a = _fis_yaz(oturum, cid_a, dunya["hasat_a"], "1000")
    fis_b = _fis_yaz(oturum, cid_b, dunya["hasat_b"], "7000")
    _makbuz(
        uygulama, personel_istemcisi(cid_a), dunya["tedarikci_a"], dunya["urun_a"],
        fis=fis_a, fiyat="3.00",
    )
    komsu = _makbuz(
        uygulama, personel_istemcisi(cid_b), dunya["tedarikci_b"], dunya["urun_b"],
        fis=fis_b, fiyat="4.00",
    )

    _ciftci(oturum, cid_a, dunya["tedarikci_a"])
    kantar = _cevap(oturum, "kantar")
    assert "NET: 1.000,00 kg" in kantar
    assert "7.000" not in kantar
    makbuz = _cevap(oturum, "makbuz")
    assert "3.000,00" in makbuz  # 1000 kg × 3.00
    assert _tl(komsu["gross_amount"]) not in makbuz
    assert "4,00" not in makbuz


def test_TASLAK_GIZLI_ve_BASKASININ_NUMARASI_HICLIK_CEVABIYLA_BAYT_BAYT_AYNI(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 4: taslak görünmez; başkasının numarası "kayıt yok" ile AYIRT EDİLEMEZ.

    Bayt karşılaştırması ÜÇ cevap arasında: kaydı olmayan çiftçinin
    "makbuz"u, başkasının GERÇEK numarası ve hiç var olmayan bir numara.
    Çiftçinin KENDİ makbuzu kesildikten sonra da başkasının numarası AYNI
    metni alır — "numara var ama sizin değil" diyen hiçbir dal yoktur.
    """
    from app.whatsapp.ciftci_yurutucu import MAKBUZ_YOK_MESAJI

    cid, sid = dunya["firma_a"], dunya["tedarikci_a"]
    h = personel_istemcisi(cid)
    _makbuz(uygulama, h, sid, dunya["urun_a"], kes=False)
    baskasi = _makbuz(uygulama, h, dunya["tedarikci_a2"], dunya["urun_a"])

    _ciftci(oturum, cid, sid)
    hiclik = _cevap(oturum, "makbuz")
    assert hiclik == MAKBUZ_YOK_MESAJI
    baskasinin = _cevap(oturum, f"makbuz {baskasi['receipt_no']}")
    yok_olan = _cevap(oturum, "makbuz MM-999999")
    assert hiclik.encode("utf-8") == baskasinin.encode("utf-8") == yok_olan.encode(
        "utf-8"
    )

    kendi = _makbuz(uygulama, h, sid, dunya["urun_a"])
    assert kendi["receipt_no"] in _cevap(oturum, "makbuz")
    assert _cevap(oturum, f"makbuz {baskasi['receipt_no']}").encode("utf-8") == (
        hiclik.encode("utf-8")
    )
    # Harf büyüklüğü numarayı değiştirmez.
    assert kendi["receipt_no"] in _cevap(oturum, f"makbuz {kendi['receipt_no'].lower()}")


def test_MUSTERI_TARAFI_IKISINDE_DE_NOTR_ve_TARAF_TIPI_SIZMIYOR(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 5: CUSTOMER bağlantısı KANTAR/MAKBUZ sorar → NÖTR, kaydı yok ile AYNI.

    Müşteri ile AYNI SAYISAL kimliği taşıyan bir tedarikçinin kesilmiş,
    fişli makbuzu VAR. `party_type` denetimi düşerse müşterinin kimliği o
    makbuzu bulur ve rakam döner.
    """
    from app.whatsapp.ciftci_yurutucu import KANTAR_YOK_MESAJI, MAKBUZ_YOK_MESAJI

    cid = dunya["firma_a"]
    assert dunya["musteri_a"] == dunya["tedarikci_ikiz"]
    fis = _fis_yaz(oturum, cid, dunya["hasat_a"], "3333", ticket_no="T-IKIZ")
    ikiz = _makbuz(
        uygulama, personel_istemcisi(cid), dunya["tedarikci_ikiz"], dunya["urun_a"],
        fis=fis,
    )

    _ciftci(oturum, cid, dunya["musteri_a"], party_type="CUSTOMER")
    kantar = _cevap(oturum, "kantar")
    makbuz = _cevap(oturum, "makbuz")
    assert kantar == KANTAR_YOK_MESAJI
    assert makbuz == MAKBUZ_YOK_MESAJI
    assert _cevap(oturum, f"makbuz {ikiz['receipt_no']}") == MAKBUZ_YOK_MESAJI
    for metin in (kantar, makbuz):
        assert "T-IKIZ" not in metin and ikiz["receipt_no"] not in metin
        assert "müşteri" not in metin.lower()


def test_RIZASIZ_CIFTCI_KVKK_METNI_ALIYOR_RAKAM_ALMIYOR(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 6: rıza NO_RECORD → KVKK metni; fiş/makbuz rakamı DÖNMEZ."""
    from app.whatsapp.ciftci_niyet import kvkk_metni

    cid, sid = dunya["firma_a"], dunya["tedarikci_a"]
    fis = _fis_yaz(oturum, cid, dunya["hasat_a"], "4321", ticket_no="T-RIZA")
    makbuz = _makbuz(uygulama, personel_istemcisi(cid), sid, dunya["urun_a"], fis=fis)

    _baglanti_ac(oturum, cid, "SUPPLIER", sid)
    beklenen = kvkk_metni(dunya["firma_a_adi"])
    for soru in ("kantar", "makbuz", f"makbuz {makbuz['receipt_no']}", "kantar liste"):
        cevap = _cevap(oturum, soru)
        assert cevap == beklenen, soru
        assert "4.321" not in cevap and makbuz["receipt_no"] not in cevap


def test_IKI_FIRMALI_CIFTCI_ONEK_FIRMAYI_SECIYOR(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 7: "1 KANTAR" / "2 MAKBUZ" o firmanın satırı; öneksiz soru LİSTE alır."""
    cid_a, cid_b = dunya["firma_a"], dunya["firma_b"]
    fis_a = _fis_yaz(oturum, cid_a, dunya["hasat_a"], "1111")
    fis_b = _fis_yaz(oturum, cid_b, dunya["hasat_b"], "2222")
    _makbuz(
        uygulama, personel_istemcisi(cid_a), dunya["tedarikci_a"], dunya["urun_a"],
        fis=fis_a, fiyat="3.00",
    )
    makbuz_b = _makbuz(
        uygulama, personel_istemcisi(cid_b), dunya["tedarikci_b"], dunya["urun_b"],
        fis=fis_b, fiyat="5.00",
    )
    _baglanti_ac(oturum, cid_a, "SUPPLIER", dunya["tedarikci_a"])
    _baglanti_ac(oturum, cid_b, "SUPPLIER", dunya["tedarikci_b"])

    secim = _cevap(oturum, "KANTAR")
    assert "1." in secim and "2." in secim
    assert "1.111" not in secim and "2.222" not in secim

    _cevap(oturum, "1 EVET")
    _cevap(oturum, "2 EVET")
    bir = _cevap(oturum, "1 KANTAR")
    assert "NET: 1.111,00 kg" in bir and "2.222" not in bir
    iki = _cevap(oturum, "2 MAKBUZ")
    assert f"NET ÖDENECEK: {_tl(makbuz_b['net_payable'])} TL" in iki
    assert "3.333" not in iki  # 1111 × 3.00 — birinci firmanın brütü
    assert "NET 2.222,00 kg" in _cevap(oturum, "2 KANTAR LISTE")


def test_PERSONEL_NUMARASI_KANTAR_SORARSA_PERSONEL_YOLU_DEGISMIYOR(
    oturum, dunya, monkeypatch
):
    """Kapı 8: personel numarası "KANTAR" yazar → personel cevabı AYNEN; çiftçi aracı YOK.

    Önce yalnız personel bağlıyken cevap ölçülür; sonra AYNI numara bir
    tedarikçiye de (rızalı) bağlanır ve cevap BİREBİR aynı kalır. Çiftçi
    araç gövdeleri casusla değiştirilir ve SIFIR kez çağrılır.
    """
    from sqlalchemy import text

    from app.whatsapp import ciftci_yurutucu

    cagrilar: list[str] = []

    def casus(ad):
        def govde(*a, **k):
            cagrilar.append(ad)
            raise AssertionError(f"personel yolunda {ad} cagrildi")

        return govde

    monkeypatch.setattr(
        ciftci_yurutucu,
        "CIFTCI_ARAC_GOVDELERI",
        {ad: casus(ad) for ad in ciftci_yurutucu.CIFTCI_BEYAZ_LISTESI},
    )
    an = datetime.now(timezone.utc)
    oturum.execute(
        text(
            "INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
            "created_at,updated_at) VALUES(:c,:u,:p,1,:t,:t)"
        ),
        {"c": dunya["firma_a"], "u": dunya["personel"], "p": NUMARA, "t": an},
    )
    oturum.commit()

    once = {soru: _cevap(oturum, soru) for soru in ("KANTAR", "makbuz")}
    _ciftci(oturum, dunya["firma_a"], dunya["tedarikci_a"])
    sonra = {soru: _cevap(oturum, soru) for soru in ("KANTAR", "makbuz")}
    assert sonra == once
    assert cagrilar == []
    sayac = oturum.execute(text(f"SELECT COUNT(*) FROM {SAYAC_TABLO}")).scalar_one()
    assert sayac == 0, "personel yolu çiftçi mesaj sayacına DOKUNMAMALI"


def test_HIZ_SINIRI_KANTAR_VE_MAKBUZ_CEVAPLARINI_DA_SAYIYOR(
    oturum, dunya, uygulama, personel_istemcisi
):
    """Kapı 9: 15 cevap / 20 işleme sınırı bu iki niyete de AYNEN uygulanır."""
    from sqlalchemy import text

    from app.whatsapp import schema

    cid, sid = dunya["firma_a"], dunya["tedarikci_a"]
    fis = _fis_yaz(oturum, cid, dunya["hasat_a"], "1500")
    _makbuz(uygulama, personel_istemcisi(cid), sid, dunya["urun_a"], fis=fis)
    _ciftci(oturum, cid, sid)

    for sira in range(1, schema.MESAJ_CEVAP_SINIRI + 1):
        soru = "kantar" if sira % 2 else "makbuz"
        gonderilen, satir = _konus(oturum, soru)
        assert satir["status"] == "ANSWERED", sira
        assert gonderilen and "NET" in gonderilen[-1][1], sira

    gonderilen, satir = _konus(oturum, "kantar")
    assert gonderilen == []
    assert satir["status"] == "IGNORED"
    assert satir["last_error"] == "cevap_siniri"
    sayac = oturum.execute(
        text(f"SELECT attempt_count FROM {SAYAC_TABLO} WHERE phone=:p"),
        {"p": NUMARA},
    ).scalar_one()
    assert sayac == schema.MESAJ_CEVAP_SINIRI + 1
