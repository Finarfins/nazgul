"""KİRACI GERİ YÜKLEME — 5.1a zip'inden TEK firmanın CANLI veritabanına dönüşü.

NEDEN PLATFORM GERİ YÜKLEMESİ BUNU KARŞILAMIYOR
------------------------------------------------
``routers/platform_backups.py`` ``pg_restore`` ile KÜMENİN TAMAMINI geri
yükler: bütün firmalar, tek dosyadan, çalışan veritabanının ÜSTÜNE. "Yanlışlıkla
sildim" diyen tek bir kiracı için bu çare değil — o dosya öteki kiracıların
o günden beri yazdığı her şeyi de geri sarardı. Bu modül tam tersini yapar:
5.1a'nın ürettiği zip'teki TEK firmanın satırlarını, ÇALIŞAN veritabanına,
öteki kiracılara dokunmadan yazar.

ÜRÜN KARARI — YENİ FİRMA, ÜSTÜNE YAZMA YOK
------------------------------------------
Varsayılan kip ``yeni``: zip'teki firma YENİ bir ``company_id`` ile doğar.
Var olan aktif bir kiracının üstüne HİÇBİR KOŞULDA yazılmaz. ``yerine`` kipi
yalnız kaynak firma HALA VAR, KAPALI (``is_active=false``) ve o kimliğe ait
satır HİÇBİR kiracı tablosunda YOKKEN kimliği yerinde canlandırır; ölçülen
tek bir artık satır 409 verir.

KİMLİK HARİTALAMA — 120 TABLO, 294 FK SÜTUNU
--------------------------------------------
Yeni firma yeni birincil anahtarlar demektir: her tablonun satırları TAZE
kimliklerle yazılır ve ``eski -> yeni`` haritası tablo başına tutulur. Sonra
gelen her satırın YABANCI ANAHTAR sütunları o haritalarla yeniden yazılır. Sütun
listesi ELLE YAZILMAZ; ``Table.foreign_key_constraints`` yansımadan okunur
(ölçüldü, 0082 şemasında: 294 FK sütunu, 188'i kiracı tablosuna, 76'sı
``companies``e, 30'u ``app_users``a; 8'i kendi tablosuna).

FK KISITI OLMAYAN "YUMUŞAK" REFERANSLAR da vardır (ölçüldü: ``*_id`` adlı,
kısıtsız 94 sütun). Bunlar yansımadan TÜRETİLEMEZ, çünkü hedefi söyleyen bir
kısıt yok; hedef ya sütun adından (``customer_id`` -> ``customers``) ya da satırın
ayırt edici sütunundan (``entity_type='customer'``) okunur. Bu bilgi aşağıda
AÇIKÇA yazılıdır ve KAPALIDIR: tanınmayan bir ``*_id`` sütunu sessizce geçmez,
``siniflandirilmamis_sutunlar`` onu adıyla verir ve
``tests/test_kiraci_geri_yukleme.py`` o listenin BOŞ olduğunu çiviler.

KENDİNE REFERANS iki geçişte yazılır: satır önce o sütunu NULL ile girer,
hedef satır da yazıldıktan sonra UPDATE ile bağlanır. Sıra tablo İÇİNDEDİR ve
dışa aktarım satırları birincil anahtar sırasıyla verdiği için ebeveyn çoğu
kez önce gelir; gelmezse erteleme yakalar.

TEK İŞLEM
---------
Bütün yazma TEK bağlantı, TEK işlemdedir. Herhangi bir adımda hata — bozuk
satır, çözülemeyen zorunlu referans, kısıt ihlali — işlemin TAMAMINI geri alır;
yarım firma YOKTUR. ``dry_run`` aynı yolu sonuna kadar yürür ve commit yerine
BİLEREK geri alır: kuru koşunun hiçbir şey yazmaması bir bayrak dalı değil,
işlemin kendisidir.

EKLER İŞLEMDEN SONRA
--------------------
Dosyalar diske veritabanı COMMIT edildikten SONRA kopyalanır. Tersi, geri
alınan bir işlemin ardında sahipsiz dosya bırakırdı. Kopyalanamayan ek
veritabanı satırını düşürmez; sayısı raporda durur (5.1a'nın
``missing_attachments`` bayrağıyla aynı felsefe).
"""
from __future__ import annotations

import ast
import base64
import json
import time
import zipfile
from datetime import date, datetime, time as saat, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    Table,
    Time,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection

from .auth import users, utcnow
from .db import engine
from .routers.kiraci_disa_aktarim import (
    _depo_koku,
    _ek_yolu,
    _kiraci_tablolari,
    _sema_seviyesi,
    _tablo_sirasi,
    _yansit,
)
from .tenancy import companies, memberships


class GeriYuklemeHatasi(RuntimeError):
    """Geri yüklemeyi DURDURAN, adı ve HTTP durumu konmuş hata.

    ``disa_aktarim_errors`` ailesiyle aynı biçim: ``kod`` sözleşmedir, metin
    değil. ``main.py``deki işleyici bunu ``{"detail", "code"}`` gövdesine ve
    ``durum`` alanındaki HTTP koduna çevirir. Hiçbiri kısmi yazma bırakmaz:
    doğrulama hataları işlem AÇILMADAN, ötekiler işlemin İÇİNDE (ve geri
    alınarak) doğar.
    """

    def __init__(self, kod: str, durum: int, mesaj: str, ayrinti: Any = None) -> None:
        super().__init__(mesaj)
        self.kod = kod
        self.durum = durum
        self.ayrinti = ayrinti


class _KuruKosuBitti(Exception):
    """Kuru koşunun işlemi geri almak için kullandığı İÇ sinyal."""

    def __init__(self, rapor: dict[str, Any]) -> None:
        super().__init__("dry run")
        self.rapor = rapor


#: Geçerli kipler. ``yeni``: yeni firma; ``yerine``: kapalı kaynak firmayı
#: kimliğiyle canlandır.
KIPLER = ("yeni", "yerine")

# ---------------------------------------------------------------------------
# YUMUŞAK REFERANS SÖZLÜĞÜ — ÖLÇÜLDÜ (0082 şeması, 94 sütun), ELLE SINIFLANDI
# ---------------------------------------------------------------------------
#: Kullanıcı kimliği taşıyan sütunlar. ``app_users`` PLATFORM tablosudur ve zip'e
#: girmez; kimlikler aynı platformda geçerliliğini korur, olduğu gibi kalır.
KULLANICI_SUTUNLARI: frozenset[tuple[str, str]] = frozenset(
    {
        ("activity_logs", "user_id"),
        ("entity_change_logs", "actor_user_id"),
        ("invoice_audit", "actor_user_id"),
        ("invoice_history", "actor_user_id"),
        ("notification_consent_events", "user_id"),
        ("policy_override_logs", "user_id"),
        ("security_audit_logs", "user_id"),
        ("warehouses", "technician_user_id"),
        # ÜYELİKLER AYRI YOLDAN yazılır (``_uyelikleri_yaz``); satırın kendisi
        # tablo döngüsünde ATLANIR, sütun yine de sınıflı olsun diye burada.
        ("user_company_memberships", "user_id"),
    }
)

#: ``*_id`` adlı ama kimlik OLMAYAN metin sütunları (istek/ilinti/dış kimlikler).
METIN_KIMLIK_SUTUNLARI: frozenset[tuple[str, str]] = frozenset(
    {
        ("activity_logs", "correlation_id"),
        # E4a (göç 0083): şoförün TCKN'si — metin kimlik, satır referansı DEĞİL.
        # `*_id` soneki bu sınıflandırıcıya düşürür; kayıt YOKSA
        # `test_siniflandirilmamis_yumusak_referans_yok` adıyla kırmızı olur.
        ("despatch_notes", "driver_national_id"),
        ("entity_change_logs", "request_id"),
        ("farm_operations", "operation_id"),
        ("field_operations", "operation_id"),
        ("invoices", "einvoice_external_id"),
        ("notifications", "external_id"),
        ("notifications_archive", "external_id"),
        ("policy_override_logs", "request_id"),
        ("push_devices", "session_family_id"),
        ("security_audit_logs", "request_id"),
        # İdempotens defterlerinin ``resource_id``si metindir; içeriği başka
        # bir tablonun kimliği olabilir ama sözleşmesi "kaynağın ADI" değil
        # "aynı isteğin tekrarı"dır. Yeni firmada eski kimliğe işaret etmesi
        # yalnız tekrar korumasını etkiler, veriyi değil.
        ("payment_idempotency", "resource_id"),
        ("receivable_charge_idempotency", "resource_id"),
    }
)

#: Sütun adı -> hedef tablo. Ayırt edici sütunu OLMAYAN yumuşak referanslar.
DOGRUDAN_HEDEFLER: dict[tuple[str, str], str] = {
    ("delivery_note_items", "product_id"): "products",
    ("delivery_notes", "customer_id"): "customers",
    ("delivery_notes", "warehouse_id"): "warehouses",
    ("delivery_notes", "converted_order_id"): "orders",
    ("entity_change_logs", "restored_from_log_id"): "entity_change_logs",
    ("finance_transactions", "account_id"): "finance_accounts",
    ("financial_instruments", "account_id"): "finance_accounts",
    ("financial_instruments", "financial_transaction_id"): "finance_transactions",
    ("income_expenses", "account_id"): "finance_accounts",
    ("machine_idempotency", "machine_id"): "machines",
    ("machines", "customer_id"): "customers",
    ("notification_consent_events", "consent_id"): "notification_consents",
    ("notification_rules", "template_id"): "notification_templates",
    ("notifications", "rule_id"): "notification_rules",
    ("notifications", "template_id"): "notification_templates",
    ("notifications", "consent_id"): "notification_consents",
    ("notifications_archive", "rule_id"): "notification_rules",
    ("notifications_archive", "template_id"): "notification_templates",
    ("notifications_archive", "consent_id"): "notification_consents",
    ("order_items", "product_id"): "products",
    ("orders", "customer_id"): "customers",
    ("orders", "branch_id"): "branches",
    ("orders", "warehouse_id"): "warehouses",
    ("payments", "branch_id"): "branches",
    ("payments", "account_id"): "finance_accounts",
    ("payments", "financial_transaction_id"): "finance_transactions",
    ("pos_idempotency", "order_id"): "orders",
    ("pos_system_customers", "customer_id"): "customers",
    ("products", "supplier_id"): "suppliers",
    ("purchase_items", "product_id"): "products",
    ("purchases", "supplier_id"): "suppliers",
    ("purchases", "branch_id"): "branches",
    ("purchases", "warehouse_id"): "warehouses",
    ("quote_items", "product_id"): "products",
    ("quotes", "customer_id"): "customers",
    ("quotes", "warehouse_id"): "warehouses",
    ("return_items", "product_id"): "products",
    ("returns", "warehouse_id"): "warehouses",
    ("sales_order_items", "product_id"): "products",
    ("sales_orders", "customer_id"): "customers",
    ("sales_orders", "warehouse_id"): "warehouses",
    ("stock_movements", "product_id"): "products",
    ("stock_movements", "warehouse_id"): "warehouses",
    ("stock_transfer_items", "product_id"): "products",
    ("stock_transfers", "source_warehouse_id"): "warehouses",
    ("stock_transfers", "target_warehouse_id"): "warehouses",
    ("warehouse_stocks", "warehouse_id"): "warehouses",
    ("warehouse_stocks", "product_id"): "products",
    ("warehouses", "branch_id"): "branches",
    ("work_order_stock_events", "work_order_id"): "work_orders",
    ("work_order_stock_events", "part_id"): "products",
}

#: Ayırt edici DEĞER -> hedef tablo. ``None``: bu değerin hedefi yoktur (kimlik
#: anlamsızdır, olduğu gibi kalır). Sözlükte OLMAYAN değer ``cozulemeyen``
#: olarak raporlanır ve kimlik olduğu gibi kalır.
_VARLIK: dict[str, str | None] = {
    "customer": "customers",
    "supplier": "suppliers",
    "machine": "machines",
    "machine_hour_reading": "machine_hour_readings",
    "work_order": "work_orders",
    "work_order_part": "work_order_parts",
    "work_order_labor_line": "work_order_labor_lines",
    "work_order_attachment": "work_order_attachments",
    "payment": "payments",
    "order": "orders",
    "product": "products",
    "harvest_region": "harvest_regions",
    "harvest_calendar": "harvest_calendars",
    "harvest_due_rule": "harvest_due_rules",
    "receivable_charge_document": "receivable_charge_documents",
    "supplier_import_profile": "supplier_import_profiles",
    "supplier_price": "supplier_part_prices",
    "supplier_price_import": "supplier_price_imports",
    "technician_profile": "technician_profiles",
}
_REFERANS: dict[str, str | None] = {
    "order": "orders",
    "sale": "orders",
    "purchase": "purchases",
    "transfer": "stock_transfers",
    "supplier_advance": "supplier_advances",
    "returns": "returns",
    "return": "returns",
    "producer_receipt": "producer_receipts",
    "payment": "payments",
    "instrument": "financial_instruments",
    "field_integration_event": "field_integration_events",
    "late_fee": "receivable_charge_documents",
    "invoice": "invoices",
    "work_order": "work_orders",
    "manual": None,
}
_KAYNAK: dict[str, str | None] = {
    "order": "orders",
    "orders": "orders",
    "sale": "orders",
    "sales_order": "sales_orders",
    "quote": "quotes",
    "products": "products",
    "product": "products",
    "payment": "payments",
    "purchase": "purchases",
    "field_harvest_ticket": "field_harvest_tickets",
    "field_harvest": "field_harvests",
    "harvest": "field_harvests",
    "field_activity": "field_activities",
    "activity": "field_activities",
    "animal_treatment": "animal_treatments",
    "animal_vaccination": "animal_vaccinations",
}
_DONUSUM: dict[str, str | None] = {
    "sale": "orders",
    "order": "orders",
    "sales_order": "sales_orders",
    "delivery": "delivery_notes",
}
_KAYNAK_TIPI: dict[str, str | None] = {
    # ``app.activity_log.RESOURCE_TYPES`` (22 tip) ile ölçüldü.
    "sale": "orders",
    "payment": "payments",
    "return": "returns",
    "product": "products",
    "stock": "stock_movements",
    "work_order": "work_orders",
    "invoice": "invoices",
    "late_fee_charge": "receivable_charge_documents",
    "payment_allocation": "payment_allocations",
    "notification": "notifications",
    "notification_consent": "notification_consents",
    "notification_rule": "notification_rules",
    "notification_template": "notification_templates",
    "supplier_price_import": "supplier_price_imports",
    "vet_drug": "vet_drugs",
    "animal_quarantine": "animal_quarantines",
    "field_integration_event": "field_integration_events",
    "whatsapp_pending": "whatsapp_pending_actions",
    "activity_log": "activity_logs",
    "user": None,
    "backup": None,
    "orders": "orders",
    "products": "products",
}
_IADE: dict[str, str | None] = {
    "sale_return": "customers",
    "purchase_return": "suppliers",
}
#: `notification_consents.party_type` BÜYÜK harf saklanır (CHECK listesi
#: ölçüldü: 'CUSTOMER'/'SUPPLIER'); küçük harf de kabul edilir.
_TARAF: dict[str, str | None] = {
    "customer": "customers", "supplier": "suppliers",
    "CUSTOMER": "customers", "SUPPLIER": "suppliers",
}
_TARLA_KIND: dict[str, str | None] = {
    "activity": "field_activities",
    "harvest": "field_harvests",
}
_WA_ISLEM: dict[str, str | None] = {"TAHSILAT": "payments"}

#: (tablo, sütun) -> (ayırt edici sütun, değer sözlüğü).
AYIRT_EDICI_HEDEFLER: dict[tuple[str, str], tuple[str, dict[str, str | None]]] = {
    ("activity_logs", "resource_id"): ("resource_type", _KAYNAK_TIPI),
    ("delivery_notes", "source_id"): ("source_type", _KAYNAK),
    ("entity_change_logs", "entity_id"): ("entity_type", _VARLIK),
    ("entity_contacts", "entity_id"): ("entity_type", _VARLIK),
    ("entity_notes", "entity_id"): ("entity_type", _VARLIK),
    ("entity_tasks", "entity_id"): ("entity_type", _VARLIK),
    ("farm_operations", "target_id"): ("kind", _TARLA_KIND),
    ("field_integration_events", "source_id"): ("source_type", _KAYNAK),
    ("finance_transactions", "reference_id"): ("reference_type", _REFERANS),
    ("financial_instruments", "entity_id"): ("entity_type", _VARLIK),
    ("herd_integration_events", "source_id"): ("source_type", _KAYNAK),
    ("notification_consents", "party_id"): ("party_type", _TARAF),
    ("payments", "entity_id"): ("entity_type", _VARLIK),
    ("payments", "reference_id"): ("reference_type", _REFERANS),
    ("policy_override_logs", "resource_id"): ("resource_type", _KAYNAK_TIPI),
    ("quotes", "converted_id"): ("converted_type", _DONUSUM),
    ("returns", "entity_id"): ("return_type", _IADE),
    ("returns", "source_id"): ("source_type", _KAYNAK),
    ("sales_orders", "converted_id"): ("converted_type", _DONUSUM),
    ("stock_movements", "reference_id"): ("reference_type", _REFERANS),
    ("whatsapp_pending_actions", "result_id"): ("action_type", _WA_ISLEM),
}

#: Hedefi BİLİNMEYEN ve bilerek olduğu gibi bırakılan sütunlar (gerekçesiyle).
BILINEN_COZUMSUZLER: dict[tuple[str, str], str] = {
    ("receivable_charge_periods", "installment_id"): (
        "Taksit kimliği; şemada taksit tablosu yok, kaynak sütun şema dışı bir "
        "kimlik taşıyor (late_fee_charge_engine, ölçüldü)."
    ),
}

#: Firma dışında TEKİL olan ve taşınamayan sütunlar. Kaynak firmanın satırları
#: (yumuşak imha veriyi SİLMEZ) hâlâ dururken aynı değeri ikinci kez yazmak
#: kısıtı ihlal ederdi. İkisi de tek kullanımlık SIRDIR (eşleştirme kodu özeti,
#: taslak işlem anahtarı); çakışan satır ATLANIR ve raporda sayılır.
KURESEL_TEKIL_ATLANIR: dict[str, tuple[str, ...]] = {
    "whatsapp_pairing_codes": ("code_digest",),
    "whatsapp_pending_actions": ("islem_anahtari",),
}

#: Firma dışında tekil olup sütunları TAŞINAN kimliklerden oluşan kısıtlar:
#: haritalama çakışmayı kendiliğinden çözer. Ölçüldü (0082).
KURESEL_TEKIL_HARITALANIR: frozenset[str] = frozenset(
    {"pos_system_customers", "supplier_price_import_lines"}
)

# ---------------------------------------------------------------------------
# DEĞER ÇÖZÜMÜ — ``_seri``nin tersi
# ---------------------------------------------------------------------------
def _deseri(deger: Any, sutun, diyalekt: str) -> Any:
    """Zip'teki JSON değerini sütun tipine göre Python değerine çevirir."""
    if deger is None:
        return None
    tip = sutun.type
    if isinstance(tip, Numeric):
        return Decimal(str(deger))
    if isinstance(tip, DateTime):
        zaman = datetime.fromisoformat(str(deger))
        if zaman.tzinfo is None:
            zaman = zaman.replace(tzinfo=timezone.utc)
        zaman = zaman.astimezone(timezone.utc)
        # SQLite damgayı naive UTC saklar (``utcnow`` öyle yazar); PostgreSQL'de
        # yalnız ``timestamptz`` sütunu farkındalık taşır.
        if diyalekt != "postgresql" or not getattr(tip, "timezone", False):
            return zaman.replace(tzinfo=None)
        return zaman
    if isinstance(tip, Date):
        return date.fromisoformat(str(deger))
    if isinstance(tip, Time):
        return saat.fromisoformat(str(deger))
    if isinstance(tip, Boolean):
        if deger in (0, 1):
            return bool(deger)
        return deger
    if isinstance(tip, LargeBinary):
        return base64.b64decode(str(deger))
    if isinstance(tip, JSON):
        if isinstance(deger, (dict, list)):
            return deger
        try:
            return json.loads(str(deger))
        except ValueError:
            # 5.1a ``_seri`` sözlüğü ``str()`` ile (Python repr) yazar; JSON
            # olarak okunamayan metin repr olarak denenir.
            try:
                return ast.literal_eval(str(deger))
            except (ValueError, SyntaxError):
                return deger
    return deger


def _ndjson(zf: zipfile.ZipFile, tablo: str) -> Iterator[dict[str, Any]]:
    with zf.open(f"tables/{tablo}.ndjson") as akis:
        for satir in akis:
            metin = satir.decode("utf-8").strip()
            if metin:
                yield json.loads(metin)


def _satir_say(zf: zipfile.ZipFile, tablo: str) -> int:
    sayi = 0
    with zf.open(f"tables/{tablo}.ndjson") as akis:
        for satir in akis:
            if satir.strip():
                sayi += 1
    return sayi


# ---------------------------------------------------------------------------
# MANİFEST DOĞRULAMA — HİÇBİR YAZMADAN ÖNCE
# ---------------------------------------------------------------------------
def manifest_dogrula(zf: zipfile.ZipFile, conn: Connection, md) -> dict[str, Any]:
    """Manifesti okur ve zip'i ŞEMAYA karşı doğrular; hata = hiç yazma yok.

    Üç kapı: manifest var mı, satır sayıları ndjson'la eşit mi, şema seviyesi
    çalışan veritabanının alembic başıyla aynı mı. Şema kapısı ZORUNLU: başka
    seviyeden bir zip'in sütunları bu şemayla uyuşmaz ve uyuşmazlık işlemin
    ortasında değil burada görünmeli.
    """
    adlar = set(zf.namelist())
    if "manifest.json" not in adlar:
        raise GeriYuklemeHatasi("RESTORE_MANIFEST_MISSING", 422, "Zip'te manifest.json yok")
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except ValueError as exc:
        raise GeriYuklemeHatasi(
            "RESTORE_MANIFEST_INVALID", 422, "manifest.json okunamadı"
        ) from exc
    for alan in ("schema_revision", "company_id", "table_order", "row_counts"):
        if alan not in manifest:
            raise GeriYuklemeHatasi(
                "RESTORE_MANIFEST_INVALID", 422, f"manifest.json alanı eksik: {alan}"
            )
    sema = _sema_seviyesi(conn, md)
    if manifest["schema_revision"] != sema:
        raise GeriYuklemeHatasi(
            "RESTORE_SCHEMA_MISMATCH",
            409,
            "Zip'in şema seviyesi çalışan veritabanıyla aynı değil",
            {"zip": manifest["schema_revision"], "database": sema},
        )
    kiraci = _kiraci_tablolari(md)
    zipte = set(manifest["table_order"])
    if zipte != set(kiraci):
        raise GeriYuklemeHatasi(
            "RESTORE_TABLE_SET_MISMATCH",
            422,
            "Zip'teki tablo kümesi şemanın kiracı tablolarıyla aynı değil",
            {"only_in_zip": sorted(zipte - kiraci), "only_in_schema": sorted(kiraci - zipte)},
        )
    sayilar = manifest["row_counts"]
    sapma: dict[str, dict[str, int]] = {}
    for tablo in manifest["table_order"]:
        if f"tables/{tablo}.ndjson" not in adlar:
            raise GeriYuklemeHatasi(
                "RESTORE_TABLE_FILE_MISSING", 422, f"Zip'te tablo dosyası yok: {tablo}"
            )
        gercek = _satir_say(zf, tablo)
        beklenen = int(sayilar.get(tablo, -1))
        if gercek != beklenen:
            sapma[tablo] = {"manifest": beklenen, "actual": gercek}
    if sapma:
        raise GeriYuklemeHatasi(
            "RESTORE_ROW_COUNT_MISMATCH",
            422,
            "Manifest satır sayıları ndjson içeriğiyle uyuşmuyor",
            sapma,
        )
    if f"companies/{manifest['company_id']}.json" not in adlar:
        raise GeriYuklemeHatasi(
            "RESTORE_COMPANY_FILE_MISSING", 422, "Zip'te firma satırı yok"
        )
    return manifest


# ---------------------------------------------------------------------------
# PLAN — sütun sınıflandırması, yansımadan
# ---------------------------------------------------------------------------
class _Plan:
    """Bir tablonun sütunlarının nasıl yeniden yazılacağı."""

    def __init__(self, tablo: Table, kiraci: frozenset[str]) -> None:
        self.tablo = tablo
        self.ad = tablo.name
        pk = list(tablo.primary_key.columns)
        self.tek_tamsayi_pk = (
            len(pk) == 1 and isinstance(pk[0].type, Integer) and pk[0].name == "id"
        )
        # KİMLİĞİ VERİTABANI ÜRETMEZ: PostgreSQL yansıması serial/identity
        # olmayan tamsayı PK'yi ``autoincrement=False`` verir (ölçüldü, 0082:
        # yalnız ``notifications_archive``; arşiv satırı özgün bildirimin
        # kimliğini taşır). Böyle tabloda ``yeni`` kipi kimliği kendisi üretir.
        # SQLite ``"auto"`` verir ve rowid her zaman atanır.
        self.kimlik_uretilir = self.tek_tamsayi_pk and pk[0].autoincrement is False
        self.fk_kiraci: list[tuple[str, str, bool]] = []  # (sütun, hedef, nullable)
        self.fk_kendine: list[tuple[str, bool]] = []
        self.fk_firma: list[str] = []
        self.fk_kullanici: list[tuple[str, bool]] = []
        fk_adlari: set[str] = set()
        for kisit in tablo.foreign_key_constraints:
            hedef = kisit.referred_table.name
            for sutun in kisit.columns:
                if sutun.name in fk_adlari:
                    continue
                fk_adlari.add(sutun.name)
                if hedef == "companies" or sutun.name == "company_id":
                    self.fk_firma.append(sutun.name)
                elif hedef == "app_users":
                    self.fk_kullanici.append((sutun.name, bool(sutun.nullable)))
                elif hedef == self.ad:
                    self.fk_kendine.append((sutun.name, bool(sutun.nullable)))
                elif hedef in kiraci:
                    self.fk_kiraci.append((sutun.name, hedef, bool(sutun.nullable)))
        if "company_id" in tablo.c and "company_id" not in fk_adlari:
            self.fk_firma.append("company_id")
        self.yumusak_dogrudan: list[tuple[str, str, bool]] = []
        self.yumusak_ayirt: list[tuple[str, str, dict[str, str | None], bool]] = []
        self.siniflandirilmamis: list[str] = []
        for sutun in tablo.c:
            if not sutun.name.endswith("_id") or sutun.name in fk_adlari or sutun.name == "company_id":
                continue
            anahtar = (self.ad, sutun.name)
            if anahtar in KULLANICI_SUTUNLARI or anahtar in METIN_KIMLIK_SUTUNLARI:
                continue
            if anahtar in BILINEN_COZUMSUZLER:
                continue
            if anahtar in DOGRUDAN_HEDEFLER:
                self.yumusak_dogrudan.append(
                    (sutun.name, DOGRUDAN_HEDEFLER[anahtar], bool(sutun.nullable))
                )
            elif anahtar in AYIRT_EDICI_HEDEFLER:
                ayirt, sozluk = AYIRT_EDICI_HEDEFLER[anahtar]
                self.yumusak_ayirt.append((sutun.name, ayirt, sozluk, bool(sutun.nullable)))
            else:
                self.siniflandirilmamis.append(sutun.name)


def sutun_plani(md, kiraci: frozenset[str]) -> dict[str, _Plan]:
    return {ad: _Plan(md.tables[ad], kiraci) for ad in kiraci}


def geri_yukleme_sirasi(md, kiraci: frozenset[str], planlar: dict[str, _Plan]) -> list[Table]:
    """Yazma sırası: FK kenarları KESİN, yumuşak referans kenarları TERCİH.

    Dışa aktarımın ``sort_tables`` sırası yalnız FK grafiğini bilir; yumuşak
    referanslar (``pos_system_customers.customer_id`` gibi, FK'sız) onun için
    görünmezdir ve hedef tablo SONRA gelebilir — o zaman kimlik haritası henüz
    yoktur. Bu sıralayıcı yumuşak kenarları da sayar ama onları KIRABİLİR:
    ölçüldü, 286 yumuşak kenar döngü kuruyor (``delivery_notes`` <->
    ``sales_orders`` gibi). Kahn algoritması FK bağımlılığı bitmiş tablolar
    arasından yumuşak bağımlılığı da bitmiş olanı seçer; hiçbiri yoksa en az
    bekleyeni alır ve o tablonun bekleyen yumuşak referansları ERTELENİR
    (``_ertelenenleri_bagla``). Sıra deterministiktir (ad sırası).
    """
    sert: dict[str, set[str]] = {ad: set() for ad in kiraci}
    yumusak: dict[str, set[str]] = {ad: set() for ad in kiraci}
    for ad in kiraci:
        for kisit in md.tables[ad].foreign_key_constraints:
            hedef = kisit.referred_table.name
            if hedef in kiraci and hedef != ad:
                sert[ad].add(hedef)
        plan = planlar[ad]
        for _, hedef, _ in plan.yumusak_dogrudan:
            if hedef in kiraci and hedef != ad:
                yumusak[ad].add(hedef)
        for _, _, sozluk, _ in plan.yumusak_ayirt:
            for hedef in sozluk.values():
                if hedef and hedef in kiraci and hedef != ad:
                    yumusak[ad].add(hedef)
    bitti: set[str] = set()
    sira: list[Table] = []
    kalan = set(kiraci)
    while kalan:
        hazir = sorted(ad for ad in kalan if sert[ad] <= bitti)
        if not hazir:
            raise GeriYuklemeHatasi(
                "RESTORE_FK_CYCLE", 500, "FK grafiğinde döngü: yazma sırası kurulamadı",
                sorted(kalan),
            )
        tam = [ad for ad in hazir if yumusak[ad] <= bitti]
        secilen = tam[0] if tam else min(hazir, key=lambda ad: (len(yumusak[ad] - bitti), ad))
        sira.append(md.tables[secilen])
        bitti.add(secilen)
        kalan.discard(secilen)
    return sira


def siniflandirilmamis_sutunlar(md, kiraci: frozenset[str]) -> list[tuple[str, str]]:
    """Sözlükte OLMAYAN ``*_id`` sütunları — test bunu BOŞ çiviler."""
    return sorted(
        (p.ad, s) for p in sutun_plani(md, kiraci).values() for s in p.siniflandirilmamis
    )


# ---------------------------------------------------------------------------
# GERİ YÜKLEME
# ---------------------------------------------------------------------------
class _Rapor:
    def __init__(self) -> None:
        self.tablolar: dict[str, dict[str, int]] = {}
        self.fk_sutunlari: set[tuple[str, str]] = set()
        self.yumusak_sutunlar: set[tuple[str, str]] = set()
        self.cozulemeyen: dict[tuple[str, str, str], int] = {}
        self.sarkan: dict[tuple[str, str], int] = {}
        self.nullanan: dict[tuple[str, str], int] = {}
        self.ertelenen: int = 0
        #: (tablo adı, yeni id, sütun, hedef tablo, eski hedef id, nullable)
        self.yumusak_bekleyen: list[tuple[str, int, str, str, int, bool]] = []
        self.uyelik_atlanan: list[int] = []
        self.uyelik_yazilan: int = 0
        self.ek_plani: list[tuple[str, str]] = []  # (zip üyesi, yeni göreli yol)

    def sozluk(self) -> dict[str, Any]:
        return {
            "tables": self.tablolar,
            "table_count": len(self.tablolar),
            "row_total": sum(t["rows"] for t in self.tablolar.values()),
            "skipped_total": sum(t["skipped"] for t in self.tablolar.values()),
            "fk_columns_remapped": len(self.fk_sutunlari),
            "soft_columns_remapped": len(self.yumusak_sutunlar),
            "soft_refs_unresolved": [
                {"table": t, "column": s, "discriminator": d, "rows": n}
                for (t, s, d), n in sorted(self.cozulemeyen.items())
            ],
            "soft_refs_dangling_kept": [
                {"table": t, "column": s, "rows": n} for (t, s), n in sorted(self.sarkan.items())
            ],
            "dangling_references_nulled": [
                {"table": t, "column": s, "rows": n} for (t, s), n in sorted(self.nullanan.items())
            ],
            "deferred_self_references": self.ertelenen,
            "memberships": {
                "restored": self.uyelik_yazilan,
                "skipped_user_ids": sorted(self.uyelik_atlanan),
            },
        }


def _firma_satiri(zf: zipfile.ZipFile, cid: int, diyalekt: str) -> dict[str, Any]:
    ham = json.loads(zf.read(f"companies/{cid}.json").decode("utf-8"))
    satir: dict[str, Any] = {}
    for sutun in companies.c:
        if sutun.name in ham:
            satir[sutun.name] = _deseri(ham[sutun.name], sutun, diyalekt)
    return satir


def _kaynak_firma_durumu(conn: Connection, cid: int):
    return (
        conn.execute(
            select(companies.c.id, companies.c.is_active).where(companies.c.id == cid)
        )
        .mappings()
        .first()
    )


def _artik_satirlar(conn: Connection, sirali: list[Table], cid: int) -> dict[str, int]:
    """``yerine`` kipi için: kaynak kimliğe ait satırı olan tablolar."""
    artik: dict[str, int] = {}
    for tablo in sirali:
        satirlar = conn.execute(
            select(tablo.c.company_id).where(tablo.c.company_id == cid)
        ).scalars().all()
        if satirlar:
            artik[tablo.name] = len(satirlar)
    return artik


def _kuresel_tekil_var(conn: Connection, tablo: Table, sutun: str, deger: Any) -> bool:
    """Firma dışı tekil sütunda ``deger`` zaten var mı (kiracı yüklemi YOK — bilerek).

    Kısıt küreseldir: çakışma BAŞKA bir firmanın satırıyla da olur ve tam
    olarak o yüzden sorulur. Yalnız ``KURESEL_TEKIL_ATLANIR``daki iki sütun.
    """
    var = conn.execute(
        select(tablo.c[sutun]).where(tablo.c[sutun] == deger).limit(1)
    ).first()
    return var is not None


def geri_yukle(
    zip_yolu: Path,
    *,
    kip: str = "yeni",
    kuru_kosu: bool = False,
    operator_user_id: int | None = None,
) -> dict[str, Any]:
    """Zip'i çalışan veritabanına TEK işlemde yazar ve rapor döndürür.

    ``kuru_kosu=True`` aynı yolu sonuna kadar yürüyüp işlemi geri alır; rapor
    "yazılsaydı ne olurdu"yu söyler ve hiçbir satır/dosya kalıcı olmaz.
    """
    if kip not in KIPLER:
        raise GeriYuklemeHatasi("RESTORE_MODE_INVALID", 422, f"Geçersiz kip: {kip}")
    if not zipfile.is_zipfile(zip_yolu):
        raise GeriYuklemeHatasi("RESTORE_ZIP_INVALID", 422, "Dosya geçerli bir zip değil")
    basladi = time.perf_counter()
    rapor = _Rapor()
    with zipfile.ZipFile(zip_yolu) as zf:
        try:
            with engine.begin() as conn:
                md = _yansit(conn)
                manifest = manifest_dogrula(zf, conn, md)
                kiraci = _kiraci_tablolari(md)
                planlar = sutun_plani(md, kiraci)
                # Dışa aktarımın sırası (FK) `_tablo_sirasi` ile aynı evrende;
                # yazma sırası yumuşak kenarları da sayar (bkz. docstring).
                _tablo_sirasi(md, kiraci)
                sirali = geri_yukleme_sirasi(md, kiraci, planlar)
                diyalekt = conn.dialect.name
                eski_cid = int(manifest["company_id"])

                yeni_cid = _firmayi_yaz(conn, zf, kip, eski_cid, sirali, diyalekt)
                haritalar: dict[str, dict[int, int]] = {}
                mevcut_kullanicilar = {
                    int(u) for u in conn.execute(select(users.c.id)).scalars().all()
                }
                for tablo in sirali:
                    if tablo.name == "user_company_memberships":
                        # Üyelik ayrı yoldan: var olan kullanıcılar + operatör.
                        continue
                    _tabloyu_yaz(
                        conn, zf, tablo, planlar[tablo.name], kip, yeni_cid,
                        haritalar, mevcut_kullanicilar, diyalekt, rapor,
                    )
                if kip == "yeni":
                    _ertelenenleri_bagla(conn, md, yeni_cid, haritalar, rapor)
                _uyelikleri_yaz(conn, zf, kip, yeni_cid, mevcut_kullanicilar, operator_user_id, rapor)
                if kip == "yerine" and diyalekt == "postgresql":
                    _sirayi_ilerlet(conn, sirali, planlar)
                sonuc = rapor.sozluk()
                sonuc.update(
                    {
                        "mode": kip,
                        "dry_run": kuru_kosu,
                        "source_company_id": eski_cid,
                        "company_id": yeni_cid,
                        "schema_revision": manifest["schema_revision"],
                        "app_version": manifest.get("app_version"),
                        "id_maps": {t: len(h) for t, h in haritalar.items()},
                    }
                )
                if kuru_kosu:
                    raise _KuruKosuBitti(sonuc)
        except _KuruKosuBitti as bitti:
            sonuc = bitti.rapor
            sonuc["attachments"] = {
                "planned": len(rapor.ek_plani), "copied": 0, "missing_in_zip": 0,
            }
            sonuc["duration_ms"] = int((time.perf_counter() - basladi) * 1000)
            return sonuc
        # --- COMMIT EDİLDİ; ekler şimdi ------------------------------------
        sonuc["attachments"] = _ekleri_kopyala(zf, rapor.ek_plani)
    sonuc["duration_ms"] = int((time.perf_counter() - basladi) * 1000)
    return sonuc


def _firmayi_yaz(conn, zf, kip, eski_cid, sirali, diyalekt) -> int:
    satir = _firma_satiri(zf, eski_cid, diyalekt)
    kaynak = _kaynak_firma_durumu(conn, eski_cid)
    if kip == "yerine":
        if kaynak is None:
            raise GeriYuklemeHatasi(
                "RESTORE_SOURCE_MISSING", 404, "Kaynak firma veritabanında yok; 'yeni' kipini kullanın"
            )
        if bool(kaynak["is_active"]):
            raise GeriYuklemeHatasi(
                "RESTORE_SOURCE_ACTIVE", 409, "Kaynak firma aktif; aktif bir kiracının üstüne yazılmaz"
            )
        artik = _artik_satirlar(conn, sirali, eski_cid)
        if artik:
            raise GeriYuklemeHatasi(
                "RESTORE_SOURCE_ROWS_PRESENT",
                409,
                "Kaynak firmanın satırları hâlâ duruyor; yerinde geri yükleme yalnız boş kimliğe yapılır",
                artik,
            )
        degerler = {k: v for k, v in satir.items() if k != "id"}
        degerler["is_active"] = True
        # SET listesi ÇALIŞTIRMA parametresinden gelir (``values(**)`` DEĞİL):
        # sütun kümesi zip'ten türer ve Core envanteri ``**`` açılımını göremez.
        conn.execute(update(companies).where(companies.c.id == eski_cid), degerler)
        return eski_cid
    degerler = {k: v for k, v in satir.items() if k != "id"}
    degerler["is_active"] = True
    if not degerler.get("created_at"):
        degerler["created_at"] = utcnow()
    return int(conn.execute(insert(companies), degerler).inserted_primary_key[0])


def _tabloyu_yaz(conn, zf, tablo, plan, kip, yeni_cid, haritalar,
                 mevcut_kullanicilar, diyalekt, rapor) -> None:
    ad = tablo.name
    harita: dict[int, int] = {}
    if plan.tek_tamsayi_pk:
        haritalar[ad] = harita
    ertelenen: list[tuple[int, dict[str, int]]] = []  # (yeni id, {sütun: eski hedef})
    yazilan = 0
    atlanan = 0
    tekil = KURESEL_TEKIL_ATLANIR.get(ad, ())
    sonraki_kimlik = (_en_buyuk_kimlik(conn, tablo) or 0) + 1 if (kip == "yeni" and plan.kimlik_uretilir) else None
    for ham in _ndjson(zf, ad):
        satir = {k: _deseri(v, tablo.c[k], diyalekt) for k, v in ham.items() if k in tablo.c}
        eski_id = satir.get("id") if plan.tek_tamsayi_pk else None
        bekleyen: dict[str, int] = {}
        # 1) firma
        for sutun in plan.fk_firma:
            satir[sutun] = yeni_cid
        if kip == "yeni":
            # 2) kiracı FK'ları
            for sutun, hedef, bos_olur in plan.fk_kiraci:
                satir[sutun] = _haritala(
                    satir.get(sutun), haritalar.get(hedef, {}), ad, sutun, bos_olur, rapor, zorunlu_hata=True
                )
                rapor.fk_sutunlari.add((ad, sutun))
            # 3) kullanıcı FK'ları
            for sutun, bos_olur in plan.fk_kullanici:
                deger = satir.get(sutun)
                if deger is not None and int(deger) not in mevcut_kullanicilar:
                    if not bos_olur:
                        raise GeriYuklemeHatasi(
                            "RESTORE_USER_MISSING", 409,
                            f"{ad}.{sutun} artık var olmayan bir kullanıcıya bağlı (id={deger})",
                        )
                    satir[sutun] = None
                    rapor.nullanan[(ad, sutun)] = rapor.nullanan.get((ad, sutun), 0) + 1
            # 4) kendine referans: şimdilik NULL, sonra bağlanır
            for sutun, bos_olur in plan.fk_kendine:
                deger = satir.get(sutun)
                if deger is None:
                    continue
                rapor.fk_sutunlari.add((ad, sutun))
                if int(deger) in harita:
                    satir[sutun] = harita[int(deger)]
                elif bos_olur:
                    satir[sutun] = None
                    bekleyen[sutun] = int(deger)
                else:
                    raise GeriYuklemeHatasi(
                        "RESTORE_SELF_REFERENCE_UNRESOLVED", 409,
                        f"{ad}.{sutun} henüz yazılmamış bir satıra zorunlu olarak bağlı",
                    )
            # 5) yumuşak referanslar
            yumusak_hedefler: list[tuple[str, str, bool]] = list(plan.yumusak_dogrudan)
            for sutun, ayirt, sozluk, bos_olur in plan.yumusak_ayirt:
                deger = satir.get(sutun)
                if deger is None:
                    continue
                tur = str(satir.get(ayirt) or "")
                if tur not in sozluk:
                    anahtar = (ad, sutun, tur)
                    rapor.cozulemeyen[anahtar] = rapor.cozulemeyen.get(anahtar, 0) + 1
                    continue
                hedef = sozluk[tur]
                if hedef is not None:
                    yumusak_hedefler.append((sutun, hedef, bos_olur))
            sonraya: list[tuple[str, str, int, bool]] = []
            for sutun, hedef, bos_olur in yumusak_hedefler:
                deger = satir.get(sutun)
                if deger is None:
                    continue
                rapor.yumusak_sutunlar.add((ad, sutun))
                if hedef == ad:
                    if int(deger) in harita:
                        satir[sutun] = harita[int(deger)]
                    else:
                        satir[sutun] = None
                        bekleyen[sutun] = int(deger)
                    continue
                if hedef not in haritalar:
                    # HEDEF HENÜZ YAZILMADI (yumuşak kenar döngüde kırıldı).
                    # FK kısıtı yok: nullable ise NULL, değilse ESKİ değer
                    # geçici olarak yazılır; `_ertelenenleri_bagla` düzeltir.
                    if plan.tek_tamsayi_pk:
                        sonraya.append((sutun, hedef, int(deger), bos_olur))
                        satir[sutun] = None if bos_olur else int(deger)
                    else:
                        rapor.sarkan[(ad, sutun)] = rapor.sarkan.get((ad, sutun), 0) + 1
                    continue
                satir[sutun] = _haritala(
                    deger, haritalar[hedef], ad, sutun, bos_olur, rapor, zorunlu_hata=False
                )
            if plan.tek_tamsayi_pk:
                satir.pop("id", None)
            if sonraki_kimlik is not None:
                satir["id"] = sonraki_kimlik
                sonraki_kimlik += 1
        # 6) ek yolu: yeni firma/iş emri kimliğiyle
        if ad == "work_order_attachments" and satir.get("storage_path"):
            eski_yol = str(satir["storage_path"])
            uye = f"attachments/{ham.get('work_order_id')}/{Path(eski_yol).name}"
            yeni_yol = f"attachments/{yeni_cid}/{satir.get('work_order_id')}/{Path(eski_yol).name}"
            satir["storage_path"] = yeni_yol
            rapor.ek_plani.append((uye, yeni_yol))
        # 7) küresel tekil çakışması
        if any(_kuresel_tekil_var(conn, tablo, s, satir.get(s)) for s in tekil):
            atlanan += 1
            continue
        # Satır ÇALIŞTIRMA parametresidir (``values(**)`` DEĞİL); sütun kümesi
        # zip'ten türer, tablo yansımadan gelir.
        sonuc = conn.execute(insert(tablo), satir)
        yazilan += 1
        if plan.tek_tamsayi_pk and eski_id is not None:
            yeni_id = int(sonuc.inserted_primary_key[0]) if kip == "yeni" else int(eski_id)
            harita[int(eski_id)] = yeni_id
            if bekleyen:
                ertelenen.append((yeni_id, bekleyen))
            for sutun, hedef, eski_hedef, bos_olur in sonraya:
                rapor.yumusak_bekleyen.append((ad, yeni_id, sutun, hedef, eski_hedef, bos_olur))
    # ertelenen kendine referanslar
    for yeni_id, bekleyen in ertelenen:
        degerler = {}
        for sutun, eski_hedef in bekleyen.items():
            if eski_hedef in harita:
                degerler[sutun] = harita[eski_hedef]
            else:
                rapor.nullanan[(ad, sutun)] = rapor.nullanan.get((ad, sutun), 0) + 1
        if degerler:
            _satiri_guncelle(conn, tablo, yeni_cid, yeni_id, degerler)
            rapor.ertelenen += 1
    rapor.tablolar[ad] = {"rows": yazilan, "skipped": atlanan}


def _satiri_guncelle(conn, tablo, yeni_cid, yeni_id, degerler) -> None:
    """Bu işlemde yazılmış TEK satırın ertelenen referans sütunlarını bağlar.

    Yüklem yeni firma + yeni kimliktir; SET listesi çalıştırma parametresi.
    """
    conn.execute(
        update(tablo).where(tablo.c.company_id == yeni_cid, tablo.c.id == yeni_id),
        degerler,
    )


def _ertelenenleri_bagla(conn, md, yeni_cid, haritalar, rapor) -> None:
    """Döngü yüzünden hedefi sonra yazılan yumuşak referansları bağlar."""
    for ad, yeni_id, sutun, hedef, eski_hedef, bos_olur in rapor.yumusak_bekleyen:
        harita = haritalar.get(hedef, {})
        if eski_hedef in harita:
            _satiri_guncelle(conn, md.tables[ad], yeni_cid, yeni_id, {sutun: harita[eski_hedef]})
            rapor.ertelenen += 1
        elif bos_olur:
            rapor.nullanan[(ad, sutun)] = rapor.nullanan.get((ad, sutun), 0) + 1
        else:
            rapor.sarkan[(ad, sutun)] = rapor.sarkan.get((ad, sutun), 0) + 1


def _haritala(deger, harita, ad, sutun, bos_olur, rapor, *, zorunlu_hata: bool):
    if deger is None:
        return None
    eski = int(deger)
    if eski in harita:
        return harita[eski]
    if not bos_olur and zorunlu_hata:
        raise GeriYuklemeHatasi(
            "RESTORE_DANGLING_REFERENCE", 409,
            f"{ad}.{sutun} zip'te olmayan bir satıra zorunlu olarak bağlı (id={eski})",
        )
    if bos_olur:
        rapor.nullanan[(ad, sutun)] = rapor.nullanan.get((ad, sutun), 0) + 1
        return None
    # Zorunlu YUMUŞAK referans: kısıt yok, eski değer olduğu gibi kalır.
    rapor.sarkan[(ad, sutun)] = rapor.sarkan.get((ad, sutun), 0) + 1
    return eski


def _uyelikleri_yaz(conn, zf, kip, yeni_cid, mevcut_kullanicilar, operator_user_id, rapor) -> None:
    """Var olan kullanıcıların üyeliğini geri getirir; operatörü ekler.

    ZİP KULLANICI E-POSTASI TAŞIMAZ (5.1a ``app_users``ı dışa aktarmaz, o
    platform tablosudur). Eşleme bu yüzden KİMLİKLE yapılır — zip aynı
    platformdan geldiği için kimlik geçerlidir; kullanıcı silinmişse üyelik
    yazılmaz ve kimliği raporda durur. E-postayla eşleme, zip biçimi e-posta
    taşımaya başlarsa mümkün olur (açık karar).
    """
    yazilanlar: set[int] = set()
    for ham in _ndjson(zf, "user_company_memberships"):
        uid = int(ham["user_id"])
        if uid not in mevcut_kullanicilar:
            rapor.uyelik_atlanan.append(uid)
            continue
        if uid in yazilanlar:
            continue
        # `yerine` kimliği KORUR (PostgreSQL'de ölçüldü: kimlik verilmezse sıra
        # yeni bir değer üretir; SQLite'ın rowid yeniden kullanımı bunu gizler).
        degerler: dict[str, Any] = {
            "user_id": uid, "company_id": yeni_cid,
            "is_default": bool(ham.get("is_default", False)), "created_at": utcnow(),
        }
        if kip == "yerine" and ham.get("id") is not None:
            degerler["id"] = int(ham["id"])
        conn.execute(insert(memberships).values(company_id=yeni_cid), degerler)
        yazilanlar.add(uid)
    if operator_user_id is not None and int(operator_user_id) not in yazilanlar:
        conn.execute(
            insert(memberships).values(
                user_id=int(operator_user_id), company_id=yeni_cid,
                is_default=False, created_at=utcnow(),
            )
        )
        yazilanlar.add(int(operator_user_id))
    rapor.uyelik_yazilan = len(yazilanlar)
    rapor.tablolar["user_company_memberships"] = {"rows": len(yazilanlar), "skipped": len(rapor.uyelik_atlanan)}


def _sirayi_ilerlet(conn, sirali, planlar) -> None:
    """``yerine`` kipinde açık kimlikle yazılan serial sütunların sırasını ilerletir.

    PostgreSQL'de açık ``id`` yazmak sıralayıcıyı OYNATMAZ; sonraki normal
    ekleme aynı kimliği üretip düşerdi. Yalnız ``id`` birincil anahtarlı
    tablolar; ``setval`` en büyük kimliğe çekilir.
    """
    for tablo in sirali:
        plan = planlar[tablo.name]
        if not plan.tek_tamsayi_pk or plan.kimlik_uretilir:
            continue
        en_buyuk = _en_buyuk_kimlik(conn, tablo)
        if en_buyuk is None:
            continue
        conn.execute(
            select(func.setval(func.pg_get_serial_sequence(tablo.name, "id"), int(en_buyuk)))
        )


def _en_buyuk_kimlik(conn, tablo) -> int | None:
    """Tablodaki en büyük ``id`` (firma süzgeci YOK — kimlik uzayı küreseldir)."""
    deger = conn.execute(select(func.max(tablo.c.id))).scalar()
    return int(deger) if deger is not None else None


def _ekleri_kopyala(zf: zipfile.ZipFile, plan: list[tuple[str, str]]) -> dict[str, int]:
    kok = _depo_koku()
    adlar = set(zf.namelist())
    kopyalanan = 0
    eksik = 0
    for uye, yeni_yol in plan:
        if uye not in adlar:
            eksik += 1
            continue
        hedef = _ek_yolu(kok, yeni_yol)
        if hedef is None:
            eksik += 1
            continue
        hedef.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(uye) as kaynak, hedef.open("wb") as cikis:
            while True:
                blok = kaynak.read(64 * 1024)
                if not blok:
                    break
                cikis.write(blok)
        kopyalanan += 1
    return {"planned": len(plan), "copied": kopyalanan, "missing_in_zip": eksik}
