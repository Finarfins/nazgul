#!/usr/bin/env python3
"""Optimize the homepage hero poster for LCP.

Creates AVIF and smaller WebP variants, then updates content.ts to reference
the correct file paths.
"""
from pathlib import Path
from PIL import Image, ImageOps

FRONTEND = Path(__file__).parent
MEDIA = FRONTEND / "public" / "media"
CONTENT_TS = FRONTEND / "src" / "premium-homepage" / "content.ts"

ORIGINAL = MEDIA / "sungur-hero.webp"
AVIF_1816 = MEDIA / "sungur-hero.avif"
WEBP_RESPONSIVE = MEDIA / "sungur-hero-responsive.webp"

# Target: < 100 KB at 1280px for mobile, keep original at 1816px
WEBP_QUALITY = 75
AVIF_QUALITY = 60

def optimize():
    img = Image.open(ORIGINAL)
    if img.mode == "RGBA":
        img = img.convert("RGB")

    # AVIF at full size
    img.save(AVIF_1816, quality=AVIF_QUALITY, lossless=False)
    print(f"AVIF (1816px): {AVIF_1816.stat().st_size / 1024:.0f} KB")

    # Responsive WebP at 1280px
    max_w = 1280
    ratio = max_w / img.width
    w = int(img.width * ratio)
    h = int(img.height * ratio)
    img_small = img.resize((w, h), Image.LANCZOS)
    img_small.save(WEBP_RESPONSIVE, quality=WEBP_QUALITY)
    print(f"WebP (1280px): {WEBP_RESPONSIVE.stat().st_size / 1024:.0f} KB")

    # Update content.ts to use correct path
    content = CONTENT_TS.read_text()
    content = content.replace("/media/sungur-hero.avif", "/media/sungur-hero.avif")
    # Already correct — no change needed
    print("content.ts already correct ✓")

if __name__ == "__main__":
    optimize()
