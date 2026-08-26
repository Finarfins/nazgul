# Servis Hatırlatma + Bildirimler — FAZ-0 TASARIM

**Durum:** Tasarım taslağı, kod yok. İki reviewer'a (mimari + repo-gerçekleme) sunulur.
**Kapsam:** Bildirim altyapısı (outbox), KVKK rıza yönetimi, servis/harman hatırlatma tetikleyicileri.
**Kapsam dışı (bilinçli):** Ödeme linki — Faz-C konusu, v1'de **hiç yok**. Gerçek SMS/WhatsApp sağlayıcı seçimi.

---

## 0. Yönetici özeti — en kritik bulgu

**Bildirim outbox'ı zaten var ve migrate edilmiş durumda.** Bu görev yeşil alan (greenfield) değil; mevcut, test edilmiş ama **hiç bağlanmamış** bir seam'in üzerine inşa.

| Bileşen | Durum | Kanıt |
|---|---|---|
| `notifications` outbox tablosu | **VAR** | [schema.py:21](backend/app/notifications/schema.py:21), migration [20260724_0022_notifications.py](backend/alembic/versions/20260724_0022_notifications.py) |
| Durum makinesi + CAS lease | **VAR** | [service.py:169](backend/app/notifications/service.py:169) `_claim_notification` |
| Idempotency (dedupe key) | **VAR** | [service.py:151](backend/app/notifications/service.py:151) `ON CONFLICT ... DO NOTHING RETURNING` |
| Sağlayıcı-bağımsız adapter | **VAR** | [provider.py:26](backend/app/notifications/provider.py:26) `NotificationProvider(ABC)` |
| Outbox listeleme + retry ucu | **VAR** | [routers/notifications.py:81](backend/app/routers/notifications.py:81) |
| **Üretimde `enqueue_notification` çağrısı** | **YOK — sıfır** | Yalnız testlerde: `test_v3_notifications.py`, `test_notifications_postgresql.py` |
| Rıza / KVKK (`consents`) | **YOK** | `grep -ri "consent\|rıza\|kvkk" backend/app` → hiçbir alan sonucu yok |
| Şablon tablosu | **YOK** | `template` yalnız `String(120)` bir ad kolonu ([schema.py:29](backend/app/notifications/schema.py:29)) |
| Zamanlama / retry backoff | **YOK** | `next_attempt_at` kolonu yok; retry **yalnız elle** tetikleniyor |
| Gönderim onayı / audit | **YOK** | `approved_by`, `created_by` kolonları yok |

**Sonuç:** F1'in işi outbox *yazmak* değil, mevcut outbox'ı **rızaya bağlamak, şablonlandırmak, onaya bağlamak ve ilk üreticiyi (producer) bağlamaktır.**

---

## 1. KEŞİF

### 1.1 Mevcut outbox — ne yapıyor?

`notifications` tablosu ([schema.py:21-68](backend/app/notifications/schema.py:21)):

```
id, company_id, type, channel, recipient, template, payload(TEXT/JSON),
dedupe_key, status, external_id, last_error, attempt_count,
last_attempt_at, locked_until, lock_token, created_at, updated_at
UNIQUE (company_id, dedupe_key)
INDEX (company_id, status), (company_id, locked_until)
```

Gerçek durum makinesi — **görev tanımındaki `SENDING` değil, `PROCESSING`**:

```
PENDING ──┐
FAILED  ──┼──> PROCESSING ──> SENT | DELIVERED   (terminal)
NONE    ──┘         │
                    ├──────> FAILED   (yeniden denenebilir)
                    └──────> NONE     (sağlayıcı yapılandırılmamış)
```

- Dispatch edilebilir kümesi: `{PENDING, FAILED, NONE}` — [service.py:24](backend/app/notifications/service.py:24)
- Terminal: `{SENT, DELIVERED}` — [service.py:25](backend/app/notifications/service.py:25)
- CAS: `UPDATE ... WHERE status IN (...) ` + `lock_token=uuid4`, `rowcount==1` kontrolü — [service.py:192-218](backend/app/notifications/service.py:192). Claim **provider çağrısından önce commit edilir**.
- Lease: `_LEASE_MINUTES = 5` ([service.py:26](backend/app/notifications/service.py:26)). Süresi dolmuş `PROCESSING` satırı **yalnızca** `provider.supports_idempotency` ise geri alınabilir ([service.py:249](backend/app/notifications/service.py:249)) — doğru ve korunmalı bir fail-closed kararı.
- Teslim semantiği açıkça **at-least-once**, dokümante edilmiş: [docs/notification-delivery-semantics.md:3](docs/notification-delivery-semantics.md:3).
- Hata sızdırma koruması: ham provider exception metni asla `last_error`'a yazılmaz; sınıf adına göre AUTH/NETWORK/VALIDATION/UNKNOWN sınıflandırması ([service.py:68-74](backend/app/notifications/service.py:68)).
- `enqueue_notification` **çağıranın transaction'ını kullanır, commit etmez** ([service.py:111-117](backend/app/notifications/service.py:111)) — domain rollback olursa bildirim de yazılmaz. Bu, e-Fatura için de doğru olan davranıştır; korunmalı.

**Eksikler (F1'in listesi):** retry backoff/zamanlama kolonu yok, `created_by`/`approved_by` yok, toplu gönderim ucu yok, worker/scheduler yok, şablon render yok, rıza kontrolü yok.

### 1.2 Sağlayıcı seam

[provider.py](backend/app/notifications/provider.py): `NotificationProvider` ABC + `NoOpNotificationProvider` (varsayılan, ağa çıkmaz, `NONE` döner) + `TwilioNotificationProvider` / `WhatsAppNotificationProvider` (yalnız wiring stub, `NotImplementedError`). Factory bilinmeyen adı **güvenli tarafa, NoOp'a** düşürür ([provider.py:71-77](backend/app/notifications/provider.py:71)). Ayar: `settings.notification_provider = "noop"` ([config.py:79](backend/app/config.py:79)).

> Görevdeki "MOCK sağlayıcı" isteği fiilen karşılanmış durumda: `NoOpNotificationProvider`. F1'de bunun üstüne, test/demo için gönderimi **kaydeden** (`SENT` dönen, sahte `external_id` üreten) bir `MockNotificationProvider` eklenmesi öneriliyor — NoOp'un `NONE` dönmesi "gönderildi" akışını test etmeyi zorlaştırıyor.

### 1.3 Karıştırılmaması gereken ikinci "bildirim"

`GET /api/companies/notifications` ([routers/companies.py:147-199](backend/app/routers/companies.py:147)) — bu **outbox değil**. Anlık hesaplanan, kalıcı olmayan bir uygulama-içi uyarı şeridi: kritik stok, vadesi geçmiş satış belgesi, bugünkü tahsilat sayısı. Frontend'de `AppShell` çekiyor ([AppShell.tsx:22](frontend/src/components/AppShell.tsx:22)).

İki gotcha:
- `orders.due_date` **VARCHAR ISO string**, `date` değil. PostgreSQL'de varchar/date karşılaştırma operatörü yok; kod bilinçli olarak bağlı ISO string'e karşı karşılaştırıyor ([companies.py:150-155](backend/app/routers/companies.py:150)). **Vade tetikleyicisi yazan herkes bu tuzağa düşecektir** — `fix/notifications-overdue-varchar-date-comparison` dalı bunun izi.
- Bu uç dış gönderim yapmaz, PII taşımaz. Outbox ile **birleştirilmemeli**; ilişki "aynı olaydan iki farklı görünüm" olarak kalmalı.

### 1.4 Müşteri iletişim alanları

`customers` / `suppliers`: `phone String(60)`, `email String(200)` ([core_schema.py:29-30](backend/app/core_schema.py:29), [core_schema.py:48](backend/app/core_schema.py:48)).

- **Doğrulama YOK.** Format normalizasyonu, E.164 dönüşümü, benzersizlik, "doğrulanmış mı" bayrağı — hiçbiri yok.
- Serbest metin: `"0532 111 22 33"`, `"532-111-22-33"`, `"bilinmiyor"` hepsi kabul edilir.
- **Tasarım sonucu:** F1'de bir `normalize_msisdn()` + fail-closed doğrulama şart. Geçersiz numara → gönderim kuyruğa **hiç girmez**, kullanıcıya "numara geçersiz" denir.

### 1.5 Hatırlatmaya temel olacak alanlar

| Kaynak | Alan | Kanıt |
|---|---|---|
| İş emri | `work_orders.scheduled_date` (DateTime tz) + `SCHEDULED` durumu | migration [20260727_0029_servis_v2_faz1.py:85](backend/alembic/versions/20260727_0029_servis_v2_faz1.py:85) |
| İş emri (şema) | `scheduled_date: datetime \| None`, `SCHEDULED` ise zorunlu | [work_order_schemas.py:46](backend/app/work_order_schemas.py:46), [:109](backend/app/work_order_schemas.py:109) |
| Makine | `working_hours Numeric(12,2)` | [machines.py:41](backend/app/machines.py:41) |
| Harman vade | `resolve_harvest_due_date`, takvim `due_date` | [harvest_scheduling.py:232](backend/app/harvest_scheduling.py:232), [:99](backend/app/harvest_scheduling.py:99) |
| Vade gecikme | `due_date + grace_days`, faiz başlangıcı | [late_fee_engine.py:58](backend/app/late_fee_engine.py:58), [:72](backend/app/late_fee_engine.py:72) |

- **`maintenance_plans` tablosu YOK** (`grep -rn "maintenance_plan" backend` → boş). Periyodik bakım F5'in konusu; v1 tetikleyicisi **yalnız `scheduled_date`** üzerinden çalışmalı. "Son servis tarihi" için de ayrı bir kolon yok — gerekirse `work_orders` üzerinden türetilir (`MAX(completed_at)` benzeri), yeni kolon açmadan.
- `working_hours` var ama **saat-bazlı bakım eşiği (örn. "her 250 saatte bir")** yok. v1 kapsamı dışı, F5'e bırakılmalı.

### 1.6 Aktivite paneli (#169) — yeniden kullanılabilirler

`feat/activity-log-panel` dalı, **pr-170'e merge edilmemiş** (ayrı worktree: `.worktrees/activity-log-panel`). [activity_log.py](.worktrees/activity-log-panel/backend/app/activity_log.py) dört sözleşme tanımlıyor ve **dördü de bu modülde aynen benimsenmeli**:

1. **Değişmezlik** — append-only, UPDATE/DELETE yok.
2. **Aynı transaction** — `log_activity` çağıranın Session'ında, commit etmez; işlem düşerse log da düşer. (`enqueue_notification` zaten birebir aynı deseni izliyor.)
3. **Ham SQL + literal `company_id = :cid`** her sorguda.
4. **Decimal para** — `format_money_tr` ile, `float` yok.

Ayrıca **kapalı olay kataloğu** deseni ([activity_log.py:56+](.worktrees/activity-log-panel/backend/app/activity_log.py:56)): `ACTION_TYPES` sözlüğü dışında bir `action_type` `ValueError` ile reddedilir. Bildirim olayları (`notification.sent` / `notification.failed`) bu kataloğa **eklenmeli**, yeni bir paralel katalog kurulmamalı.

> ⚠️ **Bağımlılık riski:** #169 merge edilmeden `notification.sent` olayını kataloğa ekleyemeyiz. Fazlama bunu hesaba katmalı (bkz. §7).

### 1.7 RBAC mevcut durumu

`ROLE_PERMISSIONS` ([auth.py:101-115](backend/app/auth.py:101)): `admin: {"*"}`, `yonetici`, `muhasebe`, `satis`, `depo`, `rapor`. `has_permission` basit küme kontrolü ([auth.py:706](backend/app/auth.py:706)). `ROLE_RANK` yetki gücünü ayrı tutuyor ([auth.py:120](backend/app/auth.py:120)).

Mevcut bildirim ucu **`"users"` yetkisine** bakıyor ([routers/notifications.py:55-58](backend/app/routers/notifications.py:55)) — yani kullanıcı yönetebilen herkes bildirim retry edebiliyor. Bu **fazla kaba**; §6'da ayrıştırılıyor.

Ayrıca kodda not düşülmüş: *"the future `service` role must also receive it when introduced"* ([auth.py:104-105](backend/app/auth.py:104)) — servis rolü planlanmış ama yok.

---

## 2. TASARIM — Outbox (genel amaçlı, e-Fatura dahil)

### 2.1 İlke: mevcut tabloyu genişlet, ikinci outbox kurma

`notification_outbox` adında **yeni bir tablo açılmayacak.** Mevcut `notifications` tablosu genişletilecek. Gerekçe: tablo zaten migrate edilmiş, PostgreSQL ve SQLite'ta test edilmiş, CAS/dedupe semantiği kanıtlanmış. İkinci bir outbox, e-Fatura ve bildirimi bölerek tam da kaçınmak istediğimiz durumu yaratır.

> **Reviewer'a açık soru:** Görev tanımı `notification_outbox` adını veriyor. Mevcut tablo `notifications`. Ad değişikliği expand-only ilkesiyle çelişir (rename = contract). **Öneri: ad `notifications` kalsın**, dokümantasyonda "outbox" olarak anılsın. Onay bekliyor.

### 2.2 Expand-only migration planı

**Yeni kolonlar (hepsi nullable veya server_default'lu — expand-only):**

```
notifications ADD COLUMN:
  next_attempt_at  TIMESTAMPTZ NULL   -- retry backoff zamanı
  scheduled_for    TIMESTAMPTZ NULL   -- ileri tarihli gönderim
  created_by       INTEGER     NULL   -- kuyruğa ekleyen kullanıcı
  approved_by      INTEGER     NULL   -- gönderimi onaylayan kullanıcı
  approved_at      TIMESTAMPTZ NULL
  approval_mode    VARCHAR(20) NULL   -- MANUAL | RULE | SYSTEM
  dispatch_armed   BOOLEAN NOT NULL DEFAULT FALSE  -- §2.9
  disarmed_at      TIMESTAMPTZ NULL   -- disarm zamanı; arşiv saklama sayacı (§2.9)
  rule_id          INTEGER     NULL   -- kaynak kural; disarm bunun üzerinden (§2.9)
  message_class    VARCHAR(24) NULL   -- SERVICE_TRANSACTIONAL | COMMERCIAL (§4.0)
  template_id      INTEGER     NULL   -- notification_templates FK (mantıksal)
  template_version INTEGER     NULL   -- hash kapsamında (§2.5)
  content_hash     CHAR(64)    NULL   -- kanonik render edilmiş payload özeti (§2.5)
  max_attempts     INTEGER NOT NULL DEFAULT 5
  last_error_code  VARCHAR(20) NULL   -- AUTH | NETWORK | VALIDATION | UNKNOWN

  -- Rıza AUDIT snapshot'ı (§3.3). Karar verici DEĞİL, yalnız kanıt.
  consent_id             INTEGER     NULL
  consent_version        INTEGER     NULL
  consent_checked_at     TIMESTAMPTZ NULL
  consent_decision       VARCHAR(20) NULL  -- ALLOWED | BLOCKED
  consent_decision_reason VARCHAR(60) NULL -- NO_RECORD | REVOKED | RECIPIENT_CHANGED | IYS_UNAVAILABLE | ...

  -- İYS (yalnız COMMERCIAL, §4.0)
  iys_status         VARCHAR(20) NULL  -- ONAY | RET | BILINMIYOR
  iys_checked_at     TIMESTAMPTZ NULL
  iys_reference      VARCHAR(120) NULL -- İYS kayıt kimliği
  iys_source         VARCHAR(20) NULL  -- LIVE | CACHE
  compliance_attempt_count INTEGER NOT NULL DEFAULT 0  -- İYS sorgu denemesi (§4.0)

INDEX ix_notifications_dispatch
      (company_id, status, dispatch_armed, next_attempt_at)   -- §2.4 lease sorgusu
INDEX ix_notifications_company_scheduled (company_id, scheduled_for)
INDEX ix_notifications_company_rule (company_id, rule_id, status)  -- §2.9 disarm
```

> **Terminoloji notu (rev1 → rev2):** rev1'de bu bayrak `is_enabled` adıyla geçiyordu ve §2.4 yükleminde `is_enabled = TRUE` olarak yazılmıştı. Kural tablosundaki `notification_rules.is_enabled` ile karışmaması için satır üzerindeki bayrak **`dispatch_armed`** olarak yeniden adlandırılmıştır. Anlam ve yüklemdeki rolü **aynıdır**; ikisi farklı şeylerdir ve §2.9 ilişkilerini tanımlar.

`notification_consents` tablosuna da `version INTEGER NOT NULL DEFAULT 1` eklenir; her GRANT/REVOKE versiyonu artırır (§3.1).

**`dispatch_armed` neden `notifications` üzerinde?** §2.4'teki lease yüklemi (predicate) tek tablo üzerinde, JOIN'siz ve atomik olmalıdır. Kural tablosuna JOIN atmak, kuralın gönderim anında kapatılması ile satırın kilitlenmesi arasında ikinci bir yarış açardı. Bunun yerine bayrak satıra denormalize edilir: manuel gönderimde **onay anında**, kural kaynaklı gönderimde **kural tetiklenirken** `TRUE` yazılır. Varsayılan `FALSE` — bir satır açıkça silahlanmadan asla dispatch edilmez. Denormalizasyonun bedeli, kural kapatıldığında kuyruktaki satırların da güncellenmesi zorunluluğudur; bu §2.9'da normatif olarak tanımlanır.

Mevcut `template VARCHAR(120)` kolonu **bırakılıyor** (silinmiyor) — şablon adının denormalize kopyası olarak kalır, gönderim anındaki şablonu tarihsel olarak korur.

**Yeni tablolar:** `notification_consents`, `notification_consent_events`, `notification_templates`, `notification_rules` (§3, §4) ve `notifications_archive` (§2.9 — `notifications` ile aynı şekil + `archived_at` / `archived_by` / `archive_reason`).

Tüm migration'lar tek revision, `down_revision = "20260728_0030"` (mevcut head — [20260728_0030_work_order_labor_lines.py:25](backend/alembic/versions/20260728_0030_work_order_labor_lines.py:25)). Downgrade **yazılacak** (kolon drop + tablo drop), repo kuralı gereği tersinir olmalı.

### 2.3 Durum makinesi — mevcut korunur, `SENDING` eklenmez

Görev tanımı `PENDING→SENDING→SENT/FAILED` diyor. Mevcut kod `PROCESSING` kullanıyor. **Öneri: `PROCESSING` korunsun.** Yeniden adlandırma, migrate edilmiş satırların data migration'ını ve test/PG paritesinin yeniden doğrulanmasını gerektirir — sıfır fonksiyonel kazanç karşılığında. Bu bilinçli bir sapmadır ve reviewer onayına sunulur.

Eklenen durumlar: **`AWAITING_APPROVAL`**, **`RETRY_SCHEDULED`**, **`CANCELLED`** (terminal), **`REJECTED`** (terminal — rıza/İYS/içerik kapısına takılan satır).

`_TERMINAL_STATUSES` → `{SENT, DELIVERED, CANCELLED, REJECTED}`.

#### Kapalı geçiş tablosu

Aşağıdaki tablo **tam kümedir**. Burada yazmayan hiçbir geçiş yoktur; kod bunu açık bir `_ALLOWED_TRANSITIONS` sözlüğüyle uygular ve küme dışı geçiş `ValueError` ile reddedilir (#169'un kapalı katalog deseni).

| # | Kaynak | Hedef | Tetikleyen | CAS koşulu |
|---|---|---|---|---|
| 1 | *(yok)* | `AWAITING_APPROVAL` | `enqueue` (onaysız yol) | — |
| 2 | *(yok)* | `PENDING` | `enqueue` (kural, silahlı) | — |
| 3 | `AWAITING_APPROVAL` | `PENDING` | onay ucu | `status='AWAITING_APPROVAL' AND content_hash=:seen_hash` |
| 4 | `AWAITING_APPROVAL` | `CANCELLED` | iptal ucu | `status='AWAITING_APPROVAL'` |
| 5 | `AWAITING_APPROVAL` | `AWAITING_APPROVAL` | içerik/alıcı değişimi | onayı geçersizleştirir (§2.5) |
| 6 | `PENDING` | `AWAITING_APPROVAL` | içerik/alıcı değişimi | onayı geçersizleştirir (§2.5) |
| 7 | `PENDING` | `PROCESSING` | dispatch lease | §2.4 yüklemi + `lock_token` |
| 8 | `RETRY_SCHEDULED` | `PROCESSING` | dispatch lease | §2.4 yüklemi + `lock_token` |
| 9 | `PROCESSING` | `SENT` / `DELIVERED` | provider başarılı | `lock_token=:tok AND status='PROCESSING'` |
| 10 | `PROCESSING` | `RETRY_SCHEDULED` | provider hatası, `attempt_count < max_attempts` | `lock_token=:tok AND status='PROCESSING'` |
| 11 | `PROCESSING` | `FAILED` | provider hatası, `attempt_count >= max_attempts` | `lock_token=:tok AND status='PROCESSING'` |
| 12 | `PROCESSING` | `NONE` | sağlayıcı yapılandırılmamış | `lock_token=:tok AND status='PROCESSING'` |
| 13 | `PROCESSING` | `REJECTED` | son rıza/İYS okuması engelledi (§3.3) | `lock_token=:tok AND status='PROCESSING'` |
| 14 | `PENDING` / `RETRY_SCHEDULED` | `CANCELLED` | iptal ucu | `status IN (...)` — `PROCESSING` iptal **edilemez** |
| 15 | `FAILED` / `NONE` | `RETRY_SCHEDULED` | elle retry ucu (§2.6) | `status IN ('FAILED','NONE') AND approved_at IS NOT NULL` |
| 15b | `RETRY_SCHEDULED` | `RETRY_SCHEDULED` | elle retry ucu — "şimdi dene" (§2.6) | `status='RETRY_SCHEDULED' AND approved_at IS NOT NULL` |

> **Geçiş 15b (rev5, normatif):** backoff'u bekleyen bir satır için operatörün
> "şimdi dene" işlemidir. Durum değişmez; tek etkisi `next_attempt_at`'in öne
> (`:now`) çekilmesidir. Diğer tüm kapılar AYNEN geçerli kalır: onay korunur
> (üretilmez/taşınmaz), `dispatch_armed = TRUE` şarttır, rıza gönderim anında
> **yeniden okunur** (§3.3), `content_hash` dispatch öncesi **yeniden
> doğrulanır** (§2.5 adım 7). Yalnız `notifications_dispatch` yetkisi
> tetikleyebilir.
| 16 | `PROCESSING` | `COMPLIANCE_CHECK_UNAVAILABLE` | İYS erişilemez (§4.0) | `lock_token=:tok AND status='PROCESSING'` |
| 17 | `COMPLIANCE_CHECK_UNAVAILABLE` | `RETRY_SCHEDULED` | devre-kesici kapalı, backoff doldu | `compliance_attempt_count < :cb_max` |
| 18 | `COMPLIANCE_CHECK_UNAVAILABLE` | `REJECTED` | devre-kesici açıldı / üst sınır aşıldı | `compliance_attempt_count >= :cb_max` |
| 19 | `PENDING` / `RETRY_SCHEDULED` | *(aynı durum, `dispatch_armed=FALSE`)* | kural disarm (§2.9) | `rule_id=:rid AND company_id=:cid AND status IN (...)` |

`AWAITING_APPROVAL`, `CANCELLED`, `REJECTED`, `SENT`, `DELIVERED`, `COMPLIANCE_CHECK_UNAVAILABLE` **hiçbir koşulda** dispatch edilmez — bu, UI filtresi değil §2.4'teki SQL yükleminin doğrudan sonucudur.

`_TERMINAL_STATUSES` güncel hâli: `{SENT, DELIVERED, CANCELLED, REJECTED}`. `COMPLIANCE_CHECK_UNAVAILABLE` **terminal değildir** — geçici bir bekleme durumudur ve geçiş 17/18 ile çözülür.

> **Geçiş 19 neden durum değiştirmiyor:** kural kapatıldığında satırın durumu korunur, yalnız silahı alınır. Böylece "neden gönderilmedi" sorusunun cevabı (`PENDING` ama `dispatch_armed=FALSE`) denetimde okunabilir kalır; satırı `CANCELLED`'a düşürmek, kural yeniden açıldığında yeniden onaydan geçmesi gereken bir satırı terminal yapardı.

> **Geçiş 13 neden var:** rıza kontrolü provider çağrısından hemen önce, satır zaten `PROCESSING` iken yapılır (§3.3). Engel çıkarsa satır `REJECTED`'a düşer ve provider **hiç çağrılmaz**. `PENDING`'e geri döndürülmez — geri döndürmek, iptal edilmiş bir rızanın satırı sonsuza kadar kuyrukta tutmasına yol açardı.

### 2.4 Dispatch lease yüklemi (DB seviyesinde kapalı)

Worker'ın satır kiralamak için kullandığı `UPDATE ... WHERE` yüklemi **tam olarak** şudur ve gevşetilemez:

```sql
UPDATE notifications
   SET status = 'PROCESSING',
       attempt_count = attempt_count + 1,
       last_attempt_at = :now,
       locked_until = :lease_until,
       lock_token = :tok,
       updated_at = :now
 WHERE company_id      = :cid            -- tenant literal
   AND id              = :nid
   AND status          IN ('PENDING', 'RETRY_SCHEDULED')
   AND approved_at     IS NOT NULL
   AND approved_by     IS NOT NULL
   AND next_attempt_at <= :now
   AND dispatch_armed  = TRUE          -- rev1'deki "is_enabled" terimi (§2.2 notu)
```

Beş koşulun tamamı **veritabanı seviyesindedir**. `AWAITING_APPROVAL` bu yüklemi hiçbir zaman geçemez; onay eksikse `approved_at IS NOT NULL` düşer; kural kapatılmışsa `dispatch_armed` düşer (§2.9); backoff dolmamışsa `next_attempt_at` düşer. Uygulama katmanında ya da arayüzde ek bir filtreye **güvenilmez** — arayüz filtresi yalnız görüntüleme içindir.

`rowcount == 1` değilse satır alınmamıştır; mevcut koddaki davranış korunur ([service.py:208](backend/app/notifications/service.py:208)).

> `enqueue` sırasında `next_attempt_at`, onaylı yolda `:now`, onaysız yolda `NULL` yazılır. `NULL <= :now` her iki veritabanında da `NULL`/false döndüğü için onaysız satır ayrıca bu koşuldan da elenir — çift emniyet, tek doğruluk kaynağı değil.

### 2.5 Onayın geçersizleşmesi

> **İçerik veya alıcı değişirse önceki onay GEÇERSİZDİR.**

> **Hash, şablon gövdesinden DEĞİL, kanonik render edilmiş nihai payload'dan üretilir.** Şablon metnini hash'lemek yetersizdir: aynı şablon farklı müşteriye, farklı tutara, farklı vadeye render edilir ve bunların hepsi **farklı mesajlardır**.

#### Zorunlu sıra — 7 adım

Bu sıra normatiftir; adımlar atlanamaz ve yeri değiştirilemez.

| # | Adım | Not |
|---|---|---|
| 1 | **Değişken doğrulama** | Allow-list üyeliği, tip ve uzunluk sınırı (§4.2). Başarısızsa hata — render'a geçilmez. |
| 2 | **Render** | Değişkenler şablona yerleştirilir. |
| 3 | **Normalizasyon + URL denetimi** | §5.5 zinciri (NFKC → kontrol karakteri temizliği → decode → case-fold) ve içerik kapısı. Red varsa akış **burada durur**. |
| 4 | **Gösterilecek nihai payload'ın kanonikleştirilmesi** | Alan sırası sabit, boşluk daraltılmış, kanonik JSON (`sort_keys`, `ensure_ascii=False`, sabit ayraç) — mevcut `build_notification_payload` deseniyle uyumlu ([service.py:79](backend/app/notifications/service.py:79)). |
| 5 | **Hash BU payload'dan** | `content_hash = SHA256(kanonik_payload)`. Şablon gövdesinden değil. |
| 6 | **Onay CAS'i** | Onay ucu kullanıcının **GÖRDÜĞÜ** hash'i (`:seen_hash`) parametre alır ve CAS koşuluna koyar (geçiş 3). Uyuşmazlık → **409**. |
| 7 | **Dispatch öncesi yeniden doğrulama** | Provider çağrısından önce, **saklanan payload'ın hash'i yeniden hesaplanır** ve `content_hash` ile karşılaştırılır. Uyuşmazlık → gönderim yok, satır `AWAITING_APPROVAL`'a döner. |

Adım 7, adım 6'nın tekrarı değildir: 6 "kullanıcı ne onayladı", 7 "şu anda gönderilmek üzere olan şey hâlâ o mu" sorusunu yanıtlar. Onay ile dispatch arasında geçen sürede saklanan payload bozulmuş veya değiştirilmişse, gönderim sessizce yapılmaz.

#### Hash kapsamı

Aşağıdaki alanların **tamamı** hash'e girer:

```
message_class, channel, recipient,
template_id, template_version,
kanonik subject, kanonik body,
ek/referans metadata (iş emri no, belge no, tutar referansı vb.)
```

> **Açık örnek:** Aynı `harvest.due_soon` şablonundan üretilen
> *"…1.000,00 TL tutarındaki ödemenizin…"* ile
> *"…10.000,00 TL tutarındaki ödemenizin…"*
> **FARKLI `content_hash`'lerdir.** İlkini onaylamak ikincisini onaylamaz.

Eski onayı geçersizleyen değişiklikler — hepsi hash'e girdiği için mekanik olarak yakalanır:

- **recipient** değişimi (numara düzeltmesi dahil — ayrıca §3'e göre yeni rıza da gerektirir)
- **tutar** değişimi
- **vade tarihi** değişimi
- **makine / iş emri** referansı değişimi
- **şablon sürümü** (`template_version`) değişimi
- **mesaj sınıfı** (`message_class`) değişimi

Değişim tespit edilirse satır `approved_at = NULL`, `approved_by = NULL`, `dispatch_armed = FALSE` ile `AWAITING_APPROVAL`'a döner (geçiş 5/6).

**Retry ve yeniden render:** Retry yolunda yeniden render **kendiliğinden yapılmaz**. Retry, saklanan aynı payload'ı gönderir (ve adım 7'den geçer). İçerik yenilenecekse satır onaya geri düşer. Bu, "retry sırasında sessizce değişmiş metin gitmesi"ni imkânsız kılar.

### 2.6 Retry ucu — sınırlar

Elle retry ucu ([routers/notifications.py:100](backend/app/routers/notifications.py:100)) yalnız **terminal olmayan başarısız** durumları kabul eder:

| Durum | Retry |
|---|---|
| `FAILED`, `NONE` | ✅ kabul |
| `RETRY_SCHEDULED` | ✅ kabul — "şimdi dene": aynı kapılar (onay + silahlı + rıza engeli yok), tek etki `next_attempt_at` öne çekilir; yalnız `notifications_dispatch` (geçiş 15b) |
| `AWAITING_APPROVAL` | ❌ 409 — önce onay gerekir |
| `CANCELLED`, `REJECTED` | ❌ 409 — terminal |
| `SENT`, `DELIVERED` | ❌ 409 — terminal (mevcut davranış, korunur) |
| rıza/İYS ile bastırılmış (`consent_decision='BLOCKED'`) | ❌ 409 — retry rızayı aşamaz |
| `PROCESSING` | ❌ 409 — lease sahibi var (mevcut `NotificationBusyError`) |

Ek pazarlıksız kurallar:
- **Retry onay üretmez ve onay taşımaz.** `approved_by`/`approved_at` alanlarına dokunmaz. Onaysız bir satır retry ile gönderilebilir hâle **gelemez** (geçiş 15 zaten `approved_at IS NOT NULL` şartlı).
- Retry, satırı `RETRY_SCHEDULED`'a alır ve `next_attempt_at`'i **hemen** (`:now`) yazar; dispatch yine §2.4 yükleminden geçer. Retry ucu provider'ı doğrudan çağırmaz.
- `attempt_count`, `next_attempt_at`, `last_error_code` **tek `UPDATE` içinde atomik** yazılır; ayrı ifadelere bölünmez.

### 2.7 Retry + backoff

- Üstel backoff: `delay = min(2^attempt_count * 60s, 6 saat)` — **üst sınır zorunlu**, sınırsız büyüme yok.
- Jitter: ±%20, sunucu tarafında `random`/`secrets` ile. Deterministik test için seed enjekte edilebilir olmalı.
- `next_attempt_at = now + delay`, `RETRY_SCHEDULED` yazılırken **aynı** `UPDATE` içinde set edilir (§2.6).
- `attempt_count >= max_attempts` → `FAILED`, `next_attempt_at = NULL` → satır **ölü**; yalnız elle retry ile canlanır.
- Worker (F2): dış cron/systemd timer'dan çağrılan yönetim komutu. **Uygulama içi background thread önerilmiyor** — çok işçili kurulumda gereksiz lease çekişmesi.

### 2.8 Idempotency ve çift gönderim modeli

Mevcut iki katman korunur ve yeterlidir:
1. **Üretici tarafı:** `dedupe_key`, `UNIQUE(company_id, dedupe_key)` + `ON CONFLICT DO NOTHING RETURNING` ([service.py:140-166](backend/app/notifications/service.py:140)). Aynı domain olayı ikinci kez tetiklenirse orijinal satır id'si döner.
2. **Sağlayıcı tarafı:** `provider_idempotency_key = f"notification:{company_id}:{notification_id}"` ([service.py:256](backend/app/notifications/service.py:256)), adapter'a `Idempotency-Key` başlığı olarak geçmesi sözleşmede yazılı ([docs/notification-delivery-semantics.md:11](docs/notification-delivery-semantics.md:11)).

**Dedupe key konvansiyonu (v1):**
```
service.reminder:{work_order_id}:{scheduled_date_iso}:{offset_days}
harvest.due_soon:{order_id}:{due_date_iso}:{offset_days}
harvest.overdue:{order_id}:{due_date_iso}:{offset_days}
```
`scheduled_date` değişirse key değişir → yeni hatırlatma çıkar, eskisi kuyrukta kalırsa iptal edilir. Bu bilinçli: tarih değişimi yeni bir olaydır.

**Çift gönderim modeli — açıkça yazılı sınır:**

| Katman | Neyi garanti eder | Neyi ETMEZ |
|---|---|---|
| `dedupe_key` | Aynı domain olayı iki outbox satırı üretemez | Tek satırın iki kez gönderilmesini engellemez |
| Lease + `lock_token` CAS | İki worker aynı satırı aynı anda gönderemez | Gönderim sonrası ölen worker'ın tekrarını engellemez |
| `provider_idempotency_key` | Sağlayıcı, ambigü retry'ı **kendi tarafında** tekilleştirebilir | Yalnız sağlayıcı gerçekten destekliyorsa geçerlidir |

`provider_idempotency_key = f"notification:{company_id}:{notification_id}"` — **değişmez outbox kimliğinden** türer, `attempt_count`'a bağlı değildir; bu yüzden retry'lar arasında **sabit kalır** ve sağlayıcı tarafında tekilleştirme mümkün olur ([service.py:256](backend/app/notifications/service.py:256)).

Kritik sonuç ve pazarlıksız kural:
- Adapter `supports_idempotency = True` **ancak** anahtarı gerçekten `Idempotency-Key` başlığı olarak gönderiyorsa beyan edilebilir ([docs/notification-delivery-semantics.md:11](docs/notification-delivery-semantics.md:11)).
- Süresi dolmuş `PROCESSING` satırının geri alınması **yalnız** `supports_idempotency = True` iken yapılır ([service.py:249](backend/app/notifications/service.py:249)). Sağlayıcı tekilleştirmiyorsa süresi dolmuş lease **elle müdahale** bekler — otomatik geri alma çift SMS demektir.
- Sistem genel olarak **at-least-once**'tır ve exactly-once **iddia etmez** ([docs/notification-delivery-semantics.md:3](docs/notification-delivery-semantics.md:3)). Bu, seam'in bilinçli sınırıdır.

**`NoOpNotificationProvider` asla `SENT` yazmaz.** `NONE` döner ([provider.py:40-44](backend/app/notifications/provider.py:40)) ve bu davranış korunur — yapılandırılmamış bir sistemin "gönderildi" raporlaması, denetim izini yalan söyler hâle getirir. Gönderim akışını test etmek için `SENT` dönen ayrı bir `MockNotificationProvider` kullanılır (§1.2) ve mock, `settings.notification_provider` ile **açıkça** seçilmeden devreye girmez.

### 2.9 `dispatch_armed` yaşam döngüsü — fail-closed disarm politikası

`notification_rules.is_enabled` (kural seviyesi) ile `notifications.dispatch_armed` (satır seviyesi) **iki farklı şeydir**. Denormalizasyon (§2.2) lease yüklemini JOIN'siz tutar, ancak karşılığında kural kapatıldığında kuyruktaki satırların da güncellenmesi **zorunludur**. Bu bölüm o zorunluluğu normatif olarak tanımlar.

#### Disarm — kural kapatıldığında

> **Bir kural `is_enabled = FALSE` yapıldığında, AYNI TRANSACTION içinde, o kurala bağlı ve henüz başlamamış (`PENDING` / `RETRY_SCHEDULED`) satırların tamamı atomik olarak `dispatch_armed = FALSE` yapılır.**

```sql
-- Kuralı kapatan işlemin AYNI transaction'ı içinde, tek ifade:
UPDATE notifications
   SET dispatch_armed = FALSE,
       updated_at     = :now
 WHERE company_id = :cid              -- tenant literal, pazarlıksız
   AND rule_id    = :rid
   AND status     IN ('PENDING', 'RETRY_SCHEDULED')
```

- **Aynı transaction:** kural kapatma ile disarm arasında commit edilmiş bir ara durum **yoktur**. İkisi birlikte başarılı olur ya da birlikte geri alınır. Ayrı transaction, tam da kapatma anında worker'ın satır kiralaması için bir pencere açardı.
- **Tenant-safe:** disarm `rule_id` **ve** `company_id` ile birlikte yapılır. Yalnız `rule_id` ile yapılması, kiracı sınırını aşan bir yazma riski taşır ve kabul edilmez.
- **`PROCESSING` satırlara DOKUNULMAZ.** Bunlar için provider çağrısı zaten başlamış olabilir; §3.3'teki rıza yarışı sınırı burada aynen geçerlidir: başlamış bir dış işlem geri alınamaz. Disarm, başlamamış işi durdurma mekanizmasıdır; başlamış işi iptal etme mekanizması **değildir**.
- **`AWAITING_APPROVAL` satırlar zaten silahsızdır** (`dispatch_armed = FALSE`); yükleme dahil edilmelerine gerek yoktur.

#### Re-arm — kural yeniden açıldığında

> **Kuralı yeniden etkinleştirmek, disarm edilmiş eski satırları KENDİLİĞİNDEN yeniden silahlandırmaz.**

`is_enabled = TRUE` yapmak yalnız **bundan sonra** üretilecek satırları etkiler. Kuyrukta bekleyen eski satırlar için **açık yeniden-onay** gerekir: satır `AWAITING_APPROVAL`'a alınır ve §2.5'teki onay akışından (güncel `content_hash` ile) yeniden geçer.

Gerekçe: kural bir sebeple kapatılmıştır. Kapalı kaldığı süre boyunca vade geçmiş, randevu değişmiş, müşteri rızasını çekmiş olabilir. Otomatik re-arm, aradaki değişikliklerden habersiz bayat mesajların topluca gitmesi demektir — tam olarak §5'teki "otomatik gönderim yok" kuralının ihlali.

#### Audit ve görünürlük

Her disarm/re-arm işlemi kalıcı olarak kaydedilir:

| Alan | İçerik |
|---|---|
| actor | İşlemi yapan kullanıcı (`user_id`) |
| zaman | `created_at` |
| neden | Kullanıcının girdiği serbest metin gerekçe (**zorunlu**) |
| etkilenen satır sayısı | `UPDATE`'in `rowcount` değeri |
| kural | `rule_id`, kural kodu |

Aktivite kataloğuna karşılık gelen olaylar: `notification.rule_disabled`, `notification.rule_enabled`, `notification.rows_disarmed` (§6).

**UI gereksinimi:** kural kapatıldığında kullanıcıya yalnız "kural kapatıldı" denmez; **"Kuyruktakiler de durduruldu (N satır)"** bilgisi `rowcount` değerinden gösterilir. Kullanıcının, kapatma işleminin kuyruktaki bekleyen mesajları da etkilediğini görmesi gerekir — aksi hâlde "kapattım ama yine de gitti mi?" belirsizliği kalır.

Yetki: disarm/re-arm `notifications_admin` gerektirir (§6).

#### Disarmed satır yaşam döngüsü (normatif)

Disarm edilmiş bir satır ne kuyrukta canlıdır ne de ölüdür. Bu ara durumun tanımsız bırakılması iki somut arızaya yol açar: satır "bekleyenler" sayımını şişirerek yanlış operasyonel tablo çizer, ya da bir gün kendiliğinden gönderilir. Aşağıdaki altı kural bunu kapatır.

**1. Görünürlük — ayrı filtre**

> Disarmed satırlar normal **"Bekleyenler"** görünümünden **ÇIKAR.**

Ayrı bir **"Durdurulanlar"** filtresinde listelenir ve her satırda şunlar gösterilir:

| Sütun | Kaynak |
|---|---|
| Kural | `rule_id` → kural kodu/adı |
| Durdurma zamanı | disarm audit kaydının `created_at` değeri |
| Aktör | disarm'ı yapan kullanıcı |
| Gerekçe | disarm sırasında girilen zorunlu serbest metin (§2.9 audit) |

**2. Hiçbir otomatik mekanizma dokunmaz**

> **Lease, otomatik retry ve kural yeniden-açma disarmed satırlara KESİNLİKLE dokunmaz.**

- **Lease:** §2.4 yüklemi `dispatch_armed = TRUE` şartlıdır; disarmed satır yüklemi geçemez. Bu bir UI filtresi değil, veritabanı seviyesinde kapalılıktır.
- **Otomatik retry:** backoff zamanlayıcısı (§2.7) `next_attempt_at` dolsa bile satırı alamaz — aynı yüklem.
- **Kural yeniden açma:** `is_enabled = TRUE` yapmak eski satırları etkilemez (yukarıdaki re-arm kuralı). Otomatik re-arm yoktur.

**3. Saklama ve arşiv — asla silinmez**

> **Saklama süresi dolduğunda disarmed satır SİLİNMEZ; arşivlenir / soğuk audit saklamasına taşınır.**

Gerekçe: bu satırlar "gönderilmesi planlanmış ama durdurulmuş mesaj" kaydıdır ve KVKK/uyum denetiminde "neden gönderilmedi" sorusunun kanıtıdır. Hard delete, denetim izini yok eder.

- **Varsayılan saklama süresi: 90 gün** (disarm tarihinden itibaren), **şirket politikası parametresi** olarak yapılandırılabilir — mevcut `company_policies` deseninin altına ([company_policies.py](backend/app/company_policies.py)) `notification_disarmed_retention_days` olarak. Alt sınır uygulanır (öneri: 30 günden kısa yapılandırılamaz), böylece politika ayarı denetim izini pratikte kapatmak için kullanılamaz.
- **Hedef:** `notifications_archive` tablosu — `notifications` ile aynı şekil + `archived_at`, `archived_by` (`SYSTEM` olabilir), `archive_reason`. #169'un arşiv üçlüsü deseniyle uyumlu ([activity_log.py](.worktrees/activity-log-panel/backend/app/activity_log.py) madde 1).

**Arşiv işinin çalışma biçimi:**

- Dış cron/systemd timer'dan çağrılan bir yönetim komutu (worker ile aynı desen, §2.7). Uygulama içi zamanlayıcı yok.
- **Sıra pazarlıksızdır:** her parti için önce `INSERT INTO notifications_archive`, sonra `DELETE FROM notifications` — **tek transaction içinde**. INSERT başarısızsa DELETE çalışmaz. Kaynak satır, kopyası kalıcı olarak yazılmadan asla kaldırılmaz.
- **Tenant-safe:** her ifade `company_id = :cid` literal filtresi taşır; şirketler arası toplu iş yapılmaz, şirket şirket ilerlenir.
- **Partili ve idempotent:** sabit parti boyu (öneri 500), `id` sırasına göre. İş yarıda kesilirse yeniden çalıştırma güvenlidir — taşınmış satır kaynakta yoktur, tekrar taşınmaz.
- **Kapsam yalnız disarmed ve terminal-olmayan-ama-ölü satırlardır:** `dispatch_armed = FALSE AND status IN ('PENDING','RETRY_SCHEDULED') AND disarmed_at <= now() - :retention`. `PROCESSING` satır **hiçbir koşulda** arşivlenmez.
- Her çalıştırma actor (`SYSTEM`), zaman ve taşınan satır sayısıyla audit'e yazılır.

> Arşiv tablosu **salt okunurdur**; oradan `notifications`'a geri taşıma yolu yoktur. Arşivlenmiş bir mesaj yeniden gönderilecekse **yeni bir satır** üretilir ve baştan onay akışından geçer.

**4. Metrik ayrımı**

> Raporlarda ve metriklerde **`PENDING` sayımı YALNIZ `dispatch_armed = TRUE` satırları içerir.**
> Disarmed satırlar **ayrı bir metriktir** (`disarmed_count`), `PENDING`'e karıştırılmaz.

Karıştırmak, "kuyrukta 200 mesaj bekliyor" diyen bir panelin aslında hiçbiri gönderilmeyecek 200 satırı göstermesi demektir — operasyonel olarak yanlış tablo. Aynı ayrım gönderim raporunda da geçerlidir: durdurulan mesajlar "başarısız" değil, **"durduruldu"** kategorisindedir.

**5. Yeniden değerlendirme yolu**

> **Manuel yeniden değerlendirme, doğrudan re-arm DEĞİLDİR.**

Disarmed bir satırı yeniden canlandırmanın **tek** yolu `AWAITING_APPROVAL` üzerinden **yeni ve açık bir onaydır**: satır `AWAITING_APPROVAL`'a alınır, güncel `content_hash` yeniden hesaplanır (§2.5) ve §6'daki onay yetkisiyle, oluşturan≠onaylayan kuralına tabi olarak onaylanır. `dispatch_armed = TRUE` yazan başka hiçbir kod yolu yoktur.

Bu, satır kuyrukta beklerken vade/randevu/rıza değişmiş olabileceği içindir — §2.9'daki otomatik re-arm yasağının aynı gerekçesi.

**6. UI'da net cevap**

Kullanıcının sorduğu soru şudur: *"Kuralı kapattım, bu mesaj gidecek mi?"* Arayüz buna belirsiz değil, **net** cevap verir. Durdurulmuş satır şu şekilde görünür:

> **Gönderilmeyecek** — kural kapatıldı · 12.08.2026 14:30 · Ayşe Yılmaz

"Bekliyor", "zamanlandı" veya boş bir durum rozeti **kullanılmaz**; bunlar satırın bir gün gidebileceği izlenimi verir.

### 2.10 e-Fatura ortak kullanımı

Outbox'ın genel kalması için üç kural:

1. `type` alanı **namespace'li** olur: `service.reminder`, `harvest.due_soon`, `einvoice.send`, `einvoice.status_poll`.
2. `channel` yalnız iletişim kanalı değil, **taşıma hedefi**dir: `SMS | WHATSAPP | EMAIL | EINVOICE_PROVIDER | INAPP`.
3. Rıza kontrolü **kanal-bazlı ve koşulludur**: `EINVOICE_PROVIDER` kanalı ticari/yasal zorunluluk olduğu için rıza kapısına takılmaz. Rıza gerektiren kanallar açık bir kümede tutulur: `CONSENT_REQUIRED_CHANNELS = {SMS, WHATSAPP, EMAIL}`. Küme dışı kanal eklemek **açık bir kod değişikliği** gerektirir — sessizce bypass edilemez.

Mevcut `app/einvoice/` modülünün outbox'a taşınması bu tasarımın kapsamında **değildir**; yalnız seam'in buna engel olmaması garanti edilir.

---

## 3. Opt-in / KVKK

### 3.1 `notification_consents`

```
id, company_id NOT NULL,
party_type VARCHAR(20) NOT NULL,     -- CUSTOMER | SUPPLIER
party_id   INTEGER     NOT NULL,
channel    VARCHAR(30) NOT NULL,     -- SMS | WHATSAPP | EMAIL
status     VARCHAR(20) NOT NULL,     -- GRANTED | REVOKED
granted_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
source     VARCHAR(30) NOT NULL,     -- FORM | PHONE | CONTRACT | IMPORT
source_ref TEXT,                     -- sözleşme no, form id
recipient_snapshot VARCHAR(255),     -- rıza verildiği andaki numara/e-posta
version    INTEGER NOT NULL DEFAULT 1,  -- her GRANT/REVOKE artırır (§3.3 audit snapshot'ı)
created_by INTEGER, created_at, updated_at
UNIQUE (company_id, party_type, party_id, channel)
```

`version`, gönderim satırına `notifications.consent_version` olarak kopyalanır. İki gönderim arasında rızanın değişip değişmediği böylece denetimde tek bakışta görülür — ancak bu snapshot **karar verici değildir** (§3.3).

### 3.2 `notification_consent_events` (append-only)

Her rıza değişikliği bir satır: `consent_id, company_id, action (GRANT|REVOKE), old_status, new_status, user_id, reason, created_at`. **UPDATE/DELETE yok** — #169'un değişmezlik sözleşmesi ([activity_log.py](.worktrees/activity-log-panel/backend/app/activity_log.py) madde 1) birebir uygulanır. `notifications` tablosunun `consent_id` kolonu, gönderimin **hangi rızaya dayandığını** kalıcı olarak bağlar.

### 3.3 FAIL-CLOSED kuralı ve rıza yarışının kesin semantiği

> **Rıza kaydı yoksa, `REVOKED` ise, ya da `recipient` rıza anındaki numaradan farklıysa → gönderim yapılmaz.**
> Belirsizlik (kayıt bulunamadı, kanal bilinmiyor, numara normalize edilemedi, İYS'ye ulaşılamadı) **reddedilme** anlamına gelir; asla varsayılan-izin değil.

Kontrol **iki noktada** yapılır (defense in depth):
1. `enqueue_*` üreticisinde — kuyruğa hiç girmez, kullanıcıya sebep gösterilir.
2. `send_notification` içinde, satır `PROCESSING`'e alındıktan **sonra**, provider çağrısından **hemen önce** — rıza enqueue ile gönderim arasında iptal edilmiş olabilir. Engel varsa satır `REJECTED`'a düşer (geçiş 13) ve provider **hiç çağrılmaz**.

#### Linearization point — ne vaat ediliyor, ne edilmiyor

> **Vaat:** Provider çağrısından hemen önceki **son rıza okuması**, gönderimin *linearization point*'idir. O okuma anında **commit edilmiş** bir iptal varsa, gönderim fail-closed olarak engellenir.
>
> **Vaat EDİLMEYEN:** Gönderim **başladıktan sonra** gelen bir iptal, devam eden provider işlemini geri alamaz. Dış sistemde tetiklenmiş bir SMS geri çağrılamaz.

Bunun doğrudan sonucu, açıkça kabul edilen bir yarış penceresidir: son rıza okuması ile provider çağrısının dış sistemde etki etmesi arasında iptal edilen bir rıza, o tek mesajı durduramaz. Pencere provider çağrısının süresiyle sınırlıdır; sonraki tüm gönderimler engellenir.

> **Önceki taslaktaki "rızanın iptali her zaman kazanmalıdır" ifadesi geri çekilmiştir.** Dış çağrı ile veritabanı arasında atomiklik olmadığı için bu garanti edilemez; edilebilirmiş gibi yazmak yanlış bir güvence olurdu.

#### Kilit politikası — açık

**Dış sağlayıcı çağrısı boyunca hiçbir veritabanı kilidi TUTULMAZ.** Ne satır kilidi (`FOR UPDATE`), ne açık transaction. Mevcut kodun deseni budur ve korunur: lease **commit edilir**, ondan sonra provider çağrılır ([service.py:217](backend/app/notifications/service.py:217) claim commit'i → [service.py:258](backend/app/notifications/service.py:258) provider çağrısı). Gerekçe: dış çağrı saniyeler-dakikalar sürebilir; kilit tutmak bağlantı havuzunu tüketir ve provider yavaşlığını veritabanı kilitlenmesine dönüştürür.

Eşzamanlılık kontrolü kilitle değil, **lease + `lock_token` CAS**'ı ile sağlanır. Sonuç yazımı da `lock_token` eşleşmesi şartlıdır ([service.py:285-304](backend/app/notifications/service.py:285)); lease süresi dolup satır başkasına geçtiyse yazım reddedilir.

#### Snapshot yalnız audit içindir

Satıra yazılan `consent_id`, `consent_version`, `consent_checked_at`, `consent_decision`, `consent_decision_reason` alanları (§2.2) **yalnız denetim kanıtıdır**: "bu mesaj hangi rızaya, hangi versiyona dayanarak, ne zaman yapılan kontrolle gönderildi".

> **Snapshot, güncel rıza kontrolünün yerine GEÇMEZ.** Her gönderim denemesi — ilk deneme de, her retry de — rızayı **yeniden okur**. Satırdaki snapshot'a bakılarak "zaten izin vardı" denip kontrol atlanamaz. Retry, saatler önce alınmış bir izni yeniden kullanamaz.

`consent_version`, `notification_consents.version` alanından kopyalanır; rıza değişince versiyon artar, böylece iki gönderim arasında rızanın değiştiği denetimde görülebilir.

---

## 4. Tetikleyiciler ve şablonlar (v1)

### 4.0 Mesaj sınıfı — ZORUNLU alan

> **Her şablon, tam olarak bir mesaj sınıfı beyan etmek ZORUNDADIR:**
> **`SERVICE_TRANSACTIONAL`** (işlemsel/hizmet bildirimi) veya **`COMMERCIAL`** (ticari elektronik ileti).
>
> Sınıfsız şablon kaydedilemez; `NULL` veya varsayılan sınıf **yoktur**. Bu, `notification_templates.message_class NOT NULL` ile veritabanı seviyesinde zorlanır.

Sınıf, satıra `notifications.message_class` olarak kopyalanır (§2.2) ve gönderim boyunca değişmez.

#### `COMMERCIAL` için çifte kapı

Ticari sınıftaki bir mesaj **iki bağımsız kapıyı birlikte** geçmek zorundadır. İkisi de fail-closed:

1. **Yerel rıza** — `notification_consents` kaydı `GRANTED` (§3).
2. **İYS doğrulaması** — İleti Yönetim Sistemi'nde alıcının ilgili kanal için durumu `ONAY`.

> **Her iki kapı da geçilmeden ticari gönderim YAPILMAZ.** Yerel rızanın varlığı İYS kontrolünü atlatmaz; İYS onayı yerel rızanın yerine geçmez.
>
`SERVICE_TRANSACTIONAL` sınıfı İYS kapısına tabi değildir — yalnız yerel rıza kapısına tabidir.

#### İYS erişilemezlik davranışı (normatif)

> **`COMMERCIAL` bir mesajda İYS'ye erişilemiyorsa provider ÇAĞRILMAZ.**
> Erişilemezlik "bilinmiyor" demektir, "izin var" demek **değildir**.

**Durum ayrımı — genel `FAILED` değil:** Satır `FAILED`'a düşürülmez; ayrı bir duruma alınır:

```
COMPLIANCE_CHECK_UNAVAILABLE
```

Gerekçe: `FAILED` "sağlayıcı mesajı reddetti/gönderilemedi" demektir ve operatör bunu teknik bir arıza olarak okur. İYS kesintisi ise **uyum kapısının doğrulanamaması**dır — farklı bir sebep, farklı bir müdahale, farklı bir rapor satırı. İkisini aynı kovaya koymak, uyum ihlali riskini teknik gürültünün içinde gizler. Geçişler: §2.3 tablo satır 16–18.

**Kontrollü backoff + devre-kesici (circuit breaker):**

- Yeniden sorgu **kontrollü backoff** ile yapılır (§2.7'deki üstel + jitter + üst sınır şeması).
- **Sınırsız yeniden sorgu YASAKTIR.** `compliance_attempt_count` üst sınırı (`cb_max`, öneri **5**) aşıldığında satır `REJECTED` + `consent_decision_reason='IYS_UNAVAILABLE'` ile kapatılır ve operatöre gösterilir.
- Şirket+kanal bazında bir **devre-kesici** tutulur: art arda N başarısız İYS sorgusundan sonra devre açılır ve o pencere boyunca yeni İYS sorgusu yapılmaz; bekleyen ticari satırlar sorgu denemeden `COMPLIANCE_CHECK_UNAVAILABLE`'da bekletilir. Devre açıkken İYS'ye yük bindirmek kesintiyi uzatır.
- Bu davranış **kabul kriteri 13**'tür (§7.5).

**Manuel retry İYS'yi atlamaz:**

> Elle tetiklenen retry de İYS'yi **yeniden sorgular**. Manuel müdahale uyum kapısını aşmanın yolu değildir. Retry, `COMPLIANCE_CHECK_UNAVAILABLE` satırını yalnız yeni bir İYS sorgusu kuyruğuna alır; sorgu başarısızsa satır aynı durumda kalır.

**Cache geçerliliği ve öncelik sırası:**

- **Olumlu (`ONAY`) cache YALNIZ 24 saat içindeyse geçerlidir.** TTL dolmuşsa veya hiç cache yoksa, kesinti sırasında **gönderim yoktur**. Süresi geçmiş olumlu cache, kesintiyi kapatmak için kullanılamaz — TTL'in tek amacı budur.
- **Doğrulanmış `RET` gönderimi derhal bastırır ve olumlu cache tarafından EZİLMEZ.**

> **Öncelik sırası (pazarlıksız): `RET` > olumlu cache > canlı `ONAY`.**
> Bilinen bir ret, daha yeni tarihli bir olumlu cache kaydı olsa dahi kazanır.

- **Teknik hata ASLA onay sayılmaz.** Timeout, 5xx, bağlantı hatası, ayrıştırma hatası, beklenmeyen yanıt şeması — hepsi `BILINMIYOR`'dur ve fail-closed davranır. Hiçbir kod yolunda "hata aldık, devam et" dalı bulunmaz.

#### İYS cache ve audit

- **Cache TTL tanımlıdır:** İYS sorgu sonucu şirket+alıcı+kanal bazında en fazla **24 saat** cache'lenir (`iys_checked_at`, `iys_source='CACHE'`). TTL dolmuşsa canlı sorgu zorunludur; TTL'siz süresiz cache **yasaktır**.
- `RET` sonucu için cache yalnız **bastırma yönünde** kullanılır (yukarıdaki öncelik sırası); ret'in gecikmeli görülmesi riskine karşı canlı sorgu tercih edilir. Risk asimetriktir: yanlış bastırma zararsız, yanlış gönderim uyum ihlalidir.
- Her İYS kontrolü audit'lidir: `iys_status`, `iys_checked_at`, `iys_reference` (İYS kayıt kimliği), `iys_source` satıra yazılır. İYS'ye yapılan onay/ret **senkronizasyon** işlemleri de kimlik + tarih + sonuç ile `notification_consent_events`'e düşer.

#### Sınıf karışmaz

> **Metne promosyon/pazarlama içeriği eklenmesi, şablonun sınıfını `COMMERCIAL`'a çevirir.** İşlemsel bir şablona kampanya cümlesi eklenerek İYS kapısı atlatılamaz.

Şablon gövdesi değiştiğinde sınıf **yeniden onaya** düşer: `message_class` değişikliği veya gövde değişikliği, şablonu `is_active = FALSE` yapar ve `notifications_admin` yetkisiyle yeniden etkinleştirilmesini gerektirir.

> ⚠️ **"Servis hatırlatması işlemseldir" bir VARSAYIMDIR, tasarım kararı değildir.** Randevu hatırlatmasının işlemsel mi ticari mi sayılacağı hukuki bir niteleme sorusudur. Bu, **şablon sınıfı bazında** berkay'ın ve/veya uyum (compliance) tarafının açık onayına bağlanır — §8 soru 1 ve 11. Tasarım her iki cevabı da destekler; varsayılan olarak hiçbir şablon otomatik `SERVICE_TRANSACTIONAL` işaretlenmez.

### 4.1 `notification_templates`

```
id, company_id, code VARCHAR(60), channel VARCHAR(30),
name VARCHAR(160), body TEXT,
message_class VARCHAR(24) NOT NULL,     -- SERVICE_TRANSACTIONAL | COMMERCIAL (§4.0)
is_active BOOLEAN NOT NULL DEFAULT FALSE,
version INTEGER NOT NULL DEFAULT 1,
created_by, created_at, updated_by, updated_at,
approved_by, approved_at                 -- sınıf/gövde onayı (§4.0)
UNIQUE (company_id, code, channel)
```

Şablon **düzenlenirken versiyon artar**; `notifications.template` kolonu gönderim anındaki kodu, `payload` ise render edilmiş değişkenleri saklar → geçmiş bildirimler şablon değişince bozulmaz.

**Render kuralları:**
- Değişken sözdizimi `{degisken_adi}` — kapalı bir allow-list; bilinmeyen değişken `ValueError`, sessiz boş string **değil**.
- Para değerleri `format_money_tr` üzerinden **Decimal**'den üretilir; `float` yok (#169 madde 4).
- Tarihler `dd.MM.yyyy` Türkçe formatta.
- Render çıktısı kanal limitine göre doğrulanır (SMS için karakter sayısı + Türkçe karakter/GSM-7 uyarısı).

**Değişken tip ve uzunluk sınırları (zorunlu):** Her allow-list değişkeni bir tip ve bir üst uzunluk beyan eder. Sınır aşılırsa render **hata verir**; sessizce kırpılmaz — kırpma, kontrol karakteriyle bölünmüş bir şema (§5.5) gizleyebilir.

| Değişken | Tip | Üst sınır |
|---|---|---|
| `musteri_adi`, `firma_adi` | `str` (harf/rakam/boşluk/`.,-'`) | 80 |
| `makine_adi` | `str` | 60 |
| `randevu_tarihi`, `vade_tarihi` | `date` → `dd.MM.yyyy` | 10 (sabit) |
| `tutar` | `Decimal` → `format_money_tr` | 24 |

Serbest metin (`str`) değişkenleri, gövdeye yerleştirilmeden **önce** §5.5'teki içerik kapısından geçer. Değişken üzerinden link enjeksiyonu böylece render aşamasında durdurulur.

### 4.2 v1 şablonları (Türkçe)

| Kod | Kanal | Sınıf (§4.0) | Gövde taslağı |
|---|---|---|---|
| `service.reminder` | SMS | onay bekliyor ⚠️ | `Sayın {musteri_adi}, {makine_adi} makinenizin servis randevusu {randevu_tarihi} tarihindedir. {firma_adi}` |
| `harvest.due_soon` | SMS | onay bekliyor ⚠️ | `Sayın {musteri_adi}, {vade_tarihi} vadeli {tutar} tutarındaki ödemenizin vadesi yaklaşmaktadır. {firma_adi}` |
| `harvest.overdue` | SMS | onay bekliyor ⚠️ | `Sayın {musteri_adi}, {vade_tarihi} vadeli {tutar} tutarındaki ödemenizin vadesi geçmiştir. {firma_adi}` |

⚠️ Üç şablonun da sınıfı **berkay/uyum onayına bağlıdır** (§4.0). Tasarım hiçbirini kendiliğinden `SERVICE_TRANSACTIONAL` saymaz.

**Hiçbir şablonda link yoktur.** (§5)

### 4.3 Tetikleyici sorguları

**Servis hatırlatma:** `work_orders` içinde `status='SCHEDULED'` ve `scheduled_date` bugünden `offset_days` sonra olan kayıtlar. `scheduled_date` gerçek `TIMESTAMPTZ` ([migration 0029:85](backend/alembic/versions/20260727_0029_servis_v2_faz1.py:85)) — tip sorunu yok.

**Harman vade:** vade `orders.due_date` üzerinden okunur.
> ⚠️ **`orders.due_date` VARCHAR ISO string'dir, DATE değil.** PostgreSQL'de varchar/date karşılaştırması `UndefinedFunction` verir. Mevcut kod bu deseni belgelemiş: [companies.py:150-155](backend/app/routers/companies.py:150).

**F2 giriş kriteri (blokaj):** F2 **başlamadan önce** `due_date` için açık bir dönüşüm kararı verilmiş olmalıdır. Karar iki seçenekten biridir:

- **(A) Tercih edilen — normalize edilmiş ayrı tarih alanı.** Expand-only ile `orders.due_date_normalized DATE NULL` eklenir, backfill edilir, tetikleyici sorguları **yalnız** bu alanı kullanır. Eski VARCHAR kolonu yerinde kalır (contract yok).
- **(B) String karşılaştırması sürdürülür.** Bağlı ISO string'e karşı karşılaştırma yapılır (ISO'nun sözlük sıralaması = kronolojik sıralama). Bu durumda boş string ve `NULL` ayrımı, format bozuk kayıtlar ve kısmi tarihler açıkça ele alınmalıdır.

Her iki durumda da **PostgreSQL regresyon testi şarttır** — `@pytest.mark.postgresql` altında, gerçek PG 16'ya karşı, sınır değerleriyle (`NULL`, `''`, bozuk format, vade bugün, vade dün, vade yarın). SQLite'ta geçen bir sorgu PG'de `UndefinedFunction` ile patlayabilir; bu tuzak F2'nin en olası hata kaynağıdır ve `fix/notifications-overdue-varchar-date-comparison` dalı bunun geçmiş kanıtıdır.

Periyodik bakım tetikleyicisi **v1'de yok** — `maintenance_plans` tablosu mevcut değil. F5 ile birleşecek.

### 4.4 `notification_rules` (F2)

```
id, company_id, code, trigger_type, channel, template_id,
offset_days INTEGER,           -- negatif: öncesi, pozitif: sonrası
is_enabled BOOLEAN NOT NULL DEFAULT FALSE,   -- varsayılan KAPALI
send_window_start TIME, send_window_end TIME,
max_per_run INTEGER NOT NULL DEFAULT 50,
scope_filter TEXT,             -- JSON: sınırlı kapsam (şube, müşteri grubu)
created_by, approved_by, created_at, updated_at
```

`is_enabled` **varsayılan olarak FALSE**. Bir kural, açıkça etkinleştirilmeden tek bir mesaj bile göndermez.

> Kural kapatıldığında kuyrukta bekleyen satırların da durdurulması **zorunludur** — atomiklik, tenant güvenliği, `PROCESSING` istisnası ve otomatik re-arm yasağı §2.9'da normatif olarak tanımlıdır.

---

## 5. 🔒 SABİT GÜVENLİK KURALI (pazarlıksız)

> **SMS / ödeme linki ASLA otomatik gönderilmez.**
>
> Her gönderim **ya**:
> - **(a)** kullanıcının **AÇIK onayıyla** — arayüzde buton + yetki kontrolü + audit kaydı; **ya da**
> - **(b)** açıkça **etkinleştirilmiş, kapsamı sınırlı** bir zamanlama kuralıyla (`notification_rules.is_enabled = TRUE`, tanımlı `scope_filter`, tanımlı `max_per_run`).
>
> **Ödeme linki v1'de HİÇ YOKTUR.** Faz-C'nin konusudur; bu modüle karışmaz.

Uygulama karşılıkları — hepsi mekanik, "dikkatli olalım" değil:

1. **Varsayılan kapalı:** `notification_rules.is_enabled` DEFAULT FALSE. Yeni kural sessizce çalışmaz.
2. **Onay durumu:** onaysız satır `AWAITING_APPROVAL`'da bekler ve dispatch edilebilir kümesinde **değildir**. Onay olmadan worker onu asla almaz.
3. **Toplu gönderim üst limiti:** tek istekte en fazla **50** alıcı (`max_per_run`). Aşan istek reddedilir — kısaltılmaz, sessizce kırpılmaz.
4. **Tek tek onay listesi:** toplu gönderim ucu iki adımlıdır. Adım 1 (`POST /notifications/bulk/preview`) hiçbir şey göndermez; alıcı listesini, maskelenmiş numaraları, rıza durumunu, render edilmiş metni ve **rıza yüzünden elenenleri** döner. Adım 2 (`POST /notifications/bulk/confirm`) yalnız kullanıcının **tek tek işaretlediği** id'leri kuyruğa alır. "Hepsini gönder" tek tuşu **yoktur**.
5. **Link yasağı — izin-verilen-içerik modeli:** bkz. §5.5. Ödeme linki v1'de teknik olarak imkânsızdır, yalnız politika olarak yasak değil.
6. **Gönderim penceresi:** kural-tabanlı gönderim yalnız `send_window_start`–`send_window_end` arasında (öneri 09:00–18:00, hafta içi). Pencere dışı satır `scheduled_for` ile ötelenir, iptal edilmez.
7. **Her gönderim audit'li:** `created_by`, `approved_by`, `approved_at`, `approval_mode` (MANUAL/RULE/SYSTEM) satırda kalıcıdır.
8. **Görevler ayrılığı:** onaylama ve dispatch **ayrı yetkilerdir** ve **oluşturan kendi batch'ini onaylayamaz** (§6).

### 5.5 İçerik kapısı — izin-verilen-içerik modeli (fail-closed)

> Basit "URL ara ve reddet" regex'i **yetersizdir** ve kullanılmayacaktır. Kara liste yaklaşımı, atlatma yöntemlerinin sonsuz kuyruğuna karşı kaybeden bir yarıştır.

**v1 modeli:** URL *aramak* yerine, **izin verilen karakter ve içerik kümesi** tanımlanır; kümenin dışındaki her şey reddedilir. Bilinmeyen girdi = red (fail-closed).

**İzin verilen küme (SMS, v1):** Türkçe harfler (`a-zA-ZçÇğĞıİöÖşŞüÜ`), rakamlar, boşluk, ve `. , : ; ! ? ' " ( ) - /` noktalama alt kümesi + yeni satır. Bu kümenin dışındaki her karakter reddi tetikler.

Bu küme, `:` ve `/` karakterlerine izin verdiği için tek başına yeterli değildir; üstüne **açık yasak kontrolleri** eklenir. Kritik olan, kontrolün **nerede** çalıştığıdır:

> **Kontrol, normalize edilmiş NİHAİ içerik üzerinde çalışır** — şablon gövdesi üzerinde değil, değişkenler yerleştirildikten ve tüm normalizasyon uygulandıktan sonraki, sağlayıcıya gidecek olan tam metin üzerinde.

**Normalizasyon zinciri (kontrolden önce, bu sırayla):**
1. Unicode **NFKC** normalizasyonu — genişlik varyantları, ligatürler, homoglif kanonikleştirme
2. Sıfır-genişlik ve kontrol karakterlerinin **kaldırılması** (`U+200B`–`U+200D`, `U+FEFF`, `U+00AD` yumuşak tire vb.) — bunlar bölünmüş şema gizler
3. **URL-decode** (`%68%74%74%70` → `http`) ve **HTML entity decode** (`&#104;`, `&period;`) — yinelemeli, sabit noktaya kadar
4. **Case-insensitive** karşılaştırma (`HtTp://`)
5. Boşluk daraltma

**Normalize edilmiş metinde reddedilen içerik:**

| Kategori | Örnek |
|---|---|
| Şema | `http://`, `https://`, `ftp://`, `data:`, `tel:`, **`mailto:`** |
| Bölünmüş şema | `h t t p : / /`, `http[:]//`, `hxxp://`, `http\u200b://` |
| `www.` öneki | `www.ornek.com` |
| **Çıplak domain** | `ornek.com`, `ornek.com.tr` — TLD listesine karşı, şemasız |
| Kısaltıcılar | `bit.ly`, `t.co` vb. (çıplak domain kuralı zaten yakalar) |
| IP adresi | `192.0.2.1`, `0xC0000201`, `2130706433` (decimal/hex IP) |

**HTML kanalı için ek (EMAIL, gelecek):** `href`, `src`, `action`, `formaction`, `data-*` öznitelikleri ve CSS `url(...)` / `@import` yapıları **ayrıca ve bağımsız** denetlenir. Düz metin kontrolü, HTML özniteliği içine gömülü bir URL'yi yakalamaya yetmez.

**Reddedilen içeriğin audit'i:** Her red bir denetim kaydı üretir — kural kimliği, tetikleyen kategori, kullanıcı, zaman. Kaydedilen içerik **PII-maskelidir**: alıcı bilgisi `_mask_recipient` ([routers/notifications.py:61](backend/app/routers/notifications.py:61)) ile maskelenir ve reddedilen metnin tamamı değil, yalnız eşleşen parça + sınırlı bağlam (±20 karakter) saklanır. Red kaydı, atlatma denemelerini görünür kılmak içindir; PII arşivi değildir.

**Kontrol noktaları (üçü de zorunlu):**
1. Şablon kaydedilirken (gövde üzerinde)
2. Değişken değeri yerleştirilmeden önce (§4.2)
3. Render sonrası nihai içerik üzerinde — **belirleyici olan budur**

> v1'de hiçbir şablonun meşru bir link ihtiyacı yoktur. Bu yüzden izin-verilen-içerik modeli fonksiyonel bir kayıp yaratmaz. Faz-C'de ödeme linki gündeme geldiğinde, bu kapı **imzalı ve allow-list'lenmiş tek bir alan adı** için açılır — genel URL izni olarak değil.

---

## 6. RBAC

Mevcut ucun `"users"` yetkisine bakması ([routers/notifications.py:55](backend/app/routers/notifications.py:55)) fazla kaba. Üç yeni yetki önerilir:

Mevcut ucun `"users"` yetkisi yerine **dört** ayrı yetki. Onaylama ile tetikleme bilinçli olarak ayrılmıştır:

| Yetki | Ne yapar |
|---|---|
| `notifications` | Outbox listesini + gönderim raporunu görür |
| `notifications_approve` | Gönderimi **onaylar** (`AWAITING_APPROVAL` → `PENDING`), toplu onay listesinde tek tek işaretler |
| `notifications_dispatch` | Onaylanmış satırı **tetikler**/retry eder |
| `notifications_admin` | Şablon düzenler, sınıf onaylar, kural tanımlar/etkinleştirir, rıza kaydı yönetir |

Rol dağılımı:

| Rol | `notifications` | `notifications_approve` | `notifications_dispatch` | `notifications_admin` |
|---|:---:|:---:|:---:|:---:|
| `admin` (`*`) | ✅ | ✅ | ✅ | ✅ |
| `yonetici` | ✅ | ✅ | ✅ | ✅ |
| `muhasebe` | ✅ | ✅ (yalnız vade) | ✅ (yalnız vade) | ❌ |
| `satis` | ✅ | ❌ | ❌ | ❌ |
| `depo` / `rapor` | ❌ | ❌ | ❌ | ❌ |

**Görevler ayrılığı — pazarlıksız:**

> **Bir batch'i oluşturan kullanıcı, kendi batch'ini ONAYLAYAMAZ.**
> Onay ucu `approved_by != created_by` koşulunu **veritabanı seviyesinde** uygular; ihlal 403 döner. Tek kullanıcılı kurulumda bu, toplu gönderimin `admin` onayı gerektirmesi anlamına gelir — bilinçli sürtünmedir.

Ek kısıtlar:
- **Rıza yönetimi `notifications_admin` gerektirir** ve her değişiklik `notification_consent_events`'e yazılır.
- **Şablon düzenleme (`notifications_admin`) ile gönderim onayı (`notifications_approve`) ayrı yetkilerdir** — aynı kişi şablonu değiştirip tek başına toplu gönderim yapamasın diye.
- **Mesaj sınıfı onayı** (§4.0) yalnız `notifications_admin`'dedir; `notifications_approve` bir şablonun sınıfını değiştiremez.
- Her onay, dispatch ve red işlemi **actor kimliği + zaman damgası** ile kalıcı olarak kaydedilir (`created_by`, `approved_by`, `approved_at`, `approval_mode`).
- `recipient` alanı listede **maskelenir** ([routers/notifications.py:61-69](backend/app/routers/notifications.py:61) `_mask_recipient` zaten var, korunur). `payload` operasyonel listede boş döner ([:77](backend/app/routers/notifications.py:77)) — bu korunmalı, çünkü payload PII içerir.

**Aktivite kataloğu olayları** — #169'un `ACTION_TYPES` sözlüğüne eklenecekler:

```
"notification.sent":            "Bildirim gönderildi"
"notification.failed":          "Bildirim gönderilemedi"
"notification.approved":        "Bildirim gönderimi onaylandı"
"notification.cancelled":       "Bildirim iptal edildi"
"notification.consent_granted": "Bildirim izni verildi"
"notification.consent_revoked": "Bildirim izni kaldırıldı"
"notification.template_updated":"Bildirim şablonu güncellendi"
"notification.rule_enabled":    "Bildirim kuralı etkinleştirildi"
"notification.rule_disabled":   "Bildirim kuralı kapatıldı"
"notification.rows_disarmed":   "Kuyruktaki bildirimler durduruldu"
"notification.compliance_unavailable": "Uyum doğrulaması yapılamadı"
```

`notification.rows_disarmed` olayının detayında **etkilenen satır sayısı** (`rowcount`) ve zorunlu gerekçe bulunur (§2.9).

---

## 7. Fazlama

**F1 — Outbox + mock + manuel gönderim**
- Migration: yeni kolonlar + `notification_consents` (+`version`) + `notification_consent_events` + `notification_templates`
- `MockNotificationProvider` (`SENT` döner, sahte `external_id`) — NoOp yanına; **NoOp asla `SENT` yazmaz** (§2.8)
- Rıza kontrolü (çift noktada, fail-closed) + linearization semantiği (§3.3) + `normalize_msisdn` doğrulama
- Şablon CRUD + **zorunlu `message_class`** + render (allow-list değişken, tip/uzunluk sınırı, §5.5 içerik kapısı, Decimal para)
- Kapalı geçiş tablosu (§2.3) + §2.4 lease yüklemi + `AWAITING_APPROVAL` / `RETRY_SCHEDULED` / `CANCELLED` / `REJECTED`
- Onay geçersizleşmesi (`content_hash`, §2.5) + retry ucu sınırları (§2.6)
- Toplu önizleme/onay ucu (limit 50, tek tek işaretleme, oluşturan≠onaylayan)
- Yeni RBAC yetkileri (4 adet, §6)
- Frontend: outbox listesi + şablon ekranı + rıza yönetimi
- **İlk gerçek üretici:** iş emri `SCHEDULED` olduğunda **elle** hatırlatma gönderme butonu

> 🚩 **F1 blokaj koşulu — İYS.** F1 kapsamında **gerçek müşteriye gönderim yapılacaksa**, İYS kararı (§4.0, §8 soru 1) **F1'in blokajıdır** ve kod yazılmadan çözülmelidir. F1 yalnız `NoOp`/`Mock` sağlayıcıyla, gerçek alıcıya hiçbir mesaj gitmeden kalırsa İYS kararı **F3'e ertelenebilir**. Bu ayrım F1 başlangıcında açıkça karara bağlanır; "sonra bakarız" bir seçenek değildir çünkü `message_class` alanı F1'in migration'ındadır.

**F2 — Hatırlatma kuralları + zamanlama**
- `notification_rules` tablosu (varsayılan kapalı) + gönderim penceresi
- Retry backoff (`next_attempt_at`, üst sınır + jitter, §2.7) + worker komutu (dış cron)
- Servis hatırlatma tetikleyicisi (`scheduled_date`)
- Harman vade yaklaşan/geçen tetikleyicisi
- Kural etkinleştirme audit'i

> 🚩 **F2 giriş kriteri — `due_date`.** F2 **başlamadan önce** §4.3'teki dönüşüm kararı (tercihen A: normalize edilmiş ayrı tarih alanı) verilmiş ve PG regresyon testi şartı kabul edilmiş olmalıdır.

**F3 — Gerçek sağlayıcı** (berkay seçtikten sonra)
- Seçilen adapter, `supports_idempotency` **doğru** beyanı, `Idempotency-Key` başlığı
- İYS entegrasyonu (F1'de ertelendiyse) — canlı sorgu, TTL'li cache, audit
- Şirket-bazlı kimlik bilgisi saklama (şifreli), timeout < 5dk lease
- Teslim durumu webhook'u → `DELIVERED`

**Bağımlılık:** `notification.*` aktivite olayları **#169'un merge'ine bağlıdır**. #169 F1'den önce merge olmazsa iki seçenek var: (a) F1'i aktivite olayları olmadan bitirip olayları F2'de eklemek, ya da (b) F1'i #169'a stack'lemek. **Öneri: (a)** — bağımlılık zincirini kısa tutar.

---

## 7.5 KABUL KRİTERLERİ (16 madde)

Bu maddeler tasarımın kabul kapısıdır. **Hepsi karşılanmadan build fazı tamamlanmış sayılmaz.** Her madde, doğrulanabilir bir çıktıya (test, migration, doküman) bağlıdır.

> 1–12 rev2'de gelen özgün 12 kriterdir; 13–15 rev3'te eklendi (İYS devre-kesici, disarm atomikliği, kanonik hash); 16 rev4'te eklendi (disarmed satır yaşam döngüsü).

| # | Kriter | Karşılık | Doğrulama |
|---|---|---|---|
| 1 | **Kapalı geçiş tablosu** — durum makinesinin tüm geçişleri açıkça listelenmiş, küme dışı geçiş reddediliyor | §2.3 | `_ALLOWED_TRANSITIONS` sözlüğü + katalog dışı geçişin `ValueError` verdiği birim testi |
| 2 | **`AWAITING_APPROVAL` DB-dışlama** — onay bekleyen satır veritabanı yükleminde eleniyor, UI filtresiyle değil | §2.4 | Lease sorgusunun `AWAITING_APPROVAL` satırını `rowcount=0` ile geçtiği test; SQL yükleminin beş koşulu da içerdiği kod incelemesi |
| 3 | **Onay geçersizleşme** — içerik veya alıcı değişimi (yeniden render dahil) önceki onayı geçersiz kılıyor | §2.5 | `content_hash` değişince satırın `AWAITING_APPROVAL`'a döndüğü test; bayat `:seen_hash` ile onayın 409 aldığı test |
| 4 | **Linearization belgesi** — rıza yarışının kesin semantiği, kilit tutulmadığı ve snapshot'ın karar verici olmadığı yazılı | §3.3 | Doküman bölümü mevcut; "iptal her zaman kazanır" iddiası geri çekilmiş |
| 5 | **Consent-worker PG yarış testi** — rıza iptali ile dispatch arasındaki yarış gerçek PostgreSQL'de test edilmiş | §3.3 | `@pytest.mark.postgresql` altında eşzamanlı iptal + dispatch testi; commit edilmiş iptalin `REJECTED` ürettiği doğrulanmış |
| 6 | **Normalize URL testi** — içerik kapısı normalize edilmiş nihai metinde çalışıyor ve atlatma vektörlerini yakalıyor | §5.5 | NFKC, sıfır-genişlik, URL-encode, HTML entity, bölünmüş şema, `mailto:`, çıplak domain, decimal/hex IP vektörlerinin **her biri** için ayrı test |
| 7 | **Backoff + jitter + üst sınır** — retry gecikmesi üstel, jitter'lı ve üst sınırlı | §2.7 | `min(2^n * 60s, 6h)` üst sınır testi; jitter'ın seed ile deterministik test edilebildiği doğrulama |
| 8 | **Actor/zaman audit** — her onay, dispatch, red ve rıza değişikliği kim+ne zaman bilgisiyle kalıcı | §3.2, §5(7), §6 | `created_by`/`approved_by`/`approved_at`/`approval_mode` kolonları dolu; `notification_consent_events` append-only testi |
| 9 | **Sınıf karışmaz + İYS fail-closed** — mesaj sınıfı zorunlu, promosyon eklenmesi sınıfı değiştirir, İYS erişilemezse ticari gönderim yok | §4.0 | `message_class NOT NULL` migration; İYS `BILINMIYOR`/erişilemez durumunda `REJECTED` üretildiği test; gövde değişiminin şablonu `is_active=FALSE` yaptığı test |
| 10 | **`due_date` kriteri** — F2 öncesi açık dönüşüm kararı + PG regresyon testi | §4.3, §7 | Karar (A veya B) yazılı; `@pytest.mark.postgresql` altında sınır değer testleri (`NULL`, `''`, bozuk format, bugün/dün/yarın) |
| 11 | **Provider idempotency key + duplicate modeli** — anahtar değişmez kimlikten türer, retry'lar arası sabit; çift gönderim sınırı açıkça yazılı | §2.8 | Anahtarın `attempt_count`'tan bağımsız olduğu test; `supports_idempotency=False` iken süresi dolmuş lease'in **otomatik** geri alınmadığı test |
| 12 | **NoOp asla `SENT` yazmaz** — yapılandırılmamış sağlayıcı "gönderildi" raporlamaz | §2.8 | `NoOpNotificationProvider`'ın `NONE` döndüğü ve satırın `SENT`'e geçmediği test; `Mock` sağlayıcının yalnız açık ayarla devreye girdiği test |
| 13 | **İYS devre-kesici — sınırsız yeniden sorgu yok** | §4.0 | `compliance_attempt_count >= cb_max` sonrası satırın `REJECTED`'a düştüğü test; devre açıkken yeni İYS sorgusu yapılmadığı test; `COMPLIANCE_CHECK_UNAVAILABLE`'ın `FAILED`'dan ayrı raporlandığı doğrulama |
| 14 | **Disarm atomikliği** — kural kapatma ile satır disarm aynı transaction, tenant-safe, `PROCESSING`'e dokunmaz, otomatik re-arm yok | §2.9 | Kural kapatma transaction'ı rollback olduğunda disarm'ın da geri alındığı test; `PROCESSING` satırın etkilenmediği test; re-arm sonrası eski satırların `dispatch_armed=FALSE` kaldığı test; `rowcount`'un audit'e yazıldığı doğrulama |
| 15 | **Hash kanonik payload'dan** — şablon gövdesinden değil; tutar/vade/recipient değişimi farklı hash | §2.5 | Aynı şablon + farklı tutar → farklı `content_hash` testi; 7 adımın sırasının korunduğu test; dispatch öncesi yeniden doğrulamanın (adım 7) bozulmuş payload'ı yakaladığı test |
| 16 | **Disarmed satır yaşam döngüsü** — ayrı görünüm, otomatik mekanizmalar dokunmaz, silinmez/arşivlenir, metrik ayrı, re-arm yalnız yeni onayla | §2.9 | Disarmed satırın lease yükleminden ve otomatik retry'dan geçmediği test; saklama süresi sonunda **`DELETE` değil arşiv** olduğu ve INSERT başarısızsa kaynağın kaldığı transaction testi; `PENDING` metriğinin disarmed satırları saymadığı test; `dispatch_armed=TRUE` yazan tek yolun onay ucu olduğu kod incelemesi |

---

## 7.6 UYUM TABLOSU — 10 maddelik kontrol listesi

İnceleme turlarında sorulan kontrol maddelerinin her biri, tasarımda **nerede tanımlandığıyla** birlikte aşağıdadır. (1–9 rev3'ün kontrol listesi; 10 rev4'te eklendi.)

| # | Kontrol maddesi | Durum | Nerede |
|---|---|---|---|
| 1 | **Armed-kapatma** — kural kapatılınca kuyruktakiler de duruyor mu? | ✅ tanımlandı | **§2.9** — aynı transaction, atomik, `rule_id`+`company_id` tenant-safe, `PROCESSING` hariç, otomatik re-arm yok, `rowcount` audit + UI'da "(N satır)" |
| 2 | **Rendered-hash** — hash şablondan mı, render edilmiş içerikten mi? | ✅ tanımlandı | **§2.5** — 7 adımlı zorunlu sıra; hash kanonik render edilmiş payload'dan; 1.000 TL ≠ 10.000 TL örneği yazılı; dispatch öncesi yeniden doğrulama (adım 7) |
| 3 | **İYS önceliği** — ret mi cache mi kazanır? | ✅ tanımlandı | **§4.0** — `RET` > olumlu cache > canlı `ONAY`; doğrulanmış ret olumlu cache tarafından ezilmez; teknik hata asla onay sayılmaz |
| 4 | **Şablon/sınıf değişimi onay iptali** | ✅ tanımlandı | **§2.5** (`template_version` + `message_class` hash kapsamında → geçiş 5/6) + **§4.0** (gövde/sınıf değişimi şablonu `is_active=FALSE` yapar, `notifications_admin` yeniden onayı) |
| 5 | **Recipient değişimi = yeni rıza + yeni onay** | ✅ tanımlandı | **§2.5** (recipient hash kapsamında → onay geçersiz) + **§3.3** (`recipient_snapshot` uyuşmazlığı → `RECIPIENT_CHANGED`, fail-closed; yeni rıza gerekir) |
| 6 | **Disarm/re-arm yetki + audit** | ✅ tanımlandı | **§2.9** (actor + zaman + zorunlu gerekçe + etkilenen satır sayısı; `notification.rule_disabled` / `rule_enabled` / `rows_disarmed`) + **§6** (`notifications_admin`) |
| 7 | **Provider timeout → duplicate / idempotency** | ✅ tanımlandı | **§2.8** — sabit `provider_idempotency_key` (attempt'tan bağımsız); süresi dolmuş lease **yalnız** `supports_idempotency=True` iken otomatik geri alınır, aksi hâlde elle müdahale; at-least-once sınırı açık |
| 8 | **NoOp ≠ SENT** | ✅ tanımlandı | **§2.8** — `NoOp` `NONE` döner, `SENT` yazmaz; `Mock` yalnız açık ayarla devreye girer (kriter 12) |
| 9 | **`PROCESSING` lease timeout politikası** | ✅ tanımlandı | **§2.8** + **§3.3** — lease 5 dk ([service.py:26](backend/app/notifications/service.py:26)); adapter timeout'u lease'ten kısa olmalı ([docs/notification-delivery-semantics.md:14](docs/notification-delivery-semantics.md:14)); süresi dolmuş `PROCESSING` sağlayıcı idempotent değilse otomatik geri alınmaz; §2.9 disarm `PROCESSING`'e dokunmaz |
| 10 | **Disarmed satırın sonu ne olacak?** — görünürlük, dokunulmazlık, saklama, metrik, yeniden değerlendirme | ✅ tanımlandı | **§2.9 "Disarmed satır yaşam döngüsü"** — ayrı "Durdurulanlar" filtresi (kural/zaman/aktör/gerekçe); lease+retry+kural-açma dokunmaz; 90 gün (şirket politikası, alt sınır 30) sonunda **silinmez, `notifications_archive`'a taşınır** (INSERT→DELETE tek transaction, tenant-safe, partili, idempotent); `PENDING` metriği yalnız `dispatch_armed=TRUE`, disarmed ayrı metrik; re-arm yalnız `AWAITING_APPROVAL` + yeni açık onay; UI'da "Gönderilmeyecek — kural kapatıldı · tarih · kim" |

**Açık soruların tasarıma etkisi:** §8'deki 15 açık sorudan **tasarımı değiştiren kalmamıştır.** Hepsi ya (a) tasarımın **her iki cevabı da desteklediği** parametre seçimleridir (gönderim saatleri, offset günleri, cache TTL değeri, saklama süresi, tedarikçi kapsamı), ya da (b) **fazlama kapısıdır** — cevabı build'i bloke eder ama tasarımın yapısını değiştirmez:

| Soru | Tür | Etki |
|---|---|---|
| 1 (İYS / F1'de gerçek gönderim) | 🚩 faz kapısı | F1 blokajı olabilir; tasarım her iki yolu da tanımlı (§4.0, §7) |
| 11 (şablon sınıfı) | 🚩 faz kapısı | `message_class` değeri; alan ve kapılar tanımlı (§4.0) |
| 12 (`due_date` A/B) | 🚩 faz kapısı | F2 giriş kriteri; iki seçenek de yazılı (§4.3) |
| 2, 3 (tablo adı, `PROCESSING`) | karar | Öneri yazılı, onay bekliyor (§2.1, §2.3) |
| 4, 5, 14, 15 (saatler, offset, TTL, saklama) | parametre | Yapı değişmez, değer girilir |
| 6, 7, 8, 9, 10, 13 | kapsam/parametre | Yapı değişmez |

---

## 8. AÇIK SORULAR (berkay'a)

1. 🚩 **İYS kararı (F1 blokajı olabilir):** SMS mi WhatsApp mı öncelikli? Ticari elektronik ileti gönderiyorsak İYS entegrasyonu zorunludur (§4.0). **F1'de gerçek müşteriye gönderim olacak mı?** Olacaksa İYS kararı F1'i bloke eder; yalnız NoOp/Mock ile kalınacaksa F3'e ertelenebilir. Bu sorunun cevabı F1'in migration içeriğini değiştirir.
2. **Tablo adı:** Mevcut `notifications` tablosu genişletilsin mi, yoksa görev tanımındaki `notification_outbox` adı için rename yapılsın mı? (Rename expand-only ilkesine aykırı — §2.1.)
3. **`SENDING` vs `PROCESSING`:** Mevcut `PROCESSING` durumu korunsun mu? (§2.3'teki öneri: korunsun.)
4. **Gönderim saatleri:** Varsayılan pencere 09:00–18:00 hafta içi mi? Cumartesi gönderim olacak mı? Resmî tatil takvimi v1'de gerekli mi?
5. **Hatırlatma zamanlaması:** Servis randevusundan kaç gün önce? (Öneri: 1 gün.) Harman vadesinden kaç gün önce ve geçtikten kaç gün sonra? (Öneri: 3 gün önce, 1 ve 7 gün sonra.) Aynı müşteriye günde/haftada en fazla kaç mesaj?
6. **Hangi olaylar v1'de?** Görev servis hatırlatma + harman vadeyi sayıyor. İş emri tamamlandı, parça geldi, fatura hazır gibi olaylar v1'e girsin mi, yoksa F2+'ya mı?
7. **Rıza geçmişi:** Mevcut müşteriler için rıza kaydı yok. Toplu içe aktarma (`source=IMPORT`) ile mi başlanacak, yoksa her müşteri tek tek mi işaretlenecek? Rıza olmayan müşteriye hiç mesaj gitmeyecek — bu, başlangıçta gönderilebilir kitlenin sıfır olması demek. Kabul mü?
8. **Alıcı numarası:** `customers.phone` doğrulanmamış serbest metin. Normalizasyon başarısız olan kayıtlar için bir temizlik ekranı gerekli mi, yoksa gönderim anında hata göstermek yeterli mi?
9. **Tedarikçiler:** Bildirimler yalnız müşterilere mi, tedarikçilere de mi? (Tasarım `party_type` ile ikisini de destekliyor.)
10. **`service` rolü:** [auth.py:104](backend/app/auth.py:104)'te planlandığı belirtilen servis rolü bu modülde tanımlansın mı, yoksa ayrı bir görev mi?
11. 🚩 **Şablon sınıfı onayı (§4.0):** `service.reminder`, `harvest.due_soon`, `harvest.overdue` — bu üçü `SERVICE_TRANSACTIONAL` mi `COMMERCIAL` mi? "Servis hatırlatması işlemseldir" tasarımın **varsayımı değil**, senin/uyum tarafının kararıdır. Vade hatırlatması özellikle tartışmalı: borç bildirimi işlemsel sayılabilir, ama aynı mesaj pazarlama tonu taşırsa ticariye döner. Üçü için ayrı ayrı karar gerekiyor.
12. **`due_date` dönüşümü (§4.3):** F2 öncesi seçenek A (normalize edilmiş ayrı `DATE` kolonu, expand-only) mı, seçenek B (ISO string karşılaştırması) mı? A önerilir.
13. **Görevler ayrılığı (§6):** "Oluşturan kendi batch'ini onaylayamaz" kuralı tek kullanıcılı/küçük ekip kurulumunda toplu gönderimi `admin` onayına bağlar. Bu sürtünme kabul mü, yoksa tek kullanıcılı kurulum için tanımlı bir istisna mı gerekiyor?
14. **İYS cache TTL:** Öneri 24 saat (`ONAY` için; `RET` cache'lenmez). Uygun mu?
15. **Durdurulan bildirimlerin saklama süresi (§2.9):** Öneri 90 gün (şirket politikası parametresi, alt sınır 30 gün), sonunda silme değil arşivleme. Uyum tarafının daha uzun bir süre talebi var mı?

---

## 9. Mimari kural uyumu

| Kural | Uyum |
|---|---|
| ORM yok | ✅ SQLAlchemy Core `Table` + `text()`; mevcut modül zaten böyle |
| Tenant literal `company_id=:cid` | ✅ Her sorguda literal; yeni tabloların hepsinde `company_id NOT NULL` |
| Expand-only migration | ✅ Yalnız nullable/default'lu kolon ekleme + yeni tablo; rename/drop yok |
| Decimal para | ✅ Şablon render'ı `format_money_tr`, `float` yok |
| Audit | ✅ Append-only rıza olayları + aktivite kataloğu olayları + actor/zaman (kriter 8) |
| Idempotency | ✅ Mevcut iki katman korunur; çift gönderim sınırı açıkça yazılı (§2.8) |
| Transaction | ✅ `enqueue_*` çağıranın Session'ında, commit etmez; **dış çağrı boyunca DB kilidi tutulmaz** (§3.3) |
| PG/SQLite paritesi | ✅ `TEXT` payload, `ON CONFLICT ... RETURNING`; VARCHAR `due_date` ve consent-worker yarışı için PG testleri (kriter 5, 10) |

---

## 10. Revizyon notu

§0 ve §1 (mevcut durum tespiti, Antigravity 8/8 doğruladı) **hiçbir turda değiştirilmemiştir**.

### rev5 — RETRY_SCHEDULED self-loop normatif

| Değişiklik | Bölüm |
|---|---|
| Geçiş **15b**: `RETRY_SCHEDULED → RETRY_SCHEDULED` ("şimdi dene") kapalı geçiş tablosuna ve §2.6 retry matrisine normatif eklendi. F1 build'i bu geçişi uygulamada gerektirmişti (backoff'taki satırın elle öne çekilmesi); tablo dışı bırakmak "kapalı tablo = kod" sözleşmesini bozuyordu. Aynı kapılar aynen geçerli: onay korunur/üretilmez, `dispatch_armed` şart, rıza yeniden okunur, hash yeniden doğrulanır; yalnız `notifications_dispatch` tetikler | §2.3, §2.6 |

### rev4 — disarmed satır yaşam döngüsü

| Değişiklik | Bölüm |
|---|---|
| **Disarmed satır yaşam döngüsü** normatif olarak eklendi: ayrı "Durdurulanlar" görünümü (kural/zaman/aktör/gerekçe); lease + otomatik retry + kural yeniden-açma **dokunmaz**; saklama sonunda **silinmez, arşivlenir** (90 gün varsayılan, şirket politikası, alt sınır 30 gün; `notifications_archive`, INSERT→DELETE tek transaction, tenant-safe, partili, idempotent, `PROCESSING` hariç, geri taşıma yok); `PENDING` metriği yalnız `dispatch_armed=TRUE`, disarmed **ayrı metrik**; yeniden değerlendirme = `AWAITING_APPROVAL` + yeni açık onay; UI'da "Gönderilmeyecek — kural kapatıldı · tarih · kim" | **§2.9** |
| `disarmed_at` kolonu + `notifications_archive` tablosu migration planına eklendi | §2.2 |
| Uyum tablosuna 10. madde | §7.6 |
| Kabul kriteri **16** | §7.5 |
| Saklama süresi açık sorusu (15) | §8 |

### rev3 — 3 kalan madde + uyum tablosu

| Değişiklik | Bölüm |
|---|---|
| **`dispatch_armed` yaşam döngüsü** — kural kapatma ile disarm aynı transaction'da atomik; `rule_id`+`company_id` tenant-safe; `PROCESSING`'e dokunulmaz; otomatik re-arm **yok** (açık yeniden-onay); actor+zaman+zorunlu gerekçe+`rowcount` audit; UI'da "kuyruktakiler de durduruldu (N satır)" | **§2.9** (yeni) |
| Bayrak `is_enabled` → **`dispatch_armed`** olarak yeniden adlandırıldı (kural tablosundaki `is_enabled` ile karışmasın); `rule_id` kolonu + kural indeksi eklendi | §2.2, §2.4 |
| **`content_hash` 7 adımlı kanonik sıra** — hash şablondan değil **kanonik render edilmiş payload'dan**; kapsam (sınıf, kanal, recipient, template_id, template_version, kanonik subject/body, referans metadata); adım 7 dispatch öncesi yeniden doğrulama; 1.000 TL ≠ 10.000 TL örneği | **§2.5** |
| **İYS erişilemezlik** — provider çağrılmaz; `COMPLIANCE_CHECK_UNAVAILABLE` (genel `FAILED` değil); kontrollü backoff + **devre-kesici**, sınırsız yeniden sorgu yasak; manuel retry de İYS'yi sorgular; olumlu cache yalnız 24 saat içinde geçerli; **`RET` > cache** önceliği; teknik hata asla onay değil | **§4.0** |
| Geçiş tablosuna 16–19 eklendi (`COMPLIANCE_CHECK_UNAVAILABLE` üçlüsü + disarm) | §2.3 |
| Kabul kriterleri 12 → **15** (İYS devre-kesici, disarm atomikliği, kanonik hash) | §7.5 |
| **9 maddelik uyum tablosu** + açık soruların tasarımı değiştirmediğinin gösterimi | **§7.6** (yeni) |

### rev2 — 2 blokaj + 12 kabul kriteri

| Değişiklik | Bölüm |
|---|---|
| **Blokaj 1 —** "İptal her zaman kazanır" iddiası **geri çekildi**; linearization point, kabul edilen yarış penceresi, kilit tutulmadığının açık beyanı, snapshot'ın yalnız audit olduğu | §3.3 |
| **Blokaj 2 —** Kapalı geçiş tablosu (15 geçiş), DB seviyesinde beş koşullu lease yüklemi, retry ucu sınırları, onay geçersizleşmesi (`content_hash`), CAS + atomik alan yazımı | §2.3–§2.7 |
| URL yasağı → **izin-verilen-içerik modeli**, normalize nihai içerik üzerinde, PII-maskeli red audit'i | §5.5 |
| **Mesaj sınıfı zorunlu** + İYS çifte kapı, fail-closed, TTL'li cache, sınıf karışmaz kuralı | §4.0 |
| Şablon değişkenlerine tip/uzunluk sınırı | §4.2 |
| `due_date` için F2 giriş kriteri (dönüşüm kararı + PG regresyon) | §4.3, §7 |
| Onay/dispatch **ayrı yetkiler** + oluşturan kendi batch'ini onaylayamaz | §6 |
| Provider idempotency/duplicate modeli + **NoOp asla `SENT` yazmaz** | §2.8 |
| 12 kabul kriteri, doğrulama karşılıklarıyla | §7.5 |
| İYS'nin F1 blokajı olma koşulu | §7 |
| Yeni açık sorular (11–14) | §8 |

**Sonraki adım:** ChatGPT'ye son delta turu.
