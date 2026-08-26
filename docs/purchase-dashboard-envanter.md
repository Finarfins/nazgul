# Satın Alma Panosu — Veri Envanteri

Kapsam: `feat/purchase-dashboard`. Bu not, panonun hangi veriyi #153'ten (Satın Alma
Motoru B/C) hazır aldığını, hangi agregasyonun eksik olduğunu ve bu dalda ne
eklendiğini kaydeder. Pano **görünür katmandır**: hiçbir satın alma iş kuralı bu
dalda değişmedi, hiçbir yazma ucu eklenmedi.

## 1. #153'ün sağladığı hazır uçlar

Tümü `backend/app/routers/supplier_prices.py` içinde, `/api` önekiyle. RBAC:
`app/auth.py:required_permission` `/api/purchase-comparison*` yolunu `purchases`
iznine bağlar; tenant `company_id(request)` ile oturumdan gelir.

| Uç | Ne verir | Pano kullanıyor mu |
| --- | --- | --- |
| `GET /purchase-comparison` | Ürün × tedarikçi karşılaştırma ızgarası, en iyi teklif, ⚡ alternatif; sayfalı (`page_size ≤ 200`) | Hayır — sayfalı ızgara, özet değil |
| `GET /products/{id}/supplier-prices` | Tek ürün için tüm teklifler + FX kurları | Hayır — ürün detayı ekranının işi |
| `GET /purchase-comparison/products/{id}/analysis` | Miktara göre indirim merdiveni, efektif TL maliyet, KDV-hariç brüt marj, teslim süresi filtresi | Hayır — tek ürün derinliği |
| `GET /purchase-comparison/reorder-suggestions` | Sipariş noktası altındaki ürünler + en iyi tedarikçi + önerilen miktar | **Evet** — "Yeniden Sipariş Önerileri" listesi |
| `GET /purchase-comparison/dashboard` | Öneri listesinin özeti: kaç ürün altında, tedarikçi bazında öneri maliyeti, en büyük açıklar | **Evet** — öneri maliyeti ve sayaç kartı |
| `GET /exchange-rates` | Efektif TCMB/override kurları | Hayır |

Yazma uçları (`POST /purchase-orders`, `POST /purchase-comparison/reorder-drafts`,
`PUT .../reorder-policy`, `supplier-prices` CRUD) pano kapsamı dışında. Pano
kasıtlı olarak salt okunurdur: öneri bir **taslaktır**, sipariş oluşturma akışı
Tedarikçi Fiyat Karşılaştırma ekranında kalır.

## 2. Panonun ihtiyacı olup #153'te BULUNMAYAN veri

| İhtiyaç | Durum | Neden mevcut uçlar yetmiyor |
| --- | --- | --- |
| Aylık/dönemsel alış harcama trendi | **Eksikti** | `GET /reports/summary` yalnızca `purchases_total` (tek sayı) verir; `monthly_*` kırılımı **sadece satış** içindir |
| Tedarikçi bazlı harcama kırılımı | **Eksikti** | Hiçbir uç alış belgelerini tedarikçiye göre toplamıyor; `reorder-suggestions` gelecekteki öneriyi verir, geçmiş harcamayı değil |
| Ürün / kategori bazlı harcama | **Eksikti** | `reports/summary.top_products` sadece **satış** satırlarından üretilir |
| Fiyat karşılaştırma özeti (kim kaç üründe en iyi / en pahalı) | **Eksikti** | `GET /purchase-comparison` sayfalıdır; bir sayfadan çıkarılan "en iyi tedarikçi" katalogun tamamını temsil etmez, yanıltıcı olur |
| Sipariş noktası altı ürün sayısı ve öneri maliyeti | Hazır | `GET /purchase-comparison/dashboard` |

Frontend'de toplama yapmak seçenek değildi: hem sayfalı veri üzerinden yanlış
sonuç verirdi, hem de proje kuralı gereği tutarlar sunucuda Decimal ile
hesaplanır.

## 3. Bu dalda eklenen agregasyon uçları (SALT OKUNUR)

İkisi de aynı router'da, aynı `purchase-comparison` öneki altında — böylece
mevcut `purchases` RBAC kuralını ve `company_id` fail-closed tenant çözümünü
olduğu gibi devralırlar. Yeni tablo, migration veya iş kuralı yok.

### `GET /api/purchase-comparison/spend-analytics`

Parametreler: `date_from`, `date_to` (ISO tarih), `months` (1–36, varsayılan 12),
`top` (1–50, varsayılan 10).

- **Taslak ve iptal belgeler hariçtir** (`COALESCE(status,'completed') NOT IN
  ('draft','cancelled')`). Bu kritik: yeniden sipariş motorunun kendisi *draft*
  alış belgesi üretir; sayılsalardı pano kimsenin onaylamadığı siparişlerle
  kendini şişirirdi.
- İki farklı taban raporlanır ve karıştırılmaz:
  - `totals.total_try`, `monthly`, `by_supplier` → `purchases.final_total`
    (belge indirimi **sonrası** yetkili belge toplamı);
  - `by_product`, `by_category` → `purchase_items.line_total` (satır bazlı, belge
    indirimi **öncesi**). Bu tabanın toplamı `totals.line_total_try` olarak ayrıca
    döner, böylece paylar yanlış paydaya bölünmez.
- Tüm tutarlar KDV **dahil** ve Decimal string.
- `top` ile kesilen kuyruk atılmaz, `other_supplier_total_try` /
  `other_product_total_try` / `other_category_total_try` olarak raporlanır — kesilmiş
  bir grafik "hepsi bu" gibi okunmasın diye.
- Kategorisi olmayan veya serbest (product_id NULL) satırlar `category: null`
  kovasında görünür, düşürülmez.
- Eski `GG.AA.YYYY` biçimli `purchase_date` değerleri ay kovalarına ve tarih
  filtresine ISO olanlarla aynı şekilde girer (modülün mevcut
  `_normalized_date_sql` ifadesi).

### `GET /api/purchase-comparison/supplier-scorecard`

Parametreler: `limit` (1–500, varsayılan 200), `alt_threshold`.

- `_build_offers` motorunun çıktısı üzerinde **saf sayımdır**; kendi fiyat mantığı
  yoktur. Miktar verilmez, yani `GET /purchase-comparison` ızgarasının varsayılan
  görünümüyle aynı sıralama geçerlidir.
- `best_count` / `worst_count` yalnızca **en az iki tedarikçinin** fiyat verdiği
  ürünlerde artar: tek teklifin en ucuzu olmak kazanç değildir.
- Tarama `limit` ile sınırlıdır ve `truncated` + `candidate_product_count`
  alanlarıyla kısmi olduğunu söyler.

Sözleşme: `frontend/src/api/types.gen.ts` yeniden üretildi (yalnızca ekleme,
129 satır); `contract-drift` kapısı yeşil.

## 4. Ekranlar

Rota: `/raporlar/satin-alma-panosu` (`RequirePermission permission="reports"`),
menüde "Satın Alma Panosu". Sayfa: `frontend/src/pages/PurchaseDashboard.tsx`.
Grafikler mevcut `recharts` ile — yeni bağımlılık yok.

1. Özet kartları — toplam alış (KDV dahil), belge sayısı, tedarikçi sayısı,
   ortalama belge, sipariş noktası altı ürün sayısı.
2. Aylık alış harcaması — `BarChart` (belge toplamı bazında).
3. Kategori kırılımı — `PieChart` (satır toplamı bazında).
4. Tedarikçi bazlı harcama — yatay `BarChart` + pay/belge/son alış tablosu,
   tedarikçi kartına bağlantı.
5. Fiyat karşılaştırma özeti — en iyi / en pahalı tedarikçi rozetleri +
   tedarikçi tablosu, tek kaynak ve kısmi tarama uyarıları.
6. Ürün bazlı harcama — pay tablosu; `product_id` çözülemeyen satır bağlantısız.
7. Yeniden sipariş önerileri — **salt okunur** liste + önerilen taslak maliyeti.

RBAC davranışı: rota `reports` iznine bağlı; veri `purchases` iznine bağlı.
Yetkisiz kullanıcı 403 alır ve ekran hiçbir veri göstermeden "Bu pano satın alma
yetkisi gerektirir (maliyet ve tedarikçi verisi)." uyarısını verir — mevcut
Tedarikçi Karşılaştırma ekranıyla aynı desen.

## 5. Testler

- `backend/test_v3_purchase_dashboard.py` — el ile hesaplanmış toplamlar, iki
  taban ayrımı, draft dışlama, tarih penceresi, top-N mutabakatı, boş tenant,
  tenant izolasyonu, scorecard sayımları ve sınırlama, RBAC (harita + HTTP).
- `backend/test_purchase_dashboard_postgresql.py` — aynı toplamlar gerçek
  PostgreSQL'de (NUMERIC toplamları, `substr` ay kovası, çıktı-alias `ORDER BY`,
  `COUNT(DISTINCT ...)`).
- `frontend/src/pages/PurchaseDashboard.test.tsx` — render, sunucudan gelen
  tutarların gösterimi, en iyi/en pahalı rozetleri, salt-okunur öneri listesi
  (sipariş düğmesi yok), boş-veri durumu, 403 ve genel hata yolları.
- `frontend/e2e/screens.spec.ts` — pano taze veritabanında konsol-temiz açılır
  (boş-durum yolunun gerçek yığın kanıtı).
