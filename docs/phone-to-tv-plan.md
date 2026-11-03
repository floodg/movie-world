# Phone → Telegram download → TV playback plan

Investigated 3 November 2026. Planning only; application behavior is unchanged.

## Findings

- `server.py` is a Python standard-library catalogue server with GET-only APIs. It has no Telegram client, download worker, media endpoint, or UPnP controller.
- `web/app.js` adds the existing Telegram link in the movie details actions. This is the insertion point for Play on TV and job progress. Reuse the main mobile browser interface.
- The read-only catalogue contains 5,935 Telegram links: 5,933 private `/c/<channel>/<message>` links and two links with only one path segment. The latter need explicit validation and must not be assumed to identify video messages.
- README identifies a Samsung UA65RU7100WXXY, with FIAT-471 previously reachable at 192.168.0.20. These are documented values, not freshly verified addresses.
- Samsung lists DLNA on this TV model. That does not prove remote AVTransport control is enabled or accessible on the actual TV.
- A five-second SSDP MediaRenderer discovery from this PC returned no devices. This does not establish incompatibility: the TV might be off, multicast/firewall rules may interfere, or the device may advertise another service version/type. Broader discovery and a manual description URL are needed during the first feasibility test.
- Existing uncommitted TV-interface changes were left intact.

## Recommended architecture

Phone browser → catalogue API on PC → background playback job → authenticated Telegram download → completed local file → HTTP media server on PC → TV native DLNA player.

The PC sends UPnP control commands; the TV pulls the actual video over HTTP. The phone only selects and controls playback. Closing the phone page must not stop a download or playback.

Keep the current Python/vanilla-JavaScript stack. Add Telethon for Telegram and async-upnp-client for discovery/control, with pinned tested versions. Use one persistent asyncio worker loop, with a thread-safe interface from the existing HTTP request handlers. Do not create a Telegram client or event loop per request.

Prefer a separate plain-HTTP media listener on a private-network port (proposed 8766). The TV must receive a reachable LAN IPv4 URL, never localhost or the existing self-signed HTTPS URL. Phone browsing can retain HTTPS if its certificate is accepted; a private-network HTTP browser listener is an alternative if phone certificate handling is troublesome.

## Phase 1: prove the two external integrations

1. With the TV on and on the same LAN, discover using MediaRenderer v1/v2 and ssdp:all; inspect device description and service documents. Check AVTransport actions and ConnectionManager protocol information. Save the selected device UDN, rather than only an IP address. Allow a configured description URL when discovery fails.
2. Serve a short known-compatible MP4 from this PC with HEAD and byte-range support. Send SetAVTransportURI with appropriate DIDL-Lite metadata, then Play. Verify picture, sound, pause, stop and seeking. Any TV approval prompt is handled on the TV.
3. Create a local Telegram API application and authenticate the user's account once on this PC, including two-step verification if enabled. Use an account already entitled to read the private channel. Populate channel entities/dialogs so Telethon has the access hash; a numeric channel ID from a URL alone is insufficient.
4. Resolve one catalogue message and confirm it contains downloadable video. Download one representative file and inspect its container/video/audio tracks with ffprobe. Check albums or text-only posts explicitly rather than silently choosing unrelated media.
5. Play that downloaded file on the TV. This is the go/no-go gate before implementing the complete phone workflow.

If the TV can browse DLNA libraries but exposes no usable remote playback service, a DLNA media server can offer manual TV selection, but that does not satisfy automatic Play on TV. Reassess the renderer/control method before building the full UI.

## Phase 2: reliable local downloads

- Store credentials, Telegram session, downloads and job database beneath `.local/`, outside the web root and excluded from Git. Never expose Telegram login/session data through the browser API.
- Keep the existing catalogue read-only. Use a separate writable SQLite database for jobs, cache records and settings.
- Accept catalogue movie IDs, resolve links server-side, and validate the expected Telegram channel/message form. Do not accept arbitrary download URLs or filesystem paths.
- Download to a generated `.part` filename; atomically rename only on completion. First version waits for the entire download before playback. Growing files introduce seek/container issues and are a later optimization.
- Show bytes, total size where known, speed and estimated remaining time. Support cancellation, retries, Telegram flood-wait handling, inaccessible/deleted messages, missing media, network loss and insufficient disk space.
- Deduplicate repeated taps by movie/source and reuse completed cached files. One active TV session and a bounded download queue for the first release. A second movie must not silently interrupt the first.
- Set a configurable cache quota with LRU eviction of inactive files only. Protect files in use and reserve disk headroom. On restart, reconcile partial jobs and offer safe retry; do not promise resume unless implemented and verified.

## Phase 3: serving and TV control

- Serve only registered cache objects through opaque capability URLs. No directory listing or arbitrary path access. Use streaming reads, correct MIME types, Content-Length, HEAD, Range/206, invalid-range/416 responses and DLNA headers where requested.
- Query renderer capabilities and attach compatible metadata. Call Stop when needed, then SetAVTransportURI and Play, with bounded retries/timeouts. Do not report Playing solely because Play returned successfully: confirm transport status.
- Poll transport state initially; support subscriptions later if useful. Report device offline, playback refused and unsupported media distinctly. Support pause/resume/stop and seek only when advertised and verified.
- Restrict control access to paired phone sessions using a local pairing secret, POST-only mutation endpoints, same-origin checks and CSRF protection. Media capability URLs must work without cookies because the TV will not have the phone session.
- Restrict discovery/control destinations to local devices, validate discovered URLs, and reject redirects/addresses outside the allowed LAN. Permit only private-network firewall access; no router port forwarding.

## Phase 4: phone workflow

Suggested endpoints:

- GET /api/tvs — discovered/known renderers and availability.
- POST /api/playback — movie_id, renderer_id, idempotency key; returns a job ID promptly.
- GET /api/playback/{id} — state, progress, title, device and actionable error.
- POST /api/playback/{id}/cancel — cancel download/preparation.
- POST /api/playback/{id}/control — pause, resume, stop, or supported seek.

Movie details show Play on TV, a remembered TV selector, and status: Queued → Downloading → Checking format → Preparing (if needed) → Starting TV → Playing. Include paused, cancelled, stopped, ended and failed states. Poll job status and recover the active job after page reload. Keep the existing Telegram link available.

## Format handling

Start with direct playback of compatible completed files. Extension alone does not establish compatibility; inspect codec/profile/level, resolution, bitrate and audio tracks. Test a real catalogue sample against the TV.

Add FFmpeg only where required: remux without re-encoding when possible; convert an unsupported audio track while copying compatible video; transcode video only as a fallback after testing PC capacity. Treat subtitles and audio-track selection as explicit follow-up scope. Downloads and conversions can delay playback substantially; show real progress and do not promise instant start.

## Acceptance checks

- On the phone, select a catalogue movie and TV; the video downloads on FIAT-471 and begins playing on the TV without its browser.
- Phone reload/disconnection does not cancel work. Repeated taps do not create duplicate downloads. Cached replay avoids downloading again.
- Confirm HEAD, full GET, byte ranges, seeking, invalid ranges and concurrent media reads without loading the whole file into RAM.
- Confirm inaccessible channel/message, non-video post, Telegram rate limits, disk full, cancelled download, PC restart, TV off and TV rejection produce clear recoverable states.
- Check unauthorized control, cross-origin requests, path traversal, arbitrary URLs and cache cleanup while a file is playing.
- Verify actual picture and sound on the RU7100. Automated control tests cannot establish real codec compatibility.

## Delivery order and estimate

1. Hardware/Telegram proof: approximately half a day if access and discovery cooperate; otherwise resolve that first.
2. Download jobs, cache and range-serving endpoint: 1–2 days.
3. TV controls, mobile actions, progress and pairing: 1–2 days.
4. Real-device validation and reliability fixes: approximately one day.

Rough total: 3–6 development days after feasibility is established. Codec conversion, subtitle support or unusual TV behavior can add time. This is a planning estimate, not a benchmark.

## Sources

- Samsung model specifications: https://www.samsung.com/au/tvs/uhd-4k-tv/ru7100-65-inch-crystal-uhd-smart-tv-ua65ru7100wxxy/
- Telethon client, message access and downloads: https://docs.telethon.dev/en/stable/modules/client.html
- async-upnp-client implementation and usage: https://github.com/StevenLooman/async_upnp_client
- UPnP AVTransport specification: https://upnp.org/specs/av/UPnP-av-AVTransport-v1-Service.pdf
