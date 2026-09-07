# Harman AI köprüsü — imza sözleşmesi (KOPRU_SOZLESMESI)

Bu dosya bir BELGE değil bir FIXTURE'dır: `backend/tests/test_wa3_kopru.py`
aşağıdaki JSON bloğunu okur ve `app/whatsapp/kopru.py`yi ona karşı ölçer.
Web tarafı (`nazgul_website` — `web/src/app/api/harman-ai/whatsapp-koprusu/route.ts`)
aynı üç vektörü BAYT BAYT geçmek zorundadır; vektörler
`app.whatsapp.kopru` içe aktarılmadan, yalnız `hashlib`/`hmac` ile üretildi.

## Taraflar

* **İmzalayan:** ERP backend'i (`app/whatsapp/kopru.py::imzala`). Her zaman
  BİRİNCİL sırla imzalar.
* **Doğrulayan:** web ucu. BİRİNCİL ya da İKİNCİL sırla doğrular (sır
  döndürme penceresi). Python tarafındaki yansıması `kopru.dogrula`.

## Başlıklar

| Başlık | Değer |
| :--- | :--- |
| `X-Harman-Kopru-Timestamp` | unix saniye, ondalık tamsayı metni (`"1757203200"`) |
| `X-Harman-Kopru-Nonce` | en az **16 bayt** rastgele, **küçük harf hex** (≥ 32 karakter, çift uzunluk) |
| `X-Harman-Kopru-Imza` | `hex( HMAC-SHA256( sır, imza_metni ) )`, küçük harf |
| `Content-Type` | `application/json` |

Eski `X-Harman-Kopru-Sirri` (düz metin sır) başlığı GÖNDERİLMEZ ve web
ucu bu sözleşmeye geçtiğinde onu KABUL ETMEMELİDİR: sır telin üzerinden
hiç geçmez.

## İmza metni

```
imza_metni = timestamp + "
" + nonce + "
" + sha256_hex(govde)
sır        = ortam değişkenindeki metnin UTF-8 baytları (trim edilmiş)
govde      = tel üzerinden giden HAM baytlar (JSON; Python tarafı
             `json.dumps(..., ensure_ascii=False)` ile üretir — ayraçlar
             `", "` ve `": "`, Türkçe harfler kaçışsız)
```

Gövde özeti imza metnine GİRER: aynı zaman damgası ve nonce ile gövdesi
değiştirilmiş bir istek doğrulanamaz.

## Kabul kuralları (doğrulayan tarafta)

1. `timestamp` tamsayıya çevrilemiyorsa **RED** (`zaman_bicimi`).
2. `|simdi − timestamp| > 300 s` ise **RED** (`zaman_kaymasi`). 300 tam
   olarak KABUL, 301 RED — iki yönde de.
3. `nonce` küçük harf hex değilse, 32 karakterden kısaysa ya da tek
   uzunluktaysa **RED** (`nonce_bicimi`).
4. İmza, önce BİRİNCİL sonra İKİNCİL sırla **sabit süreli** karşılaştırılır;
   ikisinden biri tutarsa KABUL, hiçbiri tutmazsa **RED** (`imza_uyusmuyor`).
   Boş sır aday DEĞİLDİR.
5. **Nonce tekrarı bu sözleşmenin ALICI tarafındadır.** Backend yalnız
   üretir; web ucu son 300 s içinde görülen nonce'ları tutup ikinci
   görüşte reddetmelidir. `kopru.dogrula` bunu BİLEREK denetlemez ve testi
   bunu adıyla söyler.

## Gövde ve yanıt (danisman.py sözleşmesi, değişmedi)

İstek gövdesi `{"numara": "<E.164 rakamları, işaretsiz>", "soru": "<≤500 karakter>"}`;
yanıt `{"durum": "...", "metin": "..."}` ve backend yalnız `metin`i okur.

## Altın vektörler

Her vektörde `imza`, yukarıdaki kurallarla `sir`, `timestamp`, `nonce` ve
`govde`nin UTF-8 baytlarından üretilmiştir. `govde_utf8_hex` gövdenin tam
baytlarını, `imza_metni_hex` HMAC'e giren tam metni verir; bir tarafın
JSON serileştirmesi farklıysa önce `govde_sha256` karşılaştırılmalıdır.

```json
[
  {
    "ad": "V1 birincil, Türkçe gövde",
    "sir": "harman-birincil-sir-2026",
    "timestamp": 1757203200,
    "nonce": "0123456789abcdef0123456789abcdef",
    "govde": "{\"numara\": \"905405995959\", \"soru\": \"biçerdöver yağ filtresi hangi aralıkta değişir?\"}",
    "govde_utf8_hex": "7b226e756d617261223a2022393035343035393935393539222c2022736f7275223a20226269c3a7657264c3b6766572207961c49f2066696c74726573692068616e6769206172616cc4b16b7461206465c49f69c59f69723f227d",
    "govde_sha256": "ed52f3434b1e5eaf0b267ff2e969d4eb3c5ccffd230480f9cee76578c8e93db1",
    "imza_metni_hex": "313735373230333230300a30313233343536373839616263646566303132333435363738396162636465660a65643532663334333462316535656166306232363766663265393639643465623363356363666664323330343830663963656537363537386338653933646231",
    "imza": "8c25b9336cfcd735b53ef577d592ed0956ff4288ab5d9e33b6acb306c8347dc3"
  },
  {
    "ad": "V2 ikincil, boş nesne",
    "sir": "harman-ikincil-sir-2026",
    "timestamp": 1757203260,
    "nonce": "ffffffffffffffffffffffffffffffff",
    "govde": "{}",
    "govde_utf8_hex": "7b7d",
    "govde_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    "imza_metni_hex": "313735373230333236300a66666666666666666666666666666666666666666666666666666666666666660a34343133366661333535623336373861313134366164313666376538363439653934666234666332316665373765383331306330363066363163616166663861",
    "imza": "4205f957257738ed0c24231e0865b5c76410b39147ce2d2273386ea4fae135f9"
  },
  {
    "ad": "V3 birincil, satır sonu içeren soru (ayıraç \n ile karışmamalı)",
    "sir": "harman-birincil-sir-2026",
    "timestamp": 1757289599,
    "nonce": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4",
    "govde": "{\"numara\": \"905000000000\", \"soru\": \"Merhaba,\\nkontrol edin.\\n\"}",
    "govde_utf8_hex": "7b226e756d617261223a2022393035303030303030303030222c2022736f7275223a20224d6572686162612c5c6e6b6f6e74726f6c206564696e2e5c6e227d",
    "govde_sha256": "a49df21e6118558cfd3c3a0ecf178e099700aa4f621a666e6d139d75aed3bd38",
    "imza_metni_hex": "313735373238393539390a613162326333643465356636303731383239336134623563366437653866393061316232633364340a61343964663231653631313835353863666433633361306563663137386530393937303061613466363231613636366536643133396437356165643362643338",
    "imza": "b1c214ec171ede47c574a7793d3f51c821f8702bda54de7803118bd7733a7c33"
  }
]
```
