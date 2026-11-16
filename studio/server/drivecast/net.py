"""HTTP sessions bound to an IP group's SOCKS5 proxy, and the error types the
worker uses to decide what is safe to retry."""
import time
from urllib.parse import quote

import requests

from . import security

TIMEOUT = (20, 120)
IP_ENDPOINTS = ("https://api.ipify.org?format=json", "https://www.cloudflare.com/cdn-cgi/trace")


class DriveCastError(Exception):
    """Base error. ``user_message`` is safe to show in the panel."""

    def __init__(self, user_message, detail=None):
        super().__init__(detail or user_message)
        self.user_message = user_message


class ProxyDown(DriveCastError):
    """The group proxy cannot be reached or its IP changed. Never fall back."""


class AuthRevoked(DriveCastError):
    """Google refused the saved token. The owner must reconnect."""


class QuotaExceeded(DriveCastError):
    """Daily API/upload quota used up. Wait for the reset."""


class Transient(DriveCastError):
    """Temporary problem before anything was published. Safe to try later."""


class Permanent(DriveCastError):
    """This file or request will never work as is."""


class Uncertain(DriveCastError):
    """The video may or may not exist on the platform. Never retry blindly."""


def proxy_url(group):
    user = group.get("username") or ""
    pw = security.unseal(group.get("password_enc")) if group.get("password_enc") else ""
    auth = f"{quote(user, safe='')}:{quote(pw or '', safe='')}@" if user else ""
    # socks5h: DNS is resolved by the proxy too, so the real IP never leaks.
    return f"socks5h://{auth}{group['host']}:{int(group['port'])}"


def session_for(group=None):
    s = requests.Session()
    s.trust_env = False  # never pick up a system proxy by accident
    if group is not None:
        url = proxy_url(group)
        s.proxies = {"http": url, "https": url}
    s.headers["User-Agent"] = "DriveCast/1.0"
    return s


def outgoing_ip(session):
    """Return (ip, latency_ms) as seen by the internet through this session."""
    last = None
    for url in IP_ENDPOINTS:
        t0 = time.monotonic()
        try:
            r = session.get(url, timeout=(15, 20))
            r.raise_for_status()
            ms = int((time.monotonic() - t0) * 1000)
            if url.endswith("json"):
                return r.json()["ip"], ms
            for line in r.text.splitlines():
                if line.startswith("ip="):
                    return line[3:].strip(), ms
        except Exception as e:  # try the next endpoint
            last = e
    raise ProxyDown("The proxy did not respond", str(last))


def check_group(group):
    """Connect through the group proxy and report its outgoing IP."""
    try:
        return outgoing_ip(session_for(group))
    except ProxyDown:
        raise
    except Exception as e:
        raise ProxyDown("The proxy did not respond", str(e))
