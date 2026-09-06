"""HAYVAN/SÜRÜ KARANTİNASI ve KARANTİNA KİLİTLERİ.

Konu: göç `20260909_0075`, `app/routers/herd.py` (`_karantina_*`,
`_kilit_karari`, karantina uçları), `app/herd_schemas.py`,
`app/routers/companies.py`, `app/auth.py`, `app/activity_log.py`.

ÖLÇÜLEN EKSİK: hayvancılık modülünde KARANTİNA kavramı HİÇ YOKTU. Depoda
`quarantine` / `karantina` / `izolasyon` literalleri `app/routers/herd.py`,
`app/herd_*.py` ve `alembic/versions/` içinde SIFIR isabet veriyordu. E2 (göç
0074) İLAÇ KALINTISINI kapattı ve karantina BAŞKA BİR OLGUDUR: arınma bir
İLACIN prospektüsünden HESAPLANIR, karantina bir İNSANIN kararıdır ve ne zaman
biteceği yazıldığı an BİLİNMEZ.

Şekil, deponun mevcut kalıbı ve `tests/test_e2_tedavi_arinma.py` ile BİREBİR:
STATİK KAPILAR + alt süreçte GERÇEK ŞEMALI davranış smoke'u.

--- BU DOSYADAKİ KAPILARIN MUTASYON TABLOSU -------------------------------

Her kapı, HANGİ değişikliğin onu kırmızı yapacağını ADIYLA söylüyor:

  * `_KARANTINA_KILITLI_HAREKETLER`den `TRANSFER_OUT`u çıkarmak
                                       -> NAKİL KİLİTLİ adımı KIRMIZI
  * `_KARANTINA_KILITLI_HAREKETLER`e `DEATH` eklemek
                                       -> ÖLÜM KİLİTLİ DEĞİL adımı KIRMIZI
  * `_karantina_ihlalleri`de sürü yolunu (`_KARANTINA_GRUP_SORGU`) atlayıp
    yalnız bireysel karantinaya bakmak -> SÜRÜ YOLU adımı KIRMIZI
  * `_karantina_ihlalleri`de `hedef_gun >= bitis` atlamasını kaldırmak
    (kapanmış karantina HÂLÂ kilitler) -> SINIR GÜNÜ adımı KIRMIZI
  * `_KARANTINA_*_SORGU`dan iç sorgunun `h.company_id=:cid` yüklemini düşürmek
                                       -> ÇAPRAZ KİRACI adımı KIRMIZI
  * Göçün iki kısmi tekil indeksini düşürmek (ikinci AÇIK karantina serbest)
                                       -> İKİNCİ AÇIK KARANTİNA adımı KIRMIZI
  * `close_quarantine`ın `AND ended_on IS NULL` koşulunu kaldırmak
                                       -> İKİNCİ KAPATMA adımı KIRMIZI
  * `_kilit_karari`nin `block` dalını `warn` gibi davrandırmak
                                       -> POLİTİKA block adımı KIRMIZI
  * `create_milk`/`create_movement`te karantina uyarısını arınmanınkiyle AYNI
    sütuna yazmak                      -> İKİ KİLİT AYRI adımı KIRMIZI
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260909_0075_karantina.py"
HERD = BACKEND / "app" / "routers" / "herd.py"


# --------------------------------------------------------------- statik ---

def test_goc_KARANTINA_TABLOSU_aciyor_ve_BITIS_NULL_KABUL_EDIYOR() -> None:
    """Karantina KENDİ tablosunda; `ended_on` NULL = HÂLÂ AÇIK.

    MUTASYON: `ended_on`u NOT NULL yapmak ya da arınmanın desenini (başlangıç +
    gün sayısı) kopyalamak bunu KIRMIZI yapar. Karantina HESAPLANMAZ: süresi
    belirsizken uydurma bir gün sayısı girmek ZORUNLU olurdu ve o sayı deponun
    İDDİASI olurdu.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert "animal_quarantines" in kaynak
    # `ended_on` NULL KABUL EDER; kapanış AYRI bir karardır.
    assert 'sa.Column("ended_on", sa.Date(), nullable=True)' in kaynak
    assert 'sa.Column("started_on", sa.Date(), nullable=False)' in kaynak
    # REDDEDİLEN TASARIM: arınmanın gün sayacı burada YOK.
    govde = kaynak.split('"""')[2]
    assert "withdrawal_days" not in govde
    assert "quarantine_days" not in govde
    # Bileşik yabancı anahtarlar ADIYLA duruyor: çıplak anahtar çapraz kiracı
    # referansını engellemez (0062'nin kuralı).
    for fk in (
        "fk_animal_quarantines_animal_same_company",
        "fk_animal_quarantines_group_same_company",
    ):
        assert fk in kaynak, fk
    # HEDEF ŞEMADA: hayvan YA DA grup, ikisi birden değil.
    assert "ck_animal_quarantines_hedef" in kaynak
    # ARALIK GERİYE AKMAZ.
    assert "ck_animal_quarantines_aralik" in kaynak
    # SEBEP BOŞLUKTAN İBARET OLAMAZ.
    assert "ck_animal_quarantines_sebep_dolu" in kaynak


def test_ACIK_KARANTINA_indeksi_IKI_TANE_ve_KISMI() -> None:
    """Tek indeks, sürü karantinalarında HİÇBİR ŞEYİ engellemezdi.

    SQL'de UNIQUE NULL'ları BİRBİRİNDEN FARKLI sayar: `(company_id, animal_id,
    group_id)` üzerindeki TEK bir indeks, sürü satırlarında `animal_id` NULL
    olduğu için sınırsız sayıda açık sürü karantinası kabul ederdi.

    KOŞUL İKİ DİYALEKTE DE VERİLMEK ZORUNDA: alembic koşulu diyalekt adıyla
    etiketlenmiş argümandan okur; biri yazılmasaydı O diyalektte indeks
    KOŞULSUZ kurulur ve KAPANMIŞ karantinaları da tekilleştirirdi — yani aynı
    hayvana ikinci bir karantina HİÇ açılamazdı.

    MUTASYON: `sqlite_where`i silmek bunu KIRMIZI yapar (ve aşağıdaki davranış
    smoke'unda ikinci karantina 409 alırdı).
    """
    kaynak = GOC.read_text(encoding="utf-8")
    for ad in (
        "uq_animal_quarantines_acik_hayvan", "uq_animal_quarantines_acik_grup",
    ):
        assert ad in kaynak, ad
    assert kaynak.count("sqlite_where=") == 2, kaynak.count("sqlite_where=")
    assert kaynak.count("postgresql_where=") == 2
    assert "ended_on IS NULL AND animal_id IS NOT NULL" in kaynak
    assert "ended_on IS NULL AND group_id IS NOT NULL" in kaynak


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Dört parça da doğuyor, `downgrade` DÖRDÜNÜ DE geri alıyor, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo ve indeks adlarını
    SABİTTEN okuyor ve dizge araması onu göremezdi — daha kötüsü, `drop_column`
    çağrılarının SQLite'ta GERÇEKTEN çalıştığını hiç ölçmezdi. 0071'de ölçülen
    kusur (yansıtılan CHECK düşürülmüş sütunu adıyla anıyor) yalnız gerçek bir
    turda görünür.
    """
    veritabani = tmp_path / "e3-goc.db"
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
        "karantina": "animal_quarantines" in tablolar,
        "sut": {"quarantine_warning", "quarantine_override_reason"}
               <= sutunlar("milk_yields"),
        "hareket": {"quarantine_warning", "quarantine_override_reason"}
                   <= sutunlar("animal_movements"),
        "firma": "herd_quarantine_policy" in sutunlar("companies"),
    }


command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# 0074'ÜN ÇİFTİ YERİNDE KALIYOR: bu göç ona DOKUNMUYOR ve iki kilit iki AYRI
# sütun çiftiyle yaşıyor.
d = sa.inspect(motor)
sut = {c["name"] for c in d.get_columns("milk_yields")}
assert {"withdrawal_warning", "withdrawal_override_reason"} <= sut, sut

# KISMİ TEKİL İNDEKSLER GERÇEKTEN KISMİ: SQLite `sqlite_master` metnini
# saklıyor ve WHERE'siz kurulmuş olsalardı burada görünmezlerdi.
with motor.connect() as baglanti:
    metinler = {
        ad: (sql or "")
        for ad, sql in baglanti.execute(sa.text(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='animal_quarantines'"
        ))
    }
for ad, sutun in (
    ("uq_animal_quarantines_acik_hayvan", "animal_id"),
    ("uq_animal_quarantines_acik_grup", "group_id"),
):
    sql = metinler[ad]
    assert "UNIQUE" in sql, (ad, sql)
    assert f"WHERE ended_on IS NULL AND {sutun} IS NOT NULL" in sql, (ad, sql)

# HEDEF AÇIK YAZILDI, "-1" DEĞİL. "-1" bir GÖREL adımdır ve zincirin UCUNU
# indirir; üstüne bir göç bindiği anda bu kapı BAŞKA bir göçü ölçmeye başlar
# ve "geri alma çalışmıyor" diye kırmızı olur (0072'de tam olarak bu oldu).
command.downgrade(config, "20260908_0074")
motor.dispose(); motor = sa.create_engine(URL)
assert not any(durum().values()), durum()
# 0074'ÜN ÇİFTİ GERİ ALMADAN SONRA DA YERİNDE: bu göç yalnız KENDİ getirdiğini
# götürür.
d = sa.inspect(motor)
sut = {c["name"] for c in d.get_columns("milk_yields")}
assert {"withdrawal_warning", "withdrawal_override_reason"} <= sut, sut

command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# BAŞ TEK: göç 0075 zincire ikinci bir baş EKLEMEDİ.
from alembic.script import ScriptDirectory
baslar = ScriptDirectory.from_config(config).get_heads()
assert tuple(baslar) == ("20260909_0075",), baslar
print("GOC TURU TAMAM")
"""


def test_check_ve_sutun_AYNI_batchte_dusuyor() -> None:
    """0071'in dersi: SQLite'ta yansıtılan CHECK, düşürülmüş sütunu adıyla anar.

    `herd_quarantine_policy` düşürülürken CHECK'i AYNI batch'te ÖNCE düşmeli;
    ayrı çağrılara bölünürse `downgrade` `OperationalError` verir.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    asagi = kaynak[kaynak.index("def downgrade"):]
    batch = asagi[asagi.index("batch_alter_table(FIRMA)"):]
    kisit = batch.index("drop_constraint")
    sutun = batch.index("drop_column(POLITIKA_SUTUNU)")
    assert kisit < sutun, "CHECK sütundan SONRA düşüyor"


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """0075'in nesnelerinden HANGİLERİ açılış DDL'inde de bildiriliyor.

    ÖLÇÜLMÜŞ KUSUR (0072'de CI'da kırmızı oldu, 0074'te tekrarlanmasın diye
    aynı ayrım yapıldı): `app/tenancy.py` `companies`i `Table()` olarak
    bildiriyor ve uygulamanın AÇILIŞI o tabloyu alembic'ten ÖNCE kurabiliyor.
    Sütun bildirime eklendiği için göç onu VAR bulup tek `if` dalını ATLAR ve
    CHECK HİÇ KURULMAZ — göç yeşil biter, kısıt yoktur.
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
    for tablo in ("animal_quarantines", "milk_yields", "animal_movements"):
        assert tablo not in bildirilen, (
            "%s açılış DDL'ine girmiş; göç 0075 onu VAR bulup atlayabilir "
            "(companies'te ölçülen kusurun aynısı)" % tablo
        )

    goc = GOC.read_text(encoding="utf-8")
    assert "sutun_eksik" in goc and "check_eksik" in goc, (
        "companies dalı sütun ve CHECK'i tek koşulda soruyor; açılış DDL'i "
        "sütunu kurduğunda CHECK SESSİZCE kurulmaz"
    )


def test_karantina_politikasinda_allow_seviyesi_YOK_ve_VARSAYILAN_block() -> None:
    """0048/0064/0072/0074 ile AYNI sınır; VARSAYILAN ise BİLEREK FARKLI.

    MUTASYON: `CompanyPolicyUpdate`e `"allow"` eklemek bunu KIRMIZI yapar.
    Varsayılanın `block` olması göçün `server_default`ında ölçülüyor:
    karantinayı bir insan ELLE açtı ve AÇIK bıraktı; doğru yol onu
    KAPATMAKTIR, etrafından gerekçeyle dolaşmak DEĞİL.
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

    assert seviyeler("herd_quarantine_policy") == {"warn", "require_reason", "block"}
    assert "allow" not in seviyeler("herd_quarantine_policy")
    # KARDEŞ KAPILAR: arınma ve tarla seviyeleri de KIMILDAMADI.
    assert seviyeler("herd_withdrawal_policy") == {"warn", "require_reason", "block"}
    assert seviyeler("farm_plantback_policy") == {"warn", "require_reason", "block"}

    kaynak = GOC.read_text(encoding="utf-8")
    assert 'POLITIKA_VARSAYILAN = "block"' in kaynak
    from app.routers.herd import _VARSAYILAN_KARANTINA_POLITIKASI
    assert _VARSAYILAN_KARANTINA_POLITIKASI == "block"


def test_POLITIKA_DALLARI_TEK_GOVDEDE() -> None:
    """İki kilit de `_kilit_karari`ye gidiyor; ikinci bir kopya YOK.

    Politika dallanmasının iki kopyası olsaydı, `block` dalının bir gün
    birinde düzeltilip ötekinde unutulması SESSİZ bir güvenlik farkı üretirdi.

    MUTASYON: `_karantina_dogrula`nın gövdesine `if politika == "block"`
    dallanmasını KOPYALAMAK bunu KIRMIZI yapar.
    """
    kaynak = HERD.read_text(encoding="utf-8")
    # `block` karşılaştırması TEK yerde: `_kilit_karari`.
    assert kaynak.count('politika == "block"') == 1, kaynak.count(
        'politika == "block"'
    )
    assert kaynak.count('politika == "warn"') == 1
    for ad in ("_arinma_dogrula", "_karantina_dogrula"):
        bas = kaynak.index(f"def {ad}(")
        govde = kaynak[bas:bas + 1200]
        assert "_kilit_karari(" in govde, ad


def test_KARANTINA_KILIDI_nakli_de_kesiyor_olumu_KESMIYOR() -> None:
    """Küme arınmanınkinden GENİŞ ve fark TEK KELİMEDE: TRANSFER_OUT.

    Arınma süresi hayvanla BİRLİKTE taşınır ve kesim kararını karşı taraf alır
    — o yüzden 0074'te nakil serbestti. Karantina TAŞINMAZ: var olma sebebi
    hayvanın işletmeden ÇIKMAMASIDIR ve nakli serbest bırakmak, kilidi tam da
    engellemek için kurulduğu yoldan boşaltırdı.

    ÖLÜM İKİSİNDE DE SERBEST: karantina çoğu zaman bir HASTALIK şüphesidir ve o
    hayvanın ölümü denetimin en çok ihtiyaç duyduğu kayıttır.

    MUTASYON: kümeye `DEATH` eklemek ya da `TRANSFER_OUT`u çıkarmak, aşağıdaki
    davranış smoke'unun iki adımını KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.herd_schemas import MOVEMENT_KIND
    from app.routers.herd import (
        _ET_KILITLI_HAREKETLER,
        _KARANTINA_KILITLI_HAREKETLER,
    )

    assert _KARANTINA_KILITLI_HAREKETLER == {"SALE", "SLAUGHTER", "TRANSFER_OUT"}
    assert _KARANTINA_KILITLI_HAREKETLER < MOVEMENT_KIND
    assert {"DEATH", "PURCHASE", "TRANSFER_IN"} & _KARANTINA_KILITLI_HAREKETLER == set()
    # FARK ADIYLA: arınma kümesi DAR, karantina kümesi GENİŞ ve tek fark nakil.
    assert _ET_KILITLI_HAREKETLER < _KARANTINA_KILITLI_HAREKETLER
    assert _KARANTINA_KILITLI_HAREKETLER - _ET_KILITLI_HAREKETLER == {"TRANSFER_OUT"}


def test_karantina_sorgulari_KIRACI_BAGLI_her_halkada() -> None:
    """Kök yüklem VE iç sorgular AYRI AYRI `company_id=:cid` taşıyor.

    MUTASYON: iç sorgudan `h.company_id=:cid` yüklemini düşürmek, aşağıdaki
    ÇAPRAZ KİRACI adımını KIRMIZI yapar — komşu firmanın sürüsündeki bir
    hayvan bu firmanın sağımını kesebilirdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.herd import (
        _KARANTINA_GRUP_SORGU,
        _KARANTINA_HAYVAN_SORGU,
    )

    for sorgu in (_KARANTINA_HAYVAN_SORGU, _KARANTINA_GRUP_SORGU):
        metin = str(sorgu)
        assert "k.company_id=:cid" in metin
        assert "h.company_id=:cid" in metin
    # İki sorgu da MODÜL SABİTİDİR (f-string değil): istekten gelen hiçbir
    # değer metne giremez.
    kaynak = HERD.read_text(encoding="utf-8")
    for ad in ("_KARANTINA_HAYVAN_SORGU", "_KARANTINA_GRUP_SORGU"):
        bas = kaynak.index(f"{ad} = text(")
        assert 'f"""' not in kaynak[bas:bas + 900]


def test_KAPATMA_KOSULLU_YAZMA_ve_ACILIS_DEGISTIRILEMIYOR() -> None:
    """Kapatma CAS'tır ve genel bir güncelleme ucu AÇILMADI.

    `WHERE ... AND ended_on IS NULL` olmasaydı iki eşzamanlı kapatmanın
    ikincisi de 200 alır ve `ended_on`u EZERDİ.

    `PUT /animal-quarantines/{id}` YOK ve yokluğu BİLİNÇLİ: `started_on`u
    geçmişe dönük değiştirmek, o karantinanın kestiği bütün sağım ve
    hareketleri GERİYE DÖNÜK olarak haklı ya da haksız çıkarırdı.

    MUTASYON: koşulu kaldırmak İKİNCİ KAPATMA adımını KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    # `app.routes` DEĞİL, YÖNLENDİRİCİNİN KENDİSİ: `app.routes` dahil edilen
    # router'ları iç düğümde saklar ve düz bir gezinti hayvancılık uçlarını
    # HİÇ göremez (`test_authorization_population_reconciliation._walk`ın var
    # olma sebebi bu). Burada ölçülen şey hangi uçların AÇILDIĞIDIR ve o
    # bilgi yönlendiricide TAM olarak duruyor; `/api` öneki `main.py`de
    # ekleniyor ve izin kapısı ayrıca `test_uclar_...` içinde ölçülüyor.
    from app.routers.herd import router

    kaynak = HERD.read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def close_quarantine("):]
    assert "AND ended_on IS NULL" in govde[:govde.index("log_request_activity")]

    yollar = {
        (yol, yontem)
        for r in router.routes
        for yol in [getattr(r, "path", "")]
        for yontem in getattr(r, "methods", set())
        if yol.startswith("/animal-quarantines")
    }
    assert ("/animal-quarantines", "POST") in yollar, yollar
    assert ("/animal-quarantines", "GET") in yollar, yollar
    assert ("/animal-quarantines/{quarantine_id}", "GET") in yollar, yollar
    assert ("/animal-quarantines/{quarantine_id}/close", "POST") in yollar, yollar
    # GENEL GÜNCELLEME ve SİLME UCU YOK.
    assert not any(y == "PUT" for _, y in yollar), yollar
    assert not any(y == "DELETE" for _, y in yollar), yollar


def test_uclar_herd_izin_ailesinde_ve_SAGLIK_kapisinda() -> None:
    """Karantina uçları genel `read` iznine DÜŞMÜYOR; `herd.health`teler.

    Karantina bir SAĞLIK OLAYIDIR: hayvanı hasta/şüpheli gördüğü için ayıran
    da, gözlem bitince çıkaran da veteriner ya da sağlık sorumlusudur. Aşı ve
    tedavi kuralının GENİŞLETİLMESİ değil AYNEN UYGULANMASI.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/animal-quarantines") == "herd.view"
    assert required_permission("POST", "/api/animal-quarantines") == "herd.health"
    assert required_permission("GET", "/api/animal-quarantines/1") == "herd.view"
    # KAPATMA AÇMAYLA AYNI İZİNDE: açabilen ama kapatamayan bir rol,
    # karantinayı hiç açmamayı öğrenirdi.
    assert (
        required_permission("POST", "/api/animal-quarantines/1/close")
        == "herd.health"
    )
    # ÖNEK EŞLEŞMESİ ÖLÇÜLDÜ: kendi satırı olmasaydı genel `read`e düşerdi.
    assert not "/api/animal-quarantines".startswith("/api/animals")


def test_KARANTINA_OLAYLARI_aktivite_katalogunda() -> None:
    """Açma ve kapatma AYRI iki tip; `log_activity` katalog dışını reddeder.

    Kapatma neden ayrıca kayıt altında: `animal_quarantines` KULLANICI SÜTUNU
    TAŞIMIYOR (0049'dan beri hayvancılık modülünün deseni; ölçüldü,
    varsayılmadı) ve "bu hayvanı karantinadan kim çıkardı" sorusunun cevabı
    yalnız BURADAN çıkar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    for tip in ("animal_quarantine.opened", "animal_quarantine.closed"):
        assert tip in ACTION_TYPES, tip
    assert "animal_quarantine" in RESOURCE_TYPES
    # KULLANICI SÜTUNU YOK — kaydın var olma sebebi bu OLGUDUR.
    goc_49 = (BACKEND / "alembic" / "versions").glob("*0049*.py")
    kaynak = GOC.read_text(encoding="utf-8")
    assert "created_by" not in kaynak, (
        "karantina tablosuna kullanıcı sütunu eklenmiş; aktivite kaydının "
        "gerekçesi (0049 deseni) yeniden incelenmeli"
    )
    assert list(goc_49), "0049 göçü bulunamadı"


def test_IKI_KILIT_AYRI_SUTUN_CIFTINDE() -> None:
    """0074'ün `withdrawal_*` çifti YERİNDE; 0075 KENDİ çiftini açıyor.

    Bir sağım HEM arınma HEM karantina ihlal edebilir. Tek çifte bindirmek,
    ikinci uyarının birinciyi EZMESİ demekti — ve gerekçe de öyle:
    kullanıcının arınma için yazdığı gerekçe karantina için GEÇERLİ DEĞİLDİR.

    MUTASYON: `create_milk`te `quarantine_warning` yerine `withdrawal_warning`
    yazmak, aşağıdaki İKİ KİLİT AYRI adımını KIRMIZI yapar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.herd_schemas import MilkYieldWrite, MovementWrite

    for sema in (MilkYieldWrite, MovementWrite):
        assert "withdrawal_override_reason" in sema.model_fields, sema
        assert "quarantine_override_reason" in sema.model_fields, sema
    kaynak = GOC.read_text(encoding="utf-8")
    # Bu göç 0074'ün sütunlarına DOKUNMUYOR.
    assert "withdrawal_warning" not in kaynak
    assert "withdrawal_override_reason" not in kaynak


# ------------------------------------------------------------- davranış ---

def run_karantina_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_karantina_enforcement_sqlite(tmp_path: Path) -> None:
    run_karantina_smoke(f"sqlite:///{(tmp_path / 'e3-karantina.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient
from app.main import app

ADMIN_PW = 'E3Kar!123'


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


def hayvan(client, h, kupe, tur='CATTLE', **fazla):
    r = client.post('/api/animals', headers=h, json={
        'ear_tag':kupe,'species':tur,'sex':'FEMALE', **fazla})
    assert r.status_code == 201, r.text
    return r.json()['id']


def karantina(client, h, **govde):
    return client.post('/api/animal-quarantines', headers=h, json=govde)


def kapat(client, h, kid, **govde):
    return client.post('/api/animal-quarantines/%d/close' % kid, headers=h,
                       json=govde)


def sagim(client, h, gun, **fazla):
    return client.post('/api/milk-yields', headers=h, json={
        'milked_on':gun,'quantity_liters':'20', **fazla})


def hareket(client, h, aid, tur, gun, **fazla):
    return client.post('/api/animal-movements', headers=h, json={
        'animal_id':aid,'kind':tur,'moved_on':gun, **fazla})


with TestClient(app) as client:
    h = admin_headers(client)

    # VARSAYILAN `block` — kardeşlerinden FARKLI ve bilerek.
    ayar = client.get('/api/company-settings', headers=h).json()
    assert ayar['herd_quarantine_policy'] == 'block', ayar
    assert ayar['herd_withdrawal_policy'] == 'require_reason', ayar

    suru = client.post('/api/animal-groups', headers=h, json={
        'code':'karantina','name':'Gözlem Sürüsü','species':'CATTLE'}).json()
    inek = hayvan(client, h, 'TR3000000001', group_id=suru['id'])
    inek2 = hayvan(client, h, 'TR3000000002', group_id=suru['id'])
    yalniz = hayvan(client, h, 'TR3000000003')

    # --- 1) AÇMA: bitiş tarihi YOK -----------------------------------------
    k = karantina(client, h, animal_id=inek, started_on='2026-09-01',
                  reason='Şap şüphesi', notes='resmi gözlem')
    assert k.status_code == 201, k.text
    kj = k.json()
    assert kj['ended_on'] is None, kj
    assert kj['started_on'] == '2026-09-01', kj
    assert kj['reason'] == 'Şap şüphesi', kj

    # AÇMA GÖVDESİ `ended_on` KABUL ETMİYOR: kapanış AYRI bir karardır.
    assert karantina(client, h, animal_id=yalniz, started_on='2026-09-01',
                     reason='x', ended_on='2026-09-05').status_code == 422

    # --- 2) İKİNCİ AÇIK KARANTİNA -> 409 -----------------------------------
    # ŞEMA ZORLUYOR (kısmi tekil indeks); uygulama katmanı iki EŞZAMANLI
    # isteği ayırt edemezdi.
    ikinci = karantina(client, h, animal_id=inek, started_on='2026-09-03',
                       reason='ikinci karantina')
    assert ikinci.status_code == 409, ikinci.text

    # SEBEPSİZ ve BOŞLUKTAN İBARET SEBEP REDDEDİLİYOR.
    assert karantina(client, h, animal_id=yalniz,
                     started_on='2026-09-01').status_code == 422
    assert karantina(client, h, animal_id=yalniz, started_on='2026-09-01',
                     reason='   ').status_code == 422
    # HAYVAN YA DA GRUP, İKİSİ BİRDEN DEĞİL.
    assert karantina(client, h, animal_id=yalniz, group_id=suru['id'],
                     started_on='2026-09-01', reason='x').status_code == 422
    assert karantina(client, h, started_on='2026-09-01',
                     reason='x').status_code == 422

    # --- 3) SÜT KİLİDİ: block (VARSAYILAN) --------------------------------
    ihlal = sagim(client, h, '2026-09-05', animal_id=inek)
    assert ihlal.status_code == 422, ihlal.text
    d = ihlal.json()['detail']
    assert d['sebep'] == 'KARANTINA_ACIK', d
    assert d['blocking'][0]['scope'] == 'ANIMAL', d
    assert d['blocking'][0]['ended_on'] is None, d
    assert d['blocking'][0]['reason'] == 'Şap şüphesi', d
    assert 'izin vermiyor' in d['message'], d
    # POLİTİKA block: GEREKÇE YAZILSA DA GEÇMİYOR.
    assert sagim(client, h, '2026-09-05', animal_id=inek,
                 quarantine_override_reason='gerekçe ama block').status_code == 422

    # BAŞLANGIÇ GÜNÜ KAPSANIYOR; ÖNCESİ SERBEST.
    assert sagim(client, h, '2026-09-01', animal_id=inek).status_code == 422
    assert sagim(client, h, '2026-08-31', animal_id=inek).status_code == 201

    # --- 4) SÜRÜ YOLU ------------------------------------------------------
    # `inek` sürüde; sürünün toplu sağımı onun sütünü de içerir.
    grup_ihlal = sagim(client, h, '2026-09-05', group_id=suru['id'])
    assert grup_ihlal.status_code == 422, grup_ihlal.text
    assert grup_ihlal.json()['detail']['blocking'][0]['scope'] == 'ANIMAL'
    # SÜRÜ DIŞINDAKİ hayvan ETKİLENMİYOR.
    assert sagim(client, h, '2026-09-05', animal_id=yalniz).status_code == 201

    # --- 5) HAREKET KİLİDİ: SALE/SLAUGHTER/TRANSFER_OUT --------------------
    for tur in ('SALE', 'SLAUGHTER', 'TRANSFER_OUT'):
        r = hareket(client, h, inek, tur, '2026-09-05')
        assert r.status_code == 422, (tur, r.text)
        assert r.json()['detail']['sebep'] == 'KARANTINA_ACIK', (tur, r.text)

    # --- 6) ÖLÜM ve GİRİŞ KİLİTLİ DEĞİL ------------------------------------
    olen = hayvan(client, h, 'TR3000000004')
    ko = karantina(client, h, animal_id=olen, started_on='2026-09-01',
                   reason='ölüm testi')
    assert ko.status_code == 201, ko.text
    olum = hareket(client, h, olen, 'DEATH', '2026-09-05')
    assert olum.status_code == 201, olum.text
    assert olum.json()['quarantine_warning'] is None, olum.text

    gelen = hayvan(client, h, 'TR3000000005')
    assert karantina(client, h, animal_id=gelen, started_on='2026-09-01',
                     reason='giriş karantinası').status_code == 201
    for tur in ('PURCHASE', 'TRANSFER_IN'):
        r = hareket(client, h, gelen, tur, '2026-09-05')
        assert r.status_code == 201, (tur, r.text)
        assert r.json()['quarantine_warning'] is None, (tur, r.text)

    # --- 7) KAPATMA ve İKİNCİ KAPATMA -> 409 -------------------------------
    kapanis = kapat(client, h, kj['id'], ended_on='2026-09-10')
    assert kapanis.status_code == 200, kapanis.text
    assert kapanis.json()['ended_on'] == '2026-09-10', kapanis.text
    tekrar = kapat(client, h, kj['id'], ended_on='2026-09-11')
    assert tekrar.status_code == 409, tekrar.text
    # ARALIK GERİYE AKMAZ.
    k2 = karantina(client, h, animal_id=inek2, started_on='2026-09-01',
                   reason='geriye akış testi')
    assert k2.status_code == 201, k2.text
    assert kapat(client, h, k2.json()['id'],
                 ended_on='2026-08-31').status_code == 422
    # AYNI GÜN KAPANIŞ SERBEST.
    assert kapat(client, h, k2.json()['id'],
                 ended_on='2026-09-01').status_code == 200

    # KAPANDIKTAN SONRA İKİNCİ KARANTİNA AÇILABİLİYOR — kısmi indeksin
    # KISMİ olmasının ölçüsü budur; koşulsuz olsaydı burada 409 gelirdi.
    yeniden = karantina(client, h, animal_id=inek, started_on='2026-10-01',
                        reason='ikinci dönem')
    assert yeniden.status_code == 201, yeniden.text
    assert kapat(client, h, yeniden.json()['id'],
                 ended_on='2026-10-02').status_code == 200

    # --- 8) SINIR GÜNÜ: ended_on GÜNÜ SERBEST ------------------------------
    # Karantina 01-10 arasıydı. 10'u SERBEST, 09'u HÂLÂ KESİYOR.
    sinir = hareket(client, h, inek, 'SALE', '2026-09-10')
    assert sinir.status_code == 201, sinir.text
    assert sinir.json()['quarantine_warning'] is None, sinir.text
    assert sagim(client, h, '2026-09-09', animal_id=inek).status_code == 422
    # KAPANMIŞ KARANTİNA GEÇMİŞİ HÂLÂ KESİYOR: kapatıp geçmişe kayıt girerek
    # kilidi atlatmak MÜMKÜN DEĞİL.
    assert sagim(client, h, '2026-09-05', animal_id=inek).status_code == 422

    # --- 9) POLİTİKA: require_reason ---------------------------------------
    kural_yaz(client, h, herd_quarantine_policy='require_reason')
    gerekcesiz = sagim(client, h, '2026-09-05', animal_id=inek)
    assert gerekcesiz.status_code == 422, gerekcesiz.text
    assert 'gerekçe girin' in gerekcesiz.json()['detail']['message']
    gecti = sagim(client, h, '2026-09-05', animal_id=inek,
                  quarantine_override_reason='süt buzağıya, tanka girmiyor')
    assert gecti.status_code == 201, gecti.text
    g = gecti.json()
    assert g['quarantine_warning'], g
    # 0048 kuralı: sistemin bulduğu ile kullanıcının söylediği AYRI sütunda.
    assert g['quarantine_override_reason'] == 'süt buzağıya, tanka girmiyor', g
    assert g['quarantine_warning'] != g['quarantine_override_reason'], g

    # --- 10) POLİTİKA: warn -> KABUL ama UYARI YAZILIYOR -------------------
    kural_yaz(client, h, herd_quarantine_policy='warn')
    uyarili = sagim(client, h, '2026-09-06', animal_id=inek)
    assert uyarili.status_code == 201, uyarili.text
    assert uyarili.json()['quarantine_warning'], uyarili.text
    assert uyarili.json()['quarantine_override_reason'] is None, uyarili.text
    # KAPANMIŞ ama KAPSAYAN aralık üzerinden: `inek`in 01-10 karantinası
    # 09-05'i içeriyor. `inek2`nin karantinası AYNI GÜN kapandığı için
    # (yarı açık aralık) hiçbir günü kapsamıyor ve burada ölçüm YAPMAZDI.
    uyarili_h = hareket(client, h, inek, 'TRANSFER_OUT', '2026-09-05')
    assert uyarili_h.status_code == 201, uyarili_h.text
    assert uyarili_h.json()['quarantine_warning'], uyarili_h.text

    # --- 11) "allow" SEVİYESİ YOK ------------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'herd_quarantine_policy':'allow'})
    assert kotu.status_code == 422, kotu.text

    # --- 12) İKİ KİLİT AYRI: arınma VE karantina BİRLİKTE ------------------
    # E2 kilidi de kuruluyor ve İKİ UYARI BAĞIMSIZ sütunlarda duruyor.
    kural_yaz(client, h, herd_quarantine_policy='require_reason',
              herd_withdrawal_policy='require_reason')
    ikili = hayvan(client, h, 'TR3000000006')
    ilac = client.post('/api/products', headers=h, json={
        'name':'E3 Antibiyotik','unit':'ML','sale_price':'10.00'}).json()['id']
    assert client.post('/api/vet-drugs', headers=h, json={
        'product_id':ilac,'species':'','milk_withdrawal_days':10,
        'meat_withdrawal_days':28}).status_code == 201
    assert client.post('/api/animal-treatments', headers=h, json={
        'animal_id':ikili,'treated_on':'2026-09-01',
        'items':[{'product_id':ilac,'drug_name':'A'}]}).status_code == 201
    assert karantina(client, h, animal_id=ikili, started_on='2026-09-01',
                     reason='hem tedavi hem karantina').status_code == 201

    # YALNIZ ARINMA GEREKÇESİ: karantina HÂLÂ kesiyor.
    yarim = sagim(client, h, '2026-09-05', animal_id=ikili,
                  withdrawal_override_reason='arınma için gerekçe')
    assert yarim.status_code == 422, yarim.text
    assert yarim.json()['detail']['sebep'] == 'KARANTINA_ACIK', yarim.text
    # İKİ GEREKÇE BİRDEN: iki uyarı da AYRI sütuna yazılıyor.
    ikisi = sagim(client, h, '2026-09-05', animal_id=ikili,
                  withdrawal_override_reason='arınma için gerekçe',
                  quarantine_override_reason='karantina için gerekçe')
    assert ikisi.status_code == 201, ikisi.text
    ij = ikisi.json()
    assert ij['withdrawal_warning'], ij
    assert ij['quarantine_warning'], ij
    assert ij['withdrawal_warning'] != ij['quarantine_warning'], ij
    assert ij['withdrawal_override_reason'] == 'arınma için gerekçe', ij
    assert ij['quarantine_override_reason'] == 'karantina için gerekçe', ij
    # ARINMA UYARISI ARINMADAN, KARANTİNA UYARISI KARANTİNADAN BAHSEDİYOR.
    assert 'arınma' in ij['withdrawal_warning'], ij
    assert 'karantina' in ij['quarantine_warning'].lower(), ij

    # --- 13) LİSTE ve open_only --------------------------------------------
    liste = client.get('/api/animal-quarantines', headers=h).json()
    assert liste['total'] >= 6, liste
    acik = client.get('/api/animal-quarantines', headers=h,
                      params={'open_only':'true'}).json()
    assert all(x['ended_on'] is None for x in acik['items']), acik
    assert acik['total'] < liste['total'], (acik['total'], liste['total'])
    hayvan_suzgec = client.get('/api/animal-quarantines', headers=h,
                               params={'animal_id':inek}).json()
    assert hayvan_suzgec['total'] == 2, hayvan_suzgec

    # --- 14) AKTİVİTE KAYDI ------------------------------------------------
    loglar = client.get('/api/activity-logs', headers=h,
                        params={'limit':100}).json()['items']
    tipler = [x['action_type'] for x in loglar]
    # SAYILAR TAM: altı açma (inek, olen, gelen, inek2, inek'in ikinci
    # dönemi, ikili) ve UC kapatma. REDDEDILEN istekler kayit YAZMIYOR —
    # 409 alan ikinci acma, 409 alan ikinci kapatma ve 422 alan geriye akan
    # kapanis defterde YOK; olmayan bir karar kaydedilmemelidir.
    assert tipler.count('animal_quarantine.opened') == 6, tipler
    assert tipler.count('animal_quarantine.closed') == 3, tipler
    acilis = [x for x in loglar if x['action_type'] == 'animal_quarantine.opened']
    assert any('Şap şüphesi' in x['summary'] for x in acilis), acilis
    kapanislar = [x for x in loglar
                  if x['action_type'] == 'animal_quarantine.closed']
    assert any('2026-09-10' in x['summary'] for x in kapanislar), kapanislar

    # --- 15) ÇAPRAZ KİRACI: A'nın karantinası B'yi KESMEZ ------------------
    firma_b = client.post('/api/companies', headers=h,
                          json={'name':'E3 B Firması'}).json()
    hb = dict(h, **{'X-Company-ID': str(firma_b['id'])})
    assert client.get('/api/animal-quarantines', headers=hb).json()['total'] == 0
    b_suru = client.post('/api/animal-groups', headers=hb, json={
        'code':'karantina','name':'B Sürü','species':'CATTLE'}).json()
    b_inek = hayvan(client, hb, 'TR4000000001', group_id=b_suru['id'])
    temiz = sagim(client, hb, '2026-09-05', animal_id=b_inek)
    assert temiz.status_code == 201, temiz.text
    assert temiz.json()['quarantine_warning'] is None, temiz.text
    assert sagim(client, hb, '2026-09-05',
                 group_id=b_suru['id']).status_code == 201
    assert hareket(client, hb, b_inek, 'SALE', '2026-09-05').status_code == 201
    # A'nın kaydı B'ye görünmüyor.
    assert client.get('/api/animal-quarantines/%d' % kj['id'],
                      headers=hb).status_code == 404
    assert kapat(client, hb, kj['id'], ended_on='2026-09-30').status_code == 404
    # B'nin karantinası A'nın HAYVANINI gösteremez.
    assert karantina(client, hb, animal_id=inek, started_on='2026-09-01',
                     reason='çapraz').status_code == 404

    print('KARANTINA KILIDI TAMAM')
'''
