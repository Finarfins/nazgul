# E4b — e-İrsaliye Kısmi Sevk ve ReceiptAdvice (İrsaliye Yanıtı) Keşif Raporu

Tarih: 2026-09-10 (Düzeltme: 2026-09-11). Taban commit: `2e35393` (origin/develop, PR #122 dâhil).
Kapsam: Salt-okunur keşif ve tasarım dokümanı. Uygulama kodu veya göç içermez.
Önceki keşif: `docs/e4-eirsaliye-kesif-2026-09-09.md` (WSDL 12 operasyon, 100/101 kodları, XSD incelemesi).

---

## 1. Veri Modeli Deltası — Kısmi Sevk (Option A vs Option B)

### Mevcut Durum (E4a / develop)
E4a'da bir faturaya en fazla bir e-İrsaliye açılabilir:
- `backend/alembic/versions/20260913_0083_eirsaliye.py:268` (kısıt adı satır 194'te `FATURA_TEKIL = "uq_despatch_notes_company_invoice"`): `sa.UniqueConstraint("company_id", "invoice_id", name=FATURA_TEKIL)`.
- `backend/app/routers/despatch_notes.py:139-142`: Çakışma SQLite ve PostgreSQL için `FATURA_TEKIL_IMZALARI` tuple'ı ile yakalanıp `IRSALIYE_ZATEN_VAR` (:125) mesajıyla HTTP 409 (:446-447) fırlatılır.
- `backend/alembic/versions/20260913_0083_eirsaliye.py:272` (kısıt adı satır 193'te `FIRMA_KIMLIK = "uq_despatch_notes_company_id"`): `sa.UniqueConstraint("company_id", "id", name=FIRMA_KIMLIK)` bileşik anahtarı, gelecekteki `despatch_lines` tablosunun bağlanabilmesi için E4a tarafından bilerek eklendi.

### Seçenekler ve Değerlendirme
- **Seçenek (a) — `despatch_notes` üzerindeki tekilliği kaldırıp `despatch_lines` eklemek:**
  E4a kodunda ve göçünde bu yol açıkça dikişlenmiştir (`backend/app/routers/despatch_notes.py:55-62`, `backend/alembic/versions/20260913_0083_eirsaliye.py:268,272`). Faturaya bağlı $N$ adet irsaliye oluşturulabilir. Her irsaliye kendi sevk kalemlerini (`despatch_lines`) taşır.
- **Seçenek (b) — Faz 11-5 `sales_orders` / `delivery_notes` üzerinden "sevk emri → n irsaliye" modellemek:**
  - Ölçüm: `sales_orders` (`backend/app/workflow.py:29-52`) ve `delivery_notes` (`backend/app/workflow.py:84-100`) tabloları depoda mevcuttur.
  - Ancak `backend/app/routers/workflow.py:876-882, 933-939` incelendiğinde sipariş dönüşümü katı 1:1 kurgulanmıştır (`converted_type`, `converted_id`); kısmi sevke izin vermez: aynı türe tekrar dönüştürmede `already_converted: True` ile idempotent döner (`:878, :935`), farklı türe dönüştürmede ise HTTP 400 değil HTTP 409 fırlatır (`:880, :937`).
  - Ayrıca PR #115 geri yükleme motorunda (`backend/app/kiraci_geri_yukleme.py:588-598` ve `docs/durum/pr-0115.md:1`), kiracı grafında toplam 286 yumuşak bağımlılık kenarının döngü kurduğu (docstring'de `delivery_notes ↔ sales_orders` çifti yalnızca bir örnek, "… gibi" olarak anılmıştır) ve `CircularDependencyError` ürettiği ölçülmüştür.
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
   - `item_name`: `Text`, NOT NULL (sevk anındaki ürün adı)
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
3. **5.1c Kiracı Geri Yükleme Sınıflandırıcısı (`backend/app/kiraci_geri_yukleme.py`):**
   - `DOGRUDAN_HEDEFLER` sözlüğüne `("despatch_lines", "product_id"): "products"` yumuşak hedefi eklenir.
4. **Sayısal Manifesto (`backend/app/numeric_manifest.py`):**
   - `QUANTITY_COLUMNS` sözlüğüne `"despatch_lines": ("quantity",)` eklenir (18, 4 hassasiyet sözleşmesi).

---

## 2. Miktar Mutabakatı ve Hizmet Kalemleri

### Miktar Mutabakat Kuralları
1. **Fatura Kalemi Tavanı:** Faturadan sevk yapıldığında fatura önceden kesilmiştir (`OrderReference` faturaya işaret eder). Bir fatura kalemine (`invoice_item_id`) ait sevk edilen toplam miktar, faturalanan miktarı aşamaz:
   $$\sum_{d \in \text{despatches}} \text{quantity}_{d, i} \le \text{invoice\_items}[i].\text{quantity}$$
2. **Kalan Miktar:** $\text{kalan}_i = \text{invoice\_items}[i].\text{quantity} - \sum \text{quantity}_{\text{onceki}}$. Kısmi sevk isteğindeki miktar $\le \text{kalan}_i$ olmalıdır; aksi halde HTTP 422 döner.
3. **Pozitif Miktar:** Her sevk satırında $\text{quantity} > 0$ zorunludur (CHECK kısıtı).

### Hizmet Kalemleri (`Servis İşçiliği`) Analizi
- **Statik Durum ve E4a Lens Gözlemi:** `backend/app/invoice_service.py:57` içinde iş emri faturalanırken `item_type='LABOR'` ("Servis İşçiliği") kalemi `entry["hours"]` saatiyle yazılır. E4a UBL üreticisi `backend/app/einvoice/edespatch.py:771-772` satır miktarını (`satir['quantity']`) değiştirmeden `<cbc:DeliveredQuantity>` içine yazar. E4a lens raporundaki "DeliveredQuantity=0" iddiası çalışma zamanı gözlemidir (0 saatlik işçilik testi senaryosu) ve statik olarak **DOĞRULANMADI**; ancak filtreleme yapılmadığında işçilik satırının UBL sevk satırına dönüştüğü gerçektir.
- **Mevzuat İddiası (VUK 230/5 "yalnız mal nakli"):** **DOĞRULANMADI** (kaynak metin eklenemedi; sevk irsaliyesinin fiziki mal hareketine özgü olduğu kabulüne dayalı mevzuat yorumudur).
- **GİB / UBL-TR Kuralları:** GİB Schematron'unda `DeliveredQuantity=0` değerinin doğrudan bir Schematron hata koduyla (örn. 10003) reddedilip reddedilmediği **DOĞRULANMADI** (güncel GİB Schematron paketi indirilemedi).
- **Hüküm ve Tasarım:** Hizmet kalemleri (`invoice_items.item_type == 'LABOR'` veya fiziki olmayan hizmetler) e-İrsaliye UBL'ine ve `despatch_lines` tablosuna dâhil edilmemelidir. Yalnızca `item_type == 'PART'` (fiziki mallar) sevk satırı olabilir. Yalnızca hizmet içeren bir faturaya e-İrsaliye açılmak istendiğinde sistem 422 ("Faturada sevk edilecek mal kalemi bulunmuyor") ile durmalıdır.

---

## 3. ReceiptAdvice (Gelen İrsaliye Yanıtı) ve Durum Makinesi

### İzibiz Operasyon Haritası (`docs/e4-eirsaliye-kesif-2026-09-09.md` §2.2–2.3)
12 WSDL operasyonu içinden irsaliye yanıtı (ReceiptAdvice) ile ilgili olanlar:
1. `GetDespatchAdviceStatus` (Request: `UUID 1+`, Response: `DESPATCHADVICE_STATUS 1+`):
   - Sağlayıcı durumunun yanında ticari yanıt özetini döner. `docs/e4-eirsaliye-kesif-2026-09-09.md:61` uyarınca XSD'de `DESPATCH_RESPONSE_STATUS = {KABUL, RED}` tanımlıdır.
   - `RESPONSE_CODE = KISMI_KABUL` değeri **DOĞRULANMADI** (XSD'de kısmi kabul kodu yoktur; request `STATUS` alanı serbest string olsa da standart kod kümesinde doğrulanmamıştır).
2. `GetReceiptAdvice` (Request: `SEARCH_KEY 1`, Response: `RECEIPTADVICE 0+`):
   - Alıcının gönderdiği UBL `ReceiptAdvice` belgesini XML olarak getirir. Kısmi kabul, belge içindeki `cac:ReceiptLine` satırlarında (`ReceivedQuantity`, `RejectedQuantity`, `RejectReason`) ifade edilir.
   - `GetReceiptAdvice` ile satırlı yanıt okuma akışı **DOĞRULANMADI** (sandbox testiyle doğrulanmalıdır).
3. `SendDespatchResponse` / `SendReceiptAdvice`: Alıcı rolündeyken yanıt gönderme operasyonlarıdır; satıcı rolünde gelen yanıtı okumak için kullanılmazlar.

### Durum Makinesi Genişlemesi ve Mevzuat Uyuşmazlığı
E4a'da durum makinesi `backend/app/einvoice/edespatch.py:107` içinde `TERMINAL = frozenset({DELIVERED})` olarak tanımlanmıştı ve `REJECTED` bilerek dışarıda bırakılmıştı (gerekçe `:35-40`).
- **7 Gün Zımni Kabul:** `docs/e4-eirsaliye-kesif-2026-09-09.md:143` (2020 GİB kılavuzu §12, §15.24–25; "güncel sürümle değişmezliği DOĞRULANMADI"). Fiili sevkten itibaren 7 gün içinde yanıt verilmezse tam teslim varsayılır.
- **Tam RED Kısıtı:** 2020 GİB kılavuzuna göre tam RED yalnız sevkten önce, yanlış alıcı/mal halinde geçerlidir; fiili teslimat yapıldıktan sonra tam RED düzenlenemez, mal iadesi iade irsaliyesi gerektirir.
- **`DELIVERED -> REJECTED` Geçişi:** Yukarıdaki mevzuat kısıtı nedeniyle, teslim edilmiş bir irsaliyenin sonradan tam `REJECTED` durumuna geçmesi **DOĞRULANMADI** olarak işaretlenmiştir. Sistemde ticari ret durumu ya sevk öncesi aşamada yakalanmalı ya da satır bazlı kısmi kabul (`PARTIALLY_ACCEPTED`) şeklinde modellenmelidir.
- Durumlar: `NONE`, `FAILED`, `UNKNOWN`, `QUEUED`, `PROCESSING`, `SIGNED`, `SENT`, `DELIVERED`, `ACCEPTED`, `PARTIALLY_ACCEPTED`, `REJECTED` (etiketli).
- Terminal Kümesi: `TERMINAL = frozenset({ACCEPTED, PARTIALLY_ACCEPTED, REJECTED})`.

### Yanıt Depolama ve Idempotency
- `despatch_notes` üzerinde özet kolonlar: `edespatch_status`, `response_status` (KABUL/RED/KISMI_KABUL — KISMI_KABUL DOĞRULANMADI, bkz. §3), `response_received_at`.
- Ayrı yanıt tabloları (`despatch_responses` ve `despatch_response_lines`):
  - `despatch_responses`: `id`, `company_id`, `despatch_id`, `response_uuid` (String(36)), `response_number` (String(16)), `response_type`, `issue_date`, `notes`, `created_at`.
  - `despatch_response_lines`: `id`, `company_id`, `response_id`, `despatch_line_id`, `received_quantity` (`Numeric(18, 4)`), `rejected_quantity` (`Numeric(18, 4)`), `reject_reason`.
- **5.1c Geri Yükleme & Sayısal Manifesto Entegrasyonu:**
  - `backend/app/kiraci_geri_yukleme.py`: FK'sız sütunlar `KULLANICI_SUTUNLARI` / `DOGRUDAN_HEDEFLER` altına işlenir.
  - `backend/app/numeric_manifest.py`: `QUANTITY_COLUMNS` altına `"despatch_response_lines": ("received_quantity", "rejected_quantity")` eklenir.
- **Idempotency Anahtarı:** `UniqueConstraint("company_id", "response_uuid", name="uq_despatch_responses_uuid")`. Gelen yanıt ETTN'i tektir.

---

## 4. Alış Tarafı (Biz Alıcıyız) — `purchases` ve Kapsam Ayrımı

### Mevcut Durum Ölçümü
- `backend/app/core_schema.py:180-250` (`purchases` ve `purchase_items`) incelendi.
- `purchases` ve `purchase_items` tablolarında herhangi bir irsaliye referansı (`despatch_id`, `despatch_uuid`, `despatch_number`) **YOKTUR**.
- Satın alma tarafı fatura/makbuz odaklıdır; e-İrsaliye gelen kutusu (inbound despatch) henüz modellenmemiştir.

### Kapsam Önerisi
"Gelen e-İrsaliye → Alış İrsaliyesi / Kabul Yanıtı (ReceiptAdvice oluşturup gönderme)" akışı müstakil bir **E4c** iş paketi olarak planlanmalıdır; E4b kapsamına dâhil edilmemelidir.

---

## 5. Sandbox ve CI Doğrulama Planı

### CI Ortamı (Kimliksiz / Mock / Sözleşme — 100% Deterministik)
CI ortamında dış ağa çıkılmaz; şu çağrı ve kontroller doğrulanır:
1. **UBL Builder & Kısmi Sevk:** `edespatch.build_despatch_xml` kısmi kalemlerle, `item_type=='LABOR'` hariç tutularak, Decimal miktarlarla UBL 2.1 şemasına uygun XML üretir.
2. **Fail-Closed Doğrulama:** Yapılandırma yokken (`is_configured=False`) `submit_despatch` `FAILED`, `despatch_status` `UNKNOWN` döner; uç 503 `EBELGE_YAPILANDIRILMAMIS` üretir.
3. **Mock SOAP Ayrıştırma (KISMI_KABUL bağımsız — kod DOĞRULANMADI, bkz. §3):**
   - `GetDespatchAdviceStatusResponse` içindeki `RESPONSE_CODE` (`KABUL`, `RED`) ayrıştırılması.
   - `GetReceiptAdviceResponse` XML gövdesinden `ReceiptLine` (`ReceivedQuantity`, `RejectedQuantity`, `RejectReason`) ayrıştırılması (**DOĞRULANMADI** — sandbox teyidi bekliyor).
4. **Durum Makinesi:** `durumu_ilerlet` ve `kodu_coz` fonksiyonlarının yeni yanıt kodlarıyla ileri yönlü işletilmesi.
5. **Veritabanı Göçü ve Kısıtlar:** SQLite ve PostgreSQL ikizinde `despatch_lines` FK, CHECK (`quantity > 0`) ve UNIQUE testleri.

### Gerçek Sandbox Ortamı (Yalnız Ana PC, `backend/.env.izibiz.local`, Gizli Bilgi Asla Yazdırılmaz)
Betik: `backend/sandbox/izibiz_edespatch_smoke.py` (E4b operasyonlarıyla genişletilmiş).
1. `Login`: Test oturumu açma (`SESSION_ID` alma).
2. `GetDespatchAdviceStatus`: E4a'da kabul edilen `IRS2026993046536` irsaliyesinin ham yanıtını sorgulama; `RESPONSE_CODE` etiketinin XML içindeki tam yolunu ve adını doğrulama.
3. `GetReceiptAdvice`: `SEARCH_KEY` (kendi VKN'miz ve `DIRECTION=IN`) ile gelen yanıt arama sözleşmesini doğrulama (**DOĞRULANMADI**).
4. `SendDespatchResponse` / `SendReceiptAdvice`: Kendi irsaliyemize `STATUS=KABUL` yanıtı gönderilip gönderilemediğini sınama.

---

## 6. PR Ayrımı ve Pin Deltaları (Develop `2e35393` Tabanlı)

Taban (origin/develop `2e35393`): **Rota 407 op / 315 path**, **GET 197**, **TENANT_TABLES 121**, **pg_twins 128** (test_pp1_platform_paneli_postgresql.py).

### PR E4b-1: Model + Göç + Kısmi Sevk Builder + Belge Sayacı Deltası
- **Kapsam:**
  - Göç 0086 (`uq_despatch_notes_company_invoice` drop + `despatch_lines` tablosu).
  - `backend/app/document_engine.py` Deltası: `DOCUMENT_TABLES` kümesine (`:27-38`) `despatch_notes` eklenmesi (mevcutta yoktur ve `:114-115` ValueError fırlatır); `document_engine.py:189` `PREFIX-000001` formatı yerine GİB 16-haneli (`^[A-Z]{3}[0-9]{13}$`) standardına uygun yıl destekli sayaç (`edespatch.belge_numarasi_uret` entegrasyonu).
  - `backend/app/kiraci_geri_yukleme.py`: `DOGRUDAN_HEDEFLER` altına `("despatch_lines", "product_id"): "products"`.
  - `backend/app/numeric_manifest.py`: `QUANTITY_COLUMNS` altına `"despatch_lines": ("quantity",)`.
  - `invoice_items.item_type != 'LABOR'` filtrelemesi, UBL kısmi sevk builder'ı, `POST /api/despatch-notes` kısmi miktar desteği, `GET /api/invoices/{id}/despatchable-items` ucu.
- **Pin Deltası:**
  - Rota İşlemleri: 407 → **408** (+1 GET)
  - Rota Yolları: 315 → **316** (+1 path: `/api/invoices/{invoice_id}/despatchable-items`)
  - GET İzin Envanteri: 197 → **198** (izin: `sales`)
  - `TENANT_TABLES`: 121 → **122** (`despatch_lines` eklendi)
  - `pg_twins.txt`: 128 → **129** (`test_e4b1_partial_despatch_postgresql.py`)
- **Testler:** Miktar mutabakatı, aşan miktar reddi (422), hizmet satırı dışlama, PG ikizi.

### PR E4b-2: ReceiptAdvice Inbound + Durum Makinesi + Sync
- **Kapsam:**
  - Göç 0087 (`despatch_responses`, `despatch_response_lines` ve durum CHECK kısıtı genişletmesi).
  - `backend/app/numeric_manifest.py`: `QUANTITY_COLUMNS` altına `"despatch_response_lines": ("received_quantity", "rejected_quantity")`.
  - `backend/app/kiraci_geri_yukleme.py`: FK'sız kullanıcı/ilişki sütunlarının sınıflandırılması.
  - `edespatch.py` durum makinesi (`ACCEPTED`, `PARTIALLY_ACCEPTED`, `REJECTED`), `endpoints.py` (`GetReceiptAdvice`), `provider.py` (`get_receipt_advice`), `despatch_notes.py` sync güncellemesi, `GET /api/despatch-notes/{id}/response` ucu.
- **Pin Deltası:**
  - Rota İşlemleri: 408 → **409** (+1 GET)
  - Rota Yolları: 316 → **317** (+1 path: `/api/despatch-notes/{despatch_id}/response`)
  - GET İzin Envanteri: 198 → **199** (izin: `sales`)
  - `TENANT_TABLES`: 122 → **124** (`despatch_responses`, `despatch_response_lines`)
  - `pg_twins.txt`: 129 → **130** (`test_e4b2_receipt_advice_postgresql.py`)
- **Testler:** Gelen yanıt XML ayrıştırma, kısmi kabul ret kalemleri, idempotent sync, PG ikizi.

### PR E4b-3: Frontend (Kısmi Sevk Dialogu ve Yanıt Görünümü)
- **Kapsam:** `DespatchNotePanel.tsx` ve `InvoiceDetail.tsx` arayüz geliştirmeleri (kısmi sevk modalı, yanıt rozeti ve ret gerekçeleri akordiyonu, tip güncellemesi).
- **Pin Deltası:** Arka uç pinleri değişmez (409 op, 317 path, 199 GET, 124 tablo, 130 pg_twin sabit). Frontend testleri güncellenir (`DespatchNotePanel.test.tsx`).

---

## 7. Açık Kararlar (Berkay)

1. **Göç Numaralandırması:** Çek/senet keşfi (PR #117) `0085` revizyonunu hedeflemektedir. E4b-1 için `0086` (`despatch_lines`), E4b-2 için `0087` (`despatch_responses`) sırası uygun mudur?
2. **Hizmet Faturası Kısıtı:** Faturada yalnızca hizmet kalemi (`item_type == 'LABOR'`) varsa kullanıcıya irsaliye butonu tamamen gizlensin mi, yoksa tıklandığında bilgilendirici 422 uyarısı mı gösterilsin?
3. **Varsayılan Sevk Miktarı:** Kısmi sevk diyaloğu açıldığında satır miktarları varsayılan olarak "kalan miktarın tamamı" (%100) şeklinde mi dolsun?
4. **Belge Numarası Sayacı:** `document_engine.py` `DOCUMENT_TABLES` (:27-38) kümesine `despatch_notes` eklenerek ve `:189` formatı GİB 16-haneli standardına (`edespatch.belge_numarasi_uret`) genişletilerek `document_sequences` tablosunda `sequence_key='despatch_notes:IRS'` ile yıl bazlı artış onaylanıyor mu?
5. **7 Günlük Zımni Kabul Otomasyonu:** 7 gün boyunca alıcıdan yanıt gelmeyen `DELIVERED` irsaliyeler otomatik olarak arka plan işiyle `ACCEPTED` durumuna çekilsin mi, yoksa operatör `sync` yapana kadar `DELIVERED` olarak mı kalsın?

---

## Kaynaklar ve Kanıtlar
- `docs/e4-eirsaliye-kesif-2026-09-09.md` (İzibiz WSDL 12 operasyon, UBL-TR, mevzuat eşikleri).
- `backend/alembic/versions/20260913_0083_eirsaliye.py:193-194, 268-272` (E4a tekil kısıtı `FATURA_TEKIL`, `FIRMA_KIMLIK`).
- `backend/app/routers/despatch_notes.py:55-62` (E4b için bırakılan dikişler).
- `backend/app/routers/despatch_notes.py:125, 139-142, 446-447` (`FATURA_TEKIL_IMZALARI`, `IRSALIYE_ZATEN_VAR` ve 409 fırlatma).
- `backend/app/einvoice/edespatch.py:35-40, 107` (`TERMINAL = frozenset({DELIVERED})` ve `REJECTED` gerekçesi).
- `backend/app/workflow.py:29-52, 84-100` (`sales_orders`, `delivery_notes` mevcut şema).
- `backend/app/routers/workflow.py:876-882, 933-939` (`workflow` tekil dönüşüm kısıtları, idempotent dönüş ve 409).
- `backend/app/kiraci_geri_yukleme.py:588-598` (toplam 286 yumuşak bağımlılık döngüsü kanıtı).
- `backend/app/core_schema.py:180-250` (`purchases` ve `purchase_items` mevcut şema).
- `backend/app/invoice_service.py:57` (`LABOR` kaleminin `entry["hours"]` ile üretimi).
- `backend/app/document_engine.py:27-38, 114-115, 189` (`DOCUMENT_TABLES`, ValueError ve numara formatı kısıtları).
- `backend/app/numeric_manifest.py:61-72` (`QUANTITY_COLUMNS` envanteri).
