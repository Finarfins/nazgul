"""Konuşma bağlamı ve FİRMA SEÇ komutu (WA2).

Kaynak `nazgul_website/backend/app/whatsapp/service.py`nin bağlam
fonksiyonları. Kaynağın bağlamı "az önce hangi listeyi gösterdik" sorusunu
cevaplıyordu; buradaki bağlam TEK BİR soruya cevap verir: **hangi firma
aktif**.

--- NEDEN BU MODÜL VAR: `kimlik_coz` ARTIK LİSTE DÖNÜYOR ----------------

Kaynağın `kimlik_coz`u ikinci bir aktif bağlantı görürse `None` dönüyordu.
Bu depoda aynı numara İKİ FARKLI firmada aktif olabilir (göç
`20260910_0079`un başlığı) çünkü muhasebecisi iki firmaya bakan kullanıcı
bu ürünün tipik kullanıcısıdır.

Belirsizliği RASTGELE kapatmak, yanlış tenant'ın rakamlarını dönmek
demektir; bu yüzden belirsizlik KULLANICIYA SORULUR ve cevabı burada
saklanır.

--- BAĞLAMDAKİ DEĞER BİR YETKİ İDDİASI DEĞİLDİR -------------------------

`payload` içindeki `aktif_firma`, kullanıcının SEÇİMİDİR — yetkisi değil.
Her okumada seçim, o anki AKTİF BAĞLANTI adaylarına karşı yeniden
doğrulanır (`kimlik_secimi`): üyeliği düşmüş, firması kapanmış ya da
bağlantısı kapatılmış bir seçim, bağlam satırı DURSA BİLE çözülmez.

Yazsaydık ve okurken doğrulamasaydık, bir kez seçilmiş firma kullanıcının
o firmadan çıkarılmasından SONRA da geçerli kalırdı — yani bağlam tablosu
sessiz bir yetki deposuna dönerdi.

--- SÜRE: 30 DAKİKA -----------------------------------------------------

"Aktif firma" bir yetki seçimidir ve seçim, kullanıcının o an ne yaptığını
bildiği ANA bağlıdır. Bir gün sonra gelen "borcum ne kadar" mesajının dün
seçilmiş firmaya sessizce cevap vermesi, YANLIŞ firmanın rakamını verirdi.
Süre `schema.BAGLAM_OMRU_DAKIKA`dadır.

--- BU MODÜLÜN YAPMADIĞI: İŞÇİ, META CEVABI — ÖLÇÜLMEDİ -----------------

`komut_isle` bir DİZE döner ve hiçbir yere göndermez. Kuyruğu bu
fonksiyonlara bağlayan işçi ve Meta'ya giden cevap WA3'ün işidir.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..tenancy import companies
from . import eslestirme, schema
from .eslestirme import Kimlik
from .schema import whatsapp_context
from .telefon import normalize_phone

log = logging.getLogger("nazgul.whatsapp.baglam")

#: Bağlam yükünün TEK anahtarı. Sözlük genişletilebilir ama bu anahtarın
#: anlamı sabittir: kullanıcının SEÇTİĞİ firma kimliği.
AKTIF_FIRMA = "aktif_firma"


@dataclass(frozen=True, slots=True)
class Secim:
    """Bir numaranın kimlik çözümünün TAM sonucu.

    ``kimlik`` doluysa iş yapılabilir. ``firma_secimi_gerekli`` ise
    kullanıcıya soru sorulmalıdır. İkisi de boşsa numara TANINMIYOR.
    """

    kimlik: Kimlik | None
    adaylar: tuple[Kimlik, ...]
    firma_secimi_gerekli: bool


def baglam_yaz(
    db: Session,
    kimlik: Kimlik,
    telefon: str,
    icerik: dict,
    *,
    simdi: datetime | None = None,
) -> None:
    """Üçlü (firma+kullanıcı+numara) başına TEK bağlam satırı tutar.

    Önce SİL sonra YAZ: ``uq_whatsapp_context_scope`` üçlüyü tekil kılıyor
    ve "yenisi eskisini değiştirir" kuralının hakemi o kısıttır. Silme
    yüklemi ÜÇÜ DE taşır — ``company_id`` dâhil, çünkü bu bir KİRACI
    tablosudur ve yüklemsiz bir silme başka firmanın satırına dokunurdu.
    """
    an = simdi or utcnow()
    normal = normalize_phone(telefon)
    db.execute(
        delete(whatsapp_context).where(
            whatsapp_context.c.company_id == kimlik.company_id,
            whatsapp_context.c.user_id == kimlik.user_id,
            whatsapp_context.c.phone == normal,
        )
    )
    db.execute(
        insert(whatsapp_context).values(
            company_id=kimlik.company_id,
            user_id=kimlik.user_id,
            phone=normal,
            payload=json.dumps(icerik, ensure_ascii=False),
            created_at=an,
            expires_at=an + timedelta(minutes=schema.BAGLAM_OMRU_DAKIKA),
        )
    )


def baglam_oku(
    db: Session,
    kimlik: Kimlik,
    telefon: str,
    *,
    simdi: datetime | None = None,
) -> dict | None:
    """Süresi GEÇMEMİŞ bağlamı döner; yoksa ya da geçmişse ``None``.

    Süre denetimi SORGUDA: satırı okuyup Python'da elemek, süresi dolmuş
    bir bağlamın bir kod yolunda gözden kaçmasına açık kapı bırakırdı.
    """
    an = simdi or utcnow()
    normal = normalize_phone(telefon)
    ham = db.execute(
        select(whatsapp_context.c.payload).where(
            whatsapp_context.c.company_id == kimlik.company_id,
            whatsapp_context.c.user_id == kimlik.user_id,
            whatsapp_context.c.phone == normal,
            whatsapp_context.c.expires_at > an,
        )
    ).scalar_one_or_none()
    if ham is None:
        return None
    try:
        icerik = json.loads(ham)
    except ValueError:
        # Bozuk yük bir KUSURDUR ama çağıranı patlatmaz: bağlam YOK sayılır
        # ve kullanıcıya yeniden sorulur. Sessizce YANLIŞ firmaya düşmekten
        # her zaman daha iyidir.
        log.warning("whatsapp: baglam yuku cozulemedi, yok sayildi")
        return None
    return icerik if isinstance(icerik, dict) else None


def baglami_temizle(
    db: Session, adaylar: tuple[Kimlik, ...] | list[Kimlik], telefon: str
) -> None:
    """Numaranın BÜTÜN adaylarındaki bağlam satırlarını siler.

    Her silme KENDİ ``company_id`` eşitliğini taşır; ``IN`` yazmak kiracı
    yüklemini bir listeye gömerdi ve nöbetçinin okuduğu şey artık "bu sorgu
    tek firmayla daralıyor" olmazdı. Aday sayısı bir kullanıcının üye
    olduğu firma sayısıdır — döngü ucuz.
    """
    normal = normalize_phone(telefon)
    for aday in adaylar:
        db.execute(
            delete(whatsapp_context).where(
                whatsapp_context.c.company_id == aday.company_id,
                whatsapp_context.c.user_id == aday.user_id,
                whatsapp_context.c.phone == normal,
            )
        )


def kimlik_secimi(
    db: Session, telefon: str, *, simdi: datetime | None = None
) -> Secim:
    """Numarayı kimliğe çözer. BAĞLAM ÖNCE okunur.

    SIRA ÖNEMLİ ve mutasyonu ADIYLA yazılı:

      1. Adaylar (`eslestirme.kimlik_coz`) — aktif bağlantı + aktif
         kullanıcı + aktif firma + üyelik.
      2. BAĞLAM: adaylardan birinde süresi geçmemiş bir seçim var mı?
         Varsa O adaydır. MUTASYON: bu adımı atlamak (`baglam_oku`
         çağrısını kaldırmak) çok firmalı kullanıcıyı sonsuza dek
         "FİRMA SEÇ" ekranında bırakır — kapı:
         `test_BAGLAM_COKLU_FIRMADA_SECIMI_COZUYOR`.
      3. Tek aday varsa o. Bağlam GEREKMEZ.
      4. Çok aday ve seçim yoksa: ``firma_secimi_gerekli``. RASTGELE
         SEÇİM YOKTUR.

    Bağlam yalnız hâlâ ADAY olan bir firmayı çözebilir: üyeliği düşmüş bir
    seçimin bağlam satırı dursa bile 1. adımda eleniyor.
    """
    an = simdi or utcnow()
    adaylar = tuple(eslestirme.kimlik_coz(db, telefon))
    if not adaylar:
        return Secim(kimlik=None, adaylar=(), firma_secimi_gerekli=False)

    for aday in adaylar:
        icerik = baglam_oku(db, aday, telefon, simdi=an)
        if icerik is None:
            continue
        secilen = icerik.get(AKTIF_FIRMA)
        # Yükteki değer, satırın KENDİ firmasıyla tutmak zorunda. Tutmuyorsa
        # yük bozulmuş ya da elle kurcalanmıştır ve HİÇBİR yetki iddiası
        # taşımaz — yok sayılır.
        if isinstance(secilen, int) and secilen == aday.company_id:
            return Secim(kimlik=aday, adaylar=adaylar, firma_secimi_gerekli=False)

    if len(adaylar) == 1:
        return Secim(kimlik=adaylar[0], adaylar=adaylar, firma_secimi_gerekli=False)

    return Secim(kimlik=None, adaylar=adaylar, firma_secimi_gerekli=True)


def firma_sec(
    db: Session,
    telefon: str,
    sira: int,
    *,
    simdi: datetime | None = None,
) -> Kimlik | None:
    """``FİRMA SEÇ <n>``: 1 tabanlı sırayı aktif firma yapar.

    Sıra ``kimlik_coz``un DETERMİNİSTİK sıralamasıyla aynıdır
    (``company_id``), yani ``FİRMA LİSTELE``de gösterilen numara ile burada
    çözülen satır AYNIDIR. Aralık dışında ``None`` döner ve HİÇBİR ŞEY
    yazılmaz.

    Seçim yazılmadan ÖNCE bütün adaylardaki eski bağlam satırları silinir:
    aksi hâlde A firması seçiliyken B seçildiğinde İKİ satır kalır ve
    "aktif firma" sorusunun iki cevabı olurdu.
    """
    adaylar = tuple(eslestirme.kimlik_coz(db, telefon))
    if sira < 1 or sira > len(adaylar):
        return None
    secilen = adaylar[sira - 1]
    baglami_temizle(db, adaylar, telefon)
    baglam_yaz(
        db, secilen, telefon, {AKTIF_FIRMA: secilen.company_id}, simdi=simdi
    )
    return secilen


def firma_adlari(db: Session, adaylar: tuple[Kimlik, ...] | list[Kimlik]) -> list[str]:
    """Adayların firma adları — sıra KORUNUR.

    Ad tek tek okunuyor ve her okuma KENDİ ``company_id`` eşitliğini
    taşıyor. ``IN`` ile tek sorguya indirmek kiracı yüklemini bir listeye
    gömerdi; ayrıca sıra sorgudan değil, ``adaylar``dan gelmek zorunda.
    """
    adlar: list[str] = []
    for aday in adaylar:
        ad = db.execute(
            select(companies.c.name).where(companies.c.id == aday.company_id)
        ).scalar_one_or_none()
        adlar.append(str(ad or "").strip() or f"Firma #{aday.company_id}")
    return adlar


# ---------------------------------------------------------------------------
# Deterministik komut sözdizimi
# ---------------------------------------------------------------------------
# `BAĞLA` ile AYNI sınıfta: TAM eşleşme, serbest cümleden çıkarım YOK.
# Türkçe "İ/ı" ve "Ğ/g" toleransı klavye/otomatik düzeltme içindir.
_LISTELE_RE = re.compile(r"^\s*F[İI]RMA\s+L[İI]STELE\s*$", re.IGNORECASE)
_SEC_RE = re.compile(r"^\s*F[İI]RMA\s+SE[ÇC]\s+(\d{1,3})\s*$", re.IGNORECASE)

TANINMAYAN_NUMARA_MESAJI = (
    "Bu numara bir ERP hesabına bağlı değil. "
    "Yöneticinizden eşleştirme kodu isteyip \"BAĞLA <KOD>\" yazın."
)
FIRMA_SECIN_MESAJI = (
    "Bu numara birden çok firmaya bağlı. "
    "\"FİRMA LİSTELE\" yazıp \"FİRMA SEÇ <numara>\" ile birini seçin."
)
SIRA_GECERSIZ_MESAJI = "Geçersiz firma numarası. \"FİRMA LİSTELE\" ile bakın."
KOMUT_ANLASILMADI_MESAJI = (
    "Komut anlaşılamadı. Kullanılabilir komutlar: "
    "\"FİRMA LİSTELE\", \"FİRMA SEÇ <numara>\"."
)


def _turkce_buyut(metin: str) -> str:
    return metin.replace("ı", "I").replace("İ", "I")


def firma_komutu(
    db: Session, telefon: str, metin: str, *, simdi: datetime | None = None
) -> str | None:
    """YALNIZ ``FİRMA LİSTELE`` / ``FİRMA SEÇ <n>``; başka her şey ``None``.

    WA3'ün işçisi (`app/whatsapp/service.py`) bu iki komutu niyet
    çözümünden ÖNCE denemek zorunda ve `komut_isle`ı kullanamaz: o
    fonksiyon `BAĞLA`yı da işler ve bağlı kullanıcıya
    `KOMUT_ANLASILMADI_MESAJI` döner — yani niyet akışına hiç yer
    bırakmaz.

    AYRI BİR GÖVDE YAZILMADI, ORTAK GÖVDE DIŞARI ALINDI: `komut_isle` de
    artık bu fonksiyonu çağırıyor, yani iki yüzey AYNI sözdizimini ve AYNI
    sırayı paylaşıyor. Kopya bir ayrıştırıcı, birinin tanıdığı komutu
    ötekinin tanımadığı güne kadar sessiz kalırdı.
    """
    an = simdi or utcnow()
    duz = _turkce_buyut(metin or "")

    if _LISTELE_RE.match(duz):
        adaylar = tuple(eslestirme.kimlik_coz(db, telefon))
        if not adaylar:
            return TANINMAYAN_NUMARA_MESAJI
        adlar = firma_adlari(db, adaylar)
        satirlar = [f"{i}. {ad}" for i, ad in enumerate(adlar, start=1)]
        return "Bağlı firmalarınız:\n" + "\n".join(satirlar)

    sec = _SEC_RE.match(duz)
    if sec:
        secilen = firma_sec(db, telefon, int(sec.group(1)), simdi=an)
        if secilen is None:
            return SIRA_GECERSIZ_MESAJI
        ad = firma_adlari(db, (secilen,))[0]
        return f"Aktif firma: {ad}."

    return None


def komut_isle(
    db: Session, telefon: str, metin: str, *, simdi: datetime | None = None
) -> str:
    """Metni komuta çevirir ve KULLANICIYA GÖSTERİLECEK dizeyi döner.

    HİÇBİR ŞEY GÖNDERMEZ ve commit ETMEZ: çağıran (WA3'ün işçisi) hem
    gönderimi hem transaction sınırını kendisi yönetir. Bu tur bilerek
    böyle: gönderim yolunu ölçmeden yazmak, ölçülmemiş bir iddia olurdu.

    SIRA ve gerekçeleri:

      1. ``BAĞLA <KOD>`` — kimlik ÇÖZÜLMEDEN ÖNCE. Eşleştirme tanımı gereği
         HENÜZ BAĞLI OLMAYAN numaradan gelir; kimlik çözümü onu "tanınmayan
         numara"ya düşürür ve kullanıcı hiçbir zaman bağlanamazdı.
      2. ``FİRMA LİSTELE`` / ``FİRMA SEÇ`` — kimlik çözümünden ÖNCE, çünkü
         ikisi de tam olarak çözümün BELİRSİZ olduğu durumda anlamlıdır.
      3. Geri kalan her şey: önce kimlik çözülür, çözülemezse soru sorulur.
    """
    an = simdi or utcnow()
    ham = metin or ""

    ham_kod = eslestirme.bagla_ayristir(ham)
    if ham_kod is not None:
        sonuc = eslestirme.kod_kullan(db, telefon, ham_kod, simdi=an)
        if sonuc.basarili:
            return eslestirme.BASARI_MESAJI
        # Sınır ısırdığında da AYNI metin döner; ayrım `cevapla` alanındadır
        # ve gönderim kararını veren çağırana aittir. Metni farklılaştırmak,
        # "sınıra takıldın" bilgisini saldırgana vermek olurdu.
        return eslestirme.RED_MESAJI

    firma_cevabi = firma_komutu(db, telefon, ham, simdi=an)
    if firma_cevabi is not None:
        return firma_cevabi

    secim = kimlik_secimi(db, telefon, simdi=an)
    if secim.firma_secimi_gerekli:
        return FIRMA_SECIN_MESAJI
    if secim.kimlik is None:
        return TANINMAYAN_NUMARA_MESAJI
    return KOMUT_ANLASILMADI_MESAJI


__all__ = [
    "AKTIF_FIRMA",
    "FIRMA_SECIN_MESAJI",
    "KOMUT_ANLASILMADI_MESAJI",
    "SIRA_GECERSIZ_MESAJI",
    "Secim",
    "TANINMAYAN_NUMARA_MESAJI",
    "baglam_oku",
    "baglam_yaz",
    "baglami_temizle",
    "firma_adlari",
    "firma_komutu",
    "firma_sec",
    "kimlik_secimi",
    "komut_isle",
]
