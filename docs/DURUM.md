# Harman Zamanı — inen iş kaydı

**Yeni bir PR şunu yapar:** `python scripts/durum.py --sonraki <PR numarası>`
komutunun verdiği ada sahip dosyayı `docs/durum/` altında oluşturur ve içine
tek satırlık girdisini yazar — bu dosyanın kendisini DEĞİŞTİRMEZ.

Kaydı en yeni üstte okumak için:

```
python scripts/durum.py
```

## Kaydın bilinen boşluğu (donmuş sayı, 2026-08-17)

Kayıt, kendisini ölçen bir kapı olmadan yaşadı: girdinin DOĞRULUĞUNU ölçen
bayat-sıra kapısı vardı, VAR OLUŞUNU ölçen hiçbir şey yoktu. Ölçüldü (develop
`f244c8f3`): birleşme commit'i olan **50** PR'ın **17**'si girdisiz inmiştir.

| küme | adet | ne |
| :--- | ---: | :--- |
| kayıttan önce | 10 | PR #30'dan öncesi — göç başlangıcını #30 olarak seçti |
| göç penceresinde atlanan | 5 | #31, #61, #62, #65, #66 |
| bu PR ile kapanan | 2 | #71, #72 |

**15'i geriye dönük yazılmayacak.** `sıra` İNİŞ SIRASIDIR: atlanmış girdileri
sona eklemek #66'nın #70'ten sonra indiğini kaydederdi — kaydın söylemek için
var olduğu şeyin tersi. Kayıttan öncekileri şimdi yazmak ise kimsenin o gün
tutmadığı bir kaydı hatırlayarak kurmak olurdu; kaydın var olma sebebi tam
olarak budur.

Bundan sonrası için: girdisiz inen PR artık CI'da kırmızıdır
(`scripts/durum.py --kapi`, varlık kapısı).

## `sıra` benzersizdir — ölçüldü, varsayılmadı

`sıra` İNİŞ SIRASIDIR ve inen kayıtta **benzersiz** olmalıdır. Bu bir tercih
değil, ölçülmüş bir olgudur: develop'ın **284 ilk-ebeveyn tepe durumunun
hiçbiri** yinelenen sıra taşımıyor.

Buna karşılık **5 dal-tarafı birleşme sonucu** yinelenen sıra taşıyordu
(0032, 0034, 0035, 0038, 0040 — hepsi `Merge origin/develop into <dal>`).
Kusurun yaşadığı yer budur: **birleşme sonucu**, dalın kendi ağacı değil.
Bugüne dek altı yinelenme oldu (#73/#75, #78/#80, #77/#80, #77/#82, #77/#81,
#77/#83) ve **altısını da insan yakaladı** — ne `--sonraki` ne `--kapi`
yinelenmeyi görüyordu.

Artık `--kapi` birleşme sonucunda yinelenen sırayı **reddeder** ve iki dosyayı
da adlandırır. Dosya adındaki `pr` ikincil anahtarı bir **lisans değildir**:
yalnız geçici bir yinelenmede okumayı belirlenimci tutar, o durumun inmesini
serbest bırakmaz.

Girdiler silinmez; geri alınan ya da yerine geçilen bir iş yeni bir girdiyle
belirtilir. 2026-08-11 öncesi tarihler yaklaşıktır; kesinlik gerekirse
git'ten alınır.

## Kayıt neden tek dosyada değil

Girdiler eskiden bu dosyada, aynı başlığın altında, aynı satıra eklenirdi.
İki eşzamanlı PR'ın girdileri hiçbir zaman İÇERİK olarak çakışmıyordu; yalnız
KONUM olarak çakışıyorlardı — ve bu, çakışmayı kaçınılmaz kılıyordu.
Ölçülen bedel: #63, #60 ve #59 bu yüzden çakıştı, #59 iki kez. İkinci
çakışma, dört inceleme turundan geçmiş bir başı oynatmak zorunda bıraktı;
başın oynaması iki incelemeyi de öldürdü, tam bir sınırlı inceleme turuna mal
oldu ve çakışan bir PR'ın birleşme nesnesi olmadığı için asla yeniden
kurulamayacak bir merge ref'i on sekiz dakika yoklattı.

Şimdi her girdi KENDİ dosyasında. İki dal aynı yolu yazmadığı sürece git'in
birleştirecek bir şeyi yoktur; çakışma olasılık değil, YAPISAL olarak
imkânsızdır. Dosya adı `<sıra>-pr-<numara>.md` biçimindedir: `sıra` insanın
gördüğü okuma sırasını taşır, `pr` ise aynı sırayı seçen iki eşzamanlı PR'ın
dosya adlarını birbirinden ayırır. İkisi de ADDA olduğu için sıralamayı
değiştirmek hiçbir dosyanın İÇİNİ değiştirmeyi gerektirmez.

Elenen seçenekler ve neden elendikleri:

- **Dosyanın SONUNA eklemek.** Konumu değiştirir, çakışmayı çözmez: iki dal
  yine aynı yere — son satırdan sonrasına — ekler ve git yine çakışır.
  Üstelik en yeni girdi en alta düşeceği için okuma sırası da bozulur.
- **Üretilen bir dizin dosyasını depoya koymak.** Girdiler ayrı dosyalarda
  olsa bile, her PR dizini yeniden üretip işlerse çakışma dizin dosyasına
  taşınmış olur. Dizini güncel tutmakla çakışmasız olmak aynı anda mümkün
  değildir; bu yüzden dizin depoda tutulmaz, `scripts/durum.py` ile anında
  üretilir.
- **Numaraya göre sıralı tek dosya.** Ardışık numaralı iki PR bitişik
  satırlara eklenir ve git bitişik eklemeleri de çakışma sayar. Ayrıca
  bugünkü kayıt birleşme sırasında tutuluyor, numara sırasında değil; numaraya
  geçmek mevcut okuma sırasını yeniden yazardı.

## Sıra bayatlarsa

`--sonraki` sırayı DALIN KENDİ ağacından hesaplar. Dal bekledikçe bu sayı
geride kalabilir: aradan başka PR'lar inerse, dalın girdisi kendisinden ÖNCE
inmiş girdilerin üstünde okunur ve kayıt "hangi iş ne zaman indi" demeyi
bırakır. Bunu hiçbir dal-yerel test göremez — kusur yalnız base ile head'in
BİRLEŞİMİNDE vardır. `durum-kaydi` CI işi bu yüzden `alembic-chain` gibi
birleşmeyi AÇIKÇA kurar ve ölçtüğü ağacı yazdırır.

Ayrım şudur: **eşzamanlılık meşru, bayatlık değil.** Aynı kuşaktan iki PR
aynı sırayı seçer ve `pr` onları ayırır — tasarımın var olma sebebi budur.
Base'in en büyüğünün ALTINDA kalan bir sıra ise bayattır ve kırmızı olur.
Çaresi zaten kullandığımız kural: develop'ı dala merge edip girdi dosyasını
`--sonraki`nin verdiği yeni adla yeniden adlandırmak.

## Çapanın kapsamı — bilerek sınırlı

`test_durum_kaydi.py` yalnız TAŞINAN 30 girdinin metnini ve sırasını sha256
ile kilitler. Bundan sonraki girdilerin metni çapayı hareket ettirmez; bu
BİLİNÇLİ bir seçimdir, çünkü çapayı her girdide hareket ettirmek, kaldırdığımız
paylaşılan-çapa çakışmasını testin içine geri taşırdı. Yeni girdiler için
garanti içerik değişmezliği DEĞİL, YAPISAL bütünlüktür: ad deseni, tek satır
kuralı, `(sıra, pr)` biricikliği, azalan okuma sırası ve bayat sıra kapısı.
Kilitlenmesi gereken şey geçmiştir; bugünün girdisi zaten incelemeden geçer.

## Geçiş

Bu PR, eski düzenin SON, yeni düzenin İLK PR'ı. Daha önce inmiş 30 girdinin
tamamı `docs/durum/` altına, metinleri harfi harfine korunarak ve eski okuma
sırası birebir aynı kalacak şekilde taşındı — hiçbir satır silinmedi, hiçbiri
yeniden yazılmadı, sıralaması değişmedi. `backend/tests/test_durum_kaydi.py`
bunu çapaya bağlıyor: taşınan korpusun sha256'sı sabitlenmiştir, dolayısıyla
bir girdinin metnini sessizce düzeltmek testi kırar. Zaten inmiş işler için
yapılacak bir şey yoktur; bundan sonraki her PR yalnız yukarıdaki tek cümleyi
uygular.

## Bilinen boşluklar

Bunlar KAPANMADI; inen işin ne kanıtlayıp ne kanıtlamadığını okuyanın
bilmesi için burada. Yeniden keşfedilmesinler.

- **SQLite'ta kimlik üretimi YAPISAL olarak doğrulanamıyor.** `inspect()`
  bu `id` sütunlarının hepsinde — #47'nin inmiş tablosu dahil —
  `autoincrement=None` döndürüyor, oysa rowid takma adı olarak üretim
  çalışıyor. Garanti yalnızca DAVRANIŞSAL: açık `id` vermeden INSERT.
  PostgreSQL'de yapısal olarak da ölçülüyor.
- **Türetilmiş "değişiklik öncesi" şema bağımsız bir parmak izi değildir.**
  Fixture, test edilen migration'ın kendi downgrade'iyle üretiliyor;
  migration yukarı çıkarken bir şeyi bozarsa hasar "önceki" görüntüye de
  işleniyor ve karşılaştırma hiçbir şey göremiyor. Ölçüldü (#56): hem
  indeks DÜŞÜREN hem sessizce indeks EKLEYEN mutasyonlar bağımsız çapa
  eklenene kadar yeşil geçti. Çözüm çapa + tam eşitlik; her yeni dilim
  KENDİ çapasını, kendi migration'ı yazılmadan önce ölçmek zorunda.
  #47 ölçüldü: çapası baştan vardı, kapıları kaydettiğimizi kanıtlıyor.
- **Beyan edilen eklemeler GENİŞLETİLEBİLİR — ama yalnız GÖRÜNÜR şekilde.**
  Yanlışlıkla eklenen bir indeks testte de beyan edilmek zorunda, yani
  diff'te görünür. Desenin verdiği garanti "migration'ın BEYAN ETMEDİĞİ
  hiçbir şey geçemez"; "beyan edilen her şey DOĞRUDUR" değil. İkincisi
  inceleme kararıdır ve öyle kalmalı.
