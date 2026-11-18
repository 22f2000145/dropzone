#!/usr/bin/env python3
"""Dropzone - tiny local phone <-> PC transfer. Stdlib only.

Run:  python3 dropzone.py [--port 8765]
Then open the shown URL on your phone (same Wi-Fi / hotspot).
"""
import argparse, json, mimetypes, os, shutil, socket, subprocess, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, unquote

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
FILES = DATA / "files"
INDEX = DATA / "items.json"
LOCK = threading.Lock()
STATE = {"items": [], "version": 0}


def load():
    FILES.mkdir(parents=True, exist_ok=True)
    if INDEX.exists():
        try:
            STATE["items"] = json.loads(INDEX.read_text())
        except Exception:
            STATE["items"] = []
    STATE["version"] = int(time.time() * 1000)


def save():
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE["items"]))
    tmp.replace(INDEX)
    STATE["version"] += 1


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
    return "phone" if any(k in ua for k in ("android", "iphone", "mobile")) else "pc"


class H(BaseHTTPRequestHandler):
    server_version = "Dropzone"

    def log_message(self, *a):
        pass

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

    # ---------- GET ----------
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/items":
            with LOCK:
                return self.send(200, {"version": STATE["version"], "items": STATE["items"]})
        if p == "/api/info":
            return self.send(200, {"ips": lan_ips(), "port": self.server.server_address[1]})
        if p.startswith("/files/"):
            iid = p.split("/")[2]
            dl = "?dl" in self.path
            with LOCK:
                it = next((i for i in STATE["items"] if i["id"] == iid and i["kind"] == "file"), None)
            fp = FILES / iid if it else None
            if not fp or not fp.exists():
                return self.send(404, {"error": "not found"})
            disp = "attachment" if dl else "inline"
            self.send_response(200)
            self.send_header("Content-Type", it.get("mime") or "application/octet-stream")
            self.send_header("Content-Length", str(fp.stat().st_size))
            self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(it['name'])}")
            self.end_headers()
            with open(fp, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return
        self.send(404, {"error": "not found"})

    # ---------- POST ----------
    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        src = device_of(self.headers.get("User-Agent"))
        if p == "/api/text":
            try:
                text = json.loads(self.rfile.read(n) or b"{}").get("text", "").strip()
            except Exception:
                return self.send(400, {"error": "bad json"})
            if not text:
                return self.send(400, {"error": "empty"})
            it = {"id": uuid.uuid4().hex[:12], "kind": "text", "text": text, "from": src, "ts": time.time()}
            with LOCK:
                STATE["items"].insert(0, it)
                save()
            return self.send(200, it)
        if p == "/api/upload":
            name = unquote(self.headers.get("X-Filename") or "file").replace("/", "_").replace("\\", "_")[:200] or "file"
            iid = uuid.uuid4().hex[:12]
            fp = FILES / iid
            left = n
            try:
                with open(fp, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
            except Exception:
                fp.unlink(missing_ok=True)
                return self.send(500, {"error": "upload failed"})
            if left > 0:
                fp.unlink(missing_ok=True)
                return self.send(400, {"error": "incomplete upload"})
            mime = self.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
            it = {"id": iid, "kind": "file", "name": name, "size": n, "mime": mime, "from": src, "ts": time.time()}
            with LOCK:
                STATE["items"].insert(0, it)
                save()
            return self.send(200, it)
        self.send(404, {"error": "not found"})

    # ---------- DELETE ----------
    def do_DELETE(self):
        p = self.path.split("?")[0]
        with LOCK:
            if p == "/api/items":
                for it in STATE["items"]:
                    (FILES / it["id"]).unlink(missing_ok=True)
                STATE["items"] = []
                save()
                return self.send(200, {"ok": True})
            if p.startswith("/api/items/"):
                iid = p.rsplit("/", 1)[1]
                STATE["items"] = [i for i in STATE["items"] if i["id"] != iid]
                (FILES / iid).unlink(missing_ok=True)
                save()
                return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Dropzone</title>
<meta name="theme-color" content="#0f1115">
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14161a;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;--acc2:#dbeafe;--danger:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--fg:#e8eaee;--mut:#8b93a1;--line:#272b33;--acc:#5b8cff;--acc2:#1c2740;--danger:#f87171}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:16px 16px 80px}
header{display:flex;align-items:center;justify-content:space-between;margin:4px 0 16px}
h1{font-size:20px;margin:0;letter-spacing:-.02em}
h1 span{color:var(--acc)}
.btn{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:10px;padding:8px 12px;font:inherit;font-size:14px;cursor:pointer}
.btn:hover{border-color:var(--acc)}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.ghost{border:none;background:none;color:var(--mut);padding:6px 8px}
.btn.ghost:hover{color:var(--fg)}
.compose{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px}
textarea{width:100%;border:none;outline:none;resize:none;background:transparent;color:var(--fg);font:inherit;min-height:52px}
.row{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:8px}
.drop{margin-top:12px;border:2px dashed var(--line);border-radius:14px;padding:22px;text-align:center;color:var(--mut);cursor:pointer;transition:.15s}
.drop.on{border-color:var(--acc);background:var(--acc2);color:var(--fg)}
.list{margin-top:20px;display:flex;flex-direction:column;gap:10px}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;display:flex;gap:12px;align-items:flex-start}
.item .body{flex:1;min-width:0}
.txt{white-space:pre-wrap;word-break:break-word}
.txt a{color:var(--acc)}
.meta{font-size:12px;color:var(--mut);margin-top:4px}
.fname{font-weight:600;word-break:break-all}
.thumb{width:64px;height:64px;border-radius:10px;object-fit:cover;background:var(--line);flex:none}
.ico{width:64px;height:64px;border-radius:10px;background:var(--acc2);color:var(--acc);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none;text-transform:uppercase}
.acts{display:flex;gap:2px;flex:none;align-items:center}
.empty{color:var(--mut);text-align:center;padding:40px 0}
.prog{height:4px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:6px}
.prog i{display:block;height:100%;background:var(--acc);width:0}
#qrbox{display:none;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:16px;text-align:center}
#qrbox.show{display:block}
#qr{display:inline-block;background:#fff;padding:10px;border-radius:10px}
#qrurl{margin-top:8px;font-family:ui-monospace,monospace;font-size:14px}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--fg);color:var(--bg);padding:8px 14px;border-radius:10px;font-size:14px;opacity:0;transition:.2s;pointer-events:none}
.toast.show{opacity:1}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;background:var(--acc2);color:var(--acc);margin-right:6px}
</style></head><body>
<div class="wrap">
<header><h1>Drop<span>zone</span></h1>
<div><button class="btn" id="qrbtn">Connect phone</button> <button class="btn ghost" id="clear">Clear all</button></div></header>
<div id="qrbox"><div id="qr"></div><div id="qrurl"></div><div class="meta">Scan with your phone camera (same Wi-Fi / hotspot)</div></div>
<div class="compose">
<textarea id="t" placeholder="Paste a link or type text…"></textarea>
<div class="row"><span class="meta" id="hint"></span><button class="btn pri" id="send">Send</button></div>
</div>
<label class="drop" id="drop"><input type="file" id="f" multiple hidden><span id="droptxt">Tap to choose files</span></label>
<div id="up"></div>
<div class="list" id="list"></div>
</div>
<div class="toast" id="toast"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
const $=s=>document.querySelector(s);
const mobile=/android|iphone|mobile/i.test(navigator.userAgent);
const me=mobile?'phone':'pc';
$('#droptxt').textContent=mobile?'Tap to choose files / photos':'Drop files here, click to choose, or Ctrl+V anywhere';
$('#hint').textContent=mobile?'':'Ctrl+Enter to send';
if(mobile)$('#qrbtn').style.display='none';
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),1600)}
function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function linkify(s){return esc(s).replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>')}
function size(n){const u=['B','KB','MB','GB'];let i=0;while(n>=1024&&i<3){n/=1024;i++}return (i?n.toFixed(1):n)+' '+u[i]}
function ago(ts){const s=Date.now()/1000-ts;if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return new Date(ts*1000).toLocaleDateString()}
function copy(text){
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(()=>toast('Copied'));return}
  const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();
  try{document.execCommand('copy');toast('Copied')}catch(e){toast('Copy failed')}ta.remove();
}
let ver=-1,items=[];
function render(){
  const L=$('#list');
  if(!items.length){L.innerHTML='<div class="empty">Nothing here yet. Send something from your phone or PC.</div>';return}
  L.innerHTML=items.map(it=>{
    const who=`<span class="tag">${it.from==='phone'?'📱 phone':'💻 pc'}</span>`;
    if(it.kind==='text'){
      const isUrl=/^https?:\/\/\S+$/.test(it.text);
      return `<div class="item"><div class="body"><div class="txt">${linkify(it.text)}</div><div class="meta">${who}${ago(it.ts)}</div></div>
      <div class="acts">${isUrl?`<a class="btn ghost" href="${esc(it.text)}" target="_blank" rel="noopener">Open</a>`:''}<button class="btn ghost" data-copy="${it.id}">Copy</button><button class="btn ghost" data-del="${it.id}">✕</button></div></div>`;
    }
    const img=(it.mime||'').startsWith('image/');
    const ext=(it.name.split('.').pop()||'file').slice(0,4);
    const pre=img?`<img class="thumb" loading="lazy" src="/files/${it.id}">`:`<div class="ico">${esc(ext)}</div>`;
    return `<div class="item">${pre}<div class="body"><div class="fname">${esc(it.name)}</div><div class="meta">${who}${size(it.size)} · ${ago(it.ts)}</div></div>
    <div class="acts"><a class="btn ghost" href="/files/${it.id}" target="_blank">View</a><a class="btn ghost" href="/files/${it.id}?dl" download="${esc(it.name)}">Save</a><button class="btn ghost" data-del="${it.id}">✕</button></div></div>`;
  }).join('');
}
async function poll(){
  try{const r=await fetch('/api/items');const d=await r.json();
    if(d.version!==ver){
      const fresh=ver!==-1&&d.items.length&&(!items.length||d.items[0].id!==items[0].id)&&d.items[0].from!==me;
      ver=d.version;items=d.items;render();
      if(fresh){toast('New from '+d.items[0].from);document.title='• Dropzone';}
    }
  }catch(e){}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)document.title='Dropzone'});
setInterval(()=>{poll();if(ver!==-1)render()},2000);poll();
$('#list').addEventListener('click',async e=>{
  const c=e.target.closest('[data-copy]'),d=e.target.closest('[data-del]');
  if(c){copy(items.find(i=>i.id===c.dataset.copy).text)}
  if(d){await fetch('/api/items/'+d.dataset.del,{method:'DELETE'});poll()}
});
$('#clear').onclick=async()=>{if(confirm('Delete everything?')){await fetch('/api/items',{method:'DELETE'});poll()}};
async function sendText(){
  const v=$('#t').value.trim();if(!v)return;
  const r=await fetch('/api/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})});
  if(r.ok){$('#t').value='';toast('Sent');poll()}else toast('Failed');
}
$('#send').onclick=sendText;
$('#t').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendText()}});
function upload(file){
  return new Promise(res=>{
    const row=document.createElement('div');row.className='item';
    row.innerHTML=`<div class="body"><div class="fname">${esc(file.name)}</div><div class="prog"><i></i></div></div>`;
    $('#up').appendChild(row);const bar=row.querySelector('i');
    const x=new XMLHttpRequest();x.open('POST','/api/upload');
    x.setRequestHeader('X-Filename',encodeURIComponent(file.name||'file'));
    if(file.type)x.setRequestHeader('Content-Type',file.type);
    x.upload.onprogress=e=>{if(e.lengthComputable)bar.style.width=(e.loaded/e.total*100)+'%'};
    x.onloadend=()=>{row.remove();if(x.status!==200)toast('Upload failed: '+file.name);poll();res()};
    x.send(file);
  });
}
async function uploadAll(fs){for(const f of fs)await upload(f);if(fs.length)toast(fs.length>1?fs.length+' files sent':'Sent')}
$('#f').onchange=e=>{uploadAll([...e.target.files]);e.target.value=''};
const dz=$('#drop');
['dragenter','dragover'].forEach(ev=>document.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('on')}));
['dragleave','drop'].forEach(ev=>document.addEventListener(ev,e=>{e.preventDefault();if(ev==='drop'||!e.relatedTarget)dz.classList.remove('on')}));
document.addEventListener('drop',e=>{const fs=[...(e.dataTransfer?.files||[])];if(fs.length)uploadAll(fs)});
document.addEventListener('paste',e=>{
  const fs=[...(e.clipboardData?.files||[])];
  if(fs.length){e.preventDefault();uploadAll(fs);return}
  if(document.activeElement!==$('#t')){const t=e.clipboardData.getData('text');if(t){$('#t').value=t;sendText()}}
});
$('#qrbtn').onclick=async()=>{
  const b=$('#qrbox');b.classList.toggle('show');if(!b.classList.contains('show')||b._done)return;
  const d=await (await fetch('/api/info')).json();
  const host=(location.hostname!=='localhost'&&location.hostname!=='127.0.0.1')?location.hostname:(d.ips[0]||location.hostname);
  const url=`http://${host}:${d.port}/`;
  $('#qrurl').textContent=url+(d.ips.length>1?'   (others: '+d.ips.slice(1).join(', ')+')':'');
  if(window.QRCode){new QRCode($('#qr'),{text:url,width:180,height:180});}else{$('#qr').style.display='none'}
  b._done=true;
};
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    load()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    print("\n  Dropzone is running\n")
    print(f"  PC:    http://localhost:{a.port}")
    nat = False
    try:
        nat = subprocess.run(["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=2).stdout.strip() == "nat"
    except Exception:
        pass
    if nat:
        print("\n  \033[33m! Phone can't connect yet: WSL is in NAT mode.\033[0m")
        print("    Open this folder in Windows Explorer and double-click Setup-Dropzone.bat (one time):")
        print(f"    \\\\wsl.localhost\\{os.environ.get('WSL_DISTRO_NAME','kali-linux')}{BASE}".replace("/", "\\"))
    else:
        for ip in lan_ips():
            print(f"  Phone: http://{ip}:{a.port}")
    print("\n  Click 'Connect phone' on the PC page for a QR code. Ctrl+C to stop.\n")
    if not a.no_browser:
        url = f"http://localhost:{a.port}"
        for cmd in (["cmd.exe", "/c", "start", url], ["xdg-open", url]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd="/")
                break
            except Exception:
                continue
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
