# REAL / Float → NUMERIC Migration Mutabakatı

Amaç, migration öncesi ve sonrası finansal satır sayısı, NULL sayısı, toplam, minimum ve maksimum değerlerin aynı kaldığını kanıtlamaktır. Snapshot değerleri production migration ile aynı `ROUND_HALF_UP` ve kolon ölçeği kullanılarak hesaplanır.

## 1. Migration öncesi snapshot

```powershell
cd backend
python reconcile_numeric_migration.py snapshot `
  --database-url "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB" `
  --output "migration_snapshots/before.json"
```

## 2. Yedek al ve migration çalıştır

```powershell
python manage_backup.py create --database-url "..." --destination "D:/Yedekler/pre_numeric.dump"
python -m alembic -c alembic.ini upgrade head
```

## 3. Migration sonrası snapshot

```powershell
python reconcile_numeric_migration.py snapshot `
  --database-url "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB" `
  --output "migration_snapshots/after.json"
```

## 4. Karşılaştır

```powershell
python reconcile_numeric_migration.py compare `
  --before "migration_snapshots/before.json" `
  --after "migration_snapshots/after.json"
```

Çıkış kodu `0` ise mutabakat eşleşir. `2` ise farklı kolonlar JSON olarak raporlanır ve deployment durdurulmalıdır. Snapshot dosyaları SHA-256 ile korunur; elle değiştirilirse araç reddeder.
