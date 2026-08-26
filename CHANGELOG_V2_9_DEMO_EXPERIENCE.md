# V2.9 Demo Experience Checkpoint

- DEMO_MODE ayarı eklendi.
- DEMO_SURUMU_BASLAT.bat demo modunu otomatik etkinleştiriyor.
- /api/demo/summary endpoint'i firma bazlı örnek veri adetlerini döndürüyor.
- Dashboard, demo sürümünde örnek veri uyarısı ve müşteri/tedarikçi/ürün/satış/alış adetlerini gösteriyor.
- Demo özet endpoint'i temiz SQLite veritabanında seed edilerek test edildi.

## Test

- backend/test_v2_9_demo_summary.py: 1 passed
- Python compileall: passed
- Frontend node_modules pakette bulunmadığı için Vitest/Vite build bu checkpoint'te tekrar çalıştırılamadı.
