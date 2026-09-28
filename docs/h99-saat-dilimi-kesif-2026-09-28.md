# Hijyen H99 — Saat Dilimi Politikası ve "TSİ" Etiketi — KEŞİF

**Ölçüm tarihi:** 2026-09-28 · **Taban:** `origin/develop` = `9a6d7e3` (#183 birleşmesi) · **Worktree:** `F:\nazgul-agy-q13` · **Dal:** `docs/h99-saat-dilimi-kesif` · **Kapsam:** SALT OKUMA keşif. Uygulama kodu, göç, test, şema ve rota DEĞİŞMEDİ.

Her sayı bu tabanda ölçüldü ve `dosya:satır` ile yazıldı. Ölçülemeyen her iddia **DOĞRULANMADI** etiketi taşır (Ek). Bütün `app/`, `alembic/` ve `tests/` yolları `backend/` altındadır.

**Ölçüm düzeneği (depoya GİRMEDİ, scratchpad'de):** `scratch/probe_sale_period.py`, `scratch/check_all_probes.py`, `scratch/check_vat.py` ile izole SQLite tohumu ve API sondaları koşuldu; geçici test veritabanı ölçüm bittikten sonra silindi. PG :5433 portu ortamda PostgreSQL/Docker servisi çalışmadığı için canlı koşulamadı, SQLite ölçüldü, PG davranışı H73/H94 ikiz testleri ve kod analiziyle belgelendi (Ek).

---

## 0. YÖNETİCİ ÖZETİ — beş cümlelik sonuç

1. **İş tarihi arka uçta `app/business_time.py` (`business_today()`, `business_now()`) ile İstanbul'a bağlanmış olsa da dört modülde tehlikeli kaçak `date.today()` ve `datetime.utcnow()` çağrıları bulunmaktadır.** `routers/late_fees.py:61` (`as_of` varsayılanı — `default_factory=date.today` parantezsiz), `routers/cek_senetler.py:569` (ciro tarihi), `routers/supplier_prices.py:2001` (TCMB kur günü) ve `app/crm.py:90,118,150,160` (müşteri etkileşimleri) doğrudan sunucu saatini kullanmakta; EC2 sunucusu UTC çalıştığından 00:00–03:00 TSİ arasında dünkü takvim gününü seçmektedir. (`backend/app` genelinde `date.today` taraması 5 satır verir: 3 gerçek çağrı ve 2 yorum satırı).
2. **Veritabanı şemasında zaman damgaları Alembic genelinde `%100` `DateTime(timezone=True)` (188 sütun) olarak bildirilmiştir; ancak SQLite ile PostgreSQL'in tel biçimi ve oturum davranışı asimetriktir.** PostgreSQL `TIMESTAMPTZ` sütunlarını oturum saat diliminde (`Europe/Istanbul` ayarlandıysa `+03:00`) döndürürken, SQLite SQLAlchemy sürücüsü saat dilimi ekini düşürmekte ve istemciye naive `datetime` (`...T13:47:14.612096`) göndermektedir; fatura (`routers/invoices.py`) ve WhatsApp (`routers/whatsapp.py`) modülleri bu sorunu `app/zaman.py` (`utc_iso` / `zamanlari_iso`) ile, irsaliye modülü ise `_utc(...).isoformat()` (:259, :1474) ile çözmüş fakat genel API'ye yaymamıştır.
3. **Satış, alış ve iade belgelerinin iş tarihi (`order_date`, `purchase_date`, `return_date`) metin (`String(30)`) saklanırken, servis faturası (`created_at`) ve müstahsil makbuzu (`issued_at`) UTC zaman damgası saklamaktadır.** Canlı probda ölçüldüğü üzere (`2026-07-31 22:30 UTC` = `01:30 TSİ 1 Ağustos`), `orders` tablosundaki bir satış `order_date` değerine göre Temmuz veya Ağustos'a düşerken, servis faturası ve müstahsil makbuzu `app/muhasebe/kaynak.py` içindeki ay-başı UTC aralığı (`_UtcAn`) sayesinde Ağustos'a yazılmakta ve `G5` uyarısı üretmektedir.
4. **Ön yüzde tarih biçimlendiricilerin tamamına yakını tarayıcının yerel saat dilimine bağımlıdır ve tüm depoda "TSİ" / "TSI" etiketi tam olarak SIFIR kez geçmektedir.** İncelenen 17 ön yüz tarih biçimlendirme noktasından yalnızca biri (`frontend/src/pages/platform/ortak/bicim.ts:8`) `{timeZone: 'Europe/Istanbul'}` zorlamakta; geri kalan 16 çağrı noktası (`Audit`, `ActivityLog`, `Backups`, `Companies`, `Dashboard`, `FieldWorkOrders`, `Insights`, `Notifications`, `Pos`, `SupplierPrices`, `TransferDetail`, `Users`, `NotificationConsentPanel`, `farmApi`), 7 yerel gün kurucu (`EntityStatementDialog`, `farmApi`, `AnimalDetail`, `HerdBreeding`, `HerdHealth`, `HerdYields`) ve `utils/tarih.ts::yerelGun` kullanıcının tarayıcı saatine düşmektedir. Bu durum somut bir hataya yol açmaktadır: SQLite üzerinde `GET /api/audit` naive `created_at` dönerken, `Audit.tsx` içindeki `new Date(v)` bunu tarayıcı yerel saati olarak okumakta ve 3 saatlik kaymaya (3 h off) neden olmaktadır.
5. **Kapsamlı UI yeniden tasarımı öncesinde net bir saat dilimi sözleşmesi şarttır:** Veritabanında tüm anlar UTC saklanmalı, iş günü kararları istisnasız `app/business_time.py` üzerinden Europe/Istanbul takvimine bağlanmalı, API telinde `*_at` alanları UTC ISO-8601 (`+00:00` / `Z`) zorlanmalı ve ön yüzde saat gösterilen her alana `TSİ` soneki eklenmelidir.

---

## 1. BACKEND YAZMA ZAMANI VE SAAT KAYNAKLARI

### 1.1 Sayımlar ve Kullanım Dağılımı

`backend/app` kaynak kodunda yapılan AST ve düzenli ifade taraması sonuçları:

| Saat Fonksiyonu / İfade | `app/` Sayısı | `alembic/` Sayısı | `tests/` Sayısı | Açıklama |
|---|---|---|---|---|
| `datetime.now(timezone.utc)` | **49** | 3 | 105 | Kanonik UTC damga yazıcı (`app/activity_log.py:397`, `app/payment_allocation_engine.py:483` vb.). Ölçüm: `git grep -F -o "datetime.now(timezone.utc)" 9a6d7e3 -- backend/app | wc -l` (49), `git grep -F -o "datetime.now(timezone.utc)" 9a6d7e3 -- backend/tests | wc -l` (105), `backend/alembic` (3). |
| `datetime.now(UTC)` | **2** | 0 | 0 | `app/migrations.py:27`, `app/routers/absorption.py:357` (Python 3.11+ `UTC` alias) |
| Naive `datetime.now()` | **0** | 0 | 0 | `app/` altında çıplak argümansız `datetime.now()` YOKTUR. |
| `datetime.utcnow()` | **4** | 0 | 0 | **KUSUR:** `app/crm.py:90, 118, 150, 160` (Python 3.12'de deprecated, naive UTC döner) |
| `date.today` | **5 satır (3 gerçek + 2 yorum)** | 0 | 3 satır (1 gerçek + 2 yorum) | **KUSUR:** Ölçüm: `git grep -n "date\.today" 9a6d7e3 -- backend/app` → 5 satır: 3 gerçek çağrı (`app/routers/late_fees.py:61` — `default_factory=date.today` parantezsiz, `app/routers/cek_senetler.py:569`, `app/routers/supplier_prices.py:2001`), 2 yorum satırı (`herd_vaccine_schedule.py:7`, `parti.py:20`). |
| `func.now()` | **0** | 19 | 0 | Yalnızca Alembic DDL tablolarında varsayılan ifade olarak kullanılmış. |
| `CURRENT_TIMESTAMP` | **9** | 19 | 7 | `app/notifications/schema.py:141,147,231,238,268,274,301,323,324` server_default. |
| `ZoneInfo("Europe/Istanbul")` | **2** | 0 | 0 | `app/business_time.py:24` (kanonik tanım), `app/einvoice/edespatch.py:462` (açıklama). |
| `from ..business_time import ISTANBUL` | **5** | 0 | 0 | `muhasebe/kaynak.py:51`, `service_receivable_engine.py:13`, `uretici_kayit_defteri.py:61`, `routers/farm.py:59`, `whatsapp/ciftci_yurutucu.py:130`. |
| `business_now()` | **4** | 0 | 4 | `app/business_time.py:27`, `app/labels.py:101`, `app/routers/analytics.py:119`. |
| `business_today()` | **37** | 0 | 6 | `app/business_time.py:32`, `entity_detail.py`, `dashboard.py`, `reports.py`, `pos.py`, `transactions.py` vb. |

### 1.2 Kaçak Saat Kullanımları (Ciddi Kusurlar)

`backend/test_istanbul_business_date.py:85-105` test dosyası, belirli iş günü modüllerinde çıplak `date.today()` kullanımını yasaklamaktadır. Ancak aşağıdaki dosyalar o denetim listesine dahil edilmediği için gözden kaçmıştır:

1. **`app/routers/late_fees.py:61`**:
   ```python
   as_of: date = Query(default_factory=date.today)
   ```
   *Etki:* Gecikme faizi önizleme ucu `/api/late-fees/preview`, `as_of` parametresi verilmediğinde sunucunun sistem saatini alır (`default_factory=date.today` parantezsiz çağrısı). EC2 UTC saatinde çalışırken gece 00:00–03:00 TSİ arasında `date.today()` henüz önceki gündür. Faiz hesabı 1 gün eksik hesaplanır.
2. **`app/routers/cek_senetler.py:569`**:
   ```python
   degerler["endorsed_date"] = payload.endorsed_date or date.today()
   ```
   *Etki:* Çek ciro işleminde tarih boş geçilirse sunucu UTC gününü yazar.
3. **`app/routers/supplier_prices.py:2001`**:
   ```python
   store_rates(db, rates, _date.today())
   ```
   *Etki:* TCMB kur bülteni indirilirken sunucu UTC günüyle kaydedilir. (Bu dosya `test_istanbul_business_date.py:79` içinde `_TODAY_ALLOWLIST`e alınmış fakat düzeltilmemiştir).
4. **`app/crm.py:90, 118, 150, 160`**:
   ```python
   created_at = datetime.utcnow().isoformat(timespec='seconds')
   completed_at = datetime.utcnow().isoformat(timespec='seconds') if status == 'completed' else None
   ```
   *Etki:* Naive UTC metni üretir; ISO zaman damgası sonunda saat dilimi bilgisi (`Z` veya `+00:00`) bulunmaz.

### 1.3 İş Tarihi Kararları Tablosu

İş mantığındaki kritik tarih kararları ve kullandıkları saat kaynakları:

| İş Kararı | Saat Kaynağı | Dilim | Dosya:Satır | Notlar |
|---|---|---|---|---|
| Muhasebe Ay/Dönem Sınırı (`Donem.bas_an`, `son_an`) | `datetime(..., tzinfo=ISTANBUL).astimezone(timezone.utc)` | **Europe/Istanbul** | `app/muhasebe/kaynak.py:97-106` | Ay başı TSİ 00:00, UTC karşılığı (yazın 21:00 UTC) ile filtrelenir. |
| Satış / Alış / İade Dönem Filtresi | `d.bas_tarih.isoformat()` (String karşılaştırma) | **Yerel Gün** | `app/muhasebe/kaynak.py:283, 337, 391` | `orders.order_date >= 'YYYY-MM-01'` metin süzgeci. |
| Servis Faturası Dönem Filtresi | `d.bas_an` (UTC timestamp) | **Europe/Istanbul** | `app/muhasebe/kaynak.py:528-536` | `invoices.created_at >= d.bas_an`. |
| Müstahsil Makbuzu Dönem Filtresi | `d.bas_an` (UTC timestamp) | **Europe/Istanbul** | `app/muhasebe/kaynak.py:647-668` | `producer_receipts.issued_at >= d.bas_an`. |
| KDV Özeti G5 Uyarısı | `an.strftime("%Y-%m") != d.metin` | **Karşılaştırma** | `app/muhasebe/kaynak.py:590-597, 733-741` | UTC ayı ile TSİ ayı farklıysa belge TSİ ayına yazılır ve G5 uyarısı verilir. |
| Yaşlandırma Raporu Varsayılan Tarihi | `report_date = as_of or business_today()` | **Europe/Istanbul** | `app/routers/reports.py:332` | `business_today()` çağrılır. |
| Yaşlandırma Kovası Hesabı | `overdue_days = (as_of - due_date).days` | **Takvim Günü** | `app/routers/reports.py:83-93` | Gün farkı tam sayı olarak hesaplanır. |
| Alacak Motoru Kapsam Filtresi | `effective_date <= as_of`, `period_end <= as_of` | **Takvim Günü** | `app/receivables_engine.py:156, 481, 494` | `as_of` gününe kadar olan hareketler. |
| Dashboard Gecikmiş Alacaklar | `today = business_today()`, `due_date < today` | **Europe/Istanbul** | `app/routers/dashboard.py:128, 133` | Vadesi bugünden önce olanlar listelenir. |
| Gecikme Faizi `as_of` Varsayılanı | `Query(default_factory=date.today)` | **UTC (KUSUR)** | `app/routers/late_fees.py:61` | EC2 sunucusunun UTC gününü alır! |
| Gecikme Faizi Başlangıcı | `due_date + timedelta(days=grace_days + 1)` | **Takvim Günü** | `app/late_fee_engine.py:72` | Vadeye gün eklenir. |
| Servis Alacağı Vade Türetimi | `completed_at.astimezone(ISTANBUL).date() + term` | **Europe/Istanbul** | `app/service_receivable_engine.py:92-102` | Tamamlanma UTC anı açıkça İstanbul gününe çevrilip vade eklenir. |
| Çek Ciro Tarihi Varsayılanı | `payload.endorsed_date or date.today()` | **UTC (KUSUR)** | `app/routers/cek_senetler.py:569` | `date.today()` sunucu saatini alır. |
| Çek Tahsilat Tarihi Varsayılanı | `payload.odeme_tarihi or business_today()` | **Europe/Istanbul** | `app/routers/cek_senetler.py:444` | `business_today()` kullanılır. |
| Çek Cari Olay Günü | `olay = business_today()` | **Europe/Istanbul** | `app/cek_senet_cari.py:210` | `business_today()` kullanılır. |
| Tahsis Gelecek Tarih Engeli | `if effective_date > business_today():` | **Europe/Istanbul** | `app/payment_allocation_engine.py:1542, 1568` | İleri tarihli tahsis reddedilir. |
| Cari Hesap Ekstresi Bitiş Tarihi | `current = today or business_today()` | **Europe/Istanbul** | `app/statement.py:154` | `business_today()` kullanılır. |
| Üretici Kayıt Defteri Olay Günü | `_yerel_gun = an.astimezone(ISTANBUL).date()` | **Europe/Istanbul** | `app/uretici_kayit_defteri.py:160-172` | Gece yarısı kırılmasını önlemek için İstanbul gününe çevrilir. |
| Etiket Basım Zamanı | `moment = self.printed_at or business_now()` | **Europe/Istanbul** | `app/labels.py:101` | `business_now()` kullanılır. |
| POS Satış Günü | `transaction_date = business_today().isoformat()` | **Europe/Istanbul** | `app/routers/pos.py:323` | `business_today()` kullanılır. |

---

## 2. VERİTABANI SAKLAMA TİPLERİ VE OTURUM DİLİMİ

### 2.1 Tablo ve Sütun Tipleri

Alembic migrasyonlarında ve `core_schema.py` üzerinde yapılan inceleme:

- **Zaman Damgaları (Timestamps):** Alembic sürümlerinde `DateTime(timezone=True)` tam **188** kez tanımlanmıştır. Buna karşın çıplak `DateTime()` veya `DateTime(timezone=False)` sayısı **0**'dır. Bütün zaman damgaları şema seviyesinde saat dilimli tasarlanmıştır.
- **Tarihler (Dates):** Alembic sürümlerinde `sa.Date()` **42** kez kullanılmıştır (`effective_date`, `vade`, `due_date_normalized`, `period_start`, `period_end` vb.).
- **Metin Tarihler (Legacy String Dates):** `20260712_0000_schema_baseline.py` ile gelen ilk tablolar tarihleri `String(30)` veya `String(40)` olarak saklamaktadır:
  - `orders.order_date`: `String(30)`
  - `orders.due_date`: `String(30)` (Ayrıca `20260728_0035_notifications_f2.py` ile `due_date_normalized DATE` eklenmiştir).
  - `purchases.purchase_date`: `String(30)`
  - `purchases.due_date`: `String(30)` (`due_date_normalized` bu tabloda YOKTUR).
  - `payments.payment_date`: `String(30)`
  - `stock_movements.movement_date`: `String(30)`
  - `quotes.quote_date`: `String(30)`
  - `returns.return_date`: `String(30)`
  - `financial_transactions.txn_date`: `String(30)`

### 2.2 SQLite vs PostgreSQL Davranış Farkı

`scratch/test_sqlite_roundtrip.py` ile canlı test edilmiştir:
1. **SQLite Davranışı:**
   - SQLAlchemy SQLite lehçesinde `DateTime(timezone=True)` sütununa `datetime.now(timezone.utc)` (ör. `2026-09-28 13:50:13.005230+00:00`) yazıldığında, veritabanına saat dilimi uzantısı kırpılarak `'2026-09-28 13:50:13.005230'` metni yazılır.
   - Geri okunduğunda SQLAlchemy nesnesi `val.tzinfo = None` (NAIVE) olarak döner.
   - Bu durum `_UtcAn` (`app/muhasebe/kaynak.py:124-148`) sınıfında belgelenmiştir: SQLite üzerinde string karşılaştırmasında `+` karakteri `.` karakterinden önce geldiği için milisaniye/saat dilimi karşılaştırmalarında satır kaybı yaşanmaktadır.
2. **PostgreSQL Davranışı:**
   - `DateTime(timezone=True)` sütunu PostgreSQL'de yerel `TIMESTAMPTZ` tipine eşlenir.
   - PostgreSQL depolamayı daima dahili UTC mikro-saniye olarak yapar; ancak istemciye dönerken bağlantının o anki `TimeZone` oturum parametresine göre dönüştürerek verir.

### 2.3 PostgreSQL Oturum Dilimi (Session TimeZone)

Uygulama ve test ortamlarının `TimeZone` yapılandırması:
- **Uygulama Çalışma Zamanı (`app/db.py:23-31`):**
  PostgreSQL için `connect_args = {"connect_timeout": int(settings.db_connect_timeout_seconds)}` verilmektedir. **Uygulama bağlantı açarken `timezone` parametresi VERMEMEKTEDİR.** Dolayısıyla üretimde PostgreSQL oturumu sunucu/RDS varsayılanı olan `UTC` diliminde çalışır (eğer ortamda `PGOPTIONS` yoksa).
- **Test ve CI Ortamı (`tests/pg_ikiz_yardimci.py:21-42` ve `conftest.py:13-30`):**
  `PG_OTURUM_DILIMI = "Europe/Istanbul"` olarak sabitlenmiştir. `conftest.py` modülü yüklenirken `os.environ["PGOPTIONS"] = pg_secenekleri(...)` ile ortama `-c timezone=Europe/Istanbul` eklenir. `_h94_baglanti_dilimi` dinleyicisi her yeni bağlantıda `SHOW TimeZone` sorgusuyla dilimin İstanbul olduğunu doğrular.
- **H94 Kapısı (`tests/test_h94_pg_dilim_secenekleri.py`):**
  PostgreSQL ikiz testlerinde `options` veya `PGOPTIONS` parametresini `pg_secenekleri` yardımcısı dışında kuran her kodu AST düzeyinde kırmızı yakar.

---

## 3. API TEL BİÇİMİ (WIRE FORMAT)

### 3.1 Durum Analizi: Üç Farklı Tel Biçimi

Bugün backend API uçlarının yanıtlarında üç farklı zaman damgası serileştirme biçimi bulunmaktadır:

1. **`app/zaman.py::utc_iso` / `zamanlari_iso` Biçimi (`+00:00` uzantılı ISO):**
   `routers/invoices.py` ve `routers/whatsapp.py` (:760, :761) modüllerinde doğrudan `utc_iso` / `zamanlari_iso` kullanılır; `routers/despatch_notes.py` modülü ise `_utc(...).isoformat()` (:259, :1474) aracılığıyla aynı `+00:00` ISO biçimini üretir. Duyarlılık korunur, sonuna `+00:00` eklenir:
   `"2026-09-24T21:22:20.054785+00:00"`.
2. **`app/activity_log.py::_iso_utc` Biçimi (`Z` uzantılı ISO):**
   `app/activity_log.py:500` içinde `return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")` kullanılır:
   `"2026-09-28T13:47:14Z"`.
3. **Ham Naive ISO Biçimi (Saat Dilimi Bilgisi YOK):**
   FastAPI/Pydantic'in doğrudan ORM satırını sözlüğe çevirdiği yerlerde (`app/routers/auth.py:1411` `/api/audit`):
   SQLite üzerinde: `"2026-09-28T13:47:14.612096"` (ne `Z` ne `+00:00` vardır!).
   PostgreSQL üzerinde (test ortamında): `"2026-09-28T16:47:14.612096+03:00"` (oturum dilimi olan İstanbul ile döner!).

### 3.2 Temsili 5 Uç Nokta Ölçümü (TestClient Kanıtları)

`scratch/probe_wire_format.py` ve `scratch/probe_5_endpoints.py` ile canlı API üzerinden ölçülmüştür:

| Uç Nokta | Dönen Zaman Alanı | Ölçülen Tel Değeri | Durum / Biçim |
|---|---|---|---|
| `GET /api/invoices/{id}` | `created_at` | `"2026-07-31T22:30:00+00:00"` | `+00:00` ISO (Kanonik `zamanlari_iso`) |
| `GET /api/despatch-notes/{id}` | `issue_date`, `created_at` | Tarih `"YYYY-MM-DD"`, damga `"+00:00"` | `+00:00` ISO (`zaman.utc(...).isoformat()`) |
| `GET /api/audit?limit=1` | `created_at` | `"2026-09-28T13:47:14.612096"` | **NAIVE (KUSUR: Saat dilimi yok!)** |
| `GET /api/activity-logs?limit=1` | `timestamp` | `"2026-09-28T13:47:14Z"` | `Z` sonekli UTC ISO |
| `GET /api/accounting/vouchers` | `fis_tarihi`, `uretim_zamani` | `"2026-08-01"`, `"2026-09-28T13:51:02.290+00:00"` | Takvim günü metin, damga `+00:00` ISO |

---

## 4. FRONTEND BİÇİMLENDİRİCİLERİ VE "TSİ" ETİKETİ

### 4.1 Tüm Biçimlendiriciler Listesi (17 Çağrı Noktası: 1 Istanbul + 16 Tarayıcı-Yerel)

`frontend/src` altındaki tüm `.ts` ve `.tsx` dosyaları taranmış; sayısal biçimlendirmeler (`Number(x).toLocaleString('tr-TR')`) ayıklanarak 17 tarih/zaman biçimlendirici çağrı noktası tespit edilmiştir (1 Europe/Istanbul zorlamalı + 16 tarayıcı yerel saati):

| Dosya ve Satır | Biçimlendirici Çağrısı | Saat Dilimi Davranışı |
|---|---|---|
| `pages/platform/ortak/bicim.ts:8` | `new Date(deger).toLocaleString('tr-TR', {timeZone: 'Europe/Istanbul'})` | **EUROPE/ISTANBUL ZORLAMALI (Tek Örnek)** |
| `pages/Audit.tsx:3` | `new Date(v).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/ActivityLog.tsx:55` | `parsed.toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Backups.tsx:54` | `new Date(activeOperation.started_at).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Backups.tsx:58` | `new Date(item.created_at).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Companies.tsx:75` | `new Date(log.created_at).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Dashboard.tsx:128` | `new Intl.DateTimeFormat('tr-TR', {dateStyle: 'full'}).format(new Date())` | **Tarayıcı Yerel Saati** |
| `pages/FieldWorkOrders.tsx:152` | `new Date(ek.created_at).toLocaleDateString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/FieldWorkOrders.tsx:161` | `new Date(access.meta.lastSyncAt).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Insights.tsx:17` | `new Date(data.generated_at).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Notifications.tsx:47` | `parsed.toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Pos.tsx:193` | `new Date().toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/SupplierPrices.tsx:29` | `new Intl.DateTimeFormat('tr-TR', {dateStyle: 'short', timeStyle: 'short'}).format(...)` | **Tarayıcı Yerel Saati** |
| `pages/TransferDetail.tsx:19` | `parsed.toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `pages/Users.tsx:29` | `new Date(v).toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `components/NotificationConsentPanel.tsx:26` | `parsed.toLocaleString('tr-TR')` | **Tarayıcı Yerel Saati** |
| `farm/farmApi.ts:409` | `t.toLocaleString('tr-TR', {dateStyle: 'short', timeStyle: 'short'})` | **Tarayıcı Yerel Saati** |

### 4.2 `utils/tarih.ts` ve Tarayıcı-Yerel "Bugün" Kurucuları (7 Nokta)

`frontend/src/utils/tarih.ts` dosyasındaki `yerelGun` fonksiyonu incelendiğinde:
```typescript
export function yerelGun(value?:string|null):string{
 if(!value)return '-';
 const metin=String(value);
 if(YALNIZ_TARIH.test(metin))return metin;
 const an=new Date(metin);
 if(Number.isNaN(an.getTime()))return metin.slice(0,10);
 return `${an.getFullYear()}-${iki(an.getMonth()+1)}-${iki(an.getDate())}`;
}
```
Yorum satırında "H73: fatura uçları *_at alanlarını UTC ISO-8601 yazar... İstanbul'da 00:00-03:00 arası kesilen fatura önceki günde görünürdü... YEREL günü yazar" denmektedir. Ancak kod `an.getFullYear()`, `an.getMonth()` ve `an.getDate()` çağrılarını yaparak **tarayıcının yerel saat dilimini** kullanmaktadır. Eğer tarayıcı Londra'da (UTC) veya Tokyo'da (UTC+9) ise fatura günü kullanıcının kendi saatine kayar; İstanbul iş günü elde edilemez.

`yerelGun` fonksiyonunun yanı sıra, ön yüzde doğrudan tarayıcı yerel saatiyle bugünün ISO tarihini oluşturan **7 adet tarayıcı-yerel "bugün" kurucusu (today builder)** tespit edilmiştir:
1. `components/EntityStatementDialog.tsx:18` (`isoDate = (value: Date) => ...`, `todayIso = () => isoDate(new Date())`)
2. `farm/farmApi.ts:416` (`todayIso = () => { const now = new Date(); return ...; }`)
3. `pages/AnimalDetail.tsx:59` (`bugunIso = () => { const now = new Date(); return ...; }`)
4. `pages/HerdBreeding.tsx:65` (`bugunIso = () => { const now = new Date(); return ...; }`)
5. `pages/HerdHealth.tsx:56` (`bugunIso = () => { const now = new Date(); return ...; }`)
6. `pages/HerdYields.tsx:64` (`bugunIso = () => { const now = new Date(); return ...; }`)
7. `pages/HerdYields.tsx:72` (`gunOnce = (n: number) => { const d = new Date(); ...; return ...; }`)

Bu 7 kurucu da kullanıcının cihaz saatini baz almakta, gece 00:00–03:00 TSİ arasında Türkiye dışındaki tarayıcılarda iş gününü yanlış güne bağlamaktadır.

**Somut Hata / Sapma Örneği (Lens Tarafından Doğrulanan Sonuç):**
SQLite üzerinde `GET /api/audit` uç noktası `created_at` alanını naive timestamp olarak döner (ör. `"2026-09-28T13:47:14.612096"` — ne `Z` ne de `+00:00` saat dilimi eki vardır). `Audit.tsx:3` sayfasındaki `valueFormatter: v => new Date(v).toLocaleString('tr-TR')` kodu bu naive metni tarayıcının yerel saat diliminde ayrıştırır (`new Date("2026-09-28T13:47:14.612096")` ECMAScript standartlarına göre yerel zaman olarak kabul edilir). Kullanıcı Türkiye'de (UTC+3) olsa bile, sunucunun UTC 13:47'de kaydettiği an (TSİ 16:47 olması gerekirken) doğrudan yerel 13:47 olarak ekrana basılır ve **tam 3 saatlik kayma (3 h off)** oluşur.

### 4.3 "TSİ" / "TSI" Etiketi Taraması

Depodaki tüm dosyalarda `TSİ` ve `TSI` kısaltması taranmıştır:
- `git grep -n "TSİ" 9a6d7e3`: **7** ham satır eşleşmesi verir; ancak bu 7 satırın tamamı kelime içi Türkçe eklerdir (`İŞARETSİZ`, `KİLİTSİZ`, `HASSASİYETSİZLİK` vb.; bağımsız bir etiket veya saat dilimi kısaltması DEĞİLDİR).
- `git grep -n -w "TSI" 9a6d7e3`: **0** satır eşleşmesi verir.
- **Etiket Sayısı (Label Count):** **0 (SIFIR)** adet.
  - Backend hata/uyarı/log mesajlarında saat dilimi etiketi: **0 (SIFIR)** adet.
  - Frontend arayüz etiketlerinde, tablo başlıklarında, chip ve tooltip'lerde: **0 (SIFIR)** adet.
- **Sonuç:** Kod tabanında bugün hiçbir kullanıcı arayüzünde veya API mesajında saat dilimini açıkça belirten bir "TSİ" ibaresi / etiketi yer almamaktadır.

---

## 5. GECE YARISI KIRILMA SONDASI (PROBES)

### 5.1 Düzeneğin Tanımı

Ölçüm anı: `2026-07-31 22:30:00 UTC` = **`2026-08-01 01:30:00 +03:00` (İstanbul)**.
Dört farklı belge tipiyle taze bir SQLite veritabanında test edilmiştir (`scratch/probe_sale_period.py`, `scratch/check_all_probes.py`):
- **Belge A (`orders` - UTC günü):** `order_date = "2026-07-31"`, tutar 1200 TL (1000 matrah + 200 KDV).
- **Belge B (`orders` - TSİ günü):** `order_date = "2026-08-01"`, tutar 1200 TL (1000 matrah + 200 KDV).
- **Belge C (`invoices` - Servis Faturası):** `created_at = "2026-07-31 22:30:00+00:00"`, tutar 1200 TL (1000 matrah + 200 KDV).
- **Belge D (`producer_receipts` - Müstahsil):** `issued_at = "2026-07-31 22:30:00+00:00"`, brüt 1000 TL, net 980 TL.

### 5.2 Canlı Ölçüm Sonuçları

#### 1. KDV Özeti (`/api/accounting/vat-summary?period=...`)
- **2026-07 (Temmuz):**
  - Hesaplanan KDV: **200.00 TL** (Yalnızca Belge A: `orders` `order_date="2026-07-31"`).
  - Belge C (Servis faturası) ve Belge D (Müstahsil) Temmuz'a **GİRMEDİ**.
  - Uyarılar: `[]` (Boş).
- **2026-08 (Ağustos):**
  - Hesaplanan KDV: **400.00 TL** (Belge B: `orders` 200 TL + Belge C: Servis Faturası 200 TL).
  - Müstahsil: Brüt 1000 TL, Stopaj 20 TL, Net 980 TL (Belge D).
  - **Üretilen Uyarılar:**
    - `G5 (SERVIS_FATURA)`: *"Servis faturası INV-STRADDLE-01 UTC'ye göre 2026-07, İstanbul saatine göre 2026-08 — İSTANBUL ayına yazıldı"*
    - `G5 (MUSTAHSIL)`: *"Müstahsil makbuzu MM-STRADDLE-01 UTC'ye göre 2026-07, İstanbul saatine göre 2026-08 — İSTANBUL ayına yazıldı"*
- **Önemli Bulgular:**
  `orders` tablosu string `order_date` üzerinden süzüldüğü için sisteme o an UTC günü yazılmışsa Temmuz'a düşer; fakat servis faturası ve müstahsil makbuzu UTC timestamp taşıdığı ve `app/muhasebe/kaynak.py` ay başını İstanbul anına göre (`2026-07-31 21:00:00 UTC`) açtığı için Ağustos'a düşer! Aynı saatte kesilen iki farklı satış belgesi iki ayrı aya dağılabilir!

#### 2. Kanonik Fişler (`/api/accounting/vouchers?period=...`)
- **2026-07 (Temmuz):**
  - 1 fiş: `Fis: SAT-1` | Tarih: `2026-07-31` | Kaynak: `SATIS`.
- **2026-08 (Ağustos):**
  - 3 fiş:
    - `Fis: SAT-2` | Tarih: `2026-08-01` | Kaynak: `SATIS`.
    - `Fis: SVF-INV-STRADDLE-01` | Tarih: `2026-08-01` | Kaynak: `SERVIS_FATURA` (`an.astimezone(ISTANBUL).date()`).
    - `Fis: MM-MM-STRADDLE-01` | Tarih: `2026-08-01` | Kaynak: `MUSTAHSIL` (`_istanbul_gunu(...)`).

#### 3. Rapor Özeti (`/api/reports/summary?date_from=...&date_to=...`)
- `date_from=2026-07-01&date_to=2026-07-31`: Satış toplamı **1200.00 TL** (Belge A).
- `date_from=2026-08-01&date_to=2026-08-31`: Satış toplamı **1200.00 TL** (Belge B).
- *Not:* Servis faturaları ve müstahsil makbuzları `/api/reports/summary` satış toplamına girmez (yalnızca `orders` tablosuna bakar).

#### 4. Alacak Yaşlandırma (`/api/reports/receivables-aging?as_of=...`)
- `as_of = 2026-07-31`:
  - Belge A (`due_date="2026-07-31"`): Kova = **`not_due`** (`overdue_days = 0`).
  - Belge B (`order_date="2026-08-01"`): Gelecek tarihli olduğu için rapora **GİRMEDİ**.
  - Toplam risk: **1200.00 TL** (`not_due`).
- `as_of = 2026-08-01`:
  - Belge A (`due_date="2026-07-31"`): Kova = **`days_1_30`** (`overdue_days = 1` — Vadesi geçmiş!).
  - Belge B (`due_date="2026-08-01"`): Kova = **`not_due`** (`overdue_days = 0`).
  - Toplam risk: **2400.00 TL** (1200 TL vadesi geçmiş `days_1_30`, 1200 TL `not_due`).
- *Etki:* 1 günlük tarih kayması, alacağı "vadesi gelmemiş" kovasından doğrudan "gecikmiş alacak" kovasına fırlatmaktadır.

#### 5. Dashboard Gecikmiş Alacaklar (`/api/dashboard`)
- `dashboard.py:128` `today = business_today()` alır ve `due_date < today` olanları listeler.
- 1 Ağustos TSİ gününde `ORD-JUL-UTC` (vadesi 31 Temmuz) **gecikmiş alacak** olarak panoya düşer; `ORD-AUG-IST` (vadesi 1 Ağustos) gecikmiş sayılmaz.

---

## 6. POLİTİKA ÖNERİSİ (POLICY PROPOSAL)

UI yeniden tasarımı öncesinde mimari ve sözleşme netliği için önerilen 4 ayaklı kural seti:

1. **Veritabanı Saklama (Storage) İlkesi:**
   - **Teknik Zaman Damgaları:** `created_at`, `updated_at`, `*_at` alanları veritabanında daima UTC saklanır (PostgreSQL'de `TIMESTAMPTZ`, SQLite'ta `+00:00` ekli ISO metni).
   - **İş / Belge Tarihleri:** `order_date`, `purchase_date`, `due_date`, `movement_date`, `receipt_date` gibi takvim günleri saat dilimi taşımaz; veritabanında `Date` (veya `YYYY-MM-DD` standart ISO String) saklanır (Mevcut `String(30)` sütunlarının `Date` tipine dönüştürülmesi **GÖÇ GEREKTİRİR**).
2. **Arka Uç İş Tarihi (Business Logic) İlkesi:**
   - İş takvimi, dönem sınırları, vadeler, gecikme faizleri ve rapor kesim tarihleri için **TEK SAAT KAYNAĞI `app/business_time.py`** modülüdür.
   - Hiçbir iş modülü çıplak `date.today()` veya `datetime.utcnow()` çağıramaz; tüm takvim kararları `business_today()` ve `business_now()` (Europe/Istanbul) üzerinden yürütülür.
3. **API Tel Biçimi (Wire Format) İlkesi:**
   - API yanıtlarında dönen bütün zaman damgaları istisnasız **ISO-8601 UTC** (`+00:00` veya `Z`) biçiminde serileştirilir.
   - Naive (saat dilimi olmayan) zaman damgalarının API'den çıkması engellenir.
   - Takvim günleri daima `YYYY-MM-DD` döner.
4. **Ön Yüz ve "TSİ" Gösterim İlkesi:**
   - Ön yüzde tarayıcının yerel saatine bağımlı `toLocaleDateString` / `toLocaleString` çağrıları yasaklanır.
   - Merkezi bir yardımcı (`src/utils/tarih.ts`) kurulur: tüm anları `Europe/Istanbul` saatine çevirir.
   - **"TSİ" Etiketi Sözleşmesi:** Arayüzde yalnızca tarih gösterilen yerlerde (`15.08.2026`) ek etiket aranmaz; ancak **saat veya zaman damgası gösterilen her yerde** kullanıcıya netlik sağlamak için `TSİ` soneki eklenir (Örnek: `15.08.2026 14:30 TSİ`).

---

## 7. PR BÖLÜMLEME PLANI VE PİN DELTALARI

Politikanın hayata geçirilmesi için önerilen 3 dilimli PR haritası:

### Dilim 1: Backend Kaçak Saat Temizliği ve Kapı Genişletmesi
- **Kapsam:**
  - `app/routers/late_fees.py:61` içindeki `date.today` -> `business_today`
  - `app/routers/cek_senetler.py:569` içindeki `date.today` -> `business_today`
  - `app/routers/supplier_prices.py:2001` içindeki `_date.today` -> `business_today`
  - `app/crm.py:90,118,150,160` içindeki `datetime.utcnow` -> `datetime.now(timezone.utc)`
  - `backend/test_istanbul_business_date.py::_BUSINESS_DATE_MODULES` listesine `late_fees.py` ve `cek_senetler.py` eklenmesi; `_TODAY_ALLOWLIST`in boşaltılması.
- **Pin Deltaları:**
  - `backend/test_istanbul_business_date.py` (+ `backend/test_istanbul_business_date_postgresql.py` ikizi): modül listesi 19 -> 21 (`backend/tests/` altında DEĞİL, `backend/` kökündedir).
  - `backend/tests/test_tenant_scoping_guard.py` AST parmak izleri: `late_fees.py` bu korumada taranmaz (0 eşleşme); yalnızca `supplier_prices.py` (:493) pini güncellenir (sayı 271 sabit kalır).

### Dilim 2: API Tel Biçimi Normalizasyonu
- **Kapsam:**
  - `app/routers/auth.py:1411` (`list_audit`) ve diğer açık uçların `zamanlari_iso` / `utc_iso` süzgecinden geçirilmesi.
  - FastAPI JSON response encoder seviyesinde `datetime` serileştirmesinin UTC ISO-8601'e sabitlenmesi.
- **Pin Deltaları:**
  - Rota sayıları ve yetki pinleri DEĞİŞMEZ.

### Dilim 3: Ön Yüz Merkezi Tarih-Saat Formatlayıcısı ve TSİ Etiketi
- **Kapsam:**
  - `frontend/src/utils/tarih.ts` genişletilir: `formatTarih(val)`, `formatTarihSaatTsi(val)` eklenir (Europe/Istanbul kilitli).
  - 16 tarayıcı-yerel biçimlendirme noktası (14 sayfa ve bileşendeki `toLocaleString`/`Intl.DateTimeFormat` çağrıları) ile 7 tarayıcı-yerel "bugün" kurucusu (`EntityStatementDialog`, `farmApi`, `AnimalDetail`, `HerdBreeding`, `HerdHealth`, `HerdYields`) ve `utils/tarih.ts::yerelGun` bu yardımcıya taşınır.
  - Gerekli zaman damgalı tablolara (`Users`, `Backups`, `Companies`, `FieldWorkOrders`) `TSİ` etiketi yerleştirilir.
- **Pin Deltaları:**
  - Frontend testleri ve e2e testleri güncellenir.

---

## 8. AÇIK KARARLAR (K1..K6) — ORKESTRATÖR İÇİN

- **K1: SQLite'ta Timestamp Saklama:** SQLite'ta `DateTime(timezone=True)` sütunlarının SQLAlchemy tarafından tzinfo'suz okunmasını engellemek için `app/db.py` içinde global bir `TypeDecorator` kuralı konulmalı mı, yoksa mevcut `app/zaman.py::utc_iso` okuma katmanı yeterli mi?
- **K2: Belge Tarihi vs Timestamp Standartlaşması:** Servis faturası (`invoices`) ve müstahsil makbuzuna (`producer_receipts`) `orders` tablosundaki gibi bağımsız bir `document_date: Date` kolonu eklenmeli mi (**GÖÇ GEREKTİRİR**), yoksa UTC timestamp'ten İstanbul günü türetme kuralı (`astimezone(ISTANBUL).date()`) korunmalı mı?
- **K3: API Wire Format Formatı:** UTC serileştirmesinde `+00:00` mi yoksa `Z` son eki mi resmi kanonik kabul edilecek? (Mevcut kodda `zaman.py` `+00:00`, `activity_log.py` `Z` kullanmaktadır).
- **K4: "TSİ" Etiketinin Kapsamı:** TSİ ibaresi yalnızca masaüstü detay sayfalarında mı yer almalı, yoksa mobil kartlarda yer tasarrufu için tooltip veya dipnot olarak mı tutulmalı?
- **K5: e2e Testlerinde Saat Dilimi:** Playwright testleri halihazırda `isletme-tarihi-saat-dilimi.spec.ts` ile UTC ve İstanbul projelerini koşmaktadır. Tüm frontend e2e süitinde varsayılan tarayıcı dilimi UTC olarak mı kilitlenmeli?
- **K6: Backlog İzni:** `supplier_prices.py:2001` TCMB kurları için `business_today()` mi yoksa bültenin resmi yayım tarihi mi kullanılmalı?

---

## EK: DOĞRULANMADI LİSTESİ

Aşağıdaki ölçümler çalışma ortamındaki eksiklikler veya sınırlandırmalar nedeniyle doğrudan test edilememiş, kod analizi ve ikiz test kayıtlarıyla belgelenmiştir:

1. **`DOĞRULANMADI: PostgreSQL :5433 Canlı Sonda`**:
   Yerel çalışma ortamında Docker Desktop / PostgreSQL 5433 servisi çalışmadığı için §5 sondaları PostgreSQL üzerinde canlı çalıştırılamamıştır. Davranış, `tests/test_h73_fatura_zaman_bicimi.py` ve `tests/test_h94_pg_dilim_secenekleri.py` AST ve ikiz kayıtlarına dayanılarak çıkarılmıştır.
2. **`DOĞRULANMADI: e-Defter ve GİB Portalında TSİ Etiketi Kabulü`**:
   e-Fatura / e-Arşiv XML çıktılarında (UBL-TR) zaman damgalarının `TSİ` eki taşıyamayacağı (UBL standardında `Time` alanının `HH:MM:SS` olması gerektiği) bilinmektedir; ancak dış sistem entegrasyonu canlı ortamda doğrulanmamıştır.
