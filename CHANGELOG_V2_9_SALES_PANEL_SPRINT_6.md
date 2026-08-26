# V2.9 Satış Paneli Sprint 6

## Tamamlananlar
- Çoklu ödeme dağılımı eklendi.
- Aynı satış nakit, POS, havale/EFT, çek ve senet yöntemleri arasında bölünebilir.
- Her ödeme satırı için uyumlu kasa/banka/POS hesabı seçilebilir.
- Dağıtım toplamı belge toplamını aşarsa kayıt reddedilir.
- Dağılımdaki her satır ayrı payment ve finance_transaction kaydı oluşturur.
- Eski tek ödeme akışı geriye uyumlu olarak korunur.

## Doğrulama
- Backend split payment + payment account testleri: 2/2 geçti.
- Frontend Vitest: 6/6 geçti.
- TypeScript ve Vite production build geçti.
- Python compileall geçti.
