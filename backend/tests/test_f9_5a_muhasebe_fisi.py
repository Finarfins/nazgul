"""F9-5a: kanonik muhasebe fişi + aylık KDV özeti + hesap planı eşlemesi.

Konu: `app/muhasebe/{fis,schema,hesap_plani,kaynak,kdv_ozeti}.py`,
`app/routers/accounting.py`, göç `20260927_0093`, `app/auth.py`nin METODA
BAKAN `/api/accounting` kuralı.

--- MUTASYON TABLOSU ------------------------------------------------------

  * `fis.denge_hatalari`da Σborç/Σalacak kıyasını düşürmek
                                    -> DENGE kapısı KIRMIZI
  * `kaynak._SATIS`ta `cari_borclu`yu çevirmek (borç/alacak takası)
                                    -> SATIŞ FİŞİ adımı KIRMIZI
  * Bir kolun `where`inden `company_id == cid`yi düşürmek
                                    -> KİRACI YÜKLEMİ İLK kapısı + Core kiracı
                                       kapısı + KOMŞU FİRMA adımı KIRMIZI
  * `STOCK_STATUSES` süzgecini düşürmek
                                    -> DURUM adımı KIRMIZI
  * Servis kolunda `* kur` çarpımını düşürmek
                                    -> DÖVİZ adımı KIRMIZI
  * `_UtcAn` yerine düz `DateTime` bağlamak
                                    -> AY SINIRI adımı (SQLite'ta) KIRMIZI;
                                       `substr(created_at,…)` PG ikizinde
  * `auth.py`deki `/api/accounting` kuralını `GET -> read`in ALTINA almak
                                    -> İZİN MATRİSİ (`depo`/`satis` 200) KIRMIZI
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260927_0093_muhasebe_hesap_eslemeleri.py"
KAYNAK = BACKEND / "app" / "muhasebe" / "kaynak.py"
DONEM = "2026-07"

_CALISMA = Path(tempfile.mkdtemp(prefix="f95a-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "f95a.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"
sys.path.insert(0, str(BACKEND))


# ------------------------------------------------------------------ saf ---

def _fis(satirlar, **alan):
    from app.muhasebe.fis import Fis

    temel = dict(
        fis_no="SAT-1", fis_tarihi=date(2026, 7, 1), belge_tipi="invoice", belge_no="S-1",
        belge_tarihi=date(2026, 7, 1), odeme_yontemi="credit", kaynak="SATIS",
    )
    temel.update(alan)
    return Fis(satirlar=tuple(satirlar), **temel)


def _s(kod, borc="0.00", alacak="0.00"):
    from app.muhasebe.fis import FisSatiri

    return FisSatiri(kod, D(borc), D(alacak), "a")


def test_DENGE_kurusu_kurusuna() -> None:
    from app.muhasebe.fis import FisHatasi, dogrula

    assert dogrula(_fis([_s("120", borc="100.00"), _s("600", alacak="100.00")]))
    with pytest.raises(FisHatasi) as hata:
        dogrula(_fis([_s("120", borc="100.00"), _s("600", alacak="99.99")]))
    assert "fark 0.01" in str(hata.value)


def test_TEK_TARAF_ve_EN_AZ_IKI_SATIR() -> None:
    from app.muhasebe.fis import denge_hatalari

    iki_taraf = denge_hatalari(_fis([_s("120", "5.00", "5.00"), _s("600", "0.00", "0.00")]))
    assert any("tam olarak bir taraf" in n for n in iki_taraf)
    assert any("en az iki satır" in n for n in denge_hatalari(_fis([_s("120", "0.00", "0.00")])))
    negatif = denge_hatalari(_fis([_s("120", "-1.00"), _s("600", alacak="-1.00")]))
    assert any("negatif" in n for n in negatif)
    kesirli = denge_hatalari(_fis([_s("120", "1.005"), _s("600", alacak="1.005")]))
    assert any("kuruşa tam" in n for n in kesirli)


def test_K11_BELGE_ALANLARI_ZORUNLU() -> None:
    from app.muhasebe.fis import denge_hatalari

    nedenler = denge_hatalari(
        _fis([_s("120", "1.00"), _s("600", alacak="1.00")], belge_no=" ", odeme_yontemi="")
    )
    assert "belge_no boş (K11)" in nedenler
    assert "odeme_yontemi boş (K11)" in nedenler


def test_FIS_NO_BELIRLENIMCI_ve_ONEKLER_CAKISMAZ() -> None:
    from app.muhasebe.fis import FIS_ONEKLERI, fis_no

    assert fis_no("SATIS", 12) == "SAT-12" == fis_no("SATIS", "12")
    assert fis_no("ALIS", 3) == "ALS-3"
    assert fis_no("SERVIS_FATURA", "F2026-1") == "SVF-F2026-1"
    assert fis_no("MUSTAHSIL", "M-9") == "MM-M-9"
    assert len(set(FIS_ONEKLERI.values())) == len(FIS_ONEKLERI)
    with pytest.raises(ValueError):
        fis_no("KASA", 1)
    with pytest.raises(ValueError):
        fis_no("SATIS", "  ")


def test_TOPLAYICI_sifiri_dusurur_negatifi_karsi_tarafa_gecirir() -> None:
    from app.muhasebe.fis import SatirToplayici

    t = SatirToplayici()
    t.ekle("120", "B", D("10.00"), "a")
    t.ekle("120", "B", D("5.00"), "a")
    t.ekle("600", "A", D("0.00"), "a")
    t.ekle("610", "A", D("-3.00"), "a")
    assert [(s.hesap_kodu, s.borc, s.alacak) for s in t.satirlar()] == [
        ("120", D("15.00"), D("0.00")),
        ("610", D("3.00"), D("0.00")),
    ]


def test_HESAP_PLANI_cozum_sirasi_ve_varsayilan() -> None:
    from app.muhasebe.hesap_plani import Esleme, HesapPlani, VARSAYILANLAR, hesap_kodu_gecerli
    from app.muhasebe.schema import OLAYLAR

    assert tuple(VARSAYILANLAR) == OLAYLAR
    assert HesapPlani().hesap("SATIS_KDV", 20) == "391"
    plan = HesapPlani([
        Esleme("SATIS_KDV", D("20"), None, "391.20"),
        Esleme("SATIS_CARI", None, "CUSTOMER", "120.01"),
        Esleme("SATIS_CARI", None, None, "120.99"),
    ])
    assert plan.hesap("SATIS_KDV", D("20.0000")) == "391.20"
    assert plan.hesap("SATIS_KDV", 10) == "391"
    assert plan.hesap("SATIS_CARI", taraf="CUSTOMER") == "120.01"
    assert plan.hesap("SATIS_CARI") == "120.99"
    for kod, beklenen in (("120", True), ("391.20", True), ("120.01.05", True),
                          ("12", False), ("120.1", False), ("١٢٠", False), ("120.", False)):
        assert hesap_kodu_gecerli(kod) is beklenen, kod


def test_DONEM_bicimi_ve_istanbul_sinirlari() -> None:
    from app.muhasebe.kaynak import DonemHatasi, donem_coz

    d = donem_coz("2026-07")
    assert d.bas_an == datetime(2026, 6, 30, 21, 0, tzinfo=timezone.utc)
    assert d.son_an == datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)
    assert donem_coz("2026-12").son_tarih == date(2027, 1, 1)
    for kotu in ("2026-13", "2026-7", "2026-07-01", "26-07", "2026/07", "٢٠٢٦-07", ""):
        with pytest.raises(DonemHatasi):
            donem_coz(kotu)


# -------------------------------------------------------------- statik ---

def test_OLAYLAR_goc_ve_Core_tanimda_BIREBIR() -> None:
    import importlib.util

    from app.muhasebe import schema

    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260927_0093"' in kaynak
    assert 'down_revision = "20260925_0092"' in kaynak
    spec = importlib.util.spec_from_file_location("f95a_goc", GOC)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    assert modul.OLAYLAR == schema.OLAYLAR
    assert modul.TARAF_TIPLERI == schema.TARAF_TIPLERI
    assert (modul.TEKIL_ORANLI, modul.TEKIL_ORANSIZ) == (schema.TEKIL_ORANLI, schema.TEKIL_ORANSIZ)
    for ad in ("ck_mhe_olay", "ck_mhe_kdv_orani", "ck_mhe_taraf_tipi"):
        assert ad in kaynak, ad
    # Tohum YOK (göç başlığı): göç tabloya satır yazmaz.
    agac = ast.parse(kaynak)
    cagrilar = {getattr(d.func, "attr", None) for d in ast.walk(agac) if isinstance(d, ast.Call)}
    assert not cagrilar & {"bulk_insert", "execute"}, cagrilar


def test_KIRACI_YUKLEMI_her_where_in_ILK_kosulu() -> None:
    """`kaynak.py`deki her `.where(...)`in ilk argümanı `<t>.c.company_id == cid`."""
    agac = ast.parse(KAYNAK.read_text(encoding="utf-8"))
    bulunan = 0
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Call) and getattr(dugum.func, "attr", None) == "where":
            bulunan += 1
            ilk = dugum.args[0]
            assert isinstance(ilk, ast.Compare), ast.unparse(ilk)
            assert isinstance(ilk.left, ast.Attribute) and ilk.left.attr == "company_id", ast.unparse(ilk)
            assert isinstance(ilk.comparators[0], ast.Name) and ilk.comparators[0].id == "cid"
    assert bulunan == 12, bulunan  # satış 2, alış 2, iade 3, servis 2, müstahsil 3


def test_KURAL_GENEL_GET_READIN_USTUNDE() -> None:
    from app.auth import required_permission

    assert required_permission("GET", "/api/accounting/vouchers") == "reports"
    assert required_permission("GET", "/api/accounting/vat-summary") == "reports"
    assert required_permission("GET", "/api/accounting/account-map") == "reports"
    assert required_permission("PUT", "/api/accounting/account-map") == "finance"


# ------------------------------------------------------------ davranış ---

@pytest.fixture(scope="module")
def uygulama():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))
PAROLA = "F95aTest!12345"
ROLLER = ("admin", "yonetici", "muhasebe", "satis", "depo", "rapor")
UTC = timezone.utc


def _ekle(db, tablo: str, **alan) -> int:
    from sqlalchemy import text

    sutunlar = ",".join(alan)
    yer = ",".join(":" + k for k in alan)
    return int(db.execute(
        text(f"INSERT INTO {tablo}({sutunlar}) VALUES({yer}) RETURNING id"), alan
    ).scalar_one())


def _belge(db, cid, cari, tarih, durum, kalemler, *, no=None, vat_total=None, odeme="credit",
           alis=False):
    tablo, kalem_tablo, fk, cari_sutun, tarih_sutun = (
        ("purchases", "purchase_items", "purchase_id", "supplier_id", "purchase_date") if alis
        else ("orders", "order_items", "order_id", "customer_id", "order_date")
    )
    kdv = sum((D(k[2]) for k in kalemler), D("0"))
    toplam = sum((D(k[3]) for k in kalemler), D("0"))
    oid = _ekle(db, tablo, company_id=cid, **{cari_sutun: cari, tarih_sutun: tarih},
                status=durum, document_no=no, payment_method=odeme,
                vat_total=D(vat_total) if vat_total is not None else kdv,
                final_total=toplam, subtotal=toplam - kdv, grand_total=toplam)
    for oran, matrah, kv, tp in kalemler:
        _ekle(db, kalem_tablo, company_id=cid, **{fk: oid}, product_name="Ürün", quantity=D("1"),
              unit_price=D(tp), vat_rate=D(oran), line_subtotal=D(matrah), line_vat=D(kv),
              line_total=D(tp))
    return oid


def _iade(db, cid, tur, cari, tarih, kalemler, durum="completed"):
    kdv = sum((D(k[2]) for k in kalemler), D("0"))
    toplam = sum((D(k[3]) for k in kalemler), D("0"))
    rid = _ekle(db, "returns", company_id=cid, return_type=tur, entity_id=cari, return_date=tarih,
                status=durum, total=toplam, vat_total=kdv, subtotal=toplam - kdv,
                document_no=f"IADE-{tur[:1]}")
    for oran, matrah, kv, tp in kalemler:
        _ekle(db, "return_items", company_id=cid, return_id=rid, product_name="Ürün",
              quantity=D("1"), unit_price=D(tp), line_total=D(tp), vat_rate=D(oran),
              line_subtotal=D(matrah), line_vat=D(kv))
    return rid


def _fatura(db, cid, uid, no, an, *, durum="ISSUED", doviz="TRY", kur="1", kalemler=(),
            musteri=None):
    import json

    bos = "{}"
    fid = _ekle(db, "invoices", company_id=cid, invoice_number=no, status=durum, currency=doviz,
                exchange_rate=D(kur), customer_snapshot=json.dumps(musteri or {}),
                machine_snapshot=bos, work_order_snapshot=bos, company_snapshot=bos,
                technician_snapshot=bos, warranty_snapshot=bos, totals_snapshot=bos,
                tax_snapshot=bos, created_by=uid, created_at=an, updated_at=an)
    for oran, kdv, toplam, musteri_payi, garanti in kalemler:
        _ekle(db, "invoice_items", company_id=cid, invoice_id=fid, item_type="PART",
              description="Parça", quantity=D("1"), unit_price=D(toplam), original_price=D(toplam),
              discount_amount=D("0"), tax_rate=D(oran), tax_amount=D(kdv), total=D(toplam),
              warranty_percent=D("0"), customer_payable=D(musteri_payi),
              company_payable=D(garanti), source_snapshot=bos)
    return fid


def tohumla(db, firma_a, firma_b, admin_id) -> dict:
    """A'da Temmuz 2026 belgeleri, B'de komşu belge. PG ikizi de bunu kullanır."""
    an = datetime.now(UTC)

    def cari(tablo, cid, ad, vkn):
        return _ekle(db, tablo, name=ad, tax_number=vkn, is_active=True, company_id=cid,
                     opening_balance=0, risk_limit=0, payment_term_days=0)

    musteri = cari("customers", firma_a, "Ahmet Çiftçi", "1234567890")
    tedarikci = cari("suppliers", firma_a, "Tohum A.Ş.", "9876543210")
    komsu = cari("customers", firma_b, "Komşu Müşteri", "5555555555")
    b = {"musteri": musteri, "tedarikci": tedarikci}
    b["o1"] = _belge(db, firma_a, musteri, "2026-07-10", "approved",
                     [("20", "100.00", "20.00", "120.00"), ("10", "50.00", "5.00", "55.00")],
                     no="S-1", odeme="cash")
    # G3: başlık KDV'si satırdan bir kuruş sapıyor.
    b["o2"] = _belge(db, firma_a, musteri, "2026-07-31", "completed",
                     [("20", "10.00", "2.00", "12.00")], no="S-2", vat_total="2.01")
    for anahtar, gun, durum in (("o3", "2026-07-15", "pending"), ("o4", "2026-07-16", "cancelled"),
                                ("o5", "2026-07-17", "draft"), ("o6", "2026-08-01", "approved")):
        b[anahtar] = _belge(db, firma_a, musteri, gun, durum,
                            [("20", "1000.00", "200.00", "1200.00")], no="S-" + anahtar)
    # G2: eski %18.
    b["o7"] = _belge(db, firma_a, musteri, "2026-07-18", "approved",
                     [("18", "100.00", "18.00", "118.00")], no="S-7")
    b["p1"] = _belge(db, firma_a, tedarikci, "2026-07-05", "completed",
                     [("20", "200.00", "40.00", "240.00")], no="A-1", alis=True)
    b["r1"] = _iade(db, firma_a, "sale_return", musteri, "2026-07-20",
                    [("20", "10.00", "2.00", "12.00")])
    b["r2"] = _iade(db, firma_a, "purchase_return", tedarikci, "2026-07-21",
                    [("20", "20.00", "4.00", "24.00")])
    m = {"id": musteri, "name": "Ahmet Çiftçi", "tax_number": "1234567890"}
    # 31 Temmuz 23:30 İstanbul = 20:30 UTC -> TEMMUZ (G5 sınırı).
    b["i1"] = _fatura(db, firma_a, admin_id, "SF-1", datetime(2026, 7, 31, 20, 30, tzinfo=UTC),
                      doviz="USD", kur="30", musteri=m,
                      kalemler=[("20", "2.00", "12.00", "12.00", "0.00")])
    # 1 Temmuz 00:30 İstanbul = 30 Haziran 21:30 UTC -> TEMMUZ + G5 uyarısı.
    b["i2"] = _fatura(db, firma_a, admin_id, "SF-2", datetime(2026, 6, 30, 21, 30, tzinfo=UTC),
                      musteri=m, kalemler=[("20", "20.00", "120.00", "100.00", "20.00")])
    # 1 Ağustos 00:30 İstanbul -> AĞUSTOS.
    b["i3"] = _fatura(db, firma_a, admin_id, "SF-3", datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
                      musteri=m, kalemler=[("20", "20.00", "120.00", "120.00", "0.00")])
    # TAM sınır: 1 Ağustos 00:00 İstanbul = 21:00 UTC -> AĞUSTOS.
    b["i5"] = _fatura(db, firma_a, admin_id, "SF-5", datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
                      musteri=m, kalemler=[("20", "20.00", "120.00", "120.00", "0.00")])
    b["i4"] = _fatura(db, firma_a, admin_id, "SF-4", datetime(2026, 7, 10, 9, 0, tzinfo=UTC),
                      durum="CANCELLED", musteri=m,
                      kalemler=[("20", "20.00", "120.00", "120.00", "0.00")])
    makbuz = _ekle(db, "producer_receipts", company_id=firma_a, supplier_id=tedarikci,
                   purchase_id=b["p1"], receipt_no="MM-7", status="issued",
                   issued_at=datetime(2026, 7, 12, 8, 0, tzinfo=UTC), gross_amount=D("1000.00"),
                   withholding_total=D("20.00"), social_security_total=D("10.00"),
                   net_payable=D("970.00"), created_at=an, updated_at=an)
    _ekle(db, "producer_receipt_items", company_id=firma_a, receipt_id=makbuz,
          entered_quantity=D("1000"), entered_unit="kg", entered_factor=D("1"),
          base_quantity=D("1000"), unit_price=D("1.00"), line_gross=D("1000.00"),
          withholding_rate=D("2"), withholding_amount=D("20.00"), social_security_rate=D("1"),
          social_security_amount=D("10.00"), line_net=D("970.00"), created_at=an, updated_at=an)
    b["mm"] = makbuz
    b["b1"] = _belge(db, firma_b, komsu, "2026-07-10", "approved",
                     [("20", "999.00", "199.80", "1198.80")], no="S-1")
    return b


@pytest.fixture()
def dunya(uygulama):
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    n = next(_SAYAC)
    with SessionLocal() as db:
        db.execute(text("DELETE FROM muhasebe_hesap_eslemeleri"))
        an = datetime.now(UTC)
        firma_a = _ekle(db, "companies", name=f"F95a Bir {n}", is_active=True, created_at=an)
        firma_b = _ekle(db, "companies", name=f"F95a Iki {n}", is_active=True, created_at=an)
        roller = {}
        for rol in ROLLER:
            roller[rol] = _ekle(
                db, "app_users", username=f"f95a-{rol}-{n}", email=f"f95a-{rol}-{n}@f95a.invalid",
                email_verified=True, display_name=rol, password_hash=hash_password(PAROLA), role=rol,
                is_active=True, must_change_password=False, created_at=an,
            )
            for cid in (firma_a, firma_b):
                _ekle(db, "user_company_memberships", user_id=roller[rol], company_id=cid,
                      is_default=False, created_at=an)
        belge = tohumla(db, firma_a, firma_b, roller["admin"])
        db.commit()
    yield {"firma_a": firma_a, "firma_b": firma_b, "roller": roller, "n": n, "belge": belge,
           "musteri": belge["musteri"]}


def _veri(cid):
    from app.db import SessionLocal
    from app.muhasebe.hesap_plani import plan_oku
    from app.muhasebe.kaynak import donem_coz, donem_oku

    with SessionLocal() as db:
        return donem_oku(db, cid, donem_coz(DONEM), plan_oku(db, cid))


def _fis_bul(veri, no):
    return next(f for f in veri.fisler if f.fis_no == no)


def _satir_ozeti(fis):
    return [(s.hesap_kodu, format(s.borc, "f"), format(s.alacak, "f")) for s in fis.satirlar]


def test_SATIS_FISI_satirdan_ve_dogru_yonde(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    fis = _fis_bul(veri, f"SAT-{dunya['belge']['o1']}")
    assert _satir_ozeti(fis) == [
        ("120", "175.00", "0.00"),
        ("600", "0.00", "50.00"),
        ("391", "0.00", "5.00"),
        ("600", "0.00", "100.00"),
        ("391", "0.00", "20.00"),
    ]
    assert fis.odeme_yontemi == "cash" and fis.belge_no == "S-1"
    assert fis.fis_tarihi == date(2026, 7, 10) and fis.cari_vkn == "1234567890"
    assert fis.satirlar[0].cari_tipi == "CUSTOMER" and fis.satirlar[0].cari_id == dunya["musteri"]


def test_ALIS_ve_IADE_FISLERI(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    b = dunya["belge"]
    assert _satir_ozeti(_fis_bul(veri, f"ALS-{b['p1']}")) == [
        ("320", "0.00", "240.00"), ("153", "200.00", "0.00"), ("191", "40.00", "0.00"),
    ]
    assert _satir_ozeti(_fis_bul(veri, f"SIA-{b['r1']}")) == [
        ("120", "0.00", "12.00"), ("610", "10.00", "0.00"), ("191", "2.00", "0.00"),
    ]
    assert _satir_ozeti(_fis_bul(veri, f"AIA-{b['r2']}")) == [
        ("320", "24.00", "0.00"), ("153", "0.00", "20.00"), ("391", "0.00", "4.00"),
    ]


def test_DURUM_pending_draft_cancelled_GIRMEZ_ve_G9(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    b = dunya["belge"]
    nolar = {f.fis_no for f in veri.fisler}
    for anahtar in ("o3", "o4", "o5", "o6"):
        assert f"SAT-{b[anahtar]}" not in nolar, anahtar
    g9 = [u for u in veri.uyarilar if u.kod == "G9" and u.kaynak == "SATIS"]
    assert len(g9) == 1 and g9[0].mesaj.startswith("2 satış")


def test_G3_BASLIK_SATIR_AYRISMASI_uyari_ve_satirdan_toplam(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    o2 = dunya["belge"]["o2"]
    g3 = [u for u in veri.uyarilar if u.kod == "G3" and u.belge_id == o2]
    assert len(g3) == 1 and g3[0].fark == D("0.01")
    assert _fis_bul(veri, f"SAT-{o2}").borc_toplami == D("12.00")


def test_SERVIS_FATURASI_DOVIZ_ve_ISTANBUL_AY_SINIRI(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    b = dunya["belge"]
    i1 = _fis_bul(veri, "SVF-SF-1")
    assert _satir_ozeti(i1) == [("120", "360.00", "0.00"), ("600", "0.00", "300.00"),
                                ("391", "0.00", "60.00")]
    assert i1.fis_tarihi == date(2026, 7, 31)
    i2 = _fis_bul(veri, "SVF-SF-2")
    assert _satir_ozeti(i2) == [("120", "100.00", "0.00"), ("120", "20.00", "0.00"),
                                ("600", "0.00", "100.00"), ("391", "0.00", "20.00")]
    assert i2.fis_tarihi == date(2026, 7, 1)
    assert i2.satirlar[1].aciklama.endswith("(garanti payı)")
    nolar = {f.fis_no for f in veri.fisler}
    assert not nolar & {"SVF-SF-3", "SVF-SF-4", "SVF-SF-5"}, nolar
    kodlar = {(u.kod, u.belge_id) for u in veri.uyarilar}
    assert ("G4", b["i1"]) in kodlar
    assert ("G5", b["i2"]) in kodlar and ("G5", b["i1"]) not in kodlar


def test_MUSTAHSIL_FISI_ve_G8(dunya) -> None:
    veri = _veri(dunya["firma_a"])
    assert _satir_ozeti(_fis_bul(veri, "MM-MM-7")) == [
        ("153", "1000.00", "0.00"), ("320", "0.00", "970.00"),
        ("360", "0.00", "20.00"), ("361", "0.00", "10.00"),
    ]
    g8 = [u for u in veri.uyarilar if u.kod == "G8"]
    assert [u.belge_id for u in g8] == [dunya["belge"]["p1"]]
    assert veri.mustahsil.brut == D("1000.00") and veri.mustahsil.belge_sayisi == 1


def test_KDV_OZETI_satirdan_uclu_ve_bilinmeyen_oran(dunya) -> None:
    from app.muhasebe.kdv_ozeti import kdv_ozeti

    ozet = kdv_ozeti(_veri(dunya["firma_a"]))
    satir = {(s["yon"], s["tur"], s["oran"]): s for s in ozet["satirlar"]}
    assert satir[("HESAPLANAN", "SATIS", "20.00")]["matrah"] == "110.00"
    assert satir[("HESAPLANAN", "SATIS", "20.00")]["kdv"] == "22.00"
    assert satir[("HESAPLANAN", "SATIS", "20.00")]["belge_sayisi"] == 2
    assert satir[("HESAPLANAN", "SATIS", "10.00")]["kdv"] == "5.00"
    assert satir[("HESAPLANAN", "SERVIS_FATURA", "20.00")]["kdv"] == "80.00"
    assert satir[("HESAPLANAN", "ALIS_IADE", "20.00")]["kdv"] == "4.00"
    assert satir[("INDIRILECEK", "ALIS", "20.00")]["kdv"] == "40.00"
    assert satir[("INDIRILECEK", "SATIS_IADE", "20.00")]["kdv"] == "2.00"
    on_sekiz = satir[("HESAPLANAN", "SATIS", "18.00")]
    assert on_sekiz["bilinen_oran"] is False and on_sekiz["kdv"] == "18.00"
    assert any(u["kod"] == "G2" and "%18.00" in u["mesaj"] for u in ozet["uyarilar"])
    assert ozet["hesaplanan_kdv"] == "129.00" and ozet["indirilecek_kdv"] == "42.00"
    assert ozet["fark"] == "87.00"
    assert any("Tevkifat desteklenmiyor" in n for n in ozet["kapsam_notlari"])


def test_KOMSU_FIRMA_YALITIMI(dunya) -> None:
    a = _veri(dunya["firma_a"])
    b = _veri(dunya["firma_b"])
    assert f"SAT-{dunya['belge']['b1']}" not in {f.fis_no for f in a.fisler}
    assert [f.fis_no for f in b.fisler] == [f"SAT-{dunya['belge']['b1']}"]
    assert all(f.cari_vkn != "5555555555" for f in a.fisler)


def test_DENGESIZ_FIS_YAZILMAZ_listelenir(dunya) -> None:
    """Kalemsiz onaylı satış sıfır satırlı fiş olur: YAZILMAZ, uyarıda adıyla."""
    from app.db import SessionLocal
    from app.muhasebe.kdv_ozeti import kdv_ozeti

    with SessionLocal() as db:
        bos = _belge(db, dunya["firma_a"], dunya["musteri"], "2026-07-22", "approved", [], no="S-BOS")
        db.commit()
    veri = _veri(dunya["firma_a"])
    assert f"SAT-{bos}" not in {f.fis_no for f in veri.fisler}
    assert [h.fis.fis_no for h in veri.reddedilen] == [f"SAT-{bos}"]
    uyarilar = kdv_ozeti(veri)["uyarilar"]
    dengesiz = next(u for u in uyarilar if u["kod"] == "DENGESIZ" and u["belge_no"] == "S-BOS")
    assert dengesiz["kdv_dahil"] is True
    assert "fişi yazılmadı; KDV özetine DAHİL" in dengesiz["mesaj"]


def test_DENGESIZ_FIS_KDV_OZETINE_DAHIL_ve_UYARI(dunya) -> None:
    """Dengesiz fiş yazılmaz ancak KDV'si özete DAHİL edilir; uyarı kdv_dahil: True taşır (H112)."""
    from app.db import SessionLocal
    from app.muhasebe.kdv_ozeti import kdv_ozeti

    with SessionLocal() as db:
        dengesiz_satis = _belge(
            db, dunya["firma_a"], dunya["musteri"], "2026-07-25", "approved",
            [("20", "100.00", "20.00", "150.00")], no="S-DENGESIZ",
        )
        db.commit()

    veri = _veri(dunya["firma_a"])
    assert f"SAT-{dengesiz_satis}" not in {f.fis_no for f in veri.fisler}
    assert any(h.fis.fis_no == f"SAT-{dengesiz_satis}" for h in veri.reddedilen)

    ozet = kdv_ozeti(veri)
    dengesiz_uyari = next(
        u for u in ozet["uyarilar"]
        if u["kod"] == "DENGESIZ" and u["belge_no"] == "S-DENGESIZ"
    )
    assert dengesiz_uyari["kdv_dahil"] is True
    assert "fişi yazılmadı; KDV özetine DAHİL" in dengesiz_uyari["mesaj"]


def test_DENGESIZ_ALIS_KDV_OZETINE_DAHIL_ve_UYARI(dunya) -> None:
    """Alış dalında dengesiz fiş indirilecek KDV'ye DAHİL edilir ve kdv_dahil: True uyarısı üretir (H112)."""
    from app.db import SessionLocal
    from app.muhasebe.kdv_ozeti import kdv_ozeti

    with SessionLocal() as db:
        dengesiz_alis = _belge(
            db, dunya["firma_a"], dunya["musteri"], "2026-07-26", "completed",
            [("20", "200.00", "40.00", "300.00")], no="A-DENGESIZ", alis=True,
        )
        db.commit()

    veri = _veri(dunya["firma_a"])
    assert f"ALS-{dengesiz_alis}" not in {f.fis_no for f in veri.fisler}
    assert any(h.fis.fis_no == f"ALS-{dengesiz_alis}" for h in veri.reddedilen)

    ozet = kdv_ozeti(veri)
    dengesiz_uyari = next(
        u for u in ozet["uyarilar"]
        if u["kod"] == "DENGESIZ" and u["belge_no"] == "A-DENGESIZ"
    )
    assert dengesiz_uyari["kdv_dahil"] is True
    assert "fişi yazılmadı; KDV özetine DAHİL" in dengesiz_uyari["mesaj"]


def test_VAT_SUMMARY_DENGESIZ_UYARI_RESPONSE_MODEL_VALIDATION(uygulama, dunya) -> None:
    """/api/accounting/vat-summary uç noktası dengesiz fiş uyarısını KdvOzeti response_model ile doğrular (H112)."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        _belge(
            db, dunya["firma_a"], dunya["musteri"], "2026-07-27", "approved",
            [("20", "50.00", "10.00", "80.00")], no="S-ROUTE-DENGESIZ",
        )
        db.commit()

    h = _baslik(uygulama, dunya, "muhasebe")
    r = uygulama.get(f"/api/accounting/vat-summary?period={DONEM}", headers=h)
    assert r.status_code == 200, r.text
    veri = r.json()
    uyari = next(u for u in veri["uyarilar"] if u["kod"] == "DENGESIZ" and u["belge_no"] == "S-ROUTE-DENGESIZ")
    assert uyari["kdv_dahil"] is True
    assert "fişi yazılmadı; KDV özetine DAHİL" in uyari["mesaj"]


def test_MUSTAHSIL_G5_ISTANBUL_AY_SINIRI(dunya) -> None:
    """2026-07-31 22:30 UTC (= 2026-08-01 01:30 İstanbul) kesilen makbuz:
    Ağustos dönemine (2026-08) düşmeli, G5 uyarısı üretmeli; Temmuz'da (2026-07)
    özetinde bu makbuz için satır OLMAMALIDIR (H113).
    """
    from datetime import datetime, timezone
    from decimal import Decimal as D
    from app.db import SessionLocal
    from app.muhasebe.hesap_plani import plan_oku
    from app.muhasebe.kaynak import donem_coz, donem_oku
    from app.muhasebe.kdv_ozeti import kdv_ozeti

    UTC = timezone.utc
    an = datetime(2026, 7, 31, 22, 30, tzinfo=UTC)
    with SessionLocal() as db:
        tedarikci_b = _ekle(db, "suppliers", company_id=dunya["firma_b"], name="Sınır Çiftçi B",
                            tax_number="9999999999", opening_balance=0, risk_limit=0,
                            payment_term_days=0, is_active=True)
        makbuz = _ekle(
            db, "producer_receipts", company_id=dunya["firma_b"], supplier_id=tedarikci_b,
            receipt_no="MM-G5", status="issued",
            issued_at=an, gross_amount=D("500.00"),
            withholding_total=D("10.00"), social_security_total=D("5.00"),
            net_payable=D("485.00"), created_at=an, updated_at=an,
        )
        _ekle(
            db, "producer_receipt_items", company_id=dunya["firma_b"], receipt_id=makbuz,
            entered_quantity=D("500"), entered_unit="kg", entered_factor=D("1"),
            base_quantity=D("500"), unit_price=D("1.00"), line_gross=D("500.00"),
            withholding_rate=D("2"), withholding_amount=D("10.00"), social_security_rate=D("1"),
            social_security_amount=D("5.00"), line_net=D("485.00"), created_at=an, updated_at=an,
        )
        db.commit()

    with SessionLocal() as db:
        plan_b = plan_oku(db, dunya["firma_b"])
        temmuz_veri = donem_oku(db, dunya["firma_b"], donem_coz("2026-07"), plan_b)
        agustos_veri = donem_oku(db, dunya["firma_b"], donem_coz("2026-08"), plan_b)

    temmuz_ozet = kdv_ozeti(temmuz_veri)
    # Temmuz özetinde müstahsil satırı/belgesi YOK
    assert temmuz_ozet["mustahsil"]["belge_sayisi"] == 0
    assert not any(f.fis_no == "MM-MM-G5" for f in temmuz_veri.fisler)

    # Ağustos özetine düşer ve G5 uyarısı taşır
    agustos_ozet = kdv_ozeti(agustos_veri)
    assert agustos_ozet["mustahsil"]["belge_sayisi"] == 1
    agustos_fis = next(f for f in agustos_veri.fisler if f.fis_no == "MM-MM-G5")
    assert agustos_fis.fis_tarihi == date(2026, 8, 1)

    g5_uyarilari = [
        u for u in agustos_ozet["uyarilar"]
        if u["kod"] == "G5" and u["kaynak"] == "MUSTAHSIL" and u["belge_no"] == "MM-G5"
    ]
    assert len(g5_uyarilari) == 1, f"G5 uyarısı bulunamadı: {agustos_ozet['uyarilar']}"
    assert "UTC'ye göre 2026-07, İstanbul saatine göre 2026-08 — İSTANBUL ayına yazıldı" in g5_uyarilari[0]["mesaj"]



# --------------------------------------------------------------- uçlar ---

def _baslik(istemci, dunya, rol, cid=None):
    r = istemci.post("/api/auth/login",
                     json={"username": f"f95a-{rol}-{dunya['n']}", "password": PAROLA})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"],
            "X-Company-ID": str(cid or dunya["firma_a"])}


#: ELLE yazıldı — `ROLE_PERMISSIONS`tan türetilseydi izin tablosunu bozan bir
#: mutasyon beklentiyi de kaydırırdı.
OKUMA = {"admin": 200, "yonetici": 200, "muhasebe": 200, "rapor": 200, "satis": 403, "depo": 403}
YAZMA = {"admin": 200, "yonetici": 200, "muhasebe": 200, "rapor": 403, "satis": 403, "depo": 403}


@pytest.mark.parametrize("rol", ROLLER)
def test_IZIN_MATRISI(uygulama, dunya, rol) -> None:
    h = _baslik(uygulama, dunya, rol)
    for yol in ("/api/accounting/account-map", f"/api/accounting/vouchers?period={DONEM}",
                f"/api/accounting/vat-summary?period={DONEM}"):
        assert uygulama.get(yol, headers=h).status_code == OKUMA[rol], (rol, yol)
    r = uygulama.put("/api/accounting/account-map", headers=h,
                     json={"eslemeler": [{"olay": "SATIS_CARI", "hesap_kodu": "120"}]})
    assert r.status_code == YAZMA[rol], (rol, r.text)


def test_VKN_MASKESI_rapor_rolunde(uygulama, dunya) -> None:
    def cari(rol):
        r = uygulama.get(f"/api/accounting/vouchers?period={DONEM}&limit=500",
                         headers=_baslik(uygulama, dunya, rol))
        assert r.status_code == 200, r.text
        return next(f["cari"] for f in r.json()["items"]
                    if f["fis_no"] == f"SAT-{dunya['belge']['o1']}")

    assert cari("muhasebe") == {"ad": "Ahmet Çiftçi", "tax_number": "1234567890"}
    maskeli = cari("rapor")
    assert maskeli["ad"] == "Ahmet Çiftçi" and maskeli["tax_number"] != "1234567890"
    assert maskeli["tax_number"].endswith("890")


def test_ONIZLEME_sayfalama_ve_donem_422(uygulama, dunya) -> None:
    h = _baslik(uygulama, dunya, "admin")
    tum = uygulama.get(f"/api/accounting/vouchers?period={DONEM}&limit=500", headers=h).json()
    sayfa = uygulama.get(f"/api/accounting/vouchers?period={DONEM}&limit=2&offset=1",
                         headers=h).json()
    assert sayfa["total"] == tum["total"] == len(tum["items"]) == 9
    assert [f["fis_no"] for f in sayfa["items"]] == [f["fis_no"] for f in tum["items"][1:3]]
    for kotu in ("2026-13", "2026-7", "2026-07x", "abcd-ef"):
        assert uygulama.get(f"/api/accounting/vouchers?period={kotu}", headers=h).status_code == 422
        assert uygulama.get(f"/api/accounting/vat-summary?period={kotu}",
                            headers=h).status_code == 422


def test_HESAP_PLANI_bos_varsayilan_upsert_idempotent_ve_fise_yansir(uygulama, dunya) -> None:
    from sqlalchemy import text

    from app.db import SessionLocal

    h = _baslik(uygulama, dunya, "muhasebe")
    ilk = uygulama.get("/api/accounting/account-map", headers=h).json()
    assert ilk["eslemeler"] == [] and len(ilk["varsayilanlar"]) == 16
    govde = {"eslemeler": [
        {"olay": "SATIS_KDV", "kdv_orani": "20", "hesap_kodu": "391.20"},
        {"olay": "SATIS_CARI", "taraf_tipi": "CUSTOMER", "hesap_kodu": "120.01"},
    ]}

    def sayim():
        with SessionLocal() as db:
            satir = db.execute(text(
                "SELECT COUNT(*) FROM muhasebe_hesap_eslemeleri WHERE company_id=:c"),
                {"c": dunya["firma_a"]}).scalar_one()
            aktivite = db.execute(text(
                "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c "
                "AND action_type='accounting.account_map_updated'"),
                {"c": dunya["firma_a"]}).scalar_one()
        return satir, aktivite

    r1 = uygulama.put("/api/accounting/account-map", headers=h, json=govde)
    assert r1.status_code == 200, r1.text
    assert sayim() == (2, 1)
    r2 = uygulama.put("/api/accounting/account-map", headers=h, json=govde)
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert sayim() == (2, 1), "değişmeyen yazma aktivite satırı ÜRETMEZ"
    fisler = uygulama.get(f"/api/accounting/vouchers?period={DONEM}&limit=500", headers=h).json()
    fis = next(f for f in fisler["items"] if f["fis_no"] == f"SAT-{dunya['belge']['o1']}")
    assert [s["hesap_kodu"] for s in fis["satirlar"]] == ["120.01", "600", "391", "600", "391.20"]
    # Komşu firma HÂLÂ varsayılanda.
    hb = _baslik(uygulama, dunya, "muhasebe", dunya["firma_b"])
    assert uygulama.get("/api/accounting/account-map", headers=hb).json()["eslemeler"] == []


@pytest.mark.parametrize("govde", [
    {"eslemeler": [{"olay": "SATIS_CARI", "hesap_kodu": "12"}]},
    {"eslemeler": [{"olay": "SATIS_CARI", "hesap_kodu": "120.1"}]},
    {"eslemeler": [{"olay": "SATIS_CARI", "hesap_kodu": "١٢٠"}]},
    {"eslemeler": [{"olay": "YOK", "hesap_kodu": "120"}]},
    {"eslemeler": [{"olay": "SATIS_KDV", "kdv_orani": "101", "hesap_kodu": "391"}]},
    {"eslemeler": [{"olay": "SATIS_CARI", "taraf_tipi": "BANKA", "hesap_kodu": "120"}]},
    {"eslemeler": []},
    {"eslemeler": [{"olay": "SATIS_CARI", "hesap_kodu": "120"},
                   {"olay": "SATIS_CARI", "hesap_kodu": "121"}]},
])
def test_HESAP_PLANI_gecersiz_girdi_422(uygulama, dunya, govde) -> None:
    r = uygulama.put("/api/accounting/account-map", headers=_baslik(uygulama, dunya, "admin"),
                     json=govde)
    assert r.status_code == 422, r.text
