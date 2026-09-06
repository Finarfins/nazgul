"""KİRACI YUMUŞAK İMHASI sözleşmesi — ``POST /api/company/erase``.

Bu kapının koruduğu iddialar, hepsi SESSİZCE yanlış davranış üretir:

1. **Onay adı BİREBİR.** Gevşetilirse yanlış kiracı kapatılır ve geri
   alınamaz.
2. **Rol EN YÜKSEK, üyelik ŞART.** Dışa aktarımla AYNI kapılar; ayrışırlarsa
   biri diğerinden daha zayıf olur ve zayıf olan kazanır.
3. **KİLİT KİRACI ÇÖZÜMÜNDE.** İmhadan sonra ``/api``nin TAMAMI kapanmalı —
   uca tek tek kapı takılsaydı gözden kaçan uç kilidi yalan yapardı. DÖRT ayrı
   router'ın rotası üzerinde ölçülür ve gövde HEPSİNDE
   ``COMPANY_ACCESS_DENIED``tir: kapalı firmaya ayrı bir kod vermek kiracı
   yaşam döngüsünü dışarıya sızdırırdı.
4. **Üyelik silme FİRMA KAPSAMLI.** Yüklem düşerse kümedeki her kullanıcı
   her firmasını kaybeder.
5. **Tekrar çağrı 409**, ikinci bir denetim kaydı YAZILMAZ.
6. **Bu dosyada ``delete()`` YALNIZ üyeliklere.** Durağan kapı aşağıda.
7. **Zamanlayıcı kapatılmış firmaya YAZMAZ.** HTTP'den geçmeyen tek yazma
   yolu odur; yüklemi olmasaydı "kapatıldı" iddiası denetlenmeyen yerde
   yalan olurdu.

ÖLÇÜM YÖNTEMİ
-------------
Kiracı yaşam döngüsü senaryoları ALT SÜREÇTE koşar: ``app.config.Settings``
modül düzeyinde TEK KOPYADIR ve taze bir ``DATABASE_URL`` ancak ayrı bir
süreçte görülebilir (``test_kiraci_disa_aktarim.py`` ile AYNI gerekçe). Her
senaryo kendi veritabanını kurar; imha GERİ ALINAMAZ olduğu için senaryoları
tek bir veritabanında sıraya dizmek mümkün değildir.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

BACKEND = Path(__file__).resolve().parents[1]
ROUTER = BACKEND / "app" / "routers" / "kiraci_imha.py"

#: Bootstrap parolası KODA GÖMÜLMEZ. Alt süreç onu ``settings`` üzerinden
#: okur; sabit ``admin123`` yazan bir giriş, parolayı döndüren başka bir
#: smoke'tan sonra koştuğunda düşerdi.
_ORTAK = r'''
import json, os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, MetaData

from app.main import app
from app.config import settings
from app.db import engine

YENI_PAROLA = "Imha!2026Kiraci"
CIKTI = Path(os.environ["CIKTI_DIZINI"])


def admin_headers(client):
    adaylar = (settings.effective_bootstrap_admin_password, YENI_PAROLA, "admin123")
    for aday in adaylar:
        giris = client.post("/api/auth/login",
                            json={"username": "admin", "password": aday})
        if giris.status_code == 200:
            break
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    h = {"Authorization": "Bearer " + govde["access_token"],
         "X-Company-ID": str(govde["companies"][0]["id"])}
    if aday != YENI_PAROLA:
        ch = client.post("/api/auth/change-password", headers=h,
                         json={"current_password": aday, "new_password": YENI_PAROLA})
        assert ch.status_code == 200, ch.text
        h["Authorization"] = "Bearer " + ch.json()["access_token"]
    return h, govde


def semayi_yansit():
    md = MetaData(); md.reflect(bind=engine)
    return md


def ikinci_kiraci(md, admin_id, ad="B Kiracisi"):
    """ADMIN'i İKİNCİ bir firmaya da üye yapar ve o firmanın kimliğini verir."""
    with engine.begin() as conn:
        b_id = conn.execute(insert(md.tables["companies"]).values(
            name=ad, is_active=True,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=b_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
    return int(b_id)


def yaz(ad, veri):
    (CIKTI / ad).write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
'''


def _kos(betik: str, veritabani: Path, ciktilar: Path):
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["CIKTI_DIZINI"] = str(ciktilar)
    ortam["SUNGUR_DATA_DIR"] = str(ciktilar / "veri")
    (ciktilar / "veri").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", betik], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, timeout=900,
    )


def _basarili(tamam) -> None:
    assert tamam.returncode == 0, tamam.stdout[-6000:] + "\n" + tamam.stderr[-6000:]


# --------------------------------------------------------------------------
# 1) DURAĞAN KAPI — BU DOSYA NEYİ SİLEBİLİR
# --------------------------------------------------------------------------
def test_ucta_uyelikten_baska_hicbir_tablo_silinmiyor() -> None:
    """MUTASYON: uca ikinci bir ``delete(<baska tablo>)`` eklemek — ya da ham
    bir ``DELETE FROM ...`` metni yazmak — bu testi KIRMIZI yapar.

    Kapı GEREKLİ çünkü ucun ADI "imha"dır ve dosyaya "madem imha, şunu da
    silelim" diye bir satır eklemek en kolay ve en yıkıcı hatadır. Yumuşak
    imha VERİ SATIRI SİLMEZ: ``activity_logs`` ve ``notifications_archive``
    üzerindeki ``BEFORE DELETE`` tetikleyicileri sert silmeyi zaten imkânsız
    kılar, yani yarım kalmış bir silme denemesi tek olası sonuçtur.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")

    # (a) Ham SQL metni HİÇ yok — ne izinli tablo için ne başkası için.
    #     Tetiği ``DELETE``, ``DROP``, ``TRUNCATE`` sözcükleridir; dizgeleri
    #     bu kapının kendi doküman metninden ayırmak için AST'ten okunur.
    agac = ast.parse(kaynak)
    metinler = [
        d.value for d in ast.walk(agac)
        if isinstance(d, ast.Constant) and isinstance(d.value, str)
    ]
    # Modül/fonksiyon docstring'leri metin sabitidir ve "DELETE FROM" gibi
    # sözcükleri AÇIKLAMA olarak taşıyabilir; kapı YÜRÜTÜLEN metni arar, o
    # yüzden docstring'ler çıkarılır.
    docstringler = {
        ast.get_docstring(d, clean=False)
        for d in ast.walk(agac)
        if isinstance(d, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    yurutulen = [m for m in metinler if m not in docstringler]
    for tehlike in ("DELETE", "DROP ", "TRUNCATE"):
        kirli = [m for m in yurutulen if tehlike in m.upper()]
        assert not kirli, (tehlike, kirli)

    # (b) Her ``delete(...)`` çağrısının argümanı TEK ve AYNI ad: memberships.
    hedefler = {
        (getattr(d.args[0], "id", None) if d.args else None)
        for d in ast.walk(agac)
        if isinstance(d, ast.Call) and getattr(d.func, "id", None) == "delete"
    }
    assert hedefler == {"memberships"}, hedefler

    # (c) ...ve o ad GERÇEKTEN üyelik tablosu. (b) tek başına, adı başka bir
    #     tabloya yeniden bağlayan bir satırla atlatılabilirdi.
    sys.path.insert(0, str(BACKEND))
    from app.routers.kiraci_imha import memberships

    assert memberships.name == "user_company_memberships"


def test_izin_kapisi_disa_aktarimla_ayni() -> None:
    """MUTASYON: ``required_permission``taki ``/api/company/erase`` satırını
    silmek ya da dönüşü ``"read"`` yapmak bu testi KIRMIZI yapar."""
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS, has_permission, required_permission

    assert required_permission("POST", "/api/company/erase") == "__admin_only__"
    assert required_permission("GET", "/api/company/export") == "__admin_only__"
    assert has_permission("admin", "__admin_only__")
    for rol in ROLE_PERMISSIONS:
        if rol != "admin":
            assert not has_permission(rol, "__admin_only__"), rol
    # ÖNEK DEĞİL TAM EŞLEŞME: "/api/company-settings" de "/api/company" ile
    # başlar; önek yazılsaydı ayarlar ucu sessizce admin'e kilitlenirdi.
    assert required_permission("GET", "/api/company-settings") != "__admin_only__"


# --------------------------------------------------------------------------
# 2) ÖN KOŞULLAR
# --------------------------------------------------------------------------
_YANLIS_AD = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    md = semayi_yansit()
    with engine.connect() as conn:
        ad = conn.execute(select(md.tables["companies"].c.name).where(
            md.tables["companies"].c.id == cid)).scalar_one()

    denemeler = {}
    for etiket, deger in (("yanlis", ad + " A.S."),
                          ("buyuk_harf", ad.upper()),
                          ("bosluklu", " " + ad + " ")):
        r = client.post("/api/company/erase", headers=h,
                        json={"confirm_name": deger})
        denemeler[etiket] = r.status_code

    # Reddedilen denemeler HICBIR YAN ETKI birakmamis olmali.
    with engine.connect() as conn:
        aktif = conn.execute(select(md.tables["companies"].c.is_active).where(
            md.tables["companies"].c.id == cid)).scalar_one()
        uyelik = conn.execute(select(md.tables["user_company_memberships"].c.id)
                              .where(md.tables["user_company_memberships"]
                                     .c.company_id == cid)).fetchall()
        kayit = conn.execute(select(md.tables["activity_logs"].c.id).where(
            md.tables["activity_logs"].c.action_type == "company.erased")).fetchall()

    yaz("yanlis_ad.json", {"denemeler": denemeler, "aktif": bool(aktif),
                           "uyelik": len(uyelik), "kayit": len(kayit)})
    print("YANLIS AD TAMAM")
'''


def test_yanlis_onay_adi_422_ve_yan_etkisiz(tmp_path: Path) -> None:
    """MUTASYON: ``confirm_name != satir["name"]`` denetimini kaldırmak ya da
    ``.lower()``/``turkce_katla`` ile gevşetmek bu testi KIRMIZI yapar.

    Üç deneme de reddedilmeli: farklı ad, BÜYÜK harfli ad, boşluk kırpılmış
    ad. Katlama ya da ``strip()`` eklense "Ada Tarım" ile "ADA TARIM" aynı
    sayılırdı — oysa bunlar AYRI iki kiracı adı olabilir.
    """
    _basarili(_kos(_YANLIS_AD, tmp_path / "y.db", tmp_path))
    sonuc = json.loads((tmp_path / "yanlis_ad.json").read_text(encoding="utf-8"))
    assert sonuc["denemeler"] == {
        "yanlis": 422, "buyuk_harf": 422, "bosluklu": 422
    }, sonuc
    assert sonuc["aktif"] is True, sonuc
    assert sonuc["uyelik"] >= 1, sonuc
    assert sonuc["kayit"] == 0, sonuc


_ROL_DUSUK = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    md = semayi_yansit()
    with engine.connect() as conn:
        ad = conn.execute(select(md.tables["companies"].c.name).where(
            md.tables["companies"].c.id == cid)).scalar_one()

    yeni = client.post("/api/users", headers=h, json={
        "username": "raporcu", "password": "Raporcu!2026",
        "display_name": "Raporcu", "role": "rapor"})
    assert yeni.status_code in (200, 201), yeni.text
    giris = client.post("/api/auth/login",
                        json={"username": "raporcu", "password": "Raporcu!2026"})
    assert giris.status_code == 200, giris.text
    g = giris.json()
    hr = {"Authorization": "Bearer " + g["access_token"], "X-Company-ID": str(cid)}
    if g.get("user", {}).get("must_change_password"):
        ch = client.post("/api/auth/change-password", headers=hr, json={
            "current_password": "Raporcu!2026", "new_password": "Raporcu!2027"})
        assert ch.status_code == 200, ch.text
        hr["Authorization"] = "Bearer " + ch.json()["access_token"]

    # UYE ama rolu yetmiyor.
    rol = client.post("/api/company/erase", headers=hr, json={"confirm_name": ad})
    # UYE DEGIL: dis aktarimla AYNI govde beklenir.
    h_yabanci = dict(h); h_yabanci["X-Company-ID"] = "999999"
    uye = client.post("/api/company/erase", headers=h_yabanci,
                      json={"confirm_name": ad})
    disa = client.get("/api/company/export", headers=h_yabanci)

    with engine.connect() as conn:
        aktif = conn.execute(select(md.tables["companies"].c.is_active).where(
            md.tables["companies"].c.id == cid)).scalar_one()

    yaz("rol.json", {
        "rol_durum": rol.status_code, "rol_kod": rol.json().get("code"),
        "uye_durum": uye.status_code, "uye_kod": uye.json().get("code"),
        "disa_durum": disa.status_code, "disa_kod": disa.json().get("code"),
        "aktif": bool(aktif)})
    print("ROL TAMAM")
'''


def test_dusuk_rol_ve_uye_olmayan_reddedilir(tmp_path: Path) -> None:
    """MUTASYON: ``__admin_only__`` iznini ``rapor`` rolüne eklemek, ya da
    ``resolve_company``daki üyelik yüklemini kaldırmak, bu testi KIRMIZI
    yapar.

    Üye olmayanın gövdesi dışa aktarımınkiyle AYNI olmalı: iki uç aynı iki
    kapıyı paylaşır, ayrışmaları birinin diğerinden zayıf olduğu anlamına
    gelirdi.
    """
    _basarili(_kos(_ROL_DUSUK, tmp_path / "r.db", tmp_path))
    sonuc = json.loads((tmp_path / "rol.json").read_text(encoding="utf-8"))
    assert (sonuc["rol_durum"], sonuc["rol_kod"]) == (403, "PERMISSION_DENIED"), sonuc
    assert (sonuc["uye_durum"], sonuc["uye_kod"]) == (403, "COMPANY_ACCESS_DENIED"), sonuc
    assert (sonuc["disa_durum"], sonuc["disa_kod"]) == (
        sonuc["uye_durum"], sonuc["uye_kod"]
    ), sonuc
    assert sonuc["aktif"] is True, sonuc


# --------------------------------------------------------------------------
# 3) BAŞARILI İMHA — ETKİLER VE KİLİT
# --------------------------------------------------------------------------
_IMHA = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    a_id = int(govde["companies"][0]["id"])
    admin_id = int(govde["user"]["id"])
    md = semayi_yansit()
    firmalar = md.tables["companies"]
    uyelikler = md.tables["user_company_memberships"]
    kayitlar = md.tables["activity_logs"]

    with engine.connect() as conn:
        ad = conn.execute(select(firmalar.c.name).where(
            firmalar.c.id == a_id)).scalar_one()

    # ADMIN IKI FIRMANIN UYESI: "digeri KORUNUR" iddiasi ancak boyle olculur.
    b_id = ikinci_kiraci(md, admin_id)
    # SADECE A'nin uyesi olan IKINCI bir kullanici: hesabinin YASADIGI ama
    # hicbir firmaya cozulemedigi buradan olculur.
    tek = client.post("/api/users", headers=h, json={
        "username": "tekfirma", "password": "TekFirma!2026",
        "display_name": "Tek Firma", "role": "admin"})
    assert tek.status_code in (200, 201), tek.text
    tek_id = int(tek.json()["id"])

    r = client.post("/api/company/erase", headers=h, json={"confirm_name": ad})
    govde_imha = r.json() if r.status_code < 500 else {"detail": r.text[:400]}

    # --- KILIT: DORT AYRI ROTA, DORT AYRI ROUTER ---------------------------
    # Rotalar BILEREK farkli routerlardan secildi (products, customers,
    # dashboard, work_orders): kapinin uca DEGIL kiraci cozumune takili
    # oldugu ancak boyle gorunur. HICBIRI imha diliminde YAZILMADI — kendi
    # kapisini tasiyan bir uc secilseydi kilit degil o kapi olculurdu.
    rotalar = {
        "urunler": client.get("/api/products", headers=h),
        "musteriler": client.get("/api/customers", headers=h),
        "panolar": client.get("/api/dashboard/summary", headers=h),
        "isemirleri": client.get("/api/work-orders", headers=h),
    }
    kilit = {k: {"durum": v.status_code, "kod": (v.json() or {}).get("code")}
             for k, v in rotalar.items()}
    disa = client.get("/api/company/export", headers=h)
    ikinci = client.post("/api/company/erase", headers=h, json={"confirm_name": ad})

    # --- B FIRMASI CALISMAYA DEVAM EDIYOR -----------------------------------
    hb = dict(h); hb["X-Company-ID"] = str(b_id)
    b_urun = client.get("/api/products", headers=hb)
    b_musteri = client.get("/api/customers", headers=hb)

    # --- KILIDIN GERCEK TASIYICISI: `is_active` YUKLEMI ---------------------
    # UYELIK GERI VERILIR (dogrudan veritabanina) ve ayni istek TEKRARLANIR.
    # Uyelik yeniden varken hala 403 gelmesi, kilidi tutan seyin uyelik
    # SILINMESI DEGIL `resolve_company`deki `is_active` yuklemi oldugunu
    # gosterir. Bu ayrim OLCULDU: yuklem kaldirilinca bu istek 200 doner.
    with engine.begin() as conn:
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=a_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
    uyelik_geri = client.get("/api/products", headers=h)

    # --- TEK UYELIKLI KULLANICI: HESAP YASIYOR, FIRMA YOK -------------------
    tek_giris = client.post("/api/auth/login",
                            json={"username": "tekfirma", "password": "TekFirma!2026"})

    with engine.connect() as conn:
        a_aktif = conn.execute(select(firmalar.c.is_active).where(
            firmalar.c.id == a_id)).scalar_one()
        b_aktif = conn.execute(select(firmalar.c.is_active).where(
            firmalar.c.id == b_id)).scalar_one()
        a_uyelik = conn.execute(select(uyelikler.c.id).where(
            uyelikler.c.company_id == a_id)).fetchall()
        b_uyelik = conn.execute(select(uyelikler.c.id).where(
            uyelikler.c.company_id == b_id)).fetchall()
        tek_var = conn.execute(select(md.tables["app_users"].c.id).where(
            md.tables["app_users"].c.id == tek_id)).fetchall()
        imha_kayit = conn.execute(select(kayitlar).where(
            kayitlar.c.action_type == "company.erased")).mappings().all()

    yaz("imha.json", {
        "imha_durum": r.status_code, "imha_govde": govde_imha,
        "kilit": kilit,
        "disa": {"durum": disa.status_code, "kod": (disa.json() or {}).get("code")},
        "ikinci": {"durum": ikinci.status_code, "kod": (ikinci.json() or {}).get("code")},
        "b_urun": b_urun.status_code, "b_musteri": b_musteri.status_code,
        "uyelik_geri": {"durum": uyelik_geri.status_code,
                        "kod": (uyelik_geri.json() or {}).get("code")},
        "tek_giris": tek_giris.status_code,
        "tek_firmalar": (tek_giris.json().get("companies")
                         if tek_giris.status_code == 200 else None),
        "tek_hesap_var": len(tek_var),
        "a_aktif": bool(a_aktif), "b_aktif": bool(b_aktif),
        "a_uyelik": len(a_uyelik), "b_uyelik": len(b_uyelik),
        "kayit": [{"action_type": x["action_type"], "resource_type": x["resource_type"],
                   "company_id": x["company_id"], "user_id": x["user_id"],
                   "summary": x["summary"], "details": x["details"]}
                  for x in imha_kayit],
    })
    print("IMHA TAMAM")
'''


@pytest.fixture(scope="module")
def imha(tmp_path_factory) -> dict:
    """İmha GERİ ALINAMAZ: senaryo tek koşar, iddialar onu okur."""
    dizin = tmp_path_factory.mktemp("kiraci-imha")
    _basarili(_kos(_IMHA, dizin / "i.db", dizin))
    return json.loads((dizin / "imha.json").read_text(encoding="utf-8"))


def test_imha_bayragi_ve_yanit(imha) -> None:
    """MUTASYON: ``update(companies).values(is_active=False)`` satırını
    kaldırmak bu testi KIRMIZI yapar."""
    assert imha["imha_durum"] == 200, imha["imha_govde"]
    assert imha["imha_govde"]["is_active"] is False, imha["imha_govde"]
    assert imha["a_aktif"] is False, imha
    # Kapsam dışı olan şey yanıtta AÇIKÇA yazılı: taze yedek aranmaz, yol
    # söylenir.
    assert "/api/company/export" in imha["imha_govde"]["export_hint"], imha["imha_govde"]


def test_uyelikler_yalniz_bu_firmadan_dustu(imha) -> None:
    """MUTASYON: ``delete(UYELIKLER)``ten ``where(company_id == cid)``
    yüklemini kaldırmak bu testi KIRMIZI yapar — B firmasının üyeliği de
    düşerdi."""
    # Sayim UYELIK GERI VERILDIKTEN sonra alindi (bkz. `test_kilit_...`), o
    # yuzden 1: imha aninda 0'a dustugu `removed_memberships` ile olculur.
    assert imha["a_uyelik"] == 1, imha
    assert imha["b_uyelik"] >= 1, imha
    assert imha["imha_govde"]["removed_memberships"] >= 1, imha["imha_govde"]
    # B firması ÇALIŞMAYA DEVAM EDİYOR: aynı kullanıcı, aynı jeton.
    assert imha["b_aktif"] is True, imha
    assert imha["b_urun"] == 200, imha
    assert imha["b_musteri"] == 200, imha


def test_tek_uyeligi_bu_firma_olan_kullanici_hesabini_korur(imha) -> None:
    """Hesap SİLİNMEZ: ``app_users`` GLOBALDİR ve bu ucun yetkisi tek kiracı
    kadardır. Kullanıcı giriş yapar ama hiçbir firmaya çözülemez."""
    assert imha["tek_hesap_var"] == 1, imha
    assert imha["tek_giris"] == 200, imha
    assert imha["tek_firmalar"] == [], imha


def test_kilit_kiraci_cozumunde_dort_ayri_rotada(imha) -> None:
    """MUTASYON: ``resolve_company``daki ``is_active`` yüklemini kaldırmak bu
    testi KIRMIZI yapar.

    Dört rota, dört ayrı router. Hepsi AYNI gövdeyle düşmeli: kapı uçta değil
    kiracı çözümünde olduğu için hiçbiri kendi kapısını taşımıyor.

    KOD ``COMPANY_ACCESS_DENIED`` — VE BU AYRIMSIZLIK BİLİNÇLİDİR. Kapalı
    firmaya ayrı bir kod vermek (``COMPANY_INACTIVE``) kilidi daha okunur
    yapardı ama kimliği doğrulanmış herhangi bir kullanıcıya, açıkça
    ``X-Company-ID`` yazarak bir kimliğin "var olan ama kapatılmış firma"
    olduğunu öğrenme yolu açardı; kiracı yaşam döngüsü dışarıya sızmaz.
    Ayrımsızlığın kendisi ``tests/test_tenancy_inactive_company.py``deki
    ``test_non_member_cannot_tell_active_from_inactive_from_absent`` ile
    ayrıca çivilidir.
    """
    for ad, gozlem in imha["kilit"].items():
        assert gozlem["durum"] == 403, (ad, gozlem)
        assert gozlem["kod"] == "COMPANY_ACCESS_DENIED", (ad, gozlem)
    # Dışa aktarım da AYNI kilidin arkasında: imhadan sonra veri çıkmaz.
    assert imha["disa"]["durum"] == 403, imha["disa"]
    assert imha["disa"]["kod"] == "COMPANY_ACCESS_DENIED", imha["disa"]
    # KİLİDİ TUTAN ŞEY ÜYELİK SİLME DEĞİL, `is_active` YÜKLEMİ. Üyelik
    # DOĞRUDAN VERİTABANINDAN geri verildiğinde bile istek 403'te kalmalı;
    # yüklem kaldırılırsa bu satır 200 ölçer ve KIRMIZI yanar. Üyelik geri
    # verilmeseydi bu mutasyon HİÇBİR ŞEYİ kırmazdı — üyelik silme tek
    # başına da 403 üretirdi ve kilit ölçülmemiş kalırdı.
    assert imha["uyelik_geri"]["durum"] == 403, imha["uyelik_geri"]
    assert imha["uyelik_geri"]["kod"] == "COMPANY_ACCESS_DENIED", imha["uyelik_geri"]


def test_ikinci_cagri_ara_katmanda_duser(imha) -> None:
    """İkinci çağrı UCA HİÇ VARMAZ — ölçüldü, varsayılmadı.

    Üyelik silindi ve firma kapalı, yani ara katman kiracıyı çözemiyor ve
    istek 403 ``COMPANY_ACCESS_DENIED`` alıyor. Ucun kendi 409 dalı bu
    yüzden HTTP'den erişilemez ve ayrıca — doğrudan çağrıyla —
    ``test_ikinci_cagri_dogrudan_409_uretir``de ölçülüyor. İkinci bir
    ``company.erased`` satırı yazılmadığı ``test_tek_aktivite_kaydi``da.
    """
    assert imha["ikinci"]["durum"] == 403, imha["ikinci"]
    assert imha["ikinci"]["kod"] == "COMPANY_ACCESS_DENIED", imha["ikinci"]


def test_ikinci_cagri_dogrudan_409_uretir(tmp_path: Path) -> None:
    """MUTASYON: ``if not bool(satir["is_active"]): raise
    FirmaZatenKapaliError()`` satırını kaldırmak bu testi KIRMIZI yapar.

    ÖLÇÜM NEDEN HTTP'DEN DEĞİL: bu dal bugün ara katmandan GEÇİLEREK
    erişilemez — kapalı firma ``resolve_company``de düşer ve uca hiç varılmaz
    (``test_ikinci_cagri_403``te ölçüldü). Dalı yine de ölçüyoruz çünkü tek
    denetim kaydı garantisini kilit gevşerse O tutar; ölçmenin tek yolu
    fonksiyonu DOĞRUDAN çağırmaktır.
    """
    sys.path.insert(0, str(BACKEND))
    from sqlalchemy.orm import Session

    from app.routers.kiraci_imha import (
        FirmaZatenKapaliError,
        ImhaTalebi,
        firmayi_imha_et,
    )
    from app.tenancy import companies, metadata

    motor = create_engine(f"sqlite:///{(tmp_path / 'i9.db').as_posix()}")
    metadata.create_all(motor)
    with Session(motor) as db:
        kapali = int(db.execute(insert(companies).values(
            name="Kapali Firma", is_active=False,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0])
        db.commit()

        class _Istek:
            state = type("S", (), {"company_id": kapali, "user": {"id": 1}})()

        with pytest.raises(FirmaZatenKapaliError) as hata:
            firmayi_imha_et(ImhaTalebi(confirm_name="Kapali Firma"), _Istek(), db)

    assert hata.value.kod == "COMPANY_ALREADY_INACTIVE"
    # ADIN DOĞRU OLMASI DALI DEĞİŞTİRMEZ: "zaten kapalı" denetimi isim
    # onayından ÖNCE gelir, yoksa yanlış ad 422 verir ve 409 gölgelenirdi.
    assert str(hata.value)


def test_tek_aktivite_kaydi(imha) -> None:
    """MUTASYON: ``log_activity`` çağrısını kaldırmak bu testi KIRMIZI
    yapar."""
    assert len(imha["kayit"]) == 1, imha["kayit"]
    kayit = imha["kayit"][0]
    assert kayit["action_type"] == "company.erased", kayit
    assert kayit["resource_type"] == "backup", kayit
    assert kayit["user_id"] is not None, kayit
    assert kayit["summary"], kayit
    ayrinti = json.loads(kayit["details"]) if isinstance(kayit["details"], str) \
        else kayit["details"]
    assert ayrinti["membership_count"] >= 1, ayrinti
    # Firma kapandıktan sonra adı yalnız denetim izinde kalır.
    assert ayrinti["company_name"], ayrinti


def test_katalog_girdisi_var() -> None:
    """MUTASYON: ``ACTION_TYPES``tan ``company.erased``ı silmek uç çalışma
    zamanında ``ValueError`` alır — kapı burada da yazılı."""
    sys.path.insert(0, str(BACKEND))
    from app.activity_log import ACTION_TYPES

    assert "company.erased" in ACTION_TYPES
    assert ACTION_TYPES["company.erased"]
    assert "company.exported" in ACTION_TYPES


# --------------------------------------------------------------------------
# 4) HTTP'DEN GEÇMEYEN YAZMA YOLU — ZAMANLAYICI
# --------------------------------------------------------------------------
def test_zamanlayici_kapatilmis_firmayi_atlar(tmp_path: Path) -> None:
    """MUTASYON: ``tum_firmalari_isle``deki ``WHERE is_active = :aktif``
    yüklemini kaldırmak bu testi KIRMIZI yapar.

    NEDEN AYRI BİR KAPI: bu döngü HTTP'den GEÇMEZ — uygulama sürecinde koşar
    ve kiracı çözümünü HİÇ görmez. Yumuşak imhanın kilidi orada olduğu için,
    yüklem olmadan kapatılmış bir kiracının bekleyen outbox olayları
    işlenmeye devam eder ve ``stock_movements``e YENİ satır yazardı. Yani
    "kapatıldı" iddiası, tam da denetlenmeyen tek yazma yolunda yalan olurdu.

    ÖLÇÜM firma SEÇİMİ düzeyinde: ``tum_firmalari_isle``nin okuduğu SQL,
    tablonun kendisine karşı koşturulur ve kapalı firmanın kimliğinin
    DÖNMEDİĞİ görülür. Tam bir outbox akışı kurmak bu iddiayı ölçmez —
    iddia "olay işlenmedi" değil, "firma HİÇ SEÇİLMEDİ"dir.
    """
    sys.path.insert(0, str(BACKEND))
    from app.field_stok_tuketici import tum_firmalari_isle
    from app.tenancy import companies, metadata

    kaynak = (BACKEND / "app" / "field_stok_tuketici.py").read_text(encoding="utf-8")
    secim = re.search(
        r'text\(\s*"(SELECT id FROM companies[^"]*)"\s*\)', kaynak
    )
    assert secim, "firma seçimi kaynakta bulunamadı"
    sql = secim.group(1)
    assert "is_active" in sql, sql

    engine = create_engine(f"sqlite:///{(tmp_path / 'z.db').as_posix()}")
    metadata.create_all(engine)
    simdi = datetime.now(timezone.utc)
    with engine.begin() as conn:
        aktif = conn.execute(insert(companies).values(
            name="Acik", is_active=True, created_at=simdi)).inserted_primary_key[0]
        kapali = conn.execute(insert(companies).values(
            name="Kapali", is_active=False, created_at=simdi)).inserted_primary_key[0]

    from sqlalchemy import text as _text

    with engine.connect() as conn:
        secilen = conn.execute(_text(sql), {"aktif": True}).scalars().all()

    assert int(aktif) in secilen, secilen
    assert int(kapali) not in secilen, secilen
    # Fonksiyonun KENDİSİ hâlâ çağrılabilir olmalı — yüklem imzayı bozmadı.
    assert callable(tum_firmalari_isle)


def test_pii_anonimlestirme_bu_dilimde_YOK() -> None:
    """ÖLÇÜLMEDİ / KAPSAM DIŞI — ve bu AÇIKÇA yazılı.

    5.1b "kapattı" der, "sildi" DEMEZ. Kişisel veri taşıyan satırlar olduğu
    gibi kalır; 5.1 keşfinde adı konmuş tablolar aşağıda. Bu test bir
    davranış ölçmez, KAPSAMI SABİTLER: uca bir gün "anonimleştirdim" diyen
    bir cümle eklenirse, o cümlenin arkasında duran kodun da eklenmiş olması
    gerektiğini burada yazılı olan liste hatırlatır.
    """
    kaynak = ROUTER.read_text(encoding="utf-8")
    for tablo in ("customers", "suppliers", "entity_contacts", "work_orders"):
        assert tablo in kaynak, (
            f"{tablo} PII listesinden düştü; kapsam notu kaynakta kalmalı"
        )
    # Uç bu tablolara DOKUNMUYOR: adları yalnız KAPSAM NOTUNDA geçer.
    agac = ast.parse(kaynak)
    docstringler = {
        ast.get_docstring(d, clean=False)
        for d in ast.walk(agac)
        if isinstance(d, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    govde = "\n".join(
        satir for satir in kaynak.splitlines()
        if not any(satir in (d or "") for d in docstringler)
    )
    for tablo in ("customers", "suppliers", "entity_contacts", "work_orders"):
        assert tablo not in govde, tablo
