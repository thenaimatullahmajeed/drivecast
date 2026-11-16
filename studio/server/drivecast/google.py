"""Google OAuth: consent links, code exchange and token refresh.

Tokens are kept as JSON sealed in the vault. ``TokenBox`` refreshes access
tokens through the same session (and therefore the same proxy) as the caller.
"""
import json
import time
from urllib.parse import urlencode

from . import security
from .net import AuthRevoked, Transient

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
]


def consent_url(client_id, redirect_uri, scopes, state):
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "false",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(q)


def _token_request(session, data):
    try:
        r = session.post(TOKEN_URL, data=data, timeout=(20, 40))
    except Exception as e:
        raise Transient("Could not reach Google", str(e))
    if r.status_code == 400 and "invalid_grant" in r.text:
        raise AuthRevoked("Google access was removed or expired. Reconnect this account.")
    if r.status_code in (400, 401):
        raise AuthRevoked("Google rejected the app settings or token.", r.text[:300])
    if r.status_code >= 500:
        raise Transient("Google sign-in service is busy", r.text[:300])
    r.raise_for_status()
    return r.json()


def exchange(session, client_id, client_secret, code, redirect_uri):
    tok = _token_request(session, {
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if not tok.get("refresh_token"):
        raise AuthRevoked("Google did not return offline access. Remove the app from the Google account and try again.")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    return tok


class TokenBox:
    """Holds one sealed token; ``access(session)`` returns a fresh access token
    and calls ``save(sealed)`` whenever it refreshed."""

    def __init__(self, sealed, client_id, client_secret, save):
        self.tok = json.loads(security.unseal(sealed)) if sealed else None
        self.client_id, self.client_secret, self.save = client_id, client_secret, save

    def access(self, session):
        if not self.tok:
            raise AuthRevoked("Not connected to Google.")
        if self.tok.get("expires_at", 0) - 120 > time.time():
            return self.tok["access_token"]
        new = _token_request(session, {
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.tok["refresh_token"], "grant_type": "refresh_token",
        })
        self.tok["access_token"] = new["access_token"]
        self.tok["expires_at"] = time.time() + int(new.get("expires_in", 3600))
        if new.get("refresh_token"):
            self.tok["refresh_token"] = new["refresh_token"]
        self.save(security.seal(json.dumps(self.tok)))
        return self.tok["access_token"]

    def force_expire(self):
        if self.tok:
            self.tok["expires_at"] = 0


def authed(session, box, method, url, **kw):
    """Make one Google API call, refreshing the token once on HTTP 401."""
    kw.setdefault("timeout", (20, 120))
    for attempt in (1, 2):
        headers = dict(kw.pop("headers", {}) or {})
        headers["Authorization"] = "Bearer " + box.access(session)
        r = session.request(method, url, headers=headers, **kw)
        if r.status_code == 401 and attempt == 1:
            box.force_expire()
            kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
            continue
        if r.status_code == 401:
            raise AuthRevoked("Google refused the saved access. Reconnect this account.")
        return r
    return r
