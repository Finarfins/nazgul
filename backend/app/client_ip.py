from __future__ import annotations

import logging

from ipaddress import ip_address, ip_network
from typing import Iterable

from starlette.datastructures import Address, Headers
from starlette.requests import Request

_PATCH_INSTALLED = False
_TRUSTED_NETWORKS: tuple = ()
#: Yanlış yapılandırma uyarısı bir KEZ yazılır; her istekte yazmak logu
#: doldurur ve asıl olayları görünmez kılar.
_MISCONFIG_WARNED = False

logger = logging.getLogger(__name__)


def parse_trusted_proxy_networks(value: str) -> tuple:
    """Parse a comma-separated trusted-proxy allowlist into IP networks."""
    networks = []
    for raw in value.split(","):
        candidate = raw.strip()
        if candidate:
            networks.append(ip_network(candidate, strict=False))
    return tuple(networks)


def resolve_client_host(
    peer: str | None,
    forwarded_for: str | None,
    trusted_networks: Iterable,
) -> str | None:
    """Resolve an originating client while rejecting spoofable forwarding data."""
    if not peer:
        return None
    try:
        peer_ip = ip_address(peer)
    except ValueError:
        return peer

    networks = tuple(trusted_networks)
    if not networks or not any(peer_ip in network for network in networks):
        # SESSİZ BOZULMAYI GÖRÜNÜR KIL. Buraya X-Forwarded-For BAŞLIĞIYLA
        # düşmek, önümüzde bir ters vekil olduğu ama onu TANIMADIĞIMIZ
        # anlamına gelir. Sonuç sessizdir: her isteğin "istemci IP'si" vekilin
        # kendi adresi olur. Giriş kilidi (kullanıcı, IP) ikilisine bağlı
        # olduğu için TÜM kullanıcılar tek IP'ye düşer; bir kişinin başarısız
        # denemeleri başkalarını da etkileyebilir ve IP başına sınırlar
        # anlamını yitirir.
        #
        # Davranışı DEĞİŞTİRMİYORUZ — güvenilmeyen bir başlığa itibar etmek
        # daha kötü olurdu (istemci kendi IP'sini uydurabilirdi). Yalnız
        # durumu bir kez logluyoruz ki yapılandırma hatası fark edilsin.
        _uyar_vekil_taninmiyor(peer, forwarded_for, networks)
        return peer
    if not forwarded_for:
        return peer

    chain = []
    for raw in forwarded_for.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            chain.append(ip_address(candidate))
        except ValueError:
            return peer

    for candidate in reversed(chain):
        if not any(candidate in network for network in networks):
            return str(candidate)
    return str(chain[0]) if chain else peer


def _uyar_vekil_taninmiyor(peer: str, forwarded_for: str | None, networks: tuple) -> None:
    global _MISCONFIG_WARNED
    if _MISCONFIG_WARNED or not forwarded_for:
        return
    _MISCONFIG_WARNED = True
    logger.warning(
        "X-Forwarded-For geldi ama bağlanan taraf (%s) TRUSTED_PROXY_CIDRS "
        "listesinde değil (%s). İstemci IP'si vekilin adresi olarak "
        "kaydediliyor; giriş kilidi ve IP başına sınırlar tüm kullanıcılar "
        "için ortaklaşıyor. Ters vekilin ağını bu listeye ekleyin.",
        peer,
        ", ".join(str(n) for n in networks) or "boş",
    )


def install_trusted_proxy_client_resolution(trusted_proxy_cidrs: str) -> None:
    """Make Starlette's request.client trusted-proxy aware for this process."""
    global _PATCH_INSTALLED, _TRUSTED_NETWORKS, _MISCONFIG_WARNED
    _TRUSTED_NETWORKS = parse_trusted_proxy_networks(trusted_proxy_cidrs)
    # Yapılandırma yeniden yüklendiyse uyarı hakkı tazelenir.
    _MISCONFIG_WARNED = False
    if _PATCH_INSTALLED:
        return

    def trusted_client(request: Request) -> Address | None:
        raw_client = request.scope.get("client")
        if not raw_client:
            return None
        peer, port = raw_client
        headers = Headers(scope=request.scope)
        resolved = resolve_client_host(
            str(peer), headers.get("x-forwarded-for"), _TRUSTED_NETWORKS
        )
        return Address(resolved or str(peer), int(port))

    Request.client = property(trusted_client)
    _PATCH_INSTALLED = True
