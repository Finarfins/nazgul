"""Çiftçi yürütücüsü: rıza kapısı, hız sınırı ve DÖRT okuma aracı (F10-1b/1c).

`yurutucu.py` personelin YEDİ aracını koşturur; bu modül çiftçinin DÖRDÜNÜ
(`ciftci_ekstre`, `ciftci_avans`, `ciftci_kantar`, `ciftci_makbuz`)
koşturur ve onların ÖNÜNDEKİ iki kapıyı kurar. Tasarımın tamamı
`docs/f10-1-ciftci-selfservice-kesif-2026-09-17.md` §4a–§4e, §5.2, §5.3 ve
§5.4'tedir.

--- KANTAR VE MAKBUZ: YALNIZ KESİLMİŞ MAKBUZ ÜZERİNDEN (F10-1c) ----------

Kantar fişinin TARAF SÜTUNU YOKTUR (keşif §3.1). Bu yüzden çiftçiye YALNIZ
`producer_receipts.ticket_id` üzerinden KENDİ (`supplier_id`) ve KESİLMİŞ
(`status='issued'`) makbuzuna bağlı fişler gösterilir (K5). Sahipsiz fiş,
parsel zinciri ve `buyer_name` eşleşmesi SORGULANMAZ — fişte çiftçinin
adı yazsa bile. Taslak ve iptal edilmiş makbuz GÖRÜNMEZ: kesilmemiş bir
kağıdı göstermek verilmemiş bir sözü vermek olurdu (keşif §4d).

`CUSTOMER` tarafı ikisini de sorabilir ve NÖTR "kayıt bulunmuyor" alır —
kaydı olmayan TEDARİKÇİNİN aldığı metnin AYNISI (`ciftci_avans`ın
kuralı). Başka birinin makbuz numarası da AYNI metni alır: numara yalnız
çiftçinin KENDİ satırları arasında seçim yapar, var olup olmadığını
söylemez.

SQL `app/mustahsil_okuma.py`dedir ve router ile ORTAKTIR; fişin neti
`farm._turetilmis_net`ten o modül üzerinden gelir. Burada SQL de formül
de YOKTUR (kapı `test_KANTAR_MAKBUZ_SQL_KOPYASI_YOK_FIS_NETI_ORTAK`).

Kırpılanlar (keşif §4c/§4d): fişin `buyer_name`/`notes`u hiç SEÇİLMEZ;
makbuzun `purchase_id`/`note`u aracın dönüşüne GİRMEZ. Çiftçinin KENDİ
kalem fiyatı (`unit_price`) gösterilir (K3); `products.sale_price`
okunmaz.

--- İKİ BEYAZ LİSTE ASLA BİRLEŞMEZ ---------------------------------------

`niyet.ARAC_BEYAZ_LISTESI` (yedi personel aracı) bu PR'da DEĞİŞMEDİ ve
`CIFTCI_BEYAZ_LISTESI` onunla HİÇ KESİŞMEZ. Kapı `KeyError`dır ve
`yurutucu.VeritabaniYurutucu.kos`un kapısıyla AYNI biçimdedir — ama AYRI
bir frozenset'e bakar. Tek bir liste olsaydı "hangi listeye bakılacağı"
çağrı bağlamına bağlı kalırdı ve yanlış listeyle çağıran bir mutasyon
hiçbir davranış testini kırmazdı.

`niyet.tahsilat_coz` bu dosyada ÇAĞRILMAZ ve içe AKTARILMAZ: çiftçi
yolunda YAZMA niyeti YOKTUR, yani `whatsapp_pending_actions`e çiftçiden
HİÇBİR satır düşemez (keşif §5.1).

--- KİRACI SINIRI: ÜÇLÜ ARGÜMANDIR, MESAJDAN GELMEZ ----------------------

Her sorgu `TarafKimlik`in `(company_id, party_type, party_id)` üçlüsüyle
koşar. `yurutucu.py`nin cümlesi burada BİR ADIM DAHA SIKIDIR: orada
mesajdan gelen bir firma iddiası "kabul edilmiyor"du; burada mesajdan
gelen bir CARİ iddiası da kabul edilmiyor — `niyet._terim_cikar` bu
dalda hiç çağrılmıyor (`ciftci_niyet` başlığı).

MUTASYON ADIYLA: `company_id` yüklemini düşürmek komşu firmanın AYNI ADI
taşıyan çiftçisinin bakiyesini döndürür. Kapı
`test_KIRACI_YALITIMI_ayni_ad_komsu_firmada_CIFTCI`.

--- RIZA KAPISI: KARAR HER MESAJDA YENİDEN OKUNUR ------------------------

`consents.py`nin ikinci sözleşmesi ("anlık görüntü karar verici değildir")
burada AYNEN uygulanır: `whatsapp_party_links.consent_at` bir İZDİR,
KARAR DEĞİLDİR. Her ERP okumasından ÖNCE `evaluate_consent` çağrılır ve
`NO_RECORD`/`REVOKED`/`RECIPIENT_CHANGED`/`RECIPIENT_INVALID`in dördü de
FAIL-CLOSED'dur — veri DÖNMEZ.

RIZAYI ÇİFTÇİNİN İLK MESAJI VERİR, EŞLEŞTİRME DEĞİL (Şef kararı,
`taraf.kod_kullan` başlığı). `BAĞLA <KOD>` başarılı olduğunda bağlantı
açılır ama rıza satırı AÇILMAZ; çiftçinin ilk sorusu KVKK metnini ve
`EVET`/`HAYIR` sorusunu alır.

`HAYIR` bir NO-OP DEĞİLDİR, `REVOKED` yazar. Defter bunu destekliyor
(ölçüldü: `set_consent` kayıt YOKKEN `granted=False` ile çağrıldığında
`status='REVOKED'`, `version=1` satırını INSERT eder) ve tercih edilme
gerekçesi davranışsaldır: no-op bırakılsaydı `evaluate_consent` her
mesajda yine `NO_RECORD` derdi ve çiftçi KVKK sorusunu SONSUZA DEK
yeniden alırdı — hem rahatsız edici hem Meta maliyeti üzerinden bir
masraf yüzeyi. `EVET` her zaman `GRANTED` yazabildiği için reddeden
çiftçi fikrini değiştirebilir.

`HAYIR` AÇIK bir rızayı da KAPATIR (GRANTED → REVOKED, versiyon +1, olay
satırı; runtime lens tur 2). Çiftçiye "bu numaraya bilgi göndermeyeceğiz"
denen her yolda defter o cümleyi doğrulamalıdır. `consent_at` iz olarak
KALIR (tarihçe); zaten `REVOKED` olan deftere ikinci yazım YAPILMAZ.

`EVET` YALNIZ rıza AÇIK DEĞİLKEN yazar. Zaten açıkken gelen "EVET"
kapsam mesajına düşer; aksi hâlde tekrarlanan tek bir kelime sınırsız
`version` artışı ve sınırsız olay satırı üretebilirdi.

RIZA (FİRMA, TARAF) BAŞINADIR — ÇOK FİRMALI ÇİFTÇİ (Şef kararı, runtime
lens düzeltme 2). KVKK metni firmayı ADIYLA anar, yani cevabı da o
firmaya gider: "1 EKSTRE" rızasız 1. firma için KVKK metnini alır ve
metin "1 EVET / 1 HAYIR" ile biter; "1 EVET" YALNIZ 1. firmaya GRANTED,
"1 HAYIR" YALNIZ 1. firmaya REVOKED yazar. Öneksiz `EVET`/`HAYIR` N>1'de
HİÇBİR ŞEY yazmaz (`_firma_coz`). `DUR`/`İPTAL` GLOBAL kalır. Tekrar
kuralı (yukarıdaki paragraf) firma BAŞINA uygulanır.

--- AKTÖR `NULL`, KAYNAK `whatsapp_party` -------------------------------

`log_activity` bir `app_users.id` bekler; çiftçinin böyle bir kimliği
YOKTUR (keşif §5.5). `activity_logs.user_id` geri yüklemede zaten yumuşak
referanstır (`kiraci_geri_yukleme.KULLANICI_SUTUNLARI`), `NULL` onu
bozmaz. Kaynak kimliği `whatsapp_party_links.id`dir.

MESAJ METNİ VE TELEFON ASLA LOGLANMAZ — `routers/whatsapp.py:347-349`un
kuralı: *"Kod, özet, telefon ve mesaj metni YAZILMAZ."*

--- HIZ SINIRI YALNIZ BU DALDA ------------------------------------------

`mesaj_deneme_say` YALNIZ buradan çağrılır. Personel yolu
(`service.cevap_uret`) ona HİÇ uğramaz ve bu KAPSAM kararıdır, eksiklik
değil: keşif §1.5 personel yolunda sınır OLMADIĞINI ölçtü ve §5.3 onu
çiftçi için ZORUNLU saydı. Personel sınırını değiştirmek bu PR'ın
kapsamı dışındadır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..avans_servis import tedarikci_tum_avanslari
from ..auth import utcnow
from ..business_time import ISTANBUL
from ..notifications import consents
from ..tenancy import companies
from . import ciftci_niyet, schema, taraf
from .niyet import para_tr
from .schema import whatsapp_message_attempts
from .taraf import TarafKimlik
from .telefon import normalize_phone

log = logging.getLogger(__name__)

#: Çiftçinin koşturabileceği araçların TAMAMI. `niyet.ARAC_BEYAZ_LISTESI`
#: ile KESİŞİMİ BOŞTUR ve bu bir kapıyla ölçülüyor
#: (`test_IKI_BEYAZ_LISTE_KESISMIYOR`).
CIFTCI_BEYAZ_LISTESI: frozenset[str] = frozenset(
    {"ciftci_ekstre", "ciftci_avans", "ciftci_kantar", "ciftci_makbuz"}
)

#: WhatsApp kanalının rıza defterindeki adı. `consents.CONSENT_REQUIRED_CHANNELS`
#: üyesidir (ölçüldü, `notifications/schema.py:72`).
KANAL = "WHATSAPP"


# ---------------------------------------------------------------------------
# HIZ SINIRI — `eslestirme.deneme_say`in deseni, AYRI tablo
# ---------------------------------------------------------------------------


def _pencere_basi(an: datetime) -> datetime:
    """Sabit pencereye yuvarlar.

    `eslestirme._pencere_basi` ile AYNI gövde ve AYNI gerekçe (kayan
    pencere gerekmiyor); AYRI sabitle (`MESAJ_PENCERE_DAKIKA`) koşar.
    """
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    dakika = schema.MESAJ_PENCERE_DAKIKA
    return an.replace(minute=(an.minute // dakika) * dakika, second=0, microsecond=0)


def mesaj_deneme_say(
    db: Session, telefon: str, *, simdi: datetime | None = None
) -> int:
    """Çiftçi mesajını ATOMİK olarak sayar ve penceredeki TOPLAMI döner.

    Tek deyimlik UPSERT: iki işçi aynı anda artırsa bile sayaç KAYBOLMAZ
    (`uq_whatsapp_message_attempts_pencere` çakışma hedefidir). Uygulama
    belleği KULLANILMAZ — çok konteynerde paylaşılmaz.

    Tablo PLATFORM tablosudur ve bu ZORUNLU (göç `20260920_0091` başlığı):
    sınırın koruduğu şey bir firmanın verisi değil, BOT NUMARASININ mesaj
    bütçesidir. Bu yüzden sorgu KİRACI YÜKLEMİ TAŞIMAZ ve taşıyamaz —
    ölçülen istisna kaydı `tests/test_core_tenant_scoping_guard.py`dedir.
    """
    an = simdi or utcnow()
    pencere = _pencere_basi(an)
    normal = normalize_phone(telefon)

    lehce = db.bind.dialect.name if db.bind is not None else ""
    if lehce == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        deyim = pg_insert(whatsapp_message_attempts).values(
            phone=normal, window_start=pencere, attempt_count=1, updated_at=an
        )
        deyim = deyim.on_conflict_do_update(
            index_elements=["phone", "window_start"],
            set_={
                "attempt_count": whatsapp_message_attempts.c.attempt_count + 1,
                "updated_at": an,
            },
        ).returning(whatsapp_message_attempts.c.attempt_count)
        return int(db.execute(deyim).scalar_one())

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    deyim = sqlite_insert(whatsapp_message_attempts).values(
        phone=normal, window_start=pencere, attempt_count=1, updated_at=an
    )
    deyim = deyim.on_conflict_do_update(
        index_elements=["phone", "window_start"],
        set_={
            "attempt_count": whatsapp_message_attempts.c.attempt_count + 1,
            "updated_at": an,
        },
    ).returning(whatsapp_message_attempts.c.attempt_count)
    return int(db.execute(deyim).scalar_one())


# ---------------------------------------------------------------------------
# ARAÇLAR — DÖRDÜ DE OKUMA
# ---------------------------------------------------------------------------

#: `TarafKimlik.party_type` → `statement.build_statement`in `entity_type`i.
#: Keşif §3.1 ölçtü: çiftçi İKİ ayrı caridir ve hangi defterin okunacağını
#: BAĞLANTININ TİPİ söyler, mesaj DEĞİL.
_EKSTRE_TARAFI: dict[str, str] = {"CUSTOMER": "customer", "SUPPLIER": "supplier"}


def ciftci_ekstre(
    db: Session, kimlik: TarafKimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    """Ekstre/bakiye (keşif §4a). `build_statement`i `rol` GEÇİRMEDEN çağırır.

    `rol` ATLANMASI BİR UNUTMA DEĞİL, KARARIN KENDİSİDİR: `build_statement`
    başlığı *"varsayılanı MASKELİDİR ... yarın eklenen bir çağıran `rol`
    geçirmeyi unutursa sonuç GİZLİ olur, sızıntı DEĞİL"* diyor. Çiftçi
    kendi bakiyesini görmeli, firmanın CARİ KARTI görünümünü (VKN, adres,
    e-posta) değil.

    Bakiye formülü KOPYALANMADI: aynı fonksiyon `routers/customers.py` ve
    `routers/finance.py` tarafından da çağrılıyor. Kopya bir formül, iki
    yüzeyin aynı çiftçi için farklı sayı söylediği güne kadar sessiz
    kalırdı ve o gün hangisinin doğru olduğu BİLİNEMEZDİ.

    İÇE AKTARMA GÖVDEDE: `statement` modülü `fastapi`yi çeker ve
    `app/whatsapp/__init__.py` *"bu paketi içe aktaran bir test ya da işçi
    FastAPI uygulamasını yüklemek ZORUNDA OLMAMALI"* diyor —
    `yurutucu.py`nin kuralı.
    """
    from ..statement import build_statement

    entity_type = _EKSTRE_TARAFI[kimlik.party_type]
    ekstre = build_statement(
        db,
        kimlik.company_id,
        entity_type,
        kimlik.party_id,
        argumanlar.get("date_from"),
        argumanlar.get("date_to"),
    )
    # SON HAREKET son SATIRDAN okunur, `date_to`dan DEĞİL: `date_to`
    # pencerenin sonudur ve hareket olmayan bir ayda kullanıcıya olmamış
    # bir hareket tarihi söylerdi.
    son_hareket = ekstre.lines[-1].entry_date if ekstre.lines else ""
    return {
        "ad": ekstre.entity.name,
        "date_to": ekstre.date_to,
        "borc": Decimal(str(ekstre.total_debit)),
        "alacak": Decimal(str(ekstre.total_credit)),
        "net": Decimal(str(ekstre.closing_balance)),
        "son_hareket": son_hareket,
    }


def ciftci_avans(
    db: Session, kimlik: TarafKimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    """Avans durumu (keşif §4b). YALNIZ tedarikçi tarafında veri vardır.

    `CUSTOMER` tarafı avans sorduğunda TARAF TİPİ SIZDIRILMAZ: cevap
    "kayıt bulunmuyor"dur ve o cevap, kaydı gerçekten olmayan bir
    TEDARİKÇİNİN alacağı cevapla BİREBİR AYNIDIR. "Siz müşterisiniz,
    avansınız olamaz" demek, dışarıdaki birine defterin şeklini
    anlatırdı.
    """
    if kimlik.party_type != schema.TARAF_SUPPLIER:
        return {"adet": 0, "alinan": Decimal("0"), "kalan": Decimal("0"), "son": None}

    # Satırlar personel ucunun (`routers/avans.py::list_supplier_advances`)
    # okuduğu fonksiyonun AYNISINDAN gelir; burada YALNIZ toplanır. Avans
    # SQL'inin ikinci bir kopyası YOKTUR — kopya, iki yüzeyin aynı çiftçi
    # için farklı rakam söylediği güne kadar sessiz kalırdı. Eşitlik
    # kapısı: `test_AVANS_TOPLAMLARI_UCUN_KENDI_SAYILARIYLA_AYNI`.
    #
    # Satır `payment_id` ve `note` TAŞIR ama bu fonksiyonun dönüşüne
    # GİRMEZ (keşif §4b: `note` personel notu olabilir); dönüş yalnız
    # dört toplamdır. Tarih `payments.payment_date`tir çünkü
    # `supplier_advances`in kendi tarih sütunu YOKTUR (`avans_servis`
    # başlığı).
    satirlar = tedarikci_tum_avanslari(db, kimlik.company_id, kimlik.party_id)
    tarihler = [str(r["payment_date"]) for r in satirlar if r["payment_date"]]
    return {
        "adet": len(satirlar),
        "alinan": sum((Decimal(str(r["amount"] or 0)) for r in satirlar), Decimal("0")),
        "kalan": sum(
            (Decimal(str(r["remaining_amount"] or 0)) for r in satirlar), Decimal("0")
        ),
        "son": max(tarihler) if tarihler else None,
    }


#: "LISTE" cevabının satır sayısı (keşif §4c: "KANTAR LISTE" → son beş).
LISTE_ADEDI = 5
#: Çiftçiye gösterilen TEK makbuz durumu (başlık). Sözlük, iç durum adının
#: (`issued`) mesaja sızmaması içindir.
_KESILDI = "issued"
_DURUM_TR = {_KESILDI: "kesildi"}
#: Kantar aracının makbuz sayfası. Aynı fişe bağlı iki makbuz TEK fiş sayılır;
#: sayfalama, tekrarlı fişler yüzünden beş farklı fişe ulaşmak içindir.
_FIS_SAYFASI = 20
#: Bir kantar cevabı için okunacak kesilmiş+fişli makbuz satırı TAVANI (H92).
#: Sayfalama "beş farklı fiş ya da satırlar bitene kadar" dönüyordu; tek bir
#: fişe binlerce makbuz bağlayan bir geçmiş, WhatsApp işleyicisini çiftçinin
#: makbuz tablosunun TAMAMINI gezmeye zorlardı. 500 satır = en çok 25
#: sayfa sorgusu (her biri ≤20 satır). Olağan bir çiftçide fiş başına bir-iki
#: makbuz düşer, yani beş fişe onlarca satırda ulaşılır ve tavan yalnız
#: düşmanca/bozuk bir geçmişte devreye girer. Tavan KESİNDİR (son sayfa kırpılır) ve aşılınca
#: liste o ana kadar bulunan fişlerle döner — hata YOK.
_TARAMA_TAVANI = 500


def ciftci_kantar(
    db: Session, kimlik: TarafKimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    """Kantar fişi (keşif §4c, K5). YALNIZ kesilmiş makbuza bağlı fişler.

    Sıra makbuzun sırasıdır (`id` AZALAN): "son fişiniz" = en son KESİLEN
    makbuzun fişi. Fişin kendi `weighed_at`i NULL olabildiği için sıra
    oradan kurulmaz.

    "LISTE" beş FARKLI fiştir: makbuzlar sayfa sayfa okunur ve aynı fişe
    bağlı ikinci makbuz atlanır; tarama `_TARAMA_TAVANI` satırda durur (H92).

    İÇE AKTARMA GÖVDEDE: `mustahsil_okuma` `routers.farm` üzerinden
    `fastapi`yi çeker (`ciftci_ekstre`nin kuralı).
    """
    liste = bool(argumanlar.get("liste"))
    if kimlik.party_type != schema.TARAF_SUPPLIER:
        return {"liste": liste, "fisler": []}

    from ..mustahsil_okuma import fis_ozeti, makbuz_listesi

    adet = LISTE_ADEDI if liste else 1
    fisler: list[dict[str, Any]] = []
    gorulen: set[int] = set()
    taranan = 0
    while len(fisler) < adet and taranan < _TARAMA_TAVANI:
        istenen = min(_FIS_SAYFASI, _TARAMA_TAVANI - taranan)
        sayfa = makbuz_listesi(
            db,
            kimlik.company_id,
            supplier_id=kimlik.party_id,
            status=_KESILDI,
            yalniz_fisli=True,
            limit=istenen,
            offset=taranan,
        )
        taranan += len(sayfa)
        for makbuz in sayfa:
            fis_id = int(makbuz["ticket_id"])
            if fis_id in gorulen:
                continue
            gorulen.add(fis_id)
            ozet = fis_ozeti(db, kimlik.company_id, fis_id)
            if ozet is None:
                # Bileşik FK bunu imkânsız kılar; olursa fiş YOK sayılır.
                continue
            fisler.append(
                {
                    "ticket_no": ozet["ticket_no"],
                    "weighed_at": ozet["weighed_at"],
                    "entered_unit": ozet["entered_unit"],
                    "brut": ozet["brut"],
                    "kesinti_orani": ozet["kesinti_orani"],
                    "net": ozet["net"],
                    "receipt_no": makbuz["receipt_no"],
                    "status": makbuz["status"],
                }
            )
            if len(fisler) == adet:
                break
        if len(sayfa) < istenen:
            break
    return {"liste": liste, "fisler": fisler}


def ciftci_makbuz(
    db: Session, kimlik: TarafKimlik, argumanlar: dict[str, Any]
) -> dict[str, Any]:
    """Müstahsil makbuzu (keşif §4d). YALNIZ tedarikçi, YALNIZ `issued`.

    ``receipt_no`` verilmişse yalnız O numara, yine çiftçinin KENDİ kesilmiş
    makbuzları arasında aranır; bulunamazsa sonuç "hiç kaydı yok" ile
    AYIRT EDİLEMEZ (boş liste).

    Dönüş keşfin alan listesidir; `purchase_id` ve `note` GİRMEZ. Tekil
    görünümde çiftçinin KENDİ kalem fiyatları (`unit_price`) eklenir (K3).
    """
    liste = bool(argumanlar.get("liste"))
    if kimlik.party_type != schema.TARAF_SUPPLIER:
        return {"liste": liste, "makbuzlar": []}

    from ..mustahsil_okuma import makbuz_kalemleri, makbuz_listesi

    satirlar = makbuz_listesi(
        db,
        kimlik.company_id,
        supplier_id=kimlik.party_id,
        status=_KESILDI,
        receipt_no=argumanlar.get("receipt_no"),
        limit=LISTE_ADEDI if liste else 1,
    )
    makbuzlar = [
        {
            "receipt_no": s["receipt_no"],
            "issued_at": s["issued_at"],
            "gross_amount": Decimal(str(s["gross_amount"])),
            "withholding_total": Decimal(str(s["withholding_total"])),
            "social_security_total": Decimal(str(s["social_security_total"])),
            "net_payable": Decimal(str(s["net_payable"])),
            "status": s["status"],
            "birim_fiyatlar": (
                []
                if liste
                else [
                    Decimal(str(k["unit_price"]))
                    for k in makbuz_kalemleri(db, kimlik.company_id, int(s["id"]))
                ]
            ),
        }
        for s in satirlar
    ]
    return {"liste": liste, "makbuzlar": makbuzlar}


#: Araç adı → gövde. Anahtar kümesi `CIFTCI_BEYAZ_LISTESI` ile BİREBİR
#: aynı olmak ZORUNDA — `yurutucu.ARAC_GOVDELERI`nin kuralı ve aynı
#: gerekçe: eksik bir gövde, beyaz listeden geçmiş bir aracın `KeyError`
#: ile DEAD üretmesi demekti. Kapı `test_IKI_ARACIN_IKISI_DE_YURUTULEBILIYOR`.
CIFTCI_ARAC_GOVDELERI = {
    "ciftci_ekstre": ciftci_ekstre,
    "ciftci_avans": ciftci_avans,
    "ciftci_kantar": ciftci_kantar,
    "ciftci_makbuz": ciftci_makbuz,
}


class CiftciYurutucu:
    """Tek örnek TEK `TarafKimlik`e bağlıdır — `VeritabaniYurutucu`nun kuralı.

    `kos` firma ya da cari SEÇMEZ; kurulurken seçilmiştir. Argümanla
    geçirilebilseydi, argümanı mesajdan dolduran bir kod yolu bir gün
    yazılabilirdi.
    """

    def __init__(self, db: Session, kimlik: TarafKimlik) -> None:
        self._db = db
        self._kimlik = kimlik

    def kos(self, arac: str, argumanlar: dict[str, Any]) -> dict[str, Any]:
        if arac not in CIFTCI_BEYAZ_LISTESI:
            raise KeyError(arac)
        return CIFTCI_ARAC_GOVDELERI[arac](self._db, self._kimlik, dict(argumanlar))


# ---------------------------------------------------------------------------
# ŞABLONLU CEVAP — dış model YOK, rakamlar Decimal ile biçimlenir
# ---------------------------------------------------------------------------


def _gun_tr(iso: str) -> str:
    """``"2026-09-17"`` → ``"17.09.2026"``. Ayrıştırılamayan değer OLDUĞU GİBİ döner.

    `date.fromisoformat` KULLANILMAZ: `statement` satırlarının
    `entry_date`i `COALESCE(...,'')` ile geliyor, yani boş dize OLABİLİR ve
    bir istisna, cevabın tamamını DEAD'e çevirirdi.
    """
    parca = (iso or "").split("-")
    if len(parca) != 3 or not all(parca):
        return iso or ""
    return f"{parca[2][:2]}.{parca[1]}.{parca[0]}"


def _ekstre_yaz(veri: dict[str, Any]) -> str:
    """Keşif §4a'nın BEŞ SATIRLIK şablonu. Başka cari ADI geçmez."""
    net = veri["net"]
    yon = "borç" if net > 0 else ("alacak" if net < 0 else "bakiye")
    satirlar = [
        f"Sayın {veri['ad']}, {_gun_tr(veri['date_to'])} itibarıyla bakiyeniz:",
        f"Borç {para_tr(veri['borc'])} TL · Alacak {para_tr(veri['alacak'])} TL",
        f"NET: {para_tr(abs(net))} TL {yon}",
    ]
    if veri["son_hareket"]:
        satirlar.append(f"Son hareket: {_gun_tr(veri['son_hareket'])}")
    satirlar.append("Detay için \"EKSTRE EYLÜL\" gibi bir ay yazabilirsiniz.")
    return "\n".join(satirlar)


def _avans_yaz(veri: dict[str, Any]) -> str:
    """Keşif §4b'nin şablonu. `payment_id` ve `note` HİÇ GEÇMEZ (başlık)."""
    if not veri["adet"]:
        return "Bu numara için avans kaydı bulunmuyor."
    mahsup = veri["alinan"] - veri["kalan"]
    satirlar = [
        "Avans durumunuz:",
        f"Toplam alınan: {para_tr(veri['alinan'])} TL",
        f"Mahsup edilen: {para_tr(mahsup)} TL",
        f"KALAN AVANS: {para_tr(veri['kalan'])} TL",
    ]
    if veri["son"]:
        satirlar.append(f"Son avans {_gun_tr(str(veri['son'])[:10])}.")
    return "\n".join(satirlar)


#: NÖTR metinler — CUSTOMER tarafı, kaydı olmayan tedarikçi ve başkasının
#: makbuz numarası AYNI metni alır (başlık; kapı bayt karşılaştırmasıdır).
KANTAR_YOK_MESAJI = "Bu numara için kantar fişi kaydı bulunmuyor."
MAKBUZ_YOK_MESAJI = "Bu numara için müstahsil makbuzu kaydı bulunmuyor."


def _tarih_tr(deger: Any) -> str:
    """`datetime` ya da ISO metni → İstanbul günü ``"11.09.2026"``; yoksa ``""``.

    PG `TIMESTAMPTZ`yi `datetime`, SQLite metin döndürür; ikisi de aynı güne
    çevrilir. Gün İSTANBUL'dadır: gece yarısına yakın kesilen bir makbuz
    UTC gününde gösterilseydi çiftçinin elindeki kağıttan bir gün
    ayrışırdı. Ayrıştırılamayan metin `_gun_tr`a düşer (istisna YOK).
    """
    if deger is None or deger == "":
        return ""
    an = deger
    if not isinstance(an, datetime):
        metin = str(deger).strip()
        try:
            an = datetime.fromisoformat(metin.replace("Z", "+00:00"))
        except ValueError:
            return _gun_tr(metin[:10])
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an.astimezone(ISTANBUL).strftime("%d.%m.%Y")


def _sayi_tr(deger: Decimal, *, en_az: int) -> str:
    """Türkçe biçim, ölçek KORUNARAK: ``23575.5125`` → ``"23.575,5125"``.

    Sondaki sıfırlar ``en_az`` basamağa kadar atılır (``23575.5000`` →
    ``"23.575,50"``) ama anlamlı basamak ASLA yuvarlanmaz: cevaptaki net,
    router'ın `_fis_neti`sinin döndürdüğü sayının KENDİSİDİR (kapı gram
    düzeyinde karşılaştırıyor).
    """
    isaret = "-" if deger < 0 else ""
    tam, _, kesir = format(abs(deger), "f").partition(".")
    kesir = kesir.rstrip("0").ljust(en_az, "0")
    gruplu = f"{int(tam):,}".replace(",", ".")
    return f"{isaret}{gruplu},{kesir}" if kesir else f"{isaret}{gruplu}"


def _fis_basligi(fis: dict[str, Any]) -> str:
    """``"#4412, 11.09.2026"`` — numarası ya da tarihi olmayan fişte eksik parça ATLANIR."""
    ekler = [f"#{fis['ticket_no']}"] if fis["ticket_no"] else []
    gun = _tarih_tr(fis["weighed_at"])
    if gun:
        ekler.append(gun)
    return ", ".join(ekler)


def _durum_tr(durum: str) -> str:
    return _DURUM_TR.get(durum, durum)


def _kantar_yaz(veri: dict[str, Any]) -> str:
    """Keşif §4c'nin şablonu (≤5 satır). `buyer_name`/`notes` HİÇ GEÇMEZ."""
    fisler = veri["fisler"]
    if not fisler:
        return KANTAR_YOK_MESAJI
    if veri["liste"]:
        satirlar = []
        for fis in fisler:
            baslik = _fis_basligi(fis)
            parcalar = [baslik] if baslik else []
            parcalar.append(
                f"NET {_sayi_tr(fis['net'], en_az=2)} {fis['entered_unit'].lower()}"
            )
            parcalar.append(fis["receipt_no"])
            satirlar.append(" · ".join(parcalar))
        return "\n".join(satirlar)
    fis = fisler[0]
    birim = fis["entered_unit"].lower()
    baslik = _fis_basligi(fis)
    return "\n".join(
        [
            f"Son kantar fişiniz ({baslik}):" if baslik else "Son kantar fişiniz:",
            f"Brüt {_sayi_tr(fis['brut'], en_az=2)} {birim} · "
            f"Kesinti %{_sayi_tr(fis['kesinti_orani'], en_az=0)}",
            f"NET: {_sayi_tr(fis['net'], en_az=2)} {birim}",
            f"Makbuz: {fis['receipt_no']} ({_durum_tr(fis['status'])})",
            'Daha eskisi için "KANTAR LISTE" yazın.',
        ]
    )


def _makbuz_yaz(veri: dict[str, Any]) -> str:
    """Keşif §4d'nin şablonu (≤5 satır). `purchase_id`/`note` HİÇ GEÇMEZ."""
    makbuzlar = veri["makbuzlar"]
    if not makbuzlar:
        return MAKBUZ_YOK_MESAJI
    if veri["liste"]:
        satirlar = []
        for m in makbuzlar:
            parcalar = [m["receipt_no"]]
            gun = _tarih_tr(m["issued_at"])
            if gun:
                parcalar.append(gun)
            parcalar.append(f"NET {para_tr(m['net_payable'])} TL")
            satirlar.append(" · ".join(parcalar))
        return "\n".join(satirlar)
    m = makbuzlar[0]
    ekler = [_tarih_tr(m["issued_at"]), _durum_tr(m["status"])]
    brut = f"Brüt {para_tr(m['gross_amount'])} TL"
    if m["birim_fiyatlar"]:
        # Çiftçinin KENDİ kalem fiyatları (K3), kalem sırasıyla, tekrarsız.
        fiyatlar = list(dict.fromkeys(m["birim_fiyatlar"]))
        brut += " · Birim fiyat " + " / ".join(para_tr(f) for f in fiyatlar) + " TL"
    return "\n".join(
        [
            f"Müstahsil makbuzunuz {m['receipt_no']} "
            f"({', '.join(e for e in ekler if e)}):",
            brut,
            f"Stopaj {para_tr(m['withholding_total'])} TL · "
            f"Bağ-Kur {para_tr(m['social_security_total'])} TL",
            f"NET ÖDENECEK: {para_tr(m['net_payable'])} TL",
            'PDF için alım merkezinize başvurun; eskiler için "MAKBUZ LISTE".',
        ]
    )


_SABLONLAR = {
    "ciftci_ekstre": _ekstre_yaz,
    "ciftci_avans": _avans_yaz,
    "ciftci_kantar": _kantar_yaz,
    "ciftci_makbuz": _makbuz_yaz,
}


def cevap_yaz(arac: str, veri: dict[str, Any]) -> str:
    """Araç sonucunu kullanıcı metnine çevirir. Beyaz liste dışı → `KeyError`."""
    return _SABLONLAR[arac](veri)


# ---------------------------------------------------------------------------
# AKIŞ — dağıtıcının çağırdığı TEK giriş
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CiftciSonucu:
    """`_mesaj_isle`in bu daldan aldığı TAM sonuç.

    ``islendi=False`` pencere sınırının (20) ÜSTÜ demektir: mesaja HİÇ
    dokunulmadı, hiçbir yazma yapılmadı. ``cevapla=False`` ama
    ``islendi=True`` ise cevap eşiğinin (15) üstündedir: mesaj TAM OLARAK
    işlendi — `DUR` ısırdı, rıza yazması düştü — ama dış mesaj
    GÖNDERİLMEZ. Ayrım `PAIRING_CEVAP_SINIRI`nin gerekçesidir.
    """

    cevap: str
    cevapla: bool
    islendi: bool


def _firma_adi(db: Session, company_id: int) -> str:
    """Firma adı — KİRACI YÜKLEMLİ tek satır okuması (`baglam.firma_adlari`)."""
    ad = db.execute(
        select(companies.c.name).where(companies.c.id == company_id)
    ).scalar_one_or_none()
    return str(ad or "").strip() or f"Firma #{company_id}"


def _riza_degerlendir(db: Session, kimlik: TarafKimlik, telefon: str) -> dict[str, Any]:
    """`evaluate_consent`in TEK çağrı yeri. Alıcı SIKI normalleştiriciden geçer.

    K2 kararı: doğrulama `consents.normalize_msisdn` (sıkı) ile yapılır ve
    o dönüşüm `evaluate_consent`in KENDİ İÇİNDEDİR — buraya ham numarayı
    geçmek, defterin kendi kuralını uygulamasına izin vermektir.
    """
    return consents.evaluate_consent(
        db,
        company_id=kimlik.company_id,
        party_type=kimlik.party_type,
        party_id=kimlik.party_id,
        channel=KANAL,
        recipient=telefon,
    )


def _riza_yaz(db: Session, kimlik: TarafKimlik, telefon: str, *, verildi: bool) -> None:
    """Rızayı yazar VE denetim satırını düşürür. Commit ETMEZ.

    Olay satırı + versiyon artışı `set_consent`in İÇİNDE olur (ölçüldü,
    `consents.py`: `notification_consent_events` append-only); burada
    YENİDEN yazılmaz.
    """
    consents.set_consent(
        db,
        company_id=kimlik.company_id,
        party_type=kimlik.party_type,
        party_id=kimlik.party_id,
        channel=KANAL,
        granted=verildi,
        source="PHONE",
        source_ref=None,
        recipient=telefon,
        user_id=None,
    )
    log_activity(
        db,
        kimlik.company_id,
        None,
        (
            "party.whatsapp_consent_granted"
            if verildi
            else "party.whatsapp_consent_revoked"
        ),
        "whatsapp_party",
        kimlik.link_id or None,
        (
            "WhatsApp cari rızası verildi"
            if verildi
            else "WhatsApp cari rızası geri çekildi"
        ),
        {"party_type": kimlik.party_type, "party_id": kimlik.party_id},
    )


def _dur_isle(
    db: Session, adaylar: list[TarafKimlik], telefon: str, an: datetime
) -> str:
    """`DUR`/`İPTAL`: rızayı geri çeker VE bağlantıyı kapatır. İKİSİ BİRDEN.

    Keşif §5.4'ün gerekçesi: ikisi İKİ AYRI soruyu cevaplıyor — rıza
    "mesaj gönderebilir miyiz", bağlantı "bu numara kim". Yalnız rızayı
    çekmek, numarayı hâlâ çözülebilir bırakırdı ve `taraf_coz` onu
    yarın yine aday sayardı.

    ADAYLARIN HEPSİNE uygulanır: `DUR` diyen çiftçi TAMAMEN çıkmak
    istiyor. Firma seçimi SORULMAZ — "hangi firmadan çıkmak istersiniz"
    sorusu, çıkmak isteyen birine bir adım daha attırırdı.
    """
    for kimlik in adaylar:
        _riza_yaz(db, kimlik, telefon, verildi=False)
        if kimlik.link_id:
            taraf.baglantiyi_kapat(db, kimlik.company_id, kimlik.link_id, simdi=an)
    log.info("whatsapp: ciftci DUR isledi aday=%s", len(adaylar))
    return ciftci_niyet.DUR_MESAJI


@dataclass(frozen=True, slots=True)
class _FirmaSecimi:
    """`_firma_coz`un sonucu. ``kimlik is None`` → ``komut`` SORULACAK metindir.

    ``komut`` sıra öneki ATILMIŞ metindir ve `EVET`/`HAYIR`/niyet eşleşmesi
    YALNIZ ona yapılır. Runtime lens NO-GO (tur 1) tam olarak bunu ölçtü:
    eşleşme TAM metne yapılıyordu, "1 EVET" `evet_mi`den geçmiyordu ve
    çok firmalı hiçbir çiftçi rıza VEREMİYORDU — sonsuz KVKK döngüsü.

    ``sira`` çok firmalı seçimin numarasıdır (tek adayda ``None``); cevap
    metinleri çiftçiye aynı öneki öğretmek için onu taşır.
    """

    kimlik: TarafKimlik | None
    komut: str
    sira: int | None = None


def _firma_coz(
    db: Session, metin: str, adaylar: list[TarafKimlik]
) -> _FirmaSecimi:
    """Adaylardan BİRİNİ seçer; seçemezse sorulacak metni döner.

    RASTGELE SEÇİM YOKTUR — `taraf.taraf_coz`un cümlesi: belirsizlikte
    rastgele seçim, YANLIŞ tenant'ın verisini dönmek demektir. Seçim
    sözdiziminin neden `FİRMA SEÇ` DEĞİL de sıra öneki olduğu
    `ciftci_niyet.firma_secin_mesaji` başlığında ölçülerek yazılı.

    ÖNEKSİZ `EVET`/`HAYIR` N>1'de HİÇBİR ŞEY YAZMAZ (Şef kararı): liste ve
    önekli örnek döner. Bütün firmalara yaymak, çiftçinin adını görmediği
    firmalara da onay vermek; birini tahmin etmek rastgele seçim olurdu.
    """
    if len(adaylar) == 1:
        return _FirmaSecimi(adaylar[0], metin)

    adlar = [_firma_adi(db, aday.company_id) for aday in adaylar]
    if ciftci_niyet.listele_mi(metin):
        return _FirmaSecimi(None, ciftci_niyet.firma_secin_mesaji(adlar))

    sira, kalan = ciftci_niyet.sira_oneki_ayir(metin)
    if sira is None or not 1 <= sira <= len(adaylar):
        if ciftci_niyet.evet_mi(metin) or ciftci_niyet.hayir_mi(metin):
            return _FirmaSecimi(None, ciftci_niyet.firma_secin_riza_mesaji(adlar))
        return _FirmaSecimi(None, ciftci_niyet.firma_secin_mesaji(adlar))
    return _FirmaSecimi(adaylar[sira - 1], kalan, sira)


def ciftci_cevap(
    db: Session,
    telefon: str,
    metin: str,
    adaylar: list[TarafKimlik],
    *,
    medya_mi: bool,
    simdi: datetime,
    bugun: date,
) -> CiftciSonucu:
    """Çiftçi mesajının TAMAMI. Commit ETMEZ — çağıran (`service`) commit eder.

    SIRA SÖZLEŞMEDİR ve her adımın kendinden sonrakinden ÖNCE olmasının
    bir gerekçesi var:

      1. HIZ SINIRI — her şeyden önce. Sınırın üstündeki bir mesaj
         hiçbir sorgu koşturmamalı.
      2. `DUR`/`İPTAL` — firma çözümünden ÖNCE. Çıkmak isteyen çiftçiye
         önce firma seçtirmek, çıkışı zorlaştırmak olurdu.
      3. FİRMA ÇÖZÜMÜ — rızadan ÖNCE. Rıza `(firma, taraf)` başınadır;
         hangi defterin okunacağı bilinmeden sorulamaz.
      4. `EVET`/`HAYIR` — rıza KAPISINDAN önce. Kapı `NO_RECORD`da
         soruyu soruyor; cevabın kapıya takılması sonsuz döngü olurdu.
      5. RIZA KAPISI — HER ERP okumasından önce, HER mesajda yeniden.
      6. MEDYA — rızadan sonra, niyetten önce. Çiftçinin gönderdiği
         fotoğraf bir FATURA DEĞİLDİR; `fatura.medya_ozeti` personel
         yolunun aracıdır ve çiftçiye ERP özeti döndürürdü.
      7. NİYET.
    """
    sayac = mesaj_deneme_say(db, telefon, simdi=simdi)
    if sayac > schema.MESAJ_PENCERE_SINIRI:
        log.warning("whatsapp: ciftci mesaj siniri asildi, islenmedi")
        return CiftciSonucu(cevap="", cevapla=False, islendi=False)
    cevapla = sayac <= schema.MESAJ_CEVAP_SINIRI

    # `DUR` GLOBALDİR (bütün adaylar, başlık) ve önekli yazılışı da ("1 DUR")
    # AYNI komuttur: çıkmak isteyen çok firmalı çiftçi, firmaya soru
    # sorduğu biçimle de çıkabilmeli.
    if ciftci_niyet.dur_mu(metin) or ciftci_niyet.dur_mu(
        ciftci_niyet.sira_oneki_ayir(metin)[1]
    ):
        return CiftciSonucu(
            cevap=_dur_isle(db, adaylar, telefon, simdi),
            cevapla=cevapla,
            islendi=True,
        )

    secim = _firma_coz(db, metin, adaylar)
    kimlik, komut, sira = secim.kimlik, secim.komut, secim.sira
    if kimlik is None:
        return CiftciSonucu(cevap=komut, cevapla=cevapla, islendi=True)

    karar = _riza_degerlendir(db, kimlik, telefon)

    # `EVET`/`HAYIR` ÖNEKİ ATILMIŞ KOMUTLA eşleşir ve YALNIZ seçilen
    # `(firma, taraf)`a yazar — rıza firma başınadır (Şef kararı).
    if ciftci_niyet.evet_mi(komut):
        if karar["allowed"]:
            # Rıza ZATEN açık: tekrarlanan "EVET" sınırsız versiyon artışı
            # ve sınırsız olay satırı üretmesin (başlık).
            return CiftciSonucu(
                cevap=ciftci_niyet.CIFTCI_KAPSAM_MESAJI, cevapla=cevapla, islendi=True
            )
        _riza_yaz(db, kimlik, telefon, verildi=True)
        # İZ, KARAR DEĞİL (`taraf` modül başı): damga yalnız GRANTED'da
        # yazılır ve hiçbir izin kararı onu okumaz.
        if kimlik.link_id:
            taraf.riza_damgasi_yaz(db, kimlik.company_id, kimlik.link_id, simdi=simdi)
        return CiftciSonucu(
            cevap=ciftci_niyet.RIZA_ALINDI_MESAJI, cevapla=cevapla, islendi=True
        )

    if ciftci_niyet.hayir_mi(komut):
        # Kayıt YOKKEN (`REVOKED` v1) ve rıza AÇIKKEN (GRANTED → REVOKED)
        # yazar. Runtime lens tur 2: açık rızada hiçbir şey yazılmıyordu,
        # çiftçiye "göndermeyeceğiz" deniyor ve sonraki EKSTRE rakam
        # dönüyordu — KVKK beyanı yanlıştı. Zaten `REVOKED` olan deftere
        # ikinci bir `REVOKED` YAZILMAZ: tekrarlanan tek kelime sınırsız
        # versiyon artışı üretirdi (`EVET`in kuralıyla simetrik).
        if karar["allowed"] or karar["reason"] == consents.NO_RECORD:
            _riza_yaz(db, kimlik, telefon, verildi=False)
        return CiftciSonucu(
            cevap=ciftci_niyet.riza_reddedildi_mesaji(sira),
            cevapla=cevapla,
            islendi=True,
        )

    if not karar["allowed"]:
        # FAIL-CLOSED. `NO_RECORD` soruyu sorar; kalan üç gerekçe
        # (`REVOKED`, `RECIPIENT_CHANGED`, `RECIPIENT_INVALID`) AYNI genel
        # metni alır — ayırt edilemezlik (`ciftci_niyet` başlığı).
        if karar["reason"] == consents.NO_RECORD:
            cevap = ciftci_niyet.kvkk_metni(_firma_adi(db, kimlik.company_id), sira)
        else:
            cevap = ciftci_niyet.riza_kapali_mesaji(sira)
        return CiftciSonucu(cevap=cevap, cevapla=cevapla, islendi=True)

    if medya_mi:
        return CiftciSonucu(
            cevap=ciftci_niyet.CIFTCI_KAPSAM_MESAJI, cevapla=cevapla, islendi=True
        )

    niyet = ciftci_niyet.coz(komut, bugun=bugun)
    if niyet.mesaj is not None or niyet.arac is None:
        return CiftciSonucu(
            cevap=niyet.mesaj or ciftci_niyet.CIFTCI_KAPSAM_MESAJI,
            cevapla=cevapla,
            islendi=True,
        )

    try:
        veri = CiftciYurutucu(db, kimlik).kos(niyet.arac, niyet.argumanlar)
    except KeyError:
        # Beyaz liste dışı araç. Çözücü bunu üretemez; üretirse kullanıcıya
        # iç hata değil KAPSAM mesajı gider (`service.cevap_uret`in kuralı).
        log.warning("whatsapp: ciftci beyaz liste disi arac istendi")
        return CiftciSonucu(
            cevap=ciftci_niyet.CIFTCI_KAPSAM_MESAJI, cevapla=cevapla, islendi=True
        )
    return CiftciSonucu(
        cevap=cevap_yaz(niyet.arac, veri), cevapla=cevapla, islendi=True
    )


__all__ = [
    "CIFTCI_ARAC_GOVDELERI",
    "CIFTCI_BEYAZ_LISTESI",
    "CiftciSonucu",
    "CiftciYurutucu",
    "KANAL",
    "KANTAR_YOK_MESAJI",
    "LISTE_ADEDI",
    "MAKBUZ_YOK_MESAJI",
    "cevap_yaz",
    "ciftci_avans",
    "ciftci_cevap",
    "ciftci_ekstre",
    "ciftci_kantar",
    "ciftci_makbuz",
    "mesaj_deneme_say",
]
