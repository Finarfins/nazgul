# V2.9 Sales Panel Sprint 8

- Barkod veya ürün kodu Enter akışı geliştirildi.
- Tam eşleşen ürün eklendikten sonra odak otomatik sonraki ürün satırına taşınıyor.
- Aynı ürün tekrar okutulursa mevcut satırın miktarı artırılıyor.
- Ürün bulunamazsa anlaşılır hata mesajı gösterilip Hızlı Ürün penceresi otomatik açılıyor.
- F8 ve Kalem Ekle aksiyonları yeni satırı açıp doğrudan ürün alanına odaklanıyor.
- Mobil dialog yatay boşlukları azaltıldı.
- Mobilde ana kayıt düğmesi tam genişlikte gösteriliyor.

## Quality gates

- Frontend Vitest: 6/6 passed
- TypeScript + Vite production build: passed
- Backend compileall: passed
