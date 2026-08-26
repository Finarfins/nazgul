"""Guard: env examples and the compose environment map agree, BOTH directions.

docker-compose.yml sources required vars from .env.docker, and the production
override adds more required vars sourced from .env.production. Production runs
the base + override merged, so .env.production must satisfy BOTH files.

YÖN 1 -- BELGESİZ ZORUNLU DEĞİŞKEN. Bir `${VAR:?...}` örnek dosyada yoksa
deploy, değeri olmayan bir değişkenle durur.

YÖN 2 -- BELGELİ AMA ETKİSİZ AYAR (bu dosyanın yeni kuralı). `--env-file`
YALNIZ ``${...}`` yerine koyma içindir; bir değişken app konteynerinin
ortamına ANCAK bir ``environment:`` girdisiyle ulaşır (SUNGUR_DATA_DIR için
ölçüldü, bkz. docker-compose.prod.yml'deki yorum bloğu). Taşınmayan bir ayar
.env.production'a yazılınca SESSİZCE etkisiz kalır: uygulama kod
varsayılanında koşarken operatör onu ayarlamış sanır.

    KURAL, ÖRNEK DEĞİL. Bu dosya bir zamanlar TEK bir çifti (outbox
    anahtarı + aralığı) adıyla sabitliyordu. O bir kuralın DEĞİL, bir
    örneğin donmuş hâliydi ve ölçüldü: aynı sınıftan ALTI ayar daha
    sessizce kod varsayılanına düşüyordu ve hiçbir test bunu görmüyordu.
    Kural artık `Settings` alanlarının TAMAMI üzerinden koşar; bilinen
    taşınmayanlar ADIYLA ve TARİHİYLE aşağıdaki muafiyet listesindedir.

MUAFİYET LİSTESİ KENDİ KENDİNİ TEMİZLER: bir muaf değişken artık
taşınıyorsa ya da artık belgeli değilse test KIRMIZI yanar ve satırın
silinmesini ister. Yoksa liste, kapattığı kusuru saklayan bayat bir örtüye
dönerdi.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")
#: `environment:` bloğu içindeki ``AD:`` satırları.
ENV_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*):")
DOC_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")

#: ÜRETİM yığınında BELGELİ ama app konteynerine TAŞINMAYAN ayarlar.
#: 2026-08-26'da ölçüldü (#95 yokluk incelemesi). Hepsi gerçek `Settings`
#: alanı, yani bugün .env.production'a yazılan değerleri UYGULAMAYA HİÇ
#: ULAŞMIYOR ve uygulama kod varsayılanında koşuyor. Taşımak bu dalın işi
#: DEĞİL — ayrı iş olarak sıradadır; buradaki liste onları GÖRÜNÜR yapar.
MUAF_URETIM: dict[str, str] = {
    "PAYMENT_ALLOCATION_ENGINE_ENABLED":
        "tahsis motoru anahtarı; kod varsayılanı False",
    "PAYMENT_ALLOCATION_CLOSED_THROUGH":
        "kapalı dönem sınırı (örnekte yorumlu); kod varsayılanı None",
    "SUNGUR_PLATFORM_OPERATORS":
        "platform operatörü listesi; kod varsayılanı boş dize",
    "BACKUP_RETENTION_COUNT":
        "yedek saklama adedi; kod varsayılanı 14",
    "BACKUP_LOCK_TIMEOUT_SECONDS":
        "yedek kilit üstü; kod varsayılanı 0 (SINIRSIZ)",
    "RESTORE_DRAIN_TIMEOUT_SECONDS":
        "geri yükleme boşaltma üstü; kod varsayılanı 60",
}

#: GELİŞTİRME (yalın docker-compose.yml) yığını için aynı ölçüm, aynı tarih.
#: Üretim örtüsü bunların bir kısmını taşıyor; taban yığın taşımıyor.
MUAF_GELISTIRME: dict[str, str] = dict(
    MUAF_URETIM,
    TRUSTED_PROXY_CIDRS="taban yığında taşınmıyor; üretim örtüsü taşıyor",
    TURNSTILE_SECRET_KEY="taban yığında taşınmıyor; üretim örtüsü taşıyor",
    TURNSTILE_SITE_KEY=(
        "taban yığında yalnız `build.args` içinde kullanılıyor (Vite paketine "
        "gömülür); konteyner ortamına taşınmıyor"
    ),
)


def _required_vars(*compose_files: str) -> set[str]:
    names: set[str] = set()
    for name in compose_files:
        names |= set(REQUIRED_RE.findall((ROOT / name).read_text(encoding="utf-8")))
    return names


def _documented_keys(example_name: str) -> set[str]:
    """Örnek dosyada ADI GEÇEN her değişken; YORUMLU satırlar DAHİL.

    Yorumlu bir `# VAR=...` satırı da bir BELGEDİR: operatör onu açıp
    kullanır. Taşınmıyorsa açtığı gün sessizce etkisiz kalır, yani bu
    dosyanın kapattığı kusur yorumlu satırlar için de geçerlidir.
    """
    keys: set[str] = set()
    for line in (ROOT / example_name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped[1:].strip()
        eslesme = DOC_KEY_RE.match(stripped)
        if eslesme:
            keys.add(eslesme.group(1))
    return keys


def _forwarded_keys(*compose_files: str) -> set[str]:
    """YALNIZ `app` servisinin `environment:` haritasıyla taşınanlar.

    Compose, `-f` dosyaları arasında `environment` haritalarını BİRLEŞTİRİR;
    üretim yığını taban + örtüdür, bu yüzden çağıran ikisini birden verir.

    SERVİS AYRIMI BİLİNÇLİ: `db` servisinin haritası app konteynerine HİÇBİR
    şey taşımaz. Servis ayrımı olmadan, yanlış servisin altına yazılmış bir
    değişken "taşınıyor" sayılırdı — yani bu dosyanın yakalamak için var
    olduğu kusurun ta kendisi kuralı GEÇERDİ.
    """
    names: set[str] = set()
    for name in compose_files:
        servis: str | None = None
        blokta = False
        girinti = 0
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            servis_basi = re.match(r"^  ([A-Za-z_][\w-]*):\s*$", line)
            if servis_basi:
                servis, blokta = servis_basi.group(1), False
                continue
            basi = re.match(r"^(\s*)environment:\s*$", line)
            if basi:
                blokta, girinti = True, len(basi.group(1))
                continue
            if not blokta or servis != "app" or not line.strip():
                continue
            if len(line) - len(line.lstrip()) <= girinti:
                blokta = False
                continue
            anahtar = ENV_KEY_RE.match(line)
            if anahtar:
                names.add(anahtar.group(1))
    return names


def _settings_env_names() -> set[str]:
    """`Settings` alanlarının ORTAM DEĞİŞKENİ adları.

    Kaynak `app/config.py`nin KENDİSİ: elle yazılmış ayar listesi YOK, yani
    yarın eklenen bir ayar bu kurala kendiliğinden girer. Alan adı büyük
    harfe çevrilir; modelde takma ad (`alias`) ya da önek kullanılmıyor.
    """
    from app.config import Settings

    return {ad.upper() for ad in Settings.model_fields}


def _tasinmayanlar(ornek: str, muaf: dict[str, str], *compose: str) -> set[str]:
    belgelenen_ayarlar = _documented_keys(ornek) & _settings_env_names()
    return belgelenen_ayarlar - _forwarded_keys(*compose) - set(muaf)


def test_env_docker_example_covers_base_compose():
    missing = _required_vars("docker-compose.yml") - _documented_keys(".env.docker.example")
    assert not missing, f".env.docker.example missing required vars: {sorted(missing)}"


def test_env_production_example_covers_merged_prod_stack():
    required = _required_vars("docker-compose.yml", "docker-compose.prod.yml")
    missing = required - _documented_keys(".env.production.example")
    assert not missing, f".env.production.example missing required vars: {sorted(missing)}"


def test_belgelenen_her_URETIM_ayari_konteynere_TASINIYOR():
    """KURAL: .env.production.example'da belgeli her ayar TAŞINMALI."""
    tasinmayan = _tasinmayanlar(
        ".env.production.example", MUAF_URETIM,
        "docker-compose.yml", "docker-compose.prod.yml",
    )
    assert not tasinmayan, (
        "Bu ayar(lar) .env.production.example'da BELGELİ ama üretim app "
        f"konteynerine TAŞINMIYOR: {sorted(tasinmayan)}. `--env-file` yalnız "
        "${...} yerine koyma içindir; .env.production'a yazılan değer "
        "uygulamaya HİÇ ULAŞMAZ ve uygulama kod varsayılanında koşar — "
        "operatör onu ayarlamış sanarak. Ya docker-compose.prod.yml'deki "
        "`environment:` haritasına ekleyin, ya da MUAF_URETIM listesine "
        "TARİHİ ve GEREKÇESİYLE yazın."
    )


def test_belgelenen_her_GELISTIRME_ayari_konteynere_TASINIYOR():
    """Aynı kural, taban (geliştirme) yığını için."""
    tasinmayan = _tasinmayanlar(
        ".env.docker.example", MUAF_GELISTIRME, "docker-compose.yml",
    )
    assert not tasinmayan, (
        "Bu ayar(lar) .env.docker.example'da BELGELİ ama taban yığının app "
        f"konteynerine TAŞINMIYOR: {sorted(tasinmayan)}. Ya "
        "docker-compose.yml'deki `environment:` haritasına ekleyin, ya da "
        "MUAF_GELISTIRME listesine TARİHİ ve GEREKÇESİYLE yazın."
    )


def test_MUAFIYET_listeleri_BAYAT_DEGIL():
    """Muafiyet, kapattığı kusuru SAKLAYAN bayat bir örtüye dönüşmemeli.

    İki yönde de bayatlar: değişken artık taşınıyorsa muafiyet GEREKSİZDİR
    (ve orada durursa gerçek bir kuralı sessizce delik bırakır); artık
    belgeli değilse muafiyet OLMAYAN bir şeyi anlatıyordur.
    """
    for ad, muaf, ornek, compose in (
        ("MUAF_URETIM", MUAF_URETIM, ".env.production.example",
         ("docker-compose.yml", "docker-compose.prod.yml")),
        ("MUAF_GELISTIRME", MUAF_GELISTIRME, ".env.docker.example",
         ("docker-compose.yml",)),
    ):
        belgelenen = _documented_keys(ornek)
        tasinan = _forwarded_keys(*compose)
        ayarlar = _settings_env_names()

        artik_tasiniyor = sorted(set(muaf) & tasinan)
        assert not artik_tasiniyor, (
            f"{ad} içindeki şu değişken(ler) ARTIK TAŞINIYOR: "
            f"{artik_tasiniyor}. Muafiyet satırını SİLİN; yoksa kural o "
            "değişken için sessizce kapalı kalır."
        )
        belgesiz = sorted(set(muaf) - belgelenen)
        assert not belgesiz, (
            f"{ad} içindeki şu değişken(ler) {ornek} içinde ARTIK BELGELİ "
            f"DEĞİL: {belgesiz}. Muafiyet olmayan bir şeyi anlatıyor; satırı "
            "SİLİN."
        )
        ayar_degil = sorted(set(muaf) - ayarlar)
        assert not ayar_degil, (
            f"{ad} içindeki şu ad(lar) artık bir `Settings` alanı DEĞİL: "
            f"{ayar_degil}. Kural yalnız ayarlar üzerinde koşar; satırı SİLİN."
        )


def test_outbox_anahtarinin_FAIL_SAFE_varsayilani_SABIT():
    """Taşınmak YETMEZ: taşınan değerin varsayılanı da GÜVENLİ olmalı.

    Bu iddia bilerek DAR ve değişkene özeldir: yukarıdaki kural bir ayarın
    taşınıp taşınmadığını söyler, hangi varsayılanın güvenli olduğunu ise
    SÖYLEYEMEZ — o her değişken için ayrı bir karardır. Burada karar şudur:
    değeri eksik bir deploy tüketiciyi AÇMAMALI (envanteri değiştirir) ve
    aralık kod varsayılanıyla aynı kalmalı.
    """
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for var, varsayilan in (
        ("FIELD_STOCK_OUTBOX_ENABLED", "false"),
        ("FIELD_STOCK_OUTBOX_INTERVAL_SECONDS", "30"),
    ):
        assert re.search(
            rf"^\s+{var}: \$\{{{var}:-{varsayilan}\}}\s*$", prod, re.M
        ), (
            f"{var} için compose fail-safe varsayılanı {varsayilan!r} "
            "değil; değeri eksik bir deploy tüketiciyi yanlış tarafa "
            "açabilir."
        )
