"""FAZ 4 — outbox TÜKETİCİSİ: tarla olaylarını stok hareketine çevirir.

#81 ve #88 yazıcı tarafını kapattı: her `field_activities` ve `field_harvests`
yazımı aynı işlemde TAM BİR olay üretiyor. Bu olayları KİMSE OKUMUYORDU.
Ölçülen sonuç (iki firmalık gerçek veri): 10 olay PENDING, attempts=0, ve
stok defterindeki her hareket ya açılış bakiyesi ya satış. Çiftçiye elinde
OLMAYAN stok gösteriliyordu.

TASARIM KARARLARI, hepsi ÖLÇÜLEREK verildi:

* **YÖN.** Tarla girdisi stok TÜKETİR (eksi), hasat stok ÜRETİR (artı).
  Ters işaret hata vermez — CEVAP verir ve cevap yanlıştır. Bu yüzden yön
  kaynak tipinden TÜRETİLİR (`_YON`) ve kaynak başına sınanır.

* **HASAT BUGÜN UYGULANAMAZ, ÇÜNKÜ ÜRÜNE GİDEN YOL YOK.** Ölçüldü
  (c9d3eb1): `field_harvests` içinde `product_id` yok, `crop_seasons.crop`
  serbest metin, ikisinden de `products`a bağ yok. Ürün eşlemesi UYDURMAK
  yanlış ürünün stoğunu artırırdı; hasat olayı bu yüzden ADI KONMUŞ bir
  kovaya (`SKIPPED_NO_PRODUCT`) düşer ve SAYILIR. Kova bir karardır:
  şema hasadı bir ürüne bağladığı gün bu kovanın boşalması beklenir.

* **KAYNAĞI GÖRÜNMEYEN OLAY UYGULANMAZ.** Kaynak satırı bu kiracıda yoksa
  hareket YAZILMAZ: olmayan bir faaliyet için envanter düşülemez. Sessizce
  atlanmaz — `SKIPPED_SOURCE_NOT_VISIBLE` olarak sayılır ve `last_error` ile
  görünür kalır.

  BU KOVA YETİMİ VE ÇAPRAZ KİRACIYI BİRLİKTE TAŞIR ve bu bir KARARDIR
  (bildirilmiş bedel), gözden kaçma değil: ikisini ayırmak
  `company_id <> :company_id` ile kiracı sınırının DIŞINI okumayı gerektirir
  ve bu deponun statik kiracı kapısı — RLS'in bildirilmiş yerine geçen kapı —
  tam olarak bunu reddeder. Teşhis etiketini iyileştirmek için güvenceyi
  delmek, ölçülebilirlik uğruna güvenceden vazgeçmek olurdu. Güvence zaten
  KANITLI: kaynak okuması kapsamlı olduğu için başka firmanın satırı için
  hareket YAZILAMAZ ve çapraz kiracı senaryosu SIFIR hareket ölçer.

* **KİRACI.** Hareketin firması OLAYIN KENDİ satırından gelir; kaynak satırın
  firması farklıysa olay uygulanmaz (`FAILED_TENANT`). Çapraz kiracı stok
  hareketi bu depoda RLS olmadığı için uygulama katmanında durdurulmalıdır.

* **KORUNUM.** İşlenen her PENDING olay ya uygulanır ya ADI KONMUŞ bir kovaya
  düşer. `girdi == uygulanan + kovaların toplamı` bir ASSERT'tir, tesadüf
  değil.

* **TEKRAR.** Olay yalnız PENDING iken alınır ve uçtan uca aynı işlemde
  sonlanır; ikinci koşum onu görmez. Ayrıca hareket satırı
  `reference_type`/`reference_id` taşır, yani tekrar tespit edilebilir.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .auth import utcnow
from .inventory import adjust_warehouse_stock, default_warehouse, sync_product_stock
from .money import quantity as normalize_quantity
# NET FORMÜLÜ İTHAL EDİLİYOR, KOPYALANMIYOR. `_turetilmis_net` okuma
# yüzeyinin (`derived_net_quantity`) de kaynağıdır; iki kopya bir gün ayrışır
# ve ayrışma "ekranda başka, defterde başka net" demek olurdu. Yönlendirici
# modülünden içe bağ TERS YÖNDE YOKTUR (ölçüldü: hiçbir router bu modülü
# içe aktarmıyor), yani döngü kurmuyor.
from .routers.farm import _turetilmis_net
from .units import BirimCozulemedi, UrunTemsilEdilemez, resolve as birim_coz

logger = logging.getLogger("yerel_hesap.field_stok_tuketici")

#: Olayın hedefi. Bu tüketici YALNIZ stok hedefli olayları okur.
HEDEF_STOK = "stock"

#: Başlangıç durumu — yazıcı bunu koyar.
DURUM_BEKLIYOR = "PENDING"

#: GEÇİCİ talep durumu. Terminal DEĞİLDİR ve bir işlemin dışında ASLA
#: görünmez: talep, hareket ve sonlandırma TEK işlemde yapılır, tek commit
#: ile biter. Süreç arada ölürse işlem geri alınır ve olay `PENDING` kalır —
#: yani "SENT ama hareket yok" ya da "hareket var ama PENDING" durumu
#: OLUŞAMAZ. Bu bir iddia değil, öldürme probuyla ölçülür.
DURUM_TALEP = "CLAIMED"

#: TERMİNAL DURUMLAR. Her biri bir KARARDIR: adı var, sayılır ve
#: `test_field_stok_tuketici.py` içinde iki yönde dondurulmuştur.
DURUM_UYGULANDI = "SENT"            # stok hareketi yazıldı
DURUM_GORUNMEZ = "SKIPPED_SOURCE_NOT_VISIBLE"  # kaynak bu kiracıda YOK
DURUM_URUNSUZ = "SKIPPED_NO_PRODUCT"  # taşınacak ürün yok (hasat, ürünsüz girdi)
DURUM_OLU = "DEAD"                  # deneme hakkı bitti ya da tanınmayan kaynak
#: Ürün BELLİ ama ürünün TABAN BİRİMİ bildirilmemiş: `units.resolve`
#: `TABAN_BILDIRILMEMIS` ile durdu. `SKIPPED_NO_PRODUCT`TAN AYRI BİR KOVADIR
#: ve ayrılığı bir KARARDIR (`units.py` sahip kararı 2): iki kova FARKLI iş
#: gerektirir — ürünsüzde SEZONA ürün bildirilir, burada ÜRÜN KARTINA taban
#: birim yazılır (`PUT /api/products/{id}`). İkisini tek kovaya atmak,
#: `last_error` okuyan kişiyi hangi kaydı düzelteceğini bilmeden bırakırdı.
#:
#: SESSİZ BİR VARSAYIM YERİNE KOVA: girileni taban SAYMAK bir olgu uydurmak
#: olurdu ve "1 ton" ile "1 kg"ı aynı deftere yazardı — 1000× bir hata,
#: hiçbir yerde kırmızı olmadan.
DURUM_TABANSIZ = "SKIPPED_TABAN_BILDIRILMEMIS"

TERMINAL_DURUMLAR = (
    DURUM_UYGULANDI,
    DURUM_GORUNMEZ,
    DURUM_URUNSUZ,
    DURUM_TABANSIZ,
    DURUM_OLU,
)

#: Kaynak tipi -> (tablo, YÖN). Yön +1 ÜRETİM, -1 TÜKETİM.
#: Girdi tüketir, hasat üretir. Bu sözlük yönün TEK kaynağıdır.
#: KAYNAK OKUYUCULARI. Her `text()` çağrısının argümanı SABİT bir metindir.
#: Önce tablo adı `%s` ile gömülüyordu, sonra metin bir DEĞİŞKENDEN
#: geçiriliyordu; ÖLÇÜLDÜ: statik kapı ikisini de "çalışma zamanında kurulan
#: SQL" sayıyor — haklı olarak, çünkü çağrı yerinde okunamayan bir metni
#: kimse gözden geçiremez. Kaynak sayısı iki olduğu için her okuma kendi
#: fonksiyonunda, sabit metinle duruyor.
def _faaliyet_kaynagi(db: Session, firma: int, sid: int):
    return db.execute(
        text(
            """SELECT id, company_id FROM field_activities
            WHERE company_id = :company_id AND id = :sid"""
        ),
        {"company_id": int(firma), "sid": int(sid)},
    ).mappings().first()


def _hasat_kaynagi(db: Session, firma: int, sid: int):
    return db.execute(
        text(
            """SELECT id, company_id FROM field_harvests
            WHERE company_id = :company_id AND id = :sid"""
        ),
        {"company_id": int(firma), "sid": int(sid)},
    ).mappings().first()


#: Fiş kaynağının ADI, TEK YERDE. Hem `_KAYNAK` anahtarı hem de
#: `_zaten_duzeltilmis`in `source_type` süzgeci bu sabiti kullanır: ikisi
#: ayrışırsa düzeltme toplamı SIFIR okunur ve her fiş bir öncekini TEKRAR
#: sayar — sessizce, hiçbir kapı kırılmadan.
KAYNAK_FIS = "field_harvest_ticket"


def _fis_kaynagi(db: Session, firma: int, sid: int):
    return db.execute(
        text(
            """SELECT id, company_id FROM field_harvest_tickets
            WHERE company_id = :company_id AND id = :sid"""
        ),
        {"company_id": int(firma), "sid": int(sid)},
    ).mappings().first()


#: Kaynak tipi -> (tablo, YÖN, okuyucu).
#: Yön +1 ÜRETİM, -1 TÜKETİM; bu sözlük yönün TEK kaynağıdır.
#:
#: FİŞİN YÖNÜ +1'DİR AMA MİKTARI İŞARETSİZ DEĞİLDİR. Fiş yolu bir MİKTAR
#: değil bir FARK yazar (`_fis_kalemleri`) ve fark EKSİ olabilir: kantar
#: hasadın bildirdiğinden az tartarsa düzeltme stoğu DÜŞÜRÜR. Yön yine de
#: +1 kalıyor çünkü işaret farkın KENDİSİNDEDİR; buraya -1 koymak farkı bir
#: kez daha ters çevirirdi.
_KAYNAK = {
    "field_activity": ("field_activities", Decimal("-1"), _faaliyet_kaynagi),
    "field_harvest": ("field_harvests", Decimal("1"), _hasat_kaynagi),
    KAYNAK_FIS: ("field_harvest_tickets", Decimal("1"), _fis_kaynagi),
}

#: FARK YAZAN KAYNAKLAR: farkı SIFIR çıkan olay TERMİNAL biter (`SENT`) ve
#: HİÇBİR SATIR YAZMAZ. Sıfırlık bir hata değil bir CEVAPTIR — "defterde
#: düzeltilecek bir şey yok".
#:
#: NİYE SADECE BU KAYNAKLAR: `field_harvest`/`field_activity` yollarında
#: sıfır miktar GERÇEK bir sıfırdır (sıfır hasat, sıfır girdi) ve onların
#: satırı bugün yazılıyor; o davranışı bu dilim DEĞİŞTİRMİYOR.
_FARK_KAYNAKLARI = frozenset({KAYNAK_FIS})

#: Hareket defterinde bu tüketicinin izi.
HAREKET_REFERANSI = "field_integration_event"
HAREKET_TIPI = "FIELD"

#: Varsayılan deneme tavanı. Tavana varan olay ÖLÜ kovasına düşer; sonsuz
#: yeniden deneme, kuyruğu sessizce büyüten bir kaçış olurdu.
AZAMI_DENEME = 3


#: Talebi KAYBEDEN tüketicinin sonucu. Olayın TERMİNAL DURUMU değildir —
#: olayı kazanan bitirir — ama bu koşumun bir ÇIKTISIDIR ve korunum
#: denkleminde sayılır.
SONUC_KAYBEDILDI = "CLAIM_LOST"

#: BEKLENMEYEN bir istisnadan sonra olayin sonucu. Terminal DEGILDIR — olay
#: PENDING kalir ve bir sonraki dongude yeniden denenir — ama bu KOSUMUN adi
#: konmus bir CIKTISIDIR ve korunum denkleminde `CLAIM_LOST` gibi sayilir.
#:
#: BU KOVA OLMADAN DENEME TAVANI ULASILAMAZDI. Olculdu: `attempts` yalnizca
#: `_talep_et` icinde artiyordu ve o artis, olayi sonlandiran commit ile AYNI
#: islemdeydi; secici ise yalnizca PENDING okuyor. Beklenmeyen bir istisnada
#: islem geri aliniyor, artis da onunla birlikte gidiyordu: olay PENDING'e
#: attempts=0 ile donuyor ve 30 saniyede bir SONSUZA KADAR yeniden deneniyordu.
#: `ORDER BY id` yuzunden de kendi firmasinin kuyrugunda hep BIRINCI kaliyor,
#: ve istisna tum-firmalar dongusunu de kestigi icin SIRADAKI HER FIRMA
#: isleme alinmiyordu. Artis artik AYRI ve KENDI basina commit edilen bir
#: islemde kalici olur, tavan da bu sayede ULASILABILIR.
SONUC_YENIDEN = "RETRY_SCHEDULED"

#: KURTARMA YAZIMININ KENDISI de basarisiz oldu. Terminal DEGILDIR ve olayin
#: veritabanindaki hali DEGISMEMISTIR: `PENDING`, `attempts` ESKI degerinde,
#: `last_error` eski. Olay bir sonraki dongude yeniden alinir; bu kosumda
#: tavan YAKLASMAZ.
#:
#: NEDEN AYRI BIR KOVA: kurtarma kolu, bir veritabani islemi PATLADIGI icin
#: girilir; ayni oturum uzerinden yapilan kurtarma yazimi da (kopan baglanti,
#: `OperationalError`, `PendingRollbackError`) ayni nedenle patlayabilir.
#: Bunu `RETRY_SCHEDULED` saymak YALAN olurdu — hicbir sey kaydedilmedi ve
#: hicbir deneme kalici olmadi. Sessizce yutmak ise gurultulu bir cokusu
#: SESSIZ bir yanlisa cevirirdi. Bu yuzden kova ADI KONMUS, korunum
#: denklemine KATILMIS ve her olusumu `logger.exception` ile GURULTULUDUR.
SONUC_KURTARILAMADI = "RECOVERY_FAILED"

#: BIR FIRMANIN TUM DONGUSU DUSTU. Bu bir OLAY kovasi DEGILDIR: olaylari degil
#: FIRMALARI sayar, `_sayac()` icinde YOKTUR ve iki korunum denkleminin
#: HICBIRINE girmez.
#:
#: NEDEN DENKLEMIN DISINDA: denklem `girdi == kovalarin toplami` der ve
#: `girdi` OLAY sayisidir. Dusen firmanin `olaylari_isle` cagrisi bir sozluk
#: DONDURMEZ, yani ne `girdi`ye ne bir kovaya katki verir; denklemin iki yani
#: da o firmadan HIC etkilenmez ve esitlik AYNEN korunur. Bu terimi denkleme
#: eklemek `cikti`yi `girdi`nin bir fazlasina cikarir ve assert'i PATLATIRDI —
#: `test_field_stok_korunum_denklemi` bu yanlisi zaten KIRMIZI ile karsilar.
#:
#: NEDEN VAR: aksi halde ac kalan bir dongu ancak CIKARILABILIRDI (gunlukteki
#: bir izden). Simdi SAYILIYOR ve kova satirinda gorunuyor.
SONUC_FIRMA_DUSTU = "COMPANY_FAILED"

#: KURTARMA TAZE BIR OTURUMDA BASARILDI. Olayin durumu bu kosumda DEGISTI:
#: deneme KALICI oldu (ya da tavan dolduysa olay OLU yazildi), yani
#: `AZAMI_DENEME` bu yoldan da ULASILABILIR.
#:
#: NEDEN VAR: `RECOVERY_FAILED` olayi PENDING ve `attempts`i DEGISMEMIS
#: birakir. Kusur oturumun kendisindeyse (baglantisi olmus ama veritabani
#: AYAKTA) her dongude ayni sey olur: deneme hic artmaz, tavan hic dolmaz ve
#: olay SONSUZA KADAR yeniden denenir. OLCULDU (PG 16.4): backend
#: `pg_terminate_backend` ile dusuruldugunde `db.rollback()`un KENDISI
#: `AdminShutdown` atiyor, ama AYNI ANDA acilan TAZE bir oturum denemeyi
#: sorunsuz yaziyor. Yani bu sinifta kusur veritabaninda degil OTURUMDADIR ve
#: taze oturum siniri gecerek tavani ULASILABILIR kilar.
#:
#: SINIR (bilincli): kilit catismasi ve BAKIM MODU'nda taze oturum da
#: yazamaz (olculdu: `LockNotAvailable` ve `MaintenanceActiveError`). Orada
#: deneme YAKILMAZ ve olay `RECOVERY_FAILED` kalir — dogrusu budur: bu iki
#: durum KURESELDIR ve olayin zehirli olmasindan degil platformun mesgul
#: olmasindan kaynaklanir; masum olaylari tavana surmek yanlis olurdu.
SONUC_KURTARMA_TAZE = "RECOVERY_ESCALATED"


#: TUKETICININ PG islemlerine konan KILIT bekleme USTU (ms).
#:
#: NEDEN VAR (olculdu, PG 16.4): sunucunun varsayilani `lock_timeout = 0`,
#: yani SINIRSIZ (`SHOW lock_timeout` -> `0`). Bu bastaki tek sinir, PG
#: ikizindeki probun TUKETICI oturumuna koydugu `SET SESSION lock_timeout`
#: idi; taze oturum havuzdan BASKA bir baglanti alir ve onu DEVRALMAZ. Yani
#: olculen sinir kodun degil TESTIN ozelligiydi. Uretimde bu UPDATE, tek
#: thread'li zamanlayicinin ICINDE, SONSUZA KADAR beklerdi — ve o hal hic
#: dongu satiri uretmedigi icin, bu commit'in yerine gectigi gurultulu
#: sinirsiz yeniden denemeden DAHA AZ gorunurdur.
#:
#: DEGER: 3 sn. Buradaki catisma baska bir iscinin UCUSTAKI talebidir ve o
#: tek bir UPDATE + commit'tir (saniye altı); 3 sn durust catisma penceresinin
#: cok ustunde, 30 sn'lik dongu periyodunun (`field_stock_outbox_interval_
#: seconds`) ise onda biridir — yani bekleyen bir yazim bir sonraki donguyu
#: HICBIR halde geciktiremez.
KILIT_ZAMAN_ASIMI_MS = 3000

#: TUKETICININ PG islemlerine konan IFADE USTU (ms).
#:
#: DEGER: 10 sn. `lock_timeout` yalnizca kilit BEKLEMESINI sinirlar; kilidi
#: aldiktan sonra sunucuda asilan bir ifade (tikanmis backend, ag) hala
#: sinirsizdir. Bu ust o artigi kapatir: `lock_timeout`tan BUYUK oldugu icin
#: kilit catismasini asla ONCELEMEZ (hata sinifi spesifik olan kalir), 30 sn'lik
#: dongunun ise ucte biridir — yani en kotu halde bile dongu KOSMAYA devam eder.
IFADE_ZAMAN_ASIMI_MS = 10000


#: BIR DONGUNUN SURE BUTCESI (saniye). `tum_firmalari_isle` bu butceyi asan
#: olaylari o dongude HIC ALMAZ; alinmayan olay `PENDING` kalir ve bir sonraki
#: dongude yeniden secilir.
#:
#: NEDEN VAR (olculdu): dongunun hicbir siniri yoktu ve 20 CATISMALI olay tek
#: dongude 180.58 saniye surdu — olay basina 9.03 sn (uc kilit bekleme bacagi
#: x 3 sn), dogrusal ve SINIRSIZ. `field_stock_outbox_interval_seconds`
#: 1..3600'e dogrulaniyor, yani ayar kodun TUTAMADIGI bir kadans vaat
#: ediyordu: aralik donguler ARASINDAKI beklemedir, dongunun kendisi sinirsiz
#: buyuyebiliyordu.
#:
#: DEGER: 25 sn — varsayilan 30 sn'lik araligin ICINDE, yani doymus bir dongu
#: bile bir sonraki tik vadesinden ONCE biter; olculen catismali maliyetle
#: (9.03 sn/olay) ~3 olay alir ve durur, 180 sn yerine. Ustu asan tek ucustaki
#: olay kendi ustleriyle (kilit 3 sn x 3 bacak) sinirlidir, yani dongu en kotu
#: halde butce + ~9 sn'de biter.
#: TAM SAYI saniyedir, ondalik DEGIL: bu bir kadans korumasi, bir olcum
#: degeri degildir. Saniye alti bir butce ne olculebilir (tek kilit bacagi
#: 3 sn) ne de anlamlidir; tam sayi tutmak degeri ikili kayan noktanin
#: yuvarlamasindan da (`app/` genelinde `float` YASAK) kurtarir. Vade
#: `time.monotonic_ns()` ile TAM SAYI nanosaniyede tutulur — tam, monoton,
#: yuvarlamasiz.
DONGU_SURE_BUTCESI_SANIYE = 25

#: Saniye -> nanosaniye. Vade aritmetigi TAM SAYI uzerinde yurur.
NANOSANIYE = 1_000_000_000

#: FIRMA BASINA DONGU PARTISI. Deadline zamani sinirlar, bu sinir ise tek
#: firmanin dev kuyrugunun butcenin tamamini yutmasini onler ve siradaki
#: firmalara sira gelmesini saglar.
#:
#: DEGER: 200 — olculen catismasiz maliyet milisaniye duzeyinde, yani 200 olay
#: saniyeler icinde biter ve 30 sn'lik araligin cok icinde kalir; catismali
#: halde ise zaten sure butcesi devreye girer.
AZAMI_PARTI = 200


#: `last_error`e yazilan metnin USTU. Sutun BUGUN sinirsiz (`sa.Text()`,
#: olculdu: PG 16.4 `information_schema` -> data_type=text, maxlen=UNBOUNDED),
#: ama YAZILAN metin sinirsiz DOGAR: `mesaj` bir istisnanin `str()`idir ve
#: SQLAlchemy hatalari SQL ifadesini ve PARAMETRELERI de tasir (olculdu: sade
#: bir `UndefinedTable` icin 314 karakter).
#:
#: NEDEN KAPI: sinirsiz metni sinirli olabilecek bir sutuna yazmak, bu deponun
#: BIR KEZ GONDERDIGI kusurun ta kendisidir (`status` VARCHAR(20) vs 26
#: karakter, goc 0061). O kusur burada OLUMCUL olurdu: kurtarma yazimi HER
#: dongude AYNI metinle patlar, `attempts` HIC artmaz ve olay sonsuza kadar
#: denenir — yani transient degil BELIRLENIMCI bir sonsuz dongu. Metni
#: kaynakta sinirlamak o sinifi bir gocun insafina birakmadan kapatir.
AZAMI_HATA_METNI = 500


def _kisalt(hata: str | None) -> str | None:
    """`last_error` metnini USTE kirpar. None gecirilirse None doner."""
    if hata is None:
        return None
    metin = str(hata)
    if len(metin) <= AZAMI_HATA_METNI:
        return metin
    return metin[:AZAMI_HATA_METNI - 3] + "..."


#: HAM istisna metninin ISARETI. Okuma yuzeyinin arindirmasi
#: (`routers/entegrasyon_olaylari._gerekceyi_arindir`) saklanmis metni TAM
#: OLARAK bu isaretten keser; isareti TASIMAYAN metin arindirmanin YANINDAN
#: gecer ve `farm.view` tasiyan salt okur rollere AYNEN sunulur.
#:
#: NEDEN SABIT: bu modulde isareti yazan IKI yer var (beklenmeyen istisna kolu
#: ve `_depo_gerekcesi`). Uclusu ayri ayri yazili literal olsaydi, birinde
#: degisen tek kelime otekilerde sessizce eski kalirdi — o kaymanin ta kendisi
#: `tests/test_entegrasyon_olaylari_onek_baglantisi.py`in var olma nedenidir.
#: Modul ici bag BURADA kurulur; modul DISI bag (bu isaret ile ucun
#: `_HAM_ISTISNA_ONEKI`i) o dosyada KOSTURULARAK olculur.
_HAM_ISTISNA_ONEKI = "beklenmeyen hata: "


#: `inventory.default_warehouse`in KURATE gerekce KUMESI: bu kolun ONEKSIZ,
#: yani ARINDIRILMADAN gecirmesine izin verilen metinlerin TAMAMI.
#:
#: OLCULDU (CPython 3.12, AST ile): `default_warehouse` bir YAPRAKTIR — govdesi
#: yalniz `db.execute`, `select` ve `int` cagirir, baska hicbir uygulama
#: fonksiyonuna inmez — ve TEK bir `raise` tasir:
#: `RuntimeError("Aktif depo bulunamadı")`. Metin duz bir literaldir; icine
#: hicbir yakalanmis istisna interpole EDILMEZ.
#:
#: NEDEN KUME, NEDEN SABITIN KENDISI DEGIL: bu kol `str(hata)`yi kume UYELIGI
#: ile suzer, dogrudan bu sabiti YAZMAZ. Fark, `default_warehouse` bir gun
#: ikinci bir `RuntimeError` atarsa ortaya cikar: sabit yazilsaydi o YENI
#: gerekce, kullaniciya AYNEN "Aktif depo bulunamadı" diye YALAN soylenerek
#: sunulurdu. Uyelik suzgeci ise onu isaretler ve arindirilir — yani
#: bilinmeyen metin SESSIZCE YANLIS degil, GORUNUR bicimde SAKLI olur.
#: `RuntimeError`in ALT SINIFLARI (`RecursionError`, `NotImplementedError`)
#: da bu kola duser ve ayni suzgecten gecer.
KURATE_DEPO_GEREKCELERI = frozenset({"Aktif depo bulunamadı"})


def _depo_gerekcesi(hata: BaseException) -> str:
    """Depo cozumleme kolunun `last_error` metnini kurar. FAIL CLOSED.

    KURATE metin AYNEN gecer — o cumlenin tum degeri okuyana HANGI KAYDI
    duzeltecegini soylemesindedir ve arindirilirsa yuzey degersizlesir.
    KURATE OLMAYAN her metin ISARETLENIR, yani okuma yuzeyinde sabit cumleye
    indirgenir.

    NE KAZANILIR: bu kol boylece ARTIK bir HAM ISTISNA yazim yeri DEGILDIR.
    Once guvenligi `except RuntimeError`in DARLIGINA dayaniyordu — surucu /
    ORM hatalarinin o kola HIC dusmedigi olgusuna. O olgu bu modulde HICBIR
    yerde sinanmiyordu ve tek kelimelik bir genisletme (`except Exception`)
    onu sessizce cururtuyordu; OLCULDU: o mutasyon altinda sunulan metin ham
    `[SQL: ...] [parameters: ...]` oluyor ve TUM kosum YESIL kaliyordu.
    Suzgec, kolun DARLIGINI TASIYICI olmaktan cikarir: kol genisletilse bile
    disari cikan metin isaretli, yani arindirilmis olur.
    """
    metin = str(hata)
    if metin in KURATE_DEPO_GEREKCELERI:
        return metin
    return "%s%s: %s" % (_HAM_ISTISNA_ONEKI, type(hata).__name__, metin)


def _oturumu_sinirla(oturum: Session) -> None:
    """Verilen oturumun ISLEMINE KILIT ve IFADE ustu koyar. FAIL CLOSED.

    TAZE OTURUMA OZEL DEGILDIR. Ayni ust TUKETICININ KENDI oturumuna da
    konur (`_bir_olayi_isle`, `_kurtar`); bkz. oradaki gerekceler.

    ISLEM KAPSAMLI (`SET LOCAL` esdegeri) KULLANILIR, `SET SESSION` DEGIL — bilincli. Bu oturum havuzdan
    odunc alinmis bir baglantidir ve kapandiginda baglanti havuza GERI DONER;
    `SET SESSION` orada KALIR ve sonraki ISTEGE sizardi. Bu deponun bu bastaki
    olculen kusuru tam olarak o sinifin ta kendisidir (testin TUKETICI
    oturumuna koydugu `SET SESSION lock_timeout`, havuz uzerinden TAZE oturuma
    sizip kodun sahip olmadigi bir siniri varmis gibi gosteriyordu). Islem kapsamli
    ayar islemle birlikte biter, yani ayni tuzagi kurmaz.

    YALNIZ PostgreSQL'DE. SQLite'ta `set_config` diye bir sey yoktur ve
    oturumlar arasi satir kilidi de yoktur; orada bu ust ANLAMSIZDIR.

    FAIL CLOSED: ust KONAMAZSA istisna YUKARI CIKAR ve yazim HIC DENENMEZ.
    Onceki hal fail-open'di: istisna yutulur, yazim ustsuz denenirdi — yani
    ustun tam olarak koruyacagi kosulda (bozuk oturum, erisilemeyen sunucu)
    yazim SINIRSIZ kosardi. Simdi her cagiran bu istisnayi kendi `except`
    kolunda yakalar ve olay ADI KONMUS bir kovaya (`RECOVERY_FAILED`) duser:
    veritabaninda HICBIR SEY degismez, olay `PENDING` kalir, deneme YAKILMAZ —
    kusur olayin degil OTURUMUN/PLATFORMUN oldugu icin kilit catismasi ve
    bakim moduyla ayni politika. Basarisizlik GURULTULUDUR.
    """
    if oturum.get_bind().dialect.name != "postgresql":
        return
    try:
        # `set_config(..., is_local => true)` == `SET LOCAL`, ama SQL SABIT
        # kalir ve deger BAGLI PARAMETRE olarak gider. `SET LOCAL x = '%d'`
        # bicimi calisma zamaninda SQL KURARDI; bu modulde bugun hic dinamik
        # SQL yok ve `test_every_dynamic_text_call_is_exactly_reviewed` bunu
        # HAKLI OLARAK reddediyor (olculdu: kapi bu bicimi KIRMIZI ile
        # karsiladi). `SET` bind parametresi kabul etmez, `set_config` eder.
        oturum.execute(
            text("SELECT set_config('lock_timeout', :deger, true)"),
            {"deger": "%dms" % KILIT_ZAMAN_ASIMI_MS},
        )
        oturum.execute(
            text("SELECT set_config('statement_timeout', :deger, true)"),
            {"deger": "%dms" % IFADE_ZAMAN_ASIMI_MS},
        )
    except Exception:  # noqa: BLE001 - gurultu + YENIDEN FIRLATMA (fail closed)
        logger.exception(
            "Field stok oturumuna zaman asimi ustu KONAMADI; FAIL CLOSED: "
            "ustsuz yazim DENENMEYECEK, olay PENDING kalacak"
        )
        raise


def _talep_et(db: Session, firma: int, olay_id: int) -> bool:
    """Olayı ATOMİK olarak talep eder. Yalnız KAZANAN True alır.

    Tek ifade: `PENDING` -> `CLAIMED`. İki tüketici aynı anda koşarsa
    veritabanı satırı kilitler; ikincisi birincinin commit'ini bekler ve
    sonra 0 satır eşler. "Kazandım mı" kararı bu UPDATE'in rowcount'una
    dayanır ve ÖLÇÜLDÜ (bkz. test): kosullu UPDATE hem sqlite3 hem psycopg
    için ilk çağrıda 1, ikincide 0 döndürüyor. Tuzak `INSERT ... RETURNING`
    içindi; bu ifade o biçim DEĞİLDİR.
    """
    return db.execute(
        text(
            """UPDATE field_integration_events
            SET status = :talep, attempts = attempts + 1, updated_at = :simdi
            WHERE company_id = :company_id AND id = :id
              AND status = :bekliyor"""
        ),
        {
            "talep": DURUM_TALEP, "simdi": utcnow(), "company_id": int(firma),
            "id": int(olay_id), "bekliyor": DURUM_BEKLIYOR,
        },
    ).rowcount == 1


def _sayac() -> dict[str, int]:
    sayac = {
        "girdi": 0, SONUC_KAYBEDILDI: 0, SONUC_YENIDEN: 0,
        SONUC_KURTARILAMADI: 0, SONUC_KURTARMA_TAZE: 0,
    }
    for durum in TERMINAL_DURUMLAR:
        sayac[durum] = 0
    return sayac


def _olayi_sonlandir(
    db: Session, firma: int, olay_id: int, durum: str, hata: str | None,
    deneme: int
) -> None:
    """Olayi TERMINAL yazar. ANA YOL: kosulsuz ve `attempts` MUTLAK.

    Burada kosula GEREK YOKTUR: bu yola gelen oturum olayi `CLAIMED` olarak
    KAZANMISTIR, yani baska bir isci ayni satiri sonlandiramaz. Kurtarma
    yolunun ihtiyaci farklidir; bkz. `_olayi_sonlandir_bekleyeni`.
    """
    db.execute(
        text(
            """UPDATE field_integration_events
            SET status = :durum, last_error = :hata, attempts = :deneme,
                processed_at = :simdi, updated_at = :simdi
            WHERE company_id = :company_id AND id = :id"""
        ),
        {
            "durum": durum, "hata": _kisalt(hata), "deneme": deneme,
            "simdi": utcnow(), "id": int(olay_id),
            "company_id": int(firma),
        },
    )


def _olayi_sonlandir_bekleyeni(
    db: Session, firma: int, olay_id: int, durum: str, hata: str | None
) -> None:
    """KURTARMA YOLU: yalnizca hala `PENDING` olan satiri terminal yazar ve
    `attempts`i BAGIL artirir.

    NEDEN AYRI BIR IFADE VAR. Kurtarma kolunun tavan yazimi `_olayi_sonlandir`i
    cagiriyordu: KOSULSUZ, ve `attempts`i dongunun BASINDAKI anlik goruntuden
    turemis MUTLAK bir degerle. `--workers N` ile N tuketici kosar ve iki isci
    ayni olayin anlik goruntusunu okuyabilir. Isci A kurtarma kolunda tavani
    doldurup yazmaya hazirlanirken isci B olayi talep edip BITIRMIS (`SENT`)
    olabilir: A'nin kosulsuz UPDATE'i o terminal satiri `DEAD`e cevirir ve
    sayaci kendi ESKI goruntusune geri yurutur — yani UYGULANMIS bir olay OLU
    gorunur. `status = :bekliyor` kosulu A'yi 0 satirla dondurur; `attempts + 1`
    de sayacin geri yurumesini engeller.

    BU TEHLIKE ONCEDEN VARDI (`_kurtar`in tavan yazimi bu bastan ONCE de
    kosulsuz ve mutlakti). Bu dal onu YARATMADI, `_taze_oturumda_kurtar` ile
    IKINCI bir yola TASIDI. Iki yol da artik bu ifadeyi kullanir, yani yapilan
    sey bir GENISLETME degil DARALTMADIR.

    SQL CALISMA ZAMANINDA KURULMAZ. Iki bicimi tek bir ifadede `%` ile
    birlestirmek kolaydi ama `test_every_dynamic_text_call_is_exactly_reviewed`
    bunu HAKLI OLARAK reddeder: bu modulde bugun hic dinamik SQL yok ve oyle
    kalmalı. Iki AYRI sabit ifade, o kapiyi gevsetmeden ayni isi yapar.

    `attempts + 1` YINELEMEZ: cagiran buraya gelmeden `db.rollback()` yapar,
    yani `_talep_et`in artisi geri alinmistir.
    """
    db.execute(
        text(
            """UPDATE field_integration_events
            SET status = :durum, last_error = :hata, attempts = attempts + 1,
                processed_at = :simdi, updated_at = :simdi
            WHERE company_id = :company_id AND id = :id
              AND status = :bekliyor"""
        ),
        {
            "durum": durum, "hata": _kisalt(hata), "simdi": utcnow(),
            "id": int(olay_id), "company_id": int(firma),
            "bekliyor": DURUM_BEKLIYOR,
        },
    )


def _denemeyi_kaydet(
    db: Session, firma: int, olay_id: int, hata: str
) -> None:
    """Denemeyi KALICI yapar ve olayi PENDING birakir.

    `_olayi_sonlandir`dan tek farki: `status` DEGISMEZ ve `processed_at`
    YAZILMAZ; olay yeniden denenmeye uygun kalir. Cagiran bunu, basarisiz
    olayin islemi geri alindiktan SONRA ayri bir islemde commit eder.

    ARTIS BAGILDIR (`attempts + 1`), MUTLAK DEGIL. Onceden buraya cagiranin
    `deneme` degeri MUTLAK yaziliyordu; o deger dongunun basindaki anlik
    SELECT'ten (`olay["attempts"]`) turuyordu. Zamanlayici lifespan'dan
    baslatildigi icin `--workers N` N tane tuketici demektir: iki isci ayni
    olayin anlik goruntusunu okur, biri once basarisiz olup `attempts`i
    kalici olarak artirir, digeri ise KENDI eski goruntusunden hesapladigi
    degeri MUTLAK yazip o artisi SESSIZCE SILERDI — sayaci geri yuruten bir
    KAYIP GUNCELLEME. `_talep_et` zaten `attempts + 1` yapiyordu; kurtarma
    yazimi da artik ayni bicimde bagil ve bu yuzden birikimlidir.

    Cagiran bu fonksiyona GELMEDEN once `db.rollback()` yapar, yani
    `_talep_et`in artisi geri alinmistir ve buradaki `+ 1` onu YINELEMEZ.
    """
    db.execute(
        text(
            """UPDATE field_integration_events
            SET last_error = :hata, attempts = attempts + 1, updated_at = :simdi
            WHERE company_id = :company_id AND id = :id"""
        ),
        {
            "hata": _kisalt(hata), "simdi": utcnow(),
            "id": int(olay_id), "company_id": int(firma),
        },
    )


def _hareket_yaz(
    db: Session, firma: int, urun: int, depo: int, miktar: Decimal, olay_id: int,
    not_metni: str,
) -> None:
    """Defter satırı + depo/ürün stoğu. Miktar İŞARETLİDİR."""
    db.execute(
        text(
            """INSERT INTO stock_movements(
            product_id,movement_type,quantity,movement_date,reference_type,
            reference_id,note,company_id,warehouse_id
            ) VALUES(:urun,:tip,:miktar,:tarih,:ref_tip,:ref_id,:not_metni,
                     :company_id,:depo)"""
        ),
        {
            "urun": int(urun), "tip": HAREKET_TIPI, "miktar": miktar,
            "tarih": utcnow(), "ref_tip": HAREKET_REFERANSI,
            "ref_id": int(olay_id), "not_metni": not_metni,
            "company_id": int(firma), "depo": int(depo),
        },
    )
    # Tarla tüketimi stoğu EKSİYE düşürebilir ve düşürmelidir: ölçülen kusurda
    # çiftçi elinde olmayan 200 kg tohumu ekmişti. Fiziksel gerçeği gizlemek
    # yerine göstermek gerekiyor.
    adjust_warehouse_stock(
        db, int(firma), int(depo), int(urun), miktar, allow_negative=True
    )
    sync_product_stock(db, int(firma), int(urun))


def _taban_miktar(miktar, birim, taban_birim) -> Decimal:
    """Girilen miktarı ürünün TABAN birimine çevirir. Çeviremezse ATAR.

    Tek satırlık bir sarmalayıcı ama TEK KAPI olması önemli: hasat yolu ve
    fiş yolu AYNI çeviriyi kullanmak zorunda, yoksa fişin farkı hasadın
    miktarından FARKLI bir ölçekte çıkar ve fark sessizce anlamsızlaşır.

    `units.resolve` bir demet döndürür (`(base_quantity, factor_used)`);
    burada yalnız ÜRÜN kullanılıyor. `factor_used` ATILIYOR ve bu bilinçli:
    katsayı bir KANITTIR ve kanıtın durduğu yer fişin SATIRIDIR
    (`field_harvest_tickets.entered_factor`), hareket defteri değil — defter
    birim taşımıyor, katsayıyı orada saklamak taşınıyormuş izlenimi verirdi.

    İSTİSNA YUTULMAZ: `BirimCozulemedi` ailesi çağırana kadar çıkar ve
    `_bir_olayi_isle` onu `.sebep`e göre ADI KONMUŞ kovalara ayırır.
    """
    return birim_coz(Decimal(str(miktar)), str(birim or ""), taban_birim)[0]


def _hasati_serilestir(db: Session, firma: int, fis_id: int) -> None:
    """Aynı hasadın fiş olaylarını HASAT SATIRINDA serileştirir.

    ÖLÇÜLEN KUSUR (mercek, READ COMMITTED): iki fiş olayı aynı hasat için
    eşzamanlı işlendiğinde ikisi de `_zaten_duzeltilmis`i 0 okudu ve 25/25
    koşum +2400 yazdı; doğru fark +1200 idi. `_talep_et` OLAYI serileştirir,
    HASADI değil — iki ayrı olay, iki ayrı talep, tek hasat.

    ÇAĞRI YERİ: `_bir_olayi_isle` içinde, `_fis_kalemleri` / `_zaten_duzeltilmis`
    OKUNMADAN ÖNCE. Kilit commit'e kadar tutulur (talep + hareket + sonlandırma
    tek işlem).

    PostgreSQL: `SELECT id FROM field_harvests WHERE id=:hid AND company_id=:cid
    FOR UPDATE` — ikinci oturum birincinin commit'ini bekler, sonra `zaten`'i
    görür.

    SQLite: tek yazar — yazmalar veritabanı düzeyinde zaten seri. `FOR UPDATE`
    sözdizimi reddedilir ve kilit zaten gereksizdir; bu dal no-op (SQL
    KOSMAZ). İki AYRI sabit ifade; SQL çalışma zamanında KURULMAZ — bu
    modülde dinamik SQL yoktur ve `test_every_dynamic_text_call_is_exactly_
    reviewed` onu haklı olarak reddeder.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    hid = db.execute(
        text(
            """SELECT harvest_id FROM field_harvest_tickets
            WHERE id = :tid AND company_id = :cid"""
        ),
        {"tid": int(fis_id), "cid": int(firma)},
    ).scalar()
    if hid is None:
        return
    db.execute(
        text(
            """SELECT id FROM field_harvests
            WHERE id = :hid AND company_id = :cid FOR UPDATE"""
        ),
        {"hid": int(hid), "cid": int(firma)},
    ).first()


def _fis_kalemleri(db: Session, firma: int, fis_id: int) -> list[dict]:
    """Fişin defterde açtığı FARK. Miktar DEĞİL, DELTA döner.

    --- NİYE FARK, NİYE MİKTAR DEĞİL ------------------------------------

    Hasat yazıldığında deftere hasadın BİLDİRDİĞİ miktar girdi. Kantar
    sonradan başka bir sayı söylüyor. Doğru cevap "fişin netini de yaz"
    DEĞİLDİR — o, aynı ürünü iki kez üretirdi. Doğru cevap defteri o hasat
    için fişlerin netine GETİREN farktır:

        delta = Σ(fiş netleri, taban birimde)
              − hasadın taban miktarı
              − BU HASAT İÇİN daha önce yazılmış fiş düzeltmeleri

    Üçüncü terim (`zaten_duzeltilmis`) OLMADAN ikinci fiş birinciyi TEKRAR
    sayardı: iki fişten sonra defter netin iki katına giderdi. Terim
    ölçülüyor, varsayılmıyor — `stock_movements`tan OKUNUYOR.

    --- NET NEREDEN GELİYOR ---------------------------------------------

    `routers.farm._turetilmis_net` ile, İTHAL EDİLEREK. Formül orada tek
    yerde duruyor (`net = brüt − Σ(brüt × oran/100)`, TOPLAMSAL, tek
    yuvarlama, ROUND_HALF_UP). Burada KOPYALANSAYDI iki formül bir gün
    ayrışırdı ve ayrışma, okuma yüzeyi (`derived_net_quantity`) ile defterin
    FARKLI net söylemesi demek olurdu — hiçbir yerde kırmızı vermeden.

    Uygulandığı sayı `base_quantity`dir, `gross_entered_quantity` DEĞİL:
    oranlar yüzdedir ve yüzde ölçekten bağımsızdır, ama defter TABAN
    birimde tutulur.

    --- KOVALAR ----------------------------------------------------------

    * Fişin hasadının sezonunda ürün YOKSA -> boş liste -> `SKIPPED_NO_PRODUCT`
      (hasat yolunun kardeşi; ürün UYDURULMAZ).
    * Ürün var, `base_unit` yoksa -> `_taban_miktar` `TABAN_BILDIRILMEMIS`
      atar -> `SKIPPED_TABAN_BILDIRILMEMIS`.
    * Fark SIFIR -> liste DOLU ama miktar 0 -> `SENT`, satır YAZILMAZ
      (`_FARK_KAYNAKLARI`).

    BÜTÜN OKUMALAR KİRACI KAPSAMLI ve birleştirmeler kiracı İÇİNDE.
    """
    fis = db.execute(
        text(
            """SELECT t.harvest_id AS harvest_id, s.product_id AS product_id,
                   h.quantity AS harvest_quantity, h.unit AS harvest_unit,
                   u.base_unit AS base_unit
            FROM field_harvest_tickets t
            JOIN field_harvests h
              ON h.id = t.harvest_id AND h.company_id = t.company_id
            JOIN crop_seasons s
              ON s.id = h.season_id AND s.company_id = h.company_id
            LEFT JOIN products u
              ON u.id = s.product_id AND u.company_id = h.company_id
            WHERE t.company_id = :company_id AND t.id = :tid
              AND s.product_id IS NOT NULL"""
        ),
        {"company_id": int(firma), "tid": int(fis_id)},
    ).mappings().first()
    if fis is None:
        # Ürünsüz sezon ya da (kısıtın engellediği) kopuk zincir: ürün
        # UYDURULMAZ, olay `SKIPPED_NO_PRODUCT` kovasına düşer.
        return []

    hasat_id = int(fis["harvest_id"])
    taban_birim = fis["base_unit"]
    hasat_taban = _taban_miktar(
        fis["harvest_quantity"], fis["harvest_unit"], taban_birim
    )

    # O HASADIN BÜTÜN FİŞLERİ. Bu olayın fişi de dahil — fark hasat başına
    # hesaplanır, fiş başına değil. `entered_unit`/`entered_factor` burada
    # OKUNMUYOR çünkü `base_quantity` fiş yazılırken ZATEN çözülmüş ve
    # satırda duruyor; ikinci kez çözmek o günün katsayısını BUGÜNÜN
    # katsayısıyla değiştirmek olurdu (`units.py` sahip kararı 1).
    fisler = db.execute(
        text(
            """SELECT id, base_quantity FROM field_harvest_tickets
            WHERE company_id = :company_id AND harvest_id = :hid"""
        ),
        {"company_id": int(firma), "hid": hasat_id},
    ).mappings().all()
    kesintiler = _fis_kesinti_oranlari(db, firma, [int(f["id"]) for f in fisler])
    fis_neti = sum(
        (
            _turetilmis_net(
                normalize_quantity(f["base_quantity"]), kesintiler.get(int(f["id"]), [])
            )
            for f in fisler
        ),
        Decimal("0"),
    )

    zaten = _zaten_duzeltilmis(db, firma, hasat_id, int(fis["product_id"]))
    return [
        {
            "id": int(fis_id),
            "product_id": int(fis["product_id"]),
            "quantity": normalize_quantity(fis_neti - hasat_taban - zaten),
        }
    ]


def _fis_kesinti_oranlari(
    db: Session, firma: int, fis_kimlikleri: list[int]
) -> dict[int, list[dict]]:
    """Fiş -> kesinti satırları. `_turetilmis_net`in beklediği biçimde.

    Boş listeyle çağrılabilir ve o zaman SQL'e HİÇ inmez: `IN ()` diyalekte
    göre ya sözdizimi hatası ya da sessizce boş küme olurdu.
    """
    if not fis_kimlikleri:
        return {}
    satirlar = db.execute(
        text(
            """SELECT ticket_id, rate_percent
            FROM field_harvest_ticket_deductions
            WHERE company_id = :company_id AND ticket_id IN :ids"""
        ).bindparams(bindparam("ids", expanding=True)),
        {"company_id": int(firma), "ids": [int(i) for i in fis_kimlikleri]},
    ).mappings().all()
    gruplar: dict[int, list[dict]] = {int(i): [] for i in fis_kimlikleri}
    for satir in satirlar:
        gruplar[int(satir["ticket_id"])].append(
            {"rate_percent": satir["rate_percent"]}
        )
    return gruplar


def _zaten_duzeltilmis(
    db: Session, firma: int, hasat_id: int, urun_id: int
) -> Decimal:
    """BU HASAT için daha önce yazılmış FİŞ düzeltmelerinin toplamı.

    HAREKET OLAYA `reference_id` İLE BAĞLI, kaynağa DEĞİL: `stock_movements`
    fişin ya da hasadın kimliğini taşımıyor, taşıdığı tek şey OLAY kimliği
    (`reference_type='field_integration_event'`, `reference_id=<olay id>`).
    Bu yüzden hasada varmak için `field_integration_events` ÜZERİNDEN
    geçiliyor — ölçüldü, başka yol yok.

    SÜZGEÇ `source_type='field_harvest_ticket'`: hasadın KENDİ olayının
    yazdığı asıl hareket bu toplama GİRMEZ. Girseydi hasat miktarı iki kez
    çıkarılırdı (`hasat_taban` zaten ayrı bir terim).

    `source_id` fişlerin kimlikleridir ve hasada `field_harvest_tickets`
    üzerinden bağlanır; ÜÇ tablo da KİRACI KAPSAMLI okunuyor.
    """
    toplam = db.execute(
        text(
            """SELECT COALESCE(SUM(m.quantity), 0)
            FROM stock_movements m
            JOIN field_integration_events e
              ON e.id = m.reference_id AND e.company_id = m.company_id
            JOIN field_harvest_tickets t
              ON t.id = e.source_id AND t.company_id = e.company_id
            WHERE m.company_id = :company_id
              AND m.reference_type = :ref_tip
              AND m.product_id = :urun
              AND e.source_type = :kaynak_tipi
              AND t.harvest_id = :hid"""
        ),
        {
            "company_id": int(firma), "ref_tip": HAREKET_REFERANSI,
            "urun": int(urun_id), "kaynak_tipi": KAYNAK_FIS, "hid": int(hasat_id),
        },
    ).scalar()
    return normalize_quantity(toplam or 0)


def _faaliyet_kalemleri(db: Session, firma: int, faaliyet_id: int) -> list[dict]:
    """Faaliyetin ÜRÜNE BAĞLI girdileri. Ürünsüz girdi stok taşıyamaz."""
    satirlar = db.execute(
        text(
            """SELECT id, product_id, quantity FROM field_activity_inputs
            WHERE company_id = :company_id AND activity_id = :aid
              AND product_id IS NOT NULL"""
        ),
        {"company_id": int(firma), "aid": int(faaliyet_id)},
    ).mappings().all()
    return [dict(s) for s in satirlar]


def _hasat_kalemleri(db: Session, firma: int, hasat_id: int) -> list[dict]:
    """Hasadın ürünü: SEZON bildirir, hasat DEVRALIR (göç 20260827_0062).

    `_faaliyet_kalemleri`nin KARDEŞİ ve SÖZLEŞMESİ AYNI: `id`, `product_id`,
    `quantity` anahtarlı sözlüklerden oluşan bir liste. Çağıran ikisini AYIRT
    ETMEZ — aynı döngü `Decimal(str(kalem["quantity"])) * yon` ile hareketi
    yazar; yön `_KAYNAK`tan gelir ve hasat için +1'dir (ÜRETİM).

    FAALİYETTEN TEK YAPISAL FARK: bir faaliyetin BİRDEN ÇOK girdisi olabilir,
    bir hasat satırı ise TEK kalemdir. Liste yine de dönüyor, çünkü sözleşme
    çağıranda tek: liste boşsa olay `SKIPPED_NO_PRODUCT` kovasına düşer.

    `field_harvests.unit` ARTIK OKUNUYOR VE ÇEVRİLİYOR (C2). ÖNCEDEN HAM
    yazılıyordu ve bu ÖLÇÜLMÜŞ bir 1000× riskiydi: `unit="ton"` giren bir
    hasat deftere `quantity` kadar KİLO yazıyordu, çünkü hareket defteri
    birim TAŞIMIYOR ve miktarın ürünün TABAN biriminde olduğu VARSAYILIYOR.
    Varsayım hiçbir yerde sınanmadığı için hata kırmızı vermez, CEVAP verir.

    Bayrak ürette prod'da KAPALI olduğu için CANLI VERİ YOK — yani bu bir
    düzeltme değil, bir KAPI: `units.resolve` çevirmeyi yapar ve çeviremediği
    yerde ADI KONMUŞ bir kovaya düşer (`SKIPPED_TABAN_BILDIRILMEMIS`).
    Sessizce ham yazmaya dönüş, testte `unit="ton"`/`base_unit="KG"` ile
    1000× olarak çivilendi.

    `product_id IS NOT NULL` KOŞULU KARDEŞİYLE AYNI GEREKÇEDEDİR ve
    KALDIRILAMAZ: sütun NULL kabul eder (kabul etmek zorunda — bkz. göç
    başlığı), ve ürünü bildirilmemiş sezonun hasadı ürün UYDURULARAK
    yazılamaz. Bu koşul, `SKIPPED_NO_PRODUCT` kovasını ERİŞİLEBİLİR tutan
    satırdır.

    BİRLEŞTİRME İKİ YANDAN DA KİRACI KAPSAMLIDIR (`h.company_id` VE
    `s.company_id`). Sezonu başka firmada olan bir hasat — veritabanı kısıtı
    bunu zaten engelliyor — burada da ürünsüz görünür, sessizce başka
    firmanın ürününü taşımaz.
    """
    satirlar = db.execute(
        text(
            """SELECT h.id AS id, s.product_id AS product_id,
                   h.quantity AS quantity, h.unit AS unit,
                   u.base_unit AS base_unit
            FROM field_harvests h
            JOIN crop_seasons s
              ON s.id = h.season_id AND s.company_id = h.company_id
            LEFT JOIN products u
              ON u.id = s.product_id AND u.company_id = h.company_id
            WHERE h.company_id = :company_id AND h.id = :hid
              AND s.company_id = :company_id
              AND s.product_id IS NOT NULL"""
        ),
        {"company_id": int(firma), "hid": int(hasat_id)},
    ).mappings().all()
    return [
        {
            "id": satir["id"],
            "product_id": satir["product_id"],
            "quantity": _taban_miktar(
                satir["quantity"], satir["unit"], satir["base_unit"]
            ),
        }
        for satir in satirlar
    ]


#: Kaynak tipi -> stok taşıyacak KALEMLERİ okuyan işlev.
#: ANAHTAR KÜMESİ `_KAYNAK` İLE AYNI OLMAK ZORUNDA. Eksik bir anahtar sessiz
#: değil, ADI KONMUŞ bir yanlış cevap üretirdi: o kaynağın HER olayı sonsuza
#: kadar `SKIPPED_NO_PRODUCT` kovasına düşer ve hiçbir yerde hata görünmez —
#: bu dosyanın kapattığı kusurun TAM OLARAK kendisi. Eşitlik
#: `tests/test_field_stok_tuketici.py` içinde dondurulmuştur.
_KALEM_OKUYUCU = {
    "field_activity": _faaliyet_kalemleri,
    "field_harvest": _hasat_kalemleri,
    KAYNAK_FIS: _fis_kalemleri,
}

#: `SKIPPED_NO_PRODUCT` kovasının GEREKÇESİ, kaynak tipine göre.
#: Kova tek ama ORAYA DÜŞME SEBEBİ tek değil, ve ikisi FARKLI işler gerektirir:
#: hasat için yapılacak şey SEZONA ürün bildirmek, faaliyet için GİRDİYE. Tek
#: bir genel metin, `last_error`ı okuyan kişiyi hangi kaydı düzelteceğini
#: bilmeden bırakırdı.
_URUNSUZ_VARSAYILAN = "stok taşıyacak ürün bağı yok (%s)"
_URUNSUZ_GEREKCE = {
    "field_harvest": (
        "sezonun ürünü bildirilmemiş; hasat stok taşıyamaz "
        "(%s -> crop_seasons.product_id NULL)"
    ),
    "field_activity": "ürüne bağlı girdi yok; faaliyet stok taşıyamaz (%s)",
    KAYNAK_FIS: (
        "fişin hasadının sezonunda ürün bildirilmemiş; fiş stok taşıyamaz "
        "(%s -> crop_seasons.product_id NULL)"
    ),
}

#: `SKIPPED_TABAN_BILDIRILMEMIS` kovasının GEREKÇESİ. Kova tek, çare tek:
#: ürün kartına taban birim yazmak. Kaynak tipi metne giriyor ki hangi
#: olayın düştüğü `last_error`dan okunabilsin.
_TABANSIZ_GEREKCE = (
    "ürünün taban birimi bildirilmemiş; miktar tabana çevrilemedi (%s). "
    "Çare ürün kartındadır: PUT /api/products/{id} ile base_unit yazın. "
    "Girileni taban SAYMAK bir olgu uydurmak olurdu (units.py, sahip kararı 2)."
)


def _taze_oturumda_kurtar(
    db: Session, firma: int, olay_id: int, deneme: int, azami_deneme: int,
    mesaj: str,
) -> str:
    """Kurtarma yazimini TAZE bir oturumda dener; HER YOL bir kova dondurur.

    NEDEN: `RECOVERY_FAILED` olayi `PENDING` ve `attempts`i DEGISMEMIS birakir.
    Kusur oturumun kendisindeyse tavan HIC dolmaz ve olay sonsuza kadar
    denenir. Taze oturum, olu baglantiyi olayin kaderinden AYIRIR.

    TAVAN KARARI BURADA DA VERILIR. Yalnizca denemeyi yazmak yetmezdi: tavan
    dolduktan sonra `_kurtar`in `DEAD` yazimi da AYNI olu oturumda patlar ve
    olay tavana varmis olmasina ragmen HIC emekli olamazdi. Bu yuzden taze
    oturum ayni karari yeniden verir: tavan dolduysa olayi OLU yazar.

    BAKIM MODU DELINMEZ: taze oturum da `db.py` icindeki oturum SINIFIYLA
    acilir, yani yazim yine `pg_try_advisory_xact_lock_shared` kapisindan
    gecer. Olculdu: bakim modunda bu yol `MaintenanceActiveError` alir ve
    deneme YAKILMAZ.
    """
    try:
        baglayici = db.get_bind()
    except Exception:  # noqa: BLE001 - kova adi konmus, sayilir
        logger.exception(
            "Field stok taze oturum icin baglayici alinamadi; firma=%s olay=%s",
            firma, olay_id,
        )
        return SONUC_KURTARILAMADI
    try:
        with type(db)(bind=baglayici) as taze:
            # USTU YAZIMDAN ONCE KOY. Bu satir olmadan asagidaki UPDATE,
            # sunucunun varsayilani (`lock_timeout = 0`) altinda bir satir
            # kilidini SONSUZA KADAR bekler — tek thread'li zamanlayicinin
            # ICINDE ve hic dongu satiri uretmeden.
            _oturumu_sinirla(taze)
            if deneme >= azami_deneme:
                _olayi_sonlandir_bekleyeni(
                    taze, firma, olay_id, DURUM_OLU,
                    "deneme tavani asildi (%d), TAZE oturumda kapatildi: %s"
                    % (azami_deneme, mesaj))
                taze.commit()
                logger.error(
                    "Field stok kurtarma TAZE oturumda tamamlandi ve olay OLU "
                    "yazildi; firma=%s olay=%s deneme=%s", firma, olay_id, deneme,
                )
                return DURUM_OLU
            _denemeyi_kaydet(taze, firma, olay_id, mesaj)
            taze.commit()
            logger.error(
                "Field stok kurtarma yazimi TAZE oturumda basarildi; deneme "
                "KALICI oldu ve tavan YAKLASTI; firma=%s olay=%s deneme=%s",
                firma, olay_id, deneme,
            )
            return SONUC_KURTARMA_TAZE
    except Exception:  # noqa: BLE001 - kova adi konmus, sayilir, GURULTULU
        # TAZE OTURUM DA YAZAMADI. Olculdu: kilit catismasi ve BAKIM MODU
        # boyledir. Ikisi de KURESEL durumlardir; deneme YAKILMAZ ve olay
        # DEGISMEDEN `PENDING` kalir.
        logger.exception(
            "Field stok kurtarma TAZE oturumda da basarisiz; olay DEGISMEDI "
            "ve PENDING kaldi; firma=%s olay=%s deneme=%s",
            firma, olay_id, deneme,
        )
        return SONUC_KURTARILAMADI


def _kurtar(
    db: Session, firma: int, olay_id: int, deneme: int, azami_deneme: int,
    mesaj: str,
) -> str:
    """Basarisiz bir olaydan kurtulur ve KENDI basarisizligini da atlatir.

    Bu kol, bir VERITABANI islemi patladigi icin girilir; ayni oturum
    uzerinden yapilan kurtarma yazimi da ayni nedenle patlayabilir (kopan
    baglanti, `OperationalError`, `PendingRollbackError`). Onceden bu
    yazimlar korumasizdi: ikinci istisna `olaylari_isle`den disari kacar,
    korunum assert'ine hic varilmaz ve `tum_firmalari_isle` olur — yani
    SIRADAKI HER FIRMA islenmezdi. Tam olarak duzeltilmek istenen davranis,
    onu tetiklemesi EN OLASI istisna sinifi icin ayakta kaliyordu.

    HER YOL BIR KOVA ADI DONDURUR; bu fonksiyon istisna ATMAZ.
    """
    try:
        # Islemi geri al: yarim kalmis hareket satiri BIRAKILMAZ.
        db.rollback()
        # USTU YENIDEN KOY. `rollback()` islemi KAPATIR ve islem kapsamli ust
        # onunla birlikte DUSER; bu satir olmadan asagidaki kurtarma yazimi
        # (`_denemeyi_kaydet` ya da tavan yazimi) YENI ve USTSUZ bir islemde
        # kosar, yani `_bir_olayi_isle`de kapatilan sinirsizlik tam burada
        # geri acilirdi. Ust `_taze_oturumda_kurtar`daki ile AYNIDIR; iki
        # yolun da ayni siniri gormesi bilinclidir.
        _oturumu_sinirla(db)
        if deneme >= azami_deneme:
            # Tavan DOLDU. Olay terminal olur ve kuyrugu bir daha tikamaz.
            _olayi_sonlandir_bekleyeni(
                db, firma, olay_id, DURUM_OLU,
                "deneme tavani asildi (%d): %s" % (azami_deneme, mesaj))
            db.commit()
            return DURUM_OLU
        # Denemeyi KALICI yap; olay PENDING kalir, tavan yaklasir.
        _denemeyi_kaydet(db, firma, olay_id, mesaj)
        db.commit()
        return SONUC_YENIDEN
    except Exception:  # noqa: BLE001 - kova adi konmus, sayilir, GURULTULU
        # OLAYIN DURUMU: veritabaninda HICBIR SEY degismedi. Olay `PENDING`,
        # `attempts` ESKI degerinde, `last_error` eski. Bir sonraki dongude
        # yeniden alinir; bu kosumda tavan YAKLASMAZ. Bu, sessiz bir yalanin
        # (`RETRY_SCHEDULED` saymak) yerine ADI KONMUS bir bosluktur.
        logger.exception(
            "Field stok KURTARMA YAZIMI da basarisiz; olay DEGISMEDI ve "
            "PENDING kaldi; firma=%s olay=%s deneme=%s", firma, olay_id, deneme,
        )
        # Oturumu bir sonraki olay icin kullanilabilir birakmayi DENE. Bu da
        # patlarsa oturum olmustur: kalan olaylar da bu kovaya duser, dongu
        # yine de korunumlu biter ve tum-firmalar dongusu YASAR.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Field stok kurtarma sonrasi rollback de basarisiz; oturum "
                "kullanilamaz durumda; firma=%s olay=%s", firma, olay_id,
            )
        # ESKALASYON: bu oturum yazamiyorsa TAZE bir oturum yazabilir mi?
        # Yazabiliyorsa kusur OTURUMDADIR ve tavan ULASILABILIR kalir;
        # yazamiyorsa (kilit, bakim) deneme YAKILMAZ ve kova
        # `RECOVERY_FAILED` olur — yani bugunku davranis AYNEN korunur.
        return _taze_oturumda_kurtar(
            db, firma, olay_id, deneme, azami_deneme, mesaj)


def _bir_olayi_isle(
    db: Session, firma: int, olay, azami_deneme: int
) -> str:
    """Tek bir olayi isler ve TAM OLARAK BIR kova adi dondurur.

    SOZLESME: bu fonksiyon ISTISNA ATMAZ ve her zaman bir kova adi dondurur.
    Cagiran tarafta tek bir `sayac[...] += 1` vardir, yani korunum denklemi
    "her cikis tam olarak bir terim artirir" kuralini YAPISAL olarak saglar.

    Sayac artisi COMMIT'TEN SONRA, donus degeri araciligiyla olur: commit
    patlarsa bu cagri `_kurtar`in kovasini dondurur ve TEK terim sayilir.
    """
    # SATIR OKUMASI DA KORUMANIN ICINDE. Bugun ikisi de patlayamaz — goc
    # `attempts`i NOT NULL DEFAULT 0 ilan ediyor ve `id` birincil anahtar — ama
    # KORUMANIN DISINDA duran bir ifade, tam olarak tekrar tekrar onardigimiz
    # SEKILDIR: orada patlayan bir olay HICBIR kovayi artirmadan dongudan
    # kacar. Sentinel degerler `except` kolunun (logger ve `_kurtar`) tanimsiz
    # bir ada bakmasini engeller; `olay_id = -1` hicbir satiri eslemez, yani
    # kurtarma yazimi 0 satir gunceller ve olay PENDING kalir.
    olay_id = -1
    deneme = 0
    # BEKLENMEYEN ISTISNA BU OLAYI OLDURMEZ, KUYRUGU DA DURDURMAZ.
    # TALEP DE KORUMANIN ICINDEDIR: `_talep_et` yarisan bir KOSULLU
    # UPDATE'tir ve kilitlenme, kilit zaman asimi, kopan baglanti onun
    # SIRADAN basarisizliklaridir. Onceden talep (ve rollback'i) `try`in
    # DISINDA duruyordu: orada patlayan bir olay HICBIR kovayi artirmadan
    # tum-firmalar dongusunu olduruyordu.
    try:
        olay_id = int(olay["id"])
        deneme = int(olay["attempts"]) + 1
        # USTU TALEPTEN ONCE KOY. BU KAPIDIR, SUS DEGIL.
        #
        # OLCULEN KUSUR: kurtarma yolunun ustu (`_taze_oturumda_kurtar`)
        # sinirliydi ama ona GIDEN yol degildi. `_talep_et` yarisan bir
        # KOSULLU UPDATE'tir; baska bir oturum ayni satiri tutuyorsa bu
        # ifade, sunucunun varsayilani (`lock_timeout = 0`) altinda SONSUZA
        # KADAR bekler — tek thread'li zamanlayicinin ICINDE. Yani tuketici,
        # sinirli eskalasyona HIC VARAMADAN burada asili kalirdi: sinirli bir
        # eskalasyonun ONUNDE sinirsiz bir kapi, ATESLENEMEYEN bir mekanizma
        # demektir. Ust burada oldugu icin kilit catismasi artik SINIRLI
        # surede bir istisnaya donusur, `except` koluna duser ve olay ADI
        # KONMUS bir kovaya (`RECOVERY_FAILED`) yazilir.
        #
        # ISLEM KAPSAMLIDIR (`is_local => true`): bu cagri islemi ACAR ve ust
        # o islemle birlikte biter. Yani ust olay BASINA yeniden konur ve
        # commit/rollback sonrasi havuza DONEN baglantida KALMAZ.
        #
        # KAPSAM: ust yalnizca talebi degil, bu olayin TUM islemini (hareket
        # yazimi ve sonlandirma dahil) tek commit'e kadar orter. Bu bilincli:
        # kilidi talepte degil `_hareket_yaz`da bekleyen bir olay da ayni
        # sekilde sonsuza kadar beklerdi.
        _oturumu_sinirla(db)
        # ATOMİK TALEP. Kaybeden HİÇBİR ŞEY yazmaz ve hemen çıkar.
        if not _talep_et(db, firma, olay_id):
            db.rollback()
            return SONUC_KAYBEDILDI

        tip = str(olay["source_type"])
        eslesme = _KAYNAK.get(tip)
        if eslesme is None:
            _olayi_sonlandir(
                db, firma, olay_id, DURUM_OLU,
                "tanınmayan kaynak tipi: %s" % tip, deneme)
            db.commit()
            return DURUM_OLU
        if deneme > azami_deneme:
            _olayi_sonlandir(
                db, firma, olay_id, DURUM_OLU,
                "deneme tavanı aşıldı (%d)" % azami_deneme, deneme)
            db.commit()
            return DURUM_OLU

        tablo, yon, oku = eslesme
        # Kaynak satırı OLAYIN firmasıyla okunur (kiracı kapsamlı). Satır
        # bu firmada yoksa ya gerçekten YOK ya da BAŞKA firmanın.
        kaynak = oku(db, firma, int(olay["source_id"]))

        # YETİM: kaynak satırı YOK. Olmayan bir kayıt için envanter düşülemez.
        if kaynak is None:
            _olayi_sonlandir(
                db, firma, olay_id, DURUM_GORUNMEZ,
                "kaynak satırı bu KİRACIDA görünmüyor (yok ya da başka firmanın): "
                "%s#%s" % (tablo, olay["source_id"]), deneme)
            db.commit()
            return DURUM_GORUNMEZ

        # HASAT KİLİDİ — yalnız fiş olayları. Olay talebi OLAYI serileştirir;
        # aynı hasadın iki fişi ayrı olaylardır ve READ COMMITTED altında
        # ikisi de `_zaten_duzeltilmis`i 0 okur (mercek: 25/25 +2400, doğru
        # +1200). PG'de hasat satırı FOR UPDATE; SQLite tek yazar, no-op.
        # `_fis_kalemleri` / `_zaten_duzeltilmis` bu kilitten SONRA okunur.
        if tip == KAYNAK_FIS:
            _hasati_serilestir(db, firma, int(olay["source_id"]))

        kalem_oku = _KALEM_OKUYUCU.get(tip)
        # BİRİM REDDİ ADI KONMUŞ BİR KOVADIR, BEKLENMEYEN BİR İSTİSNA DEĞİL.
        # Aşağıdaki genel `except` bu ailenin ÜSTÜNDEN geçseydi red
        # `RECOVERY_*`/yeniden deneme yoluna düşerdi ve olay üç kez daha
        # denenip `DEAD` olurdu — oysa taban birimin bildirilmemesi bir
        # ARIZA değil bir VERİ OLGUSUDUR: aynı olay yarın da aynı sebeple
        # duracak, denemek onu düzeltmez. Düzeltecek olan ÜRÜN KARTIDIR ve
        # kova tam olarak bunu söyler.
        try:
            kalemler = (
                kalem_oku(db, firma, int(olay["source_id"])) if kalem_oku else []
            )
        except UrunTemsilEdilemez as red:
            # Birim ÇÖZÜLDÜ, sayı SIĞMADI. `DEAD` çünkü çare veri girişinde
            # (temsil edilebilir bir birimle yeniden girmek) ve tekrar
            # denemek aynı sayıyı aynı yere sığdırmayı dener.
            _olayi_sonlandir(db, firma, olay_id, DURUM_OLU, str(red), deneme)
            db.commit()
            return DURUM_OLU
        except BirimCozulemedi as red:
            # HER KOL KENDİ SABİTİNİ DÖNDÜRÜR, ORTAK BİR DEĞİŞKEN DEĞİL.
            # Korunum denklemi (`test_field_stok_korunum_denklemi.py`) dönüş
            # ADLARINI statik çözer; `return kova` gibi bir yerel ad kovayı
            # denklemin GÖRÜŞ ALANINDAN çıkarır ve o kova sessizce
            # sayılmayabilir hâle gelirdi.
            if red.sebep == BirimCozulemedi.TABAN_BILDIRILMEMIS:
                _olayi_sonlandir(
                    db, firma, olay_id, DURUM_TABANSIZ,
                    _TABANSIZ_GEREKCE % tablo, deneme)
                db.commit()
                return DURUM_TABANSIZ
            # Kalan sebepler (BIRIM_TANIMSIZ, BOYUT_UYUSMAZLIGI,
            # KATSAYI_GECERSIZ, MIKTAR_SONLU_DEGIL) de VERİ OLGUSUDUR ve
            # yeniden denemek onları düzeltmez.
            _olayi_sonlandir(db, firma, olay_id, DURUM_OLU, str(red), deneme)
            db.commit()
            return DURUM_OLU
        # ÜRÜNSÜZ KOVA — KALDIRILMADI, KAÇINILABİLİR YAPILDI.
        # Buraya üç yoldan düşülür ve ÜÇÜ DE ADI KONMUŞ bir karardır:
        #   * ürünü BİLDİRİLMEMİŞ sezonun hasadı (`crop_seasons.product_id`
        #     NULL — göç 20260827_0062 sütunu bilerek NULL kabul eden yaptı),
        #   * ürünsüz girdi taşıyan faaliyet,
        #   * `_KAYNAK`ta olup `_KALEM_OKUYUCU`da olmayan bir kaynak tipi.
        # Ürün uydurmak yerine olay burada SAYILARAK biter.
        if not kalemler:
            _olayi_sonlandir(
                db, firma, olay_id, DURUM_URUNSUZ,
                _URUNSUZ_GEREKCE.get(tip, _URUNSUZ_VARSAYILAN) % tablo, deneme)
            db.commit()
            return DURUM_URUNSUZ

        try:
            depo = default_warehouse(db, firma)
        except RuntimeError as hata:
            # ONEKSIZ GECEN metin ARTIK yalnizca KURATE kumenin uyesi olabilir;
            # gerisi isaretlenir ve okuma yuzeyinde arindirilir.
            _olayi_sonlandir(
                db, firma, olay_id, DURUM_OLU, _depo_gerekcesi(hata), deneme)
            db.commit()
            return DURUM_OLU

        fark_kaynagi = tip in _FARK_KAYNAKLARI
        for kalem in kalemler:
            miktar = normalize_quantity(Decimal(str(kalem["quantity"])) * yon)
            # SIFIR FARK = SATIR YOK. Defterde düzeltilecek bir şey olmadığı
            # hâlde sıfırlık bir hareket yazmak, defteri anlamsız satırlarla
            # şişirir ve "bu fiş bir şey değiştirdi" izlenimi verirdi. Olay
            # yine de TERMİNAL biter (`SENT`): iş yapıldı, sonucu sıfır.
            if fark_kaynagi and miktar == 0:
                continue
            _hareket_yaz(
                db, firma, int(kalem["product_id"]), depo, miktar, olay_id,
                "tarla olayı #%d (%s)" % (olay_id, tip),
            )
        _olayi_sonlandir(db, firma, olay_id, DURUM_UYGULANDI, None, deneme)
        # TALEP + HAREKET + SONLANDIRMA TEK COMMIT. Arada ölen bir süreç
        # hiçbirini bırakmaz.
        db.commit()
        return DURUM_UYGULANDI
    except Exception as hata:  # noqa: BLE001 - kova adi konmus, sayilir
        mesaj = "%s%s: %s" % (_HAM_ISTISNA_ONEKI, type(hata).__name__, hata)
        logger.exception(
            "Field stok olayi islenemedi; firma=%s olay=%s deneme=%s",
            firma, olay_id, deneme,
        )
        return _kurtar(db, firma, olay_id, deneme, azami_deneme, mesaj)


def olaylari_isle(
    db: Session, firma: int, *, azami_deneme: int = AZAMI_DENEME,
    sinir: int | None = None, son_an: int | None = None
) -> dict[str, int]:
    """PENDING stok olaylarını işler ve KORUNUMLU bir sayaç döndürür.

    Dönen sözlükte ``girdi`` işlenen olay sayısıdır ve terminal kovaların
    toplamına EŞİTTİR; bu eşitlik burada assert edilir.

    ``son_an`` bir `time.monotonic_ns()` VADESIDIR (TAM SAYI nanosaniye): vade dolduktan sonra HICBIR
    yeni olay alinmaz. Alinmayan olay `girdi`ye SAYILMAZ ve `PENDING` kaldigi
    icin bir sonraki dongude yeniden secilir — korunum denklemi vade kesse de
    kesmese de ayni kalir. Vade `tum_firmalari_isle`de TUM dongu icin BIR KEZ
    kurulur; buradaki parametre o vadenin firmalar arasinda paylasilmasi
    icindir.
    """
    sayac = _sayac()
    # SORGU METNİ SABİTTİR ve company_id BAĞLIDIR. Önceki hâli parçalardan
    # birleştiriyordu; ÖLÇÜLDÜ: statik kiracı kapısı böyle bir sorguyu HİÇ
    # GÖRMÜYOR (`_iter_text_sql` yalnız sabit metinleri topluyor). Yani kapı
    # yeşildi ama kapsamlı olduğu için değil, GÖRÜNMEZ olduğu için. Sınır
    # artık SQL'e değil, Python tarafına uygulanıyor.
    olaylar = db.execute(
        text(
            """SELECT id, company_id, source_type, source_id, attempts
            FROM field_integration_events
            WHERE company_id = :company_id AND status = :bekliyor
              AND target = :hedef
            ORDER BY id"""
        ),
        {
            "company_id": int(firma), "bekliyor": DURUM_BEKLIYOR,
            "hedef": HEDEF_STOK,
        },
    ).mappings().all()
    if sinir is not None:
        olaylar = olaylar[: int(sinir)]

    # TEK ARTIS NOKTASI. `_bir_olayi_isle` her olay icin TAM OLARAK BIR kova
    # adi dondurur ve ASLA istisna atmaz, yani "her cikis tam olarak bir terim
    # artirir" kurali burada YAPISALDIR — alti ayri cagri yerinde
    # tekrarlanan bir GELENEK degil.
    #
    # ONCEDEN her terminal kol once `sayac[X] += 1` yapip SONRA commit
    # ediyordu. Commit patlarsa (serilestirme hatasi, kilitlenme, kopan
    # baglanti, ya da bu dalin kendi gocunun onlemek icin var oldugu
    # `StringDataRightTruncation`) dis `except` IKINCI bir terim ekliyordu:
    # tek olay, girdi=1, cikti=2, korunum assert'i PATLIYOR, zamanlayicinin
    # genel `except`ine kaciyor ve TUM DONGU oluyordu.
    #
    # `girdi` ALINAN olaylari sayar, SECILENLERI degil: vade (`son_an`)
    # kestiginde alinmayan olay iki yana da katki vermez ve denklem AYNEN
    # tutar. Sayim dongunun disinda tek atama ile yazilir — sayac artis
    # noktasi TEK kalir (bkz. `test_SAYAC_ARTISI_TEK_NOKTADA`).
    islenen = 0
    for olay in olaylar:
        if son_an is not None and time.monotonic_ns() >= son_an:
            logger.warning(
                "Field stok dongu sure butcesi doldu; firma=%s bu dongude "
                "%d/%d olay alindi, kalanlar PENDING ve SONRAKI dongude",
                firma, islenen, len(olaylar),
            )
            break
        sayac[_bir_olayi_isle(db, firma, olay, azami_deneme)] += 1
        islenen += 1
    sayac["girdi"] = islenen

    # KORUNUM: her girdi bir kovaya çıktı. Bu bir ASSERT, tesadüf değil.
    cikti = (
        sum(sayac[d] for d in TERMINAL_DURUMLAR)
        + sayac[SONUC_KAYBEDILDI]
        + sayac[SONUC_YENIDEN]
        + sayac[SONUC_KURTARILAMADI]
        + sayac[SONUC_KURTARMA_TAZE]
    )
    assert cikti == sayac["girdi"], (
        "KORUNUM İHLALİ: %d olay alındı, %d sonuca yazıldı (terminal kovalar "
        "+ kaybedilen talep): %r" % (sayac["girdi"], cikti, sayac)
    )
    return sayac


#: `tum_firmalari_isle`nin ISTEGE BAGLI olcum kabina yazdigi anahtarlar.
#: Zamanlayicinin canlilik kaydi (acilis kosulu 4) bunlari okur.
OLCUM_FIRMA_SAYISI = "firma_sayisi"
OLCUM_FIRMA_ISLENEN = "firma_islenen"


def tum_firmalari_isle(
    db: Session, *, azami_deneme: int = AZAMI_DENEME,
    sure_butcesi_saniye: int | None = DONGU_SURE_BUTCESI_SANIYE,
    sinir: int | None = AZAMI_PARTI,
    olcum: dict[str, int] | None = None,
) -> dict[str, int]:
    """Her firmayı KENDİ kapsamında işler ve sayaçları toplar.

    Firma listesi `companies` tablosundan gelir; `companies` bir KİRACI
    tablosu değildir (ölçüldü), kiracı tablolarına dokunan her sorgu ise
    company_id BAĞLI olarak koşar.

    DONGU SINIRLI. Iki sinir VARSAYILAN olarak acik: `sure_butcesi_saniye`
    tum dongunun duvar saatini keser (olculdu: sinirsiz hali 20 catismali
    olayda 180.58 sn surmustu), `sinir` ise firma basina parti buyuklugunu.
    Kesilen olay `PENDING` kalir ve BIR SONRAKI dongude yeniden secilir;
    `None` gecirmek ilgili siniri kapatir.

    FIRMA SAYISI DONEN SOZLUGE GIRMEZ, `olcum` KABINA YAZILIR. Zamanlayicinin
    canlilik kaydi (acilis kosulu 4) "bu dongu kac firmayi GERCEKTEN gezdi"
    bilgisini istiyor, ama bu sayiyi donen sayaca EKLEMEK KIRMIZI OLURDU ve bu
    OLCULDU: bu sozlugun anahtarlarini SAYAN kapilar var
    (`tests/test_field_stok_zamanlayici.py` sozlugu BIREBIR karsilastirir,
    `tests/test_field_stok_dongu_siniri.py` ve PG ikizi ise `girdi` ve
    `COMPANY_FAILED` DISINDAKI butun degerleri TOPLAYIP `girdi`ye esitler) —
    yani yeni bir tam sayi anahtar korunum denklemini SESSIZCE bozardi.
    `olcum` VARSAYILAN OLARAK `None`dir: gecmeyen her cagiran icin bu
    fonksiyon BIREBIR eskisi gibi davranir. Gecildiginde iki anahtar yazilir:
    `firma_sayisi` (aktif firma sayisi) ve `firma_islenen` (dongunun
    GERCEKTEN girdigi firma sayisi — sure butcesi kestiginde ya da bir firma
    dustugunde ikisi AYRISIR).
    """
    toplam = _sayac()
    # OLAY KOVASI DEGIL, FIRMA SAYACI. `_sayac()` disinda tutulur; bkz.
    # `SONUC_FIRMA_DUSTU`.
    toplam[SONUC_FIRMA_DUSTU] = 0
    # VADE DONGU ICIN BIR KEZ kurulur ve firmalar arasinda PAYLASILIR: sinir
    # firma basina degil DONGU basinadir, cunku zamanlayicinin kadans sozu
    # dongunun tamami icindir.
    son_an = (
        time.monotonic_ns() + sure_butcesi_saniye * NANOSANIYE
        if sure_butcesi_saniye is not None else None
    )
    # KAPATILMIS FIRMA ISLENMEZ. Bu yuklem 5.1b'de OLCULEREK eklendi: yumusak
    # imha `companies.is_active`i false yapar ve `/api`nin tamamini kapatir,
    # AMA bu dongu HTTP'den gecmez — uygulama surecinde kosar ve kiraci
    # cozumunu HIC gormez. Yuklem olmadan kapatilmis bir kiracinin bekleyen
    # outbox olaylari islenmeye DEVAM eder ve `stock_movements`e YENI satir
    # yazardi; yani "kapatildi" iddiasi tam da denetlenmeyen yerde yalan olurdu.
    # BAGLI PARAMETRE, `= TRUE` metni DEGIL: PG'de boolean, SQLite'ta 1 baglanir
    # ve iki lehcede de ayni satirlar doner.
    firmalar = db.execute(
        text("SELECT id FROM companies WHERE is_active = :aktif ORDER BY id"),
        {"aktif": True},
    ).scalars().all()
    if olcum is not None:
        olcum[OLCUM_FIRMA_SAYISI] = len(firmalar)
        olcum[OLCUM_FIRMA_ISLENEN] = 0
    for sira, firma in enumerate(firmalar):
        if son_an is not None and time.monotonic_ns() >= son_an:
            logger.warning(
                "Field stok dongu sure butcesi doldu; %d firma bu dongude "
                "HIC islenmedi, olaylari PENDING ve SONRAKI dongude",
                len(firmalar) - sira,
            )
            break
        # BIR FIRMANIN DUSMESI SIRADAKI FIRMALARI ALIP GOTURMEZ.
        #
        # OLCULEN ACIK: `_kurtar` kendi yazimini atlatir ama ikinci
        # `db.rollback()` de patlarsa oturum KULLANILAMAZ kalir. O noktada bu
        # dongudeki bir sonraki `olaylari_isle` cagrisi daha ilk SELECT'te
        # patlar; istisna HER IKI korunum assert'ini de atlayarak disari kacar
        # ve id'si BUYUK olan her firma o dongude HIC islenmez. Uretimin
        # veritabani uygulama kabinin DISINDA oldugu icin islem ortasinda kopan
        # bir baglanti burada uzak bir ihtimal degildir.
        try:
            parca = olaylari_isle(
                db, int(firma), azami_deneme=azami_deneme, sinir=sinir,
                son_an=son_an,
            )
        except AssertionError:
            # KORUNUM IHLALI SESSIZLESTIRILMEZ. Bu bir calisma zamani arizasi
            # degil KOD hatasidir: yakalamak, bu dosyanin butun amaci olan
            # invaryanti gurultulu bir cokusten sessiz bir sayiya cevirirdi.
            # Bilerek YUKARI birakiliyor.
            raise
        except Exception:  # noqa: BLE001 - adi konmus, sayilir, GURULTULU
            logger.exception(
                "Field stok firma dongusu DUSTU; bu firma bu kosumda "
                "islenmedi, DIGER FIRMALAR islenmeye devam ediyor; firma=%s",
                firma,
            )
            toplam[SONUC_FIRMA_DUSTU] += 1
            # Oturumu siradaki firma icin kullanilabilir birakmayi DENE:
            # patlayan islem geri alinmadan bir sonraki SELECT de patlardi.
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Field stok firma dongusu sonrasi rollback de basarisiz; "
                    "oturum kullanilamaz durumda; firma=%s", firma,
                )
            continue
        if olcum is not None:
            olcum[OLCUM_FIRMA_ISLENEN] += 1
        for anahtar, deger in parca.items():
            toplam[anahtar] = toplam.get(anahtar, 0) + deger
    # KORUNUM, FİRMA BAŞINAKİ İLE AYNI DENKLEM OLMAK ZORUNDA. `CLAIM_LOST`
    # olayın TERMİNAL DURUMU değildir — olayı KAZANAN sonlandırır — ama bu
    # KOŞUMUN adı konmuş bir ÇIKTISIDIR. Yalnız terminal kovaları saymak,
    # gerçek bir talep yarışında `girdi`yi eksik kapatır: assert patlar,
    # zamanlayıcının genel `except`ine kaçar ve `CLAIM_LOST=1` taşıyan
    # NORMAL bir döngü satırı HİÇ yazılamaz. Denklem burada da eksiksiz.
    cikti = (
        sum(toplam[d] for d in TERMINAL_DURUMLAR)
        + toplam[SONUC_KAYBEDILDI]
        + toplam[SONUC_YENIDEN]
        + toplam[SONUC_KURTARILAMADI]
        + toplam[SONUC_KURTARMA_TAZE]
    )
    assert cikti == toplam["girdi"], (
        "KORUNUM İHLALİ (tüm firmalar): %d olay alındı, %d sonuca yazıldı "
        "(terminal kovalar + kaybedilen talep): %r"
        % (toplam["girdi"], cikti, toplam)
    )
    return toplam
