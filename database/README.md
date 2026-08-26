# Database

Bu klasör veritabanı mimarisi, migration ve demo veri üretimiyle ilgili yardımcı belgeler içindir.

## Kurallar

- Şema değişiklikleri Alembic migration ile yapılır.
- Runtime sırasında kontrolsüz tablo/kolon oluşturulmaz.
- Para kolonları `NUMERIC(18,2)`, miktar kolonları `NUMERIC(18,4)` kullanır.
- Gerçek veritabanı, dump veya backup dosyaları GitHub'a yüklenmez.
- Demo verisi seed betiğiyle yeniden üretilir.

## Ana kaynaklar

- Migration dosyaları: `backend/alembic/`
- Alembic ayarları: `backend/alembic.ini`
- SQLAlchemy modelleri: `backend/app/models.py` ve ilgili model modülleri
- Demo seed: `backend/seed_demo_data.py`

## Production kontrolü

1. Boş PostgreSQL 16 veritabanı oluşturun.
2. `alembic upgrade head` çalıştırın.
3. Uygulama bootstrap işlemini çalıştırın.
4. İkinci başlangıçta duplicate kayıt oluşmadığını doğrulayın.
5. Backup alın, ayrı veritabanına restore edin.
6. Satış, alış, ödeme ve stok toplamlarını karşılaştırın.
