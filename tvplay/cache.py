"""Downloaded video cache under .local/cache with a size quota and LRU eviction."""
import secrets
import shutil
from pathlib import Path

from . import config, store
from .errors import PlaybackError
from .media import MediaFile

GB = 1_000_000_000


class Cache:
    def __init__(self, settings, reads):
        self.reads = reads
        self.quota = float(settings.get("cache_quota_gb") or 50) * GB
        self.headroom = float(settings.get("disk_headroom_gb") or 5) * GB
        self.max_file = float(settings.get("max_file_gb") or 30) * GB
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def path(name):
        return config.CACHE_DIR / name

    def remove_partials(self):
        for leftover in config.CACHE_DIR.glob("*.part"):
            leftover.unlink(missing_ok=True)

    def remove_incomplete(self):
        """Drop downloads that did not finish. Finished cache files are left for Resume."""
        self.remove_partials()
        for entry in store.query_all("SELECT * FROM cache_entries WHERE ready = 0"):
            self.remove(entry)
        if not config.CACHE_DIR.is_dir():
            return
        for path in config.CACHE_DIR.glob("*.headerprobe"):
            unlink(path)

    def lookup(self, channel_id, message_id):
        entry = store.query_one(
            "SELECT * FROM cache_entries WHERE channel_id = ? AND message_id = ?", (channel_id, message_id)
        )
        if entry is None or entry.get("ready", 1) == 0:
            return None
        source = self.path(entry["source_name"])
        if not source.is_file() or source.stat().st_size != entry["source_size"]:
            self.remove(entry)
            return None
        if entry["play_name"] and not self.path(entry["play_name"]).is_file():
            store.execute(
                "UPDATE cache_entries SET play_name = NULL, play_size = NULL, probe = NULL WHERE id = ?", (entry["id"],)
            )
            entry.update(play_name=None, play_size=None, probe=None)
        return entry

    def register(self, channel_id, message_id, source_name, size, mime, duration):
        now = store.now()
        entry_id = store.execute(
            """INSERT INTO cache_entries
               (channel_id, message_id, source_name, source_size, mime, duration, token, ready, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (channel_id, message_id, source_name, size, mime, duration, secrets.token_urlsafe(24), now, now),
        )
        return store.query_one("SELECT * FROM cache_entries WHERE id = ?", (entry_id,))

    def begin_stream(self, channel_id, message_id, source_name, size, mime, duration, probe):
        """Register a file the TV can read before the download finishes. ready stays 0 until it does."""
        now = store.now()
        entry_id = store.execute(
            """INSERT INTO cache_entries
               (channel_id, message_id, source_name, source_size, play_name, play_size, mime, duration, probe,
                token, ready, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (channel_id, message_id, source_name, size, source_name, size, mime, duration, probe,
             secrets.token_urlsafe(24), now, now),
        )
        return store.query_one("SELECT * FROM cache_entries WHERE id = ?", (entry_id,))

    def finish_stream(self, entry_id, duration=None):
        entry = store.query_one("SELECT * FROM cache_entries WHERE id = ?", (entry_id,))
        if entry is None:
            raise PlaybackError("The download disappeared before it could be saved.", "internal", True)
        path = self.path(entry["source_name"])
        actual = path.stat().st_size if path.is_file() else -1
        if actual != entry["source_size"]:
            raise PlaybackError("The downloaded file is incomplete. Try again.", "network", True)
        store.execute(
            "UPDATE cache_entries SET ready = 1, play_size = ?, duration = COALESCE(?, duration) WHERE id = ?",
            (actual, duration, entry_id),
        )
        return store.query_one("SELECT * FROM cache_entries WHERE id = ?", (entry_id,))

    def set_playable(self, entry_id, play_name, mime, probe, duration):
        play_size = self.path(play_name).stat().st_size
        store.execute(
            """UPDATE cache_entries SET play_name = ?, play_size = ?, mime = ?, probe = ?, duration = ?, ready = 1
               WHERE id = ?""",
            (play_name, play_size, mime, probe, duration, entry_id),
        )
        return store.query_one("SELECT * FROM cache_entries WHERE id = ?", (entry_id,))

    def touch(self, entry_id):
        store.execute("UPDATE cache_entries SET last_used_at = ? WHERE id = ?", (store.now(), entry_id))

    def resolve_token(self, token):
        """Called from media listener threads."""
        entry = store.query_one("SELECT id, play_name, mime FROM cache_entries WHERE token = ?", (token,))
        if entry is None or not entry["play_name"]:
            return None
        return MediaFile(key=entry["id"], path=self.path(entry["play_name"]), mime=entry["mime"] or "video/mp4")

    def remove(self, entry):
        names = {entry["source_name"], entry.get("play_name")}
        for name in filter(None, names):
            unlink(self.path(name))
        store.execute("DELETE FROM cache_entries WHERE id = ?", (entry["id"],))

    def used_bytes(self):
        return sum(
            self._entry_bytes(entry)
            for entry in store.query_all("SELECT source_name, source_size, play_name, play_size FROM cache_entries")
        )

    def ensure_space(self, needed, protected_ids=()):
        if needed > self.max_file:
            raise PlaybackError(
                f"This file is {needed / GB:.1f} GB, above the {self.max_file / GB:.0f} GB limit set on the PC.",
                "too_large",
            )
        protected = set(protected_ids)
        candidates = [
            entry
            for entry in store.query_all("SELECT * FROM cache_entries ORDER BY last_used_at")
            if entry["id"] not in protected and entry.get("ready", 1) != 0 and not self.reads.busy(entry["id"])
        ]
        used = self.used_bytes()
        free = shutil.disk_usage(config.CACHE_DIR).free
        victims = []
        for entry in candidates:
            if used + needed <= self.quota and free - needed >= self.headroom:
                break
            size = self._entry_bytes(entry)
            used -= size
            free += size
            victims.append(entry)
        if used + needed > self.quota or free - needed < self.headroom:
            raise PlaybackError(
                "Not enough disk space on the PC for this video, and no unused cached videos can be removed.",
                "disk_full",
            )
        for entry in victims:
            self.remove(entry)

    @staticmethod
    def _entry_bytes(entry):
        size = entry["source_size"] or 0
        if entry["play_name"] and entry["play_name"] != entry["source_name"]:
            size += entry["play_size"] or 0
        return size


def safe_name(channel_id, message_id, ext):
    return f"{channel_id}_{message_id}{ext}"


MIME_BY_EXT = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".flv": "video/x-flv",
}


def mime_for(ext, reported=None):
    return MIME_BY_EXT.get(ext) or (reported if reported and reported.startswith("video/") else "video/mp4")


def disk_full(exc):
    if not isinstance(exc, OSError):
        return False
    return exc.errno == 28 or getattr(exc, "winerror", None) in (39, 112)


def unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
