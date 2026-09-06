"""Kullanıcı Aktivite Paneli (activity_logs) — v1 sözleşmesi.

Kanıtlanan invariantlar:

* **Tenant izolasyonu** — B firması A'nın aktivite kayıtlarını hiçbir filtreyle
  göremez; tekil okumada 404 alır.
* **RBAC** — panel yalnız admin + yönetici; satış/muhasebe/rapor 403. Arşivleme
  ve arşivlenmişleri görme yalnız admin.
* **Değişmezlik** — çekirdek alanları değiştiren bir uç YOKTUR: PUT/PATCH/DELETE
  405/404 ile karşılanır ve satır diskte değişmeden kalır.
* **Arşiv akışı** — gerekçe zorunlu, yalnız arşiv üçlüsü değişir, arşivlemenin
  kendisi de ayrı bir aktivite satırı üretir.
* **Aynı transaction** — iki yönlü: işlem rollback olursa log satırı yazılmaz;
  log yazılamazsa işlem de düşer.
* **Gerçek emisyon** — satış iptali, ödeme silme ve toplu fiyat güncelleme
  uçlarından gerçek HTTP ile.
* **Sayfalama / filtre** — limit + offset, kullanıcı / tip / tarih filtreleri.
"""

from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from app.activity_log import ACTION_TYPES, RESOURCE_TYPES, format_money_tr
from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def run_activity_log_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    # Türkçe özet ve hata metinleri üzerinde assert edildiği için çocuk süreç
    # konsol kod sayfasından bağımsız olarak UTF-8 yazmalıdır.
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
    )
    assert completed.returncode == 0, (completed.stdout or "") + (completed.stderr or "")


# ---------------------------------------------------------------------------
# Saf birim doğrulamaları (süreç açmadan)
# ---------------------------------------------------------------------------
def test_panel_requires_user_management_permission() -> None:
    """Panel okuması baseline ``read`` rolüne DÜŞMEZ: users yetkisi ister."""
    assert required_permission("GET", "/api/activity-logs") == "users"
    assert required_permission("GET", "/api/activity-logs/catalog") == "users"
    assert required_permission("POST", "/api/activity-logs/1/archive") == "users"
    assert required_permission("POST", "/api/activity-logs/1/unarchive") == "users"


def test_money_summary_is_decimal_and_turkish() -> None:
    """Özetteki tutar Decimal'den üretilir; binlik/ondalık ayracı Türkçedir."""
    assert format_money_tr(Decimal("4500")) == "4.500,00 TL"
    assert format_money_tr(Decimal("1234567.891")) == "1.234.567,89 TL"
    assert format_money_tr("0") == "0,00 TL"
    assert format_money_tr(Decimal("-12.5")) == "-12,50 TL"
    assert format_money_tr(Decimal("99"), "USD") == "99,00 USD"


def test_v1_catalog_is_closed_and_labelled() -> None:
    """v1 kataloğu kapalı bir kümedir ve her tipin Türkçe etiketi vardır."""
    expected = {
        "sale.create", "sale.update", "sale.cancel",
        "pos.sale_created", "pos.sale_cancelled",
        "payment.create", "payment.update", "payment.delete",
        "return.create", "product.bulk_price_update",
        # Taban birim yazma yolu (kantar fişi v2, sütun göç 20260902_0066):
        # PUT /api/products/{id} `base_unit` değişince eski/yeni değerle
        # kaydeder. Bu kapı `backend/` altında durduğu için `tests/` taraması
        # onu görmedi; CI koşusu 33956592477 kırmızı ile buldu.
        "product.base_unit_update",
        "stock.adjust", "stock.transfer",
        "work_order.create", "work_order.status_change", "work_order.cancel",
        "work_order.receivable.reverse",
        "invoice.create", "invoice.cancel",
        "late_fee.draft", "late_fee.post", "late_fee.reversal",
        "allocation.manual", "allocation.reversal", "allocation.reallocation",
        "user.create", "user.role_change", "user.status_change",
        "activity_log.archive", "activity_log.unarchive",
        "company.exported",
        "company.erased",
        "backup.created", "backup.downloaded",
        "backup.restore_started", "backup.restore_completed", "backup.restore_failed",
        "backup.restore_rollback_started", "backup.restore_rollback_completed",
        "backup.restore_rollback_failed",
        "backup.maintenance_recovered",
        "backup.maintenance_legacy_normalized",
        "backup.maintenance_force_cleared",
        # Bildirimler FAZ-1 (tasarım §6): gönderim kadar onay, iptal, izin
        # değişikliği ve kural durdurma da denetim kaydına girer.
        "notification.queued", "notification.approved",
        "notification.cancelled", "notification.sent", "notification.failed",
        "notification.consent_granted", "notification.consent_revoked",
        "notification.template_created", "notification.template_updated",
        "notification.rule_enabled", "notification.rule_disabled",
        "notification.rows_disarmed", "notification.compliance_unavailable",
        "supplier_price.import_created", "supplier_price.import_applied",
        "supplier_price.import_reverted", "supplier_price.block_overridden",
        "supplier_price.xref_created",
        # Outbox yeniden kuyruklama (FIELD_STOK_OUTBOX açılış koşulu 3).
        # `field_integration_events` tablosunda `requeued_by`/`requeued_at`
        # SÜTUNU YOK ve o dilim göç EKLEMEDİ; kimin hangi olayı hangi
        # terminal durumdan geri aldığı YALNIZ bu satırda durur.
        "field_event.requeued",
        # HAYVANCILIĞIN KATALOĞA GİREN İLK OLAYLARI (göç 20260908_0074).
        # Modül 0049'dan beri HİÇ aktivite kaydı yazmıyordu ve o boşluk
        # buraya kadar zararsızdı: hayvan/sürü/sağım kayıtları kendi
        # tablolarında `updated_at` ile izleniyordu. Arınma süresi FARKLI —
        # katalog satırı bir SAYIYI TANIMLAR ve 28 günlük et arınmasını 2
        # güne çeken bir düzenleme o andan sonraki bütün tedavileri sessizce
        # serbest bırakır; satırın kendi `updated_at`i yalnız "değişti" der,
        # "neydi" demez. Üçüncüsü ise kilidin GEREKÇEYLE geçilmesidir ve
        # ayrı bir ad ŞART: gerekçe metni `milk_yields` /
        # `animal_movements` satırında durur ama O SATIR KİMİN YAZDIĞINI
        # TAŞIMAZ — iki tabloda da kullanıcı sütunu YOKTUR (0049; ölçüldü),
        # yani "bu sütü kim tanka yazdırdı" sorusunun cevabı YALNIZ burada.
        "vet_drug.create", "vet_drug.update",
        "herd_withdrawal.overridden",
    }
    assert set(ACTION_TYPES) == expected
    # 58 -> 59: product.base_unit_update (kantar fişi v2).
    # 59 -> 60: KIRACI DISA AKTARIMI (`company.exported`). Ayri bir eylem adi
    # gerekli cunku `backup.*` PLATFORM yedegidir (kumenin tamami); bu ise TEK
    # firmanin verisini o firmaya teslim eder. Ayni ad altinda toplansalardi
    # panelde birbirinden ayirt edilemezlerdi.
    # 60 -> 61: YENİDEN KUYRUKLAMA (`field_event.requeued`). Ayrı bir eylem
    # adı ŞART: uç bir olayın TERMİNAL kararını geri alıyor ve bunun izi
    # başka hiçbir yerde yok — `activity_logs` bu dilimde `requeued_by`
    # sütununun YERİNE geçiyor, üstelik append-only olduğu için ondan daha
    # güçlü bir iz olarak.
    # 61 -> 62: KIRACI YUMUSAK IMHASI (`company.erased`). `company.exported`
    # ile ayni aileden ama AYRI bir eylem: biri veriyi TESLIM eder, digeri
    # kiraciyi KAPATIR. Ayni ad altinda toplansalardi denetim izinde
    # "verisini indirdi" ile "firmayi kapatti" ayirt edilemezdi. KAYNAK TIPI
    # dis aktarimla AYNI ("backup"): RESOURCE_TYPES 18'de SABIT, yeni kaynak
    # tipi ACILMADI.
    # 62 -> 65: E2 ARINMA (göç 20260908_0074). ÜÇ yeni eylem ve gerekçeleri
    # yukarıda; İKİ YENİ KAYNAK TİPİ de açıldı ve bu 0058'in "kaynak tipi
    # AÇILMADI" kararının TERSİ değil, aynı ölçütün öteki yanıdır: orada
    # kiracı imhasının kaynağı zaten `backup` ailesindendi, burada ise
    # `vet_drugs.id` ve gerekçeyi taşıyan SATIRIN kimliği panelde
    # bağlanabilecek başka hiçbir mevcut tipe karşılık gelmiyor.
    assert len(ACTION_TYPES) == 65, sorted(ACTION_TYPES)
    assert all(ACTION_TYPES.values()), ACTION_TYPES
    assert "activity_log" in RESOURCE_TYPES
    # POS fişi de bir ``orders`` satırıdır: ayrı bir kaynak tipi eklenmez,
    # böylece paneldeki kaynak linki POS satırlarında da çalışır.
    assert "pos_sale" not in RESOURCE_TYPES
    # Outbox olayı kendi kaynak tipidir: kaynak KİMLİĞİ olay satırının id'si,
    # bağlantısı da okuma yüzeyi (`GET /api/field-integration-events`).
    assert "field_integration_event" in RESOURCE_TYPES
    # Veteriner ilaç kataloğu satırı ve arınma kilidinin gerekçeli geçişi
    # (göç 20260908_0074). İkincisinin kaynak KİMLİĞİ `milk_yields.id` YA DA
    # `animal_movements.id`dir ve hangisi olduğu `details`ta `kaynak` ile
    # yazılı; İKİ AYRI tip açmak aynı kararı panelde ikiye bölerdi.
    assert {"vet_drug", "herd_withdrawal"} <= RESOURCE_TYPES


def test_log_activity_rejects_unknown_action_and_resource() -> None:
    """Katalog dışı tip sessizce yazılmaz; helper ValueError yükseltir."""
    import app.activity_log as module

    with pytest.raises(ValueError):
        module.log_activity(None, 1, 1, "sale.teleport", "sale", 1, "x")
    with pytest.raises(ValueError):
        module.log_activity(None, 1, 1, "sale.create", "spaceship", 1, "x")


def test_trigger_column_list_is_derived_from_schema() -> None:
    """Korunan kolon listesi elle yazılmaz; tablo tanımından türer.

    Tabloya sonradan eklenen bir kolon, listeye elle eklenmediği için sessizce
    korumasız kalamaz: ``core_columns()`` her zaman "tüm kolonlar − arşiv
    üçlüsü"dür.
    """
    import importlib.util

    path = BACKEND / "alembic" / "versions" / "20260727_0030_activity_log_panel.py"
    spec = importlib.util.spec_from_file_location("activity_log_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    all_columns = [column.name for column in migration._columns()]
    assert set(migration.ARCHIVE_COLUMNS) < set(all_columns), all_columns
    assert migration.core_columns() == [
        name for name in all_columns if name not in migration.ARCHIVE_COLUMNS
    ]
    # Çekirdek kolonların hiçbiri unutulmamalı.
    assert set(migration.core_columns()) == {
        "id", "company_id", "user_id", "action_type", "resource_type",
        "resource_id", "summary", "details", "correlation_id", "created_at",
    }


def test_activity_log_sql_never_interpolates_values() -> None:
    """Tenant filtresi literal+doğrulamalıdır; SQL'e hiçbir değer gömülmez.

    Modülde f-string yalnız *sabit* SQL parçaları (kolon listesi ve bu modülde
    üretilen koşul metni) için kullanılır. Kullanıcıdan gelen her değer bağlı
    parametredir; bu test izinli yer tutucu kümesini kilitler.
    """
    source = (BACKEND / "app" / "activity_log.py").read_text(encoding="utf-8")
    assert source.count("company_id = :cid") >= 3, source
    assert source.count("tenant_text(") >= 2, source
    sql_keywords = ("SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE", "COUNT(")
    sql_placeholders: set[str] = set()
    for line in source.splitlines():
        if any(keyword in line for keyword in sql_keywords):
            sql_placeholders |= set(re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)\}", line))
    assert sql_placeholders <= {"optional_where", "_SELECT_COLUMNS"}, sql_placeholders


# ---------------------------------------------------------------------------
# Uçtan uca (SQLite + PostgreSQL ikizi)
# ---------------------------------------------------------------------------
def test_activity_log_panel_sqlite(tmp_path: Path) -> None:
    run_activity_log_smoke(f"sqlite:///{(tmp_path / 'activity-log.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def login(client, username, password, new_password=None, company_id=None):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(company_id or body["companies"][0]["id"]),
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
    initial = "ActivityPanelUser123!"
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
    return login(client, username, initial, "ActivityPanelUser456!")


def logs(client, headers, **params):
    return client.get("/api/activity-logs", headers=headers, params=params)


def ok_logs(client, headers, **params):
    response = logs(client, headers, **params)
    assert response.status_code == 200, response.text
    return response.json()


with TestClient(app) as client:
    admin = login(client, "admin", "admin123", "ActivityPanelBoss123!")
    company_a = int(admin["X-Company-ID"])

    yonetici = create_user(client, admin, "akt_yonetici", "yonetici")
    satis = create_user(client, admin, "akt_satis", "satis")
    muhasebe = create_user(client, admin, "akt_muhasebe", "muhasebe")
    rapor = create_user(client, admin, "akt_rapor", "rapor")

    # ================= RBAC =================================================
    # Satış / muhasebe / rapor rolleri paneli hiçbir uçtan göremez.
    for denied, label in ((satis, "satis"), (muhasebe, "muhasebe"), (rapor, "rapor")):
        assert logs(client, denied).status_code == 403, label
        assert client.get(
            "/api/activity-logs/catalog", headers=denied
        ).status_code == 403, label
        assert client.get(
            "/api/activity-logs/1", headers=denied
        ).status_code == 403, label
        assert client.post(
            "/api/activity-logs/1/archive", headers=denied, json={"reason": "denemedir"}
        ).status_code == 403, label
        assert client.post(
            "/api/activity-logs/1/unarchive", headers=denied
        ).status_code == 403, label

    admin_user_id = [
        user["id"]
        for user in client.get("/api/users", headers=admin).json()
        if user["username"] == "admin"
    ][0]

    # Admin ve yönetici görebilir.
    assert logs(client, admin).status_code == 200
    assert logs(client, yonetici).status_code == 200
    assert client.get("/api/activity-logs/catalog", headers=yonetici).status_code == 200

    # Kullanıcı ekleme olayları zaten kaydedilmiş olmalı.
    seeded = ok_logs(client, admin, action_type="user.create", limit=100)
    assert seeded["total"] == 4, seeded
    assert all(row["resource_type"] == "user" for row in seeded["items"]), seeded
    assert any("akt_satis" in row["summary"] for row in seeded["items"]), seeded

    # ================= Kurgu: müşteri, ürün, satış =========================
    customer = client.post(
        "/api/customers", headers=admin, json={"name": "Ahmet Yılmaz"}
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["id"]

    product = client.post(
        "/api/products",
        headers=admin,
        json={"name": "Gübre 25kg", "sale_price": "1500", "stock": "100", "vat_rate": 20},
    )
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]

    def create_sale(quantity="3", price="1500", status="completed"):
        response = client.post(
            "/api/orders",
            headers=admin,
            json={
                "entity_id": customer_id,
                "transaction_date": "2026-07-20",
                "status": status,
                "payment_method": "credit",
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "unit_price": price,
                        "vat_rate": 20,
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    sale = create_sale()

    # ---- OLAY 1: satış oluşturma özeti panelde okunabilir --------------
    created = ok_logs(client, admin, action_type="sale.create")
    assert created["total"] == 1, created
    row = created["items"][0]
    assert row["resource_type"] == "sale" and row["resource_id"] == sale["id"], row
    assert row["summary"].startswith(sale["document_no"]), row
    assert "satışını oluşturdu" in row["summary"], row
    assert "4.500,00 TL" in row["summary"], row
    assert "Ahmet Yılmaz" in row["summary"], row
    assert row["correlation_id"], row
    assert row["is_archived"] is False, row
    assert row["action_label"] == "Satış oluşturma", row

    # ---- OLAY 2 (gerçek HTTP): SATIŞ İPTALİ ---------------------------
    deleted = client.delete("/api/orders/%d" % sale["id"], headers=admin)
    assert deleted.status_code == 204, deleted.text
    cancelled = ok_logs(client, admin, action_type="sale.cancel")
    assert cancelled["total"] == 1, cancelled
    cancel_row = cancelled["items"][0]
    assert "satışını iptal etti" in cancel_row["summary"], cancel_row
    assert "4.500,00 TL" in cancel_row["summary"], cancel_row
    assert "Ahmet Yılmaz" in cancel_row["summary"], cancel_row
    assert cancel_row["details"]["cancelled"]["document_no"] == sale["document_no"]

    # ---- OLAY 3 (gerçek HTTP): ÖDEME SİLME ----------------------------
    payment = client.post(
        "/api/payments",
        headers={**admin, "Idempotency-Key": "activity-payment-1"},
        json={
            "entity_type": "customer",
            "entity_id": customer_id,
            "amount": "250.50",
            "payment_date": "2026-07-21",
            "payment_method": "cash",
        },
    )
    assert payment.status_code == 201, payment.text
    payment_id = payment.json()["id"]
    payment_created = ok_logs(client, admin, action_type="payment.create")
    assert payment_created["total"] == 1, payment_created
    assert "250,50 TL" in payment_created["items"][0]["summary"], payment_created

    removed = client.delete("/api/payments/%d" % payment_id, headers=admin)
    assert removed.status_code == 204, removed.text
    payment_deleted = ok_logs(client, admin, action_type="payment.delete")
    assert payment_deleted["total"] == 1, payment_deleted
    delete_row = payment_deleted["items"][0]
    assert "sildi" in delete_row["summary"], delete_row
    assert "250,50 TL" in delete_row["summary"], delete_row
    assert delete_row["details"]["deleted"]["entity_type"] == "customer", delete_row

    # ---- OLAY 4 (gerçek HTTP): TOPLU FİYAT GÜNCELLEME -----------------
    bulk = client.post(
        "/api/products/bulk-price",
        headers=admin,
        json={
            "field": "sale_price",
            "method": "percent",
            "value": "10",
            "product_ids": [product_id],
        },
    )
    assert bulk.status_code == 200, bulk.text
    bulk_logs = ok_logs(client, admin, action_type="product.bulk_price_update")
    assert bulk_logs["total"] == 1, bulk_logs
    bulk_row = bulk_logs["items"][0]
    assert bulk_row["resource_id"] is None, bulk_row
    assert "1 ürünün satış fiyatı alanını toplu güncelledi" in bulk_row["summary"], bulk_row
    assert bulk_row["details"]["updated_count"] == 1, bulk_row
    assert bulk_row["details"]["truncated"] is False, bulk_row
    assert bulk_row["details"]["products"][0]["id"] == product_id, bulk_row

    # ---- OLAY 5 (gerçek HTTP): POS SATIŞI -----------------------------
    # Dükkândaki satışın büyük kısmı dokunmatik POS'tan geçer; panel bu akışa
    # kör kalmamalıdır. POS ucu `_save`'i request'siz çağırdığı için `sale.create`
    # burada tetiklenmez — POS'un kendi olay tipi yazılır (çift kayıt yok).
    def pos_sale(key, quantity="2", payment_type="cash", product=None, headers=None):
        return client.post(
            "/api/pos/sale",
            headers={**(headers or admin), "Idempotency-Key": key},
            json={
                "payment_type": payment_type,
                "items": [
                    {
                        "product_id": product or product_id,
                        "quantity": quantity,
                        "unit_price": "625.00",
                        "discount_percent": "0",
                    }
                ],
            },
        )

    sold = pos_sale("aktivite-pos-1")
    assert sold.status_code == 201, sold.text
    pos_sale_id = sold.json()["sale_id"]

    pos_created = ok_logs(client, admin, action_type="pos.sale_created")
    assert pos_created["total"] == 1, pos_created
    pos_row = pos_created["items"][0]
    assert pos_row["resource_type"] == "sale", pos_row
    assert pos_row["resource_id"] == pos_sale_id, pos_row
    assert pos_row["user_id"] == admin_user_id, pos_row
    assert pos_row["action_label"] == "POS satışı", pos_row
    assert "POS satışı yaptı" in pos_row["summary"], pos_row
    assert "1.250,00 TL" in pos_row["summary"], pos_row
    assert "1 kalem" in pos_row["summary"], pos_row
    # details: belge no + kalem sayısı + tutar (Decimal metni, float değil)
    assert pos_row["details"]["item_count"] == 1, pos_row
    assert pos_row["details"]["final_total"] == "1250.00", pos_row
    assert pos_row["details"]["payment_type"] == "cash", pos_row
    assert pos_row["details"]["document_no"], pos_row
    pos_document_no = pos_row["details"]["document_no"]
    assert pos_document_no in pos_row["summary"], pos_row

    # Idempotent tekrar oynatma YENİ BİR OLAY DEĞİLDİR: ikinci kayıt yazılmaz.
    replayed = pos_sale("aktivite-pos-1")
    assert replayed.status_code in (200, 201), replayed.text
    assert replayed.json()["sale_id"] == pos_sale_id, replayed.text
    assert ok_logs(client, admin, action_type="pos.sale_created")["total"] == 1

    # BAŞARISIZ POS isteği (4xx) hiçbir kayıt üretmez.
    before_pos_total = ok_logs(client, admin, limit=100)["total"]
    missing_product = pos_sale("aktivite-pos-yok", product=999999)
    assert missing_product.status_code == 404, missing_product.text
    no_key = client.post(
        "/api/pos/sale",
        headers=admin,
        json={"payment_type": "cash",
              "items": [{"product_id": product_id, "quantity": "1",
                         "unit_price": "10", "discount_percent": "0"}]},
    )
    assert no_key.status_code == 400, no_key.text
    # Stok yetmeyen POS satışı: işlem rollback olur, log satırı YAZILMAZ.
    oversold = pos_sale("aktivite-pos-stok", quantity="99999")
    assert oversold.status_code == 409, oversold.text
    assert ok_logs(client, admin, limit=100)["total"] == before_pos_total
    assert ok_logs(client, admin, action_type="pos.sale_created")["total"] == 1

    # ---- OLAY 6 (gerçek HTTP): POS SATIŞI İPTALİ ----------------------
    # POS'un ayrı bir iptal ucu yoktur; iptal /api/orders üzerinden geçer ama
    # kaynağı pos_idempotency'den ayırt edilip POS olayı olarak yazılır.
    pos_cancelled = client.delete("/api/orders/%d" % pos_sale_id, headers=admin)
    assert pos_cancelled.status_code == 204, pos_cancelled.text
    pos_cancel_logs = ok_logs(client, admin, action_type="pos.sale_cancelled")
    assert pos_cancel_logs["total"] == 1, pos_cancel_logs
    pos_cancel_row = pos_cancel_logs["items"][0]
    assert pos_cancel_row["resource_id"] == pos_sale_id, pos_cancel_row
    assert "POS satışını iptal etti" in pos_cancel_row["summary"], pos_cancel_row
    assert pos_document_no in pos_cancel_row["summary"], pos_cancel_row
    assert "1.250,00 TL" in pos_cancel_row["summary"], pos_cancel_row
    assert pos_cancel_row["details"]["origin"] == "pos", pos_cancel_row
    # POS iptali sıradan satış iptalini ARTIRMAZ: iki olay ayrı filtrelenir.
    assert ok_logs(client, admin, action_type="sale.cancel")["total"] == 1

    # Log yazılamazsa POS satışı da düşer (aynı-tx, ikinci yön).
    import app.routers.pos as pos_module

    original_pos_logger = pos_module.log_request_activity

    def exploding_pos_logger(*args, **kwargs):
        raise RuntimeError("denetim kaydı yazılamadı")

    pos_module.log_request_activity = exploding_pos_logger
    try:
        # POS ucu gövde-içi geniş bir except taşımadığı için TestClient sunucu
        # istisnasını yeniden fırlatır; asıl iddia HTTP kodu değil, ETKİdir:
        # ne fiş ne de log satırı oluşur.
        try:
            blocked_pos = pos_sale("aktivite-pos-patlasin")
            assert blocked_pos.status_code >= 400, blocked_pos.text
        except RuntimeError:
            pass
    finally:
        pos_module.log_request_activity = original_pos_logger
    assert ok_logs(client, admin, action_type="pos.sale_created")["total"] == 1
    with SessionLocal() as probe:
        assert probe.execute(
            text(
                "SELECT COUNT(*) FROM pos_idempotency "
                "WHERE company_id=:cid AND idempotency_key='aktivite-pos-patlasin'"
            ),
            {"cid": company_a},
        ).scalar_one() == 0, "log yazılamadığı hâlde POS fişi kaydedilmiş"

    # ================= AYNI TRANSACTION — yön 1 ============================
    # İşlem rollback olursa log satırı da yazılmaz. Log yazımı çağıranın kendi
    # Session'ında olur ve commit etmez; rollback her ikisini birden siler.
    with SessionLocal() as probe:
        from app.activity_log import log_activity

        before_total = probe.execute(
            text("SELECT COUNT(*) FROM activity_logs WHERE company_id=:cid"),
            {"cid": company_a},
        ).scalar_one()
        log_id = log_activity(
            probe, company_a, 1, "sale.create", "sale", 999999,
            "ROLLBACK OLMALI", {"probe": True},
        )
        assert probe.execute(
            text("SELECT COUNT(*) FROM activity_logs WHERE company_id=:cid AND id=:id"),
            {"cid": company_a, "id": log_id},
        ).scalar_one() == 1
        probe.rollback()
        assert probe.execute(
            text("SELECT COUNT(*) FROM activity_logs WHERE company_id=:cid"),
            {"cid": company_a},
        ).scalar_one() == before_total

    # ================= AYNI TRANSACTION — yön 2 ============================
    # Log yazılamazsa İŞLEM DE DÜŞER: satış kaydı hiç oluşmaz.
    import app.routers.transactions as transactions_module

    original_logger = transactions_module.log_request_activity

    def exploding_logger(*args, **kwargs):
        raise RuntimeError("denetim kaydı yazılamadı")

    transactions_module.log_request_activity = exploding_logger
    try:
        blocked = client.post(
            "/api/orders",
            headers=admin,
            json={
                "entity_id": customer_id,
                "transaction_date": "2026-07-22",
                "document_no": "DENETIM-DUSMELI",
                "status": "completed",
                "payment_method": "credit",
                "items": [
                    {
                        "product_id": product_id,
                        "quantity": "1",
                        "unit_price": "1000",
                        "vat_rate": 20,
                    }
                ],
            },
        )
        assert blocked.status_code == 500, blocked.text
    finally:
        transactions_module.log_request_activity = original_logger

    with SessionLocal() as probe:
        orphan = probe.execute(
            text(
                "SELECT COUNT(*) FROM orders WHERE company_id=:cid "
                "AND document_no='DENETIM-DUSMELI'"
            ),
            {"cid": company_a},
        ).scalar_one()
        assert orphan == 0, "log yazılamadığı hâlde satış kaydedilmiş"

    # ================= DEĞİŞMEZLİK =========================================
    target = ok_logs(client, admin, action_type="sale.cancel")["items"][0]
    target_id = target["id"]
    # Çekirdek alanları değiştiren bir uç yoktur: yöntem bile tanımlı değildir.
    for method, path, payload in (
        ("put", "/api/activity-logs/%d" % target_id, {"summary": "elle değiştirildi"}),
        ("patch", "/api/activity-logs/%d" % target_id, {"summary": "elle değiştirildi"}),
        ("post", "/api/activity-logs/%d" % target_id, {"summary": "elle değiştirildi"}),
    ):
        response = getattr(client, method)(path, headers=admin, json=payload)
        assert response.status_code in (404, 405), (method, path, response.status_code)
    assert client.delete(
        "/api/activity-logs/%d" % target_id, headers=admin
    ).status_code in (404, 405)
    # Koleksiyon ucunda da yazma yolu yok.
    assert client.post(
        "/api/activity-logs", headers=admin, json={"summary": "sahte"}
    ).status_code in (404, 405, 422)
    assert client.delete("/api/activity-logs", headers=admin).status_code in (404, 405)

    unchanged = client.get("/api/activity-logs/%d" % target_id, headers=admin).json()
    assert unchanged["summary"] == target["summary"], unchanged
    assert unchanged["created_at"] == target["created_at"], unchanged

    # ========== DEĞİŞMEZLİK: VERİTABANI SEVİYESİ (doğrudan SQL) ============
    # API disiplini yeterli değildir: yanlış yazılmış bir iç kod yolu, bakım
    # scripti veya elle açılmış bir oturum da çekirdeği değiştirememelidir.
    # Aşağıdaki iddialar uçlardan DEĞİL, ham text() SQL ile motora doğrudan
    # gönderilir; koruma trigger'lardan gelir.
    from sqlalchemy.exc import DatabaseError

    def raw(statement, params=None):
        """Ayrı bir oturumda ham SQL; hata alırsa oturum temiz kapanır."""
        with SessionLocal() as session:
            try:
                result = session.execute(text(statement), params or {})
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def snapshot(log_id):
        with SessionLocal() as session:
            return session.execute(
                text(
                    "SELECT id,user_id,action_type,summary,created_at "
                    "FROM activity_logs WHERE company_id=:cid AND id=:id"
                ),
                {"cid": company_a, "id": log_id},
            ).mappings().first()

    guarded_id = target_id
    before_row = snapshot(guarded_id)
    assert before_row is not None, guarded_id

    # Canlı şemada HİÇBİR çekirdek kolon korumasız kalmamalı: trigger tanımını
    # motordan okuyup tablonun gerçek kolon listesiyle karşılaştırıyoruz.
    with SessionLocal() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            live_columns = [
                row[0]
                for row in session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='activity_logs'"
                    )
                )
            ]
            definition = session.execute(
                text(
                    "SELECT pg_get_functiondef(oid) FROM pg_proc "
                    "WHERE proname='activity_logs_forbid_core_update'"
                )
            ).scalar_one()
        else:
            live_columns = [
                row[1]
                for row in session.execute(text("PRAGMA table_info(activity_logs)"))
            ]
            definition = session.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='trg_activity_logs_core_immutable'"
                )
            ).scalar_one()
    archive_columns = {"archived_at", "archived_by", "archive_reason"}
    expected_guarded = [c for c in live_columns if c not in archive_columns]
    assert expected_guarded, live_columns
    for column in expected_guarded:
        assert column in definition, (column, definition)
    # Arşiv kolonları KORUNMAZ (yoksa arşivleme de imkânsız olurdu).
    guarded_section = definition.split("ON activity_logs")[0]
    for column in archive_columns:
        assert column not in guarded_section, (column, definition)

    # (a) DELETE her koşulda yasak — satır yerinde kalır.
    try:
        raw("DELETE FROM activity_logs WHERE id=:id", {"id": guarded_id})
        raise AssertionError("DELETE trigger'a takılmadı")
    except DatabaseError as exc:
        assert "silinemez" in str(exc), str(exc)
    assert snapshot(guarded_id) is not None, "trigger'a rağmen satır silinmiş"

    # Koşulsuz toplu silme de aynı şekilde reddedilir.
    try:
        raw("DELETE FROM activity_logs")
        raise AssertionError("koşulsuz DELETE trigger'a takılmadı")
    except DatabaseError:
        pass
    assert snapshot(guarded_id) is not None

    # (b) Çekirdek kolon UPDATE'leri yasak — değer değişmemiş kalır.
    for column, value in (
        ("summary", "'elle degistirildi'"),
        ("user_id", "999999"),
        ("action_type", "'sale.create'"),
    ):
        try:
            raw(
                "UPDATE activity_logs SET %s=%s WHERE id=:id" % (column, value),
                {"id": guarded_id},
            )
            raise AssertionError("UPDATE %s trigger'a takılmadı" % column)
        except DatabaseError as exc:
            assert "degistirilemez" in str(exc), (column, str(exc))
        assert snapshot(guarded_id)[column] == before_row[column], column

    # (d) Karışık güncelleme (arşiv üçlüsü + çekirdek kolon) da reddedilir.
    try:
        raw(
            "UPDATE activity_logs SET archive_reason='karisik',"
            "archived_by=1,archived_at=created_at,summary='sizinti' "
            "WHERE id=:id",
            {"id": guarded_id},
        )
        raise AssertionError("karışık UPDATE trigger'a takılmadı")
    except DatabaseError as exc:
        assert "degistirilemez" in str(exc), str(exc)
    after_mixed = snapshot(guarded_id)
    assert after_mixed["summary"] == before_row["summary"], after_mixed
    with SessionLocal() as session:
        assert session.execute(
            text("SELECT archived_at FROM activity_logs WHERE id=:id"),
            {"id": guarded_id},
        ).scalar_one() is None, "karışık UPDATE kısmen uygulanmış"

    # (c) Arşiv üçlüsü tek başına GÜNCELLENEBİLİR — arşiv yolu açık kalmalı.
    raw(
        "UPDATE activity_logs SET archived_at=created_at,archived_by=:uid,"
        "archive_reason='dogrudan sql arsiv' WHERE company_id=:cid AND id=:id",
        {"uid": admin_user_id, "cid": company_a, "id": guarded_id},
    )
    with SessionLocal() as session:
        assert session.execute(
            text("SELECT archive_reason FROM activity_logs WHERE id=:id"),
            {"id": guarded_id},
        ).scalar_one() == "dogrudan sql arsiv"
    # Geri al ki HTTP arşiv akışı temiz zeminde koşsun.
    raw(
        "UPDATE activity_logs SET archived_at=NULL,archived_by=NULL,"
        "archive_reason=NULL WHERE company_id=:cid AND id=:id",
        {"cid": company_a, "id": guarded_id},
    )
    assert snapshot(guarded_id)["summary"] == before_row["summary"]

    # ================= ARŞİV AKIŞI =========================================
    # (e) HTTP arşiv/unarchive uçları trigger'lı şemada sorunsuz çalışır.
    # Gerekçe zorunlu ve kısa gerekçe reddedilir.
    assert client.post(
        "/api/activity-logs/%d/archive" % target_id, headers=admin, json={}
    ).status_code == 422
    assert client.post(
        "/api/activity-logs/%d/archive" % target_id, headers=admin, json={"reason": "kısa"}
    ).status_code == 422

    # Yönetici arşivleyemez — arşiv yalnız admin'dedir.
    assert client.post(
        "/api/activity-logs/%d/archive" % target_id,
        headers=yonetici,
        json={"reason": "yönetici denemesi"},
    ).status_code == 403

    archived = client.post(
        "/api/activity-logs/%d/archive" % target_id,
        headers=admin,
        json={"reason": "mükerrer kayıt temizliği"},
    )
    assert archived.status_code == 200, archived.text
    archived_body = archived.json()
    assert archived_body["is_archived"] is True, archived_body
    assert archived_body["archive_reason"] == "mükerrer kayıt temizliği", archived_body
    # Arşivleme YALNIZ arşiv kolonlarını değiştirir; çekirdek alanlar aynıdır.
    for field in ("summary", "action_type", "resource_type", "resource_id",
                  "user_id", "created_at", "correlation_id"):
        assert archived_body[field] == target[field], (field, archived_body, target)

    # Arşivlenen kayıt varsayılan listede yoktur.
    assert all(
        row["id"] != target_id
        for row in ok_logs(client, admin, limit=100)["items"]
    )
    # Yalnız admin arşivlenenleri açabilir.
    assert logs(client, yonetici, include_archived=True).status_code == 403
    assert client.get(
        "/api/activity-logs/%d" % target_id, headers=yonetici
    ).status_code == 404
    with_archived = ok_logs(client, admin, include_archived=True, limit=100)
    assert any(row["id"] == target_id for row in with_archived["items"]), with_archived

    # Arşivlemenin KENDİSİ de aktivite kaydıdır.
    archive_events = ok_logs(client, admin, action_type="activity_log.archive")
    assert archive_events["total"] == 1, archive_events
    archive_event = archive_events["items"][0]
    assert archive_event["resource_type"] == "activity_log", archive_event
    assert archive_event["resource_id"] == target_id, archive_event
    assert "arşivledi" in archive_event["summary"], archive_event
    assert "mükerrer kayıt temizliği" in archive_event["summary"], archive_event

    # Aynı kaydı ikinci kez arşivlemek 404 (idempotent değil, fail-closed).
    assert client.post(
        "/api/activity-logs/%d/archive" % target_id,
        headers=admin,
        json={"reason": "ikinci kez denemedir"},
    ).status_code == 404

    # Arşivden çıkarma da loglanır.
    restored = client.post("/api/activity-logs/%d/unarchive" % target_id, headers=admin)
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False, restored.text
    assert restored.json()["summary"] == target["summary"], restored.text
    unarchive_events = ok_logs(client, admin, action_type="activity_log.unarchive")
    assert unarchive_events["total"] == 1, unarchive_events
    assert unarchive_events["items"][0]["resource_id"] == target_id
    assert client.post(
        "/api/activity-logs/%d/unarchive" % target_id, headers=admin
    ).status_code == 404

    # ================= FİLTRE / SAYFALAMA ==================================
    everything = ok_logs(client, admin, limit=100)
    total = everything["total"]
    assert total >= 10, everything

    first_page = ok_logs(client, admin, limit=3, offset=0)
    second_page = ok_logs(client, admin, limit=3, offset=3)
    assert len(first_page["items"]) == 3, first_page
    assert first_page["total"] == total == second_page["total"]
    first_ids = [row["id"] for row in first_page["items"]]
    assert first_ids == sorted(first_ids, reverse=True), first_page
    assert not set(first_ids) & {row["id"] for row in second_page["items"]}

    # Sunucu tarafı üst sınır: 100'ün üstü istek reddedilir.
    assert logs(client, admin, limit=101).status_code == 422
    assert logs(client, admin, limit=0).status_code == 422
    assert logs(client, admin, offset=-1).status_code == 422

    # Kullanıcı filtresi
    admin_id = admin_user_id
    by_user = ok_logs(client, admin, user_id=admin_id, limit=100)
    assert by_user["total"] > 0, by_user
    assert all(row["user_id"] == admin_id for row in by_user["items"]), by_user
    assert ok_logs(client, admin, user_id=999999)["total"] == 0

    # Kaynak tipi filtresi
    by_resource = ok_logs(client, admin, resource_type="payment", limit=100)
    assert by_resource["total"] == 2, by_resource
    assert all(row["resource_type"] == "payment" for row in by_resource["items"])

    # Tarih aralığı filtresi — bugünü kapsar, geçmiş bir gün kapsamaz.
    today = ok_logs(client, admin, date_from="2026-01-01", limit=100)
    assert today["total"] == total, today
    assert ok_logs(
        client, admin, date_from="2000-01-01", date_to="2000-01-02"
    )["total"] == 0

    # Bilinmeyen tip/kaynak fail-closed 400.
    assert logs(client, admin, action_type="sale.teleport").status_code == 400
    assert logs(client, admin, resource_type="spaceship").status_code == 400
    assert logs(client, admin, date_from="bugün").status_code == 400

    # ================= TENANT İZOLASYONU ===================================
    company_b = client.post(
        "/api/companies", headers=admin, json={"name": "Aktivite B Firması"}
    )
    assert company_b.status_code == 201, company_b.text
    company_b_id = company_b.json()["id"]
    admin_b = dict(admin)
    admin_b["X-Company-ID"] = str(company_b_id)

    # B firmasında hiçbir aktivite yok; A'nın kayıtları görünmez.
    b_logs = ok_logs(client, admin_b, limit=100, include_archived=True)
    assert b_logs["total"] == 0, b_logs
    assert b_logs["items"] == [], b_logs
    # A'nın tekil kaydı B bağlamında 404.
    assert client.get(
        "/api/activity-logs/%d" % target_id, headers=admin_b
    ).status_code == 404
    # B'den A'nın kaydını arşivlemek de mümkün değildir.
    assert client.post(
        "/api/activity-logs/%d/archive" % target_id,
        headers=admin_b,
        json={"reason": "çapraz kiracı denemesi"},
    ).status_code == 404
    assert client.post(
        "/api/activity-logs/%d/unarchive" % target_id, headers=admin_b
    ).status_code == 404
    # Ve A tarafı hâlâ eksiksiz.
    assert ok_logs(client, admin, limit=100)["total"] == total

    # B firmasında üretilen olay yalnız B'de görünür.
    b_customer = client.post(
        "/api/customers", headers=admin_b, json={"name": "B Firma Müşterisi"}
    )
    assert b_customer.status_code == 201, b_customer.text
    b_product = client.post(
        "/api/products",
        headers=admin_b,
        json={"name": "B Ürünü", "sale_price": "100", "stock": "10", "vat_rate": 20},
    )
    assert b_product.status_code == 201, b_product.text
    b_bulk = client.post(
        "/api/products/bulk-price",
        headers=admin_b,
        json={"field": "sale_price", "method": "set", "value": "120",
              "product_ids": [b_product.json()["id"]]},
    )
    assert b_bulk.status_code == 200, b_bulk.text
    b_after = ok_logs(client, admin_b, limit=100)
    assert b_after["total"] == 1, b_after
    assert b_after["items"][0]["company_id"] == company_b_id, b_after
    assert ok_logs(client, admin, limit=100)["total"] == total

    print("ACTIVITY_LOG_PANEL_OK")
'''
