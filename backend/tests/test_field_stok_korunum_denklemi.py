"""KORUNUM DENKLEMİ: iki fonksiyon da AYNI ve TAM kova kümesini saymalı.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

`olaylari_isle` denklemine `CLAIM_LOST`i KATIYORDU; `tum_firmalari_isle`
KATMIYORDU — yalnız terminal kovaları sayıyordu. Gerçek bir talep yarışında
`girdi=1, CLAIM_LOST=1` gelir, terminal toplam 0 kalır ve assert PATLAR.
Patlayan assert zamanlayıcının genel `except Exception` kolluna kaçar; yani
`CLAIM_LOST=1` taşıyan NORMAL bir döngü satırı HİÇ yazılamazdı.

--- BU DOSYA NEYİ DONDURUR ---------------------------------------------------

"Sayılmayan bir terim yüzünden tutan invaryant, invaryant değildir." Bu yüzden
burada üç şey birden TÜRETİLİR — elle yazılmış kova listesi YOK:

1. **Kova kümesi**: `_sayac()` — iki fonksiyonun da tek sayaç kurucusu.
2. **Yazılan kovalar**: `olaylari_isle` gövdesindeki HER `sayac[...] += 1`
   AST'den toplanır; kurucuda olmayan bir kova yazmak testi kırar.
3. **Denklem terimleri**: iki fonksiyonun `cikti = ...` ifadesindeki adlar
   AST'den okunup modüldeki DEĞERLERİNE çözülür.

Sonuç: iki denklem birbirinden ayrıştığı an ya da bir kova denklemlerin
dışında kaldığı an bu dosya kırmızı olur ve EKSİK TERİMİ söyler.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
KAYNAK = BACKEND / "app" / "field_stok_tuketici.py"

GIRDI = "girdi"


def _agac() -> ast.Module:
    return ast.parse(KAYNAK.read_text(encoding="utf-8"), filename=str(KAYNAK))


def _fonksiyon(ad: str) -> ast.FunctionDef:
    for dugum in _agac().body:
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError("fonksiyon bulunamadı: %s" % ad)


def _cozumle(ad: str) -> set[str]:
    """Modüldeki bir adı, TAŞIDIĞI durum metin(ler)ine çözer."""
    from app import field_stok_tuketici as tuketici

    deger = getattr(tuketici, ad)
    if isinstance(deger, str):
        return {deger}
    if isinstance(deger, (tuple, list, set, frozenset)):
        return {d for d in deger if isinstance(d, str)}
    raise AssertionError(f"denklem terimi bir duruma çözülmüyor: {ad}={deger!r}")


def _denklem_terimleri(fonksiyon_adi: str) -> set[str]:
    """`cikti = ...` ifadesinde geçen adların çözülmüş kova kümesi."""
    fonksiyon = _fonksiyon(fonksiyon_adi)
    atamalar = [
        dugum for dugum in ast.walk(fonksiyon)
        if isinstance(dugum, ast.Assign)
        and any(
            isinstance(h, ast.Name) and h.id == "cikti" for h in dugum.targets
        )
    ]
    assert len(atamalar) == 1, (
        f"{fonksiyon_adi}: tam olarak BİR `cikti` ataması bekleniyordu; "
        f"bulunan {len(atamalar)}. Denklem okunamıyorsa dondurulamaz."
    )
    adlar = {
        d.id for d in ast.walk(atamalar[0])
        if isinstance(d, ast.Name) and d.id.isupper()
    }
    assert adlar, f"{fonksiyon_adi}: denklemde çözülebilir bir terim yok"
    kovalar: set[str] = set()
    for ad in adlar:
        kovalar |= _cozumle(ad)
    return kovalar


def _kovalar() -> set[str]:
    from app.field_stok_tuketici import _sayac

    return set(_sayac()) - {GIRDI}


#: Kova adini DONDUREN fonksiyonlar. Kovalar artik `olaylari_isle` icinde
#: yazilmiyor; orada TEK bir artis noktasi var ve artirilacak kova bu iki
#: fonksiyonun DONUS DEGERINDEN geliyor.
#: `_taze_oturumda_kurtar` de bir kova DONDURUR: `_kurtar` onun donusunu
#: dogrudan dondurur. Listeye eklenmezse donusleri bu dosyanin gozunden KACAR
#: ve kurucuda olmayan bir kova dondurmek sessizce mumkun olurdu.
KOVA_DONDUREN = ("_bir_olayi_isle", "_kurtar", "_taze_oturumda_kurtar")


def _dondurulen_kovalar() -> set[str]:
    """`_bir_olayi_isle` ve `_kurtar` icindeki her `return` kovasi."""
    kovalar: set[str] = set()
    for ad in KOVA_DONDUREN:
        for dugum in ast.walk(_fonksiyon(ad)):
            if not isinstance(dugum, ast.Return) or dugum.value is None:
                continue
            deger = dugum.value
            if isinstance(deger, ast.Constant) and isinstance(deger.value, str):
                kovalar.add(deger.value)
            elif isinstance(deger, ast.Name):
                kovalar |= _cozumle(deger.id)
            elif isinstance(deger, ast.Call):
                # `return _kurtar(...)` — kovalari kendi govdesinden toplanir.
                continue
            else:  # pragma: no cover - okunamayan donus dondurulamaz
                raise AssertionError(f"kova donusu okunamadı: {ast.dump(deger)}")
    return kovalar


def test_DONDURULEN_her_kova_sayac_kurucusunda_VAR() -> None:
    """Kurucuda olmayan bir kova döndürmek, denklemin dışına kaçmak olurdu.

    Kova döndüren fonksiyonlar `sayac`a DOKUNMAZ; döndürdükleri ad
    `olaylari_isle` içindeki TEK artış noktasında sayaca yazılır. Bu yüzden
    burada yazımlar değil DÖNÜŞLER toplanır.
    """
    from app import field_stok_tuketici as tuketici

    dondurulen = _dondurulen_kovalar()
    assert dondurulen, "Hiç kova dönüşü bulunamadı; bu koşum sınamıyor."
    kurucu = set(tuketici._sayac())
    kacak = sorted(dondurulen - kurucu)
    assert not kacak, (
        "SAYAÇ KURUCUSUNDA OLMAYAN KOVA DÖNDÜRÜLÜYOR: "
        f"{kacak!r}. Bu kova hiçbir korunum denklemine giremez."
    )


def test_SAYAC_ARTISI_TEK_NOKTADA_ve_donus_degeriyle() -> None:
    """ÇİFT ARTIŞ yapısal olarak İMKÂNSIZ kalmalı.

    ÖLÇÜLEN KUSUR: her terminal kol önce `sayac[X] += 1` yapıp SONRA commit
    ediyordu. Commit patlarsa dış `except` İKİNCİ bir terim ekliyordu; tek
    olay için `girdi=1, cikti=2` olup korunum assert'i patlıyor, zamanlayıcının
    genel `except`ine kaçıyor ve TÜM DÖNGÜ ölüyordu.

    Bu testin dondurduğu şey bir sayı değil, bir YAPI: `olaylari_isle` içinde
    `sayac`a TAM OLARAK BİR artış vardır ve artırılan kova bir FONKSİYON
    ÇAĞRISININ dönüş değeridir. Artış tekrar commit'ten önceye alınmak
    istenirse ikinci bir artış noktası açmak gerekir; bu test o an KIRMIZI olur.

    Kardeş dosya `test_field_stok_korunum_denklemi` küme eşitliği ölçer ve
    çift artışı GÖREMEZ: iki kez artan bir koşumda da kova kümesi aynıdır.
    """
    artislar = []
    for dugum in ast.walk(_fonksiyon("olaylari_isle")):
        if not isinstance(dugum, ast.AugAssign):
            continue
        hedef = dugum.target
        if (isinstance(hedef, ast.Subscript)
                and isinstance(hedef.value, ast.Name)
                and hedef.value.id == "sayac"):
            artislar.append(dugum)

    assert len(artislar) == 1, (
        "`olaylari_isle` içinde `sayac` artışı TEK OLMALI; bulunan "
        f"{len(artislar)}. Birden çok artış noktası, çift sayımın (commit "
        "patladığında iki terim birden artması) geri gelmesi demektir."
    )
    anahtar = artislar[0].target.slice
    assert isinstance(anahtar, ast.Call), (
        "Artırılan kova bir FONKSİYON ÇAĞRISININ dönüşü olmalı; bulunan "
        f"{ast.dump(anahtar)}. Sabit bir kova adı, kararın commit'ten ÖNCE "
        "verildiği anlamına gelir."
    )
    assert isinstance(anahtar.func, ast.Name) and anahtar.func.id in KOVA_DONDUREN, (
        f"Artırılan kova {KOVA_DONDUREN!r} içinden bir fonksiyondan gelmeli."
    )


def test_KOVA_DONDUREN_fonksiyonlar_sayaca_DOKUNMAZ() -> None:
    """Sayaç tek noktada artıyorsa, kova döndürenler sayacı hiç görmemeli."""
    for ad in KOVA_DONDUREN:
        for dugum in ast.walk(_fonksiyon(ad)):
            if isinstance(dugum, ast.Name) and dugum.id == "sayac":
                raise AssertionError(
                    f"{ad} `sayac`a dokunuyor. Kova döndüren bir fonksiyon "
                    "sayacı artırırsa tek-artış-noktası güvencesi düşer ve "
                    "çift sayım geri gelebilir."
                )


def test_IKI_KORUNUM_denklemi_AYNI_ve_TAM_kume() -> None:
    """Firma başına ve tüm firmalar denklemleri aynı kovaları saymalı."""
    kovalar = _kovalar()
    firma_basina = _denklem_terimleri("olaylari_isle")
    tum_firmalar = _denklem_terimleri("tum_firmalari_isle")

    assert firma_basina == tum_firmalar, (
        "KORUNUM DENKLEMLERİ AYRIŞMIŞ. Firma başına sayılıp tüm firmalarda "
        f"sayılmayan kova(lar): {sorted(firma_basina - tum_firmalar)!r}; "
        f"tersi: {sorted(tum_firmalar - firma_basina)!r}. Ayrışma, tek bir "
        "gerçek talep yarışında `tum_firmalari_isle` assert'ini patlatır ve "
        "zamanlayıcının genel except'ine kaçar."
    )
    assert tum_firmalar == kovalar, (
        "KORUNUM DENKLEMİ TÜM KOVALARI KAPSAMIYOR. Sayaçta olup denklemde "
        f"olmayan: {sorted(kovalar - tum_firmalar)!r}; denklemde olup "
        f"sayaçta olmayan: {sorted(tum_firmalar - kovalar)!r}. Hiç sayılmamış "
        "bir terim yüzünden tutan invaryant, invaryant değildir."
    )
