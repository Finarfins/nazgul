# Faz 9-4: Çek/Senet Portföyü Keşfi ve PR Planı

**Tarih:** 2026-09-10  
**Başlangıç Commit:** `c66e232` (`Merge pull request #115 from Finarfins/feat/5-1c-kiraci-geri-yukleme`)  
**Kapsam:** Salt okunur keşif, mimari analiz, muhasebe semantiği kararı ve 3 PR'lık uygulama planı.

---

## 1. Mevcut Ödeme ve Finans Altyapısı Envanteri

- `backend/app/core_schema.py:256-274`: `payments` tablosu `(id, entity_type, entity_id, amount, payment_date, note, company_id, branch_id, payment_method, reference_type, reference_id, account_id, financial_transaction_id)` alanlarını taşır. Tabloda bir `due_date` (vade tarihi) sütunu **YOKTUR**.
- `backend/app/document_engine.py:15`: `PAYMENT_METHODS = {"credit", "cash", "card", "bank_transfer", "check", "promissory_note", "mixed"}` kümesi çek (`check`) ve senet (`promissory_note`) tanımlarını içerir. Ancak `document_engine.py:22`deki `PAYMENT_TERMS = {"PESIN", "HARMAN_VADELI"}` kuralı yalnızca satışlara (`orders.due_date`) aittir; ödemelerde vadeli evrak kavramı **YOKTUR**.
- `backend/app/finance_engine.py:42-61`: Temel şemadan (`alembic/versions/20260712_0000_schema_baseline.py:89,92`) gelen ilkel bir `financial_instruments` tablosu mevcuttur: `(id, company_id, instrument_type, direction, status, entity_type, entity_id, amount, issue_date, due_date, serial_no, bank_name, account_no, note, account_id, financial_transaction_id, created_at)`.
- `backend/app/finance_engine.py:63-83`: `ACCOUNT_TYPES = {'cash', 'bank', 'pos'}` ve `METHOD_ACCOUNT_TYPES = {'cash': 'cash', 'card': 'pos', 'bank_transfer': 'bank'}`. Çek/senet için tanımlı bir hesap tipi YOKTUR. Bir ödeme `check` yöntemiyle ve `account_id` ile gönderilirse `validate_payment_account` HTTP 400 fırlatır; `account_id=None` ise `sync_payment_finance` (`:157-172`) finans işlemi üretmez (`financial_transaction_id=NULL`).
- `backend/app/routers/finance.py:273-352`: `POST /api/payments` tahsilat/ödeme kaydı oluşturur. `payments` ile `financial_instruments` arasında hiçbir bağ **YOKTUR**; çek yöntemiyle ödeme girildiğinde portföy satırı doğmaz.
- `backend/app/routers/finance.py:663-709`: `/api/finance/instruments` uçları `financial_instruments` tablosuna bağımsız CRUD sunar. `PUT /status` (`:683-700`) evrak durumu `collected`/`paid` olduğunda `finance_transactions`a satır ekler; ancak cari bakiyeye (`customers`/`suppliers`) veya `payments`a dokunmaz.
- `backend/alembic/versions/20260726_0025_payment_allocation_foundation.py:22-69` & `20260728_0032_harman_v2c_charge_allocation.py:57-80`: `payment_allocations` tablosu `payment_id`yi `order_id` veya `receivable_charge_id`ye bağlar; evrak türü veya vade bilmez.
- `backend/app/billing_service.py:14-79`: İş emri işçilik/parça faturalandırmasını yönetir; ödeme aracı veya evrak yönetimiyle ilgisi yoktur.
- `backend/app/allocation_reconciliation.py:51-100`: `payments.amount` ile `payment_allocations` mutabakatını denetler; çek/senet ayrımı yoktur.
- `backend/app/late_fee_engine.py:55-100`: `calculate_late_fee` faiz hesabını `due_date` ve `PrincipalChange` timeline'ı üzerinden yapar (`receivables_engine.py:34-51`); karşılıksız çek gecikme faizi zinciri yoktur.
- `frontend/src/pages/Finance.tsx:19,43-47,52`: Ön yüzde "Nakit Yönetimi" altında ilkel bir "Çek / Senet" sekmesi bulunur. Tahsilat hesabı `prompt()` ile alınır (`:44`); bağımsız bir portföy takip ekranı, bordro veya ciro yönetimi yoktur.

---

## 2. `cek_senetler` Veri Modeli ve Cari Bakiye Muhasebe Kararı

### 2.1. Muhasebe Kararı: Çek Cariyi Ne Zaman Düşer?
- **Seçenek A (Alındığında Düşer — Türk Muhasebe & Ticari Teamül / ÖNERİLEN):**
  - *Gerekçe:* Müşteri çeki verdiğinde açık hesap borcu kapanmış kabul edilir (Tekdüzen Hesap Planı: `101 Alınan Çekler B / 120 Alıcılar A`). Fatura tahsisi yapılır.
  - *Risk Yönetimi:* Çek karşılıksız çıktığında veya iade edildiğinde ters kayıt/dekont ile cariye yeniden borç yazılır (`120 B / 101 A`), tahsis iptal edilir veya karşılıksız çek borç belgesi (`receivable_charge_documents`) doğar.
- **Seçenek B (Tahsil Edildiğinde Düşer — Nakit Esaslı / İfa Uğruna Model):**
  - *Gerekçe:* TTK m. 730 uyarınca çek ödeme yerine değil ödemeyi teminen verilir. Nakit hesaba geçene kadar borç hukuken son bulmaz.
  - *Mahzur:* Müşteri ekstrede çek verdiği halde borçlu görünür; çiftçi/bayi mutabakatını imkânsız kılar.
- **Tavsiye:** **Seçenek A**. Çek alındığında `payments` satırı açılarak cari bakiye düşürülmeli, durum `'portfoyde'` olarak takip edilmeli; karşılıksız durumunda otomatik borç dekontu üretilmelidir.

### 2.2. Veri Modeli (`cek_senetler`)
Kiracı tablosudur (`company_id` taşır). Aynı firma bileşik yabancı anahtar sözleşmesi (göç 0044: `20260807_0044_farm_management_v1.py`) gereği tüm ilişkiler kiracı kapsamındadır.

| Sütun | Tip | Kısıt / Nullable | Açıklama |
| :--- | :--- | :--- | :--- |
| `id` | `Integer` | PK, autoincrement | Kayıt kimliği |
| `company_id` | `Integer` | NOT NULL, FK -> companies.id | Kiracı kimliği |
| `tur` | `String(20)` | NOT NULL, CHECK in ('cek','senet') | Evrak türü |
| `yon` | `String(20)` | NOT NULL, CHECK in ('alinan','verilen') | Alınan (müşteri) / Verilen (kendi/tedarikçi) |
| `portfoy_durumu` | `String(30)` | NOT NULL, default 'portfoyde' | `portfoyde`, `tahsile_verildi`, `tahsil_edildi`, `ciro_edildi`, `karsiliksiz`, `iade` |
| `customer_id` | `Integer` | NULL, FK -> (company_id, customer_id) | Alınan evrakta borçlu/keşideci müşteri |
| `supplier_id` | `Integer` | NULL, FK -> (company_id, supplier_id) | Verilen evrakta lehtar tedarikçi |
| `endorsed_supplier_id`| `Integer` | NULL, FK -> (company_id, endorsed_supplier_id) | Ciro edilen tedarikçi |
| `endorsed_date` | `Date` | NULL | Ciro tarihi |
| `tutar` | `Numeric(18,2)`| NOT NULL, CHECK tutar > 0 | Evrak nominal tutarı (MONEY) |
| `vade` | `Date` | NOT NULL, Index | Vade tarihi |
| `keside_tarihi` | `Date` | NULL | Düzenleme tarihi |
| `banka_adi` | `String(160)` | NULL | Muhatap banka |
| `sube_adi` | `String(120)` | NULL | Banka şubesi |
| `hesap_no` | `String(100)` | NULL | Keşideci hesap no |
| `seri_no` | `String(100)` | NOT NULL | Çek/senet seri ve sıra no |
| `kesideci` | `String(200)` | NULL | Asıl keşideci/borçlu unvanı (cirolu çekler için) |
| `tahsil_hesap_id` | `Integer` | NULL, FK -> (company_id, tahsil_hesap_id) | Tahsilatın aktarıldığı kasa/banka hesabı |
| `tahsil_tarihi` | `Date` | NULL | Gerçekleşen tahsilat/ödeme tarihi |
| `payment_id` | `Integer` | NULL, FK -> (company_id, payment_id) | Doğurduğu/bağlı olduğu payments satırı |
| `financial_transaction_id`| `Integer`| NULL | Kasa/banka hareket kaydı kimliği |
| `charge_document_id` | `Integer` | NULL | Karşılıksız çıktığında doğan borç belgesi FK |
| `notlar` | `Text` | NULL | Açıklama ve zilyetlik notları |
| `created_at` | `DateTime` | NOT NULL, server_default=now() | Kayıt zaman damgası (UTC) |
| `created_by` | `Integer` | NULL, FK -> app_users.id | Oluşturan operatör |

**İndeksler ve Kısıtlar:**
- `ix_cek_senetler_company_durum_vade`: `(company_id, portfoy_durumu, vade)`
- `ix_cek_senetler_company_customer`: `(company_id, customer_id)`
- `ix_cek_senetler_company_supplier`: `(company_id, supplier_id)`
- `ix_cek_senetler_company_seri`: `(company_id, seri_no)`
- `ck_cek_senetler_yon_taraf`: Alınan evrakta `customer_id IS NOT NULL`, verilen evrakta `supplier_id IS NOT NULL`.

---

## 3. Entegrasyon Noktaları

1. **Payment → Çek Bağı (`finance.py:273-352`):**
   - `POST /api/payments` üzerinde `payment_method='check'` seçildiğinde, `payments` satırıyla birlikte atomik olarak `cek_senetler` satırı (`portfoy_durumu='portfoyde'`) oluşturulur.
   - Tersine, bağımsız bordro girişi yapıldığında da opsiyonel olarak `payments` oluşturulup fatura tahsisine bağlanır.
2. **Fatura Tahsisleri (`payment_allocation_engine.py:605,2147`):**
   - Çek ile tahsilat faturaya tahsis edilir (`payment_allocations`).
   - Çek karşılıksız çıktığında (`karsiliksiz`): İki yoldan biri seçilir: (a) Tahsis `reversal` ile geri alınır ve fatura açılır, ya da (b) Tahsis korunur, karşılıksız çek için `receivable_charge_documents` açılarak cariye borç yazılır (Önerilen: B).
3. **Ekstre Satırları (`statement.py:319-325`):**
   - Mevcut durumda `statement.py` çekleri `Tahsilat (Çek)` olarak düz düşmektedir.
   - `statement.py`ye `cek_senetler` LEFT JOIN yapılarak ekstre satırında durum ibaresi gösterilmelidir: `Tahsilat (Çek - Portföyde)`, `Tahsilat (Çek - Tahsil Edildi)`, `İade/Karşılıksız Çek Dekontu`.
4. **Gecikme Zammı (`late_fee_engine.py:55`):**
   - Karşılıksız çıkan çek için `receivable_charge_documents` türü `late_fee` / `bounced_check` borcu açılarak orijinal vade tarihinden itibaren gecikme faizi işletilir.
5. **Pano ve Alacak Yaşlandırma (`dashboard.py:108-114`, `reports.py:286-350`):**
   - Pano özetinde (`dashboard.py:166`) `customer_receivables` yanında `portfolio_checks` (portföydeki bekleyen çekler) kartı eklenmeli.
   - `reports/receivables-aging` raporuna "Portföy Çekleri" kolonu eklenerek net risk (Alacak − Portföydeki Çekler) ayrıştırılmalıdır.
6. **WhatsApp "Çekim ne oldu" Niyeti (Faz 10-1):**
   - `backend/app/whatsapp/niyet.py:65-96`: `_SORU_SOZLUGU_HAM` içine `CEK`, `SENET` kökleri eklenecek.
   - `niyet.py:132-160`: `CEK_KOKLER = {"CEK", "SENET", "EVRAK"}` tanımlanacak. Doğrulanmış müşteri mesajında çek sorulduğunda `cek_durumu(customer_id)` aracı tetiklenecektir.
7. **Dışa Aktarım ve Geri Yükleme (5.1c):**
   - `test_tenant_scoping_guard.py:41-201`: `TENANT_TABLES` listesine `cek_senetler` eklenir (E4a sonrası 121 -> 122).
   - `kiraci_geri_yukleme.py:172-200`: `DOGRUDAN_HEDEFLER`e müşteri, tedarikçi, tahsil hesabı ve ödeme FK'leri eklenir.
   - `numeric_manifest.py:58`: `cek_senetler: ("tutar",)` kaydedilir.

---

## 4. Veritabanı Göçü (Migration) Tasarımı

- **Göç Sürümü:** **`20260914_0085_cek_senet_portfoyu.py`** (Geliştirme dalı: `0082`, bekleyen PR 116 E4a: `0083`, SEC-9: `0084`).
- **Hedef Tablo:** `cek_senetler` (yukarıdaki tablo yapısıyla tek seferde kurulur).
- **Mevcut `financial_instruments` Tablosunun Durumu:**
  - `0085` göçü yalnızca yeni `cek_senetler` tablosunu oluşturur (yalnızca oluşturma — creation only); mevcut `financial_instruments` tablosuna KESİNLİKLE dokunmaz.
  - Eski `financial_instruments` tablosunun emekliye ayrılması (retirement), veri aktarımı veya kod tasfiyesi bu PR ve göçün kapsamı dışındadır; bağımsız bir sonraki hijyen maddesidir.
- **DDL İkizleri:** SQLite `PRAGMA foreign_keys=ON` ve PostgreSQL `ALTER TABLE ... VALIDATE CONSTRAINT` sözleşmelerine tam uyumlu olmalıdır.

---

## 5. PR Bölümleme Planı ve Pin Deltaları

### PR 1: CS1 — Model, Göç, CRUD ve Portföy Durum Makinesi (Backend)
- **Kapsam:**
  - `alembic/versions/20260914_0085_cek_senet_portfoyu.py` göçü.
  - `backend/app/cek_senet_engine.py` (durum makinesi: portföyde -> tahsilde -> tahsil edildi / ciro / karşılıksız / iade).
  - `backend/app/routers/cek_senetler.py` (uç noktalar: `GET/POST /api/cek-senetler`, `GET /api/cek-senetler/{id}`, `POST /api/cek-senetler/{id}/durum-degistir`, `POST /api/cek-senetler/bordro`).
  - `backend/app/auth.py`: `/api/cek-senetler` için açık önek kuralı eklenmesi (`if path.startswith("/api/cek-senetler"): return "payments"`). Bu kural zorunludur; aksi hâlde `required_permission` ölçümünde GET uçları varsayılan `read`e (auth.py:1242), POST uçları ise `__admin_only__`a (auth.py:1318) düşer. Şef kararı: `POST /api/payments` ile aynı `payments` yetkisi tanımlanmalıdır (`muhasebe` + `yonetici` yazma; `satis`ın okuma/yazma durumu Açık Karar 4'te listelenen karara bağlıdır).
- **Pin Tabanı ve CS1 Deltaları (#116 E4a Sonrası develop Taban Alınarak):**
  - **Rota Sayısı & Yol Sayısı:** 400/308 → 405/312 (+5 operasyon, +4 yol / path count pini).
  - **GET İzin Envanteri (`test_route_get_permission_inventory.py`):** 190 → 192 (+2 GET ucu, yetki: `"payments"` — `auth.py` açık önek kuralı ile).
  - **`TENANT_TABLES`:** 121 → 122 (`cek_senetler`).
  - **PG İkizleri (`backend/tests/pins/pg_twins.txt`):** 125 → 126 (`test_cs1_cek_senet_postgresql.py` eklenir).
  - **`ROUTE_REASON_GROUPS` (`test_route_security_contracts.py`):** Operational read grubuna 2 GET ucu eklenir.
  - **`numeric_manifest.py`:** `cek_senetler: ("tutar",)` eklenir.

### PR 2: CS2 — Cari, Ekstre, Tahsis ve Gecikme Zammı Entegrasyonu (Backend)
- **Kapsam:**
  - `finance.py`: `POST /api/payments` çek ödemesinde otomatik `cek_senetler` oluşturma köprüsü.
  - `statement.py`: Çeklerin ekstrede durum bazlı ayrıştırılması ve yürüyen bakiye uyumu.
  - Karşılıksız çek tetikleyicisi: `receivable_charge_documents` ile borç dekontu üretimi ve gecikme faizi timeline'ına bağlama.
  - `reports.py` & `dashboard.py`: Yaşlandırma raporunda ve panoda çek risk kolonları.
  - `kiraci_geri_yukleme.py` & `kiraci_disa_aktarim.py`: 5.1c dışa aktarım/geri yükleme grafiği eşlemesi.
- **Öngörülen Pin Deltaları:**
  - Yeni uç yok (0 rota). `statement.py` ve `finance.py` AST parmak izi güncellemeleri.

### PR 3: CS3 — Portföy Listesi, Vade Takvimi ve Bordro Yönetimi (Frontend)
- **Kapsam:**
  - `frontend/src/pages/CekSenetPortfoyu.tsx`: Çek/senet portföy tablosu, filtreler (vade, durum, cari, banka).
  - Vade takvimi bileşeni (yaklaşan çekler, bugün vadeli olanlar uyarısı).
  - İşlem pencereleri: Tahsile verme, bankadan tahsil kaydı (hesap seçimiyle), ciro etme (tedarikçi seçimiyle), karşılıksız işaretleme modalları (`prompt()` kaldırılır).
  - `frontend/src/navigation.tsx`: `/cek-senet-portfoyu` menü girdisi (`NAV_LABELS.cheques`, yetki: `payments`).
- **Öngörülen Pin Deltaları:**
  - `navigation.consistency.test.ts`: Rota listesi pini +1 (`/cek-senet-portfoyu`).

---

## 6. Berkay İçin Açık Kararlar

1. **Muhasebe Kuralı (Alındığında vs Tahsil Edildiğinde Cari Düşüşü):**
   - *Seçenek A (Önerilen):* Alındığında cari düşer (`payments` yazılır); karşılıksız çıkarsa otomatik borç dekontu açılarak cariye borç iade edilir. (Türkiye piyasa standardı).
   - *Seçenek B:* Yalnızca tahsil edildiğinde cari düşer. Portföydeyken bakiye değişmez.
2. **Eski `financial_instruments` Tablosu:**
   - *Karar:* `0085` göçü yalnızca yeni `cek_senetler` tablosunu kurar ve eski tabloya dokunmaz (creation only). Eski ilkel tablonun emekliye ayrılması (retirement) sonraki bağımsız bir hijyen maddesi olarak yürütülecektir.
3. **Ciro İşleminin Tedarikçi Bakiyesine Etkisi:**
   - Bir müşteri çeki tedarikçiye ciro edildiğinde (`ciro_edildi`), tedarikçiye otomatik bir `payment` (`direction='out'`, `payment_method='check'`) açılarak tedarikçi borcu anında düşmeli midir?
4. **Saha Satış Yetkisi (`satis` Rolü):**
   - Şef kararıyla `/api/cek-senetler` için `payments` yetkisi tanımlanmıştır (`muhasebe` + `yonetici` yazma yetkisine sahiptir). Satış temsilcisi sahada çek teslim aldığında çek kaydı açabilmeli mi (`satis` rolü yazma yetkisi alacak mı), yoksa `satis` rolü yalnızca okuma (`read`) ile mi sınırlandırılmalıdır?
5. **Karşılıksız Çek Masraf ve Ceza Politikası:**
   - Karşılıksız çıkan çekte banka masrafı veya yasal tazminat (%10 çek tazminatı) otomatik olarak borç dekontuna eklenmeli midir?

---

## 7. En Önemli 3 Bulgu

1. **Ödemelerde Vade ve Portföy Bağı Tamamen Eksik (`core_schema.py:256`, `finance.py:273`):**
   Mevcut sistemde `payments` tablosunda hiçbir vade (`due_date`) alanı bulunmamakta, çek yöntemiyle ödeme girildiğinde tutar sanki anında kasaya girmiş gibi cariyi düşürmektedir. `financial_instruments` tablosu ise `payments`tan tamamen kopuk olup izole bir ada halinde durmaktadır.
2. **Finans Hesabı Uyuşmazlığı ve Güvenlik Boşluğu (`finance_engine.py:64,81`):**
   Finans motorunda çek/senet için bir hesap tipi (`ACCOUNT_TYPES`) veya eşlemesi yoktur. Kullanıcı çek tahsilatında bir banka/kasa seçerse backend 400 hatası üretmekte; ön yüz ise bu kısıtı atlamak için ilkel bir browser `prompt()` penceresi kullanmaktadır.
3. **Sıfır Bakiye Çatışması İle 3 PR'lık Güvenli Yol Haritası:**
   Altyapı (5.1c geri yükleme, TENANT_TABLES bekçisi, dinamik SQL nöbetçisi) yeni bir kiracı tablosunu (`cek_senetler`) sıfır regresyon riskiyle karşılayacak olgunluktadır. Model (CS1), Cari/Ekstre (CS2) ve Arayüz (CS3) ayrımı stacked-PR mimarisine tam oturmaktadır.
