#!/usr/bin/env python3
"""Discover DLNA MediaRenderers on the LAN and print their AVTransport capabilities.

Turn the TV on first. If discovery finds nothing, pass the TV's device description URL
with --url (for Samsung TVs this is often http://<tv-ip>:9197/dmr).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from async_upnp_client.client_factory import UpnpFactory  # noqa: E402

from tvplay import config  # noqa: E402
from tvplay.renderer import LanRequester, load_renderer, ssdp_locations  # noqa: E402


async def probe(args):
    settings = config.load()
    print(f"Searching for {args.timeout}s (MediaRenderer v1/v2, AVTransport, ssdp:all)...")
    found = await ssdp_locations(args.timeout)
    for location, target in sorted(found.items()):
        print(f"  {location}  [{target}]")
    locations = set(found) | set(args.url) | set(settings.get("renderer_urls") or [])
    if not locations:
        print("No LAN devices responded. Check the TV is on, the PC firewall allows UDP 1900/SSDP, "
              "and retry with --url http://<tv-ip>:9197/dmr")
        return 1
    requester = LanRequester()
    factory = UpnpFactory(requester, non_strict=True)
    renderers = []
    try:
        for location in sorted(locations):
            try:
                renderer = await asyncio.wait_for(load_renderer(factory, location), 10)
            except Exception as exc:  # report and continue with other devices
                print(f"\n{location}: could not read description ({exc})")
                continue
            if renderer.avtransport is None:
                continue
            renderers.append(renderer)
            print(f"\n== {renderer.name} ==")
            print(f"  UDN:       {renderer.udn}")
            print(f"  Model:     {renderer.device.manufacturer} {renderer.device.model_name}")
            print(f"  Location:  {location}")
            print(f"  AVTransport actions: {', '.join(renderer.actions)}")
            try:
                state, status = await renderer.transport_info()
                print(f"  Transport: {state} ({status})")
            except Exception as exc:
                print(f"  Transport: unavailable ({exc})")
            try:
                sink = await renderer.sink_protocols()
                video = sorted({item.split(":")[2] for item in sink if item.count(":") >= 3 and "video" in item})
                print(f"  Sink video MIME types ({len(sink)} protocol entries): {', '.join(video) or 'none reported'}")
            except Exception as exc:
                print(f"  Sink protocols: unavailable ({exc})")
    finally:
        await requester.close()
    if not renderers:
        print("\nNo device exposes AVTransport, so automatic Play on TV is not possible with these devices.")
        return 1
    if args.save:
        urls = sorted(set(settings.get("renderer_urls") or []) | {r.location for r in renderers})
        settings["renderer_urls"] = urls
        config.save(settings)
        print(f"\nSaved renderer description URLs to {config.SETTINGS_FILE}")
    if args.json:
        print(json.dumps([r.summary() for r in renderers], indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--url", action="append", default=[], help="Device description URL to probe directly")
    parser.add_argument("--save", action="store_true", help="Remember found renderers in .local/tvplay.json")
    parser.add_argument("--json", action="store_true")
    raise SystemExit(asyncio.run(probe(parser.parse_args())))


if __name__ == "__main__":
    main()
