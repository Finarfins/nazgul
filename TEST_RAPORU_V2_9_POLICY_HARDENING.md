# V2.9 Policy Hardening — Test Raporu

Tarih: 13 Temmuz 2026

## Sonuç özeti

| Katman | Sonuç |
|---|---:|
| Backend aktif test | 46 geçti |
| PostgreSQL entegrasyon testi | 5 atlandı |
| Backend başarısız | 0 |
| Frontend test | 6 geçti |
| Frontend production build | Geçti |
| Python compileall | Geçti |

## Yeni senaryo testi

`test_v2_9_stock_policy_surface.py` aşağıdaki kaçış yüzeylerini tek temiz veritabanında
doğrular:

1. Eksi açılış stoklu ürün oluşturma yönetici onayı ister.
2. Ürün kartından eksi stok belirleme yönetici onayı ister.
3. Toplu stok işlemi yönetici onayı ister.
4. Depolar arası yetersiz stok transferi yönetici onayı ister.
5. Excel ile eksi stok yükleme yönetici onayı ister.
6. Gerekçeli yönetici onayından sonra işlemler tamamlanır.
7. İstisna kaynak tipleri `policy_override_logs` tablosuna yazılır.

Sonuç: 1/1 geçti.

## Firma politikası ve finansal risk

`test_v2_9_company_policies.py`:

- Güvenli varsayılan politika değerleri
- Firma ayarlarının yetkili kullanıcıyla değiştirilmesi
- Negatif stok block/manager override davranışı
- Kredi limiti block/manager override davranışı
- Yönetici olmayan kullanıcının istisna verememesi
- Gerekçe ve audit alanları
- `AUTO_MIGRATE=false` stale-schema fail-fast

Sonuç: 2/2 geçti.

## İşlem ve workflow bütünlüğü

- `test_v2_9_transaction_integrity_isolated.py`: 1/1 geçti
- `test_workflow_documents.py`: 1/1 geçti
- `test_v2_9_tenant_stock_security.py`: 1/1 geçti
- `test_v2_9_document_sequence.py`: 4/4 geçti

Doğrulanan kritik davranışlar:

- Satış güncelleme/silme sonrası stok, ödeme ve finans toplamları tutarlı kalır.
- Aynı workflow dönüşümü tekrarlandığında aynı hedef kimliği döner.
- Aynı sipariş farklı hedef belgeye ikinci kez dönüştürülemez.
- Tenantlar arası depo/ürün/belge erişimi engellenir.

## Diğer geçen kalite paketleri

- Claude audit fixes: 4/4
- Runtime migrations: 4/4
- Operational hardening: 2/2
- Production readiness helpers: 2/2
- Clean install: 2/2
- Password policy: 2/2
- Backup reconciliation: 4/4
- Bootstrap lock: 4/4
- Decimal contract: 5/5
- Finance decimal: 1/1
- General hardening: 5/5
- Role security: 1/1

## Frontend

- Vitest: 3 dosya, 6/6 test geçti.
- TypeScript project build geçti.
- Vite production bundle üretildi.
- Yeni gerekçeli onay alanları build içinde doğrulandı.

## Çalıştırılamayan üretim kapısı

Bu çalışma ortamında gerçek PostgreSQL 16 sunucusu/bağlantısı bulunmadığı için 5
PostgreSQL entegrasyon testi atlandı. SQLite sonucunun PostgreSQL eşzamanlılığı,
kilit davranışı ve migration veri dönüşümü için yeterli kanıt olmadığı açıkça kabul edilir.
