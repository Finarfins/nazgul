"""PostgreSQL ikizi: WA4 bekleyen işlem defterinin GERÇEK kısıtları ve YARIŞI.

Göç `20260910_0080`. SQLite ikizi `tests/test_wa4_bekleyen.py` akışın
davranışını ölçüyor (taslak → ONAY → APPLIED, İPTAL, süre, kiracı yalıtımı,
çökme penceresi); bu dosya yalnız ŞEMANIN GERÇEKTEN ISIRAN kısımlarını ve
GELİŞTİRME DİYALEKTİNDE GÖRÜNMEYEN tuzakları ölçer.

--- BU İKİZ NEDEN VAR — BEŞ GEREKÇE, BEŞİ DE YALNIZ BURADA GÖRÜNÜR ------

1. **BİR TASLAK TAM OLARAK BİR KEZ UYGULANIR — YALNIZ BURADA ÖLÇÜLEBİLİR.**
   Kullanıcı `ONAY` yazdığında (ya da bir işçi kuyruğu yeniden okuduğunda)
   AYNI taslak için YİRMİ eşzamanlı uygulama denemesi doğabilir. Yalnız
   BİRİ ödeme yazmalıdır. SQLite'ta bu ÜRETİLEMEZ (tek yazar).

   NEYİ ÖLDÜRDÜĞÜ ve NEYİ ÖLDÜRMEDİĞİ ÖLÇÜLDÜ — testin kendi başlığında.
   Kısaca: değişmez ÜÇ BAĞIMSIZ katmanla korunuyor (CAS, idempotency
   defteri, jeton sahipliği) ve HERHANGİ BİRİ TEK BAŞINA yetiyor, yani tek
   bir katmanı düşüren mutant bu testten SAĞ ÇIKABİLİR. Katmanların tek tek
   çivilenmesi bu yüzden AST kapılarının işidir
   (`tests/test_wa4_bekleyen.py::test_CAS_KOSULU_STATUS_ve_KIRACI_YUKLEMLI`
   ve `..._IDEMPOTENCY_ANAHTARI_...`), bu testin değil. Bu test ÜÇÜNÜN
   BİRDEN kaybını yakalıyor.

2. **KISMİ TEKİL POSTGRESQL'DE DE KURULUYOR ve ISIRIYOR.** Göç yüklemi İKİ
   diyalekt parametresine de veriliyor; SQLite ikizi yalnız BİRİNCİSİNİ
   ölçebilir. İkincisi yanlış yazılmış olsaydı indeks PostgreSQL'de HİÇ
   kurulmaz ve aynı kapsamda İKİ aktif taslak yaşayabilirdi — yani `ONAY`
   kelimesinin İKİ anlamı olurdu. Burada hem RED hem İZİN ölçülüyor.

3. **DÖRT `ck_wpa_*` CHECK'İ GERÇEKTEN REDDEDİYOR.** SQLite CHECK'i
   YANSITMIYOR (0072'de ölçüldü). `ck_wpa_applied_result` açık olsaydı
   `APPLIED` ama `result_id` NULL bir satır yazılabilirdi ve "hangi ödeme
   yazıldı" sorusu cevapsız kalır, mutabakat imkânsızlaşırdı.

4. **BİLEŞİK YABANCI ANAHTAR GERÇEKTEN BAĞLIYOR.** SQLite yabancı
   anahtarları varsayılan olarak UYGULAMAZ. Burada bir firmanın taslağının
   BAŞKA firmanın bağlantısını işaret etmesi denenip REDDEDİLDİĞİ
   görülüyor — kaynağı grep'lemek bunu SÖYLEYEMEZ.

5. **ZAMAN SÜTUNLARI `TIMESTAMPTZ`.** SQLite'ta zaman bir metindir ve saat
   dilimi sorusu HİÇ SORULMAZ. Saat dilimi taşımayan bir `expires_at`,
   "taslak ne zaman doldu" sorusunu oturumun TZ'sine göre yanlış cevaplardı
   ve süresi dolmuş bir taslak ONAYLANABİLİR hâle gelirdi.

--- ÖLÇÜLEN KÖK SEBEP, İDDİA DEĞİL ---------------------------------------

Kısıt testleri kısıtın VARLIĞINI değil GERÇEKTEN REDDETTİĞİNİ ölçüyor: her
biri kısıtı ihlal eden bir yazma deneyip `IntegrityError` bekliyor.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent

#: DOSYA SEVİYESİNDE İŞARET: bu ikizin TAMAMI gerçek bir PostgreSQL
#: sunucusu ister (`pytest.ini`in `postgresql` işareti).
pytestmark = pytest.mark.postgresql

# TAHSİS MOTORU AÇIK — `app.config.Settings` içe aktarmada TEK KOPYA olarak
# kurulur, bu yüzden ortam DEĞİŞKENİ uygulama modülleri gelmeden yazılmalı.
# Gerekçe SQLite ikizindekiyle AYNI: WA4 kapalı motorda BİLEREK yazmaz.
os.environ["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
if os.environ.get("APP_TEST_DATABASE_URL", "").startswith("postgresql"):
    os.environ["DATABASE_URL"] = os.environ["APP_TEST_DATABASE_URL"]

TABLO = "whatsapp_pending_actions"
BAGLANTI = "whatsapp_links"

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR. CI'da PostgreSQL ikizleri AYNI
#: veritabanını paylaşabiliyor; sabit bir ad kapıyı ilk koşuda yeşil,
#: ikincisinde kırmızı yapardı ve kırmızılığı kusuru DEĞİL koşu sırasını
#: gösterirdi.
KOSU = uuid4().hex[:8]

NUMARA = "905405995959"

#: Eşzamanlı `ONAY` sayısı. Yirmi, tek bir yazarın kazanmasını tesadüf
#: olmaktan çıkaracak kadar çok; koşuyu yavaşlatmayacak kadar az.
ESZAMANLI = 20


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("WA4 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _temizle(engine) -> None:
    """KENDİ satırlarını siler — ÖNEKLE, tabloyu SÜPÜRMEDEN.

    ÖLÇÜLMÜŞ TUZAK: PG ikizleri paylaşık bir şemada koşabiliyor ve arkada
    bıraktığı satır KOMŞU dosyayı kırıyor. `DELETE FROM
    whatsapp_pending_actions` yazmak da yanlış olurdu: bu dosya kendi çöpünü
    toplamalı, başkasınınkini değil.

    SIRA TERS ve bu ZORUNLU: taslak → ödeme defteri → tahsis → ödeme →
    belge → bağlantı → müşteri → aktivite → üyelik → kullanıcı → firma.
    Bileşik yabancı anahtarlar GERÇEK; ters sıra `DependentObjectsStillExist`
    verirdi.

    --- `activity_logs` APPEND-ONLY ve BU TEARDOWN ONU BİLEREK AŞIYOR ----

    ÖLÇÜLDÜ (varsayılmadı): `taslak_olustur`, `onayla` ve `iptal_et` her biri
    bir aktivite satırı yazıyor; o satır `companies`e FK ile bağlı ve göç
    `20260727_0030` tabloya KOŞULSUZ bir `BEFORE DELETE` tetikleyicisi
    koyuyor (`trg_activity_logs_no_delete`). Yani firmayı silmek için
    aktivite satırını silmek, aktivite satırını silmek için de tetikleyiciyi
    geçmek gerekiyor.

    ÜÇ SEÇENEK VARDI ve ikisi reddedildi:

    * Satırları BIRAKMAK (firma + kullanıcı + aktivite): dosya kendi çöpünü
      toplamamış olurdu. Paylaşık şemada biriken firma satırı, firma SAYAN
      bir komşu dosyayı kırar — ve kırıldığında kusuru DEĞİL koşu sırasını
      gösterir. Bu, `product_lots` artıklarıyla zaten bir kez ölçülmüş bir
      hata sınıfı.
    * Tetikleyiciyi kalıcı olarak DÜŞÜRMEK: denetim kaydının değişmezliği
      test veritabanında da gerçek olmalı — bu ikizin KENDİ testleri o
      koruma AÇIKKEN koşuyor.

    SEÇİLEN: tetikleyici YALNIZ teardown transaction'ının içinde kapatılıp
    aynı transaction'da geri açılıyor. GÜVENLİ olmasının sebebi
    PostgreSQL'de DDL'in TRANSACTION'A DÂHİL olmasıdır: bu blok herhangi bir
    sebeple düşerse (süreç ölür, bir DELETE patlar) `DISABLE TRIGGER` de
    GERİ ALINIR ve koruma hiç kapanmamış olur. Bir sonraki `COMMIT`e kadar
    korumanın kapalı olduğu tek yer bu transaction'dır ve o transaction
    içinde `activity_logs`a başka hiçbir şey dokunmaz.

    SQLite ikizinde bu sorun YOKTUR ve olmaması bir şans değil: orada
    aktivite tablosu her testte baştan kurulan geçici bir dosyada yaşıyor.
    """
    onek = KOSU + "%"
    firma_alt = "(SELECT id FROM companies WHERE name LIKE :o)"
    with engine.begin() as b:
        for tablo in (
            TABLO, "payment_idempotency", "payment_allocations",
            "payments", "orders", BAGLANTI, "customers",
        ):
            b.execute(
                text(f"DELETE FROM {tablo} WHERE company_id IN {firma_alt}"),
                {"o": onek},
            )
        # Gerekçe yukarıda. Kapatma ve açma AYNI transaction'da.
        b.execute(
            text("ALTER TABLE activity_logs DISABLE TRIGGER"
                 " trg_activity_logs_no_delete")
        )
        try:
            b.execute(
                text(f"DELETE FROM activity_logs WHERE company_id IN {firma_alt}"),
                {"o": onek},
            )
        finally:
            b.execute(
                text("ALTER TABLE activity_logs ENABLE TRIGGER"
                     " trg_activity_logs_no_delete")
            )
        b.execute(
            text(f"DELETE FROM user_company_memberships"
                 f" WHERE company_id IN {firma_alt}"),
            {"o": onek},
        )
        b.execute(text("DELETE FROM app_users WHERE username LIKE :o"), {"o": onek})
        b.execute(text("DELETE FROM companies WHERE name LIKE :o"), {"o": onek})


def _acilisa_cek(engine) -> None:
    """Ortak yardımcı (#81/#82). BU DOSYA İÇİN BUGÜN NO-OP ve bu ÖLÇÜLDÜ.

    Bu ikiz HİÇBİR ZAMAN `admin` olarak giriş YAPMIYOR — kendi firmalarını
    ham SQL ile açıyor. Yine de İKİ UÇTAN çağrılıyor; gerekçe WA2 ikizinde
    yazılı olanla AYNI (dolaylı yol + `_sync_sequences`): bu dosya
    `companies`, `app_users`, `customers`, `orders` ve `payments`a satır
    ekleyip siliyor ve paylaşılan bir veritabanında dizilerin `max(id)`ye
    çekilmesi komşu dosyaların açık kimlik yazan yollarını korur.
    """
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    _acilisa_cek(engine)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        _acilisa_cek(engine)
        engine.dispose()


@pytest.fixture()
def dunya(motor):
    """İKİ firma, BİR kullanıcı, İKİ müşteri, İKİ bağlantı, İKİ açık belge.

    Açık belge bir SÜS DEĞİL: tahsis motoru belge kalanını AŞAN bir müşteri
    ödemesini 409 ile reddediyor, yani tahsilatın yazılabilmesi için
    müşterinin gerçekten o kadar açık borcu olmalı.
    """
    an = datetime.now(timezone.utc)
    with motor.begin() as b:
        firma_a = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,true,:t) RETURNING id"),
            {"a": f"{KOSU} WA4 Bir", "t": an},
        ).scalar_one()
        firma_b = b.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,true,:t) RETURNING id"),
            {"a": f"{KOSU} WA4 Iki", "t": an},
        ).scalar_one()
        kul = b.execute(
            text("INSERT INTO app_users(username,email,email_verified,"
                 "display_name,password_hash,role,is_active,"
                 "must_change_password,created_at)"
                 " VALUES(:k,:e,true,:k,'x','admin',true,false,:t) RETURNING id"),
            {"k": f"{KOSU}-wa4", "e": f"{KOSU}-wa4@wa4.invalid", "t": an},
        ).scalar_one()
        for c in (firma_a, firma_b):
            b.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,false,:t)"),
                {"u": kul, "c": c, "t": an},
            )

        def musteri(cid: int, ad: str) -> int:
            return b.execute(
                text("INSERT INTO customers(name,opening_balance,risk_limit,"
                     "payment_term_days,is_active,company_id)"
                     " VALUES(:a,0,0,0,true,:c) RETURNING id"),
                {"a": f"{KOSU} {ad}", "c": cid},
            ).scalar_one()

        mus_a = musteri(firma_a, "Musteri A")
        mus_b = musteri(firma_b, "Musteri B")

        def baglanti(cid: int, tel: str) -> int:
            return b.execute(
                text(f"INSERT INTO {BAGLANTI}(company_id,user_id,phone,"
                     "is_active,created_at,updated_at)"
                     " VALUES(:c,:u,:p,true,:t,:t) RETURNING id"),
                {"c": cid, "u": kul, "p": tel, "t": an},
            ).scalar_one()

        link_a = baglanti(firma_a, NUMARA)
        link_b = baglanti(firma_b, "905331112233")

        for cid, mid in ((firma_a, mus_a), (firma_b, mus_b)):
            b.execute(
                text("""INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,
                    paid_amount,payment_method,company_id
                ) VALUES(
                    :m,'2026-01-01','2026-02-01',200000,'completed',0,
                    'credit',:c
                )"""),
                {"m": mid, "c": cid},
            )
    return {
        "firma_a": int(firma_a), "firma_b": int(firma_b), "kul": int(kul),
        "mus_a": int(mus_a), "mus_b": int(mus_b),
        "link_a": int(link_a), "link_b": int(link_b),
    }


def _kimlik(dunya, firma: str = "firma_a"):
    return SimpleNamespace(company_id=dunya[firma], user_id=dunya["kul"])


def _taslak(db, dunya, tutar: str = "175000"):
    from app.whatsapp.bekleyen import TahsilatHedefi, taslak_olustur
    from app.whatsapp.niyet import TahsilatNiyeti

    return taslak_olustur(
        db, _kimlik(dunya), NUMARA,
        whatsapp_link_id=dunya["link_a"],
        niyet=TahsilatNiyeti(
            tutar=Decimal(tutar), yontem="cash", musteri_terimi="Musteri"
        ),
        hedef=TahsilatHedefi(
            musteri_id=dunya["mus_a"], musteri_adi="Musteri A",
            hesap_id=None, hesap_adi="Kasa",
            acik_bakiye=Decimal("200000"),
        ),
    )


def _ham_taslak(**degerler):
    """CHECK/tekil denemeleri için minimum alan kümesi."""
    temel = {
        "action_type": "TAHSILAT",
        "payload": "{}",
        "status": "PENDING",
        "islem_anahtari": uuid4().hex,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    temel.update(degerler)
    return temel


_EKLE = text(f"""INSERT INTO {TABLO}(
    company_id,user_id,whatsapp_link_id,phone,action_type,payload,status,
    islem_anahtari,claim_token,claim_expires_at,result_id,fail_reason,
    created_at,expires_at,resolved_at
) VALUES(
    :company_id,:user_id,:whatsapp_link_id,:phone,:action_type,:payload,
    :status,:islem_anahtari,:claim_token,:claim_expires_at,:result_id,
    :fail_reason,:created_at,:expires_at,:resolved_at
)""")


def _ekle(baglanti, dunya, **degerler):
    satir = _ham_taslak(
        company_id=dunya["firma_a"], user_id=dunya["kul"],
        whatsapp_link_id=dunya["link_a"], phone=NUMARA, **degerler,
    )
    for bos in ("claim_token", "claim_expires_at", "result_id",
                "fail_reason", "resolved_at"):
        satir.setdefault(bos, None)
    baglanti.execute(_EKLE, satir)


# ------------------------------------------------------------- kisitlar ----


def test_KISMI_TEKIL_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """Kapsamda İKİNCİ aktif taslak PostgreSQL'de de REDDEDİLİYOR.

    HEM RED HEM İZİN ölçülüyor: terminal satır anahtarın DIŞINDA kalmalı,
    yoksa aynı kullanıcı aynı numaradan İKİNCİ KEZ tahsilat yapamazdı.
    """
    indeksler = {i["name"] for i in inspect(motor).get_indexes(TABLO)}
    assert "uq_wpa_aktif_taslak" in indeksler, indeksler

    with motor.begin() as b:
        _ekle(b, dunya, status="PENDING")
    # İKİNCİ AKTİF: RED.
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, status="PENDING")
    # APPLYING de "aktif": RED.
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, status="APPLYING",
                  claim_token="x", claim_expires_at=datetime.now(timezone.utc))

    # TERMINAL satırlar anahtarın DIŞINDA: İZİN.
    with motor.begin() as b:
        b.execute(
            text(f"UPDATE {TABLO} SET status='CANCELLED',resolved_at=now()"
                 " WHERE company_id=:c"), {"c": dunya["firma_a"]},
        )
    with motor.begin() as b:
        _ekle(b, dunya, status="PENDING")
    with motor.begin() as b:
        sayi = b.execute(
            text(f"SELECT count(*) FROM {TABLO} WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
    assert sayi == 2, sayi


def test_DORT_CHECK_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """SQLite CHECK'i yansıtmıyor; burada dördü de ISIRIYOR."""
    # 1) kapalı işlem türü kümesi
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, action_type="TRANSFER")
    # 2) kapalı durum kümesi (`SUPERSEDED` bu depoda YOK)
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, status="SUPERSEDED")
    # 3) APPLIED bir `result_id` TAŞIMAK ZORUNDA
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, status="APPLIED", result_id=None,
                  resolved_at=datetime.now(timezone.utc))
    # 4) terminal satırda lease ARTIĞI kalamaz
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, status="CANCELLED", claim_token="artik",
                  resolved_at=datetime.now(timezone.utc))


def test_ISLEM_ANAHTARI_TEKILI_gercekten_REDDEDIYOR(motor, dunya) -> None:
    """Ödeme idempotensisinin kökü: aynı anahtar İKİ satırda yaşayamaz."""
    anahtar = uuid4().hex
    with motor.begin() as b:
        _ekle(b, dunya, islem_anahtari=anahtar, status="CANCELLED",
              resolved_at=datetime.now(timezone.utc))
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            _ekle(b, dunya, islem_anahtari=anahtar, status="CANCELLED",
                  resolved_at=datetime.now(timezone.utc))


def test_BILESIK_FK_CAPRAZ_KIRACIYI_REDDEDIYOR(motor, dunya) -> None:
    """Bir firmanın taslağı BAŞKA firmanın bağlantısına asılamaz.

    SQLite yabancı anahtarları varsayılan olarak UYGULAMAZ; bu kapı ancak
    burada gerçek.
    """
    with pytest.raises(IntegrityError):
        with motor.begin() as b:
            satir = _ham_taslak(
                company_id=dunya["firma_a"], user_id=dunya["kul"],
                # BAŞKA firmanın bağlantısı.
                whatsapp_link_id=dunya["link_b"], phone=NUMARA,
                claim_token=None, claim_expires_at=None, result_id=None,
                fail_reason=None, resolved_at=None,
            )
            b.execute(_EKLE, satir)


def test_ZAMAN_SUTUNLARI_TIMESTAMPTZ(motor) -> None:
    """Saat dilimi taşımayan `expires_at` süreyi yanlış cevaplardı."""
    sutunlar = {c["name"]: c for c in inspect(motor).get_columns(TABLO)}
    for ad in ("created_at", "expires_at", "resolved_at", "claim_expires_at"):
        assert sutunlar[ad]["type"].timezone is True, ad


# ---------------------------------------------------------------- yaris ----


def test_YIRMI_ESZAMANLI_ONAY_TEK_ODEME_YAZIYOR(motor, dunya) -> None:
    """YİRMİ eşzamanlı `ONAY`dan yalnız BİRİ uygular; ödeme TAM BİR TANE.

    NEYİ ÖLDÜRMEDİĞİ — ÖLÇÜLDÜ, VARSAYILMADI. Bu testin ilk hâlinde
    "CAS düşerse bu test kırmızı olur" YAZIYORDU ve o cümle YANLIŞTI;
    mutasyon bataryası ikisini de YEŞİL bıraktı:

      * CAS'ten `status='PENDING'` yüklemini düşürmek -> BU TEST YEŞİL
        KALIR. Yirmi işçi de claim alır ama tamamlama CAS'i (`claim_token`
        sahipliği) yalnız SONUNCUSUNU geçirir ve ödeme defteri ödemeyi
        TEK tutar. Yani sonuç doğru kalır, kaybolan şey KATMANDIR.
      * Ödeme anahtarını taze bir UUID yapmak -> BU TEST YEŞİL KALIR.
        CAS sağlamken ödeme yoluna zaten TEK işçi giriyor, yani ikinci bir
        anahtarın yazılacağı ikinci bir çağrı hiç doğmuyor.

    Sebep başlıkta yazılı: değişmez ÜÇ BAĞIMSIZ katmanla korunuyor ve
    HERHANGİ BİRİ TEK BAŞINA yetiyor. Bu test ÜÇÜNÜN BİRDEN kaybını
    yakalar; TEK katmanı düşüren mutantlar için AYRI kapılar var ve
    hangisinin hangisini öldürdüğü ölçüldü:

      * CAS         -> `test_YIRMI_ESZAMANLI_CLAIM_TEK_JETON_VERIYOR`
                       (bu dosyada, hemen aşağıda) ve
                       `tests/test_wa4_bekleyen.py::test_CAS_KOSULU_...`
      * anahtar     -> `test_COKME_PENCERESI_IKINCI_ODEME_YAZMIYOR`
                       (bu dosyada) ve SQLite ikizindeki aynı adlı adım
      * jeton sahipliği -> SQLite ikizindeki çökme penceresi adımı
                       (eski jetonun kapatamadığı ölçülüyor)
    """
    from app.whatsapp import bekleyen

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        taslak = _taslak(db, dunya)
    assert taslak.islem.status == "PENDING"

    kapi = Barrier(ESZAMANLI)

    def dene(_):
        with Oturum() as db:
            kapi.wait(timeout=60)
            try:
                return bekleyen.onayla(db, _kimlik(dunya), NUMARA, "ONAY")
            except Exception as hata:  # yarış kaybı istisnaya dönerse say
                return hata

    with ThreadPoolExecutor(max_workers=ESZAMANLI) as havuz:
        sonuclar = list(havuz.map(dene, range(ESZAMANLI)))

    uygulanan = [
        s for s in sonuclar
        if isinstance(s, bekleyen.OnaySonucu) and s.uygulandi
    ]
    assert len(uygulanan) == 1, [type(s).__name__ for s in sonuclar]

    with motor.begin() as b:
        durum = b.execute(
            text(f"SELECT status,result_id FROM {TABLO} WHERE id=:i"),
            {"i": taslak.islem.id},
        ).mappings().one()
        odemeler = b.execute(
            text("SELECT id,amount FROM payments WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).mappings().all()
        # Defterde de TEK iddia var.
        defter = b.execute(
            text("SELECT count(*) FROM payment_idempotency"
                 " WHERE company_id=:c AND operation_type='create_payment'"),
            {"c": dunya["firma_a"]},
        ).scalar_one()

    assert durum["status"] == "APPLIED"
    assert len(odemeler) == 1, odemeler
    assert Decimal(str(odemeler[0]["amount"])) == Decimal("175000")
    assert int(durum["result_id"]) == int(odemeler[0]["id"])
    assert uygulanan[0].payment_id == int(odemeler[0]["id"])
    assert defter == 1, defter



def test_YIRMI_ESZAMANLI_CLAIM_TEK_JETON_VERIYOR(motor, dunya) -> None:
    """CAS KATMANINI TEK BAŞINA ölçer: yirmi `claim_et`ten yalnız BİRİ jeton alır.

    NEDEN AYRI BİR TEST — ölçüldü: `test_YIRMI_ESZAMANLI_ONAY_...` CAS
    düşürüldüğünde YEŞİL KALIYOR, çünkü alttaki iki katman (jeton sahipliği
    ve ödeme defteri) sonucu yine de doğru tutuyor. Bu test o iki katmanı
    DEVREDEN ÇIKARIR: `claim_et`i doğrudan çağırır ve KAÇ jeton dağıtıldığını
    sayar.

    MUTASYON: `claim_et`ten `wpa.c.status == schema.BEKLEYEN_PENDING`
    yüklemini düşürmek bunu KIRMIZI yapar — kazanan satırı APPLYING'e
    çektikten sonra bile ikinci koşul (`expires_at > simdi`) HÂLÂ doğru
    kalır ve yirmi işçinin yirmisi de jeton alır. O hâlde yirmi işçi ödeme
    yoluna girer; bugün ödemeyi tek tutan şey defterdir ve bir gün defter
    değişirse (ya da tür `TAHSILAT` olmayan bir işlem eklenirse) tek koruma
    ORTADAN KALKMIŞ olurdu.
    """
    from app.whatsapp import bekleyen

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        taslak = _taslak(db, dunya)

    kapi = Barrier(ESZAMANLI)

    def dene(_):
        with Oturum() as db:
            kapi.wait(timeout=60)
            try:
                return bekleyen.claim_et(db, _kimlik(dunya), NUMARA, taslak.islem.id)
            except Exception:
                return None

    with ThreadPoolExecutor(max_workers=ESZAMANLI) as havuz:
        jetonlar = [j for j in havuz.map(dene, range(ESZAMANLI)) if j]

    assert len(jetonlar) == 1, len(jetonlar)
    with motor.begin() as b:
        durum = b.execute(
            text(f"SELECT status FROM {TABLO} WHERE id=:i"),
            {"i": taslak.islem.id},
        ).scalar_one()
    assert durum == "APPLYING"


def test_COKME_PENCERESI_IKINCI_ODEME_YAZMIYOR(motor, dunya) -> None:
    """DEFTER KATMANINI TEK BAŞINA ölçer: aynı anahtar İKİNCİ ödeme yazmaz.

    Ödeme yazıldıktan SONRA ama `APPLIED` damgası yazılmadan ÖNCE süreç
    ölürse satır `APPLYING` kalır, lease dolar, devralınır ve uygulama
    YENİDEN denenir. Bu adım tam olarak o pencereyi kurar — ve CAS'i
    devreden çıkarır (tek iş parçacığı), yani ölçtüğü tek şey DEFTERDİR.

    MUTASYON: `_tahsilat_yaz`daki anahtarı `uuid4().hex` yapmak bunu KIRMIZI
    yapar — İKİ ödeme sayılır ve müşteriye iki kez tahsilat işlenir.

    SQLite ikizinde aynı adım var; burada AYRICA ölçülmesinin sebebi
    `_claim_payment_create`in PostgreSQL'de `FOR UPDATE` ile satır kilidi
    almasıdır (`_lock_suffix`) — o kol SQLite'ta HİÇ koşmaz.
    """
    from app.whatsapp import bekleyen

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        taslak = _taslak(db, dunya)
        kimlik = _kimlik(dunya)

        jeton = bekleyen.claim_et(db, kimlik, NUMARA, taslak.islem.id)
        assert jeton is not None
        ilk = bekleyen._tahsilat_yaz(db, kimlik, taslak.islem)

    with motor.begin() as b:
        assert b.execute(
            text("SELECT count(*) FROM payments WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one() == 1
        # Lease'i geçmişe çek: satır DEVRALINABİLİR olsun.
        b.execute(
            text(f"UPDATE {TABLO} SET claim_expires_at=now() - interval '1 minute'"
                 " WHERE id=:i"),
            {"i": taslak.islem.id},
        )

    with Oturum() as db:
        yeni_jeton = bekleyen.claim_et(db, kimlik, NUMARA, taslak.islem.id)
        assert yeni_jeton is not None and yeni_jeton != jeton
        ikinci = bekleyen._tahsilat_yaz(db, kimlik, taslak.islem)
        assert bekleyen.uygulandi(
            db, kimlik, taslak.islem.id, yeni_jeton, result_id=int(ikinci["id"])
        ) is True

    assert int(ikinci["id"]) == int(ilk["id"])
    with motor.begin() as b:
        odemeler = b.execute(
            text("SELECT id FROM payments WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).all()
        durum = b.execute(
            text(f"SELECT status,result_id FROM {TABLO} WHERE id=:i"),
            {"i": taslak.islem.id},
        ).mappings().one()
    assert len(odemeler) == 1, odemeler
    assert durum["status"] == "APPLIED"
    assert int(durum["result_id"]) == int(ilk["id"])


def test_TASLAK_YARISI_TEK_TASLAK_BIRAKIYOR(motor, dunya) -> None:
    """Eşzamanlı İKİ taslak denemesinden yalnız biri geçer (kısmi tekil).

    Hakem uygulama sorgusu DEĞİL veritabanıdır: `SELECT` ile `INSERT`
    arasındaki yarış `IntegrityError`a düşer ve `BekleyenIslemSurmekte`ye
    çevrilir.
    """
    from app.whatsapp import bekleyen

    Oturum = sessionmaker(bind=motor)
    kapi = Barrier(ESZAMANLI)

    def dene(_):
        with Oturum() as db:
            kapi.wait(timeout=60)
            try:
                return _taslak(db, dunya)
            except bekleyen.BekleyenIslemSurmekte:
                return None
            except Exception as hata:
                return hata

    with ThreadPoolExecutor(max_workers=ESZAMANLI) as havuz:
        sonuclar = list(havuz.map(dene, range(ESZAMANLI)))

    acilan = [s for s in sonuclar if isinstance(s, bekleyen.TaslakSonucu)]
    assert len(acilan) == 1, [type(s).__name__ for s in sonuclar]

    with motor.begin() as b:
        sayi = b.execute(
            text(f"SELECT count(*) FROM {TABLO} WHERE company_id=:c"
                 " AND status IN ('PENDING','APPLYING')"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
    assert sayi == 1, sayi


def test_SUPURUCU_KURESEL_kosar_ve_APPLYINGe_DOKUNMAZ(motor, dunya) -> None:
    """Süresi geçmiş `PENDING` kapanır; `APPLYING` KIMILDAMAZ.

    `APPLYING`i kapatmak, ödemesi yazılmış ama damgası yazılamamış bir
    işlemi düşürürdü ve satır bir daha ASLA `APPLIED` olamazdı.
    """
    from app.whatsapp import bekleyen

    gecmis = datetime.now(timezone.utc) - timedelta(minutes=1)
    with motor.begin() as b:
        _ekle(b, dunya, status="PENDING", expires_at=gecmis)
        # AYNI kapsamda ikinci aktif satır olamaz; ikinciyi BAŞKA firmaya
        # yazıyoruz — süpürücünün KÜRESEL koştuğunu da bu gösteriyor.
        satir = _ham_taslak(
            company_id=dunya["firma_b"], user_id=dunya["kul"],
            whatsapp_link_id=dunya["link_b"], phone="905331112233",
            status="APPLYING", expires_at=gecmis,
            claim_token="jeton", claim_expires_at=gecmis,
            result_id=None, fail_reason=None, resolved_at=None,
        )
        b.execute(_EKLE, satir)

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        kapanan = bekleyen.suresi_gecenleri_kapat(db)
    assert kapanan == 1, kapanan

    with motor.begin() as b:
        durumlar = dict(b.execute(
            text(f"SELECT company_id,status FROM {TABLO}")
        ).all())
    assert durumlar[dunya["firma_a"]] == "EXPIRED"
    assert durumlar[dunya["firma_b"]] == "APPLYING"
