YEREL HESAP PRO NEXT 1.0.3 — PERFORMANS VE LİSTE STABİLİZASYONU

Bu sürüm yeni modül eklemek yerine uygulama açılışı, liste tutarlılığı ve hata görünürlüğünü iyileştirir.

DÜZELTİLENLER
- Sayfalar React lazy loading ile ihtiyaç halinde yüklenir.
- İlk JavaScript giriş paketi küçültüldü ve vendor paketleri ayrıldı.
- Satış/alış listelerinde tarih aralığı filtresi.
- Tarih, tutar ve cari adına göre sunucu taraflı sıralama.
- Hızlı arama sırasında eski isteğin yeni sonucu ezmesi engellendi.
- Liste ve belge hataları kullanıcıya görünür mesaj olarak gösterilir.
- Satış/alış listesinde kalan bakiye kolonu.
- Tedarikçi listesinde alış adedi.
- Genel React hata sınırı ve güvenli sayfa yenileme ekranı.
- Mobil kartlarda durum ve kalan tutar görünümü.

ÇALIŞTIRMA
1. ZIP dosyasını yeni ve boş bir klasöre çıkarın.
2. baslat.bat dosyasını çalıştırın.
3. Giriş: admin / admin123

TEST
test_et.bat tüm backend testlerini çalıştırır. Frontend build pakette hazırdır.

ÖNEMLİ
Gerçek verilerle kullanmadan önce veriler.db dosyasının yedeğini alın.
