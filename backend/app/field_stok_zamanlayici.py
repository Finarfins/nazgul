"""Application-process scheduler for the measured field stock consumer."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .db import SessionLocal
from .field_stok_tuketici import tum_firmalari_isle


LOGGER_NAME = "yerel_hesap.field_stok_zamanlayici"
logger = logging.getLogger(LOGGER_NAME)

#: AKTIF thread'in durdurma bayragi. HER THREAD KENDI bayragini tasir; bu
#: bayrak modul duzeyinde PAYLASILAN tek bir Event OLAMAZ.
#:
#: OLCULEN KUSUR: tek paylasilan bir Event ile, `join(timeout=5)` zaman
#: asimina ugradiginda (`_dur.wait()` yalnizca donguler ARASINDA uyanir;
#: gercek bir birikim 5 saniyeyi asar) thread SET edilmis bayrakla hayatta
#: kaliyordu. Sonraki `baslat` onu "yasiyor" diye SAHIPLENIYOR, derinligi 1
#: yapiyor ve bayragi TEMIZLEMIYORDU; eski thread ise bir sonraki kontrolde
#: sonlaniyordu. Sonuc: derinlik 1, elde OLU bir thread nesnesi, kosan HICBIR
#: tuketici ve gunlukte TEK BIR SATIR YOK. Bayrak thread'e ait olunca eski
#: thread kendi SET bayragiyla sonlanir, yenisi TAZE bir bayrakla acilir ve
#: ikisi birbirini bastirmaz.
_dur: threading.Event | None = None
_thread: threading.Thread | None = None
#: Kac ic ice yasam dongusu bu zamanlayiciyi acik tutuyor. Bir `TestClient`
#: baska bir `TestClient` icinde acildiginda (olculdu: dort PostgreSQL dosyasi
#: bunu yapiyor, ikisi Barrier ile ES ZAMANLI) ayni surecte lifespan IKINCI kez
#: kosar. Bunu bir kurulum hatasi saymak uygulamayi baslatmiyordu; ic ice acilis
#: artik SAYILIR, en distaki kapanis durdurur. Sayac ve `_thread` birlikte
#: degistigi ve es zamanli cagrildigi icin ikisi de `_kilit` altindadir.
_derinlik = 0
_kilit = threading.RLock()


def bir_dongu_calistir() -> dict[str, int]:
    """Run one all-company cycle and log every outcome, including no work."""
    with SessionLocal() as db:
        sonuc = tum_firmalari_isle(db)

    kovalar = " ".join(f"{anahtar}={deger}" for anahtar, deger in sonuc.items())
    if sonuc.get("girdi", 0) == 0:
        logger.info("Field stok outbox dongusu calisti; olay bulunmadi; %s", kovalar)
    else:
        logger.info("Field stok outbox dongusu tamamlandi; %s", kovalar)
    return sonuc


def _dongu(aralik_saniye: int, dur: threading.Event) -> None:
    # Bayrak PARAMETRE ile gelir: bu thread KENDI bayragina bakar, moduldeki
    # guncel bayraga DEGIL. Yerine yeni bir thread acilmis olsa bile bu
    # thread yalnizca kendisine verilen `dur` ile sonlanir.
    while not dur.is_set():
        try:
            bir_dongu_calistir()
        except Exception:
            logger.exception("Field stok outbox dongusu basarisiz")
        dur.wait(aralik_saniye)


def _yeni_thread(*, target: Callable[[], None], name: str) -> threading.Thread:
    return threading.Thread(target=target, name=name, daemon=True)


def baslat_field_stok_zamanlayici(aralik_saniye: int) -> None:
    """Start the in-process scheduler; thread start errors intentionally escape."""
    global _thread, _derinlik, _dur
    with _kilit:
        # IC ICE YASAM DONGUSU. Thread zaten yasiyorsa IKINCI bir tuketici
        # ISTENMEZ: tek thread yeterlidir ve iki thread ayni outbox'i yarisirdi.
        # Eskiden burasi RuntimeError atiyordu ve `main.py` bunu BILEREK
        # yakalamadigi icin ic ice acilan her uygulama BASLAMIYORDU.
        #
        # SAHIPLENME KOSULU BAYRAGI DA OKUR. Yasayan ama bayragi SET edilmis
        # bir thread SONLANMAK uzeredir; onu sahiplenmek, hicbir tuketicisi
        # olmayan bir sureci SESSIZCE birakirdi (olculen kusur).
        if _thread is not None and _thread.is_alive() and _dur is not None \
                and not _dur.is_set():
            _derinlik += 1
            return

        if _thread is not None and _thread.is_alive():
            # Onceki durdurma thread'i DURDURAMADI. Sessizce devam etmek
            # tuketicisiz bir surec demekti; bunun yerine GURULTULU olup
            # calisan bir tuketici aciyoruz. Eski thread KENDI (set) bayragiyla
            # kendi dongusunde sonlanir.
            logger.error(
                "Onceki field stok zamanlayici thread'i HALA yasiyor "
                "(durdurma zaman asimina ugramisti); TAZE bayrakla YENI bir "
                "thread aciliyor, eskisi kendi dongusunde sonlanacak"
            )

        bayrak = threading.Event()
        aday = _yeni_thread(
            target=lambda: _dongu(aralik_saniye, bayrak),
            name="field-stock-outbox-scheduler",
        )
        # Gercek bir kurulum hatasi HALA olumcul: `start()` patlarsa istisna
        # buradan kacar, sayac artmaz ve uygulama baslamaz.
        aday.start()
        _thread = aday
        _dur = bayrak
        # ARTIR, ATAMA YAPMA. `_derinlik = 1` bekleyen ic ice sahipleri
        # SESSIZCE siliyordu; saglikli durumda `_derinlik` zaten 0 oldugu
        # icin sonuc ayni, bozuk durumda ise sahipler korunur.
        _derinlik += 1
        logger.info(
            "Field stok outbox zamanlayicisi baslatildi; aralik_saniye=%s",
            aralik_saniye,
        )


def durdur_field_stok_zamanlayici() -> None:
    global _thread, _derinlik, _dur
    with _kilit:
        # Ic ice acilmis bir yasam dongusu kapaniyorsa thread DISTAKINE aittir.
        if _derinlik > 1:
            _derinlik -= 1
            return
        _derinlik = 0
        if _dur is not None:
            _dur.set()
        if _thread is not None:
            _thread.join(timeout=5)
            if _thread.is_alive():
                # DURDURAMAYAN BIR DURDURMA BUNU SOYLER. Bayragi SET
                # birakiyoruz: thread kendi dongusunde sonlanacak. `_thread`
                # ve `_dur` de birlikte duruyor, boylece sonraki `baslat`
                # bunu ANLAYIP taze bir bayrakla yeni thread acar.
                logger.error(
                    "Field stok outbox zamanlayicisi 5 saniyede DURMADI; "
                    "bayragi SET birakildi ve thread kendi dongusunde "
                    "sonlanacak. Bu surecte yeni bir baslatma olursa TAZE "
                    "bayrakla YENI bir thread acilacak"
                )
            else:
                _thread = None
                _dur = None
