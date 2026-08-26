# V2.9 Policy Hardening Checkpoint

Tarih: 13 Temmuz 2026

Bu checkpoint, Claude P0/P1 denetiminde bilinçli olarak açık bırakılan negatif stok,
kredi/risk limiti ve belge dönüşümü bütünlüğü maddelerini kapatır.

## Firma bazlı işlem politikaları

Firmalara iki yeni ayar eklendi:

- `negative_stock_policy`
- `credit_limit_policy`

Her politika üç moddan birini kullanır:

- `block`: ihlal kesin olarak engellenir.
- `manager_override`: yalnızca admin/yönetici, en az 5 karakterlik gerekçeyle devam edebilir.
- `allow`: işlem firma kararıyla serbest bırakılır.

Güvenli varsayılan `block` modudur.

## Denetlenebilir yönetici istisnası

- `policy_override_logs` tablosu ve Alembic migration'ı eklendi.
- İstisna kayıtlarında firma, kullanıcı, kullanıcı adı, politika, kaynak tipi,
  kaynak kimliği, gerekçe, request ID ve zaman damgası tutulur.
- Firma ayarları ve politika istisna kayıtları yönetim ekranından görüntülenebilir.
- Türkçe/Unicode gerekçeler HTTP başlığında URL kodlanarak güvenli taşınır.
- Yetkisiz roller politika istisnası veremez.

## Negatif stok politikasının kapsadığı yüzeyler

Politika artık yalnızca satış ekranında değil, stok düşürebilen tüm doğrulanmış
mutasyonlarda uygulanır:

- Satış oluşturma, güncelleme ve silme
- İrsaliye, satış iadesi, alış iadesi ve diğer workflow belgeleri
- Workflow belge silme ve dönüşüm işlemleri
- Ürün açılış stoku
- Ürün kartından stok değiştirme
- Manuel depo stok düzeltmesi
- Toplu stok işlemi
- Excel ürün içe aktarma
- Depolar arası transfer

Eski `allow_negative=True` sabit geçişleri uygulama kodundan kaldırıldı.

## Kredi/risk limiti zorlaması

- Satış kaydı öncesinde müşterinin güncel kredi maruziyeti hesaplanır.
- Yeni veya güncellenen belgenin etkisi çift sayılmadan değerlendirilir.
- PostgreSQL'de ilgili müşteri satırı eşzamanlı satış yarışına karşı kilitlenir.
- Firma politikasına göre işlem engellenir, gerekçeli yönetici onayı istenir veya izin verilir.
- Uygulanan istisna ayrı denetim kaydına yazılır.

## Atomik ve idempotent belge dönüşümleri

- Teklif → sipariş ve sipariş → satış/irsaliye dönüşümleri tek veritabanı işlemi oldu.
- Kaynak belgenin dönüşüm işareti ile hedef belgenin oluşması artık ayrı commit'lere bölünmez.
- PostgreSQL'de kaynak satır `FOR UPDATE` ile kilitlenir.
- Aynı dönüşüm tekrar çağrıldığında mevcut hedef belge döndürülür.
- Aynı siparişin farklı hedef tipe ikinci kez dönüştürülmesi `409` ile engellenir.

## Migration ve başlangıç güvenliği

- Yeni Alembic head: `20260713_0003_company_policies`
- `AUTO_MIGRATE=false` iken veritabanı head revision'da değilse uygulama sessizce
  başlamaz; açık hata ile durur.

## Frontend

Gerekçeli yönetici onayı şu ekranlara eklendi:

- Satış/alış işlem diyalogları
- Workflow belge oluşturma, silme ve dönüştürme
- Ürün oluşturma/düzenleme
- Excel ürün aktarımı
- Toplu stok
- Depo transferi
- Firma politika ayarları ve istisna logları

## Hâlâ açık zorunlu üretim kapıları

- Gerçek PostgreSQL 16 üzerinde temiz kurulum, upgrade, eşzamanlılık ve veri mutabakatı
- Karantinadaki legacy testlerin modern fixture yapısına taşınması
- Runtime `initialize_*` DDL ile Alembic şema yönetiminin tek kaynağa indirilmesi
- Token saklamanın httpOnly cookie/refresh token mimarisine geçirilmesi

Bu nedenlerle checkpoint teknik olarak ilerlemiştir ancak ürün hâlâ `production-ready` değildir.
