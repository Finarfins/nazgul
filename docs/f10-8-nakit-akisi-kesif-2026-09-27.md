# Faz 10-8 — 30/60/90 gün nakit akışı projeksiyonu — KEŞİF

**Ölçüm tarihi:** 2026-09-27 · **Taban:** `origin/develop` = `7f45b1e` (#173 birleşmesi); dal
`d5670e9`e (#176) taşındı. `7f45b1e..d5670e9` yalnız `docs/` değiştirdi (`git diff --stat 7f45b1e d5670e9 --
backend frontend` → boş), yani §6.0 pinleri `d5670e9`te de AYNIDIR · **Worktree:** `F:\nazgul-kesif-f108` ·
**Dal:** `docs/f10-8-nakit-akisi-kesif` · **Kapsam:** SALT OKUMA keşif. Uygulama kodu, göç, test, şema ve rota
DEĞİŞMEDİ.

Her sayı bu tabanda ölçüldü ve `dosya:satır` ile yazıldı. Ölçülemeyen her iddia **DOĞRULANMADI** etiketi taşır
(Ek). Bütün `app/`, `alembic/` ve `tests/` yolları `backend/` altındadır.

**Ölçüm düzeneği (depoya GİRMEDİ, scratchpad'de):** `seed_demo_data.py --database f108.db --force` ile SQLite
tohumu (Alembic başı `20260925_0092`; 25 müşteri, 10 tedarikçi, 60 satış, 25 alış, 100 ödeme, 3 finans hesabı,
130 finans hareketi). Tohum çek/senet, eski enstrüman, borç belgesi, müstahsil makbuzu ve vergi yükümlülüğü
YAZMAZ. Bu yüzden sonda betiği (`nakit_sonda.py`) tohumun KOPYASINA `[ENJEKTE]` işaretli dokuz satır ekledi
(§2.3) ve kovaları oradan çıkardı. PG'de koşulmadı (Ek).

> **Faz tanımı (ölçüldü):** `docs/yol-haritasi.html:100`: "30/60/90 gün nakit akışı projeksiyonu", dayanağı
> "yaşlandırma + çek + planlı ödeme". Faz başlığı `:90` ("Faz 10 — fark yaratan özellikler"). Komşu satırlar:
> `:94` "Hasat tahmini ve sezon alım/nakit planı" (tema örtüşmesi) ve `:101` 10-9 risk skoru (keşfi
> `docs/f10-9-musteri-risk-skoru-kesif-2026-09-27.md`, #174). `docs/ROADMAP.md` 10-8'i ANMIYOR
> (`grep -rln "nakit ak" docs` → yalnız `yol-haritasi.html`).

---

## 0. YÖNETİCİ ÖZETİ — dört cümlelik sonuç

1. **Girişin iki güvenilir tarihli kaynağı ZATEN VAR; ama bugün hiçbir uç ileriye bakan kova üretmiyor.** Satış
   ve borç belgeleri için belge başına açık bakiye + vade veren tek kanonik fonksiyon
   `receivables_engine.calculate_net_receivables`tir (`app/receivables_engine.py:469-478`; iade, eski artık ve
   bağsız tahsilat FIFO'sunu uygular, `:341-376`). İkinci kaynak alınan çek/senettir (`cek_senetler.vade`,
   `yon='alinan'`, `portfoyde|tahsile_verildi`; `app/cek_senet_schema.py:30-37`). Mevcut yaşlandırma raporunun
   kovaları GERİYE bakar (`not_due`, `days_1_30`… `routers/reports.py:34-40`) ve vadesi gelmemiş her şey TEK
   `not_due` kovasına düşer.
2. **Çıkış tarafı zayıf ve bir yerde YANILTICI.** Tarihli çıkış yalnız üç kaynakta var: verilen çek/senet
   (`yon='verilen'`), eski enstrüman (`financial_instruments.direction='issued'`) ve yalnız AY bilen vergi
   yükümlülüğü (`tax_liabilities.due_period`). Alış vadesi hiç TÜRETİLMİYOR, yalnız elle girilirse var
   (`routers/transactions.py:889,940`; `suppliers.payment_term_days` hiçbir hesapta kullanılmıyor). Asıl kusur
   şu: genel ödeme ekranından yapılan tedarikçi ödemesi `purchases.paid_amount`ı DÜŞÜRMEZ ve tahsis motoru
   yalnız müşteri ödemesi kabul eder (`app/payment_allocation_engine.py:511-512`). Tohumda belge bazlı açık
   alış **1.238.769,00**, cari düzey tedarikçi borcu **1.015.178,00** çıktı (§2.3). Farkın kaynağı 403.591,00
   TL'lik bağsız ödemedir; belge toplamı borcu olduğundan büyük gösterir. → Çıkış tarafı için saf bir FIFO
   gerekir (K4).
3. **Planlı/tekrarlayan ödeme, maaş, KDV ödemesi ve POS valörü YOK — bilerek v1 dışında.** Aşağıdaki
   aramaların hepsi 0 gerçek isabet verdi: `planned_payment|recurring|tekrarlayan|periyodik|abonelik|
   sabit.?gider|payroll|maas|bordro` ("bordro" yalnız çek toplu girişidir), `vat-summary|kdv_ozeti`
   (9-5a develop'ta YOK) ve `blocked_days|bloke|valor|settlement_date`. Tek istisna ileri tarihli manuel finans
   hareketidir: `POST /api/finance/transactions` her `txn_date`i kabul eder ve bakiyeye HEMEN sayar
   (`routers/finance.py:856-862`, bakiye SQL'i `:802-805`). Göç gerektirmeyen tek "planlı ödeme" adayı budur
   (K7).
4. **Öneri: göçsüz, iki PR.** 10-8a salt-okunur bir uç ekler: `GET …/cash-flow-projection?horizon=30|60|90`
   (kovalar × giriş/çıkış × kesinlik sınıfı + açılış bakiyesi + tarihsiz kalemler + uyarılar). Girdilerin
   tamamı bugünkü tablolarda duruyor, yeni saklanan durum yok. 10-8b ise Dashboard'a `finance` kapılı küçük bir
   kart ekler. **İzin ölçümle `reports` DEĞİL `finance` çıktı:** `reports` `rapor` rolüne de açık
   (`app/auth.py:240`). O rol bugün banka bakiyesini göremez: `/api/finance/*` → `finance` (`:1092-1093`),
   Dashboard'da kasa/banka `can('finance')` ile gizli (`frontend/src/pages/Dashboard.tsx:112,374`). K1 bu
   yüzden açıktır.

---

## 1. VERİ TABANININ BİLDİĞİ TARİHLİ GELECEK NAKİT OLAYLARI

### 1.1 Kaynak kataloğu (giriş G, çıkış Ç)

| # | Yön | Kaynak | Tarih sütunu (tip) | Tutar | "Açık" yüklemi | Dosya:satır |
|---:|---|---|---|---|---|---|
| G1 | giriş | Satış alacağı `orders` | `due_date` String(30); `due_date_normalized` Date | motorun `remaining`i (`final_total − tahsis/bağlı ödeme − iade − artık − FIFO`) | muhasebe durumu (iptal hariç, taslak yalnız içe aktarım notuyla; `document_engine.py:57-77`) ve `due_date` dolu | `core_schema.py:115-148`; motor `receivables_engine.py:270-392` |
| G2 | giriş | Borç belgesi `receivable_charge_documents` (`late_fee`, `service_fee`, `bounced_check`) | `period_end` (vade farkı) / `due_date_snapshot` (servis, karşılıksız): `charge_due_date_sql` | `gross_amount − Σ tahsis` | `status IN ('posted','reversed') AND posted_at IS NOT NULL AND period_end <= as_of` | göç `20260727_0028:77-141`; `receivables_engine.py:67-111, 395-462` |
| G3 | giriş | Alınan çek/senet `cek_senetler` | `vade` Date NOT NULL | `tutar` | `yon='alinan' AND portfoy_durumu IN ('portfoyde','tahsile_verildi')` | `cek_senet_schema.py:24-52`; aynı küme `routers/reports.py:303-309`, `routers/dashboard.py:166-175` |
| G4 | giriş | Eski enstrüman `financial_instruments` | `due_date` String(30) NOT NULL (biçim doğrulayıcısı yok) | `amount` | `direction='received' AND status IN ('portfolio','due')` | `finance_engine.py:43-61`; `routers/finance.py:808` |
| Ç1 | çıkış | Alış `purchases` | `due_date` String(30), NULL olabilir | `final_total − paid_amount` (**fazla**, §1.3) | muhasebe durumu | `core_schema.py:180-254`; `transactions.py:889,940` |
| Ç2 | çıkış | Verilen çek/senet `cek_senetler` | `vade` | `tutar` | `yon='verilen' AND portfoy_durumu IN ('portfoyde','tahsile_verildi')` (bugün HİÇBİR kod bunu toplamıyor) | CHECK göç `20260914_0085:109,220-224` |
| Ç3 | çıkış | Eski enstrüman | `due_date` | `amount` | `direction='issued' AND status IN ('portfolio','due')` | `routers/finance.py:809` (`issued_open`) |
| Ç4 | çıkış | Vergi yükümlülüğü `tax_liabilities` (stopaj, Bağ-Kur) | yalnız `due_period` 'YYYY-MM' (KESİM ayı, ödeme ayı DEĞİL) | `amount` | `settled_at IS NULL` (kapatan uç YOK, §1.4) | göç `20260906_0071:240-289`; `avans_engine.py:74-84` |
| Ç5 | çıkış, **tarihsiz** | Müstahsil makbuzu `producer_receipts` | yalnız `issued_at` | `net_payable − advance_applied_total − Σ bağlı ödeme` | `status='issued'` | göç `20260905_0070:143-211`; formül `routers/avans.py:341-347` |
| — | tarihsiz | Müşteri/tedarikçi açılış bakiyesi | yok | `opening_balance` | — | `core_schema.py:38,57` |

**Olmayanlar (grep ile ölçüldü, `backend/app` + `backend/alembic`):** planlı ödeme / tekrarlayan gider / maaş
tablosu (§0.3), taksit tablosu (tek isabet `receivable_charge_periods.installment_id`, göç 0028:27 — alacak
tarafı; `kiraci_geri_yukleme.py:408-409` "şemada taksit tablosu yok"), KDV yükümlülüğü, POS valörü. Eski
`income_expenses` tablosunun (`core_schema.py:425-436`) yazan kodu YOK (`INTO income_expenses|
insert(income_expenses` → 0), yalnız okunur.

### 1.2 Satış alacağı (G1) — vade nereden gelir

* `payment_term ∈ {PESIN, HARMAN_VADELI}` (`document_engine.py:22`; `schemas.py:189-195`). Başka vade türü YOK.
* **PESIN:** elle vade yoksa `_derived_due_date` = belge tarihi + `customers.payment_term_days`
  (`transactions.py:506-523`), **yalnız `paid_amount < final_total` ise**. Tam ödenmiş peşin satışın vadesi
  NULL'dır ve motora girmez (`:508-509`).
* **HARMAN_VADELI:** elle vade yoksa `harvest_scheduling.resolve_harvest_due_date` (`harvest_scheduling.py:232-332`)
  sonraki `harvest_calendars.due_date`i seçer. Hasat vadeleri 30/60/90'ın DIŞINA taşan tipik kalemdir (Ek).
* **POS veresiye** normal `orders` satırı yazar (`routers/pos.py:349-356`), `payment_term` vermez → PESIN,
  vade = bugün + `payment_term_days`. POS'a özgü bir kredi süresi YOK.
* **Kural:** projeksiyon satış tarafını motorun KENDİSİNDEN okur. Dashboard, analytics, müşteri listesi ve
  bildirim kuralları açık tutarı `final_total − paid_amount` ile hesaplar (`routers/dashboard.py:106-116`,
  `routers/analytics.py:53-69`, `routers/customers.py:100-157`, `notifications/rules.py:155-194`). Bu formül
  iadeyi, eski artığı ve bağsız tahsilat FIFO'sunu GÖRMEZ. Tohumda farkı ölçüldü: vadesi dolu satışlarda
  `final_total − paid_amount` = **250.636,50**, motorun açık toplamı **155.057,48** (§2.3; fark 95.579,02 =
  bağsız 285.662,00 TL tahsilatın FIFO ile düşülen kısmı). Kart bu iki rakamdan birini gösterirse yaşlandırma
  raporuyla ÇELİŞİR.
* **Bayrak etkisi:** `payment_allocation_engine_enabled` varsayılan `False` (`app/config.py:216`). Açıkken
  satış `paid_amount=0` yazılır (`transactions.py:700-704`), yani `final_total − paid_amount` formülü açık
  bakiyeyi TAM belge tutarı gösterir. Motor iki kipte de doğrudur (`receivables_engine.py:341-356`).

### 1.3 Alış (Ç1) — vade de açık tutar da eksik

* `purchases`ta `due_date_normalized` ve `payment_term` YOK (`transactions.py:718-719` yorumu). Vade kullanıcı
  girerse yazılır (`:889` UPDATE, `:940` INSERT), aksi hâlde NULL kalır. `_derived_due_date` yalnız satışta
  koşar (`:482-525`). Ön yüzde alış "Vade" alanı isteğe bağlıdır (`frontend/src/components/TransactionDialog.tsx:128`).
* `suppliers.payment_term_days` (`core_schema.py:59`) yalnız CRUD'da geçer (`routers/finance.py:163,676-677,711`;
  `EntityDialog.tsx:17` "Varsayılan Vade (Gün)"). **Hiçbir vade ya da borç hesabı onu okumaz.**
* **Açık tutar fazla görünür:** `paid_amount` yalnız alış formundaki ödemeyi taşır (`transactions.py:700-704`).
  Genel ödeme ekranından yapılan tedarikçi ödemesi `purchases`ı güncellemez (`UPDATE purchases` → 0 isabet) ve
  tahsis motoru tedarikçiyi 422 ile reddeder (`payment_allocation_engine.py:511-512`). Doğru tedarikçi borcu
  yalnız CARİ düzeyde var ve tarih taşımaz: açılış + aktif alış + kesilmiş makbuz − tedarikçi ödemeleri
  (`routers/dashboard.py:150-187`).
* **Sonuç:** belge başına `final_total − paid_amount`, bağsız ödeme yapılmış her tedarikçide çıkışı ŞİŞİRİR.
  Tohumda 10 bağsız tedarikçi ödemesi (403.591,00) bu farkın kaynağıdır (§2.3). → K4: bağsız tedarikçi
  ödemelerini ve açılış bakiyesini en eski vadeli alıştan başlayarak düşen saf bir FIFO. Bu,
  `calculate_receivables`in bayrak kapalı FIFO'sunun (`receivables_engine.py:363-376`) aynası olur.

### 1.4 Vergi yükümlülüğü (Ç4) — tarih kuralı kodda YOK

* `due_period` makbuzun KESİLDİĞİ aydır. Göç yorumunda "ödemenin yapıldığı ay DEĞİL" diye yazıyor (0071:247-248).
* Ödeme günü sabiti YOK: `day=26|"26"|odeme_gunu|payment_day|muhtasar|kdv1|beyan_gunu` → 0 isabet. Muhtasar
  ve SGK'nın izleyen ayın 26'sında ödendiği kuralı **DOĞRULANMADI** (mevzuat okunmadı). Sonda bu kuralı
  varsayım olarak uyguladı (`date(due_period||'-01','+1 month','+25 days')`).
* **`settled_at` hiç dolmaz:** yükümlülüğü kapatan uç YOK (0071:87-97, `routers/avans.py:54-61`) ve
  `/api/tax-liabilities` salt okunurdur (`routers/avans.py:469-510`). Yani bir satır ödendikten SONRA da açık
  görünür. Projeksiyon yalnız "ödeme tarihi bugünden sonra olan" satırları saymalıdır. Geçmiş ayın açık satırı
  "gecikmiş" DEĞİL "kapatılamıyor" uyarısıdır (K6).
* İptal edilen makbuzun kapanmamış yükümlülüğü SERT silinir (`avans_engine.py:360-366`). F9-5 §1.4 aynı şeyi
  ölçmüştü; projeksiyon için bu doğru davranıştır.

### 1.5 Çek/senet (G3, Ç2) — çift sayım tuzağı

* `yon ∈ {alinan, verilen}` (göç 0085:109). Verilen evrakın tedarikçisi zorunludur (`ck_cek_senetler_yon_taraf`,
  :222-224). Durumlar `cek_senet_engine.py:37-41`; nakit hâlâ beklenen küme `portfoyde`, `tahsile_verildi`.
* `ciro_edildi` (yalnız alınan evrak, `cek_senet_engine.py:120-123`) şirkete giriş DEĞİLDİR. Ciro kasa
  hareketi yazmaz (`cek_senet_cari.py:271-305`), yani nakit açısından nötrdür.
* **Çift sayım:** `payment_id` dolu evrak (tahsilat ile girilmiş) satış alacağını ZATEN düşürmüştür. O parayı
  artık yalnız evrak temsil eder ve G3'te sayılmalıdır. `payment_id` NULL evrak ise (düz giriş ya da `/bordro`)
  alacağı düşürmemiştir (`routers/cek_senetler.py:30-43, 427-463`). Hem satış (G1) hem evrak (G3) sayılırsa
  aynı para İKİ kez girer. Tohumda gerçek evrak YOK. Sonda enjekte ettiği iki açık evrakı `payment_id` NULL
  olduğu hâlde saydı, çünkü amaç yalnız SQL'i sınamaktı. K5 bu satırları kovadan çıkarıp uyarıya taşır.
* **Eski ve yeni evrak tablosu yan yana yaşıyor:** `/api/finance/instruments` hâlâ yazıyor
  (`routers/finance.py:885-931`). `GET /api/finance/summary` ise yalnız ESKİ tabloyu okur (`:808-809`). İki
  tabloda aynı evrakın bulunup bulunmadığı **DOĞRULANMADI**.

### 1.6 Bakiyeler — açılış noktası

* `finance_accounts.account_type ∈ {cash, bank, pos}` (`finance_engine.py:19,63`). Bakiye yardımcı bir
  fonksiyonda değil, aynı SQL üç yere kopyalanmış: `opening_balance + Σ(in) − Σ(out)`
  (`routers/finance.py:802-805`, `:812-821`, `routers/dashboard.py:207-227`).
* **Bakiye TARİH SÜZMEZ:** ileri tarihli manuel hareket bugünün bakiyesine girer (§0.3). Projeksiyon açılışı
  `txn_date <= bugün` ile süzmeli, ileri tarihli hareketleri ayrı "planlı" satırı yapmalıdır (K7).
  `txn_date` String(30) (`finance_engine.py:27-42`).
* **POS valörü yok:** `pos` bakiyesi düz defter bakiyesidir; banka geçiş tarihi ya da bloke günü modellenmemiş.
  v1 POS bakiyesini "bugün kullanılabilir" sayar ve ayrı satırda gösterir (K8).
* Tohumda banka bakiyesi **−826.712,81** çıktı (tohum alış ödemelerini bankadan yazıyor). Bu tohumun bir
  yan etkisidir, ürün kusuru değil. Negatif açılış kartta olduğu gibi gösterilmelidir, sıfıra kırpılmamalıdır.

---

## 2. KOVALAMA SQL'İ VE TOHUMDA KOŞUSU

### 2.1 Kova tanımı

`gun = vade − bugün` (İstanbul iş günü; `app/business_time.py:32` `business_today()`):
`gecikmis` (< 0) · `0-30` · `31-60` · `61-90` · `90+` · `tarihsiz` (vade NULL). Bugünün vadesi `0-30`a girer.
`horizon` dışında kalan (`> horizon`) tek bir `ufuk_sonrasi` satırında toplanır.

### 2.2 SQL (sondada koşan biçim; G1/G2 hariç — onlar motordan)

```sql
WITH olay AS (
  SELECT 'G:cek_senet' kaynak, vade, tutar FROM cek_senetler
   WHERE company_id=:c AND yon='alinan'  AND portfoy_durumu IN ('portfoyde','tahsile_verildi')
  UNION ALL
  SELECT 'C:cek_senet', vade, tutar FROM cek_senetler
   WHERE company_id=:c AND yon='verilen' AND portfoy_durumu IN ('portfoyde','tahsile_verildi')
  UNION ALL
  SELECT 'G:eski_enstruman', substr(due_date,1,10), amount FROM financial_instruments
   WHERE company_id=:c AND direction='received' AND status IN ('portfolio','due')
  UNION ALL
  SELECT 'C:eski_enstruman', substr(due_date,1,10), amount FROM financial_instruments
   WHERE company_id=:c AND direction='issued'   AND status IN ('portfolio','due')
  UNION ALL
  SELECT 'C:alis', NULLIF(substr(due_date,1,10),''), final_total-COALESCE(paid_amount,0) FROM purchases
   WHERE company_id=:c AND COALESCE(status,'completed') NOT IN ('cancelled','draft')
     AND final_total-COALESCE(paid_amount,0) > 0
  UNION ALL
  SELECT 'C:vergi_yukumlulugu', date(due_period||'-01','+1 month','+25 days'), amount
    FROM tax_liabilities WHERE company_id=:c AND settled_at IS NULL
)
SELECT kaynak,
  CASE WHEN vade IS NULL THEN 'tarihsiz'
       WHEN julianday(vade)-julianday(:bugun) <  0 THEN 'gecikmis'
       WHEN julianday(vade)-julianday(:bugun) <= 30 THEN '0-30'
       WHEN julianday(vade)-julianday(:bugun) <= 60 THEN '31-60'
       WHEN julianday(vade)-julianday(:bugun) <= 90 THEN '61-90'
       ELSE '90+' END kova,
  COUNT(*) adet, SUM(tutar) toplam
FROM olay GROUP BY 1,2 ORDER BY 1,2;
```

**Lehçe notu (ölçüldü: yalnız SQLite koştu).** `julianday` ve `date(...,'+1 month')` SQLite'a özgüdür. PG'de
bunlar `(CAST(vade AS date) - CAST(:bugun AS date))` ve `(to_date(due_period,'YYYY-MM') + interval '1 month' +
interval '25 days')::date` olur. Metin tarihli sütunlar (`purchases.due_date`, `financial_instruments.due_date`)
PG'de açık `CAST` ister. Eski `dd.mm.yyyy` değerini motor `parse_receivable_date` ile okur
(`receivables_engine.py:53-64`), bu SQL okuyamaz. **Uygulamadaki öneri:** kovalama SQL'de DEĞİL Python'da
yapılır. Sorgular yalnız `(kaynak, vade, tutar)` satırı döndürür, kovayı saf bir fonksiyon atar. Böylece lehçe
farkı iki tarih ayrıştırıcısına iner ve G1/G2 (motor, Python) ile aynı yoldan geçer. PG ikizi yine ZORUNLUDUR
(§6.1).

### 2.3 Sonuç — tohumlu SQLite, `cid=1`, bugün = **2026-09-27**

`[ENJEKTE]` satırlar: alınan çek 15.000 (+20 gün, `portfoyde`), alınan çek 8.000 (+45, `tahsile_verildi`),
alınan çek 5.000 (+10, `tahsil_edildi` → **dışlanmalı**), verilen çek 12.000 (+75), eski alınan enstrüman
3.000 (+10), eski verilen enstrüman 7.000 (+100), makbuz `MM-SONDA-1` (net 9.700, `issued`), vergi
yükümlülüğü stopaj 200 + Bağ-Kur 100 (`due_period` = 2026-09).

| Kaynak | gecikmiş | 0–30 | 31–60 | 61–90 | 90+ | tarihsiz |
|---|---:|---:|---:|---:|---:|---:|
| G: satış (motor) | 137.181,32 (9) | 17.876,16 (4) | — | — | — | — |
| G: çek/senet | — | 15.000,00 (1) | 8.000,00 (1) | — | — | — |
| G: eski enstrüman | — | 3.000,00 (1) | — | — | — | — |
| **G toplam** | **137.181,32** | **35.876,16** | **8.000,00** | 0,00 | 0,00 | 0,00 |
| Ç: alış | 1.238.769,00 (20) | — | — | — | — | — |
| Ç: çek/senet | — | — | — | 12.000,00 (1) | — | — |
| Ç: eski enstrüman | — | — | — | — | 7.000,00 (1) | — |
| Ç: vergi yükümlülüğü | — | 300,00 (2) | — | — | — | — |
| Ç: müstahsil makbuzu | — | — | — | — | — | 9.700,00 (1) |
| **Ç toplam** | **1.238.769,00** | **300,00** | 0,00 | **12.000,00** | **7.000,00** | **9.700,00** |

Açılış (finance/summary SQL'i): kasa **393.536,58**, banka **−826.712,81**, POS **266.037,66**.

**Okuma:**
* `tahsil_edildi` evrak (5.000) kovalarda YOK → durum süzgeci ölçüldü. `cid=2` → motor 0 belge (kiracı yalıtımı).
* Tohumun alışları 2026-03-07…2026-07-26 tarihli, vadeleri 2026-04-06…2026-08-25 (`+30`, `seed_demo_data.py:257`).
  Bu yüzden 20 alışın 20'si GECİKMİŞ görünür ve ileri kovalarda alış YOK. Bu tohumun takvim kaymasıdır.
* Satış tarafında 25 açık belgenin vadeleri 2026-05-08…2026-10-17. Motor FIFO sonrası 13'ünü açık bırakıyor
  (9 gecikmiş + 4 ileri).
* **Ç1 şişmesi ölçüldü:** belge bazlı açık alış 1.238.769,00. Cari düzey 1.015.178,00 = 1.238.769,00 + açılış
  180.000,00 − bağsız tedarikçi ödemesi 403.591,00. Bağlı tedarikçi ödemeleri (`reference_type='purchase'`,
  20 satır, 854.910,60) zaten `paid_amount`ta.
* **Gecikmişin payı:** her iki yönde de en büyük kova "gecikmiş". Projeksiyon bu kovayı 0–30'a KATMAZSA kart
  "önümüzdeki 30 günde +35.576" der ve 137 bin tahsil edilmemiş alacağı ve 1,2 milyon ödenmemiş borcu saklar.
  Katarsa da gecikmiş alacağın tamamının 30 günde geleceğini VARSAYAR. → K3.

---

## 3. PROJEKSİYONU KULLANIŞLI YAPMAK İÇİN EKSİKLER

| Eksik | Bugün | Etkisi | v1 önerisi |
|---|---|---|---|
| **Beklenen tahsilat oranı** | Hazır özet YOK. F10-9 §1.3: kaynak `build_principal_timelines` (`receivables_engine.py:481-784`), "kapanış − vade" türetimi prototipte koştu. Bayrak kapalıyken kapanış tarihi FIFO'nun model çıktısıdır. | Vadeli alacak kovası "gelecek para" değil "gelmesi gereken para"dır. | v1 oran UYGULAMAZ, kesinlik SINIFI gösterir (§4.2). 10-9a indikten sonra `gecikme_orani` ikinci dilimde çarpan olabilir (K2). |
| Müşteri başına vade varsayılanı | `customers.payment_term_days` VAR ve PESIN kısmi/veresiye satışta KULLANILIYOR (`transactions.py:506-523`). | Yok, zaten var. | — |
| Tedarikçi vade varsayılanı | `suppliers.payment_term_days` VAR ama KULLANILMIYOR (§1.3). | Vadesiz alış "tarihsiz"e düşer. | v1: `due_date` NULL alışı `purchase_date + suppliers.payment_term_days` ile TAHMİNİ vadeye koyar ve sınıfını `tahmini` işaretler. Kaydedilmiş veriye dokunmaz (K4). |
| KDV ödeme tarihi/tutarı | 9-5a (KDV özeti) develop'ta YOK (`vat-summary|kdv_ozeti` → 0). | KOBİ'nin en büyük aylık düzenli çıkışlarından biri projeksiyonda görünmez. | v1'de YOK, uyarı olarak adıyla yazılır. 9-5a inince hesaplanan − indirilecek fark, ödeme günü kuralıyla ayrı dilim olur (K9; ödeme günü **DOĞRULANMADI**). |
| Bakiye | VAR (§1.6), tarih süzmüyor, POS valörü yok. | Açılış yanlış olabilir. | `txn_date <= bugün` süzgeci + ayrı POS satırı (K7, K8). |
| Maaş, kira, abonelik | YOK (0 isabet). | Sabit giderler görünmez. | v1'de YOK. İstek gelirse "planlı ödeme" tablosu ayrı göçlü faz olur (§5.3). |
| Hasat vadeli toplu tahsilat | `harvest_calendars.due_date` VAR. | Tarım firmasında 90+ kovası baskın olabilir. | `horizon` 90'ı aşan tek bir `ufuk_sonrasi` satırıyla görünür. |

---

## 4. ÖNERİLEN UÇ

### 4.1 Yol ve izin — ölçüm

| Seçenek | Yol | İzin kaynağı | Kim görür | Bedel |
|---|---|---|---|---|
| **(A) ÖNERİLEN** | `GET /api/finance/cash-flow-projection` | mevcut `/api/finance` → `finance` kuralı (`auth.py:1092-1093`) | admin (`*`), yonetici, muhasebe (`auth.py:216,230`) | `auth.py`ye DOKUNULMAZ; banka bakiyesiyle aynı izin |
| (B) | `GET /api/reports/cash-flow-projection` + `auth.py:1129`dan ÖNCE `finance` istisnası (`:1127` `seasonal-plan` istisnası deseniyle) | yeni önek kuralı | aynı | `auth.py` +2 satır; rapor menüsüyle yol tutarlı |
| (C) brifingteki | `GET /api/reports/cash-flow-projection`, izin `reports` | `auth.py:1129-1130` | admin, yonetici, muhasebe, **rapor** (`:240`) | `rapor` rolü bugün göremediği banka bakiyesini ve tedarikçi borcunu görmeye başlar = **izin GENİŞLEMESİ** |

Üç seçenekte de uç `read`e ÇÖZÜLMEZ. Dolayısıyla `_populations` (`tests/test_authorization_population_
reconciliation.py:714-745`) onu yalnız `authenticated`a ekler: `read`/`guarded`/`undeniable` SABİT kalır (`:739`
yalnız `permission == "read"`i sayar).

### 4.2 Sorgu parametreleri ve cevap şekli

Örnekteki rakamlar §2.3 sondasının ham çıktısıdır. K4/K5 uygulanmamıştır: K5'te `payment_id` NULL iki
enjekte evrak `kesin` girişten çıkıp uyarıya düşerdi.

`?horizon=30|60|90` (varsayılan 90; başka değer 422) · `?include_overdue=true|false` (varsayılan `false`, K3).
`as_of` parametresi YOK: projeksiyon her zaman `business_today()`dan başlar. Geçmiş tarihten projeksiyon, geçmiş
bakiyeyi ve o günkü evrak durumunu gerektirir; durum geçmişi saklanmıyor (F9-5 §1.5).

```json
{
  "as_of": "2026-09-27",
  "horizon": 90,
  "currency": "TRY",
  "opening_balance": {"cash": "393536.58", "bank": "-826712.81", "pos": "266037.66",
                      "total": "-167138.57", "future_dated_excluded": "0.00"},
  "buckets": [
    {"key": "overdue", "start": null, "end": "2026-09-26",
     "inflow":  {"kesin": "0.00", "vadeli": "137181.32", "tahmini": "0.00", "total": "137181.32"},
     "outflow": {"kesin": "0.00", "vadeli": "1238769.00", "tahmini": "0.00", "total": "1238769.00"},
     "net": "-1101587.68", "counted_in_balance": false},
    {"key": "d0_30", "start": "2026-09-27", "end": "2026-10-27",
     "inflow":  {"kesin": "18000.00", "vadeli": "17876.16", "tahmini": "0.00", "total": "35876.16"},
     "outflow": {"kesin": "300.00", "vadeli": "0.00", "tahmini": "0.00", "total": "300.00"},
     "net": "35576.16", "cumulative_balance": "-131562.41", "counted_in_balance": true},
    {"key": "d31_60", "…": "…"}, {"key": "d61_90", "…": "…"}
  ],
  "after_horizon": {"inflow": "0.00", "outflow": "7000.00"},
  "undated": {"inflow": [],
              "outflow": [{"source": "producer_receipts", "amount": "9700.00", "count": 1}]},
  "sources": [{"key": "sales", "direction": "in", "certainty": "vadeli",
               "by_bucket": {"overdue": "137181.32", "d0_30": "17876.16"}, "count": 13}],
  "warnings": [{"code": "VAT_NOT_INCLUDED", "message": "KDV ödemesi projeksiyona dahil değil"},
               {"code": "UNLINKED_CHECKS_EXCLUDED", "amount": "…", "count": 0},
               {"code": "TAX_LIABILITY_UNSETTLEABLE", "amount": "…", "count": 0},
               {"code": "PURCHASE_DUE_ESTIMATED", "amount": "…", "count": 0}]
}
```

**Kesinlik sınıfı (üç değer, kapalı küme):**
* `kesin`: evrak ve vergi. Tutar ve tarih belgeye bağlıdır. Karşılıksız kalma riski ayrı bir konudur.
* `vadeli`: satış/alış ve borç belgesi. Tarih vadedir, gerçekleşme karşı tarafa bağlıdır.
* `tahmini`: türetilmiş vade. Alışta `payment_term_days` (K4), vergide 26. gün kuralı (K6).

Para alanları dizgi (`Decimal`) döner. Bu, projede ölçülen sözleşmedir (`test_v2_9_decimal_contract.py`,
**DOĞRULANMADI**: dosya bu keşifte okunmadı).

**Cevapta cari ADI YOK:** yalnız toplamlar döner. Bu sayede SEC-3b alan maskelemesi ve
`cari_alan_envanteri.txt` kıpırdamaz (tarayıcının bu uca bakışı **DOĞRULANMADI**, dilimde ölçülür). Belge
listesi drill-down'u zaten var olan uçlara yönlendirilir: `/raporlar/alacak-yaslandirma`,
`GET /api/cek-senetler?vade_from=&vade_to=` (`routers/cek_senetler.py:331-409`).

### 4.3 Modül yerleşimi

* `app/nakit_akisi.py`: SAF kısım. `kova(vade, bugun, horizon)`, `(kaynak, vade, tutar, sınıf)` olaylarından
  cevap kuran fonksiyon ve tedarikçi FIFO'su (K4). SQL içermez. `app/mustahsil.py`nin "aritmetik saf modülde"
  duruşunu izler.
* Okuma: G1+G2 `calculate_net_receivables(db, cid, bugun)`. Geri kalan kaynaklar için yeni Core `select`leri
  yazılır; her biri `company_id == cid` taşır (Core envanteri ve kiracı ifadesi pinleri bu yüzden oynar, §6.1).
* Uç: `routers/finance.py`e (A) ya da `routers/reports.py`ye (B) tek `@router.get`.
* **Maliyet (F10-9 §3.3 ölçümü):** firma geneli `calculate_net_receivables` 2.025 müşteri / 20.060 satışta
  **1.896 ms**. Bu yüzden projeksiyon `/api/dashboard`ın İÇİNE KONMAZ. Kart ayrı ve tembel istek atar; Dashboard
  ilk boyamayı beklemez. Önbellek v1'de YOK (saklanan durum = göç).

### 4.4 Ön yüz kartı (10-8b)

* Yer: `Dashboard.tsx:345-423` üç sütunlu satırı. `canViewFinance ? <Grid size={{xs:12,md:4}}>` deseni
  (`:374`) aynen kullanılır: "Kasa / Banka" kartının altına ya da yerine yeni bir satır. `canViewFinance =
  can('finance')` (`:112`) (A)/(B) izniyle birebir örtüşür.
* İçerik: 30/60/90 için üç sütunlu küçük tablo (giriş / çıkış / net / kümülatif), gecikmiş satırı ayrı ve
  gri, uyarılar ikon + tooltip. "Detay" bağlantısı mevcut `/nakit-yonetimi`ne gider (`navigation.tsx:190`).
  **Yeni rota ve menü maddesi YOK** → rota/menü sayım pinleri SABİT (§6.2).
* `Dashboard.test.tsx:49` her `api.get` çağrısına AYNI `dashboard` gövdesini döndürüyor
  (`get.mockResolvedValue({data:dashboard})`). Kartın ikinci isteği bu sahte gövdeyi alır. Test URL'ye göre
  dallanan bir sahteye çevrilmeli, yoksa kart ya çöker ya da sessizce boş çizer.

### 4.5 Göç GEREKMEZ — gerekçe

Cevaptaki her alan bugünkü sütunlardan türüyor (§1.1 tablosu). Saklanan tek yeni şey ne olurdu? (a) Önbellek:
§4.3'e göre gerekmez. (b) "Planlı ödeme" tablosu: v1 dışı (§5.3). (c) Tedarikçi tahsisi (`payment_allocations`
tedarikçiye açılması): K4 FIFO'su göçsüz bir TAHMİN verir; gerçek tahsis ayrı fazdır. Tek tartışmalı nokta, tarihi
olmayan vergi yükümlülüğü ödemesidir: `settled_at` sütunu zaten var, onu yazan bir uç yok. O uç 10-8'in değil
D2'nin eksiğidir (K6).

---

## 5. PR BÖLÜNMESİ

### 5.1 F10-8a — `nakit_akisi.py` + salt-okunur uç (GÖÇ YOK)

Saf modül + dört Core okuma (evrak ×2 yön, eski enstrüman, alış, vergi, makbuz; toplam sayı dilimde) + uç.
Testler: `tests/test_f10_8a_nakit_akisi.py` ve `test_f10_8a_nakit_akisi_postgresql.py` (kök düzey PG ikizi).

Test kapsamı:
* kova sınırları (0, 30, 31, 90, 91 gün; `horizon` dışı);
* `tahsil_edildi`/`ciro_edildi`/`karsiliksiz` evrak dışlanır;
* `payment_id` NULL evrak kovaya girmez ve uyarıya düşer (K5);
* satış tarafı motorla kuruşu kuruşuna aynı (yaşlandırma raporu toplamı = gecikmiş + ileri kovalar);
* tedarikçi FIFO'su bağsız ödemeyi en eski vadeden düşer (K4);
* ileri tarihli finans hareketi açılışa girmez (K7);
* komşu firma yalıtımı;
* `rapor` rolü 403 (A/B'de).

**Mutasyon hedefleri:** evrak durum süzgecinden `tahsile_verildi` silinince kırmızı; FIFO kaldırılınca kırmızı.

### 5.2 F10-8b — Dashboard kartı (GÖÇ YOK, backend yok)

`Dashboard.tsx` + `Dashboard.test.tsx`; `types.gen.ts` 10-8a'dan gelir.

### 5.3 (Kapsam DIŞI, önerilen sonraki dilimler)

* **10-8c:** KDV ödeme satırı. 9-5a (KDV özeti) İNDİKTEN sonra (K9).
* **10-8d:** 10-9a skorundan tahsilat olasılığı çarpanı (K2).
* **10-8e:** planlı/tekrarlayan ödeme tablosu (göç, `TENANT_TABLES` +1). Ayrı keşif ister.

---

## 6. PİN DELTALARI

### 6.0 TABAN — `7f45b1e` üzerinde ÖLÇÜLDÜ (`d5670e9`te backend/frontend farkı YOK)

| Pin | Değer | Yer |
|---|---:|---|
| Rota işlemi / yolu | **425 / 331** | `tests/test_route_security_contracts.py:744-745` + `EXPECTED_SECURITY_FINGERPRINT` (`:746`) |
| GET envanteri | **202** | `tests/test_route_get_permission_inventory.py:614` + `EXPECTED_GET_PERMISSIONS` (reports girdileri `:454-458`) + parmak izi (`:615`) |
| Kimlik doğrulamalı / read / undeniable | **412 / 91 / 97** | `tests/test_authorization_population_reconciliation.py:421-423` |
| guarded / farm_herd / naked_read | **35 / 41 / 56** | aynı dosya `:771`, `:796`, `:820` |
| `TENANT_TABLES` | **127** | `tests/test_tenant_scoping_guard.py:41-263` (AST sayımı) |
| Core sorgu envanteri | **252** (select 171 / update 69 / delete 12) | `tests/test_core_query_inventory.py:1348,1352` |
| Core kiracı ifadesi | **209** | `tests/test_core_tenant_scoping_guard.py:2149` |
| `pg_twins.txt` | **146** satır | `tests/pins/pg_twins.txt` (`wc -l`) |
| `alt_surec_sql.txt` | **137** satır | `tests/pins/alt_surec_sql.txt` |
| Alembic başı | **`20260925_0092`** | `alembic/versions/20260925_0092_arama_katli_sutunlar.py:56`; `grep -rln '"20260925_0092"' backend --include=*.py` → 19 dosya (göçün kendisi dahil) |
| `ACTION_TYPES` / `RESOURCE_TYPES` | **84 / 24** | `app/activity_log.py:61-261, 263` (sayı çivisi YOK, yalnız üyelik testi) |
| Ön yüz: `ROTA_ENVANTERI` / `ALL_NAV_ITEMS` / `BOOKMARKED_MENU_URLS` / `AppShell` `hrefs` | **80 / 59 / 59 / 59** | `rota-kapsam-sozlesmesi.test.ts:341`, `navigation.consistency.test.ts:212,219`, `components/AppShell.test.tsx:365` |

> ⚠ **10-9a ile yarış:** F10-9 keşfi (#174) de bir GET ekliyor (425→426, 202→203, 412→413). Hangisi ÖNCE
> inerse diğerinin sayıları +1 kayar. Her dilim dalını kestiği andaki `origin/develop` üzerinde YENİDEN ölçer
> (depo hafızası: envanter pinleri paralel PR'larda git çatışması olmadan çürür).

### 6.1 F10-8a

| Pin | Delta |
|---|---|
| Rota işlemi / yolu | 425 → **426** / 331 → **332** |
| `EXPECTED_SECURITY_FINGERPRINT` | yeniden |
| GET envanteri | 202 → **203**; `EXPECTED_GET_PERMISSIONS` +1 (`("GET", "/api/finance/cash-flow-projection"): "finance"`); parmak izi yeniden |
| Kimlik doğrulamalı | 412 → **413** |
| read / guarded / undeniable / farm_herd | **91 / 35 / 97 / 41 SABİT** (izin `finance`; seçenek C'de `reports` — o da `read` değil, yine SABİT) |
| `TENANT_TABLES` / Alembic / `alt_surec_sql.txt` | SABİT (127 / `0092` / 137) |
| `ACTION_TYPES` / `RESOURCE_TYPES` | SABİT (salt okuma, aktivite yazılmaz) |
| Core envanteri | 252 → ~258 (+~6 select; **DOĞRULANMADI**, sorgular yazılmadı); parmak izi yeniden |
| Core kiracı ifadesi | 209 → ~215 (+~6; **DOĞRULANMADI**) |
| `pg_twins.txt` | 146 → **147** |
| `cari_alan_envanteri.txt` | SABİT (cevapta cari alanı yok; tarayıcı davranışı **DOĞRULANMADI**) |
| `auth.py` | (A) DEĞİŞMEZ · (B) +1 önek kuralı · (C) DEĞİŞMEZ |
| `openapi.json` / `types.gen.ts` | değişir (bir uç + cevap şeması); `npm ci` sonrası `types:gen` ile yeniden üretilir |

**PG ikizi ZORUNLU:** metin tarihli sütunların (`purchases.due_date`, `financial_instruments.due_date`) PG'de
tarihe çevrilmesi ve motorun PG yolu yalnız PG'de görünür.

### 6.2 F10-8b

| Pin | Delta |
|---|---|
| Backend pinleri | SABİT |
| `ROTA_ENVANTERI`, `ALL_NAV_ITEMS`, `BOOKMARKED_MENU_URLS`, `AppShell` `hrefs`, `topLevelLabels` | SABİT (yeni rota/menü yok) |
| `Dashboard.test.tsx` | sahte `get` URL'ye göre dallanır; kart için +2/+3 test (finance yok → kart yok; uyarı çizimi; negatif açılış) |

---

## 7. ŞEFE AÇIK KARARLAR (K1–K9)

**K1 — İzin ve yol.** Ölçüm: `reports` `rapor` rolüne açık (`auth.py:240`), banka bakiyesi bugün yalnız
`finance` ile görünüyor (§4.1). → **Tavsiye: (A) `GET /api/finance/cash-flow-projection`, izin `finance`.**
Brifingteki `reports` (C) bir izin genişlemesidir. Rapor menüsüyle yol tutarlılığı isteniyorsa (B).

**K2 — Beklenen tahsilat oranı v1'de mi?** Ölçüm: hazır özet yok; bayrak kapalıyken kapanış tarihi FIFO
modelidir (F10-9 §1.3). → **Tavsiye: v1'de oran YOK, kesinlik sınıfı VAR.** Çarpan 10-9a skoruyla 10-8d'de.
Gerekçe: kalibre edilmemiş bir çarpan, rakamı "doğru görünen yanlış"a çevirir.

**K3 — Gecikmiş kalemler kovaya girer mi?** Ölçüm: tohumda iki yönde de en büyük kova gecikmiş (§2.3).
→ **Tavsiye: ayrı `overdue` satırı, kümülatif bakiyeye VARSAYILAN olarak KATILMAZ, `include_overdue=true`
ile katılır.** Kartta gri ve ayrı görünür.

**K4 — Alış tarafı nasıl düzeltilir?** Ölçüm: bağsız tedarikçi ödemesi belge bakiyesini düşürmüyor
(1.238.769 vs 1.015.178), vadesiz alış olabilir, tedarikçi vadesi kullanılmıyor (§1.3).
→ **Tavsiye:**
* bağsız tedarikçi ödemeleri + açılış bakiyesi en eski vadeli alıştan başlayarak saf FIFO ile düşülür;
* vadesiz alış `purchase_date + suppliers.payment_term_days` ile `tahmini` sınıfa girer;
* kayıtlı veriye YAZILMAZ.

Gerçek tedarikçi tahsisi (motorun tedarikçiye açılması) ayrı fazdır.

**K5 — `payment_id` NULL alınan evrak.** Ölçüm: satış alacağını düşürmemiş evrak iki kez sayılır (§1.5).
→ **Tavsiye: kovaya GİRMEZ; `UNLINKED_CHECKS_EXCLUDED` uyarısı tutar ve adetle gösterir.** Satış vadesi yerine
evrak vadesini kullanmak daha doğru olurdu ama evrak–satış eşleşmesi saklanmıyor.

**K6 — Vergi yükümlülüğü ödeme günü ve kapanmayan satırlar.** Ölçüm: gün kuralı kodda yok, `settled_at`
hiç dolmaz (§1.4). → **Tavsiye:**
* `due_period + 1 ay, 26. gün` kuralı `tahmini` sınıfta uygulanır (mevzuat teyidi müşavirden, **DOĞRULANMADI**);
* ödeme tarihi geçmiş satırlar kovaya girmez, `TAX_LIABILITY_UNSETTLEABLE` uyarısına düşer.

Kapatma ucu D2'nin eksiği olarak ayrı kayda alınır.

**K7 — İleri tarihli finans hareketleri.** Ölçüm: bugünkü bakiye onları hemen sayıyor (§1.6).
→ **Tavsiye:**
* açılış `txn_date <= bugün` ile süzülür;
* `txn_date > bugün` hareketler `kesin` sınıfında "planlı" kaynak olarak kovalara girer.

Bu, göçsüz tek planlı ödeme kaynağıdır. Mevcut `finance/summary` ve Dashboard bakiyesi DEĞİŞMEZ, yani iki
ekran arasında ileri tarihli hareket kadar fark oluşur. Fark uyarıda adıyla yazılır.

**K8 — POS bakiyesi.** Ölçüm: valör/bloke modellenmemiş. → **Tavsiye: açılışta ayrı satır, "bugün
kullanılabilir" sayılır; POS valörü v2.**

**K9 — KDV.** Ölçüm: 9-5a develop'ta yok. → **Tavsiye: v1 `VAT_NOT_INCLUDED` uyarısı; 10-8c 9-5a'ya
bağımlı.** Tahmini bir KDV satırı (`order_items`/`purchase_items`ten ay içi fark) 9-5a'nın işini
çoğaltacağı için yazılmaz.

---

## 8. EN BÜYÜK DÖRT RİSK

1. **Kart ile yaşlandırma raporu farklı rakam gösterir.** Satış tarafını `final_total − paid_amount` ile okuyan
   her yol motordan sapar (tohumda 250.636,50 vs 155.057,48; bayrak açıkken daha da büyük). Kural: satış tarafı
   YALNIZ `calculate_net_receivables`ten okunur; test yaşlandırma toplamıyla eşitliği çiviler.
2. **Çıkış olduğundan büyük görünür, kullanıcı alarm yorgunluğuna girer.** Bağsız tedarikçi ödemesi (§1.3) ve
   ödenmiş ama kapatılamayan vergi satırı (§1.4) çıkışı şişirir. K4 FIFO'su ve K6 süzgeci olmadan kart kalıcı
   olarak "nakit açığı" gösterir ve güveni yitirir.
3. **Evrak çift sayılır.** `payment_id` NULL alınan evrak + açık satış (§1.5) ve eski/yeni evrak tablosunun
   yan yana yaşaması aynı parayı iki kez sokar. K5 uyarısı ve eski tabloyla örtüşmenin ölçümü (Ek) şart.
4. **Görünmeyen çıkışlar rakamı iyimser yapar.** KDV, maaş, kira, abonelik, POS valörü v1'de YOK. Cevap bunları
   `warnings`te ADIYLA yazmazsa kullanıcı "önümüzdeki 30 gün net +35 bin" rakamına güvenir.

---

## EK — ÖLÇÜLEMEYENLER (DOĞRULANMADI)

* **Muhtasar/SGK ödeme günü (26) ve KDV ödeme günü**: mevzuat kaynağına gidilmedi. Kodda sabit yok (§1.4).
* **PG'de koşu**: SQL ve motor yalnız SQLite tohumunda koştu. PG lehçe biçimleri (§2.2) yazıldı, çalıştırılmadı.
* **Eski `financial_instruments` ile `cek_senetler` arasında aynı evrakın çift kaydı**: canlı veri yok, tohumda
  iki tablo da boş.
* **Üretimde vadesiz alış oranı** ve **bağsız tedarikçi ödemesi hacmi**: yalnız tohum ölçüldü (tohum her alışa
  vade yazıyor).
* **İleri tarihli manuel finans hareketinin firmalarca planlı ödeme olarak kullanılıp kullanılmadığı** (K7).
* **Core envanteri / kiracı ifadesi artışı (+~6)**: sorgular yazılmadı, aritmetik TAHMİNDİR.
* **SEC-3b tarayıcısının yeni uca bakışı** ve **`test_v2_9_decimal_contract.py`nin para dizgisi sözleşmesi**:
  dosyalar bu keşifte okunmadı.
* **Tam ödenmiş peşin satışın ödemesi sonradan silinirse vadenin yeniden türetilmemesi**: kodda türeten başka
  yol bulunamadı (yalnız `_save`), senaryo koşulmadı. Böyle bir satış motora ve projeksiyona hiç girmez.
* **Firma geneli motor maliyetinin bu uçtaki payı**: F10-9 §3.3'ün ölçümü (1.896 ms @ 20.060 satış) ödünç
  alındı, projeksiyonun toplam süresi ölçülmedi.
