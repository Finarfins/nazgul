# Legacy Test Migration Plan

## Neden karantinadalar?

Eski sürüm testlerinin bir bölümü gerçek pytest testi değil, dosya import edilir
edilmez API çağrıları yapan çalıştırılabilir smoke scriptleridir. Aynı Python
sürecinde `app.main` modülünü ve ortak `veriler.db` dosyasını paylaşmaları:

- test sırasına bağlı sonuçlara,
- başka testin token/veri durumunun sızmasına,
- temiz dağıtım paketinde bulunmayan örnek veriye bağımlılığa,
- `pytest` collection aşamasında hata alınmasına

neden oluyordu.

`conftest.py` bu tarihsel scriptleri geçici olarak varsayılan pytest toplamasının
dışında tutar. Bu bir başarı iddiası değildir; aşağıdaki dönüşüm backlog'udur.

## Dönüşüm standardı

Her legacy script:

1. Gerçek `test_*` fonksiyonlarına ayrılacak.
2. Kendi `tmp_path` SQLite veritabanını oluşturacak.
3. İhtiyaç duyduğu müşteri/ürün/satış verisini API üzerinden kendisi hazırlayacak.
4. `app.main` modül önbelleği nedeniyle gerektiğinde izole subprocess kullanacak.
5. Hazır veya gerçek `veriler.db` dosyasına bağımlı olmayacak.
6. PostgreSQL için anlamlı olan senaryolar CI entegrasyon testine eklenecek.

## Öncelik

1. Finans ve ödeme senaryoları
2. Satış/alış belge bütünlüğü
3. Depo transferleri ve raporlar
4. Import/export
5. Dashboard/CRM
6. Tarayıcı E2E

Varsayılan `python -m pytest -q` yalnızca izole ve deterministik testleri çalıştırır.
Legacy testler dönüştürüldükçe `collect_ignore` listesinden çıkarılacaktır.
