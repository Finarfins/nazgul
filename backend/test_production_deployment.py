from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ComposeLoader(yaml.SafeLoader):
    pass


ComposeLoader.add_constructor(
    "!override", lambda loader, node: loader.construct_sequence(node)
)
#: ``!reset`` Compose'un birleştirme sırasında bir anahtarı TAMAMEN kaldırma
#: etiketidir (Compose >= 2.24). Üretimde ``app.build`` bunun için kullanılıyor:
#: base docker-compose.yml'deki derleme bloğu prod'a sızarsa, ``pull``
#: başarısız olduğunda Compose imajı yerelde derlemeye düşebilir. Yükleyici bu
#: etiketi tanımazsa dosya hiç parse edilemez, o yüzden burada da tanımlı.
#:
#: Değer olarak ``None`` DEĞİL, kendine özgü bir nöbetçi döner. ``None`` seçilseydi
#: "``!reset`` ile düşürülmüş anahtar" ile "değeri boş bırakılmış anahtar" ayırt
#: edilemezdi; ``build:`` satırı yanlışlıkla boş bırakıldığında test yeşil kalır,
#: oysa Compose o hâlde base'in derleme bloğunu MİRAS ALIR.
RESET = object()

ComposeLoader.add_constructor("!reset", lambda loader, node: RESET)


def _compose_yukle(ad: str) -> dict:
    return yaml.load((ROOT / ad).read_text(encoding="utf-8"), Loader=ComposeLoader)


def cozumlenmis_app_servisi() -> dict:
    """``-f docker-compose.yml -f docker-compose.prod.yml`` birleşiminde app servisi.

    Compose anahtarları -f dosyaları arasında birleştirir; ``!reset`` ile
    işaretlenmiş anahtar sonuçtan TAMAMEN düşer. Burada modellenen tam olarak
    bu iki kural — ve yalnız bu kadarı, çünkü test edilen değişmez tek bir
    anahtarın (``build``) birleşme sonrası varlığı.

    Compose'un tam çözümleme semantiği burada YENİDEN YAZILMAZ; gerçek
    ``docker compose config`` çıktısı üzerinden doğrulama
    ``deploy/deploy-sozlesme-testi.sh`` (A2) ve CI'daki "İmaj ↔ compose
    sözleşmesi" kapısında yapılır. Bu test onların Docker'sız, hızlı ikizidir:
    ``docker`` bulunmayan bir makinede de regresyonu yakalar.
    """
    base = _compose_yukle("docker-compose.yml")["services"]["app"]
    prod = _compose_yukle("docker-compose.prod.yml")["services"]["app"]
    birlesik = {**base, **prod}
    return {ad: deger for ad, deger in birlesik.items() if deger is not RESET}


def test_production_compose_hides_app_and_adds_tls_proxy_and_gate() -> None:
    compose = yaml.load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    services = compose["services"]
    assert set(("app", "db", "proxy", "gate")).issubset(services)
    assert services["app"]["ports"] == []
    app = services["app"]
    assert app["environment"]["ENVIRONMENT"] == "production"
    assert "DATABASE_URL must be set in .env.production" in app["environment"]["DATABASE_URL"]
    assert "BOOTSTRAP_ADMIN_PASSWORD must be set in .env.production" in app["environment"]["BOOTSTRAP_ADMIN_PASSWORD"]
    assert app["environment"]["COOKIE_SECURE"] == "true"
    assert app["environment"]["NOTIFICATION_PROVIDER"].startswith(
        "${NOTIFICATION_PROVIDER:?"
    )
    assert app["environment"]["SMTP_HOST"].startswith("${SMTP_HOST:?")
    assert app["environment"]["SMTP_PORT"] == "${SMTP_PORT:-587}"
    assert app["environment"]["SMTP_USERNAME"] == "${SMTP_USERNAME:-}"
    assert app["environment"]["SMTP_PASSWORD"] == "${SMTP_PASSWORD:-}"
    assert app["environment"]["SMTP_FROM_EMAIL"].startswith("${SMTP_FROM_EMAIL:?")
    assert app["environment"]["SMTP_FROM_NAME"] == "${SMTP_FROM_NAME:-}"
    assert app["environment"]["SMTP_USE_TLS"] == "${SMTP_USE_TLS:-true}"
    assert app["environment"]["PUBLIC_APP_URL"].startswith("https://${APP_DOMAIN:?")
    assert "SMTP_ENABLED" not in app["environment"]
    # İmaj KANONİK depodan gelmeli. CI imajı `ghcr.io/${{ github.repository }}`
    # = ghcr.io/finarfins/nazgul'a yayınlıyor; compose başka bir depoyu
    # gösterirse deploy ya imaj bulamaz ya da yeniden adlandırma öncesi
    # paketten BAYAT bir imaj çeker. Bu sapma bir kez gerçekleşti ve hiçbir
    # kapı görmedi.
    assert app["image"].startswith("ghcr.io/finarfins/nazgul:"), app["image"]
    # `build` üretimde `!reset` ile düşürülmüş olmalı. Burada YALNIZ etiketin
    # doğru anahtara uygulandığı görülür; birleşme sonrası anahtarın gerçekten
    # yok olduğu ayrı testte iddia edilir (test_cozumlenmis_app_derleme...).
    assert app["build"] is RESET, app["build"]
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert app["security_opt"] == ["no-new-privileges:true"]
    assert services["proxy"]["ports"] == ["80:80", "443:443", "443:443/udp"]
    assert services["proxy"]["read_only"] is True
    assert services["gate"]["build"]["target"] == "test"
    assert services["gate"]["read_only"] is True


def test_cozumlenmis_app_derleme_anahtari_icermez() -> None:
    """Birleşmiş üretim yapılandırmasında ``app.build`` HİÇ BULUNMAMALI.

    Üretimde imaj derlenmez, indirilir. ``build`` ayakta kalırsa ``pull``
    başarısız olduğunda (yanlış etiket, GHCR kimlik hatası, ağ kesintisi)
    Compose imajı sunucuda DERLEMEYE düşebilir — 1 GB'lik kutuda Vite derlemesi
    2026-08-05'te makineyi takasa düşürmüştü. Kapatılan yol tam olarak budur.
    """
    base_app = _compose_yukle("docker-compose.yml")["services"]["app"]
    # Testin boşa dönmediğinin kanıtı: base gerçekten bir `build` taşıyor,
    # yani düşürülecek bir şey VAR. Base bir gün `build`i bırakırsa bu satır
    # kırmızı yanar ve aşağıdaki iddia sessizce anlamsızlaşmaz.
    assert "build" in base_app, "base compose'ta app.build yok; test artık bir şey ölçmüyor"

    app = cozumlenmis_app_servisi()
    assert "build" not in app, sorted(app)
    # Derleme düşerken imajın kendisi kaybolmamalı; aksi hâlde `up -d`
    # çalıştıracak hiçbir şey bulamaz.
    assert app["image"].startswith("ghcr.io/finarfins/nazgul:"), app["image"]


#: e-Fatura yapılandırmasının compose'dan uygulamaya geçirilmesi gereken
#: değişkenleri ve beklenen passthrough biçimi. ``EINVOICE_SENDER_VKN``
#: KASITLI olarak yok: ölü bir alan (gönderici VKN companies.tax_number'dan
#: okunur), compose'a girmesi onu canlıymış gibi gösterirdi.
EINVOICE_PASSTHROUGH = {
    "EINVOICE_PROVIDER": "${EINVOICE_PROVIDER:-noop}",
    "EINVOICE_BASE_URL": "${EINVOICE_BASE_URL:-}",
    "EINVOICE_USERNAME": "${EINVOICE_USERNAME:-}",
    "EINVOICE_PASSWORD": "${EINVOICE_PASSWORD:-}",
    "EINVOICE_API_KEY": "${EINVOICE_API_KEY:-}",
    "EINVOICE_ENDPOINTS_VERIFIED": "${EINVOICE_ENDPOINTS_VERIFIED:-false}",
    "IZIBIZ_ENV": "${IZIBIZ_ENV:-test}",
}


def _documented_keys(example_name: str) -> set[str]:
    keys: set[str] = set()
    for line in (ROOT / example_name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def test_einvoice_configuration_reaches_the_container_without_hard_failing_boot() -> None:
    """e-Fatura değişkenleri geçirilir ama HİÇBİRİ ``:?`` değildir.

    ``:?`` SMTP'de doğrudur: e-posta sessizce kaybolmaktansa uygulama hiç
    açılmasın. e-Fatura'da aynı seçim yanlış olurdu — yapılandırılmamış bir
    entegrasyon yüzünden tüm ERP kapanırdı. Fail-closed davranış compose'da
    değil, gönderim ucunda yaşıyor (503, app/routers/invoices.py).
    """
    compose = yaml.load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    environment = compose["services"]["app"]["environment"]
    for name, expected in EINVOICE_PASSTHROUGH.items():
        assert environment.get(name) == expected, name
        assert ":?" not in str(environment[name]), name
    # Ölü değişken compose'a sızmamalı.
    assert "EINVOICE_SENDER_VKN" not in environment


def test_einvoice_variables_are_documented_in_both_env_examples() -> None:
    """Operatörün dolduracağı her değişken şablonlarda anahtar olarak durur."""
    for example in (".env.production.example", "backend/.env.example"):
        documented = _documented_keys(example)
        missing = set(EINVOICE_PASSTHROUGH) - documented
        assert not missing, f"{example} eksik: {sorted(missing)}"


def test_env_examples_carry_no_real_einvoice_credentials() -> None:
    """Şablonlarda gerçek kimlik bilgisi olamaz: kullanıcı adı/parola BOŞ kalır."""
    for example in (".env.production.example", "backend/.env.example"):
        for line in (ROOT / example).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() in ("EINVOICE_USERNAME", "EINVOICE_PASSWORD", "EINVOICE_API_KEY"):
                assert value.strip() == "", f"{example}: {key} boş olmalı"


def test_runtime_image_remains_non_root_and_test_tools_are_separate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM runtime-base AS test" in dockerfile
    assert "FROM runtime-base AS production" in dockerfile
    production = dockerfile.split("FROM runtime-base AS production", 1)[1]
    assert "USER app" in production
    # requirements-dev.txt uretim stage'inde KURULMAMALI. Onceki iddia bunu
    # "metinde HIC gecmesin" diye olcuyordu; o vekil, dosyayi SILEN satirin
    # kendisini de reddediyor, yani KURULUM ile KALDIRMA'yi ayirt etmiyordu --
    # oysa silme, iddianin korudugu seyin TA KENDISIDIR. Iddia artik yone bakar.
    calisan = [
        satir.strip()
        for satir in production.splitlines()
        if satir.strip() and not satir.strip().startswith("#")
    ]
    kurulum = [s for s in calisan if "pip install" in s and "requirements-dev" in s]
    assert not kurulum, f"production stage dev bagimliliklarini kuruyor: {kurulum}"
    silme = [
        s
        for s in calisan
        if s.startswith("RUN rm -rf") and "/app/backend/requirements-dev.txt" in s
    ]
    assert silme, "production stage requirements-dev.txt'i SILMIYOR"


def test_reverse_proxy_security_headers_and_forwarding_are_declared() -> None:
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    for expected in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "header_up X-Forwarded-Proto",
    ):
        assert expected in caddy


def test_reverse_proxy_health_check_tolerates_a_single_slow_probe() -> None:
    # Regresyon (31 Tem 2026 17:29 UTC): health_fails yazılmadığı için varsayılan 1'di,
    # tek bir 3s probe timeout'u tek upstream'i DOWN işaretliyor ve bir sonraki probe'a
    # kadar (15s) her istek upstream'e hiç gitmeden 503 dönüyordu.
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    block = caddy.split("reverse_proxy app:5050 {", 1)[1].split("\n    }", 1)[0]
    for expected in (
        "health_uri /api/live",
        "health_interval 10s",
        "health_timeout 8s",
        "health_fails 3",
        "health_passes 1",
    ):
        assert expected in block, expected
    # Probe'lar üst üste binmemeli: bir kontrol bir sonraki tick'ten önce bitmeli.
    interval = int(re.search(r"health_interval (\d+)s", block).group(1))
    timeout = int(re.search(r"health_timeout (\d+)s", block).group(1))
    assert timeout < interval


def test_production_gate_rejects_placeholder_or_short_password(tmp_path, monkeypatch) -> None:
    # Static guarantee: the executable gate contains minimum secret and placeholder checks.
    gate = (ROOT / "scripts" / "production_gate.py").read_text(encoding="utf-8")
    assert 'len(password) < 24' in gate
    assert '"CHANGE" in password.upper()' in gate
    assert 'docker", "compose"' in gate
