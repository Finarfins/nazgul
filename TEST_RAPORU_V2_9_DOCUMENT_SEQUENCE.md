# V2.9 Belge Numarası — Test Raporu

## Yeni testler

`backend/test_v2_9_document_sequence.py`

- Silinen/eksik ID'lerden bağımsız monoton sıra
- Firma bazında sayaç izolasyonu
- Aynı tabloda farklı prefix izolasyonu
- 24 eşzamanlı SQLite işleminde benzersiz numara
- Tehlikeli tablo adı ve prefix reddi

## Gerçek sonuçlar

- Yeni belge sayacı testleri: **4/4 geçti**
- Production readiness testleri: **2/2 geçti**
- Hardening testleri: **6/6 geçti**
- Runtime migration testleri: **4/4 geçti**; test runner kapanışında mevcut süreç bekleme davranışı gözlendi, test sonuçları üretildi
- Decimal contract: **5/5 geçti**
- Finans Decimal testi: **1/1 geçti**
- Workflow tenant testi: **1/1 geçti**
- Rol güvenliği: **1/1 geçti**
- Parola politikası: **2/2 geçti**
- Frontend: **6/6 geçti**
- TypeScript + Vite production build: **geçti**
- Python compileall: **geçti**
- SQLite temiz migration: `20260713_0002`, `document_sequences` tablosu mevcut

## Açık kapı

Gerçek PostgreSQL 16 üzerinde eşzamanlı sayaç testi henüz çalıştırılmadı. SQL ve migration PostgreSQL için hazırdır; production-ready kararı için gerçek sunucu testi zorunludur.
