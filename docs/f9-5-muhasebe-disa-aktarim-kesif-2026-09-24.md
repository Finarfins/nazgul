# Faz 9-5 — Muhasebe fişi / KDV dışa aktarımı (Luca / Mikro / Logo) + mali müşavir salt-okunur rolü — KEŞİF

**Ölçüm tarihi:** 2026-09-25 (dosya adı brifingdeki 2026-09-24'ü korur) · **Taban:** `origin/develop` =
`63e57f6` (#159 birleşmesi; ilk ölçüm `8a6e3e1`te yapıldı, §7.0 pinleri `63e57f6`te YENİDEN ölçüldü ve AYNI
çıktı) · **Düzeltme 1 (doc lens):** 2026-09-25 · **Worktree:** `C:\Users\HHH\nazgul-f95` · **Dal:**
`docs/f9-5-muhasebe-disa-aktarim-kesif` **Kapsam:** SALT OKUMA keşif. Uygulama kodu, göç, test ve şema
DEĞİŞMEDİ.

Her sayı bu taban üzerinde ölçüldü ve `dosya:satır` ile yazıldı. Ölçülemeyen her iddia **DOĞRULANMADI**
etiketi taşır (Ek). Bütün `app/` ve `tests/` yolları `backend/` altındadır.

> **Brifing ile taban farkı (ölçüldü):** #155 hâlâ **AÇIK**; pinler ve #155 sonrası değerler §7.0'da.
> `docs/ROADMAP.md` (47 satır) **Faz 9-5'i HİÇ ANMIYOR**; faz tanımı `docs/yol-haritasi.html:84` ve
> `docs/FAZ-11-adaylar-2026-09-09.md:6,24`tedir.

---

## 0. YÖNETİCİ ÖZETİ — dört cümlelik sonuç

1. **Muhasebe olayının dört ana kaynağı düzenlenebilir ve SERT silinebilir; hiçbirinde `updated_at` YOK.**
   Satış (`orders`), alış (`purchases`), iade (`returns`) ve serbest ödeme (`payments`) `PUT` ile değişir ve
   `DELETE FROM` ile satırı gider (`routers/transactions.py:1401,1414,1508-1625`,
   `routers/workflow.py:729,748`, `routers/finance.py:558,630`). İz her yolda AYNI değil (ölçüldü): satış
   `PUT`u `record_change` → `entity_change_logs` + `sale.update` yazar (`transactions.py:1153-1165`), satış
   silmesi `sale.cancel` (`:1638`); ödeme `PUT`u ve silmesi `record_change` + `payment.delete` yazar
   (`finance.py:592,616,641-665`); iade `PUT`u yalnız `return.create` aktivitesi (`workflow.py:619`); **alış
   `PUT`/`DELETE` ve iade `DELETE` HİÇ iz bırakmaz**. İz olan yerde bile bu bir denetim KAYDIDIR, belge
   satırında sürüm DEĞİL. Tam göçlenmiş şemada bu dört tabloda `created_at`/`updated_at`/`version` sütunu
   **SIFIR** (`PRAGMA table_info`, §1.8). Yani "belge başına dışa aktarıldı damgası" ile SONRADAN DEĞİŞİMİ
   yakalamak bugün İMKÂNSIZDIR → **durumsuz, belirlenimci yeniden aktarım + dönem kilidi** önerilir (K5).
2. **"Yeni `muhasebe` rolü" seçeneği ADI YÜZÜNDEN düşer: `muhasebe` rolü ZATEN VAR ve YAZAR**
   (`app/auth.py:230-235`: `sales`, `purchases`, `payments`, `finance`…). Daha önemlisi izin modeli **alan
   başına okuma/yazma AYIRMAZ**: `/api/payments` kuralı metoda bakmaz (`app/auth.py:1075-1076`), yani
   `payments` hem `GET` hem `POST/PUT/DELETE /api/payments` verir; `/api/finance` (`:1092`) ve
   `/api/producer-receipts` (`:1168`) de öyle. `/api/purchases`ta GET `purchases` ister (`:1285-1287`) ve
   yazma da `purchases` (`:1379`). (`/api/orders` karşı örnek DEĞİL: her GET önce `read`e düşer, `:1346-1347`;
   `:1354` yalnız yazmayı `sales`a bağlar.) Hassas okumayı açan izin aynı zamanda yazmayı açar → salt-okunur
   bir müşavir mevcut izinlerden KURULAMAZ; ya rol düzeyinde bir "salt okunur" kapısı ya da yalnız yeni
   muhasebe yüzeyini gören dar bir izin gerekir (K7).
3. **KDV bugün SATIR başına saklanıyor ve aylık özet TEK Core sorgusuyla çıkıyor** — demo verisinde
   çalıştırıldı (§2.4). Ama dört gerçek boşluk var: **tevkifat HİÇBİR yerde yok** (`grep -i tevkifat app` →
   0), `vat_rate` kapalı bir küme değil (`schemas.py:63,108`: `ge=0, le=100`), servis faturasında **işçilik
   satırı %0 KDV ile kesiliyor** (`invoice_service.py:57-58`) ve belge iskontosu servis faturasında
   `tax_amount`ı DÜŞÜRMÜYOR (`invoice_service.py:68,72`).
4. **Hesap planı eşlemesi SIFIRDAN kurulmalı** — §4.1 deseni (`hesap_kodu|account_code|chart_of|
   hesap_plan|tek_duzen|tekduzen`) `app/`, `alembic/`, `frontend/src`te **0 isabet**. (Çıplak `chart` deseni 0
   DEĞİL: 1 backend + 7 ön yüz dosyası — grafik bileşenleri; 0'ı veren `chart_of`tur.) Önerilen tek yeni
   kiracı tablosu `muhasebe_hesap_eslemeleri` (`TENANT_TABLES` 127 → 128).

---

## 1. MUHASEBE OLAYLARININ KAYNAĞI — bugünkü gerçek

### 1.1 Belge ailesi haritası

| Kaynak | Başlık / satır | Tutar sütunları | KDV sütunu | Durum | Sonradan değişir mi | Silme |
|---|---|---|---|---|---|---|
| Satış | `orders` / `order_items` (`core_schema.py:115-178`) | başlık `subtotal`,`vat_total`,`grand_total`,`discount_percent`,`discount_amount`,`final_total`,`paid_amount` (`:121-136`); satır `line_subtotal`,`line_vat`,`line_total`,`discount_percent`,`discount_amount` (`:167-168`) | `order_items.vat_rate` `MONEY` (`:163`) | `draft/pending/approved/completed/cancelled` (`document_engine.py:14`) | **EVET** `PUT /api/orders/{id}` (`transactions.py:1401`) | **SERT** `DELETE FROM` (`transactions.py:1621-1625`) |
| Alış | `purchases` / `purchase_items` (`core_schema.py:180-254`) | aynı küme (`grand_total` `:188`, iskonto `:189-190`, `paid_amount` `:200`, satır iskontosu `:226-227`) | `purchase_items.vat_rate` (`:222`) | aynı küme | **EVET** `PUT /api/purchases/{id}` (`:1414`) | **SERT** (aynı uç) |
| İade (1B-E) | `returns` / `return_items` (`core_schema.py:373-423`) | `subtotal`,`vat_total`,`total`; satır üçlü | `return_items.vat_rate` (`:411`) | `status` + `return_type ∈ {sale_return, purchase_return}` (`routers/workflow.py:84-112`) | **EVET** `PUT /api/workflow/{kind}/{id}` (`workflow.py:729`) | **SERT** (`workflow.py:773,791`) |
| Servis faturası | `invoices` / `invoice_items` (göç `20260718_0012`) | satır `tax_amount`,`total`,`customer_payable`,`company_payable` | `invoice_items.tax_rate` `Numeric(9,4)` + `tax_exempt` | `ISSUED → CANCELLED` | **HAYIR** — "immutable enterprise invoice aggregate" (göç başlığı); iptal CAS `WHERE status='ISSUED'` (`routers/invoices.py:185`) | YOK; iptal satırı TUTAR |
| Tahsilat/ödeme | `payments` (`core_schema.py:256-274`) | `amount` | YOK | yok | **EVET** `PUT /api/payments/{id}` — belgeye bağlı olan hariç (`finance.py:566-567`) | **SERT** (`finance.py:630-636`) |
| Tahsis | `payment_allocations` (göç `20260726_0025`) | `amount` | YOK | ters kayıt (`reversed_at`, `reversal_of_allocation_id`) | HAYIR — ters kayıtla | ters kayıt |
| Kasa/banka | `finance_transactions` (`finance_engine.py:26-40`) | `amount`,`direction` | YOK | yok | — | `DELETE /api/finance/transactions/{id}` (`finance.py:857`) |
| Eski çek/senet (v1) | `financial_instruments` (`/api/finance/instruments`) | `amount` | — | `INSTRUMENT_STATUSES` | **EVET** `PUT …/{id}/status` bağlı `finance_transactions` satırını **SERT siler** ve yeniden yazar (`finance.py:896-913`, `DELETE` `:903`) | **SERT** `DELETE …/{id}` (`finance.py:915-921`); iki uçta da aktivite/`record_change` **YOK** |
| Müstahsil (D1) | `producer_receipts` / `producer_receipt_items` (göç `20260905_0070`) | `gross_amount`,`withholding_total`,`social_security_total`,`net_payable` | **BİLEREK YOK** (göç başlığı :24-32) | `draft/issuing/issued/cancelled` (göç :189-191) | **HAYIR — düzenleme ucu YOK**: uçlar yalnız `POST` oluştur, `GET` ×2, `POST …/issue`, `POST …/cancel` (`routers/mustahsil.py:308,450,491,525,606`); taslak da DEĞİŞTİRİLEMEZ | **SİLME UCU YOK** (`routers/mustahsil.py:55-58`) |
| Vergi yükümlülüğü (D2) | `tax_liabilities` (göç `20260906_0071`:239-285) | `amount`, `kind ∈ {withholding, social_security}`, `due_period 'YYYY-MM'` | — | `settled_at` | — | **SERT**: makbuz iptali kapanmamış (`settled_at IS NULL`) satırları SİLER (`avans_engine.py:358-364`) |
| Avans (D2) | `supplier_advances` + bir `payments` satırı (`payment_id` NOT NULL, göç 0071 başlığı) | `amount`,`remaining_amount` | — | — | — | — |
| Vade farkı | `receivable_charge_documents` (göç `20260727_0028`) | `net_amount`,`vat_amount`,`gross_amount` | `vat_rate_snapshot` | `draft/posted/reversed` + `reversal_of_document_id` | HAYIR — ters kayıtla | ters kayıt |
| Çek/senet | `cek_senetler` (`cek_senet_schema.py:26-52`) | `tutar` | — | 6 durum (§1.5) | geçişle | — |

`vat_rate` taşıyan tabloların TAMAMI (ölçüm: `grep vat_rate|tax_rate` hem `app/` hem `alembic/versions/`):

| # | Tablo.sütun | Tip | Kaynak |
|---:|---|---|---|
| 1 | `products.vat_rate` | `MONEY`, varsayılan 20 | `core_schema.py:75` |
| 2 | `order_items.vat_rate` | `MONEY`, varsayılan 20 | `core_schema.py:163` |
| 3 | `purchase_items.vat_rate` | `MONEY`, varsayılan 20 | `core_schema.py:222` |
| 4 | `quote_items.vat_rate` | `MONEY`, varsayılan 20 | `core_schema.py:356` |
| 5 | `return_items.vat_rate` | `MONEY`, varsayılan 20 | `core_schema.py:411` |
| 6 | `sales_order_items.vat_rate` | `MONEY`, varsayılan 20 | `workflow.py:67` |
| 7 | `invoice_items.tax_rate` (+`tax_exempt`) | `Numeric(9,4)` | göç `0012` |
| 8 | `work_order_parts.tax_rate` | `Numeric(9,4)`, `>= 0` CHECK | göç `0010`:30,37 |
| 9 | `late_fee_policies.vat_rate` | `Numeric(9,4)`, **CHECK `vat_rate = 0`** | göç `0027`:47,84 |
| 10 | `receivable_charge_documents.vat_rate_snapshot` | `Numeric(9,4)` | göç `0028`:94 |
| 11a | `supplier_price_import_lines.vat_rate`, `.old_vat_rate` | `Numeric(5,2)` | göç `0037`:117,123 |
| 11b | `supplier_part_prices.vat_rate` | `Numeric(5,2)` NOT NULL | göç `0037`:159 |

`core_schema.py` yalnız 1–5'i taşır (brifingin "≥5"i budur). Muhasebe fişine giren BELGE satırları 2, 3, 5, 7
ve 10'dur; 1/4/6/8/11a/11b belge değil kart/teklif/sipariş/fiyattır ve **fişe GİRMEZ**.

### 1.2 Satış ve alış (asıl hacim)

* **Fiyat KDV DAHİLDİR ve matrah ayrıştırılır**: `line_subtotal = money(line_total / (1 + vat_rate/100))`,
  `line_vat = line_total - line_subtotal` (`transactions.py:256-266`).
* **Belge iskontosu satırlara largest-remainder ile DAĞITILIR** ve saklanan `line_subtotal/line_vat` DAĞITIM
  SONRASIDIR (`transactions.py:303-322`). Kayıt anında iki eşitlik zorlanır: Σ satır = `final_total` ve
  matrah+KDV = `final_total` (`:325-328`). Yani **satırlar kanonik kaynaktır** —
  `docs/financial-rounding-policy.md:20-21` "Bağlayıcı invariant" ile aynı.
* **ÖLÇÜLEN İSTİSNA — eski/dışarıdan yazılmış veride invariant TUTMUYOR:** demo tohumunda **60 satış
  belgesinin 11'inde** başlık `vat_total` ≠ Σ `line_vat` (kuruş farkı; 2026-07'de başlık 38.587,91 / satırlar
  38.587,94). Sebep: `seed_demo_data.py:277` başlık KDV'sini `subtotal × 0,20` olarak hesaplıyor, satır
  toplamından değil. Alışta 25'in 0'ı sapıyor. Yuvarlama politikası "mevcut kayıtlar yeniden hesaplanmaz"
  diyor (`financial-rounding- policy.md:24-29`) — yani **üretimde de başlık/satır ayrışan eski belge
  olabilir** (sayısı DOĞRULANMADI). Fiş SATIRDAN kurulmalı ve ayrışan belge aktarım raporunda ADIYLA
  listelenmelidir (K4).
* **Tarih `String(30)`dur** (`orders.order_date`, `purchases.purchase_date`, `returns.return_date`,
  `payments.payment_date`) — ISO `YYYY-MM-DD` metni (`typeof` = `text`, tohumda ölçüldü). Dönem süzgeci
  `substr(…,1,7)` iki lehçede de çalışır; `invoices.created_at` ve `producer_receipts.issued_at` ise
  `DateTime(timezone=True)`dir ve **PG'de `substr(timestamptz,…)` ÇALIŞMAZ** (§2.4 G5).
* **Hangi durum deftere girer?** Stok yazan küme `{approved, completed}` (`document_engine.py:16`). `pending`
  ve `draft` bir BELGE değildir. Öneri: fişe yalnız `STOCK_STATUSES` girer; `cancelled` hiç girmez (satır
  silinmez ama durum iptal).

### 1.3 Servis faturası (`invoices`) — tek DEĞİŞMEZ belge

* Göç başlığı "Create immutable enterprise invoice aggregate" (`20260718_0012`). Tek geçiş `ISSUED →
  CANCELLED`, CAS ile (`routers/invoices.py:173,185`). Anlık görüntüler (`customer_snapshot`,
  `totals_snapshot`, `tax_snapshot`) metin sütunlarıdır.
* Numara `uq_invoices_company_number` ile tekil; `currency` + `exchange_rate` taşır (döviz! — §2.4 G4).
* **ÖLÇÜLEN KUSUR 1 — işçilik %0 KDV:** `LABOR` satırı `compute_line(entry["hours"], entry["hourly_rate"])`
  ile, `tax_rate` argümanı VERİLMEDEN kurulur ve satırın `tax_rate` alanına `0` yazılır
  (`invoice_service.py:57-58`). Parça satırı `part["tax_rate"]` taşır (`:62-63`). Türkiye'de servis işçiliği
  genel orana tabidir (hukuki teyit **DOĞRULANMADI**); bugünkü veriyle KDV özeti işçilik matrahını **%0
  satırında** gösterir.
* **ÖLÇÜLEN KUSUR 2 — genel iskonto KDV'yi düşürmüyor:** `item_total = money(line.total - share)` ama
  `"tax_amount": line.tax` (`:68,72`). Yani iskonto payı KDV'den değil matrahtan düşer; `total - tax_amount`
  ile türetilen matrah OLDUĞUNDAN KÜÇÜK, KDV OLDUĞUNDAN BÜYÜK çıkar. Satış/alış yolunun "KDV dahil dağıtım"
  kuralı (`transactions.py:303`) burada YOK. **Bu BİLİNÇLİ bir tercih:** `invoice_service.py:47-52` yorumu
  payın `discount_amount`a katlandığını ve `tax_amount`ın satır KDV'si OLARAK KALDIĞINI yazar (gerekçe: satır
  özdeşliği `total == raw - discount_amount + tax_amount` ve iskontosuz yolun bayt-aynılığı). Yani kusur bir
  kod hatası değil, KDV matrahı açısından sonucu tartılmamış bir TASARIM kararıdır; K8 onu değiştirmeyi
  önerir.
* Garanti payı (`company_payable`, `customer_payable`) satır başına ayrılır (`:69`); muhasebede iki ayrı cari
  kalemi demektir (müşteri 120 / garanti veren firma 120 ya da 136 — hesap eşlemesi sorusu, K3).

### 1.4 Müstahsil makbuzu (D1) + vergi yükümlülüğü (D2)

* **KDV YOK ve bu bilinçli** (`app/mustahsil.py:24-32`, göç 0070 :24-32): satıcı çiftçi KDV mükellefi
  değildir. Fişte KDV satırı ÜRETİLMEZ.
* **Stopaj ve Bağ-Kur oranı SATIRDA saklanır, kodda sabit YOKTUR** (`withholding_rate`,
  `social_security_rate`; göç 0070 :8-21, `app/mustahsil.py` "ORANLAR BURADA SABİT DEĞİLDİR"). Brifingin "rate
  table where?" sorusunun cevabı: **oran tablosu YOK, bilinçli olarak YOK**.
* **Yükümlülük defteri VAR:** `tax_liabilities` — `kind ∈ {withholding, social_security}`, `due_period` =
  makbuzun KESİLDİĞİ ay ("Beyanname dönemi budur; ödemenin yapıldığı ay DEĞİL", göç 0071 :246-248),
  `UNIQUE(company_id, receipt_id, kind)` ile çift beyan şemaca engelli. **Muhtasar/stopaj tarafı için hazır
  kaynak budur** (bu keşfin konusu KDV1'dir; muhtasar ayrı soru).
* **ÇİFT SAYIM RİSKİ:** `producer_receipts.purchase_id` nullable bir bağdır (`routers/mustahsil.py:406-416`).
  Makbuz bir `purchases` satırına bağlıysa ve o alışın kalemleri varsayılan `vat_rate=20` ile yazılmışsa
  (`core_schema.py:222`), KDV özeti **olmayan bir indirilecek KDV** sayar. Tohumda makbuz 0 olduğu için
  ölçülemedi; kural: `purchase_id` ile makbuza bağlı alış satırları KDV özetinde **%0 değilse ADIYLA uyarı**
  üretmeli.
* Silme ucu YOK, iptal satırı tutar, numara korunur (`routers/mustahsil.py:606-625`) → fiş belirlenimcidir.
  **Düzenleme ucu da YOK** — taslak dahil (§1.1).
* **İPTAL `tax_liabilities`i SİLER:** makbuz iptali, kapanmamış (`settled_at IS NULL`) yükümlülük satırlarını
  `DELETE FROM` ile kaldırır (`avans_engine.py:358-364`; kapanmış olan iptali 409 ile engeller, `:347-356`).
  Yani 9-5d'nin `due_period` okuduğu tablo GERİYE DÖNÜK küçülür: Temmuz muhtasarı raporlandıktan sonra iptal
  edilen bir Temmuz makbuzu, yeniden üretilen Temmuz raporundan İZSİZ düşer (§9 risk 4, §7.4 tasarım notu).

### 1.5 Çek/senet — 36 çiftin kaçı muhasebeyi ilgilendirir

`app/cek_senet_engine.py:13`: "Matris 6x6 = 36 çift; 7'si izinli, 29'u değil". İzinli yedi geçiş (`:44-51`) ve
muhasebe anlamı:

* portföyde → tahsile_verildi: **EVET (kıymet hareketi)** 101 → 101 alt hesap/bankadaki çekler; evrak yer
  değiştirir, cariye dokunmaz.
* portföyde → ciro_edildi: **EVET** 101 A / 320 B — CS2: ciro anahtarı açıksa tedarikçi ödemesi yazar
  (`MUHASEBE_HEDEFLERI`, `:55-72`). portföyde → iade: **EVET** 101 A / 120 B.
* tahsile_verildi → tahsil_edildi: **EVET** 101 A / 102 B (`tahsil_hesap_id`, `payment_id`); → karşılıksız:
  **EVET** 101 → karşılıksız çekler + 120 borç belgesi (CS2 `bounced_check`, göç 0086); → portföyde: HAYIR,
  yalnız yer (`:7-8`). karşılıksız → iade: **EVET**, evrak cariye döner.

Yani **7 izinli geçişin 6'sı** bir muhasebe fişidir; 29 izinsiz geçiş hiç oluşmaz. **AMA** modül başlığı
"MUHASEBE YOK (karar 1, CS2): geçiş `payments`a, `finance_transactions`a ya da borç belgelerine YAZMAZ"
(`:20-23`) — yani çek hareketlerinin fişi, durum geçmişi tablosu olmadan yalnız ANLIK durumdan kurulabilir.
Geçiş TARİHİ yalnız `endorsed_date` ve `tahsil_tarihi` için saklanıyor (`cek_senet_schema.py:34,42`);
`tahsile_verildi` ve `iade` anının tarihi YOKTUR → **v1 kapsamı DIŞI** önerilir (K8).

### 1.6 İrsaliye (E4) — muhasebe DEĞİL

`despatch_lines` (`despatch_schema.py:24-39`): `quantity`, `unit_code`, `item_name` — **fiyat, tutar, KDV
sütunu YOK**. `despatch_notes` yalnız durum/numara taşır (`:40-54`). İrsaliye mal hareketini belgeler, bir
alacak ya da borç doğurmaz; muhasebe olayı faturadır (şema ölçümü: **DOĞRULANDI**). E-Defter tarafında da
irsaliye yevmiyeye girmez, belge türü olarak fatura atfedilir — bu mevzuat iddiası **DOĞRULANMADI** (GİB
kılavuzu okunmadı, §3.4).

### 1.7 Gecikme/vade farkı

`late_fee_policies`: `tax_mode = 'NO_VAT'` ve `vat_rate = 0` **CHECK ile zorunlu** (göç 0027 :80-86). Oysa
belge tablosu `vat_amount` ve `vat_rate_snapshot` taşıyor (0028 :94,98). Vade farkı Türkiye'de KDV'ye tabidir
(KDVK md. 24/c — hukuki teyit **DOĞRULANMADI**); bugünkü şema KDV'li vade farkını YAZDIRMAZ. Fişte `600/642 +
391` yerine yalnız `642` doğar. Bu bir 9-5 kusuru değil, KDV raporunda ADIYLA gösterilecek bir KAPSAM
SINIRIDIR.

### 1.8 Değişebilirlik — dışa aktarım idempotency'si için belirleyici ölçüm

Tam göçlenmiş şemada (`seed_demo_data.py` → `alembic upgrade` → `20260918_0090`) `PRAGMA table_info` ile
zaman/sürüm sütunları: `orders`, `purchases`, `returns`, `payments` → **boş** (created_at/updated_at YOK);
`invoices` → `created_at`, `updated_at`, `einvoice_updated_at`; `producer_receipts` → `created_at`,
`updated_at`.

> **Tasarım sonucu:** en büyük üç kaynakta bir belgenin DIŞA AKTARILDIKTAN SONRA değiştiğini
> söyleyecek HİÇBİR sütun yok, silindiğini söyleyecek hiçbir iz yok (sert `DELETE`; yalnız satış
> silmesi `sale.cancel` aktivitesi yazar, `transactions.py:1638` — alış ve iade silmesi YAZMAZ).
> "Aktarıldı" damgası bu tabloların ÜSTÜNE konursa, damgalı belge `PUT` ile değişir ve damga YALAN
> söyler.

---

## 2. KDV — saklama, beyanname ihtiyacı, bugünkü sorgu

### 2.1 Saklama biçimi

* **Satır başına oran**, belge başına DEĞİL — beş belge tablosunun beşinde de.
* Oran **kapalı küme DEĞİL**: `vat_rate: int = Field(ge=0, le=100)` (`app/schemas.py:63,108`); DB'de CHECK
  yok. Bugünkü yasal oranlar (%1/%10/%20) dışındaki bir değer (eski %8/%18) sessizce saklanır. KDV özeti
  bilinmeyen oranı AYRI bir satırda göstermeli, yutmamalı.
* **Tevkifat YOK:** `grep -rni tevkifat app` → 0 isabet; `withholding` (`grep -rli`) yalnız BEŞ dosyada geçer
  ve beşi de müstahsil stopajıdır: `app/avans_engine.py`, `app/avans_schemas.py`, `app/mustahsil.py`,
  `app/mustahsil_schemas.py`, `app/routers/mustahsil.py`. (`finance.py`, `statement.py`, `dashboard.py`,
  `auth.py`de GEÇMEZ.) Kısmi/tam tevkifat (KDVGUT I/C) ve tevkifat kodu hiçbir satırda saklanmıyor.
* **İstisna kodu firma bazlı değil:** `%0` satırında e-Fatura sabit `351` yazar
  (`docs/efatura-adapter-spec.md:226-247`). KDV1'in istisna tabloları için kod gerekir → bugün yalnız "351"
  söylenebilir.

### 2.2 KDV1 beyannamesinin istediği ↔ bugün kaynağı

| KDV1 kalemi | Kaynak bugün | Durum |
|---|---|---|
| Matrah — oran bazında (1/10/20) | `order_items` + `invoice_items` + `receivable_charge_documents` | ✅ satırdan (servis faturası iki kusurla, §1.3) |
| Hesaplanan KDV — oran bazında | aynı | ✅ |
| İndirilecek KDV — oran bazında | `purchase_items` | ✅ (müstahsil bağlı alış uyarısıyla, §1.4) |
| Satıştan iade (indirilecek KDV'ye) | `return_items` `sale_return` | ✅ şema var; tohumda 0 satır |
| Alıştan iade (hesaplanana) | `return_items` `purchase_return` | ✅ şema var |
| Kısmi/tam tevkifat | — | ❌ **YOK** |
| İstisna kodlu teslimler | `tax_exempt` yalnız servis faturasında | ❌ kod yok |
| Müstahsil | `producer_receipts` — KDV yok, stopaj var | ➖ KDV1'e girmez; muhtasar tarafı `tax_liabilities` |
| Dövizli fatura | `invoices.currency/exchange_rate` | ⚠ özet TRY'ye çevirmeli |
| Önceki dönemden devreden KDV | — | ❌ beyanname dışı veri; müşavirin |

KDV1 satır numaraları ve kod listesi **DOĞRULANMADI** (GİB beyanname kılavuzuna bu oturumda erişilmedi).
Tasarım bunları KOD olarak değil, `(yön, oran, tür)` üçlüsü olarak üretmeli; kod eşlemesi rapor katmanında.

### 2.3 Ba-Bs — kapsam DIŞI

`EKSIK-OZELLIKLER-sef-raporu-2026-09-09.md:28` Ba-Bs'yi eksik alt madde sayıyordu;
`docs/FAZ-11-adaylar-2026-09-09.md:6` düzeltiyor: *"Ba-Bs kaldırıldı (565 sıra no'lu VUK tebliği, Eylül
2024'ten itibaren) → Faz 9-5 muhasebe aktarımından çıkarıldı; yerine hesap eşlemesi + dönem kilidi + tekrar
aktarım koruması girer."* Bu belge Ba-Bs için HİÇBİR şey tasarlamaz.

### 2.4 Bugünkü aylık KDV özeti — Core, kiracı kapsamlı, KOŞTURULDU

Betik: oturum karalama alanında `kdv_ozeti.py` (depoya GİRMEDİ). Satış kolu: `select(kaynak,
order_items.vat_rate, Σ line_subtotal, Σ line_vat, count(distinct orders.id))`, `order_items ⋈ orders` (`id` +
`company_id == cid`), `where` iki tabloda `company_id == cid`, `substr(order_date,1,7) == dönem`, `status IN
('approved','completed')`, `group_by vat_rate`. Diğer dört kol aynı biçimde: `ALIS`
(`purchases`/`purchase_items`), `SATIS_IADE` ve `ALIS_IADE` (`returns`/`return_items`, `return_type` süzgeci),
`SERVIS_FATURA` (`invoices`/`invoice_items`, `status='ISSUED'`, matrah = `total - tax_amount`). Hepsi
`union_all`. Kiracı yüklemi her kolda ÜÇ kez (join + iki `where`) — `uq_*_company_id` bileşik FK kuralıyla
aynı savunma derinliği. Müstahsil ayrı sorgu (`status='issued'`, `issued_at` ayı): brüt / stopaj / Bağ-Kur.

PG derlemesi (`postgresql.dialect()`) ölçüldü: `%(company_id_N)s::INTEGER` bağlı parametreler,
`substr(orders.order_date, …)`, `IN (__[POSTCOMPILE…])` — metinde dinamik tablo/sütun YOK.

**Sonuç — `seed_demo_data.py` SQLite, `cid=1` (tohum tarihleri bugüne göre kayar; 2026-09-25 koşusu):**

| Dönem | Satış matrah | Hesaplanan KDV | Belge | Alış matrah | İndirilecek KDV | Belge | Fark (hes.−ind.) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-03 | — | — | 0 | 338.621,00 | 67.724,20 | 5 | −67.724,20 |
| 2026-04 | 186.305,35 | 37.261,07 | 10 | 276.037,00 | 55.207,40 | 5 | −17.946,33 |
| 2026-05 | 102.353,61 | 20.470,73 | 8 | 450.705,00 | 90.141,00 | 6 | −69.670,27 |
| 2026-06 | 186.147,20 | 37.229,46 | 11 | 198.490,00 | 39.698,00 | 2 | −2.468,54 |
| 2026-07 | 192.939,61 | 38.587,94 | 11 | 480.880,00 | 96.176,00 | 7 | −57.588,06 |
| 2026-08 | 145.619,57 | 29.123,92 | 13 | — | — | 0 | +29.123,92 |
| 2026-09 | 136.352,56 | 27.270,51 | 7 | — | — | 0 | +27.270,51 |

Her satır yalnız **%20** oranında (tohum `vat_rate=money(20)` yazıyor, `seed_demo_data.py:161,225,300`;
`order_items` 140 satırın 140'ı %20). İade, servis faturası ve müstahsil kollarının üçü de tohumda **0 satır**
— sorgu onlar için yalnız SÖZDİZİMİ olarak sınandı. `cid=2` → boş (kiracı yalıtımı ölçüldü).

**Ölçülen boşluklar:**

* **G1 — tevkifat yok** (§2.1). KDV1'in tevkifat bölümü doldurulamaz.
* **G2 — oran kapalı küme değil** (§2.1).
* **G3 — başlık/satır ayrışması:** 2026-07 başlıktan 38.587,91, satırdan 38.587,94 (−0,03). Özet SATIRDAN
  alınmalı, ayrışan belgeler listelenmeli.
* **G4 — döviz:** servis faturası kolu `exchange_rate` ile ÇARPMIYOR; dövizli faturada matrah yanlış birimde
  toplanır.
* **G5 — tarih tipi:** `invoices.created_at`/`producer_receipts.issued_at` `timestamptz`dir; `substr`
  SQLite'ta çalıştı (metin saklıyor) ama **PG'de ÇALIŞMAZ** ve UTC ay sınırı İstanbul iş gününden 3 saat kayar
  (`app/business_time.py` zaten var). Tarih aralığı parametresi (`>= :bas AND < :son`) + İstanbul dönüşümü
  gerekir.
* **G6 — servis faturası iki kusur** (§1.3): işçilik %0, iskonto KDV'yi düşürmüyor.
* **G7 — vade farkı KDV'siz** (§1.7).
* **G8 — müstahsil bağlı alış** çift sayım riski (§1.4).
* **G9 — durum semantiği:** `pending` belge özete GİRMİYOR; bir firmanın `pending`i "kesilmiş fatura" olarak
  kullanıp kullanmadığı DOĞRULANMADI.

---

## 3. HEDEF BİÇİMLER — yalnız halka açık belgeler

### 3.1 Luca — ÖLÇÜLDÜ (kısmen)

Kaynak: [Luca Destek — 01.02.09 Excel Veri
Aktarımı](https://lucayazilim.freshdesk.com/support/solutions/articles/67000267580-01-02-09-excel-veri-aktar-m-)

* Zorunlu alanlar: **Fiş No, Fiş Tarihi, Hesap Kodu, Borç, Alacak**.
* **Tek yüklemede EN FAZLA 50 fiş.**
* **Fiş ayrıştırma kuralı:** Fiş No + Fiş Tarihi AYNI olan satırlar TEK fişe birleşir.
* Şablon uygulamanın içinden indirilir ("Şablon indir"); tam sütun başlıkları ve sırası halka açık değil →
  **DOĞRULANMADI**. Üçüncü taraf bir CSV kılavuzu (`destek.yesilmaviyazilim.com.tr/…/LucaExcelFis(CSV).pdf`)
  **403** döndü.

**Tasarım sonucu:** (1) 50 fiş sınırı → dışa aktarım dosyası 50 fişlik PARÇALARA bölünmeli (zip içinde
`luca-2026-07-001.xlsx`, `-002`…). (2) Fiş No belirlenimci olmalı; iki farklı belge aynı (no, tarih) çiftini
ALAMAZ, yoksa Luca onları SESSİZCE birleştirir.

### 3.2 Mikro — büyük ölçüde DOĞRULANMADI

Kaynaklar: [Mikro Buluo — masraf fişlerinin Excel'den
aktarımı](https://buluo.mikro.com.tr/s/article/Masraf-Fi%C5%9Flerinin-Muhasebeye-veya-Ticari-Tarafa-Excel-den-Toplu-Aktar%C4%B1m%C4%B1n%C4%B1-Nas%C4%B1l-Ger%C3%A7ekle%C5%9Ftirebiliriz-New)
(sayfa istemci tarafında yükleniyor; WebFetch "CSS Error" gördü), eski forum adresi
`forum.mikro.com.tr/…topic13145` Buluo köküne 301.

Arama özetinden (sayfa gövdesi okunamadı): Mikro'nun Excel/CSV aktarımı **kullanıcı tanımlı bir KOLON EŞLEME
ekranıdır** — açıklama kolonu, tutar kolonu, isteğe bağlı borç/alacak tipi kolonu (`B/A`, `+/-`) VEYA ayrı
"borç tutar"/"alacak tutar" kolonları; CSV için ayraç seçilebilir. Yani Mikro sabit bir şema DAYATMIYOR,
eşlemeyi kullanıcıya bırakıyor. XML/API biçimi **DOĞRULANMADI**.

**Tasarım sonucu:** Mikro serileştiricisi "sabit başlıklı, ayrı Borç/Alacak kolonlu, `;` ayraçlı UTF-8 CSV"
üretir ve aynı başlık listesi Mikro eşleme ekranında bir kez tanımlanır. Bu varsayım pilot bir müşavirle
SINANMALIDIR.

### 3.3 Logo (Tiger/GO) — büyük ölçüde DOĞRULANMADI

Kaynaklar: [furkanpezek — Logo XML veri
aktarımı](https://www.furkanpezek.com.tr/2018/05/logo-xml-veri-aktarimi/), [logohizmetmerkezi — Logoya
Excel'den veri
aktarımı](https://www.logohizmetmerkezi.com/kullanim-dokumanlari/logoya-excel-den-veri-aktarimi.html).

* Menü yolu ölçüldü: **Araçlar → Veri Aktarımı (içeri/dışarı) → XML** ve **Veri Aktarımı (Excel'den)**; "Genel
  Muhasebe" modülü XML ile aktarılabilir.
* Muhasebe fişi XML etiket adları (`GL_VOUCHER`/`TRANSACTIONS` vb.) halka açık bir sayfada **BULUNAMADI** →
  DOĞRULANMADI. Doğru yöntem Logo'nun KENDİ "Veri Aktarımı (dışarı)" çıktısını bir örnek fişte almak ve
  şablonu ONDAN türetmektir (K2).
* LOGO Connect / Objects API — lisanslı, halka açık şema yok → DOĞRULANMADI.

### 3.4 e-Defter — fiş standardının ÖLÇÜLEN çekirdeği

Kaynaklar: [Turkcell e-Şirket — e-Defter format
kılavuzu](https://docs.turkcellesirket.com/integration/edefter/format.html), [GİB e-Defter Uygulama Kılavuzu
V1.11](https://www.edefter.gov.tr/dosyalar/kilavuzlar/e-Defter_Uygulama_Kilavuzu_V.1.11.pdf) (arama sonucu;
PDF gövdesi okunmadı).

Yevmiye `entryDetail` başına zorunlu alanlar (Turkcell kılavuzundan, adıyla): `accountMainID`, `accountSubID`,
`amount`, `debitCreditCode`, `postingDate`, `documentType`, `documentNumber`, `documentDate`, `paymentMethod`.
Ayrıca arama özetinden: `entrynumber` (muhasebe fiş no) ile `documentreference` aynı olmalı; bir `entryHeader`
en az iki `entryDetail` taşır ve toplam borç = toplam alacak. `documentType` izinli değer listesi
**DOĞRULANMADI** (kılavuz §3.10.7 ADIYLA işaret ediliyor, okunmadı).

**Tasarım sonucu:** kanonik fiş satırı `belge_tipi`, `belge_no`, `belge_tarihi`, `odeme_yontemi` alanlarını
ŞİMDİDEN taşımalı — e-Defter'i müşavirin programı üretir, ama o program bu dört alanı BİZİM dosyamızdan
alamazsa elle doldurulur.

### 3.5 Ortak asgari kolon kümesi ve farklar

| Alan | Luca | Mikro | Logo | e-Defter | Kanonik |
|---|---|---|---|---|---|
| Fiş no | **zorunlu** | eşlenebilir | DOĞRULANMADI | `entrynumber` | `fis_no` |
| Fiş tarihi | **zorunlu** | eşlenebilir | DOĞRULANMADI | `postingDate` | `fis_tarihi` |
| Hesap kodu | **zorunlu** | eşlenebilir | `GL_CODE` adı arama özetinde | `accountMainID`+`accountSubID` | `hesap_kodu` |
| Borç | **zorunlu** | ayrı kolon ya da tip+tutar | DOĞRULANMADI | `amount`+`debitCreditCode` | `borc` |
| Alacak | **zorunlu** | ayrı kolon ya da tip+tutar | DOĞRULANMADI | `amount`+`debitCreditCode` | `alacak` |
| Açıklama | isteğe bağlı (DOĞRULANMADI) | eşlenebilir | — | `detailComment` (DOĞRULANMADI) | `aciklama` |
| Belge no | DOĞRULANMADI | — | — | `documentNumber` **zorunlu** | `belge_no` |
| Belge tarihi | — | — | — | `documentDate` **zorunlu** | `belge_tarihi` |
| Belge türü | toplu düzenleme ekranı var ([Luca 01.02.013](https://lucayazilim.freshdesk.com/support/solutions/articles/67000267597-01-02-013-fi%C5%9F-i%CC%87%C5%9Flemleri-toplu-belge-t%C3%BCr%C3%BC-ve-%C3%B6deme-y%C3%B6ntemi-d%C3%BCzenleme)) | — | — | `documentType` **zorunlu** | `belge_tipi` |
| Ödeme yöntemi | aynı ekran | — | — | `paymentMethod` | `odeme_yontemi` |
| KDV oranı | — | — | — | — | `kdv_orani` (bilgi) |
| Cari VKN | — | — | — | — | `cari_vkn` (alt hesap üretimi için) |

**Asgari ortak küme = {fiş no, fiş tarihi, hesap kodu, borç, alacak, açıklama}.** Belge no/tarih/tür/ödeme
yöntemi e-Defter'in şartıdır ve kanonik modelde ZORUNLU tutulur; hangi hedefin onları yazdığı serileştiricinin
işidir.

### 3.6 Öneri — tek kanonik "muhasebe fişi" + hedef başına serileştirici

`app/einvoice/provider.py`nin biçimi AYNEN tekrar kullanılır: soyut taban (`EInvoiceProvider(ABC)`, `:176`),
kayıtlı somut sınıflar (`IzibizEInvoiceProvider`, `:1316`), yapılandırılmamış hâl için `NoOpEInvoiceProvider`
(`:294`), sınırlar sabit (`_ZIP_MAX_BAYT = 25 MiB`, `:128`).

Modüller (`app/muhasebe/`): `fis.py` (SAF: `FisSatiri`/`Fis` dataclass'ları + dengeli-fiş kapısı), `kaynak.py`
(SQL: dönem → `Iterator[Fis]`, TEK okuma yeri), `hesap_plani.py` (§4), `kdv_ozeti.py` (§2.4),
`serilestirici.py` (ABC `yaz(fisler) -> Iterator[bytes]`), `hedef_{luca,mikro,logo,kanonik}.py`.
`FisSatiri(frozen)`: `hesap_kodu, borc, alacak, aciklama, kdv_orani?, cari_tipi?, cari_id?`. `Fis(frozen)`:
`fis_no` (belirlenimci: `SAT-<order_id>`, `ALS-<purchase_id>`, `SVF-<invoice_no>`, `MM-<receipt_no>`),
`fis_tarihi, belge_tipi, belge_no, belge_tarihi, odeme_yontemi?, kaynak, satirlar` — K11 gereği `belge_*` ve
`odeme_yontemi` ZORUNLU doldurulur.

Kapılar (saf, SQL'siz — `app/mustahsil.py`nin "aritmetik saf modülde" duruşu): Σ borç = Σ alacak **kuruşu
kuruşuna**; her satırda tam olarak biri > 0; en az iki satır (e-Defter şartı). Dengesiz fiş YAZILMAZ, raporda
listelenir.

---

## 4. HESAP PLANI EŞLEMESİ

### 4.1 Bugün

`grep -rniE "hesap_kodu|account_code|chart_of|hesap_plan|tek_duzen|tekduzen"` → `backend/app`,
`backend/alembic`, `frontend/src`: **0 isabet**. `finance_accounts.account_type ∈ {cash, bank, pos}`
(`finance_engine.py:18,63`) — hesap KODU değil hesap TÜRÜ; vergi borcu türü yok (`routers/mustahsil.py:19-38`
bunu zaten ölçmüş).

### 4.2 Önerilen asgari tablo

`muhasebe_hesap_eslemeleri`: `id` PK · `company_id` NOT NULL FK `companies(id)` · `olay` String(40) NOT NULL
(kapalı küme; `CHECK olay IN (...)`, `schema.py`de birebir kopya — 0079 geleneği) · `kdv_orani` Numeric(9,4)
NULL (NULL = oran bağımsız; `CHECK NULL OR 0..100`) · `taraf_tipi` String(20) NULL (`CUSTOMER|SUPPLIER|NULL`)
· `hesap_kodu` String(40) NOT NULL (`'120'`, `'391.20'`…) · `updated_at` DateTime(tz) NOT NULL · `UNIQUE
(company_id, olay, kdv_orani, taraf_tipi)` — PG16'da `NULLS NOT DISTINCT` yok → iki kısmi indeks (`kdv_orani
IS NULL` / `IS NOT NULL`).

`olay` kapalı kümesi ve **Tek Düzen varsayılanları** (firma başına düzenlenir; satır yoksa varsayılan KODDAN
gelir, tabloya tohum YAZILMAZ — boş tablo "hep varsayılan" demektir):

`SATIS_CARI` 120 · `SATIS_GELIR` 600 · `SATIS_KDV` (oran başına) 391 · `SATIS_IADE` 610 · `ALIS_CARI` 320 ·
`ALIS_STOK` 153 · `ALIS_KDV` (oran başına) 191 · `ALIS_IADE` 153 (alacak) · `SERVIS_GELIR` 600 (ya da 602, K3)
· `MUSTAHSIL_STOPAJ` 360 · `MUSTAHSIL_BAGKUR` 361 · `KASA` 100 · `BANKA` 102 · `POS` 108 (DOĞRULANMADI:
firmalar 102 alt hesabı da kullanır) · `ALINAN_CEK` 101 · `VADE_FARKI_GELIR` 642.

Alt hesap (`120.01.<cari>`) üretimi v1'de YOK: fiş ana hesapla yazılır ve `cari_vkn`/`cari_ad` açıklamada
taşınır. Luca/Mikro alt hesap açma kuralları müşaviri bağlar → **K3**.

### 4.3 Kiracı ve geri yükleme etkisi — ölçüldü

* `TENANT_TABLES` 127 → **128**: tanım `tests/test_tenant_scoping_guard.py:41` + **beş dosyada yedi çivi**
  (aynı liste F10-1 §6.0'da sayılmıştı, bugün 127 ile yeniden ölçüldü): `tests/test_sec6_ip_limitleri.py:419`,
  `tests/test_wa1_ingress.py:290`, `tests/test_wa2_eslestirme.py:320`, `tests/test_wa4_bekleyen.py:252`,
  `tests/test_kiraci_disa_aktarim.py:498, 517, 749`. Toplam ALTI dosya.
* **Dışa aktarım** (`routers/kiraci_disa_aktarim.py`) tabloyu KENDİLİĞİNDEN alır: sıra yansıtılan FK
  grafiğinden türer (`:11-17`); elle liste yok.
* **Geri yükleme** (`app/kiraci_geri_yukleme.py`): tabloda `company_id` dışında `*_id` sütunu YOKTUR →
  `siniflandirilmamis_sutunlar` (`:696`) boş kalır, yumuşak referans sözlüğüne (`:136-431`) satır EKLENMEZ.
  Bu, tabloyu bilerek `created_by` taşımadan tasarlamanın bedelsiz tarafıdır; denetim izi `activity_logs`ta
  durur.

---

## 5. MALİ MÜŞAVİR SALT-OKUNUR ROLÜ

### 5.1 Rol modeli — ölçüm

* Altı rol: `admin`, `yonetici`, `muhasebe`, `satis`, `depo`, `rapor` (`app/auth.py:195-241`); sıralama
  `ROLE_RANK` (`:246-253`: 100/80/60/40/40/20).
* **`muhasebe` ZATEN VAR ve yazar:** `{read, sales, purchases, payments, finance, reports, notifications*,
  supplier_prices.*, farm.view, herd.view}` (`:230-235`). Çek/senette `MUHASEBE_ROLLERI = {admin, yonetici,
  muhasebe}` (`cek_senet_engine.py:74`, ön yüz kopyası `pages/cek-senet/cekSenet.ts:89`). Brifingin (a)
  seçeneğindeki ad KULLANILAMAZ.
* **Rol kullanıcıya bağlı, üyeliğe DEĞİL:** `app_users.role` (`app/auth.py:61`); `user_company_memberships`
  rol sütunu TAŞIMAZ (`app/tenancy.py:15-16`). Otuz müşteri firmaya üye bir müşavir HER firmada aynı roldedir
  — müşavir için doğru, ama kendi firmasında `admin` olan bir müşavir aynı hesapla müşteri firmada müşavir
  OLAMAZ.
* **İzin modeli alan başına okuma/yazma AYIRMAZ:** ara katman `required_permission(method, path)`
  (`app/auth.py:896`) iki katmanlıdır: `:1346-1347`ten ÖNCEKİ kurallar metoddan BAĞIMSIZ tek izin döndürür —
  `/api/payments` → `payments` (`:1075`), `/api/finance` → `finance` (`:1092`), `/api/producer-receipts` →
  `purchases` (`:1168`); GET'i hassas sayılan önekler GET için ayrı kural taşır ve yazma da AYNI izne düşer —
  `/api/purchases` (`:1285` GET, `:1379` yazma), `/api/invoices` (`:1301` GET, `:1377` yazma). `:1346-1347`
  kalan her GET'i `read`e çevirir; sonrası (`/api/orders` → `sales`, `:1354`) yalnız yazmayı bağlar. Kapı
  `main.py:478-481`: `has_permission(user["role"], permission)`. Okumayı yazmadan AYIRAN tek örnek
  `payment-allocations`tır (GET `payments`, yazma `finance`, `:1124`).
* `rapor` rolü `{read, reports, farm.view, herd.view}` (`:240`) — satış, alış, ödeme belgelerini GÖREMEZ.

### 5.2 Seçenekler

**(a′) Yeni rol `musavir` + rol düzeyinde SALT-OKUNUR kapısı — ÖNERİLEN**

* `ROLE_PERMISSIONS["musavir"] = {read, reports, farm.view, herd.view, sales, purchases, payments, finance}`;
  `ROLE_RANK["musavir"] = 10`.
* `READ_ONLY_ROLES = frozenset({"musavir"})` ve `main.py:478`in HEMEN ÖNÜNDE: rol bu kümedeyse, metot
  `SAFE_METHODS` dışındaysa ve yol `SELF_SERVICE_API`de (`app/auth.py:878`) değilse → 403 `ROLE_READ_ONLY`.
  Kapı YOL listesine değil METODA bakar → yarın eklenen yazan uç da KENDİLİĞİNDEN kapalı (deny-by-default).
* **`farm.view` + `herd.view` ŞART ve sebebi pin:** `UNIVERSAL_PERMISSIONS = {read, farm.view, herd.view}`
  (`tests/test_undeniable_endpoint_ population.py:102`) rol tablosundan TÜRETİLİR (`:299-303`). Müşavir bu
  ikisini taşımazsa küme `{read}`e düşer ve 41 `farm/herd` okuma ucunun
  (`test_authorization_population_reconciliation.py:796`) "hiçbir rolle reddedilemez" sınıflaması YALAN olur.
* `+` Müşavir faturayı, alışı, tahsilatı, bankayı KENDİ gözüyle mutabakat yapabilir — dışa aktarım dosyası tek
  başına denetlenemez.
* `-` Ön yüzde yazma düğmeleri görünür (§5.4) — sunucu reddeder ama UX kötü.
* `-` GET ile yan etki yapan bir uç varsa açılır. `GET /api/company/export` `__admin_only__` (`:904`) →
  güvenli; kalanın taraması **DOĞRULANMADI**.

**(a″) Yeni rol `musavir` = `rapor` + yeni `accounting.view` izni**

* Yalnız yeni `/api/accounting/*` okuma yüzeyi + raporlar. Ara katmana dokunulmaz.
* `+` Sıfır yazma riski; ön yüzde düğme sorunu yok.
* `-` Müşavir kaynak belgeyi GÖREMEZ (satış/alış/ödeme sayfaları `sales`/ `purchases`/`payments` ister). "Bu
  fiş hangi faturadan?" sorusu cevapsız.
* `-` `accounting.view` yeni bir izin → `yonetici` ve `muhasebe` satırlarına da girer (`admin` `{"*"}` olduğu
  için KIMILDAMAZ); `backend/test_v3_payments_permission.py:28` TAM sözlük eşitliği çivisi İKİ rol satırında
  kıpırdar (+ yeni `musavir` anahtarı).

**(b) Dış müşavir ayrı kullanıcı TÜRÜ**

* Üyelik başına rol ya da `app_users.user_type` gerektirir; `app/tenancy.py` `memberships`, `resolve_company`
  ve 5.1c geri yükleme e-posta eşlemesi (`kiraci_geri_yukleme.py:1037-1151`) yeniden düşünülür.
* `+` Kendi firmasında admin olan müşavir sorununu çözer.
* `-` Göç + kimlik katmanı + geri yükleme — bu fazın 3–4 katı iş. Ayrı faz.

### 5.3 Pin deltaları — ÖLÇÜLDÜ (ad grep'i + TAM EŞİTLİK aramasıyla)

(a′) için:

| Pin | Yer | Delta |
|---|---|---|
| Rol kümesi TAM eşitliği | `tests/test_role_fallback_failclosed.py:141-145` | +`musavir` (döngü demeti + küme) |
| `ROLES` demeti | `tests/test_undeniable_endpoint_population.py:230` (+ `:307` türetilmiş eşitlik) | +`musavir` |
| `ROLE_PERMISSIONS` TAM sözlük | `backend/test_v3_payments_permission.py:28` | +1 anahtar |
| `payments` taşıyanlar | `tests/test_cs1_cek_senet.py:469-470` `["admin","muhasebe","satis","yonetici"]` | +`musavir` (sıralı) |
| `UNIVERSAL_PERMISSIONS` | `tests/test_undeniable_endpoint_population.py:102` | **SABİT** (farm/herd verildiği için) |
| `EXPECTED_AUTHENTICATED/READ/UNDENIABLE` | `test_authorization_population_reconciliation.py` | **SABİT** — rolden bağımsız türer (`:714-745`), yeni rota yok |
| `GUARDED_READ_OPERATIONS` | aynı dosya | SABİT |
| `herd.view` okuyanlar TAM eşitliği | `tests/test_herd_management_foundation.py:76-77` `{"yonetici","muhasebe","satis","depo","rapor"}` | +`musavir` (küme eşitliği KIRILIR — `herd.view` verildiği için) |
| Rol başına üst düzey menü sayımı | `frontend/src/navigation.consistency.test.ts:451` (`EXPECTED` tablosu) | +`musavir` anahtarı (yoksa `musavir` hiç sınanmaz) |
| E4b-1/E4b-2 `sales` taşıyanlar | `test_e4b1_kismi_sevk.py:1045`, `test_e4b2_irsaliye_yaniti.py:1158` | SABİT — `ROLLER` demeti (`:52`, `:59`) elle yazılı ve `musavir`i İÇERMEZ; eklenirse ikisi de +1 |
| Rol matrisi belgesi | `docs/rbac-permissions.md:11-18` | +1 satır |
| Yeni kapı testi | `tests/test_f9_5c_musavir_salt_okunur.py` | yeni |

(a″) için: ilk üç satır aynı; `test_cs1`:470 SABİT; `test_v3_payments_ permission.py:28` İKİ rol satırı daha
kıpırdar (`yonetici`, `muhasebe`; `admin` `*`); `UNIVERSAL_PERMISSIONS` yine SABİT (farm/herd verilirse);
`test_herd_management_foundation.py:76-77` herd.view verilirse kıpırdar.

### 5.4 Ön yüz — rol kapıları

* İzinler sunucudan gelir: `/api/auth/me` → `"permissions": sorted(permissions_for(role))`
  (`routers/auth.py:538`); `can()` `AuthContext.tsx:56`.
* H48/H50 deseni: sunucu TEK doğru kaynak, ön yüz yalnız GİZLER (`pages/cek-senet/cekSenet.ts:80-92`).
* (a′)'da `can('sales')` müşavir için `true` döner. Dört izinden birine doğrudan `can('…')` ile bakan **16
  çağrı, 13 dosyada** (test dışı; ölçüldü): `EntityQuickActions.tsx:25`, `SupplierPricesPanel.tsx:43`,
  `CekSenetPortfoyu.tsx:42`, `CostRates.tsx:80`, `Dashboard.tsx:112`, `DespatchNotePanel.tsx:511`,
  `EntityDetail.tsx:69` (**İKİ çağrı**: `payments` + `purchases`), `HarvestSeasonAdmin.tsx:254`,
  `PaymentAllocations.tsx:234`, `Payments.tsx:29,33`, `PurchaseComparison.tsx:63`,
  `WorkOrderDetail.tsx:228,232`, `WorkOrders.tsx:15`.
* **Bu çağrıların hepsi yazma düğmesi DEĞİL; en az DÖRDÜ OKUMA gizler:** `Dashboard.tsx:112`
  (`canViewFinance`), `WorkOrderDetail.tsx:232` (`canSeeInvoice`), `CekSenetPortfoyu.tsx:42`
  (`tedarikciGorunur`), `EntityDetail.tsx:69` (`canViewSupplierPrices`). Öneri: `/me` bir `read_only: true`
  alanı döner, `AuthContext` bir `canWrite(p) = can(p) && !readOnly` sunar; **`canWrite` YALNIZ yazma
  düğmelerine** uygulanır, okuma gizleyen dört yer `can()`de KALIR ve müşavire GÖRÜNÜR (K13).
* **Alan maskeleme (SEC-3b):** `MASKESIZ_ROLLER = {admin, yonetici, muhasebe, satis}`
  (`app/alan_maskeleme.py:53`) deny-by-default'tur — `musavir` eklenmezse müşavir VKN, adres, IBAN ve
  `hesap_no`yu MASKELİ görür; oysa fişin `cari_vkn` alanı ve mutabakat tam bu değerler için vardır. **K13 (şef
  kararı): `musavir` `MASKESIZ_ROLLER`e EKLENİR.** Maskeleme testleri rolü ELLE yazılı demetlerden alır ve
  `musavir`i İÇERMEZ: `tests/test_sec3b_cari_alan_envanteri.py:100` (`ROLLER`),
  `test_sec3b_cari_maskeleme_postgresql.py:58-59` (`MASKELI_ROLLER`/`MASKESIZ_ROLLER`). Kendiliğinden
  KIRILMAZLAR ama `musavir`i SINAMAZLAR → 9-5c ikisine de `musavir` ekler; `ROLLER`a eklemek
  `cari_alan_envanteri.txt` veri satırlarını da oynatabilir (dilimde ölçülür). Sayfa içinde `can()`SIZ çizilen
  "Yeni …" düğmelerinin sayısı **DOĞRULANMADI** (literal olmayan kalıplar grep'e görünmez).
* Rol listesi elle kopya: `pages/Users.tsx:9-10` (`roleRank`, `allRoles`) ve
  `navigation.consistency.test.ts:146-160` (`ROLE_PERMISSIONS` kopyası).
* Yeni menü maddesi "Muhasebe Aktarımı" DÖRT sayım çivisini oynatır (depo hafızasındaki ölçüm deseni, bugün
  yeniden okundu): `rota-kapsam-sozlesmesi.test.ts:341` (80 → 81) + `e2e/rota-envanteri.ts` girdisi,
  `navigation.consistency.test.ts:212` (`ALL_NAV_ITEMS` 59 → 60), aynı dosya `:219-220`
  (`BOOKMARKED_MENU_URLS` 59 → 60, iki çivi), `components/AppShell.test.tsx:365` (`hrefs` 59 → 60). Madde
  herkesin gördüğü `Raporlar` grubuna girerse rol başına üst düzey sayımlar
  (`AppShell.test.tsx:79,262,277,302`) KIMILDAMAZ; yeni grup açılırsa kıpırdar.

### 5.5 TAVSİYE

**(a′).** Müşavirin işi "dosyayı indirmek" değil "dosyayı kaynakla mutabakat yapmaktır"; (a″) bunu yapamaz.
Salt-okunur kapı METODA baktığı için kapsamı büyüdükçe gevşemez. (b) ayrı fazdır; K7'de not edilir.

---

## 6. DIŞA AKTARIM MEKANİĞİ

### 6.1 `kiraci_disa_aktarim.py`den ne alınır

* **Akış:** `StreamingResponse` + unseekable `zipfile` + `stream_results`
  (`routers/kiraci_disa_aktarim.py:33-40`). ALINIR.
* **Tutarlı kesit:** PG'de `REPEATABLE READ, READ ONLY` (`:303-308`). ALINIR — fiş ve KDV özeti aynı kesitten
  okunmalı, yoksa özet ile fiş toplamı ayrışabilir.
* **Durum 200'de kilitlenir** (`:42-52`): akış başladıktan sonraki hata yarım zip üretir. Bu yüzden **denge
  kapısı (§3.6) akıştan ÖNCE**, tüm dönem bellekte değil ama ilk geçişte sayılarak koşmalı; dengesiz fiş varsa
  409 gövde ile, akış BAŞLAMADAN reddedilir. Aylık bir KOBİ dönemi binlerle ölçülür (tohum: ay başına ≤13
  satış) — iki geçiş kabul edilebilir.
* **Günlük** (`_gunlukle`, `:311-332`): kesitten SONRA, ayrı işlemde, TEK satır. ALINIR.
* **Kapı** `__admin_only__` (`app/auth.py:904`) — ALINMAZ; muhasebe aktarımı `reports` (okuma) izniyle açılır
  (K6).
* Sınır: e-belge `_ZIP_MAX_BAYT = 25 MiB` (`einvoice/provider.py:128`) gelen zip içindir; giden için sınır
  yok. Dönem en fazla 1 ay (sorgu parametresi `YYYY-AA`; **K12 KABUL**), satır tavanı yok, Luca parçası 50
  fiş.

### 6.2 Denetim kataloğu

* `ACTION_TYPES` **82** girdi (`app/activity_log.py:61`, AST ile sayıldı), `RESOURCE_TYPES` **24** (`:250`);
  ikisi de KAPALI — katalog dışı değer `ValueError` (`:393-396`).
* Eklenecek: `accounting.exported` (9-5b), `accounting.account_map_updated` (9-5a); kaynak tipi `accounting`
  (+1).
* **Gizli pin:** `activity_log.py` dinamik-`text()` parmak izi `tests/test_tenant_scoping_guard.py:378`te
  çivili ve sözlüğe eklenen HER girdi onu kıpırdatır (girdinin kendi gerekçe metni bunu ON BİR kez kaydetmiş —
  `:378`deki "Parmak izi 20…" cümleleri; on tanesi `eski->yeni` hash oku taşır). 9-5a ve 9-5b bu satırı İKİ
  AYRI kez yeniden türetir.

### 6.3 İdempotency — damga mı, durumsuz mu?

| | Belge başına damga (`exported_at`, `batch_id`) | Durumsuz, belirlenimci yeniden aktarım |
|---|---|---|
| `orders/purchases/returns/payments`e yazma | GEREKİR — dört tabloya sütun, dört göç yolu, sayısal manifesto riski (`core_schema.py:93-99`) | GEREKMEZ |
| Damgadan sonra `PUT` | damga YALAN söyler (§1.8: `updated_at` YOK) | yeni dosya YENİ gerçeği yazar |
| Sert `DELETE` | damgalı belge satırı gider (alış/iade silmesi iz de bırakmaz, §0.1) | yeni dosyada fiş YOKTUR |
| Müşavir tarafında çift kayıt | "yalnız yeniler" kolay | fiş no belirlenimci → Luca aynı (no, tarih)ı birleştirir; müşavir ikinci aktarımda önceki fişleri SİLİP yükler |
| Müşavirin rolü | yazma ister (damgalamak bir POST) → (a′) kapısıyla ÇELİŞİR | GET — salt-okunur rolle uyumlu |

**Tavsiye: DURUMSUZ.** Dosya `(company_id, dönem, hedef, kaynak kesit)`in saf fonksiyonudur; aynı veri aynı
baytı üretir (sıralı satırlar, `Decimal` biçimi sabit). Her aktarım bir `accounting.exported` aktivitesi
yazar: `details = {period, target, fis_sayisi, borc_toplami, icerik_sha256}`. İkinci aktarımda özet FARKLIYSA
ekranda "Bu dönem son aktarımdan beri DEĞİŞTİ" uyarısı çıkar — değişimi belge düzeyinde değil DÖNEM düzeyinde
yakalar ve hiçbir belge tablosuna dokunmaz. **Dönem kilidi** (`FAZ-11-adaylar:6`) bu faza değil ayrı dilime
(9-5e) bırakılır: kilit, dört yazma yoluna (`transactions._save`, `delete_transaction`, `workflow` PUT/DELETE,
`finance` PUT/DELETE) bir tarih kapısı demektir ve bu keşfin ölçtüğü en geniş yüzeydir.

### 6.4 Dosya biçimleri

* **XLSX:** `openpyxl>=3.1,<4` ZATEN bağımlılık (`requirements.txt:8`; `pip` 3.1.5 kurdu). `write_only=True`
  kip akışla uyumlu.
* **CSV:** standart kütüphane; UTF-8 BOM + `;` (Türkçe Excel ondalık virgülü için). Mikro için varsayılan.
* **XML:** `xml.etree` yeterli; Logo şablonu doğrulanana kadar (K2) YAZILMAZ.
* **Kanonik JSON/CSV:** her zaman üretilir; hedef serileştiricisi doğrulanmamış bir müşteri için güvenli geri
  dönüş.

---

## 7. PR BÖLÜNMESİ VE PİN DELTALARI

### 7.0 TABAN — `63e57f6` üzerinde YENİDEN ÖLÇÜLDÜ (`8a6e3e1` ile birebir aynı)

| Pin | Değer | Yer |
|---|---:|---|
| Rota işlemi / yolu | **425 / 331** | `tests/test_route_security_contracts.py:744-745` + `EXPECTED_SECURITY_FINGERPRINT` (`:746`) |
| GET envanteri | **202** | `tests/test_route_get_permission_inventory.py:614` + `EXPECTED_GET_PERMISSIONS` (`:57`) + `GET_INVENTORY_FINGERPRINT` (`:615`) |
| Kimlik doğrulamalı / read / undeniable | **412 / 91 / 97** | `tests/test_authorization_population_reconciliation.py` (`EXPECTED_*`, `:421-423`) |
| `GUARDED_READ_OPERATIONS` | **35** | aynı dosya |
| `TENANT_TABLES` | **127** | tanım + beş dosyada yedi çivi (§4.3) |
| Core sorgu envanteri | **250** (select 170 / update 68 / delete 12) | `tests/test_core_query_inventory.py:1325-1326` |
| Core kiracı ifadesi | **208** | `tests/test_core_tenant_scoping_guard.py:2134` |
| `pg_twins.txt` | **140** satır | `tests/pins/pg_twins.txt` |
| `alt_surec_sql.txt` | **137** | `tests/pins/alt_surec_sql.txt` |
| `cari_alan_envanteri.txt` | **65 fiziksel satır / 21 veri satırı** — test YALNIZ 21 veri satırını karşılaştırır (boş ve `#` satırları atlanır, `test_sec3b_cari_alan_envanteri.py:408-409`) | `tests/pins/cari_alan_envanteri.txt` |
| Alembic başı | **`20260918_0090`** | **ON BEŞ dosyada ON BEŞ çivi** (aşağıda) |
| `ACTION_TYPES` / `RESOURCE_TYPES` | **82 / 24** | `app/activity_log.py:61,250` |

**Alembic başı `20260918_0090` — ON BEŞ çivi** (`grep -rn '"20260918_0090"' backend --include=*.py` → **17
isabet / 16 dosya**; ikisi çivi DEĞİL):

* `backend/` kökü PG ikizleri (ON; SQLite hattı ATLAR): `test_1b_a_alis_lot_postgresql.py:401`,
  `test_cs1_cek_senet_postgresql.py:43`, `test_cs2_cek_senet_cari_postgresql.py:45`,
  `test_e1_efatura_sertlestirme_postgresql.py:52`, `test_e4a_despatch_notes_postgresql.py:72`,
  `test_e4b1_kismi_sevk_postgresql.py:47`, `test_e4b2_irsaliye_yaniti_postgresql.py:54`,
  `test_f10_1a_taraf_baglantisi_postgresql.py:60`, `test_h17_auth_rate_limits_index_postgresql.py:36`,
  `test_wa3_worker_postgresql.py:471`.
* `tests/` (DÖRT dosya, BEŞ çivi): `test_e1b_plantback.py:117`, `test_e2_tedavi_arinma.py:152`,
  `test_e3_karantina.py:205`, `test_goc_zinciri.py:424`, `:511`.
* Çivi OLMAYAN iki isabet: göçün KENDİ `revision = "20260918_0090"` satırı
  (`alembic/versions/20260918_0090_whatsapp_taraf_baglantisi.py:86`) ve
  `tests/test_f10_1a_taraf_baglantisi.py:103` (göç DOSYASININ metni).

> ⚠ **#155 SONRASI (ölçüldü, #155 dalı `9787f3b`, `63e57f6`i içerir; #155 hâlâ AÇIK):** Core
> **252** (171/69/12), Core kiracı ifadesi **209**, `pg_twins.txt` **141**, Alembic başı
> **`20260920_0091`** (`"20260920_0091"` 18 isabet → ON ALTI çivi: on bir PG ikizi + beş `tests/`);
> rota/GET/AUTH/`TENANT_TABLES` DEĞİŞMEZ. **9-5a'nın göçü `0092`dir** ve aşağıdaki deltalar #155
> indikten sonraki tabana göre yazıldı. Her dilim dalını kestiği andaki `origin/develop` üzerinde
> YENİDEN ölçer.

### 7.0b F9-5-fix — servis faturası KDV'si (K8; 9-5a'dan ÖNCE, GÖÇ YOK)

* `invoice_service.py`: işçilik satırına firma varsayılan KDV oranı (`:57-58`), genel iskonto payı KDV dahil
  dağıtılır ve `tax_amount` yeniden türetilir (`:47-52` yorumu güncellenir, `:68,72`).
* Yalnız YENİ faturalar; kesilmiş faturalar değişmez (politika `:26-29`), özet onları `uyarilar`da gösterir.
* Pin: rota/GET/AUTH/`TENANT_TABLES`/Alembic SABİT; Core envanteri SABİT (`invoice_service.py` Core sorgu
  yazmıyorsa — dilimde ölçülür); `openapi.json` SABİT. Test: işçilikli ve iskontolu faturada `Σ tax_amount` =
  oran × matrah (kuruş), iskontosuz yolun bayt-aynılığı korunur.

### 7.1 F9-5a — kanonik fiş modeli + KDV özeti + hesap eşlemesi (GÖÇ VAR)

* Göç `…_0092` (#155 sonrası; tarih öneki dilimde): `muhasebe_hesap_eslemeleri` (§4.2).
* `app/muhasebe/{fis,kaynak,hesap_plani,kdv_ozeti}.py`.
* Uçlar (izin: GET → `reports`, PUT → `finance`; `app/auth.py`e METODA BAKAN bir `/api/accounting` önek kuralı
  — `"reports" if method in SAFE_METHODS else "finance"` — `:1346-1347` GET→`read` kuralının ÜSTÜNE,
  `payment-allocations` (`:1124`) deseniyle): `GET /api/accounting/account-map`, `PUT
  /api/accounting/account-map`, `GET /api/accounting/vouchers?period=YYYY-MM` (kanonik JSON önizleme), `GET
  /api/accounting/vat-summary?period=YYYY-MM`.
* §2.4'ün G3/G4/G5/G8 kapıları özet cevabında `uyarilar` listesi olarak.

| Pin | Delta |
|---|---|
| Rota işlemi / yolu | 425 → **429** / 331 → **334** |
| `EXPECTED_SECURITY_FINGERPRINT` | yeniden (`test_route_security_contracts.py:746`) |
| GET envanteri | 202 → **205**; `EXPECTED_GET_PERMISSIONS` (`:57`) +3 girdi (`account-map`, `vouchers`, `vat-summary` → `reports`); `GET_INVENTORY_FINGERPRINT` (`:615`) yeniden |
| Kimlik doğrulamalı | 412 → **416** |
| read / guarded / undeniable | **91 / 35 / 97 SABİT — YALNIZ** yukarıdaki metoda bakan önek kuralı `:1346`nın ÜSTÜNDEYSE. Kural yoksa ya da altına düşerse üç GET `read`e çözülür: read ve undeniable **+3** (9-5b sonrası +4, 9-5d sonrası +5) |
| `TENANT_TABLES` | 127 → **128** (ALTI dosya) |
| Alembic başı | `0091` → `0092`: ON ALTI çivi (#155 sonrası sayım, §7.0) |
| Core envanteri | 252 → ~259 (+~6 select, +~1 update/insert; ölçülecek); parmak izi yeniden |
| Core kiracı ifadesi | 209 → ~215 (+~6) |
| `activity_log.py` parmak izi | yeniden (`test_tenant_scoping_guard.py:378`); `ACTION_TYPES` 82 → 83, `RESOURCE_TYPES` 24 → 25 |
| `pg_twins.txt` | 141 → **142** |
| `cari_alan_envanteri.txt` | ⚠ `vouchers` cevabı cari ADI/VKN taşırsa 21 veri satırı büyür (SEC-3b; K13 müşaviri maskesiz yapar) |
| `openapi.json` / `types.gen.ts` | değişir (dört uç) |

**Testler:** `tests/test_f9_5a_muhasebe_fisi.py` (saf denge kapısı, belirlenimci fiş no, satır-kanonik KDV,
başlık/satır ayrışma uyarısı, komşu firma yalıtımı, `pending`/`draft`/`cancelled` dışlama) +
`test_f9_5a_muhasebe_fisi_postgresql.py`. **PG ikizi ZORUNLU:** G5 (`timestamptz` ay sınırı) ve `UNIQUE …
NULL` davranışı (§4.2 iki kısmi indeks) yalnız PG'de görünür.

### 7.2 F9-5b — Luca / Mikro / Logo serileştiricileri + indirme (GÖÇ YOK)

* `app/muhasebe/serilestirici.py` + `hedef_{luca,mikro,kanonik}.py`; `hedef_logo.py` K2 kapanana kadar YOK (uç
  `target=logo` için 422 + gerekçe).
* `GET /api/accounting/export?period=YYYY-MM&target=luca|mikro|canonical&format=xlsx|csv` — akan zip, Luca 50
  fişlik parçalar, sonunda `manifest.json` (fiş sayısı, borç/alacak toplamı, `icerik_sha256`).

| Pin | Delta |
|---|---|
| Rota işlemi / yolu | 429 → **430** / 334 → **335** |
| `EXPECTED_SECURITY_FINGERPRINT` | yeniden |
| GET envanteri | 205 → **206**; `EXPECTED_GET_PERMISSIONS` +1 (`export` → `reports`); `GET_INVENTORY_FINGERPRINT` yeniden |
| Kimlik doğrulamalı | 416 → **417** |
| read / guarded / undeniable | SABİT (aynı önek kuralı şartıyla; yoksa +4 kümülatif) |
| `TENANT_TABLES`, Alembic | SABİT |
| `activity_log.py` parmak izi | yeniden; `ACTION_TYPES` 83 → 84 |
| Core envanteri | +~0 (okuma 9-5a'nın `kaynak.py`sinden) |
| `pg_twins.txt` | 142 → **143** (REPEATABLE READ kesiti + akış hata sınırı) |

**Testler:** `tests/test_f9_5b_muhasebe_aktarim.py` — aynı veri İKİ koşuda aynı SHA; Luca 51. fiş ikinci
dosyaya; aynı (fiş no, tarih) iki belgeye ASLA verilmez; dengesiz fiş akış BAŞLAMADAN 409; `xlsx` openpyxl ile
geri okunur. PG ikizi: `test_f9_5b_muhasebe_aktarim_postgresql.py`.

### 7.3 F9-5c — `musavir` rolü + salt-okunur kapı + ön yüz (dükkan) (GÖÇ YOK)

* Backend: `ROLE_PERMISSIONS`/`ROLE_RANK` +1, `READ_ONLY_ROLES`, `main.py` kapısı, `/me`'ye `read_only`.
* Backend (K13): `alan_maskeleme.MASKESIZ_ROLLER` += `musavir`.
* Ön yüz: `AuthContext.canWrite` (yalnız yazma düğmeleri; okuma gizleyen dört `can()` KALIR), 16 çağrı yeri
  taranır (§5.4), `Users.tsx` rol listesi, "Muhasebe Aktarımı" sayfası (dönem seçici, KDV özeti tablosu,
  uyarılar, hedef seçimli indirme).

| Pin | Delta |
|---|---|
| Rota (backend) | SABİT (yeni uç yok) |
| Rol çivileri | §5.3 tablosu: BEŞ dosya (`test_role_fallback_failclosed.py`, `test_undeniable_endpoint_population.py`, `test_v3_payments_permission.py`, `test_cs1_cek_senet.py`, `test_herd_management_foundation.py:76-77`) |
| Maskeleme rol demetleri | `test_sec3b_cari_alan_envanteri.py:100`, `test_sec3b_cari_maskeleme_postgresql.py:58-59` — `musavir` eklenir (K13) |
| `UNIVERSAL_PERMISSIONS`, `EXPECTED_*` | SABİT |
| Ön yüz sayımları | `ROTA_ENVANTERI` 80 → 81, `ALL_NAV_ITEMS` 59 → 60, `BOOKMARKED_MENU_URLS` 59 → 60 (×2), `AppShell` `hrefs` 59 → 60 |
| `navigation.consistency.test.ts:146` | `ROLE_PERMISSIONS` kopyasına `musavir` |
| `navigation.consistency.test.ts:451` | rol başına üst düzey sayım tablosuna `musavir: N` (N dilimde ölçülür) |
| `types.gen.ts` | `/me` şeması değişirse fark |

**Testler:** `tests/test_f9_5c_musavir_salt_okunur.py` — rotaları YÜRÜYEN bir kapı: `musavir` ile HER yazan
işlem (`_gated_operations()` deseni, `test_undeniable_endpoint_population.py:252-274`) 403 `ROLE_READ_ONLY`,
`SELF_SERVICE_API` (parola değişimi, çıkış) 2xx; her GET muhasebe uçları için 200. Mutasyon: `READ_ONLY_ROLES`
boşaltılınca kapı ADIYLA kırmızı. PG ikizi GEREKMEZ (SQL yok) — `non_twin_skip_exceptions.json` gerekçesi.
Vitest: `canWrite` + dört sayım dosyası hedefli koşu.

### 7.4 F9-5d — KDV beyan destek raporu (GÖÇ YOK)

* 9-5a özetinin beyanname biçimli görünümü: oran bazında matrah/KDV, iadeler, müstahsil (stopaj/Bağ-Kur,
  `tax_liabilities.due_period`), "beyannamede elle doldurulacak" bölümü (tevkifat, istisna kodu, devreden KDV)
  AÇIKÇA boş ve gerekçeli.
* `GET /api/accounting/vat-summary.xlsx?period=` → rota +1/+1, GET +1 (+1 `EXPECTED_GET_PERMISSIONS` girdisi,
  `EXPECTED_SECURITY_FINGERPRINT` ve `GET_INVENTORY_FINGERPRINT` yeniden), AUTH +1; read/guarded/undeniable
  SABİT (önek kuralı şartıyla; yoksa +5 kümülatif); PDF İSTENMEZ.
* **Tasarım notu — `tax_liabilities` geriye dönük küçülür** (§1.4): iptal kapanmamış satırı SİLER
  (`avans_engine.py:358-364`). Rapor `tax_liabilities`i TEK kaynak saymaz: müstahsil bölümü
  `producer_receipts`ten (`issued` + `cancelled`, `issued_at` ayı) kurulur, `tax_liabilities` ile mutabakat
  yapılır; dönem içinde kesilip SONRA iptal edilen makbuz "iptal edildi (tarih)" satırıyla GÖRÜNÜR ve son
  `accounting.exported` SHA'sı farklıysa §6.3 uyarısı çıkar.

### 7.5 (Önerilen, kapsam DIŞI) F9-5e — dönem kilidi

§6.3. Dört yazma yolu + `companies`e `muhasebe_kilit_tarihi` sütunu (göç). Ayrı keşif ister.

---

## 8. ŞEFE AÇIK KARARLAR (K1–K9) + ŞEF KARARLARI (K10–K13)

**K1 — Fiş hangi belgelerden doğar (v1)?** Ölçüm: satış/alış/iade/servis faturası/müstahsil/tahsilat birer
olay; çek hareketinin geçiş tarihi saklanmıyor (§1.5); irsaliye tutar taşımıyor (§1.6). → **Tavsiye: satış,
alış, iade, servis faturası, müstahsil makbuzu, tahsilat/ödeme.** Çek/senet (`cek_senetler`), eski çek/senet
(`financial_instruments` — `/api/finance/instruments`, bağlı kasa hareketini logsuz SERT siler, §1.1) ve vade
farkı **AÇIKÇA v2**.

**K2 — Logo hedefi nasıl doğrulanır?** Halka açık fiş şeması bulunamadı (§3.3). → **Tavsiye: 9-5b Logo'suz
iner; pilot müşteride Logo'nun "Veri Aktarımı (dışarı)" ile ALINMIŞ örnek fiş XML'i depoya fikstür olarak
girer ve `hedef_logo.py` ONDAN türetilir.** Tahminle yazılmış bir XML, müşavirin programında sessizce yanlış
fiş üretir.

**K3 — Hesap planı derinliği.** → **Tavsiye: v1 ana hesap + oran başına KDV hesabı (`391.20`, `191.10` gibi
firma tanımlı); cari alt hesabı (`120.01.<cari>`) v1'de YOK.** Servis geliri varsayılanı `600`; garanti payı
`company_payable` ayrı satır (`120` aynı hesap, açıklamada garanti veren).

**K4 — Başlık/satır ayrışan eski belgeler.** Ölçüm: tohumda 60'ın 11'i (§1.2). → **Tavsiye: fiş SATIRDAN
kurulur; ayrışan belge dosyaya GİRER ama `manifest.json` + ekranda ADIYLA ve farkıyla listelenir.** Politika
backfill'i YASAKLAMAZ: toplu backfill "kapsam dışı" ve "ayrıca mutabakat raporu ile onay gerektirir"
(`financial-rounding-policy.md:28-29`); kullanıcının belgeyi yeniden kaydetmesi ise kuralı ZATEN uygular
(`:27`). Yani ayrışan belge ya ekrandan yeniden kaydedilerek düzelir ya da ayrı onaylı bir backfill dilimiyle
— 9-5 ikisini de YAPMAZ, yalnız listeler.

**K5 — İdempotency.** → **Tavsiye: DURUMSUZ + dönem başına içerik SHA'lı aktivite satırı** (§6.3). Belge
tablolarına damga YAZILMAZ; değişim DÖNEM düzeyinde uyarılır. Dönem kilidi ayrı dilim (9-5e).

**K6 — Aktarım uçlarının izni.** → **Tavsiye: GET → `reports`, eşleme PUT → `finance`.** `reports` bugün
`admin`, `yonetici`, `muhasebe`, `rapor` ve (K7) `musavir`de var; `satis` ve `depo`da yok. Yeni izin adı
uydurulmaz (pin maliyeti §5.3 (a″)).

**K7 — Müşavir rolü.** → **Tavsiye: (a′) yeni `musavir` rolü = mevcut okuma izinleri + `farm.view`/
`herd.view` + rol düzeyinde METODA bakan salt-okunur kapı.** `muhasebe` adı dolu; (a″) kaynak belgeyi
göstermez; (b) (üyelik başına rol) ayrı faz — kendi firmasında admin olan müşavir v1'de İKİ hesap kullanır.

**K8 — Servis faturasının iki KDV kusuru (§1.3).** İşçilik %0, iskonto KDV'yi düşürmüyor. → **Tavsiye: 9-5'ten
ÖNCE ayrı bir `fix` dilimi** (yeni faturalar için; eskiler politika gereği yeniden hesaplanmaz ve özet onları
`uyarilar`da gösterir). Hukuki oran teyidi müşavirden (DOĞRULANMADI).

**K9 — Tevkifat.** → **Tavsiye: v1'de YOK ve raporda AÇIKÇA "tevkifat desteklenmiyor" yazar.** Tevkifat,
satırda oran + kod + tevkif payı demektir: belge şemasına (`order_items`, `purchase_items`) sütun, `_totals`
aritmetiğine dal ve e-Fatura UBL'sine `WithholdingTaxTotal` — başlı başına bir faz.

**Şef kararları (2026-09-25, doc lens) — önceki taslakta SESSİZ verilmişti, artık kayıtlı:**

**K10 — Deftere giren durum kümesi = `{approved, completed}`** (`document_engine.py:16` `STOCK_STATUSES`);
`draft`, `pending`, `cancelled` girmez (§1.2, G9). → **KABUL EDİLDİ.**

**K11 — e-Defter'in dört alanı kanonik modelde ZORUNLU:** `belge_tipi`, `belge_no`, `belge_tarihi`,
`odeme_yontemi` her `Fis`te dolu olmalı (§3.4-3.5); hedef serileştiricisi yazmasa bile. → **KABUL EDİLDİ.**

**K12 — Dönem ≤ 1 ay** (`period=YYYY-MM`, çok aylık istek 422; §6.1). → **KABUL EDİLDİ.**

**K13 — Müşavir maskelemesi:** `musavir` `MASKESIZ_ROLLER`e (`app/alan_maskeleme.py:53`) EKLENİR — müşavirin
varlık sebebi vergi kimliğini görmektir; okuma gizleyen dört `can()` yeri (`Dashboard:112`,
`WorkOrderDetail:232`, `CekSenetPortfoyu:42`, `EntityDetail:69`) müşavire GÖRÜNÜR kalır; `canWrite` YALNIZ
yazma düğmelerine uygulanır (§5.4). → **YAZILDIĞI GİBİ KABUL EDİLDİ.**

---

## 9. EN BÜYÜK DÖRT RİSK

1. **Belge değişebilirliği dosyayı sessizce eskitir.** `orders/purchases/ returns/payments` `PUT` ile değişir,
   sert silinir ve `updated_at` taşımaz (§1.8). Müşavir Temmuz'u 5 Ağustos'ta aktarır, satışçı 7 Ağustos'ta
   bir Temmuz faturasını düzeltir → müşavirin defteri ile ERP ayrışır ve HİÇBİR sütun bunu söylemez. §6.3'ün
   SHA uyarısı yalnız YENİDEN aktarıldığında yakalar; kalıcı çare dönem kilididir (9-5e).
2. **Tahmine dayalı hedef biçimi yanlış fiş üretir.** Luca'nın yalnız beş zorunlu alanı ve 50 fiş sınırı halka
   açık kaynakla ölçüldü; Mikro ve Logo şemaları DOĞRULANMADI (§3.2-3.3). Luca'nın (no, tarih) birleştirme
   kuralı iki belgeye aynı fiş no verilirse iki faturayı TEK fişte birleştirir — dengeli göründüğü için kimse
   fark etmez.
3. **KDV özeti "doğru toplam, yanlış beyan" üretebilir.** Tevkifat yok, servis işçiliği %0, servis iskontosu
   KDV'yi düşürmüyor, dövizli fatura çevrilmiyor, vade farkı KDV'siz, müstahsile bağlı alışta hayali %20
   indirilecek KDV riski (§2.4 G1–G8). Rapor bunları GİZLERSE müşavir rakama güvenir; bu yüzden her boşluk
   özet cevabında ADIYLA bir uyarıdır, dipnot değil.
4. **Muhtasar kaynağı geriye dönük küçülür.** Makbuz iptali kapanmamış `tax_liabilities` satırlarını SİLER
   (`avans_engine.py:358-364`), 9-5d ise `due_period`u o tablodan okur. Raporlanmış bir ayın stopajı, sonradan
   yapılan iptalle yeniden üretilen raporda izsiz azalır. Çare §7.4 notu: kaynak `producer_receipts` (iptaller
   dahil), `tax_liabilities` yalnız mutabakat.

---

## EK — ÖLÇÜLEMEYENLER (DOĞRULANMADI)

* **Luca şablonunun tam sütun başlıkları ve sırası** — şablon uygulama içinden indiriliyor; üçüncü taraf CSV
  kılavuzu 403.
* **Mikro muhasebe fişi aktarım ekranının tam alan adları ve XML/API biçimi** — Buluo sayfası istemci
  tarafında yükleniyor, gövde okunamadı; yalnız arama özeti kullanıldı.
* **Logo Tiger/GO muhasebe fişi XML etiketleri ve LOGO Connect Excel şablonu** — halka açık şema bulunamadı.
* **e-Defter `documentType` izinli değerleri** — GİB kılavuzu §3.10.7 ADIYLA işaret ediliyor, PDF okunmadı.
* **KDV1 beyanname satır kodları**, servis işçiliğinin ve vade farkının KDV oranı (hukuki) — GİB/mevzuat
  kaynağına bu oturumda gidilmedi.
* **Üretimde başlık/satır KDV'si ayrışan belge sayısı** — yalnız tohum ölçüldü (60'ın 11'i); canlı veriye
  erişilmedi.
* **`pending` durumunun firmalarca "kesilmiş belge" olarak kullanılıp kullanılmadığı** (G9).
* **GET ile yan etki yapan uçların tam taraması** (§5.2 (a′)) ve **ön yüzde `can()`SIZ çizilen yazma
  düğmelerinin sayısı** (§5.4).
* **Core envanteri / kiracı ifadesi artışları** (`+~6`) — sorgular henüz yazılmadı; aritmetik TAHMİNDİR, her
  dilim tarayıcıyla yeniden ölçer.
* **İade, servis faturası ve müstahsil kollarının SAYISAL sonucu** — tohum bu üç kaynağa satır yazmıyor
  (`seed_demo_data.py` yalnız satış/alış/ödeme/ gelir-gider); sorgu yalnız sözdizimi olarak koştu.
* **İrsaliyenin e-Defter yevmiyesine girmediği** (§1.6) — mevzuat iddiası; GİB kılavuzu okunmadı.
* **9-5c'nin açık ölçümleri:** `navigation.consistency.test.ts:451` tablosunda `musavir`in üst düzey
  madde sayısı; `ROLLER`a `musavir` eklenince `cari_alan_envanteri.txt` veri satırı deltası (§5.4).
