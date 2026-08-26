# V2.9 — Belge Numarası Eşzamanlılık Güçlendirmesi

## Sorun

Önceki belge numarası üretimi doğrudan `MAX(id) + 1` kullanıyordu. Aynı firmada aynı anda iki satış/alış/teklif oluşturulursa iki işlem aynı belge numarasını alabilirdi. Silinen kayıtlar ve dışarıdan içe aktarılan belge numaraları da sayaç davranışını belirsizleştiriyordu.

## Değişiklikler

- `document_sequences` tablosu eklendi.
- Sayaçlar `company_id + sequence_key` bileşik anahtarıyla tenant bazında izole edildi.
- Sayaç artışı `UPDATE ... RETURNING` ile atomik hâle getirildi.
- PostgreSQL ve SQLite için güvenli `ON CONFLICT DO NOTHING` başlangıcı eklendi.
- Tablo adı ve belge öneki kapalı whitelist/format doğrulamasından geçiriliyor.
- Mevcut/ithal bir belge numarasıyla çakışma görülürse sayaç otomatik ilerliyor.
- Alembic head `20260713_0002` oldu.
- Migration revision testleri sabit revision yerine gerçek Alembic head değerini doğrulayacak şekilde güncellendi.

## Güvenlik

Dinamik SQL tablo adı yalnızca uygulamanın kapalı belge tablosu listesine izin verir. Kullanıcı kontrollü tablo adı veya tehlikeli önek reddedilir.

## Uyumluluk

Endpoint sözleşmeleri değişmedi. Belge numarası biçimi korunuyor:

```text
SAT-000001
ALS-000001
TEK-000001
```

## Breaking change

Yok.
