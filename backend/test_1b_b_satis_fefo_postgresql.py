"""PostgreSQL ikizi: 1B-B satış/FEFO tüketiminin GERÇEK DİYALEKTTE eşi.

Göç YOKTUR (1B-B tek sütun eklemez); ölçülen şey 0067'nin kısıtlarının ve
PostgreSQL'in sayı/tarih/kilit davranışının TÜKETİM yolunda gerçekten
ısırdığıdır. SQLite ikizi `tests/test_1b_b_satis_fefo.py` DAVRANIŞI ölçüyor
(FEFO sırası, bölüştürme, 409/422, geri alma); bu dosya yalnız GELİŞTİRME
DİYALEKTİNDE GÖRÜNMEYEN yarıları ölçer.

--- BU İKİZ NEDEN ZORUNLU --------------------------------------------------

1. **YARIŞ SQLite'TA ÜRETİLEMEZ.** SQLite TEK YAZARDIR: iki isteği aynı ana
   getiremezsiniz, yani kusur da düzeltmesi de geliştirme diyalektinde
   GÖRÜNMEZ — koruma kaldırılsa SQLite süiti YEŞİL KALIR.

   KORUYAN ŞEYİN NE OLDUĞU ÖLÇÜLDÜ VE İLK VARSAYIM YANLIŞ ÇIKTI: ortada bir
   `FOR UPDATE` YOKTUR (`app/inventory.py`de `with_for_update` geçmiyor).
   Koruma KORUMALI ATOMİK UPDATE'tir — koşul yazmanın kendi `WHERE`ünde
   durur ve satır dönmezse çağıran reddeder. Bu ayrım tembel bir ayrıntı
   değil: iki AYRI durum üretiyor ve ikisi de aşağıda AYRI AYRI ölçülüyor.

     * Negatif stok BLOKLU: `warehouse_stocks` UPDATE'i (`quantity + delta
       >= 0`) ikinci isteği zaten reddeder ve parti katmanına SIRA GELMEZ.
       PostgreSQL o satırda satır kilidi tuttuğu için ikinci işlem BEKLER,
       sonra İŞLENMİŞ değer üzerinde yeniden değerlendirir.
     * Negatif stok SERBEST: o koruma KAPALIDIR ve ikinci istek parti
       katmanına ULAŞIR. Orada tek koruma `_parti_tuket`in `quantity>=:pay`
       yüklemidir; olmasaydı iki istek AYNI partiyi iki kez düşerdi.

2. **`CHECK (quantity >= 0 AND quantity <> 'NaN'::numeric)` GERÇEKTEN
   ISIRIR.** Tüketim yolunun `quantity=quantity-:pay` yazması eksiye
   düşemez; SQLite'ta bu kısıt uygulanmaz (0067'nin NaN yarısı zaten YALNIZ
   PostgreSQL'dedir). Yani "eksiye düşürmüyorum" cümlesi ancak burada
   sınanabilir.

3. **`NUMERIC(18,4)` ÖLÇEĞİ.** Bölüştürülen paylar (`Secim.dagitim`) parti
   satırına ve hareket satırına yazılıyor. Yanlış ölçekli bir pay SQLite'ta
   SESSİZCE geçer; toplamın istenene TAM eşit olduğu iddiası ancak gerçek
   `NUMERIC` üzerinde bir kanıttır.

4. **TARİH VE DAMGA TİPLERİ AYRIŞIYOR.** `expiry_date` burada `date`,
   SQLite'ta `str`; `created_at` burada tz-AWARE `timestamptz`, SQLite'ta
   naive dizgi. FEFO'nun BİRİNCİ ve ÜÇÜNCÜ anahtarları tam bu iki sütundur
   (`parti_defteri._skt`, `._damga`). Çevrim yanlışsa sıra bir diyalektte
   takvimsel, ötekinde alfabetik olur ve ikisi SESSİZCE ayrışır — her biri
   kendi ikizinde yeşil kalarak.

5. **KİRACI YÜKLEMİ `_parti_tuket`in SORGUSUNDADIR**, seçicide değil
   (`app/parti.py`: `Parti`nin `company_id` taşımaması bir karardır).
   Yüklemin gerçekten ısırdığı ancak İKİ KİRACILI gerçek bir şemada
   sorulabilir.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from time import sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

KOMSU_ADI = "1B-B İKİZİ komşu firması"
ADMIN_PW = "SatisFefoPG!123"
YARIS_TURU = 20

#: KOŞU BAŞINA BENZERSİZ SON EK — kolaylık değil, ÖLÇÜLMÜŞ bir tuzağın çaresi.
#:
#: Bu dosya ADMIN firmasında çalışıyor ve o firma silinemez, yani ürünler ile
#: parti kodları KOŞULAR ARASINDA birikiyor. Sabit bir kod (`PG-Y0`) ikinci
#: koşuda `scalar_one()`ı `MultipleResultsFound` ile düşürüyordu — ÖLÇÜLDÜ.
#: Sabit kalsaydı kapı CI'da ilk koşuda yeşil, ikincisinde kırmızı olurdu ve
#: kırmızılığı kusuru DEĞİL koşu sırasını gösterirdi.
KOSU = uuid4().hex[:8]

GECMIS = "2020-01-01"
YAKIN = "2098-01-31"
UZAK = "2099-01-31"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("1B-B ikizi APP_TEST_DATABASE_URL ister")
    return url


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI ve gerekçesi ölçülmüş bir tuzaktır:
    PostgreSQL ikizleri CI'da AYNI veritabanını paylaşıyor ve her biri
    girişten sonra admin şifresini KENDİ sabitine çeviriyor. Tek yönlü bir
    çare (yalnız teardown) dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz,
    çünkü şifreyi bozan ÖNCEKİ dosya olabilir.
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
    engine = create_engine(_url())
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
    """Bir testin ihtiyaç duyduğu kiracı içi kurulum; her test KENDİNİ kurar.

    Ürünler ve partiler test başına YENİDEN açılıyor (ad son ekiyle ayrılıyor)
    çünkü PostgreSQL ikizleri veritabanını PAYLAŞIYOR ve önceki bir koşudan
    kalan satır sessizce cevabı değiştirebilirdi.
    """

    def __init__(self, client, baslik, cid, etiket):
        self.client = client
        self.baslik = baslik
        self.cid = cid
        self.etiket = etiket
        self.depo = self.ok(client.get("/api/warehouses", headers=baslik))[0]["id"]
        self.tedarikci = self.ok(
            client.post(
                "/api/suppliers", headers=baslik, json={"name": f"1B-B TED {etiket}"}
            )
        )["id"]
        self.musteri = self.ok(
            client.post(
                "/api/customers", headers=baslik, json={"name": f"1B-B MUS {etiket}"}
            )
        )["id"]

    @staticmethod
    def ok(cevap):
        assert cevap.status_code < 300, (cevap.status_code, cevap.text)
        return cevap.json() if cevap.content else None

    def urun(self, ad):
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
                    "unit": "Adet",
                },
            )
        )["id"]

    def kalem(self, pid, adet, kod=None, skt=None, fiyat=10):
        satir = {
            "product_id": pid,
            "quantity": adet,
            "unit_price": fiyat,
            "vat_rate": 20,
        }
        if kod is not None:
            satir["lot_code"] = kod
        if skt is not None:
            satir["expiry_date"] = skt
        return satir

    def alis(self, kalemler, depo=None):
        return self.ok(
            self.client.post(
                "/api/purchases",
                headers=self.baslik,
                json={
                    "entity_id": self.tedarikci,
                    "transaction_date": "2026-09-08",
                    "warehouse_id": depo or self.depo,
                    "items": kalemler,
                },
            )
        )

    def satis_istegi(self, kalemler, depo=None, **fazla):
        govde = {
            "entity_id": self.musteri,
            "transaction_date": "2026-09-09",
            "due_date": "2026-09-30",
            "warehouse_id": depo or self.depo,
            "items": kalemler,
        }
        govde.update(fazla)
        return self.client.post("/api/orders", headers=self.baslik, json=govde)

    def satis(self, kalemler, depo=None, **fazla):
        return self.ok(self.satis_istegi(kalemler, depo, **fazla))

    def partiler(self, pid):
        satirlar = self.ok(
            self.client.get(f"/api/products/{pid}/lots", headers=self.baslik)
        )["lots"]
        return {s["lot_code"]: Decimal(str(s["quantity"])) for s in satirlar}

    def hareketler(self, tur, belge_id):
        from app.db import SessionLocal

        with SessionLocal() as db:
            return [
                (satir[0], Decimal(str(satir[1])), satir[2])
                for satir in db.execute(
                    text(
                        "SELECT l.lot_code, h.quantity, h.note FROM stock_movements h "
                        "LEFT JOIN product_lots l ON l.id=h.lot_id "
                        "WHERE h.company_id=:cid AND h.reference_type=:rt "
                        "AND h.reference_id=:rid ORDER BY h.id"
                    ),
                    {"cid": self.cid, "rt": tur, "rid": belge_id},
                ).all()
            ]


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
    return client, _Sahne(client, baslik, cid, f"{etiket}-{KOSU}")


# --------------------------------------------------------------- sıra ---

def test_FEFO_sirasi_GERCEK_tarih_ve_GERCEK_damga_uzerinde(motor) -> None:
    """SKT önce, NULL en sona, eşitlikte `created_at` — hepsi native tiplerle.

    Bu iddia SQLite ikizinde de var ve BURADA TEKRARLANMASI kasıtlıdır:
    orada `expiry_date` bir DİZGİ, `created_at` naive bir dizgidir; burada
    ikisi de native (`date`, `timestamptz`). `parti_defteri._skt`/`._damga`
    çevrimlerinden biri bozulursa iddia YALNIZ BİR diyalektte kırılır — ve
    ötekinde yeşil kalması, kusuru GİZLERDİ.
    """
    client, s = _sahne("sira")
    with client:
        urun = s.urun("FEFO PG Sıra")
        s.alis([s.kalem(urun, 5, "PG-UZAK", UZAK)])
        s.alis([s.kalem(urun, 3, "PG-YAKIN", YAKIN)])
        s.alis([s.kalem(urun, 4, "PG-SKTSIZ")])

        belge = s.satis([s.kalem(urun, 9, fiyat=20)])
        assert [(k, m) for k, m, _ in s.hareketler("orders", belge["id"])] == [
            ("PG-YAKIN", Decimal("-3")),
            ("PG-UZAK", Decimal("-5")),
            ("PG-SKTSIZ", Decimal("-1")),
        ], s.hareketler("orders", belge["id"])
        # TOPLAM İSTENENE TAM EŞİT — `NUMERIC(18,4)` üzerinde, yuvarlama
        # farkı YOK (`app/parti.py`: seçici çarpmaz, yalnız bölüştürür).
        assert sum(m for _, m, _ in s.hareketler("orders", belge["id"])) == Decimal("-9")
        assert s.partiler(urun) == {
            "PG-UZAK": Decimal("0"),
            "PG-YAKIN": Decimal("0"),
            "PG-SKTSIZ": Decimal("3"),
        }


def test_ONDALIK_paylar_NUMERIC_18_4_olceginde_TAM_topluyor(motor) -> None:
    """Ölçek gerçek `NUMERIC`te sınanıyor: 2.5 + 0.7500 = 3.25, artık YOK.

    SQLite `NUMERIC` ölçeğini DAYATMAZ; yanlış ölçekli bir pay orada sessizce
    geçerdi ve parti defteri ile hareket defteri dördüncü basamakta ayrışırdı.
    """
    client, s = _sahne("ondalik")
    with client:
        urun = s.urun("FEFO PG Ondalık")
        s.alis([s.kalem(urun, "2.5000", "PG-O1", YAKIN)])
        s.alis([s.kalem(urun, "1.2500", "PG-O2", UZAK)])
        belge = s.satis([s.kalem(urun, "3.2500", fiyat=20)])
        satirlar = s.hareketler("orders", belge["id"])
        assert [(k, m) for k, m, _ in satirlar] == [
            ("PG-O1", Decimal("-2.5000")),
            ("PG-O2", Decimal("-0.7500")),
        ], satirlar
        assert sum(m for _, m, _ in satirlar) == Decimal("-3.2500")
        assert s.partiler(urun) == {
            "PG-O1": Decimal("0"),
            "PG-O2": Decimal("0.5000"),
        }


# ------------------------------------------------------------- redler ---

def test_YETERSIZ_409_ve_SURESI_GECMIS_422_gercek_semada(motor) -> None:
    """İki red, iki çare — ve ikisi de DEFTERİ OYNATMADAN duruyor."""
    client, s = _sahne("red")
    with client:
        az = s.urun("FEFO PG Az")
        s.alis([s.kalem(az, 2, "PG-AZ", UZAK)])
        reddedildi = s.satis_istegi([s.kalem(az, 5, fiyat=20)])
        assert reddedildi.status_code == 409, reddedildi.text
        ayrinti = reddedildi.json()["detail"]
        assert ayrinti["code"] == "PARTI_YETERSIZ", ayrinti
        assert Decimal(ayrinti["eksik"]) == Decimal("3"), ayrinti
        assert s.partiler(az) == {"PG-AZ": Decimal("2")}

        bozuk = s.urun("FEFO PG Bozuk")
        s.alis([s.kalem(bozuk, 4, "PG-BOZUK", GECMIS)])
        reddedildi = s.satis_istegi([s.kalem(bozuk, 1, fiyat=20)])
        assert reddedildi.status_code == 422, reddedildi.text
        ayrinti = reddedildi.json()["detail"]
        assert ayrinti["code"] == "PARTI_SURESI_GECMIS", ayrinti
        # SKT `date` olarak geri geldi ve ISO'ya ÇEVRİLDİ; ham `date` JSON'a
        # düşemezdi ve çevrim yapılmasaydı yanıt 500 olurdu.
        assert ayrinti["suresi_gecmis"][0]["expiry_date"] == GECMIS, ayrinti
        assert s.partiler(bozuk) == {"PG-BOZUK": Decimal("4")}

        # AÇIK İZİNLE geçer ve hareket notu bunu SÖYLER.
        belge = s.satis([s.kalem(bozuk, 1, fiyat=20)], allow_expired_lots=True)
        satirlar = s.hareketler("orders", belge["id"])
        assert "SURESI GECMIS PARTI" in satirlar[0][2], satirlar
        assert s.partiler(bozuk) == {"PG-BOZUK": Decimal("3")}


def test_PARTISIZ_urun_bugunku_davranisini_koruyor(motor) -> None:
    """Parti satırı OLMAYAN ürün: TEK hareket, `lot_id` NULL, red YOK."""
    client, s = _sahne("partisiz")
    with client:
        urun = s.urun("FEFO PG Partisiz")
        s.alis([s.kalem(urun, 9)])
        belge = s.satis([s.kalem(urun, 4, fiyat=20)])
        assert s.hareketler("orders", belge["id"]) == [
            (None, Decimal("-4"), f"sale #{belge['id']}")
        ], s.hareketler("orders", belge["id"])
        assert s.partiler(urun) == {}


# -------------------------------------------------------- geri alma ---

def test_GUNCELLEME_ve_SILME_partileri_TAM_geri_veriyor(motor) -> None:
    """Geri alma HAREKETİN İŞARETİNDEN geliyor; `NUMERIC` üzerinde TAM."""
    client, s = _sahne("gerial")
    with client:
        urun = s.urun("FEFO PG Geri")
        s.alis([s.kalem(urun, 3, "PG-G1", YAKIN)])
        s.alis([s.kalem(urun, 3, "PG-G2", UZAK)])
        belge = s.satis([s.kalem(urun, 5, fiyat=20)])
        assert s.partiler(urun) == {"PG-G1": Decimal("0"), "PG-G2": Decimal("1")}

        s.ok(
            s.client.put(
                f"/api/orders/{belge['id']}",
                headers=s.baslik,
                json={
                    "entity_id": s.musteri,
                    "transaction_date": "2026-09-09",
                    "due_date": "2026-09-30",
                    "warehouse_id": s.depo,
                    "items": [s.kalem(urun, 2, fiyat=20)],
                },
            )
        )
        assert s.partiler(urun) == {"PG-G1": Decimal("1"), "PG-G2": Decimal("3")}

        assert (
            s.client.delete(f"/api/orders/{belge['id']}", headers=s.baslik).status_code
            == 204
        )
        assert s.partiler(urun) == {"PG-G1": Decimal("3"), "PG-G2": Decimal("3")}


# ------------------------------------------------------------ kısıt ---

def test_PARTI_EKSIYE_DE_NaN_e_DE_dusemiyor_kisit_ISIRIYOR(motor) -> None:
    """0067'nin `CHECK`i gerçekten reddediyor — tüketimin son savunması.

    `_parti_tuket` payı `fefo_sec`ten alıyor ve `quantity>=:pay` yüklemiyle
    yazıyor; ikisi de düşse defterin kendisi durur. O "durur" cümlesi
    SQLite'ta SINANAMAZ (kısıt orada dayatılmıyor) ve NaN yarısı zaten YALNIZ
    PostgreSQL'de var: PostgreSQL `NaN`ı her sonlu sayının ÜSTÜNE sıralar,
    yani NaN'lı bir parti FEFO'da en sona düşer ve "yetiyor" der.
    """
    client, s = _sahne("kisit")
    with client:
        from app.db import SessionLocal

        urun = s.urun("FEFO PG Kısıt")
        s.alis([s.kalem(urun, 2, "PG-K", UZAK)])
        with SessionLocal() as db:
            lot_id = db.execute(
                text(
                    "SELECT id FROM product_lots WHERE company_id=:cid "
                    "AND product_id=:pid"
                ),
                {"cid": s.cid, "pid": urun},
            ).scalar_one()

        for deger in ("-1", "NaN"):
            with SessionLocal() as db:
                with pytest.raises(IntegrityError):
                    db.execute(
                        text(
                            "UPDATE product_lots SET quantity=CAST(:q AS numeric) "
                            "WHERE company_id=:cid AND id=:id"
                        ),
                        {"q": deger, "cid": s.cid, "id": lot_id},
                    )
                    db.flush()
                db.rollback()


# ----------------------------------------------------------- kiracı ---

def test_KIRACI_YUKLEMI_komsunun_partisini_GORMUYOR(motor) -> None:
    """`_parti_tuket`in `company_id=:cid` yüklemi GERÇEKTEN ısırıyor.

    Yüklem `app/parti.py`de DEĞİL, çağıranın sorgusundadır (`Parti` bilerek
    `company_id` taşımaz) ve tek bir yerde durması karardır. Tek yer demek,
    o yerin ölçülmesi ZORUNLU demektir: düşerse komşunun malı bizim
    depomuzdan çıkmış görünür ve hiçbir kısıt bunu engellemez — parti
    okuması bir SELECT'tir, yabancı anahtar orada devreye girmez.

    Kapı `_parti_tuket`i DOĞRUDAN çağırıyor, uçtan DEĞİL: uçtan iki kiracıyı
    aynı anda konuşturmak ikinci bir kullanıcı/oturum kurulumu isterdi ve
    ölçülen şey yine bu tek yüklem olurdu.
    """
    client, s = _sahne("kiraci")
    with client:
        from app.db import SessionLocal
        from app.parti_defteri import _parti_tuket

        komsu_cid = None
        with SessionLocal() as db:
            komsu_cid = db.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at) "
                    "VALUES(:ad,true,:now) RETURNING id"
                ),
                {"ad": KOMSU_ADI, "now": datetime.now(timezone.utc)},
            ).scalar_one()
            komsu_depo = db.execute(
                text(
                    "INSERT INTO warehouses(company_id,name,code,is_active,"
                    "is_default) VALUES(:cid,'Komşu Depo','KMS',true,false) "
                    "RETURNING id"
                ),
                {"cid": komsu_cid},
            ).scalar_one()
            komsu_urun = db.execute(
                text(
                    "INSERT INTO products(company_id,name,sale_price,"
                    "purchase_price,vat_rate,stock,unit) "
                    "VALUES(:cid,'Komşu Ürün',20,10,20,0,'Adet') RETURNING id"
                ),
                {"cid": komsu_cid},
            ).scalar_one()
            db.execute(
                text(
                    "INSERT INTO product_lots(company_id,product_id,lot_code,"
                    "expiry_date,quantity,warehouse_id,created_at) "
                    "VALUES(:cid,:pid,'KMS-LOT',CAST(:skt AS date),50,:wid,:now)"
                ),
                {
                    "cid": komsu_cid,
                    "pid": komsu_urun,
                    "skt": UZAK,
                    "wid": komsu_depo,
                    "now": datetime.now(timezone.utc),
                },
            )
            db.commit()

        # BİZİM kiracımız komşunun ürün+deposunu SORSA BİLE hiçbir parti
        # göremez: boş `Tuketim`, yani "parti yok" — komşunun 50 birimi DEĞİL.
        with SessionLocal() as db:
            bizim = _parti_tuket(
                db,
                s.cid,
                product_id=int(komsu_urun),
                warehouse_id=int(komsu_depo),
                miktar=Decimal("1"),
                bugun=datetime.now(timezone.utc).date(),
            )
            db.rollback()
        assert bizim.dagitim == (), (
            f"KİRACI YÜKLEMİ DÜŞMÜŞ: komşunun partisi bize göründü ({bizim})"
        )

        # SAHTE YEŞİL KARŞITI: aynı çağrı KOMŞUNUN kiracısıyla parti BULUYOR.
        # Bulmasaydı yukarıdaki boşluk yüklemin değil kurulumun kanıtı olurdu.
        with SessionLocal() as db:
            onun = _parti_tuket(
                db,
                int(komsu_cid),
                product_id=int(komsu_urun),
                warehouse_id=int(komsu_depo),
                miktar=Decimal("1"),
                bugun=datetime.now(timezone.utc).date(),
            )
            db.rollback()
        assert len(onun.dagitim) == 1 and onun.dagitim[0][1] == Decimal("1"), onun


# ---------------------------------------------------------- eşzamanlı ---

def test_ESZAMANLI_iki_satis_BLOKLU_stokta_partiyi_IKI_KEZ_dusmuyor(motor) -> None:
    """BİRİNCİ DURUM: stok koruması AÇIK — parti katmanına sıra GELMİYOR.

    ÖLÇÜLEN İDDİA: `_parti_tuket` KENDİ kilidini ALMIYOR ve bu durumda buna
    gerek de yok. Çağıran onu `adjust_warehouse_stock`tan SONRA çağırıyor;
    o da `quantity + delta >= 0` koşulunu KENDİ `WHERE`ünde taşıyan atomik
    bir UPDATE yapıyor (`FOR UPDATE` DEĞİL — ölçüldü). Eşzamanlı ikinci
    istek o satırda BEKLER, sonra İŞLENMİŞ değeri görür ve reddedilir.

    Kurulum bunu ölçülebilir yapıyor: partide TAM 1 birim var, iki istek
    1'er birim istiyor ve negatif stok BLOKLU — doğru cevap [201, 409]'dur.
    Koruma kalkarsa ikisi de yazar ve parti EKSİYE düşerdi.

    SQLite'ta bu ÜRETİLEMEZ (tek yazar); yani kusur da düzeltmesi de yalnız
    burada görünür. `YARIS_TURU` tur koşuyor: tek turluk bir yeşil, yarışın
    hiç tetiklenmediği anlamına da gelebilirdi.
    """
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app

    client, s = _sahne("yaris")
    with client:
        # NEGATİF STOK BLOKLU: ikinci isteğin reddi ÖLÇÜLEBİLİR olsun.
        s.ok(
            s.client.put(
                "/api/company-settings",
                headers=s.baslik,
                json={
                    "negative_stock_policy": "block",
                    "credit_limit_policy": "block",
                },
            )
        )
        urun = s.urun("FEFO PG Yarış")

        for tur in range(YARIS_TURU):
            s.alis([s.kalem(urun, 1, f"PG-Y{tur}", UZAK)])
            engel = Barrier(2)

            def bir_satis() -> int:
                with TestClient(app) as esli:
                    engel.wait(timeout=30)
                    return esli.post(
                        "/api/orders",
                        headers=s.baslik,
                        json={
                            "entity_id": s.musteri,
                            "transaction_date": "2026-09-09",
                            "due_date": "2026-09-30",
                            "warehouse_id": s.depo,
                            "items": [s.kalem(urun, 1, fiyat=20)],
                        },
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as havuz:
                durumlar = sorted(
                    is_.result(timeout=90)
                    for is_ in (havuz.submit(bir_satis), havuz.submit(bir_satis))
                )
            assert durumlar == [201, 409], (tur, durumlar)

            # DEFTERİN KENDİSİ: parti TAM sıfırda ve o partiyi taşıyan
            # hareket TEK. İki hareket olsaydı durum kodları yine [200, 409]
            # görünebilirdi (ikinci istek başka bir sebeple düşmüş olurdu) —
            # bu yüzden defter ayrıca sorulur.
            with SessionLocal() as db:
                kalan, tasiyan = db.execute(
                    text(
                        "SELECT l.quantity, "
                        "(SELECT count(*) FROM stock_movements h "
                        " WHERE h.company_id=l.company_id AND h.lot_id=l.id) "
                        "FROM product_lots l WHERE l.company_id=:cid "
                        "AND l.product_id=:pid AND l.lot_code=:kod"
                    ),
                    {"cid": s.cid, "pid": urun, "kod": f"PG-Y{tur}"},
                ).one()
            assert Decimal(str(kalan)) == Decimal("0"), (tur, kalan)
            # 1 alış hareketi + 1 satış hareketi = 2. Üçüncü bir satır, aynı
            # partinin İKİ KEZ tüketildiğinin kanıtı olurdu.
            assert int(tasiyan) == 2, (tur, tasiyan)


def test_ESZAMANLI_iki_satis_SERBEST_stokta_partiyi_IKI_KEZ_dusmuyor(motor) -> None:
    """İKİNCİ DURUM: stok koruması KAPALI — parti YİNE iki kez düşmüyor.

    Yukarıdaki kardeşi negatif stok BLOKLU iken koşuyor ve orada ikinci istek
    parti katmanına HİÇ ulaşmıyor. Burada politika SERBEST: ikisi de stok
    katmanını geçiyor, yani parti defteri KENDİ BAŞINA doğru kalmak zorunda.

    ÖLÇÜLEN CEVAP [201, 201]'DİR VE BU BİR KUSUR DEĞİL, KAPSAM SINIRIDIR.
    Kazanan istek partiyi 1'den 0'a düşürüyor; kaybeden istek partileri
    TÜKENMİŞ buluyor ve `defter_bosaldi` dalına giriyor — yani satış geçiyor
    (politika SERBEST diyor) ama hiçbir partiden düşmüyor. Bu noktada stok
    (-1) ile parti defteri (0) AYRIŞIR.

    AYRIŞMA SESSİZ DEĞİL: kaybeden hareketin `lot_id`si NULL ve NOTU
    `DEFTER_BOSALDI_DAMGASI` taşıyor. Kapı damgayı ADIYLA soruyor, çünkü
    damgasız bir [201, 201] ile İKİ KEZ DÜŞÜLMÜŞ bir [201, 201] dışarıdan
    AYNI görünürdü.

    ASIL ÇİVİ DEFTERDEDİR: parti TAM 0 (eksiye İNMEDİ) ve o partiyi taşıyan
    hareket sayısı 2 (1 alış + 1 satış). Üçüncü bir satır ya da negatif bir
    miktar, aynı partinin iki kez tüketildiğinin kanıtı olurdu.
    """
    from fastapi.testclient import TestClient

    from app.db import SessionLocal
    from app.main import app
    from app.parti_defteri import DEFTER_BOSALDI_DAMGASI

    client, s = _sahne("yarisserbest")
    with client:
        # NEGATİF STOK SERBEST: stok koruması BİLEREK kapatılıyor ki parti
        # katmanının kendi davranışı ölçülebilsin.
        s.ok(
            s.client.put(
                "/api/company-settings",
                headers=s.baslik,
                json={
                    "negative_stock_policy": "allow",
                    "credit_limit_policy": "block",
                },
            )
        )
        # HER TUR YENİ ÜRÜN — kolaylık değil, ÖLÇÜLMÜŞ bir engelin çaresi.
        # Kaybeden istek stoğu EKSİYE indiriyor (politika SERBEST) ve bir
        # sonraki turun ALIŞI o eksiye çarpıyor: alış yolu `allow_negative`
        # olmadan çağrıldığı için korumalı UPDATE'in `quantity + delta >= 0`
        # koşulu `-1 + 1 >= 0` ile tutmuyor ve alış 409 alıyor. Bu, bu kapının
        # ölçtüğü şeyle ilgisiz, ZATEN VAR OLAN bir stok davranışı; ürünü tur
        # başına yenilemek onu kapının dışında bırakıyor.
        for tur in range(YARIS_TURU):
            urun = s.urun(f"FEFO PG Yarış Serbest {tur}")
            s.alis([s.kalem(urun, 1, f"PG-YS{tur}", UZAK)])
            engel = Barrier(2)

            def bir_satis() -> int:
                with TestClient(app) as esli:
                    engel.wait(timeout=30)
                    return esli.post(
                        "/api/orders",
                        headers=s.baslik,
                        json={
                            "entity_id": s.musteri,
                            "transaction_date": "2026-09-09",
                            "due_date": "2026-09-30",
                            "warehouse_id": s.depo,
                            "items": [s.kalem(urun, 1, fiyat=20)],
                        },
                    ).status_code

            with ThreadPoolExecutor(max_workers=2) as havuz:
                durumlar = sorted(
                    is_.result(timeout=90)
                    for is_ in (havuz.submit(bir_satis), havuz.submit(bir_satis))
                )
            assert durumlar == [201, 201], (tur, durumlar)

            with SessionLocal() as db:
                kalan, tasiyan = db.execute(
                    text(
                        "SELECT l.quantity, "
                        "(SELECT count(*) FROM stock_movements h "
                        " WHERE h.company_id=l.company_id AND h.lot_id=l.id) "
                        "FROM product_lots l WHERE l.company_id=:cid "
                        "AND l.product_id=:pid AND l.lot_code=:kod"
                    ),
                    {"cid": s.cid, "pid": urun, "kod": f"PG-YS{tur}"},
                ).one()
                bosluklu = db.execute(
                    text(
                        "SELECT count(*) FROM stock_movements "
                        "WHERE company_id=:cid AND product_id=:pid "
                        "AND lot_id IS NULL AND note LIKE :damga"
                    ),
                    {"cid": s.cid, "pid": urun, "damga": f"%{DEFTER_BOSALDI_DAMGASI}%"},
                ).scalar_one()
            assert Decimal(str(kalan)) == Decimal("0"), (tur, kalan)
            assert int(tasiyan) == 2, (tur, tasiyan)
            # Ürün tur başına yenilendiği için sayı BİRDE sabit: kaybeden
            # isteğin TEK damgalı hareketi.
            assert int(bosluklu) == 1, (tur, bosluklu)


def test_PARTI_DUSMESI_YARISTA_ikinci_yaziciyi_REDDEDIYOR(motor) -> None:
    """`quantity>=:pay` yükleminin KENDİSİ ölçülüyor — HTTP'den değil.

    BU KAPI OLMASAYDI BİR BOŞLUK SESSİZ KALIRDI. İki HTTP isteği pratikte
    parti UPDATE'inde ÇAKIŞMIYOR (ÖLÇÜLDÜ: kaybeden istek partileri zaten
    tükenmiş buluyor ve `defter_bosaldi` dalına giriyor), yani yüklem oradan
    HİÇ tetiklenmiyor. Yüklem silinse iki yarış kapısı da YEŞİL KALIRDI.

    Burada çakışma DOĞRUDAN kuruluyor: iki AYRI oturum aynı partiyi tüketmek
    istiyor ve ikisi de SELECT'i yaptıktan sonra yazıyor. İkinci oturumun
    UPDATE'i birincinin SATIR KİLİDİNDE BEKLER; birinci `commit` edince
    PostgreSQL yüklemi İŞLENMİŞ değer üzerinde YENİDEN değerlendirir, koşul
    tutmaz, hiçbir satır dönmez ve `_parti_tuket` 409 `PARTI_YARISTA_TUKENDI`
    atar.

    YÜKLEM OLMASAYDI: ikinci UPDATE `1 - 1 = 0` yerine `0 - 1 = -1` yazardı
    ve 0067'nin `CHECK`i `IntegrityError` verirdi — yani bu test yine kırmızı
    olurdu, ama BAŞKA bir istisnayla. Kapı istisnanın TİPİNİ ve kodu ADIYLA
    soruyor ki "kırmızı olması" değil "DOĞRU sebeple kırmızı olması" ölçülsün.
    """
    from fastapi import HTTPException

    from app.db import SessionLocal
    from app.parti_defteri import _parti_tuket

    client, s = _sahne("kilit")
    with client:
        urun = s.urun("FEFO PG Kilit")
        s.alis([s.kalem(urun, 1, "PG-KILIT", UZAK)])
        bugun = datetime.now(timezone.utc).date()

        basla = Barrier(2)
        sonuc: dict[str, object] = {}

        def ikinci_oturum() -> None:
            with SessionLocal() as db:
                try:
                    basla.wait(timeout=30)
                    _parti_tuket(
                        db,
                        s.cid,
                        product_id=int(urun),
                        warehouse_id=int(s.depo),
                        miktar=Decimal("1"),
                        bugun=bugun,
                    )
                    db.commit()
                    sonuc["durum"] = "GECTI"
                except HTTPException as hata:
                    sonuc["durum"] = hata.status_code
                    sonuc["kod"] = (
                        hata.detail.get("code")
                        if isinstance(hata.detail, dict)
                        else None
                    )
                    db.rollback()
                except Exception as hata:  # noqa: BLE001 — tip ADIYLA raporlanır
                    sonuc["durum"] = type(hata).__name__
                    sonuc["kod"] = str(hata)[:200]
                    db.rollback()

        with ThreadPoolExecutor(max_workers=1) as havuz:
            with SessionLocal() as db:
                # BİRİNCİ oturum partiyi 1'den 0'a düşürür ve COMMIT ETMEZ.
                birinci = _parti_tuket(
                    db,
                    s.cid,
                    product_id=int(urun),
                    warehouse_id=int(s.depo),
                    miktar=Decimal("1"),
                    bugun=bugun,
                )
                assert len(birinci.dagitim) == 1, birinci

                is_ = havuz.submit(ikinci_oturum)
                basla.wait(timeout=30)
                # BEKLEME ZORUNLU VE YÖNÜ TEK TARAFLIDIR. İkincinin SELECT'i
                # bizim İŞLENMEMİŞ yazmamızı GÖRMEZ (READ COMMITTED): partiyi
                # hâlâ 1 okur, sonra UPDATE'i BİZİM satır kilidimizde BLOKE
                # olur. Commit'imizden sonra PostgreSQL yüklemi İŞLENMİŞ değer
                # üzerinde yeniden değerlendirir ve hiçbir satır dönmez.
                #
                # Erken commit edersek ikinci oturum partiyi 0 OKUR, hiç
                # bloke olmaz ve `defter_bosaldi` dalına düşer — yani kapı
                # ÖLÇMEK İSTEDİĞİ ŞEYİ ölçmez. Bu yüzden bekleme CÖMERTTİR ve
                # yönü tek taraflı: uzun beklemek testi yavaşlatır, kısa
                # beklemek onu YANLIŞ ŞEYİ ölçer hale getirir. Yanlış tarafa
                # düşerse sonuç "GECTI" olur ve kapı KIRMIZI olur — sessizce
                # geçmez.
                sleep(2.0)
                db.commit()
            is_.result(timeout=60)

        assert sonuc.get("durum") == 409, sonuc
        assert sonuc.get("kod") == "PARTI_YARISTA_TUKENDI", sonuc

        # DEFTER: parti TAM 0, eksiye İNMEDİ.
        with SessionLocal() as db:
            kalan = db.execute(
                text(
                    "SELECT quantity FROM product_lots WHERE company_id=:cid "
                    "AND product_id=:pid AND lot_code='PG-KILIT'"
                ),
                {"cid": s.cid, "pid": urun},
            ).scalar_one()
        assert Decimal(str(kalan)) == Decimal("0"), kalan
