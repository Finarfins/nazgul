"""Faaliyet okuma yolu kapısı: `field_activities` TEK kapıdan okunur.

Konu: mobil-erp#24 (Gerçek Maliyet), FAZ 3 öncesi koruma.

--- NİYE STATİK BİR KAPI GEREKİYOR ------------------------------------------

``_satir`` ``SELECT *`` yapıyor. `field_activities` satırını o yardımcıyla okuyan
HER yol dört oran/maliyet sütununu (``labor_hourly_rate``,
``machine_hourly_rate`` ve onlardan türeyen ``labor_cost``/``machine_cost``)
kendiliğinden taşır.

Bugün bütün okuma yolları maskeli, ama bunu sağlayan tek şey her çağıranın
``_faaliyet_yaniti``yi çağırmayı hatırlaması — yani disiplin. FAZ 3 tam olarak
YENİ OKUMA YOLLARI açacak (parsel zaman çizelgesi, pano). Kapı o fazdan ÖNCE
kurulmazsa ilk unutulan çağrı maskeyi sessizce deler ve `depo`/`satis` rolleri
donmuş oranı görür.

--- İKİ KAPI, İKİ AYRI DELİK -------------------------------------------------

Deliklerin biçimi iki türlü ve tek bir tarama ikisini birden göremiyor:

1. **``SELECT *`` yolu.** ``_satir`` tablo adını f-string ile araya koyuyor
   (``SELECT * FROM {tablo}``), yani SQL metninde ne tablo adı ne sütun adı
   görünüyor. Bunu ancak ÇAĞRI YERİNDEN yakalayabiliriz:
   ``test_field_activities_is_only_read_through_the_single_helper``.

2. **Açık sütun listesi yolu.** Liste ucu sütunları tek tek sayıyor, yani
   tablo ve sütun adları SQL metninde GÖRÜNÜYOR. Bunu metinden yakalıyoruz:
   ``test_every_query_selecting_masked_columns_normalizes_its_rows``.

--- İKİNCİ KAPININ ÖLÇTÜĞÜ ŞEYİN SINIRI (dürüst kayıt) ----------------------

İkinci kapı, maskelenen sütunları seçen bir sorgunun bulunduğu FONKSİYONUN
``_faaliyet_yaniti``/``_faaliyet_satiri`` çağırdığını doğrular. **Satırların
gerçekten o çağrıdan geçtiğini kanıtlamaz**: bir fonksiyon iki sorgu çalıştırıp
birini normalize edip diğerini ham döndürebilir ve bu kapı bunu göremez.

Bunu veri akışı analizi (taint tracking) ile ölçmek gerekirdi; bu depoda öyle
bir altyapı yok ve tek bir uç için kurmak, kapının kendisinden daha büyük ve
daha kırılgan bir makine olurdu. Kapı bu hâliyle **"maskelenen sütunları seçtin
ama normalleştiriciyi hiç çağırmadın"** hatasını yakalar — pratikte unutmanın
aldığı biçim budur. Daha fazlasını iddia etmiyor.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FARM = BACKEND / "app" / "routers" / "farm.py"

# Tek güvenli kapı. `_satir` ile `field_activities` okumaya YALNIZ burada izin
# var; kural bu ismin kendisine bağlı, çünkü koruma "oku ve çevir"in
# ayrılmamasından geliyor.
TEK_KAPI = "_faaliyet_satiri"
NORMALLESTIRICILER = {"_faaliyet_yaniti", TEK_KAPI}

# Maske bu dört alanı çıkarıyor; ikisi sütun, ikisi onlardan türetiliyor.
# Sorgu metninde ancak SÜTUN olanlar görünebilir.
MASKELI_SUTUNLAR = ("labor_hourly_rate", "machine_hourly_rate")


def _agac() -> ast.Module:
    return ast.parse(FARM.read_text(encoding="utf-8"), filename=str(FARM))


def _fonksiyon_haritasi(agac: ast.Module) -> dict[ast.AST, str]:
    """Her düğüm için EN YAKIN kapsayan fonksiyonun adı."""
    harita: dict[ast.AST, str] = {}

    def gez(dugum: ast.AST, ad: str) -> None:
        for cocuk in ast.iter_child_nodes(dugum):
            yeni_ad = (
                cocuk.name
                if isinstance(cocuk, (ast.FunctionDef, ast.AsyncFunctionDef))
                else ad
            )
            harita[cocuk] = yeni_ad
            gez(cocuk, yeni_ad)

    gez(agac, "<modül>")
    return harita


def _cagrilan_adlar(fonksiyon: ast.AST) -> set[str]:
    adlar: set[str] = set()
    for dugum in ast.walk(fonksiyon):
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name):
            adlar.add(dugum.func.id)
    return adlar


def _fonksiyonlar(agac: ast.Module) -> dict[str, ast.AST]:
    return {
        d.name: d
        for d in ast.walk(agac)
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _metin_sql(dugum: ast.AST) -> str:
    """``text(...)`` argümanının SABİT parçalarını birleştirir.

    f-string'in interpole edilen parçaları atlanıyor; bizi ilgilendiren tablo ve
    sütun adları her hâlükârda sabit parçada duruyor.
    """
    parcalar: list[str] = []
    for alt in ast.walk(dugum):
        if isinstance(alt, ast.Constant) and isinstance(alt.value, str):
            parcalar.append(alt.value)
    return " ".join(" ".join(parcalar).split()).lower()


def test_single_helper_exists_and_reads_through_satir() -> None:
    """Kapı bir isme dayanıyor; o isim kaybolursa kapı sessizce boşa düşerdi."""
    fonksiyonlar = _fonksiyonlar(_agac())
    assert TEK_KAPI in fonksiyonlar, (
        f"{TEK_KAPI} yok; okuma yolu kapısının dayandığı tek kapı kaldırılmış."
    )
    govde = _cagrilan_adlar(fonksiyonlar[TEK_KAPI])
    assert "_satir" in govde and "_faaliyet_yaniti" in govde, (
        f"{TEK_KAPI} hem okumalı (_satir) hem çevirmeli (_faaliyet_yaniti); "
        f"bulunan çağrılar: {sorted(govde)}"
    )


def _modul_duzeyi_sozlukler(agac: ast.Module) -> dict[str, set[str]]:
    """Modül düzeyindeki sabit ``{str: str}`` sözlüklerinin OLASI DEĞERLERİ."""
    sozlukler: dict[str, set[str]] = {}
    for dugum in agac.body:
        if not (isinstance(dugum, ast.Assign) and isinstance(dugum.value, ast.Dict)):
            continue
        if len(dugum.targets) != 1 or not isinstance(dugum.targets[0], ast.Name):
            continue
        degerler = dugum.value.values
        if not degerler or not all(
            isinstance(d, ast.Constant) and isinstance(d.value, str) for d in degerler
        ):
            continue
        sozlukler[dugum.targets[0].id] = {d.value for d in degerler}
    return sozlukler


def _olasi_tablolar(
    tablo: ast.AST, sozlukler: dict[str, set[str]],
) -> tuple[set[str], bool]:
    """``_satir``ın tablo argümanının olası değerleri. İkinci dönüş: çözülebildi mi.

    Üç durum var ve ÜÇÜNCÜSÜ İHLALDİR:

    * sabit metin → tek değer;
    * modül düzeyindeki sabit bir sözlüğün indekslenmesi (``_KUYRUK_TABLOSU
      [kind]`` gibi) → o sözlüğün bütün değerleri;
    * başka her şey → ÖLÇEMİYORUZ.

    Ölçemediğini sessizce geçiren şey kapı değildir: bu kapının ilk hâli tam da
    böyleydi ve `_tekrar_mi`nin dolaylı `field_activities` okumasını görmüyordu,
    üstelik testin adı "field_activities okumaları" diye daha güçlü bir iddia
    taşıyordu.
    """
    if isinstance(tablo, ast.Constant) and isinstance(tablo.value, str):
        return {tablo.value}, True
    if isinstance(tablo, ast.Subscript) and isinstance(tablo.value, ast.Name):
        ad = tablo.value.id
        if ad in sozlukler:
            return set(sozlukler[ad]), True
    return set(), False


def test_field_activities_is_only_read_through_the_single_helper() -> None:
    """``_satir`` ile `field_activities` okumak yalnız tek kapının içinde olabilir.

    ``SELECT *`` yüzünden bu çağrı dört oran/maliyet alanını da getirir; kapının
    dışında kullanılması maskesiz bir okuma yolu açmak demektir.

    Kapı FAIL-CLOSED: tablo argümanı çözülemiyorsa "sızıntı yok" varsayılmaz,
    ihlal sayılır.
    """
    agac = _agac()
    harita = _fonksiyon_haritasi(agac)
    sozlukler = _modul_duzeyi_sozlukler(agac)

    kacaklar: list[str] = []
    olculemeyen: list[str] = []
    incelenen = 0
    for dugum in ast.walk(agac):
        if not (isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)):
            continue
        if dugum.func.id != "_satir" or len(dugum.args) < 3:
            continue
        incelenen += 1
        kapsayan = harita.get(dugum, "<modül>")
        olasi, cozuldu = _olasi_tablolar(dugum.args[2], sozlukler)
        if not cozuldu:
            olculemeyen.append(
                f"{kapsayan} (satır {dugum.lineno}): {ast.unparse(dugum.args[2])}"
            )
            continue
        if "field_activities" in olasi and kapsayan != TEK_KAPI:
            kacaklar.append(
                f"{kapsayan} (satır {dugum.lineno}, olası tablolar {sorted(olasi)})"
            )

    assert incelenen > 5, f"tarayıcı `_satir` çağrılarını bulamadı: {incelenen}"
    assert not olculemeyen, (
        "`_satir`ın tablo argümanı STATİK OLARAK ÇÖZÜLEMİYOR, yani bu çağrının "
        "`field_activities` okuyup okumadığı bilinemiyor. Kapı ölçemediğini "
        f"geçirmez: {olculemeyen}. Sabit bir tablo adı kullanın ya da faaliyet "
        f"okumasını {TEK_KAPI} üzerine taşıyın."
    )
    assert not kacaklar, (
        "`field_activities` TEK KAPIDAN okunmalı. `_satir` ile doğrudan okuyan "
        "yollar maskeyi atlar ve donmuş oranı `finance` izni olmayan role "
        f"sızdırır. Kapı dışı okumalar: {kacaklar}. "
        f"Bu yolları {TEK_KAPI}(db, cid, activity_id, request) üzerine taşıyın."
    )


def test_queue_table_map_cannot_reach_field_activities() -> None:
    """Kuyruk haritası `field_activities`e ULAŞAMAMALI.

    Ulaşsaydı `_satir(db, cid, _KUYRUK_TABLOSU[kind], ...)` dolaylı bir faaliyet
    okuması olurdu. Yukarıdaki kapı bunu artık yakalıyor; bu iddia ise niyeti
    ayrıca kaydediyor, çünkü haritaya bir anahtar eklemek tek satırlık ve masum
    görünen bir değişiklik.
    """
    sozlukler = _modul_duzeyi_sozlukler(_agac())
    assert "_KUYRUK_TABLOSU" in sozlukler, sorted(sozlukler)
    assert "field_activities" not in sozlukler["_KUYRUK_TABLOSU"], (
        "Faaliyet kuyruk haritasına geri konmuş; tek kapı kuralı delinir. "
        f"Harita: {sorted(sozlukler['_KUYRUK_TABLOSU'])}"
    )


def test_every_query_selecting_masked_columns_normalizes_its_rows() -> None:
    """Maskelenen sütunları SEÇEN her sorgu, normalleştiriciyi de çağırmalı.

    Sınırı modül başlığında yazılı: bu kapı fonksiyon düzeyinde ölçer, veri
    akışını izlemez.
    """
    agac = _agac()
    harita = _fonksiyon_haritasi(agac)
    fonksiyonlar = _fonksiyonlar(agac)

    olculen: list[str] = []
    ihlaller: list[str] = []
    for dugum in ast.walk(agac):
        if not (isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)):
            continue
        if dugum.func.id not in {"text", "tenant_text"} or not dugum.args:
            continue
        sql = _metin_sql(dugum.args[0])
        if "select" not in sql or "field_activities" not in sql:
            continue
        if not any(sutun in sql for sutun in MASKELI_SUTUNLAR):
            continue
        kapsayan = harita.get(dugum, "<modül>")
        olculen.append(f"{kapsayan}:{dugum.lineno}")
        govde = fonksiyonlar.get(kapsayan)
        if govde is None or not (_cagrilan_adlar(govde) & NORMALLESTIRICILER):
            ihlaller.append(f"{kapsayan} (satır {dugum.lineno})")

    assert olculen, (
        "Tarayıcı maskelenen sütunları seçen HİÇBİR sorgu bulamadı — kapı "
        "vakumda geçiyor olabilir (sorgu yeniden yazıldı ya da tarayıcı bozuldu)."
    )
    assert not ihlaller, (
        "Maskelenen oran sütunlarını seçen sorgular, satırlarını "
        f"{sorted(NORMALLESTIRICILER)} üzerinden geçirmeli; aksi hâlde ham "
        f"NUMERIC değerler maskesiz döner. İhlal: {ihlaller}"
    )


# --------------------------------------------------------------------------
# İÇ MANTIK MASKEYE BAĞLANMAMALI
# --------------------------------------------------------------------------
#
# Tek kapı kuralı iç doğrulama okumalarını da kapsıyor (gerekçe
# `_faaliyet_satiri` başlığında). Bunun bir yan etkisi var: iç iş mantığı artık
# bir SUNUM kararına bağlı. `_faaliyet_yaniti` maskeyi `request`e göre
# uyguluyor, yani iç çağıran `finance` izni OLMAYAN bir kullanıcının isteğiyle
# geldiğinde dönen sözlükte dört alan YOK.
#
# Bugün zararsız: iç çağıranlar yalnız `activity_type` ve `season_id` okuyor,
# ikisi de maskenin dışında. Ama biri `labor_cost` okumaya başlarsa o mantık
# `depo` kullanıcısında "alan yok", `yonetici`de değer görür — ROLE BAĞLI ve
# teşhisi zor bir iş mantığı hatası.
#
# Kapı bunu şimdiden yakalıyor. Kırıldığı gün verilecek karar: o alanı iç
# mantıkta okuyacaksan MASKESİZ okumalısın (iç okuma yolunu maskeden ayır).

# Yanıt döndüren çağıranlar: maskeli sözlük zaten istenen şey.
# `_kuyruk_satiri` de buraya girer — çevrimdışı kuyruğun tekrar-gönderim
# cevabını üretiyor, yani satır doğrudan istemciye gidiyor.
YANIT_CAGIRANLARI = {"create_activity", "get_activity", "_kuyruk_satiri"}
# Yanıt döndürmeyen çağıranlar: satırı yalnız doğrulama için okuyorlar.
IC_CAGIRANLAR = {"add_activity_input", "_gorev_baglantilari"}

# Maskenin çıkardığı DÖRT alanın tamamı (ikisi sütun, ikisi türetilen).
MASKELI_ALANLAR = (
    "labor_hourly_rate", "machine_hourly_rate", "labor_cost", "machine_cost",
)


def _ebeveyn_haritasi(agac: ast.Module) -> dict[ast.AST, ast.AST]:
    ebeveyn: dict[ast.AST, ast.AST] = {}
    for dugum in ast.walk(agac):
        for cocuk in ast.iter_child_nodes(dugum):
            ebeveyn[cocuk] = dugum
    return ebeveyn


def _izlenen_okumalar(
    agac: ast.Module, fonksiyon: ast.AST, ad: str, derinlik: int = 0,
) -> tuple[set[str], list[str]]:
    """``ad`` ismine bağlı sözlükten OKUNAN alan adlarını toplar.

    Sözlük başka bir modül fonksiyonuna argüman olarak geçerse oraya İNİLİR
    (parametre adına yeniden bağlanarak). İzlenemeyen bir kullanım görülürse
    ``izlenemeyen`` listesine yazılır ve kapı FAIL-CLOSED davranır — bu deponun
    `test_core_query_inventory`de kurduğu desenin aynısı.

    SINIR: takma ad (``b = a``), bir kapsayıcıya konma (``[satir]``), ``**``
    açılımı ve modül dışı fonksiyona geçme izlenmiyor; hepsi izlenemeyen sayılıp
    kapıyı kırar, sessizce geçmez.
    """
    okunan: set[str] = set()
    izlenemeyen: list[str] = []
    if derinlik > 3:
        izlenemeyen.append(f"{getattr(fonksiyon, 'name', '?')}: derinlik sınırı")
        return okunan, izlenemeyen

    ebeveyn = _ebeveyn_haritasi(agac)
    fonksiyonlar = _fonksiyonlar(agac)

    for dugum in ast.walk(fonksiyon):
        if not (isinstance(dugum, ast.Name) and dugum.id == ad):
            continue
        if not isinstance(dugum.ctx, ast.Load):
            continue
        ust = ebeveyn.get(dugum)

        # satir["alan"]
        if isinstance(ust, ast.Subscript):
            dilim = ust.slice
            if isinstance(dilim, ast.Constant) and isinstance(dilim.value, str):
                okunan.add(dilim.value)
                continue
            izlenemeyen.append(f"{fonksiyon.name}:{dugum.lineno} sabit olmayan indeks")
            continue

        # satir.get("alan")
        if isinstance(ust, ast.Attribute):
            cagri = ebeveyn.get(ust)
            if (
                ust.attr == "get"
                and isinstance(cagri, ast.Call)
                and cagri.args
                and isinstance(cagri.args[0], ast.Constant)
                and isinstance(cagri.args[0].value, str)
            ):
                okunan.add(cagri.args[0].value)
                continue
            izlenemeyen.append(f"{fonksiyon.name}:{dugum.lineno} .{ust.attr}")
            continue

        # baska_fonksiyon(..., satir, ...) -> parametre adına bağlanıp inilir
        if isinstance(ust, ast.Call) and dugum in ust.args:
            if not isinstance(ust.func, ast.Name) or ust.func.id not in fonksiyonlar:
                izlenemeyen.append(
                    f"{fonksiyon.name}:{dugum.lineno} modül dışı/çözülemeyen çağrı"
                )
                continue
            hedef = fonksiyonlar[ust.func.id]
            sira = ust.args.index(dugum)
            parametreler = [a.arg for a in hedef.args.args]
            if sira >= len(parametreler):
                izlenemeyen.append(f"{fonksiyon.name}:{dugum.lineno} parametre eşleşmedi")
                continue
            alt_okunan, alt_izlenemeyen = _izlenen_okumalar(
                agac, hedef, parametreler[sira], derinlik + 1
            )
            okunan |= alt_okunan
            izlenemeyen += alt_izlenemeyen
            continue

        izlenemeyen.append(f"{fonksiyon.name}:{dugum.lineno} izlenemeyen kullanım")

    return okunan, izlenemeyen


def test_single_helper_caller_inventory_is_exact() -> None:
    """Yeni bir çağıran eklendiğinde hangi sınıfa girdiği YENİDEN karara bağlansın."""
    agac = _agac()
    harita = _fonksiyon_haritasi(agac)
    cagiranlar = {
        harita.get(d, "<modül>")
        for d in ast.walk(agac)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
        and d.func.id == TEK_KAPI
    }
    assert cagiranlar == YANIT_CAGIRANLARI | IC_CAGIRANLAR, (
        f"{TEK_KAPI} çağıran fonksiyonlar envanteri değişti: {sorted(cagiranlar)}. "
        "Yeni çağıran yanıt mı döndürüyor yoksa yalnız doğrulama mı yapıyor — "
        "envantere ekleyip iç mantık kuralını yeniden gözden geçirin."
    )


def test_internal_callers_never_read_a_masked_field() -> None:
    """İç iş mantığı maskelenen dört alandan HİÇBİRİNİ okumamalı.

    Okusaydı davranış çağıranın rolüne göre değişirdi: `depo`da alan yok,
    `yonetici`de değer var.
    """
    agac = _agac()
    fonksiyonlar = _fonksiyonlar(agac)

    ihlaller: list[str] = []
    izlenemeyen_tum: list[str] = []
    olculen: list[str] = []
    for ad in sorted(IC_CAGIRANLAR):
        govde = fonksiyonlar[ad]
        for dugum in ast.walk(govde):
            if not (
                isinstance(dugum, ast.Assign)
                and isinstance(dugum.value, ast.Call)
                and isinstance(dugum.value.func, ast.Name)
                and dugum.value.func.id == TEK_KAPI
                and len(dugum.targets) == 1
                and isinstance(dugum.targets[0], ast.Name)
            ):
                continue
            izlenen = dugum.targets[0].id
            okunan, izlenemeyen = _izlenen_okumalar(agac, govde, izlenen)
            olculen.append(f"{ad}.{izlenen} -> {sorted(okunan)}")
            izlenemeyen_tum += izlenemeyen
            for alan in sorted(okunan & set(MASKELI_ALANLAR)):
                ihlaller.append(f"{ad} `{alan}` okuyor")

    assert olculen, (
        "Hiçbir iç çağıranda izlenecek atama bulunamadı — kapı vakumda geçiyor."
    )
    assert not izlenemeyen_tum, (
        "Faaliyet satırının izlenemeyen bir kullanımı var; kapı bu hâliyle "
        f"maskelenen alanın okunmadığını KANITLAYAMAZ: {izlenemeyen_tum}"
    )
    assert not ihlaller, (
        "İÇ İŞ MANTIĞI MASKELENEN ALANI OKUYOR: "
        f"{ihlaller}. Bu alanlar `finance` izni olmayan çağıranın isteğinde "
        "sözlükte HİÇ BULUNMAZ, yani mantık role göre farklı davranır. "
        "Bu alanı iç mantıkta okuyacaksanız MASKESİZ okumanız gerekir: iç okuma "
        "yolunu maskeden ayırın (ham satır iç mantığa, maske yalnız yanıt "
        f"sınırında). Ölçülen okumalar: {olculen}"
    )


def test_internal_read_tracker_follows_a_row_into_a_helper() -> None:
    """İzleyici tek sıçramayı gerçekten takip ediyor mu — kapının kendi testi.

    Takip etmeseydi `_ilac_girdisi_dogrula` içindeki okuma görünmez olurdu ve
    kapı sığ kalırdı; bu iddia onu çiviliyor.
    """
    agac = _agac()
    fonksiyonlar = _fonksiyonlar(agac)
    okunan, izlenemeyen = _izlenen_okumalar(
        agac, fonksiyonlar["add_activity_input"], "faaliyet"
    )
    assert not izlenemeyen, izlenemeyen
    # `activity_type` yalnız `_ilac_girdisi_dogrula` İÇİNDE okunuyor.
    assert "activity_type" in okunan, okunan


def test_gate_actually_scans_the_module() -> None:
    """Tarayıcı bozulup sessizce sıfır bulgu üretirse kapı yalancı yeşil olur."""
    agac = _agac()
    fonksiyonlar = _fonksiyonlar(agac)
    assert len(fonksiyonlar) > 40, len(fonksiyonlar)
    satir_cagrilari = [
        d for d in ast.walk(agac)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
        and d.func.id == "_satir"
    ]
    assert len(satir_cagrilari) > 5, len(satir_cagrilari)


def test_masked_field_inventory_matches_the_router() -> None:
    """Kapının sütun listesi üretimdeki maske listesinden sapmamalı.

    Sapsaydı yeni bir maskeli sütun eklendiğinde ikinci kapı onu görmez ve
    sessizce dar kalırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _FINANSAL_ALANLAR

    assert set(MASKELI_SUTUNLAR) <= set(_FINANSAL_ALANLAR), (
        MASKELI_SUTUNLAR, _FINANSAL_ALANLAR,
    )
    # Türetilen alanların sütunu yok; sorgu metninde geçemezler.
    turetilen = set(_FINANSAL_ALANLAR) - set(MASKELI_SUTUNLAR)
    assert turetilen == {"labor_cost", "machine_cost"}, turetilen
