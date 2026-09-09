"""SEC-4 — AÇILIŞTA GÖÇ SÜRMEK BİTTİ: üretimde açılış SALT OKUNUR bir KAPIDIR.

Konu: `app/main.py` (`_semayi_hazirla`, `_semayi_dogrula`, lifespan),
`app/bootstrap_data.py` (`main`), `docker-compose.prod.yml`,
`deploy/sunucu-deploy.sh`, `.github/workflows/ci.yml`.

--- KUSUR NEYDİ ------------------------------------------------------------

`app/main.py` MODÜL İTHALİNDE ve KOŞULSUZ olarak şunu koşuyordu:

    with database_bootstrap_lock(engine):
        if settings.auto_migrate: run_database_migrations(...)
        else:                     <salt okunur durum kontrolü>
        seed_bootstrap_data(engine)

Uygulamayı ithal eden HER süreç bu bloğa girer. `WEB_CONCURRENCY>1` ile açılan
her uvicorn işçisi ve her replika, cluster genelindeki TEK advisory kilit için
(`POSTGRES_MIGRATION_LOCK_KEY`) sıraya girer; kilidi
`settings.migration_lock_timeout_seconds` (120 sn) içinde alamayan işçi
`TimeoutError` ile AÇILIŞTA ÇÖKER. Kilit yalnız PostgreSQL'de vardır, yani
SQLite yolu bu serileştirmeyi HİÇ yapmaz.

ÜRETİM O GÜN AUTO_MIGRATE=true İLE KOŞUYORDU ve bu bir varsayım değil,
ölçüldü: `docker-compose.prod.yml` AUTO_MIGRATE'i HİÇ yazmıyordu ve taban
`docker-compose.yml` `AUTO_MIGRATE: ${AUTO_MIGRATE:-true}` diyordu — Compose
`environment` haritalarını -f dosyaları arasında BİRLEŞTİRDİĞİ için üretim
app'i taban değeri devralıyordu.

--- ÇÖZÜM NE DEĞİL ---------------------------------------------------------

"Bloğu lifespan'a taşımak" TEK BAŞINA HİÇBİR ŞEY ÇÖZMEZ ve bu dilimin en
önemli cümlesi budur: lifespan da işçi BAŞINA koşar. N işçi, N lifespan, aynı
kilit, aynı yarış. Yarışı bitiren şey bloğun YERİ değil, üretimde YAZMAYI
BIRAKMASIDIR.

--- ÇÖZÜM NE ---------------------------------------------------------------

İKİ YOL, İKİ FARKLI İŞ:

  AUTO_MIGRATE=true  (geliştirme/CI/testler; kod varsayılanı DEĞİŞMEDİ)
      İthalde YAZAR: kilit + `alembic upgrade head` + bootstrap tohumu.
      İthalde KALDI ve bu bilinçli: depodaki alt-süreç testlerinin çoğu
      `TestClient(app)`i lifespan'a GİRMEDEN kurar (98 dosya ölçüldü), yani
      şemayı ithalden başka bir yerden alamaz.

  AUTO_MIGRATE=false (ÜRETİM; docker-compose.prod.yml artık SABİT yazıyor)
      İthalde HİÇBİR ŞEY: kilit YOK, DDL YOK, DML YOK. Lifespan'da yalnız
      SALT OKUNUR ve KİLİTSİZ bir doğrulama; şema güncel değilse açılış
      operatöre KOŞULACAK KOMUTU vererek durur.

Şemayı üretimde süren tek yer `deploy/sunucu-deploy.sh` 7/8 adımıdır: `up -d
app`ten ÖNCE, TEK bir `run --rm --no-deps` konteynerinde.

--- BU DOSYANIN ÖLÇTÜĞÜ SINIR ----------------------------------------------

AUTO_MIGRATE=false iken `import app.main` güncel OLMAYAN bir veritabanında
BAŞARILI OLUR ve veritabanına HİÇ DOKUNMAZ; hata lifespan açılışında patlar.
İki mutant bu iki yarıyı ayrı ayrı çiviliyor (`BLOK_ITHALE_GERI`,
`DURUM_KONTROLU_DUSURULDU`) ve İKİSİ DE bu dosyanın SAĞLAM koşumda geçen
senaryolarının TA KENDİSİNDE ölür. Mutasyon KAYNAK AĞACA DEĞİL, tek
kullanımlık bir KOPYAYA uygulanır: kanonik koşucu dosyaları paralel
koşturuyor ve yerinde mutasyon komşu dosyaları nedensizce kırmızıya
çevirirdi.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
KOK = BACKEND.parent
MAIN_PY = BACKEND / "app" / "main.py"
BOOTSTRAP_PY = BACKEND / "app" / "bootstrap_data.py"
PROD_COMPOSE = KOK / "docker-compose.prod.yml"
TABAN_COMPOSE = KOK / "docker-compose.yml"
DEPLOY_BETIGI = KOK / "deploy" / "sunucu-deploy.sh"
URETIM_ORNEK = KOK / ".env.production.example"
CI_YML = KOK / ".github" / "workflows" / "ci.yml"

#: Hata metninin de deploy betiğinin de kullandığı TEK komut gövdesi.
KOMUT_GOVDESI = "sh -c 'cd /app/backend && python -m alembic upgrade head'"


# --------------------------------------------------------------- yardımcı ---

def _ortam(veritabani: Path, *, auto_migrate: str, veri_dizini: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["AUTO_MIGRATE"] = auto_migrate
    env["SUNGUR_DATA_DIR"] = str(veri_dizini)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    # Testin konusu göç kapısıdır; zamanlayıcılar lifespan'da kapının ARDINDAN
    # gelir ve açılmaları bu ölçümü bulandırırdı.
    env["FIELD_STOCK_OUTBOX_ENABLED"] = "false"
    env["WHATSAPP_WORKER_ENABLED"] = "false"
    return env


def _kos(kod: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", kod],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
        # Windows'ta `text=True` yerel kod sayfasina (cp1254) duser ve
        # Turkce hata metnini COZEMEZ; olcum, olcmek istedigi seyden once
        # cozucude patlardi.
        encoding="utf-8", errors="replace",
    )


def _tablolar(veritabani: Path) -> set[str]:
    if not veritabani.exists():
        return set()
    baglanti = sqlite3.connect(veritabani)
    try:
        return {
            ad for (ad,) in baglanti.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        baglanti.close()


_ITHAL = "import app.main\nprint('ITHAL_TAMAM')\n"

_LIFESPAN = (
    "from fastapi.testclient import TestClient\n"
    "from app.main import app\n"
    "with TestClient(app) as istemci:\n"
    "    assert istemci.get('/api/live').status_code == 200\n"
    "print('LIFESPAN_TAMAM')\n"
)


# ------------------------------------------------------------- davranış ---

def test_AUTO_MIGRATE_KAPALI_ITHAL_veritabanina_HIC_DOKUNMUYOR(tmp_path: Path) -> None:
    """İthal BAŞARILI olur ve veritabanı dosyası ŞEMASIZ kalır.

    "Dokunmuyor" burada bir niyet değil bir ÖLÇÜMDÜR: `alembic_version` ve
    `users` tablolarının YOKLUĞU, ithalde ne DDL ne DML koştuğunu söyler.
    (Kilit için ayrı bir ölçüm gerekmez: SQLite'ta kilit zaten no-op'tur, ve
    kilidi alan yolun kendisi `_semayi_hazirla` içindedir.)
    """
    veritabani = tmp_path / "sec4-ithal.db"
    sonuc = _kos(_ITHAL, _ortam(veritabani, auto_migrate="false", veri_dizini=tmp_path))

    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr
    assert "ITHAL_TAMAM" in sonuc.stdout

    tablolar = _tablolar(veritabani)
    assert "alembic_version" not in tablolar, (
        "AUTO_MIGRATE=false iken ithal göç sürmüş: " + repr(sorted(tablolar))
    )
    assert "app_users" not in tablolar, (
        "AUTO_MIGRATE=false iken ithal şema kurmuş: " + repr(sorted(tablolar))
    )


def test_AUTO_MIGRATE_KAPALI_GUNCEL_OLMAYAN_semada_LIFESPAN_DURUYOR(tmp_path: Path) -> None:
    """Açılış kapısı: şema güncel değilse trafik alınmaz ve KOMUT gösterilir."""
    veritabani = tmp_path / "sec4-lifespan.db"
    sonuc = _kos(_LIFESPAN, _ortam(veritabani, auto_migrate="false", veri_dizini=tmp_path))

    assert sonuc.returncode != 0, (
        "güncel olmayan şemada lifespan açıldı:\n" + sonuc.stdout + sonuc.stderr
    )
    ciktilar = sonuc.stdout + sonuc.stderr
    assert "LIFESPAN_TAMAM" not in ciktilar
    assert "RuntimeError" in ciktilar
    assert "AUTO_MIGRATE" in ciktilar
    # Hata OPERATÖRE KOŞULACAK KOMUTU verir. "Şema güncel değil" demek, bunu
    # nasıl düzelteceğini söylemeden, bir üretim olayında işe yaramaz.
    assert KOMUT_GOVDESI in ciktilar, ciktilar[-3000:]
    assert "run --rm --no-deps app" in ciktilar


def test_AUTO_MIGRATE_KAPALI_GUNCEL_semada_LIFESPAN_ACILIYOR(tmp_path: Path) -> None:
    """Kapı bir DUVAR değil: şema güncelken AUTO_MIGRATE=false normal açılır."""
    veritabani = tmp_path / "sec4-guncel.db"

    # Şemayı ÖNCE ve AYRI bir süreçte kur (üretimdeki deploy adımının aynısı:
    # göç uygulamadan ÖNCE, tek bir süreçte).
    hazirlik = _kos(_ITHAL, _ortam(veritabani, auto_migrate="true", veri_dizini=tmp_path))
    assert hazirlik.returncode == 0, hazirlik.stdout + "\n" + hazirlik.stderr
    assert "alembic_version" in _tablolar(veritabani)

    sonuc = _kos(_LIFESPAN, _ortam(veritabani, auto_migrate="false", veri_dizini=tmp_path))
    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr
    assert "LIFESPAN_TAMAM" in sonuc.stdout


def test_TOHUM_ETKISIZDIR_ikinci_kosu_TEK_BIR_SATIR_eklemiyor(tmp_path: Path) -> None:
    """`python -m app.bootstrap_data` idempotent — BEYAN DEĞİL, ÖLÇÜM.

    Deploy betiği tohumu HER deploy'da koşturuyor. Etkisiz olmasaydı her
    deploy ikinci bir "Ana Firma", ikinci bir "Merkez Depo" ve üç finans
    hesabı daha yaratırdı. Sayım TABLO BAZINDA yapılıyor: tek bir toplam,
    bir tabloda artıp diğerinde eksilen bir sapmayı gizleyebilirdi.
    """
    veritabani = tmp_path / "sec4-tohum.db"
    env = _ortam(veritabani, auto_migrate="true", veri_dizini=tmp_path)

    hazirlik = _kos(_ITHAL, env)
    assert hazirlik.returncode == 0, hazirlik.stdout + "\n" + hazirlik.stderr

    tablolar = sorted(
        _tablolar(veritabani) & {
            "app_users", "companies", "branches", "user_company_memberships",
            "warehouses", "warehouse_stocks", "finance_accounts",
        }
    )
    assert len(tablolar) == 7, tablolar

    def _sayim() -> dict[str, int]:
        baglanti = sqlite3.connect(veritabani)
        try:
            return {
                ad: baglanti.execute(f"SELECT COUNT(*) FROM {ad}").fetchone()[0]
                for ad in tablolar
            }
        finally:
            baglanti.close()

    once = _sayim()
    # Tohumu AÇILIŞTAN AYRI, deploy betiğinin çağırdığı giriş noktasıyla koştur.
    tekrar = subprocess.run(
        [sys.executable, "-m", "app.bootstrap_data"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    assert tekrar.returncode == 0, tekrar.stdout + "\n" + tekrar.stderr
    sonra = _sayim()

    assert sonra == once, f"tohum idempotent DEĞİL: {once} -> {sonra}"


def test_TOHUM_GIRIS_NOKTASI_app_main_ITHAL_ETMIYOR() -> None:
    """`python -m app.bootstrap_data` uygulamanın açılış yüzeyini kurmaz.

    Etseydi tohumlama, yönlendirici/ara katman kablolamasının tamamına ve
    (AUTO_MIGRATE'e göre) ithal-zamanı göçe bağımlı olurdu — yani deploy'un
    "önce göç, sonra tohum" sırası kendi içinde ikinci kez ve gizlice
    kurulurdu.
    """
    kaynak = BOOTSTRAP_PY.read_text(encoding="utf-8")
    assert "def main() -> int:" in kaynak
    assert 'if __name__ == "__main__":' in kaynak
    assert "app.main" not in kaynak.replace("`app.main`", "")
    assert re.search(r"^from \.main import|^from \. import main$", kaynak, re.M) is None


# ------------------------------------------------------------- mutantlar ---
#
# MUTASYON KAYNAK AĞACINDA YAPILMAZ, KOPYASINDA YAPILIR.
#
# İlk yazımda mutantlar `backend/app/main.py`yi YERİNDE değiştirip `finally`de
# geri koyuyordu. Tek başına koşarken doğru çalışıyordu ve TAM DA BU YÜZDEN
# tehlikeliydi: kanonik koşucu (`run_isolated_tests.py --workers N`) dosyaları
# PARALEL koşturur. Aynı anda koşan bir komşu dosya, mutasyon penceresi
# içinde BOZULMUŞ `app/main.py`yi ithal eder ve NEDENSİZ kırmızı yanardı —
# ya da daha kötüsü, bu dosyanın kendi mutantı komşunun ithalinde ölür ve
# hata BAŞKA BİR DOSYANIN adıyla raporlanırdı.
#
# Artık her mutant kendi tek kullanımlık ağacını kurar (`app/` + `alembic/` +
# `alembic.ini`), mutasyonu ORADA yapar ve alt süreci PYTHONPATH'i o ağaca
# bakacak biçimde koşturur. Kaynak ağaca HİÇ DOKUNULMAZ ve bu iddia da
# ölçülüyor (`test_MUTANTLAR_KAYNAK_AGACA_DOKUNMUYOR`).


def _sanal_agac(hedef: Path) -> Path:
    """`app/` + `alembic/` + `alembic.ini`den ibaret tek kullanımlık kopya.

    `alembic/` de KOPYALANIR çünkü `runtime_migrations.expected_revision`
    head'i `Path(__file__).parents[1] / "alembic"` altından okur; yalnız `app/`
    kopyalansaydı beklenen sürüm HİÇ bulunamaz ve kapı yanlış sebeple çalardı.
    """
    hedef.mkdir(parents=True, exist_ok=True)
    yoksay = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(BACKEND / "app", hedef / "app", ignore=yoksay)
    shutil.copytree(BACKEND / "alembic", hedef / "alembic", ignore=yoksay)
    shutil.copy2(BACKEND / "alembic.ini", hedef / "alembic.ini")
    return hedef


def _mutasyona_ugrat(dosya: Path, eski: str, yeni: str) -> None:
    metin = dosya.read_text(encoding="utf-8")
    # Kopya, çalışma kopyasının satır sonlarını taşır (Windows'ta CRLF);
    # çapa ise LF yazılıdır.
    sonu = "\r\n" if "\r\n" in metin else "\n"
    capa = eski.replace("\n", sonu)
    assert capa in metin, f"mutant çapası bulunamadı: {eski!r}"
    dosya.write_text(metin.replace(capa, yeni.replace("\n", sonu), 1), encoding="utf-8")


def test_MUTANT_BLOK_ITHALE_GERI_kirmizi_yanar(tmp_path: Path) -> None:
    """`if settings.auto_migrate:` -> `if True:` (blok yine KOŞULSUZ ithalde).

    Bu, kusurun TA KENDİSİDİR: AUTO_MIGRATE=false olan üretim işçisi yine
    ithalde kilidi alıp göç sürmeye kalkardı.
    """
    agac = _sanal_agac(tmp_path / "agac")
    _mutasyona_ugrat(
        agac / "app" / "main.py",
        "if settings.auto_migrate:\n    _semayi_hazirla()",
        "if True:\n    _semayi_hazirla()",
    )

    veritabani = tmp_path / "mutant-ithal.db"
    env = _ortam(veritabani, auto_migrate="false", veri_dizini=tmp_path)
    env["PYTHONPATH"] = str(agac)
    sonuc = subprocess.run(
        [sys.executable, "-c", _ITHAL], cwd=agac, env=env,
        capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )

    assert sonuc.returncode == 0, sonuc.stdout + "\n" + sonuc.stderr
    tablolar = _tablolar(veritabani)
    assert "alembic_version" in tablolar and "app_users" in tablolar, (
        "MUTANT HAYATTA KALDI: blok koşulsuz ithale döndü ama veritabanına "
        "yine dokunulmadı — `test_AUTO_MIGRATE_KAPALI_ITHAL_veritabanina_HIC_"
        "DOKUNMUYOR` bu mutantı görmüyor demektir. Bulunan tablolar: "
        + repr(sorted(tablolar))
    )


def test_MUTANT_DURUM_KONTROLU_DUSURULDU_kirmizi_yanar(tmp_path: Path) -> None:
    """Lifespan'daki `_semayi_dogrula()` çağrısı düşürülür.

    Mutant hayattaysa şeması OLMAYAN bir süreç trafik almaya başlar: kapı
    yalnız BİR YERDE, o çağrıda vardır.
    """
    agac = _sanal_agac(tmp_path / "agac")
    _mutasyona_ugrat(
        agac / "app" / "main.py", "    _semayi_dogrula()\n", "    pass  # mutant\n"
    )

    veritabani = tmp_path / "mutant-kapi.db"
    env = _ortam(veritabani, auto_migrate="false", veri_dizini=tmp_path)
    env["PYTHONPATH"] = str(agac)
    sonuc = subprocess.run(
        [sys.executable, "-c", _LIFESPAN], cwd=agac, env=env,
        capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )

    assert "LIFESPAN_TAMAM" in sonuc.stdout, (
        "MUTANT HAYATTA KALDI ya da BAŞKA BİR SEBEPLE düştü; kapının TEK yeri "
        "`_semayi_dogrula()` çağrısı olmalı:\n" + sonuc.stdout + sonuc.stderr
    )


def test_MUTANTLAR_KAYNAK_AGACA_DOKUNMUYOR() -> None:
    """Mutasyon yolu `backend/app/main.py`ye YAZMAYI hiç bilmiyor.

    "Geri koyuyoruz" bir güvence DEĞİLDİR: çöken bir koşu geri koyamaz ve
    paralel bir komşu, geri koymadan ÖNCE okur. Bu yüzden kapı davranışsal
    değil YAPISALDIR.

    ÖLÇÜM AST İLEDİR, METİN ARAMASIYLA DEĞİL. İlk yazımda bu test yasak
    dizeyi `in` ile arıyordu ve KENDİ KAYNAĞINDA eşleşiyordu: dosyanın
    kendisi hakkındaki bir iddia, iddianın metnini de okuduğu için her zaman
    kırmızıydı. AST, iddiayı yazan satırı değil ÇALIŞAN çağrıyı görür.
    """
    import ast

    agac = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    hedefler: list[str] = []
    for dugum in ast.walk(agac):
        if (
            isinstance(dugum, ast.Call)
            and isinstance(dugum.func, ast.Name)
            and dugum.func.id == "_mutasyona_ugrat"
        ):
            hedefler.append(ast.dump(dugum.args[0]))

    assert len(hedefler) == 2, f"iki mutant bekleniyor, {len(hedefler)} bulundu"
    beklenen = ast.dump(
        ast.parse('agac / "app" / "main.py"', mode="eval").body
    )
    assert hedefler == [beklenen, beklenen], hedefler

    # Modül düzeyindeki `MAIN_PY` yalnız OKUNUR: hiçbir çağrının alıcısı
    # olmaz (`MAIN_PY.write_text(...)` gibi) ve hiçbir yere atanmaz.
    yazimlar = [
        ast.dump(d)
        for d in ast.walk(agac)
        if isinstance(d, ast.Attribute)
        and isinstance(d.value, ast.Name)
        and d.value.id == "MAIN_PY"
        and d.attr.startswith("write")
    ]
    assert yazimlar == [], yazimlar


# --------------------------------------------------- statik sözleşmeler ---

def test_URETIM_COMPOSE_AUTO_MIGRATE_i_SABIT_false_yaziyor() -> None:
    """Değer `${AUTO_MIGRATE:-false}` DEĞİL, düz `"false"` olmalı.

    `${...}` yazmak, .env.production'a tek satır ekleyerek üretimde
    auto-migrate'i geri açmayı mümkün kılardı — yani bu dilimin kapattığı
    yarışın tamamı tek bir operatör satırıyla geri gelirdi.
    """
    metin = PROD_COMPOSE.read_text(encoding="utf-8")
    satirlar = [s.strip() for s in metin.splitlines() if s.strip().startswith("AUTO_MIGRATE:")]
    assert satirlar == ['AUTO_MIGRATE: "false"'], satirlar

    # Taban compose'un `true` varsayılanı DEĞİŞMEDİ: geliştirme ve CI o yola
    # dayanıyor ve bu dilim onu KIMILDATMIYOR.
    taban = TABAN_COMPOSE.read_text(encoding="utf-8")
    assert "AUTO_MIGRATE: ${AUTO_MIGRATE:-true}" in taban


def test_URETIM_ORNEGI_de_false_diyor_ve_SEBEBINI_YAZIYOR() -> None:
    """Operator dosyasi ile compose CELISMEMELI.

    SEC-5 (#94) `.env.production.example`e `AUTO_MIGRATE=true` yazmisti.
    Compose degeri SABITLEDIGI icin oradaki deger zaten ETKISIZDIR -- ama
    `true` yazan bir ornek, operatore uretimde auto-migrate'in ACIK oldugunu
    soyler ve bu YANLISTIR. Satir `false` olmakla kalmiyor, ETKISIZ oldugunu
    da SOYLUYOR: o cumle olmasa dosya "belgeli ama etkisiz ayar" sinifina
    girerdi -- `test_env_example_completeness.py`nin var olma sebebi.
    """
    satirlar = URETIM_ORNEK.read_text(encoding="utf-8").splitlines()
    (i,) = [n for n, s in enumerate(satirlar) if s.startswith("AUTO_MIGRATE=")]
    assert satirlar[i] == "AUTO_MIGRATE=false", satirlar[i]
    yorum = satirlar[i - 1]
    assert yorum.startswith("#"), yorum
    assert "docker-compose.prod.yml" in yorum and "sunucu-deploy.sh" in yorum, yorum


def test_AUTO_MIGRATE_KOD_VARSAYILANI_ACIK_KALDI() -> None:
    """CI ve geliştirme bu varsayılana dayanıyor; kapatmak ikisini de kırardı."""
    from app.config import Settings

    assert Settings.model_fields["auto_migrate"].default is True

    ci = CI_YML.read_text(encoding="utf-8")
    assert "-e AUTO_MIGRATE=true" in ci, (
        "CI duman testi AUTO_MIGRATE=true ile koşuyordu; bu satır kaybolduysa "
        "konteyner artık şemasız açılıyor demektir"
    )


def test_CI_COZUMLENMIS_AUTO_MIGRATE_i_false_diye_CIVILIYOR() -> None:
    """ci.yml LAFZEN okunur: kapı dosyadaki satırda DEĞİL, çözümlenmiş üründe.

    (Bu ayrı bir testtir ve bilerek: compose dosyasına bakan iddia,
    ci.yml'deki kapı silindiğinde KIMILDAMAZDI.)
    """
    ci = CI_YML.read_text(encoding="utf-8")
    assert ".services.app.environment.AUTO_MIGRATE" in ci, (
        "ci.yml'de çözümlenmiş AUTO_MIGRATE kapısı yok"
    )
    assert 'app_auto_migrate" = "false"' in ci


def test_DEPLOY_BETIGI_GOCU_ve_TOHUMU_up_d_den_ONCE_SURUYOR() -> None:
    """SIRA SÖZLEŞMENİN KENDİSİDİR: göç -> tohum -> `up -d app`.

    Sıra bozulursa işçiler henüz sürülmemiş bir şemada açılır ve kapı onları
    (doğru biçimde) reddeder — yani deploy her seferinde düşerdi.
    """
    metin = DEPLOY_BETIGI.read_text(encoding="utf-8")

    goc = metin.index(KOMUT_GOVDESI)
    tohum = metin.index("python -m app.bootstrap_data")
    # `up -d app`in DEPLOY ADIMI olanı: kurtarma dalındaki geri alma çağrıları
    # `>/dev/null` ile biter, asıl adım satır sonuyla.
    servis = metin.index('echo "== 8/8 Servis güncelleniyor =="')

    assert goc < tohum < servis, (goc, tohum, servis)
    assert "run --rm --no-deps app" in metin


def test_HATA_METNINDEKI_KOMUT_DEPLOY_BETIGINDEKIYLE_AYNI() -> None:
    """Operatöre gösterilen komut, betiğin gerçekten koştuğu komut olmalı.

    İkisi ayrı ayrı yazılsaydı biri değiştiğinde diğeri sessizce YANLIŞ bir
    komut göstermeye devam ederdi — bir üretim olayında en pahalı sapma budur.
    """
    from app.main import ALEMBIC_UPGRADE_ARGV, KOMUT_ALEMBIC_UPGRADE

    assert ALEMBIC_UPGRADE_ARGV == KOMUT_GOVDESI
    assert ALEMBIC_UPGRADE_ARGV in KOMUT_ALEMBIC_UPGRADE
    assert KOMUT_GOVDESI in DEPLOY_BETIGI.read_text(encoding="utf-8")


def test_ITHALDE_YAZAN_TEK_YOL_auto_migrate_KOSULUNUN_ARDINDA() -> None:
    """Modül gövdesinde `_semayi_hazirla()`yı çağıran BAŞKA bir yer yok.

    İkinci bir koşulsuz çağrı, davranış testlerinin gördüğü tek yolu es
    geçerdi.
    """
    import ast

    agac = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    korumasiz = [
        dugum for dugum in agac.body
        if isinstance(dugum, ast.Expr)
        and isinstance(dugum.value, ast.Call)
        and isinstance(dugum.value.func, ast.Name)
        and dugum.value.func.id in {"_semayi_hazirla", "_semayi_dogrula"}
    ]
    assert korumasiz == [], "modül gövdesinde KORUMASIZ şema çağrısı var"

    kosullu = [
        dugum for dugum in agac.body
        if isinstance(dugum, ast.If)
        and ast.dump(dugum.test) == ast.dump(
            ast.parse("settings.auto_migrate", mode="eval").body
        )
    ]
    assert len(kosullu) == 1, "ithal yolu tam olarak BİR `settings.auto_migrate` koşulu"


@pytest.mark.parametrize("ad", ["_semayi_dogrula", "_semayi_hazirla"])
def test_SALT_OKUNUR_YOL_KILIT_ALMIYOR(ad: str) -> None:
    """`_semayi_dogrula` kilit ALMAZ, `_semayi_hazirla` ALIR.

    Doğrulama kilit alsaydı N işçi yine tek kilit için sıraya girerdi ve bu
    dilim hiçbir şey değiştirmemiş olurdu.
    """
    import ast

    agac = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    (islev,) = [d for d in agac.body if isinstance(d, ast.FunctionDef) and d.name == ad]
    kilitler = [
        d for d in ast.walk(islev)
        if isinstance(d, ast.Call)
        and isinstance(d.func, ast.Name)
        and d.func.id == "database_bootstrap_lock"
    ]
    beklenen = 1 if ad == "_semayi_hazirla" else 0
    assert len(kilitler) == beklenen, (ad, len(kilitler))
