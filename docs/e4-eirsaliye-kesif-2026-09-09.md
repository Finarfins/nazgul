# E4 e-İrsaliye keşif raporu

Tarih: 2026-09-09. Hedef konum: `docs/e4-eirsaliye-kesif-2026-09-09.md`. Yalnızca araştırma; uygulama kodu, migration, commit veya push yapılmadı.
Kaynak: Kullanıcı talimatı, `ARASTIRMA-e4-eirsaliye-kesif.txt:1`.

## 1. Karar özeti ve kanıt sınırı

- Test WSDL ve dört bağlı XSD doğrudan indirildi ve XML olarak okundu. 12 operation var; XSD'deki `CancelDocumentRequest`, WSDL'de çağrılabilir bir operation değil. Kaynak: [WSDL][W], [servis XSD][X].
- `100` e-İrsaliye için durum güncellenmedi; `101` kuyruğa eklendi. e-Arşiv eşlemesini taşımayın. Kaynak: [İzibiz, E-İrsaliye Durumları][D].
- Production endpoint, PDF parametresinin çalışan değeri ve sandbox imzalaması **DOĞRULANMADI**. Bunlar entegrasyonun açık kabul kriterleri. Kaynak: [W][W], [X][X]; aşağıdaki erişim ve şema incelemesi.
- “1 Temmuz 2026 herkes için geçiş” doğru bir genelleme değil. Takvim yılı kullanan, 2025 hasılatı ≥10 milyon TL olan e-Fatura mükellefi için bu tarih, okunan konsolide metinden çıkan sonuçtur. Resmî güncel metinle son kontrol **DOĞRULANMADI**. Kaynak: [509, IV.3.5–IV.3.6, s.21–22][L].
- Nazgul kaynak kodu ve migration geçmişi sağlanmadı. FastAPI/SQLAlchemy, adapter dosyaları, `http.client`, uuid5 ve e-Arşiv sandbox davranışı kullanıcı beyanıdır; kod üzerinden **DOĞRULANMADI**. Kaynak: `ARASTIRMA-e4-eirsaliye-kesif.txt:3,9`.

## 2. İzibiz SOAP sözleşmesi

### 2.1 Ortamlar ve namespace

| Ortam | Adres | Sonuç |
|---|---|---|
| Test WSDL | `https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?wsdl` | HTTP 200; XML okundu |
| Test SOAP | `https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye` | WSDL `soap:address` |
| Production adayı | `https://efatura.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?wsdl` | HTTP 404; **DOĞRULANMADI**, konfigürasyona alınmamalı |
| İkinci production adayı | `https://irsaliye.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?wsdl` | HTTP 502; **DOĞRULANMADI**, adresin yokluğunu kanıtlamaz |

Kaynak: [test WSDL][W]; tabloda verilen URL'lere 2026-09-09 tarihli doğrudan GET sonuçları. Production adresleri doğrulanmış sağlayıcı adresleri değil, sınanan adaylardır.

WSDL namespace `http://schemas.i2i.com/ei/wsdl`; entity namespace `http://schemas.i2i.com/ei/entity`; SOAP 1.1 document/literal binding ve optional MTOM policy mevcut. XSD yerel elementler için `elementFormDefault="qualified"` tanımlamıyor; kök namespace'ini bütün çocuklara otomatik yaymak hatalı olabilir.
Kaynak: [WSDL][W], [XSD 2][X], [XSD 3][H].

### 2.2 Operation, request ve response listesi

Aşağıda `Op` sütunundaki her ad için request kökü **`OpRequest`**, response kökü **`OpResponse`** şeklindedir; örneğin `SendDespatchResponseRequest` → `SendDespatchResponseResponse`. Tüm request'lerde ayrıca `REQUEST_HEADER/SESSION_ID` zorunludur. `1+` bir veya daha fazla; `?` isteğe bağlı; `0+` sıfır veya daha fazla anlamındadır.
Kaynak: [WSDL message/part eşlemeleri][W], [XSD 2][X], [XSD 3 REQUEST][H].

| Op | XSD açısından header dışındaki zorunlu request alanları | Response çocukları |
|---|---|---|
| `SendDespatchAdvice` | `DESPATCHADVICE` 1+ | `DESPATCH_ID?`, `REQUEST_RETURN?`, `ERROR_TYPE?` |
| `LoadDespatchAdvice` | `DESPATCHADVICE` 1+ | `REQUEST_RETURN?`, `ERROR_TYPE?` |
| `GetDespatchAdvice` | `SEARCH_KEY` 1 | `DESPATCHADVICE` 0+, `ERROR_TYPE?` |
| `GetDespatchAdviceWithStatus` | `SEARCH_KEY` 1 | `DESPATCHADVICE` 0+, `ERROR_TYPE?` |
| `GetDespatchAdviceStatus` | `UUID` 1+ (`xs:token`) | `DESPATCHADVICE_STATUS` 1+, `ERROR_TYPE?` |
| `SendReceiptAdvice` | `RECEIPTADVICE` 1+ | `RECEIPT_ID?`, `REQUEST_RETURN?`, `ERROR_TYPE?` |
| `LoadReceiptAdvice` | `RECEIPTADVICE` 1+ | `REQUEST_RETURN?`, `ERROR_TYPE?` |
| `GetReceiptAdvice` | `SEARCH_KEY` 1 | `RECEIPTADVICE` 0+, `ERROR_TYPE?` |
| `GetReceiptAdviceStatus` | `UUID` 1+ (`xs:token`) | `RECEIPTADVICE_STATUS` 1+, `ERROR_TYPE?` |
| `SendDespatchResponse` | `DESPATCHADVICE` 1, `STATUS` 1 (`xs:string`) | `REQUEST_RETURN?`, `ERROR_TYPE?`, `RECEIPT_ADVICE_ID?`, `RECEIPT_ADVICE_UUID?` |
| `MarkDespatchAdvice` | `MARK/DESPATCHADVICEINFO` 1+ | `REQUEST_RETURN?`, `ERROR_TYPE?` |
| `MarkReceiptAdvice` | `MARK/RECEIPTADVICEINFO` 1+ | `REQUEST_RETURN?`, `ERROR_TYPE?` |

Kaynak: [XSD 2, aynı adlı complexType'lar][X]; operation varlığı [WSDL portType][W].

### 2.3 İç alanlar ve iş kuralı ayrımı

- Header'da `SESSION_ID` zorunlu; `APPLICATION_NAME`, `CHANNEL_NAME`, `COMPRESSED`, `CLIENT_TXN_ID`, `ACTION_DATE` XSD'de optional. Sağlayıcı dokümanı uygulama/kanal adını da ister: XSD minimumu servis kabul garantisi değildir. Kaynak: [header XSD][H], [ortak alanlar][D].
- `DESPATCHADVICE` içinde `DESPATCHADVICEHEADER?`, `RECEIPTADVICEHEADER?`, `CONTENT?`; `ID`, `UUID`, `DIRECTION` optional attribute. `RECEIPTADVICE` içinde `RECEIPTADVICEHEADER?`, `CONTENT?` ve aynı attribute'lar var. `CONTENT`, xmlmime `base64Binary`. Kaynak: [XSD 2][X], [XSD 1][B].
- Yeni gönderim için sağlayıcı, `RECEIVER/@vkn`, `RECEIVER/@alias` ve `DESPATCHADVICE/CONTENT` ister; XSD bunları optional bırakır. Sıkıştırılmamış XML için `COMPRESSED=N`; içeriği base64 taşıyın. Kaynak: [SendDespatchAdvice][D].
- Send işlemlerinde `SENDER`, `RECEIVER`, `ID_ASSIGN_FLAG` (`boolean`), `ID_ASSIGN_PREFIX`, `XSLT_NAME` optional. LoadDespatchAdvice'da `PRINTED_FLAG?`; SendDespatchResponse'da `DESCRIPTION?`; Mark işlemlerinde `MARK/@value` optional. Kaynak: [XSD 2][X].
- Get aramalarında `SEARCH_KEY` zorunlu fakat çocuklarının tamamı optional: `LIMIT`, `ID`, `UUID`, `FROM`, `TO`, `START_DATE`, `END_DATE`, `READ_INCLUDED`, `DIRECTION`, `SENDER`, `RECEIVER`, `CONTENT_TYPE`; standart Get işlemlerinde ayrıca `DATE_TYPE` var. `HEADER_ONLY` optional; standart Get için default `N`. Kaynak: [XSD 2][X].
- `READ_INCLUDED` güncel XSD'de **boolean**; eski dokümanın Y/N anlatımını kopyalamayın. Status request'i eski dokümandaki `DESPATCHADVICEINFO` yerine güncel XSD'deki `UUID` listesidir. Kaynak: [XSD 2][X], [eski doküman][D].
- `REQUEST_RETURN` varsa `INTL_TXN_ID` ve `RETURN_CODE` zorunlu; `ERROR_TYPE` varsa `INTL_TXN_ID`, `ERROR_CODE`, **`ERROR_SHORT_DES`** zorunlu. SOAP fault detail kökü `RequestFault`. Kaynak: [XSD 3][H], [WSDL][W].
- XSD'de `DESPATCH_RESPONSE_STATUS={KABUL,RED}` tanımlı, fakat request `STATUS` alanı bu tipe değil `xs:string`'e bağlı. Kısmi kabulün kısa operation ile desteklenmesi **DOĞRULANMADI**; satırlı ReceiptAdvice akışı doğrulanmalı. Kaynak: [XSD 2][X].

### 2.4 XML, PDF ve iptal

XML okuma işlemi `GetDespatchAdvice`; önerilen tekil sorgu `SEARCH_KEY/UUID`, `DIRECTION=OUT`, `READ_INCLUDED=true`, `HEADER_ONLY=N`, `COMPRESSED=N`. Dönen içerik `DESPATCHADVICE/CONTENT`. Bu kombinasyonun canlı hesap testi **DOĞRULANMADI**.
Kaynak: [XSD 2][X], [GetDespatchAdvice açıklaması][D].

Ayrı `GetPDF` operation yok. `SEARCH_KEY/CONTENT_TYPE` bir string olduğundan `PDF` değerinin desteklendiği yalnız XSD'den çıkarılamaz. **PDF retrieval operation/parametre sözleşmesi DOĞRULANMADI**; `GetDespatchAdvice + CONTENT_TYPE=PDF` yalnız test adayıdır.
Kaynak: [WSDL][W], [XSD 2][X].

`CancelDocumentRequest/Response` şemada mevcut, ama `CancelDocument` WSDL portType/binding'de yok. Bu endpoint üzerinden iptal destekleniyor sonucu çıkarılamaz. Mark işlemlerini iptal saymayın.
Kaynak: [WSDL][W], [XSD 2][X].

## 3. UBL-TR ve sevkiyat alanları

### 3.1 Sürüm ve doğrulama

Okunan OASIS XSD **UBL 2.1**; eski GİB teknik belgesinde `CustomizationID=TR1.2.1`, `ProfileID=TEMELIRSALIYE`. “UBL-TR 1.2” ile `UBLVersionID=1.2` karıştırılmamalı. GİB'in güncel TR paketi GET denemeleri 502 verdi; **2026 TR XSD/Schematron bütünü DOĞRULANMADI**. Aşağıdaki eşleme OASIS XSD, tarihli GİB kılavuzu ve sağlayıcı XML örneğine dayanır.
Kaynak: [OASIS DespatchAdvice XSD][U], [GİB teknik belge s.10][T], [paket adresi][P].

OASIS kökünde zorunlu: `ID`, `IssueDate`, `DespatchSupplierParty`, `DeliveryCustomerParty`, `DespatchLine(1+)`. Türkiye kılavuzu ayrıca profil, UUID, saat, tip, imza ve Shipment gibi alanları ister; yalnız OASIS XSD geçişi Türkiye uygunluğu sağlamaz.
Kaynak: [OASIS XSD][U], [GİB uygulama kılavuzu §9][G].

### 3.2 Temel mal sevki için alan haritası

XPath'ler `DespatchAdvice` köküne göredir. “TR” tarihli kılavuz gereğini; “örnek” zorunluluk ispatı olmayan sağlayıcı örneğini gösterir.

| Bilgi | XML yolu | Kanıt / zorunluluk |
|---|---|---|
| Gönderen | `cac:DespatchSupplierParty/cac:Party` | OASIS üst taraf 1; TR kimlik/adres gerektirir. [U][U], [G §9][G] |
| Alıcı | `cac:DeliveryCustomerParty/cac:Party` | OASIS üst taraf 1; TR kimlik/adres gerektirir. [U][U], [G §9][G] |
| Taraf kimliği | `cac:PartyIdentification/cbc:ID[@schemeID='VKN' veya 'TCKN']` | Taraf altında; XML örneği. [E][E] |
| Gönderi | `cac:Shipment/cbc:ID` | Shipment TR gereği; ID, Shipment varsa XSD 1. [G][G], [C][C] |
| Fiili sevk | `cac:Shipment/cac:Delivery/cac:Despatch/cbc:ActualDespatchDate` ve `cbc:ActualDespatchTime` | TR gereği; XML örneği. [G §9–10][G], [E][E] |
| Teslimat adresi | `cac:Shipment/cac:Delivery/cac:DeliveryAddress` | OASIS izin verir; farklı adresin bulunması TR gereği. Güncel TR alt-alan cardinality **DOĞRULANMADI**. [C][C], [G][G] |
| Taşıyıcı | `cac:Shipment/cac:Delivery/cac:CarrierParty` | Kimlik `PartyIdentification/ID`, unvan `PartyName/Name`. Örnek. [E][E] |
| Araç plakası | `cac:Shipment/cac:ShipmentStage/cac:TransportMeans/cac:RoadTransport/cbc:LicensePlateID` | Örnekte `schemeID=PLAKA`. [E][E] |
| Şoför | `cac:Shipment/cac:ShipmentStage/cac:DriverPerson` | `FirstName`, `FamilyName`, **`NationalityID`**; örnekte TCKN bu alanda. [E][E] |
| Dorse | `cac:Shipment/cac:TransportHandlingUnit/cac:TransportEquipment/cbc:ID` | Örnekte `schemeID=DORSEPLAKA`; genel zorunluluk **DOĞRULANMADI**. [E][E] |
| Satır | `cac:DespatchLine/cbc:ID`, `cac:Item` | XSD zorunlu; `Item/Name` örnekte. [C][C], [E][E] |
| Miktar/birim | `cac:DespatchLine/cbc:DeliveredQuantity/@unitCode` | Örnekte C62; güncel kod listesi **DOĞRULANMADI**. [E][E] |
| Sipariş satırı | `cac:DespatchLine/cac:OrderLineReference/cbc:LineID` | OASIS OrderLineReference 1+; uydurma sipariş numarası üretmeyin. [C][C] |

Taşıma bilgisi kılavuzda plaka+şoför **veya** kargo/lojistik firması bilgileri olarak düzenlenir; tüm sevklere ikisini birden NOT NULL dayatmayın. Düzenleme zamanı ile fiili sevk zamanı ayrı tutulmalıdır.
Kaynak: [GİB §9–10][G].

### 3.3 Migration 0083 veya sonrası için tasarım önerisi

**ÖNERİ; mevcut Nazgul şemasında eksiklik DOĞRULANMADI.** “Faturada karşılığı yok” teknik olarak fazla kesin: UBL Invoice `Delivery` ve `DespatchDocumentReference` içerir; ortak tipler shipment/carrier/driver bilgilerini taşıyabilir. Yeni ihtiyaç, bunları her sevkiyat için bağımsız saklamak ve fatura miktarından ayırmaktır.
Kaynak: [Invoice XSD][I], [ortak XSD][C]; mevcut kod sınırı `ARASTIRMA-e4-eirsaliye-kesif.txt:3`.

| Önerilen tablo | Kolonlar ve SQLAlchemy türleri | Gerekçe / kaynak |
|---|---|---|
| `despatches` | `id:Uuid`, `tenant_id:<mevcut tenant PK tipi>`, `uuid:Uuid`, `document_no:String(16)` | Bağımsız sevkiyat kimliği; ETTN ve belge no ayrı. [X][X], [T][T] |
| `despatches` | `issue_date:Date`, `issue_time:Time`, `actual_despatch_date:Date`, `actual_despatch_time:Time`, `timezone:String(64)` | Fatura tarihi fiili sevk yerine geçmesin. Saat dilimi tasarım önerisi. [E][E] |
| `despatches` | `profile_id:String(32)`, `type_code:String(32)`, `direction:String(3)`, `transport_mode:String(32)` | Belge profili/yönü ve taşıma doğrulama dalı. [X][X], [U][U], [C][C] |
| `despatch_parties` | `despatch_id:Uuid FK`, `role:String(32)`, `tax_id:String(11)`, `tax_id_scheme:String(4)`, `name:String(255)`, `tax_office:String(255)` | Sender/receiver/carrier için belge anındaki kopya. VKN/TCKN sayısal değil metin. [E][E] |
| `despatch_addresses` | `despatch_id:Uuid FK`, `role:String(32)`, `street:Text`, `building_no:String(32)`, `district:String(128)`, `city:String(128)`, `postal_code:String(20)`, `country_code:String(2)`, `country_name:String(128)` | Sevk, teslim ve taraf adresleri; uzunluklar öneri, mevzuat limiti değil. [C][C] |
| `despatch_vehicles` | `despatch_id:Uuid FK`, `stage_no:Integer`, `plate:String(32)`, `kind:String(16)` | Araç ve dorse ayrı satır; tek kolonla çoklu taşıtı kaybetmeyin. [E][E] |
| `despatch_drivers` | `despatch_id:Uuid FK`, `stage_no:Integer`, `tckn:String(11)`, `first_name:String(100)`, `family_name:String(100)` | Birden çok şoför; gerçek değerleri loglardan uzak tutma tasarım tercihi. [E][E] |
| `despatch_lines` | `id:Uuid`, `despatch_id:Uuid FK`, `line_no:Integer`, `product_id:<mevcut PK> nullable`, `item_name:Text`, `quantity:Numeric(20,6)`, `unit_code:String(16)`, `order_line_id:<mevcut PK> nullable` | Sevk edilen miktar faturalanan miktardan bağımsız. [C][C], [E][E] |
| `despatch_invoice_lines` | `despatch_line_id:Uuid FK`, `invoice_line_id:<mevcut PK> FK`, `allocated_quantity:Numeric(20,6)` | Kısmi/çoklu sevk için miktarlı bağlantı; many-to-many tasarım önerisi. [I][I], [C][C] |
| `despatch_receipts` | `uuid:Uuid`, `despatch_id:Uuid FK`, `document_no:String(16)`, `response_code:String(32)`, `received_at:DateTime(timezone=True)` | Yanıt kimliği irsaliye ETTN'sinden ayrı. [X][X] |
| `despatch_receipt_lines` | `receipt_id:Uuid FK`, `despatch_line_id:Uuid FK`, `received_quantity:Numeric(20,6)`, `rejected_quantity:Numeric(20,6)`, `reason:Text` | Kısmi kabul tasarım adayı; güncel ReceiptAdvice mapping **DOĞRULANMADI**. [G §12][G] |
| `despatches` veya provider tablosu | `provider_status_code:String(32)`, `provider_status:Text`, `gib_status_code:Integer`, `response_code:String(32)`, `envelope_uuid:Uuid nullable`, `provider_txn_id:BigInteger nullable`, `last_checked_at:DateTime(timezone=True)` | SOAP sonucu, sağlayıcı durumu, GİB durumu ve ticari yanıt ayrı. [X][X], [H][H] |
| Belge arşivi | `despatch_id:Uuid FK`, `kind:String(32)`, `object_key:Text`, `sha256:String(64)`, `created_at:DateTime(timezone=True)` | Gönderilen XML, imzalı XML, yanıt ve PDF ayrı nesneler; depolama önerisi. [X CONTENT][X] |

Önerilen kısıtlar: `(tenant_id,uuid)` unique; belge numarası tekillik kapsamı yön/gönderen ayrımıyla tasarlansın; `(despatch_id,line_no)` unique; tahsis miktarı pozitif ve toplamı sevk miktarını aşmasın. Taslakta boş lojistik alanına izin verin, gönderimden önce koşullu doğrulayın. UUIDv5 girdisine tenant + belge türü + değişmez sevkiyat kimliği koyun; aynı faturanın farklı sevkleri aynı UUID'yi üretmesin. Bunlar **tasarım önerisidir**, mevcut uygulama davranışı **DOĞRULANMADI**.
Kaynak: [X kimlik alanları][X], [I referanslar][I], kullanıcı uuid5 sorusu `ARASTIRMA-e4-eirsaliye-kesif.txt:9`.

## 4. Mevzuat, tarih ve fatura ilişkisi

### 4.1 Kapsam ve geçiş

Okunan konsolide 509 metni; EPDK/ÖTV-I, ÖTV-III üretim/ithalat/ana dağıtım, maden, şeker üretimi, demir-çelik/GTİP72–73 imal-ithal-ihracı, Gübre Takip Sistemi, sebze-meyve komisyoncu/tüccarlarını kapsıyor. Genel eşik: e-Fatura kayıtlılarında 2021 ve sonrası ≥10 milyon TL. İDİS için 2024 ve sonrası ≥1 milyon TL; ayrıca GİB yazılı bildirimle kapsam belirleyebiliyor. Sektörel koşullar ve istisnalar bent bazında uygulanmalı.
Kaynak: [509 konsolide metin, IV.3.5][L]; 10 milyon değişikliği ayrıca [GİB 535 bilgi notu][N].

Genel hasılat geçişi izleyen hesap döneminin yedinci ayı başıdır: takvim yılı/2025 eşiği için **01.07.2026**; 2024 eşiği için **01.07.2025**. Sektörel/İDİS kuralı koşulların sağlandığı ayı izleyen dördüncü ay başıdır. Özel hesap döneminde 1 Temmuz sabitlenmemeli. **Çıkarım:** roadmap tarihi mükellef koşuluna bağlanmalı. 2026-09-09 itibarıyla sonraki tüm değişikliklerin yokluğu resmî kaynakla **DOĞRULANMADI**; oda yayını konsolide metin, Resmî Gazete yerine geçmez.
Kaynak: [509 IV.3.6, s.22][L].

### 4.2 Yanıt ve sevk ilişkisi

2020 GİB kılavuzunda kabul/kısmi kabul süresi fiili sevkten itibaren 7 gün; yanıt isteğe bağlıdır. Tam RED yalnız sevkten önce, yanlış alıcı/mal halinde geçerlidir. Yanıtsızlıkta tam teslim varsayılır; eksik teslim ayrıca tevsik edilebilir. Geri taşıma yeni irsaliye gerektirir. Güncel sürümle değişmezliği **DOĞRULANMADI**.
Kaynak: [GİB §12, §15.24–25][G].

**Tasarım önerisi:** Kısmi sevk (siparişin parça parça yollanması), kısmi kabul (giden malın bir bölümünün kabulü) ve kısmi faturalama ayrı olay olsun. Her fiziksel sevke ayrı kayıt, satır bazında fatura tahsisi oluşturun; yanıt bekleme süresini fatura düzenleme ertelemesi saymayın. Invoice'da çoklu `DespatchDocumentReference` var. Önceden faturalanmış malın sevkinde fatura referansı; sonradan faturalamada irsaliye referansı eşlemesi güncel TR kurallarıyla doğrulanmalı.
Kaynak: [Invoice XSD][I], [GİB §15.3 ve §15.25][G]; önerilen DB ilişkisi §3.3.

## 5. İzibiz'e özgü sonuçlar

| Konu | Sonuç | Kaynak |
|---|---|---|
| ETTN | XML `cbc:UUID`, SOAP `@UUID`, sorgu `UUID`; belge no `ID` ayrı | [T][T], [X][X] |
| uuid5 | Şema UUID sürümünü kısıtlamıyor; uuid5'in sağlayıcı kabulü ve mevcut pattern'in tekilliği **DOĞRULANMADI** | [X token/string tipleri][X] |
| 100 / 101 | 100 güncellenmemiş; 101 kuyruk | [İzibiz durum tablosu][D] |
| Diğer kodlar | 102 taslak işleme; 103/104/105 paketleme süreci/başarı/hata; 107 imzalı; 133 alındı; 134 timeout; 135/136/137 gönderim süreci/hata/başarı | [D][D] |
| Çelişkili kod | Tabloda `SIGN_PROCESSING` ve `SIGN_FAILED` aynı 106 ile verilmiş; güvenilir hata eşlemesi **DOĞRULANMADI** | [D][D] |
| GİB ve ticari yanıt | `GIB_STATUS_CODE` ve `RESPONSE_CODE`, provider `STATUS_CODE`'dan ayrı | [X][X] |
| PDF | Ayrı operation yok; CONTENT_TYPE değer kümesi tanımsız; canlı PDF dönüşü **DOĞRULANMADI** | [W][W], [X][X] |
| Sandbox imzası | İmzalama durumlarının varlığı sandbox'ın imzaladığını kanıtlamaz. Hesap testi/sağlayıcı teyidi yok: **DOĞRULANMADI** | [D][D], kullanıcı beyanı `ARASTIRMA-e4-eirsaliye-kesif.txt:9` |

## 6. Orchestrator için öncelikli riskler ve kapanış kriterleri

Aşağıdaki öncelik ve kabul kriterleri araştırmacı önerisidir; dayanaklar her satırdadır.

| Öncelik | Risk / açık soru | Kapanış kriteri |
|---|---|---|
| P0 | Güncel TR paket yok; eski teknik dokümanla 2026 uygunluğu kurulamaz. [G][G], [T][T], [P][P] | Yürürlük tarihli XSD+Schematron+kod listesi alınsın; mal sevki ve yanıt örnekleri doğrulansın |
| P0 | Production endpoint doğrulanmadı. §2.1 | Sağlayıcının production WSDL adresi ve import zinciri teyit edilsin; test/prod sözleşme farkı çıkarılsın |
| P0 | “1 Temmuz 2026” koşulsuz yorumlanıyor. [L][L] | Mükellef faaliyetleri, dönem, ciro ve İDİS durumu güncel resmî metne bağlansın |
| P0 | e-Arşiv status yeniden kullanımı yanlış; 106 tablosu çelişkili. [D][D] | Ürüne özel state mapping, bilinmeyen durum ve terminal koşul tanımlansın |
| P1 | Eski doküman ile canlı XSD request farkları. [D][D], [X][X] | Namespace, boolean ve UUID-list kontratı sandbox örnekleriyle kabul edilsin |
| P1 | Sandbox imza/PDF davranışı bilinmiyor. [X][X], §5 | İmzalı XML doğrulaması, gerçek PDF dönüşü ve nihai status örneği alınsın; takılma davranışı belgelenmiş olsun |
| P1 | Kısmi kabul kısa operation ile kanıtlanmadı; iptal operation yok. [W][W], [X][X] | ReceiptAdvice satırları, kabul/ret nedenleri ve sevk öncesi RED akışı doğrulansın |
| P1 | Timeout sonrası çift belge / aynı faturanın farklı sevkleri. [X UUID][X] | Sabit sevkiyat UUID'si, tekrar öncesi status sorgusu ve farklı sevke farklı ETTN senaryosu doğrulansın |
| P1 | Nazgul şeması ve migration 0083 boşluğu bilinmiyor. Kullanıcı dosyası:3,5 | Mevcut model ve migration HEAD okunup kolon delta'sı çıkarılsın; numarayı orchestrator atasın |
| P2 | İrsaliye–fatura miktar kayması, çoklu şoför/araç kaybı. [I][I], [E][E] | İki sevk/tek fatura ve tek sevk/çoklu fatura tahsisleri; çoklu şoför saklama doğrulansın |

## Kaynaklar

[W]: https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?wsdl
[X]: https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?xsd=2
[H]: https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?xsd=3
[B]: https://efaturatest.izibiz.com.tr/EIrsaliyeWS/EIrsaliye?xsd=1
[D]: https://dev.izibiz.com.tr/
[U]: https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-DespatchAdvice-2.1.xsd
[C]: https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/common/UBL-CommonAggregateComponents-2.1.xsd
[I]: https://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-Invoice-2.1.xsd
[T]: https://dev.izibiz.com.tr/resource/BELGELER/UBL-TR%20irsaliye%20-%20V%201.0.pdf
[E]: https://dev.izibiz.com.tr/resource/xml/irsaliye_temel_ornek.xml
[G]: https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/e-Irsaliye_Uygulama_Kilavuzu.pdf
[L]: https://www.asmmmo.org.tr/userfiles/others/files/Mvzt/Gh/26/06-25-Vergi%20Usul%20Kanunu%20Genel%20Tebli%C4%9Fi%20%28S%C4%B1ra%20No%20509%29.pdf
[N]: https://cdn.gib.gov.tr/api/gibportal-file/file/getFileResources?objectKey=arsiv%2Ffileadmin%2Fmevzuatek%2Fmevzuatbilginotu%2F535_serno_vukgenteb_abn.pdf
[P]: https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/UBL-TR1.2.1_Paketi.zip
