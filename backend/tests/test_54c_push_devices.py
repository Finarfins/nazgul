"""PUSH CİHAZ DEFTERİ: bir kullanıcıya hangi cihazdan ulaşılacağı (5.4c).

Konu: göç `20260909_0077`, `app/push_devices.py`, `app/routers/push.py`,
`app/notifications/provider.py` (`PushNotificationProvider`),
`app/notifications/service.py` (`enqueue_push_notification`) ve
`app/routers/auth.py` (`logout-all` süpürgesi).

ÖLÇÜLEN EKSİK: 5.4a mobil oturumu getirdi (cihaz başına refresh ailesi) ama
bildirim kanalı olarak depoda YALNIZ üç değer yazılıyordu — `SMS`,
`WHATSAPP`, `EMAIL`. Kullanıcının CİHAZINA giden yol YOKTU: bir cihaz
jetonunu saklayan tablo hiç yoktu.

Şekil, deponun mevcut kalıbı ve `tests/test_54b_idempotency.py` ile BİREBİR:
STATİK KAPILAR + alt süreçte GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * Göçün `(company_id, token)` tekilini düşürmek (upsert KOPYA üretir)
                                       -> UPSERT adımı ve göç kapısı KIRMIZI
  * `kaydet`teki `UPDATE`i atlayıp doğrudan `INSERT` yazmak
                                       -> UPSERT adımı KIRMIZI
  * Tekilden `company_id`yi düşürmek (kiraci kapsamı YOK)
                                       -> ÇAPRAZ FİRMA adımı KIRMIZI
  * `delete_push_device`ten sahiplik yüklemini düşürmek
                                       -> SAHİPLİK 403 adımı KIRMIZI
  * `logout_all`dan `kullanicinin_cihazlarini_dusur` çağrısını kaldırmak
                                       -> LOGOUT-ALL adımı KIRMIZI
  * `_ETKIN_HEDEFLER`den `is_active` süzgecini düşürmek
                                       -> PASİF CİHAZ adımı KIRMIZI
  * `KANAL_SAGLAYICILARI`ndan `PUSH`u silmek (kanal ayardan seçilir)
                                       -> KANAL ÇİVİSİ kapısı ve SIMULATED
                                          adımı KIRMIZI
  * `PUSH`u `CONSENT_REQUIRED_CHANNELS`e eklemek
                                       -> RIZA MUAFİYETİ kapısı KIRMIZI ve
                                          davranışta her push REJECTED olur
  * `required_permission`daki `/api/push/` kuralını silmek
                                       -> YETKİ kapısı KIRMIZI (POST ve DELETE
                                          `__admin_only__`a düşerdi)
  * `enqueue_push_notification`da `dedupe_key`i cihazla genişletmemek
                                       -> FAN-OUT adımı KIRMIZI (üç cihazdan
                                          yalnız biri satır alırdı)
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260909_0077_push_devices.py"
MODUL = BACKEND / "app" / "push_devices.py"
UC = BACKEND / "app" / "routers" / "push.py"
KIMLIK = BACKEND / "app" / "auth.py"
OTURUM = BACKEND / "app" / "routers" / "auth.py"


# --------------------------------------------------------------- statik ---

def test_goc_DEFTERI_aciyor_ve_KAPSAM_FIRMA_ARTI_JETON() -> None:
    """Tekillik `(company_id, token)`; ikisi de düşürülemez.

    MUTASYON: tekili yalnız `(token)`a indirmek bunu KIRMIZI yapar — ve
    davranışta ÇAPRAZ FİRMA adımını düşürür: aynı telefonu iki firmada
    kullanan bir kişinin ikinci kaydı BİRİNCİYİ ezerdi. Tekili tamamen
    kaldırmak ise UPSERT adımını düşürür: aynı jeton İKİ satır olurdu ve
    bildirim aynı cihaza İKİ KEZ giderdi.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert "push_devices" in kaynak
    assert 'sa.UniqueConstraint("company_id", "token"' in kaynak
    # 0062'nin kuralı: bileşik yabancı anahtar HEDEFİ olabilmesi için.
    assert 'sa.UniqueConstraint("company_id", "id"' in kaynak
    assert "ix_push_devices_company_user" in kaynak
    # `user_id` yabancı anahtarı ÇIPLAK ve bu bir istisna değil: `app_users`ta
    # `company_id` sütunu YOKTUR (0076 ile AYNI gerekçe).
    assert 'sa.ForeignKeyConstraint(["user_id"], ["app_users.id"])' in kaynak
    assert 'sa.ForeignKeyConstraint(["company_id"], ["companies.id"])' in kaynak


def test_platform_kumesi_KAPALI_ve_CHECK_ile_civili() -> None:
    """Üç değer var ve veritabanı seviyesinde ısırıyor.

    MUTASYON: CHECK'i kaldırmak bunu KIRMIZI yapar. Küme açık olsaydı
    tanınmayan bir platform satırı HİÇBİR adaptörün almadığı sessiz bir ölü
    satır olurdu.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'PLATFORMLAR = ("android", "ios", "web")' in kaynak
    assert "ck_push_devices_platform" in kaynak
    # Uç, veritabanına gitmeden ÖNCE reddedebilmeli: CHECK'e bırakılsaydı
    # istemci 422 yerine 500 alırdı.
    assert 'Literal["android", "ios", "web"]' in UC.read_text(encoding="utf-8")


def test_session_family_id_YABANCI_ANAHTAR_DEGIL_ve_gerekce_YAZILI() -> None:
    """Aile bir TABLO değil; FK KURULAMAZ — iddia değil, ölçüm.

    `app/auth.py`de aile `auth_refresh_tokens.family_id` SÜTUNUDUR ve o sütun
    TEKİL DEĞİLDİR (rotasyon aynı aileye yeni satırlar yazar). Yabancı
    anahtarın hedefi tekil olmak zorunda olduğu için bağ kurulamaz.

    MUTASYON: `family_id`yi tekil yapan bir kısıt eklemek bunu KIRMIZI yapar
    ve o hâlde rotasyon KIRILIRDI — kapı, gerekçenin dayandığı OLGUYU ölçüyor.
    """
    kimlik = KIMLIK.read_text(encoding="utf-8")
    assert 'Column("family_id", String(64), nullable=False, index=True)' in kimlik
    assert 'Column("family_id", String(64), unique=True' not in kimlik

    goc = GOC.read_text(encoding="utf-8")
    assert 'sa.Column("session_family_id", sa.String(length=64), nullable=True)' in goc
    assert '["session_family_id"]' not in goc


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """`push_devices` açılış DDL'inde BİLDİRİLMİYOR.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu; 0074/0075/0076'da aynı ayrım
    yapıldı): `app/tenancy.py` `companies`i `Table()` olarak bildiriyor ve
    uygulamanın AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor; göç onu VAR
    bulup tek `if` dalını ATLAR ve YEŞİL biter.
    """
    import re

    acilis = ""
    for modul in ("tenancy.py", "core_schema.py", "auth.py", "inventory.py",
                  "finance_engine.py", "workflow.py"):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    acilis += (BACKEND / "app" / "notifications" / "schema.py").read_text(
        encoding="utf-8"
    )
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))

    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    assert "push_devices" not in bildirilen, (
        "push_devices açılış DDL'ine girmiş; göç 0077 onu VAR bulup atlar "
        "(companies'te ölçülen kusurun aynısı)"
    )


def test_defter_KIRACI_TABLOLARI_listesinde() -> None:
    """`company_id` taşıyan her tablo kiracı nöbetçisinin evreninde OLMALI.

    Girmeseydi dışa aktarma o tablonun satırlarını kiracıya VERMEZDİ ve ham
    SQL nöbetçisi tabloyu "kiracıya ait değil" sayıp yüklemsiz sorguyu
    geçirirdi.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert "push_devices" in TENANT_TABLES


def test_her_SQL_KIRACI_yuklemi_tasiyor_ve_SABIT_METIN() -> None:
    """Sekiz metnin sekizi de `company_id=:cid` ile daralıyor.

    MUTASYON: herhangi birinden `company_id=:cid`i düşürmek bunu KIRMIZI
    yapar — ve davranışta ÇAPRAZ FİRMA adımını düşürür. Kiracı yüklemi düşen
    bir yazma, BAŞKA firmanın cihazını pasifleştirebilirdi.
    """
    agac = ast.parse(MODUL.read_text(encoding="utf-8"))
    metinler = [
        d.args[0].value
        for d in ast.walk(agac)
        if isinstance(d, ast.Call)
        and getattr(d.func, "id", None) == "text"
        and d.args
        and isinstance(d.args[0], ast.Constant)
    ]
    assert len(metinler) == 8, metinler
    for sql in metinler:
        if sql.lstrip().upper().startswith("INSERT"):
            # YAZIM YOLU: kiracı sütun listesinde AÇIKÇA duruyor ve değeri
            # BAĞLI PARAMETRE. INSERT'ün WHERE'i yoktur.
            assert "push_devices(company_id,user_id," in sql, sql
            assert "VALUES(:cid,:uid," in sql, sql
        else:
            assert "company_id=:cid" in sql, sql
        # SABİT METİN: f-string ya da birleştirilmiş değişken YOK. Kiracı
        # nöbetçisinin dinamik listesine girmemesinin sebebi budur.
        assert "%" not in sql and "{" not in sql, sql


def test_PASIF_CIHAZ_SUZGECI_SQLDE_degil_PYTHONDA_DEGIL() -> None:
    """`is_active` süzgeci gönderim sorgusunun İÇİNDE.

    MUTASYON: `_ETKIN_HEDEFLER`den `is_active=:etkin`i düşürmek bunu KIRMIZI
    yapar — ve davranışta PASİF CİHAZ adımını düşürür: satırlar yine üretilir,
    sağlayıcı onları teslim edemez ve satır yeniden denemeye takılırdı.
    """
    kaynak = MODUL.read_text(encoding="utf-8")
    hedef = kaynak.split("_ETKIN_HEDEFLER = text(")[1].split(")")[0]
    assert "company_id=:cid" in hedef, hedef
    assert "user_id=:uid" in hedef, hedef
    assert "is_active=:etkin" in hedef, hedef


def test_YETKI_KURALI_YAZILI_ve_uc_metot_AYNI_kapidan() -> None:
    """`/api/push/` öneki `read`e bağlı; üç uç da AYNI kapıdan geçiyor.

    MUTASYON: kuralı silmek bunu KIRMIZI yapar — ve o hâlde POST ile DELETE
    dosyanın SONUNDAKİ deny-by-default nöbetçisine düşer, yani `admin`
    dışında hiç kimse kendi telefonunu kaydedemezdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS, required_permission

    assert required_permission("POST", "/api/push/devices") == "read"
    assert required_permission("GET", "/api/push/devices") == "read"
    assert required_permission("DELETE", "/api/push/devices/7") == "read"

    # SINIRIN DAYANDIĞI OLGU ÖLÇÜLÜYOR: bugün `read` taşımayan rol YOK.
    # Bir gün olursa bu kapı kırmızı olur ve seçim YENİDEN yapılır — sessizce
    # push'suz kalan bir rol doğmaz.
    yoksun = sorted(
        rol for rol, izinler in ROLE_PERMISSIONS.items()
        if "*" not in izinler and "read" not in izinler
    )
    assert yoksun == [], yoksun


def test_PUSH_kanali_RIZA_KAPISINDAN_MUAF() -> None:
    """`PUSH` `CONSENT_REQUIRED_CHANNELS`te YOK — bilerek.

    MUTASYON: kanalı listeye eklemek bunu KIRMIZI yapar; davranışta her push
    satırı taraf araması yapar, taraf bulamaz ve `TARGET_MISSING` ile terminal
    `REJECTED` olurdu — yani kanal DOĞDUĞU ANDA ölü olurdu.
    """
    sys.path.insert(0, str(BACKEND))
    from app.notifications.rules import CHANNELS as KURAL_KANALLARI
    from app.notifications.schema import CONSENT_REQUIRED_CHANNELS, PUSH
    from app.notifications.templates import CHANNELS as SABLON_KANALLARI

    assert PUSH == "PUSH"
    assert PUSH not in CONSENT_REQUIRED_CHANNELS, CONSENT_REQUIRED_CHANNELS
    # Şablon TANIMLANABİLİR olmalı...
    assert PUSH in SABLON_KANALLARI, SABLON_KANALLARI
    # ...ama KURAL MOTORU alıcıyı MÜŞTERİ kaydından çözer ve müşterinin cihaz
    # jetonu YOKTUR. Eklenseydi kural motoru alıcısı boş satırlar üretirdi.
    assert PUSH not in KURAL_KANALLARI, KURAL_KANALLARI


def test_SAGLAYICI_KANALA_CIVILI_ayara_DEGIL() -> None:
    """PUSH satırı, ayar ne olursa olsun push sağlayıcısına gider.

    MUTASYON: `KANAL_SAGLAYICILARI`ndan `PUSH`u silmek bunu KIRMIZI yapar —
    ve o hâlde `notification_provider="smtp"` ayarlı bir kurulumda push satırı
    SMTP adaptörüne giderdi; o adaptör alıcı alanındaki CİHAZ JETONUNU bir
    e-posta adresi sanıp ona mail atmaya çalışırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.notifications.provider import (
        PushNotificationProvider,
        SmtpEmailNotificationProvider,
        get_notification_provider,
    )

    class _Ayar:
        notification_provider = "smtp"
        smtp_host = "ornek.test"
        smtp_from_email = "a@ornek.test"

    ayar = _Ayar()
    assert isinstance(
        get_notification_provider(ayar, channel="PUSH"), PushNotificationProvider
    )
    # Küçük harf de aynı kapıdan geçer: kanal normalize ediliyor.
    assert isinstance(
        get_notification_provider(ayar, channel="push"), PushNotificationProvider
    )
    # KANALSIZ ÇAĞRI 5.4c ÖNCESİYLE BİREBİR AYNI — mevcut çağıranlar
    # kımıldamadı.
    assert isinstance(
        get_notification_provider(ayar), SmtpEmailNotificationProvider
    )
    assert isinstance(
        get_notification_provider(ayar, channel="EMAIL"),
        SmtpEmailNotificationProvider,
    )


def test_push_saglayicisi_SENT_YAZMIYOR() -> None:
    """Gerçek teslimat ÖLÇÜLMEDİ; `SIMULATED` bunu SÖYLÜYOR.

    MUTASYON: sağlayıcıyı `SENT` döndürecek şekilde değiştirmek bunu KIRMIZI
    yapar. Gerçekten gönderilmemiş bir mesajı "gönderildi" diye raporlamak
    denetim izini yalan söyler hâle getirir — `SimulationNotificationProvider`
    için yazılı olan kuralın AYNISI.
    """
    sys.path.insert(0, str(BACKEND))
    from app.notifications.provider import PushNotificationProvider
    from app.notifications.schema import TERMINAL_STATUSES

    sonuc = PushNotificationProvider(None).send(
        {"recipient": "jeton", "provider_idempotency_key": "notification:1:2"}
    )
    assert sonuc.status == "SIMULATED", sonuc
    assert sonuc.status in TERMINAL_STATUSES, sonuc
    assert sonuc.external_id == "push-notification-1-2", sonuc

    # HEDEFSİZ SATIR İÇERİK HATASIDIR, AĞ HATASI DEĞİL: yeniden denenerek
    # düzelmez.
    try:
        PushNotificationProvider(None).send({"recipient": ""})
    except ValueError:
        pass
    else:
        raise AssertionError("jetonsuz push ValueError vermedi")


def test_logout_all_CIHAZLARI_da_dusuruyor() -> None:
    """Süpürge `logout_all` içinde ve TEK commit'in ÖNÜNDE.

    MUTASYON: çağrıyı kaldırmak bunu KIRMIZI yapar — ve davranışta LOGOUT-ALL
    adımını düşürür: çalınmış telefon bildirim almaya DEVAM ederdi.
    """
    kaynak = OTURUM.read_text(encoding="utf-8")
    govde = kaynak.split("def logout_all(")[1].split("\n@router")[0]
    sira_refresh = govde.index("revoke_user_refresh_tokens(db, user_id)")
    sira_cihaz = govde.index("kullanicinin_cihazlarini_dusur(")
    sira_commit = govde.index("db.commit()")
    assert sira_refresh < sira_cihaz < sira_commit, (
        sira_refresh, sira_cihaz, sira_commit
    )
    # FİRMA FİRMA: kiracı yüklemi her yazımda AÇIK duruyor.
    assert "company_ids=[int(f[\"id\"]) for f in user_companies(db, user_id)]" in govde


def test_kayit_ucu_GENEL_IDEMPOTENSI_DEFTERINDEN_MUAF_DEGIL() -> None:
    """`POST /api/push/devices` 5.4b'nin ara katmanına GİRİYOR.

    MUTASYON: ucu `ATLANAN_UCLAR`a eklemek bunu KIRMIZI yapar — ve o hâlde uç
    kendi defterini tutmadığı için `Idempotency-Key` SESSİZCE hiçbir şey
    yapmaz hâle gelirdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.idempotency import kendi_defterini_tutuyor

    assert not kendi_defterini_tutuyor("POST", "/api/push/devices")

    # 5.4b'NİN ÖLÇÜM YÖNTEMİ BU DOSYAYA DA UYGULANIYOR: o kapı
    # (`test_54b_idempotency.py`) `app/routers/` altında başlığın ADINI
    # arayarak "kendi defterini tutan uçlar" kümesini kuruyor. `push.py` o
    # kümeye GİRMEMELİ — girseydi ara katman ucu ATLAR ve `Idempotency-Key`
    # sessizce hiçbir şey yapmazdı. ÖLÇÜLDÜ, VARSAYILMADI.
    baslik_adi = "idempotency" + "-key"
    assert baslik_adi not in UC.read_text(encoding="utf-8").lower()


# ------------------------------------------------------- göç turu (SQLite) ---

_GOC_TURU = r'''
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

motor = sa.create_engine(settings.database_url)
yapilandirma = Config('alembic.ini')
yapilandirma.set_main_option('sqlalchemy.url', settings.database_url)

DEFTER = 'push_devices'


def gorunen():
    return set(sa.inspect(motor).get_table_names())


command.upgrade(yapilandirma, 'head')
assert DEFTER in gorunen(), 'yukari: defter dogmadi'
gozlemci = sa.inspect(motor)
indeksler = {i['name'] for i in gozlemci.get_indexes(DEFTER)}
assert 'ix_push_devices_company_user' in indeksler, indeksler
tekiller = {u['name']: tuple(u['column_names'])
            for u in gozlemci.get_unique_constraints(DEFTER)}
assert tekiller.get('uq_push_devices_company_token') == (
    'company_id', 'token'), tekiller
assert tekiller.get('uq_push_devices_company_id') == (
    'company_id', 'id'), tekiller
sutunlar = {c['name']: c for c in gozlemci.get_columns(DEFTER)}
assert sutunlar['session_family_id']['nullable'] is True
assert sutunlar['company_id']['nullable'] is False
assert sutunlar['user_id']['nullable'] is False
assert sutunlar['token']['nullable'] is False
assert sutunlar['platform']['nullable'] is False
assert sutunlar['is_active']['nullable'] is False
assert sutunlar['last_seen_at']['nullable'] is False

command.downgrade(yapilandirma, '20260909_0076')
assert DEFTER not in gorunen(), 'asagi: defter dusmedi'

command.upgrade(yapilandirma, 'head')
assert DEFTER in gorunen(), 'ikinci yukari: tur kapanmadi'
print('GOC TURU TAMAM')
'''


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Defter doğuyor, `downgrade` onu GERİ ALIYOR, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo ve indeks adlarını
    SABİTTEN okuyor ve dizge araması onu göremezdi — daha kötüsü, indeksin
    tabloyla birlikte gerçekten düştüğünü hiç ölçmezdi.
    """
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + (tmp_path / "goc.db").as_posix()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _GOC_TURU],
        cwd=str(BACKEND), env=ortam, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "GOC TURU TAMAM" in sonuc.stdout


# ------------------------------------------------------------- davranis ---

_SMOKE = r'''
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

import app.push_devices as cihaz
from app.auth import hash_password
from app.db import SessionLocal
from app.main import app
from app.notifications.service import enqueue_push_notification, send_notification

ADMIN_PW = 'Push54c!123'
IKINCI_PW = 'Push54cIki!123'
client = TestClient(app)


def admin_headers():
    for aday in ('admin123', ADMIN_PW):
        r = client.post('/api/auth/login',
                        json={'username': 'admin', 'password': aday})
        if r.status_code == 200:
            break
    assert r.status_code == 200, r.text
    b = r.json()
    h = {'Authorization': 'Bearer ' + b['access_token'],
         'X-Company-ID': str(b['companies'][0]['id'])}
    if aday != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password': aday,
                               'new_password': ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer ' + ch.json()['access_token']
    return h


def defter(cid):
    with SessionLocal() as db:
        return db.execute(text(
            'SELECT id,company_id,user_id,platform,token,session_family_id,'
            'is_active,last_seen_at FROM push_devices WHERE company_id=:c'
            ' ORDER BY id'), {'c': cid}).mappings().all()


h = admin_headers()
cid = int(h['X-Company-ID'])
with SessionLocal() as db:
    admin_uid = db.execute(text(
        "SELECT id FROM app_users WHERE username='admin'")).scalar_one()

# --- 1. KAYIT ------------------------------------------------------------
JETON = 'jeton-bir'
ilk = client.post('/api/push/devices', headers=h,
                  json={'platform': 'android', 'token': JETON,
                        'session_family_id': 'aile-bir'})
assert ilk.status_code == 201, ilk.text
kayit = ilk.json()
assert kayit['platform'] == 'android', kayit
assert kayit['is_active'] is True, kayit
assert kayit['session_family_id'] == 'aile-bir', kayit
assert len(defter(cid)) == 1, defter(cid)

# --- 2. UPSERT: AYNI JETON IKINCI KEZ -> TEK SATIR, last_seen ILERLEDI ----
# MUTASYON: gocun (company_id, token) tekilini dusurmek ya da `kaydet`teki
# UPDATE'i atlayip dogrudan INSERT yazmak bu adimi KIRMIZI yapar.
GERI = datetime.now(timezone.utc) - timedelta(days=1)
with SessionLocal() as db:
    db.execute(text('UPDATE push_devices SET last_seen_at=:t'
                    ' WHERE company_id=:c AND token=:k'),
               {'t': GERI, 'c': cid, 'k': JETON})
    db.commit()
eski_gorulme = defter(cid)[0]['last_seen_at']

ikinci = client.post('/api/push/devices', headers=h,
                     json={'platform': 'ios', 'token': JETON})
assert ikinci.status_code == 201, ikinci.text
satirlar = defter(cid)
assert len(satirlar) == 1, satirlar
assert satirlar[0]['id'] == kayit['id'], (satirlar, kayit)
assert satirlar[0]['platform'] == 'ios', satirlar
assert str(satirlar[0]['last_seen_at']) > str(eski_gorulme), (
    satirlar[0]['last_seen_at'], eski_gorulme)
# Aile VERILMEDI -> NULL'a dusuyor: gonderilen govde satirin TAMAMIDIR.
assert satirlar[0]['session_family_id'] is None, satirlar

# --- 3. CAPRAZ FIRMA: AYNI JETON, AYRI SATIR -----------------------------
# MUTASYON: tekilden `company_id`yi dusurmek bu adimi KIRMIZI yapar.
with SessionLocal() as db:
    komsu_cid = db.execute(text(
        "INSERT INTO companies (name, is_active, created_at)"
        " VALUES ('Push Komsu A.S.', 1, :t) RETURNING id"),
        {'t': datetime.now(timezone.utc)}).scalar_one()
    db.execute(text(
        'INSERT INTO user_company_memberships(user_id,company_id,is_default,'
        'created_at) VALUES(:u,:c,0,:t)'),
        {'u': admin_uid, 'c': komsu_cid, 't': datetime.now(timezone.utc)})
    db.commit()
hk = dict(h); hk['X-Company-ID'] = str(komsu_cid)
komsu = client.post('/api/push/devices', headers=hk,
                    json={'platform': 'web', 'token': JETON})
assert komsu.status_code == 201, komsu.text
assert len(defter(cid)) == 1, defter(cid)
assert len(defter(komsu_cid)) == 1, defter(komsu_cid)
assert komsu.json()['id'] != kayit['id'], (komsu.json(), kayit)

# --- 4. LISTE YALNIZ CAGIRANIN CIHAZLARI ---------------------------------
with SessionLocal() as db:
    ikinci_uid = db.execute(text(
        'INSERT INTO app_users(username,email,display_name,password_hash,'
        'role,is_active,must_change_password,email_verified,created_at)'
        ' VALUES(:k,:e,:d,:p,:r,:a,:m,:v,:t) RETURNING id'),
        {'k': 'push2', 'e': 'push2@ornek.test', 'd': 'Push Iki',
         'p': hash_password(IKINCI_PW), 'r': 'depo', 'a': True, 'm': False,
         'v': True, 't': datetime.now(timezone.utc)}).scalar_one()
    db.execute(text(
        'INSERT INTO user_company_memberships(user_id,company_id,is_default,'
        'created_at) VALUES(:u,:c,1,:t)'),
        {'u': ikinci_uid, 'c': cid, 't': datetime.now(timezone.utc)})
    db.commit()

g2 = client.post('/api/auth/login',
                 json={'username': 'push2', 'password': IKINCI_PW})
assert g2.status_code == 200, g2.text
h2 = {'Authorization': 'Bearer ' + g2.json()['access_token'],
      'X-Company-ID': str(cid)}
ikinci_kayit = client.post('/api/push/devices', headers=h2,
                           json={'platform': 'android', 'token': 'jeton-iki'})
assert ikinci_kayit.status_code == 201, ikinci_kayit.text

benim = client.get('/api/push/devices', headers=h)
assert benim.status_code == 200, benim.text
assert [d['token'] for d in benim.json()] == [JETON], benim.json()
onun = client.get('/api/push/devices', headers=h2)
assert [d['token'] for d in onun.json()] == ['jeton-iki'], onun.json()

# --- 5. SAHIPLIK: BASKASININ CIHAZI -> 403 -------------------------------
# MUTASYON: `delete_push_device`ten sahiplik yuklemini dusurmek bu adimi
# KIRMIZI yapar.
yasak = client.delete('/api/push/devices/%d' % kayit['id'], headers=h2)
assert yasak.status_code == 403, (yasak.status_code, yasak.text)
assert defter(cid)[0]['is_active'] in (1, True), defter(cid)
# BASKA FIRMANIN cihazi ise 404: satir kiraci yuklemiyle HIC okunamiyor.
yok = client.delete('/api/push/devices/%d' % komsu.json()['id'], headers=h)
assert yok.status_code == 404, (yok.status_code, yok.text)
# SAHIBI dusurebiliyor ve SATIR SILINMIYOR, pasiflesiyor.
oncesi = len(defter(cid))
sil = client.delete('/api/push/devices/%d' % ikinci_kayit.json()['id'],
                    headers=h2)
assert sil.status_code == 204, sil.text
assert len(defter(cid)) == oncesi, defter(cid)
pasif = [d for d in defter(cid) if d['token'] == 'jeton-iki'][0]
assert pasif['is_active'] in (0, False), pasif
# ADMIN de baskasinin cihazini dusurebiliyor (rol istisnasi).
admin_sil = client.delete('/api/push/devices/%d' % ikinci_kayit.json()['id'],
                          headers=h)
assert admin_sil.status_code == 204, admin_sil.text

# --- 6. YENIDEN KAYIT CIHAZI CANLANDIRIYOR ------------------------------
canlan = client.post('/api/push/devices', headers=h2,
                     json={'platform': 'android', 'token': 'jeton-iki'})
assert canlan.status_code == 201, canlan.text
assert canlan.json()['id'] == ikinci_kayit.json()['id'], (canlan.json(),
                                                          ikinci_kayit.json())
assert canlan.json()['is_active'] is True, canlan.json()

# --- 7. IDEMPOTENCY-KEY KAYIT UCUNDA TEKRAR OYNATIYOR --------------------
# MUTASYON: ucu `ATLANAN_UCLAR`a eklemek bu adimi KIRMIZI yapar.
BAS = {'Idempotency-Key': 'push-anahtari'}
GOVDE = {'platform': 'web', 'token': 'jeton-idem'}
i1 = client.post('/api/push/devices', json=GOVDE, headers={**h, **BAS})
assert i1.status_code == 201, i1.text
assert 'Idempotent-Replayed' not in i1.headers, dict(i1.headers)
i2 = client.post('/api/push/devices', json=GOVDE, headers={**h, **BAS})
assert i2.status_code == 201, i2.text
assert i2.headers.get('Idempotent-Replayed') == 'true', dict(i2.headers)
assert i2.text == i1.text, (i1.text, i2.text)

# --- 8. PUSH KANALI: ETKIN CIHAZ BASINA BIR SATIR -----------------------
# MUTASYON: `dedupe_key`i cihazla genisletmemek bu adimi KIRMIZI yapar
# (uc cihazdan yalniz biri satir alirdi).
with SessionLocal() as db:
    kimlikler = enqueue_push_notification(
        db, company_id=cid, user_id=admin_uid, type_='PUSH_TEST',
        template='push.test', payload_dict={'body': 'Merhaba'},
        dedupe_key='push-test-1')
    db.commit()
# Admin'in bu firmada IKI etkin cihazi var: JETON ve 'jeton-idem'.
assert len(kimlikler) == 2, kimlikler
with SessionLocal() as db:
    satir = db.execute(text(
        'SELECT id,channel,recipient,status FROM notifications'
        ' WHERE company_id=:c AND type=:t ORDER BY id'),
        {'c': cid, 't': 'PUSH_TEST'}).mappings().all()
assert [s['channel'] for s in satir] == ['PUSH', 'PUSH'], satir
assert sorted(s['recipient'] for s in satir) == sorted([JETON, 'jeton-idem']), satir
# SILAHLI DOGUYOR: dort-goz onayi beklemiyor.
assert all(s['status'] == 'PENDING' for s in satir), satir

# --- 9. PASIF CIHAZ KUYRUGA HIC GIRMIYOR --------------------------------
# MUTASYON: `_ETKIN_HEDEFLER`den `is_active` suzgecini dusurmek bu adimi
# KIRMIZI yapar.
with SessionLocal() as db:
    db.execute(text('UPDATE push_devices SET is_active=:e'
                    ' WHERE company_id=:c AND token=:k'),
               {'e': False, 'c': cid, 'k': 'jeton-idem'})
    db.commit()
    tekrar = enqueue_push_notification(
        db, company_id=cid, user_id=admin_uid, type_='PUSH_TEST2',
        template='push.test', payload_dict={'body': 'Ikinci'},
        dedupe_key='push-test-2')
    db.commit()
assert len(tekrar) == 1, tekrar
with SessionLocal() as db:
    alici = db.execute(text(
        'SELECT recipient FROM notifications WHERE company_id=:c AND type=:t'),
        {'c': cid, 't': 'PUSH_TEST2'}).scalars().all()
assert alici == [JETON], alici

# --- 10. GONDERIM: KANALA CIVILI SAGLAYICI, TERMINAL SIMULATED ----------
# MUTASYON: `KANAL_SAGLAYICILARI`ndan PUSH'u silmek bu adimi KIRMIZI yapar.
with SessionLocal() as db:
    sonuc = send_notification(db, company_id=cid, notification_id=kimlikler[0])
assert sonuc.status == 'SIMULATED', sonuc
with SessionLocal() as db:
    durum = db.execute(text(
        'SELECT status,external_id FROM notifications'
        ' WHERE company_id=:c AND id=:i'),
        {'c': cid, 'i': kimlikler[0]}).mappings().one()
assert durum['status'] == 'SIMULATED', durum
assert str(durum['external_id']).startswith('push-notification-'), durum

# --- 11. LOGOUT-ALL BUTUN CIHAZLARI DUSURUYOR ---------------------------
# MUTASYON: `logout_all`dan supurgeyi kaldirmak bu adimi KIRMIZI yapar.
etkin_once = [d['token'] for d in defter(cid) if d['is_active'] in (1, True)]
assert JETON in etkin_once, etkin_once
assert [d for d in defter(komsu_cid) if d['is_active'] in (1, True)]
cikis = client.post('/api/auth/logout-all', headers=h)
assert cikis.status_code == 204, cikis.text
assert [d['token'] for d in defter(cid)
        if d['user_id'] == admin_uid and d['is_active'] in (1, True)] == [], defter(cid)
# KOMSU FIRMADAKI cihaz da dustu: supurge kullanicinin UYE OLDUGU HER
# firmada kosuyor. MUTASYON: supurgeyi tek firmaya (istegin kiracisina)
# indirmek bu satiri KIRMIZI yapar.
assert [d for d in defter(komsu_cid) if d['is_active'] in (1, True)] == [], defter(komsu_cid)
# BASKA KULLANICININ cihazi DOKUNULMADI: supurgenin yuklemi `user_id`
# tasiyor. MUTASYON: yuklemi dusurmek bu satiri KIRMIZI yapar.
baskasi = [d for d in defter(cid) if d['user_id'] != admin_uid]
assert baskasi and all(d['is_active'] in (1, True) for d in baskasi), baskasi
h2yeni = client.post('/api/auth/login',
                     json={'username': 'push2', 'password': IKINCI_PW})
assert h2yeni.status_code == 200, h2yeni.text

client.close()
print('5.4C DAVRANIS TAMAM')
'''


def test_davranis_smoke_GERCEK_SEMADA(tmp_path: Path) -> None:
    """On bir adım, hepsi GERÇEK şema üzerinde.

    ALT SÜREÇ ZORUNLU: smoke kendi `DATABASE_URL`iyle TAZE bir şema kurar ve
    göç 0077'yi o turda sürer; `app.config.Settings` modül düzeyinde TEK
    KOPYADIR (1B-A/E2/E3/5.4b ikizlerinin AYNI gerekçesi), yani süreç İÇİNDE
    değiştirilemez.
    """
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + (tmp_path / "smoke.db").as_posix()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=str(BACKEND), env=ortam, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "5.4C DAVRANIS TAMAM" in sonuc.stdout
