# V2.9 Production Gate Sprint

- Caddy tabanlı otomatik HTTPS reverse proxy eklendi.
- Production compose uygulama portunu host erişimine kapatıyor.
- Secure cookie ve HTTPS origin ayarları production override içinde zorunlu hale geldi.
- PostgreSQL 16, readiness, restart ve entegrasyon testlerini yöneten production gate betiği eklendi.
- Test bağımlılıkları production imajından ayrılarak ayrı Docker `test` target'ına taşındı.
- GitHub Actions container işi yalnız build değil gerçek read-only/non-root smoke testi çalıştıracak şekilde güçlendirildi.
- Uvicorn trusted proxy başlıkları environment ile sınırlandırıldı.
