#!/usr/bin/env python3
"""Serve one local video file over HTTP and play it on a DLNA renderer.

Use a short, known-compatible MP4 first, then a file downloaded from Telegram.
Interactive commands after playback starts: p (pause), r (resume), s <seconds> (seek),
i (status), q (stop and quit).
"""
import argparse
import asyncio
import mimetypes
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from async_upnp_client.client_factory import UpnpFactory  # noqa: E402

from tvplay import config  # noqa: E402
from tvplay.media import MediaFile, MediaServer  # noqa: E402
from tvplay.netutil import local_ip_for  # noqa: E402
from tvplay.renderer import LanRequester, didl_metadata, load_renderer, ssdp_locations  # noqa: E402

MIME_BY_SUFFIX = {".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4", ".ts": "video/mp2t"}


async def choose_renderer(factory, urls):
    locations = set(urls) or set(await ssdp_locations(5))
    for location in sorted(locations):
        try:
            renderer = await asyncio.wait_for(load_renderer(factory, location), 10)
        except Exception:
            continue
        if renderer.avtransport is not None:
            return renderer
    return None


async def run(args):
    path = args.file.resolve()
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    settings = config.load()
    mime = MIME_BY_SUFFIX.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "video/mp4"
    token = secrets.token_urlsafe(24)
    media = MediaFile(key="test", path=path, mime=mime)
    server = MediaServer(lambda value: media if value == token else None, port=args.port)
    server.start()
    requester = LanRequester()
    factory = UpnpFactory(requester, non_strict=True)
    try:
        renderer = await choose_renderer(factory, args.url or settings.get("renderer_urls") or [])
        if renderer is None:
            print("No renderer with AVTransport found. Run scripts/tv_probe.py first.")
            return 1
        host = args.host or settings.get("media_host") or local_ip_for(renderer.host)
        url = server.url_for(host, token, path.name)
        mime = await renderer.pick_mime(mime)
        media.mime = mime
        print(f"Renderer: {renderer.name} ({renderer.location})")
        print(f"Serving:  {url} as {mime}")
        try:
            await renderer.stop()
        except Exception:
            pass
        await renderer.set_uri(url, didl_metadata(path.stem, url, mime, path.stat().st_size))
        await renderer.play()
        for _ in range(30):
            state, status = await renderer.transport_info()
            print(f"  transport: {state} ({status})")
            if state == "PLAYING":
                break
            await asyncio.sleep(1)
        else:
            print("The TV did not report PLAYING. Check the TV screen for a prompt or an error.")
        loop = asyncio.get_running_loop()
        while True:
            command = (await loop.run_in_executor(None, input, "command [p/r/s N/i/q]: ")).strip()
            try:
                if command == "p":
                    await renderer.pause()
                elif command == "r":
                    await renderer.play()
                elif command.startswith("s "):
                    await renderer.seek(int(command[2:]))
                elif command == "i":
                    print(await renderer.transport_info(), await renderer.position_info())
                elif command == "q":
                    await renderer.stop()
                    break
            except Exception as exc:
                print(f"  failed: {exc}")
        return 0
    finally:
        await requester.close()
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--url", action="append", default=[], help="Renderer description URL")
    parser.add_argument("--host", help="LAN IPv4 the TV should fetch from (auto-detected by default)")
    parser.add_argument("--port", type=int, default=8766)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
