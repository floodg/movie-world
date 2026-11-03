#!/usr/bin/env python3
"""One-time Telegram login for Play on TV, plus an optional download test.

Create an API application at https://my.telegram.org (API development tools), then run:

    python scripts/telegram_login.py
    python scripts/telegram_login.py --test-movie 123

The session is stored in .local/telegram.session; keep it private. Use an account that
already belongs to the catalogue's private channels. Stop server.py while this runs:
the session file can only be used by one process at a time.
"""
import argparse
import asyncio
import getpass
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient, errors  # noqa: E402

from tvplay import config, formats  # noqa: E402
from tvplay.errors import PlaybackError  # noqa: E402
from tvplay.links import parse_message_link  # noqa: E402
from tvplay.telegram_dl import TelegramSource  # noqa: E402


def ask_credentials(settings):
    if config.telegram_credentials(settings):
        return
    print("Enter the API credentials from https://my.telegram.org -> API development tools.")
    api_id = input("api_id: ").strip()
    api_hash = getpass.getpass("api_hash (hidden): ").strip()
    if not api_id.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", api_hash):
        raise SystemExit("Invalid api_id or api_hash (the hash is 32 characters, 0-9 and a-f).")
    settings["telegram_api_id"] = int(api_id)
    settings["telegram_api_hash"] = api_hash
    config.save(settings)
    print(f"Saved to {config.SETTINGS_FILE}")


async def login(settings):
    api_id, api_hash = config.telegram_credentials(settings)
    client = TelegramClient(str(config.TELEGRAM_SESSION), api_id, api_hash)
    await client.start(password=lambda: getpass.getpass("Two-step verification password: "))
    me = await client.get_me()
    print(f"Logged in as {me.first_name or ''} (@{me.username or 'no username'}).")
    dialogs = await client.get_dialogs()
    channels = [d for d in dialogs if d.is_channel]
    print(f"Loaded {len(dialogs)} chats, {len(channels)} channels.")
    await client.disconnect()


def catalogue_link(movie_id):
    con = sqlite3.connect(f"file:{config.CATALOGUE_DB.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT COALESCE(title, source_title), telegram_url FROM movies WHERE id = ?", (movie_id,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"Movie {movie_id} not found")
    return row


async def test_download(settings, movie_id, download):
    title, url = catalogue_link(movie_id)
    print(f"Movie {movie_id}: {title}\n  {url}")
    channel_id, message_id = parse_message_link(url)
    source = TelegramSource(settings)
    try:
        video = await source.resolve(channel_id, message_id)
        print(f"  file: {video.name}  {video.size / 1e9:.2f} GB  {video.mime}  duration={video.duration}s")
        if not download:
            return None
        target_dir = config.LOCAL / "samples"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{channel_id}_{message_id}{video.ext}"
        part = target.with_name(target.name + ".part")

        def progress(done, total):
            print(f"\r  downloading {done / 1e6:,.0f} / {total / 1e6:,.0f} MB", end="", flush=True)

        await source.download(video, part, progress)
        part.replace(target)
        print(f"\n  saved {target}")
        return target
    finally:
        await source.close()


def ffprobe(settings, path):
    tool = formats.tool(settings, "ffprobe")
    if tool is None:
        print("ffprobe not found; install FFmpeg to inspect tracks.")
        return
    result = subprocess.run(
        [tool, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    data = json.loads(result.stdout or "{}")
    print(f"  container: {data.get('format', {}).get('format_name')}")
    for stream in data.get("streams", []):
        print(
            f"  #{stream.get('index')} {stream.get('codec_type')}: {stream.get('codec_name')} "
            f"{stream.get('profile', '')} level={stream.get('level', '')} "
            f"{stream.get('width', '')}x{stream.get('height', '')} {stream.get('pix_fmt', '')} "
            f"ch={stream.get('channels', '')} lang={stream.get('tags', {}).get('language', '')}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-movie", type=int, help="Catalogue movie ID to resolve")
    parser.add_argument("--download", action="store_true", help="Also download it to .local/samples and ffprobe it")
    args = parser.parse_args()
    settings = config.load()
    ask_credentials(settings)
    if args.test_movie is None:
        try:
            asyncio.run(login(settings))
        except errors.ApiIdInvalidError:
            settings["telegram_api_id"] = None
            settings["telegram_api_hash"] = None
            config.save(settings)
            raise SystemExit(
                "Telegram rejected the api_id/api_hash pair, so they were cleared.\n"
                "Copy both from the same app at https://my.telegram.org/apps and run this again.\n"
                "A newly created app can take a few minutes before Telegram accepts it."
            )
        return
    try:
        path = asyncio.run(test_download(settings, args.test_movie, args.download))
    except PlaybackError as exc:
        raise SystemExit(f"  {exc.message}")
    if path is not None:
        ffprobe(settings, path)
        print(f"Play it on the TV with: python scripts/tv_play_test.py \"{path}\"")


if __name__ == "__main__":
    main()
