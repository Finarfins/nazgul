"""Bir işi süre bütçesiyle koşturan yardımcı — uygulamadan bağımsız.

Kendi başına bir modül, çünkü ulaşılamayan bir veritabanını sınayan testler
``app.main``i import EDEMEZ: açılışta migration koşturulur ve veritabanı yoksa
o zaten patlar. Ölçülmek istenen şey açılış değil, ucun davranışı.
"""
from __future__ import annotations

import threading
from collections.abc import Callable


class KontenjanDolu(RuntimeError):
    """Aynı anda koşabilecek iş sayısı sınırına ulaşıldı.

    Beklemez: çağıran bunu hemen "sağlıksız" olarak yanıtlar. Kuyruğa almak,
    bütün mesele olan "prob her zaman yanıt verir" sözleşmesini bozardı.
    """


def sureli_kos(
    is_: Callable[[], None], sinir: int,
    kontenjan: threading.BoundedSemaphore | None = None,
) -> None:
    """``is_``i ayrı bir DAEMON iş parçacığında koşturur, ``sinir`` kadar bekler.

    Süre dolarsa ``TimeoutError`` yükseltir; iş parçacığı bırakılır. ``is_``
    kendi bir istisna yükseltirse o istisna çağırana aynen taşınır.

    NEDEN ``ThreadPoolExecutor`` DEĞİL — iki kez ısırdı ve ikisi de ölçüldü:

    1. ``with ThreadPoolExecutor(...)`` blok çıkışında ``shutdown(wait=True)``
       çağırır ve asılı kalan işin bitmesini bekler. Zaman aşımı yakalansa bile
       istek yine donardı; yani bütçe tam olarak işe yaraması gereken yerde
       çalışmazdı.
    2. Havuzu uzun ömürlü yapmak da yetmedi: havuz iş parçacıkları daemon
       DEĞİL ve yorumlayıcı çıkışındaki ``atexit`` kancası onları bekler. Donmuş
       bir veritabanı, testte sürecin kapanmasını 195 saniye geciktirdi;
       üretimde ``stop_grace_period`` boyunca aynısını yapardı.

    Daemon iş parçacığı ikisini birden kapatıyor: ne istek bekler ne kapanış.
    İş parçacığı İPTAL EDİLMEZ — sürücünün içinde bloke ve kesilemez; sunucu
    döndüğünde ya da ``connect_timeout`` dolduğunda kendi kendine biter.
    """
    # KONTENJAN İŞ PARÇACIĞI SAYISINI SINIRLAR. Süre bütçesi dolunca iş
    # parçacığı BIRAKILIYOR (kesilemez); kontenjan olmadan her asılı prob bir
    # iş parçacığı daha eklerdi ve sayı istek sayısıyla büyürdü — ölçüldü:
    # donmuş veritabanında 10 eş zamanlı istek 6 → 35.
    #
    # Slotu ÇAĞIRAN değil İŞÇİ bırakır. Çağıran zaman aşımıyla döndüğünde iş
    # parçacığı hâlâ yaşıyor; slotu orada bırakmak sayıyı sınırlamazdı.
    if kontenjan is not None and not kontenjan.acquire(blocking=False):
        raise KontenjanDolu("eşzamanlı iş sınırına ulaşıldı")

    kutu: dict[str, BaseException] = {}
    bitti = threading.Event()

    def sarmal() -> None:
        try:
            is_()
        except BaseException as exc:  # noqa: BLE001 - çağırana aynen taşınıyor
            kutu["hata"] = exc
        finally:
            # Sıra bilinçli: önce slot, sonra bayrak. Tersi olsaydı çağıran
            # dönerken slot hâlâ tutulu olur ve hemen ardından gelen istek
            # sebepsiz "kontenjan dolu" yerdi.
            if kontenjan is not None:
                kontenjan.release()
            bitti.set()

    # SLOT İŞÇİYE TESLİM EDİLENE KADAR ÇAĞIRANIN SORUMLULUĞUNDA. `start()`
    # yükseltirse (en akla yatkını iş parçacığı/kaynak tükenmesi) işçi HİÇ var
    # olmaz, yani `sarmal`ın `finally`si de hiç koşmaz. Burada bırakılmazsa slot
    # kalıcı olarak kaybolur: sınır kadar tekrarı hazırlığı sonsuza dek doymuş
    # bırakır — baskı geçtikten sonra bile.
    try:
        threading.Thread(target=sarmal, name="sure-butcesi", daemon=True).start()
    except BaseException:
        if kontenjan is not None:
            kontenjan.release()
        raise
    if not bitti.wait(sinir):
        raise TimeoutError(f"iş {sinir} sn içinde bitmedi")
    if "hata" in kutu:
        raise kutu["hata"]
