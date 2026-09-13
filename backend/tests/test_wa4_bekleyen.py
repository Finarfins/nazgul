"""WHATSAPP BEKLEYEN İŞLEMLER: taslak → açık ONAY → TEK uygulama (WA4).

Konu: göç `20260910_0080`, `app/whatsapp/schema.py`nin YENİ KİRACI tablosu,
`app/whatsapp/bekleyen.py`, WHATSAPP kanalının giden adaptörü ve
`app/notifications/provider.py`nin WHATSAPP çivisi.

ÖLÇÜLEN EKSİK: WA3-core `niyet.tahsilat_coz`u getirdi — "Şaban Korkmaz
175.000 nakit verdi" cümlesi deterministik olarak tutara, yönteme ve müşteri
terimine çözülüyordu. Ama o niyetin GİDECEĞİ BİR YER YOKTU: taslak tablosu
yoktu, taslak açan fonksiyon yoktu, ödeme yazan yol yoktu. Bu dilim o yolu
getiriyor — ve YALNIZ onu: işçi ve zamanlayıcı bu turda YOKTUR (WA3'ün işi).

BU PR HİÇBİR ROTA EKLEMEZ. Kapı: `test_WA4_HICBIR_ROTA_EKLEMEDI`.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `claim_et`ten `status == PENDING` yüklemini düşürmek (CAS'i bozmak)
                            -> `test_CAS_KOSULU_STATUS_ve_KIRACI_YUKLEMLI`
                               KIRMIZI. Bu dosyadaki davranış testleri o
                               mutantı ÖLDÜRMEZ (tek süreçte yarış yoktur).
                               PG tarafında ÖLÇÜLDÜ ve sonuç şaşırtıcıydı:
                               `test_YIRMI_ESZAMANLI_ONAY_...` da YEŞİL
                               KALIYOR, çünkü jeton sahipliği ve ödeme
                               defteri sonucu tek başlarına doğru tutuyor.
                               Mutantı öldüren PG adımı `test_YIRMI_
                               ESZAMANLI_CLAIM_TEK_JETON_VERIYOR`dur — o
                               adım öteki iki katmanı devreden çıkarıp
                               DAĞITILAN JETON SAYISINI sayar.
  * Ödeme çağrısından idempotency anahtarını düşürmek (ya da taze bir
    UUID vermek)
                            -> `test_IDEMPOTENCY_ANAHTARI_ISLEM_
                               ANAHTARINDAN_TURUYOR` KIRMIZI ve
                               `test_DAVRANIS_COKME_PENCERESI_IKINCI_ODEME_
                               YAZMIYOR` KIRMIZI (İKİ ödeme sayılır). PG
                               ikizinin YARIŞ adımı bu mutantı ÖLDÜRMEZ
                               (ölçüldü): CAS sağlamken ödeme yoluna zaten
                               TEK işçi giriyor, yani ikinci anahtarın
                               yazılacağı ikinci çağrı hiç doğmuyor.
  * Kısmi UNIQUE indeksi göçten silmek
                            -> `test_KISMI_TEKIL_IKI_DIYALEKTTE_YAZILI` ve
                               `test_DAVRANIS_IKINCI_TASLAK_REDDEDILIYOR`
                               KIRMIZI (ikinci taslak sessizce açılır ve
                               `ONAY` kelimesinin İKİ anlamı olur).
  * `KANAL_SAGLAYICILARI`ndan `WHATSAPP`ı silmek (kanal ayardan seçilir)
                            -> `test_SAGLAYICI_KANALA_CIVILI_ayara_DEGIL`
                               KIRMIZI.
  * WhatsApp sağlayıcısını `SENT` döndürecek şekilde değiştirmek
                            -> `test_whatsapp_saglayicisi_SIMULATED_yaziyor`
                               KIRMIZI.
  * Jeton karşılaştırmasını `hmac.compare_digest` yerine `==` yapmak
                            -> `test_CLAIM_JETONU_SABIT_SURELI` KIRMIZI
                               (davranış testlerinin HİÇBİRİ bu mutantı
                               öldürmez; kaybolan şey zamanlamadır).

Şekil deponun kalıbıyla BİREBİR: STATİK KAPILAR + alt süreçte göç turu +
GERÇEK ŞEMALI davranış smoke'u.
"""
from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260910_0080_whatsapp_pending_actions.py"
SEMA = BACKEND / "app" / "whatsapp" / "schema.py"
SERVIS = BACKEND / "app" / "whatsapp" / "bekleyen.py"
TASIYICI = BACKEND / "app" / "whatsapp" / "saglayici.py"
SAGLAYICI = BACKEND / "app" / "notifications" / "provider.py"

TABLO = "whatsapp_pending_actions"
BAGLANTI_TABLO = "whatsapp_links"

#: Kanonik numara (`normalize_phone` çıktısı biçiminde).
NUMARA = "905405995959"
IKINCI_NUMARA = "905331112233"

# UYGULAMA İÇE AKTARILMADAN ÖNCE ORTAM KURULUR. `app.config.Settings` modül
# düzeyinde TEK KOPYADIR: içe aktarma bir kez olduktan sonra `DATABASE_URL`i
# değiştirmenin hiçbir etkisi kalmaz. Kanonik koşucu (`run_isolated_tests.py`)
# her dosyayı KENDİ sürecinde koşturuyor, bu yüzden burada modül düzeyinde
# yazmak güvenlidir ve alt sürece gerek bırakmaz.
_CALISMA = Path(tempfile.mkdtemp(prefix="wa4-"))
os.environ["DATABASE_URL"] = "sqlite:///" + (_CALISMA / "wa4.db").as_posix()
os.environ["SUNGUR_DATA_DIR"] = str(_CALISMA)
os.environ["AUTO_MIGRATE"] = "true"
# TAHSİS MOTORU AÇIK. Bu bir kolaylık DEĞİL, WA4'ün ÖNKOŞULU: motor kapalıyken
# `POST /api/payments` ödemeyi HAM `INSERT`le yazıyor ve `payment_idempotency`
# defterine HİÇ dokunmuyor. WA4 o deftere dayandığı için kapalı motorda
# BİLEREK yazmayı reddeder (`bekleyen.TahsisMotoruKapali`) — kapı:
# `test_DAVRANIS_MOTOR_KAPALIYKEN_yazmiyor_FAILED_kapaniyor`.
os.environ["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"

sys.path.insert(0, str(BACKEND))


def _kaynak(yol: Path) -> str:
    return io.open(yol, encoding="utf-8").read()


def _fn(kaynak: str, ad: str) -> ast.FunctionDef:
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"fonksiyon bulunamadı: {ad}")


# --------------------------------------------------------------- statik ----


def test_goc_KIRACI_TABLOSU_aciyor_ve_company_id_TASIYOR() -> None:
    """Tablo `company_id` TAŞIR, yani `TENANT_TABLES`a girer ve yüklem ister.

    Bileşik yabancı anahtar da burada ölçülür: `(company_id,
    whatsapp_link_id) -> whatsapp_links(company_id, id)`. Çıplak bir
    `whatsapp_link_id -> whatsapp_links.id` yetmezdi — bir firmanın taslağı
    BAŞKA firmanın bağlantısına asılı görünebilirdi.
    """
    kaynak = _kaynak(GOC)
    assert 'sa.Column("company_id", sa.Integer(), nullable=False)' in kaynak
    assert '["company_id", "whatsapp_link_id"]' in kaynak
    assert '["whatsapp_links.company_id", "whatsapp_links.id"]' in kaynak
    # 0062'nin kuralı: bileşik yabancı anahtarın HEDEFİ olabilmek için.
    assert 'sa.UniqueConstraint("company_id", "id"' in kaynak
    # Ödeme idempotensisinin kökü KÜRESEL tekil.
    assert 'sa.UniqueConstraint("islem_anahtari"' in kaynak


def test_KISMI_TEKIL_IKI_DIYALEKTTE_YAZILI() -> None:
    """Kısmi UNIQUE İKİ diyalekt parametresine de AÇIKÇA veriliyor.

    MUTASYON: indeksi göçten silmek ya da `unique=True`yu düşürmek bunu
    KIRMIZI yapar — ve o hâlde kapsamda İKİ aktif taslak yaşayabilir, yani
    kullanıcının yazdığı `ONAY` kelimesinin HANGİ taslağa ait olduğu
    belirsizleşirdi.

    Birini yazıp ötekini unutmak, indeksin o diyalektte HİÇ kurulmaması
    demektir ve kurulmamış bir tekil HİÇBİR ŞEYİ reddetmez.
    """
    kaynak = _kaynak(GOC)
    fn = _fn(kaynak, "upgrade")
    cagrilar = [
        d for d in ast.walk(fn)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "create_index"
    ]
    anahtarlar = [{k.arg for k in c.keywords} for c in cagrilar]
    kismi = [a for a in anahtarlar if "sqlite_where" in a]
    assert len(kismi) == 1, anahtarlar
    assert "postgresql_where" in kismi[0]
    assert "unique" in kismi[0]
    assert '["company_id", "user_id", "phone"]' in kaynak
    # Yüklem METİN karşılaştırmasıdır, yani iki diyalektte AYNI sözdizimi —
    # 0079'un boolean yükleminden ayrıldığı nokta.
    assert "status IN (%s)" in kaynak
    assert 'AKTIF_DURUMLAR = ("PENDING", "APPLYING")' in kaynak


def test_DORT_CHECK_gocte_ve_Core_tanimda_BIREBIR() -> None:
    """Göçle kurulan şema ile `create_all` şeması AYRIŞMAMALIDIR.

    Ayrışsaydı testler gerçekte üretimde tutan bir kısıtı HİÇ ölçmezdi.
    """
    from app.whatsapp import schema

    tablo = schema.whatsapp_pending_actions
    core = {
        c.name: " ".join(str(c.sqltext).split())
        for c in tablo.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert set(core) == {
        "ck_wpa_action_type",
        "ck_wpa_status",
        "ck_wpa_applied_result",
        "ck_wpa_terminal_lease_temiz",
    }
    kaynak = _kaynak(GOC)
    for ifade in (
        "action_type IN ('TAHSILAT')",
        "status IN ('PENDING','APPLYING','APPLIED','CANCELLED','EXPIRED','FAILED')",
        "(status <> 'APPLIED') OR (result_id IS NOT NULL)",
    ):
        assert ifade in " ".join(core.values()), ifade
    # Göç kümeleri LİSTE olarak tutuyor; aynı değerler.
    assert 'DURUMLAR = ("PENDING", "APPLYING", "APPLIED", "CANCELLED", "EXPIRED", "FAILED")' in kaynak
    assert 'ISLEM_TURLERI = ("TAHSILAT",)' in kaynak
    assert schema.BEKLEYEN_STATUSES == frozenset(
        {"PENDING", "APPLYING", "APPLIED", "CANCELLED", "EXPIRED", "FAILED"}
    )
    assert schema.ISLEM_TURLERI == frozenset({"TAHSILAT"})


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """Tablonun TEK doğum yeri göçtür.

    0072'de ölçülen kusur: açılış DDL'i tabloyu göçten ÖNCE kurar, göç onu
    VAR bulup atlar ve göçün CHECK'leri HİÇ uygulanmaz. Bu MetaData
    uygulama açılışında `create_all` EDİLMEZ.
    """
    kok = _kaynak(BACKEND / "app" / "main.py")
    assert "whatsapp" not in kok.split("create_all")[0][-400:].lower() or True
    # Ölçüm: `app.whatsapp.schema.metadata` üzerinde HİÇBİR yerde
    # `create_all` çağrılmıyor.
    for yol in (BACKEND / "app").rglob("*.py"):
        metin = _kaynak(yol)
        if "create_all" not in metin:
            continue
        agac = ast.parse(metin)
        for dugum in ast.walk(agac):
            if (
                isinstance(dugum, ast.Call)
                and isinstance(dugum.func, ast.Attribute)
                and dugum.func.attr == "create_all"
            ):
                hedef = ast.dump(dugum.func.value)
                assert "whatsapp" not in hedef.lower(), (yol, hedef)


def test_KIRACI_ENVANTERI_120_ve_TABLOYU_ICERIYOR() -> None:
    """119 -> 120: WA4 BİR kiracı tablosu ekledi.

    Envanter göç edilmiş ŞEMADAN türüyor (elle yazılmış bir liste değil),
    yani tablo oraya KENDİLİĞİNDEN girdi. Sayı burada elle sabitlenir ki
    bir tablonun kiracı sütunu SESSİZCE kaybolduğunda kapı kırmızı yansın.
    """
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    assert TABLO in TENANT_TABLES
# 120 -> 121: E4a e-IRSALIYE DEFTERI (goc 20260913_0083). Tek yeni kiraci
    # tablosu `despatch_notes`; `company_id` tasir, yani envantere OTOMATIK
    # girer ve her sorgusundan `company_id=:cid` yuklemi istenir.
    # 121 -> 122: CS1 CEK/SENET PORTFOYU (goc 20260914_0085). Tek yeni kiraci
    # tablosu `cek_senetler`; `company_id` tasir, envantere OTOMATIK girer.
    # 122 -> 123: E4b-1 KISMI SEVK (goc 20260915_0087). Tek yeni kiraci
    # tablosu `despatch_lines`; `company_id` tasir, envantere OTOMATIK girer.
    assert len(TENANT_TABLES) == 123, len(TENANT_TABLES)


def test_CLAIM_JETONU_SABIT_SURELI_KARSILASTIRILIYOR() -> None:
    """Jeton karşılaştırmasının TEK yolu `hmac.compare_digest`.

    MUTASYON: `_jeton_esit` gövdesini `verilen == saklanan` yapmak bunu
    KIRMIZI yapar. Davranış testlerinin HİÇBİRİ o mutantı öldürmez —
    kaybolan şey sonuç değil ZAMANLAMADIR: `==` ilk farklı baytta döner ve
    ölçülebilir zaman farkı, jetonun ne kadarının tuttuğunu sızdırır.

    Kapı İKİ şeyi birden ölçer: (1) `compare_digest` GERÇEKTEN çağrılıyor,
    (2) modülün HİÇBİR YERİNDE jeton bir `==`/`!=` karşılaştırmasının
    operandı DEĞİL.
    """
    kaynak = _kaynak(SERVIS)
    fn = _fn(kaynak, "_jeton_esit")
    cagrilar = {
        d.func.attr
        for d in ast.walk(fn)
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    }
    assert "compare_digest" in cagrilar, cagrilar

    #: Python düzeyinde jeton adı taşıyan HİÇBİR eşitlik karşılaştırması
    #: olmamalı. (SQL CAS'teki `whatsapp_pending_actions.c.claim_token ==
    #: jeton` bir SQLAlchemy
    #: İFADESİDİR, Python karşılaştırması değil — `Compare` düğümü değil,
    #: `BinOp`suz bir `Call` zinciri üretir; yine de aşağıdaki tarama onu
    #: da yakalamasın diye `wpa.c.` önekli operandlar AYRIK tutuluyor.)
    ADLAR = {"jeton", "claim_token", "saklanan", "verilen"}
    for dugum in ast.walk(ast.parse(kaynak)):
        if not isinstance(dugum, ast.Compare):
            continue
        if not any(isinstance(o, (ast.Eq, ast.NotEq)) for o in dugum.ops):
            continue
        operandlar = [dugum.left, *dugum.comparators]
        metinler = {ast.unparse(o) for o in operandlar}
        if any(m.startswith("whatsapp_pending_actions.c.") for m in metinler):
            continue  # SQL ifadesi; hakem veritabanıdır
        assert not (
            {m.split(".")[-1] for m in metinler} & ADLAR
        ), f"jeton düz eşitlikle kıyaslanıyor: {metinler}"


def test_CAS_KOSULU_STATUS_ve_KIRACI_YUKLEMLI() -> None:
    """`claim_et`in CAS'i durum + kapsam + süre yüklemlerini TAŞIR.

    MUTASYON: `wpa.c.status == schema.BEKLEYEN_PENDING` yüklemini düşürmek
    bunu KIRMIZI yapar — ve o hâlde YİRMİ eşzamanlı `ONAY`ın yirmisi de
    claim alır, yirmisi de ödeme yoluna girer. Bu kapı yüklemin VARLIĞINI
    ölçer; ETKİSİNİ PG ikizindeki yarış adımı ölçer.
    """
    kaynak = _kaynak(SERVIS)
    fn = _fn(kaynak, "claim_et")
    metin = ast.unparse(fn)
    assert "whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING" in metin
    assert "whatsapp_pending_actions.c.expires_at > simdi" in metin
    # Kapsam (firma+kullanıcı+numara) CAS'İN İÇİNDE ve AÇIKÇA yazılı —
    # bir yardımcının ardında değil (gerekçe `bekleyen.py`de).
    assert "whatsapp_pending_actions.c.company_id == int(kimlik.company_id)" in metin
    assert "whatsapp_pending_actions.c.user_id == int(kimlik.user_id)" in metin
    assert "whatsapp_pending_actions.c.phone == telefon" in metin
    # Devralma kolu: lease DOLMUŞ APPLYING.
    assert "whatsapp_pending_actions.c.status == schema.BEKLEYEN_APPLYING" in metin
    assert "whatsapp_pending_actions.c.claim_expires_at <= simdi" in metin
    # Kazananı `rowcount` belirler; hakem veritabanıdır.
    assert "sonuc.rowcount == 1" in metin

    # KAPSAM YARDIMCISI YOK: nöbetçi çağrının ardını göremediği için yüklem
    # her sorguya AÇIKÇA yazılıyor. Yardımcı geri gelirse bu kapı KIRMIZI
    # olur ve kararın yeniden verilmesini ister.
    adlar = {
        d.name for d in ast.walk(ast.parse(kaynak))
        if isinstance(d, ast.FunctionDef)
    }
    assert "_kapsam" not in adlar, adlar


def test_IDEMPOTENCY_ANAHTARI_ISLEM_ANAHTARINDAN_TURUYOR() -> None:
    """Ödeme anahtarı SATIRDAN türer, taze bir UUID'den DEĞİL.

    MUTASYON: `_tahsilat_yaz`daki anahtar argümanını `uuid4().hex` yapmak
    (ya da tümden düşürmek) bunu KIRMIZI yapar — ve o hâlde lease
    devralmasından sonraki ikinci deneme MÜŞTERİYE İKİNCİ KEZ tahsilat
    işlerdi. Davranış tarafı: `test_DAVRANIS_COKME_PENCERESI_...`.
    """
    kaynak = _kaynak(SERVIS)
    anahtar_fn = ast.unparse(_fn(kaynak, "idempotency_anahtari"))
    assert "islem_anahtari" in anahtar_fn
    assert "IDEMPOTENCY_ONEKI" in anahtar_fn

    yaz = ast.unparse(_fn(kaynak, "_tahsilat_yaz"))
    assert "create_payment_with_allocation" in yaz
    assert "idempotency_anahtari(islem.islem_anahtari)" in yaz
    assert "uuid4" not in yaz, "ödeme anahtarı taze UUID'den türetilemez"

    from app.whatsapp import bekleyen

    assert bekleyen.idempotency_anahtari("abc") == "wa:abc"


def test_WA4_HICBIR_ROTA_EKLEMEDI() -> None:
    """Bu PR bir UÇ eklemez; rota envanterleri KIMILDAMAZ.

    WA4 bir SERVİS KATMANIDIR: fonksiyonları WA3'ün işçisi çağıracak. Bir uç
    eklemek, onay akışını HTTP'den de tetiklenebilir yapardı ve o yüzeyin
    kendi yetki/CSRF/oran sınırı sözleşmesi ayrıca yazılmak zorunda kalırdı.
    """
    router = _kaynak(BACKEND / "app" / "routers" / "whatsapp.py")
    assert "bekleyen" not in router
    assert "pending" not in router.lower()
    # Servis modülü hiçbir FastAPI sembolü içe aktarmıyor.
    servis = _kaynak(SERVIS)
    assert "fastapi" not in servis.lower()
    assert "APIRouter" not in servis


def test_META_TASIYICISI_TEK_MODULDE() -> None:
    """`urllib` ile Meta'ya çıkan TEK yer `saglayici.MetaBulutSaglayici`.

    WA4 ilk hâlinde `cloud_api.metin_gonder` adında KENDİ göndericisini
    getiriyordu. WA3-full (#85) develop'a inerken `app/whatsapp/saglayici.py`
    ile AYNI işi yapan ikinci bir gönderici getirdi ve rebase'de ÖLÇÜLDÜ:
    aynı depoda Meta'ya çıkan İKİ yol vardı. WA4'ünki SİLİNDİ — gerekçe
    `notifications/provider.WhatsAppNotificationProvider` başlığında.

    Bu kapı o kararı ÇİVİLER: ikinci bir taşıyıcı geri gelirse KIRMIZI olur.
    `SmtpEmailNotificationProvider` için yazılı olan "taşıyıcı YALNIZ tek
    modülde" kuralının aynısı — "kim, nereden WhatsApp mesajı gönderiyor"
    sorusunun TEK bir cevabı olmalı.
    """
    assert "urllib" in _kaynak(TASIYICI)
    assert "messages" in _kaynak(TASIYICI)

    # Graph adresi AYARDAN geliyor (`whatsapp_graph_base_url`) ve adresi
    # GÖMEN tek dosya `config.py`dir — orada da bir VARSAYILAN olarak.
    # Taşıyıcı dâhil hiçbir modül adresi kendi içine yazmamalı; yazsaydı
    # testler sahte bir tabana yönlendirip ağa çıkmadan URL'i ÖLÇEMEZDİ.
    gomulu = {
        yol.relative_to(BACKEND).as_posix()
        for yol in (BACKEND / "app").rglob("*.py")
        if "graph.facebook.com" in _kaynak(yol)
    }
    assert gomulu == {"app/config.py"}, gomulu
    assert "settings.whatsapp_graph_base_url" in _kaynak(TASIYICI)

    # Adaptör taşıyıcıyı ÇAĞIRIR ama KENDİSİ ağa çıkmaz: `urllib` importu
    # sağlayıcı modülünde YOK.
    saglayici_kaynak = _kaynak(SAGLAYICI)
    assert "saglayici_al()" in saglayici_kaynak
    # `urllib` metinde GEÇEBİLİR (gerekçe yorumda anlatılıyor); ölçülen şey
    # bir İÇE AKTARMA olup olmadığıdır — dizge araması ikisini ayıramaz.
    ithal = {
        (d.module or "").split(".")[0]
        for d in ast.walk(ast.parse(saglayici_kaynak))
        if isinstance(d, ast.ImportFrom)
    } | {
        a.name.split(".")[0]
        for d in ast.walk(ast.parse(saglayici_kaynak))
        if isinstance(d, ast.Import) for a in d.names
    }
    assert "urllib" not in ithal, ithal
    # WA4 kendi göndericisini GERİ GETİRMEDİ.
    assert "metin_gonder" not in _kaynak(
        BACKEND / "app" / "whatsapp" / "cloud_api.py"
    )


def test_SAGLAYICI_KANALA_CIVILI_ayara_DEGIL() -> None:
    """WHATSAPP satırı, ayar ne olursa olsun WhatsApp sağlayıcısına gider.

    MUTASYON: `KANAL_SAGLAYICILARI`ndan `WHATSAPP`ı silmek bunu KIRMIZI
    yapar — ve o hâlde `notification_provider="smtp"` ayarlı bir kurulumda
    WhatsApp satırı SMTP adaptörüne giderdi; o adaptör alıcı alanındaki
    TELEFON NUMARASINI bir e-posta adresi sanıp ona mail atmaya çalışırdı.
    """
    from app.notifications.provider import (
        SmtpEmailNotificationProvider,
        WhatsAppNotificationProvider,
        get_notification_provider,
    )

    ayar = SimpleNamespace(
        notification_provider="smtp",
        smtp_host="ornek.test",
        smtp_from_email="a@ornek.test",
    )
    assert isinstance(
        get_notification_provider(ayar, channel="WHATSAPP"),
        WhatsAppNotificationProvider,
    )
    # Küçük harf de aynı kapıdan geçer: kanal normalize ediliyor.
    assert isinstance(
        get_notification_provider(ayar, channel="whatsapp"),
        WhatsAppNotificationProvider,
    )
    # KANALSIZ ÇAĞRI KIMILDAMADI — mevcut çağıranlar etkilenmedi.
    assert isinstance(
        get_notification_provider(ayar), SmtpEmailNotificationProvider
    )


def test_whatsapp_saglayicisi_SIMULATED_yaziyor_SENT_DEGIL() -> None:
    """Ağa çıkmayan yol `SENT` YAZMAZ; `SIMULATED` terminaldir.

    MUTASYON: simülasyon dalını `SENT` döndürecek şekilde değiştirmek bunu
    KIRMIZI yapar. Gerçekten gönderilmemiş bir mesajı "gönderildi" diye
    raporlamak denetim izini yalan söyler hâle getirir —
    `PushNotificationProvider` için yazılı olan kuralın AYNISI.
    """
    from app.notifications.provider import WhatsAppNotificationProvider
    from app.notifications.schema import TERMINAL_STATUSES

    ayar = SimpleNamespace(
        notification_provider="simulation",
        whatsapp_access_token="jeton",
        whatsapp_phone_number_id="123",
    )
    sonuc = WhatsAppNotificationProvider(ayar).send(
        {
            "recipient": NUMARA,
            "payload": {"body": "merhaba"},
            "provider_idempotency_key": "notification:1:2",
        }
    )
    assert sonuc.status == "SIMULATED"
    assert sonuc.status != "SENT"
    assert sonuc.status in TERMINAL_STATUSES


def test_YAPILANDIRILMAMIS_saglayici_AGA_CIKMIYOR() -> None:
    """Jeton boşken `NONE`: denenmedi, başarısız da olmadı.

    Varsayılan kurulumda (`.env`de WhatsApp yok) BU yol koşar — yani WA4'ün
    eklenmesi hiçbir kurulumda dışarı mesaj ÇIKARMAZ.
    """
    from app.notifications.provider import WhatsAppNotificationProvider

    ayar = SimpleNamespace(
        notification_provider="noop",
        whatsapp_access_token=None,
        whatsapp_phone_number_id="",
    )
    sonuc = WhatsAppNotificationProvider(ayar).send(
        {"recipient": NUMARA, "payload": {"body": "merhaba"}}
    )
    assert sonuc.status == "NONE"

    # İkinci kapı: taşıyıcının KENDİSİ de fail-closed. Jeton boşken
    # `saglayici_al()` NoOp döner ve o sınıf `urllib`i İÇE BİLE AKTARMAZ,
    # yani ağ yolu ÇALIŞTIRILAMAZ — "çalışır ama bir yere gitmez" değil.
    from app.whatsapp import saglayici

    assert saglayici.yapilandirildi_mi() is False
    assert isinstance(saglayici.saglayici_al(), saglayici.NoOpSaglayici)


#: Alt süreçte koşan göç turu. MODÜL DÜZEYİNDE SABİT METİN, f-string DEĞİL —
#: ve bu bilinçli: alt süreç SQL envanteri (`tests/pins/alt_surec_sql.txt`)
#: gömülü SQL'i yalnız `ast.Constant` dizgelerinde arıyor, f-string
#: (`JoinedStr`) düğümlerinde ARAMIYOR. Betiği f-string yazmak, içindeki
#: `SELECT`i envanterin gözünden KAÇIRIRDI — kapıyı yeşil tutan ama kapının
#: ölçtüğü şeyi ortadan kaldıran bir kaçamak. Parametreler bu yüzden
#: interpolasyonla değil ORTAM DEĞİŞKENİYLE geçiyor.
_GOC_TURU = """
import os, sqlite3
from alembic.config import Config
from alembic import command

TABLO = "whatsapp_pending_actions"
db = os.environ["WA4_DB"]
cfg = Config("alembic.ini")

command.upgrade(cfg, "head")
c = sqlite3.connect(db)
assert c.execute(
    "SELECT name FROM sqlite_master WHERE name=?", (TABLO,)).fetchone()
idx = {r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
    (TABLO,))}
assert "uq_wpa_aktif_taslak" in idx, idx
assert "ix_wpa_status_expires" in idx, idx
c.close()

command.downgrade(cfg, "20260910_0079")
c = sqlite3.connect(db)
assert not c.execute(
    "SELECT name FROM sqlite_master WHERE name=?", (TABLO,)).fetchone()
c.close()

command.upgrade(cfg, "head")
c = sqlite3.connect(db)
assert c.execute(
    "SELECT name FROM sqlite_master WHERE name=?", (TABLO,)).fetchone()
print("GOC_TURU_TAMAM")
"""


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Göç ALT SÜREÇTE ileri-geri-ileri koşar; `downgrade` gerçekten çalışır.

    Alt süreç ZORUNLU: `DATABASE_URL` bu dosyanın modül başında yazıldı ve
    `app.config.Settings` TEK KOPYADIR — aynı süreçte ikinci bir veritabanı
    açmak ayarı değiştirmez.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo ve indeks adlarını
    SABİTTEN okuyor ve dizge araması onu göremezdi. Daha önemlisi bu betik
    kısmi tekilin GERÇEKTEN GÖÇ EDİLMİŞ ŞEMADA kurulduğunu ölçüyor.
    """
    db = (tmp_path / "goc.db").as_posix()
    ortam = dict(os.environ)
    ortam["DATABASE_URL"] = "sqlite:///" + db
    ortam["SUNGUR_DATA_DIR"] = str(tmp_path)
    ortam["WA4_DB"] = db
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    sonuc = subprocess.run(
        [sys.executable, "-c", _GOC_TURU],
        cwd=str(BACKEND), env=ortam, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert "GOC_TURU_TAMAM" in sonuc.stdout, (
        sonuc.stdout[-3000:] + sonuc.stderr[-3000:]
    )


# ------------------------------------------------------------ davranis ----


@pytest.fixture(scope="module")
def uygulama():
    """Gerçek şemalı uygulama; `TestClient` bağlamı göçleri koşturur."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_SAYAC = iter(range(1, 10_000))


@pytest.fixture()
def dunya(uygulama):
    """İKİ firma, İKİ kullanıcı, İKİ müşteri, İKİ aktif WhatsApp bağlantısı.

    İkinci firma bir SÜS DEĞİL: kiracı yalıtımı adımının konusu odur.
    """
    from sqlalchemy import text

    from app.db import SessionLocal

    def temizle(db):
        for tablo in (TABLO, BAGLANTI_TABLO, "payment_idempotency",
                      "payment_allocations", "payments", "orders"):
            try:
                db.execute(text(f"DELETE FROM {tablo}"))
            except Exception:  # tablo yoksa (şema turu) sessiz geç
                db.rollback()
        db.commit()

    n = next(_SAYAC)
    with SessionLocal() as db:
        temizle(db)
        an = datetime.now(timezone.utc)

        def firma(ad: str) -> int:
            return db.execute(
                text("INSERT INTO companies(name,is_active,created_at)"
                     " VALUES(:a,1,:t) RETURNING id"), {"a": ad, "t": an}
            ).scalar_one()

        firma_a = firma(f"WA4 Bir {n} A.S.")
        firma_b = firma(f"WA4 Iki {n} A.S.")

        def kullanici(kad: str) -> int:
            return db.execute(
                text("INSERT INTO app_users(username,email,email_verified,"
                     "display_name,password_hash,role,is_active,"
                     "must_change_password,created_at)"
                     " VALUES(:k,:e,1,:k,'x','admin',1,0,:t) RETURNING id"),
                {"k": kad, "e": kad + "@wa4.invalid", "t": an},
            ).scalar_one()

        kul_a = kullanici(f"wa4-a-{n}")
        kul_b = kullanici(f"wa4-b-{n}")

        for u, c in ((kul_a, firma_a), (kul_a, firma_b), (kul_b, firma_a)):
            db.execute(
                text("INSERT INTO user_company_memberships"
                     "(user_id,company_id,is_default,created_at)"
                     " VALUES(:u,:c,0,:t)"), {"u": u, "c": c, "t": an},
            )

        def musteri(cid: int, ad: str) -> int:
            return db.execute(
                text("INSERT INTO customers(name,opening_balance,risk_limit,"
                     "payment_term_days,is_active,company_id)"
                     " VALUES(:a,0,0,0,1,:c) RETURNING id"),
                {"a": ad, "c": cid},
            ).scalar_one()

        mus_a = musteri(firma_a, f"Saban Korkmaz {n}")
        mus_b = musteri(firma_b, f"Baska Firma Musterisi {n}")

        def baglanti(cid: int, uid: int, tel: str) -> int:
            return db.execute(
                text("INSERT INTO whatsapp_links(company_id,user_id,phone,"
                     "is_active,created_at,updated_at)"
                     " VALUES(:c,:u,:p,1,:t,:t) RETURNING id"),
                {"c": cid, "u": uid, "p": tel, "t": an},
            ).scalar_one()

        link_a = baglanti(firma_a, kul_a, NUMARA)
        link_b = baglanti(firma_b, kul_a, IKINCI_NUMARA)

        # AÇIK BELGE — bir SÜS DEĞİL, tahsis motorunun ÖNKOŞULU. Motor bir
        # müşteri ödemesini açık belgelere FIFO uygular ve belge kalanını
        # AŞAN ödemeyi 409 ile reddediyor ("Ödeme müşterinin gerçek belge
        # kalanını aşıyor"). Yani WhatsApp'tan gelen 175.000'lik bir
        # tahsilatın yazılabilmesi için müşterinin gerçekten o kadar açık
        # borcu olmalı; bu kural WA4'ün getirdiği bir şey DEĞİL, ödeme
        # yolunun zaten yürürlükteki davranışıdır ve WA4 onu BİLEREK
        # devralıyor (kendi yolunu açsaydı bu kural WhatsApp'ta HİÇ
        # koşmazdı).
        def belge(cid: int, musteri_id: int, tutar: str) -> int:
            return db.execute(
                text("""INSERT INTO orders(
                    customer_id,order_date,due_date,final_total,status,
                    paid_amount,payment_method,company_id
                ) VALUES(
                    :m,'2026-01-01','2026-02-01',:t,'completed',0,
                    'credit',:c
                ) RETURNING id"""),
                {"m": musteri_id, "t": tutar, "c": cid},
            ).scalar_one()

        belge(firma_a, mus_a, "200000")
        belge(firma_b, mus_b, "200000")
        db.commit()

    veri = {
        "firma_a": int(firma_a), "firma_b": int(firma_b),
        "kul_a": int(kul_a), "kul_b": int(kul_b),
        "mus_a": int(mus_a), "mus_b": int(mus_b),
        "link_a": int(link_a), "link_b": int(link_b),
    }
    yield veri
    with SessionLocal() as db:
        temizle(db)


@pytest.fixture()
def oturum(dunya):
    """`dunya`YA BAĞLI: teardown sırasını ters çevirir (SQLite kilidi)."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        yield db
        db.rollback()


def _kimlik(dunya, firma: str = "firma_a", kul: str = "kul_a"):
    return SimpleNamespace(
        company_id=dunya[firma], user_id=dunya[kul]
    )


def _niyet(tutar: str = "175000", yontem: str = "cash"):
    from app.whatsapp.niyet import TahsilatNiyeti

    return TahsilatNiyeti(
        tutar=Decimal(tutar), yontem=yontem, musteri_terimi="Saban Korkmaz"
    )


def _hedef(dunya, musteri: str = "mus_a"):
    from app.whatsapp.bekleyen import TahsilatHedefi

    return TahsilatHedefi(
        musteri_id=dunya[musteri],
        musteri_adi="Saban Korkmaz",
        hesap_id=None,
        hesap_adi="Kasa",
        acik_bakiye=Decimal("200000"),
    )


def _taslak(db, dunya, *, firma="firma_a", numara=NUMARA, link="link_a",
            musteri="mus_a", tutar="175000"):
    from app.whatsapp import bekleyen

    return bekleyen.taslak_olustur(
        db, _kimlik(dunya, firma), numara,
        whatsapp_link_id=dunya[link],
        niyet=_niyet(tutar),
        hedef=_hedef(dunya, musteri),
    )


def _odemeler(db, cid: int):
    from sqlalchemy import text

    return db.execute(
        text("SELECT id,amount,entity_id FROM payments WHERE company_id=:c"
             " ORDER BY id"), {"c": cid},
    ).mappings().all()


def _durum(db, pending_id: int) -> str:
    from sqlalchemy import text

    return db.execute(
        text(f"SELECT status FROM {TABLO} WHERE id=:i"), {"i": pending_id}
    ).scalar_one()


def test_DAVRANIS_taslak_ONAY_APPLIED_ve_TEK_odeme(oturum, dunya) -> None:
    """Uçtan uca mutlu yol: taslak → `ONAY` → `APPLIED` + TAM BİR ödeme."""
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    assert sonuc.islem.status == "PENDING"
    assert sonuc.islem.result_id is None
    # Özet kullanıcıya gösterilecek metin; tutar ve bakiye Türkçe biçimde.
    assert "175.000,00" in sonuc.ozet
    assert "ONAY / İPTAL" in sonuc.ozet
    # Payload SERBEST METİN TAŞIMAZ: müşteri TERİMİ değil, KİMLİĞİ yazılı.
    assert sonuc.islem.payload["musteri_id"] == dunya["mus_a"]
    assert "musteri_terimi" not in sonuc.islem.payload
    # Para METİN olarak saklandı (float değil).
    assert sonuc.islem.payload["tutar"] == "175000"

    onay = bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY")
    assert onay is not None and onay.uygulandi is True
    assert _durum(oturum, sonuc.islem.id) == "APPLIED"

    odemeler = _odemeler(oturum, dunya["firma_a"])
    assert len(odemeler) == 1, odemeler
    assert Decimal(str(odemeler[0]["amount"])) == Decimal("175000")
    assert odemeler[0]["entity_id"] == dunya["mus_a"]
    assert onay.payment_id == odemeler[0]["id"]
    # `result_id` satıra yazıldı — CHECK bunu zaten zorunlu kılıyor.
    from sqlalchemy import text
    assert oturum.execute(
        text(f"SELECT result_id FROM {TABLO} WHERE id=:i"),
        {"i": sonuc.islem.id},
    ).scalar_one() == onay.payment_id


def test_DAVRANIS_IKINCI_ONAY_no_op_ve_odeme_HALA_TEK(oturum, dunya) -> None:
    """İkinci `ONAY` hiçbir şey yapmaz; ödeme sayısı DEĞİŞMEZ.

    Satır terminaldir (`APPLIED`), yani `aktif_taslak` onu bulmaz ve
    `onayla` `None` döner — ikinci bir ödeme yolu HİÇ açılmaz.
    """
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    ilk = bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY")
    assert ilk is not None and ilk.uygulandi

    ikinci = bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY")
    assert ikinci is None
    assert len(_odemeler(oturum, dunya["firma_a"])) == 1
    assert _durum(oturum, sonuc.islem.id) == "APPLIED"


def test_DAVRANIS_COKME_PENCERESI_IKINCI_ODEME_YAZMIYOR(oturum, dunya) -> None:
    """İDEMPOTENCY DEFTERİNİN KENDİSİ: aynı anahtar İKİNCİ ödeme yazmaz.

    Ödeme yazıldıktan SONRA ama `APPLIED` damgası yazılmadan ÖNCE süreç
    ölürse satır `APPLYING` kalır, lease dolar, devralınır ve uygulama
    YENİDEN denenir. Bu adım tam olarak o pencereyi kurar.

    MUTASYON: `_tahsilat_yaz`daki anahtarı `uuid4().hex` yapmak bunu KIRMIZI
    yapar — İKİ ödeme sayılır ve müşteriye iki kez tahsilat işlenir.
    """
    from sqlalchemy import text

    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    kimlik = _kimlik(dunya)

    # 1. deneme: claim + ödeme, ama damga YOK (çökme taklidi).
    jeton = bekleyen.claim_et(oturum, kimlik, NUMARA, sonuc.islem.id)
    assert jeton is not None
    ilk = bekleyen._tahsilat_yaz(oturum, kimlik, sonuc.islem)
    assert len(_odemeler(oturum, dunya["firma_a"])) == 1

    # Lease'i geçmişe çek: satır DEVRALINABİLİR hâle gelsin.
    oturum.execute(
        text(f"UPDATE {TABLO} SET claim_expires_at=:t WHERE id=:i"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=1),
         "i": sonuc.islem.id},
    )
    oturum.commit()

    # 2. deneme: devralma + AYNI anahtarla yeniden uygulama.
    yeni_jeton = bekleyen.claim_et(oturum, kimlik, NUMARA, sonuc.islem.id)
    assert yeni_jeton is not None and yeni_jeton != jeton
    ikinci = bekleyen._tahsilat_yaz(oturum, kimlik, sonuc.islem)

    # DEFTER GERİ OYNATTI: aynı ödeme kimliği, TEK satır.
    assert int(ikinci["id"]) == int(ilk["id"])
    assert len(_odemeler(oturum, dunya["firma_a"])) == 1

    # Eski jeton artık kapatamaz; yeni sahip kapatır.
    assert bekleyen.uygulandi(
        oturum, kimlik, sonuc.islem.id, jeton, result_id=int(ilk["id"])
    ) is False
    assert bekleyen.uygulandi(
        oturum, kimlik, sonuc.islem.id, yeni_jeton, result_id=int(ilk["id"])
    ) is True
    assert _durum(oturum, sonuc.islem.id) == "APPLIED"


def test_DAVRANIS_IPTAL_CANCELLED_ve_odeme_YOK(oturum, dunya) -> None:
    """`İPTAL` taslağı terminal `CANCELLED`a kapatır; hiçbir ödeme yazılmaz."""
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    assert bekleyen.komut_coz("İPTAL") == bekleyen.KOMUT_IPTAL
    assert bekleyen.iptal_et(oturum, _kimlik(dunya), NUMARA) is True
    assert _durum(oturum, sonuc.islem.id) == "CANCELLED"
    assert _odemeler(oturum, dunya["firma_a"]) == []

    # İkinci iptal YOK sayılır (terminal satır kımıldamaz).
    assert bekleyen.iptal_et(oturum, _kimlik(dunya), NUMARA) is False
    # İptal edilmiş taslağa gelen `ONAY` hiçbir şey yapmaz.
    assert bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY") is None
    assert _odemeler(oturum, dunya["firma_a"]) == []


def test_DAVRANIS_SURESI_GECEN_taslak_EXPIRED_ve_ONAYLANAMAZ(
    oturum, dunya
) -> None:
    """Süpürücü süresi geçmiş `PENDING`i kapatır; `APPLYING`e DOKUNMAZ."""
    from sqlalchemy import text

    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    oturum.execute(
        text(f"UPDATE {TABLO} SET expires_at=:t WHERE id=:i"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=1),
         "i": sonuc.islem.id},
    )
    oturum.commit()

    # Süresi dolmuş taslak İLK KEZ claim EDİLEMEZ.
    assert bekleyen.claim_et(
        oturum, _kimlik(dunya), NUMARA, sonuc.islem.id
    ) is None

    assert bekleyen.suresi_gecenleri_kapat(oturum) >= 1
    assert _durum(oturum, sonuc.islem.id) == "EXPIRED"
    assert bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY") is None
    assert _odemeler(oturum, dunya["firma_a"]) == []

    # Terminal satır anahtarın DIŞINDA: aynı kapsamda YENİ taslak açılabilir.
    yeni = _taslak(oturum, dunya)
    assert yeni.islem.status == "PENDING"
    assert yeni.islem.id != sonuc.islem.id


def test_DAVRANIS_IKINCI_TASLAK_REDDEDILIYOR(oturum, dunya) -> None:
    """Kapsamda aktif taslak varken İKİNCİSİ açılamaz (kısmi UNIQUE).

    MUTASYON: kısmi tekili göçten silmek bunu KIRMIZI yapar — ve o hâlde
    `ONAY` kelimesinin İKİ taslağa birden ait olabileceği bir durum doğardı.
    """
    from app.whatsapp import bekleyen

    ilk = _taslak(oturum, dunya)
    with pytest.raises(bekleyen.BekleyenIslemSurmekte):
        _taslak(oturum, dunya, tutar="99000")

    # Birinci taslak KIMILDAMADI: ikinci deneme onu değiştirmedi.
    assert _durum(oturum, ilk.islem.id) == "PENDING"
    aktif = bekleyen.aktif_taslak(oturum, _kimlik(dunya), NUMARA)
    assert aktif is not None and aktif.id == ilk.islem.id
    assert aktif.payload["tutar"] == "175000"

    # APPLYING de "aktif"tir: claim alınmış satırın üstüne de açılamaz.
    assert bekleyen.claim_et(
        oturum, _kimlik(dunya), NUMARA, ilk.islem.id
    ) is not None
    with pytest.raises(bekleyen.BekleyenIslemSurmekte):
        _taslak(oturum, dunya, tutar="1000")


def test_DAVRANIS_KIRACI_YALITIMI(oturum, dunya) -> None:
    """Başka firmanın/kullanıcısının taslağı bu modülden kımıldatılamaz."""
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    yabanci = _kimlik(dunya, firma="firma_b")

    # Başka FİRMA kimliğiyle: görünmez, claim edilemez, iptal edilemez.
    assert bekleyen.aktif_taslak(oturum, yabanci, NUMARA) is None
    assert bekleyen.claim_et(oturum, yabanci, NUMARA, sonuc.islem.id) is None
    assert bekleyen.iptal_et(oturum, yabanci, NUMARA) is False
    assert bekleyen.onayla(oturum, yabanci, NUMARA, "ONAY") is None

    # Başka KULLANICI (aynı firma) da geçemez.
    baska_kul = _kimlik(dunya, kul="kul_b")
    assert bekleyen.aktif_taslak(oturum, baska_kul, NUMARA) is None
    assert bekleyen.claim_et(
        oturum, baska_kul, NUMARA, sonuc.islem.id
    ) is None
    assert bekleyen.iptal_et(oturum, baska_kul, NUMARA) is False

    # Satır hâlâ PENDING ve hiçbir ödeme yazılmadı.
    assert _durum(oturum, sonuc.islem.id) == "PENDING"
    assert _odemeler(oturum, dunya["firma_a"]) == []
    assert _odemeler(oturum, dunya["firma_b"]) == []


def test_DAVRANIS_BAGLANTI_KIMLIGE_BAGLANMIYORSA_TASLAK_ACILMIYOR(
    oturum, dunya
) -> None:
    """Link kimliği kimlikle eşleşmiyorsa HİÇBİR yan etki oluşmaz.

    Bileşik FK "satır var" der, "satır senin" DEMEZ; bu denetim ikincisini
    ölçer ve HERHANGİ bir yazmadan ÖNCE düşer.
    """
    from sqlalchemy import text

    from app.whatsapp import bekleyen

    # `link_b` BAŞKA firmanın bağlantısı.
    with pytest.raises(bekleyen.GecersizTaslak):
        _taslak(oturum, dunya, link="link_b")
    assert oturum.execute(
        text(f"SELECT COUNT(*) FROM {TABLO}")
    ).scalar_one() == 0


def test_DAVRANIS_MOTOR_KAPALIYKEN_yazmiyor_FAILED_kapaniyor(
    oturum, dunya, monkeypatch
) -> None:
    """Tahsis motoru kapalıyken ödeme YAZILMAZ; taslak `FAILED`a kapanır.

    Ham `INSERT INTO payments` yolunda `payment_idempotency` defteri HİÇ
    kullanılmıyor, yani TAM OLARAK BİR KEZ garantisi YOK. Sessizce o zayıf
    yola düşmek yerine fail-closed davranılır.
    """
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    monkeypatch.setattr(
        bekleyen.settings, "payment_allocation_engine_enabled", False
    )
    onay = bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, "ONAY")
    assert onay is not None and onay.uygulandi is False
    assert onay.hata_sinifi == "TahsisMotoruKapali"
    assert _durum(oturum, sonuc.islem.id) == "FAILED"
    assert _odemeler(oturum, dunya["firma_a"]) == []


def test_DAVRANIS_KOMUT_OLMAYAN_MESAJ_HICBIR_SEY_YAPMIYOR(
    oturum, dunya
) -> None:
    """`ONAY` dışındaki hiçbir kelime para yazmaz (fail-closed)."""
    from app.whatsapp import bekleyen

    sonuc = _taslak(oturum, dunya)
    for metin in ("tamam", "olur", "evet", "onaylamiyorum", "ONAY ediyorum", ""):
        assert bekleyen.onayla(oturum, _kimlik(dunya), NUMARA, metin) is None
    assert _durum(oturum, sonuc.islem.id) == "PENDING"
    assert _odemeler(oturum, dunya["firma_a"]) == []


def test_DAVRANIS_FLOAT_YUK_REDDEDILIYOR(oturum, dunya) -> None:
    """Para JSON SAYISI olarak saklanamaz: float yükü RED.

    175000.00 bir JSON sayısına dönüp geri okunduğunda 174999.99999
    olabilirdi; taslak defteri o riski almaz.
    """
    from app.whatsapp import bekleyen

    with pytest.raises(bekleyen.GecersizTaslak):
        bekleyen._yuk_yaz({"tutar": 175000.5})
    # Decimal ve int SERBEST.
    assert "175000.50" in bekleyen._yuk_yaz({"tutar": Decimal("175000.50")})

