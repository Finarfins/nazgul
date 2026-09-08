# PARTİ MUTABAKATI — iki defterin farkı neden ÖLÇÜLÜR, neden KISITLANMAZ

**Konu:** 1B-G. Kod: `backend/app/parti_mutabakat.py`,
`GET /api/products/lots/mutabakat`. Kapılar:
`backend/tests/test_1b_g_mutabakat.py` (SQLite) ve
`backend/test_1b_g_mutabakat_postgresql.py` (gerçek diyalekt).
**Göç YOKTUR** — bu dilim tek bir sütun eklemez, yalnız okur.

---

## 1. Hangi sayı otoritedir

| Defter | Anlamı | Kim yazar |
|---|---|---|
| `warehouse_stocks.quantity` | **OTORİTE.** (firma, depo, ürün) başına tek satır. | `inventory.adjust_warehouse_stock` |
| `products.stock` | Yukarıdakinin **TOPLAMI**, üçüncü bir sayı DEĞİL. | `inventory.sync_product_stock` (tek SQL) |
| `product_lots.quantity` | Aynı stoğun **PARTİ KIRILIMI**. Üçüncü bir defter değil, ikinci bir görünüm. | `app/parti_defteri.py` (TEK yazıcı) |

`product_lots` stoktan **ayrılabilir** ve bu ayrılma bir kusur değil, göç
`20260903_0067`'nin **tasarım kararıdır**: parti defteri 1B-A ile açıldı ve
stoğa dokunan yolların yalnız bir kısmı ona bağlandı. Geri kalanı (§4)
partisiz yazmaya devam eder.

Mutabakat o farkı **ölçer** ve **kimin** farkı olduğunu söyler. Ölçülmeseydi
fark yine var olurdu; tek değişen, kimsenin göremeyecek olmasıydı.

---

## 2. Üç kova

Kural tek yerdedir: `parti_mutabakat.kova_sec`. Sıra **zorunludur**.

```
ÖNCE  SAPMA denetimi  ->  SONRA  LOTSUZ_TASARIM  ->  KALAN  ESIT
```

| Kova | Koşul | Okunuşu |
|---|---|---|
| `ESIT` | Parti satırı **var** ve toplam stoğa **tam eşit** | Defter bu çift için TAM |
| `LOTSUZ_TASARIM` | Bu çift için **hiç parti satırı açılmamış** | Fark **beklenendir**, kusur değil |
| `SAPMA` | Parti satırı **var** ama toplam tutmuyor; **ya da** parti toplamı stoğu aşıyor | **İncelenmesi gereken tek kova** |

### Neden üç kova, iki değil

İki kova (eşit / eşit değil) yazmak kolaydı ve tam bu yüzden reddedildi:
bugün stoğa partisiz dokunan yolların **hepsi** "eşit değil" kovasına
düşerdi, kova anında yüzlerce satırla dolardı ve içindeki tek gerçek kusur o
gürültüde görünmez olurdu. **Gürültüyle dolu bir uyarı, uyarı değildir.**

### `LOTSUZ_TASARIM` "toplam 0" DEĞİL, "SATIR YOK"tur

Mekanizma `parti_toplami == 0` olsaydı kovanın **adı yalan söylerdi**.

1B-B bir partiyi sonuna kadar tükettiğinde satırı **silmez**, `quantity`sini
0'a çeker — ve satırın kalması 0067'nin kararıdır, çünkü o satır **geri
çağırmanın kanıtıdır**. Tükenmiş bir parti için `parti_toplami == 0` ve
`stok == 0`dır. "Toplam 0" mekanizması bu çifti `LOTSUZ_TASARIM`a atardı ve
kova "hiç parti açılmadı" derken, açılmış **ve düzgün kapanmış** bir partiyi
gösterirdi. Doğru cevap `ESIT`tir: iki defter de 0 diyor, ikisi de doğru.

Ayrım bu yüzden **satır sayısındadır** (`COUNT(*)`), toplamda değil.

> **1B-G'nin borcu, 1B-H'de ÖDENDİ.** 1B-G şunu yazmıştı: ürün açılışında
> her aktif depo için sıfır miktarlı bir `warehouse_stocks` satırı doğar; bu
> çiftler (stok 0, parti satırı yok) `LOTSUZ_TASARIM`a düşer ve kovayı ürün ×
> depo çarpımıyla **kalabalıklaştırır**. Ölçüm 1B-H'ye bırakılmıştı ve
> yapıldı — sonuç §7'dedir: bu çiftler artık **rapordan düşer** ve düşen sayı
> `bos_ciftler` alanında **taşınır**.

### `parti_toplami > stok` koşulsuzdur

`SAPMA`nın ikinci dalı `LOTSUZ_TASARIM`ın **içinden de** ısırır ve ısırması
gerekir: parti satırı yokken toplam 0'dır, yani `0 > stok` ancak stok
**negatifse** doğrudur. Negatif stok bu depoda politikayla serbest
bırakılabilir (`negative_stock_policy='allow'`), ama **"eksi bakiye"** ile
**"bu çift bilinçli olarak partisizdir"** aynı cümle değildir. Koşulu
`LOTSUZ_TASARIM`ın arkasına koymak, eksi bakiyeyi "tasarım" etiketiyle
**gömerdi**.

### `fark`ın işareti anlamlıdır

`fark = stok - parti_toplami`

* `fark > 0` — stok partiden fazla: partisiz bir giriş olmuş (çoğunlukla
  `LOTSUZ_TASARIM`).
* `fark < 0` — parti stoktan fazla: **tehlikeli yön budur**, defter elde
  olmayan malı **var** gösteriyor.

---

## 3. Neden bir veritabanı kısıtı DEĞİL

"Madem iki sayı eşit olmalı, bir `CHECK` koy" sorusunun cevabı üç katlıdır ve
üçü de ölçüldü:

**1. Kısıt iki tabloyu birden okuyamaz.** `warehouse_stocks.quantity` ile
`SUM(product_lots.quantity)` ayrı tablolardadır; satır içi bir `CHECK` başka
bir tabloya bakamaz. Kalan tek araç **tetikleyicidir** ve bu depoda
tetikleyici yoktur — göç zinciri saf DDL'dir. Bir tetikleyici eklemek, göç
zincirinin türünü değiştirmek olurdu.

**2. SQLite eşliği.** Kısıt PostgreSQL'de kurulup SQLite'ta kurulamasaydı iki
diyalekt **iki farklı şema** olurdu ve testlerin yeşili üretimin yeşilini
**temsil etmezdi**. Bu depo o eşliği ayrı bir kapıyla koruyor.

**3. Kısıt yanlış cevabı verirdi.** §4'teki lot-suz yollar **kusur
değildir**; bir kısıt onları **reddederdi** ve reddettiği şey bugün **çalışan**
bir iş akışı olurdu. Doğru cevap "yaz"/"yazma" arasında değil, **görünür
kılmak** ile **gizlemek** arasındadır.

Yani mutabakat bir kısıtın yerine geçmiyor; kısıtın **kurulamayacağı** yerde
kurulabilecek tek şeyi kuruyor: **ölçülebilir bir rapor.**

### Ve neden bir "düzelt" ucu da yok

`POST .../duzelt` biçiminde bir uç iki defteri uyumlu **gösterir** ama malın
hangi partiden çıktığını **söyleyemez**; geri çağırma kaydı yalan söylemeye
devam ederdi. Farkı kapatmanın tek doğru yolu, farkı **üreten** yolu partiye
bağlamaktır — yani §4.

---

## 4. Bugün partisiz yazan yollar (1B-H'nin listesi)

Hepsi `warehouse_stocks`a yazar, hiçbiri `product_lots`a dokunmaz. Bu dilim
onları **kapatmıyor**; `LOTSUZ_TASARIM` kovasına düştüklerini ve `SAPMA`yı
kirletmediklerini **ölçüyor**.

### 4a. 1B-G'nin kapsam dışı bıraktığı BEŞ uç — 1B-H'DE KAPANDI

| Yer | Uç / yol | Not |
|---|---|---|
| `backend/app/routers/imports.py:352` | `POST /api/imports/products/excel` — **güncelleme** dalı | Excel farkı stoğa yazılır, partiye yazılmaz |
| `backend/app/routers/imports.py:360` | `POST /api/imports/products/excel` — **ekleme** dalı | Açılış stoku partisiz doğar |
| `backend/app/routers/products.py` → `create()` | `POST /api/products` | Ürün açılış stoku |
| `backend/app/routers/products.py` → `update_product()` | `PUT /api/products/{id}` | Stok alanı düzenlemesi |
| `backend/app/routers/products.py` → `bulk_stock()` | `POST /api/products/bulk-stock` | Toplu stok yazımı |

> 1B-G bu beşi **bilerek** dışarıda bırakmıştı ve gerekçesi kapsamdı, teknik
> imkânsızlık değil. **1B-H beşini de kapattı** — nasıl kapattığı ve neden
> hepsine aynı cevabın verilmediği §8'dedir. Yukarıdaki satır numaraları
> (`imports.py:352/360`) 1B-H **öncesinin** hâlini gösterir ve tarihsel
> kayıt olarak duruyor: bugün o iki dal `_parti_kalemi`ni çağırıyor.

### 4b. Tasarım gereği partisiz kalan yollar

| Yol | Neden partisiz |
|---|---|
| Tarla faaliyet **girdileri** (`app/field_stok_tuketici.py:682`) | Girdi tüketimi partiyi sormaz |
| Kantar fişi **farkları** | Fark bir düzeltmedir, bir parti hareketi değil |
| **İş emri parçaları** (`app/routers/work_order_parts.py:92`, `app/work_order_stock.py:261`) | Servis aracı deposundan parça çıkışı |
| **Partisiz** transfer / sayım / iade / alış / satış | Kalemde `lot_code` yoksa yol partisiz akar (1B-A..1B-E'nin açık kapsam sınırı) |
| **Kaynaksız** satış iadesi (`source_type`/`source_id` NULL) | İadenin hangi partiden çıktığı **sorulamaz**; uydurulmaz (1B-E) |

---

## 5. Uç

```
GET /api/products/lots/mutabakat?limit=200&offset=0
```

İzin `read` (yol `/api/products` önekinden; `auth.py`ye satır **eklenmedi**,
bu **ölçüldü**). Kiracı kapsamlı, **salt okuma**.

```jsonc
{
  "items": [
    {"product_id": 1, "warehouse_id": 1,
     "stok": 5.0, "parti_toplami": 6.0, "fark": -1.0, "kova": "SAPMA"}
  ],
  "counts": {"ESIT": 6, "LOTSUZ_TASARIM": 5, "SAPMA": 1},
  "total": 12,
  "has_more": false,
  "bos_ciftler": 9
}
```

`bos_ciftler` (1B-H) rapordan **düşülen** boş çift sayısıdır ve `total`a
**dahil değildir** — `total` raporun satır sayısıdır, deponun çift sayısı
değil. Gerekçesi ve ölçülmüş büyüklüğü §7'dedir.

**`counts` sayfanın değil KİRACININ TAMAMININDIR.** İkinci sayfadaki tek
`SAPMA`yı ilk sayfaya bakan operatör görmezdi ve "sapma yok" diye okurdu.

Bunun **ölçülmüş bir bedeli** var ve gizlenmiyor: kova kuralı Python'da tek
kopya olarak durduğu için saymak tüm çiftleri okumayı gerektirir. Alternatif,
kuralı bir de SQL `CASE`i olarak yazmaktı — yani **iki kopya**. İki kopya
ayrıştığı gün rapor kendi sayacıyla çelişirdi ve hangisinin doğru olduğu
sorulamazdı.

### Tek SQL, iki diyalekt

* `FULL OUTER JOIN` **yazılmadı** — SQLite onu 3.39'dan önce tanımıyor ve kapı
  koştuğu **makineye** göre yeşil olurdu. Sürücü küme bir `UNION`dur
  (`UNION ALL` değil: aynı çift iki satır olurdu).
* `GROUP BY` alt sorguda **seçilen her sütunu** sayar. PostgreSQL'in işlevsel
  bağımlılık kuralı SQLite'ınkinden dardır; dar olana göre yazmak ikisini de
  karşılar, tersi karşılamaz.
* Kova **Python'da** hesaplanır. SQL'e bırakılsaydı karşılaştırma diyalektin
  döndürdüğü **tipe** bağlı olurdu: PostgreSQL `NUMERIC`i `Decimal`, SQLite
  aynı sütun için `float` verebilir ve `0.1+0.1+0.1 != 0.3` **sessiz bir
  `SAPMA` uydururdu**. Her iki sayı da karşılaştırmadan önce `money.quantity`
  ile aynı kuantuma (`NUMERIC(18,4)`) çekilir. PG ikizi bunu şemadan okuyup
  dördüncü basamağa kadar ölçüyor.

---

## 6. Ekran

`Ürün Detayı → Partiler` sekmesi (`frontend/src/components/ProductLotsPanel.tsx`),
mevcut `GET /api/products/{id}/lots` ucundan beslenir ve **sekme açılınca**
ister. Miktarı sıfır olan parti **gizlenmez, soldurulur ve "Tükendi" etiketi
alır** — §2'deki aynı gerekçe: o satır geri çağırmanın kanıtıdır ve listeden
düşürmek, kanıtı veritabanında tutup operatörden saklamak olurdu.

---

## 7. `BOS_CIFT` — raporun dışında, ama sessiz değil (1B-H)

§2 bir borcu adıyla yazmıştı: ürün açılışı **aktif her depo** için sıfır
miktarlı bir `warehouse_stocks` satırı doğurur, o çiftlerin hepsi
`LOTSUZ_TASARIM`a düşer ve kova ürün × depo çarpımıyla büyür. 1B-H ölçtü.

### Ölçüm

Sayılar 1B test veritabanlarından, raporun kendi cevabından okundu:

| Veritabanı | Elenen (`bos_ciftler`) | Kalan (`total`) | Eleme öncesi | `LOTSUZ_TASARIM` |
|---|---:|---:|---:|---:|
| 1B-G birleşik (A–F mutlu yollar + partisiz yollar) | **10** | 14 | 24 | 16 → **6** |
| 1B-H davranış | **9** | 7 | 16 | 11 → **2** |

Yani rapor satırlarının **%42'si** (1B-G) ve **%56'sı** (1B-H) iki defterin de
boş olduğu çiftlerdi. `LOTSUZ_TASARIM` kovası sırasıyla **%62** ve **%82**
küçüldü. §2'nin "gürültü" tahmini bir tahmin değildi: kovanın çoğunluğuydu.

### Kural

```
stok == 0  VE  parti satırı YOK   ->   BOS_CIFT: rapora GİRMEZ
```

Bu çiftlerde **hiçbir şey yoktur**: iki defter de boş. Karşılaştırılacak bir
sayı olmadığı için "uyuşuyor mu" sorusu bile sorulamaz. `LOTSUZ_TASARIM` ise
bir **şey** söyler: "burada mal VAR ve partisiz girdi". Aynı kovada durmaları,
o cümleyi taşıyan satırları taşımayanların içinde görünmez yapıyordu.

### Neden dördüncü bir kova değil

`sayimlar` dört anahtarlı olurdu ve operatör raporu okurken dört sayıyı
toplayıp "hangisi benim ürünlerim" diye sormak zorunda kalırdı. Boş çift bir
**sınıflandırma** değil, bir **yokluktur**; sınıflandırmaya bir "hiçbiri"
kovası eklemek, kovaların üçünün de **mal** hakkında konuştuğu olgusunu
bozardı. `KOVALAR` bu yüzden **üç** kaldı.

### Ama sayısı taşınır

Satırlar rapordan düşer ve düşen sayı gövdede `bos_ciftler` olarak **durur**.
Sessizce atmak, `total`ın deponun çift sayısından neden küçük olduğunu
sorulamaz yapardı — "rapor eksik mi, yoksa öyle mi" sorusunu koda bakmadan
cevaplayamayan bir operatör bırakırdı.

### Sıra yine zorunlu

```
kova_sec (SAPMA denetimi dahil)  ->  SONRA  bos_cift_mi
```

Eleme `kova_sec`ten **sonradır**. Negatif stoklu ve partisiz bir çift
`SAPMA`dır (`parti_toplami 0 > stok`), ve eleme öne alınsaydı o çift "boş"
sayılıp rapordan **silinirdi** — §2'deki sıra kuralının kapattığı kaçağın
aynısı, bu kez öteki uçtan: eksi bakiye bir etiketle gömülmez, **rapordan
düşürülerek** gömülürdü. `bos_cift_mi` bu yüzden `stok == 0` **tam
eşitliğine** bakar, `not stok`a değil.

---

## 8. §4a'nın beş yazıcısı: kapanış (1B-H)

Kural tek cümledir:

> Lot-suz yazmak **tasarımdır** — ama **yalnız** defter o (ürün, depo) çifti
> için **kapalıyken**. Satır varsa yazıcı ya partiyi **sorar** ya 409 ile
> **reddeder**.

Satır varken lot-suz yazmak `SAPMA` **üretir**: stok kımıldar, parti toplamı
kımıldamaz. Yani bu kapı yeni bir katılık değil, §3'ün "farkı üreten yolu
partiye bağla" cümlesinin **kaynakta** uygulanmasıdır.

### Kim ne yapıyor

| Yazıcı | Karar | Nasıl |
|---|---|---|
| `POST /api/imports/products/excel` (iki dal) | **SORAR** | İsteğe bağlı `Parti Kodu` sütunu → `_parti_ayarla` |
| `POST /api/products` (açılış stoku) | **SORAR** | İsteğe bağlı `lot_code` alanı → `_parti_ac` |
| `PUT /api/products/{id}` (stok alanı) | **REDDEDER** | 409 `LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ` |
| `POST /api/products/bulk-stock` | **REDDEDER** | 409, **tüm partiyi** düşürür |

### Neden dördü aynı cevabı vermiyor

Karar **yazıcı başınadır** ve üslup değil, o yolun **ne olduğudur**:

* **Excel içe aktarma** ve **ürün açılışı** bir **giriştir**: mal depoya yeni
  giriyor ve hangi partiden girdiği **sorulabilir** bir olgudur. İsteğe bağlı
  bir alan eklemek, bugün çalışan partisiz akışı **kırmadan** cevabı mümkün
  kılar.
* **Ürün kartı stok düzenlemesi** ve **toplu stok yazımı** bir **düzeltmedir**,
  bir mal hareketi değil: operatör bir sayıyı elle değiştiriyor. Parti takipli
  bir üründe o sayının hangi partiye ait olduğunun cevabı **yoktur** ve
  uydurulamaz. Doğru yol **zaten var** — `POST /api/products/{id}/stock`
  (1B-C) parti kodunu sorar ve defteri yazar. Red bir yeteneği
  **kaldırmıyor**, cevaplanabilir olana yönlendiriyor.

Bu ayrım ölçülmeden yazılmadı. Dördüne de "sor" demek, toplu stok yazımına
ürün başına parti kodu taşıtmak demekti — o uç **tek** bir değeri onlarca
ürüne basar ve ürün başına kod alan bir gövde, ucun **ne olduğunu**
değiştirirdi. Dördüne de "reddet" demek ise Excel ile açılış yapan bir
firmanın parti takibine **hiç geçememesi** demekti: ürünü partili açacak yol
kalmazdı.

### Yüklem: "satır var mı", "toplam > 0 mı" değil

`parti_mutabakat.kova_sec` ile **aynı** yüklem, ve aynı olmak zorunda:
tükenmiş bir parti (`quantity=0`, satır **duruyor**) o ürünün parti takipli
olduğunun kanıtıdır. "Toplam > 0" deseydik, defteri sonuna kadar tüketilmiş
bir ürüne lot-suz yazmak **serbest** kalırdı ve o yazma çifti doğrudan
`SAPMA`ya iterdi — kapı, tam da savunması gereken durumda açılırdı.

Yüklem **depo başınadır** (0073'ün tekilliği): aynı ürün bir şubede parti
takipli, ötekinde takipsiz olabilir. Ürün düzeyine çıkarmak, hiç parti
görmemiş bir depoya açılış stoku yazmayı da reddederdi.

### Neden kapı `app/parti_defteri.py`de

Yüklem `product_lots`u **okur**. `tests/test_1b_a_alis_lot.py` tablo adını
**anan** dosyaları kapalı bir kümede tutuyor; kapıyı çağıranın içine yazmak o
kümeyi `imports.py` ile **genişletirdi**. Defterden çağırmak ise **üçüncü**
ekseni (`CAGIRANLAR`, defteri **kullananlar**) bir adımla büyütür: **6 → 7**.
Ayrıca red **metni ve kodu tek kopyadır** — dört yazıcı aynı cümleyi kurmaz.

### Red neden tüm isteği düşürüyor

Toplu yazımda o ürünü **atlamak** 200 dönerdi ve `updated` sayısı istenen ürün
sayısından küçük olurdu — `bulk_price`ın #157'de kapattığı kusurun aynısı:
**kısmen uygulanmış bir seçim, tamamlanmış olandan ayırt edilemezdi.** Excel
içe aktarmada `errors` listesine bir kayıt düşürüp devam etmek de aynı şeydi:
depo yarı aktarılmış bir katalogla kalırdı. Excel'in çaresi bu yüzden **satır
numarası taşır** — "bir ürünün defteri açık" demek, yüzlerce satırlık bir
dosyayı elle taramak demekti.

### İşaret kuralı tek kopya oldu

`_parti_ayarla` (defterde) artık hem `POST /api/products/{id}/stock` hem de
Excel içe aktarma tarafından çağrılıyor: **işaret** karar verir (`diff < 0`
düşer, değilse açar), `mode` ya da dal değil. Gövde 1B-C'de `products.py`nin
içindeydi ve tek çağıranlıydı; ikinci çağıran gelince **kopyalanmadı**,
taşındı. İki kopya ayrıştığı gün aynı parti kodu iki uçtan iki farklı satır
üretirdi ve geri çağırma kaydı hangisinin doğru olduğunu söyleyemezdi.
