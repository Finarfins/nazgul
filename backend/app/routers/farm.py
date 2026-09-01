"""Tarla Yönetimi V1 — CRUD API ve dashboard (mobil-erp#2, FAZ 2).

BU MODÜLDE TEKRARLANAN ÜÇ KURAL:

1. **Her okuma ve yazma kiracı filtreli.** Tek bir sorgu bile ``company_id``
   olmadan yazılmıyor. Başka firmanın kimliği verildiğinde **404** dönüyor,
   403 değil — 403 "var ama sana kapalı" bilgisini sızdırır.

2. **Toplam maliyet sunucuda türetilir.** İstemci ``total_cost`` gönderemez
   (şemada alan yok); ``quantity * unit_cost`` Decimal ile hesaplanır. Sezon
   maliyeti ve dekar başına maliyet doğrudan bu değerden geliyor.

3. **Güncellemede iyimser kilit.** İstemci gördüğü ``updated_at``i geri
   gönderir; tutmazsa 409. Tarla kayıtları sahada telefondan ve ofiste
   masaüstünden düzenleniyor, son yazan kazanır kabul edilemez.

4. **Oran satıra KOPYALANIR, okunmaz** (Gerçek Maliyet FAZ 2, mobil-erp#24).
   İşçilik/makine saatlik oranı faaliyet yazılırken `cost_rates`ten çözülüp
   satıra donduruluyor; okuma yolu bir daha o tabloya BAKMIYOR. Oranı
   değiştirmek geçmiş maliyeti değiştirmemeli — aksi hâlde geçen sezonun kârı
   bu yıl başka çıkardı. Ayrıntı `_oran_kopyala` ve migration 0053'te.

V1 STOK/MUHASEBEYE FİŞ YAZMAZ. Girdi bağlamak depo stoğunu düşürmez; bu
bilinçli (bkz. migration 0044 başlığı).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..farm_schemas import (
    ActivityInputWrite,
    ActivityWrite,
    FarmUpdate,
    FarmWrite,
    HarvestWrite,
    ParcelUpdate,
    ParcelWrite,
    PlantProtectionProductUpdate,
    PlantProtectionProductWrite,
    SeasonUpdate,
    SeasonWrite,
    TaskUpdate,
    TaskWrite,
)
from ..auth import has_permission, required_permission
from ..business_time import ISTANBUL, business_today
from ..money import money, quantity
from ..tenancy import company_id
# İÇE AKTARMA OKUYUCUSU PAYLAŞILIYOR, KOPYALANMIYOR (göç 20260901_0064).
# `routers/imports.py`teki bu üç yardımcı yükleme yüzeyinin SINIRLARINI
# taşıyor: 10 MB gövde tavanı, 50.000 satır tavanı, UTF-8/cp1254 çözümü ve
# zip-bomba kontrolü. İkinci bir kopya yazmak o tavanların BİRİNİ unutmak
# demekti ve unutulan tavan yalnız saldırı anında görünürdü. Uç `imports`
# yönlendiricisine DEĞİL, `farm`a bağlı kalıyor: `/api/plant-protection-# products` öneki `farm.manage` istiyor, `/api/imports` istemiyor — uç orada
# olsaydı yasal bekleme sürelerini içe aktarma yetkisi olan herkes yazabilirdi.
from .imports import _cell, _map, _read_tabular_upload

router = APIRouter(tags=["farm"])

_SAYFA = Query(default=50, ge=1, le=200)
_ATLA = Query(default=0, ge=0, le=100_000)


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


def _kullanici_id(request: Request) -> int | None:
    user = getattr(request.state, "user", {}) or {}
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _satir(db: Session, cid: int, tablo: str, kayit_id: int) -> dict[str, Any]:
    """Kiracı kapsamlı tekil okuma.

    ``tablo`` YALNIZ bu modülün sabit listesinden gelir; istekten gelen bir
    değer buraya asla ulaşmaz (aşağıdaki uçların hepsi sabit isim veriyor).
    """
    if tablo not in _TABLOLAR:
        raise HTTPException(500, "Geçersiz tablo")
    row = db.execute(
        text(f"SELECT * FROM {tablo} WHERE id=:id AND company_id=:cid"),
        {"id": kayit_id, "cid": cid},
    ).mappings().first()
    if row is None:
        # Başka firmanın kaydı da buraya düşer: 404, bilgi sızdırmamak için.
        raise HTTPException(404, "Kayıt bulunamadı")
    return dict(row)


_TABLOLAR = frozenset({
    "farms", "farm_parcels", "crop_seasons", "field_activities",
    "field_activity_inputs", "field_harvests", "field_tasks",
    "plant_protection_products",
})


def _surum_dogrula(mevcut: dict[str, Any], beklenen: datetime) -> Any:
    """İyimser kilit.

    Mikrosaniye sürümün parçasıdır. Aynı saniyede yapılan iki yazmayı eşit
    saymak, ikinci yazmanın ilkini sessizce ezmesine izin verirdi.
    """
    var = mevcut.get("updated_at")
    if var is None:
        raise HTTPException(409, "Kayıt sürümü okunamadı; yenileyip tekrar deneyin")
    karsilastirilan = datetime.fromisoformat(var) if isinstance(var, str) else var
    if karsilastirilan.tzinfo is None:
        karsilastirilan = karsilastirilan.replace(tzinfo=timezone.utc)
    gelen = beklenen if beklenen.tzinfo else beklenen.replace(tzinfo=timezone.utc)
    if karsilastirilan.astimezone(timezone.utc) != gelen.astimezone(timezone.utc):
        raise HTTPException(409, "Kayıt siz düzenlerken değişti; yenileyip tekrar deneyin")
    # SQL compare-and-set için veritabanından GELEN ham değeri kullanılır.
    # Böylece SQLite metni ve PostgreSQL datetime bağlaması kendi diyalektinde
    # birebir karşılaştırılır.
    return var


def _cas_sonuc_dogrula(db: Session, sonuc: Any) -> None:
    """Compare-and-set UPDATE tam bir satır değiştirmediyse yarış kaybedildi."""
    if sonuc.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Kayıt siz düzenlerken değişti; yenileyip tekrar deneyin")


def _hasat_yaniti(satir: dict[str, Any]) -> dict[str, Any]:
    """Hasat satış alanlarını sabit ölçekli Decimal metin sözleşmesine çevirir."""
    sold = satir.get("sold_quantity")
    revenue = satir.get("revenue_amount")
    return {
        **satir,
        "sold_quantity": None if sold is None else format(quantity(sold), "f"),
        "revenue_amount": None if revenue is None else format(money(revenue), "f"),
    }


def _tutar(saat: Any, oran: Any) -> str | None:
    """Bir maliyet kalemi: `saat × oran`, TEK yerde yuvarlanıp METİN olarak.

    Değer SAKLANMIYOR, her okumada donmuş iki çarpandan türetiliyor (aynı
    yaklaşım: `work_order_labor_lines._line_total`). İkisi de satırda durduğu
    için sonuç kayıt anındaki oranla sonsuza kadar aynı çıkar.

    Oran YOKSA sonuç ``None`` — SIFIR DEĞİL. Sıfır "bu iş bedava yapıldı"
    demektir; oranı tanımlanmamış bir işin maliyeti ise BİLİNMİYOR ve ikisini
    aynı değere indirmek sezon maliyetini sessizce eksik gösterirdi (aynı
    ayrım: `field_harvests.revenue_amount`).
    """
    if saat is None or oran is None:
        return None
    return format(money(quantity(saat) * money(oran)), "f")


# ---------------------------------------------------------------------------
# ORAN OKUMA MASKESİ (Gerçek Maliyet FAZ 2)
# ---------------------------------------------------------------------------
#
# FAZ 1 `GET /api/cost-rates`i BİLİNÇLİ olarak `finance`e bağladı: "oran bir
# para tanımı ve OKUNMASI da firmanın maliyet yapısını açar; genel `read`e
# düşseydi girdi giren depo rolü de görebilirdi."
#
# FAZ 2 aynı sayıyı `field_activities` satırına koydu ve o satır `farm.view`
# ile okunuyor — yani `depo` ve `satis` de oranı görürdü. Bu, FAZ 1'in kararını
# fiilen geri almak olurdu; maskesiz bırakılamaz.
#
# ALAN NULL YAPILMIYOR, TAMAMEN ÇIKARILIYOR. Bu modülün kendi anlam
# sözleşmesinde ``None`` = "maliyet BİLİNMİYOR" (oran tanımlı değil, bkz.
# `_tutar`). Maske ``None`` ile yapılsaydı yetkisiz kullanıcı `labor_cost: null`
# görüp "oran tanımlanmamış" diye okurdu — YANLIŞ BİLGİ. Alanın hiç olmaması
# "sana kapalı", ``null`` ise "ölçülmemiş" demek; ikisi karışmamalı.
#
# SAATLER MASKELENMİYOR: `labor_hours`/`machine_hours` operasyonel veri (işin ne
# kadar sürdüğü), finansal değil. Onları da gizlemek sahadaki kullanıcının kendi
# kaydını okuyamaması olurdu.

# Maskenin izni SABİT YAZILMIYOR: oran tablosunu okuyabilen kim ise, satırdaki
# donmuş oranı da o görebilir. FAZ 1 o ucu başka bir izne taşırsa maske
# kendiliğinden onu takip eder ve iki karar ayrışamaz.
_ORAN_OKUMA_IZNI = required_permission("GET", "/api/cost-rates")

_FINANSAL_ALANLAR = (
    "labor_hourly_rate", "machine_hourly_rate", "labor_cost", "machine_cost",
)


def _oran_gorebilir(request: Request) -> bool:
    """Çağıran, oran tablosunu okuyabilen biri mi?"""
    user = getattr(request.state, "user", {}) or {}
    return has_permission(str(user.get("role")), _ORAN_OKUMA_IZNI)


def _faaliyet_yaniti(satir: dict[str, Any], request: Request) -> dict[str, Any]:
    """Faaliyetin maliyet alanlarını dış temsile çevirir ve gerekirse maskeler.

    PARA TELDEN FLOAT ÇIKMAZ. Ham ``text()`` sonucu diyalekte göre değişiyor:
    PostgreSQL ``NUMERIC``i float'a çeviren bir JSON kodlamasına düşerken SQLite
    ``Decimal`` benzeri bir değer veriyor. FAZ 1'de bu sapma CI'da GERÇEKTEN
    yaşandı (bkz. `routers/cost_rates.py._disari`); aynı normalizasyon burada da
    faaliyet satırının çıktığı HER yola uygulanıyor.

    ``request`` ZORUNLU ve bir bayrak DEĞİL: yetki kararı tek bir yerde
    (`_oran_gorebilir`) veriliyor. Bayrak alsaydı yeni bir okuma yolu yanlışlıkla
    ``True`` geçebilirdi; `Request` isteyince o yol kararı veremez, yalnız
    devreder.
    """
    isci_saat = satir.get("labor_hours")
    isci_oran = satir.get("labor_hourly_rate")
    makine_saat = satir.get("machine_hours")
    makine_oran = satir.get("machine_hourly_rate")
    yanit = {
        **satir,
        "labor_hours": None if isci_saat is None else format(quantity(isci_saat), "f"),
        "labor_hourly_rate": None if isci_oran is None else format(money(isci_oran), "f"),
        "machine_hours": None if makine_saat is None else format(quantity(makine_saat), "f"),
        "machine_hourly_rate": (
            None if makine_oran is None else format(money(makine_oran), "f")
        ),
        # Türetilen alanlar; sütunları YOK (gerekçe migration 0053 başlığında).
        "labor_cost": _tutar(isci_saat, isci_oran),
        "machine_cost": _tutar(makine_saat, makine_oran),
    }
    if not _oran_gorebilir(request):
        for alan in _FINANSAL_ALANLAR:
            yanit.pop(alan, None)
    return yanit


def _faaliyet_satiri(
    db: Session, cid: int, activity_id: int, request: Request,
) -> dict[str, Any]:
    """Faaliyet satırını okumanın TEK güvenli yolu: oku VE dış temsile çevir.

    --- NEDEN OKUMA İLE MASKELEME AYRILAMAZ ------------------------------

    ``_satir`` ``SELECT *`` yapıyor, yani `field_activities`ı onunla okuyan HER
    yol dört oran/maliyet sütununu KENDİLİĞİNDEN taşır. Bugünkü dört okuma
    yolunun dördü de maskeli, ama bunu sağlayan tek şey her çağıranın
    ``_faaliyet_yaniti``yi çağırmayı hatırlaması. Yeni bir okuma yolu (FAZ 3
    tam olarak bunları açacak: parsel zaman çizelgesi, pano) unuttuğu anda
    maske sessizce delinir ve `depo`/`satis` donmuş oranı görür.

    Bu yüzden iki adım TEK fonksiyonda birleştirildi. Ayrı bırakıldıklarında
    "okudum ama çevirmeyi unuttum" mümkün; birleştirildiklerinde değil.

    Kural ``test_activity_read_path_gate`` ile çiviliyor: ``_satir(...,
    "field_activities", ...)`` bu fonksiyonun DIŞINDA geçerse kapı kırılır.

    İç doğrulama için çağıranlar (girdi eklerken faaliyet türünü, görev
    bağlarken sezonu okuyanlar) da buradan geçiyor. Onların maskelenmiş bir
    sözlük alması zararsız — okudukları alanlar (``activity_type``,
    ``season_id``) maskeden etkilenmiyor — ve tek kapı kuralını istisnasız
    tutmak, "bu çağrı zaten dışarı vermiyor" muhakemesini her yeni yolda
    yeniden yapmaktan güvenli.
    """
    return _faaliyet_yaniti(_satir(db, cid, "field_activities", activity_id), request)


# ---------------------------------------------------------------------------
# SEZON MALİYETİNE İŞÇİLİK VE MAKİNE (Gerçek Maliyet FAZ 3, dilim 1)
# ---------------------------------------------------------------------------
#
# İşin başladığı şikâyet buydu: "panoda gösterdiğimiz kâr eksik — tarla brüt
# katkısı yalnız girdi maliyetini düşüyor, işçilik ve traktör hesapta yok."
# FAZ 1 oranı kurdu, FAZ 2 satıra dondurdu; burada o iki sütun kâra bağlanıyor.
#
# --- TOPLAM ORANI SIZDIRIR, O YÜZDEN TOPLAM DA MASKELİ ---------------------
#
# Saatleri gören biri toplam maliyeti de görürse bölerek ORANI bulur. FAZ 1
# oran okumayı bilinçli olarak `finance`e bağladı, FAZ 2 satırdaki oranı
# maskeledi; toplamı maskesiz vermek iki kararı da boşa çıkarırdı.
#
# Çözüm, toplamı AYRI bir yetki kontrolüne bağlamak DEĞİL — o iki karar
# ayrışabilirdi. Toplam, `_faaliyet_yaniti`den geçmiş satırlardan hesaplanıyor:
# `finance` yoksa satırlarda `labor_cost`/`machine_cost` zaten YOK, dolayısıyla
# toplama giremezler. Maske ile toplamın tabanı TEK kaynaktan geliyor.
#
# --- ORANI TANIMSIZ FAALİYET SESSİZCE ATLANMAZ ----------------------------
#
# Saati girilmiş ama oranı tanımlanmamış faaliyetin maliyeti BİLİNMİYOR
# (`_tutar` None döner). Bunu toplamda sıfır saymak maliyeti eksik gösterir —
# yani işin başındaki şikâyetin aynısını yeni bir yerde üretir. Toplam bu
# yüzden bir TABAN olarak veriliyor ve yanında `cost_incomplete` +
# `activities_missing_rate` taşınıyor: sayı dürüst, eksikliği görünür.

_EMEK_ALANLARI = (("labor_hours", "labor_cost"), ("machine_hours", "machine_cost"))


def _emek_maliyeti(faaliyetler: list[dict[str, Any]]) -> tuple[dict[int, Decimal], dict[int, int]]:
    """Sezon başına işçilik+makine maliyeti ve ORANI EKSİK faaliyet sayısı.

    Girdi ``_faaliyet_yaniti``den GEÇMİŞ satırlar olmalı: maskeli çağıranda
    maliyet alanları hiç bulunmaz ve toplama katkı vermezler.

    "Eksik" sayımı saat GİRİLMİŞ ama maliyeti hesaplanamamış faaliyetleri
    sayar; saati olmayan faaliyet eksik değildir (o işte işçilik yok).
    """
    toplam: dict[int, Decimal] = {}
    eksik: dict[int, int] = {}
    for f in faaliyetler:
        sid = int(f["season_id"])
        # FAALİYET BAŞINA BİR KEZ. Sayaç eskiden bileşen döngüsünün İÇİNDE
        # artıyordu: aynı faaliyette hem işçilik hem makine saati girilmiş ve
        # iki oran da tanımsızsa sonuç 2 çıkıyordu. Alan adı, docstring ve
        # sözleşme "faaliyet sayısı" diyor — kullanıcıya gösterilen sayı
        # olduğundan gerçekte kaç faaliyetin eksik olduğunu söylemeli.
        bu_faaliyet_eksik = False
        for saat_alani, maliyet_alani in _EMEK_ALANLARI:
            if maliyet_alani not in f:
                continue  # maskeli çağıran: bu bileşen toplama girmiyor
            maliyet = f[maliyet_alani]
            if maliyet is not None:
                toplam[sid] = toplam.get(sid, Decimal("0")) + money(maliyet)
            elif f.get(saat_alani) is not None:
                bu_faaliyet_eksik = True
        if bu_faaliyet_eksik:
            eksik[sid] = eksik.get(sid, 0) + 1
    return toplam, eksik


def _maliyet_ozeti(
    girdi_maliyeti: Decimal, emek: Decimal, eksik: int, request: Request,
) -> dict[str, Any]:
    """Sezon özetinin maliyet bloğu; tabanı ve eksikliği AÇIKÇA söyler.

    ``request`` alıyor, BAYRAK DEĞİL — `_faaliyet_yaniti` ile aynı desen.
    Bayrak alsaydı yeni bir çağıran maskeleme kararını KENDİSİ verebilirdi;
    `Request` isteyince yalnız DEVREDEBİLİR. İzin de sabit yazılmıyor,
    `_ORAN_OKUMA_IZNI` üzerinden geliyor (bkz. `_oran_gorebilir`), yani
    `GET /api/cost-rates` izni bir gün değişirse maske kendiliğinden takip eder.

    ``cost_incomplete`` ve ``activities_missing_rate`` izinsiz çağırana
    DÖNMÜYOR. Bu davranış YENİ DEĞİL — iki alan zaten yalnız aşağıdaki yetkili
    dalda üretiliyordu; burada yazılı olan gerekçesi. O çağıranın toplamı
    ``cost_basis="inputs"`` ve girdilerin hepsi fiyatlıysa gördüğü sayı EKSİK
    DEĞİLDİR; ona "eksik" demek, kendi görüşünün doğru olduğu bir durumu hatalı
    gösterirdi. Ayrıca ``activities_missing_rate`` oranın DEĞERİNİ değil
    firmanın maliyet YAPILANDIRMASININ durumunu sızdırır. ``null`` da
    kullanılmıyor: null "bilinmiyor" demek, burada kastedilen "sana kapalı".

    ``cost_basis`` bilerek var: aynı alan adı iki farklı temeli taşıyabiliyor
    (maskeli çağıranda yalnız girdi, yetkilide girdi+işçilik+makine). Tabanı
    yazmasaydık aynı sayı iki şeyi anlatır ve hangisi olduğu okuyanın rolüne
    göre sessizce değişirdi.
    """
    # PARA SABİT ÖLÇEKLİ METİN. Ham `SUM(NUMERIC)` doğrudan `str()`lenirse
    # sonuç DİYALEKTE GÖRE değişiyordu: PostgreSQL '1000.00', SQLite '1000'.
    # Bu, FAZ 1'de `cost_rates`te kapatılan sapmanın aynısı; PG ikizi bu dilimde
    # onu yakaladı. Değer değişmiyor, yalnız temsil iki diyalektte eşitleniyor.
    if not _oran_gorebilir(request):
        return {"total_cost": str(money(girdi_maliyeti)), "cost_basis": "inputs"}
    return {
        "total_cost": str(money(girdi_maliyeti + emek)),
        "cost_basis": "inputs+labor+machine",
        "input_cost": str(money(girdi_maliyeti)),
        "labor_machine_cost": str(money(emek)),
        # Toplam bir TABAN: oranı tanımsız faaliyetler hesaba giremedi.
        "cost_incomplete": eksik > 0,
        "activities_missing_rate": eksik,
    }


# ---------------------------------------------------------------- çiftlik ---

# ---------------------------------------------------------------------------
# ÇEVRİMDIŞI KUYRUKTAN GELEN OLUŞTURMALARIN TEKRAR KORUMASI
# ---------------------------------------------------------------------------
#
# Tarla kayıtları parselin ortasında, kapsama alanı dışında giriliyor; istemci
# kuyruğa yazıp sonra gönderiyor ve cevabı kaybolan bir isteği YENİDEN yolluyor.
# Koruma olmasaydı ikinci gönderim ikinci bir faaliyet/girdi/hasat satırı
# oluşturur ve sezon maliyeti sessizce ikiye katlanırdı.
#
# Bu blok `field.py`'deki saha defterinden bir noktada ayrılıyor: burada
# OLUŞAN SATIRIN KİMLİĞİ saklanıyor. Gerekçe migration 0045 başlığında.
#
# ÖLÇÜLDÜ (ön kontrol devre dışı bırakılıp SQLite VE PostgreSQL'de koşuldu) —
# İKİ MEKANİZMA VAR, İKİSİ AYRI İŞ YAPIYOR:
#
#   * Benzersizlik kısıtı MÜKERRER SATIRI önlüyor. Ön kontrol kapalıyken bile
#     eşzamanlı yarış testi geçiyor: ikinci INSERT kısıta takılıyor, işlem
#     tümüyle geri alınıyor ve kazananın satırı dönüyor.
#
#   * Ön kontrol, TEKRAR GÖNDERİMİN DOĞRULAMALARA TAKILMAMASINI sağlıyor. Bunu
#     kısıt yapamaz, çünkü doğrulamalar INSERT'ten ÖNCE çalışır. Ölçümde iki
#     diyalektte de AYNI hata çıktı: parsel küçültüldükten sonra ZATEN
#     UYGULANMIŞ bir faaliyetin tekrarı "alan aşımı" 422'si alıyor. O hâlde
#     istemcinin kuyruğunda tamamlanmış bir iş kalıcı hata olarak takılı kalır
#     ve teknisyen onu elle silmek zorunda kalırdı.
#
# Yani ön kontrol bir hızlandırma değil, davranışın parçası.
#
# UYARI — BU ÖLÇÜM BİR KEZ YANLIŞ YAPILDI. İlk sefer yalnız SQLite ile
# bakılmıştı ve "koruma çalışıyor" sonucu çıkmıştı; oysa o sırada defter
# PostgreSQL'de HİÇ yazılmıyordu (bkz. `_kuyruga_isle`). Bu modülün tekrar
# koruması dialect davranışına duyarlı; SQLite tek başına kanıt değildir.

# `activity` BU HARİTADA BİLEREK YOK. Faaliyet satırı TEK KAPIDAN
# (`_faaliyet_satiri`) okunuyor; buraya konsaydı `_satir(db, cid,
# _KUYRUK_TABLOSU[kind], ...)` çağrısı DOLAYLI bir `field_activities` okuması
# olurdu ve kapı onu literal tablo adı görmediği için kaçırırdı. Haritadan
# çıkarmak, o dolaylı yolun var olamamasını sağlıyor.
_KUYRUK_TABLOSU = {
    "activity_input": "field_activity_inputs",
    "harvest": "field_harvests",
}


def _kuyruk_satiri(
    db: Session, cid: int, kind: str, target_id: int, request: Request,
) -> dict[str, Any]:
    """Deftere yazılmış kaydı OKUR ve dış temsiline çevirir.

    Tekrar gönderimin cevabı ilk gönderimin cevabıyla AYNI ŞEKİLDE olmalı; aksi
    hâlde aynı işlem istemciye iki farklı temsille döner (biri metin para, biri
    ham) ve MASKE yalnız ilk gönderimde uygulanmış olurdu.

    Faaliyet dalı tek kapıya bağlı: okuma ile maskeleme burada da ayrılamıyor.
    """
    if kind == "activity":
        return _faaliyet_satiri(db, cid, target_id, request)
    satir = _satir(db, cid, _KUYRUK_TABLOSU[kind], target_id)
    return _hasat_yaniti(satir) if kind == "harvest" else satir


def _tekrar_mi(
    db: Session, cid: int, kind: str, operation_id: str | None, *, request: Request,
) -> dict[str, Any] | None:
    """Bu işlem daha önce uygulandıysa OLUŞAN satırı döndürür.

    Satır her seferinde TAZEDEN okunuyor: defterde sonucun fotoğrafı değil,
    yalnız kimliği duruyor. Arada kayıt değiştiyse istemci güncel hâlini görür.
    """
    if operation_id is None:
        return None
    kayit = db.execute(
        text(
            """SELECT target_id FROM farm_operations
            WHERE company_id=:cid AND operation_id=:oid AND kind=:kind"""
        ),
        {"cid": cid, "oid": operation_id, "kind": kind},
    ).mappings().first()
    if not kayit:
        return None
    return _kuyruk_satiri(db, cid, kind, int(kayit["target_id"]), request)


def _kuyruga_isle(
    db: Session, cid: int, request: Request, kind: str,
    operation_id: str | None, target_id: int,
) -> dict[str, Any] | None:
    """Oluşan satırı deftere yazar. ÇAĞIRAN HENÜZ COMMIT ETMEMİŞ OLMALI.

    KAYIT VE DEFTER SATIRI AYNI İŞLEMDE. İlk hâli iki ayrı commit kullanıyordu
    ve PostgreSQL'de ÖLÇÜLEN GERÇEK BİR HATA verdi: kayıt commit edilip defter
    satırı savepoint içinde bırakılıyordu, hiç commit edilmiyordu ve
    ``get_db``'nin ``db.close()``'u onu geri alıyordu. Sonuç: defter HER ZAMAN
    boş, tekrar koruması hiç çalışmıyor. SQLite'ta gözükmedi çünkü oradaki
    bağlantı yazmayı kendiliğinden işliyordu — bu, tek başına SQLite ile test
    etmenin neden yetmediğinin somut örneği.

    Yarış kaybedildiğinde artık SATIR SİLİNMİYOR, İŞLEMİN TAMAMI GERİ ALINIYOR.
    Silme yaklaşımı, "az önce yazdığımızı bulup temizle" gibi kırılgan bir
    telafiydi; aynı işlemde olduğumuz için rollback hem daha basit hem de
    kısmen yazılmış bir durum bırakma ihtimalini tamamen ortadan kaldırıyor.
    """
    if operation_id is None:
        return None
    kullanici = _kullanici_id(request)
    try:
        with db.begin_nested():
            db.execute(
                text(
                    """INSERT INTO farm_operations
                    (company_id,user_id,operation_id,kind,target_id,created_at)
                    VALUES(:cid,:uid,:oid,:kind,:target,:now)"""
                ),
                {"cid": cid, "uid": kullanici or 0, "oid": operation_id,
                 "kind": kind, "target": target_id, "now": _simdi()},
            )
    except IntegrityError:
        # Yarışı KAYBETTİK: aynı kimlik zaten deftere yazılmış. Kendi
        # kaydımızı da içeren işlemi tümüyle geri alıyoruz; kazananın satırı
        # geçerli olan.
        db.rollback()
        onceki = db.execute(
            text(
                """SELECT target_id FROM farm_operations
                WHERE company_id=:cid AND operation_id=:oid AND kind=:kind"""
            ),
            {"cid": cid, "oid": operation_id, "kind": kind},
        ).mappings().first()
        if not onceki:
            # Kısıt patladı ama kayıt okunamıyor — tutarsız durum; sessizce
            # mükerrer yaratmaktansa açıkça hata vermek doğru.
            raise HTTPException(409, "İşlem kimliği çakıştı, tekrar deneyin")
        return _kuyruk_satiri(db, cid, kind, int(onceki["target_id"]), request)
    return None


@router.get("/farms")
def list_farms(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND status=:status" if status else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if status:
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM farms WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,code,name,customer_id,city,district,notes,status,updated_at
            FROM farms WHERE company_id=:cid{kosul}
            ORDER BY code LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/farms", status_code=201)
def create_farm(payload: FarmWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    if payload.customer_id is not None:
        _cari_dogrula(db, cid, payload.customer_id)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO farms(company_id,code,name,customer_id,city,district,
                notes,status,created_at,updated_at)
                VALUES(:cid,:code,:name,:customer_id,:city,:district,:notes,'ACTIVE',:now,:now)
                RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu çiftlik kodu zaten kullanılıyor") from exc
    db.commit()
    return _satir(db, cid, "farms", int(yeni))


@router.get("/farms/{farm_id}")
def get_farm(farm_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "farms", farm_id)


@router.put("/farms/{farm_id}")
def update_farm(farm_id: int, payload: FarmUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    mevcut = _satir(db, cid, "farms", farm_id)
    beklenen_surum = _surum_dogrula(mevcut, payload.expected_updated_at)
    if payload.customer_id is not None:
        _cari_dogrula(db, cid, payload.customer_id)
    veri = payload.model_dump(exclude={"expected_updated_at"})
    try:
        sonuc = db.execute(
            text(
                """UPDATE farms SET code=:code,name=:name,customer_id=:customer_id,
                city=:city,district=:district,notes=:notes,status=:status,updated_at=:now
                WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
            ),
            {"id": farm_id, "cid": cid, "now": _simdi(),
             "expected_updated_at": beklenen_surum, **veri},
        )
        _cas_sonuc_dogrula(db, sonuc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu çiftlik kodu zaten kullanılıyor") from exc
    db.commit()
    return _satir(db, cid, "farms", farm_id)


def _cari_dogrula(db: Session, cid: int, customer_id: int) -> None:
    """Cari AYNI firmaya ait olmalı.

    ``farms.customer_id`` sade yabancı anahtarla bağlı (bileşik FK yalnız yeni
    tablolar arasında kuruldu; gerekçesi migration 0044 başlığında). Bu yüzden
    çapraz kiracı bağı burada, uygulama katmanında engelleniyor.
    """
    var = db.execute(
        text("SELECT 1 FROM customers WHERE id=:id AND company_id=:cid"),
        {"id": customer_id, "cid": cid},
    ).first()
    if not var:
        raise HTTPException(404, "Cari bulunamadı")


def _aktif_firma_kullanicisi_dogrula(
    db: Session, cid: int, user_id: int, alan: str,
) -> None:
    """Kullanıcı aktif olmalı ve açıkça aynı firmanın üyesi olmalı."""
    var = db.execute(
        text(
            """SELECT 1 FROM app_users u
            JOIN user_company_memberships membership ON membership.user_id=u.id
            WHERE u.id=:user_id AND membership.company_id=:cid
              AND u.is_active=TRUE"""
        ),
        {"user_id": user_id, "cid": cid},
    ).first()
    if not var:
        raise HTTPException(400, f"{alan} bu firmanın aktif bir kullanıcısı olmalıdır")


# ----------------------------------------------------------------- parsel ---

@router.get("/farm-parcels")
def list_parcels(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    farm_id: int | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND farm_id=:farm_id" if farm_id else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if farm_id:
        params["farm_id"] = farm_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM farm_parcels WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,farm_id,code,name,area_decare,parcel_no,block_no,city,
            district,neighborhood,status,updated_at
            FROM farm_parcels WHERE company_id=:cid{kosul}
            ORDER BY code LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/farm-parcels", status_code=201)
def create_parcel(payload: ParcelWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    # Çiftlik AYNI firmada olmalı; bileşik FK zaten engelliyor ama önce
    # anlaşılır bir 404 dönmek, veritabanı hatasına düşmekten iyi.
    _satir(db, cid, "farms", payload.farm_id)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO farm_parcels(company_id,farm_id,code,name,area_decare,
                parcel_no,block_no,city,district,neighborhood,boundary_geojson,
                status,created_at,updated_at)
                VALUES(:cid,:farm_id,:code,:name,:area_decare,:parcel_no,:block_no,
                :city,:district,:neighborhood,:boundary_geojson,'ACTIVE',:now,:now)
                RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu parsel kodu zaten kullanılıyor") from exc
    db.commit()
    return _satir(db, cid, "farm_parcels", int(yeni))


@router.get("/farm-parcels/{parcel_id}/timeline")
def parcel_timeline(parcel_id: int, request: Request, db: Session = Depends(get_db)):
    """Parselin sezonları + faaliyet/hasat zaman çizelgesi TEK istekte.

    NEDEN AYRI UÇ. Mevcut liste uçlarıyla da yapılabilirdi ama istemcinin
    önce sezonları çekip HER SEZON İÇİN ayrı faaliyet ve hasat isteği atması
    gerekirdi (N+1). Sahadaki telefon bağlantısında bu, ekranın açılmasını
    sezon sayısı kadar yavaşlatır.

    Faaliyet ve hasat AYRI sorgularla toplanıp burada birleştiriliyor —
    tek sorguda JOIN'lemek kartezyen çarpım üretir ve iki faaliyeti olan bir
    sezonda her hasat iki kez görünürdü.
    """
    cid = company_id(request)
    parsel = _satir(db, cid, "farm_parcels", parcel_id)
    ciftlik = _satir(db, cid, "farms", int(parsel["farm_id"]))

    sezonlar = db.execute(
        text(
            """SELECT id,season_year,crop,variety,status,started_on,ended_on,
            planted_area_decare,notes,updated_at
            FROM crop_seasons WHERE company_id=:cid AND parcel_id=:pid
            ORDER BY season_year DESC,id DESC"""
        ),
        {"cid": cid, "pid": parcel_id},
    ).mappings().all()
    sezon_ids = [int(r["id"]) for r in sezonlar]

    faaliyetler: list[dict[str, Any]] = []
    hasatlar: list[dict[str, Any]] = []
    maliyetler: dict[int, Decimal] = {}
    urunler: dict[int, Decimal] = {}
    gelirler: dict[int, Decimal] = {}
    gelirli_sezonlar: set[int] = set()

    if sezon_ids:
        # Kiracı filtresi HER sorguda; sezon kimlikleri zaten kiracı kapsamlı
        # okunmuş olsa da ikinci savunma bırakılıyor.
        # Donmuş oran sütunları da seçiliyor (FAZ 3): sezon maliyetine işçilik
        # ve makine giriyor. Satırlar `_faaliyet_yaniti`den GEÇMEK ZORUNDA —
        # okuma yolu kapısı bunu statik olarak zorluyor ve toplam da bu maskeli
        # satırlardan hesaplanıyor.
        faaliyetler = [_faaliyet_yaniti(dict(r), request) for r in db.execute(
            text(
                """SELECT a.id,a.season_id,a.activity_type,a.performed_at,
                a.applied_area_decare,a.area_override_reason,a.notes,
                a.reentry_interval_days,a.preharvest_interval_days,
                a.labor_hours,a.labor_hourly_rate,a.machine_hours,a.machine_hourly_rate,
                COALESCE((SELECT SUM(i.total_cost) FROM field_activity_inputs i
                          WHERE i.company_id=a.company_id AND i.activity_id=a.id),0) input_cost
                FROM field_activities a
                WHERE a.company_id=:cid AND a.season_id IN :ids AND a.status='RECORDED'
                ORDER BY a.performed_at DESC,a.id DESC"""
            ).bindparams(bindparam("ids", expanding=True)),
            {"cid": cid, "ids": sezon_ids},
        ).mappings().all()]

        hasatlar = [dict(r) for r in db.execute(
            text(
                """SELECT id,season_id,harvested_on,quantity,unit,harvested_area_decare,
                quality_grade,moisture_percent,safety_override_reason,safety_warning,notes,
                sold_quantity,revenue_amount
                FROM field_harvests
                WHERE company_id=:cid AND season_id IN :ids AND status='RECORDED'
                ORDER BY harvested_on DESC,id DESC"""
            ).bindparams(bindparam("ids", expanding=True)),
            {"cid": cid, "ids": sezon_ids},
        ).mappings().all()]

        for f in faaliyetler:
            maliyetler[int(f["season_id"])] = (
                maliyetler.get(int(f["season_id"]), Decimal("0"))
                + Decimal(str(f["input_cost"] or 0))
            )
        emek_maliyetleri, eksik_oranlar = _emek_maliyeti(faaliyetler)
        hasatlar = [_hasat_yaniti(hv) for hv in hasatlar]
        for hv in hasatlar:
            sid = int(hv["season_id"])
            urunler[sid] = urunler.get(sid, Decimal("0")) + Decimal(str(hv["quantity"] or 0))
            if hv["revenue_amount"] is not None:
                # Geliri GİRİLMİŞ hasat: sıfır ile "henüz bilinmiyor" ayrımı
                # için kaydı işaretliyoruz.
                gelirli_sezonlar.add(sid)
                gelirler[sid] = gelirler.get(sid, Decimal("0")) + Decimal(str(hv["revenue_amount"]))

    sezon_ozet = []
    for r in sezonlar:
        sid = int(r["id"])
        # Ekilen alan girilmemişse parselin alanına DÜŞMÜYORUZ: sezonun alanı
        # bilinmiyorsa oran da bilinmiyor demektir. Parsel alanını varsaymak,
        # ölçülmemiş bir sezonu ölçülmüş gibi gösterirdi.
        alan = Decimal(str(r["planted_area_decare"])) if r["planted_area_decare"] is not None else None
        girdi_maliyeti = maliyetler.get(sid, Decimal("0"))
        blok = _maliyet_ozeti(
            girdi_maliyeti, emek_maliyetleri.get(sid, Decimal("0")),
            eksik_oranlar.get(sid, 0), request,
        )
        # Oran/dekar ve marj HANGİ TABANDAN hesaplandıysa ondan; `cost_basis`
        # okuyana hangisi olduğunu söylüyor.
        maliyet = Decimal(blok["total_cost"])
        miktar = urunler.get(sid, Decimal("0"))
        sezon_ozet.append({
            **{k: r[k] for k in (
                "id", "season_year", "crop", "variety", "status",
                "started_on", "ended_on", "planted_area_decare", "notes", "updated_at",
            )},
            **blok,
            "harvest_quantity": str(miktar),
            "cost_per_decare": str((maliyet / alan).quantize(Decimal("0.01"))) if alan and alan > 0 else None,
            "yield_per_decare": str((miktar / alan).quantize(Decimal("0.0001"))) if alan and alan > 0 else None,
            # Gelir girilmemişse None — SIFIR DEĞİL (bkz. dashboard'daki not).
            # Gelir de SABİT ÖLÇEKLİ METİN: `total_cost`/`gross_margin` money()
            # ile normalize edilirken gelir ham kalmıştı ve aynı diyalekt
            # sapmasını taşıyordu (PG '5000.00', SQLite '5000').
            "revenue_amount": str(money(gelirler.get(sid, Decimal("0")))) if sid in gelirli_sezonlar else None,
            # Yetkili çağıranda artık işçilik ve makine de düşülüyor; maskeli
            # çağıranda taban hâlâ yalnız girdi ve `cost_basis` bunu söylüyor.
            "gross_margin": (
                str(money(gelirler.get(sid, Decimal("0")) - maliyet))
                if sid in gelirli_sezonlar else None
            ),
        })

    return {
        "parcel": parsel,
        "farm": {"id": ciftlik["id"], "code": ciftlik["code"], "name": ciftlik["name"]},
        "seasons": sezon_ozet,
        "activities": faaliyetler,
        "harvests": hasatlar,
    }


@router.get("/farm-parcels/{parcel_id}")
def get_parcel(parcel_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "farm_parcels", parcel_id)


@router.put("/farm-parcels/{parcel_id}")
def update_parcel(parcel_id: int, payload: ParcelUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    mevcut = _satir(db, cid, "farm_parcels", parcel_id)
    beklenen_surum = _surum_dogrula(mevcut, payload.expected_updated_at)
    _satir(db, cid, "farms", payload.farm_id)
    veri = payload.model_dump(exclude={"expected_updated_at"})
    sonuc = db.execute(
        text(
            """UPDATE farm_parcels SET farm_id=:farm_id,code=:code,name=:name,
            area_decare=:area_decare,parcel_no=:parcel_no,block_no=:block_no,
            city=:city,district=:district,neighborhood=:neighborhood,
            boundary_geojson=:boundary_geojson,status=:status,updated_at=:now
            WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
        ),
        {"id": parcel_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": beklenen_surum, **veri},
    )
    _cas_sonuc_dogrula(db, sonuc)
    db.commit()
    return _satir(db, cid, "farm_parcels", parcel_id)


# ------------------------------------------------------------------ sezon ---

@router.get("/crop-seasons")
def list_seasons(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    parcel_id: int | None = None,
    season_year: int | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if parcel_id:
        kosul += " AND parcel_id=:parcel_id"
        params["parcel_id"] = parcel_id
    if season_year:
        kosul += " AND season_year=:season_year"
        params["season_year"] = season_year
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM crop_seasons WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,parcel_id,season_year,crop,product_id,variety,
            started_on,ended_on,
            status,planted_area_decare,notes,updated_at
            FROM crop_seasons WHERE company_id=:cid{kosul}
            ORDER BY season_year DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/crop-seasons", status_code=201)
def create_season(payload: SeasonWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    parsel = _satir(db, cid, "farm_parcels", payload.parcel_id)
    _ekim_alani_dogrula(payload.planted_area_decare, parsel)
    _sezon_urunu_dogrula(db, cid, payload.product_id)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO crop_seasons(company_id,parcel_id,season_year,crop,product_id,
            variety,
            started_on,ended_on,status,planted_area_decare,notes,created_at,updated_at)
            VALUES(:cid,:parcel_id,:season_year,:crop,:product_id,:variety,
            :started_on,:ended_on,
            'PLANNED',:planted_area_decare,:notes,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, **payload.model_dump()},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "crop_seasons", int(yeni))


def _sezon_urunu_dogrula(db: Session, cid: int, product_id: int | None) -> None:
    """Sezonun bildirdiği ürün ÇAĞIRANIN firmasında olmalı.

    Ürün opsiyoneldir: bildirilmemiş sezon serbestçe geçer ve hasadı
    tüketicide adı konmuş `SKIPPED_NO_PRODUCT` kovasına düşer.

    Kapı `_urun_dogrula` — faaliyet girdilerinin `product_id`si için
    kullanılan AYNI kapı. Veritabanındaki bileşik yabancı anahtar
    (`fk_crop_seasons_product_same_company`) bu kapıyı GEREKSİZ KILMAZ, ikisi
    AYRI şey söyler: kısıt çapraz-firma yazımı ENGELLER (ve `IntegrityError`
    olarak 500 döndürürdü), bu kapı ise onu çağırana ANLAŞILIR bir 404 olarak
    söyler. Kısıt son savunmadır, kapı ilk.
    """
    if product_id is None:
        return
    _urun_dogrula(db, cid, product_id)


def _ekim_alani_dogrula(ekilen: Decimal | None, parsel: dict[str, Any]) -> None:
    """Ekilen alan parseli aşamaz.

    Parselin kendi alanından büyük bir ekim alanı fiziken imkânsız ve dekar
    başına verimi olduğundan küçük gösterirdi.
    """
    if ekilen is None:
        return
    parsel_alani = Decimal(str(parsel["area_decare"]))
    if Decimal(str(ekilen)) > parsel_alani:
        raise HTTPException(
            422, f"Ekilen alan parsel alanını aşamaz (parsel {parsel_alani} dekar)"
        )


@router.get("/crop-seasons/{season_id}")
def get_season(season_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "crop_seasons", season_id)


@router.put("/crop-seasons/{season_id}")
def update_season(season_id: int, payload: SeasonUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    mevcut = _satir(db, cid, "crop_seasons", season_id)
    beklenen_surum = _surum_dogrula(mevcut, payload.expected_updated_at)
    parsel = _satir(db, cid, "farm_parcels", payload.parcel_id)
    _ekim_alani_dogrula(payload.planted_area_decare, parsel)
    _sezon_urunu_dogrula(db, cid, payload.product_id)
    veri = payload.model_dump(exclude={"expected_updated_at"})
    sonuc = db.execute(
        text(
            """UPDATE crop_seasons SET parcel_id=:parcel_id,season_year=:season_year,
            crop=:crop,product_id=:product_id,
            variety=:variety,started_on=:started_on,ended_on=:ended_on,
            status=:status,planted_area_decare=:planted_area_decare,notes=:notes,
            updated_at=:now WHERE id=:id AND company_id=:cid
            AND updated_at=:expected_updated_at"""
        ),
        {"id": season_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": beklenen_surum, **veri},
    )
    _cas_sonuc_dogrula(db, sonuc)
    db.commit()
    return _satir(db, cid, "crop_seasons", season_id)


# --------------------------------------------------------------- faaliyet ---

@router.get("/field-activities")
def list_activities(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    season_id: int | None = None,
    activity_type: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if season_id:
        kosul += " AND season_id=:season_id"
        params["season_id"] = season_id
    if activity_type:
        kosul += " AND activity_type=:activity_type"
        params["activity_type"] = activity_type.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM field_activities WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,season_id,activity_type,performed_at,applied_area_decare,
            operator_user_id,machine_id,reentry_interval_days,preharvest_interval_days,
            preharvest_source,catalogue_preharvest_days,
            notes,area_override_reason,status,
            labor_hours,labor_hourly_rate,machine_hours,machine_hourly_rate,
            updated_at
            FROM field_activities WHERE company_id=:cid{kosul}
            ORDER BY performed_at DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [_faaliyet_yaniti(dict(r), request) for r in rows],
            "total": int(toplam or 0),
            "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# FAZ 4 OUTBOX — YAZICI (tüketici BU DİLİMDE YOK)
# ---------------------------------------------------------------------------
#
# ``field_integration_events`` 0044'te açıldı ve bugüne kadar HİÇ satır
# üretmedi: tablo, kiracı sütunu, bileşik anahtar ve TENANT_TABLES üyeliği
# vardı ama ``backend/app`` içinde adı bir kez bile geçmiyordu. Ölçüldü.
#
# ANAHTAR NEDEN KAYNAKTAN TÜRETİLİYOR. ``idempotency_key`` yalnız kaynak
# satırdan hesaplanabilir olmak ZORUNDA: ``field_activity:<id>:<hedef>``.
# Sebebi FAZ 4'ün kendi gerekçesi — geçmiş faaliyetler sonradan yeniden
# işlenecek. Geri doldurma da canlı yazıcı da AYNI anahtarı üretir, dolayısıyla
# ikinci bir fiş yazılamaz: çakışmayı veritabanındaki
# ``uq_field_integration_events_key`` (company_id, idempotency_key) reddeder.
# Anahtar rastgele ya da zamana bağlı olsaydı geri doldurma her koşuda yeni
# satır üretir ve tüketici idempotent OLAMAZDI — 2. dilim imkânsız hale
# gelirdi.
#
# HEDEF neden anahtarın İÇİNDE. Bir faaliyet ileride hem stok hem muhasebe
# fişi doğurabilir. Hedef anahtarın parçası olmasaydı ikinci hedef aynı
# ``(company_id, idempotency_key)`` çiftine düşer ve şema onu reddederdi. Bu
# dilimde tek satır yazılıyor (``stock``), ama ikinci hedef eklemek şema
# değişikliği gerektirmiyor.
#
# HAYVANCILIK AYNI YOLU KULLANABİLİR. ``herd_integration_events`` 0049'da aynı
# gerekçeyle ve aynı sütun sözleşmesiyle açıldı (kaynak/hedef/anahtar/durum).
# Anahtar biçimi bilerek tablo adı içermiyor: ``animal_movement:<id>:stock``
# aynı kalıba oturur. Bu dilim hayvancılık tarafını YAZMIYOR; yalnız yazmayı
# imkânsız kılmıyor.
_ENTEGRASYON_KAYNAGI_FAALIYET = "field_activity"
_ENTEGRASYON_KAYNAGI_HASAT = "field_harvest"
_ENTEGRASYON_HEDEFI_STOK = "stock"


def _entegrasyon_anahtari(kaynak_tipi: str, kaynak_id: int, hedef: str) -> str:
    """Kaynak satırdan TEK BAŞINA türetilen idempotency anahtarı."""
    return f"{kaynak_tipi}:{int(kaynak_id)}:{hedef}"


def _entegrasyon_olayi_yaz(
    db: Session, cid: int, kaynak_tipi: str, kaynak_id: int, hedef: str, now: Any
) -> str:
    """Outbox satırını AYNI İŞLEMDE yazar; commit ÇAĞIRMAZ.

    Commit'i bilerek çağırmıyor: faaliyet geri alınırsa olay da geri alınmalı,
    olay yazılamazsa faaliyet sessizce başarılı OLMAMALI. İkisi tek işlemde.
    """
    anahtar = _entegrasyon_anahtari(kaynak_tipi, kaynak_id, hedef)
    db.execute(
        text(
            """INSERT INTO field_integration_events(company_id,source_type,source_id,
            target,idempotency_key,status,attempts,created_at,updated_at)
            VALUES(:cid,:source_type,:source_id,:target,:idempotency_key,
            'PENDING',0,:now,:now)"""
        ),
        {
            "cid": cid, "source_type": kaynak_tipi, "source_id": int(kaynak_id),
            "target": hedef, "idempotency_key": anahtar, "now": now,
        },
    )
    return anahtar


@router.post("/field-activities", status_code=201)
def create_activity(payload: ActivityWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    # ÖNCE tekrar kontrolü: kuyruktan gelen bir yeniden gönderim, doğrulamalara
    # hiç girmemeli. Girseydi arada sezon kapanmış bir kayıt "geçersiz" diye
    # reddedilir ve istemcinin kuyruğunda zaten UYGULANMIŞ bir işlem hata
    # olarak takılı kalırdı.
    onceki = _tekrar_mi(db, cid, "activity", payload.operation_id, request=request)
    if onceki is not None:
        return onceki
    sezon = _satir(db, cid, "crop_seasons", payload.season_id)
    parsel = _satir(db, cid, "farm_parcels", int(sezon["parcel_id"]))
    kurallar = _firma_kurallari(db, cid)
    _ilaclama_alani_dogrula(payload)
    _faaliyet_alani_dogrula(payload, parsel, kurallar["farm_area_override_policy"])
    if payload.operator_user_id is not None:
        _aktif_firma_kullanicisi_dogrula(
            db, cid, payload.operator_user_id, "Operatör"
        )
    if payload.machine_id is not None:
        _makine_dogrula(db, cid, payload.machine_id)
    now = _simdi()
    # ORAN BURADA DONUYOR. Bundan sonra `cost_rates`te ne olursa olsun bu satırın
    # maliyeti değişmez (bkz. `_oran_kopyala` başlığı ve migration 0053).
    isci_oran = _oran_kopyala(
        db, cid, "LABOR", payload.operator_user_id,
        payload.labor_hourly_rate, payload.labor_hours,
    )
    makine_oran = _oran_kopyala(
        db, cid, "MACHINE", payload.machine_id,
        payload.machine_hourly_rate, payload.machine_hours,
    )
    # PHI KÖKENİ BURADA ÇÖZÜLÜYOR — girdiler yazılmadan ÖNCE. Süre faaliyet
    # BAŞLIĞINDA duruyor, girdiler ise satırlarda; başlığı yazdıktan sonra
    # çözmek ikinci bir UPDATE gerektirir ve arada kesilme, kökeni boş ama
    # süresi dolu bir satır bırakırdı.
    katalog_gun, etkin_gun, koken = _phi_coz(db, cid, payload, sezon)
    yeni = db.execute(
        text(
            """INSERT INTO field_activities(company_id,season_id,activity_type,performed_at,
            applied_area_decare,operator_user_id,machine_id,reentry_interval_days,
            preharvest_interval_days,preharvest_source,catalogue_preharvest_days,
            notes,area_override_reason,status,
            labor_hours,labor_hourly_rate,machine_hours,machine_hourly_rate,
            created_at,updated_at)
            VALUES(:cid,:season_id,:activity_type,:performed_at,:applied_area_decare,
            :operator_user_id,:machine_id,:reentry_interval_days,:preharvest_interval_days,
            :preharvest_source,:catalogue_preharvest_days,
            :notes,:area_override_reason,'RECORDED',
            :labor_hours,:labor_hourly_rate,:machine_hours,:machine_hourly_rate,
            :now,:now) RETURNING id"""
        ),
        {
            "cid": cid, "now": now,
            **payload.model_dump(exclude={"inputs"}),
            # Şemadan gelen ham süreyi DEĞİL, çözülmüş etkin değeri yazıyoruz.
            "preharvest_interval_days": etkin_gun,
            "preharvest_source": koken,
            "catalogue_preharvest_days": katalog_gun,
            # Saatler de normalize ediliyor: istemci "2" de "2.0000" da
            # gönderebilir, sütun 18,4 ve iki diyalekt aynı değeri saklamalı.
            "labor_hours": (
                None if payload.labor_hours is None else quantity(payload.labor_hours)
            ),
            "machine_hours": (
                None if payload.machine_hours is None else quantity(payload.machine_hours)
            ),
            # Şemadan gelen ham oranı DEĞİL, çözülmüş kopyayı yazıyoruz.
            "labor_hourly_rate": isci_oran,
            "machine_hourly_rate": makine_oran,
        },
    ).scalar_one()

    # GİRDİLER AYNI İŞLEMDE. Ayrı istekle eklendiğinde ikisi ayrı işlem oluyor
    # ve arada kesilme girdisiz bir faaliyet bırakıyordu — sezon maliyeti
    # sessizce eksik çıkardı. Burada ya hepsi yazılır ya hiçbiri.
    for girdi in payload.inputs or []:
        _ilac_girdisi_dogrula(girdi, {"activity_type": payload.activity_type},
                              kurallar["farm_spraying_dose_required"])
        if girdi.product_id is not None:
            _urun_dogrula(db, cid, girdi.product_id)
        db.execute(
            text(
                """INSERT INTO field_activity_inputs(company_id,activity_id,product_id,
                input_name,quantity,unit,unit_cost,total_cost,dose,dose_unit,
                created_at,updated_at)
                VALUES(:cid,:aid,:product_id,:input_name,:quantity,:unit,:unit_cost,
                :total_cost,:dose,:dose_unit,:now,:now)"""
            ),
            {"cid": cid, "aid": int(yeni), "now": now,
             "total_cost": _girdi_toplami(girdi), **girdi.model_dump()},
        )

    # FAZ 4 OUTBOX. Faaliyet, girdileri ve olay TEK işlemde: geri alınırsa
    # yetim olay kalmaz, olay yazılamazsa faaliyet de yazılmaz.
    _entegrasyon_olayi_yaz(
        db, cid, _ENTEGRASYON_KAYNAGI_FAALIYET, int(yeni),
        _ENTEGRASYON_HEDEFI_STOK, now,
    )

    # Defter satırı AYNI işlemde yazılıyor; commit hepsinden sonra.
    yaris = _kuyruga_isle(db, cid, request, "activity", payload.operation_id, int(yeni))
    if yaris is not None:
        return yaris
    db.commit()
    kayit = _faaliyet_satiri(db, cid, int(yeni), request)
    kayit["inputs"] = [dict(r) for r in db.execute(
        text(
            """SELECT id,product_id,input_name,quantity,unit,unit_cost,total_cost,
            dose,dose_unit FROM field_activity_inputs
            WHERE company_id=:cid AND activity_id=:aid ORDER BY id"""
        ),
        {"cid": cid, "aid": int(yeni)},
    ).mappings().all()]
    return kayit


def _faaliyet_alani_dogrula(
    payload: ActivityWrite, parsel: dict[str, Any], politika: str = "require_reason",
) -> None:
    """Uygulanan alan parseli aşıyorsa AÇIK gerekçe şart.

    Aşmak her zaman hata değil (aynı parsele iki geçiş yapılmış olabilir), ama
    sessizce geçmesi dekar başına maliyeti bozar. Gerekçe zorunlu tutuluyor ki
    denetimde "neden" sorusunun cevabı kayıtta olsun.
    """
    if payload.applied_area_decare is None:
        return
    parsel_alani = Decimal(str(parsel["area_decare"]))
    if Decimal(str(payload.applied_area_decare)) <= parsel_alani:
        return
    # Firma ayarı: allow (sessiz geç) | require_reason (varsayılan) | block
    if politika == "allow":
        return
    if politika == "block":
        raise HTTPException(
            422,
            f"Uygulanan alan parsel alanını ({parsel_alani} dekar) aşamaz "
            "(firma ayarı: aşıma izin verilmiyor)",
        )
    if not payload.area_override_reason:
        raise HTTPException(
            422,
            f"Uygulanan alan parsel alanını ({parsel_alani} dekar) aşıyor; "
            "devam etmek için gerekçe girin",
        )


# ---------------------------------------------------------------------------
# İLAÇ GÜVENLİK ARALIKLARI
# ---------------------------------------------------------------------------
#
# `preharvest_interval_days` (hasat bekleme) ve `reentry_interval_days`
# (tarlaya giriş yasağı) V1'de toplanıyor ama HİÇ KULLANILMIYORDU. Toplanıp
# kullanılmayan bir güvenlik verisi, hiç toplanmamasından KÖTÜDÜR: kullanıcı
# sistemin kontrol ettiğini sanır.
#
# Tarih hesabı İŞ SAATİ DİLİMİNDE yapılıyor. `performed_at` UTC saklanıyor;
# UTC gününe göre hesaplasaydık akşam 22:00'de (yerel) yapılan bir ilaçlama
# ertesi güne kayar ve bekleme süresi BİR GÜN ERKEN dolmuş görünürdü. Kalıntı
# süresinde bir gün gerçek bir farktır.


def _yerel_gun(deger: Any) -> date:
    """Zaman damgasını İSTANBUL gününe çevirir (bkz. yukarıdaki gerekçe)."""
    if isinstance(deger, str):
        deger = datetime.fromisoformat(deger)
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(ISTANBUL).date()


def _bekleme_ihlalleri(
    db: Session, cid: int, season_id: int, hedef_gun: date,
) -> list[dict[str, Any]]:
    """`hedef_gun`de hasat yapılırsa ihlal edilecek bekleme sürelerini döndürür.

    Sezondaki, hasat bekleme süresi GİRİLMİŞ faaliyetlere bakılıyor. Süresi
    girilmemiş faaliyet ihlal sayılmıyor — bilinmeyeni ihlal saymak, sistemi
    kullanılamaz hâle getirir ve kullanıcıyı gerekçe yazmaya alıştırır; o da
    gerçek uyarıyı değersizleştirir.
    """
    satirlar = db.execute(
        text(
            """SELECT id,activity_type,performed_at,preharvest_interval_days
            FROM field_activities
            WHERE company_id=:cid AND season_id=:sid
              AND preharvest_interval_days IS NOT NULL
              AND status='RECORDED'"""
        ),
        {"cid": cid, "sid": season_id},
    ).mappings().all()

    ihlaller = []
    for r in satirlar:
        gun = int(r["preharvest_interval_days"])
        guvenli = _yerel_gun(r["performed_at"]) + timedelta(days=gun)
        if hedef_gun < guvenli:
            ihlaller.append({
                "activity_id": int(r["id"]),
                "activity_type": r["activity_type"],
                "performed_on": _yerel_gun(r["performed_at"]).isoformat(),
                "interval_days": gun,
                "safe_from": guvenli.isoformat(),
            })
    # En geç biten kısıt en üstte: kullanıcının beklemesi gereken tarih o.
    ihlaller.sort(key=lambda x: x["safe_from"], reverse=True)
    return ihlaller


def _hasat_guvenlik_dogrula(
    db: Session, cid: int, payload: HarvestWrite, politika: str = "require_reason",
) -> str | None:
    """Bekleme süresi dolmadan hasat: gerekçesiz GEÇMEZ.

    Sert ret DEĞİL — gerekçe isteniyor. Sebebi migration 0046 başlığında:
    ilaçlama kaydının tarihi yanlış girilmiş olabilir ya da hasat zaten yapılmış
    olup sisteme sonradan giriliyor olabilir. İkisinde de doğru olan işi bloke
    etmek değil, kararı KAYIT ALTINA almak.
    """
    ihlaller = _bekleme_ihlalleri(db, cid, payload.season_id, payload.harvested_on)
    if not ihlaller:
        return None
    metin = _hasat_ihlal_metni(ihlaller)

    if politika == "block":
        raise HTTPException(
            422,
            f"Hasat bekleme süresi dolmadı: {metin}. "
            "Firma ayarı erken hasada izin vermiyor.",
        )

    # `warn`: istek KABUL EDİLİYOR ama sistemin bulduğu kayda yazılıyor.
    # Kullanıcının gerekçesi varsa o da ayrı sütunda duruyor; ikisi
    # karıştırılmıyor (bkz. migration 0048).
    if politika == "warn":
        return metin

    # `require_reason` (varsayılan): gerekçesiz geçmez.
    if payload.safety_override_reason:
        return metin
    raise HTTPException(
        422,
        f"Hasat bekleme süresi dolmadı: {metin}. "
        "Yine de kaydetmek için gerekçe girin.",
    )


def _hasat_ihlal_metni(ihlaller: list[dict[str, Any]]) -> str:
    ilk = ihlaller[0]
    return (
        f"{ilk['performed_on']} tarihli işlem {ilk['interval_days']} gün bekleme "
        f"gerektiriyor, güvenli tarih {ilk['safe_from']}"
    )


# ---------------------------------------------------------------------------
# İLAÇLAMA DEĞİŞMEZLERİ (konu #2, "Değişmezler" başlığı)
# ---------------------------------------------------------------------------
#
# Konu şunu şart koşuyor: "İlaçlamada ürün, doz ve birim zorunlu." V1'de bu
# HİÇ UYGULANMIYORDU — ölçüldü: alansız bir ilaçlama ve dozsuz bir ilaç
# girdisi 201 alıyordu.
#
# Neden önemli: doz ve uygulanan alan olmadan dekar başına ilaç kullanımı
# hesaplanamaz, kalıntı hesabı yapılamaz ve denetimde "ne kadar attınız"
# sorusunun cevabı yoktur. Miktar tek başına yetmez — 5 litre ilacı 10 dekara
# atmakla 100 dekara atmak farklı şeydir.
#
# BİLEREK ZORUNLU TUTULMAYAN: bekleme süreleri. Konu onları "GİRİLDİYSE
# negatif olamaz" diye tanımlıyor, yani opsiyonel. Zorunlu yapmak, süresi
# bilinmeyen bir ilaç için kullanıcıyı rakam uydurmaya iterdi — uydurulmuş bir
# bekleme süresi, hiç olmamasından TEHLİKELİDİR (sistem güvenli sanır).
#
# AÇIK KALAN SORU (owner kararı): "ürün" ile katalog kaydı (`product_id`) mı
# kastediliyor, yoksa girdi adı yeterli mi? Şu an `input_name` zorunlu,
# `product_id` opsiyonel. product_id zorunlu tutmak her zirai ilacın ürün
# kataloğuna girilmesini şart koşardı; owner lot/SKT takibini "ilaç-tohumda
# yok" diyerek kapsam dışı bırakmıştı, bu yüzden burada da dayatılmadı.


# ---------------------------------------------------------------------------
# FİRMA BAZLI KURAL AYARLARI (FAZ 9)
# ---------------------------------------------------------------------------
#
# Alan aşımı, erken hasat ve doz zorunluluğu artık her firmanın kendi kararı
# (bkz. migration 0048). Varsayılanlar mevcut davranışı koruyor.
#
# ERKEN HASATTA "KAPAT" SEVİYESİ YOK — en gevşek seviye `warn`. Kontrolü
# tamamen kapatabilen bir ayar, sessiz bir güvenlik kapatma düğmesi olurdu.
# `warn` modunda istek kabul ediliyor ama sistemin bulduğu ihlal
# `field_harvests.safety_warning` sütununa yazılıyor: gevşetme kaydı yok
# etmiyor, yalnız engellemiyor.

_VARSAYILAN_KURALLAR = {
    "farm_area_override_policy": "require_reason",
    "farm_early_harvest_policy": "require_reason",
    "farm_spraying_dose_required": True,
}


def _firma_kurallari(db: Session, cid: int) -> dict[str, Any]:
    """Firmanın tarla kural ayarları; satır okunamazsa VARSAYILAN (sıkı).

    Okunamayan bir ayarda gevşek tarafa düşmek, bir veritabanı sorununu
    sessizce kural gevşetmesine çevirirdi. Fail-closed.
    """
    row = db.execute(
        text(
            """SELECT farm_area_override_policy,farm_early_harvest_policy,
            farm_spraying_dose_required FROM companies WHERE id=:cid"""
        ),
        {"cid": cid},
    ).mappings().first()
    if not row:
        return dict(_VARSAYILAN_KURALLAR)
    return {
        "farm_area_override_policy": row["farm_area_override_policy"] or "require_reason",
        "farm_early_harvest_policy": row["farm_early_harvest_policy"] or "require_reason",
        # SQLite boolean'ı 0/1 döndürüyor; bool() ikisini de doğru okur.
        "farm_spraying_dose_required": bool(row["farm_spraying_dose_required"]),
    }


def _ilaclama_alani_dogrula(payload: ActivityWrite) -> None:
    """İlaçlamada uygulanan alan zorunlu.

    Diğer faaliyet türlerinde opsiyonel kalıyor: sulamada ya da toprak
    işlemede alan bilinmese de kayıt değerlidir. İlaçlamada değildir — alan
    olmadan doz/dekar hesabı yapılamaz.
    """
    if payload.activity_type != "SPRAYING":
        return
    if payload.applied_area_decare is None:
        raise HTTPException(
            422,
            "İlaçlama kaydında uygulanan alan (dekar) zorunludur; "
            "doz ve kalıntı hesabı buna dayanır",
        )


def _ilac_girdisi_dogrula(
    payload: ActivityInputWrite, faaliyet: dict[str, Any], zorunlu: bool = True,
) -> None:
    """İlaçlama faaliyetine bağlanan girdide doz ve doz birimi zorunlu.

    ``zorunlu`` firma ayarından gelir (`farm_spraying_dose_required`).
    Kapatan firma dekar başına ilaç kullanımını hesaplayamaz; bu bir VERİ
    KALİTESİ tercihidir, güvenlik kapatma değil.
    """
    if not zorunlu:
        return
    if faaliyet.get("activity_type") != "SPRAYING":
        return
    eksik = [
        ad for ad, deger in (("doz", payload.dose), ("doz birimi", payload.dose_unit))
        if deger is None or (isinstance(deger, str) and not deger.strip())
    ]
    if eksik:
        raise HTTPException(
            422,
            f"İlaçlama girdisinde {' ve '.join(eksik)} zorunludur "
            "(örn. 100 ML/DEKAR); miktar tek başına dekar başına kullanımı vermez",
        )


def _girdi_toplami(girdi: Any) -> Decimal | None:
    """Toplam maliyet — TEK yerde türetiliyor.

    İç içe girdi ile ayrı uçtan gelen girdi aynı formülü kullanmalı; iki ayrı
    kopya, birinde yapılan bir düzeltmenin diğerinde unutulmasına açık olurdu.
    """
    if girdi.unit_cost is None:
        return None
    return (Decimal(str(girdi.quantity)) * Decimal(str(girdi.unit_cost))).quantize(Decimal("0.01"))


def _urun_dogrula(db: Session, cid: int, product_id: int) -> None:
    var = db.execute(
        text("SELECT 1 FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).first()
    if not var:
        raise HTTPException(404, "Ürün bulunamadı")


# ---------------------------------------------------------------------------
# ORANIN SATIRA KOPYALANMASI (Gerçek Maliyet FAZ 2, mobil-erp#24)
# ---------------------------------------------------------------------------
#
# `cost_rates` GEÇMİŞ TUTMUYOR, yalnız bugünkü varsayılanı tutuyor (gerekçesi
# migration 0052 başlığında). Geçmişi tutan yer bu satırın kendisi: oran BURADA,
# faaliyet yazılırken kopyalanıyor ve bir daha okunmuyor. Oranı sonradan
# değiştirmek bu satıra DOKUNMAZ — aksi hâlde geçen sezonun kârı bu yıl başka
# çıkardı ve hiçbir rapora güvenilemezdi.
#
# Faaliyetin GÜNCELLEME ucu yok (yalnız POST/GET), yani kopya tek bir yerde ve
# tek bir kez yazılıyor. Yeni bir yazma yolu açılırsa oranın oradan da yeniden
# çözülmemesi gerekir; `work_order_labor_lines.update_labor_line` bu durumda ne
# yapılacağının örneği ("never silently refreshed from the header").
#
# ÇÖZÜM SIRASI hedefin özelinden geneline: önce o makinenin/kişinin kendi oranı,
# yoksa o TÜRÜN firma geneli varsayılanı, o da yoksa BOŞ. Sıra `cost_rates`in üç
# hedef biçimiyle birebir aynı (bkz. migration 0052 başlığı) — başka bir sıra,
# kişiye özel tanımlanmış bir oranı sessizce görmezden gelmek olurdu.

_ORAN_MAKINE = text(
    """SELECT hourly_rate FROM cost_rates
    WHERE company_id=:cid AND kind='MACHINE' AND status='ACTIVE'
      AND machine_id=:machine_id"""
)
_ORAN_KULLANICI = text(
    """SELECT hourly_rate FROM cost_rates
    WHERE company_id=:cid AND kind='LABOR' AND status='ACTIVE'
      AND user_id=:user_id"""
)
_ORAN_GENEL = text(
    """SELECT hourly_rate FROM cost_rates
    WHERE company_id=:cid AND kind=:kind AND status='ACTIVE'
      AND machine_id IS NULL AND user_id IS NULL"""
)


def _oran_kopyala(
    db: Session, cid: int, kind: str, hedef: Any, acik_oran: Any, saat: Any,
) -> Decimal | None:
    """Satıra yazılacak oranı belirler. HER SORGU KİRACI FİLTRELİ.

    ``machine_id``/``user_id`` zaten aynı firmaya ait olduğu doğrulanmış olsa da
    oran sorgusu kendi ``company_id=:cid`` yüklemini taşıyor: `cost_rates` ile
    `machines` arasında bileşik yabancı anahtar YOK (migration 0052), yani
    "makine bizim" bilgisi "orana bakma hakkımız var" anlamına gelmez.

    Saat girilmemişse oran da yazılmaz: maliyeti olmayan bir satırda duran oran
    yalnız kafa karıştırırdı.
    """
    if saat is None:
        return None
    if acik_oran is not None:
        # İstemcinin açıkça gönderdiği oran; tabloya hiç bakılmıyor.
        return money(acik_oran)
    if hedef is not None:
        ozel = db.execute(
            _ORAN_MAKINE if kind == "MACHINE" else _ORAN_KULLANICI,
            {"cid": cid, "machine_id": hedef} if kind == "MACHINE"
            else {"cid": cid, "user_id": hedef},
        ).scalar()
        if ozel is not None:
            return money(ozel)
    genel = db.execute(_ORAN_GENEL, {"cid": cid, "kind": kind}).scalar()
    # Tanımlı oran yoksa BOŞ kalır. Sıfır yazmak "bu iş bedava yapıldı" demek
    # olurdu; doğrusu "maliyeti bilinmiyor" (bkz. `_tutar`).
    return None if genel is None else money(genel)


def _makine_dogrula(db: Session, cid: int, machine_id: int) -> None:
    """Makine AYNI firmaya ait olmalı (uygulama katmanı; bkz. migration 0044)."""
    var = db.execute(
        text("SELECT 1 FROM machines WHERE id=:id AND company_id=:cid"),
        {"id": machine_id, "cid": cid},
    ).first()
    if not var:
        raise HTTPException(404, "Makine bulunamadı")


@router.get("/field-activities/{activity_id}")
def get_activity(activity_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    faaliyet = _faaliyet_satiri(db, cid, activity_id, request)
    girdiler = db.execute(
        text(
            """SELECT id,product_id,input_name,quantity,unit,unit_cost,total_cost,
            dose,dose_unit FROM field_activity_inputs
            WHERE company_id=:cid AND activity_id=:aid ORDER BY id"""
        ),
        {"cid": cid, "aid": activity_id},
    ).mappings().all()
    faaliyet["inputs"] = [dict(r) for r in girdiler]
    return faaliyet


@router.post("/field-activities/{activity_id}/inputs", status_code=201)
def add_activity_input(
    activity_id: int,
    payload: ActivityInputWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    """Faaliyete girdi bağla.

    ``farm.inputs`` iznine açık (depo rolü). V1'DE STOK HAREKETİ ÜRETMEZ —
    yalnız ne kullanıldığını kaydeder.
    """
    cid = company_id(request)
    onceki = _tekrar_mi(db, cid, "activity_input", payload.operation_id, request=request)
    if onceki is not None:
        return onceki
    faaliyet = _faaliyet_satiri(db, cid, activity_id, request)
    _ilac_girdisi_dogrula(
        payload, faaliyet, _firma_kurallari(db, cid)["farm_spraying_dose_required"]
    )
    if payload.product_id is not None:
        _urun_dogrula(db, cid, payload.product_id)
    # TOPLAM MALİYET SUNUCUDA TÜRETİLİR (istemci gönderemez, şemada alan yok).
    # Formül `_girdi_toplami`de TEK yerde: iç içe gönderilen girdi de aynı
    # hesabı kullanıyor, iki kopya arasında sapma olamaz.
    toplam = _girdi_toplami(payload)
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO field_activity_inputs(company_id,activity_id,product_id,
            input_name,quantity,unit,unit_cost,total_cost,dose,dose_unit,
            created_at,updated_at)
            VALUES(:cid,:aid,:product_id,:input_name,:quantity,:unit,:unit_cost,
            :total_cost,:dose,:dose_unit,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "aid": activity_id, "now": now, "total_cost": toplam,
         **payload.model_dump()},
    ).scalar_one()
    yaris = _kuyruga_isle(db, cid, request, "activity_input", payload.operation_id, int(yeni))
    if yaris is not None:
        return yaris
    db.commit()
    return _satir(db, cid, "field_activity_inputs", int(yeni))


# ------------------------------------------------------------------ hasat ---

@router.get("/field-harvests")
def list_harvests(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    season_id: int | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND season_id=:season_id" if season_id else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if season_id:
        params["season_id"] = season_id
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM field_harvests WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,season_id,harvested_on,quantity,unit,harvested_area_decare,
            quality_grade,moisture_percent,sold_quantity,revenue_amount,
            safety_override_reason,safety_warning,notes,status,updated_at
            FROM field_harvests WHERE company_id=:cid{kosul}
            ORDER BY harvested_on DESC,id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [_hasat_yaniti(dict(r)) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.get("/field-harvest-decision")
def harvest_decision(
    request: Request,
    season_id: int = Query(gt=0),
    harvested_on: date = Query(),
    db: Session = Depends(get_db),
):
    """Seçilen sezon ve TARİH için PHI kararını sunucu tarafında üretir."""
    cid = company_id(request)
    _satir(db, cid, "crop_seasons", season_id)
    politika = _firma_kurallari(db, cid)["farm_early_harvest_policy"]
    ihlaller = _bekleme_ihlalleri(db, cid, season_id, harvested_on)
    if not ihlaller:
        karar = "safe"
        uyari = None
    else:
        karar = {
            "warn": "warning",
            "require_reason": "reason_required",
            "block": "blocked",
        }.get(politika, "reason_required")
        uyari = _hasat_ihlal_metni(ihlaller)
    return {
        "season_id": season_id,
        "harvested_on": harvested_on.isoformat(),
        "policy": politika,
        "decision": karar,
        "safe_from": ihlaller[0]["safe_from"] if ihlaller else None,
        "warning": uyari,
        "blocking": ihlaller,
    }


@router.post("/field-harvests", status_code=201)
def create_harvest(payload: HarvestWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    onceki = _tekrar_mi(db, cid, "harvest", payload.operation_id, request=request)
    if onceki is not None:
        return onceki
    sezon = _satir(db, cid, "crop_seasons", payload.season_id)
    # HASAT SEZON BAŞLANGICINDAN ÖNCE OLAMAZ. Veritabanı bunu bilemez (iki ayrı
    # tablo); kontrol burada.
    baslangic = sezon.get("started_on")
    if baslangic:
        if isinstance(baslangic, str):
            from datetime import date as _date
            baslangic = _date.fromisoformat(baslangic[:10])
        if payload.harvested_on < baslangic:
            raise HTTPException(
                422, f"Hasat tarihi sezon başlangıcından ({baslangic}) önce olamaz"
            )
    uyari = _hasat_guvenlik_dogrula(
        db, cid, payload, _firma_kurallari(db, cid)["farm_early_harvest_policy"]
    )
    # SATILAN MİKTAR HASADI AŞAMAZ. Aşması fiziken imkânsız ve sessiz geçmesi
    # dekar başına geliri şişirirdi; veritabanı bunu bilemez (iki sütun aynı
    # satırda ama ilişki iş kuralı).
    if (
        payload.sold_quantity is not None
        and Decimal(str(payload.sold_quantity)) > Decimal(str(payload.quantity))
    ):
        raise HTTPException(
            422,
            f"Satılan miktar ({payload.sold_quantity}) hasat miktarını "
            f"({payload.quantity}) aşamaz",
        )
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO field_harvests(company_id,season_id,harvested_on,quantity,unit,
            harvested_area_decare,quality_grade,moisture_percent,notes,
            safety_override_reason,safety_warning,sold_quantity,revenue_amount,
            status,created_at,updated_at)
            VALUES(:cid,:season_id,:harvested_on,:quantity,:unit,:harvested_area_decare,
            :quality_grade,:moisture_percent,:notes,:safety_override_reason,
            :safety_warning,:sold_quantity,:revenue_amount,'RECORDED',:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, "safety_warning": uyari, **payload.model_dump()},
    ).scalar_one()
    # FAZ 4 OUTBOX — HASAT DİLİMİ. Hasat STOK GİRİŞİdir: olayı düşen bir
    # hasat, fiziken var olan ürünün hiç kaydedilmemesi demektir. Bu yüzden
    # hasat ve olayı TEK işlemde; hasat geri alınırsa olay da geri alınır,
    # olay yazılamazsa hasat da yazılmaz.
    _entegrasyon_olayi_yaz(
        db, cid, _ENTEGRASYON_KAYNAGI_HASAT, int(yeni),
        _ENTEGRASYON_HEDEFI_STOK, now,
    )

    yaris = _kuyruga_isle(db, cid, request, "harvest", payload.operation_id, int(yeni))
    if yaris is not None:
        return yaris
    db.commit()
    return _hasat_yaniti(_satir(db, cid, "field_harvests", int(yeni)))


# ------------------------------------------------------------------ görev ---

@router.get("/field-tasks")
def list_tasks(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = " AND status=:status" if status else ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if status:
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(f"SELECT COUNT(*) FROM field_tasks WHERE company_id=:cid{kosul}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""SELECT id,season_id,parcel_id,title,due_date,status,priority,
            assigned_user_id,activity_id,notes,updated_at
            FROM field_tasks WHERE company_id=:cid{kosul}
            ORDER BY due_date IS NULL,due_date,id LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/field-tasks", status_code=201)
def create_task(payload: TaskWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    season_id, parcel_id = _gorev_baglantilari(
        db, cid, payload.season_id, payload.parcel_id, request=request
    )
    if payload.assigned_user_id is not None:
        _aktif_firma_kullanicisi_dogrula(
            db, cid, payload.assigned_user_id, "Atanan kullanıcı"
        )
    veri = payload.model_dump()
    veri.update({"season_id": season_id, "parcel_id": parcel_id})
    now = _simdi()
    yeni = db.execute(
        text(
            """INSERT INTO field_tasks(company_id,season_id,parcel_id,title,due_date,
            status,priority,assigned_user_id,notes,created_at,updated_at)
            VALUES(:cid,:season_id,:parcel_id,:title,:due_date,'OPEN',:priority,
            :assigned_user_id,:notes,:now,:now) RETURNING id"""
        ),
        {"cid": cid, "now": now, **veri},
    ).scalar_one()
    db.commit()
    return _satir(db, cid, "field_tasks", int(yeni))


@router.put("/field-tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    mevcut = _satir(db, cid, "field_tasks", task_id)
    beklenen_surum = _surum_dogrula(mevcut, payload.expected_updated_at)
    season_id, parcel_id = _gorev_baglantilari(
        db, cid, payload.season_id, payload.parcel_id, payload.activity_id,
        request=request,
    )
    if payload.assigned_user_id is not None:
        _aktif_firma_kullanicisi_dogrula(
            db, cid, payload.assigned_user_id, "Atanan kullanıcı"
        )
    veri = payload.model_dump(exclude={"expected_updated_at"})
    veri.update({"season_id": season_id, "parcel_id": parcel_id})
    sonuc = db.execute(
        text(
            """UPDATE field_tasks SET season_id=:season_id,parcel_id=:parcel_id,
            title=:title,due_date=:due_date,status=:status,priority=:priority,
            assigned_user_id=:assigned_user_id,activity_id=:activity_id,notes=:notes,
            updated_at=:now WHERE id=:id AND company_id=:cid
            AND updated_at=:expected_updated_at"""
        ),
        {"id": task_id, "cid": cid, "now": _simdi(),
         "expected_updated_at": beklenen_surum, **veri},
    )
    _cas_sonuc_dogrula(db, sonuc)
    db.commit()
    return _satir(db, cid, "field_tasks", task_id)


def _gorev_baglantilari(
    db: Session,
    cid: int,
    season_id: int | None,
    parcel_id: int | None,
    activity_id: int | None = None,
    *,
    request: Request,
) -> tuple[int | None, int | None]:
    """Faaliyet -> sezon -> parsel zincirini doğrular ve eksik üst bağları türetir.

    ``request`` YALNIZ faaliyet satırını tek kapıdan (`_faaliyet_satiri`) okumak
    için var; burada okunan tek alan ``season_id`` ve maskeden etkilenmiyor.
    """
    if activity_id is not None:
        faaliyet = _faaliyet_satiri(db, cid, activity_id, request)
        faaliyet_sezonu = int(faaliyet["season_id"])
        if season_id is not None and season_id != faaliyet_sezonu:
            raise HTTPException(422, "Görev faaliyeti seçilen sezona ait değil")
        season_id = faaliyet_sezonu

    if season_id is not None:
        sezon = _satir(db, cid, "crop_seasons", season_id)
        sezon_parseli = int(sezon["parcel_id"])
        if parcel_id is not None and parcel_id != sezon_parseli:
            raise HTTPException(422, "Görev sezonu seçilen parsele ait değil")
        parcel_id = sezon_parseli
    elif parcel_id is not None:
        _satir(db, cid, "farm_parcels", parcel_id)

    return season_id, parcel_id


# -------------------------------------------------------------- dashboard ---

@router.get("/field-safety")
def field_safety(request: Request, db: Session = Depends(get_db)):
    """Şu anda yürürlükte olan ilaç güvenlik kısıtları.

    İKİ AYRI KISIT, ikisi farklı kişiyi ilgilendiriyor ve bilerek ayrı
    listeleniyor:

    * ``harvest_blocks`` — hasat bekleme süresi dolmamış SEZONLAR. Hasadı
      planlayan kişi bunu görmeli.
    * ``reentry_blocks`` — tarlaya giriş yasağı sürmekte olan PARSELLER. Bu,
      hasatla ilgisi olmayan bir iş için tarlaya girecek kişiyi de ilgilendirir
      (sulama, gübreleme); tek listede birleştirmek onu gizlerdi.

    Kısıtlar BUGÜNE göre hesaplanıyor; süresi geçmiş olanlar listede yok.
    """
    cid = company_id(request)
    bugun = business_today()

    sezonlar = db.execute(
        text(
            """SELECT s.id,s.crop,s.season_year,p.name parcel_name
            FROM crop_seasons s
            JOIN farm_parcels p ON p.id=s.parcel_id AND p.company_id=s.company_id
            WHERE s.company_id=:cid AND s.status IN ('ACTIVE','PLANNED')"""
        ),
        {"cid": cid},
    ).mappings().all()

    hasat_kisitlari = []
    for sz in sezonlar:
        ihlaller = _bekleme_ihlalleri(db, cid, int(sz["id"]), bugun)
        if ihlaller:
            hasat_kisitlari.append({
                "season_id": int(sz["id"]),
                "crop": sz["crop"],
                "season_year": int(sz["season_year"]),
                "parcel_name": sz["parcel_name"],
                "safe_from": ihlaller[0]["safe_from"],
                "blocking": ihlaller,
            })

    giris_satirlari = db.execute(
        text(
            """SELECT a.id,a.activity_type,a.performed_at,a.reentry_interval_days,
            p.id parcel_id,p.name parcel_name
            FROM field_activities a
            JOIN crop_seasons s ON s.id=a.season_id AND s.company_id=a.company_id
            JOIN farm_parcels p ON p.id=s.parcel_id AND p.company_id=s.company_id
            WHERE a.company_id=:cid AND a.reentry_interval_days IS NOT NULL
              AND a.status='RECORDED'"""
        ),
        {"cid": cid},
    ).mappings().all()

    giris_kisitlari = []
    for r in giris_satirlari:
        gun = int(r["reentry_interval_days"])
        guvenli = _yerel_gun(r["performed_at"]) + timedelta(days=gun)
        if bugun < guvenli:
            giris_kisitlari.append({
                "parcel_id": int(r["parcel_id"]),
                "parcel_name": r["parcel_name"],
                "activity_id": int(r["id"]),
                "activity_type": r["activity_type"],
                "performed_on": _yerel_gun(r["performed_at"]).isoformat(),
                "interval_days": gun,
                "safe_from": guvenli.isoformat(),
            })
    giris_kisitlari.sort(key=lambda x: x["safe_from"], reverse=True)

    return {
        "as_of": bugun.isoformat(),
        "harvest_blocks": hasat_kisitlari,
        "reentry_blocks": giris_kisitlari,
    }


@router.get("/field-dashboard")
def field_dashboard(request: Request, db: Session = Depends(get_db)):
    """İlk görünümde YALNIZ karar verdiren bilgi (issue'nun ekran kuralı).

    Ayrıntı değil özet: kaç parsel, kaç dekar, açık sezon, geciken/yaklaşan iş,
    ve sezon başına gerçek maliyet + dekar başına verim.

    Maliyet ``field_activity_inputs.total_cost`` toplamıdır — o değer de
    sunucuda türetilmişti, yani rapor istemciden gelen bir sayıya dayanmıyor.
    """
    cid = company_id(request)
    bugun = _simdi().date()

    ozet = db.execute(
        text(
            """SELECT
            (SELECT COUNT(*) FROM farms WHERE company_id=:cid AND status='ACTIVE') farm_count,
            (SELECT COUNT(*) FROM farm_parcels WHERE company_id=:cid AND status='ACTIVE') parcel_count,
            (SELECT COALESCE(SUM(area_decare),0) FROM farm_parcels
             WHERE company_id=:cid AND status='ACTIVE') total_area_decare,
            (SELECT COUNT(*) FROM crop_seasons WHERE company_id=:cid AND status='ACTIVE') active_season_count"""
        ),
        {"cid": cid},
    ).mappings().first()

    gorevler = db.execute(
        text(
            """SELECT
            SUM(CASE WHEN due_date IS NOT NULL AND due_date < :bugun THEN 1 ELSE 0 END) overdue,
            SUM(CASE WHEN due_date = :bugun THEN 1 ELSE 0 END) due_today,
            SUM(CASE WHEN due_date IS NOT NULL AND due_date > :bugun THEN 1 ELSE 0 END) upcoming
            FROM field_tasks WHERE company_id=:cid AND status='OPEN'"""
        ),
        {"cid": cid, "bugun": bugun},
    ).mappings().first()

    # Sezon başına maliyet ve verim. Girdi maliyeti faaliyet üzerinden sezona
    # bağlanıyor; hasat ayrı toplanıyor — ikisini tek sorguda toplamak
    # kartezyen çarpım üretip maliyeti hasat satırı sayısı kadar şişirirdi.
    sezonlar = db.execute(
        text(
            """SELECT s.id,s.season_year,s.crop,s.status,s.planted_area_decare,
            p.name parcel_name,p.area_decare parcel_area,
            COALESCE((SELECT SUM(i.total_cost) FROM field_activity_inputs i
                      JOIN field_activities a ON a.id=i.activity_id AND a.company_id=i.company_id
                      WHERE i.company_id=:cid AND a.season_id=s.id),0) total_cost,
            COALESCE((SELECT SUM(h.quantity) FROM field_harvests h
                      WHERE h.company_id=:cid AND h.season_id=s.id
                        AND h.status='RECORDED'),0) harvest_quantity,
            -- Gelir AYRI alt sorgu: maliyetle tek sorguda toplamak kartezyen
            -- çarpım üretirdi (bkz. yukarıdaki not).
            COALESCE((SELECT SUM(h.revenue_amount) FROM field_harvests h
                      WHERE h.company_id=:cid AND h.season_id=s.id
                        AND h.status='RECORDED'),0) revenue_amount,
            -- Geliri GİRİLMİŞ hasat var mı? Sıfır ile "henüz bilinmiyor"u
            -- ayırmak için şart: ikisini aynı göstermek, satılmamış bir sezonu
            -- zarar etmiş gibi gösterirdi.
            (SELECT COUNT(*) FROM field_harvests h
             WHERE h.company_id=:cid AND h.season_id=s.id
               AND h.status='RECORDED' AND h.revenue_amount IS NOT NULL) revenue_rows
            FROM crop_seasons s
            JOIN farm_parcels p ON p.id=s.parcel_id AND p.company_id=s.company_id
            WHERE s.company_id=:cid AND s.status IN ('ACTIVE','HARVESTED')
            ORDER BY s.season_year DESC,s.id DESC"""
        ),
        {"cid": cid},
    ).mappings().all()

    # İŞÇİLİK/MAKİNE TOPLAMI SQL'DE DEĞİL PYTHON'DA. `SUM(saat * oran)` iki
    # diyalektte aynı sonucu vermez (SQLite NUMERIC'i REAL'e düşürür) ve para
    # float'a dönerdi — FAZ 1'de tam bu sapma CI'da yaşandı. Satırlar
    # `_faaliyet_yaniti`den geçip Decimal olarak toplanıyor; maskeli çağıranda
    # maliyet alanları hiç gelmediği için toplama da giremiyorlar.
    dashboard_sezon_ids = [int(r["id"]) for r in sezonlar]
    emek_maliyetleri: dict[int, Decimal] = {}
    eksik_oranlar: dict[int, int] = {}
    if dashboard_sezon_ids:
        dashboard_faaliyetleri = [_faaliyet_yaniti(dict(r), request) for r in db.execute(
            text(
                """SELECT a.season_id,a.labor_hours,a.labor_hourly_rate,
                a.machine_hours,a.machine_hourly_rate
                FROM field_activities a
                WHERE a.company_id=:cid AND a.season_id IN :ids AND a.status='RECORDED'"""
            ).bindparams(bindparam("ids", expanding=True)),
            {"cid": cid, "ids": dashboard_sezon_ids},
        ).mappings().all()]
        emek_maliyetleri, eksik_oranlar = _emek_maliyeti(dashboard_faaliyetleri)

    sezon_ozet = []
    for r in sezonlar:
        sid = int(r["id"])
        alan = Decimal(str(r["planted_area_decare"] or r["parcel_area"] or 0))
        blok = _maliyet_ozeti(
            Decimal(str(r["total_cost"] or 0)), emek_maliyetleri.get(sid, Decimal("0")),
            eksik_oranlar.get(sid, 0), request,
        )
        maliyet = Decimal(blok["total_cost"])
        miktar = Decimal(str(r["harvest_quantity"] or 0))
        gelir_var = int(r["revenue_rows"] or 0) > 0
        gelir = Decimal(str(r["revenue_amount"] or 0)) if gelir_var else None
        sezon_ozet.append({
            "season_id": sid,
            "season_year": int(r["season_year"]),
            "crop": r["crop"],
            "status": r["status"],
            "parcel_name": r["parcel_name"],
            "area_decare": str(alan),
            **blok,
            "harvest_quantity": str(miktar),
            # Sıfıra bölmeyi ÖNCEDEN eleme: alansız sezonda oran anlamsız,
            # None dönmek "hesaplanamadı"yı dürüstçe söyler.
            "yield_per_decare": str((miktar / alan).quantize(Decimal("0.0001"))) if alan > 0 else None,
            "cost_per_decare": str((maliyet / alan).quantize(Decimal("0.01"))) if alan > 0 else None,
            # Gelir girilmemişse None — SIFIR DEĞİL. Sıfır göstermek, ürünü
            # henüz satmamış bir sezonu "hiç kazanmadı" diye gösterirdi.
            # Gelir de SABİT ÖLÇEKLİ METİN — bkz. timeline'daki aynı alan.
            "revenue_amount": str(money(gelir)) if gelir is not None else None,
            # FAZ 3'ten beri yetkili çağıranda işçilik ve makine de düşülüyor;
            # `cost_basis` hangi tabanın kullanıldığını söylüyor. Yakıt ve genel
            # giderler hâlâ dışarıda — o veriler toplanmıyor, dolayısıyla bu
            # hâlâ tam kâr değil.
            "gross_margin": str(money(gelir - maliyet)) if gelir is not None else None,
        })

    return {
        "summary": {
            "farm_count": int(ozet["farm_count"] or 0),
            "parcel_count": int(ozet["parcel_count"] or 0),
            "total_area_decare": str(Decimal(str(ozet["total_area_decare"] or 0))),
            "active_season_count": int(ozet["active_season_count"] or 0),
        },
        "tasks": {
            "overdue": int(gorevler["overdue"] or 0),
            "due_today": int(gorevler["due_today"] or 0),
            "upcoming": int(gorevler["upcoming"] or 0),
        },
        "seasons": sezon_ozet,
    }


# ---------------------------------------------------------------------------
# BKÜ KATALOĞU VE PHI KÖKENİ (göç 20260901_0063)
# ---------------------------------------------------------------------------
#
# PHI kilidi 0046/0048'den beri çalışıyor ama beslendiği sayı ELLE giriliyordu:
# operatör yazmayı unuttuğunda kilit sessizce hiçbir şey yapmıyordu. Katalog o
# sayının ETİKETTEN gelen kaydı.
#
# ÜÇ KURAL, ÜÇÜ DE BİLİNÇLİ:
#
# 1. KATALOG ÖNERİR, OPERATÖR KARAR VERİR. Operatörün girdiği değer HER ZAMAN
#    kazanır. Katalog sahayı ezseydi, etiketi elinde tutan kişi sistemi
#    düzeltemez hâle gelirdi.
# 2. KÖKEN KAYIT ALTINDA. Üstüne yazma SESSİZ OLAMAZ; hangi değerin nereden
#    geldiği ayrı sütunlarda duruyor (0048'in kurduğu desen).
# 3. BOŞUN ANLAMI DEĞİŞMEDİ. Katalog da bir şey bulamazsa süre BOŞ kalır ve boş
#    hâlâ ihlal DEĞİLDİR (`_bekleme_ihlalleri`). Bu göç boşun anlamını değil,
#    boş kalma SIKLIĞINI düşürüyor.

_PHI_KOKEN_KATALOG = "CATALOGUE"
_PHI_KOKEN_OPERATOR = "OPERATOR"
_PHI_KOKEN_USTUNE_YAZMA = "OPERATOR_OVERRIDE"


def _bitki_esit(a: str, b: str) -> bool:
    """Bitki adları SERBEST METİN; karşılaştırma Python'da yapılıyor.

    SQL'de ``LOWER()`` ile karşılaştırmak cazipti ama Türkçe'de İ/ı eşlemesi
    DİYALEKTE BAĞLI: SQLite'ın ``LOWER``ı ASCII dışına dokunmaz, PostgreSQL'inki
    yerel ayara göre davranır. İki diyalektin AYNI kataloğu farklı çözmesi,
    ikizi koşulan bir testin yakalayamayacağı bir sapma olurdu — hesabı
    tek bir yerde, Python'da tutuyoruz.
    """
    return a.casefold() == b.casefold()


def _katalog_phi(db: Session, cid: int, product_id: int, bitki: str) -> int | None:
    """Ürün için katalogdaki PHI; bitkiye ÖZEL satır varsa onu tercih eder.

    Bitkiden bağımsız satır (``crop=''``) yedek: firma tek satırla başlayıp
    gerektiğinde bitkiye özelleştirebilsin diye.
    """
    satirlar = db.execute(
        text(
            """SELECT crop,preharvest_interval_days
            FROM plant_protection_products
            WHERE company_id=:cid AND product_id=:pid AND status='ACTIVE'"""
        ),
        {"cid": cid, "pid": int(product_id)},
    ).mappings().all()

    genel: int | None = None
    for r in satirlar:
        katalog_bitki = (r["crop"] or "").strip()
        if katalog_bitki and _bitki_esit(katalog_bitki, bitki):
            # Bitkiye ÖZEL satır bulundu; yedeğe bakmaya gerek yok.
            return int(r["preharvest_interval_days"])
        if not katalog_bitki:
            genel = int(r["preharvest_interval_days"])
    return genel


def _phi_coz(
    db: Session, cid: int, payload: ActivityWrite, sezon: dict[str, Any],
) -> tuple[int | None, int | None, str | None]:
    """``(katalogun_dediği, etkin_değer, köken)``.

    Birden çok girdi varsa EN UZUN bekleme kazanır: iki ilaç atıldığında
    kısa olanı seçmek, uzun olanın süresi dolmadan hasada izin verirdi.
    """
    bitki = str(sezon.get("crop") or "").strip()
    katalog: int | None = None
    for girdi in payload.inputs or []:
        if girdi.product_id is None:
            # Serbest metin girdi (kendi ürettiği gübre) — etiketi yok, çözülmez.
            continue
        gun = _katalog_phi(db, cid, girdi.product_id, bitki)
        if gun is not None and (katalog is None or gun > katalog):
            katalog = gun

    operator = payload.preharvest_interval_days
    if operator is None:
        if katalog is None:
            # Ne operatör ne katalog: süre BOŞ kalır ve boş ihlal değildir.
            return None, None, None
        return katalog, katalog, _PHI_KOKEN_KATALOG
    if katalog is None:
        return None, operator, _PHI_KOKEN_OPERATOR
    # Katalog da konuştu, operatör de. Operatör kazanır — ama aynı şeyi
    # söylüyorlarsa bu bir ÜSTÜNE YAZMA DEĞİLDİR; her uyuşmazlığı üstüne yazma
    # saymak denetimde gerçek üstüne yazmaları görünmez kılardı.
    if operator == katalog:
        return katalog, operator, _PHI_KOKEN_OPERATOR
    return katalog, operator, _PHI_KOKEN_USTUNE_YAZMA


@router.get("/plant-protection-products")
def list_ppp(
    request: Request,
    limit: int = _SAYFA,
    offset: int = _ATLA,
    product_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    kosul = ""
    params: dict[str, Any] = {"cid": cid, "limit": limit, "offset": offset}
    if product_id:
        kosul += " AND k.product_id=:product_id"
        params["product_id"] = product_id
    if status:
        kosul += " AND k.status=:status"
        params["status"] = status.strip().upper()
    toplam = db.execute(
        text(
            f"SELECT COUNT(*) FROM plant_protection_products k "
            f"WHERE k.company_id=:cid{kosul}"
        ),
        params,
    ).scalar()
    # ÜRÜN ADI BİRLEŞTİRİLEREK GELİYOR: ekran ham `product_id` gösterseydi
    # kullanıcı hangi ilacın kaydını düzenlediğini bilemezdi. Birleştirme
    # KİRACI İÇİNDE: `u.company_id=k.company_id` olmadan başka firmanın ürün
    # adı bu listeye düşebilirdi.
    rows = db.execute(
        text(
            f"""SELECT k.id,k.product_id,u.name AS product_name,k.crop,
            k.registration_no,k.preharvest_interval_days,
            k.reentry_interval_days,k.notes,k.status,
            k.origin,k.origin_reference,k.updated_at
            FROM plant_protection_products k
            JOIN products u ON u.id=k.product_id AND u.company_id=k.company_id
            WHERE k.company_id=:cid{kosul}
            ORDER BY u.name,k.crop LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(toplam or 0),
            "limit": limit, "offset": offset}


@router.post("/plant-protection-products", status_code=201)
def create_ppp(
    payload: PlantProtectionProductWrite, request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # Ürün AYNI firmaya ait olmalı. Veritabanındaki bileşik yabancı anahtar da
    # bunu zorluyor; buradaki kontrol kullanıcıya 404 veriyor, 500 değil.
    _urun_dogrula(db, cid, payload.product_id)
    now = _simdi()
    try:
        yeni = db.execute(
            text(
                """INSERT INTO plant_protection_products(company_id,product_id,crop,
                registration_no,preharvest_interval_days,reentry_interval_days,
                notes,status,origin,origin_reference,created_at,updated_at)
                VALUES(:cid,:product_id,:crop,:registration_no,
                :preharvest_interval_days,:reentry_interval_days,:notes,'ACTIVE',
                'MANUAL',NULL,:now,:now) RETURNING id"""
            ),
            {"cid": cid, "now": now, **payload.model_dump()},
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Bu ürün ve bitki için katalog kaydı zaten var"
        ) from exc
    db.commit()
    return _satir(db, cid, "plant_protection_products", int(yeni))


@router.get("/plant-protection-products/{ppp_id}")
def get_ppp(ppp_id: int, request: Request, db: Session = Depends(get_db)):
    return _satir(db, company_id(request), "plant_protection_products", ppp_id)


@router.put("/plant-protection-products/{ppp_id}")
def update_ppp(
    ppp_id: int, payload: PlantProtectionProductUpdate, request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    mevcut = _satir(db, cid, "plant_protection_products", ppp_id)
    beklenen_surum = _surum_dogrula(mevcut, payload.expected_updated_at)
    _urun_dogrula(db, cid, payload.product_id)
    veri = payload.model_dump(exclude={"expected_updated_at"})
    try:
        sonuc = db.execute(
            text(
                """UPDATE plant_protection_products SET product_id=:product_id,
                crop=:crop,registration_no=:registration_no,
                preharvest_interval_days=:preharvest_interval_days,
                reentry_interval_days=:reentry_interval_days,notes=:notes,
                status=:status,updated_at=:now
                WHERE id=:id AND company_id=:cid AND updated_at=:expected_updated_at"""
            ),
            {"id": ppp_id, "cid": cid, "now": _simdi(),
             "expected_updated_at": beklenen_surum, **veri},
        )
        _cas_sonuc_dogrula(db, sonuc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Bu ürün ve bitki için katalog kaydı zaten var"
        ) from exc
    db.commit()
    return _satir(db, cid, "plant_protection_products", ppp_id)


# ---------------------------------------------------------------------------
# KATALOĞUN DOSYADAN DOLDURULMASI (göç 20260901_0064)
# ---------------------------------------------------------------------------
#
# 0063 kataloğu açtı ama doldurma yolu TEK TEK FORM'du. BKÜ listesi kalabalık
# olan bir firma için bu gerçek bir veri girişi yüküdür ve katalog boş kaldığı
# sürece PHI kilidi 0063 öncesindeki gibi susmaya devam eder. Bu uç o yükü
# kaldırıyor.
#
# DÖRT KURAL, DÖRDÜ DE BİLİNÇLİ:
#
# 1. BİR BOZUK SATIR YALNIZ KENDİSİNİ REDDEDER, DOSYAYI DEĞİL. 200 satırlık
#    listede bir yazım hatası olan çiftçi diğer 199'u kaybetmemeli. Ama SESSİZ
#    kısmi başarı da yok: yanıt reddedilen HER satırı kendi satır numarası ve
#    gerekçesiyle sayıyor, SAYI vermiyor — "3 satır atlandı" kullanıcıya
#    hangisini düzelteceğini SÖYLEMEZ.
#
# 2. MEVCUT SATIRLA ÇAKIŞMA REDDEDİLİR, GÜNCELLENMEZ. Bu, deponun DİĞER
#    içe aktarmalarından (`routers/imports.py`, müşteri/ürün) bilerek AYRILIR:
#    onlar eşleşeni günceller. Burada güncelleme YANLIŞ olurdu — katalogdaki
#    değer bir insanın ETİKETE BAKARAK yazdığı yasal bir süredir ve bir dosya
#    onu sessizce ezerse, ezildiği kimseye görünmez. Atlamak da yanlış olurdu:
#    kullanıcı listesinin tamamının yazıldığını sanırdı. Satır REDDEDİLİR ve
#    çakıştığı kaydın kimliği SÖYLENİR; kullanıcı karar verip yeniden yükler.
#
# 3. KÖKEN KAYIT ALTINDA. Yazılan her satır `origin='IMPORT'` ve
#    `origin_reference='<dosya>:<satır>'` taşır. Gerekçe 0064'ün başlığında:
#    "katalogdaki 21 nereden geldi" sorusunun cevabı bir adım daha geriye,
#    firmanın KENDİ dosyasına kadar gidiyor.
#
# 4. DOSYA FİRMANIN KENDİ DOSYASIDIR. Başlangıç listesi, paketlenmiş bakanlık
#    verisi, örnek katalog YOK — 0063'ün duruşu burada da geçerli: depo hiçbir
#    PHI rakamı iddia etmez. Bu uç yalnız firmanın getirdiğini okur.

#: Satırın hangi ürünü tarif ettiği ÖNCE koddan, sonra addan çözülür. Kod
#: kısa ve makineden gelir; ad insanın yazdığıdır. `products`ta İKİSİ DE
#: TEKİL DEĞİL (`core_schema.py`: yalnız indeksli), dolayısıyla ikisi de
#: birden fazla ürüne uyabilir — o durumda satır reddedilir, çünkü
#: "muhtemelen bunu kastetti" diye seçmek yasal bir bekleme süresini YANLIŞ
#: ürüne bağlardı.
_ICE_AKTARMA_BASLIKLARI: dict[str, list[str]] = {
    "product_code": ["Ürün Kodu", "Urun Kodu", "Stok Kodu", "Kod", "product_code"],
    "product_name": ["Ürün Adı", "Urun Adi", "Ürün", "İlaç", "Ilac", "product_name"],
    "crop": ["Bitki", "Kültür", "Kultur", "crop"],
    "registration_no": ["Ruhsat No", "Ruhsat Numarası", "Ruhsat", "registration_no"],
    "preharvest_interval_days": [
        "Hasat Bekleme (Gün)", "Hasat Bekleme", "Hasat Öncesi Bekleme",
        "PHI", "PHI (Gün)", "preharvest_interval_days",
    ],
    "reentry_interval_days": [
        "Giriş Yasağı (Gün)", "Giris Yasagi (Gun)", "Giriş Yasağı",
        "Tarlaya Giriş Yasağı", "reentry_interval_days",
    ],
    "notes": ["Not", "Notlar", "Açıklama", "Aciklama", "notes"],
}

#: 0063'ün `plant_protection_products` şemasındaki `CHECK` ile ve
#: `PlantProtectionProductWrite` ile AYNI sınır. Üç yer farklı söyleseydi
#: dosyadan geçen bir değer forma girilemez ya da veritabanına yazılamaz
#: olurdu ve hata kullanıcıya anlamsız görünürdü.
_PHI_EN_COK = 3650
_BITKI_EN_UZUN = 120
_RUHSAT_EN_UZUN = 60
#: `origin_reference` sütununun genişliği (göç 0064). Dosya adı kullanıcıdan
#: gelir ve uzun olabilir; kaydın YAZILMASINI engellememeli, o yüzden işaret
#: kırpılır — kırpılmış bir işaret, hiç olmayan işaretten iyidir.
_KOKEN_ISARETI_EN_UZUN = 255


def _ice_aktarma_tamsayi(ham: Any) -> int | None:
    """Hücreden tamsayı; çözülemezse ``None``.

    ``int(float(...))`` KISA YOLU BİLEREK KULLANILMIYOR. Elektronik tablo bir
    tam sayıyı ``21.0`` olarak verir ve o yol bunu 21 yapar — ama AYNI yol
    ``20,6``yı da sessizce 20 yapardı. Bekleme süresi bir GÜN sayısıdır ve
    aşağı yuvarlanmış bir gün, süresi dolmadan hasada izin verir. Sıfır
    olmayan kesir REDDEDİLİR; ``21,0`` bir yazım değil BİÇİM olduğu için
    kabul edilir.
    """
    if ham is None:
        return None
    if isinstance(ham, bool):
        # `bool` Python'da `int`tir; "Evet/Hayır" yazılmış bir hücre 1 güne
        # dönüşmemeli.
        return None
    if isinstance(ham, int):
        return ham
    if isinstance(ham, float):
        return int(ham) if ham.is_integer() else None
    metin = str(ham).strip()
    if not metin:
        return None
    if "," in metin or "." in metin:
        gövde, ayrac, kesir = metin.replace(",", ".").rpartition(".")
        if ayrac and gövde and kesir and set(kesir) == {"0"}:
            metin = gövde
    try:
        return int(metin)
    except ValueError:
        return None


def _ice_aktarma_urun_coz(
    db: Session, cid: int, kod: str, ad: str,
) -> tuple[int | None, str]:
    """``(product_id, hata)``. Kod ADDAN önce gelir; belirsizlik REDDEDİLİR.

    Aynı kodu ya da adı iki ürün taşıyorsa hangisinin kastedildiği BİLİNMEZ
    ve tahmin etmek yasal bir bekleme süresini yanlış ürüne bağlardı. Satır
    reddedilir; kullanıcı kodla ayırt eder.
    """
    if kod:
        satirlar = db.execute(
            text(
                "SELECT id FROM products WHERE company_id=:cid "
                "AND LOWER(product_code)=LOWER(:kod)"
            ),
            {"cid": cid, "kod": kod},
        ).scalars().all()
        if len(satirlar) == 1:
            return int(satirlar[0]), ""
        if len(satirlar) > 1:
            return None, (
                f"'{kod}' kodunu {len(satirlar)} ürün taşıyor; hangisi olduğu belirsiz."
            )
        if not ad:
            return None, f"'{kod}' kodlu ürün bulunamadı."
    if not ad:
        return None, "Ürün kodu ve ürün adı sütunlarının ikisi de boş."
    satirlar = db.execute(
        text("SELECT id FROM products WHERE company_id=:cid AND LOWER(name)=LOWER(:ad)"),
        {"cid": cid, "ad": ad},
    ).scalars().all()
    if len(satirlar) == 1:
        return int(satirlar[0]), ""
    if len(satirlar) > 1:
        return None, (
            f"'{ad}' adını {len(satirlar)} ürün taşıyor; ürün kodu sütunu ile ayırt edin."
        )
    return None, f"'{ad}' adlı ürün bulunamadı."


@router.post("/plant-protection-products/import")
async def import_ppp(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Kataloğu firmanın KENDİ dosyasından doldurur; bozuk satırı ADIYLA reddeder.

    Yanıt biçimi deponun diğer içe aktarmalarından (`inserted/updated/errors`)
    BİLEREK farklı: bu uç HİÇBİR ŞEYİ GÜNCELLEMEZ, dolayısıyla `updated` alanı
    her zaman sıfır olan ve okuyanı "acaba ne güncellendi" diye düşündüren bir
    alan olurdu. `rejected` bir SAYI değil LİSTEdir ve her öğesi kendi satır
    numarasını taşır.
    """
    cid = company_id(request)
    dosya_adi = (file.filename or "").strip() or "liste"
    basliklar, satirlar = await _read_tabular_upload(file)
    esleme = _map(basliklar, _ICE_AKTARMA_BASLIKLARI)
    # BAŞLIK EKSİKLİĞİ DOSYAYI REDDEDER, SATIRI DEĞİL — ve bu, "bir bozuk satır
    # dosyayı düşürmez" kuralıyla ÇELİŞMEZ: eksik sütun her satırı AYNI şekilde
    # çözümsüz bırakır, yani reddedilen şey gerçekten dosyanın kendisidir.
    # Satır satır saymak aynı cümleyi 200 kez yazardı.
    if "product_code" not in esleme and "product_name" not in esleme:
        raise HTTPException(
            400,
            "Dosyada 'Ürün Kodu' ya da 'Ürün Adı' sütunu bulunamadı; katalog "
            "satırı bir ürüne bağlanmadan yazılamaz.",
        )
    if "preharvest_interval_days" not in esleme:
        raise HTTPException(
            400,
            "Dosyada 'Hasat Bekleme (Gün)' sütunu bulunamadı; kataloğun var "
            "olma sebebi bu değerdir ve uygulama bir gün sayısı üretmez.",
        )

    yazilan = 0
    reddedilen: list[dict[str, Any]] = []
    #: Dosyanın KENDİ İÇİNDEKİ çakışmalar. Veritabanına bakmak yetmez: aynı
    #: yüklemede iki kez geçen (ürün, bitki) çifti, ilki yazıldıktan SONRA
    #: veritabanında görünür — ama o zaman gerekçe "katalogda zaten var"
    #: olurdu ve kullanıcı satırı KENDİSİNİN iki kez yazdığını anlamazdı.
    dosyadaki: dict[tuple[int, str], int] = {}
    now = _simdi()

    for satir_no, satir in enumerate(satirlar, start=2):
        def _hucre(anahtar: str, _satir: list[Any] = satir) -> str:
            return str(_cell(_satir, esleme, anahtar, "") or "").strip()

        def _reddet(mesaj: str, etiket: str = "", _no: int = satir_no) -> None:
            reddedilen.append({"row": _no, "message": mesaj, "product": etiket})

        kod = _hucre("product_code")
        ad = _hucre("product_name")
        etiket = kod or ad
        product_id, hata = _ice_aktarma_urun_coz(db, cid, kod, ad)
        if product_id is None:
            _reddet(hata, etiket)
            continue

        bitki = " ".join(_hucre("crop").split())
        if len(bitki) > _BITKI_EN_UZUN:
            _reddet(f"Bitki adı {_BITKI_EN_UZUN} karakteri aşıyor.", etiket)
            continue
        ruhsat = " ".join(_hucre("registration_no").split()) or None
        if ruhsat is not None and len(ruhsat) > _RUHSAT_EN_UZUN:
            _reddet(f"Ruhsat no {_RUHSAT_EN_UZUN} karakteri aşıyor.", etiket)
            continue

        ham_phi = _cell(satir, esleme, "preharvest_interval_days", "")
        phi = _ice_aktarma_tamsayi(ham_phi)
        if phi is None:
            ham_metin = str(ham_phi if ham_phi is not None else "").strip()
            _reddet(
                "Hasat bekleme günü boş."
                if not ham_metin
                else f"Hasat bekleme günü tam sayı değil: '{ham_metin}'.",
                etiket,
            )
            continue
        if phi < 0 or phi > _PHI_EN_COK:
            _reddet(
                f"Hasat bekleme günü 0 ile {_PHI_EN_COK} arasında olmalı: {phi}.", etiket
            )
            continue

        ham_giris = _cell(satir, esleme, "reentry_interval_days", "")
        giris: int | None = None
        if str(ham_giris if ham_giris is not None else "").strip():
            giris = _ice_aktarma_tamsayi(ham_giris)
            if giris is None:
                _reddet(
                    f"Giriş yasağı günü tam sayı değil: '{str(ham_giris).strip()}'.",
                    etiket,
                )
                continue
            if giris < 0 or giris > _PHI_EN_COK:
                _reddet(
                    f"Giriş yasağı günü 0 ile {_PHI_EN_COK} arasında olmalı: {giris}.",
                    etiket,
                )
                continue

        # Dosya içi çakışma karşılaştırması `casefold` ile, SQL'in
        # `LOWER()`ında DEĞİL — 0063'ün `_bitki_esit` gerekçesinin aynısı:
        # Türkçe İ/ı eşlemesi diyalekte bağlıdır ve hesap tek yerde kalmalı.
        anahtar = (product_id, bitki.casefold())
        onceki = dosyadaki.get(anahtar)
        if onceki is not None:
            _reddet(
                f"Aynı ürün ve bitki dosyanın {onceki}. satırında da var; "
                "içe aktarma aynı kaydı iki kez yazmaz.",
                etiket,
            )
            continue

        # ÇAKIŞMA REDDEDİLİR, GÜNCELLENMEZ (başlıktaki 2. kural). Var olan
        # kaydın kimliği yanıtta VERİLİYOR; kullanıcı ekrandan bakıp
        # hangisinin doğru olduğuna KENDİSİ karar versin diye.
        mevcut = db.execute(
            text(
                "SELECT id FROM plant_protection_products "
                "WHERE company_id=:cid AND product_id=:pid AND crop=:crop"
            ),
            {"cid": cid, "pid": product_id, "crop": bitki},
        ).scalar()
        if mevcut is not None:
            _reddet(
                f"Katalogda bu ürün ve bitki için kayıt zaten var (#{int(mevcut)}); "
                "içe aktarma mevcut değeri değiştirmez.",
                etiket,
            )
            continue

        isaret = f"{dosya_adi}:{satir_no}"[:_KOKEN_ISARETI_EN_UZUN]
        try:
            # SAVEPOINT: bir satırın kısıt ihlali TÜM işlemi zehirlemesin.
            # PostgreSQL'de başarısız bir deyimden sonra işlem ABORTED duruma
            # düşer ve sonraki HER sorgu hata verir — savepoint olmadan tek
            # bozuk satır, başlıktaki 1. kuralı SESSİZCE bozup dosyanın
            # kalanını da düşürürdü. SQLite'ta bu yol görünmez; ayrım
            # PostgreSQL ikizinde ölçülüyor.
            with db.begin_nested():
                db.execute(
                    text(
                        """INSERT INTO plant_protection_products(company_id,product_id,
                        crop,registration_no,preharvest_interval_days,
                        reentry_interval_days,notes,status,origin,origin_reference,
                        created_at,updated_at)
                        VALUES(:cid,:product_id,:crop,:registration_no,
                        :preharvest_interval_days,:reentry_interval_days,:notes,
                        'ACTIVE','IMPORT',:origin_reference,:now,:now)"""
                    ),
                    {
                        "cid": cid, "product_id": product_id, "crop": bitki,
                        "registration_no": ruhsat,
                        "preharvest_interval_days": phi,
                        "reentry_interval_days": giris,
                        "notes": _hucre("notes") or None,
                        "origin_reference": isaret,
                        "now": now,
                    },
                )
        except IntegrityError:
            # Yukarıdaki SELECT ile bu INSERT arasında başkası aynı satırı
            # yazdıysa buraya düşeriz. Kısıt (`uq_ppp_company_product_crop`)
            # son sözü söylüyor ve kullanıcıya verilen gerekçe aynı kalıyor.
            _reddet(
                "Katalogda bu ürün ve bitki için kayıt zaten var; "
                "içe aktarma mevcut değeri değiştirmez.",
                etiket,
            )
            continue
        dosyadaki[anahtar] = satir_no
        yazilan += 1

    db.commit()
    return {
        "filename": dosya_adi,
        "total_rows": len(satirlar),
        "imported": yazilan,
        # SAYI DEĞİL LİSTE: "3 satır reddedildi" kullanıcıya hangisini
        # düzelteceğini söylemez.
        "rejected": reddedilen,
    }
