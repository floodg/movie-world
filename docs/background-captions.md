# SubDL and background captions

In Android TV settings, select **SubDL** under Subtitle downloads, enter a key from
https://subdl.com/panel/api, and press **Save and test**. OpenSubtitles remains
available. Keys are encrypted on the phone. Downloads are bounded ZIP archives;
only single-file SRT releases are accepted. Existing subtitle timing and caching
controls apply to both providers.

Automatic Whisper is **off by default**. In Android TV settings, use **PC captions →
Automatic Whisper captions** to enable or disable it on the PC. The switch requires
the home PC to be reachable and applies to all devices using that PC. Turning it
off stops active inference, leaves movie downloads running and keeps existing
caption files. Turning it on affects preparation jobs that have not completed;
it does not backfill already completed favourites.

Adding a movie to favourites on Android schedules a persistent WorkManager command
to the configured PC server. Wi-Fi and an available PC are required; the command
retries with backoff while offline. On mobile data or away from home Wi-Fi, the
favourite saves immediately and its PC command waits until the PC is reachable
over Wi-Fi; this command does not download the movie to the phone. Android may
delay retries to conserve battery. Removing the favourite before delivery skips
the command. Removing it after delivery does not delete the download.

Browser favourite additions received through personal sync also queue preparation.
Existing favourites are not automatically backfilled. The PC must run with
`--tv-playback` and have its Telegram login configured.

Install `python -m pip install -r requirements-whisper.txt` in the server's Python
environment. The first caption job downloads the open Whisper `small` model into
`.local/whisper-models`. In `.local/tvplay.json`, `whisper_model` selects the model
and `whisper_threads` defaults to 2. Inference uses CPU int8 in a separate process,
one movie at a time. Captions are English (Whisper translates non-English speech).

The persistent queue lives in `.local/jobs.sqlite`. It reuses complete cached
movies, waits for an existing streaming download to finish, and never replaces or
stops an active playback session. Downloads use the existing cache quota and free
disk limits. A playback request for a movie being downloaded by the queue waits
for that download. Caption inference protects its source file from cache eviction.
Finished movies remain subject to the normal cache eviction policy; generated SRTs
are retained separately by Telegram source ID.

Generated captions are used on subsequent PC DLNA playback when no phone SRT was
supplied. In the phone's subtitle controls, **Get PC captions** copies ready
captions to its existing subtitle cache for phone-hosted playback. Caption changes
do not alter an already playing session. The native Samsung app retains its
existing embedded subtitle controls.

LAN endpoints:

- `POST /api/playback/prepare` with `{"movie_id": 123}` queues preparation.
- `GET /api/playback/preparation/123` returns queued/downloading/transcribing/ready/failed.
- `GET /api/playback/captions/123` returns `{"srt": "..."}` or null when unavailable.

Interrupted queue work resumes after server restart. A failed job exposes its
error through the status endpoint; submitting preparation again retries it.
Playback and caption generation remain independent: a caption failure leaves a
completed movie cached and playable. No captions are uploaded to a hosted AI API.
