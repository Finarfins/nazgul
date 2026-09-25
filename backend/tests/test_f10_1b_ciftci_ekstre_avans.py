"""ÇİFTÇİ DAĞITIMI + KVKK RIZA KAPISI + EKSTRE/AVANS (F10-1b).

Konu: göç `20260920_0091` (PLATFORM sayacı), `app/whatsapp/ciftci_niyet.py`,
`app/whatsapp/ciftci_yurutucu.py`, `app/whatsapp/service.py`nin çiftçi dalı
ve `app/activity_log.py`nin İKİ yeni rıza olayı.

F10-1a bir numarayı CARİYE bağlamayı getirmişti ve dağıtıcıya HİÇ
dokunmamıştı: bağlanmış bir çiftçi yazdığında `baglam.kimlik_secimi` onu
PERSONEL defterinde arıyor, bulamıyor ve `_bagsiz_cevap`a düşüyordu. Bu
dilim o dalı açıyor — ve açarken ÜÇ kapı kuruyor: çiftçi personel
araçlarına ULAŞAMAZ, rızası olmadan ERP verisi ALAMAZ, numarası başına
sınırsız mesaj ÜRETEMEZ.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

  * Dağıtıcıda çiftçi dalını `cevap_uret`e bağlamak (ya da çiftçi dalını
    `secim.kimlik is None` denetiminin ÜSTÜNE almak)
                                    -> PERSONEL ARACI ve PERSONEL KAZANIR
                                       kapıları KIRMIZI
  * `ciftci_cevap`tan `_riza_degerlendir` çağrısını düşürmek
                                    -> RIZASIZ VERİ YOK kapısı KIRMIZI
  * `evaluate_consent`i bir kez okuyup sonucu ÖNBELLEĞE almak (sözleşme 2)
                                    -> DUR SONRASI adımı KIRMIZI
  * `DUR`da yalnız rızayı çekip bağlantıyı AÇIK bırakmak
                                    -> DUR İKİSİNİ DE KAPATIR kapısı KIRMIZI
  * `taraf_coz` çok aday döndüğünde ilkini SEÇMEK
                                    -> SEÇİM SORULUR kapısı KIRMIZI
  * `ciftci_ekstre`ye `rol="admin"` geçirmek
                                    -> MASKELİ BAŞLIK kapısı KIRMIZI
  * `ciftci_avans`tan `party_type` denetimini düşürmek
                                    -> MÜŞTERİ AVANS SORARSA kapısı KIRMIZI
  * Sorgulardan `company_id` yüklemini düşürmek
                                    -> KİRACI YALITIMI kapısı KIRMIZI
  * `_bagla_akisi`de sayacı İKİ kez artırmak (yani `deneme=` geçirmemek)
                                    -> TEK BAĞLA kapısı KIRMIZI
  * `whatsapp_message_attempts`e `company_id` eklemek
                                    -> PLATFORM ve FİRMALAR ARASI kapıları
                                       KIRMIZI
  * `EVET`/`HAYIR`ı sıra öneki ATILMAMIŞ tam metne eşlemek (lens NO-GO 1)
                                    -> ÇOK FİRMALI LENS DİZİSİ, 2 HAYIR ve
                                       SEÇİM SORULUR kapıları KIRMIZI
  * Öneksiz `EVET`i N>1'de ilk adaya ya da bütün adaylara yazmak
                                    -> ÖNEKSİZ EVET/HAYIR kapısı KIRMIZI
  * `EVET`te `consent_at`i yazmamak / bir izin kararında onu OKUMAK
                                    -> CONSENT_AT kapıları KIRMIZI
  * `HAYIR`ı yalnız NO_RECORD'da yazmak (açık rızayı KAPATMAMAK; lens
    tur 2)                          -> HAYIR AÇIK RIZAYI KAPATIYOR, ÇOK
                                       FİRMALI LENS DİZİSİ ve 1 HAYIR
                                       kapıları KIRMIZI
  * Yönetici listesinde `consent_at`i `utc_iso`dan geçirmemek
                                    -> CONSENT_AT EVETTE kapısı KIRMIZI
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260920_0091_whatsapp_message_attempts.py"
NIYET = BACKEND / "app" / "whatsapp" / "ciftci_niyet.py"
YURUTUCU = BACKEND / "app" / "whatsapp" / "ciftci_yurutucu.py"
SERVIS = BACKEND / "app" / "whatsapp" / "service.py"

SAYAC_TABLO = "whatsapp_message_attempts"
BAGLANTI_TABLO = "whatsapp_party_links"
ESLESTIRME_SAYAC = "whatsapp_pairing_attempts"

NUMARA = "905321112233"
IKINCI_NUMARA = "905331112233"

# Ortam UYGULAMA İÇE AKTARILMADAN ÖNCE kurulur (`test_f10_1a` başlığı).
_CALISMA = Path(tempfile.mkdtemp(prefix="f10b-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "f10b.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"

sys.path.insert(0, str(BACKEND))


# --------------------------------------------------------------- statik ---


def test_goc_TEK_PLATFORM_TABLOSU_aciyor_ve_company_id_TASIMIYOR() -> None:
    """Göç `0091` TEK tablo açıyor ve o tablo PLATFORM tablosu.

    MUTASYON: `company_id` eklemek bunu ve FİRMALAR ARASI kapısını KIRMIZI
    yapar — iki firmaya bağlı bir çiftçi sınırı İKİYE KATLARDI.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'revision = "20260920_0091"' in kaynak
    assert 'down_revision = "20260918_0090"' in kaynak

    agac = ast.parse(kaynak)
    kurulan = [
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "create_table"
    ]
    assert len(kurulan) == 1, kurulan
    parca = ast.get_source_segment(kaynak, kurulan[0]) or ""
    assert '"company_id"' not in parca
    assert '"phone"' in parca and '"window_start"' in parca
    # Sayacın HAKEMİ bu kısıttır; süs değil (göç başlığı).
    assert 'sa.UniqueConstraint("phone", "window_start"' in parca


def test_YENI_TABLO_KIRACI_ENVANTERINE_GIRMIYOR() -> None:
    """`TENANT_TABLES` DEĞİŞMEZ: `company_id` taşımayan tablo kiracı değildir.

    Dışa aktarım listesini ŞEMADAN türetiyor
    (`routers/kiraci_disa_aktarim._kiraci_tablolari`), yani bu iddia ikinci
    bir elle-liste değil, aynı kuralın ikinci tanığıdır.
    """
    from app.whatsapp.schema import whatsapp_message_attempts
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert "company_id" not in whatsapp_message_attempts.c
    assert SAYAC_TABLO not in TENANT_TABLES


def test_IKI_BEYAZ_LISTE_KESISMIYOR() -> None:
    """Personel ve çiftçi araç kümeleri AYRI ve KESİŞİMSİZ (keşif §5.1).

    MUTASYON: çiftçi araçlarını `niyet.ARAC_BEYAZ_LISTESI`ne eklemek
    bunu KIRMIZI yapar — o gün tek bir `dene` çağrısı her iki kümeden
    araç koşturabilirdi.
    """
    from app.whatsapp.ciftci_yurutucu import CIFTCI_BEYAZ_LISTESI
    from app.whatsapp.niyet import ARAC_BEYAZ_LISTESI

    assert CIFTCI_BEYAZ_LISTESI == {"ciftci_ekstre", "ciftci_avans"}
    assert not (CIFTCI_BEYAZ_LISTESI & ARAC_BEYAZ_LISTESI)
    # Personel listesi bu PR'da KIMILDAMADI.
    assert len(ARAC_BEYAZ_LISTESI) == 7


def test_IKI_ARACIN_IKISI_DE_YURUTULEBILIYOR() -> None:
    """Gövde sözlüğünün anahtarları beyaz listeyle BİREBİR aynı.

    `yurutucu.ARAC_GOVDELERI`nin kuralı: eksik bir gövde, beyaz listeden
    geçmiş bir aracın `KeyError` ile DEAD üretmesi demekti.
    """
    from app.whatsapp.ciftci_yurutucu import (
        CIFTCI_ARAC_GOVDELERI,
        CIFTCI_BEYAZ_LISTESI,
    )

    assert set(CIFTCI_ARAC_GOVDELERI) == CIFTCI_BEYAZ_LISTESI


def test_CIFTCI_DALI_PERSONEL_SOZLUGUNU_ICE_AKTARMIYOR() -> None:
    """`_terim_cikar` ve `tahsilat_coz` çiftçi modüllerinde GEÇMİYOR.

    GREP'LENEBİLİR KAPI (iki modül başlığının sözü): serbest metinden cari
    adı çıkarımı çiftçinin yazdığı adın sorguya girebileceği TEK kapıydı;
    `tahsilat_coz` ise `whatsapp_pending_actions`e giden yazma yolunun
    başlangıcı. Yorum satırları ELENİYOR — gerekçeyi ANLATAN bir yorum
    kapıyı kırmamalı, çağrıyı YAPAN bir satır kırmalı.
    """
    for yol in (NIYET, YURUTUCU):
        kaynak = yol.read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        adlar = {
            d.attr for d in ast.walk(agac) if isinstance(d, ast.Attribute)
        } | {d.id for d in ast.walk(agac) if isinstance(d, ast.Name)}
        assert "_terim_cikar" not in adlar, yol.name
        assert "tahsilat_coz" not in adlar, yol.name


def test_KAPALI_AKTIVITE_KATALOGU_IKI_RIZA_OLAYI_TASIYOR() -> None:
    """`ACTION_TYPES` KAPALI bir katalogdur; iki yeni tip ONA eklendi.

    `log_activity` bilinmeyen tipi `ValueError` ile reddediyor, yani bu
    iki satır `_riza_yaz`ın ÖNKOŞULUDUR.
    """
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    assert "party.whatsapp_consent_granted" in ACTION_TYPES
    assert "party.whatsapp_consent_revoked" in ACTION_TYPES
    # Kaynak tipi F10-1a'da açılmıştı; bu PR onu DEĞİŞTİRMİYOR.
    assert "whatsapp_party" in RESOURCE_TYPES


def test_DAGITICIDA_CIFTCI_DALI_PERSONELDEN_SONRA() -> None:
    """SIRA KAPISI: `kimlik_secimi` çağrısı `taraf_coz`dan ÖNCE geliyor.

    Hiçbir DAVRANIŞ testi bu sırayı tek başına ölçemez — sıra tersine
    çevrilse bile personel/çiftçi ayrımı çoğu kurguda AYNI görünür;
    ayrışan tek kurgu "aynı numara İKİSİNE de bağlı"dır ve o kurgunun
    kapısı ayrıca var (PERSONEL KAZANIR). Bu kapı sırayı KAYNAKTA
    çiviliyor, yani ayrışmayan kurguları da kapsıyor.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    islev = next(
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.FunctionDef) and d.name == "_mesaj_isle"
    )
    izlenen = {"kimlik_secimi", "taraf_coz", "ciftci_cevap", "cevap_uret"}

    def _ad(cagri: ast.Call) -> str | None:
        # `cevap_uret` AYNI modülde tanımlı, yani ÇIPLAK bir `Name`dir;
        # ötekiler modül nitelemesiyle (`baglam.`, `taraf.`) çağrılıyor.
        # İkisini de görmeyen bir kapı, adı bulamadığı çağrıyı SESSİZCE
        # atlar ve sıra iddiası eksik ölçerdi.
        ad = getattr(cagri.func, "attr", None) or getattr(cagri.func, "id", None)
        return ad if ad in izlenen else None

    cagrilar = sorted(
        (d.lineno, _ad(d))
        for d in ast.walk(islev)
        if isinstance(d, ast.Call) and _ad(d) is not None
    )
    adlar = [ad for _, ad in cagrilar]
    assert adlar.index("kimlik_secimi") < adlar.index("taraf_coz")
    assert adlar.index("taraf_coz") < adlar.index("ciftci_cevap")
    # `cevap_uret` PERSONEL dalında ve ÇİFTÇİ dalından AYRI durmaya devam
    # ediyor; kaybolsaydı personel yolu ölürdü.
    assert "cevap_uret" in adlar


# ------------------------------------------------------------- fixtures ---


@pytest.fixture(scope="module")
def uygulama():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))
_WAMID = iter(range(1, 1_000_000))


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma; her birinde AYNI ADLI çiftçi (kiracı yalıtımı kurgusu).

    Kurgu `test_wa3_worker.py::test_KIRACI_YALITIMI_ayni_ad_komsu_firmada`dan
    alındı ve taraf defterine uyarlandı: aynı ad, FARKLI açılış bakiyesi.
    """
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    def temizle(db):
        for tablo in (
            "whatsapp_party_pairing_codes",
            BAGLANTI_TABLO,
            "whatsapp_pairing_codes",
            "whatsapp_links",
            "whatsapp_context",
            ESLESTIRME_SAYAC,
            SAYAC_TABLO,
            "whatsapp_inbound",
            "notification_consent_events",
            "notification_consents",
            "supplier_advances",
            "payments",
        ):
            db.execute(text(f"DELETE FROM {tablo}"))
        db.commit()

    n = next(_SAYAC)
    parola = "F10bTest!12345"
    an = datetime.now(timezone.utc)
    with SessionLocal() as db:
        temizle(db)

        def firma(ad: str) -> int:
            return db.execute(
                text(
                    "INSERT INTO companies(name,is_active,created_at)"
                    " VALUES(:a,1,:t) RETURNING id"
                ),
                {"a": ad, "t": an},
            ).scalar_one()

        firma_a = firma(f"F10b Alim Merkezi {n}")
        firma_b = firma(f"F10b Komsu Merkez {n}")

        personel = db.execute(
            text(
                "INSERT INTO app_users(username,email,email_verified,display_name,"
                "password_hash,role,is_active,must_change_password,created_at)"
                " VALUES(:k,:e,1,:k,:h,'admin',1,0,:t) RETURNING id"
            ),
            {
                "k": f"f10b-admin-{n}",
                "e": f"f10b-admin-{n}@f10b.invalid",
                "h": hash_password(parola),
                "t": an,
            },
        ).scalar_one()
        for cid in (firma_a, firma_b):
            db.execute(
                text(
                    "INSERT INTO user_company_memberships"
                    "(user_id,company_id,is_default,created_at)"
                    " VALUES(:u,:c,0,:t)"
                ),
                {"u": personel, "c": cid, "t": an},
            )

        def cari(tablo: str, cid: int, ad: str, acilis: int) -> int:
            return db.execute(
                text(
                    f"INSERT INTO {tablo}(name,is_active,company_id,"
                    "opening_balance,risk_limit,payment_term_days)"
                    " VALUES(:a,1,:c,:o,0,0) RETURNING id"
                ),
                {"a": ad, "c": cid, "o": acilis},
            ).scalar_one()

        # AYNI AD, İKİ FİRMA, FARKLI AÇILIŞ BAKİYESİ. Bakiye ayrımı kapının
        # KENDİSİDİR: `company_id` yüklemi düşerse cevapta KOMŞUNUN sayısı
        # görünür ve iki firmalı kurgu bunu RAKAMLA yakalar.
        ad = f"Ciftci Sungur {n}"
        tedarikci_a = cari("suppliers", firma_a, ad, 10000)
        tedarikci_b = cari("suppliers", firma_b, ad, 77000)
        musteri_a = cari("customers", firma_a, ad, 5000)
        db.commit()

    veri = {
        "firma_a": int(firma_a),
        "firma_b": int(firma_b),
        "tedarikci_a": int(tedarikci_a),
        "tedarikci_b": int(tedarikci_b),
        "musteri_a": int(musteri_a),
        "personel": int(personel),
        "kad": f"f10b-admin-{n}",
        "parola": parola,
        "ad": ad,
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


class _SahteSaglayici:
    """Ağa çıkmaz; gönderilenleri biriktirir (`test_wa3_worker` kopyası)."""

    def __init__(self) -> None:
        self.gonderilenler: list[tuple[str, str]] = []

    def metin_gonder(self, alici: str, metin: str) -> None:
        self.gonderilenler.append((alici, metin))


def _baglanti_ac(
    db, cid: int, party_type: str, party_id: int, telefon: str = NUMARA
) -> int:
    """Taraf bağlantısını DOĞRUDAN açar (kod akışı F10-1a'da ölçülüyor)."""
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


def _mesaj_yaz(db, metin: str, *, telefon: str = NUMARA, medya: str | None = None):
    from sqlalchemy import text

    from app.config import settings

    an = datetime.now(timezone.utc)
    return int(
        db.execute(
            text(
                "INSERT INTO whatsapp_inbound(wamid,sender_phone,phone_number_id,"
                "text,media_id,status,attempt_count,received_at)"
                " VALUES(:w,:p,:n,:m,:md,'RECEIVED',0,:t) RETURNING id"
            ),
            {
                "w": f"f10b-{next(_WAMID)}",
                "p": telefon,
                "n": settings.whatsapp_phone_number_id or "",
                "m": metin,
                "md": medya,
                "t": an,
            },
        ).scalar_one()
    )


def _konus(db, metin: str, *, telefon: str = NUMARA, medya: str | None = None):
    """GERÇEK dağıtıcıyı koşturur ve (gönderilenler, satır) döner.

    `bekleyenleri_isle` kullanılıyor, `_mesaj_isle` DEĞİL: kiralama,
    sonlandırma ve commit sınırı da ölçülsün.
    """
    from sqlalchemy import text

    from app.whatsapp import service

    satir_id = _mesaj_yaz(db, metin, telefon=telefon, medya=medya)
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


# ------------------------------------------------------------- davranış ---


def test_DAVRANIS_CIFTCI_PERSONEL_ARACINA_ULASAMIYOR(oturum, dunya, monkeypatch):
    """Personel araç cümleleri çiftçiye KAPSAM MESAJI döndürür.

    ÜÇ AYRI İDDİA:
      1. Dört ayrı personel cümlesinin dördü de kapsam mesajı alıyor.
      2. `niyet.tahsilat_coz` HİÇ çağrılmıyor (sayaç sıfır) — yani
         `whatsapp_pending_actions` yolunun BAŞLANGICI bile koşmuyor.
      3. Çiftçi kapsam metni personel araçlarının sözcüklerini ("stok",
         "tahsilat") TAŞIMIYOR; `niyet.KAPSAM_MESAJI` sızsaydı çiftçiye
         YANLIŞ bir söz verilirdi (keşif §5.2).
    """
    from app.whatsapp import niyet, service
    from app.whatsapp.ciftci_niyet import CIFTCI_KAPSAM_MESAJI

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    sayac = {"n": 0}
    gercek = niyet.tahsilat_coz

    def casus(metin):
        sayac["n"] += 1
        return gercek(metin)

    monkeypatch.setattr(service.niyet, "tahsilat_coz", casus)

    for cumle in (
        "84993120 stok",
        "kritik stok",
        "bu ay ciro",
        "Saban Korkmaz 20.000 nakit tahsilat",
    ):
        gonderilen, satir = _konus(oturum, cumle)
        assert satir["status"] == "ANSWERED", cumle
        assert gonderilen[-1][1] == CIFTCI_KAPSAM_MESAJI, cumle

    assert sayac["n"] == 0, "ciftci dalinda tahsilat_coz CAGRILMAMALI"
    assert "stok" not in CIFTCI_KAPSAM_MESAJI.lower()
    assert "tahsilat" not in CIFTCI_KAPSAM_MESAJI.lower()


def test_DAVRANIS_CARI_KOKU_CIFTCININ_KENDI_EKSTRESINI_VERIYOR(oturum, dunya):
    """"cari durum <ad>" → çiftçinin KENDİ ekstresi; yazdığı ad YOK SAYILIR.

    Keşif §4a `CARI_KOKLER`i çiftçi tarafında YENİDEN KULLANIYOR, yani bu
    cümle bir kapsam mesajı DEĞİL bir ekstre üretir — ama cümledeki AD
    sorguya GİRMEZ (`_terim_cikar` bu dalda yok). İddia tam olarak budur:
    cevapta çiftçinin KENDİ adı var, yazdığı YABANCI ad YOK.
    """
    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, satir = _konus(oturum, "cari durum Yabanci Cari Adi")
    assert satir["status"] == "ANSWERED"
    metin = gonderilen[-1][1]
    assert dunya["ad"] in metin
    assert "Yabanci Cari Adi" not in metin


def test_KIRACI_YALITIMI_ayni_ad_komsu_firmada_CIFTCI(oturum, dunya):
    """Aynı adlı çiftçi KOMŞU firmada; cevapta YALNIZ kendi firmasının sayısı.

    MUTASYON ADIYLA: `ciftci_ekstre`den (ya da `build_statement`ten)
    `company_id` yüklemini düşürmek komşunun 77.000'ini döndürür.
    """
    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, _ = _konus(oturum, "ekstre")
    metin = gonderilen[-1][1]
    assert "10.000,00" in metin
    assert "77.000,00" not in metin


def test_RIZASIZ_NUMARA_ERP_VERISI_ALMIYOR_ve_KVKK_SORULUYOR(oturum, dunya):
    """Rıza YOKKEN: veri DÖNMEZ, KVKK metni + EVET/HAYIR sorusu gider.

    MUTASYON: `_riza_degerlendir` çağrısını düşürmek bunu KIRMIZI yapar —
    bağlanmış ama rıza vermemiş bir numara bakiyeyi görürdü.
    """
    from sqlalchemy import text

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, satir = _konus(oturum, "ekstre")
    assert satir["status"] == "ANSWERED"
    metin = gonderilen[-1][1]
    assert "EVET" in metin and "HAYIR" in metin and "DUR" in metin
    # FİRMAYI ADIYLA ANIYOR (keşif §5.4: metin neyin gönderileceğini sayar).
    assert "Alim Merkezi" in metin
    # ERP RAKAMI YOK.
    assert "10.000,00" not in metin
    # Rıza defterine bu adımda HİÇBİR satır düşmedi.
    assert (
        oturum.execute(
            text("SELECT COUNT(*) FROM notification_consents WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == 0
    )


def test_EVET_RIZAYI_ACIYOR_OLAY_DUSUYOR_sonraki_mesaj_VERI_ALIYOR(oturum, dunya):
    """`EVET` → GRANTED + olay satırı + versiyon 1 → sonraki mesaj VERİ alır.

    Versiyon ve olay satırı `set_consent`in İÇİNDE yazılıyor; burada
    ölçülen şey `_riza_yaz`ın onu GERÇEKTEN çağırdığıdır.
    """
    from sqlalchemy import text

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    _konus(oturum, "ekstre")  # KVKK sorusu
    gonderilen, _ = _konus(oturum, "EVET")
    assert "Onayınız alındı" in gonderilen[-1][1]

    satir = (
        oturum.execute(
            text(
                "SELECT id,status,version,source FROM notification_consents"
                " WHERE company_id=:c AND party_type='SUPPLIER' AND party_id=:p"
            ),
            {"c": dunya["firma_a"], "p": dunya["tedarikci_a"]},
        )
        .mappings()
        .one()
    )
    assert satir["status"] == "GRANTED"
    assert int(satir["version"]) == 1
    assert satir["source"] == "PHONE"
    assert (
        oturum.execute(
            text(
                "SELECT COUNT(*) FROM notification_consent_events WHERE consent_id=:i"
            ),
            {"i": satir["id"]},
        ).scalar_one()
        >= 1
    )

    # Denetim izi: AKTÖR NULL, kaynak `whatsapp_party` (keşif §5.5).
    aktivite = (
        oturum.execute(
            text(
                "SELECT user_id,resource_type FROM activity_logs WHERE company_id=:c"
                " AND action_type='party.whatsapp_consent_granted'"
            ),
            {"c": dunya["firma_a"]},
        )
        .mappings()
        .all()
    )
    assert len(aktivite) == 1
    assert aktivite[0]["user_id"] is None
    assert aktivite[0]["resource_type"] == "whatsapp_party"

    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" in gonderilen[-1][1]


def test_TEKRARLANAN_EVET_VERSIYONU_SISIRMIYOR(oturum, dunya):
    """Rıza AÇIKKEN gelen "EVET" defteri KIMILDATMAZ (başlık).

    Aksi hâlde tekrarlanan tek bir kelime sınırsız `version` artışı ve
    sınırsız olay satırı üretebilirdi.
    """
    from sqlalchemy import text

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    once = oturum.execute(
        text("SELECT version FROM notification_consents WHERE company_id=:c"),
        {"c": dunya["firma_a"]},
    ).scalar_one()
    for _ in range(3):
        _konus(oturum, "EVET")
    sonra = oturum.execute(
        text("SELECT version FROM notification_consents WHERE company_id=:c"),
        {"c": dunya["firma_a"]},
    ).scalar_one()
    assert int(sonra) == int(once)


def test_HAYIR_REDDI_YAZIYOR_ve_KVKK_SORUSU_TEKRARLANMIYOR(oturum, dunya):
    """`HAYIR` → REVOKED satırı; sonraki soru GENEL metni alır, KVKK'yı DEĞİL.

    Kararın gerekçesi `ciftci_yurutucu` başlığında: no-op bırakılsaydı
    `evaluate_consent` her mesajda yine `NO_RECORD` der ve çiftçi aynı
    soruyu SONSUZA DEK yeniden alırdı.
    """
    from sqlalchemy import text

    from app.whatsapp.ciftci_niyet import RIZA_KAPALI_MESAJI

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _konus(oturum, "ekstre")
    gonderilen, _ = _konus(oturum, "HAYIR")
    assert "göndermeyeceğiz" in gonderilen[-1][1]

    assert (
        oturum.execute(
            text("SELECT status FROM notification_consents WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == "REVOKED"
    )

    gonderilen, _ = _konus(oturum, "ekstre")
    assert gonderilen[-1][1] == RIZA_KAPALI_MESAJI

    # FİKİR DEĞİŞTİRİLEBİLİR: `EVET` reddi geri alır (başlık).
    gonderilen, _ = _konus(oturum, "EVET")
    assert "Onayınız alındı" in gonderilen[-1][1]
    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" in gonderilen[-1][1]


def test_HAYIR_ACIK_RIZAYI_KAPATIYOR_tekrari_YAZMIYOR_EVET_geri_aciyor(oturum, dunya):
    """GRANTED → `HAYIR` → REVOKED v2 → kapalı metin; `HAYIR` yine → v2; `EVET` → v3.

    Runtime lens tur 2: `HAYIR` açık rızada HİÇBİR ŞEY yazmıyordu, çiftçiye
    "bu numaraya bilgi göndermeyeceğiz" deniyor ve sonraki EKSTRE rakam
    dönüyordu. `consent_at` bu yolda DEĞİŞMEZ (iz, tarihçe).

    MUTASYON: `ciftci_cevap`ın `HAYIR` dalında `karar["allowed"]` koşulunu
    düşürmek (yalnız NO_RECORD'da yazmak) bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    from app.whatsapp.ciftci_niyet import RIZA_KAPALI_MESAJI

    link = _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _konus(oturum, "ekstre")  # KVKK sorusu
    _konus(oturum, "EVET")

    def defter() -> tuple[str, int, int]:
        satir = (
            oturum.execute(
                text(
                    "SELECT id,status,version FROM notification_consents"
                    " WHERE company_id=:c AND party_type='SUPPLIER' AND party_id=:p"
                ),
                {"c": dunya["firma_a"], "p": dunya["tedarikci_a"]},
            )
            .mappings()
            .one()
        )
        olay = oturum.execute(
            text(
                "SELECT COUNT(*) FROM notification_consent_events WHERE consent_id=:i"
            ),
            {"i": satir["id"]},
        ).scalar_one()
        return satir["status"], int(satir["version"]), int(olay)

    def geri_cekme_denetimi() -> int:
        return int(
            oturum.execute(
                text(
                    "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c"
                    " AND action_type='party.whatsapp_consent_revoked'"
                ),
                {"c": dunya["firma_a"]},
            ).scalar_one()
        )

    def damga():
        return oturum.execute(
            text(f"SELECT consent_at FROM {BAGLANTI_TABLO} WHERE id=:i"),
            {"i": link},
        ).scalar_one()

    durum, surum, olay_once = defter()
    assert (durum, surum) == ("GRANTED", 1)
    damga_once = damga()
    assert damga_once is not None
    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" in gonderilen[-1][1]

    gonderilen, _ = _konus(oturum, "HAYIR")
    assert "göndermeyeceğiz" in gonderilen[-1][1]
    durum, surum, olay = defter()
    assert (durum, surum) == ("REVOKED", 2)
    assert olay == olay_once + 1
    assert geri_cekme_denetimi() == 1
    assert damga() == damga_once, "consent_at iz olarak KALIR"

    gonderilen, _ = _konus(oturum, "ekstre")
    assert gonderilen[-1][1] == RIZA_KAPALI_MESAJI
    assert "10.000,00" not in gonderilen[-1][1]

    # Tekrarlanan HAYIR: defter KIMILDAMAZ, denetim satırı DÜŞMEZ.
    _konus(oturum, "HAYIR")
    assert defter() == ("REVOKED", 2, olay)
    assert geri_cekme_denetimi() == 1

    gonderilen, _ = _konus(oturum, "EVET")
    assert "Onayınız alındı" in gonderilen[-1][1]
    assert defter()[:2] == ("GRANTED", 3)
    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" in gonderilen[-1][1]


def test_DUR_RIZAYI_VE_BAGLANTIYI_IKISINI_BIRDEN_KAPATIYOR(oturum, dunya):
    """`DUR` → rıza REVOKED **VE** bağlantı `is_active=False`; sonrası BAĞSIZ.

    MUTASYON: yalnız rızayı çekip bağlantıyı açık bırakmak son adımı
    KIRMIZI yapar — numara hâlâ çözülebilir kalır ve `taraf_coz` onu
    yarın yine aday sayar (keşif §5.4).
    """
    from sqlalchemy import text

    from app.whatsapp.baglam import TANINMAYAN_NUMARA_MESAJI

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, satir = _konus(oturum, "DUR")
    assert satir["status"] == "ANSWERED"
    assert "kapatıldı" in gonderilen[-1][1]

    assert (
        oturum.execute(
            text("SELECT status FROM notification_consents WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == "REVOKED"
    )
    assert (
        int(
            oturum.execute(
                text(
                    f"SELECT COUNT(*) FROM {BAGLANTI_TABLO}"
                    " WHERE company_id=:c AND is_active=1"
                ),
                {"c": dunya["firma_a"]},
            ).scalar_one()
        )
        == 0
    )
    assert (
        oturum.execute(
            text(
                "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c"
                " AND action_type='party.whatsapp_consent_revoked'"
            ),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == 1
    )

    # DUR'dan SONRA numara BAĞSIZDIR: çiftçi dalına hiç girmez.
    gonderilen, _ = _konus(oturum, "ekstre")
    assert gonderilen[-1][1] == TANINMAYAN_NUMARA_MESAJI


def test_IPTAL_DUR_ILE_AYNI_ama_SERBEST_CUMLE_KOMUT_DEGIL(oturum, dunya):
    """`İPTAL` TAM eşleşmedir; "iptal etmek istemiyorum" komut DEĞİLDİR.

    MUTASYON: regex'i `search`e ya da bir KÖKE çevirmek ilk adımı KIRMIZI
    yapar — rızayı geri çeken bir komutun çıkarımla tetiklenmesi,
    kullanıcının vermediği bir kararı onun defterine yazmak olurdu.
    """
    from sqlalchemy import text

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, _ = _konus(oturum, "iptal etmek istemiyorum")
    assert "kapatıldı" not in gonderilen[-1][1]
    assert (
        oturum.execute(
            text("SELECT status FROM notification_consents WHERE company_id=:c"),
            {"c": dunya["firma_a"]},
        ).scalar_one()
        == "GRANTED"
    )

    gonderilen, _ = _konus(oturum, "İPTAL")
    assert "kapatıldı" in gonderilen[-1][1]


def test_PERSONEL_KAZANIR_ayni_numara_ikisine_de_bagliyken(oturum, dunya):
    """Numara hem personele hem cariye bağlıysa PERSONEL dalı koşar.

    Keşif §5.1: personel kimliği DAHA DAR bir zincirden geçmiştir
    (aktif kullanıcı + aktif firma + üyelik) ve DARALTICI OLAN KAZANIR.

    MUTASYON: çiftçi dalını `secim.kimlik is None` denetiminin ÜSTÜNE
    almak bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    from app.whatsapp.ciftci_niyet import CIFTCI_KAPSAM_MESAJI

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    an = datetime.now(timezone.utc)
    oturum.execute(
        text(
            "INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
            "created_at,updated_at) VALUES(:c,:u,:p,1,:t,:t)"
        ),
        {"c": dunya["firma_a"], "u": dunya["personel"], "p": NUMARA, "t": an},
    )
    oturum.commit()

    gonderilen, _ = _konus(oturum, "84993120 stok")
    # PERSONEL yolu koştu: stok aracının "bulunamadı" cevabı da bir ARAÇ
    # cevabıdır ve ÇİFTÇİ kapsam mesajından FARKLIDIR.
    assert gonderilen[-1][1] != CIFTCI_KAPSAM_MESAJI


def _riza_durumu(db, cid: int, sid: int) -> str | None:
    """`(firma, tedarikçi)` rıza satırının durumu; satır YOKSA ``None`` (NO_RECORD)."""
    from sqlalchemy import text

    return db.execute(
        text(
            "SELECT status FROM notification_consents WHERE company_id=:c"
            " AND party_type='SUPPLIER' AND party_id=:p AND channel='WHATSAPP'"
        ),
        {"c": cid, "p": sid},
    ).scalar_one_or_none()


def _iki_firmali_ciftci(db, dunya) -> tuple[int, int]:
    """AYNI numara İKİ firmada, RIZA YOK. Sıra `taraf_coz`un sırasıdır (A=1, B=2)."""
    a = _baglanti_ac(db, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    b = _baglanti_ac(db, dunya["firma_b"], "SUPPLIER", dunya["tedarikci_b"])
    return a, b


def test_COK_FIRMADA_SECIM_SORULUYOR_RASTGELE_SECILMIYOR(oturum, dunya):
    """İki firmada aktif numara → NUMARALI liste; hiçbir firmanın rakamı YOK.

    MUTASYON: `_firma_coz`da `adaylar[0]`ı seçmek bunu KIRMIZI yapar — ve
    o mutant YANLIŞ TENANT'ın verisini döndürürdü.

    RIZA WHATSAPP'TAN VERİLİYOR, `_riza_ver` İLE DEĞİL: bu testin önceki
    hâli rızayı doğrudan deftere yazıyordu ve bu yüzden çok firmalı
    çiftçinin rıza VEREMEDİĞİNİ hiç görmedi (runtime lens NO-GO, tur 1).
    """
    _iki_firmali_ciftci(oturum, dunya)

    gonderilen, _ = _konus(oturum, "ekstre")
    metin = gonderilen[-1][1]
    assert "1." in metin and "2." in metin
    assert "Alim Merkezi" in metin and "Komsu Merkez" in metin
    assert "10.000,00" not in metin and "77.000,00" not in metin

    # SIRA ÖNEKİ firmayı çözüyor ve HER BİRİ KENDİ rızasını KENDİ sayısıyla veriyor.
    _konus(oturum, "1 EVET")
    _konus(oturum, "2 EVET")
    gonderilen, _ = _konus(oturum, "1 ekstre")
    assert "10.000,00" in gonderilen[-1][1]
    assert "77.000,00" not in gonderilen[-1][1]
    gonderilen, _ = _konus(oturum, "2 ekstre")
    assert "77.000,00" in gonderilen[-1][1]
    assert "10.000,00" not in gonderilen[-1][1]


def test_COK_FIRMALI_CIFTCI_LENS_DIZISI_RIZAYI_YALNIZ_BIRINCI_FIRMAYA_VERIYOR(
    oturum, dunya
):
    """Runtime lens'in TAM dizisi: EKSTRE / EVET / 1 EVET / 1 evet / 1. EVET / 1 HAYIR.

    Düzeltmeden önce bu dizi SIFIR rıza satırıyla bitiyordu: `evet_mi`
    TAM metne bakıyordu ve "1 EVET"i tanımıyordu. Şimdi: 1. firma GRANTED
    (v1), 2. firma NO_RECORD (satır YOK).

    Dizinin SONUNDAKİ "1 HAYIR" 1. firmayı KAPATIR (GRANTED → REVOKED v2;
    runtime lens tur 2). Düzeltme 2'de burada "GRANTED kalır" iddiası
    duruyordu ve çiftçiye "göndermeyeceğiz" dendiği hâlde sonraki EKSTRE
    rakam dönüyordu. 2. firma YİNE NO_RECORD — `HAYIR` da firma BAŞINADIR.

    MUTASYON: `ciftci_cevap`ta `evet_mi(komut)`u `evet_mi(metin)`e geri
    çevirmek üçüncü adımı KIRMIZI yapar (sonsuz KVKK döngüsü).
    """
    from sqlalchemy import text

    from app.whatsapp.ciftci_niyet import RIZA_ALINDI_MESAJI

    _iki_firmali_ciftci(oturum, dunya)

    gonderilen, _ = _konus(oturum, "EKSTRE")
    assert "Sorunuzun başına firma numarasını" in gonderilen[-1][1]

    # ÖNEKSİZ EVET N>1'de HİÇBİR ŞEY YAZMAZ, önekli örneği öğretir.
    gonderilen, _ = _konus(oturum, "EVET")
    assert '"1 EVET"' in gonderilen[-1][1]
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) is None
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) is None

    gonderilen, _ = _konus(oturum, "1 EVET")
    assert gonderilen[-1][1] == RIZA_ALINDI_MESAJI

    for tekrar in ("1 evet", "1. EVET"):
        _konus(oturum, tekrar)

    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) == "GRANTED"
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) is None

    def surum() -> int:
        return int(
            oturum.execute(
                text("SELECT version FROM notification_consents WHERE company_id=:c"),
                {"c": dunya["firma_a"]},
            ).scalar_one()
        )

    # Tekrar kuralı firma başına: "1 evet" / "1. EVET" versiyonu şişirmedi.
    assert surum() == 1

    # "1 EKSTRE" RAKAM döner.
    gonderilen, _ = _konus(oturum, "1 EKSTRE")
    assert "10.000,00" in gonderilen[-1][1]

    # "1 HAYIR" AÇIK rızayı KAPATIR ve sonraki "1 EKSTRE" rakam DÖNMEZ.
    gonderilen, _ = _konus(oturum, "1 HAYIR")
    assert "göndermeyeceğiz" in gonderilen[-1][1]
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) == "REVOKED"
    assert surum() == 2
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) is None
    gonderilen, _ = _konus(oturum, "1 EKSTRE")
    assert "10.000,00" not in gonderilen[-1][1]

    gonderilen, _ = _konus(oturum, "2 EKSTRE")
    metin = gonderilen[-1][1]
    assert "Komsu Merkez" in metin and "Alim Merkezi" not in metin
    assert metin.endswith("2 EVET / 2 HAYIR"), metin
    assert "77.000,00" not in metin and "10.000,00" not in metin
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) is None


def test_COK_FIRMADA_ONEKLI_EKSTRE_O_FIRMANIN_KVKK_METNINI_ALIYOR(oturum, dunya):
    """Rızasız "1 EKSTRE" → 1. firmanın ADIYLA KVKK metni, "1 EVET / 1 HAYIR" ile biter."""
    _iki_firmali_ciftci(oturum, dunya)

    gonderilen, _ = _konus(oturum, "1 EKSTRE")
    metin = gonderilen[-1][1]
    assert "Alim Merkezi" in metin and "Komsu Merkez" not in metin
    assert metin.endswith("1 EVET / 1 HAYIR"), metin
    assert "10.000,00" not in metin


def test_COK_FIRMADA_ONEKSIZ_EVET_HAYIR_HICBIR_SEY_YAZMIYOR(oturum, dunya):
    """Öneksiz `EVET`/`HAYIR` N>1'de: rıza satırı YOK, denetim satırı YOK.

    Şef kararı: bütün firmalara YAYMAK yok, birini TAHMİN etmek yok.
    MUTASYON: `_firma_coz`da öneksiz EVET'i `adaylar[0]`a bağlamak ya da
    bütün adaylara yazmak bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    _iki_firmali_ciftci(oturum, dunya)

    for kelime in ("EVET", "evet", "HAYIR", "hayır"):
        gonderilen, _ = _konus(oturum, kelime)
        metin = gonderilen[-1][1]
        assert '"1 EVET"' in metin and '"1 HAYIR"' in metin, kelime
        assert "Alim Merkezi" in metin and "Komsu Merkez" in metin, kelime

    for cid in (dunya["firma_a"], dunya["firma_b"]):
        assert (
            oturum.execute(
                text("SELECT COUNT(*) FROM notification_consents WHERE company_id=:c"),
                {"c": cid},
            ).scalar_one()
            == 0
        )
        assert (
            oturum.execute(
                text(
                    "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c"
                    " AND action_type LIKE 'party.whatsapp_consent_%'"
                ),
                {"c": cid},
            ).scalar_one()
            == 0
        )


def test_COK_FIRMADA_2_HAYIR_YALNIZ_IKINCI_FIRMAYA_REVOKED_YAZIYOR(oturum, dunya):
    """"2 HAYIR" → 2. firma REVOKED, 1. firma NO_RECORD; geri dönüş metni "2 EVET"."""
    _iki_firmali_ciftci(oturum, dunya)

    gonderilen, _ = _konus(oturum, "2 HAYIR")
    assert "2 EVET" in gonderilen[-1][1]
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) == "REVOKED"
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) is None

    # Reddedilmiş firmaya soru: GENEL kapalı metni, ONUN önekiyle.
    gonderilen, _ = _konus(oturum, "2 EKSTRE")
    assert "gönderemiyoruz" in gonderilen[-1][1]
    assert "2 EVET" in gonderilen[-1][1]
    assert "77.000,00" not in gonderilen[-1][1]

    # Fikir değiştirilebilir — YALNIZ o firmada.
    _konus(oturum, "2 EVET")
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) == "GRANTED"
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) is None


def test_COK_FIRMADA_1_HAYIR_YALNIZ_BIRINCI_ACIK_RIZAYI_KAPATIYOR(oturum, dunya):
    """İki firma GRANTED; "1 HAYIR" → 1. firma REVOKED, 2. firma GRANTED KALIR.

    Runtime lens tur 2'nin çok firmalı hâli: `HAYIR`ın açık rızayı kapatması
    da firma BAŞINADIR — öneki atılmış komut YALNIZ seçilen `(firma, taraf)`a
    yazar. "2 EKSTRE" rakamını VERMEYE devam eder.
    """
    _iki_firmali_ciftci(oturum, dunya)
    _konus(oturum, "1 EVET")
    _konus(oturum, "2 EVET")

    gonderilen, _ = _konus(oturum, "1 HAYIR")
    assert "göndermeyeceğiz" in gonderilen[-1][1]
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) == "REVOKED"
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) == "GRANTED"

    gonderilen, _ = _konus(oturum, "1 EKSTRE")
    assert "10.000,00" not in gonderilen[-1][1]
    gonderilen, _ = _konus(oturum, "2 EKSTRE")
    assert "77.000,00" in gonderilen[-1][1]
    assert "10.000,00" not in gonderilen[-1][1]


@pytest.mark.parametrize("komut", ["DUR", "1 DUR", "İPTAL"])
def test_COK_FIRMADA_DUR_GLOBAL_IKISINI_DE_KAPATIYOR(oturum, dunya, komut):
    """`DUR` (önekli yazılışı da) BÜTÜN adaylarda rızayı çeker VE bağlantıyı kapatır."""
    from sqlalchemy import text

    _iki_firmali_ciftci(oturum, dunya)
    _konus(oturum, "1 EVET")
    _konus(oturum, "2 EVET")

    gonderilen, _ = _konus(oturum, komut)
    assert "kapatıldı" in gonderilen[-1][1]
    assert _riza_durumu(oturum, dunya["firma_a"], dunya["tedarikci_a"]) == "REVOKED"
    assert _riza_durumu(oturum, dunya["firma_b"], dunya["tedarikci_b"]) == "REVOKED"
    assert (
        int(
            oturum.execute(
                text(f"SELECT COUNT(*) FROM {BAGLANTI_TABLO} WHERE is_active=1")
            ).scalar_one()
        )
        == 0
    )


# ------------------------------------------------------------ consent_at ---


def test_CONSENT_AT_EVETTE_YAZILIYOR_yonetici_listesinde_GORUNUYOR(
    oturum, dunya, uygulama
):
    """`EVET` → `consent_at` O bağlantıda dolar; yönetici listesi onu gösterir.

    Çok firmalı kurguda ölçülüyor: "1 EVET" YALNIZ 1. bağlantıyı damgalar.
    `DUR` damgayı SİLMEZ (tarihçe, `taraf` modül başı).
    """
    from sqlalchemy import text

    a, b = _iki_firmali_ciftci(oturum, dunya)

    def damga(link_id: int):
        return oturum.execute(
            text(f"SELECT consent_at FROM {BAGLANTI_TABLO} WHERE id=:i"),
            {"i": link_id},
        ).scalar_one()

    _konus(oturum, "1 EKSTRE")
    assert damga(a) is None
    _konus(oturum, "1 EVET")
    assert damga(a) is not None
    assert damga(b) is None

    r = uygulama.post(
        "/api/auth/login",
        json={"username": dunya["kad"], "password": dunya["parola"]},
    )
    assert r.status_code == 200, r.text
    basliklar = {
        "Authorization": "Bearer " + r.json()["access_token"],
        "X-Company-ID": str(dunya["firma_a"]),
    }
    liste = uygulama.get(
        "/api/whatsapp/party-links",
        headers=basliklar,
        params={"party_type": "SUPPLIER", "party_id": dunya["tedarikci_a"]},
    )
    assert liste.status_code == 200, liste.text
    satirlar = liste.json()
    assert [s["id"] for s in satirlar] == [a]
    # TEL BİÇİMİ UTC (H73, `app/zaman.py`): SQLite naive döndürür ve
    # yardımcıdan geçmeseydi sonek TAŞIMAZDI; PG ikizi `+03:00`ı ölçüyor.
    tel = satirlar[0]["consent_at"]
    assert isinstance(tel, str) and tel.endswith("+00:00"), tel
    assert datetime.fromisoformat(tel).utcoffset() == timedelta(0)

    _konus(oturum, "DUR")
    assert damga(a) is not None, "DUR damgayi silmemeli (tarihce)"


def test_CONSENT_AT_IZDIR_KARAR_DEGIL_tek_okuyucu_yonetici_listesi(oturum, dunya):
    """`consent_at`i çevirmek `evaluate_consent`in kararını DEĞİŞTİRMEZ.

    İKİ YÖNDE: GRANTED rızada damgayı SİLMEK veriyi kesmez; REVOKED rızada
    damgayı DOLDURMAK veriyi açmaz. Ve kaynakta `.consent_at` OKUYAN tek
    dosya yöneticinin listesidir — yarın bir izin kararı damgaya
    bakmaya başlarsa bu kapı KIRMIZI olur.
    """
    from sqlalchemy import text

    from app.notifications import consents

    link = _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    def karar() -> bool:
        return consents.evaluate_consent(
            oturum,
            company_id=dunya["firma_a"],
            party_type="SUPPLIER",
            party_id=dunya["tedarikci_a"],
            channel="WHATSAPP",
            recipient=NUMARA,
        )["allowed"]

    def damgala(deger) -> None:
        oturum.execute(
            text(f"UPDATE {BAGLANTI_TABLO} SET consent_at=:d WHERE id=:i"),
            {"d": deger, "i": link},
        )
        oturum.commit()

    _konus(oturum, "EVET")
    assert karar() is True

    damgala(None)
    assert karar() is True
    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" in gonderilen[-1][1]

    consents.set_consent(
        oturum,
        company_id=dunya["firma_a"],
        party_type="SUPPLIER",
        party_id=dunya["tedarikci_a"],
        channel="WHATSAPP",
        granted=False,
        source="PHONE",
        source_ref=None,
        recipient=NUMARA,
        user_id=None,
    )
    damgala(datetime.now(timezone.utc))
    assert karar() is False
    gonderilen, _ = _konus(oturum, "ekstre")
    assert "10.000,00" not in gonderilen[-1][1]

    okuyanlar = set()
    for yol in (BACKEND / "app").rglob("*.py"):
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        if any(
            isinstance(d, ast.Attribute) and d.attr == "consent_at"
            for d in ast.walk(agac)
        ):
            okuyanlar.add(yol.relative_to(BACKEND).as_posix())
    assert okuyanlar == {"app/routers/whatsapp.py"}, okuyanlar


def test_MEDYA_CIFTCIDEN_GELIRSE_FATURA_OZETI_DONMUYOR(oturum, dunya):
    """Çiftçinin fotoğrafı bir FATURA DEĞİLDİR → kapsam mesajı (keşif §5.2)."""
    from app.whatsapp.ciftci_niyet import CIFTCI_KAPSAM_MESAJI

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, satir = _konus(oturum, "", medya="media-1")
    assert satir["status"] == "ANSWERED"
    assert gonderilen[-1][1] == CIFTCI_KAPSAM_MESAJI


# ----------------------------------------------------------- hız sınırı ---


def test_HIZ_SINIRI_ONALTINCI_ISLENIR_CEVAPLANMAZ_YIRMIBIRINCI_ISLENMEZ(
    oturum, dunya
):
    """15 cevap / 20 işleme sınırı; İKİ eşik İKİ `last_error` üretir.

    `PAIRING_CEVAP_SINIRI`nin gerekçesi: ilk mesajlarda kullanıcı gerçek
    bir hata yapmış olabilir; ötesinde sessiz düşürme Meta mesaj maliyeti
    üzerinden kurulacak bir masraf saldırısını kapatır.
    """
    from app.whatsapp import schema

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    assert schema.MESAJ_PENCERE_DAKIKA == 15
    assert schema.MESAJ_CEVAP_SINIRI == 15
    assert schema.MESAJ_PENCERE_SINIRI == 20

    for sira in range(1, 16):
        gonderilen, satir = _konus(oturum, "ekstre")
        assert satir["status"] == "ANSWERED", sira
        assert gonderilen, sira

    # 16..20: İŞLENİR ama CEVAPLANMAZ.
    for sira in range(16, 21):
        gonderilen, satir = _konus(oturum, "ekstre")
        assert gonderilen == [], sira
        assert satir["status"] == "IGNORED", sira
        assert satir["last_error"] == "cevap_siniri", sira

    # 21+: HİÇ İŞLENMEZ.
    gonderilen, satir = _konus(oturum, "ekstre")
    assert gonderilen == []
    assert satir["status"] == "IGNORED"
    assert satir["last_error"] == "mesaj_siniri"


def test_HIZ_SINIRI_PENCERE_DONUNCE_SIFIRLANIYOR(oturum, dunya):
    """Sabit pencere: bir sonraki 15 dakikalık kova YENİ bir sayaçtır."""
    from app.whatsapp import ciftci_yurutucu

    an = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    for _ in range(20):
        ciftci_yurutucu.mesaj_deneme_say(oturum, NUMARA, simdi=an)
    oturum.commit()
    assert ciftci_yurutucu.mesaj_deneme_say(oturum, NUMARA, simdi=an) == 21

    sonraki = an + timedelta(minutes=15)
    assert ciftci_yurutucu.mesaj_deneme_say(oturum, NUMARA, simdi=sonraki) == 1


def test_SAYAC_NUMARA_BASINA_FIRMALAR_ARASI(oturum, dunya):
    """Sayaç NUMARA başınadır; iki firmaya bağlı çiftçi sınırı KATLAYAMAZ.

    MUTASYON ADIYLA: tabloya `company_id` eklemek bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    from app.whatsapp.schema import whatsapp_message_attempts

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _baglanti_ac(oturum, dunya["firma_b"], "SUPPLIER", dunya["tedarikci_b"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_b"], "SUPPLIER", dunya["tedarikci_b"])

    _konus(oturum, "1 ekstre")
    _konus(oturum, "2 ekstre")

    # İKİ FİRMA, TEK SATIR: kova NUMARANIN kendisidir.
    satirlar = (
        oturum.execute(text(f"SELECT phone,attempt_count FROM {SAYAC_TABLO}"))
        .mappings()
        .all()
    )
    assert len(satirlar) == 1
    assert satirlar[0]["phone"] == NUMARA
    assert int(satirlar[0]["attempt_count"]) == 2
    assert "company_id" not in whatsapp_message_attempts.c


def test_PERSONEL_YOLU_MESAJ_SAYACINA_DOKUNMUYOR(oturum, dunya):
    """KAPSAM KARARI (keşif §5.3): sınır YALNIZ çiftçi dalındadır.

    Personel yolunda numara başına sınır bugün YOKTUR ve bu PR onu
    DEĞİŞTİRMEZ; kapı o kararı yazılı hâle getiriyor, iddia etmiyor.
    """
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    oturum.execute(
        text(
            "INSERT INTO whatsapp_links(company_id,user_id,phone,is_active,"
            "created_at,updated_at) VALUES(:c,:u,:p,1,:t,:t)"
        ),
        {"c": dunya["firma_a"], "u": dunya["personel"], "p": IKINCI_NUMARA, "t": an},
    )
    oturum.commit()

    _konus(oturum, "bu ay ciro", telefon=IKINCI_NUMARA)
    assert (
        oturum.execute(
            text(f"SELECT COUNT(*) FROM {SAYAC_TABLO} WHERE phone=:p"),
            {"p": IKINCI_NUMARA},
        ).scalar_one()
        == 0
    )


def test_TEK_BAGLA_SAYACI_BIR_ARTIRIR(oturum, dunya):
    """Bir `BAĞLA <KOD>` mesajı eşleştirme penceresini BİR artırır, İKİ değil.

    ÖLÇÜLEN TUZAK: dağıtıcı iki defteri de deniyor ve İKİSİ DE kendi adım
    1'inde `deneme_say`i çağırıyordu — yani tek yanlış kod sayacı İKİ
    yakardı ve personelin 5'lik sınırı ÜÇ denemede ısırırdı.

    MUTASYON: `_bagla_akisi`de `deneme=` geçirmemek bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    _konus(oturum, "BAGLA ABCD-EFGH-JKLM")

    sayi = oturum.execute(
        text(f"SELECT attempt_count FROM {ESLESTIRME_SAYAC} WHERE phone=:p"),
        {"p": NUMARA},
    ).scalar_one()
    assert int(sayi) == 1


def test_TARAF_KODU_DAGITICIDAN_BAGLANIYOR(oturum, dunya):
    """`BAĞLA <KOD>` taraf defterini de deniyor ve ÇİFTÇİ başarı metni gidiyor.

    F10-1a'da `taraf.kod_kullan` vardı ama dağıtıcı ona HİÇ uğramıyordu;
    bu adım o bağlantıyı ölçüyor.
    """
    from app.whatsapp import taraf

    uretilen = taraf.kod_uret(
        oturum,
        dunya["firma_a"],
        "SUPPLIER",
        dunya["tedarikci_a"],
        hedef_telefon=NUMARA,
    )
    oturum.commit()

    gonderilen, satir = _konus(oturum, f"BAGLA {uretilen.kod}")
    assert satir["status"] == "ANSWERED"
    assert gonderilen[-1][1] == taraf.BASARI_MESAJI


def test_H78_BAGLANTI_KODU_URETEN_PERSONELI_TASIYOR(oturum, dunya):
    """`created_by` kod satırından bağlantı satırına TAŞINIYOR.

    H78 (#154 mercek bulgusu): sütunun yorumu "Kodu üreten personel"
    diyordu ama `taraf.kod_kullan` oraya `None` yazıyordu — yorum ile veri
    AYRIŞIYORDU ve bağlantıyı hangi personelin açtırdığı denetimde
    GÖRÜNMÜYORDU.
    """
    from sqlalchemy import text

    from app.whatsapp import taraf

    uretilen = taraf.kod_uret(
        oturum,
        dunya["firma_a"],
        "SUPPLIER",
        dunya["tedarikci_a"],
        hedef_telefon=NUMARA,
        created_by=dunya["personel"],
    )
    oturum.commit()
    sonuc = taraf.kod_kullan(oturum, NUMARA, uretilen.kod)
    oturum.commit()
    assert sonuc.basarili

    assert (
        int(
            oturum.execute(
                text(f"SELECT created_by FROM {BAGLANTI_TABLO} WHERE id=:i"),
                {"i": sonuc.link_id},
            ).scalar_one()
        )
        == dunya["personel"]
    )


# -------------------------------------------------------------- ekstre ---


def test_EKSTRE_BASLIGI_MASKELI_ve_RAKAMLAR_BUILD_STATEMENT_ILE_AYNI(oturum, dunya):
    """Cevapta VKN/adres/e-posta YOK; rakamlar `rol=""` çağrısıyla BİREBİR.

    MUTASYON: `ciftci_ekstre`ye `rol="admin"` geçirmek maskeyi düşürür ve
    ilk üç iddiayı KIRMIZI yapar.
    """
    from sqlalchemy import text

    from app.statement import build_statement
    from app.whatsapp.niyet import para_tr

    # Maskeli alanları DOLDUR ki maskelenip maskelenmediği ÖLÇÜLEBİLSİN.
    oturum.execute(
        text(
            "UPDATE suppliers SET tax_number='1234567890',"
            " address='Tekirdag Merkez Mah.', email='ciftci@example.invalid',"
            " phone='05321112233' WHERE id=:i AND company_id=:c"
        ),
        {"i": dunya["tedarikci_a"], "c": dunya["firma_a"]},
    )
    oturum.commit()

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    gonderilen, _ = _konus(oturum, "ekstre")
    metin = gonderilen[-1][1]

    assert "1234567890" not in metin
    assert "Tekirdag Merkez" not in metin
    assert "ciftci@example.invalid" not in metin

    ekstre = build_statement(oturum, dunya["firma_a"], "supplier", dunya["tedarikci_a"])
    assert para_tr(Decimal(str(ekstre.closing_balance))) in metin
    assert para_tr(Decimal(str(ekstre.total_debit))) in metin
    # BEŞ SATIRI AŞMIYOR (keşif §4a).
    assert len(metin.splitlines()) <= 5


def test_EKSTRE_AY_ADI_DONEMI_DARALTIYOR(oturum, dunya):
    """"EKSTRE EYLÜL" → Eylül penceresi; gelecek ay ÖNCEKİ yıla düşer."""
    from app.whatsapp.ciftci_niyet import ay_araligi

    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])

    bugun = date(2026, 9, 20)
    assert ay_araligi("ekstre eylul", bugun) == (date(2026, 9, 1), date(2026, 9, 30))
    # Gelecekteki ay ÖNCEKİ yıla düşer (modül başlığı).
    assert ay_araligi("ekstre aralik", bugun) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
    )
    # Ay adı GEÇMEYEN mesajda dönem çözümü YOK (varsayılan: içinde
    # bulunulan ay, `statement.default_period`).
    assert ay_araligi("ekstre", bugun) is None

    gonderilen, satir = _konus(oturum, "EKSTRE EYLUL")
    assert satir["status"] == "ANSWERED"
    assert "30.09" in gonderilen[-1][1]


# --------------------------------------------------------------- avans ---


def _avans_yaz(oturum, cid: int, sid: int, tutar: str, kalan: str, gun: str) -> None:
    from sqlalchemy import text

    an = datetime.now(timezone.utc)
    odeme = oturum.execute(
        text(
            "INSERT INTO payments(entity_type,entity_id,amount,payment_date,"
            "payment_method,company_id) VALUES('supplier',:e,:a,:d,'cash',:c)"
            " RETURNING id"
        ),
        {"e": sid, "a": tutar, "d": gun, "c": cid},
    ).scalar_one()
    oturum.execute(
        text(
            "INSERT INTO supplier_advances(company_id,supplier_id,payment_id,"
            "amount,remaining_amount,created_at,updated_at)"
            " VALUES(:c,:s,:p,:a,:r,:t,:t)"
        ),
        {"c": cid, "s": sid, "p": odeme, "a": tutar, "r": kalan, "t": an},
    )
    oturum.commit()


def test_AVANS_TOPLAMLARI_UCUN_KENDI_SAYILARIYLA_AYNI(oturum, dunya, uygulama):
    """Servis toplamları = ucun döndürdüğü satırların toplamı (keşif §4b).

    Uç ÇAĞRILMIYOR (o bir `Request` ve bir yetki yüklemi ister) ama
    SAYILARI karşılaştırılıyor — kopya bir toplam, iki yüzeyin aynı
    çiftçi için farklı rakam söylediği güne kadar sessiz kalırdı.
    """
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    _avans_yaz(
        oturum, dunya["firma_a"], dunya["tedarikci_a"], "75000", "30000", "2026-09-02"
    )
    _avans_yaz(
        oturum, dunya["firma_a"], dunya["tedarikci_a"], "20000", "0", "2026-08-11"
    )

    r = uygulama.post(
        "/api/auth/login",
        json={"username": dunya["kad"], "password": dunya["parola"]},
    )
    assert r.status_code == 200, r.text
    basliklar = {
        "Authorization": "Bearer " + r.json()["access_token"],
        "X-Company-ID": str(dunya["firma_a"]),
    }
    uc = uygulama.get(
        f"/api/suppliers/{dunya['tedarikci_a']}/advances", headers=basliklar
    )
    assert uc.status_code == 200, uc.text
    satirlar = uc.json()["items"]
    assert len(satirlar) == 2
    uc_alinan = sum(Decimal(str(s["amount"])) for s in satirlar)
    uc_kalan = sum(Decimal(str(s["remaining_amount"])) for s in satirlar)

    veri = ciftci_avans(
        oturum,
        TarafKimlik(
            company_id=dunya["firma_a"],
            party_type="SUPPLIER",
            party_id=dunya["tedarikci_a"],
        ),
        {},
    )
    assert veri["alinan"] == uc_alinan
    assert veri["kalan"] == uc_kalan
    assert veri["adet"] == len(satirlar)


def test_AVANS_TOPLAMLARI_SAYFALARIN_BIRLESIMINDEN(oturum, dunya, monkeypatch):
    """Toplam BİR sayfanın değil BÜTÜN sayfaların toplamıdır (düzeltme 1).

    Sayfa boyu 1'e indiriliyor ki iki avans İKİ sayfaya düşsün.
    MUTASYON: `avans_servis.tedarikci_tum_avanslari`nın ilk sayfadan sonra
    dönmesi (ya da `offset`i ilerletmemesi) bunu KIRMIZI yapar — 200'den
    fazla avansı olan bir çiftçiye eksik rakam söylenirdi.
    """
    from app import avans_servis
    from app.whatsapp.ciftci_yurutucu import ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    _avans_yaz(
        oturum, dunya["firma_a"], dunya["tedarikci_a"], "75000", "30000", "2026-09-02"
    )
    _avans_yaz(
        oturum, dunya["firma_a"], dunya["tedarikci_a"], "20000", "5000", "2026-08-11"
    )
    monkeypatch.setattr(avans_servis, "SAYFA_UST_SINIRI", 1)

    veri = ciftci_avans(
        oturum,
        TarafKimlik(
            company_id=dunya["firma_a"],
            party_type="SUPPLIER",
            party_id=dunya["tedarikci_a"],
        ),
        {},
    )
    assert veri["adet"] == 2
    assert veri["alinan"] == Decimal("95000")
    assert veri["kalan"] == Decimal("35000")
    assert str(veri["son"])[:10] == "2026-09-02"


def test_AVANS_SQL_KOPYASI_YOK_IKI_YUZEY_TEK_FONKSIYONU_OKUYOR():
    """Avans satırlarının SQL'i YALNIZ `app/avans_servis.py`dedir (düzeltme 1).

    AGY kontrat merceği: çiftçi aracı ucun SELECT'inin bir KOPYASINI
    taşıyordu ve iki kopya biri düzeltildiğinde SESSİZCE ayrışırdı.
    Kapı İKİ yönlü: `app/whatsapp/` altında `supplier_advances` geçen
    HİÇBİR dize sabiti yok (belge dizileri hariç — onlar SQL değil), ve
    iki yüzey de ortak fonksiyonu ÇAĞIRIYOR.
    """
    def _dize_sabitleri(yol: Path) -> list[str]:
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        belge = set()
        for dugum in ast.walk(agac):
            if isinstance(
                dugum,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                ilk = dugum.body[0] if dugum.body else None
                if isinstance(ilk, ast.Expr) and isinstance(ilk.value, ast.Constant):
                    belge.add(id(ilk.value))
        return [
            d.value
            for d in ast.walk(agac)
            if isinstance(d, ast.Constant)
            and isinstance(d.value, str)
            and id(d) not in belge
        ]

    def _cagrilan_adlar(yol: Path) -> set[str]:
        agac = ast.parse(yol.read_text(encoding="utf-8"))
        return {
            d.func.id
            for d in ast.walk(agac)
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
        }

    sizan = [
        (yol.name, dize)
        for yol in sorted((BACKEND / "app" / "whatsapp").glob("*.py"))
        for dize in _dize_sabitleri(yol)
        if "supplier_advances" in dize
    ]
    assert sizan == []

    assert "tedarikci_tum_avanslari" in _cagrilan_adlar(
        BACKEND / "app" / "whatsapp" / "ciftci_yurutucu.py"
    )
    assert "tedarikci_avans_satirlari" in _cagrilan_adlar(
        BACKEND / "app" / "routers" / "avans.py"
    )
    # Ucun eski iki liste metni geri gelmesin: `remaining_amount>0`
    # süzgeci YALNIZ ortak serviste yaşar.
    assert not [
        d
        for d in _dize_sabitleri(BACKEND / "app" / "routers" / "avans.py")
        if "remaining_amount>0" in d
    ]


def test_AVANS_CEVABI_PAYMENT_ID_VE_NOT_TASIMIYOR(oturum, dunya):
    """Cevapta `payment_id` ve `note` HİÇ GEÇMEZ (keşif §4b)."""
    _baglanti_ac(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _riza_ver(oturum, dunya["firma_a"], "SUPPLIER", dunya["tedarikci_a"])
    _avans_yaz(
        oturum, dunya["firma_a"], dunya["tedarikci_a"], "75000", "30000", "2026-09-02"
    )

    gonderilen, _ = _konus(oturum, "avans durumu")
    metin = gonderilen[-1][1]
    assert "75.000,00" in metin
    assert "45.000,00" in metin  # mahsup = alınan - kalan
    assert "30.000,00" in metin  # kalan
    assert "02.09.2026" in metin
    assert len(metin.splitlines()) <= 5


def test_MUSTERI_AVANS_SORARSA_TARAF_TIPI_SIZMIYOR(oturum, dunya):
    """`CUSTOMER` tarafı "avans" sorarsa NÖTR cevap; taraf tipi AÇIKLANMAZ.

    Cevap, kaydı gerçekten olmayan bir TEDARİKÇİNİN alacağı cevapla
    BİREBİR AYNI olmak zorunda — "siz müşterisiniz, avansınız olamaz"
    demek dışarıdaki birine defterin şeklini anlatırdı.

    MUTASYON: `ciftci_avans`tan `party_type` denetimini düşürmek bunu
    KIRMIZI yapar (müşteri kimliğiyle `supplier_advances`te arama
    yapılırdı).
    """
    from app.whatsapp.ciftci_yurutucu import cevap_yaz, ciftci_avans
    from app.whatsapp.taraf import TarafKimlik

    _baglanti_ac(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])
    _riza_ver(oturum, dunya["firma_a"], "CUSTOMER", dunya["musteri_a"])

    gonderilen, _ = _konus(oturum, "avans")
    musteri_cevabi = gonderilen[-1][1]
    assert musteri_cevabi == "Bu numara için avans kaydı bulunmuyor."

    # AYNI metin, avansı OLMAYAN bir TEDARİKÇİ için de geliyor.
    bos = ciftci_avans(
        oturum,
        TarafKimlik(
            company_id=dunya["firma_a"],
            party_type="SUPPLIER",
            party_id=dunya["tedarikci_a"],
        ),
        {},
    )
    assert cevap_yaz("ciftci_avans", bos) == musteri_cevabi


# ------------------------------------------------------------- göç turu ---


_GOC_TURU = r"""
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

motor = sa.create_engine(settings.database_url)
yapilandirma = Config('alembic.ini')
yapilandirma.set_main_option('sqlalchemy.url', settings.database_url)

SAYAC = 'whatsapp_message_attempts'


def gorunen():
    return set(sa.inspect(motor).get_table_names())


command.upgrade(yapilandirma, 'head')
assert SAYAC in gorunen(), 'yukari: sayac dogmadi'
gozlemci = sa.inspect(motor)

sutunlar = {c['name'] for c in gozlemci.get_columns(SAYAC)}
assert 'company_id' not in sutunlar, sutunlar
assert {'phone', 'window_start', 'attempt_count', 'updated_at'} <= sutunlar, sutunlar

tekiller = {u['name']: tuple(u['column_names'])
            for u in gozlemci.get_unique_constraints(SAYAC)}
assert tekiller.get('uq_whatsapp_message_attempts_pencere') == (
    'phone', 'window_start'), tekiller

indeksler = {i['name'] for i in gozlemci.get_indexes(SAYAC)}
assert 'ix_whatsapp_message_attempts_pencere' in indeksler, indeksler

command.downgrade(yapilandirma, '20260918_0090')
assert SAYAC not in gorunen(), 'asagi: sayac dusmedi'
# ESLESTIRME sayaci AYAKTA KALIYOR: iki tablo AYRI ve geri alma birininkini
# otekinin uzerinden yapmiyor (K6'nin gerekcesi).
assert 'whatsapp_pairing_attempts' in gorunen(), 'komsu sayac dustu'

command.upgrade(yapilandirma, 'head')
assert SAYAC in gorunen(), 'ikinci yukari: tur kapanmadi'
print('GOC TURU TAMAM')
"""


def test_goc_0091_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Tablo doğuyor, `downgrade` onu GERİ ALIYOR, tur kapanıyor.

    ALT SÜREÇTE koşuyor (`test_54b_idempotency`in deseni): Alembic'in
    `env.py`si bağlantıyı `settings.database_url`den kuruyor, yani
    `sqlalchemy.url`i süreç içinde değiştirmek ÇALIŞAN veritabanını
    DEĞİŞTİRMEZ — bu önce yazıldı, KIRMIZI görüldü, sonra alt sürece
    taşındı.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi adları SABİTTEN
    okuyor ve dizge araması indeksin tabloyla birlikte GERÇEKTEN
    düştüğünü hiç ölçmezdi.
    """
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + (tmp_path / "goc.db").as_posix()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _GOC_TURU],
        cwd=str(BACKEND),
        env=ortam,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "GOC TURU TAMAM" in sonuc.stdout


def test_GERI_YUKLEME_SINIFLANDIRICISI_PLATFORM_TABLOSUNU_ISTEMIYOR() -> None:
    """Platform tablosu kiracı geri yüklemesinin sözlüğüne GİRMEZ.

    Sınıflandırıcı (`kiraci_geri_yukleme`) yalnız dışa aktarımda görünen
    kiracı tablolarını tanır; `company_id` taşımayan bir tablo oraya hiç
    girmediği için bir sınıflandırıcı kaydı ÖLÜ KOD olurdu.
    """
    from app import kiraci_geri_yukleme as geri

    kaynak = Path(geri.__file__).read_text(encoding="utf-8")
    assert SAYAC_TABLO not in kaynak


# ----------------------------------------------------------------- K2 ----


def test_TOHUM_TELEFONLARI_IKI_NORMALLESTIRICIDE_AYNI() -> None:
    """K2: demo tohumundaki HER telefon İKİ normalleştiricide AYNI rakamlar.

    Keşif §2.2 ölçtü: `telefon.normalize_phone` (gevşek) ile
    `consents.normalize_msisdn` (sıkı) demo kümesinin %100'ünde
    AYRIŞIYORDU — yani demo müşterisine/tedarikçisine WhatsApp rızası HİÇ
    yazılamıyordu ve F10-1 uçtan uca DENENEMİYORDU.

    İKİ İDDİA: (1) desenlerin ÜRETTİĞİ değerler iki normalleştiricide
    AYNI; (2) tohum betiği GERÇEKTEN bu desenleri taşıyor. İkincisi
    olmasaydı betik değişip test yeşil kalabilirdi.
    """
    from app.notifications.consents import normalize_msisdn
    from app.whatsapp.telefon import normalize_phone

    musteri_deseni = "5{30 + idx % 20:02d} 55{idx:02d} {100 + idx:03d}"
    tedarikci_deseni = "53{idx % 10} 44{idx:02d} {200 + idx:03d}"

    for idx in range(1, 31):
        musteri = f"+90 5{30 + idx % 20:02d} 55{idx:02d} {100 + idx:03d}"
        tedarikci = f"+90 53{idx % 10} 44{idx:02d} {200 + idx:03d}"
        for numara in (musteri, tedarikci):
            gevsek = normalize_phone(numara)
            siki = normalize_msisdn(numara)
            assert siki is not None, numara
            assert gevsek == siki.lstrip("+"), (numara, gevsek, siki)

    kaynak = (BACKEND / "seed_demo_data.py").read_text(encoding="utf-8")
    assert musteri_deseni in kaynak
    assert tedarikci_deseni in kaynak
    # SABİT HAT ÖNEKİ GERİ GELMESİN: `normalize_msisdn` abonenin `5` ile
    # başlamasını ŞART koşuyor (keşif §2.2).
    assert "+90 212" not in kaynak
