# V2.9 Product Quick Create + Demo Industry Data

- Bilinmeyen barkod/ürün kodu ile hızlı ürün açıldığında taranan değer forma otomatik taşınır.
- Sayısal uzun değerler barkod, diğer değerler ürün kodu olarak önceden doldurulur.
- ProductDialog `initialValues` desteği kazandı.
- Demo veri setindeki 75 ürün OEM, alternatif OEM, marka, üretici, uyumlu model ve teknik notlarla zenginleştirildi.
- Hazır `backend/demo_veriler.db` yeni sektörel alanlarla yeniden üretildi.

## Doğrulama

- Frontend Vitest: 7/7
- Frontend production build: başarılı
- Backend hedef testleri: 2/2
- Python compileall: başarılı
