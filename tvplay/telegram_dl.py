"""Authenticated Telegram access for resolving and downloading catalogue messages."""
import re
from dataclasses import dataclass

from telethon import TelegramClient, errors
from telethon.tl.types import MessageMediaDocument, PeerChannel

from . import config
from .errors import PlaybackError

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".wmv", ".mpg", ".mpeg", ".flv"}
SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,5}$")

@dataclass
class VideoMessage:
    message: object
    name: str
    ext: str
    size: int
    mime: str
    duration: int | None


def is_video_document(message):
    if not isinstance(message.media, MessageMediaDocument) or message.file is None:
        return False
    mime = (message.file.mime_type or "").lower()
    ext = (message.file.ext or "").lower()
    return mime.startswith("video/") or ext in VIDEO_EXTENSIONS


class TelegramSource:
    """One persistent client, used only from the worker event loop."""

    def __init__(self, settings):
        self.settings = settings
        self.client = None
        self._dialogs_loaded = False
        self._entities = {}

    async def start(self):
        if self.client is not None and self.client.is_connected():
            return
        credentials = config.telegram_credentials(self.settings)
        if credentials is None:
            raise PlaybackError(
                "Telegram is not set up on the PC. Run scripts/telegram_login.py there first.", "telegram_setup"
            )
        api_id, api_hash = credentials
        self.client = TelegramClient(str(config.TELEGRAM_SESSION), api_id, api_hash, flood_sleep_threshold=0)
        try:
            await self.client.connect()
        except OSError as exc:
            self.client = None
            raise PlaybackError("Could not reach Telegram. Check the PC's internet connection.", "network", True) from exc
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            self.client = None
            raise PlaybackError(
                "The Telegram login on the PC has expired. Run scripts/telegram_login.py again.", "telegram_setup"
            )

    async def close(self):
        if self.client is not None:
            await self.client.disconnect()
            self.client = None

    async def _channel(self, channel_id):
        if channel_id in self._entities:
            return self._entities[channel_id]
        try:
            entity = await self.client.get_entity(PeerChannel(channel_id))
        except ValueError:
            if self._dialogs_loaded:
                raise PlaybackError("The Telegram account on the PC cannot see this channel.", "no_access")
            await self.client.get_dialogs()
            self._dialogs_loaded = True
            try:
                entity = await self.client.get_entity(PeerChannel(channel_id))
            except ValueError as exc:
                raise PlaybackError("The Telegram account on the PC cannot see this channel.", "no_access") from exc
        self._entities[channel_id] = entity
        return entity

    async def resolve(self, channel_id, message_id):
        await self.start()
        try:
            entity = await self._channel(channel_id)
            message = await self.client.get_messages(entity, ids=message_id)
        except (errors.ChannelPrivateError, errors.ChannelInvalidError) as exc:
            raise PlaybackError("The Telegram account on the PC has no access to this channel.", "no_access") from exc
        except errors.MessageIdInvalidError as exc:
            raise PlaybackError("This Telegram post no longer exists.", "missing") from exc
        if message is None:
            raise PlaybackError("This Telegram post no longer exists or was deleted.", "missing")
        if message.media is None:
            raise PlaybackError("This Telegram post has no attached file.", "not_video")
        if not is_video_document(message):
            hint = " It is part of an album; the linked item is not the video." if message.grouped_id else ""
            raise PlaybackError(f"This Telegram post does not contain a video file.{hint}", "not_video")
        file = message.file
        ext = (file.ext or "").lower()
        if not SAFE_EXT.match(ext):
            ext = ".mp4" if (file.mime_type or "") == "video/mp4" else ".mkv"
        return VideoMessage(
            message=message,
            name=file.name or f"{channel_id}-{message_id}{ext}",
            ext=ext,
            size=int(file.size or 0),
            mime=(file.mime_type or "").lower(),
            duration=int(file.duration) if file.duration else None,
        )

    async def download(self, video, destination, progress):
        """Download to destination. progress(done, total) is called from the event loop."""
        await self.start()
        await self.client.download_media(video.message, file=str(destination), progress_callback=progress)

    async def copy_range(self, video, offset, count, write):
        """Write exactly count bytes from offset by calling write(bytes). Returns the number written."""
        await self.start()
        if count <= 0:
            return 0
        written = 0
        stream = self.client.iter_download(
            video.message, offset=offset, request_size=512 * 1024, file_size=video.size or None
        )
        try:
            async for chunk in stream:
                data = bytes(chunk)
                if written + len(data) > count:
                    data = data[: count - written]
                if not data:
                    break
                write(data)
                written += len(data)
                if written >= count:
                    break
        finally:
            if getattr(stream, "_sender", None):
                await stream.close()
        return written
