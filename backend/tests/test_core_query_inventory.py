"""SQLAlchemy Core sorgu envanteri kapısı (yalnızca test/CI).

KAPSAM — DAR VE AÇIK
--------------------
Bu kapı **yalnız** `backend/app/` içindeki, doğrudan tanınabilir biçimde yazılmış
SQLAlchemy Core ``select(...)``, ``update(...)``, ``delete(...)`` çağrılarını
kapsar. Aşağıdakiler **kapsam dışıdır** ve bu kapı tarafından korunmaz:

* ``insert(...)`` çağrıları,
* ``text()`` / ``tenant_text()`` ham SQL (ayrı kapı: ``test_tenant_scoping_guard.py``),
* ORM ``Query`` API'si,
* sorgunun **çalışma zamanındaki** davranışı (bağlanan parametreler, gerçek
  yürütme, transaction sınırları).

Bu kapı bir sorgunun güvenli olduğunu **kanıtlamaz**; yalnız "beklenmedik veya
çözümlenemeyen Core sorgusu yok" der. Kiracı sınırı doğrulaması
``test_tenant_scoping_guard.py`` ve ``test_line_item_sql_gate.py`` işidir.

KİMLİK
------
Kimlik = ``(dosya, lexical bağlam, işlem, zincir AST parmak izi)``.

``lexical bağlam`` modül → sınıf(lar) → fonksiyon(lar) zinciridir: modül düzeyi
``""``, ``dogrula`` fonksiyonu ``"dogrula"``, ``A`` sınıfının ``m`` metodu
``"A.m"``, ``f`` içindeki iç fonksiyon ``g`` ``"f.g"``. İki farklı sınıftaki aynı
adlı metotlar ve iç içe fonksiyonlar böylece birbirinden ayrılır.

**Satır numarası kimliğin parçası değildir** (yalnız yorum olarak tutulur), fakat
bağlam kimliğe dahil olduğu için bir sorgunun aynı dosyada başka bir fonksiyona
taşınması yeni+eksik drift üretir — sessiz ikame mümkün değildir. Aynı fonksiyon
içindeki satır kayması, yorum ve biçim değişikliği drift üretmez.

Parmak izi ``ast.dump(..., include_attributes=False)`` ile zincirin **tamamından**
alınır: ``.where()`` eklenmesi/kaldırılması, AND↔OR, join hedefi/koşulu,
``order_by``/``group_by``/``having``/``values``/``returning``/``with_for_update``
ve iç içe subquery değişiklikleri parmak izini değiştirir.

Aynı bağlamda yapısal olarak birebir aynı sorgu birden çok geçebilir; kimlik
tekildir ve **adet** envantere yazılır. Adet değişimi de kapıyı kırar.

FAIL-CLOSED
-----------
Kusursuz statik çözümleme iddiası yoktur. Çözemediğimiz her şey güvensiz sayılır
ve ``unsupported`` olarak raporlanır:

``unresolved-target``
    hedef tablo ``arg0`` / ``.select_from()`` / zincirdeki tek tablo yoluyla
    çözülemiyor.
``method-form``
    ``<tablo>.select()/.update()/.delete()`` biçimi zincir olarak çözümlenmiyor.
``variable-arg``
    ``where``/``having``/``values``/``returning`` ve benzeri zincir metotlarına
    yapısı görülemeyen bir argüman veriliyor: tablo olmayan bir ``Name``,
    ``*liste`` açılımı veya ``**sözlük`` açılımı. Bu argümanın içeriği başka bir
    ifadede (ör. ``append``/``insert``/``extend``/``del``/yeniden atama ile)
    değiştirilebilir ve parmak izi bunu **görmez**.

    Kural yalnız **argümanın kendisi** opak olduğunda tetiklenir.
    ``where(tablo.c.company_id == cid)`` opak değildir: yüklemin yapısı (sütun,
    operatör) parmak izinde görünür, ``cid`` yalnız bağlanan bir değerdir. Bu
    ayrım olmasaydı her karşılaştırma muafiyet gerektirirdi ve kural
    kullanılamazdı. Sınır ``test_pozitif_karsilastirma_operandi_degisken_arguman_sayilmaz``
    ile sabitlenmiştir.
``rebound-op``
    Core işlem adı başka bir isme bağlanıp o isim üzerinden çağrılıyor
    (``q = select; q(t)``), alias zinciri (``r = q``) veya bir kapsayıcıya
    konulup indeksle çağrılıyor (``OPS["s"](t)``).
``shadowed-op``
    Core'dan içe aktarılan bir ad aynı modülde yerel olarak da tanımlanmış;
    hangisinin çağrıldığı statik olarak ayırt edilemiyor.
``parse-error``
    Kaynak ayrıştırılamıyor.

Ayrıca tarama sıfır sorgu bulursa veya envanter boşsa kapı kırılır — bozuk
tarayıcı sahte yeşil göremez.

MUAFİYETLER
-----------
``VARIABLE_ARG_ALLOWLIST`` dar ve gerekçelidir: anahtarı
``(dosya, bağlam, işlem, hedef tablo, parmak izi)`` beşlisi ve beklenen argüman
tanımlayıcı kümesidir. Genel isim muafiyeti veya dosya bazlı muafiyet yoktur.
Muafiyetli sorgu başka bir fonksiyona taşınırsa (bağlam değişir), zinciri
değişirse (parmak izi değişir) veya yeni bir değişken argüman eklenirse kapı
kırılır.

**Bu muafiyetin sınırı:** allowlist yalnız "bu ifadenin bu bağlamda değişken
argüman kullandığı bilinmektedir" der. Değişkenin **içeriğinin** değiştiğini
algılamaz ve algıladığı iddiasında değildir. Örneğin
``app/notifications/service.py`` içindeki ``claimable`` yükleminin ``or_(...)``
ile genişletilmesi bu kapı tarafından **yakalanmaz**. Bu, bilinen ve kabul
edilmiş bir sınırdır.

BİLİNEN SINIRLAR
----------------
1. ``sole-table`` çözümleme yolu bir sezgiseldir (zincirde tam olarak bir tablo
   geçiyorsa o kabul edilir). Zincire ikinci bir tablo girerse ``unresolved``
   olur ve kapı kırılır. Yol adı envantere yazıldığı için denetlenebilir.
2. Tablo kaydı yalnız modül düzeyinde ``X = Table("ad", ...)`` biçimini bilir.
3. Dekoratör ifadeleri, kapsayıcı fonksiyonun bağlamına atfedilir.
4. Yeniden bağlama çözümlemesi modül genelinde isim tabanlıdır; koşullu veya
   çalışma zamanında değişen bağlamaları izlemez — bu yüzden çağrı anında
   fail-closed davranır.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
from typing import NamedTuple

import pytest

CORE_OPS = frozenset({"select", "update", "delete"})
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"

#: Argüman taşıyan zincir metotları — yapısı görülemeyen argüman burada aranır.
ARG_TASIYAN_METOTLAR = frozenset(
    {
        "where",
        "having",
        "filter",
        "filter_by",
        "values",
        "returning",
        "order_by",
        "group_by",
        "join",
        "outerjoin",
        "distinct",
        "select_from",
        "with_for_update",
    }
)


class CoreQuery(NamedTuple):
    dosya: str
    baglam: str
    satir: int
    islem: str
    hedef: str | None
    yol: str
    parmak_izi: str


class Unsupported(NamedTuple):
    dosya: str
    baglam: str
    satir: int
    tur: str
    aciklama: str


# --------------------------------------------------------------------------
# AST yardımcıları
# --------------------------------------------------------------------------
def _parse(kaynak: str) -> ast.Module | None:
    try:
        return ast.parse(kaynak)
    except SyntaxError:
        return None


def _table_registry(kaynaklar: dict[str, str]) -> dict[str, str]:
    """Modül düzeyinde ``X = Table("ad", ...)`` ile tanımlanan değişkenler."""
    kayit: dict[str, str] = {}
    for kaynak in kaynaklar.values():
        agac = _parse(kaynak)
        if agac is None:
            continue
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Assign) or not isinstance(dugum.value, ast.Call):
                continue
            fn = dugum.value.func
            ad = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if ad != "Table":
                continue
            tablo_adi = None
            if dugum.value.args and isinstance(dugum.value.args[0], ast.Constant):
                deger = dugum.value.args[0].value
                if isinstance(deger, str):
                    tablo_adi = deger
            for hedef in dugum.targets:
                if isinstance(hedef, ast.Name):
                    kayit[hedef.id] = tablo_adi or hedef.id
    return kayit


def _sqlalchemy_op_names(agac: ast.Module) -> tuple[set[str], set[str]]:
    dogrudan: set[str] = set()
    modul_alias: set[str] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.ImportFrom):
            modul = dugum.module or ""
            if modul == "sqlalchemy" or modul.startswith("sqlalchemy."):
                for a in dugum.names:
                    if a.name in CORE_OPS:
                        dogrudan.add(a.asname or a.name)
        elif isinstance(dugum, ast.Import):
            for a in dugum.names:
                if a.name == "sqlalchemy" or a.name.startswith("sqlalchemy."):
                    modul_alias.add(a.asname or a.name.split(".")[0])
    return dogrudan, modul_alias


def _local_definitions(agac: ast.Module) -> set[str]:
    return {
        d.name
        for d in ast.walk(agac)
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _rebound_core_names(agac: ast.Module, core_adlari: set[str]) -> tuple[set[str], set[str]]:
    """(Core işlemine bağlanmış takma adlar, Core işlemi tutan kapsayıcılar).

    Sabit noktaya kadar yinelenir, böylece ``q = select; r = q`` zinciri de
    yakalanır. Yalnız **atama** görmek yetmez; kapı çağrı anında tetiklenir, bu
    yüzden kullanılmayan bir takma ad sahte drift üretmez.
    """
    alias: set[str] = set(core_adlari)
    kapsayici: set[str] = set()
    degisti = True
    while degisti:
        degisti = False
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Assign):
                continue
            hedefler = [t.id for t in dugum.targets if isinstance(t, ast.Name)]
            if not hedefler:
                continue
            deger = dugum.value
            if isinstance(deger, ast.Name) and deger.id in alias:
                for h in hedefler:
                    if h not in alias:
                        alias.add(h)
                        degisti = True
            elif isinstance(deger, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                if any(
                    isinstance(i, ast.Name) and i.id in alias for i in ast.walk(deger)
                ):
                    for h in hedefler:
                        if h not in kapsayici:
                            kapsayici.add(h)
                            degisti = True
    return alias - core_adlari, kapsayici


def _context_map(agac: ast.Module) -> dict[ast.AST, str]:
    """Her düğüm için ``modül → sınıf → fonksiyon`` lexical bağlamı."""
    harita: dict[ast.AST, str] = {agac: ""}

    def gez(dugum: ast.AST, yol: tuple[str, ...]) -> None:
        for cocuk in ast.iter_child_nodes(dugum):
            harita[cocuk] = ".".join(yol)
            if isinstance(cocuk, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                gez(cocuk, yol + (cocuk.name,))
            else:
                gez(cocuk, yol)

    gez(agac, ())
    return harita


def _parent_map(agac: ast.AST) -> dict[ast.AST, ast.AST]:
    ebeveyn: dict[ast.AST, ast.AST] = {}
    for dugum in ast.walk(agac):
        for cocuk in ast.iter_child_nodes(dugum):
            ebeveyn[cocuk] = dugum
    return ebeveyn


def _outermost_chain(dugum: ast.AST, ebeveyn: dict[ast.AST, ast.AST]) -> ast.AST:
    guncel = dugum
    while True:
        ust = ebeveyn.get(guncel)
        if isinstance(ust, ast.Attribute) and ust.value is guncel:
            buyuk = ebeveyn.get(ust)
            if isinstance(buyuk, ast.Call) and buyuk.func is ust:
                guncel = buyuk
                continue
        break
    return guncel


def _base_name(dugum: ast.AST) -> str | None:
    guncel = dugum
    while isinstance(guncel, (ast.Attribute, ast.Subscript)):
        guncel = guncel.value
    return guncel.id if isinstance(guncel, ast.Name) else None


def _fingerprint(dugum: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(dugum, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def _resolve_target(
    cagri: ast.Call, zincir: ast.AST, tablolar: dict[str, str]
) -> tuple[str | None, str]:
    if cagri.args:
        aday = _base_name(cagri.args[0])
        if aday in tablolar:
            return aday, "arg0"
    for dugum in ast.walk(zincir):
        if (
            isinstance(dugum, ast.Call)
            and getattr(dugum.func, "attr", None) == "select_from"
            and dugum.args
        ):
            aday = _base_name(dugum.args[0])
            if aday in tablolar:
                return aday, "select_from"
    gecenler = sorted(
        {d.id for d in ast.walk(zincir) if isinstance(d, ast.Name) and d.id in tablolar}
    )
    if len(gecenler) == 1:
        return gecenler[0], "sole-table"
    return None, "unresolved"


def _variable_args(zincir: ast.AST, tablolar: dict[str, str]) -> tuple[str, ...]:
    """Zincirde yapısı görülemeyen argümanların tanımlayıcıları."""
    bulunan: list[str] = []
    for dugum in ast.walk(zincir):
        if not (isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Attribute)):
            continue
        if dugum.func.attr not in ARG_TASIYAN_METOTLAR:
            continue
        for arg in dugum.args:
            if isinstance(arg, ast.Starred):
                bulunan.append(f"{dugum.func.attr}:*")
            elif isinstance(arg, ast.Name) and arg.id not in tablolar:
                bulunan.append(f"{dugum.func.attr}:name:{arg.id}")
        for kw in dugum.keywords:
            if kw.arg is None:
                bulunan.append(f"{dugum.func.attr}:**")
    return tuple(sorted(bulunan))


# --------------------------------------------------------------------------
# Tarayıcı
# --------------------------------------------------------------------------
def scan_sources(kaynaklar: dict[str, str]) -> tuple[list[CoreQuery], list[Unsupported]]:
    tablolar = _table_registry(kaynaklar)
    sorgular: list[CoreQuery] = []
    desteksiz: list[Unsupported] = []

    for dosya in sorted(kaynaklar):
        agac = _parse(kaynaklar[dosya])
        if agac is None:
            desteksiz.append(
                Unsupported(dosya, "", 0, "parse-error", "kaynak ayrıştırılamadı")
            )
            continue
        dogrudan, modul_alias = _sqlalchemy_op_names(agac)
        if not dogrudan and not modul_alias:
            continue

        golgelenen = dogrudan & _local_definitions(agac)
        if golgelenen:
            desteksiz.append(
                Unsupported(
                    dosya,
                    "",
                    0,
                    "shadowed-op",
                    f"Core adı yerel olarak da tanımlanmış: {sorted(golgelenen)}",
                )
            )

        ebeveyn = _parent_map(agac)
        baglamlar = _context_map(agac)
        yeniden_baglanan, kapsayicilar = _rebound_core_names(agac, dogrudan)

        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call):
                continue
            baglam = baglamlar.get(dugum, "")

            # Core işlem adının yeniden bağlanması
            if isinstance(dugum.func, ast.Name) and dugum.func.id in yeniden_baglanan:
                desteksiz.append(
                    Unsupported(
                        dosya,
                        baglam,
                        dugum.lineno,
                        "rebound-op",
                        f"Core işlemi '{dugum.func.id}' adına bağlanıp çağrılıyor",
                    )
                )
                continue
            if isinstance(dugum.func, ast.Subscript):
                taban = _base_name(dugum.func)
                if taban in kapsayicilar:
                    desteksiz.append(
                        Unsupported(
                            dosya,
                            baglam,
                            dugum.lineno,
                            "rebound-op",
                            f"Core işlemi '{taban}' kapsayıcısı üzerinden çağrılıyor",
                        )
                    )
                    continue

            # metot biçimi: <tablo>.select() / .update() / .delete()
            if isinstance(dugum.func, ast.Attribute) and dugum.func.attr in CORE_OPS:
                taban = _base_name(dugum.func.value)
                if taban in tablolar and taban not in modul_alias:
                    desteksiz.append(
                        Unsupported(
                            dosya,
                            baglam,
                            dugum.lineno,
                            "method-form",
                            f"{taban}.{dugum.func.attr}(...) zincir olarak çözümlenmiyor",
                        )
                    )
                    continue

            islem: str | None = None
            if isinstance(dugum.func, ast.Name) and dugum.func.id in dogrudan:
                islem = dugum.func.id
            elif (
                isinstance(dugum.func, ast.Attribute)
                and dugum.func.attr in CORE_OPS
                and isinstance(dugum.func.value, ast.Name)
                and dugum.func.value.id in modul_alias
            ):
                islem = dugum.func.attr
            if islem is None:
                continue

            zincir = _outermost_chain(dugum, ebeveyn)
            hedef, yol = _resolve_target(dugum, zincir, tablolar)
            parmak = _fingerprint(zincir)

            if hedef is None:
                desteksiz.append(
                    Unsupported(
                        dosya,
                        baglam,
                        dugum.lineno,
                        "unresolved-target",
                        f"{islem}(...) hedef tablosu çözülemedi (parmak izi {parmak})",
                    )
                )

            degisken = _variable_args(zincir, tablolar)
            if degisken:
                anahtar = (dosya, baglam, islem, hedef, parmak)
                izinli = VARIABLE_ARG_ALLOWLIST.get(anahtar)
                if izinli is None or izinli[0] != degisken:
                    desteksiz.append(
                        Unsupported(
                            dosya,
                            baglam,
                            dugum.lineno,
                            "variable-arg",
                            f"{islem}(...) zincirinde yapısı görülemeyen argüman: "
                            f"{list(degisken)} (hedef {hedef}, parmak izi {parmak})",
                        )
                    )

            sorgular.append(
                CoreQuery(dosya, baglam, dugum.lineno, islem, hedef, yol, parmak)
            )

    return sorgular, desteksiz


Kimlik = tuple[str, str, str, str]
Kayit = tuple[int, str | None, str]


def group(sorgular: list[CoreQuery]) -> dict[Kimlik, Kayit]:
    """Sorguları ``(dosya, bağlam, işlem, parmak izi)`` kimliğine göre gruplar."""
    gruplu: dict[Kimlik, Kayit] = {}
    for q in sorgular:
        anahtar = (q.dosya, q.baglam, q.islem, q.parmak_izi)
        if anahtar in gruplu:
            adet, hedef, yol = gruplu[anahtar]
            # Aynı AST aynı çözümlemeyi verir; farklıysa belirsizlik vardır.
            assert (hedef, yol) == (q.hedef, q.yol), (
                f"aynı kimlik farklı çözümleme verdi: {anahtar}"
            )
            gruplu[anahtar] = (adet + 1, hedef, yol)
        else:
            gruplu[anahtar] = (1, q.hedef, q.yol)
    return gruplu


def inventory_fingerprint(gruplu: dict[Kimlik, Kayit]) -> str:
    govde = "\n".join(
        f"{dosya}|{baglam}|{islem}|{parmak}|{adet}|{hedef}|{yol}"
        for (dosya, baglam, islem, parmak), (adet, hedef, yol) in sorted(gruplu.items())
    )
    return hashlib.sha256(govde.encode("utf-8")).hexdigest()


def inventory_drift(
    gozlenen: dict[Kimlik, Kayit], beklenen: dict[Kimlik, Kayit]
) -> tuple[list[str], list[str], list[str]]:
    """(yeni, eksik, değişmiş) — üçü de boşsa envanter güncel demektir."""
    yeni = [
        f"{d} :: {b or '<modül>'} :: {i} :: {p} (adet {gozlenen[(d, b, i, p)][0]}, "
        f"hedef {gozlenen[(d, b, i, p)][1]})"
        for (d, b, i, p) in gozlenen
        if (d, b, i, p) not in beklenen
    ]
    eksik = [
        f"{d} :: {b or '<modül>'} :: {i} :: {p} "
        f"(envanterde adet {beklenen[(d, b, i, p)][0]})"
        for (d, b, i, p) in beklenen
        if (d, b, i, p) not in gozlenen
    ]
    degismis = [
        f"{a[0]} :: {a[1] or '<modül>'} :: {a[2]} :: {a[3]} "
        f"beklenen {beklenen[a]} -> gözlenen {gozlenen[a]}"
        for a in gozlenen
        if a in beklenen and gozlenen[a] != beklenen[a]
    ]
    return sorted(yeni), sorted(eksik), sorted(degismis)


def load_app_sources() -> dict[str, str]:
    kaynaklar: dict[str, str] = {}
    for yol in sorted(APP_ROOT.rglob("*.py")):
        bagil = yol.relative_to(BACKEND_ROOT).as_posix()
        kaynaklar[bagil] = yol.read_text(encoding="utf-8", errors="replace")
    return kaynaklar


# --------------------------------------------------------------------------
# DAR VE GEREKÇELİ MUAFİYET
# --------------------------------------------------------------------------
#   (dosya, bağlam, işlem, hedef tablo, parmak izi) -> (argüman tanımlayıcıları, gerekçe)
#
# SINIR: Bu muafiyet yalnız "bu ifade bu bağlamda değişken argüman kullanır"
# beyanıdır. Değişkenin İÇERİĞİNİN değiştiğini algılamaz ve algıladığı
# iddiasında değildir. Bkz. modül docstring'i.
VARIABLE_ARG_ALLOWLIST: dict[tuple[str, str, str, str | None, str], tuple[tuple[str, ...], str]] = {
    # app/notifications/service.py:585  (_claim_notification)
    ("app/notifications/service.py", "_claim_notification", "update", "notifications",
     "3a82e3ca6f7cc4791d0b3bcbcfbf1e15adaecb615f680e111153b015e13855a0"): (
        ('where:name:claimable',),
        "Lease yeniden talep yuklemi ayni fonksiyonda kosullu olarak kuruluyor (or_ ile). Kiraci suzgeci company_id AYRI bir AND argumani oldugu icin bu degisken kiraci sinirini genisletemez. SINIR: bu muafiyet claimable ifadesinin ICERIGINDEKI degisimi algilamaz.",
    ),
    # app/notifications/service.py:758  (_finalize)
    ("app/notifications/service.py", "_finalize", "update", "notifications",
     "b0bea479f550103e9bd739f0e1419da7cc1b6c1ee7577e614499aaa6ad153ca2"): (
        ('values:**',),
        "Sonuc yazimi SET govdesi cagiran katmandan sozluk olarak geliyor; kiraci suzgeci ve lease CAS kosullari .where() icinde acikta duruyor. SINIR: bu muafiyet sozlugun ICERIGINDEKI sutun kumesi degisimini algilamaz.",
    ),
    # app/routers/companies.py:142  (update_company_settings)
    ("app/routers/companies.py", "update_company_settings", "update", "companies",
     "129af4bf7c5971f5e15da1d92e816f288ed6c484fd6b1e154d816612a8a2a214"): (
        ('values:**',),
        "Firma ayarlari kismi guncellemesi; SET sutunlari payload alanlarindan kuruluyor. Kiraci suzgeci companies.c.id == cid .where() icinde acikta. SINIR: bu muafiyet sozlugun ICERIGINDEKI sutun kumesi degisimini algilamaz.",
    ),
}


# --------------------------------------------------------------------------
# KAPALI ENVANTER
# --------------------------------------------------------------------------
#   (dosya, bağlam, işlem, zincir parmak izi) -> (adet, hedef tablo, çözümleme yolu)
# Satır numaraları yalnız yorumdur; kimliğin parçası değildir.
EXPECTED_QUERIES: dict[Kimlik, Kayit] = {
    # --- app/auth.py
    ("app/auth.py", "authenticate", "select",
     "44f8916efd3180d01f3d142a8da73941a951a7de6395b148f6cee42c797231a0"): (1, "users", "arg0"),  # satır [409]
    ("app/auth.py", "authenticate", "update",
     "f9049467a0c800cc06b8415f552deaed437b2a15ebeb68cb16af38cd1684dd63"): (1, "users", "arg0"),  # satır [418]
    ("app/auth.py", "clear_login_failures", "delete",
     "5b8c658a0be9f13a9497bd90d48eee34fe7c70d8db1756d8ec27c88f3857c148"): (1, "login_attempts", "arg0"),  # satır [398]
    ("app/auth.py", "get_user_by_token", "delete",
     "82fcd2225b51e58729a02539ce424eedf09cb270a5438781972a4453707ff669"): (1, "auth_tokens", "arg0"),  # satır [470]
    ("app/auth.py", "get_user_by_token", "select",
     "07c0ba1b22dcea393fc664e8392fe505d1d5abd488c912aa6c17345aea36549a"): (1, "users", "arg0"),  # satır [457]
    ("app/auth.py", "initialize_auth", "select",
     "5d0c83b40de97472d2e4256a57f4ce3c16fec5298c0c82a67b0e432e927836d4"): (1, "users", "arg0"),  # satır [312]
    ("app/auth.py", "initialize_auth", "select",
     "d093b998cba2eab5e4cb4730ddfcf2977ffbbea01f222ad8cdfbb97ff1b6cc2a"): (1, "users", "arg0"),  # satır [291]
    ("app/auth.py", "initialize_auth", "update",
     "6cedcf09bf30db9125d32f7bd330b77d0f19717ed0760be6f1e6929b0ff43643"): (1, "users", "arg0"),  # satır [322]
    ("app/auth.py", "issue_refresh_token", "delete",
     "1670a3c878fe2c29181933cbc1cad15b8916e69d32d09b8275b4736b09380e60"): (1, "auth_refresh_tokens", "arg0"),  # satır [511]
    ("app/auth.py", "issue_token", "delete",
     "82fcd2225b51e58729a02539ce424eedf09cb270a5438781972a4453707ff669"): (1, "auth_tokens", "arg0"),  # satır [441]
    ("app/auth.py", "login_lock_status", "select",
     "ffd8a37093cd64dc9f761c4a1515723abb076b35ee28ca804925e7a281168910"): (1, "login_attempts", "arg0"),  # satır [340]
    ("app/auth.py", "login_lock_status", "update",
     "faadf76a60bf65c3aebaa417ae31607191b88ba4e02cb05ac4cc52fd55b5328f"): (1, "login_attempts", "arg0"),  # satır [350]
    ("app/auth.py", "record_login_failure", "select",
     "ffd8a37093cd64dc9f761c4a1515723abb076b35ee28ca804925e7a281168910"): (1, "login_attempts", "arg0"),  # satır [360]
    ("app/auth.py", "record_login_failure", "update",
     "06514928c03f51fd6e91479fbb429e08844ef886f4cfc43fd59d85b2840691a7"): (1, "login_attempts", "arg0"),  # satır [374]
    ("app/auth.py", "revoke_refresh_family", "update",
     "3e2140a017dd17bd0e7e2426b95331d8a697a3b3eba0f35b3930e14c1af064a9"): (1, "auth_refresh_tokens", "arg0"),  # satır [519]
    ("app/auth.py", "revoke_refresh_token", "select",
     "05ed2eed7147c170ade8a1328120acd51db33e26e273eb27d6b88e684215fc7c"): (1, "auth_refresh_tokens", "arg0"),  # satır [532]
    ("app/auth.py", "revoke_refresh_token", "update",
     "655c7a33e6e97702bdbe268409c101c109f0a723ac9a0f77f637fef5ffa3023d"): (1, "auth_refresh_tokens", "arg0"),  # satır [542]
    ("app/auth.py", "revoke_token", "delete",
     "92aa2987d4fb74c5afbb63c2a925e52c5de068ee6095126fcc2d6f44145996d8"): (1, "auth_tokens", "arg0"),  # satır [447]
    ("app/auth.py", "revoke_user_access_tokens", "delete",
     "b305e86c20a75b4262942f65cea7be5fed78a8d0c56815eec95b95b0ecc3e7a6"): (1, "auth_tokens", "arg0"),  # satır [452]
    ("app/auth.py", "revoke_user_refresh_tokens", "update",
     "21a4b61a97b6835c8200b64b98ff59df774b96cf64de51b6ef543e73752a73f3"): (1, "auth_refresh_tokens", "arg0"),  # satır [551]
    ("app/auth.py", "rotate_refresh_token", "select",
     "21d5619c1df033b91eca55a7fced3702fec83d73ca0f186a09005f93f8d1742d"): (1, "users", "arg0"),  # satır [612]
    ("app/auth.py", "rotate_refresh_token", "select",
     "4dbaaf00f2c28d0ae8389c1355df57b18db7cf3d9d73b355f6a3a32bbb3e800f"): (1, "auth_refresh_tokens", "arg0"),  # satır [579]
    ("app/auth.py", "rotate_refresh_token", "update",
     "78538fe11ef4171056efe10aaabda3b3d694aa167bbdb5678f906c3e01466b1e"): (2, "auth_refresh_tokens", "arg0"),  # satır [604, 618]
    ("app/auth.py", "rotate_refresh_token", "update",
     "97226b31c8de89ffb2c1d1a170b6134fdabd326e97d94ec705e7df4c1da0bb23"): (1, "auth_refresh_tokens", "arg0"),  # satır [639]
    ("app/auth.py", "rotate_refresh_token", "update",
     "e1a6f21d805f3a2f98ecde37f4519d67a9a11e93e78fcf894ec09f2304bea473"): (2, "auth_refresh_tokens", "arg0"),  # satır [593, 626]
    # --- app/bootstrap_data.py
    ("app/bootstrap_data.py", "_ensure_bootstrap_admin", "select",
     "0f9a22b1442c5781d86ee5dad90dab6266c8c010791f22c4f787bea8550e94f8"): (1, "users", "arg0"),  # satır [29]
    ("app/bootstrap_data.py", "_ensure_bootstrap_admin", "select",
     "d093b998cba2eab5e4cb4730ddfcf2977ffbbea01f222ad8cdfbb97ff1b6cc2a"): (1, "users", "arg0"),  # satır [31]
    ("app/bootstrap_data.py", "_ensure_bootstrap_admin", "update",
     "9a1160f3a14224209d8782899f0d62fab608a4e951172964ac0f12937ea348f6"): (1, "users", "arg0"),  # satır [74]
    ("app/bootstrap_data.py", "_ensure_bootstrap_company", "select",
     "4e2b7176cb2aa1ec9e98b4471f3be0f71264108ef160627ce47926f54d10888e"): (1, "companies", "arg0"),  # satır [84]
    ("app/bootstrap_data.py", "_ensure_bootstrap_company", "select",
     "6cd33932ec6d9208c99aa18913a1bfe4acafbfe5d017941d62768f66170bb8dc"): (1, "companies", "arg0"),  # satır [92]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "0208f7ee963dd2cfe36011437e9046b02ac6f031243cfdd7104adf5872f35605"): (1, "branches", "arg0"),  # satır [125]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "0e1d87032a6d26061c771801c136d994924a434b291a69a68526aa9db0e697d2"): (1, "warehouse_stocks", "arg0"),  # satır [197]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "62e04220ca6141d895b51af50ce48ae7c2c25e2d5b4889df96b7ecb4fdc21768"): (1, "memberships", "arg0"),  # satır [147]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "86d148e9eeb18ac10282eb70249778a12b1fe30c4462649dc0fad838f6176772"): (1, "finance_accounts", "arg0"),  # satır [220]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "8d006fbe8a926949f1e15fd869fcabade17db264f5de7cb6fc1a7629e964db8f"): (1, "products", "arg0"),  # satır [192]
    ("app/bootstrap_data.py", "seed_bootstrap_data", "select",
     "8dc58d735f9ca51428a47d77fcc6f3dd6a964f8640b9ed3af6777606c2ecd43e"): (1, "warehouses", "arg0"),  # satır [163]
    # --- app/change_history.py
    ("app/change_history.py", "list_history", "select",
     "41b32182b7600f6cca8dab986d6882c3c021de295afb2a57f74f18a834ac2005"): (1, "entity_change_logs", "arg0"),  # satır [133]
    ("app/change_history.py", "restore_deleted", "select",
     "31df24ccd9e1e0938a84f4a7f32d5f304fe7aceab43181c9659cc67347db4579"): (1, "entity_change_logs", "arg0"),  # satır [153]
    # --- app/company_policies.py
    ("app/company_policies.py", "get_policy_settings", "select",
     "7d2cc17f9946df9e5d4ee1f30550bf414d699fcaea6d86aa6da7d49a7f1ae370"): (1, "companies", "arg0"),  # satır [76]
    # --- app/document_engine.py
    ("app/document_engine.py", "next_document_no", "update",
     "21b2fa1223d3b3bd83b7c10aebef04baa8483b64be169dc0f44d6897d7521a6d"): (1, "document_sequences", "arg0"),  # satır [171]
    # --- app/email_verification.py
    ("app/email_verification.py", "create_verification_token", "update",
     "0ab1a90ef67cbf37789e824062ed361e5cd34fcb88ecacebe315bba3857302ed"): (1, "email_verification_tokens", "arg0"),  # satır [125]
    # --- app/finance_engine.py
    ("app/finance_engine.py", "default_account", "select",
     "7995cce888f22cddbd8d3fa240492c44990e52dc29951634e89150c805f2bf12"): (1, "finance_accounts", "arg0"),  # satır [118]
    ("app/finance_engine.py", "default_account", "select",
     "81e4f1dca08d4a112dc92550a353a2a2583248e19a7c316bc616cf0d8b0bdee8"): (1, "finance_accounts", "arg0"),  # satır [128]
    ("app/finance_engine.py", "validate_payment_account", "select",
     "48d6acf0b0bbb906b3ccd2f2311a3900878b3139ebea4b15744c5f2c655e6dac"): (1, "finance_accounts", "arg0"),  # satır [76]
    # --- app/inventory.py
    ("app/inventory.py", "_stock_row_exists", "select",
     "577b4b130f020edc0e01d63563f9610f92b4cbde217dbc26e6669d342213a909"): (1, "warehouse_stocks", "arg0"),  # satır [311]
    ("app/inventory.py", "_update_existing_stock", "update",
     "2eab600608595d841ec537cdd1cc3ce4f3b3862dd8450a732ae232d3ad8e7fbf"): (1, "warehouse_stocks", "arg0"),  # satır [285]
    ("app/inventory.py", "adjust_warehouse_stock", "select",
     "577b4b130f020edc0e01d63563f9610f92b4cbde217dbc26e6669d342213a909"): (3, "warehouse_stocks", "arg0"),  # satır [442, 459, 492]
    ("app/inventory.py", "available_warehouse_stock", "select",
     "b1b092031d7b7b41b8487a6db9bfb1d20ec41e3b28d3291f999854e3d36a7114"): (1, "warehouse_stocks", "arg0"),  # satır [407]
    ("app/inventory.py", "default_warehouse", "select",
     "6719825615f92ab3f933051395764a89af08379a81d5169b56b50e53b1be10d7"): (1, "warehouses", "arg0"),  # satır [240]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "0e1d87032a6d26061c771801c136d994924a434b291a69a68526aa9db0e697d2"): (1, "warehouse_stocks", "arg0"),  # satır [187]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "2c7d50225b5404105284a473a474af781673e2654bd789980a115c401ce08152"): (1, "warehouses", "arg0"),  # satır [109]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "7829d709798109029018fbb5714b9960d4f70f1ff0523070dc184a90333a5358"): (1, "warehouses", "arg0"),  # satır [117]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "872b38d4aad5c003bae5700cae1ddf1874f42f17aa3c3fa08fa5d7326e2c5f87"): (1, "branches", "arg0"),  # satır [127]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "8d006fbe8a926949f1e15fd869fcabade17db264f5de7cb6fc1a7629e964db8f"): (1, "products", "arg0"),  # satır [179]
    ("app/inventory.py", "ensure_company_default_warehouse", "select",
     "25773da77535f1dab49ffcc7008e739a36fc064539229f7e2cba959b8416ee88"): (1, "warehouses", "arg0"),  # satır [158]
    ("app/inventory.py", "ensure_company_default_warehouse", "update",
     "8ca3e12934d889bfa502b0d5a1e0ca5adb6cc309637b1629b2f3daaa259a098f"): (1, "warehouses", "arg0"),  # satır [163]
    ("app/inventory.py", "ensure_company_default_warehouse", "update",
     "b2cd4425defb229cd92b73db6adf897198591379db4c4702b71bfa8f6fa753ea"): (1, "warehouses", "arg0"),  # satır [168]
    ("app/inventory.py", "initialize_inventory", "select",
     "3dfd509da3705d722712c9ec5cb3b9f0e4407f46cd53711888364c09c668ce88"): (1, "companies", "arg0"),  # satır [234]
    ("app/inventory.py", "issue_reserved_stock", "update",
     "0ad77d8dfb8ac2c75864d23969e2c03dc024854d9c4fc02b244c77741cbc292f"): (1, "warehouse_stocks", "arg0"),  # satır [385]
    ("app/inventory.py", "release_warehouse_reservation", "update",
     "a8f8256acfe3f217e973d9d7d13d518967ca7744b2fa0f9b7da1de4ba42b63ad"): (1, "warehouse_stocks", "arg0"),  # satır [356]
    ("app/inventory.py", "reserve_warehouse_stock", "update",
     "2d4486cfbe266d289818be4e3da92ee834108a9dfb3634799addce77a8132a45"): (1, "warehouse_stocks", "arg0"),  # satır [334]
    ("app/inventory.py", "sync_product_stock", "select",
     "3499e2de18041a71d0f091e442fff669b0a7a9eb8246233969f5aac268cb5fd4"): (1, "warehouse_stocks", "sole-table"),  # satır [257]
    ("app/inventory.py", "sync_product_stock", "update",
     "f562cb80da40ebbe79d5ee06b1fb68f341d0e3ebafcee97994b5e630b6a0a905"): (1, "products", "arg0"),  # satır [265]
    # --- app/notifications/service.py
    ("app/notifications/service.py", "_claim_notification", "select",
     "56d12e0cb16b107f0ab7b334d5403c38984733231eed8f77af4a302bc47cd345"): (1, "notifications", "arg0"),  # satır [602]
    ("app/notifications/service.py", "_claim_notification", "update",
     "3a82e3ca6f7cc4791d0b3bcbcfbf1e15adaecb615f680e111153b015e13855a0"): (1, "notifications", "arg0"),  # satır [585]
    ("app/notifications/service.py", "_finalize", "update",
     "b0bea479f550103e9bd739f0e1419da7cc1b6c1ee7577e614499aaa6ad153ca2"): (1, "notifications", "arg0"),  # satır [758]
    ("app/notifications/service.py", "_notification_row", "select",
     "8ecb26e6916709e60fc0e050eca1bea06340242a163a1bc01a222c9fed84fd5d"): (1, "notifications", "arg0"),  # satır [220]
    ("app/notifications/service.py", "approve_notification", "update",
     "6c6f5e190779d623796a9d44609f13cdbf3b3ff774d69b5008a423429c551819"): (1, "notifications", "arg0"),  # satır [407]
    ("app/notifications/service.py", "cancel_notification", "update",
     "711580bc5670659f11007ee4e286df5d03fb58d80a03f0ce6647848f523663f0"): (1, "notifications", "arg0"),  # satır [450]
    ("app/notifications/service.py", "claimable_notification_ids", "select",
     "4b60d209b4ce30baf86ceaecd339f921a2d6c82b861b9455b8ccdcd6e536f7d1"): (1, "notifications", "arg0"),  # satır [1026]
    ("app/notifications/service.py", "enqueue_notification", "select",
     "6190ae4d1a8aeeb947b484ae6a487d5f24838618723cd3f914c4972e8759afb8"): (1, "notifications", "arg0"),  # satır [355]
    ("app/notifications/service.py", "invalidate_approval", "update",
     "cb4e3c4762282775a9248faa52232fc6293cc2fe604c26c65c895c3e10dacfe4"): (1, "notifications", "arg0"),  # satır [481]
    ("app/notifications/service.py", "schedule_retry", "update",
     "dcb57aa92318c3a7e301e9dbd268515adece288faf184432721f2fa364eb79f5"): (1, "notifications", "arg0"),  # satır [935]
    # --- app/password_reset.py
    ("app/password_reset.py", "create_reset_token", "update",
     "13b587dd39cbe87f0660be0528772d85727f108ccb1df33e9d16a38c8a9d7557"): (1, "password_reset_tokens", "arg0"),  # satır [101]
    # --- app/routers/auth.py
    ("app/routers/auth.py", "_consume_ip_limit", "delete",
     "3b87b6f09ac727b1278a17aa45d876aedde8a039a4dbd9be6becd762064a39b1"): (1, "auth_rate_limits", "arg0"),  # satır [339]
    ("app/routers/auth.py", "_consume_ip_limit", "select",
     "d36a36f453ab9c0a814299307bf77192390eeac68ac2e6391dd0fab17e43eb6c"): (1, "auth_rate_limits", "select_from"),  # satır [342]
    ("app/routers/auth.py", "change_password", "select",
     "861a925417c8a73d3e3c351c16fc43ed0c7b313d9fc3d41f6032b81be90e8833"): (1, "users", "arg0"),  # satır [937]
    ("app/routers/auth.py", "change_password", "update",
     "f8e3a20f911d319f805ddde56e40637977f6920825ee5cbf5b0525a9099ef68e"): (1, "users", "arg0"),  # satır [952]
    ("app/routers/auth.py", "change_user_status", "select",
     "97189d545b0f3404ec5b0043d16f0aee9c911b463975519bcb4b16575ef1ffc0"): (1, "users", "arg0"),  # satır [1089]
    ("app/routers/auth.py", "change_user_status", "select",
     "db7a4afbccd0a2eaf3e054109e641749d1b57a07359bfca03d8ab514b1662c7f"): (1, "memberships", "arg0"),  # satır [1072]
    ("app/routers/auth.py", "change_user_status", "update",
     "f3ba396b024921ed737502f5ea612194711e8f64e89367039684905d1d11cde6"): (1, "users", "arg0"),  # satır [1102]
    ("app/routers/auth.py", "create_user", "select",
     "446388fc8c27231e9e7bc94af2d829051021f5155efd38f1821b2a2496fb3448"): (1, "users", "arg0"),  # satır [1022]
    ("app/routers/auth.py", "forgot_password", "select",
     "3bfb9349f998bd8a993cc722a37d1887b02b667865c5abe08b57e4849cc12c6b"): (1, "users", "arg0"),  # satır [732]
    ("app/routers/auth.py", "forgot_password", "select",
     "faf2b78d986e40a0c541764a482b4747ca4ca0e8426b71ae9cdd9f1cd8f0ae21"): (1, "memberships", "arg0"),  # satır [745]
    ("app/routers/auth.py", "list_audit", "select",
     "988464d52061410996325f336cca55f04026514e171b68c2fc3edea4cfc3120c"): (1, "audit_logs", "arg0"),  # satır [1124]
    ("app/routers/auth.py", "list_users", "select",
     "30b0adcd72fba5291cc22d53773aba54b6c1843e771883a889b6cabdd49a4136"): (1, "users", "arg0"),  # satır [995]
    ("app/routers/auth.py", "register", "select",
     "f28c8f3f1dd5d3ea667b6a0227968dfa1b30a669270282718f67e2f2b347a65b"): (1, "users", "arg0"),  # satır [532]
    ("app/routers/auth.py", "resend_verification", "select",
     "eeee6da8e3bd3fc54052c4c0ec5aaed412cd819b1840c4840de8014253681e9e"): (1, "users", "arg0"),  # satır [679]
    ("app/routers/auth.py", "resend_verification", "select",
     "faf2b78d986e40a0c541764a482b4747ca4ca0e8426b71ae9cdd9f1cd8f0ae21"): (1, "memberships", "arg0"),  # satır [688]
    ("app/routers/auth.py", "reset_password", "select",
     "47f3f1c04481b7463fb9d5d3ee8580c8098c6e0bdce85e7f3b4e00a002b3c742"): (1, "password_reset_tokens", "arg0"),  # satır [779]
    ("app/routers/auth.py", "reset_password", "select",
     "915a3692a951507949d422125b4afa9c0e53bf7a2ccdb2b196be04a63f8b6e30"): (1, "users", "arg0"),  # satır [794]
    ("app/routers/auth.py", "reset_password", "update",
     "8dbd8036747316c1b3a4d697bd2906343ce6c5c7359b4ad33119aab4a932f400"): (1, "users", "arg0"),  # satır [823]
    ("app/routers/auth.py", "reset_password", "update",
     "9291eb169ba5b1a39c23f8d87e07d0cee28a32a79b54bfadfaf169e913fb2837"): (1, "password_reset_tokens", "arg0"),  # satır [811]
    ("app/routers/auth.py", "verify_email", "select",
     "9657853e0d437de455d7ce2be86409a3874d9dd1a998090d0e5f750b9e470d75"): (1, "email_verification_tokens", "arg0"),  # satır [636]
    ("app/routers/auth.py", "verify_email", "update",
     "8f2b05ec2ab554d78b2f53ccb0e15e45c4c657dd711f8d8d6b544e15042bb9ec"): (1, "email_verification_tokens", "arg0"),  # satır [651]
    ("app/routers/auth.py", "verify_email", "update",
     "ca4c2613620c6dd23ed462cfc2ff973af225e9e95573ae0621bea2d5db14953e"): (1, "users", "arg0"),  # satır [661]
    # --- app/routers/companies.py
    # Parmak izi 2026-09-04te yenilendi: projeksiyona `companies.c.profiller`
    # eklendi (göç 20260904_0068, FİRMA PROFİLİ). Sorgunun YÜKLEMİ ve hedefi
    # DEĞİŞMEDİ — `where(companies.c.id == cid)` aynen duruyor ve `cid`
    # `company_id(request)`ten geliyor; değişen tek şey OKUNAN SÜTUN kümesidir.
    # Parmak izi 2026-09-07de yenilendi: projeksiyona
    # `companies.c.farm_plantback_policy` eklendi (göç 20260907_0072,
    # EKİM-ARASI BEKLEME). Sorgunun YÜKLEMİ ve hedefi DEĞİŞMEDİ —
    # `where(companies.c.id == cid)` aynen duruyor; değişen tek şey OKUNAN
    # SÜTUN kümesidir.
    ("app/routers/companies.py", "get_company_settings", "select",
     "6f7b973d2d873a83a549635c6ddb5f3f93fab1e71c6bf8286f3958f111aebb2b"): (1, "companies", "arg0"),  # satır [115]
    ("app/routers/companies.py", "list_branches", "select",
     "a1e5782db98e816f1960a283e2827acf34c2f7f89add2c976e5c8d680863b69b"): (1, "branches", "arg0"),  # satır [183]
    ("app/routers/companies.py", "list_policy_overrides", "select",
     "ea58d0004604dbac10e300d3cf299a9f7fba9a4bb528ff1242d312055532d2a4"): (1, "policy_override_logs", "arg0"),  # satır [171]
    ("app/routers/companies.py", "update_company_settings", "update",
     "129af4bf7c5971f5e15da1d92e816f288ed6c484fd6b1e154d816612a8a2a214"): (1, "companies", "arg0"),  # satır [169]
    # --- app/routers/finance.py
    ("app/routers/finance.py", "_account_row", "select",
     "d788fedfa3e8275016b11f4e6c2153ab26d5d8ec163a2474466b3b1bcca63e01"): (1, "finance_accounts", "arg0"),  # satır [566]
    ("app/routers/finance.py", "delete_instrument", "select",
     "20a3104e323d9c24fdc7b839ca987cf2c012ec78bd840bace1d97113d84e1a98"): (1, "financial_instruments", "arg0"),  # satır [695]
    ("app/routers/finance.py", "list_payment_accounts", "select",
     "75ca79415a835ad03e2264c16020770c2b26512ddb29706fc1b3ef58947c3daa"): (1, "finance_accounts", "arg0"),  # satır [245]
    ("app/routers/finance.py", "update_instrument_status", "select",
     "de3b960fa34abfe323fa1cef8ec69adbdadaa9814c1c05be99b5174e48fa8c5b"): (1, "financial_instruments", "arg0"),  # satır [677]
    # --- app/routers/platform_audit.py
    # Kiracıya bağlanamayan denetim satırlarının okuma yolu. company_id ile
    # SÜZMEZ, tam tersine company_id IS NULL arar — hedef kiracı olmayan
    # satırlardır ve yetkiyi `require_platform_operator` verir.
    ("app/routers/platform_audit.py", "list_untenanted_audit", "select",
     "a484cea5af5f3c646ac9be74989e9d86d36cf4e7718226921df515e9212aef86"): (1, "audit_logs", "arg0"),
    # --- app/routers/products.py
    ("app/routers/products.py", "adjust", "select",
     "1f3c07e9814eaa78dfd498fe32fb7b29a97176298353a30b60f5f1a1f30a48b1"): (1, "warehouse_stocks", "arg0"),  # satır [505]
    ("app/routers/products.py", "adjust", "select",
     "a5b9afbd37da6d1ed6b4b009b4d50fad1ffb61eb4faa184e08cdaff77dfd2f99"): (1, "warehouses", "arg0"),  # satır [497]
    ("app/routers/products.py", "bulk_stock", "select",
     "1f3c07e9814eaa78dfd498fe32fb7b29a97176298353a30b60f5f1a1f30a48b1"): (1, "warehouse_stocks", "arg0"),  # satır [735]
    ("app/routers/products.py", "bulk_stock", "select",
     "a5b9afbd37da6d1ed6b4b009b4d50fad1ffb61eb4faa184e08cdaff77dfd2f99"): (1, "warehouses", "arg0"),  # satır [698]
    ("app/routers/products.py", "create", "select",
     "d1fe7f57a4d1926eff4cab929165faca79fa8b46ca26f29f543ec03cfc8f61c0"): (1, "warehouses", "arg0"),  # satır [335]
    ("app/routers/products.py", "list_products", "select",
     "a5b9afbd37da6d1ed6b4b009b4d50fad1ffb61eb4faa184e08cdaff77dfd2f99"): (1, "warehouses", "arg0"),  # satır [100]
    # --- app/routers/transactions.py
    ("app/routers/transactions.py", "_warehouse", "select",
     "747f9f16551df63f853268efc002c71076cb1d7afbe9154854dbb00e7e4fb6f1"): (1, "warehouses", "arg0"),  # satır [111]
    # Belge şubesi: id VE company_id aynı sorguda; yabancı şube 404.
    ("app/routers/transactions.py", "_branch", "select",
     "354f24a0f70581619ff178f4046eb1353cd2180a8c1816ce3dd9b4b3de3ef61e"): (1, "branches", "arg0"),  # satır [133]
    # --- app/routers/warehouse_counts.py
    ("app/routers/warehouse_counts.py", "create_count", "select",
     "6a6e44a9a8ccc68aef417d36b16afc8bdd20ed8b3f3b466f983c4acfd19dec32"): (1, "warehouses", "arg0"),  # satır [110]
    ("app/routers/warehouse_counts.py", "create_count", "select",
     "9348f81645dcc19cecae9c0a205a4a911b12fac282cff7fa250132b2c44e68c6"): (1, "warehouse_stocks", "arg0"),  # satır [136]
    ("app/routers/warehouse_counts.py", "create_count", "select",
     "ca63708390452ca66dd44fc205a7f00696b7b2584542c5c16d7ab0cb13eb1a31"): (1, "products", "arg0"),  # satır [121]
    ("app/routers/warehouse_counts.py", "create_count", "update",
     "27fb0fd54ab4932ffce603353057bad653d04a4a3f6678a3490ae556c104980c"): (1, "warehouse_stocks", "arg0"),  # satır [193]
    ("app/routers/warehouse_counts.py", "create_count", "update",
     "9c71bbdf0064c52001b19bc64237c9610501f3750462942f288ec2c3efc22274"): (1, "stock_movements", "arg0"),  # satır [222]
    ("app/routers/warehouse_counts.py", "list_counts", "select",
     "1dc424de54f4834a6675850dea89cbd9a16d99536bf222f12815910712094d23"): (1, "warehouses", "arg0"),  # satır [288]
    # --- app/routers/warehouses.py
    ("app/routers/warehouses.py", "create_transfer", "select",
     "1178057488d9732b1ae94c43a792eb82715ae020267b1b23a3ad21af567d14e0"): (1, "warehouses", "arg0"),  # satır [241]
    ("app/routers/warehouses.py", "create_warehouse", "select",
     "e4320fe6d29344b1216177b0491790418d975a684ae81ec3213670a8431dffa5"): (1, "branches", "arg0"),  # satır [56]
    ("app/routers/warehouses.py", "create_warehouse", "update",
     "1259b167b7dbee0ea6fa9bf5ac63e2a3942802018bb71ea8731a7877e9ca4ac0"): (1, "warehouses", "arg0"),  # satır [64]
    ("app/routers/warehouses.py", "set_critical", "select",
     "0aa782ea2966e4961c26873b53f115599fa154ef7e3d15c765b5dda8f7d27016"): (1, "warehouse_stocks", "arg0"),  # satır [201]
    ("app/routers/warehouses.py", "set_critical", "select",
     "82e15736f4e78c5c102abdb30f3efc09758d770dc9f2c766215115a42012860b"): (1, "warehouses", "arg0"),  # satır [188]
    ("app/routers/warehouses.py", "set_critical", "update",
     "337662a58fc7893def9fb95dea9b7df6bfc7f83c72dce741c99a46830d718284"): (1, "warehouse_stocks", "arg0"),  # satır [209]
    ("app/routers/warehouses.py", "warehouse_stock", "select",
     "10a44c92250e342b96409194a1827a43123b9fd6f79272de1bc8b5df90089367"): (1, "warehouses", "arg0"),  # satır [106]
    # --- app/routers/workflow.py
    ("app/routers/workflow.py", "_warehouse", "select",
     "1dc424de54f4834a6675850dea89cbd9a16d99536bf222f12815910712094d23"): (1, "warehouses", "arg0"),  # satır [121]
    # --- app/tenancy.py
    ("app/tenancy.py", "initialize_tenancy", "select",
     "6cd33932ec6d9208c99aa18913a1bfe4acafbfe5d017941d62768f66170bb8dc"): (1, "companies", "arg0"),  # satır [101]
    ("app/tenancy.py", "initialize_tenancy", "select",
     "d062dfd4b26f36b95dcf3ca075b78bbbd76e8f25e99c5b3285f98263545df8a9"): (1, "memberships", "arg0"),  # satır [118]
    ("app/tenancy.py", "resolve_company", "select",
     "c3a622cb58afd937cd6c7409523df011add8b483f71b9c1f34665e340977873e"): (1, "memberships", "arg0"),  # satır [127]
    ("app/tenancy.py", "user_companies", "select",
     "f1fc2361694c6728151a4dc174b64254d621e147c8478a6b7169e11c60ddb3c4"): (1, "companies", "arg0"),  # satır [122]
    # --- app/user_status_tenant_guard.py
    ("app/user_status_tenant_guard.py", "prevent_cross_tenant_user_deactivation", "select",
     "38a52a419229a3dbb74a84facd7ad35755719821e430900047785baf7b884f60"): (1, "memberships", "arg0"),  # satır [27]
    # --- app/work_order_stock.py
    ("app/work_order_stock.py", "issue_available_part_stock", "update",
     "1313db21c6eaf87716bbcc2ee6523406e85501f7aaaf6abcf07e1e93fd8461b3"): (1, "warehouse_stocks", "arg0"),  # satır [156]
    # --- app/routers/kiraci_disa_aktarim.py
    # HEDEF TABLO NEDEN `None`: bu üç sorgunun tablosu çalışma zamanında
    # YANSITILIR (``MetaData.reflect``), yani modül düzeyinde
    # ``X = Table("ad", ...)`` biçiminde YAZILI DEĞİL ve tarayıcı onu statik
    # olarak çözemez. Bu, kiracı dışa aktarımının TASARIM GEREĞİ: elle yazılmış
    # bir tablo listesi yeni göçlerde sessizce eksik kalırdı. Üçü de
    # ``UNRESOLVED_ALLOWLIST``te ayrıca gerekçelendirildi.
    ("app/routers/kiraci_disa_aktarim.py", "_sirali", "select",
     "0c66287d1ca1c6ba04ef4851ccdcf470092c7d4010cfe82e4d50d1a5c80b5fff"): (1, None, "unresolved"),  # satır [184]
    ("app/routers/kiraci_disa_aktarim.py", "_sema_seviyesi", "select",
     "cd50bebcd58d7320694fa287dd30cd8b9bd4599f558d97f225c68932c351763f"): (1, None, "unresolved"),  # satır [225]
    ("app/routers/kiraci_disa_aktarim.py", "_uret", "select",
     "22146e8f2b8865b09df07eb95d99700ca20acb0071dc458f749716f9457560f1"): (1, None, "unresolved"),  # satır [282]
    # --- app/routers/kiraci_imha.py
    # KİRACI YUMUŞAK İMHASI. Dışa aktarımın tersine burada HİÇBİR hedef
    # çözülemez DEĞİL: dört sorgunun da tablosu modül düzeyinde yazılı
    # (`tenancy.companies` / `tenancy.memberships`) ve tarayıcı ikisini de
    # görür. `UYELIKLER = memberships` biçiminde bir takma ad DENENDİ ve
    # tarayıcının hedefi çözmesini ENGELLEDİĞİ ölçüldü; takma ad kaldırıldı.
    ("app/routers/kiraci_imha.py", "firmayi_imha_et", "select",
     "1ca178e7977a564eca6a5a8be7567762ccd8ced0c2d68b61145f37c093634349"): (1, "companies", "arg0"),  # satır [111]
    ("app/routers/kiraci_imha.py", "firmayi_imha_et", "select",
     "5f7e8d234443f629f4f98b1abb49da73f3b54eb1c5c2780ee0b951fbd83889c2"): (1, "memberships", "select_from"),  # satır [126]
    ("app/routers/kiraci_imha.py", "firmayi_imha_et", "update",
     "344689a43a024706cb6327aee8f29b2a40490b80526b4bd1852ec0ba9a15294c"): (1, "companies", "arg0"),  # satır [133]
    ("app/routers/kiraci_imha.py", "firmayi_imha_et", "delete",
     "4594c3b519aab868921b5bd88a7f5bc6f2579889963ec81277bc7383effb9a72"): (1, "memberships", "arg0"),  # satır [137]
}

TOTAL_CORE_QUERIES = 144
EXPECTED_OP_COUNTS = {"select": 94, "update": 42, "delete": 8}
# 2026-08-12: iki sorgu bilerek değişti — `ensure_company_default_warehouse`
# depo adı taramasına kiracı kapsamı eklendi (şema ölçümü: warehouses.name
# üzerinde ne küresel ne kiracı kapsamlı UNIQUE var) ve `_finalize` opak
# sözlük yayılımını bırakıp açık sütun kümesine geçti.
# 20260812: +1 select — app/routers/platform_audit.py::list_untenanted_audit.
# Firmasız güvenlik denetim satırlarının okuma yolu; company_id ile SÜZMEZ,
# company_id IS NULL arar (bkz. göç 20260812_0059).
# 20260905 kantar fişi v2 (göç 20260904_0069): Core envanteri DEĞİŞMEDİ —
# ÖLÇÜLDÜ, birleşmiş ağaçta (CPython 3.12.10) 137/89/41/7 ve parmak izi
# 787d65cf SABİT. Dilimin bütün SQL'i text() ile yazıldı (farm.py kantar bloğu,
# products.py `base_unit` UPDATE'i); hiçbir Core sorgunun yüklemi ya da hedef
# tablosu değişmedi. Bu satır bir yeniden çivileme DEĞİL, ölçümün kaydıdır.
# 20260905 kiracı dışa aktarımı: +3 select — app/routers/kiraci_disa_aktarim.py.
# Üçü de hedefi ÇÖZÜLEMEYEN sorgulardır ve bu bilinçlidir: dışa aktarım
# tabloları çalışma zamanında yansıtır, çünkü elle yazılmış bir liste yeni bir
# göç kiracı tablosu eklediğinde sessizce eksik kalır ve o tablonun satırları
# dosyaya HİÇ girmezdi. Kiracı yüklemi statik olarak GÖRÜNÜR durumdadır
# (``.where(tablo.c.company_id == cid)``), yalnız tablo NESNESİ değişkendir.
# 20260906 kiracı yumuşak imhası (5.1b): 140 -> 144. DÖRDÜ DE
# app/routers/kiraci_imha.py: `companies` okuması, `memberships` sayımı,
# `companies` UPDATE'i, `memberships` DELETE'i. app/tenancy.py'ye HİÇBİR
# sorgu EKLENMEDİ — o dosyaya bu dilim HİÇ DOKUNMADI. Drift raporu ÖLÇÜLDÜ:
# `changed` ve `stale` BOŞ — artış YALNIZ ekleme, hiçbir mevcut sorgunun
# yüklemi ya da hedefi DEĞİŞMEDİ. `UNRESOLVED_ALLOWLIST` BÜYÜMEDİ: dört
# sorgunun da hedefi statik olarak çözüldü. TABAN `a2c5f61`; E1b ve yeniden
# kuyruklama Core envanterini KIMILDATMADI (ikisi de text() ile yazılmış),
# bu yüzden taban 140'ta kaldı ve artış YALNIZ bu dilimindir.
INVENTORY_FINGERPRINT = "5cb0ad7b82b7b7b931bc5e6772baf681e3434e34b7aae433297f4fab23c272da"

#: Çözülemeyen hedefler için dar, gerekçeli muafiyet.
#:
#: MUAFİYETİN SINIRI: bu liste yalnız "hedef tablo statik olarak çözülemiyor"
#: der. Kiracı yükleminin varlığını KANITLAMAZ — onu
#: ``tests/test_kiraci_disa_aktarim.py::test_hicbir_dosyada_baska_firmanin_satiri_yok``
#: çalışma zamanında, iki firmalık gerçek veriyle ve HER tabloyu tek tek
#: gezerek doğrular. Statik kapının göremediği yeri dinamik kapı kapatıyor.
UNRESOLVED_ALLOWLIST: dict[Kimlik, str] = {
    ("app/routers/kiraci_disa_aktarim.py", "_sirali", "select",
     "0c66287d1ca1c6ba04ef4851ccdcf470092c7d4010cfe82e4d50d1a5c80b5fff"):
        "Kiracı dışa aktarımının satır okuması. Tablo `MetaData.reflect` ile "
        "çalışma zamanında gelir; birincil anahtar sırası da o tablodan "
        "türetilir. Kiracı yüklemi çağıranda (`_satirlar`) AÇIKÇA eklenir: "
        "`.where(tablo.c.company_id == cid)`.",
    ("app/routers/kiraci_disa_aktarim.py", "_sema_seviyesi", "select",
     "cd50bebcd58d7320694fa287dd30cd8b9bd4599f558d97f225c68932c351763f"):
        "`alembic_version.version_num` okuması. Bu tablo KİRACI TABLOSU "
        "DEĞİL (company_id sütunu yok) ve tek satır taşır; şema seviyesini "
        "koda gömmemek için veritabanından okunur.",
    ("app/routers/kiraci_disa_aktarim.py", "_uret", "select",
     "22146e8f2b8865b09df07eb95d99700ca20acb0071dc458f749716f9457560f1"):
        "Dışa aktarılan firmanın KENDİ satırı: `companies` tablosundan "
        "`.where(firmalar.c.id == cid)` ile TEK satır. Kiracı sınırı burada "
        "birincil anahtar eşitliğidir.",
}

#: Metot biçimi kullanımlar için dar, gerekçeli muafiyet. Bugün boş.
METHOD_FORM_ALLOWLIST: dict[tuple[str, str, int], str] = {}


@pytest.fixture(scope="module")
def app_scan():
    kaynaklar = load_app_sources()
    sorgular, desteksiz = scan_sources(kaynaklar)
    return kaynaklar, sorgular, desteksiz


# --------------------------------------------------------------------------
# Kapı: tarayıcı sağlığı
# --------------------------------------------------------------------------
def test_app_directory_has_sources(app_scan):
    kaynaklar, _, _ = app_scan
    assert APP_ROOT.is_dir(), f"app dizini bulunamadı: {APP_ROOT}"
    assert len(kaynaklar) >= 50, f"beklenmedik biçimde az modül tarandı: {len(kaynaklar)}"


def test_scan_is_not_empty(app_scan):
    _, sorgular, _ = app_scan
    assert sorgular, "tarayıcı hiç Core sorgusu bulamadı — kapı bozulmuş olabilir"


def test_expected_inventory_is_not_empty():
    assert EXPECTED_QUERIES, "envanter boş — kapı hiçbir şeyi korumaz"
    assert TOTAL_CORE_QUERIES > 0
    assert sum(EXPECTED_OP_COUNTS.values()) == TOTAL_CORE_QUERIES


def test_expected_counts_sum_to_total():
    toplam = sum(adet for adet, _, _ in EXPECTED_QUERIES.values())
    assert toplam == TOTAL_CORE_QUERIES, (
        f"envanterdeki adet toplamı {toplam}, beyan edilen toplam {TOTAL_CORE_QUERIES}"
    )


# --------------------------------------------------------------------------
# Kapı: kapalı envanter
# --------------------------------------------------------------------------
def test_no_core_query_drift(app_scan):
    _, sorgular, _ = app_scan
    yeni, eksik, degismis = inventory_drift(group(sorgular), EXPECTED_QUERIES)
    mesaj = []
    if yeni:
        mesaj.append("ENVANTERDE OLMAYAN YENİ SORGU:\n  " + "\n  ".join(yeni))
    if eksik:
        mesaj.append("ENVANTERDE OLUP KODDA BULUNMAYAN SORGU:\n  " + "\n  ".join(eksik))
    if degismis:
        mesaj.append("YAPISI/ADEDİ DEĞİŞEN SORGU:\n  " + "\n  ".join(degismis))
    assert not mesaj, (
        "SQLAlchemy Core sorgu envanteri güncel değil. Değişiklik bilerek "
        "yapıldıysa envanteri güncelleyin ve kiracı kapsamını gözden geçirin.\n\n"
        + "\n\n".join(mesaj)
    )


def test_total_and_per_op_counts(app_scan):
    _, sorgular, _ = app_scan
    assert len(sorgular) == TOTAL_CORE_QUERIES, (
        f"toplam Core sorgu sayısı {len(sorgular)}, beklenen {TOTAL_CORE_QUERIES}"
    )
    gozlenen = {op: sum(1 for q in sorgular if q.islem == op) for op in sorted(CORE_OPS)}
    assert gozlenen == EXPECTED_OP_COUNTS, f"işlem dağılımı değişti: {gozlenen}"


def test_inventory_fingerprint(app_scan):
    _, sorgular, _ = app_scan
    assert inventory_fingerprint(group(sorgular)) == INVENTORY_FINGERPRINT


# --------------------------------------------------------------------------
# Kapı: fail-closed davranış
# --------------------------------------------------------------------------
def test_no_unresolved_targets(app_scan):
    _, sorgular, _ = app_scan
    cozulemeyen = [
        f"{q.dosya}:{q.satir} [{q.baglam or '<modül>'}] {q.islem}"
        for q in sorgular
        if q.hedef is None
        and (q.dosya, q.baglam, q.islem, q.parmak_izi) not in UNRESOLVED_ALLOWLIST
    ]
    assert not cozulemeyen, (
        "hedef tablosu çözülemeyen Core sorgusu var; güvenli sayılamaz:\n  "
        + "\n  ".join(cozulemeyen)
    )


def test_no_unsupported_expressions(app_scan):
    _, _, desteksiz = app_scan
    # ``unresolved-target`` AYNI olayı iki kapıya birden düşürür: hem burası
    # hem ``test_no_unresolved_targets``. O kapı muafiyeti
    # ``UNRESOLVED_ALLOWLIST``ten (dosya, bağlam, işlem, parmak izi) okuyor;
    # burası ise yalnız SATIR NUMARASINA bakan ``METHOD_FORM_ALLOWLIST``i
    # biliyordu. Aynı kayıt için iki ayrı muafiyet listesi istemek gereksiz
    # bir ikinci giriş üretir ve satır numarası kimliğe girdiği için araya bir
    # satır eklendiğinde SEBEPSİZ kırılır. Gerekçesi zaten yazılmış bir
    # çözülemeyen hedef burada tekrar istenmez; muafiyet DAR kalır, çünkü
    # kayıt hâlâ dosya + PARMAK İZİ ile eşleşmek zorundadır.
    izinli_parmaklar = {(dosya, parmak)
                        for dosya, _, _, parmak in UNRESOLVED_ALLOWLIST}
    kalan = [
        f"{u.dosya}:{u.satir} [{u.baglam or '<modül>'}] [{u.tur}] {u.aciklama}"
        for u in desteksiz
        if (u.dosya, u.tur, u.satir) not in METHOD_FORM_ALLOWLIST
        and not (
            u.tur == "unresolved-target"
            and any(parmak in u.aciklama
                    for dosya, parmak in izinli_parmaklar if dosya == u.dosya)
        )
    ]
    assert not kalan, (
        "bu tarayıcının çözümleyemediği ifadeler var; sessizce güvenli sayılmaz:\n  "
        + "\n  ".join(kalan)
    )


def test_resolution_paths_are_declared(app_scan):
    _, sorgular, _ = app_scan
    for anahtar, (_, _, yol) in group(sorgular).items():
        assert anahtar in EXPECTED_QUERIES
        assert EXPECTED_QUERIES[anahtar][2] == yol
    sezgisel = [k[0] for k, v in EXPECTED_QUERIES.items() if v[2] == "sole-table"]
    assert len(sezgisel) <= 1, (
        f"sezgisel 'sole-table' çözümlemesi yaygınlaşıyor; elle doğrulama gerekir: {sezgisel}"
    )


def test_variable_arg_allowlist_is_narrow_and_live(app_scan):
    """Muafiyetler dar olmalı ve hepsi gerçekten kullanılıyor olmalı."""
    kaynaklar, _, _ = app_scan
    tablolar = _table_registry(kaynaklar)
    gozlenen: set[tuple] = set()
    for dosya, kaynak in kaynaklar.items():
        agac = _parse(kaynak)
        if agac is None:
            continue
        dogrudan, modul_alias = _sqlalchemy_op_names(agac)
        if not dogrudan and not modul_alias:
            continue
        ebeveyn = _parent_map(agac)
        baglamlar = _context_map(agac)
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call):
                continue
            islem = None
            if isinstance(dugum.func, ast.Name) and dugum.func.id in dogrudan:
                islem = dugum.func.id
            elif (
                isinstance(dugum.func, ast.Attribute)
                and dugum.func.attr in CORE_OPS
                and isinstance(dugum.func.value, ast.Name)
                and dugum.func.value.id in modul_alias
            ):
                islem = dugum.func.attr
            if islem is None:
                continue
            zincir = _outermost_chain(dugum, ebeveyn)
            degisken = _variable_args(zincir, tablolar)
            if degisken:
                hedef, _ = _resolve_target(dugum, zincir, tablolar)
                gozlenen.add(
                    (
                        dosya,
                        baglamlar.get(dugum, ""),
                        islem,
                        hedef,
                        _fingerprint(zincir),
                    )
                )
    olu = set(VARIABLE_ARG_ALLOWLIST) - gozlenen
    assert not olu, f"kullanılmayan (ölü) muafiyet kaydı var: {sorted(olu)}"
    for anahtar, (_, gerekce) in VARIABLE_ARG_ALLOWLIST.items():
        assert anahtar[0].startswith("app/"), anahtar
        assert anahtar[1], f"modül düzeyi muafiyet çok geniş: {anahtar}"
        assert len(gerekce) >= 40, f"gerekçe çok kısa: {anahtar}"


# --------------------------------------------------------------------------
# Pozitif örnekler
# --------------------------------------------------------------------------
def test_inventory_py_examples_present(app_scan):
    _, sorgular, _ = app_scan
    envanter_py = [q for q in sorgular if q.dosya == "app/inventory.py"]
    assert envanter_py, "app/inventory.py hiç Core sorgusu içermiyor görünüyor"
    assert {"warehouse_stocks", "warehouses", "products"} <= {q.hedef for q in envanter_py}
    assert all(q.hedef is not None for q in envanter_py)
    assert all(q.baglam for q in envanter_py), "tüm sorgular bir fonksiyon içinde olmalı"


def test_document_engine_example_present(app_scan):
    _, sorgular, _ = app_scan
    belge = [q for q in sorgular if q.dosya == "app/document_engine.py"]
    assert belge, "app/document_engine.py hiç Core sorgusu içermiyor görünüyor"
    assert {q.islem for q in belge} == {"update"}
    assert {q.hedef for q in belge} == {"document_sequences"}


# --------------------------------------------------------------------------
# NEGATİF/POZİTİF TESTLER — gerçek tarayıcı ve karşılaştırma yolunu kullanır
# --------------------------------------------------------------------------
TABLO_TANIMI = (
    "from sqlalchemy import (MetaData, Table, Column, Integer, select, update,"
    " delete, and_, or_)\n"
    "meta = MetaData()\n"
    'siparisler = Table("orders", meta, Column("id", Integer),'
    ' Column("company_id", Integer), Column("total", Integer))\n'
    'kalemler = Table("order_items", meta, Column("id", Integer),'
    ' Column("order_id", Integer), Column("qty", Integer))\n'
    'teklifler = Table("quotes", meta, Column("id", Integer),'
    ' Column("company_id", Integer))\n'
)
TEMEL_SORGU = (
    "def oku(baglanti, cid):\n"
    "    return baglanti.execute(\n"
    "        select(siparisler).where(siparisler.c.company_id == cid)\n"
    "    )\n"
)


def _tara(**dosyalar: str):
    return scan_sources(dict(dosyalar))


def _temel():
    kaynak = TABLO_TANIMI + TEMEL_SORGU
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    return kaynak, group(sorgular), desteksiz


def _fp(govde: str):
    s, d = _tara(**{"app/x.py": TABLO_TANIMI + govde})
    if d:
        return "DESTEKSIZ:" + d[0].tur
    return s[0].parmak_izi if s else None


B = "def f(baglanti, cid):\n    return baglanti.execute(\n        select(siparisler)"


# --- 1. Değişken taşıyan argümanlar --------------------------------------
def test_negatif_name_argumani_allowlist_disinda_kirmizi():
    kaynak = TABLO_TANIMI + (
        "def oku(baglanti, cid):\n"
        "    kosul = siparisler.c.id == 1\n"
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid, kosul)\n"
        "    )\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert len(sorgular) == 1
    assert [u.tur for u in desteksiz] == ["variable-arg"]
    assert "name:kosul" in desteksiz[0].aciklama


def test_negatif_starred_argumani_kirmizi():
    kaynak = TABLO_TANIMI + (
        "def oku(baglanti, cid):\n"
        "    kosullar = [siparisler.c.company_id == cid]\n"
        "    return baglanti.execute(select(siparisler).where(*kosullar))\n"
    )
    _, desteksiz = _tara(**{"app/x.py": kaynak})
    assert [u.tur for u in desteksiz] == ["variable-arg"]
    assert ":*" in desteksiz[0].aciklama


def test_negatif_cift_yildiz_argumani_kirmizi():
    kaynak = TABLO_TANIMI + (
        "def yaz(baglanti, cid, degerler):\n"
        "    return baglanti.execute(\n"
        "        update(siparisler).where(siparisler.c.company_id == cid)"
        ".values(**degerler)\n"
        "    )\n"
    )
    _, desteksiz = _tara(**{"app/x.py": kaynak})
    assert [u.tur for u in desteksiz] == ["variable-arg"]


def test_pozitif_gercek_allowlist_kayitlari_yesil(app_scan):
    """Canlı muafiyetler `variable-arg` üretmemeli."""
    _, _, desteksiz = app_scan
    assert not [u for u in desteksiz if u.tur == "variable-arg"]
    assert len(VARIABLE_ARG_ALLOWLIST) == 3


def test_negatif_liste_mutasyonu_sessiz_gecmiyor():
    """append/insert/extend/del ile üretilen argüman fail-closed olmalı."""
    for mutasyon in (
        "    kosullar.append(siparisler.c.id == 1)\n",
        "    kosullar.insert(0, siparisler.c.id == 1)\n",
        "    kosullar.extend([siparisler.c.id == 1])\n",
        "    del kosullar[0]\n",
        "    kosullar = [siparisler.c.id == 1]\n",
    ):
        kaynak = TABLO_TANIMI + (
            "def oku(baglanti, cid):\n"
            "    kosullar = [siparisler.c.company_id == cid]\n"
            + mutasyon
            + "    return baglanti.execute(select(siparisler).where(*kosullar))\n"
        )
        _, desteksiz = _tara(**{"app/x.py": kaynak})
        assert [u.tur for u in desteksiz] == ["variable-arg"], mutasyon


def test_negatif_allowlist_baska_fonksiyona_tasinirsa_kirmizi():
    """Muafiyet bağlam bağlıdır; aynı ifade başka fonksiyonda muaf değildir."""
    govde = (
        "    kosul = siparisler.c.id == 1\n"
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid, kosul)\n"
        "    )\n"
    )
    a, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def A(baglanti, cid):\n" + govde})
    b, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def B(baglanti, cid):\n" + govde})
    (ka,) = group(a)
    (kb,) = group(b)
    assert ka[1] == "A" and kb[1] == "B" and ka != kb


# --- 2. Aynı dosyada ikame ------------------------------------------------
def test_negatif_ayni_dosyada_baska_fonksiyona_tasima_yakalanir():
    govde = (
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
    )
    once = TABLO_TANIMI + "def guvenli(baglanti, cid):\n" + govde
    sonra = TABLO_TANIMI + "def HERKESE_ACIK(baglanti, cid):\n" + govde
    s1, _ = _tara(**{"app/x.py": once})
    s2, _ = _tara(**{"app/x.py": sonra})
    yeni, eksik, degismis = inventory_drift(group(s2), group(s1))
    assert yeni and eksik, "ikame yeni+eksik drift üretmeli"
    assert not degismis


def test_negatif_coklu_kimlikten_biri_tasinirsa_yakalanir():
    """Adet 2 iken biri silinip aynısı başka fonksiyona konursa görünür olmalı."""
    govde = (
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
    )
    once = TABLO_TANIMI + "def a(baglanti, cid):\n" + govde + govde.replace("return", "x =")
    once = TABLO_TANIMI + (
        "def a(baglanti, cid):\n"
        "    ilk = baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
        "    ikinci = baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
        "    return ilk, ikinci\n"
    )
    sonra = TABLO_TANIMI + (
        "def a(baglanti, cid):\n"
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
        "def SIZDIRAN(baglanti, cid):\n"
        "    return baglanti.execute(\n"
        "        select(siparisler).where(siparisler.c.company_id == cid)\n"
        "    )\n"
    )
    s1, _ = _tara(**{"app/x.py": once})
    s2, _ = _tara(**{"app/x.py": sonra})
    assert len(s1) == len(s2) == 2, "toplam adet aynı kalmalı — asıl tuzak bu"
    yeni, eksik, degismis = inventory_drift(group(s2), group(s1))
    assert yeni and degismis, "taşıma yeni kimlik + adet düşüşü üretmeli"


def test_pozitif_ayni_fonksiyonda_satir_yorum_bicim_drift_uretmez():
    _, temel, _ = _temel()
    varyantlar = [
        "\n\n\n" + TABLO_TANIMI + "\n\n\n" + TEMEL_SORGU,
        TABLO_TANIMI
        + (
            "# kiracıya göre okur\n"
            "def oku(baglanti, cid):\n"
            "    # tek kiracı\n"
            "    return baglanti.execute(\n"
            "        select(siparisler).where(siparisler.c.company_id == cid)  # süzgeç\n"
            "    )\n"
        ),
        TABLO_TANIMI
        + (
            "def oku(baglanti, cid):\n"
            "    return baglanti.execute(select(siparisler).where("
            "siparisler.c.company_id  ==  cid))\n"
        ),
    ]
    for v in varyantlar:
        sorgular, _ = _tara(**{"app/x.py": v})
        assert inventory_drift(group(sorgular), temel) == ([], [], []), v[:60]


def test_pozitif_ic_ice_fonksiyonlar_ayrilir():
    kaynak = TABLO_TANIMI + (
        "def dis(baglanti, cid):\n"
        "    def ic():\n"
        "        return baglanti.execute(select(siparisler))\n"
        "    return baglanti.execute(select(kalemler)), ic\n"
    )
    sorgular, _ = _tara(**{"app/x.py": kaynak})
    baglamlar = {q.hedef: q.baglam for q in sorgular}
    assert baglamlar == {"siparisler": "dis.ic", "kalemler": "dis"}


def test_pozitif_iki_siniftaki_ayni_metot_ayrilir():
    kaynak = TABLO_TANIMI + (
        "class A:\n"
        "    def oku(self, baglanti):\n"
        "        return baglanti.execute(select(siparisler))\n"
        "class B:\n"
        "    def oku(self, baglanti):\n"
        "        return baglanti.execute(select(siparisler))\n"
    )
    sorgular, _ = _tara(**{"app/x.py": kaynak})
    assert sorted(q.baglam for q in sorgular) == ["A.oku", "B.oku"]
    assert len(group(sorgular)) == 2, "aynı yapı iki ayrı kimlik olmalı"


def test_pozitif_modul_duzeyi_baglam_bos_string():
    kaynak = TABLO_TANIMI + "SORGU = select(siparisler)\n"
    sorgular, _ = _tara(**{"app/x.py": kaynak})
    assert [q.baglam for q in sorgular] == [""]


# --- 3. Core işlem adının yeniden bağlanması ------------------------------
def test_negatif_modul_duzeyi_yeniden_baglama_unsupported():
    kaynak = TABLO_TANIMI + "q = select\ndef f(b):\n    return b.execute(q(siparisler))\n"
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular
    assert [u.tur for u in desteksiz] == ["rebound-op"]


def test_negatif_fonksiyon_icinde_yeniden_baglama_unsupported():
    kaynak = TABLO_TANIMI + (
        "def f(b):\n    q = update\n    return b.execute(q(siparisler))\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular
    assert [u.tur for u in desteksiz] == ["rebound-op"]
    assert desteksiz[0].baglam == "f"


def test_negatif_alias_zinciri_unsupported():
    kaynak = TABLO_TANIMI + (
        "q = delete\nr = q\ndef f(b):\n    return b.execute(r(siparisler))\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular
    assert [u.tur for u in desteksiz] == ["rebound-op"]


def test_negatif_kapsayici_uzerinden_cagri_unsupported():
    for kapsayici in (
        'OPS = {"s": select}\ndef f(b):\n    return b.execute(OPS["s"](siparisler))\n',
        "OPS = [select]\ndef f(b):\n    return b.execute(OPS[0](siparisler))\n",
        "OPS = (select, update)\ndef f(b):\n    return b.execute(OPS[0](siparisler))\n",
    ):
        sorgular, desteksiz = _tara(**{"app/x.py": TABLO_TANIMI + kapsayici})
        assert not sorgular, kapsayici
        assert [u.tur for u in desteksiz] == ["rebound-op"], kapsayici


def test_pozitif_kullanilmayan_alias_sahte_sorgu_uretmez():
    kaynak = TABLO_TANIMI + "q = select\ndef f(b):\n    return b\n"
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular and not desteksiz


def test_pozitif_core_disi_yerel_select_alias_i_sahte_unsupported_uretmez():
    kaynak = (
        "def select(x):\n    return x\n"
        "q = select\n"
        "def f():\n    return q(5)\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular and not desteksiz


def test_negatif_golgeleme_fail_closed():
    """Core adı hem içe aktarılmış hem yerel tanımlıysa ayırt edilemez."""
    kaynak = TABLO_TANIMI + (
        "def select(x):\n    return x\n"
        "def f(b):\n    return b.execute(select(siparisler))\n"
    )
    _, desteksiz = _tara(**{"app/x.py": kaynak})
    assert "shadowed-op" in [u.tur for u in desteksiz]


# --- 4. Envanter kapalılığı ve parmak izi duyarlılığı ---------------------
def test_negatif_yeni_sorgu_yakalanir():
    kaynak, temel, _ = _temel()
    sorgular, _ = _tara(
        **{"app/x.py": kaynak + "\ndef ikinci(b):\n    return b.execute(select(kalemler))\n"}
    )
    yeni, eksik, degismis = inventory_drift(group(sorgular), temel)
    assert yeni and not eksik and not degismis


def test_negatif_silinen_sorgu_yakalanir():
    _, temel, _ = _temel()
    sorgular, _ = _tara(**{"app/x.py": TABLO_TANIMI})
    yeni, eksik, degismis = inventory_drift(group(sorgular), temel)
    assert eksik and not yeni and not degismis


def test_negatif_adet_degisimi_yakalanir():
    kaynak = TABLO_TANIMI + (
        "def oku(b, cid):\n"
        "    ilk = b.execute(select(siparisler).where(siparisler.c.company_id == cid))\n"
        "    return ilk\n"
    )
    ciftli = TABLO_TANIMI + (
        "def oku(b, cid):\n"
        "    ilk = b.execute(select(siparisler).where(siparisler.c.company_id == cid))\n"
        "    ikinci = b.execute(select(siparisler).where(siparisler.c.company_id == cid))\n"
        "    return ilk, ikinci\n"
    )
    t, _ = _tara(**{"app/x.py": kaynak})
    c, _ = _tara(**{"app/x.py": ciftli})
    yeni, eksik, degismis = inventory_drift(group(c), group(t))
    assert degismis and not yeni and not eksik
    assert "(1," in degismis[0] and "(2," in degismis[0]


def test_negatif_dosya_tasinmasi_yakalanir():
    _, temel, _ = _temel()
    sorgular, _ = _tara(**{"app/y.py": TABLO_TANIMI + TEMEL_SORGU})
    yeni, eksik, _ = inventory_drift(group(sorgular), temel)
    assert yeni and eksik


def test_negatif_parmak_izi_yapisal_degisimlere_duyarli():
    durumlar = [
        ("where kaldırma", ".where(siparisler.c.company_id == cid)", ""),
        (
            "company_id yerine id",
            ".where(siparisler.c.company_id == cid)",
            ".where(siparisler.c.id == cid)",
        ),
        (
            "operatör",
            ".where(siparisler.c.company_id == cid)",
            ".where(siparisler.c.company_id != cid)",
        ),
        (
            "AND -> OR",
            ".where(and_(siparisler.c.company_id == cid, siparisler.c.id == 1))",
            ".where(or_(siparisler.c.company_id == cid, siparisler.c.id == 1))",
        ),
        (
            "join hedefi",
            ".join(kalemler, kalemler.c.order_id == siparisler.c.id)",
            ".join(teklifler, teklifler.c.id == siparisler.c.id)",
        ),
        (
            "join koşulu",
            ".join(kalemler, kalemler.c.order_id == siparisler.c.id)",
            ".join(kalemler, kalemler.c.id == siparisler.c.id)",
        ),
        ("order_by", ".order_by(siparisler.c.id)", ".order_by(siparisler.c.total)"),
        ("group_by", ".group_by(siparisler.c.id)", ".group_by(siparisler.c.total)"),
        ("having", ".having(siparisler.c.total > 1)", ".having(siparisler.c.total > 2)"),
        ("with_for_update", "", ".with_for_update()"),
        (
            "subquery içi",
            ".where(siparisler.c.id.in_(select(kalemler.c.order_id)"
            ".where(kalemler.c.qty == 1)))",
            ".where(siparisler.c.id.in_(select(kalemler.c.order_id)"
            ".where(kalemler.c.qty == 2)))",
        ),
    ]
    for ad, a, b in durumlar:
        fa = _fp(B + a + "\n    )\n")
        fb = _fp(B + b + "\n    )\n")
        assert fa != fb, f"parmak izi değişmedi: {ad}"


def test_negatif_hedef_tablo_degisimi_yakalanir():
    a, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def f(b):\n    return b.execute(select(siparisler))\n"})
    c, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def f(b):\n    return b.execute(select(teklifler))\n"})
    yeni, eksik, _ = inventory_drift(group(c), group(a))
    assert yeni and eksik
    assert a[0].hedef == "siparisler" and c[0].hedef == "teklifler"


def test_negatif_islem_turu_degisimi_yakalanir():
    a, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def f(b):\n    return b.execute(select(siparisler))\n"})
    c, _ = _tara(**{"app/x.py": TABLO_TANIMI + "def f(b):\n    return b.execute(delete(siparisler))\n"})
    yeni, eksik, _ = inventory_drift(group(c), group(a))
    assert yeni and eksik


def test_negatif_cozulemeyen_hedef_sessizce_gecmez():
    kaynak = (
        "from sqlalchemy import select\n"
        "def oku(b, secici, cid):\n"
        "    return b.execute(select(secici(cid)))\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert [q.hedef for q in sorgular] == [None]
    assert [u.tur for u in desteksiz] == ["unresolved-target"]


def test_negatif_metot_bicimi_desteksiz_sayilir():
    kaynak = TABLO_TANIMI + (
        "def oku(b):\n    b.execute(siparisler.update())\n    b.execute(siparisler.delete())\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular
    assert [u.tur for u in desteksiz] == ["method-form", "method-form"]


def test_negatif_bos_tarama_kapiyi_kirar():
    sorgular, _ = _tara(**{"app/x.py": "x = 1\n"})
    assert not sorgular
    _, eksik, _ = inventory_drift(group(sorgular), EXPECTED_QUERIES)
    assert eksik, "boş tarama tüm envanteri eksik göstermeli — sahte yeşil olmamalı"


def test_negatif_bos_envanter_kapiyi_kirar(app_scan):
    _, sorgular, _ = app_scan
    yeni, eksik, _ = inventory_drift(group(sorgular), {})
    assert len(yeni) == len(group(sorgular)) and not eksik


def test_negatif_ayristirilamayan_dosya_sessizce_atlanmaz():
    _, desteksiz = _tara(**{"app/x.py": "def bozuk(:\n"})
    assert [u.tur for u in desteksiz] == ["parse-error"]


# --- 5. İç içe sorgular ve içe aktarma biçimleri --------------------------
def test_pozitif_ic_ice_sorgular_ayri_sayilir():
    kaynak = TABLO_TANIMI + (
        "def f(b, cid):\n"
        "    alt = select(kalemler.c.id).where(kalemler.c.order_id == 1).scalar_subquery()\n"
        "    return b.execute(select(siparisler).where(siparisler.c.id == alt))\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert len(sorgular) == 2, "iç sorgu ve dış sorgu ayrı ayrı sayılmalı"
    assert not desteksiz


def test_pozitif_karsilastirma_operandi_degisken_arguman_sayilmaz():
    """``where(sutun == degisken)`` opak yüklem DEĞİLDİR — sınırı sabitler.

    Yüklemin **yapısı** (hangi sütun, hangi operatör) parmak izinde görünür;
    ``degisken`` yalnız bağlanan bir değerdir. Opak sayılan tek şey, argümanın
    **kendisinin** bir ``Name``/``*``/``**`` olmasıdır — o durumda yüklemin
    yapısı hiç görünmez. Bu ayrım olmasaydı her ``== cid`` karşılaştırması
    muafiyet gerektirir ve kural kullanılamaz hâle gelirdi.
    """
    goruntulu, d1 = _tara(
        **{
            "app/x.py": TABLO_TANIMI
            + "def f(b, cid):\n"
            "    return b.execute(select(siparisler).where(siparisler.c.company_id == cid))\n"
        }
    )
    opak, d2 = _tara(
        **{
            "app/x.py": TABLO_TANIMI
            + "def f(b, kosul):\n"
            "    return b.execute(select(siparisler).where(kosul))\n"
        }
    )
    assert not d1, "değer operandı muafiyet gerektirmemeli"
    assert [u.tur for u in d2] == ["variable-arg"], "opak yüklem fail-closed olmalı"
    assert len(goruntulu) == len(opak) == 1


def test_pozitif_gomulu_subquery_ayri_sayilir():
    kaynak = TABLO_TANIMI + (
        "def f(b, cid):\n"
        "    return b.execute(\n"
        "        select(siparisler).where("
        "siparisler.c.id.in_(select(kalemler.c.order_id)))\n"
        "    )\n"
    )
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert len(sorgular) == 2 and not desteksiz


def test_pozitif_ice_aktarma_bicimleri_taranir():
    durumlar = [
        (
            "from sqlalchemy import select as sec\n"
            "from sqlalchemy import MetaData, Table, Column, Integer\n"
            "meta = MetaData()\n"
            't = Table("orders", meta, Column("id", Integer))\n'
            "def f(b):\n    return b.execute(sec(t))\n"
        ),
        (
            "import sqlalchemy\n"
            "from sqlalchemy import MetaData, Table, Column, Integer\n"
            "meta = MetaData()\n"
            't = Table("orders", meta, Column("id", Integer))\n'
            "def f(b):\n    return b.execute(sqlalchemy.select(t))\n"
        ),
        (
            "from sqlalchemy.sql import select\n"
            "from sqlalchemy import MetaData, Table, Column, Integer\n"
            "meta = MetaData()\n"
            't = Table("orders", meta, Column("id", Integer))\n'
            "def f(b):\n    return b.execute(select(t))\n"
        ),
    ]
    for kaynak in durumlar:
        sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
        assert len(sorgular) == 1 and not desteksiz, kaynak[:40]
        assert sorgular[0].hedef == "t"


def test_pozitif_sqlalchemy_disi_select_sayilmaz():
    kaynak = "def select(x):\n    return x\ndef oku():\n    return select(5)\n"
    sorgular, desteksiz = _tara(**{"app/x.py": kaynak})
    assert not sorgular and not desteksiz
