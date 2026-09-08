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

> **Sonucu açıkça yazılıdır:** ürün açılışında her depo için sıfır miktarlı
> bir `warehouse_stocks` satırı doğar. Bu çiftler (stok 0, parti satırı yok)
> `LOTSUZ_TASARIM`a düşer ve kovayı **kalabalıklaştırır**. Sayı doğrudur —
> o depoda gerçekten hiç parti açılmadı — ama kovanın okunabilirliği ürün ×
> depo çarpımıyla büyür. 1B-H bu gürültüyü ayrı bir alt-kova ile ayırabilir;
> bu dilimde **ölçülmedi** ve sessiz bırakılmadı.

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

### 4a. Bu PR'ın kapsamı DIŞINDA bırakılan uçlar (adıyla)

| Yer | Uç / yol | Not |
|---|---|---|
| `backend/app/routers/imports.py:352` | `POST /api/imports/products/excel` — **güncelleme** dalı | Excel farkı stoğa yazılır, partiye yazılmaz |
| `backend/app/routers/imports.py:360` | `POST /api/imports/products/excel` — **ekleme** dalı | Açılış stoku partisiz doğar |
| `backend/app/routers/products.py` → `create()` | `POST /api/products` | Ürün açılış stoku |
| `backend/app/routers/products.py` → `update_product()` | `PUT /api/products/{id}` | Stok alanı düzenlemesi |
| `backend/app/routers/products.py` → `bulk_stock()` | `POST /api/products/bulk-stock` | Toplu stok yazımı |

> Bu beşi **bilerek** dışarıda bırakıldı ve gerekçesi kapsamdır, teknik
> imkânsızlık değil: her biri kendi doğrulama ve politika yolunu taşıyor ve
> parti defterine bağlanmaları 1B-A..1B-F'nin her birinin ayrı bir dilim
> olmasıyla aynı büyüklükte iştir.

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
  "has_more": false
}
```

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
