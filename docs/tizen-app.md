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

The first command creates the unsigned project at `.local/tizen-app`. It builds
from the current main interface through the existing compatibility transformations,
without overwriting tracked `/tv` assets. Generated files and certificates stay
outside Git. Node.js and the pinned esbuild build dependency are required only
for building; esbuild lowers newer JavaScript to Chromium 63 syntax. The pairing
command displays a private pairing token: enter it on
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

On first launch enter the PC's private IPv4 HTTP URL and pairing token. **PC
Settings** changes them later. A router DHCP reservation keeps the address stable.
The token is stored in app-local storage; uninstalling clears app settings and
local favourites. Browser favourites have separate storage.

## Controls and acceptance

- Arrows and Enter browse. Select **Play on this TV** in movie details.
- Enter or Play/Pause toggles playback; dedicated media keys also work.
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

## Backend interface

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
cross-origin reads, while app mutations require the pairing token; app code does
not depend on the TV sending an Origin header. No Telegram credentials enter the
TV app. No database migration is needed.

## Verification

```powershell
python scripts/test_tizen.py -v
python scripts/build_tizen.py
node --check .local/tizen-app/app.js
node --check .local/tizen-app/compat.js
node --check .local/tizen-app/tizen.js
```

The backend tests use temporary SQLite state and a loopback HTTP server.

Official references: [installation and testing](https://developer.samsung.com/smarttv/develop/faq/application-testing.html),
[CLI](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/command-line-interface.html),
[AVPlay](https://developer.samsung.com/smarttv/develop/guides/multimedia/media-playback/using-avplay.html),
[remote keys](https://developer.samsung.com/smarttv/develop/guides/user-interaction/remote-control.html),
[TV security limitations](https://developer.samsung.com/smarttv/develop/faq/security.html).
