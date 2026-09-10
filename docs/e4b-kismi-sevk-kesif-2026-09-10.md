# E4b — e-İrsaliye Kısmi Sevk ve ReceiptAdvice (İrsaliye Yanıtı) Keşif Raporu

Tarih: 2026-09-10. Taban commit: `38016f4` (origin/develop). PR #116 head: `407d855` (`feat/e4a-eirsaliye-duz-sevk`).
Kapsam: Salt-okunur keşif ve tasarım dokümanı. Uygulama kodu veya göç içermez.
Önceki keşif: `docs/e4-eirsaliye-kesif-2026-09-09.md` (WSDL 12 operasyon, 100/101 kodları, XSD incelemesi).

---

## 1. Veri Modeli Deltası — Kısmi Sevk (Option A vs Option B)

### Mevcut Durum (E4a / PR #116)
E4a'da bir faturaya en fazla bir e-İrsaliye açılabilir:
- `backend/alembic/versions/20260913_0083_eirsaliye.py:158`: `sa.UniqueConstraint("company_id", "invoice_id", name="uq_despatch_notes_company_invoice")`.
- `backend/app/routers/despatch_notes.py:137-142`: Çakışma SQLite ve PG için `FATURA_TEKIL_IMZALARI` ile yakalanıp 409 `IRSALIYE_ZATEN_VAR` döner.
- `backend/alembic/versions/20260913_0083_eirsaliye.py:159-161`: `sa.UniqueConstraint("company_id", "id", name="uq_despatch_notes_company_id")` bileşik anahtarı, gelecekteki `despatch_lines` tablosunun bağlanabilmesi için E4a tarafından bilerek eklendi.

### Seçenekler ve Değerlendirme
- **Seçenek (a) — `despatch_notes` üzerindeki tekilliği kaldırıp `despatch_lines` eklemek:**
  E4a kodunda ve göçünde bu yol açıkça dikişlenmiştir (`despatch_notes.py:46-52`, `20260913_0083_eirsaliye.py:156-160`). Faturaya bağlı $N$ adet irsaliye oluşturulabilir. Her irsaliye kendi sevk kalemlerini (`despatch_lines`) taşır.
- **Seçenek (b) — Faz 11-5 `sales_orders` / `delivery_notes` üzerinden "sevk emri → n irsaliye" modellemek:**
  - Ölçüm: `sales_orders` (`backend/app/workflow.py:29-52`) ve `delivery_notes` (`backend/app/workflow.py:84-100`) tabloları depoda mevcuttur.
  - Ancak `backend/app/routers/workflow.py:876-881, 933-938` incelendiğinde sipariş dönüşümü katı 1:1 kurgulanmıştır (`converted_type`, `converted_id`); kısmi sevke izin vermez (zaten dönüştürülmüşse 400 döner).
  - Ayrıca PR #115 geri yükleme motorunda (`backend/app/kiraci_geri_yukleme.py:583-584` ve `docs/durum/pr-0115.md:1`), `delivery_notes` ile `sales_orders` arasında 286 yumuşak bağımlılık kenarının döngü kurduğu (`delivery_notes ↔ sales_orders`) ve `CircularDependencyError` ürettiği ölçülmüştür.
  - Dahası, `delivery_notes` dahili depo irsaliyesidir; mali e-Belge nitelikleri (ETTN, UBL XML, sağlayıcı durumu) taşımaz.

### Öneri ve Göç Kolon Listesi (Göç 0086)
**Seçenek (a)** kesinlikle önerilir. Şef tarafından 0085 çek/senet sonrası atanacak numara: **`202609xx_0086_despatch_lines.py`**.
1. `op.drop_constraint("uq_despatch_notes_company_invoice", "despatch_notes", type_="unique")`
2. `despatch_lines` tablosu:
   - `id`: `Integer`, Primary Key, autoincrement
   - `company_id`: `Integer`, NOT NULL, FK `companies.id`
   - `despatch_id`: `Integer`, NOT NULL
   - `invoice_item_id`: `Integer`, NOT NULL, FK `invoice_items.id`
   - `line_no`: `Integer`, NOT NULL (irsaliye içi 1..N sıra)
   - `product_id`: `Integer`, NULL (ürün kartı varsa)
   - `item_name`: `Text`, NOT NULL (sevk anındaki ürün/hizmet adı)
   - `quantity`: `Numeric(18, 4)`, NOT NULL (sevk miktarı)
   - `unit_code`: `String(16)`, NOT NULL, server_default='C62'
   - `created_at`: `DateTime(timezone=True)`, NOT NULL
   - `updated_at`: `DateTime(timezone=True)`, NOT NULL
   - Kısıtlar:
     - `ForeignKeyConstraint(["company_id", "despatch_id"], ["despatch_notes.company_id", "despatch_notes.id"], name="fk_despatch_lines_despatch_same_company")`
     - `UniqueConstraint("company_id", "id", name="uq_despatch_lines_company_id")`
     - `UniqueConstraint("despatch_id", "line_no", name="uq_despatch_lines_despatch_line")`
     - `CheckConstraint("quantity > 0", name="ck_despatch_lines_quantity_positive")`
   - İndeks: `ix_despatch_lines_company_despatch` (`company_id`, `despatch_id`)

---

## 2. Miktar Mutabakatı ve Hizmet Kalemleri

### Miktar Mutabakat Kuralları
1. **Fatura Kalemi Tavanı:** Faturadan sevk yapıldığında fatura önceden kesilmiştir (`OrderReference` faturaya işaret eder). Bir fatura kalemine (`invoice_item_id`) ait sevk edilen toplam miktar, faturalanan miktarı aşamaz:
   $$\sum_{d \in \text{despatches}} \text{quantity}_{d, i} \le \text{invoice\_items}[i].\text{quantity}$$
2. **Kalan Miktar:** $\text{kalan}_i = \text{invoice\_items}[i].\text{quantity} - \sum \text{quantity}_{\text{onceki}}$. Kısmi sevk isteğindeki miktar $\le \text{kalan}_i$ olmalıdır; aksi halde HTTP 422 döner.
3. **Pozitif Miktar:** Her sevk satırında $\text{quantity} > 0$ zorunludur (CHECK kısıtı).

### Hizmet Kalemleri (`Servis İşçiliği`) Analizi
- **E4a Çalışma Zamanı Gözlemi:** `backend/app/invoice_service.py:57-63` içinde iş emri faturalanırken `item_type='LABOR'` ("Servis İşçiliği") ve `item_type='PART'` kalemleri `invoice_items` tablosuna yazılır. E4a `routers/despatch_notes.py:256-267` ise fatura kalemlerini filtrelemeden `SELECT id, description, quantity FROM invoice_items` ile okur; sonuçta "Servis İşçiliği" kalemi `cac:DespatchLine` olarak `DeliveredQuantity=0`, `unitCode=C62` ile UBL'e basılmıştır.
- **Mevzuat (VUK 230/5):** Sevk irsaliyesi münhasıran "malın nakli" için düzenlenir. Hizmet ifaları fiziki sevk ve nakliyeye konu olamayacağından irsaliyede yer alamaz.
- **GİB / UBL-TR Kuralları:** GİB e-İrsaliye Uygulama Kılavuzu §9-10 mal sevkiyatını tanımlar. GİB Schematron'unda `DeliveredQuantity=0` değerinin doğrudan bir Schematron hata koduyla (örn. 10003) reddedilip reddedilmediği **DOĞRULANMADI** (güncel GİB Schematron paketi indirilemedi).
- **Hüküm ve Tasarım:** Hizmet kalemleri (`invoice_items.item_type == 'LABOR'` veya fiziki olmayan hizmetler) e-İrsaliye UBL'ine ve `despatch_lines` tablosuna **KESİNLİKLE DÂHİL EDİLMEMELİDİR**. Yalnızca `item_type == 'PART'` (fiziki mallar) sevk satırı olabilir. Yalnızca hizmet içeren bir faturaya e-İrsaliye açılmak istendiğinde sistem 422 ("Faturada sevk edilecek mal kalemi bulunmuyor") ile durmalıdır.

---

## 3. ReceiptAdvice (Gelen İrsaliye Yanıtı) ve Durum Makinesi

### İzibiz Operasyon Haritası (`docs/e4-eirsaliye-kesif-2026-09-09.md` §2.2)
12 WSDL operasyonu içinden irsaliye yanıtı (ReceiptAdvice) ile ilgili olanlar:
1. `GetDespatchAdviceStatus` (Request: `UUID 1+`, Response: `DESPATCHADVICE_STATUS 1+`):
   - Sağlayıcı durumunun yanında ticari yanıt özetini (`RESPONSE_CODE`: KABUL, RED, KISMI_KABUL) döner (`docs/e4-eirsaliye-kesif-2026-09-09.md:125,158`).
2. `GetReceiptAdvice` (Request: `SEARCH_KEY 1`, Response: `RECEIPTADVICE 0+`):
   - Alıcının gönderdiği UBL `ReceiptAdvice` belgesini (kabul/ret miktarları, ret gerekçeleri) XML olarak getirir. Arama anahtarı `SEARCH_KEY/UUID` (irsaliye ETTN'i veya yanıt UUID'si) ve `DIRECTION=IN` olmalıdır.
3. `SendDespatchResponse` / `SendReceiptAdvice`: Alıcı rolündeyken yanıt gönderme operasyonlarıdır; satıcı rolünde gelen yanıtı okumak için kullanılmazlar.

### Durum Makinesi Genişlemesi
E4a'da durum makinesi `backend/app/einvoice/edespatch.py:73-82` içinde `TERMINAL = frozenset({DELIVERED})` olarak tanımlanmıştı ve `REJECTED` bilerek dışarıda bırakılmıştı. E4b ile ticari yanıt durumları eklenir:
- Durumlar: `NONE`, `FAILED`, `UNKNOWN`, `QUEUED`, `PROCESSING`, `SIGNED`, `SENT`, `DELIVERED`, `ACCEPTED`, `PARTIALLY_ACCEPTED`, `REJECTED`.
- Rank Tablosu:
  - `NONE: 0`, `FAILED: 0`, `UNKNOWN: 1`, `QUEUED: 2`, `PROCESSING: 3`, `SIGNED: 4`, `SENT: 5`, `DELIVERED: 6`
  - `ACCEPTED: 7`, `PARTIALLY_ACCEPTED: 7`, `REJECTED: 7` (Terminal durumlar)
- Terminal Kümesi: `TERMINAL = frozenset({ACCEPTED, PARTIALLY_ACCEPTED, REJECTED})`.
- `DELIVERED` geçişi: `DELIVERED` artık terminal değildir; alıcıdan `ReceiptAdvice` geldiğinde `ACCEPTED`, `PARTIALLY_ACCEPTED` veya `REJECTED` durumuna ilerler. Yanıt gelmezse 7 gün sonra hukuken zımni kabul sayılır ancak veritabanında `DELIVERED` olarak kalır.
- `UNKNOWN` kuralı korunur: `UNKNOWN` canlı bir `DELIVERED` veya `ACCEPTED` durumunu geriye çekemez.

### Yanıt Depolama ve Idempotency
- `despatch_notes` üzerinde özet kolonlar: `edespatch_status`, `response_status` (KABUL/KISMI_KABUL/RED), `response_received_at`.
- Ayrı yanıt tabloları (`despatch_responses` ve `despatch_response_lines`):
  Kısmi kabulde her irsaliye satırının kabul edilen miktarı (`ReceivedQuantity`), reddedilen miktarı (`RejectedQuantity`) ve ret sebebi (`RejectReason`) UBL `cac:ReceiptLine` içinde gelir. Bunlar üst tabloya sığmaz.
  - `despatch_responses`: `id`, `company_id`, `despatch_id`, `response_uuid` (String(36)), `response_number` (String(16)), `response_type`, `issue_date`, `notes`, `created_at`.
  - `despatch_response_lines`: `id`, `company_id`, `response_id`, `despatch_line_id`, `received_quantity`, `rejected_quantity`, `reject_reason`.
- **Idempotency Anahtarı:** `UniqueConstraint("company_id", "response_uuid", name="uq_despatch_responses_uuid")`. Gelen yanıt ETTN'i tektir. Tekrarlanan sync çağrılarında aynı yanıt mükerrer eklenemez (ON CONFLICT DO NOTHING / UPDATE).

---

## 4. Alış Tarafı (Biz Alıcıyız) — `purchases` ve Kapsam Ayrımı

### Mevcut Durum Ölçümü
- `backend/app/core_schema.py:180-205` (`purchases`) ve `209-250` (`purchase_items`) incelendi.
- `purchases` ve `purchase_items` tablolarında herhangi bir irsaliye referansı (`despatch_id`, `despatch_uuid`, `despatch_number`) **YOKTUR**.
- Satın alma tarafı fatura/makbuz odaklıdır; e-İrsaliye gelen kutusu (inbound despatch) henüz modellenmemiştir.

### Kapsam Önerisi
"Gelen e-İrsaliye → Alış İrsaliyesi / Kabul Yanıtı (ReceiptAdvice oluşturup gönderme)" akışı:
1. İzibiz `GetDespatchAdvice` (DIRECTION=IN) ile gelen irsaliyelerin taranmasını,
2. Tedarikçi eşleştirmesini ve stok girişini (`product_lots`),
3. `SendDespatchResponse` veya `SendReceiptAdvice` ile UBL-TR yanıtı imzalanıp gönderilmesini gerektirir.
**Öneri:** Bu akış E4b kapsamına **ALINMAMALIDIR**. E4b, satış faturalarından yapılan kısmi sevkleri ve müşteriden gelen irsaliye yanıtlarının işlenmesini tamamlamalıdır. Alış irsaliyeleri ve giden yanıt akışı müstakil bir **E4c** iş paketi olarak planlanmalıdır.

---

## 5. Sandbox ve CI Doğrulama Planı

### CI Ortamı (Kimliksiz / Mock / Sözleşme — 100% Deterministik)
CI ortamında dış ağa çıkılmaz; şu çağrı ve kontroller doğrulanır:
1. **UBL Builder & Kısmi Sevk:** `edespatch.build_despatch_xml` kısmi kalemlerle, `item_type=='LABOR'` hariç tutularak, Decimal miktarlarla UBL 2.1 şemasına uygun XML üretir.
2. **Fail-Closed Doğrulama:** Yapılandırma yokken (`is_configured=False`) `submit_despatch` `FAILED`, `despatch_status` `UNKNOWN` döner; uç 503 `EBELGE_YAPILANDIRILMAMIS` üretir.
3. **Mock SOAP Ayrıştırma:**
   - `GetDespatchAdviceStatusResponse` içindeki `RESPONSE_CODE` (`KABUL`, `RED`, `KISMI_KABUL`) ayrıştırılması.
   - `GetReceiptAdviceResponse` XML gövdesinden `ReceiptLine` (kabul/ret miktarları) ayrıştırılması.
4. **Durum Makinesi:** `durumu_ilerlet` ve `kodu_coz` fonksiyonlarının yeni yanıt kodlarıyla ileri yönlü işletilmesi.
5. **Veritabanı Göçü ve Kısıtlar:** SQLite ve PostgreSQL ikizinde `despatch_lines` FK, CHECK (`quantity > 0`) ve UNIQUE testleri.

### Gerçek Sandbox Ortamı (Yalnız Ana PC, `backend/.env.izibiz.local`, Gizli Bilgi Asla Yazdırılmaz)
Betik: `backend/sandbox/izibiz_edespatch_smoke.py` (E4b operasyonlarıyla genişletilmiş).
1. `Login`: Test oturumu açma (`SESSION_ID` alma).
2. `GetDespatchAdviceStatus`: E4a'da kabul edilen `IRS2026993046536` irsaliyesinin ham yanıtını sorgulama; `RESPONSE_CODE` etiketinin XML içindeki tam yolunu ve adını doğrulama.
3. `GetReceiptAdvice`: `SEARCH_KEY` (kendi VKN'miz ve `DIRECTION=IN`) ile gelen yanıt arama sözleşmesini doğrulama.
4. `SendDespatchResponse` / `SendReceiptAdvice`: Sandbox hesabımız hem satıcı hem alıcı olduğundan, kendi irsaliyemize `STATUS=KABUL` yanıtı gönderilip gönderilemediğini sınama.
- Beklenen Kodlar: Başarı durumunda `RETURN_CODE=0`, `STATUS_CODE=100/101/133`; bulunamadığında `ERROR_CODE=10008` ("belirtilen kritere uygun kayıt bulunamamıştır").

---

## 6. PR Ayrımı ve Pin Deltaları (Develop Sonrası #116 Tabanlı)

Taban: PR #116 sonrası develop: **Rota 400 op / 308 path**, **GET 190**, **TENANT_TABLES 121**, **pg_twins 125**.

### PR E4b-1: Model + Göç + Kısmi Sevk Builder
- **Kapsam:** Göç 0086 (`uq_despatch_notes_company_invoice` drop + `despatch_lines` tablosu), `document_sequences` entegrasyonu, `invoice_items.item_type != 'LABOR'` filtrelemesi, UBL kısmi sevk builder'ı, `POST /api/despatch-notes` kısmi miktar desteği, `GET /api/invoices/{id}/despatchable-items` ucu.
- **Pin Deltası:**
  - Rota İşlemleri: 400 → **401** (+1 GET)
  - Rota Yolları: 308 → **309** (+1 path: `/api/invoices/{invoice_id}/despatchable-items`)
  - GET İzin Envanteri: 190 → **191** (izin: `sales`)
  - `TENANT_TABLES`: 121 → **122** (`despatch_lines` eklendi)
  - `pg_twins.txt`: 125 → **126** (`test_e4b1_partial_despatch_postgresql.py`)
- **Testler:** Miktar mutabakatı, aşan miktar reddi (422), hizmet satırı dışlama, PG ikizi.

### PR E4b-2: ReceiptAdvice Inbound + Durum Makinesi + Sync
- **Kapsam:** Göç 0087 (`despatch_responses`, `despatch_response_lines` ve durum CHECK kısıtı genişletmesi), `edespatch.py` durum makinesi (`ACCEPTED`, `PARTIALLY_ACCEPTED`, `REJECTED`), `endpoints.py` (`GetReceiptAdvice`), `provider.py` (`get_receipt_advice`), `despatch_notes.py` sync güncellemesi, `GET /api/despatch-notes/{id}/response` ucu.
- **Pin Deltası:**
  - Rota İşlemleri: 401 → **402** (+1 GET)
  - Rota Yolları: 309 → **310** (+1 path: `/api/despatch-notes/{despatch_id}/response`)
  - GET İzin Envanteri: 191 → **192** (izin: `sales`)
  - `TENANT_TABLES`: 122 → **124** (`despatch_responses`, `despatch_response_lines`)
  - `pg_twins.txt`: 126 → **127** (`test_e4b2_receipt_advice_postgresql.py`)
- **Testler:** Gelen yanıt XML ayrıştırma, kısmi kabul ret kalemleri, idempotent sync, PG ikizi.

### PR E4b-3: Frontend (Kısmi Sevk Dialogu ve Yanıt Görünümü)
- **Kapsam:** `DespatchNotePanel.tsx` ve `InvoiceDetail.tsx` arayüz geliştirmeleri:
  - Faturadan kısmi sevk modalı (kalem listesi, sevk edilen / kalan miktar girişi).
  - İrsaliye listesinde ve detayında yanıt rozeti (Kabul, Kısmi Kabul, Ret) ve ret gerekçeleri akordiyonu.
  - OpenAPI ve TypeScript tiplerinin güncellenmesi (`npm run types:gen`).
- **Pin Deltası:** Arka uç pinleri değişmez (402 op, 310 path, 192 GET, 124 tablo, 127 pg_twin sabit). Frontend testleri güncellenir (`DespatchNotePanel.test.tsx`).

---

## 7. Açık Kararlar (Berkay)

1. **Göç Numaralandırması:** Çek/senet keşfi (PR #117) `0085` revizyonunu hedeflemektedir. E4b-1 için `0086` (`despatch_lines`), E4b-2 için `0087` (`despatch_responses`) sırası uygun mudur?
2. **Hizmet Faturası Kısıtı:** Faturada yalnızca hizmet kalemi (`item_type == 'LABOR'`) varsa kullanıcıya irsaliye butonu tamamen gizlensin mi, yoksa tıklandığında bilgilendirici 422 uyarısı mı gösterilsin?
3. **Varsayılan Sevk Miktarı:** Kısmi sevk diyaloğu açıldığında satır miktarları varsayılan olarak "kalan miktarın tamamı" (100%) şeklinde mi dolsun?
4. **Belge Numarası Sayacı:** e-İrsaliye serisi için `document_sequences` tablosunda `sequence_key='despatch_notes:IRS'` anahtarı kullanılarak yıl bazlı artış onaylanıyor mu?
5. **7 Günlük Zımni Kabul Otomasyonu:** 7 gün boyunca alıcıdan yanıt gelmeyen `DELIVERED` irsaliyeler otomatik olarak arka plan işiyle `ACCEPTED` durumuna çekilsin mi, yoksa operatör `sync` yapana kadar `DELIVERED` olarak mı kalsın?

---

## Kaynaklar ve Kanıtlar
- `docs/e4-eirsaliye-kesif-2026-09-09.md` (İzibiz WSDL 12 operasyon, UBL-TR, mevzuat eşikleri).
- `backend/alembic/versions/20260913_0083_eirsaliye.py:156-161` (E4a tekil kısıtı ve firma kimliği).
- `backend/app/routers/despatch_notes.py:46-52, 137-142` (E4b dikişleri ve tekil kısıt yakalama).
- `backend/app/workflow.py:29-52, 84-100` (`sales_orders`, `delivery_notes` mevcut şema).
- `backend/app/routers/workflow.py:876-881, 933-938` (`workflow` tekil dönüşüm kısıtları).
- `backend/app/kiraci_geri_yukleme.py:583-584` (`delivery_notes ↔ sales_orders` döngü kanıtı).
- `backend/app/core_schema.py:180-250` (`purchases` ve `purchase_items` mevcut şema).
- `backend/app/invoice_service.py:57-63` (`LABOR` vs `PART` fatura kalemi üretimi).
