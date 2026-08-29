#!/usr/bin/env python3
"""deploy-sozlesme-testi.sh içindeki denetim kümesini statik ve çalışma anında doğrular.

Bu kapı:
  1) Statik mod (<script> <manifest>): `deploy/deploy-sozlesme-testi.sh`
     metnini ayrıştırarak tanımlı denetim kimliklerini dondurulmuş manifest ile
     karşılaştırır.
  2) Çalışma anı modu (--emitted <emitted_file> <manifest>): Gerçek koşuda
     `yesil` tarafından dosyaya yazılan denetim kimliklerini manifest ile
     birebir karşılaştırır. `if false` veya erken `exit 0` ile devre dışı
     bırakılan kapılar bu modda fail-closed KIRMIZI olur.
  3) Listeleme modu (--list <script>): Script içindeki tüm denetim kimliklerini
     çalıştırmadan stdout'a döker.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def extract_checks(script_text: str) -> list[str]:
    """deploy-sozlesme-testi.sh metninden denetim kimliklerini sırayla çıkarır."""
    checks: list[str] = []
    lines = script_text.splitlines()
    in_j4 = False
    for idx, line in enumerate(lines):
        if "J4_ADIMLARI=(" in line:
            in_j4 = True
            continue
        if in_j4 and line.strip().startswith(")"):
            in_j4 = False
            continue

        # 1. K9 davranışsal ölçüm çağrıları: k9_olc "<ad>"
        m_k9 = re.search(r'k9_olc\s+["\']([^"\']+)["\']', line)
        if m_k9:
            checks.append(f"K9/{m_k9.group(1)}")
            continue

        # 2. Section I akış senaryoları: akis_senaryo "<ad>"
        m_akis = re.search(r'akis_senaryo\s+["\']([^"\']+)["\']', line)
        if m_akis:
            checks.append(f"I/{m_akis.group(1)}")
            continue

        # 3. Section H3 döngüsü: for mod in pg_dump-hata kucuk bozuk-arsiv; do
        if "for mod in pg_dump-hata kucuk bozuk-arsiv" in line:
            for m in ["pg_dump-hata", "kucuk", "bozuk-arsiv"]:
                checks.append(f"H3/{m}")
            continue

        # 4. Section J4 runbook adım tablosu: J4_ADIMLARI=( ... "<ad>|<desen>" ... )
        if in_j4:
            m_j4 = re.search(r'^\s*["\']([^"\'|]+)\|', line)
            if m_j4:
                checks.append(f"J4/{m_j4.group(1)}")
                continue

        # 5. Standart yesil çağrıları
        m_yesil = re.search(r'yesil\s+["\']([^"\']+)["\']', line)
        if m_yesil:
            val = m_yesil.group(1)
            # $K1_CIKTI, $K5_CAGRI, $J6_CIKTI gibi dinamik değişken referansları
            m_var = re.match(r'\$([A-Z0-9]+)_(?:CIKTI|CAGRI)', val)
            if m_var:
                checks.append(m_var.group(1))
                continue
            # Şablon etiketlerini atla: "H3/$mod ...", "I/$ad ...", "J4/$ad ...", "K9/$ad ..."
            if re.match(r'^[A-Z0-9]+/\$[a-zA-Z_]', val):
                continue
            # "A0 ...", "H7g/TAG ...", "K2 ..." gibi doğrudan etiketler
            m_tag = re.match(r'([A-Z][0-9a-zA-Z]*(?:/[A-Za-z0-9_-]+)?)\b', val)
            if m_tag:
                tag = m_tag.group(1)
                checks.append(tag)
                continue

    return checks


def main() -> int:
    if len(sys.argv) < 2:
        print("Kullanim:", file=sys.stderr)
        print("  ci-deploy-sozlesme-kapisi.py --list <deploy-sozlesme-testi.sh>", file=sys.stderr)
        print("  ci-deploy-sozlesme-kapisi.py --emitted <emitted_file> <manifest.json>", file=sys.stderr)
        print("  ci-deploy-sozlesme-kapisi.py <deploy-sozlesme-testi.sh> <manifest.json>", file=sys.stderr)
        return 1

    if sys.argv[1] == "--list":
        if len(sys.argv) != 3:
            print("Hata: --list icin betik yolu gereklidir", file=sys.stderr)
            return 1
        script_path = Path(sys.argv[2])
        if not script_path.exists():
            print(f"Hata: betik bulunamadi: {script_path}", file=sys.stderr)
            return 1
        checks = extract_checks(script_path.read_text(encoding="utf-8"))
        for c in checks:
            print(c)
        return 0

    if sys.argv[1] in ("--emitted", "--runtime"):
        if len(sys.argv) != 4:
            print("Hata: --emitted icin <emitted_file> <manifest.json> gereklidir", file=sys.stderr)
            return 1
        emitted_path = Path(sys.argv[2])
        manifest_path = Path(sys.argv[3])
        if not emitted_path.exists():
            print(f"K10 deploy sozlesme calisma-ani kapisi KIRMIZI: calisan denetim dosyasi bulunamadi: {emitted_path}")
            return 1
        if not manifest_path.exists():
            print(f"K10 deploy sozlesme calisma-ani kapisi KIRMIZI: manifest bulunamadi: {manifest_path}")
            return 1

        emitted_checks = [line.strip() for line in emitted_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        manifest_checks: list[str] = json.loads(manifest_path.read_text(encoding="utf-8"))

        missing = [c for c in manifest_checks if c not in emitted_checks]
        unexpected = [c for c in emitted_checks if c not in manifest_checks]

        if missing or unexpected or emitted_checks != manifest_checks:
            print(
                f"K10 deploy sozlesme calisma-ani kapisi KIRMIZI: calisan denetimler manifest ile uyusmuyor "
                f"(emitted={len(emitted_checks)}, manifest={len(manifest_checks)}, "
                f"eksik={missing}, beklenmeyen={unexpected})"
            )
            return 1

        print(f"K10 deploy sozlesme calisma-ani kapisi YESIL: {len(emitted_checks)}/{len(manifest_checks)} denetim gercekten kostu ve manifest ile eslesti")
        return 0

    if len(sys.argv) != 3:
        print("Hata: gecersiz arguman sayisi", file=sys.stderr)
        return 1

    script_path = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2])

    if not script_path.exists():
        print(f"K10 deploy sozlesme testleri kapisi KIRMIZI: betik bulunamadi: {script_path}")
        return 1
    if not manifest_path.exists():
        print(f"K10 deploy sozlesme testleri kapisi KIRMIZI: manifest bulunamadi: {manifest_path}")
        return 1

    script_checks = extract_checks(script_path.read_text(encoding="utf-8"))
    manifest_checks: list[str] = json.loads(manifest_path.read_text(encoding="utf-8"))

    missing = [c for c in manifest_checks if c not in script_checks]
    unexpected = [c for c in script_checks if c not in manifest_checks]

    if missing or unexpected or script_checks != manifest_checks:
        print(
            f"K10 deploy sozlesme testleri kapisi KIRMIZI: denetim kumesi uyusmuyor "
            f"(script={len(script_checks)}, manifest={len(manifest_checks)}, "
            f"eksik={missing}, beklenmeyen={unexpected})"
        )
        return 1

    print(f"K10 deploy sozlesme testleri kapisi YESIL: {len(script_checks)} denetim manifest ile tam kume esitliginde")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
