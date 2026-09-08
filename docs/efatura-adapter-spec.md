# e-Fatura Adaptör Spesifikasyonu — İzibiz / Nes (Increment 1)

> Durum: TASLAK / onay bekliyor. Bu belge **koddan önce** yazıldı. Adaptör
> kodu bu spec onaylandıktan sonra, buradaki sözleşmeye göre yazılacak.
> `‹doğrulanacak›` işaretli alanlar sağlayıcının resmi API dokümanından
> teyit edilecek; belge bu boşlukları bilinçli olarak açık bırakır.

## 0. Amaç ve kapsam

Sungur Tarım ERP'nin ürettiği iç faturayı GİB'e e-Fatura / e-Arşiv olarak
gönderecek sağlayıcı adaptörlerinin sözleşmesi. Increment 1 kapsamı: **İzibiz**
ve **Nes** sağlayıcıları için gerçek HTTP entegrasyonu — gönderim, durum
sorgusu, PDF çekme, mükellef sorgusu. Kapsam dışı: toplu gönderim kuyruğu,
gelen e-Fatura (inbox), irsaliye, müstahsil makbuzu (sonraki increment'lar).

Değişmez kural: **iç fatura tek yetkili kaynaktır.** Adaptör yalnızca dış
kanalın durumunu yansıtır; hiçbir zaman iç faturayı geçersiz kılmaz.

## 1. Sağlayıcı seçimi ve güvenlik sözleşmesi

`get_einvoice_provider(settings)` davranışı (hardening PR'ında uygulandı):

- `einvoice_provider` boş / "noop" / bilinmeyen → **NoOp** (inert).
- Gerçek sağlayıcı yalnızca **hem isim verilmiş hem** `einvoice_username`
  + `einvoice_password` doluysa çözülür; eksikse NoOp.
- `izibiz` / `nes` isimleri gerçek adaptöre çözülür; kimlik yoksa NoOp'a düşer.

Kritik davranış sözleşmeleri:

- `query_status()` **asla koşulsuz `ACCEPTED` dönmez.** Yalnızca sağlayıcının
  gerçek yanıtından türetilir. GİB'e gitmemiş bir belge hiçbir koşulda
  "kabul edildi" görünemez.
- `submit()` gerçek `external_id`/ETTN'i sağlayıcı yanıtından alır; uydurmaz.
- Hiçbir metot sessiz sahte başarı üretmez; entegrasyon hatası **gürültülü**
  (`EInvoiceResult(status="FAILED", error=...)` veya exception) olur.

### Mock / sandbox (ERTELENDİ — ayrı PR)

Yerel geliştirme/e2e için mock gerekiyorsa **ayrı bir isimle** eklenir:
`einvoice_provider="izibiz-sandbox"` / `"nes-sandbox"`. Şartlar:

- `izibiz` / `nes` isimleri **asla** mock'a çözülmez (prod config kazara
  mock'a düşemez).
- Sandbox `query_status` gerçek durum makinesini taklit eder (aşağıya bak) —
  düz `ACCEPTED` dönmez.
- Sandbox sağlayıcılar **yalnızca non-prod flag** (`settings.debug` /
  `APP_ENV != "production"`) altında seçilebilir; prod'da denenirse NoOp.

Bu, durum makinesi bu spec'te kilitlendikten sonra yazılır.

## 2. Kimlik doğrulama akışı

### İzibiz
- Protokol: SOAP/WS. `Login` operasyonu ile oturum/token alınır, sonraki
  çağrılarda `SessionID` / `Cookie` taşınır. ‹doğrulanacak: WSDL uçları›
- Kimlik: `einvoice_username`, `einvoice_password`; gönderici VKN
  `einvoice_sender_vkn`.
- Token TTL ‹doğrulanacak›; süre dolunca yeniden Login.

### Nes
- Protokol: REST/JSON. Login uç noktasından `access_token` (Bearer) alınır.
  ‹doğrulanacak: base_url, login path›
- Kimlik: aynı `einvoice_username`/`einvoice_password`; API key gerekiyorsa
  `einvoice_api_key` ‹doğrulanacak›.
- Token TTL ‹doğrulanacak›; 401 alınınca bir kez yeniden Login + tek retry.

Ortak: kimlik bilgileri yalnızca `.env.production` içinde; log'a, hata
mesajına, `raw` alanına **yazılmaz**.

## 3. Endpoint / operasyon listesi

Her sağlayıcı için soyut metot → gerçek operasyon eşlemesi:

| Soyut metot | İzibiz (SOAP) | Nes (REST) |
|---|---|---|
| `submit(payload)` | `SendInvoice` / `SendDocument` ‹doğrulanacak› | `POST /invoices` ‹doğrulanacak› |
| `query_status(ext_id)` | `GetInvoiceStatus` ‹doğrulanacak› | `GET /invoices/{ettn}/status` ‹doğrulanacak› |
| `fetch_pdf(ext_id)` | `GetInvoicePDF` ‹doğrulanacak› | `GET /invoices/{ettn}/pdf` ‹doğrulanacak› |
| `check_taxpayer(vkn)` | `CheckUser` / GB listesi ‹doğrulanacak› | `GET /taxpayers/{vkn}` ‹doğrulanacak› |

`check_taxpayer` → EFATURA vs E-ARŞİV kararı: VKN GİB mükellef listesindeyse
`{"is_efatura_user": True}`, değilse `False`. **Varsayılan `True` DEĞİL** —
liste sorgulanamıyorsa hata döner, sessizce EFATURA'ya yönlendirmez.

## 4. UBL-TR 1.2 zorunlu alanları

`build_einvoice_payload` (mevcut `ubl.py`) çıktısının içermesi gerekenler:

- Fatura: `ProfileID` (TICARIFATURA/TEMELFATURA/EARSIVFATURA), `ID` (fatura no),
  `UUID` (ETTN), `IssueDate`, `IssueTime`, `InvoiceTypeCode`, `DocumentCurrencyCode`.
- Gönderici (`AccountingSupplierParty`): VKN/TCKN, unvan, vergi dairesi, adres.
- Alıcı (`AccountingCustomerParty`): VKN/TCKN, unvan, adres.
- Satırlar (`InvoiceLine`): miktar+birim (UN/ECE kodu), birim fiyat,
  `LineExtensionAmount`, KDV oranı/tutarı, mal/hizmet adı.
- Vergi (`TaxTotal` / `TaxSubtotal`): KDV matrah, oran, tutar; tevkifat varsa
  `WithholdingTaxTotal` ‹increment 2›.
- Parasal toplam (`LegalMonetaryTotal`): `LineExtensionAmount`, `TaxExclusiveAmount`,
  `TaxInclusiveAmount`, `PayableAmount`.

Para/miktar sözleşmesi: tüm tutarlar `Decimal`, kuruşuna kadar iç fatura ile
birebir; JSON'da string (proje para/miktar sözleşmesi). Float yasak.

## 5. ETTN / zarf durum makinesi

```
              submit()            query_status() (poll)
   (yok) ───────────────▶ PENDING ───────────────▶ SENT ──┬──▶ ACCEPTED  (GİB kabul)
                             │                             │
                             │ gönderim reddi              └──▶ REJECTED  (GİB ret + gerekçe)
                             ▼
                          FAILED  (ağ/kimlik/şema hatası — external_id yok)
```

- `NONE`: hiç gönderilmemiş (NoOp veya taslak).
- `PENDING`: `submit()` kabul edildi, ETTN alındı, GİB işliyor.
- `SENT`: zarf GİB'e ulaştı, yanıt bekleniyor.
- `ACCEPTED` / `REJECTED`: **terminal**. REJECTED, `error` alanında GİB
  gerekçesini taşır.
- `FAILED`: gönderim başarısız, ETTN yok — yeniden `submit()` denenebilir.
- `CANCELLED` (E2): **terminal**. Entegratör bizim iptal isteğimizi kabul etti.

Geçişler yalnızca sağlayıcı yanıtıyla ilerler. Terminal durumdan geri dönüş yok.

### 5.1 — `CANCELLED` (E2): tek yönlü, ve yalnız üç durumdan

```
   PENDING ─┐
   SENT  ───┼── cancel() KABUL ──▶ CANCELLED   (terminal)
   ACCEPTED─┘

   NONE / FAILED / REJECTED ── cancel() ──▶ (DEĞİŞMEZ)
```

`CANCELLED`, makinedeki **tek BİZE ait durumdur**: diğerleri sağlayıcının
cevabından doğar, bu bizim isteğimizin kabulünden. Üç sonucu var:

1. **Yalnız `CANCELLABLE` = {PENDING, SENT, ACCEPTED}'ten kabul edilir.**
   Dışarıda kalan üçü ayrı gerekçelerle dışarıda: `NONE` ve `FAILED` için
   entegratörde iptal edilecek bir zarf YOKTUR (ve `FAILED`i iptal yazmak
   meşru yeniden gönderimi kapatırdı); `REJECTED` zaten GİB'in kararıdır ve
   üzerine yazmak o kararı ve gerekçesini silerdi.
2. **Kapı, terminal kontrolünden ÖNCE çalışır.** Aksi hâlde `ACCEPTED` —
   yani operatörün geri çekmesi gereken asıl belge — geçişi yutardı.
3. **Bir durum SORGUSU `CANCELLED` üretemez** (`QUERYABLE` dışındadır). Bir
   belgenin iptal edildiğinin tek kanıtı, entegratörün BİZİM iptal
   çağrımıza verdiği cevaptır.

## 6. Hata kodu → Türkçe mesaj tablosu

Sağlayıcı hata kodları son kullanıcıya ham gösterilmez; eşlenir:

| Sınıf | Örnek koşul | Kullanıcı mesajı (TR) |
|---|---|---|
| AUTH | 401 / geçersiz oturum | "e-Fatura sağlayıcı girişi başarısız — kimlik bilgilerini kontrol edin." |
| VALIDATION | UBL şema/zorunlu alan | "Fatura e-Fatura biçimine uymuyor: {alan}." |
| TAXPAYER | VKN mükellef değil | "Bu alıcı e-Fatura mükellefi değil; e-Arşiv olarak kesilecek." |
| DUPLICATE | Aynı ETTN/fatura no | "Bu fatura zaten gönderilmiş." |
| QUOTA | Kontör/limit bitti | "e-Fatura kontörü yetersiz." |
| NETWORK | timeout/5xx | "e-Fatura servisine ulaşılamadı, tekrar denenecek." |
| UNKNOWN | eşlenmemiş | "e-Fatura işlemi tamamlanamadı (kod: {code})." |

Ham sağlayıcı gövdesi yalnızca `raw` içinde (audit), UI'ya çıkmaz.

## 7. Timeout / retry / idempotency politikası

- Bağlantı timeout 5 sn, okuma timeout 30 sn ‹sağlayıcıya göre ayarlanabilir›.
- Retry yalnızca **idempotent** ve **geçici** hatalarda: `query_status`,
  `fetch_pdf`, `check_taxpayer` → 3 deneme, üstel geri çekilme (1s/2s/4s).
- `submit()` **otomatik retry edilmez** (çift gönderim riski). Ağ hatasında
  `FAILED` döner; yeniden gönderim, aynı fatura için **aynı istemci ETTN'i**
  ile yapılır (sağlayıcı DUPLICATE ile ikinciyi reddetmeli).
- Idempotency anahtarı: fatura ID + şirket ID'den türetilen kararlı ETTN;
  `submit` bunu taşır, tekrarlar aynı belgeye çözülür.

### 7.1 — B2B REDDİ AĞA ÇIKTIKTAN SONRA GELİR (ölçüldü)

`IZIBIZ_EFATURA_SUBMIT_VERIFIED = False` olduğu sürece e-Fatura (B2B) gönderimi
reddedilir. **Ama bu ret, sağlayıcıya HİÇ GİDİLMEDİĞİ anlamına GELMEZ** ve bu
ayrım operasyonel olarak önemlidir: reddedilen her B2B gönderim denemesi
İzibiz'de **bir oturum ve bir mükellefiyet sorgusu tüketir**.

Ölçüm (sahte taşımayla, çağrı sırası kaydedilerek):

| # | Servis | Gövde kök elemanı |
|---|---|---|
| 1 | `AuthenticationWS` | `LoginRequest` |
| 2 | `AuthenticationWS` | `CheckUserRequest` |
| — | *(EInvoiceWS'e çağrı YOK)* | — |

Sonuç: `FAILED`, gerekçe `IZIBIZ_EFATURA_SUBMIT_ERROR`.

**Neden böyle, ve neden bir kusur değil.** Kanal, uç tarafından
`check_taxpayer()` sonucundan çözülüyor (`resolve_channel`), yani bir belgenin
B2B olduğu ancak `CheckUser` cevaplandıktan SONRA bilinebiliyor;
`_submit_gate` de kanala baktığı için ondan önce karar veremez. Kapıyı öne
almak, mükellefiyeti sormadan "bu muhtemelen B2B'dir" diye tahmin etmek
olurdu — spec §3'ün açıkça yasakladığı iyimser varsayım.

**Karşılaştırma — hangi kapı nerede duruyor.** Üç kapı ağdan ÖNCE çalışır ve
tek bir çağrı bile üretmez: eksik UBL alanı (`missing_required_fields`),
yapılandırma (`_configured`), e-Arşiv durum sorgusunun ETTN önkoşulu
(`_query_precondition`) ve e-Arşiv iptalinin ETTN önkoşulu
(`_cancel_precondition`). B2B kapısı bunlara KATILAMAZ, çünkü girdisi
(kanal) ağdan gelir.

**Operatöre etkisi:** B2B bir alıcıya fatura kesme denemeleri, hiçbir belge
üretmeseler de sağlayıcı tarafında görünür ve kotaya yazılır. Deneme
tekrarlanan bir otomasyona bağlanmamalıdır.

## 8. Test stratejisi

- Fabrika sözleşmesi: isim+kimlik → gerçek stub; eksik → NoOp; bilinmeyen → NoOp
  (hardening PR'ında var).
- Gerçek adaptör: sağlayıcı HTTP çağrıları **mock'lanarak** durum makinesinin
  her geçişi (PENDING→SENT→ACCEPTED/REJECTED/FAILED) test edilir.
- `query_status` hiçbir mock'ta koşulsuz ACCEPTED dönmez (regresyon bekçisi).
- UBL payload toplamları iç fatura ile kuruşuna eşit (mevcut test korunur).
- Kimlik bilgilerinin log/`raw`/hata mesajına sızmadığı test edilir.

---

## §9 — E1 SERTLEŞTİRMESİNİN AÇIK BIRAKTIKLARI (göç `20260911_0081`)

Üçü de BİLİNEREK açık; her biri AYRI bir dilimin işidir ve gerekçesi burada.

### 9.1 — %0 KDV istisna kodu FİRMA BAZLI DEĞİL

`app/einvoice/ubl.py::DEFAULT_TAX_EXEMPTION_REASON_CODE` bir **SABİTTİR**
(`351` — "Diğer İstisnalar"). UBL-TR, `Percent` 0 iken
`TaxExemptionReasonCode` ZORUNLU tuttuğu için bir değer YAZILMAK ZORUNDAYDI
ve kodsuz göndermek belgeyi şema seviyesinde eksik bırakırdı.

Doğrusu bunun **firma başına** ayarlanabilmesidir: bir işletmenin %0'ları
ihracat istisnası (`301`) olabilirken bir diğerininki tarımsal istisna
olabilir ve `351` ikisi için de "en azından geçerli" ama "en doğru" değildir.

**BU DİLİMDE AÇILMADI ve sebebi ölçülmüştür:** firma bazlı bir geçersiz kılma
YENİ BİR SÜTUN demektir, yeni sütun AYRI BİR GÖÇ demektir ve bu göç zaten üç
sütun taşıyor. Ayarı "göçsüz" bir yere (ör. JSON bir ayar alanına) sıkıştırmak
daha kötü olurdu: vergi belirleyen bir değerin şemasız yaşaması, denetlenmesi
ve kısıtlanması imkânsız bir alan yaratırdı.

**Kapandığında ne değişmeli:** sabit, firma ayarından okunan bir çözücüye
dönüşür; `_tax_subtotal` çağıranından kodu ALIR, kendi varsayılanını
UYDURMAZ. `test_e1_efatura_sertlestirme.py::test_SIFIR_KDV_ISTISNA_KODU_URETIYOR`
o gün değişmelidir.

### 9.2 — ADRESTE İL/İLÇE YOK (`CityName` = "-")

**ÖLÇÜLDÜ, VARSAYILMADI:** `app/core_schema.py`de `customers` tablosunda
YALNIZ `address` (Text) var; `city`/`district` sütunu YOKTUR. Kaynakta il/ilçe
olmadığı için UBL'e yazılacak bir değer de yoktur.

Adresi ayrıştırmak (virgülden bölmek gibi) düşünüldü ve REDDEDİLDİ: bu
UYDURMAKTIR ve **yanlış bir il, boş bir ilden daha kötüdür** — boş bir alan
"bilmiyoruz" der, yanlış bir il "biliyoruz" der ve yanılır.

Sütunların YOKLUĞU teste ÇİVİLENDİ
(`test_e1_efatura_sertlestirme.py::test_ADRES_ILI_KAYNAKTA_YOK_OLCULDU`):
`customers`a `city` ya da `district` eklendiği anda o test KIRMIZI olur ve
UBL'in de güncellenmesi gerektiğini ADIYLA söyler.

### 9.3 — e-ARŞİV DURUM/PDF ÇÖZÜLMÜYOR, B2B KAPISI KAPALI

`fetch_pdf`in e-Arşiv dalı artık ŞEMAYA UYGUN bir istek kuruyor (önce hiç
kurmuyordu) ama sandbox'ta PDF döndürdüğü KANITLANAMADI; durum sorgusu da taze
bir belge için boş dönüyor. ~100 sn yoklandı, zamanlama değil. Ölçüm ve olası
sebep: `docs/izibiz-sandbox-bulgular.md` §7.3.

`IZIBIZ_EFATURA_SUBMIT_VERIFIED` bu yüzden `False` kaldı — §7.4.

**GÜNCELLEME (E2, e-belge yaşam döngüsü).** Bu maddenin iki yarısından biri
kapandı, biri kapanmadı — ve karışmasınlar diye ayrı yazılıyor:

* **PDF YOLU ARTIK VAR.** `GET /api/invoices/{id}/einvoice/download?format=pdf|xml`
  eklendi. `fetch_pdf` zaten şemaya uygun istek kuruyordu ama onu çağıran bir
  UÇ YOKTU: saklanan `einvoice_web_key` hiçbir yerden okunmuyordu, yani anahtar
  saklanıyor ama kullanılmıyordu. Uç bu boşluğu kapatıyor; anahtar seçimi
  kanala göre (e-Arşiv → `WEB_VALIDATION_KEY`, e-Fatura → sağlayıcı belge
  kimliği). `format=xml` ağa HİÇ çıkmaz: gönderilen UBL, dondurulmuş
  `einvoice_payload`dan yeniden üretilir.
* **SANDBOX'TA PDF HÂLÂ KANITLANMADI.** Ucun var olması, sağlayıcının o belgeyi
  verdiği anlamına gelmiyor. Sandbox ölçümü ve açık soru
  `docs/izibiz-sandbox-bulgular.md` §7.3'te; sandbox testi bu yüzden
  `xfail(strict=False)` ile işaretli — geçerse de kırmızı yakmaz, çünkü asıl
  belirsizlik bizde değil sağlayıcıda.
* **B2B KAPISI HÂLÂ KAPALI** (`IZIBIZ_EFATURA_SUBMIT_VERIFIED = False`) ve E2
  bunu DEĞİŞTİRMEDİ. Değişen tek şey, kapının ne zaman çalıştığının artık
  yazılı olması: §7.1.

### 9.4 — e-FATURA İPTALİ: UYGULAMA YANITI YOK (E2'de kasıtlı olarak açık)

e-Arşiv iptali E2'de kapandı (`CancelEArchiveInvoice`, şeması `?xsd=5`ten
okundu). **e-Fatura (B2B) iptali kapanmadı ve kapanamazdı**: giden bir ticari
e-Fatura tek taraflı iptal edilemez. Alıcı, belgenin kendisine ulaşmasından
itibaren **sekiz gün** içinde itiraz eder (TTK 18/3) ve iptal o sürecin
sonucudur; teknik karşılığı `ApplicationResponse` (uygulama yanıtı)
akışıdır ve o akış bu artışta YOKTUR.

Bu yüzden uç, B2B bir belge için iptali **409 ile reddediyor** ve gerekçeyi
operatöre yazıyor. Alternatif — yerelde sessizce iptal etmek — ERP'nin "iptal"
dediği bir belgenin GİB'de yürürlükte kalması demekti.

Kapatılması gereken iş, ayrı bir dilim: `SendInvoiceResponse` /
`getApplicationResponse` operasyonları (envanterde var, adaptörde yok —
`docs/izibiz-sandbox-bulgular.md` e-Fatura tablosu).
