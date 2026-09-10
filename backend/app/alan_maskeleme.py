"""SEC-3b — cari (musteri/tedarikci) hassas alanlarinin ROLE GORE maskelenmesi.

--- BU DOSYA NIYE VAR -------------------------------------------------------

SEC-3 (#102) ondokuz GET'i `read`ten cikarip `purchases`/`sales`/`payments`/
`stock` iznine bagladi, ama `auth.py` icindeki kendi notu sinirini yaziyor:

    "Cari LISTESI ve DETAYI (`/api/customers`, `/api/customers/{id}`)
     BILEREK `read`te KALIYOR -- gunluk is yuzeyi; alan maskeleme ayri bir
     is olarak (SEC-3b) acildi."

Yani `depo` ve `rapor` bugun musteri listesini ve kartini TAM okuyor: telefon,
e-posta, adres ve VERGI NUMARASI dahil. SEC-3 KIMIN girecegini daraltti; bu
dosya GIREN'in NE GORECEGINI daraltiyor. Ikisi ayri kapidir ve ikincisi
olmadan birincisi cari veriyi korumaz.

--- NEDEN TEK TABLO ---------------------------------------------------------

Maskeleme kurallari `MASKELENEN_ALANLAR` icinde TEK yerdedir. Uc bagimsiz
serilestirme yuzeyi (`customers.py` listesi, `finance.py` tedarikci listesi,
`entity_detail.py` karti) ayni tabloyu okur; alan adi bir yerde eklenip
digerinde unutulamaz. Yeni bir hassas sutun geldiginde tek satir eklenir ve
`tests/pins/cari_alan_envanteri.txt` pini o sutunu tasiyan HER rotayi
siniflandirilmamis diye kirmizi yakar.

--- SAF FONKSIYON -----------------------------------------------------------

Bu modul I/O YAPMAZ, DB'ye DOKUNMAZ, `Request` GORMEZ. Girdisi bir `dict` ve
bir rol adi; ciktisi YENI bir `dict`tir -- girdi ASLA yerinde degistirilmez.
Cagiran, satiri zaten `dict`e cevirdigi noktada bu fonksiyondan gecirir.
Boylece maskeleme birim testte veritabanisiz olculebilir.

--- MASKESIZ ROLLER: `admin` NEDEN LISTEDE ----------------------------------

Urun karari uc rolu maskesiz sayiyor: `yonetici`, `muhasebe`, `satis`.
`admin` de listededir ve bu OLCUME dayanir, varsayima degil: `admin` izni
`"*"` jokeridir ve `ROLE_RANK` icinde 100 ile `yonetici`nin (80) USTUNDEDIR.
Maskelemeyi "listede olmayan herkesi maskele" diye yazip `admin`i disarida
birakmak, sistemin EN YETKILI rolune `yonetici`den DAHA AZ veri gosterirdi --
yetki siralamasini tersine ceviren bir hata olurdu.

Karar YONU su: liste MASKESIZ olanlari sayar, maskelenenleri DEGIL. Yarin
eklenen bir rol otomatik olarak MASKELI baslar (deny-by-default). Yeni rolun
tam veri gormesi ACIK bir satir eklemeyi gerektirir.
"""
from __future__ import annotations

from typing import Any, Callable

#: Maskeleme UYGULANMAYAN roller. Bunun disindaki HER rol maskelidir
#: (deny-by-default); yeni bir rol sessizce tam veri goremez.
#: `admin` icin gerekce modul basligindadir.
MASKESIZ_ROLLER: frozenset[str] = frozenset({"admin", "yonetici", "muhasebe", "satis"})

#: Maskelenen degerin tamamen gizlendigi durumlarda donen sabit. Degerin VAR
#: oldugunu soyler, icerigini soylemez.
TAM_MASKE = "***"


def maskele_telefon(deger: Any) -> Any:
    """Telefonun yalniz ILK IKI ve SON IKI hanesi kalir.

    Ornek: ``0555 123 45 12`` -> ``05** *** ** 12``.

    Rakam sayisi 11 ise (Turkiye cep formati) ciktiya 4-3-2-2 gruplamasi
    uygulanir; degilse gruplanmadan dondurulur. Dort haneden KISA numaralarda
    "iki bas + iki son" degerin neredeyse tamamini acardi, o yuzden tamami
    maskelenir.
    """
    metin = _metin(deger)
    if metin is None:
        return deger
    haneler = [k for k in metin if k.isdigit()]
    if len(haneler) < 4:
        return TAM_MASKE
    gizli = "".join(haneler[:2]) + "*" * (len(haneler) - 4) + "".join(haneler[-2:])
    if len(gizli) != 11:
        return gizli
    return f"{gizli[:4]} {gizli[4:7]} {gizli[7:9]} {gizli[9:]}"


def maskele_eposta(deger: Any) -> Any:
    """Yerel kismin ILK KARAKTERI kalir, alan adi DEGISMEZ.

    Ornek: ``ahmet@ornek.com`` -> ``a***@ornek.com``.

    Alan adi bilerek acik birakiliyor: kurumsal alan adi zaten cari ADINDAN
    cikarilabilir ve destek ekibi "hangi firmadan yazmis" sorusunu maskeli
    rolde de cevaplayabilmelidir. Gizlenen KISININ kimligidir.

    ``@`` icermeyen ya da yerel kismi bos olan deger e-posta DEGILDIR; ne
    oldugu bilinmediginden tamami maskelenir.
    """
    metin = _metin(deger)
    if metin is None:
        return deger
    yerel, ayrac, alan = metin.partition("@")
    if not ayrac or not yerel or not alan:
        return TAM_MASKE
    return f"{yerel[0]}***@{alan}"


def maskele_vergi_no(deger: Any) -> Any:
    """Vergi/TC numarasinin yalniz SON UC hanesi kalir; uzunluk korunur.

    Ornek: ``1234567890`` -> ``*******890``.

    Uc haneden kisa deger zaten uc haneyi acmaya yetmez, tamami maskelenir.
    """
    metin = _metin(deger)
    if metin is None:
        return deger
    if len(metin) <= 3:
        return TAM_MASKE
    return "*" * (len(metin) - 3) + metin[-3:]


def maskele_adres(deger: Any) -> Any:
    """Adresin yalniz ILK SATIRI, 24 karaktere kirpilarak kalir.

    Ucnokta (``…``) yalniz GERCEKTEN icerik dusuruldugunde eklenir: ya satir
    24 karakteri asmistir ya da alt satirlar atilmistir. Kisa tek satirlik bir
    adres oldugu gibi doner ve okuyucu yanlislikla "devami var" sanmaz.
    Kirpma KARAKTER bazlidir, bayt degil -- Turkce ve diger Unicode adresler
    ortasindan bolunmez.
    """
    metin = _metin(deger)
    if metin is None:
        return deger
    satirlar = metin.splitlines()
    if not satirlar:
        return TAM_MASKE
    ilk = satirlar[0].strip()
    dusuruldu = len(satirlar) > 1 or len(ilk) > 24
    if len(ilk) > 24:
        ilk = ilk[:24]
    return f"{ilk}…" if dusuruldu else ilk


def maskele_iban(deger: Any) -> Any:
    """IBAN/banka hesabinin yalniz SON DORT hanesi kalir; uzunluk korunur.

    OLCUM (2026-09-10, develop 72cfa09): `customers` ve `suppliers`
    tablolarinda IBAN ya da banka sutunu YOKTUR. Depodaki tek `iban` sutunu
    `finance_engine.finance_accounts` uzerindedir ve o FIRMANIN KENDI kasa/
    banka hesabidir, bir cari degildir -- SEC-3b kapsami disindadir.

    Fonksiyon yine de burada ve `MASKELENEN_ALANLAR` yine de `iban`/
    `bank_account` anahtarlarini tasiyor: cariye boyle bir sutun eklendigi gun
    maskeleme kendiliginden devreye girsin, ekleyen kisinin bu dosyayi
    hatirlamasi gerekmesin diye. Bugun cari yuzeyinde cagrilani YOKTUR ve bu
    bilinclidir.
    """
    metin = _metin(deger)
    if metin is None:
        return deger
    if len(metin) <= 4:
        return TAM_MASKE
    return "*" * (len(metin) - 4) + metin[-4:]


#: Alan adi -> maskeleme fonksiyonu. TEK KAYNAK: her serilestirme yuzeyi
#: bunu okur. `owner_name` BILEREK YOK -- cari yetkilisinin adi ekranin
#: calisma birimidir (depo "kimden geldi" diye sorar) ve urun karari yalniz
#: iletisim/vergi verisini maskeliyor.
MASKELENEN_ALANLAR: dict[str, Callable[[Any], Any]] = {
    "phone": maskele_telefon,
    "email": maskele_eposta,
    "tax_number": maskele_vergi_no,
    "address": maskele_adres,
    # Bugun cari tablolarinda YOK; gerekce `maskele_iban` docstring'inde.
    "iban": maskele_iban,
    "bank_account": maskele_iban,
}

#: Duz (nested olmayan) sutun adlarinda kullanilan cari onekleri. `work_orders`
#: birlesimi alani `c.tax_number customer_tax_number` olarak DUZLESTIRIYOR;
#: maskeleme o adlari da yakalamazsa ayni veri ikinci bir yoldan sizardi.
CARI_ONEKLERI: tuple[str, ...] = ("customer_", "supplier_")


def maskelenecek_mi(rol: Any) -> bool:
    """Rol maskeli mi? Bilinmeyen/bos rol MASKELIDIR (deny-by-default)."""
    return not (isinstance(rol, str) and rol in MASKESIZ_ROLLER)


def _alan_maskesi(anahtar: str) -> Callable[[Any], Any] | None:
    """Sutun adina karsilik gelen maskeleme fonksiyonu (varsa).

    Once TAM ad denenir (`phone`), sonra cari onekleri soyulur
    (`customer_phone` -> `phone`). Onek soyma yalniz `CARI_ONEKLERI` icin
    yapilir: `company_tax_number` FIRMANIN KENDI vergi numarasidir, cari
    verisi degildir ve maskelenmemelidir.
    """
    maske = MASKELENEN_ALANLAR.get(anahtar)
    if maske is not None:
        return maske
    for onek in CARI_ONEKLERI:
        if anahtar.startswith(onek):
            return MASKELENEN_ALANLAR.get(anahtar[len(onek):])
    return None


def maskele_cari(row: Any, role: Any) -> dict:
    """Tek bir cari satirini role gore maskeler; YENI `dict` doner.

    Girdi `dict` ya da SQLAlchemy `RowMapping` olabilir; her iki durumda da
    cikti yeni bir `dict`tir ve GIRDI DEGISMEZ. Cagiranlar ayni satiri hem
    maskeli yanitta hem maskesiz denetim kaydinda kullanabildigi icin bu
    onemlidir: yerinde degistirme, `record_change`in "once/sonra"
    goruntusunu sessizce bozardi.
    """
    veri = dict(row)
    if not maskelenecek_mi(role):
        return veri
    for anahtar, deger in veri.items():
        if deger is None:
            continue
        maske = _alan_maskesi(anahtar)
        if maske is not None:
            veri[anahtar] = maske(deger)
    return veri


def maskele_cari_listesi(rows: Any, role: Any) -> list[dict]:
    """`maskele_cari`nin liste hali; sira KORUNUR."""
    return [maskele_cari(row, role) for row in rows]


def _metin(deger: Any) -> str | None:
    """Maskelenebilir metni dondurur; maskelenemeyecekse ``None``.

    ``None`` ve bos/bosluk-only deger OLDUGU GIBI birakilir: "veri yok"
    ile "veri var ama gizli" ayrimi maskeleme sonrasi da okunabilir kalir.
    Bos bir telefonu ``***`` yapmak, olmayan bir numarayi VARMIS gibi
    gosterirdi.
    """
    if deger is None:
        return None
    metin = deger if isinstance(deger, str) else str(deger)
    if not metin.strip():
        return None
    return metin
