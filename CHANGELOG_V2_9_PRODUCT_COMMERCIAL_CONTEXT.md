# V2.9 Product Commercial Context

- Ürün detay API'sine firma kapsamlı satış ve alış geçmişi eklendi.
- Son satış/alış fiyatları ve toplam satılan/alınan miktarlar eklendi.
- Stok hareketi depo JOIN'i company_id ile güçlendirildi.
- Ürün detay arayüzünde OEM, alternatif OEM, marka, üretici, uyumlu modeller, raf ve teknik notlar görünür hale getirildi.
- Depo stokları, stok hareketleri, satış geçmişi ve alış geçmişi ayrı sekmelerde gösteriliyor.
- Temiz veritabanında tenant kapsamlı ticari bağlam testi eklendi.

Doğrulama:
- Backend: 2/2 hedef test geçti.
- Frontend: 7/7 test geçti.
- TypeScript/Vite production build geçti.
- Python compileall geçti.
