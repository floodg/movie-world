"""Retention, credential redaction, Android retry dedupe and API boundaries."""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from tvplay import config, operations


def android(index=1):
    return dict(event_id=f'android-event-{index}', timestamp=int(time.time()*1000),
                level='info', operation='preparation', message='Command sent', movie_id='123')


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch.object(config, 'LOCAL', Path(self.temp.name))
        self.paths.start()

    def tearDown(self):
        self.paths.stop(); self.temp.cleanup()

    def test_redacts_secrets_and_keeps_context(self):
        operations.event('captions', 'Failed token=secret password=hunter2 https://service.test/?api_key=secret', 'error', 123)
        row = operations.history({})['events'][0]
        self.assertNotIn('secret', row['message'])
        self.assertNotIn('hunter2', row['message'])
        self.assertIn('Failed', row['message'])
        self.assertEqual(row['movie_id'], '123')

    def test_duplicate_phone_uploads_are_acknowledged_once(self):
        row = android(); row['source'] = 'server'
        self.assertEqual(operations.ingest([row]), [row['event_id']])
        operations.ingest([row])
        rows = operations.history({})['events']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], 'android')

    def test_rejects_whole_invalid_batch(self):
        bad = android(2); bad['timestamp'] = 'tomorrow'
        with self.assertRaises(ValueError): operations.ingest([android(), bad])
        self.assertEqual(operations.history({})['events'], [])

    def test_retention_and_filters(self):
        with patch.object(operations, 'LIMIT', 3):
            for i in range(5): operations.event('playback', f'State {i}', 'error' if i == 4 else 'info')
        self.assertEqual(len(operations.history({})['events']), 3)
        self.assertEqual(len(operations.history({'level':['error']})['events']), 1)
        self.assertEqual(len(operations.history({'q':['State 3']})['events']), 1)
        self.assertEqual(operations.history({'source':['android']})['events'], [])

    def test_pagination_has_no_overlap(self):
        with operations.database() as db:
            for i in range(205): operations._insert(db, dict(android(i), source='android'))
        first = operations.history({})
        second = operations.history({'before':[str(first['next'])]})
        self.assertEqual(len(first['events']), 200)
        self.assertEqual(len(second['events']), 5)
        self.assertFalse({r['event_id'] for r in first['events']} & {r['event_id'] for r in second['events']})

    def test_http_ingest_and_origin_restrictions(self):
        class Handler(server.Handler):
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
        base = f'http://127.0.0.1:{http.server_port}/api/operations'
        try:
            req = Request(base, data=json.dumps({'events':[android()]}).encode(), headers={'Content-Type':'application/json'})
            with urlopen(req) as response:
                self.assertEqual(json.load(response)['accepted'], ['android-event-1'])
            with urlopen(base) as response:
                self.assertNotIn('Access-Control-Allow-Origin', response.headers)
                self.assertEqual(len(json.load(response)['events']), 1)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base, headers={'Origin':'https://other.test'}))
            self.assertEqual(error.exception.code, 403)
        finally:
            http.shutdown(); http.server_close(); worker.join()


if __name__ == '__main__': unittest.main()
