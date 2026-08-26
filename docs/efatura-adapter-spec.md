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

Geçişler yalnızca sağlayıcı yanıtıyla ilerler. Terminal durumdan geri dönüş yok.

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

## 8. Test stratejisi

- Fabrika sözleşmesi: isim+kimlik → gerçek stub; eksik → NoOp; bilinmeyen → NoOp
  (hardening PR'ında var).
- Gerçek adaptör: sağlayıcı HTTP çağrıları **mock'lanarak** durum makinesinin
  her geçişi (PENDING→SENT→ACCEPTED/REJECTED/FAILED) test edilir.
- `query_status` hiçbir mock'ta koşulsuz ACCEPTED dönmez (regresyon bekçisi).
- UBL payload toplamları iç fatura ile kuruşuna eşit (mevcut test korunur).
- Kimlik bilgilerinin log/`raw`/hata mesajına sızmadığı test edilir.
