"""Which address is used for what — the one place that decides.

  public  → everything people see: panel, sign-in/Allow links and their redirect URIs,
            invites, register links, landing, Privacy/Terms.
            Own domain when it is on (s1.drivecast.site), else the Tailscale link,
            else whatever address the request came in on.
  media   → files that Instagram fetches for publishing: the Tailscale link (big files
            stay off Cloudflare), else the public address.
  backup  → the owner's way in when the own domain is down: the Tailscale link.
"""
from . import db


def own_domain():
    return (db.meta_get("public_url_override") or "").rstrip("/")


def tailscale():
    u = (db.meta_get("public_url") or "").rstrip("/")
    return u if u.startswith("https://") else ""


def public(req=None):
    u = own_domain() or tailscale()
    if u:
        return u
    if req is None:
        return ""
    proto = req.headers.get("X-Forwarded-Proto") or ("https" if getattr(req, "secure", False) else "http")
    return f"{proto}://{req.headers.get('Host', 'localhost')}"


def media():
    return tailscale() or own_domain()


def backup():
    return tailscale()
