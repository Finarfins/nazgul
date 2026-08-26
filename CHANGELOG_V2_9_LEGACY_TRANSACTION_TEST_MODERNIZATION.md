# V2.9 Legacy Transaction Test Modernization

- `test_transaction_integrity.py` artık paket dışı `backend/veriler.db` örnek dosyasına bağlı değil.
- `test_transaction_warehouse.py` artık temiz SQLite veritabanını Alembic/bootstrap üzerinden kendisi oluşturuyor.
- Testler zorunlu ilk parola değişikliği akışını tamamlıyor.
- Depo testi negatif stok politikasını açıkça `allow` olarak ayarlıyor; varsayılan politika davranışına bağımlı değil.
- Satış güncelleme/silme sırasında stok hareketi, ödeme ve finans hareketi bütünlüğü temiz DB üzerinde doğrulanıyor.
- Seçili depo, negatif stok, alış/satış stok etkisi ve silme geri alma akışı temiz DB üzerinde doğrulanıyor.

## Quality gate

- Modernize edilen testler: 2/2 geçti.
- Güncel satış regresyonlarıyla birleşik sonuç: 7/7 geçti.
- Python compileall: geçti.
