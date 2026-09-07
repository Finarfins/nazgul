# WA3-core — bilinen sınırlar

Bu dilim `nazgul_website` çözücüsünü **davranışı değiştirmeden** taşıdı.
Aşağıdakiler ölçülmüş kaynak davranışlarıdır; düzeltme **WA3-full**'un işidir.
Her biri bir testle adıyla sabitlidir ki biri "düzeltirken" iki akış sessizce
ayrışmasın.

| # | Girdi | Bugünkü çıktı | Neden | Sabitleyen test |
| :-- | :--- | :--- | :--- | :--- |
| 1 | `İsmail Yılmaz bakiyesi` | `cari_durum`, `musteri = "İsmail"` — soyadı **düşer** | Okuma akışındaki `_terim_cikar`, `SORU_SOZLUGU` köküne 3 harften itibaren yaslanır; `YILMAZ` = `YIL` + 3 harflik ek (tolerans 6) olduğundan durak kelime sayılıp elenir. Yazma akışı (`tahsilat_coz`) kökü ≥4 harfle sınırlar ve `"İsmail Yılmaz"`ı korur; iki akış burada ayrışır. Aynı sınıf: `Aydın`, `Günay`, `Sonat` gibi durak kelime kökü taşıyan adlar. | `tests/test_wa3_niyet.py::test_OKUMA_akisinda_YILMAZ_soyadi_YIL_kokune_yaslanir_OLCULDU` |
| 2 | `tahsilat` (tek kelime) | `donem_ozeti`, `donem = "bu_ay"` | `TAHSILAT` hem `DONEM_KOKLER` içindedir hem de yazma niyetinin anahtarıdır; tutar yokken `tahsilat_coz` `None` döner ve okuma akışı kelimeyi dönem özeti (bu ayki tahsilat toplamı) olarak okur. Kullanıcı "tahsilat girmek istiyorum" demiş olabilir; kanal bunu sormaz, özet döner. | `tests/test_wa3_niyet.py::test_dar_niyetler_mevcut_akislari_bozmaz` (`"geçen ay tahsilat"` → `donem_ozeti` aynı kural) |

WA3-full'da ele alınacak yön: (1) için okuma akışında da ≥4 harf kök sınırı
ya da ad/soyad terimini yalnız tam eşleşmeyle eleme; (2) için tutarsız çıplak
`tahsilat`a rehberlik mesajı (yöntem + tutar kalıbı) döndürmek.
