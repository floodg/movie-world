"""UPnP/DLNA MediaRenderer discovery and AVTransport control."""
import asyncio
import socket
import time
from xml.sax.saxutils import escape

import aiohttp
from async_upnp_client.client import UpnpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.const import HttpResponse
from async_upnp_client.exceptions import (
    UpnpActionResponseError,
    UpnpConnectionError,
    UpnpConnectionTimeoutError,
    UpnpError,
)
from async_upnp_client.search import async_search

from .errors import PlaybackError
from .netutil import is_lan_address, lan_url_host

SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaRenderer:2",
    "urn:schemas-upnp-org:service:AVTransport:1",
    "ssdp:all",
)
DLNA_FLAGS = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
MIME_ALIASES = {
    "video/x-matroska": ("video/x-matroska", "video/x-mkv", "video/mkv"),
    "video/mp4": ("video/mp4", "video/mpeg4"),
    "video/x-msvideo": ("video/x-msvideo", "video/avi", "video/msvideo"),
    "video/mp2t": ("video/mp2t", "video/vnd.dlna.mpeg-tts", "video/mpeg"),
}
UPNP_ERROR_TEXT = {
    701: "The TV refused the transition.",
    714: "The TV does not accept this video type.",
    716: "The TV could not open the video URL.",
    718: "The TV rejected the playback instance.",
}


class LanRequester(UpnpRequester):
    """HTTP requester that only talks to private-network hosts and never follows redirects."""

    def __init__(self, timeout=8):
        self._timeout = timeout
        self._session = None

    async def async_http_request(self, http_request):
        if lan_url_host(http_request.url) is None:
            raise UpnpConnectionError(f"Refusing non-LAN URL {http_request.url}")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        timeout = aiohttp.ClientTimeout(total=http_request.timeout or self._timeout)
        try:
            async with self._session.request(
                http_request.method,
                http_request.url,
                headers=dict(http_request.headers or {}),
                data=http_request.body,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                raw = await response.read()
                headers = dict(response.headers)
                status = response.status
        except asyncio.TimeoutError as exc:
            raise UpnpConnectionTimeoutError(str(exc)) from exc
        except aiohttp.ClientError as exc:
            raise UpnpConnectionError(str(exc)) from exc
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            body = raw.decode("latin-1")
        return HttpResponse(status, headers, body)

    async def close(self):
        if self._session is not None:
            await self._session.close()


def seconds_to_hms(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def hms_to_seconds(value):
    if not value or value in ("NOT_IMPLEMENTED", ""):
        return None
    try:
        parts = [float(part) for part in value.split(":")]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return int(total)


def protocol_info(mime):
    return f"http-get:*:{mime}:{DLNA_FLAGS}"


def didl_metadata(title, url, mime, size=None, duration=None):
    attrs = [f'protocolInfo="{escape(protocol_info(mime))}"']
    if size:
        attrs.append(f'size="{int(size)}"')
    if duration:
        attrs.append(f'duration="{seconds_to_hms(duration)}.000"')
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
        '<item id="movie-world-0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.item.videoItem.movie</upnp:class>"
        f"<res {' '.join(attrs)}>{escape(url)}</res>"
        "</item></DIDL-Lite>"
    )


def _find_service(device, name):
    for candidate in device.all_devices:
        for service in candidate.all_services:
            if f":service:{name}:" in service.service_type:
                return service
    return None


def upnp_error(exc, fallback):
    if isinstance(exc, UpnpActionResponseError):
        text = UPNP_ERROR_TEXT.get(exc.error_code)
        if text:
            code = "unsupported" if exc.error_code == 714 else "refused"
            return PlaybackError(text, code, retryable=exc.error_code != 714)
        return PlaybackError(f"{fallback} (TV error {exc.error_code}).", "refused", retryable=True)
    if isinstance(exc, (UpnpConnectionError, UpnpConnectionTimeoutError, asyncio.TimeoutError, OSError)):
        return PlaybackError("The TV is not responding. Check it is on and on the same network.", "tv_offline", retryable=True)
    return PlaybackError(f"{fallback}: {exc}", "refused", retryable=True)


class Renderer:
    def __init__(self, device, location):
        self.device = device
        self.location = location
        self.udn = device.udn
        self.name = device.friendly_name or device.model_name or "TV"
        self.avtransport = _find_service(device, "AVTransport")
        self.connection_manager = _find_service(device, "ConnectionManager")
        self.host = lan_url_host(location)

    @property
    def actions(self):
        return sorted(self.avtransport.actions) if self.avtransport else []

    def can(self, action):
        return self.avtransport is not None and self.avtransport.has_action(action)

    def summary(self):
        return {
            "id": self.udn,
            "name": self.name,
            "model": " ".join(filter(None, [self.device.manufacturer, self.device.model_name])),
            "location": self.location,
            "can_pause": self.can("Pause"),
            "can_seek": self.can("Seek"),
        }

    async def _call(self, action, timeout=10, **kwargs):
        if not self.can(action):
            raise PlaybackError(f"The TV does not support {action}.", "unsupported")
        return await asyncio.wait_for(
            self.avtransport.async_call_action(action, InstanceID=0, **kwargs), timeout
        )

    async def sink_protocols(self):
        if self.connection_manager is None or not self.connection_manager.has_action("GetProtocolInfo"):
            return []
        result = await asyncio.wait_for(
            self.connection_manager.async_call_action("GetProtocolInfo"), 10
        )
        sink = result.get("Sink") or ""
        return [item.strip() for item in sink.split(",") if item.strip()]

    async def pick_mime(self, mime):
        """Choose the MIME alias the renderer advertises; fall back to mime when it reports nothing."""
        try:
            protocols = await self.sink_protocols()
        except (UpnpError, asyncio.TimeoutError, OSError):
            return mime
        advertised = set()
        for item in protocols:
            parts = item.split(":")
            if len(parts) >= 3 and parts[0] == "http-get":
                advertised.add(parts[2].lower())
        if not advertised or "*" in advertised:
            return mime
        for alias in MIME_ALIASES.get(mime, (mime,)):
            if alias in advertised:
                return alias
        return mime

    async def transport_info(self):
        result = await self._call("GetTransportInfo")
        return result.get("CurrentTransportState"), result.get("CurrentTransportStatus")

    async def position_info(self):
        if not self.can("GetPositionInfo"):
            return None, None
        result = await self._call("GetPositionInfo")
        return hms_to_seconds(result.get("RelTime")), hms_to_seconds(result.get("TrackDuration"))

    async def current_uri(self):
        if not self.can("GetMediaInfo"):
            return None
        result = await self._call("GetMediaInfo")
        return result.get("CurrentURI")

    async def set_uri(self, url, metadata):
        await self._call("SetAVTransportURI", timeout=20, CurrentURI=url, CurrentURIMetaData=metadata)

    async def play(self):
        await self._call("Play", timeout=20, Speed="1")

    async def pause(self):
        await self._call("Pause")

    async def stop(self):
        await self._call("Stop")

    async def seek(self, seconds):
        await self._call("Seek", timeout=15, Unit="REL_TIME", Target=seconds_to_hms(seconds))


async def ssdp_locations(timeout=5, targets=SEARCH_TARGETS):
    """Search the LAN and return {location: search target} for private-network responders."""
    found = {}

    async def on_response(headers):
        location = headers.get("location")
        if location and lan_url_host(location):
            found.setdefault(location, headers.get("st") or headers.get("nt") or "")

    sources = [(address, 0) for address in local_lan_addresses()] or [None]
    await asyncio.gather(
        *(
            async_search(on_response, timeout=timeout, search_target=target, source=source)
            for target in targets
            for source in sources
        ),
        return_exceptions=True,
    )
    return found


def local_lan_addresses():
    """Private IPv4 addresses of this PC, so SSDP is sent on every LAN interface."""
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if is_lan_address(address) and not address.startswith("169.254."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


async def load_renderer(factory, location):
    if lan_url_host(location) is None:
        raise PlaybackError("Renderer address is not on the local network.", "bad_renderer")
    device = await factory.async_create_device(location)
    renderer = Renderer(device, location)
    for service in (renderer.avtransport, renderer.connection_manager):
        if service is not None and lan_url_host(service.control_url) is None:
            raise PlaybackError("Renderer control URL is not on the local network.", "bad_renderer")
    return renderer


class RendererRegistry:
    """Discovered renderers, keyed by UDN. Used only from the worker event loop."""

    def __init__(self, manual_urls=()):
        self.requester = LanRequester()
        self.factory = UpnpFactory(self.requester, non_strict=True)
        self.manual_urls = list(manual_urls)
        self.renderers = {}
        self.seen_at = {}
        self.last_discovery = 0.0
        self._lock = asyncio.Lock()

    async def discover(self, timeout=5, known_locations=()):
        async with self._lock:
            locations = set(await ssdp_locations(timeout))
            locations.update(self.manual_urls)
            locations.update(known_locations)
            results = await asyncio.gather(
                *(self._load(location) for location in locations), return_exceptions=True
            )
            now = time.time()
            for renderer in results:
                if isinstance(renderer, Renderer) and renderer.avtransport is not None:
                    self.renderers[renderer.udn] = renderer
                    self.seen_at[renderer.udn] = now
            self.last_discovery = now
            return [self.renderers[udn] for udn, seen in self.seen_at.items() if seen == now]

    async def _load(self, location):
        return await asyncio.wait_for(load_renderer(self.factory, location), 10)

    async def get(self, udn, location=None):
        renderer = self.renderers.get(udn)
        if renderer is not None:
            return renderer
        if location:
            try:
                renderer = await self._load(location)
            except (UpnpError, PlaybackError, asyncio.TimeoutError, OSError):
                renderer = None
            if renderer is not None and renderer.udn == udn and renderer.avtransport is not None:
                self.renderers[udn] = renderer
                self.seen_at[udn] = time.time()
                return renderer
        await self.discover(timeout=4)
        renderer = self.renderers.get(udn)
        if renderer is None:
            raise PlaybackError("The selected TV was not found. Check it is on.", "tv_offline", retryable=True)
        return renderer

    async def close(self):
        await self.requester.close()
