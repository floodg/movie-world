"""Phone captions travel through PC metadata and HTTP without copying the movie."""
import asyncio
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvplay import config, store
from tvplay.cache import Cache
from tvplay.errors import PlaybackError
from tvplay.jobs import JobManager
from tvplay.media import ActiveReads, MediaServer
from tvplay.subtitles import validate_srt

SRT = '1\n00:00:01,500 --> 00:00:03,500\nHello & goodbye.\n'


class ValidationTests(unittest.TestCase):
    def test_off_and_normalized_unicode(self):
        self.assertIsNone(validate_srt(None))
        self.assertEqual(validate_srt('\ufeff' + SRT.replace('\n', '\r\n')), SRT)

    def test_rejects_paths_urls_malformed_and_oversized_payloads(self):
        for value in ['C:/secret.srt', 'http://other/subtitles', '', {}, SRT + '\0', SRT + 'x' * (2 * 1024 * 1024)]:
            with self.subTest(value_type=type(value)), self.assertRaises(PlaybackError):
                validate_srt(value)


class CaptionPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, LOCAL=root, JOBS_DB=root/'jobs.sqlite', CACHE_DIR=root/'cache')
        self.paths.start()
        store.init()
        self.manager = JobManager.__new__(JobManager)
        m = self.manager
        m.cache = Cache({}, ActiveReads())
        m.jobs, m.tasks, m.uploads, m.growing = {}, {}, {}, {}
        m.settings = {'media_host': '127.0.0.1'}
        m.registry = Mock()
        self.renderer = Mock(host='127.0.0.1')
        self.renderer.pick_mime = AsyncMock(return_value='video/mp4')
        self.renderer.transport_info = AsyncMock(return_value=('STOPPED', None))
        self.renderer.set_uri = AsyncMock()
        self.renderer.play = AsyncMock()
        m.registry.get = AsyncMock(return_value=self.renderer)
        m._confirm_playing = AsyncMock()
        m._jump_to_start = AsyncMock()
        m._monitor = AsyncMock()
        m.media = MediaServer(m.resolve_media, host='127.0.0.1', port=0)
        m.media.start()
        store.execute('INSERT INTO renderers(id,name,location,last_seen) VALUES(?,?,?,?)',
                      ('uuid:tv', 'TV', 'http://127.0.0.1/device', store.now()))
        m.cache.path('movie.mp4').write_bytes(b'video')
        entry = m.cache.register(123, 456, 'movie.mp4', 5, 'video/mp4', 1000)
        self.entry = m.cache.set_playable(entry['id'], 'movie.mp4', 'video/mp4', '{}', 1000)
        self.movie = dict(id=1, title='Movie', telegram_url='https://t.me/c/123/456')

    async def asyncTearDown(self):
        for task in list(self.manager.tasks.values()):
            await task
        self.manager.media.stop()
        self.paths.stop()
        self.temp.cleanup()

    async def test_pc_advertises_caption_and_serves_identical_srt_and_ranges(self):
        m = self.manager
        async def inspect(job, renderer, url):
            metadata = ET.fromstring(renderer.set_uri.call_args.args[1])
            caption = metadata.find('.//{http://www.sec.co.kr/}CaptionInfoEx')
            self.assertIsNotNone(caption)
            with urlopen(Request(url, method='HEAD')) as response:
                self.assertEqual(response.headers['CaptionInfoEx.sec'], caption.text)
            with urlopen(caption.text) as response:
                self.assertEqual(response.read().decode(), SRT)
            with urlopen(Request(caption.text, headers={'Range': 'bytes=0-9'})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), SRT.encode()[:10])
            self.assertEqual(job['start_at'], 400)
            m._jump_to_start.assert_awaited_once()
            self.caption_path = job['_caption']['path']
        m._monitor = inspect
        result, _ = await m.create(self.movie, 'uuid:tv', start_at=400, cached_only=True, subtitle_srt=SRT)
        await m.tasks[result['id']]
        self.assertNotEqual(m.jobs[result['id']]['state'], 'failed', m.jobs[result['id']].get('error'))
        self.assertNotIn('_caption', m.jobs[result['id']])
        self.assertFalse(self.caption_path.exists())
        self.assertTrue(m.cache.path('movie.mp4').exists())

    async def test_invalid_subtitles_do_not_start_or_replace_playback(self):
        with self.assertRaises(PlaybackError):
            await self.manager.create(self.movie, 'uuid:tv', cached_only=True, subtitle_srt='bad')
        self.assertFalse(self.manager.jobs)
        self.renderer.set_uri.assert_not_awaited()

    async def test_stop_replay_refreshes_movie_and_subtitles_and_supports_off(self):
        m = self.manager
        urls = []
        captions = []
        expected_srt = None
        async def inspect(job, renderer, url):
            urls.append(url)
            metadata = ET.fromstring(renderer.set_uri.call_args.args[1])
            caption = metadata.find('.//{http://www.sec.co.kr/}CaptionInfoEx')
            with urlopen(Request(url, method='HEAD')) as response:
                advertised = response.headers.get('CaptionInfoEx.sec')
            if expected_srt is None:
                self.assertIsNone(caption)
                self.assertIsNone(advertised)
            else:
                self.assertEqual(advertised, caption.text)
                captions.append(caption.text)
                with urlopen(caption.text) as response:
                    self.assertEqual(response.read().decode(), expected_srt)
            m._set(job, 'stopped', 'Stopped')
        m._monitor = inspect
        for expected_srt in (SRT, SRT.replace('Hello', 'Updated'), None):
            result, _ = await m.create(self.movie, 'uuid:tv', start_at=400,
                                       cached_only=True, subtitle_srt=expected_srt)
            await m.tasks[result['id']]
            self.assertEqual(m.jobs[result['id']]['state'], 'stopped', m.jobs[result['id']].get('error'))
        self.assertEqual(len(set(urls)), 3)
        self.assertEqual(len(set(captions)), 2)
        self.assertEqual(m.cache.path('movie.mp4').read_bytes(), b'video')


if __name__ == '__main__':
    unittest.main()
