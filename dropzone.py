#!/usr/bin/env python3
"""Dropzone - tiny self-hosted phone <-> PC <-> friends transfer. Stdlib only.

  dropzone                 start (shares over the internet when a PIN is set)
  dropzone --set-pin       set / change the PIN (required for internet sharing)
  dropzone --setup-link    one-time: create a permanent link on GitHub Pages
  dropzone --local         don't open the internet tunnel this time
"""
import argparse, getpass, hashlib, hmac, json, mimetypes, os, platform, re, secrets, shutil
import signal, socket, subprocess, sys, threading, time, urllib.request, uuid, zipfile
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, parse_qs

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
FILES = DATA / "files"
PARTS = DATA / "parts"
INDEX = DATA / "items.json"
CONF = DATA / "config.json"
LOCK = threading.Lock()
STATE = {"items": [], "version": 0}
CONFIG = {}
PUBLIC = {"url": None}
FAILS = []                  # timestamps of wrong PIN attempts (global limiter)
DONE_UPLOADS = {}           # upload id -> item id (makes final-chunk retries idempotent)
STOP = threading.Event()
GIST_FILE = "dropzone-link.txt"
GIST_DESC = "dropzone-link"


# ---------------------------------------------------------------- storage
def load():
    FILES.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    if INDEX.exists():
        try:
            STATE["items"] = json.loads(INDEX.read_text())
        except Exception:
            STATE["items"] = []
    if CONF.exists():
        try:
            CONFIG.update(json.loads(CONF.read_text()))
        except Exception:
            pass
    for p in PARTS.iterdir():  # stale partial uploads
        if time.time() - p.stat().st_mtime > 86400:
            p.unlink(missing_ok=True)
    STATE["version"] = int(time.time() * 1000)


def save():
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE["items"]))
    tmp.replace(INDEX)
    STATE["version"] += 1


def save_conf():
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(json.dumps(CONFIG, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(CONF)


def safe_rel(p):
    parts = [x.replace("\0", "") for x in p.replace("\\", "/").split("/")]
    parts = [x[:200] for x in parts if x not in ("", ".", "..")]
    return "/".join(parts)[:1000]


def safe_name(n):
    n = (n or "").replace("/", "_").replace("\\", "_").replace("\0", "").strip()[:200]
    return n or "file"


def find(iid):
    return next((i for i in STATE["items"] if i["id"] == iid), None)


def remove_item_files(it):
    p = FILES / it["id"]
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------- network helpers
def wsl_nat():
    try:
        return subprocess.run(["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=2).stdout.strip() == "nat"
    except Exception:
        return False


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        for ip in out.split():
            if "." in ip and ip not in ips and not ip.startswith(("127.", "10.255.")):
                ips.append(ip)
    except Exception:
        pass
    return ips


def device_of(ua):
    ua = (ua or "").lower()
    return "phone" if any(k in ua for k in ("android", "iphone", "ipad", "mobile")) else "pc"


# ---------------------------------------------------------------- auth
def pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def pin_enabled():
    return bool(CONFIG.get("pin"))


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "Dropzone"
    timeout = 300

    def log_message(self, *a):
        pass

    # -- helpers
    def send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("too big")
        return json.loads(self.rfile.read(n) or b"{}")

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto") == "https" or "https" in (self.headers.get("Cf-Visitor") or "")

    def token(self):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:
            return None
        return c["dz"].value if "dz" in c else None

    def authed(self):
        if not pin_enabled():
            return True
        t = self.token()
        return bool(t) and any(hmac.compare_digest(t, x) for x in CONFIG.get("tokens", []))

    def who(self):
        return unquote(self.headers.get("X-Who") or "").strip()[:30]

    def new_item(self, **kw):
        it = {"id": uuid.uuid4().hex[:12], "from": device_of(self.headers.get("User-Agent")),
              "who": self.who(), "ts": time.time()}
        it.update(kw)
        return it

    def route(self):
        u = urlsplit(self.path)
        return unquote(u.path), parse_qs(u.query, keep_blank_values=True)

    # -- GET
    def do_GET(self):
        p, q = self.route()
        if p in ("/", "/index.html"):
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/ping":
            return self.send(200, {"dropzone": True}, extra={"Access-Control-Allow-Origin": "*"})
        if p == "/api/me":
            return self.send(200, {"authed": self.authed(), "pin": pin_enabled()})
        if not self.authed():
            return self.send(401, {"error": "login required"})
        if p == "/api/items":
            with LOCK:
                v = STATE["version"]
                if q.get("v", [""])[0] == str(v):
                    return self.send(200, {"version": v, "same": True})
                return self.send(200, {"version": v, "items": STATE["items"]})
        if p == "/api/info":
            nat = wsl_nat()
            return self.send(200, {"ips": [] if nat else lan_ips(), "port": self.server.server_address[1],
                                   "public": PUBLIC["url"], "stable": CONFIG.get("stable_url")})
        if p.startswith("/files/"):
            return self.serve_file(p[len("/files/"):], "dl" in q)
        if p == "/zip":
            ids = [x for x in ",".join(q.get("ids", [])).split(",") if x]
            return self.send_zip(ids)
        self.send(404, {"error": "not found"})

    def serve_file(self, rest, dl):
        iid, _, rel = rest.partition("/")
        with LOCK:
            it = find(iid)
        if not it:
            return self.send(404, {"error": "not found"})
        if it["kind"] == "file" and not rel:
            fp, name, mime = FILES / iid, it["name"], it.get("mime")
        elif it["kind"] == "folder" and rel:
            root = (FILES / iid).resolve()
            fp = (root / safe_rel(rel)).resolve()
            if root not in fp.parents:
                return self.send(404, {"error": "not found"})
            name, mime = fp.name, mimetypes.guess_type(fp.name)[0]
        else:
            return self.send(404, {"error": "not found"})
        if not fp.is_file():
            return self.send(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(fp.stat().st_size))
        self.send_header("Content-Disposition", f"{'attachment' if dl else 'inline'}; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        with open(fp, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def send_zip(self, ids):
        with LOCK:
            sel = [i for i in STATE["items"] if i["id"] in set(ids)]
        if not sel:
            return self.send(404, {"error": "nothing selected"})
        if len(sel) == 1 and sel[0]["kind"] == "folder":
            zname = sel[0]["name"] + ".zip"
        else:
            zname = time.strftime("dropzone-%Y%m%d-%H%M.zip")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(zname)}")
        self.send_header("Connection", "close")
        self.end_headers()
        used = set()

        def uniq(n):
            base, ext = os.path.splitext(n)
            k, out = 1, n
            while out.lower() in used:
                k += 1
                out = f"{base} ({k}){ext}"
            used.add(out.lower())
            return out

        texts = []
        with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for it in sel:
                if it["kind"] == "file":
                    fp = FILES / it["id"]
                    if fp.is_file():
                        zf.write(fp, uniq(it["name"]))
                elif it["kind"] == "folder":
                    top = uniq(it["name"])
                    for f in it.get("files", []):
                        fp = FILES / it["id"] / f["path"]
                        if fp.is_file():
                            zf.write(fp, f"{top}/{f['path']}")
                elif it["kind"] == "text":
                    texts.append(it["text"])
            if texts:
                zf.writestr(uniq("notes.txt"), "\n\n---\n\n".join(texts))

    # -- POST
    def do_POST(self):
        p, q = self.route()
        if p == "/api/login":
            return self.login()
        if p == "/api/logout":
            t = self.token()
            if t and t in CONFIG.get("tokens", []):
                CONFIG["tokens"].remove(t)
                save_conf()
            return self.send(200, {"ok": True}, extra={"Set-Cookie": "dz=; Max-Age=0; Path=/"})
        if not self.authed():
            return self.send(401, {"error": "login required"})
        if p == "/api/text":
            try:
                text = str(self.body_json().get("text", "")).strip()
            except Exception:
                return self.send(400, {"error": "bad request"})
            if not text:
                return self.send(400, {"error": "empty"})
            it = self.new_item(kind="text", text=text[:100_000])
            with LOCK:
                STATE["items"].insert(0, it)
                save()
            return self.send(200, it)
        if p == "/api/folder":
            try:
                d = self.body_json()
            except Exception:
                return self.send(400, {"error": "bad request"})
            it = self.new_item(kind="folder", name=safe_name(d.get("name") or "folder"), files=[], size=0,
                               count=int(d.get("count") or 0), done=False)
            (FILES / it["id"]).mkdir(parents=True, exist_ok=True)
            with LOCK:
                STATE["items"].insert(0, it)
                save()
            return self.send(200, it)
        m = re.fullmatch(r"/api/folder/([0-9a-f]{12})/done", p)
        if m:
            with LOCK:
                it = find(m.group(1))
                if it and it["kind"] == "folder":
                    it["done"] = True
                    save()
            return self.send(200, {"ok": True})
        if p == "/api/chunk":
            return self.chunk()
        self.send(404, {"error": "not found"})

    def login(self):
        now = time.time()
        FAILS[:] = [t for t in FAILS if now - t < 600]
        if len(FAILS) >= 10:
            wait = int(600 - (now - FAILS[0])) // 60 + 1
            return self.send(429, {"error": f"Too many wrong attempts. Try again in {wait} min."})
        try:
            pin = str(self.body_json().get("pin", ""))
        except Exception:
            return self.send(400, {"error": "bad request"})
        if not pin_enabled():
            return self.send(200, {"ok": True})
        if not hmac.compare_digest(pin_hash(pin, CONFIG["salt"]), CONFIG["pin"]):
            FAILS.append(now)
            time.sleep(1)
            return self.send(403, {"error": "Wrong PIN"})
        tok = secrets.token_urlsafe(32)
        CONFIG.setdefault("tokens", []).append(tok)
        CONFIG["tokens"] = CONFIG["tokens"][-50:]
        save_conf()
        cookie = f"dz={tok}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if self.is_https() else "")
        return self.send(200, {"ok": True}, extra={"Set-Cookie": cookie})

    def chunk(self):
        h = self.headers
        uid = h.get("X-Upload-Id") or ""
        if not re.fullmatch(r"[0-9a-f]{8,40}", uid):
            return self.send(400, {"error": "bad upload id"})
        try:
            off, total, n = int(h.get("X-Offset")), int(h.get("X-Total")), int(h.get("Content-Length") or 0)
        except Exception:
            return self.send(400, {"error": "bad headers"})
        if uid in DONE_UPLOADS:
            self.rfile.read(n)
            return self.send(200, {"done": True, "id": DONE_UPLOADS[uid]})
        folder = h.get("X-Folder") or ""
        if folder:
            with LOCK:
                fit = find(folder)
            if not fit or fit["kind"] != "folder":
                return self.send(404, {"error": "folder gone"})
        part = PARTS / uid
        have = part.stat().st_size if part.exists() else 0
        if off != have:
            return self.send(409, {"have": have})
        if off + n > total:
            return self.send(400, {"error": "too much data"})
        left = n
        with open(part, "ab") as f:
            while left > 0:
                c = self.rfile.read(min(1 << 20, left))
                if not c:
                    break
                f.write(c)
                left -= len(c)
        if left:
            return self.send(400, {"error": "incomplete chunk"})
        if off + n < total:
            return self.send(200, {"done": False, "have": off + n})
        # ---- finalize
        name = safe_name(unquote(h.get("X-Filename") or "file"))
        mime = h.get("Content-Type") or ""
        if not mime or mime == "application/octet-stream":
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if folder:
            rel = safe_rel(unquote(h.get("X-Relpath") or name)) or name
            dest = FILES / folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with LOCK:
                fit = find(folder)
                if not fit:
                    part.unlink(missing_ok=True)
                    return self.send(404, {"error": "folder gone"})
                if any(f["path"] == rel for f in fit["files"]):
                    fit["size"] -= next(f["size"] for f in fit["files"] if f["path"] == rel)
                    fit["files"] = [f for f in fit["files"] if f["path"] != rel]
                part.replace(dest)
                fit["files"].append({"path": rel, "size": total})
                fit["size"] += total
                save()
            DONE_UPLOADS[uid] = folder
            return self.send(200, {"done": True, "id": folder})
        it = self.new_item(kind="file", name=name, size=total, mime=mime)
        part.replace(FILES / it["id"])
        with LOCK:
            STATE["items"].insert(0, it)
            save()
        DONE_UPLOADS[uid] = it["id"]
        return self.send(200, {"done": True, "id": it["id"]})

    # -- DELETE
    def do_DELETE(self):
        p, _ = self.route()
        if not self.authed():
            return self.send(401, {"error": "login required"})
        with LOCK:
            if p == "/api/items":
                for it in STATE["items"]:
                    remove_item_files(it)
                STATE["items"] = []
                save()
                return self.send(200, {"ok": True})
            if p.startswith("/api/items/"):
                ids = set(p.rsplit("/", 1)[1].split(","))
                for it in [i for i in STATE["items"] if i["id"] in ids]:
                    remove_item_files(it)
                STATE["items"] = [i for i in STATE["items"] if i["id"] not in ids]
                save()
                return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # client disconnects etc. are normal


# ---------------------------------------------------------------- internet tunnel (Cloudflare)
def cloudflared_path():
    p = shutil.which("cloudflared")
    if p:
        return p
    p = BASE / "bin" / "cloudflared"
    if p.exists():
        return str(p)
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}.get(platform.machine().lower(), "amd64")
    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
    print("  Downloading cloudflared (one time, ~40 MB)...", flush=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    urllib.request.urlretrieve(url, tmp)
    tmp.chmod(0o755)
    tmp.replace(p)
    return str(p)


def publish_link(url):
    gid = CONFIG.get("gist")
    if not gid or not shutil.which("gh"):
        return
    body = json.dumps({"files": {GIST_FILE: {"content": url}}})
    try:
        subprocess.run(["gh", "api", "--method", "PATCH", f"gists/{gid}", "--input", "-"],
                       input=body, text=True, capture_output=True, timeout=30)
    except Exception:
        pass


def tunnel_loop(port):
    try:
        exe = cloudflared_path()
    except Exception as e:
        print(f"  \033[31m! Could not get cloudflared: {e}\033[0m", flush=True)
        return
    backoff = 2
    while not STOP.is_set():
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        TUNNEL["proc"] = proc
        for line in proc.stderr:
            m = re.search(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com", line)
            if m and PUBLIC["url"] != m.group(0):
                PUBLIC["url"] = m.group(0)
                backoff = 2
                print(f"  Internet: \033[1m{PUBLIC['url']}\033[0m", flush=True)
                if CONFIG.get("stable_url"):
                    print(f"  Permanent link: \033[1m{CONFIG['stable_url']}\033[0m  (give this to friends)", flush=True)
                threading.Thread(target=publish_link, args=(PUBLIC["url"],), daemon=True).start()
        proc.wait()
        PUBLIC["url"] = None
        if STOP.is_set():
            return
        print(f"  Internet link dropped, reconnecting in {backoff}s...", flush=True)
        STOP.wait(backoff)
        backoff = min(backoff * 2, 60)


TUNNEL = {"proc": None}


# ---------------------------------------------------------------- CLI commands
def set_pin():
    print("Set a PIN for Dropzone (at least 6 digits). Friends need it to open the page.")
    p1 = getpass.getpass("New PIN: ").strip()
    if not re.fullmatch(r"\d{6,}", p1):
        sys.exit("PIN must be at least 6 digits (numbers only).")
    if getpass.getpass("Repeat PIN: ").strip() != p1:
        sys.exit("PINs don't match.")
    CONFIG["salt"] = secrets.token_hex(16)
    CONFIG["pin"] = pin_hash(p1, CONFIG["salt"])
    CONFIG["tokens"] = []  # log everyone out
    save_conf()
    print("PIN saved. Everyone has been logged out. Run `dropzone` to start sharing.")


def gh(*args, input=None):
    r = subprocess.run(["gh", *args], input=input, text=True, capture_output=True, timeout=60, cwd=BASE)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def setup_link():
    if not shutil.which("gh"):
        sys.exit("GitHub CLI (gh) is required: https://cli.github.com  then `gh auth login`.")
    code, nwo, err = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    if code:
        sys.exit("This folder must be your GitHub clone of dropzone (with docs/index.html pushed).\n" + err)
    owner, repo = nwo.split("/")
    if not CONFIG.get("gist"):
        body = json.dumps({"description": GIST_DESC, "public": True, "files": {GIST_FILE: {"content": "offline"}}})
        code, gid, err = gh("api", "--method", "POST", "gists", "--input", "-", "-q", ".id", input=body)
        if code:
            sys.exit("Could not create gist: " + err)
        CONFIG["gist"] = gid
        print(f"[ok] created link gist {gid}")
    body = json.dumps({"source": {"branch": "main", "path": "/docs"}})
    code, _, err = gh("api", "--method", "POST", f"repos/{nwo}/pages", "--input", "-", input=body)
    if code and "already" not in err.lower() and "409" not in err:
        print("[!!] Could not enable GitHub Pages automatically:", err)
        print("     Enable it manually: repo Settings -> Pages -> Branch: main, folder: /docs")
    else:
        print("[ok] GitHub Pages enabled (first deploy can take 1-2 minutes)")
    # Pin the gist id inside the forwarder page so it works on custom domains too
    page = BASE / "docs" / "index.html"
    if page.exists():
        html = page.read_text()
        new = re.sub(r'(<meta name="dropzone-gist" content=")[^"]*(")', rf'\g<1>{CONFIG["gist"]}\g<2>', html)
        if new != html:
            page.write_text(new)
            r = subprocess.run(f'git add docs/index.html && git commit -qm "Set permanent link gist" && git push -q',
                               shell=True, cwd=BASE, capture_output=True, text=True)
            print("[ok] forwarder page updated and pushed" if r.returncode == 0 else
                  "[!!] updated docs/index.html - commit and push it yourself:\n" + r.stderr)
    CONFIG["stable_url"] = f"https://{owner.lower()}.github.io/{repo}/"
    save_conf()
    print(f"\nPermanent link: {CONFIG['stable_url']}\nIt always forwards to your current Dropzone while it's running.")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Dropzone - share links, text, files and folders between devices.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the PC browser")
    ap.add_argument("--local", action="store_true", help="don't open the internet tunnel")
    ap.add_argument("--set-pin", action="store_true", help="set or change the PIN")
    ap.add_argument("--remove-pin", action="store_true", help="remove the PIN (disables internet sharing)")
    ap.add_argument("--setup-link", action="store_true", help="create a permanent link via GitHub Pages")
    a = ap.parse_args()
    load()
    if a.set_pin:
        return set_pin()
    if a.remove_pin:
        for k in ("pin", "salt", "tokens"):
            CONFIG.pop(k, None)
        save_conf()
        return print("PIN removed. Internet sharing is now off.")
    if a.setup_link:
        return setup_link()

    try:
        srv = Server(("0.0.0.0", a.port), H)
    except OSError:
        sys.exit(f"Port {a.port} is busy - Dropzone is probably already running. (Or use --port)")

    print("\n  \033[1mDropzone is running\033[0m\n")
    print(f"  PC:       http://localhost:{a.port}")
    if wsl_nat():
        print("  Wi-Fi:    (unavailable - WSL is in NAT mode; use the internet link)")
    else:
        for ip in lan_ips():
            print(f"  Wi-Fi:    http://{ip}:{a.port}")
    share = pin_enabled() and not a.local
    if share:
        print("  Internet: starting secure tunnel...", flush=True)
        threading.Thread(target=tunnel_loop, args=(a.port,), daemon=True).start()
    elif not pin_enabled():
        print("\n  \033[33mInternet sharing is off.\033[0m Set a PIN to share with friends anywhere:  dropzone --set-pin")
    print("\n  Click 'Links' on the page for QR codes. Ctrl+C to stop.\n", flush=True)

    if not a.no_browser:
        url = f"http://localhost:{a.port}"
        for cmd in (["cmd.exe", "/c", "start", url], ["xdg-open", url]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd="/")
                break
            except Exception:
                continue

    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        if TUNNEL["proc"]:
            TUNNEL["proc"].terminate()
        if share and CONFIG.get("gist"):
            publish_link("offline")
        print("\n  Dropzone stopped.")


# ---------------------------------------------------------------- web UI
PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Dropzone</title>
<meta name="theme-color" content="#0f1115">
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14161a;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;--acc2:#dbeafe;--danger:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--fg:#e8eaee;--mut:#8b93a1;--line:#272b33;--acc:#5b8cff;--acc2:#1c2740;--danger:#f87171}}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:16px 16px 100px}
header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:4px 0 16px}
h1{font-size:20px;margin:0;letter-spacing:-.02em}
h1 span{color:var(--acc)}
.btn{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:10px;padding:8px 12px;font:inherit;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
.btn:hover{border-color:var(--acc)}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.ghost{border:none;background:none;color:var(--mut);padding:6px 8px}
.btn.ghost:hover{color:var(--fg)}
.btn.danger{color:var(--danger)}
.hbtns{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.compose{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px}
textarea{width:100%;border:none;outline:none;resize:none;background:transparent;color:var(--fg);font:inherit;min-height:52px}
.row{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:8px}
.drop{margin-top:12px;border:2px dashed var(--line);border-radius:14px;padding:18px;text-align:center;color:var(--mut);transition:.15s}
.drop.on{border-color:var(--acc);background:var(--acc2);color:var(--fg)}
.drop .btns{display:flex;gap:8px;justify-content:center;margin-top:10px;flex-wrap:wrap}
.list{margin-top:20px;display:flex;flex-direction:column;gap:10px}
#up{display:flex;flex-direction:column;gap:10px;margin-top:12px}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;display:flex;gap:12px;align-items:flex-start}
.item.picked{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
.item .body{flex:1;min-width:0}
.cb{width:20px;height:20px;margin:22px 0 0;accent-color:var(--acc);flex:none}
.txt{white-space:pre-wrap;word-break:break-word}
.txt a{color:var(--acc)}
.meta{font-size:12px;color:var(--mut);margin-top:4px}
.fname{font-weight:600;word-break:break-all}
.thumb{width:64px;height:64px;border-radius:10px;object-fit:cover;background:var(--line);flex:none}
.ico{width:64px;height:64px;border-radius:10px;background:var(--acc2);color:var(--acc);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none;text-transform:uppercase}
.ico.fold{font-size:28px}
.acts{display:flex;gap:2px;flex:none;align-items:center;flex-wrap:wrap}
.flist{margin-top:8px;border-top:1px solid var(--line);padding-top:6px;max-height:260px;overflow:auto}
.flist a{display:flex;justify-content:space-between;gap:10px;padding:4px 0;color:var(--fg);text-decoration:none;font-size:13px;word-break:break-all}
.flist a:hover{color:var(--acc)}
.flist span{color:var(--mut);white-space:nowrap}
.empty{color:var(--mut);text-align:center;padding:40px 0}
.prog{height:4px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:6px}
.prog i{display:block;height:100%;background:var(--acc);width:0;transition:width .2s}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:16px}
.links{display:grid;gap:12px}
.lk{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.lk b{font-size:13px;color:var(--mut);font-weight:600;display:block}
.lk code{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}
#qr{display:inline-block;background:#fff;padding:10px;border-radius:10px;margin-top:4px}
.qrwrap{text-align:center}
.toast{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:8px 14px;border-radius:10px;font-size:14px;opacity:0;transition:.2s;pointer-events:none;z-index:9}
.toast.show{opacity:1}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;background:var(--acc2);color:var(--acc);margin-right:6px}
.selbar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:12px 16px calc(12px + env(safe-area-inset-bottom));display:flex;gap:8px;align-items:center;justify-content:center;flex-wrap:wrap;z-index:5}
.selbar span{margin-right:auto;color:var(--mut);font-size:14px}
.login{min-height:100vh;display:grid;place-items:center;padding:16px}
.login form{width:100%;max-width:340px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;display:grid;gap:12px}
.login h1{font-size:24px;text-align:center;margin-bottom:4px}
.login p{margin:0;color:var(--mut);text-align:center;font-size:14px}
.login input{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg);font:inherit;font-size:16px}
.login input#pin{letter-spacing:.3em;text-align:center;font-size:22px}
.login .err{color:var(--danger);text-align:center;font-size:14px;min-height:1em}
@media (max-width:540px){.item{flex-wrap:wrap}.acts{width:100%;justify-content:flex-end}.thumb,.ico{width:52px;height:52px}}
</style></head><body>

<div class="login" id="login" hidden>
<form id="loginf" autocomplete="off">
<h1>Drop<span>zone</span></h1>
<p>Enter the PIN to continue</p>
<input id="pin" type="password" inputmode="numeric" pattern="[0-9]*" placeholder="••••••" autocomplete="current-password" required>
<input id="nm" type="text" placeholder="Your name (optional)" maxlength="30">
<button class="btn pri" type="submit">Open</button>
<div class="err" id="lerr"></div>
</form></div>

<div class="wrap" id="app" hidden>
<header><h1>Drop<span>zone</span></h1>
<div class="hbtns"><button class="btn" id="linksbtn">Links</button><button class="btn ghost" id="selbtn">Select</button><button class="btn ghost" id="clear">Clear all</button></div></header>

<div class="panel" id="links" hidden>
<div class="links" id="linklist"></div>
<div class="qrwrap"><div id="qr"></div><div class="meta" id="qrcap"></div></div>
<div class="row"><span class="meta">Signed in as <b id="meName">—</b></span><button class="btn ghost" id="logout">Log out</button></div>
</div>

<div class="compose">
<textarea id="t" placeholder="Paste a link or type text…"></textarea>
<div class="row"><span class="meta" id="hint"></span><button class="btn pri" id="send">Send</button></div>
</div>
<div class="drop" id="drop">
<div id="droptxt">Drop files or folders here</div>
<div class="btns"><label class="btn">📄 Files<input type="file" id="f" multiple hidden></label><label class="btn">📁 Folder<input type="file" id="fd" webkitdirectory multiple hidden></label></div>
</div>
<div id="up"></div>
<div class="list" id="list"></div>
</div>

<div class="selbar" id="selbar" hidden><span id="selcount">0 selected</span>
<button class="btn" id="selall">All</button><button class="btn pri" id="selzip">Download ZIP</button><button class="btn danger" id="seldel">Delete</button><button class="btn ghost" id="selx">Cancel</button></div>
<div class="toast" id="toast"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
const $=s=>document.querySelector(s);
const mobile=/android|iphone|ipad|mobile/i.test(navigator.userAgent);
const me=mobile?'phone':'pc';
const CHUNK=16*1024*1024;
let myName='';try{myName=localStorage.getItem('dz-name')||''}catch(e){}
let ver=null,items=[],openF=new Set(),sel=new Set(),selMode=false,started=false,pinOn=false;

$('#droptxt').textContent=mobile?'Send files, photos or a whole folder':'Drop files or folders here · Ctrl+V to paste anywhere';
$('#hint').textContent=mobile?'':'Ctrl+Enter to send';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),1800)}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function linkify(s){return esc(s).replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>')}
function size(n){const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return (i?n.toFixed(1):n)+' '+u[i]}
function ago(ts){const s=Date.now()/1000-ts;if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return new Date(ts*1000).toLocaleDateString()}
function encPath(p){return p.split('/').map(encodeURIComponent).join('/')}
function rid(){return [...crypto.getRandomValues(new Uint8Array(12))].map(b=>b.toString(16).padStart(2,'0')).join('')}
function hdr(x){return Object.assign({'X-Who':encodeURIComponent(myName)},x||{})}
function copy(text){
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(()=>toast('Copied'),()=>toast('Copy failed'));return}
  const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();
  try{document.execCommand('copy');toast('Copied')}catch(e){toast('Copy failed')}ta.remove();
}
async function api(path,opt={}){opt.headers=hdr(opt.headers);const r=await fetch(path,opt);if(r.status===401){showLogin();throw new Error('auth')}return r}

/* ---------- login ---------- */
function showLogin(){$('#app').hidden=true;$('#selbar').hidden=true;$('#login').hidden=false;$('#nm').value=myName;setTimeout(()=>$('#pin').focus(),50)}
function showApp(){$('#login').hidden=true;$('#app').hidden=false;$('#meName').textContent=myName||me;$('#logout').hidden=!pinOn;if(!started){started=true;poll();setInterval(poll,2000);setInterval(()=>items.length&&render(),30000)}}
$('#loginf').onsubmit=async e=>{e.preventDefault();$('#lerr').textContent='';
  myName=$('#nm').value.trim().slice(0,30);try{localStorage.setItem('dz-name',myName)}catch(e){}
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:$('#pin').value})});
  let d={};try{d=await r.json()}catch(e){}
  if(r.ok){$('#pin').value='';ver=null;showApp()}else{$('#lerr').textContent=d.error||'Wrong PIN';$('#pin').value='';$('#pin').focus()}};
$('#logout').onclick=async()=>{await fetch('/api/logout',{method:'POST'});location.reload()};
(async()=>{try{const d=await (await fetch('/api/me')).json();pinOn=d.pin;d.authed?showApp():showLogin()}catch(e){toast('Cannot reach Dropzone')}})();

/* ---------- list ---------- */
function tag(it){return `<span class="tag">${it.from==='phone'?'📱':'💻'} ${esc(it.who||it.from)}</span>`}
function itemHTML(it){
  const cb=selMode?`<input type="checkbox" class="cb" data-sel="${it.id}" ${sel.has(it.id)?'checked':''}>`:'';
  const cls='item'+(sel.has(it.id)?' picked':'');
  const del=`<button class="btn ghost" data-del="${it.id}" title="Delete">✕</button>`;
  if(it.kind==='text'){
    const isUrl=/^https?:\/\/\S+$/.test(it.text);
    return `<div class="${cls}" data-id="${it.id}">${cb}<div class="body"><div class="txt">${linkify(it.text)}</div><div class="meta">${tag(it)}${ago(it.ts)}</div></div>
    <div class="acts">${isUrl?`<a class="btn ghost" href="${esc(it.text)}" target="_blank" rel="noopener">Open</a>`:''}<button class="btn ghost" data-copy="${it.id}">Copy</button>${del}</div></div>`;
  }
  if(it.kind==='folder'){
    const n=it.files.length;
    const st=it.done?`${n} file${n===1?'':'s'} · ${size(it.size)}`:`uploading ${n}/${it.count}…`;
    const list=openF.has(it.id)?`<div class="flist">${it.files.length?it.files.map(f=>`<a href="/files/${it.id}/${encPath(f.path)}?dl" download="${esc(f.path.split('/').pop())}">${esc(f.path)}<span>${size(f.size)}</span></a>`).join(''):'<div class="meta">Empty folder</div>'}</div>`:'';
    return `<div class="${cls}" data-id="${it.id}">${cb}<div class="ico fold">📁</div><div class="body"><div class="fname">${esc(it.name)}</div><div class="meta">${tag(it)}${st} · ${ago(it.ts)}</div>${list}</div>
    <div class="acts"><button class="btn ghost" data-open="${it.id}">${openF.has(it.id)?'Hide':'Files'}</button>${it.done?`<a class="btn ghost" href="/zip?ids=${it.id}">ZIP</a>`:''}${del}</div></div>`;
  }
  const img=(it.mime||'').startsWith('image/');
  const ext=(it.name.includes('.')?it.name.split('.').pop():'file').slice(0,4);
  const pre=img?`<img class="thumb" loading="lazy" src="/files/${it.id}" alt="">`:`<div class="ico">${esc(ext)}</div>`;
  return `<div class="${cls}" data-id="${it.id}">${cb}${pre}<div class="body"><div class="fname">${esc(it.name)}</div><div class="meta">${tag(it)}${size(it.size)} · ${ago(it.ts)}</div></div>
  <div class="acts"><a class="btn ghost" href="/files/${it.id}" target="_blank">View</a><a class="btn ghost" href="/files/${it.id}?dl" download="${esc(it.name)}">Save</a>${del}</div></div>`;
}
function render(){
  const L=$('#list');
  L.innerHTML=items.length?items.map(itemHTML).join(''):'<div class="empty">Nothing here yet. Send something from your phone, PC or a friend.</div>';
  for(const id of [...sel])if(!items.find(i=>i.id===id))sel.delete(id);
  $('#selcount').textContent=sel.size+' selected';
}
async function poll(){
  try{const r=await api('/api/items?v='+(ver??''));const d=await r.json();
    if(d.same)return;
    const prevTop=items[0]?.id;const first=ver===null;
    ver=d.version;items=d.items;render();
    const top=items[0];
    if(!first&&top&&top.id!==prevTop&&!(top.from===me&&(top.who||'')===myName)){toast('New from '+(top.who||top.from));if(document.hidden)document.title='• Dropzone'}
  }catch(e){}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)document.title='Dropzone'});
$('#list').addEventListener('click',async e=>{
  const c=e.target.closest('[data-copy]'),d=e.target.closest('[data-del]'),o=e.target.closest('[data-open]'),s=e.target.closest('[data-sel]');
  if(c)copy(items.find(i=>i.id===c.dataset.copy).text);
  if(d){await api('/api/items/'+d.dataset.del,{method:'DELETE'});poll()}
  if(o){const id=o.dataset.open;openF.has(id)?openF.delete(id):openF.add(id);render()}
  if(s){s.checked?sel.add(s.dataset.sel):sel.delete(s.dataset.sel);render()}
  else if(selMode&&!e.target.closest('a,button')){const it=e.target.closest('.item');if(it){const id=it.dataset.id;sel.has(id)?sel.delete(id):sel.add(id);render()}}
});
$('#clear').onclick=async()=>{if(confirm('Delete everything for everyone?')){await api('/api/items',{method:'DELETE'});poll()}};

/* ---------- select mode ---------- */
function setSel(on){selMode=on;if(!on)sel.clear();$('#selbar').hidden=!on;$('#selbtn').textContent=on?'Done':'Select';render()}
$('#selbtn').onclick=()=>setSel(!selMode);
$('#selx').onclick=()=>setSel(false);
$('#selall').onclick=()=>{if(sel.size===items.length)sel.clear();else items.forEach(i=>sel.add(i.id));render()};
$('#selzip').onclick=()=>{if(!sel.size)return toast('Select something first');location.href='/zip?ids='+[...sel].join(',')};
$('#seldel').onclick=async()=>{if(!sel.size)return;if(!confirm(`Delete ${sel.size} item(s)?`))return;await api('/api/items/'+[...sel].join(','),{method:'DELETE'});setSel(false);poll()};

/* ---------- text ---------- */
async function sendText(v){
  v=(v??$('#t').value).trim();if(!v)return;
  const r=await api('/api/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})});
  if(r.ok){$('#t').value='';toast('Sent');poll()}else toast('Failed');
}
$('#send').onclick=()=>sendText();
$('#t').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendText()}});

/* ---------- uploads (chunked + resumable) ---------- */
function xhr(blob,h,onp){return new Promise((res,rej)=>{const x=new XMLHttpRequest();x.open('POST','/api/chunk');for(const k in h)x.setRequestHeader(k,h[k]);
  x.upload.onprogress=e=>onp(e.loaded);x.onload=()=>res(x);x.onerror=x.ontimeout=()=>rej(new Error('net'));x.send(blob)})}
async function sendFile(file,folder,rel,onp){
  const uid=rid(),total=file.size;let off=0,tries=0;
  do{
    const end=Math.min(off+CHUNK,total);
    const h=hdr({'X-Upload-Id':uid,'X-Offset':String(off),'X-Total':String(total),'X-Filename':encodeURIComponent(file.name||'file'),'Content-Type':file.type||'application/octet-stream'});
    if(folder){h['X-Folder']=folder;h['X-Relpath']=encodeURIComponent(rel)}
    let x;
    try{x=await xhr(file.slice(off,end),h,l=>onp(off+l))}catch(e){if(++tries>6)throw e;await sleep(1500*tries);continue}
    if(x.status===401){showLogin();throw new Error('auth')}
    if(x.status===409){off=JSON.parse(x.responseText).have;continue}
    if(x.status!==200){if(++tries>6)throw new Error('http '+x.status);await sleep(1500*tries);continue}
    tries=0;off=end;
  }while(off<total);
  onp(total);
}
function progressRow(label){
  const row=document.createElement('div');row.className='item';
  row.innerHTML='<div class="body"><div class="fname"></div><div class="prog"><i></i></div><div class="meta"></div></div>';
  row.querySelector('.fname').textContent=label;$('#up').appendChild(row);
  return{set(p,t){row.querySelector('i').style.width=Math.min(100,p*100)+'%';row.querySelector('.meta').textContent=t||''},done(){row.remove()}};
}
let busy=Promise.resolve();
function uploadPlan(singles,folders){busy=busy.then(()=>runPlan(singles,folders));return busy}
async function runPlan(singles,folders){
  let ok=0,fail=0;
  for(const f of singles){
    const pr=progressRow(f.name);
    try{await sendFile(f,null,null,l=>pr.set(f.size?l/f.size:1,`${size(l)} / ${size(f.size)}`));ok++}
    catch(e){fail++;if(e.message==='auth'){pr.done();return}toast('Failed: '+f.name)}
    pr.done();poll();
  }
  for(const g of folders){
    const total=g.files.reduce((a,b)=>a+b.file.size,0),N=g.files.length;
    let id;
    try{id=(await (await api('/api/folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:g.name,count:N})})).json()).id}catch(e){toast('Failed: '+g.name);continue}
    const pr=progressRow('📁 '+g.name),got=new Array(N).fill(0);let n=0,idx=0,bad=0;
    const upd=()=>{const s=got.reduce((a,b)=>a+b,0);pr.set(total?s/total:(N?n/N:1),`${n}/${N} files · ${size(s)} / ${size(total)}`)};
    upd();
    const worker=async()=>{while(idx<N){const i=idx++;const{file,rel}=g.files[i];
      try{await sendFile(file,id,rel,l=>{got[i]=l;upd()})}catch(e){bad++;if(e.message==='auth')return}
      n++;upd()}};
    await Promise.all([worker(),worker(),worker()]);
    await api('/api/folder/'+id+'/done',{method:'POST'}).catch(()=>{});
    pr.done();poll();bad?(fail++,toast(`${g.name}: ${bad} file(s) failed`)):ok++;
  }
  if(ok&&!fail)toast(ok>1?ok+' items sent':'Sent');
}
function fromList(list){
  const singles=[],groups={};
  for(const f of list){const p=f.webkitRelativePath||'';
    if(p.includes('/')){const[top,...rest]=p.split('/');(groups[top]=groups[top]||[]).push({file:f,rel:rest.join('/')})}else singles.push(f)}
  return[singles,Object.entries(groups).map(([name,files])=>({name,files}))];
}
$('#f').onchange=e=>{uploadPlan([...e.target.files],[]);e.target.value=''};
$('#fd').onchange=e=>{const[s,g]=fromList([...e.target.files]);if(!s.length&&!g.length)toast('Folder is empty');uploadPlan(s,g);e.target.value=''};
const readAll=async d=>{const r=d.createReader();let out=[];for(;;){const b=await new Promise((res,rej)=>r.readEntries(res,rej));if(!b.length)break;out=out.concat(b)}return out};
const fileOf=en=>new Promise((res,rej)=>en.file(res,rej));
async function walk(en,prefix,out){
  if(en.isFile){const f=await fileOf(en);out.push({file:f,rel:prefix+f.name})}
  else if(en.isDirectory){for(const c of await readAll(en))await walk(c,prefix+en.name+'/',out)}
}
const dz=$('#drop');let dragN=0;
document.addEventListener('dragenter',e=>{e.preventDefault();dragN++;dz.classList.add('on')});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragleave',e=>{if(--dragN<=0){dragN=0;dz.classList.remove('on')}});
document.addEventListener('drop',async e=>{
  e.preventDefault();dragN=0;dz.classList.remove('on');if($('#app').hidden)return;
  const its=[...(e.dataTransfer.items||[])].filter(i=>i.kind==='file');
  const entries=its.map(i=>i.webkitGetAsEntry?i.webkitGetAsEntry():null);
  const plain=[...(e.dataTransfer.files||[])];
  if(!entries.length||!entries.every(Boolean))return uploadPlan(plain,[]);
  const singles=[],folders=[];
  for(const en of entries){
    if(en.isDirectory){const files=[];for(const c of await readAll(en))await walk(c,'',files);folders.push({name:en.name,files})}
    else singles.push(await fileOf(en));
  }
  uploadPlan(singles,folders);
});
document.addEventListener('paste',e=>{
  if($('#app').hidden)return;
  const fs=[...(e.clipboardData?.files||[])];
  if(fs.length){e.preventDefault();uploadPlan(fs,[]);return}
  if(document.activeElement!==$('#t')){const t=e.clipboardData.getData('text');if(t){e.preventDefault();sendText(t)}}
});

/* ---------- links panel ---------- */
$('#linksbtn').onclick=async()=>{
  const p=$('#links');p.hidden=!p.hidden;if(p.hidden)return;
  const d=await (await api('/api/info')).json();
  const L=[];
  if(d.stable)L.push(['Permanent link (give this to friends)',d.stable]);
  if(d.public)L.push(['Internet link (changes on restart)',d.public]);
  for(const ip of d.ips)L.push(['Same Wi-Fi',`http://${ip}:${d.port}/`]);
  if(!L.length)L.push(['This PC only',location.origin+'/']);
  $('#linklist').innerHTML=L.map(([k,u])=>`<div class="lk"><div><b>${esc(k)}</b><code>${esc(u)}</code></div><button class="btn ghost" data-cp="${esc(u)}">Copy</button></div>`).join('')
    +(!d.public&&!d.stable?'<div class="meta">Internet sharing is off. Run <code>dropzone --set-pin</code> on the PC to share with friends anywhere.</div>':'');
  $('#linklist').querySelectorAll('[data-cp]').forEach(b=>b.onclick=()=>copy(b.dataset.cp));
  $('#qr').innerHTML='';const best=L[0][1];
  if(window.QRCode){new QRCode($('#qr'),{text:best,width:180,height:180});$('#qrcap').textContent='Scan: '+L[0][0].replace(/ \(.*/,'')}else{$('#qr').hidden=true}
};
</script></body></html>"""


if __name__ == "__main__":
    main()
