# Movie World

A local movie catalogue browser built with Python, SQLite, and vanilla HTML, CSS, and JavaScript.

## Features

- Browse genres with movies sorted by IMDb rating.
- Search the catalogue by title or actor.
- View movie details, posters, and backdrops.
- Save favourites in your browser's local storage.
- Enrich movie metadata and cast information using TMDB.

Titles tagged with the Adult genre are excluded from the browser.

## Run locally

Install Python 3. No third-party Python packages or frontend build tools are required.

From the project directory, run:

```sh
python server.py
```

Open http://127.0.0.1:8765 in your browser. Stop the server with Ctrl+C.

The server reads the included `sql-lite/movie-world.sqlite` catalogue in read-only mode and listens on the local computer only. Poster and backdrop images require internet access. Favourites are stored per browser and are not written to the database.

## View on a TV over the local network

On FIAT-471 the server is at `http://192.168.0.20`. Phones and the TV do not resolve the Windows computer name, so use the IPv4 address. Other Windows PCs on the same network can use `http://FIAT-471`. Connect the TV to the same network and open the address in its web browser. The PC must stay awake while watching.

On another computer, run `ipconfig` and use that machine's IPv4 address the same way.

The server listens on port 80, so the address has no port number. It is plain HTTP, which avoids the certificate warning that some TV browsers cannot accept. The address can change with your router's assignments. A Windows firewall rule must allow inbound TCP port 80 on the private network. Only run one server on that port at a time. Binding port 80 needs an administrator approval the first time.

To start it now, or again after it has been stopped:

```powershell
.\scripts\start-tv.ps1
```

To start it automatically each time you sign in to Windows:

```powershell
.\scripts\install-startup.ps1
```

### Samsung RU7100 TV interface

For the Samsung **UA65RU7100WXXY** (2019), open **http://192.168.0.20/tv**
on the TV. Both `/tv` and `/tv/` work.
Start the server with the command above; restart an already running server after
updating `server.py` so it recognizes the new route.

The TV interface uses the existing native HTML/CSS/JavaScript stack, targeting
Samsung's 2019 Tizen 5.0 / Chromium 63 engine without a CDN or frontend dependencies.
It preserves genres, title and actor searches, cast links, details, favourites and
Telegram links. Arrow keys move between controls, Enter activates them, and the
remote Back key (or Escape) closes movie details. Left/right inside a search field
move the text cursor. The TV browser may handle Back itself before sending a key
to the page. Telegram links still require a compatible external service/app.

Favourites share the main interface's storage key when both pages use the same
browser and origin. Different devices, hostnames, schemes or ports have separate
storage. If storage is blocked, favourites remain available for the current visit.

After editing the main interface, regenerate the TV assets with
`python scripts/build_tv.py`. The generated files are committed so running the
server needs only Python. `web/tv/compat.js` is the maintained compatibility layer.
This replaces unsupported syntax, newer DOM methods, flex gaps, and dialog behavior;
the main interface at `/` remains independent. Actual TV verification is still
needed, including remote-key delivery.

For another computer:

```sh
python server.py --host 0.0.0.0 --port 80
```

## Set up Play on TV

Pick a title on the phone and the PC downloads it from Telegram, then tells the
Samsung TV to play it through UPnP/DLNA. The TV pulls the video from the PC over the
local network. Closing the phone page does not stop the download or playback.

Do this once on each PC. Credentials, the Telegram session, the TV address and the
download cache stay in `.local/`, which is not in Git. Each person uses their own
Telegram account and their own API application.

To have Codex, Cursor, or ChatGPT do the PC steps, open this folder in that tool and tell it to follow `docs/setup-plan.md`.

### 1. Install the playback tools

Python 3.12 is what `scripts/start-tv.ps1` looks for first. Any current Python 3 on `PATH` also works.

```powershell
pip install -r requirements.txt
```

Install FFmpeg and make sure `ffmpeg` and `ffprobe` are on `PATH`. Or unzip a build so both exist at `.local/ffmpeg/bin/ffmpeg.exe` and `.local/ffmpeg/bin/ffprobe.exe`. Without FFmpeg, files play as downloaded and incompatible ones cannot be converted.

### 2. Join the movie channel

On the phone, with the Telegram account this PC will log in as, open:

https://t.me/+aPF0d29KPUdlNWQ0

That invite joins **Johns Movie Channel**. Open the channel once so it appears in the chat list. The catalogue stores each film as a private post in that channel (`https://t.me/c/1669014377/<message>`). Those post links do not join the channel. Playback fails with "cannot see this channel" until this account is a member and the login below has loaded the chat list.

### 3. Create a Telegram API application

Telegram's client API is separate from the normal app login. Create an application for this account:

1. Open https://my.telegram.org in a browser.
2. Enter the phone number of the account that joined the channel, including the country code.
3. Telegram sends a confirmation code inside the Telegram app (Saved Messages / login code). Enter it on the website. This is not the `api_hash`.
4. Open **API development tools** (https://my.telegram.org/apps).
5. If the page asks for a new application, fill in an app title and a short name, for example `Movie World` and `movieworld`. The URL can be left blank. Choose Desktop, then create the application.
6. Copy **App api_id** (a number) and **App api_hash** (32 characters, `0-9` and `a-f`) from that same page. They only work as a pair. A newly created app can take a few minutes before Telegram accepts it.

Keep the `api_hash` private. Do not put it in the repo, chat, or this README. It is saved only in `.local/tvplay.json` on this PC. `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` environment variables override that file when they are set.

### 4. Log the PC in to Telegram

Stop Movie World first. The server and this script cannot use the session file at the same time.

```powershell
python scripts/telegram_login.py
```

Enter the `api_id` and `api_hash` when asked. Telegram then sends a login code to the app. If the account has two-step verification, enter that password as well. The script prints the account name and how many chats and channels it can see. Johns Movie Channel should be among them.

The session is stored at `.local/telegram.session`. Treat that file like a password: anyone with it can act as this Telegram account. It is not committed.

Check one catalogue title. `1305` is one real film in the database; any other movie id works the same way.

```powershell
python scripts/telegram_login.py --test-movie 1305 --download
```

That resolves the Telegram file, downloads it to `.local/samples`, and prints the tracks when FFmpeg is installed. A later Play on TV of the same title reuses the normal cache and does not depend on this sample.

### 5. Check the UPnP TV

The PC does not cast a picture. It discovers the TV as a UPnP MediaRenderer and calls AVTransport (set the URL, play, pause, seek, stop). The TV then downloads the video from the PC. Samsung sets, including the UA65RU7100, expose that renderer on port `9197` when the TV is on and allows the PC to connect.

On the TV:

1. Connect the TV and the PC to the same home network. A guest or isolated Wi-Fi network hides UPnP devices from each other.
2. On Windows, set that network to **Private** (Settings → Network & internet → the connected network → Private).
3. Turn the TV on and leave it awake. Deep standby often turns the network renderer off.
4. On a Samsung TV: **Settings → General → External Device Manager → Device Connection Manager**. Set access notification to **First Time Only** (or Always). The first time this PC contacts the TV, accept the allow prompt on the TV.
5. Note the TV's IPv4 address: **Settings → General → Network → Network Status**.

On the PC, with the TV still on:

```powershell
python scripts/tv_probe.py --save
```

The probe searches for about five seconds (MediaRenderer v1/v2, AVTransport, and `ssdp:all`). A usable TV prints its name, UDN, model, description URL, and a list of AVTransport actions. `--save` writes that description URL into `.local/tvplay.json` under `renderer_urls`, so later playback still works when multicast discovery does not.

If the search prints nothing:

- Confirm the TV is on, the PC's network is Private, and both are on the same LAN.
- Allow Network Discovery / SSDP (UDP 1900) through Windows Firewall for the private profile. Discovery is multicast; a firewall that blocks it makes the TV invisible even when playback by a direct URL would work.
- Probe the Samsung description page directly, using the TV's IP:

```powershell
python scripts/tv_probe.py --url http://192.168.0.50:9197/dmr --save
```

Replace `192.168.0.50` with the TV's address. The device must list AVTransport actions. A TV that only browses a DLNA library, and does not expose AVTransport, cannot be started from the phone.

To prove the TV will actually play, use a short local MP4:

```powershell
python scripts/tv_play_test.py C:\path\to\short.mp4
```

After it starts, `p` pauses, `r` resumes, `s 30` seeks to 30 seconds, `i` prints status, and `q` stops. If the TV never reports PLAYING, look at the TV screen for an allow prompt or an unsupported-format message. Pass `--url http://<tv-ip>:9197/dmr` here too when discovery fails.

### 6. Open the firewall and start the server

The phone browses the catalogue on port 80. The TV fetches video on plain HTTP port 8766 (`--media-port` changes it). Never forward either port on the router. In an Administrator PowerShell window:

```powershell
New-NetFirewallRule -DisplayName "Movie World HTTP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Profile Private
New-NetFirewallRule -DisplayName "Movie World TV media" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Private
```

`scripts/install-startup.ps1` creates the port 80 rule and registers a sign-in task. Add the port 8766 rule yourself if you use that script. Allowing `python.exe` on private networks covers both ports as well.

Start playback support (this also restarts elevated so it can bind port 80):

```powershell
.\scripts\start-tv.ps1
```

Or, from an already elevated shell:

```powershell
python server.py --host 0.0.0.0 --port 80 --tv-playback
```

The console prints `Play on TV enabled. The TV fetches video on port 8766.` On the phone, open `http://<this-pc-ip>` (no port), open a movie, choose the TV, and press **Play on TV**. If the list says no TVs were found, turn the TV on and press **Search again**. The saved `renderer_urls` entry is used when a fresh search finds nothing.

Settings in `.local/tvplay.json`: `cache_quota_gb` (default 50), `disk_headroom_gb`, `max_file_gb`, `media_host` (the PC's LAN IP, normally auto-detected), `renderer_urls` (TV description URLs if discovery fails, for example `http://<tv-ip>:9197/dmr`), and `media_port`. Downloads are cached in `.local/cache`. The least recently used inactive files are removed when the quota is reached.

A compatible title starts on the TV once the first portion has downloaded. The rest keeps
downloading, and the finished file stays in the cache. Stopping the TV while that download
is still going leaves it running so the file is kept. Resume uses that finished file and asks
the TV to jump to the saved time. Resuming a title that is not cached yet waits for the full
download, so the jump lands in the right place. An MP4 whose index is at the end of the file
has that index fetched first, then the picture starts while the rest downloads. Videos that
need converting still wait until the download finishes.

The PC remembers where each title was stopped (after the first minute, until the last few
minutes). Its details then offer **Resume from …** or **Start over**.

One title plays at a time; starting another asks before replacing it. Subtitles and
audio-track selection are not supported yet; the TV plays the file's default tracks.

## Update catalogue metadata

The optional enrichment scripts update the database using the TMDB API. Back up the database before running them. Set `TMDB_API_KEY` in your shell; the scripts do not automatically load `.env`.

PowerShell:

```powershell
$env:TMDB_API_KEY = "your-tmdb-api-key"
python scripts/enrich_johns_movies.py sql-lite/movie-world.sqlite --limit 10
python scripts/enrich_cast.py sql-lite/movie-world.sqlite --limit 10
```

Omit `--limit` to process all eligible entries. Both scripts support `--delay` to control the pause between requests and `--help` for options. Cast enrichment uses TMDB IDs already stored by metadata enrichment. An API key is not needed to browse the existing catalogue.

Keep API keys private. The local `.env` file is excluded from Git.

## Project layout

- `server.py` — local HTTP server and catalogue API.
- `tvplay/` — Play on TV: Telegram downloads, cache, media server and TV control.
- `web/` — browser interface and styles.
- `sql-lite/movie-world.sqlite` — movie catalogue database.
- `scripts/` — Telegram login, UPnP TV probe, startup, TV page build, and TMDB enrichment.
- `docs/` — supporting project documents.

## Attribution

This product uses the TMDB API but is not endorsed or certified by TMDB.
