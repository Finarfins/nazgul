#!/usr/bin/env python3
r"""Sürüm aktarımı bölümünde CANLI DİZİNİN SİLİNMEDİĞİNİ ölçer.

Neden bu kapı var
-----------------
Geri alma prosedürü şöyle yazılmıştı:

    sudo rm -rf /opt/harman-zamani && sudo mv /opt/harman-zamani-onceki-<TS> /opt/harman-zamani

İlk hâlinde hata denetimi yoktu; ``|| { …; exit 1; }`` eklendi. Bu YETMEZ ve
yanlış yerde bir onarımdır: ``exit 1`` hatayı ancak canlı ağaç ZATEN silindikten
sonra görünür kılar. ``rm -rf`` başarılı olup ardından gelen ``mv`` başarısız
olursa geriye hiçbir şey kalmaz — prosedür, tam da bir şeyin ters gittiği anda
kurtarmaya çalıştığı nesneyi yok eder. Hata denetimi yıkıcı SIRALAMAYI onarmaz.

Örnek onarıldı: geri alma artık silmeyle değil YENİDEN ADLANDIRMAYLA yapılıyor.
Ama örneği onarıp sınıfı sabitlememek, üç turdur kapattığımız kusur ailesinin
kendisidir. Bu kapı SINIFI sabitler:

    Sürüm aktarımı bölümündeki hiçbir komut CANLI DİZİNİ silemez.

Canlı dizinin silinmesi bu prosedürde hiçbir zaman doğru değildir — kurtarma
her zaman yeniden adlandırmayladır. Sahnelenen ağaç (``-yeni``) ve tutma adları
(``-onceki-``, ``-bozuk-``) BAŞKA dizinlerdir; onları silmek yasak değildir ve
3. adım bunu meşru biçimde yapar.

ÖLÇÜLEN SINIRLAR
----------------
1. Yalnız ``rm`` ölçülür. ``find -delete``, ``shred``, ``truncate`` veya bir
   betiğin içinden yapılan silme görülmez. Tehdit modeli J6 ile aynıdır:
   dikkatsiz/kopyala-yapıştır yazar, kararlı bir atlatma değil.
2. ``rm`` yalnız KOMUT KONUMUNDA sayılır: satır başı, ya da ``sudo``/``&&``/
   ``||``/``;``/``{``/``then``/``do``/``else`` ardından. Komut jetonu
   TIRNAKSIZLAŞTIRILARAK karşılaştırılır, çünkü ``"rm" -rf …`` kabukta çalışan
   bir silmedir; ham karşılaştırma onu kaçırıyordu (runtime ölçtü, kapatıldı).
   Kapı METİN İÇİ ``rm``'yi de ateşler: uzun bir tırnaklı mesajın ortasında
   ``… sudo rm -rf /opt/…`` geçerse jeton yine ``rm``'dir ve komut konumundadır.
   Bu YANLIŞ POZİTİF bilinçlidir — bir hata mesajından kopyalanan komut da
   çalıştırılır, dolayısıyla yıkıcı biçim bu bölümde metin olarak da
   bulunmamalıdır. (Ölçüldü: kapı ilk koşusunda 6. adımın mesajındaki eski
   yıkıcı öneriyi böyle yakaladı.) Buna karşılık ``echo "rm -rf …"`` biçimi
   ateşlemez: ``rm`` orada komut konumunda değildir, öncesindeki jeton
   ``echo``'dur.
3. Hedef karşılaştırması TAM YOL eşitliğidir; ``-yeni``, ``-onceki-…``,
   ``-bozuk-…`` sonekli yollar farklı dizinlerdir ve serbesttir. Yol bir
   DEĞİŞKENDE saklanırsa (``TARGET=/opt/harman-zamani && rm -rf "$TARGET"``)
   kapı göremez: jeton ``$TARGET``'tır, karşılaştırma metinseldir ve kabuk
   genişletmesi çalıştırılmaz. Bu, 1. sınırla aynı tehdit modeli altında kabul
   edilmiş bir sınırdır — ölçüldü, açıkça yazılıdır.
4. Bölüm sınırı ve blok ayrıştırması J6 kapısından alınır; ikinci bir
   ayrıştırıcı yazmak ikisinin zamanla ayrışmasına yol açardı.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

CANLI_DIZIN = "/opt/harman-zamani"
AYIRICILAR = {"&&", "||", ";", "{", "}", "then", "do", "else", "fi", "done"}
KOMUT_ONCESI = {"sudo", "&&", "||", ";", "{", "then", "do", "else"}


def _j6():
    """J6 kapısını modül olarak yükler — bölüm/blok ayrıştırması ortak."""
    yol = Path(__file__).resolve().parent / "ci-surum-aktarimi-kapisi.py"
    spec = importlib.util.spec_from_file_location("j6_kapisi", yol)
    if spec is None or spec.loader is None:
        raise ImportError(f"{yol} yüklenemedi")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _tirnaksiz(jeton: str) -> str:
    return jeton.strip("\"'").rstrip("/")


def silinen_canli_dizin(satir: str) -> bool:
    """Satır, canlı dizini silen bir ``rm`` taşıyor mu."""
    jetonlar = satir.split()
    for i, jeton in enumerate(jetonlar):
        # Komut jetonu da tırnaksızlaştırılır: `"rm" -rf …` kabukta ÇALIŞAN bir
        # silmedir. Ölçüldü — jetonu ham karşılaştırınca kapıdan geçiyordu.
        if _tirnaksiz(jeton) != "rm":
            continue
        if i > 0 and jetonlar[i - 1] not in KOMUT_ONCESI:
            continue  # komut konumunda değil
        for ham_arg in jetonlar[i + 1:]:
            # Hedef ÖNCE sınanır, ayırıcı sonra: `rm -rf /opt/x; echo ok`
            # biçiminde yol jetonu `;` ile bitişiktir ve önce kesseydik
            # ÇALIŞAN bir silme görülmeden kaçardı. Ölçüldü.
            arg = ham_arg.rstrip(";")
            if arg and not arg.startswith("-") and _tirnaksiz(arg) == CANLI_DIZIN:
                return True
            if ham_arg in AYIRICILAR or ham_arg.endswith(";"):
                break
    return False


def main() -> int:
    j6 = _j6()
    kok = Path(__file__).resolve().parent.parent
    runbook = kok / "docs" / "ops" / "DEPLOY.md"
    try:
        metin = runbook.read_text(encoding="utf-8")
    except OSError as hata:
        print(f"J7 runbook okunamadı: {hata}")
        return 1

    try:
        bloklar = j6.bloklari_al(metin)
    except ValueError as hata:
        print(f"J7 sürüm aktarımı bölümü okunamadı: {hata}")
        return 1

    if not bloklar:
        print("J7 sürüm aktarımı bölümünde bash bloğu yok — bölüm silinmiş olabilir")
        return 1

    ihlaller: list[str] = []
    taranan = 0
    for etiket, satirlar in bloklar:
        for satir in j6.mantiksal_satirlar(satirlar):
            sade = satir.strip()
            if not sade or sade.startswith("#"):
                continue
            taranan += 1
            if silinen_canli_dizin(sade):
                ihlaller.append(f"[{etiket}] {sade[:110]}")

    if not taranan:
        print("J7 taranacak komut bulunamadı — kapı boşa düşmüş olabilir")
        return 1

    if ihlaller:
        print(
            f"J7 CANLI DİZİNİ SİLEN {len(ihlaller)} adım var — geri alma "
            f"yeniden adlandırmayla yapılır, {CANLI_DIZIN} silinmez:"
        )
        for s in ihlaller:
            print(f"     {s}")
        return 1

    print(
        f"J7 sürüm aktarımı bölümündeki {taranan} komuttan hiçbiri "
        f"{CANLI_DIZIN} dizinini silmiyor"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
