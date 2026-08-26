# V2.9 Backup + Migration Reconciliation Checkpoint

- SQLite online backup, bütünlük kontrolü ve SHA-256 manifest eklendi.
- Geri yükleme atomik hale getirildi; mevcut hedef için otomatik pre-restore güvenlik kopyası oluşturuluyor.
- PostgreSQL custom-format `pg_dump` desteği eklendi.
- REAL/Float → NUMERIC migration öncesi/sonrası finansal fingerprint ve mutabakat aracı eklendi.
- Para/miktar precision manifesti tek kaynakta merkezileştirildi.
- Snapshot checksum doğrulaması ve drift raporu eklendi.
- Eski Windows test çalıştırıcısı güncel pytest kalite kapılarına geçirildi.
- Backup, dump ve migration snapshot dosyalarının paket/Git/Docker içine sızması engellendi.
