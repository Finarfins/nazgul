# Demo Sürümü

Demo sürümü, gerçek işletme verilerine dokunmadan uygulamanın satış, alış, stok, cari ve finans akışlarını hızlıca göstermek için kullanılır.

## İçerik

Seed işlemi örnek olarak şunları üretir:

- Müşteriler
- Tedarikçiler
- OEM ve uyumlu model bilgileri içeren ürünler
- Satış ve alış belgeleri
- Nakit, POS, havale, kısmi ödeme ve veresiye örnekleri
- Stok ve finans hareketleri

## Oluşturma

Projedeki güncel seed betiğini kullanın:

```bash
cd backend
python seed_demo_data.py
```

Windows paketinde mevcutsa `DEMO_SURUMU_KUR.bat` kullanılabilir.

## Önemli

- Hazır demo veritabanı GitHub'a commit edilmez.
- Demo verisi yalnızca seed betiğiyle yeniden üretilir.
- Demo modu gerçek işletme veritabanından ayrı çalıştırılmalıdır.
- Demo kullanıcı parolaları production ortamında kullanılmamalıdır.
