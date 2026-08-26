# Claude için bağımsız inceleme özeti

Bu dosya bir başarı iddiası değil, bağımsız saldırgan inceleme kapsamıdır. Her maddeyi gerçek kod, migration ve test üzerinden yeniden doğrula.

## Güncel checkpoint

- Sprint: V2.9 Production Readiness / Firma Politikaları + Veri Bütünlüğü Hardening
- Backend: FastAPI + SQLAlchemy + Alembic
- Local: SQLite
- Production hedefi: PostgreSQL 16 + Docker Compose
- Frontend: React 19 + TypeScript + MUI + Vite
- Production-ready: HAYIR — gerçek PostgreSQL çalışma ve veri migration provası bekleniyor

## Bu checkpointte uygulandığı iddia edilenler

- Uygulama başlangıcında Alembic revision kontrolü ve isteğe bağlı otomatik upgrade
- PostgreSQL migration yarışına karşı advisory lock; artık legacy initializers + Alembic dahil tüm bootstrap fazını kapsıyor
- Alembic'in uygulama tarafından yönetilen bağlantıyı kullanabilmesi
- `/api/health` içinde migration revision durumu
- SQLite için foreign keys, WAL, busy timeout ve synchronous ayarları
- Merkezi Decimal para/miktar yardımcıları
- Runtime finansal Pydantic modellerinde ve hesaplama yollarında binary float temizliği
- Para alanları için `Numeric(18,2)`, miktarlar için `Numeric(18,4)` hedefi
- Atomik depo stok güncellemesi ve non-negative guard
- Tenant bazlı atomik belge numarası sayacı (`document_sequences`, Alembic 0002)
- Stok ve workflow işlemlerinde tenant IDOR koruması
- `/api/live` ve `/api/ready`
- Non-root Docker image; read-only filesystem, capability drop ve no-new-privileges
- PostgreSQL 16 servisli GitHub Actions CI
- Eski import-time smoke scriptlerinin deterministik pytest paketinden açıkça ayrılması
- Firma bazlı negatif stok ve kredi/risk limiti politikaları (`block`, `manager_override`, `allow`)
- Gerekçeli yönetici istisnası ve `policy_override_logs` denetim kaydı
- Negatif stok politikasının satış, workflow, ürün, manuel/toplu stok, Excel ve depo transferi yüzeylerine yayılması
- Teklif/sipariş/satış/irsaliye dönüşümlerinde atomik ve idempotent işlem akışı
- `AUTO_MIGRATE=false` + stale schema durumunda fail-fast başlangıç

## Doğrulanmış yerel test sonucu

- Backend aktif testleri izole gruplarda: 46 passed
- PostgreSQL gerektiren: 5 skipped
- Frontend: 6/6 passed
- TypeScript + Vite production build: passed

Bu sonuçları yeniden çalıştır. PostgreSQL testlerinin atlanmasını başarı olarak yorumlama.

## Özellikle saldırgan gözle kontrol et

1. Runtime migration sıralaması doğru mu; legacy `initialize_*()` çağrıları Alembic ile çakışıyor mu?
2. Advisory lock bağlantı kopması, hata ve çoklu replica senaryolarında güvenli şekilde bırakılıyor mu?
3. `AUTO_MIGRATE=false` iken stale revision fail-fast uygulaması tüm başlangıç ve replica senaryolarında güvenilir mi?
4. Numeric migration mevcut PostgreSQL kolon tipleri, null değerler, defaultlar, indexler ve constraintler üzerinde veri kaybı yaratabilir mi?
5. Migration büyük tablolarda uzun exclusive lock doğurur mu; expand/contract veya online migration gerekir mi?
6. Downgrade'in bilinçli olarak engellenmesi deploy/rollback prosedürüyle uyumlu mu?
7. Decimal değerler SQLite sürücüsünde ve SQLAlchemy bind/result aşamalarında gerçekten binary float'a dönüşmeden korunuyor mu?
8. JSON response serialization para sözleşmesini istemcide kırıyor mu; string/number kararı açık ve tutarlı mı?
9. Excel import parser negatif sayıları, binlik ayraçları, boşlukları ve farklı locale biçimlerini güvenli işliyor mu?
10. Runtime `app/` altında AST taramasından kaçan `float()`, SQL `REAL` veya sürücü düzeyi dönüşüm var mı?
11. Atomik stok güncellemesi concurrent lost-update ve insert race riskini PostgreSQL'de tamamen kapatıyor mu?
12. Ürün, depo, transfer, kritik stok, workflow ve finans mutasyonlarında tenant leak/corruption kalmış mı?
13. `/api/ready` migration durumu, DB bağlantısı ve gerekli bağımlılıkları gerçekten readiness semantiğiyle ölçüyor mu?
14. Startup otomatik migration, uygulama yetkili DB hesabına gereğinden fazla DDL yetkisi verilmesini gerektiriyor mu?
15. Varsayılan pytest koleksiyonundan ayrılan legacy testlerde önemli ve henüz taşınmamış hangi davranışlar var?
16. `conftest.py` ignore listesi gerçek regresyonları görünmez kılabilir mi; CI için ayrı legacy job gerekir mi?
17. CI temiz checkout üzerinde frontend lockfile, PostgreSQL schema izolasyonu ve test sıralaması bakımından deterministik mi?
18. Docker non-root kullanıcı local SQLite, export, yedek ve migration dosyalarında izin sorunu çıkarıyor mu?
19. Audit failure logging, correlation ID, entity ID ve before/after diff eksikleri ne kadar kritik?
20. Production backup/restore ve failed migration recovery prosedürü yeterli mi?
21. `document_sequences` eşzamanlı PostgreSQL işlemlerinde gerçekten benzersiz ve monoton mu; rollback, manuel numara ve import senaryoları güvenli mi?
22. Dinamik belge tablosu whitelist yaklaşımı tüm call-site'larda kapalı mı?

## Beklenen çıktı

- Yeni P0/P1/P2 listesi
- Doğrudan sömürülebilir güvenlik açıkları
- PostgreSQL/Docker açılış kırılımları
- Migration veri kaybı veya rollback riskleri
- Finansal doğruluk sorunları
- Tenant izolasyonu ve race-condition bulguları
- Test karantinasından kaynaklanan kapsama boşlukları
- En yüksek getirili sonraki 20 geliştirme

## Backup ve migration mutabakatı (yeni)

Aşağıdaki yeni parçaları özellikle denetle:

- `backend/app/database_backup.py`
- `backend/manage_backup.py`
- `backend/app/reconciliation.py`
- `backend/reconcile_numeric_migration.py`
- `backend/app/numeric_manifest.py`
- `backend/test_v2_9_backup_reconciliation.py`

Kontrol et:

- SQLite online backup gerçekten tutarlı snapshot üretiyor mu?
- Restore atomik mi ve mevcut hedef için güvenlik kopyası bırakıyor mu?
- Manifest/checksum manipülasyonu engelleniyor mu?
- PostgreSQL `pg_dump` çağrısında parola komut satırına sızıyor mu?
- Migration mutabakatı satır, NULL ve yuvarlanmış toplam driftini yakalıyor mu?
- Büyük tablolar için bellek/performans riski var mı?
- Precision manifesti SQLAlchemy modelleri ve Alembic ile tek kaynak olarak kalıyor mu?


## Yeni bootstrap/container güvenlik kapsamı

Özellikle şu dosyaları saldırgan biçimde denetle:

- `backend/app/runtime_migrations.py` (`database_bootstrap_lock`)
- `backend/app/main.py` (initializer + Alembic sıralaması)
- `backend/test_numeric_migration_postgresql.py`
- `docker-compose.yml`
- `.dockerignore`
- `.env.docker.example`
- `POSTGRESQL_FINAL_GATE.md`

Kontrol et: advisory lock tutulurken pool tükenmesi veya deadlock oluşabilir mi; read-only container uygulamanın export/backup akışlarını kırar mı; CSP gerçek frontend davranışını engeller mi; compose secret sözleşmesi özel karakterli parolalarda doğru mu; numeric migration testi gerçekten tüm kritik veri kaybı sınıflarını kapsıyor mu.

## 2026-07-13 Claude P0/P1 düzeltme checkpoint notu

Aşağıdaki önceki denetim bulguları bu checkpointte kod ve regresyon testleriyle ele alındı:

- Global indirim KDV matrahı
- Backend must_change_password zorlaması
- Yönetici rol hiyerarşisi ve admin pasifleştirme
- Excel locale sayı ayrıştırması ve yükleme limitleri
- products.stock özet drift'i
- Frontend route permission guard
- WAL/SHM paket sızıntısı

Yeni incelemede negatif stok ve kredi/risk politikalarının tüm mutasyon yüzeylerini gerçekten
kapsadığını; yönetici istisnalarının yetki, gerekçe ve audit bütünlüğünü; workflow dönüşümlerinin
PostgreSQL eşzamanlılığında atomik/idempotent kaldığını saldırgan biçimde tekrar doğrulayın.

Açık kalan ana alanlar: gerçek PostgreSQL 16 final gate, legacy test karantinası,
şema yönetiminin Alembic üzerinde tekilleştirilmesi, httpOnly cookie/refresh token geçişi
ve connector güvenlik altyapısı.
