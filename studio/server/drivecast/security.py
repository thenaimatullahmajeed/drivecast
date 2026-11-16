"""Password hashing, random codes and an encrypted vault for tokens and secrets."""
import base64
import hashlib
import hmac
import os
import secrets

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # on Android without the wheel, use the platform's own AES-GCM
    class AESGCM:
        def __init__(self, key):
            from java import jarray, jbyte, jclass  # Chaquopy bridge
            self._jarray, self._jbyte = jarray, jbyte
            self._Cipher = jclass("javax.crypto.Cipher")
            self._Spec = jclass("javax.crypto.spec.SecretKeySpec")
            self._Gcm = jclass("javax.crypto.spec.GCMParameterSpec")
            self._key = self._Spec(self._j(key), "AES")

        def _j(self, b):
            return self._jarray(self._jbyte)([x - 256 if x > 127 else x for x in b])

        def _run(self, mode, nonce, data, aad):
            c = self._Cipher.getInstance("AES/GCM/NoPadding")
            c.init(mode, self._key, self._Gcm(128, self._j(nonce)))
            c.updateAAD(self._j(aad))
            return bytes((x + 256) % 256 for x in c.doFinal(self._j(data)))

        def encrypt(self, nonce, data, aad):
            return self._run(1, nonce, data, aad)

        def decrypt(self, nonce, data, aad):
            return self._run(2, nonce, data, aad)

_key = None
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def load_key(path):
    """Create or read the 32-byte master key kept beside the database."""
    global _key
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) != 32:
            raise RuntimeError("master key file is damaged")
        _key = raw
        return
    raw = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    _key = raw


def seal(text):
    if text is None:
        return None
    nonce = os.urandom(12)
    ct = AESGCM(_key).encrypt(nonce, text.encode("utf-8"), b"drivecast-v1")
    return base64.b64encode(nonce + ct).decode("ascii")


def unseal(blob):
    if not blob:
        return None
    raw = base64.b64decode(blob)
    return AESGCM(_key).decrypt(raw[:12], raw[12:], b"drivecast-v1").decode("utf-8")


def hash_password(pw):
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(pw, stored):
    try:
        _, s, h = stored.split("$")
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(s), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def token_hash(tok):
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def code(prefix="DC"):
    part = lambda: "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))  # noqa: E731
    return f"{prefix}-{part()}-{part()}"
