# RC Kanıt Zinciri — Gerçek PostgreSQL 16 + Compose Temiz Başlangıç

**Koşum tarihi:** 2026-08-03
**Kod:** `develop@cff45b5` (Merge PR #216) — canlıdaki commit ile aynı
**Veritabanı:** PostgreSQL **16.4** (portable, port 5433), gerçek sunucu — SQLite ikizi değil

Bu belge, RC kapılarında "gerçek PostgreSQL 16 üzerinde koşuldu" diye işaretlenen
maddelerin **fiilî çıktısını** kayda geçirir. Daha önce bu kapılar açıktı çünkü
testler yazılmıştı ama koşum kanıtı belgelenmemişti.

---

## 1. PostgreSQL 16 parity suite — 63/63

Koşum modeli CI'ın matrix davranışını taklit eder: **her test dosyası kendi taze
veritabanına** karşı koşar (`DROP DATABASE ... WITH (FORCE)` + `CREATE DATABASE`).
Bu şart, çünkü dosyalar tek DB'yi paylaşır ve erkenci bir dosya admin parolasını
döndürerek sonraki dosyanın login'ini `KeyError: 'companies'` ile düşürür.

Her koşum migration zincirini **sıfırdan** `20260730_0041`'e kadar uygular; yani
63 ayrı **clean install + upgrade rehearsal** demektir.

| | |
|---|---|
| Toplam dosya | **63** |
| Geçen | **63** |
| Düşen | **0** |
| Süre | ~4.5 dk (+ iki düzeltilmiş dosya) |

İlk koşumda 2 dosya düştü; **ikisi de ortam eksikliğiydi, dialect hatası değil**:

| Dosya | Kök neden | Çözüm |
|---|---|---|
| `test_api_contract_postgresql.py` | `ModuleNotFoundError: jsonschema` | venv'e `jsonschema` kuruldu → **1 passed** |
| `test_platform_backups_postgresql.py` | `pg_dump bulunamadı` | `PATH`'e PG16 `bin` eklendi → **9 passed** |

> `jsonschema` backend bağımlılık kilidinde yer alıyorsa yerel venv güncel değildi;
> yer almıyorsa sözleşme testinin bağımlılığı olarak eklenmesi gerekir. Bu, bu
> koşumun ortaya çıkardığı ayrı bir takip maddesidir.

**Dialect bulgusu yok.** SQLite'ta geçip PG'de düşen tek bir test olmadı — bu
suite'in var oluş nedeni olan hata sınıfı bu koşumda görülmedi.

## 2. pg_dump / pg_restore provası

`test_platform_backups_postgresql.py` → **9 passed**, içinde
`test_pg_dump_and_pg_restore_smoke` gerçek PG 16.4 ikilileriyle koştu
(`pg_dump` + `pg_restore`, `C:/pgdl/extracted/pgsql/bin`).

Kapattığı kapılar:
- `QUALITY_GATES_V2_9_BACKUP_RECONCILIATION.md` → "Gerçek PostgreSQL 16 üzerinde pg_dump/pg_restore provası"
- `QUALITY_GATES_V2_9_MIGRATION_DECIMAL.md` → "Yedek alma ve geri yükleme provası"

## 3. Docker Compose temiz başlangıç smoke

`docker compose -p sungur-smoke up -d --build` — sıfırdan imaj, sıfırdan volume,
canlı ortama hiç dokunmadan izole proje adıyla.

| Kontrol | Sonuç |
|---|---|
| Build | ✅ `sungur-smoke-app` (Scout raporu: 117 MB, 200 paket) |
| `db` sağlığı | ✅ healthy |
| `app` sağlığı | ✅ healthy |
| `GET /api/ready` | ✅ **200** — `{"status":"ready","database":"ok"}` |
| Alembic (temiz kurulum) | ✅ `20260730_0041 (head)` — canlıyla aynı |
| Kullanıcı | ✅ **non-root** — `uid=100(app) gid=101(app)`, `User=app` |
| Kök dosya sistemi | ✅ `ReadonlyRootfs=true`; `/` yazma denemesi *Read-only file system* ile reddedildi |
| `/tmp` | ✅ tmpfs, yazılabilir (beklenen) |
| Yetenekler | ✅ `CapDrop=[ALL]` |
| Ayrıcalık yükseltme | ✅ `no-new-privileges:true` |
| Zafiyet taraması | ✅ `docker scout cves` — **0C / 0H / 0M / 0L**, 200 paket indekslendi, "No vulnerable package detected" |

Ortam sonrasında `down -v` ile tamamen kaldırıldı (konteyner + ağ + volume).

Kapattığı kapılar:
- `QUALITY_GATES_V2_9_MIGRATION_DECIMAL.md` → "Docker Compose temiz başlangıç smoke testi"
- `RC_PRODUCTION_CHECKLIST.md` → non-root / read-only imaj doğrulaması

### 3.1 Bu smoke'un ortaya çıkardığı gerçek kusur

İlk deneme **build aşamasında komple düştü**:

```
ERROR: invalid file request backend/.venv-sandbox/bin/python
failed to solve: invalid file request backend/.venv-sandbox/bin/python
```

Kök neden: `.gitignore:71` `backend/.venv-sandbox/` dizinini tanır, ama
`.dockerignore` yalnızca `**/.venv` içeriyordu — `.venv-sandbox` bu kalıba
**uymaz**. Dizin build context'e giriyor ve içindeki kırık symlink context
yüklemesini düşürüyordu.

CI'da görünmez, çünkü orada checkout temizdir. Ama bu dizine sahip **herhangi bir
geliştirici makinesinde `docker compose build` çalışmaz**. Kapı açık kaldığı için
bugüne kadar yakalanmamıştı.

Düzeltme: `.dockerignore` → `**/.venv` yerine `**/.venv*`.

---

## 4. Üretim kopyası provası (2026-08-03, izole)

> **Veri hiç dışarı çıkmadı.** Prova prod kutusunda, **ayrı bir docker ağında**
> (`prova-net`) yapıldı. Geçici veritabanı canlı ağa hiç bağlanmadı — yanlış
> `DATABASE_URL` ile canlıya migration koşma riski fiziksel olarak imkânsızdı.
> Canlı veritabanına **tek bir yazma bile** yapılmadı; kopya `pg_dump` ile
> salt-okunur alındı. İş sonunda geçici konteyner, ağ ve tüm dump/çıktı
> dosyaları silindi; canlı üç konteyner boyunca `healthy` kaldı.

### 4.1 pg_restore round-trip — gerçek üretim verisiyle

| | |
|---|---|
| Kaynak | canlı `pg_dump --format=custom` (498 KB, 17 MB veritabanı) |
| Restore | **hatasız** |
| Tablo sayısı | **91** — canlıyla birebir |
| Alembic sürümü | `20260730_0041` — canlıyla birebir |
| Mutabakat kapsamı | **90 numeric kolon, 27 917 satır** (27 189 non-null) |

### 4.2 `alembic upgrade head` — no-op doğrulaması

Üretim kopyasında `upgrade head` **hiçbir migration çalıştırmadı**; şema zaten
head'de. Migration zincirinin üretim verisiyle tutarlı olduğu doğrulandı.

### 4.3 Geri alınamaz revizyon — fail-closed, gerçek veriyle

Kopyada 11 adet `service_fee` satırı vardı. `alembic downgrade -1` denendi:

```
RuntimeError: 0041 downgrade refused: service_fee receivable rows must be
archived explicitly before removing their schema
```

Sürüm `20260730_0041`'de kaldı. Yani koruma teorik değil — **gerçek üretim
verisiyle tetiklendi ve tuttu.**

### 4.4 REAL/Float → NUMERIC provası — asıl bulgu

> **Kapsam — yanlış okunmasın:** üretim veritabanında **REAL kolon YOKTUR**.
> Tüm finansal kolonlar hâlihazırda `NUMERIC(18,2)/(18,4)`. Aşağıdaki ölçüm bir
> **varsayımsal senaryonun canlandırılmasıdır**: "bu veri float'ta saklansaydı ne
> olurdu?" Manifestteki 90 kolon üretim kopyasında `REAL`'e düşürülüp yeniden
> `NUMERIC`'e alındı. Yani bulgu "prod'da şu kadar REAL bulundu" değil, "prod
> verisi float'tan geçirilseydi şu kadar kayıp kalıcı olurdu"dur.

Üç snapshot `compare_numeric_snapshots` ile karşılaştırıldı.

| Geçiş | Parasal fark |
|---|---|
| **T0 → TR** (NUMERIC → REAL) | **5** kolon |
| **TR → T1** (REAL → NUMERIC) | **33** kolon |
| **T0 → T1** (tam tur) | **33** kolon |

Tam turdaki sapmalardan bazıları (mutlak tutarlar değil, **fark** değerleri):

| Kolon | Sapma |
|---|---|
| `customers.opening_balance.sum` | **−0,92** |
| `orders.subtotal.sum` | **+0,89** |
| `orders.final_total.sum` / `grand_total.sum` | **−0,35** |
| `order_items.line_subtotal.sum` | +0,28 |
| `products.purchase_price.max` | +0,13 |
| `customers.opening_balance.max` | −0,10 |

Tüm numeric kolonların toplamında net sapma: **−0,29**.

#### Kayıp nerede oluşuyor — düzeltilmiş yorum

Bu belgenin önceki sürümü *"dönüşüm tek yönlü kayıplıdır"* diyordu. **Bu ifade
teknik olarak yanlıştı ve düzeltildi.**

Doğrusu: **kayıp dönüşümde değil, veri `REAL`'e yazıldığı anda oluşur.**
`REAL → NUMERIC` dönüşümü sadık bir kopyalamadır — float'ın taşıdığı ikili
değeri ondalığa çevirir; kaybı ne artırır ne geri getirir. "Tek yönlü" nitelemesi
doğru ama sebebi farklıdır: geri dönülemez, çünkü **orijinal ondalık değer zaten
yoktur**, `REAL`'e yazıldığı anda silinmiştir.

Aynı nedenle "sapma 5 kolondan 33 kolona yayıldı" cümlesi de yanlış okumaydı:
`TR → T1` farkının bir bölümü dönüşümden değil, **iki farklı okuma yolundan**
gelir — TR anlık görüntüsü float'ı Python'da `Decimal(str(x))` ile iki haneye
yuvarlar, T1 ise PostgreSQL'in `numeric(18,2)` cast'ini kullanır. Asıl kanıt
değeri `T0 → T1` (tam tur) satırındadır.

#### Somut örnek — mekanizmanın kendisi (PG 16.4)

Kolon bazlı toplamlar sapmanın büyüklüğünü verir ama nedenini göstermez; bu
tablo gösterir. `real_hali` sütununa dikkat: kuruş **oraya varmadan önce**
kaybolmuştur.

| Değer | `::real` | `::real::numeric(18,2)` | Fark |
|---|---|---|---|
| `0.07` | `0.07` | `0.07` | 0.00 |
| `1234.56` | `1234.56` | `1234.56` | 0.00 |
| `113276.10` | `113276.1` | `113276.00` | **−0.10** |
| `1056830.71` | `1.0568308e+06` | `1056830.00` | **−0.71** |
| `7162702.62` | `7.1627025e+06` | `7162700.00` | **−2.62** |

Küçük tutarlar sağlam kalır; `REAL`'in ~7 anlamlı basamak sınırı aşıldığında
kuruş bütünüyle silinir. Ölçekle büyüyen bir hatadır — ve tam olarak cari
bakiyesi ile sipariş toplamlarının yaşadığı ölçektir.

**Sonuçlar:**

1. **NUMERIC kararı üretim verisiyle doğrulandı.** Bu veri float'ta saklansaydı
   cari açılış bakiyesi ve sipariş toplamları kuruş düzeyinde kayardı —
   mutabakatı bozan, sessiz bir sapma.
2. **Kayıp geri getirilemez.** Migration'ın kusuru değil; float'ta saklanmış bir
   değeri hiçbir dönüşüm eski haline döndüremez.
3. **Operasyonel sonuç:** finansal kolonlarda `NUMERIC` → `REAL` yönünde bir
   downgrade **asla güvenli değildir** (17 anlamlı basamak üstü kesilir, ikili
   gösterim hatası girer, toplama sırasına göre değişen sonuçlar üretir); geri
   dönüş yolu migration değil, **yedekten geri yükleme** olmalıdır.

Kapattığı kapılar:
- `QUALITY_GATES_V2_9_MIGRATION_DECIMAL.md` → üretim kopyasında REAL/Float → NUMERIC provası; migration öncesi/sonrası finansal toplam mutabakatı
- `QUALITY_GATES_V2_9_BACKUP_RECONCILIATION.md` → gerçek legacy REAL veri setinde Alembic + finansal mutabakat provası
- `RC_PRODUCTION_CHECKLIST.md` → geri alınamaz revizyonların belgelenmesi (fail-closed kanıtı)

---

## Hâlâ açık kalanlar (bu koşum kapatmaz)

Dürüstlük için: aşağıdakiler bu kanıt zincirinin **dışında**dır.

| Madde | Neden açık |
|---|---|
| Legacy test karantinasının modern pytest'e taşınması | Ayrı bir iş kalemi |
| Oturum açmayı gerektiren frontend smoke'ları | Login/refresh/logout akışı, V3 uçtan uca, Lighthouse — kimlik bilgisi gerektirir |
| Rollout onayı, rollback sahibi, owner imzaları | İnsan kararı — teknik koşumla kapatılamaz |

> CI kanıtı: PR #228 GitHub Actions'ta **6/6 yeşil** (backend-postgresql,
> backend-quality, container, contract-drift, e2e, frontend) — `MIGRATION_DECIMAL`
> gate'indeki "CI'ın gerçek GitHub çalıştırmasında yeşil doğrulanması" maddesi
> bu koşumla karşılanır.
>
> Frontend kapıları ayrı bir turda ölçüldü; bkz. `RC_FRONTEND_DEPLOYMENT_CHECKLIST.md`.

**Sonuç:** Gerçek PostgreSQL 16 kanıt zinciri backend tarafında **kapandı** —
parity suite, clean install + upgrade rehearsal, `pg_dump`/`pg_restore`
round-trip ve üretim kopyası üzerinde REAL→NUMERIC provası dâhil.

Repo seviyesinde *production-ready* etiketi için kalan boşluk artık teknik
değil, **operasyonel**: oturum gerektiren frontend smoke'ları ve insan
imzaları. Bir istisna teknik olarak kayda değer — 4.4'teki bulgu gereği
finansal kolonlarda `NUMERIC → REAL` yönünde downgrade **yasak** kabul
edilmeli; geri dönüş yolu yedekten geri yüklemedir.

---

## Yeniden üretme

PG16 suite (her dosya taze DB):

```bash
bash scripts/run_pg16_parity.sh
```

Compose smoke:

```bash
docker compose -p sungur-smoke --env-file <yerel-env> up -d --build
```
