# Güvenlik Politikası

## Kapsam

Bu depo Sungur Tarım için geliştirilen özel ERP kaynak kodunu içerir ve private tutulmalıdır.

## GitHub'a yüklenmemesi gerekenler

- `.env` dosyaları
- API anahtarları ve parolalar
- Gerçek müşteri/tedarikçi verileri
- SQLite veritabanları
- PostgreSQL dump ve yedekleri
- Sertifika ve private key dosyaları
- Log dosyaları

## Güvenlik ilkeleri

- Firma/tenant izolasyonu sunucu tarafında doğrulanır.
- Yazma işlemleri deny-by-default yetkilendirilir.
- Para hesaplarında `Decimal`/`NUMERIC` kullanılır.
- Tarayıcı oturumları HttpOnly cookie ile yönetilir.
- CSRF koruması yazma isteklerinde uygulanır.
- Beklenmeyen hata ayrıntıları istemciye sızdırılmaz.
- Kritik değişiklikler audit ve entity history kayıtlarına alınır.

## Production öncesi zorunlu kontroller

- Varsayılan admin parolasını kaldırın.
- Parola politikasını güçlendirin.
- Rate limiting etkinleştirin.
- `COOKIE_SECURE=true` ve HTTPS kullanın.
- CORS origin listesini sınırlandırın.
- PostgreSQL migration ve backup/restore provasını çalıştırın.
- Bağımlılık ve container güvenlik taraması yapın.

## Açık bildirimi

Güvenlik açığı depoda herkese açık issue olarak paylaşılmamalıdır. Depo sahibiyle özel iletişim kurulmalı; hassas exploit ayrıntıları commit, PR veya issue açıklamalarına eklenmemelidir.
