"""Isolated backend tests; no Telegram connection, TV, or production state."""
import asyncio
import json
import sys
import tempfile
import sqlite3
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
        self.job = dict(id='012345', movie_id=1, title='Test', renderer_id='tizen:192.168.0.50',
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
        (root / '.local/tizen-token').write_text('012345')
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

    def post(self, token='012345'):
        req = Request('http://127.0.0.1:%d/api/tizen/%s/status' % (self.http.server_port, 'b' * 32),
                      data=b'{}', headers={'Content-Type':'application/json', 'X-Movie-World-Token':token})
        return urlopen(req)

    def test_token_and_ownership(self):
        with self.assertRaises(HTTPError) as error:
            self.post('654321')
        self.assertEqual(error.exception.code, 401)
        self.manager.get.assert_not_called()
        with self.post() as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            self.assertIn('media_url', json.load(response)['job'])
        self.manager.get.return_value = dict(renderer={'id':'tizen:192.168.0.99'})
        with self.assertRaises(HTTPError) as error:
            self.post()
        self.assertEqual(error.exception.code, 403)


class CatalogueUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = Path(self.temp.name) / 'catalogue.sqlite'
        con = sqlite3.connect(db)
        con.executescript('''
          CREATE TABLE movies(id INTEGER PRIMARY KEY,title TEXT,source_title TEXT,release_year INTEGER,
            imdb_rating REAL,imdb_votes INTEGER,poster_url TEXT,backdrop_url TEXT,overview TEXT,
            runtime_minutes INTEGER,telegram_url TEXT,media_type TEXT);
          CREATE TABLE genres(id INTEGER PRIMARY KEY,name TEXT);
          CREATE TABLE movie_genres(movie_id INTEGER,genre_id INTEGER);
          CREATE TABLE actors(id INTEGER PRIMARY KEY,name TEXT);
          CREATE TABLE movie_actors(movie_id INTEGER,actor_id INTEGER,billing_order INTEGER);
          INSERT INTO genres VALUES(1,'Drama'),(2,'Crime'),(3,'Adult');
          INSERT INTO movies VALUES
            (1,'Alpha',NULL,1994,9.3,100,'https://image.tmdb.org/a','https://image.tmdb.org/b','Overview',120,'https://t.me/c/1/1','movie'),
            (2,'Hidden',NULL,1995,10,200,NULL,'backdrop','Hidden',120,NULL,'movie'),
            (3,'Beta',NULL,1980,8,50,NULL,'backdrop','Overview',90,NULL,'movie'),
            (4,'Gamma',NULL,2005,7,20,NULL,NULL,'Overview',90,NULL,'movie');
          INSERT INTO movie_genres VALUES(1,1),(1,2),(2,1),(2,3),(3,1),(4,2);
          INSERT INTO actors VALUES(1,'Morgan Freeman');
          INSERT INTO movie_actors VALUES(1,1,0);
        ''')
        con.commit()
        con.close()
        self.db_patch = patch.object(server, 'DB', db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_home_matches_daily_rotation_and_excludes_adult(self):
        result = server.tizen_home(40)
        self.assertEqual(result['featured']['id'], 1)
        self.assertEqual(server.tizen_home(41)['featured']['id'], 3)
        self.assertEqual([m['id'] for m in result['top_rated']], [1,3,4])
        self.assertNotIn('Adult', [g['name'] for g in result['genres']])
        self.assertTrue(all(m['id'] != 2 for row in result['rows'] for m in row['movies']))

    def test_combined_actor_genre_and_year_search(self):
        params = {'q':['Morgan'], 'mode':['actor'], 'genre':['1'], 'years':['1990-2000']}
        self.assertEqual([m['id'] for m in server.tizen_search(params)['movies']], [1])
        params['years'] = ['1970-1980']
        self.assertEqual(server.tizen_search(params)['movies'], [])
        self.assertEqual(server.tizen_search({'q':['%']})['movies'], [])

    def test_related_movies_excludes_current_and_adult(self):
        self.assertEqual([m['id'] for m in server.tizen_similar(1)], [3,4])

    def test_actor_picker_counts_and_exact_selection(self):
        self.assertEqual(server.search_actors('M')['actors'], [])
        actors = server.search_actors('Morgan')['actors']
        self.assertEqual(actors, [{'id':1,'name':'Morgan Freeman','movie_count':1}])
        # The selected ID, rather than a name substring, is authoritative.
        movies = server.tizen_search({'q':['not the actor name'],'mode':['actor'],'actor_id':['1']})['movies']
        self.assertEqual([m['id'] for m in movies], [1])
        self.assertEqual(server.tizen_search({'mode':['actor'],'actor_id':['999']})['movies'], [])

    def test_selected_actor_loads_all_counted_movies(self):
        con = sqlite3.connect(server.DB)
        con.executemany('INSERT INTO movies(id,title,release_year,imdb_rating,imdb_votes) VALUES(?,?,?,?,?)',
                        [(i, 'Extra movie ' + str(i), 2000, 7, 10) for i in range(10,131)])
        con.executemany('INSERT INTO movie_actors VALUES(?,1,0)', [(i,) for i in range(10,131)])
        con.commit()
        con.close()
        count = server.search_actors('Morgan')['actors'][0]['movie_count']
        movies = server.tizen_search({'mode':['actor'],'actor_id':['1']})['movies']
        self.assertEqual(count, 122)
        self.assertEqual(len(movies), count)


if __name__ == '__main__':
    unittest.main()
