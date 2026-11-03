# Movie World setup plan

Give this file to Codex, Cursor, ChatGPT, or another coding agent with this repository open on Joe's Windows PC. Tell it:

> Follow `docs/setup-plan.md` until the final checklist passes. Do the PC steps yourself. Stop and ask me whenever a step is marked ASK.

The human steps are joining the Telegram channel, creating the API application, typing the Telegram login, accepting the TV prompt, and clicking the Windows administrator prompt. Everything else is a command on this PC.

## Rules for the agent

- Work in the repository root (the folder that contains `server.py`).
- Follow the steps in order. Do not skip a failed check.
- RUN means you execute the command and read the output.
- ASK means you stop and wait for Joe. Do not guess the answer.
- CHECK means you continue only when the condition is true.
- Do not invent an `api_id`, `api_hash`, phone number, TV address, or LAN IP.
- Do not print, log, commit, or paste `api_hash`, the Telegram login code, the two-step password, or the contents of `.local/telegram.session`.
- When you inspect `.local/tvplay.json`, report only whether `telegram_api_id` is present and what `renderer_urls` contains. Never show `telegram_api_hash`.
- Do not commit. Do not push. `.local/` is gitignored and must stay that way.
- Do not forward ports 80 or 8766 on the router.
- Do not edit application code while setting up. This plan only installs and configures.
- `192.168.0.20` and `FIAT-471` belong to another PC. Use this computer's own IPv4 address.
- A full movie download is large. Do not pass `--download` unless Joe agrees.

## 0. Confirm the folder

RUN:

```powershell
Get-Location
Test-Path -LiteralPath .\server.py
Test-Path -LiteralPath .\sql-lite\movie-world.sqlite
Test-Path -LiteralPath .\requirements.txt
```

CHECK: all three tests print `True`. If they do not, ASK Joe for the cloned folder. The private repo is `https://github.com/floodg/movie-world.git`. He needs access before `git clone` will work. Clone it, change into that directory, and repeat this step.

## 1. Python

RUN:

```powershell
py -3 --version
```

If `py` is missing, try `python --version`. CHECK: the version is Python 3.11 or newer. `scripts/start-tv.ps1` prefers `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` and otherwise uses `python` on PATH.

If Python is missing or too old, RUN:

```powershell
winget install --id Python.Python.3.12 -e --scope user
```

ASK Joe to approve the installer if Windows asks. Then open a new terminal so PATH updates, and repeat the version check.

## 2. Playback packages

RUN from the repository root:

```powershell
py -3 -m pip install -r requirements.txt
```

CHECK: the command exits 0 and the installed packages include `telethon` and `async-upnp-client`. If `pip` says the requirements file is missing, you are not in the repository root.

## 3. FFmpeg

RUN:

```powershell
ffmpeg -version
ffprobe -version
```

CHECK: both print a version line. If either command is not found, RUN:

```powershell
winget install --id Gyan.FFmpeg -e
```

Open a new terminal and repeat the version check. If winget cannot install it, ASK Joe to install FFmpeg, or unzip a build so these two files exist:

- `.local\ffmpeg\bin\ffmpeg.exe`
- `.local\ffmpeg\bin\ffprobe.exe`

The app looks on PATH first, then in that folder. Playback still works without FFmpeg, but incompatible files cannot be converted. Record which one is true and continue.

## 4. This PC's address

RUN:

```powershell
Get-NetConnectionProfile | Select-Object Name, InterfaceAlias, NetworkCategory
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
  Select-Object IPAddress, InterfaceAlias
```

Write down the IPv4 address of the home network adapter. That is the address the phone will open. Ignore `127.0.0.1`.

CHECK: `NetworkCategory` for that adapter is `Private`. A guest or isolated Wi-Fi network will hide the TV. If the category is `Public`, ASK Joe before changing it. Only after he agrees, in an Administrator PowerShell window:

```powershell
Set-NetConnectionProfile -InterfaceAlias "NAME" -NetworkCategory Private
```

Use the `InterfaceAlias` from the command above.

## 5. Join the movie channel

ASK Joe to do this on his phone, with the Telegram account this PC will log in as:

1. Open https://t.me/+aPF0d29KPUdlNWQ0
2. Join **Johns Movie Channel**.
3. Open the channel once so it is in the chat list.

The catalogue stores each film as a private post, `https://t.me/c/1669014377/<message>`. Those post links do not join the channel. Playback fails with "cannot see this channel" until this account is a member.

CHECK: Joe confirms he has joined and opened the channel. You cannot complete this step from the PC.

## 6. Create a Telegram API application

ASK Joe to do this in a browser. You do not log in to Telegram for him.

1. Open https://my.telegram.org
2. Enter the phone number of the account that joined the channel, including the country code.
3. Telegram sends a confirmation code inside the Telegram app. Enter that code on the website. That code is not the `api_hash`.
4. Open **API development tools**: https://my.telegram.org/apps
5. If it asks for a new application, use title `Movie World` and short name `movieworld`. Leave the URL blank. Choose Desktop. Create the application.
6. From that same page, copy **App api_id** (digits only) and **App api_hash** (32 characters, `0-9` and `a-f`). They only work as a pair. A new application can take a few minutes before Telegram accepts it.

ASK Joe to keep the hash private. He will type it into the login script in the next step. Do not ask him to paste the hash into the agent chat. If he does paste it, do not repeat it, and tell him to rely on the saved file instead.

## 7. Log this PC in to Telegram

The server and the login script cannot use `.local\telegram.session` at the same time.

RUN:

```powershell
Get-NetTCPConnection -LocalPort 80,8765,8766 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
```

If Movie World is listening, stop that process and CHECK the ports are free before continuing.

The login script asks questions in the terminal (`api_id`, a hidden `api_hash`, the Telegram code, and the two-step password if the account has one). Start it in a terminal Joe can type into:

```powershell
py -3 scripts\telegram_login.py
```

Tell Joe what each prompt means:

- `api_id` is the number from https://my.telegram.org/apps
- `api_hash` is the 32-character hash from that same page. The prompt hides it.
- The next code arrives in the Telegram app. It is not the website code from step 6 if that one was already used.
- If asked for a password, that is Telegram two-step verification.

If your tool cannot attach an interactive terminal, stop and give Joe that command to run himself. Resume when he tells you it printed `Logged in as ...`.

CHECK: the output includes `Logged in as` and a count of chats and channels. Credentials are now in `.local\tvplay.json`. The session file is `.local\telegram.session`. Treat that file like a password.

If Telegram says the api_id/api_hash pair is invalid, the script clears the saved pair. ASK Joe to copy both values again from the same page. Wait a few minutes if the application was just created, then run the command again.

Confirm the account can see one catalogue film. This resolves the file and does not download it:

```powershell
py -3 scripts\telegram_login.py --test-movie 1305
```

CHECK: it prints a file name, size, and mime type for movie 1305 (`Confessions from a Holiday Camp`). If it says the account cannot see the channel, go back to step 5, make sure the channel is open in Telegram, then run `py -3 scripts\telegram_login.py` again so the chat list reloads.

ASK before downloading. Only if Joe wants a sample file on disk:

```powershell
py -3 scripts\telegram_login.py --test-movie 1305 --download
```

That saves under `.local\samples` and can be several gigabytes. It is optional. Normal playback uses `.local\cache` and does not need this sample.

## 8. Check the UPnP TV

The PC finds the TV as a UPnP MediaRenderer and calls AVTransport. The TV then downloads the video from this PC. Samsung sets expose that renderer at `http://<tv-ip>:9197/dmr` when the TV is on and allows this PC.

ASK Joe to do this on the TV:

1. Connect the TV and this PC to the same home network. Not guest Wi-Fi.
2. Turn the TV on and leave it awake.
3. On a Samsung TV: **Settings → General → External Device Manager → Device Connection Manager**. Set access notification to **First Time Only**.
4. Read the TV's IPv4 address: **Settings → General → Network → Network Status**.
5. Watch the TV during the probe. If it asks to allow this PC, accept it.

ASK Joe for that TV IP address. Do not reuse an address from another house.

With the TV still on, RUN:

```powershell
py -3 scripts\tv_probe.py --save
```

CHECK: the output contains a device name, a UDN, and a list of AVTransport actions, and it says the description URL was saved to `.local\tvplay.json`.

If it prints `No LAN devices responded` or no device has AVTransport:

1. Confirm the TV is on, this PC's network is Private, and Joe accepted any allow prompt.
2. ASK Joe if you may enable Network Discovery for the private profile. SSDP uses UDP 1900. If he agrees, in an Administrator PowerShell window:

```powershell
Get-NetFirewallRule -DisplayGroup "Network Discovery" |
  Where-Object { $_.Profile -match "Private" -or $_.Profile -match "Any" } |
  Enable-NetFirewallRule
```

3. Probe the Samsung description page directly. Replace the IP with the one Joe read on the TV:

```powershell
py -3 scripts\tv_probe.py --url http://TV.IP.HERE:9197/dmr --save
```

CHECK again for AVTransport actions. A TV that can only browse a DLNA library, and does not expose AVTransport, cannot be started from the phone. Stop and tell Joe that, including the probe output with no secrets in it.

RUN this and report the URL list only:

```powershell
py -3 -c "import json; from pathlib import Path; p=json.loads(Path(r'.local/tvplay.json').read_text(encoding='utf-8')); print(p.get('renderer_urls'))"
```

Optional picture test. ASK Joe for a short local MP4. Skip this if he does not have one. The command waits for keyboard input, so Joe must run it in a terminal he can type into:

```powershell
py -3 scripts\tv_play_test.py C:\path\to\short.mp4 --url http://TV.IP.HERE:9197/dmr
```

`p` pauses, `r` resumes, `s 30` seeks to 30 seconds, `i` prints status, and `q` stops. CHECK: the script reports the transport as `PLAYING`, or Joe can see the picture. If it never reaches PLAYING, look at the TV for an allow prompt or an unsupported-format message.

## 9. Firewall

Port 80 serves the catalogue to the phone. Port 8766 serves the video to the TV. Both stay on the private network only.

ASK Joe to approve the administrator prompt. In an Administrator PowerShell window, add a rule only when that display name does not already exist:

```powershell
if (-not (Get-NetFirewallRule -DisplayName "Movie World HTTP" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName "Movie World HTTP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Profile Private
}
if (-not (Get-NetFirewallRule -DisplayName "Movie World TV media" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName "Movie World TV media" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Private
}
Get-NetFirewallRule -DisplayName "Movie World HTTP","Movie World TV media" |
  Select-Object DisplayName, Enabled, Profile, Direction, Action
```

CHECK: both rules exist, enabled, inbound, allow, private profile.

## 10. Start Movie World

ASK Joe before registering the sign-in task. He does not need it for the first test. If he wants Movie World to start whenever he signs in to Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-startup.ps1
```

That creates the port 80 rule, registers a task named `Movie World`, and starts the server. The port 8766 rule from step 9 is still required.

For a first run, start it yourself and leave the window open:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-tv.ps1
```

Windows will ask for administrator approval because port 80 needs it. The script then runs:

```text
python server.py --host 0.0.0.0 --port 80 --tv-playback
```

CHECK: the console prints both of these lines:

- `Movie World at http://0.0.0.0:80`
- `Play on TV enabled. The TV fetches video on port 8766.`

If port 80 is already taken, report the other process. Do not change the port unless Joe asks. The phone address has no port number only while the server is on port 80.

Leave that window running. Closing it stops the server.

## 11. Phone check

ASK Joe to connect his phone to the same home network, open this exact address in the phone browser, and play one film:

```text
http://THIS-PC-IPV4
```

Use the address from step 4. No port number.

On a film's details he should see a TV list and **Play on TV**. If the list says no TVs were found, the TV must be on, then he presses **Search again**. The saved `renderer_urls` entry is used when a fresh search finds nothing.

CHECK: Joe confirms the TV shows the picture, or he reads you the on-screen error. Do not declare success from the server log alone.

A compatible file starts once the first portion has downloaded. The rest keeps downloading into `.local\cache`. One title plays at a time. Subtitles and audio-track selection are not available. The PC offers **Resume from …** or **Start over** after a title has been stopped past the first minute.

## Final checklist

Report this to Joe. Use yes or no, plus the address and the TV name. Leave secrets out.

- Python 3 and `requirements.txt` installed
- `ffmpeg` and `ffprobe` found, or Joe was told conversion will be skipped
- Windows network profile is Private, and the phone address is `http://<this-pc-ipv4>`
- Joe joined https://t.me/+aPF0d29KPUdlNWQ0
- `py -3 scripts\telegram_login.py` printed `Logged in as`
- `py -3 scripts\telegram_login.py --test-movie 1305` resolved a video file
- `py -3 scripts\tv_probe.py --save` found AVTransport and saved `renderer_urls`
- Private firewall rules allow TCP 80 and TCP 8766
- The server printed `Play on TV enabled`
- Joe played one title and the TV showed a picture

If a row is no, stop on that step. The matching section above is the fix.
