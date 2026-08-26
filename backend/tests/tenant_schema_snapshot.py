"""Şema yapısının SAYIMLA alınan izi — migration yeniden kurulumları için.

Neden ad karşılaştırmak yetmez
------------------------------

Dilim 3 bu dosyayı doğuran hatayı taşıyordu: yeniden kurulumun indeksleri
koruyup korumadığı, indeks ADLARI karşılaştırılarak ölçülüyordu. Ad aynı
kalıp sütun sırası değişseydi kapı yeşil kalırdı —
``ix_stock_transfer_items_product`` ``(product_id, transfer_id)`` yerine
``(transfer_id, product_id)`` olarak yeniden kurulsa fark edilmezdi ve kayıp
yalnız üretimde, yanlış indeks seçilen bir sorguda görünürdü.

Burada karşılaştırılan şey SÜTUN DEMETİ ve TEKİLLİK. Ad da ayrıca
karşılaştırılıyor, çünkü iki farklı kayıp mümkün: aynı ad/farklı sütunlar
(dilim 3'ün açığı) ve farklı ad/aynı sütunlar (adına referans veren kodu
kırar).

BEKLENTİ DEĞİL, SAYIM
---------------------

İz, ``inspect()``in gösterdiği HER ŞEYİ topluyor; "şunun orada olmasını
bekliyorum" listesi değil. Aradaki fark önemli: beklenti yalnız zaten
bilineni doğrular, sayım kimsenin hatırlamadığı bir kısıtı da yakalar.
Örnek: ``stock_transfers`` bir adlı CHECK taşıyor ama ``sales_orders`` ve
``delivery_notes`` hiç CHECK taşımıyor. Elle yazılmış bir "CHECK'i doğrula"
iddiası ikincilerde sessizce vakuma düşerdi; sayım tabanlı karşılaştırmada
"hiç yoktu, hiç yok" doğru şekilde korunmuş sayılır.

AŞAMA BAŞINA BEYAN EDİLEN EKLEMELER — desenin parçası, dilim 2'ye özgü değil
---------------------------------------------------------------------------

Karşılaştırma **TAM EŞİTLİK**, alt küme değil:

* değişiklikten ÖNCE: çıplak tabana karşı tam eşitlik,
* değişiklikten SONRA (her aşamada): taban + migration'ın BEYAN ETTİĞİ
  eklemeler,
* beyan edilmemiş HER ŞEY — eksik de fazla da — hatadır.

Alt küme kontrolünün neden yetmediği ÖLÇÜLDÜ, varsayılmadı: migration yukarı
çıkarken FAZLADAN bir indeks yaratırsa, o indeks "önceki" anlık görüntüye de
işlenir (``import app.main`` zinciri zaten koşturmuştur ve downgrade
tanımadığı bir indeksi düşürmez). Önce ile sonra birbirine uyar ve "beyan
edilmemiş ekleme" kapısı hiçbir şey göremez. Sessizce fazladan indeks yaratan
bir mutasyon, alt küme kontrolünden YEŞİL geçti; tam eşitliğe geçince
kırmızıya döndü.

BEYAN DİYALEKTE BAĞLI OLMAK ZORUNDA — ölçülmüş fark
---------------------------------------------------

PostgreSQL'de bir ``UNIQUE`` KISITI arkasında bir indeks yaratır ve
``get_indexes()`` onu gösterir; SQLite'ta göstermez — kısıt yalnız
``get_unique_constraints()`` içinde görünür. Bu yüzden "ebeveyne UNIQUE
eklendi" beyanı PostgreSQL'de bir indeks eklemesini de içermek, SQLite'ta
içermemek zorundadır. Tam eşitliğe geçildiği anda bu fark, GERÇEK bir
ekleme üzerinde kapıyı kırmızıya çevirerek kendini gösterdi.

Yeni dilimler bunu yeniden keşfetmek zorunda değil: kendi eklemelerini aşama
başına beyan etsinler, diyalekt farkını da beyanın içine koysunlar.

SINIR — bu iz BAĞIMSIZ BİR PARMAK İZİ DEĞİLDİR
----------------------------------------------

İz yalnız ``inspect()``in İKİ diyalektte birden gösterebildiğini görür.
Bilinçli olarak KAPSAM DIŞI: collation, depolama parametreleri (fillfactor
vb.), indeks yöntemi/opclass, kısmi indeks yüklemi ve tetikleyiciler. Bunları
sabitlemek, iki diyalektte de koşmak zorunda olan bir kapıya yalnız
PostgreSQL'de var olan ayrıntıyı yazmak olurdu. Yani bu iz "şema hiç
değişmedi" demiyor; "sayabildiklerim beyan edilenler dışında değişmedi"
diyor.
"""
from __future__ import annotations


#: ``inspect()`` üzerinden İKİ diyalektte de okunabilen ve bu iz tarafından
#: SABİTLENEN özellikler. Rapor bu listeyi olduğu gibi aktarır.
SABITLENEN = (
    "birincil anahtar sütun listesi",
    "kimlik üretimi taşıyan sütunlar — YALNIZ PostgreSQL'de yapısal olarak "
    "görünür (bkz. SABITLENMEYEN); SQLite'ta davranışsal olarak ölçülür",
    "indeksler: SÜTUN DEMETİ + tekillik (ad değil)",
    "indeks adları (ayrı küme olarak)",
    "tekillik kısıtları: sütun demeti",
    "yabancı anahtarlar: (bağlı sütunlar, hedef tablo, hedef sütunlar)",
    "adlı CHECK kısıtları",
    "sütunlar: ad -> (tip, nullable)",
)

#: Bilinçli olarak SABİTLENMEYENLER ve gerekçesi.
SABITLENMEYEN = (
    "collation — yalnız PostgreSQL'de anlamlı",
    "depolama parametreleri (fillfactor vb.) — dialect'e özgü",
    "indeks yöntemi / opclass — SQLite'ta karşılığı yok",
    "kısmi indeks yüklemi — SQLite yansıtması güvenilir değil",
    "tetikleyiciler — inspect() iki diyalektte tutarlı göstermiyor",
    "SQLite'ta kimlik üretiminin YAPISAL tespiti — ölçüldü: SQLite inspect() "
    "bu tabloların hepsinde autoincrement=None, default=None döndürüyor, oysa "
    "rowid takma adı olarak üretim ÇALIŞIYOR. Yani yapısal iz SQLite'ta bu "
    "alanı boş görür ve 'boştu, boş kaldı' diye korur. Gerçek koruma "
    "davranışsaldır: açık id VERMEDEN INSERT (kimlik_uretimi_dogrula), ve o "
    "her iki diyalektte de koşuyor.",
)


def _kimlik_sutunlari(denetci, tablo: str) -> frozenset[str]:
    """Kendiliğinden değer üreten sütunlar.

    PostgreSQL'de dizi destekli sütun ``nextval(...)`` varsayılanıyla,
    IDENTITY sütunu ``identity`` sözlüğüyle görünür; SQLite'ta
    ``INTEGER PRIMARY KEY`` rowid takma adıdır ve ``autoincrement`` ile
    gelir. Üçü de aynı şeyi ifade ediyor: açık ``id`` vermeden INSERT çalışır.
    """
    uretilen = set()
    for sutun in denetci.get_columns(tablo):
        varsayilan = str(sutun.get("default") or "")
        if (
            varsayilan.startswith("nextval")
            or sutun.get("identity")
            or sutun.get("autoincrement") is True
        ):
            uretilen.add(sutun["name"])
    return frozenset(uretilen)


def yapisal_iz(denetci, tablo: str) -> dict:
    """Tablonun SAYILAN yapısal izi. Bkz. modül başlığı: sınırları var."""
    return {
        "pk": tuple(denetci.get_pk_constraint(tablo)["constrained_columns"]),
        "kimlik": _kimlik_sutunlari(denetci, tablo),
        # Ad DEĞİL, sütun demeti + tekillik. Dilim 3'ün açığı buradaydı.
        "indeks_sutunlari": frozenset(
            (tuple(i["column_names"]), bool(i["unique"]))
            for i in denetci.get_indexes(tablo)
        ),
        "indeks_adlari": frozenset(i["name"] for i in denetci.get_indexes(tablo)),
        "tekillik": frozenset(
            tuple(u["column_names"]) for u in denetci.get_unique_constraints(tablo)
        ),
        "yabanci_anahtar": frozenset(
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in denetci.get_foreign_keys(tablo)
        ),
        "check": frozenset(c["name"] for c in denetci.get_check_constraints(tablo)),
        "sutunlar": {
            c["name"]: (str(c["type"]), bool(c["nullable"]))
            for c in denetci.get_columns(tablo)
        },
    }


def capa_dogrula(denetci, tablo: str, taban_indeks: set, asama: str = "değişiklik öncesi",
                 ek_indeks: set | None = None) -> None:
    """BAĞIMSIZ ÇAPA: türetilmiş fixture gerçekten taban özellikleri taşıyor mu.

    Neden gerekli — ölçülmüş bir açık, teorik değil
    -----------------------------------------------

    Değişiklik öncesi şekil, test edilen migration'ın KENDİ downgrade'iyle
    üretiliyor. Migration YUKARI çıkarken bir şeyi bozarsa (``import
    app.main`` zinciri zaten koşturmuştur), hasar "önceki" anlık görüntüye de
    işlenir. Sonra "önce" ile "sonra" birbirine uyar ve iki yönlü
    karşılaştırma HİÇBİR ŞEY göremez — iki eşit derecede hasarlı fotoğrafı
    karşılaştırmış olur.

    Ölçüldü: ebeveyn indeksini yükseltme sırasında düşüren bir mutasyon,
    yalnız önce/sonra karşılaştırması yapan bir kapıdan YEŞİL geçti. Çapa
    eklendikten sonra kırmızıya döndü.

    Çapa, test edilen migration VAR OLMADAN ÖNCEKİ bir durumdan ölçülür; yani
    test edilen kod tarafından üretilemez. Her yeni dilim KENDİ çapasını,
    kendi migration'ı yazılmadan önce ölçmek zorundadır.
    """
    iz = yapisal_iz(denetci, tablo)
    # Değişiklik ÖNCESİ aşamada ``ek_indeks`` boştur ve karşılaştırma saf
    # tabana karşıdır. Sonraki aşamalarda migration'ın BEYAN ETTİĞİ eklemeler
    # tabana eklenir — beyan edilmemiş olan hâlâ hata.
    taban = set(taban_indeks) | set(ek_indeks or ())
    eksik = taban - iz["indeks_sutunlari"]
    fazla = iz["indeks_sutunlari"] - taban
    hatalar = []
    if eksik:
        hatalar.append(f"tabanın indeksi YOK: {sorted(eksik, key=str)}")
    if fazla:
        # Alt küme kontrolü YETMEZ ve bu da ölçüldü: migration yukarı çıkarken
        # FAZLADAN bir indeks yaratırsa, o indeks "önceki" görüntüye de işlenir
        # (downgrade tanımadığı indeksi düşürmez) ve "beyan edilmemiş ekleme"
        # kapısı hiçbir şey göremez. Karşılaştırma bu yüzden TAM EŞİTLİK.
        hatalar.append(f"tabanda OLMAYAN indeks var: {sorted(fazla, key=str)}")
    assert not hatalar, (
        f"FIXTURE KİRLİ ({asama}): {tablo} bağımsız tabanla uyuşmuyor — "
        + "; ".join(hatalar)
        + ". Migration yukarı çıkarken bozmuş olabilir; bu durumda 'önce' ile "
        f"'sonra' birbirine uyar ve karşılaştırma hiçbir şey göremez. Mevcut: "
        f"{sorted(iz['indeks_sutunlari'], key=str)}"
    )


def korunmus_mu(
    tablo: str,
    once: dict,
    sonra: dict,
    asama: str,
    *,
    eklenen_indeks_sutunlari: frozenset = frozenset(),
    eklenen_indeks_adlari: frozenset = frozenset(),
    eklenen_tekillik: frozenset = frozenset(),
    eklenen_yabanci_anahtar: frozenset = frozenset(),
    eklenen_sutunlar: dict | None = None,
) -> None:
    """``sonra`` == ``once`` + BEYAN EDİLEN eklemeler. Fazlası da hata.

    Yalnız "düşen bir şey var mı" diye bakmak yetmez: beyan edilmemiş bir
    EKLEME de sessiz bir değişikliktir ve yeniden kurulumun yan etkisi
    olabilir. O yüzden karşılaştırma iki yönlü.
    """
    eklenen_sutunlar = eklenen_sutunlar or {}
    hatalar: list[str] = []

    if once["pk"] != sonra["pk"]:
        hatalar.append(f"birincil anahtar {once['pk']} -> {sonra['pk']}")
    if once["kimlik"] != sonra["kimlik"]:
        hatalar.append(
            f"kimlik üreten sütunlar {sorted(once['kimlik'])} -> {sorted(sonra['kimlik'])}"
        )

    for alan, eklenen in (
        ("indeks_sutunlari", eklenen_indeks_sutunlari),
        ("indeks_adlari", eklenen_indeks_adlari),
        ("tekillik", eklenen_tekillik),
        ("yabanci_anahtar", eklenen_yabanci_anahtar),
        ("check", frozenset()),
    ):
        beklenen = once[alan] | eklenen
        if sonra[alan] != beklenen:
            dusen = sorted(beklenen - sonra[alan], key=str)
            fazla = sorted(sonra[alan] - beklenen, key=str)
            if dusen:
                hatalar.append(f"{alan}: DÜŞEN {dusen}")
            if fazla:
                hatalar.append(f"{alan}: BEYAN EDİLMEMİŞ EKLEME {fazla}")

    beklenen_sutunlar = dict(once["sutunlar"]) | eklenen_sutunlar
    if sonra["sutunlar"] != beklenen_sutunlar:
        for ad, deger in beklenen_sutunlar.items():
            if ad not in sonra["sutunlar"]:
                hatalar.append(f"sütun DÜŞTÜ: {ad}")
            elif sonra["sutunlar"][ad] != deger:
                hatalar.append(f"sütun DEĞİŞTİ: {ad} {deger} -> {sonra['sutunlar'][ad]}")
        for ad in sonra["sutunlar"]:
            if ad not in beklenen_sutunlar:
                hatalar.append(f"sütun BEYAN EDİLMEDEN EKLENDİ: {ad}")

    assert not hatalar, (
        f"{asama}: {tablo} yeniden kurulumu yapıyı bozdu:\n  " + "\n  ".join(hatalar)
    )
