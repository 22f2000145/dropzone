# Dropzone

**A tiny, clean, self-hosted way to move links, text, files and whole folders between your phone, your PC and your friends — from anywhere, with nothing to install on their side.**

Built as a replacement for the "send it to myself on WhatsApp / Google Chat, then open it on desktop" workflow. Dropzone runs on your PC; anyone with the link + PIN opens it in a browser (same Wi-Fi or across the country) and everything sent shows up for everyone within ~2 seconds.

- ✅ Single Python file, **standard library only** — nothing to `pip install`
- ✅ **Links, text, files, photos, videos and whole folders** — no compression, original quality
- ✅ **Download any folder — or any selection of items — as one ZIP**
- ✅ **PIN login** so only people you trust get in
- ✅ **Works over the internet** via a free Cloudflare tunnel (HTTPS), **nothing for friends to install**
- ✅ **Permanent link** (GitHub Pages) that always points to your Dropzone
- ✅ **Resumable chunked uploads** — big files survive flaky mobile data
- ✅ Everything is stored on **your own PC**
- ✅ Starts with one command: `dropzone`

---

## Table of contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Quick start (3 commands)](#quick-start-3-commands)
5. [Sharing with friends anywhere](#sharing-with-friends-anywhere)
6. [Permanent link](#permanent-link)
7. [Daily usage](#daily-usage)
8. [Features in detail](#features-in-detail)
9. [Command-line reference](#command-line-reference)
10. [Same Wi-Fi only (optional Windows + WSL setup)](#same-wi-fi-only-optional-windows--wsl-setup)
11. [Where data is stored](#where-data-is-stored)
12. [HTTP API](#http-api)
13. [Troubleshooting](#troubleshooting)
14. [Security](#security)
15. [Limitations](#limitations)
16. [Uninstall](#uninstall)
17. [Project layout](#project-layout)

---

## How it works

```
  Friend (Ranchi)                                   Your PC (Dhanbad)
  ┌────────────┐   https://you.github.io/dropzone   ┌───────────────────────┐
  │  browser   │ ──▶ (permanent link, GitHub Pages) │  dropzone.py          │
  └────────────┘          │ forwards to            │   ├─ web UI + API     │
                          ▼                         │   ├─ data/ (files)   │
              https://xyz.trycloudflare.com ◀──────▶│   └─ cloudflared ─────┼──▶ Cloudflare
                  (Cloudflare tunnel, HTTPS)        └───────────────────────┘
  Your phone ─── same link, or http://PC-IP:8765 on the same Wi-Fi
```

- Your PC runs `dropzone.py` (web server + UI) and `cloudflared`, which opens an **outgoing** encrypted tunnel to Cloudflare. No router/port-forwarding setup is needed and it works even behind mobile hotspots and WSL's NAT.
- Cloudflare gives a random `https://….trycloudflare.com` address. Because it changes each restart, Dropzone writes the current address into a public GitHub gist, and a tiny page on **GitHub Pages** reads it and forwards visitors there — that's your **permanent link**.
- Everyone sees the same shared list. Each item is tagged with the sender's name and device (📱 / 💻).

---

## Requirements

- **Python 3.8+** (Kali / Ubuntu / most Linux distros already have it)
- Internet on the PC (for sharing with friends) — or just the same Wi-Fi for local use
- For the permanent link: a GitHub account + the [GitHub CLI](https://cli.github.com) (`gh auth login`)
- `cloudflared` is **downloaded automatically** the first time (≈40 MB, from Cloudflare's official GitHub releases) into `dropzone/bin/`

Friends need **only a browser**.

---

## Installation

```bash
git clone https://github.com/22f2000145/dropzone.git ~/dropzone
chmod +x ~/dropzone/start.sh ~/dropzone/dropzone.py

# make the `dropzone` command available everywhere
mkdir -p ~/.local/bin
ln -sf ~/dropzone/start.sh ~/.local/bin/dropzone
which dropzone          # should print ~/.local/bin/dropzone
```

`~/.local/bin` is on `PATH` by default on Kali / Ubuntu. If not, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` / `~/.zshrc`.

---

## Quick start (3 commands)

```bash
dropzone --set-pin      # 1. choose a PIN (6+ digits)          — once
dropzone --setup-link   # 2. create your permanent link         — once
dropzone                # 3. start (every time)
```

Output:

```
  Dropzone is running

  PC:       http://localhost:8765
  Wi-Fi:    (unavailable - WSL is in NAT mode; use the internet link)
  Internet: starting secure tunnel...

  Click 'Links' on the page for QR codes. Ctrl+C to stop.

  Internet: https://brave-otter-example.trycloudflare.com
  Permanent link: https://22f2000145.github.io/dropzone/  (give this to friends)
```

Send the **permanent link** and the **PIN** to your friend once. They bookmark it — done.

---

## Sharing with friends anywhere

1. **Set a PIN** — internet sharing is **disabled until a PIN exists**:
   ```bash
   dropzone --set-pin
   ```
   Changing the PIN logs everyone out.
2. **Start** with `dropzone`. When a PIN is set, the internet tunnel starts automatically. Use `dropzone --local` to skip it.
3. Your friend opens the link, enters the PIN and (optionally) their name. Their browser stays logged in for a year.
4. They can send you files/folders/links and download what you share — no app, no account.

> The tunnel reconnects by itself if the connection drops. If it gets a new address, the permanent link is updated automatically.

---

## Permanent link

```bash
dropzone --setup-link
```

This one-time command (uses your logged-in `gh`):

1. Creates a public gist named `dropzone-link` that stores your current tunnel address (or `offline`).
2. Enables **GitHub Pages** for this repo from the `docs/` folder.
3. Saves the link: `https://<your-github-username>.github.io/<repo>/`

From then on, every time `dropzone` starts it updates the gist; when you stop it (Ctrl+C) it writes `offline`.

When someone opens the permanent link:

- **Dropzone running** → they're forwarded to it in a second or two.
- **PC off / stopped** → a clean "Dropzone is offline" screen that **retries automatically every 15 s**, so they can leave it open.

> Works with custom domains too (the page stores your gist id). GitHub Pages needs a minute or two for its first deploy. After a restart, the gist API can cache for up to ~60 s, so the link may say "offline" briefly before forwarding.

---

## Daily usage

### Start / stop

```bash
dropzone          # start (browser opens on the PC)
# Ctrl+C          # stop
```

### Connect your phone

On the PC page click **Links** → scan the QR code (it points to the permanent link if set up). In Chrome: **⋮ → Add to Home screen** to use it like an app.

### Sending

| From | How |
|------|-----|
| Phone / friend | Paste a link or text → **Send** · **📄 Files** · **📁 Folder** |
| PC | Drag & drop **files or folders** anywhere · **Ctrl+V** to paste screenshots/files/links · type + **Ctrl+Enter** |

Uploads show a progress bar; folders show `12/40 files · 120 MB / 400 MB`.

### Receiving

| Item | Buttons |
|------|---------|
| Link / text | **Open** (links) · **Copy** · **✕** |
| File | **View** · **Save** · **✕** |
| Folder | **Files** (expand & download single files) · **ZIP** (whole folder) · **✕** |

### Selecting several items

Tap **Select** → tick items (or tap the cards) → bottom bar:
**All** · **Download ZIP** (everything selected in one zip — folders keep their structure, texts go into `notes.txt`) · **Delete** · **Cancel**.

---

## Features in detail

- **Folder upload** — PC: drag a folder in or use **📁 Folder**. Phone: **📁 Folder** in Chrome (folder picking on Android depends on your Chrome version; if it isn't offered, select all files inside the folder with **📄 Files**).
- **Folder structure preserved** — subfolders are kept, both on disk and in ZIPs.
- **Streaming ZIP** — ZIPs are built on the fly (no temp copy, no waiting); no compression for speed since photos/videos are already compressed; ZIP64 for >4 GB.
- **Chunked, resumable uploads** — files go up in 16 MB chunks with automatic retries; a dropped connection resumes where it stopped. This also bypasses Cloudflare's 100 MB request limit, so there is **no file size limit** beyond your disk.
- **3 parallel uploads** inside a folder for speed.
- **PIN login** with a remembered session (1 year, HttpOnly cookie). Optional name shown on everything you send.
- **Live sync** every 2 s (cheap — only sends data when something changed). Toast + `•` in the tab title when something new arrives.
- **Image thumbnails**, file-type badges, clickable links.
- **Copy** works on HTTPS and plain HTTP.
- **Light / dark theme** follows the system.

---

## Command-line reference

```
dropzone [--port PORT] [--no-browser] [--local]
dropzone --set-pin | --remove-pin | --setup-link
```

| Option | Description |
|--------|-------------|
| *(none)* | Start. Opens the internet tunnel if a PIN is set. |
| `--set-pin` | Set / change the PIN (≥ 6 digits). Logs everyone out. |
| `--remove-pin` | Remove the PIN. Internet sharing turns off. |
| `--setup-link` | One-time: create the permanent GitHub Pages link. |
| `--local` | Don't open the internet tunnel this time. |
| `--no-browser` | Don't open the PC browser. |
| `--port N` | Use a different port (default `8765`). |

---

## Same Wi-Fi only (optional Windows + WSL setup)

**You don't need this if you use the internet link** — the tunnel works even with WSL's default NAT networking.

It's only for direct `http://PC-IP:8765` access on the same Wi-Fi (slightly faster, works without internet). WSL2 NAT hides Linux from your network, so switch WSL to **mirrored** networking and open the port:

**Automatic:** in File Explorer open `\\wsl.localhost\kali-linux\home\<user>\dropzone`, double-click **`Setup-Dropzone.bat`**, click **Yes**. It edits `C:\Users\<you>\.wslconfig` (backs up the old one to `.wslconfig.bak`), adds firewall rules for TCP 8765, and restarts WSL.

**Manual:** `C:\Users\<you>\.wslconfig`

```ini
[wsl2]
networkingMode=mirrored
```

PowerShell as Administrator:

```powershell
New-NetFirewallRule -DisplayName "Dropzone" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
New-NetFirewallHyperVRule -Name Dropzone -DisplayName "Dropzone" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8765
wsl --shutdown
```

Check with `wslinfo --networking-mode` → `mirrored`. On plain Linux just allow the port (`sudo ufw allow 8765/tcp`).

---

## Where data is stored

```
~/dropzone/data/
├── items.json      # list of items
├── config.json     # PIN hash, login sessions, gist id, permanent link (chmod 600)
├── files/          # files (by id) and folders (by id/subpath)
└── parts/          # in-progress uploads (auto-cleaned after 24 h)
~/dropzone/bin/cloudflared   # auto-downloaded tunnel binary
```

`data/` and `bin/` are git-ignored. Delete `data/files` + `data/items.json` to wipe history.

---

## HTTP API

All endpoints except `/`, `/api/ping`, `/api/me` and `/api/login` require the login cookie when a PIN is set.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ping` | Health check `{dropzone:true}` (CORS-enabled, used by the permanent link) |
| `GET` | `/api/me` | `{authed, pin}` |
| `POST` | `/api/login` | `{"pin": "..."}` → sets `dz` cookie |
| `POST` | `/api/logout` | Clears the session |
| `GET` | `/api/items?v=<version>` | Items, newest first; `{same:true}` if unchanged |
| `GET` | `/api/info` | `{ips, port, public, stable}` |
| `POST` | `/api/text` | `{"text": "..."}` |
| `POST` | `/api/chunk` | Upload a chunk. Headers: `X-Upload-Id` (hex), `X-Offset`, `X-Total`, `X-Filename` (URL-encoded); for folders also `X-Folder`, `X-Relpath`. Returns `409 {have}` if the offset is wrong (resume from `have`). |
| `POST` | `/api/folder` | `{"name","count"}` → new folder item |
| `POST` | `/api/folder/<id>/done` | Mark folder upload finished |
| `GET` | `/files/<id>` · `/files/<folderId>/<path>` | View (`?dl` = download) |
| `GET` | `/zip?ids=a,b,c` | Stream a ZIP of the items |
| `DELETE` | `/api/items/<id>[,<id>…]` · `/api/items` | Delete some / all |

Send a file from the terminal (no PIN, or add `-b cookie`):

```bash
f=report.pdf; curl -X POST localhost:8765/api/chunk \
  -H "X-Upload-Id: $(openssl rand -hex 8)" -H "X-Offset: 0" -H "X-Total: $(stat -c%s "$f")" \
  -H "X-Filename: $f" --data-binary @"$f"
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No "Internet:" line | You haven't set a PIN (`dropzone --set-pin`), or you used `--local`. |
| "Could not get cloudflared" | No internet, or GitHub blocked. Install it manually (`sudo apt install cloudflared` or download from Cloudflare) — Dropzone uses it from `PATH`. |
| Permanent link says offline | Dropzone isn't running, or it just started (wait ~1 min — it retries itself). |
| Permanent link 404 | GitHub Pages not deployed yet (wait 2 min) or not enabled — repo **Settings → Pages → main / docs**. |
| Phone: "This site can't be reached" on `http://192.168…` | That's the same-Wi-Fi link; use the internet/permanent link, or do the [Wi-Fi setup](#same-wi-fi-only-optional-windows--wsl-setup). |
| "Too many wrong attempts" | 10 wrong PINs in 10 min locks login for everyone for a few minutes (brute-force protection). |
| Folder button opens a file picker on Android | Your Chrome doesn't support folder picking; select all the files inside instead. |
| `Port 8765 is busy` | Dropzone is already running in another terminal. |
| `dropzone: command not found` | `ln -sf ~/dropzone/start.sh ~/.local/bin/dropzone` and make sure `~/.local/bin` is in `PATH`. |
| QR code missing | The QR library loads from a CDN; use the copy buttons instead. |

---

## Security

- Internet sharing **requires a PIN** (≥ 6 digits, stored as a salted PBKDF2 hash).
- **Brute-force protection:** 10 wrong attempts in 10 minutes → login locked for everyone; each wrong attempt also waits 1 s.
- Sessions are random 256-bit tokens in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS). `--set-pin` revokes all sessions.
- Internet traffic is **HTTPS** end-to-browser via Cloudflare. (Cloudflare can technically see traffic passing through its edge, like any CDN/proxy.)
- Uploaded file paths are sanitised (no `../` escapes); files are served only from Dropzone's own storage.
- Everyone with the PIN sees and can delete **all** items — share the PIN only with people you trust.
- The tunnel address in your public gist isn't secret — the PIN is what protects you.
- Stop Dropzone (Ctrl+C) when you don't need it.

---

## Limitations

- **Your PC is the server.** If it's off, asleep or Dropzone isn't running, nobody can send or receive. (For 24/7 you'd run it on an always-on machine, e.g. a free cloud VM or an old laptop.)
- Not a messenger — no chat threads, calls or push notifications when the page is closed.
- One shared space: no per-friend private rooms.
- Cloudflare "quick tunnels" are a free service with no uptime guarantee.

---

## Uninstall

```bash
rm ~/.local/bin/dropzone
rm -rf ~/dropzone
gh gist delete <gist-id>           # id is in data/config.json before deleting
```

Disable GitHub Pages in the repo settings. If you did the Wi-Fi setup, in admin PowerShell:

```powershell
Remove-NetFirewallRule -DisplayName "Dropzone"
Remove-NetFirewallHyperVRule -Name "Dropzone"
```

and remove `networkingMode=mirrored` from `C:\Users\<you>\.wslconfig`.

---

## Project layout

```
dropzone/
├── dropzone.py          # the whole app: server, tunnel manager, embedded web UI
├── docs/index.html      # permanent-link forwarder (served by GitHub Pages)
├── start.sh             # launcher (symlinked to ~/.local/bin/dropzone)
├── Dropzone.bat         # double-click launcher from Windows
├── Setup-Dropzone.bat   # optional same-Wi-Fi setup (asks for admin)
├── setup-windows.ps1    # what Setup-Dropzone.bat runs
└── README.md
```

---

## License

MIT — do whatever you like with it.
