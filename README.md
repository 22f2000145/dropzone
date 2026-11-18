# Dropzone

**A tiny, clean, local way to move links, text and files between your Android phone and your PC — no apps, no accounts, no cloud.**

Built as a replacement for the "send it to myself on WhatsApp / Google Chat, then open it on desktop" workflow. Dropzone runs a small web server on your PC; your phone opens it in the browser over the same Wi-Fi or mobile hotspot, and anything you send shows up on the other side within ~2 seconds.

- ✅ Single Python file, **standard library only** — nothing to `pip install`
- ✅ Works **both ways**: phone → PC and PC → phone
- ✅ Links, text, photos, videos, PDFs, any file type, any size
- ✅ Everything stays on your own machine
- ✅ Clean UI with automatic light / dark mode
- ✅ Starts with one command: `dropzone`

---

## Table of contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [One-time Windows + WSL setup](#one-time-windows--wsl-setup)
5. [Daily usage](#daily-usage)
6. [Features in detail](#features-in-detail)
7. [Command-line options](#command-line-options)
8. [Where data is stored](#where-data-is-stored)
9. [HTTP API](#http-api)
10. [Troubleshooting](#troubleshooting)
11. [Security notes](#security-notes)
12. [Uninstall](#uninstall)
13. [Project layout](#project-layout)

---

## How it works

```
 ┌──────────────┐        same Wi-Fi / hotspot        ┌──────────────────────┐
 │  Android     │  ───────── http://PC-IP:8765 ────▶ │  PC (Windows + WSL)  │
 │  browser     │  ◀──────── polls every 2s ──────── │  dropzone.py server  │
 └──────────────┘                                    │  data/ (your files)  │
                                                     └──────────────────────┘
                                                               ▲
                                              http://localhost:8765 (PC browser)
```

Both the phone and the PC open the **same page**. Whatever either side sends is stored on the PC and appears in the shared list on both screens. Each item is tagged 📱 phone or 💻 pc so you know where it came from.

---

## Requirements

- **Python 3.8+** (tested on 3.14) — already included in Kali / most Linux distros
- A PC and an Android phone on the **same network** (home Wi-Fi, or the PC connected to the phone's hotspot)
- On Windows + WSL2: the [one-time setup](#one-time-windows--wsl-setup) below
- Optional: internet access on the PC page to draw the QR code (the URL is always shown as text too)

Works on iPhone browsers too; it's just been tested mainly on Android.

---

## Installation

```bash
git clone https://github.com/22f2000145/dropzone.git ~/dropzone
chmod +x ~/dropzone/start.sh ~/dropzone/dropzone.py

# make the `dropzone` command available everywhere
mkdir -p ~/.local/bin
ln -sf ~/dropzone/start.sh ~/.local/bin/dropzone
```

Make sure `~/.local/bin` is on your `PATH` (it is by default on Kali / Ubuntu). Check with:

```bash
which dropzone
```

> On plain Linux (not WSL) you're done — skip to [Daily usage](#daily-usage). You may still need to allow port 8765 in your firewall (e.g. `sudo ufw allow 8765/tcp`).

---

## One-time Windows + WSL setup

**Why this is needed:** by default WSL2 runs in *NAT* mode — Linux gets a private internal IP (like `192.168.192.x`) that your phone can't reach, so the phone shows *"This site can't be reached"*. The fix is to switch WSL to **mirrored** networking (Linux shares the PC's real Wi-Fi IP) and open port 8765 in the firewall.

### Automatic (recommended)

1. Open **File Explorer** and go to:
   ```
   \\wsl.localhost\kali-linux\home\<your-linux-user>\dropzone
   ```
   (replace `kali-linux` with your distro name — see `wsl -l`)
2. Double-click **`Setup-Dropzone.bat`** and click **Yes** on the admin prompt.
3. The script will:
   - add `networkingMode=mirrored` to `C:\Users\<you>\.wslconfig` (backing up any existing file to `.wslconfig.bak`)
   - add a Windows Firewall inbound rule for TCP 8765
   - add a WSL Hyper-V firewall rule for TCP 8765 (Windows 11 22H2+)
   - run `wsl --shutdown` so the change takes effect
4. Re-open your Linux terminal.

### Manual

`C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

PowerShell **as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "Dropzone" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
New-NetFirewallHyperVRule -Name Dropzone -DisplayName "Dropzone" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8765
wsl --shutdown
```

Verify inside Linux:

```bash
wslinfo --networking-mode   # should print: mirrored
```

> Mirrored mode needs Windows 11 22H2 or newer. If `dropzone` still says *"WSL is in NAT mode"*, the setup hasn't applied yet — run `wsl --shutdown` from Windows and reopen the terminal.

---

## Daily usage

### Start

```bash
dropzone
```

Output:

```
  Dropzone is running

  PC:    http://localhost:8765
  Phone: http://10.202.202.191:8765

  Click 'Connect phone' on the PC page for a QR code. Ctrl+C to stop.
```

The PC page opens in your Windows browser automatically. Stop with **Ctrl+C**.

### Connect the phone (first time)

1. On the PC page click **Connect phone** → a QR code appears.
2. Scan it with the phone camera / Google Lens.
3. In Chrome on the phone: **⋮ → Add to Home screen**. Now Dropzone opens like an app.

> The phone's IP address can change when you reconnect to the hotspot. If the bookmark stops working, just scan the QR again.

### Send from phone → PC

- **Link / text:** paste in the box → **Send**
- **Files / photos:** tap **Tap to choose files / photos** (multiple allowed)

### Send from PC → phone

- **Drag & drop** files anywhere on the page
- **Ctrl+V** anywhere: pastes a screenshot / copied file as an upload, or copied text/link as a message
- Type text and press **Ctrl+Enter**

### On each item

| Item | Buttons |
|------|---------|
| Link / text | **Open** (links only) · **Copy** · **✕** delete |
| File | **View** (open in browser) · **Save** (download) · **✕** delete |

**Clear all** in the header wipes the whole history (files included).

---

## Features in detail

- **Live sync** — each page polls every 2 s; a toast shows *"New from phone"* and the tab title gets a `•` when something arrives from the other device.
- **Image thumbnails** for photos, file-type badge for everything else.
- **Upload progress bar** per file; large files are streamed to disk in 1 MB chunks (no size limit beyond disk space).
- **Clickable links** inside text messages.
- **Copy works over plain HTTP** (falls back from the Clipboard API, which browsers restrict to HTTPS).
- **Persistent history** — survives restarts until you delete items.
- **Device tag** — 📱 phone / 💻 pc detected from the browser user-agent.
- **Light / dark theme** follows your system setting.

---

## Command-line options

```
dropzone [--port PORT] [--no-browser]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | `8765` | Port to listen on. If you change it, also open that port in the firewall. |
| `--no-browser` | off | Don't auto-open the PC browser. |

`start.sh` passes all arguments through, so `dropzone --port 9000` works.

---

## Where data is stored

```
~/dropzone/data/
├── items.json      # list of items (text, file metadata, sender, timestamp)
└── files/          # uploaded files, stored by item id
```

Delete the `data/` folder to reset everything. It's excluded from git via `.gitignore`.

---

## HTTP API

Handy for scripts (e.g. send a file from the terminal):

| Method | Path | Body / headers | Description |
|--------|------|----------------|-------------|
| `GET` | `/` | — | Web UI |
| `GET` | `/api/items` | — | `{version, items[]}` newest first |
| `GET` | `/api/info` | — | `{ips[], port}` |
| `POST` | `/api/text` | JSON `{"text": "..."}` | Add a text / link item |
| `POST` | `/api/upload` | raw file bytes, header `X-Filename` (URL-encoded) | Upload a file |
| `GET` | `/files/<id>` | — | View file inline (`?dl` to force download) |
| `DELETE` | `/api/items/<id>` | — | Delete one item |
| `DELETE` | `/api/items` | — | Delete everything |

Examples:

```bash
# send a link to the phone
curl -X POST localhost:8765/api/text -H 'Content-Type: application/json' -d '{"text":"https://example.com"}'

# send a file
curl -X POST localhost:8765/api/upload -H 'X-Filename: report.pdf' --data-binary @report.pdf
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Phone: *"This site can't be reached"* | Run the [one-time setup](#one-time-windows--wsl-setup). Make sure phone and PC are on the same network. Use the **Phone** URL / QR, never `localhost`. |
| `dropzone` prints *"WSL is in NAT mode"* | Setup not applied — run `Setup-Dropzone.bat`, or `wsl --shutdown` and reopen the terminal. |
| Worked yesterday, not today | Hotspot gave the PC a new IP. Restart `dropzone` and re-scan the QR. |
| `dropzone: command not found` | Re-create the link: `ln -sf ~/dropzone/start.sh ~/.local/bin/dropzone` and ensure `~/.local/bin` is in `PATH`. |
| `Address already in use` | Dropzone is already running in another terminal, or use `dropzone --port 8766` (and open that port). |
| QR code doesn't show | The QR library loads from a CDN — needs internet. Type the URL shown under it instead. |
| Copy button does nothing | Long-press the text and copy manually; some browsers block clipboard access on HTTP. |
| Page opens on PC but not automatically | Open `http://localhost:8765` yourself, or start with `--no-browser` and bookmark it. |

---

## Security notes

- There is **no password**. Anyone on the same network who knows the URL can see and add items.
- That's fine on your own hotspot / home Wi-Fi. **Don't run it on public or office Wi-Fi.**
- Traffic is plain HTTP (unencrypted) on the local network.
- Stop the server (Ctrl+C) when you're not using it.

---

## Uninstall

```bash
rm ~/.local/bin/dropzone
rm -rf ~/dropzone
```

Optional, on Windows (PowerShell as Admin):

```powershell
Remove-NetFirewallRule -DisplayName "Dropzone"
Remove-NetFirewallHyperVRule -Name "Dropzone"
```

and remove `networkingMode=mirrored` from `C:\Users\<you>\.wslconfig` (or restore `.wslconfig.bak`) if you don't want mirrored networking for anything else.

---

## Project layout

```
dropzone/
├── dropzone.py          # the whole app: HTTP server + embedded web UI
├── start.sh             # launcher (symlinked to ~/.local/bin/dropzone)
├── Dropzone.bat         # double-click launcher from Windows
├── Setup-Dropzone.bat   # one-time Windows setup (asks for admin)
├── setup-windows.ps1    # what Setup-Dropzone.bat runs
└── README.md
```

---

## License

MIT — do whatever you like with it.
