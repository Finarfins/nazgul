"""GET izin envanteri kapısı (güvenlik denetimi PR-1).

BU DOSYA YALNIZ BİR KAPIDIR: uygulama davranışını değiştirmez,
``required_permission``ın çalışma zamanı davranışına dokunmaz. Bugün DOĞRU olan
sınıflandırmayı dondurur.

--- HANGİ BOŞLUĞU KAPATIYOR -----------------------------------------------

``test_route_security_contracts.py`` zaten güçlü bir kapı: ``read``e, public'e
ya da platform kapsamına düşen HER rota için ``ROUTE_REASONS`` içinde açık bir
gerekçe şart koşuyor (``app/route_security_contracts.py``).

Ama bir GET, ``required_permission`` içindeki bir ÖNEK kuralına düşerse gerçek
bir izin alır (``farm.view``, ``finance``, ``users``…) ve o kapı SESSİZ KALIR —
gerekçe istenmez. Yani yeni bir uç, adı bir öneke benzediği için hiç
düşünülmemiş bir role bağlanabilir.

Bu teorik değil: ``/api/field-safety`` ucu tam bu şekilde ``/api/field`` önekine
düşüp ``field_service`` iznine bağlanmıştı; tarla modülünde aynı tuzağa İKİ KEZ
düşüldü. Bugün doğru izinde (``farm.view``) ve bu envanter onu orada kilitliyor.

Bugün 156 GET/HEAD işleminin 84'ü ``read`` (gerekçe kapısı onları zaten
koruyor), 72'si önek kuralından devralıyor — bu dosyaya kadar onları koruyan
hiçbir kapı yoktu.

--- KAPI NASIL KIRILIR ----------------------------------------------------

* Yeni bir GET ucu eklenince: envanterde olmadığı için ``missing`` listesinde
  çıkar ve hata mesajı DEVRALDIĞI İZNİ yazar — "bu bilinçli mi?" sorusu
  incelemeye zorlanır.
* Bir ucun izni değişince ``changed``, bir uç silinince ``stale`` listesinde
  çıkar.
* Sayı ve parmak izi ayrıca donduruldu; toplu bir kayma tek satırda görünür.

--- BU KAPI NE İDDİA ETMEZ ------------------------------------------------

1. İznin DOĞRU olduğunu iddia etmez, yalnız BİLİNÇLİ olduğunu. Bir girdiyi
   değiştirmek serbesttir — ama sessizce değil.
2. ``{kind}`` gibi yol değişkeni izni belirleyen rotalarda burada kayıtlı
   değer, ŞABLONUN kendisi çözüldüğündeki sonuçtur. O rotaların somut
   değerlere göre izinleri ``test_route_security_contracts.py`` içindeki
   ``permission_cases`` ile ayrıca çivilenir; bu envanter onun yerine geçmez.
"""
from __future__ import annotations

import os
import tempfile
from hashlib import sha256

import pytest

#: (metot, yol) -> ``required_permission`` bugün ne döndürüyor.
#:
#: ``public`` değeri, yolun ``PUBLIC_API`` içinde olduğunu gösterir (kimlik
#: doğrulaması öncesi); diğerleri ``app/auth.py::required_permission``
#: sonucudur.
EXPECTED_GET_PERMISSIONS: dict[tuple[str, str], str] = {
    # --- farm.view — `_FARM_PATH_PREFIXES` önek kuralından devralınıyor.
    ("GET", "/api/crop-seasons"): "farm.view",
    ("GET", "/api/crop-seasons/{season_id}"): "farm.view",
    ("GET", "/api/farm-parcels"): "farm.view",
    ("GET", "/api/farm-parcels/{parcel_id}"): "farm.view",
    ("GET", "/api/farm-parcels/{parcel_id}/timeline"): "farm.view",
    ("GET", "/api/farms"): "farm.view",
    ("GET", "/api/farms/{farm_id}"): "farm.view",
    ("GET", "/api/field-activities"): "farm.view",
    ("GET", "/api/field-activities/{activity_id}"): "farm.view",
    ("GET", "/api/field-dashboard"): "farm.view",
    ("GET", "/api/field-harvest-decision"): "farm.view",
    ("GET", "/api/field-harvests"): "farm.view",
    # Kantar fişi listesi (göç 20260904_0069). `/api/field-harvests` ile
    # BAŞLAMIYOR (sondaki `s`); `_FARM_PATH_PREFIXES`e ayrıca yazıldı, yoksa
    # `field_service`e düşerdi (ölçüldü). Maruziyet: fişin brütü, girilen
    # birim, katsayı, taban miktar, kağıdın neti, kesintiler ve hasat başına
    # üç değerli iki bayrak — hasat listesinin yanında duran, aynı role
    # bağlı bir okuma. YAZMA `farm.manage` ister ve bu envanterde DEĞİLDİR.
    ("GET", "/api/field-harvest-tickets"): "farm.view",
    # Outbox okuma yüzeyi (FIELD_STOK_OUTBOX açılış koşulu 2). AYNI TUZAK:
    # "/api/field" ile başlıyor, `_FARM_PATH_PREFIXES`e yazılmasaydı
    # `field_service` iznine düşerdi. Kuyruk TARLA verisidir; okuması
    # `farm.view`, yani parsel/sezon/hasat listeleriyle AYNI role bağlı.
    ("GET", "/api/field-integration-events"): "farm.view",
    ("GET", "/api/field-integration-events/summary"): "farm.view",
    # DİKKAT: `/api/field` önekine benziyor ama TARLA ucu. Önek listesine
    # eklenmeseydi `field_service` iznine düşerdi — bu tuzak iki kez yaşandı.
    ("GET", "/api/field-safety"): "farm.view",
    ("GET", "/api/field-tasks"): "farm.view",
    # BKÜ kataloğu (göç 20260901_0063). `/api/field` önekiyle BAŞLAMIYOR, yani
    # saha servis tuzağına düşmezdi — ama önek listesinde olmasaydı genel
    # `read` iznine düşerdi ve yasal bekleme sürelerini okuma yetkisi olan
    # herkes değiştirebilirdi. Okuma tarafı diğer tarla listeleriyle AYNI rol.
    ("GET", "/api/plant-protection-products"): "farm.view",
    ("GET", "/api/plant-protection-products/{ppp_id}"): "farm.view",
    # Ekim-arası bekleme kataloğu (göç 20260907_0072). ÖNEK EŞLEŞMESİ
    # ÖLÇÜLDÜ: "...-plantbacks" yukarıdaki "...-products" önekinin ALTINA
    # DÜŞMEZ ('l'/'r' harfinde ayrılıyorlar), yani `_FARM_PATH_PREFIXES`e
    # KENDİ satırı yazılmasaydı genel `read` iznine düşerdi. Okuması diğer
    # tarla listeleriyle AYNI rol; yeni bir izin ailesi AÇILMADI.
    ("GET", "/api/plant-protection-plantbacks"): "farm.view",
    ("GET", "/api/plant-protection-plantbacks/{plantback_id}"): "farm.view",

    # --- field_service — `/api/field` önekinden; saha çevrimdışı anlık görüntüsü.
    ("GET", "/api/field/snapshot"): "field_service",

    # --- finance — `/api/finance`, `/api/harvest-scheduling` ve
    #     `/api/payment-allocations/reconciliation` öneklerinden.
    ("GET", "/api/finance/accounts"): "finance",
    ("GET", "/api/finance/instruments"): "finance",
    ("GET", "/api/finance/late-fees/charges/{document_id}"): "finance",
    ("GET", "/api/finance/late-fees/preview"): "finance",
    ("GET", "/api/finance/summary"): "finance",
    ("GET", "/api/finance/transactions"): "finance",
    ("GET", "/api/harvest-scheduling/calendars"): "finance",
    ("GET", "/api/harvest-scheduling/preview"): "finance",
    ("GET", "/api/harvest-scheduling/regions"): "finance",
    ("GET", "/api/harvest-scheduling/rules"): "finance",
    ("GET", "/api/harvest-scheduling/rules/conflicts"): "finance",
    ("GET", "/api/payment-allocations/reconciliation"): "finance",

    # --- herd.view — `_HERD_PATH_PREFIXES` önek kuralından devralınıyor.
    ("GET", "/api/animal-births"): "herd.view",
    ("GET", "/api/animal-breedings"): "herd.view",
    ("GET", "/api/animal-breedings/{breeding_id}"): "herd.view",
    ("GET", "/api/animal-groups"): "herd.view",
    ("GET", "/api/animal-groups/{group_id}"): "herd.view",
    ("GET", "/api/animal-movements"): "herd.view",
    # Tedavi defteri (göç 20260908_0074). ÖNEK EŞLEŞMESİ ÖLÇÜLDÜ:
    # "/api/animal-treatments" "/api/animals" önekinin ALTINA DÜŞMEZ
    # ('animal' sonrası 's' değil '-' geliyor), yani `_HERD_PATH_PREFIXES`e
    # KENDİ satırı yazılmasaydı genel `read` iznine düşerdi. OKUMASI diğer
    # hayvancılık listeleriyle AYNI rol; YAZMASI ise `herd.health`tir (aşı
    # kaydıyla aynı kapı) ve o ayrım `test_route_security_contracts`ta
    # ADIYLA çivili.
    ("GET", "/api/animal-treatments"): "herd.view",
    ("GET", "/api/animal-treatments/{treatment_id}"): "herd.view",
    # Karantina defteri (göç 20260909_0075). ÖNEK EŞLEŞMESİ ÖLÇÜLDÜ:
    # "/api/animal-quarantines" "/api/animals" önekinin ALTINA DÜŞMEZ
    # ('animal' sonrası 's' değil '-' geliyor), yani `_HERD_PATH_PREFIXES`e
    # KENDİ satırı yazılmasaydı genel `read` iznine düşerdi ve OKUMA yetkisi
    # olan herkes bir hayvanı karantinaya alıp ÇIKARABİLİRDİ. OKUMASI diğer
    # hayvancılık listeleriyle AYNI rol; YAZMASI (açma VE kapatma)
    # `herd.health`tir — karantina bir SAĞLIK OLAYIDIR.
    ("GET", "/api/animal-quarantines"): "herd.view",
    ("GET", "/api/animal-quarantines/{quarantine_id}"): "herd.view",
    ("GET", "/api/animal-vaccinations"): "herd.view",
    ("GET", "/api/animal-weights"): "herd.view",
    ("GET", "/api/animals"): "herd.view",
    ("GET", "/api/animals/{animal_id}"): "herd.view",
    ("GET", "/api/herd-dashboard"): "herd.view",
    ("GET", "/api/herd-fertility"): "herd.view",
    ("GET", "/api/milk-yields"): "herd.view",
    ("GET", "/api/vaccination-calendar"): "herd.view",
    # Veteriner ilaç kataloğu (göç 20260908_0074). Aynı önek gerekçesi:
    # "/api/vet-drugs" hiçbir mevcut önekle eşleşmiyor.
    ("GET", "/api/vet-drugs"): "herd.view",
    ("GET", "/api/vet-drugs/{drug_id}"): "herd.view",

    # --- payments — `/api/payments` ve `/api/receivables` öneklerinden.
    ("GET", "/api/payments"): "payments",
    ("GET", "/api/payments/accounts"): "payments",
    ("GET", "/api/payments/summary"): "payments",
    ("GET", "/api/receivables"): "payments",

    # --- public — kimlik doğrulaması ÖNCESİ; kiracı bağlamı yok.
    ("GET", "/api/auth/verify-email"): "public",
    ("GET", "/api/health"): "public",
    ("GET", "/api/live"): "public",
    ("GET", "/api/ready"): "public",

    # --- purchases — `/api/purchase-comparison` ve tedarikçi fiyat okuma önekleri.
    ("GET", "/api/products/{product_id}/supplier-prices"): "purchases",
    # MÜSTAHSİL MAKBUZU (göç 0070). Okuma da `purchases`: makbuz çiftçiye
    # ödenen birim fiyatı ve stopajı taşır, yani tedarikçi MALİYETİDİR.
    # `read` olsaydı her rol alım fiyatlarını görürdü.
    ("GET", "/api/producer-receipts"): "purchases",
    ("GET", "/api/producer-receipts/{receipt_id}"): "purchases",
    # D2 (göç 20260906_0071). İKİSİ DE `purchases` ve bu BİLİNÇLİDİR:
    # avans listesi çiftçiye ne ödendiğini, vergi defteri makbuzun stopaj
    # kesintisini gösterir. `/api/suppliers` öneki `read`in ALTINDA
    # olduğundan avans ucu ayrıca yazıldı — yazılmasaydı `read`e düşerdi.
    ("GET", "/api/suppliers/{supplier_id}/advances"): "purchases",
    ("GET", "/api/tax-liabilities"): "purchases",
    ("GET", "/api/purchase-comparison"): "purchases",
    ("GET", "/api/purchase-comparison/dashboard"): "purchases",
    ("GET", "/api/purchase-comparison/products/{product_id}/analysis"): "purchases",
    ("GET", "/api/purchase-comparison/reorder-suggestions"): "purchases",
    ("GET", "/api/purchase-comparison/spend-analytics"): "purchases",
    ("GET", "/api/purchase-comparison/supplier-scorecard"): "purchases",
    ("GET", "/api/supplier-prices/history"): "purchases",

    # --- read — oturum açan HER rol okuyabilir.
    #     Bunların tamamı ayrıca `ROUTE_REASONS` içinde gerekçeli olmak
    #     zorunda; aşağıdaki `test_read_get_routes_still_carry_a_review_reason`
    #     iki kapıyı birbirine bağlıyor.
    ("GET", "/api/analytics/seasonal-plan"): "read",
    ("GET", "/api/auth/me"): "read",
    ("GET", "/api/branches"): "read",
    ("GET", "/api/companies"): "read",
    # Gerçek Maliyet V1 (mobil-erp#24). `read` DEĞİL `finance`: oran bir
    # PARA TANIMI ve okunması firmanın maliyet yapısını açar. Genel `read`e
    # düşselerdi girdi giren depo rolü de görebilirdi.
    ("GET", "/api/cost-rates"): "finance",
    ("GET", "/api/cost-rates/{rate_id}"): "finance",
    ("GET", "/api/company-settings"): "read",
    ("GET", "/api/customers"): "read",
    ("GET", "/api/customers/{customer_id}"): "read",
    ("GET", "/api/customers/{customer_id}/documents"): "read",
    ("GET", "/api/customers/{customer_id}/statement"): "read",
    ("GET", "/api/customers/{customer_id}/statement.pdf"): "read",
    ("GET", "/api/dashboard"): "read",
    ("GET", "/api/demo/summary"): "read",
    ("GET", "/api/documents/{kind}/{document_id}/pdf"): "read",
    ("GET", "/api/documents/{kind}/{document_id}/xlsx"): "read",
    ("GET", "/api/exchange-rates"): "read",
    # Uygulama Kayıt Çizelgesi. `/api/exports/` altında olduğu için genel
    # `read` kuralına düşer; ASIL kapı handler'daki `farm.view`. Yol
    # `/api/field-…` seçilseydi `_FARM_PATH_PREFIXES`e eklenmediği sürece
    # sessizce `field_service`e düşerdi (bkz. /api/field-safety olayı).
    ("GET", "/api/exports/producer-logbook"): "read",
    ("GET", "/api/exports/producer-logbook.xlsx"): "read",
    ("GET", "/api/exports/products.xlsx"): "read",
    ("GET", "/api/exports/warehouse-count-variance.xlsx"): "read",
    ("GET", "/api/imports/customers/template.xlsx"): "read",
    ("GET", "/api/imports/products/template.xlsx"): "read",
    ("GET", "/api/imports/suppliers/template.xlsx"): "read",
    ("GET", "/api/invoices"): "read",
    ("GET", "/api/invoices/{invoice_id}"): "read",
    ("GET", "/api/invoices/{invoice_id}/einvoice/status"): "read",
    ("GET", "/api/invoices/{invoice_id}/history"): "read",
    ("GET", "/api/invoices/{invoice_id}/pdf"): "read",
    ("GET", "/api/machines"): "read",
    ("GET", "/api/machines/{machine_id}"): "read",
    ("GET", "/api/machines/{machine_id}/hour-readings"): "read",
    ("GET", "/api/machines/{machine_id}/ownership-history"): "read",
    ("GET", "/api/notifications"): "read",
    ("GET", "/api/notifications/consents"): "read",
    ("GET", "/api/notifications/consents/{consent_id}/events"): "read",
    ("GET", "/api/notifications/counters"): "read",
    ("GET", "/api/notifications/outbox"): "read",
    ("GET", "/api/notifications/rules"): "read",
    ("GET", "/api/notifications/templates"): "read",
    ("GET", "/api/notifications/{notification_id}/preview"): "read",
    ("GET", "/api/orders"): "read",
    ("GET", "/api/orders/last-sale-price"): "read",
    ("GET", "/api/part-supersessions"): "read",
    ("GET", "/api/payment-allocations/charges/{receivable_charge_id}"): "read",
    ("GET", "/api/payment-allocations/engine-state"): "read",
    ("GET", "/api/payment-allocations/orders/{order_id}"): "read",
    ("GET", "/api/payment-allocations/payments/{payment_id}"): "read",
    # `read` görünüyor ama router AYRICA `require_platform_operator` uyguluyor
    # (admin + ortam allowlist'i). Bkz. `app/auth.py` içindeki açıklama.
    # Aynı gerekçe: firmasız denetim satırları tek bir kiracıya ait olmadığı
    # için tek bir kiracının yöneticisine de ait değil; gerçek kapı router'daki
    # `require_platform_operator`. Göç 20260812_0059 ile birlikte geldi.
    ("GET", "/api/platform/audit"): "read",
    ("GET", "/api/platform/backups"): "read",
    ("GET", "/api/platform/backups/{name}/download"): "read",
    ("GET", "/api/pos/lookup"): "read",
    ("GET", "/api/products"): "read",
    ("GET", "/api/products/stock/movements/all"): "read",
    ("GET", "/api/products/{product_id}"): "read",
    ("GET", "/api/products/{product_id}/barcode-label.pdf"): "read",
    ("GET", "/api/products/{product_id}/current"): "read",
    ("GET", "/api/products/{product_id}/label.pdf"): "read",
    # PARTİ DEFTERİ OKUMASI (1B-A). Ürün okumasının yetkisiyle AYNI: gösterdiği
    # şey stok bakiyesinin PARTİ KIRILIMIDIR ve o bakiyeyi zaten
    # `GET /api/products/{product_id}` gösteriyor. Ayrı bir yetkiye bağlamak
    # aynı olguyu iki farklı kapının arkasına koyardı.
    ("GET", "/api/products/{product_id}/lots"): "read",
    ("GET", "/api/products/{product_id}/qr.png"): "read",
    ("GET", "/api/products/{product_id}/warehouse-stock"): "read",
    ("GET", "/api/purchases"): "read",
    ("GET", "/api/purchases/last-purchase-price"): "read",
    ("GET", "/api/quick-pick"): "read",
    ("GET", "/api/search"): "read",
    ("GET", "/api/search/parts"): "read",
    ("GET", "/api/suppliers"): "read",
    ("GET", "/api/suppliers/{supplier_id}"): "read",
    ("GET", "/api/suppliers/{supplier_id}/documents"): "read",
    ("GET", "/api/suppliers/{supplier_id}/statement"): "read",
    ("GET", "/api/suppliers/{supplier_id}/statement.pdf"): "read",
    ("GET", "/api/technician-profiles"): "read",
    ("GET", "/api/warehouse-transfers/{transfer_id}"): "read",
    ("GET", "/api/warehouses"): "read",
    ("GET", "/api/warehouses/counts"): "read",
    ("GET", "/api/warehouses/counts/{count_id}"): "read",
    ("GET", "/api/warehouses/replenishment"): "read",
    ("GET", "/api/warehouses/stock"): "read",
    ("GET", "/api/warehouses/transfers"): "read",
    ("GET", "/api/warehouses/{warehouse_id}"): "read",
    ("GET", "/api/work-order-attachments/{work_order_id}"): "read",
    (
        "GET",
        "/api/work-order-attachments/{work_order_id}/{attachment_id}/download",
    ): "read",
    ("GET", "/api/work-orders"): "read",
    ("GET", "/api/work-orders/technicians"): "read",
    ("GET", "/api/work-orders/{work_order_id}"): "read",
    ("GET", "/api/work-orders/{work_order_id}/invoice"): "read",
    ("GET", "/api/work-orders/{work_order_id}/labor-lines"): "read",
    ("GET", "/api/work-orders/{work_order_id}/parts"): "read",
    # `{kind}` izni belirliyor: somut değerler `permission_cases` ile ayrıca
    # çivili (bkz. modül başlığı, 2. madde).
    ("GET", "/api/workflow/{kind}"): "read",
    ("GET", "/api/workflow/{kind}/{doc_id}"): "read",
    ("GET", "/api/{kind}/{transaction_id}"): "read",

    # --- reports — `/api/reports` ve `/api/analytics` öneklerinden.
    ("GET", "/api/analytics/absorption-rate"): "reports",
    ("GET", "/api/analytics/insights"): "reports",
    ("GET", "/api/reports/receivables-aging"): "reports",
    ("GET", "/api/reports/summary"): "reports",

    # --- supplier_prices.view — `/api/supplier-prices/{imports,profiles}`.
    ("GET", "/api/supplier-prices/imports"): "supplier_prices.view",
    ("GET", "/api/supplier-prices/imports/{import_id}"): "supplier_prices.view",
    ("GET", "/api/supplier-prices/profiles"): "supplier_prices.view",
    ("GET", "/api/supplier-prices/profiles/{profile_id}"): "supplier_prices.view",

    # --- users — `/api/users`, `/api/audit`, `/api/history`,
    #     `/api/activity-logs`, `/api/policy-overrides` öneklerinden.
    ("GET", "/api/activity-logs"): "users",
    ("GET", "/api/activity-logs/catalog"): "users",
    ("GET", "/api/activity-logs/{log_id}"): "users",
    ("GET", "/api/audit"): "users",
    ("GET", "/api/history/{entity_type}/{entity_id}"): "users",
    ("GET", "/api/policy-overrides"): "users",
    ("GET", "/api/users"): "users",
    # KİRACI DIŞA AKTARIMI. Deny-by-default nöbetçisiyle AYNI ad: yalnız
    # `admin` ("*" jokeri) taşır, yani var olan EN YÜKSEK rol. `read` olsaydı
    # HER rol firmanın tüm defterini indirebilirdi.
    ("GET", "/api/company/export"): "__admin_only__",
}

#: Toplu kaymayı tek satırda görünür kılan drift kontrolü.
# 160 -> 162: outbox okuma yüzeyinin İKİ GET ucu (liste + özet). Başka
# hiçbir ucun izni değişmedi; drift raporu `changed` ve `stale` listelerini
# BOŞ ölçtü, yani bu artış YALNIZ ekleme.
# 164 -> 166: Uygulama Kayıt Çizelgesinin JSON ve xlsx okuma uçları. İKİ dal
# da 162 -> 164 yazmıştı (BKÜ kataloğu ve çizelge); birleşmede TOPLANDI.
# Drift raporu ÖLÇÜLDÜ: `changed` ve `stale` BOŞ — artış YALNIZ ekleme.
# 166 -> 167: kantar fişi listesi (GET /api/field-harvest-tickets, göç
# 20260904_0069). Başka hiçbir ucun izni değişmedi; drift raporu ÖLÇÜLDÜ:
# `changed` ve `stale` BOŞ — artış YALNIZ ekleme.
# 167 -> 168: kiracı dışa aktarımının TEK GET ucu (/api/company/export).
# Drift raporu ÖLÇÜLDÜ: `changed` ve `stale` BOŞ — artış YALNIZ ekleme.
# 168 -> 170: müstahsil makbuzunun İKİ GET ucu (liste + tekil, göç
# 20260905_0070). Başka hiçbir ucun izni değişmedi; drift raporu ÖLÇÜLDÜ:
# `changed` ve `stale` BOŞ — artış YALNIZ ekleme.
# 172 -> 174: E1b'nin İKİ GET ucu (ekim-arası bekleme listesi + tekil, göç
# 20260907_0072). Drift raporu ÖLÇÜLDÜ: `missing`/`stale`/`changed` ÜÇÜ DE
# BOŞ — artış YALNIZ eklemedir, hiçbir ucun izni DEĞİŞMEDİ.
# 170 -> 172: D2'nin İKİ GET ucu (avans listesi + vergi defteri, göç
# 20260906_0071). Drift raporu ÖLÇÜLDÜ: `missing`/`stale`/`changed` ÜÇÜ DE
# BOŞ — artış YALNIZ eklemedir, hiçbir ucun izni DEĞİŞMEDİ.
# 174 -> 175: 1B-A'nın TEK GET ucu (parti defteri okuması, göç
# 20260908_0073). Drift raporu ÖLÇÜLDÜ: `missing`/`stale`/`changed` ÜÇÜ DE
# BOŞ — artış YALNIZ eklemedir, hiçbir ucun izni DEĞİŞMEDİ.
# 175 -> 179: E2'nin DÖRT GET ucu (veteriner ilaç kataloğu listesi + tekil,
# tedavi defteri listesi + tekil; göç 20260908_0074). Drift raporu ÖLÇÜLDÜ:
# `missing`/`stale`/`changed` ÜÇÜ DE BOŞ — artış YALNIZ eklemedir, hiçbir
# ucun izni DEĞİŞMEDİ.
# 179 -> 181: E3 karantina defterinin İKİ OKUMA ucu (göç 20260909_0075).
GET_INVENTORY_COUNT = 181
GET_INVENTORY_FINGERPRINT = (
    "1a615fb127e29fa17383eeb7f69820da62b5e702756391e0229aae9c57bf69f7"
)


def fingerprint_get_inventory(inventory: dict[tuple[str, str], str]) -> str:
    """Envanterin sıralı, kanonik parmak izi."""
    payload = "\n".join(
        f"{method}\t{path}\t{permission}"
        for (method, path), permission in sorted(inventory.items())
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def inventory_drift(
    observed: dict[tuple[str, str], str],
    expected: dict[tuple[str, str], str],
) -> tuple[list[str], list[str], list[str]]:
    """(eksik, artık, değişmiş) üçlüsünü döndürür.

    Karşılaştırma AYRI bir fonksiyonda tutuluyor: aşağıdaki negatif testler
    gerçek uygulamayı değil bu fonksiyonu besliyor, böylece kapının kendisinin
    çalıştığı kanıtlanıyor — testin yalnız bugünkü durumu tekrar etmediği.
    """
    missing = [
        f"{method} {path} -> devraldığı izin: {permission}"
        for (method, path), permission in sorted(observed.items())
        if (method, path) not in expected
    ]
    stale = [
        f"{method} {path}"
        for (method, path) in sorted(expected)
        if (method, path) not in observed
    ]
    changed = [
        f"{method} {path}: {expected[(method, path)]} -> {permission}"
        for (method, path), permission in sorted(observed.items())
        if (method, path) in expected and expected[(method, path)] != permission
    ]
    return missing, stale, changed


@pytest.fixture(scope="module")
def observed_get_permissions() -> dict[tuple[str, str], str]:
    """Gerçek uygulamadan GET/HEAD işlemlerini ve çözülen izinlerini toplar."""
    workspace = tempfile.mkdtemp(prefix="get-permission-inventory-")
    os.environ["DATABASE_URL"] = "sqlite:///" + (
        os.path.join(workspace, "inventory.db").replace(os.sep, "/")
    )
    os.environ["SUNGUR_DATA_DIR"] = workspace
    os.environ["AUTO_MIGRATE"] = "true"

    from app.auth import required_permission
    from app.main import PUBLIC_API, app
    from app.route_security_contracts import registered_api_operations

    observed: dict[tuple[str, str], str] = {}
    for method, path in registered_api_operations(app):
        if method not in {"GET", "HEAD"}:
            continue
        observed[(method, path)] = (
            "public" if path in PUBLIC_API else required_permission(method, path)
        )
    return observed


def test_every_get_operation_is_explicitly_classified(observed_get_permissions) -> None:
    """Her GET/HEAD ucu envanterde AÇIKÇA sınıflandırılmış olmalı."""
    missing, stale, changed = inventory_drift(
        observed_get_permissions, EXPECTED_GET_PERMISSIONS
    )
    assert not missing, (
        "Sınıflandırılmamış GET ucu var. Her biri bir ÖNEK kuralından izin "
        "devralmış olabilir; bu, sessizce yanlış role bağlanmak demektir "
        "(bkz. /api/field-safety olayı). Bilinçliyse envantere ekleyin:\n  "
        + "\n  ".join(missing)
    )
    assert not changed, (
        "Bir GET ucunun izni DEĞİŞTİ. Rol ayrımını daralttığını mı yoksa "
        "genişlettiğini mi doğrulayıp envanteri güncelleyin:\n  "
        + "\n  ".join(changed)
    )
    assert not stale, (
        "Envanterde artık var olmayan uç kayıtlı; kaldırın:\n  " + "\n  ".join(stale)
    )


def test_get_inventory_count_and_fingerprint_are_frozen(
    observed_get_permissions,
) -> None:
    """Sayı ve parmak izi donduruldu: toplu kayma tek satırda görünür."""
    assert len(observed_get_permissions) == GET_INVENTORY_COUNT
    assert (
        fingerprint_get_inventory(observed_get_permissions)
        == GET_INVENTORY_FINGERPRINT
    )


def test_read_get_routes_still_carry_a_review_reason() -> None:
    """``read``/public'e düşen her GET, mevcut gerekçe kapısında kayıtlı olmalı.

    İki kapıyı birbirine bağlar. Gerekçe METNİ burada TEKRARLANMIYOR — tek
    kaynak ``ROUTE_REASONS``; burada yalnız kaydın var olduğu doğrulanıyor.
    """
    from tests.test_route_security_contracts import ROUTE_REASONS

    eksik = [
        f"{method} {path}"
        for (method, path), permission in sorted(EXPECTED_GET_PERMISSIONS.items())
        if permission in {"read", "public"} and (method, path) not in ROUTE_REASONS
    ]
    assert not eksik, (
        "`read`/public GET ucu gerekçesiz. ROUTE_REASONS içine gerekçesiyle "
        "eklenmeli:\n  " + "\n  ".join(eksik)
    )


# ---------------------------------------------------------------------------
# NEGATİF TESTLER — kapının GERÇEKTEN kırıldığını kanıtlar.
#
# Bunlar olmadan yukarıdaki testler "bugünkü durumu tekrar eden" testler olurdu:
# geçmeleri, yeni bir uç eklendiğinde kırılacaklarını GÖSTERMEZ.
# ---------------------------------------------------------------------------


def test_new_unmapped_get_breaks_the_gate() -> None:
    """Envantere girmemiş yeni bir GET ``missing`` listesine düşer."""
    observed = dict(EXPECTED_GET_PERMISSIONS)
    observed[("GET", "/api/yepyeni-uc")] = "read"

    missing, stale, changed = inventory_drift(observed, EXPECTED_GET_PERMISSIONS)

    assert missing == ["GET /api/yepyeni-uc -> devraldığı izin: read"]
    assert not stale and not changed


def test_prefix_inherited_permission_is_flagged(observed_get_permissions) -> None:
    """ASIL BOŞLUK: önek kuralından GERÇEK bir izin devralan yeni uç.

    Böyle bir uç ``read``e düşmediği için mevcut gerekçe kapısı SESSİZ kalır;
    yakalayan tek şey bu envanterdir. Test iki şeyi birden kanıtlıyor:
    devralmanın gerçekten sessiz olduğunu ve envanterin onu yakaladığını.
    """
    from app.auth import required_permission

    # `/api/field` önekine düşen, var olmayan bir uç uyduruyoruz.
    yeni = "/api/field-yepyeni-rapor"
    assert yeni not in {path for _method, path in observed_get_permissions}

    devralinan = required_permission("GET", yeni)
    # Sessiz devralma: sonuç `read` DEĞİL, yani gerekçe kapısı tetiklenmez.
    assert devralinan == "field_service", devralinan

    observed = dict(EXPECTED_GET_PERMISSIONS)
    observed[("GET", yeni)] = devralinan
    missing, _stale, _changed = inventory_drift(observed, EXPECTED_GET_PERMISSIONS)
    assert missing == [f"GET {yeni} -> devraldığı izin: field_service"]


def test_changed_permission_breaks_the_gate() -> None:
    """Mevcut bir ucun izni değişirse ``changed`` listesine düşer."""
    hedef = ("GET", "/api/farms")
    assert EXPECTED_GET_PERMISSIONS[hedef] == "farm.view"

    observed = dict(EXPECTED_GET_PERMISSIONS)
    observed[hedef] = "read"

    _missing, _stale, changed = inventory_drift(observed, EXPECTED_GET_PERMISSIONS)
    assert changed == ["GET /api/farms: farm.view -> read"]


def test_removed_route_breaks_the_gate() -> None:
    """Silinen bir uç envanterde kalırsa ``stale`` listesine düşer."""
    observed = dict(EXPECTED_GET_PERMISSIONS)
    del observed[("GET", "/api/farms")]

    _missing, stale, _changed = inventory_drift(observed, EXPECTED_GET_PERMISSIONS)
    assert stale == ["GET /api/farms"]


def test_fingerprint_changes_when_a_permission_changes() -> None:
    """Parmak izi izin değişimine duyarlı olmalı; yoksa drift kontrolü sahtedir."""
    bozuk = dict(EXPECTED_GET_PERMISSIONS)
    bozuk[("GET", "/api/farms")] = "read"
    assert fingerprint_get_inventory(bozuk) != fingerprint_get_inventory(
        EXPECTED_GET_PERMISSIONS
    )
