# Kullanıcı Aktivite Paneli (activity_logs) — v1

Yönetici, "kim ne yaptı"yı tek ekrandan okur: *"Satış görevlisi X şu satışı yaptı /
şu satışı iptal etti / şu ödemeyi sildi."* Panel yalnız **admin + yönetici**
rolüne görünür; arşivleme yalnız **admin**'dedir.

## Şema

`activity_logs` (migration `20260727_0030`, `down_revision = 20260727_0029` — Servis İş Emri v2 FAZ-1 (#168); expand-only, yalnız yeni tablo + indeks):

| Kolon | Not |
|---|---|
| `id`, `company_id`, `user_id` | `company_id → companies.id` FK; `uq_activity_logs_company_id (company_id, id)` kiracı bileşik anahtarı |
| `action_type`, `resource_type`, `resource_id` | `resource_id` toplu olaylarda (toplu fiyat) NULL |
| `summary` | Türkçe, panelde doğrudan gösterilebilir insan-okur özet |
| `details` | JSON: eski/yeni değer özü |
| `correlation_id`, `created_at` | `correlation_id` istek kimliğidir (NULL olabilir) |
| `archived_at`, `archived_by`, `archive_reason` | CHECK: üçü birden NULL ya da üçü birden dolu |

İndeksler: `(company_id, created_at DESC, id DESC)`, `(company_id, user_id)`,
`(company_id, resource_type, resource_id)`.

## Değişmezlik (pazarlıksız)

Değişmezlik API disiplinine **bırakılmaz**; iki katmanda birden uygulanır.

**API katmanı**

* Tabloya UPDATE/DELETE ucu **yoktur**. Çekirdek alanlara (özet, detay, kullanıcı,
  zaman, kaynak) dokunan bir yol bulunmaz; `PUT/PATCH/DELETE /api/activity-logs/{id}`
  404/405 döner.
* Tek istisna arşiv üçlüsüdür: `POST /api/activity-logs/{id}/archive` (admin,
  gerekçe zorunlu, ≥5 karakter) ve `.../unarchive`. Bu uçlar yalnız
  `archived_at/archived_by/archive_reason` kolonlarını yazar.
* **Arşivleme/geri alma işleminin kendisi de ayrı bir aktivite satırı üretir.**
* Gerçek silme ucu yoktur.

**Veritabanı katmanı (migration `0030` içinde)**

Yanlış yazılmış bir iç kod yolu, bir bakım scripti veya elle açılmış bir `psql`
oturumu da çekirdeği değiştiremez — koruma uygulamanın kendi DB rolü için de
geçerlidir:

| Motor | DELETE | UPDATE |
|---|---|---|
| PostgreSQL | `trg_activity_logs_no_delete` → her koşulda `RAISE EXCEPTION` | `trg_activity_logs_core_immutable` → OLD/NEW karşılaştırır, çekirdek kolonlardan biri değişirse `RAISE EXCEPTION` |
| SQLite | `trg_activity_logs_no_delete` → `RAISE(ABORT, …)` | `BEFORE UPDATE OF <çekirdek kolonlar>` → `RAISE(ABORT, …)` |

Korunan kolon listesi elle yazılmaz: migration'daki `_columns()` tanımından
`core_columns() = tüm kolonlar − arşiv üçlüsü` olarak türetilir; tabloya
eklenecek yeni bir kolon sessizce korumasız kalamaz. Arşiv üçlüsü kasıtlı olarak
korunmaz — arşivleme yolunun açık kalması için. Karışık güncelleme (arşiv üçlüsü
+ çekirdek kolon aynı `UPDATE`'te) de reddedilir.

Dialect farkı bilinçlidir: SQLite tetikleyicisi kolon `SET` listesinde
*anıldığı* anda çalışır (değer değişmese bile), PostgreSQL değer karşılaştırır.
İkisi de aynı invariantı korur; SQLite bir tık daha katıdır.

## Yakalama — merkezî helper, aynı transaction

`app/activity_log.py::log_activity(conn, company_id, user_id, action_type,
resource_type, resource_id, summary, details)` çağıranın **kendi Session'ında**
çalışır ve **commit etmez**. Denetim garantisi buradan gelir:

* işlem rollback olursa log satırı da yazılmaz;
* log satırı yazılamazsa istisna yükselir ve işlem de düşer.

Middleware/sinyal sihri yoktur — çağrı işlemin tam noktasında açıktır. Kendi
oturumunu açıp içeride commit eden motorlarda (ödeme oluşturma, vade farkı,
tahsis) kayıt, commit'ten hemen önce çalışan açık bir kanca (`on_created` /
`on_posted` / `on_reversed` / `activity_hook`) ile yine **aynı transaction**
içinde alınır. Idempotent tekrar oynatmada kanca çağrılmaz: tekrar oynatma yeni
bir olay değildir.

## v1 olay kataloğu (kapalı küme)

| Alan | `action_type` | Uç |
|---|---|---|
| Satış | `sale.create` / `sale.update` | `POST/PUT /api/orders` |
| Satış | `sale.cancel` | `DELETE /api/orders/{id}` |
| POS | `pos.sale_created` | `POST /api/pos/sale` |
| POS | `pos.sale_cancelled` | `DELETE /api/orders/{id}` (POS kökenli fiş) |
| Ödeme | `payment.create` / `payment.update` / `payment.delete` | `POST/PUT/DELETE /api/payments` |
| İade | `return.create` | `POST/PUT /api/workflow/sale_return\|purchase_return` |
| Fiyat | `product.bulk_price_update` | `POST /api/products/bulk-price` |
| Stok | `stock.adjust` | `POST /api/products/{id}/stock` |
| Stok | `stock.transfer` | `POST /api/warehouses/transfers` |
| İş emri | `work_order.create` | `POST /api/work-orders` |
| İş emri | `work_order.status_change` / `work_order.cancel` | `PATCH /api/work-orders/{id}/status` |
| Fatura | `invoice.create` | `POST /api/invoices/generate` |
| Fatura | `invoice.cancel` | `POST /api/invoices/{id}/cancel` |
| Vade farkı | `late_fee.draft` / `late_fee.post` / `late_fee.reversal` | `POST /api/finance/late-fees/charges[/{id}/post\|/reversal]` |
| Tahsis | `allocation.manual` / `allocation.reversal` / `allocation.reallocation` | `POST /api/payment-allocations/...` |
| Kullanıcı | `user.create` | `POST /api/users` |
| Kullanıcı | `user.status_change` | `PATCH /api/users/{id}/status` |
| Panel | `activity_log.archive` / `activity_log.unarchive` | `POST /api/activity-logs/{id}/archive\|unarchive` |

**POS notu.** Dükkândaki satışın büyük kısmı dokunmatik POS'tan geçtiği için POS
ayrı olay tipleridir; POS ucu `_save`'i `request` vermeden çağırdığından
`sale.create` orada tetiklenmez (çift kayıt yok). POS'un ayrı bir iptal ucu
yoktur — iptal `DELETE /api/orders/{id}`'den geçer ve kaynağı
`pos_idempotency.order_id` üzerinden ayırt edilip POS olayı olarak yazılır.
Ayrı bir POS iade ucu bulunmadığı için `pos.sale_refunded` **eklenmedi**; iade
akışı `POST /api/workflow/sale_return` üzerinden `return.create` üretir. Kaynak
tipi POS'ta da `sale`'dir: POS fişi de bir `orders` satırıdır, paneldeki kaynak
linki bu sayede çalışır.

`user.role_change` katalogda **rezerve** tutulur: `develop` üzerinde rol
değiştiren bir uç yoktur (yalnız aktif/pasif değişimi vardır), bu yüzden v1'de
emisyonu yoktur.

Örnek özet: `ORD-123 satışını iptal etti — 4.500,00 TL, müşteri: Ahmet Y.`
Tutarlar `Decimal` üzerinden `format_money_tr` ile üretilir; `float` yoluna
girilmez.

## API

* `GET /api/activity-logs` — admin/yönetici (aksi 403). Filtreler: `user_id`,
  `action_type`, `resource_type`, `date_from`, `date_to`, `include_archived`
  (varsayılan `false`, **yalnız admin** açabilir). Sunucu tarafı sayfalama:
  `limit` (1–100, varsayılan 50) + `offset`; yanıt `{items,total,limit,offset}`.
* `GET /api/activity-logs/catalog` — filtre çubuğunun beslendiği olay kataloğu.
* `GET /api/activity-logs/{id}` — tekil kayıt; tenant dışı ve yöneticinin
  göremeyeceği arşivli kayıt aynı 404 ile karşılanır.
* `POST /api/activity-logs/{id}/archive` · `.../unarchive` — yalnız admin.

Yetki haritası: `required_permission("*", "/api/activity-logs*") == "users"`
(admin + yönetici); router ayrıca rolü açıkça doğrular.

## UI

Yönetim menüsünde **Aktivite** (`/aktivite`, `users` yetkisi): tablo
(zaman · kullanıcı · özet · kaynak linki), filtre çubuğu, satır detay çekmecesi
(`details` JSON'undan eski/yeni), admin'e arşivle butonu + gerekçe modalı ve
"arşivlenenleri göster" anahtarı. Para değerleri sunucudan gelen metinlerdir;
istemcide `Number()` ile karar verilmez.

## Testler

`backend/test_activity_log_panel.py` (+ PostgreSQL ikizi
`test_activity_log_panel_postgresql.py`): tenant izolasyonu · RBAC · değişmezlik
(405/404 kanıtı + satırın diskte değişmediği) · arşiv akışı ve arşivin kendisinin
loglanması · aynı-tx garantisi (iki yönlü) · satış iptali, ödeme silme, toplu
fiyat, POS satışı ve POS iptali uçlarından gerçek HTTP emisyonu · POS idempotent
tekrar oynatmada ikinci kayıt yazılmaması · başarısız POS isteğinde (404/400/409)
hiç kayıt oluşmaması · sayfalama ve filtreler.

**DB-seviyesi değişmezlik, doğrudan ham `text()` SQL ile** (uçlardan değil, hem
SQLite hem PG twin): (a) `DELETE` → hata, satır yerinde; (b) `summary`,
`user_id`, `action_type` `UPDATE`'leri → hata, değer değişmemiş; (c) yalnız arşiv
üçlüsü `UPDATE`'i → başarılı; (d) arşiv üçlüsü + çekirdek kolon aynı `UPDATE`'te
→ hata, kısmi uygulama yok; (e) HTTP arşiv/unarchive uçları trigger'lı şemada
sorunsuz. Ayrıca canlı şemadan okunan trigger tanımı, tablonun gerçek kolon
listesiyle karşılaştırılarak "korumasız çekirdek kolon yok" doğrulanır.
`frontend/src/pages/ActivityLog.test.tsx`: liste, filtre→sorgu, detay çekmecesi,
arşiv gerekçe akışı, yöneticinin arşiv yüzeyini hiç görmemesi, boş/hata durumları.

## Bilinçli kapsam dışı

* Teklif / sipariş / irsaliye belgeleri, alış (purchase) oluşturma-güncelleme,
  makine kartı ve tedarikçi fiyat işlemleri v1 kataloğunda yoktur (v2).
* `pos.sale_refunded`: POS'ta ayrı bir iade ucu bulunmadığı için eklenmedi.
* Rol değişikliği olayı (uç yok — katalogda rezerve).
* CSV/Excel dışa aktarım, saklama süresi (retention) politikası ve otomatik
  arşivleme.
* Cursor tabanlı sayfalama (v1 `limit`+`offset` kullanır).
