"""PostgreSQL ikizi: 1B-E satış iadesinin GERÇEK DİYALEKTTE eşi.

Göç YOKTUR (1B-E tek sütun eklemez); ölçülen şey 0067/0073'ün kısıtlarının ve
PostgreSQL'in TİP davranışının GERİ VERME yolunda gerçekten ısırdığıdır.
SQLite ikizi `tests/test_1b_e_iade_lot.py` DAVRANIŞI ölçüyor (ters FEFO,
kapasite, 422/409, geri alma, depo); bu dosya yalnız GELİŞTİRME DİYALEKTİNDE
GÖRÜNMEYEN yarıları ölçer.

--- BU İKİZ NEDEN ZORUNLU --------------------------------------------------

1. **SKT'NİN TİPİ İKİ DİYALEKTTE İKİ ŞEYDİR.** `_parti_iade` kaynak partinin
   `expiry_date`ini OKUYUP `_parti_ac`a geri veriyor. PostgreSQL o sütunu
   `datetime.date` olarak döndürür, SQLite `str`. Fonksiyon değeri METNE
   çeviriyor; çevirmeseydi `_parti_ac`ın karşılaştırması (`isoformat()`
   üzerinden, METİN tarafında) `date` nesnesiyle eşleşmez ve HER İADE
   `422 LOT_SKT_CELISKI` alırdı — SKT'de hiçbir çelişki YOKKEN. Kusur YALNIZ
   burada görünür: SQLite'ta aynı satır zaten metindir.

2. **`CHECK (quantity >= 0 AND quantity <> 'NaN'::numeric)` GERİ ALMADA
   ISIRIR.** İade hareketleri POZİTİF yazılıyor, yani geri alma partiden
   DÜŞÜYOR ve eksiye düşürebilir. `_parti_geri_al`ın uygulama kapısı 409
   veriyor; o kapı kaldırılsa PostgreSQL `IntegrityError` ile ölürdü ve
   SQLite SESSİZCE eksiye inerdi. İki cevap arasındaki fark ancak gerçek
   katalogda sorulabilir.

3. **ONDALIK PAYLAR `NUMERIC(18,4)` ÖLÇEĞİNDE TOPLANMALI.** Ters FEFO
   dağıtımı bölüştürüyor; ölçek SQLite'ta DAYATILMAZ (orada her şey `REAL`
   ya da metindir), yani kayan nokta artığı geliştirme diyalektinde
   GÖRÜNMEZ.

4. **KİRACI SINIRI GERÇEK KATALOGDA.** Komşu firmanın partisi ve hareketleri
   bu yoldan GÖRÜLMEMELİ; `product_lots`un bileşik yabancı anahtarları
   SQLite'ta varsayılan olarak UYGULANMAZ (`PRAGMA foreign_keys` temiz bir
   şemada 0 döner), yani kiracı savunmasının şema yarısı orada YEŞİL kalırdı.

--- BU İKİZ NEYİ ÖLÇMÜYOR --------------------------------------------------

EŞZAMANLI İKİ İADE ölçülmüyor ve bu bir KAPSAM SINIRIDIR, unutma değil.
`_parti_iade` kapasiteyi OKUYUP sonra yazıyor; aynı satışa aynı anda kesilen
iki iade ikisi de kapasiteyi YETERLİ görüp ikisi de geri verebilir. Sonuç
STOKLA TUTARLIDIR (stok da iki kez artar), yani bu bir stok/defter ayrışması
DEĞİL, "iade satışı aşamaz" iş kuralının yarışta gevşemesidir. 1B-B'nin
tüketim yarışını ölçmesinin sebebi tam olarak ayrışmaydı; burada ayrışma
YOKTUR ve serileştirme (kaynak satırlarına `FOR UPDATE`) bu dilime
ALINMAMIŞTIR. Sınır sessiz bırakılmadı: bir sonraki dilim onu kapatacaksa
neyi kapatacağını burada okuyabilir.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent

KOMSU_ADI = "1B-E İKİZİ komşu firması"
ADMIN_PW = "IadeLotPG!123"

#: KOŞU BAŞINA BENZERSİZ SON EK — 1B-B ikizinin ölçülmüş tuzağının aynısı:
#: bu dosya ADMIN firmasında çalışıyor ve o firma silinemez, yani ürünler ile
#: parti kodları KOŞULAR ARASINDA birikiyor. Sabit bir kod ikinci koşuda
#: `scalar_one()`ı `MultipleResultsFound` ile düşürürdü.
KOSU = uuid4().hex[:8]

YAKIN = "2098-01-31"
UZAK = "2099-01-31"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("1B-E ikizi APP_TEST_DATABASE_URL ister")
    return url


def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA yaz (1B-A/1B-B ikizlerinden devralındı).

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
        depolar = self.ok(client.get("/api/warehouses", headers=baslik))
        self.depo = depolar[0]["id"]
        self.depo_b = self.ok(
            client.post(
                "/api/warehouses",
                headers=baslik,
                json={"name": f"1B-E B {etiket}", "code": f"IBE{etiket[-6:]}"},
            )
        )["id"]
        self.tedarikci = self.ok(
            client.post(
                "/api/suppliers", headers=baslik, json={"name": f"1B-E TED {etiket}"}
            )
        )["id"]
        self.musteri = self.ok(
            client.post(
                "/api/customers", headers=baslik, json={"name": f"1B-E MUS {etiket}"}
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

    def satis(self, kalemler, depo=None):
        return self.ok(
            self.client.post(
                "/api/orders",
                headers=self.baslik,
                json={
                    "entity_id": self.musteri,
                    "transaction_date": "2026-09-09",
                    "due_date": "2026-09-30",
                    "warehouse_id": depo or self.depo,
                    "items": kalemler,
                },
            )
        )

    def iade_istegi(self, kalemler, kaynak, depo=None):
        return self.client.post(
            "/api/workflow/sale_return",
            headers=self.baslik,
            json={
                "entity_id": self.musteri,
                "document_date": "2026-09-12",
                "status": "completed",
                "warehouse_id": depo or self.depo,
                "source_type": "order",
                "source_id": kaynak,
                "items": kalemler,
            },
        )

    def iade(self, kalemler, kaynak, depo=None):
        return self.ok(self.iade_istegi(kalemler, kaynak, depo))

    def partiler(self, pid, depo=None):
        satirlar = self.ok(
            self.client.get(f"/api/products/{pid}/lots", headers=self.baslik)
        )["lots"]
        if depo is not None:
            satirlar = [s for s in satirlar if s["warehouse_id"] == depo]
        return {s["lot_code"]: Decimal(str(s["quantity"])) for s in satirlar}

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


# ------------------------------------------------------------------ skt ---

def test_SKT_NATIVE_date_olarak_KOPYALANIYOR_celiski_URETMIYOR(motor) -> None:
    """Kaynak partinin `date`i geri yazılırken METNE çevriliyor.

    ÇEVRİM ATLANIRSA KUSUR YALNIZ BURADA GÖRÜNÜR: `_parti_ac`ın çelişki
    denetimi var olan SKT'yi `isoformat()` ile metne çevirip karşılaştırıyor.
    PostgreSQL sütunu `datetime.date` döndürdüğü için ham nesne geri
    verilseydi karşılaştırma `str != date` olur ve HER iade
    `422 LOT_SKT_CELISKI` alırdı — ortada hiçbir çelişki YOKKEN. SQLite'ta
    aynı satır zaten metindir, yani orası YEŞİL kalırdı.

    İki yön birden ölçülüyor: AYNI depoya iade (var olan partiye EKLEME,
    karşılaştırma dalı) ve BAŞKA depoya iade (YENİ satır, INSERT dalı).
    """
    client, s = _sahne("skt")
    with client:
        urun = s.urun("İade PG SKT")
        s.alis([s.kalem(urun, 5, f"PG-SKT-{KOSU}", UZAK)])
        satis = s.satis([s.kalem(urun, 4, fiyat=20)])
        assert s.partiler(urun, depo=s.depo) == {f"PG-SKT-{KOSU}": Decimal("1")}

        # AYNI DEPO: var olan tarihli parti, karşılaştırma dalı.
        s.iade([s.kalem(urun, 2, fiyat=20)], satis["id"])
        assert s.partiler(urun, depo=s.depo) == {f"PG-SKT-{KOSU}": Decimal("3")}

        # BAŞKA DEPO: yeni satır, INSERT dalı — SKT AYNEN kopyalanmalı.
        s.iade([s.kalem(urun, 2, fiyat=20)], satis["id"], depo=s.depo_b)
        assert s.partiler(urun, depo=s.depo_b) == {f"PG-SKT-{KOSU}": Decimal("2")}

        from app.db import SessionLocal

        with SessionLocal() as db:
            satirlar = db.execute(
                text(
                    "SELECT warehouse_id,expiry_date FROM product_lots "
                    "WHERE company_id=:cid AND product_id=:pid ORDER BY warehouse_id"
                ),
                {"cid": s.cid, "pid": urun},
            ).all()
        # TİP DE ÖLÇÜLÜYOR, yalnız değer değil: sütun gerçekten `date`
        # döndürüyor ve iki depodaki iki satır AYNI tarihi taşıyor.
        assert len(satirlar) == 2, satirlar
        for _, skt in satirlar:
            assert isinstance(skt, date), (type(skt), skt)
            assert skt.isoformat() == UZAK, skt


# -------------------------------------------------------------- ondalık ---

def test_ONDALIK_paylar_NUMERIC_18_4_olceginde_TAM_topluyor(motor) -> None:
    """Ters FEFO bölüştürmesi ölçekte TAM toplanıyor; artık YOK.

    Ölçek SQLite'ta DAYATILMAZ, yani `NUMERIC(18,4)`e sığmayan bir artık
    orada GÖRÜNMEZDİ. Burada hem hareket payları hem parti bakiyeleri
    okunuyor: ikisi de satış öncesine TAM dönmeli.
    """
    client, s = _sahne("ondalik")
    with client:
        urun = s.urun("İade PG Ondalık")
        s.alis([s.kalem(urun, "1.3333", f"PG-O1-{KOSU}", YAKIN)])
        s.alis([s.kalem(urun, "2.6667", f"PG-O2-{KOSU}", UZAK)])
        satis = s.satis([s.kalem(urun, "3.5000", fiyat=20)])
        # FEFO: O1'in tamamı (1.3333), sonra O2'den 2.1667.
        assert s.hareketler("orders", satis["id"]) == [
            (f"PG-O1-{KOSU}", Decimal("-1.3333")),
            (f"PG-O2-{KOSU}", Decimal("-2.1667")),
        ], s.hareketler("orders", satis["id"])

        iade = s.iade([s.kalem(urun, "3.5000", fiyat=20)], satis["id"])
        # TERS FEFO: önce O2'nin 2.1667'si, sonra O1'in 1.3333'ü.
        assert s.hareketler("returns", iade["id"]) == [
            (f"PG-O2-{KOSU}", Decimal("2.1667")),
            (f"PG-O1-{KOSU}", Decimal("1.3333")),
        ], s.hareketler("returns", iade["id"])
        assert sum(m for _, m in s.hareketler("returns", iade["id"])) == Decimal(
            "3.5000"
        )
        assert s.partiler(urun, depo=s.depo) == {
            f"PG-O1-{KOSU}": Decimal("1.3333"),
            f"PG-O2-{KOSU}": Decimal("2.6667"),
        }


# ---------------------------------------------------------------- kısıt ---

def test_GERI_ALMA_partiyi_EKSIYE_DUSURMUYOR_kisit_ISIRIYOR(motor) -> None:
    """İade silinirken parti eksiye inecekse 409; `IntegrityError` DEĞİL.

    İade hareketleri POZİTİFTİR, yani geri alma partiden DÜŞER. Uygulama
    kapısı (`_parti_geri_al`) kaldırılsaydı PostgreSQL 0067'nin
    `CHECK (quantity >= 0 ...)` kısıtıyla `IntegrityError` atardı — operatöre
    NE YAPACAĞINI söylemeyen bir ölüm — ve SQLite SESSİZCE eksiye inerdi.
    Ölçülen şey ikisi arasındaki fark: 409, KOD ve defterin OYNAMAMASI.
    """
    client, s = _sahne("kisit")
    with client:
        urun = s.urun("İade PG Kısıt")
        s.alis([s.kalem(urun, 3, f"PG-K-{KOSU}", UZAK)])
        satis = s.satis([s.kalem(urun, 3, fiyat=20)])
        iade = s.iade([s.kalem(urun, 3, fiyat=20)], satis["id"])
        assert s.partiler(urun, depo=s.depo) == {f"PG-K-{KOSU}": Decimal("3")}

        # Geri verilen mal YENİDEN satıldı: parti tükendi.
        s.satis([s.kalem(urun, 3, fiyat=20)])
        assert s.partiler(urun, depo=s.depo) == {f"PG-K-{KOSU}": Decimal("0")}

        engellendi = client.delete(
            f"/api/workflow/sale_return/{iade['id']}", headers=s.baslik
        )
        assert engellendi.status_code == 409, (
            engellendi.status_code,
            engellendi.text,
        )
        assert (
            engellendi.json()["detail"]["code"] == "LOT_MIKTARI_EKSIYE_DUSER"
        ), engellendi.text
        assert s.partiler(urun, depo=s.depo) == {f"PG-K-{KOSU}": Decimal("0")}
        # BELGE DE DURUYOR: red yarım bir silme bırakmadı.
        assert (
            client.get(
                f"/api/workflow/sale_return/{iade['id']}", headers=s.baslik
            ).status_code
            == 200
        )


# --------------------------------------------------------------- kiracı ---

def test_KIRACI_YUKLEMI_komsunun_partisini_GORMUYOR(motor) -> None:
    """`_parti_iade`in `company_id=:cid` yüklemi GERÇEKTEN ısırıyor.

    Kapı `_parti_iade`i DOĞRUDAN çağırıyor, uçtan DEĞİL ve bu 1B-B ikizinin
    ölçülmüş kararının aynısı: uçtan iki kiracıyı aynı anda konuşturmak
    ikinci bir kullanıcı/oturum kurulumu isterdi ve ölçülen şey yine bu tek
    yüklem olurdu. Ayrıca uçtan geçmek İMKÂNSIZ: `validate_return_reference`
    çapraz kiracı kaynağını DAHA ÖNCE 409 ile kesiyor, yani `_parti_iade`e
    hiç sıra gelmezdi ve kapı YÜKLEMİ değil o kapıyı ölçerdi.

    Yüklem düşerse komşunun satışının parti borcu BİZİM iademizin
    kapasitesine girer ve iade komşunun partisini büyütürdü — hiçbir kısıt
    bunu engellemez, çünkü okuma bir SELECT'tir.

    SAHTE YEŞİL KARŞITI ZORUNLU: aynı çağrı KOMŞUNUN kiracısıyla borcu
    BULUYOR. Bulmasaydı yukarıdaki 422 yüklemin değil KURULUMUN kanıtı
    olurdu — bu deponun ölçülmüş tuzağı (1B-B'nin depo kapısı bir tur boyunca
    tam olarak böyle sahte yeşildi).
    """
    from datetime import datetime, timezone

    from fastapi import HTTPException

    client, s = _sahne("kiraci")
    with client:
        from app.db import SessionLocal
        from app.parti_defteri import _parti_iade

        simdi = datetime.now(timezone.utc)
        with SessionLocal() as db:
            komsu_cid = db.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at) "
                    "VALUES(:ad,true,:now) RETURNING id"
                ),
                {"ad": KOMSU_ADI, "now": simdi},
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
            komsu_parti = db.execute(
                text(
                    "INSERT INTO product_lots(company_id,product_id,lot_code,"
                    "expiry_date,quantity,warehouse_id,created_at) "
                    "VALUES(:cid,:pid,'KMS-LOT',CAST(:skt AS date),47,:wid,:now) "
                    "RETURNING id"
                ),
                {
                    "cid": komsu_cid,
                    "pid": komsu_urun,
                    "skt": UZAK,
                    "wid": komsu_depo,
                    "now": simdi,
                },
            ).scalar_one()
            # KOMŞUNUN SATIŞI: parti borcunu taşıyan hareket satırı. Belge
            # kimliği 999999 SEÇİLDİ ve bizim kiracımızda böyle bir sipariş
            # YOK — yani bu satırı görmenin TEK yolu kiracı yüklemini
            # düşürmektir.
            db.execute(
                text(
                    "INSERT INTO stock_movements(product_id,movement_type,"
                    "quantity,movement_date,reference_type,reference_id,note,"
                    "company_id,warehouse_id,lot_id) "
                    "VALUES(:pid,'sale',-3,'2026-09-09','orders',999999,"
                    "'komsu satis',:cid,:wid,:lot)"
                ),
                {
                    "pid": komsu_urun,
                    "cid": komsu_cid,
                    "wid": komsu_depo,
                    "lot": komsu_parti,
                },
            )
            db.commit()

        # BİZİM kiracımız komşunun ürününü/belgesini SORSA BİLE borç GÖRMEZ:
        # "bu satışta bu ürün HİÇ yok" -> 422.
        with SessionLocal() as db:
            with pytest.raises(HTTPException) as hata:
                _parti_iade(
                    db,
                    s.cid,
                    product_id=int(komsu_urun),
                    warehouse_id=int(komsu_depo),
                    satis_tablosu="orders",
                    satis_id=999999,
                    iade_kaynak_turu="order",
                    miktar=Decimal("1"),
                )
            db.rollback()
        assert hata.value.status_code == 422, hata.value.detail
        assert hata.value.detail["code"] == "IADE_SATISI_ASIYOR", hata.value.detail

        # SAHTE YEŞİL KARŞITI: aynı çağrı KOMŞUNUN kiracısıyla borcu buluyor.
        with SessionLocal() as db:
            onun = _parti_iade(
                db,
                int(komsu_cid),
                product_id=int(komsu_urun),
                warehouse_id=int(komsu_depo),
                satis_tablosu="orders",
                satis_id=999999,
                iade_kaynak_turu="order",
                miktar=Decimal("1"),
            )
            db.rollback()
        assert onun == ((int(komsu_parti), Decimal("1.0000")),), onun

        # KOMŞUNUN DEFTERİ OYNAMADI: yukarıdaki iki çağrı da geri sarıldı.
        with SessionLocal() as db:
            kalan = db.execute(
                text(
                    "SELECT quantity FROM product_lots "
                    "WHERE company_id=:cid AND id=:id"
                ),
                {"cid": komsu_cid, "id": komsu_parti},
            ).scalar_one()
        assert Decimal(str(kalan)) == Decimal("47"), kalan
