"""GENEL İDEMPOTENSİ: `Idempotency-Key` başlığının TEK yorumu.

Konu: göç `20260909_0076`, `app/idempotency.py`, `app/main.py`
(`security_and_audit` ara katmanı).

ÖLÇÜLEN EKSİK: depoda BEŞ ayrı `*_idempotency` tablosu vardı ve BEŞİ DE FARKLI
şekildeydi; HTTP katmanında ise HİÇBİR ŞEY yoktu — ölçüldü, `app/main.py`de
`Idempotency-Key` literali SIFIR kez geçiyordu. 198 yazan uçtan yalnız 11'i
korunuyordu.

Şekil, deponun mevcut kalıbı ve `tests/test_e3_karantina.py` ile BİREBİR:
STATİK KAPILAR + alt süreçte GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `iddia_et`te `request_hash` karşılaştırmasını atlamak (özet YOK SAYILIR)
                                       -> AYNI ANAHTAR BAŞKA GÖVDE adımı KIRMIZI
  * Tekillikten ve sorgulardan `user_id`yi düşürmek (kullanıcı kapsamı YOK)
                                       -> İKİ KULLANICI adımı KIRMIZI
  * Tekillikten ve sorgulardan `company_id`yi düşürmek (kiracı kapsamı YOK)
                                       -> İKİ FİRMA adımı KIRMIZI
  * `tamamla`da 5xx dalını kaldırıp cevabı SAKLAMAK
                                       -> 5xx SAKLANMAZ adımı KIRMIZI
  * `ATLANAN_UCLAR`ı boşaltmak (kendi defterini tutan uçları da kapsamak)
                                       -> ATLANAN UÇ adımı KIRMIZI
  * `YAZAN_METOTLAR`a `GET` eklemek    -> GET DEFTERE GİRMİYOR adımı KIRMIZI
  * İddiayı `resolve_company`den ÖNCEYE almak
                                       -> SIRA kapısı KIRMIZI
  * `expires_at` süzgecini `iddia_et`ten düşürmek
                                       -> SÜRESİ DOLAN adımı KIRMIZI
  * Göçün üç sütunlu tekilini iki sütuna indirmek
                                       -> göç kapısı ve İKİ KULLANICI KIRMIZI
  * `govde_tamponlanabilir` çağrısını kaldırmak (gövde sınırsız tamponlanır)
                                       -> BÜYÜK GÖVDE adımı KIRMIZI
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260909_0076_idempotency_keys.py"
MODUL = BACKEND / "app" / "idempotency.py"
ANA = BACKEND / "app" / "main.py"


# --------------------------------------------------------------- statik ---

def test_goc_DEFTERI_aciyor_ve_KAPSAM_UC_SUTUNLU() -> None:
    """Tekillik `(company_id, user_id, key)`; ikisi de düşürülemez.

    MUTASYON: tekili `(company_id, key)`ye indirmek bunu KIRMIZI yapar — ve
    davranışta İKİ KULLANICI adımını da düşürür. Anahtarı istemci KENDİ
    yerelinde üretir; kullanıcı kapsamı düşerse aynı firmadaki iki kullanıcının
    çakışan anahtarı birbirinin isteğini yutardı.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert "idempotency_keys" in kaynak
    assert 'sa.UniqueConstraint("company_id", "user_id", "key"' in kaynak
    # 0062'nin kuralı: bileşik yabancı anahtar HEDEFİ olabilmesi için.
    assert 'sa.UniqueConstraint("company_id", "id"' in kaynak
    # Süpürgenin tarama yolu bugünden açılıyor — gerekçe göçün başlığında.
    assert "ix_idempotency_keys_expires_at" in kaynak
    # Cevabın KENDİSİ saklanıyor, iş sonucu DEĞİL.
    assert 'sa.Column("response_status"' in kaynak
    assert 'sa.Column("response_body"' in kaynak
    # `user_id` yabancı anahtarı ÇIPLAK ve bu bir istisna değil: `app_users`ta
    # `company_id` sütunu YOKTUR.
    assert 'sa.ForeignKeyConstraint(["user_id"], ["app_users.id"])' in kaynak


def test_durum_kumesi_KAPALI_ve_failed_YOK() -> None:
    """İki değer var; `failed` BİLEREK yok.

    MUTASYON: `DURUMLAR`a üçüncü bir değer eklemek bunu KIRMIZI yapar.
    Saklanmış bir başarısızlık, istemcinin tekrar denemesini SONSUZA KADAR
    aynı hataya mahkûm ederdi — 5xx bu yüzden SAKLANMAZ, satır SİLİNİR.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'DURUMLAR = ("processing", "completed")' in kaynak
    assert "ck_idempotency_keys_status" in kaynak
    govde = kaynak.split('"""')[2]
    assert "failed" not in govde


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """`idempotency_keys` açılış DDL'inde BİLDİRİLMİYOR.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu, 0074/0075'te aynı ayrım
    yapıldı): `app/tenancy.py` `companies`i `Table()` olarak bildiriyor ve
    uygulamanın AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor; göç onu VAR
    bulup tek `if` dalını ATLAR ve YEŞİL biter.
    """
    import re

    acilis = ""
    for modul in ("tenancy.py", "core_schema.py", "auth.py", "inventory.py",
                  "finance_engine.py", "workflow.py"):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))

    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    assert "idempotency_keys" not in bildirilen, (
        "idempotency_keys açılış DDL'ine girmiş; göç 0076 onu VAR bulup "
        "atlar (companies'te ölçülen kusurun aynısı)"
    )


def test_defter_KIRACI_TABLOLARI_listesinde() -> None:
    """`company_id` taşıyan her tablo kiracı nöbetçisinin evreninde OLMALI.

    Girmeseydi dışa aktarma o tablonun satırlarını kiracıya VERMEZDİ ve ham
    SQL nöbetçisi tabloyu "kiracıya ait değil" sayıp yüklemsiz sorguyu
    geçirirdi.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert "idempotency_keys" in TENANT_TABLES


def test_ATLANAN_UCLAR_kendi_basligini_okuyan_YEDI_DOSYAYA_karsilik_geliyor() -> None:
    """Atlanan liste ÖLÇÜMDEN geliyor, tahminden değil.

    `Idempotency-Key` başlığını KENDİSİ okuyan router dosyaları taranıyor. Bu
    küme büyürse (yeni bir uç kendi defterini tutmaya başlarsa) kapı KIRMIZI
    olur ve `ATLANAN_UCLAR` bilinçli olarak güncellenir — güncellenmezse İKİ
    defter aynı anahtarı iddia eder ve çakışma cevabı ikisinde FARKLI olurdu.

    KAPI KENDİ İŞİNİ BİR KEZ YAPTI: ilk el yazımı liste BEŞ dosya / DOKUZ uç
    diyordu ve `finance.py` (`POST /api/payments`) ile `work_orders.py`
    (`POST /api/work-orders/{id}/receivable/reverse`) kapıyı KIRDI. İkisi de
    başlığı okuyup KENDİ defterine yazıyor; liste onlarla ONBİRE çıktı.

    MUTASYON: `ATLANAN_UCLAR`ı boşaltmak bunu KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.idempotency import ATLANAN_UCLAR

    okuyan: set[str] = set()
    for yol in sorted((BACKEND / "app" / "routers").glob("*.py")):
        if "idempotency-key" in yol.read_text(encoding="utf-8").lower():
            okuyan.add(yol.name)
    assert okuyan == {
        "finance.py", "late_fees.py", "machines.py", "payment_allocations.py",
        "pos.py", "supplier_prices.py", "work_orders.py",
    }, okuyan

    # ON BİR uç, YEDİ dosya: 1/1/1/1/1/3/3.
    assert len(ATLANAN_UCLAR) == 11, ATLANAN_UCLAR
    assert all(m == "POST" for m, _ in ATLANAN_UCLAR), ATLANAN_UCLAR
    # `farm.py`/`field.py` LİSTEDE DEĞİL ve olmaması ölçülmüş bir karardır:
    # onların idempotensisi `operation_id` GÖVDE alanındadır, başlığı HİÇ
    # okumazlar; ara katman orada EKLEMELİDİR.
    for sablon in ("/api/field-activities", "/api/field-harvests"):
        assert all(y != sablon for _, y in ATLANAN_UCLAR), sablon


def test_ara_katman_KIRACI_COZUMUNDEN_SONRA_kosuyor() -> None:
    """SIRA: `resolve_company` -> iddia. Tersi anahtarın kapsamını YOK EDER.

    Ayrıca `must_change_password` 403'ü İDDİADAN ÖNCE düşmek ZORUNDA:
    düşmeseydi, zorunlu rotasyona takılan bir istek anahtarı iddia eder ve
    kullanıcı parolasını değiştirdikten sonra AYNI anahtarla yeniden
    denediğinde 409 alırdı.

    MUTASYON: iddia bloğunu `resolve_company`nin üstüne taşımak bunu KIRMIZI
    yapar.
    """
    kaynak = ANA.read_text(encoding="utf-8")
    sira_parola = kaynak.index("PASSWORD_CHANGE_REQUIRED")
    sira_firma = kaynak.index("request.state.company_id = resolve_company")
    sira_iddia = kaynak.index("idempotency.iddia_et(")
    sira_next = kaynak.index("response = await call_next(request)")
    assert sira_parola < sira_firma < sira_iddia < sira_next, (
        sira_parola, sira_firma, sira_iddia, sira_next
    )
    # İşleyici patlarsa iddia GERİ ALINIR; bırakılsaydı hiçbir yan etki
    # uygulanmadan anahtar 24 saat 409 dönerdi.
    assert "idempotency.iptal_et(idem)" in kaynak
    assert "response = await idempotency.tamamla(idem, response)" in kaynak


def test_her_SQL_KIRACI_ve_KULLANICI_yuklemi_tasiyor() -> None:
    """Beş sorgunun BEŞİ DE `company_id` ve `user_id` ile daralıyor.

    MUTASYON: herhangi birinden `company_id=:cid`i düşürmek bunu KIRMIZI
    yapar — ve davranışta İKİ FİRMA adımını düşürür. Kiracı yüklemi düşen bir
    okuma, BAŞKA firmanın saklanmış CEVABINI tekrar oynatırdı.
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
    assert len(metinler) == 5, metinler
    for sql in metinler:
        if sql.lstrip().upper().startswith("INSERT"):
            # YAZIM YOLU: kiracı ve kullanıcı sütun listesinde AÇIKÇA duruyor
            # ve değerleri BAĞLI PARAMETRE. Yüklem taşıyamaz — INSERT'ün
            # WHERE'i yoktur.
            assert "idempotency_keys(company_id,user_id,key," in sql, sql
            assert "VALUES(:cid,:uid,:key," in sql, sql
        else:
            assert "company_id=:cid" in sql, sql
            assert "user_id=:uid" in sql, sql
        # SABİT METİN: f-string ya da birleştirilmiş değişken YOK. Kiracı
        # nöbetçisinin dinamik listesine girmemesinin sebebi budur.
        assert "%" not in sql and "{" not in sql, sql


def test_GET_defterin_ADINI_BILE_ANMIYOR() -> None:
    """Okuma zaten idempotenttir; deftere girmesi saf maliyettir.

    MUTASYON: `YAZAN_METOTLAR`a `GET` eklemek bunu KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.idempotency import YAZAN_METOTLAR

    assert YAZAN_METOTLAR == frozenset({"POST", "PUT", "PATCH", "DELETE"})
    assert "GET" not in YAZAN_METOTLAR
    assert "HEAD" not in YAZAN_METOTLAR
    assert "OPTIONS" not in YAZAN_METOTLAR


def test_sablon_deseni_YOL_PARCASI_SINIRINI_asmiyor() -> None:
    """`{...}` tek bir yol parçasıdır; alt yollar atlanan sayılmaz.

    MUTASYON: `_desen`de `[^/]+` yerine `.+` yazmak bunu KIRMIZI yapar ve
    `/api/payment-allocations/1/reversal/anything` sessizce korumasız kalırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.idempotency import kendi_defterini_tutuyor

    assert kendi_defterini_tutuyor("POST", "/api/payment-allocations/1/reversal")
    assert not kendi_defterini_tutuyor(
        "POST", "/api/payment-allocations/1/reversal/extra"
    )
    assert not kendi_defterini_tutuyor("POST", "/api/customers")
    # METOT DA KİMLİĞİN PARÇASI: aynı yola PUT atmak atlanmaz.
    assert not kendi_defterini_tutuyor("PUT", "/api/machines")


# ------------------------------------------------------- göç turu (SQLite) ---

_GOC_TURU = r'''
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

motor = sa.create_engine(settings.database_url)
yapilandirma = Config('alembic.ini')
yapilandirma.set_main_option('sqlalchemy.url', settings.database_url)

DEFTER = 'idempotency_keys'


def gorunen():
    return set(sa.inspect(motor).get_table_names())


command.upgrade(yapilandirma, 'head')
assert DEFTER in gorunen(), 'yukari: defter dogmadi'
gozlemci = sa.inspect(motor)
indeksler = {i['name'] for i in gozlemci.get_indexes(DEFTER)}
assert 'ix_idempotency_keys_expires_at' in indeksler, indeksler
tekiller = {u['name']: tuple(u['column_names'])
            for u in gozlemci.get_unique_constraints(DEFTER)}
assert tekiller.get('uq_idempotency_keys_company_user_key') == (
    'company_id', 'user_id', 'key'), tekiller
assert tekiller.get('uq_idempotency_keys_company_id') == (
    'company_id', 'id'), tekiller
sutunlar = {c['name']: c for c in gozlemci.get_columns(DEFTER)}
assert sutunlar['response_body']['nullable'] is True
assert sutunlar['expires_at']['nullable'] is False
assert sutunlar['company_id']['nullable'] is False
assert sutunlar['user_id']['nullable'] is False

command.downgrade(yapilandirma, '20260909_0075')
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
from starlette.responses import Response

import app.idempotency as idem
from app.db import SessionLocal
from app.main import app

ADMIN_PW = 'Idem54b!123'
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


def defter():
    with SessionLocal() as db:
        return db.execute(text(
            'SELECT company_id,user_id,key,method,route,status,response_status'
            ' FROM idempotency_keys ORDER BY id')).mappings().all()


h = admin_headers()
cid = int(h['X-Company-ID'])

# --- 0. ATLANAN UCLARIN ŞABLONLARI GERÇEKTEN VAR -------------------------
# Bir uç yeniden adlandırılırsa liste SESSİZCE boşa düşmemeli.
sema = app.openapi()
for metot, sablon in idem.ATLANAN_UCLAR:
    assert sablon in sema['paths'], sablon
    assert metot.lower() in sema['paths'][sablon], (metot, sablon)

# --- 1. AYNI ANAHTAR AYNI GÖVDE: ikinci istek TEKRAR OYNATMA -------------
GOVDE = {'name': 'Idempotent Musteri', 'phone': '5550000001'}
BAS = {'Idempotency-Key': 'anahtar-bir'}
ilk = client.post('/api/customers', json=GOVDE, headers={**h, **BAS})
assert ilk.status_code == 201, ilk.text
assert 'Idempotent-Replayed' not in ilk.headers, dict(ilk.headers)

ikinci = client.post('/api/customers', json=GOVDE, headers={**h, **BAS})
assert ikinci.status_code == 201, ikinci.text
assert ikinci.headers.get('Idempotent-Replayed') == 'true', dict(ikinci.headers)
assert ikinci.text == ilk.text, (ilk.text, ikinci.text)

# YAN ETKİ BİR KEZ: ikinci istek YENİ müşteri YAZMADI.
with SessionLocal() as db:
    sayi = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND phone='5550000001'"
    ), {'c': cid}).scalar_one()
assert sayi == 1, sayi

# --- 2. AYNI ANAHTAR BAŞKA GÖVDE: 422 -----------------------------------
# MUTASYON: `iddia_et`te `request_hash` karşılaştırmasını atlamak bu adımı
# KIRMIZI yapar (o hâlde ikinci gövde BİRİNCİNİN cevabını alırdı).
farkli = client.post('/api/customers',
                     json={'name': 'Baska', 'phone': '5550000002'},
                     headers={**h, **BAS})
assert farkli.status_code == 422, farkli.text
assert farkli.json()['code'] == 'IDEMPOTENCY_KEY_REUSED', farkli.text
with SessionLocal() as db:
    assert db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND phone='5550000002'"
    ), {'c': cid}).scalar_one() == 0

# --- 3. GET DEFTERE GİRMİYOR --------------------------------------------
# MUTASYON: `YAZAN_METOTLAR`a GET eklemek bu adımı KIRMIZI yapar.
once = len(defter())
oku = client.get('/api/customers', headers={**h, 'Idempotency-Key': 'okuma'})
assert oku.status_code == 200, oku.text
assert len(defter()) == once, defter()

# --- 4. BAŞLIKSIZ İSTEK DEFTERE GİRMİYOR (OPT-IN) -----------------------
once = len(defter())
bassiz = client.post('/api/customers',
                     json={'name': 'Bassiz', 'phone': '5550000003'}, headers=h)
assert bassiz.status_code == 201, bassiz.text
assert len(defter()) == once, defter()

# --- 5. ATLANAN UÇ: kendi defteri çalışıyor, genel defter BOŞ ------------
# MUTASYON: `ATLANAN_UCLAR`ı boşaltmak bu adımı KIRMIZI yapar.
once = len(defter())
MAKINE = {'name': 'Idem Traktor', 'machine_type': 'TRACTOR',
          'brand': 'Idem', 'model': 'X1'}
mak = client.post('/api/machines', json=MAKINE,
                  headers={**h, 'Idempotency-Key': 'makine-anahtari'})
assert mak.status_code == 201, mak.text
assert len(defter()) == once, defter()
with SessionLocal() as db:
    assert db.execute(text(
        'SELECT COUNT(*) FROM machine_idempotency WHERE company_id=:c'
    ), {'c': cid}).scalar_one() == 1
# Kendi defteri TEKRAR OYNATIYOR ve o cevap ARA KATMANIN başlığını TAŞIMIYOR.
mak2 = client.post('/api/machines', json=MAKINE,
                   headers={**h, 'Idempotency-Key': 'makine-anahtari'})
assert mak2.status_code in (200, 201), mak2.text
assert 'Idempotent-Replayed' not in mak2.headers, dict(mak2.headers)
assert len(defter()) == once, defter()

# --- 6. İKİ KULLANICI, AYNI ANAHTAR: BAĞIMSIZ ---------------------------
# MUTASYON: tekilden ve sorgulardan `user_id`yi düşürmek bu adımı KIRMIZI
# yapar. İkinci kullanıcı SATIRDAN kuruluyor: ölçülen şey kullanıcı kurma
# akışı değil, anahtarın KAPSAMIDIR.
with SessionLocal() as db:
    db.execute(text(
        'INSERT INTO app_users(username,email,display_name,password_hash,'
        'role,is_active,must_change_password,created_at)'
        ' VALUES(:k,:e,:d,:p,:r,:a,:m,:t)'),
        {'k': 'idem2', 'e': 'idem2@ornek.test', 'd': 'Idem Iki', 'p': 'x',
         'r': 'depo', 'a': True, 'm': False, 't': '2026-01-01T00:00:00+00:00'})
    ikinci_uid = db.execute(text(
        "SELECT id FROM app_users WHERE username='idem2'")).scalar_one()
    db.commit()

ORTAK = 'paylasilan-anahtar'
a = idem.iddia_et(company_id=cid, user_id=1, key=ORTAK,
                  metot='POST', yol='/api/x', govde=b'{}')
b = idem.iddia_et(company_id=cid, user_id=ikinci_uid, key=ORTAK,
                  metot='POST', yol='/api/x', govde=b'{}')
assert isinstance(a, idem.Iddia) and isinstance(b, idem.Iddia), (a, b)
with SessionLocal() as db:
    assert db.execute(text(
        'SELECT COUNT(*) FROM idempotency_keys WHERE company_id=:c AND key=:k'
    ), {'c': cid, 'k': ORTAK}).scalar_one() == 2

# --- 7. İKİ FİRMA, AYNI ANAHTAR: BAĞIMSIZ -------------------------------
# MUTASYON: sorgulardan `company_id=:cid`i düşürmek bu adımı KIRMIZI yapar.
with SessionLocal() as db:
    komsu_cid = db.execute(text(
        "INSERT INTO companies (name, is_active, created_at) "
        "VALUES ('Idem Komsu A.S.', 1, :t) RETURNING id"),
        {'t': datetime.now(timezone.utc)}).scalar_one()
    db.commit()
assert komsu_cid != cid
c = idem.iddia_et(company_id=komsu_cid, user_id=1, key=ORTAK,
                  metot='POST', yol='/api/x', govde=b'{}')
assert isinstance(c, idem.Iddia), c
with SessionLocal() as db:
    assert db.execute(text(
        'SELECT COUNT(*) FROM idempotency_keys WHERE key=:k'
    ), {'k': ORTAK}).scalar_one() == 3

# --- 8. AYNI ÜÇLÜ İKİNCİ KEZ: HALA İŞLENİYOR -> 409 ---------------------
try:
    idem.iddia_et(company_id=cid, user_id=1, key=ORTAK,
                  metot='POST', yol='/api/x', govde=b'{}')
except idem.IdempotensiReddi as exc:
    assert exc.status_code == 409, exc.status_code
    assert exc.code == 'IDEMPOTENCY_IN_PROGRESS', exc.code
else:
    raise AssertionError('ikinci iddia 409 vermedi')

# --- 9. 5xx SAKLANMAZ: satır SİLİNİR ------------------------------------
# MUTASYON: `tamamla`nın 5xx dalını kaldırmak bu adımı KIRMIZI yapar ve
# istemci SONSUZA KADAR aynı 500'e mahkûm olurdu.
import asyncio

asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
    idem.tamamla(a, Response(content=b'{"detail":"Sunucu hatasi"}',
                             status_code=500, media_type='application/json'))
)
with SessionLocal() as db:
    assert db.execute(text(
        'SELECT COUNT(*) FROM idempotency_keys WHERE company_id=:c'
        ' AND user_id=1 AND key=:k'), {'c': cid, 'k': ORTAK}).scalar_one() == 0
# Satır gittiği için AYNI anahtar YENİDEN iddia edilebiliyor.
yeniden = idem.iddia_et(company_id=cid, user_id=1, key=ORTAK,
                        metot='POST', yol='/api/x', govde=b'{}')
assert isinstance(yeniden, idem.Iddia), yeniden

# --- 10. SÜRESİ DOLAN ANAHTAR YENİ SAYILIR ------------------------------
# MUTASYON: `iddia_et`teki `expires_at <= :now` süzgecini düşürmek bu adımı
# KIRMIZI yapar (dolmuş anahtar 409'da takılı kalırdı).
with SessionLocal() as db:
    db.execute(text(
        'UPDATE idempotency_keys SET expires_at=:t'
        ' WHERE company_id=:c AND user_id=1 AND key=:k'),
        {'t': datetime.now(timezone.utc) - timedelta(seconds=1),
         'c': cid, 'k': ORTAK})
    db.commit()
tazelenen = idem.iddia_et(company_id=cid, user_id=1, key=ORTAK,
                          metot='POST', yol='/api/x', govde=b'{}')
assert isinstance(tazelenen, idem.Iddia), tazelenen

# --- 11. ZORUNLU PAROLA ROTASYONU İDDİADAN ÖNCE DÜŞER -------------------
# MUTASYON: iddia bloğunu parola kapısının üstüne taşımak bu adımı KIRMIZI
# yapar: takılan istek anahtarı iddia eder ve kullanıcı parolasını
# değiştirdikten sonra AYNI anahtarla 409 alırdı.
with SessionLocal() as db:
    db.execute(text(
        "UPDATE app_users SET must_change_password=1 WHERE username='admin'"))
    db.commit()
once = len(defter())
kilitli = client.post('/api/customers',
                      json={'name': 'Kilitli', 'phone': '5550000009'},
                      headers={**h, 'Idempotency-Key': 'parola-anahtari'})
assert kilitli.status_code == 403, kilitli.text
assert kilitli.json()['code'] == 'PASSWORD_CHANGE_REQUIRED', kilitli.text
assert len(defter()) == once, defter()
with SessionLocal() as db:
    db.execute(text(
        "UPDATE app_users SET must_change_password=0 WHERE username='admin'"))
    db.commit()

# --- 12. BUYUK GOVDE: SESSIZCE KORUMASIZ DEGIL, 413 ---------------------
# MUTASYON: `govde_tamponlanabilir` cagrisini kaldirmak bu adimi KIRMIZI
# yapar. Bu ara katman `RequestBodyLimitMiddleware`in DISINDADIR, yani
# govde okunurken govde sinir kapisi HENUZ kosmamistir.
once = len(defter())
buyuk = client.post('/api/customers',
                    json={'name': 'B' * (1024 * 1024 + 10), 'phone': '5550000010'},
                    headers={**h, 'Idempotency-Key': 'buyuk-anahtar'})
assert buyuk.status_code == 413, (buyuk.status_code, buyuk.text[:200])
assert buyuk.json()['code'] == 'IDEMPOTENCY_REQUEST_TOO_LARGE', buyuk.text
assert len(defter()) == once, defter()
# AYNI GOVDE BASLIKSIZ gonderilince BU KAPI HIC KOSMAZ. Istegin nasil
# bittigi (dogrulama 422'si ya da govde sinir kapisinin 413'u) bu dilimin
# isi DEGIL; olculen sey reddin IDEMPOTENSIDEN GELMEDIGIDIR — geleseydi
# baslik gondermeyen mevcut istemciler de kimildardi.
bassiz_buyuk = client.post('/api/customers',
                           json={'name': 'B' * (1024 * 1024 + 10),
                                 'phone': '5550000010'}, headers=h)
assert 'IDEMPOTENCY_REQUEST_TOO_LARGE' not in bassiz_buyuk.text, (
    bassiz_buyuk.status_code, bassiz_buyuk.text[:200])
assert len(defter()) == once, defter()

# --- 13. 204 (gövdesiz) CEVAP DA TEKRAR OYNATILIYOR ---------------------
musteri_id = ilk.json()['id']
SIL = {'Idempotency-Key': 'silme-anahtari'}
d1 = client.delete('/api/customers/%d' % musteri_id, headers={**h, **SIL})
assert d1.status_code in (200, 204), d1.text
d2 = client.delete('/api/customers/%d' % musteri_id, headers={**h, **SIL})
assert d2.status_code == d1.status_code, (d1.status_code, d2.status_code)
assert d2.headers.get('Idempotent-Replayed') == 'true', dict(d2.headers)
assert d2.content == d1.content, (d1.content, d2.content)

client.close()
print('5.4B DAVRANIS TAMAM')
'''


def test_davranis_smoke_GERCEK_SEMADA(tmp_path: Path) -> None:
    """On üç adım, hepsi GERÇEK şema üzerinde.

    ALT SÜREÇ ZORUNLU: smoke kendi `DATABASE_URL`iyle TAZE bir şema kurar ve
    göç 0076'yı o turda sürer; `app.config.Settings` modül düzeyinde TEK
    KOPYADIR (1B-A/E2/E3 ikizlerinin AYNI gerekçesi), yani süreç İÇİNDE
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
    assert "5.4B DAVRANIS TAMAM" in sonuc.stdout
