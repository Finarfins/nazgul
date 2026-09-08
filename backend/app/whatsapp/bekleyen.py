"""WhatsApp bekleyen işlem altyapısı: taslak → açık ONAY → TEK uygulama (WA4).

Bir sohbet mesajı ASLA doğrudan para kaydetmez. Niyet çözülür
(:func:`app.whatsapp.niyet.tahsilat_coz`), TASLAK açılır ve kullanıcıya özet
gösterilir, yalnız açık ``ONAY`` üzerine ödeme yazılır. Şema göç
``20260910_0080``dedir ve gerekçelerin tamamı o göçün başlığındadır.

BU MODÜL SAF FONKSİYON YÜZEYİDİR — ZAMANLAYICI BAĞLAMAZ. Burada ne bir
rota, ne bir worker döngüsü, ne bir ``asyncio`` görevi vardır;
:func:`suresi_gecenleri_kapat` bir süpürücüdür ama onu ÇAĞIRAN yoktur.
İşçiyi WA3 kuruyor ve bu modülün fonksiyonlarını oradan çağıracak.

GÜVENLİK SÖZLEŞMESİ
-------------------
* Yetki alanları (``company_id``, ``user_id``, ``phone``,
  ``whatsapp_link_id``) her zaman SUNUCUDA çözülmüş bir kimlikten yazılır;
  payload'daki hiçbir yetki iddiası okunmaz.
* Onay/iptal/claim çağrıları satırın kapsamını (firma+kullanıcı+numara)
  çağıranın TAZE çözülmüş kimliğiyle karşılaştırır ve bu karşılaştırma
  CAS'İN İÇİNDEDİR — uymuyorsa işlem YOK sayılır (fail-closed). Başka
  kullanıcının taslağı bu modüldeki hiçbir çağrıyla kımıldatılamaz.
* Bir taslak tam olarak BİR kez uygulanabilir. Üç ayrı koruma bunu birlikte
  sağlar ve ÜÇÜ DE GEREKLİDİR:

  1. ``PENDING→APPLYING`` geçişi tek UPDATE'lik CAS'tir — yarışan yirmi
     ``ONAY``dan yalnız biri kazanır (``rowcount == 1``).
  2. Ödeme, satırla birlikte doğan SABİT ``islem_anahtari`` ile yazılır;
     ödeme defteri (``payment_idempotency``) aynı anahtarın ikinci
     yazmasını YENİ ÖDEME ÜRETMEDEN geri oynatır.
  3. Tamamlama yalnız ``claim_token`` sahibine açıktır.

  1 olmasaydı yirmi işçi ödeme yoluna girerdi; 2 olmasaydı lease devralması
  ikinci bir ödeme yazardı; 3 olmasaydı devralınmış bir satırı eski sahibi
  kapatabilirdi.
* Para değerleri JSON'a METİN olarak yazılır; float yükü (JSON sayısına
  dönüşecek ikili kayan nokta) REDDEDİLİR.
* Aktivite kayıtları hassas yük taşımaz: telefonun yalnız son 4 hanesi;
  tutar, müşteri adı ve serbest metin HİÇ yazılmaz; hata alanı SINIF adıdır.

TAŞIMA NOTU
-----------
Kaynak: ``nazgul_website/backend/app/whatsapp/bekleyen.py`` (479 satır).
Yaşam döngüsü ve CAS'ler oradan geldi. İKİ YERDE BİLİNÇLİ AYRILDI:

* ``SUPERSEDED`` YOK. Kaynakta yeni taslak eskisini deterministik olarak
  değiştiriyordu; burada kısmi UNIQUE indeks ikinci taslağı REDDEDER
  (:class:`BekleyenIslemSurmekte`). Gerekçe göçün başlığında: sessizce
  değiştirilen bir taslak, kullanıcının ekranında HALA duran eski özeti
  onaylamasına ve BAŞKA bir tutarın yazılmasına yol açabilirdi.
* Kaynakta ``payment_core``a HİÇBİR çağrı yoktu (orada "PR B"ye
  bırakılmıştı). Burada uygulama BU MODÜLDEDİR ve ödemeyi bu deponun
  gerçek yolundan yazar — ölçüldü: ``POST /api/payments``
  (``app/routers/finance.py:273``) ``payment_allocation_engine.
  create_payment_with_allocation``ı çağırıyor ve idempotency anahtarını
  ``Idempotency-Key`` başlığından veriyor. Aynı fonksiyon, aynı defter.

``app/whatsapp/service.py`` ve ``app/whatsapp/niyet.py`` bu turda
DEĞİŞTİRİLMEDİ; buradan yalnız OKUNURLAR.
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from numbers import Real
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..config import settings
from ..payment_allocation_engine import create_payment_with_allocation
from . import schema
from .niyet import TahsilatNiyeti, tahsilat_basarili, tahsilat_ozeti
# ALIAS YOK ve bu ZORUNLU: Core sorgu envanteri (`tests/
# test_core_query_inventory.py`) hedef tabloyu modül düzeyindeki
# `X = Table("ad", ...)` DEĞİŞKEN ADINDAN çözüyor. Kısaltılmış bir ad
# (`as wpa`) yazılsaydı bu dosyadaki SEKİZ sorgunun hedefi "çözülemedi"
# sayılır ve kapı onları
# GÜVENLİ SAYAMAZDI — kısa ad, kiracı kapsamının denetlenebilirliğine
# değmez.
from .schema import whatsapp_links, whatsapp_pending_actions
from .telefon import normalize_phone


class BekleyenIslemSurmekte(Exception):
    """Kapsamda AKTİF (PENDING/APPLYING) bir taslak var; üzerine açılamaz."""


class GecersizTaslak(Exception):
    """İşlem türü kapalı sözlükte değil ya da payload sözleşmeye aykırı."""


class TahsisMotoruKapali(Exception):
    """Tahsis motoru kapalı; WhatsApp'tan tahsilat YAZILAMAZ (fail-closed).

    ÖLÇÜLDÜ: ``POST /api/payments`` motor kapalıyken ödemeyi HAM
    ``INSERT INTO payments`` ile yazıyor ve o yolda ``payment_idempotency``
    defterine HİÇ dokunulmuyor — yani o yolun TAM OLARAK BİR KEZ garantisi
    YOKTUR.

    WA4'ün bütün tasarımı o deftere dayanır (lease devralması ikinci ödeme
    yazmasın diye). Motor kapalıyken ham yola düşseydik, işçi çökmesinden
    sonraki ikinci deneme müşteriye İKİNCİ KEZ tahsilat işlerdi. Sessizce
    zayıf yola düşmek yerine taslak ``FAILED``a kapanır ve kullanıcı web
    panelinden girmeye yönlendirilir.
    """


#: Kullanıcının yazabileceği İKİ komut. Kapalı küme ve KATLANMIŞ biçimde
#: karşılaştırılır ("onay", "ONAY", "Onay" aynı; "iptal", "İPTAL" aynı).
#: Serbest metin BURADA ÇÖZÜLMEZ: `ONAY` kelimesinin tek bir anlamı olmalı,
#: yani "tamam", "olur", "evet" KABUL EDİLMEZ. Bedeli dürüstçe: kullanıcı
#: "tamam" yazarsa hiçbir şey olmaz ve taslak süresi dolar. Karşılığı,
#: belirsiz bir kelimenin PARA YAZMAMASIDIR.
KOMUT_ONAY = "ONAY"
KOMUT_IPTAL = "IPTAL"

#: Türkçe katlama: "İPTAL" → "IPTAL". `niyet.tr_katla` ile AYNI eşleme ama
#: bu iki kelime için yeterli olan dar biçim; `niyet`i komut çözümü için
#: içe aktarmak, saf metin modülüne bir bağımlılık borcu yazardı.
_KATLAMA = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")


def komut_coz(metin: str) -> str | None:
    """Mesajı ``ONAY`` / ``IPTAL`` / ``None`` olarak sınıflar — SAF FONKSİYON.

    TEK KELİME şartı vardır: "onay" komuttur, "onaylamıyorum onay deme"
    DEĞİLDİR. Cümlenin içinde geçen bir kelimeyi komut saymak, kullanıcının
    tereddüt ettiği bir mesajı onay olarak okuyabilirdi.
    """
    sade = (metin or "").strip().translate(_KATLAMA).upper()
    # Noktalama toleransı: "ONAY." ve "ONAY!" aynı komuttur.
    sade = sade.strip(".!,;:?")
    if sade == KOMUT_ONAY:
        return KOMUT_ONAY
    if sade == KOMUT_IPTAL:
        return KOMUT_IPTAL
    return None


@dataclass(frozen=True, slots=True)
class BekleyenIslem:
    """Bir taslak satırının okunmuş hâli. ``claim_token`` BURADA YOKTUR."""

    id: int
    company_id: int
    user_id: int
    phone: str
    action_type: str
    status: str
    islem_anahtari: str
    expires_at: datetime
    payload: dict[str, Any]
    result_id: int | None


@dataclass(frozen=True, slots=True)
class TahsilatHedefi:
    """Taslağın SUNUCUDA çözülmüş hedefi — niyetin serbest metni DEĞİL.

    :class:`~app.whatsapp.niyet.TahsilatNiyeti` yalnız bir ``musteri_terimi``
    taşır (kullanıcının yazdığı kelimelerden kalan). O terimi gerçek bir
    müşteriye ve gerçek bir kasa/banka hesabına çevirmek ÇAĞIRANIN işidir ve
    KİRACI KAPSAMINDA yapılmalıdır. Bu sınıf o çözümün sonucudur; taslak
    payload'ına yalnız buradaki KİMLİKLER yazılır, terim yazılmaz.
    """

    musteri_id: int
    musteri_adi: str
    hesap_id: int | None
    hesap_adi: str
    acik_bakiye: Decimal


@dataclass(frozen=True, slots=True)
class TaslakSonucu:
    """Taslak + kullanıcıya gösterilecek özet metni."""

    islem: BekleyenIslem
    ozet: str


@dataclass(frozen=True, slots=True)
class OnaySonucu:
    """Onayın sonucu: uygulandıysa ödeme kimliği ve kullanıcı metni."""

    uygulandi: bool
    payment_id: int | None
    mesaj: str
    hata_sinifi: str | None = None


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _tz(deger: datetime | None) -> datetime | None:
    # SQLite naive saklar; karşılaştırmalar UTC'de yapılır (bağlam deseni).
    if deger is not None and deger.tzinfo is None:
        return deger.replace(tzinfo=timezone.utc)
    return deger


def _maskeli(telefon: str) -> str:
    return f"***{telefon[-4:]}" if telefon else "***"


def _yuk_denetle(dugum: Any, yol: str = "payload") -> None:
    """Para güvenliği: float (JSON'a ikili kayan nokta olarak inecek her
    sayı) reddedilir. Decimal ve tarih değerleri metne çevrilerek saklanır;
    int/bool/str/None serbesttir."""
    if isinstance(dugum, bool) or isinstance(dugum, int):
        return
    if isinstance(dugum, Real) and not isinstance(dugum, Decimal):
        raise GecersizTaslak(
            f"{yol}: ikili kayan nokta sayı saklanamaz; Decimal ya da metin kullanın"
        )
    if isinstance(dugum, Mapping):
        for anahtar, deger in dugum.items():
            _yuk_denetle(deger, f"{yol}.{anahtar}")
        return
    if isinstance(dugum, (list, tuple)):
        for sira, deger in enumerate(dugum):
            _yuk_denetle(deger, f"{yol}[{sira}]")


def _yuk_yaz(veri: Mapping[str, Any]) -> str:
    _yuk_denetle(veri)

    def _metin(deger: Any) -> str:
        if isinstance(deger, Decimal):
            return format(deger, "f")
        return str(deger)

    return json.dumps(veri, ensure_ascii=False, sort_keys=True, default=_metin)


def _satirdan(satir: Any) -> BekleyenIslem:
    try:
        yuk = json.loads(satir.payload)
    except ValueError:
        yuk = {}
    return BekleyenIslem(
        id=int(satir.id),
        company_id=int(satir.company_id),
        user_id=int(satir.user_id),
        phone=str(satir.phone),
        action_type=str(satir.action_type),
        status=str(satir.status),
        islem_anahtari=str(satir.islem_anahtari),
        expires_at=_tz(satir.expires_at),
        payload=yuk if isinstance(yuk, dict) else {},
        result_id=int(satir.result_id) if satir.result_id is not None else None,
    )


# KAPSAM YÜKLEMİ (firma + kullanıcı + numara) HER SORGUYA AÇIKÇA YAZILIR,
# bir yardımcıdan GELMEZ. Bir zamanlar `_kapsam(kimlik, telefon)` diye bir
# yardımcı vardı ve okunaklıydı; ÖLÇÜLDÜ ki kiracı kapsam nöbetçisi
# (`tests/test_core_tenant_scoping_guard.py`) çağrının ARDINI GÖREMİYOR ve
# yardımcıyı kullanan DOKUZ ifadeyi birden "bağlı company_id yüklemi yok"
# diye işaretliyordu. Nöbetçiyi bir istisnayla susturmak YANLIŞ olurdu: o
# listede her kayıt AYRI bir güvenlik kararıdır ve burada verilecek bir
# karar YOKTU — yüklem gerçekten vardı, yalnız GÖRÜNMÜYORDU.
#
# Bedeli dürüstçe: üç satır, birkaç yerde tekrar ediyor. Karşılığı, her
# sorgunun kiracı kapsamının O SORGUNUN YANINDA okunabilmesidir.


def _jeton_esit(verilen: str | None, saklanan: str | None) -> bool:
    """Claim jetonu karşılaştırmasının TEK yeri — ``hmac.compare_digest``.

    Düz ``==`` yazılsaydı karşılaştırma ilk farklı baytta dönerdi ve
    ölçülebilir zaman farkı, jetonun NE KADARININ tuttuğunu sızdırırdı.
    Jeton bir yetki taşıyıcısıdır (onu bilen APPLYING satırı kapatabilir),
    yani sır karşılaştırması kuralları BURADA DA geçerlidir —
    ``cloud_api.verify_signature`` ile AYNI gerekçe.

    Yarışın HAKEMİ bu fonksiyon DEĞİL, SQL CAS'idir (``_jetonla_kapat``):
    iki süreç aynı anda geçse bile ``rowcount`` yalnız birinde 1 olur. Bu
    denetim, CAS'ten ÖNCE duran fail-closed bir kapıdır ve hiçbir yan
    etkinin (aktivite kaydı dâhil) yanlış sahiple üretilmemesini sağlar.
    """
    if not verilen or not saklanan:
        return False
    return hmac.compare_digest(str(verilen), str(saklanan))


def _baglanti_dogrula(
    db: Session, kimlik: Any, telefon: str, whatsapp_link_id: int
) -> None:
    """Verilen link kimliğini SUNUCUDA çözülmüş kimliğe bağlar; fail-closed.

    Bileşik FK yalnız bağlantının AYNI FİRMAYA ait olduğunu kanıtlar — bu
    denetim ise AYNI KULLANICI + AYNI kanonik telefon + AKTİF olduğunu
    kanıtlar. İkisi AYNI şeyi ölçmüyor, bu yüzden ikisi de var: FK'yi
    geçen ama kullanıcısı başka olan bir link kimliği buradan geçemez.
    """
    satir = db.execute(
        select(whatsapp_links.c.id).where(
            whatsapp_links.c.id == int(whatsapp_link_id),
            whatsapp_links.c.company_id == int(kimlik.company_id),
            whatsapp_links.c.user_id == int(kimlik.user_id),
            whatsapp_links.c.phone == telefon,
            whatsapp_links.c.is_active.is_(True),
        )
    ).first()
    if not satir:
        raise GecersizTaslak(
            "WhatsApp bağlantısı kimlikle eşleşmiyor (firma/kullanıcı/telefon/aktiflik)"
        )


def aktif_taslak(db: Session, kimlik: Any, telefon: str) -> BekleyenIslem | None:
    """Kapsamdaki aktif (PENDING/APPLYING) satırı döndürür.

    Süresi geçmiş PENDING'i OKUMA ANINDA ``EXPIRED``a kapatır: süpürücü
    dakikada bir koşsa bile, kullanıcı süresi dolmuş bir taslağı onaylamayı
    denediğinde cevabın "süresi doldu" olması gerekir, "hâlâ bekliyor"
    değil.
    """
    telefon = normalize_phone(telefon)
    satir = db.execute(
        select(whatsapp_pending_actions).where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.user_id == int(kimlik.user_id),
            whatsapp_pending_actions.c.phone == telefon,
            whatsapp_pending_actions.c.status.in_(sorted(schema.BEKLEYEN_AKTIF_STATUSES)),
        )
    ).mappings().first()
    if not satir:
        return None
    islem = _satirdan(satir)
    if islem.status == schema.BEKLEYEN_PENDING and islem.expires_at <= _simdi():
        if _suresi_doldur(db, islem):
            db.commit()
        return None
    return islem


def taslak_olustur(
    db: Session,
    kimlik: Any,
    telefon: str,
    *,
    whatsapp_link_id: int,
    niyet: TahsilatNiyeti,
    hedef: TahsilatHedefi,
) -> TaslakSonucu:
    """Tahsilat taslağı açar ve kullanıcıya gösterilecek özeti döndürür.

    Kapsamda ZATEN aktif bir taslak varsa :class:`BekleyenIslemSurmekte`
    yükselir — kaynakta olduğu gibi sessizce DEĞİŞTİRİLMEZ (modül
    başlığındaki taşıma notu). Kısmi UNIQUE indeks aynı kararın veritabanı
    bekçisidir: SELECT ile INSERT arasındaki yarış ``IntegrityError``a
    düşer ve AYNI istisnaya çevrilir, yani iki eşzamanlı mesaj iki taslak
    üretemez.
    """
    telefon = normalize_phone(telefon)
    # Red, HİÇBİR yan etkiden önce: link kimliğe bağlanamıyorsa ne satır ne
    # aktivite izi oluşur.
    _baglanti_dogrula(db, kimlik, telefon, whatsapp_link_id)

    if schema.TAHSILAT not in schema.ISLEM_TURLERI:  # pragma: no cover
        raise GecersizTaslak(f"Bilinmeyen işlem türü: {schema.TAHSILAT}")

    # Payload SERBEST METİN TAŞIMAZ: `niyet.musteri_terimi` (kullanıcının
    # yazdığı kelimeler) BİLEREK yazılmaz — çözülmüş KİMLİK yazılır. Terim
    # bir kişi adı olabilir ve taslak defteri onu saklamak zorunda değildir.
    yuk = {
        "tutar": niyet.tutar,
        "yontem": niyet.yontem,
        "musteri_id": int(hedef.musteri_id),
        "hesap_id": int(hedef.hesap_id) if hedef.hesap_id is not None else None,
    }
    govde = _yuk_yaz(yuk)

    simdi = _simdi()
    try:
        yeni_id = int(db.execute(
            insert(whatsapp_pending_actions).values(
                company_id=int(kimlik.company_id),
                user_id=int(kimlik.user_id),
                whatsapp_link_id=int(whatsapp_link_id),
                phone=telefon,
                action_type=schema.TAHSILAT,
                payload=govde,
                status=schema.BEKLEYEN_PENDING,
                # Satırla birlikte doğar, satırla yaşar: ödeme
                # idempotensisinin kökü (göç 0080 başlığı).
                islem_anahtari=uuid4().hex,
                created_at=simdi,
                expires_at=simdi + timedelta(minutes=schema.PENDING_OMRU_DAKIKA),
            ).returning(whatsapp_pending_actions.c.id)
        ).scalar_one())
    except IntegrityError as hata:
        db.rollback()
        raise BekleyenIslemSurmekte() from hata

    log_activity(
        db, int(kimlik.company_id), int(kimlik.user_id),
        "whatsapp_pending.created", "whatsapp_pending", yeni_id,
        f"WhatsApp bekleyen işlem taslağı açıldı ({_maskeli(telefon)})",
        {"islem_turu": schema.TAHSILAT},
    )
    db.commit()
    # Kiracı yüklemi burada da AÇIK ve bu bir süs değil: satırı BİZ yazdık,
    # ama nöbetçinin kuralı "kiracı tablosuna dokunan HER ifade" der ve bir
    # kural ancak istisnasız uygulandığında kuraldır.
    satir = db.execute(
        select(whatsapp_pending_actions).where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.id == yeni_id,
        )
    ).mappings().one()
    return TaslakSonucu(
        islem=_satirdan(satir),
        ozet=tahsilat_ozeti(
            hedef.musteri_adi,
            niyet.tutar,
            niyet.yontem,
            hedef.hesap_adi,
            hedef.acik_bakiye,
        ),
    )


def iptal_et(db: Session, kimlik: Any, telefon: str) -> bool:
    """Kapsamdaki PENDING taslağı geri alınamaz biçimde iptal eder.

    Kapsam eşleşmesi CAS'in İÇİNDEDİR: başka kullanıcının / başka firmanın /
    başka numaranın taslağı bu çağrıyla iptal EDİLEMEZ (rowcount 0 → False).
    APPLYING iptal EDİLEMEZ — uygulanmakta olan bir ödeme yarı yolda
    kesilirse "ödeme yazıldı mı" sorusunun cevabı belirsiz kalırdı.
    """
    telefon = normalize_phone(telefon)
    hedef = db.execute(
        select(whatsapp_pending_actions.c.id).where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.user_id == int(kimlik.user_id),
            whatsapp_pending_actions.c.phone == telefon,
            whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING,
        )
    ).first()
    if not hedef:
        return False
    sonuc = db.execute(
        update(whatsapp_pending_actions)
        .where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.id == int(hedef.id),
            whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING,
        )
        .values(status=schema.BEKLEYEN_CANCELLED, resolved_at=_simdi())
    )
    if sonuc.rowcount != 1:
        db.rollback()
        return False
    log_activity(
        db, int(kimlik.company_id), int(kimlik.user_id),
        "whatsapp_pending.cancelled", "whatsapp_pending", int(hedef.id),
        f"WhatsApp bekleyen işlem iptal edildi ({_maskeli(telefon)})",
        {"islem_turu": schema.TAHSILAT},
    )
    db.commit()
    return True


def claim_et(db: Session, kimlik: Any, telefon: str, pending_id: int) -> str | None:
    """``PENDING→APPLYING`` CAS'i; kazanan ``claim_token`` alır, kaybeden ``None``.

    BU FONKSİYON YARIŞIN HAKEMİDİR. Yirmi eşzamanlı ``ONAY``dan yalnız
    birinde ``rowcount == 1`` olur; ötekiler ödeme yoluna HİÇ GİRMEZ.

    Kapsam (firma+kullanıcı+numara) ve süre koşulları CAS'in İÇİNDEDİR:

    * PENDING yalnız taslak süresi (``expires_at``) geçmemişse claim edilir —
      süresi dolmuş taslak İLK KEZ uygulanmaya başlayamaz.
    * APPLYING devralmasında ``expires_at`` şartı BİLEREK YOKTUR: kullanıcı
      ``ONAY``ı taslak geçerliyken vermiştir; işçi çöküp taslak ömrü de
      dolarsa satır sonsuza dek kilitli kalırdı (süpürücü APPLYING'e
      dokunmaz, kısmi UNIQUE yeni taslağı engeller) ve sohbet KALICI
      kilitlenirdi. Dolmuş lease (``claim_expires_at <= şimdi``) her koşulda
      devralınabilir; devralma GÜVENLİDİR çünkü ikinci uygulama AYNI
      ``islem_anahtari`` ile gider ve ödeme defteri ikinci ödeme YAZMAZ.
    """
    telefon = normalize_phone(telefon)
    simdi = _simdi()
    jeton = uuid4().hex
    sonuc = db.execute(
        update(whatsapp_pending_actions)
        .where(
            whatsapp_pending_actions.c.id == int(pending_id),
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.user_id == int(kimlik.user_id),
            whatsapp_pending_actions.c.phone == telefon,
            or_(
                and_(
                    whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING,
                    whatsapp_pending_actions.c.expires_at > simdi,
                ),
                and_(
                    whatsapp_pending_actions.c.status == schema.BEKLEYEN_APPLYING,
                    whatsapp_pending_actions.c.claim_expires_at.is_not(None),
                    whatsapp_pending_actions.c.claim_expires_at <= simdi,
                ),
            ),
        )
        .values(
            status=schema.BEKLEYEN_APPLYING,
            claim_token=jeton,
            claim_expires_at=simdi + timedelta(minutes=schema.PENDING_LEASE_DAKIKA),
        )
    )
    db.commit()
    return jeton if sonuc.rowcount == 1 else None


def _sahip_mi(
    db: Session, kimlik: Any, pending_id: int, jeton: str
) -> Any | None:
    """Satırı KİRACI KAPSAMINDA okur; jetonu ``compare_digest`` ile doğrular.

    ``kimlik`` bir süs DEĞİL: jeton TEK BAŞINA yetseydi, sızmış bir jeton
    BAŞKA firmanın satırını kapatabilirdi. Kapsam yüklemi o ihtimali
    veritabanı seviyesinde kapatıyor ve jeton İKİNCİ kapı olarak kalıyor.
    """
    satir = db.execute(
        select(
            whatsapp_pending_actions.c.company_id,
            whatsapp_pending_actions.c.user_id,
            whatsapp_pending_actions.c.phone,
            whatsapp_pending_actions.c.action_type,
            whatsapp_pending_actions.c.status,
            whatsapp_pending_actions.c.claim_token,
        ).where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.id == int(pending_id),
        )
    ).first()
    if satir is None or satir.status != schema.BEKLEYEN_APPLYING:
        return None
    if not _jeton_esit(jeton, satir.claim_token):
        return None
    return satir


def _jetonla_kapat(
    db: Session,
    kimlik: Any,
    pending_id: int,
    jeton: str,
    *,
    durum: str,
    result_id: int | None = None,
    fail_reason: str | None = None,
    resolved_at: datetime | None = None,
) -> bool:
    """Kapatma CAS'i: id + APPLYING + jeton. Yarışın SON hakemi budur.

    SÜTUN KÜMESİ AÇIK, `**degerler` DEĞİL — ve bu bir üslup tercihi değil.
    Opak sözlük yayılımı Core sorgu envanterinin (`tests/
    test_core_query_inventory.py`) UPDATE'in HANGİ sütunlara dokunduğunu
    statik olarak görmesini engelliyordu ve sorgu "desteksiz" sayılıyordu.
    Aynı düzeltme bu depoda daha önce `_finalize` için de yapıldı ("opak
    sözlük yayılımını bırakıp açık sütun kümesine geçti").

    Kazanç yalnız denetlenebilirlik değil: açık küme, kapanan her satırın
    lease alanlarını (`claim_token`, `claim_expires_at`) MUTLAKA
    temizlediğini de görünür kılıyor — göçün `ck_wpa_terminal_lease_temiz`
    CHECK'i tam olarak bunu istiyor.
    """
    sonuc = db.execute(
        update(whatsapp_pending_actions)
        .where(
            whatsapp_pending_actions.c.company_id == int(kimlik.company_id),
            whatsapp_pending_actions.c.id == int(pending_id),
            whatsapp_pending_actions.c.status == schema.BEKLEYEN_APPLYING,
            whatsapp_pending_actions.c.claim_token == jeton,
        )
        .values(
            status=durum,
            result_id=result_id,
            fail_reason=fail_reason,
            resolved_at=resolved_at,
            claim_token=None,
            claim_expires_at=None,
        )
    )
    return sonuc.rowcount == 1


def uygulandi(
    db: Session,
    kimlik: Any,
    pending_id: int,
    jeton: str,
    *,
    result_id: int,
    commit: bool = True,
) -> bool:
    """``APPLYING→APPLIED``; yalnız KAPSAM + ``claim_token`` sahibi kapatabilir."""
    satir = _sahip_mi(db, kimlik, pending_id, jeton)
    if satir is None:
        return False
    if not _jetonla_kapat(
        db, kimlik, pending_id, jeton,
        durum=schema.BEKLEYEN_APPLIED,
        result_id=int(result_id),
        resolved_at=_simdi(),
    ):
        return False
    log_activity(
        db, int(satir.company_id), int(satir.user_id),
        "whatsapp_pending.applied", "whatsapp_pending", int(pending_id),
        f"WhatsApp bekleyen işlem uygulandı ({_maskeli(str(satir.phone))})",
        {"islem_turu": str(satir.action_type), "result_id": int(result_id)},
    )
    if commit:
        db.commit()
    return True


# `fail_reason` yalnız Python istisna SINIFI adı biçiminde olabilir. Kesme
# ([:60]) YETMEZ: yanlış kullanımda hassas hata metni ("DB password=...")
# aktivite kaydına sızardı. Biçime uymayan her değer, ham hâli HİÇBİR yere
# yazılmadan sabit güvenli sınıfa çevrilir.
_SINIF_ADI = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
_GUVENLI_HATA_SINIFI = "BeklenmeyenHata"


def _hata_sinifi(sebep_sinifi: str) -> str:
    if isinstance(sebep_sinifi, str) and _SINIF_ADI.match(sebep_sinifi):
        return sebep_sinifi
    return _GUVENLI_HATA_SINIFI


def basarisiz(
    db: Session, kimlik: Any, pending_id: int, jeton: str, sebep_sinifi: str
) -> bool:
    """``APPLYING→FAILED`` (kalıcı hata); sebep yalnız doğrulanmış SINIF adı."""
    sinif = _hata_sinifi(sebep_sinifi)
    satir = _sahip_mi(db, kimlik, pending_id, jeton)
    if satir is None:
        return False
    if not _jetonla_kapat(
        db, kimlik, pending_id, jeton,
        durum=schema.BEKLEYEN_FAILED,
        fail_reason=sinif,
        resolved_at=_simdi(),
    ):
        db.rollback()
        return False
    log_activity(
        db, int(satir.company_id), int(satir.user_id),
        "whatsapp_pending.failed", "whatsapp_pending", int(pending_id),
        f"WhatsApp bekleyen işlem kalıcı hatayla kapandı ({_maskeli(str(satir.phone))})",
        {"islem_turu": str(satir.action_type), "hata_sinifi": sinif},
    )
    db.commit()
    return True


def birak(db: Session, kimlik: Any, pending_id: int, jeton: str) -> bool:
    """Geçici hata: ``APPLYING→PENDING``; taslak süresi içinde yeniden onaylanır."""
    if _sahip_mi(db, kimlik, pending_id, jeton) is None:
        return False
    # `resolved_at` BİLEREK None: satır terminale gitmiyor, PENDING'e
    # geri dönüyor ve taslak süresi içinde yeniden onaylanabilir.
    basarili = _jetonla_kapat(
        db, kimlik, pending_id, jeton, durum=schema.BEKLEYEN_PENDING
    )
    db.commit()
    return basarili


# ---------------------------------------------------------------------------
# UYGULAMA: taslak → ödeme. Bu deponun GERÇEK ödeme yolu kullanılır.
# ---------------------------------------------------------------------------

#: Ödeme idempotency anahtarının öneki. Ödeme defteri bütün yazma yollarında
#: PAYLAŞILIR (`payment_idempotency`), yani WhatsApp'tan gelen bir anahtarın
#: panelden gelen bir `Idempotency-Key` ile ÇAKIŞMAMASI gerekir. `uuid4().hex`
#: zaten çakışmaz ama önek, defterdeki bir satıra bakan kişinin anahtarın
#: NEREDEN geldiğini sorabilmesini sağlar.
IDEMPOTENCY_ONEKI = "wa:"


def idempotency_anahtari(islem_anahtari: str) -> str:
    """``payment_idempotency`` defterine gidecek anahtar — SAF FONKSİYON.

    Taslağın ``islem_anahtari``ndan TÜRER, yani taslak ömrü boyunca SABİTTİR.
    Bu, ikinci uygulamanın ikinci ödeme yazmamasının TEK sebebidir; taze bir
    UUID kullanmak lease devralmasında müşteriye İKİNCİ KEZ tahsilat
    işlerdi (göç 0080 başlığı).
    """
    return f"{IDEMPOTENCY_ONEKI}{islem_anahtari}"


def _tahsilat_yaz(
    db: Session, kimlik: Any, islem: BekleyenIslem
) -> dict[str, Any]:
    """Ödemeyi bu deponun GERÇEK yolundan yazar.

    ÖLÇÜLDÜ: ``POST /api/payments`` (``app/routers/finance.py:273``) allocation
    motoru açıkken ``create_payment_with_allocation``ı çağırıyor ve
    idempotency anahtarını ``Idempotency-Key`` başlığından veriyor. Burada
    AYNI fonksiyon, AYNI defter (``payment_idempotency``) ve AYNI anahtar
    sözleşmesi kullanılır — tek fark anahtarın kaynağıdır: bir HTTP başlığı
    yerine taslağın kendi ``islem_anahtari``.

    Ayrı bir yazma yolu açmak (ham ``INSERT INTO payments``) mümkündü ve
    YANLIŞ olurdu: tahsis (``allocate_payment``), cari senkronu
    (``sync_payment_finance``) ve idempotency defteri o yolda YENİDEN
    yazılmak zorunda kalırdı ve iki yol zamanla ayrışırdı.
    """
    if not getattr(settings, "payment_allocation_engine_enabled", False):
        raise TahsisMotoruKapali()

    yuk = islem.payload
    degerler: dict[str, Any] = {
        "entity_type": "customer",
        "entity_id": int(yuk["musteri_id"]),
        # Payload'da METİN olarak duruyor (float yasağı); motor `money()` ile
        # Decimal'e çevirir.
        "amount": str(yuk["tutar"]),
        "payment_date": _simdi().date().isoformat(),
        "note": None,
        "payment_method": str(yuk["yontem"]),
        "account_id": int(yuk["hesap_id"]) if yuk.get("hesap_id") is not None else None,
        # Belge tahsisi WhatsApp'tan YAPILMAZ: sohbette hangi faturaya
        # yazılacağını soracak bir arayüz yok. Tahsis motoru ödemeyi açık
        # bakiyeye FIFO uygular (`allocate_payment`).
        "reference_type": None,
        "reference_id": None,
    }
    return create_payment_with_allocation(
        db,
        int(kimlik.company_id),
        degerler,
        idempotency_anahtari(islem.islem_anahtari),
        created_by=int(kimlik.user_id),
    )


def onayla(
    db: Session,
    kimlik: Any,
    telefon: str,
    metin: str,
    *,
    yeni_bakiye_coz: Any = None,
) -> OnaySonucu | None:
    """``ONAY`` mesajını uçtan uca işler: CAS → ödeme → ``APPLIED``.

    ``metin`` bir onay komutu DEĞİLSE ``None`` döner ve HİÇBİR ŞEY YAPMAZ —
    fail-closed. Kapsamda onaylanacak taslak yoksa da ``None`` döner.

    SIRA VE COMMIT SINIRLARI (üçü ayrı, ve bu ZORUNLU):

      1. ``claim_et``   — APPLYING commit edilir. Yarış BURADA biter.
      2. ödeme          — ``create_payment_with_allocation`` KENDİ commit'ini
         atar (motorun sözleşmesi; değiştirilmedi).
      3. ``uygulandi``  — APPLIED commit edilir.

    2 ile 3 arasında süreç ölürse satır APPLYING kalır, lease dolar,
    devralınır ve ödeme AYNI anahtarla YENİDEN denenir: defter saklanmış
    sonucu geri oynatır (ikinci ödeme YOK) ve satır APPLIED'a kapanır.
    Pencere GÖRÜNÜR ve KAPALIDIR.
    """
    if komut_coz(metin) != KOMUT_ONAY:
        return None
    telefon = normalize_phone(telefon)
    islem = aktif_taslak(db, kimlik, telefon)
    if islem is None or islem.status != schema.BEKLEYEN_PENDING:
        return None

    jeton = claim_et(db, kimlik, telefon, islem.id)
    if jeton is None:
        # Yarışı kaybettik ya da satır bu arada kapandı. SESSİZ DÖNÜŞ:
        # kazanan zaten kullanıcıya cevap yazacak, ikinci bir "işleminiz
        # uygulanıyor" mesajı aynı onay için iki cevap üretirdi.
        return None

    try:
        sonuc = _tahsilat_yaz(db, kimlik, islem)
    # Sınıf adı dışında hiçbir şey saklanmaz (aşağıda `_hata_sinifi`).
    # HER HATA KALICI SAYILIR (`FAILED`), GEÇİCİ DEĞİL — ve bu bilinçli bir
    # DARLIKTIR, bir gözden kaçma değil. `birak` (APPLYING→PENDING) bu
    # modülde VAR ama buradan ÇAĞRILMIYOR: hangi istisnanın yeniden
    # denemeye değer olduğunu söyleyecek bir sınıflandırma bugün YOK ve
    # tahmin etmek, kullanıcıyı "kaydedilemedi" dedikten sonra sessizce
    # kaydedilen bir tahsilata bırakabilirdi. Kullanıcı taslağı yeniden
    # açabilir; ödeme defteri ikinci bir ödeme yazmayacağı için bu güvenli.
    # `birak`ı çağıracak olan, hatayı sınıflandırabilen işçidir (WA3).
    except Exception as hata:  # noqa: BLE001
        db.rollback()
        sinif = type(hata).__name__
        basarisiz(db, kimlik, islem.id, jeton, sinif)
        return OnaySonucu(
            uygulandi=False,
            payment_id=None,
            # Kullanıcıya İÇ HATA METNİ GİTMEZ: `str(hata)` bir SQL parçası
            # ya da bir sunucu adı taşıyabilir.
            mesaj="Tahsilat kaydedilemedi. Lütfen web panelinden deneyin.",
            hata_sinifi=_hata_sinifi(sinif),
        )

    payment_id = int(sonuc["id"])
    if not uygulandi(db, kimlik, islem.id, jeton, result_id=payment_id):
        # Lease devralınmış ve satır BAŞKASI tarafından kapatılmış olabilir.
        # Ödeme yine de TEKTİR (idempotency defteri) — kaybedilen tek şey bu
        # sürecin damgasıdır, para değil.
        return None

    yeni_bakiye = Decimal("0")
    if yeni_bakiye_coz is not None:
        yeni_bakiye = yeni_bakiye_coz(db, int(kimlik.company_id), islem.payload)
    return OnaySonucu(
        uygulandi=True,
        payment_id=payment_id,
        mesaj=tahsilat_basarili(payment_id, yeni_bakiye),
    )


def _suresi_doldur(db: Session, islem: BekleyenIslem) -> bool:
    sonuc = db.execute(
        update(whatsapp_pending_actions)
        .where(
            # Kiracı yüklemi satırın KENDİ okunmuş firmasından gelir:
            # süpürücü küresel KOŞAR ama YAZMA her zaman tek firmaya iner.
            whatsapp_pending_actions.c.company_id == islem.company_id,
            whatsapp_pending_actions.c.id == islem.id,
            whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING,
        )
        .values(status=schema.BEKLEYEN_EXPIRED, resolved_at=_simdi())
    )
    if sonuc.rowcount != 1:
        return False
    log_activity(
        db, islem.company_id, islem.user_id,
        "whatsapp_pending.expired", "whatsapp_pending", islem.id,
        f"WhatsApp bekleyen işlem süresi doldu ({_maskeli(islem.phone)})",
        {"islem_turu": islem.action_type},
    )
    return True


def suresi_gecenleri_kapat(db: Session) -> int:
    """Süresi geçmiş PENDING satırları ``EXPIRED``a çeker (süpürücü).

    ZAMANLAYICIYA BAĞLI DEĞİLDİR: bu turda bu fonksiyonu ÇAĞIRAN yoktur.
    İşçi WA3'ün işidir ve onu oradan çağıracak.

    ``APPLYING``e DOKUNMAZ: lease'i dolan APPLYING, :func:`claim_et` ile
    devralınır — "süresi doldu" diye kapatmak, ödemesi yazılmış ama damgası
    yazılamamış bir işlemi düşürür ve satır bir daha ASLA APPLIED olmazdı.

    KİRACI YÜKLEMİ YOKTUR ve bu bilinçli: süpürücü KÜRESEL koşar, bir işçi
    bütün firmaların süresi geçmiş taslaklarını kapatır. Kapı:
    `tests/test_wa4_bekleyen.py::test_SUPURUCU_KURESEL_kosar`.
    """
    simdi = _simdi()
    adaylar = db.execute(
        select(whatsapp_pending_actions).where(
            whatsapp_pending_actions.c.status == schema.BEKLEYEN_PENDING,
            whatsapp_pending_actions.c.expires_at <= simdi,
        )
    ).mappings().all()
    kapanan = 0
    for satir in adaylar:
        if _suresi_doldur(db, _satirdan(satir)):
            kapanan += 1
    if kapanan:
        db.commit()
    return kapanan


__all__ = [
    "IDEMPOTENCY_ONEKI",
    "KOMUT_IPTAL",
    "KOMUT_ONAY",
    "BekleyenIslem",
    "BekleyenIslemSurmekte",
    "GecersizTaslak",
    "TahsisMotoruKapali",
    "OnaySonucu",
    "TahsilatHedefi",
    "TaslakSonucu",
    "aktif_taslak",
    "basarisiz",
    "birak",
    "claim_et",
    "idempotency_anahtari",
    "iptal_et",
    "komut_coz",
    "onayla",
    "suresi_gecenleri_kapat",
    "taslak_olustur",
    "uygulandi",
]
