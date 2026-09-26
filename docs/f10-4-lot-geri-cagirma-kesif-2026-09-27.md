# Faz 10-4 — Lot geri çağırma: "bu partiyi kim aldı" + bildirim — KEŞİF

**Ölçüm tarihi:** 2026-09-27 · **Taban:** `origin/develop` = `7f45b1e` (#173 birleşmesi) · **Worktree:**
`C:\Users\HHH\nazgul-f10-4` · **Dal:** `docs/f10-4-lot-geri-cagirma-kesif` · **Yorumlayıcı:** CPython 3.12.10
(pin parmak izleri 3.12'de üretilir) · **Kapsam:** SALT OKUMA keşif. Uygulama kodu, göç, test ve şema
DEĞİŞMEDİ.

Her sayı bu taban üzerinde ölçüldü ve `dosya:satır` ile yazıldı. Ölçülemeyen her iddia **DOĞRULANMADI**
etiketi taşır (Ek). Bütün `app/`, `tests/`, `alembic/` ve `scripts/` yolları `backend/` altındadır. Sonda
betikleri oturum karalama alanındadır ve depoya GİRMEDİ.

> **Faz tanımı (ölçüldü):** `docs/yol-haritasi.html:96` — *"Lot geri çağırma: 'bu partiyi kim aldı' +
> bildirim"*, dayandığı altyapı *"1B lot izi"*. `docs/FAZ-11-adaylar-2026-09-09.md:24` kabul ölçütü ekler:
> *"lot geri çağırmaya harmanlanmış lotlara yayılım"*. `docs/ROADMAP.md` Faz 10-4'ü ANMIYOR.

---

## 0. YÖNETİCİ ÖZETİ — dört cümlelik sonuç

1. **"L partisini kim aldı, ne kadar, ne zaman, elinde ne kaldı" sorusu bugün GÖÇSÜZ cevaplanıyor —
   ÖLÇÜLDÜ.** Zincir `product_lots` → `stock_movements.lot_id` → (`reference_type`, `reference_id`) → belge
   başlığı → `customers`. Gerçek uçlardan tohumlanmış karalama SQLite'ında (§2) hop başına TEK Core
   sorgusu 100 birimlik partinin 60'ını dört alıcıya (satış, irsaliye, depo transferi sonrası satış, POS)
   ve satış iadesini düşerek kalan miktara kadar buldu; defter kalanı (37) + alıcılar (60) + alış iadesi
   (3) = 100. Tarayıcıyla ölçülen bedel: Core envanteri **252 → 258** (+6 `select`), Core kiracı ifadesi
   **209 → 215**, ihlal 0.
2. **Parti kimliği DÖRT yolda KAYBOLUYOR ve biri SESSİZ:** tarla faaliyeti girdisi (`lot_id` NULL,
   `field_stok_tuketici.py:1360-1373`), iş emri parçası (`work_order_stock.py:108`, sütun listesinde
   `lot_id` YOK), kaynaksız satış iadesi (`routers/workflow.py:554`) ve **partili ürünün partisiz depo
   transferi** — sonda bunu **201 ile KABUL ETTİ** ve mutabakat iki depoda da `SAPMA` gösterdi
   (`routers/warehouses.py:280`: yalnız `lot_code` VARSA parti dalı; `_lotsuz_yazmayi_reddet` ÇAĞRILMIYOR).
   Hayvan tedavisi (E2) stoğa HİÇ dokunmuyor ve kalemi parti taşımıyor. POS parti tutar ama perakende
   satış tek bir "Perakende Satış" carisine düşer — alıcı BİLİNEMEZ.
3. **Bildirim altyapısı TEK TEK mesaj için hazır, KAMPANYA için değil.** Rıza (şirket, taraf, kanal)
   başına VAR ve kuyruğa girişte + gönderimden hemen önce İKİ KEZ denetleniyor; şablon mesaj sınıfı
   zorunlu; tekilleştirme anahtarı var. EKSİK olanlar: (a) **WhatsApp'ta Meta şablon mesajı YOK** — yalnız
   `type: "text"` (`whatsapp/saglayici.py:174-180`) ve 24 saat penceresi dışındaki bildirim Meta'dan geri
   döner (`docs/whatsapp/WA4_KANALLAR.md:83-95,173`); (b) onay SATIR başına dört-göz (`service.py:463-531`)
   — N müşteri = N onay; (c) şablon değişken kataloğunda ürün/parti/SKT YOK (`render.py:50-59`); (d)
   rızası/telefonu olmayan alıcı için "elle aranacak" kaydı YOK.
4. **Önerilen bölünme: 10-4a GÖÇSÜZ salt-okur rapor (`GET /api/lots/{id}/recall-preview`), 10-4b GÖÇLÜ
   eylem (`POST /api/lots/{id}/recall`) + kayıt tablosu.** Tablonun gerekçesi ölçülmüş bir değişebilirliktir:
   satış düzenleme/silme hareketleri SERT siler (`routers/transactions.py:866-875,1615-1620`), yani iz
   CANLI bir görünümdür ve geri çağırma anındaki alıcı listesi DONDURULMAZSA sonradan yeniden
   üretilemez. `TENANT_TABLES` 127 → 129, Alembic `0092` → `0093`.

---

## 1. PARTİ İZİ — bugünkü gerçek

### 1.1 Tablolar ve bağlar

| Tablo | Rol | Anahtar sütunlar | Kaynak |
|---|---|---|---|
| `product_lots` | Parti defteri: (firma, ürün, **depo**, kod) başına TEK satır | `id`, `company_id`, `product_id`, `warehouse_id` NOT NULL, `lot_code` TEXT, `expiry_date` DATE NULL, `quantity` (`>= 0`), `created_at` | göç `20260903_0067:248-283`, `20260908_0073:187-208` |
| `stock_movements.lot_id` | Hareketin partisi; bileşik FK `(company_id, lot_id) → product_lots(company_id, id)` | `reference_type` (tablo adı), `reference_id`, `movement_type`, `warehouse_id`, `quantity` işaretli | `core_schema.py:276-313`; FK `0067:307-326` |
| `purchase_items.lot_code`, `.expiry_date` | Partiyi AÇAN belge satırı | — | `core_schema.py:228-246`, göç `0073:226-242` |
| `orders` / `delivery_notes` / `returns` | Çıkış ve iade belgeleri; cari `customer_id` / `customer_id` / `entity_id` | `returns.source_type='order'` + `source_id` | `core_schema.py:115-148,373-395`, `app/workflow.py:84-100` |
| `stock_transfer_items` | Transfer satırı | **`lot_code` TAŞIMAZ** — parti yalnız HAREKETTE | `app/inventory.py:86-97` |

Ölçülen üç yapısal olgu:

* **Transfer, hedef depoda AYRI bir `product_lots` satırı açar** (tekillik depoyu içerir, `0073:195-198`;
  `routers/warehouses.py:339` `_parti_ac`). Sonda: `L-RECALL` A deposunda `id=1`, B deposunda `id=3` (§2.2
  HOP1). Yani "parti" = `(company_id, product_id, lot_code)` ve geri çağırma **kardeş satırları** toplamak
  zorundadır; tek `product_lots.id` üzerinden yürüyen bir sorgu transferden sonraki satışı KAÇIRIR.
* **Hareket belge SATIRINA değil BAŞLIĞA bağlıdır** (`reference_type`, `reference_id`;
  `transactions.py:1098-1117`). Satırda (`order_items`) `lot_id` YOK. Bir belge aynı ürünü tek satırda
  taşımak zorunda olduğu için (`schemas.py:210` — sondada ÖLÇÜLDÜ: iki partili alış 422 aldı) (belge,
  ürün, parti) toplamı satırı birebir verir.
* **Tükenen parti silinmez** (`quantity=0`; `0067:264-266` "o satır geri çağırmanın kanıtıdır").

### 1.2 Partiye DOKUNAN yollar — yazıcı haritası

`product_lots`a yazan TEK modül `app/parti_defteri.py`dir (başlık `:1-31`; kapı
`tests/test_1b_a_alis_lot.py`). `stock_movements`a yazan ON BİR `INSERT` ölçüldü (`grep -rn "INSERT INTO
stock_movements\|insert(stock_movements)" app`):

| # | Yol | Yer | `lot_id` | Parti defteri |
|---:|---|---|---|---|
| 1 | Alış / satış (`/api/purchases`, `/api/orders`, POS) | `routers/transactions.py:1098` | ✅ alış `_parti_ac` (`:1050`), satış FEFO `_parti_tuket` (`:1076`) | ✅ |
| 2 | İrsaliye, satış iadesi, alış iadesi (`/api/workflow/{kind}`) | `routers/workflow.py:584` | ✅ çıkış FEFO (`:540`), iade ters FEFO `_parti_iade` (`:560`) | ✅ |
| 3 | Depo transferi | `routers/warehouses.py:384` | ✅ **yalnız `lot_code` verilirse** (`:280`) | ⚠ bkz. §3 G4 |
| 4 | Sayım | `routers/warehouse_counts.py:313` | ✅ (`_parti_dus`/`_parti_ac`, `:273,291`) | ✅ |
| 5 | Elle stok ayarı | `routers/products.py:885` | ✅ | ✅ |
| 6 | Açılış stoku | `routers/products.py:530` | ❌ (partili açılış ayrı dal `:552`) | kapı `:701` |
| 7 | Ürün düzenleme farkı | `routers/products.py:732` | ❌ | kapı `_lotsuz_yazmayi_reddet` `:701` |
| 8 | Toplu stok | `routers/products.py:1138` | ❌ | kapı `:1107` |
| 9 | Excel içe aktarım | `routers/imports.py:433,448` | ❌ | kapı `:356` |
| 10 | Tarla olayı (hasat / faaliyet girdisi / kantar farkı) | `field_stok_tuketici.py:665` | hasat ✅ (`:1360-1370`), diğerleri ❌ | **kapı YOK** |
| 11 | İş emri parçası | `work_order_stock.py:108` | ❌ (sütun listesinde yok) | **kapı YOK** |

`_lotsuz_yazmayi_reddet` (`parti_defteri.py:997-1030`) defter o (ürün, depo) için AÇIKSA partisiz yazmayı
409 ile reddeder. Çağıranları yalnız 6–9'dur (`grep` — `products.py:35,701,1107`, `imports.py:32,356`);
3, 10 ve 11 onu ÇAĞIRMAZ.

### 1.3 Mevcut okuma yüzeyi

* `GET /api/products/{product_id}/lots` (`routers/products.py:378`) — depoda ne kaldı; ALICI GÖSTERMEZ.
* `GET /api/products/lots/mutabakat` (`:328`, `app/parti_mutabakat.py`) — `warehouse_stocks` ↔ `product_lots`
  farkı, kovalar `ESIT`/`LOTSUZ_TASARIM`/`SAPMA`.
* İkisi de `read` (`tests/test_route_get_permission_inventory.py:386-387`). **"Bu partiyi kim aldı" ucu
  YOK**; `grep -rni "recall\|geri_cagir" app` yalnız yorum satırı döndürür (`parti.py:15,60,173`,
  `parti_defteri.py:16,405,514,729,835,1054`, `field_stok_tuketici.py:1331`).

---

## 2. SONDA — gerçek şema, gerçek uçlar, hop başına tek Core sorgusu

### 2.1 Tohum (karalama `geri_cagirma_sondasi.py`, taze SQLite, `app.main` açılışta alembic'i sürer)

Kalıp `tests/test_1b_e_iade_lot.py:135-250`in aynısı (TestClient, `admin` girişi, negatif stok serbest).
Bir ürün (`NPK 15-15-15`), iki depo (A, B), üç müşteri (Ali ve Berk telefonlu, Cem telefonsuz):

1. Alış `L-RECALL` 100 (SKT 2098-01-31) + ayrı alış `L-OTEKI` 50 (SKT 2099) → A.
2. Satış Ali 30 (A) · 3. İrsaliye Berk 20 (A) · 4. POS perakende 5 (A)
5. Partili transfer A→B 10 `L-RECALL`, ardından B'den Cem'e satış 10
6. Ali'nin satışına bağlı (`source_type='order'`) satış iadesi 5 · 7. Tedarikçiye alış iadesi 3
8. **Partisiz transfer A→B 4** (boşluk ölçümü)

### 2.2 Sorgular ve SONUÇ

**HOP 1 — kök parti → kardeş satırlar** (`product_lots`, `(company_id, product_id, lot_code)` eşitliği):

```
id=1  depo=1  L-RECALL  SKT 2098-01-31  kalan 37.0000
id=3  depo=2  L-RECALL  SKT 2098-01-31  kalan  0.0000
```

**HOP 2 — parti → hareket, belge başına net** (`stock_movements WHERE company_id=:cid AND lot_id IN (1,3)
GROUP BY reference_type, reference_id, movement_type, warehouse_id, lot_id`):

| reference_type | ref | movement_type | depo | lot | miktar | tarih |
|---|---:|---|---:|---:|---:|---|
| purchases | 1 | purchase | 1 | 1 | +100 | 2026-09-01 |
| orders | 1 | sale | 1 | 1 | −30 | 2026-09-02 |
| delivery_notes | 1 | delivery | 1 | 1 | −20 | 2026-09-03 |
| orders | 2 | sale (POS) | 1 | 1 | −5 | 2026-09-27 |
| transfer | 1 | transfer_out | 1 | 1 | −10 | 2026-09-04 |
| transfer | 1 | transfer_in | 2 | 3 | +10 | 2026-09-04 |
| orders | 3 | sale | 2 | 3 | −10 | 2026-09-05 |
| returns | 1 | sale_return | 1 | 1 | +5 | 2026-09-06 |
| returns | 2 | purchase_return | 1 | 1 | −3 | 2026-09-07 |

(POS tarihi `business_today()`dir, `pos.py:323` — gövdeden tarih ALMAZ.)

**HOP 3 — çıkış belgesi → cari, iade düşülmüş** (satış ve irsaliye kolları + `returns.source_id` ile
satışa bağlanan iade):

| kaynak | belge | tarih | cari | telefon | çıkan | iade | **kalan** |
|---|---|---|---|---|---:|---:|---:|
| orders | SAT-000001 | 2026-09-02 | Çiftçi Ali | 05321112233 | 30 | 5 | **25** |
| delivery_notes | IRS-000001 | 2026-09-03 | Çiftçi Berk | 05324445566 | 20 | 0 | **20** |
| orders | SAT-000003 | 2026-09-05 | Çiftçi Cem | — | 10 | 0 | **10** |
| orders | SAT-000002 | 2026-09-27 | **Perakende Satış** | — | 5 | 0 | **5** |

Denge: alıcılarda 60 + defterde 37 + tedarikçiye iade 3 = 100 ✔. Transfer zinciri (A→B→Cem) HOP 1'in
kardeş satırı sayesinde yakalandı; kök `id=1` ile sınırlı bir sorgu Cem'i KAÇIRIRDI.

### 2.3 Sorgu biçimi — tarayıcı ile ÖLÇÜLDÜ (bellekte, depoya dosya yazılmadan)

İki biçim denendi; varsayımsal `app/lot_geri_cagirma.py` kaynağı `test_core_query_inventory.scan_sources`e
ve `test_core_tenant_scoping_guard._core_kapsami`ye (karalama kopyası üzerinde) verildi:

| Biçim | Core envanteri | Desteksiz ifade | Kiracı ifadesi | İhlal |
|---|---|---|---|---|
| (1) takma ad `kok`, yerel `sm = stock_movements`, `union_all` + alt sorgu `outerjoin` | 252 → 258 | 6 → **9** (`variable-arg` ×2, `unresolved-target` ×1) | — | — |
| (2) takma adsız, yerel yeniden bağlamasız, altı ayrı düz `select` | **252 → 258** (select 171 → 177) | **6 → 6** | **209 → 215** | **0** |

Biçim (1) `test_no_unsupported_expressions`/`test_no_unresolved_targets`i KIRAR; **10-4a biçim (2) ile
yazılmalıdır.** Biçim (2) aynı tohumda aynı sonucu verdi (`varyant2_kosu.py`: KÖK `(1,'L-RECALL')`, kardeş
{1,3}, satış 30/10/5, irsaliye 20, iade 5). Dokunulan YENİ kiracı tabloları (`BEKLENEN_KIRACI_TABLOLARI`):
`delivery_notes`, `orders`, `product_lots`, `returns` (+4; `customers` ve `stock_movements` zaten
kümede).

> **`product_lots` Core'da BİLDİRİLMEMİŞ** — `grep -rn '"product_lots"' app` → 0; bütün erişim ham
> `text()`. 10-4a Core yazacaksa tabloyu bildirmelidir. `core_schema.py`ye eklemek `stock_movements.lot_id`
> için 1B-C'de ölçülen tuzağı DOĞURUR (`core_schema.py:291-312`: `create_all` sütunu/tabloyu taze
> veritabanında açar ve 0067'nin koşullu DDL'i atlanabilir) → **tavsiye: modül-yerel `MetaData` ile
> bildirim** (sonda biçimi); `metadata.create_all` onu GÖRMEZ.

PG derlemesi (`postgresql.dialect()`) ölçüldü: bağlı parametreler `%(…)s`, `IN` genişleyen parametre,
metinde dinamik tablo/sütun YOK. PG'de KOŞULMADI (Ek).

---

## 3. BOŞLUKLAR — parti kimliğinin kaybolduğu hoplar

| # | Hop | Ölçüm | Sonuç |
|---|---|---|---|
| G1 | **POS perakende** | `pos.py:189-199`: `customer_id` yoksa satış `POS_CUSTOMER_NAME = "Perakende Satış"` (`:33`) carisine; parti FEFO ile TUTULUR (`_save`, `:349`) | Miktar bilinir, **alıcı bilinemez**. Rapor ayrı satırda "kimliksiz perakende" göstermeli, bildirim adayı SAYILMAMALI |
| G2 | **Kaynaksız satış iadesi** | `workflow.py:554-560`: `source_id` yoksa tek satır, `lot_id` NULL; `validate_return_reference` kaynağı zorunlu KILMIYOR (`movement_references.py:42-51`) | İade partiye dönmez; alıcının "kalanı" OLDUĞUNDAN büyük görünür (güvenli yöne hata) |
| G3 | **İrsaliyeye bağlı iade** | `iade_kaynagi=("order","orders")` yalnız satış (`workflow.py:102`); `validate_return_reference` yalnız `order`/`purchase` (`movement_references.py:51-54`) | İrsaliye alıcısının iadesi partiye DÜŞMEZ; kalan büyük görünür |
| G4 | **Partisiz depo transferi (partili üründe)** | `warehouses.py:280` yalnız `lot_code is not None` ise parti dalı; `_lotsuz_yazmayi_reddet` çağrılmıyor. **Sonda: 201 KABUL**, hareketler `lot_id` NULL (`transfer_out −4`, `transfer_in +4`); mutabakat: A deposu stok 83 / parti 87, B deposu stok 4 / parti 0 → iki `SAPMA` | Kaynak depoda defter 4 FAZLA, hedefte 4 partisiz mal: o 4 birim B'den satılırsa `defter_bosaldi` damgasıyla partisiz çıkar ve **geri çağırmadan kaçar**. Tek satırlık düzeltme: kapı çağrısı (§6.1, K6) |
| G5 | **Tarla faaliyeti girdisi (E1a/E1b bağlamı)** | `field_stok_tuketici.py:1360-1373`: parti YALNIZ hasatta; `_faaliyet_kalemleri` (`:917-927`) `field_activity_inputs`tan `product_id, quantity` okur, parti yok; başlık `:30-36` sınırı ADIYLA koyar | İlaç/gübre partisinin HANGİ parsele gittiği sorulamaz; stok düşer, defter DÜŞMEZ (`SAPMA`) |
| G6 | **Kantar fişi farkı** | aynı dosya `:30-36` — eksi fark partiye uygulanmaz | Hasat partisinin fiş düzeltmesi defterde görünmez |
| G7 | **İş emri parçası** | `work_order_stock.py:108` INSERT'te `lot_id` sütunu YOK; kapı yok | Parça partisi kaybolur (makine parçası için düşük olasılık — DOĞRULANMADI) |
| G8 | **Hayvan tedavisi (E2)** | `routers/herd.py:2016-2063` yalnız `animal_treatments` + `animal_treatment_items` yazar, stok HAREKETİ YOK; kalem sütunları `product_id, drug_name, dose, dose_unit` (göç `0074:346-354`) | "Bu ilaç partisi hangi hayvana verildi" SORULAMAZ; arınma kilidi ürüne bağlı, partiye değil |
| G9 | **Belge düzenleme / silme** | satış/alış `PUT` ve `DELETE` hareketleri SERT siler ve yeniden yazar (`transactions.py:866-875`, `:1615-1620`); workflow aynı (`workflow.py:351-359,780-788`) | İz CANLI görünümdür; geri çağırmadan SONRA düzeltilen satış listeden düşer ya da değişir → §5 kayıt tablosu gerekçesi |
| G10 | **Harmanlanmış lot** (FAZ-11 ölçütü) | parti kodlu bir karıştırma/üretim adımı YOK (`grep -i "harman\|blend" app/parti*.py app/field_stok_tuketici.py` → 0; `harman` başka dosyalarda vade/ödeme anlamında) | Bugün yayılacak bir harman ilişkisi yok; ölçüt, bir karıştırma yolu doğduğunda o yolun `lot_id` taşımasıyla karşılanır (K9) |

---

## 4. BİLDİRİM — ne var, ne eksik

### 4.1 Var olan

* **Outbox** `notifications` (`notifications/schema.py:118-176`): kanal, alıcı, şablon kimliği+sürümü,
  `content_hash`, rıza kararı kanıtı (`consent_id/version/checked_at/decision/reason`), `UNIQUE(company_id,
  dedupe_key)` (`:176-180`). `enqueue_notification` (`service.py:253-378`) `ON CONFLICT DO NOTHING` ile
  aynı anahtarda MEVCUT satırı döndürür → **idempotency bedava**.
* **Durum makinesi:** onaysız satır `AWAITING_APPROVAL` doğar (`service.py:295`); `approve_notification`
  (`:463-531`) bayat önizleme CAS'ı + **dört-göz** (oluşturan onaylayamaz, veritabanı yükleminde de).
* **Rıza:** `notification_consents` `UNIQUE(company_id, party_type, party_id, channel)` (`schema.py:276-282`),
  olay defteri `notification_consent_events`; `evaluate_consent` (`consents.py:176-222`) kayıt yok →
  `NO_RECORD`, iptal → `REVOKED`, numara değişti → `RECIPIENT_CHANGED`. İki noktada çağrılır (kuyruk + gönderim
  öncesi, `:187-189`). `CONSENT_REQUIRED_CHANNELS = {SMS, WHATSAPP, EMAIL}` (`schema.py:72`).
* **Şablon:** `message_class ∈ {SERVICE_TRANSACTIONAL, COMMERCIAL}` zorunlu (`templates.py:1-13`,
  `schema.py:71`); gövde değişince `is_active=FALSE` → yeniden onay; içerik kapısı (`content_gate.py:263`).
* **Kanıtlanmış üretici deseni:** `POST /api/notifications/work-orders/{id}/reminder`
  (`routers/notifications.py:539-664`) — rıza → şablon → `build_content` → `enqueue_notification` →
  `notification.queued` aktivitesi. 10-4b bu deseni N cari için döngüye alır.
* **Boşaltma:** `scripts/dispatch_notifications.py` cron ile silahlı satırları gönderir (`DEFAULT_LIMIT =
  50`); elle `POST /{id}/dispatch` (`notifications_dispatch`, `routers/notifications.py:772-780`).
  Kural motorunda `max_per_run` 1..50 ve aşımda HİÇ satır üretmez (`rules.py:200-201`).

### 4.2 Eksik (her biri ölçüldü)

| # | Eksik | Ölçüm | Etki |
|---|---|---|---|
| B1 | **WhatsApp şablon (HSM) gönderimi** | `saglayici.py:174-180` yalnız `"type": "text"`; `WA4_KANALLAR.md:83-95,173` "BUGÜN ŞABLON DESTEĞİ YOKTUR … 24 saatlik pencere dışına çıkan bildirim Meta tarafından reddedilir" | Geri çağırma iş başlatan mesajdır → WHATSAPP kanalı pratikte ÇALIŞMAZ. v1 kanalı SMS (sağlayıcı `TwilioNotificationProvider`, `provider.py:110`) ya da WhatsApp şablon işi AYRI dilim (K4) |
| B2 | **Toplu onay** | onay satır başına (`service.py:463`); kampanya kavramı yok | 40 alıcı = 40 onay tıklaması. 10-4b tek onayla N satırı silahlandıran bir "kampanya onayı" ister ya da satırlar AYNI `content_hash` ile gruplanır (K5) |
| B3 | **Şablon değişkenleri** | `VARIABLE_SPECS` (`render.py:50-59`) = müşteri/firma/makine adı, randevu/vade tarihi, tutar, iş emri/belge no | `urun_adi`, `parti_kodu`, `skt` YOK → eklenmeli (+3 girdi; test pini YOK — `grep VARIABLE_SPECS tests` → 0) |
| B4 | **Rızasız / telefonsuz alıcı** | `evaluate_consent` `NO_RECORD`da kuyruğa HİÇ sokmaz (üretici deseni 400 döner, `routers/notifications.py:589-598`) | Sondada Cem telefonsuz, perakende kimliksiz: mesaj ATILAMAYAN alıcı SESSİZCE düşmemeli → "elle aranacak" listesi (kayıt tablosu, §5) |
| B5 | **Teslim sonucu** | `WA4_KANALLAR.md:176-178` delivered/read geri okuması YOK; `SENT` yalnız "Meta 2xx" (`provider.py:161-163`) | "Bilgilendirildi" kanıtı zayıf; FAZ-11 ölçütü "teslim sonucu" ayrı iş |
| B6 | **Hız sınırı** | kanal başına hız sınırı YOK; yalnız cron `--limit 50` ve WA işçisi `whatsapp_worker_batch = 10` (`config.py:194`, asistan için) | Yüzlerce alıcılı geri çağırma dakikada 50'şer boşalır — DOĞRULANMADI: sağlayıcı kotası |

### 4.3 KVKK / mesaj sınıfı

Rıza kaydı sınıftan BAĞIMSIZ zorunludur (`evaluate_consent` sınıfa bakmaz, `consents.py:191-222`).
Geri çağırma bir ürün güvenliği bildirimidir — ticari ileti DEĞİL (`SERVICE_TRANSACTIONAL`). Yasal
yükümlülük/meşru menfaat istisnasıyla rızasız gönderilebilir mi sorusunun hukuki cevabı
**DOĞRULANMADI**; bu keşif rıza kapısını DELMEYİ önermez: rızası olmayan alıcı elle aranacak listesine
düşer (K3).

---

## 5. ÖNERİLEN API

### 5.1 `GET /api/lots/{lot_id}/recall-preview` (10-4a)

* `lot_id` = herhangi bir `product_lots.id`; kapsam kardeş satırlar `(company_id, product_id, lot_code)`
  (§1.1). Başka firmanın kimliği → 404 (kiracı yüklemi `kok_parti`de).
* Cevap: `parti {urun, kod, skt, depolar[{depo, kalan}]}`, `alicilar[{kaynak, belge_id, belge_no, tarih,
  cari_id, cari_adi, telefon, cikan, iade, kalan, bildirilebilir, engel}]`, `kimliksiz_perakende {miktar,
  belge_sayisi}`, `diger_hareketler[]` (transfer/sayım/alış iadesi), `uyarilar[]` — mutabakat kovası
  `SAPMA` ise ADIYLA (G4), kaynaksız iade varsa, `defter_bosaldi` damgalı hareket varsa.
  `bildirilebilir/engel` `evaluate_consent`in kuru çalıştırmasıdır (yazmaz).
* **İzin:** yeni yol `/api/lots` bugün GET'te `read`e (`auth.py:1346-1347`), yazmada `__admin_only__`e
  (`:1422`) düşer. Cevap cari adı + telefon taşır → `read` DEĞİL. Öneri: `:1346`nın ÜSTÜNE `if method in
  SAFE_METHODS and path.startswith("/api/lots/"): return "sales"` (SEC-3 deseni, `:1285-1288`) — kaybeden
  roller `depo`, `rapor`. Telefon/adres alanları ayrıca `alan_maskeleme.maskele_cari` ile
  (`MASKESIZ_ROLLER = {admin, yonetici, muhasebe, satis}`, `alan_maskeleme.py:53`) (K2).
* Denetim: GET ise aktivite YAZMAZ (diğer GET'lerle tutarlı) → `ACTION_TYPES` SABİT.

### 5.2 `POST /api/lots/{lot_id}/recall` (10-4b)

* Gövde: `{sebep (zorunlu, ≥10 karakter), kanal, sablon_kodu, onizleme_sha}`. `onizleme_sha` = önizleme
  cevabının kanonik SHA'sı; farklıysa 409 (bayat önizleme — `approve_notification`un CAS deseni).
* Tek işlemde: (1) `lot_recalls` satırı + (2) önizleme anındaki alıcıların `lot_recall_lines`e
  DONDURULMASI + (3) bildirilebilen her alıcı için `enqueue_notification(..., armed=False)` →
  `AWAITING_APPROVAL`, `type_="lot.recall"`, `dedupe_key = "lot.recall:{recall_id}:{cari_id}:{kanal}"` +
  (4) `lot.recall_started` aktivitesi (`details`: `recall_id`, `parti_kodu`, `alici_sayisi`,
  `bildirim_sayisi`, `elle_aranacak_sayisi` — telefon GİRMEZ, `activity_log.py:130,146` kuralı).
* **Idempotency:** aynı parti için AÇIK (`status='open'`) bir geri çağırma varsa ikinci POST 409 ve
  mevcut `recall_id`yi döndürür (kısmi tekil indeks `(company_id, product_id, lot_code) WHERE
  status='open'`); bildirim satırları zaten `dedupe_key` ile tekil.
* **İzin:** `POST /api/lots/` → `notifications_approve` (üretici deseni `routers/notifications.py:552`
  aynı izni ister). Onay yine DÖRT-GÖZ: POST'u atan satırları onaylayamaz (`service.py:496`, veritabanı yükleminde de `:510-513`).
* Denetim: `ACTION_TYPES` 84 → **85** (`lot.recall_started`); kapanış ucu v1'de YOK (K7) — eklenirse +1.
  `RESOURCE_TYPES` 24 → **25** (`lot_recall`; kaynak kimliği `lot_recalls.id`).

---

## 6. PR BÖLÜNMESİ VE PİN DELTALARI

### 6.0 TABAN — `7f45b1e` üzerinde ÖLÇÜLDÜ

Altı kapı dosyası (`test_route_security_contracts`, `test_route_get_permission_inventory`,
`test_authorization_population_reconciliation`, `test_core_query_inventory`,
`test_core_tenant_scoping_guard`, `test_tenant_scoping_guard`) + `test_1b_g_mutabakat`: **192 passed**
(CPython 3.12.10, 507 s).

| Pin | Değer | Yer |
|---|---:|---|
| Rota işlemi / yolu | **425 / 331** | `tests/test_route_security_contracts.py:744-745` + `EXPECTED_SECURITY_FINGERPRINT` `:746` |
| GET envanteri | **202** | `tests/test_route_get_permission_inventory.py:614` + `EXPECTED_GET_PERMISSIONS` + `GET_INVENTORY_FINGERPRINT` `:615` |
| Kimlik doğrulamalı / read / undeniable | **412 / 91 / 97** | `tests/test_authorization_population_reconciliation.py:421-423` |
| `GUARDED_READ_OPERATIONS` | **35** | aynı dosya `:428` |
| Core sorgu envanteri | **252** (select 171 / update 69 / delete 12) | `tests/test_core_query_inventory.py:1348-1351` |
| Core kiracı ifadesi | **209** | `tests/test_core_tenant_scoping_guard.py:2149` |
| Dinamik `text()` toplamı | **271** | `tests/test_tenant_scoping_guard.py:1328` |
| `TENANT_TABLES` | **127** | `tests/test_tenant_scoping_guard.py:41` + ALTI çivi: `test_sec6_ip_limitleri.py:419`, `test_wa1_ingress.py:290`, `test_wa2_eslestirme.py:320`, `test_wa4_bekleyen.py:252`, `test_kiraci_disa_aktarim.py:498,517,749` |
| `ACTION_TYPES` / `RESOURCE_TYPES` | **84 / 24** | `app/activity_log.py:61,263` (AST ile sayıldı) |
| Alembic başı | **`20260925_0092`** | `grep -rn '"20260925_0092"' --include=*.py` → 21 isabet / 18 dosya (biri göçün kendisi) |
| `pg_twins.txt` / `alt_surec_sql.txt` | **146 / 137** satır | `tests/pins/` |

### 6.1 F10-4a — salt-okur geri çağırma raporu (GÖÇ YOK)

* `app/lot_geri_cagirma.py`: biçim (2) altı `select` (§2.3), modül-yerel `product_lots` bildirimi, saf
  birleştirme (iade düşme, kimliksiz perakende ayrımı, uyarılar) Python'da.
* Uç `GET /api/lots/{lot_id}/recall-preview` + `auth.py`de `/api/lots/` GET kuralı (§5.1).
* **Aynı dilimde G4 kapatılır** (K6): `warehouses.py:280` `else` dalına `_lotsuz_yazmayi_reddet(...)`.
  Bu `parti_defteri` ÇAĞIRAN kümesini değiştirmez (warehouses zaten çağıran, `warehouses.py:20`).
  `tests/test_1b_d_transfer_lot.py:299-308`in partisiz transfer vakası (201 + iki `lot_id` NULL hareket)
  KIRILMAZ — ÖLÇÜLDÜ: o vakanın ürünü (`'Partisiz Transfer'`) hiç parti açmaz (`add(plain, source, 4)`,
  `lot_code` yok), yani `_parti_takipli_mi` yanlış döner ve kapı geçer. Yeni kırmızı yalnız partili ürünün
  partisiz transferidir — istenen de budur.
* Ön yüz: ürün parti listesinde "Kim aldı?" bağlantısı → yeni sayfa DEĞİL, çekmece/dialog → rota ve
  menü sayımı KIMILDAMAZ (menü maddesi eklenirse dört çivi, depo hafızası).

| Pin | Delta |
|---|---|
| Rota işlemi / yolu | 425 → **426** / 331 → **332** + `EXPECTED_SECURITY_FINGERPRINT` yeniden |
| GET envanteri | 202 → **203**; `EXPECTED_GET_PERMISSIONS` +1 (`("GET", "/api/lots/{lot_id}/recall-preview"): "sales"`); parmak izi yeniden |
| AUTH / read / undeniable / guarded | 412 → **413** / **91 / 97 / 35 SABİT** — yalnız kural `:1346`nın ÜSTÜNDEYSE. Kural yoksa uç `read`e düşer: read 92, undeniable 98 ve telefon listesi herkese açılır |
| Core envanteri | **252 → 258** (select 171 → 177) — ÖLÇÜLDÜ (§2.3) + `EXPECTED_INVENTORY`/parmak izi yeniden |
| Core kiracı ifadesi | **209 → 215** — ÖLÇÜLDÜ; `BEKLENEN_KIRACI_TABLOLARI` +4 (`delivery_notes`, `orders`, `product_lots`, `returns`) |
| `text()` toplamı | 271 SABİT (yeni modül Core); `warehouses.py` kapı çağrısı SQL eklemez → yalnız o dosyanın AST parmak izi kımıldar (dilimde ölçülür) |
| `TENANT_TABLES`, Alembic, `ACTION_TYPES` | SABİT |
| `pg_twins.txt` | 146 → **147** (`test_f10_4a_geri_cagirma_postgresql.py` — `IN` genişlemesi + `expiry_date` tipi PG'de `date`, SQLite'ta `str`: `parti_defteri._skt` dersinin aynısı) |
| `alt_surec_sql.txt` | alt süreç smoke'u SQL taşırsa +1 satır |
| `openapi.json` / `types.gen.ts` | değişir (bir uç) |

**Testler:** `tests/test_f10_4a_geri_cagirma.py` — sondanın senaryosu (transfer zinciri, iade düşme, POS
ayrımı, telefonsuz alıcı `bildirilebilir=false`), komşu firma 404, `depo` rolü 403, `satis` maskesiz,
partisiz transfer 409. Mutasyonlar: kardeş satır dalı kaldırılınca Cem kaybolur; iade düşmesi
kaldırılınca Ali 30 okunur; kiracı yüklemi düşürülünce komşu firma satırı görünür.

### 6.2 F10-4b — geri çağırma eylemi + outbox kampanyası (GÖÇ VAR)

**Göç gerekli mi — gerekçe.** Göçsüz seçenek (kayıt = `lot.recall_started` aktivitesi + `notifications`
satırları) üç ölçülmüş noktada YETMEZ:

1. **İz değişebilir:** satış/irsaliye düzenleme ve silme hareketleri SERT siler (G9). Geri çağırma
   anındaki alıcı listesi DONDURULMAZSA bir ay sonra "kime bildirdik" sorusu yeniden üretilemez.
2. **Mesaj atılamayan alıcı:** rızasız/telefonsuz/perakende alıcı hiç `notifications` satırı DOĞURMAZ
   (B4). Onların "elle arandı mı" durumu bir yerde tutulmalı.
3. **`details` hassas yük taşımaz** (`activity_log.py:130,146`) — alıcı listesi aktiviteye gömülemez.

Karşı argüman (bedel): +2 kiracı tablosu, göç zinciri çivileri, geri yükleme sınıflandırması. Bedel
ölçülebilir ve tek seferlik; eksikliğin bedeli denetimde "bilgilendirdik" iddiasının kanıtsız kalmasıdır.
→ **Tavsiye: göç VAR** (K1).

* Göç `…_0093` (tarih öneki dilimde): 
  * `lot_recalls`: `id`, `company_id` FK, `product_id` (bileşik FK `products`), `lot_code` TEXT, `sebep`
    TEXT NOT NULL, `status` `CHECK IN ('open','closed')`, `onizleme_sha` CHAR(64), `created_by`,
    `created_at`, `closed_at` NULL; `UNIQUE(company_id, id)`; kısmi tekil `(company_id, product_id,
    lot_code) WHERE status='open'`.
  * `lot_recall_lines`: `id`, `company_id`, `recall_id` (bileşik FK), `customer_id` NULL (perakende için
    NULL), `kaynak` `CHECK IN ('orders','delivery_notes','pos_retail')`, `belge_id`, `cikan`, `iade`,
    `kalan` `NUMERIC(18,4)`, `notification_id` NULL, `sonuc` `CHECK IN ('queued','blocked','manual_pending',
    'manual_done')`, `engel` VARCHAR(60) NULL.
* `render.VARIABLE_SPECS` +3 (`urun_adi` TEXT 80, `parti_kodu` TEXT 80, `skt` DATE 10).
* Uç `POST /api/lots/{lot_id}/recall` (§5.2) + `auth.py` `/api/lots/` POST → `notifications_approve`.
* (İsteğe bağlı, K5) toplu onay: `POST /api/lots/recalls/{id}/approve` — dört-göz ve `content_hash` CAS'ı
  her satır için `approve_notification` ÇAĞIRARAK (yeniden yazmadan).

| Pin | Delta (tek POST ucu; K5 toplu onayı eklenirse ikinci satırdaki gibi) |
|---|---|
| Rota işlemi / yolu | 426 → **427** / 332 → **333** (+ toplu onay: 428 / 334) + parmak izi yeniden |
| GET envanteri | SABİT |
| AUTH / read / undeniable / guarded | 413 → **414** (+1 daha) / SABİT |
| `TENANT_TABLES` | 127 → **129**: tanım + ALTI çivi (§6.0) |
| Alembic başı | `0092` → `0093`: 18 dosyadaki 20 çivi (`--include=*.py` isabetleri eksi göçün kendisi) — her dilim kestiği anda YENİDEN sayar |
| `ACTION_TYPES` / `RESOURCE_TYPES` | 84 → **85** / 24 → **25**; `activity_log.py` dinamik `text()` parmak izi yeniden (`test_tenant_scoping_guard.py:383`) |
| Core envanteri / kiracı ifadesi | + (INSERT'ler `text()` ise Core SABİT, `text()` toplamı +N; Core `insert()` ise envanter +N) — dilimde tarayıcıyla ölçülür, TAHMİN YAZILMADI |
| Geri yükleme | `lot_recall_lines.customer_id/belge_id/notification_id` ve `lot_recalls.product_id` → `kiraci_geri_yukleme.py` `DOGRUDAN_HEDEFLER` (`:187`) / `AYIRT_EDICI_HEDEFLER` (`:370`; `belge_id` `kaynak`a göre) — yoksa `siniflandirilmamis_sutunlar` (`:697`) KIRMIZI |
| Dışa aktarım | `routers/kiraci_disa_aktarim.py` tabloyu FK grafiğinden KENDİLİĞİNDEN alır; sayım çivileri `TENANT_TABLES` ile aynı üç satır |
| `pg_twins.txt` | +1 (kısmi tekil indeks + CHECK'ler PG'de) |
| `openapi.json` / `types.gen.ts` | değişir |

**Testler:** `tests/test_f10_4b_geri_cagirma_eylem.py` — tek POST: rızalı Ali `queued`, rızasız Berk
`blocked` + `engel=NO_RECORD`, telefonsuz Cem `manual_pending`, perakende satırı `pos_retail`; ikinci POST
409 + aynı `recall_id`; bayat `onizleme_sha` 409; POST'u atan onaylayamaz; satış SİLİNDİKTEN sonra
`lot_recall_lines` DEĞİŞMEZ (G9 donması). PG ikizi: `test_f10_4b_geri_cagirma_eylem_postgresql.py`.

### 6.3 (Kapsam DIŞI, önerilen) — ayrı dilimler

* **WA-HSM:** Meta şablon mesajı gönderimi (B1) — `saglayici`ya `sablon_gonder`, şablon adı/dil/parametre
  eşlemesi. 10-4b SMS ile iner; WhatsApp kanalı bu dilimden sonra açılır.
* **G5/G6 tarla girdisi FEFO'su** — faaliyet girdisinin partiden düşmesi; `field_activity_inputs`e
  `lot_code` (göç). E1a/E1b'nin REI/PHI kilitleriyle birlikte tasarlanmalı.
* **G2/G3 iade kaynağı zorunluluğu** ve irsaliye kaynaklı iade.
* **G8 tedavi kalemi `lot_code`** (göç `animal_treatment_items`).

---

## 7. ŞEFE AÇIK KARARLAR (K1–K9)

**K1 — 10-4b kayıt tablosu (göç) mu, aktivite + outbox mu?** Ölçüm §6.2 (üç nokta). → **Tavsiye: iki
tablo (`lot_recalls`, `lot_recall_lines`), göç `0093`.**

**K2 — Önizleme ucunun izni.** Cevap cari adı ve telefon taşır. → **Tavsiye: GET `sales` (SEC-3 deseni),
POST `notifications_approve`; telefon `maskele_cari` ile rol maskesine tabi.** Alternatif `stock`: `depo`
görür ama `depo` maskeli roldür → telefonsuz liste; geri çağırmayı depo başlatmaz.

**K3 — Rızasız alıcı.** Hukuki istisna DOĞRULANMADI. → **Tavsiye: rıza kapısı DELİNMEZ; rızasız alıcı
`manual_pending` ile elle aranacak listesine düşer, ekranda ADIYLA.** Mesaj sınıfı `SERVICE_TRANSACTIONAL`.

**K4 — Kanal.** WhatsApp'ta şablon yok (B1). → **Tavsiye: 10-4b yalnız SMS (+ isteğe bağlı EMAIL);
WHATSAPP kanalı WA-HSM dilimine kadar 422 + gerekçe.** Pencere içi (son 24 saatte yazmış) çiftçiye serbest
metin denemesi BİLEREK yapılmaz: hangi alıcının pencerede olduğu outbox'tan bilinmez.

**K5 — Toplu onay.** Satır başına dört-göz korunur mu? → **Tavsiye: 10-4b'de toplu onay ucu VAR ama her
satır için mevcut `approve_notification`ı çağırır** (dört-göz + CAS aynen); yeni bir onay yolu yazılmaz.
Alternatif: satır satır onay (sıfır yeni rota, 40 tık).

**K6 — G4 (partisiz transfer) 10-4a'da mı kapanır?** Sonda: 201 + çift `SAPMA`. → **Tavsiye: EVET, 10-4a
içinde tek kapı çağrısı** — rapor, açığını bildiği bir kaçağı açık bırakarak yayımlanmamalı. Partisi olmayan
ürünlerin transferi DEĞİŞMEZ (`_parti_takipli_mi` yalnız defter açıksa reddeder, `parti_defteri.py:969-994`).

**K7 — Kapanış.** `lot_recalls.status='closed'`a geçiş (tüm satırlar `queued→SENT` ya da `manual_done`).
→ **Tavsiye: v1'de sütun VAR, kapanış ucu YOK** (açık geri çağırma aynı parti için ikincisini engeller;
kapanış 10-4c). Alternatif: 10-4b'de `POST …/close` (+1 rota, +1 ACTION_TYPES).

**K8 — Kimliksiz perakende.** → **Tavsiye: raporda "kimliksiz perakende: 5 birim, 1 belge" satırı;
`lot_recall_lines`e `customer_id NULL, kaynak='pos_retail'`.** POS'ta partili ürün satışında müşteri
seçimini zorunlu kılmak AYRI karar (kasada sürtünme).

**K9 — Harmanlanmış lot ölçütü (FAZ-11).** Bugün karıştırma/üretim yolu yok (G10). → **Tavsiye: ölçüt
"yeni bir karışım yolu `lot_id` taşımadan birleşemez" diye yeniden yazılır**; 10-4 kapsamında iş yok.

---

## 8. EN BÜYÜK DÖRT RİSK

1. **Kaçan alıcı, yanlış güven.** Rapor "partinin tamamını izledim" dediği an, G4/G5/G7 gibi partisiz
   yollardan çıkan mal görünmez olur. Çare: mutabakat `SAPMA`sı ve `defter_bosaldi` damgası rapora
   `uyarilar` olarak girer — rapor "izlenemeyen N birim var" diyebilmeli; denge satırı (§2.2: 60 + 37 + 3 =
   100) her önizlemede hesaplanır ve tutmazsa ADIYLA gösterilir.
2. **Mesaj gitmedi ama `SENT`.** WhatsApp pencere dışı reddi (B1) ve teslim geri okuması yokluğu (B5):
   "bilgilendirdik" iddiası `SENT` üzerine kurulursa yanlış olabilir. K4 SMS'le başlatır; `sonuc` sütunu
   `SENT` ile "teslim" arasında ayrım yapmaz — v1'de bilinen sınır.
3. **Canlı iz, donmuş kayıt ayrışması.** Geri çağırmadan sonra bir satış düzeltilirse önizleme ile
   `lot_recall_lines` ayrışır (G9). Bu bilerek istenen davranıştır (kayıt donar) ama ekran ikisini yan
   yana gösterip farkı söylemezse kullanıcı hangisine güveneceğini bilemez.
4. **Kardeş satır anahtarının zayıflığı.** Parti kimliği `(ürün, lot_code)`; iki farklı tedarikçi aynı
   ürüne aynı kodu basarsa (`_parti_ac` aynı depoda satırı BİRLEŞTİRİR, `parti_defteri.py:122-160`) geri
   çağırma iki gerçek partiyi tek sanır. Tedarikçiye göre ayırmak `product_lots`ta tedarikçi sütunu ister
   — kapsam dışı, raporda "bu kodla N ayrı alış belgesi" bilgisi verilerek görünür kılınır.

---

## EK — ÖLÇÜLEMEYENLER (DOĞRULANMADI)

* **PostgreSQL koşusu** — sorgular yalnız `postgresql.dialect()` ile DERLENDİ; PG :5433 (Docker) bu
  oturumda kullanılmadı.
* **KVKK / 6563 / İYS açısından geri çağırma bildiriminin rızasız gönderilebilirliği** (§4.3, K3) —
  hukuki kaynağa gidilmedi.
* **Meta şablon onay süresi ve "utility" kategorisinin geri çağırmayı kapsaması** (B1) — Meta
  belgesine bu oturumda erişilmedi; yalnız depo içi `WA4_KANALLAR.md` okundu.
* **SMS sağlayıcı kotası / saniye başına gönderim sınırı** (B6).
* **Makine parçalarının partili tutulma sıklığı** (G7) ve **üretimde partisiz transfer sayısı** (G4) —
  canlı veriye erişilmedi.
* **10-4b'nin Core/`text()` deltası** — INSERT'lerin Core mu `text()` mi yazılacağı dilimin kararıdır;
  bu belge 10-4a için ÖLÇÜLMÜŞ (+6/+6) değeri verir, 10-4b için sayı UYDURMAZ.
* **Rota/GET/AUTH deltaları** — sayım kurallarından türetildi (yeni yol başına +1 işlem/+1 yol/+1 AUTH, GET
  için +1 envanter); uç yazılmadığı için kapı dosyalarında KOŞULMADI.
