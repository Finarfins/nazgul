# V2.9 Depo Stok Operasyonları

## Eklenenler

- Depo stoklarında yalnız kritik ürünleri gösteren filtre.
- Ürün, kod, barkod, OEM veya raf konumuna göre arama.
- Toplam ürün, kritik ürün ve toplam miktar özetleri.
- Depo stok tablosunda OEM ve raf sütunları.
- Satır üzerinden doğrudan stok sayımı/düzeltmesi.
- Sayım sonucu olarak ayarlama, stok ekleme ve stok düşme seçenekleri.
- Kritik stok seviyesini aynı pencereden güncelleme.
- Negatif stok politikası için mevcut yönetici onay akışının korunması.

## Doğrulama

- Backend depo stok operasyon testi: geçti.
- Ürün sektörel alan regresyon testi: geçti.
- Frontend Vitest: 7/7 geçti.
- TypeScript ve Vite production build: geçti.
