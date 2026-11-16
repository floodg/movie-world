"""Persistent favourites preparation queue, independent of renderer sessions."""
import asyncio
import json
import sys
import uuid

from . import config, store
from .cache import mime_for, unlink
from .errors import PlaybackError
from .links import parse_message_link
from .subtitles import validate_srt
from .operations import event


class Library:
    def __init__(self, manager):
        self.manager = manager
        self.task = None
        self.downloading = {}  # Source link -> future; playback can join without cancelling it.
        self.protected = set()
        self.process = None

    def start(self):
        store.execute("""CREATE TABLE IF NOT EXISTS library_queue (
            movie_id INTEGER PRIMARY KEY, movie TEXT NOT NULL, state TEXT NOT NULL,
            error TEXT, updated_at REAL NOT NULL)""")
        store.execute("UPDATE library_queue SET state='queued' WHERE state IN ('downloading','transcribing')")
        self.task = asyncio.create_task(self.run())

    def enqueue(self, movie):
        parse_message_link(movie.get("telegram_url"))
        row = self.status(movie["id"])
        if row and row["state"] != "failed":
            channel, message = parse_message_link(movie.get("telegram_url"))
            if row["state"] != "ready" or ((not self.whisper_enabled() or self.caption(movie)) and self.manager.cache.lookup(channel, message)):
                return row
        store.execute("INSERT OR REPLACE INTO library_queue VALUES (?,?,'queued',NULL,?)",
                      (movie["id"], json.dumps(movie), store.now()))
        event('preparation', f'{movie.get("title", "Movie")} · queued for download' + (' and captions' if self.whisper_enabled() else ''), movie_id=movie['id'])
        return self.status(movie["id"])

    def status(self, movie_id):
        return store.query_one("SELECT movie_id,state,error,updated_at FROM library_queue WHERE movie_id=?", (movie_id,))

    def caption_path(self, movie):
        channel, message = parse_message_link(movie.get("telegram_url"))
        return config.CACHE_DIR / f"generated-{channel}-{message}.srt"

    def caption(self, movie):
        path = self.caption_path(movie)
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return None
        try:
            return validate_srt(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, PlaybackError):
            return None

    def state(self, movie_id, state, error=None):
        store.execute("UPDATE library_queue SET state=?,error=?,updated_at=? WHERE movie_id=?",
                      (state, error, store.now(), movie_id))
        event('preparation', f'{state}' + (f' · {error}' if error else ''), 'error' if state == 'failed' else 'info', movie_id)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def request(self, movie):
        return self.enqueue(movie)

    def whisper_enabled(self):
        return self.manager.settings.get('auto_whisper') is True

    async def whisper_settings(self, enabled=None):
        if enabled is not None:
            if not isinstance(enabled, bool):
                raise PlaybackError('Automatic Whisper must be on or off.', 'bad_request')
            settings = {**self.manager.settings, 'auto_whisper': enabled}
            config.save(settings)
            self.manager.settings['auto_whisper'] = enabled
            if not enabled and self.process and self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            event('settings', 'Automatic Whisper ' + ('enabled' if enabled else 'disabled'))
        return {'auto_whisper': self.whisper_enabled()}

    async def run(self):
        while True:
            row = store.query_one("SELECT * FROM library_queue WHERE state='queued' ORDER BY updated_at LIMIT 1")
            if not row:
                await asyncio.sleep(2)
                continue
            movie = json.loads(row["movie"])
            try:
                await self.prepare(movie)
                self.state(movie["id"], "ready")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state(movie["id"], "failed", str(exc)[:500])

    async def prepare(self, movie):
        manager = self.manager
        channel, message = parse_message_link(movie["telegram_url"])
        # Join existing downloads, including streaming downloads, before touching cache files.
        while any(j["state"] not in {"failed","cancelled","stopped","ended"}
                  and j.get("telegram_url") == movie["telegram_url"]
                  and not manager.cache.lookup(channel, message) for j in manager.jobs.values()) or manager.uploads:
            await asyncio.sleep(2)
        entry = manager.cache.lookup(channel, message)
        if entry is None:
            self.state(movie["id"], "downloading")
            future = asyncio.get_running_loop().create_future()
            self.downloading[movie["telegram_url"]] = future
            path = None
            pending = None
            try:
                video = await manager.telegram.resolve(channel, message)
                manager.cache.ensure_space(video.size, manager._protected_ids())
                name = "library-" + uuid.uuid4().hex + video.ext
                path = manager.cache.path(name)
                pending = manager.cache.begin_stream(channel, message, name, video.size, mime_for(video.ext, video.mime), video.duration, None)
                self.protected.add(pending["id"])
                await manager.telegram.download(video, path, lambda done, total: None)
                if path.stat().st_size != video.size:
                    raise ValueError("Incomplete background download; retry preparation.")
                entry = manager.cache.finish_stream(pending["id"])
                store.execute("UPDATE cache_entries SET play_name=NULL,play_size=NULL WHERE id=?", (entry["id"],))
                entry.update(play_name=None, play_size=None)
            finally:
                if entry is None and path:
                    unlink(path)
                    if pending:
                        manager.cache.remove(pending)
                        self.protected.discard(pending["id"])
                self.downloading.pop(movie["telegram_url"], None)
                future.set_result(None)
        self.protected.add(entry["id"])
        try:
            if not self.whisper_enabled() or self.caption(movie):
                return
            self.state(movie["id"], "transcribing")
            destination = self.caption_path(movie)
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, str(config.ROOT / "scripts" / "generate_captions.py"),
                str(manager.cache.path(entry["source_name"])), str(destination),
                "--model", str(manager.settings.get("whisper_model", "small")),
                "--threads", str(manager.settings.get("whisper_threads", 2)),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, error = await self.process.communicate()
            if not self.whisper_enabled():
                return
            if self.process.returncode:
                raise RuntimeError("Whisper failed: " + error.decode(errors="replace")[-500:])
            if not self.caption(movie):
                raise ValueError("Whisper produced no captions.")
        finally:
            if self.process and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            self.process = None
            self.protected.discard(entry["id"])
