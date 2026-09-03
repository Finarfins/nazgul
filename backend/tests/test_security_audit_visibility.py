"""Kapı: hiçbir denetim olayı GÖRÜNMEZ kalmamalı.

Üç ayrı kusur tek özelliğin parçalarıydı ve üçü de burada çivilenir:

1. YAZICI YUTUYORDU. ``_write_security_audit`` her istisnayı yakalayıp yalnız
   log yazıyordu; insert başarısız olduğunda istek normal dönüyor, satır
   sessizce kayboluyordu. Kaybolanlar tam olarak bir saldırganın ürettiği
   olaylardı. Artık olay yedek çıkışa düşer VE ``/api/ready`` kırmızıya döner —
   ama sıradan kullanıcının isteği DÜŞMEZ.
2. REDDEDİLEN İSTEĞİN FİRMASI YAZILMIYORDU. ``COMPANY_ACCESS_DENIED`` olayı,
   sınırı yoklanan firmanın denetim izine aittir. İSTENEN firma yazılır;
   hiçbir firma belirtmemiş isteğe varsayılan seçmek YASAK kalır.
3. FİRMASIZ SATIRLAR OKUNAMIYORDU. ``GET /api/audit`` kiracıya göre süzer, yani
   firmasız satırlar hiçbir okumada görünmüyordu. Asıl şikâyet buydu; CHECK
   kısıtı tek başına onu kapatmaz, yalnız yerini değiştirirdi.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "DenetimKapi!2026x"
ANAHTAR = "denetim-kapisi-anahtar-denetim-kapisi-anahtar"


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


def _alt_surec(kod: str, ortam: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Env'e bağlı ``app.main`` içe aktarması ebeveynin ``sys.modules``ine dokunmaz.

    Şekil ``test_donmus_saat_kapisi._alt_surec`` ve
    ``test_tenant_scoping_guard`` canlı-envanter çocuğu ile aynı: ``python -c``,
    kopyalanmış env, ``cwd=backend``.
    """
    return subprocess.run(
        [sys.executable, "-c", kod],
        env=ortam,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=120,
    )


def _temiz_ortam(tmp_path: Path, veritabani_adi: str) -> dict[str, str]:
    ortam = os.environ.copy()
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["PYTHONIOENCODING"] = "utf-8"
    ortam["DATABASE_URL"] = f"sqlite:///{(tmp_path / veritabani_adi).as_posix()}"
    ortam["SECRET_KEY"] = ANAHTAR
    ortam["BOOTSTRAP_ADMIN_PASSWORD"] = PAROLA
    ortam["SUNGUR_PLATFORM_OPERATORS"] = "1"
    return ortam


def _cocukta_sema_kur(ortam: dict[str, str]) -> None:
    kod = (
        "from sqlalchemy import text\n"
        "import app.main as main\n"
        "from app.config import settings\n"
        "from app.db import engine\n"
        "with engine.begin() as connection:\n"
        "    connection.execute(text('UPDATE app_users SET must_change_password=0'))\n"
        "print('DENETIM_KURULDU')\n"
        "print('OPERATORLER', settings.sungur_platform_operators)\n"
    )
    tamamlanan = _alt_surec(kod, ortam, BACKEND)
    assert tamamlanan.returncode == 0, tamamlanan.stdout + tamamlanan.stderr
    assert "DENETIM_KURULDU" in tamamlanan.stdout, tamamlanan.stdout + tamamlanan.stderr
    assert "OPERATORLER 1" in tamamlanan.stdout, tamamlanan.stdout


def _ebeveyn_app_modullerini_geri_yukle(onceki: dict[str, object]) -> None:
    for name in list(_app_anahtarlari()):
        del sys.modules[name]
    sys.modules.update(onceki)


@contextmanager
def _uygulama_baglam(tmp_path, monkeypatch):
    """Taze şema: env'e bağlı içe aktarma ALT SÜREÇTE; ebeveyn app.* geri yüklenir.

    Göç ve tohum çocuğun sürecindedir (``DATABASE_URL``, ``SECRET_KEY``,
    ``BOOTSTRAP_ADMIN_PASSWORD``, ``SUNGUR_PLATFORM_OPERATORS``). Ebeveynde
    TestClient için alınan kopya çıkışta anlık görüntüyle değiştirilir:
    ``set(k for k in sys.modules if k=='app' or k.startswith('app.'))``
    fikstürden önce ve sonra özdeştir. Eski zehir (``del sys.modules`` ile
    ebeveyni boş bırakmak) yoktur.
    """
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    ortam = _temiz_ortam(tmp_path, "denetim.db")
    _cocukta_sema_kur(ortam)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    for name in list(_app_anahtarlari()):
        del sys.modules[name]
    monkeypatch.setenv("DATABASE_URL", ortam["DATABASE_URL"])
    monkeypatch.setenv("SECRET_KEY", ANAHTAR)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    monkeypatch.setenv("SUNGUR_PLATFORM_OPERATORS", "1")
    monkeypatch.syspath_prepend(str(BACKEND))

    import app.main as main
    from app.db import engine

    # Bootstrap admin parola rotasyonu ile başlar; kapı rotasyonu değil denetimi
    # ölçüyor, bu yüzden bayrak düşürülür.
    with engine.begin() as connection:
        connection.execute(text("UPDATE app_users SET must_change_password=0"))

    try:
        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield main, engine, client
    finally:
        _ebeveyn_app_modullerini_geri_yukle(onceki)


@pytest.fixture()
def uygulama(tmp_path, monkeypatch):
    with _uygulama_baglam(tmp_path, monkeypatch) as baglam:
        yield baglam


def test_uygulama_fiksturu_app_sys_modules_anahtarlarini_birakmaz(tmp_path, monkeypatch):
    """Sentinel: ebeveyn ``app`` / ``app.*`` kümesi fikstürden önce ve sonra özdeş."""
    monkeypatch.syspath_prepend(str(BACKEND))
    import app.config  # noqa: F401 — boş==boş her zaman geçer; ebeveynde app.* olsun

    onceki = {k for k in sys.modules if k == "app" or k.startswith("app.")}
    assert onceki, "sentinel kör: app.* yok"
    with _uygulama_baglam(tmp_path, monkeypatch) as (_main, _engine, client):
        assert client is not None
    sonra = {k for k in sys.modules if k == "app" or k.startswith("app.")}
    assert sonra == onceki, (
        f"fikstür app.* sızdırdı: eklenen={sorted(sonra - onceki)} "
        f"silinen={sorted(onceki - sonra)}"
    )


def _giris(client) -> dict:
    cevap = client.post("/api/auth/login", json={"username": "admin", "password": PAROLA})
    assert cevap.status_code == 200, cevap.text
    client.cookies.clear()  # Bearer ile devam: CSRF kapısı kapının konusu değil.
    return {"Authorization": "Bearer " + cevap.json()["access_token"]}


def _satirlar(engine):
    from sqlalchemy import text

    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT company_id, user_id, status_code, path, failure_reason, request_id "
                "FROM security_audit_logs ORDER BY id"
            )
        ).all()


# ---------------------------------------------------------------------------
# 1. YAZIM ARIZASI SESSİZ KALMAMALI — ama isteği de düşürmemeli
# ---------------------------------------------------------------------------


def _kapi_yazim_arizasi_sesli(main, engine, client, etiket: str) -> None:
    """İddia gövdesi; hem KONTROL hem MUTASYON tarafından çağrılır."""
    from sqlalchemy import text

    kayitlar: list[logging.LogRecord] = []

    class Yakala(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            kayitlar.append(record)

    kaydedici = logging.getLogger("yerel_hesap")
    tutucu = Yakala(level=logging.CRITICAL)
    kaydedici.addHandler(tutucu)
    try:
        # Yazım hedefini kaldır: gerçek bir insert arızası, taklit değil.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE security_audit_logs RENAME TO sal_gizli"))

        cevap = client.post(
            "/api/auth/login", json={"username": "saldirgan", "password": "yanlis"}
        )

        # (a) İSTEK YOLU DÜŞMEDİ.
        assert cevap.status_code == 401, (
            f"{etiket}: denetim yazımı bozukken istek yolu da düştü; sıradan "
            f"kullanıcı denetimin arızasından etkilendi (HTTP {cevap.status_code})"
        )

        # (b) OLAY KAYBOLMADI, yedek çıkışta içeriğiyle duruyor.
        kritik = [k for k in kayitlar if "DENETİM KAYDI YAZILAMADI" in k.getMessage()]
        assert kritik, (
            f"{etiket}: denetim yazımı başarısız oldu ve HİÇBİR CRITICAL kayıt "
            "çıkmadı — olay sessizce kayboldu"
        )
        yedek = getattr(kritik[0], "audit_fallback_record", None)
        assert yedek is not None, f"{etiket}: CRITICAL kaydı olayın kendisini taşımıyor"
        assert yedek["path"] == "/api/auth/login", yedek
        assert yedek["status_code"] == 401, yedek
        assert kritik[0].exc_info is not None, f"{etiket}: arızanın nedeni kayda eklenmemiş"

        # (c) ARIZA MAKİNE TARAFINDAN GÖRÜLÜYOR.
        assert not main.audit_sink_healthy(), (
            f"{etiket}: yazım arızasından sonra mandal kalkmadı"
        )
        assert client.get("/api/ready").status_code == 503, (
            f"{etiket}: denetim kaydı yazılamazken /api/ready hâlâ yeşil"
        )
    finally:
        kaydedici.removeHandler(tutucu)
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE sal_gizli RENAME TO security_audit_logs"))


def test_denetim_yazimi_basarisiz_olunca_olay_yedek_cikisa_duser(uygulama):
    main, engine, client = uygulama
    _kapi_yazim_arizasi_sesli(main, engine, client, "KONTROL")


def test_yutan_yazici_geri_gelirse_kapi_kirmiziya_doner(uygulama, monkeypatch):
    """MUTASYON: düzeltme öncesi YUTAN blok geri konur.

    Eski davranış tam olarak buydu: istisna yakalanır, ERROR seviyesinde bir log
    yazılır (olayın İÇERİĞİ olmadan), mandal kalkmaz. Ölçülen sonuç: istek normal
    döner ve satır sessizce kaybolur.
    """
    main, engine, client = uygulama
    from sqlalchemy import insert

    from app.auth import audit_logs
    from app.db import SessionLocal

    def eski_yazici(request, response, started_at, *, failure_reason=None):
        path = request.url.path
        if not (path.startswith("/api") and request.method in {"POST", "PUT", "PATCH", "DELETE"}):
            return
        try:
            with SessionLocal.begin() as db:
                db.execute(insert(audit_logs).values(
                    action=request.method, path=path,
                    status_code=int(response.status_code),
                    created_at=main.utcnow(), outcome="denied",
                ))
        except Exception:
            # YUTMA: yalnız log, mandal yok, olay içeriği yok.
            logging.getLogger("yerel_hesap").exception("Audit kaydı yazılamadı")

    monkeypatch.setattr(main, "_write_security_audit", eski_yazici)

    with pytest.raises(AssertionError) as excinfo:
        _kapi_yazim_arizasi_sesli(main, engine, client, "MUTASYON")
    mesaj = str(excinfo.value)
    assert "HİÇBİR CRITICAL kayıt" in mesaj or "mandal kalkmadı" in mesaj, mesaj
    print(f"MUTATION_RED yutan-yazici: {mesaj}")


def test_mandal_kendiliginden_dusmez(uygulama):
    """Sonraki başarılı yazım, kaybolmuş olayı GERİ GETİRMEZ.

    Mandalı başarıya bağlamak, kapatmaya çalıştığımız sessiz kaybın ta kendisi
    olurdu: tek bir kayıp satır, bir sonraki istekte örtülürdü.
    """
    main, engine, client = uygulama
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE security_audit_logs RENAME TO sal_gizli"))
    client.post("/api/auth/login", json={"username": "saldirgan", "password": "yanlis"})
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE sal_gizli RENAME TO security_audit_logs"))

    # Yazım yolu ONARILDI ve başarıyla yazıyor…
    client.post("/api/auth/login", json={"username": "saldirgan", "password": "yanlis"})
    assert _satirlar(engine), "onarımdan sonra yazım hâlâ çalışmıyor"

    # …ama mandal kalkık kalır.
    assert not main.audit_sink_healthy(), "başarılı bir yazım mandalı sessizce düşürdü"
    assert client.get("/api/ready").status_code == 503


def test_saglikli_yolda_denetim_isteklere_karismaz(uygulama):
    """Ters yön: sıradan trafik mandalı KALDIRMAMALI."""
    main, engine, client = uygulama

    h = _giris(client)
    olustur = client.post("/api/customers", json={"name": "Sıradan Müşteri"}, headers=h)
    assert olustur.status_code == 201, olustur.text

    assert main.audit_sink_healthy(), "sıradan trafik yazım arızası mandalını kaldırdı"
    assert client.get("/api/ready").status_code == 200
    assert _satirlar(engine), "sıradan trafik hiç denetim satırı yazmadı"


# ---------------------------------------------------------------------------
# 2. İSTENEN FİRMA — reddedilse bile yazılır, ama asla UYDURULMAZ
# ---------------------------------------------------------------------------


def _kapi_reddedilen_firma(engine, client, h, etiket: str) -> None:
    """İddia gövdesi; hem KONTROL hem MUTASYON tarafından çağrılır.

    Her çağrı KENDİ ``X-Request-ID``'siyle yalıtılır. Yalıtım olmadan mutasyon
    çağrısı, kontrol çağrısının bıraktığı SAĞLAM satırı okuyup yeşil kalırdı —
    ölçüldü, kapı bu yüzden bir kez yanlışlıkla yeşil geçti.
    """
    # ``X-Request-ID`` ASCII olmak ZORUNDA: hem HTTP başlığı hem de sunucudaki
    # ``REQUEST_ID_RE`` ([A-Za-z0-9._-]) bunu şart koşuyor. Türkçe etiket doğrudan
    # başlığa konulduğunda istemci UnicodeEncodeError veriyor.
    istek_no = "kapi-" + "".join(
        k if k.isascii() and (k.isalnum() or k in "._-") else "-" for k in etiket
    )
    reddedilen = client.post(
        "/api/customers",
        json={"name": "X"},
        headers={**h, "x-company-id": "9999", "x-request-id": istek_no},
    )
    assert reddedilen.status_code == 403, reddedilen.text
    assert reddedilen.json().get("code") == "COMPANY_ACCESS_DENIED", reddedilen.text

    ilgili = [r for r in _satirlar(engine) if r.request_id == istek_no]
    assert ilgili, f"{etiket}: erişimi reddedilen istek hiç denetim satırı yazmadı"
    assert ilgili[-1].company_id == 9999, (
        f"{etiket}: sınırı yoklanan firmanın denetim izine yazılmadı; "
        f"company_id={ilgili[-1].company_id}"
    )


def test_reddedilen_istegin_firmasi_o_firmanin_izine_yazilir(uygulama):
    _main, engine, client = uygulama
    h = _giris(client)
    _kapi_reddedilen_firma(engine, client, h, "KONTROL")


def test_istenen_firma_yazilmazsa_kapi_kirmiziya_doner(uygulama, monkeypatch):
    """MUTASYON: yazıcıdaki ``requested_company_id`` yedeği kaldırılır.

    Mutasyon, düzeltmenin TAM OLARAK kaldırdığı satırı geri koyar; böylece
    kanıtlanan şey bu kapının o satıra bağlı olduğudur.
    """
    main, engine, client = uygulama
    h = _giris(client)
    _kapi_reddedilen_firma(engine, client, h, "MUTASYON öncesi")

    gercek = main._write_security_audit

    def yutan(request, response, started_at, *, failure_reason=None):
        # Düzeltme öncesi davranış: yalnız ÇÖZÜLMÜŞ firma yazılırdı.
        if getattr(request.state, "requested_company_id", None) is not None:
            request.state.requested_company_id = None
        return gercek(request, response, started_at, failure_reason=failure_reason)

    monkeypatch.setattr(main, "_write_security_audit", yutan)

    with pytest.raises(AssertionError) as excinfo:
        _kapi_reddedilen_firma(engine, client, h, "MUTASYON")
    mesaj = str(excinfo.value)
    # İKİ kırmızı biçim de meşru ve ikisi de bu kapıya ait:
    #   - "izine yazılmadı"  -> satır yazıldı ama YANLIŞ firmayla (CHECK yokken),
    #   - "hiç ... yazmadı"  -> satır HİÇ yazılamadı. CHECK varken olan budur:
    #     kimlik çözülmüş + firma NULL biçimi zaten reddedilir. Yani yedek
    #     kaldırıldığında olay yalnız yanlış etiketlenmiyor, TAMAMEN kayboluyor.
    assert (
        "sınırı yoklanan firmanın denetim izine yazılmadı" in mesaj
        or "hiç denetim satırı yazmadı" in mesaj
    ), mesaj
    print(f"MUTATION_RED istenen-firma: {mesaj}")


def test_firma_belirtmeyen_istege_varsayilan_firma_UYDURULMAZ(uygulama):
    """Karşı yön: seçici YOKSA ya da OKUNAMIYORSA firma tahmin edilmez.

    Bu, #57'nin kaldırdığı sessiz KİRACI UYDURMASI'nın geri gelmediğini çivileyen
    iddia. Bu iddia olmadan, "reddedilen isteğin firmasını yaz" kuralı kolayca
    "her isteğe bir firma yaz"a kayardı.
    """
    _main, engine, client = uygulama

    h = _giris(client)
    bozuk = client.post(
        "/api/customers", json={"name": "X"}, headers={**h, "x-company-id": "abc"}
    )
    assert bozuk.status_code == 403, bozuk.text

    # Kimliği doğrulanmamış istek: hiçbir firma belirtilmedi.
    client.post("/api/customers", json={})

    firmasiz = [r for r in _satirlar(engine) if r.company_id is None]
    assert firmasiz, "okunamayan seçici ve kimlik-öncesi istek için satır yazılmadı"
    for satir in firmasiz:
        assert satir.user_id is None, (
            "firmasız bir satır KİMLİK taşıyor: ya firma uydurulmalıydı ya da "
            f"bu satır hiç yazılmamalıydı — {satir}"
        )


# ---------------------------------------------------------------------------
# 3. GÖRÜNMEZLİK — asıl şikâyet
# ---------------------------------------------------------------------------


def test_firmasiz_satirlar_kiraci_okumasinda_gorunmez(uygulama):
    """Kusurun kendisi: kiracı okuması firmasız satırı GÖREMEZ.

    Bu iddia bilerek kusuru DOĞRULAR. Platform yolunun bir şeyi gerçekten
    çözdüğünü söyleyebilmek için önce çözülmemiş hâlin ölçülmesi gerekir.
    """
    _main, engine, client = uygulama

    h = _giris(client)
    client.post("/api/customers", json={})  # AUTH_REQUIRED, firmasız

    kiraci = client.get("/api/audit", headers=h)
    assert kiraci.status_code == 200, kiraci.text
    assert all(r["company_id"] is not None for r in kiraci.json())

    tablodaki_firmasiz = [r for r in _satirlar(engine) if r.company_id is None]
    assert tablodaki_firmasiz, "kapı anlamsız: tabloda hiç firmasız satır yok"


def _kapi_hicbir_satir_gorunmez_kalmaz(engine, client, h, etiket: str) -> None:
    kiraci = client.get("/api/audit", headers=h)
    assert kiraci.status_code == 200, kiraci.text
    platform = client.get("/api/platform/audit", headers=h)
    assert platform.status_code == 200, platform.text

    gorunen = len(kiraci.json()) + len(platform.json())
    toplam = len(_satirlar(engine))
    assert gorunen == toplam, (
        f"{etiket}: {toplam} denetim satırından yalnız {gorunen} tanesi okunabiliyor; "
        f"{toplam - gorunen} satır hiçbir okuma yolunda GÖRÜNMÜYOR"
    )
    assert all(r["company_id"] is None for r in platform.json())


def _karisik_trafik(client, h) -> None:
    client.post("/api/auth/login", json={"username": "hayalet", "password": "yanlis"})
    client.post("/api/customers", json={"name": "Kendi"}, headers=h)
    client.post("/api/customers", json={})


def test_platform_yolu_firmasiz_satirlari_gosterir(uygulama):
    """Ve kapanış: o satırlar BİR YERDE okunabilir olmalı."""
    _main, engine, client = uygulama
    h = _giris(client)
    _karisik_trafik(client, h)
    _kapi_hicbir_satir_gorunmez_kalmaz(engine, client, h, "KONTROL")


def test_platform_yolu_kaldirilinca_kapi_kirmiziya_doner(uygulama, monkeypatch):
    """MUTASYON: platform okuma yolu kiracıya göre süzmeye başlarsa kapı kırmızı.

    Bu, düzeltme ÖNCESİ dünyadır: her okuma ``company_id = ?`` süzer ve firmasız
    satırlar hiçbir yerde görünmez.
    """
    main, engine, client = uygulama
    h = _giris(client)
    _karisik_trafik(client, h)
    _kapi_hicbir_satir_gorunmez_kalmaz(engine, client, h, "MUTASYON öncesi")

    from fastapi.routing import APIRoute

    özgün = list(main.app.router.routes)
    kalanlar = [r for r in özgün if getattr(r, "path", None) != "/api/platform/audit"]
    # Platform ucu, kiracıya göre süzen bir okumaya indirgenir: firmasız satır
    # döndürmez. Düzeltme öncesi dünyanın tam karşılığı.
    kör = APIRoute("/api/platform/audit", endpoint=lambda: [], methods=["GET"])
    main.app.router.routes = [kör] + kalanlar
    try:
        with pytest.raises(AssertionError) as excinfo:
            _kapi_hicbir_satir_gorunmez_kalmaz(engine, client, h, "MUTASYON")
        assert "hiçbir okuma yolunda GÖRÜNMÜYOR" in str(excinfo.value)
        print(f"MUTATION_RED platform-okuma: {excinfo.value}")
    finally:
        main.app.router.routes = özgün


def test_platform_yolu_sıradan_yoneticiye_kapali(uygulama, monkeypatch):
    """Bu satırlar tek bir kiracıya ait değil; tek bir kiracının yöneticisine de."""
    _main, _engine, client = uygulama
    from app.config import settings

    monkeypatch.setattr(settings, "sungur_platform_operators", "", raising=False)

    h = _giris(client)
    cevap = client.get("/api/platform/audit", headers=h)
    assert cevap.status_code == 403, (
        f"platform denetim yolu platform operatörü olmayana açık (HTTP {cevap.status_code})"
    )
