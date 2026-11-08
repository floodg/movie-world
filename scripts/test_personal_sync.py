"""Isolated personal-data protocol, SQLite, HTTP and Telegram transport regression tests."""
import asyncio
import copy
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from tvplay.personal import PersonalStore, record, validate, covers, attachment, encode
from tvplay.personal_sync import PersonalSync


def row(movie="1305",updated=10,origin="android",**values):
    return record(dict(movieId=movie,updated=updated,origin=origin,**values))

def snapshot(device="android",account=123,favourites=None,progress=None):
    return dict(format="Movie World Sync",version=1,accountId=account,deviceId=device,
                favourites=favourites or [],progress=progress or [])

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.local=PersonalStore(Path(self.temp.name)/"test.sqlite")
    def tearDown(self):self.temp.cleanup()
    def test_existing_resume_adopted_once(self):
        path=Path(self.temp.name)/"legacy.sqlite"
        import sqlite3
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE resume_points(movie_id INTEGER PRIMARY KEY,position INTEGER NOT NULL,duration INTEGER,updated_at REAL NOT NULL)")
            db.execute("INSERT INTO resume_points VALUES(1305,300,7200,10)")
        db.close()
        local=PersonalStore(path);local.bind(123)
        self.assertEqual(local.snapshot()["progress"][0]["position"],300)
        self.assertEqual(local.snapshot()["progress"][0]["updated"],10000)
    def test_local_rows_adopted_and_accounts_isolated(self):
        self.local.progress(1305,300,7200)
        self.local.bind(123)
        self.assertEqual(len(self.local.snapshot()["progress"]),1)
        self.local.bind(456)
        self.assertEqual(self.local.snapshot()["progress"],[])
        self.local.bind(123)
        self.assertEqual(len(self.local.snapshot()["progress"]),1)
    def test_tombstone_and_reset_survive_old_device(self):
        self.local.bind(123)
        old=snapshot(favourites=[row()],progress=[row(position=500)])
        new=snapshot(favourites=[row(updated=20,removed=20)],progress=[row(updated=20,position=0)])
        self.local.merge([old,new,old])
        self.assertEqual(self.local.snapshot()["favourites"][0]["removed"],20)
        self.assertEqual(self.local.snapshot()["progress"][0]["position"],0)
        with self.local.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM resume_points").fetchone()[0],0)
    def test_new_remote_resume_reaches_playback_store(self):
        self.local.bind(123)
        self.local.merge([snapshot(progress=[row(position=300,duration=7200)])])
        with self.local.connect() as db:self.assertEqual(db.execute("SELECT position FROM resume_points WHERE movie_id=1305").fetchone()[0],300)
    def test_completion_keeps_history_but_clears_resume(self):
        self.local.bind(123)
        self.local.merge([snapshot(progress=[row(position=7200,duration=7200,finished=True)])])
        self.assertTrue(self.local.snapshot()["progress"][0]["finished"])
        with self.local.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM resume_points").fetchone()[0],0)
    def test_clock_skew_and_duplicate_paused_positions(self):
        self.local.bind(123)
        future=9999999999999
        self.local.merge([snapshot(progress=[row(updated=future,position=500)])])
        self.local.progress(1305,0,0)
        self.assertEqual(self.local.snapshot()["progress"][0]["updated"],future+1)
        self.local.progress(1305,0,0)
        self.assertEqual(self.local.snapshot()["progress"][0]["updated"],future+1)
    def test_foreign_or_malformed_snapshot_is_atomic(self):
        self.local.bind(123)
        for value in [snapshot(account=456,favourites=[row()]),snapshot(favourites=[row(),row()])]:
            with self.assertRaises(ValueError):self.local.merge([value])
        self.assertEqual(self.local.snapshot()["favourites"],[])
    def test_equal_versions_converge(self):
        self.local.bind(123)
        self.local.merge([snapshot(favourites=[row(origin="z",removed=10)]),snapshot(favourites=[row(origin="a")])])
        self.assertEqual(self.local.snapshot()["favourites"][0]["origin"],"z")
    def test_browser_stale_scope_does_not_write(self):
        self.local.bind(123)
        with self.assertRaises(ValueError):self.local.browser_merge(dict(scope="456",favourites=[row()]))
        self.assertEqual(self.local.snapshot()["favourites"],[])
    def test_browser_bad_batch_does_not_partially_write(self):
        self.local.bind(123)
        with self.assertRaises(ValueError):self.local.browser_merge(dict(scope="123",favourites=[row(),dict(movieId="invalid",updated=10)]))
        self.assertEqual(self.local.snapshot()["favourites"],[])
    def test_android_defaults_and_sanitized_filenames(self):
        value=snapshot(favourites=[dict(movieId="1305",updated=10,origin="android")])
        self.assertEqual(validate(value)["favourites"][0]["position"],0)
        self.assertTrue(attachment("movie_world_sync_v1_phone_backup.json"))
        self.assertFalse(attachment("unrelated.json"))
    def test_cleanup_requires_coverage(self):
        old=snapshot(favourites=[row()])
        self.assertFalse(covers([snapshot()],old))
        self.assertTrue(covers([snapshot(favourites=[row(updated=11,removed=11)])],old))

class FakeClient:
    def __init__(self):self.account=123;self.messages=[];self.sent=0;self.deleted=[]
    async def get_me(self):return SimpleNamespace(id=self.account)
    def add(self,data,name=None,caption=None):
        mid=max([m.id for m in self.messages] or [0])+1
        raw=encode(data).encode()
        message=SimpleNamespace(id=mid,raw_text=caption or "Movie World Sync v1 · "+data["deviceId"],
            file=SimpleNamespace(name=name or "movie_world_sync_v1_"+data["deviceId"].replace("-","_")+"_backup.json",size=len(raw)),raw=raw)
        self.messages.append(message);return message
    async def iter_messages(self,*args,**kwargs):
        for message in list(reversed(self.messages)):yield message
    async def download_media(self,message,file):return message.raw
    async def get_messages(self,peer,ids):return next(m for m in self.messages if m.id==ids)
    async def send_file(self,peer,stream,caption,**kwargs):
        self.sent+=1
        return self.add(json.loads(stream.getvalue()),stream.name,caption)
    async def delete_messages(self,peer,ids,**kwargs):
        self.deleted.extend(ids);self.messages=[m for m in self.messages if m.id not in ids]

class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.local=PersonalStore(Path(self.temp.name)/"sync.sqlite")
        self.client=FakeClient();self.source=SimpleNamespace(start=AsyncMock(),client=self.client)
        self.engine=PersonalSync(self.local,self.source)
    async def asyncTearDown(self):self.temp.cleanup()
    async def test_android_restore_and_no_repeated_upload(self):
        self.client.add(snapshot(favourites=[row()],progress=[row(position=300,duration=7200)]))
        await self.engine.reconcile()
        await self.engine.reconcile()
        self.assertEqual(self.client.sent,1)
        self.assertEqual(len(self.local.snapshot()["progress"]),1)
    async def test_upload_is_throttled_and_local_changes_survive(self):
        await self.engine.reconcile()
        self.local.progress(1305,300,7200)
        await self.engine.reconcile()
        self.assertEqual(self.client.sent,1)
        self.assertTrue(self.engine.status["pending"])
        await self.engine.reconcile(force=True)
        self.assertEqual(self.client.sent,2)
    async def test_cleanup_leaves_two_and_other_devices(self):
        self.local.bind(123)
        for index in range(4):
            self.client.add(snapshot(device=self.local.device,favourites=[row(updated=index+1)]))
        other=self.client.add(snapshot(favourites=[row("1306")]))
        await self.engine.reconcile()
        own=[m for m in self.client.messages if self.local.device in m.raw_text]
        self.assertEqual(len(own),2)
        self.assertIn(other,self.client.messages)
    async def test_invalid_snapshot_cannot_replace_local_data(self):
        self.local.bind(123);self.local.progress(1305,300,7200)
        self.client.add(snapshot(account=456))
        with self.assertRaises(ValueError):await self.engine.reconcile()
        self.assertEqual(self.local.snapshot()["progress"][0]["position"],300)
        self.assertEqual(self.client.sent,0)
    async def test_deleted_latest_backup_is_republished(self):
        await self.engine.reconcile()
        self.client.messages=[];self.engine.last_upload=0
        await self.engine.reconcile()
        self.assertEqual(self.client.sent,2)
    async def test_account_switch_during_playback_is_blocked(self):
        self.local.bind(123);self.local.progress(1305,300,7200)
        self.client.account=456;self.engine.active=lambda:True
        with self.assertRaises(ValueError):await self.engine.reconcile()
        self.assertEqual(self.local.meta("owner"),"123")

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();local=PersonalStore(Path(self.temp.name)/"api.sqlite")
        class Handler(server.Handler):
            personal=local
            personal_sync=None
            def log_message(self,*args):pass
        self.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.http.server_port)
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()
    def test_local_get_and_post_without_telegram(self):
        request=urllib.request.Request(self.base+"/api/personal/merge",data=json.dumps(dict(scope="local",favourites=[row()])).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(request) as response:
            data=json.load(response);self.assertEqual(len(data["snapshot"]["favourites"]),1)
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
    def test_cross_origin_and_oversized_requests_rejected(self):
        requests=[urllib.request.Request(self.base+"/api/personal",headers={"Origin":"https://evil.example"}),
                  urllib.request.Request(self.base+"/api/personal/merge",data=b"x"*9000,headers={"Content-Type":"application/json"})]
        for request,status in zip(requests,[403,413]):
            with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(request)
            self.assertEqual(ctx.exception.code,status)

if __name__=="__main__":unittest.main()
