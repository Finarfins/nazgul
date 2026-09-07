# Durum kayıt numaralandırması — karar notu (H5)

**Hedef yol (kapı değişince):** `docs/durum/NUMARALAMA.md`  
**Bugünkü yol:** `docs/NUMARALAMA.md`

`docs/durum/` altındaki her `*.md` bugün girdi sayılır
(`scripts/durum.py` `AD_DESENI`, `backend/tests/test_durum_kaydi.py::test_dosya_adlari_desene_uyuyor`).
Girdi olmayan bir tasarım notu oraya konursa varlık/bayat kapısı ve yerel okuma
kırmızı olur. Bu yüzden not şimdilik `docs/` altında; Option A/B (veya
"girdi-olmayan markdown yok sayılır" önkoşulu) seçilince `docs/durum/` altına
taşınır. **Bu PR kod değiştirmez** — yalnız karar metnidir.

## Sorun

Bugün girdi dosya adı `<sıra>-pr-<PR>.md` biçimindedir. `sıra`, dalın kendi
ağacında `python scripts/durum.py --sonraki <PR>` ile hesaplanır. İki eşzamanlı
PR aynı `sıra`yı alabilir (`pr` ayırır) — bu tasarım gereği meşrudur. Ama dal
bayatladığında üretilen `sıra` sessizce geride kalır; kusur yalnız base⊕head
birleşiminde görünür (`bayat_sira_denetle`). İnsan sık sık `--sonraki` ile
yeniden adlandırır; CI `durum-kaydi` birleşmeyi kurup ölçer.

Önerilen hijyen: **dal tarafında `sıra` seçilmesin**; dosya adı çakışmasız
`pr-NNNN.md` olsun; `sıra` iniş anında (veya iniş sırasından) atansın.

## Option A — post-merge rename job

**Dal / PR:** kayıt dosyası `docs/durum/pr-NNNN.md` (addında `sıra` yok).  
**Develop push (post-merge) CI işi:** birleşmiş ağaçta `pr-NNNN.md` görür,
bir sonraki boş `sıra`yı verir, dosyayı `SSSS-pr-NNNN.md` olarak yeniden
adlandırır (tek commit veya aynı push üzerindeki bot commit).

### Kapı sonuçları

| kapı / davranış | ne olur |
| :--- | :--- |
| `--sonraki` | Artık `pr-NNNN.md` basar; `sıra` üretmez. |
| `bayat_sira_denetle` | Dal tarafında bayat `sıra` **imkânsızlaşır** (sıra yok). Kapı ya kalkar ya yalnız bot'un ürettiği birleşme sonucunu denetler. |
| `yinelenen_sira_denetle` | Bot atomik/sıralı çalışmazsa iki merge aynı `sıra`yı yazabilir — bot'ta kilit veya "max+1" yeniden okuma şart. |
| `girdi_varligi_denetle` | Beklenen ad `pr-NNNN.md` (veya birleşme sonrası `SSSS-pr-NNNN.md`) olacak şekilde güncellenir. |
| Eşzamanlı PR çakışması | Aynı yolu yazmazlar (`pr` farklı) — bugünkü yapısal kazanç korunur. |
| Okuma sırası | İnsanın gördüğü sıra bot commit'ine bağlı; merge anı ≠ rename anı gecikmesi kaydı "ne zaman indi"den kaydırabilir. |
| Geri alma / revert | Rename commit'i ayrıysa revert sırası boşluk bırakabilir; politika yazılmalı. |
| Yerel önizleme | Dalda `sıra` görünmez; `python scripts/durum.py` çıktısı develop'a inene kadar eksik kalır. |

### Maliyet

- Yeni CI işi (develop-only), bot kimliği, yarışa dayanıklı max+1.
- `AD_DESENI`, `--sonraki`, `--kapi`, `test_durum_kaydi` ve `docs/DURUM.md` metinleri.
- Mevcut `SSSS-pr-NNNN.md` korpusu aynen kalır; yalnız yeni girdiler `pr-NNNN` ile başlar.

## Option B — `sıra` merge sırasından (git log)

**Dal / PR:** yine `docs/durum/pr-NNNN.md` (veya geçici herhangi bir çakışmasız ad).  
**Sıra dosya adında tutulmaz** ya da **üretilmiş** sayılır: okuyucu
`git log --first-parent develop -- docs/durum/pr-*.md` (veya girdi ekleyen
merge commit) sırasına göre `sıra = 1..N` atar. Depoda rename yok.

### Kapı sonuçları

| kapı / davranış | ne olur |
| :--- | :--- |
| `--sonraki` | Yalnız `pr-NNNN.md` yolunu söyler; sıra hesaplamaz. |
| `bayat_sira_denetle` | **Ortadan kalkar** — bayatlanacak bir dal-yerel sıra yoktur. |
| `yinelenen_sira_denetle` | Dosya adında sıra yoksa anlamsız; yerine "aynı PR için iki girdi" / "pr biricik" denetimi kalır. |
| `girdi_varligi_denetle` | `pr-NNNN.md` varlığı ölçülür (bugünkü varlık kapısının PR numarası sıkılaştırması korunur). |
| Okuma sırası | `scripts/durum.py` git tarihçesine bağımlı olur; clone + git şart, "sadece dosya ağacı" yetmez. |
| Rebase / history rewrite | First-parent sırası değişirse geçmiş okuma sırası kayar — korpusun "donmuş kanıt" iddiası zayıflar. |
| Offline / tarball | Git olmayan kopyada sıra üretilemez; bugün dosya adından okunabiliyor. |
| CI | Rename job yok; kapı sadeleşir ama okuyucu karmaşıklaşır. |

### Maliyet

- Okuyucu ve kapıların git-log sözleşmesi; `docs/DURUM.md` "sıra addadır" cümlesi çürür.
- Mevcut `SSSS-pr-NNNN.md` ile hibrit: ya bir kerelik göç (sıra adını düşür) ya da okuyucu her iki biçimi de kabul eder.

## Karşılaştırma (şef için)

| ölçüt | A (post-merge rename) | B (git log sırası) |
| :--- | :--- | :--- |
| Dalda bayat sıra | yok | yok |
| Depoda kararlı sıra | evet (rename sonrası ad) | hayır (tarihçeye bağlı) |
| Yeni hareketli parça | CI bot + yarış | git-log sözleşmesi |
| Mevcut korpus | olduğu gibi kalır | göç veya hibrit okuyucu |
| "Kayıt dosyadan okunur" | korunur | zayıflar |
| Kapı yüzeyi | bayat kapısı kalkar/daralır; bot kapısı eklenir | bayat + yinelenen sıra kapıları yeniden yazılır |

## Bu PR'ın bilerek yapmadığı

- `scripts/durum.py` veya CI işi **değiştirilmedi**.
- Ne A ne B uygulanmadı; yalnız karar metni.

## Önerilen karar soruları

1. Sıra, depo ağacında **kalıcı bir ad** mı olmalı (A), yoksa **türetilebilir bir görünüm** mü (B)?
2. Develop bot commit'i kabul mü (A), yoksa history'ye yeni yazar eklemek istemiyor muyuz (B)?
3. `docs/durum/` altında girdi-olmayan markdown (bu not) serbest mi? Serbestse `AD_DESENI` dışı dosyalar yok sayılmalı — A/B'den bağımsız küçük bir kapı gevşetmesi.
