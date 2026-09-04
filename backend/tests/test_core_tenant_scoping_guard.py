"""Enforcement guard: SQLAlchemy **Core** ifadeleri de kiracıya bağlı olmalı.

`test_tenant_scoping_guard` elle yazılmış `text()` SQL'ini kapatıyor. Ölçüldü
(2026-08-12): uygulamada 56 Core `select/insert/update/delete` çağrısı 14
kiracı tablosuna dokunuyor ve bunların HİÇBİRİ o kapıdan geçmiyordu — kapı
yalnız `text()` görüyor. 57.'yi yazmak sıradan iş olduğu için bu, kod
tabanının en doğal kör noktasıydı.

Satır düzeyi güvenlik (RLS) eklenmeme kararı bu kapıyı taşıyıcı yapıyor:
unutulmuş bir `WHERE`'i çalışma zamanında durduracak bir şey yok, o hâlde
derleme zamanında durmalı.

FAIL-CLOSED — bu kapının bütün meselesi budur:

    Çözemediği ifade İHLALDİR, atlanan ifade değil.

Tablo bir değişkenden geliyorsa, yüklem başka bir yerde kuruluyorsa, ifade
fonksiyonlar arasına yayılmışsa: kırmızı. Literal SQL analizcisi de böyle
davranıyor (`_literal_sql` çözemediğinde çağrı dinamik sayılıp ayrı bir
envantere düşer, sessizce geçmez); bu kapı onunla aynı hizada.

KAPILAR
    1. Kiracı tablosuna dokunan Core sorgusunda bağlı `company_id` yüklemi yoksa
       → kırmızı.
    2. JOIN'de yalnız bir taraf kapsanmışsa → kırmızı.
    3. Analizcinin çözemediği ifade → kırmızı.
    4. Kapsam SABİTLE veriliyorsa (`company_id == 1`) → kırmızı; literal
       analizcinin `company_id=3`'ü reddetmesiyle aynı gerekçe: sabit, isteğin
       kiracısına bağlı değildir.

Kiracı tablosu listesi ELLE TUTULMUYOR: `test_tenant_scoping_guard` içindeki
`TENANT_TABLES` doğrudan import ediliyor ve o liste zaten göç etmiş şemaya
karşı iki yönlü doğrulanıyor (`test_tenant_table_inventory_matches_migrated_schema`).
Yeni bir kiracı tablosu eklendiğinde bu kapı da onu aynı anda görmeye başlar.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import symtable

import pytest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"

_spec = importlib.util.spec_from_file_location(
    "_kiraci_sql_kapisi", Path(__file__).with_name("test_tenant_scoping_guard.py")
)
_modul = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_modul)
TENANT_TABLES: frozenset[str] = _modul.TENANT_TABLES

# Core ifade üreticileri. `db.execute(...)` sarmalayıcısı değil, ifadenin
# KENDİSİ aranıyor; sarmalayıcıya bakmak zincirin tamamını kaçırırdı.
URETICILER = {"select", "insert", "update", "delete"}

# İfadenin kiracıya bağlandığını kabul eden sütun.
KIRACI_SUTUNU = "company_id"

# PYTHON ADI ≠ SQL TABLO ADI. Kapı düğümün ADINA bakar; `Table("x", ...)`
# başka bir değişkene bağlanmışsa tablo kapıya GÖRÜNMEZ. Bu bir muafiyet
# değil KÖRLÜKTÜR: sorgu izin verildiği için değil, sorulmadığı için geçer.
#
# ÖLÇÜLDÜ: app/ içinde adı tablo adından farklı olan DÖRT bağ var; ikisi
# kiracı tablosuna çözülüyor (aşağıdaki envanter testi bunu çiviliyor).
# Burada YALNIZ bu PR'ın sınırını getirdiği tablo çözülür.
TABLO_TAKMA_ADLARI: dict[str, str] = {
    "audit_logs": "security_audit_logs",
    # ÇÖZÜLDÜ (eskiden ZORLANMAYAN_TAKMA_ADLAR'daydı ve tablo GÖRÜNMEZDİ).
    "memberships": "user_company_memberships",
}

# ÇÖZÜCÜNÜN BİLDİRİLMİŞ SINIRLARI.
#
# `TABLO_TAKMA_ADLARI` yalnız DOĞRUDAN bağı çözer: `X = Table("y")` ve o adın
# sorguda doğrudan kullanılmasını. Tablo NESNESİ başka bir ada ya da yuvaya
# taşınırsa, sorgu yerinde kapının tabloya bağlayamayacağı bir ad görünür ve
# ifade sessizce görünmez olur.
#
# Bu sınırlar BİLEREK kapatılmadı (aşağıdaki not), ama BİLDİRİLMEDEN de
# bırakılmadı: bu PR'ın kapattığı kusur, tam olarak bildirilmemiş bir kör
# noktanın bir sorguyu incelenmeden geçirmesiydi. Aynı kapıda ikinci bir
# bildirilmemiş kör nokta bırakmak tutarsız olurdu — okuyan kişi "artık takma
# adlar çözülüyor" görüp kendi aliaslı sorgusunun denetlendiğini sanardı.
#
# ON YEDİ BİÇİM. Beşi ilk turda bildirildi, altısı ikinci turda ÖLÇÜMLE
# eklendi, altısı da üçüncü turda — yine ölçümle, yine aynı kusur sınıfından.
# Üçüncü turun bulgusu şuydu: BİLDİRİLEN bir biçimin SÖZDİZİMSEL İKİZİ
# ölçülmüyordu. `def f(): return T` sayılıyordu ama `lambda: T` sayılmıyordu;
# `T if k else X` sayılıyordu ama `T or X` sayılmıyordu; `{"k": T}` sayılıyordu
# ama `{T: 1}` ve `dict(k=T)` sayılmıyordu. Bunlar AYNI ŞEYİN başka yazılışı.
# Bildirilen bir sınırın yarısını ölçmek, bu kapının ikinci kez düştüğü kusur.
#
#   1. İKİNCİL YENİDEN TAKMA:      t2 = audit_logs
#   2. KOŞULLU ATAMA:              if kosul:\n        t = audit_logs
#   3. ÜÇLÜ İFADE:                 t = audit_logs if kosul else baska_tablo
#   4. FONKSİYON DÖNÜŞÜ:           def tablo(): return audit_logs
#   5. SÖZLÜKTE SAKLAMA:           TABLOLAR = {"denetim": audit_logs}
#   6. YÜRÜYEN ATAMA:              if (t := audit_logs): ...
#   7. DEMET AÇMA:                 t, _ = audit_logs, 1
#   8. AÇIKLAMALI ATAMA:           t: Table = audit_logs
#   9. PARAMETRE VARSAYILANI:      def f(t=audit_logs): ...
#  10. ÖZNİTELİK ATAMASI:          obj.attr = audit_logs
#  11. ABONELİK ATAMASI:           d["k"] = audit_logs
#  12. LAMBDA GÖVDESİ:             f = lambda: audit_logs        (4'ün ikizi)
#  13. VE/VEYA:                    t = audit_logs or None        (3'ün ikizi)
#  14. KÜMEDE SAKLAMA:             t = {audit_logs}              (5'in ikizi)
#  15. SÖZLÜK ANAHTARI:            t = {audit_logs: 1}           (5'in ikizi)
#  16. ÇAĞRI ANAHTAR ARGÜMANI:     dict(k=audit_logs)            (5/9'un ikizi)
#  17. DESEN BAĞLAMA:              match audit_logs:\n    case x: ...
#  18. DEKORATÖR:                  @audit_logs / def f(): ...
#  19. SINIF TABANI:               class C(audit_logs): ...
#
# ÖLÇÜT SAYILMAZ, TÜRETİLİR — ve bu, listenin kendisinden ÖNEMLİDİR.
# Yukarıdaki liste okuyana somutluk verir; dedektör onu TARAMAZ. Düğüm
# tiplerini saymak, bu kusuru üreten allowlist duruşunun ta kendisiydi.
#
# CPython bağlamayı İKİ BİÇİMDE gösterir ve ölçüt İKİSİNİ DE kullanır:
#
#   (1) İFADELERDE: bağlanan ada `ctx=Store`, parametreye `ast.arg`.
#   (2) DÜZ `str` ALANLARDA: `MatchAs.name`, `ExceptHandler.name`,
#       `Global.names`, `alias.asname`, `FunctionDef.name`, `ClassDef.name`.
#       Bunlarda `ctx` YOKTUR. `ast.arg` de bu sınıftandı ve TEK TEK
#       yamanmıştı; tek tek yamamak kardeşlerini geride bıraktı. Bu yüzden
#       ikinci gösterim de TÜRETİLİR: ifade OLMAYAN bir düğümün, `symtable`'a
#       göre bu modülde BAĞLANAN bir ada eşit `str` alanı bir bağlama
#       konumudur. `symtable`, `ctx=Store`'un düz-`str` dünyasındaki karşılığı
#       — ikisi de CPython'un kendi cevabı, ikisi de bizim listemiz değil.
#
# Ölçüt bu yüzden TEK CÜMLEDİR:
#
#   Dilbilgisinin SOL tarafı saydığı herhangi bir konumda bağlama varken,
#   DEĞER tarafında tabloyu ADLANDIRAN ÇIPLAK bir ad görünüyorsa, say.
#
# `select(t.c.x)` gibi sıradan kullanım GİRMEZ — çağrının KONUMSAL argümanına
# ve özniteliğe İNİLMEZ; kapı onu zaten okuyor. (Ölçüldü: `select(t.c.id)`,
# `t.select()`, `t.c.company_id`, `t.join(...)`, listede, sözlükte ve
# `select(audit_logs)` — SIFIR bulgu.)
#
# BİLDİRİLEN SINIRLAR — ÖLÇÜLDÜ, KAPATILMADI, SESSİZ DE BIRAKILMADI.
# Aşağıdakiler bugün de KAÇAR. Bunları bildirmek, kapatmamanın bedelini
# görünür kılar; sessiz bırakmak bu PR'ın kapattığı kusurun kendisi olurdu:
#
#   - `t = getattr(m, "audit_logs")`  — dinamik; statik ağaçta ad YOKTUR.
#   - `t = audit_logs.alias()`        — ÇAĞRI SONUCU. Çözücü genişletilmedi;
#                                       bu, `memberships` körlüğüyle aynı
#                                       dilime ait AYRI bir iştir.
#   - `t = list((audit_logs,))`       — çağrının KONUMSAL argümanı. Bilerek
#                                       dışarıda: `select(audit_logs)` sıradan
#                                       kullanımdır ve app/ içinde GERÇEKTEN
#                                       vardır; inmek onu da kırmızı yapardı.
#   - `t = [audit_logs][0]`           — kapsayıcıyı yazıp hemen OKUMA.
#
# ÖLÇÜLEN AŞIRI-YAKLAŞIM, bilerek bırakıldı. Ölçüt `for satir in audit_logs`,
# `with audit_logs as a`, `[x for x in audit_logs]` ve `birikim += audit_logs`
# biçimlerini de sayar. Bunlarda bağlanan ad tabloyu ADLANDIRMAZ, yani bunlar
# 19 biçimin tanımına girmez. `except audit_logs as e` de bu sınıftan. Yine de DIŞLANMADILAR: dışlamak, tam da
# terk edilen "saydığım düğümler" duruşuna geri dönmek olurdu. Aşırı-yaklaşım
# KAPALI YÖNE çalışır — kapıyı yalnız KIRMIZIYA çevirebilir, asla yeşile.
# Bugün app/ içinde bunların da sayısı SIFIR.
COZUCU_SINIR_BICIMLERI = (
    "ikincil yeniden takma (t2 = audit_logs)",
    "koşullu atama (if ...: t = audit_logs)",
    "üçlü ifade (t = audit_logs if ... else ...)",
    "fonksiyon dönüşü (return audit_logs)",
    "sözlükte saklama ({\"k\": audit_logs})",
    "yürüyen atama (if (t := audit_logs): ...)",
    "demet açma (t, _ = audit_logs, 1)",
    "açıklamalı atama (t: Table = audit_logs)",
    "parametre varsayılanı (def f(t=audit_logs): ...)",
    "öznitelik ataması (obj.attr = audit_logs)",
    "abonelik ataması (d[\"k\"] = audit_logs)",
    "lambda gövdesi (f = lambda: audit_logs)",
    "ve/veya (t = audit_logs or None)",
    "kümede saklama (t = {audit_logs})",
    "sözlük anahtarı (t = {audit_logs: 1})",
    "çağrı anahtar argümanı (dict(k=audit_logs))",
    "desen bağlama (match audit_logs: case x)",
    "dekoratör (@audit_logs)",
    "sınıf tabanı (class C(audit_logs))",
)

# BİLDİRİLEN VE KAPATILMAYAN SINIRLAR. Ölçülerek yazıldı; her biri
# `test_bildirilen_sinirlar_HALA_kaciyor` testinde ÇİVİLİ — biri sessizce
# kapanırsa (ya da kapandığı sanılırsa) o test kırmızı yanar ve bu liste
# gerçekle yeniden hizalanır.
COZUCU_KAPATILMAYAN_SINIRLAR = (
    "dinamik erişim (getattr(m, \"audit_logs\"))",
    "çağrı sonucu (t = audit_logs.alias())",
    "çağrının konumsal argümanı (list((audit_logs,)))",
    "kapsayıcıyı okuma (t = [audit_logs][0])",
)

# ÖLÇÜLDÜ: backend/app içinde bu biçimlerin HİÇBİRİ bugün yok. Sıfır da
# DONDURULUR — yazılmayan bir sıfır geri büyür.
BEKLENEN_COZUCU_SINIR_IHLALI = 0


def _modul_tablo_baglari(kok: Path) -> dict[str, dict[str, str]]:
    """Her modülün MODÜL DÜZEYİNDEKİ ``X = Table("y")`` bağları."""
    sonuc: dict[str, dict[str, str]] = {}
    for yol in sorted(kok.rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        anahtar = yol.relative_to(kok).with_suffix("").as_posix().replace("/", ".")
        baglar: dict[str, str] = {}
        for dugum in agac.body:  # YALNIZ modül düzeyi
            if not (isinstance(dugum, ast.Assign) and isinstance(dugum.value, ast.Call)):
                continue
            fn = dugum.value.func
            ad = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if ad != "Table" or not dugum.value.args:
                continue
            ilk = dugum.value.args[0]
            if not (isinstance(ilk, ast.Constant) and isinstance(ilk.value, str)):
                continue
            for hedef in dugum.targets:
                if isinstance(hedef, ast.Name):
                    baglar[hedef.id] = ilk.value
        sonuc[anahtar] = baglar
    return sonuc


def _saklama_alani_mi(deger: object) -> bool:
    """Bu ALAN dilbilgisinin BAĞLAMA (sol) tarafı mı? — İFADE düğümleri için.

    Düğüm tipi SORULMAZ. Python bağlanan ada ``ctx=Store``, bağlanan
    parametreye ``ast.arg`` verir; işaret zaten oradadır.

    Bu ölçüt CPython'un İKİ bağlama gösteriminden YALNIZ BİRİNİ görür. İkincisi
    için ``_str_baglama_hedefleri``'ne bakınız.
    """
    if isinstance(deger, ast.arg):
        return True
    if isinstance(deger, ast.expr):
        return isinstance(getattr(deger, "ctx", None), ast.Store)
    if isinstance(deger, list) and deger:
        return all(_saklama_alani_mi(o) for o in deger)
    return False


# BUNLAR HATA DEĞİL. Yukarıdaki ölçüt `for satir in audit_logs`,
# `with audit_logs as a`, `[x for x in audit_logs]` ve `birikim += audit_logs`
# biçimlerini de sayar; bu dördünde bağlanan ad tabloyu ADLANDIRMAZ, yani
# bildirilen biçimlerin tanımına girmezler. Dışlamak için "Assign say, For
# sayma" yazmak gerekirdi — bu kapının terk ettiği allowlist duruşunun ta
# kendisi. Aşırı-yaklaşım KAPALI yöne çalışır: kapıyı yalnız KIRMIZIYA
# çevirebilir, asla yeşile. Gerekçenin tamamı `COZUCU_SINIR_BICIMLERI`
# bildiriminin başındadır.

# AÇIKLAMA ALANLARI DEĞER DEĞİLDİR. `t: audit_logs = None` ifadesinde bağlanan
# şey `None`'dır; tablo adı yalnız TÜR AÇIKLAMASINDA geçer. Bunu değer saymak
# ÖLÇÜLMÜŞ bir yanlış pozitifti. CPython açıklamayı tam iki alanda taşır:
# `annotation` (AnnAssign, arg) ve `returns` (FunctionDef, AsyncFunctionDef);
# ikisi de DEĞERLENDİRİLİR ama HİÇBİR ADA BAĞLANMAZ. Liste bu yüzden kapalıdır
# ve düğüm tipi saymaz — alanın ADI üzerinden çalışır.
_ACIKLAMA_ALANLARI = frozenset({"annotation", "returns"})


def _baglanan_adlar(kaynak: str, dosya: str) -> frozenset[str]:
    """Bu modülde BAĞLANAN adlar — CPython'un kendi otoritesinden.

    ``ctx=Store`` ifadeler için CPython'un bağlama işaretidir; ``symtable`` ise
    AYNI sorunun modül düzeyindeki cevabıdır ve düz ``str`` alanlarla gösterilen
    bağlamaları da bilir. İkisi de TÜRETİLMİŞ ölçüttür; ikisi de "hangi düğüm
    tipleri bağlar" listesi TUTMAZ.
    """
    try:
        kok = symtable.symtable(kaynak, dosya, "exec")
    except (SyntaxError, ValueError):  # pragma: no cover - savunma
        return frozenset()
    adlar: set[str] = set()
    yigin = [kok]
    while yigin:
        kapsam = yigin.pop()
        for sembol in kapsam.get_symbols():
            if sembol.is_assigned() or sembol.is_parameter() or sembol.is_imported():
                adlar.add(sembol.get_name())
        yigin.extend(kapsam.get_children())
    return frozenset(adlar)


def _str_baglama_hedefleri(dugum: ast.AST, baglanan: frozenset[str]) -> list[str]:
    """CPython'un İKİNCİ bağlama gösterimi: düz ``str`` alanlar.

    ``MatchAs.name``, ``ExceptHandler.name``, ``Global.names``,
    ``Nonlocal.names``, ``alias.asname``, ``FunctionDef.name``,
    ``ClassDef.name`` — hepsi ad BAĞLAR ama hiçbirinde ``ctx`` YOKTUR;
    ``ast.arg`` da bu sınıftandı ve TEK TEK yamanmıştı. Tek tek yamamak
    kardeşlerini geride bırakır, bu yüzden ölçüt TÜRETİLİR:

        İFADE OLMAYAN bir düğümün, bu modülde BAĞLANAN bir ada eşit ``str``
        alanı, bir bağlama konumudur.

    "İfade olmayan" şartı zorunludur ve kendisi de türetilmiştir: ifadeler ad
    BAĞLAMAZ. O şart olmadan ``Attribute.attr``, ``Name.id``, ``keyword.arg``
    ve ``Constant.value`` — hepsi ``str`` — bağlama sanılırdı.
    """
    if isinstance(dugum, ast.expr):
        return []
    if isinstance(dugum, ast.keyword):
        # `f(k=...)` içindeki `k`, BU modülde değil ÇAĞRILANIN kapsamında
        # bağlanır; bu modülün sembol tablosuyla eşleşmesi kanıt değil RASTLANTI
        # olurdu. Anahtar argümanın gerçek anlamı `_cagri_anahtar_tablolari`
        # kuralındadır. Bu, düğüm tipi dışlaması değil KAPSAM ölçütüdür:
        # başka bir kapsamda bağlanan ad, burada bağlama sayılmaz.
        return []
    bulunan: list[str] = []
    for alan, icerik in ast.iter_fields(dugum):
        if alan in _ACIKLAMA_ALANLARI:
            continue
        if isinstance(icerik, str):
            if icerik in baglanan:
                bulunan.append(icerik)
        elif isinstance(icerik, list):
            for oge in icerik:
                if isinstance(oge, str) and oge in baglanan:
                    bulunan.append(oge)
    return bulunan


def _desen_baglamalari(dugum: ast.AST, baglanan: frozenset[str]) -> list[str]:
    """Desen bağlamaları, DESTEKLEDİKLERİ yapıya aittir.

    ``match audit_logs:`` / ``case x:`` ifadesinde bağlanan ad (``x``) ile
    bağlandığı DEĞER (``subject``) aynı düğümde DEĞİLDİR; değer iki düzey
    yukarıdadır. Bu yüzden desen adları, onları destekleyen yapıya taşınır.
    Gezinti DEYİM sınırında durur — böylece bir fonksiyonun gövdesindeki desen
    o fonksiyona mal edilmez.
    """
    bulunan: list[str] = []

    def gez(d: ast.AST) -> None:
        for cocuk in ast.iter_child_nodes(d):
            if isinstance(cocuk, ast.stmt):
                continue  # DEYİM sınırını geçme
            if isinstance(cocuk, ast.pattern):
                bulunan.extend(_str_baglama_hedefleri(cocuk, baglanan))
            gez(cocuk)

    gez(dugum)
    return bulunan


def _yol_birlestir(yol: str, ek: str) -> str:
    return ek if yol == "doğrudan" else f"{yol}>{ek}"


def _deger_tablolari(
    deger: object, kapsam: dict[str, str], yol: str = "doğrudan",
) -> list[tuple[str, str]]:
    """DEĞER tarafında ÇIPLAK görünen tablo adları — ``(ad, yol)``.

    SAYDAM kapsayıcılara inilir: üçlünün dalları, ``and``/``or`` işlenenleri,
    demet/liste/küme öğeleri, yıldızlı öğe ve LAMBDA GÖVDESİ. Bunlar bildirilen
    biçimlerin İKİZLERİDİR ve ayrı ölçülmeleri için sebep yoktur:
    ``lambda: T`` ile ``def f(): return T`` aynı şeydir, ``T or None`` ile
    ``T if k else None`` aynı şeydir, ``{T}`` ile ``[T]`` aynı şeydir.

    ÇAĞRININ KONUMSAL argümanına İNİLMEZ: ``select(audit_logs)`` sıradan
    kullanımdır ve kapı onu ZATEN okur. Çağrının ANAHTAR argümanı ayrı bir
    kuraldır (``_cagri_anahtar_tablolari``), çünkü o, çağrılanın kapsamında bir
    PARAMETRE bağlar. Özniteliğe de inilmez: ``t.c.id`` sıradan kullanımdır.
    """
    if isinstance(deger, ast.Name):
        return [(deger.id, yol)] if deger.id in kapsam else []
    if isinstance(deger, ast.IfExp):
        alt = _yol_birlestir(yol, "üçlü")
        return (
            _deger_tablolari(deger.body, kapsam, alt)
            + _deger_tablolari(deger.orelse, kapsam, alt)
        )
    if isinstance(deger, ast.BoolOp):
        alt = _yol_birlestir(yol, "ve-veya")
        sonuc: list[tuple[str, str]] = []
        for islenen in deger.values:
            sonuc += _deger_tablolari(islenen, kapsam, alt)
        return sonuc
    if isinstance(deger, ast.Lambda):
        return _deger_tablolari(
            deger.body, kapsam, _yol_birlestir(yol, "lambda-gövdesi")
        )
    if isinstance(deger, (ast.Tuple, ast.List, ast.Set)):
        alt = _yol_birlestir(yol, "demet-öğesi")
        sonuc = []
        for oge in deger.elts:
            sonuc += _deger_tablolari(oge, kapsam, alt)
        return sonuc
    if isinstance(deger, ast.Starred):
        return _deger_tablolari(deger.value, kapsam, yol)
    if isinstance(deger, list):
        sonuc = []
        for oge in deger:
            if isinstance(oge, ast.expr):
                sonuc += _deger_tablolari(oge, kapsam, yol)
        return sonuc
    return []


def _cagri_anahtar_tablolari(
    dugum: ast.AST, kapsam: dict[str, str],
) -> list[tuple[str, str]]:
    """``dict(k=audit_logs)`` — ANAHTAR argüman, çağrılanın kapsamında bağlar.

    ``{"k": audit_logs}`` ile ``dict(k=audit_logs)`` aynı şeydir; birincisi
    bildirilen biçimdi, ikincisi kaçıyordu. ``f(t=audit_logs)`` ise bildirilen
    "parametre varsayılanı" biçiminin ÇAĞRI yerindeki hâlidir. KONUMSAL
    argüman bilerek DIŞARIDA: ``select(audit_logs)`` sıradan kullanımdır ve
    ``backend/app`` içinde GERÇEKTEN vardır.
    """
    if not isinstance(dugum, ast.Call):
        return []
    sonuc: list[tuple[str, str]] = []
    for anahtar in dugum.keywords:
        for ad, yol in _deger_tablolari(anahtar.value, kapsam):
            sonuc.append((ad, _yol_birlestir(yol, "çağrı-anahtarı")))
    return sonuc


# Etiket SÖZLÜKLERİ yalnız OKUNABİLİRLİK içindir; TESPİT bunlara bakmaz.
# Bilinmeyen bir yapı yakalandığında etiket AST sınıf adına düşer — yapı yine
# SAYILIR. Ölçütün türetilmiş olması tam olarak bu yüzden korunur.
_BAGLAMA_ADLARI = {
    "Assign": "atama", "AnnAssign": "açıklamalı-atama",
    "AugAssign": "artırmalı-atama", "NamedExpr": "yürüyen-atama",
    "arguments": "parametre-varsayılanı", "For": "döngü", "AsyncFor": "döngü",
    "comprehension": "kurgu", "withitem": "with", "Match": "eşleme",
}
_HEDEF_ADLARI = {
    "Name": "ad", "Attribute": "öznitelik", "Subscript": "abonelik",
    "Tuple": "demet", "List": "liste", "Starred": "yıldızlı", "arg": "parametre",
    "str": "ad",
}


def _hedef_dugumleri(deger: object) -> list[object]:
    if isinstance(deger, list):
        sonuc: list[object] = []
        for oge in deger:
            sonuc += _hedef_dugumleri(oge)
        return sonuc
    return [deger]


def _satir_haritasi(agac: ast.AST) -> dict[int, int]:
    """Her düğüm için EN YAKIN satır. ``arguments``/``withitem``/
    ``comprehension`` düğümlerinin kendi ``lineno``'su yoktur; kapsayanınkini
    devralırlar."""
    harita: dict[int, int] = {}

    def gez(dugum: ast.AST, satir: int) -> None:
        satir = getattr(dugum, "lineno", satir)
        harita[id(dugum)] = satir
        for cocuk in ast.iter_child_nodes(dugum):
            gez(cocuk, satir)

    gez(agac, 0)
    return harita


def cozucu_sinir_ornekleri(kok: Path = APP_DIR) -> list[str]:
    """Bildirilen biçimlerin GERÇEK örnekleri; her biri "yol:satır biçim"."""
    modul_baglari = _modul_tablo_baglari(kok)
    bulgular: list[str] = []

    for yol in sorted(kok.rglob("*.py")):
        kaynak = yol.read_text(encoding="utf-8")
        agac = ast.parse(kaynak, filename=str(yol))
        anahtar = yol.relative_to(kok).with_suffix("").as_posix().replace("/", ".")
        kapsam = dict(modul_baglari.get(anahtar, {}))
        # içe aktarılan tablo adları da bu dosyada tabloyu adlandırır
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.ImportFrom):
                for ad in dugum.names:
                    for baglar in modul_baglari.values():
                        if ad.name in baglar:
                            kapsam[ad.asname or ad.name] = baglar[ad.name]
        if not kapsam:
            continue

        try:
            bagil = yol.relative_to(BACKEND.parent).as_posix()
        except ValueError:
            # Sentetik ağaç depo dışında; karşı-örnek testi için ad yeterli.
            bagil = yol.as_posix()

        satirlar = _satir_haritasi(agac)
        baglanan = _baglanan_adlar(kaynak, str(yol))

        for dugum in ast.walk(agac):
            satir = satirlar.get(id(dugum), 0)

            # (a) BAĞLAMA: ölçüt türetilir — ctx=Store / ast.arg (ifadeler) VE
            #     symtable'a göre bağlayan düz `str` alanlar (ifade olmayanlar).
            hedefler: list[object] = []
            degerler: list[object] = []
            for alan, icerik in ast.iter_fields(dugum):
                if _saklama_alani_mi(icerik):
                    hedefler += _hedef_dugumleri(icerik)
                elif alan not in _ACIKLAMA_ALANLARI:
                    degerler.append(icerik)
            hedefler += _str_baglama_hedefleri(dugum, baglanan)
            hedefler += _desen_baglamalari(dugum, baglanan)
            if hedefler:
                hedef_eti = "+".join(sorted({
                    _HEDEF_ADLARI.get(type(h).__name__, type(h).__name__)
                    for h in hedefler
                }))
                baglama_eti = _BAGLAMA_ADLARI.get(
                    type(dugum).__name__, type(dugum).__name__
                )
                for icerik in degerler:
                    for ad, deger_yolu in _deger_tablolari(icerik, kapsam):
                        bulgular.append(
                            f"{bagil}:{satir} {baglama_eti}-{hedef_eti}/{deger_yolu} <- {ad}"
                        )

            # (b) FONKSİYON DÖNÜŞÜ: bağlama DEĞİLDİR, ayrı bildirilen biçim.
            if isinstance(dugum, ast.Return) and dugum.value is not None:
                for ad, deger_yolu in _deger_tablolari(dugum.value, kapsam):
                    bulgular.append(f"{bagil}:{satir} dönüş/{deger_yolu} <- {ad}")

            # (c) SÖZLÜKTE SAKLAMA: ANAHTAR da DEĞER de sayılır. Anahtar konumu,
            #     bildirilen "sözlükte saklama" biçiminin ölçülmeyen yarısıydı —
            #     `d["k"] = T` ile aynı sınıftan bir eksik.
            if isinstance(dugum, ast.Dict):
                for eleman in dugum.keys:
                    if eleman is None:
                        continue
                    for ad, deger_yolu in _deger_tablolari(eleman, kapsam):
                        bulgular.append(
                            f"{bagil}:{satir} sözlük-anahtarı/{deger_yolu} <- {ad}"
                        )
                for eleman in dugum.values:
                    if eleman is None:
                        continue
                    for ad, deger_yolu in _deger_tablolari(eleman, kapsam):
                        bulgular.append(f"{bagil}:{satir} sözlük/{deger_yolu} <- {ad}")

            # (d) ÇAĞRI ANAHTAR ARGÜMANI: çağrılanın kapsamında parametre bağlar.
            for ad, deger_yolu in _cagri_anahtar_tablolari(dugum, kapsam):
                bulgular.append(f"{bagil}:{satir} çağrı/{deger_yolu} <- {ad}")

    return bulgular


# KÖR NOKTA KAPATILDI — DONDURULMADI.
#
# `memberships` -> `user_company_memberships` artık ÇÖZÜLÜYOR (aşağıdaki
# `TABLO_TAKMA_ADLARI`). Eskiden bu takma ad çözülemediği için tablo kapıya
# GÖRÜNMEZDİ ve körlük 16 ifade / 5 ihlal olarak DONDURULMUŞTU. Donmuş sayı
# bir çare değil, bir ertelemeydi.
#
# ÇÖZÜLDÜĞÜNDE ORTAYA ÇIKAN 5 İFADE ELLE OKUNDU ve hepsi AYNI ŞEYİ söylüyor:
# `user_company_memberships` KULLANICIYA göre kapsanır, firmaya göre DEĞİL.
#   * `tenancy.py:resolve_company`  — hangi firmanın seçileceğini BULAN sorgu;
#     `company_id` ile süzmek DAİRESEL olurdu.
#   * `user_status_tenant_guard.py` — kullanıcının firmalarını BİLEREK sayar;
#     amacı çapraz kiracı görünürlüğünü engellemek.
#   * `auth.py:register / forgot_password / resend_verification` — firma
#     bağlamı HENÜZ YOKKEN koşan kimlik akışları.
# Beşinin de bağladığı yüklem `memberships.c.user_id`dir.
#
# BU YÜZDEN KURAL TABLOYA GÖRE BİLDİRİLİR: bu tablonun kiracı anahtarı
# `user_id`dir. Kapı artık tabloyu GÖRÜR ve DOĞRU anahtarla denetler —
# muaf tutmaz. `user_id` de bağlı değilse KIRMIZI yanar.
# TÜRETİLMİŞ ÇAPA — bu sözlük ELLE YAZILIR ama SERBEST DEĞİLDİR.
#
# ÖLÇÜLDÜ (runtime lens, fb3a03b1): sözlüğe `"orders": "id"` eklendiğinde
# HİÇBİR test itiraz etmiyordu; yani güvenlik açısından kritik bir eşleme
# "bugün doğru" olmanın ötesinde hiçbir şeye bağlı değildi. Doğru olması
# yetmez; DOĞRU KALMASINI sağlayan bir şey gerekir.
#
# BAĞLANDIĞI DEĞİŞMEZ: bir tablo ancak KİRACI ÇÖZÜMLEME YOLUNDA okunuyorsa
# alternatif anahtar bildirebilir. Gerekçe budur ve dairesellik tam olarak
# buradan gelir: `resolve_company` firmayı BULAN fonksiyondur, okuduğu tabloyu
# `company_id` ile süzmek çıktıyı girdi olarak istemek olurdu. Bu tablo o
# yolda DEĞİLSE dairesellik iddiası da yoktur ve alternatif anahtarın
# gerekçesi yoktur.
#
# `"orders": "id"` bu değişmezle ELENİR: `orders` çözümleme yolunda okunmaz.
#: Şirket bağlamının KURULDUĞU alan. Bu bir ad listesi DEĞİL, bir ROLDÜR:
#: `request.state.company_id`ye atanan şey, tanımı gereği firmayı üretendir.
BAGLAM_ALANI = "company_id"


def _baglam_kuran_fonksiyonlar() -> set[str]:
    """`request.state.company_id = <çağrı>` — çağrılan fonksiyonların adları.

    ÖZELLİKTEN TÜRETİLİR, ADDAN DEĞİL. Eski hâli
    ``{"resolve_company", "user_companies"}`` diye SABİT YAZIYORDU ve inceleme
    (4986380382) bunu haklı olarak canlı DELİK saydı: şirket bağlamı kuran
    YENİ bir fonksiyon bu türetmeye görünmez olurdu ve bu, "bilinen şekiller
    güvenlidir" sınıfını bir kat yukarıda yeniden üretirdi — bu deponun altı
    kez reddettiği ölçüt, üstelik bir önceki blocker'ın DÜZELTMESİNİN adını
    taşıyarak.

    Buradaki çapa ad değil DAVRANIŞTIR: şirket bağlamı ancak
    `request.state.company_id`ye yazılarak kurulabilir; o atamanın sağ
    tarafındaki çağrı, tanımı gereği çözücüdür. Devralan yeni bir fonksiyon
    aynı yere atamak ZORUNDADIR, yoksa bağlam hiç kurulmaz.
    """
    adlar: set[str] = set()
    for yol in sorted(APP_DIR.rglob("*.py")):
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        except SyntaxError:
            continue
        for dugum in ast.walk(agac):
            if not isinstance(dugum, (ast.Assign, ast.AnnAssign)):
                continue
            hedefler = dugum.targets if isinstance(dugum, ast.Assign) else [dugum.target]
            if not any(
                isinstance(h, ast.Attribute)
                and h.attr == BAGLAM_ALANI
                and isinstance(h.value, ast.Attribute)
                and h.value.attr == "state"
                for h in hedefler
            ):
                continue
            if dugum.value is None:
                continue
            # EN DIŞTAKİ çağrı — iç içe çağrılar DEĞİL. `int(user["id"])` bir
            # ARGÜMANDIR, çözücü değil; onu da toplamak kümeyi ZAYIFLATAN
            # yönde büyütürdü. Değer bir çağrı değilse (ör. `a or b()`)
            # temkinli davranıp içteki çağrılar taranır.
            if isinstance(dugum.value, ast.Call):
                ad = _tablo_adi(dugum.value.func)   # Name -> id, Attribute -> attr
                if ad:
                    adlar.add(ad)
                continue
            for ic in ast.walk(dugum.value):
                if isinstance(ic, ast.Call):
                    ad = _tablo_adi(ic.func)
                    if ad:
                        adlar.add(ad)
    return adlar


def _kiraci_cozumleme_tablolari() -> set[str]:
    """Firmayı ÜRETEN fonksiyonların okuduğu kiracı tabloları — TÜRETİLİR.

    İki katman, ikisi de ÖZELLİK tabanlı:
      1. Çözücüler `request.state.company_id` atamasından bulunur
         (``_baglam_kuran_fonksiyonlar``) — ad listesi yok.
      2. O fonksiyonların gövdesinde okunan kiracı tabloları toplanır.

    YÖN — ölçülerek doğrulanacak ve kayda geçirildi: bu kümenin BÜYÜMESİ
    kapıyı ZAYIFLATIR (daha çok tabloya alternatif anahtar istisnası açar),
    KÜÇÜLMESİ güçlendirir. Bu yüzden türetme geniş tarafa kaçmıyor: yalnız
    bağlam atamasında ADI GEÇEN fonksiyonlar sayılıyor, `tenancy.py`nin
    tamamı değil. `user_companies` bilerek DIŞARIDA — listeleme ucudur,
    bağlam kurmaz. Eski ad listesi onu İÇERİYORDU; yeni türetme daha DAR.
    """
    hedef_adlar = _baglam_kuran_fonksiyonlar()
    tablolar: set[str] = set()
    for yol in sorted(APP_DIR.rglob("*.py")):
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        except SyntaxError:
            continue
        for dugum in ast.walk(agac):
            if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if dugum.name not in hedef_adlar:
                continue
            for ic in ast.walk(dugum):
                if not isinstance(ic, (ast.Name, ast.Attribute)):
                    continue
                ad = _tablo_adi(ic)
                if ad and _kanon(ad) in TENANT_TABLES:
                    tablolar.add(_kanon(ad))
    return tablolar


# ŞEMADAN TÜRETME YOLU DENENDİ VE ÖLÇÜMLE ÖLDÜ — İKİ KEZ.
#
# Elle yazılmış bir sözlük gören bir sonraki sahip makul olarak "bunu
# şemadan türetin" diyecektir. İki yol da denendi; ikisi de değişmezi
# İFADE EDEMİYOR. Ölçümler burada dursun ki aynı saatler yeniden
# harcanmasın.
#
#   1. ORM METADATA'sından türetme -> OLMAZ.
#      `user_company_memberships`, `core_schema.metadata` içinde YOKTUR.
#      Ölçüldü: "user_company_memberships" in core_schema.metadata.tables
#      -> False. Tablo `app/tenancy.py`de kendi metadata'sıyla tanımlı.
#      Metadata'dan türeyen bir kural, korunması gereken tabloyu hiç
#      göremez.
#
#   2. YABANCI ANAHTARLARDAN türetme -> OLMAZ.
#      `orders` SIFIR yabancı anahtar bildirir. Ölçüldü:
#      len(core_schema.metadata.tables["orders"].foreign_keys) -> 0.
#      "company_id FK'sı olmayan tablo alternatif anahtar kullanabilir"
#      gibi bir kural, ayırt etmesi gereken tabloları ayıramaz.
#
# Bu yüzden eşleme `app/tenancy.py`den — yani kiracının GERÇEKTEN
# çözüldüğü yerden — türetiliyor (bkz. `_baglam_kuran_fonksiyonlar`).
#
# BÜYÜMEYE KARŞI ÇİVİ: türetilen TABLO kümesinin kendisi çivili DEĞİLDİR.
# Etkisini çivileyen şey aşağıdaki sözlüğün TAM EŞİTLİKLE dondurulmuş
# olmasıdır (`test_alternatif_anahtar_sozlugu_CAPALI`): kümenin büyümesi
# bir muafiyet kazanmak için GEREKLİ ama YETERLİ değildir — sözlük de
# düzenlenmek zorundadır ve o düzenleme diff'te görünür. Kümenin kendisini
# doğrudan çivilemek AYRI BİR DİLİM olarak kuyruğa bırakıldı.
KIRACI_ANAHTARI_ISTISNALARI: dict[str, str] = {
    "user_company_memberships": "user_id",
}

# YENİDEN TAKMA ADLAMA YÜZEYİ — ÖLÇÜLEN SIFIR, DONDURULDU.
#
# `.alias()` bu depoda "en gerçekçi yeniden takma ad yolu" olarak bildirilmiş
# ama kapatılmamıştı. ÖLÇÜLDÜ (develop 53aab22, AST + metin taraması):
# `backend/app` içinde `.alias()`, `aliased()`, `.cte()`, `.subquery()`,
# `.lateral()`, `.table_valued()` çağrısı SIFIR tanedir. Yani bugün hiçbir
# kiracı tablosu yeniden takma adla görünmez hâle gelmiyor.
#
# Sıfır en güçlü sonuçtur ama yazılmamış sıfır geri büyür: biri yarın
# `orders.alias("o")` yazarsa tablo bu kapıya görünmez olur. Bu yüzden sıfır
# ÇAPALANIR ve büyümesi ayrı bir karar olur.
YENIDEN_TAKMA_AD_URETICILERI: frozenset = frozenset(
    {"alias", "aliased", "cte", "subquery", "lateral", "table_valued"}
)
BEKLENEN_YENIDEN_TAKMA_AD_SAYISI = 0


class Ihlal:
    __slots__ = ("yol", "satir", "tablolar", "neden", "fonksiyon", "parmak_izi")

    def __init__(
        self, yol: str, satir: int, tablolar: list[str], neden: str,
        fonksiyon: str = "", parmak_izi: str = "",
    ) -> None:
        self.yol, self.satir, self.tablolar, self.neden = yol, satir, tablolar, neden
        # KİMLİK = kaynak + fonksiyon + İFADENİN BİÇİMİ. Satır numarası kimliğe
        # GİRMEZ (dosyayı kaydırmak lisansı düşürmemeli), ama ifadenin biçimi
        # girer: fonksiyon sonradan düzenlenirse parmak izi değişir ve lisans
        # KENDİLİĞİNDEN düşer. İstisnanın devralınmaması bu sayede.
        self.fonksiyon, self.parmak_izi = fonksiyon, parmak_izi

    @property
    def kimlik(self) -> tuple[str, str, str]:
        return (self.yol, self.fonksiyon, self.parmak_izi)

    def __str__(self) -> str:
        return f"{self.yol}:{self.satir} {sorted(self.tablolar)} — {self.neden}"


def _uretici_adlari(agac: ast.Module) -> set[str]:
    """`from sqlalchemy import select as sec` gibi takma adları da toplar."""
    adlar: set[str] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.ImportFrom) and (dugum.module or "").startswith("sqlalchemy"):
            for alan in dugum.names:
                if alan.name in URETICILER:
                    adlar.add(alan.asname or alan.name)
    return adlar


def _cagri_ureticisi(dugum: ast.AST, adlar: set[str]) -> str | None:
    """`select(...)`, `sa.select(...)` — üretici adını döndürür."""
    if not isinstance(dugum, ast.Call):
        return None
    if isinstance(dugum.func, ast.Name) and dugum.func.id in adlar:
        return dugum.func.id
    if isinstance(dugum.func, ast.Attribute) and dugum.func.attr in URETICILER:
        kok: ast.AST = dugum.func.value
        while isinstance(kok, ast.Attribute):
            kok = kok.value
        if isinstance(kok, ast.Name) and kok.id.startswith(("sa", "sqlalchemy")):
            return dugum.func.attr
    return None


def _tablo_adi(dugum: ast.AST) -> str | None:
    """Bir düğümün adlandırdığı tabloyu döndürür — çözemezse ``None``."""
    if isinstance(dugum, ast.Name):
        return dugum.id
    if isinstance(dugum, ast.Attribute):
        return dugum.attr
    return None


def _sutun_sahibi(dugum: ast.AST) -> tuple[str, str] | None:
    """``orders.c.company_id`` → ``("orders", "company_id")``."""
    if not isinstance(dugum, ast.Attribute):
        return None
    ic = dugum.value
    if not (isinstance(ic, ast.Attribute) and ic.attr == "c"):
        return None
    tablo = _tablo_adi(ic.value)
    return (tablo, dugum.attr) if tablo else None


def _kapsam_govdesi(kapsam: ast.AST):
    """Bir kapsamın KENDİ düğümlerini verir; iç kapsamların gövdesine GİRMEZ.

    ``ast.walk`` alt ağaç budayamaz — döngü içinde ``continue`` yazmak yalnız o
    düğümü atlar, çocuklarını yine gezer. Bu yüzden burada elle bir yığın
    kullanılıyor. Budama olmadan bir fonksiyondaki literal, BAŞKA bir
    fonksiyondaki aynı adı çözerdi ve gerçek bir ``company_id`` UPDATE ihlali
    güvenli görünürdü — ölçüldü, yanlış negatif üretiyordu.
    """
    yigin = list(ast.iter_child_nodes(kapsam))
    while yigin:
        dugum = yigin.pop()
        yield dugum
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # iç kapsam: kendi bağları kendine ait
        yigin.extend(ast.iter_child_nodes(dugum))


#: Sınıflandırılmış her AST düğüm türü, KENDİ KANITINI taşır.
#:
#: Bu kümeye bir tür eklemek bir İDDİADIR: "analizci bu yapıyı anlıyor ve
#: onu güvenli sayıyor". İddia gerekçesiz eklenemez — küme doğrudan bu
#: tablodan türetilir, dolayısıyla gerekçesiz üye MÜMKÜN DEĞİLDİR; ayrıca
#: `BEKLENEN_SINIFLANDIRILMIS` çapası kümenin sessizce büyümesini engeller.
#:
#: Değer: (İKİNCİ SAVUNMA, GEREKÇE). Sınıflandırma tek savunma hattı DEĞİLDİR:
#: `Match` sınıflandırılsa bile bağladığı adları `_string_baglari` yakalar.
#: Bir türün ikinci bir mekanizmayla da korunduğu yer burada YAZILIDIR.
SINIFLANDIRMA_KANITLARI: dict[str, tuple[str, str]] = {
    "AsyncFunctionDef": ("_string_baglari", "fonksiyon adını STRING olarak bağlar"),
    "ClassDef": ("_string_baglari", "sınıf adını STRING olarak bağlar"),
    "ExceptHandler": ("_string_baglari", "yakalanan istisnayı `name` alanında STRING olarak bağlar"),
    "FunctionDef": ("_string_baglari", "fonksiyon adını STRING olarak bağlar"),
    "Global": ("_string_baglari", "`global X` — bildirilen adları STRING listesinde taşır"),
    "Nonlocal": ("_string_baglari", "`nonlocal X` — bildirilen adları STRING listesinde taşır"),
    "alias": ("_string_baglari", "`import x as d` — bağlanan adı `asname`/`name` alanında STRING olarak taşır"),
    "arg": ("_string_baglari", "parametre adını STRING olarak bağlar"),
    "arguments": ("_string_baglari", "parametre listesi; bağlama `arg` alt düğümlerinde yapılır"),
    "AnnAssign": ("Name/ctx denetimi", "hedefi `ast.Name(Store)` üretir"),
    "Assign": ("Name/ctx denetimi", "hedefleri `ast.Name(Store)` üretir"),
    "AsyncFor": ("Name/ctx denetimi", "döngü değişkeni `ast.Name(Store)` üretir"),
    "AugAssign": ("Name/ctx denetimi", "hedefi `ast.Name(Store)` üretir; artırmalı atama yeniden bağlamadır"),
    "Delete": ("Name/ctx denetimi", "hedefleri `ast.Name(Del)` üretir"),
    "For": ("Name/ctx denetimi", "döngü değişkeni `ast.Name(Store)` üretir"),
    "Name": ("Name/ctx denetimi", "adın kendisi; ctx'e göre bağlama/okuma olarak sınıflandırılır"),
    "NamedExpr": ("Name/ctx denetimi", "walrus hedefi `ast.Name(Store)` üretir"),
    "comprehension": ("Name/ctx denetimi", "kurgu hedefi `ast.Name(Store)` üretir; `iter` konumu GÜVENLİ OKUMADIR"),
    "withitem": ("Name/ctx denetimi", "`as` hedefi `ast.Name(Store)` üretir"),
    "Attribute": ("Name/ctx denetimi", "öznitelik erişimi; okuması TESLİM sayılır, kendisi bağlamaz"),
    "Call": ("Name/ctx denetimi", "çağrı; argüman konumu TESLİM sayılır, kendisi ad bağlamaz"),
    "Compare": ("Name/ctx denetimi", "karşılaştırma; yalnız okur, referansı dışarı vermez"),
    "Del": ("Name/ctx denetimi", "silme bağlamı işareti; kararı `Name` denetimi verir"),
    "Load": ("Name/ctx denetimi", "yalnız okuma bağlamı işareti"),
    "Starred": ("Name/ctx denetimi", "`*ad` — GÜVENLİ OKUMA konumu"),
    "Store": ("Name/ctx denetimi", "yazma bağlamı işareti; kararı `Name` denetimi verir"),
    "Subscript": ("Name/ctx denetimi", "indeks erişimi; yazması dolaylı yazma olarak ayrıca denetlenir"),
    "keyword": ("Name/ctx denetimi", "`ad=` / `**ad`; ikincisi GÜVENLİ OKUMA konumu"),
    "Add": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "And": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Assert": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "AsyncWith": ("yok", "gövde; `as` hedefi `withitem` düğümünde bağlanır"),
    "Await": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "BinOp": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "BitAnd": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "BitOr": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "BitXor": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "BoolOp": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Break": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Constant": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Continue": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Dict": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "DictComp": ("yok", "kendi kapsamını açar; hedefi `comprehension` düğümü bağlar"),
    "Div": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Eq": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Expr": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "FloorDiv": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "FormattedValue": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "GeneratorExp": ("yok", "kendi kapsamını açar; hedefi `comprehension` düğümü bağlar"),
    "Gt": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "GtE": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "If": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "IfExp": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Import": ("yok", "modül adını bağlar ama bağlama `alias` alt düğümünde yapılır"),
    "ImportFrom": ("yok", "modül adını bağlar ama bağlama `alias` alt düğümünde yapılır"),
    "In": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Invert": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Is": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "IsNot": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "JoinedStr": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "LShift": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Lambda": ("yok", "kendi kapsamını açar; parametreleri `arg` düğümleri bağlar"),
    "List": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "ListComp": ("yok", "kendi kapsamını açar; hedefi `comprehension` düğümü bağlar"),
    "Lt": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "LtE": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "MatMult": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Mod": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Module": ("yok", "kapsam kökü; kendisi ad bağlamaz"),
    "Mult": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Not": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "NotEq": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "NotIn": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Or": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Pass": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Pow": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "RShift": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Raise": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Return": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Set": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "SetComp": ("yok", "kendi kapsamını açar; hedefi `comprehension` düğümü bağlar"),
    "Slice": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Sub": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "Try": ("yok", "gövde; istisna adı `ExceptHandler` düğümünde bağlanır"),
    "TryStar": ("yok", "gövde; istisna adı `ExceptHandler` düğümünde bağlanır"),
    "Tuple": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "UAdd": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "USub": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "UnaryOp": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "While": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "With": ("yok", "gövde; `as` hedefi `withitem` düğümünde bağlanır"),
    "Yield": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
    "YieldFrom": ("yok", "ad bağlamaz ve bir değeri yerinde değiştirmez"),
}

#: Küme tablodan TÜRETİLİR; elle ikinci bir liste tutulmaz.
SINIFLANDIRILMIS_DUGUMLER = frozenset(SINIFLANDIRMA_KANITLARI)

#: Üyeliğin BAĞIMSIZ çapası. Elle yazılmıştır ve küme eşitliğiyle sınanır:
#: tabloya bir tür eklemek, bu çapayı da güncellemeyi ZORUNLU kılar. Böylece
#: fail-open sınırının genişlemesi görünür ve bilinçli bir işlem olur.
BEKLENEN_SINIFLANDIRILMIS = frozenset({
    "Add",
    "And",
    "AnnAssign",
    "Assert",
    "Assign",
    "AsyncWith",
    "AsyncFor",
    "AsyncFunctionDef",
    "Attribute",
    "AugAssign",
    "Await",
    "BinOp",
    "BitAnd",
    "BitOr",
    "BitXor",
    "BoolOp",
    "Break",
    "Call",
    "ClassDef",
    "Compare",
    "Constant",
    "Continue",
    "Del",
    "Delete",
    "Dict",
    "DictComp",
    "Div",
    "Eq",
    "ExceptHandler",
    "Expr",
    "FloorDiv",
    "For",
    "FormattedValue",
    "FunctionDef",
    "GeneratorExp",
    "Global",
    "Gt",
    "GtE",
    "If",
    "IfExp",
    "Import",
    "ImportFrom",
    "In",
    "Invert",
    "Is",
    "IsNot",
    "JoinedStr",
    "LShift",
    "Lambda",
    "List",
    "ListComp",
    "Load",
    "Lt",
    "LtE",
    "MatMult",
    "Mod",
    "Module",
    "Mult",
    "Name",
    "NamedExpr",
    "Nonlocal",
    "Not",
    "NotEq",
    "NotIn",
    "Or",
    "Pass",
    "Pow",
    "RShift",
    "Raise",
    "Return",
    "Set",
    "SetComp",
    "Slice",
    "Starred",
    "Store",
    "Sub",
    "Subscript",
    "Try",
    "TryStar",
    "Tuple",
    "UAdd",
    "USub",
    "UnaryOp",
    "While",
    "With",
    "Yield",
    "YieldFrom",
    "alias",
    "arg",
    "arguments",
    "comprehension",
    "keyword",
    "withitem",
})


def _string_baglari(dugum: ast.AST) -> set[str]:
    """Bir düğümün ``ast.Name`` ÜRETMEDEN bağladığı adlar."""
    adlar: set[str] = set()
    if isinstance(dugum, ast.ExceptHandler) and dugum.name:
        adlar.add(dugum.name)
    elif isinstance(dugum, ast.alias):
        adlar.add(dugum.asname or dugum.name.split(".")[0])
    elif isinstance(dugum, (ast.Global, ast.Nonlocal)):
        adlar.update(dugum.names)
    elif isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        adlar.add(dugum.name)
    elif isinstance(dugum, ast.arg):
        adlar.add(dugum.arg)
    else:
        for alan in ("name", "rest"):
            deger = getattr(dugum, alan, None)
            if isinstance(deger, str):
                adlar.add(deger)
    return adlar


def _degismez_literal(dugum: ast.AST) -> bool:
    """Yerinde DEĞİŞTİRİLEMEYEN literal mi (yalnız sabitlerden oluşan demet)."""
    return isinstance(dugum, ast.Tuple) and all(
        isinstance(oge, ast.Constant) for oge in dugum.elts
    )


def _dolayli_yazma(dugum: ast.AST) -> tuple[set[str], bool]:
    """``globals()['x'] = ...`` / ``mod.__dict__['x'] = ...`` — ad STRING'dir.

    Bu biçimde hedef bir ``ast.Name`` üretmez; ad bir sabit dizedir. Anahtar
    sabit değilse HANGİ adın yeniden bağlandığı bilinemez ve ikinci değer
    ``True`` döner: o kapsamda hiçbir bağ çözülmez.
    """
    hedefler: list[ast.AST] = []
    if isinstance(dugum, ast.Assign):
        hedefler = list(dugum.targets)
    elif isinstance(dugum, (ast.AugAssign, ast.AnnAssign)):
        hedefler = [dugum.target]
    elif isinstance(dugum, ast.Delete):
        hedefler = list(dugum.targets)

    adlar: set[str] = set()
    belirsiz = False
    for hedef in hedefler:
        if not isinstance(hedef, ast.Subscript):
            continue
        taban = hedef.value
        kuresel_mi = (
            isinstance(taban, ast.Call)
            and isinstance(taban.func, ast.Name)
            and taban.func.id in {"globals", "vars"}
        ) or (isinstance(taban, ast.Attribute) and taban.attr == "__dict__")
        if not kuresel_mi:
            continue
        anahtar = hedef.slice
        if isinstance(anahtar, ast.Constant) and isinstance(anahtar.value, str):
            adlar.add(anahtar.value)
        else:
            belirsiz = True
    return adlar, belirsiz


def _guvenli_okuma(ad_dugumu: ast.Name, ebeveynler: dict[int, ast.AST]) -> bool:
    """Adın DEĞERİNİ değiştiremeyen ve referansını dışarı VERMEYEN okuma."""
    ust = ebeveynler.get(id(ad_dugumu))
    if isinstance(ust, ast.keyword) and ust.arg is None:
        return True  # `**ad`
    if isinstance(ust, ast.Starred):
        return True  # `*ad`
    if isinstance(ust, ast.comprehension) and ust.iter is ad_dugumu:
        return True  # `for x in ad`
    if isinstance(ust, ast.Compare):
        return True  # `x in ad`, `ad == y` — yalnız okur
    return False


def _kapsam_agaci(agac: ast.Module) -> tuple[list[ast.AST], dict[int, ast.AST | None]]:
    """Kapsam listesi ve her kapsamın ÜST kapsamı (modül + fonksiyonlar)."""
    kapsamlar: list[ast.AST] = [agac]
    ust: dict[int, ast.AST | None] = {id(agac): None}

    def gez(kapsam: ast.AST) -> None:
        for dugum in _kapsam_govdesi(kapsam):
            if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kapsamlar.append(dugum)
                ust[id(dugum)] = kapsam
                gez(dugum)

    gez(agac)
    return kapsamlar, ust


def _bildirimler(kapsam: ast.AST) -> tuple[set[str], set[str]]:
    """Bu kapsamın KENDİ ``global`` / ``nonlocal`` bildirimleri."""
    kuresel: set[str] = set()
    ustkapsam: set[str] = set()
    for dugum in _kapsam_govdesi(kapsam):
        if isinstance(dugum, ast.Global):
            kuresel.update(dugum.names)
        elif isinstance(dugum, ast.Nonlocal):
            ustkapsam.update(dugum.names)
    return kuresel, ustkapsam


def _kapsam_tum_dugumler(kapsam: ast.AST):
    """Kapsamın gövdesi VE içindeki bütün iç kapsamlar.

    İç kapsamlar dış bağı kapanış üzerinden değiştirebilir; bu yüzden bir adın
    denetimi iç kapsamları DA kapsar.
    """
    if isinstance(kapsam, ast.Module):
        yield from ast.walk(kapsam)
        return
    for alt in ast.iter_child_nodes(kapsam):
        yield from ast.walk(alt)


def _yerel_baglar(agac: ast.Module) -> dict[int, tuple[dict[str, ast.AST], set[str]]]:
    """Her kapsam için ``ad -> tek atanmış literal`` haritası — FAIL-CLOSED.

    TASARIM: "tanımadığım tehlikeyi ararım" DEĞİL, "her olayı hesaba katmadıkça
    çözmem". Önceki sürüm bir kara liste (tehlikeli metot adları) ve elle sayılmış
    bir bağlama-düğümü listesi kullanıyordu; tanımadığı biçimi GÜVENLİ sayıyordu.
    Bu, kapının kendi ilkesinin tersiydi ve her yeni Python biçimi, her yeni
    yardımcı metot, her yeni takma-ad deyimi sessizce yeni bir delik açıyordu.

    Bir ad ANCAK ŞU KOŞULLARDA çözülür:

      * Kapsamda (iç kapsamlar dahil) o adın BÜTÜN geçişleri hesaba katılmıştır.
      * Bağlayan tek olay vardır ve o da tek bir LİTERAL atamadır
        (``ad = {...}`` / ``ad: T = {...}``; sözlük, liste, demet, sözlük kurgusu).
      * Geri kalan her geçiş GÜVENLİ OKUMADIR: ``**ad``, ``*ad``,
        ``for x in ad``, karşılaştırma. Bunlar değeri değiştiremez ve referansı
        dışarı vermez.
      * Adı içeren her düğüm türü SINIFLANDIRILMIS_DUGUMLER içindedir.

    Bunların herhangi biri sağlanmazsa ad DÜŞER. Özellikle:

      * Başka her yazma biçimi — walrus, ``except ... as``, ``case {...: ad}``,
        ``import ... as``, ``for ad in``, ``with ... as ad``, ``del ad``,
        artırmalı atama, parametre — çözülmeyi engeller.
      * TESLİM (hand-off): adın bir çağrıya argüman olarak verilmesi, bir
        özniteliğe/indekse yazılması, başka bir ada atanması. Analizci nesnenin
        oraya gidip ne olacağını izleyemez. DEĞİŞMEZ literal (yalnız sabitlerden
        oluşan demet) bunun istisnasıdır: yerinde değiştirilemeyeceği için
        teslim edilmesi bağı bozmaz.
      * SINIFLANDIRILMAMIŞ DÜĞÜM: türü ``SINIFLANDIRILMIS_DUGUMLER`` içinde
        olmayan bir düğüm bir adı içeriyorsa o ad düşer. Gelecekteki bir Python
        biçimi, kimsenin aklına gelmemiş bir deyim, kırmızı üretir — birisi onu
        açıkça sınıflandırana kadar.

    DEKLARE EDİLMİŞ SINIR: bu tarayıcı yalnız ``backend/app/**/*.py`` ağacını
    okur. Bir bağa bu ağacın DIŞINDAN erişilip değiştirilmesi görülmez.
    """
    haritalar: dict[int, tuple[dict[str, ast.AST], set[str]]] = {}
    kapsamlar, ust = _kapsam_agaci(agac)

    ebeveynler: dict[int, ast.AST] = {}
    for ustd in ast.walk(agac):
        for alt in ast.iter_child_nodes(ustd):
            ebeveynler[id(alt)] = ustd

    # Bildirimler kapsama özgüdür (önceki turda kabul edildi).
    bildirim_gecersiz: dict[int, set[str]] = {id(k): set() for k in kapsamlar}
    for kapsam in kapsamlar:
        kuresel, ustkapsam = _bildirimler(kapsam)
        bildirim_gecersiz[id(kapsam)] |= kuresel | ustkapsam
        bildirim_gecersiz[id(agac)] |= kuresel
        for ad in ustkapsam:
            gezici = ust.get(id(kapsam))
            while gezici is not None:
                bildirim_gecersiz[id(gezici)].add(ad)
                gezici = ust.get(id(gezici))

    for kapsam in kapsamlar:
        dugumler = list(_kapsam_tum_dugumler(kapsam))

        # 1) Aday literal atamalar: bare Name hedefi, literal değer.
        adaylar: dict[str, ast.AST] = {}
        atama_dugumu: dict[str, ast.AST] = {}
        for dugum in _kapsam_govdesi(kapsam):
            hedefler: list[ast.AST] = []
            atanan: ast.AST | None = None
            if isinstance(dugum, ast.Assign):
                hedefler, atanan = list(dugum.targets), dugum.value
            elif isinstance(dugum, ast.AnnAssign) and dugum.value is not None:
                hedefler, atanan = [dugum.target], dugum.value
            if atanan is None or not isinstance(
                atanan, (ast.Dict, ast.List, ast.Tuple, ast.DictComp)
            ):
                continue
            for hedef in hedefler:
                if isinstance(hedef, ast.Name):
                    adaylar[hedef.id] = atanan
                    atama_dugumu[hedef.id] = hedef

        # 2) Her adayı, kapsamdaki BÜTÜN geçişleriyle denetle.
        dusen: set[str] = set(bildirim_gecersiz[id(kapsam)])
        # GÖLGE yalnız BAĞLANAN adlardır. Salt okuma bir adı gölgelemez;
        # gölgelerse dış kapsamdaki meşru bağ (ör. modül sabiti) sırf okunduğu
        # için kaybolur — ölçüldü, `_FINALIZE_SUTUNLARI` böyle düşüyordu.
        baglanan: set[str] = set()

        for dugum in dugumler:
            tur = type(dugum).__name__
            if tur not in SINIFLANDIRILMIS_DUGUMLER:
                # Sınıflandırılmamış düğüm: içindeki HER adı düşür.
                for ic in ast.walk(dugum):
                    if isinstance(ic, ast.Name):
                        dusen.add(ic.id)
                    dusen |= _string_baglari(ic)
                continue
            _str_bag = _string_baglari(dugum)
            dusen |= _str_bag
            baglanan |= _str_bag
            _dolayli, _belirsiz = _dolayli_yazma(dugum)
            dusen |= _dolayli
            baglanan |= _dolayli
            if _belirsiz:
                dusen.add("*")
            if isinstance(dugum, ast.Name):
                if isinstance(dugum.ctx, (ast.Store, ast.Del)):
                    baglanan.add(dugum.id)
                # Yalnız BU kapsamın adaylarını yargılarız. Başka bir kapsamda
                # bağlanan bir ad (ör. modül sabiti) burada okunuyorsa kararı
                # onu bağlayan kapsam verir; o kapsamın denetimi zaten iç
                # kapsamları da gezer. Burada karar vermek, meşru bir modül
                # sabitini sırf bir fonksiyonda kullanıldığı için düşürürdü.
                if dugum.id in adaylar:
                    if isinstance(dugum.ctx, ast.Store):
                        if atama_dugumu.get(dugum.id) is not dugum:
                            dusen.add(dugum.id)  # başka bir yazma biçimi
                    elif isinstance(dugum.ctx, ast.Del):
                        dusen.add(dugum.id)
                    elif not _guvenli_okuma(dugum, ebeveynler):
                        # TESLİM: değişmez literal dışında bağ düşer.
                        if not _degismez_literal(adaylar[dugum.id]):
                            dusen.add(dugum.id)

        hepsi_dustu = "*" in dusen
        cozulen = {
            ad: v for ad, v in adaylar.items() if not hepsi_dustu and ad not in dusen
        }
        golge = baglanan | dusen | set(adaylar)
        haritalar[id(kapsam)] = (cozulen, golge)
    return haritalar


def _coz(dugum: ast.AST, baglar: dict[str, ast.AST]) -> ast.AST | None:
    """Bir adı, aynı fonksiyondaki tek literal atamasına çözer."""
    if isinstance(dugum, ast.Name):
        return baglar.get(dugum.id)
    return None


def _yayilim_anahtarlari(dugum: ast.AST, baglar: dict[str, ast.AST]) -> set[str] | None:
    """``**X`` yayılımının STATİK olarak bilinen sütun kümesi — bilinmiyorsa ``None``.

    İki biçim çözülür, ikisi de deterministik:
      * yerel bir sözlük literali (``d = {"company_id": cid}``),
      * literal bir anahtar demeti üzerinde sözlük kurgusu
        (``{ad: v[ad] for ad in _SUTUNLAR if ...}``).

    İkincisi, "hangi sütunlar yazılabilir" sorusunu çağıranın davranışından
    alıp AÇIK BİR LİSTEYE bağlayan yazım biçimidir; kapı bu yüzden onu görebilir.
    Başka her şey ``None`` döner ve çözülemez sayılır.
    """
    hedef = dugum if isinstance(dugum, (ast.Dict, ast.DictComp)) else _coz(dugum, baglar)
    if isinstance(hedef, ast.Dict):
        anahtarlar: set[str] = set()
        for anahtar in hedef.keys:
            if not (isinstance(anahtar, ast.Constant) and isinstance(anahtar.value, str)):
                return None
            anahtarlar.add(anahtar.value)
        return anahtarlar
    if isinstance(hedef, ast.DictComp) and len(hedef.generators) == 1:
        uretec = hedef.generators[0]
        if not (isinstance(hedef.key, ast.Name) and isinstance(uretec.target, ast.Name)):
            return None
        if hedef.key.id != uretec.target.id:
            return None
        kaynak = uretec.iter if isinstance(uretec.iter, (ast.Tuple, ast.List)) else _coz(uretec.iter, baglar)
        if not isinstance(kaynak, (ast.Tuple, ast.List)):
            return None
        anahtarlar = set()
        for oge in kaynak.elts:
            if not (isinstance(oge, ast.Constant) and isinstance(oge.value, str)):
                return None
            anahtarlar.add(oge.value)
        return anahtarlar
    return None


def _ebeveyn_haritasi(agac: ast.Module) -> dict[int, ast.AST]:
    harita: dict[int, ast.AST] = {}
    for ust in ast.walk(agac):
        for alt in ast.iter_child_nodes(ust):
            harita[id(alt)] = ust
    return harita


def _en_dis_ifade(dugum: ast.AST, ebeveynler: dict[int, ast.AST]) -> ast.AST:
    """Zincirin tamamını verir: ``select(x).where(...).order_by(...)``.

    Yukarı yalnız ZİNCİR boyunca tırmanır (``.where`` gibi metot çağrıları);
    ``db.execute(...)`` argümanı olmak zinciri bitirir.
    """
    guncel = dugum
    while True:
        ust = ebeveynler.get(id(guncel))
        if isinstance(ust, ast.Attribute) and ust.value is guncel:
            guncel = ust
            continue
        if isinstance(ust, ast.Call) and ust.func is guncel:
            guncel = ust
            continue
        return guncel


def _ifade_parmak_izi(ifade: ast.AST) -> str:
    """İfadenin KONUMDAN BAĞIMSIZ biçim özeti.

    ``ast.dump`` öntanımlı olarak satır/sütun niteliklerini DIŞARIDA bırakır;
    bu yüzden kod aşağı kaysa özet aynı kalır, ama ifade değişirse değişir.
    """
    return hashlib.sha256(ast.dump(ifade).encode("utf-8")).hexdigest()


def _kanon(ad: str | None) -> str | None:
    """Python adını GERÇEK tablo adına çevirir (kiracı olup olmadığına bakmadan).

    KAPSAM ANALİZİ DE bunu kullanmak ZORUNDA: dokunulan tablolar gerçek adla,
    kapsanan tablolar takma adla anılırsa küme farkı boş çıkmaz ve DOĞRU
    kapsanmış bir sorgu yanlışlıkla kırmızı olur. Ölçüldü: yalnız dokunma
    tarafını çevirmek `routers/auth.py::list_audit`'i (company_id ile SÜZEN
    okuma) ihlal gösteriyordu.
    """
    if ad is None:
        return None
    return TABLO_TAKMA_ADLARI.get(ad, ad)


def _gercek_tablo(ad: str | None) -> str | None:
    """Python adını GERÇEK tablo adına çevirir; kiracı tablosu değilse ``None``."""
    if ad is None:
        return None
    cozulen = TABLO_TAKMA_ADLARI.get(ad, ad)
    return cozulen if cozulen in TENANT_TABLES else None


def _dokunulan_kiraci_tablolari(ifade: ast.AST) -> set[str]:
    tablolar: set[str] = set()
    for dugum in ast.walk(ifade):
        sahip = _sutun_sahibi(dugum)
        if sahip:
            gercek = _gercek_tablo(sahip[0])
            if gercek:
                tablolar.add(gercek)
        if isinstance(dugum, (ast.Name, ast.Attribute)):
            gercek = _gercek_tablo(_tablo_adi(dugum))
            if gercek:
                tablolar.add(gercek)
    return tablolar


def _cozulemez_nedenler(
    ifade: ast.AST, uretici: str, cagri: ast.Call, baglar: dict[str, ast.AST]
) -> list[str]:
    """Analizcinin ÇÖZEMEDİĞİ biçimler. Hepsi ihlaldir, atlama değil."""
    nedenler: list[str] = []

    if uretici in {"insert", "update", "delete"}:
        if not cagri.args:
            nedenler.append(f"{uretici}() hedef tablosu yok — çözülemiyor")
        elif _tablo_adi(cagri.args[0]) is None:
            nedenler.append(f"{uretici}() hedefi sabit bir ad değil — çözülemiyor")

    if uretici == "update":
        for dugum in ast.walk(ifade):
            if not (isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Attribute)):
                continue
            if dugum.func.attr != "values":
                continue
            yazilan: set[str] = {kw.arg for kw in dugum.keywords if kw.arg}
            for kw in dugum.keywords:
                if kw.arg is None:
                    kume = _yayilim_anahtarlari(kw.value, baglar)
                    if kume:
                        yazilan |= kume
            for arg in dugum.args:
                kume = _yayilim_anahtarlari(arg, baglar)
                if kume:
                    yazilan |= kume
            if KIRACI_SUTUNU in yazilan:
                nedenler.append(
                    "UPDATE company_id YAZAMAZ — satırın sahibini değiştirmek "
                    "çapraz kiracı yazımıdır"
                )

    for dugum in ast.walk(ifade):
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name):
            # `and_(*kosullar)` / `or_(*kosullar)` — liste çözülemiyorsa kırmızı.
            for arg in dugum.args:
                if isinstance(arg, ast.Starred) and _coz(arg.value, baglar) is None:
                    nedenler.append(
                        f"{dugum.func.id}(*...) yüklem listesi çözülemiyor"
                    )
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Attribute):
            metot = dugum.func.attr
            if metot in {"where", "filter", "having"}:
                for arg in dugum.args:
                    if isinstance(arg, ast.Starred) and _coz(arg.value, baglar) is None:
                        nedenler.append(
                            f".{metot}(*...) yüklemi başka yerde kuruluyor — çözülemiyor"
                        )
            if metot == "values":
                # Açık `company_id=` anahtarı varsa `**yayılım` onu EZEMEZ:
                # Python yinelenen anahtar sözcüğü çalışma zamanında reddeder.
                # Bu yüzden açık anahtar, yayılımı çözülebilir kılar.
                acik_kiraci = any(kw.arg == KIRACI_SUTUNU for kw in dugum.keywords)
                for kw in dugum.keywords:
                    if (
                        kw.arg is None
                        and not acik_kiraci
                        and _yayilim_anahtarlari(kw.value, baglar) is None
                    ):
                        nedenler.append(".values(**...) sütunları çalışma zamanında — çözülemiyor")
                for arg in dugum.args:
                    if not isinstance(arg, ast.Dict):
                        nedenler.append(".values(<ifade>) sözlüğü çözülemiyor")
                    else:
                        for anahtar in arg.keys:
                            if not (isinstance(anahtar, ast.Constant) and isinstance(anahtar.value, str)):
                                nedenler.append(".values({<ifade>: ...}) anahtarı çözülemiyor")
    return nedenler


def _genisletilmis_dugumler(ifade: ast.AST, baglar: dict[str, ast.AST]):
    """İfadeyi, tek adımda çözülen yerel literalleri de katarak dolaşır."""
    yield from ast.walk(ifade)
    for dugum in ast.walk(ifade):
        hedef = None
        if isinstance(dugum, ast.Starred):
            hedef = _coz(dugum.value, baglar)
        elif isinstance(dugum, ast.keyword) and dugum.arg is None:
            hedef = _coz(dugum.value, baglar)
        if hedef is not None:
            yield from ast.walk(hedef)


def _alternatif_anahtar_bagli(
    ifade: ast.AST, baglar: dict[str, ast.AST], tablo: str, sutun: str,
    agac: ast.Module | None = None, kapsam: ast.AST | None = None,
    kimlik_anahtari: tuple[str, str, str] = ("", "", ""),
) -> bool:
    """`T.c.<sutun> == <KİMLİK TAŞIYAN değer>` var mı.

    ÖNCEKİ HÂLİ FAIL-OPEN'DI ve inceleme (4986380382) haklı olarak canlı
    DELİK saydı: SABİT OLMAYAN her sağ taraf yeterli sayılıyordu, yani
    KARŞILAŞTIRMANIN VARLIĞI ölçülüyordu, KİMLİK değil.
    `memberships.c.user_id == some_user_variable` geçiyordu ve kural
    `some_user_variable`ın kimlik doğrulanmış kullanıcı olduğunu HİÇ
    kurmuyordu. RLS'siz bir depoda bu doğrudan kiracı sınırı zayıflığıdır.

    Yüklem yukarıda yazılı (bkz. KİMLİK bölümü). Kısaca: değer ya asilin
    doğrudan alan okuması olacak, ya da her çağrı yerinde asil geçirilen bir
    parametre. Kanıtlanamayan her şey FAIL-CLOSED: kimlik taşımıyor sayılır.

    `agac`/`kapsam` verilmezse (sentetik testler) eski gevşek davranış
    KULLANILMAZ — kimlik kanıtlanamaz, yani False döner. Bu bilinçli: kapıya
    eksik bağlam vermek onu gevşetmemeli.
    """
    # BİLDİRİLMİŞ İSTİSNA İFADE DÜZEYİNDEDİR, karşılaştırma düzeyinde değil.
    # Döngüden ÖNCE bakılır: `536` sütun-sütun JOIN olduğu için döngü onu
    # atlıyor ve istisnaya hiç ulaşılmıyordu — ölçüldü.
    if kimlik_anahtari in KIMLIK_ISTISNALARI:
        return True

    for dugum in _genisletilmis_dugumler(ifade, baglar):
        if not (
            isinstance(dugum, ast.Compare)
            and len(dugum.ops) == 1
            and isinstance(dugum.ops[0], ast.Eq)
        ):
            continue
        for taraf, karsi in (
            (dugum.left, dugum.comparators[0]),
            (dugum.comparators[0], dugum.left),
        ):
            sahip = _sutun_sahibi(taraf)
            if not (sahip and sahip[1] == sutun and _kanon(sahip[0]) == tablo):
                continue
            if isinstance(karsi, ast.Constant):
                continue
            # Sütun-sütun JOIN (`users.c.id`) bir kimlik bağı DEĞİLDİR.
            if _sutun_sahibi(karsi):
                continue
            if agac is None:
                return False         # bağlam yok -> kanıt yok -> FAIL-CLOSED
            asil = _kimlik_kaynagi_adlari(agac)
            if _asil_okumasi(karsi, asil):
                return True
            hedef = _asil_soy(karsi)
            if isinstance(hedef, ast.Name) and kapsam is not None:
                if _parametre_kimlik_tasiyor(kapsam, hedef.id):
                    return True
            return False
    return False


def _kapsam_analizi(
    ifade: ast.AST, tablolar: set[str], baglar: dict[str, ast.AST],
    uretici: str = "select",
) -> tuple[set[str], list[str]]:
    """Bağlı yüklemi olan tabloları ve sabitle kapsanmış olanları döndürür."""
    bagli: set[str] = set()
    sabitle: list[str] = []
    kiraci_baglari: dict[str, set[str]] = {t: set() for t in tablolar}

    def kiraci_sutunu(dugum: ast.AST) -> str | None:
        sahip = _sutun_sahibi(dugum)
        if sahip and sahip[1] == KIRACI_SUTUNU:
            return _kanon(sahip[0])
        return None

    hedef_tablo = sorted(tablolar)[0] if len(tablolar) == 1 else None
    for dugum in _genisletilmis_dugumler(ifade, baglar):
        # Çözülen sözlükte "company_id": <değer> — INSERT/UPDATE
        if isinstance(dugum, ast.Dict) and hedef_tablo:
            for anahtar, deger in zip(dugum.keys, dugum.values):
                if isinstance(anahtar, ast.Constant) and anahtar.value == KIRACI_SUTUNU:
                    if isinstance(deger, ast.Constant):
                        sabitle.append(hedef_tablo)
                    else:
                        bagli.add(hedef_tablo)
        # Karşılaştırma: T.c.company_id == <değer>
        if isinstance(dugum, ast.Compare) and len(dugum.ops) == 1 and isinstance(dugum.ops[0], ast.Eq):
            sol = kiraci_sutunu(dugum.left)
            sag_dugum = dugum.comparators[0]
            sag = kiraci_sutunu(sag_dugum)
            if sol and sag:
                kiraci_baglari.setdefault(sol, set()).add(sag)
                kiraci_baglari.setdefault(sag, set()).add(sol)
            elif sol:
                if isinstance(sag_dugum, ast.Constant):
                    sabitle.append(sol)
                else:
                    bagli.add(sol)
        # values(company_id=<değer>) — INSERT/UPDATE
        if (
            uretici != "update"
            and isinstance(dugum, ast.Call)
            and isinstance(dugum.func, ast.Attribute)
            and dugum.func.attr == "values"
        ):
            hedef = None
            iç = dugum.func.value
            while isinstance(iç, ast.Call):
                if iç.args:
                    hedef = _kanon(_tablo_adi(iç.args[0]))
                    break
                iç = iç.func.value if isinstance(iç.func, ast.Attribute) else iç
            for kw in dugum.keywords:
                if kw.arg == KIRACI_SUTUNU and hedef:
                    if isinstance(kw.value, ast.Constant):
                        sabitle.append(hedef)
                    else:
                        bagli.add(hedef)
            for arg in dugum.args:
                if isinstance(arg, ast.Dict) and hedef:
                    for anahtar, deger in zip(arg.keys, arg.values):
                        if isinstance(anahtar, ast.Constant) and anahtar.value == KIRACI_SUTUNU:
                            if isinstance(deger, ast.Constant):
                                sabitle.append(hedef)
                            else:
                                bagli.add(hedef)

    # Kiracı eşitliğiyle bağlı tablolar, kapsanmış olandan kapsam devralır.
    kapsanan = set(bagli)
    bekleyen = list(bagli)
    while bekleyen:
        guncel = bekleyen.pop()
        for komsu in kiraci_baglari.get(guncel, set()) - kapsanan:
            kapsanan.add(komsu)
            bekleyen.append(komsu)
    return kapsanan, sabitle


def core_ihlallerini_bul(kok: Path = APP_DIR) -> list[Ihlal]:
    ihlaller: list[Ihlal] = []
    for yol in sorted(kok.rglob("*.py")):
        ihlaller.extend(
            _kaynagi_incele(yol.relative_to(BACKEND.parent).as_posix(), yol.read_text(encoding="utf-8"))
        )
    return ihlaller


def _kaynagi_incele(bagil: str, kaynak: str) -> list[Ihlal]:
    """Tek bir kaynak metnini inceler — sentetik örnekler de buradan geçer."""
    ihlaller: list[Ihlal] = []
    agac = ast.parse(kaynak, filename=bagil)
    adlar = _uretici_adlari(agac)
    if not adlar:
        return ihlaller

    ebeveynler = _ebeveyn_haritasi(agac)
    yerel = _yerel_baglar(agac)
    # Kapsayan fonksiyon: dıştan içe yazılır, EN İÇTEKİ kazanır. `setdefault`
    # ile yazmak Module'ü her düğüme yapıştırırdı (ast.walk önce Module'ü
    # verir) ve yerel bağlar hiç bulunamazdı — ölçüldü, 5 yanlış ihlal üretti.
    kapsayan: dict[int, ast.AST] = {id(a): agac for a in ast.walk(agac)}
    for ust in ast.walk(agac):
        if isinstance(ust, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for alt in ast.walk(ust):
                kapsayan[id(alt)] = ust

    gorulen: set[int] = set()
    for dugum in ast.walk(agac):
        uretici = _cagri_ureticisi(dugum, adlar)
        if uretici is None:
            continue
        assert isinstance(dugum, ast.Call)
        ifade = _en_dis_ifade(dugum, ebeveynler)
        if id(ifade) in gorulen:
            continue
        tablolar = _dokunulan_kiraci_tablolari(ifade)
        if not tablolar:
            continue
        gorulen.add(id(ifade))
        _kaps = kapsayan.get(id(dugum), agac)
        _fn = _kaps.name if isinstance(_kaps, (ast.FunctionDef, ast.AsyncFunctionDef)) else ""
        _fp = _ifade_parmak_izi(ifade)

        # Modül düzeyi bağlar + fonksiyon düzeyi bağlar. `_FINALIZE_SUTUNLARI`
        # gibi modül sabitleri fonksiyon içinden kullanılır; ikisini birleştirmezsek
        # açık anahtar kümesi görülemez.
        modul_baglari, _ = yerel.get(id(agac), ({}, set()))
        ic_kapsam = kapsayan.get(id(dugum), agac)
        ic_baglar, ic_golge = yerel.get(id(ic_kapsam), ({}, set()))
        baglar = {
            ad: v for ad, v in modul_baglari.items() if ad not in ic_golge
        }
        baglar.update(ic_baglar)
        nedenler = _cozulemez_nedenler(ifade, uretici, dugum, baglar)
        if nedenler:
            ihlaller.append(
                Ihlal(bagil, dugum.lineno, sorted(tablolar),
                      "; ".join(sorted(set(nedenler))), _fn, _fp)
            )
            continue

        kapsanan, sabitle = _kapsam_analizi(ifade, tablolar, baglar)
        if sabitle:
            ihlaller.append(Ihlal(
                bagil, dugum.lineno, sorted(set(sabitle)),
                "company_id SABİTLE veriliyor; isteğin kiracısına bağlı değil",
                _fn, _fp,
            ))
            continue
        eksik = sorted(tablolar - kapsanan)
        # KİRACI ANAHTARI TABLOYA GÖRE: `user_company_memberships` kullanıcıya
        # göre kapsanır. Tablo GÖRÜNÜR ve DENETLENİR — muaf değildir; doğru
        # anahtar da bağlı değilse aşağıda kırmızı yanar.
        eksik = [
            t for t in eksik
            if not (
                t in KIRACI_ANAHTARI_ISTISNALARI
                and _alternatif_anahtar_bagli(
                    ifade, baglar, t, KIRACI_ANAHTARI_ISTISNALARI[t],
                    agac, _kaps, (bagil, _fn, _fp),
                )
            )
        ]
        if eksik:
            ihlaller.append(Ihlal(
                bagil, dugum.lineno, eksik,
                "bağlı company_id yüklemi yok",
                _fn, _fp,
            ))
    return ihlaller


# --------------------------------------------------------------------------
# YETKİLENDİRME İSTİSNASI — envanterden AYRI bir iddia
# --------------------------------------------------------------------------
# Bir sorguyu envantere yazmak, onu bu kapıdan MUAF TUTMAK DEĞİLDİR.
# `test_core_query_inventory` sorgunun NE YAPTIĞINI kaydeder; burası NEDEN
# İZİN VERİLDİĞİNİ. İki ayrı iddia, iki ayrı yer.
#
# Ayrım boş bir titizlik değil: bir sorgu sadakatle envanterlenip yine de
# kapıdan GEÇEBİLİR, çünkü geçmesi "izin verildi" anlamına gelmez —
# "sorulmadı" anlamına gelebilir. Yeşil bir CI ikisini AYNI gösterir.
#
# KİMLİK ÜÇLÜSÜ = (kaynak dosya, fonksiyon, ifadenin parmak izi). Yol öneki
# değil, router değil, izin seviyesi değil: TEK bir ifade. Fonksiyon sonradan
# düzenlenirse parmak izi değişir ve lisans KENDİLİĞİNDEN düşer — sonraki bir
# yazar muafiyeti sessizce DEVRALAMAZ.
CEKIRDEK_KIRACI_ISTISNALARI: dict[tuple[str, str, str], str] = {
    (
        "backend/app/routers/platform_audit.py",
        "list_untenanted_audit",
        "a484cea5af5f3c646ac9be74989e9d86d36cf4e7718226921df515e9212aef86",
    ): (
        "Kasıtlı olarak KİRACISIZ denetim satırlarının ayrıcalıklı platform "
        "okuma yolu. `company_id IS NULL` bir kapsam KAÇAĞI değil sorgunun "
        "KONUSUDUR: bu satırlar (giriş denemeleri, kayıt, AUTH_REQUIRED 401) "
        "hiçbir firmaya ait değildir ve kiracıya bağlı her okumanın dışına "
        "düşer. Yetki kiracı kapsamıyla değil `require_platform_operator` ile "
        "verilir. Bkz. göç 20260812_0059 ve CHECK "
        "ck_security_audit_logs_untenanted_only_preauth."
    ),
}


def _lisanssiz(ihlaller: list[Ihlal]) -> list[Ihlal]:
    return [i for i in ihlaller if i.kimlik not in CEKIRDEK_KIRACI_ISTISNALARI]


def test_istisna_gercekten_kullaniliyor() -> None:
    """Her lisans BİR ihlali karşılamalı; ölü lisans birikemez.

    Lisans edilen ifade düzeltilir ya da silinirse ve kayıt burada kalırsa,
    ileride ONA BENZEYEN bir ifadeye sessizce uygulanabilir hâlde durur.
    """
    kimlikler = {i.kimlik for i in core_ihlallerini_bul()}
    olu = sorted(k for k in CEKIRDEK_KIRACI_ISTISNALARI if k not in kimlikler)
    assert not olu, f"karşılığı olmayan (ölü) istisna kaydı: {olu}"
    assert len(CEKIRDEK_KIRACI_ISTISNALARI) == 1, (
        "Bu kapıdaki istisna sayısı arttı. Her yeni kayıt AYRI bir güvenlik "
        "kararıdır ve kendi gerekçesiyle incelenmelidir: "
        f"{sorted(CEKIRDEK_KIRACI_ISTISNALARI)}"
    )


def test_core_ifadeleri_kiraciya_bagli() -> None:
    ihlaller = _lisanssiz(core_ihlallerini_bul())
    assert not ihlaller, (
        "Kiracı tablosuna dokunan Core ifadesi bağlı bir company_id yüklemi "
        "taşımalı (çözülemeyen ifade de ihlaldir):\n  "
        + "\n  ".join(str(i) for i in ihlaller)
    )


# --------------------------------------------------------------------------
# KAPSAM BİLDİRİMİ — eşik değil, TAM SAYIM
# --------------------------------------------------------------------------
# Önceki hâli `sayac >= 50` idi. Bir eşik "tarayıcı büsbütün sustu mu" sorusunu
# cevaplar ama kapsamı GÖSTERMEZ: hem eksilmeyi hem fazlalaşmayı gizler. Bir
# ifadenin sessizce görünmez olması da, kapının bilmediği yeni bir ifadenin
# eklenmesi de eşiğin altında kalabilir.
#
# Bu yüzden hem İFADE SAYISI hem DOKUNULAN KİRACI TABLOSU KÜMESİ birebir
# bildiriliyor; iki yönde de sapma kırmızıdır.
#
# ÖLÇÜLDÜ (2026-08-12, bu tarayıcıyla): 101 ifade, 14 tablo.
# PR gövdesindeki ilk anket "56 çağrı" diyordu; o sayı FARKLI BİR ÖLÇÜTTÜ —
# yalnız `select/insert/update/delete(<kiracı tablosu>)` biçiminde İLK
# ARGÜMANI kiracı tablosu olan çağrıları sayıyordu. Kapının kendi ölçütü
# "zincirin herhangi bir yerinde kiracı tablosuna dokunan en dış ifade"
# olduğundan sayı daha yüksek. Bildirilen rakam kapının kendi ölçtüğüdür.
# 104 -> 120: `memberships` takma adı çözüldüğü için `user_company_memberships`
# artık kapıya GÖRÜNÜYOR. Artış tam olarak +16'dır ve bu, eski donmuş körlüğün
# bildirdiği sayının ta kendisidir (BEKLENEN_ZORLANMAYAN_IFADE = 16). Yani
# görünmez yüzey kaybolmadı, GÖRÜNÜR yüzeye taşındı; sayı bunu doğruluyor.
BEKLENEN_CORE_IFADE_SAYISI = 120

BEKLENEN_KIRACI_TABLOLARI = frozenset({
    "branches",
    "document_sequences",
    "entity_change_logs",
    "finance_accounts",
    "finance_transactions",
    "financial_instruments",
    "notifications",
    "policy_override_logs",
    "products",
    # 20260812: takma ad çözümü (`audit_logs` -> `security_audit_logs`) bu
    # tabloyu kapıya GÖRÜNÜR yaptı. Üç ifade: kiracıya bağlı okuma
    # (routers/auth.py::list_audit), açık company_id ile yazma
    # (main.py::_write_security_audit) ve KASITLI kiracısız platform okuması
    # (routers/platform_audit.py::list_untenanted_audit — istisna kaydı var).
    "security_audit_logs",
    "stock_movements",
    "stock_transfer_items",
    "stock_transfers",
    "warehouse_stocks",
    "warehouses",
    # Takma adı çözüldü; artık görünür ve DENETLENİYOR (anahtarı user_id).
    "user_company_memberships",
})


def _core_kapsami() -> tuple[int, set[str], list[str]]:
    """(ifade sayısı, dokunulan kiracı tabloları, ifade yerleri)."""
    sayi = 0
    tablolar: set[str] = set()
    yerler: list[str] = []
    for yol in sorted(APP_DIR.rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        adlar = _uretici_adlari(agac)
        if not adlar:
            continue
        ebeveynler = _ebeveyn_haritasi(agac)
        gorulen: set[int] = set()
        for dugum in ast.walk(agac):
            if _cagri_ureticisi(dugum, adlar) is None:
                continue
            ifade = _en_dis_ifade(dugum, ebeveynler)
            if id(ifade) in gorulen:
                continue
            dokunulan = _dokunulan_kiraci_tablolari(ifade)
            if not dokunulan:
                continue
            gorulen.add(id(ifade))
            sayi += 1
            tablolar |= dokunulan
            yerler.append(f"{yol.relative_to(BACKEND.parent).as_posix()}:{dugum.lineno}")
    return sayi, tablolar, yerler


def test_kapsam_tam_olarak_bildirildigi_kadar() -> None:
    """Kapının gördüğü ifade sayısı ve tablo kümesi birebir bildirilenle aynı."""
    sayi, tablolar, yerler = _core_kapsami()

    assert sayi == BEKLENEN_CORE_IFADE_SAYISI, (
        "Kiracı tablosuna dokunan Core ifadesi sayısı değişti: "
        f"bildirilen={BEKLENEN_CORE_IFADE_SAYISI}, ölçülen={sayi}.\n"
        "Değişiklik bilerek yapıldıysa BEKLENEN_CORE_IFADE_SAYISI güncellenmeli "
        "ve yeni/kalkan ifadenin kiracı kapsamı gözden geçirilmeli.\n"
        f"Ölçülen yerler:\n  " + "\n  ".join(yerler)
    )

    eksik = sorted(BEKLENEN_KIRACI_TABLOLARI - tablolar)
    fazla = sorted(tablolar - BEKLENEN_KIRACI_TABLOLARI)
    assert not eksik and not fazla, (
        "Core ifadelerinin dokunduğu kiracı tablosu kümesi değişti: "
        f"artık dokunulmayan={eksik}, yeni dokunulan={fazla}.\n"
        "Yeni bir kiracı tablosuna Core üzerinden dokunulmaya başlandıysa o "
        "ifadenin kapsamı gözden geçirilmeli."
    )


# --------------------------------------------------------------------------
# KAPILARIN KANITI — sentetik örnekler
# --------------------------------------------------------------------------
# Kod tabanı bugün temiz olabilir; bu kapının değeri YARIN yazılacak sorguda.
# Aşağıdaki örnekler kapının dört davranışını doğrudan sınar ve her biri
# karşı örneğiyle birlikte gelir: kapı yalnız kırmızı vermeyi değil, doğru
# sorguyu geçirmeyi de yapmalı.
_BASLIK = "from sqlalchemy import select, insert, update, delete, and_\n"


def _ornek(govde: str) -> list[str]:
    return [i.neden for i in _kaynagi_incele("sentetik.py", _BASLIK + govde)]


def test_kapi1_kapsamsiz_core_sorgusu_kirmizi() -> None:
    assert _ornek("x = select(orders.c.id).where(orders.c.id == 5)\n") == [
        "bağlı company_id yüklemi yok"
    ]
    assert _ornek("x = update(orders).where(orders.c.id == 5).values(total=1)\n") == [
        "bağlı company_id yüklemi yok"
    ]
    assert _ornek("x = delete(orders).where(orders.c.id == 5)\n") == [
        "bağlı company_id yüklemi yok"
    ]
    # Karşı örnekler: doğru yazılmış sorgu geçer.
    assert _ornek("x = select(orders.c.id).where(orders.c.company_id == cid)\n") == []
    assert _ornek("x = insert(orders).values(company_id=cid, total=1)\n") == []


def test_kapi2_joinde_tek_taraf_kapsanmissa_kirmizi() -> None:
    tek = _ornek(
        "x = select(orders.c.id).join(payments, payments.c.order_id == orders.c.id)"
        ".where(orders.c.company_id == cid)\n"
    )
    assert tek == ["bağlı company_id yüklemi yok"], tek
    # Karşı örnek: iki taraf kiracı eşitliğiyle bağlıysa geçer.
    assert _ornek(
        "x = select(orders.c.id).join(payments, payments.c.company_id == orders.c.company_id)"
        ".where(orders.c.company_id == cid)\n"
    ) == []


def test_kapi3_cozulemeyen_ifade_kirmizi() -> None:
    # Yüklem başka yerde kuruluyor.
    assert _ornek(
        "def f(kosullar):\n"
        "    return select(orders.c.id).where(and_(*kosullar))\n"
    ) == ["and_(*...) yüklem listesi çözülemiyor"]
    # Sütunlar çalışma zamanında.
    assert _ornek(
        "def f(d):\n    return insert(orders).values(**d)\n"
    ) == [".values(**...) sütunları çalışma zamanında — çözülemiyor"]
    # Hedef tablo bir değişkenden geliyor.
    assert _ornek(
        "def f(tablo):\n    return delete(tablo).where(orders.c.company_id == 1)\n"
    ) != []
    # AYNI ifadeler yerel bir literalden geliyorsa deterministik olarak çözülür.
    assert _ornek(
        "def f(cid):\n"
        "    kosullar = [orders.c.company_id == cid]\n"
        "    return select(orders.c.id).where(and_(*kosullar))\n"
    ) == []
    assert _ornek(
        "def f(cid):\n"
        "    d = {'company_id': cid, 'total': 1}\n"
        "    return insert(orders).values(**d)\n"
    ) == []
    # Ama aynı ad iki kez atanıyorsa artık deterministik değildir → kırmızı.
    assert _ornek(
        "def f(cid, bayrak):\n"
        "    d = {'company_id': cid}\n"
        "    if bayrak:\n"
        "        d = {'total': 1}\n"
        "    return insert(orders).values(**d)\n"
    ) == [".values(**...) sütunları çalışma zamanında — çözülemiyor"]


def test_kapi4_sabitle_kapsam_kirmizi() -> None:
    assert _ornek("x = select(orders.c.id).where(orders.c.company_id == 1)\n") == [
        "company_id SABİTLE veriliyor; isteğin kiracısına bağlı değil"
    ]
    assert _ornek("x = insert(orders).values(company_id=1, total=5)\n") == [
        "company_id SABİTLE veriliyor; isteğin kiracısına bağlı değil"
    ]
    # Karşı örnek: bağlı değer geçer.
    assert _ornek("x = select(orders.c.id).where(orders.c.company_id == cid)\n") == []


def test_kiraci_disi_tablo_kapinin_isi_degil() -> None:
    """Kapı yalnız kiracı tablolarına karışır; gürültü üretmemeli."""
    assert _ornek("x = select(login_attempts.c.id).where(login_attempts.c.ip == ip)\n") == []


def test_kapi5_update_company_id_yazamaz() -> None:
    """Kiracı tablosunda UPDATE satırın SAHİBİNİ değiştiremez.

    Kapsam yükleminin doğru olması yetmez: doğru kiracıdan seçilen bir satır,
    ``values(company_id=...)`` ile başka kiracıya taşınabilir. Bu, engellenmek
    istenen çapraz kiracı yazımının ta kendisidir.
    """
    neden = "UPDATE company_id YAZAMAZ — satırın sahibini değiştirmek çapraz kiracı yazımıdır"
    assert _ornek(
        "x = update(orders).where(orders.c.company_id == cid).values(company_id=digeri)\n"
    ) == [neden]
    # Yayılım içinden gelse de görülür: açık anahtar kümesi statik okunur.
    assert _ornek(
        "SUTUNLAR = ('company_id', 'total')\n"
        "def f(cid, v):\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == [neden]
    # Karşı örnek: aynı yapı company_id İÇERMEYEN bir anahtar kümesiyle geçer.
    assert _ornek(
        "SUTUNLAR = ('total', 'status')\n"
        "def f(cid, v):\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == []
    # Ve opak bir parametre hâlâ kırmızı — bu çağrı yeri için kaçış yok.
    assert _ornek(
        "def f(cid, v):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**v)\n"
    ) == [".values(**...) sütunları çalışma zamanında — çözülemiyor"]


def test_kapi6_baska_kapsamdaki_bag_ihlali_maskelemez() -> None:
    """Bir kapsamdaki literal, BAŞKA kapsamdaki adı çözemez.

    Bu, düzeltilmeden önce YANLIŞ NEGATİF üretiyordu: ``ast.walk`` alt ağaç
    budayamadığı için bir fonksiyondaki ``govde = {...}`` literali, başka bir
    fonksiyondaki opak ``govde`` parametresini çözüyordu; gerçek bir çapraz
    kiracı UPDATE'i güvenli görünüyordu. Kapı susmuştu — en pahalı arıza türü.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"

    # a) Literal BAŞKA bir fonksiyonda; buradaki `govde` bir parametre.
    assert _ornek(
        "def masum(cid):\n"
        "    govde = {'total': 1}\n"
        "    return insert(orders).values(company_id=cid, **govde)\n"
        "def kotucul(cid, govde):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**govde)\n"
    ) == [opak]

    # b) Modül sabiti, aynı adlı bir PARAMETRE tarafından gölgeleniyor.
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def kotucul(cid, GOVDE):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [opak]

    # c) İç içe fonksiyondaki literal dıştakini çözmemeli.
    assert _ornek(
        "def dis(cid, govde):\n"
        "    def ic():\n"
        "        govde = {'total': 1}\n"
        "        return govde\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**govde)\n"
    ) == [opak]

    # KARŞI ÖRNEK 1: bağ AYNI kapsamdaysa çözülür ve geçer.
    assert _ornek(
        "def tek(cid, v):\n"
        "    govde = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**govde)\n"
    ) == []

    # KARŞI ÖRNEK 2: modül sabiti gölgelenmiyorsa kapsayan kapsam olarak
    # kullanılabilir — `_FINALIZE_SUTUNLARI` deseni tam olarak budur.
    assert _ornek(
        "SUTUNLAR = ('total', 'status')\n"
        "def f(cid, v):\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == []


def test_kapi7_global_ve_nonlocal_adlar_cozulemez() -> None:
    """``global``/``nonlocal`` bildirilen ad statik olarak bilinemez.

    Böyle bir adın çağrı anındaki değeri programın çalışma sırasına bağlıdır ve
    başka bir modül onu yeniden bağlayabilir; gözle görünen literal, yürürlükte
    olan değer DEĞİLDİR.

    İKİ YÖN de sınanıyor ve asıl mesele ikincisidir:

      * Görünen literal ``company_id`` İÇERİYOR  → kapı zaten ateşliyordu, ama
        YANLIŞ GEREKÇEYLE: içeriği bildiğini sanıyordu. Artık "çözülemiyor"
        diyor; doğrusu bu, çünkü içeriği bilmiyor.
      * Görünen literal ``company_id`` İÇERMİYOR → düzeltmeden önce GEÇİYORDU.
        Gerçek çapraz kiracı UPDATE'i güvenli görünüyordu. Kapının yalnız
        ateşlediği yönü sınamak, aşırı güvenmediğini KANITLAMAZ.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"

    kuresel_iceren = (
        "GOVDE = {'company_id': 1, 'total': 2}\n"
        "def yeniden_bagla(v):\n"
        "    global GOVDE\n"
        "    GOVDE = v\n"
        "def kotucul(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    )
    kuresel_icermeyen = (
        "GOVDE = {'total': 2}\n"
        "def yeniden_bagla(v):\n"
        "    global GOVDE\n"
        "    GOVDE = v\n"
        "def kotucul(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    )
    ust_iceren = (
        "def dis(cid):\n"
        "    G = {'company_id': 1}\n"
        "    def ic(v):\n"
        "        nonlocal G\n"
        "        G = v\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**G)\n"
    )
    ust_icermeyen = (
        "def dis(cid):\n"
        "    G = {'total': 2}\n"
        "    def ic(v):\n"
        "        nonlocal G\n"
        "        G = v\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**G)\n"
    )

    # YÖN A — görünen literal company_id İÇERİYOR.
    assert _ornek(kuresel_iceren) == [opak], "global, literal iceriyor"
    assert _ornek(ust_iceren) == [opak], "nonlocal, literal iceriyor"

    # KARŞI ÖRNEK: kural `global`/`nonlocal` BİLDİRİMİNE bakar, modül
    # sabitlerine değil. `global` bildirilmemiş ve gölgelenmemiş bir modül
    # demeti hâlâ meşru biçimde çözülür — `_FINALIZE_SUTUNLARI` deseni budur.
    assert _ornek(
        "SUTUNLAR = ('total', 'status')\n"
        "def f(cid, v):\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == []

    # KARŞI ÖRNEK 2: aynı modülde BAŞKA bir ad global bildirilmiş olması,
    # bildirilmemiş adı çözülemez yapmamalı — kural ada özgüdür.
    assert _ornek(
        "SAYAC = 0\n"
        "SUTUNLAR = ('total', 'status')\n"
        "def artir():\n"
        "    global SAYAC\n"
        "    SAYAC = SAYAC + 1\n"
        "def f(cid, v):\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == []


def test_finalize_sutunlari_hala_mesru_bicimde_cozuluyor() -> None:
    """Modül sabiti ve ondan kurulan sözlük kurgusu MEŞRU biçimde çözülüyor.

    Test kendi iddiasını ölçer: `_finalize`in UPDATE'i ihlal vermemeli.
    Dosyanın BAŞKA bir yerindeki ihlal bu testin konusu değildir; onu
    `test_core_ifadeleri_kiraciya_bagli` bildirir. Daha önce bu test tüm
    dosyayı süzüyordu ve başka bir satırın kırmızısını kendi kırmızısı gibi
    gösteriyordu.
    """
    kaynak = (APP_DIR / "notifications" / "service.py").read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    baglar = _yerel_baglar(agac)
    modul_cozulen, _ = baglar[id(agac)]
    assert "_FINALIZE_SUTUNLARI" in modul_cozulen, (
        "modül sabiti çözülemiyor: değişmez demet teslim edilse bile çözülmeli"
    )
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and dugum.name == "_finalize":
            ic_cozulen, _ = baglar[id(dugum)]
            assert "yazilacak" in ic_cozulen, "açık anahtar kümesinden kurulan sözlük çözülmeli"
            break
    else:
        raise AssertionError("_finalize bulunamadı")

    ihlaller = [i for i in core_ihlallerini_bul() if "service.py" in i.yol and 700 < i.satir < 800]
    assert not ihlaller, (
        "_finalize UPDATE'i ihlal veriyor: "
        + "; ".join(str(x) for x in ihlaller)
    )

def test_kapi7b_gorunen_literal_company_id_ICERMESE_DE_cozulemez() -> None:
    """AŞIRI GÜVENME YÖNÜ — düzeltmeden önce tam olarak burası geçiyordu.

    Görünen literal ``company_id`` içermediğinde kapı "demek ki güvenli" diyor
    ve gerçek bir çapraz kiracı UPDATE'i geçiriyordu. Kapının yalnız ateşlediği
    yönü sınamak, aşırı güvenmediğini kanıtlamaz; bu test o boşluğu tutar.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"

    assert _ornek(
        "GOVDE = {'total': 2}\n"
        "def yeniden_bagla(v):\n"
        "    global GOVDE\n"
        "    GOVDE = v\n"
        "def kotucul(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [opak], "global, literal company_id ICERMIYOR"

    assert _ornek(
        "def dis(cid):\n"
        "    G = {'total': 2}\n"
        "    def ic(v):\n"
        "        nonlocal G\n"
        "        G = v\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**G)\n"
    ) == [opak], "nonlocal, literal company_id ICERMIYOR"


# --------------------------------------------------------------------------
# DEKLARE EDİLMİŞ SINIR
# --------------------------------------------------------------------------
# Bu kapı yalnız `backend/app/**/*.py` ağacını okur. Bir bağa bu ağacın
# DIŞINDAN erişilip değiştirilmesi (başka bir paket, test kodu, eklenti,
# `importlib` ile çalışma zamanında yüklenen modül) görülmez ve kapı bunu
# yakalayamaz. Bu bir varsayım değil, bilinen ve yazılı bir sınırdır.
DEKLARE_EDILMIS_SINIR = (
    "Bu kapı yalnız backend/app/**/*.py ağacını okur. Bir bağa bu ağacın "
    "DIŞINDAN erişilip değiştirilmesi (başka bir paket, test kodu, eklenti, "
    "importlib ile çalışma zamanında yüklenen modül) görülmez ve kapı bunu "
    "yakalayamaz. Bu bir varsayım değil, bilinen ve yazılı bir sınırdır."
)

#: `DEKLARE_EDILMIS_SINIR` metninin sha256'sı. Metin daraltılırsa ya da bir
#: kelimesi değişirse bu iz tutmaz ve test kırmızı olur: sınır, örneklerine
#: değil KENDİNE sabitlenmiştir.
BEKLENEN_SINIR_IZI = "00507668de769ae104f77732be83affb0535b4a24bd85af654d8b024ad25d97d"


def test_kapi8_bildirim_kapsama_ozgudur_yazilisa_degil() -> None:
    """``global``/``nonlocal`` yalnız BİLDİRDİĞİ bağı geçersiz kılar.

    Önceki sürüm dosyadaki tüm bildirilen adları tek kümede toplayıp o
    YAZILIŞI her kapsamda reddediyordu. Bildirim taşımayan bir kapsamdaki
    gerçek yerel literal de reddediliyordu — yanlış pozitif.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"

    # AYNI AD, FARKLI KAPSAM: `arm` global bildiriyor, `unrelated` bildirmiyor.
    # `unrelated`in yerel literali GERÇEK bir bağdır ve çözülmelidir.
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def arm(v):\n"
        "    global GOVDE\n"
        "    GOVDE = v\n"
        "def unrelated(cid, v):\n"
        "    GOVDE = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [], "bildirim taşımayan kapsamdaki yerel literal reddedilmemeli"

    # nonlocal için de aynı: bildirim `ic`te, `baska` etkilenmemeli.
    assert _ornek(
        "def dis(v):\n"
        "    G = {'total': 1}\n"
        "    def ic():\n"
        "        nonlocal G\n"
        "        G = v\n"
        "    return G\n"
        "def baska(cid, v):\n"
        "    G = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**G)\n"
    ) == [], "nonlocal bildirimi başka kapsamdaki aynı adı etkilememeli"

    # KARŞI YÖN: bildirimin KENDİ kapsamı ve hedeflediği bağ hâlâ çözülemez.
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def arm(v):\n"
        "    global GOVDE\n"
        "    GOVDE = v\n"
        "def kullan(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [opak], "global bildirilen modül bağı çözülememeli"

    assert _ornek(
        "def dis(cid, v):\n"
        "    G = {'total': 1}\n"
        "    def ic():\n"
        "        nonlocal G\n"
        "        G = v\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**G)\n"
    ) == [opak], "nonlocal hedeflenen dış bağ çözülememeli"


def test_kapi9a_subscript_atamasi_bagi_cozulemez_yapar() -> None:
    """``d['company_id'] = 999`` — atamadan sonra yerinde değişiklik."""
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"
    assert _ornek(
        "def f(cid):\n"
        "    d = {'total': 1}\n"
        "    d['company_id'] = 999\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak]
    # KARŞI YÖN: mutasyon YOKSA aynı yapı çözülür ve geçer.
    assert _ornek(
        "def f(cid):\n"
        "    d = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == []


def test_kapi9b_yerinde_guncelleme_bagi_cozulemez_yapar() -> None:
    """``d.update({...})`` — yerinde değiştiren metot çağrısı."""
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"
    assert _ornek(
        "def f(cid, disaridan):\n"
        "    d = {'total': 1}\n"
        "    d.update(disaridan)\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak]
    # İÇ KAPSAMDAN yapılan mutasyon da görülür: kapanış dış bağı değiştirebilir.
    assert _ornek(
        "def f(cid, disaridan):\n"
        "    d = {'total': 1}\n"
        "    def ic():\n"
        "        d.update(disaridan)\n"
        "    ic()\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak]
    # KARŞI YÖN: hiçbir ek olay yoksa çözülür. `d.get(...)` bilerek KARŞI
    # ÖRNEK DEĞİLDİR: yeni tasarımda öznitelik erişimi de TESLİMDİR, çünkü
    # analizci `get`in ne yaptığını bilmez — kara liste kalmadı.
    assert _ornek(
        "def f(cid):\n"
        "    d = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == []
    assert _ornek(
        "def f(cid):\n"
        "    d = {'total': 1}\n"
        "    x = d.get('total')\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak], "öznitelik erişimi de teslimdir; kara liste yok"


def test_kapi9c_setattr_ve_globals_bagi_cozulemez_yapar() -> None:
    """``setattr(d, ...)`` ve ``globals()['d'] = ...`` — dolaylı yeniden bağlama."""
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"
    assert _ornek(
        "def f(cid, v):\n"
        "    d = {'total': 1}\n"
        "    setattr(d, 'x', v)\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak]
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def arm(v):\n"
        "    globals()['GOVDE'] = v\n"
        "def f(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [opak]
    # Anahtar SABİT DEĞİLSE hangi adın değiştiği bilinemez → kapsamda hiçbir
    # bağ çözülmez (fail-closed).
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def arm(ad, v):\n"
        "    globals()[ad] = v\n"
        "def f(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == [opak]
    # KARŞI YÖN: `globals()` yalnız OKUNUYORSA bağ düşmez.
    assert _ornek(
        "GOVDE = {'total': 1}\n"
        "def bak():\n"
        "    return globals()['GOVDE']\n"
        "def f(cid):\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**GOVDE)\n"
    ) == []


def test_deklare_edilmis_sinir_yazili() -> None:
    """Sınır metni KENDİNE sabitlenir, örneklerine değil.

    Önceki hâli üç alt-dize arıyordu; metin daraltılıp bu üçü korunabilir ve
    test yine yeşil kalırdı. Artık sabitin TAMAMININ sha256'sı sabitlenmiştir:
    tek kelime değişse bile kırmızıdır ve değişikliği yapan onu bilerek
    güncellemek zorundadır.
    """
    izi = hashlib.sha256(DEKLARE_EDILMIS_SINIR.encode("utf-8")).hexdigest()
    assert izi == BEKLENEN_SINIR_IZI, (
        "Deklare edilmiş sınır metni değişti. Daraltıldıysa kapının kapsamı "
        "sessizce büyümüş demektir; bilerek değiştiyse BEKLENEN_SINIR_IZI "
        f"güncellenmeli. Ölçülen: {izi}"
    )
    # Metin yalnız bir sabitin içinde saklı kalmamalı; okuyanın göreceği yerde
    # yorum bloğu olarak da dursun.
    kaynak = Path(__file__).read_text(encoding="utf-8")
    assert "# DEKLARE EDİLMİŞ SINIR" in kaynak

def test_kapi10_yedi_bicim_de_fail_closed() -> None:
    """İncelemenin ölçtüğü yedi yanlış-yeşil biçim — hepsi gerileme testi.

    Bunlar KANIT DEĞİL, gerilemedir. Kanıt `test_kapi11_...` içindedir:
    sınıflandırılmamış bir biçimin kırmızı üretmesi. Yedi biçim, tek bir
    tasarım kusurunun görünümleriydi — çözümleyici "tanıdığım tehlike yoksa
    çöz" diye kuruluydu.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"
    bicimler = {
        "dict.update(d, ...) bağsız biçim": (
            "def f(cid, x):\n"
            "    d = {'total': 1}\n"
            "    dict.update(d, x)\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "d.custom_update() — kara listede olmayan metot": (
            "def f(cid):\n"
            "    d = {'total': 1}\n"
            "    d.custom_update()\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "takma ad: e = d; e['company_id'] = 999": (
            "def f(cid):\n"
            "    d = {'total': 1}\n"
            "    e = d\n"
            "    e['company_id'] = 999\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "walrus (d := ...)": (
            "def f(cid, g):\n"
            "    d = {'total': 1}\n"
            "    if (d := g()):\n"
            "        pass\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "except ... as d": (
            "def f(cid):\n"
            "    d = {'total': 1}\n"
            "    try:\n"
            "        pass\n"
            "    except KeyError as d:\n"
            "        pass\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "case {'k': d}": (
            "def f(cid, x):\n"
            "    d = {'total': 1}\n"
            "    match x:\n"
            "        case {'k': d}:\n"
            "            pass\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
        "import ... as d": (
            "def f(cid):\n"
            "    d = {'total': 1}\n"
            "    from payloads import DANGER as d\n"
            "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
        ),
    }
    for ad, govde in bicimler.items():
        assert _ornek(govde) == [opak], f"fail-open: {ad}"

    # KARŞI YÖN: hiçbir olay eklenmemişse aynı yapı çözülür ve geçer.
    assert _ornek(
        "def f(cid):\n"
        "    d = {'total': 1}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == []


def test_kapi11_siniflandirilmamis_bicim_kirmizi_uretir() -> None:
    """ASIL KANIT: çözümleyicinin SINIFLANDIRMADIĞI bir biçim kırmızı verir.

    Yedi biçimi tek tek kapatmak whack-a-mole olurdu. Bu test, kapının
    tasarımını sınar: analizcinin sınıflandırma listesinde OLMAYAN bir düğüm
    türü bir adı içeriyorsa o ad düşer. Dolayısıyla gelecekteki bir Python
    biçimi — bugün ikimizin de aklına gelmemiş olan — birisi onu açıkça
    sınıflandırana kadar KIRMIZIDIR.

    Test, örneği sabitlemek yerine ÖNCE listede olmadığını doğrular; liste
    büyüdüğünde bu test kendini günceller ya da kırmızıya döner, sessizce
    anlamsızlaşmaz.
    """
    # `match` deyimi bu kod tabanında kullanılmıyor ve bilerek
    # sınıflandırılmamıştır. Önce bunu ÖLÇ.
    for tur in ("Match", "MatchValue", "MatchMapping", "MatchAs"):
        assert tur not in SINIFLANDIRILMIS_DUGUMLER, (
            f"{tur} artık sınıflandırılmış; bu test başka bir sınıflandırılmamış "
            "biçimle güncellenmeli — kanıt örneğe değil MEKANİZMAYA bağlıdır"
        )

    # `**d` GÜVENLİ OKUMADIR; tek başına bağı düşürmez. Aynı çağrı
    # sınıflandırılmamış bir düğümün İÇİNDE olduğunda düşer. Kırmızıyı
    # üreten şey böylece YALNIZ sınıflandırılmama kuralıdır — teslim değil.
    match_icinde = (
        "def f(cid, x, g2):\n"
        "    d = {'total': 1}\n"
        "    match x:\n"
        "        case _:\n"
        "            g2(**d)\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    )
    match_disinda = (
        "def f(cid, g2):\n"
        "    d = {'total': 1}\n"
        "    g2(**d)\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    )
    assert _ornek(match_icinde) == [
        ".values(**...) sütunları çalışma zamanında — çözülemiyor"
    ], "sınıflandırılmamış düğüm sessiz kalmamalı"
    assert _ornek(match_disinda) == [], (
        "aynı çağrı sınıflandırılmış bağlamda geçmeli — yoksa bu test "
        "sınıflandırılmama kuralını değil başka bir şeyi ölçüyor demektir"
    )


def test_kapi12_teslim_edilen_deger_cozulmez_degismez_olan_cozulur() -> None:
    """Teslim (hand-off): izlenemeyen yere verilen DEĞİŞTİRİLEBİLİR bağ düşer.

    Değişmez bir literal (yalnız sabitlerden oluşan demet) yerinde
    değiştirilemeyeceği için teslim edilmesi bağı bozmaz — `_FINALIZE_SUTUNLARI`
    deseni tam olarak budur ve meşruluğu bu ayrımdan gelir.
    """
    opak = ".values(**...) sütunları çalışma zamanında — çözülemiyor"

    # Değiştirilebilir sözlük bir çağrıya veriliyor → düşer.
    assert _ornek(
        "def f(cid, kaydet):\n"
        "    d = {'total': 1}\n"
        "    kaydet(d)\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**d)\n"
    ) == [opak]

    # Değişmez demet teslim edilse bile çözülür.
    assert _ornek(
        "SUTUNLAR = ('total', 'status')\n"
        "def f(cid, v):\n"
        "    kontrol = set(SUTUNLAR)\n"
        "    y = {ad: v[ad] for ad in SUTUNLAR if ad in v}\n"
        "    return update(orders).where(orders.c.company_id == cid).values(**y)\n"
    ) == []


# İkinci savunma etiketleri. "yok" DEMEK, o türün hiç ad bağlamaması demektir.
IKINCI_SAVUNMALAR = frozenset({"_string_baglari", "Name/ctx denetimi", "yok"})


def test_kapi13a_siniflandirilmis_kume_capayla_birebir() -> None:
    """Sınıflandırılmış küme SESSİZCE BÜYÜYEMEZ.

    Bu küme, kapının fail-open/fail-closed sınırını çizer: içindeki bir tür
    "analizci bunu anlıyor" demektir, dışındaki her tür kırmızıdır. `kapi11`
    mekanizmayı TEK BİR örnekle (`match`) çiviliyor; başka bir tür eklendiğinde
    o test yeşil kalır ve sınır sessizce genişler. Bu yüzden üyeliğin KENDİSİ
    bağımsız bir çapayla sabitlenmiştir — projenin nüfus çapalarıyla aynı
    desen: küme eşitliği, iki yönde.
    """
    eksik = sorted(BEKLENEN_SINIFLANDIRILMIS - SINIFLANDIRILMIS_DUGUMLER)
    fazla = sorted(SINIFLANDIRILMIS_DUGUMLER - BEKLENEN_SINIFLANDIRILMIS)
    assert not eksik and not fazla, (
        "Sınıflandırılmış düğüm kümesi çapadan ayrıştı. "
        f"çapada olup kümede olmayan={eksik}, kümeye eklenmiş={fazla}. "
        "Bir tür eklemek fail-closed sınırını DARALTIR: eklenmesi bilinçli bir "
        "işlem olmalı ve hem SINIFLANDIRMA_KANITLARI hem "
        "BEKLENEN_SINIFLANDIRILMIS güncellenmelidir."
    )


def test_kapi13b_her_siniflandirilmis_tur_kendi_kanitini_tasir() -> None:
    """Her üye, sınıflandırılmasının NEDEN güvenli olduğunu yazılı taşır.

    Çapa tek başına yetmez: değişikliği GÖRÜNÜR yapar ama GEREKÇELİ yapmaz.
    Küme doğrudan kanıt tablosundan türetildiği için gerekçesiz üye zaten
    imkânsızdır; bu test gerekçenin içeriğini de sınar.

    KATMANLILIK: sınıflandırma tek savunma hattı DEĞİLDİR. Ad bağlayan bir tür
    sınıflandırılsa bile bağladığı adlar ikinci bir mekanizmayla yakalanır —
    `_string_baglari` (string ile bağlayanlar) ya da `Name`/ctx denetimi. Her
    üye hangi ikinci hattın kendisini koruduğunu söylemek zorundadır.
    """
    assert set(SINIFLANDIRMA_KANITLARI) == SINIFLANDIRILMIS_DUGUMLER, (
        "küme kanıt tablosundan türetilmeli; ikinci bir elle liste tutulmamalı"
    )

    kusurlu: list[str] = []
    for tur, kanit in sorted(SINIFLANDIRMA_KANITLARI.items()):
        if not isinstance(kanit, tuple) or len(kanit) != 2:
            kusurlu.append(f"{tur}: kanıt (ikinci savunma, gerekçe) çifti değil")
            continue
        ikinci, gerekce = kanit
        if ikinci not in IKINCI_SAVUNMALAR:
            kusurlu.append(f"{tur}: bilinmeyen ikinci savunma etiketi {ikinci!r}")
        if not isinstance(gerekce, str) or len(gerekce.strip()) < 25:
            kusurlu.append(f"{tur}: gerekçe yok ya da içi boş")
    assert not kusurlu, "Kanıtsız ya da kusurlu sınıflandırma:\n  " + "\n  ".join(kusurlu)

    # Ad BAĞLAYAN türler "yok" diyemez: onları ikinci hat tutuyor olmalı.
    baglayanlar = {
        "ExceptHandler", "alias", "Global", "Nonlocal", "FunctionDef",
        "AsyncFunctionDef", "ClassDef", "arg", "arguments",
        "Assign", "AnnAssign", "AugAssign", "For", "AsyncFor", "withitem",
        "NamedExpr", "comprehension", "Delete", "Name",
    }
    korumasiz = sorted(
        tur for tur in baglayanlar
        if SINIFLANDIRMA_KANITLARI.get(tur, ("yok", ""))[0] == "yok"
    )
    assert not korumasiz, (
        "Ad bağlayan tür ikinci savunma bildirmeden sınıflandırılamaz: "
        f"{korumasiz}"
    )


def test_kapi13c_kanit_tablosu_capayi_tek_basina_belirlemez() -> None:
    """Çapa ile tablo AYRI iki kayıttır; biri diğerinden üretilmez.

    Çapa tablodan türetilseydi hiçbir şeyi sabitlemezdi: tabloya eklenen tür
    çapaya da kendiliğinden girer ve test hep yeşil kalırdı. Bu test, çapanın
    kaynakta ELLE yazılmış bir küme literali olduğunu doğrular.
    """
    kaynak = Path(__file__).read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    for dugum in agac.body:
        if (
            isinstance(dugum, ast.Assign)
            and isinstance(dugum.targets[0], ast.Name)
            and dugum.targets[0].id == "BEKLENEN_SINIFLANDIRILMIS"
        ):
            assert isinstance(dugum.value, ast.Call), "çapa bir frozenset(...) çağrısı olmalı"
            assert dugum.value.args and isinstance(dugum.value.args[0], ast.Set), (
                "çapa ELLE yazılmış bir küme literali olmalı; tablodan türetilirse "
                "hiçbir şeyi sabitlemez"
            )
            adlar = {
                oge.value for oge in dugum.value.args[0].elts
                if isinstance(oge, ast.Constant)
            }
            assert len(adlar) == len(dugum.value.args[0].elts), "çapada yinelenen tür var"
            return
    raise AssertionError("BEKLENEN_SINIFLANDIRILMIS bulunamadı")


# --------------------------------------------------------------------------
# İSTİSNANIN SINIRLARI — bunlar olmadan "istisna" değil DELİK olurdu
# --------------------------------------------------------------------------
# Bir istisna, kapıya eklenebilecek EN TEHLİKELİ şeydir. Aşağıdakiler lisansın
# TAM OLARAK bir iddiayı kapsadığını, komşusunu kapsamadığını gösterir.

_LISANSLI_YOL = "backend/app/routers/platform_audit.py"
_LISANSLI_FN = "list_untenanted_audit"
_SENTETIK_BASLIK = "from sqlalchemy import select\n"


def _sentetik_ihlaller(govde: str, yol: str = _LISANSLI_YOL) -> list[Ihlal]:
    return _kaynagi_incele(yol, _SENTETIK_BASLIK + govde)


def test_lisans_ayni_dosyadaki_komsu_sorguyu_KAPSAMAZ() -> None:
    """AYNI dosyada BAŞKA bir kiracı tablosu okuması hâlâ kırmızı olmalı.

    Lisans dosyaya değil TEK BİR İFADEYE verildi. Bu iddia olmasaydı
    `platform_audit.py` içine yazılacak her kapsamsız sorgu muafiyeti
    sessizce devralırdı — kapı genişlemiş, adı "istisna" kalmış olurdu.
    """
    ihlaller = _sentetik_ihlaller(
        "def komsu_okuma(db):\n"
        "    return db.execute(select(products.c.id)).all()\n"
    )
    assert len(ihlaller) == 1, ihlaller
    assert _lisanssiz(ihlaller) == ihlaller, (
        "aynı dosyadaki komşu sorgu lisansı DEVRALDI; istisna dosya geneline "
        "yayılmış demektir"
    )
    assert ihlaller[0].neden == "bağlı company_id yüklemi yok"


def test_lisans_ayni_ISIMLI_ama_DEGISMIS_fonksiyonu_KAPSAMAZ() -> None:
    """Fonksiyon adı aynı, gövde farklı → parmak izi farklı → lisans DÜŞER.

    Lisansın devralınmazlığı budur: birisi ileride bu fonksiyonu değiştirip
    başka bir kiracı tablosunu kapsamsız okumaya başlarsa, eski gerekçe yeni
    sorguyu örtmez.
    """
    ihlaller = _sentetik_ihlaller(
        f"def {_LISANSLI_FN}(db):\n"
        "    return db.execute(select(products.c.id)).all()\n"
    )
    assert len(ihlaller) == 1, ihlaller
    assert ihlaller[0].yol == _LISANSLI_YOL
    assert ihlaller[0].fonksiyon == _LISANSLI_FN
    assert _lisanssiz(ihlaller) == ihlaller, (
        "aynı isimli ama DEĞİŞMİŞ fonksiyon lisansı devraldı; parmak izi "
        "bağlaması çalışmıyor"
    )


def test_lisansli_ifadenin_KENDISI_lisansli() -> None:
    """Karşı örnek: kapı yalnız kırmızı vermeyi değil, DOĞRU olanı geçirmeyi de yapmalı.

    Yukarıdaki iki iddia, her şeyi reddeden bozuk bir lisans kontrolünden de
    geçerdi. Bu üçüncüsü lisansın gerçekten TUTTUĞUNU gösterir.
    """
    gercek = [i for i in core_ihlallerini_bul() if i.yol == _LISANSLI_YOL]
    assert len(gercek) == 1, gercek
    assert gercek[0].fonksiyon == _LISANSLI_FN
    assert _lisanssiz(gercek) == [], (
        "gerçek platform okuma yolu lisanslı olmasına rağmen kırmızı"
    )


def test_kiraciya_bagli_yazilirsa_ihlal_KALMAZ() -> None:
    """Aynı fonksiyon kiracıya BAĞLI yazılsaydı ortada ihlal olmazdı.

    Yani lisans, kapsamsızlığı "düzelten" şey değil; yalnızca bu tek kasıtlı
    kapsamsızlığı KAYDEDEN şey. Böyle yazılsaydı istisna da ÖLÜ kalırdı ve
    `test_istisna_gercekten_kullaniliyor` bunu yakalardı.
    """
    ihlaller = _sentetik_ihlaller(
        f"def {_LISANSLI_FN}(db, cid):\n"
        "    return db.execute(\n"
        "        select(audit_logs).where(audit_logs.c.company_id == cid)\n"
        "    ).all()\n"
    )
    assert ihlaller == [], ihlaller


# --------------------------------------------------------------------------
# TAKMA AD ENVANTERİ — körlük kapatılmadıysa bile SAYILIR
# --------------------------------------------------------------------------
def test_takma_ad_envanteri_bildirildigi_gibi() -> None:
    """`X = Table("y")` bağlarının kiracı tablosuna çözülenleri tam olarak bildirilen küme.

    Kapı düğümün ADINA bakıyor; yeni bir takma ad eklenirse o tablo kapıya
    GÖRÜNMEZ olur ve sessizce muaf kalırdı. Bu test yeni takma adı kırmızıya
    çevirir.
    """
    bulunan: dict[str, str] = {}
    for yol in sorted(APP_DIR.rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        for dugum in ast.walk(agac):
            if not (isinstance(dugum, ast.Assign) and isinstance(dugum.value, ast.Call)):
                continue
            fn = dugum.value.func
            ad = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if ad != "Table" or not dugum.value.args:
                continue
            ilk = dugum.value.args[0]
            if not (isinstance(ilk, ast.Constant) and isinstance(ilk.value, str)):
                continue
            for hedef in dugum.targets:
                if isinstance(hedef, ast.Name) and hedef.id != ilk.value:
                    bulunan[hedef.id] = ilk.value

    kiraci_takmalari = {k: v for k, v in bulunan.items() if v in TENANT_TABLES}
    bildirilen = dict(TABLO_TAKMA_ADLARI)
    assert kiraci_takmalari == bildirilen, (
        "Kiracı tablosuna çözülen takma ad kümesi değişti.\n"
        f"  ölçülen  = {kiraci_takmalari}\n"
        f"  bildirilen = {bildirilen}\n"
        "Yeni bir takma ad, o tabloyu bu kapıya GÖRÜNMEZ yapar; "
        "TABLO_TAKMA_ADLARI'na eklenip ZORLANMALIDIR."
    )


def test_memberships_takma_adi_COZULUYOR() -> None:
    """Kör nokta KAPATILDI: tablo artık kapıya GÖRÜNÜYOR."""
    assert TABLO_TAKMA_ADLARI.get("memberships") == "user_company_memberships"
    assert "user_company_memberships" in BEKLENEN_KIRACI_TABLOLARI


def test_memberships_ifadeleri_KULLANICI_anahtariyla_gecer() -> None:
    """Beş ifade de `user_id` bağlıyor; tablo görünür ve DENETLENİYOR."""
    ihlaller = [
        i for i in core_ihlallerini_bul()
        if "user_company_memberships" in i.tablolar
    ]
    assert ihlaller == [], [f"{i.yol}:{i.satir} {i.neden}" for i in ihlaller]


def test_memberships_KULLANICI_anahtari_da_yoksa_KIRMIZI() -> None:
    """Muafiyet degil, ANAHTAR DEGISIKLIGI: user_id de bagli degilse kirmizi."""
    NL = chr(10)
    kod = NL.join([
        "def f(db):",
        "    return db.execute(select(memberships.c.company_id)).all()",
        "",
    ])
    ihlaller = _sentetik_ihlaller(kod)
    assert len(ihlaller) == 1, ihlaller
    assert "user_company_memberships" in ihlaller[0].tablolar
    assert ihlaller[0].neden == "bagli company_id yuklemi yok".replace(
        "bagli", "bağlı").replace("yuklemi", "yüklemi")


def test_memberships_SABIT_kullanici_kapsama_SAYILMAZ() -> None:
    """`user_id == 5` sabittir; istegin kullanicisina bagli degildir."""
    NL = chr(10)
    kod = NL.join([
        "def f(db):",
        "    return db.execute(",
        "        select(memberships.c.company_id).where(memberships.c.user_id == 5)",
        "    ).all()",
        "",
    ])
    assert len(_sentetik_ihlaller(kod)) == 1


def test_memberships_CIPLAK_parametre_ARTIK_GECMEZ() -> None:
    """Bu satırın İŞARETİ DEĞİŞTİ — kapatılan delik tam olarak buydu.

    Eski sözleşmede `memberships.c.user_id == user_id` bir CONTROL satırıydı
    ve GEÇİYORDU: kural "sabit olmayan sağ taraf yeterli" diyordu. İnceleme
    (4986380382) bunu canlı delik saydı ve haklıydı — `user_id` çağıranın
    verdiği herhangi bir kullanıcı olabilir; kural onun KİMLİK DOĞRULANMIŞ
    kullanıcı olduğunu hiç kurmuyordu. RLS'siz bir depoda bu doğrudan kiracı
    sınırı zayıflığıdır.

    Sentetik kodda `f`in hiç çağrı yeri yoktur, dolayısıyla yüklemin (b)
    şıkkı kanıtlanamaz ve FAIL-CLOSED devreye girer: kimlik taşımıyor.
    """
    NL = chr(10)
    kod = NL.join([
        "def f(db, user_id):",
        "    return db.execute(",
        "        select(memberships.c.company_id).where(memberships.c.user_id == user_id)",
        "    ).all()",
        "",
    ])
    assert len(_sentetik_ihlaller(kod)) == 1, (
        "çıplak parametre kimlik sayıldı; kapatılan delik geri açılmış"
    )


def test_memberships_ASIL_OKUMASI_ile_gecer() -> None:
    """CONTROL (KARŞI YÖN): asilin doğrudan okuması KAPSAMA SAYILIR.

    Her şeyi reddeden bir kapı da yukarıdaki satırı "kırmızı" gösterirdi.
    Bu satır ayırt ediciliği ölçüyor: yüklemin (a) şıkkı geçmelidir.
    """
    NL = chr(10)
    kod = NL.join([
        "def f(db, request):",
        "    return db.execute(",
        "        select(memberships.c.company_id)"
        ".where(memberships.c.user_id == request.state.user['id'])",
        "    ).all()",
        "",
    ])
    assert _sentetik_ihlaller(kod) == [], (
        "asilin doğrudan okuması reddedildi; kapı her şeyi reddediyor olabilir"
    )


def test_yeniden_takma_ad_yuzeyi_SIFIR_ve_donduruldu() -> None:
    """`.alias()` ve akrabaları: ölçülen sıfır, çapalandı.

    Sıfır en güçlü sonuçtur ama yazılmamış sıfır geri büyür. Biri
    `orders.alias("o")` yazarsa tablo bu kapıya GÖRÜNMEZ olur; burası kırmızı
    yanar ve o ifade "çözücü hallediyordur" varsayımıyla geçmez.
    """
    bulunan: list[str] = []
    for yol in sorted(APP_DIR.rglob("*.py")):
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        bagil = yol.relative_to(APP_DIR.parents[1]).as_posix()
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call):
                continue
            islev = dugum.func
            ad = None
            if isinstance(islev, ast.Attribute):
                ad = islev.attr
            elif isinstance(islev, ast.Name):
                ad = islev.id
            if ad in YENIDEN_TAKMA_AD_URETICILERI:
                bulunan.append(f"{bagil}:{dugum.lineno} {ad}()")
    assert len(bulunan) == BEKLENEN_YENIDEN_TAKMA_AD_SAYISI, (
        "Yeniden takma adlama yüzeyi büyüdü; bir kiracı tablosu bu kapıya "
        "Yeniden takma adlama yuzeyi buyudu; bir kiraci tablosu bu kapiya "
        "gorunmez hale gelmis olabilir:" + chr(10) + chr(10).join(bulunan)
    )


def test_cozucu_sinir_bicimleri_bugun_kod_tabaninda_YOK() -> None:
    """Bildirilen ON BİR biçimin GERÇEK örneği bugün yok — ve bu sıfır DONDURULDU.

    Sıfır, en güçlü sonuç; ama yazılmamış bir sıfır geri büyür. Biri yarın
    tabloyu bir sözlüğe koyarsa burası kırmızı yanar ve o sorgu "çözücü
    zaten hallediyordur" varsayımıyla incelenmeden geçmez.
    """
    bulgular = cozucu_sinir_ornekleri()
    assert len(bulgular) == BEKLENEN_COZUCU_SINIR_IHLALI, (
        "Çözücünün BİLDİRİLMİŞ sınırlarından biri artık kod tabanında GERÇEKTEN "
        f"var: bildirilen={BEKLENEN_COZUCU_SINIR_IHLALI}, ölçülen={len(bulgular)}.\n  "
        + "\n  ".join(bulgular)
        + "\nBu ifade kapıya GÖRÜNMEZ; incelenmeden bırakılamaz."
    )


# HER BİÇİM İÇİN TEK TEK ÖRNEK — ölçülerek yazıldı, elle tahmin edilmedi.
# (ad, kod, beklenen etiket, beklenen bulgu sayısı)
BICIM_ORNEKLERI: tuple[tuple[str, str, str, int], ...] = (
    ("ikincil yeniden takma", "t2 = audit_logs\n", "atama-ad/doğrudan", 1),
    ("koşullu atama", "if True:\n    kosullu = audit_logs\n", "atama-ad/doğrudan", 1),
    ("üçlü ifade", "uclu = audit_logs if True else baska\n", "atama-ad/üçlü", 2),
    ("fonksiyon dönüşü", "def tablo():\n    return audit_logs\n", "dönüş/doğrudan", 1),
    ("sözlükte saklama", 'TABLOLAR = {"denetim": audit_logs}\n', "sözlük/doğrudan", 1),
    ("yürüyen atama", "if (yuruyen := audit_logs):\n    pass\n",
     "yürüyen-atama-ad/doğrudan", 1),
    ("demet açma", "demet, _k = audit_logs, 1\n", "atama-demet/demet-öğesi", 1),
    ("açıklamalı atama", "aciklamali: int = audit_logs\n",
     "açıklamalı-atama-ad/doğrudan", 1),
    ("parametre varsayılanı", "def parametreli(v=audit_logs):\n    pass\n",
     "parametre-varsayılanı-parametre/doğrudan", 1),
    ("öznitelik ataması", "class Kutu:\n    pass\nkutu = Kutu()\nkutu.oz = audit_logs\n",
     "atama-öznitelik/doğrudan", 1),
    ("abonelik ataması", "abone = {}\nabone['k'] = audit_logs\n",
     "atama-abonelik/doğrudan", 1),
    ("lambda gövdesi", "lam = lambda: audit_logs\n", "atama-ad/lambda-gövdesi", 1),
    ("ve/veya", "veya = audit_logs or None\n", "atama-ad/ve-veya", 1),
    ("kümede saklama", "kume = {audit_logs}\n", "atama-ad/demet-öğesi", 1),
    ("sözlük anahtarı", "anahtar = {audit_logs: 1}\n", "sözlük-anahtarı/doğrudan", 1),
    ("çağrı anahtar argümanı", "cagri = dict(k=audit_logs)\n", "çağrı/çağrı-anahtarı", 1),
    ("desen bağlama", "match audit_logs:\n    case desen:\n        pass\n",
     "eşleme-ad/doğrudan", 1),
    # Bu ikisinin etiketi YEDEK yoldan gelir: `FunctionDef`/`ClassDef`
    # `_BAGLAMA_ADLARI` sözlüğünde YOKTUR ve etiket AST sınıf adına düşer.
    # Bilerek böyle bırakıldı — bilinmeyen bir yapının yine de SAYILDIĞINI ve
    # ETİKETLENDİĞİNİ kapılayan tek örnek bunlar.
    ("dekoratör", "@audit_logs\ndef dekore():\n    pass\n",
     "FunctionDef-ad/doğrudan", 1),
    ("sınıf tabanı", "class Turetilmis(audit_logs):\n    pass\n",
     "ClassDef-ad/doğrudan", 1),
)

# BİLDİRİLEN ve KAPATILMAYAN sınırların ÖRNEKLERİ. Bunlar KAÇMALI. Test,
# kaçmayı ÇİVİLER: biri sessizce kapanırsa burası kırmızı yanar ve
# `COZUCU_KAPATILMAYAN_SINIRLAR` bildirimi gerçekle yeniden hizalanır.
# Bir sınırın SESSİZCE kapanması da bir bildirim yalanıdır.
KAPATILMAYAN_ORNEKLERI: tuple[tuple[str, str], ...] = (
    ("dinamik erişim", "import types\nmm = types.SimpleNamespace()\n"
                       "t = getattr(mm, 'audit_logs')\n"),
    ("çağrı sonucu", "t = audit_logs.alias()\n"),
    ("çağrının konumsal argümanı", "t = list((audit_logs,))\n"),
    ("kapsayıcıyı okuma", "t = [audit_logs][0]\n"),
)

_TABLO_KAYNAGI = (
    "from sqlalchemy import Table, MetaData\n"
    "metadata = MetaData()\n"
    'audit_logs = Table("security_audit_logs", metadata)\n'
    'baska = Table("orders", metadata)\n'
)
# KONTROL gövdesi: tablo İÇE AKTARILIR ve KULLANILIR ama hiçbir bağlama biçimi
# yoktur. Boş bir dosya zayıf kontrol olurdu — sıfırı dosyanın boşluğu da
# üretebilirdi. Burada sıfır, ölçütün SIRADAN kullanımı saymadığını gösterir.
_KONTROL_GOVDESI = (
    "from sqlalchemy import select\n"
    "q1 = select(audit_logs.c.id)\n"
    "q2 = audit_logs.select()\n"
    "q3 = audit_logs.c.company_id\n"
    "q4 = audit_logs.join(baska)\n"
    "q5 = select(audit_logs)\n"
)


def _sentetik_bulgular(kok: Path, govde: str) -> list[str]:
    """Sentetik bir app ağacı kurar ve ölçümü döndürür."""
    sahte = kok / "app"
    sahte.mkdir(parents=True, exist_ok=True)
    (sahte / "tablolar.py").write_text(_TABLO_KAYNAGI, encoding="utf-8")
    (sahte / "kullanim.py").write_text(
        "from .tablolar import audit_logs, baska\n" + govde, encoding="utf-8"
    )
    return cozucu_sinir_ornekleri(sahte)


@pytest.mark.parametrize(
    "ad,kod,beklenen_etiket,beklenen_sayi",
    BICIM_ORNEKLERI,
    ids=[o[0] for o in BICIM_ORNEKLERI],
)
def test_her_bicim_VARKEN_kirmizi_YOKKEN_yesil(
    tmp_path: Path, ad: str, kod: str, beklenen_etiket: str, beklenen_sayi: int,
) -> None:
    """Her bildirilen biçim İKİ YÖNDE de kapılanır.

    Tek yön yetmez. Yalnız "varken görülüyor" denseydi, her şeyi sayan bir
    dedektör de geçerdi; yalnız "yokken sıfır" denseydi, hiçbir şeyi saymayan
    bir dedektör de geçerdi. İkisi birlikte ölçümü sıkıştırır.
    """
    # KONTROL — biçim YOKKEN sıfır (sıradan kullanım var, bağlama yok).
    kontrol = _sentetik_bulgular(tmp_path / "kontrol", _KONTROL_GOVDESI)
    assert kontrol == [], (
        f"[{ad}] KONTROL yeşil olmalıydı: biçim yokken ölçüm bulgu üretti; "
        "bu, biçim varken görülen kırmızıyı ANLAMSIZ kılar.\n  "
        + "\n  ".join(kontrol)
    )

    # KAPI — biçim VARKEN kırmızı, ve DOĞRU etiketle.
    bulgular = _sentetik_bulgular(tmp_path / "kapi", _KONTROL_GOVDESI + kod)
    etiketler = {b.split(" ")[1] for b in bulgular}
    assert beklenen_etiket in etiketler, (
        f"[{ad}] biçim GÖRÜLMEDİ; donmuş sıfır bu biçim için anlamsız olurdu.\n"
        f"  beklenen etiket: {beklenen_etiket}\n"
        f"  görülen        : {sorted(etiketler)}\n  " + "\n  ".join(bulgular)
    )
    ayni = [b for b in bulgular if b.split(" ")[1] == beklenen_etiket]
    assert len(ayni) == beklenen_sayi, (
        f"[{ad}] bulgu SAYISI değişti: beklenen={beklenen_sayi} "
        f"ölçülen={len(ayni)}. Bir biçim iki kez sayılıyor ya da bir örnek "
        f"sessizce düşüyor olabilir.\n  " + "\n  ".join(ayni)
    )


@pytest.mark.parametrize(
    "ad,kod", KAPATILMAYAN_ORNEKLERI, ids=[o[0] for o in KAPATILMAYAN_ORNEKLERI],
)
def test_bildirilen_sinirlar_HALA_kaciyor(tmp_path: Path, ad: str, kod: str) -> None:
    """BİLDİRİLEN sınırlar gerçekten kaçıyor — bildirim doğru mu, ölçülür.

    Bir sınırı bildirip sonra sessizce kapatmak da, kapatmayıp sessiz bırakmak
    da okuyanı yanıltır. Bu test bildirimi GERÇEĞE bağlar: sınır kapanırsa
    kırmızı yanar ve `COZUCU_KAPATILMAYAN_SINIRLAR` güncellenmek zorunda kalır.
    """
    bulgular = _sentetik_bulgular(tmp_path / "sinir", kod)
    assert bulgular == [], (
        f"[{ad}] BİLDİRİLEN sınır artık KAÇMIYOR — yani bildirim yanlış. "
        "Sınır kapandıysa `COZUCU_KAPATILMAYAN_SINIRLAR` listesinden "
        f"çıkarılmalı.\n  " + "\n  ".join(bulgular)
    )


def test_bildirim_ile_olcum_ayni_biCimleri_sayiyor() -> None:
    """Bildirilen biçim sayısı ile tek tek kapılanan biçim sayısı EŞİT.

    Bildirime bir satır eklenip örneği eklenmezse, o biçim ölçülmeden
    "bildirildi" görünürdü — bu PR'ın kapattığı kusurun tam olarak kendisi.
    """
    assert len(BICIM_ORNEKLERI) == len(COZUCU_SINIR_BICIMLERI), (
        f"bildirilen biçim={len(COZUCU_SINIR_BICIMLERI)} ama tek tek kapılanan "
        f"örnek={len(BICIM_ORNEKLERI)}. Bildirilen her biçimin ÖLÇÜLEN bir "
        "örneği olmalı."
    )
    assert len(KAPATILMAYAN_ORNEKLERI) == len(COZUCU_KAPATILMAYAN_SINIRLAR), (
        f"bildirilen sınır={len(COZUCU_KAPATILMAYAN_SINIRLAR)} ama örnek="
        f"{len(KAPATILMAYAN_ORNEKLERI)}."
    )


def test_sinir_olcumu_TUM_bildirilen_bicimleri_AYNI_agacta_gorebiliyor(
    tmp_path: Path,
) -> None:
    """KARŞI ÖRNEK: on yedi biçim TEK ağaçta, hepsi aynı anda görülür.

    Tek tek kapılar her biçimi yalnız KENDİ dosyasında ölçer. Bu test hepsini
    bir arada koyar: biçimler birbirini maskelerse (bir kural diğerinin
    bulgusunu yutarsa) burada yakalanır.
    """
    govde = "".join(kod for _ad, kod, _et, _n in BICIM_ORNEKLERI)
    bulgular = _sentetik_bulgular(tmp_path, govde)
    etiketler = {b.split(" ")[1] for b in bulgular}
    beklenen = {et for _ad, _kod, et, _n in BICIM_ORNEKLERI}
    assert etiketler == beklenen, (
        "Biçimler bir arada ölçülünce küme değişti.\n"
        f"  EKSİK: {sorted(beklenen - etiketler)}\n"
        f"  FAZLA: {sorted(etiketler - beklenen)}\n  " + "\n  ".join(sorted(bulgular))
    )
    beklenen_toplam = sum(n for _ad, _kod, _et, n in BICIM_ORNEKLERI)
    assert len(bulgular) == beklenen_toplam, sorted(bulgular)


def test_sinir_olcumu_SIRADAN_kullanimi_saymaz(tmp_path: Path) -> None:
    """Ölçüt aşırı geniş de olmamalı: `select(t.c.x)` kapıya ZATEN görünür.

    Türetilmiş ölçüt çağrının KONUMSAL argümanına ve özniteliğe inmediği için
    sıradan okuma biçimleri SIFIR bulgu vermeli. Bu kenar olmazsa dedektör her
    dosyayı kırmızıya boyar ve donmuş sıfır anlamını yitirir.
    """
    bulgular = _sentetik_bulgular(tmp_path, _KONTROL_GOVDESI)
    assert bulgular == [], (
        "Ölçüt SIRADAN kullanımı sayıyor; bu kadar geniş bir ağ donmuş sıfırı "
        "anlamsız kılar (her dosya kırmızı olurdu).\n  " + "\n  ".join(bulgular)
    )


def test_aciklama_alani_DEGER_sayilmaz(tmp_path: Path) -> None:
    """`t: audit_logs = None` — bağlanan `None`'dır, tablo yalnız AÇIKLAMADA.

    Bu, ölçülmüş bir YANLIŞ POZİTİFTİ: açıklama alanı değer sayılıyordu.
    """
    bulgular = _sentetik_bulgular(tmp_path, "t: audit_logs = None\n")
    assert bulgular == [], (
        "Tür açıklaması DEĞER sayıldı; `t: audit_logs = None` bir yeniden "
        "takma DEĞİLDİR — bağlanan şey None.\n  " + "\n  ".join(bulgular)
    )


def test_alternatif_anahtar_sozlugu_TURETILMIS_degismeze_bagli() -> None:
    """Sözlük serbest değildir: her girdi çözümleme yolunda okunmalı.

    ÖLÇÜLDÜ: bu bağ yokken `{"orders": "id"}` eklemek HİÇBİR testi kırmıyordu.
    """
    cozumleme = _kiraci_cozumleme_tablolari()
    assert cozumleme, "çözümleme yolu boş ölçüldü; türetme bozulmuş olabilir"
    for tablo, anahtar in KIRACI_ANAHTARI_ISTISNALARI.items():
        assert tablo in TENANT_TABLES, tablo
        assert anahtar != KIRACI_SUTUNU, (
            f"{tablo}: alternatif anahtar {KIRACI_SUTUNU} olamaz — anlamsız"
        )
        assert tablo in cozumleme, (
            f"{tablo}: alternatif kiracı anahtarı bildirilmiş ama tablo KİRACI "
            f"ÇÖZÜMLEME yolunda okunmuyor (ölçülen: {sorted(cozumleme)}). "
            "Dairesellik iddiası yoksa alternatif anahtarın gerekçesi de yoktur."
        )


def test_uydurma_alternatif_anahtar_SESSIZCE_gecemez() -> None:
    """Runtime lens'in probu KALICI test: `{"orders": "id"}` reddedilmeli."""
    cozumleme = _kiraci_cozumleme_tablolari()
    assert "orders" in TENANT_TABLES
    assert "orders" not in cozumleme, (
        "orders çözümleme yolunda okunuyor görünüyor; bu testin ayırt ediciliği kalktı"
    )


def test_alternatif_anahtar_sozlugu_CAPALI() -> None:
    """Sözlüğün içeriği donduruldu: büyümesi GÖRÜNÜR bir diff olmalı."""
    assert KIRACI_ANAHTARI_ISTISNALARI == {
        "user_company_memberships": "user_id",
    }, KIRACI_ANAHTARI_ISTISNALARI


# ---------------------------------------------------------------------------
# YANSIMA MEKANİZMASI — BİÇİM DIŞLAMASI DEĞİL, MEKANİZMA YASAĞI
# ---------------------------------------------------------------------------
#
# İnceleme (4986380382) ikinci deliği şöyle koydu: kapı, yansımayla kurulmuş
# nesneleri "app.* modülüne ait metadata/tablolar + kaynakta
# `Table("<literal>")`" biçimlerine bakarak DIŞLIYORDU. Bu, bir çalışma
# zamanı uygulama tablosunun yansımayla kurulamayacağının SEMANTİK KANITI
# değildir; incelenen biçimlerin dışında yansımalı bir `Table` kuran gerçek
# bir yazma yolu GÖRÜNMEZ olurdu.
#
# Cümlenin kendi sunduğu kapanış uygulandı: MEKANİZMAYI YASAKLA. Yansımanın
# hiç kurulamadığı bir depoda "yansımayla kurulan tablo görünmez olur"
# iddiası konusuz kalır — analiz etmeye gerek yok.
#
# KÖKEN TABANLI, BİÇİM TABANLI DEĞİL. `Table` adı bu depoda İKİ ayrı şeyi
# gösteriyor ve ölçüldü: `app/invoice_pdf.py` ile `app/routers/outputs.py`
# içindeki dokuz `Table(...)` çağrısı `reportlab.platypus.Table`tır, PDF
# çizer, veritabanıyla ilgisi yoktur. Ada bakan bir kapı bu dokuzunu yanlış
# pozitif üretirdi. Bu yüzden her modülde `Table`ın SQLAlchemy'den gelip
# gelmediği İMPORTTAN çözülüyor, `as` takma adları dahil.
YANSIMA_ANAHTARLARI = frozenset({"autoload", "autoload_with"})
# 0 -> 1 (20260905, KİRACI DIŞA AKTARIMI). Bu, kapının kendi öngördüğü
# "ayrı bir karar"dır ve sessizce alınmadı.
#
# TEK İSTİSNA: `app/routers/kiraci_disa_aktarim.py` içindeki
# `MetaData.reflect`. Gerekçe, bu kapının yasakladığı şeyin TAM TERSİ bir
# riski kapatıyor olmasıdır. Dışa aktarım 102 kiracı tablosunun HEPSİNİ
# yazmak zorunda; elle yazılmış bir tablo listesi, yeni bir göç 103'üncü
# tabloyu eklediğinde SESSİZCE eski kalır ve o tablonun satırları kiracıya
# teslim edilen dosyaya HİÇ girmezdi. Eksik veri teslim etmek, burada
# gürültülü bir hatadan kötüdür ve hiçbir statik kapı onu yakalamaz.
#
# KAPININ ASIL KORKUSU BURADA KARŞILANIYOR: kapı, "yansıma tablo adını
# çalışma zamanına taşır ve STATİK OLARAK görülemeyen bir kiracı yüzeyi
# açar" diyor. Bu doğru — ve bu yüzden o yüzey ÇALIŞMA ZAMANINDA
# kapatılıyor:
#   * `tests/test_kiraci_disa_aktarim.py::test_hicbir_dosyada_baska_firmanin_satiri_yok`
#     iki firmayı da veriyle tohumlar, üretilen zip'teki HER tablonun HER
#     satırını tek tek gezer ve `company_id`nin dışa aktarılan firmaya ait
#     olduğunu doğrular. Tek bir tabloda yüklem düşerse test o tablonun ADINI
#     söyleyerek kırılır. Statik bir kapının 102 tabloda veremeyeceği kanıt
#     budur.
#   * `test_yuz_iki_tablo_dosyasi_tam` yansımayla türetilen kümenin
#     `TENANT_TABLES` ile BİREBİR aynı olduğunu doğrular (102 = 102), yani
#     yansıma kapının bildiği evrenin dışına çıkamaz.
#
# SINIR: istisna TEK dosyaya ait. Sayı 1'de çapalı kaldığı için `app/` içinde
# ikinci bir yansıma yolu açılırsa bu kapı yine kırılır.
BEKLENEN_YANSIMA_SAYISI = 1


def _sqlalchemy_table_adlari(agac: ast.Module) -> set[str]:
    """Bu modülde SQLAlchemy'nin ``Table``ına bağlı YEREL adlar."""
    adlar: set[str] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.ImportFrom):
            kok = (dugum.module or "").split(".")[0]
            if kok != "sqlalchemy":
                continue
            for parca in dugum.names:
                if parca.name == "Table":
                    adlar.add(parca.asname or parca.name)
        elif isinstance(dugum, ast.Import):
            for parca in dugum.names:
                if parca.name.split(".")[0] == "sqlalchemy" and parca.asname is None:
                    adlar.add("sqlalchemy.Table")
    return adlar


def yansima_ornekleri(kok: Path = APP_DIR) -> list[str]:
    """`app/` içinde tabloyu YANSIMAYLA kuran her yapı."""
    bulgular: list[str] = []
    for yol in sorted(kok.rglob("*.py")):
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        except SyntaxError:
            continue
        yerel = _sqlalchemy_table_adlari(agac)
        goreli = yol.relative_to(BACKEND).as_posix()
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Call):
                continue
            islev = dugum.func
            ad = (
                islev.attr if isinstance(islev, ast.Attribute)
                else islev.id if isinstance(islev, ast.Name) else None
            )
            # (a) `metadata.reflect(...)` — toplu yansıma
            if ad == "reflect":
                bulgular.append(f"{goreli}:{dugum.lineno} .reflect() — toplu yansıma")
            # (b) `autoload` / `autoload_with` — tekil yansıma
            for kw in dugum.keywords:
                if kw.arg in YANSIMA_ANAHTARLARI:
                    bulgular.append(f"{goreli}:{dugum.lineno} {kw.arg}= — tekil yansıma")
            # (c) SQLAlchemy `Table(<literal olmayan>)` — adı okunamayan tablo.
            #     reportlab'ın `Table`ı KÖKENDEN elenir; ada bakılmaz.
            if ad in yerel or (isinstance(islev, ast.Attribute) and "sqlalchemy.Table" in yerel):
                ilk = dugum.args[0] if dugum.args else None
                if not (isinstance(ilk, ast.Constant) and isinstance(ilk.value, str)):
                    bulgular.append(
                        f"{goreli}:{dugum.lineno} Table(<literal değil>) — adı statik okunamaz"
                    )
    return bulgular


def test_yansimayla_tablo_kurulamaz() -> None:
    """MEKANİZMA YASAĞI: `app/` içinde yansımayla tablo kurulamaz.

    Bu, "yansımalı nesneleri dışla" biçim kuralının YERİNE geçer. Biçim
    dışlaması, incelenen biçimlerin dışındaki bir yolu görmezdi; mekanizma
    yasağı böyle bir yolun VAR OLMASINI engelliyor.

    ÖLÇÜLDÜ: bugün `app/` içinde sıfır tane var. Sıfır en güçlü sonuçtur ama
    yazılmamış sıfır geri büyür — bu yüzden çapalanıyor ve büyümesi ayrı bir
    karar oluyor. `YENIDEN_TAKMA_AD_SAYISI` ile aynı şekil; o da bu depoda
    çalışan bir MALİYET olarak kabul edildi.
    """
    bulgular = yansima_ornekleri()
    assert len(bulgular) == BEKLENEN_YANSIMA_SAYISI, (
        f"yansımayla kurulan tablo sayısı {BEKLENEN_YANSIMA_SAYISI} değil "
        f"({len(bulgular)}). Yansıma, tablo adını çalışma zamanına taşır ve "
        "bu kapının statik olarak göremeyeceği bir kiracı yüzeyi açar.\n  "
        + "\n  ".join(bulgular)
    )


def test_yansima_kapisi_reportlab_Table_ile_KARISMIYOR() -> None:
    """KARŞI YÖN: `reportlab.platypus.Table` yanlış pozitif ÜRETMEMELİ.

    Ada bakan bir kapı `app/invoice_pdf.py` ve `app/routers/outputs.py`
    içindeki dokuz PDF tablosunu ihlal sayardı. Köken tabanlı çözüm bunu
    ayırt ediyor. Her şeyi reddeden bir kapı da 'geçmiş' görünürdü; bu test
    o yönü ölçüyor.
    """
    for goreli in ("app/invoice_pdf.py", "app/routers/outputs.py"):
        yol = BACKEND / goreli
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        assert not _sqlalchemy_table_adlari(agac), (
            f"{goreli}: SQLAlchemy `Table` import ediyor görünüyor; bu testin "
            "ayırt ediciliği kalkar"
        )
        sayi = sum(
            1 for d in ast.walk(agac)
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Name) and d.func.id == "Table"
        )
        assert sayi > 0, f"{goreli}: reportlab Table çağrısı ölçülemedi ({sayi})"


# ---------------------------------------------------------------------------
# KİMLİK — "KARŞILAŞTIRMA VAR" DEĞİL, "DOĞRU KİMLİK BAĞLI"
# ---------------------------------------------------------------------------
#
# İnceleme (4986380382) üçüncü deliği şöyle koydu: `_alternatif_anahtar_bagli`
# SABİT OLMAYAN her sağ tarafı yeterli sayıyordu.
# `memberships.c.user_id == some_user_variable` geçiyordu, oysa kural
# `some_user_variable`ın KİMLİK taşıdığını kurmuyordu. RLS olmayan bir depoda
# bu doğrudan bir kiracı sınırı zayıflığıdır.
#
# YÜKLEM — uygulamadan ÖNCE yazıldı, satırların beklenen sonucu bundan türedi:
#
#   Bir değer KİMLİK TAŞIR  <=>  kapsayan fonksiyondaki yerel bağlar
#   izlendiğinde şuna çözülür:
#     (a) kimlik doğrulanmış asilin bir alan okuması — `request.state.user`
#         zincirinden ya da o zincire ATANAN yerel addan gelen `["id"]`/`.id`;
#         VEYA
#     (b) kapsayan fonksiyonun bir parametresi ki `app/` içindeki HER çağrı
#         yerinde o konuma (a)'yı sağlayan bir değer geçiliyor.
#
# (b) çağrılar arası ve pahalıdır; `app/` küçük olduğu için ölçülebiliyor.
# HİÇ çağrı yeri bulunamazsa FAIL-CLOSED: kimlik taşımıyor sayılır.
#
# NAİF YÜKLEM YANLIŞ OLURDU. "Sağ taraf HER ZAMAN kimlik taşımalı" diyen bir
# kural meşru bir yolu kırmızıya çevirirdi ve ölçümde bu çıktı.
KIMLIK_ALANI = "user"
KIMLIK_ANAHTARLARI = frozenset({"id"})
_KIMLIK_SARMALAYICILARI = frozenset({"int", "str", "UUID"})

# KALICI BİLDİRİLMİŞ İSTİSNA — #47.
#
# `user_status_tenant_guard.py` bir sızıntı DEĞİL, sızıntıya karşı korumadır:
# "Prevent cross-tenant deactivation of shared users (#47)". Kapsamsız okuma
# VERİ DÖNDÜRMEZ; "bu hesap paylaşımlı mı" sorusunu yanıtlar ve o soru
# şirketler arasına bakmadan yanıtlanamaz. Çağıranın şirketi üyelikler
# arasında değilse fonksiyon sessizce döner, yani kiracıya özgü 404 davranışı
# korunur. Dışarı çıkan tek şey bir BİT — "bu hesap başka yerde de bağlı" — ve
# yalnız o kullanıcıyı kendi şirketinde zaten yöneten bir yöneticiye.
#
# Altındaki kök neden (`is_active`in kullanıcı düzeyinde küresel olması) ayrı
# bir ürün dilimidir ve bu kapının borcu değildir.
KIMLIK_ISTISNALARI: dict[tuple[str, str, str], str] = {
    # --- KİMLİK DOĞRULANMADAN ÖNCEKİ HESAP AKIŞLARI ------------------------
    # Üçünde de asil TANIMI GEREĞİ yoktur: kimlik burada KULLANILMIYOR,
    # KURULUYOR. Anahtar, çağıranın verdiği e-postadır ve okunan company_id
    # ÇAĞIRANA DÖNMEZ — doğrulama/sıfırlama bağlantısı e-posta KUTUSUNA
    # gider, yani yalnız adresin sahibine. Üçü de IP oranıyla sınırlı.
    ("backend/app/routers/auth.py", "register",
     "f28c8f3f1dd5d3ea667b6a0227968dfa1b30a669270282718f67e2f2b347a65b"):
        "kayıt — asil henüz yok; 'bu e-posta zaten kayıtlı mı' sorusu, sonuç "
        "çağırana veri olarak dönmez",
    ("backend/app/routers/auth.py", "resend_verification",
     "faf2b78d986e40a0c541764a482b4747ca4ca0e8426b71ae9cdd9f1cd8f0ae21"):
        "doğrulama tekrarı — asil henüz yok; company_id yalnız e-posta "
        "kutusuna giden bağlantıyı kurmak için okunur",
    ("backend/app/routers/auth.py", "forgot_password",
     "faf2b78d986e40a0c541764a482b4747ca4ca0e8426b71ae9cdd9f1cd8f0ae21"):
        "parola sıfırlama — asil henüz yok; company_id yalnız e-posta "
        "kutusuna giden bağlantıyı kurmak için okunur",

    # --- #47: SIZINTI DEĞİL, SIZINTIYA KARŞI KORUMA ------------------------
    # "Prevent cross-tenant deactivation of shared users (#47)". Kapsamsız
    # okuma VERİ DÖNDÜRMEZ; "bu hesap paylaşımlı mı" sorusunu yanıtlar ve o
    # soru şirketler arasına bakmadan yanıtlanamaz. Çağıranın şirketi
    # üyelikler arasında değilse fonksiyon SESSİZCE döner, yani kiracıya özgü
    # 404 davranışı korunur. Dışarı çıkan tek şey bir BİT — "bu hesap başka
    # yerde de bağlı" — ve yalnız o kullanıcıyı kendi şirketinde ZATEN
    # yöneten bir yöneticiye. Altındaki kök neden (`is_active`in kullanıcı
    # düzeyinde küresel olması) AYRI BİR ÜRÜN DİLİMİDİR, bu kapının borcu
    # değildir.
    ("backend/app/user_status_tenant_guard.py",
     "prevent_cross_tenant_user_deactivation",
     "38a52a419229a3dbb74a84facd7ad35755719821e430900047785baf7b884f60"):
        "#47 — kiracılar arası pasifleştirmeyi ENGELLEYEN koruma; veri değil "
        "tek bir 'paylaşımlı mı' biti",
}

# PARMAK İZİ KİMLİĞE GİRER: fonksiyon sonradan düzenlenirse iz değişir ve
# lisans KENDİLİĞİNDEN düşer. Satır numarası girmez — dosyayı kaydırmak bir
# istisnayı düşürmemeli. `Ihlal.kimlik` ile birebir aynı sözleşme.


def _kimlik_kaynagi_adlari(agac: ast.Module) -> set[str]:
    """Bu modülde kimlik doğrulanmış asile bağlı YEREL adlar.

    İki yön de sayılır ve ikisi de aynı asili gösterir:
      `request.state.user = u`  ->  {"u"}      (asil oraya YAZILIYOR)
      `u = request.state.user`  ->  {"u"}      (asil oradan OKUNUYOR)
    """
    adlar: set[str] = set()

    def _asil_zinciri(dugum: ast.AST) -> bool:
        return (
            isinstance(dugum, ast.Attribute)
            and dugum.attr == KIMLIK_ALANI
            and isinstance(dugum.value, ast.Attribute)
            and dugum.value.attr == "state"
        )

    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Assign):
            continue
        for hedef in dugum.targets:
            if _asil_zinciri(hedef) and isinstance(dugum.value, ast.Name):
                adlar.add(dugum.value.id)          # request.state.user = u
            if isinstance(hedef, ast.Name) and _asil_zinciri(dugum.value):
                adlar.add(hedef.id)                # u = request.state.user
    return adlar


def _asil_okumasi(dugum: ast.AST, asil_adlari: set[str]) -> bool:
    """(a): kimlik doğrulanmış asilin `["id"]` / `.id` okuması mı."""
    # `int(...)` gibi zararsız sarmalayıcılar soyulur — tür dönüşümü kimliği
    # değiştirmez. Bilinmeyen bir çağrı DEĞİŞTİREBİLİR, o yüzden soyulmaz.
    while (
        isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id in _KIMLIK_SARMALAYICILARI
        and dugum.args
    ):
        dugum = dugum.args[0]

    if isinstance(dugum, ast.Subscript):
        anahtar = dugum.slice
        if not (isinstance(anahtar, ast.Constant) and anahtar.value in KIMLIK_ANAHTARLARI):
            return False
        taban = dugum.value
    elif isinstance(dugum, ast.Attribute) and dugum.attr in KIMLIK_ANAHTARLARI:
        taban = dugum.value
    else:
        return False

    if isinstance(taban, ast.Name):
        return taban.id in asil_adlari
    return (
        isinstance(taban, ast.Attribute)
        and taban.attr == KIMLIK_ALANI
        and isinstance(taban.value, ast.Attribute)
        and taban.value.attr == "state"
    )


def _cagri_yerleri(hedef_ad: str, kok: Path = APP_DIR) -> list[tuple[str, ast.Call]]:
    """`app/` içinde `hedef_ad(...)` çağrılarının tamamı."""
    bulunan: list[tuple[str, ast.Call]] = []
    for yol in sorted(kok.rglob("*.py")):
        try:
            agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        except SyntaxError:
            continue
        for dugum in ast.walk(agac):
            if isinstance(dugum, ast.Call) and _tablo_adi(dugum.func) == hedef_ad:
                bulunan.append((yol.relative_to(BACKEND).as_posix(), dugum))
    return bulunan


def _parametre_kimlik_tasiyor(
    fonksiyon: ast.AST, param_adi: str, derinlik: int = 2
) -> bool:
    """(b): parametreye HER çağrı yerinde kimlik geçiliyor mu.

    FAIL-CLOSED üç yerde:
      * parametre konumu bulunamazsa   -> hayır
      * hiç çağrı yeri yoksa           -> hayır (kanıtlanmamış = kanıtsız)
      * tek bir çağrı yeri bile kimlik geçirmiyorsa -> hayır
    """
    if not isinstance(fonksiyon, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    konumlar = [a.arg for a in fonksiyon.args.args]
    if param_adi not in konumlar:
        return False
    indeks = konumlar.index(param_adi)

    yerler = _cagri_yerleri(fonksiyon.name)
    if not yerler:
        return False

    for goreli, cagri in yerler:
        yol = BACKEND / goreli
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        asil = _kimlik_kaynagi_adlari(agac)
        arg = None
        if len(cagri.args) > indeks:
            arg = cagri.args[indeks]
        else:
            for kw in cagri.keywords:
                if kw.arg == param_adi:
                    arg = kw.value
                    break
        if arg is None:
            return False
        if _asil_okumasi(arg, asil):
            continue
        # ASİL BAĞIMLILIKLA GELİYOR OLABİLİR: `_session_payload(db, user)`
        # içinde `user` bir PARAMETREDİR, `request.state.user` okuması değil.
        # Bir kat yukarı çıkılır. Derinlik SINIRLI ve tükenirse FAIL-CLOSED.
        cozuldu = False
        if derinlik > 0 and isinstance(_asil_soy(arg), ast.Name):
            ad = _asil_soy(arg).id
            for ust in ast.walk(agac):
                if not isinstance(ust, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not any(ic is cagri for ic in ast.walk(ust)):
                    continue
                if _parametre_kimlik_tasiyor(ust, ad, derinlik - 1):
                    cozuldu = True
                break
        if not cozuldu:
            return False
    return True


def _asil_soy(dugum: ast.AST) -> ast.AST:
    """`int(x)` gibi zararsız sarmalayıcıları soyar."""
    while (
        isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id in _KIMLIK_SARMALAYICILARI
        and dugum.args
    ):
        dugum = dugum.args[0]
    return dugum


# --- DEKLARE EDİLMİŞ SINIRLAR — MALİYET Mİ DELİK Mİ, GEREKÇESİYLE ----------
#
# AYRIM: kapının ÇALIŞMAYA DEVAM ETTİĞİ kısıt MALİYETTİR ve deklare edilir.
# Kapıyı YEŞİL KALARAK VAKUMA DÜŞÜREN yol DELİKTİR ve kapatılır. Etiket tek
# başına sınıflandırma değildir; her satırda gerekçe yazılı.
#
# 1. MALİYET — KİMLİK DERİNLİĞİ İKİ KAT. `_parametre_kimlik_tasiyor`
#    çağrı yerlerini iki kat yukarı izler (`resolve_company` <- `main`,
#    `user_companies` <- `_session_payload` <- uç). Üçüncü kata çıkan bir
#    zincir KANITLANAMAZ ve o durumda FAIL-CLOSED çalışır: kimlik taşımıyor
#    sayılır, kapı KIRMIZI verir. Yani derinlik yetmediğinde kapı susmuyor,
#    bağırıyor — bu yüzden maliyet, delik değil.
#
# 2. MALİYET — İSTİSNALAR PARMAK İZİNE BAĞLI, DAVRANIŞA DEĞİL. Dört
#    istisnanın gerekçesi insan tarafından okunmuş bir davranış iddiasıdır
#    ("sonuç çağırana dönmez"). Kapı o iddiayı DOĞRULAMAZ; yalnız ifade
#    değişirse lisansın DÜŞMESİNİ garanti eder. Kapı çalışmaya devam eder,
#    kapsamı bellidir.
#
# 3. MALİYET — YANSIMA YASAĞI `app/` İLE SINIRLI. `backend/` altındaki başka
#    bir paket yansımayla tablo kurabilir ve bu kapı görmez. Kapı kendi
#    yüzeyinde çalışır; iddiası da o yüzeyle sınırlı yazılmıştır.
#
# 4. MALİYET — ÇÖZÜCÜ TÜRETMESİ TEK ÇAPAYA BAĞLI. `request.state.company_id`
#    dışında bir mekanizmayla şirket bağlamı kurulursa türetme onu görmez.
#    Ama o mekanizma bu uygulamada YOKTUR ve eklenmesi `company_id(request)`
#    yardımcısını da baypas etmeyi gerektirir; ayrıca türetme boşalırsa
#    `test_alternatif_anahtar_sozlugu_TURETILMIS_degismeze_bagli` KIRMIZI
#    verir (ölçüldü: mutasyon satırı 1). Fail-closed olduğu için maliyet.
#
# 5. MALİYET — YENİDEN TAKMA AD SIFIRI. İnceleme bunu zaten "bildirilen
#    üretici kümesi için çalışan bir MALİYET" olarak kabul etti; kör bir
#    dondurulmuş sayı değil, yeni bir üretici çağrısı sayıyı bozar.
#
# DELİK: yok. Bu turda üç delik kapatıldı — türetmenin ad listesi olması
# (özellikten türetmeye çevrildi), yansıma dışlamasının biçim tabanlı olması
# (mekanizma yasağına çevrildi), ve karşılaştırmanın varlığının kimlik
# sayılması (icra edilebilir kimlik yüklemine çevrildi).
