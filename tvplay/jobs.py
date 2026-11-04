"""Playback jobs: Telegram download, format preparation, TV start and monitoring.

Everything here runs on the single worker event loop owned by service.TvPlayback.
"""
import asyncio
import json
import os
import re
import time
import traceback
import uuid

from async_upnp_client.exceptions import UpnpError
from telethon import errors as tg_errors

from . import config, formats, store
from .cache import Cache, disk_full, mime_for, safe_name, unlink
from .errors import PlaybackError
from .links import parse_message_link
from .media import ActiveReads, GrowingFile, MediaServer
from .netutil import local_ip_for
from .renderer import RendererRegistry, didl_metadata, seconds_to_hms, upnp_error
from .telegram_dl import TelegramSource

TERMINAL = {"failed", "cancelled", "stopped", "ended"}
ON_TV = {"starting", "playing", "paused"}
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DOWNLOAD_ATTEMPTS = 4
MAX_FLOOD_WAIT = 900
START_TIMEOUT = 45
POLL_SECONDS = 3
OFFLINE_POLLS = 10
DISCOVERY_MAX_AGE = 60
RESUME_MIN_SECONDS = 60
RESUME_SAVE_SECONDS = 15
STREAM_BUFFER = 16 * 1024 * 1024
STREAM_MIN_FILE = 64 * 1024 * 1024
STREAM_PROBE_GAP = 48 * 1024 * 1024
HEADER_READ = 1024 * 1024
INDEX_EXTENSIONS = {".mp4", ".m4v", ".mov"}


class Busy(Exception):
    def __init__(self, job):
        super().__init__("busy")
        self.job = job


class NotFound(Exception):
    pass


class JobManager:
    def __init__(self, settings):
        self.settings = settings
        self.reads = ActiveReads()
        self.cache = Cache(settings, self.reads)
        self.telegram = TelegramSource(settings)
        self.registry = RendererRegistry(settings.get("renderer_urls") or [])
        self.media = MediaServer(self.resolve_media, port=int(settings.get("media_port") or 8766), reads=self.reads)
        self.jobs = {}
        self.tasks = {}
        self.growing = {}
        self.discovery_task = None

    async def start(self):
        store.init()
        self.cache.remove_incomplete()
        self._reconcile()
        self.media.start()
        self.discovery_task = asyncio.create_task(self._discover())

    async def close(self):
        for job_id in list(self.tasks):
            self.tasks[job_id].cancel()
        await self.telegram.close()
        await self.registry.close()
        self.media.stop()

    def resolve_media(self, token):
        """Media-server lookup. A download still in progress is served at its final size, and readers wait for bytes."""
        media = self.cache.resolve_token(token)
        if media is None:
            return None
        growing = self.growing.get(token)
        if growing is not None and not growing.finished:
            media.size = growing.size
            media.wait = growing.wait_until
            media.read_at = growing.read_at
            media.streaming = True
        return media

    def _reconcile(self):
        now = store.now()
        store.execute(
            """UPDATE jobs SET state = 'failed', retryable = 1, error_code = 'restart', updated_at = ?,
               error = 'Interrupted because the PC server restarted. Try again.'
               WHERE state IN ('queued', 'downloading', 'checking', 'preparing')""",
            (now,),
        )
        store.execute(
            """UPDATE jobs SET state = 'stopped', updated_at = ?,
               detail = 'The PC server restarted, so this playback can no longer be controlled here.'
               WHERE state IN ('starting', 'playing', 'paused')""",
            (now,),
        )

    # Renderers

    async def _discover(self, timeout=5):
        known = [row["location"] for row in store.query_all("SELECT location FROM renderers")]
        try:
            found = await self.registry.discover(timeout=timeout, known_locations=known)
        except Exception as exc:
            print(f"TV discovery failed: {exc}", flush=True)
            return []
        for renderer in found:
            store.execute(
                """INSERT INTO renderers (id, name, location, info, last_seen) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, location = excluded.location,
                   info = excluded.info, last_seen = excluded.last_seen""",
                (renderer.udn, renderer.name, renderer.location, json.dumps(renderer.summary()), store.now()),
            )
        return found

    async def list_renderers(self, refresh=False):
        stale = time.time() - self.registry.last_discovery > DISCOVERY_MAX_AGE
        running = self.discovery_task is not None and not self.discovery_task.done()
        if refresh or (stale and not running):
            self.discovery_task = asyncio.create_task(self._discover())
            running = True
        if refresh and running:
            try:
                await asyncio.wait_for(asyncio.shield(self.discovery_task), 9)
            except asyncio.TimeoutError:
                pass
        rows = store.query_all("SELECT id, name, info, last_seen FROM renderers ORDER BY last_seen DESC")
        renderers = []
        for row in rows:
            info = json.loads(row["info"] or "{}")
            seen = self.registry.seen_at.get(row["id"])
            renderers.append({
                "id": row["id"],
                "name": row["name"],
                "model": info.get("model", ""),
                "available": seen is not None and time.time() - seen < 600,
                "last_seen": row["last_seen"],
            })
        return {"renderers": renderers, "searching": self.discovery_task is not None and not self.discovery_task.done()}

    # Jobs: public API

    def _active(self):
        for job in self.jobs.values():
            if job["state"] not in TERMINAL:
                return job
        return None

    async def active(self):
        job = self._active()
        return self.snapshot(job) if job else None

    async def get(self, job_id):
        job = self.jobs.get(job_id)
        if job is not None:
            return self.snapshot(job)
        row = store.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            raise NotFound()
        return self.snapshot(row)

    async def resume_point(self, movie_id):
        return store.query_one("SELECT position, duration FROM resume_points WHERE movie_id = ?", (movie_id,))

    async def resume_points(self):
        return store.query_all("SELECT movie_id, position, duration FROM resume_points WHERE duration > 0")

    async def create(self, movie, renderer_id, idempotency_key=None, replace=False, start_at=None):
        if start_at is not None and (
            not isinstance(start_at, (int, float)) or isinstance(start_at, bool) or start_at < 0
        ):
            raise PlaybackError("Invalid start position.", "bad_request")
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.match(idempotency_key):
                raise PlaybackError("Invalid request key.", "bad_request")
            existing = store.query_one("SELECT id FROM jobs WHERE idempotency_key = ?", (idempotency_key,))
            if existing:
                return await self.get(existing["id"]), False
        parse_message_link(movie.get("telegram_url"))
        renderer = store.query_one("SELECT id, name FROM renderers WHERE id = ?", (renderer_id,))
        if renderer is None:
            raise PlaybackError("Unknown TV. Refresh the TV list and choose again.", "bad_renderer")
        active = self._active()
        if active is not None:
            if active["movie_id"] == movie["id"] and active["renderer_id"] == renderer_id:
                return self.snapshot(active), False
            if not replace:
                raise Busy(self.snapshot(active))
            await self._finish(active, "stopped", "Replaced by another title.")
        if start_at is None:
            saved = await self.resume_point(movie["id"])
            if saved:
                start_at = saved["position"]
        now = store.now()
        for old_id in [key for key, old in self.jobs.items() if old["state"] in TERMINAL and now - old["updated_at"] > 86400]:
            del self.jobs[old_id]
        job = {
            "id": uuid.uuid4().hex,
            "movie_id": movie["id"],
            "title": movie["title"],
            "telegram_url": movie["telegram_url"],
            "renderer_id": renderer["id"],
            "renderer_name": renderer["name"],
            "state": "queued",
            "detail": "Queued",
            "error": None,
            "error_code": None,
            "retryable": 0,
            "cache_id": None,
            "created_at": now,
            "updated_at": now,
            "progress": None,
            "position": None,
            "duration": None,
            "can_pause": False,
            "can_seek": False,
            "start_at": int(start_at) if start_at is not None else None,
        }
        store.execute(
            """INSERT INTO jobs (id, movie_id, title, renderer_id, renderer_name, idempotency_key, state, detail,
               created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job["id"], job["movie_id"], job["title"], job["renderer_id"], job["renderer_name"],
             idempotency_key, job["state"], job["detail"], now, now),
        )
        self.jobs[job["id"]] = job
        self.tasks[job["id"]] = asyncio.create_task(self._run(job))
        return self.snapshot(job), True

    async def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return await self.get(job_id)
        if job["state"] not in TERMINAL:
            if job["state"] in ("playing", "paused") and self._download_running(job):
                job["_halt_playback"] = True
                halt = job.get("_halt_event")
                if halt is not None:
                    halt.set()
                await self._stop_renderer_quiet(job)
                self._set(job, "downloading", "Saving the file for resume")
                return self.snapshot(job)
            final = "stopped" if job["state"] in ON_TV else "cancelled"
            await self._finish(job, final, "Stopped from the phone." if final == "stopped" else "Cancelled.")
        return self.snapshot(job)

    async def control(self, job_id, action, position=None):
        job = self.jobs.get(job_id)
        if job is None or job["state"] in TERMINAL:
            raise PlaybackError("This playback has already finished.", "finished")
        if action == "stop":
            return await self.cancel(job_id)
        renderer = job.get("_renderer")
        if renderer is None or job["state"] not in ("playing", "paused"):
            raise PlaybackError("The TV has not started playing yet.", "not_ready")
        try:
            if action == "pause":
                await renderer.pause()
                self._set(job, "paused", "Paused")
            elif action == "resume":
                await renderer.play()
                self._set(job, "playing", "Playing")
            elif action == "seek":
                if not job["can_seek"]:
                    raise PlaybackError("The TV does not support seeking.", "unsupported")
                if not isinstance(position, (int, float)) or position < 0:
                    raise PlaybackError("Invalid seek position.", "bad_request")
                if job["duration"]:
                    position = min(position, max(0, job["duration"] - 5))
                await renderer.seek(int(position))
                job["position"] = int(position)
            else:
                raise PlaybackError("Unknown control action.", "bad_request")
        except (UpnpError, asyncio.TimeoutError, OSError) as exc:
            raise upnp_error(exc, "The TV did not accept the command") from exc
        return self.snapshot(job)

    # Jobs: state

    def snapshot(self, job):
        error = None
        if job.get("error"):
            error = {"message": job["error"], "code": job.get("error_code"), "retryable": bool(job.get("retryable"))}
        return {
            "id": job["id"],
            "movie_id": job["movie_id"],
            "title": job["title"],
            "renderer": {"id": job["renderer_id"], "name": job.get("renderer_name")},
            "state": job["state"],
            "detail": job.get("detail"),
            "error": error,
            "progress": job.get("progress"),
            "position": job.get("position"),
            "duration": job.get("duration"),
            "can_pause": job.get("can_pause", False),
            "can_seek": job.get("can_seek", False),
            "active": job["state"] not in TERMINAL,
            "updated_at": job["updated_at"],
        }

    def _set(self, job, state, detail=None, persist=True):
        changed = job["state"] != state or job.get("detail") != detail
        job["state"] = state
        job["detail"] = detail
        job["updated_at"] = store.now()
        if persist and changed:
            store.execute(
                "UPDATE jobs SET state = ?, detail = ?, error = ?, error_code = ?, retryable = ?, cache_id = ?, updated_at = ? WHERE id = ?",
                (state, detail, job.get("error"), job.get("error_code"), int(bool(job.get("retryable"))),
                 job.get("cache_id"), job["updated_at"], job["id"]),
            )

    def _fail(self, job, error):
        job["error"] = error.message
        job["error_code"] = error.code
        job["retryable"] = error.retryable
        job["progress"] = None
        self._set(job, "failed", None)

    async def _finish(self, job, final, detail):
        job["_final"] = (final, detail)
        renderer = job.get("_renderer")
        if job["state"] in ON_TV and renderer is not None:
            try:
                await renderer.stop()
            except (UpnpError, PlaybackError, asyncio.TimeoutError, OSError):
                pass
        task = self.tasks.get(job["id"])
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if job["state"] not in TERMINAL:
            job["progress"] = None
            self._set(job, final, detail)

    async def _run(self, job):
        try:
            channel_id, message_id = parse_message_link(job["telegram_url"])
            entry = self.cache.lookup(channel_id, message_id)
            if entry is not None:
                job["cache_id"] = entry["id"]
                self.cache.touch(entry["id"])
                self._set(job, "downloading", "Already downloaded")
                await self._play_ready(job, entry)
            else:
                await self._download_and_play(job, channel_id, message_id)
        except asyncio.CancelledError:
            job["_aborted"] = True
            final, detail = job.get("_final") or ("cancelled", "Cancelled.")
            job["progress"] = None
            self._set(job, final, detail)
        except PlaybackError as exc:
            self._fail(job, exc)
        except Exception as exc:
            traceback.print_exc()
            self._fail(job, PlaybackError(f"Unexpected error on the PC: {exc}", "internal", retryable=True))
        finally:
            try:
                await self._cleanup_job_files(job)
            except Exception:
                traceback.print_exc()
            if job.get("_renderer") is not None:
                self._remember(job, force=True)
            self.tasks.pop(job["id"], None)

    async def _play_ready(self, job, entry):
        entry = await self._prepare(job, entry)
        renderer, url = await self._start_tv(job, entry)
        await self._monitor(job, renderer, url)

    # Download

    async def _telegram(self, job, call):
        """Run a Telegram call, waiting out flood limits."""
        while True:
            try:
                return await call()
            except tg_errors.FloodWaitError as exc:
                await self._flood_wait(job, exc.seconds)
            except tg_errors.RPCError as exc:
                raise PlaybackError(f"Telegram refused the request ({exc.__class__.__name__}).", "telegram", True) from exc
            except (ConnectionError, asyncio.TimeoutError) as exc:
                raise PlaybackError("Could not reach Telegram. Check the PC's internet connection.", "network", True) from exc

    async def _flood_wait(self, job, seconds):
        if seconds > MAX_FLOOD_WAIT:
            raise PlaybackError(
                f"Telegram rate limit: try again in about {seconds // 60} minutes.", "rate_limited", retryable=True
            )
        for remaining in range(seconds, 0, -1):
            job["detail"] = f"Telegram rate limit, waiting {remaining}s"
            await asyncio.sleep(1)

    async def _download_and_play(self, job, channel_id, message_id):
        self._set(job, "downloading", "Connecting to Telegram")
        video = await self._telegram(job, lambda: self.telegram.resolve(channel_id, message_id))
        job["_video"] = video
        stale = store.query_one(
            "SELECT * FROM cache_entries WHERE channel_id = ? AND message_id = ?", (channel_id, message_id)
        )
        if stale is not None and stale.get("ready", 1) == 0:
            self.cache.remove(stale)
        self.cache.ensure_space(video.size, self._protected_ids())
        name = safe_name(channel_id, message_id, video.ext)
        path = self.cache.path(name)
        unlink(path)
        growing = GrowingFile(path, video.size)
        job["_download_path"] = path
        if job.get("start_at"):
            self._skip_early(job, "Downloading the whole file so Resume can jump to the saved time.")
        elif not video.size or video.size < STREAM_MIN_FILE:
            self._skip_early(job, None)
        download_task = asyncio.create_task(self._fetch(job, path, growing, channel_id, message_id))
        job["_download_task"] = download_task
        entry = None
        try:
            while not download_task.done() and entry is None and not job.get("_no_early"):
                await asyncio.sleep(0.4)
                entry = await self._try_open_stream(job, growing, channel_id, message_id, name)
            if entry is None:
                actual = await download_task
                video = job["_video"]
                entry = self.cache.register(
                    channel_id, message_id, name, actual, mime_for(video.ext, video.mime), video.duration
                )
                job["cache_id"] = entry["id"]
                job["progress"] = None
                await self._play_ready(job, entry)
                return
            print(
                f"Starting {job['title']} on the TV with {growing.written / 1_000_000:.0f} MB downloaded",
                flush=True,
            )
            try:
                renderer, url = await self._start_tv(job, entry)
            except asyncio.CancelledError:
                raise
            except (PlaybackError, UpnpError, asyncio.TimeoutError, OSError) as exc:
                print(f"The TV could not open the partial file ({exc}). Waiting for the rest.", flush=True)
                config.log_playback(f"partial open failed: {exc}")
                self._set(job, "downloading", "The TV needs the whole file. Download is continuing.")
                await self._seal_download(job, download_task, entry)
                await self._play_ready(job, job["_stream_entry"])
                return
            job["_halt_event"] = asyncio.Event()
            play_task = asyncio.create_task(self._monitor(job, renderer, url))
            job["_monitor_task"] = play_task
            await self._finish_streaming(job, play_task, download_task, entry)
        finally:
            monitor = job.get("_monitor_task")
            if monitor is not None and not monitor.done():
                monitor.cancel()
            token = job.get("_growing_token")
            if token:
                self.growing.pop(token, None)

    def _skip_early(self, job, detail):
        job["_no_early"] = True
        if detail:
            job["_status_locked"] = detail
            self._set(job, "downloading", detail)

    def _download_status(self, job, detail):
        if job["state"] in ON_TV or job["state"] in ("starting", "checking", "preparing"):
            return
        self._set(job, "downloading", job.get("_status_locked") or detail)

    async def _try_open_stream(self, job, growing, channel_id, message_id, name):
        """Point the TV at the growing file once its header is playable. None leaves the full-download path."""
        if job.get("_no_early") or growing.serving:
            return None
        if growing.finished and not growing.ok:
            return None
        video = job["_video"]
        if growing.written < STREAM_BUFFER:
            return None
        failed_at = job.get("_probe_failed_at", 0)
        if failed_at and growing.written < failed_at + STREAM_PROBE_GAP:
            return None
        if formats.tool(self.settings, "ffprobe") is None:
            self._skip_early(job, None)
            self._set(job, "downloading", job.get("detail") or "Downloading from Telegram")
            return None
        generation = growing.generation
        self._set(job, "checking", "Checking if the TV can start before the download finishes")
        if growing.tail_offset is not None:
            data = await formats.probe_indexed(self.settings, growing.path, growing.tail)
        else:
            data = await formats.probe_header(self.settings, growing.path)
        if growing.generation != generation or (growing.finished and not growing.ok):
            return None
        if not data:
            if failed_at:
                self._skip_early(job, "This file has to finish downloading before the TV can open it.")
            else:
                job["_probe_failed_at"] = growing.written
                self._set(job, "downloading", "Downloading from Telegram")
            return None
        try:
            plan = formats.plan_for(data, mime_for(video.ext, video.mime))
        except PlaybackError as exc:
            self._skip_early(job, exc.message)
            return None
        if plan.action != "direct":
            self._skip_early(job, "This video has to finish downloading before the TV can play it.")
            return None
        duration = video.duration
        if not duration:
            probed = formats.duration_of(data)
            if probed and probed >= 120:
                duration = probed
        if not duration:
            self._skip_early(job, "This file has to finish downloading before the TV can open it.")
            return None
        if growing.generation != generation or (growing.finished and not growing.ok):
            return None
        summary = json.dumps({"action": "direct", "reasons": plan.reasons, "streamed": True})
        entry = self.cache.begin_stream(channel_id, message_id, name, growing.size or video.size, plan.mime, duration, summary)
        job["cache_id"] = entry["id"]
        job["_stream_entry"] = entry
        job["_growing_token"] = entry["token"]
        growing.start_serving()
        self.growing[entry["token"]] = growing
        return entry

    def _incomplete(self):
        raise PlaybackError("The downloaded file is incomplete. Try again.", "network", True)

    async def _download_video(self, job, path, growing, video, tracker):
        """Download the file. An MP4 index that lives at the end is moved to the front for the TV."""
        if job.get("_no_early") or not video.size or video.ext not in INDEX_EXTENSIONS:
            await self.telegram.download(
                video, path, lambda done, total: self._note_download(growing, tracker, done, total)
            )
            return
        size = video.size
        header = bytearray()

        def take_header(data):
            header.extend(data)
            tracker.update(len(header), size)

        first = min(HEADER_READ, size)
        got = await self.telegram.copy_range(video, 0, first, take_header)
        if got != first:
            self._incomplete()
        layout = formats.mp4_tail_index(bytes(header), size)
        if layout is not None and layout[1] > got:
            await self._download_with_index(job, path, growing, video, tracker, header, got, layout)
            return
        with path.open("wb") as handle:
            self._write_growing(handle, growing, header, tracker, size)

            def accept(data):
                self._write_growing(handle, growing, data, tracker, size)

            rest = size - got
            if rest > 0 and await self.telegram.copy_range(video, got, rest, accept) != rest:
                self._incomplete()

    async def _download_with_index(self, job, path, growing, video, tracker, header, got, layout):
        """Fetch the trailing index, write it at the front, then download the media behind it."""
        size = video.size
        mdat_pos, mdat_end = layout
        self._download_status(job, "Fetching the end of the file so the TV can start")
        tail = bytearray()

        def take_tail(data):
            tail.extend(data)
            tracker.update(len(header) + len(tail), size)

        if await self.telegram.copy_range(video, mdat_end, size - mdat_end, take_tail) != size - mdat_end:
            self._incomplete()
        opening = formats.place_index_first(bytes(header[:mdat_pos]), bytes(tail))
        with path.open("wb") as handle:
            if opening is None:
                self._skip_early(job, "This file has to finish downloading before the TV can open it.")
                config.log_playback(f"index for {job['title']} could not be moved to the front")
                self._write_original_tail(handle, growing, video, tracker, header, got, mdat_end, tail)
                return
            config.log_playback(f"index for {job['title']} moved to the front ({len(opening)} bytes)")
            self._write_growing(handle, growing, opening, tracker, size)
            self._write_growing(handle, growing, header[mdat_pos:], tracker, size)
            self._download_status(job, "Downloading from Telegram")

            def accept(data):
                self._write_growing(handle, growing, data, tracker, size)

            body = mdat_end - got
            if body > 0 and await self.telegram.copy_range(video, got, body, accept) != body:
                self._incomplete()

    def _write_growing(self, handle, growing, data, tracker, size):
        handle.write(data)
        handle.flush()
        growing.note_prefix(handle.tell())
        tracker.update(handle.tell(), size)

    async def _write_original_tail(self, handle, growing, video, tracker, header, got, mdat_end, tail):
        """Keep the original byte order when the index cannot be moved. Progress counts the index once."""
        size = video.size
        handle.write(header)
        handle.flush()
        growing.note_prefix(handle.tell())

        def accept(data):
            handle.write(data)
            handle.flush()
            growing.note_prefix(handle.tell())
            tracker.update(handle.tell() + len(tail), size)

        body = mdat_end - got
        if body > 0 and await self.telegram.copy_range(video, got, body, accept) != body:
            self._incomplete()
        handle.write(tail)
        handle.flush()
        growing.note_prefix(handle.tell())
        tracker.update(handle.tell(), size)

    async def _fetch(self, job, path, growing, channel_id, message_id):
        try:
            return await self._fetch_attempts(job, path, growing, channel_id, message_id)
        except asyncio.CancelledError:
            growing.finish(ok=False)
            raise

    async def _fetch_attempts(self, job, path, growing, channel_id, message_id):
        video = job["_video"]
        tracker = Progress(job, video.size)
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            self._download_status(job, "Downloading from Telegram")
            try:
                await self._download_video(job, path, growing, video, tracker)
                break
            except asyncio.CancelledError:
                raise
            except tg_errors.FloodWaitError as exc:
                if not self._discard_attempt(path, growing):
                    raise PlaybackError("Telegram interrupted the download after the TV had started.", "telegram", True) from exc
                await self._flood_wait(job, exc.seconds)
            except tg_errors.FileReferenceExpiredError:
                if not self._discard_attempt(path, growing):
                    raise PlaybackError("The download expired after the TV had started. Try again.", "telegram", True)
                video = await self._telegram(job, lambda: self.telegram.resolve(channel_id, message_id))
                job["_video"] = video
                growing.size = video.size or growing.size
            except tg_errors.RPCError as exc:
                self._discard_attempt(path, growing)
                raise PlaybackError(f"Telegram refused the download ({exc.__class__.__name__}).", "telegram", True) from exc
            except (OSError, asyncio.TimeoutError) as exc:
                if not self._discard_attempt(path, growing):
                    if disk_full(exc):
                        raise PlaybackError("The PC ran out of disk space while downloading.", "disk_full") from exc
                    raise PlaybackError("The download stopped after the TV had started.", "network", True) from exc
                if disk_full(exc):
                    raise PlaybackError("The PC ran out of disk space while downloading.", "disk_full") from exc
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise PlaybackError("The download kept failing. Check the PC's internet connection.", "network", True) from exc
                delay = 5 * attempt
                job["detail"] = f"Connection lost, retrying in {delay}s"
                await asyncio.sleep(delay)
            except Exception:
                self._discard_attempt(path, growing)
                raise
        else:
            raise PlaybackError("The download did not complete. Try again.", "network", True)
        video = job["_video"]
        actual = path.stat().st_size if path.is_file() else -1
        if video.size and actual != video.size:
            growing.finish(ok=False)
            if not growing.serving:
                unlink(path)
            raise PlaybackError("The downloaded file is incomplete. Try again.", "network", True)
        growing.finish(ok=True)
        return actual

    @staticmethod
    def _note_download(growing, tracker, done, total):
        growing.progress(done, total)
        tracker.update(done, total)

    @staticmethod
    def _discard_attempt(path, growing):
        """Drop a failed attempt. False means the TV is already reading the file, so it must not be replaced."""
        if growing.serving:
            growing.finish(ok=False)
            return False
        unlink(path)
        growing.reset()
        return True

    async def _seal_download(self, job, download_task, entry):
        if not download_task.done():
            await download_task
        elif download_task.cancelled():
            raise PlaybackError("The download was cancelled.", "network", True)
        elif download_task.exception():
            download_task.result()
        await self._complete_stream(job, entry)

    async def _complete_stream(self, job, entry):
        if entry.get("ready"):
            job["progress"] = None
            return entry
        path = self.cache.path(entry["source_name"])
        duration = entry.get("duration")
        try:
            data = await formats.probe(self.settings, path)
            duration = formats.duration_of(data) or duration
        except PlaybackError:
            pass
        entry = self.cache.finish_stream(entry["id"], duration)
        job["_stream_entry"] = entry
        if duration:
            job["duration"] = duration
        job["progress"] = None
        return entry

    async def _finish_streaming(self, job, play_task, download_task, entry):
        pending = {play_task, download_task}
        failed = None
        sealed = False
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if download_task in done and not sealed:
                sealed = True
                if download_task.cancelled():
                    failed = PlaybackError("The download was cancelled.", "network", True)
                else:
                    exc = download_task.exception()
                    if exc:
                        failed = exc if isinstance(exc, PlaybackError) else PlaybackError(
                            f"Unexpected error on the PC: {exc}", "internal", True
                        )
                    else:
                        try:
                            await self._complete_stream(job, entry)
                        except PlaybackError as exc:
                            failed = exc
                if failed is not None and not play_task.done():
                    await self._stop_renderer_quiet(job)
                    play_task.cancel()
            if play_task in done and failed is None and not play_task.cancelled():
                exc = play_task.exception()
                if exc is not None:
                    failed = exc if isinstance(exc, PlaybackError) else PlaybackError(
                        f"Unexpected error on the PC: {exc}", "internal", True
                    )
            if play_task in done and download_task in pending and failed is None:
                if not job.get("_halt_playback") and not job.get("_playback_end"):
                    job["_playback_end"] = ("stopped", "Stopped on the TV.")
                self._set(job, "downloading", "Saving the file for resume")
        if not play_task.done():
            play_task.cancel()
        if failed is not None:
            await self._stop_renderer_quiet(job)
            raise failed
        if job.get("_halt_playback"):
            self._remember(job, force=True)
            self._set(job, "stopped", "Saved for resume.")
        elif job.get("_playback_end") and job["state"] == "downloading":
            state, detail = job["_playback_end"]
            self._set(job, state, detail if state == "ended" else "Saved for resume.")

    async def _cleanup_job_files(self, job):
        download = job.get("_download_task")
        if download is not None and not download.done():
            if job.get("_aborted") or job["state"] in ("cancelled", "failed"):
                download.cancel()
            try:
                await download
            except (asyncio.CancelledError, Exception):
                pass
        token = job.pop("_growing_token", None)
        if token:
            self.growing.pop(token, None)
        entry = job.get("_stream_entry")
        if entry and not entry.get("ready"):
            if self._file_complete(job, entry):
                try:
                    job["_stream_entry"] = self.cache.finish_stream(entry["id"], entry.get("duration"))
                    return
                except PlaybackError:
                    self.cache.remove(entry)
                    return
            self.cache.remove(entry)
            return
        path = job.get("_download_path")
        if path is not None and not job.get("cache_id"):
            unlink(path)

    def _file_complete(self, job, entry):
        path = job.get("_download_path") or self.cache.path(entry["source_name"])
        try:
            return bool(entry.get("source_size")) and path.is_file() and path.stat().st_size == entry["source_size"]
        except OSError:
            return False

    def _download_running(self, job):
        task = job.get("_download_task")
        return task is not None and not task.done()

    async def _stop_renderer_quiet(self, job):
        renderer = job.get("_renderer")
        if renderer is None:
            return
        try:
            await renderer.stop()
        except (UpnpError, PlaybackError, asyncio.TimeoutError, OSError):
            pass

    def _protected_ids(self):
        return {job["cache_id"] for job in self.jobs.values() if job["state"] not in TERMINAL and job.get("cache_id")}

    # Format preparation

    async def _prepare(self, job, entry):
        if entry["play_name"]:
            return entry
        self._set(job, "checking", "Checking the video format")
        source = self.cache.path(entry["source_name"])
        data = await formats.probe(self.settings, source)
        duration = formats.duration_of(data) if data else entry["duration"]
        plan = formats.plan_for(data, entry["mime"])
        summary = json.dumps({"action": plan.action, "reasons": plan.reasons})
        if plan.action == "direct":
            return self.cache.set_playable(entry["id"], entry["source_name"], plan.mime, summary, duration)
        out_name = formats.output_name(source, plan)
        out_part = self.cache.path(out_name + ".part")
        self.cache.ensure_space(entry["source_size"], self._protected_ids() | {entry["id"]})
        label = {"remux": "Repackaging", "audio": "Converting the audio", "transcode": "Converting the video"}[plan.action]
        self._set(job, "preparing", f"{label} for the TV ({'; '.join(plan.reasons)})")

        def progress(fraction):
            job["progress"] = {"fraction": round(fraction, 4)}

        try:
            await formats.convert(self.settings, source, out_part, plan, duration, progress)
        except BaseException:
            unlink(out_part)
            raise
        os.replace(out_part, self.cache.path(out_name))
        job["progress"] = None
        return self.cache.set_playable(entry["id"], out_name, plan.mime, summary, duration)

    # TV

    async def _start_tv(self, job, entry):
        self._set(job, "starting", "Connecting to the TV")
        location = store.query_one("SELECT location FROM renderers WHERE id = ?", (job["renderer_id"],))
        renderer = await self.registry.get(job["renderer_id"], location and location["location"])
        job["_renderer"] = renderer
        job["can_pause"] = renderer.can("Pause")
        job["can_seek"] = renderer.can("Seek")
        job["duration"] = entry["duration"]
        host = self.settings.get("media_host") or local_ip_for(renderer.host)
        try:
            mime = await renderer.pick_mime(entry["mime"])
            if mime != entry["mime"]:
                store.execute("UPDATE cache_entries SET mime = ? WHERE id = ?", (mime, entry["id"]))
            ext = os.path.splitext(entry["play_name"])[1]
            url = self.media.url_for(host, entry["token"], f"movie{ext}")
            metadata = didl_metadata(job["title"], url, mime, entry["play_size"], entry["duration"])
            state, _ = await renderer.transport_info()
            if state not in ("STOPPED", "NO_MEDIA_PRESENT"):
                await renderer.stop()
            self._set(job, "starting", "Sending the video to the TV")
            for attempt in range(2):
                try:
                    await renderer.set_uri(url, metadata)
                    break
                except (UpnpError, asyncio.TimeoutError):
                    if attempt == 1:
                        raise
                    await asyncio.sleep(2)
            await renderer.play()
            await self._confirm_playing(job, renderer)
        except (UpnpError, asyncio.TimeoutError, OSError) as exc:
            raise upnp_error(exc, "The TV did not accept the video") from exc
        if job.get("start_at"):
            try:
                await self._jump_to_start(job, renderer)
            except PlaybackError:
                await self._stop_renderer_quiet(job)
                raise
        elif job.get("start_at") == 0:
            store.execute("DELETE FROM resume_points WHERE movie_id = ?", (job["movie_id"],))
        self.cache.touch(entry["id"])
        self._set(job, "playing", self._playing_detail(job))
        return renderer, url

    async def _confirm_playing(self, job, renderer):
        started = time.monotonic()
        seen_transition = False
        while time.monotonic() - started < START_TIMEOUT:
            state, status = await renderer.transport_info()
            if state == "PLAYING":
                return
            if status == "ERROR_OCCURRED":
                raise PlaybackError("The TV could not play this video; its format may be unsupported.", "unsupported")
            if state == "TRANSITIONING":
                seen_transition = True
                job["detail"] = "The TV is opening the video"
            elif state == "STOPPED" and (seen_transition or time.monotonic() - started > 15):
                raise PlaybackError(
                    "The TV stopped instead of playing. Its format may be unsupported, or playback was declined on the TV.",
                    "refused",
                    retryable=True,
                )
            await asyncio.sleep(1)
        raise PlaybackError("The TV did not start playing. Check the TV for a prompt and try again.", "refused", True)

    async def _jump_to_start(self, job, renderer):
        """Verify that the TV reached the requested position before tracking progress."""
        target = job["start_at"]
        if job["duration"]:
            target = min(target, max(0, job["duration"] - 5))
        if not renderer.can("Seek"):
            raise PlaybackError("This TV does not support seeking. Your saved position was kept.", "unsupported")
        self._set(job, "starting", f"Jumping to {seconds_to_hms(target)}")
        # Some TVs ignore the first seek after Play or only accept one time mode.
        for mode in ("REL_TIME", "REL_TIME", "ABS_TIME"):
            try:
                await renderer.seek(target, mode=mode)
                await asyncio.sleep(3)
                position, _ = await renderer.position_info()
            except (UpnpError, PlaybackError, asyncio.TimeoutError, OSError):
                continue
            if target > 0 and position and abs(position - target) <= 10:
                job["position"] = position
                return
        raise PlaybackError(
            f"The TV did not jump to {seconds_to_hms(target)}. Your saved position was kept.",
            "seek_failed",
            retryable=True,
        )

    def _remember(self, job, force=False):
        """Save or clear the movie's resume point from the last position reported by the TV."""
        position, duration = job.get("position"), job.get("duration")
        if position is None:
            return
        now = store.now()
        if not force and now - job.get("_resume_saved", 0) < RESUME_SAVE_SECONDS:
            return
        job["_resume_saved"] = now
        if duration and position >= min(duration - 90, duration * 0.95):
            store.execute("DELETE FROM resume_points WHERE movie_id = ?", (job["movie_id"],))
        elif position >= RESUME_MIN_SECONDS:
            store.execute(
                """INSERT INTO resume_points (movie_id, position, duration, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(movie_id) DO UPDATE SET position = excluded.position,
                   duration = excluded.duration, updated_at = excluded.updated_at""",
                (job["movie_id"], int(position), duration, now),
            )

    def _progress_open(self, job):
        progress = job.get("progress") or {}
        total = progress.get("total")
        done = progress.get("done") or 0
        return bool(total) and done < total - 4096

    def _playing_detail(self, job):
        if self._progress_open(job):
            return "Playing now. Download is still finishing for Resume."
        return "Playing"

    def _paused_detail(self, job):
        if self._progress_open(job):
            return "Paused. Download is still finishing for Resume."
        return "Paused"

    def _end_playback(self, job, state, detail):
        job["_playback_end"] = (state, detail)
        if self._download_running(job):
            self._set(job, "downloading", "Saving the file for resume")
        else:
            self._set(job, state, detail)

    async def _pause(self, job, seconds):
        """Sleep, but return as soon as playback is stopped from the phone while a download continues."""
        halt = job.get("_halt_event")
        if halt is None:
            await asyncio.sleep(seconds)
            return False
        sleep_task = asyncio.create_task(asyncio.sleep(seconds))
        halt_task = asyncio.create_task(halt.wait())
        try:
            await asyncio.wait({sleep_task, halt_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleep_task, halt_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleep_task, halt_task, return_exceptions=True)
        return bool(job.get("_halt_playback"))

    async def _monitor(self, job, renderer, url):
        failures = 0
        polls = 0
        while True:
            if job.get("_halt_playback") or await self._pause(job, POLL_SECONDS):
                return
            polls += 1
            try:
                state, _ = await renderer.transport_info()
                position, duration = await renderer.position_info()
                current = await renderer.current_uri() if polls % 5 == 0 else None
                failures = 0
            except (UpnpError, PlaybackError, asyncio.TimeoutError, OSError):
                failures += 1
                if failures >= OFFLINE_POLLS:
                    self._end_playback(job, "stopped", "Lost contact with the TV.")
                    return
                job["detail"] = "The TV is not responding"
                continue
            if job.get("_halt_playback"):
                return
            if current and current != url:
                self._end_playback(job, "stopped", "The TV is now playing something else.")
                return
            if state == "PLAYING":
                if position is not None:
                    job["position"] = position
                if duration:
                    job["duration"] = duration
                self._remember(job)
                self._set(job, "playing", self._playing_detail(job))
            elif state == "PAUSED_PLAYBACK":
                if position is not None:
                    job["position"] = position
                self._remember(job)
                self._set(job, "paused", self._paused_detail(job))
            elif state in ("STOPPED", "NO_MEDIA_PRESENT"):
                last, total = job.get("position"), job.get("duration")
                if total and last is not None and last >= min(total - 90, total * 0.95):
                    self._end_playback(job, "ended", "Finished.")
                else:
                    self._end_playback(job, "stopped", "Stopped on the TV.")
                return


class Progress:
    """Download progress with a smoothed speed estimate, reported through the job dict."""

    def __init__(self, job, total):
        self.job = job
        self.total = total
        self.last_time = time.monotonic()
        self.last_done = 0
        self.speed = None
        job["progress"] = {"done": 0, "total": total or None, "speed": None, "eta": None}

    def update(self, done, total):
        total = total or self.total
        now = time.monotonic()
        elapsed = now - self.last_time
        if elapsed >= 1:
            instant = max(0, done - self.last_done) / elapsed
            self.speed = instant if self.speed is None else 0.7 * self.speed + 0.3 * instant
            self.last_time, self.last_done = now, done
        eta = int((total - done) / self.speed) if self.speed and total else None
        self.job["progress"] = {
            "done": done,
            "total": total or None,
            "speed": int(self.speed) if self.speed else None,
            "eta": eta,
        }
