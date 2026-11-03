"""Plain-HTTP media listener for the TV: opaque capability URLs, HEAD and byte ranges."""
import os
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import config
from .netutil import is_lan_address

CHUNK = 256 * 1024
TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
CONTENT_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


@dataclass
class MediaFile:
    key: object
    path: Path
    mime: str
    size: int | None = None
    wait: object = None
    read_at: object = None
    streaming: bool = False


class GrowingFile:
    """A cache file still being written. HTTP readers block until the bytes they need land on disk."""

    def __init__(self, path, size):
        self.path = path
        self.size = size or 0
        self.written = 0
        self.generation = 0
        self.serving = False
        self.finished = False
        self.ok = False
        self.tail_offset = None
        self.tail = b""
        self.extra = 0
        self._cond = threading.Condition()

    def progress(self, done, total=None):
        with self._cond:
            self.written = max(self.written, int(done or 0))
            if total:
                self.size = int(total)
            self._cond.notify_all()

    def note_prefix(self, count):
        """Bytes of the contiguous front of the file that are flushed to disk."""
        with self._cond:
            self.written = max(self.written, int(count))
            self._cond.notify_all()

    def note_extra(self, count):
        """Bytes of the end-of-file index downloaded before the front reaches them."""
        with self._cond:
            self.extra = max(0, int(count))
            self._cond.notify_all()

    def set_tail(self, offset, data):
        with self._cond:
            self.tail_offset = int(offset)
            self.tail = bytes(data)
            self.extra = len(self.tail)
            self._cond.notify_all()

    def accounted(self):
        """Bytes obtained so far, counting the index once while it is still only held aside."""
        with self._cond:
            if self.tail_offset is not None and self.written >= self.tail_offset:
                return self.written
            extra = self.extra
            if self.tail_offset is not None:
                extra = max(extra, len(self.tail))
            total = self.written + extra
            return min(self.size, total) if self.size else total

    def start_serving(self):
        with self._cond:
            self.serving = True

    def reset(self):
        """A failed attempt is being discarded and started over. Only valid before the TV is reading."""
        with self._cond:
            self.written = 0
            self.generation += 1
            self.finished = False
            self.ok = False
            self.tail_offset = None
            self.tail = b""
            self.extra = 0
            self._cond.notify_all()

    def finish(self, ok):
        with self._cond:
            self.finished = True
            self.ok = ok
            if ok:
                try:
                    self.written = self.path.stat().st_size
                except OSError:
                    pass
            self._cond.notify_all()

    def read_at(self, position, length):
        """Return bytes that are already available at position, or b'' when the reader must wait."""
        with self._cond:
            written = self.written
            tail_offset = self.tail_offset
            tail = self.tail
        if position < written:
            try:
                with self.path.open("rb") as handle:
                    handle.seek(position)
                    return handle.read(min(length, written - position))
            except OSError:
                return b""
        if tail_offset is not None and position >= tail_offset:
            rel = position - tail_offset
            if rel < len(tail):
                return bytes(tail[rel:rel + length])
        return b""

    def _byte_ready(self, index):
        if index < self.written:
            return True
        return self.tail_offset is not None and self.tail_offset <= index < self.tail_offset + len(self.tail)

    def wait_until(self, offset):
        """Block until the byte at offset - 1 is readable, or the download has finished without it."""
        while True:
            with self._cond:
                if offset <= 0 or self._byte_ready(offset - 1):
                    return True
                if self.tail_offset is None:
                    try:
                        have = self.path.stat().st_size if self.path.is_file() else 0
                    except OSError:
                        have = 0
                    if have >= offset:
                        self.written = max(self.written, have)
                        return True
                if self.finished:
                    return False
                self._cond.wait(timeout=0.4)


class ActiveReads:
    """Counts open media reads per cache key so cleanup can skip files being streamed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}

    def enter(self, key):
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1

    def leave(self, key):
        with self._lock:
            count = self._counts.get(key, 0) - 1
            if count <= 0:
                self._counts.pop(key, None)
            else:
                self._counts[key] = count

    def busy(self, key):
        with self._lock:
            return self._counts.get(key, 0) > 0


def parse_range(header, size):
    """Return (start, end) inclusive, None for no/ignored range, or 'invalid' for 416."""
    if not header:
        return None
    match = RANGE.match(header.strip())
    if match is None:
        return None if "," in header else "invalid"
    first, last = match.groups()
    if first == "" and last == "":
        return "invalid"
    if first == "":
        length = int(last)
        if length == 0 or size == 0:
            return "invalid"
        return max(0, size - length), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start >= size or (last != "" and int(last) < start):
        return "invalid"
    return start, end


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"


class MediaServer:
    def __init__(self, resolver, host="0.0.0.0", port=8766, reads=None, allow_loopback=True):
        self.resolver = resolver
        self.reads = reads or ActiveReads()
        self.allow_loopback = allow_loopback
        self.httpd = ExclusiveHTTPServer((host, port), self._handler())
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = None

    def url_for(self, host, token, name):
        return f"http://{host}:{self.port}/media/{token}/{quote(name)}"

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="media-server", daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                print("media %s - %s" % (self.address_string(), fmt % args), flush=True)

            def do_GET(self):
                self.serve(send_body=True)

            def do_HEAD(self):
                self.serve(send_body=False)

            def fail(self, status, extra=None):
                self.send_response(status)
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def serve(self, send_body):
                if not is_lan_address(self.client_address[0], server.allow_loopback):
                    self.fail(403)
                    return
                parts = [part for part in unquote(urlparse(self.path).path).split("/") if part]
                if len(parts) not in (2, 3) or parts[0] != "media" or not TOKEN.match(parts[1]):
                    self.fail(404)
                    return
                media = server.resolver(parts[1])
                if media is None or not media.path.is_file():
                    self.fail(404)
                    return
                server.reads.enter(media.key)
                try:
                    self.send_file(media, send_body)
                except (ConnectionError, TimeoutError):
                    self.close_connection = True
                finally:
                    server.reads.leave(media.key)

            def send_file(self, media, send_body):
                try:
                    size = media.path.stat().st_size if media.size is None else media.size
                except OSError:
                    self.fail(404)
                    return
                byte_range = parse_range(self.headers.get("Range"), size)
                if byte_range == "invalid":
                    self.fail(416, {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
                    return
                if byte_range is None:
                    start, end, status = 0, size - 1, 200
                else:
                    start, end = byte_range
                    status = 206
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", media.mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                if self.headers.get("getcontentFeatures.dlna.org") == "1":
                    self.send_header("contentFeatures.dlna.org", CONTENT_FEATURES)
                if self.headers.get("transferMode.dlna.org") or self.headers.get("getcontentFeatures.dlna.org"):
                    self.send_header("transferMode.dlna.org", "Streaming")
                self.end_headers()
                if media.streaming:
                    config.log_playback(
                        f"media {self.client_address[0]} {self.command} {status} "
                        f"range={self.headers.get('Range') or '-'} {start}-{end}/{size}"
                    )
                if not send_body or length == 0:
                    return
                if media.wait is None:
                    self._write_ready(media, start, length)
                    return
                self._write_growing(media, start, length)

            def _write_ready(self, media, start, length):
                with media.path.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = handle.read(min(CHUNK, remaining))
                        if not chunk:
                            self.close_connection = True
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

            def _write_growing(self, media, start, length):
                """Send a file that is still downloading. Advertised size is the final size; reads wait for bytes."""
                position = start
                remaining = length
                stalls = 0
                while remaining > 0:
                    try:
                        if media.read_at is not None:
                            chunk = media.read_at(position, min(CHUNK, remaining))
                        else:
                            with media.path.open("rb") as handle:
                                handle.seek(position)
                                chunk = handle.read(min(CHUNK, remaining))
                    except OSError:
                        self.close_connection = True
                        break
                    if not chunk:
                        if not media.wait(position + 1):
                            self.close_connection = True
                            break
                        stalls += 1
                        if stalls > 20:
                            self.close_connection = True
                            break
                        time.sleep(0.05)
                        continue
                    stalls = 0
                    self.wfile.write(chunk)
                    position += len(chunk)
                    remaining -= len(chunk)

        return Handler
