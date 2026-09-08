# İzibiz sandbox (TEST) keşif raporu — AWS-1

Kaynak: `https://efaturatest.izibiz.com.tr` üzerinden **canlı olarak indirilen WSDL/XSD**
dosyaları + `backend/sandbox/izibiz_smoke.py` ile yapılan gerçek test çağrıları.
Bu belgedeki her operasyon adı, alan adı ve enum değeri şemadan okundu; tahmin yok.

Canlı uçlara (`authenticationws` / `efaturaws` / `earsivws` / `api.izibiz.com.tr`)
hiçbir istek yapılmadı — duman betiğinde kilit var (`_assert_test_endpoint`),
adaptörde de artık var (`IZIBIZ_ENV`, §11).

> **Uyarı — sandbox hesabı PAYLAŞIMLIDIR.** `urn:mail:defaultgb@izibiz.com.tr`
> hesabında aynı gün onlarca farklı kullanıcının belgesi bulunuyor (`KON`, `SRV`,
> `QAZ`, `MDT`, `RF0`, `IDS`… önekleri). Portalda görülen her kayıt bize ait
> değildir; bizim ürettiklerimiz yalnız **`SNG`** önekli olanlardır. Bu raporun
> ilk sürümünde "gelen kutusunda 5 fatura" diye aktarılan kayıtlar da bize ait
> değildi.

## 1. SOAP mı REST mi — öneri

**Öneri: SOAP'ta kalınmalı.** Gerekçeler:

1. **REST bir seçenek değil.** İzibiz'in e-Fatura/e-Arşiv entegrasyon yüzeyi bu
   ortamda tamamen JAX-WS/SOAP 1.1 (`document/literal`). Dokümantasyon portalında
   REST uçları yalnız yan ürünler için var; e-Fatura gönderimi ve e-Arşiv
   yazımı WSDL üzerinden yapılıyor. Yani "karşılaştır ve seç" sorusunun cevabı
   fiilen tek taraflı.
2. **#149 adaptörü zaten SOAP'a oturuyor.** `IzibizEInvoiceProvider` SOAP zarfı
   kuruyor, XML'den alan çekiyor, `SESSION_ID` önbellekliyor. Değişmesi gereken
   şey protokol değil, `endpoints.py` içindeki ‹doğrulanacak› sabitler.
3. **Ek bağımlılık gerekmiyor.** Duman testi yalnız stdlib (`urllib` + `ElementTree`)
   ile çalıştı; `zeep` gibi bir SOAP istemcisine ihtiyaç yok. Mevcut
   `HttpTransport` seamı korunabilir.

Bu bir öneridir; **seçim kararı orkestratöründür.**

## 2. Doğrulanan uçlar ve zarf biçimi

| | Değer |
|---|---|
| Auth WSDL | `https://efaturatest.izibiz.com.tr/AuthenticationWS?wsdl` |
| e-Fatura WSDL | `https://efaturatest.izibiz.com.tr/EInvoiceWS?wsdl` |
| e-Arşiv WSDL | `https://efaturatest.izibiz.com.tr/EIArchiveWS/EFaturaArchive?wsdl` |
| SOAP stili | `document` / `literal` |
| `SOAPAction` | **boş** (`soapAction=""`) — `http://tempuri.org/...` değil |
| Ad alanı (auth + e-Fatura) | `http://schemas.i2i.com/ei/wsdl` |
| Ad alanı (e-Arşiv) | `http://schemas.i2i.com/ei/wsdl/archive` |
| Gövde kök elemanı | `<OperasyonAdı>Request` (ör. `LoginRequest`), **`Login` değil** |
| Alt elemanlar | `elementFormDefault` yok ⇒ **niteliksiz** (namespace'siz) |
| Oturum | Her istekte `REQUEST_HEADER/SESSION_ID`; login'de yer tutucu (`-1`) |
| `SESSION_ID` | JWT, ~5 KB. Ölçülen: `eyJhbG…` (5113 karakter). TTL 8 saat |

Hata sözleşmesi: iş hataları **HTTP 200** gövdesinde `ERROR_TYPE` olarak gelir
(`ERROR_CODE` / `ERROR_SHORT_DES` / `ERROR_LONG_DES`). Ölçülen hiçbir ret SOAP
Fault ya da 5xx ile gelmedi. Ayrıntı ve mutasyon kanıtı için §7.

## 3. Metod eşleme tablosu

### Kimlik (AuthenticationWS)

| İzibiz operasyonu | İstek/yanıt elemanı | Adaptör fonksiyonu | Durum / eksik |
|---|---|---|---|
| `Login` | `LoginRequest` → `LoginResponse/SESSION_ID` | `_authenticate()` | ✅ canlı doğrulandı. Adaptör `<Login>` + `tempuri.org` gönderiyor → **düzeltilmeli** |
| `Logout` | `LogoutRequest` → `REQUEST_RETURN/RETURN_CODE` | *(yok)* | ⚠️ Adaptörde karşılığı yok. TTL 8 saat olduğu için şart değil ama eklenmeli |
| `CheckUser` | `CheckUserRequest/USER/IDENTIFIER` → `USER[]` (`ALIAS`, `TYPE`, `TITLE`) | `check_taxpayer()` | ✅ doğrulandı. **Yanıt boolean değil, etiket listesi**: mükellef ⇒ ≥1 `USER`, değil ⇒ 0. `IZIBIZ_FIELD_TAXPAYER` bu yüzden yanlış |
| `GetUserAuthorization` | `PRODUCT` = EINVOICE/EARCHIVE/… | *(yok)* | ℹ️ Ürün yetkisi ön kontrolü için faydalı |
| `GetAccount` | `ACCOUNT_ADDRESS` | *(yok)* | ℹ️ Gönderici bilgisini UBL'e doldurmak için faydalı |

### e-Fatura (EInvoiceWS) — 23 operasyon

| İzibiz operasyonu | Rol | Adaptör fonksiyonu | Durum / eksik |
|---|---|---|---|
| `LoadInvoice` | Taslak yükle (imzasız UBL) | *(yok)* | ⚠️ Taslak akışı adaptörde hiç yok |
| `SendInvoice` | Gönder → `SendInvoiceResponse/INVOICE_ID` | `submit()` | ⚠️ Wire'da denenmedi. `INVOICE.CONTENT` = **ZIP'lenmiş UBL'in base64'ü**; adaptör bugün payload'ı JSON olarak gömüyor → **yeniden yazılmalı** |
| `GetInvoiceStatus` | `INVOICE_STATUS/STATUS`, `STATUS_CODE`, `GIB_STATUS_CODE` | `query_status()` | ⚠️ İstek `INVOICE` (UUID attribute) alır, düz `<UUID>` değil |
| `GetInvoice` | Gelen kutusu (`INVOICE_SEARCH_KEY`) | *(yok)* | ✅ salt-okuma doğrulandı (5 kayıt). `DATE_TYPE` enum'u yalnız `ISSUE\|CREATE` |
| `GetInvoiceWithType` | PDF/HTML/XML çekme | `fetch_pdf()` | ⚠️ `GetInvoicePDF` diye bir operasyon **yok**; doğrusu bu |
| `MarkInvoice` | READ/UNREAD işaretle | *(yok)* | ℹ️ Gelen kutusu senkronu için gerekli |
| `SendInvoiceResponse` / `…WithServerSign` | Uygulama yanıtı (kabul/red) | *(yok)* | ⚠️ Ticari fatura akışı için eksik |
| `GetEnvelope*`, `getApplicationResponse`, `CancelDraftInvoice`, `ArchiveInvoice`, `GetInvoiceCount`, `GetUserList*`, `PrepareInvoiceResponse`, `GetInvoiceStatusAll` | — | *(yok)* | ℹ️ Bu artış için kapsam dışı |

### e-Arşiv (EIArchiveWS) — 21 operasyon

| İzibiz operasyonu | İstek elemanı | Adaptör fonksiyonu | Durum / eksik |
|---|---|---|---|
| `WriteToArchiveExtended` | `ArchiveInvoiceExtendedRequest` | *(yok)* | ✅ **canlı doğrulandı** — taslak oluşturuldu |
| `WriteToArchive` | `ArchiveInvoiceWriteRequest` | *(yok)* | ℹ️ Basit sürüm; e-Arşiv özellikleri yok |
| `GetEArchiveInvoiceStatus` | `UUID` (1–500) → `INVOICE/HEADER/STATUS` | *(yok)* | ✅ canlı doğrulandı |
| `GetEArchiveInvoice` / `ReadFromArchive` | Belge çekme | `fetch_pdf()` karşılığı | ⚠️ Eşlenmedi |
| `CancelEArchiveInvoice` | İptal | *(yok)* | ⚠️ e-Arşiv iptali adaptörde yok |
| `GetEArchiveReport`, `ReadEArchiveReport`, `MarkEArchiveInvoice`, `GetEmailEarchiveInvoice`, `SendSmsEarchiveInvoice`, `EArchiveInvoiceCount`, `GetEArchiveInvoiceList`, `Get*Generic*`, `CancelEDefter`, `GetELedgerStatus` | — | *(yok)* | ℹ️ Kapsam dışı |

### `endpoints.py` için somut düzeltme listesi

| Sabit | Bugün | Olması gereken |
|---|---|---|
| `IZIBIZ_WSDL_URL` | `""` | Servis başına **üç** ayrı uç (auth / einvoice / earchive) — tek URL yetmiyor |
| `IZIBIZ_SOAP_NAMESPACE` | `http://tempuri.org/` | `http://schemas.i2i.com/ei/wsdl` (+ `…/archive`) |
| `SOAPAction` başlığı | `{ns}{operation}` | `""` |
| Gövde kök adı | `Login`, `SendInvoice`… | `LoginRequest`, `SendInvoiceRequest`… |
| `IZIBIZ_OP_PDF` | `GetInvoicePDF` | `GetInvoiceWithType` |
| `IZIBIZ_OP_TAXPAYER` | `CheckUser` | `CheckUser` ✅ (tek doğru olan) |
| `IZIBIZ_TOKEN_TTL_SECONDS` | `0` | `28800` (8 saat) |
| `IZIBIZ_FIELD_TAXPAYER` | `("IS_EFATURA_USER", …)` | Boolean alan yok — `USER` eleman sayısına bakılmalı |
| `IZIBIZ_FIELD_ETTN` | `("ETTN", "UUID", …)` | Gönderimde `INVOICE_ID`; ETTN istemcinin ürettiği `UUID` |
| `IZIBIZ_FIELD_REASON` | `("STATUS_DESCRIPTION", …)` | ✅ doğru + `ERROR_SHORT_DES` / `ERROR_LONG_DES` eklenmeli |
| Oturum aktarımı | — | Her gövdede `REQUEST_HEADER/SESSION_ID` |

### Durum kodu eşlemesi

`PROVIDER_STATUS_ALIASES` metin bekliyor; İzibiz e-Arşiv **sayısal** kod +
Türkçe açıklama döndürüyor (`STATUS=105`, `STATUS_DESC="TASLAK NUMARASI OLARAK
EKLENDİ"`). e-Fatura tarafı ise `"RECEIVE - SUCCEED"` gibi tireli birleşik metin
veriyor. Her ikisi de bugünkü sözlükte **yok** ⇒ `map_provider_status()`
"bilinmiyor" döner. Bu güvenli taraftır (asla `ACCEPTED` uydurmaz) ama eşleme
tablosu genişletilmeli. Kod↔durum listesinin tamamı için İzibiz'den resmî durum
kodu dokümanı istenmeli.

## 4. UBL gereksinimleri — deneyerek bulunanlar

`INVOICE_CONTENT` / `INVOICE.CONTENT` alanına ne konacağı şemada yazmıyor.
Reddedilen her denemeden çıkan sonuç:

1. `ERROR_CODE=10007 "Zip bir dosya içermelidir."`
   → İçerik **çıplak UBL değil**, tek dosyalık bir **ZIP**'in base64'ü olmalı.
2. `ERROR_CODE=10013 "Belge içerisinde şablon bulunamamıştır."`
   → UBL, görselleştirme **XSLT**'sini kendi içinde taşımalı:
   `cac:AdditionalDocumentReference` + `cbc:DocumentType=XSLT` +
   `cac:Attachment/cbc:EmbeddedDocumentBinaryObject` (base64).
3. `ERROR_CODE=10003 "eArşiv belge gönderim şekli boş olamaz"`
   → Ayrı bir `cac:AdditionalDocumentReference` gerekiyor:
   `cbc:DocumentTypeCode=SendingType`, `cbc:DocumentType=ELEKTRONIK`.

**Mali mühür / imza engeli çıkmadı.** e-Arşiv'de `INVOICE_CONTENT` imzasız UBL
kabul ediyor; imzayı İzibiz kendi mührüyle sunucu tarafında atıyor. e-Fatura
`SendInvoice` tarafı bu artışta wire'da denenmedi; oradaki imza modeli ayrıca
doğrulanmalı.

## 5. Duman testi çıktıları (maskeli)

```
[kilit] uçlar test ortamı olarak doğrulandı: efaturatest.izibiz.com.tr
[1/4] Login OK — SESSION_ID=eyJhbG…(5113 karakter)
[1b ] CheckUser(484…(10 karakter)) → 7 etiket
       ALIAS=urn:mail:defaultpk@izibiz.com.tr TYPE=OZEL TITLE=İZİBİZ AKTİVASYON
[1c ] GetInvoice (gelen kutusu, son 7 gün, salt okuma) → 5 fatura
       SENDER=484…(10 karakter) 2026-07-20+03:00 STATUS=RECEIVE - SUCCEED
[2/4] e-Arşiv TASLAK → ID=SNG2026210140404 UUID=1E6F40C0-D0EF-4F2D-A441-F70EDA72F7E9
       RETURN_CODE=0 INVOICE_ID=SNG2026210140404 WEB_KEY=https://portaltest.izibiz.com.tr/...
[3/4] GetEArchiveInvoiceStatus → 1 kayıt
       INVOICE_ID=SNG2026210140404 UUID=1E6F40C0-D0EF-4F2D-A441-F70EDA72F7E9
       STATUS=105 (TASLAK NUMARASI OLARAK EKLENDİ) PROFILE=EARSIVFATURA
[4/4] Logout OK — RETURN_CODE=0
```

Portalda gözle doğrulanacak taslak:

| Alan | Değer |
|---|---|
| Fatura No | `SNG2026210140404` |
| ETTN (UUID) | `1E6F40C0-D0EF-4F2D-A441-F70EDA72F7E9` |
| Profil | `EARSIVFATURA` |
| Durum | `105 — TASLAK NUMARASI OLARAK EKLENDİ` |
| Tutar | 100,00 TRY + %20 KDV = **120,00 TRY** |
| Müşteri | uydurma birey, TCKN `11111111111` |

`portaltest.izibiz.com.tr` → e-Arşiv → Taslaklar.

Aynı akışta daha önce üretilmiş taslaklar (aynı hesapta görünürler):
`SNG2026729140248`, `SNG2026210140345`, `SNG2026210140404`.

## 6. Sınırlar

- Migration yok, şema değişikliği yok, `app/` altında değişiklik yok.
- Gerçek müşteri verisi kullanılmadı.
- `backend/.env.izibiz.local*` `.gitignore`'a alındı; kimlik değerleri ne kodda
  ne logda ne bu raporda yer alıyor.
- e-Fatura `SendInvoice` gönderimi **yapılmadı** (görev e-Arşiv taslağını
  istiyordu); e-Fatura tarafı yalnız salt-okuma ile doğrulandı.

---

# TIER 1 — Adaptörün gerçeğe oturtulması

SOAP kararı verildi. `app/einvoice/` içindeki İzibiz adaptörü artık bu belgedeki
ölçümlere göre çalışıyor. Aşağıdaki tablo Tier 0'daki "eksik" kolonunun ne
kadarının kapandığını gösterir.

## 7. En kritik düzeltme: iş hatası HTTP 200 gövdesinde gelir

Tier 0'da varsayılmıştı ki belge reddi SOAP Fault (HTTP 500) ile gelir. **Yanlış.**
Kaydedilen gerçek gövde (`backend/tests/fixtures/izibiz/WriteToArchiveExtended-fault.200.xml`):

```xml
HTTP/1.1 200 OK
<ArchiveInvoiceExtendedResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">
  <ERROR_TYPE><INTL_TXN_ID>65986667</INTL_TXN_ID>
    <ERROR_CODE>10007</ERROR_CODE>
    <ERROR_SHORT_DES>Zip bir dosya içermelidir.</ERROR_SHORT_DES>
  </ERROR_TYPE>
</ArchiveInvoiceExtendedResponse>
```

`response.ok` **True**. HTTP durumuna bakan bir adaptör bunu başarı sayar.

Yapılan: `_HttpEInvoiceProvider._business_failure()` kancası eklendi ve
`submit` / `query_status` / `fetch_pdf` / `check_taxpayer`'ın **dördünde de**
HTTP durumu ne olursa olsun çağrılıyor. İzibiz uygulaması `ERROR_TYPE` ve
sıfırdan farklı `RETURN_CODE` arıyor; `ERROR_CODE` → hata sınıfı eşlemesi
`IZIBIZ_ERROR_CODE_CLASSES` içinde (10003/10007/10013 → `VALIDATION`).
`classify_http(200)` bu sınıfı asla üretemezdi.

Fail-closed: hata zarfı varsa ve okunamıyorsa bile hata döner. "Çözemedim"
cevabı "sorun yok" cevabı değildir.

**Mutasyon kanıtı** (`test_izibiz_wire_contract.py`): kanca sökülünce
`RETURN_CODE=9` diyen bir yanıt `PENDING` + gerçek bir `external_id` ile
dönüyor — tam olarak engellenmek istenen sessiz sahte başarı. Gerçek ret
gövdesinde ise sınıf `VALIDATION` olmaktan çıkıyor ve "Zip bir dosya
içermelidir." gerekçesi kayboluyor. İki test de bunu açıkça ölçüyor.

## 8. "Eksik" kolonunun kapanma durumu

| Konu | Tier 0 durumu | Tier 1 | Kanıt |
|---|---|---|---|
| Ad alanı `tempuri.org` | yanlış | ✅ `schemas.i2i.com/ei/wsdl` (+`/archive`) | canlı çağrı |
| `SOAPAction` | yanlış | ✅ boş | canlı çağrı |
| Gövde kökü `<Login>` | yanlış | ✅ `<LoginRequest>` (tablo) | canlı çağrı |
| Oturum aktarımı | yok | ✅ `REQUEST_HEADER/SESSION_ID` her gövdede | canlı çağrı |
| Oturum TTL | 0 (her çağrıda login) | ✅ 8 saat − 5 dk güvenlik payı | doküman + test |
| Tek uç | yanlış | ✅ üç servis, tek tabandan türetiliyor | canlı çağrı |
| `GetInvoicePDF` | operasyon yok | ✅ `GetInvoiceWithType` | WSDL |
| Mükellef boolean alanı | yok | ✅ `USER` etiket sayısı | fixture testi |
| **200 + `ERROR_TYPE`** | **bilinmiyordu** | ✅ fail-closed, mutasyonla kanıtlı | fixture testi |
| Durum kodu eşlemesi | boş | ✅ `IZIBIZ_STATUS_ALIASES` (105/120/130 + metinler) | fixture testi |
| UBL üretimi | yalnız sözlük | ✅ `ubl_xml.py` — ZIP + gömülü XSLT + SendingType | fixture testi |
| e-Arşiv gönderimi | yok | ✅ `WriteToArchiveExtended` | canlı çağrı |
| e-Arşiv durum sorgusu | yok | ✅ `GetEArchiveInvoiceStatus` | canlı çağrı |
| e-Fatura durum sorgusu | eşlenmemiş | ✅ `GetInvoiceStatus` (UUID/ID özniteliği) | canlı çağrı |
| e-Fatura **gönderimi** | eksik | ⛔ **kapalı** — `IZIBIZ_EFATURA_SUBMIT_VERIFIED=False` | — |
| e-Arşiv PDF çekme | eksik | ⛔ açık boşluk — `WEB_VALIDATION_KEY` saklanmıyor | — |
| `Logout` | eksik | ⚠️ hâlâ yok (TTL 8 saat, kritik değil) | — |
| Uygulama yanıtı (kabul/red) | eksik | ⚠️ hâlâ yok (ticari fatura akışı) | — |
| e-Arşiv iptali | eksik | ⚠️ hâlâ yok | — |
| Gelen kutusu senkronu | eksik | ⚠️ hâlâ yok (`GetInvoice` yalnız smoke'ta) | — |
| Nes sağlayıcısı | ‹doğrulanacak› | ⚪ değişmedi, bilerek | — |

## 9. Kapalı bırakılan iki kapı ve gerekçesi

**e-Fatura gönderimi (`SendInvoice`) kapalı.** İstek gövdesi WSDL'e uygun
kurulur ama sandbox'ta hiç çalıştırılmadı: talimat salt-okumaydı ve gönderim
kararı orkestratörün. Ölçülmeyen iki şey var — `INVOICE.CONTENT` e-Fatura
tarafında da ZIP mi bekliyor, ve imzayı kim atıyor. Okuma yollarının aksine
yanlış bir gönderim **canlı bir belge üretir ve geri alınamaz**; bu yüzden ayrı
bir kapı (`IZIBIZ_EFATURA_SUBMIT_VERIFIED`) arkasında duruyor ve o kanala
gönderim gürültülü biçimde reddediliyor — ağa çıkılmadan, login bile açılmadan.

**e-Arşiv PDF çekme kapalı.** `GetEArchiveInvoice` UUID değil
`WEB_VALIDATION_KEY` istiyor. Bu anahtar gönderim yanıtında (`WEB_KEY`) geliyor
ama bugün saklanmıyor. Yanlış bir anahtarla çağırmak yerine gürültülü hata
veriliyor; kalıcı çözüm `WEB_KEY`'in gönderim sonucuna eklenmesi.

## 10. `external_id` neyi ifade ediyor?

İzibiz gönderim yanıtı **ETTN taşımaz**; yalnız kendi belge kimliğini
(`INVOICE_ID`) döner. Spec §1 kuralı "`external_id` sağlayıcı yanıtından alınır,
uydurulmaz" olduğu için `external_id = INVOICE_ID` yapıldı; istemci ETTN'i
`EInvoiceResult.uuid` alanında ayrıca duruyor.

Sonucu: durum sorgusu ETTN ile yapılır, `external_id` ile değil. Bu yüzden
`query_status()` isteğe bağlı `uuid=` ve `channel=` parametreleri aldı. e-Arşiv
durum sorgusu **yalnız** UUID kabul ettiği için, ETTN verilmeden yapılan bir
e-Arşiv sorgusu çağrı yapmadan `UNRESOLVED` döner — belge kimliğini UUID yerine
göndermek boş bir yanıt üretir ve bu "belge yok" gibi okunurdu.

## 11. Adaptör tarafı ortam kilidi (`IZIBIZ_ENV`)

Tier 1'de bir boşluk kalmıştı: `_assert_test_endpoint` yalnız duman betiğindeydi,
`app/einvoice` tarafında karşılığı yoktu. Operatör `EINVOICE_BASE_URL`'i canlı bir
adrese ayarladığında adaptörü durduran hiçbir şey yoktu. Kapatıldı.

`IZIBIZ_ENV` — yalnız `test` | `live`. Ayarlanmamışsa `test`. **Boş dize ya da
başka bir değer hatadır**; sessizce varsayılana düşmez, çünkü `IZIBIZ_ENV=prod`
yazan bir operatör canlıya gittiğini sanırken sandbox'a gitmemeli.

| `IZIBIZ_ENV` | Host | Sonuç |
|---|---|---|
| `test` | `efaturatest.izibiz.com.tr` | ✅ izin |
| `test` | `efaturaws.izibiz.com.tr` (denylist) | ⛔ ret |
| `test` | exact allowlist dışındaki herhangi bir uç | ⛔ ret |
| `test` | HTTP / URL userinfo / boş / host'suz / şemasız | ⛔ ret |
| `live` | `efaturatest.izibiz.com.tr` | ⛔ ret (ters yön de kapalı) |
| `live` | denylist ucu | ⛔ ret (denylist her ortamda) |
| `live` | allowlist dışındaki herhangi bir uç | ⛔ ret |
| geçersiz (`prod`, `""`, …) | herhangi | ⛔ hata |

Kontrol `IzibizEInvoiceProvider._guard()` içinde, `_post()`'un ilk satırında —
yani **her ağ çağrısının hemen öncesinde**, sınıf kurulumunda değil. Sebep:
`IZIBIZ_ENV` bir adaptör örneği yaşarken değişebilir (yeniden yapılandırma,
uzun ömürlü worker) ve tek seferlik bir kontrol o değişikliği kaçırır.

Dördü de fail-closed: `submit` → `FAILED`, `query_status` → `UNRESOLVED`
(ETTN'i inkâr etmez), `fetch_pdf` / `check_taxpayer` → `EInvoiceError`.
Hiçbiri sessiz `None` dönmez ve hiçbiri taşımaya ulaşmaz.

**Denylist her ortamda geçerli ve canlı allowlist boştur** — sonucu:
`IZIBIZ_ENV=live` bugün hiçbir uca ulaşamaz, yani canlı kanal fiilen kapalıdır.
Canlıya geçiş hem ilgili adresi denylist'ten çıkarmayı hem de exact
`IZIBIZ_LIVE_HOST_ALLOWLIST` girdisini PR'da gerekçelendirmeyi gerektirir;
`IZIBIZ_ENV=live` yazan biri kazara fatura kesememeli.

Bu yüzden mesaj ortama göre değişir. `test` ortamında "CANLI e-Fatura ucu
engellendi" doğru ve yeterli. `live` ortamında ise yanıltıcı olurdu — operatör
zaten canlı moddadır ve mesajı bir yapılandırma hatası sanar. Orada engelin
kasıtlı olduğu ve nereden kaldırılacağı söylenir:

> `IZIBIZ_ENV=live ama {host} hâlâ IZIBIZ_LIVE_HOST_DENYLIST içinde. Canlı
> faturaya geçiş bilinçli bir karardır: adresi denylist'ten çıkar ve bunu PR'da
> gerekçelendir.`

Kanıt: `backend/test_izibiz_live_guard.py` (41 test) — tablo testi, dört giriş
noktasının fail-closed davranışı, çağrı-başına yeniden değerlendirme, ve iki
mutasyon testi (kilit sökülünce çağrı `efaturaws.izibiz.com.tr`'ye ulaşıyor ve
`PENDING` + gerçek `external_id` üretiyor). Dosyada `socket.socket` etkisiz
hâle getirilmiş; hiçbir test soket açamaz.

---

## §7 — E1 SERTLEŞTİRME KOŞUSU (2026-09-08, GERÇEK SANDBOX)

Koşan dosya: `backend/sandbox/test_e1_izibiz_sandbox.py`
(`5 passed, 1 skipped, 2 xfailed`). Konak `efaturatest.izibiz.com.tr`,
`IZIBIZ_ENV=test`. Kimlikler yalnız bellekte; hiçbir değer kayda geçmedi.

### 7.1 — KANITLANANLAR

**e-Arşiv gönderimi BUGÜN de kabul ediliyor** ve yeni UBL alanlarıyla birlikte:
UN/ECE birim kodu (`kg` → `unitCode="KGM"`), %0 KDV'de
`TaxExemptionReasonCode=351`, ve alıcı vergi dairesi. Ölçülen belge kimlikleri
(hepsi ayrı koşulardan, hepsi `status=PENDING`):

| belge kimliği | ölçülen |
|---|---|
| `SNG2026833641341` | ilk başarılı gönderim; `WEB_KEY` GELDİ |
| `SNG2026962923391` | `DOCUMENT_TYPE` düzeltmesinden sonra |
| `SNG2026466356080` | ETTN `778cbf46-581d-517b-8504-4f9a4a0daaf1`; yoklama turu |
| `SNG2026765340161` | son yeşil koşu |

**`WEB_KEY` GERÇEKTEN DÖNÜYOR** — göç `20260911_0081`in
`invoices.einvoice_web_key` sütununun bütün dayanağı budur. Değer çıplak bir
anahtar DEĞİL, `webValidationKey=` parametresi taşıyan bir portal URL'idir ve
ilan edilen 255 haneye sığıyor (ölçüldü).

**B2B kapısı KAPALIYKEN gerçekten kapalı:** `IZIBIZ_EFATURA_SUBMIT_VERIFIED`
`False` iken `TICARIFATURA` gönderimi ağa HİÇ ÇIKMADAN reddedildi
(`status=FAILED`, `external_id=None`). Alıcı olarak sandbox'ın kendi posta
kutusu (`urn:mail:defaultpk@izibiz.com.tr`) hedeflenmişti.

### 7.2 — ÜÇ TEL HATASI, ÜÇÜ DE ÖLÇÜMLE BULUNDU

Üçü de tahminle yazılmıştı ve üçünü de sandbox reddederek düzeltti:

1. **`IssueDate` GELECEK TARİHLİ OLAMAZ.** Sabit `2026-09-11` yazılmıştı;
   cevap `ERROR_CODE=10003 SCHEMATRON KONTROL SONUCU HATALI (1:Geçersiz
   cbc:IssueDate değeri : '2026-09-11' … günün tarihinden ileri bir tarih
   olamaz)`. Test artık BUGÜNÜN tarihini üretiyor.
2. **Fatura numarasının son dokuz hanesi RAKAM olmak ZORUNDA.**
   `uuid4().hex` harf karıştırıyordu (`SNG20263BCA97661`) ve aynı Schematron
   kapısına takılıyordu.
3. **`GetEArchiveInvoiceRequest` yalnız `REQUEST_HEADER` + `WEB_VALIDATION_KEY`
   alır.** Eklenen `DOCUMENT_TYPE` şema dışıydı:
   `ERROR_CODE=10013 "Gönderilen istek geçersizdir. INVALID XML!
   cvc-complex-type.2.4b"`. Şema CANLI WSDL'den okundu
   (`/EIArchiveWS/EFaturaArchive?wsdl` → `?xsd=5`), tahmin edilmedi.
   `DOCUMENT_TYPE` PDF'i seçmiyor; yanıt belgeyi `INVOICE` alanında veriyor.

### 7.3 — KANITLANAMAYANLAR (AÇIK, GİZLENMEDİ)

**Taze bir e-Arşiv belgesi için durum sorgusu ve PDF ÇÖZÜLMÜYOR.**
`GetEArchiveInvoiceStatus` boş dönüyor (`RETURN_CODE=0`, `INVOICE` yok) ve
`GetEArchiveInvoice` PDF vermiyor (`PDF_YOK`). 0/3/8/15/30/45 sn beklenerek
yoklandı, TOPLAM ~100 sn: durum HEP `UNRESOLVED` kaldı — yani ZAMANLAMA
DEĞİL. Gönderimin kendisi başarılı olduğu için bu bir gönderim hatası da
değildir.

Sebep ÖLÇÜLMEDİ ve bu yüzden VARSAYILMIYOR. En olası aday: sorgu bizim
İSTEMCİ ETTN'imizle (uuid5) yapılıyor, oysa İzibiz belgeyi KENDİ ürettiği bir
kimlikle anahtarlıyor olabilir — kayıtlı fixture'daki UUID
(`034EE590-0D2F-4291-9B71-4AA1060FFA7E`) bizim türettiğimize benzemiyor. Bu
bir SONRAKİ DİLİMİN işidir.

Sonuç olarak `fetch_pdf`in e-Arşiv dalı ARTIK ŞEMAYA UYGUN BİR İSTEK KURUYOR
(önce hiç kurmuyordu) ama bugün PDF döndürdüğü KANITLANMADI. Sessizce boş bayt
dönmüyor: `PDF_YOK` ile gürültülü düşüyor. İki test bu gerçeği
`xfail(strict=False)` ile taşıyor; sandbox çözer hâle gelirse XPASS olur ve
satır gözden geçirilir.

### 7.4 — B2B KAPISI: `False` KALIYOR

`IZIBIZ_EFATURA_SUBMIT_VERIFIED` **DEĞİŞTİRİLMEDİ, `False` kaldı.** Kapıyı
açmak için gereken kanıt BAŞARILI BİR `SendInvoice` + onun
`GetInvoiceStatus` cevabıdır. O gönderim YAPILMADI: kapı zaten kapalı olduğu
için adaptör isteği ağa çıkarmadan reddediyor ve kapıyı "denemek için" geçici
olarak açmak, doğrulanmamış bir gönderimin CANLI BİR BELGE üretmesi riskini
alırdı — okuma yollarının aksine burada hata GERİ ALINAMAZ.

Dahası, e-Arşiv tarafında durum sorgusunun bugün çözülmediği ÖLÇÜLDÜ (§7.3);
`GetInvoiceStatus` cevabı alınamadan açılan bir kapı, gönderilen belgenin
akıbetini SORAMAYACAĞIMIZ bir kanal açmak olurdu. Kapı, §7.3 kapandıktan
sonra ayrı bir dilimde ve kanıtıyla birlikte açılmalıdır.
