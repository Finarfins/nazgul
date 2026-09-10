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
    # META WEBHOOK DOGRULAMASI (WA1, goc 20260910_0078). "public" burada bir
    # IZIN ADI DEGIL, bu envanterin PUBLIC_API uyeligine verdigi addir: uc
    # yetki kapisina HIC GIRMEZ. Yerini alan sey `hub.verify_token`in SABIT
    # ZAMANLI karsilastirmasidir ve uc HICBIR SEY YAZMAZ, yalniz
    # `hub.challenge`i aynen doner. Ustelik uc ancak UC AYAR birden doluyken
    # vardir; yapilandirilmamis bir kurulumda 404 doner.
    ("GET", "/api/whatsapp/webhook"): "public",

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
    # SEC-3: `read` -> `sales`. Ekstre `StatementEntity`yi (`statement.py:87-97`
    # tax_number/address/phone/email) ve TUM cari defteri tasiyor. Izin ADI
    # SECILMEDI, DEPODAN OKUNDU: `statement.py:46-68` `customer` icin
    # `"permission": "sales"` diyor ve `outputs.py:875` bunu PDF'te ZATEN
    # uyguluyor. Yani bu satirdan ONCE ayni belgenin JSON'i `read`, PDF'i
    # `sales` istiyordu; `depo`/`rapor` icin JSON 200, PDF 403 donuyordu ve
    # `EntityStatementDialog.tsx:36`/`:44` ikisini YAN YANA cagiriyordu.
    # KAYBEDEN: `depo`, `rapor`.
    ("GET", "/api/customers/{customer_id}/statement"): "sales",
    # PDF'in ENVANTER DEGERI DEGISTI ama ERISIM DEGISMEDI: handler kapisi zaten
    # `sales` istiyordu. Degisen sey kapinin YERI — artik middleware'de, yani
    # uc `GUARDED_READ`ten CIKIYOR (26 -> 24). `EXPECTED_UNDENIABLE`i zaten
    # buyutmuyordu; kucultmuyor da.
    ("GET", "/api/customers/{customer_id}/statement.pdf"): "sales",
    ("GET", "/api/dashboard"): "read",
    ("GET", "/api/demo/summary"): "read",
    # --- e-IRSALIYE (E4a, goc 20260913_0083) -------------------------------
    # DORDU DE "sales" ve HICBIRI "read" DEGIL. Gerekce `app/auth.py`deki
    # kuralin ustunde: bu satirlar musteri VKN/TCKN'sini, teslim adresini ve
    # SOFOR TCKN'SINI tasiyor. `/api/invoices`in SEC-3'te `read`ten `sales`a
    # cekilmesiyle ayni gerekce; burada gerekce daha da keskin cunku sofor
    # TCKN'si bir GERCEK KISI verisidir.
    #
    # `.../edespatch/download` de "sales" ve bu `/api/invoices/.../einvoice/
    # download`dan FARKLI bir yoldan geliyor: orada AYRI bir onek+sonek
    # kurali yazilmak zorundaydi cunku `/api/invoices` ailesinde `read`te
    # kalmasi gereken uclar vardi. Burada tek onek yetiyor.
    ("GET", "/api/despatch-notes"): "sales",
    ("GET", "/api/despatch-notes/{despatch_id}"): "sales",
    ("GET", "/api/despatch-notes/{despatch_id}/edespatch/download"): "sales",
    ("GET", "/api/despatch-notes/{despatch_id}/edespatch/status"): "sales",
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
    # SEC-3: `read` -> `sales`. Bu ailenin EN GENIS sizintisi LISTEDIR:
    # `invoices.py:40-53` `customer_snapshot`i secip `items[].customer` olarak
    # cozuyor, yani TEK istekle firmanin TUM faturalarinin musteri VKN'si ve
    # adresi dokuluyor (`billing_service.py:113-114` snapshot'i boyle kuruyor).
    # `/pdf` tek belge verir, liste HEPSINI. KAYBEDEN: `depo`, `rapor`.
    ("GET", "/api/invoices"): "sales",
    ("GET", "/api/invoices/{invoice_id}"): "sales",
    # E2 (GOC YOK): e-belge sureti indirme. Izin "sales" ve bu OLCULDU,
    # VARSAYILMADI: `auth.py`ye acik kural yazilmadan once
    # `required_permission` bu uc icin "read" veriyordu (genel guvenli-metot
    # kurali). Envanterdeki TEK "sales" GET budur ve gerekcesi `auth.py`de
    # yazili: uc DIS BIR YAN ETKI uretir (saglayicida oturum + kota) ve
    # indirilen sey resmi mali belgedir. Komsu uclarin degeri DEGISMEDI, o da
    # olculdu: `.../einvoice/status`, `.../pdf` ve `/api/invoices` hala "read".
    ("GET", "/api/invoices/{invoice_id}/einvoice/download"): "sales",
    # SEC-3: `read` -> `sales`. `/einvoice/status` (`invoices.py:265`) bu
    # ailenin EN hassas ucu: `_einvoice_view` `einvoice_payload`i OLDUGU GIBI
    # donduruyor — saglayiciya gonderilen TAM UBL govdesi (alici VKN/TCKN,
    # adres, satir fiyatlari) ve `einvoice_web_key`, yani e-Arsiv suretine
    # erisim anahtari. Hemen ustteki `.../einvoice/download` ZATEN `sales`ti;
    # `status`un `read`te kalmasi o kuralla ACIKCA tutarsizdi.
    ("GET", "/api/invoices/{invoice_id}/einvoice/status"): "sales",
    # `/history` `SELECT * FROM invoice_history` yapiyor: actor_username,
    # ip_address, reason. Bir DENETIM yuzeyi oldugu icin `users` de savunulur
    # (`auth.py` `/api/audit` ve `/api/history`yi oraya bagliyor); karar `sales`
    # oldu — uc fatura ailesinin parcasi ve ayni ekrandan aciliyor. En azindan
    # `read` OLMAMASI sart.
    ("GET", "/api/invoices/{invoice_id}/history"): "sales",
    ("GET", "/api/invoices/{invoice_id}/pdf"): "sales",
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
    # Siparis LISTESI BILEREK `read`te KALIYOR: `depo`/`rapor` icin gunluk is
    # yuzeyi. Daralan sey yalniz FIYAT ucudur (bir alttaki satir).
    ("GET", "/api/orders"): "read",
    # SEC-3: `read` -> `sales`. Musteriye ozel SATIS `unit_price` +
    # `discount_percent` (`transactions.py:1393`). Alis ikizi
    # (`/api/purchases/last-purchase-price`) `purchases`a baglandi; "fiyat
    # ticari olarak hassastir" gerekcesi kabul edildiyse iki kardes ucun
    # AYRISMASI tutarsiz olurdu. `auth.py`de ONEK DEGIL TAM YOL
    # karsilastirmasi kullanildi, yoksa `/api/orders` listesi de duserdi.
    # KAYBEDEN: `depo`, `rapor`.
    ("GET", "/api/orders/last-sale-price"): "sales",
    ("GET", "/api/part-supersessions"): "read",
    # SEC-3: `read` -> `payments` (UC UC). Uculu GERCEK tahsis tutarlari
    # donduruyor (`AllocationView`, `payment_allocation_schemas.py:42-58`):
    # hangi tahsilatin hangi belgeye ne kadar yazildigi. Kural artik
    # "GET -> payments, yazma -> finance", yani OKUMA ile YAZMA AYRILDI:
    # `satis` defteri okur ama tahsis edemez — `satis` tahsilat rolu oldugu
    # icin bu tutarlidir. KAYBEDEN: `depo`, `rapor`.
    ("GET", "/api/payment-allocations/charges/{receivable_charge_id}"): "payments",
    # ENGINE-STATE `read`te KALIYOR ve bu BILINCLI: yanit tek bir yapilandirma
    # boolean'i (`payment_allocations.py:76-88`), kiraci verisi yok, tutar yok,
    # cari yok. Arayuz "hic tahsis yok" ile "ozellik kapali"yi bununla ayirir;
    # `payments`a baglamak o ayrimi geri kirardi. Kural `auth.py`de tahsis
    # kuralinin USTUNDE durmak ZORUNDA — altinda kalsaydi onek onu da yuturdu.
    ("GET", "/api/payment-allocations/engine-state"): "read",
    ("GET", "/api/payment-allocations/orders/{order_id}"): "payments",
    ("GET", "/api/payment-allocations/payments/{payment_id}"): "payments",
    # `read` görünüyor ama router AYRICA `require_platform_operator` uyguluyor
    # (admin + ortam allowlist'i). Bkz. `app/auth.py` içindeki açıklama.
    # Aynı gerekçe: firmasız denetim satırları tek bir kiracıya ait olmadığı
    # için tek bir kiracının yöneticisine de ait değil; gerçek kapı router'daki
    # `require_platform_operator`. Göç 20260812_0059 ile birlikte geldi.
    ("GET", "/api/platform/audit"): "read",
    ("GET", "/api/platform/backups"): "read",
    ("GET", "/api/platform/backups/{name}/download"): "read",
    # PUSH CİHAZ DEFTERİ OKUMASI (5.4c, göç 20260909_0077). İzin
    # `/api/push/` önek kuralından geliyor (`app/auth.py`) ve BİLİNÇLİDİR:
    # uç YALNIZ çağıranın KENDİ cihazlarını döndürür — `user_id` istekten
    # değil OTURUMDAN okunuyor. Ayrı bir izne bağlamak, `read` taşıyan bir
    # rolün kendi telefonunu göremediği bir sistem üretirdi.
    ("GET", "/api/push/devices"): "read",
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
    # Parti mutabakatinin okumasi (1B-G, GOC YOK). Ayni `/api/products`
    # onekinden, ayni `read` izni: rapor, `GET /api/products/{id}/lots`in
    # gosterdigi parti kiriliminin KIRACI GENELINDEKI toplamidir ve ayni
    # olguyu iki farkli iznin arkasina koymak, birinin otekinden sessizce
    # ayrismasina yol acardi.
    ("GET", "/api/products/lots/mutabakat"): "read",
    ("GET", "/api/products/{product_id}/lots"): "read",
    ("GET", "/api/products/{product_id}/qr.png"): "read",
    ("GET", "/api/products/{product_id}/warehouse-stock"): "read",
    # SEC-3: `read` -> `purchases`. Liste (`transactions.py:1263`)
    # supplier_name, final_total (ALIS tutari), paid_amount, due_date;
    # `last-purchase-price` (`:1418`) tedarikci+urun bazinda `unit_price` ve
    # `discount_percent` — yani TEDARIKCI MALIYETININ TA KENDISI. Bu, `auth.py`
    # icindeki mustahsil makbuzu kuralinin ("okumasi da ticari olarak
    # hassastir") AYNI gerekcesidir ve kural onunla AYNI bolgeye yazildi.
    # KAYBEDEN: `satis`, `rapor`. `depo` `purchases` TASIR ve ETKILENMEZ.
    # NOT: somut `GET /api/purchases/1` bu envanterde YOKTUR — sablonu
    # `/api/{kind}/{transaction_id}`dir ve envanter SABLONU sorar, `{kind}`
    # hicbir oneke uymaz. O daralmanin kaniti `DYNAMIC_PERMISSION_CASES` ve
    # `test_sec3_read_daraltma.py`nin GERCEK istegidir.
    ("GET", "/api/purchases"): "purchases",
    ("GET", "/api/purchases/last-purchase-price"): "purchases",
    ("GET", "/api/quick-pick"): "read",
    ("GET", "/api/search"): "read",
    ("GET", "/api/search/parts"): "read",
    # SEC-3: `read` -> `purchases` (BES UC). Liste ve detay (`finance.py:128`,
    # `:470`) tax_number, opening_balance, phone, email, address,
    # current_balance, overdue_amount, risk_limit; `/documents` alis belgeleri
    # + final_total; `/statement` TUM alis defteri + acilis/kapanis bakiye.
    # KAYBEDEN: `satis`, `rapor`. `depo` ETKILENMEZ.
    # `/api/suppliers/{id}/advances` ZATEN `purchases`ti (D2) ve DEGISMEDI —
    # yeni kural onu da kapsiyor, sonuc AYNI.
    ("GET", "/api/suppliers"): "purchases",
    ("GET", "/api/suppliers/{supplier_id}"): "purchases",
    ("GET", "/api/suppliers/{supplier_id}/documents"): "purchases",
    ("GET", "/api/suppliers/{supplier_id}/statement"): "purchases",
    # PDF: handler ZATEN `purchases` istiyordu (`statement.py:46-68` ->
    # `outputs.py:875`). ERISIM DEGISMIYOR; degisen, kapinin middleware'e
    # tasinmasi ve boylece PDF ile JSON'in TEK kapiya baglanmasi.
    ("GET", "/api/suppliers/{supplier_id}/statement.pdf"): "purchases",
    ("GET", "/api/technician-profiles"): "read",
    ("GET", "/api/warehouse-transfers/{transfer_id}"): "read",
    ("GET", "/api/warehouses"): "read",
    ("GET", "/api/warehouses/counts"): "read",
    ("GET", "/api/warehouses/counts/{count_id}"): "read",
    # SEC-3: `read` -> `stock`. Yanit `unit_price` ve TEDARIKCI ONERISI
    # tasiyor (`warehouses.py:139`). Tek cagirani `/depolar` sayfasi ve o sayfa
    # ZATEN `stock` nav izninde (`Warehouses.tsx:55`), yani EKRAN tarafinda
    # kimse kaybetmiyor; daralan sey ucun API'den DOGRUDAN cagrilabilirligidir.
    # `auth.py`de ONEK DEGIL TAM YOL: komsu depo uclari (`/warehouses`,
    # `/stock`, `/transfers`, `/counts`) `read`te KALIYOR.
    # KAYBEDEN: `muhasebe`, `satis`, `rapor`. `depo` ETKILENMEZ.
    ("GET", "/api/warehouses/replenishment"): "stock",
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
    # WHATSAPP ESLESTIRME DEFTERI (WA2, goc 20260910_0079). `users` ve bu
    # OLCULDU, varsayilmadi: kural yazilmadan once `required_permission`
    # bu yol icin "read" veriyordu (genel SAFE_METHODS kurali) — yani okuma
    # yetkisi olan HER rol firmanin hangi numaralarinin bota bagli oldugunu
    # gorurdu. "Kime WhatsApp'tan ulasilabilir" listesi bir KULLANICI
    # YONETIMI yuzeyidir ve `/api/users` ile AYNI kapidan gecmelidir.
    # Telefon CEVAPTA MASKELI doner; tam numara hicbir uctan cikmaz.
    ("GET", "/api/whatsapp/links"): "users",
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
# 181 -> 182: 5.4c push cihaz defterinin TEK OKUMA ucu
# (`GET /api/push/devices`, göç 20260909_0077). Drift raporu ÖLÇÜLDÜ:
# `missing`/`stale`/`changed` ÜÇÜ DE BOŞ — artış YALNIZ eklemedir, hiçbir
# ucun izni DEĞİŞMEDİ. 5.4c'nin diğer İKİ ucu (POST ve DELETE) bu
# envantere GİRMEZ: bu dosya YALNIZ GET'leri sayar; o ikisi
# `test_undeniable_endpoint_population.py`nin `SELF_SCOPED_WRITE_
# EXEMPTIONS` çapasında ve orada GERÇEK istekle kanıtlanıyor.
# 182 -> 183: 1B-G'nin TEK OKUMA ucu (`GET /api/products/lots/mutabakat`,
# GÖÇ YOK). İzin `read` ve `auth.py`ye SATIR EKLENMEDİ: yol `/api/products`
# önekindedir, yani izin ZATEN doğru aileden geliyor — bu ÖLÇÜLDÜ
# (`required_permission("GET", "/api/products/lots/mutabakat") -> "read"`),
# varsayılmadı. Drift raporu ÖLÇÜLDÜ: `missing`/`stale`/`changed` ÜÇÜ DE BOŞ
# — artış YALNIZ eklemedir, hiçbir ucun izni DEĞİŞMEDİ.
# 183 -> 184: WA1 Meta webhook DOGRULAMA ucu (`GET /api/whatsapp/webhook`,
# goc 20260910_0078). Sayi 1B-G (#79) develop'a INDIKTEN SONRA YENIDEN
# OLCULDU: iki dal da kendi tabaninda `182 -> 183` diyordu ve ikisi de
# KENDI tabaninda DOGRUYDU; ayni sayiyi AYRI uclar icin soyleyen dallar
# birlesince sayi SECILMEZ, TOPLANIR — iki duzyazidan biri otekinin yerine
# konsaydi sayi duzelir ama GEREKCE YALAN olurdu, o yuzden IKISI DE duruyor.
# Drift raporu YENIDEN OLCULDU: `missing`/`stale`/`changed` UCU DE BOS.
# Ucun POST ikizi bu envantere GIRMEZ (bu dosya YALNIZ GET sayar); o uc
# `test_undeniable_endpoint_population.py`nin `PUBLIC_WEBHOOK_EXEMPTIONS`
# capasindadir ve orada GERCEK istekle kanitlaniyor.
# 184 -> 185: WA2 esleştirme defterinin TEK OKUMA ucu
# (`GET /api/whatsapp/links`, goc 20260910_0079). Izin `users` ve
# `auth.py`ye `/api/whatsapp/` ONEK KURALI EKLENDI — bu VARSAYILMADI,
# OLCULDU: kural yazilmadan once `required_permission("GET",
# "/api/whatsapp/links")` -> "read" veriyordu ve ayni ailenin POST/DELETE
# uclari `__admin_only__`e dusuyordu, yani ayni uc ailesi metoda gore IKI
# FARKLI kapidan geciyordu. Webhook uclarinin degeri DEGISMEDI ve bu da
# OLCULDU: ikisi de `PUBLIC_API`dedir, envanter onlari `required_permission`a
# HIC sormaz. Drift raporu OLCULDU: `missing`/`stale`/`changed` UCU DE BOS —
# artis YALNIZ eklemedir, hicbir ucun izni DEGISMEDI. Ucun uc yazma ikizi bu
# envantere GIRMEZ (bu dosya YALNIZ GET sayar); onlar
# `test_route_security_contracts.py`nin sozlesme envanterindedir.
# SEC-3 (`read` DARALTMASI, GOC YOK): sayim 186'da SABIT KALDI ve bu SAYININ
# KENDISI BIR IDDIADIR — SEC-3 hicbir rota EKLEMEDI, SILMEDI ve yol sablonu
# DEGISTIRMEDI; yalniz 19 GET'in IZNINI degistirdi. `missing`/`stale` UCU DE
# BOS olculdu, `changed` ise TAM 19 satir: yedisi `purchases`, sekizi `sales`,
# ucu `payments`, biri `stock`. Envanterin kendi hata mesaji bu 19'u tek tek
# yazar; her satirin gerekcesi yukarida ilgili girdinin ustunde duruyor.
#
# 19'un DAGILIMI (olculdu, tahmin EDILMEDI):
#   purchases (7): /api/purchases, /api/purchases/last-purchase-price,
#                  /api/suppliers, /api/suppliers/{id},
#                  /api/suppliers/{id}/documents,
#                  /api/suppliers/{id}/statement,
#                  /api/suppliers/{id}/statement.pdf
#   sales     (8): /api/invoices, /api/invoices/{id},
#                  /api/invoices/{id}/history, /api/invoices/{id}/pdf,
#                  /api/invoices/{id}/einvoice/status,
#                  /api/customers/{id}/statement,
#                  /api/customers/{id}/statement.pdf,
#                  /api/orders/last-sale-price
#   payments  (3): /api/payment-allocations/{payments,orders,charges}/{id}
#   stock     (1): /api/warehouses/replenishment
#
# YIRMINCI YOL BU ENVANTERDE GORUNMEZ: `GET /api/purchases/1` de `read`ten
# `purchases`a dustu, ama SABLONU `/api/{kind}/{transaction_id}`dir ve envanter
# `required_permission`i HAM yolla cagirir — `{kind}` hicbir oneke uymadigi
# icin sablon `read`te KALIR ve bu SATIR KIMILDAMAZ. Daralmanin kaniti bu
# dosyada DEGIL, `test_route_security_contracts.py`nin `DYNAMIC_PERMISSION_CASES`
# girdisinde ve `test_sec3_read_daraltma.py`nin GERCEK HTTP istegindedir.
# E4a (e-IRSALIYE DUZ SEVK, goc 20260913_0083): sayim 186 -> 190. DORT yeni
# GET ve DORDU DE "sales"; hicbir mevcut ucun izni DEGISMEDI. Drift raporu
# OLCULDU: `missing`/`stale`/`changed` UCU DE BOS — artis YALNIZ eklemedir.
# Ailenin UC YAZMA ikizi (POST create/submit/sync) bu envantere GIRMEZ (bu
# dosya YALNIZ GET sayar); onlar `test_route_security_contracts.py`nin
# sozlesme envanterindedir ve orada sayim 392/301 -> 399/307.
GET_INVENTORY_COUNT = 190
GET_INVENTORY_FINGERPRINT = (
    # 5.4c (göç 20260909_0077): parmak izi EN SON alındı — önce uç yazıldı,
    # sonra `auth.py`ye `/api/push/` önek kuralı eklendi, sonra izin
    # `required_permission` ile ÖLÇÜLDÜ ("read"), sonra envantere ve
    # `ROUTE_REASONS`a girdi. 1a615fb1 -> cfb171c3.
    # 1B-G (GOC YOK): parmak izi EN SON alindi — once uc yazildi, sonra izin
    # `required_permission` ile OLCULDU ("read"), sonra envantere ve
    # `ROUTE_REASONS`a girdi, EN SON parmak izi. cfb171c3 -> 070f4e0a.
    # WA2 (goc 20260910_0079): AYNI SIRA izlendi — (1) uclar yazildi, (2)
    # `auth.py`ye `/api/whatsapp/` onek kurali eklendi, (3) izin
    # `required_permission` ile OLCULDU ("users"), (4) envantere ve
    # `ROUTE_REASONS`a girdi, (5) sayim 184 -> 185 olarak yeniden olculdu,
    # (6) EN SON parmak izi turetildi. bdf500d3 -> 22fcac03.
    # WA1 (göç 20260910_0078): aynı sıra izlendi — önce uç yazıldı, sonra
    # TAM YOL `PUBLIC_API`ye eklendi, sonra izin ÖLÇÜLDÜ ("public": uç yetki
    # kapısına HİÇ girmiyor), sonra envantere ve `ROUTE_REASONS`a girdi, EN
    # SON parmak izi alındı. 1B-G (#79) SONRASI BİRLEŞMİŞ AĞAÇTA yeniden
    # türetildi: bu dalın önceki `cfb171c3 -> efd2dfc0` ölçümü, taban
    # değiştiği anda GEÇERSİZ oldu ve ARİTMETİKLE taşınamazdı — parmak izi
    # envanterin TAMAMINDAN türüyor. 070f4e0a -> bdf500d3.
    # E2 (GOC YOK): AYNI SIRA izlendi — (1) uc yazildi
    # (`GET .../einvoice/download`), (2) `auth.py`ye ACIK bir kural eklendi
    # (onek+sonek birlikte; salt onek fatura listesini de yakalardi), (3) izin
    # `required_permission` ile OLCULDU ("sales"; kural yazilmadan onceki
    # olcum "read"di), (4) envantere girdi — `ROUTE_REASONS`a GIRMEDI ve bu
    # DOGRU: o kapi yalniz `read`/`public` GET'leri icin gerekce ister,
    # "sales" onun disindadir, (5) sayim 185 -> 186 olarak yeniden olculdu,
    # (6) EN SON parmak izi turetildi. 22fcac03 -> 445ebb4f.
    # SEC-3 (`read` DARALTMASI, GOC YOK): AYNI SIRA izlendi ama BASKA bir
    # sirayla, cunku yeni uc YOK — (1) `auth.py`ye genel guvenli-metot
    # kuralinin USTUNE alti kural yazildi, (2) izinler `required_permission`
    # ile OLCULDU, (3) envanterdeki 19 satir olculen degerlerle guncellendi,
    # (4) `missing`/`stale`in BOS ve sayimin 186'da SABIT oldugu yeniden
    # olculdu, (5) EN SON parmak izi turetildi. Parmak izinin degismesi bir
    # KAYMA DEGIL, kapinin ISLEVIDIR: yuk `permission` alanini tasiyor ve o
    # alan 19 satirda bilerek degisti. 445ebb4f -> 0e8ce4f3.
    # E4a (goc 20260913_0083): parmak izi EN SON alindi — once uc yazildi,
    # sonra `auth.py`ye `/api/despatch-notes` onek kurali eklendi, sonra izin
    # `required_permission` ile OLCULDU ("sales", dordu de), sonra envantere
    # girdi. 0e8ce4f3 -> 0f87b6bd.
    "0f87b6bd0483c246d6cbb041bac9002cc8f1c09fe97297e16719569cc6a5f2d9"
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
