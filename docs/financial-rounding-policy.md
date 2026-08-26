# Finansal yuvarlama ve geriye uyumluluk kararı

## Envanter

Vergi ve iskonto hesaplayan üç kanonik alan:

1. Satış/alış ve workflow belgeleri: KDV dahil birim fiyat; satır iskontosu,
   belge iskontosu, matrah ve KDV `app.money` yardımcılarıyla hesaplanır.
2. İş emri ve fatura: KDV hariç işçilik/parça tutarı; `compute_line` hem kayıtlı
   parça toplamının, hem fatura satırının, hem de iş emri özetinin kaynağıdır.
3. Rapor/özetler: kaydedilmiş `final_total` ve satır toplamlarını toplar; yeni bir
   vergi veya iskonto hesabı üretmez.

## Kanonik kural

- Girdiler `Decimal(str(value))` ile dönüştürülür; ikili float aritmetiği yoktur.
- Para her bileşen sınırında `0.01`, `ROUND_HALF_UP` ile yuvarlanır.
- Miktar `0.0001` ile; veritabanına kaydedilen belge/satır yüzdeleri kolon
  sözleşmesiyle uyumlu olarak `0.01` ile `ROUND_HALF_UP` yuvarlanır.
- KDV dahil belgelerde başlık iskontosu satırlara largest-remainder yöntemiyle
  dağıtılır. Bağlayıcı invariant: satır toplamları = matrah + KDV = belge toplamı.
- İş emri/fatura satırında sıra: brüt → satır iskontosu → matrah → KDV → toplam.

## Geriye uyumluluk

Bu değişiklik mevcut kaydedilmiş belgeleri yeniden hesaplamaz veya güncellemez.
Kural yalnızca yeni oluşturulan ya da kullanıcı tarafından yeniden kaydedilen
belgelere uygulanır. Tarihsel tutarları değiştirecek toplu backfill/migration bu
PR'ın kapsamı dışındadır ve ayrıca mutabakat raporu ile onay gerektirir.
