"""Testler için DONMUŞ iş saati — ölçüm aleti, uygulama kodu DEĞİL.

`NAZGUL_DONMUS_GUN=YYYY-MM-DD` ortam değişkeni verildiğinde `uygula()`
`app.business_time.business_now` fonksiyonunu o günün İstanbul 12:00'ına
sabitler. `business_today` onu modül global'inden çağırdığı için tek yama
ikisini de kapsar; `from ..business_time import business_today` ile ADINI
bağlayan her rota da aynı sabit günü okur.

Neden var: `test_v3_harman_vadeli` (ve PostgreSQL ikizi) vadeleri sunucunun
saatinden türetir. "Donmuş saatte yeşil" iddiası ancak aleti depoda olan bir
ölçümle tekrarlanabilir; depoda olmayan bir aletle alınmış ölçüm ÖLÇÜM
SAYILMAZ. Bu dosya o alettir ve `_SMOKE` metninin İLK satırında çağrılır —
yani SQLite testinin PYTHONPATH'i yeniden yazan alt sürecinde de, PG
ikizinin aynı süreçteki `exec`'inde de aynı alet çalışır.

Sessiz başarısızlık YASAK: değişken verilmiş ama yama uygulanamıyorsa
(`app` paketi erişilemez, değer bozuk, ya da yamadan sonra `business_today`
hâlâ donmuş günü döndürmüyorsa) `CIKIS_KODU` (97) ile ÇIKAR ve ölümü
stderr'e yazar. Kullanıcı kodu koşmadan ölür; "donmuş saatte yeşil" ancak
bu yüzden delildir. Değişken yoksa hiçbir şey yapmaz ve `None` döner.

Uygulama kodu (`app/`) bu modülü BİLMEZ ve import ETMEZ. Bu iddia artık
gelenek değil KAPIDIR: `tests/test_donmus_saat_kapisi.py` hem `app.main`
import grafiğini hem de `app/` altındaki her `.py` dosyasının AST'sini
tarar ve mutasyonla kırmızıya döndüğü gösterilmiştir.

ÜRETİMDE RED
------------
`uygula()` çağrıldığında `ENVIRONMENT` ham ortam değişkeni `production` ise
hiçbir şey yamalamadan `CIKIS_KODU` ile ÖLÜR — donmuş gün verilmiş olsun ya
da olmasın. Sebep ölçülmüştür: üretim imajı `COPY backend/ ./backend/` ile
bu dosyayı da taşır ve `.dockerignore` test dosyalarını dışlamaz, yani alet
üretimde diskte DURUR. Tek bir `import donmus_saat; donmus_saat.uygula()`
satırı canlı veride `/api/receivables` satırlarını ACIK -> VADESI_GECTI'ye
kaydırırdı; vade, PHI/REI ve karantina "bugün"e bağlıdır.

Karar HAM ORTAM DEĞİŞKENİYLE verilir, `app.config.settings` ile DEĞİL:
(1) `app.config` import edilseydi alet uygulama yapılandırmasına bağlanır ve
modül düzeyinde yan etki üretirdi; (2) `Settings` nesnesi süreç içinde
kurulabilir/değiştirilebilir, yani kapı olarak güvenilmez — ham değişken
konteynerin gerçekten taşıdığı şeydir; (3) `ENVIRONMENT` tanımsızken
`settings.environment` varsayılanı `development`'tır, bu yüzden ek bir
`settings` kontrolü hiçbir yeni red üretmez. `ENVIRONMENT` tanımsızsa
alet ÇALIŞIR: geliştirme ve CI'nin varsayılan hâli budur.

TAM ÖZ-DENETİM
--------------
`business_time.business_now` yamalandıktan sonra `sys.modules` içindeki her
`app` / `app.*` modülü taranır ve yamadan ÖNCEKİ fonksiyon nesnesine `is`
ile eşit her öznitelik (ERKEN BAĞLANAN takma ad) donmuş fonksiyona yeniden
bağlanır. Ölçüldü: `app/labels.py:31` ve `app/routers/analytics.py:6`
`from ...business_time import business_now` ile ADI bağlar; bu modüller
`uygula()`dan ÖNCE import edilmişse tek modül yaması onlara ULAŞMAZ ve alet
"ETKIN" yazıp 0 ile çıkarken o çağrı yerleri gerçek saati okumaya devam
ederdi — YARI DONMUŞ ve sessiz. Bulunan her takma ad yamadan sonra
ÇAĞRILARAK doğrulanır; biri hâlâ donmuş günü döndürmüyorsa alet ölür.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

ORTAM_ANAHTARI = "NAZGUL_DONMUS_GUN"
ORTAM_ADI_ANAHTARI = "ENVIRONMENT"
URETIM_ADI = "production"
# Yalnızca `tests/test_donmus_saat_kapisi.py` içindir: takma ad yamasını
# kapatarak öz-denetimin GERÇEKTEN kırmızıya döndüğünü gösterir. Hiç
# gösterilmemiş bir denetim denetim değildir.
TAKMA_ADSIZ_ANAHTARI = "NAZGUL_DONMUS_SAAT_ALIASSIZ"
CIKIS_KODU = 97
ETKIN_ISARETI = "DONMUS_SAAT_ETKIN"
OLUMCUL_ISARETI = "DONMUS_SAAT_OLUMCUL"
TAKMA_AD_ISARETI = "DONMUS_SAAT_TAKMA_ADLAR"


def _olumcul(neden: str) -> None:
    sys.stderr.write(
        f"{OLUMCUL_ISARETI}: {neden}; hiçbir kullanıcı kodu koşmadan "
        f"{CIKIS_KODU} ile çıkılıyor (cwd={os.getcwd()!r})\n"
    )
    sys.stderr.flush()
    os._exit(CIKIS_KODU)


def uygula() -> date | None:
    """Ortam değişkeni varsa saati dondurur ve donmuş günü döndürür.

    `ENVIRONMENT=production` ise HİÇBİR ŞEY yamalamadan 97 ile ölür.
    """

    # ÜRETİM REDDİ ilk sıradadır: donmuş gün okunmadan, `app` import
    # edilmeden ve özel test bayrakları okunmadan önce.
    if os.environ.get(ORTAM_ADI_ANAHTARI, "").strip().lower() == URETIM_ADI:
        _olumcul(f"{ORTAM_ADI_ANAHTARI}={URETIM_ADI} iken saat dondurulamaz")

    deger = os.environ.get(ORTAM_ANAHTARI)
    if not deger:
        return None
    try:
        import app.business_time as business_time
    except Exception as exc:  # noqa: BLE001 - ne olursa olsun gürültülü öl
        _olumcul(f"app.business_time yüklenemedi ({exc!r})")
    try:
        yil, ay, gun = (int(parca) for parca in deger.split("-"))
        donmus = datetime(yil, ay, gun, 12, 0, 0, tzinfo=business_time.ISTANBUL)
    except (ValueError, OverflowError) as exc:
        _olumcul(f"{ORTAM_ANAHTARI}={deger!r} çözülemedi ({exc!r})")

    orijinal_business_now = business_time.business_now

    def business_now() -> datetime:
        return donmus

    business_time.business_now = business_now

    # `business_time` kendi özniteliği yukarıda değiştiği için taramada
    # ARTIK eşleşmez; geriye yalnızca ERKEN BAĞLANAN takma adlar kalır.
    takma_adlar: list[tuple[object, str, str]] = []
    for modul_adi, modul in list(sys.modules.items()):
        if modul is None:
            continue
        if modul_adi != "app" and not modul_adi.startswith("app."):
            continue
        try:
            oznitelikler = list(vars(modul).items())
        except TypeError:  # __dict__'i olmayan egzotik modül nesneleri
            continue
        for ad, mevcut in oznitelikler:
            if mevcut is orijinal_business_now:
                takma_adlar.append((modul, ad, f"{modul_adi}.{ad}"))

    if os.environ.get(TAKMA_ADSIZ_ANAHTARI) != "1":
        for modul, ad, _tam_ad in takma_adlar:
            setattr(modul, ad, business_now)

    # Yamalandı denmesi yetmez: bulunan her takma ad ÇAĞRILARAK doğrulanır.
    for modul, ad, tam_ad in takma_adlar:
        try:
            okunan = getattr(modul, ad)().date()
        except Exception as exc:  # noqa: BLE001
            _olumcul(f"erken bağlanan business_now yamalanamadı: {tam_ad} ({exc!r})")
        if okunan != donmus.date():
            _olumcul(f"erken bağlanan business_now yamalanamadı: {tam_ad}")

    if business_time.business_today() != donmus.date():
        _olumcul("yama uygulandı ama business_today donmuş günü döndürmüyor")
    sys.stderr.write(
        f"{ETKIN_ISARETI} pid={os.getpid()} business_now -> {donmus.isoformat()}\n"
    )
    sys.stderr.write(
        f"{TAKMA_AD_ISARETI} {' '.join(tam_ad for _m, _a, tam_ad in takma_adlar) or '(yok)'}\n"
    )
    sys.stderr.flush()
    return donmus.date()
