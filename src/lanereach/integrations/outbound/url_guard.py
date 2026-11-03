"""SSRF protection for URLs that tenants give LaneReach to call (webhooks, Slack).

A tenant must not be able to make LaneReach's servers call internal addresses (the database,
Redis, cloud metadata at 169.254.169.254, the Docker network). Every call therefore:

* requires `https` (plain `http` only when `INTEGRATIONS_ALLOW_PRIVATE_URLS` is on, for local
  development against a test receiver);
* resolves the host and refuses loopback, private, link-local, multicast, reserved and
  unspecified addresses, including IPv6 and IPv4-mapped forms;
* is checked again at delivery time (DNS can change after the URL was saved) and is sent without
  following redirects.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_URL_LENGTH = 2000


class UnsafeUrlError(ValueError):
    """The URL may not be called."""


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve a host name to its IP addresses (A and AAAA).

    Args:
        host: Host name.
        port: Port (part of the lookup).

    Returns:
        The addresses.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def check_url_shape(url: str, *, allow_private: bool = False) -> tuple[str, int]:
    """Check a URL without the network: scheme, host, no credentials.

    Args:
        url: The URL.
        allow_private: Development mode (allows `http`).

    Returns:
        Host and port.

    Raises:
        UnsafeUrlError: If the URL is not acceptable.
    """
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError("The URL is too long.")
    parts = urlsplit(url)
    allowed = ("https", "http") if allow_private else ("https",)
    if parts.scheme not in allowed:
        raise UnsafeUrlError("The URL must start with https://.")
    if not parts.hostname:
        raise UnsafeUrlError("The URL has no host.")
    if parts.username or parts.password:
        raise UnsafeUrlError("Put credentials in the signing secret, not in the URL.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("The URL has an invalid port.") from exc
    return parts.hostname, port


async def check_url_is_public(
    url: str, *, allow_private: bool = False, resolver: Resolver | None = None
) -> None:
    """Refuse URLs that point at internal addresses.

    Args:
        url: The URL.
        allow_private: Development mode: `http` and private addresses allowed.
        resolver: DNS resolver; defaults to `resolve_host` looked up at call time.

    Raises:
        UnsafeUrlError: If the URL is not acceptable.
    """
    resolver = resolver or resolve_host
    host, port = check_url_shape(url, allow_private=allow_private)
    if allow_private:
        return
    try:
        addresses = [host] if _is_ip(host) else await resolver(host, port)
    except OSError as exc:
        raise UnsafeUrlError(f"The host {host} cannot be resolved.") from exc
    if not addresses:
        raise UnsafeUrlError(f"The host {host} cannot be resolved.")
    if not all(_is_public(address) for address in addresses):
        raise UnsafeUrlError("The URL points at a private or internal address.")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
