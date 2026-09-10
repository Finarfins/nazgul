# Platform Yönetim Paneli — Keşif ve PR Planı (Büyük Sipariş Adım 5)

**Tarih:** 2026-09-10  
**Başlangıç Commit:** `72cfa09` (`72cfa09 Merge pull request #112 from Finarfins/fix/h18-verify-email-once`)  
**Kapsam:** Salt okunur keşif, ölçümler, güvenlik sınırları ve 2–3 PR'lık uygulama planı.

---

## 1. Mevcut Altyapı Envanteri

### 1.1. Operatör Yetkilendirmesi
- `backend/app/platform_access.py:11-16`: `platform_operator_entries` ortam değişkeni `settings.sungur_platform_operators` değerini virgülle ayrılmış dizgi olarak çözer (`frozenset`).
- `backend/app/platform_access.py:19-26`: `is_platform_operator(user)`: Kullanıcının rolü `"admin"` OLMAK ZORUNDA (`role != 'admin'` ise `False`), kullanıcı kimliği sayısal olmalı ve `hmac.compare_digest` ile ortam değişkenindeki sayısal kimliklerle sabit zamanlı karşılaştırılmalıdır.
- `backend/app/platform_access.py:29-32`: `require_platform_operator(request)`: `request.state.user` üzerinden operatörlük doğrulanır, yoksa HTTP 403 (`"Platform operatörü yetkisi gerekli"`) fırlatır.
- `backend/app/routers/auth.py:539`: `_session_payload` içinde `"is_platform_operator": is_platform_operator(user)` alanı üretilir; `/auth/login` (satır 613), `/auth/refresh` (gövde satır 1048, çerez satır 1117, dekoratör `routers/auth.py:1052`) ve `/auth/me` (satır 1214) oturum yanıtlarında yer alır. Şirket değişimi için ayrı bir uç yoktur, başlık bazlıdır (`X-Company-Id`).

### 1.2. Yetki Kuralları ve Yönlendiriciler
- `backend/app/auth.py:908-911`: `required_permission` fonksiyonunda `/api/platform/backups` öneki `"read"` döner (gerekçe: yönlendirici daha sert olan admin + allow-list kontrolünü uygular, ara katman oturum ve CSRF denetler).
- `backend/app/auth.py:1233`: `GET /api/platform/audit` için açık bir kural tanımlı DEĞİLDİR; genel güvenli metot kuralına düşerek `"read"` alır.
- `backend/app/routers/platform_backups.py:35`: `APIRouter(prefix="/platform/backups")`. Uçlar:
  - `GET ""` (`:95`, dekoratör `:94`): Yedek listesi ve aktif işlem durumu (`list_platform_backups`).
  - `POST ""` (`:106`, dekoratör `:105`): Anlık tam veritabanı yedeği oluşturma (`create_platform_backup`).
  - `GET "/{name}/download"` (`:125`, dekoratör `:124`): Yedek dump dosyasını indirme (`FileResponse`).
  - `POST "/{name}/verify"` (`:142`, dekoratör `:141`): Yedek bütünlük ve şema sürümü doğrulama.
  - `POST "/{name}/restore"` (`:152`, dekoratör `:151`): Geri yükleme (HTTP üzerinden yalnız bakım kipinde; SQLite'ta HTTP kapalıdır `:156-160`, 5.1c ertelenmiştir).
  - *Mimari Kusur* (`:48-52`): `_log` fonksiyonu `log_activity(db, int(request.state.company_id), ...)` çağrısıyla platform olayını çağıranın kiracısına yazar.
- `backend/app/routers/platform_audit.py:32`: `APIRouter(prefix="/platform/audit")`.
  - `GET ""` (`:35-51`): `audit_logs.c.company_id.is_(None)` olan firmasız güvenlik kayıtlarını listeler. Yalnızca `limit` parametresi (1-1000) vardır, başka süzgeç yoktur.
- `backend/app/routers/kiraci_disa_aktarim.py:80`: `APIRouter(prefix="/company/export")`.
  - `GET ""` (`:378`): Aktif firmanın tüm verisini topolojik sırada ZIP olarak akıtır (`auth.py:891` kuralıyla `__admin_only__`).
- `backend/app/routers/kiraci_imha.py:114`: `APIRouter(prefix="/company")`.
  - `POST "/erase"` (`:130`): Aktif firmayı yumuşak imha eder (`companies.is_active=False`, `memberships` silinir, `auth.py:907` kuralıyla `__admin_only__`).
- `backend/app/routers/companies.py`:
  - `GET "/companies"` (`:81`): `user_companies` ile yalnız oturumlu kullanıcının aktif üyeliklerini listeler.
  - `POST "/companies"` (`:86`): Yeni firma açar (yalnız `admin`).
  - `GET "/company-settings"` (`:125`) & `PUT "/company-settings"` (`:157`): Aktif firma ayarları.
- `backend/app/routers/auth.py`:
  - `POST "/auth/register"` (`:643`): Yeni kullanıcı kaydı.
  - `GET "/auth/verify-email"` (`:763`) & `POST "/auth/verify-email"` (`:792`): E-posta doğrulama token tüketimi.
  - `POST "/auth/resend-verification"` (`:831`): IP limitli e-posta doğrulama yeniden gönderimi.
  - `GET "/users"` (`:1282`), `POST "/users"` (`:1303`), `PATCH "/users/{user_id}/status"` (`:1357`): Yalnızca aktif firma kapsamındaki kullanıcılar.
- `backend/app/email_verification.py`: Token üretimi (`:118`), bildirim kuyruklama (`:146`).
- `backend/app/routers/notifications.py`: `GET "/notifications/outbox"` (`:270`), `GET "/notifications/counters"` (`:300`), `POST "/notifications/{id}/retry"` (`:808`). Tamamı `company_id(request)` ile kiracıya bağlıdır.
- `backend/app/routers/push.py:82,117`: `POST/GET /push/devices`. Buradaki `platform` alanı cihaz işletim sistemidir (`"android" | "ios" | "web"`), platform yönetimiyle ilgisi yoktur.

### 1.3. Ön Yüz Durumu
- `frontend/src/` dizininde `/platform` önekli hiçbir sayfa veya rota YOKTUR (`frontend/src/App.tsx`).
- Mevcut tek platform sayfası `frontend/src/pages/Backups.tsx` bileşenidir ve rotası `/yedekler`dir (`frontend/src/navigation.tsx:214`).
- `frontend/src/AuthContext.tsx:24,56`: Ön yüz `/auth/me`den gelen `is_platform_operator` alanını `useState`te tutar ve `can('platform')` kontrolünü buna bağlar.
- `frontend/e2e/rota-envanteri.ts:643-644`: 71 rota arasında `/yedekler` tek platform rotasıdır ve e2e ortamında ortam değişkeni verilmediği için `muaf` olarak işaretlidir.

---

## 2. Tablo Envanteri: Kiracı (120) vs Platform (15)

Veritabanı şeması doğrudan incelendiğinde toplam **135 tablo** bulunmaktadır:
- **Kiracı Tabloları (120 adet):** `tests/test_tenant_scoping_guard.py:41-201` listesindeki `TENANT_TABLES` kümesidir (`company_id` sütunu taşır).
  - Önemli örnekler: `activity_logs`, `idempotency_keys`, `invoices`, `notifications`, `orders`, `push_devices`, `security_audit_logs`, `user_company_memberships`, `whatsapp_context`, `whatsapp_links`, `whatsapp_pairing_codes`, `whatsapp_pending_actions`.
- **Platform / Kiracısız Tablolar (15 adet):**
  1. `alembic_version`: Veritabanı şema göç sürümü.
  2. `app_users`: Global kullanıcı hesapları (`company_id` taşımaz).
  3. `auth_rate_limits`: IP bazlı hız sınırlandırma pencereleri (SEC-6).
  4. `auth_refresh_tokens`: Oturum yenileme belirteçleri.
  5. `auth_tokens`: Erişim oturum belirteçleri.
  6. `companies`: Kiracı üst defteri (`is_active` durumunu taşır).
  7. `email_verification_tokens`: E-posta doğrulama belirteçleri.
  8. `exchange_rates`: TCMB döviz kuru günlük kayıtları.
  9. `login_attempts`: Giriş deneme geçmişi.
  10. `password_reset_tokens`: Parola sıfırlama belirteçleri.
  11. `platform_maintenance`: Bakım kipi durumu.
  12. `schema_migrations`: Eski şema göç kayıtları.
  13. `settings`: Platform genel anahtar/değer ayarları ve kalp atışı.
  14. `whatsapp_inbound`: Eşleşme öncesi gelen ham Meta webhook mesajları (WA1).
  15. `whatsapp_pairing_attempts`: Eşleşme kodu deneme hız sınırı sayaçları (WA1).

---

## 3. Rapor Soruları ve Ölçümler

### S1. Operatör Kimlik Modeli: Ortam Değişkeni vs `platform_operators` Tablosu
- **Mevcut Durum:** `settings.sungur_platform_operators` değişkenini okuyan **3 kod yolu** vardır:
  1. `backend/app/platform_access.py:14`: `platform_operator_entries()` ayrıştırıcısı.
  2. `backend/test_platform_backups.py:56`: `test_non_operator_admin_is_rejected_by_api_guard` testi.
  3. `backend/tests/test_security_audit_visibility.py:460`: `test_platform_yolu_sıradan_yoneticiye_kapali` testi.
- **Değerlendirme:**
  - Panel açılışı (PP1–PP3) için ortam değişkeni modeli **yeterlidir ve daha güvenlidir**: Veritabanı üzerinden yetki yükseltme (privilege escalation) saldırılarına kapalıdır, sunucu ortamı erişimi gerektirir.
  - Ancak dinamik operatör atama/kaldırma, arayüzden operatör daveti ve yetki devri için bir veritabanı tablosu gereklidir.
  - Göç sırası: Geliştirme dalındaki en son göç `20260912_0082`dir. Açık/bekleyen işler `0083` (E4a e-İrsaliye) ve `0084` (SEC-9) numaralarını alacağından, olası bir operatör tablosu göçü **`0085`** olarak planlanmalıdır.
  - **Öneri:** PP1–PP3 aşamasında mevcut env listesiyle devam edilmeli; `0085` göçü isteğe bağlı PP0 olarak geleceğe bırakılmalıdır.

### S2. Kiracı Nöbetçisi Muafiyeti ve `X-Company-Id` Durumu
- **Ara Katman Analizi (`backend/app/main.py:403-474`):**
  - `main.py:403`: `/api` ile başlayan ve `PUBLIC_API`de olmayan istekler yakalanır.
  - `main.py:413-431`: `request.headers.get("x-company-id")` okunur ve biçimi geçerliyse `request.state.requested_company_id`ye atanır.
  - `main.py:465-473`: **HİÇBİR MUAFİYET OLMAKSIZIN** `resolve_company(db, int(user["id"]), requested_company)` çalıştırılır.
  - **KRİTİK BULGU:** `/api/platform/*` yolları için ara katmanda kiracı muafiyeti **YOKTUR**.
    1. Operatör hiçbir aktif firmaya üye değilse `resolve_company` HTTP 403 (`COMPANY_ACCESS_DENIED`) verir; operatör platform uçlarına **erişemez**.
    2. Operatör istekte `X-Company-Id` gönderirse, üyesi olduğu firmalardan biri seçilir ve `request.state.company_id`ye yazılır.
    3. `backend/app/routers/platform_backups.py:49` satırında `_log` fonksiyonu `log_activity(db, int(request.state.company_id), ...)` çağrısı yaptığı için, platform yedek alma olayı o kiracının denetim günlüğüne kaydedilir.
  - **Gerekli Düzeltme:** `main.py` içinde `/api/platform/` öneki kiracı çözümünden muaf tutulmalı (`request.state.company_id = None`), `X-Company-Id` başlığı yoksayılmalı/reddedilmeli ve `platform_backups._log` kiracısız denetim kütüğüne (`security_audit_logs` `company_id=NULL`) yazmalıdır.

### S3. Ekran Bazında Veri İhtiyacı ve Uç Durumu

| Ekran / İşlev | Veri / Eylem | Durum | Mevcut Dosya:Satır | İzin Kuralı | Göç İhtiyacı |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **a. Firmalar** | Liste (aktif/pasif, üye sayısı, son hareket) | **EKSİK** | `companies.py:81` (yalnız üye olunan ve aktifler) | `require_platform_operator` | Yok |
| | Aktif/Pasif yapma | **EKSİK** | Yok (yalnız `/company/erase` pasife alır) | `require_platform_operator` | Yok |
| | İmha (soft-erase) | **VAR (Kısmi)** | `kiraci_imha.py:130` (`POST /api/company/erase`) | `__admin_only__` (kiracı admini) | Yok |
| **b. Kullanıcılar** | Liste (tüm sistem, e-posta, doğrulama, üyelikler) | **EKSİK** | `auth.py:1282` (yalnız tek firma) | `require_platform_operator` | Yok |
| | Kilitle/Kilit aç (`is_active`) | **EKSİK** | `auth.py:1357` (firma admini kilitler) | `require_platform_operator` | Yok |
| | Doğrulama e-postası tekrar tetikleme | **EKSİK** | `auth.py:831` (yalnız genel anonim uç) | `require_platform_operator` | Yok |
| | Parola sıfırlamaya zorlama | **EKSİK** | `auth.py:1329` (yalnız kullanıcı ilk açılışında) | `require_platform_operator` | Yok |
| **c. Doğrulama** | Bekleyen doğrulamalar listesi, son geçerlilik | **EKSİK** | `email_verification.py:118` (iç fonksiyon) | `require_platform_operator` | Yok |
| **d. Outbox Sağlığı** | Bildirim PENDING/FAILED sayaçları (tüm sistem) | **EKSİK** | `notifications/service.py:1138` (tek firma) | `require_platform_operator` | Yok |
| | Kanal bazlı dağılım, en eski bekleyen yaşı | **EKSİK** | `entegrasyon_olaylari.py:308` (stok outbox var, bildirim yok) | `require_platform_operator` | Yok |
| | Yeniden kuyruklama / retry | **EKSİK** | `notifications.py:808` (yalnız tek firma) | `require_platform_operator` | Yok |
| **e. Hız Sınırları** | `auth_rate_limits` bloklanan IP'ler | **EKSİK** | `auth.py:406-435` (tüketim var, okuma yok) | `require_platform_operator` | Yok |
| | IP blokajı temizleme (unblock) | **EKSİK** | Yok | `require_platform_operator` | Yok |
| **f. Yedekler** | Liste, oluşturma, hash doğrulama, indirme | **VAR** | `platform_backups.py:95,106,125,142` (dekoratörler `:94,105,124,141`) | `require_platform_operator` | Yok |
| | Geri yükleme (Restore) | **KAPSAM DIŞI** | `platform_backups.py:152` (Faz 5.1c) | - | - |
| **g. Denetim** | Firmasız olay listesi (`company_id IS NULL`) | **VAR (Kısmi)** | `platform_audit.py:35` (filtresiz, limit var) | `require_platform_operator` | Yok |
| | Olay tipi, IP, tarih, kullanıcı süzgeçleri | **EKSİK** | Yok | `require_platform_operator` | Yok |
| **h. e-Belge Sağlığı** | Firma bazında `einvoice_status` sayaçları | **EKSİK** | `invoices.py:279` (fatura tekil durum sorgusu) | `require_platform_operator` | Yok |
| | test/live ortam bilgisi gösterimi | **EKSİK** | `config.py:141` (`izibiz_env`, `einvoice/endpoints.py:64-66`) | `require_platform_operator` | Yok |

### S4. Güvenlik İncelemesi: Operatör Yetki Sınırları (SEC-3b Uyumu)
- **Asla Yapılamayacaklar:**
  1. Operatörler hiçbir kiracının ticari veri satırlarını (müşteri adları, VKN/TCKN, cari bakiye, fatura kalemleri, fiyatlar, stok hareketleri, hayvan ve tarla kayıtları) toplu veya tekil olarak **GÖREMEMELİDİR**.
  2. Panel ekranlarında müşteri/tedarikçi PII (kişisel veri) bulunmamalı; yalnızca sayısal sayaçlar, hacim metrikleri ve durum dağılımları sunulmalıdır.
  3. Outbox veya entegrasyon hata mesajlarında alıcı telefon/e-posta ve ham SQL/istisna metinleri temizlenmeli (`notifications.py:197` `_mask_recipient` ve `entegrasyon_olaylari.py` `_gerekceyi_arindir` deseni uygulanmalıdır).
  4. E-belge ekranında entegratör kimlik bilgileri asla JSON yanıtına sızdırılmamalı; yalnızca ortam bayrağı (`"test" | "live"`, `config.py:141` `izibiz_env`, `einvoice/endpoints.py:64-66`) ve uç nokta erişilebilirliği gösterilmelidir.
  5. Kiracısız denetim izi (`GET /api/platform/audit`) yalnız oturum öncesi/hatalı denetimleri göstermeli, kiracıya ait `/api/audit` satırlarını içermemelidir.

### S5. Ön Yüz Planı: `/platform` Rota Ağacı
- Ön yüz `AuthContext.tsx` üzerinden `is_platform_operator` değerini zaten almaktadır.
- Yeni bir `/platform` kök rotası açılarak `can('platform')` nöbetçisi arkasına alınmalıdır:
  - `/platform` (Genel Özet / Sağlık Paneli)
  - `/platform/firmalar` (Firma Listesi, Durum ve Üye Sayıları)
  - `/platform/kullanicilar` (Kullanıcı Listesi, Doğrulama Durumu, Hesap Kilitleme)
  - `/platform/kuyruk` (Outbox Sağlığı, Gecikme Metrikleri, Toplu Yeniden Tetikleme)
  - `/platform/guvenlik` (Hız Sınırı Blokları, Temizleme, Firmasız Denetim Günlüğü)
  - `/platform/e-belge` (Fatura Durum Sayaçları, Entegratör Canlılığı)
  - `/platform/yedekler` (Mevcut `/yedekler` sayfası bu ağaca bağlanır, eski rota yönlendirilir)
- UI Deseni: 71 rota keşfinde tespit edilen standart **Liste/Filtre** deseni (`ResponsiveTable`, MUI `DataGrid`, arama/süzgeç çubuğu ve tehlikeli işlemler için çift onaylı onay diyaloğu) yeniden kullanılacaktır.

---

## 4. PR Bölümleme Planı

```
                               ┌────────────────────────────────┐
                               │  PP1: Backend Salt Okunur API  │
                               │  - Platform Ara Katman Düzelt. │
                               │  - 7 Yeni GET Ucu (Göç YOK)    │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │  PP2: Backend Eylemleri & Audit │
                               │  - 7 Yeni Eylem Ucu (POST/DEL) │
                               │  - Güvenlik Kütüğü (Göç YOK)   │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │  PP3: Frontend /platform Ağacı │
                               │  - 7 Yeni MUI Ekranı           │
                               │  - Rota Envanteri (71 -> 77)   │
                               └────────────────────────────────┘
```

### PP1: Backend Salt Okunur Uçlar & Kiracı Muafiyeti
- **Kapsam:**
  1. `backend/app/main.py`: `/api/platform/` istekleri için `resolve_company` muafiyeti, `X-Company-Id` yoksayma, `request.state.company_id = None` kurulumu.
  2. `backend/app/auth.py:908`: `required_permission` içine `path.startswith("/api/platform/")` kuralının eklenmesi (`return "read"`).
  3. `backend/app/routers/platform_backups.py:46`: `_log` fonksiyonunun `company_id=None` ile platform denetimine yazacak şekilde düzeltilmesi.
  4. `backend/app/routers/platform_audit.py`: Süzgeç parametrelerinin eklenmesi (`action`, `ip_address`, `username`, `status_code`, `date_from`, `date_to`).
  5. `backend/app/routers/platform_management.py` (YENİ): 7 adet salt okunur uç:
     - `GET /api/platform/overview`
     - `GET /api/platform/companies`
     - `GET /api/platform/users`
     - `GET /api/platform/verifications`
     - `GET /api/platform/outbox/health`
     - `GET /api/platform/rate-limits`
     - `GET /api/platform/edocuments/health`
- **Sözleşme İğne Deltaları:**
  - `EXPECTED_OPERATION_COUNT`: **392 → 399** (+7 GET)
  - `EXPECTED_PATH_COUNT`: **301 → 308** (+7 yol)
  - `EXPECTED_AUTHENTICATED`: **379 → 386** (+7)
  - `EXPECTED_READ`: **80** (Değişmez; 7 uç `GUARDED_READ`e eklendiği için çıplak `read` sabit kalır)
  - `GUARDED_READ_OPERATIONS`: **24 → 31** (`test_authorization_population_reconciliation.py:658` doğrular, 7 uç korumalı okuma kümesine girer)
  - `EXPECTED_UNDENIABLE`: **97** (Değişmez)
  - `EXPECTED_GET_PERMISSIONS`: **186 → 193** (+7)
  - `EXPECTED_SECURITY_FINGERPRINT`: Güncellenir.
- **Etkilenen Dosyalar:** `main.py`, `auth.py`, `platform_backups.py`, `platform_audit.py`, `platform_management.py` (yeni), ilgili 4 test sözleşmesi dosyası.
- **Göç:** YOK.
- **Tahmini Boyut:** ~400 satır backend + ~350 satır test.

### PP2: Backend Yönetim Eylemleri & Denetim Kayıtları
- **Kapsam:**
  1. `backend/app/routers/platform_management.py` içine 7 yönetim eylemi:
     - `POST /api/platform/companies/{id}/activate` (Pasif firmayı açma)
     - `POST /api/platform/companies/{id}/deactivate` (Firmayı dondurma)
     - `POST /api/platform/users/{id}/status` (Global hesap kilitleme/açma)
     - `POST /api/platform/users/{id}/resend-verification` (Operatör tarafından aktivasyon e-postası tetikleme)
     - `POST /api/platform/users/{id}/force-password-reset` (Zorunlu parola rotasyonu ve oturum düşürme)
     - `DELETE /api/platform/rate-limits` (IP blokajı kaldırma)
     - `POST /api/platform/outbox/retry` (Başarısız bildirimleri toplu yeniden kuyruklama)
  2. Her eylem için `security_audit_logs` tablosuna `company_id=None` ve `action="platform.*"` ile değişmez denetim kütüğü yazımı.
- **Sözleşme İğne Deltaları:**
  - `EXPECTED_OPERATION_COUNT`: **399 → 406** (+7 işlem)
  - `EXPECTED_PATH_COUNT`: **308 → 314** (+6 yeni yol; `/api/platform/rate-limits` PP1'de açılmıştı, DELETE işlemi eklenir)
  - `EXPECTED_AUTHENTICATED`: **386 → 393** (+7)
  - `EXPECTED_READ`: **80** (Değişmez; POST ve DELETE işlemleridir)
  - `EXPECTED_UNDENIABLE`: **97** (Değişmez)
  - `EXPECTED_GET_PERMISSIONS`: **193** (Değişmez)
  - `EXPECTED_SECURITY_FINGERPRINT`: Güncellenir.
- **Etkilenen Dosyalar:** `platform_management.py`, ilgili test sözleşmesi dosyaları.
- **Göç:** YOK.
- **Tahmini Boyut:** ~350 satır backend + ~350 satır test.

### PP3: Ön Yüz `/platform` Yönetim Paneli Ekranları
- **Kapsam:**
  1. `frontend/src/App.tsx`: `/platform/*` rota ağacının tanımlanması.
  2. `frontend/src/navigation.tsx` & `AppShell.tsx`: `can('platform')` ile koşullu "Platform Yönetimi" sol menü kümesi.
  3. Yeni Sayfalar (`frontend/src/pages/platform/`):
     - `PlatformDashboard.tsx` (Özet göstergeler, kuyruk sağlık sinyalleri, hızlı sayaçlar)
     - `PlatformCompanies.tsx` (Firma listesi, filtreler, dondurma/açma diyaloğu)
     - `PlatformUsers.tsx` (Kullanıcı listesi, kilit aç/kapa, parola sıfırlama, doğrulama gönderme)
     - `PlatformOutbox.tsx` (Kanal kuyrukları, en eski yaş, yeniden deneme)
     - `PlatformSecurity.tsx` (Hız sınırı blokajları, IP açma, denetim günlüğü)
     - `PlatformEDocuments.tsx` (Firma fatura durumları, test/live ortam bayrağı)
  4. Eski `/yedekler` rotasının `/platform/yedekler` altına taşınması (eski rotaya yönlendirme bırakılarak).
  5. `frontend/e2e/rota-envanteri.ts`: 6 yeni rotanın eklenmesi (**71 → 77 rota**; e2e ortamı gereği `muaf` olarak gerekçelendirilir).
- **Etkilenen Dosyalar:** `App.tsx`, `navigation.tsx`, `AppShell.tsx`, 6 yeni sayfa dosyası, `rota-envanteri.ts`, `rota-kapsam-sozlesmesi.test.ts`.
- **Göç:** YOK.
- **Tahmini Boyut:** ~850 satır TSX + ~150 satır test.

### PP0 (İsteğe Bağlı / Gelecek Aşama): Operatör Veritabanı Tablosu
- **Gerekçe:** Ortam değişkeni yerine arayüzden operatör ekleme/çıkarma yeteneği istenirse.
- **Göç Numarası:** **`0085`** (0083 E4a ve 0084 SEC-9 sonrası).
- **Tablo:** `platform_operators(id, user_id FK -> app_users.id, created_at, created_by, is_active, notes)`.
- **Durum:** PP1-PP3'ü BLOKE ETMEZ. Berkay onayına bağlı ayrı bir iş paketi.

---

## 5. Berkay İçin Açık Kararlar

1. **Operatör Kimlik Kaynağı (Env List vs `platform_operators` Tablosu):**
   - *Seçenek A (Önerilen):* PP1–PP3 aşamasında mevcut `SUNGUR_PLATFORM_OPERATORS` ortam değişkeniyle devam edilsin. Sıfır göç riski taşır, veri tabanı açıklarına karşı tam koruma sağlar.
   - *Seçenek B:* Operatör tablosu (`0085` göçü) hemen açılsın. Arayüzden operatör yönetilebilir ancak migration bağımlılığı ekler.
2. **Firma Dondurma vs Yumuşak İmha Semantiği:**
   - 5.1b'deki `POST /api/company/erase` firmanın tüm üyeliklerini siler ve `is_active=False` yapar. Üyelikler silindiği için firma tekrar aktif edilse bile sahipsiz kalır.
   - *Karar:* Platform operatörüne üyeliği silmeden geçici askıya alma ("Dondurma" — `is_active=False`) yetkisi verilmeli midir? (Tavsiye: Evet; geçici borç/ödeme kilitleri için dondurma ayrılmalıdır).
3. **Kullanıcı Parola Sıfırlama Yetkisi:**
   - Operatör bir kullanıcının parolasını doğrudan belirleyebilmeli mi, yoksa yalnızca `must_change_password=True` bayrağını kaldırıp e-postasına tek kullanımlık sıfırlama linki mi tetiklemelidir? (Tavsiye: Güvenlik gereği operatör parolayı asla doğrudan girmemeli, yalnızca bağlantı tetiklemelidir).

---

## 6. En Önemli 3 Bulgu

1. **Kiracı Kapsamı Kaçağı / Eksik Muafiyet (`main.py:465`):**
   Mevcut sistemde `/api/platform/*` yolları ara katmandaki `resolve_company` kontrolünden muaf tutulmamıştır. Bir platform operatörünün hiçbir firmaya üyeliği yoksa platform uçlarına HTTP 403 alarak erişemez; üye olduğu bir firma varsa platform yedeği denetim kaydı yanlışlıkla o kiracının yerel aktivite tablosuna (`activity_logs`) yazılmaktadır.
2. **Ön Yüzde Operatör Bayrağı Zaten Hazır (`AuthContext.tsx:24,56`):**
   `/auth/me` uç noktası `is_platform_operator` bayrağını zaten döndürmekte ve React `AuthContext` bunu `can('platform')` fonksiyonuna bağlamaktadır. Ön yüzde yetki altyapısı mevcuttur; yalnızca `/platform` rota ağacı ve sayfaları eksiktir.
3. **Sıfır Göç İle Tam Panel Mümkündür (0 Göç):**
   Panelin ihtiyaç duyduğu tüm platform verileri (`companies`, `app_users`, `email_verification_tokens`, `auth_rate_limits`, `notifications`) mevcut şemada eksiksiz bulunmaktadır. PP1, PP2 ve PP3 paketlerinin hiçbiri veritabanı göçü (migration) gerektirmemekte, sıfır riskle devreye alınabilmektedir.
