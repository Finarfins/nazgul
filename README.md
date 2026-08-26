# Sungur Tarım ERP

Sungur Tarım'ın tarım makineleri yedek parça, satış, satın alma, stok, cari ve finans operasyonları için geliştirilen özel ERP uygulaması.

> Durum: **V2.9 Release Candidate çalışmaları devam ediyor.**

## Ana özellikler

- Müşteri ve tedarikçi yönetimi
- Satış ve alış belgeleri
- Barkodla hızlı ürün ekleme
- Hızlı müşteri, tedarikçi ve ürün oluşturma
- Nakit, POS, havale ve çoklu ödeme
- Çoklu depo ve depo transferleri
- Stok sayımı, düzeltme ve kritik stok takibi
- OEM, alternatif OEM, marka, üretici, uyumlu model ve raf bilgileri
- Ürün satış/alış geçmişi
- Cari bakiye ve risk takibi
- Dashboard ve finans özetleri
- Rol ve yetki yönetimi
- Audit log ve değişiklik geçmişi
- Demo veri üretimi

## Teknoloji

### Backend

- Python 3.12
- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL 16 / geliştirme için SQLite
- Pytest

### Frontend

- React
- TypeScript
- Vite
- Material UI
- Vitest

## Proje yapısı

```text
backend/        FastAPI uygulaması, migration ve backend testleri
frontend/       React/TypeScript kullanıcı arayüzü
docs/           Kurulum, güvenlik ve yol haritası belgeleri
database/       Veritabanı ile ilgili açıklamalar ve yardımcı kaynaklar
scripts/        Kurulum, test, yedekleme ve release yardımcıları
demo/           Demo veri üretimi ve kullanım notları
```

## Kurulum

Ayrıntılı kurulum adımları için [docs/INSTALL.md](docs/INSTALL.md) dosyasına bakın.

Genel geliştirme akışı:

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
npm run dev
```

Ortam değişkenleri ve veritabanı bağlantısı mevcut proje yapılandırmasına göre hazırlanmalıdır. `.env` dosyaları GitHub'a yüklenmez.

## Demo sürümü

Demo veritabanı doğrudan kaynak kontrolüne eklenmez. Demo kayıtları seed betiğiyle yeniden üretilebilir. Ayrıntılar için [demo/README.md](demo/README.md) dosyasına bakın.

## Güvenlik

- Depo private tutulmalıdır.
- Gerçek müşteri verileri, veritabanı dosyaları, yedekler ve secret değerleri commit edilmemelidir.
- Güvenlik açığı bildirimleri için [docs/SECURITY.md](docs/SECURITY.md) dosyasına bakın.

## Sürüm planı

- **V2.9:** ERP çekirdeğinin tamamlanması ve production hardening
- **V3.0:** Sungur Tarım Edition, OEM ve makine odaklı iş akışları
- **V3.1:** Mobil/PWA ve saha kullanımı
- **V3.2:** Akıllı öneriler ve gelişmiş analiz

Ayrıntılı yol haritası: [docs/ROADMAP.md](docs/ROADMAP.md)

## Lisans ve kullanım

Bu proje Sungur Tarım için özel olarak geliştirilmektedir. Kaynak kod izinsiz dağıtılamaz veya ticari olarak kullanılamaz.
