"""WhatsApp gelen kuyruğunun İŞÇİSİ: kirala → cevapla → damgala (WA3-full).

Kaynak `nazgul_website/backend/app/whatsapp/service.py`
(`bekleyenleri_isle`, `_claim`, `_sonlandir`, `_mesaj_isle`,
`takilanlari_kapat`). Kiralama/CAS deseni BİREBİR taşındı; AYRILAN ÜÇ YER
aşağıda ADIYLA yazılı ve üçü de ölçülmüş bir gerekçeye dayanıyor.

--- KAYNAKTAN AYRILAN BİRİNCİ YER: SATIRA KİRACI YAZILMIYOR --------------

Kaynağın `_sonlandir`ı terminal durumla birlikte `company_id`/`user_id` de
yazıyordu, çünkü kaynağın `whatsapp_inbound`u o sütunları TAŞIYOR. Bu
depoda TAŞIMIYOR ve gerekçe göç `20260910_0078`in başlığındadır: webhook'a
gelen mesaj henüz hiçbir firmaya ait değildir ve `company_id` taşıyan her
tablo `TENANT_TABLES`a girip her sorgusundan `company_id=:cid` yüklemi
ister.

Bu dilim o kararı DEĞİŞTİRMİYOR ve bir göç AÇMIYOR. Sonuç açıkça
kabullenilmiş bir bedeldir: **kuyruk satırı hangi firmaya cevap
verildiğini SÖYLEMEZ.** Satırda duran tek iz `status` ve `last_error`dır.
Çözümün kendisi (`whatsapp_links` + `whatsapp_context`) zaten kalıcıdır ve
"bu numara o an hangi firmadaydı" sorusu oradan cevaplanır.

--- KAYNAKTAN AYRILAN İKİNCİ YER: HIZ SINIRI SAYACI AYRI İŞLEMDE ---------

ÖLÇÜLMÜŞ KUSUR (mercek bulgusu). `eslestirme.deneme_say` ÇAĞIRANIN
transaction'ında yazar. Kaynağın `_bagla_akisi`ı gönderim hatasında
`db.rollback()` çağırıyor ve o rollback SAYACI DA GERİ ALIYOR: düşen bir
sağlayıcıyla, sınırsız sayıda `BAĞLA` denemesi hız sınırına HİÇ takılmadan
tekrarlanabilirdi — yani sınır tam da saldırının işe yaradığı koşulda
kayboluyordu.

Bu depoda sayaç, dıştaki transaction geri alındığında AYRI VE KISA bir
işlemde yeniden kalıcı kılınıyor (:func:`_sayaci_kalicilastir`). Net etki
HER ZAMAN tek artıştır: rollback içerideki artışı siler, ayrı oturum
birini geri koyar. Kapı:
`tests/test_wa3_worker.py::test_HIZ_SINIRI_SAYACI_DIS_ROLLBACKI_ASIYOR`.

--- KAYNAKTAN AYRILAN ÜÇÜNCÜ YER: DANIŞMAN DEĞİL KÖPRÜ, VE YAZMA YOK -----

Bağsız numaraya kaynak kendi `danisman`ını soruyordu; burada aynı yeri
WA3-core'un `kopru.KopruIstemcisi`si tutuyor ve KAPALIYKEN (varsayılan)
kullanıcı `BAĞLA <KOD>` rehberliği alır — sessizlik DEĞİL, çünkü bu depoda
eşleştirme kodu diye somut bir çıkış yolu VAR.

Tahsilat (yazma) niyeti bu turda ÇALIŞTIRILMAZ: taslak/onay defteri
(`whatsapp_pending_actions`) bu depoda henüz YOK. Kullanıcıya "yakında"
denir (:data:`WA4_MESAJI`), sessizce dönem özeti DÖNÜLMEZ.

--- EŞZAMANLILIK ---------------------------------------------------------

`whatsapp_inbound` satırı kuyruğun KENDİSİDİR. Claim tek UPDATE ile
yapılır ve COMMIT edilir; dış çağrı (sağlayıcı) sırasında veritabanı
kilidi TUTULMAZ. Aynı mesajı iki işçi aynı anda işleyemez; süresi dolmuş
kira devralınabilir.

Cevap gönderimi ile ANSWERED damgası arasında çökme TEK bir yinelenen
cevaba yol açabilir — bilinçli tercih: tersi (önce damga, sonra gönderim)
cevabın HİÇ gitmemesine yol açardı.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..config import settings
from . import (
    baglam,
    eslestirme,
    fatura,
    kopru,
    niyet,
    saglayici as saglayici_modulu,
    schema,
)
from .schema import whatsapp_inbound
from .telefon import TelefonGecersiz, e164, normalize_phone

log = logging.getLogger("nazgul.whatsapp.service")

#: Tek mesajda işlenecek soru uzunluğu. `kopru.SORU_MAKS` (500) ile AYNI
#: olmak ZORUNDA DEĞİL ama daha büyük OLMAMALI: köprüye giden metin orada
#: zaten kırpılır, burada kırpmak niyet çözücüsünü de aynı sınıra sokar.
SORU_MAKS = 500

#: Bağlı OLMAYAN numaraya, köprü KAPALIYKEN verilen cevap. Sessizlik
#: DEĞİL: bu depoda somut bir çıkış yolu var (`baglam` WA2'de açtı) ve
#: kullanıcıya onu söylememek, çalışan bir eşleştirmeyi gizlemek olurdu.
#: Metin `baglam.TANINMAYAN_NUMARA_MESAJI` ile AYNI — iki yüzey aynı şeyi
#: iki farklı cümleyle söylememeli.
BAGSIZ_MESAJI = baglam.TANINMAYAN_NUMARA_MESAJI

#: Yazma (tahsilat) niyetinin bu turdaki cevabı. Taslak/onay defteri
#: (`whatsapp_pending_actions`) bu depoda YOK; onsuz bir tahsilat, ONAY
#: ADIMI OLMADAN para kaydı demekti.
WA4_MESAJI = (
    "Tahsilat girişi bu kanalda henüz açık değil (WA4'te geliyor). "
    "Şimdilik web panelinden girebilirsiniz; borç, stok ve özet "
    "sorularınızı buradan sorabilirsiniz."
)

#: Çok eşleşmede kullanıcıya gösterilen liste. Kaynakta bu listeye bir
#: SEÇİM BAĞLAMI eşlik ediyordu ("2" yazınca ikincisi seçilir); bu depoda
#: bağlam satırı YALNIZ aktif firmayı tutuyor (`baglam.AKTIF_FIRMA`) ve
#: ikinci bir anlam yüklemek WA2'nin sözleşmesini bozardı. Kullanıcıdan
#: TAM AD isteniyor; sınır `docs/whatsapp/WA3_BILINEN_SINIRLAR.md`de.
COK_ESLESME_BASLIGI = "Birden fazla kayıt buldum:"
COK_ESLESME_KUYRUGU = "Hangisi? Adını tam yazarak tekrar sorun."


def _simdi() -> datetime:
    return utcnow()


# ---------------------------------------------------------------------------
# Kiralama (lease) ve CAS
# ---------------------------------------------------------------------------


def _claim(db: Session, satir_id: int, *, simdi: datetime | None = None) -> str | None:
    """Satırı TEK UPDATE ile kiralar; kazanan `lock_token`ını alır.

    Yüklem üç şeyi birden söyler ve ÜÇÜ DE gerekli:

    * ``attempt_count < MAX_DENEME`` — tavana ulaşmış satır bir daha
      kiralanamaz (`takilanlari_kapat` onu DEAD yapar);
    * ``status = RECEIVED`` **ya da** süresi DOLMUŞ ``PROCESSING`` —
      ikincisi olmadan çöken bir işçinin satırı sonsuza dek kilitli
      kalırdı;
    * ``rowcount == 1`` — kazanan TEK taraftır. Yirmi işçi aynı satıra
      girerse on dokuzu ``None`` alır ve satıra HİÇ dokunmaz.

    MUTASYON ADIYLA: `lock_token`ı yazmamak `_sonlandir`ın CAS'ini
    işlevsiz kılar (her jeton eşleşirdi) ve kaybeden işçi kazananın
    satırını damgalayabilirdi — kapı
    `test_CLAIM_LOCK_TOKEN_YAZMADAN_OLMAZ`.
    """
    an = simdi or _simdi()
    jeton = uuid4().hex
    sonuc = db.execute(
        update(whatsapp_inbound)
        .where(
            whatsapp_inbound.c.id == satir_id,
            whatsapp_inbound.c.attempt_count < schema.MAX_DENEME,
            or_(
                whatsapp_inbound.c.status == schema.RECEIVED,
                and_(
                    whatsapp_inbound.c.status == schema.PROCESSING,
                    whatsapp_inbound.c.locked_until <= an,
                ),
            ),
        )
        .values(
            status=schema.PROCESSING,
            lock_token=jeton,
            locked_until=an + timedelta(minutes=schema.LEASE_DAKIKA),
            attempt_count=whatsapp_inbound.c.attempt_count + 1,
        )
    )
    db.commit()
    return jeton if sonuc.rowcount == 1 else None


def _sonlandir(db: Session, satir_id: int, jeton: str, **degerler: Any) -> bool:
    """YALNIZ kira sahibi durum yazabilir (CAS). Kira alanları temizlenir.

    `company_id`/`user_id` BU DEPODA YAZILMAZ; gerekçe modül başlığında.
    """
    sonuc = db.execute(
        update(whatsapp_inbound)
        .where(
            whatsapp_inbound.c.id == satir_id,
            whatsapp_inbound.c.lock_token == jeton,
        )
        .values(locked_until=None, lock_token=None, **degerler)
    )
    db.commit()
    return sonuc.rowcount == 1


def takilanlari_kapat(db: Session, *, simdi: datetime | None = None) -> int:
    """Deneme hakkı biten RECEIVED satırları DEAD'e çeker.

    Kaynakta durumun adı `FAILED`di; bu depoda `DEAD` çünkü bildirim
    outbox'ı `FAILED`i YENİDEN DENENEBİLİR anlamında kullanıyor
    (`schema.py` başlığı) ve iki kuyruğun aynı sözcüğü ters anlamda
    kullanması, süpürücüyü yazanın yapabileceği en sessiz hatadır.
    """
    sonuc = db.execute(
        update(whatsapp_inbound)
        .where(
            whatsapp_inbound.c.status == schema.RECEIVED,
            whatsapp_inbound.c.attempt_count >= schema.MAX_DENEME,
        )
        .values(status=schema.DEAD, processed_at=simdi or _simdi())
    )
    db.commit()
    return int(sonuc.rowcount or 0)


def _gecici_hata(db: Session, satir_id: int, jeton: str, hata: Exception) -> None:
    """Geçici hata: kira BIRAKILIR, satır yeniden denenebilir kalır.

    `attempt_count` claim'de arttı; tavana ulaşan satır bir daha claim
    edilemez ve `takilanlari_kapat` onu DEAD yapar. Yani "sonsuza dek
    dene" YOKTUR.
    """
    _sonlandir(
        db,
        satir_id,
        jeton,
        status=schema.RECEIVED,
        last_error=type(hata).__name__,
    )


def _sayaci_kalicilastir(
    oturum_fabrikasi: Callable[[], Session] | None,
    telefon: str,
    an: datetime,
) -> None:
    """Hız sınırı sayacını AYRI ve KISA bir işlemde geri koyar.

    ÇAĞRILDIĞI TEK YER: dıştaki transaction'ın GERİ ALINDIĞI dallar. O
    rollback `kod_kullan` içindeki `deneme_say` artışını da siler
    (mercek bulgusu; modül başlığı). Burada AYRI bir oturumda bir artış
    daha yazılır ve COMMIT edilir, yani net etki yine TEK artıştır.

    HATASI YUTULUR: sayaç bir gözlem/koruma aracıdır, teslimat yolu
    DEĞİL. Yazılamaması işçiyi düşürmemeli — `field_stok_zamanlayici`
    kalp atışıyla AYNI sınıftan bir karar.
    """
    if oturum_fabrikasi is None:
        # Oturum fabrikası verilmemişse ayrı işlem AÇILAMAZ. Sessiz
        # geçilmez: sayacın kaybı ölçülebilir kalmalı.
        log.warning(
            "whatsapp: oturum fabrikasi yok, hiz siniri sayaci AYRI islemde "
            "kalicilastirilamadi (%s)", saglayici_modulu.maskele(telefon)
        )
        return
    try:
        with oturum_fabrikasi() as sayac_db:
            eslestirme.deneme_say(sayac_db, telefon, simdi=an)
            sayac_db.commit()
    except Exception:  # noqa: BLE001 - koruma aracı ARIZA KAYNAĞI OLAMAZ
        log.exception("whatsapp: hiz siniri sayaci AYRI islemde yazilamadi")


# ---------------------------------------------------------------------------
# Soru → cevap (kimlik ÇÖZÜLMÜŞ)
# ---------------------------------------------------------------------------


def cevap_uret(db: Session, kimlik: eslestirme.Kimlik, metin: str) -> str:
    """Bağlı bir kullanıcının mesajını cevaba çevirir. GÖNDERMEZ.

    MUTLAK SÖZLEŞME: bu akışta DIŞ MODEL ÇAĞRISI YOKTUR. Niyet seçimi,
    terim çıkarımı ve cevap cümlesi tamamen `niyet` modülünde,
    deterministik koşar. Köprü YALNIZ bağsız numara dalındadır ve orada
    ERP verisi zaten YOKTUR.
    """
    from .yurutucu import VeritabaniYurutucu

    soru = (metin or "")[:SORU_MAKS]

    # 1) YAZMA niyeti okuma niyetlerinden ÖNCE denenir. Sıra kaynakla aynı
    #    ve gerekçesi aynı: "500 TL nakit tahsilat" mesajı bir okuma
    #    niyetine benzeyebilir ve dönem özetiyle cevaplanırsa kullanıcı
    #    yazma isteğinin yutulduğunu HİÇ öğrenmez.
    yazma = niyet.tahsilat_coz(soru)
    if isinstance(yazma, niyet.TahsilatNiyeti):
        return WA4_MESAJI
    if yazma is not None:
        # Rehberlik/red mesajı (yöntem eksik, geçmiş tarih, çıplak
        # "tahsilat"): kullanıcıyı doğru kalıba götürür ve o kalıp bu
        # turda yine WA4 cevabına çıkar — ama sebep FARKLIDIR ve
        # kullanıcının okuması gereken sebep budur.
        return yazma.mesaj or niyet.KAPSAM_MESAJI

    # 2) Deterministik OKUMA niyeti.
    sonuc = niyet.coz(soru)
    if sonuc.mesaj is not None:
        return sonuc.mesaj

    try:
        veri = niyet.dene(sonuc, VeritabaniYurutucu(db, kimlik))
    except KeyError:
        # Beyaz liste dışı araç. Niyet çözücüsü bunu üretemez; üretirse
        # kullanıcıya iç hata değil kapsam mesajı gider.
        log.warning("whatsapp: beyaz liste disi arac istendi")
        return niyet.KAPSAM_MESAJI
    except ValueError as hata:
        return str(hata)

    adaylar = veri.get("adaylar")
    if isinstance(adaylar, list) and len(adaylar) > 1:
        liste = "\n".join(
            f"{i}) {ad}" for i, ad in enumerate(adaylar[:5], start=1)
        )
        return f"{COK_ESLESME_BASLIGI}\n{liste}\n{COK_ESLESME_KUYRUGU}"

    return niyet.cevap_yaz(sonuc.arac or "", veri)


def _bagsiz_cevap(telefon: str, metin: str) -> str:
    """Bağlı OLMAYAN numaraya verilecek metin. ERP verisi ASLA yok.

    Köprü AÇIKSA soru Harman'a iletilir ve dönen metin kullanılır; köprü
    KAPALIYSA (varsayılan) ya da cevap üretilemezse eşleştirme rehberliği
    döner. İki dalda da kimlik YOKTUR, yani hiçbir firmanın verisine
    erişilemez — köprü ERP'ye değil, teknik danışmana bakar.
    """
    if not kopru.acik_mi():
        return BAGSIZ_MESAJI
    try:
        cevap = kopru.KopruIstemcisi().sor(telefon, (metin or "")[:SORU_MAKS])
    except kopru.KopruKapali:
        # `acik_mi` ile bu dal arasında ayar değişmiş olabilir.
        return BAGSIZ_MESAJI
    return cevap or BAGSIZ_MESAJI


# ---------------------------------------------------------------------------
# Mesaj işleme
# ---------------------------------------------------------------------------


def _bagla_akisi(
    db: Session,
    satir_id: int,
    jeton: str,
    telefon: str,
    ham_kod: str,
    saglayici: saglayici_modulu.MesajSaglayici,
    oturum_fabrikasi: Callable[[], Session] | None,
    an: datetime,
) -> int:
    """``BAĞLA <KOD>`` mesajını sonlandırır. HER DAL TERMİNAL durumla biter.

    Sözleşme:

    * Geçerli kod → bağlantı + CONSUMED AYNI transaction, başarı cevabı,
      satır ANSWERED.
    * Geçersiz/dolmuş/kullanılmış/zaten bağlı → AYNI genel red, ANSWERED.
    * Cevap eşiği aşılmış → DIŞ MESAJ YOK, satır IGNORED. Sayaç COMMIT
      edilir: satır yeniden denenirse cevap ve maliyet fırtınası olurdu.
    * Gönderim hatası → rollback (yarım bağlantı bırakılmaz) VE sayaç
      ayrı işlemde geri konur (modül başlığı, mercek bulgusu).
    """
    try:
        sonuc = eslestirme.kod_kullan(db, telefon, ham_kod, simdi=an)
    except Exception as hata:  # noqa: BLE001 - kalıcı: aynı girdi aynı hata
        db.rollback()
        _sayaci_kalicilastir(oturum_fabrikasi, telefon, an)
        log.exception("whatsapp: bagla islenemedi id=%s", satir_id)
        _sonlandir(
            db, satir_id, jeton,
            status=schema.DEAD,
            last_error=type(hata).__name__,
            processed_at=_simdi(),
        )
        return 1

    if not sonuc.cevapla:
        # Sessiz düşürme. Sayaç BU transaction'da yazıldı ve `_sonlandir`
        # onu commit eder — rollback yok, ayrı işleme de gerek yok.
        _sonlandir(db, satir_id, jeton, status=schema.IGNORED, processed_at=_simdi())
        return 1

    cevap = eslestirme.BASARI_MESAJI if sonuc.basarili else eslestirme.RED_MESAJI
    try:
        saglayici.metin_gonder(telefon, cevap)
    except saglayici_modulu.KaliciGonderimHatasi as hata:
        db.rollback()
        _sayaci_kalicilastir(oturum_fabrikasi, telefon, an)
        _sonlandir(
            db, satir_id, jeton,
            status=schema.DEAD,
            last_error=type(hata).__name__,
            processed_at=_simdi(),
        )
        return 1
    except saglayici_modulu.GonderimHatasi as hata:
        # Geçici: bağlantı yazıldıysa da GERİ ALINIR — kod PENDING kalır ve
        # yeniden deneme aynı sonucu üretir. Yarım bağlantı bırakılmaz.
        db.rollback()
        _sayaci_kalicilastir(oturum_fabrikasi, telefon, an)
        _gecici_hata(db, satir_id, jeton, hata)
        return 0

    _sonlandir(db, satir_id, jeton, status=schema.ANSWERED, processed_at=_simdi())
    return 1


def _mesaj_isle(
    db: Session,
    satir_id: int,
    jeton: str,
    saglayici: saglayici_modulu.MesajSaglayici,
    oturum_fabrikasi: Callable[[], Session] | None = None,
) -> int:
    """Kiralanmış TEK satırı sonlandırır. 1 = işlendi, 0 = yeniden denenecek."""
    an = _simdi()
    satir = db.execute(
        select(whatsapp_inbound).where(whatsapp_inbound.c.id == satir_id)
    ).mappings().first()
    if not satir:
        return 0

    # SAVUNMA KATMANI. Webhook zaten yabancı `phone_number_id`yi kuyruğa
    # yazmaz (`routers/whatsapp.py`), ama satır başka bir yoldan gelmiş
    # olabilir: eski kayıt, elle müdahale, sonradan değişen yapılandırma.
    # Bizim numaramıza gelmemiş mesaj HİÇBİR ERP verisine dokunmadan kapanır.
    beklenen = (settings.whatsapp_phone_number_id or "").strip()
    if beklenen and str(satir["phone_number_id"]) != beklenen:
        _sonlandir(
            db, satir_id, jeton, status=schema.IGNORED,
            last_error="phone_number_id", processed_at=_simdi(),
        )
        return 1

    # MEDYA SATIRINDA ALTYAZI ZORUNLU DEĞİL: fotoğrafın kendisi mesajdır.
    # `media_id` yüklemi olmasaydı altyazısız bir fatura fotoğrafı burada
    # IGNORED ile kapanır ve fatura yolu HİÇ koşmazdı.
    medya_mi = bool(satir["media_id"])
    metin = str(satir["text"] or "").strip()
    if not metin and not medya_mi:
        _sonlandir(db, satir_id, jeton, status=schema.IGNORED, processed_at=_simdi())
        return 1

    # KANONİK NUMARA. `e164` rakam sayısını [10, 15] dışında bulursa
    # yükseltir; sessizce kırpılmış bir numaraya cevap göndermek, YANLIŞ
    # KİŞİYE cevap göndermek olabilirdi. Eşleşme anahtarı ise `e164` değil
    # `normalize_phone` çıktısıdır (`whatsapp_links.phone` o biçimde).
    ham_telefon = str(satir["sender_phone"] or "")
    try:
        e164(ham_telefon)
    except TelefonGecersiz:
        _sonlandir(
            db, satir_id, jeton, status=schema.IGNORED,
            last_error="TelefonGecersiz", processed_at=_simdi(),
        )
        return 1
    telefon = normalize_phone(ham_telefon)

    # 1) ``BAĞLA <KOD>`` — kimlik ÇÖZÜLMEDEN ÖNCE. Eşleştirme tanımı gereği
    #    HENÜZ BAĞLI OLMAYAN numaradan gelir; kimlik çözümü onu bağsız
    #    sayar ve kullanıcı hiçbir zaman bağlanamazdı.
    ham_kod = eslestirme.bagla_ayristir(metin)
    if ham_kod is not None:
        return _bagla_akisi(
            db, satir_id, jeton, telefon, ham_kod, saglayici, oturum_fabrikasi, an
        )

    try:
        # 2) ``FİRMA LİSTELE`` / ``FİRMA SEÇ`` — niyet çözümünden ÖNCE,
        #    çünkü ikisi de tam olarak çözümün BELİRSİZ olduğu durumda
        #    anlamlıdır. Gövde `baglam.firma_komutu`da, kopya değil.
        cevap = baglam.firma_komutu(db, telefon, metin, simdi=an)
        if cevap is None:
            secim = baglam.kimlik_secimi(db, telefon, simdi=an)
            if secim.firma_secimi_gerekli:
                cevap = baglam.FIRMA_SECIN_MESAJI
            elif secim.kimlik is None:
                cevap = _bagsiz_cevap(telefon, metin)
            elif medya_mi:
                # FATURA YOLU KİMLİK ÇÖZÜLDÜKTEN SONRA. Bağsız numaraya ya
                # da firma seçmemiş kullanıcıya fatura özeti dönmek, ERP
                # bağlamı olmayan birine ERP cevabı vermek olurdu; üstteki
                # iki dal onları KENDİ cevaplarıyla karşılıyor.
                #
                # Bu tur yazma YAPMIYOR (`fatura` modülü `db` bile almaz),
                # bu yüzden AYRI bir yetki yüklemi YOK. Kaynak burada
                # `has_permission(role, "purchases")` arıyordu ve gerekçesi
                # taslak ALIŞ BELGESİ açmasıydı; açılan belge olmayınca
                # yüklemin koruduğu şey de yok. Belge açan tur onu GERİ
                # GETİRMELİDİR — `docs/whatsapp/WA5_FATURA.md` bunu yazıyor.
                cevap = fatura.medya_ozeti(saglayici, satir)
            else:
                cevap = cevap_uret(db, secim.kimlik, metin)
    except Exception as hata:  # noqa: BLE001 - kalıcı: aynı girdi aynı hata
        db.rollback()
        log.exception("whatsapp: mesaj islenemedi id=%s", satir_id)
        _sonlandir(
            db, satir_id, jeton, status=schema.DEAD,
            last_error=type(hata).__name__, processed_at=_simdi(),
        )
        return 1

    try:
        saglayici.metin_gonder(telefon, cevap)
    except saglayici_modulu.KaliciGonderimHatasi as hata:
        _sonlandir(
            db, satir_id, jeton, status=schema.DEAD,
            last_error=type(hata).__name__, processed_at=_simdi(),
        )
        return 1
    except saglayici_modulu.GonderimHatasi as hata:
        _gecici_hata(db, satir_id, jeton, hata)
        return 0

    _sonlandir(db, satir_id, jeton, status=schema.ANSWERED, processed_at=_simdi())
    return 1


def bekleyenleri_isle(
    db: Session,
    *,
    en_fazla: int | None = None,
    saglayici: saglayici_modulu.MesajSaglayici | None = None,
    oturum_fabrikasi: Callable[[], Session] | None = None,
    simdi: datetime | None = None,
) -> int:
    """İşlenmemiş mesajları SIRAYLA ele alır; işlenen sayısını döner.

    Kalıcı satır + kira/CAS sayesinde çok işçili kurulumda da güvenlidir:
    iş süreç belleğinde değil VERİTABANINDADIR.

    Aday seçimi `_claim`in yüklemiyle AYNI koşulu taşır; bu bir tekrar
    DEĞİL, iki ayrı iştir: burada SIRALAMA ve SINIR var (`id` artan,
    `en_fazla` satır), claim'de ise HAKEMLİK var. Aday listesi eskiyebilir
    ve bu zararsızdır — kaybeden claim `None` alır ve satıra dokunmaz.
    """
    an = simdi or _simdi()
    sinir = en_fazla if en_fazla is not None else int(settings.whatsapp_worker_batch)
    adaylar = db.execute(
        select(whatsapp_inbound.c.id)
        .where(
            whatsapp_inbound.c.attempt_count < schema.MAX_DENEME,
            or_(
                whatsapp_inbound.c.status == schema.RECEIVED,
                and_(
                    whatsapp_inbound.c.status == schema.PROCESSING,
                    whatsapp_inbound.c.locked_until <= an,
                ),
            ),
        )
        .order_by(whatsapp_inbound.c.id)
        .limit(sinir)
    ).scalars().all()

    gonderici = saglayici if saglayici is not None else saglayici_modulu.saglayici_al()
    islenen = 0
    for satir_id in adaylar:
        jeton = _claim(db, int(satir_id))
        if jeton is None:
            continue  # başka işçi kazandı ya da tavan doldu
        islenen += _mesaj_isle(db, int(satir_id), jeton, gonderici, oturum_fabrikasi)
    return islenen


__all__ = [
    "BAGSIZ_MESAJI",
    "COK_ESLESME_BASLIGI",
    "COK_ESLESME_KUYRUGU",
    "SORU_MAKS",
    "WA4_MESAJI",
    "bekleyenleri_isle",
    "cevap_uret",
    "takilanlari_kapat",
]
