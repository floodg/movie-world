"""Offline regression tests for catalogue links pointing at album posters."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon.tl.types import MessageMediaDocument
from tvplay.errors import PlaybackError
from tvplay.telegram_dl import TelegramSource


def post(number, album=7, video=False):
    return SimpleNamespace(
        id=number, grouped_id=album,
        media=MessageMediaDocument() if video else object(),
        file=SimpleNamespace(name="movie.mp4", ext=".mp4", mime_type="video/mp4",
                             size=1234, duration=7200) if video else None,
    )


class ResolveTests(unittest.IsolatedAsyncioTestCase):
    def source(self, original, following=()):
        source = TelegramSource({})
        source.start = AsyncMock()
        source._channel = AsyncMock(return_value="channel")
        source.client = SimpleNamespace(get_messages=AsyncMock(side_effect=[original, following]))
        return source

    async def test_direct_video_needs_no_search(self):
        source = self.source(post(10, video=True))
        self.assertEqual((await source.resolve(1, 10)).message.id, 10)
        self.assertEqual(source.client.get_messages.await_count, 1)

    async def test_skips_multiple_posters_and_missing_items(self):
        source = self.source(post(10), [post(14, video=True), None, post(11)])
        result = await source.resolve(1, 10)
        self.assertEqual(result.message.id, 14)
        self.assertEqual(result.size, 1234)

    async def test_does_not_cross_into_another_movie(self):
        source = self.source(post(10), [post(11), post(12, album=8, video=True)])
        with self.assertRaises(PlaybackError) as caught:
            await source.resolve(1, 10)
        self.assertEqual(caught.exception.code, "not_video")

    async def test_unrelated_video_without_album_is_not_used(self):
        source = self.source(post(10, album=None), [post(11, video=True)])
        with self.assertRaises(PlaybackError):
            await source.resolve(1, 10)
        self.assertEqual(source.client.get_messages.await_count, 1)

    async def test_missing_link_remains_missing(self):
        source = self.source(None)
        with self.assertRaises(PlaybackError) as caught:
            await source.resolve(1, 10)
        self.assertEqual(caught.exception.code, "missing")


if __name__ == "__main__":
    unittest.main()
