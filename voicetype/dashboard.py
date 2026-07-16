#!/usr/bin/env python3
"""
The dashboard — a tiny local web server for your history, stats and settings.

Standard library only: http.server + sqlite3. No FastAPI, no uvicorn. For one
user hitting loopback with a dozen endpoints, a framework would add ten
dependencies (including a compiled Rust wheel) to buy async concurrency and
request validation we do not need, on a project whose whole pitch is that it
doesn't drag the world in.

It starts lazily on the first "Open dashboard" click and shuts itself down after
15 idle minutes, so app startup pays nothing.

SECURITY — your dictation history is as personal as a diary, and a web page you
visit can talk to your loopback ports. Three controls, none redundant:

  bind 127.0.0.1 + random port  — nothing off-machine can reach it at all.
  Host header must be ours      — kills DNS rebinding, where a site flips its DNS
                                  to 127.0.0.1 and reads every response. The
                                  browser sends Host: attacker.com and cannot lie.
  token on every /api/* call    — kills CSRF, which the Host check does NOT stop:
                                  evil.com fetching our port sends a perfectly
                                  legitimate Host and passes that check. A custom
                                  header forces a preflight we never answer, and a
                                  no-cors request can't carry one.

The token travels in the URL fragment, which browsers never send to a server, so
it can't leak into logs or a Referer.
"""

import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
IDLE_TIMEOUT = 15 * 60

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

_server = None
_token = None
_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VoiceType"

    def log_message(self, *a):
        pass                       # don't print every request to the terminal

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self):
        host = self.headers.get("Host", "")
        return host in ("127.0.0.1:%d" % self.server.server_port,
                        "localhost:%d" % self.server.server_port)

    def _auth_ok(self):
        # Required on EVERY /api/* route, including GETs. A safelisted-header-only
        # request can never carry this, so no simple cross-site request can reach us.
        got = self.headers.get("X-VoiceType-Token", "")
        return bool(_token) and hmac.compare_digest(got, _token)

    def _guard(self):
        if not self._host_ok():
            self._send(403, b'{"error":"bad host"}')
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs and sfs not in ("same-origin", "none"):
            self._send(403, b'{"error":"cross-site"}')
            return False
        if not self._auth_ok():
            self._send(401, b'{"error":"bad token"}')
            return False
        self.server.touched = time.time()
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        if not path.startswith("/api/"):
            return self._static(path)
        if not self._guard():
            return

        if path == "/api/bootstrap":
            return self._json({
                "entries": store.load(limit=500),
                "total": store.count(),
                "stats": store.stats(),
                "settings": {
                    "lang": store.get_lang(),
                    "autostart": store.get_setting("autostart", False),
                },
                "vocab": store.get_vocab(),
                "corrections": store.get_corrections(),
                "snippets": store.get_snippets(),
                "modifier": getattr(self.server, "modifier", "Left ⌥"),
                "engine": getattr(self.server, "engine", "?"),
            })
        if path == "/api/entries":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            q = (qs.get("q") or [None])[0]
            limit = min(int((qs.get("limit") or [200])[0]), 1000)
            offset = int((qs.get("offset") or [0])[0])
            return self._json({"entries": store.load(limit, offset, q),
                               "total": store.count(q)})
        if path == "/api/stats":
            return self._json(store.stats())
        return self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/") or not self._guard():
            if not path.startswith("/api/"):
                self._send(404, b'{"error":"not found"}')
            return
        body = self._body()

        if path == "/api/entries/clear":
            if body.get("confirm") is not True:
                return self._send(400, b'{"error":"confirm required"}')
            store.clear()
            return self._json({"ok": True})
        if path == "/api/settings":
            if "lang" in body:
                lang = body["lang"]
                if lang not in ("en", "sv", "auto"):
                    return self._send(400, b'{"error":"bad lang"}')
                store.set_lang(lang)
                d = getattr(self.server, "dictation", None)
                if d:
                    d.prewarm_lang(lang)     # start loading it NOW, not on first use
            if "autostart" in body:
                from . import autostart
                want = bool(body["autostart"])
                autostart.set(want)
                store.set_setting("autostart", want)
            return self._json({"ok": True, "settings": {
                "lang": store.get_lang(),
                "autostart": store.get_setting("autostart", False)}})
        if path == "/api/vocab":
            store.set_vocab(body.get("terms", []))
            store.set_corrections(body.get("corrections", {}))
            return self._json({"ok": True, "terms": store.get_vocab(),
                               "corrections": store.get_corrections()})
        if path == "/api/snippets":
            store.set_snippets(body.get("items", []))
            return self._json({"ok": True, "items": store.get_snippets()})
        if path == "/api/shutdown":
            threading.Thread(target=stop, daemon=True).start()
            return self._json({"ok": True})
        return self._send(404, b'{"error":"not found"}')

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/") or not self._guard():
            return
        if path.startswith("/api/entries/"):
            try:
                store.delete(int(path.rsplit("/", 1)[1]))
            except ValueError:
                return self._send(400, b'{"error":"bad id"}')
            return self._json({"ok": True})
        return self._send(404, b'{"error":"not found"}')

    def _static(self, path):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        name = "index.html" if path == "/" else path.lstrip("/")
        if "/" in name or "\\" in name or ".." in name:
            return self._send(404, b"not found", "text/plain")
        full = os.path.join(WEB, name)
        if not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript",
                 "css": "text/css", "svg": "image/svg+xml"}.get(
                     name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


def _reaper(srv):
    while True:
        time.sleep(30)
        if getattr(srv, "closing", False):
            return
        if time.time() - srv.touched > IDLE_TIMEOUT:
            stop()
            return


def start(dictation=None, modifier="", engine=""):
    """Start (or reuse) the server and return the URL, token in the fragment."""
    global _server, _token
    with _lock:
        if _server is None:
            _token = secrets.token_urlsafe(32)     # per start; never persisted
            srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            srv.daemon_threads = True
            srv.touched = time.time()
            srv.closing = False
            srv.dictation = dictation
            srv.modifier = modifier
            srv.engine = engine
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            threading.Thread(target=_reaper, args=(srv,), daemon=True).start()
            _server = srv
        else:
            _server.touched = time.time()
            if dictation is not None:
                _server.dictation = dictation
        return "http://127.0.0.1:%d/#t=%s" % (_server.server_port, _token)


def stop():
    global _server, _token
    with _lock:
        if _server is None:
            return
        _server.closing = True
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server, _token = None, None


def open_in_browser(dictation=None, modifier="", engine=""):
    webbrowser.open(start(dictation, modifier, engine))
