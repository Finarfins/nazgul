# `POST /api/products/bulk-price` — bulgu raporu

**Tarih:** 2026-07-26 · **Dal:** `feat/test-coverage-writes` · **Durum: ÜÇÜ DE DÜZELTİLDİ**

Bu uç için modern regresyon testleri yazılırken üç gerçek hata ortaya çıktı. İlk turda testler
bunları yalnızca *karakterize* etti (kapsam test yazmakla sınırlıydı); ikinci turda üçü de
düzeltildi ve eşlenmiş assert'ler doğru davranışa çevrildi.

Kanıt ve regresyon ağı: [`backend/test_bulk_price_contract.py`](../backend/test_bulk_price_contract.py)
İlgili kod: [`backend/app/routers/products.py`](../backend/app/routers/products.py) `bulk_price`.

> **Şema değişmedi.** Tüm doğrulama router içinde yapıldı, `BulkPriceUpdate`
> ([`backend/app/schemas.py:234-238`](../backend/app/schemas.py)) olduğu gibi bırakıldı.
> Böylece OpenAPI sözleşmesi ve `frontend/src/api/types.gen.ts` sabit kaldı, `contract-drift`
> CI işi etkilenmedi.

---

## BUG-A — `percent` metodu SQLite'ta tamsayı bölmesi yapıyordu (KRİTİK, sessiz) — ✅ düzeltildi

**Eski kök neden**

```python
expression = f"ROUND({column}*(1+:v/100),2)"   # kaldırıldı
```

`:v` SQLite'a tamsayı olarak bağlandığı için `:v/100` **tamsayı bölmesi** oluyordu:
`10/100` → `0`, `25/100` → `0`, `150/100` → `1`.

**Ölçülen eski davranış** (100,00 ₺ başlangıç, SQLite):

| İstenen zam | Beklenen | Eski sonuç |
|---|---|---|
| %10 | 110,00 | **100,00** (hiç değişmedi) |
| %25 | 125,00 | **100,00** (hiç değişmedi) |
| %50 | 150,00 | **100,00** (hiç değişmedi) |
| %100 | 200,00 | 200,00 |
| %150 | 250,00 | **200,00** (%100 uygulandı) |
| %200 | 300,00 | 300,00 |

Her durumda yanıt `{"updated": 1}` — yani **başarı bildiriyordu**. PostgreSQL `:v`'yi NUMERIC
bağlayıp doğru hesapladığı için iki ortam farklı sonuç veriyordu.

**Düzeltme**
Hesap SQL'den çıkarılıp `app.money`'ye taşındı. Etkilenen satırlar çekiliyor, her satır için
Decimal hesaplanıyor, sonra yazılıyor:

```python
new_value = money(current * (HUNDRED + percentage(payload.value)) / HUNDRED)
```

Bu üç şeyi birden bitiriyor: tamsayı bölmesi, dialect farkı ve SQL'in yuvarlama davranışı.
Artık her iki dialect de aynı `ROUND_HALF_UP` 2 ondalık sonucu üretiyor. Test dosyasındaki
`DIALECT` dallanması tamamen kaldırıldı — artık gerekmiyor.

---

## BUG-B — Boş `product_ids` listesi tüm tenant kataloğunu güncelliyordu (YÜKSEK) — ✅ düzeltildi

**Eski kök neden:** boş liste falsy olduğu için `AND id IN (...)` kısıtı düşüyor ve UPDATE
`WHERE company_id=:cid` ile tüm kataloğa uygulanıyordu. 5 ürünlü bir tenant'ta
`product_ids: []` + `set 1` → `{"updated": 5}`, beş ürün de 1,00 ₺ oluyordu.

**Düzeltme**

```python
if payload.product_ids is not None and not payload.product_ids:
    raise HTTPException(400, "Ürün seçilmedi")
```

`None` (anahtar hiç gönderilmemiş) = "tüm ürünler" **bilinçli davranışı korundu**; ayırt edilen
tek şey açıkça gönderilen boş liste.

---

## BUG-C — Fiyat için alt sınır yoktu: sıfır ve negatif kabul ediliyordu (ORTA-YÜKSEK) — ✅ düzeltildi

**Eski davranış:** `set -50` → `-50,00`; `set 0` → `0,00`; `percent -200` → `-100,00`, hepsi 200.

**Düzeltme:** iki katmanlı, **yazmadan önce**:

```python
if payload.method == "percent" and percentage(payload.value) < -HUNDRED:
    raise HTTPException(400, "Yüzde değeri -100'ün altında olamaz")
...
if new_value <= ZERO_MONEY:
    raise HTTPException(400, "Fiyat sıfır veya negatif olamaz")
```

Doğrulama satır satır, yazma öncesi yapıldığı için **tek bir hatalı satır tüm batch'i
reddediyor** — kısmi uygulama yok. `fixed` negatif delta ile de sıfırın altına inemiyor.

> ⚠️ **Karar gerektiren varsayım.** Görev metni bir yerde "sonuç `>= 0` zorunlu" (sıfır serbest),
> test kriterinde ise "negatif/**sıfır** → 400" diyordu. Test kriterini esas alıp **sıfırı da
> reddettim** (`new_value <= ZERO_MONEY`). Sıfır fiyatın meşru sayılması isteniyorsa tek
> değişiklik: `<=` → `<` ve testteki `set 0` beklentisini 200'e çevirmek. Bu, promosyon/bedelsiz
> ürün senaryosunu etkileyebilir — berkay'ın kararı.

---

## Yan bulgu — yabancı/bilinmeyen id sinyali hizalandı — ✅ düzeltildi

Eskiden yabancı tenant ürün id'si **200 `{"updated": 0}`** dönüyordu; bu, başarılı bir no-op ile
ayırt edilemiyordu. Artık **404 "Ürün bulunamadı"** dönüyor ve içinde böyle bir id geçen batch
**bütün olarak** reddediliyor (çağıranın kendi satırı da değişmiyor).

### ⚠️ Önceki rapordaki hata — düzeltme

İlk raporda "`bulk-stock` aynı durumda 404 dönüyor, `bulk-price` uyumsuz" yazmıştım.
**Bu yanlıştı.** `test_v2_9_tenant_stock_security.py`'deki 404, yabancı **ürün** id'sinden değil
yabancı **depo** id'sinden (`"Depo bulunamadı"`) geliyor. Ampirik olarak doğrulandı:

```
bulk-stock, yabancı ÜRÜN id'si, kendi deposu -> 200 {"updated":0,"policy_overrides":[]}
bulk-stock, var olmayan ürün id'si           -> 200 {"updated":0,"policy_overrides":[]}
```

Yani `bulk-stock`'ta da aynı sessiz-no-op davranışı var. Hizalama isteği kendi başına doğruydu
ve uygulandı; ancak sonucu **`bulk-price` artık `bulk-stock`'tan daha katı**. Asimetri yön
değiştirdi, ortadan kalkmadı.

**Öneri (bu görevin kapsamı dışında, bilerek yapılmadı):** aynı 404 kuralını `bulk_stock`'a da
taşımak. Kapsam "başka uca dokunma" dediği için `bulk_stock` değiştirilmedi.

---

# Değişmeden geçen invaryantlar

Düzeltmeler sonrası bozulmadan geçen testler:

- **Tenant izolasyonu.** Yabancı id artık 404 alıyor; her iki yönde de çapraz tenant yazma yok,
  karışık batch bütün olarak reddediliyor. Tenant çapındaki (id'siz) çağrı tenant sınırında
  duruyor. `tests/test_tenant_scoping_guard.py` (AST taraması) yeni SELECT/UPDATE'lerde
  `company_id` görüyor ve geçiyor.
- **RBAC.** `stock` izni zorunlu: `satis`/`muhasebe`/`rapor` → 403 (okuma erişimleri bozulmadan),
  `depo`/`admin` → 200.
- **SQL injection yok.** `field`/`method` allowlist'i SQL'e interpolasyondan önce kontrol
  ediliyor; `'sale_price=1--'`, `'set; DROP TABLE products'` → 400. `product_ids` `int()` cast.
- **KDV-dahil sözleşmesi.** Toplu fiyat değişimi `vat_rate`'e dokunmuyor, seçilmeyen para
  kolonunu hareket ettirmiyor.
- **`set` / `fixed` 2 ondalık doğruluğu.** `19,99 + 0,01 = 20,00`, `150,50 + 10,25 = 160,75`.
