# Rol ve Yetki Matrisi

Sungur Tarım ERP, tahsilat/ödeme işlemleri ile hazine yönetimini ayrı
yetkilerle korur:

- `payments`: müşteri tahsilatı, tedarikçi ödemesi ve bu formlarda kullanılacak
  sınırlı hesap seçimi.
- `finance`: kasa/banka/POS hesapları, bakiye ve IBAN bilgileri, virman,
  finans hareketleri ile çek/senet yönetimi.

| Rol | read | sales | purchases | payments | finance | stock | reports | users | machines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| admin | Tümü | Tümü | Tümü | Tümü | Tümü | Tümü | Tümü | Tümü | Tümü |
| yonetici | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| muhasebe | ✓ | ✓ | ✓ | ✓ | ✓ |  | ✓ |  |  |
| satis | ✓ | ✓ |  | ✓ |  |  |  |  |  |
| depo | ✓ |  | ✓ |  |  | ✓ |  |  |  |
| rapor | ✓ |  |  |  |  |  | ✓ |  |  |
| musavir | ✓ (salt-okunur) | ✓ (salt-okunur) | ✓ (salt-okunur) | ✓ (salt-okunur) | ✓ (salt-okunur) |  | ✓ (salt-okunur) |  |  |

`musavir` (dış mali müşavir, F9-5c) muhasebenin OKUMA izinlerini taşır ama
HİÇBİR yazma yapamaz: `READ_ONLY_ROLES` kapısı (`app/main.py`, izin kapısının
önünde) GET/HEAD/OPTIONS dışındaki her isteği 403 `ROLE_READ_ONLY` ile
reddeder. Kapı yola değil METODA bakar; yalnız self-servis uçlar (parola
değişimi, çıkış) açıktır. Cari alanlarını (VKN, adres, IBAN) maskesiz görür
(`MASKESIZ_ROLLER`); `/api/auth/me` `read_only: true` döner.

Satış rolü tahsilat kaydedebilir ve yalnızca hesap adı/türü/para birimi gibi
seçim alanlarını görebilir. Bakiye, IBAN, açılış bakiyesi ve hazine hareketleri
`finance` izni olmadan sunulmaz.
