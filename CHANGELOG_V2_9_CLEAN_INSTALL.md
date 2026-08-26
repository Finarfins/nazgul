# V2.9 Clean Install / Cross-Database Checkpoint

## Tamamlananlar
- Temiz SQLite/PostgreSQL kurulumları için SQLAlchemy tabanlı çekirdek ERP şeması eklendi.
- Müşteri, tedarikçi, ürün, satış, alış, ödeme, stok hareketi, teklif, iade ve eski gider tabloları temiz veritabanında otomatik oluşturuluyor.
- Runtime kodundaki `lastrowid`, `date('now')`, `AUTOINCREMENT`, `REAL` ve SQLAlchemy `Float` bağımlılıkları kaldırıldı.
- Yeni para alanları `NUMERIC(18,2)`, miktar alanları `NUMERIC(18,4)` olarak tanımlandı.
- Kimlik üretimi `INSERT ... RETURNING id` ile iki veritabanında ortaklaştırıldı.
- Eski `income_expenses` tablosuna tenant alanı ve indeksi eklendi; dashboard sorgusu firma filtresiyle güvenli hale getirildi.
- Eski SQLite kurulumlarındaki global `warehouses.name UNIQUE` kısıtı nedeniyle yeni firma açılışında oluşan çökme engellendi.
- Temiz kurulum, müşteri/ürün/satış smoke testi ve çoklu firma sonrası yeniden başlatma testi eklendi.

## Doğrulanan testler
- `test_v2_9_clean_install.py`: 2/2 geçti.
- Temiz SQLite: sağlık, dashboard, müşteri, ürün, depo, satış akışı geçti.
- Çoklu firma oluşturulduktan sonra ikinci uygulama başlangıcı geçti.
- Yönetici rolünün admin rolü ataması temiz veritabanında 403 döndü.

## Kalan ana risk
- Gerçek PostgreSQL 16 sunucusunda uçtan uca çalışma testi henüz yapılmadı.
- Mevcut eski SQLite `REAL` kolonlarının `NUMERIC` tipine dönüşümü Alembic migration gerektiriyor.
