"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 4 tekrar koruması.

SQLite koşusu mantığı doğruluyor. Üretim diyalektinde ayrıca kanıtlanması
gereken şey EŞZAMANLILIK: iki özdeş istek AYNI ANDA geldiğinde ön kontrolün
ikisini de geçirmesi mümkündür, ve o noktada tek koruma benzersizlik kısıtıdır.

SQLite'ta bu yarışı kurmak anlamlı değil — tek yazar kilidi var ve istekler
zaten sıraya giriyor. Bu yüzden yarış testi YALNIZ burada.

Ayrıca PG'de bir şey daha önemli: başarısız bir statement işlemin TAMAMINI
iptal eder. `_kuyruga_isle` bu yüzden INSERT'i `begin_nested()` içine alıyor;
almasaydı kısıt patladığında dış işlem çöker ve mükerrer satırı silme adımı
hiç çalışamazdı — yani mükerrer kayıt kalıcı olurdu.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_offline_idempotency", BACKEND / "tests" / "test_farm_offline_idempotency.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_offline_smoke = _contract.run_offline_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm offline test database URL must point to PostgreSQL")
    return url


def _ayri_veritabani(sonek: str) -> str:
    """Bu dosyadaki İKİ testin AYRI veritabanı kullanmasını sağlar.

    Neden gerekli: her smoke, admin'in ``must_change_password`` bayrağı
    yüzünden şifreyi döndürmek zorunda. İki test aynı veritabanını
    paylaşsaydı ikincisi ``admin123`` ile giremez ve test kodun değil, önceki
    testin bıraktığı durumun yüzünden düşerdi — SQLite tarafında bu sorun yok,
    çünkü orada her test kendi ``tmp_path`` dosyasını alıyor.

    Ölçüldü: paylaşımlı veritabanıyla ikinci test "Kullanıcı adı veya şifre
    hatalı" ile düşüyordu.
    """
    import psycopg
    from sqlalchemy.engine import make_url

    taban = make_url(_pg_url())
    hedef = f"{taban.database}_{sonek}"
    yonetim = taban.set(database="postgres")
    dsn = yonetim.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as baglanti:
        var = baglanti.execute("SELECT 1 FROM pg_database WHERE datname=%s", (hedef,)).fetchone()
        if var:
            # Önceki koşudan kalan durum testi kirletmesin.
            baglanti.execute(f'DROP DATABASE "{hedef}" WITH (FORCE)')
        baglanti.execute(f'CREATE DATABASE "{hedef}"')
    return taban.set(database=hedef).render_as_string(hide_password=False)


@pytest.mark.postgresql
def test_farm_offline_idempotency_postgresql() -> None:
    run_offline_smoke(_ayri_veritabani("offline"))


@pytest.mark.postgresql
def test_concurrent_identical_requests_create_exactly_one_row() -> None:
    """İKİ ÖZDEŞ İSTEK AYNI ANDA: tek satır oluşmalı.

    Bu, ön kontrolün YAKALAYAMAYACAĞI durumdur — iki istek de "daha önce
    uygulanmamış" cevabını alabilir. Kısıt olmasaydı iki faaliyet oluşur ve
    sezon maliyeti sessizce ikiye katlanırdı.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = _ayri_veritabani("race")
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _YARIS],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_YARIS = r'''
import threading

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmRace!123456'
OP = 'yaris-islem-0001-abcdefgh'

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    h['Authorization'] = 'Bearer '+ch.json()['access_token']

    ciftlik = client.post('/api/farms', headers=h, json={'code':'y1','name':'Yarış Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'yp','name':'Yarış Parsel',
                               'area_decare':'25.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Nohut'}).json()

    govde = {'season_id':sezon['id'],'activity_type':'SOWING',
             'performed_at':'2026-04-05T09:00:00+00:00','operation_id':OP}

    sonuc = {}
    kapi = threading.Barrier(2)

    def gonder(i):
        kapi.wait()  # ikisi de TAM aynı anda dursun
        r = client.post('/api/field-activities', headers=h, json=govde)
        sonuc[i] = (r.status_code, r.json())

    t = [threading.Thread(target=gonder, args=(i,)) for i in range(2)]
    for x in t: x.start()
    for x in t: x.join()

    kodlar = [sonuc[i][0] for i in range(2)]
    assert all(k in (200, 201) for k in kodlar), sonuc

    # ASIL İDDİA: veritabanında TEK satır var.
    liste = client.get('/api/field-activities', headers=h).json()
    assert liste['total'] == 1, ('MÜKERRER FAALİYET OLUŞTU', liste)

    # Ve iki istek de AYNI kaydı görmüş olmalı.
    kimlikler = {sonuc[i][1]['id'] for i in range(2)}
    assert kimlikler == {liste['items'][0]['id']}, (kimlikler, liste['items'])

    print('YARIS TESTI TAMAM')
'''
