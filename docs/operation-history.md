# Operation history

Open **Operation logs** in the PC catalogue header, or `/operations.html` on the
PC server. In Android, open **TV settings → Operation history**. This is separate
from the movie watch history.

The page shows timestamps, source (PC server or Android app), severity, operation,
message, and movie ID when available. Filter by source or severity and search
messages, operations or movie IDs. The PC page loads older records in batches.
Android shows the latest 300 local and 200 PC events and keeps phone history
available when the PC is offline. Use Refresh for new entries.

Recorded operations include:

- PC server startup/shutdown, failed HTTP requests and accepted mutation requests.
- PC playback state transitions and errors.
- Background preparation queued, downloading, transcribing, completed or failed.
- Android app opening, favourite changes, playback requests/status and failures.
- Android subtitle search/download starts, completions and errors.
- Android commands sent to the PC and background preparation retries.

The phone stores events locally in its private `operations.sqlite`, and uploads
unsent events to the configured PC over the existing Wi-Fi-only LAN connection.
WorkManager retries offline uploads with backoff and periodically checks every
15 minutes; opening the history screen also attempts a sync. IDs make upload
retries idempotent. Android remains usable with no PC.

The PC stores the latest 5,000 received events in `.local/operations.sqlite`; the
phone retains 2,000. Logging starts with this update; existing console logs are
not imported. Successful polling requests are omitted to avoid filling history.
Events are structured operational summaries, not raw HTTP bodies, subtitle files,
Telegram messages or credentials. URLs and credential-like strings in error
messages are redacted. These pages are intended for the same trusted LAN as the
catalogue; browser access to the history API requires the same origin.

`GET /api/operations` supports `source`, `level`, `q` and `before` filters.
`POST /api/operations` accepts at most 50 Android events in a 128 KB body and
returns accepted IDs. Client-supplied events are always labelled Android.
