#!/usr/bin/env python3
r"""Sürüm aktarımı prosedüründeki HER adımın durmaya bağlı olduğunu ölçer.

Neden bu kapı var
-----------------
2026-08-16 deploy'unda prosedür bu kurulum için yazılı değildi ve yazılırken
dört adım "doğrula ama devam et" biçiminde kaldı: sağlamayı ekrana basıp göz
kararı karşılaştırmak, ``gzip -t``'yi bağlamamak, ``.env`` yedeğini iki
dosyalık bir ``sha256sum`` çıktısıyla "doğrulamak", ve takas sonrası ``cmp``'yi
bağlamamak. Bunlar tek tek düzeltilebilirdi; ama bu, deseni değil örnekleri
onarmak olurdu ve gelecek ay eklenecek adım yine ``echo`` ile gelirdi.

Bu yüzden kapı ÖRNEKLERİ değil KURALI sabitler: ``docs/ops/DEPLOY.md``
"Sürüm aktarımı — dizin takası" bölümündeki HER ``bash`` bloğunda, her
mantıksal satır ``exit 1`` içermek ZORUNDA.

VARSAYILAN KIRMIZI — muafiyet şekil tanımaya DAYANMAZ
-----------------------------------------------------
Kapının ilk hâli önek tanıyordu: ``if``, ``for``, atama ve ``echo`` ile
başlayan satırlar muaftı. Doğrulama komutu muaf önekli bir satıra konunca
kaçıyordu. Sınıflandırma tersine çevrildi: bir satır varsayılan olarak
KIRMIZIDIR, muaf olabilmesi için iki koşulu birden sağlamalıdır — komut
ikamesi taşımayacak, ve baştan sona çapalı bir kalıba tam uyacak.

İkinci turda ölçülen şuydu: çapalama ve metakarakter ELEMESİ tek başına
yetmiyordu, çünkü eleme bir KARA LİSTEYDİ. Sayılmayan her şekil sessizce kabul
ediliyordu ve dördü ölçüldü:

    echo "payload" > /opt/fake.txt      -> "düz çıktı" muafiyeti alıyordu
    echo <(cat /etc/passwd)             -> "düz çıktı" muafiyeti alıyordu
    VAR=<(/opt/fake_script.sh)          -> "düz atama" muafiyeti alıyordu
    # adım 8 \  + sonraki satır         -> gerçek komut yoruma yutuluyordu

İlk üçü yönlendirme ve süreç ikamesidir: ``$()`` de zincirleme de içermezler,
ama dosya sistemi yan etkisi üretir ve komut çalıştırırlar. Dördüncüsü
birleştiricinin kendi kusuruydu ve raporlanan üç dizgeyi denemekle değil,
"muaf kalıp gerçekten komut taşıyamıyor mu" diye sorarak bulundu.

Düzeltme üç dizgeyi yasaklamak DEĞİLDİR — o, aynı kusurun bir kat aşağısı
olurdu. Muafiyet gövdesi artık BEYAZ LİSTEDİR (``GUVENLI_GOVDE``): izin
verilen karakterler sayılır, yasaklananlar değil. Sayılmamış bir metakarakter
artık sessizce kabul edilmez; reddedilir. İDDİA EDİLEN INVARIANT budur ve
sınırı da aşağıda yazılıdır.

ÖLÇÜLEN SINIRLAR — burada yazılı, okuyucunun keşfetmesine bırakılmamıştır
------------------------------------------------------------------------
1. KAPSAM BÖLÜMDÜR, BELGE DEĞİL. "Sürüm aktarımı — dizin takası" bölümündeki
   HER ``bash`` bloğu taranır — bölüme ikinci bir prosedür bloğu eklemek
   denetimden kaçmanın yolu değildir. Belgenin geri kalanı bilinçli olarak
   kapsam dışıdır: DEPLOY.md başka bölümlerinde açıklayıcı bloklar taşır
   (``docker rm -f …``, veri kurtarma örnekleri, tek satırlık kullanım
   gösterimleri). Onlar prosedür değil ÖRNEKTİR; ``exit 1`` dayatmak yanlış
   olurdu ve gürültü üretirdi. Gürültü üreten kapıyı ilk takılan kişi devre
   dışı bırakır — sınır bu yüzden bölümdür.
2. "Durmaya bağlı" ölçütü, mantıksal satırda ``exit 1`` dizgesinin GEÇMESİDİR.
   Satırın gerçekten o dala girdiği çalıştırılarak kanıtlanmaz; ``|| true;
   exit 1`` gibi bilinçli bir kandırma bu kapıyı geçer. Tehdit modeli
   dikkatsiz/kopyala-yapıştır yazar, kararlı bir atlatma değil.
3. Muafiyet dilbilgisi aşağıda kaynak olarak durur; genişletilmesi görünür bir
   düzenlemedir. Ama tek başına genişletme yasağı yoktur — koruma, dilbilgisinin
   dar ve komut-taşımaz oluşundan gelir, listenin dokunulmazlığından değil.
4. Invariant SÖZDİZİMSELDİR: muaf bir satır kabuk sözdizimi olarak komut,
   yönlendirme veya alt kabuk BAŞLATAMAZ. Anlamsal bir kanıt değildir — blok
   çalıştırılmaz (bkz. 2). Bu iki sınır birlikte okunmalıdır.
"""

from __future__ import annotations

import re
from pathlib import Path

BOLUM_BASLIGI = "## Sürüm aktarımı"
DURDURMA = "exit 1"
BLOK_ACILIS = "```bash"
BLOK_KAPANIS = "```"

# Komut ikamesi taşıyan hiçbir satır muaf olamaz — şekli ne olursa olsun.
KOMUT_IKAMESI = ("$(", "`")

# Muaf bir satırın gövdesinde bulunabilecek karakterler. Bu bir BEYAZ LİSTEDİR,
# kara liste değil: yasaklanacak metakarakterleri saymak yerine, izin verilen
# karakterler sayılır. Fark davranışsaldır — kara liste, sayılmayan her yeni
# şekli sessizce kabul eder; bu kümenin dışındaki her karakter reddedilir.
#
# Kümede BULUNMAYAN ve bu yüzden hiçbir muaf satırda geçemeyecek olanlar:
#   >  <   yönlendirme ve süreç ikamesi  ( echo x > /tmp/y ,  echo <(cmd) )
#   ( )    alt kabuk ve süreç ikamesinin ikinci yarısı
#   { }    komut gruplama ve ${...} genişletmesi
#   | & ;  boru hattı, artalan, zincirleme
#   ` $(   komut ikamesi (ayrıca yukarıda ayrıca elenir)
#   * ? [  glob genişletmesi
#   ! \    tarihçe genişletmesi ve kaçış
#
# Geriye kalan küme sözcük, sayı, yol, tırnak ve ``$VAR`` genişletmesidir.
# ``$VAR`` bilinçli olarak serbesttir: yönlendirme kabuk tarafından
# genişletmeden ÖNCE ayrıştırılır, dolayısıyla bir değişkenin DEĞERİ komut ya
# da yönlendirme üretemez.
GUVENLI_GOVDE = r"""A-Za-z0-9 _./:=,@%+~'\"$-"""

# Muafiyet dilbilgisi: BAŞTAN SONA çapalı, gövdesi yalnız GUVENLI_GOVDE'den
# oluşan satır biçimleri. `if`/`while`/`until`/`case` bilerek YOKTUR: koşul
# satırı komut taşır.
#
# Yorum, gövde kısıtından muaftır — içeriği kabuk tarafından hiç
# ayrıştırılmaz. Bunun bedeli ``mantiksal_satirlar()`` içinde ödenir: yorum
# satırı ters bölü ile BİTSE BİLE devam ettirilmez. Kabukta da öyledir (ters
# bölü yorumun içinde özel değildir), ama birleştirici bunu bilmezse ``# x \``
# ardından gelen gerçek komutu yoruma yutar ve komut muaf hâle gelirdi.
MUAFIYET_DILBILGISI: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("yorum", re.compile(r"^#.*$")),
    ("döngü sonu", re.compile(r"^done$")),
    ("koşul sonu", re.compile(r"^fi$")),
    ("koşul gövdesi", re.compile(r"^(then|else)$")),
    ("döngü başlığı",
     re.compile(r"^for +[A-Za-z_][A-Za-z0-9_]* +in +[" + GUVENLI_GOVDE + r"]+; *do$")),
    ("düz atama",
     re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[" + GUVENLI_GOVDE + r"]*$")),
    ("düz çıktı",
     re.compile(r"^echo +[" + GUVENLI_GOVDE + r"]*$")),
)


def bolum_satirlari(metin: str) -> list[str]:
    """Sürüm aktarımı bölümünün satırları: başlıktan sonraki ``## ``'a kadar.

    Sınır bir sonraki ``## `` başlığıdır; bölüm içindeki ``### `` alt
    başlıkları bölümü BİTİRMEZ — prosedür onların altına yazılıyor.
    """
    satirlar = metin.split("\n")
    bas = next(
        (i for i, s in enumerate(satirlar) if s.startswith(BOLUM_BASLIGI)),
        None,
    )
    if bas is None:
        raise ValueError(f"'{BOLUM_BASLIGI}' bölümü bulunamadı")
    son = next(
        (j for j in range(bas + 1, len(satirlar)) if satirlar[j].startswith("## ")),
        len(satirlar),
    )
    return satirlar[bas:son]


def bloklari_al(metin: str) -> list[tuple[str, list[str]]]:
    """Bölümdeki HER bash bloğunu (etiket, satırlar) olarak döndürür.

    Etiket, bloğun üstündeki en yakın ``### `` alt başlığıdır; hata metni
    ihlalin hangi blokta olduğunu böyle söyleyebiliyor.
    """
    bloklar: list[tuple[str, list[str]]] = []
    etiket = BOLUM_BASLIGI
    icerde = False
    tampon: list[str] = []
    for satir in bolum_satirlari(metin):
        if not icerde and satir.startswith("### "):
            etiket = satir[4:].strip()
            continue
        if not icerde and satir.strip() == BLOK_ACILIS:
            icerde = True
            tampon = []
            continue
        if icerde and satir.strip() == BLOK_KAPANIS:
            bloklar.append((etiket, tampon))
            icerde = False
            continue
        if icerde:
            tampon.append(satir)
    if icerde:
        raise ValueError("kapanmamış bash bloğu var")
    return bloklar


def mantiksal_satirlar(satirlar: list[str]) -> list[str]:
    """Ters bölü ile devam eden satırları tek mantıksal satırda birleştirir."""
    birlesik: list[str] = []
    tampon = ""
    for ham in satirlar:
        parca = ham.rstrip()
        tampon = (tampon + " " + parca.strip()) if tampon else parca
        # Yorum satırı ASLA devam etmez: kabukta ters bölü yorumun içinde özel
        # değildir, dolayısıyla sonraki satır gerçek bir komuttur. Birleştirsek
        # o komut yorum kılığına girip muaf olurdu — ölçüldü, kaçıyordu.
        if tampon.lstrip().startswith("#"):
            birlesik.append(tampon)
            tampon = ""
            continue
        if tampon.endswith("\\"):
            tampon = tampon[:-1].rstrip()
            continue
        birlesik.append(tampon)
        tampon = ""
    if tampon:
        birlesik.append(tampon)
    return birlesik


def muaf_mi(satir: str) -> str | None:
    """Muafsa gerekçesini, değilse None döndürür. Varsayılan: muaf DEĞİL."""
    if any(im in satir for im in KOMUT_IKAMESI):
        return None
    for ad, kalip in MUAFIYET_DILBILGISI:
        if kalip.match(satir):
            return ad
    return None


def main() -> int:
    kok = Path(__file__).resolve().parent.parent
    runbook = kok / "docs" / "ops" / "DEPLOY.md"
    try:
        metin = runbook.read_text(encoding="utf-8")
    except OSError as hata:
        print(f"J6 runbook okunamadı: {hata}")
        return 1

    try:
        bloklar = bloklari_al(metin)
    except ValueError as hata:
        print(f"J6 sürüm aktarımı bölümü okunamadı: {hata}")
        return 1

    if not bloklar:
        print(f"J6 '{BOLUM_BASLIGI}' bölümünde bash bloğu yok — bölüm silinmiş olabilir")
        return 1

    bagsiz: list[str] = []
    komut_sayisi = 0
    for etiket, satirlar in bloklar:
        for satir in mantiksal_satirlar(satirlar):
            sade = satir.strip()
            if not sade or muaf_mi(sade):
                continue
            komut_sayisi += 1
            if DURDURMA not in satir:
                bagsiz.append(f"[{etiket}] {sade[:110]}")

    if not komut_sayisi:
        print("J6 bloklarda komut bulunamadı — kapı boşa düşmüş olabilir")
        return 1

    if bagsiz:
        print(f"J6 durmaya BAĞLANMAMIŞ {len(bagsiz)} adım var:")
        for s in bagsiz:
            print(f"     {s}")
        return 1

    print(
        f"J6 sürüm aktarımı bölümündeki {len(bloklar)} blokta "
        f"{komut_sayisi} komutun tümü durmaya bağlı"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
