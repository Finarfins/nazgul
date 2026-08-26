# V2.9 Satış Paneli Sprint 5

## Tamamlananlar
- Seçili müşterinin mevcut bakiye, risk limiti ve kullanılabilir limit özeti satış formuna eklendi.
- Girilen satışın kalan tutarına göre satış sonrası tahmini müşteri bakiyesi gösterildi.
- Risk limiti aşımı form üzerinde anlık ve görünür hale getirildi.
- Firma ve müşteri izolasyonlu son satış fiyatı endpoint'i eklendi.
- Ürün seçildiğinde müşterinin o ürünü en son aldığı fiyat, iskonto, tarih ve belge bilgisi getiriliyor.
- Son satış fiyatı ve iskonto tek tıkla satıra uygulanabiliyor.
- Satır içi Enter akışı ürün -> miktar -> birim fiyat -> KDV -> iskonto -> sonraki ürün şeklinde tamamlandı.

## Doğrulama
- Frontend Vitest: 6/6 geçti.
- TypeScript + Vite production build geçti.
- Yeni backend müşteri bağlamı testi geçti.
- Satış ödeme hesabı + müşteri bağlamı + firma politikası regresyonu: 4/4 geçti.
- Python compileall geçti.
