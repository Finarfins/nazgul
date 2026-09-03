"""Enforcement guard: every raw ``text()`` SQL that touches a tenant-owned table
must be scoped by ``company_id``.

Tenant isolation in this codebase rests on discipline — each hand-written query
repeats its own ``company_id=:cid`` predicate. This static guard turns that
discipline into a check: it parses every ``text("...")`` literal under
``backend/app`` and fails if a statement references a tenant table (one that
carries a ``company_id`` column) without mentioning ``company_id`` anywhere in
the statement.

It is deliberately static (no database, like ``test_public_health_contract``).
Queries whose scoping is built dynamically (an f-string ``WHERE``/column list the
literal scan cannot see) or that are pure schema DDL are listed in ``ALLOWLIST``
with a hand-verified justification; changing any of them re-triggers review.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from app.tenancy import tenant_predicate_is_required

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Tenant-owned tables carry a ``company_id`` column, so every read/write against
# them must be company-scoped. Frozen from the live schema. Regenerate when a
# tenant table is added (run from ``backend/``):
#
#   python -c "import app.main; from sqlalchemy import inspect; from app.db import engine; \
# i=inspect(engine); print(sorted(t for t in i.get_table_names() \
# if 'company_id' in {c['name'] for c in i.get_columns(t)}))"
TENANT_TABLES = frozenset({
    "activity_logs", "branches", "company_exchange_rate_overrides", "customers",
    "delivery_notes", "document_sequences",
    "cost_rates",
    "crop_seasons",
    "entity_change_logs", "entity_contacts", "entity_notes", "entity_tasks",
    # Tarla Yönetimi V1 (mobil-erp#2). Hepsi company_id taşır ve tekillikleri
    # kiracı kapsamındadır; ayrıca kendi aralarındaki ilişkiler BİLEŞİK yabancı
    # anahtarla bağlı, yani çapraz kiracı bağ veritabanı seviyesinde imkânsız.
    # Hayvancılık V1 (mobil-erp#17). Hepsi company_id taşır; kendi
    # aralarındaki ilişkiler BİLEŞİK yabancı anahtarla bağlı.
    "animal_births", "animal_breedings", "animal_groups", "animal_movements",
    "animal_vaccinations", "animal_weights", "animals",
    "herd_integration_events", "milk_yields",
    "farm_operations", "farm_parcels", "farms",
    "field_activities", "field_activity_inputs", "field_harvests",
    "field_integration_events", "field_operations", "field_tasks",
    "finance_accounts", "finance_transactions", "financial_instruments",
    "harvest_calendars", "harvest_due_rules", "harvest_regions", "income_expenses",
    "invoice_audit", "invoice_counters", "invoice_history",
    "invoice_items", "invoices", "machine_hour_readings", "machine_idempotency",
    "machine_ownership_history", "machines",
    "late_fee_policies", "notification_consent_events", "notification_consents",
    "notification_rules", "notification_templates", "notifications",
    "notifications_archive", "orders", "part_supersessions", "payment_allocations",
    "payment_idempotency", "payments", "policy_override_logs", "pos_idempotency",
    "pos_system_customers", "products", "purchase_draft_idempotency", "purchases",
    # BKÜ kataloğu (göç 20260901_0063). PHI gün sayısının firma tarafından
    # doldurulan kaydı; company_id taşır, ürüne BİLEŞİK yabancı anahtarla
    # bağlıdır ve tekilliği (company_id, product_id, crop) kapsamındadır.
    "plant_protection_products",
    # Birim dönüşümü katsayı defteri (göç 20260902_0066). company_id taşır ve
    # ürüne BİLEŞİK yabancı anahtarla bağlıdır (0062'nin kuralı), yani bir
    # kiracının katsayı beyanı BAŞKA kiracının ürününü işaret edemez.
    # EKLEMELİDİR: düzeltme yeni `effective_from` ile YENİ SATIRDIR, `UPDATE`
    # değil; tekillik (company_id, product_id, unit_code, effective_from)
    # kapsamındadır. BU PR'DA OKUYAN/YAZAN YOL YOKTUR — tablo çözücüden önce
    # açıldı, bu yüzden buradaki kayıt şimdilik yalnız kiracı NÖBETÇİSİNE
    # görünürlük sağlar; ilk çağıran geldiğinde kapsam zaten kurulmuş olur.
    "product_unit_factors",
    "quotes", "receivable_charge_documents", "receivable_charge_idempotency",
    "receivable_charge_periods", "receivable_legacy_residuals",
    "returns", "sales_orders", "security_audit_logs", "stock_movements",
    # Belge SATIRLARI da kiracı taşır ve ebeveynlerine BİLEŞİK yabancı
    # anahtarla bağlıdır; çapraz kiracı satır veritabanı seviyesinde imkânsız.
    # Dilim 3: stock_transfer_items. Dilim 2: sales_order_items,
    # delivery_note_items. Kalan dört tablo (quote_items, return_items,
    # order_items, purchase_items) 1. dilimde gelecek.
    "delivery_note_items", "sales_order_items", "stock_transfer_items",
    # 1. dilim (#73, göç 20260812_0058): son dört satır tablosu da kendi
    # company_id'sini taşıyor. Bununla birlikte `tests/test_line_item_sql_gate.py`
    # EMEKLİYE AYRILDI: o kapı "bu tablolarda company_id YOKTUR, koruma yalnız
    # ebeveyn join'inden gelir" varsayımı üzerine kuruluydu ve varsayım artık
    # yanlış. Yerine geçen üç şey, hepsi bu dilimde ölçüldü: (1) tablolar bu
    # kümede olduğu için literal SQL nöbetçisinin kapsamında — sekiz sorgu yeri
    # tek tek mutasyonla çivilendi; (2) bileşik yabancı anahtar satırın başka
    # firmanın belgesine bağlanmasını veritabanı seviyesinde imkânsız kılıyor;
    # (3) company_id NOT NULL, yani yazarın onu vermemesi sert hata.
    "order_items", "purchase_items", "quote_items", "return_items",
    "stock_transfers", "suppliers", "technician_profiles",
    "user_company_memberships",
    "supplier_import_profiles", "supplier_part_prices", "supplier_part_xrefs",
    "supplier_price_discount_tiers", "supplier_price_history",
    "supplier_price_import_lines", "supplier_price_imports", "supplier_product_prices",
    "warehouse_stocks", "warehouses", "work_order_attachments",
    "work_order_labor_lines", "work_order_parts", "work_order_stock_events", "work_orders",
})

# text() SQL that builds its company_id scoping dynamically (f-string WHERE /
# column list the static scanner cannot see) or that is pure schema DDL. Each
# entry is (source-path suffix, distinctive substring, why it is safe). Verified
# by hand; editing any of these queries re-triggers this guard.
ALLOWLIST: list[tuple[str, str, str]] = [
    ("app/auth.py", "ALTER TABLE security_audit_logs ADD COLUMN company_id",
     "legacy schema DDL, not a tenant data query"),
    ("app/core_schema.py", "CREATE INDEX IF NOT EXISTS ix_income_expenses_company",
     "schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_notes",
     "legacy schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_notes_lookup",
     "schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_contacts",
     "legacy schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_contacts_lookup",
     "schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_tasks",
     "legacy schema DDL, not a tenant data query"),
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_tasks_lookup",
     "schema DDL, not a tenant data query"),
    ("app/inventory.py", "UPDATE warehouses SET company_id=1",
     "one-time legacy backfill before tenant-scoped operation"),
    ("app/inventory.py", "UPDATE warehouse_stocks SET company_id=1",
     "one-time legacy backfill before tenant-scoped operation"),
    ("app/main.py", "SELECT DISTINCT company_id FROM payment_allocations",
     "startup warning intentionally enumerates tenants without returning tenant data"),
    ("app/notifications/archive.py", "SELECT DISTINCT company_id FROM notifications",
     "platform archive scheduler enumerates tenants, then archives each tenant separately"),
    ("app/entity_detail.py", "SELECT d.id,d.charge_type,d.period_end",
     "nested allocation is tenant-bound; work order joins the tenant-bound charge alias"),
    ("app/routers/customers.py", "SELECT (SELECT COUNT(*) FROM orders WHERE customer_id=:id",
     "both scalar subqueries independently bind company_id=:cid"),
    ("app/routers/finance.py", "SELECT (SELECT COUNT(*) FROM purchases WHERE supplier_id=:id",
     "both scalar subqueries independently bind company_id=:cid"),
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND receivable_charge_id",
     "outer allocation and nested payment scopes independently bind company_id=:cid"),
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND payment_id IN ( SELECT id FROM payments WHERE company_id=:cid AND reference_type=:rt",
     "outer allocation and nested payment scopes independently bind company_id=:cid"),
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND payment_id IN ( SELECT id FROM payments WHERE company_id=:cid AND reference_type='order'",
     "outer allocation and nested payment scopes independently bind company_id=:cid"),
    ("app/routers/products.py", "SELECT COALESCE((SELECT SUM(oi.quantity)",
     "four scalar subqueries independently bind their order or purchase alias to :cid"),
    # Tarla panosu özeti: dış SELECT'in FROM'u YOK, dolayısıyla üst seviyede
    # bağlanacak bir company_id de yok. Dört skaler alt sorgunun HER BİRİ
    # kendi company_id=:cid yüklemini taşıyor (elle doğrulandı).
    ("app/routers/dashboard.py", "WITH sales AS (",
     "yedi CTE'nin HER BİRİ kendi company_id=:cid yüklemini bağımsız taşıyor; "
     "elle doğrulandı (orders/purchases/payments/income_expenses/products/"
     "customers/suppliers). Çözücü yedi CTE'li sorguda bunu KANITLAYAMIYOR"),
    ("app/routers/farm.py", "SELECT (SELECT COUNT(*) FROM farms WHERE company_id=:cid",
     "four scalar subqueries independently bind company_id=:cid"),
]

# Exact normalized-SQL fingerprints make each exception reviewable. Keeping a
# broad marker is useful for readable failure output, but the marker alone must
# never exempt a changed or newly added statement.
ALLOWLIST_FINGERPRINTS: dict[tuple[str, str], str] = {
    ("app/auth.py", "ALTER TABLE security_audit_logs ADD COLUMN company_id"): "156eaa9a560e42b09d75b3fdb967359d7c59f77581100f339017799941b1fc20",
    ("app/core_schema.py", "CREATE INDEX IF NOT EXISTS ix_income_expenses_company"): "6b75a598e9965ce1474529f9faedba1433689f7528fa266d6791eff526b13316",
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_notes"): "405bfe48657bb9e9622e8b1b69858257f3eee8cd84dda9217473f62918b5687a",
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_notes_lookup"): "451259566612c315d9658d4654fdae0b88d1b387d4c4def1d4ddcd64e155a644",
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_contacts"): "12a79584d057b50abbf8ee1b4b7d2fa6bbd0e8750a047ec8cf0c182871ea0dd3",
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_contacts_lookup"): "a85780ce3eab0dd91725b876ef0052d7300c02f4c58642dafaa478e4f7e8185c",
    ("app/crm.py", "CREATE TABLE IF NOT EXISTS entity_tasks"): "5064e9cf16eccb1895f37083f71ef21ae0cfc33f6bda5a898874e0596c22b0ea",
    ("app/crm.py", "CREATE INDEX IF NOT EXISTS idx_entity_tasks_lookup"): "d6d26e0c1dbe4f9a40a47b1de7154fc27d610550af3c7b8d602fffb4c72567d5",
    ("app/inventory.py", "UPDATE warehouses SET company_id=1"): "5023240c67316a1b242efab2ce5b7375ca5b593ba716cb1fdee16baa0dbb3997",
    ("app/inventory.py", "UPDATE warehouse_stocks SET company_id=1"): "6aa383112edcea1338d70dc2ed0179629e963dc7ccba835f48abf1bd64f8e29e",
    ("app/main.py", "SELECT DISTINCT company_id FROM payment_allocations"): "29eff6439dcc579654a588d5c92814fd914dfe637dbccc2330deaebe0df35154",
    ("app/notifications/archive.py", "SELECT DISTINCT company_id FROM notifications"): "8a0d94bc7dd50c2278a57d599848429faaeb5b3e84aae9b44e62d848126bc496",
    ("app/entity_detail.py", "SELECT d.id,d.charge_type,d.period_end"): "0f70ff4f66edfb227ebb4ba231eb428d2ed7483293d1b7c16a8416921102800a",
    ("app/routers/customers.py", "SELECT (SELECT COUNT(*) FROM orders WHERE customer_id=:id"): "2095ef89862f7d5c85cf156364049932c39283ad73654fa865cf56db91cafadb",
    ("app/routers/finance.py", "SELECT (SELECT COUNT(*) FROM purchases WHERE supplier_id=:id"): "4a1cffd30e75c0d17d6b0efbab47a9df4b62bdd759bcc9536ba8aa31db9cb8b3",
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND receivable_charge_id"): "1a77e6561f5e27503932338383f0440db23123fa840a25525e5bfef2177d04d3",
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND payment_id IN ( SELECT id FROM payments WHERE company_id=:cid AND reference_type=:rt"): "dbc163e6998c3f7f12f835f15acb6fc76f638c8e3cb4cc61e6ce63ebced96bdd",
    ("app/routers/transactions.py", "SELECT 1 FROM payment_allocations WHERE company_id=:cid AND payment_id IN ( SELECT id FROM payments WHERE company_id=:cid AND reference_type='order'"): "7a995681a144fbc4fdc9d0d4c0dfc995ea06405bf54eccc5b6169d942211d753",
    ("app/routers/dashboard.py", "WITH sales AS ("): "760c7fcc5d47fbca84ecd991bf3063b22162ae965d121b282e7102c86ac76dd4",
    ("app/routers/farm.py", "SELECT (SELECT COUNT(*) FROM farms WHERE company_id=:cid"): "690b96587e400f906d75ab8ee7a38b8b5f4f4cd264be0d466e57917e07ca066d",
    ("app/routers/products.py", "SELECT COALESCE((SELECT SUM(oi.quantity)"): "5223bd2ba7bb9f3e2427267c53181268394506e653876df8e4ff34edc367896e",
}

# Runtime-built SQL cannot be proven from literal text alone. Every file that
# contains such a call is reviewed as a unit and locked by its call count plus a
# whole-file AST fingerprint. Any source or inventory change forces re-review.
#
# BU HASH'LER PYTHON SÜRÜMÜNE DUYARLI — REGENERATE ETMEDEN ÖNCE OKUYUN.
#
# Parmak izi ``sha256(ast.dump(tree, include_attributes=False))``. ``ast.dump``
# çıktısı YORUMLAYICI SÜRÜMÜNE bağlıdır: 3.12, PEP 695 ile FunctionDef /
# AsyncFunctionDef / ClassDef düğümlerine ``type_params`` alanını ekledi, o
# yüzden 3.11'de üretilen dökümde bu alan YOKTUR ve BÜTÜN dosyaların hash'i
# kayar. Kaynakta tek satır değişmemiş olsa bile.
#
# Ayırt etme kuralı, kapı kırmızıya döndüğünde:
#   * BÜTÜN dosyalar birden uymuyorsa  -> SÜRÜM uyuşmazlığı. Kaynak değişmedi.
#     Yapılacak şey sabitleri yenilemek DEĞİL, doğru yorumlayıcıyla koşmak.
#     Yenilemek CI'ı kırar ve inceleme kilidini sessizce sıfırlar.
#   * TEK bir dosya uymuyorsa          -> GERÇEK değişiklik. O dosya yeniden
#     incelenir ve hash'i bilerek güncellenir.
#
# Sabitlenen sürüm: CPython 3.12 (.github/workflows/ci.yml'deki
# ``python-version: "3.12"`` ile aynı). Yerelde başka bir sürümle koşuluyorsa
# bu kapı ölçüm yapmıyor demektir; 3.12 kurup tekrar koşun.
DYNAMIC_SQL_FILE_ALLOWLIST: dict[str, tuple[int, str, str]] = {
    "backend/app/activity_log.py": (3, "f7e0178685fbc0d2f50e579f4e8f8d62ba18aaf0387e692c16943e0c955ca7aa", "fixed filters after tenant_text-enforced company scope"),
    "backend/app/allocation_reconciliation.py": (1, "93f03e612ba84b11d2b60fc33777bc87375080de70a004532969364a56aa8875", "internal fixed company-scoped aggregate callers"),
    "backend/app/billing_service.py": (1, "3442e191e4713a297adcc9fc6e9c6003a360e663db7db17271a2052fbb920bef", "dialect lock suffix; work-order root and joins tenant-scoped"),
    "backend/app/change_history.py": (3, "bdef8b48f40d5f03678750aedecb4fb077323ffb496e50fe48aedc38f44f2fad", "closed restore table map after same-company source check"),
    "backend/app/core_schema.py": (3, "283ae60626d35347a0cdb68497feeee7ed878e538b58ad5362a5ca53ff1e9b82", "dialect-quoted schema DDL and fixed legacy tenant backfill identifiers. Parmak izi 2026-08-27de guncellendi: products tablosuna UniqueConstraint('company_id','id', name='uq_products_company_id') eklendi — crop_seasons.product_id'nin bilesik yabanci anahtarinin HEDEFI (goc 20260827_0062); orders/purchases/quotes/returns'te zaten var olan ayni bildirim. SQL METNI DEGISMEDI: eklenen sey bir Core kisit BILDIRIMIDIR, text() cagrisi degil. Dosyadaki dinamik text() cagrisi sayisi 3te SABIT ve argumanlari develop ile BIREBIR ayni; parmak izi degisti cunku dosya AST'sinin TAMAMINDAN turuyor."),
    "backend/app/crm.py": (1, "4384964124861a259e75c8d7f5d188fb9c7635556e28e1d501fc5ad07ab4245f", "schema DDL from closed CRM column map"),
    "backend/app/database_backup.py": (1, "b0cc6d813dbb67caff63cd1e8291dc308e40e806958fbf3ab302953b6d6f080a", "aggregate backup inventory over closed required-table set"),
    "backend/app/document_engine.py": (9, "1861cf823555d8f8c61fd20b2be7e6b55e44f3224059ec5d42583bff3c8287e1", "closed document maps; scoped lookups plus legacy schema repair"),
    # ORDER BY artık config['document_date']'i de interpole ediyor. Değer
    # ENTITY_CONFIG'teki kapalı sözlükten gelir ('order_date' / 'purchase_date'),
    # istekten değil; aynı ifade zaten aynı sorgunun SELECT'inde kullanılıyor.
    "backend/app/entity_detail.py": (9, "0b51fa166b5a3b50ad1bcdfd16b1e5dc151b9eaf76ed134c668251ebec121dcc", "closed entity config; tenant roots, charge subqueries, paged/year-filtered document reads scoped"),
    "backend/app/finance_engine.py": (3, "a3ed0e64d76e0285180a4ae47fdfc47bf2b4d166c933f5bcc53c1d0f005904ca", "fixed schema identifiers and dialect lock suffixes"),
    "backend/app/inventory.py": (1, "a9d2246c50037de031cc8707b86ed120ebb2910f6a8760656924f7a46e20eefb", "schema DDL from internal fixed identifiers; transfer line tenant column and composite parent key. Parmak izi 2026-08-12de guncellendi: ensure_company_default_warehouse depo adi taramasi kiraci kapsamina alindi (sema olcumu: warehouses.name uzerinde UNIQUE yok). Dosyadaki dinamik text() cagrisi ve argumanlari DEGISMEDI."),
    "backend/app/late_fee_charge_engine.py": (7, "3f96f5d377ad88b4b8990ffa5245ab68f8e4bd6ca68f83c5e2b952599d2f76c6", "closed lock/installment fragments; every root binds company_id"),
    "backend/app/maintenance.py": (1, "02ce8076a29cb3ffe6ddfb06fe4b61b0e4db877ebb218ec0a98e7651dbb5aff7", "PostgreSQL catalog query with fixed lock-mode constants"),
    "backend/app/movement_references.py": (1, "2d8c02a5727a43be7607d58b5eee06f523eced75c97799cf22895c278f979a5e", "closed reference map with canonical tenant predicate"),
    "backend/app/notifications/archive.py": (3, "0805e49398f75be912764525bb6f5a1009d90bc929eb85fb3da5b1504410357f", "fixed columns and tenant-selected bound id placeholders"),
    "backend/app/notifications/consents.py": (2, "2e7b3605b52f27e2c78d56e5a1edbeb8a610cd8e0dab31ac31a193f3a6e4400b", "fixed projections with company_id predicates"),
    "backend/app/notifications/rules.py": (2, "ed10bd5eb98db186e017981539376ef17bc5194518839db0dd0e77b2f9a36e9f", "validated scope filters; explicit scheduler global-or-tenant mode"),
    "backend/app/notifications/service.py": (1, "39f5416b94e7e3a7795e39b2cb0d69f26ce8b5721c9d53a12d1b0d30ad9a429d", "fixed insert values include tenant conflict key. Parmak izi 2026-08-15te tekrar guncellendi: enqueue_notification da Core INSERT icin acik _ENQUEUE_SUTUNLARI demetine gecti ve company_id ACIK anahtar olarak veriliyor; sozluk yalnizca ON CONFLICT yolundaki text() sorgusunun sutun/yer tutucu listesi ve bind parametresi olarak kaliyor. Onceki guncelleme (2026-08-12): _finalize opak **values yayilimini birakip _FINALIZE_SUTUNLARI demetinden suzuyor (company_id o demette YOK). Iki turda da dosyadaki dinamik text() cagrisi ve argumanlari DEGISMEDI; degisen yalnizca Core ifade govdeleri."),
    "backend/app/notifications/templates.py": (3, "227df912c1ab33bd8d0d95291c273f3e4987aa9ba0eb8cd2e64b79b87db38aff", "fixed projections with company_id predicates"),
    "backend/app/payment_allocation_engine.py": (16, "8bbd3c8c15beeb048831e7380900973dcb8df5b80293277cbc0b8533f7ea8b3f", "closed lock/filter/selector fragments; canonical tenant predicates"),
    "backend/app/receivables_engine.py": (8, "8463c1b38ae3b68069b7d4ff4b6824c2b31755dd91e70bf6d16d2dd590acec01", "fixed date/status fragments; roots and joins tenant-scoped"),
    "backend/app/routers/absorption.py": (2, "95f560bc8b13c7451d12f204cc958ae0cb46f8d57b893f7cbc6d1b221c309e4b", "fixed excluded-status fragment; order roots tenant-scoped"),
    "backend/app/routers/analytics.py": (2, "c57b094ee46e10171eb6b55a1f1651785063989799e4a31a561916ab6b9e0091", "fixed accounting-status helper over tenant-scoped orders"),
    "backend/app/routers/customers.py": (1, "27b663dfee2cb6b51759654054051d06eba11c9a31d792ad70c8f13092031bb9", "closed sort/filter fragments; all tenant sources scoped"),
    "backend/app/routers/dashboard.py": (2, "b87074fbfdcc0a976da4f6ad504d990c62507ca42edfddcd057a7e91bfe8ad5e", "module-local sinks with reviewed company-scoped callers #77: satır tablosu (order_items/return_items/purchase_items) #73 ile kiracıya ait oldu; geçiş yardımcısı görünürlüğü bu dört sorguyu kapıya AÇTI ve çocuk yüklemi eklendi. Sonuç KORUNUR: 0058 company_id'yi ebeveynden backfill eder, sahipsiz satırda DURUR, NOT NULL yapar ve bileşik FK child.company_id=parent.company_id'yi değişmez kılar — ebeveyn zaten :cid'e bağlıyken çocuk yüklemi tek satır bile eleyemez."),
    "backend/app/routers/demo.py": (1, "3d1d2c8605e8fd85356fd0607c49e3c069982f79afd4dade2d31d009d9b336a4", "fixed demo table inventory with tenant-only counts"),
    # Saha snapshot'ı ve saha durum yazması AYNI SELECT gövdesini paylaşıyor
    # (_FIELD_WORK_ORDER_SELECT). Paylaşım bilinçli: iki uç aynı DTO'yu
    # döndürmek zorunda, ayrı yazılsalardı istemci önbelleğine iki farklı şekil
    # girebilirdi. Birleştirilen parçaların hepsi modül sabiti — biri terminal
    # durum filtresi, biri `AND w.id=:wid`, biri diyalektten türeyen FOR UPDATE
    # eki; hiçbiri istekten gelmiyor. Gövde zaten `w.company_id=:cid AND
    # w.technician_id=:user_id` ile başlıyor, yani her iki uç da hem firma hem
    # kimlik kapsamlı.
    "backend/app/routers/field.py": (2, "6807f17b129fe86bb99a2c1f136dc5188001f028a5d787a2f62afecb2a39ecac", "shared field SELECT; closed status/id/lock fragments after company+technician predicate"),
    # Tarla Yönetimi V1 (mobil-erp#2). 13 çağrının hepsi aynı desen: sabit bir
    # SELECT gövdesi + isteğe bağlı filtre parçaları. İnterpole edilen tek şey
    # bu modülün KENDİ sabitlerinden gelen WHERE ekleri (`AND farm_id=:farm_id`
    # gibi) ve _satir()'daki tablo adı — o da modül içi _TABLOLAR kümesinden
    # doğrulanıyor, istekten gelen bir değer oraya ulaşmıyor. Her gövde
    # `company_id=:cid` ile başlıyor.
    "backend/app/routers/herd.py": (19, "f4799533127f092ca69833b724a72103757d15cba8698b2f2ef2d8f2cbf5631f", "closed filter fragments and module-internal table names; 1 of 18 interpolates a table name from the _TABLOLAR frozenset (guarded by a membership check, request data never reaches it), the other 17 append filter fragments built from literal tuples; every root binds company_id=:cid and every value goes through a bound parameter"),
    "backend/app/routers/cost_rates.py": (3, "f3ecab60c99f0eef2eca1b5a1d233472341ffdfaf6a80ec849b6730d51abf153", "kapalı süzgeç parçaları; 3 çağrının üçü de sabit tuple'lardan kurulan filtre ekliyor (kind/status), tablo adı YOK ve istek verisi hiçbir zaman metne girmiyor; her kök company_id=:cid bağlıyor ve her değer bağlı parametre"),
    # Gerçek Maliyet FAZ 2 (mobil-erp#24) yeniden inceleme: eklenen üç oran
    # sorgusu (`_ORAN_MAKINE`/`_ORAN_KULLANICI`/`_ORAN_GENEL`) TAMAMEN SABİT
    # metin — modül düzeyinde `text()` sabiti olarak duruyorlar, hiçbir parçası
    # interpole edilmiyor ve üçü de `company_id=:cid` bağlıyor. Bu yüzden bu
    # dosyanın DİNAMİK çağrı sayısı 13'te kalıyor (statik tarayıcı üçünü de
    # kendisi doğruluyor); yalnız dosya parmak izi yenilendi.
    # Okuma yolu kapısı turu: yine SQL'e HİÇ dokunulmadı. Eklenen tek şey
    # `_faaliyet_satiri` yardımcısı (mevcut `_satir` çağrısını sarıyor, YENİ
    # sorgu değil) ve dört çağrı yerinin ona taşınması. Dinamik çağrı sayısı
    # 13'te sabit; parmak izi dosya içeriğinden türediği için yenilendi.
    "backend/app/routers/entegrasyon_olaylari.py": (
        3,
        "a2c5ed3d0b278b63148e993456ba7e09a5e59a4cf7f0d8d965e289ac1c4a0a0c",
        "outbox READ surface (acilis kosulu 2). Uc dinamik text() cagrisi: liste COUNT, liste SELECT ve ozet GROUP BY. Dinamik parcalarin TAMAMI modul ici: tablo/sutun adlari `_YUZEYLER` kumesindeki donmus `OlayYuzeyi` betimleyicisinden, filtre parcalari `_kosul`daki KAPALI dallardan gelir. Istek verisi SQL METNINE hicbir yoldan girmez: status/source_type DEGERLERI :status ve :source_type olarak baglanir, `failed_only` ise yalnizca yer tutucu SAYISINI (demet uzunlugu) belirler ve kova adlari :kova0..:kovaN olarak baglanir. Her uc kok `company_id=:cid` yuklemini tasir. Ayni desen `routers/herd.py` icin zaten gozden gecirilmisti (tablo adinin modul ici kumeden gelmesi). Parmak izi 2026-08-28de guncellendi: liste ucunun `last_error` alani yanit yolunda ARINDIRILIYOR (`_gerekceyi_arindir`). Tuketicinin SAKLADIGI ham istisna metni (`beklenmeyen hata: ` onekinden sonrasi) sabit bir cumleyle degistirilir; kurate Turkce gerekce ve onekten ONCEKI parca AYNEN gecer. OLCULDU: SQL METNI DEGISMEDI — dinamik text() cagrisi sayisi 3te SABIT, uc sorgunun argumanlari develop ile BIREBIR ayni ve her kok `company_id=:cid` yuklemini tasimaya devam ediyor. Degisen tek sey Python tarafi: satirlar sozluge cevrildikten SONRA tek alan uzerinde bir donusum. Parmak izi degisti cunku dosya AST'sinin TAMAMINDAN turuyor.",
    ),
    "backend/app/routers/farm.py": (15, "88cc25ec3f9969804a71292541aec69835a9d99c83dfe04a24e8646898be5ac1", "closed filter fragments and module-internal table names; every root binds company_id. FAZ 4 adds no new interpolation: the replay ledger uses static SQL and the lost-race path rolls the transaction back instead of deleting rows. Gerçek Maliyet FAZ 2 adds three fully literal, module-level cost-rate lookups, each with its own bound company_id predicate; the read-side rate mask and the single-door read helper touch no SQL at all. FAZ 3 dilim 1 re-review: parmak izi dosya AST'sinin tamamından türediği için SQL'e dokunmayan değişiklikler de bu kapıyı açar; bu turda değişenler yalnız Python tarafı — eksik-oran sayacı faaliyet başına indi, revenue_amount money() ile normalize edildi ve _maliyet_ozeti bayrak yerine Request alıyor. Dinamik text() çağrısı sayısı 13'te sabit ve argümanları develop ile BİREBİR aynı; hiçbir sorgu metni değişmedi Parmak izi 2026-08-18'de guncellendi: FAZ 4 outbox YAZICISI eklendi (_entegrasyon_olayi_yaz). Yeni text() cagrisi SABIT bir INSERT INTO field_integration_events'tir; company_id ACIK sutun olarak :cid ile baglanir, dinamik parca tasimaz. Dosyadaki mevcut dinamik cagrilarin sayisi ve argumanlari DEGISMEDI. Parmak izi 2026-08-21 yeniden gozden gecirildi: FAZ 4 HASAT dilimi (create_harvest icinde _entegrasyon_olayi_yaz cagrisi). Bu tur SQL metni EKLEMEDI - yalniz mevcut ve zaten gozden gecirilmis SABIT INSERT INTO field_integration_events yazicisi ikinci bir yerden CAGRILIYOR; company_id yine ACIK sutun olarak :cid ile baglaniyor. OLCULDU: dosyadaki dinamik text() cagrisi sayisi 13te SABIT kaldi, yani dinamik yuzey buyumedi; parmak izi degisti cunku dosya AST'sinin TAMAMINDAN turuyor ve Python tarafi degisti. Parmak izi 2026-08-27de yeniden gozden gecirildi: SEZON URUNU dilimi (goc 20260827_0062). Bu tur YENI dinamik text() EKLEMEDI. Degisenler: (a) list_seasons'un f-string SELECT'inin PROJEKSIYONUNA product_id eklendi — WHERE company_id=:cid{kosul} yuklemi ve kosul'un geldigi KAPALI kume (parcel_id, season_year) DEGISMEDI; (b) create_season/update_season'un SABIT metinlerine product_id sutunu ve :product_id yer tutucusu eklendi; (c) _sezon_urunu_dogrula eklendi ve SQL'i YOK — zaten gozden gecirilmis _urun_dogrula'yi cagiriyor (SELECT 1 FROM products WHERE id=:id AND company_id=:cid). OLCULDU: dinamik text() cagrisi sayisi 13te SABIT kaldi. Parmak izi 2026-09-01de yenilendi: BKU KATALOGU dilimi (goc 20260901_0063). Bu tur dinamik text() cagrisini 13ten 15e CIKARDI ve iki yeni cagri da `list_ppp` icindedir: (a) COUNT(*) FROM plant_protection_products k WHERE k.company_id=:cid{kosul}, (b) ayni tablodan sayfali SELECT, `products`a kiraci ICINDE birlestirilerek (JOIN products u ON u.id=k.product_id AND u.company_id=k.company_id). HER IKISININ de kok yuklemi ACIK: k.company_id=:cid. Birlestirmede u.company_id=k.company_id olmasaydi baska firmanin urun ADI bu listeye dusebilirdi; alias esitligi bunu kapatiyor. Interpolasyon YALNIZ `kosul` degiskeninden geliyor ve o degisken KAPALI bir kumeden kuruluyor: yalnizca ' AND k.product_id=:product_id' ve ' AND k.status=:status' parcalari eklenebilir; istekten gelen DEGERLER her iki durumda da BAGLI PARAMETRE olarak gidiyor, metne hic girmiyor. Bu turda eklenen diger SQL'lerin hepsi SABIT metindir ve dinamik yuzeye girmez: create_ppp/update_ppp'nin INSERT ve UPDATE'leri (ikisi de company_id=:cid tasiyor; UPDATE ayrica updated_at=:expected_updated_at ile iyimser kilitli), _katalog_phi'nin SELECT'i (WHERE company_id=:cid AND product_id=:pid AND status='ACTIVE') ve create_activity'nin INSERT'une eklenen iki sutun (preharvest_source, catalogue_preharvest_days). _phi_coz ve _bitki_esit'in SQL'i YOK. Tekil okumalar zaten gozden gecirilmis _satir'dan geciyor; `plant_protection_products` _TABLOLAR kumesine eklendi ve o kume istekten gelen bir degeri asla kabul etmiyor. Parmak izi 2026-09-01de bir kez daha: TARLA KILITLERI (PR #19, goc 20260901_0064) ayni dosyaya bindi. OLCULDU (CPython 3.12.3, birlesmis agac): dinamik text() sayisi 15te SABIT (kilit sorgulari `_monokultur_gecmisi` `:y1`/`:y2` ve `_GIRIS_SORGU` modul sabiti, dinamik yuzeye girmez); global toplam 253te SABIT; AST 5b93084f→a708e84b cunku list_activities/create_activity projeksiyonuna reentry_override_reason/reentry_warning eklendi ve kilit dogrulama fonksiyonlari dosya AST'sine girdi. Parmak izi 2026-09-01de yeniden: `_GIRIS_SORGU` `:pid` bindparam'ina Integer tipi eklendi (PostgreSQL AmbiguousParameter — GET /field-safety pid=None). SQL METNI AYNI; dinamik text() 15te SABIT; global toplam 253te SABIT; AST a708e84b→d543d853. Parmak izi 2026-09-02de yenilendi: BKU KATALOGU CSV ICE AKTARMA dilimi (goc 20260902_0065) develop'a birlestirildi. Bu tur YENI dinamik text() EKLEMEDI: AST ile OLCULDU (CPython 3.12.3, PR #22 sonrasi birlesmis agac), dosyadaki dinamik cagri sayisi 15te SABIT ve kuresel toplam DEVELOP'UN 254u ile ayni kaldi; onceki turdaki 253 rakami PR #22 Uygulama Kayit Cizelgesinin tek dinamik cagrisini getirdiginde GECERSIZ oldu ve bu satir yeniden olculdu. Degisenler: (a) list_ppp'in f-string SELECT'inin PROJEKSIYONUNA k.origin ve k.origin_reference eklendi; WHERE k.company_id=:cid{kosul} yuklemi ve kosul'un geldigi KAPALI kume (product_id, status) DEGISMEDI, degerler yine BAGLI PARAMETRE. (b) create_ppp'nin SABIT INSERT'une origin/origin_reference sutunlari eklendi, degerleri KOD SABITI ('MANUAL', NULL). (c) Yeni import_ppp ucunun SQL'lerinin HEPSI SABIT METIN: iki urun aramasi (SELECT id FROM products WHERE company_id=:cid AND LOWER(product_code)=LOWER(:kod) ve ayni sekilde LOWER(name)=LOWER(:ad)), cakisma aramasi (SELECT id FROM plant_protection_products WHERE company_id=:cid AND product_id=:pid AND crop=:crop) ve katalog INSERT'u. DOSYADAN GELEN HICBIR DEGER SQL METNINE GIRMIYOR: urun kodu, urun adi, bitki, ruhsat no ve notlarin tamami bagli parametredir. Dosya basliklari SQL'e hic ulasmiyor — `_map` onlari yalnizca SUTUN INDEKSINE cevirir. Uc `farm` yonlendiricisinde kaldi (imports'ta DEGIL) cunku yetki onekten geliyor: /api/plant-protection-products -> farm.manage, /api/imports ise _FARM_PATH_PREFIXES'te DEGIL. _ice_aktarma_tamsayi ve _ice_aktarma_urun_coz disinda kalan yardimcilarin SQL'i YOK; `float` dali test_v2_9_decimal_contract kapisi geregi HIC YOK. Parmak izi 2026-09-02de yeniden gozden gecirildi: BITKI ADI KATLAMA dilimi develop'a bindirildi. Bu tur SQL'e HIC dokunmadi: dosyadaki dinamik text() cagrisi sayisi 15te SABIT, kuresel toplam 254te SABIT ve her cagrinin metni BIREBIR ayni. Degisen tek sey SAF PYTHON: _bitki_esit artik casefold() yerine yeni _bitki_katla yardimcisini cagiriyor (Turkce katlama, lower() ile). Parmak izi 8635fc6d->88cc25ec cunku dosya AST'sinin TAMAMINDAN turuyor. Karsilastirma SQL'e INMEDI, hala Python'da; bu dilim tam tersine SQL LOWER()a inmenin neden imkansiz oldugunu teste yaziyor."),
    "backend/app/routers/finance.py": (7, "6e4fdec89c2253e152abbc69f73f78742e7958715e1bd51128a332618f0420ee", "closed entity/sort maps; roots and joins tenant-scoped"),
    "backend/app/routers/invoices.py": (2, "c75698af0f9fe7a4a2be62aacba6585ae715df18e8597c6926e080c0a56133d8", "fixed filter/sort fragments after company predicate"),
    "backend/app/routers/machine_hour_readings.py": (3, "f53401fc20b2398f80b0741aa147fcd2059264c4db76c7300419f1ca183acbfb", "fixed columns and dialect locks; roots and joins tenant-scoped"),
    "backend/app/routers/machine_ownership.py": (1, "b2a8cc1b91a7b238de89cbae18c5371ae58b22a179636d5cb2e579876b81fd40", "fixed projection; history root and joins tenant-scoped"),
    "backend/app/routers/machines.py": (6, "224a3748aaf09973e13e264002ecfee453865dcfb9b8b8e62062f2e5f7b39782", "closed columns, filters, locks, and uniqueness identifiers"),
    "backend/app/routers/notifications.py": (1, "2e5757aaae7b1db50f6eeefdc666feddcef1235a8bb980d6c316a6f465600136", "fixed projection/tab filters; notification root tenant-scoped"),
    # 20260807 marka değişimi: bu dosyada YALNIZ PDF marka metinleri değişti
    # (belge `author` alanı ve şirket adı yedeği). SQL'e DOKUNULMADI —
    # sorgular birebir aynı. Parmak izi dosya içeriğinden türediği için yine de
    # yenilendi; kapının amacı zaten "gözden geçirilmiş dosyaya dokundun,
    # tekrar bak" demek.
    # Parmak izi 20260901de yenilendi: Uygulama Kayit Cizelgesinin iki okuma
    # ucu eklendi. OLCULDU: bu dosyanin dinamik text() cagrisi sayisi 4te
    # SABIT kaldi ve dordunun argumanlari develop ile BIREBIR ayni — yeni
    # uclarin SQL'i bu dosyada DEGIL, `app/uretici_kayit_defteri.py`de ve
    # orasi ayri bir kayitla gozden gecirildi. Parmak izi degisti cunku
    # dosya AST'sinin TAMAMINDAN turuyor.
    "backend/app/routers/outputs.py": (4, "61549fac051e522179ea6ef719c18af049e78093d3c359132c206c724523a5b9", "closed document config after tenant-scoped parent lookup"),
    "backend/app/routers/pos.py": (1, "4ebd99382d5a0a8766ad792e86bcd2823fa18b39dbc1a3481cc07609d84fecdb", "fixed barcode expression; product and stock join tenant-scoped"),
    "backend/app/routers/products.py": (6, "dec8ff78d6a3037490a6740890368c28f87ec7dfcf378b5d4ac4c6a76f3359a9", "closed sort/filter/column maps and integer-only id lists"),
    "backend/app/routers/quick_pick.py": (1, "53fbdcd9d0ea6951b8fa701ea9693339e000258cb1911321d2840d31674c2e22", "fixed optional customer filter; roots and joins tenant-scoped"),
    "backend/app/routers/reports.py": (5, "8bec3fd70e7c086ebcd7ed591b2a64f4a24ab48bc60b91e89591741a3a1b2eeb", "literal table choices and tenant-first date conditions"),
    "backend/app/routers/search.py": (2, "d11551f7da9cd8c9c9a39cdf0724d60b1a56782b65ba7ef924027a8d8e8c1536", "fixed source map; every search arm tenant-scoped"),
    "backend/app/routers/seasonal_plan.py": (1, "9927b85e1a2d87696ce9f89bc551013fa29f9c4fa8588ded2585b9044f84736a", "generated month bind names; order/product roots tenant-scoped"),
    "backend/app/routers/supplier_price_bridge.py": (6, "cb78da74130e3d97d31ba7e07926c40604f31b90c9ecb968f73c73cc43a23cc7", "closed dialect lock suffixes and tenant request context"),
    "backend/app/routers/supplier_prices.py": (17, "c17c2b075957b2adbfe7ac12dfd0260a3086915c2368180ee8c2831fe5ea1062", "module-local scoped builders and bound filter fragments #77: satır tablosu (order_items/return_items/purchase_items) #73 ile kiracıya ait oldu; geçiş yardımcısı görünürlüğü bu dört sorguyu kapıya AÇTI ve çocuk yüklemi eklendi. Sonuç KORUNUR: 0058 company_id'yi ebeveynden backfill eder, sahipsiz satırda DURUR, NOT NULL yapar ve bileşik FK child.company_id=parent.company_id'yi değişmez kılar — ebeveyn zaten :cid'e bağlıyken çocuk yüklemi tek satır bile eleyemez."),
    "backend/app/routers/technician_profiles.py": (2, "9fa286462864f1a727fe4226a5377abe9964f0e10a603436dcd3c0b6b31bb8c8", "fixed projection/status suffix; profile root tenant-scoped"),
    "backend/app/routers/transactions.py": (18, "5bd758b5e2c22ab70a55ee7a4cbdc2d2415732eeaa49753ae87ea27c5b2f9c4c", "closed sale/purchase config; scoped parents gate item operations; line INSERT writes company_id; branch_id tenant-checked before write"),
    "backend/app/routers/warehouse_count_reports.py": (1, "03c12fd287629a035b3289b93598269c4ec3b5acb4ab3949be1f7f730c17d16c", "fixed clause builder and tenant-equal correlated joins"),
    "backend/app/routers/warehouse_counts.py": (1, "2d21e6f91f26db8499823b1ac65dc3e90a68c6b67bf613c4e69be8233723b26f", "tenant-first fixed filters and HAVING fragment"),
    "backend/app/routers/work_order_attachments.py": (3, "27a5feda6049e2fafd2a9579304cf008faef67c7a6b0f6d9ca33f95c4bfe9442", "fixed columns and dialect locks; attachment roots scoped"),
    "backend/app/routers/work_order_labor_lines.py": (3, "cfdba8b38441443b4d0af228faac0d6a0401cb38e3a43615a4ebb1e212c46059", "fixed columns, locks, and validated status suffix"),
    "backend/app/routers/work_order_parts.py": (1, "f337425d18ddcfa6fd2ee024ef43354f3fa0246d79e8c01c859575410abea1f4", "fixed dialect lock suffix; work-order root tenant-scoped"),
    "backend/app/routers/work_orders.py": (4, "3a90fa216d3899b497e3bb9d8d99a6f19906320bcd97ed7731478ddba1a8a991", "fixed list fragments begin with tenant predicate and tenant joins"),
    "backend/app/routers/workflow.py": (16, "b418371785ff4cbfb227fc9279d256d8f2f6c60d465fa7d59e7d959f76f512d0", "closed workflow config; scoped parents gate item operations; line tenant predicate is UNCONDITIONAL (tenant_line scaffolding removed in slice 1)"),
    "backend/app/service_receivable_engine.py": (2, "4590a3a9db14aa3e5f4127a316f85fe1a9018f0f6a179958d60e0f30f34c0bfc", "canonical tenant predicates with closed lock suffix"),
    "backend/app/statement.py": (4, "c9a2d509dbeaff969d1c686e690e5eb35dd54dd12f7e58d294cc6d11c3946ead", "closed entity config; document and payment sources scoped"),
    "backend/app/tenancy.py": (4, "b50d5fd5a9d35269380bfaa33b70fc95a241a72bd48e8157c56c72cf8df473a7", "hardcoded bootstrap DDL plus runtime-validated tenant_text. Parmak izi 2026-09-01de guncellendi: companies Table'ina farm_monoculture_policy ve farm_reentry_policy sutunlari eklendi (goc 20260901_0064). Dinamik text() sayisi 4te SABIT; tenant_text davranisi DEGISMEDI."),
    # Uygulama Kayit Cizelgesi (20260901). TEK dinamik text() cagrisi var:
    # `_sezonlar`in WHERE gövdesi. Interpole edilen parca `kosullar`
    # listesidir ve bu liste KAPALI: dört sabit metinden ("f.id=:farm_id",
    # "p.id=:parcel_id", "s.id=:season_id", "s.season_year=:season_year")
    # yalnizca varligina bakilarak secilir; istekten gelen DEGERLER metne
    # HICBIR yoldan girmez, hepsi ayni adli BAGLI PARAMETRE olarak gecer.
    # Kok yuklem her zaman ilk sirada: "s.company_id=:cid". Iki JOIN de
    # kiracıya bagli (p.company_id=s.company_id, f.company_id=p.company_id).
    # Dosyadaki diger dort sorgu TAMAMEN SABIT metindir ve hepsi
    # `company_id=:cid` bagliyor; `app_users` firma sutunu tasimadigi icin
    # `user_company_memberships` uzerinden :cid ile baglaniyor.
    "backend/app/uretici_kayit_defteri.py": (1, "4dd7410309e069358bbbcbce2072a48ff3b5ce77ac3802a2a8930265f9c7905f", "closed filter fragments; the single dynamic WHERE body is chosen from four literal predicate strings, request values are always bound parameters, and the root predicate company_id=:cid is unconditional"),
    "backend/app/workflow.py": (2, "ee227773e34829776546ece8aa57d7df412d36fbb36899ec36b82cfdea0f44f7", "quoted hardcoded legacy schema DDL identifiers; line tables carry company_id and a composite parent key"),
}

def _literal_sql(node: ast.AST) -> str | None:
    """Concatenate the string content of a ``text(...)`` argument.

    Handles plain string constants and static ``+`` concatenation. Any runtime
    expression makes the whole call dynamic so identifier/suffix provenance is
    covered by the exact reviewed-file inventory.
    """
    parts: list[str] = []
    has_literal_text = False
    has_dynamic_expression = False

    def walk(n: ast.AST) -> None:
        nonlocal has_dynamic_expression, has_literal_text
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            parts.append(n.value)
            has_literal_text = has_literal_text or bool(n.value.strip())
        elif isinstance(n, ast.JoinedStr):
            for value in n.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                    has_literal_text = has_literal_text or bool(value.value.strip())
                else:
                    has_dynamic_expression = True
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            walk(n.left)
            walk(n.right)
        else:
            has_dynamic_expression = True

    walk(node)
    if not parts or not has_literal_text:
        return None
    if has_dynamic_expression:
        return None
    return "".join(parts)


def _text_constructor_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return imported constructor names and SQLAlchemy module aliases."""

    constructors = {"text", "tenant_text"}
    sqlalchemy_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if (
                    imported.name == "sqlalchemy"
                    or imported.name.startswith("sqlalchemy.sql")
                ):
                    sqlalchemy_aliases.add(imported.asname or "sqlalchemy")
        elif isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if (
                    node.module is not None
                    and node.module.startswith("sqlalchemy")
                    and imported.name == "text"
                ):
                    constructors.add(imported.asname or "text")
                if (
                    node.module in {"sqlalchemy", "sqlalchemy.sql"}
                    and imported.name in {"sql", "expression"}
                ):
                    sqlalchemy_aliases.add(imported.asname or imported.name)
                if (
                    node.module is not None
                    and node.module.endswith("tenancy")
                    and imported.name == "tenant_text"
                ):
                    constructors.add(imported.asname or "tenant_text")
    return constructors, sqlalchemy_aliases


def _is_text_constructor(
    call: ast.Call,
    constructors: set[str],
    sqlalchemy_aliases: set[str],
) -> bool:
    if isinstance(call.func, ast.Name):
        return call.func.id in constructors
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "text":
        return False
    root: ast.AST = call.func.value
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in sqlalchemy_aliases


def _iter_text_sql():
    """Yield (relative_path, lineno, normalized_sql) for every text() literal."""
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constructors, sqlalchemy_aliases = _text_constructor_names(tree)
        rel = path.relative_to(APP_DIR.parents[1]).as_posix()
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and _is_text_constructor(call, constructors, sqlalchemy_aliases)
                and call.args
            ):
                continue
            sql = _literal_sql(call.args[0])
            if sql is None:
                continue
            yield rel, call.lineno, " ".join(sql.split())


# GEÇİŞ YARDIMCILARI — SQL'i ÇAĞIRAN yazar, yardımcı yalnız çalıştırır.
#
# KUSUR: bu kapı `text()` çağrılarını tarar. Bir yardımcı `def h(db, sql)` biçimindeyse
# ve gövdesinde `text(sql)` çağırıyorsa, o `text()` çağrısının argümanı bir DEĞİŞKENDİR;
# gerçek SQL çağıranın satırındadır ve kapı onu HİÇ görmez. Ölçüldü (develop f244c8f3):
# dört yardımcı 14 sorguyu böyle taşıyor. On dördü de bugün doğru kapsamlı —
# kusur canlı bir sızıntı değil, biri kapsamsız kalsa KİMSENİN görmeyecek olmasıdır.
#
# YARDIMCILAR DEĞİŞTİRİLMEDİ. Kapının kendisi parametreyi izler: yardımcı ŞEKİLDEN
# bulunur (bir parametresi doğrudan `text(...)`/`.execute(...)` ilk argümanı olan
# fonksiyon), sonra o yardımcının HER çağrı yerindeki SQL çözülür ve AYNI kapsam
# denetiminden geçirilir. Böylece ihlal, yardımcının değil ÇAĞIRANIN satırıyla adlanır.
#
# `text` adı `_text_constructor_names` ile çözülür: `response.text(limit)` gibi
# SQLAlchemy olmayan `.text()` çağrıları bu yüzden yardımcı sayılmaz.
def _delegates_to_super(function: ast.AST) -> bool:
    """`super().<kendi adı>(...)` -> çerçeve ezmesi (çalıştırma hunisi)."""
    for node in ast.walk(function):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != function.name:
            continue
        inner = node.func.value
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "super":
            return True
    return False


def _pass_through_helper_positions() -> dict[str, set[int]]:
    """{ad: GÖRÜLEN TÜM konumlar}. Çakışma burada KAYBOLMAZ.

    ÖLÇÜLDÜ: sözlük ada göre anahtarlanınca aynı adlı iki yardımcı birbirini
    eziyordu ve kazanan, yolların sıralamasına bağlıydı. Yanlış konum
    kaydedilince dashboard'ın 8 `_rows` çağrısından 7'si görünmez oldu.
    Bu SESSİZ BİR DELİK DEĞİLDİ — sayım çapası dört testle bağırıyordu;
    yine de ölçüm doğruluğu için kapatıldı.
    """
    positions: dict[str, set[int]] = {}
    for name, position in _pass_through_helper_sightings():
        positions.setdefault(name, set()).add(position)
    return positions


def _refuses_missing_tenant(function: ast.AST) -> bool:
    """Gövdedeki bir `raise`, KİRACI SÜTUNUNU adlandıran bir metin taşıyor mu?"""
    for node in ast.walk(function):
        if not isinstance(node, ast.Raise):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Constant)
                and isinstance(inner.value, str)
                and "company_id" in inner.value
            ):
                return True
    return False


def _self_validating_helpers() -> set[str]:
    """SQL'ini KENDİSİ reddedebilen yardımcılar — TÜRETİLİR, sayılmaz.

    Ölçüt: yardımcı, KİRACI YÜKLEMİ YOKSA reddediyor mu — yalnız "bir `raise`
    var mı" DEĞİL. `tenant_text` tam olarak budur;
    kiracı yüklemi yoksa `ValueError` atar, yani çağrı yerini ayrıca ölçmek
    gereksizdir. Ölçüldü: keşfedilen altı yardımcıdan yalnız `tenant_text`
    `raise` içeriyor (diğer beşi 0).

    NİYE SADECE `raise` YETMEZ: donmuş küme yalnız ÜYELİĞİ sabitliyordu.
    `tenant_text` içindeki `raise` alakasız bir doğrulama hatasına
    ÇEVRİLSEYDİ, yardımcı kümede kalır ve SQL yolu kırmızısız muaf kalırdı.
    Bu yüzden muafiyet REDDİN KENDİSİNE bağlanır: `raise`in taşıdığı metin
    kiracı sütununu adlandırmalı.

    SINIR, AÇIKÇA: ölçüt hâlâ bir vekildir — reddin ÇALIŞMA ZAMANINDA doğru
    koşulda tetiklendiğini kanıtlamaz, yalnız reddin kiracı yüklemini
    adlandırdığını görür. Küme ayrıca ÇAPALANIR ki büyümesi sessiz kalmasın.
    """
    validating: set[str] = set()
    names = set(_pass_through_helper_positions())
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if function.name not in names:
                continue
            if _refuses_missing_tenant(function):
                validating.add(function.name)
    return validating


def _pass_through_helpers() -> dict[str, int]:
    """{ad: konum} — YALNIZ tek konumlu adlar. Çakışan ad DIŞARIDA bırakılmaz,
    ayrı bir kapıyla ihlal sayılır; sessizce bir konum seçmek ölçümü bozardı."""
    return {
        name: next(iter(positions))
        for name, positions in _pass_through_helper_positions().items()
        if len(positions) == 1
    }


# UYGULANAN KURAL — BİLDİRİLEN KURALLA AYNI OLMALI.
#
# Bu kapının ilk sürümlerinde bildirim ile kod ayrışmıştı: metin "yapısal
# `super()` ölçütü bırakıldı" diyordu ama `is_super_forward` keşifte duruyordu.
# Sözleşme incelemesi bunu SÖZLEŞME UYUŞMAZLIĞI olarak işaretledi ve haklıydı —
# davranış yeşil olsa bile bildirim yanlışsa kimse neyin ölçüldüğünü bilemez.
# Aşağıdaki iki cümle KODUN YAPTIĞI ŞEYDİR:
#
#   1. KEŞİF (ne yardımcıdır): bir fonksiyonun parametresi `text(...)`e, bir
#      `.execute(...)`/`.exec_driver_sql(...)`a YA DA AYNI ADLI `super()`
#      metoduna akıyorsa o fonksiyon geçiş yardımcısıdır. `super()` iletimi
#      BİLEREK keşfin PARÇASIDIR: çağıranın SQL'ini üst sınıfa taşıyan bir
#      sarmalayıcı, `def _forward_rows(self, db, sql): return
#      super()._forward_rows(db, sql)` biçiminde, aksi hâlde görünmez kalır
#      (ölçüldü). Bu, "super'e devrediyorsa hunidir" ölçütünün TERSİDİR;
#      o ölçüt kaldırıldı, bu ise ONUN YERİNE GELMEDİ, keşfi GENİŞLETTİ.
#
#   2. HUNİ AYRIMI (ne yardımcı DEĞİLDİR): yalnız ADA bakılır — aşağıdaki
#      `FRAMEWORK_EXECUTION_NAMES`. Yapısal bir huni ölçütü YOKTUR.
#
# ÇERÇEVENİN KENDİ ÇALIŞTIRMA ADLARI — KAPSAM VE YENİDEN AÇILMA KOŞULU
#
# KAPSAM: bu adları taşıyan fonksiyonlar geçiş yardımcısı SAYILMAZ. Adla
# eşleşme yapıldığı için bunları içeri almak her `db.execute(...)` çağrısını
# geçiş çağrısı sayardı (ölçüldü: site 16 -> 22, çapa kırılır).
#
# NİYE YÜZEY KAYBI YOK: `db.execute(text("..."))` biçiminde SQL çağrı yerinde
# LİTERAL durur ve `_iter_text_sql()` tarafından ZATEN taranır. Sınır aynı
# yüzeyi iki kez saymayı önler, kapsamı daraltmaz.
#
# BU GEREKÇEYİ NE YANLIŞLAR — VE KAPIYI NE ZAMAN YENİDEN AÇMAK GEREKİR:
# SQL'i DOLAYLI alan, çerçeve adı taşıyan bir UYGULAMA fonksiyonu. Yani
# `text()` sarmalayıcısı olmadan ham dize alan ve onu bir çalıştırıcıya
# ileten `def execute(self, sql)` benzeri bir tanım. Böyle bir fonksiyonun
# çağrı yerleri NE `_iter_text_sql()`e (ortada `text()` yok) NE geçiş yoluna
# (adı burada dışlanmış) girer — iki kapının da dışında kalır.
#
# BUGÜN BÖYLE BİRİ VAR MI — ÖLÇÜLDÜ, VARSAYILMADI: uygulamada çerçeve adı
# taşıyan TEK tanım `backend/app/db.py` içindeki `SungurSession.execute`'tir
# ve o HUNİNİN KENDİSİDİR (SQL saklamaz, `super().execute(...)`e devreder).
# Ham dize hâlinde doğrudan bir çalıştırıcıya giden SQL 7 yerde var
# (`database_backup.py` x4, `db.py` x3) ve hepsi `PRAGMA` ya da
# `alembic_version` okumasıdır — kiracı tablosu YOK.
#
# KOŞUL: yukarıdaki iki ölçümden biri değişirse (çerçeve adlı ikinci bir
# uygulama tanımı, ya da ham dizeyle kiracı tablosu okuyan bir çalıştırıcı
# çağrısı) bu sınır artık geçerli değildir ve gerekçe yeniden açılmalıdır.
# `test_cerceve_adi_siniri_KAPSAMI_olculur` bu iki ölçümü çapalar.
FRAMEWORK_EXECUTION_NAMES = frozenset({"execute", "exec_driver_sql", "scalar", "scalars"})


def _pass_through_helper_sightings() -> list[tuple[str, int]]:
    """(ad, konum) — konum, `self` DIŞLANMIŞ (bağlı çağrı) hâliyle."""
    return [(name, spec["bound_position"]) for name, spec in _helper_specs_raw()]


def _helper_specs_raw() -> list[tuple[str, dict]]:
    """Her tanım için: SQL parametresinin ADI ve olası konumları.

    KEŞİF GENİŞ: parametre `text(...)`e, bir `.execute(...)`a YA DA aynı adlı
    `super()` metoduna akıyorsa yardımcıdır. `super()` iletimi de sayılır —
    çağıranın SQL'ini üst sınıfa taşıyan bir sarmalayıcı, ölçüldüğünde
    görünmez kalıyordu (`def _forward_rows(self, db, sql): return
    super()._forward_rows(db, sql)`); "super'e devrediyorsa hunidir" ölçütü
    HEM bu sarmalayıcıyı yanlışlıkla dışlıyor HEM de super çağırmayan bir
    çerçeve ezmesini yanlışlıkla içeri alıyordu. Huni artık ADLA, bildirilen
    ve gerekçelendirilmiş `FRAMEWORK_EXECUTION_NAMES` sınırıyla ayrılır.
    """
    specs: list[tuple[str, dict]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constructors, sqlalchemy_aliases = _text_constructor_names(tree)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if function.name in FRAMEWORK_EXECUTION_NAMES:
                continue
            # DUNDER'lar geçiş yardımcısı değildir; `__init__` gibi bir kurucu
            # parametresini bir execute'a taşıyabilir ama çağrı yeri adla
            # eşleşmez (her sınıfın `__init__`i aynı adı taşır).
            if function.name.startswith("__") and function.name.endswith("__"):
                continue
            arguments = function.args
            names = [
                a.arg
                for a in (arguments.posonlyargs + arguments.args + arguments.kwonlyargs)
            ]
            if not names:
                continue
            has_self = names[0] in {"self", "cls"}
            for call in ast.walk(function):
                if not (isinstance(call, ast.Call) and call.args):
                    continue
                first = call.args[0]
                if not (isinstance(first, ast.Name) and first.id in names):
                    continue
                is_text = _is_text_constructor(call, constructors, sqlalchemy_aliases)
                is_exec = (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"execute", "exec_driver_sql"}
                )
                is_super_forward = (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == function.name
                    and isinstance(call.func.value, ast.Call)
                    and isinstance(call.func.value.func, ast.Name)
                    and call.func.value.func.id == "super"
                )
                if not (is_text or is_exec or is_super_forward):
                    continue
                raw = names.index(first.id)
                specs.append((function.name, {
                    "param": first.id,
                    "raw_position": raw,
                    # BAĞLI çağrı (`x.h(db, sql)`) `self`i örtük geçirir;
                    # BAĞLANMAMIŞ çağrı (`C.h(obj, db, sql)`) geçirmez.
                    # İKİSİ DE denenir — biri sessizce atlanmasın.
                    "bound_position": raw - 1 if has_self else raw,
                    "has_self": has_self,
                }))
                break
    return specs


def _helper_specs() -> dict[str, dict]:
    """Ad -> tek anlamlı spec. Çakışan ad ayrı kapıyla raporlanır."""
    grouped: dict[str, list[dict]] = {}
    for name, spec in _helper_specs_raw():
        grouped.setdefault(name, []).append(spec)
    return {
        name: specs[0]
        for name, specs in grouped.items()
        if len({s["bound_position"] for s in specs}) == 1
    }


def _resolve_pass_through_argument(call: ast.Call, spec: dict):
    """(eşleşti_mi, sql_metni). BELİRSİZ bağlama SEÇİLMEZ — OPAK'a düşer.

    ANAHTAR KELİME KESİNDİR: `h(db, sql="...")` imzadaki ADLA çözülür ve
    konum belirsizliği doğmaz.

    KONUM BELİRSİZLİĞİ: `self` taşıyan bir tanımda bağlı çağrı (`x.h(db, sql)`)
    ile bağlanmamış çağrı (`H.h(obj, db, sql)`) FARKLI konumlar ister ve çağrı
    BİÇİMİ ikisini ayırt etmez — `self.h(...)` de `H.h(...)` de bir öznitelik
    çağrısıdır. Eskiden iki konum denenip METİN ÇÖZÜLEN İLKİ alınıyordu; bu
    YANLIŞ ARGÜMANI seçebiliyordu:

        H.h(obj, "not-the-sql", "SELECT id FROM customers")
        -> bağlı konum 1 = "not-the-sql" (kiracı tablosu YOK, denetim atlar)
        -> gerçek SQL konum 2'de, HİÇ İNCELENMEZ = yanlış yeşil

    ARTIK: menzildeki aday konum birden fazlaysa SEÇİM YAPILMAZ; çağrı OPAK
    sayılır ve kırmızıya düşer. Tek aday varsa (bağlı çağrıda ham konum
    menzil dışıdır) belirsizlik yoktur ve o çözülür.
    """
    for keyword in call.keywords:
        if keyword.arg == spec["param"]:
            return True, _pass_through_sql_text(keyword.value)

    candidates = [
        position
        for position in sorted({spec["bound_position"], spec["raw_position"]})
        if 0 <= position < len(call.args)
    ]
    if len(candidates) == 1:
        return True, _pass_through_sql_text(call.args[candidates[0]])
    if len(candidates) > 1:
        # BELİRSİZ: hangi argümanın SQL olduğu çağrıdan okunamaz. Aynı metne
        # çözülüyorlarsa fark yoktur; aksi hâlde seçim yapılmaz -> OPAK.
        texts = {_pass_through_sql_text(call.args[position]) for position in candidates}
        if len(texts) == 1:
            only = texts.pop()
            return True, only
        return True, None

    if call.args or call.keywords:
        return True, None
    return False, None


def _pass_through_sql_text(node: ast.AST) -> str | None:
    """SQL'in SABİT İSKELETİ — f-string ve `+` birleştirmesi dahil.

    `_literal_sql` yalnız düz literali çözer; geçiş yardımcılarına verilen 14
    sorgunun 8'i f-string'dir ve o yolla GÖRÜNMEZ kalırdı — kapatmaya
    çalıştığımız körlüğün bir kat aşağısı. Yorumlanan parçalar atılır, sabit
    parçalar korunur: kiracı yüklemi (`company_id=:cid`) bu sorguların
    hepsinde SABİT metindedir, ölçüldü.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # Yorumlanan parça SİLİNMEZ, YERİNE NÖTR bir bağlı-değer konur. Silmek
        # `AND )` gibi bozuk SQL üretiyordu ve takma ad çözümlemesini yanıltıyordu.
        # Nötr belirteç, yorumlanan parçanın İÇİNDE bir kiracı yüklemi varsa onu
        # GÖRMEZ — yani hata kapalıya düşer (yanlış kırmızı), açığa değil.
        parts: list[str] = []
        saw_constant = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                saw_constant = True
            else:
                parts.append(" :__gecis_yer_tutucu ")
        return "".join(parts) if saw_constant else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _pass_through_sql_text(node.left)
        right = _pass_through_sql_text(node.right)
        if left is None and right is None:
            return None
        return f"{left or ''} {right or ''}"
    return None


def _called_helper_name(call: ast.AST) -> str | None:
    """Çağrılan adı çöz: `h(...)` ve `x.h(...)` biçimlerinin İKİSİ de."""
    if not isinstance(call, ast.Call):
        return None
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _iter_pass_through_sql():
    """Yield (rel, lineno, sql) for SQL handed to a pass-through helper."""
    for rel, lineno, _helper, sql in _iter_pass_through_sites():
        yield rel, lineno, sql


def _iter_pass_through_sites():
    """Yield (rel, lineno, helper_name, sql) — çözülebilen SQL."""
    specs = _helper_specs()
    if not specs:
        return
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(APP_DIR.parents[1]).as_posix()
        for call in ast.walk(tree):
            name = _called_helper_name(call)
            spec = specs.get(name) if name else None
            if spec is None:
                continue
            matched, sql = _resolve_pass_through_argument(call, spec)
            if not matched or sql is None:
                continue
            yield rel, call.lineno, name, " ".join(sql.split())


def _iter_opaque_pass_through_sites():
    """SQL'i STATİK olarak çözülemeyen geçiş çağrıları — SESSİZCE ATLANMAZ."""
    specs = _helper_specs()
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(APP_DIR.parents[1]).as_posix()
        for call in ast.walk(tree):
            name = _called_helper_name(call)
            spec = specs.get(name) if name else None
            if spec is None:
                continue
            matched, sql = _resolve_pass_through_argument(call, spec)
            if matched and sql is None:
                yield rel, call.lineno, name


def _iter_dynamic_text_calls():
    """Yield non-literal text() calls with a semantic fingerprint of their file."""

    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        constructors, sqlalchemy_aliases = _text_constructor_names(tree)
        rel = path.relative_to(APP_DIR.parents[1]).as_posix()
        # ``ast.dump`` çıktısı yorumlayıcı sürümüne bağlı: hash'ler CPython
        # 3.12'ye sabitli. Ayrıntı ve "hepsi mi tek mi" ayrımı için
        # DYNAMIC_SQL_FILE_ALLOWLIST'in üstündeki nota bakın.
        fingerprint = hashlib.sha256(
            ast.dump(tree, include_attributes=False).encode("utf-8")
        ).hexdigest()
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and _is_text_constructor(call, constructors, sqlalchemy_aliases)
                and call.args
                and _literal_sql(call.args[0]) is None
            ):
                continue
            yield rel, call.lineno, ast.unparse(call.args[0]), fingerprint


def _referenced_tenant_tables(sql_lower: str) -> list[str]:
    return sorted(
        table
        for table in TENANT_TABLES
        if re.search(rf"\b{re.escape(table)}\b", sql_lower)
    )


_BOUND_TENANT_PREDICATE = re.compile(
    r"\b(?:(?P<alias>[a-z_]\w*)\.)?company_id\s*=\s*:(?:cid|company_id)\b",
    re.IGNORECASE,
)
_TENANT_ALIAS_EQUALITY = re.compile(
    r"\b(?P<left>[a-z_]\w*)\.company_id\s*=\s*"
    r"(?P<right>[a-z_]\w*)\.company_id\b",
    re.IGNORECASE,
)
_CLAUSE_KEYWORD = re.compile(
    r"\b(?:select|from|join|on|where|having|set|returning|limit|offset|union|"
    r"group\s+by|order\s+by)\b",
    re.IGNORECASE,
)
_ALIAS_STOPWORDS = {
    "cross", "full", "group", "having", "inner", "join", "left", "limit",
    "offset", "on", "order", "outer", "returning", "right", "set", "union",
    "where",
}


def _is_filter_context(sql: str, position: int) -> bool:
    clauses = list(_CLAUSE_KEYWORD.finditer(sql, 0, position))
    if not clauses:
        return False
    return " ".join(clauses[-1].group(0).lower().split()) in {"on", "where"}


def _is_conjunctive_tenant_predicate(sql: str, match: re.Match[str]) -> bool:
    before = sql[:match.start()]
    if re.search(r"\b(?:not|or)\s*(?:\(\s*)*$", before, re.IGNORECASE):
        return False

    after = sql[match.end():]
    after = re.sub(r"^\s*\)+", "", after)
    if re.match(r"\s*(?:or\b|is\b|[<>=!])", after, re.IGNORECASE):
        return False
    return tenant_predicate_is_required(sql, match.start(), match.end())


def _tenant_alias_occurrences(sql: str, tables: list[str]) -> list[str]:
    aliases: list[str] = []
    for table in tables:
        pattern = re.compile(
            rf"\b(?:from|join|update|delete\s+from)\s+{re.escape(table)}\b"
            rf"(?:\s+(?:as\s+)?(?P<alias>[a-z_]\w*))?",
            re.IGNORECASE,
        )
        for match in pattern.finditer(sql):
            alias = (match.group("alias") or table).lower()
            aliases.append(table if alias in _ALIAS_STOPWORDS else alias)

        comma_pattern = re.compile(
            rf",\s*{re.escape(table)}\b"
            rf"(?:\s+(?:as\s+)?(?P<alias>[a-z_]\w*))?",
            re.IGNORECASE,
        )
        for match in comma_pattern.finditer(sql):
            clauses = list(_CLAUSE_KEYWORD.finditer(sql, 0, match.start()))
            if not clauses or clauses[-1].group(0).lower() != "from":
                continue
            alias = (match.group("alias") or table).lower()
            aliases.append(table if alias in _ALIAS_STOPWORDS else alias)
    return aliases


def _tenant_aliases(sql: str, tables: list[str]) -> set[str]:
    return set(_tenant_alias_occurrences(sql, tables))


def _all_tenant_aliases_are_scoped(sql: str, tables: list[str]) -> bool:
    occurrences = _tenant_alias_occurrences(sql, tables)
    aliases = set(occurrences)
    if not aliases:
        return False
    if len(occurrences) != len(aliases):
        return False

    directly_scoped: set[str] = set()
    for match in _BOUND_TENANT_PREDICATE.finditer(sql):
        if not _is_filter_context(sql, match.start()):
            continue
        if not _is_conjunctive_tenant_predicate(sql, match):
            continue
        alias = match.group("alias")
        if alias:
            directly_scoped.add(alias.lower())
        elif len(aliases) == 1:
            directly_scoped.update(aliases)

    edges: dict[str, set[str]] = {alias: set() for alias in aliases}
    for match in _TENANT_ALIAS_EQUALITY.finditer(sql):
        if not _is_filter_context(sql, match.start()):
            continue
        if not tenant_predicate_is_required(sql, match.start(), match.end()):
            continue
        left, right = match.group("left").lower(), match.group("right").lower()
        if left in aliases and right in aliases:
            edges[left].add(right)
            edges[right].add(left)

    scoped = set(directly_scoped)
    pending = list(directly_scoped)
    while pending:
        current = pending.pop()
        for connected in edges.get(current, set()) - scoped:
            scoped.add(connected)
            pending.append(connected)
    return aliases <= scoped


def _query_is_company_scoped(sql: str, tables: list[str]) -> bool:
    """Recognize a bound tenant predicate or a tenant-owned INSERT value."""

    sql_structure = re.sub(
        r"(?P<dollar>\$(?:[A-Za-z_]\w*)?\$).*?(?P=dollar)"
        r"|--[^\r\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\""
        r"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]",
        " ",
        sql,
        flags=re.DOTALL,
    )
    if _all_tenant_aliases_are_scoped(sql_structure, tables):
        return True
    for table in tables:
        insert_match = re.search(
            rf"\binsert(?:\s+or\s+[a-z_]+)?\s+into\s+{re.escape(table)}\s*"
            rf"\((?P<columns>[^)]*)\)\s*values\s*\((?P<values>[^)]*)\)",
            sql_structure,
            re.IGNORECASE | re.DOTALL,
        )
        if insert_match is None:
            return False
        columns = [column.strip().strip('"') for column in insert_match["columns"].split(",")]
        values = [value.strip() for value in insert_match["values"].split(",")]
        if len(columns) != len(values) or "company_id" not in columns:
            return False
        tenant_value = values[columns.index("company_id")]
        if tenant_value not in {":cid", ":company_id"}:
            return False
    return bool(tables)


def _is_allowlisted(rel_path: str, normalized_sql: str) -> tuple[str, str, str] | None:
    fingerprint = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()
    for entry in ALLOWLIST:
        suffix, marker, _ = entry
        expected = ALLOWLIST_FINGERPRINTS.get((suffix, marker))
        if (
            rel_path == f"backend/{suffix}"
            and marker in normalized_sql
            and fingerprint == expected
        ):
            return entry
    return None


def test_every_tenant_table_query_is_company_scoped() -> None:
    unscoped: list[str] = []
    matched_allowlist: dict[tuple[str, str, str], int] = {
        entry: 0 for entry in ALLOWLIST
    }

    for rel, lineno, sql in [*_iter_text_sql(), *_iter_pass_through_sql()]:
        low = sql.lower()
        tables = _referenced_tenant_tables(low)
        if not tables:
            continue
        if _query_is_company_scoped(sql, tables):
            continue
        entry = _is_allowlisted(rel, sql)
        if entry is not None:
            matched_allowlist[entry] += 1
            continue
        unscoped.append(f"{rel}:{lineno} references {tables} without company_id\n    {sql[:140]}")

    assert not unscoped, (
        "Raw SQL against tenant-owned tables must have a bound company_id predicate "
        "(or added to ALLOWLIST with justification):\n" + "\n".join(unscoped)
    )

    # Keep the allowlist honest: a query that was fixed or removed should not
    # keep a stale exemption.
    invalid_counts = [
        f"{entry[0]} :: {entry[1]} matched {count} queries"
        for entry, count in matched_allowlist.items()
        if count != 1
    ]
    assert not invalid_counts, (
        "Each ALLOWLIST fingerprint must match exactly one unscoped query:\n"
        + "\n".join(invalid_counts)
    )

    expected_fingerprint_keys = {(suffix, marker) for suffix, marker, _ in ALLOWLIST}
    assert set(ALLOWLIST_FINGERPRINTS) == expected_fingerprint_keys


def test_guard_actually_scans_queries() -> None:
    # Guard against a regression that silently stops finding SQL (e.g. a parser
    # or path change) and would make the scoping check vacuously pass.
    count = sum(1 for _ in _iter_text_sql())
    assert count > 200, f"expected the scanner to find many text() queries, found {count}"


def test_every_dynamic_text_call_is_exactly_reviewed() -> None:
    observed: dict[str, list[tuple[int, str, str]]] = {}
    for rel, lineno, argument, fingerprint in _iter_dynamic_text_calls():
        observed.setdefault(rel, []).append((lineno, argument, fingerprint))

    expected_files = set(DYNAMIC_SQL_FILE_ALLOWLIST)
    observed_files = set(observed)
    assert observed_files == expected_files, (
        "Every file containing runtime-built SQL must be explicitly reviewed; "
        f"unexpected={sorted(observed_files - expected_files)}, "
        f"stale={sorted(expected_files - observed_files)}"
    )

    mismatched: dict[str, object] = {}
    for rel, rows in observed.items():
        expected_count, expected_fingerprint, reason = DYNAMIC_SQL_FILE_ALLOWLIST[rel]
        fingerprints = {fingerprint for _, _, fingerprint in rows}
        if not reason.strip():
            mismatched[rel] = "missing review reason"
        elif len(rows) != expected_count:
            mismatched[rel] = {
                "expected_calls": expected_count,
                "observed_calls": len(rows),
            }
        elif fingerprints != {expected_fingerprint}:
            mismatched[rel] = {
                "expected_fingerprint": expected_fingerprint,
                "observed_fingerprints": sorted(fingerprints),
            }

    # Tuzağı kapatan ipucu, listenin ÖNÜNDE: 58 dosyalık dökümün arkasına
    # yazılsa okunmaz. BÜTÜN dosyaların birden kayması kaynak değişikliğinin
    # değil, yorumlayıcı sürümünün imzasıdır.
    ipucu = ""
    yalniz_parmak_izi = all(
        isinstance(item, dict) and "observed_fingerprints" in item
        for item in mismatched.values()
    )
    if mismatched and len(mismatched) == len(observed) and yalniz_parmak_izi:
        ipucu = (
            f"BÜTÜN {len(observed)} dosya birden uymuyor. Bu KAYNAK değişikliği "
            "değil, YORUMLAYICI SÜRÜMÜ uyuşmazlığının imzası: parmak izi "
            "``ast.dump`` üzerinden alınıyor ve 3.12 ``type_params`` alanını "
            "eklediği için sürüm değişince her hash kayar. Sabitler CPython "
            f"3.12'ye pinli, bu koşu {sys.version_info.major}."
            f"{sys.version_info.minor}. Sabitleri YENİLEMEYİN — 3.12 ile "
            "koşun. (Tek bir dosya uymuyorsa o gerçek bir değişikliktir.)\n"
        )

    # 251 -> 253: BKU katalogu listeleme yuzeyi (`routers/farm.py:list_ppp`)
    # iki dinamik cagri ekledi (sayim + sayfali okuma); ikisi de kendi
    # `k.company_id=:cid` yuklemini tasiyor ve interpolasyon kapali bir
    # filtre kumesinden geliyor.
    # 248 -> 251: outbox okuma yuzeyi (`routers/entegrasyon_olaylari.py`)
    # UC dinamik cagri ekledi. Toplam burada da donuk: tek tek dosya girdileri
    # dogru olsa bile toplu bir kayma bu satirda gorunur.
    # 253 -> 254: Uygulama Kayit Cizelgesinin TEK dinamik cagrisi
    # (`uretici_kayit_defteri._sezonlar`). Diger dort sorgusu sabit metin.
    assert sum(item[0] for item in DYNAMIC_SQL_FILE_ALLOWLIST.values()) == 254
    assert not mismatched, (
        f"{ipucu}Dynamic SQL source changed and needs re-review: {mismatched}"
    )


def test_scanner_recognizes_qualified_and_aliased_text_calls() -> None:
    tree = ast.parse(
        "import sqlalchemy as sa\n"
        "import sqlalchemy.sql as sa_sql\n"
        "import sqlalchemy.sql\n"
        "import sqlalchemy.sql.expression\n"
        "import sqlalchemy.sql.expression as expression_module\n"
        "from sqlalchemy import sql\n"
        "from sqlalchemy.sql import expression\n"
        "from sqlalchemy.sql import expression as expression_alias\n"
        "from sqlalchemy import text as sql_text\n"
        "from sqlalchemy.sql import text as sql_expression\n"
        "sa.text('SELECT id FROM payments')\n"
        "sa_sql.text('SELECT id FROM payments')\n"
        "sqlalchemy.sql.text('SELECT id FROM payments')\n"
        "sqlalchemy.sql.expression.text('SELECT id FROM payments')\n"
        "expression_module.text('SELECT id FROM payments')\n"
        "sql.text('SELECT id FROM payments')\n"
        "expression.text('SELECT id FROM payments')\n"
        "expression_alias.text('SELECT id FROM payments')\n"
        "sql_text('SELECT id FROM payments')\n"
        "sql_expression('SELECT id FROM payments')\n"
        "def local_imports():\n"
        "    from sqlalchemy import text as local_text\n"
        "    import sqlalchemy as local_sa\n"
        "    from sqlalchemy.sql import expression as local_expression\n"
        "    local_text('SELECT id FROM payments')\n"
        "    local_sa.text('SELECT id FROM payments')\n"
        "    local_expression.text('SELECT id FROM payments')\n"
    )
    constructors, sqlalchemy_aliases = _text_constructor_names(tree)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert len(calls) == 13
    assert all(
        _is_text_constructor(call, constructors, sqlalchemy_aliases)
        for call in calls
    )


def test_expression_bearing_sql_is_always_dynamic() -> None:
    expression_only = ast.parse("text(f'{sql}')").body[0].value
    helper_expression_only = ast.parse("tenant_text(f'{sql}', cid)").body[0].value
    mixed = ast.parse("text(f'SELECT * FROM payments {suffix}')").body[0].value
    comment_prefixed = ast.parse("text(f'/* reviewed */ {sql}')").body[0].value
    concatenated = ast.parse("text('/* reviewed */ ' + sql)").body[0].value

    assert isinstance(expression_only, ast.Call)
    assert isinstance(helper_expression_only, ast.Call)
    assert isinstance(mixed, ast.Call)
    assert isinstance(comment_prefixed, ast.Call)
    assert isinstance(concatenated, ast.Call)
    assert _literal_sql(expression_only.args[0]) is None
    assert _literal_sql(helper_expression_only.args[0]) is None
    assert _literal_sql(mixed.args[0]) is None
    assert _literal_sql(comment_prefixed.args[0]) is None
    assert _literal_sql(concatenated.args[0]) is None


def test_scope_detection_rejects_projection_without_bound_predicate() -> None:
    assert not _query_is_company_scoped(
        "SELECT company_id, id FROM notifications ORDER BY company_id",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT n.id FROM notifications n JOIN rules r ON r.company_id=n.company_id",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications -- WHERE company_id=:cid",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT $$ WHERE company_id=:cid $$ FROM notifications",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        'SELECT id AS "company_id=:cid" FROM notifications',
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id AS `company_id=:cid` FROM notifications",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id AS [company_id=:cid] FROM notifications",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id, company_id=:cid AS same_company FROM notifications",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT p.id FROM products p JOIN customers c ON c.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND (c.company_id=p.company_id OR 1=1) "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND NOT (c.company_id=p.company_id) "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND (c.company_id=p.company_id) IS FALSE "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND c.company_id=p.company_id IS FALSE "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND c.company_id=p.company_id IS NOT TRUE "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p JOIN customers c "
        "ON c.id=p.customer_id AND c.company_id=p.company_id IS NULL "
        "WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications ORDER BY company_id=:cid",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications WHERE company_id=:user_id",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications WHERE company_id=:cid OR 1=1",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications WHERE company_id=:cid AND status=:status OR 1=1",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications "
        "WHERE (company_id=:cid AND status=:status) OR archived_at IS NULL",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications "
        "WHERE NOT (status=:status AND company_id=:cid)",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications WHERE (company_id=:cid) IS FALSE",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT id FROM notifications "
        "WHERE (company_id=:cid AND status=:status) IS FALSE",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p, customers c WHERE p.company_id=:cid",
        ["customers", "products"],
    )
    assert not _query_is_company_scoped(
        "SELECT * FROM products p WHERE EXISTS ("
        "SELECT 1 FROM customers p WHERE p.company_id=:cid)",
        ["customers", "products"],
    )
    assert _query_is_company_scoped(
        "SELECT id FROM notifications "
        "WHERE company_id=:cid AND (status=:status OR archived_at IS NULL)",
        ["notifications"],
    )
    assert _query_is_company_scoped(
        "SELECT id FROM notifications WHERE company_id=:cid",
        ["notifications"],
    )
    assert _query_is_company_scoped(
        "INSERT INTO notifications(company_id, event_type) VALUES(:cid, :event)",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "INSERT INTO notifications(company_id, event_type) VALUES(1, :event)",
        ["notifications"],
    )
    assert not _query_is_company_scoped(
        "INSERT INTO notifications(company_id, event_type) VALUES(:other_company_id, :event)",
        ["notifications"],
    )


def test_allowlist_requires_the_exact_reviewed_statement() -> None:
    assert _is_allowlisted(
        "backend/app/routers/reports.py",
        "SELECT x FROM payments",
    ) is None
    assert _is_allowlisted(
        "backend/app/notifications/service.py",
        "INSERT INTO notifications(event_type) VALUES(:event)",
    ) is None
    reviewed_sql = next(
        sql
        for rel, _, sql in _iter_text_sql()
        if rel == "backend/app/auth.py"
        and "ALTER TABLE security_audit_logs ADD COLUMN company_id" in sql
    )
    assert _is_allowlisted("backend/app/auth.py", reviewed_sql) is not None
    assert _is_allowlisted("backend/app/evil/app/auth.py", reviewed_sql) is None


def test_tenant_table_inventory_matches_migrated_schema(tmp_path: Path) -> None:
    """A new company-owned table must enter the static SQL guard immediately."""

    database = tmp_path / "tenant-inventory.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(APP_DIR.parent)
    # ÇOCUĞUN ve EBEVEYNİN kodlaması AÇIKÇA eşitlenir. İKİ YARI DA GEREKLİ:
    #
    #   * bu satır olmadan çocuk, yerel ayarın kodlamasında yazar (Windows'ta
    #     cp1254) ve aşağıdaki katı UTF-8 çözümü göç günlüğündeki Türkçe
    #     harflerde patlar;
    #   * aşağıdaki ``encoding="utf-8"`` olmadan ebeveyn yerel ayarla çözer;
    #     çocuk UTF-8 yazdığı için ``Ş`` (c5 9e) cp1254'te TANIMSIZDIR, okuma
    #     iş parçacığı ölür, ``stdout`` None olur ve satır 921'deki birleştirme
    #     ``TypeError`` verir — kapı ölçüm yapamadan ÇÖKER.
    #
    # Çocuk bir Python süreci (``sys.executable``), bu yüzden PYTHONIOENCODING
    # gerçekten etkilidir; çağrı bir gün Python OLMAYAN bir sürece dönerse bu
    # yarı sessizce işlevsiz kalır ve eşleşme yeniden bozulur.
    #
    # Bunun için KAPI YOK ve bilerek yok: uyumsuzluk yalnız UTF-8 OLMAYAN bir
    # yerel ayarda görünür, CI'da öyle bir ortam yok, dolayısıyla böyle bir
    # testin kırmızıya döndüğü GÖSTERİLEMEZDİ. Gösterilemeyen test kapı
    # değildir; bilgi bu yüzden burada, çağrı yerinde duruyor.
    env["PYTHONIOENCODING"] = "utf-8"
    script = r'''
import json
import app.main
from sqlalchemy import inspect
from app.db import engine

inspector = inspect(engine)
tables = sorted(
    table
    for table in inspector.get_table_names()
    if "company_id" in {column["name"] for column in inspector.get_columns(table)}
)
print("TENANT_TABLES_JSON=" + json.dumps(tables))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=APP_DIR.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    marker = next(
        (line for line in completed.stdout.splitlines() if line.startswith("TENANT_TABLES_JSON=")),
        None,
    )
    assert marker is not None, output
    migrated_tables = frozenset(json.loads(marker.removeprefix("TENANT_TABLES_JSON=")))
    assert migrated_tables == TENANT_TABLES, (
        f"tenant table inventory drift: missing={sorted(migrated_tables - TENANT_TABLES)}, "
        f"stale={sorted(TENANT_TABLES - migrated_tables)}"
    )


# ---------------------------------------------------------------------------
# ALT SÜREÇTE SQL — bu nöbetçinin ÖLÇÜLMÜŞ kör noktası
# ---------------------------------------------------------------------------
# Bu dosyadaki kapılar AST tabanlıdır. Bir test, SQL'i `subprocess` ile ÇOCUK
# bir Python sürecine gömülü metin olarak verdiğinde, AST için o tek bir
# string sabitidir ve içindeki ifadeler GÖRÜNMEZ. Ölçüldü: #73'te iki
# `DROP TABLE orders` tam olarak bu yüzden taramadan kaçtı ve yalnız CI'nın
# izole toplayıcısı gösterdi.
#
# Sınır bir SAYIYLA donduruluyor. Amaç kör noktayı kapatmak değil — AST bunu
# yapısal olarak yapamaz — SESSİZCE BÜYÜMESİNİ engellemek.
#
# KRİTİK OLAN: `app/` altında SIFIR. Üretim kodu SQL'i alt sürece VERMİYOR;
# `app/database_backup.py`deki üç `subprocess.run` çağrısı `pg_dump` ve
# `pg_restore` ikililerini argv listesiyle çağırıyor, `-c`/`--command` yok,
# kabuk yok. Yani nöbetçinin kör noktası bir TEST AĞACI özelliğidir; kiracı
# sınırında görünmeyen bir üretim ifadesi yoktur.
#
# 123 -> 124 (#81): DOSYA sayisi degismedi (91). Yeni SQL yuzeyi DE yok.
# test_field_outbox_writer.py'deki tek smoke metni IKIYE bolundu:
# _ORTAK_KURULUM (login/ciftlik/parsel/sezon) artik iki smoke tarafindan
# paylasiliyor -- ikincisi outbox kuralini CALISMA ZAMANINDA olcen
# gozlemci. AYNI metin iki literalde duruyor; alternatifi kurulumu
# kopyalamakti, o da ayni sayiyi ayni kadar buyuturdu ve iki kopyayi
# ayri ayri bayatlatirdi.
#
# 124 -> 125 (#81, ikinci tur): yine DOSYA sayisi degismedi (91) ve yine
# app/ altinda SIFIR. Ucuncu gomulu metin, sozlesme lensinin istedigi
# KIRACI KIMLIGI capasi: outbox olayinin company_id'si kaynak faaliyetin
# company_id'sine ESIT olmali. Bu olcum CALISMA ZAMANINDA, gercek yazma
# yolunun ardindan yapilmak zorunda; AST ile yapilamaz. Buyume BILINCLI
# bir karardir ve bedeli burada kayitlidir.
#
# 125 -> 126 (#88, hasat dilimi): yine DOSYA sayisi degismedi (91) ve yine
# app/ altinda SIFIR. Yeni gomulu metin, KIMLIK PININ ULASILABILIRLIGINI
# olcen mutasyon: gecerli ama BASKA bir firma yaratip uyusmayan bir outbox
# olayi yaziyor. #81'in `cid + 1` mutasyonu FK'ye takilip pine HIC
# ULASMIYORDU; bu metin pine ulasan ilk mutasyondur ve calisma zamaninda
# kosmak zorundadir. Buyume BILINCLI bir karardir.
#
# 91/126 -> 92/128 (#91, outbox TUKETICISI): YENI bir dosya girdi —
# tests/test_field_stok_tuketici.py. Tuketici senaryolari uygulamayi
# ALT SURECTE kosturmak zorunda: gozlemci ve gocler surec basina bir kez
# kuruluyor, ve her senaryo KENDI taze veritabanini istiyor. Iki gomulu
# metin, senaryolarin ortak SENTETIK kurulumu ile kosum govdeleridir.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR.
#
# 128 -> 131 (#91 ikinci hal): DOSYA sayisi 92'de SABIT. Uc yeni gomulu
# metin, tuketicinin YARIS ve ETKI kanitlaridir: talebi kaybeden tuketici,
# ikinci hareketi reddeden benzersiz indeks ve kosullu UPDATE rowcount
# olcumu. Ucu de CALISMA ZAMANINDA, ayri surecte kosmak zorunda; statik
# olarak kanitlanamazlar. app/ altinda yine SIFIR.
#
# 92/131 -> 93/135 (#91, PG ikizi): YENI dosya —
# test_field_stok_tuketici_postgresql.py. CI'nin PG parcalari yalniz
# `test_*postgresql*.py` desenini kosuyor; tuketicinin YARIS guvencesi
# SQLite'ta OLCULEMEZ (yazmalar seri), yani bu ikiz olmadan yaris kanidi
# CI'da HIC kosmazdi. Dort gomulu metin: kurulum, tuketici, rapor ve
# ikinci-hareket/rowcount problari; hepsi AYRI SUREC gerektiriyor.
# app/ altinda yine SIFIR.
#
# 93/135 -> 94/136 (#95, zamanlayici): YENI dosya —
# tests/test_field_stok_zamanlayici.py. Zamanlayici bir UYGULAMA SURECINDE
# yasiyor; dongusunun gercekten kostugunu olcmek uygulamayi ALT SURECTE
# ayaga kaldirmayi gerektiriyor. Bir gomulu metin: dongu probu.
#
# 94/136 -> 94/139 (#95, iki kusur): DOSYA sayisi 94'te SABIT.
# Uc yeni gomulu metin, iki OLCULEN kusurun kanitlaridir:
#   +1 tests/test_field_stok_zamanlayici.py — POZITIF bir CLAIM_LOST'un
#      `tum_firmalari_isle` korunum denklemini VE zamanlayici dongusunu
#      normal tamamladigini olcen prob. Yaris kaybini belirlenimci kurmak
#      ve dongu gunlugunu okumak icin AYRI SUREC sart.
#   +2 test_field_stok_tuketici_postgresql.py — kaynagi GORUNMEYEN olayin
#      PG'de terminallesebildigini olcen kurulum ve rapor problari.
#      `SKIPPED_SOURCE_NOT_VISIBLE` 26 karakter; sutun VARCHAR(20) iken PG
#      `StringDataRightTruncation` verir ve kuyruk KALICI durur. SQLite
#      uzunlugu YOK SAYDIGI icin bu yol ancak gercek PG'de olculebilir.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR.
#
# 94/139 -> 95/143 (#95, uc acik bulgu): +1 DOSYA, +4 gomulu metin.
#   +1 dosya / +1 metin  tests/test_field_stok_deneme_tavani.py — YENI dosya.
#      DENEME TAVANININ ATESLENEBILDIGINI olcer. Zehirli olay dort tur
#      dondurulur; tavandan ONCE olmedigi, tavanda OLDUGU ve arkasindaki
#      olayin AYNI dongude islendigi ayni surecte gozlenemez: `_KAYNAK`
#      okuyucusunu zehirlemek ve olay basina commit'i izlemek AYRI SUREC
#      ister.
#   +3 metin  tests/test_field_stok_zamanlayici.py (2 -> 5) — IC ICE
#      `TestClient` ve AC/KAPA anahtari problari. Ic ice lifespan'in
#      uygulamayi artik dusurmedigi ve TEK thread actigi, anahtar kapaliyken
#      HIC thread acilmadigi ve stogun KIMILDAMADIGI ancak gercek bir
#      uygulama surecinde olculebilir; ikisi de thread sayar.
# app/ altinda yine SIFIR.
#
# 95/143 -> 95/144 (#95, TALEBIN KENDISI PATLARSA): DOSYA sayisi 95'te SABIT.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_TALEP_KILIT`.
#      Talep KORUMANIN ICINDE mi sorusunu olcer. Kilit BASKA bir baglantidan
#      `FOR UPDATE` ile alinir ve tuketici oturumuna `lock_timeout` konur:
#      basarisizlik BELIRLENIMCIDIR (yaris DEGIL) ama iki ES ZAMANLI baglanti
#      ve gercek bir kilit yoneticisi gerektirir. SQLite'ta bu yol HIC
#      olculemez: orada oturumlar arasi satir kilidi ve `lock_timeout` yoktur.
#
# Ayni is icin eklenen IKINCI test (tests/test_field_stok_zamanlayici.py,
# sahiplenilen olu thread) bu sayaci HIC HAREKET ETTIRMEZ ve bu bir karardir:
# probu SQL'siz kurulabildi (`SessionLocal` ve `tum_firmalari_isle` yerine
# sahte kondu), cunku olculen sey veritabani degil THREAD davranisidir.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR.
#
# 95/144 -> 97/147 (#95, KAPANIS TURU): +2 DOSYA, +3 metin.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_FIRMA_DUSTU`.
#      Bir firmanin dongusu duserse SIRADAKI firmalar isleniyor mu? Oturum
#      GERCEKTEN oldurulur (`pg_terminate_backend`), yani ariza uydurulmaz.
#      SQLite'ta bu yol HIC olculemez: orada baglanti oldurme yoktur.
#   +1 dosya/metin  tests/test_field_stok_deneme_bagil.py.
#      Kurtarma yaziminin BAGIL oldugunu olcer. Kayip guncelleme IFADENIN
#      ozelligidir, surec sayisinin degil; tek surecte IKI OTURUM yeter ve
#      bu yuzden SQLite'ta kosar.
#   +1 dosya/metin  tests/test_field_stok_firma_dongusu.py.
#      Firma korumasinin neyi yuttugunu ve neyi YUTMADIGINI dondurur:
#      calisma zamani arizasi SAYILIR, `AssertionError` (korunum ihlali)
#      YUTULMAZ.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR.
#
# 97/147 -> 97/148 (#95, ZEHIRLI OTURUMDA TAVAN): DOSYA sayisi 97'de SABIT.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_ZEHIRLI_OTURUM`.
#      Oturum zehirli ama veritabani AYAKTA iken tavanin ULASILABILIR
#      oldugunu olcer. Backend `pg_terminate_backend` ile dusurulur; SQLite'ta
#      bu yol HIC olculemez cunku orada baglanti oldurme yoktur.
#
# 97/148 -> 97/150 (#95, TAZE OTURUMUN USTU): DOSYA sayisi 97'de SABIT.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_TAZE_KILIT`.
#      `_taze_oturumda_kurtar`in KENDI oturumunun bir satir kilidi altinda
#      SINIRLI surede reddettigini olcer. Prob HICBIR `SET` icermez ve
#      sunucunun `lock_timeout`unun 0 oldugunu ONCE dogrular: yoksa olculen
#      sinir kodun degil ORTAMIN ozelligi olurdu. Kilit iki AYRI oturum
#      gerektirdigi icin SQLite'ta HIC olculemez.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_TAVAN_EZME`.
#      Kurtarma tavan yaziminin, BASKA bir iscinin bitirdigi (`SENT`) satiri
#      ezmedigini olcer. Iki oturum SIRAYLA kosar (yaris YOK); ayni anda iki
#      oturum SQLite'ta anlamli degildir.
#
# 97/150 -> 97/151 (#95, TALEBIN USTU): DOSYA sayisi 97'de SABIT.
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_TALEP_USTU`.
#      TUKETICININ KENDI oturumunun, talepte (`_talep_et`) bir satir kilidi
#      altinda SINIRLI surede reddettigini olcer. Ayni yolu kosan `_TALEP_KILIT`
#      bunu OLCEMEZ: o, tuketici oturumuna `SET SESSION lock_timeout` KOYAR ve
#      boylece kodun degil TESTIN sinirini olcer. Bu prob HICBIR `SET` icermez
#      ve sunucunun `lock_timeout`unun 0 oldugunu ONCE dogrular. Kilit iki AYRI
#      oturum gerektirdigi icin SQLite'ta HIC olculemez.
# 97/151 -> 98/155 (#95, KAPANIS: fail-closed sinir + dongu sinirlari +
# varsayilan KAPALI): +1 DOSYA, +4 metin.
#   +1 dosya / +1 metin  tests/test_field_stok_dongu_siniri.py — `AZAMI_PARTI`
#      parti sinirinin ISIRDIGINI ve kalanin SONRAKI dongude islendigini
#      belirlenimci olarak olcer (kapasite ustu kuyruk, iki dongu).
#   +1 metin  tests/test_field_stok_tuketici.py — FAIL CLOSED probu: sinir
#      kurulamayinca yazim HIC denenmiyor, olay `RECOVERY_FAILED` ve PENDING.
#   +1 metin  tests/test_field_stok_zamanlayici.py — anahtar varsayilani
#      BILEREK cevrildi (True -> False); varsayilan-ACIK probu, varsayilan-
#      KAPALI ve ACIKCA-acik olmak uzere IKI proba ayrildi (net +1).
#   +1 metin  test_field_stok_tuketici_postgresql.py — `_DONGU_BUTCESI`.
#      20 catismali olayla tek dongunun sure butcesinde KESILDIGINI olcer
#      (sinirsiz taban 180.58 sn idi); kilit iki AYRI oturum gerektirdigi
#      icin SQLite'ta HIC olculemez.
#
# 98/155 -> 99/156 (#95, 0061'in GERI ALMA yolu): +1 DOSYA, +1 metin.
#   +1 dosya / +1 metin  tests/test_field_stok_0061_gidis_donus.py — YENI
#      dosya. 0061'in `downgrade()`i bugune kadar HICBIR arka ucta kosmadi;
#      ustelik yalniz sema degil VERI de yaziyor (20 karakteri asan her
#      `status` daraltmadan ONCE 'DEAD' oluyor). Gidis-donus AYRI SUREC
#      gerektiriyor: `import app.main` goc zincirini surec basina BIR KEZ
#      kuruyor ve `command.downgrade` semayi SUREC GENELINDE degistiriyor,
#      yani ayni surecte hem 64 hem 20 hem yeniden 64 gozlenemez; her
#      iddia da KENDI taze veritabanini istiyor.
#      PG ikizi (test_field_stok_0061_postgresql.py) bu sayaca GIRMEZ:
#      alt surec KULLANMIYOR, `test_platform_backups_postgresql.py` gibi
#      surec ICINDE `command.downgrade` cagiriyor.
#
# 99/156 -> 99/160 (#2, 0062'nin BILESIK yabanci anahtari): +0 DOSYA, +4 metin.
#   Dosya sayisi DEGISMIYOR: dordu de zaten sayilan
#   `test_field_stok_tuketici_postgresql.py` icinde. Ayri surec bu dosyanin
#   KURULU deseni; yeni problar onu izliyor.
#   +1 metin  `_BILESIK_KURULUM` — iki kiracinin birer urunu ve A kiracisinda
#      bir parsel. Capraz probun hedefi olan B urunu OLMADAN kisit hicbir sey
#      reddedemezdi.
#   +1 metin  `_PROB_GOVDESI` — sezona urun yazmayi UC kez dener (AYNI kiraci
#      / BASKA kiraci / NULL) ve her birinin sonucunu SINIF + KISIT ADI ile
#      basar. Zincir probu AYNI govdeyi tekrar kullanir: geri alma + yeniden
#      kurulumdan sonra olculen sey oncekiyle HARFI HARFINE ayni olmali.
#   +1 metin  `_HASAT_KURULUM` — urununu BILDIREN ve BILDIRMEYEN iki sezon,
#      her birinde birer hasat ve olay.
#   +1 metin  `_HASAT_TUKETICI` — tuketiciyi kosturup hareketi miktar VE TIP
#      ile basar; PG NUMERIC -> `Decimal` sozlesmesi sqlite3'te olculemez.
#   Bilesik yabanci anahtar SQLite'ta `PRAGMA foreign_keys` kapaliyken HIC
#   denetlenmez ve 0062'nin batch yeniden kurulumu pragma'yi tam o pencerede
#   kapatir; guvence ancak gercek PG'de olculebilir.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR.
# 99 -> 100 / 160 -> 161: bu tur `tests/
# test_entegrasyon_olaylari_gerekce_arindirma.py` eklendi (okuma yuzeyinin
# SAKLANMIS istisna metnini sizdirmadigini olcer). BILINCLI KARAR ve app/
# ALTINDA DEGIL — ustteki `test_alt_surecte_sql_uretim_kodunda_yok` kapisi
# ETKILENMEZ. Senaryo kardesleriyle (`test_v2_9_*_sanitization.py`) ayni
# desende TAZE bir veritabaninda alt surecte kosar; gomulu SQL yalniz kanarya
# satirini yazan INSERT'tir. Metin sayisindaki 160 tabani develop'tan gelir:
# iki dal da alt surece SQL eklediginde bu sayac CAKISIR ve uzlasmayi ZORLAR —
# kapinin amaci tam olarak budur.
# 100/161 -> 102/163 (arindirmanin ETRAFINDAKI kapinin uc bosluğu): +2 DOSYA,
# +2 metin. Uc yeni iddianin ikisi YENI DOSYA acti, biri MEVCUT dosyayi
# genisletti — ve tam bu ayrim yuzunden sayac 3 degil 2 hareket ediyor.
#   +1 dosya / +1 metin  tests/test_entegrasyon_olaylari_onek_baglantisi.py —
#      YENI dosya. Arindirmanin kestigi ONEK ucta, o oneği yazan bicim dizgisi
#      BASKA modulde (`app/field_stok_tuketici.py`) ve arada HICBIR BAG YOKTU.
#      OLCULDU: tuketicinin literalindeki tek kelime degistirildiginde 49
#      `test_field_stok_*` testi ve yuzeyin 4 testi YESIL kaldi, sizinti uctan
#      uca YENIDEN ACILDI. Kapi tuketiciyi GERCEK bir veritabani hatasina
#      surer (goc 0060'in kismi benzersiz indeksi) ve SUTUNA GIREN metnin ucun
#      sabitiyle BASLADIGINI olcer; bunun icin AYRI SUREC sart: `import
#      app.main` goc zincirini surec basina bir kez kurar ve senaryo KENDI taze
#      veritabanini ister.
#   +1 dosya / +1 metin  tests/test_entegrasyon_olaylari_depo_yolu.py — YENI
#      dosya. Ham istisna `str()`i sutuna IKI yerden girer, birinden degil;
#      ikincisi (`default_warehouse` cagrisini saran `except RuntimeError`)
#      ONEKSIZ yazar, yani arindirmanin YANINDAN gecer. Bugun zararsiz oldugu
#      SINANMIYORDU. Kapi o yolu gercekten kosturur ve sunulan metnin TAM
#      OLARAK sabit cumle oldugunu olcer.
#   +0 dosya / +0 metin  tests/test_entegrasyon_olaylari_gerekce_arindirma.py —
#      ZATEN sayiliyordu (99->100 turunda eklenmisti). Bu tur ona `PENDING`
#      tasiyici bir satir ekledi; gomulu SQL yine TEK senaryo sabiti icinde
#      oldugu icin metin sayisi DEGISMEDI. Sayacin dosya-ici buyumeyi
#      gormemesi bilincli: kapi ALT SUREC YUZEYINI olcer, satir sayisini degil.
# app/ altinda yine SIFIR: uretim kodu SQL'i alt surece VERMIYOR (olculdu).
# 102/163 -> 102/165 (arindirmanin IKINCI ayagi: dar `except` yerine ARIZA
# olculuyor): +0 DOSYA, +2 metin. Ikisi de MEVCUT
# `tests/test_entegrasyon_olaylari_depo_yolu.py` icinde acildi; dosya zaten
# sayiliyordu (100/161 -> 102/163 turunda eklenmisti), bu yuzden DOSYA sayaci
# KIMILDAMIYOR.
#   +1 metin  `_SENARYO_ARIZA` — o dosyanin ILK kapisi yalniz BIRINCI ayagi
#      (kurate metnin sabitligini) olcuyordu. IKINCI ayak — `except
#      RuntimeError`in surucu/ORM hatalarini o kola HIC dusurmeyecek kadar DAR
#      oldugu — HICBIR YERDE sinanmiyordu. OLCULDU: tek kelime (`except
#      RuntimeError` -> `except Exception`) degistirildiginde
#      `default_warehouse` icindeki GERCEK bir surucu arizasi ONEKSIZ yaziliyor
#      ve HTTP cagirana `[SQL: ...] [parameters: (1,)]` AYNEN sunuluyordu; tum
#      kosum YESIL kaliyordu. Yeni senaryo bir `except` kolunun GENISLIGINI
#      olcmez (olculemez); ILISKIYI ADIYLA YOK EDER (`ALTER TABLE warehouses
#      RENAME TO ...`), yani UYDURMA DEGIL GERCEK bir `OperationalError`
#      dogurur ve DISARI CIKAN METNI olcer. AYRI SUREC sart: `import app.main`
#      goc zincirini surec basina BIR KEZ kurar ve senaryo hem KENDI taze
#      veritabanini hem de o veritabaninda GERI ALINABILIR bir sema arizasi
#      ister — ayni surecte hem saglam hem arizali iliski gozlenemez.
#   +1 metin  `_SENARYO_SUZGEC` — `_depo_gerekcesi` suzgecinin KENDISINI olcer
#      (kurate metin AYNEN gecer, kurate OLMAYAN her metin ISARETLENIR ve
#      arindirmadan sonra iz tasimaz) ve `KURATE_DEPO_GEREKCELERI` kumesinin
#      `inventory.py`deki GERCEK `raise` ile BAGINI kosturarak dogrular.
#      ARIZA senaryosundan AYRI TUTULMASI BILINCLIDIR: ariza senaryosu
#      tuketicinin ic adlarindan HICBIRINI import ETMEZ, boylece suzgec HIC
#      var olmayan bir agacta da (yani asil kusurun kendisinde) KIRMIZI
#      olabilir. Tek dosyada birlestirilseydi o agacta `ImportError` verir ve
#      sizintiyi HIC olcemezdi — olculdu.
# app/ altinda yine SIFIR: bu tur uretim kodu SQL'i alt surece VERMIYOR.
BEKLENEN_ALT_SUREC_SQL_DOSYA = 102
BEKLENEN_ALT_SUREC_SQL_METIN = 165


def _alt_surecte_sql() -> tuple[list[str], int]:
    import ast as _ast

    sql = re.compile(
        r"\b(SELECT|INSERT INTO|UPDATE|DELETE FROM|DROP TABLE|CREATE TABLE|ALTER TABLE)\b",
        re.IGNORECASE,
    )
    cocuk = {"run", "Popen", "check_output", "check_call", "call", "system", "popen"}
    kok = APP_DIR.parent
    dosyalar: list[str] = []
    toplam = 0
    for yol in sorted(kok.rglob("*.py")):
        if any(p in {"__pycache__", ".venv", "node_modules"} for p in yol.parts):
            continue
        try:
            agac = _ast.parse(yol.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        gomulu = [
            d for d in _ast.walk(agac)
            if isinstance(d, _ast.Constant) and isinstance(d.value, str)
            and len(d.value) > 120 and sql.search(d.value)
            and ("execute(" in d.value or "create_engine" in d.value or "import app" in d.value)
        ]
        cagri = [
            d for d in _ast.walk(agac) if isinstance(d, _ast.Call)
            and (getattr(d.func, "attr", None) in cocuk or getattr(d.func, "id", None) in cocuk)
        ]
        if gomulu and cagri:
            dosyalar.append(str(yol.relative_to(kok)).replace("\\", "/"))
            toplam += len(gomulu)
    return dosyalar, toplam


def test_alt_surecte_sql_uretim_kodunda_yok() -> None:
    """ÜRETİM kodu SQL'i alt sürece vermemeli — nöbetçi onu göremezdi."""
    dosyalar, _ = _alt_surecte_sql()
    uretim = sorted(d for d in dosyalar if d.startswith("app/"))
    assert not uretim, (
        "app/ altında alt sürece SQL veren dosya var; bu ifadeler AST tabanlı "
        f"kiracı nöbetçisine GÖRÜNMEZ: {uretim}"
    )


def test_alt_surecte_sql_sayisi_donduruldu() -> None:
    """Kör nokta sessizce BÜYÜMESİN."""
    dosyalar, metin = _alt_surecte_sql()
    assert (len(dosyalar), metin) == (
        BEKLENEN_ALT_SUREC_SQL_DOSYA, BEKLENEN_ALT_SUREC_SQL_METIN
    ), (
        f"Alt süreçte SQL taşıyan yüzey değişti: dosya {len(dosyalar)} "
        f"(bildirilen {BEKLENEN_ALT_SUREC_SQL_DOSYA}), gömülü metin {metin} "
        f"(bildirilen {BEKLENEN_ALT_SUREC_SQL_METIN}). Bu yüzey AST kapılarına "
        "görünmez; büyümesi bilinçli bir karar olmalı."
    )


# GEÇİŞ YARDIMCISI GÖRÜNÜRLÜĞÜ — BOŞLUK KARŞITI ÇAPA
#
# Kapsam denetimi, gördüğü sorgular üzerinde çalışır. Keşif bir gün sıfır
# yardımcı bulursa denetim hiçbir şey bulamaz ve YİNE YEŞİL kalır — yani kapı
# ölçmediğini "temiz" diye bildirir. Bu yüzden görünürlüğün KENDİSİ çapalanır.
#
# Ölçüldü 2026-08-18, develop f244c8f3: dört yardımcı, 14 çağrı yeri.
# `db.py:execute` (Session.execute örtmesi), `tenancy.py:tenant_text` (çalışma
# anında kendisi doğrular) ve HTTP `response.text()` bu sayıya GİRMEZ.
# ---------------------------------------------------------------------------

BEKLENEN_GECIS_YARDIMCILARI = {
    "_row": 1,                 # backend/app/routers/dashboard.py
    "_rows": 1,                # backend/app/routers/dashboard.py
    "_totals_by_entity": 1,    # backend/app/allocation_reconciliation.py
    "_sum_by_product": 1,      # backend/app/routers/supplier_prices.py
}
BEKLENEN_GECIS_CAGRI_SAYISI = 14

# TOPLAM POPÜLASYON DA ÇAPALANIR — prose ile sabitler ayrışmasın.
# Keşfedilen yardımcı: 5 (dört SQL taşıyan + `tenant_text`).
# Görülen çağrı yeri: 16 (14 sayılan + `tenant_text`in kendi kendini
# doğrulayan 2 çağrısı). Aşağıdaki 14, MUAF OLMAYANLARIN sayısıdır.
BEKLENEN_KESFEDILEN_YARDIMCI = 5
BEKLENEN_TUM_GECIS_SITESI = 16


def test_gecis_yardimcilari_hala_bulunuyor() -> None:
    """Keşif, SQL taşıyan dört yardımcıyı ŞEKİLDEN bulmaya devam etmeli."""
    bulunan = _pass_through_helpers()
    for ad, konum in BEKLENEN_GECIS_YARDIMCILARI.items():
        assert ad in bulunan, (
            f"SQL taşıyan yardımcı '{ad}' artık keşfedilmiyor; kapsam denetimi "
            "onun çağrı yerlerini görmez ve sessizce yeşil kalır"
        )
        assert bulunan[ad] == konum, (ad, bulunan[ad], konum)


def test_gecis_cagri_yerleri_gorunur_ve_sayisi_capali() -> None:
    """Yardımcılara verilen SQL'in kaç çağrı yerinden GÖRÜLDÜĞÜ donmuştur."""
    # MUAFİYET TÜRETİLİR, SAYILMAZ: dosya adı değil, YARDIMCININ kendisi ölçüt.
    # Eskiden `activity_log.py` adına göre dışlanıyordu; bu, çapanın içine
    # yerleştirilmiş sayılı bir istisnaydı ve ölçüldü — o dosyaya eklenen yeni
    # bir geçiş çağrısı sayıma HİÇ girmiyordu (kapsam denetimi yine yakalıyordu,
    # ama çapa kör kalıyordu). Doğru ölçüt: SQL'ini kendisi reddedebilen yardımcı.
    dogrulayan = _self_validating_helpers()
    gorunen = [
        (rel, lineno)
        for rel, lineno, helper, _ in _iter_pass_through_sites()
        if helper not in dogrulayan
    ]
    assert len(gorunen) == BEKLENEN_GECIS_CAGRI_SAYISI, (
        f"geçiş yardımcılarından görülen sorgu sayısı {len(gorunen)}, "
        f"beklenen {BEKLENEN_GECIS_CAGRI_SAYISI}: {gorunen}"
    )


def test_gecis_yolundaki_sorgular_kapsam_denetimine_GIRIYOR() -> None:
    """Görünürlük kapsam denetimine BAĞLI olmalı, yalnız listelenmiş değil.

    Kapsamsız bir SQL yardımcıya verildiğinde `_query_is_company_scoped`
    onu reddetmeli; aksi hâlde görünürlük süs olur.
    """
    kapsamsiz = "SELECT id, name FROM customers ORDER BY id"
    tablolar = _referenced_tenant_tables(kapsamsiz.lower())
    assert tablolar == ["customers"], tablolar
    assert not _query_is_company_scoped(kapsamsiz, tablolar)


def test_gecis_yolunda_COZULEMEYEN_sql_kalmamali() -> None:
    """Çözülemeyen bir geçiş çağrısı GÖRÜNMEZDİR; kapalıya düşülür.

    #59'un dersi: çözücünün göremediği biçim atlanmaz, ihlal sayılır. Bugün
    böyle bir çağrı YOK; biri eklenirse bu kapı onu adlandırır.
    """
    opaque = sorted(_iter_opaque_pass_through_sites())
    assert not opaque, (
        "SQL'i statik olarak çözülemeyen geçiş çağrısı var; kapsam denetimi "
        "bunları göremez ve sessizce yeşil kalır:" + chr(10)
        + chr(10).join(f"  {rel}:{lineno} -> {fn}()" for rel, lineno, fn in opaque)
    )


def test_ayni_adli_yardimci_CAKISMASI_yok() -> None:
    """Aynı ad, FARKLI SQL konumu -> ölçüm bozulur; sessizce bir konum seçilmez.

    ÖLÇÜLDÜ: sözlük ada göre anahtarlanınca kazanan yolların sıralamasına
    bağlıydı ve yanlış konum kaydedilince dashboard'ın 8 `_rows` çağrısından
    7'si görünmez oldu. Bu SESSİZ değildi — sayım çapası dört testle bağırdı;
    yine de belirsizlik burada ADIYLA raporlanır.
    """
    cakisan = {
        ad: sorted(konumlar)
        for ad, konumlar in _pass_through_helper_positions().items()
        if len(konumlar) > 1
    }
    assert not cakisan, (
        "Aynı adlı geçiş yardımcısı farklı SQL parametre konumlarında tanımlı; "
        "hangisinin geçerli olduğu yol sıralamasına kalır ve ölçüm bozulur: "
        + repr(cakisan)
    )


def test_kendi_kendini_dogrulayan_kume_CAPALI() -> None:
    """Türetilen muafiyet kümesi sessizce BÜYÜMESİN.

    Ölçüt "gövdesinde `raise` var" — yani "reddedebiliyor mu"nun vekilidir.
    Alakasız bir sebeple `raise` eden bir yardımcı yanlışlıkla muaf olurdu.
    """
    assert _self_validating_helpers() == {"tenant_text"}, _self_validating_helpers()


def test_OZNITELIK_cagrisi_da_denetime_giriyor() -> None:
    """Kalıcı regresyon: `x.h(db, sql)` biçimi ADIYLA çözülmeli.

    ÖLÇÜLDÜ (bu PR'dan önce): her iki yineleyici de `isinstance(call.func,
    ast.Name)` ile başlıyordu; `self._rows(db, sql)` NE kapsam denetimine NE
    opak listesine giriyordu, sayım çapası 14'te kalıyordu ve tek kırmızı
    dosyanın AST parmak iziydi. PARMAK İZİ KIRMIZISI KİRACI YARGISI DEĞİLDİR —
    yazar onu rutin olarak yeniden onaylar, dolayısıyla kapı bir şey yakalamış
    SAYILMAZ. Kapatmaya çalıştığımız görünmez sorgu sınıfının düzeltmenin
    içinde yeniden üretilmiş hâliydi.
    """
    ornek = ast.parse("self._rows(db, sql, params)").body[0].value
    assert _called_helper_name(ornek) == "_rows"
    duz = ast.parse("_rows(db, sql, params)").body[0].value
    assert _called_helper_name(duz) == "_rows"
    assert _called_helper_name(ast.parse("42").body[0].value) is None


def test_sinif_metotlari_gecis_yardimcisi_SAYILMAZ() -> None:
    """`SungurSession.execute` huni; sarmalayıcı değil.

    Öznitelik çağrıları kabul edilince bu ayrım şart oldu: aksi hâlde her
    `db.execute(...)` bir geçiş çağrısı sayılıyordu (ölçüldü: yüzlerce yanlış
    site). Keşif MODÜL DÜZEYİ tanımlarla sınırlı; probun `self._rows(...)`
    çağrısı bundan etkilenmez çünkü `_rows` TANIMI modül düzeyindedir.
    """
    assert "execute" not in _pass_through_helpers()
    assert {"_row", "_rows", "_totals_by_entity", "_sum_by_product"} <= set(
        _pass_through_helpers()
    )


def test_SINIF_ICINDEKI_yardimci_da_kesfediliyor() -> None:
    """Asimetri kapandi: tanim sinif icindeyse de yardimci sayilir."""
    NL = chr(10)
    kaynak = NL.join([
        "class H:",
        "    def _qrows(self, db, sql):",
        "        return db.execute(text(sql))",
    ])
    fonksiyonlar = [n for n in ast.walk(ast.parse(kaynak)) if isinstance(n, ast.FunctionDef)]
    assert fonksiyonlar and not _delegates_to_super(fonksiyonlar[0])

    ezme = NL.join([
        "class S:",
        "    def execute(self, statement):",
        "        return super().execute(statement)",
    ])
    metot = [n for n in ast.walk(ast.parse(ezme)) if isinstance(n, ast.FunctionDef)][0]
    assert _delegates_to_super(metot), "cerceve ezmesi huni sayilmali"


def test_BAGLI_METOT_konumu_self_icin_kaydirilir() -> None:
    """`self` cagri yerinde ortuk gecer; konum kaydirilmazsa cagri ATLANIR."""
    assert _pass_through_helpers()["tenant_text"] == 0
    assert _pass_through_helpers()["_rows"] == 1


def test_MUAFIYET_reddin_metnine_bagli() -> None:
    """`raise` var olmasi yetmez; red kiraci sutununu ADLANDIRMALI."""
    NL = chr(10)
    kiraci = NL.join(['def f(s):', '    raise ValueError("must include company_id = :cid")'])
    alakasiz = NL.join(['def f(s):', '    raise ValueError("statement must not be empty")'])
    assert _refuses_missing_tenant(ast.parse(kiraci).body[0])
    assert not _refuses_missing_tenant(ast.parse(alakasiz).body[0])


def test_populasyon_TOPLAMLARI_capali() -> None:
    """Prose ile sabitler ayrismasin: toplamlar da iddia edilir."""
    assert len(_pass_through_helpers()) == BEKLENEN_KESFEDILEN_YARDIMCI
    assert len(list(_iter_pass_through_sites())) == BEKLENEN_TUM_GECIS_SITESI


def test_ANAHTAR_KELIME_ile_verilen_sql_de_cozuluyor() -> None:
    """`h(db, sql=...)` — yalnız `call.args`a bakan çözümleme bunu ATLIYORDU."""
    NL = chr(10)
    cagri = ast.parse('_rows(db, sql="SELECT 1")').body[0].value
    spec = _helper_specs()["_rows"]
    eslesti, metin = _resolve_pass_through_argument(cagri, spec)
    assert eslesti and metin == "SELECT 1"


def test_BELIRSIZ_baglama_SECILMEZ_opak_olur() -> None:
    """İki konum da menzildeyse SEÇİM YAPILMAZ — yanlış dize alınabilirdi.

    Sözleşmenin verdiği şekil: `H.h(obj, 'not-the-sql', 'SELECT ...')`.
    Bağlı konum 1 tuzak dizeyi gösterir (kiracı tablosu YOK, kapsam denetimi
    atlar), gerçek SQL konum 2'dedir ve HİÇ incelenmezdi = yanlış yeşil.
    """
    spec = {"param": "sql", "raw_position": 2, "bound_position": 1, "has_self": True}
    tuzak = ast.parse('H.h(obj, "not-the-sql", "SELECT id FROM customers")').body[0].value
    eslesti, metin = _resolve_pass_through_argument(tuzak, spec)
    assert eslesti and metin is None, "belirsiz bağlama OPAK olmalı, seçilmemeli"

    # Bağlanmamış ama tuzaksız çağrı da belirsizdir: iki konum da menzilde.
    baglanmamis = ast.parse('H.h(obj, db, "SELECT id FROM customers")').body[0].value
    assert _resolve_pass_through_argument(baglanmamis, spec) == (True, None)


def test_BELIRSIZ_OLMAYAN_cagri_hala_cozuluyor() -> None:
    """Daraltma kapıyı kör etmemeli: tek adaylı ve anahtar-kelimeli formlar çözülür."""
    spec = {"param": "sql", "raw_position": 2, "bound_position": 1, "has_self": True}
    bagli = ast.parse('self.h(db, "SELECT id FROM customers")').body[0].value
    assert _resolve_pass_through_argument(bagli, spec) == (True, "SELECT id FROM customers")

    anahtar = ast.parse('H.h(obj, db, sql="SELECT id FROM customers")').body[0].value
    assert _resolve_pass_through_argument(anahtar, spec) == (True, "SELECT id FROM customers")

    cozulemez = ast.parse("self.h(db, sql_degiskeni)").body[0].value
    eslesti, metin = _resolve_pass_through_argument(cozulemez, spec)
    assert eslesti and metin is None


def test_cerceve_adi_siniri_KAPSAMI_olculur() -> None:
    """Ad listesinin gerekçesi ÖLÇÜLÜR; yanlışlayıcısı boş kalmalı.

    Sınır ancak şu iki ölçüm doğruyken geçerlidir. Biri değişirse gerekçe
    yeniden açılmalıdır (dosyanın üstündeki KOŞUL bölümüne bakın).
    """
    uygulama_tanimlari = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if function.name in FRAMEWORK_EXECUTION_NAMES:
                    rel = path.relative_to(APP_DIR.parents[1]).as_posix()
                    uygulama_tanimlari.append((rel, function.name))
    assert uygulama_tanimlari == [("backend/app/db.py", "execute")], (
        "Çerçeve adı taşıyan ikinci bir uygulama tanımı belirdi; ad listesinin "
        "gerekçesi artık geçerli olmayabilir: " + repr(uygulama_tanimlari)
    )

    ham_dize_kiraci = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr not in {"execute", "exec_driver_sql"} or not call.args:
                continue
            first = call.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if _referenced_tenant_tables(first.value.lower()):
                rel = path.relative_to(APP_DIR.parents[1]).as_posix()
                ham_dize_kiraci.append((rel, call.lineno))
    assert not ham_dize_kiraci, (
        "Ham dize hâlinde bir KİRACI sorgusu doğrudan çalıştırıcıya gidiyor; "
        "bu, ad listesinin yanlışlayıcısıdır: " + repr(ham_dize_kiraci)
    )


def test_SUPER_ileten_sarmalayici_yardimci_SAYILIR() -> None:
    """`super()` iletimi huni DEĞİLDİR; çağıranın SQL'ini taşıyor olabilir.

    Huni artık ADLA ayrılır (`FRAMEWORK_EXECUTION_NAMES`), yapısal `super()`
    ölçütüyle değil — o ölçüt HEM bu sarmalayıcıyı yanlış dışlıyor HEM super
    çağırmayan bir ezmeyi yanlış içeri alıyordu.
    """
    assert "execute" in FRAMEWORK_EXECUTION_NAMES
    assert "execute" not in _helper_specs()
    assert "_rows" in _helper_specs()


def test_cerceve_adlari_disinda_kalan_yuzey_ZATEN_taranyor() -> None:
    """Sınırın bedeli ölçülür: `db.execute(text(...))` SQL'i çağrı yerinde
    LİTERAL taşır ve `_iter_text_sql()` onu zaten görür."""
    kaynak = [rel for rel, _, _ in _iter_text_sql()]
    assert kaynak, "text() taraması boş olamaz"
