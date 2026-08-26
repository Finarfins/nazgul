# Scripts

Bu klasör kurulum, test, yedekleme ve release işlemlerini tek komutla çalıştıran yardımcı betikler için ayrılmıştır.

## Planlanan betikler

- `install.bat`: Windows geliştirme kurulumu
- `run_tests.bat`: Backend ve frontend testleri
- `backup.bat`: Veritabanı yedeği
- `restore.bat`: Kontrollü geri yükleme
- `reset_demo.bat`: Demo verisini yeniden üretme
- `build_release.bat`: Release paketi oluşturma

## Güvenlik

- Betiklerde parola veya secret sabit yazılmamalıdır.
- Veritabanı bağlantıları ortam değişkenlerinden alınmalıdır.
- Restore işlemi mevcut veritabanının güvenli yedeğini almadan başlamamalıdır.
- Release paketi `.env`, gerçek DB, backup, log, cache ve `node_modules` içermemelidir.
