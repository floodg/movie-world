"""Paths and settings for Play on TV. Settings live in .local/tvplay.json."""
import json
import os
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
SETTINGS_FILE = LOCAL / "tvplay.json"
TELEGRAM_SESSION = LOCAL / "telegram"
JOBS_DB = LOCAL / "jobs.sqlite"
CACHE_DIR = LOCAL / "cache"
CATALOGUE_DB = ROOT / "sql-lite" / "movie-world.sqlite"

DEFAULTS = {
    "telegram_api_id": None,
    "telegram_api_hash": None,
    "media_port": 8766,
    "media_host": None,
    "renderer_urls": [],
    "cache_quota_gb": 50,
    "disk_headroom_gb": 5,
    "max_file_gb": 30,
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "pairing_code": None,
}


def load():
    settings = dict(DEFAULTS)
    if SETTINGS_FILE.is_file():
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise SystemExit(f"{SETTINGS_FILE} must contain a JSON object")
        settings.update(stored)
    if os.environ.get("TELEGRAM_API_ID"):
        settings["telegram_api_id"] = os.environ["TELEGRAM_API_ID"]
    if os.environ.get("TELEGRAM_API_HASH"):
        settings["telegram_api_hash"] = os.environ["TELEGRAM_API_HASH"]
    return settings


def save(settings):
    LOCAL.mkdir(exist_ok=True)
    stored = {key: value for key, value in settings.items() if key in DEFAULTS}
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


def ensure_pairing_code(settings):
    code = settings.get("pairing_code")
    if isinstance(code, str) and code.isdigit() and len(code) >= 6:
        return code
    settings["pairing_code"] = f"{secrets.randbelow(10**8):08d}"
    save(settings)
    return settings["pairing_code"]


def log_playback(line):
    """Append one line to .local/playback.log. The TV task's console is not visible from here."""
    try:
        LOCAL.mkdir(exist_ok=True)
        with (LOCAL / "playback.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        pass


def telegram_credentials(settings):
    api_id = settings.get("telegram_api_id")
    api_hash = settings.get("telegram_api_hash")
    if not api_id or not api_hash:
        return None
    return int(api_id), str(api_hash)
