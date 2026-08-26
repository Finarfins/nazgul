# V2.9 Dashboard SQL Aggregation

- Dashboard özetleri tam tabloları Python belleğine çekmek yerine SQL `SUM`, `COUNT` ve `GROUP BY` ile hesaplanıyor.
- Son satışlar, kritik ürünler, gecikmiş alacaklar, finans hesapları ve aktivite listeleri `LIMIT` ile sınırlandı.
- Müşteri alacakları, tedarikçi borçları, kasa/banka toplamları ve kritik stok adedi DB tarafında hesaplanıyor.
- 14 günlük satış trendi yalnızca ilgili tarih aralığını sorguluyor.
- Tenant join'leri `company_id` ile güçlendirildi.
- Eski dashboard testi eksik `veriler.db` bağımlılığından çıkarılıp temiz demo veritabanı kullanan pytest senaryosuna dönüştürüldü.
