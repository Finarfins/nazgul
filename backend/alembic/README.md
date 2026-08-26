# Alembic geçişi

Bu klasör V2.9 Production Readiness sırasında eklenen kontrollü migration altyapısıdır.
Mevcut uygulama geçici olarak başlangıçta idempotent şema kurulumu yapmaya devam eder;
Alembic yeni ve veri dönüştüren değişikliklerin kayıtlı/tekrarlanabilir yürütülmesi için kullanılır.

## Komutlar

Önce veritabanının yedeğini alın ve `DATABASE_URL` tanımlayın:

```powershell
$env:DATABASE_URL="postgresql+psycopg://kullanici:sifre@localhost:5432/yerelhesap"
python -m alembic -c alembic.ini current
python -m alembic -c alembic.ini upgrade head
```

İlk migration PostgreSQL'deki eski para kolonlarını `NUMERIC(18,2)`, miktar kolonlarını
`NUMERIC(18,4)` tipine dönüştürür. SQLite'ta veri tipi dönüşümü yapılmaz; migration sürümü
yine kaydedilir. Dönüşüm geri alınamaz çünkü exact `NUMERIC` değerleri tekrar kayan noktaya
çevirmek finansal doğruluğu bozabilir.
