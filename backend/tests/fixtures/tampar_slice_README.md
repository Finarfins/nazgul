# TAMPAR gerçek dosya dilimi

`tampar_slice.xlsx`, worktree kökündeki commit edilmeyen `_local_tampar.xlsx`
dosyasından alınmış gerçek hücreleri içerir. Ham değerler uydurulmamış veya
normalize edilmemiştir. Tam kaynak ticari fiyatlar içerdiği için repoya
eklenmez; `_local_tampar.xlsx` `.gitignore` ile dışlanmıştır.

- Kaynak SHA-256:
  `99f7ec6ca5d679e70e5878936a8fdc60aa370573c06521c55c6d98371da63f9b`
- Sheet sayısı: 4
- Dolu satır sayısı: 78
- Dosya boyutu: 13.737 bayt
- Kabul sınırları: 200 satırdan ve 200 KB'den küçük

## Zorunlu gerçek örnekler

| Desen | Ham değer | Kaynak ve fixture hücresi |
|---|---|---|
| İki satırlı ürün kodu | `64S20-3602\n64TC/20diş` | `06_Bicaklar!A6` |
| Ondalık boşluğu ve USD soneki | `1 ,10 USD` | `06_Bicaklar!F6` |
| Euro işareti ve ondalık boşluğu | `€ 0 ,35` | `06_Bicaklar!F10` |
| TL `,-` yazımı | `600,-TL` | `07_Zipka_Mastar!E20` |

## Korunan ek kirlilik örnekleri

- Metin fiyat sentinel'leri: `xxx`, `sorunuz`.
- Tekrarlanan/birleşik bölüm başlıkları.
- Boş devam satırları ve yalnız KDV taşıyan satırlar.
- `Stoklarla\nSınırlı`, `kalmadı`, `STOK AZ` gibi fiyat dışı notlar.
- Aralıklı `2 0 %\nKDV\nDAHİL` metni.
- Aynı not hücresinde birden fazla EUR fiyatı.

## Korunan yapılar

- `Indeks`: bölüm ve KDV/iskonto metadatası.
- `Tum_Tablolar`: konsolide tablonun ilk gerçek dilimi.
- `06_Bicaklar`: iki fiyat kolonlu, başlığı 5. satırdaki sayfa.
- `07_Zipka_Mastar`: çok bölümlü ve birleşik başlıklı sayfa.

Bu fixture saldırı/performance fixture'ı değildir. ZIP bomb, 51.000 satır ve
26 MB senaryoları ayrı sentetik testlerde üretilir.
