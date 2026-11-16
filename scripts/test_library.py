"""Queue recovery, cache reuse, and sharing an active streaming download."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvplay import config, store
from tvplay.cache import Cache
from tvplay.library import Library
from tvplay.media import ActiveReads

SRT = "1\n00:00:00,000 --> 00:00:02,000\nHello.\n"
MOVIE = dict(id=1, title="Test", telegram_url="https://t.me/c/123/456")


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, LOCAL=root, JOBS_DB=root/'jobs.sqlite', CACHE_DIR=root/'cache')
        self.paths.start()
        store.init()
        self.manager = SimpleNamespace(cache=Cache({}, ActiveReads()), jobs={}, uploads={}, settings={'auto_whisper': True}, telegram=Mock())
        self.library = Library(self.manager)
        self.manager._protected_ids = lambda: self.library.protected
        self.library.start()
        await self.library.close()

    async def asyncTearDown(self):
        await self.library.close()
        self.paths.stop()
        self.temp.cleanup()

    def cached(self):
        self.manager.cache.path('test.mp4').write_bytes(b'video')
        return self.manager.cache.register(123, 456, 'test.mp4', 5, 'video/mp4', 10)

    async def test_deduplicates_and_recovers_interrupted_queue(self):
        self.library.enqueue(MOVIE)
        self.library.enqueue(MOVIE)
        self.assertEqual(len(store.query_all('SELECT * FROM library_queue')), 1)
        self.library.state(1, 'transcribing')
        self.library.start()
        self.assertEqual(self.library.status(1)['state'], 'queued')

    async def test_reuses_complete_cache_and_existing_captions(self):
        self.cached()
        self.library.caption_path(MOVIE).write_text(SRT)
        await self.library.prepare(MOVIE)
        self.manager.telegram.resolve.assert_not_called()
        self.assertFalse(self.library.protected)

    async def test_waits_for_active_stream_instead_of_downloading_twice(self):
        self.manager.jobs['playing'] = dict(state='playing', telegram_url=MOVIE['telegram_url'])
        self.library.caption_path(MOVIE).write_text(SRT)
        task = asyncio.create_task(self.library.prepare(MOVIE))
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        self.cached()
        await asyncio.wait_for(task, 3)
        self.manager.telegram.resolve.assert_not_called()

    async def test_download_is_protected_and_complete_before_whisper(self):
        self.manager.telegram.resolve = AsyncMock(return_value=SimpleNamespace(size=5, ext='.mp4', mime='video/mp4', duration=10))
        async def download(video, path, progress):
            self.assertIn(MOVIE['telegram_url'], self.library.downloading)
            self.assertTrue(self.library.protected)
            path.write_bytes(b'video')
        self.manager.telegram.download = download
        process = Mock(returncode=0)
        async def communicate():
            self.assertIsNotNone(self.manager.cache.lookup(123, 456))
            self.library.caption_path(MOVIE).write_text(SRT)
            return b'', b''
        process.communicate = communicate
        self.library.enqueue(MOVIE)
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
            await self.library.prepare(MOVIE)
        self.assertFalse(self.library.downloading)
        self.assertFalse(self.library.protected)
        self.assertEqual(self.library.caption(MOVIE), SRT)

    async def test_failed_download_removes_partial_and_releases_waiters(self):
        self.manager.telegram.resolve = AsyncMock(return_value=SimpleNamespace(size=5, ext='.mp4', mime='video/mp4', duration=10))
        async def fail(video, path, progress):
            path.write_bytes(b'x')
            raise OSError('offline')
        self.manager.telegram.download = fail
        with self.assertRaises(OSError):
            await self.library.prepare(MOVIE)
        self.assertFalse(self.library.downloading)
        self.assertFalse(self.library.protected)
        self.assertEqual(store.query_all('SELECT * FROM cache_entries'), [])

    async def test_disabled_whisper_keeps_download_and_does_not_start_inference(self):
        self.cached()
        self.manager.settings['auto_whisper'] = False
        with patch('asyncio.create_subprocess_exec', AsyncMock()) as process:
            await self.library.prepare(MOVIE)
        process.assert_not_awaited()
        self.assertIsNotNone(self.manager.cache.lookup(123,456))

    async def test_setting_persists_and_stops_running_whisper(self):
        process = Mock(returncode=None)
        self.library.process = process
        with patch('tvplay.config.save') as save:
            result = await self.library.whisper_settings(False)
        self.assertEqual(result, {'auto_whisper':False})
        self.assertFalse(save.call_args.args[0]['auto_whisper'])
        process.kill.assert_called_once()
        self.assertFalse(self.library.whisper_enabled())

    async def test_disabling_during_spawn_kills_worker_and_preserves_download(self):
        entry = self.cached()
        spawning, release = asyncio.Event(), asyncio.Event()
        process = Mock(returncode=None, communicate=AsyncMock(), wait=AsyncMock())

        async def spawn(*args, **kwargs):
            spawning.set()
            await release.wait()
            return process

        with patch('asyncio.create_subprocess_exec', side_effect=spawn), patch('tvplay.config.save'):
            task = asyncio.create_task(self.library.prepare(MOVIE))
            try:
                await asyncio.wait_for(spawning.wait(), 2)
                self.assertIsNone(self.library.process)
                await self.library.whisper_settings(False)
                release.set()
                await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
        process.communicate.assert_not_awaited()
        self.assertIsNone(self.library.process)
        self.assertFalse(self.library.protected)
        self.assertEqual(self.manager.cache.lookup(123, 456)['id'], entry['id'])


if __name__ == '__main__':
    unittest.main()
