#!/usr/bin/env python3
"""Validate local-only HTTP endpoints used by Voice Healer."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

LOCAL_HOSTNAMES = {"localhost"}


def _is_loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized in LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_local_http_url(url: str, *, label: str) -> str:
    """Return a URL only when it is plain HTTP and points to loopback."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid URL.") from exc

    if parsed.scheme.lower() != "http":
        raise ValueError(f"{label} must use http:// and a loopback host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain embedded credentials.")
    if not _is_loopback_hostname(hostname):
        raise ValueError(f"{label} must point to localhost, 127.0.0.1, or ::1.")
    return url


def validate_loopback_bind_host(host: str) -> str:
    """Return a web-server bind host only when it is loopback."""
    if _is_loopback_hostname(host):
        return host
    raise ValueError("UI server must bind to a loopback host (localhost, 127.0.0.1, or ::1).")
