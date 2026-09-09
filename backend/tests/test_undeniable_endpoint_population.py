"""Hiçbir rolün REDDEDİLEMEDİĞİ uçlar — sınıf olarak sabitlenir.

Konu: altı rolün altısının da taşıdığı bir izinle korunan uçlar.

--- KUSUR ----------------------------------------------------------------------

Bir izin, hiçbir rolün reddedilemediği bir uçta HİÇBİR BİLGİ TAŞIMAZ. Kapı
çalışır, günlüğe yazar, 403 döndürebilecek gibi durur — ama döndüremez, çünkü
tabloda o izni taşımayan rol yoktur. Ölçüldüğünde (develop `fd71326`):

    yetki kapısından geçen operasyon        : 326
    hiçbir rolün reddedilemediği operasyon  : 117
      read       89
      farm.view  14
      herd.view  14
    yöntem dağılımı                          : 114 GET, 3 POST

--- İKİ AYRI KATEGORİ, AYRI MUAMELE --------------------------------------------

**Tasarım gereği açık (114 GET).** ``read``/``farm.view``/``herd.view``
izinlerini altı rolün altısı da BİLEREK taşır: bu üründe her rol okur. Okuma
uçlarının reddedilemez olması bir kusur değil, ürün kararıdır. Bu dosya o kararı
değiştirmez; DEĞİŞTİĞİNDE ya da SESSİZCE BÜYÜDÜĞÜNDE görünür kılar — dördüncü
bir izin herkese verilirse aşağıdaki çapa kırmızı olur.

**Yazan uçlar (3 POST) — burada tasarım kararı YOKTUR.** Yazan bir operasyonun
middleware izni evrenselse, o operasyonu middleware katmanında hiçbir rolden men
edemezsiniz. Bugün üçü de ``/api/platform/backups`` altındadır ve router kendi
daha güçlü denetimini uygular (``require_platform_operator``: rol ``admin`` VE
kimlik operatör listesinde). Yani middleware izni ATILDIR.

Ama atıllık ÖLÇÜLMEMİŞTİ: bu üç uç yalnız rota envanterinde listeleniyordu,
hiçbir test gerçek bir istekle reddedildiklerini göstermiyordu. ``_authorize``
çağrısı bir gün silinse, uçlar altı rolün altısına da açılırdı ve hiçbir kapı
ateşlemezdi. Bu dosya o boşluğu kapatır.

**Kendi kapsamlı yazan uçlar (5.4c'de eklendi) — ÜÇÜNCÜ kategori.** Yukarıdaki
iki kategori "reddedilemez olması sorun değil, çünkü okuma" ya da "reddedilemez
görünüyor ama router zaten reddediyor" diyor. Push cihaz uçları İKİSİ DE
değildir: yazıyorlar VE her rolde gerçekten çalışıyorlar. Onları atıl ilan
etmek yalan, bir rolden men etmek ise ürünü bozmak olurdu (depo görevlisi de
telefonuna bildirim alabilmelidir). Kural bu yüzden DARALTILARAK genişletildi:
bir yazan uç evrensel izinle korunabilir, ANCAK VE ANCAK yazdığı satırın
öznesi ÇAĞIRANIN KENDİSİYSE — ve o sahiplik sınırı GERÇEK bir istekle
kanıtlanmak zorundadır. Ayrıntı ``SELF_SCOPED_WRITE_EXEMPTIONS``ın üstünde.

**PUBLIC yazan uçlar (WA1'de eklendi) — DÖRDÜNCÜ kategori ve AYRI BİR
EVREN.** Yukarıdaki üç kategori de "yetki kapısından geçen" uçlarla ilgili.
ÖLÇÜLDÜ: `_gated_operations()` `PUBLIC_API` üyelerini SESSİZCE atıyor, yani
`PUBLIC_API`ye eklenen YAZAN bir uç bu dosyanın hiçbir kuralına girmiyordu.
Kapının o uçlar için koşmaması doğru; GÖRÜNMEZ olmaları kusurdu — ve WA1'in
Meta webhook'u tam o sınıftan bir uçtur (public, yazan, hiçbir rol tarafından
reddedilemez çünkü hiçbir rol ona bakmaz).

Sınıf kuralı bu yüzden İKİNCİ EVRENE de yazıldı: `PUBLIC_API` ile muaf
tutulan her GET-DIŞI operasyon tek tek çapalanmak zorundadır
(`PUBLIC_SESSION_WRITE_EXEMPTIONS` ve `PUBLIC_WEBHOOK_EXEMPTIONS`), ve
webhook'un imza kapısı GERÇEK isteklerle kanıtlanır (YÖN E/F/G).

--- SINIF SABİTLENİR, ÖRNEKLER DEĞİL -------------------------------------------

Kural: **evrensel bir izinle korunan her GET-DIŞI operasyon, tek tek çapalanmak
ve ATIL OLDUĞU KANITLANMAK zorundadır.** Çapalanmamış yeni bir tanesi, kapı onu
hiç tanımasa bile KIRMIZIDIR — varsayılan reddir. Bilinen-kötü uçları sayan bir
kara liste değildir; 117 ucu tek tek yazmak da değildir.

Çapalar ``ROLE_PERMISSIONS``tan ya da rota tablosundan TÜRETİLMEZ, elle
yazılır. Türetseydik, kaynağı bozan bir mutasyon çapayı da kaydırır ve kapı
sessizce yeşil kalırdı — bu depoda üç kez yaşandı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:///./__undeniable.db")

from fastapi.routing import APIRoute  # noqa: E402

from app.auth import (  # noqa: E402
    ROLE_PERMISSIONS,
    SELF_SERVICE_API,
    has_permission,
    required_permission,
)
from app.main import PUBLIC_API, app  # noqa: E402

#: ELLE YAZILMIŞ ÇAPA — altı rolün ALTISININ da taşıdığı izinler.
#:
#: Her biri bilinçli bir ürün kararıdır:
#:   read       — her rol kendi ekranını okuyabilmeli; okuma yetkisi rol ayrımı
#:                yapmaz, ayrım YAZMA izinlerinde kurulur.
#:   farm.view  — Tarla V1 yetki modeli okumayı ayırmadı (mobil-erp#2).
#:   herd.view  — Hayvancılık V1 aynı deseni izledi (mobil-erp#17).
#:
#: Dördüncü bir izin altı role birden verilirse burası kırmızı olur: yeni bir
#: izin sınıfının sessizce bilgi taşımaz hâle gelmesi görünür olsun diye.
UNIVERSAL_PERMISSIONS = frozenset({"read", "farm.view", "herd.view"})

#: ELLE YAZILMIŞ ÇAPA — evrensel izinle korunan GET-DIŞI operasyonlar.
#:
#: Üçü de ``require_platform_operator`` ile korunur: rol ``admin`` OLMAK ZORUNDA
#: ve kullanıcı kimliği ``SUNGUR_PLATFORM_OPERATORS`` listesinde bulunmak
#: zorunda. Middleware izni (``read``) bu yüzden ATILDIR — ve atıllık aşağıda
#: gerçek isteklerle kanıtlanır, iddia edilmez.
INERT_WRITE_EXEMPTIONS = frozenset({
    ("POST", "/api/platform/backups"),
    ("POST", "/api/platform/backups/{name}/restore"),
    ("POST", "/api/platform/backups/{name}/verify"),
})

#: İKİNCİ ÇAPA — ve NEDEN İKİNCİ BİR KATEGORİ GEREKTİ (5.4c).
#:
#: Yukarıdaki çapa "middleware izni ATIL, router daha güçlü denetliyor" diyor
#: ve atıllığı GERÇEK bir istekle kanıtlıyor. Push cihaz uçları o cümleyi
#: KURAMAZ ve kurmaya çalışmak YALAN olurdu: `POST /api/push/devices` her
#: rolde GERÇEKTEN çalışmalıdır — depo görevlisi de telefonuna bildirim
#: alabilmelidir. Yani middleware izni atıl DEĞİL; sadece hiçbir rolü
#: reddetmiyor, çünkü reddetmesi İSTENMİYOR.
#:
#: O hâlde dosyanın sınıf kuralı ("yazan uçta tasarım kararı YOKTUR") bu uçlar
#: için YENİDEN yazılıyor ve DARALTILARAK yazılıyor: bir yazan uç evrensel
#: izinle korunabilir, ANCAK VE ANCAK yazdığı satırın öznesi ÇAĞIRANIN
#: KENDİSİYSE. Sınır o zaman ROL değil SAHİPLİKTİR ve sahiplik denetimi
#: middleware'de değil router'da olmak ZORUNDADIR (rol tablosu "bu satır
#: kimin" sorusunu soramaz).
#:
#: İDDİA EDİLMİYOR, ÖLÇÜLÜYOR. Aşağıdaki smoke İKİ YÖNÜ de gerçek istekle
#: gösteriyor:
#:   * YÖN C — `admin` OLMAYAN bir rol (satis) KENDİ cihazını kaydedebiliyor
#:     ve düşürebiliyor. Bu, "atıl" iddiasının burada GEÇERSİZ olduğunun
#:     tanığıdır; uçlar gerçekten açıktır ve açık olmaları karardır.
#:   * YÖN D — AYNI rol BAŞKASININ cihazını düşüremiyor (403). Bu, kararın
#:     bedelinin NEREDE durduğunun tanığıdır. Router'daki sahiplik yüklemi
#:     silinirse bu satır KIRMIZI olur.
#:
#: `GET /api/push/devices` bu kümede DEĞİL: GET'ler zaten "tasarım gereği
#: açık" kategorisindedir ve bu dosyanın yazma kuralına hiç girmezler.
SELF_SCOPED_WRITE_EXEMPTIONS = frozenset({
    ("POST", "/api/push/devices"),
    ("DELETE", "/api/push/devices/{device_id}"),
})

#: ÜÇÜNCÜ ÇAPA SINIFI — VE NEDEN GEREKTİ (WA1).
#:
#: ÖLÇÜLDÜ, VARSAYILMADI: `_gated_operations()` `PUBLIC_API` üyelerini
#: SESSİZCE atlıyor. Yani bu dosyanın bütün kuralları — sınıf kuralı dâhil —
#: yalnız YETKİ KAPISINDAN GEÇEN uçlar üzerinde koşuyordu. `PUBLIC_API`ye
#: eklenen YAZAN bir uç, yukarıdaki iki çapadan HİÇBİRİNE düşmüyordu ve bu
#: dosya onu HİÇ GÖRMÜYORDU. Kapının atlaması bir kusur değil (kapı
#: gerçekten koşmuyor); GÖRÜNMEZLİK kusurdu.
#:
#: WA1'in Meta webhook'u tam da bu sınıftandır: PUBLIC, YAZIYOR, ve hiçbir
#: rol tablosu onu reddedemez çünkü hiçbir rol ona hiç bakmaz.
#:
#: Kural bu yüzden İKİNCİ BİR EVRENE genişletildi ve orada da SINIF olarak
#: yazıldı: `PUBLIC_API` ile muaf tutulan her GET-DIŞI operasyon tek tek
#: çapalanmak zorundadır. Çapalanmamış yeni bir tanesi KIRMIZIDIR.
#:
#: İki alt küme AYRI, çünkü kimliğin yerini alan şey AYRI:
#:
#:   * `PUBLIC_SESSION_WRITE_EXEMPTIONS` — oturum KURAN ya da oturumsuz
#:     kimlik akışını yürüten uçlar. Oturum isteyemezler çünkü oturumu
#:     onlar üretir. Korumaları IP başına hız sınırı + tek kullanımlık
#:     token'dır (`app/routers/auth.py`).
#:   * `PUBLIC_WEBHOOK_EXEMPTIONS` — Meta webhook'u. Oturum isteyemez
#:     çünkü çağıran bir KULLANICI DEĞİL, üçüncü bir tarafın sunucusudur.
#:     Koruması, JSON AYRIŞTIRILMADAN ÖNCE HAM GÖVDE üzerinde doğrulanan
#:     `X-Hub-Signature-256` HMAC'idir — ve o koruma AŞAĞIDA GERÇEK
#:     İSTEKLERLE kanıtlanıyor (YÖN E/F/G), iddia edilmiyor.
#:
#: `GET /api/whatsapp/webhook` bu kümede DEĞİL: GET'ler bu dosyanın yazma
#: kuralına hiç girmez (`GET /api/push/devices` ile AYNI gerekçe).
PUBLIC_SESSION_WRITE_EXEMPTIONS = frozenset({
    ("POST", "/api/auth/forgot-password"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/resend-verification"),
    ("POST", "/api/auth/reset-password"),
    # SEC-10: E-posta doğrulama token'ı tüketen yazma ucu. Oturumu olmayan
    # kullanıcı tek kullanımlık token ile e-posta adresini onaylar.
    ("POST", "/api/auth/verify-email"),
})

PUBLIC_WEBHOOK_EXEMPTIONS = frozenset({
    ("POST", "/api/whatsapp/webhook"),
})

ROLES = ("admin", "yonetici", "muhasebe", "satis", "depo", "rapor")


def _walk(routes, prefix: str = ""):
    """``app.routes`` dahil edilen router'ları iç düğümde saklar."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
        else:
            context = getattr(route, "include_context", None)
            original = getattr(route, "original_router", None)
            if context is not None and original is not None:
                yield from _walk(original.routes, f"{prefix}{context.prefix}")


def _concrete(path: str) -> str:
    return "/".join(
        "orders" if part == "{kind}" else ("1" if part.startswith("{") else part)
        for part in path.split("/")
    )


def _gated_operations() -> list[tuple[str, str, str]]:
    """Yetki kapısından GERÇEKTEN geçen (yöntem, yol, somut yol) üçlüleri."""
    operations = sorted({
        (method, path)
        for path, route in _walk(app.routes)
        for method in sorted(route.methods or ())
        if method not in ("HEAD", "OPTIONS")
    })
    gated: list[tuple[str, str, str]] = []
    for method, path in operations:
        concrete = _concrete(path)
        if (method, path) in PUBLIC_API or path in PUBLIC_API or concrete in PUBLIC_API:
            continue
        if (
            (method, path) in SELF_SERVICE_API
            or path in SELF_SERVICE_API
            or concrete in SELF_SERVICE_API
        ):
            continue
        gated.append((method, path, concrete))
    return gated


def _public_write_operations() -> list[tuple[str, str]]:
    """`PUBLIC_API` ile muaf tutulan GET-DIŞI operasyonlar.

    `_gated_operations()`in TAM TERSİ: o, kapıdan geçenleri toplar ve
    `PUBLIC_API` üyelerini atar; bu, atılanların YAZAN olanlarını toplar.
    İkisi birlikte yazma yüzeyinin TAMAMINI kapsar — arada kalan hiçbir uç
    yoktur ve bu kapsamı `test_yazma_yuzeyi_ARADA_UC_BIRAKMIYOR` ölçer.
    """
    operations = sorted({
        (method, path)
        for path, route in _walk(app.routes)
        for method in sorted(route.methods or ())
        if method not in ("HEAD", "OPTIONS")
    })
    public: list[tuple[str, str]] = []
    for method, path in operations:
        if method == "GET":
            continue
        concrete = _concrete(path)
        if (method, path) in PUBLIC_API or path in PUBLIC_API or concrete in PUBLIC_API:
            public.append((method, path))
    return public


def _universal_permissions() -> frozenset[str]:
    """Altı rolün de taşıdığı izinler — rota tablosundan değil, ROL tablosundan."""
    every = {permission for granted in ROLE_PERMISSIONS.values() for permission in granted}
    every.discard("*")
    return frozenset(p for p in every if all(has_permission(role, p) for role in ROLES))


def test_role_table_still_has_exactly_the_six_known_roles() -> None:
    assert set(ROLE_PERMISSIONS) == set(ROLES)


def test_universal_permission_set_matches_its_anchor() -> None:
    """Dördüncü bir izin herkese verilirse burası kırmızı olur."""
    assert _universal_permissions() == UNIVERSAL_PERMISSIONS


def test_every_universal_permission_is_truly_undeniable() -> None:
    """Çapanın kendisi doğru mu: altı rol de gerçekten taşıyor mu."""
    for permission in UNIVERSAL_PERMISSIONS:
        for role in ROLES:
            assert has_permission(role, permission) is True, (role, permission)


def test_no_unanchored_write_is_gated_by_a_universal_permission() -> None:
    """SINIF KURALI — varsayılan RED.

    Evrensel izinle korunan yeni bir yazan uç, kapı onu hiç tanımasa bile
    kırmızıdır. Çapaya eklemek görünür bir düzenlemedir.
    """
    offenders = sorted(
        (method, path)
        for method, path, concrete in _gated_operations()
        if method != "GET"
        and required_permission(method, concrete) in UNIVERSAL_PERMISSIONS
        and (method, path) not in INERT_WRITE_EXEMPTIONS
        and (method, path) not in SELF_SCOPED_WRITE_EXEMPTIONS
    )
    assert not offenders, (
        "Evrensel izinle korunan ve ÇAPALANMAMIŞ yazan uç(lar) var; middleware "
        "katmanında hiçbir rolden men edilemezler: " + repr(offenders)
    )


def test_no_unanchored_PUBLIC_write_exists() -> None:
    """SINIF KURALI, İKİNCİ EVREN — varsayılan RED.

    `PUBLIC_API`ye eklenen yazan bir uç, hiçbir rol tablosunun reddedemediği
    bir uçtur: kapı onun için HİÇ KOŞMAZ. Çapalanmamış yeni bir tanesi
    KIRMIZIDIR.

    MUTASYON: `main.py`nin `PUBLIC_API` kümesine yeni bir yazan yol eklemek
    (ya da mevcut bir yazan ucu oraya taşımak) bunu KIRMIZI yapar.
    """
    capali = PUBLIC_SESSION_WRITE_EXEMPTIONS | PUBLIC_WEBHOOK_EXEMPTIONS
    offenders = sorted(op for op in _public_write_operations() if op not in capali)
    assert not offenders, (
        "PUBLIC_API ile muaf tutulmuş ve ÇAPALANMAMIŞ yazan uç(lar) var; "
        "yetki kapısı onlar için HİÇ koşmuyor: " + repr(offenders)
    )


def test_yazma_yuzeyi_ARADA_UC_BIRAKMIYOR() -> None:
    """Yazan her operasyon ÜÇ evrenden BİRİNDE — dördüncü bir kaçış yolu yok.

    Muafiyetin ÜÇ yolu var ve üçü de bilinçli:
      1. Yetki kapısından GEÇEN uçlar — bu dosyanın ilk iki çapası.
      2. `PUBLIC_API` — yukarıdaki iki PUBLIC çapası.
      3. `SELF_SERVICE_API` — kimliği doğrulanmış kullanıcının KENDİ oturumu
         üzerindeki işlemleri; kendi kapısı `tests/test_self_service_
         exemption.py`de ve üyeliği orada TEK TEK gerekçeli.

    Üçüncüsü burada TÜRETİLİYOR, çapalanmıyor: onun envanteri başka bir
    dosyanın işidir ve iki yerde tutmak sürüklenme üretirdi. Bu kapının
    ölçtüğü şey KAPSAMDIR — dördüncü bir muafiyet yolu açılırsa (ya da bir
    uç hiçbirine düşmezse) burası KIRMIZI olur.
    """
    tum_yazan = {
        (method, path)
        for path, route in _walk(app.routes)
        for method in sorted(route.methods or ())
        if method not in ("HEAD", "OPTIONS", "GET")
    }
    kapili = {(m, p) for m, p, _ in _gated_operations() if m != "GET"}
    self_servis = {
        (method, path)
        for method, path in tum_yazan
        if (method, path) in SELF_SERVICE_API
        or path in SELF_SERVICE_API
        or _concrete(path) in SELF_SERVICE_API
    }
    assert self_servis, "ölçüm boşa düştü: self-servis yazan uç bulunamadı"
    kapsanan = kapili | set(_public_write_operations()) | self_servis
    assert tum_yazan - kapsanan == set(), sorted(tum_yazan - kapsanan)


def test_UC_capa_AYRIK() -> None:
    """Bir uç aynı anda iki gerekçeye ait olamaz — gerekçeler birbirini yalanlar."""
    assert PUBLIC_SESSION_WRITE_EXEMPTIONS & PUBLIC_WEBHOOK_EXEMPTIONS == frozenset()
    kapisiz = PUBLIC_SESSION_WRITE_EXEMPTIONS | PUBLIC_WEBHOOK_EXEMPTIONS
    kapili = INERT_WRITE_EXEMPTIONS | SELF_SCOPED_WRITE_EXEMPTIONS
    assert kapisiz & kapili == frozenset()


def test_PUBLIC_capalar_BAYAT_DEGIL() -> None:
    """Silinmiş ya da artık public OLMAYAN bir giriş KIRMIZIDIR."""
    public = set(_public_write_operations())
    for op in sorted(PUBLIC_SESSION_WRITE_EXEMPTIONS | PUBLIC_WEBHOOK_EXEMPTIONS):
        assert op in public, (
            f"{op[0]} {op[1]} artık PUBLIC_API ile muaf değil; çapa bayat"
        )


def test_iki_capa_AYRIK() -> None:
    """Bir uç HEM atıl HEM kendi-kapsamlı olamaz — iki gerekçe birbirini yalanlar.

    Kesişselerdi hangi kanıtın o ucu koruduğu SORULAMAZ hâle gelirdi: atıllık
    "hiçbir rol geçemez" der, kendi-kapsamlılık "her rol geçer ama yalnız
    kendi satırına" der.
    """
    assert INERT_WRITE_EXEMPTIONS & SELF_SCOPED_WRITE_EXEMPTIONS == frozenset()


def test_every_anchored_exemption_still_exists_and_still_needs_the_anchor() -> None:
    """Çapa BAYATLAMAZ: silinmiş ya da artık evrensel olmayan bir giriş kırmızıdır."""
    gated = {(method, path): concrete for method, path, concrete in _gated_operations()}
    for method, path in sorted(INERT_WRITE_EXEMPTIONS | SELF_SCOPED_WRITE_EXEMPTIONS):
        assert (method, path) in gated, (
            f"{method} {path} artık yetki kapısından geçmiyor; çapa bayat"
        )
        assert required_permission(method, gated[(method, path)]) in UNIVERSAL_PERMISSIONS, (
            f"{method} {path} artık evrensel bir izinle korunmuyor; çapa gereksiz"
        )


def test_gets_gated_by_universal_permissions_are_the_declared_design() -> None:
    """Tasarım gereği açık olan GET'ler ölçülür ve BEYAN EDİLİR, gizlenmez."""
    universal_gets = [
        (method, path)
        for method, path, concrete in _gated_operations()
        if method == "GET" and required_permission(method, concrete) in UNIVERSAL_PERMISSIONS
    ]
    assert universal_gets, "ölçüm boşa düştü: evrensel izinli GET bulunamadı"
    for method, path in universal_gets:
        assert method == "GET", (method, path)


# --------------------------------------------------------------------------
# ATILLIK GERÇEK İSTEKLE KANITLANIR — iddia edilmez
# --------------------------------------------------------------------------

#: Kurulumun her adımı ETİKETLİ assert ile doğrulanır: bir mutasyon kurulumu
#: bozduğunda "kapı ateşlendi" ile "kurulum çöktü" ayırt edilebilsin diye.
_SMOKE = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from fastapi.testclient import TestClient

import app.main as m

BOOT = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
# Gövde, ŞEMAYA UYGUN gönderilir. Pydantic doğrulaması router gövdesinden
# ÖNCE koşuyor; eksik alanla 422 alınır ve o 422 bir REDDİN kanıtı değildir.
# Ölçmek istediğimiz şey doğrulama değil, yetki reddi.
YAZAN = (
    ("POST", "/api/platform/backups", {}),
    ("POST", "/api/platform/backups/dosya/restore", {"confirmation": "GERI YUKLE"}),
    ("POST", "/api/platform/backups/dosya/verify", {}),
)

c = TestClient(m.app)
first = c.post("/api/auth/login", json={"username": "admin", "password": BOOT})
assert first.status_code == 200, ("KURULUM admin login", first.status_code, first.text)
rotated = c.post(
    "/api/auth/change-password",
    json={"current_password": BOOT, "new_password": "AtilKapi!2026"},
    headers={"Authorization": "Bearer " + first.json()["access_token"]},
)
assert rotated.status_code == 200, ("KURULUM admin rotation", rotated.status_code, rotated.text)
AH = {"Authorization": "Bearer " + rotated.json()["access_token"]}

# --- YÖN A: ADMIN ama operatör listesinde DEĞİL -> 403 --------------------
# Middleware izni `read`; admin onu taşır. Reddi veren tek şey router'ın
# kendi denetimidir. Bu yüzden bu iki sütun ayrı ayrı ölçülüyor.
for method, path, body in YAZAN:
    answer = c.request(method, path, headers=AH, json=body)
    assert answer.status_code == 403, ("YON-A admin-operator-degil", path, answer.status_code, answer.text)

# --- YÖN B: OKUMA izni taşıyan ama admin OLMAYAN rol -> 403 ---------------
created = c.post("/api/users", headers=AH, json={
    "username": "atil_satis", "display_name": "Atil Satis",
    "password": "AtilKapi!2026x", "role": "satis"})
assert created.status_code == 201, ("KURULUM satis kullanici", created.status_code, created.text)

sc = TestClient(m.app)
login = sc.post("/api/auth/login",
                json={"username": "atil_satis", "password": "AtilKapi!2026x"})
assert login.status_code == 200, ("KURULUM satis login", login.status_code, login.text)
# Yeni hesap zorunlu parola rotasyonuyla doğar; rotasyon yapılmadan HER uç
# PASSWORD_CHANGE_REQUIRED döner ve o 403 yetki reddi DEĞİLDİR. Rotasyon
# self-servis uçtan yapılır (yetki kapısına hiç girmez).
sc_rot = sc.post(
    "/api/auth/change-password",
    json={"current_password": "AtilKapi!2026x", "new_password": "AtilKapi!2026y"},
    headers={"Authorization": "Bearer " + login.json()["access_token"]},
)
assert sc_rot.status_code == 200, ("KURULUM satis rotation", sc_rot.status_code, sc_rot.text)
SH = {"Authorization": "Bearer " + sc_rot.json()["access_token"]}

# Kurulum varsayımı: bu rol middleware izni `read`i GERÇEKTEN taşıyor —
# yani reddin kaynağı yetki kapısı DEĞİL, router olmalı.
okuma = sc.get("/api/products", headers=SH)
assert okuma.status_code == 200, ("KURULUM satis read", okuma.status_code, okuma.text)

for method, path, body in YAZAN:
    answer = sc.request(method, path, headers=SH, json=body)
    assert answer.status_code == 403, ("YON-B satis", path, answer.status_code, answer.text)

# --- YON C: KENDI KAPSAMLI YAZMA GERCEKTEN ACIK (5.4c) -------------------
# Bu bolum yukaridakinin TERSINI olcuyor ve olcmesi ZORUNLU: push uclarinin
# capasi "middleware izni atil" DEMIYOR, "her rol gecer ama YALNIZ kendi
# satirina" diyor. Ilk yari o cumlenin ILK yarisini kanitlar.
CIHAZ = "/api/push/devices"
admin_cihaz = c.post(CIHAZ, headers=AH,
                     json={"platform": "android", "token": "atil-admin-jeton"})
assert admin_cihaz.status_code == 201, ("YON-C admin kayit", admin_cihaz.status_code, admin_cihaz.text)
satis_cihaz = sc.post(CIHAZ, headers=SH,
                      json={"platform": "ios", "token": "atil-satis-jeton"})
assert satis_cihaz.status_code == 201, ("YON-C satis kayit", satis_cihaz.status_code, satis_cihaz.text)
# Liste de yalniz KENDI cihazlarini veriyor.
kendi = sc.get(CIHAZ, headers=SH)
assert kendi.status_code == 200, ("YON-C satis liste", kendi.status_code, kendi.text)
assert [d["token"] for d in kendi.json()] == ["atil-satis-jeton"], kendi.json()

# --- YON D: BASKASININ CIHAZI -> 403 -------------------------------------
# Kararin BEDELI tam olarak burada duruyor. Router'daki sahiplik yuklemi
# silinirse bu satir KIRMIZI olur ve capa gerekcesini kaybeder.
yasak = sc.delete("%s/%d" % (CIHAZ, admin_cihaz.json()["id"]), headers=SH)
assert yasak.status_code == 403, ("YON-D baskasinin cihazi", yasak.status_code, yasak.text)
# KENDI cihazini ise dusurebiliyor: red SAHIPLIKTEN geliyor, ROLDEN degil.
kendi_sil = sc.delete("%s/%d" % (CIHAZ, satis_cihaz.json()["id"]), headers=SH)
assert kendi_sil.status_code == 204, ("YON-D kendi cihazi", kendi_sil.status_code, kendi_sil.text)

# --- YON E/F/G: PUBLIC WEBHOOK'UN KAPISI HMAC (WA1) ----------------------
# Bu bolum ucuncu capa sinifini kanitliyor ve digerlerinden FARKLI bir sey
# olcuyor: burada REDDEDEN sey bir ROL DEGIL, bir IMZADIR. Rol tablosu bu
# uc icin HIC calismaz (`PUBLIC_API`), yani "hangi rol gecer" sorusunun
# cevabi yoktur; sorulabilecek tek soru "imzasiz gecer mi"dir.
import hashlib, hmac, json

WH = "/api/whatsapp/webhook"
SIR = os.environ["WHATSAPP_APP_SECRET"]
PNID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
GOVDE = json.dumps({
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": PNID},
        "messages": [{"id": "atil-wamid-1", "from": "905405995959",
                      "type": "text", "text": {"body": "merhaba"}}],
    }}]}],
}).encode("utf-8")


def imza(govde, sir):
    return "sha256=" + hmac.new(sir.encode("utf-8"), govde, hashlib.sha256).hexdigest()


# OTURUMSUZ ISTEMCI: ne Authorization ne cerez ne CSRF jetonu tasiyor.
# Gecmesi, `PUBLIC_API` uyeliginin GERCEKTEN etkili oldugunun kanitidir.
wc = TestClient(m.app)

# YON E: GECERLI IMZA -> 200. Uc GERCEKTEN aciktir ve acik olmasi karardir.
acik = wc.post(WH, content=GOVDE,
               headers={"Content-Type": "application/json",
                        "X-Hub-Signature-256": imza(GOVDE, SIR)})
assert acik.status_code == 200, ("YON-E gecerli imza", acik.status_code, acik.text)

# YON F: BOZUK IMZA -> 403. Kararin BEDELI burada duruyor: `verify_signature`
# cagrisi silinirse bu satir KIRMIZI olur ve capa gerekcesini kaybeder.
sahte = wc.post(WH, content=GOVDE,
                headers={"Content-Type": "application/json",
                         "X-Hub-Signature-256": imza(GOVDE, SIR + "x")})
assert sahte.status_code == 403, ("YON-F bozuk imza", sahte.status_code, sahte.text)

# YON G: IMZA HIC YOK -> 403, AYNI kod. Ayrim sizdirilmiyor: "secret
# yapilandirilmis mi" sorusu kimliksiz bir cagirana cevaplanmaz.
ciplak = wc.post(WH, content=GOVDE, headers={"Content-Type": "application/json"})
assert ciplak.status_code == 403, ("YON-G imzasiz", ciplak.status_code, ciplak.text)
assert ciplak.status_code == sahte.status_code, (ciplak.status_code, sahte.status_code)

print("ATIL-KAPI-TAMAM")
'''


def _run_smoke(database_url: str, workspace: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(workspace)
    env["AUTO_MIGRATE"] = "true"
    env["ENVIRONMENT"] = "development"
    env["BOOTSTRAP_ADMIN_PASSWORD"] = "AtilBootstrap!2026"
    # Operatör listesi BİLEREK boş: admin bile geçememeli.
    env["SUNGUR_PLATFORM_OPERATORS"] = ""
    # WA1: webhook UCU ANCAK bu uc ayar birden doluyken VARDIR; boş
    # bırakılsalardı YÖN E/F/G'nin üçü de 404 alır ve kapı hiçbir şey
    # ölçmezdi (ölçüldü, varsayılmadı).
    env["WHATSAPP_APP_SECRET"] = "AtilWebhookSir!2026"
    env["WHATSAPP_VERIFY_TOKEN"] = "AtilWebhookJeton!2026"
    env["WHATSAPP_PHONE_NUMBER_ID"] = "111222333444555"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    # Alt süreç KATI UTF-8 ile okunur. Windows'ta varsayılan kod sayfası
    # (cp1254) uygulamanın Türkçe günlük satırlarını çözemiyor ve test, kapının
    # sonucuyla ilgisiz bir UnicodeDecodeError ile kırmızıya düşüyordu.
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "ATIL-KAPI-TAMAM" in completed.stdout, completed.stdout


def test_anchored_write_exemptions_are_provably_inert(tmp_path: Path) -> None:
    """Beş çapalı uç GERÇEKTEN iddia ettikleri şeyi yapıyor mu.

    Tek smoke, İKİ AYRI iddiayı ölçüyor ve ikisi birbirinin TERSİDİR:

      * `INERT_WRITE_EXEMPTIONS` (üç yedek ucu): hiçbir rol geçemiyor (YÖN A/B).
        Router'ın `_authorize` çağrısı silinirse KIRMIZI.
      * `SELF_SCOPED_WRITE_EXEMPTIONS` (iki push ucu): her rol geçiyor ama
        YALNIZ kendi satırına (YÖN C/D). Router'ın sahiplik yüklemi silinirse
        KIRMIZI; uçlar bir rolden men edilirse de KIRMIZI.
      * `PUBLIC_WEBHOOK_EXEMPTIONS` (Meta webhook POST'u): OTURUMSUZ bir
        istemci geçiyor ama YALNIZ GEÇERLİ İMZAYLA (YÖN E/F/G). Burada
        reddeden şey bir ROL değil, HMAC'tir; `verify_signature` çağrısı
        silinirse KIRMIZI olur.
    """
    _run_smoke(f"sqlite:///{(tmp_path / 'atil.db').as_posix()}", tmp_path)
