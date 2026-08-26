# V2.9 Sales Panel Sprint 10

## Tamamlananlar
- Satış ekranı başlangıç verileri için görünür yükleme durumu eklendi.
- Müşteri/tedarikçi, depo veya finans hesabı yükleme hataları sessizce yutulmak yerine kullanıcıya gösteriliyor.
- Seçili deponun ürünleri yüklenirken ayrı yükleme durumu eklendi.
- Ürün yükleme hatasında ürün listesi güvenli biçimde temizleniyor ve açıklayıcı hata gösteriliyor.
- Henüz müşteri/tedarikçi yoksa Hızlı Ekle akışına yönlendiren boş durum mesajı eklendi.
- Aktif depo yoksa belge kaydı açıkça engelleniyor.
- Seçili depoda ürün yoksa Hızlı Ürün akışına yönlendiren boş durum mesajı eklendi.
- Hata bildirimi kapatılabilir hale getirildi.
- Başlangıç/ürün verileri yüklenirken F9 ve kayıt düğmesi devre dışı bırakıldı.

## Testler
- Frontend Vitest: 6/6 geçti.
- TypeScript + Vite production build geçti.
- Satış müşteri bağlamı, ödeme hesabı, ödeme dağılımı, yazdırma ve silme geri alma regresyonları: 5/5 geçti.
- Python compileall geçti.
