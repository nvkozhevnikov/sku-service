"""Scheme, host and address checks for supplier-provided URLs.

Two concerns live here:
- links that end up in HTML or exports must be plain http(s);
- every URL a crawler is about to request (including each redirect hop) must
  stay on the supplier's hosts, on ports 80/443, and never reach private,
  loopback, link-local or otherwise reserved addresses.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit

HTTP_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})
UNSAFE_URL = "UNSAFE_URL"


class UnsafeUrlError(ValueError):
    """A crawl target failed URL validation; reported as UNSAFE_URL, never as a network error."""


def is_http_url(value: object) -> bool:
    """True only for absolute http(s) URLs with a host and no control characters."""
    text = str(value or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return parsed.scheme.lower() in HTTP_SCHEMES and bool(parsed.netloc)


def _forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
            or address.is_multicast or address.is_unspecified)


def validate_public_http_url(url: str, allowed_hosts: Iterable[str] | None = None, *,
                             resolve_dns: bool = False,
                             resolver: Callable[..., list] = socket.getaddrinfo) -> str:
    """Return ``url`` unchanged or raise UnsafeUrlError.

    ``allowed_hosts=None`` means "any public host" (used only for generic
    transports without a supplier binding); crawlers always pass their hosts.
    """
    text = str(url or "")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise UnsafeUrlError("URL поставщика недопустим")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise UnsafeUrlError("URL поставщика недопустим") from error
    if parsed.scheme.lower() not in HTTP_SCHEMES or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrlError("URL поставщика недопустим")
    host = parsed.hostname.rstrip(".").lower()
    if allowed_hosts is not None:
        allowed = {str(item).rstrip(".").lower() for item in allowed_hosts}
        if host not in allowed:
            raise UnsafeUrlError("Хост не разрешён адаптером поставщика")
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeUrlError("Разрешены только порты 80 и 443")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _forbidden_address(literal):
        raise UnsafeUrlError("Локальные и служебные адреса запрещены")
    if resolve_dns and literal is None:
        # A resolver failure is a network error (OSError), not an unsafe URL.
        infos = resolver(host, port or (443 if parsed.scheme.lower() == "https" else 80))
        for *_, sockaddr in infos:
            if _forbidden_address(ipaddress.ip_address(sockaddr[0])):
                raise UnsafeUrlError("DNS указывает на локальный или служебный адрес")
    return text


def host_validator(allowed_hosts: Iterable[str], *, resolve_dns: bool = True,
                   resolver: Callable[..., list] = socket.getaddrinfo) -> Callable[[str], str]:
    hosts = tuple(allowed_hosts)
    return lambda url: validate_public_http_url(url, hosts, resolve_dns=resolve_dns, resolver=resolver)


def error_type_name(error: BaseException) -> str:
    return UNSAFE_URL if isinstance(error, UnsafeUrlError) else type(error).__name__
