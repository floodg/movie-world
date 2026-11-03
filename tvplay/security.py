"""Phone pairing: a local pairing code exchanged for an HttpOnly session cookie plus a CSRF token."""
import hashlib
import hmac
import secrets
import threading
import time

from . import store

COOKIE = "mw_tv"
SESSION_DAYS = 180
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def _hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class PairingLocked(Exception):
    def __init__(self, seconds):
        super().__init__("locked")
        self.seconds = seconds


class Pairing:
    def __init__(self, code):
        self.code = code
        self._lock = threading.Lock()
        self._failures = 0
        self._locked_until = 0.0

    def pair(self, code):
        """Return (token, csrf) for a correct code, None for a wrong one."""
        with self._lock:
            now = time.time()
            if now < self._locked_until:
                raise PairingLocked(int(self._locked_until - now) + 1)
            if not isinstance(code, str) or not hmac.compare_digest(code.strip().encode(), self.code.encode()):
                self._failures += 1
                if self._failures >= MAX_FAILURES:
                    self._failures = 0
                    self._locked_until = now + LOCKOUT_SECONDS
                return None
            self._failures = 0
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = store.now()
        store.execute("DELETE FROM sessions WHERE last_seen < ?", (now - SESSION_DAYS * 86400,))
        store.execute(
            "INSERT INTO sessions (token_hash, csrf, created_at, last_seen) VALUES (?, ?, ?, ?)",
            (_hash(token), csrf, now, now),
        )
        return token, csrf

    def session(self, token):
        """Return the CSRF token for a valid session cookie, else None."""
        if not token or len(token) > 100:
            return None
        row = store.query_one("SELECT csrf, last_seen FROM sessions WHERE token_hash = ?", (_hash(token),))
        if row is None or row["last_seen"] < store.now() - SESSION_DAYS * 86400:
            return None
        if store.now() - row["last_seen"] > 3600:
            store.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (store.now(), _hash(token)))
        return row["csrf"]

    @staticmethod
    def check_csrf(expected, supplied):
        return bool(expected) and isinstance(supplied, str) and hmac.compare_digest(expected.encode(), supplied.encode())

    @staticmethod
    def cookie(token, secure):
        parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={SESSION_DAYS * 86400}"]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)
