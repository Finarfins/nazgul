# V2.9 Isolated CI Sprint

Tarih: 2026-07-14

## Amaç

Aktif backend testlerinin tek Python sürecinde kaynak biriktirmesi nedeniyle oluşan kapanış beklemelerini kaldırmak ve yeni test dosyalarının CI matrisine elle eklenmeden otomatik olarak kalite kapısına girmesini sağlamak.

## Değişiklikler

- `backend/run_isolated_tests.py` eklendi.
- Aktif `test_*.py` dosyaları otomatik keşfediliyor.
- `backend/conftest.py` içindeki tarihsel smoke-script karantinası tek dışlama kaynağı olarak kullanılıyor.
- Her aktif test dosyası ayrı Python/pytest sürecinde çalıştırılıyor.
- Her dosya için bağımsız süre sınırı uygulanıyor.
- Bir dosyanın kaynak sızıntısı veya kapanış sorunu diğer testleri engellemiyor.
- CI içindeki elle tutulan eksik backend test matrisi kaldırıldı.
- Session security, company policy, stock policy surface ve transaction integrity testleri artık otomatik olarak CI kapsamına giriyor.
- PostgreSQL 16 entegrasyon kapısı ayrı ve gerçek servis üzerinde çalışmaya devam ediyor.

## Yerel doğrulama

- Aktif test dosyası: 23
- Geçen test: 49
- Yerelde PostgreSQL gerektirdiği için atlanan test: 5
- Başarısız test: 0
- `python -m compileall`: başarılı

PostgreSQL entegrasyon testlerinin gerçek geçişi GitHub Actions PostgreSQL 16 servis kapısında doğrulanmalıdır.
