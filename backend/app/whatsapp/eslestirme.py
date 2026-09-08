"""WhatsApp numarasını ERP kimliğine bağlayan defter (WA2).

Kaynak `nazgul_website/backend/app/whatsapp/eslestirme.py` (590 satır) ve
`service.py::kimlik_coz`. Kod üretme/tüketme gövdesi büyük ölçüde BİREBİR
taşındı; AYRILAN İKİ YER aşağıda ADIYLA yazılı ve ikisi de ölçülmüş bir
gerekçeye dayanıyor.

Akış:

1. Yönetici panelden KENDİ firmasındaki bir kullanıcı için kod üretir.
2. Kod düz metin olarak YALNIZ o cevapta bir kez görünür; veritabanına
   SHA-256 özeti yazılır.
3. Kullanıcı bot numarasına ``BAĞLA <KOD>`` yazar.
4. Kod geçerliyse numara doğru ``company_id + user_id`` kimliğine bağlanır.

--- KAYNAKTAN AYRILAN BİRİNCİ YER: AKTİF NUMARA KÜRESEL TEK DEĞİL --------

Kaynağın kısmi UNIQUE indeksi ``uq_whatsapp_links_active_phone`` KÜRESELDİ
(``company_id`` anahtarda YOKTU) ve gerekçesi kaynağın kendi yorumunda
yazılı: "WhatsApp mesajında güvenilir bir firma SEÇİCİ yoktur".

Bu depoda seçici VAR — ``baglam.py`` + ``FİRMA SEÇ``. Bu yüzden indeks
``(company_id, phone) WHERE is_active``tır: aynı numara aynı firmada iki
kez aktif OLAMAZ, başka firmada OLABİLİR. Gerekçenin tamamı göç
``20260910_0079``un başlığındadır.

Sonuç, bu modülde iki yerde görünür: ``kod_kullan``ın ``IntegrityError``
dalı artık "numara başkasına bağlı" DEĞİL "bu numara BU FİRMADA zaten
bağlı" demektir; ve ``kimlik_coz`` tek satır yerine LİSTE döner.

--- KAYNAKTAN AYRILAN İKİNCİ YER: `kimlik_coz` FAIL-CLOSED DEĞİL, ÇOKLU --

Kaynağın ``kimlik_coz``u ikinci bir aktif satır görürse ``None`` dönüyordu
("belirsizlikte rastgele birini seçmek yanlış tenant'ın verisini dönmek
demektir"). O cümle HÂLÂ DOĞRU ve burada da uygulanıyor — ama çare
değişti: belirsizlik SESSİZCE kapatılmıyor, KULLANICIYA SORULUYOR
(``FİRMA LİSTELE`` / ``FİRMA SEÇ``). Rastgele seçim burada da YOKTUR;
seçim yapılmamışsa ``baglam.kimlik_secimi`` hiçbir firma DÖNDÜRMEZ.

--- BU TURDA SÜPÜRÜCÜ YOK — ÖLÇÜLMÜŞ BİR ÇIKARMA ------------------------

Kaynakta iki bakım fonksiyonu var: ``sureleri_gecenleri_kapat`` (süresi
dolan kodları ``EXPIRED`` damgalar) ve ``eski_denemeleri_sil``. İKİSİ DE BU
TURA ALINMADI ve gerekçe ölçüldü:

* Bu turda İŞÇİ YOKTUR, yani ikisini de ÇAĞIRACAK hiçbir şey yok.
* Süre denetimi ``kod_kullan``ın KENDİSİNDEDİR: süpürücü hiç koşmasa bile
  süresi dolmuş bir kod TÜKETİLEMEZ. Yani süpürücünün yaptığı iş defteri
  temizlemektir, bir güvence sağlamak değil.
* ``sureleri_gecenleri_kapat`` KİRACI YÜKLEMİ TAŞIYAMAZ (bütün firmaları
  tarar) ve bu, ``tests/test_core_tenant_scoping_guard.py``de AYRI bir
  güvenlik istisnası demektir. Hiçbir çağıranı olmayan kod için istisna
  harcamıyoruz; ikisi de işçiyle birlikte, WA3'te gelir.

--- DEĞİŞMEYEN TASARIM KARARLARI ----------------------------------------

* **Telefon payload'dan okunmaz.** Numara, imzası doğrulanmış Meta
  gövdesinden gelip ``normalize_phone``dan geçmiş kanonik değerdir.
* **Yetki payload'dan okunmaz.** ``company_id``/``user_id`` YALNIZ kod
  satırından gelir ve kod tüketilirken kullanıcı/firma/üyelik zinciri
  BAŞTAN doğrulanır — kod üretildikten sonra kullanıcı pasifleşmiş olabilir.
* **Tek transaction.** Bağlantının INSERT'i ile kodun tüketilmesi aynı
  SAVEPOINT içindedir. Bağlantı yazılamazsa kod tüketilmiş KALMAZ.
* **İki katmanlı hız sınırı.** Kod satırındaki ``attempt_count`` yalnız
  BULUNAN kodu korur; var olmayan kod denemeleri hiçbir satıra dokunmaz,
  bu yüzden telefon+pencere bazlı KALICI sayaç
  (``whatsapp_pairing_attempts``, WA1'in açtığı platform tablosu) ayrıca
  tutulur — uygulama belleği çok konteynerde paylaşılmaz.
* **Hiçbir cevap bilgi sızdırmaz.** Geçersiz, süresi dolmuş, kullanılmış ve
  hiç var olmamış kod AYNI genel cevabı alır.
* **Log hijyeni.** Kod, ham telefon, kullanıcı adı ve firma adı loglanmaz;
  telefon yalnız son dört haneyle görünür.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import token_digest, users, utcnow
from ..tenancy import companies, memberships
from . import schema
from .schema import (
    whatsapp_links,
    whatsapp_pairing_attempts,
    whatsapp_pairing_codes,
)
from .telefon import normalize_phone

log = logging.getLogger("nazgul.whatsapp.eslestirme")


# ---------------------------------------------------------------------------
# Kod biçimi
# ---------------------------------------------------------------------------
# Crockford Base32'nin karışan harfleri çıkarılmış hâli: I/L/O/U yok.
# (I↔1, L↔1, O↔0 karışır; U müstehcen kısaltmaları önlemek için düşer.)
# 32 karakterlik alfabe → karakter başına TAM 5 bit.
ALFABE = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# 12 karakter × 5 bit = 60 bit entropi. İstenen alt sınır 50 bitti; 60,
# WhatsApp'a elle yazmayı hâlâ makul kılarken 1024 kat pay bırakır.
KOD_UZUNLUGU = 12
KOD_ENTROPI_BIT = KOD_UZUNLUGU * 5

#: Okunabilir gösterim grubu: "ABCD-EFGH-JKMN".
GRUP = 4


class _YarisKaybedildi(Exception):
    """CAS'i kaybeden taraf; SAVEPOINT'i geri almak için İÇ sinyal."""


class EslestirmeHatasi(Exception):
    """Kod üretilemedi (yetki/durum sorunu). Mesajı KULLANICIYA gösterilebilir."""


@dataclass(frozen=True, slots=True)
class UretilenKod:
    kod_id: int
    kod: str  # DÜZ kod — yalnız bu nesnede, yalnız bir kez
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Kimlik:
    """Bir numaranın çözülebildiği TEK bir (firma, kullanıcı) adayı."""

    company_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class EslestirmeSonucu:
    basarili: bool
    link_id: int | None = None
    company_id: int | None = None
    user_id: int | None = None
    # Cevap gönderilsin mi? Hız sınırı aşıldığında sessizce düşürülür:
    # bilinmeyen numara istisnası bir mesaj/masraf saldırısına dönüşmemeli.
    cevapla: bool = True
    # Sınır ısırdı mı? YALNIZ testler ve loglar için; kullanıcıya giden
    # metin bu alandan BAĞIMSIZ olarak aynıdır (ayırt edilemezlik).
    sinirlandi: bool = False


def kod_uret_metin() -> str:
    """Kriptografik rastgelelikle kod üretir. Tahmin edilebilir sayı YOK."""
    return "".join(secrets.choice(ALFABE) for _ in range(KOD_UZUNLUGU))


def kod_bicimle(kod: str) -> str:
    """Kullanıcıya gösterim: ``ABCD-EFGH-JKMN``. Girdi kanonik olmalıdır."""
    return "-".join(kod[i : i + GRUP] for i in range(0, len(kod), GRUP))


def kod_kanonik(ham: str) -> str | None:
    """Kullanıcının yazdığını kanonik koda çevirir; geçersizse ``None``.

    Tolere edilenler: küçük harf, araya giren tire/boşluk/nokta, Türkçe
    klavyede karışan I/İ/ı biçimleri. TOLERE EDİLMEYEN: eksik/fazla karakter,
    alfabe dışı karakter. Serbest cümleden kod ÇIKARILMAZ.
    """
    if not ham:
        return None
    temiz = ham.replace("ı", "I").replace("İ", "I").upper()
    temiz = temiz.replace("-", "").replace(" ", "").replace(".", "")
    if len(temiz) != KOD_UZUNLUGU:
        return None
    # Sık karışan karakterleri alfabeye eşle (Crockford geleneği).
    cevrim = {"I": "1", "L": "1", "O": "0"}
    temiz = "".join(cevrim.get(k, k) for k in temiz)
    if any(k not in ALFABE for k in temiz):
        return None
    return temiz


def _ozet(kod: str) -> str:
    """Kanonik kodun SHA-256 hex özeti (``auth.token_digest`` sözleşmesi)."""
    return token_digest(kod)


def _yaris_dikisi() -> None:
    """Test dikişi: kod satırı OKUNDU, koşullu yazım HENÜZ yapılmadı.

    Üretimde hiçbir şey yapmaz ve hiçbir davranışı değiştirmez. Yarış
    testleri burayı monkeypatch'leyerek iki işçiyi TAM OLARAK bu noktada
    buluşturur: CAS'siz bir varyantta ikinci işçinin de aynı ``PENDING``
    görünümünü okuduğu böyle kanıtlanır. Zamanlamaya bağlı bir ``Barrier``
    kurgusu bunu YAPAMAZ — ``Barrier`` yalnız başlangıcı eşitler, kritik
    bölümü değil.
    """


def _maskeli(telefon: str) -> str:
    return f"***{telefon[-4:]}" if len(telefon) >= 4 else "***"


# ---------------------------------------------------------------------------
# Deterministik komut sözdizimi
# ---------------------------------------------------------------------------
# YALNIZ tam eşleşme. Serbest cümleden kod ÇIKARILMAZ ve dış bir modele
# HİÇBİR ŞEY gönderilmez. "BAĞLA" dışında bir şey yazan mesaj bu yola hiç
# girmez.
#
# Tolere edilen: baştaki/sondaki boşluk, araya giren fazla boşluk, küçük
# harf, Türkçe "ğ" yerine "g" (klavye/otomatik düzeltme), kodun içindeki tire.
_BAGLA_RE = re.compile(
    r"^\s*BA[GĞ]LA\s+([0-9A-Za-z\-\s]{%d,%d})\s*$" % (KOD_UZUNLUGU, KOD_UZUNLUGU * 3),
    re.IGNORECASE,
)


def bagla_ayristir(metin: str) -> str | None:
    """``BAĞLA <KOD>`` ise ham kod parçasını, değilse ``None`` döner.

    ``None`` ise mesaj bu yola AİT DEĞİLDİR ve normal akış işler. Bir dize
    ise mesaj bir eşleştirme denemesidir — geçerliliğine ``kod_kullan``
    karar verir (biçim hatası da bir eşleştirme REDDİDİR, normal cevap değil).
    """
    if not metin:
        return None
    # Türkçe küçük "ı"/"İ" normalizasyonu regex'ten ÖNCE.
    duz = metin.replace("İ", "I").replace("ı", "i")
    eslesme = _BAGLA_RE.match(duz)
    if not eslesme:
        return None
    return eslesme.group(1)


# ---------------------------------------------------------------------------
# Kod üretme (yönetici tarafı)
# ---------------------------------------------------------------------------

# TEK public red metni. Aşağıdaki BEŞ ret yolunun tamamı bu SABİT dizeyi
# üretir; çağıran (router) onu TEK bir HTTP durumuna çevirir. Amaç ayırt
# edilemezlik: farklı metin/kod, saldırgana "bu kullanıcı var ama pasif" ya
# da "bu id başka firmaya ait" bilgisini verirdi — yani kullanıcı ve firma
# envanterini sızdırırdı.
HEDEF_RED_MESAJI = "Bu kullanıcı için eşleştirme kodu üretilemez."


def _hedef_dogrula(db: Session, company_id: int, user_id: int) -> str:
    """Kullanıcı + firma + üyelik zincirini doğrular; görünen adı döner.

    Kod ÜRETİLİRKEN ve KULLANILIRKEN AYRI AYRI çağrılır: arada kullanıcı
    pasifleşmiş, üyeliği düşmüş ya da firma kapanmış olabilir.

    AYIRT EDİLEMEZLİK SÖZLEŞMESİ — şu hedeflerin HEPSİ aynı istisnayı, aynı
    metinle üretir: kullanıcı hiç yok / başka tenant'ın kullanıcısı /
    kullanıcı pasif / firma pasif / firma hiç yok / üyelik yok.

    NOT: ``user_company_memberships`` tablosunda ``is_active`` sütunu
    YOKTUR; üyelik satırın VARLIĞIYLA tanımlıdır. "Üyelik pasif" ayrı bir
    durum değil, "üyelik yok"un ta kendisidir ve aynı yola düşer.

    İç log da nedeni AÇIKLAMAZ: hangi kullanıcı/firma olduğunu ya da hangi
    koşulun tuttuğunu yazmak, log'a erişen için aynı envanteri üretirdi.
    """
    satir = db.execute(
        select(
            users.c.id,
            users.c.is_active,
            users.c.display_name,
            companies.c.is_active.label("firma_aktif"),
        )
        .select_from(users)
        .join(memberships, memberships.c.user_id == users.c.id)
        .join(companies, companies.c.id == memberships.c.company_id)
        .where(users.c.id == user_id, memberships.c.company_id == company_id)
        .limit(1)
    ).first()
    if satir is None or not satir.is_active or not satir.firma_aktif:
        log.info("whatsapp: eslestirme hedefi reddedildi")
        raise EslestirmeHatasi(HEDEF_RED_MESAJI)
    return str(satir.display_name or "")


def bekleyenleri_iptal_et(
    db: Session, company_id: int, user_id: int, *, simdi: datetime | None = None
) -> int:
    """Firma+kullanıcı için bekleyen kodları ``CANCELLED`` yapar; sayıyı döner.

    Deterministik: yeni kod üretmeden ÖNCE çağrılır, böylece "hangi kod
    geçerli" sorusunun her an tek cevabı olur.
    """
    an = simdi or utcnow()
    sonuc = db.execute(
        update(whatsapp_pairing_codes)
        .where(
            whatsapp_pairing_codes.c.company_id == company_id,
            whatsapp_pairing_codes.c.user_id == user_id,
            whatsapp_pairing_codes.c.status == schema.PAIRING_PENDING,
        )
        .values(status=schema.PAIRING_CANCELLED, cancelled_at=an)
    )
    return int(sonuc.rowcount or 0)


def kod_uret(
    db: Session,
    company_id: int,
    user_id: int,
    *,
    created_by: int | None = None,
    simdi: datetime | None = None,
) -> UretilenKod:
    """Tek kullanımlık kod üretir. DÜZ kod YALNIZ dönüş değerinde yaşar.

    Commit ETMEZ: çağıran (router) aktivite kaydıyla birlikte TEK
    transaction'da commit eder.
    """
    an = simdi or utcnow()
    _hedef_dogrula(db, company_id, user_id)

    # Önceki bekleyen kodlar deterministik biçimde düşer. Kısmi UNIQUE
    # indeks (`uq_wpc_aktif_kod`) bunu ZORUNLU kılar; burada açıkça yapılması
    # bir kısıt hatası değil, DOĞRU DAVRANIŞ üretir.
    bekleyenleri_iptal_et(db, company_id, user_id, simdi=an)

    expires_at = an + timedelta(minutes=schema.PAIRING_OMRU_DAKIKA)
    # Özet çakışması pratikte imkânsız (60 bit), ama UNIQUE kısıt GERÇEK:
    # birkaç kez dene, sessiz başarısızlık bırakma.
    for _ in range(5):
        kod = kod_uret_metin()
        try:
            with db.begin_nested():
                sonuc = db.execute(
                    insert(whatsapp_pairing_codes).values(
                        company_id=company_id,
                        user_id=user_id,
                        created_by=created_by,
                        code_digest=_ozet(kod),
                        status=schema.PAIRING_PENDING,
                        expires_at=expires_at,
                        attempt_count=0,
                        max_attempts=schema.PAIRING_MAX_ATTEMPTS,
                        created_at=an,
                    )
                )
        except IntegrityError:
            continue
        return UretilenKod(
            kod_id=int(sonuc.inserted_primary_key[0]),
            kod=kod,
            expires_at=expires_at,
        )
    raise EslestirmeHatasi("Kod üretilemedi; lütfen tekrar deneyin.")


def kod_iptal(
    db: Session, company_id: int, kod_id: int, *, simdi: datetime | None = None
) -> bool:
    """Yöneticinin bekleyen kodu iptali. TENANT KAPSAMLIDIR.

    ``company_id`` yüklemi bir süs DEĞİL: düşseydi bir firmanın yöneticisi
    BAŞKA firmanın bekleyen kodunu iptal edebilirdi ve o firmanın kullanıcısı
    hiçbir zaman bağlanamazdı.
    """
    sonuc = db.execute(
        update(whatsapp_pairing_codes)
        .where(
            whatsapp_pairing_codes.c.id == kod_id,
            whatsapp_pairing_codes.c.company_id == company_id,
            whatsapp_pairing_codes.c.status == schema.PAIRING_PENDING,
        )
        .values(status=schema.PAIRING_CANCELLED, cancelled_at=simdi or utcnow())
    )
    return int(sonuc.rowcount or 0) == 1


# ---------------------------------------------------------------------------
# Kalıcı hız sınırı (telefon + zaman penceresi)
# ---------------------------------------------------------------------------

def _pencere_basi(an: datetime) -> datetime:
    """Denemeyi SABİT bir pencereye yuvarlar (kayan pencere gerekmiyor)."""
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    dakika = schema.PAIRING_PENCERE_DAKIKA
    yuvarlanmis = (an.minute // dakika) * dakika
    return an.replace(minute=yuvarlanmis, second=0, microsecond=0)


def deneme_say(db: Session, telefon: str, *, simdi: datetime | None = None) -> int:
    """Denemeyi ATOMİK olarak artırır ve penceredeki TOPLAMI döner.

    Tek deyimlik UPSERT: iki işçi aynı anda artırsa bile sayaç KAYBOLMAZ
    (``uq_whatsapp_pairing_attempts_pencere`` çakışma hedefidir). Uygulama
    belleği KULLANILMAZ — çok konteynerde paylaşılmaz.

    Tablo bir PLATFORM tablosudur (``company_id`` YOK) ve bu ZORUNLU: deneme
    yapan numara henüz hiçbir firmaya ait değildir. WA1 onu tam bu çağıran
    için açmıştı.
    """
    an = simdi or utcnow()
    pencere = _pencere_basi(an)
    normal = normalize_phone(telefon)

    lehce = db.bind.dialect.name if db.bind is not None else ""
    if lehce == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        deyim = pg_insert(whatsapp_pairing_attempts).values(
            phone=normal, window_start=pencere, attempt_count=1, updated_at=an
        )
        deyim = deyim.on_conflict_do_update(
            index_elements=["phone", "window_start"],
            set_={
                "attempt_count": whatsapp_pairing_attempts.c.attempt_count + 1,
                "updated_at": an,
            },
        ).returning(whatsapp_pairing_attempts.c.attempt_count)
        return int(db.execute(deyim).scalar_one())

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    deyim = sqlite_insert(whatsapp_pairing_attempts).values(
        phone=normal, window_start=pencere, attempt_count=1, updated_at=an
    )
    deyim = deyim.on_conflict_do_update(
        index_elements=["phone", "window_start"],
        set_={
            "attempt_count": whatsapp_pairing_attempts.c.attempt_count + 1,
            "updated_at": an,
        },
    ).returning(whatsapp_pairing_attempts.c.attempt_count)
    return int(db.execute(deyim).scalar_one())


# ---------------------------------------------------------------------------
# Kod tüketme (WhatsApp tarafı)
# ---------------------------------------------------------------------------
# Geçersiz, süresi dolmuş, kullanılmış, iptal edilmiş ve HİÇ VAR OLMAMIŞ kod
# AYNI cevabı alır. Kodun/firmanın/kullanıcının varlığı ayırt edilemez.
RED_MESAJI = (
    "Eşleştirme kodu geçersiz, süresi dolmuş veya kullanılmış. "
    "ERP yöneticinizden yeni kod isteyin."
)
BASARI_MESAJI = (
    "WhatsApp numaranız ERP hesabınıza güvenle bağlandı. "
    "Artık borç, stok ve tahsilat işlemlerini kullanabilirsiniz."
)


def _basarisiz(cevapla: bool, *, sinirlandi: bool = False) -> EslestirmeSonucu:
    return EslestirmeSonucu(basarili=False, cevapla=cevapla, sinirlandi=sinirlandi)


def kod_kullan(
    db: Session,
    telefon: str,
    ham_kod: str,
    *,
    simdi: datetime | None = None,
) -> EslestirmeSonucu:
    """``BAĞLA <KOD>`` mesajını işler. Commit ETMEZ — çağıran commit eder.

    SÖZLEŞME:

    * Bağlantı INSERT'i ile kodun tüketilmesi AYNI transaction'dadır.
      Bağlantı yazılamazsa kod tüketilmiş KALMAZ (SAVEPOINT geri alır).
    * Aynı kodu iki işçi aynı anda kullanırsa CAS (``status='PENDING'``
      koşullu UPDATE) TAM BİR kazanan bırakır.
    * Aynı numara aynı firmada iki kez bağlanamaz; hakem
      ``uq_whatsapp_links_aktif_numara`` KISMİ UNIQUE indeksidir. BAŞKA bir
      firmaya bağlanmak SERBESTTİR (göç 0079 başlığı).
    * Hız sınırı aşılmışsa kod TÜKETİLMEZ ve satırın ``attempt_count``u
      ARTAR; cevap da üretilmez (``cevapla=False``) — bilinmeyen numara
      istisnası bir mesaj/masraf saldırısına dönüşmemelidir.
    """
    an = simdi or utcnow()
    normal = normalize_phone(telefon)
    if not normal:
        return _basarisiz(False)

    # 1) KALICI hız sınırı — kod BULUNMADAN ÖNCE. Var olmayan kod denemesi de
    #    sayılır; yoksa saldırgan hiçbir satıra dokunmadan sınırsız cevap
    #    ürettirebilirdi.
    deneme = deneme_say(db, normal, simdi=an)
    sinirda = deneme > schema.PAIRING_PENCERE_SINIRI
    # Sınırın altında ama cevap eşiğinin üstündeyse: işlenir, cevap verilmez.
    cevapla = (not sinirda) and deneme <= schema.PAIRING_CEVAP_SINIRI

    # 2) Biçim. Serbest cümleden kod ÇIKARILMAZ; tam sözdizimi şart.
    kod = kod_kanonik(ham_kod)
    if kod is None:
        return _basarisiz(cevapla, sinirlandi=sinirda)

    beklenen_ozet = _ozet(kod)

    # 3) Kod satırı. PostgreSQL'de satır kilidi alınır: iki işçi aynı kodu
    #    aynı anda okuyup İKİSİ de "PENDING" görmesin.
    sorgu = select(
        whatsapp_pairing_codes.c.id,
        whatsapp_pairing_codes.c.company_id,
        whatsapp_pairing_codes.c.user_id,
        whatsapp_pairing_codes.c.code_digest,
        whatsapp_pairing_codes.c.status,
        whatsapp_pairing_codes.c.expires_at,
        whatsapp_pairing_codes.c.attempt_count,
        whatsapp_pairing_codes.c.max_attempts,
    ).where(whatsapp_pairing_codes.c.code_digest == beklenen_ozet)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        sorgu = sorgu.with_for_update()
    satir = db.execute(sorgu).mappings().first()
    if satir is None:
        return _basarisiz(cevapla, sinirlandi=sinirda)

    # 3b) ÖZET SABİT SÜREDE DOĞRULANIR — ve bu bir tekrar DEĞİL, KARARIN
    #     KENDİSİDİR. Yukarıdaki `WHERE code_digest = :ozet` bir SUNUCU
    #     karşılaştırmasıdır: nasıl yapıldığı (indeks taraması, kısmi
    #     eşleşme, gelecekte bir önek/aralık sorgusuna dönüşmesi) bu kodun
    #     kontrolünde DEĞİLDİR. Satır elimize geldikten sonra "bu gerçekten
    #     aradığımız satır mı" sorusunu UYGULAMA cevaplar ve cevabı sabit
    #     sürede verir.
    #
    #     MUTASYON: bu satırı `satir["code_digest"] == beklenen_ozet` yapmak
    #     DAVRANIŞI DEĞİŞTİRMEZ — hiçbir davranış testi onu öldüremez.
    #     Kaybolan şey ZAMANLAMADIR: `==` ilk farklı baytta döner. Kapı bu
    #     yüzden AST'dedir (`test_wa2_eslestirme.py::
    #     test_KOD_OZETI_SABIT_SURELI_KARSILASTIRILIYOR`), WA1'in
    #     `verify_signature` kapısıyla AYNI kalıpta.
    if not hmac.compare_digest(str(satir["code_digest"]), beklenen_ozet):
        return _basarisiz(cevapla, sinirlandi=sinirda)

    # 4) HIZ SINIRI ISIRDI: kod DOĞRU olsa bile TÜKETİLMEZ. Satırın kendi
    #    sayacı da artar, yani ısrar eden bir saldırgan pencereyi beklese
    #    bile kodu kilitlemiş olur.
    if sinirda:
        log.warning(
            "whatsapp: eslestirme deneme siniri asildi (%s), kod tuketilmedi",
            _maskeli(normal),
        )
        _denemeyi_artir(db, int(satir["company_id"]), int(satir["id"]))
        return _basarisiz(False, sinirlandi=True)

    # Bulunan kodun KENDİ deneme tavanı: aynı koda ısrarla yanlış koşulda
    # gelinmesi (ör. numara bu firmada zaten bağlı) kodu kilitler.
    if int(satir["attempt_count"]) >= int(satir["max_attempts"]):
        return _basarisiz(cevapla)

    if satir["status"] != schema.PAIRING_PENDING:
        return _basarisiz(cevapla)

    son = satir["expires_at"]
    if son is not None and son.tzinfo is None:
        son = son.replace(tzinfo=timezone.utc)
    if son is not None and son <= an:
        return _basarisiz(cevapla)

    company_id = int(satir["company_id"])
    user_id = int(satir["user_id"])

    # 5) Kimlik zinciri TEKRAR doğrulanır: kod üretildikten sonra kullanıcı
    #    pasifleşmiş, üyeliği düşmüş ya da firma kapanmış olabilir.
    try:
        _hedef_dogrula(db, company_id, user_id)
    except EslestirmeHatasi:
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)

    _yaris_dikisi()

    # 6) Bağlantı + tüketim TEK transaction. SAVEPOINT, bağlantı yazılamazsa
    #    kodun tüketilmiş kalmasını engeller.
    #
    #    pysqlite TUZAĞI: SAVEPOINT'ten önce YALNIZ SELECT çalışmışsa sürücü
    #    örtük bir transaction başlatır ve RELEASE SAVEPOINT onu COMMIT eder
    #    — dıştaki rollback yazılanı geri alamaz. Burada (1) adımındaki
    #    deneme sayacı UPSERT'i çalıştığı için GERÇEK bir transaction ZATEN
    #    açıktır ve tuzak tetiklenmez. Bu SIRA tesadüf değil, SÖZLEŞMEDİR:
    #    hız sınırı yazması SAVEPOINT'ten ÖNCE kalmalıdır.
    try:
        with db.begin_nested():
            sonuc = db.execute(
                insert(whatsapp_links).values(
                    company_id=company_id,
                    user_id=user_id,
                    phone=normal,
                    is_active=True,
                    created_at=an,
                    updated_at=an,
                    created_by=None,
                )
            )
            link_id = int(sonuc.inserted_primary_key[0])

            # CAS: YALNIZ hâlâ PENDING olan kod tüketilebilir. İki işçi
            # yarışırsa ``rowcount`` TAM OLARAK BİRİNDE 1 olur.
            #
            # ÜÇÜNCÜ KATMAN ve bu ÖLÇÜLDÜ: yukarıdaki satır kilidi (yalnız
            # PostgreSQL) ve okuma sonrası durum denetimiyle birlikte ÜÇ
            # bağımsız hakem var; PG'de yirmi eşzamanlı işçi üzerinde
            # ölçüldü, HER BİRİ TEK BAŞINA yetiyor ve ÜÇÜ BİRDEN düşerse
            # yirmi bağlantı doğuyor.
            #
            # CAS YİNE DE VAZGEÇİLMEZ: satır kilidi SQLite'ta HİÇ ÇALIŞMAZ
            # ve durum denetimi tek başına bir TOCTOU'dur — okuma ile yazma
            # arasında satır değişebilir. Yazmayı KOŞULA BAĞLAYAN tek katman
            # budur. Hiçbir davranış testi bu tek mutantı öldüremediği için
            # kapı AST'dedir: `tests/test_wa2_eslestirme.py::
            # test_CAS_KOSULU_STATUS_PENDING_ve_KIRACI_YUKLEMLI`.
            cas = db.execute(
                update(whatsapp_pairing_codes)
                .where(
                    whatsapp_pairing_codes.c.id == int(satir["id"]),
                    whatsapp_pairing_codes.c.company_id == company_id,
                    whatsapp_pairing_codes.c.status == schema.PAIRING_PENDING,
                )
                .values(
                    status=schema.PAIRING_CONSUMED,
                    consumed_at=an,
                    consumed_link_id=link_id,
                )
            )
            if int(cas.rowcount or 0) != 1:
                # Kaybeden taraf: bağlantı INSERT'i de geri alınır.
                raise _YarisKaybedildi()
    except _YarisKaybedildi:
        return _basarisiz(cevapla)
    except IntegrityError:
        # Numara BU FİRMADA zaten aktif bir bağlantıya sahip. Otomatik
        # TAŞIMA YOK: bağlantıyı kapatmak ayrı bir yönetici işlemidir.
        # (BAŞKA bir firmaya bağlanmak bu dala HİÇ DÜŞMEZ — kısmi tekil
        # `company_id`yi de kapsıyor.)
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)

    log.info(
        "whatsapp: numara eslestirildi (%s) firma=%s", _maskeli(normal), company_id
    )
    return EslestirmeSonucu(
        basarili=True,
        link_id=link_id,
        company_id=company_id,
        user_id=user_id,
        cevapla=True,
    )


def _denemeyi_artir(db: Session, company_id: int, kod_id: int) -> None:
    """Bulunan kodun deneme sayacını artırır (AYRI deyim, geri alınmaz).

    KİRACI YÜKLEMİ TAŞIR ve bu bir süs DEĞİL. Satır zaten `id` ile tekil
    bulunuyor, yani yüklem bugün hiçbir satırı ELEMİYOR — ama `company_id`
    çağırana `satir["company_id"]`den geliyor ve yüklem, bu yazmanın hangi
    firmanın defterine dokunduğunu SORGUNUN KENDİSİNDE söylüyor. Yüklemsiz
    yazılsaydı kiracı nöbetçisi (`test_core_tenant_scoping_guard.py`) AYRI
    bir güvenlik istisnası isterdi ve o istisna, bir gün `id` yerine başka
    bir anahtarla aranan bir varyanta SESSİZCE uygulanabilir hâlde dururdu.
    """
    db.execute(
        update(whatsapp_pairing_codes)
        .where(
            whatsapp_pairing_codes.c.id == kod_id,
            whatsapp_pairing_codes.c.company_id == company_id,
        )
        .values(attempt_count=whatsapp_pairing_codes.c.attempt_count + 1)
    )


# ---------------------------------------------------------------------------
# Kimlik çözme (numara → adaylar)
# ---------------------------------------------------------------------------

def kimlik_coz(db: Session, telefon: str) -> list[Kimlik]:
    """Numaranın çözülebildiği (firma, kullanıcı) ADAYLARINI döner.

    Boş liste = numara hiçbir kimliğe bağlı değil. Tek eleman = kimlik
    kesindir. Birden çok eleman = ``FİRMA SEÇ`` gerekir; ÇAĞIRAN RASTGELE
    BİRİNİ SEÇEMEZ (kaynaktan devralınan en önemli cümle: belirsizlikte
    rastgele seçim, YANLIŞ tenant'ın verisini dönmek demektir).

    İKİ ADIM, ve ayrım BİLİNÇLİ:

    1. **Aday taraması** — YALNIZ ``whatsapp_links``, yalnız ``phone`` ve
       ``is_active`` yüklemiyle. Bu sorgu KİRACI YÜKLEMİ TAŞIMAZ ve
       TAŞIYAMAZ: sorduğu şeyin kendisi "bu numara HANGİ firmalarda aktif".
       Bir ``company_id=:cid`` yüklemi eklemek, cevabı soruyla birlikte
       vermek olurdu. Kiracı nöbetçisinde AYRI bir istisna kaydı vardır
       (``tests/test_core_tenant_scoping_guard.py::
       CEKIRDEK_KIRACI_ISTISNALARI``) ve gerekçesi orada yazılı.

    2. **Aday doğrulaması** — her aday için ``_hedef_dogrula``. O sorgu
       KİRACI YÜKLEMLİDİR (``memberships.c.company_id == company_id``) ve
       zinciri BAŞTAN kurar: aktif kullanıcı → aktif firma → O FİRMADAKİ
       ÜYELİK. Bu adım bir tekrar DEĞİL, kapının kendisi: bir kullanıcının
       bir firmadaki üyeliği düştüğünde bağlantı satırı defterde KALIR (izi
       silmiyoruz) ve o satır tek başına yetki iddiası olsaydı, kovulmuş
       kullanıcı WhatsApp'tan veri okumaya devam ederdi.

    İki adımı TEK sorguya (üyelik JOIN'i ile) katlamak DENENDİ ve
    BIRAKILDI: o hâlde ``user_company_memberships`` ifadesi ne kiracıya ne
    de isteğin KULLANICISINA bağlanabiliyordu, yani nöbetçinin üyelik
    kapısına (``test_memberships_ifadeleri_KULLANICI_anahtariyla_gecer``)
    İKİNCİ bir istisna gerekiyordu. Ayrı sorgu o istisnayı gereksiz kılıyor
    ve üyelik denetimini kiracı yüklemli tek bir yere topluyor.

    Sıra DETERMİNİSTİK (``company_id``): ``FİRMA LİSTELE``de gösterilen
    numaralandırma ile ``FİRMA SEÇ <n>``in çözdüğü satır AYNI olmak zorunda.
    """
    normal = normalize_phone(telefon)
    if not normal:
        return []
    satirlar = db.execute(
        select(whatsapp_links.c.company_id, whatsapp_links.c.user_id)
        .where(
            whatsapp_links.c.phone == normal,
            whatsapp_links.c.is_active.is_(True),
        )
        .order_by(whatsapp_links.c.company_id)
    ).all()

    adaylar: list[Kimlik] = []
    for satir in satirlar:
        company_id = int(satir[0])
        user_id = int(satir[1])
        try:
            _hedef_dogrula(db, company_id, user_id)
        except EslestirmeHatasi:
            # Pasif kullanıcı / kapanmış firma / düşmüş üyelik. Bağlantı
            # satırı duruyor ama kimlik ÇÖZÜLMÜYOR — fail-closed.
            continue
        adaylar.append(Kimlik(company_id=company_id, user_id=user_id))
    return adaylar


__all__ = [
    "ALFABE",
    "BASARI_MESAJI",
    "EslestirmeHatasi",
    "EslestirmeSonucu",
    "HEDEF_RED_MESAJI",
    "KOD_ENTROPI_BIT",
    "KOD_UZUNLUGU",
    "Kimlik",
    "RED_MESAJI",
    "UretilenKod",
    "bagla_ayristir",
    "bekleyenleri_iptal_et",
    "deneme_say",
    "kimlik_coz",
    "kod_bicimle",
    "kod_iptal",
    "kod_kanonik",
    "kod_kullan",
    "kod_uret",
    "kod_uret_metin",
]
