"""Cached-only requests must never start a Telegram download."""
import asyncio
import sys
import tempfile
import unittest
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvplay import config, store
from tvplay.cache import Cache
from tvplay.errors import PlaybackError
from tvplay.jobs import JobManager
from tvplay.media import ActiveReads
import server


class CachedPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, LOCAL=root, JOBS_DB=root / 'jobs.sqlite', CACHE_DIR=root / 'cache')
        self.paths.start()
        store.init()
        self.manager = JobManager.__new__(JobManager)
        self.manager.cache = Cache({}, ActiveReads())
        self.manager.jobs = {}
        self.manager.tasks = {}
        self.manager.uploads = {}
        self.manager._play_ready = AsyncMock()
        self.manager._download_and_play = AsyncMock()
        self.manager._cleanup_job_files = AsyncMock()
        self.movie = dict(id=1, title='Movie', telegram_url='https://t.me/c/123/456')
        store.execute("INSERT INTO renderers(id,name,location,last_seen) VALUES(?,?,?,?)",
                      ('uuid:tv', 'TV', 'http://192.168.1.2/description.xml', store.now()))

    async def asyncTearDown(self):
        for task in list(self.manager.tasks.values()):
            await task
        self.paths.stop()
        self.temp.cleanup()

    def cached(self):
        self.manager.cache.path('movie.mp4').write_bytes(b'video')
        return self.manager.cache.register(123, 456, 'movie.mp4', 5, 'video/mp4', 1000)

    async def test_miss_creates_no_job_or_download(self):
        self.assertIsNone(await self.manager.cached_movie(self.movie))
        with self.assertRaises(PlaybackError) as raised:
            await self.manager.create(self.movie, 'uuid:tv', cached_only=True)
        self.assertEqual(raised.exception.code, 'cache_miss')
        self.assertFalse(self.manager.jobs)
        self.manager._download_and_play.assert_not_awaited()

    async def test_hit_uses_cache_and_pc_resume_and_protects_file(self):
        entry = self.cached()
        await self.manager.mirror_resume_points([dict(movie_id=1, position=400, duration=1000, updated_at=store.now())])
        result, created = await self.manager.create(self.movie, 'uuid:tv', cached_only=True)
        self.assertTrue(created)
        self.assertEqual(result['start_at'], 400)
        self.assertIn(entry['id'], self.manager._protected_ids())
        await self.manager.tasks[result['id']]
        self.manager._play_ready.assert_awaited_once()
        self.manager._download_and_play.assert_not_awaited()

    async def test_explicit_start_over_wins(self):
        self.cached()
        await self.manager.mirror_resume_points([dict(movie_id=1, position=400, duration=1000, updated_at=store.now())])
        result, _ = await self.manager.create(self.movie, 'uuid:tv', start_at=0, cached_only=True)
        self.assertEqual(result['start_at'], 0)

    async def test_disappearing_cache_never_downloads(self):
        entry = self.cached()
        result, _ = await self.manager.create(self.movie, 'uuid:tv', cached_only=True)
        self.manager.cache.path(entry['source_name']).unlink()
        await self.manager.tasks[result['id']]
        self.assertEqual(self.manager.jobs[result['id']]['error_code'], 'cache_miss')
        self.manager._download_and_play.assert_not_awaited()

    async def test_incomplete_cache_is_a_miss(self):
        self.manager.cache.begin_stream(123, 456, 'partial.mp4', 5, 'video/mp4', None, None)
        self.assertIsNone(await self.manager.cached_movie(self.movie))

    async def test_normal_request_still_downloads(self):
        result, _ = await self.manager.create(self.movie, 'uuid:tv')
        await self.manager.tasks[result['id']]
        self.manager._download_and_play.assert_awaited_once()


class CachedApiTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        service = Mock(manager=self.manager)
        service.call.side_effect = lambda value: value
        class Handler(server.Handler):
            tv = service
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.movie = dict(id=1, title='Movie', telegram_url='https://t.me/c/123/456')
        self.detail = patch.object(server, 'movie_detail', return_value=self.movie)
        self.detail.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.detail.stop()

    def request(self, path, body=None):
        req = Request(f'http://127.0.0.1:{self.http.server_port}{path}',
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req) as response:
            return json.load(response)

    def test_cache_check_hides_file_details_and_never_starts_job(self):
        self.manager.cached_movie.return_value = {'token': 'private', 'source_name': 'secret.mp4'}
        self.assertEqual(self.request('/api/playback/cache/1'), {'cached': True})
        self.manager.create.assert_not_called()
        self.manager.cached_movie.return_value = None
        self.assertEqual(self.request('/api/playback/cache/1'), {'cached': False})

    def test_unknown_movie_is_cache_miss(self):
        self.detail.stop()
        self.detail = patch.object(server, 'movie_detail', return_value=None)
        self.detail.start()
        self.assertEqual(self.request('/api/playback/cache/999'), {'cached': False})
        self.manager.cached_movie.assert_not_called()

    def test_android_post_passes_cache_only_guard(self):
        self.manager.create.return_value = ({'id': 'abc'}, True)
        self.assertEqual(self.request('/api/playback', dict(movie_id=1, renderer_id='uuid:tv',
                         cached_only=True, start_at=400, idempotency_key='android-key')), {'job': {'id': 'abc'}})
        self.manager.create.assert_called_once_with(self.movie, 'uuid:tv', 'android-key', False, 400, cached_only=True, subtitle_srt=None)

    def test_android_subtitle_upload_larger_than_normal_json_limit(self):
        srt = '1\n00:00:01,000 --> 00:00:03,000\n' + 'Caption text ' * 1000 + '\n'
        self.manager.create.return_value = ({'id': 'abc'}, True)
        self.request('/api/playback', dict(movie_id=1, renderer_id='uuid:tv', cached_only=True, subtitle_srt=srt))
        self.assertEqual(self.manager.create.call_args.kwargs['subtitle_srt'], srt)


if __name__ == '__main__':
    unittest.main()
