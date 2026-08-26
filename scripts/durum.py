#!/usr/bin/env python3
"""İnen iş kaydını EN YENİ ÜSTTE olacak şekilde basar.

Kayıt tek bir dosyada DEĞİL, `docs/durum/` altında girdi başına bir dosyada
tutulur. Sebebi ölçülmüştür: aynı dosyanın aynı satırına ekleyen iki dal,
İÇERİK çakışmadan yalnız KONUM yüzünden çakışır. Ayrı dosyalar aynı yolu
paylaşmadığı için birleşme çakışması YAPISAL olarak imkânsızdır.

Kullanım:
    python scripts/durum.py            # en yeni üstte bas
    python scripts/durum.py --sonraki 67   # yeni girdinin dosya adını söyle
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KAYIT_DIZINI = Path(__file__).resolve().parent.parent / "docs" / "durum"
AD_DESENI = re.compile(r"^(\d{4})-pr-(\d{4})\.md$")


def girdileri_oku(dizin: Path = KAYIT_DIZINI) -> list[tuple[int, int, str, Path]]:
    """(sıra, pr, metin, yol) demetlerini EN YENİDEN ESKİYE döndürür.

    Sıralama anahtarı (sıra, pr): `sıra` insanın gördüğü okuma sırasını
    taşır. İkisi de dosya ADINDA olduğu için sıralamayı okumak hiçbir dosyanın
    İÇİNİ değiştirmeyi gerektirmez.

    `pr` İKİNCİL ANAHTARDIR VE BİR LİSANS DEĞİLDİR. Aynı sırayı taşıyan iki
    girdi okunabilir kalsın diye vardır; o durumun İNMESİ serbest demek
    DEĞİLDİR. Ölçüldü: develop'ın 284 tepe durumunun hiçbirinde yinelenen sıra
    yok — sıra inen kayıtta benzersizdir ve `yinelenen_sira_denetle` bunu
    zorlar. `pr` yalnız birleşme sonucu geçici olarak yinelendiğinde okumayı
    belirlenimci tutar.
    """
    girdiler: list[tuple[int, int, str, Path]] = []
    for yol in sorted(dizin.glob("*.md")):
        eslesme = AD_DESENI.match(yol.name)
        if not eslesme:
            raise ValueError(
                f"{yol.name}: dosya adı <sıra>-pr-<numara>.md biçiminde olmalı"
            )
        metin = yol.read_text(encoding="utf-8").strip("\n")
        if "\n" in metin:
            raise ValueError(f"{yol.name}: bir girdi TEK satırdır")
        if not metin.strip():
            raise ValueError(f"{yol.name}: girdi boş olamaz")
        girdiler.append((int(eslesme.group(1)), int(eslesme.group(2)), metin, yol))
    girdiler.sort(key=lambda girdi: (girdi[0], girdi[1]), reverse=True)
    return girdiler


def sonraki_ad(dizin: Path = KAYIT_DIZINI, pr: int = 0) -> str:
    """Yeni bir girdinin alması gereken dosya adı.

    İki eşzamanlı PR aynı `sıra` değerini alır — bu SORUN DEĞİLDİR: PR
    numaraları farklı olduğu için dosya adları da farklıdır, dolayısıyla
    aynı yolu yazmazlar ve çakışmazlar.
    """
    mevcut = girdileri_oku(dizin)
    sira = (mevcut[0][0] + 1) if mevcut else 1
    return f"{sira:04d}-pr-{pr:04d}.md"


def sira_ayikla(adlar: list[str]) -> list[tuple[int, int]]:
    """Dosya adlarından (sıra, pr) çiftlerini çıkarır."""
    cikti: list[tuple[int, int]] = []
    for ad in adlar:
        eslesme = AD_DESENI.match(Path(ad).name)
        if not eslesme:
            raise ValueError(f"{ad}: dosya adı <sıra>-pr-<numara>.md biçiminde olmalı")
        cikti.append((int(eslesme.group(1)), int(eslesme.group(2))))
    return cikti


def bayat_sira_denetle(base_adlari: list[str], head_adlari: list[str]) -> list[str]:
    """BAYAT sıra ihlallerini döndürür; boş liste = temiz.

    Bu denetim TASARIM GEREĞİ ağaç-yerel DEĞİLDİR. `sonraki_ad()` sırayı
    dalın KENDİ ağacından hesaplar; dolayısıyla dal bayatladığında üretilen
    sıra sessizce geride kalır ve okuyucu o girdiyi, ondan ÖNCE inmiş
    girdilerin ÜSTÜNDE gösterir. Kayıt "hangi iş ne zaman indi" demeyi
    bırakır. Kusur yalnız base ile head'in BİRLEŞİMİNDE vardır, bu yüzden
    yalnız birleşme sonucundan görülebilir — `alembic-chain` kapısının
    aynı sebeple açık birleşme kurmasıyla aynı ders.

    AYRIM — eşzamanlılık MEŞRU, bayatlık DEĞİL:
      * Eşzamanlı bir girdi base'in EN BÜYÜK sırasındadır ya da bir
        fazlasındadır: aynı kuşaktan iki PR aynı sırayı seçer ve `pr`
        onları ayırır. Bu, tasarımın var olma sebebidir.
      * Bayat bir girdi base'in en büyüğünün ALTINDADIR: aradan başka
        kuşaklar inmiştir ve bu girdi onların üstünde görünür.

    ALT SINIR — `sıra < base_max` BAYATTIR. Bloklayan kusur budur.

    ÜST SINIR — eklenen sıralar base'in üstünde BOŞLUKSUZ olmalı. Tek girdi
    ekleyen normal bir PR için bu `base_max + 1` demektir; ama bir PR birden
    çok girdi ekleyebilir (bu PR göçte 31 tane ekliyor) ve o durumda 1..31
    meşrudur. Bu yüzden sınır sabit değil, EKLENEN SAYISINA bağlı:
    `max(eklenen) <= base_max + eklenen_farkli_sira_sayisi`. Boşluk bırakarak
    sırayı olduğundan yeni göstermek böylece hâlâ kırmızıdır.
    """
    base_ciftler = sira_ayikla(base_adlari)
    base_max = max((sira for sira, _ in base_ciftler), default=0)
    eklenen = sorted(set(sira_ayikla(head_adlari)) - set(base_ciftler))
    eklenen_siralar = {sira for sira, _ in eklenen}
    ust_sinir = base_max + len(eklenen_siralar)

    ihlaller: list[str] = []
    for sira, pr in eklenen:
        if sira < base_max:
            ihlaller.append(
                f"{sira:04d}-pr-{pr:04d}.md: BAYAT sıra — base'in en büyüğü "
                f"{base_max:04d}, bu girdi {sira:04d}. Dal, kayıt sırası "
                f"bakımından geride kalmış: bu girdi kendisinden ÖNCE inmiş "
                f"girdilerin üstünde okunur. Çare: develop'ı dala merge edip "
                f"girdi dosyasını `python scripts/durum.py --sonraki {pr}` "
                f"adıyla yeniden adlandırın."
            )
        elif sira > ust_sinir:
            ihlaller.append(
                f"{sira:04d}-pr-{pr:04d}.md: sıra BOŞLUK bırakmış — base'in en "
                f"büyüğü {base_max:04d}, bu PR {len(eklenen_siralar)} farklı sıra "
                f"ekliyor, izin verilen en büyük {ust_sinir:04d}. Sırayı "
                f"olduğundan yeni göstermek okuma sırasını yanlışlar."
            )
    return ihlaller


# VARLIK KAPISI — girdinin DOĞRULUĞU değil, VAR OLUŞU ölçülür.
#
# NİYE AYRI BİR KAPI: `bayat_sira_denetle` yalnız VAR OLAN bir girdinin sırasını
# denetler. Girdi HİÇ yoksa denetleyecek bir şey bulamaz ve sessizce yeşil
# kalır. Ölçüldü (2026-08-17, develop f244c8f): bu depoda birleşmiş 50 PR'ın
# 17'si girdisiz indi; kaydın kendi döneminde bile #68, #71 ve #72 girdisiz
# geçti. Kayıt bugüne kadar YALNIZ iki birleşmeyle büyüdü: #67 (göç, 31 girdi)
# ve #70 (kendi girdisi + #68'inki). Yani "PR başına bir girdi" kuralı bir kez
# uygulandı; kuralı ölçen bir şey olmadığı için gerisi kaydedilmedi.
#
# MUAFİYET YOK — ARANDI VE BULUNAMADI. `docs/DURUM.md`, `scripts/durum.py` ve
# `backend/tests/test_durum_kaydi.py` içinde muafiyet/istisna bildiren HİÇBİR
# metin yok. Ölçüm aracı olarak açılan ve hiç birleşmeyen PR'lar (#69, #74)
# muafiyet gerektirmez: bunlar gerçek PR'ın head'ini DEĞİŞTİRMEDEN taşır, o
# yüzden gerçek PR'ın girdisini de taşır — gerçek PR yeşilse araç da yeşildir.
def girdi_varligi_denetle(
    base_adlari: list[str], head_adlari: list[str], pr: int | None = None
) -> list[str]:
    """Birleşme sonucu KENDİ girdisini eklemiş olmalı.

    KURAL SIKI: eklenen girdilerden EN AZ BİRİ birleşen PR'ın kendi numarasını
    taşımalı. Başkasının girdisini geriye dönük yazmak (backfill) SERBESTTİR ve
    yasaklanmadı — #70 tam bunu yaptı: `0032-pr-0068` ile `0033-pr-0070`. Sıkı
    kural bunu reddetmez, çünkü ikincisi #70'i adlandırır.

    NİYE GEVŞEK KURAL YETMEZ: yalnız "bir girdi eklendi mi" diye sorulursa, bir
    PR BAŞKASININ girdisini yazıp kendi girdisi olmadan inebilir — kapatmaya
    çalıştığımız boşluğun aynısını bir kat aşağıda, üstelik yeşil kapıdan
    geçerek üretir.

    ÖLÇÜLDÜ (2026-08-17, develop f244c8f3): 64 birleşme geriye dönük sınandı.
    Gevşek kuraldan geçen 2 (#67, #70), sıkı kuraldan geçen de AYNI 2. Gevşekten
    geçip sıkıdan kalan birleşme sayısı SIFIR — yani sıkı kural bugüne kadarki
    hiçbir meşru işi reddetmezdi; bedeli yok, kapattığı delik gerçek.
    """
    eklenen = sorted(set(sira_ayikla(head_adlari)) - set(sira_ayikla(base_adlari)))
    if eklenen and pr is not None and not any(p == pr for _, p in eklenen):
        yazilanlar = ", ".join(f"#{p}" for _, p in eklenen)
        return [
            f"KENDİ GİRDİSİ YOK — bu PR (#{pr}) `docs/durum/` altına girdi "
            f"ekliyor ama hiçbiri kendisini adlandırmıyor: eklenen {yazilanlar}. "
            "Başkasının girdisini yazmak serbesttir, kendi girdisinin YERİNE "
            "geçemez: aksi hâlde iş, yeşil bir kapıdan geçerek kayıtsız iner. "
            f"Çare: `python scripts/durum.py --sonraki {pr}` komutunun verdiği "
            "adla kendi girdinizi de ekleyin."
        ]
    if eklenen:
        return []
    return [
        "GİRDİ YOK — bu PR'ın birleşme sonucu `docs/durum/` altına hiçbir yeni "
        "girdi eklemiyor. Kayıt PR başına bir girdiyle büyür; girdisiz inen iş "
        "kayıttan düşer ve sonradan ancak hatırlanarak geri konur. Çare: "
        "`python scripts/durum.py --sonraki <PR numarası>` komutunun verdiği "
        "adla dosyayı oluşturup tek satırlık girdinizi yazın."
    ]


# YİNELENEN SIRA — TEK BİR AĞAÇ LİSTESİ ÜZERİNDE.
#
# İLK SÜRÜM YANLIŞTI: `set(base) | set(head)` bir BİRLEŞME SONUCU DEĞİL, iki
# listenin BİRLEŞİMİdir. Head bir girdiyi SİLİP başkasını eklerse (silme,
# yeniden adlandırma, base/head sınırını geçen taşıma) birleşimde ikisi de
# durur ve kapı GEÇERLİ bir birleşmeyi reddeder. Kapının işi sonucu
# BELGELEMEKken, ölçmediği bir şeyi reddediyordu.
#
# ARTIK TEK GİRDİ: ölçülen ağacın dosya listesi. Silme ve yeniden adlandırma
# MODELLENMEZ — çünkü ağaç zaten sonucu taşır.
#
# BU AĞAÇ NEREDEN GELİR:
#   * CI: `durum-kaydi` işi base'i alıp `git merge --no-edit --no-ff $HEAD_SHA`
#     çalıştırır ("Merge made by the 'ort' strategy"), yani ÇALIŞAN AĞAÇ
#     birleşme sonucudur ve `--kapi` onu okur.
#   * pytest: gerçek bir dizin kurulur (`tmp_path`) ve dosyaları listelenir;
#     yani testler de LİSTE ÇİFTİ değil AĞAÇ verir. Kusuru üreten şey liste
#     çiftiydi.
#
# SINIR (COST, HOLE DEĞİL): yerelde `--kapi` çalışan ağacı okur; dal develop'ı
# merge etmemişse o ağaç birleşme sonucu DEĞİLDİR ve yinelenme görünmez. Kapı
# bozulmaz, yalnız yerelde erken uyarmaz; kararın verildiği yer CI'dır.
def yinelenen_sira_denetle(agac_adlari: list[str]) -> list[str]:
    """Ölçülen AĞAÇTA aynı sırayı iki girdi taşıyorsa İHLAL — ikisini de adlandırır."""
    gruplar: dict[int, list[int]] = {}
    for sira, pr in sira_ayikla(agac_adlari):
        gruplar.setdefault(sira, []).append(pr)
    ihlaller: list[str] = []
    for sira in sorted(gruplar):
        prler = sorted(set(gruplar[sira]))
        if len(prler) < 2:
            continue
        dosyalar = ", ".join(f"{sira:04d}-pr-{pr:04d}.md" for pr in prler)
        ihlaller.append(
            f"YİNELENEN sıra {sira:04d}: {dosyalar}. Sıra İNİŞ SIRASIDIR ve "
            "benzersiz olmalıdır; aynı numarayı taşıyan iki girdi, hangisinin "
            "önce indiğini kayıttan okunamaz kılar. Çare: sonra inen girdiyi "
            f"`python scripts/durum.py --sonraki <PR>` ile yeniden adlandırın."
        )
    return ihlaller


def _git_girdi_adlari(revizyon: str) -> list[str]:
    """Bir revizyondaki girdi dosyalarının adlarını git'ten okur."""
    import subprocess

    sonuc = subprocess.run(
        ["git", "ls-tree", "--name-only", revizyon, "docs/durum/"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [s.strip() for s in sonuc.stdout.split("\n") if s.strip().endswith(".md")]


def main() -> int:
    # Kayıt Türkçe ve "→" gibi işaretler taşıyor; Windows konsolunun
    # varsayılan kod sayfası bunları basamıyor. Çıktıyı açıkça UTF-8'e
    # sabitliyoruz ki araç her iki platformda da aynı metni versin.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    ayristirici = argparse.ArgumentParser(description="İnen iş kaydı")
    ayristirici.add_argument("--sonraki", type=int, metavar="PR", default=None)
    ayristirici.add_argument(
        "--pr",
        type=int,
        metavar="NUMARA",
        default=None,
        help="birleşen PR numarası; verilirse VARLIK kapısı SIKI çalışır "
        "(eklenen girdilerden biri bu numarayı adlandırmalı)",
    )
    ayristirici.add_argument(
        "--kapi",
        metavar="BASE",
        default=None,
        help="BAYAT sıra kapısı: verilen base revizyonuna karşı ÇALIŞILAN "
        "AĞACI (birleşme sonucunu) denetler",
    )
    argumanlar = ayristirici.parse_args()
    if argumanlar.sonraki is not None:
        print(f"docs/durum/{sonraki_ad(pr=argumanlar.sonraki)}")
        return 0
    if argumanlar.kapi is not None:
        base_adlari = _git_girdi_adlari(argumanlar.kapi)
        head_adlari = [f"docs/durum/{yol.name}" for _, _, _, yol in girdileri_oku()]
        print(f"base girdi sayısı: {len(base_adlari)}")
        print(f"ölçülen ağaçtaki girdi sayısı: {len(head_adlari)}")
        ihlaller = bayat_sira_denetle(base_adlari, head_adlari)
        ihlaller += girdi_varligi_denetle(base_adlari, head_adlari, argumanlar.pr)
        # TEK AĞAÇ: çalışan ağaç (CI'da birleşme sonucu).
        ihlaller += yinelenen_sira_denetle(head_adlari)
        for ihlal in ihlaller:
            print(f"::error::{ihlal}", file=sys.stderr)
        if ihlaller:
            return 1
        print("BAYAT SIRA YOK")
        print("YİNELENEN SIRA YOK")
        print("GİRDİ VAR" if argumanlar.pr is None else f"GİRDİ VAR (#{argumanlar.pr} kendi girdisini ekliyor)")
        return 0
    for _, _, metin, _ in girdileri_oku():
        print(metin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
