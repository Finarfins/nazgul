"""Tarayıcı saatinden tarih/zaman türeten yerleri listeler — ENVANTER ARACI.

NE DEĞİLDİR: bu bir kapı değildir ve "her giriş noktasını bulur" demez.
Satır bazlı düzenli ifadelerle çalışır; aşağıdaki sınırları BİLEREK taşır ve
raporlar. Kapı isteniyorsa AST/tip çözümlemesi gerekir, bu betik onu yapmaz.

YÖNTEM
  1. Kapsam: `frontend/src` altındaki TÜM `.ts`/`.tsx` dosyaları, dizin
     gezilerek bulunur (elle liste yok). Test dosyaları ayrı işaretlenir.
  2. Aranan giriş noktaları:
       new Date()              argümansız      → şu anki an
       Date()                  `new`SİZ çağrı  → şu anki an (string)
       Date.now()                              → şu anki an (ms)
       performance.now()                       → MONOTONİK sayaç, duvar saati DEĞİL
       dayjs() / moment()      argümansız      → şu anki an
       DateTime.now() / DateTime.local()       → Luxon
       Temporal.Now.*                          → Temporal
       .format() / .formatToParts()  DEĞERSİZ  → biçimlendirici "şimdi"yi alır
       getTimezoneOffset()                     → tarayıcı diliminin kaydırması
       resolvedOptions().timeZone              → tarayıcı dilimi
  3. `new Date(x)` — ARGÜMANLI çağrı ayrı bir sınıfta raporlanır, sessizce
     ELENMEZ. Gerekçe: `x` çalışma zamanında `undefined` olabilir (isteğe bağlı
     alan, eksik API yanıtı) ve o durumda sonuç yine tarayıcının o anki
     saatidir. Argümanın kaynağını satır düzeyinde bir regex çözemez; bu yüzden
     "incelenecek" olarak listelenir, "güvenli" olarak değil.

SINIRLAR (kapatılmamış, açıkça bildirilmiş)
  * Şablon/dolaylı çağrılar (`const f = Date.now; f()`), yeniden adlandırılmış
    import'lar ve dinamik erişim (`obj['now']()`) görülmez.
  * Yorum satırları ve string içindeki metinler ayıklanmaz.
  * Üçüncü parti bir kütüphanenin içinde saat okuması varsa kapsam dışıdır.
"""
from __future__ import annotations

import pathlib
import re

KOK = pathlib.Path("frontend/src")

# Tarayıcı saatini DOĞRUDAN okuyan giriş noktaları.
DESENLER: dict[str, re.Pattern[str]] = {
    "new Date()": re.compile(r"new\s+Date\(\s*\)"),
    # `new` OLMADAN çağrı: `Date()` bir string döndürür ve yine ŞU ANı okur.
    # Aşağıdaki kontrol `new Date()` ile karışmasın diye ayrı bir işlevle
    # yapılır; `(?<!new\s)` gibi değişken uzunluklu bir lookbehind Python'da
    # geçerli değildir.
    "Date() (new'siz)": re.compile(r"(?<![.\w])Date\(\s*\)"),
    "Date.now()": re.compile(r"\bDate\.now\(\s*\)"),
    "performance.now()": re.compile(r"\bperformance\.now\(\s*\)"),
    "dayjs()/moment()": re.compile(r"\b(?:dayjs|moment)\(\s*\)"),
    "Luxon DateTime.now/local": re.compile(r"\bDateTime\.(?:now|local)\(\s*\)"),
    "Temporal.Now": re.compile(r"\bTemporal\.Now\b"),
    "format() değersiz": re.compile(r"\.(?:format|formatToParts|formatRange)\(\s*\)"),
    "getTimezoneOffset": re.compile(r"\bgetTimezoneOffset\b"),
    "Intl timeZone": re.compile(r"resolvedOptions\(\)\.timeZone"),
}

# Argümanlı `new Date(x)` — elenmez, "kaynağı incelenecek" olarak raporlanır.
ARGUMANLI = re.compile(r"new\s+Date\(\s*[^)\s]")


def tara(kok: pathlib.Path = KOK) -> tuple[int, list[tuple], list[tuple]]:
    dogrudan: list[tuple] = []
    incelenecek: list[tuple] = []
    dosya_sayisi = 0
    for yol in sorted(kok.rglob("*")):
        if yol.suffix not in {".ts", ".tsx"} or not yol.is_file():
            continue
        dosya_sayisi += 1
        test_mi = ".test." in yol.name
        for no, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
            for ad, desen in DESENLER.items():
                for eslesme in desen.finditer(satir):
                    if ad == "Date() (new'siz)" and satir[: eslesme.start()].rstrip().endswith("new"):
                        continue  # bu `new Date()`, ayrı desende zaten sayılıyor
                    dogrudan.append((yol.as_posix(), no, ad, test_mi, satir.strip()[:150]))
                    break
            if ARGUMANLI.search(satir):
                incelenecek.append((yol.as_posix(), no, "new Date(<arg>)", test_mi, satir.strip()[:150]))
    return dosya_sayisi, dogrudan, incelenecek


if __name__ == "__main__":
    dosya_sayisi, dogrudan, incelenecek = tara()
    urun = [v for v in dogrudan if not v[3]]
    urun_arg = [v for v in incelenecek if not v[3]]
    print(f"TARANAN_DOSYA={dosya_sayisi}")
    print(f"DOGRUDAN_SAAT_OKUMASI={len(dogrudan)}  (urun kodu {len(urun)})")
    print(f"ARGUMANLI_new_Date_INCELENECEK={len(incelenecek)}  (urun kodu {len(urun_arg)})")
    print()
    print("--- DOGRUDAN (urun kodu) ---")
    for yol, no, ad, _t, satir in urun:
        print(f"{yol}:{no}\t{ad}\t{satir}")
    print()
    print("--- ARGUMANLI new Date(x): kaynagi incelenecek, GUVENLI DEGIL ---")
    for yol, no, ad, _t, satir in urun_arg:
        print(f"{yol}:{no}\t{ad}\t{satir}")
