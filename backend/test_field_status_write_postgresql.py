"""PostgreSQL ikizi: sahadan durum yazmanın eşzamanlılık davranışı.

SQLite tarafındaki sözleşme testi mantığı doğruluyor ama işin ASIL riskli yarısı
orada hiç çalışmıyor: `FOR UPDATE` yalnız PostgreSQL'de ekleniyor
(`_field_work_order`, dialect kontrolü). Yani iyimser kilidin ve tekrar
korumasının gerçek yarış altındaki davranışı ancak burada görülebilir.

İki yarış sınanıyor ve ikisi de saha kuyruğunun gerçek davranışından geliyor:

1. **Aynı operation_id iki kez, aynı anda.** Cihaz cevabı alamayıp yeniden
   gönderdiğinde olan budur. İkisi de 200 dönmeli ve iş emri bir kez
   ilerlemeli.

   Bu testin ne KANITLAMADIĞINI da yazmak gerek: PostgreSQL'de işi yapan şey
   benzersizlik kısıtı değil, uçtaki ön kontrol SELECT'idir. Ölçüldü — o blok
   devre dışı bırakıldığında ikinci istek 200 değil 409 alıyor, çünkü
   `FOR UPDATE` onu bekletiyor ve kilidi alınca ilerlemiş sürümü görüyor.
   Yani kısıt bu senaryoda hiç devreye girmiyor; test ön kontrolün
   doğruluğunu sınıyor.

2. **Aynı sürümden iki FARKLI işlem, aynı anda.** İkisi de aynı anı görmüş iki
   ayrı değişiklik demektir; yalnız biri geçmelidir. Kaybedenin 409 alması
   `FOR UPDATE`'in çalıştığının kanıtıdır: kilidi bekledikten sonra satırı
   YENİDEN okuyup güncel sürümü görmesi gerekir. Kilit olmasaydı ikisi de
   bayat sürümü geçerli sanardı.
"""
from __future__ import annotations

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

BACKEND = Path(__file__).resolve().parent

# backend/tests bir paket değil (``__init__.py`` yok), bu yüzden ortak smoke
# fonksiyonu normal import ile alınamıyor; dosya yolundan yükleniyor.
_spec = importlib.util.spec_from_file_location(
    "field_status_write_contract",
    BACKEND / "tests" / "test_field_status_write_contract.py",
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_field_status_smoke = _contract.run_field_status_smoke

ADMIN_PW = "FieldStatus!123"


def _pg_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The field test database URL must point to PostgreSQL")
    return database_url


def _admin(client) -> tuple[dict, int]:
    """Dayanıklı giriş: bu dosyadaki smoke bootstrap şifresini değiştiriyor."""
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    if login.status_code == 200:
        body = login.json()
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(body["companies"][0]["id"]),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": "admin123", "new_password": ADMIN_PW},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
        return headers, int(body["user"]["id"])
    login = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    return headers, int(body["user"]["id"])


def _iş_emri_kur(client, headers: dict, admin_id: int, ad: str) -> tuple[int, str]:
    """Kendi firmasında OPEN durumda bir iş emri açar, (id, sürüm) döndürür."""
    firma = client.post("/api/companies", headers=headers, json={"name": ad})
    assert firma.status_code == 201, firma.text
    headers["X-Company-ID"] = str(firma.json()["id"])

    musteri = client.post("/api/customers", headers=headers, json={"name": "Yarış Müşterisi"})
    assert musteri.status_code == 201, musteri.text
    makine = client.post(
        "/api/machines",
        headers=headers,
        json={"customer_id": musteri.json()["id"], "brand": "PG", "model": "SahaYarış"},
    )
    assert makine.status_code == 201, makine.text
    emir = client.post(
        "/api/work-orders",
        headers=headers,
        json={
            "machine_id": makine.json()["id"],
            "customer_id": musteri.json()["id"],
            "technician_id": admin_id,
        },
    )
    assert emir.status_code == 201, emir.text
    emir_id = int(emir.json()["id"])

    anlik = client.get("/api/field/snapshot", headers=headers)
    assert anlik.status_code == 200, anlik.text
    (kayit,) = [w for w in anlik.json()["work_orders"] if w["id"] == emir_id]
    assert kayit["status"] == "OPEN", kayit
    return emir_id, kayit["version"]


def _islem_sayisi(operation_id: str) -> int:
    from sqlalchemy import text as sql_text

    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(
                sql_text(
                    "SELECT COUNT(*) FROM field_operations WHERE operation_id=:oid"
                ),
                {"oid": operation_id},
            ).scalar()
        )


@pytest.mark.postgresql
def test_field_status_write_postgresql() -> None:
    run_field_status_smoke(_pg_url())


@pytest.mark.postgresql
def test_ayni_islem_iki_kez_ayni_anda_bir_kez_uygulanir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kuyruk aynı işlemi eşzamanlı iki kez gönderirse tek sefer uygulanmalı.

    İkisi de 200 dönmeli: biri uygular, diğeri "zaten uygulanmış" deyip güncel
    kaydı verir. İkinciye hata döndürmek yanlış olurdu — iş gerçekten yapılmış
    durumda ve teknisyene başarısızlık göstermek onu tekrar denemeye iter.

    Bu senaryoda koruyucu, uçtaki ön kontrol SELECT'i. ``FOR UPDATE`` istekleri
    sıraya soktuğu için ikinci istek satırı ancak birincisi commit ettikten
    sonra okuyor ve orada işlem kaydını görüyor. (Ön kontrol kaldırıldığında bu
    test 409 ile düşüyor — ölçüldü.)
    """
    monkeypatch.setenv("DATABASE_URL", _pg_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, admin_id = _admin(client)
        emir_id, surum = _iş_emri_kur(client, headers, admin_id, "Saha Tekrar Yarışı")

    op_id = "op-esz-tekrar-0001"
    engel = Barrier(2)

    def gonder() -> int:
        with TestClient(app) as esz:
            engel.wait()
            return esz.post(
                f"/api/field/work-orders/{emir_id}/status",
                headers=headers,
                json={
                    "operation_id": op_id,
                    "expected_version": surum,
                    "status": "IN_PROGRESS",
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as havuz:
        durumlar = sorted(f.result() for f in [havuz.submit(gonder), havuz.submit(gonder)])

    # İkisi de başarılı: biri uygular, diğeri "zaten uygulanmış" deyip güncel
    # kaydı döndürür. Kullanıcıya hata göstermek yanlış olurdu — işlem gerçekten
    # gerçekleşmiş durumda.
    assert durumlar == [200, 200], durumlar
    assert _islem_sayisi(op_id) == 1, "aynı işlem iki kez kaydedilmiş"

    with TestClient(app) as client:
        anlik = client.get("/api/field/snapshot", headers=headers)
        (kayit,) = [w for w in anlik.json()["work_orders"] if w["id"] == emir_id]
        assert kayit["status"] == "IN_PROGRESS", kayit


@pytest.mark.postgresql
def test_ayni_surumden_iki_farkli_islem_yalniz_biri_gecer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """İyimser kilidin gerçek yarış altındaki kanıtı.

    İki istek de aynı ``expected_version``'ı gönderiyor, yani ikisi de aynı anı
    görmüş. Biri geçmeli, diğeri 409 almalı.

    Kaybedenin 409 alabilmesi ``FOR UPDATE``'e bağlı: kilidi bekledikten SONRA
    satırı yeniden okuyup güncel ``updated_at``i görmesi gerekir. Kilit
    olmasaydı ikisi de bayat sürümü geçerli bulur ve ikinci geçiş sessizce
    uygulanırdı — teknisyenin görmediği bir duruma yazma.
    """
    monkeypatch.setenv("DATABASE_URL", _pg_url())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        headers, admin_id = _admin(client)
        emir_id, surum = _iş_emri_kur(client, headers, admin_id, "Saha Kilit Yarışı")

    engel = Barrier(2)

    def gonder(op_id: str, hedef: str) -> int:
        with TestClient(app) as esz:
            engel.wait()
            return esz.post(
                f"/api/field/work-orders/{emir_id}/status",
                headers=headers,
                json={
                    "operation_id": op_id,
                    "expected_version": surum,
                    "status": hedef,
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as havuz:
        birinci = havuz.submit(gonder, "op-kilit-a-0001", "IN_PROGRESS")
        ikinci = havuz.submit(gonder, "op-kilit-b-0001", "IN_PROGRESS")
        durumlar = sorted([birinci.result(), ikinci.result()])

    assert durumlar == [200, 409], durumlar
    # Kaybeden hiç kayıt bırakmamalı: sürüm kontrolü ekleme İŞLEMİNDEN ÖNCE.
    kayitli = _islem_sayisi("op-kilit-a-0001") + _islem_sayisi("op-kilit-b-0001")
    assert kayitli == 1, f"{kayitli} işlem kaydedilmiş, 1 bekleniyordu"
