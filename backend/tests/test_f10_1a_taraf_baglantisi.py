"""WHATSAPP TARAF BAĞLANTISI: numara → CARİ defteri (F10-1a).

Konu: göç `20260918_0090`, `app/whatsapp/schema.py`nin İKİ yeni KİRACI
tablosu, `app/whatsapp/taraf.py`, `app/routers/whatsapp.py`nin DÖRT yeni ucu
ve `app/auth.py`nin `/api/whatsapp/party-` önek kuralı.

ÖLÇÜLEN EKSİK (keşif §3.1): `whatsapp_links` bir numarayı YALNIZ bir
`app_users` satırına çözebiliyor. Çiftçi bir kullanıcı değil bir CARİDİR ve
tek bir cari bile değildir — ekstre tarafı `customers`, avans/makbuz tarafı
`suppliers`. Bu dilim o çeviriyi getiriyor ve YALNIZ onu: dağıtıcı, çiftçi
niyetleri ve mesaj hız sınırı F10-1b'nindir.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

  * Kod özetini `hmac.compare_digest` yerine `==` ile kıyaslamak
                                    -> SABİT SÜRE kapısı KIRMIZI (hiçbir
                                       davranış testi bu mutantı öldürmez;
                                       kaybolan şey ZAMANLAMADIR)
  * CAS'ten `status == PENDING` ya da `company_id` yüklemini düşürmek
                                    -> CAS kapısı KIRMIZI
  * `target_phone` kıyasını düşürmek ya da bağlantı INSERT'inden SONRAYA
    almak                           -> HEDEF NUMARA ve SIRA kapıları KIRMIZI
                                       (ikincisini hiçbir davranış testi
                                       göremez: SAVEPOINT geri aldığı için
                                       sonuç AYNI görünür)
  * Kısmi tekili `(company_id, phone)` yerine `(phone)` yapmak
                                    -> BAŞKA FİRMA adımı KIRMIZI
  * Kısmi tekilin `WHERE`ini düşürmek
                                    -> KAPATIP YENİDEN BAĞLAMA adımı KIRMIZI
  * `_personel_baglantisi_var` denetimini kaldırmak
                                    -> PERSONEL ÇAKIŞMASI adımları KIRMIZI
  * `normalize_msisdn` yerine `e164` ile doğrulamak
                                    -> SIKI NUMARA adımı KIRMIZI (K2)
  * Eşleştirmede rıza satırı YAZMAK (ya da `consent_at` damgalamak)
                                    -> RIZA adımı KIRMIZI (rızayı
                                       personelin ürettiği kod veremez;
                                       çiftçinin ilk mesajı verir — §5.4)
  * `auth.py`deki `party-` kuralını generic kuralın ALTINA almak
                                    -> KURAL SIRASI kapısı KIRMIZI
  * Handler'daki `_taraf_izni` çağrısını kaldırmak
                                    -> İZİN MATRİSİ adımları KIRMIZI
  * `taraf_coz`dan aktif cari doğrulamasını düşürmek
                                    -> PASİF CARİ adımı KIRMIZI
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260918_0090_whatsapp_taraf_baglantisi.py"
SEMA = BACKEND / "app" / "whatsapp" / "schema.py"
SERVIS = BACKEND / "app" / "whatsapp" / "taraf.py"
UC = BACKEND / "app" / "routers" / "whatsapp.py"
YETKI = BACKEND / "app" / "auth.py"

BAGLANTI_TABLO = "whatsapp_party_links"
KOD_TABLO = "whatsapp_party_pairing_codes"

#: Kanonik numara (`normalize_phone` çıktısı) ve AYNI numaranın insan yazımı.
NUMARA = "905321112233"
NUMARA_INSAN = "0532 111 22 33"
IKINCI_NUMARA = "905331112233"
#: `telefon.e164` KABUL eder ama `consents.normalize_msisdn` REDDEDER —
#: keşif §2.2'nin ölçtüğü ayrışma (13 rakam, sabit hat öneki).
GEVSEK_NUMARA = "+90 212 4412 2012"

# Ortam UYGULAMA İÇE AKTARILMADAN ÖNCE kurulur; gerekçe `test_wa2_eslestirme`
# başlığındakiyle AYNI: `app.config.Settings` modül düzeyinde TEK KOPYADIR.
_CALISMA = Path(tempfile.mkdtemp(prefix="f10a-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "f10a.db").as_posix()
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


# --------------------------------------------------------------- statik ---

def test_goc_IKI_KIRACI_TABLOSU_aciyor_ve_BILESIK_FK_kuruyor() -> None:
    """İkisi de `company_id` + `UNIQUE(company_id, id)` taşıyor (0062 kuralı).

    MUTASYON: bir tablodan `company_id`yi düşürmek bunu ve KİRACI ENVANTERİ
    kapısını KIRMIZI yapar.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260918_0090"' in kaynak
    assert 'down_revision = "20260915_0089"' in kaynak
    agac = ast.parse(kaynak)
    kurulan = [
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "create_table"
    ]
    assert len(kurulan) == 2, kurulan
    for cagri in kurulan:
        parca = ast.get_source_segment(kaynak, cagri) or ""
        assert '"company_id"' in parca
        assert 'sa.UniqueConstraint("company_id", "id"' in parca
    # Bir firmanın kodu BAŞKA firmanın bağlantısını tüketmiş görünemez.
    assert '["company_id", "consumed_link_id"]' in kaynak


def test_PARTY_ID_GERCEK_FK_TASIMIYOR_ve_bu_BILINCLI() -> None:
    """Polimorfik sütun iki tabloya birden FK ALAMAZ (`notification_consents`).

    Kapı, bir gün "eksik FK" diye eklenecek bir `customers.id` bağını
    yakalar: o bağ, tedarikçi tarafını ŞEMA SEVİYESİNDE imkânsız kılardı.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert '["customers.id"]' not in kaynak
    assert '["suppliers.id"]' not in kaynak
    # Yerine geçen koruma: CHECK + geri yükleme sınıflandırıcısı.
    assert 'name="ck_wpl_party_type"' in kaynak
    from app.kiraci_geri_yukleme import AYIRT_EDICI_HEDEFLER

    assert AYIRT_EDICI_HEDEFLER[(BAGLANTI_TABLO, "party_id")][0] == "party_type"
    assert AYIRT_EDICI_HEDEFLER[(KOD_TABLO, "party_id")][0] == "party_type"


def test_TARAF_SOZLUGU_consents_ile_BIREBIR() -> None:
    """`schema.TARAF_TIPLERI` == `consents.PARTY_TYPES` — İKİ YERDE de aynı.

    Ayrışsalardı rıza satırı yazılamayan bir taraf tipi doğardı ve bağlantı
    ilk cevabında sessizce tıkanırdı.
    """
    from app.notifications.consents import PARTY_TYPES
    from app.whatsapp import schema

    assert schema.TARAF_TIPLERI == PARTY_TYPES
    goc = GOC.read_text(encoding="utf-8")
    assert 'TARAF_TIPLERI = ("CUSTOMER", "SUPPLIER")' in goc


def test_AKTIF_NUMARA_TEKILI_KISMI_ve_DIYALEKTE_GORE_YAZILI() -> None:
    """Tekil `(company_id, phone) WHERE is_active`; yüklem İKİ AYRI METİN."""
    import importlib.util

    kaynak = GOC.read_text(encoding="utf-8")
    assert 'BAGLANTI_AKTIF_TEKIL = "uq_whatsapp_party_links_aktif_numara"' in kaynak
    assert '["company_id", "phone"]' in kaynak
    assert 'sqlite_where=_aktif_yuklem("sqlite")' in kaynak
    assert 'postgresql_where=_aktif_yuklem("postgresql")' in kaynak

    spec = importlib.util.spec_from_file_location("f10a_goc", GOC)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    assert str(modul._aktif_yuklem("sqlite")) == "is_active = 1"
    assert str(modul._aktif_yuklem("postgresql")) == "is_active = true"


def test_CHECKLER_gocte_ve_Core_tanimda_BIREBIR() -> None:
    """CHECK adları İKİ YERDE de var (0079'un yedisi + taraf + hedef)."""
    goc = GOC.read_text(encoding="utf-8")
    sema = SEMA.read_text(encoding="utf-8")
    adlar = (
        "ck_wpl_party_type",
        "ck_wpl_party_id",
        "ck_wppc_party_type",
        "ck_wppc_target_phone",
        "ck_wppc_status",
        "ck_wppc_attempt_count",
        "ck_wppc_max_attempts",
        "ck_wppc_pending_temiz",
        "ck_wppc_consumed_alanlari",
        "ck_wppc_cancelled_alani",
        "ck_wppc_expired_temiz",
    )
    for ad in adlar:
        assert ad in goc, ad
        assert ad in sema, ad


def test_KOD_OZETI_SABIT_SURELI_KARSILASTIRILIYOR() -> None:
    """`kod_kullan` özeti `hmac.compare_digest` ile kıyaslıyor.

    MUTASYON: `==`'e çevirmek DAVRANIŞI DEĞİŞTİRMEZ — hiçbir davranış testi
    onu öldüremez. Kapı bu yüzden AST'dedir (`eslestirme.py`nin aynı
    kapısıyla AYNI kalıp).
    """
    dugum = _fn(SERVIS.read_text(encoding="utf-8"), "kod_kullan")
    cagrilar = [
        d
        for d in ast.walk(dugum)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "compare_digest"
    ]
    assert len(cagrilar) == 1, "kod özeti sabit sürede karşılaştırılmıyor"


def test_CAS_KOSULU_STATUS_PENDING_ve_KIRACI_YUKLEMLI() -> None:
    """Kodu tüketen UPDATE, `status == PENDING` ve `company_id` yüklemli.

    Satır kilidi (yalnız PG) ve okuma sonrası durum denetimi ayrı ayrı
    yetiyor, yani bu tek mutantı hiçbir davranış testi öldüremez.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    dugum = _fn(kaynak, "kod_kullan")
    guncellemeler = [
        d
        for d in ast.walk(dugum)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "update"
    ]
    assert guncellemeler, "kod tüketen UPDATE bulunamadı"
    govde = ast.get_source_segment(kaynak, dugum) or ""
    assert "whatsapp_party_pairing_codes.c.status == schema.PAIRING_PENDING" in govde
    assert "whatsapp_party_pairing_codes.c.company_id == company_id" in govde


def test_HEDEF_NUMARA_DENETIMI_BAGLANTI_INSERTINDEN_ONCE() -> None:
    """SEC-1 sırası SÖZLEŞMEDİR: `target_phone` kıyası INSERT'ten ÖNCE.

    Sonraya alınsaydı yanlış numara ÖNCE bağlanır, sonra geri alınırdı — ve
    geri alma yolunun her kusuru doğrudan bir devralma olurdu. SAVEPOINT
    sonucu AYNI gösterdiği için bunu hiçbir davranış testi göremez.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    dugum = _fn(kaynak, "kod_kullan")
    kiyas_satiri = insert_satiri = None
    for alt in ast.walk(dugum):
        parca = ast.get_source_segment(kaynak, alt) or ""
        if isinstance(alt, ast.Compare) and 'satir["target_phone"]' in parca:
            kiyas_satiri = alt.lineno
        if (
            isinstance(alt, ast.Call)
            and getattr(alt.func, "id", None) == "insert"
            and "whatsapp_party_links" in parca
        ):
            insert_satiri = alt.lineno
    assert kiyas_satiri is not None and insert_satiri is not None
    assert kiyas_satiri < insert_satiri, (kiyas_satiri, insert_satiri)


def test_YETKI_KURALI_GENERIC_WHATSAPP_KURALININ_USTUNDE() -> None:
    """`/api/whatsapp/party-` kuralı `/api/whatsapp/` kuralından ÖNCE.

    MUTASYON: sırayı çevirmek dört ucu `users` iznine düşürür ve kapı
    PRATİKTE ölür (`satis`/`depo` o izni taşımaz) — keşif §7, K4.
    """
    kaynak = YETKI.read_text(encoding="utf-8")
    ozel = kaynak.index('path.startswith("/api/whatsapp/party-")')
    genel = kaynak.index('path.startswith("/api/whatsapp/")')
    assert ozel < genel, "özel önek kuralı generic kuralın ALTINDA"

    from app.auth import required_permission

    assert required_permission("POST", "/api/whatsapp/party-pairing-codes") == "read"
    assert required_permission("GET", "/api/whatsapp/party-links") == "read"
    # Personel uçları DEĞİŞMEDİ.
    assert required_permission("GET", "/api/whatsapp/links") == "users"


def test_DORT_UC_DE_HANDLERDA_ROL_KAPISI_KURUYOR() -> None:
    """Dördü de `_require_permission` zincirinden geçiyor (yetki nüfus sayımı).

    Ara katman `read` verir; gerçek kapı burada. Çağrı kaybolursa `depo` rolü
    müşteri defterini, `satis` rolü tedarikçi defterini okuyabilir hâle gelir.
    """
    kaynak = UC.read_text(encoding="utf-8")
    for ad in (
        "taraf_kodu_uret",
        "taraf_kodu_iptal",
        "taraf_baglantilarini_listele",
        "taraf_baglantisi_kapat",
    ):
        govde = ast.get_source_segment(kaynak, _fn(kaynak, ad)) or ""
        assert "_taraf_izni(" in govde, ad
    izin_govde = ast.get_source_segment(kaynak, _fn(kaynak, "_taraf_izni")) or ""
    assert "_require_permission(" in izin_govde
    from app.routers.whatsapp import TARAF_IZINLERI

    assert TARAF_IZINLERI == {"CUSTOMER": "sales", "SUPPLIER": "purchases"}


def test_IKI_TABLO_DA_KIRACI_ENVANTERINDE() -> None:
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert BAGLANTI_TABLO in TENANT_TABLES
    assert KOD_TABLO in TENANT_TABLES


def test_UCLAR_PUBLIC_API_MUAFIYETINE_GIRMIYOR() -> None:
    """Muafiyet TAM YOL eşleşmesidir; bu dosyaya eklenen uç oturumsuz açılmaz."""
    from app.main import PUBLIC_API

    for yol in ("/api/whatsapp/party-pairing-codes", "/api/whatsapp/party-links"):
        assert yol not in PUBLIC_API


def test_AKTIVITE_KATALOGU_DORT_OLAYI_TANIYOR() -> None:
    """Kapalı katalog: kataloğa girmeden `log_activity` ValueError verir."""
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    for tur in (
        "party.whatsapp_pairing_code_created",
        "party.whatsapp_pairing_code_cancelled",
        "party.whatsapp_link_activated",
        "party.whatsapp_link_deactivated",
    ):
        assert tur in ACTION_TYPES, tur
    assert "whatsapp_party" in RESOURCE_TYPES


def test_PERSONEL_KIMLIK_YOLU_DOKUNULMADI() -> None:
    """`kimlik.user_id` çözümleri ONBİR yerde KALDI — sayı ölçüldü.

    F10-1a personel yolunun YANINDAN geçer. Bu sayı değiştiyse dilim
    kapsamını aşmış demektir.
    """
    import re

    toplam = 0
    for yol in sorted((BACKEND / "app" / "whatsapp").glob("*.py")):
        toplam += len(re.findall(r"kimlik\.user_id", yol.read_text(encoding="utf-8")))
    assert toplam == 11, toplam


# ------------------------------------------------------------ davranis ----

@pytest.fixture(scope="module")
def uygulama():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma; her birinde cari(ler); DÖRT rol; hepsi İKİ firmada üye."""
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    def temizle(db):
        for tablo in (
            KOD_TABLO,
            BAGLANTI_TABLO,
            "whatsapp_pairing_codes",
            "whatsapp_links",
            "whatsapp_pairing_attempts",
            "notification_consent_events",
            "notification_consents",
            # `activity_logs` TEMIZLENMEZ: defter APPEND-ONLY'dir (silme
            # tetikleyicisi SQLite'ta da kurulu). Her test KENDI firmasini
            # dogurdugu icin sorgular zaten `company_id` ile daralir.
        ):
            db.execute(text(f"DELETE FROM {tablo}"))
        db.commit()

    n = next(_SAYAC)
    parola = "F10aTest!12345"
    with SessionLocal() as db:
        temizle(db)
        an = datetime.now(timezone.utc)

        def firma(ad: str) -> int:
            return db.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at)"
                    " VALUES(:a,1,:t) RETURNING id"
                ),
                {"a": ad, "t": an},
            ).scalar_one()

        firma_a = firma(f"F10a Bir {n} A.S.")
        firma_b = firma(f"F10a Iki {n} A.S.")

        def kullanici(kad: str, rol: str) -> int:
            return db.execute(
                text(
                    "INSERT INTO app_users(username,email,email_verified,"
                    "display_name,password_hash,role,is_active,"
                    "must_change_password,created_at)"
                    " VALUES(:k,:e,1,:k,:h,:r,1,0,:t) RETURNING id"
                ),
                {
                    "k": kad,
                    "e": kad + "@f10a.invalid",
                    "h": hash_password(parola),
                    "r": rol,
                    "t": an,
                },
            ).scalar_one()

        # SEVK EDİLEN ALTI ROLÜN ALTISI (`auth.ROLE_PERMISSIONS`): izin
        # matrisi altısını da ÖLÇÜYOR, örneklemiyor.
        roller = {
            rol: kullanici(f"f10a-{rol}-{n}", rol)
            for rol in ("admin", "yonetici", "muhasebe", "satis", "depo", "rapor")
        }
        for uid in roller.values():
            for cid in (firma_a, firma_b):
                db.execute(
                    text(
                        "INSERT INTO user_company_memberships"
                        "(user_id,company_id,is_default,created_at)"
                        " VALUES(:u,:c,0,:t)"
                    ),
                    {"u": uid, "c": cid, "t": an},
                )

        def cari(tablo: str, cid: int, ad: str) -> int:
            return db.execute(
                text(
                    f"INSERT INTO {tablo}(name,is_active,company_id,"
                    "opening_balance,risk_limit,payment_term_days)"
                    " VALUES(:a,1,:c,0,0,0) RETURNING id"
                ),
                {"a": ad, "c": cid},
            ).scalar_one()

        musteri_a = cari("customers", firma_a, f"Ciftci Musteri {n}")
        tedarikci_a = cari("suppliers", firma_a, f"Ciftci Tedarikci {n}")
        musteri_b = cari("customers", firma_b, f"Komsu Musteri {n}")
        db.commit()

    veri = {
        "firma_a": int(firma_a),
        "firma_b": int(firma_b),
        "musteri_a": int(musteri_a),
        "tedarikci_a": int(tedarikci_a),
        "musteri_b": int(musteri_b),
        "roller": {k: int(v) for k, v in roller.items()},
        "kadlar": {k: f"f10a-{k}-{n}" for k in roller},
        "parola": parola,
    }
    yield veri
    with SessionLocal() as db:
        temizle(db)


@pytest.fixture()
def oturum(dunya):
    """`dunya`YA BAĞLI — fixture sırası teardown kilidini önlüyor (WA2 notu)."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


def _kod_ver(
    db,
    cid: int,
    party_type: str,
    party_id: int,
    *,
    hedef: str = NUMARA,
    simdi: datetime | None = None,
) -> str:
    from app.whatsapp import taraf

    uretilen = taraf.kod_uret(
        db, cid, party_type, party_id, hedef_telefon=hedef, simdi=simdi
    )
    db.commit()
    return uretilen.kod


def _baglantilar(db, cid: int | None = None):
    from sqlalchemy import text

    temel = (
        "SELECT id,company_id,party_type,party_id,phone,is_active,consent_at"
        f" FROM {BAGLANTI_TABLO}"
    )
    if cid is None:
        return db.execute(text(temel + " ORDER BY id")).mappings().all()
    return (
        db.execute(text(temel + " WHERE company_id=:c ORDER BY id"), {"c": cid})
        .mappings()
        .all()
    )


def test_DAVRANIS_kod_uret_KULLAN_baglanti_acar_RIZA_ACMAZ(oturum, dunya) -> None:
    """Uçtan uca: kod → `BAĞLA` → bağlantı + SIFIR rıza satırı + aktivite kaydı.

    H77 (#154 mercek bulgusu): eski ad "...ve_RIZA_aciyor" idi ve testin
    ÖLÇTÜĞÜNÜN TERSİNİ söylüyordu — gövde rıza defterinde SIFIR satır
    olduğunu doğruluyor (`consent_at is None` ve `notification_consents`
    boş). Yanlış ad, F10-1b'yi yazan kişiye "rıza eşleştirmede açılıyor"
    diye okunabilirdi ve o kişi rıza kapısını HİÇ kurmayabilirdi.

    Rızayı personelin ürettiği kod VERMEZ; çiftçinin ilk mesajı verir
    (F10-1b, `ciftci_yurutucu` başlığı).

    Numara İNSAN YAZIMIYLA veriliyor ve KANONİK saklanıyor.
    """
    from sqlalchemy import text

    from app.whatsapp import taraf

    kod = _kod_ver(
        oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"], hedef=NUMARA_INSAN
    )
    sonuc = taraf.kod_kullan(oturum, NUMARA, kod)
    oturum.commit()
    assert sonuc.basarili and sonuc.cevapla
    assert sonuc.party_type == "SUPPLIER"
    assert sonuc.party_id == dunya["tedarikci_a"]

    satirlar = _baglantilar(oturum, dunya["firma_a"])
    assert len(satirlar) == 1
    assert satirlar[0]["phone"] == NUMARA
    assert bool(satirlar[0]["is_active"]) is True
    # RIZA DAMGASI YOK: rizayi personelin urettigi kod DEGIL, ciftcinin ilk
    # mesaji verir (F10-1b). MUTASYON: burada `consent_at=an` yazmak bunu ve
    # RIZA kapisini KIRMIZI yapar.
    assert satirlar[0]["consent_at"] is None

    durum = (
        oturum.execute(text(f"SELECT status,consumed_link_id FROM {KOD_TABLO}"))
        .mappings()
        .one()
    )
    assert durum["status"] == "CONSUMED"
    assert int(durum["consumed_link_id"]) == sonuc.link_id

    # KVKK: BU DİLİM RIZA DEFTERİNE HİÇBİR ŞEY YAZMAZ (Şef kararı).
    # Rızayı personelin ürettiği kod veremez; çiftçinin ilk mesajındaki açık
    # onay verir ve o yol F10-1b'dedir. Defterde "bekleyen" durumu da YOKTUR
    # (`status` CHECK'i yalnız GRANTED/REVOKED), yani satırı hiç açmamak DOĞRU
    # temsildir — ve `evaluate_consent` onu tam gerektiği gibi okur.
    assert (
        oturum.execute(
            text("SELECT count(*) FROM notification_consents WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == 0
    )
    assert (
        oturum.execute(
            text(
                "SELECT count(*) FROM notification_consent_events"
                " WHERE company_id=:c"
            ),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == 0
    )
    # FAIL-CLOSED: bağlı ama rızasız numara ERP verisi ALAMAZ — kapı zaten
    # var olan `evaluate_consent`tır ve `NO_RECORD` döner.
    from app.notifications.consents import evaluate_consent

    karar = evaluate_consent(
        oturum,
        company_id=dunya["firma_a"],
        party_type="SUPPLIER",
        party_id=dunya["tedarikci_a"],
        channel="WHATSAPP",
        recipient=NUMARA,
    )
    assert karar["allowed"] is False
    assert karar["reason"] == "NO_RECORD"

    # Denetim izi: aktör NULL (çiftçinin `app_users` kimliği YOK).
    kayit = (
        oturum.execute(
            text(
                "SELECT user_id,resource_type,resource_id FROM activity_logs"
                " WHERE action_type='party.whatsapp_link_activated'"
                " AND company_id=:c"
            ),
            {"c": dunya["firma_a"]},
        )
        .mappings()
        .one()
    )
    assert kayit["user_id"] is None
    assert kayit["resource_type"] == "whatsapp_party"
    assert int(kayit["resource_id"]) == sonuc.link_id


def test_DAVRANIS_DUZ_KOD_SAKLANMIYOR(oturum, dunya) -> None:
    """Veritabanında yalnız SHA-256 özeti var; düz kod HİÇBİR yerde."""
    from sqlalchemy import text

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    ozet = oturum.execute(text(f"SELECT code_digest FROM {KOD_TABLO}")).scalar_one()
    assert len(ozet) == 64
    assert kod not in ozet


def test_DAVRANIS_BASKA_NUMARADAN_GELEN_DOGRU_KOD_REDDEDILIYOR(oturum, dunya) -> None:
    """SEC-1: kod VERİLDİĞİ numaradan gelmek zorunda; deneme YANIYOR."""
    from sqlalchemy import text

    from app.whatsapp import taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    sonuc = taraf.kod_kullan(oturum, IKINCI_NUMARA, kod)
    oturum.commit()
    assert not sonuc.basarili
    assert _baglantilar(oturum) == []
    deneme = (
        oturum.execute(text(f"SELECT attempt_count,status FROM {KOD_TABLO}"))
        .mappings()
        .one()
    )
    assert int(deneme["attempt_count"]) == 1
    assert deneme["status"] == "PENDING"

    # DOĞRU numaradan aynı kod HÂLÂ çalışıyor.
    assert taraf.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()


def test_DAVRANIS_SURESI_DOLMUS_KOD_TUKETILEMEZ(oturum, dunya) -> None:
    from app.whatsapp import taraf

    gecmis = datetime.now(timezone.utc) - timedelta(hours=2)
    kod = _kod_ver(
        oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"], simdi=gecmis
    )
    sonuc = taraf.kod_kullan(oturum, NUMARA, kod)
    oturum.commit()
    assert not sonuc.basarili
    assert _baglantilar(oturum) == []


def test_DAVRANIS_HIZ_SINIRI_personel_sayaciyla_ORTAK(oturum, dunya) -> None:
    """Pencere sınırı aşılınca kod TÜKETİLMEZ ve CEVAP ÜRETİLMEZ.

    Sayaç `whatsapp_pairing_attempts`tir — personel yoluyla AYNI tablo ve
    AYNI anahtar. Yani taraf denemesi personel sınırını ZAYIFLATMAZ.
    """
    from sqlalchemy import text

    from app.whatsapp import schema, taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    for _ in range(schema.PAIRING_PENCERE_SINIRI):
        taraf.kod_kullan(oturum, NUMARA, "YANLISKOD123")
    oturum.commit()

    sonuc = taraf.kod_kullan(oturum, NUMARA, kod)
    oturum.commit()
    assert not sonuc.basarili
    assert sonuc.sinirlandi and not sonuc.cevapla
    assert _baglantilar(oturum) == []
    sayac = oturum.execute(
        text("SELECT attempt_count FROM whatsapp_pairing_attempts WHERE phone=:p"),
        {"p": NUMARA},
    ).scalar_one()
    assert int(sayac) > schema.PAIRING_PENCERE_SINIRI


def test_DAVRANIS_AYNI_NUMARA_AYNI_FIRMADA_IKINCI_KEZ_BAGLANAMAZ(oturum, dunya) -> None:
    """Kısmi tekil ısırıyor: kod üretimi 409 `TARAF_NUMARA_BAGLI` veriyor."""
    from app.whatsapp import taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()

    with pytest.raises(taraf.TarafHatasi) as hata:
        taraf.kod_uret(
            oturum,
            dunya["firma_a"],
            "SUPPLIER",
            dunya["tedarikci_a"],
            hedef_telefon=NUMARA,
        )
    assert hata.value.durum == 409
    assert hata.value.code == "TARAF_NUMARA_BAGLI"
    oturum.rollback()


def test_DAVRANIS_BASKA_FIRMADA_AYNI_NUMARA_SERBEST(oturum, dunya) -> None:
    """Bir çiftçi İKİ alım merkezine ürün verir — tekil `company_id` kapsamlı.

    MUTASYON: anahtarı yalnız `(phone)` yapmak bunu KIRMIZI yapar.
    """
    from app.whatsapp import taraf

    kod_a = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod_a).basarili
    oturum.commit()

    kod_b = _kod_ver(oturum, dunya["firma_b"], "CUSTOMER", dunya["musteri_b"])
    assert taraf.kod_kullan(oturum, NUMARA, kod_b).basarili
    oturum.commit()

    assert len(_baglantilar(oturum)) == 2
    adaylar = taraf.taraf_coz(oturum, NUMARA)
    assert [a.company_id for a in adaylar] == sorted(
        [dunya["firma_a"], dunya["firma_b"]]
    )


def test_DAVRANIS_KAPATIP_YENIDEN_BAGLANABILIR(oturum, dunya) -> None:
    """Pasifleştirme kısmi tekilin KAPSAMINDAN çıkmaktır; iz SİLİNMEZ.

    MUTASYON: tekilin `WHERE`ini düşürmek bunu KIRMIZI yapar.
    """
    from app.whatsapp import taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()
    link_id = int(_baglantilar(oturum)[0]["id"])

    assert taraf.baglantiyi_kapat(oturum, dunya["firma_a"], link_id)
    oturum.commit()
    # İkinci kapatma FALSE döner (idempotent DEĞİL — 404'ün kaynağı).
    assert not taraf.baglantiyi_kapat(oturum, dunya["firma_a"], link_id)
    oturum.commit()

    kod2 = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod2).basarili
    oturum.commit()
    satirlar = _baglantilar(oturum)
    assert len(satirlar) == 2, "eski satır silinmiş: iz kayboldu"
    assert [bool(s["is_active"]) for s in satirlar] == [False, True]


def _personel_baglantisi_yaz(db, cid: int, uid: int, telefon: str) -> None:
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
            "created_at,updated_at) VALUES(:c,:u,:p,1,:t,:t)"
        ),
        {"c": cid, "u": uid, "p": telefon, "t": an},
    )
    db.commit()


def test_DAVRANIS_PERSONEL_NUMARASI_CAKISMASI_409(oturum, dunya) -> None:
    """Aynı numara aynı firmada personel bağlantısı taşıyorsa taraf kodu YOK.

    Dağıtıcı personel dalını ÖNCE deneyeceği için sessizce açılan bir taraf
    bağlantısı ÖLÜ olurdu (keşif §5.1).
    """
    from app.whatsapp import taraf

    _personel_baglantisi_yaz(
        oturum, dunya["firma_a"], dunya["roller"]["admin"], NUMARA
    )
    with pytest.raises(taraf.TarafHatasi) as hata:
        taraf.kod_uret(
            oturum,
            dunya["firma_a"],
            "CUSTOMER",
            dunya["musteri_a"],
            hedef_telefon=NUMARA,
        )
    assert hata.value.durum == 409
    assert hata.value.code == "TARAF_NUMARA_PERSONEL"
    oturum.rollback()


def test_DAVRANIS_PERSONEL_BAGLANTISI_SONRADAN_ACILIRSA_KOD_DUSER(
    oturum, dunya
) -> None:
    """Denetim `kod_kullan`da TEKRARLANIYOR: kod üretildikten SONRA da bakılır."""
    from app.whatsapp import taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    _personel_baglantisi_yaz(
        oturum, dunya["firma_a"], dunya["roller"]["admin"], NUMARA
    )
    sonuc = taraf.kod_kullan(oturum, NUMARA, kod)
    oturum.commit()
    assert not sonuc.basarili
    assert _baglantilar(oturum) == []


def test_DAVRANIS_KOMSU_FIRMANIN_CARISINE_KOD_URETILEMEZ(oturum, dunya) -> None:
    """Kiracı yalıtımı: `party_id` AYNI firmada olmalı — 404."""
    from app.whatsapp import taraf

    with pytest.raises(taraf.TarafHatasi) as hata:
        taraf.kod_uret(
            oturum,
            dunya["firma_a"],
            "CUSTOMER",
            dunya["musteri_b"],
            hedef_telefon=NUMARA,
        )
    assert hata.value.durum == 404
    assert hata.value.code == "TARAF_BULUNAMADI"
    oturum.rollback()


def test_DAVRANIS_PASIF_CARIYE_KOD_URETILEMEZ(oturum, dunya) -> None:
    from sqlalchemy import text

    from app.whatsapp import taraf

    oturum.execute(
        text("UPDATE customers SET is_active=0 WHERE id=:i"),
        {"i": dunya["musteri_a"]},
    )
    oturum.commit()
    with pytest.raises(taraf.TarafHatasi) as hata:
        taraf.kod_uret(
            oturum,
            dunya["firma_a"],
            "CUSTOMER",
            dunya["musteri_a"],
            hedef_telefon=NUMARA,
        )
    assert hata.value.durum == 409
    assert hata.value.code == "TARAF_PASIF"
    oturum.rollback()


def test_DAVRANIS_SIKI_NUMARA_DOGRULAMASI_K2(oturum, dunya) -> None:
    """Doğrulama `consents.normalize_msisdn` (SIKI) — `e164` YETMEZ.

    Keşif §2.2: `e164` 13 haneli sabit hat numarasını KABUL ediyor, rıza
    defteri REDDEDİYOR. Gevşek doğrulama, rıza yazılamayan ÖLÜ bir bağlantı
    üretirdi.
    """
    from app.whatsapp import taraf
    from app.whatsapp.telefon import e164

    assert e164(GEVSEK_NUMARA)  # `e164` KABUL ediyor — ölçüldü.
    with pytest.raises(taraf.TarafHatasi) as hata:
        taraf.kod_uret(
            oturum,
            dunya["firma_a"],
            "CUSTOMER",
            dunya["musteri_a"],
            hedef_telefon=GEVSEK_NUMARA,
        )
    assert hata.value.durum == 422
    assert hata.value.code == "TELEFON_GECERSIZ"
    oturum.rollback()


def test_DAVRANIS_taraf_coz_PASIF_CARIYI_ELIYOR(oturum, dunya) -> None:
    """Bağlantı satırı KALIR ama kimlik ÇÖZÜLMEZ — fail-closed."""
    from sqlalchemy import text

    from app.whatsapp import taraf

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()
    assert len(taraf.taraf_coz(oturum, NUMARA)) == 1

    oturum.execute(
        text("UPDATE customers SET is_active=0 WHERE id=:i"),
        {"i": dunya["musteri_a"]},
    )
    oturum.commit()
    assert taraf.taraf_coz(oturum, NUMARA) == []
    assert len(_baglantilar(oturum)) == 1, "bağlantı izi silinmiş"


def test_DAVRANIS_PERSONEL_KIMLIGI_TARAF_BAGLANTISINDAN_ETKILENMIYOR(
    oturum, dunya
) -> None:
    """`kimlik_coz`un cevabı taraf bağlantısı varken de AYNI.

    İki defter birbirine DOKUNMAZ; personel yolu bu dilimde değişmedi.
    """
    from app.whatsapp import eslestirme, taraf

    _personel_baglantisi_yaz(
        oturum, dunya["firma_a"], dunya["roller"]["admin"], IKINCI_NUMARA
    )
    once = eslestirme.kimlik_coz(oturum, IKINCI_NUMARA)
    assert once, "personel kimliği kurulamadı; kapı bir şey ölçmüyor"

    kod = _kod_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    assert taraf.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()

    assert eslestirme.kimlik_coz(oturum, IKINCI_NUMARA) == once
    # Ters yön: personel numarası taraf olarak ÇÖZÜLMÜYOR.
    assert taraf.taraf_coz(oturum, IKINCI_NUMARA) == []


# --------------------------------------------------------------- uclar ----

def _giris(istemci, kullanici: str, parola: str, cid: int) -> dict:
    r = istemci.post("/api/auth/login", json={"username": kullanici, "password": parola})
    assert r.status_code == 200, r.text
    return {
        "Authorization": "Bearer " + r.json()["access_token"],
        "X-Company-ID": str(cid),
    }


@pytest.fixture()
def basliklar(uygulama, dunya):
    def uret(rol: str, cid: int | None = None) -> dict:
        return _giris(
            uygulama, dunya["kadlar"][rol], dunya["parola"], cid or dunya["firma_a"]
        )

    return uret


#: SEVK EDİLEN ALTI ROLÜN ALTISI, İKİ TARAF TİPİNİN İKİSİNDE — ELLE YAZILDI.
#: `ROLE_PERMISSIONS`tan türetilseydi, izin tablosunu bozan bir mutasyon
#: beklentiyi de birlikte kaydırır ve kapı sessizce yeşil kalırdı (deponun
#: "çapa doğruladığı kaynaktan bağımsız yazılır" kuralı).
#:
#: `rapor` SATIRLARI BU KAPININ ASIL İDDİASIDIR: rol `read` TAŞIR, yani ara
#: katmandan GEÇER — ve handler'da 403 alır. Uçların KORUMALI read
#: (`GUARDED_READ_OPERATIONS`) sayılması ve `EXPECTED_UNDENIABLE`ın 97'de
#: SABİT kalması tam olarak bu ölçüme dayanıyor.
IZIN_MATRISI = [
    ("admin", "CUSTOMER", 201),      # "*" jokeri
    ("admin", "SUPPLIER", 201),
    ("yonetici", "CUSTOMER", 201),   # sales + purchases
    ("yonetici", "SUPPLIER", 201),
    ("muhasebe", "CUSTOMER", 201),   # sales + purchases
    ("muhasebe", "SUPPLIER", 201),
    ("satis", "CUSTOMER", 201),      # yalnız sales
    ("satis", "SUPPLIER", 403),
    ("depo", "CUSTOMER", 403),       # yalnız purchases
    ("depo", "SUPPLIER", 201),
    ("rapor", "CUSTOMER", 403),      # read VAR, ikisi de YOK
    ("rapor", "SUPPLIER", 403),
]


@pytest.mark.parametrize("rol,party_type,beklenen", IZIN_MATRISI)
def test_UC_IZIN_MATRISI(uygulama, dunya, basliklar, rol, party_type, beklenen) -> None:
    """Kapı ROL DEĞERİYLE reddediyor: CUSTOMER `sales`, SUPPLIER `purchases`.

    MUTASYON: handler'daki `_taraf_izni` çağrısını kaldırmak altı satırı da
    201 yapar ve bu kapı KIRMIZI olur.
    """
    hedef = dunya["musteri_a"] if party_type == "CUSTOMER" else dunya["tedarikci_a"]
    r = uygulama.post(
        "/api/whatsapp/party-pairing-codes",
        headers=basliklar(rol),
        json={"party_type": party_type, "party_id": hedef, "phone": NUMARA},
    )
    assert r.status_code == beklenen, r.text


def test_UC_kod_uret_DUZ_KODU_BIR_KEZ_donuyor(uygulama, dunya, basliklar) -> None:
    """POST: düz kod YALNIZ bu cevapta; telefon MASKELİ; `no-store` var."""
    r = uygulama.post(
        "/api/whatsapp/party-pairing-codes",
        headers=basliklar("satis"),
        json={
            "party_type": "CUSTOMER",
            "party_id": dunya["musteri_a"],
            "phone": NUMARA_INSAN,
        },
    )
    assert r.status_code == 201, r.text
    govde = r.json()
    assert len(govde["kod"]) == 12
    assert govde["kod_gosterim"].replace("-", "") == govde["kod"]
    assert govde["telefon"] == "***" + NUMARA[-4:]
    assert NUMARA not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_UC_liste_TELEFONU_MASKELI_donuyor(uygulama, dunya, basliklar) -> None:
    """Defterin amacı "bu cari bağlı mı", "hangi numaradan" DEĞİL (SEC-3b)."""
    from app.db import SessionLocal
    from app.whatsapp import taraf

    with SessionLocal() as db:
        kod = _kod_ver(db, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
        assert taraf.kod_kullan(db, NUMARA, kod).basarili
        db.commit()

    h = basliklar("satis")
    r = uygulama.get(
        "/api/whatsapp/party-links",
        headers=h,
        params={"party_type": "CUSTOMER", "party_id": dunya["musteri_a"]},
    )
    assert r.status_code == 200, r.text
    satirlar = r.json()
    assert len(satirlar) == 1
    assert satirlar[0]["phone_masked"] == "***" + NUMARA[-4:]
    assert NUMARA not in r.text
    assert satirlar[0]["is_active"] is True

    # SUPPLIER defterini AYNI rol GÖREMEZ.
    red = uygulama.get(
        "/api/whatsapp/party-links", headers=h, params={"party_type": "SUPPLIER"}
    )
    assert red.status_code == 403, red.text


def test_UC_KIRACI_YALITIMI_komsu_firmanin_satirina_404(
    uygulama, dunya, basliklar
) -> None:
    """Başka firmanın bağlantısı YOK sayılır — 404, 403 DEĞİL."""
    from app.db import SessionLocal
    from app.whatsapp import taraf

    with SessionLocal() as db:
        kod = _kod_ver(db, dunya["firma_b"], "CUSTOMER", dunya["musteri_b"])
        assert taraf.kod_kullan(db, NUMARA, kod).basarili
        db.commit()
        yabanci_link = int(_baglantilar(db, dunya["firma_b"])[0]["id"])

    h = basliklar("satis", dunya["firma_a"])
    assert (
        uygulama.get(
            "/api/whatsapp/party-links", headers=h, params={"party_type": "CUSTOMER"}
        ).json()
        == []
    )
    r = uygulama.delete(f"/api/whatsapp/party-links/{yabanci_link}", headers=h)
    assert r.status_code == 404, r.text


def test_UC_baglanti_kapatma_ve_KOD_IPTALI(uygulama, dunya, basliklar) -> None:
    """DELETE uçları: bekleyen kodun iptali + bağlantının pasifleştirilmesi."""
    h = basliklar("satis")
    kod_cevap = uygulama.post(
        "/api/whatsapp/party-pairing-codes",
        headers=h,
        json={
            "party_type": "CUSTOMER",
            "party_id": dunya["musteri_a"],
            "phone": NUMARA,
        },
    ).json()
    iptal = uygulama.delete(
        f"/api/whatsapp/party-pairing-codes/{kod_cevap['kod_id']}", headers=h
    )
    assert iptal.status_code == 200, iptal.text
    assert iptal.json()["status"] == "CANCELLED"
    # İkinci iptal 404 (idempotent DEĞİL).
    assert (
        uygulama.delete(
            f"/api/whatsapp/party-pairing-codes/{kod_cevap['kod_id']}", headers=h
        ).status_code
        == 404
    )

    from app.db import SessionLocal
    from app.whatsapp import taraf

    with SessionLocal() as db:
        kod = _kod_ver(db, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
        assert taraf.kod_kullan(db, NUMARA, kod).basarili
        db.commit()
        link_id = int(_baglantilar(db, dunya["firma_a"])[0]["id"])

    r = uygulama.delete(f"/api/whatsapp/party-links/{link_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": link_id, "is_active": False}
    assert (
        uygulama.delete(f"/api/whatsapp/party-links/{link_id}", headers=h).status_code
        == 404
    )


def test_UC_GECERSIZ_NUMARA_422_MAKINE_KODUYLA(uygulama, dunya, basliklar) -> None:
    """Hata gövdesi MAKİNE KODU taşıyor (`POST /api/pos/lookup` deseni)."""
    r = uygulama.post(
        "/api/whatsapp/party-pairing-codes",
        headers=basliklar("satis"),
        json={
            "party_type": "CUSTOMER",
            "party_id": dunya["musteri_a"],
            "phone": GEVSEK_NUMARA,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "TELEFON_GECERSIZ"


def test_UC_GOVDEDE_FAZLA_ALAN_REDDEDILIYOR(uygulama, dunya, basliklar) -> None:
    """`extra="forbid"`: sessizce yok sayılan alan YOK."""
    r = uygulama.post(
        "/api/whatsapp/party-pairing-codes",
        headers=basliklar("satis"),
        json={
            "party_type": "CUSTOMER",
            "party_id": dunya["musteri_a"],
            "phone": NUMARA,
            "company_id": 999,
        },
    )
    assert r.status_code == 422, r.text
