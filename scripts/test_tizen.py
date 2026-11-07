"""Isolated backend tests; no Telegram connection, TV, or production state."""
import asyncio
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from tvplay import config, store
from tvplay.jobs import JobManager
from tvplay.errors import PlaybackError


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(config, LOCAL=Path(self.temp.name), JOBS_DB=Path(self.temp.name) / 'jobs.sqlite')
        self.paths.start()
        store.init()
        self.manager = JobManager.__new__(JobManager)
        self.job = dict(id='a' * 32, movie_id=1, title='Test', renderer_id='tizen:192.168.0.50',
                        state='starting', detail='', updated_at=0, position=None, duration=1000,
                        media_url='http://pc/media/private/movie.mp4', cache_id=1)
        self.manager.jobs = {self.job['id']: self.job}

    async def asyncTearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    async def test_resume_and_completion(self):
        await self.manager.app_progress(self.job['id'], dict(state='paused', position=123, duration=1000))
        self.assertEqual((await self.manager.resume_point(1))['position'], 123)
        await self.manager.app_progress(self.job['id'], dict(state='ended', position=1000, duration=1000))
        self.assertIsNone(await self.manager.resume_point(1))
        self.assertEqual(self.job['state'], 'ended')
        await self.manager.app_progress(self.job['id'], dict(state='playing', position=200))
        self.assertEqual(self.job['state'], 'ended')

    async def test_reject_invalid_progress(self):
        for value in (-1, float('nan'), float('inf'), True, '12'):
            with self.assertRaises(PlaybackError):
                await self.manager.app_progress(self.job['id'], dict(state='playing', position=value))

    async def test_public_snapshot_hides_media_capability(self):
        self.assertNotIn('media_url', await self.manager.get(self.job['id']))
        self.assertIn('media_url', await self.manager.app_status(self.job['id']))

    async def test_dlna_ready_path_unchanged(self):
        self.job['renderer_id'] = 'uuid:tv'
        self.manager._prepare = AsyncMock(return_value={'id': 1})
        self.manager._start_tv = AsyncMock(return_value=('renderer', 'url'))
        self.manager._monitor = AsyncMock()
        await self.manager._play_ready(self.job, {})
        self.manager._monitor.assert_awaited_once_with(self.job, 'renderer', 'url')

    async def test_app_ready_never_starts_dlna_and_times_out(self):
        self.manager.settings = {'media_host': '192.168.0.20'}
        self.manager.media = Mock()
        self.manager.media.url_for.return_value = 'http://pc/media/video.mp4'
        self.manager._prepare = AsyncMock(return_value=dict(token='secret', play_name='film.mp4', duration=1000))
        self.manager._start_tv = AsyncMock()
        async def expire(_):
            self.job['_heartbeat'] = 0
        with patch('tvplay.jobs.asyncio.sleep', expire):
            await self.manager._play_ready(self.job, {})
        self.assertEqual(self.job['state'], 'stopped')
        self.manager._start_tv.assert_not_awaited()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / '.local').mkdir()
        (root / '.local/tizen-token').write_text('a' * 32)
        self.root_patch = patch.object(server, 'ROOT', root)
        self.root_patch.start()
        self.manager = Mock()
        self.manager.get.return_value = dict(renderer={'id': 'tizen:127.0.0.1'})
        self.manager.app_status.return_value = dict(id='b' * 32, media_url='http://pc/media/secret')
        service = Mock(manager=self.manager)
        service.call.side_effect = lambda value: value
        class Handler(server.Handler):
            tv = service
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.root_patch.stop()
        self.temp.cleanup()

    def post(self, token='a' * 32):
        req = Request('http://127.0.0.1:%d/api/tizen/%s/status' % (self.http.server_port, 'b' * 32),
                      data=b'{}', headers={'Content-Type':'application/json', 'X-Movie-World-Token':token})
        return urlopen(req)

    def test_token_and_ownership(self):
        with self.assertRaises(HTTPError) as error:
            self.post('wrong')
        self.assertEqual(error.exception.code, 401)
        self.manager.get.assert_not_called()
        with self.post() as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            self.assertIn('media_url', json.load(response)['job'])
        self.manager.get.return_value = dict(renderer={'id':'tizen:192.168.0.99'})
        with self.assertRaises(HTTPError) as error:
            self.post()
        self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
