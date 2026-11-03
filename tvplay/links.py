"""Validation of catalogue Telegram links."""
import re

from .errors import PlaybackError

MESSAGE_LINK = re.compile(r"^https://t\.me/c/(\d{1,20})/(\d{1,12})$")


def parse_message_link(url):
    """Return (channel_id, message_id) for a private channel message link."""
    if not isinstance(url, str):
        raise PlaybackError("This title has no Telegram link.", "no_link")
    match = MESSAGE_LINK.match(url.strip())
    if match is None:
        raise PlaybackError(
            "This title's Telegram link is not a channel message link, so it cannot be downloaded.",
            "bad_link",
        )
    channel_id, message_id = int(match.group(1)), int(match.group(2))
    if channel_id <= 0 or message_id <= 0:
        raise PlaybackError("This title's Telegram link is invalid.", "bad_link")
    return channel_id, message_id
