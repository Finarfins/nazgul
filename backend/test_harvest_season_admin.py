"""Harman sezon takvimi YÖNETİM yüzeyi.

#159 çözümleyicisinin kendisini değil, yönetim ekranının dayandığı sözleşmeyi
doğrular: tenant sınırlı CRUD, finance/admin RBAC, kural öncelik (scope) alanı
ve salt-okuma önizleme ucunun fail-closed davranışı.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def run_harvest_season_admin_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    # The smoke asserts on Turkish error messages, so the child must emit UTF-8
    # regardless of the host console codepage (Windows defaults to cp1254 and
    # would otherwise kill the reader thread mid-decode).
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    assert completed.returncode == 0, (completed.stdout or "") + (
        completed.stderr or ""
    )


def test_preview_requires_finance_permission() -> None:
    """Önizleme salt-okuma olsa da yönetim yüzeyinde kalır: finance/admin."""
    assert (
        required_permission("GET", "/api/harvest-scheduling/preview") == "finance"
    )
    assert (
        required_permission("PUT", "/api/harvest-scheduling/regions/1") == "finance"
    )
    assert (
        required_permission("DELETE", "/api/harvest-scheduling/rules/1") == "finance"
    )


def test_harvest_season_admin_sqlite(tmp_path: Path) -> None:
    run_harvest_season_admin_smoke(
        f"sqlite:///{(tmp_path / 'harvest-admin.db').as_posix()}"
    )


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app


def login(client, username, password, new_password=None):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if new_password:
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": password, "new_password": new_password},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


def create_user(client, admin_headers, username, role):
    initial = "HarvestAdminUser123!"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "display_name": username,
            "password": initial,
            "role": role,
        },
    )
    assert created.status_code in (200, 201), created.text
    return login(client, username, initial, "HarvestAdminUser456!")


def region(client, headers, code, name, expected=201):
    response = client.post(
        "/api/harvest-scheduling/regions",
        headers=headers,
        json={"code": code, "name": name, "active": True},
    )
    assert response.status_code == expected, response.text
    return response.json()["id"] if expected == 201 else None


def calendar(client, headers, season_code, due_date, *, name=None,
             region_id=None, category=None, expected=201):
    payload = {
        "season_code": season_code,
        "name": name or season_code,
        "season_year": int(due_date[:4]),
        "due_date": due_date,
        "active": True,
    }
    if region_id is not None:
        payload["region_id"] = region_id
    if category is not None:
        payload["product_category"] = category
    response = client.post(
        "/api/harvest-scheduling/calendars", headers=headers, json=payload
    )
    assert response.status_code == expected, response.text
    return response.json()["id"] if expected == 201 else None


def rule(client, headers, season_code, *, customer_id=None, region_id=None,
         category=None, priority=0, expected=201):
    payload = {
        "season_code": season_code,
        "priority": priority,
        "active": True,
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    if region_id is not None:
        payload["region_id"] = region_id
    if category is not None:
        payload["product_category"] = category
    response = client.post(
        "/api/harvest-scheduling/rules", headers=headers, json=payload
    )
    assert response.status_code == expected, response.text
    return response.json() if expected == 201 else None


def customer(client, headers, name, region_id=None):
    response = client.post("/api/customers", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    customer_id = response.json()["id"]
    if region_id is not None:
        assigned = client.put(
            "/api/harvest-scheduling/customers/%d/region" % customer_id,
            headers=headers,
            json={"region_id": region_id},
        )
        assert assigned.status_code == 200, assigned.text
    return customer_id


def product(client, headers, name, category):
    response = client.post(
        "/api/products",
        headers=headers,
        json={"name": name, "category": category, "sale_price": "10", "stock": "50"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def preview(client, headers, customer_id, product_ids, on_date, calendar_id=None):
    params = [("customer_id", customer_id), ("transaction_date", on_date)]
    params += [("product_ids", pid) for pid in product_ids]
    if calendar_id is not None:
        params.append(("calendar_id", calendar_id))
    return client.get(
        "/api/harvest-scheduling/preview", headers=headers, params=params
    )


with TestClient(app) as client:
    admin = login(client, "admin", "admin123", "HarvestAdminBoss123!")
    muhasebe = create_user(client, admin, "season_admin_muhasebe", "muhasebe")
    satis = create_user(client, admin, "season_admin_satis", "satis")
    rapor = create_user(client, admin, "season_admin_rapor", "rapor")

    # ---- RBAC: yetkisiz roller yönetim uçlarının hiçbirini göremez -------
    for denied in (satis, rapor):
        assert preview(
            client, denied, 1, [1], "2026-01-15"
        ).status_code == 403
        assert client.get(
            "/api/harvest-scheduling/rules", headers=denied
        ).status_code == 403
        assert client.get(
            "/api/harvest-scheduling/rules/conflicts", headers=denied
        ).status_code == 403
        assert client.put(
            "/api/harvest-scheduling/regions/1",
            headers=denied,
            json={"code": "X", "name": "X", "active": True},
        ).status_code == 403
        assert client.delete(
            "/api/harvest-scheduling/regions/1", headers=denied
        ).status_code == 403
        assert client.delete(
            "/api/harvest-scheduling/calendars/1", headers=denied
        ).status_code == 403
        assert client.delete(
            "/api/harvest-scheduling/rules/1", headers=denied
        ).status_code == 403

    # ---- Kurgu -----------------------------------------------------------
    merkez = region(client, muhasebe, "MERKEZ", "Merkez Ova")
    yayla = region(client, muhasebe, "YAYLA", "Yayla")
    pancar = product(client, admin, "Pancar Tohumu", "Tohum")
    yedek = product(client, admin, "Yedek Parça", "Yedek")

    pancar_takvim = calendar(
        client, muhasebe, "PANCAR_SOKUMU", "2026-11-30", name="Pancar Sökümü 2026"
    )
    yedek_takvim = calendar(
        client, muhasebe, "YEDEK_SEZON", "2026-10-31", category="Yedek"
    )

    rule(client, muhasebe, "PANCAR_SOKUMU")
    rule(client, muhasebe, "YEDEK_SEZON", category="Yedek")

    ciftci = customer(client, muhasebe, "Pancar Çiftçisi", merkez)

    # ---- ÖNİZLEME doğru vadeyi çözer -------------------------------------
    ok = preview(client, muhasebe, ciftci, [pancar], "2026-01-15")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["due_date"] == "2026-11-30", body
    assert body["calendar_id"] == pancar_takvim, body
    assert body["season_code"] == "PANCAR_SOKUMU", body
    assert body["season_name"] == "Pancar Sökümü 2026", body
    assert body["season_year"] == 2026, body
    assert body["mode"] == "rule", body
    assert body["customer_id"] == ciftci, body
    assert body["customer_region_id"] == merkez, body
    assert body["transaction_date"] == "2026-01-15", body
    assert body["product_categories"] == ["tohum"], body
    assert len(body["rule_ids"]) == 1, body

    # Açık takvim seçimi de aynı uçtan önizlenebilir.
    explicit = preview(
        client, muhasebe, ciftci, [pancar], "2026-01-15", calendar_id=pancar_takvim
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["mode"] == "explicit_calendar", explicit.text
    assert explicit.json()["rule_ids"] == [], explicit.text

    # ---- Fail-closed: sezon yok (422) ------------------------------------
    # Kurallar 2026 boyunca geçerli; 2027 satışı hiçbir kurala düşmez.
    sezonsuz = customer(client, muhasebe, "Sezonsuz Çiftçi")
    missing = preview(client, muhasebe, sezonsuz, [pancar], "2027-03-01")
    assert missing.status_code == 422, missing.text
    assert "sezonu tanımlı değil" in missing.json()["detail"], missing.text

    # ---- Fail-closed: karma sezon (409) ----------------------------------
    mixed = preview(client, muhasebe, ciftci, [pancar, yedek], "2026-01-15")
    assert mixed.status_code == 409, mixed.text
    assert "farklı harman sezonlarına" in mixed.json()["detail"], mixed.text

    # ---- Fail-closed: aynı öncelikte çakışan kural (409) -----------------
    calendar(client, muhasebe, "RAKIP_SEZON", "2026-12-01")
    rakip = rule(client, muhasebe, "RAKIP_SEZON")
    conflict = preview(client, muhasebe, ciftci, [pancar], "2026-01-15")
    assert conflict.status_code == 409, conflict.text
    assert "Aynı öncelikte" in conflict.json()["detail"], conflict.text

    # Öncelik ayrıştırılınca çakışma çözülür — ekranın önerdiği düzeltme.
    bumped = client.put(
        "/api/harvest-scheduling/rules/%d" % rakip["id"],
        headers=muhasebe,
        json={
            "season_code": "RAKIP_SEZON",
            "priority": 5,
            "active": True,
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert bumped.status_code == 200, bumped.text
    resolved = preview(client, muhasebe, ciftci, [pancar], "2026-01-15")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["season_code"] == "RAKIP_SEZON", resolved.text

    # Çakışmayı geri al ki sonraki senaryolar temiz zeminde koşsun.
    assert client.delete(
        "/api/harvest-scheduling/rules/%d" % rakip["id"], headers=muhasebe
    ).status_code == 204

    # ---- Kural öncelik (scope) alanı çözümleyiciyle aynı sırayı verir -----
    scoped = {
        "customer": rule(
            client, muhasebe, "PANCAR_SOKUMU", customer_id=ciftci, priority=-50
        )["scope"],
        "region_product": rule(
            client, muhasebe, "PANCAR_SOKUMU", region_id=yayla,
            category="Tohum", priority=-50,
        )["scope"],
        "product": rule(
            client, muhasebe, "PANCAR_SOKUMU", category="Tohum", priority=-50
        )["scope"],
        "region": rule(
            client, muhasebe, "PANCAR_SOKUMU", region_id=yayla, priority=-50
        )["scope"],
    }
    assert scoped["customer"] == 50, scoped
    assert scoped["region_product"] == 40, scoped
    assert scoped["product"] == 30, scoped
    assert scoped["region"] == 20, scoped
    listed = client.get("/api/harvest-scheduling/rules", headers=muhasebe)
    assert listed.status_code == 200, listed.text
    assert all("scope" in row for row in listed.json()), listed.text
    generic = [row for row in listed.json() if row["scope"] == 10]
    assert generic, listed.text

    # ---- Çakışma analizi motorla birebir ---------------------------------
    def conflicts(headers=muhasebe):
        response = client.get(
            "/api/harvest-scheduling/rules/conflicts", headers=headers
        )
        assert response.status_code == 200, response.text
        return response.json()

    def conflict_pairs(headers=muhasebe):
        return {tuple(row["rule_ids"]) for row in conflicts(headers)}

    # Mevcut kurallar farklı kapsam/önceliklerde: yanlış pozitif yok.
    assert conflicts() == [], conflicts()

    # (1) Aynı müşteri + kategorisiz kural + Tohum kuralı + eşit öncelik.
    # Kategorisiz kural TÜM kategorilere uyar, ikisi de kapsam 50 → Tohum
    # bağlamında motor sıralayamaz. Naif kategori-eşitliği bunu kaçırıyordu.
    catch_all = rule(client, muhasebe, "PANCAR_SOKUMU", customer_id=ciftci, priority=7)
    tohum_rule = rule(
        client, muhasebe, "PANCAR_SOKUMU", customer_id=ciftci,
        category="Tohum", priority=7,
    )
    expected_pair = tuple(sorted((catch_all["id"], tohum_rule["id"])))
    assert catch_all["scope"] == 50 and tohum_rule["scope"] == 50, (
        catch_all["scope"], tohum_rule["scope"]
    )
    assert expected_pair in conflict_pairs(), conflicts()
    flagged = [row for row in conflicts() if tuple(row["rule_ids"]) == expected_pair][0]
    assert flagged["scope"] == 50, flagged
    assert flagged["priority"] == 7, flagged
    assert flagged["customer_id"] == ciftci, flagged
    assert flagged["product_category"] == "tohum", flagged

    # (3) Gerçek satış motoru da aynı bağlamda 409 veriyor: ekran ile motor eşleşir.
    engine = preview(client, muhasebe, ciftci, [pancar], "2026-01-15")
    assert engine.status_code == 409, engine.text
    assert "Aynı öncelikte" in engine.json()["detail"], engine.text
    assert flagged["detail"] == engine.json()["detail"], (flagged, engine.json())

    # Önceliklerden biri ayrılınca hem motor hem ekran temizlenir.
    assert client.put(
        "/api/harvest-scheduling/rules/%d" % tohum_rule["id"],
        headers=muhasebe,
        json={
            "season_code": "PANCAR_SOKUMU", "customer_id": ciftci,
            "product_category": "Tohum", "priority": 9, "active": True,
        },
    ).status_code == 200
    assert expected_pair not in conflict_pairs(), conflicts()
    assert preview(
        client, muhasebe, ciftci, [pancar], "2026-01-15"
    ).status_code == 200

    for stale in (catch_all["id"], tohum_rule["id"]):
        assert client.delete(
            "/api/harvest-scheduling/rules/%d" % stale, headers=muhasebe
        ).status_code == 204

    # (2) Örtüşmeyen geçerlilik aralığı çakışma DEĞİLDİR.
    aralik = customer(client, muhasebe, "Aralık Çiftçisi")

    def windowed(from_date, to_date, priority=3):
        response = client.post(
            "/api/harvest-scheduling/rules",
            headers=muhasebe,
            json={
                "season_code": "PANCAR_SOKUMU", "customer_id": aralik,
                "priority": priority, "active": True,
                "effective_from": from_date, "effective_to": to_date,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    first_half = windowed("2026-01-01", "2026-06-30")
    second_half = windowed("2026-07-01", "2026-12-31")
    disjoint_pair = tuple(sorted((first_half["id"], second_half["id"])))
    assert first_half["scope"] == 50 and second_half["scope"] == 50
    assert disjoint_pair not in conflict_pairs(), conflicts()

    # Aynı çift örtüşür hale gelince çakışma olarak işaretlenir: tarih mantığı
    # gerçekten çalışıyor, her şeye "çakışma yok" demiyor.
    assert client.put(
        "/api/harvest-scheduling/rules/%d" % second_half["id"],
        headers=muhasebe,
        json={
            "season_code": "PANCAR_SOKUMU", "customer_id": aralik, "priority": 3,
            "active": True, "effective_from": "2026-06-01",
            "effective_to": "2026-12-31",
        },
    ).status_code == 200
    assert disjoint_pair in conflict_pairs(), conflicts()

    # Pasifleştirmek de çakışmayı kaldırır.
    assert client.put(
        "/api/harvest-scheduling/rules/%d" % second_half["id"],
        headers=muhasebe,
        json={
            "season_code": "PANCAR_SOKUMU", "customer_id": aralik, "priority": 3,
            "active": False, "effective_from": "2026-06-01",
            "effective_to": "2026-12-31",
        },
    ).status_code == 200
    assert disjoint_pair not in conflict_pairs(), conflicts()

    for stale in (first_half["id"], second_half["id"]):
        assert client.delete(
            "/api/harvest-scheduling/rules/%d" % stale, headers=muhasebe
        ).status_code == 204
    assert conflicts() == [], conflicts()

    # ---- Geçici kazanan: çakışma dönem ORTASINDA doğuyor ------------------
    # A (kategorisiz, pri 7, tüm yıl) + B (Tohum, pri 7, tüm yıl) çifti,
    # C (kategorisiz, pri 9, 01.01-30.06) yüzünden yılın ilk yarısında
    # maskeleniyor. Tek örnek tarihe (ortak dönem başlangıcı) bakan analiz
    # sessiz kalıyordu; motor ise 01.07'de 409 veriyor.
    gecici = customer(client, muhasebe, "Geçici Kazanan Çiftçisi")

    def yearly(category=None, priority=7, to_date="2026-12-31"):
        payload = {
            "season_code": "PANCAR_SOKUMU", "customer_id": gecici,
            "priority": priority, "active": True,
            "effective_from": "2026-01-01", "effective_to": to_date,
        }
        if category is not None:
            payload["product_category"] = category
        response = client.post(
            "/api/harvest-scheduling/rules", headers=muhasebe, json=payload
        )
        assert response.status_code == 201, response.text
        return response.json()

    rule_a = yearly()
    rule_b = yearly(category="Tohum")
    rule_c = yearly(priority=9, to_date="2026-06-30")
    ab_pair = tuple(sorted((rule_a["id"], rule_b["id"])))

    # (1) Çakışma RAPORLANIR.
    assert ab_pair in conflict_pairs(), conflicts()
    sliced = [row for row in conflicts() if tuple(row["rule_ids"]) == ab_pair][0]

    # (4) Dilimleme sınırı: raporlanan örnek tarih, C'nin bittiği günün
    # ertesidir (effective_to + 1). Yalnız dönem başlangıcı test edilseydi
    # bu satır hiç oluşmazdı.
    assert sliced["sample_date"] == "2026-07-01", sliced

    # Motorla tutarlılık: C canlıyken çözülüyor, C bitince 409.
    assert preview(
        client, muhasebe, gecici, [pancar], "2026-03-01"
    ).status_code == 200
    late = preview(client, muhasebe, gecici, [pancar], "2026-07-01")
    assert late.status_code == 409, late.text
    assert sliced["detail"] == late.json()["detail"], (sliced, late.json())

    # (2) Fazla-uyarı regresyonu: C tüm yılı kaplayan KALICI kazanan olunca
    # hiçbir dilimde 409 yok → çakışma raporlanmamalı, preview de temiz.
    assert client.put(
        "/api/harvest-scheduling/rules/%d" % rule_c["id"],
        headers=muhasebe,
        json={
            "season_code": "PANCAR_SOKUMU", "customer_id": gecici, "priority": 9,
            "active": True, "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    ).status_code == 200
    assert ab_pair not in conflict_pairs(), conflicts()
    # Takvim vadesi 30.11.2026, o tarihten sonrası zaten "aktif takvim yok".
    for moment in ("2026-01-01", "2026-07-01", "2026-11-30"):
        clean = preview(client, muhasebe, gecici, [pancar], moment)
        assert clean.status_code == 200, (moment, clean.text)

    for stale in (rule_a["id"], rule_b["id"], rule_c["id"]):
        assert client.delete(
            "/api/harvest-scheduling/rules/%d" % stale, headers=muhasebe
        ).status_code == 204
    assert conflicts() == [], conflicts()

    # ---- Sınır: effective_to = 9999-12-31 taşma üretmez --------------------
    # Şema bu tarihi kabul ediyor. Dilim sınırı "effective_to + 1 gün" olduğu
    # için date.max üzerinde Python OverflowError doğuyor ve analiz 500'e
    # düşüyordu. 9999-12-31 fiilen SÜRESİZ demek: sınır noktası üretmez -- ama
    # kuralın kendisi analizden düşmez.
    sonsuz = customer(client, muhasebe, "Sonsuz Kural Çiftçisi")

    def endless(category=None, priority=7, to_date=None, customer_id=None):
        payload = {
            "season_code": "PANCAR_SOKUMU",
            "customer_id": sonsuz if customer_id is None else customer_id,
            "priority": priority, "active": True,
            "effective_from": "2026-01-01",
        }
        if category is not None:
            payload["product_category"] = category
        if to_date is not None:
            payload["effective_to"] = to_date
        response = client.post(
            "/api/harvest-scheduling/rules", headers=muhasebe, json=payload
        )
        assert response.status_code == 201, response.text
        return response.json()

    # (1) Tek başına duran date.max kuralı analizi düşürmez.
    forever = endless(to_date="9999-12-31")
    assert forever["effective_to"] == "9999-12-31", forever
    assert conflicts() == [], conflicts()

    # (2) Aynı kural çakışmanın TARAFI iken çakışma normal raporlanır: taşma
    # susturulurken 9999 kuralı görünmez olmuyor.
    forever_tohum = endless(category="Tohum")
    forever_pair = tuple(sorted((forever["id"], forever_tohum["id"])))
    assert forever_pair in conflict_pairs(), conflicts()
    forever_row = [
        row for row in conflicts() if tuple(row["rule_ids"]) == forever_pair
    ][0]
    assert forever_row["sample_date"] == "2026-01-01", forever_row

    # Motorla tutarlılık: aynı bağlamda gerçek satış da 409 alıyor.
    engine_forever = preview(client, muhasebe, sonsuz, [pancar], "2026-06-01")
    assert engine_forever.status_code == 409, engine_forever.text
    assert forever_row["detail"] == engine_forever.json()["detail"], (
        forever_row, engine_forever.json()
    )

    # (3) Kural çakışmanın tarafı DEĞİLKEN de güvenli: sınır noktaları her
    # aktif kuraldan toplandığı için başka bir müşterinin çifti dilimlenirken
    # de taşma tetikleniyordu.
    seyirci = customer(client, muhasebe, "Seyirci Çiftçisi")
    bystander = endless(to_date="9999-12-31", customer_id=seyirci, priority=3)
    assert forever_pair in conflict_pairs(), conflicts()

    for stale in (forever["id"], forever_tohum["id"], bystander["id"]):
        assert client.delete(
            "/api/harvest-scheduling/rules/%d" % stale, headers=muhasebe
        ).status_code == 204
    assert conflicts() == [], conflicts()

    # ---- Tenant izolasyonu ------------------------------------------------
    other = client.post(
        "/api/companies", headers=admin, json={"name": "İkinci Harman Firması"}
    )
    assert other.status_code in (200, 201), other.text
    other_id = other.json()["id"]
    other_admin = dict(admin, **{"X-Company-ID": str(other_id)})

    # B firması A'nın kayıtlarını listede görmez.
    assert client.get(
        "/api/harvest-scheduling/regions", headers=other_admin
    ).json() == []
    assert client.get(
        "/api/harvest-scheduling/calendars", headers=other_admin
    ).json() == []
    assert client.get(
        "/api/harvest-scheduling/rules", headers=other_admin
    ).json() == []

    # B firması A'nın kayıtlarını güncelleyemez/silemez (404, 403 değil: yoklar).
    assert client.put(
        "/api/harvest-scheduling/regions/%d" % merkez,
        headers=other_admin,
        json={"code": "CALINDI", "name": "Çalındı", "active": True},
    ).status_code == 404
    assert client.delete(
        "/api/harvest-scheduling/regions/%d" % merkez, headers=other_admin
    ).status_code == 404
    assert client.put(
        "/api/harvest-scheduling/calendars/%d" % pancar_takvim,
        headers=other_admin,
        json={
            "season_code": "CALINDI",
            "name": "Çalındı",
            "season_year": 2026,
            "due_date": "2026-11-30",
            "active": True,
        },
    ).status_code == 404
    assert client.delete(
        "/api/harvest-scheduling/calendars/%d" % pancar_takvim, headers=other_admin
    ).status_code == 404

    # B firması A'nın müşterisi için önizleme çalıştıramaz.
    leaked = preview(client, other_admin, ciftci, [pancar], "2026-01-15")
    assert leaked.status_code == 404, leaked.text

    # A'nın kayıtları dokunulmadan duruyor.
    still = client.get("/api/harvest-scheduling/regions", headers=muhasebe).json()
    assert [row for row in still if row["id"] == merkez][0]["code"] == "MERKEZ", still
    kept = client.get("/api/harvest-scheduling/calendars", headers=muhasebe).json()
    assert [
        row for row in kept if row["id"] == pancar_takvim
    ][0]["season_code"] == "PANCAR_SOKUMU", kept

    # ---- Bilinmeyen kayıt yönetimi ---------------------------------------
    assert client.put(
        "/api/harvest-scheduling/rules/999999",
        headers=muhasebe,
        json={"season_code": "YOK", "priority": 0, "active": True},
    ).status_code == 404
    assert client.delete(
        "/api/harvest-scheduling/calendars/999999", headers=muhasebe
    ).status_code == 404
    assert preview(
        client, muhasebe, 999999, [pancar], "2026-01-15"
    ).status_code == 404
    assert preview(
        client, muhasebe, ciftci, [999999], "2026-01-15"
    ).status_code == 404
    # Ürünsüz önizleme istek doğrulamasında düşer.
    assert preview(
        client, muhasebe, ciftci, [], "2026-01-15"
    ).status_code == 422

print("harvest season admin smoke ok")
'''
