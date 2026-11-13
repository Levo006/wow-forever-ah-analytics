"""HTTP-Server (Standardbibliothek): statisches Frontend + JSON-API.

* ThreadingHTTPServer, eine SQLite-Verbindung pro Thread (WAL → parallele Leser)
* Antwort-Cache, der bei jedem Import automatisch ungültig wird (data_version)
* gzip-Kompression für JSON und statische Dateien
"""
from __future__ import annotations

import gzip
import json
import mimetypes
import os
import re
import threading
import time
import traceback
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from . import api, db

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MAX_UPLOAD = 200 * 1024 * 1024


class ResponseCache:
    def __init__(self, size=512):
        self.size = size
        self.data: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            v = self.data.get(key)
            if v is not None:
                self.data.move_to_end(key)
            return v

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


class Collector:
    """Hintergrund-Sammler für die Battle.net API (optional)."""

    def __init__(self, cfg, pool: db.ConnectionPool):
        self.cfg = cfg
        self.pool = pool
        self.running = False
        self.last_run = None
        self.last_result = None
        self.last_error = None
        self.next_run = None
        self.lock = threading.Lock()
        self._stop = threading.Event()

    def state(self):
        return {"running": self.running, "last_run": self.last_run, "next_run": self.next_run,
                "last_result": self.last_result, "last_error": self.last_error}

    def run_once(self):
        if not self.lock.acquire(blocking=False):
            return False
        self.running = True
        try:
            from .sources import battlenet
            bn = self.cfg["battlenet"]
            con = db.connect(self.cfg["server"]["db_path"])
            results = []
            for region in bn.get("regions") or ["eu"]:
                client = battlenet.BattleNetClient(bn["client_id"], bn["client_secret"],
                                                   region=region, locale=bn.get("locale", "de_DE"))
                r = battlenet.collect(con, client, ruleset=bn.get("ruleset") or None,
                                      only_houses=bn.get("houses") or None,
                                      ladder_keep=self.cfg["collector"]["ladder_keep"],
                                      fetch_metadata=self.cfg["collector"]["fetch_metadata"])
                results.append(r.summary())
            self.last_result = " | ".join(results)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            try:
                con = db.connect(self.cfg["server"]["db_path"])
                db.log_import(con, "battlenet", "error", self.last_error)
                con.commit()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.last_run = int(time.time())
            self.running = False
            self.lock.release()
        return True

    def loop(self):
        interval = max(5, int(self.cfg["collector"]["interval_minutes"])) * 60
        while not self._stop.is_set():
            self.run_once()
            self.next_run = int(time.time()) + interval
            self._stop.wait(interval)

    def start_background(self):
        threading.Thread(target=self.loop, name="collector", daemon=True).start()


def make_handler(cfg, pool: db.ConnectionPool, cache: ResponseCache, collector: Collector | None):

    routes = [
        ("GET", r"/api/status", lambda c, p, m: api.status(c, p, cfg, collector)),
        ("GET", r"/api/houses", lambda c, p, m: api.houses(c, p)),
        ("GET", r"/api/overview", lambda c, p, m: api.overview(c, p)),
        ("GET", r"/api/classes", lambda c, p, m: api.classes(c, p)),
        ("GET", r"/api/search", lambda c, p, m: api.search(c, p)),
        ("GET", r"/api/item/(\d+)", lambda c, p, m: api.item(c, int(m[1]), p)),
        ("GET", r"/api/item/(\d+)/history", lambda c, p, m: api.history(c, int(m[1]), p)),
        ("GET", r"/api/item/(\d+)/analysis", lambda c, p, m: api.analysis(c, int(m[1]), p)),
        ("GET", r"/api/item/(\d+)/snapshots", lambda c, p, m: api.item_snapshots(c, int(m[1]), p)),
        ("GET", r"/api/snapshot/(\d+)/(\d+)", lambda c, p, m: api.snapshot(c, int(m[1]), int(m[2]), p)),
        ("GET", r"/api/compare", lambda c, p, m: api.compare(c, p)),
        ("GET", r"/api/movers", lambda c, p, m: api.movers(c, p)),
        ("GET", r"/api/deals", lambda c, p, m: api.deals(c, p)),
        ("GET", r"/api/import/log", lambda c, p, m: api.import_log(c, p)),
        ("GET", r"/api/recipes", lambda c, p, m: api.recipes(c, p)),
        ("GET", r"/api/quests", lambda c, p, m: api.quests(c, p)),
        ("GET", r"/api/sources/(\d+)", lambda c, p, m: api.sources(c, m.group(1))),
    ]
    compiled = [(meth, re.compile(pat + r"/?$"), fn) for meth, pat, fn in routes]

    def start_metadata():
        """Item-Namen/Icons für neue Items im Hintergrund nachladen."""
        def run():
            from .metadata import fill_metadata
            fill_metadata(cfg, db.connect(cfg["server"]["db_path"]), log=lambda *a: None, limit=300)
        threading.Thread(target=run, name="metadata", daemon=True).start()
    uncached = {"/api/status", "/api/import/log"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "WoWAHAnalytics/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # leiser
            if os.environ.get("AH_DEBUG"):
                super().log_message(fmt, *args)

        # ---------------------------------------------------------- Ausgabe
        def _send(self, status, body: bytes, ctype, cache_ctl="no-cache", extra=None):
            gz = "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 1024
            if gz:
                body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_ctl)
            if gz:
                self.send_header("Content-Encoding", "gzip")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                              default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status, msg):
            self._json(status, {"error": msg})

        # ---------------------------------------------------------- Routen
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                return self._api("GET", url)
            return self._static(url.path)

        def do_POST(self):
            url = urlparse(self.path)
            params = dict(parse_qsl(url.query))
            con = pool.get()
            try:
                if url.path.rstrip("/") == "/api/import/auctionator":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length <= 0 or length > MAX_UPLOAD:
                        return self._error(400, "Leere oder zu große Datei")
                    data = self.rfile.read(length)
                    from .sources import auctionator
                    res = auctionator.import_bytes(con, data, region=params.get("region", "eu"),
                                                   label=params.get("filename", "Upload"))
                    start_metadata()
                    return self._json(200, res.as_dict())
                if url.path.rstrip("/") == "/api/import/auctionator/auto":
                    from .sources import auctionator
                    av = cfg["auctionator"]
                    paths = [av["path"]] if av.get("path") else auctionator.find_savedvariables(av["wow_root"])
                    paths = [p for p in paths if os.path.exists(p)]
                    if not paths:
                        return self._error(404, "Keine Auctionator.lua gefunden – Pfad in config.toml setzen.")
                    results = [auctionator.import_file(con, p, region=av.get("region", "eu")).as_dict()
                               for p in paths]
                    start_metadata()
                    return self._json(200, {"results": results})
                if url.path.rstrip("/") == "/api/collect":
                    if not collector:
                        return self._error(400, "Battle.net ist nicht konfiguriert (config.toml).")
                    started = not collector.running
                    if started:
                        threading.Thread(target=collector.run_once, daemon=True).start()
                    return self._json(202, {"started": started, "state": collector.state()})
                return self._error(404, "Unbekannter Endpunkt")
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                return self._error(500, f"{type(exc).__name__}: {exc}")

        def _api(self, method, url):
            params = dict(parse_qsl(url.query))
            for meth, rx, fn in compiled:
                m = rx.match(url.path)
                if meth == method and m:
                    break
            else:
                return self._error(404, "Unbekannter API-Endpunkt")
            con = pool.get()
            key = None
            if url.path not in uncached:
                key = (db.data_version(con), url.path, tuple(sorted(params.items())))
                hit = cache.get(key)
                if hit is not None:
                    return self._send(200, hit, "application/json; charset=utf-8",
                                      extra={"X-Cache": "hit"})
            try:
                result = fn(con, params, m)
            except api.BadRequest as exc:
                return self._error(400, str(exc))
            except api.NotFound as exc:
                return self._error(404, str(exc))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                return self._error(500, f"{type(exc).__name__}: {exc}")
            body = json.dumps(result, ensure_ascii=False, separators=(",", ":"),
                              default=str).encode("utf-8")
            if key is not None:
                cache.put(key, body)
            self._send(200, body, "application/json; charset=utf-8")

        def _static(self, path):
            if path in ("", "/"):
                path = "/index.html"
            full = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
            if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
                # SPA: unbekannte Pfade -> index.html
                full = os.path.join(STATIC_DIR, "index.html")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            with open(full, "rb") as fh:
                body = fh.read()
            cache_ctl = "public, max-age=86400" if "/vendor/" in full.replace("\\", "/") else "no-cache"
            self._send(200, body, ctype, cache_ctl)

    return Handler


def serve(cfg, with_collector=False):
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    path = cfg["server"]["db_path"]
    db.connect(path).close()  # Schema anlegen
    pool = db.ConnectionPool(path)
    collector = None
    bn = cfg["battlenet"]
    if bn.get("client_id") and bn.get("client_secret"):
        collector = Collector(cfg, pool)
        if with_collector:
            collector.start_background()
    handler = make_handler(cfg, pool, ResponseCache(), collector)
    host, port = cfg["server"]["host"], int(cfg["server"]["port"])
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    print(f"WoW: Forever AH-Analytics läuft auf http://{host}:{port}  (Strg+C beendet)")
    print(f"Datenbank: {path}")
    if with_collector and collector:
        print(f"Battle.net-Sammler aktiv (alle {cfg['collector']['interval_minutes']} Minuten)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
