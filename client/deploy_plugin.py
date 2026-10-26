#!/usr/bin/env python3
"""Upload lockall plugin to a jailbroken PS4 (GoldHEN) or PS5 (etaHEN) over FTP.

On PS4 also merges the plugin path into /data/GoldHEN/plugins.ini under
[default] (creates the file if missing, keeps other entries).

GUI (default — opens in the browser):
    python3 deploy_plugin.py

CLI:
    python3 deploy_plugin.py --cli
    python3 deploy_plugin.py --ip 192.168.1.13 --console ps5
    python3 deploy_plugin.py --ip 192.168.1.50 --console ps4
"""

from __future__ import print_function

import argparse
import io
import json
import os
import socket
import sys
import threading
import webbrowser
from ftplib import FTP, error_perm
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

def _app_dirs():
    """Return (exe_or_script_dir, project_root, bundle_dir) for frozen/dev."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bundle = getattr(sys, "_MEIPASS", exe_dir)
        return exe_dir, exe_dir, bundle
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return here, root, root


HERE, ROOT, BUNDLE = _app_dirs()


def _find_plugin(filename, *rel_dirs):
    """Locate a plugin next to the exe, in the PyInstaller bundle, or in the repo."""
    candidates = [
        os.path.join(HERE, filename),
        os.path.join(ROOT, filename),
        os.path.join(BUNDLE, "plugins", filename),
        os.path.join(BUNDLE, filename),
    ]
    for rel in rel_dirs:
        candidates.append(os.path.join(ROOT, rel, filename))
        candidates.append(os.path.join(BUNDLE, rel, filename))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    # Prefer the canonical repo path in error messages
    if rel_dirs:
        return os.path.join(ROOT, rel_dirs[0], filename)
    return os.path.join(ROOT, filename)


def get_targets():
    return {
        "ps4": {
            "label": "PS4 / GoldHEN",
            "local": _find_plugin(
                "lockall.prx",
                os.path.join("lockall_plugin", "build"),
            ),
            "remote_dir": "/data/GoldHEN/plugins",
            "remote_name": "lockall.prx",
            "plugin_path": "/data/GoldHEN/plugins/lockall.prx",
            "ini_path": "/data/GoldHEN/plugins.ini",
            "hint": "Enable Plugin Loader in GoldHEN, then launch a game.",
        },
        "ps5": {
            "label": "PS5 / etaHEN",
            "local": _find_plugin(
                "lockall_ps5.plugin",
                os.path.join("ps5_lockall_plugin", "build"),
            ),
            "remote_dir": "/data/etaHEN/plugins",
            "remote_name": "lockall_ps5.plugin",
            "plugin_path": None,
            "ini_path": None,
            "hint": "Reboot the PS5 (or reload plugins from the etaHEN toolbox).",
        },
    }


# Kept for callers/tests that expect a module-level mapping; rebuilt each access
# via get_targets() inside upload/_targets_for_page so frozen paths stay fresh.
TARGETS = get_targets()

FTP_PORT = 2121
FTP_USER = "anonymous"
FTP_PASS = "anonymous"
TIMEOUT = 10.0

DEFAULT_INI = """\
; /data/GoldHEN/plugins.ini
; Managed by deploy_plugin.py — other plugins are preserved on update.

[default]
/data/GoldHEN/plugins/lockall.prx
"""

HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>lockall — Deploy Plugin</title>
<style>
  :root {
    --bg: #14161c;
    --panel: #1e222b;
    --fg: #e8eaed;
    --muted: #9aa0a6;
    --accent: #3d8bfd;
    --accent-hover: #5ba0ff;
    --ok: #3dd68c;
    --err: #f07178;
    --border: #2c313c;
    --input: #12151a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font-family: "Segoe UI", system-ui, sans-serif;
    background:
      radial-gradient(ellipse 80% 50% at 10% -10%, #1a2a44 0%, transparent 55%),
      radial-gradient(ellipse 60% 40% at 100% 0%, #1a2830 0%, transparent 50%),
      var(--bg);
    color: var(--fg);
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }
  .wrap { width: 100%; max-width: 520px; }
  h1 { margin: 0 0 4px; font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; }
  .sub { margin: 0 0 20px; color: var(--muted); font-size: 0.9rem; }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px 18px 14px;
  }
  label {
    display: block; font-size: 0.8rem; color: var(--muted);
    margin: 0 0 6px; font-weight: 600;
  }
  .field { margin-bottom: 14px; }
  input[type=text], input[type=number] {
    width: 100%; padding: 10px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--input); color: var(--fg);
    font-size: 1rem; outline: none;
  }
  input:focus { border-color: var(--accent); }
  .radios { display: flex; gap: 18px; flex-wrap: wrap; }
  .radios label {
    display: flex; align-items: center; gap: 8px; margin: 0;
    color: var(--fg); font-size: 0.95rem; font-weight: 500; cursor: pointer;
  }
  .radios input { accent-color: var(--accent); width: 16px; height: 16px; }
  .dest {
    margin-top: 4px; font-size: 0.82rem; color: var(--muted);
    font-family: ui-monospace, Consolas, monospace; word-break: break-all;
  }
  .dest.missing { color: var(--err); }
  .row { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
  button {
    border: 0; border-radius: 8px; padding: 10px 22px;
    background: var(--accent); color: #fff; font-weight: 700;
    font-size: 0.95rem; cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--accent-hover); }
  button:disabled { opacity: 0.55; cursor: wait; }
  #status { font-size: 0.9rem; color: var(--muted); }
  #status.ok { color: var(--ok); }
  #status.err { color: var(--err); }
  #status.busy { color: var(--accent); }
  pre {
    margin: 16px 0 0; padding: 12px 14px; min-height: 160px; max-height: 280px;
    overflow: auto; background: var(--input); border: 1px solid var(--border);
    border-radius: 10px; font-size: 0.8rem; line-height: 1.45;
    font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap;
    color: var(--muted);
  }
  pre .ok { color: var(--ok); }
  pre .err { color: var(--err); }
</style>
</head>
<body>
  <div class="wrap">
    <h1>Deploy lockall plugin</h1>
    <p class="sub">Upload over GoldHEN / etaHEN FTP</p>
    <div class="card">
      <div class="field">
        <label for="ip">Console IP</label>
        <input id="ip" type="text" value="192.168.1.13" autocomplete="off"/>
      </div>
      <div class="field">
        <label>Console</label>
        <div class="radios">
          <label><input type="radio" name="console" value="ps4"/> PS4 / GoldHEN</label>
          <label><input type="radio" name="console" value="ps5" checked/> PS5 / etaHEN</label>
        </div>
      </div>
      <div class="field">
        <label for="port">FTP port</label>
        <input id="port" type="number" value="2121" min="1" max="65535" style="width:120px"/>
      </div>
      <div id="dest" class="dest"></div>
      <div class="row">
        <button id="go" type="button">Deploy</button>
        <span id="status"></span>
      </div>
    </div>
    <pre id="log"></pre>
  </div>
<script>
const TARGETS = __TARGETS_JSON__;
const destEl = document.getElementById("dest");
const logEl = document.getElementById("log");
const statusEl = document.getElementById("status");
const goBtn = document.getElementById("go");

function selectedConsole() {
  return document.querySelector('input[name="console"]:checked').value;
}

function refreshDest() {
  const t = TARGETS[selectedConsole()];
  destEl.textContent = "→ " + t.remote_dir + "/" + t.remote_name +
    (t.exists ? "  (ready)" : "  (missing — build first)");
  destEl.className = "dest" + (t.exists ? "" : " missing");
}

document.querySelectorAll('input[name="console"]').forEach(r =>
  r.addEventListener("change", refreshDest));
refreshDest();

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

goBtn.addEventListener("click", async () => {
  const ip = document.getElementById("ip").value.trim();
  const port = document.getElementById("port").value.trim();
  const consoleType = selectedConsole();
  if (!ip) { setStatus("Enter an IP", "err"); return; }

  goBtn.disabled = true;
  setStatus("Uploading…", "busy");
  logEl.textContent = "";

  try {
    const res = await fetch("/deploy", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ip, port: Number(port), console: consoleType}),
    });
    const data = await res.json();
    const lines = (data.log || []).map(line => {
      if (/^ok\\s*:|^Deploy finished|^ini\\s*:/.test(line))
        return '<span class="ok">' + escapeHtml(line) + "</span>";
      if (/failed|error|missing|ERR/i.test(line))
        return '<span class="err">' + escapeHtml(line) + "</span>";
      return escapeHtml(line);
    });
    logEl.innerHTML = lines.join("\\n");
    if (data.ok) {
      setStatus("Done", "ok");
    } else {
      setStatus("Failed", "err");
    }
  } catch (e) {
    logEl.innerHTML = '<span class="err">' + escapeHtml(String(e)) + "</span>";
    setStatus("Failed", "err");
  } finally {
    goBtn.disabled = false;
  }
});

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

document.getElementById("ip").addEventListener("keydown", e => {
  if (e.key === "Enter") goBtn.click();
});
</script>
</body>
</html>
"""


def ask(prompt, default=None):
    suffix = " [{}]".format(default) if default else ""
    try:
        value = raw_input(prompt + suffix + ": ")  # noqa: F821  (py2)
    except NameError:
        value = input(prompt + suffix + ": ")
    value = value.strip()
    return value if value else default


def ensure_dirs(ftp, path):
    """Create each segment of an absolute FTP path if missing."""
    parts = [p for p in path.strip("/").split("/") if p]
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def ftp_read_text(ftp, path):
    """Return remote file contents as str, or None if missing."""
    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR " + path, buf.write)
    except error_perm as exc:
        if str(exc).startswith("550"):
            return None
        raise
    data = buf.getvalue()
    return data.decode("utf-8", errors="replace")


def ftp_write_text(ftp, path, text):
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    if parent and parent != "/":
        ensure_dirs(ftp, parent)
    else:
        ftp.cwd("/")
    payload = text.encode("utf-8")
    ftp.storbinary("STOR " + name, io.BytesIO(payload))


def path_already_listed(line, plugin_path):
    """True if this ini line already enables plugin_path."""
    raw = line.strip()
    if not raw or raw.startswith(";") or raw.startswith("#") or raw.startswith("["):
        return False
    key = raw.split("=", 1)[0].strip()
    return key == plugin_path


def merge_plugins_ini(existing, plugin_path):
    """Ensure plugin_path is listed under [default]. Preserve other content."""
    if existing is None or not existing.strip():
        return DEFAULT_INI

    lines = existing.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    for line in lines:
        if path_already_listed(line, plugin_path):
            return existing if existing.endswith("\n") else existing + "\n"

    default_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[default]":
            default_idx = i
            break

    if default_idx is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("[default]")
        lines.append(plugin_path)
    else:
        lines.insert(default_idx + 1, plugin_path)

    return "\n".join(lines) + "\n"


def update_plugins_ini(ftp, ini_path, plugin_path, log=print):
    existing = ftp_read_text(ftp, ini_path)
    merged = merge_plugins_ini(existing, plugin_path)
    if existing is not None and merged == (existing if existing.endswith("\n") else existing + "\n"):
        log("ini    : already lists {} ({})".format(plugin_path, ini_path))
        return
    ftp_write_text(ftp, ini_path, merged)
    action = "created" if existing is None else "updated"
    log("ini    : {} {} (added under [default])".format(action, ini_path))


def upload(ip, console, port=FTP_PORT, log=print):
    info = get_targets()[console]
    local = info["local"]
    if not os.path.isfile(local):
        raise FileNotFoundError(
            "missing built plugin:\n  {}\nBuild it first.".format(local)
        )

    remote_dir = info["remote_dir"]
    remote_name = info["remote_name"]
    size = os.path.getsize(local)

    log("target : {}".format(info["label"]))
    log("host   : {}:{}".format(ip, port))
    log("upload : {} ({} bytes)".format(local, size))
    log("      -> {}/{}".format(remote_dir, remote_name))

    ftp = FTP()
    ftp.connect(ip, port, timeout=TIMEOUT)
    ftp.login(FTP_USER, FTP_PASS)
    try:
        ensure_dirs(ftp, remote_dir)
        with open(local, "rb") as f:
            ftp.storbinary("STOR " + remote_name, f)
        listing = []
        ftp.retrlines("LIST " + remote_name, listing.append)
        log("ok     : uploaded")
        for line in listing:
            log("        {}".format(line))

        if info.get("ini_path") and info.get("plugin_path"):
            update_plugins_ini(ftp, info["ini_path"], info["plugin_path"], log=log)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    log("")
    log("next   : {}".format(info["hint"]))


def _targets_for_page():
    out = {}
    for key, info in get_targets().items():
        out[key] = {
            "label": info["label"],
            "remote_dir": info["remote_dir"],
            "remote_name": info["remote_name"],
            "exists": os.path.isfile(info["local"]),
            "hint": info["hint"],
        }
    return out


def _pick_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_gui(open_browser=True):
    """Serve a local web UI and open it in the default browser."""
    port = _pick_free_port()
    url = "http://127.0.0.1:{}/".format(port)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, content_type="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
            page = HTML_PAGE.replace(
                "__TARGETS_JSON__",
                json.dumps(_targets_for_page()),
            )
            self._send(200, page)

        def do_POST(self):
            if self.path != "/deploy":
                self._send(404, '{"ok":false}', "application/json")
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                # also accept form posts
                payload = {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}

            ip = str(payload.get("ip", "")).strip()
            console = str(payload.get("console", "ps5")).strip().lower()
            try:
                ftp_port = int(payload.get("port", FTP_PORT))
            except (TypeError, ValueError):
                ftp_port = FTP_PORT

            lines = []
            ok = False
            err = None
            if not ip:
                err = "IP is required"
            elif console not in get_targets():
                err = "console must be ps4 or ps5"
            else:
                def log(msg=""):
                    lines.append(msg)

                try:
                    upload(ip, console, port=ftp_port, log=log)
                    lines.append("Deploy finished.")
                    ok = True
                except Exception as exc:
                    err = str(exc)
                    lines.append("FTP failed: {}".format(exc))
                    lines.append(
                        "  - is GoldHEN / etaHEN FTP running on port {}?".format(
                            ftp_port
                        )
                    )

            body = json.dumps({"ok": ok, "log": lines, "error": err})
            self._send(200 if ok else 400, body, "application/json; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("GUI at {}".format(url))
    print("Press Ctrl+C to quit.")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(description="Deploy lockall plugin over FTP")
    p.add_argument("--ip", help="console IP address")
    p.add_argument("--console", choices=("ps4", "ps5"),
                   help="target console")
    p.add_argument("--port", type=int, default=FTP_PORT,
                   help="FTP port (default: %(default)s)")
    p.add_argument("--cli", action="store_true",
                   help="use terminal prompts instead of the GUI")
    p.add_argument("--gui", action="store_true",
                   help="open the GUI (default when no --ip/--cli)")
    p.add_argument("--no-browser", action="store_true",
                   help="start the GUI server without opening a browser")
    return p.parse_args(argv)


def main_cli(args):
    ip = args.ip or ask("Console IP", "192.168.1.13")
    if not ip:
        raise SystemExit("IP is required")

    console = args.console
    if not console:
        choice = ask("Console type [1=PS4, 2=PS5]", "2")
        if choice in ("1", "ps4", "PS4", "4"):
            console = "ps4"
        elif choice in ("2", "ps5", "PS5", "5"):
            console = "ps5"
        else:
            raise SystemExit("choose 1 (PS4) or 2 (PS5)")

    try:
        upload(ip, console, port=args.port)
    except Exception as exc:
        print("FTP failed: {}".format(exc), file=sys.stderr)
        print("  - is GoldHEN / etaHEN FTP running on port {}?".format(args.port),
              file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    args = parse_args(argv)

    use_gui = args.gui or (not args.cli and args.ip is None and args.console is None)
    if use_gui:
        return run_gui(open_browser=not args.no_browser)
    return main_cli(args)


if __name__ == "__main__":
    sys.exit(main())
