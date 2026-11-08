"""Telegram transport on the existing playback worker, or an independent worker for catalogue-only mode."""
import asyncio
import concurrent.futures
import io
import json
import threading
import time
import uuid

from telethon import errors
from .personal import MAX_BYTES, attachment, covers, encode, validate
from .telegram_dl import TelegramSource

class PersonalSync:
    def __init__(self, local, source, active=lambda:False):
        self.local,self.source,self.active=local,source,active
        self.event=asyncio.Event()
        self.forced=False
        self.last_upload=0
        self.retry_at=0
        self.status=dict(message="Waiting for Telegram",lastSync=0,pending=True)
        self.task=None

    def request(self,force=False):
        self.forced=self.forced or force
        self.event.set()

    async def start(self):
        self.task=asyncio.create_task(self.run())
        self.request()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)

    async def run(self):
        while True:
            delay=max(1,self.last_upload+60-time.time()) if self.status.get("pending") and self.last_upload+60>time.time() else 60
            try: await asyncio.wait_for(self.event.wait(),timeout=delay)
            except asyncio.TimeoutError: pass
            self.event.clear()
            force,self.forced=self.forced,False
            if time.time()<self.retry_at:continue
            try: await self.reconcile(force)
            except asyncio.CancelledError:raise
            except errors.FloodWaitError as exc:
                self.retry_at=time.time()+max(1,exc.seconds)
                self.status={**self.status,"message":"Telegram rate limit; retrying later","pending":True}
            except Exception:
                # Never include SDK exceptions, credentials, message contents or request headers in UI/logs.
                self.status={**self.status,"message":"Telegram sync unavailable; local data kept. Check PC Telegram setup and connection.","pending":True}

    async def reconcile(self,force=False):
        await self.source.start()
        client=self.source.client
        me=await client.get_me()
        if me is None:raise ValueError("Telegram sign-in required")
        if self.local.meta("owner") not in ("local",str(me.id)) and self.active():
            raise ValueError("Stop playback before switching Telegram accounts")
        self.local.bind(me.id)
        backups=[]
        async for message in client.iter_messages("me",search="Movie World Sync v1"):
            if not message.file or not attachment(message.file.name):continue
            if message.file.size>MAX_BYTES:raise ValueError("Oversized sync attachment")
            raw=await client.download_media(message,file=bytes)
            if raw is None or len(raw)>MAX_BYTES:raise ValueError("Invalid sync attachment")
            snapshot=validate(json.loads(raw))
            if snapshot["accountId"]!=me.id:raise ValueError("Foreign snapshot")
            backups.append((message,snapshot))
        check=await client.get_me()
        if check is None or check.id!=me.id:raise ValueError("Account changed")
        self.local.merge([snapshot for _,snapshot in backups])
        snapshot=self.local.snapshot()
        encoded=encode(snapshot)
        exists=any(encode(s)==encoded for _,s in backups)
        if self.local.meta("published-"+str(me.id))!=encoded or not exists:
            if not force and time.time()<self.last_upload+60:
                self.status={**self.status,"message":"Local changes pending Telegram sync","pending":True}
                return
            data=encoded.encode("utf-8")
            if len(data)>MAX_BYTES:raise ValueError("Snapshot too large")
            stream=io.BytesIO(data)
            stream.name="movie-world-sync-v1-"+self.local.device+"-"+str(uuid.uuid4())+".json"
            message=await client.send_file("me",stream,caption="Movie World Sync v1 · "+self.local.device,
                                           force_document=True,silent=True,parse_mode=None)
            # Verify server persistence and round-trip content before recording success or pruning.
            confirmed=await client.get_messages("me",ids=message.id)
            raw=await client.download_media(confirmed,file=bytes)
            if raw is None or encode(validate(json.loads(raw)))!=encoded:raise ValueError("Upload verification failed")
            self.local.set_meta("published-"+str(me.id),encoded)
            self.last_upload=time.time()
            backups.append((confirmed,snapshot))
        now=int(time.time()*1000)
        self.local.set_meta("last-sync-"+str(me.id),now)
        pending=encode(self.local.snapshot())!=encoded
        self.status=dict(message="Synced to Saved Messages",lastSync=now,pending=pending)
        try:
            own=sorted([(m,s) for m,s in backups if s["deviceId"]==self.local.device and
                        m.raw_text=="Movie World Sync v1 · "+self.local.device and
                        m.file.name.replace("_","-").startswith("movie-world-sync-v1-"+self.local.device+"-")],
                       key=lambda pair:pair[0].id,reverse=True)
            kept=[s for _,s in own[:2]]
            remove=[m.id for m,s in own[2:] if covers(kept,s)]
            for start in range(0,len(remove),100):await client.delete_messages("me",remove[start:start+100],revoke=True)
        except asyncio.CancelledError:raise
        except errors.FloodWaitError as exc:
            self.retry_at=time.time()+max(1,exc.seconds)
            self.status["message"]="Synced to Saved Messages; backup cleanup rate-limited"
        except Exception:self.status["message"]="Synced to Saved Messages; backup cleanup pending"

class PersonalRuntime:
    def __init__(self,local,settings,tv=None):
        self.tv=tv
        self.loop=tv.loop if tv else asyncio.new_event_loop()
        self.thread=None
        self.source=tv.manager.telegram if tv else TelegramSource(settings)
        self.local=local
        self.engine=None

    def start(self):
        if not self.tv:
            self.thread=threading.Thread(target=self.loop.run_forever,name="personal-sync",daemon=True)
            self.thread.start()
        async def create():
            self.engine=PersonalSync(self.local,self.source,lambda: bool(self.tv and any(
                j["state"] not in ("failed","cancelled","stopped","ended") for j in self.tv.manager.jobs.values())))
            await self.engine.start()
        asyncio.run_coroutine_threadsafe(create(),self.loop).result(5)
        if self.tv:
            self.tv.manager.personal=self.local
            self.tv.manager.personal_sync=self.engine

    @property
    def status(self):return dict(self.engine.status) if self.engine else dict(message="Local storage ready",lastSync=0,pending=True)

    def request(self,force=False):
        if self.engine:self.loop.call_soon_threadsafe(self.engine.request,force)

    def stop(self):
        async def close():
            if self.engine:await self.engine.close()
            if not self.tv:await self.source.close()
        try:asyncio.run_coroutine_threadsafe(close(),self.loop).result(10)
        except (TimeoutError,concurrent.futures.TimeoutError):pass
        if not self.tv:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
