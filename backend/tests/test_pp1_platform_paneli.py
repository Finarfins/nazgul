"""PP1 — platform yönetim paneli: kiracı muafiyeti + salt-okunur API (göç YOK).

Konu: ``app/platform_access.py::platform_yolu`` (TEK muafiyet yüklemi),
``app/main.py::security_and_audit`` (muaf yolda kiracı çözülmez),
``app/platform_denetim.py`` (firmasız platform denetim satırı),
``app/routers/platform_management.py`` (yedi GET).

--- PP1 ÖNCESİ ÖLÇÜLEN (develop 6442794) ------------------------------------

* Üyeliği olmayan operatör: ``GET /api/platform/backups`` -> 403
  COMPANY_ACCESS_DENIED; ``POST`` -> 403 ve denetim satırı CHECK kısıtını
  (ck_security_audit_logs_untenanted_only_preauth) ihlal edip yedek çıkışa
  düştü, ``/api/ready`` 503'e döndü.
* Üyeliği olan operatör ``X-Company-Id: 1`` ile yedek aldığında olay
  ``activity_logs(company_id=1, action_type='backup.created')`` olarak O
  KİRACININ defterine yazıldı.

--- BU DOSYANIN MUTASYON TABLOSU --------------------------------------------

  * ``main.py``de ``if platform:`` dalını silmek (kiracı yine çözülür)
                         -> SIFIR ÜYELİK testleri KIRMIZI (403)
  * ``platform_yolu``nu ``"/api/platform"`` (eğik çizgisiz) yapmak
                         -> MUAFİYET ÇİVİSİ KIRMIZI
  * ``_write_security_audit``ten kimlik taşımasını silmek
                         -> DENETİM testi KIRMIZI (satır yedek çıkışa düşer,
                            ``audit_sink_healthy`` False)
  * ``platform_backups._log``u eski ``log_activity`` çağrısına döndürmek
                         -> DENETİM testi KIRMIZI (int(None))
  * herhangi bir uçtan ``require_platform_operator``ı silmek
                         -> 403 testleri KIRMIZI
  * bir yanıta cari/iletişim alanı eklemek
                         -> ANAHTAR KÜMESİ testi KIRMIZI
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "PlatformPaneli!2026x"
ANAHTAR = "pp1-platform-paneli-anahtar-pp1-platform-paneli-anahtar"

#: Yedi yeni uç (PP1).
YENI_UCLAR = (
    "/api/platform/overview",
    "/api/platform/companies",
    "/api/platform/users",
    "/api/platform/verifications",
    "/api/platform/outbox/health",
    "/api/platform/rate-limits",
    "/api/platform/edocuments/health",
)
#: Operatörün eriştiği TÜM platform GET'leri: yedi yeni + mevcut iki okuma.
TUM_GETLER = YENI_UCLAR + ("/api/platform/backups", "/api/platform/audit")

#: Hiçbir yanıtta ANAHTAR olarak geçmemesi gereken adlar. İlk dördü SEC-3b'nin
#: maskelenen cari alanları; gerisi bu uçların dokunduğu tabloların hassas
#: sütunları (bildirim alıcısı/yükü, belirteç özeti, parola, fatura
#: anlık görüntüleri).
YASAK_ANAHTARLAR = frozenset({
    "phone", "email", "address", "tax_number",
    "recipient", "payload", "last_error", "token", "token_hash",
    "password_hash", "customer_snapshot", "company_snapshot", "totals_snapshot",
    "tax_snapshot", "customer_id", "supplier_id", "details", "summary",
})
#: Tohumlanan GİZLİ değerler: yanıt METNİNİN hiçbir yerinde geçmemeli.
GIZLI_DEGERLER = (
    "gizli-alici@musteri.example", "+905550000001", "9876543210",
    "Gizli Sokak No 7", "GIZLI-YUK-PP1", "gizli-belirtec-ozeti-pp1",
)


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    """``app`` paketini bu dosyanın veritabanıyla TAZE içe aktarır, sonra geri
    koyar (``test_security_audit_visibility`` ile aynı desen)."""
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'pp1.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.syspath_prepend(str(BACKEND))
    try:
        from fastapi.testclient import TestClient

        import app.main as main
        from app.db import engine

        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield main, engine, client
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


def _giris(client, kullanici: str) -> dict:
    cevap = client.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    assert cevap.status_code == 200, cevap.text
    client.cookies.clear()  # Bearer ile devam; CSRF bu dosyanın konusu değil
    return {"Authorization": "Bearer " + cevap.json()["access_token"]}


def _tohumla(engine) -> dict:
    """İki firma + pasif üçüncü firma, beş kullanıcı, kuyruk/fatura/hız/denetim."""
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    ph = hash_password(PAROLA)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0, email_verified=1"))
        a = int(c.execute(text("SELECT MIN(id) FROM companies")).scalar_one())
        b = int(c.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES ('Bravo Tarim',1,:t) RETURNING id"),
            {"t": simdi}).scalar_one())
        pasif = int(c.execute(
            text("INSERT INTO companies(name,is_active,created_at) VALUES ('Kapali Firma',0,:t) RETURNING id"),
            {"t": simdi}).scalar_one())

        def kullanici(ad: str, rol: str, dogrulandi: bool) -> int:
            return int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                "role,is_active,created_at,must_change_password) VALUES "
                "(:u,:e,:v,:d,:h,:r,1,:t,0) RETURNING id"),
                {"u": ad, "e": f"{ad}@platform.example", "v": dogrulandi, "d": ad.title(),
                 "h": ph, "r": rol, "t": simdi}).scalar_one())

        op = kullanici("operator", "admin", True)          # SIFIR üyelik
        admin_b = kullanici("bravoadmin", "admin", True)   # B'nin admini, operatör DEĞİL
        yon = kullanici("yonetici1", "yonetici", True)
        yeni = kullanici("yenikayit", "admin", False)      # doğrulanmamış
        admin = int(c.execute(text("SELECT id FROM app_users WHERE username='admin'")).scalar_one())
        for uid, cid, varsayilan in ((admin_b, b, 1), (yon, a, 1), (yeni, b, 1), (admin, b, 0)):
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,:d,:t)"), {"u": uid, "c": cid, "d": varsayilan, "t": simdi})

        # Doğrulama belirteçleri: 1 bekleyen, 1 süresi dolmuş, 1 kullanılmış.
        for ozet, olusma, bitis, kullanildi in (
            ("gizli-belirtec-ozeti-pp1", simdi - timedelta(hours=1), simdi + timedelta(hours=23), None),
            ("ozet-suresi-dolmus-pp1", simdi - timedelta(hours=30), simdi - timedelta(hours=6), None),
            ("ozet-kullanilmis-pp1", simdi - timedelta(hours=2), simdi + timedelta(hours=22), simdi),
        ):
            c.execute(text(
                "INSERT INTO email_verification_tokens(user_id,token_hash,created_at,expires_at,used_at) "
                "VALUES (:u,:h,:c,:e,:k)"), {"u": yeni, "h": ozet, "c": olusma, "e": bitis, "k": kullanildi})

        # Bildirim kuyruğu: iki firma, iki kanal.
        def bildirim(cid, kanal, durum, silahli, olusma, deneme=None):
            c.execute(text(
                "INSERT INTO notifications(company_id,type,channel,recipient,template,payload,status,"
                "dispatch_armed,created_at,updated_at,last_attempt_at) VALUES "
                "(:c,'MANUAL',:k,:r,'tmpl',:p,:s,:a,:o,:o,:d)"),
                {"c": cid, "k": kanal, "r": "gizli-alici@musteri.example", "p": "GIZLI-YUK-PP1",
                 "s": durum, "a": silahli, "o": olusma, "d": deneme})

        bildirim(a, "EMAIL", "PENDING", True, simdi - timedelta(hours=5))
        bildirim(b, "EMAIL", "RETRY_SCHEDULED", True, simdi - timedelta(hours=2))
        bildirim(b, "EMAIL", "PENDING", False, simdi - timedelta(hours=50))   # disarmed: sayılmaz
        bildirim(a, "EMAIL", "FAILED", True, simdi - timedelta(hours=3))
        bildirim(b, "SMS", "NONE", True, simdi - timedelta(hours=3))
        bildirim(a, "SMS", "SENT", True, simdi - timedelta(hours=1), simdi - timedelta(hours=1))
        bildirim(b, "SMS", "DELIVERED", True, simdi - timedelta(hours=2), simdi - timedelta(hours=2))
        bildirim(b, "SMS", "SENT", True, simdi - timedelta(days=3), simdi - timedelta(days=3))  # pencere dışı
        bildirim(a, "SMS", "SIMULATED", True, simdi - timedelta(hours=1), simdi - timedelta(hours=1))

        # Faturalar: e-belge durumları; anlık görüntüler cari sırrı taşıyor.
        cari = json.dumps({"name": "Gizli Cari", "tax_number": "9876543210",
                           "phone": "+905550000001", "address": "Gizli Sokak No 7"})
        for cid, durum, sira in ((a, "NONE", 1), (a, "SENT", 2), (a, "SENT", 3), (b, "FAILED", 4)):
            c.execute(text(
                "INSERT INTO invoices(company_id,invoice_number,customer_snapshot,machine_snapshot,"
                "work_order_snapshot,company_snapshot,technician_snapshot,warranty_snapshot,"
                "totals_snapshot,tax_snapshot,created_by,created_at,updated_at,einvoice_status) VALUES "
                "(:c,:n,:cari,'{}','{}','{}','{}','{}','{}','{}',:u,:t,:t,:s)"),
                {"c": cid, "n": f"PP1-{sira}", "cari": cari, "u": admin, "t": simdi, "s": durum})

        # Son hareket: A'da iki kayıt (en yenisi ölçülür), B'de bir.
        for cid, zaman in ((a, simdi - timedelta(days=2)), (a, simdi - timedelta(hours=4)),
                           (b, simdi - timedelta(days=1))):
            c.execute(text(
                "INSERT INTO activity_logs(company_id,user_id,action_type,resource_type,summary,created_at) "
                "VALUES (:c,:u,'customer.create','customer','Gizli Cari eklendi',:t)"),
                {"c": cid, "u": admin, "t": zaman})

        # Hız sınırı denemeleri ve 429 denetim satırları.
        for eylem, ip, adet in (("login", "203.0.113.7", 3), ("forgot_password", "198.51.100.2", 1)):
            for _ in range(adet):
                c.execute(text(
                    "INSERT INTO auth_rate_limits(action,ip_address,attempted_at) VALUES (:a,:i,:t)"),
                    {"a": eylem, "i": ip, "t": simdi - timedelta(minutes=10)})
        for yas in (timedelta(hours=1), timedelta(hours=2), timedelta(hours=30)):
            c.execute(text(
                "INSERT INTO security_audit_logs(action,path,status_code,created_at,outcome) "
                "VALUES ('POST','/api/auth/forgot-password',429,:t,'denied')"), {"t": simdi - yas})
    return {"a": a, "b": b, "pasif": pasif, "op": op, "admin_b": admin_b, "yon": yon,
            "yeni": yeni, "admin": admin}


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pp1")
    with _uygulama(tmp) as (main, engine, client):
        kimlik = _tohumla(engine)
        from app.config import settings

        settings.sungur_platform_operators = str(kimlik["op"])
        try:
            yield {"main": main, "engine": engine, "client": client, **kimlik,
                   "h_op": _giris(client, "operator"),
                   "h_admin_b": _giris(client, "bravoadmin"),
                   "h_yon": _giris(client, "yonetici1")}
        finally:
            settings.sungur_platform_operators = ""


def _anahtarlar(deger) -> set[str]:
    if isinstance(deger, dict):
        kume = set(deger)
        for alt in deger.values():
            kume |= _anahtarlar(alt)
        return kume
    if isinstance(deger, list):
        kume: set[str] = set()
        for alt in deger:
            kume |= _anahtarlar(alt)
        return kume
    return set()


def _sql(engine, sorgu: str, **p):
    from sqlalchemy import text

    with engine.connect() as c:
        return c.execute(text(sorgu), p).all()


# --------------------------------------------------------------- muafiyet ---

def _api_rotalari(app) -> list[tuple[str, frozenset[str]]]:
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:  # pragma: no cover
        _IncludedRouter = ()
    cikti = []
    for rota in app.routes:
        if isinstance(rota, APIRoute):
            cikti.append((rota.path, frozenset(rota.methods or ())))
        elif _IncludedRouter and isinstance(rota, _IncludedRouter):
            for ctx in rota.effective_route_contexts():
                cikti.append((ctx.path, frozenset(ctx.methods or ())))
    return [(y, m) for y, m in cikti if y.startswith("/api")]


def test_muafiyet_yuklemi_her_platform_rotasini_kapsar_baskasini_kapsamaz(ortam) -> None:
    """Bağlı HER ``/api/platform`` rotası muaf; başka HİÇBİR rota muaf değil."""
    from app.platform_access import platform_yolu
    from app.route_security_contracts import PLATFORM_ROUTE_PREFIXES

    rotalar = _api_rotalari(ortam["main"].app)
    assert len(rotalar) > 300, len(rotalar)  # gezinti kör değil
    muaf = sorted({y for y, _ in rotalar if platform_yolu(y)})
    beklenen = sorted({
        "/api/platform/audit", "/api/platform/backups", "/api/platform/backups/{name}/download",
        "/api/platform/backups/{name}/verify", "/api/platform/backups/{name}/restore",
        "/api/platform/tenant-restore", *YENI_UCLAR,
        # PP2 yazma uçları (``tests/test_pp2_platform_eylemleri.py``); DELETE
        # ``/api/platform/rate-limits`` PP1'in yolunu paylaşır.
        "/api/platform/companies/{sirket_id}/activate",
        "/api/platform/companies/{sirket_id}/deactivate",
        "/api/platform/users/{kullanici_id}/status",
        "/api/platform/users/{kullanici_id}/resend-verification",
        "/api/platform/users/{kullanici_id}/force-password-reset",
        "/api/platform/outbox/retry",
    })
    assert muaf == beklenen, muaf
    for yol, _ in rotalar:
        # Tek tanım: sözleşme kapısının "platform" kapsamıyla BİREBİR aynı.
        assert platform_yolu(yol) == yol.startswith(PLATFORM_ROUTE_PREFIXES), yol
        if not yol.startswith("/api/platform/"):
            assert not platform_yolu(yol), yol
    # Eğik çizgi ZORUNLU.
    assert not platform_yolu("/api/platform")
    assert not platform_yolu("/api/platformx/backups")


def test_platform_disi_rota_muaf_DEGIL_sifir_uyelik_hala_403(ortam) -> None:
    """Muafiyet ÖNEKE bağlı: üyeliği olmayan operatör kiracı ucunda yine 403."""
    cevap = ortam["client"].get("/api/customers", headers=ortam["h_op"])
    assert cevap.status_code == 403, cevap.text
    assert cevap.json().get("code") == "COMPANY_ACCESS_DENIED"


@pytest.mark.parametrize("yol", TUM_GETLER)
def test_sifir_uyelikli_operator_200(ortam, yol: str) -> None:
    cevap = ortam["client"].get(yol, headers=ortam["h_op"])
    assert cevap.status_code == 200, (yol, cevap.status_code, cevap.text[:300])


@pytest.mark.parametrize("anahtar", ["h_admin_b", "h_yon"])
@pytest.mark.parametrize("yol", TUM_GETLER)
def test_operator_olmayan_403_operator_mesajiyla(ortam, yol: str, anahtar: str) -> None:
    """Üyeliği olan admin (listede DEĞİL) ve yönetici: yönlendiricinin 403'ü."""
    cevap = ortam["client"].get(yol, headers=ortam[anahtar])
    assert cevap.status_code == 403, (yol, anahtar, cevap.text)
    assert cevap.json()["detail"] == "Platform operatörü yetkisi gerekli"
    assert cevap.json().get("code") != "COMPANY_ACCESS_DENIED"


@pytest.mark.parametrize("secici", ["999999", "abc", "-1"])
def test_x_company_id_yok_sayilir(ortam, secici: str) -> None:
    """Yabancı, bozuk ya da negatif seçici platform yolunda HİÇBİR şeyi değiştirmez."""
    client, h = ortam["client"], ortam["h_op"]
    temel = client.get("/api/platform/companies", headers=h).json()
    cevap = client.get("/api/platform/companies", headers={**h, "X-Company-Id": secici})
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == temel


# ---------------------------------------------------------------- denetim ---

def test_platform_yedek_denetimi_firmasiz_ve_activity_logsta_YOK(ortam) -> None:
    """Yedek olayı ``security_audit_logs``a firmasız düşer, kiracı defterine değil.

    ``X-Company-Id`` VE ``Idempotency-Key`` gönderilir: ikisi de yok sayılır —
    kiracı defterine ne olay ne idempotensi anahtarı yazılır.
    """
    engine, client = ortam["engine"], ortam["client"]
    main = ortam["main"]
    # SIRADAN BAĞIMSIZ: yalnız BU isteğin satırları sayılır (komşu test de
    # platform POST'u yapıyor; ters sırada koşunca onun satırı da görünür).
    once = _sql(engine, "SELECT COALESCE(MAX(id), 0) FROM security_audit_logs")[0][0]
    idem_once = _sql(engine, "SELECT COUNT(*) FROM idempotency_keys")[0][0]
    cevap = client.post(
        "/api/platform/backups",
        headers={**ortam["h_op"], "X-Company-Id": str(ortam["a"]), "Idempotency-Key": "pp1-yedek-1"},
    )
    assert cevap.status_code == 201, cevap.text
    assert _sql(engine, "SELECT COUNT(*) FROM activity_logs WHERE action_type LIKE 'backup.%'")[0][0] == 0
    assert _sql(engine, "SELECT COUNT(*) FROM idempotency_keys")[0][0] == idem_once
    satirlar = _sql(
        engine,
        "SELECT company_id,user_id,username,action,path,status_code,failure_reason "
        "FROM security_audit_logs WHERE id > :i ORDER BY id", i=once)
    assert len(satirlar) == 2, satirlar
    olay = [r for r in satirlar if r[3] == "platform.bk_create"]
    http = [r for r in satirlar if r[3] == "POST"]
    assert len(olay) == 1 and len(http) == 1, satirlar
    for r in satirlar:
        # CHECK: firmasız satırda kimlik NULL; aktör not sütununda.
        assert r[0] is None and r[1] is None and r[2] is None, r
        assert f"aktor={ortam['op']}" in r[6], r
    assert "olay=backup.created" in olay[0][6]
    assert http[0][5] == 201
    # Yazım yedek çıkışa düşmedi: mandal kalkmadı, /api/ready yeşil.
    assert main.audit_sink_healthy()
    assert client.get("/api/ready").status_code == 200
    # Satır platform okuma yolunda GÖRÜNÜR.
    okunan = client.get("/api/platform/audit", headers=ortam["h_op"]).json()
    assert any(r["action"] == "platform.bk_create" for r in okunan)


def test_operator_olmayanin_platform_POSTu_da_kisiti_ihlal_etmez(ortam) -> None:
    """Reddedilen platform POST'unun satırı da firmasız ve kimliksiz."""
    engine, client, main = ortam["engine"], ortam["client"], ortam["main"]
    cevap = client.post("/api/platform/backups", headers=ortam["h_admin_b"])
    assert cevap.status_code == 403
    son = _sql(engine, "SELECT company_id,user_id,username,status_code,failure_reason "
                       "FROM security_audit_logs ORDER BY id DESC LIMIT 1")[0]
    assert son[0] is None and son[1] is None and son[2] is None and son[3] == 403, son
    assert f"aktor={ortam['admin_b']}" in son[4]
    assert main.audit_sink_healthy()


def test_platform_olay_katalogu_sutuna_sigar() -> None:
    from app.auth import audit_logs
    from app.platform_denetim import EYLEM_GENISLIGI, PLATFORM_EYLEM_ONEKI, PLATFORM_OLAYLARI

    assert audit_logs.c.action.type.length == EYLEM_GENISLIGI
    for olay, kod in PLATFORM_OLAYLARI.items():
        assert kod.startswith(PLATFORM_EYLEM_ONEKI) and len(kod) <= EYLEM_GENISLIGI, (olay, kod)
    assert len(set(PLATFORM_OLAYLARI.values())) == len(PLATFORM_OLAYLARI)


def test_katalog_disi_olay_ValueError_ve_satir_YAZILMAZ(ortam) -> None:
    """Katalog dışı olay reddedilir ve ``security_audit_logs``a HİÇ satır düşmez.

    NEDEN: katalog KAPALIDIR; eşlemesi olmayan ad ``action`` sütununa (String(20))
    ya kırpılarak ya da yanlış kodla yazılırdı. Ret yazımdan ÖNCE olmalı —
    yarım satır bırakan bir ret, platform defterini sessizce kirletir.
    """
    from types import SimpleNamespace

    from app.platform_denetim import platform_olayi_yaz

    engine = ortam["engine"]
    istek = SimpleNamespace(
        state=SimpleNamespace(user={"id": ortam["op"]}, request_id=None, auth_source=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
        url=SimpleNamespace(path="/api/platform/backups"),
    )
    once = _sql(engine, "SELECT COUNT(*) FROM security_audit_logs")[0][0]
    with pytest.raises(ValueError, match="Katalog dışı platform olayı: backup.bilinmeyen"):
        platform_olayi_yaz(istek, "backup.bilinmeyen", "ret denemesi")
    assert _sql(engine, "SELECT COUNT(*) FROM security_audit_logs")[0][0] == once


def test_katalog_kodlari_String20_sinirini_asmaz() -> None:
    """Her kod ``platform.`` + kısa ad olarak 20 karakteri aşmaz.

    NEDEN: sınır burada SABİT 20 olarak yazılır, ``EYLEM_GENISLIGI``den
    okunmaz — sabit ile katalog birlikte kayarsa bu pin yine kırmızı yanar.
    """
    from app.platform_denetim import PLATFORM_OLAYLARI

    for olay, kod in PLATFORM_OLAYLARI.items():
        kisa = kod.removeprefix("platform.")
        assert len("platform." + kisa) <= 20, f"{olay} -> {kod} ({len(kod)} > 20)"


# ------------------------------------------------------ yanıt biçimi/sayı ---

@pytest.mark.parametrize("yol", YENI_UCLAR)
def test_hicbir_yanitta_cari_ya_da_hassas_alan_yok(ortam, yol: str) -> None:
    cevap = ortam["client"].get(yol, headers=ortam["h_op"])
    assert cevap.status_code == 200
    anahtarlar = _anahtarlar(cevap.json())
    assert not anahtarlar & YASAK_ANAHTARLAR, (yol, sorted(anahtarlar & YASAK_ANAHTARLAR))
    for gizli in GIZLI_DEGERLER:
        assert gizli not in cevap.text, (yol, gizli)


def test_overview_sayilari(ortam) -> None:
    engine = ortam["engine"]
    govde = ortam["client"].get("/api/platform/overview", headers=ortam["h_op"]).json()
    assert set(govde) == {
        "companies", "users", "pending_verifications", "outbox", "field_stock_scheduler",
        "rate_limit_blocks_last_24h", "einvoice_status_histogram"}
    toplam, aktif = _sql(engine, "SELECT COUNT(*), SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) FROM companies")[0]
    assert govde["companies"] == {"total": toplam, "active": aktif, "inactive": toplam - aktif}
    assert govde["companies"]["inactive"] >= 1
    k_toplam, k_dogru = _sql(engine, "SELECT COUNT(*), SUM(CASE WHEN email_verified=1 THEN 1 ELSE 0 END) FROM app_users")[0]
    assert govde["users"] == {"total": k_toplam, "verified": k_dogru, "unverified": k_toplam - k_dogru}
    assert govde["users"]["unverified"] >= 1
    assert govde["pending_verifications"] == 1
    # PENDING/RETRY armed = 2 (disarmed sayılmaz); FAILED+NONE = 2.
    assert govde["outbox"]["pending"] == 2 and govde["outbox"]["failed"] == 2, govde["outbox"]
    assert 4.5 * 3600 < govde["outbox"]["oldest_pending_age_seconds"] < 5.5 * 3600
    # 429 satırları: 24 saat içinde İKİ (30 saatlik sayılmaz).
    assert govde["rate_limit_blocks_last_24h"] == 2
    assert govde["einvoice_status_histogram"] == {"FAILED": 1, "NONE": 1, "SENT": 2}
    assert set(govde["field_stock_scheduler"]) >= {"enabled", "alive", "stale"}
    assert "last_error" not in govde["field_stock_scheduler"]


def test_companies_listesi_uye_sayisi_ve_son_hareket(ortam) -> None:
    client, h = ortam["client"], ortam["h_op"]
    govde = client.get("/api/platform/companies", headers=h).json()
    assert set(govde) == {"total", "limit", "offset", "items"}
    kalemler = {k["id"]: k for k in govde["items"]}
    assert set(kalemler[ortam["a"]]) == {"id", "name", "is_active", "created_at",
                                         "member_count", "last_activity_at"}
    # A: bootstrap admin + yonetici1; B: bravoadmin + yenikayit + admin.
    a_uye = _sql(ortam["engine"], "SELECT COUNT(*) FROM user_company_memberships WHERE company_id=:c", c=ortam["a"])[0][0]
    assert kalemler[ortam["a"]]["member_count"] == a_uye
    assert kalemler[ortam["b"]]["member_count"] == 3
    assert kalemler[ortam["pasif"]]["member_count"] == 0
    assert kalemler[ortam["pasif"]]["last_activity_at"] is None
    son_a = datetime.fromisoformat(kalemler[ortam["a"]]["last_activity_at"])
    if son_a.tzinfo is None:
        son_a = son_a.replace(tzinfo=timezone.utc)
    beklenen = _sql(ortam["engine"], "SELECT MAX(created_at) FROM activity_logs WHERE company_id=:c", c=ortam["a"])[0][0]
    assert son_a >= datetime.now(timezone.utc) - timedelta(hours=5), (son_a, beklenen)
    # Süzgeçler.
    pasifler = client.get("/api/platform/companies?active=false", headers=h).json()
    assert [k["id"] for k in pasifler["items"]] == [ortam["pasif"]] and pasifler["total"] == 1
    arama = client.get("/api/platform/companies?q=bRaVo", headers=h).json()
    assert [k["name"] for k in arama["items"]] == ["Bravo Tarim"]
    # Joker karakter kaçışlı: "%" her şeyi eşleştirmez.
    assert client.get("/api/platform/companies?q=%25", headers=h).json()["total"] == 0
    sayfa = client.get("/api/platform/companies?limit=1&offset=1", headers=h).json()
    assert sayfa["limit"] == 1 and sayfa["offset"] == 1 and len(sayfa["items"]) == 1
    assert sayfa["total"] == govde["total"]
    assert client.get("/api/platform/companies?limit=201", headers=h).status_code == 422
    assert client.get("/api/platform/companies?limit=0", headers=h).status_code == 422


def test_users_listesi_uyelikler_ve_suzgec(ortam) -> None:
    client, h = ortam["client"], ortam["h_op"]
    govde = client.get("/api/platform/users?limit=200", headers=h).json()
    kalemler = {k["id"]: k for k in govde["items"]}
    assert set(kalemler[ortam["op"]]) == {
        "id", "username", "display_name", "role", "is_active", "email_verified",
        "must_change_password", "created_at", "last_login_at", "memberships"}
    assert kalemler[ortam["op"]]["memberships"] == []
    assert kalemler[ortam["op"]]["last_login_at"] is not None  # giriş yaptı
    uyelik = kalemler[ortam["admin"]]["memberships"]
    assert {u["company_id"] for u in uyelik} == {ortam["a"], ortam["b"]}
    assert set(uyelik[0]) == {"company_id", "company_name", "company_is_active", "is_default"}
    assert kalemler[ortam["yeni"]]["email_verified"] is False
    dogrulanmamis = client.get("/api/platform/users?verified=false", headers=h).json()
    assert [k["id"] for k in dogrulanmamis["items"]] == [ortam["yeni"]]
    assert client.get("/api/platform/users?q=YONETICI", headers=h).json()["total"] == 1
    assert client.get("/api/platform/users?limit=201", headers=h).status_code == 422


def test_verifications_yalniz_bekleyen_ve_belirtecsiz(ortam) -> None:
    govde = ortam["client"].get("/api/platform/verifications", headers=ortam["h_op"]).json()
    assert govde["total"] == 1 and len(govde["items"]) == 1, govde
    assert set(govde["items"][0]) == {"user_id", "username", "created_at", "expires_at"}
    assert govde["items"][0]["user_id"] == ortam["yeni"]


def test_outbox_health_kanal_basina(ortam) -> None:
    govde = ortam["client"].get("/api/platform/outbox/health", headers=ortam["h_op"]).json()
    kanallar = {k["channel"]: k for k in govde["channels"]}
    assert kanallar["EMAIL"]["pending"] == 2 and kanallar["EMAIL"]["failed"] == 1
    assert kanallar["EMAIL"]["sent_last_24h"] == 0
    assert kanallar["SMS"]["pending"] == 0 and kanallar["SMS"]["failed"] == 1
    # SENT + DELIVERED 24 saat içinde = 2; SIMULATED ve 3 günlük SENT sayılmaz.
    assert kanallar["SMS"]["sent_last_24h"] == 2
    assert kanallar["SMS"]["oldest_pending_age_seconds"] is None
    assert kanallar["EMAIL"]["oldest_pending_age_seconds"] > 4.5 * 3600


def test_outbox_sayaci_kiraci_sayaciyla_ayni(ortam) -> None:
    """Platform sayacı ile ``outbox_counters`` AYNI durum sınıflarını sayar.

    Firma firma ``outbox_counters`` toplamı = kanal kanal platform toplamı.
    Biri bir durumu sınıfa ekleyip diğerini unutursa bu eşitlik kırılır.
    """
    from app.db import SessionLocal
    from app.notifications.service import outbox_counters, platform_kanal_sayaclari

    with SessionLocal() as db:
        kiraci = [outbox_counters(db, company_id=c) for c in (ortam["a"], ortam["b"], ortam["pasif"])]
        platform = platform_kanal_sayaclari(db)
    assert sum(k["pending"] for k in kiraci) == sum(p["pending"] for p in platform) == 2
    assert sum(k["failed"] for k in kiraci) == sum(p["failed"] for p in platform) == 2
    # Tek fark pencere: 3 günlük SENT kiracı sayacında VAR, 24 saatlikte YOK.
    assert sum(k["sent"] for k in kiraci) == sum(p["sent_last_24h"] for p in platform) + 1


def test_rate_limits_eylem_ve_ip_basina(ortam) -> None:
    govde = ortam["client"].get("/api/platform/rate-limits", headers=ortam["h_op"]).json()
    assert govde["window_hours"] == 24 and govde["retention_hours"] == 1
    satirlar = {(s["action"], s["ip_address"]): s["attempts"] for s in govde["items"]}
    assert satirlar[("login", "203.0.113.7")] >= 3
    assert satirlar[("forgot_password", "198.51.100.2")] == 1
    assert ortam["client"].get("/api/platform/rate-limits?window_hours=0",
                               headers=ortam["h_op"]).status_code == 422


def test_edocuments_health_firma_basina_ve_ortam(ortam) -> None:
    from app.config import settings

    govde = ortam["client"].get("/api/platform/edocuments/health", headers=ortam["h_op"]).json()
    assert govde["izibiz_env"] == settings.izibiz_env and govde["izibiz_env"] in {"test", "live"}
    sirketler = {s["company_id"]: s for s in govde["companies"]}
    assert sirketler[ortam["a"]]["by_status"] == {"NONE": 1, "SENT": 2}
    assert sirketler[ortam["a"]]["total"] == 3
    assert sirketler[ortam["b"]] == {"company_id": ortam["b"], "company_name": "Bravo Tarim",
                                     "total": 1, "by_status": {"FAILED": 1}}
    assert ortam["pasif"] not in sirketler
