# Tedarikçi Köprüsü (Otomithat B4B) — Faz-0 Keşif ve Tasarım

Durum: Tasarım adayı — gerçek Otomithat örnek dosyaları ve ürün politikası kararları bekleniyor

Kapsam: Keşif ve tasarım; uygulama kodu yok
Kaynak gerçekliği: `origin/develop` (`4c8f35d`) ve 28 Temmuz 2026 tarihindeki açık PR'lar

## 1. Amaç ve kapsam sınırı

Hedef, Berkay'ın Otomithat B4B portalından indirdiği fiyat/katalog dosyasını güvenli biçimde ERP'ye taşıyan şu akıştır:

`dosyayı yükle → kolonları eşle → zorunlu dry-run → insan onayı → atomik uygula → raporla / gerekirse batch'i geri al`

Bu faz:

- portalda oturum açmaz, scraping veya sipariş otomasyonu yapmaz;
- kullanıcı adı, parola, cookie, OTP veya başka portal kimlik bilgisi istemez;
- gerçek dosya görülmeden Otomithat kolon sözleşmesini kesinleştirmez;
- eşleşmeyen ürünü varsayılan olarak otomatik oluşturmaz;
- satış fiyatını veya stok miktarını varsayılan olarak değiştirmez.

## 2. Repo keşfi — mevcut gerçeklik

### 2.1 Genel tablo/Excel içe alma altyapısı

Ortak okuyucu `.xlsx`, `.xlsm` ve `.csv` kabul ediyor. CSV için UTF-8 BOM ve Windows Türkçe (`cp1254`) desteği, virgül/noktalı virgül/tab delimiter algılama var; başlıklar Türkçe karakterlerden ve noktalama işaretlerinden arındırılarak normalize ediliyor (`backend/app/routers/imports.py:136-139`, `backend/app/routers/imports.py:166-201`). Alias tabanlı `_map` ilk eşleşen kolonu seçiyor; kullanıcıya gösterilen veya kaydedilen bir kolon eşleme şablonu yok (`backend/app/routers/imports.py:204-216`).

Ürün Excel import'u eşleşmeyi sırasıyla tenant içindeki `product_code`, `barcode`, sonra normalize edilmiş `name` ile yapıyor; eşleşirse ürünün hem alış/satış fiyatını hem master alanlarını ve stok farkını güncelliyor, eşleşmezse yeni ürün açıyor (`backend/app/routers/imports.py:309-341`). Bu davranış Otomithat için doğrudan yeniden kullanılmamalı: bir tedarikçi fiyat dosyasının stok, satış fiyatı veya ürün master'ını sessizce değiştirmesi güvenli değil.

BizimHesap satış raporu import'u başlık satırını ilk 15 satırda arıyor, alias tabanlı kolon eşliyor ve ürünü `code → barcode → name` önceliğiyle buluyor (`backend/app/routers/imports.py:449-523`). Eşleşmeyen müşteri/ürünleri hata ve özet listesi olarak döndürüyor; belgeleri tek transaction sonunda commit ediyor (`backend/app/routers/imports.py:523-601`).

Açık Draft PR #148 (önceki dâhilî depodaki kayıt), büyük tek-seferlik BizimHesap göçü için HTTP dışı bir emsal sunuyor:

- varsayılan `--dry-run`, açık `--apply` ayrımı ve tenant kapsamı (`backend/tools/migrate_bizimhesap.py` PR #148: satır 4-25, 535-595);
- idempotency anahtarı ve raporlama (`backend/tools/migrate_bizimhesap.py` PR #148: satır 448-532);
- apply modunda checkpoint commit (`backend/tools/migrate_bizimhesap.py` PR #148: satır 498-511).

Ancak checkpoint yaklaşımı bu görev için uygun değildir: gereksinim “kısmi yazma yok” dediğinden bir Otomithat batch'i tek veritabanı transaction'ında uygulanmalıdır. #148 sadece dry-run, rapor ve tekrar çalıştırma ergonomisi için emsaldir.

### 2.2 Body-limit güvenlik sözleşmesi (#168)

Global request body sınırı 2 MiB, onaylı import sınırı 11 MiB'dir; bunun 10 MiB dosya + multipart payı olduğu açıkça belgelenmiştir (`backend/app/config.py:26-30`). Import okuyucusu ayrıca akış sırasında 10 MiB sınırı uygular (`backend/app/routers/imports.py:195-201` ve aynı dosyadaki `_read_upload_limited`).

Middleware en uzun path-prefix eşleşmesini kullanır ve yalnız tanımlı override'ların global limiti aşmasına izin verir (`backend/app/request_limits.py:20-50`). Bugün onaylı veri-import yolları:

- `/api/imports/`
- `/api/supplier-price-lists/import`

olarak açıkça kaydedilmiştir (`backend/app/main.py:117-130`).

#168'de netleşen sözleşme testle kilitlidir: yüksek limit alan yollar exhaustive bir allowlist'tir; yeni bir giriş açık review kararı olmalıdır ve geniş bir prefix başka JSON uçlarına limit sızdırmamalıdır (`backend/tests/test_import_route_body_limit.py:234-257`). Bu nedenle önerilen yeni upload yolu kendine ait dar bir prefix kullanacak ve aynı exhaustive teste bilinçli olarak eklenecektir.

### 2.3 Mevcut `supplier_prices` ve fiyat import'u

Güncel tedarikçi teklifinin authoritative tablosu `supplier_product_prices`:

- birim fiyat `Numeric(18,4)`;
- `currency`, MOQ, termin, indirim, tedarikçi stok bilgisi ve aktiflik;
- `(company_id, supplier_id, product_id)` unique;
- şirket, tedarikçi ve ürüne FK

olarak kurulmuştur (`backend/alembic/versions/20260723_0015_supplier_price_comparison.py:46-96`). Zaman içindeki fiyat gözlemleri `supplier_price_history` tablosundadır; fiyat, para birimi, yakalandığı andaki TRY karşılığı, kaynak ve tarih saklanır (`backend/alembic/versions/20260723_0015_supplier_price_comparison.py:144-175`).

Manuel CRUD uçları `POST/PUT/DELETE /api/supplier-prices` üzerinden çalışır. Create ve update current satırı değiştirirken history kaydı ve change-history audit'i aynı transaction'a ekler (`backend/app/routers/supplier_prices.py:1682-1709`, `backend/app/routers/supplier_prices.py:1728-1793`).

Mevcut dosya ucu `POST /api/supplier-price-lists/import`:

- `supplier_id` ve dosya alır; tedarikçiyi `company_id` ile doğrular (`backend/app/routers/supplier_price_import.py:108-123`);
- 10.000 business-row sınırı uygular (`backend/app/routers/supplier_price_import.py:35-38`, `backend/app/routers/supplier_price_import.py:123-128`);
- alias ile yalnız `barcode` ve `price` kolonlarını zorunlu tutar; currency/MOQ/termin opsiyoneldir (`backend/app/routers/supplier_price_import.py:27-33`, `backend/app/routers/supplier_price_import.py:129-180`);
- tenant içindeki aktif ürünleri **yalnız tam barkod** ile eşler (`backend/app/routers/supplier_price_import.py:68-80`);
- aynı barkod birden fazla üründeyse fail-closed davranıp satırı invalid sayar, eşleşmeyen barkodu raporlar; dosya içi aynı üründe son satır kazanır (`backend/app/routers/supplier_price_import.py:183-207`);
- current fiyatı insert/update eder, her eşleşen satıra history yazar ve tek commit/rollback kullanır (`backend/app/routers/supplier_price_import.py:255-319`);
- change-history'de `supplier_price_import/import` özeti bırakır, fakat aktivite paneline olay yazmaz (`backend/app/routers/supplier_price_import.py:292-311`).

Testler tenant dışı barkodun eşleşmediğini, duplicate barkodun seçilmek yerine invalid kaldığını ve update/history davranışını doğrular (`backend/test_v3_supplier_price_import.py:99-118`, `backend/test_v3_supplier_price_import.py:151-163`, `backend/test_v3_supplier_price_import.py:193-231`).

Sonuç: Otomithat “parça no”su barkod değilse mevcut fiyat import'u onu eşleştiremez.

### 2.4 Ürün anahtarları ve Otomithat eşleşme kararı

Ürün master'ında `product_code` ve `barcode` alanları vardır; ikisi için tenant-kapsamlı index bulunur ama unique constraint yoktur (`backend/app/core_schema.py:66-72`, `backend/app/core_schema.py:98-99`). Bu yüzden her iki anahtarda da olası çoklu eşleşme fail-closed ele alınmalıdır.

Önerilen deterministik öncelik:

1. Varsa daha önce onaylanmış `(company_id, supplier_id, normalized_supplier_part_no) → product_id` tedarikçi eşleme kaydı.
2. Dosyadaki değer açıkça EAN/GTIN kolonundan geliyorsa tenant içindeki tam `barcode`.
3. Dosyadaki Otomithat parça no/stok kodu, tenant içindeki normalize edilmiş tam `product_code`.
4. Hiçbir zaman ürün adına göre otomatik eşleme yok; yalnız kullanıcıya öneri olarak gösterilebilir.

Normalizasyon yalnız trim, Unicode normalizasyonu ve dosyanın kanıtladığı zararsız format dönüşümlerinden oluşur. Tire, slash, baştaki sıfır veya harf büyüklüğünü otomatik atma gerçek dosya görülmeden yasaktır; farklı OEM numaralarını yanlış birleştirebilir.

Her otomatik eşleşme `matched_by` (`saved_mapping | barcode | product_code`) ve güven seviyesiyle preview'da görünür. Sıfır sonuç `unmatched`, birden çok sonuç `ambiguous` olur. Bu iki durum apply kapsamına girmez.

Eşleşmeyenler için varsayılan davranış:

- batch durmaz; satırlar indirilebilir “eşleşmeyenler” listesine alınır;
- kullanıcı mevcut ürünü seçip supplier mapping oluşturabilir;
- “yeni ürün oluştur” ayrı, açık yetki ve alan tamamlama akışıdır; Faz-1 default import apply içinde otomatik ürün açılmaz;
- eşleme değişince eski preview geçersiz olur ve yeniden dry-run gerekir.

### 2.5 Supersession'ın mevcut saklanışı

Supersession, parça numarası metni olarak değil ürün ID'leri arasında tutulur:

`part_supersessions(company_id, old_product_id, new_product_id, note, created_by, created_at)`.

DDL, eski ve yeni ürünün farklı olmasını ve bir tenantta eski ürün başına tek successor bulunmasını zorunlu kılar (`backend/alembic/versions/20260724_0018_part_supersessions.py:32-67`). Mevcut modül bulk katalog/feed import'unu açıkça kapsam dışı sayar (`backend/app/routers/part_supersessions.py:1-10`).

Resolver `company_id` literal kapsamıyla zinciri izler, visited-set ile cycle'ı ve 20 hop sınırını fail-closed uygular (`backend/app/routers/part_supersessions.py:69-109`). Manuel create hem iki ürünün tenant sahipliğini doğrular hem yeni uçtan eskiye dönüş olup olmadığını kontrol eder; duplicate/cycle 409'dur (`backend/app/routers/part_supersessions.py:112-159`). Liste ve delete de tenant kapsamlıdır (`backend/app/routers/part_supersessions.py:162-192`).

## 3. Önerilen mimari

### 3.1 Sınırlar

Yeni modül mevcut `supplier_product_prices`, `supplier_price_history`, `part_supersessions`, Decimal yardımcıları, auth/RBAC, change-history ve activity log altyapısını kullanır. Yeni Repository Pattern, event bus veya ayrı fiyat tablosu getirmez.

Fiyat ile supersession aynı kaynak dosyada bulunabilse bile preview raporunda ve onay ekranında ayrı bölümlerdir. Apply tek batch ve tek transaction olabilir; kullanıcı supersession bölümünü ayrıca dahil/haric seçebilir. Bu seçim preview kimliğinin parçasıdır.

### 3.2 Veri modeli (Faz-1 önerisi)

`supplier_import_batches`

- `id`, `company_id`, `supplier_id`, `kind` (`PRICE_CATALOG | SUPERSESSION | COMBINED`)
- `status` (`UPLOADED | PREVIEW_READY | BLOCKED | APPLYING | APPLIED | REVERTING | REVERTED | FAILED | EXPIRED`)
- `original_filename`, `content_sha256`, `file_size`, `detected_format`
- `mapping_template_id`, `mapping_snapshot_json`, `options_snapshot_json`
- `preview_digest`, `preview_counts_json`, `source_effective_date`, `fx_snapshot_json`
- `created_by`, `created_at`, `previewed_at`, `approved_by`, `approved_at`, `applied_at`
- `reverted_by`, `reverted_at`, `revert_reason`
- optimistic `version`

Her sorgu/yazıda `company_id=:cid` literal predicate bulunur. `(company_id, id)` index/unique'i eklenir. Aynı içeriğin ayrı batch yarışını da engellemek için `(company_id, supplier_id, content_sha256, kind)` anahtarında DB-enforced bir content claim bulunur: active/applied claim varken ikinci apply 409'dur. `REVERTED` sonrasında aynı içerik yeniden uygulanacaksa eski claim'e bağlı, gerekçeli yeni revision açılır; sessiz override yoktur. PostgreSQL'de claim anahtarı apply sırasında transaction lock ile seri hale getirilir.

`supplier_import_rows`

- `company_id`, `batch_id`, `source_row_no`, `raw_json`
- normalize alanlar: supplier part no, barcode, price, currency, MOQ, termin, old/new part no
- `resolution_status`, `product_id`, `matched_by`, `validation_errors_json`
- `old_value_json`, `proposed_value_json`, `deviation_percent`

Bu staging satırları current iş tablosu değildir. Parent batch'te `UNIQUE(company_id,id)`, child'da composite FK `(company_id,batch_id) → supplier_import_batches(company_id,id)` bulunur; böylece başka tenant batch'ine bağlanamaz. Preview/result audit için retention politikasıyla tutulur.

`supplier_column_mapping_templates`

- `company_id`, `supplier_id`, `name`, `format_signature`
- canonical alan → kaynak kolon eşlemesi ve dönüşüm ayarları
- `created_by`, `updated_by`, timestamps, `version`, `is_active`

Şablon tenant + tedarikçi kapsamlıdır. Şablon seçimi sadece öneridir; dosya başlık imzası değişmişse kullanıcı tekrar doğrular.

`supplier_product_mappings`

- `company_id`, `supplier_id`, `supplier_part_no_raw`, `supplier_part_no_normalized`, `product_id`
- `created_by`, `created_at`, `updated_at`, `revision`, `source_batch_id`
- unique `(company_id, supplier_id, supplier_part_no_normalized)`

Bu tablo Otomithat parça no ile ERP ürününü kalıcı ve açıklanabilir biçimde bağlar. Uygun parent composite unique'leriyle `(company_id,supplier_id)` ve `(company_id,product_id)` composite FK kullanılır; ürün silme/pasifleştirme halinde mapping otomatik başka ürüne kaymaz. Her manuel remap veya batch kullanımı mapping revision/change ID'sini ilgili ledger'a yazar.

`supplier_import_changes`

- `company_id`, `batch_id`, `change_type`, `target_table`, `target_id`
- `before_json`, `after_json`, `applied_at`, `reverted_at`
- `target_version_after_apply` **ve** hedef satırın canonical after-digest'i

Rollback'in kaynağı budur. Sadece fiyat değil, batch'in oluşturduğu mapping ve supersession değişiklikleri de kaydedilir.

`target_table/target_id` polimorfik olduğu için DB FK ile tüm hedefler korunamaz; izinli `change_type/target_table` kapalı kümedir ve hedef tenant sahipliği apply/revert'te tekrar doğrulanır.

ABA güvenliği için her değiştirilebilir hedef (`supplier_product_prices`, `part_supersessions`, `supplier_product_mappings`) monoton bir `revision` taşımak zorundadır. Faz-1 migration'ı mevcut satırları başlangıç revision'ıyla backfill eder; import dışındaki manuel create/update/delete yolları dahil her mutation revision'ı artırır. Ledger, apply tamamlandığı andaki `target_version_after_apply` değerini kesin olarak saklar. Revision hiçbir revert işleminde eski sayıya döndürülmez; revert'in yazdığı inverse mutation da revision'ı bir artırır.

Dosyanın kalıcı saklanması KVKK/veri-retention kararıdır. Minimum tasarım: hash + metadata + normalize staging; ham dosya saklanacaksa release dışı veri dizini, şifreli erişim, yalnız yetkili download ve süreli silme politikası gerekir.

### 3.3 API ve durum akışı

Önerilen dar uçlar:

- `POST /api/supplier-import-batches/upload`
- `PUT /api/supplier-import-batches/{id}/mapping`
- `POST /api/supplier-import-batches/{id}/preview`
- `POST /api/supplier-import-batches/{id}/apply`
- `POST /api/supplier-import-batches/{id}/revert`
- `GET /api/supplier-import-batches/{id}`
- `GET /api/supplier-import-batches/{id}/rows?status=unmatched|ambiguous|warning|invalid`
- `GET /api/supplier-import-batches/{id}/report.csv`

Yalnız `/api/supplier-import-batches/upload` 11 MiB override alır. Mevcut middleware `startswith` kullandığı için (`backend/app/request_limits.py:46-50`) çıplak string prefix “exact path” değildir; Faz-1, override modeline `exact` eşleşme türü veya segment-boundary-aware matcher eklemelidir. `/upload`, kabul edilecekse `/upload/`, `/upload-evil` ve batch JSON yolları ayrı test edilir. `/api/supplier-import-batches/` prefix'inin tamamına override verilmez. `test_import_route_body_limit.py` exhaustive allowlist'i yeni exact route ile güncellenir.

Başlangıç parser bütçeleri (gerçek dosya ölçümünden sonra yalnız config/review ile artırılır):

- upload: 10 MiB sıkıştırılmış/ham;
- XLSX açılmış toplam entry: 100 MiB ve tek entry: 50 MiB;
- compression ratio: en çok 100:1;
- sheet: en çok 5; toplam business row: 10.000; kolon: 100;
- tek hücre metni: 32.767 karakter; toplam shared-string karakteri: 20 milyon;
- parse deadline: 30 saniye; ihlalde 413/422 ve geçici kaynakların temizlenmesi.

Bu bütçeler workbook tamamen açıldıktan sonra yapılan son-kontrol değildir. ZIP central-directory/entry metadata ön-kontrolünde ve sıkıştırılmış içerik stream edilirken kümülatif sayaçlarla uygulanır; limit aşılır aşılmaz parse durur. Geçici disk alanı ve parser belleği için ölçülebilir üst bütçe konur. Güvenlik testleri yalnız “sonunda 413/422 döndü” sonucunu değil, reddedilen zip bomb/sharedStrings/entry örneklerinde peak bellek ve geçici disk tüketiminin yapılandırılmış bütçeyi aşmadığını da doğrular.

XLSM makroları çalıştırılmaz/saklanmaz (`keep_vba=False`), dış workbook bağlantıları yüklenmez (`keep_links=False`) ve varsa dosya warning/block alır. `data_only=True` formülü tek başına tespit edemediğinden workbook ayrıca formula görünümünde read-only taranır; canonical alanlardaki formül hücresi, cached değer bulunsa bile invalid'dir.

Durum geçişleri:

1. **Upload:** dosya boyutu/türü/hash doğrulanır; hiçbir iş tablosu yazılmaz.
2. **Detect/map:** format (`xlsx/xlsm/csv`, encoding, delimiter, sheet/header row) algılanır. Bilinen alias ve kaydedilmiş şablon önerilir; kullanıcı zorunlu alanları doğrular.
3. **Preview (zorunlu):** tüm satırlar parse/validate/resolve edilir; DB snapshot'ındaki before değerleri ve `preview_digest` üretilir. Kullanıcının manuel ürün seçimleri staging'de `proposed_mapping` kalır; kalıcı supplier mapping henüz yazılmaz. Mevcut saved mapping ile proposed mapping raporda ayrıdır.
4. **Apply:** batch satırı `SELECT ... FOR UPDATE` ile kilitlenir ve CAS ile `PREVIEW_READY → APPLYING` yapılır. Yalnız aynı batch version/digest ve yetkili açık onay çalışır. Hedef kilitleri alındıktan sonra güncel değerler tekrar doğrulanır; preview snapshot'ından farklıysa 409 “preview eskidi” ve yeniden preview gerekir. Proposed mapping yalnız bu transaction'da kalıcılaşır ve ledger'a girer.
5. **Result:** batch `APPLIED`, değişiklik ledger'ı, change-history ve activity olayı aynı transaction'da tamamlanır.
6. **Revert:** uygulanmış batch'in değişiklikleri çatışma kontrolüyle ters sırada geri alınır; sonuç ayrı audit/activity olayıdır.

Apply/revert çağrısı idempotency key kabul eder. `(company_id, operation, idempotency_key)` unique sonuç kaydı ve batch-row kilidi iki eşzamanlı isteği tek sonuca indirger. Aynı batch ikinci kez apply/revert edilmez; tamamlanan ilk sonucun aynısı döner veya durum 409 ile açıklanır. Ana mutation transaction'ı rollback olursa `FAILED` metadata'sı aynı transaction'da kalamaz; hata sınıfı ve correlation ID, mutation rollback'inden sonra ayrı kısa transaction'da yalnız batch metadata'sına yazılır.

### 3.4 Format algılama ve kolon eşleme

Canonical alanlar:

- zorunlu fiyat bölümü: `supplier_part_no veya barcode`, `purchase_price`;
- opsiyonel: `currency`, `effective_date`, `description`, `brand`, `moq`, `lead_time_days`, `supplier_stock`, `discount_percent`, `price_includes_vat`;
- supersession bölümü: `old_part_no`, `new_part_no`, opsiyonel `note/effective_date`.

Algılama çıktısı kullanıcıya şu kanıtlarla gösterilir: seçilen sheet, başlık satırı, delimiter/encoding, bulunan kolonlar, önerilen canonical eşlemeler, ilk 5 maskelenmemiş iş satırı. Fiyat alanında virgül/nokta ayırıcı ve yüzde dönüşümü açık seçenek olur; tahmin sessizce uygulanmaz.

Şablon `format_signature` (normalize başlık seti + sheet adı + supplier) ile önerilir. Zorunlu kolon kayıp, aynı canonical alana iki kolon atanmış veya dönüşüm belirsizse preview başlamaz.

### 3.5 Zorunlu dry-run önizlemesi

Özet en az şunları verir:

- toplam/veri/boş/duplicate satır;
- eşleşen benzersiz ürün;
- yeni supplier-price kaydı;
- değişecek fiyat;
- fiyatı değişmeyen;
- eşleşmeyen;
- ambiguous;
- invalid;
- sapma uyarısı/engeli;
- supersession: yeni, zaten aynı, çatışan successor, cycle, eşleşmeyen eski/yeni parça.

Örnek:

| Kaynak satır | Otomithat no | ERP eşleşmesi | Mevcut → önerilen | Sonuç |
|---|---|---|---|---|
| 12 | `0.900.1234.5` | `product_code`, Ürün #441 | 125,00 TRY → 131,25 TRY (+%5) | Güncellenecek |
| 13 | `8691234567890` | `barcode`, Ürün #812 | fiyat yok → 88,40 TRY | Yeni tedarikçi fiyatı |
| 14 | `OTM-7788` | yok | — | Eşleşmedi; apply dışı |
| 15 | `ABC-01` | 2 ürün | — | Belirsiz; manuel seçim gerekli |
| 16 | `FLT-100 → FLT-240` | iki ürün bulundu | — | Yeni supersession |

Eşleşmeyen ve invalid satırların tamamı UI'da filtrelenebilir ve CSV indirilebilir. Preview sonuçları sayfalıdır; response'a 10.000 satır gömülmez.

### 3.6 Fiyat değişim güvenliği

Faz-1 default önerisi:

- yalnız `supplier_product_prices.price/currency` ve kullanıcı açıkça map etmişse MOQ/termin/tedarikçi stok/indirim güncellenir;
- `products.purchase_price` ve özellikle `products.sale_price` otomatik güncellenmez;
- satış fiyatı/markup ayrı policy ve ayrı preview bölümü olmadan kapsam dışıdır.

Sapma:

`abs(new_price_in_try - old_price_in_try) / old_price_in_try * 100`

tamamen `Decimal` ile hesaplanır. Repo sözleşmesi binary float kullanmadan `Decimal(str(value))` ve açık quantize uygular (`backend/app/money.py:17-43`). Kaynak fiyat `Numeric(18,4)`, TRY normalizasyonu import anındaki kur snapshot'ıyla yapılır. Eski fiyat 0/yoksa yüzde “tanımsız”, mutlak fiyat uyarısı uygulanır.

Tenant/supplier bazlı ayarlanabilir iki eşik önerilir:

- `warning_percent` varsayılan `%25`: preview uyarısı ve onay ekranında açık kabul;
- `block_percent` varsayılan `%100`: apply engeli; yetkili manager gerekçe + ikinci onay veya düzeltilmiş dosya gerekir.

Eşik `X` açık sorudur; gerçek dosyanın dağılımı görülmeden kesinleştirilmez. Ayrıca dosyanın medyan fiyat değişimi ve değişen satır oranı gösterilir. Örneğin satırların %80'i 10 kat artmışsa dosya/para birimi/ondalık hatası şüphesiyle batch fail-closed `BLOCKED` olur.

Her SQL okuma/yazma literal `company_id=:cid` taşır; `supplier_id` ve `product_id` tenant sahipliği yeniden doğrulanır. Apply anında hedef satırlar PostgreSQL'de deterministik product-id sırasıyla kilitlenir; SQLite aynı iş kurallarını korur. Supersession içeren apply/revert ayrıca tenant düzeyinde transaction kilidi alır (PostgreSQL advisory transaction lock veya company lock row); kilitten sonra mevcut zincir + batch linkleri yeniden yüklenip topluca doğrulanır. Böylece eşzamanlı A→B ve B→A batch'lerinden yalnız biri geçebilir. SQLite tek-writer davranışı altında aynı lock-sonrası doğrulama mantığını çalıştırır. Current price + history + import change ledger + audit + activity tek transaction'dır; herhangi biri düşerse tamamı rollback olur.

Preview anında kullanılan her `currency → rate_to_try/date/source` değeri Decimal string olarak `fx_snapshot_json` ve ilgili staging satırına yazılır, `preview_digest`e katılır. Apply canlı kuru yeniden çözmez; preview snapshot'ını kullanır. Policy kur değişimini stale sayacaksa bu daha sonra açıkça seçilebilir, varsayılan snapshot sabitliğidir.

### 3.7 Supersession dosya bölümü

Her `old_part_no → new_part_no` önce aynı supplier mapping/ürün anahtar stratejisiyle iki ERP ürün ID'sine çözülür. İki uçtan biri eşleşmiyorsa ilişki apply edilmez; otomatik ürün açılmaz.

Preview sınıfları:

- `NEW`: eski üründe successor yok, cycle yok;
- `UNCHANGED`: aynı old→new zaten var;
- `CONFLICT`: eski ürün başka successor'a bağlı;
- `CYCLE/BROKEN`: yeni link cycle oluşturuyor veya zincir >20 hop;
- `UNMATCHED/AMBIGUOUS`.

Mevcut create guard'ı ile aynı `resolve_current_product` kuralı toplu apply'da da kullanılmalı; ancak dosyadaki linkler önce bellek üzerinde topluca graph olarak doğrulanmalıdır. Böylece dosya içi A→B ve B→A, DB'ye hiçbir satır yazılmadan yakalanır.

Conflict varsayılan fail-closed'dur. Mevcut successor'ı dosyayla sessizce değiştirmek yasaktır. “Replace existing link” ileride ayrı yetki, açık before/after ve zincirin tüm etkisini gösteren preview gerektirir.

Supersession batch'i fiyat geçmişini geriye doğru taşımıyor; fiyat hangi ERP ürününe eşlendiyse orada kalır. “Yeni parçaya eski parçanın fiyatını miras al” ayrı business kararıdır.

### 3.8 Batch bazında geri alma

Apply sırasında her mutation için before/after snapshot saklanır. Canonical alan manifesti değişiklik türüne göre sabittir:

- `SUPPLIER_PRICE_INSERT`: `price,currency,moq,lead_time_days,discount_percent,supplier_stock,price_includes_vat,note,is_active`; inverse **DELETE**. Yalnız satır hâlâ batch'in canonical after-digest'inde **ve** tam `target_version_after_apply` revision'ındaysa, ayrıca başka kayıt ona işlevsel olarak bağlanmıyorsa silinir. History/audit satırları silinmez.
- `SUPPLIER_PRICE_UPDATE`: aynı canonical iş alanlarının tamamı; inverse before değerlerine UPDATE.
- oluşturulan supersession → yalnız hâlâ aynı old→new canonical link ve tam apply-sonrası revision varsa sil;
- oluşturulan supplier mapping → mapping satırı kilitlenir; after digest/revision aynı ve daha yeni ledger reference yoksa silinir. Manuel remap veya sonraki batch kullanımı varsa tüm revert 409 olur.

Timestamps, DB-generated ID ve audit/history alanları canonical digest'e dahil değildir; update revert'i `updated_at`ı revert anına taşır. Null ile “satır yok” farklıdır. Güvenlik kuralı normatiftir: revert, hedef satırı kilitledikten sonra hem mevcut canonical değerin `after_json`/after-digest ile eşitliğini **hem** mevcut monoton revision'ın ledger'daki `target_version_after_apply` ile eşitliğini doğrular. Bu iki koşuldan biri bile sağlanmazsa tüm batch 409 ile durur; kısmi geri alma yoktur. Kullanıcı çatışma listesini görür. “Force revert” Faz-1 dışında veya ayrı manager çift-onayıyla tasarlanmalıdır.

Bu çift CAS, ABA'yı kapatır: değer `120 → 130 → 120` olarak aynı canonical görünüme dönse bile revision ilerlediği için eski batch geri alınamaz. Batch B'nin revert'i de revision'ı geriye düşürmez; yeni inverse mutation olarak artırır. Kontrollü LIFO-revert istenirse batch bağımlılık grafiği ve açık kullanıcı politikasıyla ayrıca tasarlanır; yalnız digest benzerliğinden türetilmez.

Revert, geçmiş `supplier_price_history` satırlarını silmez; bunun yerine `source='IMPORT_REVERT'` benzeri yeni bir history observation ve batch-revert audit izi üretir. Mevcut history source CHECK'i yeni source için migration gerektirir (`backend/alembic/versions/20260723_0015_supplier_price_comparison.py:144-164`).

Bir batch yalnız bir kez revert edilir. Revert başında batch `SELECT ... FOR UPDATE` ile kilitlenir ve CAS `APPLIED → REVERTING` yapılır. Ledger hedefleri apply ile aynı deterministik sırada kilitlenir; kilitlerden sonra tenant, dependency, mapping revision, canonical after-digest ve `target_version_after_apply` birlikte tekrar doğrulanır. Revert'in kendisi tek transaction'dır ve `supplier_import.reverted` aktivitesi aynı transaction'da yazılır. Paralel revert/revert veya apply/revert yarışında yalnız bir terminal işlem kazanır; kaybeden aynı idempotent sonucu veya 409 alır.

### 3.9 Aktivite paneli ve denetim

Aktivite kataloğu kapalı kümedir; katalog dışı olay ValueError olur (`backend/app/activity_log.py:55-58`, `backend/app/activity_log.py:212-215`). Ayrıca `supplier_import_batch` yeni resource type olarak açıkça eklenmelidir (`backend/app/activity_log.py:109-123`).

Önerilen olaylar:

- `supplier_import.previewed` — dry-run tamamlandı;
- `supplier_import.applied` — batch atomik uygulandı;
- `supplier_import.reverted` — batch geri alındı;
- `supplier_import.blocked` — eşik/format/çatışma nedeniyle engellendi (yalnız güvenlik/operasyon açısından anlamlı bloklarda).

Apply/revert olayları iş mutation'ıyla aynı session/transaction'da yazılır. Mevcut activity helper'ın commit etmemesi bu garantiyi sağlar (`backend/app/activity_log.py:194-241`, `backend/app/activity_log.py:252-278`). Activity details; batch ID, supplier, dosya adı/hash'in kısa gösterimi, count'lar, warning/block count, onaylayan ve revert gerekçesini içerir; ham satır veya hassas dosya içeriği içermez.

Change-history detaylı teknik before/after audit'i, activity paneli ise kullanıcıya dönük olay özetini taşır; biri diğerinin yerine geçmez.

## 4. Yetki, güvenlik ve operasyon

Build öncesi soru 11 ile yeni ayrı permission mı yoksa endpoint içi rol guard mı kullanılacağı kilitlenir; hedef matris:

| İşlem | Minimum erişim | Ek koşul |
|---|---|---|
| upload, mapping, preview | `purchases` | aktif kullanıcı |
| batch/rows/report GET | `purchases` | ticari fiyat içerdiği için generic `read` yetmez |
| apply | `purchases` | onay yetkili rol; kendi kendine ikinci onay yok |
| blocked override, revert | manager/admin | zorunlu gerekçe; açık audit |

Yeni GET/POST yolları `auth.py` permission resolver'a açıkça eklenir; bilinmeyen write path'in admin fallback'ine veya güvenli GET'in generic `read` kuralına güvenilmez.
- CSRF, session auth, tenant resolution ve mevcut error-response deseni korunur.
- Dosya adı hiçbir path oluşturmada kullanılmaz; MIME/uzantı tek başına güven kabul edilmez.
- Formül hücreleri hesaplanmaz; `data_only` sonucu yoksa invalid. CSV formula injection, dışa aktarılan raporda `= + - @` ile başlayan hücreleri escape ederek önlenir.
- ZIP/XML bombası, aşırı kolon/sheet/shared-string, bozuk archive ve parse timeout fail-closed ele alınır.
- Loglarda dosya içeriği, portal kimliği veya kişisel veri yoktur.
- PostgreSQL için eşzamanlı apply/revert, row lock ve preview-staleness testleri; SQLite için iş kuralı/normalizasyon parity testleri zorunludur.

## 5. Kabul kriterleri ve test matrisi

Faz-1 build ancak aşağıdakiler testli olduğunda tamamlanır:

1. CSV (UTF-8/cp1254, `, ; tab`) ve XLSX/XLSM format/header algılama.
2. Kaydedilmiş template önerisi, header değişiminde yeniden doğrulama.
3. Upload exact-path body override; `/upload-evil` ve komşu JSON uçları 2 MiB'de kalır; 10/11 MiB sınırları.
4. Dry-run olmadan apply 409/422; stale preview apply 409.
5. Barcode, product_code, saved mapping; duplicate/ambiguous fail-closed; cross-tenant hiçbir eşleşme yok.
6. Decimal, TRY kur snapshot'ı, warning/block eşikleri ve 0 fiyat davranışı.
7. Tek transaction: fiyat/history/ledger/activity'den biri düşerse hiçbir yazma kalmaz.
8. Aynı batch/idempotency key ve aynı hash'li iki ayrı paralel batch duplicate üretmez; REVERTED sonrası gerekçeli revision davranışı.
9. Supersession new/unchanged/conflict/cycle/>20 hop, dosya içi graph cycle ve iki paralel ters batch'te yalnız bir apply'ın başarıyla tamamlanması (PostgreSQL).
10. Revert success; sonradan manuel değiştirilmiş fiyat/link veya mapping revision/reference varsa tüm revert 409 ve sıfır kısmi yazma; paralel revert/revert ve apply/revert'te yalnız bir terminal sonuç.
    - Batch A `100→120`; manuel `120→130→120`; Batch A revert → 409.
    - Batch A `100→120`; Batch B `120→130`; Batch B revert `130→120`; Batch A revert → 409.
    - Batch A `100→120`; sonraki batch tekrar `120` yazar; Batch A revert → 409.
    - Preview sonrasında manuel fiyat, supplier mapping veya supersession değişir; eski preview apply → gerçek PostgreSQL'de 409 ve price/history/ledger/activity/mapping/supersession yazısı sıfır.
11. Activity olaylarının tenant, actor, correlation ve batch resource bağlantısı.
12. Sonuç/eşleşmeyenler raporunda CSV injection koruması ve sayfalama.
13. Zip bomb, aşırı sharedStrings/entry/sheet/kolon/hücre, external link/macro, cached ve uncached formula, parse timeout ve geçici kaynak cleanup testleri.
14. Endpoint bazlı RBAC negatifleri: yetkisiz, generic-read-only, purchases, manager/admin ve cross-tenant 404/403 sözleşmesi.
15. Preview sonrası kur değişse bile apply'ın digest'teki Decimal FX snapshot'ını kullanması.

## 6. Gerçek örnek dosya bekleniyor

Berkay'dan portaldan doğrudan indirilmiş, mümkünse değiştirilmemiş:

1. gerçek fiyat/katalog dosyası (`.xlsx/.xlsm/.csv`);
2. portal ayrı veriyorsa gerçek supersession/eski→yeni parça dosyası;
3. varsa aynı raporun farklı tarih/filtre/dil seçenekleriyle ikinci örneği

istenmelidir. Dosyalarda ticari fiyat bulunduğu için güvenli proje paylaşım kanalı kullanılmalıdır.

**Asla istenmeyecek:** Otomithat kullanıcı adı, parola, OTP, cookie, erişim token'ı veya ekran paylaşımıyla kimlik bilgisi.

Dosya gelince Faz-0 eki olarak şu keşif tamamlanır: sheet/header satırı, gerçek kolon adları ve tipleri, parça no normalizasyonu, EAN varlığı, para birimi/KDV anlamı, fiyatın alış/net/liste oluşu, duplicate davranışı, supersession gösterimi, satır/dosya boyutu ve tarih semantiği. Bunlar görülmeden final kolon mapping veya default dönüşüm kilitlenmez.

## 7. Berkay için açık sorular

1. Otomithat fiyatı ERP'de yalnız tedarikçi alış teklifi (`supplier_product_prices`) mi güncellesin, yoksa `products.purchase_price` da projekte edilsin mi?
2. `products.sale_price` bu akışta hiç değişsin mi? Değişecekse markup/marj, KDV ve yuvarlama kuralı nedir?
3. Dosyadaki fiyat liste, net, iskonto öncesi/sonrası ve KDV dahil/hariç hangisidir?
4. TRY/EUR/USD karışık olabilir mi; kur kaynağı/tarihi dosyada mı, TCMB mi, şirket override'ı mı?
5. MOQ, termin, tedarikçi stok, marka/açıklama gibi hangi alanlar authoritative kabul edilsin?
6. Eşleşmeyen ürünler yalnız kuyruğa mı düşsün, yoksa kullanıcı onayıyla yeni ürün açılabilsin mi? Yeni ürün için zorunlu kategori/birim/KDV nedir?
7. Otomithat parça no bugün ERP'de çoğunlukla `product_code` mu, `barcode` mu, başka bir alan mı?
8. Tire, boşluk, nokta, slash, baştaki sıfırlar ve harf büyüklüğü parça numarası anlamını değiştiriyor mu?
9. Warning/block eşiği `X` kaç olmalı? Önerilen başlangıç `%25/%100` uygun mu?
10. Her import mutlaka insan onaylı mı kalacak? Öneri: Faz-1'de evet; otomasyon ancak güvenilir dosya geçmişinden sonra ayrı faz.
11. Apply için tek onay yeterli mi, büyük sapmada ikinci manager onayı gerekir mi?
12. Supersession dosyası eski→yeni ilişki mi, yoksa tek hücrede zincir mi verir? Mevcut link çatışırsa yalnız engelleyelim mi?
13. Supersession ile fiyat aynı batch'te atomik mi uygulanmalı, yoksa ayrı onay/batch mi tercih edilir?
14. Batch rollback kaç gün açık kalsın? Sonraki manuel/batch değişikliklerinde force revert tamamen yasak mı olsun?
15. Ham dosya denetim için saklansın mı; saklanırsa retention süresi ve erişebilecek roller?
16. Aynı dosyanın tekrar yüklenmesi kesin engel mi, yoksa gerekçeli yeniden uygulama ihtiyacı var mı?

## 8. Önerilen teslim dilimleri ve karar kapısı

Faz-1A: batch + upload + format/mapping template + zorunlu preview + unmatched çözümleme.

Faz-1B: fiyat apply + deviation gate + history/audit/activity + atomiklik/idempotency.

Faz-1C: supersession preview/apply.

Faz-1D: çatışma kontrollü batch revert.

Build'e geçiş kapısı:

- gerçek örnek dosya(lar) incelenmiş;
- açık sorulardaki fiyat/ürün/sapma/onay politikaları karara bağlanmış;
- bu tasarımın iki reviewer turundaki blocker/high bulguları kapanmış;
- kullanıcı uygulama için açık onay vermiş olmalıdır.

### Build öncesi kapanacak kararlar

1. Berkay'ın sağlayacağı gerçek Otomithat fiyat/katalog ve varsa supersession dosyasıyla sheet/header, parça numarası normalizasyonu, EAN, encoding/delimiter, duplicate ve tarih semantiği doğrulanır.
2. Yalnız tedarikçi alış teklifinin mi, ayrıca ürün alış/satış fiyatının mı güncelleneceğine Berkay karar verir. `%25` uyarı / `%100` blok başlangıç önerisi gerçek dosya fiyat dağılımı incelendikten sonra kesinleşir; büyük sapmada manager/ikinci onay akışı ayrıca kilitlenir.
3. Eşleşmeyen satırlarda varsayılan fail-closed korunur; kullanıcı onayıyla yeni ürün açılacaksa zorunlu kategori/birim/KDV ve yetki politikası Berkay tarafından onaylanır.
4. RBAC için öneri: mevcut izin modelini gereksiz genişletmemek üzere upload/mapping/preview/report için `purchases`, apply için `purchases` + onay yetkili rol guard'ı, blocked override/revert için manager/admin guard'ı. Ayrı bir `supplier_import` permission'ı mı yoksa bu öneri mi kullanılacağı Berkay'a sorulup build öncesinde karara bağlanır.

## 9. Bilinen bağımlılıklar ve riskler

- `supplier_product_prices` ve `part_supersessions` FK'ları ürünün aynı tenantta olduğunu DDL düzeyinde composite FK ile garanti etmiyor; uygulama her hedefi tenant literal ile doğrulamalıdır. Faz-1 migration tasarımında composite tenant FK uygunluğu ayrıca değerlendirilmelidir.
- Ürün `product_code`/`barcode` indexleri unique değildir; ambiguity normal bir durum olarak desteklenmelidir.
- Mevcut supplier import doğrudan yazar. Yeni akış oturduğunda eski uç ya yeni batch motoruna delegasyon yapmalı ya da kontrollü deprecation almalıdır; iki farklı import semantiği kalmamalıdır.
- Activity katalog/resource genişlemesi migration değil kod/test değişimidir; ancak import batch tabloları ve history source genişlemesi Alembic migration gerektirir.
- Açık Draft PR #148 birleşmemiştir; tasarım onun varlığına runtime bağımlılık kurmaz.

## 10. Reviewer turu 1 — mimari/doğruluk

Sonuç: blocker yok; 3 high ve 3 medium bulgu kapatıldı.

- Eşzamanlı supersession cycle riski → tenant transaction lock + lock sonrası birleşik graph doğrulaması ve PG race testi eklendi.
- Prefix matcher'ın exact-route sağlamaması ve RBAC fallback riski → boundary-aware limit matcher ve açık permission map sözleşmesi eklendi.
- Rollback'in alan/inverse belirsizliği → canonical alan manifesti, insert için DELETE ve digest dışı alanlar kesinleştirildi.
- Proposed mapping zamanı → staging'de kalır, yalnız apply transaction'ında kalıcılaşır.
- Tenant DDL → composite parent/child FK'lar ve polimorfik ledger riski açıklandı.
- Apply yarış/idempotency → batch row lock, CAS ve unique operation key tasarlandı.

## 11. Reviewer turu 2 — güvenlik/operasyon

Sonuç: blocker yok; 2 high, 3 medium ve 1 low bulgu kapatıldı. Tur 1'deki altı düzeltmenin belgede karşılığı ayrıca doğrulandı.

- Revert yarışı → `APPLIED → REVERTING` CAS, batch/hedef lock sırası, yeniden doğrulama, idempotency ve PG yarış testleri eklendi.
- Parser kaynak tüketimi → ölçülebilir archive/sheet/row/column/cell/shared-string/deadline bütçeleri ile macro/link/formula politikası eklendi.
- Aynı hash'li ayrı batch yarışı → DB content claim + transaction lock ve revision semantiği eklendi.
- RBAC → endpoint bazlı hedef matris ve negatif/cross-tenant kabul testleri eklendi.
- Mapping revert bağımlılığı → revision, source batch, ledger reference ve lock kuralı eklendi.
- FX → preview'da kalıcı Decimal rate/date/source snapshot, digest ve apply sabitliği kesinleştirildi.

## 12. ChatGPT kırmızı delta — ABA ve build kilitleri

Karar: kırmızı bulgu tasarımda kapatıldı; build hâlâ gerçek dosya ve ürün politikası kararlarına kadar kapalıdır.

- Ledger artık `target_version_after_apply` **ve** canonical after-digest'i birlikte zorunlu tutar.
- Tüm değiştirilebilir hedefler monoton revision taşır; manuel/import/revert dahil her mutation artırır, revert hiçbir zaman revision'ı geriye almaz.
- Revert çift CAS uygular: hem canonical after değeri hem tam beklenen revision eşleşmelidir.
- Manuel ABA, iç içe batch apply+revert ve aynı-değerli sonraki batch için zorunlu PostgreSQL 409/sıfır-yazma testleri eklendi.
- Parser limitlerinin ZIP entry/stream sırasında uygulanması ve peak bellek/geçici disk bütçesinin testle kanıtlanması normatif hale getirildi.
- Gerçek dosya, fiyat/sapma/büyük-onay, yeni ürün ve RBAC kararları ayrı build-öncesi kapanış listesinde toplandı.
