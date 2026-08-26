# V2.9 Satış Paneli Sprint 3

- Satış/alış ekranına hızlı müşteri/tedarikçi oluşturma eklendi.
- Yeni oluşturulan cari otomatik olarak belgeye seçiliyor.
- Nakit, banka ve POS ödeme yöntemleri için finans hesabı seçimi eklendi.
- Seçilen hesap backend tarafından ödeme yöntemiyle doğrulanıyor ve otomatik ödeme/finans hareketine aktarılıyor.
- F6 ödeme alanı, F8 yeni satır, F9 kaydet ve Escape kapat kısayolları eklendi.
- Yeni backend entegrasyon testi seçilen hesabın payments ve finance_transactions kayıtlarına aktarıldığını doğruluyor.

## Kalite kapıları
- Frontend: 6/6 test geçti.
- TypeScript + Vite production build geçti.
- Backend yeni entegrasyon + clean install: 3/3 geçti.
- Python compileall geçti.
