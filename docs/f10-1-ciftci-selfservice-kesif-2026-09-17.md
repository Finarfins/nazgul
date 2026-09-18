# Faz 10-1 — Çiftçi self-service (ekstre / avans / kantar / makbuz) — KEŞİF + TASARIM

**Ölçüm tarihi:** 2026-09-17 (yazım 2026-09-18'e sarktı) ·
**Taban:** `origin/develop` = `f0e3918` (#142 birleşmesi) ·
**Worktree:** `F:\nazgul-f10-1` · **Dal:** `docs/f10-1-ciftci-selfservice-kesif`
**Kapsam:** SALT OKUMA keşif. Uygulama kodu, göç ve şema DEĞİŞMEDİ.

Her sayı bu taban üzerinde ölçüldü ve `dosya:satır` ile yazıldı.
Ölçülemeyen her iddia **DOĞRULANMADI** etiketi taşır (Ek).

---

## 0. YÖNETİCİ ÖZETİ — üç cümlelik sonuç

1. **Çiftçi tek bir cari DEĞİL, İKİ ayrı caridir ve ikisi birbirine bağlı
   değildir.** Ekstre/çiftlik tarafı `customers` (`farms.customer_id`), avans
   ve müstahsil makbuzu tarafı `suppliers` (`producer_receipts.supplier_id`,
   `supplier_advances.supplier_id`). Brifingin "çiftçiler `customers`
   satırıdır" varsayımı DÖRT niyetin ÜÇÜ için yanlıştır. Kimlik tasarımı bu
   yüzden müşteriye değil **taraf tipine** bağlanmalıdır.
2. **Rıza defteri ZATEN VAR ve WhatsApp'ı ZATEN tanıyor.**
   `notification_consents` tablosu `CONSENT_REQUIRED_CHANNELS`te `WHATSAPP`
   taşıyor ve `PARTY_TYPES` = `{CUSTOMER, SUPPLIER}`. KVKK kapısı sıfırdan
   yazılmamalı; var olan fail-closed kapı çağrılmalı.
3. **Telefon verisi bugünkü hâliyle eşleşmez.** Demo veri kümesindeki 25
   müşterinin 25'i de telefon taşıyor ama 25'i de `consents.normalize_msisdn`
   tarafından REDDEDİLİYOR (13 haneli, TR cep biçimi değil) — oysa
   `whatsapp/telefon.e164` aynı numaraları KABUL ediyor. İki normalleştirici
   demo kümesinin %100'ünde AYRIŞIYOR.

---

## 1. BUGÜNKÜ WA YIĞINI — modül haritası

`backend/app/whatsapp/` altında **15 dosya, 6300 satır** (`wc -l app/whatsapp/*.py`).

| Modül | Satır | Ne yapar | Yazdığı tablo |
|---|---:|---|---|
| `telefon.py` | 53 | `normalize_phone` (ülke kodlu, işaretsiz) + `e164` (10–15 rakam kapısı) | — |
| `cloud_api.py` | 208 | Meta taşıma sınırı; imza HAM BAYT üzerinde, JSON'dan ÖNCE | — |
| `giris.py` | 94 | `gelen_kaydet`: SAVEPOINT'li INSERT, kopya `wamid` yutulur | `whatsapp_inbound` |
| `schema.py` | 537 | Altı tablonun Core tanımı + kapalı durum kümeleri + CHECK'lerin göçle birebir kopyası | — |
| `eslestirme.py` | 795 | Kod üret/tüket, `deneme_say` hız sınırı, **`kimlik_coz`** | `whatsapp_pairing_codes`, `whatsapp_links`, `whatsapp_pairing_attempts` |
| `baglam.py` | 384 | `kimlik_secimi`, `FİRMA LİSTELE`/`FİRMA SEÇ`, aktif firma | `whatsapp_context` |
| `niyet.py` | 1053 | Deterministik niyet çözümü + şablonlu cevap. **Dış model çağrısı YOK** | — |
| `yurutucu.py` | 441 | `VeritabaniYurutucu`: araç adı → gerçek SELECT | — (yalnız okuma) |
| `bekleyen.py` | 909 | Taslak → açık `ONAY` → tek uygulama (tahsilat) | `whatsapp_pending_actions`, `payments` |
| `service.py` | 567 | İşçi: kirala → cevapla → damgala. **Dağıtıcı burada** | `whatsapp_inbound` |
| `saglayici.py` | 349 | Metin gönderir, medya indirir; `metin_gonder` `wamid` döner | — |
| `zamanlayici.py` | 331 | Süreç içi zamanlayıcı (`field_stok_zamanlayici` deseni) | — |
| `kopru.py` | 291 | Harman AI köprüsü, HMAC imzalı. YALNIZ BAĞSIZ numara dalında | — |
| `fatura.py` | 255 | Medyadan fatura ÖZETİ; kayıt AÇMAZ (`ck_wpa_action_type` bugün `IN ('TAHSILAT')`) | — |
| `__init__.py` | 33 | Paket; FastAPI yüklemeyi zorunlu kılmaz | — |

### 1.1 Tablolar ve kiracı sınıfı

| Tablo | Göç | Sınıf | Not |
|---|---|---|---|
| `whatsapp_inbound` | `20260910_0078` | **PLATFORM** | `company_id` YOK — webhook'a gelen mesaj henüz hiçbir firmaya ait değil |
| `whatsapp_pairing_attempts` | `20260910_0078` | **PLATFORM** | Deneme yapan numara henüz bağlı değil |
| `whatsapp_links` | `20260910_0079` | KİRACI | `TENANT_TABLES` üyesi |
| `whatsapp_pairing_codes` | `20260910_0079` | KİRACI | `target_phone` SEC-1 ile `20260912_0082`de eklendi |
| `whatsapp_context` | `20260910_0079` | KİRACI | |
| `whatsapp_pending_actions` | `20260910_0080` | KİRACI | |

### 1.2 PERSONEL-YALNIZ VARSAYIMI TAM OLARAK NEREDE — ölçüldü

`Kimlik` = `(company_id, user_id)`, `eslestirme.py:140-146`. Tek doğum yeri
`eslestirme.py:770`.

`kimlik.user_id` **ONBİR yerde** çözülüyor
(`grep -c "kimlik\.user_id" app/whatsapp/*.py`):

* `baglam.py` — **3** (satır 101, 108, 134): `whatsapp_context` kapsamı
* `bekleyen.py` — **8** (satır 311, 334, 391, 409, 448, 468, 503, 750): taslak
  sahipliği, aktif-taslak tekil indeksi, `payments.created_by`
* `yurutucu.py` — **SIFIR**

`kimlik.company_id` ise **23 yerde** (`baglam` 3, `bekleyen` 13, `yurutucu` 7).

> **Bu ölçümün tasarım sonucu:** yedi okuma aracının HİÇBİRİ `user_id`
> kullanmıyor. Yani **okuma yüzeyi zaten kullanıcıdan bağımsızdır**; personel
> varsayımı yalnız (a) bağlam defterinde ve (b) yazma/taslak defterinde
> yaşıyor. F10-1 salt okuma olduğu için ONBİR çözüm noktasının HİÇBİRİNE
> dokunmak zorunda değiliz — ikinci kimlik türü bunların YANINDAN geçer.

Kimliği üreten zincir (`eslestirme.kimlik_coz`, `eslestirme.py:712-772`) iki
adımlıdır: (1) `whatsapp_links` üzerinde `phone` + `is_active` taraması — bu
sorgu KİRACI YÜKLEMİ TAŞIMAZ ve kiracı nöbetçisinde lisanslı bir istisnadır
(`tests/test_core_tenant_scoping_guard.py::CEKIRDEK_KIRACI_ISTISNALARI`, bugün
**14 girdi**); (2) her aday için `_hedef_dogrula` (`eslestirme.py:246`) —
aktif kullanıcı → aktif firma → O FİRMADAKİ ÜYELİK zinciri.

### 1.3 Dağıtıcı — `service._mesaj_isle` (`service.py:401-511`)

Sıra, satır numaralarıyla:

1. `phone_number_id` savunması → `IGNORED` (`service.py:424`)
2. Boş metin + medya yok → `IGNORED` (`service.py:434`)
3. `e164` kapısı → `IGNORED` (`service.py:443`)
4. **`BAĞLA <KOD>`** — kimlik çözülmeden ÖNCE (`service.py:456`)
5. `FİRMA LİSTELE` / `FİRMA SEÇ` — `baglam.firma_komutu` (`service.py:465`)
6. `baglam.kimlik_secimi` (`service.py:467`):
   * `firma_secimi_gerekli` → `FIRMA_SECIN_MESAJI` (`service.py:468-469`)
   * **`kimlik is None` → `_bagsiz_cevap`** ← *ikinci kimlik türünün gireceği
     TEK yer (`service.py:470-471`)*
   * medya → `fatura.medya_ozeti` (`service.py:484`)
   * aksi → `cevap_uret(db, secim.kimlik, metin)` (`service.py:486`)

`cevap_uret` (`service.py:260-308`) önce YAZMA niyetini dener
(`niyet.tahsilat_coz`), sonra okuma niyetini (`niyet.coz`).

### 1.4 Araç beyaz listesi — YEDİ araç, HEPSİ OKUMA

`niyet.py:471-479`: `cari_durum`, `parca_stok`, `donem_ozeti`,
`alacak_yaslandirma`, `kritik_stok`, `en_cok_satan_parcalar`,
`parca_satis_gecmisi`. Yedisi de firma GENELİ okur; hiçbiri "yalnız bana ait"
kavramını tanımaz.

### 1.5 Hız sınırları — TAM DÖKÜM

`schema.py`den, alıntıyla:

* `PAIRING_PENCERE_DAKIKA = 15`, `PAIRING_PENCERE_SINIRI = 5` (`schema.py:143-144`)
* `PAIRING_CEVAP_SINIRI = 3` — *"Sınırın ALTINDA ama bunun ÜSTÜNDE olan deneme
  İŞLENİR, ama cevap VERİLMEZ … sessiz düşürme, Meta mesaj maliyeti üzerinden
  kurulacak bir masraf saldırısını kapatır."* (`schema.py:145-149`)
* `PAIRING_MAX_ATTEMPTS = 5` (kod SATIRI başına), `PAIRING_OMRU_DAKIKA = 10`
* `BAGLAM_OMRU_DAKIKA = 30`, `PENDING_OMRU_DAKIKA = 15`,
  `PENDING_LEASE_DAKIKA = 5`, `LEASE_DAKIKA = 5`, `MAX_DENEME = 3`

Sayaç `eslestirme.deneme_say` (`eslestirme.py:408-453`) tek deyimlik UPSERT'tir
(PG ve SQLite ayrı dallar); `uq_whatsapp_pairing_attempts_pencere` çakışma
hedefidir. Dış rollback'i aşan ikinci bir kalıcılaştırma
`service._sayaci_kalicilastir` (`service.py:223`) içindedir.

> **ÖLÇÜLEN BOŞLUK:** `app/routers/whatsapp.py` ve `app/whatsapp/service.py`
> içinde `rate_limit|hiz_sinir|RateLimit|limiter` **hiç geçmiyor**. Yani
> **BAĞLANMIŞ bir numaranın normal mesaj yolunda numara başına hız sınırı
> YOKTUR.** Bugün bu kabul edilebilir çünkü bağlı taraf personeldir; çiftçi
> için kabul edilemez (§5.3).

### 1.6 Idempotency

`UniqueConstraint("wamid", name="uq_whatsapp_inbound_wamid")` (`schema.py`).
`wamid` sütununun satır içi yorumundan alıntı (`schema.py:112-114`): *"UNIQUE kısıt idempotency'nin KENDİSİDİR … Hakem
uygulama değil, veritabanıdır — 'önce SELECT sonra INSERT' yarışı yoktur."*
Yazma tarafında ikinci bir kök: `uq_wpa_islem_anahtari` (küresel tekil).

### 1.7 Rıza defteri (KVKK) — ZATEN VAR

`app/notifications/consents.py`. Başlığından üç sözleşme, alıntıyla:

> 1. **Fail-closed.** Rıza kaydı yoksa, `REVOKED` ise, alıcı rıza anındaki
>    numaradan farklıysa ya da numara normalize edilemiyorsa gönderim yapılmaz.
> 2. **Anlık görüntü karar verici değildir (§3.3).** … Her gönderim denemesi —
>    ilk deneme de, her retry de — rızayı yeniden okur.
> 3. **Append-only olay günlüğü.** Her GRANT/REVOKE
>    `notification_consent_events` tablosuna düşer ve versiyon artar.

* `PARTY_TYPES = {"CUSTOMER", "SUPPLIER"}` (`consents.py:39`)
* `CONSENT_SOURCES = {"FORM", "PHONE", "CONTRACT", "IMPORT"}` (`consents.py:40`)
* `CONSENT_REQUIRED_CHANNELS = {"SMS", "WHATSAPP", "EMAIL"}`
  (`notifications/schema.py:72`) — **WhatsApp zaten rıza gerektiren kanal**
* Red gerekçeleri: `NO_RECORD`, `REVOKED`, `RECIPIENT_CHANGED`,
  `RECIPIENT_INVALID`, `CHANNEL_UNKNOWN` (`consents.py:43-47`)
* `notification_consents` ve `notification_consent_events` İKİSİ DE
  `TENANT_TABLES` üyesidir (`tests/test_tenant_scoping_guard.py:131`)
* Ön yüz: `frontend/src/components/NotificationConsentPanel.tsx` (122 satır)
  `WHATSAPP` anahtarını ZATEN çiziyor — ama `EntityDetail.tsx:214` onu
  **yalnız `type==='customer'`** için gösteriyor.

---

## 2. MÜŞTERİ/TEDARİKÇİ TELEFON VERİSİ — ölçüm

### 2.1 Biçim ve genişlik AYRIŞIYOR

| Sütun | Tip | Doğrulama |
|---|---|---|
| `customers.phone` | `String(60)` (`app/core_schema.py:34`) | **YOK** — serbest metin |
| `suppliers.phone` | `String(60)` (`app/core_schema.py:53`) | **YOK** — serbest metin |
| `whatsapp_links.phone` | `String(20)` | `normalize_phone` çıktısı |
| `whatsapp_inbound.sender_phone` | `String(20)` | `normalize_phone` çıktısı |
| `whatsapp_pairing_codes.target_phone` | `String(20)` | `normalize_phone` çıktısı |

`consents.normalize_msisdn` başlığı bunu zaten yazıyor: *"`customers.phone`
doğrulanmamış serbest metindir ('0532 111 22 33', '532-111-22-33',
'bilinmiyor' hepsi kabul edilmiştir)."*

### 2.2 İKİ NORMALLEŞTİRİCİ VE UYUŞMAZLIKLARI — asıl bulgu

Depoda telefonu kanonikleştiren **iki ayrı fonksiyon** var:

* `app/whatsapp/telefon.py::normalize_phone` + `e164` — **gevşek**: rakamları
  süzer, TR kısayolları uygular, `[10, 15]` rakam aralığını kabul eder.
* `app/notifications/consents.py::normalize_msisdn` — **sıkı**: TR için `90`
  + 10 hane, abone `5` ile başlamalı; aksi hâlde `None`.

Ölçüm (`seed_demo_data.py:94` biçimi, 25 müşteri):

```
demo müşteri sayısı       : 25
telefonu olan             : 25   (%100)
ayrık ham numara          : 25
ayrık normalize_phone     : 25
örnek ham                 : +90 531 5501 1001
normalize_phone           : 9053155011001      (13 RAKAM)
telefon.e164              : +9053155011001     -> KABUL
consents.normalize_msisdn : None               -> RED
```

Tedarikçi tarafı da aynı (`seed_demo_data.py:116`, `+90 212 44xx 20xx` → 13
rakam) — ama tedarikçide kusur İKİ katlıdır: `212` bir SABİT HAT alan
kodudur ve `normalize_msisdn` aboneyi `5` ile başlamaya zorlar
(`consents.py:87`). Yani hane sayısını 12'ye düzeltmek tedarikçi tohumunu
GEÇİRMEZ; tohum ayrıca `5xx` cep önekine çevrilmelidir.

> **Demo veri kümesinin %100'ünde iki normalleştirici ayrışıyor.** Ayrıca
> üretilen 13 haneli numara geçerli bir TR cep numarası DEĞİLDİR; `e164`'ün
> onu kabul etmesi `[10,15]` aralığının bilinçli genişliğindendir (yurt dışı
> numarası da mümkün olmalı), bir kusur değil — ama **çiftçi eşleştirmesinin
> hedef doğrulaması için `e164` YETERSİZDİR.**

### 2.3 Kopya senaryoları — kural önerisi

Bugünkü şemada `customers.phone`/`suppliers.phone` üzerinde HİÇBİR tekillik
kısıtı yoktur (`core_schema.py`de yalnız `ix_customers_company_active_name`).
Yani aynı numara aynı firmada iki cariye yazılabilir; canlı veride bunun
sıklığı **DOĞRULANMADI** (Ek).

Önerilen kural — `whatsapp_links`in bugünkü kuralının BİREBİR aynısı, çünkü
gerekçesi de aynı:

1. **Bir numara → bir firmada EN FAZLA BİR aktif taraf bağlantısı.** Hakem
   uygulama sorgusu değil, kısmi UNIQUE indeks olmalı:
   `(company_id, phone) WHERE is_active` — `uq_whatsapp_links_aktif_numara`
   ile aynı desen.
2. **Aynı numara FARKLI firmalarda aktif OLABİLİR.** Gerekçe `20260910_0079`
   başlığında zaten yazılı ve çiftçi için daha da güçlüdür: bir çiftçi iki
   ayrı alım merkezine ürün verir.
3. **Firmalar arası belirsizlikte SORULUR, seçilmez.** `baglam.py`nin
   `FİRMA LİSTELE`/`FİRMA SEÇ` mekanizması aynen kullanılır. `kimlik_coz`un
   başlığındaki cümle burada da geçerli: *"belirsizlikte rastgele seçim,
   YANLIŞ tenant'ın verisini dönmek demektir."*
4. **Aynı numara aynı firmada HEM müşteri HEM tedarikçi tarafına bağlıysa bu
   bir belirsizlik DEĞİLDİR** — aynı çiftçinin iki yüzüdür ve §3'teki tasarım
   ikisini TEK bağlantıda taşır (§3.2).

---

## 3. KİMLİK TASARIMI

### 3.1 ÖN BULGU — çiftçi iki ayrı caridir

Ölçüm:

| Olgu | Tablo | Taraf sütunu | Kaynak |
|---|---|---|---|
| Çiftlik sahipliği | `farms` | **`customer_id`** (nullable) → `customers.id` | `alembic/versions/20260807_0044_farm_management_v1.py:85` |
| Müstahsil makbuzu | `producer_receipts` | **`supplier_id`** (NOT NULL) | `alembic/versions/20260905_0070_mustahsil_makbuzu.py:145` |
| Üretici avansı | `supplier_advances` | **`supplier_id`** | `app/routers/avans.py:241` |
| Kantar fişi | `field_harvest_tickets` | **TARAF SÜTUNU YOK** | `alembic/versions/20260904_0069_kantar_fisi.py:134-141` |

`farms` göçündeki yorum bunu açıkça söylüyor: *"Çiftliğin sahibi bir cari
olabilir (kendi işletmemiz ise NULL)."* — ve bu cari `customers`tır.

Kantar fişinin TEK bağı `harvest_id` → `field_harvests` → `season_id` → … →
`farms.customer_id`dir. `buyer_name` (`String(180)`) serbest metindir ve
ALICIYI anlatır, üreticiyi değil. Fişin bir tedarikçiye ulaştığı tek yol
`producer_receipts.ticket_id`dir (nullable,
`20260905_0070_mustahsil_makbuzu.py:154`) — yani **makbuzu kesilmemiş bir
kantar fişinin taraf sahibi YOKTUR.**

> **Sonuç:** Yalnızca `customer_id` taşıyan bir bağlantı tablosu, dört niyetin
> ÜÇÜNÜ (avans, kantar, makbuz) cevaplayamaz. Brifingin
> `whatsapp_customer_links` önerisi bu ölçümle DÜŞER.

### 3.2 SEÇENEKLERİN KARŞILAŞTIRMASI

**A) `whatsapp_links`e nullable `customer_id` + CHECK (tam olarak biri)**

* `-` `user_id` bugün `NOT NULL`; nullable yapmak ONBİR çözüm noktasının
  (§1.2) hepsini yeniden akıl yürütmeye zorlar — `bekleyen.py`de 8'i yazma
  yolundadır ve orada `None` bir `payments.created_by` demektir.
* `-` `uq_whatsapp_links_aktif_numara` `(company_id, phone) WHERE is_active`
  anahtarı personel ve çiftçiyi AYNI kovaya sokar: bir çiftçi kendi
  numarasıyla bağlıyken aynı numaradan personel bağlanamaz.
* `-` `whatsapp_pending_actions`in bileşik FK'si
  `(company_id, whatsapp_link_id) → whatsapp_links(company_id, id)` çiftçi
  satırlarına da asılabilir hâle gelir — çiftçi hiçbir zaman taslak açmasa
  bile şema bunu YASAKLAMAZ.
* `+` Tek tablo, tek `TENANT_TABLES` üyesi (pin deltası yok).
* `-` `customer_id`, tedarikçi tarafını (üç niyet) çözemez.

**B) Yeni `whatsapp_party_links` — ÖNERİLEN**

`notification_consents`in polimorfik biçimini AYNEN yansıtır:

```
whatsapp_party_links
  id, company_id, party_type, party_id, phone,
  is_active, created_at, updated_at, created_by
  CHECK party_type IN ('CUSTOMER','SUPPLIER')    -- consents.PARTY_TYPES ile birebir
  UNIQUE (company_id, id)                        -- bileşik FK hedefi (0062 kuralı)
  INDEX ix_whatsapp_party_links_phone (phone)
  UNIQUE (company_id, phone) WHERE is_active     -- kısmi; 0079 deseni
```

Yanında `whatsapp_party_pairing_codes` (0079'un yedi CHECK'i ve
`uq_wpc_aktif_kod` kısmi tekili, taraf üçlüsüyle yeniden yazılmış).

* `+` Dört niyetin dördünü de karşılar (müşteri VE tedarikçi).
* `+` Personel tarafına SIFIR risk: ONBİR `kimlik.user_id` çözümünün hiçbiri,
  `whatsapp_pending_actions`in bileşik FK'si ve WA2/WA4 kapılarının hiçbiri
  değişmez.
* `+` **Bileşik FK için ön koşul YOK — İKİ tarafta da.**
  `uq_customers_company_id` ZATEN VAR
  (`20260726_0026_harman_season_scheduling.py:146`).
  `uq_suppliers_company_id` de ZATEN VAR: göç
  `20260905_0070_mustahsil_makbuzu.py:103` (`UQ_TEDARIKCI`) ve `:133-136`
  (`inspect` korumalı `create_unique_constraint(UQ_TEDARIKCI, ["company_id",
  "id"])`); `supplier_advances` ona bugün bileşik FK ile asılı
  (`20260906_0071_avans_tescil_vergi.py:199`,
  `fk_supplier_advances_supplier_same_company`). `20260914_0085`in başlığı
  ikisini birlikte sayıyor (`customers` → 0026, `suppliers` → 0070).
  E4b-1'in `invoice_items` için açmak zorunda kaldığı ön koşul BURADA YOK;
  göç `0090` hiçbir tedarikçi/müşteri kısıtı AÇMAZ.
  *Not:* polimorfik `party_id` gerçek bir FK ALAMAZ — `notification_consents`
  ile AYNI durumdur ve aynı çözüm kullanılır (CHECK + geri-yükleme
  sınıflandırıcısı).
* `+` **Geri yükleme (5.1c) makinesi hazır.** `app/kiraci_geri_yukleme.py:377`
  `("notification_consents", "party_id"): ("party_type", _TARAF)` girdisini
  ZATEN taşıyor — taraf-ayırt edici yumuşak referans sınıfı MEVCUT. Yeni tablo
  için tek satır eklenir; yeni bir sınıflandırıcı türü gerekmez.
* `-` İKİ yeni `TENANT_TABLES` üyesi → 125 → 127 (§6 pin deltası).
* `-` 0079'un yedi CHECK'i yeniden yazılır. Bu bir kopya DEĞİL, deponun
  yerleşik geleneğidir: `schema.py` her göç CHECK'ini birebir yeniden yazar ve
  gerekçesi orada yazılı (*"Alembic ile kurulan şema ile
  `metadata.create_all()` ile kurulan şema güvenlik anlamı bakımından
  AYRIŞMAMALIDIR"*).

### 3.3 TAVSİYE

**B'yi seçin, ama `party_type`ı `CUSTOMER`/`SUPPLIER` olarak açın — sadece
`CUSTOMER` değil.** Gerekçe §3.1'in ölçümüdür: aksi hâlde F10-1c (kantar +
makbuz) ve avans, tasarımı ikinci kez açmayı gerektirir.

Eşleştirme akışı brifingdeki gibidir ve bugünkü personel akışının BİREBİR
aynısıdır (`app/routers/whatsapp.py:312-378`):

1. Personel, cari kartından hedef numarayı yazarak kod üretir. Kod
   `target_phone`a BAĞLIDIR (SEC-1, göç `20260912_0082`) — sızan bir kod başka
   numaradan kullanılamaz.
2. Düz kod YALNIZ o cevapta, BİR KEZ döner; veritabanında SHA-256 özeti durur.
3. Çiftçi bot numarasına `BAĞLA <KOD>` yazar.
4. **Çiftçi ASLA kendi kendine kaydolmaz.** Kod üretimi bir ERP eylemidir.

---

## 4. NİYET KÜMESİ v1 — DÖRT OKUMA, SIFIR YAZMA

Ortak kural: **çiftçi YALNIZ kendi satırını görür.** Araç, `Kimlik`ten değil
`CiftciKimligi`nden `(company_id, party_type, party_id)` alır ve her sorgu o
üçlüyle koşar. Serbest metinden cari adı ÇIKARILMAZ — bugünkü `cari_durum`un
`musteri` argümanı çiftçi yolunda HİÇ YOKTUR.

### (a) EKSTRE / BAKİYE

* **Çağrılacak mevcut kod:** `app/statement.py:348`
  `build_statement(db, cid, entity_type, entity_id, date_from, date_to, *, rol="")`.
  Aynı fonksiyon `customer` ve `supplier` tarafını da kurar
  (`routers/customers.py:175`, `routers/finance.py:593`) — kopyalanacak bir
  bakiye formülü YOK.
* **Maskeleme BEDAVA GELİYOR:** `rol` VARSAYILANI MASKELİDİR ve başlığı bunu
  yazıyor: *"yarın eklenen bir çağıran `rol` geçirmeyi unutursa sonuç GİZLİ
  olur, sızıntı DEĞİL."* Çiftçi yolu `rol` GEÇİRMEZ → başlıktaki VKN/adres/
  e-posta maskelenir. Bu doğru davranıştır: çiftçi kendi bakiyesini görmeli,
  firmanın cari kartı görünümünü değil.
* **Kırpılan:** `entity_id` çiftçinin KENDİ satırıdır; başka cari ADI hiçbir
  cevapta geçmez.
* **Cevap (≤5 satır):**

  ```
  Sayın <ad>, 17.09.2026 itibarıyla bakiyeniz:
  Borç 128.400,00 TL · Alacak 96.000,00 TL
  NET: 32.400,00 TL borç
  Son hareket: 12.09.2026
  Detay için "EKSTRE EYLÜL" yazabilirsiniz.
  ```

* **Eklenecek kökler:** `EKSTRE`, `HESAP OZETI`.
  ⚠ `HESAP` tek başına EKLENMEZ — `SORU_SOZLUGU`nda zaten durak kelimedir
  (`niyet.py:80`) ve eklemek onu hem durak hem kök yapardı.
  `CARI_KOKLER` (`BORC`, `BAKIYE`, `CARI`, `VERESIYE`, `niyet.py:132`) YENİDEN
  KULLANILIR ama çiftçi dalında **terim aranmaz**.

### (b) AVANS DURUMU

* **Çağrılacak mevcut kod:** `app/routers/avans.py:278`
  `GET /api/suppliers/{supplier_id}/advances` — `remaining_amount>0` süzgeci
  dâhil (`avans.py:298`). Taraf `suppliers`tır.
* **Çiftçinin görebileceği:** kendi avansları — tutar, kalan, tarih ve hangi
  makbuza mahsup edildiği (`receipt_id`).
* **Kırpılan:** `payment_id`, `note` (personel notu olabilir), başka
  tedarikçinin hiçbir satırı.
* **Cevap (≤5 satır):**

  ```
  Avans durumunuz (Sungur Tarım):
  Toplam alınan: 75.000,00 TL
  Mahsup edilen: 45.000,00 TL
  KALAN AVANS: 30.000,00 TL
  Son avans 02.09.2026.
  ```

* **Eklenecek kökler:** `AVANS`, `KAPORA`, `PESINAT`.

### (c) KANTAR — BUGÜN NE VAR

Ölçüm: **"kantar fişi" tek bir tablodur — `field_harvest_tickets`** (göç
`20260904_0069_kantar_fisi.py`). Kesinti satırları
`field_harvest_ticket_deductions`tadır. `machine_hour_readings` ile HİÇBİR
ilgisi yoktur.

Fişin türetilen neti KOPYALANMAZ, İTHAL EDİLİR: `routers/mustahsil.py:278`
`_fis_neti` → `farm._turetilmis_net` (`mustahsil.py:279-283`'teki yorum:
*"bileşim kuralı tek yerde durmalı"*).

⚠ **Taraf sütunu YOK (§3.1).** Bu yüzden v1'de kantar niyeti şöyle
kapsamlanmalıdır:

> Çiftçiye YALNIZ `producer_receipts.ticket_id` üzerinden KENDİ makbuzuna
> bağlanmış fişler gösterilir.

Böylece taraf sorusu makbuzun `supplier_id`si ile kesin olarak cevaplanır ve
"sahipsiz fiş" hiç sorgulanmaz. Parsel zinciri
(`ticket → harvest → season → parcel → farm.customer_id`) v1 KAPSAMI
DIŞINDADIR: zincir beş JOIN'dir, `farms.customer_id` nullable'dır ve
`customers`a gider — yani makbuzun `suppliers`ıyla ÇAKIŞMAZ.

* **Kırpılan:** `buyer_name` (alıcı kimliği), `notes`.
* **Cevap (≤5 satır):**

  ```
  Son kantar fişiniz (#4412, 11.09.2026):
  Brüt 24.180 kg · Kesinti %2,5
  NET: 23.575,50 kg
  Makbuz: MM-2026-000318 (kesildi)
  Daha eskisi için "KANTAR LISTE" yazın.
  ```

* **Eklenecek kökler:** `KANTAR`, `TARTI`, `TONAJ`.
  ⚠ `FIS` EKLENMEZ: `SORU_SOZLUGU`ndaki `FATURA`/`KOD`/`NUMARA` ile aynı kovaya
  düşer ve ek toleransı 6 ile `FIYAT`ı yutma riski taşır. `KILO`/`ADET` de
  EKLENMEZ — `ADET` zaten `STOK_KOKLER`dedir (`niyet.py:133`).

### (d) MAKBUZ (MÜSTAHSİL)

* **Çağrılacak mevcut kod:** `app/routers/mustahsil.py:450`
  `GET /api/producer-receipts?supplier_id=…` (liste) ve `:491`
  `GET /api/producer-receipts/{id}` (tekil; borsa tescili tekil görünüme
  gömülüdür, listeye gömülmez — N+1 gerekçesi `mustahsil.py:493-495`).
* **Çiftçinin göreceği alanlar:** `receipt_no`, `issued_at`, `gross_amount`,
  `withholding_total`, `social_security_total`, `net_payable`, `status`.
* **Kırpılan:** `purchase_id` (firmanın iç alış belgesi), `note`.
* **Taslaklar GÖSTERİLMEZ:** yalnız `status='issued'`. Kesilmemiş bir kağıdı
  çiftçiye göstermek, henüz verilmemiş bir sözü vermek olurdu —
  `list_producer_receipts` başlığının tarih süzgeci için yazdığı gerekçenin
  aynısı.
* **Cevap (≤5 satır):**

  ```
  Müstahsil makbuzunuz MM-2026-000318 (11.09.2026):
  Brüt 306.481,50 TL
  Stopaj 6.129,63 TL · Bağ-Kur 61,30 TL
  NET ÖDENECEK: 300.290,57 TL
  PDF için alım merkezinize başvurun.
  ```

* **Eklenecek kökler:** `MAKBUZ`, `MUSTAHSIL`.
  ⚠ `FATURA` EKLENMEZ — `SORU_SOZLUGU`nda durak kelimedir (`niyet.py:80`).

### (e) FİYAT SORUSU — KARAR

**Çiftçi KENDİ fiyatını görür.** Gerekçe: `producer_receipt_items.unit_price`
zaten çiftçinin elindeki KAĞITTA yazılıdır; WhatsApp'tan gizlemek gizlilik
sağlamaz, yalnız kanalı işe yaramaz kılar. Başka çiftçinin fiyatı ve firmanın
satış fiyatı HİÇBİR cevapta geçmez (çiftçi araçlarının hiçbiri
`products.sale_price` okumaz).

---

## 5. GÜVENLİK

### 5.1 Çiftçi personel araçlarına ASLA ULAŞAMAZ — kapı yeri

Dağıtım `service.py:467-486`da kimlik TÜRÜNE göre dallanır. Önerilen biçim:

```
secim = baglam.kimlik_secimi(db, telefon, simdi=an)      # PERSONEL (bugünkü)
if secim.kimlik is not None:        -> cevap_uret(...)    # 7 personel aracı
elif (ciftci := ciftci_secimi(db, telefon)) is not None:
                                    -> ciftci_cevap(...)  # 4 çiftçi aracı
else:                               -> _bagsiz_cevap(...) # bugünkü
```

**Personel dalı ÖNCE denenir ve bu bilinçlidir:** bir numara ikisine birden
bağlıysa (muhasebeci aynı zamanda müşteri), personel kimliği DAHA DAR bir
zincirden geçmiştir (`_hedef_dogrula`: aktif kullanıcı + aktif firma + üyelik)
ve daraltıcı olan kazanmalıdır.

**İki araç kümesi ASLA aynı sözlüğü paylaşmaz.** `ARAC_BEYAZ_LISTESI`
(`niyet.py:471`) DEĞİŞMEZ; çiftçi araçları AYRI bir `CIFTCI_BEYAZ_LISTESI`
frozenset'inde yaşar. Bugünkü `niyet.dene` zaten beyaz liste dışı aracı
`KeyError` ile reddediyor (`niyet.py:525-526`) ve `cevap_uret` onu kapsam
mesajına çeviriyor (`service.py:296-299`) — aynı kapı çiftçi tarafında da
kurulur.

**`tahsilat_coz` çiftçi dalında HİÇ ÇAĞRILMAZ.** Bugün `cevap_uret`in İLK
adımıdır (`service.py:276`); çiftçi yolunda o adım YOKTUR ve
`whatsapp_pending_actions`e çiftçiden HİÇBİR satır düşemez.

### 5.2 Kapsam dışı mesaj

`niyet.KAPSAM_MESAJI` (`niyet.py:216-222`) personelin dört yeteneğini sayar ve
çiftçiye yanlış bir söz verir ("stok", "tahsilat"). Çiftçi için AYRI bir
`CIFTCI_KAPSAM_MESAJI` gerekir:

```
Bu kanaldan şunları sorabilirsiniz: bakiye/ekstre, avans durumu,
kantar fişi ve müstahsil makbuzu. Çıkmak için DUR yazın.
```

### 5.3 Numara başına hız sınırı — YENİ, ZORUNLU

§1.5'te ölçüldü: bağlı numaranın normal mesaj yolunda sınır YOKTUR. Personel
için kabul edilebilir, çiftçi için değil (dış taraf, sayısı çok, her cevap
Meta'ya ücretli bir mesaj).

Öneri: `whatsapp_pairing_attempts`in KENDİ desenini yeniden kullanın —
platform tablosu, sabit pencere, tek deyimlik UPSERT. Yeni tablo
`whatsapp_message_attempts` (PLATFORM, `company_id` YOK) ya da mevcut tabloya
bir `kind` sütunu. **Tavsiye: yeni tablo.** Gerekçe: mevcut tablonun
`uq_whatsapp_pairing_attempts_pencere` anahtarı `(phone, window_start)`tır;
`kind` eklemek anahtarı değiştirmek, yani var olan eşleştirme sınırını bir göç
boyunca zayıflatmak demektir.

Sayılar `PAIRING_*` ile aynı büyüklük sınıfında: **pencere 15 dk, sınır 20
mesaj, cevap sınırı 15.** Sınırın ÜSTÜ İŞLENİR ama CEVAPLANMAZ —
`PAIRING_CEVAP_SINIRI`nin gerekçesi (`schema.py:145-149`) burada aynen
geçerlidir.

### 5.4 Rıza (KVKK)

**Yeni bir rıza defteri AÇMAYIN.** §1.7'de ölçüldü: `notification_consents`
`WHATSAPP` kanalını ve `CUSTOMER`/`SUPPLIER` taraflarını ZATEN tanıyor, üç
sözleşmesi (fail-closed, anlık görüntü karar verici değil, append-only) tam
olarak burada gereken davranıştır.

Akış:

1. `BAĞLA <KOD>` başarılı olduğunda bağlantı satırı açılır AMA ilk ERP
   cevabından ÖNCE `consents.evaluate_consent(...)` çağrılır.
2. `NO_RECORD` → ERP verisi DÖNMEZ; KVKK metni + "EVET/HAYIR" sorulur.
   `EVET` → `set_consent(..., source="PHONE", granted=True)`; olay satırı
   `notification_consent_events`e düşer ve versiyon artar.
3. Her cevapta rıza YENİDEN okunur (sözleşme 2). `recipient_snapshot` numarası
   değişmişse `RECIPIENT_CHANGED` → fail-closed.
4. `DUR` / `İPTAL` → `set_consent(granted=False)` **VE** bağlantı satırı
   `is_active=False`. **İKİSİ BİRDEN**, çünkü ikisi iki ayrı soruyu
   cevaplıyor: rıza "mesaj gönderebilir miyiz", bağlantı "bu numara kim".
   Yalnız rızayı çekmek, numarayı hâlâ çözülebilir bırakırdı.

`DUR`/`İPTAL` sözcükleri `bagla_ayristir` gibi TAM EŞLEŞME regex'iyle
çözülmeli, serbest cümleden çıkarılmamalıdır (`eslestirme.py:217`in deseni).

### 5.5 Denetim izi

`app/activity_log.py` bugün sekiz WhatsApp olayı taşıyor (`:119-121`,
`:136-140`) ve `ACTION_TYPES` KAPALI bir kataloktur.

**Aktör sorunu:** `log_activity(db, cid, actor_user_id, ...)` bir
`app_users.id` bekler; çiftçinin böyle bir kimliği YOKTUR.

Tavsiye: **aktör `NULL`, varlık tipi yeni.** `activity_logs.user_id`
`kiraci_geri_yukleme.KULLANICI_SUTUNLARI`nda zaten yumuşak referanstır
(`kiraci_geri_yukleme.py:138`) — `NULL` onu bozmaz. Yeni varlık tipi
`whatsapp_party` açılır (`whatsapp_pending`in yanına, `activity_log.py:256`) ve
kaynak KİMLİĞİ `whatsapp_party_links.id`dir. Platform olay defterine yazmayın:
olay bir FİRMAYA aittir.

Eklenecek eylem türleri (kapalı kataloğa):
`party.whatsapp_pairing_code_created`, `party.whatsapp_link_activated`,
`party.whatsapp_link_stopped`, `party.whatsapp_consent_granted`,
`party.whatsapp_consent_revoked`.

**Sorgu içeriği ASLA loglanmaz** — bugünkü gelenek budur
(`routers/whatsapp.py:347-349`: *"Kod, özet, telefon ve mesaj metni
YAZILMAZ."*).

---

## 6. PR BÖLÜNMESİ VE PİN DELTALARI

### 6.0 TABAN — `f0e3918` üzerinde ÖLÇÜLDÜ

| Pin | Değer | Yer |
|---|---:|---|
| Rota işlemi | **421** | `tests/test_route_security_contracts.py:708` |
| Rota yolu | **327** | `tests/test_route_security_contracts.py:709` |
| GET envanteri | **201** | `tests/test_route_get_permission_inventory.py:598` |
| Kimlik doğrulamalı | **408** | `tests/test_authorization_population_reconciliation.py:408` |
| `read` | **87** | aynı dosya `:409` |
| `undeniable` | **97** | aynı dosya `:410` |
| `TENANT_TABLES` | **125** | **BEŞ dosyada YEDİ çivi** + tanım dosyası = ALTI dosya (aşağıda) |
| `pg_twins.txt` | **134** satır (f0e3918'de) | `tests/pins/pg_twins.txt` |
| `alt_surec_sql.txt` | **137** satır | `tests/pins/alt_surec_sql.txt` |
| `cari_alan_envanteri.txt` | **56** satır | `tests/pins/cari_alan_envanteri.txt` |
| Alembic başı | **`20260915_0089`** | **ON ÜÇ dosyada ON DÖRT çivi** (aşağıda) |
| Core sorgu envanteri | **233** (select 158 / update 63 / delete 12) | `tests/test_core_query_inventory.py:1255-1256` |
| Envanter parmak izi | **`494f7b8b…`** | aynı dosya `:1354` |
| Core kiracı ifadesi | **191** | `tests/test_core_tenant_scoping_guard.py:2087` |
| Kiracı istisnası | **14** girdi | aynı dosya `:1723` |

Bu bölümdeki HER sayı **`f0e3918` üzerinde ölçüldü** (`git grep … f0e3918 --
backend`; `tests/` değil, `backend/`in TAMAMI). `origin/develop` o günden beri
ilerledi — ör. `pg_twins.txt` `bf8e73f`de (#138) zaten **135**'tir ve bugün
inecek PR'larla 136+ olacaktır.

**`TENANT_TABLES` == 125 — BEŞ dosyada YEDİ donmuş sayım** (`\b125\b` ve
`TENANT_TABLES` ile grep'le bulundu, sayılmadı):

* `len(TENANT_TABLES) == 125` DÖRT kez: `tests/test_sec6_ip_limitleri.py:418`,
  `tests/test_wa1_ingress.py:289`, `tests/test_wa2_eslestirme.py:319`,
  `tests/test_wa4_bekleyen.py:251`.
* Dışa aktarım sayımları ÜÇ kez, tek dosyada:
  `tests/test_kiraci_disa_aktarim.py:497` (`len(gorulen) == 125`), `:515`
  (`len(sira) == 125 and len(set(sira)) == 125`), `:746`
  (`len(ndjson) == 125`). Bunlar `TENANT_TABLES` adını ANMAZ, şemadan türer —
  yalnız ad grep'i onları KAÇIRIR.

Tablonun kendisi `tests/test_tenant_scoping_guard.py:41`de donmuş bir
frozenset'tir. Yeni tenant tablosu toplam **ALTI** dosyaya dokunur (5 çivi
dosyası + tanım). `backend/` kökündeki PG ikizlerinde (`test_wa1_ingress_
postgresql.py`, `test_wa2_eslestirme_postgresql.py`) ad geçer ama 125 çivisi
YOKTUR.

**Alembic başı `20260915_0089` — ON ÜÇ dosyada ON DÖRT çivi:**

* `backend/` kökündeki PG ikizleri (DOKUZ; SQLite hattı bunları ATLAR):
  `test_1b_a_alis_lot_postgresql.py:401`, `test_cs1_cek_senet_postgresql.py:43`,
  `test_cs2_cek_senet_cari_postgresql.py:45`,
  `test_e1_efatura_sertlestirme_postgresql.py:52`,
  `test_e4a_despatch_notes_postgresql.py:72`,
  `test_e4b1_kismi_sevk_postgresql.py:47`,
  `test_e4b2_irsaliye_yaniti_postgresql.py:54` (`BAS`; aynı dosyanın `:51`
  `GOC`u kendi göçüdür, baş DEĞİL), `test_h17_auth_rate_limits_index_postgresql.py:36`,
  `test_wa3_worker_postgresql.py:471`.
* `tests/` altında (DÖRT dosya, BEŞ çivi): `test_e1b_plantback.py:117`,
  `test_e2_tedavi_arinma.py:152`, `test_e3_karantina.py:205`,
  `test_goc_zinciri.py:424` ve `:511`.

Çivi OLMAYANLAR: `tests/test_e4b2_irsaliye_yaniti.py:95` 0089 göç DOSYASININ
metnini denetler (`'revision = "20260915_0089"' in kaynak`), başı değil;
`test_goc_zinciri.py:449` (`0057`nin atalarında `"20260915_0089"` YOK
iddiası) baş değişince de doğru kalır.

> ⚠ **PARALEL PR UYARISI:** bu sayılar `f10-1` yazılırken başka PR'lar
> birleştikçe ÇÜRÜR. Her dilim, dalını kestiği andaki `origin/develop`
> üzerinde YENİDEN ölçmelidir.

### 6.1 F10-1a — model + göç `0090` + eşleştirme ucu

* Göç `20260918_0090_whatsapp_taraf_baglantisi`: `whatsapp_party_links` +
  `whatsapp_party_pairing_codes`; her ikisi `UNIQUE(company_id, id)` taşır.
  Müşteri/tedarikçi tarafında AÇILACAK kısıt YOK (`uq_customers_company_id`
  0026, `uq_suppliers_company_id` 0070 — §3.2).
* `app/whatsapp/schema.py`: iki tablonun Core tanımı, CHECK'ler göçle birebir.
* `app/whatsapp/taraf.py` (yeni): `kod_uret` / `kod_kullan` / `taraf_coz` —
  `eslestirme.py`nin desenini izler, gövdesini KOPYALAMAZ.
* Uçlar: `POST /api/whatsapp/party-pairing-codes`,
  `DELETE /api/whatsapp/party-pairing-codes/{id}`,
  `GET /api/whatsapp/party-links`, `DELETE /api/whatsapp/party-links/{id}`.

**Pin deltası:**

| Pin | 0090 sonrası |
|---|---|
| Rota işlemi / yolu | 421 → **425** / 327 → **331** |
| GET envanteri | 201 → **202** |
| Kimlik doğrulamalı | 408 → **412** |
| `TENANT_TABLES` | 125 → **127** (ALTI dosya: tanım + beş dosyada yedi çivi) |
| Alembic başı | `0089` → `20260918_0090` (ON ÜÇ dosyada ON DÖRT çivi; dokuzu PG ikizi) |
| Core envanteri | +~8 (ölçülecek), parmak izi YENİDEN TÜRETİLİR |
| Core kiracı ifadesi | 191 → +~8 |
| `pg_twins.txt` | **+1** (f0e3918'de 134 → 135; `bf8e73f`de zaten 135 — dilim kendi tabanında ölçer) |
| Kiracı geri yükleme | `notification_consents` girdisiyle aynı biçimde taraf girdisi (§3.2) |
| `cari_alan_envanteri.txt` | ⚠ yeni uç `phone` döndürüyorsa büyür |

**Testler:** `tests/test_f10_1a_taraf_baglantisi.py` (SQLite) +
`test_f10_1a_taraf_baglantisi_postgresql.py` (PG ikizi; `pg_twins.txt`e bir
sıralı satır). PG ikizi ZORUNLU: kısmi UNIQUE indeksin `WHERE` yüklemi iki
lehçede ayrı üretilir.

### 6.2 F10-1b — kimlik dağıtımı + ekstre + avans

* `service._mesaj_isle`e §5.1'in dalı.
* `app/whatsapp/ciftci_niyet.py` + `ciftci_yurutucu.py`.
* Rıza kapısı (§5.4) ve `DUR`/`İPTAL`.
* Hız sınırı (§5.3) — **göç gerektirir** (`whatsapp_message_attempts`,
  PLATFORM). Ayrı göç `0091`.

**Pin deltası:** yeni ROTA YOK (yüzey WhatsApp'tır) → 421/327/201/408
DEĞİŞMEZ. `TENANT_TABLES` DEĞİŞMEZ (yeni tablo PLATFORM). Core envanteri +~10,
kiracı ifadesi +~10, parmak izi yeniden. Alembic başı `0090` → `0091`
(ON ÜÇ dosyada ON DÖRT çivi — F10-1a'nın PG ikizi de başı çivilerse ON DÖRT
dosya).

**Testler:** `test_f10_1b_ciftci_ekstre_avans.py` + PG ikizi. En az üç kapı:
(1) çiftçi kimliği personel aracına ULAŞAMAZ; (2) komşu firmanın aynı adlı
çiftçisi (kiracı yalıtımı —
`test_wa3_worker.py::test_KIRACI_YALITIMI_ayni_ad_komsu_firmada` kurgusu);
(3) rızasız numara ERP verisi ALMAZ.

### 6.3 F10-1c — kantar + makbuz

* İki araç daha; `producer_receipts.ticket_id` üzerinden fiş erişimi (§4c).
* Göç YOK.

**Pin deltası:** rota pinleri DEĞİŞMEZ. `TENANT_TABLES` DEĞİŞMEZ. Core
envanteri +~6, kiracı ifadesi +~6, parmak izi yeniden. Alembic başı DEĞİŞMEZ.

**Testler:** `test_f10_1c_kantar_makbuz.py` + PG ikizi. PG ikizi ZORUNLU:
`_fis_neti` `NUMERIC` aritmetiği yapar ve SQLite'ta gizlenen bir kesinlik farkı
burada görünür.

### 6.4 F10-1d — ön yüz (dükkan)

* `frontend/src/pages/EntityDetail.tsx` (243 satır): "WhatsApp bağlantısı"
  kartı. `NotificationConsentPanel` **bugün yalnız `type==='customer'`** için
  çiziliyor (`EntityDetail.tsx:214`) — tedarikçi tarafına da açılmalı, yoksa
  çiftçinin makbuz/avans yüzünde rıza anahtarı GÖRÜNMEZ.
* `types.gen.ts` YENİDEN ÜRETİLİR (sözleşme sapma kapısı).

**Pin deltası:** backend pinleri DEĞİŞMEZ. `types.gen.ts` farkı BOŞ olmalı
(uçlar F10-1a'da zaten inmiş olduğu için).

---

## 7. ŞEFE AÇIK KARARLAR (6)

**K1 — Çiftçi hangi caridir?**
Ölçüm: ekstre `customers`, avans+makbuz `suppliers`, kantarın tarafı YOK
(§3.1).
→ **Tavsiye: `party_type ∈ {CUSTOMER, SUPPLIER}`, `notification_consents`in
sözlüğüyle birebir.** Yalnız-müşteri tasarım dört niyetin üçünü çözemez.

**K2 — Hangi telefon normalleştiricisi kanoniktir?**
İkisi demo kümesinin %100'ünde ayrışıyor (§2.2).
→ **Tavsiye: eşleştirme HEDEFİNİN doğrulaması `consents.normalize_msisdn`
(sıkı) olsun; SAKLANAN biçim `telefon.normalize_phone` kalsın** (çünkü
`whatsapp_inbound.sender_phone` o biçimdedir ve karşılaştırma oradan geçer).
Ayrıca `seed_demo_data.py:94` ve `:116` düzeltilsin — bugünkü demo verisiyle
F10-1 uçtan uca DENENEMEZ. Müşteri tohumu (`:94`) için hane sayısı yeter;
tedarikçi tohumu (`:116`) `212` sabit hat önekini taşıdığı için ayrıca `5xx`
cep önekine çevrilmelidir (§2.2).

**K3 — Çiftçi kendi fiyatını görsün mü?**
→ **Tavsiye: EVET** (§4e). Rakam zaten elindeki kağıtta yazılı.

**K4 — Çiftçi eşleştirme ucu hangi izinden geçsin?**
Ölçüm: `app/auth.py:977` `/api/whatsapp/` ÖNEKİNİN TAMAMINI `"users"` iznine
bağlıyor ve gerekçesi hemen üstünde, ASCII yazımıyla (`auth.py:970-971`):
*"kime WhatsApp'tan ulasilabilir" listesi bir KULLANICI YONETIMI yuzeyidir*. Çiftçi bağlantısı bir kullanıcı yönetimi
işi DEĞİLDİR; `"users"` izni olmayan satış/alım personeli cari kartındaki
düğmeyi kullanamaz.
→ **Tavsiye: `/api/whatsapp/party-` öneki için AYRI bir kural — `CUSTOMER`
tarafı `sales`, `SUPPLIER` tarafı `purchases`.** Alternatif (`"users"`da
bırakmak) daha güvenli ama düğmeyi pratikte ölü bırakır.

**K5 — Kantar v1 kapsamı.**
→ **Tavsiye: yalnız kesilmiş makbuza bağlı fişler** (§4c). Parsel zinciri
(`farms.customer_id`) ayrı bir dilim olsun; `customers` ve `suppliers`
taraflarını birbirine bağlama sorusunu açar ve o soru K1'in ötesindedir.

**K6 — Çiftçi mesajı için hız sınırı ve tablosu.**
→ **Tavsiye: yeni PLATFORM tablosu `whatsapp_message_attempts`, pencere 15 dk,
sınır 20, cevap sınırı 15** (§5.3). Mevcut `whatsapp_pairing_attempts`e `kind`
eklemek, yürürlükteki eşleştirme sınırını bir göç boyunca zayıflatır.

---

## 8. EN BÜYÜK ÜÇ RİSK

1. **K1 yanlış cevaplanırsa tasarım ikinci kez açılır.**
   `whatsapp_customer_links` inşa edilirse avans, kantar ve makbuz (dört
   niyetin üçü) şemaya ikinci bir bağlantı tablosu ya da
   `customers`↔`suppliers` eşleme tablosu gerektirir — ve ikincisi bugün HİÇ
   YOKTUR.
2. **Telefon verisi kanalı sessizce ölü bırakabilir.** `customers.phone`
   `String(60)` serbest metindir, tekilliği yoktur ve demo kümesinin tamamı
   sıkı normalleştiriciyi geçemez. Kod üretilir, çiftçi `BAĞLA` yazar,
   `target_phone` karşılaştırması TUTMAZ ve hata mesajı — ayırt edilemezlik
   sözleşmesi gereği — nedeni SÖYLEMEZ (`eslestirme.RED_MESAJI`). Arıza
   üretimde teşhis edilemez. K2 bu yüzden F10-1a'dan ÖNCE kapanmalıdır.
3. **Pin deltaları paralel PR'larla çürür.** `TENANT_TABLES` ALTI dosyada
   (tanım + beş dosyada yedi çivi), alembic başı ON ÜÇ dosyada ON DÖRT çivi,
   Core envanteri parmak iziyle birlikte çivili. F10-1a hem bir göç hem iki
   tenant tablosu hem dört rota getiriyor — `f0e3918` üzerinde sayıldığında
   aynı anda EN AZ OTUZ pin noktasına dokunuyor: `TENANT_TABLES` 8 (tanım +
   7), alembic başı 14, rota işlemi/yolu 2, GET envanteri 1, kimlik
   doğrulamalı ≥1, Core envanteri 2 (sayım + parmak izi), Core kiracı ifadesi
   1, `pg_twins.txt` 1. Başın dokuz çivisi `backend/` kökündeki PG
   ikizlerindedir ve SQLite hattı onları HİÇ koşmaz — yerel yeşil bunları
   görmez. Aynı pencerede inen başka bir PR, çakışmasız ama kırmızı bir CI
   üretir.

---

## EK — ÖLÇÜLEMEYENLER (DOĞRULANMADI)

* **Üretim verisinde aynı numaranın kaç caride tekrarlandığı.** Elimde yalnız
  `seed_demo_data.py` var; canlı veri tabanına erişmedim.
* **Çiftçilerin gerçekte kaçının hem `customers` hem `suppliers` satırı
  olduğu.** İki tabloyu bağlayan bir sütun yok; ad/VKN eşlemesi bir tahmindir
  ve tahmin bu belgeye girmez.
* **F10-1a/b/c'nin Core envanteri ve kiracı ifadesi artışları** (`+~8`, `+~10`,
  `+~6`). Sorgular henüz yazılmadığı için tarayıcıyla ölçülemedi; aritmetikle
  türetilmiş TAHMİNDİR ve her dilim kendi tabanında tarayıcıyla YENİDEN
  ölçmelidir.
* **`ARAC_BEYAZ_LISTESI`nin `app/` dışındaki tüketicileri.** Yalnız `app/`
  altı tarandı.
