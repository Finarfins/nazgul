"""PostgreSQL ikizi: WA3 işçisinin GERÇEK YARIŞI ve yürütücünün LEHÇESİ.

SQLite ikizi `tests/test_wa3_worker.py` akışın davranışını ölçüyor (kirala →
cevapla → damgala, bağsız numara, FİRMA SEÇ, WA4, hız sınırı sayacı). Bu
dosya yalnız GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN üç şeyi ölçer.

--- BU İKİZ NEDEN VAR — ÜÇ GEREKÇE, ÜÇÜ DE YALNIZ BURADA GÖRÜNÜR --------

1. **KİRA/CAS GERÇEK EŞZAMANLILIKTA.** `whatsapp_inbound` satırı kuyruğun
   kendisidir ve "aynı mesajı iki işçi işleyemez" cümlesi ancak GERÇEK
   eşzamanlılıkta sınanabilir. SQLite TEK YAZARLIDIR: oradaki yirmi thread
   sıraya girer, yani CAS düşse bile testi geçebilirdi. Burada yirmi işçi
   AYNI ANDA aynı satıra giriyor ve tam BİRİ jeton alıyor.

2. **YÜRÜTÜCÜNÜN SQL'i POSTGRESQL LEHÇESİNDE DE KOŞUYOR.** Yedi aracın
   arkasındaki sorgular lehçeye göre AYRIŞAN üç şey içeriyor ve üçü de
   SQLite'ta GÖRÜNMEZ:

   * tarih normalizasyonu (`reports._normalized_date_sql`) SQLite'ta
     `substr`, PostgreSQL'de `SUBSTRING ... FROM ... FOR`;
   * boolean karşılaştırması (`COALESCE(active, TRUE)=TRUE`) SQLite'ta
     TAMSAYI, PostgreSQL'de gerçek boolean;
   * `GROUP BY` işlevsel bağımlılığı — PostgreSQL, seçilen her sütunun
     gruplama anahtarına bağlı olmasını İSTER; SQLite İSTEMEZ. Kiracı
     bakiye sorgusu (`customers.musteri_satirlari`) tam da bu sınıftan bir
     sorgudur ve WhatsApp kanalı onu ŞİMDİ ÇAĞIRIYOR.

   Yedi aracın hepsi burada gerçek veriyle koşuyor: biri PostgreSQL'de
   patlarsa cevap DEAD olur ve kullanıcı HİÇBİR ŞEY almaz.

3. **HIZ SINIRI SAYACI GERÇEKTEN AYRI BİR İŞLEMDE.** SQLite'ta "ayrı
   oturum" aynı dosyayı açar; PostgreSQL'de gerçekten AYRI bir bağlantı ve
   AYRI bir transaction'dır. Dış transaction'ın rollback'i sayacı
   götürmüyor — mercek bulgusunun asıl kanıtı burada.

--- PAYLAŞILAN YARDIMCI: `acilisa_cek` İKİ UÇTAN -----------------------

`tests/pg_ikiz_yardimci.py` (#81/#82). Bu dosya `admin` olarak giriş
YAPMIYOR; çağrının görünür etkisi `_sync_sequences`tir ve bu ikiz
`companies`/`app_users`/`customers`/`products`a satır ekleyip sildiği için
paylaşılan bir veritabanında komşuların açık kimlik yazan yollarını korur.
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL ister.
pytestmark = pytest.mark.postgresql

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR (WA2 ikiziyle aynı gerekçe: CI'da
#: ikizler AYNI veritabanını paylaşabiliyor).
KOSU = uuid4().hex[:8]

NUMARA = "905405995959"
#: Yarış adımının kendi numarası: `_temizle` onu da toplasın diye AYNI
#: önekten türemiyor (numara bir firmaya bağlı değil), bu yüzden AÇIKÇA
#: siliniyor.
YARIS_NUMARASI = "905339998877"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("WA3 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN.

    ÖLÇÜLMÜŞ TUZAK (WA2 ikizinden devralındı): PG ikizleri paylaşık bir
    şemada koşabiliyor ve arkada bıraktığı satır KOMŞU dosyayı kırıyor.
    Sıra TERS: bileşik yabancı anahtarlar gerçek.
    """
    onek = KOSU + "%"
    firmalar = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        b.execute(
            text("DELETE FROM whatsapp_inbound WHERE sender_phone IN (:p, :y)"),
            {"p": NUMARA, "y": YARIS_NUMARASI},
        )
        for tablo in ("whatsapp_context", "whatsapp_pairing_codes",
                      "whatsapp_links", "order_items", "orders",
                      "customers", "products"):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firmalar}"),
                {"o": onek},
            )
        b.execute(
            text("DELETE FROM whatsapp_pairing_attempts WHERE phone IN (:p, :y)"),
            {"p": NUMARA, "y": YARIS_NUMARASI},
        )
        b.execute(
            text(f"DELETE FROM user_company_memberships WHERE company_id IN {firmalar}"),
            {"o": onek},
        )
        b.execute(text("DELETE FROM app_users WHERE username LIKE :o"), {"o": onek})
        b.execute(text("DELETE FROM companies WHERE name LIKE :o"), {"o": onek})


def _acilisa_cek(engine) -> None:
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    _acilisa_cek(engine)
    _temizle(engine)
    sys.path.insert(0, str(BACKEND))
    try:
        yield engine
    finally:
        _temizle(engine)
        _acilisa_cek(engine)
        engine.dispose()


@pytest.fixture()
def dunya(motor):
    """İKİ firma, BİR kullanıcı ve İKİ FİRMADA DA AYNI ADLI müşteri.

    Aynı ad BİLEREK: kiracı yalıtımı ancak komşuda AYNI ADI taşıyan bir
    kayıt varken ölçülebilir.
    """
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        firma_a = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:n,TRUE,:t) RETURNING id"),
            {"n": KOSU + "-bir", "t": an},
        ).scalar_one()
        firma_b = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:n,TRUE,:t) RETURNING id"),
            {"n": KOSU + "-iki", "t": an},
        ).scalar_one()
        kullanici = b.execute(
            text("INSERT INTO app_users(username,email,email_verified,"
                 "display_name,password_hash,role,is_active,"
                 "must_change_password,created_at)"
                 " VALUES(:u,:e,TRUE,:u,'x','admin',TRUE,FALSE,:t) RETURNING id"),
            {"u": KOSU + "-kul", "e": KOSU + "@wa3.invalid", "t": an},
        ).scalar_one()
        for cid in (firma_a, firma_b):
            b.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,FALSE,:t)"),
                {"u": kullanici, "c": cid, "t": an},
            )
        # AYNI AD, İKİ FİRMA, FARKLI BAKİYE.
        for cid, bakiye in ((firma_a, "1500"), (firma_b, "9900")):
            b.execute(
                text("INSERT INTO customers(company_id,name,opening_balance,"
                     "risk_limit,payment_term_days,is_active)"
                     " VALUES(:c,:a,:b,0,0,TRUE)"),
                {"c": cid, "a": "Şaban Korkmaz", "b": bakiye},
            )
        # Kritik stok ve satış geçmişi araçlarının GERÇEK satır görmesi için.
        urun = b.execute(
            text("INSERT INTO products(company_id,name,product_code,stock,unit,"
                 "purchase_price,sale_price,vat_rate,price_per,active,"
                 "critical_stock,minimum_stock)"
                 " VALUES(:c,'Yağ Filtresi','84993120',2,'Adet',10,20,20,'Adet',"
                 "TRUE,5,0) RETURNING id"),
            {"c": firma_a},
        ).scalar_one()
        musteri = b.execute(
            text("SELECT id FROM customers WHERE company_id=:c"), {"c": firma_a}
        ).scalar_one()
        siparis = b.execute(
            text("INSERT INTO orders(company_id,customer_id,order_date,subtotal,"
                 "vat_total,grand_total,discount_percent,discount_amount,"
                 "final_total,status,payment_method,paid_amount,payment_term,"
                 "due_date_source)"
                 " VALUES(:c,:m,:d,100,20,120,0,0,120,'completed','credit',0,"
                 "'PESIN','legacy') RETURNING id"),
            {"c": firma_a, "m": musteri, "d": an.date().isoformat()},
        ).scalar_one()
        b.execute(
            text("INSERT INTO order_items(company_id,order_id,product_name,"
                 "quantity,unit_price,vat_rate,line_subtotal,line_vat,line_total,"
                 "discount_percent,discount_amount)"
                 " VALUES(:c,:o,'Yağ Filtresi',3,20,20,60,12,72,0,0)"),
            {"c": firma_a, "o": siparis},
        )
    return {
        "firma_a": int(firma_a), "firma_b": int(firma_b),
        "kullanici": int(kullanici), "urun": int(urun),
    }


def _baglanti_yaz(baglanti, cid: int, uid: int, telefon: str = NUMARA) -> None:
    an = datetime.now(timezone.utc)
    baglanti.execute(
        text("INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
             "created_at,updated_at) VALUES(:c,:u,:p,TRUE,:t,:t)"),
        {"c": cid, "u": uid, "p": telefon, "t": an},
    )


_WAMID = iter(range(1, 100_000))


def _mesaj_yaz(baglanti, metin: str, *, telefon: str = NUMARA,
               durum: str = "RECEIVED", deneme: int = 0) -> int:
    an = datetime.now(timezone.utc)
    return int(
        baglanti.execute(
            text("INSERT INTO whatsapp_inbound(wamid,sender_phone,phone_number_id,"
                 "text,status,attempt_count,received_at)"
                 " VALUES(:w,:p,'',:m,:s,:d,:t) RETURNING id"),
            {
                "w": f"{KOSU}-{next(_WAMID)}",
                "p": telefon, "m": metin, "s": durum, "d": deneme, "t": an,
            },
        ).scalar_one()
    )


class _SahteSaglayici:
    def __init__(self) -> None:
        self.gonderilenler: list[tuple[str, str]] = []

    def metin_gonder(self, alici: str, metin: str) -> None:
        self.gonderilenler.append((alici, metin))


# ------------------------------------------------------------ GERÇEK YARIŞ


def test_YIRMI_ISCI_AYNI_SATIRA_GIRSE_TEK_KIRA_VERILIYOR(motor, dunya) -> None:
    """Yirmi işçi aynı `whatsapp_inbound` satırını kiralamaya çalışıyor.

    ÖLÇÜLEN ÜÇ ŞEY AYRI:
      (a) tam BİR işçi jeton aldı;
      (b) satır `PROCESSING` ve `lock_token` O jetonu taşıyor;
      (c) `attempt_count` tam BİR arttı — kaybeden UPDATE'ler satıra HİÇ
          dokunmadı. (c) olmadan CAS'i düşüren bir mutant, sayacı yirmi
          artırıp deneme tavanını TEK TURDA tüketirdi ve mesaj DEAD olurdu.

    ISINMA + KENDİ HAVUZU: bariyerden ÖNCE gidiş-dönüş yapılıyor ve
    yarışın kendi motoru var. `motor`un varsayılan havuzu ON BEŞ bağlantı
    tutar; yirmi thread bariyerden önce bağlantı tutmaya çalışınca bariyer
    düşer (WA2 ikizinde ölçüldü).
    """
    from app.whatsapp import service

    with motor.begin() as b:
        satir_id = _mesaj_yaz(b, "bu ay ciro", telefon=YARIS_NUMARASI)

    isci_sayisi = 20
    yaris_motoru = create_engine(_url(), pool_size=isci_sayisi + 5, max_overflow=5)
    Oturum = sessionmaker(bind=yaris_motoru)
    kapi = threading.Barrier(isci_sayisi)

    def kirala(_i: int) -> str | None:
        with Oturum() as db:
            db.execute(text("SELECT 1")).scalar()  # ISINMA: bariyerden ÖNCE
            kapi.wait(timeout=30)
            return service._claim(db, satir_id)

    try:
        with ThreadPoolExecutor(max_workers=isci_sayisi) as havuz:
            jetonlar = [
                i.result(timeout=120)
                for i in [havuz.submit(kirala, i) for i in range(isci_sayisi)]
            ]
    finally:
        yaris_motoru.dispose()

    kazananlar = [j for j in jetonlar if j is not None]
    assert len(kazananlar) == 1, jetonlar

    with motor.connect() as b:
        satir = b.execute(
            text("SELECT status,lock_token,attempt_count,locked_until"
                 " FROM whatsapp_inbound WHERE id=:i"),
            {"i": satir_id},
        ).mappings().one()
    assert satir["status"] == "PROCESSING"
    assert satir["lock_token"] == kazananlar[0]
    assert satir["attempt_count"] == 1, satir
    assert satir["locked_until"] is not None


def test_SURESI_DOLMUS_KIRA_DEVRALINIYOR_ve_TAVAN_ISIRIYOR(motor, dunya) -> None:
    """Süresi geçmiş kira devralınıyor; tavana ulaşan satır DEAD oluyor.

    `locked_until` PostgreSQL'de `TIMESTAMPTZ`tir ve karşılaştırma SAAT
    DİLİMLİ yapılır. Saat dilimi taşımayan bir damgayla yazılsaydı kira
    süresi ORTAM SAATİNE göre yorumlanır ve taze bir kira DOLMUŞ
    görünebilirdi — SQLite'ta bu soru HİÇ SORULMAZ.
    """
    from app.whatsapp import schema, service

    Oturum = sessionmaker(bind=motor)
    with motor.begin() as b:
        gecmis_id = _mesaj_yaz(b, "bu ay ciro", durum="PROCESSING")
        b.execute(
            text("UPDATE whatsapp_inbound SET locked_until=:t WHERE id=:i"),
            {"t": datetime.now(timezone.utc) - timedelta(minutes=1), "i": gecmis_id},
        )
        taze_id = _mesaj_yaz(b, "bu ay ciro", durum="PROCESSING")
        b.execute(
            text("UPDATE whatsapp_inbound SET locked_until=:t WHERE id=:i"),
            {
                "t": datetime.now(timezone.utc)
                + timedelta(minutes=schema.LEASE_DAKIKA),
                "i": taze_id,
            },
        )
        dolu_id = _mesaj_yaz(b, "bu ay ciro", deneme=schema.MAX_DENEME)

    with Oturum() as db:
        assert service._claim(db, gecmis_id) is not None
        assert service._claim(db, taze_id) is None
        assert service._claim(db, dolu_id) is None
        assert service.takilanlari_kapat(db) == 1

    with motor.connect() as b:
        assert b.execute(
            text("SELECT status FROM whatsapp_inbound WHERE id=:i"), {"i": dolu_id}
        ).scalar_one() == schema.DEAD


# ------------------------------------------------------------- LEHÇE ------


def test_YEDI_ARAC_POSTGRESQLDE_de_KOSUYOR(motor, dunya) -> None:
    """Yedi aracın HEPSİ gerçek PostgreSQL'de koşuyor ve cevap üretiyor.

    Lehçeye göre ayrışan ÜÇ şey burada sınanıyor (dosya başlığı): tarih
    normalizasyonu, boolean karşılaştırması ve `GROUP BY` işlevsel
    bağımlılığı. Biri yanlış yazılsaydı SQLite kulvarı YEŞİL kalır,
    PostgreSQL'de her cevap DEAD olurdu.
    """
    from app.whatsapp import eslestirme, niyet, yurutucu

    Oturum = sessionmaker(bind=motor)
    kimlik = eslestirme.Kimlik(
        company_id=dunya["firma_a"], user_id=dunya["kullanici"]
    )
    argumanlar = {
        "cari_durum": {"musteri": "Şaban"},
        "parca_stok": {"arama": "filtre"},
        "donem_ozeti": {"donem": "bu_ay"},
        "alacak_yaslandirma": {},
        "kritik_stok": {},
        "en_cok_satan_parcalar": {"donem": "bu_yil"},
        "parca_satis_gecmisi": {"arama": "filtre", "donem": "bu_yil"},
    }
    assert set(argumanlar) == set(niyet.ARAC_BEYAZ_LISTESI)
    with Oturum() as db:
        kosucu = yurutucu.VeritabaniYurutucu(db, kimlik)
        for arac, arg in argumanlar.items():
            veri = kosucu.kos(arac, arg)
            metin = niyet.cevap_yaz(arac, veri)
            assert isinstance(metin, str) and metin.strip(), arac

        # İKİSİ DE GERÇEK SATIR GÖRÜYOR: boş bir katalogda her sorgu
        # "bulunamadı" der ve lehçe hatası ORTAYA ÇIKMAZDI.
        stok = kosucu.kos("parca_stok", {"arama": "filtre"})
        assert stok["bulunan_kayit_sayisi"] == 1, stok
        kritik = kosucu.kos("kritik_stok", {})
        assert kritik["kritik_seviyedeki_urun_sayisi"] == 1, kritik
        satis = kosucu.kos("parca_satis_gecmisi", {"arama": "filtre",
                                                   "donem": "bu_yil"})
        assert satis["satilan_adet"] == 3, satis
        assert satis["belge_sayisi"] == 1, satis


def test_KIRACI_YALITIMI_AYNI_AD_KOMSU_FIRMADA(motor, dunya) -> None:
    """İki firmada AYNI ADLI müşteri; cevap YALNIZ bağlı firmanınkini taşır.

    Bakiye sorgusu `GROUP BY`lı ve çok birleştirmelidir; PostgreSQL'de
    işlevsel bağımlılık kuralı yüzünden SQLite'takinden FARKLI davranabilir.
    Kiracı yükleminin GERÇEKTEN ısırdığı bu yüzden burada da ölçülüyor.
    """
    from app.whatsapp import service

    Oturum = sessionmaker(bind=motor)
    with motor.begin() as b:
        _baglanti_yaz(b, dunya["firma_b"], dunya["kullanici"])
        _mesaj_yaz(b, "Şaban Korkmaz bakiye")

    saglayici = _SahteSaglayici()
    with Oturum() as db:
        assert service.bekleyenleri_isle(db, saglayici=saglayici) == 1
    cevap = saglayici.gonderilenler[-1][1]
    assert "9.900,00" in cevap, cevap
    assert "1.500" not in cevap, cevap


# ------------------------------------------- HIZ SINIRI SAYACI (AYRI TX) --


def test_HIZ_SINIRI_SAYACI_GERCEK_AYRI_ISLEMDE_KALICI(motor, dunya) -> None:
    """Dış transaction geri alınıyor; sayaç AYRI BİR BAĞLANTIDA kalıyor.

    SQLite'ta "ayrı oturum" aynı dosyayı açar ve bu ayrımı zayıf kılar.
    PostgreSQL'de gerçekten AYRI bir bağlantı ve AYRI bir transaction'dır;
    dıştaki `ROLLBACK` ona DOKUNAMAZ.

    MUTASYON: `_bagla_akisi`ın rollback dallarından `_sayaci_kalicilastir`
    çağrısını kaldırmak bunu KIRMIZI yapar (sayaç 0 kalır) — düşen bir
    sağlayıcıyla hız sınırı HİÇ ısırmazdı.
    """
    from app.whatsapp import saglayici as sag, service

    Oturum = sessionmaker(bind=motor)

    class _Patlayan:
        def metin_gonder(self, alici: str, metin: str) -> None:
            raise sag.GonderimHatasi("ag")

    with motor.begin() as b:
        _mesaj_yaz(b, "BAĞLA ABCD-EFGH-JKMN")

    with Oturum() as db:
        assert service.bekleyenleri_isle(
            db, saglayici=_Patlayan(), oturum_fabrikasi=Oturum
        ) == 0

    with motor.connect() as b:
        sayac = b.execute(
            text("SELECT COALESCE(SUM(attempt_count),0)"
                 " FROM whatsapp_pairing_attempts WHERE phone=:p"),
            {"p": NUMARA},
        ).scalar_one()
    # TAM BİR: dıştaki artış geri alındı, ayrı işlemdeki kaldı.
    assert int(sayac) == 1, sayac


def test_GOC_TURU_up_down_up_GERCEK_PostgreSQLde(motor) -> None:
    """Bu dilim GÖÇ AÇMIYOR: şema başı `head`de kalıyor.

    Kapı bir ölçümün kaydıdır: WA3-full yalnız WA1'in AÇTIĞI sütunların
    POLİTİKASINI yazıyor (`LEASE_DAKIKA`, `MAX_DENEME`). Yeni bir göç
    eklenirse bu kapı hatırlatır — göç turu WA1/WA2 ikizlerinin işidir ve
    orada zaten koşuyor.

    VE HATIRLATTI: WA4 (#84, göç `20260910_0080`, bekleyen işlem defteri)
    başı 0079'dan 0080'e taşıdı, bu kapı da onunla güncellendi. WA3'ün
    iddiası KIMILDAMADI — bu dilim hâlâ göç AÇMIYOR; değişen tek şey,
    altında duran zincirin ucudur.
    """
    with motor.connect() as b:
        surumler = b.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all()
    assert surumler == ["20260913_0083"], surumler
