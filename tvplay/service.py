"""Runs the JobManager on one persistent event loop thread for the threaded HTTP server."""
import asyncio
import concurrent.futures
import threading

from .jobs import JobManager

CALL_TIMEOUT = 20


class TvPlayback:
    def __init__(self, settings):
        self.settings = settings
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="tvplay-worker", daemon=True)
        self.manager = None

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self):
        self.thread.start()
        self.manager = self.call(self._create(), timeout=30)

    async def _create(self):
        manager = JobManager(self.settings)
        await manager.start()
        return manager

    def call(self, coroutine, timeout=CALL_TIMEOUT):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("The PC took too long to respond.")

    def stop(self):
        if self.manager is not None:
            try:
                self.call(self.manager.close(), timeout=10)
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
