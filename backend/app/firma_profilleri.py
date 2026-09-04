"""Firma profilleri: dört iş kolu, KÜME olarak. Depo biçimi tek yerde.

Konu: Faz 5.2 — sahibin kayıt anındaki kararını saklamak (göç
`20260904_0068`). Bu modül SAFTIR: saat okumaz, veritabanı okumaz, istek
nesnesi görmez. Yalnız "hangi değerler geçerli" ve "küme nasıl saklanır"
sorularını cevaplar.

--- KÜME, ENUM DEĞİL ------------------------------------------------------

Dört profil BİRLİKTE seçilebilir ve karışmak KURALDIR. Sahibin kendi
işletmesi dördünden üçünü aynı anda taşıyor: bayilik (tüccar), servis, tarla
(çiftçi) ve sürü (veteriner). Tek değerli bir alan bu kiracıyı üç kere
yanlış sınıflandırırdı.

Bunlar MODÜL ANAHTARIDIR, KİLİT DEĞİL — ve bu PR'da hiçbir davranış onlara
BAĞLI DEĞİLDİR. Anahtarların bir şeyi açıp kapatması AYRI bir iştir.

--- SIRALAMA SAKLANIRKEN UYGULANIR ----------------------------------------

`pazarci,ciftci` ile `ciftci,pazarci` AYNI kümedir. İkisi iki farklı dizgi
olarak saklanırsa dizgi düzeyindeki her eşitlik karşılaştırması YANLIŞ cevap
verir ve yanlışlığı gösterecek hiçbir kırmızı olmaz. Bu yüzden depoya giden
her değer BURADAN geçer: kırp, boşları at, KÜMEYE al (yineleneni düşür),
`sorted()` ile sırala, virgülle birleştir.

Şekil bu depoda yeni değildir: `app/routers/seasonal_plan.py` içindeki
`_parse_months` aynı şeyi aylar için zaten yapıyor.

--- TANINMAYAN DEĞER BURADA DA REDDEDİLİR (İKİNCİ KATMAN) -----------------

Uçların Pydantic şemaları `Literal` ile zaten 422 veriyor. `profilleri_birlestir`
BUNA GÜVENMEZ ve tanınmayan değeri kendisi de reddeder, çünkü bu fonksiyonu
Pydantic'ten geçmemiş bir çağıranın (bir betik, bir göç, bir gelecek uç)
çağırması ENGELLENMİŞ DEĞİLDİR. Tek katmanlı bir doğrulama, o çağıran
ortaya çıktığı gün SESSİZCE delinir.
"""

from __future__ import annotations

from typing import Iterable, Literal


#: Kanonik küme. Sıra ALFABETİKTİR ve saklanan sıranın da aynısıdır, yani
#: bu demet hem "geçerli değerler" hem de "beklenen sıra" olarak okunabilir.
PROFILLER: tuple[str, ...] = ("ciftci", "pazarci", "tuccar", "veteriner")

#: Pydantic şemalarının kullandığı tip. `PROFILLER` ile ELLE eşleşir; ikisinin
#: ayrışmadığı testle çivilenmiştir (`Literal` çalışma zamanında türetilemez,
#: çünkü tip denetleyicisi statik bir değer ister).
FirmaProfili = Literal["ciftci", "pazarci", "tuccar", "veteriner"]

#: Sütun boşken kastedilen şey: "bu kiracıya sorulmadı". "Hiçbiri" DEĞİL.
SECILMEDI = ""


def profilleri_birlestir(degerler: Iterable[str]) -> str:
    """Kümeyi saklanacak dizgiye çevirir: kırp, tekilleştir, sırala, birleştir.

    Tanınmayan değerde `ValueError` atar — çağıran uç bunu 422'ye çevirir.
    """

    temiz: set[str] = set()
    for ham in degerler:
        deger = str(ham).strip()
        if not deger:
            continue
        if deger not in PROFILLER:
            raise ValueError(f"Geçersiz firma profili: {deger}")
        temiz.add(deger)
    return ",".join(sorted(temiz))


def profilleri_coz(metin: str | None) -> list[str]:
    """Saklanan dizgiyi listeye çevirir. Boş/None -> boş liste.

    Doğrulama YAPMAZ: burada okunan şey depoya ZATEN `profilleri_birlestir`
    üzerinden girmiştir. Okuma yolunda ikinci kez reddetmek, elde tutulan
    veriyi okunamaz kılardı ve kusuru düzeltmezdi.
    """

    if not metin:
        return []
    return [parca.strip() for parca in metin.split(",") if parca.strip()]
