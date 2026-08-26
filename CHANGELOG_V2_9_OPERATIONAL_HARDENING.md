# V2.9 Operational Hardening Checkpoint

## Yapılan değişiklikler

- `/api/ready` artık yalnızca veritabanı bağlantısını değil Alembic revision seviyesini de doğrular.
- Güncel olmayan migration durumunda readiness `503` döndürür ve trafik kabul edilmez.
- PostgreSQL migration advisory lock için sınırlı bekleme süresi ve `pg_try_advisory_lock` eklendi.
- Advisory lock bırakılamazsa bağlantı havuzuna kilitli session dönmemesi için connection invalidate koruması eklendi.
- CORS originleri `CORS_ORIGINS` ortam değişkenine taşındı; wildcard (`*`) reddedilir.
- API yanıtlarına güvenli `X-Request-ID` üretimi ve doğrulaması eklendi.
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` ve `Permissions-Policy` başlıkları eklendi.
- Readiness yanıt modelindeki nested migration verisini reddeden FastAPI annotation hatası düzeltildi.
- CI backend testleri dosya bazında ayrı matrix job'lara bölündü. Böylece test process/thread sızıntıları diğer kalite kapılarını etkileyemez.
- PostgreSQL entegrasyon testleri de ayrı matrix job'larda çalışacak şekilde ayrıldı.
- Stok tenant güvenliği testi ayrı dosyaya taşınarak test izolasyonu artırıldı.

## Breaking change

- `/api/ready`, Alembic revision güncel değilse artık `200` yerine `503` döner. Bu bilinçli bir operasyonel güvenlik değişikliğidir.
- `CORS_ORIGINS=*` artık uygulama başlangıcında reddedilir.
