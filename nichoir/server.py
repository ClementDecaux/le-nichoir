"""Serveur web local : page de recherche + API JSON (bibliothèque standard uniquement)."""
from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, query, search
from .ingest import email_alerts, rss

WEB_DIR = config.ROOT / "web"
_LOCK = threading.Lock()  # SQLite + caches : une requête lourde à la fois


class Handler(BaseHTTPRequestHandler):
    server_version = "LeNichoir/0.1"

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path == "/api/search":
            text = (qs.get("q") or [""])[0]
            with _LOCK:
                conn = db.connect()
                listings = db.all_listings(conn, include_demo=(qs.get("demo", ["1"])[0] == "1"))
                constraints = query.parse(text) if text.strip() else {"trajets": [], "proximites": []}
                return self._json(search.search(listings, constraints))
        if url.path == "/api/listings":
            with _LOCK:
                return self._json(db.all_listings(db.connect()))
        if url.path == "/api/relais":
            from . import tcl
            with _LOCK:
                return self._json(tcl.parcs_relais())
        path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        file = (WEB_DIR / path).resolve()
        if WEB_DIR.resolve() not in file.parents or not file.is_file():
            return self._json({"erreur": "introuvable"}, 404)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(str(file))[0] or "text/plain") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        with _LOCK:
            conn = db.connect()
            if url.path == "/api/listings":  # ajout manuel (copier-coller d'une annonce)
                body = self._body()
                if not (body.get("text") or body.get("title")):
                    return self._json({"erreur": "texte de l'annonce manquant"}, 400)
                action, lid = db.upsert(conn, body)
                return self._json({"action": action, "id": lid})
            if url.path == "/api/ingest":
                feeds = config.get("NICHOIR_RSS_FEEDS").split()
                return self._json({"email": email_alerts.fetch_imap(conn),
                                   "rss": rss.fetch_feeds(conn, feeds) if feeds else None})
        return self._json({"erreur": "introuvable"}, 404)

    def log_message(self, fmt, *args):  # journal plus discret
        print("  ", self.address_string(), fmt % args)


def serve(port: int | None = None, host: str = "127.0.0.1") -> None:
    port = port or config.PORT
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Le Nichoir : http://{host}:{port}")
    httpd.serve_forever()
