"""Accounts: password hashing, session tokens and a login rate limiter. Standard library only."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

SESSION_COOKIE = "studio_session"
SESSION_DAYS = 30
MIN_PASSWORD = 10
MAX_PASSWORD = 200
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
ROLES = ("admin", "customer")
# scrypt cost: about 50 ms and 32 MB per hash on a laptop CPU.
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def normalize_email(email):
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def check_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise ValueError("Use a shorter password.")


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


# Equalizes timing for unknown emails, so responses do not reveal which accounts exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """At most `limit` failures per key within `window` seconds, kept in memory."""

    def __init__(self, limit=8, window=15 * 60):
        self.limit, self.window = limit, window
        self.failures = {}
        self.lock = threading.Lock()

    def _recent(self, key, now):
        return [moment for moment in self.failures.get(key, []) if now - moment < self.window]

    def blocked(self, *keys):
        now = time.monotonic()
        with self.lock:
            return any(len(self._recent(key, now)) >= self.limit for key in keys)

    def fail(self, *keys):
        now = time.monotonic()
        with self.lock:
            for key in keys:
                self.failures[key] = self._recent(key, now) + [now]

    def reset(self, *keys):
        with self.lock:
            for key in keys:
                self.failures.pop(key, None)
