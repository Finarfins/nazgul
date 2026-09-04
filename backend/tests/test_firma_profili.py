"""Firma profili: kayıt anındaki iş kolu seçimi SAKLANIR, HİÇBİR ŞEYİ AÇMAZ.

Konu: Faz 5.2 (göç `20260904_0068`). Bu turun sözleşmesi ikiye ayrılır ve
ikisi de ölçülüyor:

1. Seçim DOĞRU saklanır — tekilleştirilmiş, SIRALI, tanınmayan değer 422.
2. Seçime bakan HİÇBİR DAVRANIŞ YOKTUR. Modül anahtarları ayrı bir iştir ve
   bu iddia düzyazıda bırakılmadı: `test_HICBIR_MODUL_profil_ADIYLA_ACILIP_KAPANMIYOR`
   dört profil adının `app/firma_profilleri.py` DIŞINDA hiç geçmediğini
   ölçüyor. Bir gün biri `if "veteriner" in profiller:` yazarsa o test
   KIRMIZI olur ve bu DOĞRUDUR — o gün bu kapı, anahtarı DOĞRULAYAN teste
   dönüşmelidir.

SIRALAMA NEDEN SAKLANIRKEN UYGULANIR: `pazarci,ciftci` ile `ciftci,pazarci`
aynı kümedir; iki ayrı dizgi olarak saklanırlarsa dizgi düzeyindeki her
eşitlik karşılaştırması sessizce yanlış cevap verir.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.firma_profilleri import (  # noqa: E402
    PROFILLER,
    SECILMEDI,
    FirmaProfili,
    profilleri_birlestir,
    profilleri_coz,
)


def _literal_degerleri(annotation) -> set[str]:
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


def test_PROFILLER_ile_Literal_AYRISMAZ() -> None:
    """Kanonik demet ile Pydantic'in gördüğü tip AYNI kümedir.

    `Literal` çalışma zamanında türetilemez (tip denetleyicisi statik değer
    ister), yani ikisi ELLE eşleşiyor. Elle eşleşen iki şey ayrışır; bu test
    ayrışmayı yakalar.
    """
    assert set(PROFILLER) == _literal_degerleri(FirmaProfili) == {
        "ciftci",
        "pazarci",
        "tuccar",
        "veteriner",
    }
    assert list(PROFILLER) == sorted(PROFILLER), "kanonik demet SIRALI olmalı"


def test_profiller_TEKILLESTIRILIR_ve_SIRALI_saklanir() -> None:
    """`pazarci,ciftci` -> `ciftci,pazarci`. Yinelenen düşer."""
    assert profilleri_birlestir(["pazarci", "ciftci"]) == "ciftci,pazarci"
    assert profilleri_birlestir(["pazarci", "ciftci", "pazarci"]) == "ciftci,pazarci"
    # Giriş sırası cevabı DEĞİŞTİRMEZ.
    assert profilleri_birlestir(["ciftci", "pazarci"]) == profilleri_birlestir(
        ["pazarci", "ciftci"]
    )
    assert profilleri_birlestir(list(reversed(PROFILLER))) == ",".join(PROFILLER)


def test_BOS_secim_SECILMEDI_dizgisidir() -> None:
    """Boş liste `''` olur ve `''` boş listeye çözülür — tur kapanıyor."""
    assert profilleri_birlestir([]) == SECILMEDI == ""
    assert profilleri_coz("") == []
    assert profilleri_coz(None) == []
    assert profilleri_coz("ciftci,pazarci") == ["ciftci", "pazarci"]


def test_TANINMAYAN_profil_IKINCI_KATMANDA_da_REDDEDILIR() -> None:
    """Şemaya güvenilmiyor: `profilleri_birlestir` kendi de reddediyor.

    Bu fonksiyonu Pydantic'ten geçmemiş bir çağıranın (betik, göç, gelecek
    bir uç) çağırması ENGELLENMİŞ DEĞİLDİR. Tek katmanlı doğrulama o çağıran
    ortaya çıktığı gün sessizce delinirdi.
    """
    with pytest.raises(ValueError, match="Geçersiz firma profili: kasap"):
        profilleri_birlestir(["kasap"])
    # Geçerli değerlerin YANINDA duran tek bir geçersiz değer de reddedilir.
    with pytest.raises(ValueError):
        profilleri_birlestir(["ciftci", "kasap"])


def test_SEMALAR_profilleri_ISTEGE_BAGLI_alir_ve_KUMEYI_dayatir() -> None:
    """Üç şema da `profiller` taşıyor, üçünde de kümе `Literal` ile kapalı."""
    from app.routers.auth import RegisterPayload
    from app.routers.companies import CompanyCreate, CompanyPolicyUpdate

    for sema in (RegisterPayload, CompanyCreate, CompanyPolicyUpdate):
        alan = sema.model_fields["profiller"]
        assert _literal_degerleri(alan.annotation) == set(PROFILLER), sema.__name__
        assert not alan.is_required(), f"{sema.__name__}.profiller ZORUNLU olmamalı"


def test_HICBIR_MODUL_profil_ADIYLA_ACILIP_KAPANMIYOR() -> None:
    """Bu PR'da hiçbir davranış profile BAĞLI DEĞİL — ölçülerek.

    Bir modülü profile göre açmak, profil ADINI anmayı gerektirir. Dört ad
    `app/firma_profilleri.py` dışında hiçbir yerde geçmiyor; geçtiği gün bu
    test kırmızı olur ve o gün kapı, anahtarı DOĞRULAYAN teste dönüşmelidir.

    Yorum ve belge dizgisinde anmak ÇAĞIRMAK DEĞİLDİR, ama burada gerek de
    yok: ölçüm metin sabitleri ÜZERİNDEN yapılıyor, yorumlar AST'ye girmez.
    """
    app_dir = BACKEND / "app"
    muaf = {app_dir / "firma_profilleri.py"}
    ihlaller: list[str] = []
    for yol in sorted(app_dir.rglob("*.py")):
        if yol in muaf:
            continue
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        # Belge dizgilerini ELE: onlar düzyazıdır, dal değildir.
        belgeler: set[int] = set()
        for tasiyici in ast.walk(agac):
            if not isinstance(
                tasiyici,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            govde = getattr(tasiyici, "body", None) or []
            if (
                govde
                and isinstance(govde[0], ast.Expr)
                and isinstance(govde[0].value, ast.Constant)
                and isinstance(govde[0].value.value, str)
            ):
                belgeler.add(id(govde[0].value))
        for dugum in ast.walk(agac):
            if (
                isinstance(dugum, ast.Constant)
                and isinstance(dugum.value, str)
                and id(dugum) not in belgeler
                and dugum.value in PROFILLER
            ):
                ihlaller.append(f"{yol.relative_to(BACKEND).as_posix()}:{dugum.lineno} {dugum.value!r}")
    assert not ihlaller, (
        "Profil adı firma_profilleri.py DIŞINDA geçiyor — bir modül profile "
        f"göre açılıp kapanıyor olabilir: {ihlaller}"
    )


def _profil_kimligi(dugum: ast.AST) -> str | None:
    """`profiller` SÜTUNUNU adlandıran bir ifade mi? Adı döner, değilse `None`.

    Üç şekil sayılır ve üçü de aynı sütunu okur:
    `profiller` (Name), `payload.profiller` / `companies.c.profiller`
    (Attribute), `govde["profiller"]` / `values["profiller"]` (Subscript).
    """
    if isinstance(dugum, ast.Name):
        return dugum.id if dugum.id == "profiller" else None
    if isinstance(dugum, ast.Attribute):
        return dugum.attr if dugum.attr == "profiller" else None
    if isinstance(dugum, ast.Subscript):
        dilim = dugum.slice
        if (
            isinstance(dilim, ast.Constant)
            and isinstance(dilim.value, str)
            and dilim.value == "profiller"
        ):
            return dilim.value
    return None


def _bos_varsayilan_koruyucusu(dugum: ast.AST, cagri_argumanlari: set[int]) -> bool:
    """`profilleri_birlestir(payload.profiller or [])` kalıbı MI?

    Bu `BoolOp` bir DAL DEĞİL, bir BOŞ-VARSAYILAN koruyucusudur: açıkça
    `null` gönderen istemcinin `NOT NULL` sütuna `None` yazmasını engeller
    ve davranışı profilin DEĞERİNE göre değiştirmez — hangi profil olursa
    olsun aynı yola gider. Kendi testi var
    (`test_ACIK_null_YAZMA_DALINA_duser_kaydedilmemis_alan_DEGILDIR`);
    kaldırılırsa orası kırmızı olur.

    Muafiyet DAR tutuldu: yalnız `or` ve yalnız BOŞ bir sabit ile, ve yalnız
    doğrudan `profilleri_birlestir`e argüman olarak verilmişken. `or "ciftci"`
    ya da bir `if` testinde duran aynı şekil MUAF DEĞİLDİR.
    """
    if not (isinstance(dugum, ast.BoolOp) and isinstance(dugum.op, ast.Or)):
        return False
    if id(dugum) not in cagri_argumanlari:
        return False
    if len(dugum.values) != 2:
        return False
    sol, sag = dugum.values
    if _profil_kimligi(sol) is None:
        return False
    return (isinstance(sag, (ast.List, ast.Tuple)) and not sag.elts) or (
        isinstance(sag, ast.Constant) and sag.value in ("", None)
    )


def _deger_dali_ihlalleri(kaynak: str, etiket: str) -> list[str]:
    """`profiller` sütununu adlandıran ifade bir DAL TESTİNDE geçiyor mu?

    YÜRÜNEN düğüm kümesi — bir KONUM eklemek buraya yazmakla olur:

    * `ast.If.test`, `ast.IfExp.test`, `ast.While.test`
    * `ast.Match.subject`      — `match govde["profiller"]:`
    * `ast.comprehension.ifs`  — `[x for x in y if firma.profiller]`
    * `ast.Assert.test`        — `assert payload.profiller`
    * ağacın HERHANGİ yerindeki `ast.BoolOp` ve `ast.Compare`

    Son madde konumdan bağımsız olduğu için KARŞILAŞTIRMA (`match
    govde["profiller"] == x:` gibi) üç yeni konumda ZATEN yakalanıyordu; ölçülen
    KAÇAK yalnız ÇIPLAK DOĞRULUK sınamasıydı (`match govde["profiller"]:`) ve üç
    konum tam bunun için eklendi.

    YÜRÜNMEYENLER, bilerek ve sayılarak:

    * `ast.Lambda`nın gövdesi AYRI bir konum olarak ele alınmıyor. `ast.walk`
      bütün ağacı gezdiği için lambda İÇİNDEKİ bir `if`/`and`/karşılaştırma
      zaten yakalanır; yakalanmayan şey ÇIPLAK gövdeli bir YÜKLEMdir
      (`filter(lambda f: f.profiller, firmalar)`) — orada dal sözdizimsel
      olarak ÇAĞRILANIN içindedir, bu dosyada değil.
    * Aynı sebeple dışarıda kalanlar: `case _ if profiller:`
      (`ast.match_case.guard`), `bool(f.profiller)` ve `any(f.profiller for f
      in firmalar)` (üreteç ELEMANI — `ifs` değil).
    * `case {"profiller": True}:` — bu bir DESEN (`ast.MatchMapping`), yukarıda
      anılan `guard` DEĞİLDİR: `profiller` orada bir ifade olarak değil bir
      eşleme ANAHTARI olarak duruyor, `_profil_kimligi` onu görmez.
    * YEREL DEĞİŞKEN DOLAYIMI: `p = company.profiller` sonra `if p:`. Bu,
      buradaki kaçışların EN ULAŞILABİLİRİDİR ve tek KASITSIZ olanıdır —
      diğerleri kapıdan kaçmak için özel bir şekil yazmayı gerektirir, bu ise
      sıradan bir ara değişkendir. Kapıyı değere DEĞİL ada bağlamadan
      kapatmanın yolu yok: dolayımı yakalamak veri akışı izlemek demektir ve
      bu ucuz bir AST çitinin işi değildir.

    Bunlar kapının BİLİNEN sınırıdır ve liste TÜKETİCİ DEĞİLDİR: kapı bir ispat
    değil, ucuz ve okunur bir ÇİTtir. Kardeş kapı (ADIYLA) profil adlarını
    ayrıca arıyor.
    """
    agac = ast.parse(kaynak, filename=etiket)
    cagri_argumanlari = {
        id(arg)
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Call)
        and isinstance(getattr(dugum, "func", None), (ast.Name, ast.Attribute))
        and (
            getattr(dugum.func, "id", None) == "profilleri_birlestir"
            or getattr(dugum.func, "attr", None) == "profilleri_birlestir"
        )
        for arg in dugum.args
    }
    # Muafiyet KONUMDAN bağımsız olsun diye alt ağacın TAMAMI işaretleniyor:
    # aksi halde koruyucu bir `assert`in içine sarıldığı gün yalancı kırmızı
    # verirdi. Muafiyetin DARLIĞI `_bos_varsayilan_koruyucusu`nda duruyor.
    muaf_dugumler: set[int] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.BoolOp) and _bos_varsayilan_koruyucusu(
            dugum, cagri_argumanlari
        ):
            muaf_dugumler.update(id(alt) for alt in ast.walk(dugum))

    ihlaller: list[str] = []
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.If, ast.IfExp, ast.While, ast.Assert)):
            testler = [dugum.test]
        elif isinstance(dugum, ast.Match):
            testler = [dugum.subject]
        elif isinstance(dugum, ast.comprehension):
            testler = list(dugum.ifs)
        elif isinstance(dugum, (ast.BoolOp, ast.Compare)):
            if id(dugum) in muaf_dugumler:
                continue
            testler = [dugum]
        else:
            continue
        tur = type(dugum).__name__
        for test in testler:
            for alt in ast.walk(test):
                if id(alt) in muaf_dugumler:
                    continue
                ad = _profil_kimligi(alt)
                if ad is None:
                    continue
                ihlaller.append(
                    f"{etiket}:{getattr(alt, 'lineno', '?')} {tur} icinde {ad!r}"
                )
    return ihlaller


def test_HICBIR_MODUL_profil_DEGERINE_gore_DALLANMIYOR() -> None:
    """Sütunun DEĞERİ hiçbir yerde bir DALIN testinde geçmiyor — ölçülerek.

    Kardeş kapı (`test_HICBIR_MODUL_profil_ADIYLA_ACILIP_KAPANMIYOR`) profil
    ADLARINI arıyor ve `if "veteriner" in profiller:` gibi ADLA yapılan
    kapıyı yakalıyor. Ama ADSIZ bir kapı ondan KAÇAR:
    `if govde["profiller"]:` ya da `if company.profiller:` hiçbir profil adı
    anmaz, yine de davranışı sütuna BAĞLAR — "profili olan kiracı şunu
    görür" cümlesi tam olarak budur ve bu PR'ın iddiası onun YOKLUĞUDUR.
    ÖLÇÜLDÜ: adsız kapı eklendiğinde kardeş test YEŞİL kalıyordu.

    Bu yüzden burada aranan şey ad değil KONUM: `profiller` sütununu
    adlandıran bir ifadenin bir DAL TESTİNDE geçmesi. Sütunu OKUMAK, YAZMAK ve
    ÇÖZMEK serbesttir; ona göre AYRILMAK değildir. Hangi konumların YÜRÜNDÜĞÜ
    ve hangilerinin BİLEREK dışarıda bırakıldığı `_deger_dali_ihlalleri`in
    belge dizgisinde ADIYLA sayılıdır; kapının BOŞ OLMADIĞI ise ayrı bir testle
    (`test_DEGER_KAPISI_UC_ADSIZ_KONUMU_da_goruyor`) ölçülüyor, çünkü gerçek
    `app/` ağacı temiz olduğu için burası bozulsa da yeşil kalabilirdi.

    `if "profiller" in payload.model_fields_set:` KAPSAM DIŞIDIR ve olması
    gerektiği gibidir: orada sınanan şey alanın GÖNDERİLİP gönderilmediğidir,
    DEĞERİ değil — düz bir metin sabitidir, sütunu adlandıran bir ifade değil.
    """
    ihlaller: list[str] = []
    app_dir = BACKEND / "app"
    muaf = {app_dir / "firma_profilleri.py"}
    for yol in sorted(app_dir.rglob("*.py")):
        if yol in muaf:
            continue
        ihlaller += _deger_dali_ihlalleri(
            yol.read_text(encoding="utf-8"), yol.relative_to(BACKEND).as_posix()
        )
    assert not ihlaller, (
        "Bir DAL firma profilinin DEGERINE bakiyor — bu PR'in iddiasi "
        "hicbir davranisin ona bagli OLMAMASIDIR. Modul anahtarlari geldigi "
        "gun bu kapi, anahtari DOGRULAYAN teste donusmelidir: " + str(ihlaller)
    )


def test_DEGER_KAPISI_UC_ADSIZ_KONUMU_da_goruyor() -> None:
    """Kapının KENDİSİ ölçülüyor: üç konumda ÇIPLAK doğruluk KIRMIZI olmalı.

    Bu test kapının BOŞ OLMADIĞINI çiviliyor. Kapı gerçek `app/` ağacına
    bakıyor ve orası (doğru olarak) TEMİZ; temiz bir ağaca bakan bir tarayıcı
    ise bozulduğu gün de yeşil kalır. ÖLÇÜLDÜ: `ast.Match` / `ast.comprehension`
    / `ast.Assert` eklenmeden önce aşağıdaki üç mutasyon YEŞİL geçiyordu —
    aynı konumlardaki KARŞILAŞTIRMA zaten yakalanıyordu, kaçan tek şey ÇIPLAK
    addı.

    Yeşil kalması gerekenler de burada duruyor, çünkü bir çit ancak neyi
    GEÇİRDİĞİ de ölçülünce çittir: profille İLGİSİZ bir üreteç koşulu, ve
    `profilleri_birlestir(... or [])` boş-varsayılan koruyucusu.
    """
    kirmizi = {
        "Match": textwrap.dedent(
            """
            def f(govde):
                match govde["profiller"]:
                    case _:
                        return 1
            """
        ),
        "comprehension": textwrap.dedent(
            """
            def f(y, company):
                return [x for x in y if company.profiller]
            """
        ),
        "Assert": textwrap.dedent(
            """
            def f(payload):
                assert payload.profiller
            """
        ),
    }
    for tur, kaynak in kirmizi.items():
        ihlaller = _deger_dali_ihlalleri(kaynak, f"mutasyon_{tur}.py")
        assert ihlaller, f"{tur} konumunda ÇIPLAK doğruluk sınaması KAÇTI"
        assert all(tur in i for i in ihlaller), ihlaller

    yesil = {
        "ilgisiz üreteç koşulu": textwrap.dedent(
            """
            def f(y):
                return [x for x in y if x == 1]
            """
        ),
        "boş-varsayılan koruyucusu": textwrap.dedent(
            """
            def f(payload, values):
                values["profiller"] = profilleri_birlestir(payload.profiller or [])
            """
        ),
        "koruyucu bir dal testinin İÇİNE sarıldığında": textwrap.dedent(
            """
            def f(payload):
                assert profilleri_birlestir(payload.profiller or [])
            """
        ),
    }
    for etiket, kaynak in yesil.items():
        assert _deger_dali_ihlalleri(kaynak, "yesil.py") == [], etiket

    # Muafiyet DAR: aynı şekil BOŞ OLMAYAN bir varsayılanla MUAF DEĞİLDİR.
    dar = textwrap.dedent(
        """
        def f(payload):
            return profilleri_birlestir(payload.profiller or ["ciftci"])
        """
    )
    assert _deger_dali_ihlalleri(dar, "dar.py"), "boş olmayan varsayılan muaf sayılmamalı"


def test_ACIK_null_YAZMA_DALINA_duser_kaydedilmemis_alan_DEGILDIR() -> None:
    """`profiller: null` "hiç göndermedi" DEĞİLDİR — yazma dalına düşer.

    Kusurun ÖN KOŞULU budur ve ölçülmeden bırakılırsa görünmez: şema
    `list[FirmaProfili] | None` olduğu için JSON `null` GEÇERLİ bir gövdedir
    ve `model_fields_set`e GİRER. Yani uç `if "profiller" in
    payload.model_fields_set:` dalına girer ve `None`u sütuna YAZMAYA
    çalışır — sütun `NOT NULL` olduğundan bu 500 demektir.

    Uçtaki koruma `payload.profiller or []`dir ve ONUN kırmızısı uçtan uca
    dumandadır (`test_kayit_ve_ayar_ucu_sqlite`): burada yalnız dalın
    GERÇEKTEN alındığı çivileniyor, çünkü bu iddia bir veritabanı
    gerektirmiyor ve ayrı durduğunda daha okunur.
    """
    from app.routers.companies import CompanyPolicyUpdate

    model = CompanyPolicyUpdate(
        negative_stock_policy="block", credit_limit_policy="block", profiller=None
    )
    assert "profiller" in model.model_fields_set, (
        "açık null `model_fields_set`e girmeli; girmezse uç bu değeri "
        "'hiç gönderilmedi' sanar ve mevcut seçimi sessizce korurdu"
    )
    assert model.profiller is None
    # Alan hiç gönderilmediğinde ise dal ALINMAZ ve mevcut değer korunur.
    dokunmayan = CompanyPolicyUpdate(
        negative_stock_policy="block", credit_limit_policy="block"
    )
    assert "profiller" not in dokunmayan.model_fields_set


def _kayit_dumani(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["TURNSTILE_SECRET_KEY"] = ""
    tamamlanan = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert tamamlanan.returncode == 0, tamamlanan.stdout + "\n" + tamamlanan.stderr


def test_kayit_ve_ayar_ucu_sqlite(tmp_path: Path) -> None:
    _kayit_dumani(f"sqlite:///{(tmp_path / 'firma-profili.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.tenancy import companies

ADMIN_PW = 'FirmaProfil!123'


def satir(firma_adi):
    with SessionLocal() as db:
        return db.execute(
            select(companies.c.profiller).where(companies.c.name == firma_adi)
        ).scalar_one()


def kayit(client, firma_adi, eposta, **ekstra):
    return client.post('/api/auth/register', json={
        'company_name': firma_adi, 'display_name': 'Kayit Eden',
        'email': eposta, 'phone': '5551112233',
        'password': 'KayitParola!123', 'password_confirmation': 'KayitParola!123',
        'terms_accepted': True, **ekstra})


with TestClient(app) as client:
    # --- 1) profiller GÖNDERİLMEDEN kayıt: 200 ve depoda '' -------------
    # Uç 201 DEĞİL 200 döner ve gövdesi BİLİNÇLİ olarak opaktır (hesap
    # sayımını engelliyor), bu yüzden iddia GÖVDEDE değil SATIRDA ölçülüyor.
    r = kayit(client, 'Profilsiz Firma', 'profilsiz@ornek.com')
    assert r.status_code == 200, r.text
    assert r.json() == {'message': 'Doğrulama e-postası gönderildi.'}, r.text
    assert satir('Profilsiz Firma') == '', repr(satir('Profilsiz Firma'))

    # --- 2) 'pazarci','ciftci' -> depoda SIRALI 'ciftci,pazarci' --------
    r = kayit(client, 'Karma Firma', 'karma@ornek.com',
              profiller=['pazarci', 'ciftci'])
    assert r.status_code == 200, r.text
    assert satir('Karma Firma') == 'ciftci,pazarci', repr(satir('Karma Firma'))

    # --- 3) TANINMAYAN değer -> 422, ve AİLE İÇİNDE ---------------------
    r = kayit(client, 'Kasap Firma', 'kasap@ornek.com', profiller=['kasap'])
    assert r.status_code == 422, r.text
    # Kayıt GERÇEKTEN oluşmadı: reddedilen istek yan etki bırakmaz.
    with SessionLocal() as db:
        assert db.execute(
            select(companies.c.id).where(companies.c.name == 'Kasap Firma')
        ).first() is None

    # --- 4) GET /api/company-settings profilleri LİSTE olarak döner -----
    giris = client.post('/api/auth/login',
                        json={'username': 'admin', 'password': 'admin123'})
    assert giris.status_code == 200, giris.text
    g = giris.json()
    h = {'Authorization': 'Bearer ' + g['access_token'],
         'X-Company-ID': str(g['companies'][0]['id'])}
    d = client.post('/api/auth/change-password', headers=h,
                    json={'current_password': 'admin123', 'new_password': ADMIN_PW})
    assert d.status_code == 200, d.text
    h['Authorization'] = 'Bearer ' + d.json()['access_token']

    ayar = client.get('/api/company-settings', headers=h)
    assert ayar.status_code == 200, ayar.text
    assert ayar.json()['profiller'] == [], ayar.text

    # PUT ile yazılıyor ve GERİ OKUNUYOR; sıra yine normalleşiyor.
    y = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy': 'block', 'credit_limit_policy': 'block',
        'profiller': ['veteriner', 'ciftci']})
    assert y.status_code == 200, y.text
    assert y.json()['profiller'] == ['ciftci', 'veteriner'], y.text
    tekrar = client.get('/api/company-settings', headers=h)
    assert tekrar.json()['profiller'] == ['ciftci', 'veteriner'], tekrar.text

    # Alan HİÇ gönderilmezse mevcut değer KORUNUR (model_fields_set ayrımı).
    client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy': 'block', 'credit_limit_policy': 'block'})
    assert client.get('/api/company-settings',
                      headers=h).json()['profiller'] == ['ciftci', 'veteriner']

    # PUT tanınmayan değeri de 422 ile reddeder.
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy': 'block', 'credit_limit_policy': 'block',
        'profiller': ['kasap']})
    assert kotu.status_code == 422, kotu.text

    # --- 5) AÇIKÇA `null` GÖNDERİLDİĞİNDE `''` YAZILIR, `None` DEĞİL ----
    # Şema `list[FirmaProfili] | None` olduğu için JSON `null` GEÇERLİ bir
    # gövdedir ve `model_fields_set`e GİRER — yani "hiç göndermedi" dalına
    # DÜŞMEZ, yazma dalına düşer. Sütun `NOT NULL` olduğundan `None` yazmak
    # 500 üretirdi; koruma `payload.profiller or []`dir.
    bos = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy': 'block', 'credit_limit_policy': 'block',
        'profiller': None})
    assert bos.status_code == 200, bos.text
    assert bos.json()['profiller'] == [], bos.text
    # İDDİA SATIRDA ÖLÇÜLÜYOR: sütunda `''` durmalı, `NULL` DEĞİL.
    with SessionLocal() as db:
        ham = db.execute(
            select(companies.c.profiller).where(companies.c.id == int(h['X-Company-ID']))
        ).scalar_one()
    assert ham == '', repr(ham)
    assert ham is not None, 'sütunda NULL var — NOT NULL sütununa None yazılmış'
    # Seçim GERÇEKTEN temizlendi (önceki tur ['ciftci','veteriner'] yazmıştı).
    assert client.get('/api/company-settings',
                      headers=h).json()['profiller'] == [], 'açık null seçimi temizlemeliydi'

    # --- 6) İKİNCİ KATMAN GERÇEKTEN VAR: sütun ham NULL'u REDDEDER -----
    # 0067'nin çivilediği türden bir YOKLUK değil, bir VARLIK: uygulama
    # koruması düşse bile veritabanı yazmayı durdurur. Ölçülmeden iddia
    # edilseydi "veritabanı beni korur" genellemesi olurdu.
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy import text as sql_text
    reddedildi = False
    try:
        with SessionLocal() as db:
            db.execute(sql_text('UPDATE companies SET profiller=NULL WHERE id=:i'),
                       {'i': int(h['X-Company-ID'])})
            db.commit()
    except IntegrityError:
        reddedildi = True
    assert reddedildi, 'profiller sütunu NULL KABUL ETTİ — NOT NULL ikinci katmanı YOK'

print('FIRMA PROFILI SMOKE OK')
'''
