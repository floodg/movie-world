"""Bounded, inline SRT supplied by a playback client; never accept file paths or URLs."""
import re

from .errors import PlaybackError

MAX_SRT_BYTES = 2 * 1024 * 1024
TIMING = re.compile(r"^\d{2,}:\d{2}:\d{2},\d{3} --> \d{2,}:\d{2}:\d{2},\d{3}(?:\s.*)?$", re.M)


def validate_srt(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_SRT_BYTES:
        raise PlaybackError("Send an SRT subtitle file up to 2 MB.", "bad_subtitle")
    value = value.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    try:
        valid = len(value.encode("utf-8")) <= MAX_SRT_BYTES
    except UnicodeEncodeError:
        valid = False
    if not valid or "\x00" in value or not TIMING.search(value):
        raise PlaybackError("The subtitles are not a valid UTF-8 SRT file.", "bad_subtitle")
    return value
