# V2.9 Migration + Decimal Checkpoint — Test Raporu

Test tarihi: 13 Temmuz 2026

## Backend

Komut:

```bash
cd backend
python -m pytest -q
```

Sonuç:

```text
24 passed, 4 skipped in 38.62s
```

Atlanan dört test gerçek PostgreSQL bağlantısı gerektirir:

- temiz PostgreSQL uygulama smoke testi
- PostgreSQL satış/alış tarih ve tenant testi
- PostgreSQL workflow temiz şema testi
- PostgreSQL legacy workflow yükseltme testi

Bunlar başarısız değil, bu çalışma ortamında PostgreSQL sunucusu olmadığı için koşullu olarak atlanmıştır.

## Python derleme kontrolü

```bash
python -m compileall -q app alembic
```

Sonuç: başarılı.

## Frontend testleri

Komut:

```bash
cd frontend
npm test -- --run
```

Sonuç:

```text
3 test files passed
6 tests passed
```

JSDOM ortamında SVG etiketleri için uyarılar görülmektedir. Test başarısını veya production build'i engellememektedir; test ortamı gürültüsü olarak takip edilecektir.

## Frontend production build

Komut:

```bash
npm run build
```

Sonuç:

```text
TypeScript build: başarılı
Vite production build: başarılı
2103 modules transformed
```

## Doğrulanan davranışlar

- 6 karakter parola politikası
- yönetici rolünün admin oluşturamaması
- temiz SQLite kurulumu ve çoklu firma yeniden başlatması
- Decimal para yuvarlaması ve binary float drift olmaması
- finans, ödeme ve finansal enstrüman akışında Decimal kullanımı
- atomik stok ve tenant sınırları
- `/api/live`, `/api/ready` ve auth sınırları
- Alembic SQLite bootstrap ve revision raporlama
- runtime Alembic migration idempotency
- SQLite foreign key, WAL ve busy timeout ayarları
- Excel formula injection koruması
- workflow tenant read/update/delete IDOR koruması
- non-root Docker ve CI varlığı

## Açık kalite kapısı

Production-ready kararı için aşağıdakiler gerçek PostgreSQL 16 üzerinde geçmelidir:

1. Temiz veritabanında Docker Compose başlangıcı
2. Alembic upgrade'in iki kez güvenli çalışması
3. Login, müşteri, ürün, satış, alış ve workflow smoke akışı
4. Eski veri kopyasında `REAL/Float -> NUMERIC` dönüşümü
5. Dönüşüm öncesi/sonrası finansal toplam karşılaştırması
6. Yedek geri yükleme provası
