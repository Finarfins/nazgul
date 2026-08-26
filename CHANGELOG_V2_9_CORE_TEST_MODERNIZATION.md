# V2.9 Core Test Modernization

- `test_auth.py` clean-database pytest senaryosuna dönüştürüldü.
- `test_core.py` clean-database pytest senaryosuna dönüştürüldü.
- Her iki test de eksik `backend/veriler.db` bağımlılığından kurtarıldı.
- Auth testi 401 koruması, login, zorunlu parola değişimi, kullanıcı oluşturma, audit kaydı, logout ve token iptalini doğrular.
- Core testi dashboard, müşteri sıralama, ürün sıralama, sipariş ve depo endpoint sözleşmelerini temiz kurulumda doğrular.
- İki dosya `conftest.py` karantina listesinden çıkarıldı ve aktif CI kapsamına alındı.
