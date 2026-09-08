"""Gelen medyadan alış faturası OKUMA — bu turda YALNIZ ÖZET, kayıt YOK.

Kaynak `nazgul_website/backend/app/whatsapp/fatura.py` (318 satır). Kaynak
akış şudur: oku → özetle → ONAY → taslak belge. **Bu port ilk iki adımı
taşır, son ikisini TAŞIMAZ** ve gerekçe kapsam değil GÖÇ ZORUNLULUĞUDUR:

    ONAY adımı `whatsapp_pending_actions` tablosuna `action_type`
    `'FATURA_TASLAK'` yazar. O sütunun kapalı kümesi bir CHECK ile
    çivili — `schema.py`de `ck_wpa_action_type`, bugün `IN ('TAHSILAT')`.
    Yeni türü kabul ettirmek CHECK'i değiştirmek, yani BİR GÖÇ demektir.
    Bu dilim göçsüzdür; dolayısıyla tür EKLENMEDİ ve niyet YAZILMIYOR.

Yani kullanıcı fotoğrafı gönderir, faturanın ÖZETİNİ geri alır ve mesajın
sonunda kaydın AÇILMADIĞINI okur. Yarım bir söz vermektense hiç söz
vermemek: "ONAY yazın" deyip ONAY'ı işleyemeyen bir akış, kullanıcıyı
kaydın açıldığına inandırırdı.

--- BU MODÜL BİR `Session` ALMAZ (yazma YAPISAL olarak imkânsız) ---------

Kaynak imzası `oku_ve_taslakla(db, kimlik, telefon, satir, saglayici)`;
buradaki :func:`medya_ozeti` **veritabanına HİÇ dokunmaz**. Bu bir üslup
tercihi değil ölçülmüş bir kapıdır: "yazmıyoruz" bir yorum satırıyla
değil, YAZACAK NESNENİN OLMAMASIYLA garanti ediliyor. Kaynağın günlük
kota sayacı (`_gunluk_sayim`, `whatsapp_inbound` üzerinde bir `SELECT`) de
bu yüzden taşınmadı; gerekçesi kaynakta "her fotoğraf bir model
çağrısıdır" diye YAZILI ve bu turda model çağrısı YOK.

Yan etkisi ölçüldü: `app/whatsapp/*` çekirdek sorgu envanterinin
KAPSAMINDA (`tests/test_core_query_inventory.py`) ve bu dosya oraya SIFIR
satır ekler.

--- MODEL ÇAĞRISI AYRILDI: arayüz + NoOp (durum: ÖLÇÜLDÜ) ---------------

Görev "kaynağın belirlenimci ayrıştırıcısı" diyordu; ÖLÇÜM şudur:
**kaynakta belirlenimci bir ayrıştırıcı YOKTUR.** Kaynağın
`oku_ve_taslakla`sı belgeyi `app.assistant.llm.belge_oku` ile okur, yani
okuma adımı bir MODEL çağrısıdır. Belirlenimci olan, model çıktısını
ALDIKTAN SONRA çalışan karar katmanıdır (`_kalem_kararlari`,
`_ozet_metni`) ve İŞTE O taşındı — kaynağın kendi test dosyasındaki altın
vakalarla birlikte.

Bu yüzden okuma bir ARAYÜZE çekildi (:class:`BelgeCozucu`) ve bu turun
tek gerçekleştirimi :class:`NoOpCozucu`dur: ağa da modele de çıkmaz,
`None` döner. `cozucu_al()` bugün DAİMA NoOp verir. Model yolu geldiğinde
eklenecek yer BELLİ ve bu modülün geri kalanı DEĞİŞMEYECEK.

--- İNDİRME NEDEN YİNE DE KOŞUYOR ---------------------------------------

Çözücü NoOp'ken indirme boşa iş gibi görünür; değildir. İndirme yolu
sağlayıcının GERÇEK ağ yoludur (iki adımlı Meta medya ucu, jeton, boyut
sınırı) ve WA3 onu "çağıranı yok" diye taşımamıştı. Çağıran BU modüldür;
yol burada ölçülüyor (`tests/test_wa5_fatura.py`, sahte `urlopen`).
Sırayı tersine çevirip "çözücü kapalıysa indirme" demek, indirme yolunu
yeniden ölçülmez yapardı.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger("nazgul.whatsapp.fatura")

# Kaynakta `ISLEM_TURU = "purchase_draft"` vardı ve `bekleyen` tablosuna
# yazılıyordu. BURADA YOK ve olmaması ÖLÇÜLÜYOR: bir işlem türü sabiti
# tanımlamak, onu `schema.ISLEM_TURLERI`ne eklemek isteyen bir sonraki eli
# davet ederdi ve o ekleme GÖÇSÜZ yapılamaz (modül başlığı).
# Kapı: `test_FATURA_TASLAK_TURU_bu_turda_TANIMLI_DEGIL`.

#: Mesajın sonuna eklenen sınır cümlesi. AYRI sabit çünkü kapı onu ADIYLA
#: arıyor: özet metni kaydın açıldığını ima ederse kullanıcı faturayı
#: girilmiş sayar ve bir daha girmez.
SINIR_CUMLESI = (
    "NOT: Bu bir ÖZETTİR — kayıt OLUŞTURULMADI. "
    "Faturayı web panelinden girin."
)

#: Çözücü kapalıyken verilen cevap. "Hata" demiyor çünkü hata YOK:
#: yapılandırılmamış bir yetenek, bozulmuş bir yetenek değildir.
COZUCU_KAPALI_MESAJI = (
    "Belge okuma bu kurulumda açık değil; faturayı web panelinden girin."
)

INDIRME_HATASI_MESAJI = "Belge indirilemedi. Tekrar gönderir misiniz?"

OKUNAMADI_MESAJI = "Bu belgeden fatura bilgisi çıkarılamadı; web panelinden girin."


# ---------------------------------------------------------------------------
# Okuma arayüzü — bu turun tek gerçekleştirimi ağa da modele de çıkmaz
# ---------------------------------------------------------------------------


class BelgeCozucu(ABC):
    """Bayt + MIME → kaynağın `FATURA_SEMASI` biçiminde sözlük, ya da ``None``.

    ``None`` "okuyamadım" demektir ve bir istisna DEĞİLDİR: okunamayan bir
    fotoğraf beklenen bir sonuçtur (bulanık, ilgisiz, elle yazılmış) ve
    akış onu kullanıcıya söyleyerek kapatır.
    """

    @abstractmethod
    def oku(self, icerik: bytes, mime: str) -> dict[str, Any] | None: ...


class NoOpCozucu(BelgeCozucu):
    """Bu turun çözücüsü: DAİMA ``None``. Model yolu YOK.

    Sessiz bir `pass` değil ölçülebilir bir sonuç: çağıran
    :data:`COZUCU_KAPALI_MESAJI` üretir ve testler "indirildi ama
    okunmadı" durumunu gerçekten görebilir.
    """

    def oku(self, icerik: bytes, mime: str) -> dict[str, Any] | None:
        return None


def cozucu_al() -> BelgeCozucu:
    """Bu turda DAİMA NoOp. Model gerçekleştirimi geldiğinde dallanacak yer."""
    return NoOpCozucu()


# ---------------------------------------------------------------------------
# Belirlenimci karar katmanı — kaynaktan BİREBİR taşındı
# ---------------------------------------------------------------------------


def _sayi(deger: Any) -> Decimal | None:
    if deger is None:
        return None
    try:
        return Decimal(str(deger))
    except (InvalidOperation, ValueError):
        return None


def _kalem_kararlari(
    satirlar: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Okunan satırlardan kalemleri üretir; (kalemler, atlanan).

    KATALOGLA EŞLEŞMEYEN SATIR ATLANIR, otomatik yeni ürün AÇILMAZ.
    Faturadaki adla ürün yaratmak katalogda mükerrer kayıt üretir —
    tedarikçi bizim adımızı kullanmaz ve aynı parça her faturada başka
    yazılır. Eksik kalemi kullanıcı panelden ekler; sessizce yanlış ürün
    açmaktansa eksik özet vermek daha az zarar verir.

    Para alanları `Decimal` KALIR. Bu turda yazılacak bir yük olmasa da
    tip korunuyor: `float`a düşen bir ara katman, göç geldiğinde ondalık
    sözleşmesini (`test_v2_9_decimal_contract`) sessizce ihlal ederdi.
    """
    kalemler: list[dict[str, Any]] = []
    atlanan = 0
    for s in satirlar:
        eslesen = s.get("eslesen_urun")
        miktar = _sayi(s.get("miktar"))
        birim_fiyat = _sayi(s.get("birim_fiyat"))
        if (
            s.get("eslesme_durumu") != "eslesti"
            or not eslesen
            or miktar is None
            or miktar <= 0
            or birim_fiyat is None
            or birim_fiyat < 0
        ):
            atlanan += 1
            continue
        kalemler.append(
            {
                "karar": "mevcut",
                "urun_id": int(eslesen["id"]),
                "miktar": miktar,
                "birim_fiyat": birim_fiyat,
                "iskonto_yuzdesi": _sayi(s.get("iskonto_yuzdesi")) or Decimal("0"),
                "kdv_orani": int(s.get("kdv_orani") or 0),
                "birim": (s.get("birim") or "Adet")[:30],
            }
        )
    return kalemler, atlanan


def _ozet_metni(cozum: dict[str, Any], kalem_sayisi: int, atlanan: int) -> str:
    """Kullanıcıya giden özet. Kaynaktan TEK farkı SON İKİ SATIRDIR.

    Kaynak burada "TASLAK alış oluşturmak için ONAY, vazgeçmek için İPTAL
    yazın" diyordu. Bu turda ONAY'ı işleyecek bir bekleyen-işlem satırı
    AÇILAMIYOR (modül başlığı: CHECK göçü), dolayısıyla o davet
    KALDIRILDI ve yerine :data:`SINIR_CUMLESI` yazıldı. Davet dursaydı,
    kullanıcı ONAY yazar ve mesajı işlenmeden düşerdi.
    """
    satirlar = [f"Fatura okundu: {cozum.get('tedarikci') or 'tedarikçi okunamadı'}"]
    vergi = cozum.get("tedarikci_vergi_no")
    if vergi:
        satirlar.append(f"VKN: {vergi}")
    tarih = cozum.get("tarih")
    if tarih:
        satirlar.append(f"Tarih: {tarih}")
    genel = cozum.get("genel_toplam")
    if genel:
        satirlar.append(f"Genel toplam: {genel} TL")
    satirlar.append(f"Okunan kalem: {kalem_sayisi}")
    if atlanan:
        # Sessiz eksiltme, tam özet sanılır. Sayı AÇIKÇA yazılır.
        satirlar.append(f"Eşleşmeyen {atlanan} kalem ATLANDI — panelden ekleyin.")
    for uyari in (cozum.get("uyarilar") or [])[:3]:
        satirlar.append(f"Uyarı: {uyari}")
    satirlar.append("")
    satirlar.append(SINIR_CUMLESI)
    return "\n".join(satirlar)


# ---------------------------------------------------------------------------
# Akış: indir → çöz → özetle. Hiçbir dal veritabanına dokunmaz.
# ---------------------------------------------------------------------------


def medya_ozeti(saglayici: Any, satir: Any, cozucu: BelgeCozucu | None = None) -> str:
    """Medya satırının kullanıcıya gidecek CEVABINI üretir.

    HER DAL BİR METİN DÖNER — istisna sızmaz. Çağıran `service._mesaj_isle`
    ve oradaki bir istisna satırı DEAD yapardı; indirilemeyen bir fotoğraf
    ise kullanıcının tekrar gönderebileceği SIRADAN bir durumdur.

    `db` PARAMETRESİ YOK ve bu imzanın kendisi bir kapıdır (modül başlığı).
    """
    try:
        icerik = saglayici.medya_indir(str(satir["media_id"]))
    except Exception as hata:  # ağ/sağlayıcı sınıfları çağırana SIZMASIN
        log.warning("whatsapp fatura: medya indirilemedi tur=%s", type(hata).__name__)
        return INDIRME_HATASI_MESAJI

    okunan = (cozucu or cozucu_al()).oku(icerik, str(satir["media_mime"] or ""))
    if okunan is None:
        return COZUCU_KAPALI_MESAJI
    if not isinstance(okunan, dict):
        return OKUNAMADI_MESAJI

    kalemler, atlanan = _kalem_kararlari(list(okunan.get("satirlar") or []))
    if not kalemler and not atlanan:
        return OKUNAMADI_MESAJI
    return _ozet_metni(okunan, len(kalemler), atlanan)


__all__ = [
    "BelgeCozucu",
    "COZUCU_KAPALI_MESAJI",
    "INDIRME_HATASI_MESAJI",
    "NoOpCozucu",
    "OKUNAMADI_MESAJI",
    "SINIR_CUMLESI",
    "cozucu_al",
    "medya_ozeti",
]
