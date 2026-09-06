"""Açılış koşulu 4 — CANLILIK/GECİKME SİNYALİ.

`app/FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md` dördüncü koşulu şöyle yazıyordu:
"Zamanlayıcı thread'i ölürse ya da kuyruk birikirse bunu söyleyen bir
metrik/alarm yok; tek iz süreç günlüğüdür." Belge boşluğun ŞEKLİNİ de
söylüyor: okuma yüzeyi (koşul 2) onu KAPATMAZ, çünkü `summary` kuyruğun
BOYUNU ve YAŞINI gösterir ama tüketicinin KOŞUP KOŞMADIĞINI göstermez —
**ölü bir thread ile boş bir kuyruk o ekranda AYNI görünür**.

--- BU DOSYA NEYİ KANITLIYOR ------------------------------------------------

1. **KALP ATIŞI HER DÖNGÜDE YAZILIYOR** ve süreç dışına ÇIKIYOR: `settings`
   tablosundaki tek satır, döngüyü koşan sürecin ölümünden sonra da okunur.
   Yalnız süreç-içi bir sayaç, sürecin ölümünü bildiremezdi — tam da ölçmek
   istediğimiz olayda kaybolurdu.
2. **BAYATLIK EŞİĞİ 3×ARALIK** ve eşik KATI BÜYÜKTÜR: tam 3× taze, fazlası
   bayat. Sınır iki YÖNDE ölçülüyor; tek yönlü bir ölçüm eşiği sonsuza çeken
   bir mutasyonu yakalamaz.
3. **ÖLÜ THREAD İLE BOŞ KUYRUK AYRIŞIYOR** — belgenin şikâyetinin kendisi.
4. **BAYRAK KAPALIYKEN** hiçbir kalp atışı yazılmaz ve uç bunu `enabled`
   alanında AYNEN söyler.
5. **İKİ AYRI KAPSAM.** `scheduler` bloğu PLATFORM düzeyindedir (her kiracı
   için AYNI), `pending_oldest_age_seconds` ise KİRACIYA özeldir.

--- BU DOSYANIN İDDİA ETMEDİĞİ ---------------------------------------------

Kalp atışı bir TARİH SERİSİ değildir: tek satır, yalnız SON döngü. "Son bir
saatte kaç döngü koştu" sorusu burada CEVAPLANMAZ ve cevaplanması bir tablo
(yani göç) isterdi. `alive` de bir SÜREÇ-İÇİ gözlemdir: başka bir sürecin
thread'i buradan görünmez ve o durumda karar kalıcı kalp atışına DÜŞER.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]

#: Kalıcı kalp atışının anahtarı, testte BAĞIMSIZ yazılı. Modülden import
#: edilseydi totoloji olurdu: anahtar orada değiştirilse test yine geçerdi ve
#: eski satırı okuyan her operatör sessizce boş bakardı.
BEKLENEN_ANAHTAR = "field_stok_zamanlayici.heartbeat"

#: Bayatlık çarpanı da BAĞIMSIZ yazılı; aynı gerekçe.
BEKLENEN_CARPAN = 3


def _kos(kaynak: str, db_yolu: Path, imza: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


# ---------------------------------------------------------------------------
# 1) SABİTLER
# ---------------------------------------------------------------------------


def test_kalp_anahtari_ve_esik_SABIT() -> None:
    """Anahtar ve çarpan sessizce değişmemeli."""
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    assert z.KALP_ANAHTARI == BEKLENEN_ANAHTAR, z.KALP_ANAHTARI
    assert z.BAYATLIK_CARPANI == BEKLENEN_CARPAN, z.BAYATLIK_CARPANI


# ---------------------------------------------------------------------------
# 2) BAYATLIK EŞİĞİ — SINIR İKİ YÖNDE
# ---------------------------------------------------------------------------


def _bellek_oturumu():
    """`settings` tablosunu taşıyan TEK tabloluk bir bellek veritabanı."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core_schema import settings_table

    motor = create_engine("sqlite://")
    settings_table.create(motor)
    return Session(motor)


def _kalp_yaz(oturum, bitti: datetime) -> None:
    from sqlalchemy import text

    from app.field_stok_zamanlayici import KALP_ANAHTARI

    govde = json.dumps({
        "started_at": (bitti - timedelta(seconds=1)).isoformat(),
        "finished_at": bitti.isoformat(),
        "companies_processed": 1,
        "companies_total": 1,
        "events_processed": 0,
        "last_error": None,
    })
    oturum.execute(
        text("INSERT INTO settings (key, value) VALUES (:k, :v)"),
        {"k": KALP_ANAHTARI, "v": govde},
    )
    oturum.commit()


def _sahte_ayar(monkeypatch, *, acik: bool, aralik: int) -> None:
    from app import field_stok_zamanlayici as z

    monkeypatch.setattr(
        z, "settings",
        SimpleNamespace(
            field_stock_outbox_enabled=acik,
            field_stock_outbox_interval_seconds=aralik,
        ),
    )


def _surec_ici_temizle(monkeypatch) -> None:
    """Süreç-içi kalp atışını ve thread durumunu SIFIRLA.

    `canlilik` süreç-içi kanıtı KALICI kayda TERCİH eder; bu testler kalıcı
    kaydın kendi başına yeterli olduğunu ölçtüğü için süreç-içi kap boş
    olmalı.
    """
    from app import field_stok_zamanlayici as z

    monkeypatch.setattr(z, "_kalp", None)
    monkeypatch.setattr(z, "_thread", None)
    monkeypatch.setattr(z, "_dur", None)
    monkeypatch.setattr(z, "_aralik_saniye", None)
    monkeypatch.setattr(z, "_bu_surecte_baslatildi", False)


def test_bayatlik_sinirinin_TAZE_tarafi(monkeypatch) -> None:
    """`3×aralık` İÇİNDEKİ kalp atışı TAZEDİR — eşik KATI büyüktür."""
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=True, aralik=30)
    oturum = _bellek_oturumu()
    # Bir tık İÇERİ: saat testin kendisi koşarken de ilerliyor ve TAM sınırı
    # duvar saatiyle vurmak yarışa açık olurdu. Ölçülen şey sınırın TARAFI.
    _kalp_yaz(oturum, z._simdi() - timedelta(seconds=BEKLENEN_CARPAN * 30 - 2))

    durum = z.canlilik(oturum)
    assert durum["stale"] is False, durum
    assert durum["interval_seconds"] == 30, durum
    assert durum["seconds_since_last_cycle"] is not None


def test_bayatlik_sinirinin_OTESI_BAYAT(monkeypatch) -> None:
    """`3×aralık`ı AŞAN kalp atışı bayattır.

    MUTASYON: eşiği sonsuza çeken (ya da çarpanı büyüten) bir değişiklik bu
    testi KIRMIZI yakar; yukarıdaki taze taraf ise yakamaz — sınırın iki
    yönü de bu yüzden ölçülüyor.
    """
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=True, aralik=30)
    oturum = _bellek_oturumu()
    _kalp_yaz(oturum, z._simdi() - timedelta(seconds=BEKLENEN_CARPAN * 30 + 5))

    durum = z.canlilik(oturum)
    assert durum["stale"] is True, durum


def test_KALP_ATISI_YOKSA_bayat(monkeypatch) -> None:
    """Hiç döngü bitmemişse de bayattır: kanıt YOKLUĞU tazelik DEĞİLDİR."""
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=True, aralik=30)
    durum = z.canlilik(_bellek_oturumu())
    assert durum["stale"] is True, durum
    assert durum["last_cycle_finished_at"] is None, durum
    assert durum["seconds_since_last_cycle"] is None, durum


def test_OLU_THREAD_ile_BOS_KUYRUK_AYRISIYOR(monkeypatch) -> None:
    """Belgenin şikâyetinin kendisi: thread YOK + kalp atışı ESKİ -> ölü.

    Thread BAŞLATILMIYOR (ölü bir sürecin ardından kalan tablo durumu) ve
    kalıcı kalp atışı bayat: `alive` FALSE, `stale` TRUE. Aynı anda kuyruk
    BOŞ olabilir — iki işaret o yüzden AYRI.
    """
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=True, aralik=30)
    oturum = _bellek_oturumu()
    _kalp_yaz(oturum, z._simdi() - timedelta(hours=4))

    durum = z.canlilik(oturum)
    assert durum["alive"] is False, durum
    assert durum["stale"] is True, durum
    assert durum["enabled"] is True, durum


def test_TAZE_KALP_ATISI_BASKA_SURECTE_CANLI_SAYILIR(monkeypatch) -> None:
    """Bu süreçte thread yok ama kalp atışı TAZE: başkası koşuyor demektir.

    Bu dal, uygulamanın birden fazla kopyası koştuğunda `alive`ın kalıcı
    kayda DÜŞTÜĞÜNÜ ölçer; olmasaydı web kopyaları koşan bir tüketiciyi ÖLÜ
    bildirirdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=True, aralik=30)
    oturum = _bellek_oturumu()
    _kalp_yaz(oturum, z._simdi() - timedelta(seconds=5))

    durum = z.canlilik(oturum)
    assert durum["alive"] is True, durum
    assert durum["stale"] is False, durum


def test_BAYRAK_KAPALIYKEN_enabled_FALSE_ve_alive_FALSE(monkeypatch) -> None:
    """`enabled` YALNIZ `field_stock_outbox_enabled`tan gelir.

    MUTASYON: alanı başka bir bayraktan (ya da thread'in varlığından)
    türeten bir değişiklik burada KIRMIZI yanar — kalp atışı TAZE olduğu
    hâlde bayrak kapalı, yani "başka süreçte koşuyor" dalı da `enabled`ı
    yükseltemez.
    """
    sys.path.insert(0, str(BACKEND))
    from app import field_stok_zamanlayici as z

    _surec_ici_temizle(monkeypatch)
    _sahte_ayar(monkeypatch, acik=False, aralik=30)
    oturum = _bellek_oturumu()
    _kalp_yaz(oturum, z._simdi() - timedelta(seconds=5))

    durum = z.canlilik(oturum)
    assert durum["enabled"] is False, durum
    assert durum["alive"] is False, durum


# ---------------------------------------------------------------------------
# 3) UÇTAN UCA — GERÇEK DÖNGÜ, GERÇEK SATIR, GERÇEK UÇ
# ---------------------------------------------------------------------------


def test_kalp_atisi_HER_DONGUDE_yaziliyor(tmp_path: Path) -> None:
    _kos(_SENARYO_KALP, tmp_path / "kalp.db", "KALP-TAMAM")


def test_bayrak_KAPALIYKEN_kalp_atisi_YAZILMIYOR(tmp_path: Path) -> None:
    _kos(_SENARYO_KAPALI, tmp_path / "kapali.db", "KAPALI-TAMAM")


def test_ozet_ucunda_iki_kapsam_AYRISIYOR(tmp_path: Path) -> None:
    _kos(_SENARYO_UC, tmp_path / "uc.db", "UC-TAMAM")


_SENARYO_KALP = r'''
import json

from sqlalchemy import text

import app.main  # goc zinciri
import app.field_stok_zamanlayici as z
from app.db import SessionLocal
from app.field_stok_zamanlayici import (
    KALP_ANAHTARI, bir_dongu_calistir, canlilik,
)

# --- 1) TEK DONGU SUR ------------------------------------------------------
sonuc = bir_dongu_calistir()
print('SAYAC %s' % sorted(sonuc))

with SessionLocal() as db:
    ham = db.execute(text('SELECT value FROM settings WHERE key=:k'),
                     {'k': KALP_ANAHTARI}).scalar()
assert ham, 'KALP ATISI SATIRI YAZILMADI'
kayit = json.loads(ham)
for alan in ('started_at', 'finished_at', 'companies_processed',
             'events_processed', 'last_error'):
    assert alan in kayit, (alan, kayit)
assert kayit['last_error'] is None, kayit
# Bos kuyrukta da yaziliyor: "olay yok" ile "tuketici yok" AYRI seylerdir.
assert kayit['events_processed'] == 0, kayit
# En az bir firma (bootstrap firmasi) gezildi.
assert kayit['companies_processed'] >= 1, kayit
ilk_bitis = kayit['finished_at']

# --- 2) IKINCI DONGU SATIRI GUNCELLIYOR, IKINCI SATIR ACMIYOR -------------
bir_dongu_calistir()
with SessionLocal() as db:
    satirlar = db.execute(text('SELECT value FROM settings WHERE key=:k'),
                          {'k': KALP_ANAHTARI}).scalars().all()
assert len(satirlar) == 1, satirlar
ikinci = json.loads(satirlar[0])
assert ikinci['finished_at'] >= ilk_bitis, (ilk_bitis, ikinci)

# --- 3) SUREC ICI SAYAC ----------------------------------------------------
assert z._kalp is not None and z._kalp['cycles'] == 2, z._kalp

# --- 4) KALP ATISI TAZE ----------------------------------------------------
with SessionLocal() as db:
    durum = canlilik(db)
assert durum['seconds_since_last_cycle'] is not None, durum
assert durum['stale'] is False, durum
assert durum['interval_seconds'] > 0, durum
print('CANLILIK %s' % sorted(durum))
print('KALP-TAMAM')
'''


_SENARYO_KAPALI = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.field_stok_zamanlayici import KALP_ANAHTARI
from app.main import app

assert settings.field_stock_outbox_enabled is False, 'BAYRAK VARSAYILAN ACIK?'

ADMIN_PW = 'Canlilik!12345'


def admin_headers(client):
    login = client.post('/api/auth/login',
                        json={'username': 'admin', 'password': 'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization': 'Bearer ' + body['access_token'],
         'X-Company-ID': str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password': 'admin123',
                           'new_password': ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer ' + ch.json()['access_token']
    return h


# Uygulama BAYRAK KAPALI ile acilir: zamanlayici HIC baslamaz, hicbir dongu
# kosmaz, dolayisiyla kalp atisi satiri da OLUSMAZ.
with TestClient(app) as client:
    h = admin_headers(client)
    ozet = client.get('/api/field-integration-events/summary', headers=h)
    assert ozet.status_code == 200, ozet.text
    blok = ozet.json()['scheduler']
    print('KAPALI_BLOK %s' % sorted(blok))
    assert blok['enabled'] is False, blok
    assert blok['alive'] is False, blok
    assert blok['last_cycle_started_at'] is None, blok
    assert blok['last_cycle_finished_at'] is None, blok
    assert blok['seconds_since_last_cycle'] is None, blok
    assert blok['stale'] is True, blok
    assert blok['pending_oldest_age_seconds'] is None, blok
    # HATA METNI YUZEYDE YOK; adli deger veritabaninda kalir.
    assert 'last_error' not in blok, blok

with SessionLocal() as db:
    satir = db.execute(text('SELECT value FROM settings WHERE key=:k'),
                       {'k': KALP_ANAHTARI}).scalar()
assert satir is None, satir
print('KAPALI-TAMAM')
'''


_SENARYO_UC = r'''
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.field_stok_zamanlayici import bir_dongu_calistir
from app.main import app

ADMIN_PW = 'Canlilik!12345'


def admin_headers(client):
    login = client.post('/api/auth/login',
                        json={'username': 'admin', 'password': 'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization': 'Bearer ' + body['access_token'],
         'X-Company-ID': str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password': 'admin123',
                           'new_password': ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer ' + ch.json()['access_token']
    return h, body


with TestClient(app) as client:
    h, body = admin_headers(client)
    firma_a = int(h['X-Company-ID'])

    # --- IKINCI KIRACI ------------------------------------------------------
    # Ayni kullanicinin IKINCI firmasi: kiraci kapsami `X-Company-ID` ile
    # cozuluyor, yani JETON degil BASLIK ayirici. Okuma yuzeyinin kendi
    # testi de ikinci kiraciyi boyle kuruyor.
    b = client.post('/api/companies', headers=h,
                    json={'name': 'Canlilik B Firmasi'})
    assert b.status_code == 201, b.text
    firma_b = int(b.json()['id'])
    hb = dict(h, **{'X-Company-ID': str(firma_b)})
    assert firma_b != firma_a

    # --- IKI FIRMAYA DA BEKLEYEN OLAY, YASLARI FARKLI ----------------------
    # Olaylar DOGRUDAN yaziliyor: olculen sey yas ARITMETIGI ve KIRACI
    # kapsami, yazicinin yolu degil (o kosul 2'nin kendi dosyasinda olculuyor).
    # DONGU ONCE KOSAR. Tersi olsaydi dongu tam da bu iki olayi ISLER ve
    # terminal kovaya yazardi — geriye BEKLEYEN olay kalmaz, yas alani da
    # olculemezdi. Sira bir tercih degil, olculmus bir ZORUNLULUK.
    bir_dongu_calistir()

    simdi = datetime.now(timezone.utc)
    eski = simdi - timedelta(days=40)
    yeni = simdi - timedelta(minutes=10)
    with SessionLocal() as db:
        for firma, an, ek in ((firma_a, eski, 'a'), (firma_b, yeni, 'b')):
            db.execute(text(
                "INSERT INTO field_integration_events (company_id, source_type,"
                " source_id, target, idempotency_key, status, attempts,"
                " created_at, updated_at) VALUES (:cid,'field_harvest',1,"
                "'stock',:key,'PENDING',0,:an,:an)"),
                {'cid': firma, 'key': 'canlilik-%s:1:stock' % ek, 'an': an})
        db.commit()

    oa = client.get('/api/field-integration-events/summary', headers=h)
    ob = client.get('/api/field-integration-events/summary', headers=hb)
    assert oa.status_code == 200, oa.text
    assert ob.status_code == 200, ob.text
    za = oa.json()['scheduler']
    zb = ob.json()['scheduler']

    # --- PLATFORM ALANLARI HER KIRACIDA AYNI -------------------------------
    PLATFORM = ('enabled', 'alive', 'last_cycle_started_at',
                'last_cycle_finished_at', 'interval_seconds', 'stale')
    for alan in PLATFORM:
        assert za[alan] == zb[alan], (alan, za[alan], zb[alan])
    assert za['last_cycle_finished_at'] is not None, za
    assert za['stale'] is False, za

    # --- KIRACIYA OZEL ALAN AYRISIYOR --------------------------------------
    ya, yb = za['pending_oldest_age_seconds'], zb['pending_oldest_age_seconds']
    assert ya is not None and yb is not None, (ya, yb)
    # A 40 gunluk, B 10 dakikalik. `company_id` yuklemini KAYBEDEN bir
    # mutasyon B'ye de A'nin yasini gosterirdi ve bu satir KIRMIZI yanar.
    assert ya > 39 * 86400, ya
    assert yb < 3600, yb
    assert ya > yb * 100, (ya, yb)

    # Kuyruk BOS DEGIL ama bu ekranda ayri okunur: kuyruk yasi ile
    # zamanlayici tazeligi IKI AYRI sayidir.
    assert oa.json()['pending_total'] == 1, oa.json()
    assert ob.json()['pending_total'] == 1, ob.json()

print('UC-TAMAM')
'''
