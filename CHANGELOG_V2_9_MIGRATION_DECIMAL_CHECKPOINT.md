# Yerel Hesap Pro X — V2.9 Migration + Decimal Checkpoint

## Amaç

Bu checkpoint, Yerel Hesap Pro X'in yerel SQLite kullanımını korurken üretim hedefi olan PostgreSQL 16 için şema geçişini güvenli, tekrarlanabilir ve finansal olarak hassas hale getirmeye odaklanır.

## Yapılan değişiklikler

### Runtime migration güvenliği

- Uygulama başlangıcına Alembic revision kontrolü eklendi.
- `AUTO_MIGRATE=true` olduğunda bekleyen migration'lar uygulama başlamadan önce çalıştırılır.
- PostgreSQL üzerinde aynı anda başlayan birden fazla uygulama örneğinin migration yarışına girmemesi için advisory lock kullanılır.
- Alembic `env.py`, uygulama tarafından yönetilen mevcut bağlantıyı kabul edecek şekilde düzenlendi.
- `/api/health` çıktısına mevcut ve beklenen Alembic revision bilgisi eklendi.
- Runtime image için Alembic ana bağımlılıklara alındı.
- Gerçekte geri alınamayan numeric migration için yanıltıcı downgrade kaldırıldı; downgrade açık hata üretir.

### Local SQLite sağlamlığı

- Foreign key denetimi etkinleştirildi.
- WAL journal modu etkinleştirildi.
- Busy timeout eklendi.
- `synchronous=NORMAL` kullanıldı.
- Decimal değerlerin SQLite sürücüsüne binary float'a çevrilmeden bağlanması için adapter eklendi.

### Finansal doğruluk

- Merkezi `app/money.py` modülü oluşturuldu.
- Para yuvarlaması `ROUND_HALF_UP` ve iki ondalık basamakla merkezileştirildi.
- Miktar hassasiyeti dört ondalık basamakla merkezileştirildi.
- Pydantic para ve miktar alanları `float` yerine `Decimal` kullanacak şekilde dönüştürüldü.
- Satış, alış, workflow, finans, ürün, rapor, dashboard, arama, import ve export hesaplamalarındaki binary float kullanımı temizlendi.
- Excel sayı ayrıştırması Türkçe ve uluslararası biçimleri `Decimal` olarak korur.

### Test altyapısı

- Import sırasında çalışan ve ortak mutable `veriler.db` dosyasına bağlı eski smoke scriptleri varsayılan pytest koleksiyonundan ayrıldı.
- Bu testlerin yeniden yazım planı `backend/LEGACY_TEST_MIGRATION_PLAN.md` dosyasında belgelendi.
- Runtime migration, SQLite PRAGMA, Decimal sözleşmesi ve finans akışı için yeni deterministik testler eklendi.
- CI, yeni migration ve Decimal testlerini çalıştıracak şekilde güncellendi.

## Uyumluluk

- Endpoint yolları ve temel JSON sözleşmeleri korunmuştur.
- JSON'da Decimal değerleri FastAPI tarafından sayısal değer olarak sunulmaya devam eder.
- SQLite local kullanım korunur.
- PostgreSQL 16 üretim hedefidir.

## Bilinen sınırlamalar

- Bu çalışma ortamında gerçek PostgreSQL sunucusu olmadığı için dört PostgreSQL testi atlanmıştır.
- Eski üretim verisinde `REAL/Float -> NUMERIC` dönüşümü gerçek bir veri kopyası üzerinde prova edilmeden production-ready kabul edilmemelidir.
- Startup-time legacy schema initialization ile Alembic'in birlikte kullanımı geçiş dönemine aittir; uzun vadede şema sahipliği tamamen Alembic'e taşınmalıdır.
- Varsayılan koleksiyondan ayrılan eski smoke scriptlerinin kapsadığı davranışlar modern izole pytest testlerine kademeli olarak taşınmalıdır.
