"""WhatsApp numarasını bir CARİYE bağlayan defter (F10-1a).

`eslestirme.py` bir numarayı PERSONELE (`app_users`) bağlar; bu modül aynı
numarayı bir TARAFA — müşteri ya da tedarikçi satırına — bağlar. İkisi AYRI
tablolarda yaşar ve bu ayrım ölçülmüş bir karardır: keşif
`docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §3.2, seçenek B.

--- DESEN AYNI, GÖVDE KOPYA DEĞİL ----------------------------------------

`eslestirme.py`nin dört sözleşmesi burada da geçerlidir ve her biri bu
dosyada YENİDEN kuruldu (çağrılarak değil): tek kullanımlık düz kod, SHA-256
özeti, hedef numaraya bağlı kod (SEC-1), kalıcı telefon+pencere sayacı, tek
transaction (bağlantı INSERT'i + kodun CAS ile tüketilmesi).

PAYLAŞILAN TEK ŞEY SAYAÇTIR ve bu bilinçli: `eslestirme.deneme_say` aynı
PLATFORM tablosuna (`whatsapp_pairing_attempts`) yazar ve anahtar
`(phone, window_start)`tır. Taraf denemesi personel denemesiyle AYNI kovaya
düşer, yani sayaç TOPLAM denemeyi sayar. Bu personel sınırını ZAYIFLATMAZ,
SIKILAŞTIRIR — tabloya `kind` sütunu eklemek ise anahtarı değiştirmek, yani
yürürlükteki eşleştirme sınırını bir göç boyunca gevşetmek olurdu (keşif
§5.3'ün K6 gerekçesi).

--- İKİ NORMALLEŞTİRİCİ, İKİ AYRI İŞ (K2) --------------------------------

Keşif §2.2 ölçtü: `telefon.normalize_phone` (gevşek) ile
`consents.normalize_msisdn` (sıkı) demo kümesinin %100'ünde ayrışıyor.
Karar ikisini de kullanır ve her birine TEK bir iş verir:

* **Doğrulama** `consents.normalize_msisdn` ile yapılır (sıkı). Gerekçe:
  eşleştirilen numara YARIN KVKK rıza kaydının alıcısı olacaktır (rızayı
  F10-1b'de çiftçinin ilk mesajı verir) ve rıza defteri numarayı o biçimde
  saklar. Gevşek doğrulama, rızası HİÇBİR ZAMAN yazılamayacak bir bağlantı
  üretirdi — yani bağlanmış ama sonsuza dek fail-closed kalan bir çiftçi.
  Kapı bu yüzden BURADA, eşleştirme anındadır: sorun F10-1b'de değil,
  numaranın deftere girdiği anda görünmelidir.
* **Saklama** `telefon.normalize_phone` çıktısıdır. Gerekçe:
  `whatsapp_inbound.sender_phone` o biçimdedir ve karşılaştırma oradan
  geçer. `+90...` saklansaydı her karşılaştırma bir dönüşüm daha isterdi.

--- ÜÇ RET, ÜÇ AYRI ANLAM (yönetici yüzeyi) ------------------------------

`eslestirme._hedef_dogrula`nın AYIRT EDİLEMEZLİK sözleşmesi burada AYNEN
uygulanmaz ve bu ölçülmüş bir farktır: orada gizlenen şey KULLANICI
ENVANTERİDİR ve `users` izni olmayan biri o envanteri başka hiçbir yerden
göremez. Burada ise çağıran zaten `sales`/`purchases` iznine sahiptir, yani
cari listesini (`GET /api/customers`) ZATEN okuyabilir: "bu cari yok" ile
"bu cari pasif" arasındaki farkı gizlemek hiçbir şey saklamaz, yalnız hatayı
teşhis edilemez kılardı.

WHATSAPP tarafında ise sözleşme TERSİNE döner: `kod_kullan`ın BÜTÜN ret
yolları TEK bir cevap üretir (`eslestirme.RED_MESAJI` ile aynı sınıf).
Orada çağıran kimliksizdir ve her ayrım bir kâhindir.

--- PERSONEL NUMARASI ÇAKIŞMASI: 409, SESSİZ DEĞİL -----------------------

Aynı numara aynı firmada HEM personel HEM taraf bağlantısı taşıyamaz. Karar
ve gerekçesi: dağıtıcı (F10-1b) personel dalını ÖNCE deneyecek (keşif §5.1),
yani böyle bir numara taraf cevabını HİÇBİR ZAMAN almazdı. Sessizce açılan
bir bağlantı, "bağladım ama çalışmıyor" diye geri dönen bir arıza olurdu;
kod üretimi bu yüzden 409 `TARAF_NUMARA_PERSONEL` ile GÜRÜLTÜLÜ düşer.

Denetim `kod_kullan`da TEKRAR edilir: kod üretildikten sonra o numaraya bir
personel bağlantısı açılmış olabilir.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..auth import token_digest, utcnow
from ..core_schema import customers, suppliers
from ..notifications.consents import normalize_msisdn
from ..tenancy import companies
from . import schema
from .eslestirme import deneme_say, kod_bicimle, kod_kanonik, kod_uret_metin
from .schema import (
    whatsapp_links,
    whatsapp_party_links,
    whatsapp_party_pairing_codes,
)
from .telefon import normalize_phone

log = logging.getLogger("nazgul.whatsapp.taraf")

# --- RIZA BU DİLİMDE YAZILMAZ — ŞEF KARARI (2026-09-20) -------------------
#
# Bu modül `notification_consents`e HİÇBİR ŞEY yazmaz ve bu ölçülmüş bir
# karardır, bir eksiklik değil:
#
# * **Rızayı personel veremez.** Kodu üreten, cari kartındaki düğmeye basan
#   personeldir; eşleştirmenin başarısı yalnız "bu numara bu cariye ait"
#   demektir. Keşif §5.4'ün akışı rızayı ÇİFTÇİNİN İLK MESAJINA bağlıyor ve
#   o soru (KVKK metni + `EVET`/`HAYIR`) F10-1b'dedir.
# * **Defterde BEKLEYEN durumu YOK** — ölçüldü: `notification_consents.status`
#   CHECK'i `('GRANTED','REVOKED')` (göç `20260728_0033`). Yani "sorulacak"
#   diye yazılabilecek bir satır biçimi yoktur; satırı hiç açmamak DOĞRU
#   temsildir ve `evaluate_consent` onu tam da gereken biçimde okur:
#   `NO_RECORD` → fail-closed, ERP verisi DÖNMEZ.
# * `whatsapp_party_links.consent_at` bu modülde `NULL` açılır. Damgayı
#   YALNIZ F10-1b'nin `EVET` yolu yazar (`riza_damgasi_yaz`, çağıran
#   `ciftci_yurutucu.ciftci_cevap`): rıza satırı GRANTED olduğu an, O
#   bağlantıya. `HAYIR`/`DUR` damgayı SİLMEZ — son rızanın ne zaman
#   verildiği TARİHÇEDİR ve bağlantının hâlâ rıza taşıyıp taşımadığını
#   söylemez.
#
#   CONSENT_AT BİR İZDİR, KARAR DEĞİLDİR: hiçbir kod yolu onu bir izin
#   kararı için OKUMAZ; karar HER mesajda `consents.evaluate_consent`ten
#   gelir. Tek okuyucu yöneticinin bağlantı listesidir
#   (`routers/whatsapp.py`). Kapı
#   `test_CONSENT_AT_IZDIR_KARAR_DEGIL_tek_okuyucu_yonetici_listesi`.


class TarafHatasi(Exception):
    """Kod üretilemedi. `code` makine, `message` kullanıcı içindir.

    `durum` çağıranın (router) çevireceği HTTP kodudur ve burada durur çünkü
    "bu cari yok" (404) ile "bu numara zaten bağlı" (409) ayrımı bir YETKİ
    değil bir İŞ kuralıdır; router'a taşınsaydı iki yüzey (uç ve gelecekteki
    içe aktarma yolu) aynı kuralı iki kez yazardı.
    """

    def __init__(self, durum: int, code: str, message: str) -> None:
        super().__init__(message)
        self.durum = durum
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TarafKimlik:
    """Bir numaranın çözülebildiği TEK bir (firma, taraf) adayı.

    ``link_id`` F10-1b'de eklendi ve SONDA, VARSAYILANLI durur: rıza
    olaylarının denetim satırı `whatsapp_party_links.id`yi KAYNAK KİMLİĞİ
    olarak istiyor (keşif §5.5) ve o kimliği ÜRETEN sorgu zaten
    `taraf_coz`dur — ikinci bir okuma, aynı satırı iki kez sormak olurdu.
    ``0`` "bilinmiyor" demektir ve `log_activity`ye `None` olarak gider.
    """

    company_id: int
    party_type: str
    party_id: int
    link_id: int = 0


@dataclass(frozen=True, slots=True)
class UretilenTarafKodu:
    kod_id: int
    kod: str  # DÜZ kod — yalnız bu nesnede, yalnız bir kez
    expires_at: datetime
    hedef_telefon: str = ""


@dataclass(frozen=True, slots=True)
class TarafSonucu:
    basarili: bool
    link_id: int | None = None
    company_id: int | None = None
    party_type: str | None = None
    party_id: int | None = None
    cevapla: bool = True
    sinirlandi: bool = False


class _YarisKaybedildi(Exception):
    """CAS'i kaybeden taraf; SAVEPOINT'i geri almak için İÇ sinyal."""


#: `kod_kullan`ın TEK public red metni — `eslestirme.RED_MESAJI` ile AYNI
#: sınıf: geçersiz, süresi dolmuş, kullanılmış, yanlış numaradan gelen ve hiç
#: var olmamış kod AYNI cevabı alır.
RED_MESAJI = (
    "Eşleştirme kodu geçersiz, süresi dolmuş veya kullanılmış. "
    "Kodu veren alım merkezinden yeni kod isteyin."
)
BASARI_MESAJI = (
    "Numaranız kaydınıza bağlandı. Artık bakiye, avans, kantar ve makbuz "
    "bilgilerinizi bu numaradan sorabilirsiniz."
)


def _party_type_kanonik(ham: str) -> str:
    deger = (ham or "").strip().upper()
    if deger not in schema.TARAF_TIPLERI:
        raise TarafHatasi(422, "TARAF_TIPI_GECERSIZ", "Geçersiz taraf tipi.")
    return deger


def _taraf_dogrula(db: Session, company_id: int, party_type: str, party_id: int) -> str:
    """Cari AYNI FİRMADA var ve AKTİF mi? Adını döner.

    Kod ÜRETİLİRKEN ve KULLANILIRKEN AYRI AYRI çağrılır: arada cari
    pasifleşmiş olabilir. Yüklem `company_id`yi AÇIKÇA taşır — düşseydi bir
    firmanın personeli BAŞKA firmanın carisine kod üretebilirdi.

    İKİ DAL, TEK SORGU DEĞİL — ve bu bir tekrar değil ZORUNLULUK: tabloyu
    bir değişkene alıp (`tablo = customers if ... else suppliers`) tek sorgu
    yazmak ÖLÇÜLDÜ ve Core envanteri onu `unresolved-target` olarak REDDETTİ
    (`tests/test_core_query_inventory.py`). Nöbetçi haklı: hedefi görülemeyen
    bir ifade, kiracı yükleminin HANGİ tabloya uygulandığını da söyleyemez.
    """
    if party_type == schema.TARAF_CUSTOMER:
        satir = db.execute(
            select(customers.c.name, customers.c.is_active).where(
                customers.c.id == party_id, customers.c.company_id == company_id
            )
        ).first()
    else:
        satir = db.execute(
            select(suppliers.c.name, suppliers.c.is_active).where(
                suppliers.c.id == party_id, suppliers.c.company_id == company_id
            )
        ).first()
    if satir is None:
        raise TarafHatasi(404, "TARAF_BULUNAMADI", "Cari bulunamadı.")
    if not bool(satir.is_active):
        raise TarafHatasi(
            409, "TARAF_PASIF", "Pasif cari için eşleştirme kodu üretilemez."
        )
    return str(satir.name or "")


def _firma_aktif(db: Session, company_id: int) -> bool:
    return bool(
        db.execute(
            select(companies.c.is_active).where(companies.c.id == company_id)
        ).scalar_one_or_none()
    )


def _personel_baglantisi_var(db: Session, company_id: int, telefon: str) -> bool:
    """Bu numara AYNI FİRMADA aktif bir PERSONEL bağlantısı taşıyor mu?

    Taşıyorsa taraf bağlantısı açılmaz (başlık): dağıtıcı personel dalını
    önce denediği için taraf cevabı hiçbir zaman üretilmezdi.
    """
    return (
        db.execute(
            select(whatsapp_links.c.id).where(
                whatsapp_links.c.company_id == company_id,
                whatsapp_links.c.phone == telefon,
                whatsapp_links.c.is_active.is_(True),
            )
        ).first()
        is not None
    )


def _aktif_baglanti_var(db: Session, company_id: int, telefon: str) -> bool:
    return (
        db.execute(
            select(whatsapp_party_links.c.id).where(
                whatsapp_party_links.c.company_id == company_id,
                whatsapp_party_links.c.phone == telefon,
                whatsapp_party_links.c.is_active.is_(True),
            )
        ).first()
        is not None
    )


def bekleyenleri_iptal_et(
    db: Session,
    company_id: int,
    party_type: str,
    party_id: int,
    *,
    simdi: datetime | None = None,
) -> int:
    """Taraf için bekleyen kodları `CANCELLED` yapar; sayıyı döner.

    Yeni kod üretmeden ÖNCE çağrılır: "hangi kod geçerli" sorusunun her an
    tek cevabı olsun. Hakem yine de `uq_wppc_aktif_kod` kısmi tekilidir.
    """
    sonuc = db.execute(
        update(whatsapp_party_pairing_codes)
        .where(
            whatsapp_party_pairing_codes.c.company_id == company_id,
            whatsapp_party_pairing_codes.c.party_type == party_type,
            whatsapp_party_pairing_codes.c.party_id == party_id,
            whatsapp_party_pairing_codes.c.status == schema.PAIRING_PENDING,
        )
        .values(status=schema.PAIRING_CANCELLED, cancelled_at=simdi or utcnow())
    )
    return int(sonuc.rowcount or 0)


def kod_uret(
    db: Session,
    company_id: int,
    party_type: str,
    party_id: int,
    *,
    hedef_telefon: str,
    created_by: int | None = None,
    simdi: datetime | None = None,
) -> UretilenTarafKodu:
    """Tek kullanımlık kod üretir. DÜZ kod YALNIZ dönüş değerinde yaşar.

    Commit ETMEZ: çağıran (router) aktivite kaydıyla birlikte TEK
    transaction'da commit eder.

    SIRA SÖZLEŞMEDİR: önce taraf doğrulanır (404/409), sonra numara
    doğrulanır (422), sonra numara ÇAKIŞMALARI (409 personel / 409 bağlı),
    en son kod yazılır. Numara denetimleri taraf denetiminden SONRADIR ki
    var olmayan bir cari için bile numara envanteri sorulmasın.
    """
    an = simdi or utcnow()
    taraf = _party_type_kanonik(party_type)
    if party_id <= 0:
        raise TarafHatasi(404, "TARAF_BULUNAMADI", "Cari bulunamadı.")
    _taraf_dogrula(db, company_id, taraf, party_id)

    # SIKI doğrulama (K2): rıza defterinin kabul ettiği numara. `e164`
    # YETMEZ — keşif §2.2'de ölçüldü: onun kabul ettiği 13 haneli numaraları
    # rıza defteri REDDEDİYOR ve rıza yazılamayan bağlantı ÖLÜ bağlantıdır.
    if normalize_msisdn(hedef_telefon) is None:
        raise TarafHatasi(
            422,
            "TELEFON_GECERSIZ",
            "Geçerli bir TR cep telefonu numarası girin (örn. 0532 111 22 33).",
        )
    hedef = normalize_phone(hedef_telefon)

    if _personel_baglantisi_var(db, company_id, hedef):
        raise TarafHatasi(
            409,
            "TARAF_NUMARA_PERSONEL",
            "Bu numara bu firmada bir personel hesabına bağlı; önce o "
            "bağlantı kapatılmalı.",
        )
    if _aktif_baglanti_var(db, company_id, hedef):
        raise TarafHatasi(
            409, "TARAF_NUMARA_BAGLI", "Bu numara bu firmada zaten bir cariye bağlı."
        )

    bekleyenleri_iptal_et(db, company_id, taraf, party_id, simdi=an)

    expires_at = an + timedelta(minutes=schema.PAIRING_OMRU_DAKIKA)
    # Özet çakışması pratikte imkânsız (60 bit) ama UNIQUE kısıt GERÇEK.
    for _ in range(5):
        kod = kod_uret_metin()
        try:
            with db.begin_nested():
                sonuc = db.execute(
                    insert(whatsapp_party_pairing_codes).values(
                        company_id=company_id,
                        party_type=taraf,
                        party_id=party_id,
                        target_phone=hedef,
                        created_by=created_by,
                        code_digest=token_digest(kod),
                        status=schema.PAIRING_PENDING,
                        expires_at=expires_at,
                        attempt_count=0,
                        max_attempts=schema.PAIRING_MAX_ATTEMPTS,
                        created_at=an,
                    )
                )
        except IntegrityError:
            continue
        return UretilenTarafKodu(
            kod_id=int(sonuc.inserted_primary_key[0]),
            kod=kod,
            expires_at=expires_at,
            hedef_telefon=hedef,
        )
    raise TarafHatasi(500, "KOD_URETILEMEDI", "Kod üretilemedi; lütfen tekrar deneyin.")


def kod_iptal(
    db: Session, company_id: int, kod_id: int, *, simdi: datetime | None = None
) -> bool:
    """Bekleyen kodu iptal eder. TENANT KAPSAMLI (yüklem bir süs DEĞİL)."""
    sonuc = db.execute(
        update(whatsapp_party_pairing_codes)
        .where(
            whatsapp_party_pairing_codes.c.id == kod_id,
            whatsapp_party_pairing_codes.c.company_id == company_id,
            whatsapp_party_pairing_codes.c.status == schema.PAIRING_PENDING,
        )
        .values(status=schema.PAIRING_CANCELLED, cancelled_at=simdi or utcnow())
    )
    return int(sonuc.rowcount or 0) == 1


def baglantiyi_kapat(
    db: Session, company_id: int, baglanti_id: int, *, simdi: datetime | None = None
) -> bool:
    """Bağlantıyı PASİFLEŞTİRİR, SİLMEZ: kim ne zaman bağlıydı izi kalır.

    Pasifleştirme `uq_whatsapp_party_links_aktif_numara` kısmi tekilinin
    KAPSAMINDAN çıkmaktır, yani aynı numara yeniden bağlanabilir hâle gelir.

    RIZAYA DOKUNMAZ ve bu bilinçli: bağlantı "bu numara kim" sorusunu, rıza
    "mesaj gönderebilir miyiz" sorusunu cevaplar. Çiftçinin `DUR` demesi
    İKİSİNİ BİRDEN kapatır (keşif §5.4) ama o yol F10-1b'dedir; buradaki
    yönetici işlemi yalnız bağlantıyı kapatır — rızayı yönetici adına geri
    çekmek, çiftçinin vermediği bir kararı onun defterine yazmak olurdu.
    """
    sonuc = db.execute(
        update(whatsapp_party_links)
        .where(
            whatsapp_party_links.c.id == baglanti_id,
            whatsapp_party_links.c.company_id == company_id,
            whatsapp_party_links.c.is_active.is_(True),
        )
        .values(is_active=False, updated_at=simdi or utcnow())
    )
    return int(sonuc.rowcount or 0) == 1


def riza_damgasi_yaz(
    db: Session, company_id: int, baglanti_id: int, *, simdi: datetime | None = None
) -> None:
    """`consent_at`i ŞİMDİYE çeker — İZ, karar değil (modül başı). Commit ETMEZ.

    KİRACI YÜKLEMİ TAŞIR: yazmanın hangi firmanın defterine dokunduğu
    sorgunun kendisinde yazılı (`baglantiyi_kapat`ın kuralı).
    """
    an = simdi or utcnow()
    db.execute(
        update(whatsapp_party_links)
        .where(
            whatsapp_party_links.c.id == baglanti_id,
            whatsapp_party_links.c.company_id == company_id,
        )
        .values(consent_at=an, updated_at=an)
    )


def _basarisiz(cevapla: bool, *, sinirlandi: bool = False) -> TarafSonucu:
    return TarafSonucu(basarili=False, cevapla=cevapla, sinirlandi=sinirlandi)


def _denemeyi_artir(db: Session, company_id: int, kod_id: int) -> None:
    """Bulunan kodun deneme sayacı (AYRI deyim, geri alınmaz).

    KİRACI YÜKLEMİ TAŞIR — `eslestirme._denemeyi_artir` ile AYNI gerekçe:
    yazmanın hangi firmanın defterine dokunduğu SORGUNUN KENDİSİNDE yazılı.
    """
    db.execute(
        update(whatsapp_party_pairing_codes)
        .where(
            whatsapp_party_pairing_codes.c.id == kod_id,
            whatsapp_party_pairing_codes.c.company_id == company_id,
        )
        .values(attempt_count=whatsapp_party_pairing_codes.c.attempt_count + 1)
    )


def kod_kullan(
    db: Session,
    telefon: str,
    ham_kod: str,
    *,
    simdi: datetime | None = None,
    deneme: int | None = None,
) -> TarafSonucu:
    """`BAĞLA <KOD>` mesajını TARAF defterine karşı işler. Commit ETMEZ.

    `eslestirme.kod_kullan` ile AYNI sözleşmeler: tek transaction, CAS'li
    tüketim, hedef numara bağı (SEC-1), ayırt edilemez ret, hız sınırı
    ısırınca kod TÜKETİLMEZ ama satırın sayacı YANAR.

    RIZA YAZILMAZ ve bu bilinçli (modül başlığı, Şef kararı): başarı yalnız
    "bu numara bu cariye ait" demektir. KVKK rızasını çiftçinin İLK MESAJI
    verir (F10-1b) ve o an gelene kadar `evaluate_consent` `NO_RECORD`
    döndürerek fail-closed davranır — yani bağlanmış ama rıza vermemiş bir
    numara ERP verisi ALAMAZ.

    ``deneme`` F10-1b'de eklendi ve SAYACIN İKİ KEZ YANMASINI ÖNLER.
    Ölçülen tuzak: dağıtıcı `BAĞLA <KOD>` için önce personel defterini
    (`eslestirme.kod_kullan`), sonra taraf defterini dener; İKİSİ DE adım
    1'de `deneme_say`i çağırıyordu, yani TEK bir gelen mesaj penceredeki
    sayacı İKİ artırırdı ve personelin 5'lik sınırı 3 yanlış denemede
    ısırırdı. ``deneme`` verildiğinde sayaç BURADA artırılmaz; çağıran onu
    BİR KEZ artırmış ve değeri geçmiştir. ``None`` (varsayılan) bugünkü
    davranışın ta kendisidir — doğrudan çağıran testler DEĞİŞMEZ.
    Kapı: `test_TEK_BAGLA_SAYACI_BIR_ARTIRIR`.
    """
    an = simdi or utcnow()
    normal = normalize_phone(telefon)
    if not normal:
        return _basarisiz(False)

    # 1) KALICI hız sınırı — kod BULUNMADAN ÖNCE (personel yoluyla AYNI
    #    sayaç, başlık). SAVEPOINT'ten önce GERÇEK bir yazma olması ayrıca
    #    pysqlite tuzağını kapatır (`eslestirme.kod_kullan` adım 6).
    #    ÇAĞIRAN SAYMIŞSA YENİDEN SAYILMAZ (`deneme`, başlık).
    sayi = deneme_say(db, normal, simdi=an) if deneme is None else deneme
    sinirda = sayi > schema.PAIRING_PENCERE_SINIRI
    cevapla = (not sinirda) and sayi <= schema.PAIRING_CEVAP_SINIRI

    kod = kod_kanonik(ham_kod)
    if kod is None:
        return _basarisiz(cevapla, sinirlandi=sinirda)
    beklenen_ozet = token_digest(kod)

    sorgu = select(
        whatsapp_party_pairing_codes.c.id,
        whatsapp_party_pairing_codes.c.company_id,
        whatsapp_party_pairing_codes.c.party_type,
        whatsapp_party_pairing_codes.c.party_id,
        whatsapp_party_pairing_codes.c.target_phone,
        # H78: bağlantı satırının `created_by`si BURADAN gelir (aşağıda).
        whatsapp_party_pairing_codes.c.created_by,
        whatsapp_party_pairing_codes.c.code_digest,
        whatsapp_party_pairing_codes.c.status,
        whatsapp_party_pairing_codes.c.expires_at,
        whatsapp_party_pairing_codes.c.attempt_count,
        whatsapp_party_pairing_codes.c.max_attempts,
    ).where(whatsapp_party_pairing_codes.c.code_digest == beklenen_ozet)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        sorgu = sorgu.with_for_update()
    satir = db.execute(sorgu).mappings().first()
    if satir is None:
        return _basarisiz(cevapla, sinirlandi=sinirda)

    # ÖZET SABİT SÜREDE DOĞRULANIR — `eslestirme.kod_kullan`ın 3b adımıyla
    # AYNI gerekçe: `WHERE code_digest = :ozet` bir SUNUCU karşılaştırmasıdır
    # ve nasıl yapıldığı bu kodun kontrolünde değildir. Kapı AST'dedir
    # (`tests/test_f10_1a_taraf_baglantisi.py`), çünkü `==`'e dönen mutant
    # hiçbir DAVRANIŞ testini kırmaz; kaybolan şey ZAMANLAMADIR.
    if not hmac.compare_digest(str(satir["code_digest"]), beklenen_ozet):
        return _basarisiz(cevapla, sinirlandi=sinirda)

    company_id = int(satir["company_id"])
    if sinirda:
        log.warning("whatsapp: taraf eslestirme siniri asildi, kod tuketilmedi")
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(False, sinirlandi=True)

    if int(satir["attempt_count"]) >= int(satir["max_attempts"]):
        return _basarisiz(cevapla)
    if satir["status"] != schema.PAIRING_PENDING:
        return _basarisiz(cevapla)

    son = satir["expires_at"]
    if son is not None and son.tzinfo is None:
        son = son.replace(tzinfo=timezone.utc)
    if son is not None and son <= an:
        return _basarisiz(cevapla)

    # KOD, VERİLDİĞİ NUMARADAN GELMEK ZORUNDA (SEC-1). Sıra SÖZLEŞMEDİR:
    # denetim bağlantı INSERT'inden ÖNCEDİR.
    if str(satir["target_phone"]) != normal:
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)

    party_type = str(satir["party_type"])
    party_id = int(satir["party_id"])

    # Zincir BAŞTAN doğrulanır: kod üretildikten sonra firma kapanmış, cari
    # pasifleşmiş ya da numaraya bir PERSONEL bağlantısı açılmış olabilir.
    if not _firma_aktif(db, company_id):
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)
    try:
        _taraf_dogrula(db, company_id, party_type, party_id)
    except TarafHatasi:
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)
    if _personel_baglantisi_var(db, company_id, normal):
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)

    try:
        with db.begin_nested():
            sonuc = db.execute(
                insert(whatsapp_party_links).values(
                    company_id=company_id,
                    party_type=party_type,
                    party_id=party_id,
                    phone=normal,
                    is_active=True,
                    # RIZA DAMGASI YAZILMAZ (modül başlığı): rızayı çiftçinin
                    # ilk mesajı verir, eşleştirme DEĞİL. NULL, "henüz rıza
                    # yok"un kendisidir ve `evaluate_consent` zaten
                    # `NO_RECORD` ile fail-closed davranır.
                    consent_at=None,
                    created_at=an,
                    updated_at=an,
                    # H78 (#154 mercek bulgusu): sütunun yorumu "Kodu üreten
                    # personel" diyor ama buraya `None` yazılıyordu, yani
                    # yorum ile veri AYRIŞIYORDU ve bağlantıyı hangi
                    # personelin açtırdığı denetimde GÖRÜNMÜYORDU. Değer
                    # TÜKETİLEN KOD SATIRINDAN taşınır — kodu üreten uç onu
                    # zaten yazmıştı. `NULL` KALABİLİR (kod satırının kendi
                    # `created_by`si nullable'dır ve kullanıcı silinince
                    # SET NULL olur); taşınan şey bir iddia değil, var olan
                    # izin KENDİSİDİR.
                    created_by=satir["created_by"],
                )
            )
            link_id = int(sonuc.inserted_primary_key[0])

            # CAS: YALNIZ hâlâ PENDING olan kod tüketilebilir. İki işçi
            # yarışırsa `rowcount` TAM OLARAK BİRİNDE 1 olur; kaybeden
            # tarafın bağlantı INSERT'i de geri alınır.
            cas = db.execute(
                update(whatsapp_party_pairing_codes)
                .where(
                    whatsapp_party_pairing_codes.c.id == int(satir["id"]),
                    whatsapp_party_pairing_codes.c.company_id == company_id,
                    whatsapp_party_pairing_codes.c.status == schema.PAIRING_PENDING,
                )
                .values(
                    status=schema.PAIRING_CONSUMED,
                    consumed_at=an,
                    consumed_link_id=link_id,
                )
            )
            if int(cas.rowcount or 0) != 1:
                raise _YarisKaybedildi()
    except _YarisKaybedildi:
        return _basarisiz(cevapla)
    except IntegrityError:
        # Numara BU FİRMADA zaten aktif bir taraf bağlantısına sahip
        # (`uq_whatsapp_party_links_aktif_numara`). Cevap ayırt EDİLEMEZ.
        _denemeyi_artir(db, company_id, int(satir["id"]))
        return _basarisiz(cevapla)

    # Denetim izi. AKTÖR `NULL` ve bu ölçülmüş bir karar (keşif §5.5):
    # `log_activity` bir `app_users.id` bekler, çiftçinin böyle bir kimliği
    # YOKTUR ve `activity_logs.user_id` geri yüklemede zaten YUMUŞAK
    # referanstır (`kiraci_geri_yukleme.KULLANICI_SUTUNLARI`), `NULL` onu
    # bozmaz. Olay bir FİRMAYA aittir, platform defterine YAZILMAZ.
    # YÜK HASSAS DEĞİL: kod, özet ve ham telefon GİRMEZ.
    log_activity(
        db,
        company_id,
        None,
        "party.whatsapp_link_activated",
        "whatsapp_party",
        link_id,
        "WhatsApp cari bağlantısı açıldı",
        {"party_type": party_type, "party_id": party_id},
    )

    log.info("whatsapp: taraf numarasi eslestirildi firma=%s", company_id)
    return TarafSonucu(
        basarili=True,
        link_id=link_id,
        company_id=company_id,
        party_type=party_type,
        party_id=party_id,
        cevapla=True,
    )


def taraf_coz(db: Session, telefon: str) -> list[TarafKimlik]:
    """Numaranın çözülebildiği (firma, taraf) ADAYLARINI döner.

    Boş liste = numara hiçbir tarafa bağlı değil. Birden çok eleman =
    belirsizlik ve ÇAĞIRAN RASTGELE BİRİNİ SEÇEMEZ; sorulur (F10-1b'nin
    `FİRMA SEÇ` yolu). `eslestirme.kimlik_coz`un cümlesi burada da geçerli:
    belirsizlikte rastgele seçim, YANLIŞ tenant'ın verisini dönmek demektir.

    Tarama KİRACI YÜKLEMİ TAŞIMAZ ve TAŞIYAMAZ: sorduğu şeyin kendisi "bu
    numara HANGİ firmalarda aktif". Nöbetçide ayrı bir istisna kaydı vardır
    (`tests/test_core_tenant_scoping_guard.py::CEKIRDEK_KIRACI_ISTISNALARI`).

    İKİNCİ ADIM bir tekrar DEĞİL, kapının kendisi: her aday için firma ve
    cari zinciri (aktif firma → aktif cari) KİRACI YÜKLEMLİ olarak yeniden
    kurulur. Cari pasifleştiğinde bağlantı satırı defterde KALIR; o satır tek
    başına yetki iddiası olsaydı, artık çalışılmayan bir çiftçi WhatsApp'tan
    veri okumaya devam ederdi.

    Sıra DETERMİNİSTİK (`company_id`, sonra `id`).
    """
    normal = normalize_phone(telefon)
    if not normal:
        return []
    satirlar = db.execute(
        select(
            whatsapp_party_links.c.company_id,
            whatsapp_party_links.c.party_type,
            whatsapp_party_links.c.party_id,
            # F10-1b: rıza olaylarının KAYNAK KİMLİĞİ (`TarafKimlik` başlığı).
            whatsapp_party_links.c.id,
        )
        .where(
            whatsapp_party_links.c.phone == normal,
            whatsapp_party_links.c.is_active.is_(True),
        )
        .order_by(whatsapp_party_links.c.company_id, whatsapp_party_links.c.id)
    ).all()

    adaylar: list[TarafKimlik] = []
    for satir in satirlar:
        company_id = int(satir[0])
        party_type = str(satir[1])
        party_id = int(satir[2])
        if not _firma_aktif(db, company_id):
            continue
        try:
            _taraf_dogrula(db, company_id, party_type, party_id)
        except TarafHatasi:
            continue
        adaylar.append(
            TarafKimlik(
                company_id=company_id,
                party_type=party_type,
                party_id=party_id,
                link_id=int(satir[3]),
            )
        )
    return adaylar


__all__ = [
    "BASARI_MESAJI",
    "RED_MESAJI",
    "TarafHatasi",
    "TarafKimlik",
    "TarafSonucu",
    "UretilenTarafKodu",
    "baglantiyi_kapat",
    "bekleyenleri_iptal_et",
    "kod_bicimle",
    "kod_iptal",
    "kod_kullan",
    "kod_uret",
    "riza_damgasi_yaz",
    "taraf_coz",
]
