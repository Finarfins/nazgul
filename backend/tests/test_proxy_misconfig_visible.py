"""Ters vekil yanlış tanımlanırsa SESSİZ kalmasın + yapılandırma tutarlı kalsın.

Bu dosya bir hatayı düzeltmiyor; bugün her şey doğru. **Sessiz bozulma
ihtimalini görünür kılıyor.**

RİSK NEDİR. `resolve_client_host`, `X-Forwarded-For` başlığına yalnız
bağlanan taraf `TRUSTED_PROXY_CIDRS` içindeyse itibar eder — bu doğru ve
güvenli davranış (aksi hâlde istemci kendi IP'sini uydurabilirdi). Ama liste
YANLIŞSA sonuç SESSİZDİR: hata yok, log yok, her isteğin "istemci IP'si"
vekilin kendi adresi olur.

Bu neden önemli: giriş kilidi `(kullanıcı adı, IP)` ikilisine bakıyor. Tüm
kullanıcılar tek IP'ye düşerse IP başına sınırlar anlamını yitirir.

İKİ SAVUNMA:

1. **Çalışma anında uyarı.** Güvenilmeyen bir taraftan `X-Forwarded-For`
   gelmesi, "önümde bir vekil var ama onu tanımıyorum" demektir. Bir kez
   loglanır — her istekte loglamak asıl olayları görünmez kılardı.
2. **Depo tutarlılığı.** `docker-compose.prod.yml` ağ alt ağı ile örnek
   yapılandırmadaki `TRUSTED_PROXY_CIDRS` aynı olmalı. Bugün ikisi de
   172.28.0.0/16; ikisini senkron tutan bir şey YOKTU.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
KOK = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app import client_ip  # noqa: E402
from app.client_ip import parse_trusted_proxy_networks, resolve_client_host  # noqa: E402

VEKIL_AGI = parse_trusted_proxy_networks("172.28.0.0/16")


def test_trusted_proxy_still_resolves_the_real_client() -> None:
    """Doğru yapılandırmada gerçek istemci IP'si çözülür — davranış korunuyor."""
    assert resolve_client_host("172.28.0.5", "203.0.113.9", VEKIL_AGI) == "203.0.113.9"


def test_untrusted_peer_is_not_believed() -> None:
    """Güvenilmeyen taraf kendi IP'sini UYDURAMAZ.

    Uyarı eklerken bu davranışın değişmediğini çivilemek şart: uyarı bir
    gevşetme değil, yalnız görünürlük.
    """
    assert resolve_client_host("198.51.100.7", "10.0.0.1", VEKIL_AGI) == "198.51.100.7"


def test_misconfigured_proxy_logs_a_warning_once(caplog) -> None:
    """Vekil var ama tanınmıyorsa BİR KEZ uyar.

    Sessiz kalırsa yapılandırma hatası aylarca fark edilmez; her istekte
    loglarsa asıl olaylar görünmez olur.
    """
    client_ip._MISCONFIG_WARNED = False
    with caplog.at_level(logging.WARNING, logger="app.client_ip"):
        # Vekil 10.x'ten bağlanıyor ama güvenilen ağ 172.28.x — yani önümüzde
        # tanımadığımız bir vekil var.
        for _ in range(3):
            resolve_client_host("10.5.0.4", "203.0.113.9", VEKIL_AGI)

    uyarilar = [k for k in caplog.records if "TRUSTED_PROXY_CIDRS" in k.getMessage()]
    assert len(uyarilar) == 1, f"tam bir uyarı bekleniyordu, {len(uyarilar)} çıktı"
    mesaj = uyarilar[0].getMessage()
    # Mesaj NE YAPILACAĞINI söylemeli; yalnız "bir sorun var" demek yetmez.
    assert "10.5.0.4" in mesaj, mesaj
    assert "giriş kilidi" in mesaj.lower(), mesaj


def test_no_warning_when_there_is_no_proxy_header(caplog) -> None:
    """Vekilsiz kurulumda uyarı YOK — yanlış alarm, gerçek alarmı değersizleştirir."""
    client_ip._MISCONFIG_WARNED = False
    with caplog.at_level(logging.WARNING, logger="app.client_ip"):
        resolve_client_host("198.51.100.7", None, VEKIL_AGI)
    assert [k for k in caplog.records if "TRUSTED_PROXY_CIDRS" in k.getMessage()] == []


def test_compose_subnet_and_example_cidr_agree() -> None:
    """Ağ alt ağı ile güvenilen vekil listesi AYNI olmalı.

    İkisi ayrışırsa istemci IP çözümü sessizce bozulur. Bugün ikisi de
    172.28.0.0/16 ama bunu koruyan bir şey yoktu.
    """
    compose = (KOK / "docker-compose.prod.yml").read_text(encoding="utf-8")
    ornek = (KOK / ".env.production.example").read_text(encoding="utf-8")

    alt_aglar = re.findall(r"subnet:\s*([0-9./]+)", compose)
    assert alt_aglar, "docker-compose.prod.yml içinde subnet bulunamadı"

    cidrler = re.findall(r"^TRUSTED_PROXY_CIDRS=(.+)$", ornek, re.MULTILINE)
    assert cidrler, ".env.production.example içinde TRUSTED_PROXY_CIDRS bulunamadı"

    beklenen = {x.strip() for x in cidrler[0].split(",") if x.strip()}
    assert set(alt_aglar) <= beklenen, (
        "docker ağı TRUSTED_PROXY_CIDRS kapsamında değil: "
        f"ağ={alt_aglar}, güvenilen={sorted(beklenen)}. "
        "Ayrışırlarsa istemci IP'si vekilin adresi olur ve giriş kilidi "
        "tüm kullanıcılar için ortaklaşır."
    )
