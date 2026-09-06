"""VETERİNER İLAÇ KATALOĞU, TEDAVİ DEFTERİ ve ARINMA (BEKLEME) KİLİTLERİ.

Konu: göç `20260908_0074`, `app/routers/herd.py` (`_arinma_*`, `_katalog_arinma`,
`_sut_guvenlik_dogrula`, `_et_guvenlik_dogrula`, katalog ve tedavi uçları),
`app/herd_schemas.py`, `app/routers/companies.py`, `app/auth.py`,
`app/activity_log.py`.

ÖLÇÜLEN EKSİK: hayvancılık modülünde arınma süresi kavramı HİÇ YOKTU. Depoda
`treatment` / `withdrawal` / `arinma` literalleri `app/routers/herd.py` ve
`app/herd_*.py` içinde SIFIR isabet veriyordu; `animal_vaccinations` bir AŞI
defteridir ve aşının kalıntı süresi yoktur. Yani antibiyotik uygulanmış bir
hayvanın sütü, sistem HİÇBİR ŞEY BİLMEDEN tanka yazılabiliyordu.

Şekil, deponun mevcut kalıbı ve `tests/test_e1b_plantback.py` ile BİREBİR:
STATİK KAPILAR + alt süreçte GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor. Sıra
kapıların sırasıyla aynı:

  * `_arinma_coz`da alanları birleştirip "en uzun süreli ilacın çiftini al"
    demek                              -> EN UZUN KAZANIR adımı KIRMIZI
  * `_katalog_arinma`da tür eşleşmesini atlayıp ilk satırı almak
                                       -> TÜRE ÖZEL SATIR adımı KIRMIZI
  * `_arinma_ihlalleri`de `<` yerine `<=` yazmak
                                       -> SINIR GÜNÜ adımı KIRMIZI
  * `_arinma_dogrula`da `block` dalını `warn` gibi davrandırmak
                                       -> POLİTİKA block adımı KIRMIZI
  * `_ARINMA_*_SORGU`dan iç sorgunun `h.company_id=:cid` yüklemini düşürmek
                                       -> ÇAPRAZ KİRACI adımı KIRMIZI
  * `create_treatment`de `catalogue_*` sütunlarını yazmamak
                                       -> KÖKEN adımı KIRMIZI
  * `_arinma_ihlalleri`de sürü yolunu (`_ARINMA_GRUP_SORGU`) atlayıp yalnız
    bireysel tedaviye bakmak           -> SÜRÜ YOLU adımı KIRMIZI
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260908_0074_vet_ilac_tedavi.py"
HERD = BACKEND / "app" / "routers" / "herd.py"


# --------------------------------------------------------------- statik ---

def test_goc_UC_TABLO_aciyor_ve_IKI_SURESI_AYRI() -> None:
    """Katalog + tedavi + kalem; süt ve et arınması AYRI SÜTUN.

    MUTASYON: iki süreyi tek `withdrawal_days` sütununa indirmek bunu KIRMIZI
    yapar. Prospektüste ikisi ayrıdır (süt 3 gün, et 28 gün olabilir) ve tek
    sütun ikisinden birini YALAN söyletirdi.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    for tablo in ("vet_drugs", "animal_treatments", "animal_treatment_items"):
        assert tablo in kaynak, tablo
    for sutun in ("milk_withdrawal_days", "meat_withdrawal_days"):
        assert sutun in kaynak, sutun
    # Reddedilen tasarım: tek süre sütunu.
    govde = kaynak.split('"""')[2]
    assert "withdrawal_days = " not in govde
    # Bileşik yabancı anahtarlar ADIYLA duruyor: çıplak anahtar çapraz kiracı
    # referansı engellemez (0062'nin kuralı).
    for fk in (
        "fk_vet_drugs_product_same_company",
        "fk_animal_treatments_animal_same_company",
        "fk_animal_treatments_group_same_company",
        "fk_ati_treatment_same_company",
        "fk_ati_product_same_company",
    ):
        assert fk in kaynak, fk
    assert "uq_vet_drugs_company_product_species" in kaynak
    # HEDEF ŞEMADA: hayvan YA DA grup, ikisi birden değil. `milk_yields`te bu
    # kural YALNIZ uygulama katmanındaydı (0049) ve uygulama katmanı elle
    # yazılmış bir INSERT'ü durdurmaz.
    assert "ck_animal_treatments_hedef" in kaynak


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Altı parça da doğuyor, `downgrade` ALTISINI DA geri alıyor, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo adlarını SABİTTEN
    okuyor ve dizge araması onu göremezdi — daha kötüsü, `drop_column`
    çağrılarının SQLite'ta GERÇEKTEN çalıştığını hiç ölçmezdi. 0071'de ölçülen
    kusur (yansıtılan CHECK düşürülmüş sütunu adıyla anıyor) yalnız gerçek bir
    turda görünür.
    """
    veritabani = tmp_path / "e2-goc.db"
    betik = _GOC_TURU % {"url": f"sqlite:///{veritabani.as_posix()}"}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-c", betik], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_GOC_TURU = r"""
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

URL = %(url)r
config = Config("alembic.ini")
motor = sa.create_engine(URL)


def durum():
    d = sa.inspect(motor)
    tablolar = set(d.get_table_names())
    def sutunlar(t):
        return {c["name"] for c in d.get_columns(t)}
    return {
        "katalog": "vet_drugs" in tablolar,
        "tedavi": "animal_treatments" in tablolar,
        "kalem": "animal_treatment_items" in tablolar,
        "sut": {"withdrawal_warning", "withdrawal_override_reason"}
               <= sutunlar("milk_yields"),
        "hareket": {"withdrawal_warning", "withdrawal_override_reason"}
                   <= sutunlar("animal_movements"),
        "firma": "herd_withdrawal_policy" in sutunlar("companies"),
    }


command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# HEDEF AÇIK YAZILDI, "-1" DEĞİL. "-1" bir GÖREL adımdır ve zincirin UCUNU
# indirir; üstüne bir göç bindiği anda bu kapı BAŞKA bir göçü ölçmeye başlar
# ve "geri alma çalışmıyor" diye kırmızı olur (0072'de tam olarak bu oldu).
command.downgrade(config, "20260908_0073")
motor.dispose(); motor = sa.create_engine(URL)
assert not any(durum().values()), durum()

command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# BAŞ TEK: göç 0074 zincire ikinci bir baş EKLEMEDİ. Baş ARTIK 0075'tir
# (E3, KARANTİNA) ve bu kapının ölçtüğü şey başın HANGİ göç olduğu değil TEK
# olduğudur; 0074 hâlâ zincirin İÇİNDE ve yukarıdaki mutlak hedefli
# `downgrade` turu onu adıyla sürüyor.
from alembic.script import ScriptDirectory
baslar = ScriptDirectory.from_config(config).get_heads()
assert tuple(baslar) == ("20260909_0075",), baslar
print("GOC TURU TAMAM")
"""


def test_check_ve_sutun_AYNI_batchte_dusuyor() -> None:
    """0071'in dersi: SQLite'ta yansıtılan CHECK, düşürülmüş sütunu adıyla anar.

    `herd_withdrawal_policy` düşürülürken CHECK'i AYNI batch'te ÖNCE düşmeli;
    ayrı çağrılara bölünürse `downgrade` `OperationalError` verir.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    asagi = kaynak[kaynak.index("def downgrade"):]
    batch = asagi[asagi.index("batch_alter_table(FIRMA)"):]
    kisit = batch.index("drop_constraint")
    sutun = batch.index("drop_column(POLITIKA_SUTUNU)")
    assert kisit < sutun, "CHECK sütundan SONRA düşüyor"


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """0074'ün nesnelerinden HANGİLERİ açılış DDL'inde de bildiriliyor.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu): `app/tenancy.py` `companies`i
    `Table()` olarak bildiriyor ve uygulamanın AÇILIŞI o tabloyu alembic'ten
    ÖNCE kurabiliyor. Sütun bildirime eklendiği için göç onu VAR bulup tek
    `if` dalını ATLADI ve CHECK HİÇ KURULMADI — göç yeşil bitti, kısıt yoktu.

    Bu kapı o sınıfı ADIYLA çiviliyor:

    * `companies` açılışta bildiriliyor (bu bir OLGU, kusur değil), bu yüzden
      göç sütunu ve CHECK'i AYRI AYRI sormak ZORUNDA — kapı göçün kaynağında
      o ayrımı arıyor.
    * Öteki BEŞ nesnenin tabloları (`vet_drugs`, `animal_treatments`,
      `animal_treatment_items`, `milk_yields`, `animal_movements`) HİÇBİR
      açılış bildiriminde YOK, yani onların TEK yaratıcısı göçtür ve aynı
      kusur onlarda ÜRETİLEMEZ. Biri bir gün açılış DDL'ine girerse bu kapı
      kırmızı olur ve o göçün de aynı ayrımı yapması gerektiği İNCELEMEYE
      zorlanır.
    """
    import re

    acilis = ""
    for modul in ("tenancy.py", "core_schema.py", "auth.py", "inventory.py",
                  "finance_engine.py", "workflow.py"):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))

    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    for tablo in ("vet_drugs", "animal_treatments", "animal_treatment_items",
                  "milk_yields", "animal_movements"):
        assert tablo not in bildirilen, (
            "%s açılış DDL'ine girmiş; göç 0074 onu VAR bulup atlayabilir "
            "(companies'te ölçülen kusurun aynısı)" % tablo
        )

    goc = GOC.read_text(encoding="utf-8")
    assert "sutun_eksik" in goc and "check_eksik" in goc, (
        "companies dalı sütun ve CHECK'i tek koşulda soruyor; açılış DDL'i "
        "sütunu kurduğunda CHECK SESSİZCE kurulmaz"
    )


def test_arinma_politikasinda_allow_seviyesi_YOK() -> None:
    """0048/0064/0072 ile AYNI sınır: kontrolü tamamen kapatan bir seviye YOK.

    MUTASYON: `CompanyPolicyUpdate`e `"allow"` eklemek bunu KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.companies import CompanyPolicyUpdate

    def seviyeler(alan: str) -> set[str]:
        annotation = CompanyPolicyUpdate.model_fields[alan].annotation
        bulunan: set[str] = set()
        yigin = [annotation]
        while yigin:
            item = yigin.pop()
            for arg in getattr(item, "__args__", ()):
                if isinstance(arg, str):
                    bulunan.add(arg)
                else:
                    yigin.append(arg)
        return bulunan

    assert seviyeler("herd_withdrawal_policy") == {"warn", "require_reason", "block"}
    assert "allow" not in seviyeler("herd_withdrawal_policy")
    # KARDEŞ KAPI: tarla seviyeleri de KIMILDAMADI.
    assert seviyeler("farm_plantback_policy") == {"warn", "require_reason", "block"}


def test_koken_sozlugu_PHI_ile_AYNI_DEGERLERI_kullaniyor() -> None:
    """İkinci bir sözlük uydurmak denetçiye aynı olguyu iki dilde okuturdu.

    MUTASYON: `_ARINMA_KOKEN_*` sabitlerine `"VET"` gibi hayvancılığa özel bir
    değer koymak bunu KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers import farm, herd

    assert herd._ARINMA_KOKEN_KATALOG == farm._PHI_KOKEN_KATALOG == "CATALOGUE"
    assert herd._ARINMA_KOKEN_OPERATOR == farm._PHI_KOKEN_OPERATOR == "OPERATOR"
    assert (
        herd._ARINMA_KOKEN_USTUNE_YAZMA
        == farm._PHI_KOKEN_USTUNE_YAZMA
        == "OPERATOR_OVERRIDE"
    )


def test_kodda_YASAL_ARINMA_SABITI_YOK() -> None:
    """Depo hiçbir arınma süresi İDDİA ETMEZ (0063'ün duruşu).

    "Antibiyotikler için 7 gün" gibi bir varsayılan koda düşerse depo o rakamın
    SAHİBİ olur ve yanlış olduğunda sorumluluğu üstlenir. Arınma süresi YASAL
    bir süredir ve kaynağı ilacın PROSPEKTÜSÜDÜR.

    MUTASYON: `_katalog_arinma`nın boş dönüşünü `return (7, 28)` yapmak bunu
    KIRMIZI yapar.
    """
    kaynak = HERD.read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def _katalog_arinma"):kaynak.index("def _tedavi_turu")]
    for uydurma in ("7", "28", "3", "21", "10"):
        assert (
            f"return ({uydurma}" not in govde and f", {uydurma})" not in govde
        ), "kodda arınma sabiti: %s" % uydurma
    # Göç de tek satır veri YAZMIYOR.
    assert "INSERT INTO" not in GOC.read_text(encoding="utf-8")


def test_uclar_herd_izin_ailesinde_ve_TEDAVI_saglik_kapisinda() -> None:
    """İki uç ailesi de genel `read` iznine DÜŞMÜYOR; tedavi `herd.health`te.

    Tedavinin `herd.health`e bağlanması, aşı kuralının GENİŞLETİLMESİ değil
    AYNEN UYGULANMASIDIR: kural "veteriner ya da sağlık sorumlusu" diyor ve
    ilaç tedavisi aşıdan daha da açık biçimde veterinerlik işidir. Katalog
    `herd.manage`da kalır — o bir OLAY değil TANIMDIR.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/vet-drugs") == "herd.view"
    assert required_permission("POST", "/api/vet-drugs") == "herd.manage"
    assert required_permission("PUT", "/api/vet-drugs/1") == "herd.manage"
    assert required_permission("GET", "/api/animal-treatments") == "herd.view"
    assert required_permission("POST", "/api/animal-treatments") == "herd.health"
    # ÖNEK EŞLEŞMESİ ÖLÇÜLDÜ: kendi satırları olmasaydı ikisi de genel `read`e
    # düşerdi. "/api/animal-treatments" "/api/animals" önekiyle EŞLEŞMEZ.
    assert not "/api/animal-treatments".startswith("/api/animals")
    assert not "/api/vet-drugs".startswith("/api/animals")


def test_ET_KILIDI_yalniz_satis_ve_kesimde() -> None:
    """DEATH ve TRANSFER_OUT bilerek DIŞARIDA ve gerekçesi kodda YAZILI.

    * SALE / SLAUGHTER — hayvan İNSAN GIDA ZİNCİRİNE giriyor; kilit burayı
      korur.
    * DEATH — hayvan ölmüştür. Kilidi buraya koymak ölümü BİLDİRMEYİ
      zorlaştırırdı; ölüm kaydı denetimin en çok ihtiyaç duyduğu kayıttır ve
      onu caydırmak arınma süresini korumaz, defteri bozar.
    * TRANSFER_OUT — hayvan başka işletmeye gidiyor, kesime değil. Arınma
      süresi hayvanla birlikte taşınır; kesim kararını karşı taraf alır.
    * PURCHASE / TRANSFER_IN — hayvan GELİYOR, zaten kesilmiyor.

    MUTASYON: kümeyi bütün hareket türlerine açmak, aşağıdaki davranış
    smoke'unun "ÖLÜM ve NAKİL KİLİTLİ DEĞİL" adımını KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.herd_schemas import MOVEMENT_KIND
    from app.routers.herd import _ET_KILITLI_HAREKETLER

    assert _ET_KILITLI_HAREKETLER == {"SALE", "SLAUGHTER"}
    assert _ET_KILITLI_HAREKETLER < MOVEMENT_KIND
    assert {"DEATH", "TRANSFER_OUT", "PURCHASE", "TRANSFER_IN"} & _ET_KILITLI_HAREKETLER == set()


def test_arinma_sorgulari_KIRACI_BAGLI_her_halkada() -> None:
    """Kök yüklem VE iç sorgular AYRI AYRI `company_id=:cid` taşıyor.

    MUTASYON: iç sorgudan `h.company_id=:cid` yüklemini düşürmek, aşağıdaki
    ÇAPRAZ KİRACI adımını KIRMIZI yapar — komşu firmanın sürüsündeki bir
    hayvan bu firmanın sağımını kesebilirdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.herd import _ARINMA_GRUP_SORGU, _ARINMA_HAYVAN_SORGU

    for sorgu in (_ARINMA_HAYVAN_SORGU, _ARINMA_GRUP_SORGU):
        metin = str(sorgu)
        assert "t.company_id=:cid" in metin
        assert "h.company_id=:cid" in metin
    # İki sorgu da MODÜL SABİTİDİR (f-string değil): istekten gelen hiçbir
    # değer metne giremez.
    kaynak = HERD.read_text(encoding="utf-8")
    for ad in ("_ARINMA_HAYVAN_SORGU", "_ARINMA_GRUP_SORGU"):
        bas = kaynak.index(f"{ad} = text(")
        assert 'f"""' not in kaynak[bas:bas + 900]


def test_ARINMA_OLAYLARI_aktivite_katalogunda() -> None:
    """Hayvancılık modülünün kataloğa giren İLK olayları.

    `log_activity` katalog dışı bir tipi `ValueError` ile reddeder, yani
    kataloğa girmeyen bir çağrı ucu 5xx yapardı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    for tip in ("vet_drug.create", "vet_drug.update", "herd_withdrawal.overridden"):
        assert tip in ACTION_TYPES, tip
    assert {"vet_drug", "herd_withdrawal"} <= RESOURCE_TYPES


# ------------------------------------------------------------- davranış ---

def run_arinma_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_arinma_enforcement_sqlite(tmp_path: Path) -> None:
    run_arinma_smoke(f"sqlite:///{(tmp_path / 'e2-arinma.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient
from app.main import app

ADMIN_PW = 'E2Vet!123'


def admin_headers(client):
    for aday in ('admin123', ADMIN_PW):
        r = client.post('/api/auth/login',
                        json={'username':'admin','password':aday})
        if r.status_code == 200:
            break
    assert r.status_code == 200, r.text
    b = r.json()
    h = {'Authorization':'Bearer '+b['access_token'],
         'X-Company-ID':str(b['companies'][0]['id'])}
    if aday != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':aday,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h


def kural_yaz(client, h, **kurallar):
    r = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow', **kurallar})
    assert r.status_code == 200, r.text


def urun(client, h, ad):
    return client.post('/api/products', headers=h, json={
        'name':ad,'unit':'ML','sale_price':'10.00'}).json()['id']


def hayvan(client, h, kupe, tur='CATTLE', **fazla):
    r = client.post('/api/animals', headers=h, json={
        'ear_tag':kupe,'species':tur,'sex':'FEMALE', **fazla})
    assert r.status_code == 201, r.text
    return r.json()['id']


def tedavi(client, h, **govde):
    return client.post('/api/animal-treatments', headers=h, json=govde)


def sagim(client, h, gun, **fazla):
    return client.post('/api/milk-yields', headers=h, json={
        'milked_on':gun,'quantity_liters':'20', **fazla})


def hareket(client, h, aid, tur, gun, **fazla):
    return client.post('/api/animal-movements', headers=h, json={
        'animal_id':aid,'kind':tur,'moved_on':gun, **fazla})


with TestClient(app) as client:
    h = admin_headers(client)

    ayar = client.get('/api/company-settings', headers=h).json()
    assert ayar['herd_withdrawal_policy'] == 'require_reason', ayar

    # --- KATALOG ----------------------------------------------------------
    # A: bütün türler süt 3 / et 28; koyunda süt 7 / et 10.
    # B: bütün türler süt 10 / et 2.
    # C: KATALOGSUZ — çözülmez ve boş ihlal değildir.
    a = urun(client, h, 'Antibiyotik A')
    b = urun(client, h, 'Antibiyotik B')
    c = urun(client, h, 'Antibiyotik C')
    assert client.post('/api/vet-drugs', headers=h, json={
        'product_id':a,'species':'','milk_withdrawal_days':3,
        'meat_withdrawal_days':28,'route':'IM','dose_unit':'ML',
        'registration_no':'TR-1'}).status_code == 201
    assert client.post('/api/vet-drugs', headers=h, json={
        'product_id':a,'species':'SHEEP','milk_withdrawal_days':7,
        'meat_withdrawal_days':10}).status_code == 201
    assert client.post('/api/vet-drugs', headers=h, json={
        'product_id':b,'species':'','milk_withdrawal_days':10,
        'meat_withdrawal_days':2}).status_code == 201

    # AYNI ÜRÜN+TÜR İÇİN İKİNCİ SATIR REDDEDİLİYOR (uq_..._product_species).
    tekrar = client.post('/api/vet-drugs', headers=h, json={
        'product_id':a,'species':'','milk_withdrawal_days':1,
        'meat_withdrawal_days':1})
    assert tekrar.status_code == 409, tekrar.text
    # ÜRÜNSÜZ katalog satırı ŞEMADA imkânsız; uçta 422.
    assert client.post('/api/vet-drugs', headers=h, json={
        'species':'','milk_withdrawal_days':1,
        'meat_withdrawal_days':1}).status_code == 422
    # KAPALI TÜR KÜMESİ: uydurma bir tür kodu reddediliyor.
    assert client.post('/api/vet-drugs', headers=h, json={
        'product_id':c,'species':'DEVE','milk_withdrawal_days':1,
        'meat_withdrawal_days':1}).status_code == 422

    liste = client.get('/api/vet-drugs', headers=h, params={'product_id':a}).json()
    assert liste['total'] == 2, liste
    # Ürün adı BİRLEŞTİRİLEREK geliyor.
    assert liste['items'][0]['product_name'] == 'Antibiyotik A', liste

    # --- SÜRÜ ve HAYVANLAR -------------------------------------------------
    suru = client.post('/api/animal-groups', headers=h, json={
        'code':'sagmal','name':'Sağmal Sürü','species':'CATTLE'}).json()
    inek = hayvan(client, h, 'TR1000000001', group_id=suru['id'])
    inek2 = hayvan(client, h, 'TR1000000002', group_id=suru['id'])
    yalniz = hayvan(client, h, 'TR1000000003')
    koyun = hayvan(client, h, 'TR1000000004', tur='SHEEP')

    # --- 1) EN UZUN KAZANIR, İKİ ALAN BAĞIMSIZ ----------------------------
    # A: süt 3 / et 28. B: süt 10 / et 2. Beklenen: süt 10 (B), et 28 (A).
    # "En uzun süreli ilacın çiftini al" deseydi et 2 çıkardı ve 28 günlük
    # kısıt SESSİZCE kaybolurdu.
    t = tedavi(client, h, animal_id=inek, treated_on='2026-09-01',
               veterinarian='Vet Ali', diagnosis='Mastitis',
               items=[{'product_id':a,'drug_name':'A','dose':'10',
                       'dose_unit':'ML'},
                      {'product_id':b,'drug_name':'B'}])
    assert t.status_code == 201, t.text
    tj = t.json()
    assert tj['milk_withdrawal_days'] == 10, tj
    assert tj['meat_withdrawal_days'] == 28, tj
    # --- 2) KÖKEN: katalog konuştu, operatör susmuş -----------------------
    assert tj['withdrawal_source'] == 'CATALOGUE', tj
    assert tj['catalogue_milk_days'] == 10, tj
    assert tj['catalogue_meat_days'] == 28, tj
    assert len(tj['items']) == 2, tj

    # --- 3) SÜT KİLİDİ: require_reason ------------------------------------
    ihlal = sagim(client, h, '2026-09-05', animal_id=inek)
    assert ihlal.status_code == 422, ihlal.text
    d = ihlal.json()['detail']
    assert d['sebep'] == 'ARINMA_SURESI_DOLMADI', d
    assert d['blocking'][0]['withdrawal_days'] == 10, d
    assert d['blocking'][0]['earliest_allowed'] == '2026-09-11', d
    assert d['blocking'][0]['scope'] == 'ANIMAL', d

    gecti = sagim(client, h, '2026-09-05', animal_id=inek,
                  withdrawal_override_reason='veteriner onayı, süt buzağıya')
    assert gecti.status_code == 201, gecti.text
    g = gecti.json()
    assert g['withdrawal_warning'], g
    # 0048 kuralı: sistemin bulduğu ile kullanıcının söylediği AYRI sütunda.
    assert g['withdrawal_override_reason'] == 'veteriner onayı, süt buzağıya', g
    assert g['withdrawal_warning'] != g['withdrawal_override_reason'], g

    # --- 4) SINIR GÜNÜ İZİNLİ: 01 + 10 = 11 -------------------------------
    sinir = sagim(client, h, '2026-09-11', animal_id=inek)
    assert sinir.status_code == 201, sinir.text
    assert sinir.json()['withdrawal_warning'] is None, sinir.text
    # Bir gün öncesi HÂLÂ kesiyor.
    assert sagim(client, h, '2026-09-10', animal_id=inek).status_code == 422

    # --- 5) SÜRÜ YOLU: bireysel tedavi GRUP sağımını da kesiyor -----------
    # `inek` sürüde; sürünün toplu sağımı onun sütünü de içerir.
    grup_ihlal = sagim(client, h, '2026-09-05', group_id=suru['id'])
    assert grup_ihlal.status_code == 422, grup_ihlal.text
    dg = grup_ihlal.json()['detail']
    assert dg['blocking'][0]['scope'] == 'ANIMAL', dg
    assert dg['blocking'][0]['withdrawal_days'] == 10, dg

    # SÜRÜYE yazılan tedavi ise SÜRÜDEKİ HER HAYVANI kesiyor.
    ts = tedavi(client, h, group_id=suru['id'], treated_on='2026-09-20',
                items=[{'product_id':a,'drug_name':'A'}])
    assert ts.status_code == 201, ts.text
    assert ts.json()['milk_withdrawal_days'] == 3, ts.text
    bireysel = sagim(client, h, '2026-09-21', animal_id=inek2)
    assert bireysel.status_code == 422, bireysel.text
    assert bireysel.json()['detail']['blocking'][0]['scope'] == 'GROUP', bireysel.text
    # 3 gün dolunca serbest.
    assert sagim(client, h, '2026-09-23', animal_id=inek2).status_code == 201

    # SÜRÜ DIŞINDAKİ hayvan ETKİLENMİYOR.
    assert sagim(client, h, '2026-09-21', animal_id=yalniz).status_code == 201

    # --- 6) TÜRE ÖZEL SATIR: koyunda 7/10, genel 3/28 DEĞİL ---------------
    tk = tedavi(client, h, animal_id=koyun, treated_on='2026-09-01',
                items=[{'product_id':a,'drug_name':'A'}])
    assert tk.status_code == 201, tk.text
    assert tk.json()['milk_withdrawal_days'] == 7, tk.text
    assert tk.json()['meat_withdrawal_days'] == 10, tk.text

    # --- 7) KATALOGSUZ İLAÇ ve SERBEST METİN: SUSULUYOR -------------------
    bos = hayvan(client, h, 'TR1000000005')
    tb = tedavi(client, h, animal_id=bos, treated_on='2026-09-01',
                items=[{'product_id':c,'drug_name':'C'},
                       {'drug_name':'Kendi karışımım'}])
    assert tb.status_code == 201, tb.text
    assert tb.json()['milk_withdrawal_days'] is None, tb.text
    assert tb.json()['withdrawal_source'] is None, tb.text
    # Boş ihlal DEĞİLDİR: sağım serbest.
    assert sagim(client, h, '2026-09-02', animal_id=bos).status_code == 201

    # --- 8) OPERATÖR KAZANIR ve ÜSTÜNE YAZMA SESSİZ DEĞİL ----------------
    ustune = hayvan(client, h, 'TR1000000006')
    tu = tedavi(client, h, animal_id=ustune, treated_on='2026-09-01',
                milk_withdrawal_days=1, items=[{'product_id':a,'drug_name':'A'}])
    assert tu.status_code == 201, tu.text
    tuj = tu.json()
    assert tuj['milk_withdrawal_days'] == 1, tuj
    assert tuj['withdrawal_source'] == 'OPERATOR_OVERRIDE', tuj
    # KATALOGUN DEDİĞİ AYRI SÜTUNDA DURUYOR — üstüne yazma denetimde GÖRÜNÜR.
    assert tuj['catalogue_milk_days'] == 3, tuj
    assert tuj['catalogue_meat_days'] == 28, tuj

    # AYNI ŞEYİ SÖYLEMEK ÜSTÜNE YAZMA DEĞİLDİR.
    ayni = hayvan(client, h, 'TR1000000007')
    ta = tedavi(client, h, animal_id=ayni, treated_on='2026-09-01',
                milk_withdrawal_days=3, meat_withdrawal_days=28,
                items=[{'product_id':a,'drug_name':'A'}])
    assert ta.json()['withdrawal_source'] == 'OPERATOR', ta.text

    # --- 9) ET KİLİDİ: SALE/SLAUGHTER kesiyor -----------------------------
    # AYRI HAYVAN, BİLEREK: `inek` sürüdedir ve 5. adımın SÜRÜ tedavisi
    # (2026-09-20, et 28) onun et kısıtını da uzatır. Bu adım SINIR GÜNÜNÜ
    # ölçüyor, yani ölçülen kısıt TEK olmalı — iki kısıtın üst üste bindiği
    # bir hayvanda sınır günü başka bir tarihe kayar ve kapı ölçmek
    # istediğinden BAŞKA bir şeyi ölçmüş olurdu.
    etlik = hayvan(client, h, 'TR1000000010')
    assert tedavi(client, h, animal_id=etlik, treated_on='2026-09-01',
                  items=[{'product_id':a,'drug_name':'A'}]).status_code == 201
    sat = hareket(client, h, etlik, 'SALE', '2026-09-28')
    assert sat.status_code == 422, sat.text
    de = sat.json()['detail']
    assert de['sebep'] == 'ARINMA_SURESI_DOLMADI', de
    assert de['blocking'][0]['earliest_allowed'] == '2026-09-29', de
    # SINIR GÜNÜ İZİNLİ.
    sinir_et = hareket(client, h, etlik, 'SALE', '2026-09-29')
    assert sinir_et.status_code == 201, sinir_et.text
    assert sinir_et.json()['withdrawal_warning'] is None, sinir_et.text

    # --- 10) ÖLÜM ve NAKİL KİLİTLİ DEĞİL ----------------------------------
    olen = hayvan(client, h, 'TR1000000008')
    assert tedavi(client, h, animal_id=olen, treated_on='2026-09-01',
                  items=[{'product_id':a,'drug_name':'A'}]).status_code == 201
    olum = hareket(client, h, olen, 'DEATH', '2026-09-02')
    assert olum.status_code == 201, olum.text
    assert olum.json()['withdrawal_warning'] is None, olum.text

    nakil = hayvan(client, h, 'TR1000000009')
    assert tedavi(client, h, animal_id=nakil, treated_on='2026-09-01',
                  items=[{'product_id':a,'drug_name':'A'}]).status_code == 201
    cikis = hareket(client, h, nakil, 'TRANSFER_OUT', '2026-09-02')
    assert cikis.status_code == 201, cikis.text
    assert cikis.json()['withdrawal_warning'] is None, cikis.text

    # --- 11) POLİTİKA: block ----------------------------------------------
    kural_yaz(client, h, herd_withdrawal_policy='block')
    bloke = sagim(client, h, '2026-09-05', animal_id=inek,
                  withdrawal_override_reason='gerekçe YAZILDI ama block')
    assert bloke.status_code == 422, bloke.text
    assert 'izin vermiyor' in bloke.json()['detail']['message'], bloke.text
    # ET tarafı da AYNI politikadan besleniyor.
    bloke_et = hareket(client, h, inek2, 'SLAUGHTER', '2026-09-21',
                       withdrawal_override_reason='gerekçe ama block')
    assert bloke_et.status_code == 422, bloke_et.text

    # --- 12) POLİTİKA: warn -> KABUL ama UYARI YAZILIYOR ------------------
    kural_yaz(client, h, herd_withdrawal_policy='warn')
    uyarili = sagim(client, h, '2026-09-06', animal_id=inek)
    assert uyarili.status_code == 201, uyarili.text
    assert uyarili.json()['withdrawal_warning'], uyarili.text
    assert uyarili.json()['withdrawal_override_reason'] is None, uyarili.text
    uyarili_et = hareket(client, h, inek2, 'SLAUGHTER', '2026-09-21')
    assert uyarili_et.status_code == 201, uyarili_et.text
    assert uyarili_et.json()['withdrawal_warning'], uyarili_et.text
    kural_yaz(client, h, herd_withdrawal_policy='require_reason')

    # --- 13) "allow" SEVİYESİ YOK -----------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'herd_withdrawal_policy':'allow'})
    assert kotu.status_code == 422, kotu.text

    # --- 14) HAYVAN YA DA GRUP, İKİSİ BİRDEN DEĞİL ------------------------
    ikisi = tedavi(client, h, animal_id=inek, group_id=suru['id'],
                   treated_on='2026-09-01', items=[])
    assert ikisi.status_code == 422, ikisi.text
    hicbiri = tedavi(client, h, treated_on='2026-09-01', items=[])
    assert hicbiri.status_code == 422, hicbiri.text

    # --- 15) AKTİVİTE KAYDI ------------------------------------------------
    loglar = client.get('/api/activity-logs', headers=h,
                        params={'limit':100}).json()['items']
    tipler = [x['action_type'] for x in loglar]
    assert tipler.count('vet_drug.create') == 3, tipler
    # Gerekçeyle geçilen İKİ kayıt (3. adımdaki sağım + 11. adım block'ta
    # DÜŞTÜĞÜ için yazılmadı). `warn` politikasındaki geçiş de yazılmadı:
    # orada karar kullanıcının DEĞİL firmanın ayarınındır.
    assert tipler.count('herd_withdrawal.overridden') == 1, tipler
    # KATALOG GÜNCELLEME: eski ve yeni değer birlikte kaydediliyor.
    kayit = client.get('/api/vet-drugs', headers=h,
                       params={'product_id':b}).json()['items'][0]
    put = client.put('/api/vet-drugs/%d' % kayit['id'], headers=h, json={
        'product_id':b,'species':'','milk_withdrawal_days':2,
        'meat_withdrawal_days':2,'status':'ACTIVE',
        'expected_updated_at':kayit['updated_at']})
    assert put.status_code == 200, put.text
    loglar = client.get('/api/activity-logs', headers=h,
                        params={'limit':100}).json()['items']
    guncelleme = [x for x in loglar if x['action_type'] == 'vet_drug.update']
    assert len(guncelleme) == 1, loglar
    assert '10' in guncelleme[0]['summary'] and '2' in guncelleme[0]['summary'], (
        guncelleme[0]['summary'])
    # İYİMSER KİLİT: aynı sürümle ikinci PUT 409.
    tekrar_put = client.put('/api/vet-drugs/%d' % kayit['id'], headers=h, json={
        'product_id':b,'species':'','milk_withdrawal_days':4,
        'meat_withdrawal_days':4,'status':'ACTIVE',
        'expected_updated_at':kayit['updated_at']})
    assert tekrar_put.status_code == 409, tekrar_put.text

    # --- 16) KATALOG DEĞİŞİKLİĞİ GEÇMİŞ TEDAVİYİ KIMILDATMIYOR ------------
    # 1. adımdaki tedavi B'nin 10 gününü SATIRA yazmıştı; B şimdi 2 gün.
    eski = client.get('/api/animal-treatments/%d' % tj['id'], headers=h).json()
    assert eski['milk_withdrawal_days'] == 10, eski

    # --- 17) ÇAPRAZ KİRACI: A'nın tedavisi B'yi KESMEZ --------------------
    firma_b = client.post('/api/companies', headers=h,
                          json={'name':'E2 B Firması'}).json()
    hb = dict(h, **{'X-Company-ID': str(firma_b['id'])})
    assert client.get('/api/vet-drugs', headers=hb).json()['total'] == 0
    assert client.get('/api/animal-treatments', headers=hb).json()['total'] == 0
    b_suru = client.post('/api/animal-groups', headers=hb, json={
        'code':'sagmal','name':'B Sürü','species':'CATTLE'}).json()
    b_inek = hayvan(client, hb, 'TR2000000001', group_id=b_suru['id'])
    temiz = sagim(client, hb, '2026-09-05', animal_id=b_inek)
    assert temiz.status_code == 201, temiz.text
    assert temiz.json()['withdrawal_warning'] is None, temiz.text
    assert sagim(client, hb, '2026-09-05',
                 group_id=b_suru['id']).status_code == 201
    # A'nın hayvanı B'ye görünmüyor.
    assert client.get('/api/animal-treatments/%d' % tj['id'],
                      headers=hb).status_code == 404
    assert client.get('/api/vet-drugs/%d' % kayit['id'],
                      headers=hb).status_code == 404
    # B'nin katalog satırı A'nın ÜRÜNÜNÜ gösteremez.
    assert client.post('/api/vet-drugs', headers=hb, json={
        'product_id':a,'species':'','milk_withdrawal_days':1,
        'meat_withdrawal_days':1}).status_code == 404

    print('ARINMA KILIDI TAMAM')
'''
