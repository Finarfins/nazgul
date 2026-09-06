"""Application-process scheduler for the measured field stock consumer.

--- ACILIS KOSULU 4: CANLILIK/GECIKME SINYALI --------------------------------

`FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md` dorduncu kosulu sunu soyluyordu:
"Zamanlayici thread'i olurse ya da kuyruk birikirse bunu soyleyen bir
metrik/alarm yok; tek iz surec gunlugudur." Okuma yuzeyi (kosul 2) bu bosluga
DOKUNMAZ ve belge bunu adiyla yaziyor: `summary` kuyrugun BOYUNU ve YASINI
gosterir ama tuketicinin KOSUP KOSMADIGINI gostermez — OLU BIR THREAD ile BOS
BIR KUYRUK o ekranda AYNI gorunur.

Bu modul o ayrimi iki AYRI ISARETLE yapar ve ikisi BIRBIRININ YEDEGI DEGILDIR:

* **`alive` — SIMDI.** `threading.Thread.is_alive()`. Yalnizca thread'i
  TASIYAN SURECTE anlamlidir ve hicbir seyi hayatta kalmaz: surec yeniden
  baslarsa bu bilgi sifirlanir. Buna karsilik SAHTELENEMEZ — bir kayit degil,
  isletim sisteminin thread nesnesine dogrudan soru.
* **`stale` — SON DONGUDEN BU YANA GECEN SURE.** Kalici kalp atisi satirindan
  turer, yani SURECI ASAR. Bir surec olup yerine yenisi gelmediyse `alive`
  sorulacak bir thread yoktur; kalan tek delil BU satirdir.

--- GOC YOK: KALP ATISI `settings` SATIRINA YAZILIR ------------------------

OLCULDU, VARSAYILMADI. Depoda BIR isci kalp atisi tablosu ARANDI ve YOKTUR:
`heartbeat` gecen tek sema yeri `platform_maintenance.heartbeat_at`tir (goc
`20260728_0034`) ve o sutun BAKIM ISLEMININ (`operation_id`/`operation_kind`)
kalp atisidir — bir zamanlayici oraya yazsaydi bakim kilidinin sahipligi
hakkinda YALAN soylerdi. Yeni bir tablo ise GOC demekti ve bu dilim goc
ACMIYOR.

Kullanilan sey ZATEN VAR OLAN `settings` anahtar/deger tablosudur
(`app/core_schema.py`; semaya `20260712_0000` tabaniyla giriyor, yani hem
SQLite hem PostgreSQL kurulumlarinda VAR). O tablonun kaynaktaki notu
"Platform-global markers only. Never store company-specific data here" diyor
ve KALP ATISI TAM OLARAK BUDUR: zamanlayici SUREC DUZEYINDE kosar, tek bir
thread TUM firmalari gezer, yani `company_id` tasiyan bir kalp atisi ANLAMSIZ
olurdu — kiracisi olmayan bir olguya kiraci uydurmak olurdu.

BUNUN BEDELI ACIKCA YAZILIYOR: kalp atisi PLATFORM DUZEYINDEDIR. `summary`
ucunda donen `scheduler` blogu her kiraci icin AYNIDIR; kiraca OZEL olan tek
alan `pending_oldest_age_seconds`tir ve o alan olay tablosundan
`company_id=:cid` ile hesaplanir.

--- HATA METNI KAYITTA VAR, YUZEYDE YOK -----------------------------------

Kalp atisi `last_error` tasir ama `summary` ucu onu DONDURMEZ. Gerekce
deponun kendi kararidir (`routers/entegrasyon_olaylari._gerekceyi_arindir`):
ham istisna metni ADLI DEGERINI veritabaninda korur, HTTP uzerinden
`farm.view` tasiyan salt-okur rollere ACILMAZ. Bir dongu istisnasinin metni
SQL, kisit adi ve satir degeri tasiyabilir; onu canlilik ucundan sizdirmak,
kosul 2'de kapatilan sizinti sinifini kosul 4'te yeniden acmak olurdu.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import SessionLocal
from .field_stok_tuketici import (
    OLCUM_FIRMA_ISLENEN,
    OLCUM_FIRMA_SAYISI,
    tum_firmalari_isle,
)


LOGGER_NAME = "yerel_hesap.field_stok_zamanlayici"
logger = logging.getLogger(LOGGER_NAME)

#: Kalici kalp atisinin `settings.key` degeri. TEK SATIR: zamanlayici surec
#: duzeyinde tektir, tarih serisi TUTULMAZ (o bir olcum sistemi isidir ve
#: burada bir tablo/goc demek olurdu).
KALP_ANAHTARI = "field_stok_zamanlayici.heartbeat"

#: BAYATLIK CARPANI. Bir dongu `aralik_saniye`de bir kosar; ucunu birden
#: kacirmis bir zamanlayici artik "biraz gecikti" degildir. Carpan 1 olsaydi
#: normal jitter surekli alarm uretirdi, cok buyuk olsaydi olu bir thread
#: saatlerce SAGLIKLI gorunurdu.
BAYATLIK_CARPANI = 3

#: Kalp atisi hata metninin tavani; `field_stok_tuketici.AZAMI_HATA_METNI` ile
#: AYNI sinir. Metin YUZEYE CIKMAZ (bkz. modul basligi), yalniz satirda durur.
AZAMI_HATA_METNI = 500

#: SUREC ICI kalp atisi. `settings` satirinin AYNISINI tasir, arti bu surecte
#: kac dongu kostugunu. Surec yeniden baslayinca SIFIRLANIR ve bu bir kusur
#: degil TANIMDIR: bu kap "SU ANKI surec ne yapiyor" sorusunu cevaplar.
_kalp: dict[str, Any] | None = None
#: Kosan thread'in aralik degeri. `enabled` bayragindan BAGIMSIZ tutuluyor:
#: bayrak sonradan degistirilse bile bayatlik esigi KOSAN thread'in kadansina
#: gore hesaplanmali.
_aralik_saniye: int | None = None
#: Bu SURECTE tamamlanan (dusen dahil) dongu sayisi.
_cevrim_sayisi = 0
#: Bu SURECTE bir zamanlayici HIC baslatildi mi. `_thread`den AYRI tutuluyor:
#: temiz bir durdurma `_thread`i None yapar ve o noktada surecte KOSAN bir
#: tuketici YOKTUR — ama son kalp atisi hala TAZEDIR. Bu bayrak olmasaydi
#: durdurulmus bir zamanlayici, kalp atisi bayatlayana kadar (uc dongu boyu)
#: CANLI bildirilirdi.
_bu_surecte_baslatildi = False

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


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _kisalt(hata: str) -> str:
    return hata if len(hata) <= AZAMI_HATA_METNI else hata[:AZAMI_HATA_METNI]


def _kalbi_kur(
    basladi: datetime, bitti: datetime, olcum: dict[str, int],
    sonuc: dict[str, int] | None, hata: str | None,
) -> dict[str, Any]:
    """Bir dongunun kalp atisi kaydini kurar. YAZMAZ, yalniz kurar."""
    return {
        "started_at": basladi.isoformat(),
        "finished_at": bitti.isoformat(),
        # DUSEN bir dongude bu iki sayi olcum kabinda KALDIGI KADARIYLA durur:
        # istisna dongunun ortasinda kacmis olabilir ve o zaman `firma_islenen`
        # gercekten girilen firma sayisidir.
        "companies_processed": int(olcum.get(OLCUM_FIRMA_ISLENEN, 0)),
        "companies_total": int(olcum.get(OLCUM_FIRMA_SAYISI, 0)),
        "events_processed": int((sonuc or {}).get("girdi", 0)),
        # METIN YUZEYE CIKMAZ; bkz. modul basligi.
        "last_error": hata,
    }


def _kalbi_yaz(kayit: dict[str, Any]) -> None:
    """Kalp atisini SUREC ICINE ve `settings` satirina yazar.

    KALP ATISI DONGUYU ASLA DUSURMEZ. Bu fonksiyonun her hatasi yutulur
    (gurultulu: `logger.exception`). Tersi, gozlem araci olmasi gereken bir
    seyi ARIZA KAYNAGINA cevirirdi: kalp atisi yazilamadigi icin islenmemis
    bir outbox olayi, kapatmaya calistigi bosluktan DAHA KOTU olurdu.

    KENDI OTURUMU. Tuketicinin oturumu ile PAYLASILMAZ: o oturum bir firma
    dustugunde KULLANILAMAZ durumda olabilir ve ayni islemde yazmak, kalp
    atisini tam da onu en cok istedigimiz kosumda kaybettirirdi.
    """
    global _kalp, _cevrim_sayisi
    with _kilit:
        _cevrim_sayisi += 1
        _kalp = dict(kayit, cycles=_cevrim_sayisi)
    try:
        govde = json.dumps(kayit, ensure_ascii=False)
        with SessionLocal() as db:
            # UPSERT LEHCESIZ YAZILIYOR. `ON CONFLICT` PostgreSQL ve modern
            # SQLite'ta calisir ama iki lehcede de AYNI metni kosturmak
            # zorunda kalmamak icin karar rowcount'a birakildi: once UPDATE,
            # satir yoksa INSERT. INSERT yarisini KAYBEDEN surec (ayni anda
            # ikinci bir uygulama kopyasi acilirsa) IntegrityError alir ve
            # UPDATE'e DUSER — kayip bir kalp atisi degil, bir tur gecikme.
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
    except Exception:  # noqa: BLE001 - gozlem araci ariza kaynagi OLAMAZ
        logger.exception(
            "Field stok zamanlayici kalp atisi YAZILAMADI; dongu etkilenmedi"
        )


def bir_dongu_calistir() -> dict[str, int]:
    """Run one all-company cycle and log every outcome, including no work.

    KALP ATISI HER DONGUDE YAZILIR — DUSEN DONGUDE DE. Yalnizca basarili
    dongude yazsaydi, surekli patlayan bir zamanlayici KALP ATISI BAKIMINDAN
    hic kosmamis gibi gorunurdu; oysa aradaki fark tam da anlatmak istedigimiz
    sey: thread YASIYOR ama IS YAPAMIYOR.
    """
    basladi = _simdi()
    olcum: dict[str, int] = {}
    try:
        with SessionLocal() as db:
            sonuc = tum_firmalari_isle(db, olcum=olcum)
    except BaseException as hata:  # noqa: BLE001 - yazilir ve YENIDEN atilir
        _kalbi_yaz(
            _kalbi_kur(
                basladi, _simdi(), olcum, None,
                _kisalt(f"{type(hata).__name__}: {hata}"),
            )
        )
        raise

    kovalar = " ".join(f"{anahtar}={deger}" for anahtar, deger in sonuc.items())
    if sonuc.get("girdi", 0) == 0:
        logger.info("Field stok outbox dongusu calisti; olay bulunmadi; %s", kovalar)
    else:
        logger.info("Field stok outbox dongusu tamamlandi; %s", kovalar)
    _kalbi_yaz(_kalbi_kur(basladi, _simdi(), olcum, sonuc, None))
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
    global _thread, _derinlik, _dur, _aralik_saniye, _bu_surecte_baslatildi
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
        _bu_surecte_baslatildi = True
        # BAYATLIK ESIGI KOSAN THREAD'IN KADANSINDAN turer, ayarin O ANKI
        # degerinden degil: ayar sonradan degistirilse bile bu thread eski
        # aralikla kosmaya devam eder ve esik ona gore hesaplanmalidir.
        _aralik_saniye = aralik_saniye
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


# --------------------------------------------------------------------------
# ACILIS KOSULU 4 — OKUNAN YUZ
# --------------------------------------------------------------------------


def _zamani_coz(deger: Any) -> datetime | None:
    """ISO metnini (ya da hazir `datetime`i) UTC'ye cevirir.

    NEDEN IKI TIP. Kalp atisi satirinda deger HER ZAMAN metindir (JSON), ama
    ayni cozucu olay tablosundan HAM `text()` ile okunan `created_at` icin de
    kullanilir ve ORASI LEHCEYE GORE DEGISIR: sqlite3 surucusu ham sorguda
    METIN dondurur, psycopg ise gercek `datetime`. Bu fark VARSAYILMADI,
    PG ikizinde OLCULUYOR.

    NAIVE DAMGA UTC SAYILIR. SQLite saat dilimi SAKLAMAZ; uygulama her damgayi
    `datetime.now(timezone.utc)` ile yazar, yani dilimi olmayan bir damga
    UTC'dir. Bunu varsaymamak, SQLite kulvarinda her yas hesabini
    `TypeError` ile dusurur.
    """
    if deger is None:
        return None
    if isinstance(deger, datetime):
        an = deger
    else:
        try:
            an = datetime.fromisoformat(str(deger).replace("Z", "+00:00"))
        except ValueError:
            return None
    if an.tzinfo is None:
        return an.replace(tzinfo=timezone.utc)
    return an.astimezone(timezone.utc)


# DONUS TIPI `Any` YAZILI, `float | None` DEGIL. Bu bir belirsizlik degil bir
# KAPININ sonucudur: `test_v2_9_decimal_contract` `app/` altinda `float`
# ADININ HIC gecmesini yasaklar ve istisna kabul etmez. Yasak PARASAL ikili
# kayan nokta icindir; buradaki deger para DEGIL bir SURE olcusudur ve
# `timedelta.total_seconds()`ten gelir — ama kapinin mutlak olmasi bilincli
# bir karardir (istisna listesi, paranin da sizabilecegi ilk delik olurdu) ve
# burada ona UYULUYOR.
def yas_saniye(deger: Any, simdi: datetime | None = None) -> Any:
    """Bir damganin YASI (saniye). Gelecekteki damga NEGATIF degil 0 doner."""
    an = _zamani_coz(deger)
    if an is None:
        return None
    fark = ((simdi or _simdi()) - an).total_seconds()
    return fark if fark > 0 else 0.0


def _kalici_kalp(db) -> dict[str, Any] | None:
    """`settings` satirini okur. Bozuk/eksik satir `None`dur, HATA DEGIL."""
    try:
        ham = db.execute(
            text("SELECT value FROM settings WHERE key=:anahtar"),
            {"anahtar": KALP_ANAHTARI},
        ).scalar()
    except SQLAlchemyError:
        logger.exception("Field stok zamanlayici kalp atisi OKUNAMADI")
        return None
    if not ham:
        return None
    try:
        kayit = json.loads(ham)
    except ValueError:
        logger.error("Field stok zamanlayici kalp atisi BOZUK JSON; yok sayildi")
        return None
    return kayit if isinstance(kayit, dict) else None


def canlilik(db) -> dict[str, Any]:
    """Zamanlayicinin CANLILIK/GECIKME sinyali (acilis kosulu 4).

    PLATFORM DUZEYINDE. Donen alanlarin HICBIRI kiraca bagli degildir ve
    olmamalidir: zamanlayici tek bir surec-ici thread'dir ve TUM firmalari tek
    dongude gezer. Kiraca ozel gecikme olcusu (`pending_oldest_age_seconds`)
    bu sozlukte DEGIL, uctaki olay sorgusunda hesaplanir.

    `alive` ile `stale` AYRI SORULARDIR ve biri digerinin yerine gecmez:
    taze baslamis bir surecte ilk dongu bitene kadar `alive=True, stale=True`
    gorulur — thread ORADADIR ama tamamlanmis bir dongu KANITI henuz yoktur.
    """
    # BAYRAK ADIYLA OKUNUYOR. `field_stock_outbox_enabled` disinda hicbir sey
    # bu alani belirleyemez: aralik ayarindan ya da thread'in varligindan
    # turetmek, bayrak KAPALIYKEN bile "acik" diyebilirdi.
    acik = bool(settings.field_stock_outbox_enabled)
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
        else settings.field_stock_outbox_interval_seconds
    )
    simdi = _simdi()
    bitti = kalp.get("finished_at") if kalp else None
    gecen = yas_saniye(bitti, simdi)
    # ESIK KATI BUYUKTUR: tam 3x hala TAZEDIR. Kalp atisi YOKSA bayattir —
    # "hic dongu bitmemis" ile "cok once bitmis" ayni sonuca varir: kanit yok.
    bayat = gecen is None or gecen > BAYATLIK_CARPANI * aralik_saniye

    if thread is not None:
        # SUREC ICI KANIT. Bayragi SET edilmis bir thread SONLANMAK uzeredir
        # ve CANLI sayilmaz; `baslat`in sahiplenme kosulu da ayni ikiliye
        # bakiyor.
        canli = thread.is_alive() and dur is not None and not dur.is_set()
    elif baslatildi:
        # Bu surecte baslatildi ve DURDURULDU: kalp atisi hala taze olabilir
        # ama kosan bir tuketici YOK.
        canli = False
    else:
        # BU SURECTE THREAD YOK. Tek delil kalici kalp atisidir; bayat degilse
        # BASKA bir surecte bir tuketici kosuyor demektir.
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
