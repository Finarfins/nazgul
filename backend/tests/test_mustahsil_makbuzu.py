"""Müstahsil makbuzu (D1) — SÖZLEŞME, ARİTMETİK ve KİRACI SINIRI.

Konu: göç 20260905_0070, `app/mustahsil.py`, `app/routers/mustahsil.py`.
PostgreSQL ikizi: `test_mustahsil_makbuzu_postgresql.py` (bileşik yabancı
anahtarın kiracı sınırını VERİTABANI seviyesinde ölçer; SQLite'ta yabancı
anahtar zorlaması varsayılan olarak KAPALIDIR, bu yüzden o iddia BURADA
değil orada durur).

--- SESSİZCE YANLIŞ OLABİLECEK SEKİZ ŞEY -----------------------------------

1. **BAŞLIKTA YENİDEN YUVARLAMA.** Başlığın toplamları YUVARLANMIŞ satır
   değerlerinin toplamıdır. Biri toplamı ham çarpımlardan alıp sonra
   yuvarlarsa `net_payable != Σ line_net` olur ve İKİ SAYI DA "doğru
   yuvarlanmış" göründüğü için kimse fark etmez. Senaryo: üç satırın her
   biri 0.005 sapan bir makbuz.
2. **KDV UYGULANMASI.** `purchases` KDV'yi FİYATA DAHİL hesaplar. O formül
   buraya sızarsa olmayan bir vergi tutardan düşülür ve `net_payable`
   sessizce EKSİK çıkar. Müstahsilde KDV YOKTUR.
3. **ORANIN KODA GÖMÜLMESİ.** Oran SATIRDAN gelir. Kodda yasal bir sabit
   olsaydı tebliğ değiştiği gün hangi satırın hangi oranla yazıldığı
   okunamazdı. Kapı: kaynakta oran SABİTİ ARANIR.
4. **BİLEŞİK FK'NIN DÜŞMESİ.** `producer_receipts` üç bileşik yabancı
   anahtar taşır (supplier/purchase/ticket) ve hepsi `(company_id, id)`
   hedefler. Düşerlerse başka firmanın tedarikçisine makbuz kesilebilir.
5. **`issue`IN İKİ NUMARA ÜRETMESİ.** İkinci `issue` 409 verir ve seriden
   numara HARCAMAZ. Sessizce geçseydi bir makbuz iki numara taşırdı.
6. **TABAN BİRİM VARSAYILMASI.** `base_unit` bildirilmemişken yazma 422
   alır; girileni taban SAYMAK bir olgu uydurmaktır (`units.py`, sahip
   kararı 2).
7. **DEFTERE YAZMA.** Bu dilim stok ve ödeme defterine DOKUNMAZ. Kapı
   kaynak metni üzerinde; davranış testinden ÖNCE ve SEBEBİYLE kırılır.
8. **FİŞ ÖNERİSİNİN EZİLİNCE KAYBOLMASI.** Kullanıcı fişin netini ezerse
   İKİSİ DE saklanır (`ticket_net_snapshot`). Öneriyi silmek, kararın
   fişten NEREDE ayrıldığını görünmez yapardı.

--- BU DOSYANIN ÖLÇMEDİĞİ ŞEYLER (ADIYLA) ----------------------------------

* **e-Müstahsil (e-MM) sağlayıcı entegrasyonu.** D1 kağıdı KAYDEDER;
  GİB'e ya da bir entegratöre HİÇBİR ŞEY göndermez. Belgenin elektronik
  hâli, imzası ve gönderim durumu bu PR'ın DIŞINDADIR.
* **YASAL ORANLAR.** Hangi ürün için stopajın kaç olduğu ÖLÇÜLMEDİ ve
  kodda YOKTUR (bkz. madde 3). Bu bir mevzuat sorusudur, bir şema sorusu
  değil.
* **NET ÖDENECEĞİN ÖDENMESİ.** Çiftçiye ödeme ve avans mahsubu D2'dir.
  D1 yalnız BORCU doğurur.
* **STOPAJ YÜKÜMLÜLÜĞÜNÜN DEFTERE YAZILMASI.** Tasarım bunu öngörüyordu;
  yazılamadı. `finance_transactions.account_id` NOT NULL ve `ACCOUNT_TYPES`
  = {cash, bank, pos} — vergi dairesine olan borcu temsil eden hesap türü
  YOK. Ölçüm ve seçenekler PR gövdesinde;
  `test_stopaj_defteri_YAZILMADI_ve_sebebi_olculdu` bu YOKLUĞU çiviliyor ki
  bir gün eklendiğinde kırmızı olsun ve karar GÖRÜLEREK verilsin.

--- MUTASYONLAR (ÖLÇÜLDÜ; sonuçlar adıyla, aşağıdaki tabloda) ---------------

Her mutasyon tek tek uygulandı, adı geçen test KIRMIZI oldu, geri alındı.
Tablo `MUTASYONLAR` sabitindedir ve `test_mutasyon_tablosu_dolu` onu
boş bırakmaya karşı korur.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

ROUTER = BACKEND / "app" / "routers" / "mustahsil.py"
CEKIRDEK = BACKEND / "app" / "mustahsil.py"

#: (mutasyon, KIRMIZI olan test) — HER BİRİ TEK TEK UYGULANDI, ADI GEÇEN
#: TEST KIRMIZI OLDU, GERİ ALINDI. Ölçüm ortamı: CPython 3.12.3 (SQLite) ve
#: PostgreSQL 16.13 (ikiz).
MUTASYONLAR = (
    (
        "yuvarlama SATIRDAN BAŞLIĞA taşınınca (`satir_hesapla` kesintileri "
        "yuvarlamaz, `makbuz_topla` toplamı `money()` ile yuvarlar)",
        "test_baslik_toplami_satirlarla_BIREBIR (0.03 yerine 0.02) + "
        "test_aritmetik_tablosu iki satır",
    ),
    (
        "`satir_hesapla` brütü KDV-dahil sayıp ayrıştırınca (brüt/1.01) — "
        "`transactions.py`in alım formülünün buraya kopyalanması",
        "test_aritmetik_tablosu (dört satır) + test_mustahsil_makbuzu_sqlite "
        "(12500.00 yerine 12376.24)",
    ),
    (
        "stopaj oranı parametre yerine modül sabitinden alınınca "
        "(`STOPAJ_ORANI = Decimal('2')`)",
        "test_aritmetik_tablosu (dört satır) + "
        "test_kaynakta_yasal_oran_sabiti_YOK",
    ),
    (
        "göçte `fk_producer_receipts_supplier_same_company` bileşikten TEKİL "
        "yabancı anahtara indirilince (`supplier_id` -> `suppliers.id`)",
        "PG ikizi: test_baska_firmanin_tedarikcisi_VERITABANINDA_reddedilir "
        "(YALNIZ o; öteki 15 yeşil kaldı — kapı DAR)",
    ),
    (
        "`issue`daki `status='draft'` CAS kapısı kaldırılınca "
        "(ya da `next_document_no` claim ÖNCESİNE taşınınca)",
        "PG ikizi: test_eszamanli_issue_ayni_taslak_tek_kazanan "
        "(iki 200 + sira delta>1) ve/veya test_mustahsil_makbuzu_sqlite "
        "(senaryo 5: ikinci `issue` 200 + İKİNCİ NUMARA)",
    ),
    (
        "`create_producer_receipt` `status='issued'` yazınca",
        "test_mustahsil_makbuzu_sqlite (senaryo 1: 'TASLAKTA NUMARA VAR' — "
        "göçün `ck_..._no_follows_status`u zaten INSERT'ü reddediyor)",
    ),
    (
        "router'a `stock_movements`a değen TEK bir sorgu eklenince",
        "test_kaynak_defterin_ADINI_bile_gecirmiyor (davranış testinden ÖNCE "
        "ve SEBEBİYLE)",
    ),
    (
        "`ticket_net_snapshot` ezme durumunda None yazılınca",
        "test_mustahsil_makbuzu_sqlite (senaryo 9: 'EZILINCE FISIN ONERISI "
        "KAYBOLDU')",
    ),
    (
        "router `finance_transactions`a değince (stopaj yükümlülüğü deftere "
        "yazılınca)",
        "test_stopaj_defteri_YAZILMADI_ve_sebebi_olculdu",
    ),
)

#: ÖLÇÜLDÜ VE YANLIŞ ÇIKTI, ADIYLA: `makbuz_topla`nın toplamlarını `money()`
#: ile SARMAK hiçbir testi kırmaz — ve kırmaması DOĞRUDUR. Girdiler zaten
#: 0.01'in katıdır, ikinci bir `quantize` kimliktir. Tehlike ikinci
#: yuvarlamada DEĞİL, yuvarlamanın SATIRDAN KALKMASINDADIR; yukarıdaki 1.
#: mutasyon bu yüzden İKİ parçalıdır. Bu satır burada duruyor ki
#: `makbuz_topla`daki "money() ÇAĞRILMAZ" yorumu bir KORUMA sanılmasın —
#: o yorum bir OKUNABİLİRLİK kararıdır, gerekçesi kendi başlığında.
OLCULDU_AMA_KIRMIZI_OLMAYAN = (
    "`makbuz_topla` toplamları `money()` ile sarınca -> 40/40 YEŞİL "
    "(no-op; tehlike satır yuvarlamasının kalkmasıdır, 1. mutasyona bakın)",
)


def test_mutasyon_tablosu_dolu() -> None:
    """Tablo boşalırsa başlıktaki "ölçüldü" iddiası dayanaksız kalır."""
    assert len(MUTASYONLAR) >= 9
    assert OLCULDU_AMA_KIRMIZI_OLMAYAN
    for mutasyon, kirmizi in MUTASYONLAR:
        assert mutasyon and kirmizi


# ---------------------------------------------------------------------------
# ARİTMETİK — saf, veritabanı YOK.
# ---------------------------------------------------------------------------

from app.mustahsil import (  # noqa: E402
    MustahsilHatasi,
    makbuz_topla,
    satir_hesapla,
)

#: (taban_miktar, birim_fiyat, stopaj%, sgk%, brüt, stopaj, sgk, net)
ARITMETIK = (
    # Sıradan satır.
    ("10", "2.50", "2", "1", "25.00", "0.50", "0.25", "24.25"),
    # HER İKİ ORAN DA SIFIR: kesinti yok, net brüte EŞİT. Sıfırın
    # "varsayılan" değil KARAR olduğu durum (bkz. şema, kural 2).
    ("100", "3", "0", "0", "300.00", "0.00", "0.00", "300.00"),
    # STOPAJ %100: net SIFIR. Şemanın izin verdiği uç; aritmetik onu
    # reddetmez çünkü aralık kapısı 100'ü İÇERİR.
    ("10", "5", "100", "0", "50.00", "50.00", "0.00", "0.00"),
    # İKİ ORAN TOPLAMI 100: net yine SIFIR, ama iki kesintiden.
    ("10", "5", "60", "40", "50.00", "30.00", "20.00", "0.00"),
    # YUVARLAMA UCU 0.005 -> ROUND_HALF_UP YUKARI: 0.5 * 1% = 0.005 -> 0.01.
    # ROUND_HALF_EVEN olsaydı 0.00 çıkardı ve fark gözle görülmezdi.
    ("1", "0.50", "1", "0", "0.50", "0.01", "0.00", "0.49"),
    # BRÜTÜN KENDİSİ 0.005'te: 0.1 * 0.05 = 0.005 -> 0.01.
    ("0.1", "0.05", "0", "0", "0.01", "0.00", "0.00", "0.01"),
    # ONDALIKLI MİKTAR ve ONDALIKLI ORAN birlikte.
    ("12.3456", "7.89", "4.25", "2.5", "97.41", "4.14", "2.44", "90.83"),
    # SIFIR FİYAT: brüt sıfır, kesintiler sıfır. Reddedilmez — bedelsiz
    # teslim bir olgudur ve makbuzu kesilir.
    ("50", "0", "10", "5", "0.00", "0.00", "0.00", "0.00"),
)


@pytest.mark.parametrize(
    "miktar,fiyat,stopaj_o,sgk_o,brut,stopaj,sgk,net", ARITMETIK
)
def test_aritmetik_tablosu(
    miktar, fiyat, stopaj_o, sgk_o, brut, stopaj, sgk, net
) -> None:
    """Sekiz satır, SAYILARIYLA çivili.

    Tablo hem formülü hem YUVARLAMA YÖNÜNÜ ölçüyor: 0.005 satırları
    ROUND_HALF_UP'ı ROUND_HALF_EVEN'dan ayırır (ikincisi 0.00 verirdi).
    """
    sonuc = satir_hesapla(
        Decimal(miktar), Decimal(fiyat), Decimal(stopaj_o), Decimal(sgk_o)
    )
    assert sonuc.line_gross == Decimal(brut), sonuc
    assert sonuc.withholding_amount == Decimal(stopaj), sonuc
    assert sonuc.social_security_amount == Decimal(sgk), sonuc
    assert sonuc.line_net == Decimal(net), sonuc
    # MAKBUZUN İÇ TUTARLILIĞI: okuyucu bunu gözle denetler.
    assert (
        sonuc.line_net + sonuc.withholding_amount + sonuc.social_security_amount
        == sonuc.line_gross
    )


def test_baslik_toplami_satirlarla_BIREBIR() -> None:
    """Başlık = Σ satır. YENİDEN YUVARLAMA yok.

    Üç satırın her biri kesintide 0.005'te duruyor ve her biri 0.01'e
    yukarı yuvarlanıyor: toplam 0.03. Başlık ham çarpımların toplamından
    (0.015 -> 0.02) hesaplansaydı bu test 0.02 görür ve düşerdi — yani
    `net_payable != Σ line_net` olurdu.
    """
    satirlar = [
        satir_hesapla(Decimal("1"), Decimal("0.50"), Decimal("1"), Decimal("0"))
        for _ in range(3)
    ]
    toplam = makbuz_topla(satirlar)
    assert toplam.withholding_total == Decimal("0.03"), toplam
    assert toplam.gross_amount == Decimal("1.50"), toplam
    assert toplam.net_payable == Decimal("1.47"), toplam
    # Toplamın SATIRLARDAN geldiğini doğrudan söyle.
    assert toplam.net_payable == sum(s.line_net for s in satirlar)
    assert toplam.withholding_total == sum(s.withholding_amount for s in satirlar)


def test_bos_makbuz_dort_sifir() -> None:
    """Kalemsiz taslak geçerlidir; kalem zorunluluğu `issue` kapısındadır."""
    toplam = makbuz_topla([])
    assert toplam.gross_amount == Decimal("0")
    assert toplam.net_payable == Decimal("0")


@pytest.mark.parametrize("bozuk", ["NaN", "Infinity", "-Infinity", "sNaN"])
@pytest.mark.parametrize("alan", ["miktar", "fiyat", "stopaj", "sgk"])
def test_sonlu_olmayan_sayi_AILE_ICINDE_reddedilir(bozuk: str, alan: str) -> None:
    """NaN/sonsuzluk her alanda ve ARALIK KAPISINDAN ÖNCE reddedilir.

    Niye aralıktan önce: `NaN >= 0` FALSE'tur, yani aralık kapısı NaN'ı
    "negatif" sanıp doğru sonucu YANLIŞ sebeple verirdi; `Infinity` ise
    aralıktan GEÇER ve `quantize` üzerinde `InvalidOperation` atardı — o da
    bu modülün sözleşmesinin DIŞINDA kalırdı.
    """
    args = {
        "miktar": Decimal("1"),
        "fiyat": Decimal("1"),
        "stopaj": Decimal("0"),
        "sgk": Decimal("0"),
    }
    args[alan] = Decimal(bozuk)
    with pytest.raises(MustahsilHatasi) as hata:
        satir_hesapla(args["miktar"], args["fiyat"], args["stopaj"], args["sgk"])
    assert hata.value.sebep == MustahsilHatasi.SAYI_SONLU_DEGIL, hata.value


@pytest.mark.parametrize("oran", ["-0.01", "100.01", "1000"])
def test_oran_araligi(oran: str) -> None:
    with pytest.raises(MustahsilHatasi) as hata:
        satir_hesapla(Decimal("1"), Decimal("1"), Decimal(oran), Decimal("0"))
    assert hata.value.sebep == MustahsilHatasi.ORAN_ARALIK_DISI


def test_float_KABUL_EDILMEZ() -> None:
    """İkili kayan nokta bir para tutarına giremez."""
    with pytest.raises(MustahsilHatasi) as hata:
        satir_hesapla(1.5, Decimal("1"), Decimal("0"), Decimal("0"))  # type: ignore[arg-type]
    assert hata.value.sebep == MustahsilHatasi.SAYI_SONLU_DEGIL


# ---------------------------------------------------------------------------
# STATİK KAPILAR — veritabanı YOK.
# ---------------------------------------------------------------------------

DEFTER_ADLARI = (
    "stock_movements",
    "warehouse_stocks",
    "field_integration_events",
    "payments",
)


def _kod_metni(dosya: Path) -> str:
    """Dosyanın KOD'u — belge dizgileri ÇIKARILMIŞ hâlde.

    `ast.unparse` belge dizgilerini KORUR, yani bu dosyanın kapıları
    modülün KENDİ GEREKÇESİYLE kırmızı olurdu: `mustahsil.py` başlığı
    "`stock_movements` yazmıyoruz" demek için o adı ANMAK zorundadır.
    Kapı, adın YAZILAN KODDA geçip geçmediğini sormalı — anlatıldığı yerde
    değil. (ÖLÇÜLDÜ: bu ayıklama olmadan üç kapı da kendi başlığına takıldı.)
    """
    agac = ast.parse(dosya.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if not isinstance(
            dugum, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        govde = dugum.body
        if (
            govde
            and isinstance(govde[0], ast.Expr)
            and isinstance(govde[0].value, ast.Constant)
            and isinstance(govde[0].value.value, str)
        ):
            # Gövdeyi BOŞ bırakmamak için `pass` ile değiştir.
            govde[0] = ast.Pass()
    return ast.unparse(ast.fix_missing_locations(agac))


def test_kaynak_defterin_ADINI_bile_gecirmiyor() -> None:
    """Router stok ve ödeme defterine DEĞİNMEZ (madde 7).

    Bu kapı davranış testinden ÖNCE ve SEBEBİYLE kırılır: biri `issue`a bir
    stok hareketi ya da ödeme satırı eklediğinde burası kırmızı olur.

    NE İDDİA ETMEZ: dolaylı bir yoldan (başka modül üzerinden) yazılmadığını.
    Ölçtüğü şey, eklemenin pratikte aldığı biçim olan doğrudan referanstır.

    --- D2 GELDİ VE BU KAPI HÂLÂ YEŞİL: SEBEBİ ADIYLA YAZILIYOR ---------

    Aşağıdaki hata metni "ödeme yazmak D2'dir" diyordu. D2 (göç
    `20260906_0071`) İNDİ ve ödeme defterine GERÇEKTEN YAZIYOR — ama
    `routers/avans.py`den, buradan DEĞİL. Kapı bu yüzden yeşil kaldı ve
    yeşilliği artık ŞUNU ölçüyor, daha azını değil:

      * `issue` hâlâ ödeme satırı YAZMIYOR. Doğurduğu şey vergi
        yükümlülüğü (`tax_liabilities`) ve avans mahsubudur; para
        hareketi DEĞİL. Bu, D1'in "makbuz kesmek BORÇ doğurur, borcun
        kapanması AYRI bir olaydır" duruşunun hâlâ geçerli olduğu
        anlamına gelir.
      * `cancel`ın ödeme denetimi `avans_engine.iptal_engelleri`
        üzerinden OKUR; bu kapı böyle bir dolaylı okumayı ZATEN iddia
        dışı bırakıyor (yukarıdaki "NE İDDİA ETMEZ").

    YANLIŞ OKUNMASIN: bu kapının yeşil olması "müstahsil makbuzu ödeme
    defterine dokunmuyor" DEMEK DEĞİLDİR. `/producer-receipts/{id}/pay`
    ve `/suppliers/{id}/advances` `payments`a YAZAR. Kapının koruduğu
    şey, D1 router'ının kendi yaşam döngüsü dışına TAŞMAMASIDIR.
    """
    # Başlıktaki gerekçe bölümü bu adları ANLATIYOR; kapı KOD satırlarına
    # bakmalı, yoksa kendi belgesiyle kırmızı olurdu.
    kod = _kod_metni(ROUTER)
    gecenler = [ad for ad in DEFTER_ADLARI if ad in kod]
    assert gecenler == [], (
        f"Müstahsil router'ı deftere değiniyor: {gecenler}. D1'in tek iddiası "
        "defterin DEĞİŞMEMESİYDİ. Ödeme yazmak D2'dir ve D2 İNDİ — ama "
        "yazma yeri `routers/avans.py`dir, BURASI değil (bkz. başlık). "
        "Stok yazmak ise hâlâ ayrı ve verilmemiş bir karardır."
    )


def test_stopaj_defteri_YAZILMADI_ve_sebebi_olculdu() -> None:
    """`finance_transactions` YAZILMIYOR — ve bu ÖLÇÜLMÜŞ bir engeldir.

    Tasarım stopaj yükümlülüğünü yeni bir `withholding` kategorisiyle
    deftere yazmayı öngörüyordu. Yazılamadı:

      * `finance_transactions.account_id` NOT NULL,
      * `ACCOUNT_TYPES` = {cash, bank, pos} — hiçbiri vergi dairesine olan
        BORCU temsil etmez,
      * satır gerçek bir hesaba İÇ BİRLEŞTİRİLİR ve o hesabın bakiyesinde
        GÖRÜNÜR.

    Yani yükümlülüğü bir kasa/banka hesabına yazmak, gerçekleşmemiş bir para
    hareketiyle bakiyeyi kirletirdi.

    Bu test YOKLUĞU çiviliyor. Bir gün yazılırsa KIRMIZI olur ve karar
    görülerek verilir — sessizce sızmaz.
    """
    kod = _kod_metni(ROUTER)
    assert "finance_transactions" not in kod, (
        "Router deftere finans satırı yazıyor. Bu bir sahip kararıdır: "
        "`ACCOUNT_TYPES`e yükümlülük türü eklemek finans modülünün HER "
        "doğrulama kapısını etkiler."
    )
    # Engelin hâlâ GERÇEK olduğunu ölç: kapı, sebep ortadan kalktığında da
    # (ör. account_id nullable olunca) düşünülmesi için burada.
    from app.finance_engine import ACCOUNT_TYPES, finance_transactions

    assert finance_transactions.c.account_id.nullable is False, (
        "`account_id` artık NULL kabul ediyor: yükümlülüğü hesapsız yazmanın "
        "önündeki ÖLÇÜLMÜŞ engel kalkmış olabilir. Karar yeniden verilmeli."
    )
    assert ACCOUNT_TYPES == {"cash", "bank", "pos"}, ACCOUNT_TYPES


def test_kaynakta_yasal_oran_sabiti_YOK() -> None:
    """Ne çekirdekte ne router'da yasal bir stopaj/SGK oranı SABİTİ vardır.

    Oran SATIRDAN gelir (göç 0070 başlığı). Kodda bir sabit olsaydı, tebliğ
    değiştiği gün hangi satırın hangi oranla yazıldığı OKUNAMAZDI.

    Kapı, Türkiye'de fiilen kullanılan müstahsil stopaj oranlarını (%1, %2,
    %4) ve Bağ-Kur kesintisini (%1, %2) *modül seviyesinde bir sabit olarak*
    arar; aritmetiğin kendi 100'ü (`_ORAN_TAVANI`) bir ORAN değil bir
    ARALIK SINIRIDIR ve kapının dışındadır.
    """
    for dosya in (CEKIRDEK, ROUTER):
        agac = ast.parse(dosya.read_text(encoding="utf-8"))
        for dugum in agac.body:
            if not isinstance(dugum, ast.Assign):
                continue
            for hedef in dugum.targets:
                if not isinstance(hedef, ast.Name):
                    continue
                ad = hedef.id.lower()
                if hedef.id == "_ORAN_TAVANI":
                    # ARALIK SINIRI, oran DEĞİL: şemanın 0..100 kapısı.
                    continue
                if any(
                    k in ad
                    for k in ("stopaj", "withholding", "sgk", "bagkur", "oran")
                ):
                    raise AssertionError(
                        f"{dosya.name} içinde modül seviyesinde oran sabiti: "
                        f"{hedef.id}. Oran SATIRDAN gelir."
                    )


def test_yazma_semasinda_sunucunun_turettigi_alanlar_YOK() -> None:
    """Yazma şeması hiçbir TÜREV TUTAR taşımaz; `extra=forbid` onları 422 yapar.

    Alan şemada olsaydı bir istemci hatası (ya da kötü niyet) stopajı
    sessizce sıfırlayabilirdi ve makbuz vergi dairesine yanlış giderdi.
    """
    from app.mustahsil_schemas import ProducerReceiptItemWrite, ProducerReceiptWrite

    kalem = set(ProducerReceiptItemWrite.model_fields)
    for turev in (
        "line_gross",
        "withholding_amount",
        "social_security_amount",
        "line_net",
        "entered_factor",
        "base_quantity",
        "ticket_net_snapshot",
    ):
        assert turev not in kalem, (turev, sorted(kalem))
    baslik = set(ProducerReceiptWrite.model_fields)
    for turev in (
        "gross_amount",
        "withholding_total",
        "social_security_total",
        "net_payable",
        "receipt_no",
        "status",
        "issued_at",
    ):
        assert turev not in baslik, (turev, sorted(baslik))
    assert ProducerReceiptWrite.model_config.get("extra") == "forbid"
    assert ProducerReceiptItemWrite.model_config.get("extra") == "forbid"
    # ORANLAR ZORUNLU: varsayılan koymak kodda yasal bir sabit tutmaktı.
    for ad in ("withholding_rate", "social_security_rate"):
        assert ProducerReceiptItemWrite.model_fields[ad].is_required(), ad


def test_uc_purchases_iznine_bagli_GET_DAHIL() -> None:
    """Tüm yöntemler `purchases`. GET de — makbuz tedarikçi MALİYETİDİR.

    Kural temel ``read`` kuralının ÜSTÜNDE durmalı; altında kalsaydı okuma
    sessizce herkese açık `read` iznine düşerdi.
    """
    from app.auth import required_permission

    for yontem in ("GET", "POST", "PUT", "DELETE"):
        assert (
            required_permission(yontem, "/api/producer-receipts") == "purchases"
        ), yontem
    assert (
        required_permission("POST", "/api/producer-receipts/1/issue") == "purchases"
    )
    # Kapının GERÇEKTEN bu satır sayesinde kapalı olduğunu göster: benzer ama
    # başka bir yol temel `read` kuralına düşer.
    assert required_permission("GET", "/api/producer-baska-bir-sey") == "read"


def test_belge_serisi_kayitli_ve_numara_sutunu_BILDIRILMIS() -> None:
    """`producer_receipts` DOCUMENT_TABLES'ta ve numara sütunu `receipt_no`.

    Sütun bildirilmeseydi `next_document_no`nun tekrar denetimi olmayan
    `document_no` sütununa bakar ve SQL hatası verirdi.
    """
    from app.document_engine import DOCUMENT_NUMBER_COLUMNS, DOCUMENT_TABLES

    assert "producer_receipts" in DOCUMENT_TABLES
    assert DOCUMENT_NUMBER_COLUMNS["producer_receipts"] == "receipt_no"


def test_silme_ucu_YOK() -> None:
    """Numarası verilmiş bir vergi belgesi kayıttan kaldırılamaz."""
    kaynak = ROUTER.read_text(encoding="utf-8")
    assert "@router.delete" not in kaynak, (
        "Silme ucu eklenmiş: kesilmiş makbuz vergi belgesidir, `cancel` onu "
        "`cancelled` yapar ve SATIRLAR DURUR."
    )


def test_fis_neti_ITHAL_EDILIYOR_kopyalanmiyor() -> None:
    """Bileşim kuralı TEK YERDE: `farm._turetilmis_net` ithal edilir.

    İkinci bir kopya, kural (toplamsal/sıralı) bir gün düzeltildiğinde
    makbuzu fişten AYIRIRDI ve hangisinin doğru olduğu sorulamazdı.

    İTHALIN KULLANILDIĞI da ölçülüyor: kullanılmayan bir `import` kuralı
    tek yerde tutmaz, yalnız öyle görünmesini sağlar.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    assert "from .farm import _turetilmis_net" in kaynak
    kod = _kod_metni(ROUTER)
    assert "_turetilmis_net(" in kod, "İthal edilmiş ama ÇAĞRILMAMIŞ."
    # Formül router'da YENİDEN yazılmamalı: bileşimin imzası `/ 100` ile
    # oran çarpımıdır ve burada hiç geçmemeli.
    assert "rate_percent /" not in kod and "/ 100" not in kod, (
        "Kesinti bileşimi router'da yeniden yazılmış görünüyor; kural "
        "`farm._turetilmis_net`te KALMALI."
    )


# ---------------------------------------------------------------------------
# DAVRANIŞ — gerçek şema, gerçek uçlar (alt süreç, kendi veritabanı).
# ---------------------------------------------------------------------------


def run_makbuz_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "MUSTAHSIL MAKBUZU TAMAM" in completed.stdout, completed.stdout


def test_mustahsil_makbuzu_sqlite(tmp_path: Path) -> None:
    run_makbuz_smoke(f"sqlite:///{(tmp_path / 'mustahsil.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.main import app

ADMIN_PW = 'Mustahsil!123'
URUN_ID = 5101
URUN_TABANSIZ_ID = 5102


def admin_headers(client):
    for candidate in ('admin123', ADMIN_PW):
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':candidate,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h, int(body['companies'][0]['id'])


def urun_yaz(db, urun_id, cid, taban):
    """Urun ham SQL ile: bu dosyanin konusu urun ucu degil, makbuz.

    `active` BOOLEAN olarak baglaniyor, 1 olarak DEGIL: PostgreSQL boolean
    sutununa tamsayi kabul etmez (kantar ikizinde olculmus kusur).
    """
    db.execute(_sql(
        "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
        "stock,unit,price_per,active,critical_stock,minimum_stock,company_id,"
        "base_unit) VALUES (:i,:n,0,0,0,0,'KG',1,:a,0,0,:c,:b)"),
        {'i':urun_id,'n':'Mustahsil urunu %d' % urun_id,'a':True,'c':cid,
         'b':taban})


def stok_sayilari(db, cid):
    hareket = db.execute(_sql(
        "SELECT COUNT(*) FROM stock_movements WHERE company_id=:c"),
        {'c':cid}).scalar_one()
    finans = db.execute(_sql(
        "SELECT COUNT(*) FROM finance_transactions WHERE company_id=:c"),
        {'c':cid}).scalar_one()
    odeme = db.execute(_sql(
        "SELECT COUNT(*) FROM payments WHERE company_id=:c"),
        {'c':cid}).scalar_one()
    return int(hareket), int(finans), int(odeme)


with TestClient(app) as client:
    h, cid = admin_headers(client)

    # --- HAZIRLIK: iki firma, iki tedarikci, bir urun --------------------
    with SessionLocal() as db:
        urun_yaz(db, URUN_ID, cid, 'KG')
        urun_yaz(db, URUN_TABANSIZ_ID, cid, None)
        db.execute(_sql(
            "INSERT INTO companies(name,is_active,created_at)"
            " VALUES(:n,:a,:t)"),
            {'n':'Mustahsil Yabanci AS','a':True,'t':'2026-01-01T00:00:00+00:00'})
        yabanci_cid = int(db.execute(_sql(
            "SELECT id FROM companies WHERE name='Mustahsil Yabanci AS'"
            )).scalar_one())
        assert yabanci_cid != cid
        for c_, ad in ((cid,'Benim Ciftci'), (yabanci_cid,'Yabanci Ciftci')):
            db.execute(_sql(
                "INSERT INTO suppliers(name,tax_number,opening_balance,"
                "is_active,company_id) VALUES(:n,:v,0,:a,:c)"),
                {'n':ad,'v':'11111111111','a':True,'c':c_})
        benim_ciftci = int(db.execute(_sql(
            "SELECT id FROM suppliers WHERE company_id=:c AND name='Benim Ciftci'"),
            {'c':cid}).scalar_one())
        yabanci_ciftci = int(db.execute(_sql(
            "SELECT id FROM suppliers WHERE company_id=:c AND name='Yabanci Ciftci'"),
            {'c':yabanci_cid}).scalar_one())
        db.commit()
        taban_hareket, taban_finans, taban_odeme = stok_sayilari(db, cid)

    # --- 1. TASLAK DOGAR, NUMARASIZ -------------------------------------
    kalem = {'product_id':URUN_ID,'entered_quantity':'1000','entered_unit':'KG',
             'unit_price':'12.50','withholding_rate':'2','social_security_rate':'1'}
    oluştur = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'items':[kalem]})
    assert oluştur.status_code == 201, oluştur.text
    makbuz = oluştur.json()
    assert makbuz['status'] == 'draft', makbuz
    assert makbuz['receipt_no'] is None, 'TASLAKTA NUMARA VAR'
    assert makbuz['issued_at'] is None
    # 1000 KG x 12.50 = 12500.00; stopaj %2 = 250.00; sgk %1 = 125.00.
    assert makbuz['gross_amount'] == '12500.00', makbuz
    assert makbuz['withholding_total'] == '250.00', makbuz
    assert makbuz['social_security_total'] == '125.00', makbuz
    assert makbuz['net_payable'] == '12125.00', makbuz
    assert makbuz['items'][0]['base_quantity'] == '1000.0000', makbuz
    makbuz_id = makbuz['id']

    # --- 2. TABAN BIRIM YOKSA 422, VARSAYIM DEGIL -----------------------
    tabansiz = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,
        'items':[dict(kalem, product_id=URUN_TABANSIZ_ID)]})
    assert tabansiz.status_code == 422, tabansiz.text
    assert tabansiz.json()['detail']['sebep'] == 'TABAN_BILDIRILMEMIS', tabansiz.text
    # Urun karti HIC verilmezse de taban birim YOKTUR: ayni red, ayni sebep.
    urunsuz = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,
        'items':[{k:v for k,v in kalem.items() if k != 'product_id'}]})
    assert urunsuz.status_code == 422, urunsuz.text
    assert urunsuz.json()['detail']['sebep'] == 'TABAN_BILDIRILMEMIS', urunsuz.text

    # --- 3. NaN/Infinity SEMADA DURUR -----------------------------------
    for bozuk in ('NaN','Infinity','-Infinity'):
        red = client.post('/api/producer-receipts', headers=h, json={
            'supplier_id':benim_ciftci,'items':[dict(kalem, unit_price=bozuk)]})
        assert red.status_code == 422, (bozuk, red.text)

    # --- 4. KIRACI SINIRI: baska firmanin tedarikcisi 404 ---------------
    sizinti = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':yabanci_ciftci,'items':[kalem]})
    assert sizinti.status_code == 404, (
        'BASKA FIRMANIN TEDARIKCISINE makbuz kesildi: ' + sizinti.text)

    # --- 5. ISSUE: numara MM- serisinden, TEKRAR 409 --------------------
    kes = client.post('/api/producer-receipts/%d/issue' % makbuz_id, headers=h)
    assert kes.status_code == 200, kes.text
    kesilmis = kes.json()
    assert kesilmis['status'] == 'issued', kesilmis
    numara = kesilmis['receipt_no']
    assert numara is not None and numara.startswith('MM-'), kesilmis
    assert kesilmis['issued_at'] is not None
    # Tutarlar issue ile DEGISMEZ.
    assert kesilmis['net_payable'] == '12125.00', kesilmis

    tekrar = client.post('/api/producer-receipts/%d/issue' % makbuz_id, headers=h)
    assert tekrar.status_code == 409, (
        'ISSUE TEKRARI GECTI - makbuz iki numara tasiyabilir: ' + tekrar.text)
    sonra = client.get('/api/producer-receipts/%d' % makbuz_id, headers=h).json()
    assert sonra['receipt_no'] == numara, (
        'IKINCI NUMARA URETILDI: %s -> %s' % (numara, sonra['receipt_no']))

    # Seri ARTIYOR: ikinci makbuz ikinci numarayi alir.
    ikinci_id = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'items':[kalem]}).json()['id']
    ikinci_no = client.post('/api/producer-receipts/%d/issue' % ikinci_id,
                            headers=h).json()['receipt_no']
    assert ikinci_no != numara, (numara, ikinci_no)
    assert ikinci_no.startswith('MM-'), ikinci_no

    # --- 6. KALEMSIZ MAKBUZ KESILEMEZ -----------------------------------
    bos_id = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'items':[]}).json()['id']
    bos_kes = client.post('/api/producer-receipts/%d/issue' % bos_id, headers=h)
    assert bos_kes.status_code == 422, bos_kes.text

    # --- 7. CANCEL: satirlar DURUR, numara KORUNUR ----------------------
    iptal = client.post('/api/producer-receipts/%d/cancel' % makbuz_id, headers=h)
    assert iptal.status_code == 200, iptal.text
    iptalli = iptal.json()
    assert iptalli['status'] == 'cancelled', iptalli
    assert iptalli['receipt_no'] == numara, 'IPTALDE NUMARA SILINDI'
    assert len(iptalli['items']) == 1, 'IPTALDE KALEMLER SILINDI'
    assert iptalli['net_payable'] == '12125.00'
    # Taslak iptal EDILEMEZ.
    assert client.post('/api/producer-receipts/%d/cancel' % bos_id,
                       headers=h).status_code == 409

    # --- 8. DEFTERE HICBIR SEY YAZILMADI --------------------------------
    with SessionLocal() as db:
        hareket, finans, odeme = stok_sayilari(db, cid)
    assert hareket == taban_hareket, (
        'STOK HAREKETI YAZILDI: %d -> %d' % (taban_hareket, hareket))
    assert finans == taban_finans, (
        'FINANS SATIRI YAZILDI: %d -> %d' % (taban_finans, finans))
    assert odeme == taban_odeme, (
        'ODEME YAZILDI: %d -> %d' % (taban_odeme, odeme))

    # --- 9. FIS BAGI: oneri ve EZME, IKISI DE SAKLANIR ------------------
    # Tarla -> parsel -> sezon -> hasat -> fis zinciri HAM SQL ile kuruluyor:
    # bu dosyanin konusu kantar ucu degil, makbuzun fisten ONERI almasi.
    # (Kantar ucunun kendi sozlesmesi kardes dosyada.) Zincir KOSULSUZ
    # kuruluyor: "varsa olc" bicimindeki bir kontrol, tablo bos oldugu gun
    # senaryo 9'u SESSIZCE atlar ve bu dosyanin 8. maddesi olculmemis olurdu.
    T = '2026-01-01T00:00:00+00:00'
    with SessionLocal() as db:
        tarla_id = int(db.execute(_sql(
            "INSERT INTO farms(company_id,code,name,status,created_at,"
            "updated_at) VALUES(:c,'MM-T1','Mustahsil Tarlasi','ACTIVE',:t,:t)"
            " RETURNING id"), {'c':cid,'t':T}).scalar_one())
        parsel_id = int(db.execute(_sql(
            "INSERT INTO farm_parcels(company_id,farm_id,code,name,"
            "area_decare,status,created_at,updated_at) VALUES(:c,:f,'MM-P1',"
            "'Parsel',10,'ACTIVE',:t,:t) RETURNING id"),
            {'c':cid,'f':tarla_id,'t':T}).scalar_one())
        sezon_id = int(db.execute(_sql(
            "INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,"
            "status,product_id,created_at,updated_at) VALUES(:c,:p,2026,"
            "'Bugday','ACTIVE',:u,:t,:t) RETURNING id"),
            {'c':cid,'p':parsel_id,'u':URUN_ID,'t':T}).scalar_one())
        hasat_id = int(db.execute(_sql(
            "INSERT INTO field_harvests(company_id,season_id,harvested_on,"
            "quantity,unit,status,created_at,updated_at) VALUES(:c,:s,"
            "'2026-08-01',1000,'KG','DONE',:t,:t) RETURNING id"),
            {'c':cid,'s':sezon_id,'t':T}).scalar_one())
        fis_id = int(db.execute(_sql(
            "INSERT INTO field_harvest_tickets(company_id,harvest_id,"
            "gross_entered_quantity,entered_unit,entered_factor,"
            "base_quantity,created_at,updated_at) VALUES(:c,:hh,1000,'KG',"
            "1,1000,:t,:t) RETURNING id"), {'c':cid,'hh':hasat_id,'t':T}
            ).scalar_one())
        db.execute(_sql(
            "INSERT INTO field_harvest_ticket_deductions(company_id,"
            "ticket_id,label,rate_percent,created_at,updated_at)"
            " VALUES(:c,:f,'Rutubet',5,:t,:t)"), {'c':cid,'f':fis_id,'t':T})
        db.commit()

    # ONERI: turetilen net = 1000 - 1000*5/100 = 950.0000 (TOPLAMSAL bilesim,
    # `farm._turetilmis_net`ten ITHAL). Fisin kendi kagit neti DEGIL.
    oneri = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'ticket_id':fis_id,'items':[kalem]})
    assert oneri.status_code == 201, oneri.text
    o = oneri.json()['items'][0]
    assert o['base_quantity'] == '950.0000', (
        'FISIN TURETILEN NETI ONERI OLARAK KULLANILMADI: %s' % o)
    assert o['ticket_net_snapshot'] == '950.0000', o
    # 950 x 12.50 = 11875.00
    assert oneri.json()['gross_amount'] == '11875.00', oneri.json()

    # EZME: kullanici 900 yazar; IKISI DE saklanir.
    ezme = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'ticket_id':fis_id,
        'items':[dict(kalem, base_quantity_override='900')]})
    assert ezme.status_code == 201, ezme.text
    e = ezme.json()['items'][0]
    assert e['base_quantity'] == '900.0000', e
    assert e['ticket_net_snapshot'] == '950.0000', (
        'EZILINCE FISIN ONERISI KAYBOLDU: %s' % e)
    assert ezme.json()['gross_amount'] == '11250.00', ezme.json()

    # BASKA FIRMANIN FISI 404: bilesik FK son savunma, bu ilk.
    yabanci_fis = client.post('/api/producer-receipts', headers=h, json={
        'supplier_id':benim_ciftci,'ticket_id':fis_id + 10000,'items':[kalem]})
    assert yabanci_fis.status_code == 404, yabanci_fis.text

    # --- 10. LISTE SUZGECLERI -------------------------------------------
    hepsi = client.get('/api/producer-receipts', headers=h).json()
    assert len(hepsi) >= 3, hepsi
    iptalliler = client.get('/api/producer-receipts?status=cancelled',
                            headers=h).json()
    assert [r['id'] for r in iptalliler] == [makbuz_id], iptalliler
    baskasi = client.get('/api/producer-receipts?supplier_id=%d' % yabanci_ciftci,
                         headers=h).json()
    assert baskasi == [], ('BASKA FIRMANIN TEDARIKCISI LISTEDE: %s' % baskasi)

    # TARIH ARALIGI `issued_at` uzerindedir ve TASLAKLAR DUSER: hic kesilmemis
    # bir kagit "su iki tarih arasinda kesilenler"in cevabinda olamaz.
    genis = client.get(
        '/api/producer-receipts?date_from=2020-01-01&date_to=2099-12-31',
        headers=h).json()
    assert genis, 'GENIS ARALIK BOS DONDU'
    assert all(r['status'] != 'draft' for r in genis), (
        'TARIH ARALIGINA TASLAK DUSTU: %s' % [r['id'] for r in genis
                                              if r['status'] == 'draft'])
    assert all(r['issued_at'] is not None for r in genis), genis
    dar = client.get('/api/producer-receipts?date_from=2099-01-01',
                     headers=h).json()
    assert dar == [], dar

    # COZULEMEYEN TARIH 422 VERIR, 500 DEGIL. OLCULDU (PostgreSQL 16.13):
    # ham dizgi `timestamptz` ile karsilastirilinca surucu
    # InvalidDatetimeFormat atiyor ve uc 500 donduruyordu; SQLite'ta ayni
    # sorgu SESSIZCE bos liste veriyordu, yani kusur yalniz uretim
    # diyalektinde gorunurdu. Kapi her iki diyalektte de 422 olcer.
    for bozuk in ('not-a-date', '2026-13-45', ''):
        red = client.get('/api/producer-receipts?date_from=%s' % bozuk,
                         headers=h)
        assert red.status_code == 422, (bozuk, red.status_code, red.text)

    # --- 11. BASKA FIRMANIN MAKBUZU OKUNAMAZ/KESILEMEZ ------------------
    with SessionLocal() as db:
        yabanci_makbuz = int(db.execute(_sql(
            "INSERT INTO producer_receipts(company_id,supplier_id,gross_amount,"
            "withholding_total,social_security_total,net_payable,status,"
            "created_at,updated_at) VALUES(:c,:s,0,0,0,0,'draft',:t,:t)"
            " RETURNING id"),
            {'c':yabanci_cid,'s':yabanci_ciftci,'t':'2026-01-01T00:00:00+00:00'}
            ).scalar_one())
        db.commit()
    for yol, yontem in (('', 'get'), ('/issue', 'post'), ('/cancel', 'post')):
        cevap = getattr(client, yontem)(
            '/api/producer-receipts/%d%s' % (yabanci_makbuz, yol), headers=h)
        assert cevap.status_code == 404, (
            'BASKA FIRMANIN MAKBUZUNA ERISILDI (%s): %s' % (yol, cevap.text))

print('MUSTAHSIL MAKBUZU TAMAM')
'''
