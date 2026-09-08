# WA4 — WhatsApp KANALLARI: iki ayrı yol, tek taşıyıcı

Bu belge **tek bir soruyu** cevaplıyor: WhatsApp'tan çıkan mesajlar hangi
yoldan gider ve neden hepsi aynı yoldan **gitmez**?

Konu WA4'ün giden tarafıdır: `app/notifications/provider.py::
WhatsAppNotificationProvider` (adaptör) ve `KANAL_SAGLAYICILARI["WHATSAPP"]`
(kanal çivisi). **Taşıyıcı WA4'ün DEĞİL**: `app/whatsapp/saglayici.py::
MetaBulutSaglayici` WA3-full (#85) ile geldi ve WA4 onu KULLANIR —
gerekçe §3.1.

---

## 1. İKİ YOL VAR ve ayrı olmaları BİLİNÇLİ

WhatsApp üzerinden kullanıcıya iki farklı sınıftan mesaj gidebilir:

| | **A — Asistan cevabı** | **B — Bildirim** |
|---|---|---|
| Kim başlattı | KULLANICI (bize yazdı) | SİSTEM (vade geldi, stok kritik) |
| Ne zaman | Kullanıcının mesajından hemen sonra | Bir kural tetiklediğinde |
| Alıcı kim | `whatsapp_links`teki **ERP KULLANICISI** | `customers` / `suppliers` — **MÜŞTERİ ya da TEDARİKÇİ** |
| Rıza kaydı | YOK (ve gerekmez — §2) | `notification_consents` ZORUNLU |
| Defter | YOK; cevap senkron döner | `notification_outbox` satırı |
| Kod yolu | WA3 işçisi → `saglayici.metin_gonder` | `notifications/service` → `WhatsAppNotificationProvider` → `saglayici.metin_gonder` |

**Taşıyıcı ORTAK, defter AYRI.** İkisi de sonunda
`saglayici.metin_gonder`e iner — "kim, nereden WhatsApp mesajı gönderiyor"
sorusunun tek bir cevabı olsun diye (`SmtpEmailNotificationProvider` için
yazılı olan kuralın aynısı). Ayrılan şey **defter ve rıza**dır.

---

## 2. ASİSTAN CEVAPLARI OUTBOX'A GİRMEZ — ölçülmüş gerekçe

Bu, WA4'ün en çok sorulacak kararı, o yüzden gerekçesi burada tam yazılı.

### 2.1 Rıza defteri CUSTOMER/SUPPLIER'dır; ERP kullanıcısı orada YOKTUR

Ölçüldü: `app/notifications/consents.py`

```python
PARTY_TYPES: frozenset[str] = frozenset({"CUSTOMER", "SUPPLIER"})
```

ve bu bir uygulama tercihi değil, **veritabanı kısıtı**: göç `0033`ün
`ck_notification_consents_party_type` CHECK'i de yalnız bu iki değeri kabul
ediyor (`app/notifications/schema.py`nin kendi yorumunda yazılı).

Asistan cevabının alıcısı ise `whatsapp_links.user_id` — yani bir
**`app_users` satırı**. O kullanıcının `notification_consents`te bir kaydı
YOKTUR ve **olamaz**.

Asistan cevabını outbox'tan geçirmek üç seçeneğe zorlardı ve **üçü de kötü**:

1. **Rıza denetimini atlamak.** Outbox'ın var olma sebebi rızadır; içinde
   rıza denetlenmeyen bir satır sınıfı açmak, defterin verdiği güvenceyi
   *bütün* satırlar için zayıflatırdı — "bu kanal rızalı" cümlesi artık
   satır tipine bakmadan söylenemezdi.
2. **`PARTY_TYPES`e `USER` eklemek.** Kısıtı ve göçü değiştirmek gerekirdi
   ve anlamı yanlış olurdu: rıza, **pazarlama/bildirim** için verilen bir
   izindir. Kullanıcının kendi sorduğu sorunun cevabını almak için "izin
   vermesi" kavramsal olarak boştur — zaten mesajı O yazdı.
3. **Sahte bir müşteri/tedarikçi kaydı uydurmak.** Söylemeye gerek yok.

### 2.2 Rıza zaten VERİLMİŞ durumdadır — ve daha güçlü biçimde

Kullanıcı bota **kendisi yazıyor**. Dahası bağlantının kendisi
(`whatsapp_links`) tek kullanımlık bir eşleştirme koduyla, panelden,
kullanıcının kendi oturumuyla açılıyor (WA2). Yani "bu numaraya yazabilir
miyiz" sorusunun cevabı bir onay kutusundan değil, **kanıtlanmış bir
eşleştirmeden** geliyor. `whatsapp_links.is_active = false` yapmak da
iptal mekanizmasının ta kendisi.

### 2.3 Meta'nın 24 saat kuralı iki yolu ZATEN ayırıyor

Meta Cloud API'de kullanıcının son mesajından sonraki 24 saat içinde serbest
metin gönderilebilir; dışında yalnız **önceden onaylı şablon** gönderilebilir.

* Asistan cevabı **her zaman** o pencerenin içindedir (kullanıcı az önce
  yazdı) → serbest metin, `type: "text"`.
* Bildirim **çoğu zaman** pencerenin dışındadır → şablon gerektirir.

İki yolu tek deftere sokmak, bu ayrımı çalışma zamanında yeniden keşfetmeye
zorlardı. **BUGÜN ŞABLON DESTEĞİ YOKTUR** ve bu açıkça söyleniyor:
`saglayici.MetaBulutSaglayici` yalnız `type: "text"` gönderir. Yani WHATSAPP kanalına
düşen bir bildirim satırı, pencere dışındaysa Meta tarafından reddedilir ve
`GonderimHatasi` olarak kaydedilir. Şablon desteği ayrı bir iştir.

### 2.4 Kuyruk gecikmesi sohbeti bozar

Outbox bir **kuyruktur**: satır yazılır, işçi alır, gönderir. Asistan cevabı
ise kullanıcının az önceki mesajına verilen cevaptır; araya bir kuyruk
turu koymak, "borcum ne kadar" sorusunun cevabını dakikalar sonra
göndermek demektir. Sohbette bu, cevabın **yanlış soruya** ait
görünmesine yol açar.

---

## 3. KANAL ÇİVİSİ: `KANAL_SAGLAYICILARI["WHATSAPP"]`

5.4c'de `KANAL_SAGLAYICILARI` yalnız `PUSH` taşıyordu ve WHATSAPP açıkça
dışarıda bırakılmıştı; gerekçesi o zaman yazılmıştı: bu kanalın gerçek bir
adaptörü yoktu, "TARİHSEL olarak ayardan seçiliyor ve o davranış BU TURDA
KIMILDAMADI".

**Kımıldatan şey artık gerçek bir adaptörün olmasıdır.**

Çivilenmeseydi ölçülen kusur şu olurdu: `notification_provider = "smtp"`
ayarlı bir kurulumda WHATSAPP kanalındaki bir outbox satırı SMTP
adaptörüne giderdi ve o adaptör alıcı alanındaki **telefon numarasını** bir
e-posta adresi sanıp ona mail atmaya çalışırdı — PUSH için ölçülen kusurun
birebir aynısı, yalnız cihaz jetonu yerine numarayla.

Kapı: `tests/test_wa4_bekleyen.py::test_SAGLAYICI_KANALA_CIVILI_ayara_DEGIL`.

---

## 4. SAĞLAYICININ ÜÇ YOLU

`WhatsAppNotificationProvider.send` üç şeyden birini yapar ve **üçü de ayrı
bir şey söyler**:

| Koşul | Sonuç | Anlamı |
|---|---|---|
| `whatsapp_access_token` ya da `whatsapp_phone_number_id` **BOŞ** | `NONE` | Denenmedi, başarısız da olmadı. Ağa **hiç** çıkılmaz. |
| `notification_provider = "simulation"` | `SIMULATED` | Akış koştu, mesaj **dışarı gitmedi**. Terminaldir. |
| Yapılandırılmış | `SENT` | Meta bir `wamid` **döndürdü**. |

**`SENT` yalnız KANIT varken yazılır.** Meta'dan mesaj kimliği gelmeyen bir
2xx bile `GonderimHatasi`dır. Bu, `SimulationNotificationProvider` ve
`PushNotificationProvider` için yıllardır yazılı olan kuralın aynısı:
gerçekten gönderilmemiş bir mesajı "gönderildi" diye raporlamak denetim
izini yalan söyler hâle getirir.

**Varsayılan kurulumda birinci satır koşar.** `.env`de WhatsApp jetonu
olmayan bir kurulumda bu PR hiçbir mesaj göndermez — WA4'ün eklenmesi
tek başına hiçbir kanalı AÇMAZ.

`supports_idempotency = False` ve bu ölçülmüş bir karardır: Meta Cloud
API'nin `messages` ucunda istemci tarafı tekilleştirme anahtarı yoktur.
`True` demek, süresi dolmuş bir lease'in otomatik geri alınmasını açar ve
aynı mesajı kullanıcıya **iki kez** gönderirdi.

---

## 5. BU TURDA YAPILMAYANLAR (iddia edilmiyor)

Dürüstlük için, WA4'ün giden tarafında **olmayan** şeyler:

* **Şablon (template) gönderimi YOK.** Yalnız `type: "text"`. 24 saatlik
  pencere dışına çıkan bildirim Meta tarafından reddedilir.
* **Medya gönderimi YOK.** Giden taraf yalnız düz metin.
* **Teslimat durumu (delivered/read) geri okuması YOK.** Meta bu olayları
  webhook'a yollar; WA1'in ayrıştırıcısı onları sessizce eliyor
  (`gelen_mesajlari_coz` yalnız `messages` düğümüne bakıyor).
* **Asistan cevabını GÖNDEREN çağıran YOK.** WA4 taşıyıcıyı ve adaptörü
  getiriyor; onları çağıracak işçi **WA3'ün işidir**.
* **Kullanıcı başına giden mesaj hız sınırı YOK.** Meta'nın kendi sınırları
  dışında bir tavan uygulanmıyor.
