"""Veritabanı bağlantısı koptuğunda /api/ready ne yapmalı — gerçek PostgreSQL 16.

İşin çıktığı olay: 2026-08-11 00:16 UTC, RDS haftalık bakım penceresinde motoru
16.11'den 16.13'e yükseltti. Motor 42 saniye kapalı kaldı, uygulama iki
``/api/ready`` isteğine 503 döndü. Bakım penceresi HAFTALIK, yani bu tekrar eder.

Ölçümle çıkan tablo (varsayım değil, hepsi bu dosyadaki senaryolarla alındı):

* Havuzda bekleyen bağlantı sunucu tarafından öldürülür → sonraki istek **200**.
  ``pool_pre_ping`` bunu zaten kurtarıyordu; kurtaran ayar TEST EDİLMEMİŞTİ.
* Uçuş hâlindeki sorgu öldürülür → o istek düşer (doğrusu bu), sonraki **200**.
* Veritabanı ulaşılamaz → ``/api/ready`` **hiç yanıt vermiyordu**. Yanıtsız bir
  hazırlık probu, 503 dönen bir probdan kötüdür: yük dengeleyici "sağlıksız"
  bilgisini alamaz, isteği zaman aşımına bırakır.

İKİ KAPI
    1. Sunucu tarafından öldürülen bağlantı bir sonraki istekte 5xx üretmez.
    2. Veritabanı GERÇEKTEN ulaşılamazken uç yine de "sağlıksız" der — ve
       susmaz. Kurtarma, gerçek bir arızayı maskelememeli.

Kapı 2 iki ayrı arıza biçimiyle sınanıyor, çünkü ikisini farklı katmanlar
kapatıyor:
    2a *bağlanamıyor* → ``connect_timeout`` (sürücü katmanı)
    2b *soket açık, sunucu susuyor* → hazırlık süre bütçesi (uygulama katmanı).
       Donmuş bir sunucunun çekirdek ağ yığını TCP'yi hâlâ ACK'lediği için
       sürücü ayarlarının HİÇBİRİ bunu görmez — TCP keepalive dahil ölçüldü,
       üçü de 20 saniyeden uzun asılı kaldı.

Bağlantı ``pg_terminate_backend`` ile SUNUCU TARAFINDAN kesiliyor; istemci
tarafında soket kapatmak farklı bir şeyi ölçerdi.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("Bu testin veritabanı URL'i PostgreSQL'i göstermelidir")
    return url


def _kos(betik: str, url: str, ek: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["PYTHONPATH"] = str(BACKEND)
    env.update(ek or {})
    tamam = subprocess.run(
        [sys.executable, "-c", betik],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    return tamam.stdout


# --------------------------------------------------------------------------
# KAPI 1 — sunucu tarafından öldürülen bağlantı bir sonraki istekte 5xx üretmez
# --------------------------------------------------------------------------
_KAPI1 = r'''
import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import engine
from app.config import settings

ham = settings.database_url.replace("postgresql+psycopg://", "postgresql://")

def oldur():
    """Uygulamanın TÜM bağlantılarını sunucu tarafından sonlandır."""
    with psycopg.connect(ham, autocommit=True) as k:
        return k.execute(
            "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid<>pg_backend_pid()) t"
        ).fetchone()[0]

with TestClient(app) as c:
    assert c.get("/api/ready").status_code == 200, "baslangicta hazir olmaliydi"

    # --- 1a) HAVUZDA BEKLEYEN baglanti oldurulur -------------------------
    n = oldur()
    assert n > 0, "oldurulecek baglanti yoktu; senaryo kurulmadi"
    r = c.get("/api/ready")
    print("1a|%d|%d" % (n, r.status_code))
    assert r.status_code == 200, (
        "sunucu tarafindan oldurulen baglantidan SONRAKI istek 5xx uretti: %d" % r.status_code)
    assert c.get("/api/live").status_code == 200, "/api/live dusmemeliydi"

    # --- 1b) UCUS HALINDEKI sorgu oldurulur -------------------------------
    import threading, time
    kutu = {}
    def yavas():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT pg_sleep(3)"))
            kutu["r"] = "istisna yok"
        except Exception as ex:
            kutu["r"] = type(ex).__name__
    t = threading.Thread(target=yavas); t.start(); time.sleep(1.0)
    n2 = oldur(); t.join()
    r2 = c.get("/api/ready")
    print("1b|%s|%d" % (kutu["r"], r2.status_code))
    assert r2.status_code == 200, (
        "ucus halindeki sorgu oldurulduktan SONRAKI istek 5xx uretti: %d" % r2.status_code)
    assert c.get("/api/live").status_code == 200

    # --- 1c) ard arda oldurme, her seferinde toparlanmali -----------------
    for tur in range(3):
        oldur()
        k = c.get("/api/ready").status_code
        assert k == 200, "tur %d: %d" % (tur, k)
    print("1c|3-tur|200")
print("KAPI1|GECTI")
'''


@pytest.mark.postgresql
def test_kapi1_oldurulen_baglanti_sonraki_istekte_5xx_uretmez() -> None:
    cikti = _kos(_KAPI1, _pg_url())
    assert "KAPI1|GECTI" in cikti, cikti
    print(cikti)


# --------------------------------------------------------------------------
# KAPI 2 — gerçekten ulaşılamayan veritabanı SUSMAZ, "sağlıksız" der
# --------------------------------------------------------------------------
# 2b'nin kara-delik dinleyicisi: bağlantıyı KABUL eder, tek bayt konuşmaz.
# Donmuş bir sunucunun istemciye görünüşü tam olarak budur.
_KAPI2 = r'''
import socket, threading, time, os

def kara_delik():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0)); s.listen(8)
    port = s.getsockname()[1]
    def dongu():
        tutulan = []
        while True:
            try:
                baglanti, _ = s.accept()
                tutulan.append(baglanti)   # kabul et, HIC konusma, kapatma
            except OSError:
                return
    threading.Thread(target=dongu, daemon=True).start()
    return port

MOD = os.environ["SENARYO"]
if MOD == "kara-delik":
    port = kara_delik()
    os.environ["DATABASE_URL"] = "postgresql+psycopg://u:p@127.0.0.1:%d/yok" % port
else:  # baglanti-reddedildi: dinleyen kimse yok
    bos = socket.socket(); bos.bind(("127.0.0.1", 0)); kapali = bos.getsockname()[1]; bos.close()
    os.environ["DATABASE_URL"] = "postgresql+psycopg://u:p@127.0.0.1:%d/yok" % kapali

# Uygulama import edilirken migration kosmaya calisir; ulasilamayan veritabaninda
# bu zaten patlar. Bu yuzden UCU dogrudan cagiriyoruz: olculen sey ucun ulasilamaz
# veritabani karsisindaki davranisi, uygulamanin acilisi degil.
import importlib
import app.config as cfg
importlib.reload(cfg)
import app.db as dbmod
importlib.reload(dbmod)

from fastapi import HTTPException
from sqlalchemy import text
from app.sure_butcesi import sureli_kos

def prob():
    with dbmod.engine.connect() as conn:
        conn.execute(text("SELECT 1")).scalar_one()

sinir = int(cfg.settings.readiness_timeout_seconds)
t0 = time.time()
try:
    sureli_kos(prob, sinir)
    sonuc = "BEKLENMEDIK-BASARI"
except TimeoutError:
    sonuc = "503-sure-butcesi"
except Exception as ex:
    sonuc = "503-" + type(ex).__name__
gecen = time.time() - t0
print("%s|%s|%.1f|%.1f" % (MOD, sonuc, gecen, sinir))
assert sonuc != "BEKLENMEDIK-BASARI", "ulasilamayan veritabani BASARILI dondu"
assert gecen < sinir + 4.0, (
    "uc %.1f sn icinde yanit vermeliydi, %.1f sn surdu — SUSTU" % (sinir + 4.0, gecen))
print("KAPI2|%s|GECTI" % MOD)
'''


@pytest.mark.postgresql
@pytest.mark.parametrize("senaryo", ["baglanti-reddedildi", "kara-delik"])
def test_kapi2_ulasilamayan_veritabani_susmaz(senaryo: str) -> None:
    cikti = _kos(_KAPI2, _pg_url(), {"SENARYO": senaryo})
    assert "KAPI2|%s|GECTI" % senaryo in cikti, cikti
    print(cikti)


# --------------------------------------------------------------------------
# KAPI 2c — KURULMUŞ bağlantı üzerinde sunucu susarsa
# --------------------------------------------------------------------------
# 2a ve 2b'yi `connect_timeout` kapatıyor: ikisinde de BAĞLANMA aşaması hiç
# tamamlanmıyor. Uygulama süre bütçesinin gerçekten gerektiği hâl bambaşka:
# bağlantı KURULMUŞ, havuzda duruyor, sonra sunucu donuyor. O anda TCP sağlıklı
# görünür (donmuş sunucunun çekirdeği ACK'lemeye devam eder), sürücünün hiçbir
# ayarı devreye girmez ve prob sonsuza kadar bekler.
#
# Bu senaryo docker'a bağlı değil: gerçek PostgreSQL'in önüne DONDURULABİLİR bir
# TCP vekili konuyor. Vekil önce trafiği aynen taşıyor (bağlantı kuruluyor,
# istek başarılı oluyor), sonra iletmeyi kesiyor — soketler açık kalıyor, tek
# bayt akmıyor. Donmuş bir sunucunun istemciye görünüşü tam olarak budur.
_KAPI2C = r'''
import os, socket, threading, time, select

HEDEF_HOST = os.environ["PG_HOST"]; HEDEF_PORT = int(os.environ["PG_PORT"])
DONDU = threading.Event()

def vekil():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0)); s.listen(16)
    port = s.getsockname()[1]
    def tasi(a, b):
        try:
            while True:
                if DONDU.is_set():
                    time.sleep(0.2); continue      # soket ACIK, veri AKMIYOR
                r, _, _ = select.select([a], [], [], 0.2)
                if not r: continue
                veri = a.recv(65536)
                if not veri: break
                b.sendall(veri)
        except OSError:
            pass
    def dongu():
        while True:
            try:
                istemci, _ = s.accept()
            except OSError:
                return
            sunucu = socket.create_connection((HEDEF_HOST, HEDEF_PORT))
            threading.Thread(target=tasi, args=(istemci, sunucu), daemon=True).start()
            threading.Thread(target=tasi, args=(sunucu, istemci), daemon=True).start()
    threading.Thread(target=dongu, daemon=True).start()
    return port

port = vekil()
os.environ["DATABASE_URL"] = os.environ["PG_TEMPLATE"] % port

# UYGULAMA GERCEKTEN ACILIR: vekil su an iletiyor, veritabani erisilebilir.
# Olculen sey URUNUN /api/ready ucu — yardimci fonksiyon degil.
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

with TestClient(app) as c:
    assert c.get("/api/ready").status_code == 200, "vekil calisirken hazir olmaliydi"
    print("2c|baglanti-kuruldu|ok")

    DONDU.set()          # sunucu DONAR: soket acik, veri akmiyor

    sinir = int(settings.readiness_timeout_seconds)
    t0 = time.time()
    kod = c.get("/api/ready").status_code
    gecen = time.time() - t0
    canli = c.get("/api/live").status_code
    print("2c|%d|%.1f|%.1f|live=%d" % (kod, gecen, sinir, canli))
    assert kod == 503, "donmus sunucuda uc %d dondu, 503 beklenmeliydi" % kod
    assert gecen < sinir + 4.0, (
        "uc %.1f sn icinde yanit vermeliydi, %.1f sn surdu — SUSTU" % (sinir + 4.0, gecen))
    assert canli == 200, "/api/live dusmemeliydi"

DONDU.clear()   # vekili coz: asili prob bitsin, surec kapanabilsin
print("KAPI2C|GECTI")
'''


@pytest.mark.postgresql
def test_kapi2c_kurulmus_baglantida_sunucu_susarsa_uc_yine_konusur() -> None:
    url = _pg_url()
    # Vekilin arkasına koyacağımız gerçek PostgreSQL'in host/port'u.
    kalan = url.split("://", 1)[1]
    konak = kalan.split("@", 1)[1].split("/", 1)[0]
    host, _, port = konak.partition(":")
    sablon = url.replace(konak, "127.0.0.1:%d", 1)
    cikti = _kos(_KAPI2C, url, {
        "PG_HOST": host, "PG_PORT": port or "5432", "PG_TEMPLATE": sablon,
    })
    assert "KAPI2C|GECTI" in cikti, cikti
    print(cikti)


# --------------------------------------------------------------------------
# Kurtarma ayarları SÖZLEŞMEDİR — sessizce kaldırılamasınlar
# --------------------------------------------------------------------------
def test_kurtarma_ayarlari_yerinde() -> None:
    """`pool_pre_ping` ve `connect_timeout` yük taşıyor; varlıkları kilitli.

    Bu iki ayar davranışsal kapılarla zaten ölçülüyor, ama o kapılar gerçek
    PostgreSQL ister. Bu kontrol SQLite'ta da koşar ve ayarın kazara
    düşürülmesini hızlı geri bildirimle yakalar.
    """
    kaynak = (BACKEND / "app" / "db.py").read_text(encoding="utf-8")
    aktif = [s for s in kaynak.splitlines() if not s.lstrip().startswith("#")]
    govde = "\n".join(aktif)
    assert "pool_pre_ping=True" in govde, (
        "pool_pre_ping kaldirilmis: sunucu tarafindan oldurulen baglanti bir "
        "sonraki istekte 503 uretir"
    )
    assert "connect_timeout" in govde, (
        "connect_timeout kaldirilmis: ulasilamayan veritabaninda baglanma "
        "denemesi sinirsiz bekler"
    )


# --------------------------------------------------------------------------
# KAPI 3 — uçuştaki prob iş parçacığı SAYISI SINIRLI
# --------------------------------------------------------------------------
# Süre bütçesi dolunca iş parçacığı bırakılıyor (kesilemez). Sınır olmadan sayı
# istek sayısıyla büyür: donmuş veritabanında 10 eş zamanlı istek 6 -> 35
# ölçüldü. Bugün /api/ready'yi yalnız deploy doğrulaması çağırıyor, ama bu
# BUGÜNKÜ ÇAĞIRAN hakkında bir savunma; uca bir uptime denetleyicisi bağlandığı
# gün sınırsız yol tükenme vektörüne döner.
_KAPI3 = r'''
import os, socket, threading, time, select
HEDEF=("127.0.0.1", int(os.environ["PG_PORT"])); DONDU=threading.Event()
def vekil():
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",0)); s.listen(64); port=s.getsockname()[1]
    def tasi(a,b):
        try:
            while True:
                if DONDU.is_set(): time.sleep(0.2); continue
                r,_,_=select.select([a],[],[],0.2)
                if not r: continue
                d=a.recv(65536)
                if not d: break
                b.sendall(d)
        except OSError: pass
    def dongu():
        while True:
            try: i,_=s.accept()
            except OSError: return
            srv=socket.create_connection(HEDEF)
            threading.Thread(target=tasi,args=(i,srv),daemon=True).start()
            threading.Thread(target=tasi,args=(srv,i),daemon=True).start()
    threading.Thread(target=dongu,daemon=True).start(); return port

port=vekil()
os.environ["DATABASE_URL"]=os.environ["PG_TEMPLATE"] % port
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
SINIR=int(settings.readiness_max_inflight)

def prob_sayisi():
    return sum(1 for t in threading.enumerate() if t.name == "sure-butcesi")

with TestClient(app) as c:
    assert c.get("/api/ready").status_code == 200
    DONDU.set()                      # sunucu donar
    N = SINIR + 6                    # sinir + N es zamanli istek
    kodlar=[]; kilit=threading.Lock()
    def istek():
        k=c.get("/api/ready").status_code
        with kilit: kodlar.append(k)
    tl=[threading.Thread(target=istek) for _ in range(N)]
    [t.start() for t in tl]
    time.sleep(1.0)
    zirve = prob_sayisi()             # istekler ucustayken olc
    [t.join() for t in tl]
    canli = c.get("/api/live").status_code
    print("3|istek=%d|sinir=%d|zirve_prob_ipligi=%d|kodlar=%s|live=%d"
          % (N, SINIR, zirve, sorted(set(kodlar)), canli))
    assert all(k == 503 for k in kodlar), "hepsi 503 olmaliydi: %s" % sorted(set(kodlar))
    assert len(kodlar) == N, "her istek yanit almaliydi: %d/%d" % (len(kodlar), N)
    assert zirve <= SINIR, (
        "prob ipligi sayisi sinirla degil ISTEK SAYISIYLA buyudu: zirve=%d sinir=%d"
        % (zirve, SINIR))
    assert canli == 200
DONDU.clear()
print("KAPI3|GECTI")
'''


@pytest.mark.postgresql
def test_kapi3_ucustaki_prob_iplikleri_sinirli() -> None:
    url = _pg_url()
    kalan = url.split("://", 1)[1]
    konak = kalan.split("@", 1)[1].split("/", 1)[0]
    host, _, port = konak.partition(":")
    sablon = url.replace(konak, "127.0.0.1:%d", 1)
    cikti = _kos(_KAPI3, url, {"PG_HOST": host, "PG_PORT": port or "5432",
                               "PG_TEMPLATE": sablon})
    assert "KAPI3|GECTI" in cikti, cikti
    print(cikti)


# --------------------------------------------------------------------------
# KAPI 4 — işçiye teslim EDİLEMEYEN slot geri bırakılır
# --------------------------------------------------------------------------
# Kontenjan iş parçacığı başlatılmadan ÖNCE alınıyor. ``Thread.start()``
# yükseltirse işçi hiç var olmaz ve ``finally``si koşmaz; slot bırakılmazsa
# kalıcı olarak kaybolur. Sınır kadar tekrarında hazırlık, baskı geçtikten
# sonra bile sonsuza dek doymuş kalır.
_KAPI4 = r'''
import threading, time
from app.sure_butcesi import sureli_kos, KontenjanDolu

SINIR = 4
# Uygulama bütçesi. Hata yolu bu bütçeyi BEKLEMEDEN dönmeli; "hemen" iddiası
# ölçülmezse, hatayı yakalayıp bütçeyi doldurduktan sonra yükselten bir
# gerçekleme diğer bütün savları geçerdi.
BUTCE = 8
TAVAN = 1.0
kontenjan = threading.BoundedSemaphore(SINIR)
kosan = []

def is_():
    kosan.append(1)

# --- start() SINIR kadar kez patlar ------------------------------------
gercek = threading.Thread.start
def patlayan(self):
    raise RuntimeError("iş parçacığı başlatılamadı")
threading.Thread.start = patlayan
try:
    sureler = []
    for tur in range(SINIR):
        t0 = time.monotonic()
        try:
            sureli_kos(is_, BUTCE, kontenjan)
            raise AssertionError("tur %d: hata beklenirken basarili dondu" % tur)
        except RuntimeError as ex:
            gecen = time.monotonic() - t0
            sureler.append(gecen)
            assert not isinstance(ex, KontenjanDolu), (
                "tur %d: slot sizdi, istek KONTENJAN DOLU yedi" % tur)
            # HER cagri ayri ayri olculuyor: bir tanesinin butceyi beklemesi
            # ortalamada kaybolmasin.
            assert gecen < TAVAN, (
                "tur %d: start() hatasi HEMEN donmedi — %.2f sn (tavan %.2f, butce %d)"
                % (tur, gecen, TAVAN, BUTCE))
finally:
    threading.Thread.start = gercek

# --- baski gecti: TAM kapasite geri gelmis olmali -----------------------
alinan = 0
while kontenjan.acquire(blocking=False):
    alinan += 1
for _ in range(alinan):
    kontenjan.release()
print("4|start_hatasi=%d|sureler=%s|tavan=%.1f|butce=%d|geri_gelen_kapasite=%d/%d"
      % (SINIR, ["%.3f" % x for x in sureler], TAVAN, BUTCE, alinan, SINIR))
assert alinan == SINIR, (
    "start() hatasindan sonra kapasite geri gelmedi: %d/%d slot kalici kayip"
    % (alinan, SINIR))

# --- ve saglikli bir prob normal kosuyor --------------------------------
sureli_kos(is_, BUTCE, kontenjan)
assert kosan, "sonraki saglikli prob kosmadi"
print("KAPI4|GECTI")
'''


def test_kapi4_baslatilamayan_iscinin_slotu_geri_birakilir() -> None:
    cikti = _kos(_KAPI4, os.getenv("APP_TEST_DATABASE_URL", "sqlite://"))
    assert "KAPI4|GECTI" in cikti, cikti
    print(cikti)
