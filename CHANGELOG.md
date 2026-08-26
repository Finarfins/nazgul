# Changelog

Bu dosya Sungur Tarım ERP'nin önemli değişikliklerini sürüm bazında takip eder.

## [Unreleased]

### Eklenen

- Profesyonel GitHub depo düzeni
- Proje README ve dokümantasyon yapısı
- GitHub Actions hazırlığı

### Düzeltilen

- Bayat service worker kill-switch'i: eski PWA worker'ı (`yhp-shell-v1`) bazı
  tarayıcıları eski `index.html` + eski chunk paketine sabitleyip yeni dağıtım
  sonrası "Failed to fetch dynamically imported module" hatasıyla
  ErrorBoundary'ye düşürebiliyordu. `frontend/public/sw.js` artık kendini
  kaldıran, tüm Cache Storage'ı temizleyen, istemcileri claim edip açık
  sekmeleri bir kez ağdan yeniden yükleten bir kill-switch'tir. **Tek sürümlük
  geçiş önlemidir**; üretimde eski worker taşıyan tarayıcı kalmadığından emin
  olununca dosya sonraki bir sürümde tamamen kaldırılabilir.

### Devam eden

- V2.9 final regresyonu
- Production hardening
- Release Candidate hazırlığı

## [2.9.0-rc] - 2026-07-15

### Satış ve alış

- Barkod ve ürün koduyla hızlı ürün ekleme
- Hızlı müşteri, tedarikçi ve ürün oluşturma
- Çoklu ödeme dağılımı
- Kasa, POS ve banka hesabı seçimi
- Son satış ve son alış fiyatı bağlamı
- Müşteri/tedarikçi bakiye ve risk özeti
- PDF ve ödeme özeti
- Satış/alış silme sonrası stok, cari ve finans geri alma

### Ürün ve stok

- OEM ve alternatif OEM alanları
- Marka, üretici ve uyumlu makine bilgileri
- Raf ve teknik not alanları
- Ürün ticari geçmişi
- Kritik stok filtresi
- Sayım ve stok düzeltme akışı
- Çoklu depo ve transfer doğrulamaları

### Performans

- Dashboard sorguları SQL aggregation yapısına taşındı
- Satış, alış ve workflow kalemlerindeki ürün N+1 sorguları kaldırıldı
- Müşteri ve tedarikçi ödeme toplamları toplu sorgulara taşındı

### Güvenlik

- Müşteri/tedarikçi yazma yetki bypass'ı kapatıldı
- Bilinmeyen yazma endpoint'leri deny-by-default hale getirildi
- Zorunlu parola değişikliğinde refresh oturumu sertleştirildi
- Ham exception ve veritabanı hata ayrıntısı sızıntısı kapatıldı

### Test ve demo

- Eski örnek veritabanına bağımlı testler temiz DB senaryolarına dönüştürüldü
- Satış, alış, depo, dashboard, auth ve core test kapsamı genişletildi
- Ürün, müşteri, tedarikçi, satış, alış ve finans hareketleri içeren demo veri üretimi eklendi

## [2.8.0]

- V2.9 öncesi çekirdek ERP geliştirmeleri
