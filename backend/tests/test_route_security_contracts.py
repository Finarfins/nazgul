from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from fastapi import FastAPI

from app.route_security_contracts import (
    build_route_security_contracts,
    fingerprint_route_contracts,
    registered_api_operations,
)


ROUTE_REASON_GROUPS = (
    (
        "Public authentication or health endpoint; no tenant context is available.",
        {
            ("POST", "/api/auth/login"),
            ("POST", "/api/auth/refresh"),
            ("POST", "/api/auth/register"),
            ("POST", "/api/auth/resend-verification"),
            ("GET", "/api/auth/verify-email"),
            # Şifresini unutan kullanıcının oturumu yoktur; bu iki uç kimlik
            # doğrulama kapısının önünde olmak zorunda. /forgot-password adresin
            # kayıtlı olup olmadığını sızdırmaz, ikisi de IP başına saatlik
            # limite tabidir ve /reset-password tek kullanımlık token ister.
            ("POST", "/api/auth/forgot-password"),
            ("POST", "/api/auth/reset-password"),
            ("GET", "/api/health"),
            ("GET", "/api/live"),
            ("GET", "/api/ready"),
        },
    ),
    (
        "Authenticated session self-service; no feature permission is required.",
        {
            ("POST", "/api/auth/logout"),
            # 5.4a: cihaz kaybi yolu. Kullanicinin KENDI oturumlarinin tamamini
            # kapatir (access + refresh); baska bir aktore dokunmaz, bu yuzden
            # kimlik dogrulamasi YETERLI ve ayri bir izin ailesi acilmadi.
            ("POST", "/api/auth/logout-all"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/auth/change-password"),
        },
    ),
    (
        "Tenant directory or configuration read available to authenticated company members.",
        {
            ("GET", "/api/companies"),
            ("GET", "/api/company-settings"),
            ("GET", "/api/branches"),
        },
    ),
    (
        "Tenant-scoped operational read; the handler must filter by request.state.company_id.",
        {
            ("GET", "/api/notifications"),
            ("GET", "/api/warehouses/counts"),
            ("GET", "/api/warehouses/counts/{count_id}"),
            ("GET", "/api/warehouses"),
            ("GET", "/api/warehouses/stock"),
            ("GET", "/api/warehouses/replenishment"),
            ("GET", "/api/warehouses/transfers"),
            ("GET", "/api/warehouses/{warehouse_id}"),
            ("GET", "/api/warehouse-transfers/{transfer_id}"),
            ("GET", "/api/dashboard"),
            ("GET", "/api/demo/summary"),
            ("GET", "/api/customers"),
            ("GET", "/api/customers/{customer_id}"),
            ("GET", "/api/customers/{customer_id}/statement"),
            # Kart listesi bir önizlemedir; TÜM satış/alış geçmişi bu iki uçtan
            # sayfalanır. Kök sorgu company_id ile bağlıdır ve cari başka
            # firmaya aitse 404 döner (belge sayısı bile sızmaz).
            ("GET", "/api/customers/{customer_id}/documents"),
            ("GET", "/api/suppliers/{supplier_id}/documents"),
            ("GET", "/api/machines"),
            ("GET", "/api/machines/{machine_id}"),
            ("GET", "/api/machines/{machine_id}/hour-readings"),
            ("GET", "/api/machines/{machine_id}/ownership-history"),
            ("GET", "/api/technician-profiles"),
            ("GET", "/api/products"),
            ("GET", "/api/products/stock/movements/all"),
            ("GET", "/api/products/{product_id}"),
            ("GET", "/api/products/{product_id}/warehouse-stock"),
            # Parti defterinin okuması (1B-A). Kök yüklem `l.company_id=:cid`
            # ve `warehouses` birleştirmesi kiracı İÇİNDE kuruluyor
            # (`w.company_id=l.company_id`); ürün başka firmaya aitse ürün
            # kapısı zaten 404 döner ve parti SAYISI bile sızmaz.
            ("GET", "/api/products/{product_id}/lots"),
            ("GET", "/api/suppliers"),
            ("GET", "/api/suppliers/{supplier_id}"),
            ("GET", "/api/suppliers/{supplier_id}/statement"),
            ("GET", "/api/payment-allocations/engine-state"),
            ("GET", "/api/payment-allocations/payments/{payment_id}"),
            ("GET", "/api/payment-allocations/orders/{order_id}"),
            ("GET", "/api/payment-allocations/charges/{receivable_charge_id}"),
            ("GET", "/api/search"),
            ("GET", "/api/search/parts"),
            ("GET", "/api/analytics/seasonal-plan"),
            ("GET", "/api/pos/lookup"),
            ("GET", "/api/quick-pick"),
            ("GET", "/api/imports/customers/template.xlsx"),
            ("GET", "/api/imports/suppliers/template.xlsx"),
            ("GET", "/api/imports/products/template.xlsx"),
            ("GET", "/api/invoices"),
            ("GET", "/api/invoices/{invoice_id}"),
            ("GET", "/api/invoices/{invoice_id}/history"),
            ("GET", "/api/invoices/{invoice_id}/pdf"),
            ("GET", "/api/invoices/{invoice_id}/einvoice/status"),
            ("GET", "/api/exchange-rates"),
            ("GET", "/api/part-supersessions"),
            ("GET", "/api/products/{product_id}/current"),
            ("GET", "/api/orders"),
            ("GET", "/api/purchases"),
            ("GET", "/api/orders/last-sale-price"),
            ("GET", "/api/purchases/last-purchase-price"),
            ("GET", "/api/{kind}/{transaction_id}"),
        },
    ),
    (
        "Tenant-scoped document or export read; source records remain company-filtered.",
        {
            ("GET", "/api/documents/{kind}/{document_id}/pdf"),
            ("GET", "/api/documents/{kind}/{document_id}/xlsx"),
            ("GET", "/api/products/{product_id}/qr.png"),
            ("GET", "/api/products/{product_id}/label.pdf"),
            ("GET", "/api/products/{product_id}/barcode-label.pdf"),
            ("GET", "/api/exports/products.xlsx"),
            ("GET", "/api/customers/{customer_id}/statement.pdf"),
            ("GET", "/api/suppliers/{supplier_id}/statement.pdf"),
            ("GET", "/api/exports/warehouse-count-variance.xlsx"),
            # 20260901 Uygulama Kayıt Çizelgesi: tarla uygulama ve hasat
            # kayıtlarının denetime gösterilebilir çıktısı. Middleware `read`
            # çözer, handler `farm.view` ister (`_require_permission`); yol
            # BİLEREK `/api/exports/` altında, `/api/field-…` altında DEĞİL —
            # oradaki önek listesinde olmayan yollar sessizce `field_service`e
            # düşüyor. Kaynak sorguların hepsi `company_id` bağlı.
            ("GET", "/api/exports/producer-logbook"),
            ("GET", "/api/exports/producer-logbook.xlsx"),
        },
    ),
    (
        "Tenant-scoped notification read; recipient and payload redaction rules still apply.",
        {
            ("GET", "/api/notifications/outbox"),
            ("GET", "/api/notifications/counters"),
            ("GET", "/api/notifications/templates"),
            ("GET", "/api/notifications/consents"),
            ("GET", "/api/notifications/consents/{consent_id}/events"),
            ("GET", "/api/notifications/rules"),
            ("GET", "/api/notifications/{notification_id}/preview"),
        },
    ),
    (
        "Tenant-scoped workflow or service read available to baseline operational roles.",
        {
            ("GET", "/api/workflow/{kind}"),
            ("GET", "/api/workflow/{kind}/{doc_id}"),
            ("GET", "/api/work-orders"),
            ("GET", "/api/work-orders/technicians"),
            ("GET", "/api/work-orders/{work_order_id}"),
            ("GET", "/api/work-orders/{work_order_id}/parts"),
            ("GET", "/api/work-orders/{work_order_id}/labor-lines"),
            ("GET", "/api/work-order-attachments/{work_order_id}"),
            ("GET", "/api/work-order-attachments/{work_order_id}/{attachment_id}/download"),
            ("GET", "/api/work-orders/{work_order_id}/invoice"),
        },
    ),
    (
        "Platform backup operation; router-level admin and operator allow-list checks apply.",
        {
            ("GET", "/api/platform/backups"),
            ("POST", "/api/platform/backups"),
            ("GET", "/api/platform/backups/{name}/download"),
            ("POST", "/api/platform/backups/{name}/verify"),
            ("POST", "/api/platform/backups/{name}/restore"),
        },
    ),
    (
        "Untenanted security audit read; rows belong to no company, so the router "
        "applies the platform-operator allow-list instead of tenant scoping.",
        {
            ("GET", "/api/platform/audit"),
        },
    ),
)
ROUTE_REASONS = {
    route: reason
    for reason, routes in ROUTE_REASON_GROUPS
    for route in routes
}
DYNAMIC_PERMISSION_CASES = {
    ("GET", "/api/documents/{kind}/{document_id}/pdf"): {
        "/api/documents/orders/1/pdf": "sales",
        "/api/documents/purchases/1/pdf": "purchases",
    },
    ("GET", "/api/documents/{kind}/{document_id}/xlsx"): {
        "/api/documents/orders/1/xlsx": "sales",
        "/api/documents/purchases/1/xlsx": "purchases",
    },
    ("GET", "/api/workflow/{kind}"): {
        "/api/workflow/quote": "read",
        "/api/workflow/sales_order": "read",
        "/api/workflow/delivery": "read",
        "/api/workflow/sale_return": "read",
        "/api/workflow/purchase_return": "read",
    },
    ("POST", "/api/workflow/{kind}"): {
        "/api/workflow/quote": "sales",
        "/api/workflow/sales_order": "sales",
        "/api/workflow/delivery": "sales",
        "/api/workflow/sale_return": "sales",
        "/api/workflow/purchase_return": "purchases",
    },
    ("DELETE", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "sales",
        "/api/workflow/sales_order/1": "sales",
        "/api/workflow/delivery/1": "sales",
        "/api/workflow/sale_return/1": "sales",
        "/api/workflow/purchase_return/1": "purchases",
    },
    ("GET", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "read",
        "/api/workflow/sales_order/1": "read",
        "/api/workflow/delivery/1": "read",
        "/api/workflow/sale_return/1": "read",
        "/api/workflow/purchase_return/1": "read",
    },
    ("PUT", "/api/workflow/{kind}/{doc_id}"): {
        "/api/workflow/quote/1": "sales",
        "/api/workflow/sales_order/1": "sales",
        "/api/workflow/delivery/1": "sales",
        "/api/workflow/sale_return/1": "sales",
        "/api/workflow/purchase_return/1": "purchases",
    },
    ("DELETE", "/api/{kind}/{transaction_id}"): {
        "/api/orders/1": "sales",
        "/api/purchases/1": "purchases",
    },
    ("GET", "/api/{kind}/{transaction_id}"): {
        "/api/orders/1": "read",
        "/api/purchases/1": "read",
    },
}

# 347/268 -> 349/269 (kantar fişi v2, göç 20260904_0069): TEK yol, İKİ işlem —
# GET ve POST /api/field-harvest-tickets. İzin `_FARM_PATH_PREFIXES`ten gelir
# (GET farm.view, POST farm.manage); yol `/api/field-harvests` ile
# BAŞLAMADIĞI için öneke ayrıca yazıldı, yazılmasaydı `field_service`e düşerdi
# (ölçüldü: önek silinince `test_every_farm_endpoint_is_covered_by_the_farm_
# permission_prefixes` kırmızı). Başka hiçbir ucun sözleşmesi değişmedi.
# 349/269 -> 350/270: kiracı dışa aktarımının TEK ucu (GET
# /api/company/export). Ayrı bir `review_reason` GEREKMİYOR: uç kiracı
# kapsamlıdır (aktif firmanın satırlarını verir) ve izni `__admin_only__`,
# yani deny-by-default nöbetçisiyle aynı addır — platform yedeği gibi router
# düzeyinde ek bir operatör listesi kullanmaz.
# 350/270 -> 355/274: müstahsil makbuzu (göç 20260905_0070). DÖRT yol, BEŞ
# işlem (ilk yol İKİ işlem taşır): POST + GET /api/producer-receipts, GET
# /api/producer-receipts/{receipt_id}, POST .../issue, POST .../cancel.
# İzin BEŞİNDE DE `purchases` ve GET'ler DAHİL öyle: kural auth.py'de temel
# `read` kuralının ÜSTÜNE yazıldı, çünkü makbuz çiftçiye ödenen birim
# fiyatı ve stopajı taşır — tedarikçi MALİYETİDİR. Altında kalsaydı okuma
# `read`e düşer ve alım fiyatları her role açılırdı (ölçüldü: kural
# silinince GET envanteri `read` sayısını 93 -> 95 gösteriyor). Ayrı bir
# `review_reason` GEREKMİYOR: beş uç da kiracı kapsamlıdır ve izinleri
# mevcut `purchases` ailesindendir. Başka hiçbir ucun sözleşmesi değişmedi.
# 360/278 -> 364/280: E1b — ekim-arası bekleme kataloğu (göç 20260907_0072).
# İKİ yol, DÖRT işlem: GET + POST /api/plant-protection-plantbacks ve
# GET + PUT /api/plant-protection-plantbacks/{plantback_id}. İzin DÖRDÜNDE DE
# tarla ailesinden (`farm.view` / `farm.manage`) ve YENİ BİR İZİN AİLESİ
# AÇILMADI: plant-back süresi de PHI gibi BKÜ etiketinden gelen tarla
# verisidir. Önek `_FARM_PATH_PREFIXES`e AYRICA yazıldı ve gerekliliği
# ÖLÇÜLDÜ: "...-plantbacks" mevcut "...-products" önekiyle EŞLEŞMİYOR, yani
# satır olmasaydı dördü de genel `read`/yazma yoluna düşerdi. Ayrı bir
# `review_reason` GEREKMİYOR: dört uç da kiracı kapsamlıdır ve izinleri
# mevcut `farm` ailesindendir. Başka hiçbir ucun sözleşmesi değişmedi.
# 355/274 -> 360/278: D2 — avans, makbuz ödemesi, borsa tescili ve vergi
# defteri (göç 20260906_0071). DÖRT yol, BEŞ işlem (ilk yol İKİ işlem
# taşır): POST + GET /api/suppliers/{supplier_id}/advances, POST
# /api/producer-receipts/{receipt_id}/pay, POST
# /api/producer-receipts/{receipt_id}/exchange-registration, GET
# /api/tax-liabilities. İzin BEŞİNDE DE `purchases` ve GET'ler DAHİL öyle:
# iki yeni kural auth.py'de temel `read` kuralının ÜSTÜNE yazıldı, çünkü
# avans çiftçiye ödenen PARADIR ve stopaj yükümlülüğü makbuzun kesintisidir
# — ikisi de tedarikçi maliyetini AÇIK EDER. Avans yolu için bu ÖZELLİKLE
# gerekliydi: `/api/suppliers` kuralı ZATEN var ama `read`in ALTINDA, yani
# kural yazılmasaydı avans listesi `read`e düşerdi (ölçüldü: kural silinince
# GET envanteri o ucu `read` gösteriyor). Ayrı bir `review_reason`
# GEREKMİYOR: beş uç da kiracı kapsamlıdır ve izinleri mevcut `purchases`
# ailesindendir. Başka hiçbir ucun sözleşmesi değişmedi.
# 365/281 -> 366/282: kiracı yumuşak imhası (5.1b). TEK yol, TEK işlem:
# POST /api/company/erase. İzni `__admin_only__` — dışa aktarımla AYNI ad,
# yani ayrı bir `review_reason` GEREKMİYOR (aynı gerekçe: deny-by-default
# nöbetçisiyle aynı ad, router düzeyinde ek operatör listesi yok). GET
# envanteri KIMILDAMIYOR: dilim hiçbir GET ucu getirmiyor. TABAN, E1b (#53)
# ve YENİDEN KUYRUKLAMA (#55) indikten SONRAKİ `a2c5f61`dir; önceki turun
# 360/278 -> 361/279 ölçümü tabanı değiştiği anda GEÇERSİZ oldu ve bu
# satırlar YENİDEN ÖLÇÜLDÜ, aritmetikle türetilmedi.
# 366/282 -> 367/283 (TABAN #54 SONRASI, YENIDEN OLCULDU): 1B-A — parti defterinin OKUMASI (göç 20260908_0073).
# TEK yol, TEK işlem: GET /api/products/{product_id}/lots. İzin `read` ve
# YENİ BİR İZİN AİLESİ AÇILMADI: uç, `GET /api/products/{product_id}`in
# gösterdiği stok bakiyesinin PARTİ KIRILIMIDIR — aynı olguyu iki farklı
# kapının arkasına koymak, birinin diğerinden sessizce ayrışmasına yol
# açardı. Yolu `/api/products` önekindedir, yani izin ZATEN doğru aileden
# geliyor ve `auth.py`ye satır EKLENMEDİ. `review_reason` gerektiği için
# `ROUTE_REASONS`daki kiracı kapsamlı okuma kümesine yazıldı. Başka hiçbir
# ucun sözleşmesi değişmedi.
# 367/283 -> 374/287 (TABAN #56 SONRASI, YENIDEN OLCULDU): E2 — veteriner
# ilaç kataloğu + tedavi defteri + arınma kilitleri (göç 20260908_0074).
# YEDİ işlem, DÖRT yol: GET/POST /api/vet-drugs, GET/PUT
# /api/vet-drugs/{drug_id}, GET/POST /api/animal-treatments, GET
# /api/animal-treatments/{treatment_id}.
#
# İZİNLER MEVCUT AİLEDEN ve YENİ BİR İZİN ADI AÇILMADI: okuma `herd.view`,
# katalog yazma `herd.manage`, tedavi yazma `herd.health`. Sonuncusu
# `auth.py`deki AŞI kuralının GENİŞLETİLMESİ değil AYNEN UYGULANMASIDIR —
# kural "veteriner ya da sağlık sorumlusu" diyor ve ilaç tedavisi aşıdan
# daha da açık biçimde veterinerlik işidir; kataloğun `herd.manage`da
# kalması ise TANIM ile OLAY ayrımıdır (katalog satırı firmanın bütün
# gelecek tedavilerinin süresini belirler).
#
# `auth.py`ye İKİ önek satırı EKLENDİ (`/api/vet-drugs`,
# `/api/animal-treatments`) ve gerekçesi ÖLÇÜLDÜ: ikisi de mevcut hiçbir
# önekle eşleşmiyor ("/api/animal-treatments" "/api/animals" ile EŞLEŞMEZ),
# yani yazılmasalardı genel `read` iznine düşerlerdi. Ayrı bir
# `review_reason` GEREKMİYOR: yedi uç da kiracı kapsamlıdır ve izinleri
# mevcut `herd.*` ailesindendir. Başka hiçbir ucun sözleşmesi değişmedi.
EXPECTED_OPERATION_COUNT = 375
EXPECTED_PATH_COUNT = 288
EXPECTED_SECURITY_FINGERPRINT = (
    # 20260807: saha yazma yüzeyi eklendi —
    #   POST /api/field/work-orders/{work_order_id}/status  (durum ilerletme)
    #   POST /api/field/work-orders/{work_order_id}/parts   (kullanılan parça)
    #   POST /api/field/work-orders/{work_order_id}/attachments (fotoğraf/imza)
    #   POST /api/field/work-orders/{work_order_id}/labor   (işçilik, DRAFT açılır)
    # İzin `/api/field` önekinden gelir (field_service); kapsam firma VE isteği
    # yapan teknisyen, başkasının iş emrine 404 döner. Parça ucunda fiyat ve
    # depo istemciden ALINMAZ: fiyat üründen, depo teknisyenin servis aracı
    # deposundan çözülür.
    # 20260807 Tarla Yönetimi V1 FAZ 2 (mobil-erp#2): 13 uç eklendi —
    # /api/farms, /api/farm-parcels, /api/crop-seasons, /api/field-activities
    # (+/inputs), /api/field-harvests, /api/field-tasks, /api/field-dashboard.
    # İzinler farm.view / farm.manage / farm.inputs. DİKKAT: bu yollar
    # /api/field ile başlıyor; izin çözümleyicide tarla kuralı saha
    # kuralından ÖNCE olmak zorunda (testi var).
    # 20260807 (FAZ 5): GET /api/field-safety — yürürlükteki ilaç güvenlik
    # kısıtları. İzni `farm.view`; `/api/field-safety` de `/api/field` ile
    # BAŞLADIĞI için `_FARM_PATH_PREFIXES`'e eklenmeseydi sessizce
    # `field_service`'e düşerdi (bu tuzak iki kez yaşandı, artık türetilmiş bir
    # test tüm tarla uçlarını tarıyor).
    #
    # NOT: bu parmak izi ÖNCE yanlış hesaplandı — uç `field_service`'e
    # düşmüşken alınmıştı. Yani sözleşme yanlış izni sadakatle çiviliyordu;
    # doğru sıra "izni düzelt, SONRA parmak izini al".
    # 20260807 (FAZ 7): GET /api/farm-parcels/{parcel_id}/timeline — parselin
    # sezonları + faaliyet/hasat çizelgesi TEK istekte. İzni `farm.view`;
    # `/api/farm-parcels` öneki zaten `_FARM_PATH_PREFIXES`'te olduğu için
    # `/api/field` tuzağına düşmüyor (yine de türetilmiş test tarıyor).
    # 20260808 (Hayvancılık V1 FAZ 2): 12 uç — /api/animals, /api/animal-*,
    # /api/milk-yields, /api/herd-dashboard. İzinler `herd.view/manage/health`;
    # aşı ucu AYRI izinde (veteriner hayvan satamamalı). Parmak izi
    # dondurulmadan ÖNCE tüm uçların herd.* iznine düştüğü türetilmiş kontrolle
    # doğrulandı — FAZ 5'te yanlış izin sadakatle çivilenmişti.
    # 20260808: Rota envanteri, onaylı tenant/SQL incelemesi sonrası yenilendi.
    # 20260808 Gerçek Maliyet V1 FAZ 1 (mobil-erp#24): 4 işlem / 2 yol eklendi —
    # GET+POST /api/cost-rates ve GET+PUT /api/cost-rates/{rate_id}. Dördü de
    # `finance` iznine bağlı, GET'ler dahil: oran bir PARA TANIMI ve okunması da
    # değiştirilmesi de kâr rakamını ilgilendirir. Genel `read`e düşselerdi depo
    # rolü firmanın maliyet yapısını görebilirdi.
    # 20260812 (denetim görünürlüğü): 1 işlem / 1 yol eklendi —
    # GET /api/platform/audit. Hiçbir kiracıya bağlanamayan güvenlik denetim
    # satırları (giriş denemeleri, kayıt, AUTH_REQUIRED 401'leri) `company_id`
    # süzen HİÇBİR okumada görünmüyordu; bu uç onların okunabildiği yerdir.
    # İzin çözümleyicide `read`e düşer — /api/platform/backups ile AYNI — çünkü
    # gerçek kapı yönlendiricideki `require_platform_operator`'dır: satırlar tek
    # bir kiracıya ait olmadığı için tek bir kiracının yöneticisine de değildir.
    # Parmak izi DONDURULMADAN ÖNCE iznin `read` olduğu ve operatör olmayan bir
    # admin'in 403 aldığı ayrı ayrı ölçüldü (bu dosyanın yukarıdaki notu, yanlış
    # izni sadakatle çivilemenin iki kez yaşandığını söylüyor).
    # 20260827 (FIELD_STOK_OUTBOX açılış koşulu 2 — outbox OKUMA yüzeyi):
    # 2 işlem / 2 yol eklendi — GET /api/field-integration-events ve
    # GET /api/field-integration-events/summary. İkisi de `farm.view`.
    # AYNI TUZAK YİNE KURULDU VE YİNE ELLE AÇILDI: yollar `/api/field` ile
    # BAŞLIYOR, `_FARM_PATH_PREFIXES`'e eklenmeseydi sessizce
    # `field_service`'e düşerlerdi. Bu dosyanın yukarıdaki notu, parmak
    # izini YANLIŞ izinle dondurmanın iki kez yaşandığını söylüyor; bu
    # yüzden sıra korundu: önce önek listesine eklendi, `required_permission`
    # ile `farm.view` ÖLÇÜLDÜ, SONRA parmak izi alındı.
    # Uçlar SALT OKUR; tüketici davranışı ve `FIELD_STOCK_OUTBOX_ENABLED`
    # varsayılanı DEĞİŞMEDİ.
    # 20260901 BKÜ KATALOĞU (göç 20260901_0063): 4 operasyon, 2 yol eklendi —
    #   GET/POST /api/plant-protection-products
    #   GET/PUT  /api/plant-protection-products/{ppp_id}
    # Katalog PHI (hasat bekleme) gün sayısının firma tarafından doldurulan
    # kaydıdır. İzin `_FARM_PATH_PREFIXES`'e eklenen önekten geliyor: okuma
    # `farm.view`, yazma `farm.manage`. ÖNEK LİSTESİNE EKLENMESEYDİ uçlar
    # genel `read` iznine düşerdi ve okuma yetkisi olan HERKES yasal bekleme
    # sürelerini değiştirebilirdi.
    # 20260901 UYGULAMA KAYIT ÇİZELGESİ: 2 operasyon, 2 yol daha —
    #   GET /api/exports/producer-logbook       (JSON önizleme)
    #   GET /api/exports/producer-logbook.xlsx  (iki sayfalı çizelge)
    # Bu kez AYNI TUZAK KURULMADI: yol `/api/field-…` DEĞİL, `/api/exports/`
    # seçildi; `required_permission` ile `read` ÖLÇÜLDÜ, asıl yetki kapısı
    # handler'da (`_require_permission(request, "farm.view")`), SONRA parmak
    # izi alındı. Uçlar SALT OKUR ve PARA SÜTUNU SEÇMEZ.
    # Toplam: 340 -> 346 işlem, 263 -> 267 yol (iki dalın birleşimi).
    # 20260902 BKÜ İÇE AKTARMA (göç 20260902_0065): 1 operasyon, 1 yol eklendi —
    #   POST /api/plant-protection-products/import
    # Katalog 0063'te açıldı ama TEK TEK FORM ile dolduruluyordu; bu uç aynı
    # kataloğu firmanın KENDİ dosyasından doldurur. İzin YENİ DEĞİL: uç zaten
    # `_FARM_PATH_PREFIXES`teki `/api/plant-protection-products` önekinin
    # ALTINDA ve güvenli olmayan yöntem olduğu için `farm.manage` istiyor —
    # `required_permission` ile ÖLÇÜLDÜ, varsayılmadı.
    # UCUN `imports` YÖNLENDİRİCİSİNE KONMAMASI BİLİNÇLİ: `/api/imports` bu
    # önek listesinde DEĞİL, dolayısıyla uç orada olsaydı yasal bekleme
    # sürelerini içe aktarma yetkisi olan HERKES yazabilirdi. Okuyucu
    # (`_read_tabular_upload`) paylaşılıyor, YETKİ paylaşılmıyor.
    # Kapsam firma: eklenen sorguların hepsi kendi `company_id=:cid` yüklemini
    # taşıyor ve hiçbiri istekten gelen bir metni SQL'e koymuyor.
    # TABAN DEVELOP'UN 346/267'SİDİR (PR #22 indikten sonra): 346 -> 347 işlem,
    # 267 -> 268 yol. Önceki turdaki 344 -> 345 / 265 -> 266 ölçümü, tabanı
    # değiştiği anda GEÇERSİZ oldu; bu satırlar yeniden ÖLÇÜLDÜ.
    # 20260905 kantar fişi v2 (göç 20260904_0069): GET/POST
    # /api/field-harvest-tickets eklendi (farm.view / farm.manage, öneke
    # yazılarak). Parmak izi 45a56fbd -> adb27fb5; başka sözleşme değişmedi.
    # 20260905 kiracı dışa aktarımı: GET /api/company/export eklendi
    # (`__admin_only__`). Parmak izi adb27fb5 -> yeniden türetildi.
    # 20260907 E1b (göç 20260907_0072): ekim-arası bekleme kataloğunun DÖRT
    # işlemi eklendi (farm.view / farm.manage, öneke yazılarak). Parmak izi
    # 8f3eb86b -> a49f58e6; başka hiçbir sözleşme değişmedi.
    # 20260907 (FIELD_STOK_OUTBOX açılış koşulu 3 — YENİDEN KUYRUKLAMA):
    # 1 işlem / 1 yol eklendi — POST
    # /api/field-integration-events/{olay_id}/requeue. TABAN E1b SONRASIDIR:
    # 364 -> 365 işlem, 280 -> 281 yol. Önceki turdaki 360 -> 361 / 278 -> 279
    # ölçümü, taban E1b ile değiştiği anda GEÇERSİZ oldu ve bu satırlar
    # BİRLEŞMİŞ AĞAÇTA YENİDEN ÖLÇÜLDÜ (CPython 3.12.10).
    # İZİN `farm.manage`, ÖLÇÜLDÜ VE VARSAYILMADI: yol
    # `/api/field-integration-events` önekinin altında ve o önek koşul 2'de
    # `_FARM_PATH_PREFIXES`e ZATEN eklenmişti; güvenli olmayan yöntem olduğu
    # için `required_permission` `farm.manage` döndürüyor — okumanın
    # `farm.view`inden DAHA DAR. Sıra yine korundu: önce önek listesinin
    # yerinde olduğu doğrulandı, sonra izin `required_permission` ile
    # ölçüldü, EN SON parmak izi alındı. Bu dosyanın yukarıdaki notu, parmak
    # izini YANLIŞ izinle dondurmanın iki kez yaşandığını söylüyor.
    # Uç TEK BİR SÜTUN ÜÇLÜSÜ yazar (`status`/`attempts`/`updated_at`) ve
    # yalnız terminal (`SENT` OLMAYAN) satırda; göç EKLEMEDİ ve tüketici
    # davranışı ile `FIELD_STOCK_OUTBOX_ENABLED` varsayılanı DEĞİŞMEDİ.
    # Parmak izi a49f58e6 -> d11a83eb.
    # 20260906 kiracı yumuşak imhası (5.1b): POST /api/company/erase
    # (`__admin_only__`) eklendi — TEK uç. Parmak izi d11a83eb -> yeniden
    # türetildi (TABAN `a2c5f61`); başka hiçbir ucun sözleşmesi değişmedi.
    # 20260908 1B-A (göç 20260908_0073): GET /api/products/{product_id}/lots
    # eklendi (`read`, mevcut `/api/products` önekinden). Parmak izi
    # TABAN #54 SONRASI: cec015e0 -> yeniden türetildi; başka hiçbir sözleşme değişmedi.
    # 20260907 5.4a MOBIL KIMLIK AKISI (goc YOK): POST /api/auth/logout-all
    # eklendi — TEK uc, self-servis grubunda (``required_permission`` "read",
    # muafiyet ``SELF_SERVICE_API``da). SIRA: uc `SELF_SERVICE_API`ya yazildi,
    # `required_permission` ile OLCULDU ("read"), `ROUTE_REASONS`a gerekcesiyle
    # girdi, EN SON parmak izi alindi — bu dosyanin notu parmak izini YANLIS
    # izinle dondurmanin iki kez yasandigini soyluyor.
    # /auth/login, /auth/refresh ve /auth/logout'un SOZLESMESI DEGISMEDI:
    # ucu de ayni yol, ayni yontem, ayni izin; degisen yalniz govde/baslik
    # kabuludur ve parmak izi bunlari gormez. Sayim 374 -> 375, yol 287 -> 288.
    # Parmak izi 411caebd -> yeniden turetildi (TABAN develop `77aa5b0`, yani
    # #59 + #62 + #63 indikten SONRA; onceki turun 367/283 -> 368/284 olcumu
    # taban degistigi anda GECERSIZ oldu ve ARITMETIKLE tasinmadi).
    "d3626d88708e328a3d5f7bd24a1c9afc82d8835a11fcafa7ed8b6022718010d2"
)
TEST_PERMISSIONS = {"__admin_only__", "read", "sales"}


@pytest.fixture(scope="module")
def application_contract(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("route-security-contract")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(workspace / 'test.db').as_posix()}")
    monkeypatch.setenv("SUNGUR_DATA_DIR", str(workspace))
    monkeypatch.setenv("AUTO_MIGRATE", "true")

    from app.auth import ROLE_PERMISSIONS, required_permission
    from app.main import PUBLIC_API, app, security_and_audit
    from app.routers import outputs

    known_permissions = {
        permission
        for permissions in ROLE_PERMISSIONS.values()
        for permission in permissions
        if permission != "*"
    } | {"__admin_only__"}

    def effective_permission(method: str, path: str) -> str:
        if method == "GET" and path.startswith("/api/documents/"):
            kind = path.split("/")[3]
            return str(outputs._kind(kind)["permission"])
        return required_permission(method, path)

    yield (
        app,
        PUBLIC_API,
        security_and_audit,
        effective_permission,
        known_permissions,
        outputs,
    )
    monkeypatch.undo()


def _schema(*operations: tuple[str, str]) -> dict:
    paths: dict[str, dict[str, dict]] = {}
    for method, path in operations:
        paths.setdefault(path, {})[method.lower()] = {}
    return {"paths": paths}


def _accepted_kind_literals(function) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    accepted: set[str] = set()
    for comparison in ast.walk(tree):
        if not isinstance(comparison, ast.Compare):
            continue
        if not isinstance(comparison.left, ast.Name) or comparison.left.id != "kind":
            continue
        for comparator in comparison.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                accepted.add(comparator.value)
            elif isinstance(comparator, (ast.List, ast.Set, ast.Tuple)):
                accepted.update(
                    element.value
                    for element in comparator.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
    return accepted


def test_read_route_requires_an_explicit_review_reason() -> None:
    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("GET", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )


def test_public_and_platform_exceptions_require_an_explicit_reason() -> None:
    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("GET", "/api/live")),
            public_paths={"/api/live"},
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )

    with pytest.raises(ValueError, match="explicit review reason"):
        build_route_security_contracts(
            _schema(("POST", "/api/platform/backups")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
        )


def test_unknown_permission_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown permission"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "typo_permission",
            known_permissions=TEST_PERMISSIONS,
        )

    with pytest.raises(ValueError, match="unknown permission"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "*",
            known_permissions=TEST_PERMISSIONS,
        )


def test_stale_review_reason_fails_closed() -> None:
    with pytest.raises(ValueError, match="review reason is not registered"):
        build_route_security_contracts(
            _schema(("POST", "/api/example")),
            public_paths=set(),
            route_reasons={("GET", "/api/removed"): "Stale reason."},
            permission_resolver=lambda method, path: "sales",
            known_permissions=TEST_PERMISSIONS,
        )


def test_new_route_changes_the_contract_fingerprint() -> None:
    reasons = {("GET", "/api/example"): "Tenant-scoped operational read."}
    before = build_route_security_contracts(
        _schema(("GET", "/api/example")),
        public_paths=set(),
        route_reasons=reasons,
        permission_resolver=lambda method, path: "read",
        known_permissions=TEST_PERMISSIONS,
    )
    after = build_route_security_contracts(
        _schema(("GET", "/api/example"), ("POST", "/api/example")),
        public_paths=set(),
        route_reasons=reasons,
        permission_resolver=lambda method, path: "read" if method == "GET" else "sales",
        known_permissions=TEST_PERMISSIONS,
    )

    assert fingerprint_route_contracts(before) != fingerprint_route_contracts(after)


def test_kind_route_requires_concrete_permission_cases() -> None:
    with pytest.raises(ValueError, match="concrete permission cases"):
        build_route_security_contracts(
            _schema(("POST", "/api/workflow/{kind}")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: "sales",
            known_permissions=TEST_PERMISSIONS,
            permission_cases={},
        )

    with pytest.raises(ValueError, match="permission case mismatch"):
        build_route_security_contracts(
            _schema(("POST", "/api/workflow/{kind}")),
            public_paths=set(),
            route_reasons={},
            permission_resolver=lambda method, path: (
                "purchases" if "purchase_return" in path else "sales"
            ),
            known_permissions={*TEST_PERMISSIONS, "purchases"},
            permission_cases={
                ("POST", "/api/workflow/{kind}"): {
                    "/api/workflow/order": "sales",
                    "/api/workflow/purchase_return": "sales",
                }
            },
        )


def test_hidden_api_route_cannot_bypass_the_contract() -> None:
    local_app = FastAPI()

    @local_app.get("/api/visible")
    def visible() -> None:
        return None

    @local_app.get("/api/hidden", include_in_schema=False)
    def hidden() -> None:
        return None

    with pytest.raises(ValueError, match="registered API routes differ from OpenAPI"):
        build_route_security_contracts(
            local_app.openapi(),
            public_paths=set(),
            route_reasons={
                ("GET", "/api/visible"): "Reviewed tenant-scoped read."
            },
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
            registered_operations=registered_api_operations(local_app),
        )


def test_mounted_api_cannot_bypass_the_contract() -> None:
    local_app = FastAPI()

    @local_app.get("/api/visible")
    def visible() -> None:
        return None

    local_app.mount("/api/mounted", FastAPI())

    with pytest.raises(ValueError, match="registered API routes differ from OpenAPI"):
        build_route_security_contracts(
            local_app.openapi(),
            public_paths=set(),
            route_reasons={
                ("GET", "/api/visible"): "Reviewed tenant-scoped read."
            },
            permission_resolver=lambda method, path: "read",
            known_permissions=TEST_PERMISSIONS,
            registered_operations=registered_api_operations(local_app),
        )


def test_registered_route_security_contract_is_exact(application_contract) -> None:
    app, public_api, _, permission_resolver, known_permissions, _ = application_contract
    assert sum(len(routes) for _, routes in ROUTE_REASON_GROUPS) == len(ROUTE_REASONS)
    contracts = build_route_security_contracts(
        app.openapi(),
        public_paths=public_api,
        route_reasons=ROUTE_REASONS,
        permission_resolver=permission_resolver,
        known_permissions=known_permissions,
        permission_cases=DYNAMIC_PERMISSION_CASES,
        registered_operations=registered_api_operations(app),
    )

    assert len(contracts) == EXPECTED_OPERATION_COUNT
    assert len({contract.path for contract in contracts}) == EXPECTED_PATH_COUNT
    assert fingerprint_route_contracts(contracts) == EXPECTED_SECURITY_FINGERPRINT


def test_security_middleware_orders_rbac_and_tenant_before_handler(
    application_contract,
) -> None:
    app, _, security_and_audit, _, _, _ = application_contract
    installed_dispatches = [
        middleware.kwargs.get("dispatch")
        for middleware in app.user_middleware
    ]
    assert installed_dispatches.count(security_and_audit) == 1

    source = inspect.getsource(security_and_audit)

    permission_check = source.index("has_permission(")
    tenant_resolution = source.index("resolve_company(")
    handler_call = source.index("await call_next(request)")
    assert permission_check < tenant_resolution < handler_call


def test_document_output_cases_include_handler_permission(application_contract) -> None:
    _, _, _, permission_resolver, _, outputs = application_contract

    assert permission_resolver("GET", "/api/documents/orders/1/pdf") == "sales"
    assert (
        permission_resolver("GET", "/api/documents/purchases/1/xlsx")
        == "purchases"
    )
    for handler in (outputs.document_pdf, outputs.document_xlsx):
        source = inspect.getsource(handler)
        assert '_require_permission(request, config["permission"])' in source


def test_dynamic_kind_cases_match_handler_inventories(application_contract) -> None:
    _, _, _, _, _, outputs = application_contract
    from app.routers import transactions, workflow

    workflow_kind_sets = {
        frozenset(path.split("/")[3] for path in cases)
        for (method, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/workflow/")
    }
    assert workflow_kind_sets == {frozenset(workflow.CONFIG)}

    output_kind_sets = {
        frozenset(path.split("/")[3] for path in cases)
        for (method, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/documents/")
    }
    accepted_output_kinds = _accepted_kind_literals(outputs._kind)
    assert output_kind_sets == {frozenset(accepted_output_kinds)}

    transaction_kind_sets = {
        frozenset(path.split("/")[2] for path in cases)
        for (_, template), cases in DYNAMIC_PERMISSION_CASES.items()
        if template.startswith("/api/{kind}/")
    }
    handler_transaction_kinds = {
        frozenset(_accepted_kind_literals(handler))
        for handler in (transactions.detail, transactions.delete_transaction)
    }
    assert handler_transaction_kinds == transaction_kind_sets
