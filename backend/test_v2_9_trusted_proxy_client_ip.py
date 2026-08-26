from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.client_ip import parse_trusted_proxy_networks, resolve_client_host
from app.config import Settings


def test_direct_client_cannot_spoof_forwarded_header() -> None:
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")

    assert resolve_client_host("203.0.113.10", "198.51.100.25", trusted) == "203.0.113.10"


def test_trusted_proxy_uses_forwarded_origin() -> None:
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")

    assert resolve_client_host("10.1.2.3", "198.51.100.25", trusted) == "198.51.100.25"


def test_trusted_proxy_chain_removes_only_trusted_hops_from_right() -> None:
    trusted = parse_trusted_proxy_networks("10.0.0.0/8,192.168.0.0/16")

    assert (
        resolve_client_host(
            "10.1.2.3",
            "198.51.100.25, 192.168.1.7, 10.9.8.7",
            trusted,
        )
        == "198.51.100.25"
    )


def test_malformed_forwarding_chain_falls_back_to_socket_peer() -> None:
    trusted = parse_trusted_proxy_networks("10.0.0.0/8")

    assert resolve_client_host("10.1.2.3", "198.51.100.25, invalid", trusted) == "10.1.2.3"


def test_invalid_trusted_proxy_configuration_fails_fast() -> None:
    with pytest.raises(ValidationError):
        Settings(trusted_proxy_cidrs="not-a-network")
