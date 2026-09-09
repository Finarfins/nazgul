# WA3 — bilinen sınırlar

WA3-core `nazgul_website` çözücüsünü **davranışı değiştirmeden** taşımıştı ve
iki ölçülmüş kaynak davranışını "düzeltme WA3-full'un işidir" notuyla
sabitlemişti. **WA3-full ikisini de kapattı**; tablo aşağıda, kapanış
kayıtlarıyla birlikte duruyor.

Sonra gelen bölüm (**WA3-full'ün KENDİ sınırları**) bu turda BİLEREK
kapatılmayan yerleri, gerekçeleriyle ve onları sabitleyen testlerle yazıyor.

## KAPANDI — WA3-core'un iki sınırı

| # | Girdi | WA3-core'daki çıktı | WA3-full'daki çıktı | Ne değişti | Sabitleyen test |
| :-- | :--- | :--- | :--- | :--- | :--- |
| 1 | `İsmail Yılmaz bakiyesi` | `cari_durum`, `musteri = "İsmail"` — soyadı **düşüyordu** | `cari_durum`, `musteri = "İsmail Yılmaz"` — soyadı **korunuyor** | `niyet._terim_cikar` sözlük kökünü artık **yalnız tam eşleşmeyle** eliyor (`katli in SORU_SOZLUGU`), kök + ek ile değil. `YILMAZ` = `YIL` + üç harflik ek (tolerans 6) olarak okunup durak kelime sayılıyordu. **Niyet kökleri hâlâ kök+ek eşleşiyor** (`_grup_eslesir`) ve bu ayrım bilinçli: `borcu`, `stoğundan`, `cirosu` niyetin KENDİSİDİR ve elenmezse arama terimine karışır; sözlük ise DURAK KELİME listesidir ve oradaki bir kökün eki bir insanın soyadı olabilir. | `tests/test_wa3_niyet.py::test_OKUMA_akisinda_YILMAZ_soyadi_KORUNUYOR` |
| 2 | `tahsilat` (tek kelime) | `donem_ozeti`, `donem = "bu_ay"` — sessizce bu ayın tahsilat toplamı | `niyet.TAHSILAT_REHBERI` — "müşteri, tutar ve yöntemi birlikte yazın" | `tahsilat_coz` artık **üç koşul birden** sağlanınca rehberlik dönüyor: açık `tahsilat`/`tahsil` kökü **var**, açık dönem **yok**, `TAHSILAT`ın kendisi dışında başka niyet kökü **yok**. Üçü de mevcut akışları KORUMAK için; ölçüldü: `geçen ay tahsilat` hâlâ dönem özetidir (WA3-core'un pini), `tahsilat ve ciro` hâlâ dönem özetidir. | `tests/test_wa3_niyet.py::test_tutar_ayristirici_birim_tablosu` ve `tests/test_wa3_worker.py::test_YAZMA_NIYETI_WA4E_YONLENDIRILIYOR` |

Ölçülmüş yan etki (1 için): aynı sınıftan `Günay` (`GUN`) ve `Sonat` (`SON`)
de artık korunuyor. `Aydın` WA3-core'da da korunuyordu (`AY` iki harflidir ve
kök eşlemesi ≥3 harf ister) ve düzeltme onu **bozmadı** — üçü de aynı testte
adıyla ölçülüyor. Yazma akışı (`tahsilat_coz`) kökü ≥4 harfle sınırlayarak
`"İsmail Yılmaz"`ı zaten koruyordu; iki akış artık **aynı sonucu** veriyor,
aynı KODU değil.

Karşı tarafta ödenen bedel de ölçüldü ve kabul edildi: yalnız `SORU_SOZLUGU`da
bulunup **niyet köklerinde bulunmayan** bir kelimenin ekli hâli (`faturası`,
`raftaki`) artık arama terimine karışabilir. Yön güvenlidir ve seçim bu
yüzden yapıldı: fazla bırakmak "bulunamadı" cevabına yol açar, fazla eleme
ise **yanlış müşterinin bakiyesine** yol açardı.

## WA3-full'ün KENDİ sınırları

Aşağıdakiler bu turda BİLEREK yapılmadı. Her biri bir testle ya da bir kapıyla
sabit ki biri "eklerken" iki akış sessizce ayrışmasın.

| # | Sınır | Neden bu turda yok | Sabitleyen |
| :-- | :--- | :--- | :--- |
| 1 | **Tahsilat (yazma) çalıştırılmıyor.** Tam kalıplı bir tahsilat mesajı `service.WA4_MESAJI` alıyor. | Taslak/onay defteri (kaynaktaki `whatsapp_pending_actions`) bu depoda **yok** ve bu dilim göç açmıyor. Onay adımı olmadan bir tahsilat yazmak, kullanıcının onaylamadığı bir para kaydı demekti. | `tests/test_wa3_worker.py::test_YAZMA_NIYETI_WA4E_YONLENDIRILIYOR` (`payments` tablosunun boş kaldığını da ölçüyor) |
| 2 | **Çok eşleşmede numaralı SEÇİM yok.** İki müşteri eşleşirse numaralı liste dönüyor ama "2" yazmak o kaydı seçmiyor; kullanıcıdan **tam ad** isteniyor. | Kaynakta seçim, bağlam satırına yazılan bir `secenekler` yüküyle çözülüyordu. Bu depoda `whatsapp_context` yükünün **tek anlamı** var (`baglam.AKTIF_FIRMA`) ve ona ikinci bir anlam yüklemek WA2'nin sözleşmesini bozardı: `kimlik_secimi` her okumada yükü aktif firmaya karşı yeniden doğruluyor. | `tests/test_wa3_worker.py::test_COK_ESLESMEDE_LISTE_DONUYOR_TEK_MUSTERI_DEGIL` (listede hiçbir bakiyenin sızmadığını da ölçüyor) |
| 3 | **Medya (fatura görseli) işlenmiyor.** `media_id` dolu satır `IGNORED` + `last_error='medya'` ile kapanıyor. | Fatura okuma bir **model çağrısıdır** ve bu dilimde model yolu yok. İndirilen baytı okuyacak hiçbir çağıran olmadığı için `saglayici.py` kaynağın `medya_indir` yarısını da taşımadı: kullanılmayan bir ağ yolu, ölçülmeyen bir ağ yoludur. | `tests/test_wa3_worker.py::test_MEDYA_ve_BOS_METIN_SESSIZCE_KAPANIYOR` |
| 4 | **Kuyruk satırı hangi firmaya cevap verildiğini SÖYLEMEZ.** `_sonlandir` yalnız `status`, `processed_at` ve `last_error` yazıyor. | Kaynağın `whatsapp_inbound`u `company_id`/`user_id` taşıyor; bu depoda **taşımıyor** ve gerekçe göç `20260910_0078`in başlığındadır (platform tablosu; `company_id` taşıyan her tablo `TENANT_TABLES`a girer ve her sorgusundan `company_id=:cid` yüklemi ister). Sütun eklemek göç demekti. Çözümün kendisi (`whatsapp_links` + `whatsapp_context`) zaten kalıcıdır ve "bu numara o an hangi firmadaydı" sorusu oradan cevaplanır. | `tests/test_wa3_worker.py::test_KUYRUK_SATIRINA_KIRACI_YAZILMIYOR` (AST kapısı) |
| 5 | **`toplam_tahsilat` cari cevabında yok.** Şablon alanı destekliyor, yürütücü doldurmuyor. | Uçtaki bakiye sorgusu (`customers.musteri_satirlari`) ödemeleri `pay.total_paid` alt sorgusunda toplayıp bakiyeye düşüyor ama **sütun olarak döndürmüyor**. İkinci bir sorgu yazmak, kanalın uçtan ayrı bir toplam hesaplaması demekti; şablon eksik alanı zaten atlıyor. | — (şablonun eksik alanı atladığı `tests/test_wa3_niyet.py`de ölçülü) |
| 6 | **İşçi ayrı bir SÜREÇ değil, app süreci içinde bir THREAD.** | Kaynakta işçi kendi compose servisidir; bu depoda `app/field_stok_zamanlayici.py` deseni var ve ikinci bir konteyner açmak, bu dilimin ölçemeyeceği bir dağıtım değişikliği olurdu. Kaynağın `hazirlik.py` kapısı (şema head'de mi, app `/api/ready` 200 mü) bu yüzden **taşınmadı**: aynı süreçte ikisi de yapı gereği sağlanıyor; gerekçenin tamamı `app/whatsapp/zamanlayici.py` başlığındadır. | `tests/test_wa3_worker.py::test_ISCI_VARSAYILAN_KAPALI_hicbir_thread_acmiyor` |
| 7 | **Köprü yalnız BAĞSIZ numaralar için.** Bağlı bir kullanıcının kapsam dışı sorusu köprüye gitmez, `niyet.KAPSAM_MESAJI` alır. | Kaynak, bağlı kullanıcının kapsam mesajını da danışmana devrediyordu. Burada devretmemenin gerekçesi ölçülebilirlik: köprüye giden metin dışarı çıkar ve bağlı kullanıcının mesajı **ERP bağlamı taşıyabilir**. Bağsız numarada böyle bir bağlam yoktur — kimlik zaten çözülmemiştir. | `tests/test_wa3_worker.py::test_KOPRU_ACIKKEN_BAGSIZ_NUMARA_KOPRUYE_GIDIYOR` |

## KAPANDI — SEC-1: eşleştirme kodu artık bir NUMARAYA bağlı

Güvenlik incelemesi A/1 (P1) `whatsapp_pairing_codes`ta **ölçülmüş** bir
çapraz kiracı devralma buldu ve göç `20260912_0082` onu kapattı.

| | |
| :-- | :--- |
| **Açık** | Kod satırı "hangi firma, hangi kullanıcı" sorusunu cevaplıyordu (`company_id` + `user_id`) ama **hangi numara** sorusunu hiç sormuyordu. `eslestirme.kod_kullan` satırı yalnız `code_digest` ile buluyor ve bağlantıyı **çağıranın** numarasıyla açıyordu. |
| **Sonuç** | B firmasının kodunu **ele geçiren** biri (ekran görüntüsü, iletilmiş mesaj) kendi numarasını B'nin kullanıcısına bağlayabiliyordu — o numaradan gelen her mesaj B'nin borç, stok ve tahsilat verisini görürdü. Kod bir **sırdır** ama tek başına bir **kimlik değildir**. |
| **Düzeltme** | `target_phone VARCHAR(20) NOT NULL`. Uç (`POST /api/whatsapp/pairing-codes`) gövdesinde `phone` **zorunlu** ve `telefon.e164` ile doğrulanıyor; saklanan biçim kanonik `normalize_phone` çıktısı (`whatsapp_links.phone` ile birebir aynı). `kod_kullan` özet doğrulamasından sonra iki değeri **karşılaştırıyor**. |
| **Ret ayırt edilemez** | Yanlış numaradan gelen **doğru** kod, hiç var olmamış kodla **aynı** `RED_MESAJI`ni alır. Ayrılsaydı saldırgan elindeki kodun geçerli olduğunu öğrenirdi — bir kâhin. |
| **Sayaçlar yanar** | Yanlış numara kodun kendi `attempt_count`unu artırır (ayrı deyim, geri alınmaz) ve saldırganın telefon+pencere sayacına da yazılır. Israr eden biri kodu **kilitler**; sahibi yeni kod ister. |
| **Mevcut satırlar** | Tablo 0079 ile doğdu ve üretimde satırı yok; yine de göç **varsayım yapmıyor**: hedef `''` (hiçbir `normalize_phone` çıktısı olamaz — fail-closed) ve mevcut `PENDING` satırların hepsi `EXPIRED` yazılıyor. |
| **Sabitleyen** | AST kapısı `tests/test_wa2_eslestirme.py::test_HEDEF_NUMARA_DENETIMI_BAGLANTI_INSERTINDEN_ONCE` (kıyasın **varlığı**, **operandları** ve bağlantı INSERT'inden **önce** olduğu) + beş davranış adımı + PG ikizinde `test_CAPRAZ_KIRACI_SIZAN_KOD_BASKA_NUMARADA_ISE_YARAMIYOR` ve `test_GOC_0082_HEDEFSIZ_BEKLEYEN_KODU_SURESI_DOLMUS_YAPIYOR`. |

**Sıra sözleşmedir:** denetim bağlantı INSERT'inden **öncedir**. Sonraya
alan bir mutant davranışta **aynı** görünür (SAVEPOINT geri alır) — hiçbir
davranış testi onu öldüremez, kapı bu yüzden AST'dedir. Ölçüldü: dört
mutantın (kıyas silindi / INSERT'ten sonra / ayırt edilebilir hata / uçta
`phone` isteğe bağlı) dördü de kırmızı, ikincisi **yalnız** AST kapısıyla.

## Ölçülmüş bir sürpriz: CAS kaybı ÜRETİLEMİYOR

WA2'nin PG ikizindeki yirmi işçilik yarış (`test_wa2_eslestirme_postgresql.py::
test_YIRMI_ESZAMANLI_ayni_kod_TEK_KEZ_tukeniyor`) ısınma turu ve kendi
bağlantı havuzuyla ölçülmüştü. Sezgiye aykırı sonuç **kaydedildi ve
teste yazıldı**:

* `SELECT ... FOR UPDATE` (A katmanı) yirmi thread'i **sıraya sokuyor**;
* kazanan commit ettikten sonra sıradaki thread kilidi aldığında satırı
  **yeniden okuyor** ve `status='CONSUMED'` görüyor, yani **B katmanında**
  (okuma sonrası durum denetimi) dönüyor;
* CAS deyimi (C katmanı) **hiç gönderilmiyor**.

Ölçüm (PostgreSQL 16, dört koşu, ısınmalı ve ısınmasız): `FOR UPDATE` **20**,
tüketim `UPDATE`i **1**, CAS kaybı **0** — her koşuda. Yani "yirmi işçi CAS
yarışına girer, on dokuzu CAS'te kaybeder" cümlesi **doğru değildir** ve
"CAS kaybı ≥ N" biçiminde bir eşik yazmak, hiçbir zaman doğrulanamayacak bir
iddia olurdu. C katmanının varlığı bu yüzden AST kapısının işidir
(`tests/test_wa2_eslestirme.py::test_CAS_KOSULU_STATUS_PENDING_ve_KIRACI_YUKLEMLI`);
yarış testi artık **sıralamanın gerçekten çalıştığını** ölçüyor: yirmi
`FOR UPDATE`, bir tüketim.

Isınmanın kendisi ayrı bir şeyi ortaya çıkardı ve o da ölçüldü: `motor`
fixture'ı varsayılan havuzla kuruluyor (5 + 10 = **on beş** bağlantı) ve yirmi
thread bariyerden **önce** bağlantı tutmaya çalışınca on beşi bağlantıyı alıp
bariyerde bekliyor, kalan beşi havuzdan bağlantı bekliyor: kimse ilerlemiyor
ve bariyer `BrokenBarrierError` ile düşüyor (üç koşuda üçü de). Yarışın kendi
motoru bu yüzden var.

Dürüst not: ısınma **sonucu değiştirmedi**. `for_update`=20 ve
`cas_denemesi`=1 eski kurguda da aynı çıkıyor. Değişiklik bir kusuru
KAPATMIYOR; testin ölçtüğü şeyi **ölçülebilir kılıyor** — sayılar artık
bariyerin ne söz verdiğini varsaymadan doğrulanıyor.

**0082 KURGUYU DEĞİŞTİRDİ ve bu burada kayıtlı** ki yukarıdaki ölçümün
hangi kurguya ait olduğu kaybolmasın. Yarış eskiden **yirmi ayrı numara**
kullanıyordu; kod artık `target_phone`a bağlı olduğu için o kurguda on
dokuz işçi **yarışa hiç girmez** — hedef denetiminde, CAS'e varmadan
düşerler. Yani o kurgu bugün yazılsaydı "yirmi işçi yarışıyor" diye okunur,
gerçekte **tek işçi** yarışırdı: testin ölçtüğünü sandığı şeyi ölçmemesinin
ta kendisi.

Yeni kurgu **yirmi işçi, tek numara**. İki sonucu var ve ikisi de açıkça
yazılıyor:

* Hız sınırı artık yarışın **içinde**: sınır telefon başınadır
  (`PAIRING_PENCERE_SINIRI` = 5), yani yirmi işçinin **beşi** CAS'e kadar
  ilerler, on beşi `sinirda` dalında döner. Sınırı test için gevşetmek,
  üretimde **asla oluşamayacak** bir yarışı ölçmek olurdu. `FOR UPDATE`
  sayısı yine **20**'dir — sınır denetimi kilitli okumadan **sonra** gelir.
* Aynı numarayla `uq_whatsapp_links_aktif_numara` yine bir **hakemdir**,
  yani bu test tek başına "kod iki kez tüketildi" hâlini artık ayırt
  edemez. Ayırt eden şey `cas_denemesi == 1` ölçümüdür — tüketim deyimi
  **sürücü seviyesinden** sayılıyor, uygulamanın kendi raporundan değil.
