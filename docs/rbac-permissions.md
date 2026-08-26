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

Satış rolü tahsilat kaydedebilir ve yalnızca hesap adı/türü/para birimi gibi
seçim alanlarını görebilir. Bakiye, IBAN, açılış bakiyesi ve hazine hareketleri
`finance` izni olmadan sunulmaz.
