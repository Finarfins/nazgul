#!/usr/bin/env python3
"""İnen iş kaydını basar / kapıdan geçirir (Option B — sıra git log'dan).

Kayıt `docs/durum/` altında girdi başına bir dosyada tutulur.

Biçimler:
  * ESKİ (kesmeden önce): `<sıra>-pr-<PR>.md` — sıra dosya ADINDADIR.
  * YENİ (kesmeden sonra): `pr-<PR>.md` — sıra adda YOKTUR; görüntüleme
    sırası `git log --first-parent` ile türetilir.

Kullanım:
    python scripts/durum.py                 # --sira ile aynı (en yeni üstte)
    python scripts/durum.py --sonraki 67    # docs/durum/pr-0067.md
    python scripts/durum.py --sira          # merge sırasıyla listele
    python scripts/durum.py --kapi BASE --pr N
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import NamedTuple
from pathlib import Path

KAYIT_DIZINI = Path(__file__).resolve().parent.parent / "docs" / "durum"
DEPO_KOKU = KAYIT_DIZINI.parent.parent

# ESKİ ad: sıra dosya adında. YENİ ad: yalnız pr.
LEGACY_AD_DESENI = re.compile(r"^(\d{4})-pr-(\d{4})\.md$")
YENI_AD_DESENI = re.compile(r"^pr-(\d{4})\.md$")
# Geriye dönük uyumluluk: eski testler AD_DESENI bekleyebilir.
AD_DESENI = LEGACY_AD_DESENI

# KESME — develop `eb3a0c2` (#69 birleşmesi) üzerindeki en büyük legacy sıra.
# Bu sabitten SONRA eklenen her girdi `pr-NNNN.md` olmalı; yeni
# `SSSS-pr-NNNN.md` DOSYASI kapıyı kırar. Mevcut legacy korpus olduğu gibi
# kalır (yeniden adlandırılmaz). Ölçüldü: 112 girdi, max sıra 112.
KESME_SIRA = 112
KESME_GEREKCE = (
    "develop eb3a0c2 (Merge #69): en büyük legacy sıra 0112-pr-0069.md = 112; "
    "H6 Option B kesmesi"
)


class KayitAdi(NamedTuple):
    """Bir girdi dosya adının ayrıştırılmış hali."""

    ad: str
    pr: int
    legacy_sira: int | None  # None = yeni biçim

    @property
    def yeni(self) -> bool:
        return self.legacy_sira is None


def kayit_adi_ayikla(ad: str) -> KayitAdi | None:
    """Girdi dosya adını ayrıştırır; girdi olmayan .md için None."""
    isim = Path(ad).name
    eslesme = LEGACY_AD_DESENI.match(isim)
    if eslesme:
        return KayitAdi(isim, int(eslesme.group(2)), int(eslesme.group(1)))
    eslesme = YENI_AD_DESENI.match(isim)
    if eslesme:
        return KayitAdi(isim, int(eslesme.group(1)), None)
    return None


def sonraki_ad(pr: int = 0, dizin: Path = KAYIT_DIZINI) -> str:
    """Yeni girdinin dosya adı — sıra HESAPLANMAZ (Option B).

    `dizin` imzada durur: eski çağrıları kırmamak için; kullanılmaz.
    """
    del dizin  # sıra ağaçtan üretilmez
    return f"pr-{pr:04d}.md"


def _metin_dogrula(yol: Path) -> str:
    metin = yol.read_text(encoding="utf-8").strip("\n")
    if "\n" in metin:
        raise ValueError(f"{yol.name}: bir girdi TEK satırdır")
    if not metin.strip():
        raise ValueError(f"{yol.name}: girdi boş olamaz")
    return metin


def girdileri_oku(dizin: Path = KAYIT_DIZINI) -> list[tuple[int, int, str, Path]]:
    """(görünen_sıra, pr, metin, yol) — EN YENİDEN ESKİYE.

    Görünen sıra: legacy dosyada addaki sayı; yeni dosyada henüz git'siz
    okumada `KESME_SIRA + 1` tabanlı geçici anahtar (asıl sıra `--sira` /
    `sira_listesi` ile gelir). Girdi olmayan `.md` yok sayılır.
    """
    kayitlar: list[tuple[int, int, str, Path]] = []
    yeni_prler: list[tuple[int, str, Path]] = []
    for yol in sorted(dizin.glob("*.md")):
        ad = kayit_adi_ayikla(yol.name)
        if ad is None:
            continue
        metin = _metin_dogrula(yol)
        if ad.legacy_sira is not None:
            kayitlar.append((ad.legacy_sira, ad.pr, metin, yol))
        else:
            yeni_prler.append((ad.pr, metin, yol))
    # Git yokken yeni girdileri pr'ye göre sıralı göster; sıra = kesme+konum.
    yeni_prler.sort(key=lambda t: t[0])
    for konum, (pr, metin, yol) in enumerate(yeni_prler, start=1):
        kayitlar.append((KESME_SIRA + konum, pr, metin, yol))
    kayitlar.sort(key=lambda girdi: (girdi[0], girdi[1]), reverse=True)
    return kayitlar


def sira_ayikla(adlar: list[str]) -> list[tuple[int, int]]:
    """Legacy adlardan (sıra, pr) çıkarır; yeni biçim ValueError.

    Bayat kapısı testleri hâlâ legacy listeleriyle çalışır.
    """
    cikti: list[tuple[int, int]] = []
    for ad in adlar:
        eslesme = LEGACY_AD_DESENI.match(Path(ad).name)
        if not eslesme:
            raise ValueError(
                f"{ad}: legacy dosya adı <sıra>-pr-<numara>.md biçiminde olmalı"
            )
        cikti.append((int(eslesme.group(1)), int(eslesme.group(2))))
    return cikti


def bayat_sira_denetle(base_adlari: list[str], head_adlari: list[str]) -> list[str]:
    """Legacy-only bayat denetimi (kesme öncesi sözleşme; --kapi ARTIK ÇAĞIRMAZ)."""
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
                f"{base_max:04d}, bu girdi {sira:04d}."
            )
        elif sira > ust_sinir:
            ihlaller.append(
                f"{sira:04d}-pr-{pr:04d}.md: sıra BOŞLUK bırakmış — base'in en "
                f"büyüğü {base_max:04d}, izin verilen en büyük {ust_sinir:04d}."
            )
    return ihlaller


def _kayitlari_ayikla(adlar: list[str]) -> list[KayitAdi]:
    cikti: list[KayitAdi] = []
    for ad in adlar:
        isim = Path(ad).name
        if not isim.endswith(".md"):
            continue
        ayr = kayit_adi_ayikla(isim)
        if ayr is None:
            # Girdi olmayan markdown (ör. tasarım notu) yok sayılır.
            continue
        cikti.append(ayr)
    return cikti


def girdi_varligi_denetle(
    base_adlari: list[str], head_adlari: list[str], pr: int | None = None
) -> list[str]:
    """Birleşme sonucu kendi girdisini eklemiş olmalı (yeni biçim: pr-NNNN.md)."""
    base_set = {k.ad for k in _kayitlari_ayikla(base_adlari)}
    head_set = {k.ad for k in _kayitlari_ayikla(head_adlari)}
    eklenen_adlar = sorted(head_set - base_set)
    eklenen = [kayit_adi_ayikla(ad) for ad in eklenen_adlar]
    eklenen = [k for k in eklenen if k is not None]

    if not eklenen:
        return [
            "GİRDİ YOK — bu PR'ın birleşme sonucu `docs/durum/` altına hiçbir yeni "
            "girdi eklemiyor. Kayıt PR başına bir girdiyle büyür. Çare: "
            "`python scripts/durum.py --sonraki <PR numarası>` → `pr-NNNN.md`."
        ]
    if pr is None:
        return []
    if not any(k.pr == pr for k in eklenen):
        yazilanlar = ", ".join(f"#{k.pr}" for k in eklenen)
        return [
            f"KENDİ GİRDİSİ YOK — bu PR (#{pr}) girdi ekliyor ama hiçbiri "
            f"kendisini adlandırmıyor: eklenen {yazilanlar}. Çare: "
            f"`python scripts/durum.py --sonraki {pr}` → `pr-{pr:04d}.md`."
        ]
    return []


def yinelenen_sira_denetle(agac_adlari: list[str]) -> list[str]:
    """Yalnız LEGACY adlarda yinelenen sıra — yeni biçimde sıra adda yoktur."""
    gruplar: dict[int, list[int]] = {}
    for kayit in _kayitlari_ayikla(agac_adlari):
        if kayit.legacy_sira is None:
            continue
        gruplar.setdefault(kayit.legacy_sira, []).append(kayit.pr)
    ihlaller: list[str] = []
    for sira in sorted(gruplar):
        prler = sorted(set(gruplar[sira]))
        if len(prler) < 2:
            continue
        dosyalar = ", ".join(f"{sira:04d}-pr-{pr:04d}.md" for pr in prler)
        ihlaller.append(f"YİNELENEN sıra {sira:04d}: {dosyalar}.")
    return ihlaller


def kesme_sonrasi_legacy_denetle(
    base_adlari: list[str], head_adlari: list[str]
) -> list[str]:
    """Kesmeden sonra YENİ legacy-adlı dosya eklemek yasak."""
    base_set = {k.ad for k in _kayitlari_ayikla(base_adlari)}
    ihlaller: list[str] = []
    for kayit in _kayitlari_ayikla(head_adlari):
        if kayit.ad in base_set:
            continue
        if kayit.legacy_sira is not None:
            ihlaller.append(
                f"KESME SONRASI LEGACY AD — `{kayit.ad}` eklendi ama kesme "
                f"sıra={KESME_SIRA} ({KESME_GEREKCE}). Yeni girdi "
                f"`pr-{kayit.pr:04d}.md` olmalı; legacy ad yeniden seçilmez."
            )
    return ihlaller


def cift_kayit_denetle(
    base_adlari: list[str], head_adlari: list[str]
) -> list[str]:
    """Çift kayıt / legacy+yeni biçim yasak.

    Tarihsel göç korpusunda aynı PR numarasına ait İKİ legacy dosya olabilir
    (ölçüldü); onlara dokunulmaz. Yasak olan:
      * bu birleşmede aynı PR için birden fazla girdi eklemek
      * ağaçta aynı PR için hem legacy ad hem `pr-NNNN.md` bulunması
        (yeni biçim eklenince eski adla yan yana olamaz)
    """
    base_set = {k.ad for k in _kayitlari_ayikla(base_adlari)}
    head_kayitlar = _kayitlari_ayikla(head_adlari)
    eklenen = [k for k in head_kayitlar if k.ad not in base_set]
    ihlaller: list[str] = []

    by_pr_eklenen: dict[int, list[KayitAdi]] = {}
    for kayit in eklenen:
        by_pr_eklenen.setdefault(kayit.pr, []).append(kayit)
    for pr, kayitlar in sorted(by_pr_eklenen.items()):
        if len(kayitlar) > 1:
            adlar = ", ".join(sorted(k.ad for k in kayitlar))
            ihlaller.append(
                f"ÇİFT KAYIT #{pr}: bu birleşmede birden fazla girdi eklendi "
                f"({adlar}). Bir PR için tam bir girdi eklenir."
            )

    by_pr_head: dict[int, list[KayitAdi]] = {}
    for kayit in head_kayitlar:
        by_pr_head.setdefault(kayit.pr, []).append(kayit)
    for pr, kayitlar in sorted(by_pr_head.items()):
        yeniler = [k for k in kayitlar if k.yeni]
        legacyler = [k for k in kayitlar if not k.yeni]
        if yeniler and legacyler:
            adlar = ", ".join(sorted(k.ad for k in kayitlar))
            ihlaller.append(
                f"LEGACY+YENİ #{pr}: aynı PR için hem legacy ad hem "
                f"`pr-{pr:04d}.md` var ({adlar}). `pr-NNNN.md` olan PR'da "
                "legacy ad bulunamaz."
            )
    return ihlaller


def tek_satir_denetle(dizin: Path = KAYIT_DIZINI) -> list[str]:
    """Çalışan ağaçtaki her girdinin tek satır olduğunu doğrular."""
    ihlaller: list[str] = []
    for yol in sorted(dizin.glob("*.md")):
        if kayit_adi_ayikla(yol.name) is None:
            continue
        try:
            _metin_dogrula(yol)
        except ValueError as exc:
            ihlaller.append(str(exc))
    return ihlaller


def tam_bir_kayit_denetle(
    base_adlari: list[str], head_adlari: list[str], pr: int
) -> list[str]:
    """`--pr N` için eklenen kendi kaydı tam bir tane ve `pr-NNNN.md` olmalı."""
    base_set = {k.ad for k in _kayitlari_ayikla(base_adlari)}
    eklenen = [
        k for k in _kayitlari_ayikla(head_adlari)
        if k.ad not in base_set and k.pr == pr
    ]
    if not eklenen:
        return []  # varlık kapısı ayrıca söyler
    if len(eklenen) != 1:
        adlar = ", ".join(sorted(k.ad for k in eklenen))
        return [
            f"TAM BİR KAYIT DEĞİL #{pr}: eklenen {adlar}. "
            f"Beklenen tek dosya: `pr-{pr:04d}.md`."
        ]
    if not eklenen[0].yeni:
        return [
            f"TAM BİR KAYIT DEĞİL #{pr}: `{eklenen[0].ad}` legacy ad; "
            f"kesmeden sonra kendi girdisi `pr-{pr:04d}.md` olmalı."
        ]
    return []


def kapi_denetle(
    base_adlari: list[str],
    head_adlari: list[str],
    pr: int | None = None,
    dizin: Path = KAYIT_DIZINI,
) -> list[str]:
    """Option B birleşme kapısı."""
    ihlaller: list[str] = []
    ihlaller += tek_satir_denetle(dizin)
    ihlaller += kesme_sonrasi_legacy_denetle(base_adlari, head_adlari)
    ihlaller += cift_kayit_denetle(base_adlari, head_adlari)
    ihlaller += girdi_varligi_denetle(base_adlari, head_adlari, pr)
    ihlaller += yinelenen_sira_denetle(head_adlari)
    if pr is not None:
        ihlaller += tam_bir_kayit_denetle(base_adlari, head_adlari, pr)
    return ihlaller


def _git_girdi_adlari(revizyon: str, repo: Path | None = None) -> list[str]:
    """Bir revizyondaki docs/durum/*.md yollarını git'ten okur."""
    cmd = ["git", "ls-tree", "--name-only", revizyon, "docs/durum/"]
    sonuc = subprocess.run(
        cmd,
        cwd=repo or DEPO_KOKU,
        capture_output=True,
        text=True,
        check=True,
    )
    return [s.strip() for s in sonuc.stdout.split("\n") if s.strip().endswith(".md")]


def _git_merge_sirali_eklemeler(
    dal: str = "develop", repo: Path | None = None
) -> list[str]:
    """docs/durum/ girdi dosyalarını first-parent ekleme/rename sırasıyla.

    `git log --first-parent --reverse --name-status` ile A (add) ve R (rename)
    satırlarından dosyanın İLK görünen adı alınır. mtime KULLANILMAZ.
    """
    kok = repo or DEPO_KOKU
    sonuc = subprocess.run(
        [
            "git",
            "log",
            "--first-parent",
            "--reverse",
            "--name-status",
            "--pretty=format:",
            dal,
            "--",
            "docs/durum/",
        ],
        cwd=kok,
        capture_output=True,
        text=True,
        check=True,
    )
    gorulen: list[str] = []
    gorulen_set: set[str] = set()
    for satir in sonuc.stdout.splitlines():
        satir = satir.strip()
        if not satir:
            continue
        parts = satir.split("\t")
        durum = parts[0]
        if durum.startswith("A") and len(parts) >= 2:
            yol = parts[1]
        elif durum.startswith("R") and len(parts) >= 3:
            yol = parts[2]  # rename hedefi
        else:
            continue
        if not yol.startswith("docs/durum/") or not yol.endswith(".md"):
            continue
        ad = Path(yol).name
        if kayit_adi_ayikla(ad) is None:
            continue
        if ad in gorulen_set:
            continue
        gorulen_set.add(ad)
        gorulen.append(ad)
    return gorulen


def sira_listesi(
    dal: str = "develop",
    repo: Path | None = None,
    dizin: Path | None = None,
) -> list[tuple[int, int, str, str]]:
    """(sıra, pr, ad, metin) merge sırasıyla ESKİDEN YENİYE.

    Legacy: dosya adındaki sıra korunur.
    Yeni: `KESME_SIRA + konum` (konum = yeni dosyaların merge sırasındaki yeri).
    """
    kok = repo or DEPO_KOKU
    kayit_dizin = dizin or (kok / "docs" / "durum")
    sirali_adlar = _git_merge_sirali_eklemeler(dal, kok)
    # Ağaçta olup log'da görünmeyen (henüz commit edilmemiş) yok — yalnız log.
    sonuc: list[tuple[int, int, str, str]] = []
    yeni_konum = 0
    for ad in sirali_adlar:
        kayit = kayit_adi_ayikla(ad)
        if kayit is None:
            continue
        yol = kayit_dizin / ad
        metin = yol.read_text(encoding="utf-8").strip("\n") if yol.is_file() else ""
        if kayit.legacy_sira is not None:
            sira = kayit.legacy_sira
        else:
            yeni_konum += 1
            sira = KESME_SIRA + yeni_konum
        sonuc.append((sira, kayit.pr, ad, metin))
    return sonuc


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    ayristirici = argparse.ArgumentParser(description="İnen iş kaydı (Option B)")
    ayristirici.add_argument("--sonraki", type=int, metavar="PR", default=None)
    ayristirici.add_argument(
        "--pr",
        type=int,
        metavar="NUMARA",
        default=None,
        help="birleşen PR numarası; varlık kapısı sıkı çalışır",
    )
    ayristirici.add_argument(
        "--kapi",
        metavar="BASE",
        default=None,
        help="Option B kapısı: base revizyonuna karşı çalışan ağaç",
    )
    ayristirici.add_argument(
        "--sira",
        nargs="?",
        const="develop",
        metavar="DAL",
        default=None,
        help="merge sırasıyla listele (varsayılan dal: develop)",
    )
    argumanlar = ayristirici.parse_args()
    if argumanlar.sonraki is not None:
        print(f"docs/durum/{sonraki_ad(pr=argumanlar.sonraki)}")
        return 0
    if argumanlar.kapi is not None:
        base_adlari = _git_girdi_adlari(argumanlar.kapi)
        head_adlari = [
            f"docs/durum/{yol.name}"
            for yol in sorted(KAYIT_DIZINI.glob("*.md"))
            if kayit_adi_ayikla(yol.name) is not None
        ]
        print(f"base girdi sayısı: {len(_kayitlari_ayikla(base_adlari))}")
        print(f"ölçülen ağaçtaki girdi sayısı: {len(_kayitlari_ayikla(head_adlari))}")
        ihlaller = kapi_denetle(base_adlari, head_adlari, argumanlar.pr)
        for ihlal in ihlaller:
            print(f"::error::{ihlal}", file=sys.stderr)
        if ihlaller:
            return 1
        print("KESME KURALI TAMAM")
        print("ÇİFT KAYIT YOK")
        print("YİNELENEN LEGACY SIRA YOK")
        if argumanlar.pr is None:
            print("GİRDİ VAR")
        else:
            print(f"GİRDİ VAR (#{argumanlar.pr} kendi girdisini ekliyor)")
        return 0
    if argumanlar.sira is not None:
        for sira, pr, ad, metin in reversed(sira_listesi(dal=argumanlar.sira)):
            print(f"{sira:04d}  {ad}  {metin}")
        return 0
    # Varsayılan: --sira develop (en yeni üstte metin)
    try:
        for _, _, _, metin in reversed(sira_listesi(dal="develop")):
            print(metin)
    except (subprocess.CalledProcessError, FileNotFoundError):
        for _, _, metin, _ in girdileri_oku():
            print(metin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
