"""PostgreSQL ikizi: 1B-F hasat partisinin GERÇEK DİYALEKTTE eşi.

Göç YOKTUR (1B-F tek sütun eklemez); ölçülen şey 0067/0073'ün kısıtlarının,
PostgreSQL'in TİP davranışının ve GERÇEK EŞZAMANLILIĞIN hasat yolunda
ısırdığıdır. SQLite ikizi `tests/test_1b_f_hasat_lot.py` DAVRANIŞI ölçüyor
(kod/depo/SKT, FEFO sırası, idempotensi, kiracı, bayrak, dört mutasyon); bu
dosya yalnız GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN yarıları ölçer.

--- BU İKİZ NEDEN ZORUNLU --------------------------------------------------

1. **SKT SÜTUNU GERÇEK BİR `date`TİR VE HASAT ONU NULL BIRAKIR.** 1B-F,
   `product_lots`a OPERATÖR ELİ DEĞMEDEN satır açan İLK yoldur ve açtığı her
   satırın `expiry_date`i NULL'dur. SQLite'ta o sütun zaten metindir ve NULL
   ile boş dizgi arasındaki fark orada GÖRÜNMEZ; PostgreSQL'de sütun `date`
   tipindedir ve `''` yazma girişimi `InvalidDatetimeFormat` ile ölürdü.
   Ayrıca `_parti_tuket`in `_skt` çevirisi PostgreSQL'de `datetime.date`,
   SQLite'ta `str` görür — SKT'siz partinin FEFO'da SONA gitmesi bu yüzden
   İKİ DİYALEKTTE AYRI AYRI sorulmalıdır.

2. **MİKTAR `NUMERIC(18,4)` ÖLÇEĞİNDE.** Hasat miktarı `units.resolve`den
   geçiyor (`2.5 TON` -> `2500 KG`) ve parti defterine YAZILIYOR. Ölçek
   SQLite'ta DAYATILMAZ; bir kayan nokta artığı ya da taşma yalnız gerçek
   katalogda görünür.

3. **PARTİNİN DEPOSU KİRACI SINIRINA ŞEMADAN BAĞLI.** `product_lots`un
   bileşik yabancı anahtarı (`fk_product_lots_warehouse_same_company`, göç
   0073) SQLite'ta VARSAYILAN OLARAK UYGULANMAZ (`PRAGMA foreign_keys` temiz
   bir şemada 0 döner). Tüketici depoyu `default_warehouse`tan alıyor; o
   çağrı bir gün yanlış firmanın deposunu verse SQLite SESSİZCE kabul eder.
   Kısıtın GERÇEKTEN ısırdığı burada ölçülüyor.

4. **YARIŞ SQLite'TA OLUŞAMAZ.** SQLite yazmaları veritabanı düzeyinde seri
   hâle getirir. "İki tüketici aynı hasat olayını kapıyor" senaryosu ancak
   gerçek eşzamanlı oturumlarda kurulur ve 1B-F'nin sorusu oradadır: iki
   tüketici yarışırsa parti İKİ KEZ mi açılır? Cevap TEK SATIR olmalıdır ve
   bunu ORTAM değil KOD sağlamalıdır.

5. **TEKRAR TESLİM GERÇEK KATALOGDA.** Göç 0060'ın KISMİ benzersiz indeksi
   (olay + ürün başına en fazla bir hareket) ve `_kurtar`ın `db.rollback()`u
   birlikte çalışıyor: hareket reddedilince PARTİ ARTIŞI DA geri alınmalı.
   PostgreSQL'de reddedilen bir deyim işlemi ZEHİRLER (`InFailedSqlTransaction`)
   ve kurtarma yazımı ayrı bir yolu izler; SQLite'ta böyle bir durum YOKTUR.

--- BU İKİZ NEYİ ÖLÇMÜYOR --------------------------------------------------

KANTAR FİŞİ FARKININ PARTİYE YAZILMASI ölçülmüyor çünkü YAZILMIYOR: 1B-F'nin
kapsam sınırı SQLite ikizinde ADIYLA çivili (`test_PARTI_YOLU_YALNIZ_HASAT
_kaynaginda_KAPSAM_SINIRI`) ve o sınır bir DİYALEKT sorusu değildir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent

_URL = os.environ.get("APP_TEST_DATABASE_URL", "") or os.environ.get(
    "DATABASE_URL", ""
)

pytestmark = pytest.mark.skipif(
    not _URL.startswith("postgresql"),
    reason="1B-F ikizi: gerçek PostgreSQL URL'si gerekiyor",
)

KOMSU_ADI = "1B-F İKİZİ komşu firması"
ADMIN_PW = "HasatLotPG!123"

#: KOŞU BAŞINA BENZERSİZ SON EK — 1B-B/1B-E ikizlerinin ölçülmüş tuzağı: bu
#: dosya ADMIN firmasında çalışıyor ve o firma silinemez, yani ürünler, çiftlik
#: kodları ve parti kodları KOŞULAR ARASINDA birikiyor. Sabit bir kod ikinci
#: koşuda `uq_farms_company_code` ile ya da `scalar_one()`ı
#: `MultipleResultsFound` ile düşürürdü.
KOSU = uuid4().hex[:8]

UZAK = "2098-01-31"


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA yaz (1B-A..1B-E ikizlerinden devralındı).

    PostgreSQL ikizleri CI'da AYNI veritabanını paylaşıyor ve her biri girişten
    sonra admin şifresini KENDİ sabitine çeviriyor. Tek yönlü bir çare (yalnız
    teardown) dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi
    bozan ÖNCEKİ dosya olabilir.
    """
    from app.auth import hash_password
    from app.db import SessionLocal

    with SessionLocal() as db:
        if db.execute(text("SELECT to_regclass('public.app_users')")).scalar() is None:
            return
        db.execute(
            text(
                "UPDATE app_users SET password_hash=:h, "
                "must_change_password=true WHERE username='admin'"
            ),
            {"h": hash_password("admin123")},
        )
        db.commit()


def _komsuyu_temizle(engine) -> None:
    with engine.begin() as baglanti:
        for deyim in (
            "DELETE FROM stock_movements WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:ad)",
            "DELETE FROM product_lots WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:ad)",
            "DELETE FROM warehouse_stocks WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:ad)",
            "DELETE FROM warehouses WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:ad)",
            "DELETE FROM products WHERE company_id IN "
            "(SELECT id FROM companies WHERE name=:ad)",
            "DELETE FROM companies WHERE name=:ad",
        ):
            baglanti.execute(text(deyim), {"ad": KOMSU_ADI})


@pytest.fixture()
def motor():
    """Şema + açılış şifresi, İKİ UÇTAN; komşu kiracı temiz bırakılır."""
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_URL)
    command.upgrade(config, "head")
    _komsuyu_temizle(engine)
    _acilisa_cek()
    try:
        yield engine
    finally:
        _komsuyu_temizle(engine)
        _acilisa_cek()
        engine.dispose()


def _admin_headers(client):
    for aday in ("admin123", ADMIN_PW):
        giris = client.post(
            "/api/auth/login", json={"username": "admin", "password": aday}
        )
        if giris.status_code == 200:
            break
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    baslik = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    if aday != ADMIN_PW:
        degisti = client.post(
            "/api/auth/change-password",
            headers=baslik,
            json={"current_password": aday, "new_password": ADMIN_PW},
        )
        assert degisti.status_code == 200, degisti.text
        baslik["Authorization"] = "Bearer " + degisti.json()["access_token"]
    return baslik, int(govde["companies"][0]["id"])


class _Sahne:
    """Bir testin kiracı içi kurulumu; her test KENDİNİ kurar.

    Ürünler, çiftlikler ve sezonlar test başına YENİDEN açılıyor (ad/kod son
    ekiyle ayrılıyor) çünkü PostgreSQL ikizleri veritabanını PAYLAŞIYOR ve
    önceki bir koşudan kalan satır sessizce cevabı değiştirebilirdi.
    """

    def __init__(self, client, baslik, cid, etiket):
        self.client = client
        self.baslik = baslik
        self.cid = cid
        self.etiket = etiket
        self.depo = self.ok(client.get("/api/warehouses", headers=baslik))[0]["id"]
        self.ciftlik = self.ok(
            client.post(
                "/api/farms",
                headers=baslik,
                json={"code": f"hf{etiket}"[:20], "name": f"1B-F {etiket}"},
            )
        )["id"]
        self.tedarikci = self.ok(
            client.post(
                "/api/suppliers", headers=baslik, json={"name": f"1B-F TED {etiket}"}
            )
        )["id"]
        self.musteri = self.ok(
            client.post(
                "/api/customers", headers=baslik, json={"name": f"1B-F MUS {etiket}"}
            )
        )["id"]

    @staticmethod
    def ok(cevap):
        assert cevap.status_code < 300, (cevap.status_code, cevap.text)
        return cevap.json() if cevap.content else None

    def urun(self, ad, taban="KG"):
        return self.ok(
            self.client.post(
                "/api/products",
                headers=self.baslik,
                json={
                    "name": f"{ad} {self.etiket}",
                    "purchase_price": 10,
                    "sale_price": 20,
                    "vat_rate": 20,
                    "stock": 0,
                    "unit": "KG",
                    "base_unit": taban,
                },
            )
        )["id"]

    def sezon(self, kod, urun_id):
        parsel = self.ok(
            self.client.post(
                "/api/farm-parcels",
                headers=self.baslik,
                json={
                    "farm_id": self.ciftlik,
                    "code": f"{kod}{self.etiket}"[:20],
                    "name": f"{kod} {self.etiket}",
                    "area_decare": "100.0000",
                },
            )
        )["id"]
        return self.ok(
            self.client.post(
                "/api/crop-seasons",
                headers=self.baslik,
                json={
                    "parcel_id": parsel,
                    "season_year": 2026,
                    "crop": "Bugday",
                    "product_id": urun_id,
                    "started_on": "2026-03-01",
                    "planted_area_decare": "100.0000",
                },
            )
        )["id"]

    def hasat(self, sezon_id, miktar, birim="KG"):
        return self.ok(
            self.client.post(
                "/api/field-harvests",
                headers=self.baslik,
                json={
                    "season_id": sezon_id,
                    "harvested_on": "2026-07-10",
                    "quantity": miktar,
                    "unit": birim,
                },
            )
        )["id"]

    def tuket(self):
        from app.db import SessionLocal
        from app.field_stok_tuketici import olaylari_isle

        with SessionLocal() as db:
            sayac = olaylari_isle(db, self.cid)
            db.commit()
            return sayac

    def alis(self, urun_id, adet, kod, skt):
        return self.ok(
            self.client.post(
                "/api/purchases",
                headers=self.baslik,
                json={
                    "entity_id": self.tedarikci,
                    "transaction_date": "2026-09-08",
                    "warehouse_id": self.depo,
                    "items": [
                        {
                            "product_id": urun_id,
                            "quantity": adet,
                            "unit_price": 10,
                            "vat_rate": 20,
                            "lot_code": kod,
                            "expiry_date": skt,
                        }
                    ],
                },
            )
        )

    def satis(self, urun_id, adet):
        return self.ok(
            self.client.post(
                "/api/orders",
                headers=self.baslik,
                json={
                    "entity_id": self.musteri,
                    "transaction_date": "2026-09-09",
                    "due_date": "2026-09-30",
                    "warehouse_id": self.depo,
                    "items": [
                        {
                            "product_id": urun_id,
                            "quantity": adet,
                            "unit_price": 20,
                            "vat_rate": 20,
                        }
                    ],
                },
            )
        )

    def parti_satirlari(self, urun_id):
        """HAM KATALOGDAN: tip ölçülecekse okuma ucu (JSON) yetmez."""
        from app.db import SessionLocal

        with SessionLocal() as db:
            return db.execute(
                text(
                    "SELECT lot_code,expiry_date,quantity,warehouse_id "
                    "FROM product_lots WHERE company_id=:cid AND product_id=:pid "
                    "ORDER BY id"
                ),
                {"cid": self.cid, "pid": urun_id},
            ).mappings().all()

    def hareketler(self, tur, belge_id):
        from app.db import SessionLocal

        with SessionLocal() as db:
            return [
                (satir[0], Decimal(str(satir[1])))
                for satir in db.execute(
                    text(
                        "SELECT l.lot_code, h.quantity FROM stock_movements h "
                        "LEFT JOIN product_lots l ON l.id=h.lot_id "
                        "WHERE h.company_id=:cid AND h.reference_type=:rt "
                        "AND h.reference_id=:rid ORDER BY h.id"
                    ),
                    {"cid": self.cid, "rt": tur, "rid": belge_id},
                ).all()
            ]

    def hasat_olayi(self, hasat_id):
        from app.db import SessionLocal

        with SessionLocal() as db:
            return db.execute(
                text(
                    "SELECT id,status,attempts FROM field_integration_events "
                    "WHERE company_id=:cid AND source_type='field_harvest' "
                    "AND source_id=:sid"
                ),
                {"cid": self.cid, "sid": hasat_id},
            ).mappings().one()


def _sahne(etiket):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    baslik, cid = _admin_headers(client)
    _Sahne.ok(
        client.put(
            "/api/company-settings",
            headers=baslik,
            json={"negative_stock_policy": "allow", "credit_limit_policy": "block"},
        )
    )
    return client, _Sahne(client, baslik, cid, f"{etiket}{KOSU}")


# ------------------------------------------------------------ tip/ölçek ---

def test_HASAT_PARTISI_SKT_NULL_ve_MIKTAR_NUMERIC_18_4(motor) -> None:
    """Parti gerçek katalogda: `expiry_date` NULL, miktar TABAN birimde TAM.

    İKİ ÖLÇÜM BİR ARADA ve ayrı ayrı ölçülemezlerdi: hasat `2.5 TON` giriliyor,
    `units.resolve` onu `2500 KG`a çeviriyor ve parti defteri o sayıyı
    `NUMERIC(18,4)` sütununa yazıyor. Ölçek SQLite'ta DAYATILMAZ, `date` tipi
    de orada YOKTUR — yani iki kusur da (kayan nokta artığı, `''` yazılan SKT)
    yalnız burada görünür.
    """
    client, s = _sahne("skt")
    with client:
        urun = s.urun("Hasat PG SKT")
        sezon = s.sezon("hs", urun)
        hasat = s.hasat(sezon, "2.5", birim="TON")
        assert s.tuket()["SENT"] == 1

        satirlar = s.parti_satirlari(urun)
        assert len(satirlar) == 1, satirlar
        satir = satirlar[0]
        assert satir["lot_code"] == f"HASAT-{hasat}", satir
        # TİP DE ÖLÇÜLÜYOR, yalnız değer değil: sütun gerçekten `date`tir ve
        # hasat yolu onu NULL bırakır. `''` yazılsaydı PostgreSQL burada
        # `InvalidDatetimeFormat` ile ölürdü — SQLite'ta ise sessizce kabul.
        assert satir["expiry_date"] is None, (
            "SKT NULL DEĞİL: hasadın son kullanma tarihi YOKTUR ve `_parti_ac`a "
            f"`SKT_SORULMADI` gidiyor. Katalogdaki değer: {satir['expiry_date']!r}"
        )
        assert Decimal(str(satir["quantity"])) == Decimal("2500.0000"), (
            "TON -> KG çevirisi TABAN birimde TAM yazılmadı: "
            f"{satir['quantity']!r}"
        )
        assert int(satir["warehouse_id"]) == s.depo, satir

        olay = s.hasat_olayi(hasat)
        assert s.hareketler("field_integration_event", olay["id"]) == [
            (f"HASAT-{hasat}", Decimal("2500.0000"))
        ], s.hareketler("field_integration_event", olay["id"])


def test_SKT_SIZ_hasat_partisi_FEFO_SIRASININ_SONUNDA(motor) -> None:
    """Gerçek dialektte de NULL-SON: tarihli alış partisi ÖNCE tükenir.

    `_parti_tuket` `expiry_date`i `_skt` ile `date`e çeviriyor ve PostgreSQL o
    sütunu ZATEN `datetime.date` döndürüyor, SQLite ise `str`. İki diyalektte
    İKİ AYRI dal koşuyor, yani sıra iki yerde de sorulmalıdır: çevirinin bir
    dalı bozulsa sıra bir diyalektte takvimsel, ötekinde alfabetik olurdu ve
    ikisi SESSİZCE ayrışırdı.

    `ALIS` partisinin SKT'si 2098'dir — UZAK bir uçta, çünkü ölçülen kural
    "yakın tarih önce" DEĞİL "tarihi olan, tarihi olmayandan önce"dir.
    """
    client, s = _sahne("fefo")
    with client:
        urun = s.urun("Hasat PG FEFO")
        sezon = s.sezon("hf", urun)
        hasat = s.hasat(sezon, "1000")
        assert s.tuket()["SENT"] == 1
        s.alis(urun, 300, f"ALIS-{KOSU}", UZAK)

        # Tarih tipi ölçülüyor: alış partisi gerçekten `date` taşıyor.
        skt_ler = {r["lot_code"]: r["expiry_date"] for r in s.parti_satirlari(urun)}
        assert isinstance(skt_ler[f"ALIS-{KOSU}"], date), skt_ler
        assert skt_ler[f"HASAT-{hasat}"] is None, skt_ler

        satis = s.satis(urun, 400)
        dagitim = s.hareketler("orders", satis["id"])
        assert dagitim == [
            (f"ALIS-{KOSU}", Decimal("-300.0000")),
            (f"HASAT-{hasat}", Decimal("-100.0000")),
        ], ("SKT-SIZ PARTİ SIRAYA YANLIŞ GİRDİ", dagitim)

        kalanlar = {
            r["lot_code"]: Decimal(str(r["quantity"])) for r in s.parti_satirlari(urun)
        }
        assert kalanlar == {
            f"HASAT-{hasat}": Decimal("900.0000"),
            f"ALIS-{KOSU}": Decimal("0.0000"),
        }, kalanlar


# ---------------------------------------------------------------- kısıt ---

def test_parti_DEPOSU_KIRACI_DISINA_CIKAMAZ_bilesik_FK_ISIRIYOR(motor) -> None:
    """`fk_product_lots_warehouse_same_company` GERÇEKTEN reddediyor.

    Tüketici depoyu `inventory.default_warehouse`tan alıyor ve o çağrı olayın
    firmasıyla yapılıyor. Bu kapı UYGULAMAYI değil ŞEMAYI ölçüyor: çağrı bir
    gün komşunun deposunu verse SQLite SESSİZCE kabul ederdi (`PRAGMA
    foreign_keys` temiz bir şemada 0 döner), yani savunmanın şema yarısı orada
    hiç sınanmaz. Prob hasadın açtığı GERÇEK partiyi komşunun deposuna taşımayı
    deniyor — reddin kısıt ADIYLA gelmesi ayrıca ölçülüyor.
    """
    from sqlalchemy.exc import IntegrityError

    client, s = _sahne("fk")
    with client:
        urun = s.urun("Hasat PG FK")
        sezon = s.sezon("hk", urun)
        hasat = s.hasat(sezon, "100")
        assert s.tuket()["SENT"] == 1

    with motor.begin() as baglanti:
        komsu = baglanti.execute(
            text(
                "INSERT INTO companies (name,is_active,negative_stock_policy,"
                "credit_limit_policy,farm_area_override_policy,"
                "farm_early_harvest_policy,farm_spraying_dose_required,"
                "service_parts_mode,created_at) VALUES (:ad,true,'BLOCK',"
                "'BLOCK','BLOCK','BLOCK',false,'IMMEDIATE',now()) RETURNING id"
            ),
            {"ad": KOMSU_ADI},
        ).scalar_one()
        komsu_depo = baglanti.execute(
            text(
                "INSERT INTO warehouses (company_id,name,is_default,is_active,"
                "warehouse_type) VALUES (:c,'1B-F Komşu Depo',true,true,'FIXED') "
                "RETURNING id"
            ),
            {"c": komsu},
        ).scalar_one()

    with pytest.raises(IntegrityError) as hata:
        with motor.begin() as baglanti:
            baglanti.execute(
                text(
                    "UPDATE product_lots SET warehouse_id=:w "
                    "WHERE company_id=:c AND product_id=:p AND lot_code=:kod"
                ),
                {"w": komsu_depo, "c": s.cid, "p": urun, "kod": f"HASAT-{hasat}"},
            )
    assert "fk_product_lots_warehouse_same_company" in str(hata.value), str(hata.value)


# --------------------------------------------------------- tekrar teslim ---

def test_TEKRAR_TESLIM_ETKI_KISITINA_carpiyor_ve_PARTI_ARTISI_GERI_ALINIYOR(
    motor,
) -> None:
    """Olay ELLE `PENDING`e çekilse bile parti BÜYÜMEZ, hareket ÇOĞALMAZ.

    ÜÇ MEKANİZMA BİRLİKTE ÖLÇÜLÜYOR ve ancak gerçek katalogda birlikte
    koşuyorlar: göç 0060'ın KISMİ benzersiz indeksi hareketi reddeder,
    reddedilen deyim PostgreSQL'de işlemi ZEHİRLER
    (`InFailedSqlTransactionError`), `_kurtar` `db.rollback()` yapıp
    `_denemeyi_kaydet`i AYRI bir işlemde yazar. Rollback olmasaydı parti artışı
    (defter `_parti_ac`la ÖNCE büyütülüyor) commit edilmiş olurdu ve defter
    hareketsiz büyürdü — stokla ayrışan, hiçbir yerde kırmızı vermeyen bir
    sapma. SQLite'ta zehirlenmiş işlem diye bir durum YOKTUR, yani bu bileşim
    orada hiç kurulmaz.
    """
    from app.db import SessionLocal

    client, s = _sahne("tekrar")
    with client:
        urun = s.urun("Hasat PG Tekrar")
        sezon = s.sezon("ht", urun)
        hasat = s.hasat(sezon, "1000")
        assert s.tuket()["SENT"] == 1
        olay = s.hasat_olayi(hasat)
        onceki = s.parti_satirlari(urun)
        assert len(onceki) == 1, onceki

        # UÇ REDDEDER (#55): `SENT` yeniden kuyruklanamaz.
        cevap = client.post(
            f"/api/field-integration-events/{olay['id']}/requeue", headers=s.baslik
        )
        assert cevap.status_code == 409, (cevap.status_code, cevap.text)
        assert cevap.json()["detail"]["code"] == "EVENT_ALREADY_SENT", cevap.text

        # UÇ ATLANSA BİLE: at-least-once bir kuyrukta asıl soru budur.
        with SessionLocal() as db:
            db.execute(
                text(
                    "UPDATE field_integration_events SET status='PENDING' "
                    "WHERE company_id=:c AND id=:i"
                ),
                {"c": s.cid, "i": int(olay["id"])},
            )
            db.commit()
        sayac = s.tuket()
        assert sayac["SENT"] == 0, ("ikinci hareket YAZILDI", sayac)

        sonraki = s.parti_satirlari(urun)
        assert len(sonraki) == 1, ("İKİNCİ PARTİ SATIRI AÇILDI", sonraki)
        assert Decimal(str(sonraki[0]["quantity"])) == Decimal(
            str(onceki[0]["quantity"])
        ), ("TEKRAR TESLİM PARTİYİ BÜYÜTTÜ", onceki[0], sonraki[0])

        with SessionLocal() as db:
            hareket_sayisi = db.execute(
                text(
                    "SELECT COUNT(*) FROM stock_movements WHERE company_id=:c "
                    "AND reference_type='field_integration_event' "
                    "AND product_id=:p"
                ),
                {"c": s.cid, "p": urun},
            ).scalar_one()
        assert hareket_sayisi == 1, hareket_sayisi


# ----------------------------------------------------------------- yarış ---

_TUKETICI = r'''
import os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle

hedef = float(os.environ["BASLA"])
while time.time() < hedef:
    time.sleep(0.001)
with SessionLocal() as db:
    print("SAYAC %r" % (olaylari_isle(db, int(os.environ["FIRMA"])),))
'''


def test_IKI_TUKETICI_yarisirken_TEK_PARTI_aciliyor(motor) -> None:
    """İki AYRI SÜREÇ, tek PENDING hasat olayı: tek parti satırı, tek hareket.

    SQLite'ta bu yarış OLUŞAMAZ (yazmalar veritabanı düzeyinde seri) ve orada
    alınan yeşil KODUN değil ORTAMIN özelliğidir. Burada ölçülen şey 1B-F'ye
    ÖZGÜDÜR: talep yarışını KAYBEDEN tüketici hiçbir hareket yazmaz, ama parti
    defterine YAZAN ilk adım (`_parti_ac`) hareketten ÖNCEDİR. Talep koruması
    gevşeseydi kaybeden partiyi İKİNCİ KEZ büyütür, sonra hareketi kısıta
    çarpardı; hareketi geri alınır, PARTİ ARTIŞI kalırdı — stokla ayrışan ve
    hiçbir kırmızı üretmeyen bir sapma. Yani hareket sayısı TEK BAŞINA yetmez
    ve parti satırının MİKTARI ayrıca sorulur.
    """
    import time

    client, s = _sahne("yaris")
    with client:
        urun = s.urun("Hasat PG Yarış")
        sezon = s.sezon("hy", urun)
        hasat = s.hasat(sezon, "1000")

    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": _URL,
            "APP_TEST_DATABASE_URL": _URL,
            "BACKEND": str(BACKEND),
            "PYTHONPATH": str(BACKEND),
            "PYTHONIOENCODING": "utf-8",
            "FIRMA": str(s.cid),
            "BASLA": str(time.time() + 3.0),
        }
    )
    surecler = [
        subprocess.Popen(
            [sys.executable, "-c", _TUKETICI],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for _ in range(2)
    ]
    ciktilar = []
    for surec in surecler:
        cikti, hata = surec.communicate(timeout=600)
        assert surec.returncode == 0, cikti + "\n" + hata
        ciktilar.append(cikti.strip())

    kazanan = [c for c in ciktilar if "'SENT': 1" in c]
    assert len(kazanan) == 1, (
        f"tam olarak BİR kazanan bekleniyordu: {ciktilar!r}"
    )

    with motor.begin() as baglanti:
        satirlar = baglanti.execute(
            text(
                "SELECT lot_code,quantity FROM product_lots "
                "WHERE company_id=:c AND product_id=:p"
            ),
            {"c": s.cid, "p": urun},
        ).mappings().all()
        hareket_sayisi = baglanti.execute(
            text(
                "SELECT COUNT(*) FROM stock_movements WHERE company_id=:c "
                "AND product_id=:p AND reference_type='field_integration_event'"
            ),
            {"c": s.cid, "p": urun},
        ).scalar_one()

    assert len(satirlar) == 1, ("YARIŞ İKİ PARTİ SATIRI AÇTI", satirlar, ciktilar)
    assert satirlar[0]["lot_code"] == f"HASAT-{hasat}", satirlar[0]
    assert Decimal(str(satirlar[0]["quantity"])) == Decimal("1000.0000"), (
        "PARTİ İKİ KEZ BÜYÜTÜLDÜ: talep yarışını kaybeden tüketici defteri "
        f"oynatmış olabilir. Satır: {satirlar[0]!r} süreçler: {ciktilar!r}"
    )
    assert hareket_sayisi == 1, (hareket_sayisi, ciktilar)
