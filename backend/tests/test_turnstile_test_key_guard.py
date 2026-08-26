"""Production'da Cloudflare TEST anahtarı = bot koruması KAPALI.

ÖLÇÜLDÜ (2026-08-08, canlı sistemde): harmanzamani.com'un kayıt sayfası
`1x00000000000000000000AA` site anahtarıyla yayındaydı. Bu, Cloudflare'ın
belgelenmiş test anahtarı ve HER doğrulamayı geçirir. Yani kayıt formunun bot
koruması fiilen kapalıydı; `/api/auth/register` ucu ise açık ve çalışır
durumdaydı (boş gövdeye 422 dönüyordu).

BOŞLUĞUN KAYNAĞI BİR YANLIŞ GÜVEN. `config.py` production'da
``TURNSTILE_SECRET_KEY``i zorunlu tutuyor — ama yalnız DOLU olmasına bakıyor.
Test anahtarı bu kontrolü geçiyor ve sistem "bot koruması yapılandırılmış"
görünüyor. **"Var mı" kontrolü, "işe yarıyor mu" sorusunu cevaplamaz.**

Testler üç şeyi çiviliyor:

1. **Production + test anahtarı → kayıt 503.** Kabul etmek, korumayı sessizce
   kapatmak olurdu.
2. **Geliştirme/CI etkilenmez.** Test anahtarları orada DOĞRU araçtır; onları
   da reddetmek testleri ve yerel geliştirmeyi kırardı.
3. **Gerçek anahtar production'da normal çalışır** — kontrol yalnız test
   anahtarını hedefliyor, gerçek anahtarın önüne geçmiyor.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.routers import auth as auth_router  # noqa: E402

# Cloudflare'ın belgelenmiş test GİZLİ anahtarları.
GECIREN = "1x0000000000000000000000000000000AA"
REDDEDEN = "2x0000000000000000000000000000000AA"
KULLANILMIS = "3x0000000000000000000000000000000AA"
GERCEK = "0x4AAAAAAABkMYinukE8nzY_gerceğe_benzer"


class _SahteAyar:
    def __init__(self, ortam: str, gizli: str | None) -> None:
        self.environment = ortam
        self.turnstile_secret_key = gizli

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _test_anahtari_mi(ortam: str, gizli: str | None) -> bool:
    with patch.object(auth_router, "settings", _SahteAyar(ortam, gizli)):
        return auth_router._turnstile_test_key_in_production()


@pytest.mark.parametrize("gizli", [GECIREN, REDDEDEN, KULLANILMIS])
def test_production_rejects_every_documented_cloudflare_test_secret(gizli: str) -> None:
    """Üçü de test anahtarı; yalnız `1x...` değil.

    `2x...` ve `3x...` her isteği reddeder — güvenlik açığı değil ama kayıt
    akışını tamamen kırar. İkisi de "yapılandırılmamış" demektir.
    """
    assert _test_anahtari_mi("production", gizli) is True


def test_development_and_ci_are_not_affected() -> None:
    """Test anahtarları geliştirmede DOĞRU araçtır; engellenmemeli.

    Engelleseydik CI ve yerel geliştirme kırılırdı — düzeltme, düzelttiğinden
    fazlasını bozmamalı.
    """
    for ortam in ("development", "test", "staging"):
        assert _test_anahtari_mi(ortam, GECIREN) is False, ortam


def test_real_key_passes_in_production() -> None:
    """Kontrol yalnız TEST anahtarını hedefliyor, gerçek anahtarın önüne geçmiyor."""
    assert _test_anahtari_mi("production", GERCEK) is False
    # Anahtar hiç tanımlı değilse bu kontrol devreye girmez; onu config.py
    # zaten açılışta reddediyor.
    assert _test_anahtari_mi("production", None) is False
    assert _test_anahtari_mi("production", "") is False


def test_verify_turnstile_raises_503_not_400_in_production() -> None:
    """Durum kodu bilerek 503, 400 DEĞİL.

    400 "senin isteğin bozuk" demektir ve kullanıcıyı formu düzeltmeye iter —
    oysa kullanıcının yapabileceği bir şey yok, eksik olan SUNUCU
    yapılandırması. 503 + açık mesaj, doğru tarafı gösterir.
    """
    from fastapi import HTTPException

    with patch.object(auth_router, "settings", _SahteAyar("production", GECIREN)):
        with pytest.raises(HTTPException) as hata:
            auth_router._verify_turnstile("gecerli-gorunen-token", "1.2.3.4")
    assert hata.value.status_code == 503, hata.value.status_code
    assert "bot koruması" in hata.value.detail.lower(), hata.value.detail
