# V2.9 Demo Dataset

Deneme sürümünü kısa sürede değerlendirebilmek için gerçekçi, deterministik ve ayrı bir SQLite demo veritabanı eklendi.

## Veri kapsamı

- 25 müşteri
- 10 tedarikçi
- 75 ürün
- 60 satış
- 25 alış
- Nakit, POS, havale, kısmi ödeme ve veresiye örnekleri
- 15 bağımsız müşteri tahsilatı
- 10 bağımsız tedarikçi ödemesi
- 10 gelir ve 20 gider kaydı
- İlişkili stok, ödeme ve finans hareketleri

## Dosyalar

- `backend/seed_demo_data.py`: Demo verisini temiz DB üzerinde üretir.
- `backend/demo_veriler.db`: Hazır dolu deneme veritabanı.
- `DEMO_SURUMU_KUR.bat`: Demo DB'yi sıfırlar ve yeniden üretir.
- `DEMO_SURUMU_BASLAT.bat`: Uygulamayı demo DB ile başlatır.
- `DEMO_SURUMU_README.md`: Kullanım talimatı.

Demo verileri gerçek çalışma veritabanından tamamen ayrıdır. Seed işlemi aynı veritabanında ikinci kez veri çoğaltmaz.
