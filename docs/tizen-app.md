# Samsung Movie World app

Targets the UA65RU7100 (2019, Tizen 5.0). This is a privately installed web app;
the PC still supplies the catalogue, Telegram download, conversion and media.
The app uses AVPlay rather than sending playback to the DLNA renderer.

## Build and pair

Run from the repository root:

```powershell
npm ci --prefix tizen --ignore-scripts
python scripts/build_tizen.py
python scripts/pair_tizen.py
```

The build command creates the unsigned project at `.local/tizen-app` from the
dedicated sources in `tizen/`, without overwriting the website or `/tv` assets.
Generated files and certificates stay outside Git. Node.js and the pinned build
dependencies are required only for building; esbuild targets Chromium 63, resvg
renders the Android-derived launcher vector to a 117-pixel PNG, and the build
bundles Roboto/Roboto Condensed with their font licences. The pairing command
displays a private 6-digit pairing code: enter it on
the TV, not in source code. `--rotate` revokes the previous token for future API
requests; already issued media capability URLs retain their existing lifetime.
The current token is shared by installations paired to this PC.

Restart the PC server to load the new backend, using the existing startup script.
Do this when existing playback is stopped. The server must have playback enabled
and Telegram configured as described in README. Allow the catalogue HTTP port
(normally 80) and media port (normally 8766) on the private LAN.

## Sign and install

Install Tizen Studio with Samsung TV Extension and Samsung Certificate Extension.
On the TV, enable Developer Mode in Apps, enter the development PC's LAN address,
and restart the TV. Connect it with Device Manager. Create a Samsung TV certificate
profile including the device DUID, then use **Permit to install applications**.
Keep the author certificate backed up privately for future updates.

With the SDK CLI on PATH, from the repository root:

```powershell
tizen build-web -- .local/tizen-app
tizen package -t wgt -s MovieWorld -- .local/tizen-app/.buildResult
tizen install -n MovieWorld.wgt -t YOUR_DEVICE -- .local/tizen-app/.buildResult
tizen run -p MovieWorld.TV -t YOUR_DEVICE
```

`MovieWorld` is the certificate profile name in this example. Use the actual
generated `.wgt` filename if the SDK names it differently. `tizen list` lists
connected device targets. You can alternatively import `.local/tizen-app` in
Tizen Studio and build/run it there.

On first launch enter the PC's private IPv4 HTTP URL and 6-digit pairing code. The **TV
settings** cast icon changes them later. A router DHCP reservation keeps the address stable.
The token is stored in app-local storage; uninstalling clears app settings and
local favourites. Browser favourites have separate storage.

## Controls and acceptance

- Arrows and Enter browse. Select **Play** or **Resume** on Home or movie details.
- Enter or Play/Pause toggles playback; dedicated media keys also work.
- During playback press **Down** (or the Subtitle key) for the subtitle menu.
  Choose Off or an embedded track. The app remembers on/off and prefers English
  when starting the next movie with subtitles enabled. Timing buttons adjust by
  half a second, up to 30 seconds either way, where the TV supports it.
  Back closes this menu without stopping the movie. Movies without subtitle
  tracks show an explicit empty state. Playback starts with subtitles off until
  enabled once. Track support and timing require physical-TV acceptance.
- Left/right or Rewind/Fast Forward seeks 30 seconds. Back stops playback or
  cancels preparation and returns to details. Back at the catalogue root exits.
- Resume is automatic when the PC has a saved position. This first version waits
  for the completed movie file, then reuses existing format preparation.
- App backgrounding stops playback and attempts a final progress report. If the
  TV disappears, the PC releases the playback session after 90 seconds without
  progress. During download, closing the app can leave preparation finishing on
  the PC; the ready session then expires. Cached files remain reusable.

Hardware acceptance is still required: first play a short known-compatible MP4
from the existing cache (the PC interface can upload one associated with a title),
then a real downloaded film. Verify picture, sound, buffering, seeking, pause,
resume, Back, standby, PC loss, wrong token and restart recovery. Test a second
DLNA playback afterwards. Emulator or mocked tests cannot establish actual TV
codec support, app-origin networking, certificate acceptance or standby behavior.

## Subtitle preparation

Direct playback retains the original embedded subtitle tracks. When the PC must
convert video or audio, it now preserves supported subtitle streams in Matroska
(SRT, ASS/SSA, WebVTT, PGS and VobSub), converting MP4 timed text to SRT as needed.
Languages and track dispositions are carried over by FFmpeg. Other subtitle
codecs are omitted from conversions; preserving a track does not guarantee the
TV can decode it. No video encoding is added solely to display subtitles.
Previously converted cached movies are rebuilt once from their retained source
on their next playback. The PC server must be restarted to load this change.

DLNA casting receives these embedded tracks too, but subtitle selection remains
in the TV's own playback controls and needs hardware testing. The web catalogue
cannot draw subtitles over a separately cast video. Online subtitle search,
external SRT upload and automatic transcription are not implemented yet.

## Backend interface

The read-only UI endpoints match Android's catalogue presentation:

- `GET /api/tizen-catalogue/home?day=312` returns the daily featured movie, top
  24 rated titles, genre chips, and six popular genre rails of 24 titles each.
- `GET /api/tizen-catalogue/search?q=...&mode=title|actor&genre=...&years=1990-2000`
  returns up to 120 matching movies, with combined actor/title, genre and year filters.
- In Actor mode, typing at least two letters calls `GET /api/actors?q=...` and
  displays up to 12 names with visible-movie counts, ordered like the main web app.
  Choosing a name supplies `actor_id` to the search endpoint, loading that actor's
  complete filmography (without the 120-title cap), subject to genre/year filters.
  Enter selects a sole/exact match; arrows navigate suggestions and Back dismisses
  the dropdown. Editing the name clears the selected actor; stale replies are ignored.
- `GET /api/movies/{id}/similar` returns 24 related titles ranked by shared genres.

The UI ports the Kotlin app's amber/near-black theme, condensed headings,
MOVIE/WORLD wordmark, hero actions, 2:3 rounded posters, rating badges, progress
lines, Home/Search/My List tabs, details, cast and related-title sections. Remote
focus outlines and the PC connection form are TV-specific adaptations. Telegram
and local Android file-picker actions are omitted because this app plays through
the PC. Favourites and recently watched history remain local to the installation;
resume points are read from the PC. Android cloud-account sync is not added here.

The icon uses the exact paths and colours from the Kotlin app's
`ic_launcher_foreground.xml`, flattened over its adaptive-icon Night background.
Design references are in `../movie-world-android/app/src/main/java/com/movieworld/android/ui/`
and `../movie-world-android/app/src/main/res/`; the build does not require that
sibling checkout at runtime or build time.

All app requests below are JSON POSTs with `X-Movie-World-Token`:

- `/api/tizen/prepare`: `movie_id`, optional `idempotency_key` and `start_at`.
- `/api/tizen/{id}/status`: preparation status and, when ready, `media_url` and
  `start_at` inside `job`. Normal public job responses omit the media URL.
- `/api/tizen/{id}/progress`: `state` (`playing`, `paused`, `stopped`, `ended`,
  `failed`), `position` and `duration` in seconds. Send every ten seconds and on
  transitions. Completion clears the saved resume point.
- `/api/tizen/{id}/cancel`: cancel preparation or stop the session.

Sessions are tied to the requesting TV IPv4 address. One global active job remains
the default: another title or device receives 409 until playback stops. Existing
DLNA endpoints retain browser origin checks. Catalogue responses permit
cross-origin reads, while app mutations require the 6-digit pairing code; app code does
not depend on the TV sending an Origin header. No Telegram credentials enter the
TV app. No database migration is needed.

## Verification

```powershell
python scripts/test_tizen.py -v
python scripts/build_tizen.py
node --check .local/tizen-app/app.js
node --check .local/tizen-app/tizen.js
node tizen/node_modules/@playwright/test/cli.js test --config=tizen/playwright.config.cjs
```

The backend tests use temporary SQLite state and a loopback HTTP server. Browser
tests use deterministic catalogue/artwork fixtures and installed Google Chrome,
and cover navigation, theme/fonts/icon, list persistence, details, search filters,
focus trapping, and preparation cancellation. Preview screenshots are saved as
`.local/tizen-home.png`, `.local/tizen-detail.png` and `.local/tizen-search.png`.

Official references: [installation and testing](https://developer.samsung.com/smarttv/develop/faq/application-testing.html),
[CLI](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/command-line-interface.html),
[AVPlay](https://developer.samsung.com/smarttv/develop/guides/multimedia/media-playback/using-avplay.html),
[remote keys](https://developer.samsung.com/smarttv/develop/guides/user-interaction/remote-control.html),
[TV security limitations](https://developer.samsung.com/smarttv/develop/faq/security.html).
