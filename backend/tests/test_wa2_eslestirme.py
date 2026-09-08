"""WHATSAPP EŞLEŞTİRME: numara → ERP kimliği defteri (WA2).

Konu: göç `20260910_0079`, `app/whatsapp/schema.py`nin ÜÇ yeni KİRACI
tablosu, `app/whatsapp/eslestirme.py`, `app/whatsapp/baglam.py`,
`app/routers/whatsapp.py`nin DÖRT yeni ucu ve `app/auth.py`nin
`/api/whatsapp/` önek kuralı.

ÖLÇÜLEN EKSİK: WA1 Meta webhook'unun yazdığı PLATFORM kuyruğunu açtı ve
başlığında "numara hiçbir kullanıcıya BAĞLANMAZ" diyordu. Yani
`whatsapp_inbound.sender_phone`daki rakam dizisini bir firmaya ve bir
kullanıcıya çeviren hiçbir şey yoktu. Bu dilim o çeviriyi getiriyor — ve
YALNIZ onu: işçi, Meta cevabı ve giden mesaj bu turda YOKTUR.

Şekil deponun kalıbıyla BİREBİR: STATİK KAPILAR + alt süreçte göç turu +
GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * Kod özetini `hmac.compare_digest` yerine `==` ile kıyaslamak
                                    -> SABİT SÜRE kapısı KIRMIZI (davranış
                                       testlerinin HİÇBİRİ bu mutantı
                                       öldürmez; kaybolan şey zamanlamadır)
  * CAS'ten `status == PENDING` yüklemini düşürmek
                                    -> CAS kapısı KIRMIZI. YALNIZ o kapı:
                                       PG ikizindeki yarış adımı bu mutantla
                                       YEŞİL KALIR (ölçüldü) çünkü satır
                                       kilidi ve okuma-sonrası durum
                                       denetimi ayrı ayrı yetiyor. Bu kapı
                                       tam olarak o yüzden AST'dedir.
  * Hız sınırını kapatmak (`deneme_say` çağrısını kaldırmak ya da
    sınır karşılaştırmasını düşürmek)
                                    -> SINIR SIRASI kapısı ve KİLİTLİ
                                       PENCERE adımı KIRMIZI
  * `kimlik_secimi`den bağlam okumasını kaldırmak
                                    -> BAĞLAM kapısı ve FİRMA SEÇ adımı
                                       KIRMIZI (çok firmalı kullanıcı
                                       sonsuza dek seçim ekranında kalır)
  * Kısmi tekili `(company_id, phone)` yerine yalnız `(phone)` yapmak
                                    -> BAŞKA FİRMA adımı KIRMIZI
  * Kısmi tekilin `WHERE`ini düşürüp `UNIQUE(company_id, phone)` yapmak
                                    -> KAPATIP YENİDEN BAĞLAMA adımı KIRMIZI
  * Kısmi tekili büsbütün düşürmek
                                    -> AYNI FİRMA İKİNCİ AKTİF adımı KIRMIZI
  * `kod_uret`i düz kodu da yazacak biçimde değiştirmek
                                    -> DÜZ KOD kapısı KIRMIZI
  * Uçlardan `company_id` yüklemini düşürmek
                                    -> KİRACI YALITIMI adımları KIRMIZI
  * Dört ucu `PUBLIC_API`ye eklemek
                                    -> MUAFİYET kapısı KIRMIZI
  * Üç tablodan birinden `company_id`yi düşürmek
                                    -> KİRACI ENVANTERİ kapısı KIRMIZI
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260910_0079_whatsapp_eslestirme.py"
SEMA = BACKEND / "app" / "whatsapp" / "schema.py"
SERVIS = BACKEND / "app" / "whatsapp" / "eslestirme.py"
BAGLAM = BACKEND / "app" / "whatsapp" / "baglam.py"
UC = BACKEND / "app" / "routers" / "whatsapp.py"
YETKI = BACKEND / "app" / "auth.py"

BAGLANTI_TABLO = "whatsapp_links"
KOD_TABLO = "whatsapp_pairing_codes"
BAGLAM_TABLO = "whatsapp_context"

#: Kanonik numara (`normalize_phone` çıktısı biçiminde).
NUMARA = "905405995959"
#: AYNI numaranın insan yazımı: `0540 599 59 59` → `905405995959`.
NUMARA_INSAN = "0540 599 59 59"
IKINCI_NUMARA = "905331112233"

# UYGULAMA İÇE AKTARILMADAN ÖNCE ORTAM KURULUR. `app.config.Settings` modül
# düzeyinde TEK KOPYADIR: içe aktarma bir kez olduktan sonra `DATABASE_URL`i
# değiştirmenin hiçbir etkisi kalmaz. Kanonik koşucu (`run_isolated_tests.py`)
# her dosyayı KENDİ sürecinde koşturuyor, bu yüzden burada modül düzeyinde
# yazmak güvenlidir ve alt sürece gerek bırakmaz.
_CALISMA = Path(tempfile.mkdtemp(prefix="wa2-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "wa2.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"

sys.path.insert(0, str(BACKEND))


def _fn(kaynak: str, ad: str) -> ast.FunctionDef:
    """Kaynaktaki `ad` fonksiyonunun AST düğümü (iç içe olanlar dâhil)."""
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            dugum.name == ad
        ):
            return dugum
    raise AssertionError(f"fonksiyon bulunamadı: {ad}")


# --------------------------------------------------------------- statik ---

def test_goc_UC_KIRACI_TABLOSU_aciyor_ve_company_id_TASIYOR() -> None:
    """Üç tablo da `company_id` TAŞIYOR — WA1'in kararının TERSİ, bilinçli.

    WA1'in iki tablosu kiracı sütunu taşımıyordu çünkü webhook'a gelen mesaj
    henüz hiçbir firmaya ait DEĞİLDİR. Bu üç tablo ise tam olarak "bu numara
    hangi firmanın kullanıcısı" sorusunun CEVABIDIR; sütun düşseydi ya
    sorunun cevabı olmazdı ya da cevap küresel olurdu.

    MUTASYON: üç tablodan birinden `company_id`yi düşürmek bunu ve KİRACI
    ENVANTERİ kapısını KIRMIZI yapar.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'BAGLANTI = "whatsapp_links"' in kaynak
    assert 'KOD = "whatsapp_pairing_codes"' in kaynak
    assert 'BAGLAM = "whatsapp_context"' in kaynak
    # Üç `create_table` çağrısının ÜÇÜNDE de kiracı sütunu var (AST'den:
    # bir yorum satırındaki "company_id" kapıyı yanıltamasın).
    agac = ast.parse(kaynak)
    kurulan = [
        d
        for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "create_table"
    ]
    assert len(kurulan) == 3, kurulan
    for cagri in kurulan:
        parca = ast.get_source_segment(kaynak, cagri) or ""
        assert '"company_id"' in parca
        # 0062'nin kuralı: bileşik yabancı anahtar HEDEFİ olabilmesi için.
        assert 'sa.UniqueConstraint("company_id", "id"' in parca

    # BİLEŞİK yabancı anahtar: bir firmanın kodu BAŞKA firmanın bağlantısını
    # tüketmiş görünemez.
    assert '["company_id", "consumed_link_id"]' in kaynak


def test_AKTIF_NUMARA_TEKILI_KISMI_ve_DIYALEKTE_GORE_YAZILI() -> None:
    """Tekil `(company_id, phone) WHERE is_active` ve YÜKLEM İKİ AYRI METİN.

    İKİ AYRI İDDİA ve ikisi de gerçek bir kusuru kapatıyor:

    1. Anahtar `(company_id, phone)` — yalnız `(phone)` DEĞİL. Kaynak
       (`nazgul_website`) küresel yazıyordu çünkü orada firma SEÇİCİ yoktu;
       bu depoda seçici VAR (`baglam.py`), bu yüzden aynı numara başka bir
       firmada aktif olabilir.
    2. Yüklem DİYALEKTE GÖRE ayrı: SQLite'ın boolean'ı bir TAMSAYIDIR
       (`is_active = 1`), PostgreSQL'inki gerçek boolean (`is_active =
       true`). Tek bir metin yazmak, indeksin BİR DİYALEKTTE HİÇ
       KURULMAMASINA yol açardı — ve kurulmamış bir tekil HİÇBİR ŞEYİ
       reddetmez.

    MUTASYON: `_aktif_yuklem`i tek metin döndürmeye indirgemek bunu KIRMIZI
    yapar; anahtardan `company_id`yi düşürmek BAŞKA FİRMA adımını, `WHERE`i
    düşürmek KAPATIP YENİDEN BAĞLAMA adımını düşürür.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'BAGLANTI_AKTIF_TEKIL = "uq_whatsapp_links_aktif_numara"' in kaynak
    assert '["company_id", "phone"]' in kaynak
    assert 'sqlite_where=_aktif_yuklem("sqlite")' in kaynak
    assert 'postgresql_where=_aktif_yuklem("postgresql")' in kaynak


def test_AKTIF_NUMARA_TEKILI_YUKLEMI_IKI_DIYALEKTTE_FARKLI() -> None:
    """`_aktif_yuklem` iki diyalekt için FARKLI metin üretiyor — ölçüldü."""
    modul = _goc_modulu()
    sqlite_yuklem = str(modul._aktif_yuklem("sqlite"))
    pg_yuklem = str(modul._aktif_yuklem("postgresql"))
    assert sqlite_yuklem == "is_active = 1", sqlite_yuklem
    assert pg_yuklem == "is_active = true", pg_yuklem
    assert sqlite_yuklem != pg_yuklem


def _goc_modulu():
    """Göç dosyasını `alembic` olmadan, MODÜL olarak yükler."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("wa2_goc", GOC)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return modul


def test_YEDI_CHECK_gocte_ve_Core_tanimda_BIREBIR() -> None:
    """Durum × zaman damgası matrisi İKİ YERDE de AYNI.

    Alembic ile kurulan şema ile `metadata.create_all()` ile kurulan şema
    güvenlik anlamı bakımından AYRIŞMAMALIDIR; ayrışsaydı testler gerçekte
    üretimde tutan bir kısıtı hiç ölçmezdi.

    MUTASYON: yedi CHECK'ten birini bir yerden kaldırmak bunu KIRMIZI yapar.
    """
    goc = GOC.read_text(encoding="utf-8")
    sema = SEMA.read_text(encoding="utf-8")
    adlar = (
        "ck_wpc_status",
        "ck_wpc_attempt_count",
        "ck_wpc_max_attempts",
        "ck_wpc_pending_temiz",
        "ck_wpc_consumed_alanlari",
        "ck_wpc_cancelled_alani",
        "ck_wpc_expired_temiz",
    )
    for ad in adlar:
        assert ad in goc, ad
        assert ad in sema, ad

    # Durum kümesi de İKİ YERDE aynı ve KAPALI.
    assert (
        'KOD_DURUMLARI = ("PENDING", "CONSUMED", "CANCELLED", "EXPIRED")' in goc
    )
    from app.whatsapp.schema import PAIRING_STATUSES

    assert PAIRING_STATUSES == frozenset(
        {"PENDING", "CONSUMED", "CANCELLED", "EXPIRED"}
    )
    assert (
        "status IN ('PENDING','CONSUMED','CANCELLED','EXPIRED')" in sema
    )


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """Üç tablo da açılış DDL'inde BİLDİRİLMİYOR ve `create_all` EDİLMİYOR.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu): `app/tenancy.py`
    `companies`i `Table()` olarak bildiriyor ve uygulamanın AÇILIŞI o tabloyu
    alembic'ten ÖNCE kurabiliyor; göç onu VAR bulup tek `if` dalını ATLAR ve
    YEŞİL biter — kısıtlar HİÇ kurulmaz.

    MUTASYON: `whatsapp.schema.metadata`yı `create_all` etmek bunu KIRMIZI
    yapar.
    """
    acilis = ""
    for modul in (
        "tenancy.py", "core_schema.py", "auth.py", "inventory.py",
        "finance_engine.py", "workflow.py",
    ):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    acilis += (BACKEND / "app" / "notifications" / "schema.py").read_text(
        encoding="utf-8"
    )
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))
    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    for tablo in (BAGLANTI_TABLO, KOD_TABLO, BAGLAM_TABLO):
        assert tablo not in bildirilen, tablo

    # Ve `app/` altında bu MetaData'yı kuran hiçbir çağrı YOK.
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        metin = yol.read_text(encoding="utf-8")
        if "create_all" not in metin:
            continue
        for dugum in ast.walk(ast.parse(metin)):
            if (
                isinstance(dugum, ast.Call)
                and getattr(dugum.func, "attr", None) == "create_all"
            ):
                parca = ast.get_source_segment(metin, dugum) or ""
                assert "whatsapp" not in parca, (yol.name, parca)


def test_KIRACI_ENVANTERI_UCU_DE_ICERIYOR() -> None:
    """Üç tablo da `TENANT_TABLES`ta — sayı 116'dan 119'a çıktı.

    Envanter elle yazılmış bir muafiyet listesi DEĞİL; göç edilmiş şemadan
    `company_id` sütunu taşıyan tablolar taranarak türetiliyor. Yani üyeliğin
    kendisi SÜTUNUN VARLIĞIDIR.

    WA1'in iki tablosu hâlâ DIŞARIDA ve bu, iddianın zayıflaması değil
    KANITIDIR: elle yazılmış bir liste olsaydı üç tablo da sessizce dışarıda
    kalabilirdi.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    for tablo in (BAGLANTI_TABLO, KOD_TABLO, BAGLAM_TABLO):
        assert tablo in TENANT_TABLES, tablo
    assert "whatsapp_inbound" not in TENANT_TABLES
    assert "whatsapp_pairing_attempts" not in TENANT_TABLES
    assert len(TENANT_TABLES) == 119, len(TENANT_TABLES)


def test_MUAFIYET_DORT_UCU_KAPSAMIYOR() -> None:
    """Dört uç `PUBLIC_API`de DEĞİL — muafiyet TAM YOL, ÖNEK değil.

    WA1'in `test_MUAFIYET_TAM_YOL_ve_ONEK_DEGIL` kapısı tam olarak bu
    senaryoyu koruyordu: "bu router'a yarın eklenecek bir yönetim ucu
    SESSİZCE oturumsuz açılırdı". WA2 o korumanın işe yaradığı ilk turdur ve
    bu kapı onu AYNI DOSYADAN, YENİ UÇLARLA yeniden ölçüyor.

    MUTASYON: `PUBLIC_API`ye `/api/whatsapp` ya da `/api/whatsapp/` ÖNEKİ
    yazmak bunu KIRMIZI yapar.
    """
    from app.main import PUBLIC_API

    assert "/api/whatsapp/webhook" in PUBLIC_API
    for yol in (
        "/api/whatsapp/pairing-codes",
        "/api/whatsapp/links",
        "/api/whatsapp",
        "/api/whatsapp/",
    ):
        assert yol not in PUBLIC_API, yol


def test_YETKI_ONEK_KURALI_DORT_UCU_users_a_BAGLIYOR() -> None:
    """`/api/whatsapp/` öneki `users`; webhook'lar ETKİLENMİYOR.

    Kural YAZILMASAYDI aynı uç ailesi metoda göre İKİ FARKLI kapıdan geçerdi
    — ÖLÇÜLDÜ: GET `read`e (genel SAFE_METHODS kuralı), POST/DELETE
    `__admin_only__`e (deny-by-default nöbetçisi) düşüyordu. Yani okuma
    yetkisi olan HER rol firmanın bota bağlı numaralarını görürdü.

    MUTASYON: satırı silmek bunu KIRMIZI yapar.
    """
    from app.auth import required_permission

    for yontem, yol in (
        ("POST", "/api/whatsapp/pairing-codes"),
        ("DELETE", "/api/whatsapp/pairing-codes/{kod_id}"),
        ("GET", "/api/whatsapp/links"),
        ("DELETE", "/api/whatsapp/links/{baglanti_id}"),
    ):
        assert required_permission(yontem, yol) == "users", (yontem, yol)

    # Kural ÖNEK, TAM EŞLEŞME DEĞİL: `{id}` taşıyan yollar da kapsanıyor
    # (yukarıdaki iki DELETE bunun tanığıdır).
    assert 'if path.startswith("/api/whatsapp/"):' in YETKI.read_text(
        encoding="utf-8"
    )


def _sabit_sureli_kapi(yol: Path, fn_adi: str, operandlar: set[str]) -> None:
    """`fn_adi` içindeki gizli karşılaştırma `==`/`!=` ile YAPILMIYOR.

    Kalıp `tests/test_wa1_ingress.py::_sabit_sureli_kapi` ile BİREBİR AYNI ve
    gerekçesi ölçülmüş bir KÖR NOKTADIR: `hmac.compare_digest`i `==` ile
    değiştiren bir mutant DAVRANIŞI DEĞİŞTİRMEZ — doğru kod yine bağlar,
    yanlış kod yine reddedilir — yani hiçbir davranış testi onu öldüremez.
    Kaybolan şey ZAMANLAMADIR: `==` ilk farklı baytta döner.

    İki assert AYRI kusuru kapatıyor: birincisi çağrının VARLIĞINI, ikincisi
    KARARIN o çağrıdan geldiğini. İkincisi olmasaydı `compare_digest`i
    çağırıp sonucunu ATAN ve kararı `==` ile veren bir mutant geçerdi.
    """
    kaynak = yol.read_text(encoding="utf-8")
    fn = _fn(kaynak, fn_adi)
    govde = ast.get_source_segment(kaynak, fn) or ""
    assert "hmac.compare_digest(" in govde, (yol.name, fn_adi)

    def sql_sutunu(dugum: ast.expr) -> bool:
        """`<tablo>.c.<sutun>` biçimi mi? (SQLAlchemy Core kolon ifadesi)

        Bu ayrım ZORUNLU: `code_digest == :ozet` bir SUNUCU karşılaştırmasıdır
        ve Python'da hiçbir bayt kıyaslanmaz — üstelik `==` orada bir SQL
        yüklemi ÜRETİR, `==` operatörünü UYGULAMAZ. Onu bu kapıya takmak,
        sorgunun kendisini yazılamaz kılardı ve gerçek kusuru (satır
        elimize geldikten SONRA yapılan Python kıyası) gizlerdi.
        """
        return (
            isinstance(dugum, ast.Attribute)
            and isinstance(dugum.value, ast.Attribute)
            and dugum.value.attr == "c"
        )

    def etiket(dugum: ast.expr) -> str | None:
        if isinstance(dugum, ast.Name):
            return dugum.id
        if isinstance(dugum, ast.Subscript) and isinstance(
            dugum.slice, ast.Constant
        ):
            return str(dugum.slice.value)
        return None

    for dugum in ast.walk(fn):
        if not isinstance(dugum, ast.Compare):
            continue
        if any(sql_sutunu(t) for t in [dugum.left, *dugum.comparators]):
            continue
        for op in dugum.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            taraflar = {
                etiket(t) for t in [dugum.left, *dugum.comparators]
            } - {None}
            assert not (taraflar & operandlar), (
                f"{yol.name}::{fn_adi}: gizli değer `==`/`!=` ile kıyaslanmış "
                f"({sorted(taraflar & operandlar)}); sabit süreli karşılaştırma "
                "yerine erken dönen bir kıyas zamanlama sızdırır"
            )


def test_KOD_OZETI_SABIT_SURELI_KARSILASTIRILIYOR() -> None:
    """`kod_kullan` özeti `==` ile DEĞİL `compare_digest` ile doğruluyor.

    WA1'in imza kapısıyla AYNI kalıpta ve AYNI gerekçeyle. Buradaki gizli
    değer, kodun SHA-256 özetidir; onu ele geçiren biri bir kullanıcının
    WhatsApp numarasını o firmanın hesabına bağlayabilir.

    `satir["status"] != schema.PAIRING_PENDING` KARŞILAŞTIRMASI bu kapıya
    TAKILMAZ ve takılmamalı: operandı gizli DEĞİL, kapalı bir durum kümesinin
    üyesidir.

    MUTASYON: `hmac.compare_digest(str(satir["code_digest"]), beklenen_ozet)`
    satırını `satir["code_digest"] == beklenen_ozet` yapmak bunu KIRMIZI
    yapar. HİÇBİR davranış testi o mutantı öldürmez.
    """
    _sabit_sureli_kapi(SERVIS, "kod_kullan", {"code_digest", "beklenen_ozet"})


def test_CAS_KOSULU_STATUS_PENDING_ve_KIRACI_YUKLEMLI() -> None:
    """Kodu tüketen UPDATE, `status == PENDING` KOŞULUNU taşıyor.

    CAS'in tamamı budur: iki işçi aynı kodu aynı anda kullanırsa `rowcount`
    TAM OLARAK BİRİNDE 1 olur ve kaybeden taraf bağlantı INSERT'ini de geri
    alır (SAVEPOINT).

    KAPI NEDEN AST'DE — ÖLÇÜLDÜ, VARSAYILMADI: `kod_kullan` bu değişmezi ÜÇ
    BAĞIMSIZ katmanla koruyor (PostgreSQL satır kilidi, okuma sonrası durum
    denetimi, CAS) ve HERHANGİ BİRİ TEK BAŞINA yetiyor. PG ikizindeki yirmi
    eşzamanlı yarış adımı üzerinde ölçüldü: yalnız CAS düşürülünce test
    YEŞİL KALIYOR; ÜÇÜ BİRDEN düşünce kırmızı oluyor (tablo o testin
    başlığında). Yani hiçbir DAVRANIŞ testi bu tek mutantı öldüremez —
    kalıp WA1'in `compare_digest` kapısıyla aynı.

    CAS YİNE DE VAZGEÇİLMEZ: satır kilidi YALNIZ PostgreSQL'de çalışıyor,
    SQLite'ta (geliştirme) geriye durum denetimi ile CAS kalıyor ve durum
    denetimi tek başına bir TOCTOU'dur — okuma ile yazma arasında satır
    değişebilir. Yazmayı koşula bağlayan tek katman CAS'tir.

    MUTASYON: `status == PENDING` yüklemini düşürmek BU KAPIYI kırmızı yapar.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    fn = _fn(kaynak, "kod_kullan")
    guncellemeler = [
        d
        for d in ast.walk(fn)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "update"
    ]
    assert len(guncellemeler) == 1, guncellemeler
    # Zincirin TAMAMI: `update(...)` düğümünü kapsayan en dış ifade.
    parca = ast.get_source_segment(kaynak, fn) or ""
    cas = parca[parca.index("cas = db.execute(") :]
    assert "whatsapp_pairing_codes.c.status == schema.PAIRING_PENDING" in cas
    assert "whatsapp_pairing_codes.c.company_id == company_id" in cas
    assert "int(cas.rowcount or 0) != 1" in parca
    # SAVEPOINT: kaybeden tarafın bağlantı INSERT'i de geri alınmalı.
    assert "begin_nested" in parca


def test_HIZ_SINIRI_KOD_ARAMASINDAN_ONCE_KOSUYOR() -> None:
    """`deneme_say` çağrısı, kod satırının OKUNMASINDAN ÖNCE.

    SIRA bu dosyanın en önemli kapılarından biri ve İKİ ayrı şeyi koruyor:

    1. Var olmayan kod denemesi de SAYILIR. Sayılmasaydı saldırgan hiçbir
       satıra dokunmadan sınırsız "geçersiz kod" cevabı ürettirebilir ve
       Meta mesaj maliyeti oluşturabilirdi.
    2. pysqlite TUZAĞI: SAVEPOINT'ten önce YALNIZ SELECT çalışmışsa sürücü
       örtük bir transaction başlatır ve `RELEASE SAVEPOINT` onu COMMIT eder
       — dıştaki rollback yazılanı geri alamaz. Sayaç UPSERT'i SAVEPOINT'ten
       önce koştuğu için GERÇEK bir transaction zaten açıktır.

    MUTASYON: `deneme_say` çağrısını kaldırmak ya da kod aramasının ARDINA
    taşımak bunu KIRMIZI yapar.
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    fn = _fn(kaynak, "kod_kullan")
    cagrilar = sorted(
        (d.lineno, getattr(d.func, "id", None) or getattr(d.func, "attr", None))
        for d in ast.walk(fn)
        if isinstance(d, ast.Call)
    )
    adlar = [ad for _, ad in cagrilar]
    assert "deneme_say" in adlar, adlar
    assert "begin_nested" in adlar, adlar
    assert adlar.index("deneme_say") < adlar.index("begin_nested"), adlar

    # Sınır GERÇEKTEN karşılaştırılıyor ve karşılaştırma şemadaki sabitten
    # geliyor (elle yazılmış bir 5 DEĞİL).
    govde = ast.get_source_segment(kaynak, fn) or ""
    assert "deneme > schema.PAIRING_PENCERE_SINIRI" in govde
    from app.whatsapp.schema import PAIRING_PENCERE_DAKIKA, PAIRING_PENCERE_SINIRI

    assert PAIRING_PENCERE_SINIRI == 5
    assert PAIRING_PENCERE_DAKIKA == 15


def test_BAGLAM_KIMLIK_COZUMUNDE_OKUNUYOR() -> None:
    """`kimlik_secimi` bağlamı, "tek aday" kısayolundan ÖNCE okuyor.

    MUTASYON: `baglam_oku` çağrısını `kimlik_secimi`den kaldırmak bunu
    KIRMIZI yapar — ve davranışta FİRMA SEÇ adımını düşürür: çok firmalı
    kullanıcı seçimini yapsa bile sonsuza dek seçim ekranında kalırdı.
    """
    kaynak = BAGLAM.read_text(encoding="utf-8")
    fn = _fn(kaynak, "kimlik_secimi")
    govde = ast.get_source_segment(kaynak, fn) or ""
    adlar = [
        (d.lineno, getattr(d.func, "id", None) or getattr(d.func, "attr", None))
        for d in ast.walk(fn)
        if isinstance(d, ast.Call)
    ]
    sirali = [ad for _, ad in sorted(adlar)]
    assert "baglam_oku" in sirali, sirali
    assert "kimlik_coz" in sirali, sirali
    # Bağlam okuması, "tek aday" kısayolunun ÖNÜNDE.
    assert govde.index("baglam_oku") < govde.index("if len(adaylar) == 1")
    # Ve yükteki değer satırın KENDİ firmasıyla doğrulanıyor: elle
    # kurcalanmış bir yük başka firmaya AÇILAMAZ.
    assert "secilen == aday.company_id" in govde


def test_DUZ_KOD_HICBIR_YERE_YAZILMIYOR() -> None:
    """Veritabanına yalnız ÖZET gider; düz kod YALNIZ dönüş değerinde yaşar.

    MUTASYON: `kod_uret`in INSERT'ine düz kodu taşıyan bir sütun eklemek
    bunu KIRMIZI yapar (şemada öyle bir sütun da YOK — iki kapı aynı olguyu
    iki yerden tutuyor).
    """
    kaynak = SERVIS.read_text(encoding="utf-8")
    fn = _fn(kaynak, "kod_uret")
    yazmalar = [
        d
        for d in ast.walk(fn)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "insert"
    ]
    assert len(yazmalar) == 1, yazmalar
    # `insert(...)` düğümünü SARAN `.values(...)` çağrısı: yazılan sütunların
    # TAMAMI orada. `UretilenKod(kod=kod, ...)` DÖNÜŞ değeridir ve bu kapıya
    # girmemeli — düz kodun yaşamasına izin verilen TEK yer odur.
    degerler = next(
        d
        for d in ast.walk(fn)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "values"
    )
    yazilan = {kw.arg for kw in degerler.keywords}
    assert "code_digest" in yazilan, yazilan
    assert not (yazilan & {"code", "kod", "plaintext", "code_plain"}), yazilan
    ozet_parcasi = ast.get_source_segment(kaynak, degerler) or ""
    assert "code_digest=_ozet(kod)" in ozet_parcasi, ozet_parcasi

    # Şemada da düz kod sütunu yok: yalnız 64 karakterlik özet.
    from app.whatsapp.schema import whatsapp_pairing_codes

    sutunlar = {c.name for c in whatsapp_pairing_codes.columns}
    assert "code_digest" in sutunlar
    assert not (sutunlar & {"code", "kod", "plaintext", "code_plain"}), sutunlar
    assert whatsapp_pairing_codes.c.code_digest.type.length == 64

    # Uç, düz kodu DÖNÜYOR (bir kez) ve cevabı önbelleğe ALDIRMIYOR.
    uc = UC.read_text(encoding="utf-8")
    assert '"kod": uretilen.kod' in uc
    assert 'response.headers["Cache-Control"] = "no-store"' in uc


def test_KOD_ENTROPISI_ALT_SINIRIN_USTUNDE() -> None:
    """60 bit; alfabede karışan harf YOK ve kod KRİPTOGRAFİK rastgele.

    MUTASYON: `secrets.choice`ı `random.choice` yapmak bunu KIRMIZI yapar —
    ve o hâlde kod TAHMİN EDİLEBİLİR olurdu (Mersenne Twister durumu birkaç
    çıktıdan geri çözülebilir).
    """
    from app.whatsapp import eslestirme

    assert eslestirme.KOD_ENTROPI_BIT == 60
    assert len(eslestirme.ALFABE) == 32
    assert not (set(eslestirme.ALFABE) & set("ILOU"))

    kaynak = SERVIS.read_text(encoding="utf-8")
    fn = _fn(kaynak, "kod_uret_metin")
    govde = ast.get_source_segment(kaynak, fn) or ""
    assert "secrets.choice" in govde, govde
    assert "random." not in govde, govde


# ------------------------------------------------------- göç turu (SQLite) ---

_GOC_TURU = r'''
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings

motor = sa.create_engine(settings.database_url)
yapilandirma = Config('alembic.ini')
yapilandirma.set_main_option('sqlalchemy.url', settings.database_url)

BAGLANTI = 'whatsapp_links'
KOD = 'whatsapp_pairing_codes'
BAGLAM = 'whatsapp_context'
UCU = {BAGLANTI, KOD, BAGLAM}


def gorunen():
    return set(sa.inspect(motor).get_table_names())


command.upgrade(yapilandirma, 'head')
assert UCU <= gorunen(), 'yukari: tablolar dogmadi'

gozlemci = sa.inspect(motor)

# --- SUTUNLAR ------------------------------------------------------------
sutunlar = {c['name']: c for c in gozlemci.get_columns(BAGLANTI)}
for zorunlu in ('company_id', 'user_id', 'phone', 'is_active',
                'created_at', 'updated_at'):
    assert sutunlar[zorunlu]['nullable'] is False, zorunlu
assert sutunlar['created_by']['nullable'] is True
assert sutunlar['phone']['type'].length == 20, sutunlar['phone']['type']

kod_sutun = {c['name']: c for c in gozlemci.get_columns(KOD)}
for zorunlu in ('company_id', 'user_id', 'code_digest', 'status',
                'expires_at', 'attempt_count', 'max_attempts', 'created_at'):
    assert kod_sutun[zorunlu]['nullable'] is False, zorunlu
for serbest in ('created_by', 'consumed_at', 'cancelled_at',
                'consumed_link_id'):
    assert kod_sutun[serbest]['nullable'] is True, serbest
assert kod_sutun['code_digest']['type'].length == 64

baglam_sutun = {c['name']: c for c in gozlemci.get_columns(BAGLAM)}
for zorunlu in ('company_id', 'user_id', 'phone', 'payload',
                'created_at', 'expires_at'):
    assert baglam_sutun[zorunlu]['nullable'] is False, zorunlu

# --- TEKILLER ------------------------------------------------------------
for tablo, ad in ((BAGLANTI, 'uq_whatsapp_links_company_id'),
                  (KOD, 'uq_wpc_company_id'),
                  (BAGLAM, 'uq_whatsapp_context_company_id')):
    tekiller = {u['name']: tuple(u['column_names'])
                for u in gozlemci.get_unique_constraints(tablo)}
    assert tekiller.get(ad) == ('company_id', 'id'), (tablo, tekiller)

kod_tekil = {u['name']: tuple(u['column_names'])
             for u in gozlemci.get_unique_constraints(KOD)}
assert kod_tekil.get('uq_wpc_code_digest') == ('code_digest',), kod_tekil

baglam_tekil = {u['name']: tuple(u['column_names'])
                for u in gozlemci.get_unique_constraints(BAGLAM)}
assert baglam_tekil.get('uq_whatsapp_context_scope') == (
    'company_id', 'user_id', 'phone'), baglam_tekil

# --- KISMI TEKILLER ------------------------------------------------------
indeksler = {i['name']: i for i in gozlemci.get_indexes(BAGLANTI)}
aktif = indeksler['uq_whatsapp_links_aktif_numara']
assert aktif['unique'], aktif
assert tuple(aktif['column_names']) == ('company_id', 'phone'), aktif
assert 'ix_whatsapp_links_phone' in indeksler

kod_indeks = {i['name']: i for i in gozlemci.get_indexes(KOD)}
assert kod_indeks['uq_wpc_aktif_kod']['unique']
assert tuple(kod_indeks['uq_wpc_aktif_kod']['column_names']) == (
    'company_id', 'user_id')

# --- KISMI TEKIL GERCEKTEN ISIRIYOR (SQLite) -----------------------------
# Bu, gocun `sqlite_where` yukleminin KURULDUGUNUN kaniti. Yuklem yanlis
# yazilmis olsaydi (`is_active = true`) indeks HIC kurulmaz ve asagidaki
# REDDIN yerine bir KABUL gelirdi.
EKLE = ("INSERT INTO whatsapp_links"
        "(company_id,user_id,phone,is_active,created_at,updated_at)"
        " VALUES(:c,:u,:p,:a,:t,:t)")
AN = '2026-09-08 00:00:00'


def ekle(c, u, p, a):
    with motor.begin() as baglanti:
        baglanti.execute(text('PRAGMA foreign_keys=OFF'))
        baglanti.execute(text(EKLE),
                         {'c': c, 'u': u, 'p': p, 'a': a, 't': AN})


def reddedildi(c, u, p, a):
    try:
        ekle(c, u, p, a)
        return False
    except IntegrityError:
        return True


NUM = '905405995959'
ekle(1, 1, NUM, 1)
assert reddedildi(1, 2, NUM, 1), 'ayni firmada IKINCI aktif satir gecti'
assert not reddedildi(2, 3, NUM, 1), 'BASKA firmada ayni numara reddedildi'
assert not reddedildi(1, 4, NUM, 0), 'ayni firmada PASIF satir reddedildi'
assert not reddedildi(1, 5, NUM, 0), 'IKINCI pasif satir reddedildi'
with motor.begin() as baglanti:
    baglanti.execute(text('DELETE FROM whatsapp_links'))

# --- GOC TURU ------------------------------------------------------------
command.downgrade(yapilandirma, '20260910_0078')
dusen = gorunen()
assert not (UCU & dusen), 'asagi: tablolar dusmedi'
# WA1'in tablolari YERINDE: 0079 onlara DOKUNMUYOR.
assert 'whatsapp_inbound' in dusen and 'whatsapp_pairing_attempts' in dusen

command.upgrade(yapilandirma, 'head')
assert UCU <= gorunen(), 'ikinci yukari: tur kapanmadi'
yeniden = {i['name'] for i in sa.inspect(motor).get_indexes(BAGLANTI)}
assert 'uq_whatsapp_links_aktif_numara' in yeniden, yeniden
assert 'ix_whatsapp_links_phone' in yeniden, yeniden
print('GOC TURU TAMAM')
'''


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Üç tablo doğuyor, `downgrade` üçünü de GERİ ALIYOR, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo ve indeks adlarını
    SABİTTEN okuyor ve dizge araması onu göremezdi. Daha önemlisi bu betik
    KISMİ TEKİLİN GERÇEKTEN ISIRDIĞINI ölçüyor — göçün `sqlite_where`
    yüklemi yanlış yazılmış olsaydı indeks HİÇ kurulmaz, hiçbir statik kapı
    bunu görmez ve üretimde aynı numara aynı firmada iki kez bağlanabilirdi.

    ALT SÜREÇ ZORUNLU: betik KENDİ `DATABASE_URL`iyle TAZE bir şema kurar ve
    `app.config.Settings` modül düzeyinde TEK KOPYADIR, yani süreç İÇİNDE
    değiştirilemez.
    """
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + (tmp_path / "goc.db").as_posix()
    ortam["SUNGUR_DATA_DIR"] = str(tmp_path)
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _GOC_TURU],
        cwd=str(BACKEND), env=ortam, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "GOC TURU TAMAM" in sonuc.stdout


# ------------------------------------------------------------ davranis ----

@pytest.fixture(scope="module")
def uygulama():
    """Gerçek şemalı uygulama; alt süreç GEREKMİYOR.

    Ortam bu dosyanın MODÜL BAŞINDA kuruldu (gerekçe orada). `TestClient`
    bağlamı açılış olaylarını koşturur, yani göçler burada uygulanır.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


#: Her testin kendi firmalari ve kullanicilari olsun diye artan sayac.
#: Ayni ada iki kez yazmak `uq_app_users_email_lower`i kirar; sayac testleri
#: birbirinden ayirir ve SIRA BAGIMLILIGINI da imkansiz kilar.
_SAYAC = iter(range(1, 10_000))


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma, İKİ kullanıcı, dört üyelik. Her testte TAZE.

    `A` kullanıcısı İKİ firmaya da üye (çok firmalı kullanıcı — `FİRMA SEÇ`
    akışının konusu); `B` yalnız birinci firmaya üye.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    def temizle(db):
        for tablo in (BAGLAM_TABLO, KOD_TABLO, BAGLANTI_TABLO,
                      "whatsapp_pairing_attempts"):
            db.execute(text(f"DELETE FROM {tablo}"))
        db.commit()

    n = next(_SAYAC)
    ad_a = f"WA2 Bir {n} A.S."
    ad_b = f"WA2 Iki {n} A.S."
    with SessionLocal() as db:
        temizle(db)
        an = datetime.now(timezone.utc)
        firma_a = db.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,1,:t) RETURNING id"), {"a": ad_a, "t": an}
        ).scalar_one()
        firma_b = db.execute(
            text("INSERT INTO companies(name,is_active,created_at)"
                 " VALUES(:a,1,:t) RETURNING id"), {"a": ad_b, "t": an}
        ).scalar_one()

        def kullanici(kad: str) -> int:
            return db.execute(
                text("INSERT INTO app_users(username,email,email_verified,"
                     "display_name,password_hash,role,is_active,"
                     "must_change_password,created_at)"
                     " VALUES(:k,:e,1,:k,'x','admin',1,0,:t) RETURNING id"),
                {"k": kad, "e": kad + "@wa2.invalid", "t": an},
            ).scalar_one()

        kul_a = kullanici(f"wa2-a-{n}")
        kul_b = kullanici(f"wa2-b-{n}")

        def uyelik(u: int, c: int) -> None:
            db.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,0,:t)"),
                {"u": u, "c": c, "t": an},
            )

        uyelik(kul_a, firma_a)
        uyelik(kul_a, firma_b)
        uyelik(kul_b, firma_a)
        db.commit()

    veri = {
        "firma_a": int(firma_a), "firma_b": int(firma_b),
        "kul_a": int(kul_a), "kul_b": int(kul_b),
        "ad_a": ad_a, "ad_b": ad_b, "kad_a": f"wa2-a-{n}",
    }
    yield veri
    with SessionLocal() as db:
        temizle(db)


@pytest.fixture()
def oturum(dunya):
    """`dunya`YA BAĞLI ve bu bir süs DEĞİL: fixture SIRASINI belirliyor.

    ÖLÇÜLMÜŞ TUZAK: bağımlılık yazılmasaydı `oturum` ÖNCE kurulur ve `dunya`
    teardown'ı (DELETE) hâlâ AÇIK bir oturumla yarışırdı — SQLite orada
    "database is locked" verir. Bağımlılık, teardown'ı ters sıraya sokuyor:
    önce oturum kapanır, sonra defter temizlenir.
    """
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


def _kod_ver(db, cid: int, uid: int, *, simdi: datetime | None = None) -> str:
    from app.whatsapp import eslestirme

    uretilen = eslestirme.kod_uret(db, cid, uid, simdi=simdi)
    db.commit()
    return uretilen.kod


def _baglantilar(db, cid: int | None = None):
    from sqlalchemy import text

    if cid is None:
        return db.execute(text(
            "SELECT id,company_id,user_id,phone,is_active FROM whatsapp_links"
            " ORDER BY id")).mappings().all()
    return db.execute(text(
        "SELECT id,company_id,user_id,phone,is_active FROM whatsapp_links"
        " WHERE company_id=:c ORDER BY id"), {"c": cid}).mappings().all()


def test_DAVRANIS_kod_uret_KULLAN_baglanti_aciyor(oturum, dunya) -> None:
    """Uçtan uca: kod üret → `BAĞLA <KOD>` → bağlantı doğdu, kod TÜKENDİ.

    Numara İNSAN YAZIMIYLA veriliyor (`0540 599 59 59`) ve KANONİK yazılıyor:
    `normalize_phone` düşerse aynı kişinin iki yazımı iki AYRI numaraya düşer
    ve `kimlik_coz` onu bulamaz.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    kod = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert len(kod) == 12

    sonuc = eslestirme.kod_kullan(oturum, NUMARA_INSAN, kod)
    oturum.commit()
    assert sonuc.basarili, sonuc
    assert sonuc.company_id == dunya["firma_a"]
    assert sonuc.user_id == dunya["kul_a"]

    (satir,) = _baglantilar(oturum, dunya["firma_a"])
    assert satir["phone"] == NUMARA, satir
    assert bool(satir["is_active"]) is True

    kod_satiri = oturum.execute(text(
        "SELECT status,consumed_at,consumed_link_id,cancelled_at"
        " FROM whatsapp_pairing_codes WHERE company_id=:c"),
        {"c": dunya["firma_a"]}).mappings().one()
    assert kod_satiri["status"] == "CONSUMED", kod_satiri
    assert kod_satiri["consumed_at"] is not None
    assert kod_satiri["consumed_link_id"] == satir["id"]
    assert kod_satiri["cancelled_at"] is None

    # AYNI KOD İKİNCİ KEZ KULLANILAMAZ: durum artık PENDING değil.
    ikinci = eslestirme.kod_kullan(oturum, IKINCI_NUMARA, kod)
    oturum.commit()
    assert not ikinci.basarili
    assert len(_baglantilar(oturum)) == 1


def test_DAVRANIS_YANLIS_KOD_BES_KEZ_pencereyi_KILITLIYOR(oturum, dunya) -> None:
    """Beşinci denemeden sonra DOĞRU kod bile tüketilmiyor; sayaç ARTIYOR.

    Sınır `whatsapp_pairing_attempts` tablosundadır ve TELEFON+PENCERE
    bazlıdır — yani var olmayan kod denemeleri de sayılır. Kod satırının
    kendi `attempt_count`u yalnız BULUNAN kodu korur ve o tek başına yetmez:
    rastgele kod deneyen saldırgan hiçbir satıra dokunmaz.

    MUTASYON: `deneme_say` çağrısını kaldırmak ya da sınır
    karşılaştırmasını düşürmek bunu KIRMIZI yapar.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    kod = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])

    # BEŞ yanlış deneme: pencere sınırı (5) DOLDU.
    for i in range(5):
        sonuc = eslestirme.kod_kullan(oturum, NUMARA, "ZZZZ-ZZZZ-ZZZ" + str(i))
        assert not sonuc.basarili, i
        assert not sonuc.sinirlandi, i
    oturum.commit()

    sayac = oturum.execute(text(
        "SELECT attempt_count FROM whatsapp_pairing_attempts WHERE phone=:p"),
        {"p": NUMARA}).scalar_one()
    assert sayac == 5, sayac

    # ALTINCI deneme DOĞRU kodla geliyor ve YİNE reddediliyor.
    sonuc = eslestirme.kod_kullan(oturum, NUMARA, kod)
    oturum.commit()
    assert not sonuc.basarili, sonuc
    assert sonuc.sinirlandi, sonuc
    assert sonuc.cevapla is False, "sinir asildiginda cevap URETILMEMELI"
    assert _baglantilar(oturum) == [], "sinirda BAGLANTI acildi"

    # Kod TÜKENMEDİ ama satırın kendi sayacı ARTTI.
    kod_satiri = oturum.execute(text(
        "SELECT status,attempt_count FROM whatsapp_pairing_codes"
        " WHERE company_id=:c"), {"c": dunya["firma_a"]}).mappings().one()
    assert kod_satiri["status"] == "PENDING", kod_satiri
    assert kod_satiri["attempt_count"] == 1, kod_satiri

    # BAŞKA BİR NUMARA aynı pencerede ETKİLENMİYOR: sınır numara başınadır.
    baska = eslestirme.kod_kullan(oturum, IKINCI_NUMARA, kod)
    oturum.commit()
    assert baska.basarili, baska


def test_DAVRANIS_SURESI_DOLMUS_KOD_reddediliyor(oturum, dunya) -> None:
    """Kod ömrü 10 dakika; 11. dakikada gelen kod TÜKETİLMİYOR.

    Süre denetimi `kod_kullan`ın KENDİSİNDEDİR: bu turda süpürücü YOKTUR ve
    olmaması bir boşluk DEĞİL — satır `PENDING` kalsa bile tüketilemez.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme
    from app.whatsapp.schema import PAIRING_OMRU_DAKIKA

    assert PAIRING_OMRU_DAKIKA == 10
    baslangic = datetime.now(timezone.utc)
    kod = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"], simdi=baslangic)

    # 9. dakikada HÂLÂ geçerli olurdu; 11. dakikada DEĞİL.
    gec = baslangic + timedelta(minutes=PAIRING_OMRU_DAKIKA + 1)
    sonuc = eslestirme.kod_kullan(oturum, NUMARA, kod, simdi=gec)
    oturum.commit()
    assert not sonuc.basarili, sonuc
    assert _baglantilar(oturum) == []

    durum = oturum.execute(text(
        "SELECT status FROM whatsapp_pairing_codes WHERE company_id=:c"),
        {"c": dunya["firma_a"]}).scalar_one()
    assert durum == "PENDING", durum


def test_DAVRANIS_AYNI_FIRMADA_IKINCI_AKTIF_BAGLANTI_REDDEDILIYOR(
    oturum, dunya
) -> None:
    """Aynı `(firma, numara)` iki kez aktif OLAMAZ; BAŞKA firmada OLABİLİR.

    Bu, göç `20260910_0079`un kaynaktan ayrıldığı kararın DAVRANIŞTAKİ
    karşılığı ve tek bir testte İKİ YÖNÜ de ölçülüyor.

    MUTASYON: kısmi tekilden `company_id`yi düşürmek İKİNCİ iddiayı
    (başka firma) KIRMIZI yapar; tekili büsbütün düşürmek BİRİNCİYİ.
    """
    from app.whatsapp import eslestirme

    kod_a = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod_a).basarili
    oturum.commit()

    # AYNI FİRMA, AYNI NUMARA, BAŞKA KULLANICI -> RED.
    kod_ayni = _kod_ver(oturum, dunya["firma_a"], dunya["kul_b"])
    ayni = eslestirme.kod_kullan(oturum, NUMARA, kod_ayni)
    oturum.commit()
    assert not ayni.basarili, ayni
    assert len(_baglantilar(oturum)) == 1

    # BAŞKA FİRMA, AYNI NUMARA -> KABUL. (`kul_a` iki firmaya da üye.)
    kod_b = _kod_ver(oturum, dunya["firma_b"], dunya["kul_a"])
    baska = eslestirme.kod_kullan(oturum, NUMARA, kod_b)
    oturum.commit()
    assert baska.basarili, baska
    assert baska.company_id == dunya["firma_b"]
    assert len(_baglantilar(oturum)) == 2


def test_DAVRANIS_KAPATILAN_BAGLANTI_YENIDEN_ACILABILIYOR(oturum, dunya) -> None:
    """Pasifleştirilen satır tekilin KAPSAMINDAN çıkıyor; yeniden bağlanılabilir.

    MUTASYON: kısmi tekilin `WHERE`ini düşürüp düz `UNIQUE(company_id,
    phone)` yazmak bunu KIRMIZI yapar — ve o hâlde bir kullanıcı numarasını
    bir kez kapattıktan sonra ASLA yeniden bağlayamazdı.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    kod = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()

    oturum.execute(text(
        "UPDATE whatsapp_links SET is_active=0 WHERE company_id=:c"),
        {"c": dunya["firma_a"]})
    oturum.commit()

    kod2 = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod2).basarili
    oturum.commit()
    satirlar = _baglantilar(oturum, dunya["firma_a"])
    assert len(satirlar) == 2, satirlar
    assert [bool(s["is_active"]) for s in satirlar] == [False, True]


def test_DAVRANIS_kimlik_coz_SIFIR_BIR_COK(oturum, dunya) -> None:
    """`kimlik_coz` üç durumu da AYRI AYRI veriyor: 0, 1, çok.

    Ve DÖRDÜNCÜ bir durum ölçülüyor: ÜYELİĞİ DÜŞMÜŞ kullanıcı. Bağlantı
    satırı defterde KALIYOR (izi silmiyoruz) ama kimlik ÇÖZÜLMÜYOR —
    yüklem düşseydi kovulmuş kullanıcı WhatsApp'tan veri okumaya devam
    ederdi.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    # 0 — hiç bağlantı yok.
    assert eslestirme.kimlik_coz(oturum, NUMARA) == []

    # 1 — tek firma.
    kod_a = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod_a).basarili
    oturum.commit()
    tek = eslestirme.kimlik_coz(oturum, NUMARA)
    assert [(k.company_id, k.user_id) for k in tek] == [
        (dunya["firma_a"], dunya["kul_a"])
    ]

    # ÇOK — iki firma. Sıra DETERMİNİSTİK (`company_id`).
    kod_b = _kod_ver(oturum, dunya["firma_b"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod_b).basarili
    oturum.commit()
    coklu = eslestirme.kimlik_coz(oturum, NUMARA)
    assert [k.company_id for k in coklu] == sorted(
        [dunya["firma_a"], dunya["firma_b"]]
    )

    # ÜYELİK DÜŞTÜ -> aday da düştü, ama bağlantı satırı DURUYOR.
    oturum.execute(text(
        "DELETE FROM user_company_memberships WHERE user_id=:u AND company_id=:c"),
        {"u": dunya["kul_a"], "c": dunya["firma_b"]})
    oturum.commit()
    assert [k.company_id for k in eslestirme.kimlik_coz(oturum, NUMARA)] == [
        dunya["firma_a"]
    ]
    assert len(_baglantilar(oturum)) == 2, "baglanti satiri SILINMEMELI"

    # FİRMA PASİFLEŞTİ -> aday düştü, sıfıra indi.
    oturum.execute(text("UPDATE companies SET is_active=0 WHERE id=:c"),
                   {"c": dunya["firma_a"]})
    oturum.commit()
    assert eslestirme.kimlik_coz(oturum, NUMARA) == []


def test_DAVRANIS_FIRMA_LISTELE_ve_SEC_akisi(oturum, dunya) -> None:
    """Çok firmalı numara: sor → listele → seç → çözüldü.

    MUTASYON: `kimlik_secimi`den bağlam okumasını kaldırmak son iddiayı
    KIRMIZI yapar (seçim yapılsa bile hep FİRMA SEÇ dönerdi).
    """
    from app.whatsapp import baglam, eslestirme

    kod_a = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod_a).basarili
    kod_b = _kod_ver(oturum, dunya["firma_b"], dunya["kul_a"])
    assert eslestirme.kod_kullan(oturum, NUMARA, kod_b).basarili
    oturum.commit()

    # SEÇİM YOKKEN: hiçbir firma çözülmüyor — RASTGELE SEÇİM YOK.
    secim = baglam.kimlik_secimi(oturum, NUMARA)
    assert secim.kimlik is None
    assert secim.firma_secimi_gerekli is True
    assert len(secim.adaylar) == 2

    cevap = baglam.komut_isle(oturum, NUMARA, "borcum ne kadar")
    assert cevap == baglam.FIRMA_SECIN_MESAJI

    # LİSTELE: NUMARALI ve `kimlik_coz` sırasıyla AYNI.
    liste = baglam.komut_isle(oturum, NUMARA, "FİRMA LİSTELE")
    assert f"1. {dunya['ad_a']}" in liste, liste
    assert f"2. {dunya['ad_b']}" in liste, liste

    # SEÇ: ikincisi.
    onay = baglam.komut_isle(oturum, NUMARA, "FİRMA SEÇ 2")
    oturum.commit()
    assert onay == f"Aktif firma: {dunya['ad_b']}."

    secim = baglam.kimlik_secimi(oturum, NUMARA)
    assert secim.kimlik is not None
    assert secim.kimlik.company_id == dunya["firma_b"]
    assert secim.firma_secimi_gerekli is False

    # BİRİNCİYE GEÇİŞ: eski bağlam satırı SİLİNİYOR, iki cevap kalmıyor.
    baglam.komut_isle(oturum, NUMARA, "firma sec 1")
    oturum.commit()
    secim = baglam.kimlik_secimi(oturum, NUMARA)
    assert secim.kimlik is not None
    assert secim.kimlik.company_id == dunya["firma_a"]
    from sqlalchemy import text

    adet = oturum.execute(text(
        "SELECT COUNT(*) FROM whatsapp_context WHERE phone=:p"),
        {"p": NUMARA}).scalar_one()
    assert adet == 1, adet

    # ARALIK DIŞI: hiçbir şey yazılmıyor.
    assert baglam.komut_isle(oturum, NUMARA, "FİRMA SEÇ 9") == (
        baglam.SIRA_GECERSIZ_MESAJI
    )


def test_DAVRANIS_BAGLAM_SURESI_DOLUNCA_YENIDEN_SORULUYOR(oturum, dunya) -> None:
    """30 dakikalık bağlam dolunca seçim DÜŞÜYOR ve soru yeniden soruluyor.

    "Aktif firma" bir YETKİ SEÇİMİDİR; bir gün sonra gelen mesajın dün
    seçilmiş firmaya sessizce cevap vermesi YANLIŞ firmanın rakamını verirdi.
    """
    from app.whatsapp import baglam, eslestirme
    from app.whatsapp.schema import BAGLAM_OMRU_DAKIKA

    assert BAGLAM_OMRU_DAKIKA == 30
    for cid in (dunya["firma_a"], dunya["firma_b"]):
        kod = _kod_ver(oturum, cid, dunya["kul_a"])
        assert eslestirme.kod_kullan(oturum, NUMARA, kod).basarili
    oturum.commit()

    an = datetime.now(timezone.utc)
    assert baglam.firma_sec(oturum, NUMARA, 1, simdi=an) is not None
    oturum.commit()

    # 29. dakikada HÂLÂ geçerli.
    erken = baglam.kimlik_secimi(
        oturum, NUMARA, simdi=an + timedelta(minutes=BAGLAM_OMRU_DAKIKA - 1)
    )
    assert erken.kimlik is not None

    # 31. dakikada DÜŞTÜ: seçim yeniden soruluyor.
    gec = baglam.kimlik_secimi(
        oturum, NUMARA, simdi=an + timedelta(minutes=BAGLAM_OMRU_DAKIKA + 1)
    )
    assert gec.kimlik is None
    assert gec.firma_secimi_gerekli is True


def test_DAVRANIS_TANINMAYAN_NUMARA_ve_ANLASILMAYAN_KOMUT(oturum, dunya) -> None:
    """Bağlı olmayan numara YÖNLENDİRİLİYOR; bağlı olan KOMUT LİSTESİ alıyor.

    İkisi AYRI metindir ve ayrım BİLİNÇLİ: bağlı olmayan kullanıcıya
    "BAĞLA <KOD>" yolunu göstermek gerekir, bağlı olana göstermek ise onu
    yeniden eşleştirmeye iterdi.
    """
    from app.whatsapp import baglam, eslestirme

    assert baglam.komut_isle(oturum, NUMARA, "merhaba") == (
        baglam.TANINMAYAN_NUMARA_MESAJI
    )

    kod = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert baglam.komut_isle(oturum, NUMARA, "BAĞLA " + kod) == (
        eslestirme.BASARI_MESAJI
    )
    oturum.commit()
    assert baglam.komut_isle(oturum, NUMARA, "merhaba") == (
        baglam.KOMUT_ANLASILMADI_MESAJI
    )

    # GEÇERSİZ KOD ve HİÇ VAR OLMAMIŞ KOD AYNI metni alıyor.
    assert baglam.komut_isle(oturum, IKINCI_NUMARA, "BAGLA ZZZZ-ZZZZ-ZZZZ") == (
        eslestirme.RED_MESAJI
    )


def test_DAVRANIS_YENI_KOD_ESKISINI_IPTAL_EDIYOR(oturum, dunya) -> None:
    """Firma+kullanıcı başına EN FAZLA BİR bekleyen kod; eskisi CANCELLED.

    Hakem `uq_wpc_aktif_kod` kısmi tekilidir; `kod_uret` iptali AÇIKÇA
    yaparak bir kısıt hatası yerine DOĞRU davranışı üretir.
    """
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    eski = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    yeni = _kod_ver(oturum, dunya["firma_a"], dunya["kul_a"])
    assert eski != yeni

    durumlar = [
        s["status"] for s in oturum.execute(text(
            "SELECT status FROM whatsapp_pairing_codes WHERE company_id=:c"
            " ORDER BY id"), {"c": dunya["firma_a"]}).mappings().all()
    ]
    assert durumlar == ["CANCELLED", "PENDING"], durumlar

    # ESKİ kod artık çalışmıyor, YENİ çalışıyor.
    assert not eslestirme.kod_kullan(oturum, NUMARA, eski).basarili
    oturum.commit()
    assert eslestirme.kod_kullan(oturum, NUMARA, yeni).basarili
    oturum.commit()


def test_DAVRANIS_PASIF_KULLANICIYA_KOD_URETILEMEZ(oturum, dunya) -> None:
    """Beş ret yolu da AYNI metni üretiyor — envanter sızdırılmıyor."""
    from sqlalchemy import text

    from app.whatsapp import eslestirme

    # Başka firmanın kullanıcısı (`kul_b` firma_b'ye üye DEĞİL).
    with pytest.raises(eslestirme.EslestirmeHatasi) as hata:
        eslestirme.kod_uret(oturum, dunya["firma_b"], dunya["kul_b"])
    assert str(hata.value) == eslestirme.HEDEF_RED_MESAJI

    # Hiç var olmayan kullanıcı — AYNI metin.
    with pytest.raises(eslestirme.EslestirmeHatasi) as hata2:
        eslestirme.kod_uret(oturum, dunya["firma_a"], 9_999_999)
    assert str(hata2.value) == eslestirme.HEDEF_RED_MESAJI

    # Pasif kullanıcı — AYNI metin.
    oturum.execute(text("UPDATE app_users SET is_active=0 WHERE id=:u"),
                   {"u": dunya["kul_b"]})
    with pytest.raises(eslestirme.EslestirmeHatasi) as hata3:
        eslestirme.kod_uret(oturum, dunya["firma_a"], dunya["kul_b"])
    assert str(hata3.value) == eslestirme.HEDEF_RED_MESAJI
    oturum.rollback()


# --------------------------------------------------------------- uclar ----

def _giris(istemci, kullanici: str, parola: str, cid: int) -> dict:
    r = istemci.post("/api/auth/login",
                     json={"username": kullanici, "password": parola})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"],
            "X-Company-ID": str(cid)}


@pytest.fixture()
def yonetici(uygulama, dunya):
    """İKİ firmada da yöneticilik yapabilen bir oturum başlığı üreticisi.

    Parola AÇIK bir sabittir ve yalnız bu dosyada yaşar; kullanıcılar bu
    testin kendi fixture'ında doğuyor.
    """
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    parola = "Wa2Test!12345"
    with SessionLocal() as db:
        db.execute(text("UPDATE app_users SET password_hash=:h,"
                        " must_change_password=0 WHERE id IN (:a,:b)"),
                   {"h": hash_password(parola), "a": dunya["kul_a"],
                    "b": dunya["kul_b"]})
        db.commit()

    def basliklar(cid: int, kullanici: str | None = None) -> dict:
        return _giris(uygulama, kullanici or dunya["kad_a"], parola, cid)

    return basliklar


def test_UC_kod_uret_DUZ_KODU_BIR_KEZ_donuyor(uygulama, dunya, yonetici) -> None:
    """POST /pairing-codes: düz kod YALNIZ bu cevapta; `no-store` başlığı var.

    Sonraki hiçbir okuma kodu VERMEZ — `GET /links` bile telefonu MASKELİ
    döndürüyor.
    """
    h = yonetici(dunya["firma_a"])
    r = uygulama.post("/api/whatsapp/pairing-codes", headers=h,
                      json={"user_id": dunya["kul_b"]})
    assert r.status_code == 201, r.text
    govde = r.json()
    assert len(govde["kod"]) == 12
    assert govde["kod_gosterim"].count("-") == 2
    assert govde["kod_gosterim"].replace("-", "") == govde["kod"]
    assert govde["user_id"] == dunya["kul_b"]
    assert r.headers["cache-control"] == "no-store"

    # Kod düz metin olarak HİÇBİR YERDE saklanmıyor.
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        ozet = db.execute(text(
            "SELECT code_digest FROM whatsapp_pairing_codes WHERE id=:i"),
            {"i": govde["kod_id"]}).scalar_one()
    assert ozet != govde["kod"]
    assert len(ozet) == 64

    # `extra="forbid"`: sessizce yok sayılan alan YOK.
    kotu = uygulama.post("/api/whatsapp/pairing-codes", headers=h,
                         json={"user_id": dunya["kul_b"], "company_id": 1})
    assert kotu.status_code == 422, kotu.text


def test_UC_KIRACI_YALITIMI_dort_ucta_da(uygulama, dunya, yonetici) -> None:
    """Dört ucun DÖRDÜ de `company_id` ile daralıyor.

    `wa2-a` İKİ firmaya da üye, yani bu test bir YETKİ sorusu değil bir
    KAPSAM sorusu soruyor: aynı kullanıcı B firmasının oturumundayken A
    firmasının kodunu ve bağlantısını GÖREMEZ, İPTAL EDEMEZ, KAPATAMAZ.

    MUTASYON: uçlardan `company_id` yüklemini düşürmek bunu KIRMIZI yapar.
    """
    from app.db import SessionLocal
    from app.whatsapp import eslestirme

    h_a = yonetici(dunya["firma_a"])
    h_b = yonetici(dunya["firma_b"])

    # A firmasında bir kod ve bir bağlantı.
    kod_cevap = uygulama.post("/api/whatsapp/pairing-codes", headers=h_a,
                              json={"user_id": dunya["kul_a"]})
    assert kod_cevap.status_code == 201, kod_cevap.text
    kod_id = kod_cevap.json()["kod_id"]
    with SessionLocal() as db:
        assert eslestirme.kod_kullan(db, NUMARA, kod_cevap.json()["kod"]).basarili
        db.commit()
        baglanti_id = int(_baglantilar(db, dunya["firma_a"])[0]["id"])

    # 1) GET /links — B firmasında BOŞ, A firmasında BİR satır.
    assert uygulama.get("/api/whatsapp/links", headers=h_b).json() == []
    a_liste = uygulama.get("/api/whatsapp/links", headers=h_a).json()
    assert len(a_liste) == 1, a_liste
    # TELEFON MASKELİ: tam numara hiçbir uçtan çıkmıyor.
    assert a_liste[0]["phone_masked"] == "***" + NUMARA[-4:]
    assert NUMARA not in json.dumps(a_liste)

    # 2) DELETE /pairing-codes/{id} — B firmasından 404.
    assert uygulama.delete(
        f"/api/whatsapp/pairing-codes/{kod_id}", headers=h_b
    ).status_code == 404

    # 3) DELETE /links/{id} — B firmasından 404, satır DURUYOR.
    assert uygulama.delete(
        f"/api/whatsapp/links/{baglanti_id}", headers=h_b
    ).status_code == 404
    with SessionLocal() as db:
        assert bool(_baglantilar(db, dunya["firma_a"])[0]["is_active"]) is True

    # 4) POST /pairing-codes — B firmasından `kul_b` için 422: `kul_b` B'ye
    #    üye DEĞİL ve ret metni "yok"/"başka firmanın"/"pasif" ayrımını
    #    SIZDIRMIYOR.
    red = uygulama.post("/api/whatsapp/pairing-codes", headers=h_b,
                        json={"user_id": dunya["kul_b"]})
    assert red.status_code == 422, red.text
    assert red.json()["detail"] == eslestirme.HEDEF_RED_MESAJI


def test_UC_link_kapatma_kimligi_DUSURUYOR(uygulama, dunya, yonetici) -> None:
    """DELETE /links/{id} bağlantıyı PASİFLEŞTİRİYOR ve `kimlik_coz` düşüyor.

    Satır SİLİNMİYOR: "kim ne zaman bağlıydı" izi kalıyor.
    """
    from app.db import SessionLocal
    from app.whatsapp import eslestirme

    h = yonetici(dunya["firma_a"])
    kod = uygulama.post("/api/whatsapp/pairing-codes", headers=h,
                        json={"user_id": dunya["kul_a"]}).json()["kod"]
    with SessionLocal() as db:
        assert eslestirme.kod_kullan(db, NUMARA, kod).basarili
        db.commit()
        baglanti_id = int(_baglantilar(db, dunya["firma_a"])[0]["id"])
        assert len(eslestirme.kimlik_coz(db, NUMARA)) == 1

    r = uygulama.delete(f"/api/whatsapp/links/{baglanti_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": baglanti_id, "is_active": False}

    with SessionLocal() as db:
        satirlar = _baglantilar(db, dunya["firma_a"])
        assert len(satirlar) == 1, "satir SILINMEMELI"
        assert bool(satirlar[0]["is_active"]) is False
        assert eslestirme.kimlik_coz(db, NUMARA) == []

    # İKİNCİ KEZ: idempotent DEĞİL, 404.
    assert uygulama.delete(
        f"/api/whatsapp/links/{baglanti_id}", headers=h
    ).status_code == 404


def test_UC_kod_iptali_kodu_KULLANILAMAZ_yapiyor(uygulama, dunya, yonetici) -> None:
    """DELETE /pairing-codes/{id} kodu CANCELLED yapıyor; tüketilemiyor."""
    from app.db import SessionLocal
    from app.whatsapp import eslestirme

    h = yonetici(dunya["firma_a"])
    cevap = uygulama.post("/api/whatsapp/pairing-codes", headers=h,
                          json={"user_id": dunya["kul_a"]}).json()

    r = uygulama.delete(f"/api/whatsapp/pairing-codes/{cevap['kod_id']}",
                        headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"kod_id": cevap["kod_id"], "status": "CANCELLED"}

    with SessionLocal() as db:
        assert not eslestirme.kod_kullan(db, NUMARA, cevap["kod"]).basarili
        db.commit()
        assert _baglantilar(db) == []

    # İKİNCİ KEZ: 404 (yok / başka firmanın / zaten kapanmış — AYNI cevap).
    assert uygulama.delete(
        f"/api/whatsapp/pairing-codes/{cevap['kod_id']}", headers=h
    ).status_code == 404


def test_UC_AKTIVITE_KAYDI_yaziliyor_ve_SIR_TASIMIYOR(
    uygulama, dunya, yonetici
) -> None:
    """Üç olay da aktivite defterine giriyor; kod ve ham telefon YAZILMIYOR.

    `log_activity` bilinmeyen bir olay tipini `ValueError` ile reddeder, yani
    `ACTION_TYPES`taki üç satır bu uçların ÖNKOŞULUDUR.
    """
    from sqlalchemy import text

    from app.activity_log import ACTION_TYPES
    from app.db import SessionLocal
    from app.whatsapp import eslestirme

    for tur in ("user.whatsapp_pairing_code_created",
                "user.whatsapp_pairing_code_cancelled",
                "user.whatsapp_link_deactivated"):
        assert tur in ACTION_TYPES, tur

    h = yonetici(dunya["firma_a"])
    cevap = uygulama.post("/api/whatsapp/pairing-codes", headers=h,
                          json={"user_id": dunya["kul_a"]}).json()
    with SessionLocal() as db:
        assert eslestirme.kod_kullan(db, NUMARA, cevap["kod"]).basarili
        db.commit()
        baglanti_id = int(_baglantilar(db, dunya["firma_a"])[0]["id"])
    uygulama.delete(f"/api/whatsapp/links/{baglanti_id}", headers=h)

    with SessionLocal() as db:
        kayitlar = db.execute(text(
            "SELECT action_type, resource_type, resource_id, summary, details"
            " FROM activity_logs WHERE company_id=:c AND action_type LIKE"
            " 'user.whatsapp%' ORDER BY id"), {"c": dunya["firma_a"]}
        ).mappings().all()
    assert [k["action_type"] for k in kayitlar] == [
        "user.whatsapp_pairing_code_created",
        "user.whatsapp_link_deactivated",
    ], kayitlar
    hepsi = json.dumps([dict(k) for k in kayitlar], default=str)
    assert cevap["kod"] not in hepsi, "DUZ KOD aktivite kaydina girdi"
    assert NUMARA not in hepsi, "HAM TELEFON aktivite kaydina girdi"
    assert NUMARA[-4:] in hepsi, "maskeli numara kayitta yok"
    for kayit in kayitlar:
        assert kayit["resource_type"] == "user"
