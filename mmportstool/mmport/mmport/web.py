"""
Yerel arayuz — yalnizca standart kutuphane.

Tarayicida acilir ama disari hicbir sey gitmez; sunucu 127.0.0.1'e baglidir.
mmport.app bu sunucuyu baslatip tarayiciyi acar.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .pipeline import port

STATE = {
    "running": False,
    "lines": [],
    "stage": "",
    "done": False,
    "ok": False,
    "plugin": None,
    "project": None,
}
LOCK = threading.Lock()

# Gunluk satirindan hangi asamada oldugumuzu cikaran ipuclari.
# Kullaniciya ham gunluk yerine tek satirlik bir durum gostermek icin.
STAGES = [
    ("klonlan", "Kaynak indiriliyor"),
    ("arsiv aciliyor", "Arşiv açılıyor"),
    ("kaynak taraniyor", "Kaynak taranıyor"),
    ("portlanacak", "Modüller seçildi"),
    ("proje yazildi", "Proje dosyaları üretildi"),
    ("assets:", "Grafikler dönüştürülüyor"),
    ("toolchain indiriliyor", "Toolchain indiriliyor (~160 MB)"),
    ("toolchain aciliyor", "Toolchain açılıyor"),
    ("SDK klonlan", "SDK indiriliyor"),
    ("derleniyor", "Derleniyor"),
]


def _log(line: str) -> None:
    text = str(line)
    with LOCK:
        STATE["lines"].append(text)
        for needle, label in STAGES:
            if needle in text:
                STATE["stage"] = label
                break


def _run(source: str, out: str, strict: bool, do_build: bool) -> None:
    try:
        res = port(source, Path(out).expanduser() if out else None,
                   strict=strict, do_build=do_build, log=_log)
        with LOCK:
            STATE["ok"] = res.ok
            STATE["plugin"] = str(res.plugin_path) if res.plugin_path else None
            STATE["project"] = str(res.project_dir) if res.project_dir else None
            STATE["stage"] = "Bitti" if res.ok else "Başarısız"
        _log("")
        _log(res.message)
    except Exception as exc:  # noqa: BLE001
        _log("")
        _log(f"HATA: {exc}")
        with LOCK:
            STATE["stage"] = "Başarısız"
    finally:
        with LOCK:
            STATE["running"] = False
            STATE["done"] = True


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mmport</title>
<style>
  :root{--fg:#ececec;--bg:#0a0a0a;--dim:#707070;--line:#232323;--panel:#101010;--acc:#fff}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--fg);
       font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
       -webkit-font-smoothing:antialiased}
  main{max-width:760px;margin:0 auto;padding:44px 26px 70px}
  h1{font-size:12px;font-weight:400;letter-spacing:.42em;text-transform:uppercase;margin:0}
  .sub{color:var(--dim);margin:6px 0 34px;font-size:12px}

  #drop{border:1px dashed var(--line);background:var(--panel);padding:26px 20px;
        text-align:center;color:var(--dim);transition:.12s}
  #drop.hot{border-color:var(--acc);color:var(--fg);background:#161616}
  #drop b{color:var(--fg);font-weight:400}

  label{display:block;color:var(--dim);margin:22px 0 7px;font-size:11px;letter-spacing:.1em;
        text-transform:uppercase}
  input[type=text]{width:100%;padding:11px 13px;background:var(--panel);color:var(--fg);
        border:1px solid var(--line);border-radius:0;font:inherit}
  input[type=text]:focus{outline:none;border-color:#4d4d4d}

  .row{display:flex;gap:26px;align-items:center;margin:22px 0 0;color:var(--dim)}
  .row label{margin:0;display:flex;gap:9px;align-items:center;cursor:pointer;
        text-transform:none;letter-spacing:0;font-size:12px}
  input[type=checkbox]{accent-color:#c8c8c8}

  .actions{display:flex;gap:12px;align-items:center;margin-top:26px;flex-wrap:wrap}
  button{padding:12px 30px;background:var(--fg);color:#000;border:0;font:inherit;
        letter-spacing:.16em;text-transform:uppercase;cursor:pointer}
  button:disabled{background:#262626;color:#5e5e5e;cursor:default}
  button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line);
        letter-spacing:.08em;padding:12px 22px}
  button.ghost:hover{border-color:#4d4d4d}
  button.ghost[hidden]{display:none}

  #stage{margin-top:30px;display:flex;align-items:center;gap:14px;color:var(--dim);font-size:12px}
  #bar{flex:1;height:1px;background:var(--line);position:relative;overflow:hidden}
  #bar.live::after{content:"";position:absolute;inset:0;width:34%;background:var(--acc);
        animation:sweep 1.15s linear infinite}
  @keyframes sweep{from{transform:translateX(-100%)}to{transform:translateX(390%)}}

  details{margin-top:18px}
  summary{color:var(--dim);cursor:pointer;font-size:12px;list-style:none}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"› "}
  details[open] summary::before{content:"⌄ "}
  pre{margin:12px 0 0;padding:16px;background:#060606;border:1px solid var(--line);
        max-height:46vh;overflow:auto;white-space:pre-wrap;word-break:break-word;
        color:#b9b9b9;font-size:11.5px;line-height:1.65}

  #result{margin-top:26px;padding:16px 18px;border:1px solid var(--line);background:var(--panel);
        display:none}
  #result.show{display:block}
  #result.bad{border-color:#4a2a22}
  #result h2{margin:0 0 8px;font-size:12px;font-weight:400;letter-spacing:.14em;
        text-transform:uppercase}
  #result .path{color:var(--dim);word-break:break-all;font-size:12px}
  .hint{color:#575757;margin-top:34px;border-top:1px solid var(--line);padding-top:16px;
        font-size:11.5px;line-height:1.7}
</style>
<main>
  <h1>mmport</h1>
  <p class="sub">VCV Rack &rarr; MetaModule</p>

  <div id="drop">Klasörü ya da <b>.zip</b> dosyasını buraya bırak<br>&mdash; veya aşağıya bir git adresi yaz</div>

  <label for="src">Kaynak</label>
  <input type="text" id="src" placeholder="https://github.com/gosub/forsitan-modulare" autofocus>

  <label for="out">Çıktı klasörü <span style="text-transform:none;letter-spacing:0">(boş bırakılabilir)</span></label>
  <input type="text" id="out" placeholder="~/mmport-out/&lt;slug&gt;-metamodule">

  <div class="row">
    <label><input type="checkbox" id="strict" checked> yalnızca sorunsuz çalışacaklar</label>
    <label><input type="checkbox" id="build" checked> derle</label>
  </div>

  <div class="actions">
    <button id="go">Portla</button>
    <button id="reveal" class="ghost" hidden>Finder'da göster</button>
  </div>

  <div id="stage"><span id="stagetext">Hazır</span><span id="bar"></span></div>

  <div id="result">
    <h2 id="rtitle"></h2>
    <div class="path" id="rpath"></div>
  </div>

  <details><summary>Günlük</summary><pre id="log"></pre></details>

  <p class="hint">İlk çalıştırmada ARM toolchain (~160&nbsp;MB) ve SDK indirilir; sonrakiler hızlıdır.
     Her şey <code>~/.mmport</code> altında tutulur. Sunucu yalnızca bu bilgisayarda dinler.</p>
</main>
<script>
const $ = id => document.getElementById(id);
let n = 0, timer = null, lastPath = null;

const drop = $("drop");
["dragenter", "dragover"].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault();
  drop.classList.add("hot");
}));
["dragleave", "drop"].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault();
  drop.classList.remove("hot");
}));
drop.addEventListener("drop", ev => {
  const f = ev.dataTransfer.files[0];
  if (!f) return;
  // Tarayici guvenlik gerekcesiyle tam yolu vermez; kullaniciya durumu soyluyoruz.
  if (f.path) {
    $("src").value = f.path;
  } else {
    $("src").value = f.name;
    drop.innerHTML = "Tarayıcı tam yolu vermiyor &mdash; <b>" + f.name +
      "</b> için Finder'da sağ tık › <b>Bilgi al</b>'dan yolu kopyalayıp aşağıya yapıştır.";
  }
});

$("go").onclick = async () => {
  const src = $("src").value.trim();
  if (!src) { $("src").focus(); return; }
  $("go").disabled = true;
  $("reveal").hidden = true;
  $("result").className = "";
  $("log").textContent = "";
  $("bar").classList.add("live");
  $("stagetext").textContent = "Başlıyor";
  n = 0;
  await fetch("/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      source: src, out: $("out").value.trim(),
      strict: $("strict").checked, build: $("build").checked
    })
  });
  timer = setInterval(poll, 400);
};

$("reveal").onclick = () => {
  if (lastPath) fetch("/reveal?path=" + encodeURIComponent(lastPath));
};

async function poll() {
  const d = await (await fetch("/log?from=" + n)).json();
  if (d.lines.length) {
    $("log").textContent += d.lines.join("\n") + "\n";
    n += d.lines.length;
    $("log").scrollTop = $("log").scrollHeight;
  }
  if (d.stage) $("stagetext").textContent = d.stage;
  if (!d.done) return;

  clearInterval(timer);
  $("go").disabled = false;
  $("bar").classList.remove("live");
  lastPath = d.plugin || d.project;

  $("result").className = "show" + (d.ok ? "" : " bad");
  $("rtitle").textContent = d.ok ? "Hazır" : "Başarısız";
  $("rpath").textContent = d.ok ? (lastPath || "") : "Ayrıntı için günlüğe bak.";
  if (d.ok && lastPath && d.native) $("reveal").hidden = false;
  if (!d.ok) document.querySelector("details").open = true;
}
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/":
            return self._send(200, PAGE)

        if u.path == "/log":
            start = int(parse_qs(u.query).get("from", ["0"])[0])
            with LOCK:
                payload = {
                    "lines": STATE["lines"][start:],
                    "stage": STATE["stage"],
                    "done": STATE["done"] and not STATE["running"],
                    "ok": STATE["ok"],
                    "plugin": STATE["plugin"],
                    "project": STATE["project"],
                    "native": sys.platform in ("darwin", "linux"),
                }
            return self._send(200, json.dumps(payload), "application/json")

        if u.path == "/reveal":
            target = parse_qs(u.query).get("path", [""])[0]
            ok = False
            if target:
                p = Path(target).expanduser()
                if p.exists():
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", "-R", str(p)])
                        ok = True
                    elif sys.platform.startswith("linux"):
                        subprocess.Popen(["xdg-open", str(p.parent)])
                        ok = True
            return self._send(200, json.dumps({"ok": ok}), "application/json")

        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/start":
            return self._send(404, "not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            if STATE["running"]:
                return self._send(409, json.dumps({"error": "zaten calisiyor"}),
                                  "application/json")
            STATE.update(running=True, lines=[], stage="", done=False, ok=False,
                         plugin=None, project=None)
        threading.Thread(
            target=_run,
            args=(req.get("source", ""), req.get("out", ""),
                  bool(req.get("strict", True)), bool(req.get("build", True))),
            daemon=True,
        ).start()
        self._send(200, json.dumps({"started": True}), "application/json")


def free_port(preferred: int = 8731) -> int:
    """Tercih edilen port doluysa isletim sisteminden bos bir tane ister."""
    for candidate in (preferred, 0):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", candidate))
            got = s.getsockname()[1]
            s.close()
            return got
        except OSError:
            continue
    return preferred


def serve(port_num: int | None = None, open_browser: bool = True) -> int:
    port_num = port_num or free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port_num), Handler)
    url = f"http://127.0.0.1:{port_num}/"
    print(f"mmport: {url}   (durdurmak icin Ctrl-C)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nkapatiliyor")
    return 0
