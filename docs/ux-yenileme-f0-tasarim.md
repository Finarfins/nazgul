# UX Yenileme — FAZ-0 TASARIM

**Durum:** Tasarım taslağı. **Kod yok**, mevcut koda dokunulmadı (yalnız okundu).
**Kapsam:** Menü gruplama · Müşteri 360 · Makine 360 · görsel token/bileşen standardı · kademeli geçiş planı.
**Kapsam dışı (bilinçli):** UI kütüphanesi değişimi (MUI kalıyor), backend iş kuralı değişimi, yeni rapor/analiz ekranı, mobil uygulama.
**Ekler:** `ux-yenileme-f0-mockup.html` (tek dosya, framework'süz, sahte veri, repoya bağlanmaz).

---

## 0. Yönetici özeti — üç cümlelik bulgu

1. **Menü düz bir liste: 31 madde, sıfır gruplama.** Kenar çubuğu tek `List` içinde 31 `ListItemButton` render ediyor ([AppShell.tsx:17](../frontend/src/components/AppShell.tsx:17)). Bunların 6'sı "Raporlar" alt kırılımı, 2'si tanım/ayar ekranı, 3'ü yönetim ekranı — hepsi satış ekranıyla aynı düzeyde duruyor.
2. **Müşteri 360 ve Makine 360 aslında %70 hazır.** `EntityDetail` zaten 8 sekmeli ([EntityDetail.tsx:137](../frontend/src/pages/EntityDetail.tsx:137)), `MachineDetail` zaten sayaç + sahiplik + iş emri bölümlerini gösteriyor ([MachineDetail.tsx:115](../frontend/src/pages/MachineDetail.tsx:115)) — F1'de eklenen uçlar sayesinde. Eksik olan **sekme mimarisi ve 4 veri ucu**, ekranın tamamı değil.
3. **Tasarım token'ları var ama uygulanmıyor.** `theme.ts` içinde `sungurTokens` tanımlı ve yeşil ([theme.ts:21](../frontend/src/theme.ts:21)) — ama kenar çubuğu **mavi** hardcoded (`#111c2d`, `rgba(88,132,235,.18)`), makine listesi başka bir yeşil kullanıyor (`#2f6b3b`). Token'a uymak, token yazmaktan daha acil.

---

## 1. KEŞİF

### 1.1 Tüm route'lar ve menü/rol eşlemesi

Kaynak: [App.tsx:68-122](../frontend/src/App.tsx:68) (route tanımı) + [AppShell.tsx:17](../frontend/src/components/AppShell.tsx:17) (menü dizisi).

**Kabuk dışı (3 route):** `/tanitim` (PremiumHomepage), `/giris` (Login), `/sifre-degistir` (ChangePassword).

**Kabuk içi — menüde görünen 31 madde:**

| # | Route | Menü etiketi | Menü izni | Route izni | Önerilen grup |
|---|---|---|---|---|---|
| 1 | `/` | Ana Sayfa | `read` | — | (grup dışı, sabit) |
| 2 | `/hizli-satis` | Hızlı Satış | `sales` | `sales` | (grup dışı, sabit) |
| 3 | `/sezonsal-stok-plani` | Sezonsal Stok Planı | `read` | `read` | Stok & Ürünler |
| 4 | `/satislar` | Satışlar | `read` | — | Satış |
| 5 | `/alislar` | Alışlar | `read` | — | Satın Alma |
| 6 | `/belge-akislari` | Teklif / Sipariş / İade | `read` | — | Satış |
| 7 | `/musteriler` | Müşteriler | `read` | — | Müşteriler |
| 8 | `/tedarikciler` | Tedarikçiler | `read` | — | Satın Alma |
| 9 | `/urunler` | Ürünler / Stok | `read` | — | Stok & Ürünler |
| 10 | `/parca-supersession` | Parça Supersession | `read` | — | Stok & Ürünler |
| 11 | `/makineler` | Makineler | `read` | — | Servis |
| 12 | `/is-emirleri` | İş Emirleri | `read` | — | Servis |
| 13 | `/odemeler` | Tahsilat / Ödeme | `payments` | `payments` | Finans |
| 14 | `/tahsis-defteri` | Tahsis Defteri | `read` | `read` | Finans |
| 15 | `/alacaklar` | Harman Vadesi / Alacaklar | `payments` | — | Finans |
| 16 | `/nakit-yonetimi` | Nakit Yönetimi | `finance` | `finance` | Finans |
| 17 | `/tanimlar/harman-sezon` | Harman Sezon Takvimi | `finance` | `finance` | Finans |
| 18 | `/stok-hareketleri` | Stok Hareketleri | `read` | — | Stok & Ürünler |
| 19 | `/stok-sayimlari` | Stok Sayımları | `stock` | `stock` | Stok & Ürünler |
| 20 | `/depolar` | Depolar | `stock` | `stock` | Stok & Ürünler |
| 21 | `/sube-transfer` | Şubeler Arası Transfer | `read` | `read` | Stok & Ürünler |
| 22 | `/raporlar` | Raporlar | `reports` | `reports` | Raporlar |
| 23 | `/raporlar/alacak-yaslandirma` | Alacak Yaşlandırma | `reports` | `reports` | Raporlar |
| 24 | `/raporlar/tedarikci-karsilastirma` | Tedarikçi Karşılaştırma | `reports` | `reports` | Raporlar |
| 25 | `/raporlar/satin-alma-panosu` | Satın Alma Panosu | `reports` | `reports` | Raporlar |
| 26 | `/raporlar/emilim-orani` | Emilim Oranı | `reports` | `reports` | Raporlar |
| 27 | `/analizler` | Akıllı Analizler | `reports` | `reports` | Raporlar |
| 28 | `/firmalar` | Firma / Şubeler | `read` ⚠ | **`users`** | Yönetim |
| 29 | `/kullanicilar` | Kullanıcılar | `users` | `users` | Yönetim |
| 30 | `/islem-gecmisi` | İşlem Geçmişi | `users` | `users` | Yönetim |
| 31 | `/aktivite` | Aktivite | `users` | `users` | Yönetim |

**Kabuk içi — menüde görünmeyen 10 route (yalnız derin bağlantıyla):**

| Route | Bileşen | Nereden erişiliyor |
|---|---|---|
| `/musteriler/:id` | EntityDetail | Müşteri listesi satırı |
| `/tedarikciler/:id` | EntityDetail | Tedarikçi listesi satırı |
| `/urunler/:id` | ProductDetail | Ürün listesi + Müşteri 360 "Ürünler" sekmesi |
| `/makineler/:id` | MachineDetail | Makine listesi satırı |
| `/is-emirleri/:id` | WorkOrderDetail | İş emri listesi + Makine kartı |
| `/faturalar/:id` | InvoiceDetail | **Yalnız derin bağlantı** — hiçbir listeden link yok |
| `/depo-transferleri/:id` | TransferDetail | Stok hareketleri |
| `/stok-sayimlari/:id` | InventoryCountDetail | Sayım listesi |
| `/depolar/:id` | WarehouseDetail | Depo listesi |
| `/tanitim` | PremiumHomepage | Kabuk dışı pazarlama sayfası |

### 1.2 Menü neden "karışık"? — somut sayım

| Bulgu | Kanıt | Etki |
|---|---|---|
| **31 üst-düzey madde, 0 grup** | [AppShell.tsx:17](../frontend/src/components/AppShell.tsx:17) — tek `baseItems` dizisi, tek `<List>` | 252px genişlikte, 42px yükseklikli 31 satır ≈ 1300px. 1080p ekranda menü **kaydırma gerektiriyor** (`overflowY:'auto'`, [AppShell.tsx:24](../frontend/src/components/AppShell.tsx:24)). Alttaki 6-8 madde hiç görülmüyor. |
| **6 madde tek bir alt-kırılım** | 22–27: hepsi rapor | Menünün **%19'u** raporlar. `/raporlar` zaten bir hub sayfası; 5 alt rapor doğrudan menüye çıkmış. |
| **2 madde "tanım/ayar" ekranı, günde 1 kez bile açılmaz** | 17 `Harman Sezon Takvimi`, 10 `Parça Supersession` | Sezon takvimi yılda birkaç kez; supersession katalog bakımı. Satış ekranıyla aynı görsel ağırlıkta. |
| **İç içe olması gereken 4 madde ayrı** | 19 Stok Sayımları, 20 Depolar, 21 Şubeler Arası Transfer, 18 Stok Hareketleri — hepsi 9 `Ürünler / Stok`'un alt konusu | Kullanıcı "stok" için 5 farklı yere bakmak zorunda |
| **3 madde 360 kartı olmalı, liste değil** | 11 Makineler, 12 İş Emirleri ayrı; makine kartı zaten iş emirlerini gösteriyor ([MachineDetail.tsx:150](../frontend/src/pages/MachineDetail.tsx:150)) | Aynı veri iki ayrı navigasyon dalından |
| **1 ölü menü maddesi** ⚠ | `/firmalar` menüde `read` ile listeleniyor ([AppShell.tsx:17](../frontend/src/components/AppShell.tsx:17)) ama route `users` istiyor ([App.tsx:116](../frontend/src/App.tsx:116)) | `satis`/`depo`/`rapor`/`muhasebe` rolü menüde görüyor, tıklayınca **sessizce Ana Sayfa'ya atılıyor** (`<Navigate to="/" replace/>`). Hata mesajı yok. |
| **2 sıralama anomalisi** | `Sezonsal Stok Planı` diziye 3. sıraya enjekte ediliyor ([AppShell.tsx:21](../frontend/src/components/AppShell.tsx:21) `slice(0,2)` + `seasonalItem` + `slice(2)`), stok maddelerinden 15 sıra uzakta | Kod okunmadan sıralama mantığı anlaşılmıyor |
| **Komut paletinde izin filtresi yok** | [CommandPalette.tsx:36](../frontend/src/components/CommandPalette.tsx:36) — `filteredActions` yalnız metin filtreliyor, `useAuth`/`can()` hiç çağrılmıyor | `depo` rolü Ctrl+K'da "Yeni satış oluştur" görüyor; menüde görmüyor. İki navigasyon yüzeyi çelişiyor. |
| **3 yetim sayfa dosyası** | `pages/Customers.tsx`, `pages/Orders.tsx`, `pages/InventoryCounts.tsx` — App.tsx'te import edilmiyor (`/stok-sayimlari` rotası `InventoryCountsReport`'u yüklüyor, [App.tsx:26](../frontend/src/App.tsx:26)) | Ölü kod; bakım yükü ve "hangi dosya gerçek?" karışıklığı |

### 1.3 Mevcut rol → görünen madde sayısı

Roller [auth.py:101](../backend/app/auth.py:101)'den:

| Rol | İzinler | **Bugün gördüğü madde** |
|---|---|---|
| `admin` | `*` | **31 / 31** |
| `yonetici` | read, sales, purchases, payments, finance, stock, reports, users, machines | **31 / 31** |
| `muhasebe` | read, sales, purchases, payments, finance, reports | **26 / 31** |
| `rapor` | read, reports | **21 / 31** |
| `satis` | read, sales, payments | **18 / 31** |
| `depo` | read, stock, purchases | **17 / 31** |

**Kritik gözlem:** menü maddelerinin **15'i (%48) sadece `read` gerektiriyor** — yani her rol görüyor. `satis` rolü 18 madde görüyor; bunların içinde Parça Supersession, Sezonsal Stok Planı, Şubeler Arası Transfer, Tahsis Defteri ve (ölü) Firma/Şubeler var. Satış görevlisinin günlük işiyle ilgisi olan madde sayısı **6-7**. Yani **satış görevlisi için menünün ~%60'ı gürültü.**

---

## 2. MENÜ GRUPLAMA ÖNERİSİ

### 2.1 Hedef yapı — 2 sabit + 7 grup

Sabitler (grup dışı, en üstte, tek tık):

- **Ana Sayfa** (`/`)
- **Hızlı Satış** (`/hizli-satis`) — *POS menüde kalır ama grubun içine gömülmez;* gerekçe §2.4.

Gruplar (açılır/kapanır, varsayılan kapalı — aktif route'un grubu otomatik açık):

| Grup | Maddeler | Grup izni (herhangi biri) |
|---|---|---|
| **Satış** | Satışlar · Teklif / Sipariş / İade | `read` |
| **Müşteriler** | Müşteri Listesi → *360 kartı* | `read` |
| **Stok & Ürünler** | Ürünler · Stok Hareketleri · Depolar · Stok Sayımları · Şubeler Arası Transfer · Sezonsal Stok Planı · Parça Supersession | `read` |
| **Satın Alma** | Alışlar · Tedarikçiler · Satın Alma Panosu · Tedarikçi Karşılaştırma | `read` |
| **Servis** | İş Emirleri · Makineler → *360 kartı* · Teknisyenler¹ | `read` |
| **Finans** | Tahsilat / Ödeme · Alacaklar (Harman Vadesi) · Tahsis Defteri · Nakit Yönetimi · Alacak Yaşlandırma · Harman Sezon Takvimi | `payments` \| `finance` \| `read` |
| **Yönetim** | Kullanıcılar · Firma / Şubeler · Aktivite · İşlem Geçmişi · Raporlar & Analizler² | `users` \| `reports` |

¹ `GET /technician-profiles` ucu var ([technician_profiles.py:63](../backend/app/routers/technician_profiles.py:63)) ama **frontend sayfası yok** — U1'de menüye konmaz, U3'te Servis grubuna eklenir. Şimdilik listede yer tutucu olarak işaretlendi.
² Raporlar hub'ı (`/raporlar`) + Akıllı Analizler + Emilim Oranı. Satın alma/alacak raporları kendi iş grubuna taşındı (§2.2).

**Sonuç:** üst düzey madde sayısı **31 → 9** (2 sabit + 7 grup). Kaydırma ihtiyacı ortadan kalkıyor.

### 2.2 Raporların dağıtımı — bilinçli karar

6 rapor maddesi tek "Raporlar" grubuna yığılmıyor; **kullanıldıkları iş grubuna** dağıtılıyor:

| Rapor | Bugün | Öneri | Gerekçe |
|---|---|---|---|
| Satın Alma Panosu | Raporlar | **Satın Alma** | Satın almacının günlük ekranı, ayda bir bakılan rapor değil |
| Tedarikçi Karşılaştırma | Raporlar | **Satın Alma** | Alış kararı verirken açılır |
| Alacak Yaşlandırma | Raporlar | **Finans** | Tahsilat görüşmesinin girdisi |
| Raporlar (hub) | Raporlar | **Yönetim** | Genel rapor merkezi |
| Emilim Oranı | Raporlar | **Yönetim → Raporlar** | Yönetim metriği |
| Akıllı Analizler | Raporlar | **Yönetim → Raporlar** | Yönetim metriği |

> ⚠️ **Açık soru B (§8):** Bu dağıtım "raporu iş akışına götür" felsefesine dayanıyor. Berkay "tüm raporlar tek yerde olsun" derse 7. grup `Raporlar` olur, `Yönetim` 8. gruba çıkar — hâlâ hedef aralıkta.

### 2.3 Rol bazlı görünürlük — önerilen tablo

Grup, **içinde en az bir görünür madde varsa** görünür (menü izni ile route izni artık aynı kaynaktan gelir — §2.5).

| Grup / Madde | admin | yonetici | muhasebe | satis | depo | rapor |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Ana Sayfa | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Hızlı Satış** | ✓ | ✓ | ✓ | ✓ | — | — |
| **Satış** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Satışlar | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| └ Teklif/Sipariş/İade | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Müşteriler** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Stok & Ürünler** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Ürünler | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Stok Hareketleri | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Depolar | ✓ | ✓ | — | — | ✓ | — |
| ├ Stok Sayımları | ✓ | ✓ | — | — | ✓ | — |
| ├ Şubeler Arası Transfer | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Sezonsal Stok Planı | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| └ Parça Supersession | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Satın Alma** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Alışlar | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Tedarikçiler | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Satın Alma Panosu | ✓ | ✓ | ✓ | — | — | ✓ |
| └ Tedarikçi Karşılaştırma | ✓ | ✓ | ✓ | — | — | ✓ |
| **Servis** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ İş Emirleri | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Makineler | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| └ Teknisyenler¹ | ✓ | ✓ | — | — | — | — |
| **Finans** | ✓ | ✓ | ✓ | ✓ | ✓* | ✓* |
| ├ Tahsilat / Ödeme | ✓ | ✓ | ✓ | ✓ | — | — |
| ├ Alacaklar (Harman Vadesi) | ✓ | ✓ | ✓ | ✓ | — | — |
| ├ Tahsis Defteri | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ├ Nakit Yönetimi | ✓ | ✓ | ✓ | — | — | — |
| ├ Alacak Yaşlandırma | ✓ | ✓ | ✓ | — | — | ✓ |
| └ Harman Sezon Takvimi | ✓ | ✓ | ✓ | — | — | — |
| **Yönetim** | ✓ | ✓ | ✓ | — | — | ✓ |
| ├ Raporlar & Analizler | ✓ | ✓ | ✓ | — | — | ✓ |
| ├ Emilim Oranı | ✓ | ✓ | ✓ | — | — | ✓ |
| ├ Kullanıcılar | ✓ | ✓ | — | — | — | — |
| ├ Firma / Şubeler | ✓ | ✓ | — | — | — | — |
| ├ Aktivite | ✓ | ✓ | — | — | — | — |
| └ İşlem Geçmişi | ✓ | ✓ | — | — | — | — |

\* `depo` ve `rapor` rolleri Finans grubunu **yalnız Tahsis Defteri** için görür — o rota bugün `read` iznine bağlı ([App.tsx:91](../frontend/src/App.tsx:91)). Tek maddeli bir grup gösterip göstermemek bir tercih: alternatifi Tahsis Defteri'ni `payments` iznine taşımaktır ve bu bir **izin matrisi değişikliğidir** → §8 açık soru D kapsamında.

**Üst düzey madde sayısı — rol bazında** (mockup'ta doğrulandı, §7):

| Rol | Bugün (düz) | Öneri (üst düzey) | Öneri (açıldığında toplam alt madde) |
|---|:--:|:--:|:--:|
| admin | 31 | **9** | 29 |
| yonetici | 31 | **9** | 29 |
| muhasebe | 26 | **9** | 22 |
| rapor | 21 | **8** | 18 |
| satis | 18 | **8** | 15 |
| depo | 17 | **7** | 15 |

**Satış görevlisi (`satis`) ne görür?** Ana Sayfa + Hızlı Satış + 6 grup = **8 üst-düzey madde**; gruplar açıldığında 15 alt madde. Bugünkü 18 düz maddeye karşılık. Toplam benzer ama **ilk bakışta görülen** 18 → 8'e düşüyor, ilgisiz olanlar bir grubun arkasında ve hiçbiri ölü değil.

**En büyük kazanç `depo` rolünde:** bugün 17 düz madde görüyor, bunların 9'u (satış belgeleri, tahsis defteri, sezonsal plan, supersession…) işiyle ilgisiz. Grup yapısında ilk bakışta **7** madde görüyor.

### 2.4 POS (Hızlı Satış) menüde nerede?

**Öneri: menüde en üstte, sabit, grup dışında.** Gerekçeler:

- POS **dokunmatik ve tek elle** kullanılıyor — grup açıp içinden madde seçmek 2 dokunuş, kabul edilemez.
- Gün içinde en çok açılan ekran; grubun içine gömmek en sık işi en derine koymak olur.
- `sales` izni gerektirdiği için `depo`/`rapor` rolünde zaten görünmüyor — grup gürültüsü yaratmıyor.

**Alternatif (Berkay tercih ederse):** POS'u menüden **tamamen çıkarıp** üst çubuğa kalıcı bir birincil buton yapmak. POS tam ekran bir mod olduğu için savunulabilir. → **Açık soru C (§8).**

### 2.5 Menü izni ile route izninin tek kaynağa bağlanması

Bugün izinler iki yerde ayrı yazılı: menü dizisi ([AppShell.tsx:17](../frontend/src/components/AppShell.tsx:17)) ve route sarmalayıcısı ([App.tsx:89-119](../frontend/src/App.tsx:89)). `/firmalar` uyuşmazlığı (§1.2) bunun doğrudan sonucu.

**U1'in bir parçası:** tek bir navigasyon tanımı (route + etiket + ikon + izin + grup) ve hem `AppShell` hem `App` bunu okur. Böylece:
- ölü menü maddesi yapısal olarak imkânsız hale gelir;
- `CommandPalette` de aynı listeden beslenip izin filtresi kazanır (§1.2 son satır).

> Bu bir mimari genişletme değil, mevcut iki listeyi birleştirme. Yeni soyutlama (route registry framework'ü vb.) **önerilmiyor**.

---

## 3. MÜŞTERİ 360

### 3.1 Bugün ne var?

`EntityDetail` ([EntityDetail.tsx:51](../frontend/src/pages/EntityDetail.tsx:51)) müşteri ve tedarikçi için **ortak** bileşen. Mevcut yapı:

- Yapışkan başlık kartı: ad, aktif/pasif, risk aşımı rozeti, telefon/e-posta, hızlı aksiyonlar (Satış Yap, Tahsilat Al, Ekstre, Düzenle, ara/WhatsApp/e-posta)
- 4 tıklanabilir KPI: Açık Bakiye, Toplam Ciro, Vadesi Geçen, Risk Kullanımı
- 8 sekme: Genel Bakış · Satışlar · Tahsilatlar · Zaman Çizelgesi · Ürünler · Yetkililer · Görevler · Notlar

**Tek uçtan besleniyor:** `GET /customers/{id}` ([customers.py:88](../backend/app/routers/customers.py:88)) → `entity, summary, documents, payments, products, notes, contacts, tasks`.

### 3.2 Hedef sekme yapısı

| Sekme | İçerik | Durum |
|---|---|---|
| **Özet** | Bakiye · açık alacak · vadesi geçen · risk · son işlemler · **harman vadesi durumu** · açık görevler · müşteri sağlığı | Kısmen var — harman bloğu **yeni** |
| **Satışlar** | Belge listesi → belge dialogu | ✓ Var (sekme 1) |
| **Ödemeler / Tahsisler** | Tahsilat listesi + **hangi belgeye tahsis edildiği** | Kısmen — tahsis kırılımı **yeni** |
| **Harman Vadesi** | Bağlı sezon/takvim · bölge · vade kuralı · vade farkı belgeleri | **Yeni** |
| **Makineler** | Bu müşterinin makineleri → makine 360 kartına link | **Yeni** (uç hazır) |
| **Servis Geçmişi** | Müşterinin iş emirleri (makineden bağımsız) | **Yeni** (uç eksik) |
| **Aktivite** | Bu müşteriyle ilgili sistem olayları | **Yeni** (uç eksik) |
| Ürünler · Yetkililer · Görevler · Notlar | mevcut | ✓ Var — "Daha fazla" alt sekmesine taşınabilir |

> Sekme sayısı 8 → 11'e çıkıyor. Mevcut `variant="scrollable"` bunu taşır ama **dokunmatikte kaydırmalı sekme kötü**. Öneri: **birincil 7 sekme** (Özet, Satışlar, Ödemeler, Harman Vadesi, Makineler, Servis, Aktivite) + son sekme **"Daha Fazla"** altında Ürünler/Yetkililer/Görevler/Notlar. Mockup bunu gösteriyor.

### 3.3 Veri kaynağı eşlemesi — hangi uç var, hangisi yok

| Sekme / blok | Gerekli veri | Uç | Durum |
|---|---|---|---|
| Özet — bakiye/risk/vade | summary | `GET /customers/{id}` → `summary` | **VAR** |
| Özet — son işlemler | documents+payments+notes+tasks birleşimi | aynı uç, frontend'de birleştiriliyor ([EntityDetail.tsx:67](../frontend/src/pages/EntityDetail.tsx:67)) | **VAR** |
| Satışlar | belge listesi | aynı uç → `documents` | **VAR** |
| Ödemeler | tahsilat listesi | aynı uç → `payments` | **VAR** |
| Ödemeler — tahsis kırılımı | ödeme → belge eşleşmesi | `GET /payment-allocations/payments/{payment_id}` ([payment_allocations.py:70](../backend/app/routers/payment_allocations.py:70)) | **VAR** — ama **ödeme başına 1 istek**. Müşteri bazlı toplu uç yok. |
| Harman Vadesi — vade çözümü | sezon/takvim/bölge/kural | `GET /harvest-scheduling/preview?customer_id=&transaction_date=&product_ids=` ([harvest_scheduling.py:619](../backend/app/routers/harvest_scheduling.py:619)) | **VAR** — ama `product_ids` **zorunlu** (`min_length=1`). Ürün seçmeden "bu müşterinin sezonu ne?" sorulamıyor. |
| Harman Vadesi — müşteri bölgesi | `customer.harvest_region_id` | `PUT /harvest-scheduling/customers/{id}/region` (yazma) | **Okuma ucu belirsiz** — `GET /customers/{id}` yanıtı ham `customers` satırını döndüğü için kolon varsa geliyor; sözleşmede garanti değil |
| Harman Vadesi — belgedeki vade | `harvest_calendar_id`, `due_date_source` | `orders` tablosunda yazılıyor ([transactions.py:530](../backend/app/routers/transactions.py:530)) | **VAR (veride)** — ama `GET /customers/{id}` `documents` SELECT'i bu kolonları **seçmiyor** ([customers.py:93](../backend/app/routers/customers.py:93)) |
| Harman Vadesi — vade farkı belgeleri | gecikme faizi belgeleri | `GET /finance/late-fees/preview?customer_id=` ([late_fees.py:56](../backend/app/routers/late_fees.py:56)) **VAR**; belge listesi için yalnız `GET /finance/late-fees/charges/{document_id}` (tekil) | **Kısmen** — müşteri bazlı **belge listesi ucu yok** |
| Makineler | müşterinin makineleri | `GET /machines?customer_id={id}` ([machines.py:268](../backend/app/routers/machines.py:268)) | **VAR** ✓ |
| Servis Geçmişi | müşterinin iş emirleri | `GET /work-orders` — `customer` param'ı **ad metni** araması (`LIKE %ad%`), `customer_id` **yok** ([work_orders.py:228](../backend/app/routers/work_orders.py:228)) | **EKSİK** — ada göre arama aynı adlı müşterileri karıştırır; kimlik bazlı filtre gerekli |
| Aktivite | müşteriyle ilgili olaylar | `GET /activity-logs` — `user_id`, `action_type`, `resource_type`, tarih filtreleri var; **`resource_id` yok** ([activity_logs.py:68](../backend/app/routers/activity_logs.py:68)) | **EKSİK** — "şu müşteriye ait olaylar" sorgulanamıyor |

### 3.4 Eksik uç listesi (kod önerisi yok, yalnız ihtiyaç)

| # | Gerekli veri | Bugünkü durum | Kritiklik |
|---|---|---|---|
| C-1 | İş emirlerini **müşteri kimliğiyle** filtreleme | yalnız ad metni araması | **Yüksek** — Servis Geçmişi sekmesi bunsuz doğru çalışmaz |
| C-2 | Aktivite kayıtlarını **kaynak kimliğiyle** filtreleme | `resource_id` filtresi yok | **Yüksek** — Aktivite sekmesi bunsuz mümkün değil |
| C-3 | Müşteri belgelerinde harman vade alanları (`harvest_calendar_id`, `due_date_source`, sezon adı) | veride var, `GET /customers/{id}` seçmiyor | **Orta** — Harman Vadesi sekmesi için |
| C-4 | Müşteriye ait **vade farkı belgeleri listesi** | yalnız tekil belge + preview | **Orta** |
| C-5 | Ürün seçmeden müşterinin **aktif sezon/bölge özeti** | `preview` `product_ids` zorunlu | **Düşük** — Özet kartındaki "harman durumu" rozeti için; C-3 ile kısmen telafi edilebilir |
| C-6 | Müşteri bazlı **toplu tahsis** listesi | ödeme başına ayrı istek | **Düşük** — N+1 istek performans sorunu, işlevsel engel değil |

---

## 4. MAKİNE 360

### 4.1 Bugün ne var?

`MachineDetail` ([MachineDetail.tsx:67](../frontend/src/pages/MachineDetail.tsx:67)) — **sekmesiz, uzun kaydırmalı** tek sayfa, 4 `Paper` bloğu:

1. Makine Bilgileri (marka, model, üretici, varyant, seri, şasi, plaka, motor no, model yılı, çalışma saati, müşteri linki, kayıt tarihi, notlar)
2. Sayaç Okumaları (tarih, saat, tür, kaynak→iş emri linki, açıklama, kaydeden) + "Yeni Okuma"
3. Sahiplik Tarihçesi (tarih, eski/yeni sahip linkleri, açıklama, kaydeden) + "Sahiplik Devret"
4. Servis Geçmişi / İş Emirleri (no, durum, öncelik, açılış, şikâyet, işçilik, parça, genel toplam)

**F1 gerçekten besliyor:** istenen 6 sekmenin 4'ü veri olarak zaten hazır.

### 4.2 Hedef sekme yapısı ve uç eşlemesi

| Sekme | İçerik | Uç | Durum |
|---|---|---|---|
| **Kimlik** | seri / şasi / motor / plaka / marka / model / üretici / varyant / model yılı / sahip / durum | `GET /machines/{id}` ([machines.py:318](../backend/app/routers/machines.py:318)) | **VAR** ✓ |
| **Sayaç Geçmişi** | okuma tablosu + anomali/değişim/düzeltme türleri + kaynak iş emri | `GET /machines/{id}/hour-readings` ([machine_hour_readings.py:66](../backend/app/routers/machine_hour_readings.py:66)) | **VAR** ✓ |
| **Sahiplik Tarihçesi** | devir kayıtları + eski/yeni sahip linkleri | `GET /machines/{id}/ownership-history` ([machine_ownership.py:35](../backend/app/routers/machine_ownership.py:35)) | **VAR** ✓ |
| **Servis / İş Emirleri** | makinenin iş emirleri | `GET /work-orders?machine_id={id}` ([work_orders.py:228](../backend/app/routers/work_orders.py:228)) | **VAR** ✓ |
| **Kullanılan Parçalar** | makinede bugüne dek kullanılmış parçaların birleşik listesi | `GET /work-orders/{wo_id}/parts` ([work_order_parts.py:82](../backend/app/routers/work_order_parts.py:82)) — **yalnız iş emri başına** | **EKSİK (M-1)** |
| **Ekler (foto)** | makineye ait fotoğraf/imza/belge | `GET /work-order-attachments/{wo_id}` ([work_order_attachments.py:130](../backend/app/routers/work_order_attachments.py:130)) — **iş emrine bağlı**, makineye değil | **EKSİK (M-2)** |

### 4.3 Eksik uç listesi

| # | Gerekli veri | Bugünkü durum | Kritiklik |
|---|---|---|---|
| M-1 | Makine bazlı **kullanılan parçalar** toplamı | yalnız `work-orders/{id}/parts`; makinenin N iş emri için N istek | **Yüksek** — sekme bunsuz N+1 istekle çalışır; 30 iş emirli makinede kabul edilemez |
| M-2 | Makine bazlı **ekler** (foto) | ekler yalnız iş emrine bağlı; makinenin "kimlik fotoğrafı" kavramı yok | **Orta** — geçici çözüm: iş emirlerinin eklerini toplayıp göstermek (yine N+1) |
| M-3 | Makine kartında **son okuma / ortalama saat** özeti | `working_hours` var, trend/ortalama yok | **Düşük** — sekme başlığındaki özet rozet için |

### 4.4 Neden sekmeli olmalı?

Bugünkü sayfa 4 tabloyu alt alta koyuyor. 20 sayaç okuması + 15 sahiplik kaydı + 30 iş emri olan bir makinede sayfa **~4000px**. Servis danışmanı "bu makinenin son iş emri neydi?" için 3 tablo kaydırıyor. Sekme, aynı veriyi sabit yükseklikte sunar; ayrıca mobil/tablet servis kullanımı için şart.

---

## 5. MODERN GÖRÜNÜM — mevcut sorunlar ve token planı

### 5.1 Gerçekten gördüğüm somut sorunlar

| # | Sorun | Kanıt | Etki |
|---|---|---|---|
| **G-1** | **Kenar çubuğu token'ı yok sayıyor** | `theme.ts` `sidebar:'#14261a'` (yeşil-siyah, [theme.ts:36](../frontend/src/theme.ts:36)) — `AppShell` `bgcolor: mode==='dark'?'#0b111a':'#111c2d'` (**mavi-siyah**) ve aktif madde `rgba(88,132,235,.18)` + `#80aaff` (**mavi**) ([AppShell.tsx:24](../frontend/src/components/AppShell.tsx:24)) | Marka yeşil, navigasyon mavi. Uygulamanın en görünür yüzeyi tema dışında. |
| **G-2** | **Üçüncü bir yeşil** | Makine listesi marka çipi `bgcolor:'#2f6b3b'` ([Machines.tsx](../frontend/src/pages/Machines.tsx)) — token primary `#2f7d32` | Aynı ekranda iki farklı yeşil |
| **G-3** | **Tipografi ölçeği aşılıyor** | Tema en ağır ağırlık 700 ([theme.ts:92-101](../frontend/src/theme.ts:92)); sayfalarda `fontWeight={900}`, `fontWeight={750}`, `fontWeight={800}`, `fontSize={15}`, `fontSize={16}` inline ([EntityDetail.tsx:113,143,144](../frontend/src/pages/EntityDetail.tsx:113)) | 900 sistem fontunda 700'e yuvarlanır → tasarımcının beklediği hiyerarşi oluşmaz, ama kod ölçeği aştığı için tema değişince tahmin edilemez sonuç |
| **G-4** | **Boşluk ölçeği yerine keyfi ondalıklar** | `spacing:8` tanımlı ([theme.ts:86](../frontend/src/theme.ts:86)) ama kodda `p:1.5`, `py:1.1`, `mr:1.4`, `px:2.5`, `spacing={1.25}`, `my:.25`, `pb:.75` | 8px ızgara fiilen yok; 8.8px, 11.2px, 20px karışık |
| **G-5** | **Sayfa başlığı aksiyon çubuğunun altında** | `Entities`, `Machines`: önce `<Paper>` toolbar, **sonra** `<Typography variant="h4">` başlık | Okuma sırası bozuk; her liste sayfası aynı hatayı tekrarlıyor |
| **G-6** | **`variant="h4"` sayfa başlığı olarak kullanılıyor** | `h4` teması `1.375rem` — `h1` ile **aynı boyut** ([theme.ts:92,95](../frontend/src/theme.ts:92)) | Semantik başlık düzeyi yok; ekran okuyucu için `h1` hiç yok |
| **G-7** | **Dokunmatik hedefler 44px altında** | Tema `MuiOutlinedInput` `minHeight:40`, `MuiButton` `minHeight:40`, `sizeSmall:34` ([theme.ts:114-137](../frontend/src/theme.ts:114)). POS sepet satırlarında fiyat/miktar/iskonto alanları ve sil `IconButton` bu boyutta ([Pos.tsx:171](../frontend/src/pages/Pos.tsx:171)) | **POS dokunmatik kullanılıyor.** 40px hedef, eldivenli/tozlu elle yanlış dokunuş üretir. Tek 56px hedef "Satışı Tamamla" ([Pos.tsx:186](../frontend/src/pages/Pos.tsx:186)) |
| **G-8** | **Stil satır içinde, yeniden kullanılabilir bileşen yok** | `sx={{...}}` kullanımı: `ProductDetail` 29, `Dashboard` 29, `PurchaseDashboard` 28, `PurchaseComparison` 20, `Receivables` 17… | Aynı "KPI kartı", "liste satırı", "sayfa başlığı" 10+ yerde kopyalanmış; bir değişiklik 10 dosya demek |
| **G-9** | **Liste sayfalarında tutarsız durum çipi dili** | `Entities`: ad çipi `color="info"` (mavi) + risk aşımında `error`; `Machines`: ad çipi düz hex yeşil; `WorkOrders`: `WO_STATUS_COLORS` haritası | "Renk ne anlama geliyor?" sorusunun cevabı sayfadan sayfaya değişiyor |

### 5.2 Tasarım token'ları — mevcut yapı içinde

`sungurTokens` **zaten doğru yerde**. Öneri onu değiştirmek değil, **tamamlamak ve zorunlu kılmak**.

**Renk** — mevcut palet korunur, eksikler eklenir:

```
brand.primary      #2f7d32   (var)      brand.deep     #245f27  (var)
brand.tint         #eaf3ea   (var)
ink                #1c2620   (var)      muted          #6b7a70  (var)      faint  #93a099 (var)
surface            #ffffff   (var)      background     #f4f6f2  (var)      line   #e5e9e3 (var)
danger/tint · warning/tint                                                  (var)
info               #3f708f   (temada var, token'da YOK → eklenecek)
sidebar.bg         #14261a   (token'da var, KULLANILMIYOR → G-1)
sidebar.active     brand.tint üzerine %18 → token'a eklenecek (bugün mavi hardcoded)
```

**Boşluk** — `spacing:8` korunur, **izinli çarpanlar**: `0.5, 1, 1.5, 2, 3, 4, 6, 8` (yani 4/8/12/16/24/32/48/64px). `1.1`, `1.25`, `1.4`, `2.5`, `.75` gibi değerler yasak.

**Tipografi** — `h1`–`h6` yeniden anlamlandırılır ve **inline fontSize/fontWeight yasaklanır**:

| Rol | Token | Boyut / ağırlık |
|---|---|---|
| Sayfa başlığı | `h1` | 1.5rem / 700 |
| Bölüm başlığı | `h2` | 1.25rem / 700 |
| Kart başlığı | `h3` | 1.0625rem / 650 |
| KPI değeri | `h4` | 1.75rem / 700, `tabular-nums` |
| Gövde | `body1` | .875rem / 400 |
| İkincil | `body2` | .8125rem / 400 |
| Etiket/üst bilgi | `caption` | .75rem / 600 |

**Yarıçap ve gölge:** mevcut (`card:14, control:10, compact:8`; tek `card` gölgesi) yeterli, değişiklik önerilmiyor.

**Dokunmatik ölçek (yeni):**

```
touch.min      44px   — dokunmatik bağlamda her etkileşimli hedefin tabanı
touch.pos      56px   — POS birincil aksiyonları (bugün "Satışı Tamamla" zaten 56)
touch.posGrid  72px   — POS ürün/tuş ızgarası
```

> POS için ayrı bir **yoğunluk modu** öneriliyor: masaüstü ekranlar 40px'te kalır (bilgi yoğunluğu değerli), POS rotası `44/56/72` ölçeğine geçer. MUI'da bu, POS ağacını saran bir tema katmanıdır — kütüphane değişimi değil.

### 5.3 Bileşen envanteri — standartlaştırılacaklar

Hedef: G-8'deki kopyalamayı bitirmek. **Yeni tasarım dili değil, mevcut kalıbın tek yerde toplanması.**

| Bileşen | Bugün nerede kopyalanmış | Standart sözleşme |
|---|---|---|
| `PageHeader` | Her liste sayfası (başlık + aksiyon + sayaç çipleri, sırası tutarsız — G-5) | başlık(`h1`) → açıklama → aksiyonlar → filtreler; **her zaman bu sırada** |
| `KpiCard` | `EntityDetail:134`, `Dashboard`, `PurchaseDashboard`, `Receivables` | etiket / değer(`h4`) / alt açıklama / opsiyonel progress; tıklanabilirse `role="button"` + klavye desteği (EntityDetail'deki kalıp doğru, o örnek alınmalı) |
| `DataTable` | `ResponsiveTable` **zaten var ve doğru** ([ResponsiveTable.tsx](../frontend/src/components/ResponsiveTable.tsx)) | mevcut; tüm listeler buna geçmeli, elle `<Table>` kurulmamalı (MachineDetail 3 elle tablo kuruyor) |
| `StatusChip` | `Entities`, `Machines`, `WorkOrders`, `EntityDetail` ayrı ayrı | tek anlam sözlüğü: success=olumlu/tamam, warning=dikkat/bekliyor, error=risk/gecikme, default=nötr/pasif, info=bilgi. Hex renk **yasak** (G-2) |
| `EntityHeaderCard` | `EntityDetail:106` (yapışkan başlık) | Müşteri 360 + Makine 360 aynı kalıbı paylaşır |
| `TabbedDetailShell` | `EntityDetail` sekmeli, `MachineDetail` değil | başlık + KPI şeridi + sekmeler; iki 360 ekranı da bunu kullanır |
| `FormDialog` | 12+ dialog (`EntityDetail`de 3 tane inline) | başlık / içerik / `Vazgeç`+birincil aksiyon; hata `Alert`'i içerik üstünde |
| `EmptyState` | Her yerde çıplak `<Typography color="text.secondary">Kayıt yok.</Typography>` | ikon + açıklama + (varsa) birincil aksiyon. `EntityDetail:147`'deki "İlk görevi oluştur" doğru örnek |

### 5.4 Kütüphane kararı

**MUI değişmiyor.** Gerekçe: 34 sayfa, `@mui/x-data-grid` bağımlılığı, tema augmentation, `ResponsiveTable` soyutlaması ve mevcut testler MUI'ya bağlı. Değişim, ürünü aylarca dondurur ve bu görevin kapsamında olmayan bir risktir. Tüm modernleşme **mevcut tema + bileşen katmanı içinde** yapılır.

---

## 6. GEÇİŞ PLANI — big-bang yok

Her faz **ayrı PR**, ayrı dal, tek başına sevk edilebilir ve geri alınabilir. Hiçbir faz mevcut route'u kaldırmaz.

### U1 — Menü gruplama (yalnız navigasyon)

- **Kapsam:** tek navigasyon tanımı (route+etiket+ikon+izin+grup); `AppShell` gruplu render; aktif grubun otomatik açılması; `CommandPalette`'in aynı listeden beslenip izin filtresi kazanması; `/firmalar` izin uyuşmazlığının giderilmesi.
- **Değişmeyen:** hiçbir sayfa, hiçbir URL. Tüm mevcut URL'ler aynen çalışır (yer imleri kırılmaz).
- **Risk:** düşük. Yüzey: `AppShell.tsx`, `CommandPalette.tsx`, yeni navigasyon tanım dosyası, `App.tsx`'te izin okuması.
- **Kabul:** her rol için görünür madde kümesi §2.3 tablosuyla birebir; ölü menü maddesi yok; `depo` rolü Ctrl+K'da satış aksiyonu görmüyor.

### U2 — Müşteri 360

- **Kapsam:** `EntityDetail` sekme mimarisi (7 birincil + "Daha Fazla"); **Makineler** sekmesi (uç hazır, §3.3); Özet kartına harman durumu bloğu; Ödemeler sekmesine tahsis kırılımı.
- **Bağımlılık:** Servis Geçmişi ve Aktivite sekmeleri **C-1 / C-2 uçlarını bekler**. Uçlar yoksa bu iki sekme **U2'ye alınmaz** — sekme sayısı 5 birincil olur, sonra eklenir. (Sahte veriyle sekme açmak `AGENTS.md` "mock iş verisi yok" kuralına aykırı.)
- **Risk:** orta — `EntityDetail` tedarikçi için de kullanılıyor; yeni sekmeler `type==='customer'` koşullu olmalı, tedarikçi görünümü kırılmamalı.
- **Kabul:** mevcut 8 sekmenin içeriği aynen erişilebilir; tedarikçi kartı davranışı değişmemiş.

### U3 — Makine 360

- **Kapsam:** `MachineDetail`'in sekmeli kabuğa alınması (Kimlik / Sayaç / Sahiplik / Servis) — **mevcut 4 blok, 4 sekme, yeni uç gerekmez**; elle kurulmuş 3 tablonun `ResponsiveTable`'a geçmesi; Servis grubuna Teknisyenler sayfası.
- **Bağımlılık:** Kullanılan Parçalar (M-1) ve Ekler (M-2) sekmeleri uç bekler; U3'te yer tutucu bile konmaz.
- **Risk:** düşük — veri katmanı aynen kalıyor, yalnız yerleşim değişiyor.

### U4 — Görsel token'lar, sayfa sayfa

- **U4.0 (tek seferlik, düşük risk):** kenar çubuğunu token'a bağla (G-1), üçüncü yeşili kaldır (G-2), token'a `info`/`sidebar.active` ekle, `h1`–`h4` ölçeğini yeniden anlamlandır.
- **U4.1:** `PageHeader` + `KpiCard` + `StatusChip` + `EmptyState` bileşenleri; **yalnız 2 sayfada** uygula (pilot: `Entities`, `Machines`).
- **U4.2+:** sayfa başına birer PR — her PR bir sayfayı token'lara ve ortak bileşenlere taşır, inline `fontSize/fontWeight` ve ızgara dışı boşlukları temizler.
- **U4.POS (ayrı ve öncelikli):** POS yoğunluk modu — `44/56/72` dokunmatik ölçeği (G-7). Bu, görsel değil **kullanılabilirlik/hata oranı** meselesi; U4'ün başında yapılmalı, hatta U1'den önce bile savunulabilir.
- **Risk:** her PR tek sayfa → geri alma bir dosya.

### Sıralama önerisi

```
U4.POS  →  U1  →  U3  →  U2  →  U4.0  →  U4.1  →  U4.2…
(dokunmatik  (nav)   (kolay,   (uç      (token)  (pilot)  (sayfa
 hata)               uç yok)   bekler)                     sayfa)
```

Gerekçe: U4.POS gerçek para/hata riski taşıyor. U3, U2'den **önce** çünkü yeni uç gerektirmiyor — hızlı görünür kazanç. U2 eksik uçlar tamamlandıkça genişler.

### Bağımlılık: uçlar ne zaman?

C-1, C-2 (yüksek kritiklik) U2'nin ön koşulu; M-1, M-2 U3'ün genişlemesinin ön koşulu. Bunlar **backend işi** ve bu tasarımın kapsamı dışında — ayrı görev olarak açılmalı. Bu doküman yalnız "gerekli veri: var/yok" tespitini veriyor, uç tasarımı önermiyor.

---

## 7. MOCKUP

`ux-yenileme-f0-mockup.html` — tek dosya, bağımlılık yok, çift tıkla açılır.

İçerik:
- Gruplu kenar çubuğu (7 grup + 2 sabit), açılır/kapanır, aktif grup açık
- **Rol seçici** — `admin / yonetici / muhasebe / satis / depo / rapor` arasında geçiş, menü §2.3 tablosuna göre canlı daralıyor (satış görevlisinin sade menüsü doğrudan görülebiliyor)
- Müşteri 360 örnek ekranı: başlık kartı + 4 KPI + 8 sekme; **Özet, Harman Vadesi, Makineler ve Servis Geçmişi sekmeleri dolu**
- Önerilen token'lar uygulanmış (yeşil kenar çubuğu, 8px ızgara, tipografi ölçeği), açık/koyu tema anahtarı
- Dokunmatik ölçek anahtarı: masaüstü (40px) ↔ POS (44/56px) farkı gözle görülebiliyor

Tüm veriler **sahte ve öyle etiketli**. Repoya bağlanmaz, build'e girmez.

---

## 8. AÇIK SORULAR — Berkay'a

| # | Soru | Neden önemli | Varsayılan (cevap gelmezse) |
|---|---|---|---|
| **A** | **Grup adları** doğru mu? Özellikle "Stok & Ürünler" mi, "Stok" mu, "Ürünler & Depo" mu? "Yönetim" mi "Ayarlar" mı? | Adlar U1'de dondurulup navigasyon tanımına yazılıyor; sonra değiştirmek her kullanıcının kas hafızasını bozar | §2.1'deki adlar |
| **B** | **Raporlar dağıtılsın mı, tek grupta mı toplansın?** (§2.2) | 6 rapor maddesinin yeri; grup sayısı 7 mi 8 mi | Dağıtılsın (§2.2 tablosu) |
| **C** | **POS menüde mi kalsın, üst çubukta birincil buton mu olsun?** (§2.4) | POS gün içinde en çok açılan ekran; bir dokunuş farkı | Menüde en üstte sabit |
| **D** | **Satış görevlisi (`satis`) neyi görmemeli?** Bugün gördüğü ama kaldırılması gerekenler: Parça Supersession · Sezonsal Stok Planı · Şubeler Arası Transfer · Tahsis Defteri — hangileri kalsın? | §2.3 tablosunda bunları görünür bıraktım (bugünkü davranışı korumak için). Kaldırmak **izin matrisi değişikliği** demek → backend `ROLE_PERMISSIONS` etkilenir, ayrı karar | Bugünkü gibi görünür kalsın |
| **E** | **Müşteri 360'ta 11 sekme çok mu?** "Daha Fazla" altına Ürünler/Yetkililer/Görevler/Notlar taşınması kabul mü? (§3.2) | Dokunmatik/tablet kullanımında kaydırmalı sekme kötü | "Daha Fazla" grubu uygulansın |
| **F** | **Servis rolü açılacak mı?** [auth.py:103](../backend/app/auth.py:103) yorumu "gelecekteki `service` rolü"nden bahsediyor ama tanımlı değil. Servis grubu ve Teknisyenler sayfası bu rolü bekliyor mu? | Servis grubunun rol görünürlüğü buna bağlı | Açılmayacak varsayıldı; Servis grubu `read` ile herkese açık |
| **G** | **3 yetim sayfa dosyası** (`Customers.tsx`, `Orders.tsx`, `InventoryCounts.tsx`) silinsin mi? | Ölü kod; U1'de temizlenebilir ama **bu tasarımın kapsamı dışı**, ayrı PR | Bu fazda dokunulmuyor |
| **H** | **`/faturalar/:id`** hiçbir listeden linklenmiyor (§1.1). Fatura listesi eksik mi, yoksa bilinçli mi? | Eksikse Satış grubuna "Faturalar" maddesi gerekir | Bilinçli varsayıldı; menüye eklenmedi |

---

## 9. Bu dokümanın kanıt tabanı

Okunan dosyalar: `frontend/src/App.tsx`, `frontend/src/components/AppShell.tsx`, `frontend/src/components/CommandPalette.tsx`, `frontend/src/AuthContext.tsx`, `frontend/src/theme.ts`, `frontend/src/pages/{EntityDetail,MachineDetail,Entities,Machines,Pos}.tsx`, `backend/app/auth.py`, `backend/app/routers/{customers,machines,machine_hour_readings,machine_ownership,work_orders,work_order_parts,work_order_attachments,activity_logs,harvest_scheduling,payment_allocations,late_fees,technician_profiles,transactions}.py`.

**Hiçbir dosya değiştirilmedi.** Bu doküman ve mockup dışında repoya yazma yapılmadı.
