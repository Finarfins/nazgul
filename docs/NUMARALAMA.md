# Durum kayıt numaralandırması — karar notu (H5 → H6)

**Hedef yol (kapı değişince):** `docs/durum/NUMARALAMA.md`  
**Bugünkü yol:** `docs/NUMARALAMA.md`

**Karar (şef):** Option **B** — `sıra` first-parent git log birleşme sırasından
türetilir; dosya adı `pr-NNNN.md`. **H6** bunu uygular (`scripts/durum.py`,
kapı, `test_durum_kaydi`).

`docs/durum/` altında girdi dosyaları iki biçimdedir: legacy
`<sıra>-pr-<PR>.md` (kesme sıra ≤ `KESME_SIRA`) ve yeni `pr-<PR>.md`.
Girdi-olmayan markdown için `kayit_adi_ayikla` None döner; H6 kapısı hâlâ
her `docs/durum/*.md`nin girdi deseni taşımasını bekler — bu not şimdilik
`docs/` altında kalır.

## Sorun

Eskiden girdi dosya adı `<sıra>-pr-<PR>.md` biçimindeydi. `sıra`, dalın kendi
ağacında `python scripts/durum.py --sonraki <PR>` ile hesaplanırdı. İki
eşzamanlı PR aynı `sıra`yı alabilirdi (`pr` ayırır) — bu tasarım gereği
meşrudu. Ama dal bayatladığında üretilen `sıra` sessizce geride kalırdı;
kusur yalnız base⊕head birleşiminde görünürdü (`bayat_sira_denetle`). İnsan
sık sık `--sonraki` ile yeniden adlandırırdı; CI `durum-kaydi` birleşmeyi
kurup ölçerdi.

Seçilen hijyen: **dal tarafında `sıra` seçilmez**; dosya adı çakışmasız
`pr-NNNN.md` olur; görünen `sıra` iniş sırasından (git log) türetilir.

## Option A — post-merge rename job (seçilmedi)

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

## Option B — `sıra` merge sırasından (git log) — SEÇİLDİ (H6)

**Dal / PR:** `docs/durum/pr-NNNN.md` (addında `sıra` yok).  
**Sıra dosya adında tutulmaz:** okuyucu `git log --first-parent` ile
`docs/durum/` girdi ekleme sırasına göre legacy adlardaki sırayı korur;
yeni dosyalara `KESME_SIRA + konum` verir. Depoda rename yok.

### Kapı sonuçları

| kapı / davranış | ne olur |
| :--- | :--- |
| `--sonraki` | Yalnız `pr-NNNN.md` yolunu söyler; sıra hesaplamaz. |
| `bayat_sira_denetle` | Kapı `--kapi`den çıkar; fonksiyon legacy testleri için kalır. |
| `yinelenen_sira_denetle` | Yalnız legacy adlarda; yeni biçimde sıra adda yoktur. |
| `kesme_sonrasi_legacy_denetle` | Kesmeden sonra yeni `SSSS-pr-NNNN.md` yasak. |
| `cift_kayit_denetle` | Aynı PR için birleşmede iki girdi / legacy+yeni yasak. |
| `girdi_varligi_denetle` | `pr-NNNN.md` varlığı ölçülür. |
| Okuma sırası | `scripts/durum.py --sira` git tarihçesine bağımlı. |
| Rebase / history rewrite | First-parent sırası değişirse geçmiş okuma sırası kayar. |
| Offline / tarball | Git yokken geçici sıra (`KESME_SIRA+konum`); asıl sıra `--sira`. |
| CI | Rename job yok; `durum-kaydi` iş adı aynı. |

### Maliyet

- Okuyucu ve kapıların git-log sözleşmesi; hibrit legacy+yeni korpus.
- `docs/DURUM.md` "sıra addadır" cümlesi Option B ile güncellenir.

## Karşılaştırma (şef için)

| ölçüt | A (post-merge rename) | B (git log sırası) ✓ |
| :--- | :--- | :--- |
| Dalda bayat sıra | yok | yok |
| Depoda kararlı sıra | evet (rename sonrası ad) | hayır (tarihçeye bağlı) |
| Yeni hareketli parça | CI bot + yarış | git-log sözleşmesi |
| Mevcut korpus | olduğu gibi kalır | hibrit okuyucu (H6) |
| "Kayıt dosyadan okunur" | korunur | görünen sıra git'ten |
| Kapı yüzeyi | bayat kapısı kalkar/daralır; bot kapısı eklenir | kesme + çift kayıt; bayat yalnız legacy test |

## H5'in bilerek yapmadığı / H6'nın yaptığı

- H5: yalnız karar metni; kod yok.
- H6: Option B uygulandı — `scripts/durum.py`, kapı, testler, `docs/DURUM.md`.

## Önerilen karar soruları (kapanmış)

1. Sıra kalıcı ad mı (A) yoksa türetilebilir görünüm mü (B)? → **B**
2. Develop bot commit'i? → **Hayır** (B)
3. `docs/durum/` altında girdi-olmayan markdown? → `kayit_adi_ayikla` yok sayabilir; H6 testleri hâlâ her `*.md`nin girdi deseni olmasını ister.
