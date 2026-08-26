# V2.9 Backup + Reconciliation Test Raporu

## Eklenen testler

- SQLite online backup oluşturma ve integrity check
- SHA-256 manifest doğrulama
- Atomik restore ve pre-restore güvenlik kopyası
- Bozuk/yabancı checksum yedeğinin reddedilmesi
- Finansal snapshot üretimi ve checksum doğrulama
- Migration sonrası toplam drift tespiti

## Sınırlar

- Gerçek PostgreSQL 16 ve `pg_dump` bu çalışma ortamında bulunmadığı için PostgreSQL yedekleme ve gerçek NUMERIC migration provası çalıştırılamadı.
- PostgreSQL CI testleri hazırdır; production-ready kararı için gerçek sunucuda başarılı sonuç zorunludur.
