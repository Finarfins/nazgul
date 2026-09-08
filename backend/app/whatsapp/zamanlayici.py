"""WhatsApp işçisinin SÜREÇ İÇİ zamanlayıcısı (WA3-full).

Kaynakta (`nazgul_website`) işçi AYRI BİR SÜREÇTİR
(`python -m app.whatsapp.worker`, kendi compose servisi). Bu depoda öyle
DEĞİL ve ayrım bilinçli: burada zaten süreç içi bir zamanlayıcı DESENİ VAR
(`app/field_stok_zamanlayici.py`, açılış koşulu 4) ve ikinci bir konteyner
açmak, bu dilimin ölçemeyeceği bir dağıtım değişikliği olurdu. Bu modül o
deseni AYNEN izler; ayrıldığı yerler aşağıda ADIYLA yazılı.

--- KAYNAĞIN `hazirlik.py`si NEDEN TAŞINMADI ----------------------------

Kaynağın işçisi her turun ilk işi olarak İKİ yüklem ölçüyordu: şema
beklenen alembic revizyonunda mı, ve app süreci `/api/ready`de 200 veriyor
mu. İkincisi AYRI SÜREÇ olmanın bedelidir — işçi app'in ayakta olduğunu
BİLEMEZ.

Burada işçi app'in KENDİ SÜRECİNDE, `lifespan` içinde başlıyor. Yani:

* app süreci ayakta ⇔ bu thread var (aynı süreç);
* `app.main` HTTP dinlemeye başlamadan ÖNCE migration'ı çalıştırıyor, yani
  thread açıldığında şema ZATEN head'dedir.

İki yüklem de yapı gereği sağlanıyor; onları bir HTTP çağrısıyla yeniden
sormak, kendi kendine `/api/ready` isteği atan bir süreç olurdu. Kaynağın
2026-08-12'de ölçtüğü yarış (işçi migration'dan önce süpürdü) BU KURULUMDA
DOĞAMAZ ve gerekçesi budur, "muhtemelen olmaz" değil.

--- VARSAYILAN KAPALI ----------------------------------------------------

`settings.whatsapp_worker_enabled` varsayılan `False`. Kapalıyken HİÇBİR
thread açılmaz — `field_stock_outbox_enabled` ile aynı sınıftan bir karar
ve aynı gerekçe: kanalın kapalı olması ÖLÇÜLEBİLİR olmalı.

--- KALP ATIŞI `settings` SATIRINA YAZILIR, GÖÇ YOK ----------------------

`field_stok_zamanlayici.KALP_ANAHTARI` ile AYNI mekanizma ve aynı
gerekçe: kalp atışı PLATFORM düzeyindedir (tek thread tüm kuyruğu gezer,
`whatsapp_inbound` zaten kiracısız bir platform tablosudur), yani
`company_id` taşıyan bir kalp atışı ANLAMSIZ olurdu; yeni bir tablo ise
GÖÇ demekti ve bu dilim göç AÇMIYOR.
"""

from __future__ import annotations

import json
import logging
import threading

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..db import SessionLocal

LOGGER_NAME = "nazgul.whatsapp.zamanlayici"
logger = logging.getLogger(LOGGER_NAME)

#: Kalıcı kalp atışının `settings.key` değeri. TEK SATIR; tarih serisi
#: TUTULMAZ (o bir ölçüm sistemi işidir ve burada tablo/göç demekti).
KALP_ANAHTARI = "whatsapp_zamanlayici.heartbeat"

#: Bayatlık çarpanı — `field_stok_zamanlayici.BAYATLIK_CARPANI` ile AYNI
#: sayı ve aynı gerekçe: 1 olsaydı normal jitter sürekli alarm üretirdi,
#: çok büyük olsaydı ölü bir thread saatlerce SAĞLIKLI görünürdü.
BAYATLIK_CARPANI = 3

#: Kalp atışı hata metninin tavanı. Metin YÜZEYE ÇIKMAZ, yalnız satırda
#: durur — ham istisna metni SQL ve satır değeri taşıyabilir.
AZAMI_HATA_METNI = 500

_kalp: dict[str, Any] | None = None
_aralik_saniye: int | None = None
_cevrim_sayisi = 0
_bu_surecte_baslatildi = False

#: AKTİF thread'in durdurma bayrağı. HER THREAD KENDİ bayrağını taşır;
#: paylaşılan tek bir Event OLAMAZ — `field_stok_zamanlayici`de ölçülen
#: kusur: `join(timeout)` zaman aşımına uğradığında SET edilmiş bayrakla
#: yaşayan thread, sonraki `baslat` tarafından "yaşıyor" diye sahipleniliyor
#: ve süreç TÜKETİCİSİZ kalıyordu.
_dur: threading.Event | None = None
_thread: threading.Thread | None = None
_derinlik = 0
_kilit = threading.RLock()


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _kisalt(hata: str) -> str:
    return hata if len(hata) <= AZAMI_HATA_METNI else hata[:AZAMI_HATA_METNI]


def _kalbi_yaz(kayit: dict[str, Any]) -> None:
    """Kalp atışını SÜREÇ İÇİNE ve `settings` satırına yazar.

    KALP ATIŞI DÖNGÜYÜ ASLA DÜŞÜRMEZ: her hatası yutulur (gürültülü).
    Tersi, gözlem aracı olması gereken şeyi ARIZA KAYNAĞINA çevirirdi.
    """
    global _kalp, _cevrim_sayisi
    with _kilit:
        _cevrim_sayisi += 1
        _kalp = dict(kayit, cycles=_cevrim_sayisi)
    try:
        govde = json.dumps(kayit, ensure_ascii=False)
        with SessionLocal() as db:
            # LEHÇESİZ UPSERT: önce UPDATE, satır yoksa INSERT. `ON
            # CONFLICT` iki lehçede de çalışır ama iki ayrı metin yazmamak
            # için karar `rowcount`a bırakıldı — `field_stok_zamanlayici`
            # ile AYNI desen.
            etkilenen = db.execute(
                text("UPDATE settings SET value=:deger WHERE key=:anahtar"),
                {"deger": govde, "anahtar": KALP_ANAHTARI},
            ).rowcount
            if etkilenen == 0:
                try:
                    db.execute(
                        text(
                            "INSERT INTO settings (key, value) "
                            "VALUES (:anahtar, :deger)"
                        ),
                        {"anahtar": KALP_ANAHTARI, "deger": govde},
                    )
                except SQLAlchemyError:
                    db.rollback()
                    db.execute(
                        text("UPDATE settings SET value=:deger WHERE key=:anahtar"),
                        {"deger": govde, "anahtar": KALP_ANAHTARI},
                    )
            db.commit()
    except Exception:  # noqa: BLE001 - gözlem aracı ARIZA KAYNAĞI OLAMAZ
        logger.exception(
            "WhatsApp zamanlayici kalp atisi YAZILAMADI; dongu etkilenmedi"
        )


def bir_dongu_calistir() -> int:
    """Tek tur: takılanları kapat, kuyruğu işle. İşlenen sayısını döner.

    KALP ATIŞI HER TURDA YAZILIR — DÜŞEN TURDA DA. Yalnız başarılı turda
    yazsaydı, sürekli patlayan bir zamanlayıcı kalp atışı bakımından hiç
    koşmamış gibi görünürdü; oysa aradaki fark tam da anlatmak istediğimiz
    şey: thread YAŞIYOR ama İŞ YAPAMIYOR.
    """
    basladi = _simdi()
    try:
        with SessionLocal() as db:
            from . import service

            olen = service.takilanlari_kapat(db)
            islenen = service.bekleyenleri_isle(db, oturum_fabrikasi=SessionLocal)
    except BaseException as hata:  # noqa: BLE001 - yazılır ve YENİDEN atılır
        _kalbi_yaz(
            {
                "started_at": basladi.isoformat(),
                "finished_at": _simdi().isoformat(),
                "messages_processed": 0,
                "dead_marked": 0,
                "last_error": _kisalt(f"{type(hata).__name__}: {hata}"),
            }
        )
        raise

    _kalbi_yaz(
        {
            "started_at": basladi.isoformat(),
            "finished_at": _simdi().isoformat(),
            "messages_processed": int(islenen),
            "dead_marked": int(olen),
            "last_error": None,
        }
    )
    if islenen or olen:
        logger.info(
            "WhatsApp isci turu tamamlandi; islenen=%s olu=%s", islenen, olen
        )
    return int(islenen)


def _dongu(aralik_saniye: int, dur: threading.Event) -> None:
    # Bayrak PARAMETRE ile gelir: bu thread KENDİ bayrağına bakar, modüldeki
    # güncel bayrağa DEĞİL.
    while not dur.is_set():
        try:
            bir_dongu_calistir()
        except Exception:
            logger.exception("WhatsApp isci turu basarisiz")
        dur.wait(aralik_saniye)


def baslat_whatsapp_zamanlayici(aralik_saniye: int) -> None:
    """Süreç içi zamanlayıcıyı başlatır. Thread hatası BİLEREK dışarı kaçar."""
    global _thread, _derinlik, _dur, _aralik_saniye, _bu_surecte_baslatildi
    with _kilit:
        # İÇ İÇE YAŞAM DÖNGÜSÜ: bir `TestClient` başka bir `TestClient`
        # içinde açıldığında aynı süreçte lifespan İKİNCİ kez koşar. İkinci
        # bir thread İSTENMEZ (iki thread aynı kuyruğu yarışırdı); sayaç
        # artar, en dıştaki kapanış durdurur.
        if _thread is not None and _thread.is_alive() and _dur is not None \
                and not _dur.is_set():
            _derinlik += 1
            return

        if _thread is not None and _thread.is_alive():
            logger.error(
                "Onceki WhatsApp zamanlayici thread'i HALA yasiyor "
                "(durdurma zaman asimina ugramisti); TAZE bayrakla YENI bir "
                "thread aciliyor, eskisi kendi dongusunde sonlanacak"
            )

        bayrak = threading.Event()
        aday = threading.Thread(
            target=lambda: _dongu(aralik_saniye, bayrak),
            name="whatsapp-worker-scheduler",
            daemon=True,
        )
        aday.start()
        _thread = aday
        _dur = bayrak
        _bu_surecte_baslatildi = True
        _aralik_saniye = aralik_saniye
        _derinlik += 1
        logger.info(
            "WhatsApp isci zamanlayicisi baslatildi; aralik_saniye=%s",
            aralik_saniye,
        )


def durdur_whatsapp_zamanlayici() -> None:
    global _thread, _derinlik, _dur
    with _kilit:
        if _derinlik > 1:
            _derinlik -= 1
            return
        _derinlik = 0
        if _dur is not None:
            _dur.set()
        if _thread is not None:
            _thread.join(timeout=5)
            if _thread.is_alive():
                # DURDURAMAYAN BİR DURDURMA BUNU SÖYLER. Bayrak SET
                # bırakılıyor; sonraki `baslat` bunu anlayıp TAZE bayrakla
                # yeni bir thread açar.
                logger.error(
                    "WhatsApp isci zamanlayicisi 5 saniyede DURMADI; bayragi "
                    "SET birakildi ve thread kendi dongusunde sonlanacak"
                )
            else:
                _thread = None
                _dur = None


def _kalici_kalp(db) -> dict[str, Any] | None:
    """`settings` satırını okur. Bozuk/eksik satır `None`dur, HATA DEĞİL."""
    try:
        ham = db.execute(
            text("SELECT value FROM settings WHERE key=:anahtar"),
            {"anahtar": KALP_ANAHTARI},
        ).scalar()
    except SQLAlchemyError:
        logger.exception("WhatsApp zamanlayici kalp atisi OKUNAMADI")
        return None
    if not ham:
        return None
    try:
        kayit = json.loads(ham)
    except ValueError:
        logger.error("WhatsApp zamanlayici kalp atisi BOZUK JSON; yok sayildi")
        return None
    return kayit if isinstance(kayit, dict) else None


def canlilik(db) -> dict[str, Any]:
    """Zamanlayıcının CANLILIK/GECİKME sinyali. PLATFORM düzeyinde.

    `alive` ile `stale` AYRI SORULARDIR ve biri ötekinin yerine geçmez:
    taze başlamış bir süreçte ilk tur bitene kadar `alive=True,
    stale=True` görülür — thread ORADADIR ama tamamlanmış bir tur KANITI
    henüz yoktur.
    """
    from ..field_stok_zamanlayici import yas_saniye

    acik = bool(settings.whatsapp_worker_enabled)
    with _kilit:
        kalp = dict(_kalp) if _kalp is not None else None
        thread = _thread
        dur = _dur
        aralik = _aralik_saniye
        baslatildi = _bu_surecte_baslatildi
    if kalp is None:
        kalp = _kalici_kalp(db)

    aralik_saniye = int(
        aralik if aralik is not None
        else settings.whatsapp_worker_interval_seconds
    )
    simdi = _simdi()
    bitti = kalp.get("finished_at") if kalp else None
    gecen = yas_saniye(bitti, simdi)
    bayat = gecen is None or gecen > BAYATLIK_CARPANI * aralik_saniye

    if thread is not None:
        canli = thread.is_alive() and dur is not None and not dur.is_set()
    elif baslatildi:
        canli = False
    else:
        canli = acik and not bayat

    return {
        "enabled": acik,
        "alive": bool(canli),
        "last_cycle_started_at": (kalp or {}).get("started_at"),
        "last_cycle_finished_at": bitti,
        "seconds_since_last_cycle": gecen,
        "interval_seconds": aralik_saniye,
        "stale": bool(bayat),
    }


__all__ = [
    "BAYATLIK_CARPANI",
    "KALP_ANAHTARI",
    "baslat_whatsapp_zamanlayici",
    "bir_dongu_calistir",
    "canlilik",
    "durdur_whatsapp_zamanlayici",
]
