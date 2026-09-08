# WA5 — Gelen medyadan fatura ÖZETİ (kayıt YOK) + mesaj kimliği

Bu dilim üç şey yapıyor ve **üçü de göçsüz, rotasız**:

1. `saglayici.metin_gonder` artık Meta'nın **mesaj kimliğini** döndürüyor;
   WHATSAPP outbox adaptörü onu `external_id`ye yazıyor.
2. `app/whatsapp/fatura.py` (YENİ): gelen görsel/PDF **indiriliyor**,
   çözülüyor ve kullanıcıya **özet** dönülüyor. **Hiçbir kayıt açılmıyor.**
3. `.gitattributes`: `*.md` ve `docs/whatsapp/*.md` için `text eol=lf`.

---

## 1. `external_id` neden ARTIK dolu

WA4 bu alanı **bilerek** boş bırakmıştı ve gerekçesini yazmıştı
(`WA4_KANALLAR.md` §4): kimliği okumak için `saglayici.metin_gonder`in
sözleşmesini genişletmek gerekiyordu ve "o sözleşmenin tek çağıranı bugün
işçidir" — yani WA4 uğruna WA3'ün sözleşmesini genişletmek olurdu.

**WA5'te o sözleşmenin ikinci bir çağıranı doğdu** (`fatura.py`, aynı
sağlayıcıdan `medya_indir` istiyor), yani genişletme artık tek bir
tüketicinin rahatlığı için değil. Genişletme yapıldı ve **bedeli ölçüldü**:

| | Önce | Sonra |
|---|---|---|
| Dönüş tipi | `None` | `str \| None` |
| Başarı ölçütü | istisna atılmaması | **DEĞİŞMEDİ** |
| `SENT`in anlamı | "Meta 2xx ile kabul etti" | **DEĞİŞMEDİ** |
| Dönüşü yok sayan çağıran | — | **etkilenmiyor** |

Son satır bir iddia değil bir kapı:
`test_wa5_fatura.py::test_DONUSU_YOK_SAYAN_CAGIRAN_ETKILENMIYOR` işçinin
kaynağını AST ile okuyup `metin_gonder` çağrısının bir **atamaya
bağlanmadığını** çiviliyor.

### Kimlik gelmezse ne olur

`None` kalır ve satır **yine `SENT`tir**. Bu, "kanıt yoksa hata" kuralının
bilerek REDDEDİLMESİDİR. `supports_idempotency = False` olduğu için
başarısız sayılan bir gönderim outbox tarafından **yeniden denenir** —
yani Meta gövde biçimini değiştirdiği gün kullanıcı aynı mesajı **iki kez**
alırdı. Ayrıştırmanın her başarısızlığı (`{}`, boş liste, sözlük olmayan
eleman, bozuk JSON, geçersiz UTF-8) `None`dur, istisna değil.

Kapı: `test_KIMLIK_YOKSA_2xx_HALA_SENT_ve_external_id_BOS` (9 parametre).

---

## 2. Fatura: NE YAPILIYOR, NE YAPILMIYOR

Kaynak akış (`nazgul_website/backend/app/whatsapp/fatura.py`, 318 satır):

```
indir → MODELLE oku → çöz → ÖZET + "ONAY yazın" → bekleyen işlem → ONAY → TASLAK ALIŞ BELGESİ
└──── taşındı ────┘   └── taşındı ──┘              └──────────── TAŞINMADI ────────────┘
```

### Neden son yarı taşınmadı: **bu bir GÖÇ**

Bekleyen işlem satırı `whatsapp_pending_actions.action_type` sütununa
yazılır. O sütun **kapalı bir küme** ile çivili:

* `schema.py`: `ISLEM_TURLERI = frozenset({TAHSILAT})`
* aynı dosyada CHECK: `action_type IN ('TAHSILAT')`, adı
  `ck_wpa_action_type`
* göç `20260910_0080` bunu DDL'de taşıyor

`'FATURA_TASLAK'` eklemek CHECK'i değiştirmek, yani **bir göç** demektir.
Bu dilim göçsüz olduğu için **tür eklenmedi ve yazma yolu hiç açılmadı**.

**Sonuç kullanıcıya AÇIKÇA söyleniyor.** Özet metni kaynağın "ONAY yazın"
davetini TAŞIMIYOR; yerine şu duruyor:

> NOT: Bu bir ÖZETTİR — kayıt OLUŞTURULMADI. Faturayı web panelinden girin.

Davet dursaydı kullanıcı `ONAY` yazar, o mesajı işleyecek bekleyen satır
olmadığı için mesaj düşer ve kullanıcı **faturayı girilmiş sanırdı**.
Kapı: `test_OZET_KAYIT_ACILMADIGINI_SOYLUYOR`.

### "Yazmıyoruz" nasıl garanti ediliyor

Bir yorum satırıyla değil, **yazacak nesnenin yokluğuyla**. `fatura.py`
bir `Session` **almaz**; kaynağın `oku_ve_taslakla(db, ...)` imzasına
karşılık buradaki imza `medya_ozeti(saglayici, satir, cozucu=None)`.

İki AYRI kapı var ve **biri yeşilken öteki kırmızı olabilir**:

| Kapı | Ne ölçüyor | Hangi mutantı öldürür |
|---|---|---|
| `test_FATURA_MODULU_VERITABANINA_DOKUNMUYOR` | imza + modülün AST'i (yasak içe aktarma, `execute`/`commit`/`taslak_olustur` çağrısı) | `fatura`ya `db` geçirip yazmak |
| `test_DAVRANIS_MEDYA_MESAJI_HICBIR_ISLEM_YAZMIYOR` | gerçek şemada akış; `whatsapp_pending_actions` satır sayısı | `service.py`nin özeti aldıktan **sonra** kendi satırını yazması |

İkisi de ölçüldü: ikinci mutant kurulduğunda **yapısal kapı yeşil kaldı**,
yani ikisi gerçekten tamamlayıcı.

### Model çağrısı: arayüz + NoOp (durum **ÖLÇÜLDÜ**)

Görev "kaynağın **belirlenimci** ayrıştırıcısını" istiyordu. Ölçüm şudur:
**kaynakta belirlenimci bir ayrıştırıcı yoktur.** Kaynağın okuma adımı
`app.assistant.llm.belge_oku` çağrısıdır — bir model çağrısı. Belirlenimci
olan, model çıktısı **alındıktan sonra** çalışan karar katmanıdır ve işte
o taşındı:

* `_sayi`, `_kalem_kararlari`, `_ozet_metni` — birebir
* kaynağın kendi altın vakalarıyla birlikte

Okuma bir arayüze çekildi (`BelgeCozucu`); bu turun tek gerçekleştirimi
`NoOpCozucu`dur ve **daima `None`** döner. `cozucu_al()` bugün daima onu
verir.

### İndirme neden yine de koşuyor

Çözücü NoOp'ken indirme boşa iş gibi görünür; değildir. İndirme, WA3'ün
"çağıranı yok" diye **taşımadığı** gerçek ağ yoludur (iki adımlı Meta
medya ucu, jeton, boyut sınırı). Çağıran bu modüldür ve yol burada sahte
`urlopen` ile ölçülüyor. Sıra tersine çevrilseydi ("çözücü kapalıysa
indirme"), o yol yeniden **ölçülmez** olurdu.

Ölçülen güvenlik özellikleri:

* İkinci istekte de `Authorization` — düşerse Meta 401 verir.
* İkinci adres `https://` **olmak zorunda**. O adresi Meta'nın gövdesi
  söyler ve gövde bizim denetimimizde değildir; `http://`, `file://`,
  metin olmayan ve eksik alan **reddediliyor** ve ikinci istek **hiç
  kurulmuyor**.
* Boyut sınırı **okurken** uygulanıyor (`Content-Length` yalan olabilir).

### Akışın bağlandığı yer

`service._mesaj_isle`, medya dalını **kimlik çözüldükten sonra** çağırır.
Bağsız numaraya ya da firma seçmemiş kullanıcıya fatura özeti dönmek, ERP
bağlamı olmayan birine ERP cevabı vermek olurdu; üstteki iki dal onları
kendi cevaplarıyla karşılıyor.

Ayrıca `not metin` yüklemi artık `media_id` ile birlikte okunuyor:
**altyazısız** bir fotoğrafın `text` alanı boş string'tir
(`cloud_api._medya_mesaji`) ve yüklem düzeltilmeseydi o satır "boş mesaj"
sayılıp `IGNORED` ile kapanırdı — fatura yolu **hiç koşmazdı**.

### DEĞİŞEN DAVRANIŞ: medya artık IGNORED değil

WA3 medya satırını `last_error='medya'` ile `IGNORED` kapatıyordu. WA5'te
**`ANSWERED`**. Eski sözleşmeyi çivileyen test bu turda ADIYLA güncellendi:
`test_wa3_worker.py::test_BOS_METIN_SESSIZCE_KAPANIYOR_MEDYA_ARTIK_CEVAPLANIYOR`
— boş metnin yarısı **duruyor**, medyanın yarısı **değişti**.

---

## 3. `.gitattributes` — satır sonu çivisi

```
*.md               text eol=lf
docs/whatsapp/*.md text eol=lf
```

**Bu bir dönüşüm değil bir çividir.** Depodaki 259 `.md` dosyasının tamamı
bugün zaten indekste LF (`git ls-files --eol '*.md'` tek bir `i/lf` kümesi
veriyor), yani satır **indekste sıfır değişiklik** yapıyor; mevcut durumu
adlandırıyor ve `core.autocrlf` yanlış kurulmuş bir istemcinin CRLF bir
blob'u indekse sokmasını engelliyor.

`docs/whatsapp/*.md` ayrıca yazılı çünkü o dosyalar yalnız okunmuyor,
**ayrıştırılıyor**: `test_wa3_kopru.py` `KOPRU_SOZLESMESI.md` içindeki
` ```json ` bloğunu bir düzenli ifadeyle (` ```json\n ... \n``` `) çıkarıp
**üç altın vektörü** oradan okuyor.

**Dürüst sınır:** bu ayrışma bugün CRLF'e rağmen de çalışırdı, ama
çalışmasının nedeni dosya değil Python'un `read_text` evrensel satır sonu
çevrimidir. Çivi o **örtük katmana olan bağımlılığı** kaldırır: sözleşme
dosyası diskte ne ise ayrışan da odur. Ayrı satır ise, birinin `*.md`yi
daralttığı gün fixture'ın kapsam dışında kalmamasını sağlıyor.

Kanıt:

```
$ git ls-files --eol docs/whatsapp/KOPRU_SOZLESMESI.md
i/lf    w/crlf  attr/text eol=lf        docs/whatsapp/KOPRU_SOZLESMESI.md
```

İki kapı: dosyanın **içeriği** (`test_MD_SATIR_SONU_CIVISI_ve_FIXTURE_KAPSAMI`)
ve git'in niteliği **gerçekten uyguladığı** (`git check-attr`,
`test_MD_CIVISI_GITTE_GERCEKTEN_UYGULANIYOR`).

---

## 4. BU TURDA YAPILMAYANLAR (iddia edilmiyor)

* **Fatura kaydı açılmıyor.** Ne taslak, ne bekleyen işlem, ne belge.
* **Model çağrısı yok.** `NoOpCozucu` daima `None`; gerçek okuma için bir
  gerçekleştirim gerekiyor.
* **Yetki yüklemi yok.** Kaynak `has_permission(role, "purchases")`
  arıyordu ve gerekçesi taslak **alış belgesi** açmasıydı. Açılan belge
  olmayınca yüklemin koruduğu şey de yok. **Belge açan tur onu geri
  getirmelidir** — bu, bu dosyada yazılı bir borçtur.
* **Günlük kota sayacı yok.** Kaynaktaki gerekçe "her fotoğraf bir model
  çağrısıdır"; bu turda model çağrısı yok. Model yolu gelince sayaç da
  gelmelidir (ve o sayaç bir `SELECT`tir, yani çekirdek sorgu envanterine
  bir satır ekler).
* **Göç yok, rota yok.**
