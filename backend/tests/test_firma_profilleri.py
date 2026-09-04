"""Firma profilleri (göç `20260904_0068`): kanonik biçim, iki yazan yol, sıfır okuyan.

Bu dilim bir sütun AÇIYOR ve onu yazan İKİ yolu bağlıyor; hiçbir kapı, hiçbir
yetki kararı, hiçbir menü bu değere BAKMIYOR. Testlerin ağırlığı beş iddiada:

1. **KANONİK BİÇİM** — aynı küme her zaman aynı dizgiyi verir (ayıkla,
   tekilleştir, SIRALA). Bu çivilenmezse `"tuccar,ciftci"` ile
   `"ciftci,tuccar"` iki AYRI satır olur ve eşitlik SORULAMAZ hale gelir.
2. **BİLİNMEYEN BELİRTEÇ REDDEDİLİR ve red AİLE İÇİNDEDİR** — 422, ve kayıt
   akışında firma satırı AÇILMADAN reddedilir (yarım kiracı kalmaz).
3. **`''` İLE `None` AYRI ŞEYLERDİR** — biri "değiştirme", öteki "temizle".
   Birleştirilirse alanı göndermeyen bir istemcinin kaydı SESSİZCE silinirdi.
4. **KATLAMA YOKTUR** — `"CIFTCI"` reddedilir. Bu depo Türkçe katlamadan üç
   katmanda ısırıldı; kapalı ve makineye bakan bir kümeye dördüncü bir kopya
   eklemek o ayrışmayı büyütürdü.
5. **OKUYAN YOK** — `\\bprofiller\\b` `backend/app` altında YALNIZ dört
   dosyada geçer. Bu bir kapıdır: beşinci bir okuyan belirdiği gün KIRMIZI
   olur ve bu DOĞRUDUR, çünkü modül anahtarları geldiğinde bu testin
   kendisi o anahtarları DOĞRULAYAN teste dönüşmelidir.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.firma_profilleri import GECERLI_PROFILLER, profilleri_coz

BACKEND = Path(__file__).resolve().parents[1]


# ===========================================================================
# 1. KANONİK BİÇİM
# ===========================================================================

def test_KANONIK_bicim_SIRALAR_ve_TEKILLESTIRIR() -> None:
    """Aynı küme, hangi sırayla/kaç kez yazılırsa yazılsın TEK dizgi verir."""
    assert profilleri_coz("tuccar,ciftci") == "ciftci,tuccar"
    assert profilleri_coz("ciftci,tuccar") == "ciftci,tuccar"
    assert profilleri_coz("tuccar,ciftci,tuccar") == "ciftci,tuccar"
    # Boşluklar ayıklanır: `"a, b"` ile `"a,b"` AYNI kümedir.
    assert profilleri_coz("  tuccar ,  ciftci  ") == "ciftci,tuccar"


def test_SIRALAMA_kume_yineleme_sirasina_BIRAKILMAMIS() -> None:
    """Sıra ALFABETİKTİR ve çalıştırmadan çalıştırmaya DEĞİŞMEZ.

    `set` yineleme sırası PYTHONHASHSEED'e bağlıdır. Sıralama kaldırılırsa
    aynı girdi farklı satırlar yazdırır ve bu test kırmızı olur.
    """
    dort = "veteriner,tuccar,pazarci,ciftci"
    assert profilleri_coz(dort) == "ciftci,pazarci,tuccar,veteriner"
    assert profilleri_coz(dort) == profilleri_coz("ciftci,pazarci,tuccar,veteriner")


def test_BOS_olan_her_sey_BOS_KUME_verir() -> None:
    """`None`, `''`, boşluk ve yalnız ayraç — dördü de "HENÜZ SEÇİLMEDİ"."""
    for girdi in (None, "", "   ", ",", ",,", " , , "):
        assert profilleri_coz(girdi) == "", repr(girdi)


def test_coz_IDEMPOTENT_kanonigi_bozmaz() -> None:
    """Kanonik dizgi yeniden çözülünce KENDİSİNİ verir.

    Bu, PUT -> GET -> PUT turunun değeri kaydırmadığını çiviler.
    """
    for girdi in ("ciftci", "ciftci,tuccar", "ciftci,pazarci,tuccar,veteriner"):
        assert profilleri_coz(profilleri_coz(girdi)) == girdi


# ===========================================================================
# 2. BİLİNMEYEN BELİRTEÇ — RED, VE REDDİN AİLESİ
# ===========================================================================

def test_BILINMEYEN_belirtec_ValueError_ATAR_HTTPException_DEGIL() -> None:
    """Red `ValueError`dır; iki çağıran da Pydantic doğrulayıcısı olduğu için
    422'ye o katman çevirir. `HTTPException` atılsaydı bu modül FastAPI'ye
    bağlanırdı ve saf kalmazdı."""
    with pytest.raises(ValueError) as hata:
        profilleri_coz("ciftci,bakkal")
    assert "bakkal" in str(hata.value)
    # Geçerli kümenin TAMAMI hata metninde: operatör neyi yazabileceğini
    # görmeden düzeltemez.
    for belirtec in GECERLI_PROFILLER:
        assert belirtec in str(hata.value)


def test_BILINMEYENLER_hepsi_birden_ve_SIRALI_raporlanir() -> None:
    """İlkinde durmaz — iki yanlış yazımı olan operatör iki tur atmamalı."""
    with pytest.raises(ValueError) as hata:
        profilleri_coz("zeytinci,ciftci,bakkal")
    metin = str(hata.value)
    assert metin.index("bakkal") < metin.index("zeytinci"), metin


def test_GECERLI_kumenin_KENDISI_reddedilmez() -> None:
    for belirtec in GECERLI_PROFILLER:
        assert profilleri_coz(belirtec) == belirtec


# ===========================================================================
# 3. KATLAMA YOKTUR — BU BİLİNÇLİ
# ===========================================================================

@pytest.mark.parametrize("yazim", ["CIFTCI", "Ciftci", "cIftcI", "ÇİFTÇİ", "TUCCAR"])
def test_KATLAMA_YOK_buyuk_yazim_REDDEDILIR(yazim: str) -> None:
    """Belirteçler TAM eşleşir.

    Bu testin değeri esnekliği ölçmesi değil, YOKLUĞUNU çivilemesidir: biri
    `.lower()` eklerse ilk üç durum sessizce geçer, `ÇİFTÇİ` ise Türkçe
    katlama olmadan HÂLÂ geçmez — yani yarım bir esneklik, kullanıcıya
    tutarsız görünen bir uç üretirdi. Esneklik istenirse HANGİ katlama
    olduğu kendi kararıdır (`app/units.py` yukarı, `farm.py` aşağı katlıyor
    ve ikisi aynı denkliği ÜRETMİYOR).
    """
    with pytest.raises(ValueError):
        profilleri_coz(yazim)


# ===========================================================================
# 4. ŞEMA — SÜTUN GERÇEKTEN `TEXT NOT NULL DEFAULT ''`
# ===========================================================================

def test_SEMA_sutunu_NOT_NULL_ve_BOS_VARSAYILAN(tmp_path: Path) -> None:
    """Göç koştuktan sonra sütunun şekli ÖLÇÜLÜR, iddia edilmez.

    `nullable=False` + `server_default=''` ikisi birden gerekir: varsayılansız
    bir NOT NULL sütunu DOLU bir tabloya eklenemez ve göç iki diyalektte de
    düşerdi.
    """
    import sqlite3

    db = tmp_path / "sema.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    tamam = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr

    baglanti = sqlite3.connect(db)
    try:
        sutunlar = {
            satir[1]: {"tip": satir[2], "notnull": satir[3], "varsayilan": satir[4]}
            for satir in baglanti.execute("PRAGMA table_info(companies)")
        }
    finally:
        baglanti.close()
    assert "profiller" in sutunlar, sorted(sutunlar)
    assert sutunlar["profiller"]["tip"] == "TEXT", sutunlar["profiller"]
    assert sutunlar["profiller"]["notnull"] == 1, sutunlar["profiller"]
    assert sutunlar["profiller"]["varsayilan"] == "''", sutunlar["profiller"]


# ===========================================================================
# 5. OKUYAN YOK — KAPI
# ===========================================================================

def test_APP_ALTINDA_profiller_YALNIZ_DORT_DOSYADA_gecer() -> None:
    """Sütunu okuyan/yazan dosya kümesi ÇİVİLENDİ.

    Beklenen dörtlü: deposu (`tenancy.py`, bootstrap DDL), çözücüsü
    (`firma_profilleri.py`) ve YAZAN İKİ YOL (kayıt + firma ayarları).

    Beşinci bir dosya belirdiği gün bu test KIRMIZI olur ve bu DOĞRUDUR:
    modül anahtarları geldiğinde bu kapı, o anahtarları DOĞRULAYAN teste
    dönüşmelidir. Çivilenmemiş bir yokluk sessizce unutulur; çivilenmiş
    yokluk bir KARARDIR (#31'in `app/parti.py` duruşunun aynısı).

    `\\b` sınırı `profilleri_coz`u SAYMAZ: aranan şey SÜTUN ADIDIR,
    yardımcının adı değil.
    """
    desen = re.compile(r"\bprofiller\b")
    bulunan = set()
    for yol in (BACKEND / "app").rglob("*.py"):
        if desen.search(yol.read_text(encoding="utf-8")):
            bulunan.add(yol.relative_to(BACKEND).as_posix())
    assert bulunan == {
        "app/firma_profilleri.py",
        "app/tenancy.py",
        "app/routers/auth.py",
        "app/routers/companies.py",
    }, sorted(bulunan)


# ===========================================================================
# 6. İKİ YAZAN YOL — GERÇEK UÇLAR, YALITILMIŞ VERİTABANI
# ===========================================================================

_SMOKE = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.db import SessionLocal

ADMIN_PW = 'ProfilAyar!12345'


def admin_headers(client):
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h


def firma_profilleri(ad):
    db = SessionLocal()
    try:
        satir = db.execute(
            text('SELECT profiller FROM companies WHERE name=:ad'), {'ad': ad}
        ).fetchall()
    finally:
        db.close()
    return satir


def kayit(client, email, ad, **fazla):
    govde = {'company_name':ad,'email':email,'password':'ProfilTest!12345',
             'password_confirmation':'ProfilTest!12345','display_name':'Profil Sahibi',
             'phone':'5551112233','terms_accepted':True}
    govde.update(fazla)
    return client.post('/api/auth/register', json=govde)


with TestClient(app) as client:
    # === A) KAYIT: profil VERİLDİ -> KANONİK biçimde satıra düşer =========
    # İDDİA YANITTA DEĞİL SATIRDA ölçülüyor: `/auth/register` numaralandırma
    # karşıtı bir uçtur ve gövdesi HER durumda aynı 200'dür. Yanıta bakan bir
    # test hiçbir şey ölçmezdi.
    r = kayit(client, 'p1@ornek.com', 'Profil A', profiller='tuccar,ciftci,ciftci')
    assert r.status_code == 200, r.text
    assert r.json() == {'message': 'Doğrulama e-postası gönderildi.'}, r.json()
    assert firma_profilleri('Profil A') == [('ciftci,tuccar',)], firma_profilleri('Profil A')

    # === B) KAYIT: profil VERİLMEDİ -> boş küme, NULL DEĞİL ===============
    r = kayit(client, 'p2@ornek.com', 'Profil B')
    assert r.status_code == 200, r.text
    assert firma_profilleri('Profil B') == [('',)], firma_profilleri('Profil B')

    # === C) KAYIT: BİLİNMEYEN profil -> 422 ve FİRMA AÇILMAZ ==============
    # İkinci iddia birincisinden önemli: red Pydantic katmanında olduğu için
    # firma satırına HİÇ gelinmez. Red akışın içine konsaydı yarım bir kiracı
    # (firma + şube açılmış, kullanıcı açılmamış) geride kalabilirdi.
    r = kayit(client, 'p3@ornek.com', 'Profil C', profiller='ciftci,bakkal')
    assert r.status_code == 422, r.text
    assert firma_profilleri('Profil C') == [], firma_profilleri('Profil C')

    # === D) FİRMA AYARLARI: GET sütunu DÖNER =============================
    h = admin_headers(client)
    g = client.get('/api/company-settings', headers=h)
    assert g.status_code == 200, g.text
    assert 'profiller' in g.json(), g.json()
    onceki = g.json()['profiller']

    def ayar_yaz(**fazla):
        govde = {'negative_stock_policy':'block','credit_limit_policy':'block'}
        govde.update(fazla)
        return client.put('/api/company-settings', headers=h, json=govde)

    # === E) PUT yazar ve KANONİKLEŞTİRİR =================================
    p = ayar_yaz(profiller='veteriner,ciftci')
    assert p.status_code == 200, p.text
    assert client.get('/api/company-settings', headers=h).json()['profiller'] == 'ciftci,veteriner'

    # === F) ALAN GÖNDERİLMEZSE sütuna DOKUNULMAZ =========================
    # `model_fields_set` kalıbı: kardeş alanların hepsi böyle davranıyor.
    p = ayar_yaz()
    assert p.status_code == 200, p.text
    assert client.get('/api/company-settings', headers=h).json()['profiller'] == 'ciftci,veteriner'

    # === G) AÇIKÇA null TEMİZLER (500 DEĞİL) =============================
    # Sütun NOT NULL'dur; `None` olduğu gibi yazılsaydı bu satır 500 verirdi.
    p = ayar_yaz(profiller=None)
    assert p.status_code == 200, p.text
    assert client.get('/api/company-settings', headers=h).json()['profiller'] == ''

    # === H) BİLİNMEYEN belirteç PUT'ta da 422 ve DEĞER DEĞİŞMEZ ==========
    p = ayar_yaz(profiller='ciftci')
    assert p.status_code == 200, p.text
    p = ayar_yaz(profiller='ciftci,bakkal')
    assert p.status_code == 422, p.text
    assert client.get('/api/company-settings', headers=h).json()['profiller'] == 'ciftci'
'''


def _smoke_calistir(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    tamam = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr


def test_IKI_YAZAN_YOL_ucdan_uca_sqlite(tmp_path: Path) -> None:
    _smoke_calistir(f"sqlite:///{(tmp_path / 'firma-profilleri.db').as_posix()}")
