# PostgreSQL 16 + HTTPS Final Quality Gate

Production-ready etiketi yalnızca gerçek Docker Engine, PostgreSQL 16 ve geçerli bir DNS adı üzerinde bu kapı tamamlandıktan sonra verilir.

## Hazırlık

1. `.env.production.example` dosyasını `.env.production` adıyla kopyalayın.
2. `APP_DOMAIN` için sunucuya yönlenmiş gerçek DNS adı girin.
3. `POSTGRES_PASSWORD` için en az 24 karakterlik rastgele parola kullanın.
4. `DATABASE_URL` içindeki parolayı aynı değerle, gerekiyorsa URL-encoded biçimde güncelleyin.
5. 80/TCP, 443/TCP ve 443/UDP portlarını açın.

## Otomatik prova

```bash
python scripts/production_gate.py --keep
```

Bu komut:

- Compose yapılandırmasını doğrular.
- Production imajını güncel taban imajıyla oluşturur.
- PostgreSQL 16 ve read-only/non-root uygulama container'ını başlatır.
- `/api/ready` ve Alembic durumunu bekler.
- PostgreSQL entegrasyon testlerini ayrı geçici gate imajında çalıştırır.
- Uygulamayı yeniden başlatıp ikinci başlangıcı doğrular.
- Caddy HTTPS reverse proxy'yi başlatır.
- Son durumda servis tablosunu gösterir.

`--keep` kaldırılırsa prova sonunda container'lar kapatılır; kalıcı PostgreSQL volume'u silinmez.

## HTTPS doğrulaması

DNS ve ACME sertifikası hazır olduktan sonra:

```bash
curl -fsS https://APP_DOMAIN/api/live
curl -fsS https://APP_DOMAIN/api/ready
curl -I https://APP_DOMAIN/api/live
```

Beklenen güvenlik başlıkları: HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` ve `Permissions-Policy`.

## Kabul kriterleri

- Production imajı non-root ve read-only çalışır.
- Uygulama portu doğrudan host'a açılmaz; yalnızca Caddy yayın yapar.
- `/api/live` ve `/api/ready` 200 döner.
- Alembic revision repository head ile aynıdır.
- Temiz başlangıç ve ikinci başlangıç başarılıdır.
- Dört PostgreSQL entegrasyon dosyası başarısızlıksız geçer.
- Cookie'ler HTTPS altında Secure + HttpOnly çalışır.
- Caddy geçerli ACME sertifikası alır ve HTTP'yi HTTPS'ye yönlendirir.
- Yedek/geri yükleme provası ayrıca tamamlanır.

## Bu checkpoint'in sınırı

Checkpoint'in üretildiği çalışma ortamında Docker Engine yoktu. Bu nedenle container ve gerçek TLS çalıştırma sonuçları iddia edilmez; yapılandırma, otomasyon ve yerel statik/regresyon kapıları hazırlanmıştır.
