"""WA3-full: gelen kuyruğun İŞÇİSİ, niyet YÜRÜTÜCÜSÜ ve giden SAĞLAYICI.

Konu: `app/whatsapp/service.py` (kirala → cevapla → damgala),
`app/whatsapp/yurutucu.py` (araç → gerçek okuma), `app/whatsapp/saglayici.py`
(Meta Cloud API / NoOp), `app/whatsapp/zamanlayici.py` (süreç içi thread) ve
`app/whatsapp/niyet.py`nin İKİ DÜZELTMESİ.

ÖLÇÜLEN EKSİK: WA1 kuyruğu açtı, WA2 numarayı kimliğe bağladı, WA3-core
niyeti çözdü — ama üçünü birbirine bağlayan hiçbir şey yoktu.
`whatsapp_inbound` satırları sonsuza dek `RECEIVED` kalıyordu ve hiçbir
cevap üretilmiyordu. Bu dilim o bağlantıyı kuruyor.

GÖÇ YOK. Kira alanları (`lock_token`, `locked_until`, `attempt_count`) WA1'in
göçünde ZATEN VAR; bu dilim yalnız onların POLİTİKASINI (`schema.LEASE_DAKIKA`,
`schema.MAX_DENEME`) yazıyor.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `_claim`den `lock_token=jeton` yazımını düşürmek
                                -> test_ISCI_BAGSIZ_NUMARAYA_REHBERLIK_DONUYOR
                                   ve BÜTÜN akış adımları KIRMIZI: jetonsuz
                                   satırda `_sonlandir`ın CAS'i HİÇ eşleşmez
                                   ve mesaj `PROCESSING`te asılı kalır.
  * `_claim`den `attempt_count < MAX_DENEME` yüklemini düşürmek
                                -> test_DENEME_TAVANI_ISIRIYOR_ve_DEAD KIRMIZI
                                   (tavana ulaşmış satır yeniden kiralanır).
  * `takilanlari_kapat`ı kaldırmak / eşiği yükseltmek
                                -> AYNI kapı KIRMIZI.
  * `_bagla_akisi`ın rollback dallarından `_sayaci_kalicilastir` çağrısını
    kaldırmak (yani sayacı yalnız DIŞ transaction'da bırakmak)
                                -> test_HIZ_SINIRI_SAYACI_DIS_ROLLBACKI_ASIYOR
                                   KIRMIZI. Bu mercek bulgusunun kapısıdır.
  * `yurutucu`daki bir araçtan kiracı yüklemini düşürmek (ör.
    `musteri_satirlari`a `kimlik.company_id` yerine sabit/başka bir cid
    vermek)
                                -> test_KIRACI_YALITIMI_AYNI_AD_KOMSU_FIRMADA
                                   KIRMIZI (iki firmada AYNI ADLI müşteri,
                                   FARKLI bakiye).
  * `cevap_uret`te tahsilat dalını kaldırmak
                                -> test_YAZMA_NIYETI_WA4E_YONLENDIRILIYOR
                                   KIRMIZI (mesaj sessizce dönem özetine
                                   düşer — WA3-core'un ölçülmüş sınırı).
  * `saglayici_al`ı jeton boşken de Meta döndürmek
                                -> test_JETONSUZ_KURULUM_AGA_CIKMIYOR KIRMIZI
                                   (yamanmış `urllib` çağrı sayacı 0 olmalı).
  * `whatsapp_worker_enabled` varsayılanını `True` yapmak
                                -> test_ISCI_VARSAYILAN_KAPALI KIRMIZI.
  * `_terim_cikar`daki `katli in SORU_SOZLUGU` yüklemini `_kok_eslesir`e
    geri çevirmek
                                -> tests/test_wa3_niyet.py::
                                   test_OKUMA_akisinda_YILMAZ_soyadi_KORUNUYOR
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SERVIS = BACKEND / "app" / "whatsapp" / "service.py"
YURUTUCU = BACKEND / "app" / "whatsapp" / "yurutucu.py"
SAGLAYICI = BACKEND / "app" / "whatsapp" / "saglayici.py"

#: Kanonik numara (`normalize_phone` çıktısı biçiminde).
NUMARA = "905405995959"
IKINCI_NUMARA = "905331112233"

# UYGULAMA İÇE AKTARILMADAN ÖNCE ORTAM KURULUR — `tests/test_wa2_eslestirme.py`
# ile AYNI gerekçe: `app.config.Settings` modül düzeyinde TEK KOPYADIR.
_CALISMA = Path(tempfile.mkdtemp(prefix="wa3-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "wa3.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"

sys.path.insert(0, str(BACKEND))


def _fn(kaynak: str, ad: str) -> ast.FunctionDef:
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            dugum.name == ad
        ):
            return dugum
    raise AssertionError(f"fonksiyon bulunamadı: {ad}")


# =========================================================== STATİK KAPILAR


def test_KUYRUK_SATIRINA_KIRACI_YAZILMIYOR() -> None:
    """`_sonlandir` çağrılarının HİÇBİRİ `company_id`/`user_id` yazmıyor.

    Kaynak (`nazgul_website`) terminal durumla birlikte kiracıyı da
    yazıyordu çünkü ORADAKİ `whatsapp_inbound` o sütunları taşıyor. Bu
    depoda TAŞIMIYOR (göç `20260910_0078` başlığı: platform tablosu) ve bu
    dilim göç AÇMIYOR. Kapı davranışla ölçülemez — olmayan bir sütuna
    yazmak zaten `CompileError` verirdi; ölçülen şey, kimsenin o sütunları
    "eklemek kolay" diye geri getirmemesidir.

    MUTASYON: `_sonlandir(..., company_id=...)` yazan tek bir çağrı bunu
    KIRMIZI yapar.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    cagrilar = [
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Name)
        and d.func.id == "_sonlandir"
    ]
    assert len(cagrilar) >= 8, cagrilar
    for cagri in cagrilar:
        adlar = {kw.arg for kw in cagri.keywords}
        assert "company_id" not in adlar, ast.get_source_segment(kaynak, cagri)
        assert "user_id" not in adlar, ast.get_source_segment(kaynak, cagri)


def test_CLAIM_LOCK_TOKEN_ve_TAVAN_YUKLEMI_YAZILI() -> None:
    """`_claim` HEM jeton yazıyor HEM deneme tavanını yükleme koyuyor.

    İkisi de davranışla da ölçülüyor (aşağıdaki akış adımları), ama kapı
    AST'de çünkü ikisi de SESSİZCE düşürülebilir: jetonsuz bir claim
    "çalışıyor" görünür ta ki iki işçi aynı satıra girene kadar.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    govde = ast.get_source_segment(kaynak, _fn(kaynak, "_claim")) or ""
    assert "lock_token=jeton" in govde
    assert "attempt_count < schema.MAX_DENEME" in govde.replace("\n", " ").replace(
        "whatsapp_inbound.c.attempt_count", "attempt_count"
    )
    assert "db.commit()" in govde


def test_SAGLAYICI_JETONU_HICBIR_GUNLUGE_YAZMIYOR() -> None:
    """Hiçbir `log.*` çağrısı jetonu ya da mesaj metnini ARGÜMAN ALMIYOR.

    Jeton yalnız `Authorization` başlığında görünür. Bir `log.warning(...,
    metin)` eklemek, müşteri mali verisini konteyner günlüğüne dökerdi.
    """
    kaynak = SAGLAYICI.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    yasak = {"jeton", "metin", "token"}
    for dugum in ast.walk(agac):
        if not (isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Attribute)):
            continue
        if not (
            isinstance(dugum.func.value, ast.Name) and dugum.func.value.id == "log"
        ):
            continue
        for arg in dugum.args:
            for ic in ast.walk(arg):
                if isinstance(ic, ast.Name):
                    assert ic.id not in yasak, ast.get_source_segment(kaynak, dugum)


def test_YURUTUCUDE_YAZMA_YOK() -> None:
    """Yürütücüde `insert`/`update`/`delete` ADI HİÇ GEÇMİYOR.

    Yedi aracın hepsi OKUMADIR. Kanaldan çağrılabilen bir yazma yolu, onay
    adımı olmayan bir yazma yolu demekti.
    """
    kaynak = YURUTUCU.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Name):
            assert dugum.id not in {"insert", "update", "delete"}, dugum.id
    duz = kaynak.upper()
    for anahtar in (" INSERT ", " UPDATE ", " DELETE "):
        assert anahtar not in duz, anahtar


def test_YEDI_ARACIN_TAMAMI_YURUTULEBILIYOR() -> None:
    """`ARAC_GOVDELERI`nin anahtarları beyaz listeyle BİREBİR aynı.

    Eksik bir gövde, beyaz listeden geçmiş bir aracın `KeyError` ile DEAD
    üretmesi demekti; fazlası ise beyaz liste dışı bir yol açardı.
    """
    from app.whatsapp import niyet, yurutucu

    assert set(yurutucu.ARAC_GOVDELERI) == set(niyet.ARAC_BEYAZ_LISTESI)


def test_NIYETIN_URETTIGI_HER_DONEM_ETIKETI_COZULUYOR() -> None:
    """`niyet`in üretebileceği ONBİR dönem etiketinin HEPSİ tanınıyor.

    Eksik bir etiket SESSİZCE "bu ay"a düşerdi: kullanıcı "geçen yıl" diye
    sorup BU ayın rakamını alırdı ve cevabın başlığı da "Bu ay" yazardı —
    yani yanlışlık cevabın kendisinde görünmezdi.
    """
    from app.whatsapp import niyet, yurutucu

    uretilen = {e for _, _, e in niyet._DONEM_IKILI} | {
        e for _, e in niyet._DONEM_TEKLI
    }
    assert uretilen <= set(yurutucu.DONEM_ADLARI), uretilen - set(
        yurutucu.DONEM_ADLARI
    )
    bugun = datetime(2026, 5, 15).date()
    for etiket in yurutucu.DONEM_ADLARI:
        aralik = yurutucu.donem_araligi(etiket, bugun)
        assert aralik.baslangic <= aralik.bitis, etiket
        assert aralik.etiket == yurutucu.DONEM_ADLARI[etiket]
    # "SON 7 GÜN" BUGÜNÜ İÇERİR: pencere yedi gündür, altı değil.
    yedi = yurutucu.donem_araligi("son_7_gun", bugun)
    assert (yedi.bitis - yedi.baslangic).days == 6


# ============================================================== DAVRANIŞ ===


@pytest.fixture(scope="module")
def uygulama():
    """Gerçek şemalı uygulama; `TestClient` bağlamı göçleri koşturur."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))

#: `wamid` UNIQUE'tir ve o kısıt idempotency'nin KENDİSİDİR. Zaman damgası
#: yeterince ayırt edici DEĞİL (ölçüldü: aynı mikrosaniyede iki satır);
#: sayaç testleri birbirinden kesin olarak ayırır.
_WAMID = iter(range(1, 100_000))


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma, İKİ kullanıcı ve İKİ FİRMADA DA AYNI ADLI müşteri.

    Aynı ad BİLEREK: kiracı yalıtımı ancak komşuda AYNI ADI taşıyan bir
    kayıt varken ölçülebilir. Yüklemi düşüren bir mutant, adı farklı olan
    bir kurguda "bulunamadı" der ve testi yine geçerdi.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    def temizle(db):
        for tablo in (
            "whatsapp_context", "whatsapp_pairing_codes", "whatsapp_links",
            "whatsapp_pairing_attempts", "whatsapp_inbound",
            "order_items", "orders", "customers", "products",
        ):
            db.execute(text(f"DELETE FROM {tablo}"))
        db.commit()

    n = next(_SAYAC)
    with SessionLocal() as db:
        temizle(db)
        an = datetime.now(timezone.utc)
        firma_a = db.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,1,:t) RETURNING id"),
            {"a": f"WA3 Bir {n}", "t": an},
        ).scalar_one()
        firma_b = db.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,1,:t) RETURNING id"),
            {"a": f"WA3 Iki {n}", "t": an},
        ).scalar_one()

        def kullanici(kad: str) -> int:
            return db.execute(
                text("INSERT INTO app_users(username,email,email_verified,"
                     "display_name,password_hash,role,is_active,"
                     "must_change_password,created_at)"
                     " VALUES(:k,:e,1,:k,'x','admin',1,0,:t) RETURNING id"),
                {"k": kad, "e": kad + "@wa3.invalid", "t": an},
            ).scalar_one()

        kul_a = kullanici(f"wa3-a-{n}")
        kul_b = kullanici(f"wa3-b-{n}")
        for u, c in ((kul_a, firma_a), (kul_a, firma_b), (kul_b, firma_a)):
            db.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,0,:t)"),
                {"u": u, "c": c, "t": an},
            )

        # AYNI AD, İKİ FİRMA, FARKLI BAKİYE.
        for cid, bakiye in ((firma_a, "1500"), (firma_b, "9900")):
            db.execute(
                text("INSERT INTO customers(company_id,name,opening_balance,"
                     "risk_limit,payment_term_days,is_active)"
                     " VALUES(:c,:a,:b,0,0,1)"),
                {"c": cid, "a": "Şaban Korkmaz", "b": bakiye},
            )
        db.commit()

    veri = {
        "firma_a": int(firma_a), "firma_b": int(firma_b),
        "kul_a": int(kul_a), "kul_b": int(kul_b),
    }
    yield veri
    with SessionLocal() as db:
        temizle(db)


@pytest.fixture()
def oturum(dunya):
    """`dunya`YA BAĞLI: teardown sırasını belirliyor (WA2 ile aynı tuzak)."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


def _baglanti_yaz(db, cid: int, uid: int, telefon: str = NUMARA) -> None:
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    db.execute(
        text("INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
             "created_at,updated_at) VALUES(:c,:u,:p,1,:t,:t)"),
        {"c": cid, "u": uid, "p": telefon, "t": an},
    )
    db.commit()


def _mesaj_yaz(
    db,
    metin: str,
    *,
    telefon: str = NUMARA,
    wamid: str | None = None,
    durum: str = "RECEIVED",
    deneme: int = 0,
    kilit: datetime | None = None,
    medya: str | None = None,
    numara_kimligi: str | None = None,
) -> int:
    from sqlalchemy import text

    from app.config import settings

    an = datetime.now(timezone.utc)
    return int(
        db.execute(
            text("INSERT INTO whatsapp_inbound(wamid,sender_phone,phone_number_id,"
                 "text,media_id,status,attempt_count,locked_until,received_at)"
                 " VALUES(:w,:p,:n,:m,:md,:s,:d,:k,:t) RETURNING id"),
            {
                "w": wamid or f"wamid-{next(_WAMID)}",
                "p": telefon,
                "n": (
                    numara_kimligi
                    if numara_kimligi is not None
                    else (settings.whatsapp_phone_number_id or "")
                ),
                "m": metin,
                "md": medya,
                "s": durum,
                "d": deneme,
                "k": kilit,
                "t": an,
            },
        ).scalar_one()
    )


def _satir(db, satir_id: int):
    from sqlalchemy import text

    return db.execute(
        text("SELECT status,attempt_count,lock_token,locked_until,last_error"
             " FROM whatsapp_inbound WHERE id=:i"),
        {"i": satir_id},
    ).mappings().one()


class _SahteSaglayici:
    """Ağa çıkmaz; gönderilenleri biriktirir ve İSTENİRSE patlar."""

    def __init__(self, hata: Exception | None = None) -> None:
        self.gonderilenler: list[tuple[str, str]] = []
        self.hata = hata

    def metin_gonder(self, alici: str, metin: str) -> None:
        if self.hata is not None:
            raise self.hata
        self.gonderilenler.append((alici, metin))


# ---------------------------------------------------------------- claim ---


def test_YIRMI_ISCI_TEK_SATIRA_GIRSE_BIR_TANESI_KIRALIYOR(dunya) -> None:
    """Yirmi eşzamanlı `_claim`, TAM BİR kazanan bırakıyor.

    SQLite tek yazarlıdır, yani bu adım PostgreSQL'deki gerçek yarışın
    yerini TUTMAZ — ikizi (`test_wa3_worker_postgresql.py`) o işi yapar.
    Burada ölçülen şey CAS'in KENDİSİDİR: `rowcount == 1` yüklemi
    düşerse yirmi işçinin hepsi jeton alır ve aynı mesaj yirmi kez
    cevaplanır.
    """
    from app.db import SessionLocal
    from app.whatsapp import service

    with SessionLocal() as db:
        satir_id = _mesaj_yaz(db, "bu ay ciro")
        db.commit()

    kapi = threading.Barrier(20)
    jetonlar: list[str | None] = []
    kilit = threading.Lock()

    def kirala() -> None:
        with SessionLocal() as db:
            db.execute  # bağlantı ısınması: kira YARIŞTAN ÖNCE kurulsun
            kapi.wait(timeout=30)
            jeton = service._claim(db, satir_id)
        with kilit:
            jetonlar.append(jeton)

    isler = [threading.Thread(target=kirala) for _ in range(20)]
    for i in isler:
        i.start()
    for i in isler:
        i.join(timeout=60)

    kazananlar = [j for j in jetonlar if j is not None]
    assert len(kazananlar) == 1, jetonlar
    with SessionLocal() as db:
        satir = _satir(db, satir_id)
    assert satir["status"] == "PROCESSING"
    assert satir["lock_token"] == kazananlar[0]
    assert satir["attempt_count"] == 1


def test_SURESI_DOLMUS_KIRA_DEVRALINIYOR(oturum, dunya) -> None:
    """Süresi geçmiş `PROCESSING` satırı başka bir işçiye DÜŞER.

    Bu dal olmasaydı çöken bir işçinin satırı sonsuza dek kilitli kalırdı
    ve kullanıcı cevabını HİÇ almazdı.
    """
    from app.whatsapp import service

    gecmis = datetime.now(timezone.utc) - timedelta(minutes=1)
    satir_id = _mesaj_yaz(oturum, "bu ay ciro", durum="PROCESSING", kilit=gecmis)
    oturum.commit()
    assert service._claim(oturum, satir_id) is not None

    # Taze kira DEVRALINAMAZ.
    gelecek = datetime.now(timezone.utc) + timedelta(minutes=5)
    ikinci = _mesaj_yaz(oturum, "bu ay ciro", durum="PROCESSING", kilit=gelecek)
    oturum.commit()
    assert service._claim(oturum, ikinci) is None


def test_DENEME_TAVANI_ISIRIYOR_ve_DEAD(oturum, dunya) -> None:
    """Tavana ulaşmış satır kiralanamaz ve `takilanlari_kapat` onu DEAD yapar.

    MUTASYON: `_claim`den tavan yüklemini düşürmek ya da
    `takilanlari_kapat`ın eşiğini kaldırmak bunu KIRMIZI yapar — düşen bir
    sağlayıcıyla satır SONSUZA DEK denenirdi.
    """
    from app.whatsapp import schema, service

    satir_id = _mesaj_yaz(oturum, "bu ay ciro", deneme=schema.MAX_DENEME)
    oturum.commit()
    assert service._claim(oturum, satir_id) is None
    assert service.takilanlari_kapat(oturum) == 1
    assert _satir(oturum, satir_id)["status"] == schema.DEAD
    # İKİNCİ ÇAĞRI HİÇBİR ŞEY YAPMAZ: DEAD satır RECEIVED değildir.
    assert service.takilanlari_kapat(oturum) == 0


def test_GECICI_HATA_SATIRI_RECEIVEDE_DONDURUYOR(oturum, dunya) -> None:
    """Gönderim geçici hatası: satır RECEIVED, kira BIRAKILMIŞ, sayaç ARTMIŞ."""
    from app.whatsapp import saglayici as sag, service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    satir_id = _mesaj_yaz(oturum, "bu ay ciro")
    oturum.commit()

    saglayici = _SahteSaglayici(hata=sag.GonderimHatasi("ag"))
    islenen = service.bekleyenleri_isle(oturum, saglayici=saglayici)
    assert islenen == 0  # işlenmedi: yeniden denenecek
    satir = _satir(oturum, satir_id)
    assert satir["status"] == "RECEIVED"
    assert satir["lock_token"] is None and satir["locked_until"] is None
    assert satir["attempt_count"] == 1
    assert satir["last_error"] == "GonderimHatasi"


def test_KALICI_GONDERIM_HATASI_DEAD(oturum, dunya) -> None:
    """4xx: yeniden denemenin düzeltemeyeceği hata satırı DEAD yapar."""
    from app.whatsapp import saglayici as sag, service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    satir_id = _mesaj_yaz(oturum, "bu ay ciro")
    oturum.commit()

    saglayici = _SahteSaglayici(hata=sag.KaliciGonderimHatasi("http 400"))
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    satir = _satir(oturum, satir_id)
    assert satir["status"] == "DEAD"
    assert satir["last_error"] == "KaliciGonderimHatasi"


# ------------------------------------------------------------ akış -------


def test_ISCI_BAGSIZ_NUMARAYA_REHBERLIK_DONUYOR(oturum, dunya) -> None:
    """Bağlı OLMAYAN numara: ERP verisi YOK, eşleştirme rehberliği VAR.

    Sessizlik DEĞİL — bu depoda somut bir çıkış yolu var (`BAĞLA <KOD>`).
    Köprü KAPALI (varsayılan), yani hiçbir ağ çağrısı da yapılmıyor.
    """
    from app.whatsapp import kopru, service

    assert not kopru.acik_mi()
    satir_id = _mesaj_yaz(oturum, "Şaban Korkmaz borç")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert _satir(oturum, satir_id)["status"] == "ANSWERED"
    (alici, cevap), = saglayici.gonderilenler
    assert alici == NUMARA
    assert cevap == service.BAGSIZ_MESAJI
    assert "BAĞLA" in cevap
    # ERP verisi SIZMIYOR: komşu firmadaki müşterinin adı da bakiyesi de yok.
    assert "Korkmaz" not in cevap and "1.500" not in cevap


def test_KOPRU_ACIKKEN_BAGSIZ_NUMARA_KOPRUYE_GIDIYOR(
    oturum, dunya, monkeypatch
) -> None:
    """Köprü yapılandırılmışsa cevap ORADAN gelir — ama ERP'den DEĞİL.

    Köprü teknik danışmandır; kimlik ÇÖZÜLMEMİŞTİR, yani hiçbir firmanın
    verisine erişemez. Sahte gönderici ağa çıkmayı da imkânsız kılıyor.
    """
    from app.config import settings
    from app.whatsapp import kopru, service

    monkeypatch.setattr(settings, "harman_kopru_url", "https://kopru.invalid/sor")
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "harman_kopru_sirri", SecretStr("s" * 32))

    cagrilar: list[bytes] = []

    def sahte_gonderici(url, govde, basliklar, zaman_asimi):
        cagrilar.append(govde)
        return b'{"metin": "Kurulum icin destek ekibine yazin."}'

    gercek = kopru.KopruIstemcisi

    def sahte_istemci(*a, **k):
        return gercek(gonderici=sahte_gonderici)

    monkeypatch.setattr(kopru, "KopruIstemcisi", sahte_istemci)

    satir_id = _mesaj_yaz(oturum, "sisteminiz nasil calisiyor")
    oturum.commit()
    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert _satir(oturum, satir_id)["status"] == "ANSWERED"
    assert saglayici.gonderilenler[0][1] == "Kurulum icin destek ekibine yazin."
    assert len(cagrilar) == 1


def test_IKI_FIRMAYA_BAGLI_NUMARA_FIRMA_SEC_ISTIYOR(oturum, dunya) -> None:
    """Çok firmalı numara: RASTGELE SEÇİM YOK, kullanıcıya SORULUR.

    Ve seçim yapıldıktan sonra AYNI soru cevaplanabiliyor — bağlam
    okunuyor demektir.
    """
    from app.whatsapp import baglam, service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_a"])
    _baglanti_yaz(oturum, dunya["firma_b"], dunya["kul_a"])
    _mesaj_yaz(oturum, "Şaban Korkmaz borç", wamid="wa3-cok-1")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler[-1][1] == baglam.FIRMA_SECIN_MESAJI

    # FİRMA SEÇ 1 → birinci firma (sıra `company_id` artan).
    _mesaj_yaz(oturum, "FİRMA SEÇ 1", wamid="wa3-cok-2")
    oturum.commit()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler[-1][1].startswith("Aktif firma:")

    _mesaj_yaz(oturum, "Şaban Korkmaz borç", wamid="wa3-cok-3")
    oturum.commit()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    cevap = saglayici.gonderilenler[-1][1]
    assert "Şaban Korkmaz" in cevap
    # BİRİNCİ firmanın bakiyesi (1.500), İKİNCİNİNKİ (9.900) DEĞİL.
    assert "1.500,00" in cevap and "9.900" not in cevap


def test_KIRACI_YALITIMI_AYNI_AD_KOMSU_FIRMADA(oturum, dunya) -> None:
    """AYNI ADLI müşteri iki firmada; cevap YALNIZ bağlı firmanınkini taşır.

    MUTASYON ADIYLA: `yurutucu.cari_durum`daki `kimlik.company_id`yi
    başka bir değerle (ya da yüklemi düşürerek) değiştirmek bunu KIRMIZI
    yapar. Adların AYNI olması şart: farklı olsalardı yüklemsiz bir sorgu
    "bulunamadı" der ve testi geçerdi.
    """
    from app.whatsapp import service

    _baglanti_yaz(oturum, dunya["firma_b"], dunya["kul_a"])
    _mesaj_yaz(oturum, "Şaban Korkmaz bakiye")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    cevap = saglayici.gonderilenler[-1][1]
    assert "Şaban Korkmaz" in cevap
    assert "9.900,00" in cevap, cevap
    assert "1.500" not in cevap, cevap


def test_YAZMA_NIYETI_WA4E_YONLENDIRILIYOR(oturum, dunya) -> None:
    """Tahsilat niyeti bu turda ÇALIŞTIRILMAZ; kullanıcı SEBEBİ öğrenir.

    İKİ AYRI GİRDİ ve ikisi de WA3-core'un ölçülmüş sınırının kapanışı:

    * tam kalıp ("... 500 TL nakit tahsilat") → WA4 cevabı;
    * çıplak "tahsilat" → REHBERLİK (WA3-core'da SESSİZCE dönem özetine
      düşüyordu; `WA3_BILINEN_SINIRLAR.md` 2. satır).
    """
    from app.whatsapp import niyet, service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    _mesaj_yaz(oturum, "Şaban Korkmaz 500 TL nakit tahsilat", wamid="wa3-y1")
    oturum.commit()
    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler[-1][1] == service.WA4_MESAJI
    assert "WA4" in service.WA4_MESAJI

    _mesaj_yaz(oturum, "tahsilat", wamid="wa3-y2")
    oturum.commit()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler[-1][1] == niyet.TAHSILAT_REHBERI

    # HİÇBİR finansal kayıt oluşmadı.
    from sqlalchemy import text

    assert oturum.execute(
        text("SELECT COUNT(*) FROM payments")
    ).scalar_one() == 0


def test_BOS_METIN_SESSIZCE_KAPANIYOR_MEDYA_ARTIK_CEVAPLANIYOR(
    oturum, dunya
) -> None:
    """Boş metin hâlâ IGNORED; MEDYA ARTIK CEVAPLANIYOR (WA5'te DEĞİŞTİ).

    WA3 bu ikisini AYNI dala koyuyordu ("medya bu turda işlenmiyor") ve
    medya satırı `last_error='medya'` ile IGNORED kapanıyordu. Gerekçesi
    yazılıydı: indirilen baytı okuyacak çağıran YOKTU. WA5 o çağıranı
    getirdi (`app/whatsapp/fatura.py`), yani gerekçe DÜŞTÜ ve dal AYRILDI.

    Boş metnin sözleşmesi DEĞİŞMEDİ ve bu ayrım önemli: `not metin`
    yüklemi artık `media_id` ile birlikte okunuyor, dolayısıyla ALTYAZISIZ
    bir fotoğraf (metni boş string) artık "boş mesaj" SAYILMIYOR. Boş ve
    medyasız satır ise hâlâ sessizce kapanır.

    Fatura yolunun kendisi `tests/test_wa5_fatura.py`de ölçülüyor; burada
    ölçülen, WA3'ün eski sözleşmesinin HANGİ YARISININ durduğudur.
    """
    from app.whatsapp import service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    medya_id = _mesaj_yaz(oturum, "", wamid="wa3-m1", medya="MEDIA-1")
    bos_id = _mesaj_yaz(oturum, "   ", wamid="wa3-m2")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 2

    # Boş metin: DEĞİŞMEDİ.
    assert _satir(oturum, bos_id)["status"] == "IGNORED"

    # Medya: artık TERMİNAL ama CEVAPLI. Sağlayıcı `medya_indir`i
    # tanımıyor (WA3'ün sahtesi) — `fatura` bunu yutar ve kullanıcıya
    # indirme hatası döner. ÖLÇÜLEN ŞEY: istisna SIZMIYOR ve satır DEAD
    # OLMUYOR.
    assert _satir(oturum, medya_id)["status"] == "ANSWERED"
    assert _satir(oturum, medya_id)["last_error"] is None
    (alici, cevap), = saglayici.gonderilenler
    assert cevap


def test_GECERSIZ_NUMARA_CEVAPLANMIYOR(oturum, dunya) -> None:
    """E.164'e indirgenemeyen numaraya cevap GÖNDERİLMEZ.

    Kırpılmış bir numaraya cevap göndermek, YANLIŞ KİŞİYE göndermek
    olabilirdi.
    """
    from app.whatsapp import service

    satir_id = _mesaj_yaz(oturum, "bu ay ciro", telefon="12345")
    oturum.commit()
    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler == []
    assert _satir(oturum, satir_id)["last_error"] == "TelefonGecersiz"


def test_YABANCI_PHONE_NUMBER_ID_SESSIZCE_KAPANIYOR(
    oturum, dunya, monkeypatch
) -> None:
    """Bizim işletme numaramıza gelmemiş mesaj ERP'ye HİÇ dokunmaz."""
    from app.config import settings
    from app.whatsapp import service

    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "BIZIM")
    satir_id = _mesaj_yaz(oturum, "bu ay ciro", numara_kimligi="BASKASININ")
    oturum.commit()
    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler == []
    assert _satir(oturum, satir_id)["last_error"] == "phone_number_id"


# --------------------------------------------------- HIZ SINIRI SAYACI ---


def test_HIZ_SINIRI_SAYACI_DIS_ROLLBACKI_ASIYOR(oturum, dunya) -> None:
    """MERCEK BULGUSUNUN KAPISI: sayaç dış rollback'te KAYBOLMUYOR.

    `eslestirme.deneme_say` ÇAĞIRANIN transaction'ında yazar. İşçi
    gönderim hatasında `db.rollback()` çağırıyor — ve o rollback sayacı da
    geri alıyordu. Sonuç: DÜŞEN BİR SAĞLAYICIYLA hız sınırı HİÇ ısırmazdı,
    yani sınır tam da saldırının işe yaradığı koşulda kaybolurdu.

    Burada tam o kurgu koşuluyor: geçerli olmayan bir `BAĞLA` mesajı,
    gönderimi PATLAYAN bir sağlayıcıyla işleniyor. Dıştaki transaction
    geri alınıyor; sayaç AYRI VE KISA bir işlemde kalıcı kalıyor.

    MUTASYON: `_bagla_akisi`ın rollback dallarından `_sayaci_kalicilastir`
    çağrısını kaldırmak bunu KIRMIZI yapar (sayaç 0 kalır).
    """
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.whatsapp import saglayici as sag, service

    _mesaj_yaz(oturum, "BAĞLA ABCD-EFGH-JKMN")
    oturum.commit()

    saglayici = _SahteSaglayici(hata=sag.GonderimHatasi("ag"))
    assert service.bekleyenleri_isle(
        oturum, saglayici=saglayici, oturum_fabrikasi=SessionLocal
    ) == 0

    with SessionLocal() as db:
        sayac = db.execute(
            text("SELECT COALESCE(SUM(attempt_count),0)"
                 " FROM whatsapp_pairing_attempts WHERE phone=:p"),
            {"p": NUMARA},
        ).scalar_one()
    # TAM BİR: dıştaki artış geri alındı, ayrı işlemdeki kaldı. İki artış
    # olsaydı sınır olması gerekenden HIZLI ısırırdı.
    assert int(sayac) == 1, sayac


def test_HIZ_SINIRI_CEVAP_ESIGI_USTUNDE_SESSIZ(oturum, dunya) -> None:
    """Cevap eşiğinin üstündeki `BAĞLA` denemesi IGNORED ve MESAJSIZ.

    Sayaç bu dalda DIŞ transaction'da yazılır ve `_sonlandir` onu commit
    eder — ayrı işleme gerek YOKTUR ve çağrılmaz.
    """
    from sqlalchemy import text

    from app.whatsapp import schema, service

    from app.whatsapp import eslestirme

    # SAYAÇ ÜRETİMİN KENDİ YOLUYLA DOLDURULUYOR, elle INSERT ile DEĞİL.
    # ÖLÇÜLMÜŞ TUZAK: elle yazılan `window_start` damgası SQLite'ta farklı
    # metne serileşiyor, UPSERT çakışma hedefi TUTMUYOR ve sayaç sıfırdan
    # başlıyordu — yani test sınırı ölçtüğünü sanarken hiçbir şey ölçmüyordu.
    for _ in range(schema.PAIRING_CEVAP_SINIRI):
        eslestirme.deneme_say(oturum, NUMARA)
    oturum.commit()
    satir_id = _mesaj_yaz(oturum, "BAĞLA ABCD-EFGH-JKMN")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler == []
    assert _satir(oturum, satir_id)["status"] == "IGNORED"


def test_BAGLA_AKISI_UCTAN_UCA(oturum, dunya) -> None:
    """Geçerli kod: bağlantı doğuyor, başarı mesajı gidiyor, satır ANSWERED."""
    from app.whatsapp import eslestirme, service

    uretilen = eslestirme.kod_uret(
        oturum, dunya["firma_a"], dunya["kul_b"], hedef_telefon=NUMARA
    )
    oturum.commit()
    satir_id = _mesaj_yaz(oturum, f"BAĞLA {uretilen.kod}")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert saglayici.gonderilenler[-1][1] == eslestirme.BASARI_MESAJI
    assert _satir(oturum, satir_id)["status"] == "ANSWERED"
    kimlikler = eslestirme.kimlik_coz(oturum, NUMARA)
    assert [k.company_id for k in kimlikler] == [dunya["firma_a"]]


# ------------------------------------------------------------ yürütücü ---


def test_YEDI_ARAC_GERCEK_VERIYLE_CEVAP_URETIYOR(oturum, dunya) -> None:
    """Yedi aracın HEPSİ koşuyor ve `cevap_yaz` HİÇBİRİNDE patlamıyor.

    Şablon ile araç sözlüğü ayrışırsa (`KeyError`, eksik anahtar) mesaj
    DEAD olurdu ve kullanıcı hiçbir şey almazdı. Burada ölçülen şey
    içeriğin doğruluğu değil, YEDİ YOLUN DA UÇTAN UCA KOŞMASI.
    """
    from app.whatsapp import eslestirme, niyet, yurutucu

    kimlik = eslestirme.Kimlik(
        company_id=dunya["firma_a"], user_id=dunya["kul_b"]
    )
    kosucu = yurutucu.VeritabaniYurutucu(oturum, kimlik)
    argumanlar = {
        "cari_durum": {"musteri": "Şaban"},
        "parca_stok": {"arama": "filtre"},
        "donem_ozeti": {"donem": "bu_ay"},
        "alacak_yaslandirma": {},
        "kritik_stok": {},
        "en_cok_satan_parcalar": {"donem": "bu_yil"},
        "parca_satis_gecmisi": {"arama": "bicak", "donem": "bu_yil"},
    }
    assert set(argumanlar) == set(niyet.ARAC_BEYAZ_LISTESI)
    for arac, arg in argumanlar.items():
        veri = kosucu.kos(arac, arg)
        metin = niyet.cevap_yaz(arac, veri)
        assert isinstance(metin, str) and metin.strip(), arac


def test_BEYAZ_LISTE_DISI_ARAC_REDDEDILIYOR(oturum, dunya) -> None:
    """Yürütücü beyaz liste dışı bir aracı `KeyError` ile reddeder."""
    from app.whatsapp import eslestirme, yurutucu

    kosucu = yurutucu.VeritabaniYurutucu(
        oturum, eslestirme.Kimlik(company_id=dunya["firma_a"], user_id=dunya["kul_b"])
    )
    with pytest.raises(KeyError):
        kosucu.kos("tahsilat_taslak", {})


def test_COK_ESLESMEDE_LISTE_DONUYOR_TEK_MUSTERI_DEGIL(oturum, dunya) -> None:
    """İki müşteri eşleşirse rakam DEĞİL, numaralı LİSTE döner.

    Yanlış müşterinin bakiyesini "bulundu" diye dönmek, bir sayıyı yanlış
    kişiye söylemek olurdu.
    """
    from sqlalchemy import text

    from app.whatsapp import service

    oturum.execute(
        text("INSERT INTO customers(company_id,name,opening_balance,risk_limit,"
             "payment_term_days,is_active) VALUES(:c,:a,7,0,0,1)"),
        {"c": dunya["firma_a"], "a": "Şaban Demir"},
    )
    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    _mesaj_yaz(oturum, "Şaban borç")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    cevap = saglayici.gonderilenler[-1][1]
    assert cevap.startswith(service.COK_ESLESME_BASLIGI)
    assert "1) " in cevap and "2) " in cevap
    # Hiçbir bakiye SIZMIYOR: seçim yapılmadan rakam verilmez.
    assert "1.500" not in cevap


# ----------------------------------------------------------- sağlayıcı ---


def test_JETONSUZ_KURULUM_AGA_CIKMIYOR(monkeypatch) -> None:
    """Jeton boşken sağlayıcı NoOp'tur ve `urllib` HİÇ çağrılmaz.

    `urllib.request.urlopen` yamalanıp SAYILIYOR: "ağa çıkmadı" iddiası
    ölçülüyor, varsayılmıyor.
    """
    import urllib.request

    from app.config import settings
    from app.whatsapp import saglayici as sag

    cagri: list[int] = []
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: cagri.append(1) or (_ for _ in ()).throw(
            AssertionError("AGA CIKILDI")
        ),
    )
    monkeypatch.setattr(settings, "whatsapp_access_token", None)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")

    gonderici = sag.saglayici_al()
    assert isinstance(gonderici, sag.NoOpSaglayici)
    gonderici.metin_gonder(NUMARA, "merhaba")
    assert cagri == []
    assert gonderici.gonderilenler[-1] == (NUMARA, "merhaba")


def test_JETON_DOLUYKEN_META_SAGLAYICISI_ve_ADRES(monkeypatch) -> None:
    """Jeton + numara doluyken Meta sağlayıcısı kurulur; adres ve gövde ölçülür.

    Ağa yine ÇIKILMIYOR: `urlopen` yamalı ve isteğin BAŞLIKLARI okunuyor.
    """
    import json
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings
    from app.whatsapp import saglayici as sag

    istekler: list[object] = []

    class _Yanit:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            return b"{}"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda istek, timeout=None: (
            istekler.append(istek) or _Yanit()
        )
    )
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("JETON123"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "5550001")
    monkeypatch.setattr(settings, "whatsapp_graph_base_url", "https://graph.invalid")
    monkeypatch.setattr(settings, "whatsapp_graph_version", "v21.0")

    gonderici = sag.saglayici_al()
    assert isinstance(gonderici, sag.MetaBulutSaglayici)
    gonderici.metin_gonder(NUMARA, "cevap")

    istek, = istekler
    assert istek.full_url == "https://graph.invalid/v21.0/5550001/messages"
    assert istek.get_header("Authorization") == "Bearer JETON123"
    govde = json.loads(istek.data.decode("utf-8"))
    assert govde["to"] == NUMARA and govde["text"]["body"] == "cevap"
    assert govde["text"]["preview_url"] is False


def test_HTTP_HATALARI_SINIFLANIYOR(monkeypatch) -> None:
    """429 → kota (denenebilir), 4xx → kalıcı, 5xx → geçici.

    Sınıflandırma işçinin sözleşmesidir: kalıcı hata DEAD yapar, geçici
    hata satırı RECEIVED'a döndürür. Yanlış sınıflandırma ya sonsuz
    yeniden deneme ya da sessizce kaybolan bir cevap demekti.
    """
    import urllib.error
    import urllib.request

    from pydantic import SecretStr

    from app.config import settings
    from app.whatsapp import saglayici as sag

    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("J"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1")

    def yamala(durum: int) -> None:
        def patla(*a, **k):
            raise urllib.error.HTTPError("u", durum, "x", None, None)

        monkeypatch.setattr(urllib.request, "urlopen", patla)

    gonderici = sag.MetaBulutSaglayici()
    for durum, sinif in (
        (429, sag.KotaHatasi),
        (400, sag.KaliciGonderimHatasi),
        (401, sag.KaliciGonderimHatasi),
        (500, sag.GonderimHatasi),
    ):
        yamala(durum)
        with pytest.raises(sinif):
            gonderici.metin_gonder(NUMARA, "x")


# ---------------------------------------------------------- zamanlayıcı --


def test_ISCI_VARSAYILAN_KAPALI_hicbir_thread_acmiyor() -> None:
    """`whatsapp_worker_enabled` varsayılan `False` ve thread AÇILMAZ.

    Kapalı kanalın HİÇBİR thread açmaması ÖLÇÜLEBİLİR olmalı;
    `field_stock_outbox_enabled` ile aynı sınıftan bir karar.
    """
    from app.config import Settings, settings
    from app.whatsapp import zamanlayici

    assert Settings.model_fields["whatsapp_worker_enabled"].default is False
    assert settings.whatsapp_worker_enabled is False
    assert zamanlayici._thread is None
    assert not any(
        t.name == "whatsapp-worker-scheduler" for t in threading.enumerate()
    )


def test_ZAMANLAYICI_TEK_TUR_KALP_ATISI_YAZIYOR(oturum, dunya) -> None:
    """Bir tur koşup `settings` satırına kalp atışı yazıyor; GÖÇ YOK."""
    from sqlalchemy import text

    from app.whatsapp import zamanlayici

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    _mesaj_yaz(oturum, "bu ay ciro")
    oturum.commit()

    islenen = zamanlayici.bir_dongu_calistir()
    assert islenen == 1
    ham = oturum.execute(
        text("SELECT value FROM settings WHERE key=:k"),
        {"k": zamanlayici.KALP_ANAHTARI},
    ).scalar_one()
    import json

    kayit = json.loads(ham)
    assert kayit["messages_processed"] == 1
    assert kayit["last_error"] is None
    canli = zamanlayici.canlilik(oturum)
    assert canli["enabled"] is False and canli["stale"] is False


def test_ISCI_BATCH_SINIRI_ISIRIYOR(oturum, dunya) -> None:
    """`en_fazla` tek turda işlenen satır sayısını GERÇEKTEN sınırlıyor."""
    from app.whatsapp import service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    for i in range(4):
        _mesaj_yaz(oturum, "bu ay ciro", wamid=f"wa3-batch-{i}")
    oturum.commit()

    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, en_fazla=2, saglayici=saglayici) == 2
    assert len(saglayici.gonderilenler) == 2
    assert service.bekleyenleri_isle(oturum, en_fazla=10, saglayici=saglayici) == 2


def test_YENIDEN_ISLEME_YOK_ANSWERED_SATIRLAR_ADAY_DEGIL(oturum, dunya) -> None:
    """Terminal satır bir daha ADAY OLMAZ — yinelenen cevap üretilmez."""
    from app.whatsapp import service

    _baglanti_yaz(oturum, dunya["firma_a"], dunya["kul_b"])
    _mesaj_yaz(oturum, "bu ay ciro")
    oturum.commit()
    saglayici = _SahteSaglayici()
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 1
    assert service.bekleyenleri_isle(oturum, saglayici=saglayici) == 0
    assert len(saglayici.gonderilenler) == 1


def test_DECIMAL_SOZLESMESI_yeni_modullerde_float_ADI_YOK() -> None:
    """Üç yeni modülde `float` ADI HİÇ geçmiyor (`test_v2_9_decimal_contract`).

    Kapı zaten var; burada erken ve adıyla ölçülüyor ki yeni bir modül
    eklerken kural hatırlansın.
    """
    for yol in (SERVIS, YURUTUCU, SAGLAYICI,
                BACKEND / "app" / "whatsapp" / "zamanlayici.py"):
        kaynak = yol.read_text(encoding="utf-8")
        for dugum in ast.walk(ast.parse(kaynak)):
            if isinstance(dugum, ast.Name):
                assert dugum.id != "float", yol.name
            if isinstance(dugum, ast.Attribute):
                assert dugum.attr != "float", yol.name
