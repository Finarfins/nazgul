"""e-İrsaliye: UBL-TR DespatchAdvice üretimi ve KENDİ durum makinesi.

Bu modül e-Fatura/e-Arşiv'in yanında durur, ONUN ÜSTÜNE YAZMAZ. İki ayrı
şey burada birlikte yaşıyor çünkü ikisi de aynı tek olguya bağlı: **bir
sevk irsaliyesi bir fatura değildir.**

--- NEDEN AYRI BİR DURUM MAKİNESİ ----------------------------------------

Keşif raporu (`docs/e4-eirsaliye-kesif-2026-09-09.md` §1 ve §5) bunu bir
P0 riski olarak yazıyor: *"`100` e-İrsaliye için durum güncellenmedi;
`101` kuyruğa eklendi. e-Arşiv eşlemesini taşımayın."*

`app/einvoice/endpoints.py::IZIBIZ_STATUS_ALIASES` içinde `"100"`,
`PENDING`'e eşleniyor — ve orada DOĞRUDUR: e-Arşiv'de 100 "KUYRUĞA
EKLENDİ" demektir. e-İrsaliye'de AYNI SAYI "durum güncellenmedi" demek.
O tabloyu ödünç almak, hiçbir şey olmamış bir belgeyi "kuyruğa alındı"
diye göstermek olurdu. Bu yüzden bu modülün kendi tablosu
(:data:`SAGLAYICI_KODLARI`) var ve `map_provider_status` HİÇ çağrılmıyor.

--- SEKİZ DURUM, TEK TERMİNAL --------------------------------------------

::

     (yok)                                          gönderim reddi
       │                                                  │
       │  submit()                                        ▼
       ├──────────► QUEUED ──► PROCESSING ──► SIGNED ──► SENT ──► DELIVERED
       │  (101)       (102/103/104)  (107)     (137)      (133)   ★terminal
       │                                                  │
       │  TIMEOUT / anlaşılmayan kod                      └──► FAILED (105/136)
       └──────────► UNKNOWN                                     ▲
                       │  status sorgusu "böyle bir belge yok"  │
                       └────────────────────────────────────────┘

``DELIVERED`` TEK terminaldir. ``REJECTED`` bu kümede **yoktur** ve bu
bir unutma değil: e-İrsaliye'de ret, alıcının gönderdiği bir
**ReceiptAdvice**'tan doğar ve o akış E4b'nin kapsamıdır (keşif §2.2:
`SendDespatchResponse` / `GetReceiptAdvice`). Bugün hiçbir sağlayıcı kodu
``REJECTED`` üretemez; üretemeyen bir durumu sözlüğe yazmak, gelecekte
birinin oraya rastgele bir kod eşlemesini kolaylaştırırdı.

--- ``UNKNOWN``: BİR CEVAP DEĞİL, BİR İTİRAF -----------------------------

``UNKNOWN`` iki farklı yerden doğar ve ikisi de "bilmiyorum" der:

1. **Gönderim TIMEOUT'a düştü.** Belge sağlayıcıya indi mi, bilmiyoruz.
   ``FAILED`` yazmak "inmedi" iddiasıdır ve YANLIŞ OLABİLİR; o iddiayla
   yapılacak ikinci gönderim, ilki inmişse İKİNCİ BİR İRSALİYE keser.
2. **Sağlayıcı anlamadığımız bir kod döndü** (106 dâhil — keşif §5 o
   kodun tabloda `SIGN_PROCESSING` ve `SIGN_FAILED` olarak İKİ KEZ
   geçtiğini ölçtü; çelişkili bir kodu eşlemek, imzalanmış bir belgeyi
   "imza başarısız" saymak olabilirdi).

Bu yüzden ``UNKNOWN`` :data:`GONDERIM_KAPALI` kümesindedir: durumu
``UNKNOWN`` olan bir belge YENİDEN GÖNDERİLEMEZ, önce
``app/routers/despatch_notes.py`` üzerinden bir durum sorgusu
çalıştırılmalıdır. Sorgu belgenin sağlayıcıda GERÇEKTEN OLMADIĞINI
söylerse :func:`bilinmeyeni_yok_say` düğümü ``FAILED``e çevirir ve
gönderim yeniden açılır. Çıkış kapısı ADIYLA vardır; ``UNKNOWN`` bir
çıkmaz sokak değildir.

--- İLERİ YÖNLÜ, VE ``UNKNOWN`` GERİYE YÜRÜYEMEZ -------------------------

:func:`durumu_ilerlet` ``status.py::advance_status`` ile AYNI ilkeyi
taşır ama AYNI FONKSİYON DEĞİLDİR ve olamaz: rank tablosu farklı, terminal
kümesi farklı, ``UNKNOWN``ın davranışı farklı. Ortak olan tek şey ilke:
geç gelen bir cevap ilerlemiş bir belgeyi geri çekemez.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from .errors import UblBuildError


# =========================================================================
# 1. DURUM MAKİNESİ
# =========================================================================

NONE = "NONE"
QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
SIGNED = "SIGNED"
SENT = "SENT"
DELIVERED = "DELIVERED"
FAILED = "FAILED"
#: "Bilmiyorum." Bir belge durumu değil, bilgimizin yokluğu — ama SAKLANIR,
#: çünkü bir TIMEOUT'tan sonra elimizde olan tam olarak budur.
UNKNOWN = "UNKNOWN"

#: Göç `20260913_0083`in `DURUMLAR` demetiyle BİREBİR aynı olmak zorunda;
#: eşitlik bir kapıdır (`test_DURUM_KUMESI_goc_ile_modul_ayni`). Ayrılırsa
#: CHECK kısıtı, uygulamanın yazabildiği bir değeri reddeder.
BILINEN: frozenset[str] = frozenset(
    {NONE, QUEUED, PROCESSING, SIGNED, SENT, DELIVERED, FAILED, UNKNOWN}
)

#: TEK terminal. Gerekçe modül başlığında: ret (`REJECTED`) ReceiptAdvice
#: akışından doğar ve o akış E4b'dir.
TERMINAL: frozenset[str] = frozenset({DELIVERED})

#: Bu durumlardayken YENİDEN GÖNDERİM YAPILMAZ. `UNKNOWN` buradadır ve
#: gerekçesi başlıkta: bilmediğimiz bir belgeyi tekrar göndermek, ilki
#: inmişse ikinci bir irsaliye keser.
GONDERIM_KAPALI: frozenset[str] = frozenset(
    {QUEUED, PROCESSING, SIGNED, SENT, DELIVERED, UNKNOWN}
)

#: İleri-yönlü sıralama. `NONE` ve `FAILED` rank 0'ı PAYLAŞIR: ikisi de
#: "sağlayıcıda canlı bir belge yok" demektir ve yeniden gönderim meşrudur.
#: `UNKNOWN` rank 1'dir, yani `FAILED`in ÜSTÜNDE — bir TIMEOUT'tan sonra
#: gelen "gönderim inmedi" iddiası bilgimizi GERİ ALAMAZ.
_RANK: dict[str, int] = {
    NONE: 0,
    FAILED: 0,
    UNKNOWN: 1,
    QUEUED: 2,
    PROCESSING: 3,
    SIGNED: 4,
    SENT: 5,
    DELIVERED: 6,
}

#: İzibiz'in e-İrsaliye durum kodları (keşif §5 tablosu). e-Arşiv/e-Fatura
#: tablosundan BAĞIMSIZ; `IZIBIZ_STATUS_ALIASES` buraya KARIŞMAZ.
#:
#: BURADA OLMAYAN HER KOD ``UNKNOWN``dır ve bu bir varsayılan değil bir
#: SÖZLEŞMEDİR: tanımadığımız bir kodu en yakın duruma yuvarlamak, sessizce
#: yanlış bir belge durumu göstermenin ta kendisi olurdu.
SAGLAYICI_KODLARI: dict[str, str] = {
    # 100 AÇIKÇA YAZILI ve AÇIKÇA `UNKNOWN`. Boş bırakılıp "tanınmayan kod"
    # dalına düşseydi sonuç aynı olurdu — ama o zaman bu satırın YOKLUĞU
    # bir unutkanlıktan ayırt edilemezdi. Keşif §5: e-İrsaliye'de 100
    # "durum GÜNCELLENMEDİ" demek, e-Arşiv'deki "KUYRUĞA EKLENDİ" DEĞİL.
    "100": UNKNOWN,
    "101": QUEUED,  # kuyruğa eklendi
    "102": PROCESSING,  # taslak işleme
    "103": PROCESSING,  # paketleme süreci
    "104": PROCESSING,  # paketleme başarılı
    "105": FAILED,  # paketleme hatası
    # 106 BİLEREK YOK. Keşif §5: sağlayıcı tablosunda `SIGN_PROCESSING` ve
    # `SIGN_FAILED` AYNI 106 koduyla veriliyor. Birini seçmek, imzalanmış
    # bir belgeyi "imza başarısız" (ya da tersi) saymak olurdu. Çelişkili
    # bir kodun doğru eşlemesi YOKTUR; `UNKNOWN` doğru cevaptır.
    "107": SIGNED,  # imzalandı
    "133": DELIVERED,  # alındı — TEK terminal
    "134": UNKNOWN,  # timeout: sağlayıcı da bilmiyor
    "135": PROCESSING,  # gönderim süreci
    "136": FAILED,  # gönderim hatası
    "137": SENT,  # gönderim başarılı
}

_NORMALIZE = str.maketrans(
    {
        "İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
        "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C",
        " ": "", "-": "", "_": "",
    }
)


def kodu_coz(deger: Any) -> str:
    """Sağlayıcı durum kodunu iç duruma çevir; tanımıyorsa ``UNKNOWN``.

    ``None``, boş gövde ve tanınmayan kod AYNI cevabı alır ve bu bilinçli:
    üçü de "durumu bilmiyoruz" demektir. Fark, ham kodun ayrı sütunda
    (``edespatch_gib_status_code``) saklanmasıyla korunur — operatör
    "sağlayıcı tam olarak ne dedi" sorusunu orada cevaplar.

    İç durum adlarının kendisi de kabul edilir (``"QUEUED"`` → ``QUEUED``):
    bir yeniden okuma yolu, saklanmış değeri buradan geçirebilsin.
    """
    if deger is None:
        return UNKNOWN
    jeton = str(deger).strip().translate(_NORMALIZE).upper()
    if not jeton:
        return UNKNOWN
    if jeton in BILINEN:
        return jeton
    return SAGLAYICI_KODLARI.get(jeton, UNKNOWN)


def durumu_ilerlet(mevcut: Any, gelen: Any) -> str:
    """Tek bir bildirimi mevcut duruma uygula. İleri-yönlü.

    Kurallar, sırayla:

    1. Terminal (``DELIVERED``) hiçbir şeyle değişmez.
    2. ``UNKNOWN`` GELEN olarak: canlı bir belge biliyorsak bu bir GEÇİŞ
       DEĞİLDİR (cevaplanamayan sorgu belgeyi olduğu yerde bırakır);
       hiçbir şey bilmiyorsak (``NONE``/``FAILED``) SAKLANIR, çünkü
       gönderim TIMEOUT'u tam olarak o boşluğu doldurur.
    3. Geri kalan her şey rank karşılaştırmasıdır: düşük rank yüksek
       rankı ezemez. Geç gelen bir ``QUEUED``, ``SENT`` olmuş bir belgeyi
       kuyruğa geri koyamaz.
    """
    mevcut_durum = str(mevcut or NONE).strip().upper() or NONE
    gelen_durum = str(gelen or NONE).strip().upper() or NONE
    if mevcut_durum not in BILINEN:
        mevcut_durum = NONE
    if gelen_durum not in BILINEN:
        gelen_durum = UNKNOWN

    if mevcut_durum in TERMINAL:
        return mevcut_durum
    if gelen_durum == UNKNOWN:
        return UNKNOWN if _RANK[mevcut_durum] == 0 else mevcut_durum
    if _RANK[gelen_durum] < _RANK[mevcut_durum]:
        return mevcut_durum
    return gelen_durum


def bilinmeyeni_yok_say(mevcut: Any) -> str:
    """``UNKNOWN``ın TEK çıkış kapısı: sağlayıcı "böyle bir belge yok" dedi.

    :func:`durumu_ilerlet` bunu YAPAMAZ ve yapmamalı: orada ``FAILED``
    rank 0'dır ve ``UNKNOWN``ı (rank 1) ezemez — tam da bir TIMEOUT'tan
    sonra gelen rastgele bir başarısızlığın bilgimizi silmesini engellemek
    için. Ama sağlayıcı belgenin VARLIĞINI olumsuzladıysa bu rastgele bir
    başarısızlık değil, POZİTİF bir cevaptır: gönderim inmemiş.

    Ayrı bir ADI olması bunu kaza eseri çağrılamaz kılıyor. ``UNKNOWN``
    dışındaki her durumda hiçbir şey yapmaz.
    """
    mevcut_durum = str(mevcut or NONE).strip().upper() or NONE
    return FAILED if mevcut_durum == UNKNOWN else mevcut_durum


# =========================================================================
# 2. UBL-TR DespatchAdvice
# =========================================================================

#: Keşif §3.1: okunan OASIS şeması UBL 2.1, GİB teknik belgesi
#: `CustomizationID=TR1.2.1` ve `ProfileID=TEMELIRSALIYE` diyor. "UBL-TR
#: 1.2" ile `UBLVersionID=1.2` KARIŞTIRILMAMALI — ikincisi 2.1'dir.
UBL_VERSION = "2.1"
CUSTOMIZATION_ID = "TR1.2.1"
PROFILE_ID = "TEMELIRSALIYE"
#: `cbc:DespatchAdviceTypeCode`. Mal sevki için tek değer.
TYPE_CODE = "SEVK"

#: TCKN 11 hane, VKN 10 (`ubl_xml.py::_identifier_scheme` ile AYNI kural;
#: kopyalanmadı, aşağıda kendi hata metniyle yeniden yazıldı çünkü buradaki
#: alan adları farklı — "taşıyıcı VKN", "şoför TCKN").
TCKN_LENGTH = 11
VKN_LENGTH = 10

#: Keşif §3.2: örnekte `schemeID=PLAKA` ve `schemeID=DORSEPLAKA`.
PLAKA_SCHEME = "PLAKA"
DORSE_SCHEME = "DORSEPLAKA"

#: Miktar birim kodu varsayılanı. Keşif §3.2: örnekte C62; GÜNCEL KOD
#: LİSTESİ DOĞRULANMADI, o yüzden çağıran kendi kodunu verebilir.
DEFAULT_UNIT_CODE = "C62"

_NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
    'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"'
)


def _m(deger: Any) -> str:
    return xml_escape("" if deger is None else str(deger))


def _rakamlar(deger: Any) -> str:
    return "".join(ch for ch in str(deger or "") if ch.isdigit())


def _kimlik_semasi(numara: Any, alan: str) -> str:
    """VKN mi TCKN mi — UZUNLUKTAN. Bilinmiyorsa VKN VARSAYILMAZ, hata verilir.

    ``ubl_xml.py``deki kardeşiyle aynı kural: 10 hane VKN, 11 hane TCKN,
    başka her şey bir kusurdur. Sessizce VKN varsaymak, gerçek kişiyi tüzel
    kişi olarak beyan etmek olurdu.
    """
    hane = _rakamlar(numara)
    if len(hane) == TCKN_LENGTH:
        return "TCKN"
    if len(hane) == VKN_LENGTH:
        return "VKN"
    raise UblBuildError(f"{alan} 10 veya 11 haneli olmalı: {len(hane)} hane")


def _taraf(*, numara: Any, ad: Any, adres: Any, vergi_dairesi: Any = None) -> str:
    """Tek bir taraf (gönderen/alıcı). TCKN ⇒ gerçek kişi, VKN ⇒ tüzel kişi.

    Keşif §3.2: taraf kimliği `cac:PartyIdentification/cbc:ID[@schemeID]`
    altında; TR kılavuzu kimlik VE adres istiyor.
    """
    sema = _kimlik_semasi(numara, "Taraf VKN/TCKN")
    hane = _rakamlar(numara)
    parcalar = [
        "<cac:Party>",
        f'<cac:PartyIdentification><cbc:ID schemeID="{sema}">{_m(hane)}'
        "</cbc:ID></cac:PartyIdentification>",
    ]
    if sema == "VKN":
        parcalar.append(f"<cac:PartyName><cbc:Name>{_m(ad)}</cbc:Name></cac:PartyName>")
    parcalar.append(
        "<cac:PostalAddress>"
        f"<cbc:StreetName>{_m(adres or '')}</cbc:StreetName>"
        "<cbc:CitySubdivisionName>-</cbc:CitySubdivisionName>"
        "<cbc:CityName>-</cbc:CityName>"
        "<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country>"
        "</cac:PostalAddress>"
    )
    if sema == "VKN":
        parcalar.append(
            "<cac:PartyTaxScheme><cac:TaxScheme>"
            f"<cbc:Name>{_m(vergi_dairesi or '-')}</cbc:Name>"
            "</cac:TaxScheme></cac:PartyTaxScheme>"
        )
    else:
        kelimeler = str(ad or "").split()
        ilk = " ".join(kelimeler[:-1]) if len(kelimeler) > 1 else (kelimeler[0] if kelimeler else "-")
        soy = kelimeler[-1] if len(kelimeler) > 1 else "-"
        parcalar.append(
            f"<cac:Person><cbc:FirstName>{_m(ilk)}</cbc:FirstName>"
            f"<cbc:FamilyName>{_m(soy)}</cbc:FamilyName></cac:Person>"
        )
    parcalar.append("</cac:Party>")
    return "".join(parcalar)


#: BELGE SAAT DİLİMİ. UBL-TR ``ActualDespatchTime`` bir YEREL saattir:
#: irsaliyeyi okuyan denetçi malın Türkiye saatiyle kaçta yola çıktığını
#: görmek ister, UTC'de kaçta olduğunu değil.
#:
#: SABİT +03:00, ``ZoneInfo("Europe/Istanbul")`` DEĞİL — ve bu ÖLÇÜLMÜŞ bir
#: karar: Windows'ta ``zoneinfo`` ayrı bir ``tzdata`` paketi ister ve o
#: paket yoksa çağrı ``ZoneInfoNotFoundError`` ile düşer. İki koşu
#: ortamının biri tzdata'lı biri tzdatasız olsaydı AYNI satır İKİ FARKLI
#: saat beyan ederdi — bu sabitin engellemek için var olduğu şeyin ta
#: kendisi. Türkiye 2016'dan beri KALICI UTC+3'tür ve yaz saati
#: uygulamaz, yani bu sabit bugün kesilen her belge için DOĞRUDUR.
#: SINIR AÇIK: 2016 ÖNCESİNE tarihlenen bir sevk için yanlış olurdu; böyle
#: bir belge bu sistemde üretilmiyor.
BELGE_UTC_OFFSET = timezone(timedelta(hours=3))


def _an_coz(deger: Any) -> datetime:
    """Fiili sevk anını BELGE SAAT DİLİMİNE normalleştir.

    İKİ AŞAMA VE İKİSİ DE ZORUNLU:

    1. **Naive bir değer REDDEDİLMEZ, UTC sayılır.** SQLite
       ``DateTime(timezone=True)`` sütununu naive geri verir (sürücü
       offset saklamaz), yani depodan okunan her SQLite değeri bu daldan
       geçer. Uç katmanı yazarken zaten UTC'ye normalleştiriyor, o yüzden
       varsayım bir tahmin değil.

    2. **Sonuç :data:`BELGE_UTC_OFFSET`e ÇEVRİLİR.** Bu adım ÖLÇÜMLE
       eklendi ve eksikliği GERÇEK BİR KUSURDU: PostgreSQL aynı anı OTURUM
       SAAT DİLİMİNDE döndürüyor (ölçüldü: ``SHOW TimeZone`` ->
       ``Europe/Istanbul``), yani ``08:30+00:00`` yazılan bir satır
       ``11:30+03:00`` olarak geri geliyordu. Çevirim olmadan
       ``strftime`` o offset'in YEREL duvar saatini basıyor ve AYNI SATIR
       iki diyalektte İKİ FARKLI ``ActualDespatchTime`` üretiyordu
       (SQLite ``08:30:00``, PG ``11:30:00``). Daha kötüsü: PG tarafındaki
       değer SUNUCUNUN yapılandırmasına bağlıydı, yani belgenin içeriği
       veritabanı ayarıyla değişiyordu.

       Kapı: ``test_e4a_despatch_notes_postgresql.py::
       test_GIDIS_DONUS_PGden_okunan_satir_UBL_uretiyor``. Bu kusuru
       SQLite ikizi GÖREMEZ ve PG ikizinin var oluş sebebi tam olarak
       budur.
    """
    if isinstance(deger, datetime):
        an = deger
    else:
        metin = str(deger or "").strip()
        if not metin:
            raise UblBuildError("Fiili sevk zamanı zorunlu")
        try:
            an = datetime.fromisoformat(metin.replace("Z", "+00:00"))
        except ValueError:
            raise UblBuildError(f"Fiili sevk zamanı okunamadı: {metin[:40]}") from None
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an.astimezone(BELGE_UTC_OFFSET)


def _gun_coz(deger: Any) -> str:
    """Düzenleme tarihi → ``YYYY-MM-DD``."""
    if isinstance(deger, datetime):
        return deger.date().isoformat()
    if isinstance(deger, date):
        return deger.isoformat()
    metin = str(deger or "").strip()
    if not metin:
        raise UblBuildError("Düzenleme tarihi zorunlu")
    return metin[:10]


def _sevkiyat(payload: dict[str, Any]) -> str:
    """`cac:Shipment` — keşif §3.2'nin satır satır karşılığı.

    TAŞIMA İKİ DALLI (keşif §3.2, [G §9-10]): plaka+şoför **veya**
    kargo/lojistik firması. Bu fonksiyon hangi dalın DOLU olduğuna bakar
    ve yalnız onu yazar; ikisini birden zorunlu tutmaz. E4a'nın kendi
    (daha dar) kuralı uç katmanındadır.

    ``ActualDespatchDate`` ve ``ActualDespatchTime`` TEK bir andan bölünür;
    depoda ayrı iki sütun yoktur (göç 0083'ün başlığında gerekçe).
    """
    sevk = payload.get("shipment") or {}
    an = _an_coz(sevk.get("actual_shipment_at"))
    parcalar = [
        "<cac:Shipment>",
        # Shipment varsa `cbc:ID` XSD'de ZORUNLU (keşif §3.2, [C]). Sevk
        # kimliği olarak irsaliyenin ETTN'i kullanılıyor: sabit, tekil ve
        # zaten belgenin kendisini adresliyor.
        f"<cbc:ID>{_m(payload.get('uuid'))}</cbc:ID>",
    ]
    brut = sevk.get("gross_weight")
    if brut is not None and str(brut).strip():
        parcalar.append(
            f'<cbc:GrossWeightMeasure unitCode={quoteattr(str(sevk.get("weight_unit") or "KGM"))}>'
            f"{_m(brut)}</cbc:GrossWeightMeasure>"
        )
    # --- ShipmentStage: şoför ve araç ---
    plaka = str(sevk.get("vehicle_plate") or "").strip()
    sofor_ad = str(sevk.get("driver_name") or "").strip()
    sofor_tckn = str(sevk.get("driver_national_id") or "").strip()
    if plaka or sofor_ad:
        asama = ["<cac:ShipmentStage>", "<cbc:ID>1</cbc:ID>"]
        if plaka:
            asama.append(
                "<cac:TransportMeans><cac:RoadTransport>"
                f'<cbc:LicensePlateID schemeID="{PLAKA_SCHEME}">{_m(plaka)}'
                "</cbc:LicensePlateID>"
                "</cac:RoadTransport></cac:TransportMeans>"
            )
        if sofor_ad:
            kelimeler = sofor_ad.split()
            ilk = " ".join(kelimeler[:-1]) if len(kelimeler) > 1 else kelimeler[0]
            soy = kelimeler[-1] if len(kelimeler) > 1 else "-"
            sofor = ["<cac:DriverPerson>"]
            sofor.append(f"<cbc:FirstName>{_m(ilk)}</cbc:FirstName>")
            sofor.append(f"<cbc:FamilyName>{_m(soy)}</cbc:FamilyName>")
            if sofor_tckn:
                # Keşif §3.2: örnekte TCKN bu alanda — `NationalityID`.
                # Adı yanıltıcı ("uyruk" gibi okunuyor) ama ölçülen yer bu;
                # başka bir alana yazmak sağlayıcının okumadığı yere yazmaktır.
                sofor.append(f"<cbc:NationalityID>{_m(_rakamlar(sofor_tckn))}</cbc:NationalityID>")
            sofor.append("</cac:DriverPerson>")
            asama.append("".join(sofor))
        asama.append("</cac:ShipmentStage>")
        parcalar.append("".join(asama))
    # --- Dorse ---
    dorse = str(sevk.get("trailer_plate") or "").strip()
    if dorse:
        parcalar.append(
            "<cac:TransportHandlingUnit><cac:TransportEquipment>"
            f'<cbc:ID schemeID="{DORSE_SCHEME}">{_m(dorse)}</cbc:ID>'
            "</cac:TransportEquipment></cac:TransportHandlingUnit>"
        )
    # --- Delivery: fiili sevk anı, teslim adresi, taşıyıcı ---
    teslim = ["<cac:Delivery>"]
    adres = str(sevk.get("delivery_address") or "").strip()
    if adres:
        teslim.append(
            "<cac:DeliveryAddress>"
            f"<cbc:StreetName>{_m(adres)}</cbc:StreetName>"
            "<cbc:CitySubdivisionName>-</cbc:CitySubdivisionName>"
            "<cbc:CityName>-</cbc:CityName>"
            "<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country>"
            "</cac:DeliveryAddress>"
        )
    tasiyici_ad = str(sevk.get("carrier_name") or "").strip()
    tasiyici_vkn = str(sevk.get("carrier_tax_number") or "").strip()
    if tasiyici_ad or tasiyici_vkn:
        tasiyici = ["<cac:CarrierParty>"]
        if tasiyici_vkn:
            sema = _kimlik_semasi(tasiyici_vkn, "Taşıyıcı VKN/TCKN")
            tasiyici.append(
                f'<cac:PartyIdentification><cbc:ID schemeID="{sema}">'
                f"{_m(_rakamlar(tasiyici_vkn))}</cbc:ID></cac:PartyIdentification>"
            )
        if tasiyici_ad:
            tasiyici.append(f"<cac:PartyName><cbc:Name>{_m(tasiyici_ad)}</cbc:Name></cac:PartyName>")
        tasiyici.append("</cac:CarrierParty>")
        teslim.append("".join(tasiyici))
    teslim.append(
        "<cac:Despatch>"
        f"<cbc:ActualDespatchDate>{_m(an.date().isoformat())}</cbc:ActualDespatchDate>"
        f"<cbc:ActualDespatchTime>{_m(an.strftime('%H:%M:%S'))}</cbc:ActualDespatchTime>"
        "</cac:Despatch>"
    )
    teslim.append("</cac:Delivery>")
    parcalar.append("".join(teslim))
    parcalar.append("</cac:Shipment>")
    return "".join(parcalar)


def _miktar(deger: Any) -> str:
    """Miktarı METİN olarak taşı; ``float``a HİÇ uğratma.

    Depodan ``Decimal`` geliyor. ``float``a çevirmek 3 ondalıklı bir
    miktarı 2.9999999'a döndürebilir ve sevk edilen miktar FATURADAKİNDEN
    farklı görünürdü — E4a'nın tek eşitlik iddiası tam olarak budur.
    """
    if deger is None:
        raise UblBuildError("İrsaliye satırında miktar zorunlu")
    if isinstance(deger, float):
        raise UblBuildError("Miktar float olarak verilemez; Decimal ya da metin")
    metin = str(deger).strip()
    try:
        Decimal(metin)
    except (InvalidOperation, ValueError):
        raise UblBuildError(f"Miktar sayı değil: {metin[:40]}") from None
    return metin


def build_despatch_xml(payload: dict[str, Any]) -> bytes:
    """``despatch_notes`` + fatura sözlüğünü UBL-TR DespatchAdvice'a çevir.

    Eksik zorunlu alanda :class:`UblBuildError` fırlatır; yarım bir belge
    üretip sağlayıcının reddetmesini beklemez (``ubl_xml.build_invoice_xml``
    ile AYNI sözleşme).

    OASIS kökünde zorunlu olanlar (keşif §3.1): ``ID``, ``IssueDate``,
    ``DespatchSupplierParty``, ``DeliveryCustomerParty``, ``DespatchLine``
    (1+). TR kılavuzu ayrıca profil, UUID, saat, tip ve ``Shipment`` ister
    — beşi de aşağıda YAZILIYOR ve hiçbiri opsiyonel dalda değil.
    """
    payload = payload or {}
    belge_no = str(payload.get("despatch_number") or "").strip()
    ettn = str(payload.get("uuid") or "").strip()
    if not belge_no or not ettn:
        raise UblBuildError("İrsaliye için belge numarası ve ETTN zorunlu")
    duzenleme = _gun_coz(payload.get("issue_date"))
    an = _an_coz((payload.get("shipment") or {}).get("actual_shipment_at"))

    supplier = payload.get("supplier") or {}
    customer = payload.get("customer") or {}
    satirlar = payload.get("lines") or []
    if not satirlar:
        raise UblBuildError("İrsaliye için en az bir satır zorunlu")

    govde = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<DespatchAdvice {_NS}>",
        "<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/>"
        "</ext:UBLExtension></ext:UBLExtensions>",
        f"<cbc:UBLVersionID>{UBL_VERSION}</cbc:UBLVersionID>",
        f"<cbc:CustomizationID>{CUSTOMIZATION_ID}</cbc:CustomizationID>",
        f"<cbc:ProfileID>{PROFILE_ID}</cbc:ProfileID>",
        f"<cbc:ID>{_m(belge_no)}</cbc:ID>",
        "<cbc:CopyIndicator>false</cbc:CopyIndicator>",
        f"<cbc:UUID>{_m(ettn)}</cbc:UUID>",
        f"<cbc:IssueDate>{_m(duzenleme)}</cbc:IssueDate>",
        # Düzenleme SAATİ fiili sevk saatinden AYRI bir alandır; ikisi
        # aynı andan türese de aynı eleman DEĞİLDİR (keşif §3.2).
        f"<cbc:IssueTime>{_m(an.strftime('%H:%M:%S'))}</cbc:IssueTime>",
        f"<cbc:DespatchAdviceTypeCode>{TYPE_CODE}</cbc:DespatchAdviceTypeCode>",
        f"<cbc:LineCountNumeric>{len(satirlar)}</cbc:LineCountNumeric>",
    ]
    not_metni = str(payload.get("note") or "").strip()
    if not_metni:
        govde.append(f"<cbc:Note>{_m(not_metni)}</cbc:Note>")
    # FATURA REFERANSI: önce faturalanmış malın sevki. Keşif §4.2 bunu
    # açıkça istiyor ve E4a'nın akışı TAM OLARAK budur (fatura önce, sevk
    # sonra) — referans UYDURULMUYOR, elimizdeki faturanın numarası.
    fatura_no = str(payload.get("invoice_number") or "").strip()
    if fatura_no:
        govde.append(
            "<cac:OrderReference>"
            f"<cbc:ID>{_m(fatura_no)}</cbc:ID>"
            f"<cbc:IssueDate>{_m(str(payload.get('invoice_issue_date') or duzenleme)[:10])}</cbc:IssueDate>"
            "</cac:OrderReference>"
        )
    # İmza bloğu: mührü sağlayıcı atar, ama UBL-TR yapıyı yine de ister
    # (`ubl_xml.build_invoice_xml` ile aynı gerekçe).
    gonderen_vkn = _rakamlar(supplier.get("vkn"))
    govde.append(
        "<cac:Signature>"
        f'<cbc:ID schemeID="VKN_TCKN">{_m(gonderen_vkn)}</cbc:ID>'
        "<cac:SignatoryParty>"
        f'<cac:PartyIdentification><cbc:ID schemeID="VKN">{_m(gonderen_vkn)}</cbc:ID>'
        "</cac:PartyIdentification>"
        "<cac:PostalAddress><cbc:CitySubdivisionName>-</cbc:CitySubdivisionName>"
        "<cbc:CityName>-</cbc:CityName>"
        "<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress>"
        "</cac:SignatoryParty>"
        "<cac:DigitalSignatureAttachment><cac:ExternalReference>"
        "<cbc:URI>#Signature</cbc:URI>"
        "</cac:ExternalReference></cac:DigitalSignatureAttachment>"
        "</cac:Signature>"
    )
    govde.append("<cac:DespatchSupplierParty>")
    govde.append(
        _taraf(
            numara=supplier.get("vkn"),
            ad=supplier.get("name"),
            adres=supplier.get("address"),
            vergi_dairesi=supplier.get("tax_office"),
        )
    )
    govde.append("</cac:DespatchSupplierParty>")
    govde.append("<cac:DeliveryCustomerParty>")
    govde.append(
        _taraf(
            numara=customer.get("vkn_tckn"),
            ad=customer.get("name"),
            adres=customer.get("address"),
            vergi_dairesi=customer.get("tax_office"),
        )
    )
    govde.append("</cac:DeliveryCustomerParty>")
    govde.append(_sevkiyat(payload))
    for sira, satir in enumerate(satirlar, start=1):
        birim = str(satir.get("unit_code") or DEFAULT_UNIT_CODE)
        govde.append(
            "<cac:DespatchLine>"
            f"<cbc:ID>{_m(satir.get('id') or sira)}</cbc:ID>"
            f"<cbc:DeliveredQuantity unitCode={quoteattr(birim)}>"
            f"{_m(_miktar(satir.get('quantity')))}</cbc:DeliveredQuantity>"
            # `OrderLineReference/LineID` OASIS'te 1+ (keşif §3.2). Değer
            # UYDURULMUYOR: düz sevkte irsaliye satırı fatura satırının
            # birebir karşılığıdır, yani satır sırası REFERANSIN KENDİSİDİR.
            f"<cac:OrderLineReference><cbc:LineID>{sira}</cbc:LineID>"
            "</cac:OrderLineReference>"
            f"<cac:Item><cbc:Name>{_m(satir.get('name'))}</cbc:Name></cac:Item>"
            "</cac:DespatchLine>"
        )
    govde.append("</DespatchAdvice>")
    return "".join(govde).encode("utf-8")


def package_despatch(payload: dict[str, Any]) -> bytes:
    """Gönderilecek içerik = tek dosyalık ZIP'in HAM baytları.

    e-Arşiv'de ölçülen kural (``ubl_xml`` başlığı, kural 1: ``ERROR_CODE=
    10007 "Zip bir dosya içermelidir."``) burada da uygulanıyor ve bu bir
    VARSAYIMDIR — e-İrsaliye ``DESPATCHADVICE/CONTENT`` alanının ZIP mi
    çıplak XML mi beklediği sandbox'ta ÖLÇÜLMEDİ. Keşif §2.3 sağlayıcının
    ``COMPRESSED=N`` ile base64 içerik istediğini söylüyor; ``COMPRESSED``
    bayrağı SIKIŞTIRMANIN SOAP KATMANINDA olup olmadığını bildirir,
    içeriğin kendisinin ZIP olup olmadığını DEĞİL — e-Arşiv'de de
    ``COMPRESSED=N`` gönderiliyor ve içerik yine ZIP.

    Aynı ZIP yardımcısı kullanılıyor (``ubl_xml.zip_single``): DETERMİNİSTİK,
    yani aynı irsaliye her seferinde bayt bayt aynı paketi üretir ve
    idempotent bir yeniden gönderim sağlayıcıya farklı bir belge gibi
    görünmez. Sabit ``despatch_uuid`` ile birlikte, çift belgeye karşı iki
    ayrı koruma.
    """
    from .ubl_xml import zip_single

    xml = build_despatch_xml(payload)
    return zip_single(f"{payload.get('despatch_number')}.xml", xml)
