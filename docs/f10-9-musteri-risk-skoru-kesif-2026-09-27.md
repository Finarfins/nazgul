# Faz 10-9 — Müşteri risk skoru + POS'ta veresiye uyarısı — KEŞİF

**Ölçüm tarihi:** 2026-09-27 · **Taban:** `origin/develop` = `7f45b1e` (#173 birleşmesi) · **Worktree:**
`C:\Users\HHH\nazgul-f10-9` · **Dal:** `docs/f10-9-musteri-risk-skoru-kesif` · **Kapsam:** SALT OKUMA keşif.
Uygulama kodu, göç, test ve şema DEĞİŞMEDİ; yeni rota YOK.

Her sayı bu tabanda ölçüldü ve `dosya:satır` ile yazıldı. Ölçülemeyen her iddia **DOĞRULANMADI** etiketi
taşır (Ek). Bütün `app/` ve `tests/` yolları `backend/` altındadır; kök düzeyindeki `test_*.py` dosyaları da
`backend/` kökündedir.

**Ölçüm düzeneği (depoya GİRMEDİ, scratchpad'de):** CPython 3.12 venv; `seed_demo_data.py` ile SQLite tohumu
(Alembic başı `20260925_0092`; 25 müşteri, 60 satış, 100 ödeme). Üstüne üç betik koştu: (P) davranış probu, API
`TestClient` ile, 5 senaryo; (Ö) ölçek probu, tohum + N müşteri × 10 satış; (S) önerilen skorun prototipi. Süreler
bu PC'de, SQLite'ta, tek iş parçacığıyla ölçüldü. PG'de ölçülmedi (Ek).

> **Faz tanımı (ölçüldü):** `docs/yol-haritasi.html:101`: "Müşteri risk skoru, POS'ta veresiye uyarısı",
> dayanağı "gecikme geçmişi + limit". `docs/ROADMAP.md` bu satırı ANMIYOR.

---

## 0. YÖNETİCİ ÖZETİ — dört cümlelik sonuç

1. **POS'ta veresiye limiti ZATEN VAR ve uygulanıyor. Eksik olan limit değil, gecikme geçmişi.** POS satışı
   `_save`e gider (`routers/pos.py:349`), o da müşteri `risk_limit`ini `_credit_exposure` ile ölçer
   (`routers/transactions.py:150-232`, kapı `:776-804`). Firma politikası `credit_limit_policy`
   `block|manager_override|allow` değerlerini alır (`company_policies.py:14-18`). Probta limit 100 TL iken
   ikinci 80 TL'lik veresiye **409** aldı. `manager_override` modunda `satis` rolü **403** aldı, `admin` **201**
   aldı ve `policy_override_logs`a `credit_limit` satırı yazıldı (P1). **Skorun yapacağı iş bu yüzden yeni bir
   kapı değil:** limitin göremediği davranışı (gecikme, karşılıksız çek, vade farkı) satışın önüne koymak.
2. **Bugünkü limit kapısı ile cari kartı AYNI bakiyeyi görmüyor (ölçüldü).** Kapı yalnız `service_fee` borç
   belgesini sayar (`service_receivable_engine.py:493-509`, `charge_type='service_fee'`). Kart ve liste ise
   `late_fee` ile `bounced_check`i de sayar (`entity_detail.py:236-275`, `routers/customers.py:114-135`). Probta
   500 TL'lik karşılıksız çek borcu kartta **500,00**, kapıda **0,00** göründü. Satış iadesini ise yalnız
   yaşlandırma düşüyor (P3: yaşlandırma **640**, kart **740**, kapı **240**). **Sonuç: karşılıksız çeki olan
   müşteri bugün limit kapısından o çek hiç yokmuş gibi geçer.** Bu, 10-9'dan bağımsız bir kapı kusurudur (K6).
3. **Skor saf ve belirlenimci kurulabilir: SAKLANAN SÜTUN yok, istek anında hesaplanır.** Beş sinyalin beşi de
   bugün mevcut motorlardan tek müşteri için okunabiliyor (§1). Ölçülen tek-müşteri maliyeti
   `build_principal_timelines` için **3,4 ms**, `_credit_exposure` için **2,4 ms**; 2.025 müşteri / 20.060
   satışta da SABİT kaldı (§3.3). Bunun tersine firma geneli `calculate_net_receivables` **1.896 ms**,
   yaslandırma **2.226 ms**, tüm müşterilerin zaman çizelgesi **8.506 ms** sürdü. → **POS için gece önbelleği
   GEREKMEZ.** Önbellek yalnız "liste sütunu" isteği gelirse düşünülür (K3).
4. **PR bölünmesi (hepsi göçsüz):**
   * **10-9-fix:** kapıyı kartla hizalar; K6'ya bağlı, isteğe bağlı.
   * **10-9a:** `app/risk_skoru.py` saf aritmetik modülü + `GET /api/customers/{customer_id}/risk-score`,
     izin `sales`. Pinlerde rota 425→**426**, GET 202→**203**, AUTH 412→**413** oynar.
     `read`/`undeniable`/`guarded` 91/97/35 SABİT kalır, `TENANT_TABLES` 127 ve Alembic `0092` SABİT.
   * **10-9b:** POS uyarısı + onay. Yeni rota yok, `ACTION_TYPES` 84 SABİT; yalnız `pos.py` parmak izi ve
     `Pos.tsx`/`Pos.test.tsx` değişir.

   **KVKK:** skor İÇ bir değerlendirmedir, müşteriye gösterilmez. Otomatik ret yerine insan onaylı uyarı
   önerilir (K8).

---

## 1. SİNYALLER — bugün müşteri başına ne ölçülebiliyor

### 1.1 Özet tablo

| # | Sinyal | Kaynak (dosya:satır) | Tek müşteri için okunabilir mi? | Tohumda |
|---|---|---|---|---|
| S1 | Açık alacak + yaşlandırma kovası (vadesiz / 1-30 / 31-60 / 61-90 / 90+) | `receivables_engine.calculate_net_receivables` `:469-478` → `routers/reports.py:34-40,83-97,325-414` | **HAYIR**: motor firma geneli çalışır, `customer_id` süzgeci yok (`:288-304`, `:400-418`) | 25 müşterinin 25'inde açık belge var |
| S2 | Geçmiş ödeme davranışı (belge vadesinden kaç gün sonra kapandı) | `receivables_engine.build_principal_timelines` `:481-…` (müşteri başına, bayraktan bağımsız) | **EVET** | 27 müşterinin 20'sinde son 12 ayda kapanan belge < 3 (S) |
| S3 | Karşılıksız çek | `receivable_charge_documents.charge_type='bounced_check'` (`cek_senet_cari.py:27-36`, `:206-247`); `cek_senetler.portfoy_durumu='karsiliksiz'` | **EVET** (tek tablo, `customer_id`) | 0 satır |
| S4 | Vade farkı (gecikme cezası) geçmişi | `receivable_charge_documents.charge_type='late_fee'` (`late_fee_charge_engine.py:405-427`) | **EVET** | 0 satır |
| S5 | Limit kullanımı | `customers.risk_limit` (`core_schema.py:39`) + `_credit_exposure` (`transactions.py:150-232`) | **EVET** | 25/25 müşteride limit > 0 (50.000–400.000) |
| — | Satış iadesi | `returns` (`return_type='sale_return'`) | EVET | 0 satır |
| — | POS veresiye satışı | `orders.payment_method='credit'` + vade türetimi (`test_pos_credit_sale_aging.py:3-12`) | EVET | 60 satışın 15'i `credit` |

### 1.2 S1 — yaşlandırma (ölçüldü)

* Kovalar `reports.py:34-40` (`not_due`, `days_1_30`, `days_31_60`, `days_61_90`, `days_90_plus`). Sınırlar
  `_aging_bucket`ta (`:83-97`): `gecikme = as_of − due_date`; ≤0 vadesiz, ≤30, ≤60, ≤90, sonrası 90+.
* Belgeler `calculate_net_receivables`ten gelir (`receivables_engine.py:469-478`). Bu fonksiyon satış
  anaparasını (`calculate_receivables`, `:270-392`) ve deftere işlenmiş borç belgelerini birleştirir. Borç
  belgeleri `late_fee`, `service_fee` ve `bounced_check`tir (`_late_fee_receivables`, `:395-466`).
* **Satış SQL'i** (`:290-301`):
  `SELECT o.id,o.customer_id,…,o.due_date,o.final_total,COALESCE(o.paid_amount,0) FROM orders o JOIN customers c
  … WHERE o.company_id=:cid AND o.due_date IS NOT NULL AND o.due_date<>'' AND <muhasebe durumu> AND
  <normalize order_date> <= :as_of`. **`customer_id` süzgeci YOK.** Ödeme ve iade toplamları da firma geneli
  `GROUP BY` ile okunur (`_movement_totals`, `:149-232`). Bunun sonucu, tek müşteri için bile motorun bütün
  firmayı taramasıdır.
* **Borç belgesi vadesi TEK kuraldan gelir** (`charge_due_date_sql`, `:67-99`). `service_fee` ve
  `bounced_check` için `due_date_snapshot`, `late_fee` için `period_end` kullanılır. Kart, liste ve motor aynı
  kuralı okur.
* **Tahsis defteri bayrağı** `payment_allocation_engine_enabled` varsayılan `False`tur (`config.py:216`).
  Bayrak kapalıyken motor bağlı ödemeleri belgeye, bağsız ödemeleri FIFO ile dağıtır (`:341-376`).
* Yaşlandırma satırı müşteri başına `portfolio_checks` (portföyde / tahsildeki alınan evrak) ve
  `net_risk = total − portföy` de taşır (`reports.py:60-62,396-401`). Portföy **bugünün** durumudur, `as_of`a
  göre değildir (`:296-298`).

### 1.3 S2 — geç ödeme davranışı (ölçüldü: kaynak var, hazır bir ÖZET yok)

* `grep -rn "days_late\|gecikme_gun\|late_payment" app` sonucu hazır bir "kaç gün geç ödedi" alanı YOK. Vade
  farkı motoru gecikme günü hesaplar ama yalnız cezayı üretmek için, bir özet saklamaz.
* **Kaynak `build_principal_timelines`tir** (`receivables_engine.py:481-…`). Tek müşteri için her satışın
  anapara değişimlerini tarihli olarak verir:
  * satış (`:565-572`)
  * tahsis (`:574-602`)
  * bağlı ve bağsız ham ödeme (`:604-645`)
  * iade (`:647-685`)
  * eski artık (`:687-…`)

  Docstring'e göre fonksiyon bayraktan bağımsızdır: defterdeki ödemeyi defterden, defterde olmayanı eski
  yoldan okur (`:487-495`). Vade farkı motoru da bu fonksiyonu kullanır (`late_fee_charge_engine.py:22`).
* **Türetim (prototipte koştu):** her belge için bakiye ≤ 0 olduğu ilk değişim tarihi = kapanış tarihi.
  `gecikme = kapanış − due_date`. Son 12 ayda kapanan belgeler arasında `gecikme > 7 gün` olanların oranı S2'dir.
* **Kısıt (ölçüldü):** bağsız ödemeler olay listesine `order_id=0` ile girer (`:637-645`). Hangi belgeyi
  kapattıkları FIFO dağıtımına bağlıdır, yani eski düzende kapanış tarihi belge bazında bir **model
  çıktısıdır**, kesin gerçek değildir. Bayrak açıkken tahsis `effective_date` taşıdığı için kesindir.

### 1.4 S3 — karşılıksız çek (ölçüldü: iki kaynak var, biri kayıplı)

* Durum makinesinin geçişleri `cek_senet_engine.py:7-10,44-53`: `tahsile_verildi → karsiliksiz → iade`.
  `iade` bir SON durumdur.
* **Kayıplı kaynak:** `cek_senetler.portfoy_durumu='karsiliksiz'`. Karşılıksız çek müşteriye `iade`
  edildiğinde satır `iade`ye geçer. Bir `iade` satırı ise ya karşılıksız çıkmış ya da portföyden doğrudan
  iade edilmiş olabilir (`portfoyde → iade`, `:45`). **Satırın son durumu ikisini AYIRAMAZ.**
* **Kalıcı kaynak:** `receivable_charge_documents.charge_type='bounced_check'`. Belge `karsiliksiz`de açılır ve
  `karsiliksiz → iade`de ikinci belge açılmaz (`cek_senet_cari.py:27-36`). Ancak `portfoyde → iade`de de aynı
  belge açılır (`:28-29`, "karsiliksiz / iade"). **Belge sayısı yani "karşılıksız + iade edilen evrak" sayar.**
* **Kesin ayrım yalnız aktivite günlüğünde:** `cek_senet.durum` satırının `details` alanında
  `{"from":…,"to":"karsiliksiz"}` (`routers/cek_senetler.py:611-617`). Ancak aktivite satırı arşivlenebilir
  (`activity_logs.archived_at`) ve JSON alan okuması lehçeye bağlıdır.
* Yalnız köprüden doğmuş (bir ödemeye bağlı) evrak borç belgesi açar. CS1'de elle girilen evrak açmaz
  (`cek_senet_cari.py:38-40`).
* **Öneri (K4):** S3 = son 24 ayda ters kaydı olmayan `bounced_check` belge sayısı. Etiket "karşılıksız /
  iade edilen evrak" olur ve dürüsttür. Aktivite günlüğü okunmaz.

### 1.5 S4 — vade farkı geçmişi

* `receivable_charge_documents` (33 sütun). `charge_type='late_fee'` yalnız `posted` olunca yaşlandırmaya girer
  (`receivables_engine.py:411-414`). Ters kayıt `reversal_of_document_id` ile ayrı bir satırdır (CHECK
  `ck_receivable_charge_document_reversal_sign`, `PRAGMA`/`sqlite_master` ile okundu).
* **SQL (prototipte koştu):**
  `SELECT customer_id,COUNT(*) FROM receivable_charge_documents WHERE company_id=:c AND charge_type='late_fee'
  AND status='posted' AND reversal_of_document_id IS NULL GROUP BY customer_id`
* **Kısıt:** vade farkı kesmek firmanın tercihidir. Hiç kesmeyen firmada S4 herkes için 0'dır, yani S4 "müşteri
  kötü" demekten çok "firma kesti" der. Bu yüzden ağırlığı düşük tutulur (§3.1).

### 1.6 S5 — limit ve bakiye: ÜÇ FARKLI FORMÜL (ölçüldü, P2/P3)

| Yüzey | Formül | `late_fee` | `bounced_check` | `service_fee` | Satış iadesi |
|---|---|---|---|---|---|
| Limit kapısı `_credit_exposure` (`transactions.py:150-232`) | açılış + satış − ödeme + servis borcu | **YOK** | **YOK** | VAR (`service_receivable_engine.py:493-509`) | **YOK** |
| Cari kartı `entity_detail` (`entity_detail.py:236-275`) + liste (`customers.py:114-135`) | açılış + satış + borç belgesi − ödeme | VAR | VAR | VAR | **YOK** |
| Yaşlandırma (`receivables_engine.py:270-478`) | belge başına kalan (FIFO/defter) | VAR | VAR | VAR | VAR (`:197-225`) |

**P2/P3 probu** (API ile, 1000 TL limitli taze müşteri):

| Adım | Kapı `current_balance` | Kart `current_balance` | Yaşlandırma `total` |
|---|---:|---:|---:|
| Boş | 0,00 | 0,0 | — |
| + 500 TL karşılıksız çek borcu (`bounced_check`, `posted`) | **0,00** | 500,0 | 500,00 |
| + 240 TL veresiye satış (70 gün önce, 30 gün vade) | **240,00** | 740,0 | 740,00 |
| + 100 TL satış iadesi (satışa bağlı, `completed`) | **240,00** | **740,0** | **640,00** |

* **Karşılıksız çek:** kapı borcu görmüyor. Borç, müşterinin "çek hiç alınmamış gibi eski bakiyesine
  dönmesi"nin tek yoludur (`cek_senet_cari.py:29-31`), yani kapı çeki hâlâ ödenmiş sayıyor.
* **İade:** `workflow.py` iade yazarken `payments`a satır AÇMIYOR (`grep -n "payments" app/routers/workflow.py`
  → 0). Kart, liste, ekstre ve kapı iadeyi düşmüyor, yaşlandırma düşüyor. (Probta iade doğrudan SQL ile yazıldı.
  Kart/kapı SQL'inde `returns` geçmediği kodla da ölçüldü: `grep -n "returns\|sale_return" app/entity_detail.py
  app/routers/customers.py app/statement.py` → 0.)
* **Sonuç:** skor hangi bakiyeyi kullanırsa kullansın bu üç sayıdan en az biriyle çelişir. Öneri, skorun
  **yaşlandırma motorunun** sayısını kullanmasıdır. Kullanıcının aynı müşteri için gördüğü "vadesi geçmiş"
  rakamı rapordaki rakamla aynı olur. Kapı farkı K6'da ayrıca karara bağlanır.

---

## 2. BUGÜNKÜ "LİMİT" MANTIĞI — ölçüm

### 2.1 Nerede var

| Yer | Ne yapıyor | Kanıt |
|---|---|---|
| `routers/transactions.py:776-804` | Satış kaydında (`kind == "sale"`) `risk_limit > 0 AND projected > limit AND projected > current` ise politika uygulanır | P1b/P1c/P1e |
| `company_policies.py:109-124` `enforce_known_violation` | `allow` → geç; `manager_override` → başlık yoksa 409 "Yönetici onayıyla…", varsa rol + gerekçe kontrolü; `block` → 409 | P1b/P1c/P1d |
| `company_policies.py:18` | `MANAGER_OVERRIDE_ROLES = {"admin","yonetici"}`; gerekçe ≥ 5 karakter (`:93-97`) | P1d: `satis` → **403** |
| `company_policies.py:136-166` `record_policy_overrides` | `policy_override_logs` satırı: `policy_name`, `resource_type`, `resource_id`, `reason`, `request_id` | P1f: `('credit_limit','orders',62,'Tanıdık müşteri')` |
| `routers/pos.py:349` | POS satışı `_save`e gider, yani limit kapısı POS'ta da AYNEN işler | P1b: POS'ta **409** |
| `routers/pos.py:196-198` | Veresiye satış müşterisiz yapılamaz (400) | kod |
| Ön yüz `Pos.tsx:186-194,269` | 409 + "Yönetici onayıyla" → `PolicyOverrideDialog` açılır, gerekçeyle yeniden gönderilir (`api.ts:275-282`) | kod |
| Ön yüz `TransactionDialog.tsx:144` | Satış formunda bakiye / limit / "bu satış sonrası" / "kalan limit" gösterilir | kod |
| Ön yüz `EntityQuickActions.tsx:55`, `EntityDetail.tsx:140`, `Entities.tsx:186` | "Risk %" çipi, "Risk Kullanımı" KPI'ı, liste sütunu | kod |
| Firma ayarı `Companies.tsx:31` → `PUT /api/company-settings` (`routers/companies.py:33,168-206`) | `credit_limit_policy` seçimi | kod |

### 2.2 Limit aşan müşteri veresiye alırsa bugün ne olur (P1, ölçüldü)

Test müşterisinin limiti 100 TL, satış tutarı 80 TL.

| Politika | Kim | Sonuç |
|---|---|---|
| (limit altında) ilk satış | admin | **201** |
| `block` | admin | **409** "Müşteri risk limiti aşılıyor. Limit: 100.00, işlem sonrası bakiye: 160.00." |
| `manager_override`, başlıksız | admin | **409** "… Yönetici onayıyla devam etmek için gerekçe girin." |
| `manager_override` + `X-Policy-Override` | `satis` | **403** "Bu politika istisnasını yalnızca yönetici onaylayabilir" |
| `manager_override` + `X-Policy-Override` | admin | **201**; `policy_override_logs` +1 satır |
| `allow` | admin | **201**, iz YOK |

**Ek gözlemler (ölçüldü):**
* `pos.sale_created` aktivitesi (`pos.py:389-410`) istisnayı TAŞIMAZ. Ayrıntıda yalnız
  `price_or_discount_override` var (`:407`). Limit istisnası yalnız `policy_override_logs`ta görünür (P1g).
* Ön yüz rolü sormadan diyalog açar (`Pos.tsx:194`: `policyOverrideRequired` yalnız 409 + metne bakar).
  `satis` kasiyer diyaloğu görür, gerekçe yazar ve 403 alır. Bu bir UX boşluğudur, 10-9b'de kapatılabilir.
* Limit **0** ise kapı hiç çalışmaz (`risk_limit > ZERO_MONEY`, `:786`). Tohumda 25/25 müşterinin limiti var.
  Canlıda limitsiz müşteri oranı **DOĞRULANMADI**.
* Kapı yalnız **satışı** korur. Servis iş emri faturalaması (`service_fee`) ve iş akışı dönüşümü
  (`workflow.py`) bu kapıdan geçer mi, taranmadı (**DOĞRULANMADI**).

---

## 3. SKOR — seçenekler ve öneri

### 3.1 Önerilen formül (belirlenimci, açıklanabilir, sabit ağırlıklı)

`puan = max(0, 100 − Σ ceza)`, `harf = A (≥85) · B (70–84) · C (50–69) · D (30–49) · E (<30)`.

| Kod | Sinyal | Ceza | Tavan | Gerekçe |
|---|---|---|---:|---|
| `gecikme` | S1: en eski açık belgenin gecikmesi | 1–30 g → 5 · 31–60 → 15 · 61–90 → 25 · 90+ → 35 | 35 | Yaşlandırma kovalarıyla BİREBİR aynı sınırlar (`reports.py:83-97`), yani "neden D?" sorusunun cevabı rapordaki kovadır |
| `vadesi_oran` | S1: vadesi geçmiş / açık toplam | `round(oran × 20)` | 20 | Tek küçük gecikmiş belge ile bütün bakiyesi gecikmiş müşteriyi ayırır |
| `gec_kapanis` | S2: son 12 ayda >7 gün geç kapanan belge oranı | `round(oran × 15)` | 15 | "Şu an temiz ama hep geç öder" müşterisi |
| `karsiliksiz` | S3: son 24 ay `bounced_check` (ters kaydı yok) | her biri 15 | 30 | En güçlü tekil sinyal |
| `vade_farki` | S4: son 12 ay `late_fee` (ters kaydı yok) | her biri 3 | 10 | Firmanın tercihine bağlı olduğu için zayıf (§1.5) |
| `limit` | S5: bakiye / limit | ≥ %80 → 5 · > %100 → 15 | 15 | Limit zaten ayrı kapı; skor yalnız yaklaşımı söyler |

* **Tamsayı aritmetiği:** oranlar `Decimal` ile hesaplanır ve `ROUND_HALF_UP` ile tamsayıya iner (`money.py`
  deseni). Sonuç kuruş değil puandır.
* **Açıklanabilirlik:** uç, `cezalar: [{kod, puan, aciklama, kanit}]` listesini döner. `kanit` belge no + vade
  + gün taşır. Harf tek başına hiçbir yerde gösterilmez.
* **Yetersiz veri:** son 12 ayda kapanan belge sayısı < 3 ise `yetersiz_veri=true` olur. Harf yine hesaplanır
  ama arayüz "yeni/az geçmişli müşteri" der. Yeni müşteri varsayılan olarak "A" görünmez (K2). Tohumda 27
  müşterinin **20'si** bu durumda (S).
* **Pür modül:** `app/risk_skoru.py`, `app/mustahsil.py` deseninde (`mustahsil.py:1-6`: "veritabanına DOKUNMAZ,
  `Session` görmez"). Girdi `RiskSinyalleri` (dondurulmuş dataclass), çıktı `RiskSkoru`. Formül ve ağırlıklar
  modülün başında docstring olarak durur. Sinyal toplama ayrı `app/risk_skoru_okuma.py`dedir (`mustahsil_okuma.py`
  deseni).

### 3.2 Prototip sonucu (S, tohum verisi, ölçüldü)

| Veri | Müşteri | A | B | C | D | E | Yetersiz veri | Süre (hepsi) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Tohum (`seed.db`) | 25 | 19 | 1 | 1 | 4 | 0 | 18 | 355 ms |
| Tohum + P2/P3 müşterileri | 27 | 20 | 1 | 2 | 4 | 0 | 20 | 245 ms |

* En düşük: müşteri 18, **37 / D**: `gecikme 35 + vadesi_oran 20 + gec_kapanis 8`.
* Karşılıksız çekli P2 müşterisi: **50 / C** (`gecikme 15 + vadesi_oran 20 + karsiliksiz 15`). `limit` cezası
  **0** çıktı, çünkü prototip S5 için bugünkü kapı fonksiyonunu çağırdı ve kapı çeki görmüyor (§1.6). Bu, K6'nın
  skora sızan sonucudur.
* **Uyarı:** tohum gecikme için tasarlanmamıştır. Dağılım eşiklerin kalibrasyonu DEĞİLDİR, yalnız formülün uçtan
  uca koştuğunu gösterir. Eşik kalibrasyonu pilot müşteri verisiyle yapılır (K1, **DOĞRULANMADI**).

### 3.3 İstek anında mı, gece önbelleği mi — maliyet (Ö, ölçüldü)

| Ölçek (SQLite, bu PC) | `calculate_net_receivables` (firma, tek geçiş) | `yaslandirma_verisi` (firma) | `build_principal_timelines` (1 müşteri) | `_credit_exposure` (1 müşteri) | zaman çizelgesi × tüm müşteriler |
|---|---:|---:|---:|---:|---:|
| 27 müşteri / 64 satış (tohum + P) | 10,0 ms | 11,5 ms | 2,2 ms | — | — |
| 525 / 5.060 / 2.622 ödeme | 598 ms | 704 ms | 3,9 ms | 2,4 ms | 2.204 ms |
| 2.025 / 20.060 / 10.042 ödeme | 1.896 ms | 2.226 ms | **3,4 ms** | **2,4 ms** | 8.506 ms |

* **Tek müşteri yolu ölçekten bağımsız.** Tek müşteri skoru için tahmini toplam: zaman çizelgesi (S2) + müşteriye
  süzülmüş açık belgeler (S1) + iki tek tablo sayımı (S3/S4) + bakiye (S5). Ölçülen parçalar 3,4 + 2,4 ms'dir,
  S1'in müşteri süzgeçli hâli yazılmadığı için ölçülemedi. Tahmin **< 15 ms** (**DOĞRULANMADI**, dilimde
  ölçülür).
* **Firma geneli yol POS'ta kullanılamaz.** Bugünkü motorla tek müşteri için bile S1'i okumak 2.025 müşteride
  ~1,9 s demektir. Bu yüzden 10-9a motora `customer_id` süzgeci ekler (§5.2) ya da S1'i zaman çizelgesinden
  türetir (K5).
* **Öneri: saklanan sütun YOK, istek anında hesaplanır.** Gerekçeler:
  1. POS tek müşteri ister ve tek müşteri yolu ölçekten bağımsızdır.
  2. Gece önbelleği göç (`TENANT_TABLES` +1, Alembic +1) ve bir zamanlayıcı ister. Gün içi tahsilat ve
     karşılıksız çek skora ancak ertesi sabah yansır, yani POS kasiyere dünün skorunu gösterir.
  3. Hesap belirlenimci olduğu için önbellek doğruluk kazandırmaz, yalnız hız kazandırır ve bu hız tek müşteride
     gereksizdir.

  Liste ekranında "tüm müşterilerin harfi" sütunu istenirse (2.025 müşteride ~8,5 s) o gün ayrı bir karar
  verilir (K3).

---

## 4. POS UYARI SÖZLEŞMESİ

### 4.1 Yerleşim (ölçüldü)

* **Arka uç:** `POST /api/pos/sale` (`routers/pos.py:263-422`). Müşteri `_resolve_customer`da çözülür
  (`:189-210`, çağrı `:299`), toplam `_totals` ile bulunur (`:333`). `_save`ten (`:349`) ÖNCE hem müşteri hem
  tutar bellidir. Uyarı kontrolünün yeri `:335-347` (ödeme tipi dalı) ile `:349` arasıdır.
* **Ön yüz:** `frontend/src/pages/Pos.tsx`
  * müşteri seçimi `Autocomplete` `onChange` (`:242`)
  * ödeme tipi `Select` (`:243`, `credit` = "Veresiye")
  * gönderim `completeSale` (`:186-194`)
  * mevcut onay diyaloğu `PolicyOverrideDialog` (`:269`)

  Müşteri listesi sayfa açılırken tek `GET /customers` ile gelir (`:133`). Tip `{id,name}` olarak daraltılmıştır
  (`:27`), ama uç `risk_limit`, `current_balance`, `risk_exceeded` alanlarını ZATEN döner
  (`entity_detail.py:60-86`).

### 4.2 Seçenekler

| | W1: yalnız ön yüz uyarısı | **W2: arka uç uyarısı + kasiyer onayı (ÖNERİLEN)** | W3: D/E'de blok + yönetici istisnası |
|---|---|---|---|
| Ne olur | Müşteri seçilip `credit` olunca `GET …/risk-score` çağrılır, `Alert` gösterilir | W1 + satış `D`/`E` ise ve istek `X-Risk-Onay: <harf>` taşımıyorsa **409** `{"code":"RISK_SKORU_UYARISI","harf":…,"puan":…,"cezalar":[…]}` döner. Kasiyer onaylar, aynı `Idempotency-Key` ile yeniden gönderilir | `E` (veya `D`+`E`) satışı `enforce_known_violation` ile engellenir, istisna `MANAGER_OVERRIDE_ROLES` |
| API istemcisi atlayabilir mi | EVET | HAYIR | HAYIR |
| Denetim izi | YOK | `pos.sale_created` ayrıntısına `risk_harfi`, `risk_puani`, `risk_onayi` eklenir (`pos.py:389-410`). Yeni `ACTION_TYPES` YOK | `policy_override_logs` `policy_name='risk_score'` (mevcut tablo, `String(60)` serbest). Yeni tablo yok |
| Politika ayarı | yok | yok (sabit: D/E'de onay ister) | **`companies.risk_score_policy` sütunu → GÖÇ** (`credit_limit_policy` deseni, `routers/companies.py:33`). Alternatif olarak `credit_limit_policy`e bağlanır, ama bu iki farklı kararı tek anahtara kilitler |
| KVKK (K8) | sorun yok | insan karar verir | otomatik ret: KVKK md. 11/1-g itiraz hakkı riski (**DOĞRULANMADI**, hukuki yorum) |

**Tavsiye W2.** Gerekçeler:
* Asıl engel zaten var ve yönetici istisnalıdır (§2).
* Skor bir UYARIDIR. Kasiyerin "gördüm" demesini kayda geçirmek yeterli ve göçsüzdür.
* Blok kararı (W3) göç ister ve otomatik karar riskini taşır. Pilot veriyle eşikler kalibre edilmeden
  engellemek yanlış pozitifte satış kaybettirir (K1).

**Idempotency (ölçüldü):** 409, `pos_idempotency` satırı yazılmadan önce döner (`pos.py:349` öncesi). Ön yüz aynı
anahtarı yeniden kullanır (`Pos.tsx:189`, `saleKeyRef`), yani onaylı ikinci istek çift satış üretmez. İstek
parmak izi yalnız gövdeden türer (`pos.py:50-55`), başlıklar girmez. Bu yüzden onay başlığı eklemek parmak izini
değiştirmez.

**İki 409'un sırası:** risk uyarısı limit kapısından ÖNCE döner. Kasiyer önce riski onaylar; limit aşılıyorsa ikinci
409 (yönetici istisnası) gelir. Tersi, yöneticiye onaylamadığı bir riskli satışı onaylatır.

### 4.3 Roller

| Rol | `sales` izni (`auth.py:195-241`) | Skoru görür (10-9a) | W2 onayı verebilir | Limit istisnası (bugün) |
|---|---|---|---|---|
| admin | `*` | ✓ | ✓ | ✓ |
| yonetici | ✓ | ✓ | ✓ | ✓ (`company_policies.py:18`) |
| muhasebe | ✓ | ✓ | ✓ | ✗ |
| satis | ✓ | ✓ | ✓ | ✗ (P1d: 403) |
| depo | ✗ | ✗ | — (POS yazma `sales` ister, `auth.py:1356-1357`) | ✗ |
| rapor | ✗ | ✗ | — | ✗ |

---

## 5. PR BÖLÜNMESİ VE PİN DELTALARI

### 5.0 TABAN — `7f45b1e` üzerinde ÖLÇÜLDÜ

| Pin | Değer | Yer |
|---|---:|---|
| Rota işlemi / yolu | **425 / 331** | `tests/test_route_security_contracts.py:744-745` + `EXPECTED_SECURITY_FINGERPRINT` (`:746`) |
| GET envanteri | **202** | `tests/test_route_get_permission_inventory.py:614` (`GET_INVENTORY_COUNT`) + `EXPECTED_GET_PERMISSIONS` + `GET_INVENTORY_FINGERPRINT` (`:615`) |
| Kimlik doğrulamalı / read / undeniable | **412 / 91 / 97** | `tests/test_authorization_population_reconciliation.py:421-423` |
| `GUARDED_READ_OPERATIONS` | **35** | aynı dosya `:428` (modül yüklenip `len` ölçüldü) |
| `TENANT_TABLES` | **127** | `tests/test_tenant_scoping_guard.py` (modül yüklenip ölçüldü) |
| Core sorgu envanteri | **252** (select 171 / update 69 / delete 12) | `tests/test_core_query_inventory.py:1348,1352` |
| Core kiracı ifadesi | **209** | `tests/test_core_tenant_scoping_guard.py:2149` |
| `ACTION_TYPES` / `RESOURCE_TYPES` | **84 / 24** | `app/activity_log.py:61,263`; `test_activity_log_panel.py:219` (küme), `:282` (`== 84`) |
| `pg_twins.txt` / `alt_surec_sql.txt` | **146 / 137** satır | `tests/pins/` |
| `cari_alan_envanteri.txt` | **21** veri satırı (71 fiziksel) | `tests/pins/cari_alan_envanteri.txt` |
| Alembic başı | **`20260925_0092`** | `"20260925_0092"`: **21 isabet / 19 dosya** (göçün kendi `revision` satırı dahil) |
| Dosya parmak izleri (dilimlerin kıpırdatacağı) | `receivables_engine.py` (8 dinamik), `routers/pos.py` (1), `routers/transactions.py` (18), `routers/customers.py` (1) | `tests/test_tenant_scoping_guard.py:423,480,489,426` |

### 5.1 F10-9-fix — limit kapısını kartla hizala (K6 "evet" ise; 10-9b'den ÖNCE; GÖÇ YOK)

* `_credit_exposure` (`transactions.py:150-232`): `service_receivable_net_total` yerine kart ile AYNI borç belgesi
  toplamı kullanılır. Kaynak, `entity_detail.py:236-275`in `charge_total` sorgusudur: `late_fee` + `service_fee`
  + `bounced_check`, `posted/reversed`, `period_end<=bugün`.
* İade kapsam DIŞI (kart da düşmüyor; ayrı karar, Ek).
* **Davranış değişikliği:** karşılıksız çeki / vade farkı olan müşteri bugünden itibaren limite TAKILABİLİR.
  Canlıda etkilenen müşteri sayısı **DOĞRULANMADI**. Dilim öncesinde firma başına sayım önerilir.
* Pin: `transactions.py` parmak izi (`test_tenant_scoping_guard.py:489`) yeniden türetilir; dinamik `text()`
  sayısı 18'de kalmalıdır (ölçülür). Rota/GET/AUTH/`TENANT_TABLES`/Alembic/Core SABİT, `openapi.json` SABİT.
* Test: P2 senaryosu (500 TL `bounced_check` + 600 TL veresiye, limit 1000 → **409**) + PG ikizi (`pg_twins`
  146 → 147) + `alt_surec_sql.txt` (alt süreç betiği SQL taşırsa +1).

### 5.2 F10-9a — skor modülü + salt-okunur uç (GÖÇ YOK)

**Kod:**
* `app/risk_skoru.py`: SAF; §3.1 formülü, `RiskSinyalleri` → `RiskSkoru`. SQL YOK.
* `app/risk_skoru_okuma.py`: sinyal toplama. S2 `build_principal_timelines`, S3/S4 tek tablo sayımları, S5
  (10-9-fix sonrası) kapı bakiyesi.
* **S1 için müşteri süzgeci (K5 = a):** `calculate_receivables` / `_late_fee_receivables` / `_movement_totals`
  imzalarına isteğe bağlı `customer_id` eklenir ve `None` iken SQL bayt bayt aynı kalır (yaslandırma raporu
  değişmez).
* Uç: `GET /api/customers/{customer_id}/risk-score?as_of=`. Cevap `{puan, harf, yetersiz_veri, cezalar[],
  hesaplandi: as_of}`. **Cari kişisel alanı TAŞIMAZ** (ad/VKN/telefon yok, yalnız `customer_id`).
* İzin: `auth.py`de `/statement` kuralının (`:1316-1321`) yanına tam sonek kuralı:
  `method in SAFE_METHODS and path.startswith("/api/customers/") and path.endswith("/risk-score")` → `"sales"`.
  Kural `:1346-1347` genel `read` düşüşünün ÜSTÜNDE olmalıdır.

**Pin deltaları:**

| Pin | Delta |
|---|---|
| Rota işlemi / yolu | 425 → **426** / 331 → **332**; `EXPECTED_SECURITY_FINGERPRINT` yeniden |
| GET envanteri | 202 → **203**; `EXPECTED_GET_PERMISSIONS` +1 (`sales`); `GET_INVENTORY_FINGERPRINT` yeniden |
| Kimlik doğrulamalı | 412 → **413** |
| read / undeniable / guarded | **91 / 97 / 35 SABİT**, YALNIZ sonek kuralı `:1346`nın üstündeyse. Kural yoksa uç `read`e çözülür: read 92, undeniable 98 (skor `depo`/`rapor`a da açılır, K7) |
| `TENANT_TABLES` / Alembic | **127 / `0092` SABİT** (21 isabet dokunulmaz) |
| `receivables_engine.py` parmak izi | K5 = a ise yeniden (`test_tenant_scoping_guard.py:423`); dinamik çağrı sayısı 8'de kalmalı, ölçülür |
| Core envanteri / kiracı ifadesi | S3/S4 Core `select` ile yazılırsa 252 → ~254 / 209 → ~211 (tahmin, dilimde tarayıcıyla ölçülür); `text()` ile yazılırsa Core SABİT, yeni dosya `test_tenant_scoping_guard`a girdi ister |
| `ACTION_TYPES` | **84 SABİT** (okuma denetlenmez, K9) |
| `cari_alan_envanteri.txt` | cevap modeli PII taşımıyorsa 21 SABİT ya da `S` satırı +1; tarayıcı BFS'i `customers` tablosuna dokunan çağrı zincirinden kelime görebilir (`cari_alan_envanteri.txt:7-12` yanlış pozitif örneği). **DOĞRULANMADI**, dilimde üretilir |
| `pg_twins.txt` | 146 → **147** (tarih aritmetiği: `due_date` TEXT/DATE, `normalized_date_sql` iki lehçe, `receivables_engine.py:119-130`) |
| `alt_surec_sql.txt` | 137 → **138** (depo deseni: alt süreç betiği SQL taşır) |
| `openapi.json` / `types.gen.ts` | değişir (bir uç, bir şema) |
| Ön yüz | YOK (10-9a arka uçtur; nav/rota pinleri kıpırdamaz) |

**Testler:**
* `tests/test_f10_9a_risk_skoru.py`:
  * saf tablo testi (her ceza satırı, sınırlar 30/31, 60/61, 90/91, tavanlar, `max(0,…)`, harf eşikleri, yetersiz
    veri)
  * uç testi (`satis` 200, `depo`/`rapor` 403, komşu firma 404)
  * yaşlandırma ile eşitlik (skorun `vadesi` toplamı = aynı müşterinin `receivables-aging` satırı)
* `test_f10_9a_risk_skoru_postgresql.py`.

### 5.3 F10-9b — POS uyarısı + kasiyer onayı (W2; GÖÇ YOK)

**Kod:**
* `routers/pos.py`: `payment_type=='credit'` veya kalan > 0 iken ve müşteri perakende değilse skor hesaplanır;
  `D`/`E` + başlık yok → 409 `RISK_SKORU_UYARISI`. Başlık `request.headers`tan okunur (`override_context` deseni,
  `company_policies.py:53-72`), yani OpenAPI'ye parametre GİRMEZ.
* `pos.sale_created` ayrıntısına risk anlık görüntüsü eklenir (`:399-408`).
* `Pos.tsx`:
  * `Customer` tipine skor özeti
  * müşteri seçilip `credit` olunca `GET …/risk-score` çağrısı
  * seçim altında `Alert` (harf + ilk iki ceza açıklaması)
  * 409 `RISK_SKORU_UYARISI` → onay diyaloğu (mevcut `PolicyOverrideDialog` deseni, gerekçe İSTEĞE BAĞLI)
  * **ek:** limit istisnası diyaloğu yalnız `admin`/`yonetici` rolüne açılır (§2.2 UX boşluğu)

**Pin deltaları:**

| Pin | Delta |
|---|---|
| Rota / GET / AUTH / `TENANT_TABLES` / Alembic | **SABİT** (10-9a sonrası 426/332, 203, 413/91/97/35, 127, `0092`) |
| `routers/pos.py` parmak izi | yeniden (`test_tenant_scoping_guard.py:480`); dosya AST'sinin tamamından türer. Dinamik `text()` sayısı 1'de kalmalı (skor okuması `risk_skoru_okuma`da) |
| `ACTION_TYPES` / `RESOURCE_TYPES` | **84 / 24 SABİT** (yalnız `details` sözlüğü büyür) |
| `openapi.json` / `types.gen.ts` | **SABİT**, yalnız 409 gövdesi yeni `detail` şekli taşır (FastAPI 409'u şemaya yazmaz) |
| `Pos.test.tsx` | `route()` bilinmeyen URL'ye `product` döner (`Pos.test.tsx:31`). Yeni `/customers/{id}/risk-score` çağrısı mevcut 26 testte ürün nesnesi alır, yani `route` genişletilir. Yeni testler: uyarı görünür, D/E 409 → onay diyaloğu, onaylı yeniden gönderim aynı anahtarla, `satis` limit istisnası diyaloğunu görmez |
| Nav pinleri (4 dosya) | **SABİT** (yeni menü/rota yok) |
| `pg_twins.txt` | 147 → **148** (`test_pos_postgresql.py` genişletilirse SABİT) |
| e2e | `frontend/e2e` POS spec'i yeni 409'a takılmamalı; tohum müşterilerinin harfi ölçülür (tohumda 4 D var, §3.2) |

### 5.4 (Önerilen, kapsam DIŞI) F10-9c — W3 blok politikası

`companies.risk_score_policy` (göç, Alembic `0092`→`0093`: 21 isabetin çivi olanları + yeni PG ikizi),
`Companies.tsx` seçimi, `policy_override_logs.policy_name='risk_score'`. Yalnız K1 pilot kalibrasyonu ve K8 hukuki
görüş sonrası.

---

## 6. ŞEFE AÇIK KARARLAR (K1–K9)

**K1 — Eşikler ve ağırlıklar.** §3.1 tablosu ilk değerlerdir, tohum bunları kalibre ETMEZ (§3.2). → **Tavsiye:**
10-9a sabitlerle iner. Sabitler `risk_skoru.py`nin tek bir sözlüğündedir ve tablo testiyle çivilenir. Pilot
firmada 30 gün "gölge modda" harf dağılımı ölçülür (uç açık, POS uyarısı kapalı), ardından 10-9b eşikleri
kesinleşir. Firma başına ayar v1'de YOK (göç ister).

**K2 — Az geçmişli müşteri.** 27'nin 20'si "son 12 ayda < 3 kapanan belge" (§3.2). → **Tavsiye:**
`yetersiz_veri=true` ve harf yine hesaplanır. POS "yeni/az geçmişli" etiketi gösterir, W2 onayı İSTEMEZ (yalnız
D/E ister). Bu müşteriye varsayılan "A" rozeti basılmaz.

**K3 — İstek anında mı, önbellek mi?** → **Tavsiye: istek anında, saklanan sütun YOK** (§3.3: tek müşteri
3,4 + 2,4 ms, ölçekten bağımsız). Tüm müşteri listesinde harf sütunu v1'de YOK. İstenirse (2.025 müşteride
~8,5 s) ayrı dilim + gece önbelleği + göç.

**K4 — Karşılıksız çek sayımı.** `bounced_check` belgesi `portfoyde → iade`de de açılır (§1.4). → **Tavsiye:**
belge sayısı, etiket "karşılıksız / iade edilen evrak". Aktivite günlüğü JSON'u okunmaz (arşivlenebilir,
lehçeye bağlı).

**K5 — S1'in tek müşteri kaynağı.** (a) Motora `customer_id` süzgeci eklenir; yaslandırma raporuyla aynı fonksiyon
olduğu için eşitlik GARANTİDİR, `receivables_engine.py` parmak izi kıpırdar. (b) S1 zaman çizelgesinden türetilir;
motora dokunulmaz ama eşitlik ayrı testle kanıtlanmak zorundadır (**DOĞRULANMADI**, ölçülmedi). → **Tavsiye
(a).**

**K6 — Limit kapısı karşılıksız çeki ve vade farkını görmüyor (§1.6, P2).** → **Tavsiye: evet, 10-9-fix ayrı ve
10-9b'den önce.** Davranış değişir: etkilenen müşteri sayısı canlıda önce sayılır. İade farkı (kart/kapı düşmüyor,
yaşlandırma düşüyor) bu dilimde DEĞİL; ayrı bir "cari bakiyesi iadeyi düşsün mü" kararıdır.

**K7 — Skoru kim görür?** → **Tavsiye: `sales` izni** (admin, yonetici, muhasebe, satis). `depo`/`rapor` görmez.
`/statement` ile aynı gerekçe (`auth.py:1305-1321`): müşteriye dair ticari yargı günlük `read` yüzeyi değildir.

**K8 — KVKK: skor İÇTİR.** → **Tavsiye (yazılı kural):**
1. Skor hiçbir müşteri yüzüne çıkmaz: ekstre (`statement.py`), ekstre PDF'i, POS fişi (`Pos.tsx` makbuz bloğu),
   e-posta, WhatsApp. Bugün `statement.py` ve `app/whatsapp` içinde `risk_limit` geçmiyor (grep → 0). 10-9a bu
   durumu bir NEGATİF testle çiviler.
2. `yaslandirma_verisi`ne skor EKLENMEZ, çünkü WhatsApp `alacak_yaslandirma` aracı o fonksiyonu çağırıyor
   (`reports.py:326-330`, `whatsapp/niyet.py:344,756`).
3. Karar otomatik değildir: W2'de satış kasiyerin onayıyla sürer. Otomatik ret (W3), KVKK md. 11/1-g
   ("münhasıran otomatik sistemler vasıtasıyla analiz… aleyhine bir sonuç") itirazını doğurabilir (hukuki yorum
   **DOĞRULANMADI**).
4. Skor saklanmaz (K3). Denetimde yalnız satış anındaki harf/puan anlık görüntüsü kalır. Bu bir işlem kaydıdır,
   profil değildir.

**K9 — Onay ve istisna kimde, ne kaydedilir?** → **Tavsiye:**
* W2 onayını `sales` taşıyan her rol verir; iz `pos.sale_created.details.risk_onayi` + kullanıcıdır (aktivite
  satırının `user_id`si).
* Limit istisnası bugünkü gibi `admin`/`yonetici`de kalır (`company_policies.py:18`).
* Skor OKUMASI denetlenmez (`ACTION_TYPES` 84 sabit). Okuma günlüğü istenirse +1 aksiyon tipi ve
  `test_activity_log_panel.py:219,282` + `activity_log.py` parmak izi (`test_tenant_scoping_guard.py:383`) oynar.

---

## 7. EN BÜYÜK DÖRT RİSK

1. **Üç bakiye, üç cevap.** Kapı, kart ve yaşlandırma aynı müşteri için 240 / 740 / 640 dedi (§1.6). Skor
   yaşlandırmayı kullanırsa kasiyerin ekranında "bakiye 740, vadesi geçmiş 640, limit kullanımı %24" yan yana
   durur. Açıklama kutusu her sayının kaynağını ADIYLA yazmazsa kullanıcı skora değil sisteme güvenini kaybeder.
   K6 bu farkın en tehlikeli yarısını (karşılıksız çek) kapatır.
2. **Yanlış pozitif satış kaybettirir.** Eşikler kalibre edilmedi (K1). Tohumda müşterilerin %15'i D çıktı.
   W3 (blok) bu hâlde açılırsa iyi müşteri kasada bekletilir. W2 + gölge mod bu yüzden önerildi.
3. **Eski düzende S2 bir modeldir.** Tahsis defteri kapalıyken (`config.py:216`, varsayılan) bağsız ödeme FIFO ile
   dağılır ve "geç kapandı" hükmü belge bazında gerçek değil, dağıtımın sonucudur (§1.3). Bayrağın canlıda açık
   olduğu firma sayısı **DOĞRULANMADI**.
4. **KVKK sızıntısı dolaylı yoldan gelir.** Risk skorun kendisinden çok, onu taşıyan paylaşılan fonksiyondadır:
   `yaslandirma_verisi` WhatsApp'a açık (K8-2), `cari_liste_satirlari` iki ucun ortak dikişi
   (`entity_detail.py:60-86`). Skor bu ikisine EKLENİRSE müşteri kanalına bir adım uzaklıkta olur. Kural: skor
   yalnız kendi ucunda ve POS'ta.

---

## EK — ÖLÇÜLEMEYENLER (DOĞRULANMADI)

* **Canlı veride harf dağılımı ve eşik kalibrasyonu.** Tohum gecikme, karşılıksız çek, vade farkı ve iade
  üretmiyor (hepsi 0 satır); P2/P3 elle eklendi.
* **Tek müşteri S1'in süzgeçli maliyeti** (< 15 ms tahmini): süzgeç yazılmadı. Tahmin, ölçülen tek-müşteri
  parçalarından (3,4 + 2,4 ms) türetildi.
* **PostgreSQL süreleri.** Bütün ölçümler SQLite'ta. PG konteyneri bu oturumda başlatılmadı.
* **Canlıda limitsiz (`risk_limit=0`) müşteri oranı** ve **tahsis defteri bayrağı açık firma sayısı.**
* **10-9-fix'in etkileyeceği müşteri sayısı** (karşılıksız çek / vade farkı borcu olan ve limite yakın müşteriler).
* **Servis faturalaması ve iş akışı dönüşümünün limit kapısından geçip geçmediği** (§2.2).
* **Zaman çizelgesinden türetilen S1 ile motor S1'inin eşitliği** (K5-b).
* **`cari_alan_envanteri.txt` deltası.** Tarayıcı BFS'i yeni uç için koşturulmadı.
* **Core envanteri artışları** (`~+2`): sorgular yazılmadı; aritmetik tahmin.
* **KVKK md. 11/1-g'nin W3'e uygulanıp uygulanmadığı:** hukuki yorum, mevzuat/avukat görüşü alınmadı.
* **İadenin cari bakiyeden neden düşmediği:** tasarım kararı mı kusur mu, kayıt bulunamadı. Yalnız davranış ölçüldü
  (§1.6).
