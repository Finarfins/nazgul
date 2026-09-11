"""WHATSAPP GİRİŞİ: Meta webhook'u ve yazdığı PLATFORM kuyruğu (WA1).

Konu: göç `20260910_0078`, `app/whatsapp/schema.py`,
`app/whatsapp/cloud_api.py`, `app/whatsapp/giris.py`,
`app/routers/whatsapp.py` ve `app/main.py`nin `PUBLIC_API` kümesi.

ÖLÇÜLEN EKSİK: WA3 niyet çözücüyü getirdi ve başlığında "veritabanı yok,
tablo yok, rota yok" diyordu. Yani çözücüye mesaj GETİREN hiçbir şey
yoktu. Bu dilim o yolu açıyor — ve YALNIZ onu: işçi, cevap, eşleştirme
ve giden mesaj bu turda YOKTUR.

Şekil deponun kalıbıyla BİREBİR: STATİK KAPILAR + alt süreçte göç turu +
GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * HMAC'i HAM gövde yerine AYRIŞTIRILMIŞ JSON üzerinden almak
                                    -> SIRA kapısı ve HMAC adımları KIRMIZI
  * `verify_signature` çağrısını `json.loads`un ARDINA almak
                                    -> SIRA kapısı KIRMIZI
  * Gövde sınırını (`GOVDE_MAKS_BAYT`) kaldırmak
                                    -> SINIR kapısı ve 413 adımı KIRMIZI
  * `wamid` UNIQUE kısıtını düşürmek
                                    -> göç kapısı ve KOPYA adımı KIRMIZI
  * `phone_number_id` süzgecini kaldırmak
                                    -> YABANCI NUMARA adımı KIRMIZI
  * `hmac.compare_digest`i `==` yapmak (imza VEYA doğrulama jetonu)
                                    -> SABİT SÜRE kapıları KIRMIZI (davranış
                                       testlerinin HİÇBİRİ bu mutantı
                                       öldürmez; kaybolan şey zamanlamadır)
  * `_acik_ayar`ı gevşetmek (üç ayardan birini yeterli saymak)
                                    -> YAPILANDIRILMAMIŞ adımı KIRMIZI
  * Uçları `PUBLIC_API`den çıkarmak
                                    -> MUAFİYET kapısı KIRMIZI (ve webhook
                                       401 alır, Meta teslimatı tekrarlar)
  * `PUBLIC_API`ye TAM YOL yerine ÖNEK yazmak
                                    -> MUAFİYET kapısı KIRMIZI
  * WA1'in iki tablosuna `company_id` eklemek
                                    -> KİRACI ENVANTERİ kapısı KIRMIZI
  * `whatsapp.schema.metadata`yı `create_all` etmek
                                    -> AÇILIŞ DDL'i kapısı KIRMIZI
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260910_0078_whatsapp_ingress.py"
SEMA = BACKEND / "app" / "whatsapp" / "schema.py"
TASIMA = BACKEND / "app" / "whatsapp" / "cloud_api.py"
YAZMA = BACKEND / "app" / "whatsapp" / "giris.py"
UC = BACKEND / "app" / "routers" / "whatsapp.py"
ANA = BACKEND / "app" / "main.py"

#: WA1'in İKİ PLATFORM tablosu. WA2 aynı modüle ÜÇ KİRACI tablosu daha
#: ekledi; aşağıdaki kapılar bu ikisinden başkasına bakmaz.
KUYRUK_TABLO = "whatsapp_inbound"
SAYAC_TABLO = "whatsapp_pairing_attempts"


def _core_tablo_govdesi(ad: str) -> str:
    """`app/whatsapp/schema.py`deki `Table("<ad>", ...)` çağrısının KAYNAĞI.

    Dosyanın tamamını grep'lemek WA2'den sonra yanlış soruyu sorar (o
    dosyada artık `company_id` taşıyan tablolar da var). AST, sorulan
    tablonun KENDİ gövdesini veriyor.
    """
    kaynak = SEMA.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Call)
            and getattr(dugum.func, "id", None) == "Table"
            and dugum.args
            and isinstance(dugum.args[0], ast.Constant)
            and dugum.args[0].value == ad
        ):
            return ast.get_source_segment(kaynak, dugum) or ""
    raise AssertionError(f"Core tanımında `{ad}` tablosu bulunamadı")


#: SIR ve JETON testin KENDİSİNDEDİR ve gerçek bir kurulumla ilgisi yoktur.
SIR = "wa1-test-app-secret"
JETON = "wa1-test-verify-token"
PNID = "111222333444555"

# UYGULAMA İÇE AKTARILMADAN ÖNCE ORTAM KURULUR. `app.config.Settings` modül
# düzeyinde TEK KOPYADIR: içe aktarma bir kez olduktan sonra `DATABASE_URL`i
# değiştirmenin hiçbir etkisi kalmaz. Kanonik koşucu (`run_isolated_tests.py`)
# her dosyayı KENDİ sürecinde koşturuyor, bu yüzden burada modül düzeyinde
# yazmak güvenlidir ve alt sürece gerek bırakmaz.
_CALISMA = Path(tempfile.mkdtemp(prefix="wa1-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "wa1.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"
os.environ["WHATSAPP_APP_SECRET"] = SIR
os.environ["WHATSAPP_VERIFY_TOKEN"] = JETON
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = PNID

sys.path.insert(0, str(BACKEND))


# --------------------------------------------------------------- statik ---

def test_goc_IKI_PLATFORM_TABLOSU_aciyor_ve_company_id_TASIMIYOR() -> None:
    """İki tablo da `company_id` TAŞIMIYOR — bu, göçün en önemli kararı.

    MUTASYON: sütunu eklemek bunu KIRMIZI yapar ve haklı olarak: sütun
    eklendiği an tablo `TENANT_TABLES` envanterine girer (envanter göç
    edilmiş şemadan OTOMATİK türetiliyor) ve her sorgusundan
    `company_id=:cid` yüklemi istenir — karşılığı OLMAYAN bir yüklem.
    Webhook'a gelen mesaj henüz hiçbir firmaya ait DEĞİLDİR.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'KUYRUK = "whatsapp_inbound"' in kaynak
    assert 'SAYAC = "whatsapp_pairing_attempts"' in kaynak
    assert '"company_id"' not in kaynak, "platform tablosuna company_id girdi"
    assert '"user_id"' not in kaynak, "platform tablosuna user_id girdi"
    # Aynı iddia Core tanımında da tutmak zorunda: ayrışsalardı testler
    # `create_all` şemasını ölçer, üretim göç şemasında koşardı.
    #
    # İDDİA WA2'DE (göç `20260910_0079`) DARALDI ve daralmanın kendisi
    # ölçülüyor: `app/whatsapp/schema.py` artık ÜÇ KİRACI tablosu da taşıyor
    # (`whatsapp_links`, `whatsapp_pairing_codes`, `whatsapp_context`) ve
    # üçünde de `company_id` VAR — dosyanın TAMAMINDA sütun adını aramak
    # artık WA1'in iddiasını değil, WA2'nin varlığını ölçerdi. Bu yüzden
    # iddia BU İKİ TABLONUN kendi gövdesine bakıyor ve `ast` ile ayrılıyor:
    # bir yorum satırındaki "company_id" kapıyı yanıltamasın.
    for tablo in (KUYRUK_TABLO, SAYAC_TABLO):
        govde = _core_tablo_govdesi(tablo)
        assert '"company_id"' not in govde, tablo
        assert '"user_id"' not in govde, tablo


def test_wamid_TEKILI_IDEMPOTENSININ_KENDISI() -> None:
    """`wamid` UNIQUE; hakem uygulama değil VERİTABANI.

    MUTASYON: kısıtı düşürmek bunu KIRMIZI yapar — ve davranışta KOPYA
    adımını düşürür: Meta'nın ikinci teslimatı İKİNCİ BİR SATIR üretir ve
    aynı mesaj iki kez işlenirdi. "Önce SELECT sonra INSERT" bu işi
    yapamaz; iki eşzamanlı webhook çağrısı ikisi de "yok" görebilir.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert 'sa.UniqueConstraint("wamid", name=TEKIL_WAMID)' in kaynak
    assert 'TEKIL_WAMID = "uq_whatsapp_inbound_wamid"' in kaynak
    # Deneme sayacının tekili de aynı sınıftan: tekil olmasaydı iki
    # eşzamanlı deneme İKİ satır üretir ve sınır HİÇ ısırmazdı.
    assert 'sa.UniqueConstraint("phone", "window_start"' in kaynak


def test_durum_kumesi_KAPALI_ve_CHECK_ile_civili() -> None:
    """Beş değer var, veritabanı seviyesinde ısırıyor, iki yerde AYNI.

    MUTASYON: CHECK'i kaldırmak bunu KIRMIZI yapar. Küme açık olsaydı
    tanınmayan bir durum, hiçbir işçinin SEÇMEDİĞİ sessiz bir ölü satır
    üretirdi. Göç ile Core tanımının AYRIŞMASI da kırmızıdır: ayrışsalardı
    testlerin ölçtüğü şema ile üretimin şeması güvenlik anlamı bakımından
    farklı olurdu.
    """
    goc = GOC.read_text(encoding="utf-8")
    assert 'DURUMLAR = ("RECEIVED", "PROCESSING", "ANSWERED", "IGNORED", "DEAD")' in goc
    assert "ck_whatsapp_inbound_status" in goc
    assert 'sa.CheckConstraint("attempt_count >= 0"' in goc

    from app.whatsapp.schema import INBOUND_STATUSES

    assert INBOUND_STATUSES == frozenset(
        {"RECEIVED", "PROCESSING", "ANSWERED", "IGNORED", "DEAD"}
    )
    assert (
        "status IN ('RECEIVED','PROCESSING','ANSWERED','IGNORED','DEAD')"
        in SEMA.read_text(encoding="utf-8")
    )


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """İki tablo da açılış DDL'inde BİLDİRİLMİYOR.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu; 0074-0077'de aynı ayrım
    yapıldı): `app/tenancy.py` `companies`i `Table()` olarak bildiriyor ve
    uygulamanın AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor; göç onu VAR
    bulup tek `if` dalını ATLAR ve YEŞİL biter — CHECK kısıtları HİÇ
    kurulmaz.

    `app/whatsapp/schema.py` bir `MetaData` taşıyor ama HİÇBİR YERDE
    `create_all` EDİLMİYOR; bu kapı o olguyu ölçüyor.
    """
    acilis = ""
    for modul in ("tenancy.py", "core_schema.py", "auth.py", "inventory.py",
                  "finance_engine.py", "workflow.py"):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    acilis += (BACKEND / "app" / "notifications" / "schema.py").read_text(
        encoding="utf-8"
    )
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))

    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    assert "whatsapp_inbound" not in bildirilen
    assert "whatsapp_pairing_attempts" not in bildirilen

    # VE ŞEMA MODÜLÜ HİÇ `create_all` EDİLMİYOR: edilseydi yukarıdaki
    # `Table()` taraması onu görmezdi ama kusur AYNEN doğardı.
    #
    # KAPI WA2'DE KESKİNLEŞTİ ve gerekçesi ölçülmüş bir YANLIŞ SORUDUR:
    # eski hâli `app/` metninde "whatsapp.schema" dizesini ARIYORDU, yani
    # tabloyu İÇE AKTARMAYI de yasaklıyordu. WA2'nin uçları o tabloları
    # okumak zorunda (`from ..whatsapp.schema import whatsapp_links`) ve o
    # import HİÇBİR şema kurmaz. Yasaklanması gereken şey import DEĞİL,
    # `create_all` ÇAĞRISIDIR — kusuru üreten tek şey odur.
    #
    # MUTASYON: `app/` içinde herhangi bir yere `whatsapp.schema.metadata.
    # create_all(engine)` yazmak bunu KIRMIZI yapar.
    for yol in sorted((BACKEND / "app").rglob("*.py")):
        metin = yol.read_text(encoding="utf-8")
        if "create_all" not in metin:
            continue
        agac = ast.parse(metin)
        for dugum in ast.walk(agac):
            if (
                isinstance(dugum, ast.Call)
                and getattr(dugum.func, "attr", None) == "create_all"
            ):
                parca = ast.get_source_segment(metin, dugum) or ""
                assert "whatsapp" not in parca, (yol.name, parca)

    # Şema modülünün KENDİSİ de hiçbir şey kurmuyor. AST'den ölçülüyor:
    # dosyanın yorumlarında `create_all` sözcüğü GEÇİYOR (kapının kendisini
    # anlatan cümlede) ve metin araması onu ÇAĞRI sanardı.
    sema_agaci = ast.parse(SEMA.read_text(encoding="utf-8"))
    assert not [
        d
        for d in ast.walk(sema_agaci)
        if isinstance(d, ast.Call)
        and (getattr(d.func, "attr", None) or getattr(d.func, "id", None))
        == "create_all"
    ]


def test_KIRACI_ENVANTERI_KIMILDAMADI() -> None:
    """WA1'in iki tablosu `TENANT_TABLES`a GİRMİYOR — WA2'nin ÜÇÜ GİRİYOR.

    Envanter elle yazılmış bir muafiyet listesi DEĞİL; göç edilmiş şemadan
    `company_id` sütunu taşıyan tablolar taranarak türetiliyor
    (`test_tenant_scoping_guard.py::
    test_tenant_table_inventory_matches_migrated_schema`). Yani muafiyetin
    kendisi SÜTUNUN YOKLUĞUDUR ve bayatlayacak bir satır yoktur.

    MUTASYON: iki tablodan birine `company_id` eklemek bu kapıyı da o
    kapıyı da KIRMIZI yapar.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert KUYRUK_TABLO not in TENANT_TABLES
    assert SAYAC_TABLO not in TENANT_TABLES
    # WA2 (göç `20260910_0079`) ÜÇ KİRACI tablosu ekledi ve ÜÇÜ DE ENVANTERE
    # GİRDİ: 116 -> 119. Bu, yukarıdaki iddianın ZAYIFLAMASI DEĞİL
    # KANITIDIR — envanter elle yazılmış bir muafiyet listesi olsaydı üç
    # tablo da sessizce dışarıda kalabilirdi; şemadan türediği için
    # `company_id` taşıyanlar GİRDİ, taşımayanlar GİRMEDİ.
    # WA4 (göç `20260910_0080`) BİR tablo daha ekledi: 119 -> 120. WA1'in
    # İKİ tablosu HÂLÂ dışarıda ve iddia aynen ayakta.
    assert {"whatsapp_links", "whatsapp_pairing_codes", "whatsapp_context"} <= (
        TENANT_TABLES
    )
# 120 -> 121: E4a e-IRSALIYE DEFTERI (goc 20260913_0083). Tek yeni kiraci
    # tablosu `despatch_notes`; `company_id` tasir, yani envantere OTOMATIK
    # girer ve her sorgusundan `company_id=:cid` yuklemi istenir.
    # 121 -> 122: CS1 CEK/SENET PORTFOYU (goc 20260914_0085). Tek yeni kiraci
    # tablosu `cek_senetler`; `company_id` tasir, envantere OTOMATIK girer.
    assert len(TENANT_TABLES) == 122, len(TENANT_TABLES)


def test_MUAFIYET_TAM_YOL_ve_ONEK_DEGIL() -> None:
    """İki uç `PUBLIC_API`de, TAM YOL ile — `/api/auth/login` ile AYNI biçim.

    `PUBLIC_API` üyeliği `security_and_audit` içindeki kimlik + CSRF
    bloğunun TAMAMINI atlar; ayrı bir CSRF muafiyet listesi YOKTUR ve
    aranmamalıdır (ÖLÇÜLDÜ: `main.py`de CSRF denetimi o bloğun İÇİNDE).

    MUTASYON: satırı silmek bunu KIRMIZI yapar — ve o hâlde Meta'nın
    oturumsuz isteği 401 alır, Meta teslimatı tekrarlar ve kanal HİÇ
    çalışmaz. Önek muafiyeti yazmak da KIRMIZIDIR: bu router'a yarın
    eklenecek bir yönetim ucu SESSİZCE oturumsuz açılırdı.
    """
    from app.main import PUBLIC_API

    assert "/api/whatsapp/webhook" in PUBLIC_API
    assert "/api/whatsapp" not in PUBLIC_API
    assert "/api/whatsapp/" not in PUBLIC_API
    # Biçim `/api/auth/login` ile aynı sınıfta: küme TAM YOL taşıyor.
    assert "/api/auth/login" in PUBLIC_API

    ana = ANA.read_text(encoding="utf-8")
    govde = ana.split("async def security_and_audit(")[1].split("\n@app.")[0]
    # CSRF kapısı, PUBLIC_API'yi eleyen dalın İÇİNDE: ayrı bir muafiyet
    # listesi olsaydı bu iddia düşerdi ve muafiyet İKİ yerde tutulurdu.
    assert "path not in PUBLIC_API" in govde
    assert "csrf_is_valid(request)" in govde
    assert govde.index("path not in PUBLIC_API") < govde.index("csrf_is_valid(request)")


def test_SIRA_imza_JSON_AYRISTIRMADAN_ONCE() -> None:
    """`verify_signature` HAM baytları alıyor ve `json.loads`tan ÖNCE koşuyor.

    Bu dosyanın en önemli kapısı. MUTASYON: imzayı ayrıştırılmış gövde
    üzerinden almak (`verify_signature(json.dumps(govde).encode(), ...)`)
    ya da doğrulamayı ayrıştırmanın ARDINA taşımak bunu KIRMIZI yapar.

    İkisi de gerçek kusurdur: (a) `loads`→`dumps` turu anahtar sırasını,
    boşluğu ve sayı biçimini değiştirebilir, yani GEÇERLİ imzalar
    reddedilirdi; (b) doğrulama sonraya kalırsa İMZASIZ bir gövde
    ayrıştırıcıya ULAŞIR.

    Sıra kaynak METNİNDEN değil, AST'den ölçülüyor: bir yorum satırındaki
    "json.loads" ifadesi kapıyı yanıltamasın.
    """
    agac = ast.parse(UC.read_text(encoding="utf-8"))
    govde = next(
        d for d in ast.walk(agac)
        if isinstance(d, ast.AsyncFunctionDef) and d.name == "webhook"
    )
    cagrilar = [
        (d.lineno, getattr(d.func, "id", None) or getattr(d.func, "attr", None))
        for d in ast.walk(govde) if isinstance(d, ast.Call)
    ]
    adlar = [ad for _, ad in sorted(cagrilar)]
    assert "verify_signature" in adlar, adlar
    assert "loads" in adlar, adlar
    assert adlar.index("verify_signature") < adlar.index("loads"), adlar

    # İmzaya verilen argüman GÖVDENİN KENDİSİ (`ham`), türetilmiş bir şey
    # değil: `request.body()`nin döndürdüğü ad.
    imza_cagrisi = next(
        d for d in ast.walk(govde)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "verify_signature"
    )
    assert isinstance(imza_cagrisi.args[0], ast.Name)
    assert imza_cagrisi.args[0].id == "ham"


def _sabit_sureli_kapi(yol: Path, fn_adi: str, operandlar: set[str]) -> None:
    """`fn_adi` içindeki gizli karşılaştırma `==`/`!=` ile YAPILMIYOR.

    Kalıp `tests/test_wa3_kopru.py::test_dogrula_sabit_sureli_karsilastirir`
    ile AYNI ve gerekçesi ölçülmüş bir KÖR NOKTADIR: `hmac.compare_digest`i
    `==` ile değiştiren bir mutant DAVRANIŞI DEĞİŞTİRMEZ — doğru imza yine
    200, yanlış imza yine 403 alır — yani hiçbir davranış testi onu
    öldüremez. Kaybolan şey ZAMANLAMADIR: `==` ilk farklı baytta döner ve
    cevabın gecikmesi kaç baytın tuttuğunu sızdırır. Bu uçlar OTURUMSUZ,
    yani deneme sayısı da serbest.

    İki assert AYRI kusuru kapatıyor: birincisi çağrının VARLIĞINI, ikincisi
    KARARIN o çağrıdan geldiğini. İkincisi olmasaydı `compare_digest`i
    çağırıp sonucunu ATAN ve kararı `==` ile veren bir mutant geçerdi.

    Operand etiketi `Name.id` ya da `Attribute.attr`tan okunuyor: gizli
    değer bu iki uçta bir sabit adla da (`beklenen`) bir nitelikle de
    (`ayar.verify_token`) geliyor.
    """
    kaynak = yol.read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    fn = next(
        n for n in agac.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_adi
    )
    govde = ast.get_source_segment(kaynak, fn)
    assert "hmac.compare_digest(" in govde, (yol.name, fn_adi)

    def etiket(dugum: ast.expr) -> str | None:
        if isinstance(dugum, ast.Name):
            return dugum.id
        if isinstance(dugum, ast.Attribute):
            return dugum.attr
        return None

    for dugum in ast.walk(fn):
        if not isinstance(dugum, ast.Compare):
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


def test_IMZA_SABIT_SURELI_KARSILASTIRILIYOR() -> None:
    """POST yolu: `X-Hub-Signature-256` `==` ile DEĞİL `compare_digest` ile.

    MUTASYON: `cloud_api.verify_signature`daki `hmac.compare_digest`i `==`
    yapmak bunu KIRMIZI yapar. HİÇBİR davranış testi o mutantı öldürmez —
    gerekçe `_sabit_sureli_kapi`nin başlığında.
    """
    _sabit_sureli_kapi(TASIMA, "verify_signature", {"beklenen", "verilen"})


def test_DOGRULAMA_JETONU_SABIT_SURELI_KARSILASTIRILIYOR() -> None:
    """GET yolu: `hub.verify_token` `==` ile DEĞİL `compare_digest` ile.

    MUTASYON: `routers/whatsapp.py::dogrula`daki `hmac.compare_digest`i
    `==`/`!=` yapmak bunu KIRMIZI yapar. Bu uç el sıkışmasıdır ve hiçbir
    şey yazmaz, ama sızdırdığı şey KURULUM JETONUDUR: jetonu ele geçiren
    biri Meta tarafında webhook aboneliğini kendi sunucusuna çevirebilir.

    `mode != "subscribe"` KARŞILAŞTIRMASI bu kapıya TAKILMAZ ve takılmamalı:
    operandı (`mode`) gizli DEĞİL, gövdesi sabit bir dizedir.
    """
    _sabit_sureli_kapi(UC, "dogrula", {"token", "verify_token"})


def test_GOVDE_SINIRI_UCA_OZEL_ve_AYRISTIRMADAN_ONCE() -> None:
    """256 KiB, küresel 2 MiB sınırından BAĞIMSIZ ve JSON'dan ÖNCE.

    MUTASYON: sınırı kaldırmak bunu KIRMIZI yapar. Küresel sınırı (2 MiB)
    bu uç için daraltmak da çare DEĞİLDİ: aynı ayarı içe aktarma uçları
    kullanıyor ve onlar 10 MiB Excel akıtıyor.
    """
    from app.config import settings
    from app.routers.whatsapp import GOVDE_MAKS_BAYT

    assert GOVDE_MAKS_BAYT == 256 * 1024
    assert settings.max_request_body_bytes > GOVDE_MAKS_BAYT

    agac = ast.parse(UC.read_text(encoding="utf-8"))
    govde = next(
        d for d in ast.walk(agac)
        if isinstance(d, ast.AsyncFunctionDef) and d.name == "webhook"
    )
    sinir = [
        d.lineno for d in ast.walk(govde)
        if isinstance(d, ast.Name) and d.id == "GOVDE_MAKS_BAYT"
    ]
    loads = [
        d.lineno for d in ast.walk(govde)
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "loads"
    ]
    assert sinir and loads and max(sinir) < min(loads), (sinir, loads)


def test_YAZMA_YOLU_HAM_SQL_TASIMIYOR() -> None:
    """`giris.py`de `text()` YOK — kiracı nöbetçisine borç bırakılmıyor.

    Nöbetçi (`test_tenant_scoping_guard.py`) `app/` altındaki her ham SQL
    çağrısını parmak iziyle donduruyor. Bu tabloda donduracak bir KİRACI
    YÜKLEMİ yok (platform tablosu), yani ham SQL yazmak envantere gerekçesi
    olmayan bir satır eklerdi. Core `insert()` hem oraya hem Core sorgu
    envanterinin bildirilmiş kapsamına GİRMEZ.
    """
    agac = ast.parse(YAZMA.read_text(encoding="utf-8"))
    ham = [
        d for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) in {"text", "tenant_text"}
    ]
    assert not ham, ham
    yazmalar = [
        d for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "insert"
    ]
    assert len(yazmalar) == 1, yazmalar


def test_KOPYA_wamid_SAVEPOINT_ile_yalniz_KENDI_SATIRINI_geri_aliyor() -> None:
    """`begin_nested` yazma döngüsünün İÇİNDE, dışında değil.

    MUTASYON: SAVEPOINT'i kaldırmak bunu KIRMIZI yapar — ve davranışta
    "kopya + yeni birlikte" adımını düşürür: kopya `IntegrityError`ı TÜM
    transaction'ı zehirler ve aynı teslimattaki YENİ mesajlar da kaybolurdu.
    """
    agac = ast.parse(YAZMA.read_text(encoding="utf-8"))
    fn = next(
        d for d in ast.walk(agac)
        if isinstance(d, ast.FunctionDef) and d.name == "gelen_kaydet"
    )
    donguler = [d for d in ast.walk(fn) if isinstance(d, ast.For)]
    assert len(donguler) == 1, donguler
    icerde = [
        d for d in ast.walk(donguler[0])
        if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "begin_nested"
    ]
    assert icerde, "begin_nested döngünün içinde değil"


# ------------------------------------------------------- göç turu (SQLite) ---

_GOC_TURU = r'''
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

motor = sa.create_engine(settings.database_url)
yapilandirma = Config('alembic.ini')
yapilandirma.set_main_option('sqlalchemy.url', settings.database_url)

KUYRUK = 'whatsapp_inbound'
SAYAC = 'whatsapp_pairing_attempts'


def gorunen():
    return set(sa.inspect(motor).get_table_names())


command.upgrade(yapilandirma, 'head')
assert KUYRUK in gorunen(), 'yukari: kuyruk dogmadi'
assert SAYAC in gorunen(), 'yukari: sayac dogmadi'

gozlemci = sa.inspect(motor)
sutunlar = {c['name']: c for c in gozlemci.get_columns(KUYRUK)}
assert 'company_id' not in sutunlar, sorted(sutunlar)
assert 'user_id' not in sutunlar, sorted(sutunlar)
for zorunlu in ('wamid', 'sender_phone', 'phone_number_id', 'text',
                'status', 'attempt_count', 'received_at'):
    assert sutunlar[zorunlu]['nullable'] is False, zorunlu
for serbest in ('media_id', 'media_mime', 'locked_until', 'lock_token',
                'last_error', 'processed_at'):
    assert sutunlar[serbest]['nullable'] is True, serbest

tekiller = {u['name']: tuple(u['column_names'])
            for u in gozlemci.get_unique_constraints(KUYRUK)}
assert tekiller.get('uq_whatsapp_inbound_wamid') == ('wamid',), tekiller
indeksler = {i['name'] for i in gozlemci.get_indexes(KUYRUK)}
assert 'ix_whatsapp_inbound_status' in indeksler, indeksler

sayac_sutun = {c['name'] for c in gozlemci.get_columns(SAYAC)}
assert 'company_id' not in sayac_sutun, sorted(sayac_sutun)
sayac_tekil = {u['name']: tuple(u['column_names'])
               for u in gozlemci.get_unique_constraints(SAYAC)}
assert sayac_tekil.get('uq_whatsapp_pairing_attempts_pencere') == (
    'phone', 'window_start'), sayac_tekil

command.downgrade(yapilandirma, '20260909_0077')
dusen = gorunen()
assert KUYRUK not in dusen, 'asagi: kuyruk dusmedi'
assert SAYAC not in dusen, 'asagi: sayac dusmedi'

command.upgrade(yapilandirma, 'head')
assert KUYRUK in gorunen() and SAYAC in gorunen(), 'ikinci yukari: tur kapanmadi'
print('GOC TURU TAMAM')
'''


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """İki tablo doğuyor, `downgrade` ikisini de GERİ ALIYOR, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo ve indeks adlarını
    SABİTTEN okuyor ve dizge araması onu göremezdi. Daha önemlisi bu betik,
    `company_id`nin GERÇEKTEN GÖÇ EDİLMİŞ ŞEMADA olmadığını ölçüyor —
    kaynak metninde aramak, sütunu bir yardımcı fonksiyondan ekleyen bir
    değişikliği kaçırırdı.

    Bu betik SQL FİİLİ TAŞIMIYOR (yalnız alembic + `inspect`), yani alt
    süreç SQL envanterine (`tests/pins/alt_surec_sql.txt`) GİRMEZ —
    `tests/test_1b_c_ayarlama_lot.py::_TAZE_FK` ile aynı sınıf.
    """
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + (tmp_path / "goc.db").as_posix()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _GOC_TURU],
        cwd=str(BACKEND), env=ortam, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert sonuc.returncode == 0, sonuc.stdout + sonuc.stderr
    assert "GOC TURU TAMAM" in sonuc.stdout


# ------------------------------------------------------------- davranis ---

@pytest.fixture(scope="module")
def istemci():
    """Gerçek şemalı uygulama; alt süreç GEREKMİYOR.

    Ortam bu dosyanın MODÜL BAŞINDA kuruldu (gerekçe orada). `TestClient`
    bağlamı açılış olaylarını koşturur, yani göçler burada uygulanır.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def defter():
    from sqlalchemy import text

    from app.db import SessionLocal

    def oku():
        with SessionLocal() as db:
            return db.execute(text(
                "SELECT wamid, sender_phone, phone_number_id, text, media_id, "
                "media_mime, status, attempt_count, locked_until, lock_token, "
                "processed_at FROM whatsapp_inbound ORDER BY id"
            )).mappings().all()

    def temizle():
        with SessionLocal() as db:
            db.execute(text("DELETE FROM whatsapp_inbound"))
            db.commit()

    temizle()
    yield oku
    temizle()


def _metin_govdesi(*wamidler: str, pnid: str = PNID) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": pnid},
            "messages": [
                {"id": w, "from": "0540 599 59 59", "type": "text",
                 "text": {"body": "  merhaba  "}}
                for w in wamidler
            ],
        }}]}],
    }


def _imzala(govde: bytes, sir: str = SIR) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=" + hmac.new(
            sir.encode("utf-8"), govde, hashlib.sha256
        ).hexdigest(),
    }


def _gonder(istemci, yuk=None, *, sir: str = SIR, imzali: bool = True, ham: bytes | None = None):
    govde = ham if ham is not None else json.dumps(yuk).encode("utf-8")
    basliklar = _imzala(govde, sir) if imzali else {"Content-Type": "application/json"}
    return istemci.post("/api/whatsapp/webhook", content=govde, headers=basliklar)


def test_DAVRANIS_dogrulama_el_sikismasi(istemci) -> None:
    """Doğru token `hub.challenge`i AYNEN ve `text/plain` döner."""
    r = istemci.get("/api/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": JETON, "hub.challenge": "42-ABC"})
    assert r.status_code == 200, r.text
    # TIRNAKSIZ: JSON dönseydi Meta el sıkışmasını düşürürdü.
    assert r.text == "42-ABC"
    assert r.headers["content-type"].startswith("text/plain")

    # YANLIŞ TOKEN ve YANLIŞ MOD ayrı ayrı düşer.
    assert istemci.get("/api/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": JETON + "x",
        "hub.challenge": "42"}).status_code == 403
    assert istemci.get("/api/whatsapp/webhook", params={
        "hub.mode": "unsubscribe", "hub.verify_token": JETON,
        "hub.challenge": "42"}).status_code == 403


def test_DAVRANIS_HMAC_gecerli_gecersiz_ve_YOK(istemci, defter) -> None:
    """Geçerli imza yazar; geçersiz ve EKSİK imza AYNI kapıdan düşer.

    MUTASYON: `verify_signature`ı `True` döndürmeye zorlamak (ya da çağrıyı
    silmek) ikinci ve üçüncü iddiayı KIRMIZI yapar — ve o hâlde herhangi
    biri kuyruğa satır YAZDIRABİLİRDİ.
    """
    assert _gonder(istemci, _metin_govdesi("w-ok")).status_code == 200
    assert [s["wamid"] for s in defter()] == ["w-ok"]

    assert _gonder(istemci, _metin_govdesi("w-kotu"), sir="baska-sir").status_code == 403
    assert _gonder(istemci, _metin_govdesi("w-imzasiz"), imzali=False).status_code == 403
    # AYRIM SIZDIRILMIYOR: iki yol da AYNI kodu döndü ve HİÇBİRİ yazmadı.
    assert [s["wamid"] for s in defter()] == ["w-ok"]


def test_DAVRANIS_satir_RECEIVED_ve_numara_KANONIK(istemci, defter) -> None:
    """Yazılan satırın tamamı ölçülüyor: durum, numara, lease alanları.

    Numara `0540 599 59 59` olarak GELDİ ve `905405995959` olarak yazıldı:
    `normalize_phone` düşerse aynı kişinin iki yazımı iki AYRI numaraya
    düşer ve eşleştirme defteri onu bulamaz.
    """
    assert _gonder(istemci, _metin_govdesi("w-1")).status_code == 200
    (satir,) = defter()
    assert satir["wamid"] == "w-1"
    assert satir["sender_phone"] == "905405995959"
    assert satir["phone_number_id"] == PNID
    assert satir["text"] == "merhaba"
    assert satir["media_id"] is None and satir["media_mime"] is None
    assert satir["status"] == "RECEIVED"
    assert satir["attempt_count"] == 0
    # İŞÇİ YOK: lease ve sonuç alanları BOŞ kalıyor ve kalmaları bu
    # dilimin iddiasının kendisi.
    assert satir["locked_until"] is None
    assert satir["lock_token"] is None
    assert satir["processed_at"] is None


def test_DAVRANIS_KOPYA_wamid_TEK_satir_ve_kardesleri_dusurmuyor(istemci, defter) -> None:
    """İkinci teslimat 200 alır ama YENİ SATIR ÜRETMEZ.

    Üçüncü çağrı KOPYA ile YENİYİ birlikte gönderiyor: SAVEPOINT olmasaydı
    kopyanın hatası transaction'ı zehirler ve `w-yeni` de KAYBOLURDU.
    """
    assert _gonder(istemci, _metin_govdesi("w-tek")).status_code == 200
    assert _gonder(istemci, _metin_govdesi("w-tek")).status_code == 200
    assert [s["wamid"] for s in defter()] == ["w-tek"]

    assert _gonder(istemci, _metin_govdesi("w-tek", "w-yeni")).status_code == 200
    assert [s["wamid"] for s in defter()] == ["w-tek", "w-yeni"]


def test_DAVRANIS_tek_teslimatta_UC_mesaj_UC_satir(istemci, defter) -> None:
    assert _gonder(istemci, _metin_govdesi("m1", "m2", "m3")).status_code == 200
    assert [s["wamid"] for s in defter()] == ["m1", "m2", "m3"]


def test_DAVRANIS_YABANCI_phone_number_id_sessizce_eleniyor(istemci, defter) -> None:
    """İmza DOĞRU ama numara BİZİM DEĞİL: 200 döner, satır YAZILMAZ.

    Aynı Meta uygulamasındaki başka bir işletme numarasının olayı AYNI app
    secret'la imzalanır; imza tek başına "bu mesaj bize geldi" DEMEZ.

    MUTASYON: süzgeci kaldırmak bunu KIRMIZI yapar (satır yazılırdı).
    4xx döndürmek de yanlış olurdu: Meta yeniden teslimat fırtınası üretir.
    """
    r = _gonder(istemci, _metin_govdesi("w-yabanci", pnid="999999999"))
    assert r.status_code == 200, r.text
    assert defter() == []


def test_DAVRANIS_govde_SINIRI_413_ve_JSON_HIC_AYRISTIRILMIYOR(istemci, defter) -> None:
    """256 KiB üstü gövde 413; imzası DOĞRU olsa bile ayrıştırılmıyor.

    Gövde geçerli JSON DEĞİL: 400 dönseydi bu, sınırın ayrıştırmadan SONRA
    uygulandığının kanıtı olurdu.
    """
    ham = b"[" + b"x" * (256 * 1024)
    r = _gonder(istemci, ham=ham)
    assert r.status_code == 413, r.status_code
    assert defter() == []

    # SINIRIN ALTI aynı yoldan geçiyor ve 400 alıyor: kapı BOYUTU ölçüyor,
    # geçerliliği değil.
    assert _gonder(istemci, ham=b"[bozuk json").status_code == 400


def test_DAVRANIS_MEDYA_satiri_ve_DESTEKLENMEYEN_tur(istemci, defter) -> None:
    """Görsel kuyruğa KİMLİĞİYLE giriyor; ses/video HİÇ girmiyor.

    Medya İNDİRİLMİYOR: satırda yalnız Meta'daki kimliği var. İndirme
    webhook'un içinde yapılsaydı Meta'nın beklediği 2xx gecikirdi.
    """
    govde = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": PNID},
            "messages": [
                {"id": "med-1", "from": "905405995959", "type": "image",
                 "image": {"id": "MEDIA-1", "mime_type": "image/jpeg; codecs=x",
                           "caption": " fatura "}},
                {"id": "ses-1", "from": "905405995959", "type": "audio",
                 "audio": {"id": "MEDIA-2", "mime_type": "audio/ogg"}},
                {"id": "durum-1", "from": "905405995959", "type": "reaction",
                 "reaction": {"emoji": "x"}},
            ],
        }}]}],
    }
    assert _gonder(istemci, govde).status_code == 200
    (satir,) = defter()
    assert satir["wamid"] == "med-1"
    assert satir["media_id"] == "MEDIA-1"
    # "; codecs=..." ayıklanmasaydı geçerli fotoğraf ELENİRDİ.
    assert satir["media_mime"] == "image/jpeg"
    assert satir["text"] == "fatura"


def test_DAVRANIS_YAPILANDIRILMAMIS_kanal_404_ve_HICBIR_SEY_KABUL_ETMIYOR(
    istemci, defter, monkeypatch
) -> None:
    """Üç ayardan HANGİSİ boş olursa olsun iki uç da 404 ve gövde OKUNMUYOR.

    MUTASYON: `_kanal_ayari`yi gevşetmek (ör. yalnız `app_secret` yeterli
    saymak) bunu KIRMIZI yapar. Yarı açık bir kanal, üç güvenceden
    hangisinin yürürlükte olduğunu SORULAMAZ hâle getirirdi.

    403 DEĞİL 404: 403, tarayan birine kurulumun bir WhatsApp kanalı
    taşıdığını SÖYLERDİ.
    """
    from app.config import settings

    yuk = _metin_govdesi("w-kapali")
    for alan, bos in (
        ("whatsapp_app_secret", None),
        ("whatsapp_verify_token", None),
        ("whatsapp_phone_number_id", ""),
        # Yalnız BOŞLUKTAN ibaret bir sır da BOŞ sayılır: hiç kimsenin
        # üretemeyeceği bir imzayla korunuyor sanmak olurdu.
        ("whatsapp_phone_number_id", "   "),
    ):
        monkeypatch.setattr(settings, alan, bos)
        r = istemci.get("/api/whatsapp/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": JETON,
            "hub.challenge": "42"})
        assert r.status_code == 404, (alan, r.status_code)
        # İmZA DOĞRU olmasına rağmen: kapı en dışta.
        assert _gonder(istemci, yuk).status_code == 404, alan
        monkeypatch.undo()

    assert defter() == []
    # Ayarlar geri geldiğinde uç YİNE ÇALIŞIYOR — kapı kalıcı değil.
    assert _gonder(istemci, yuk).status_code == 200
