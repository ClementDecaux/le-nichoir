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
        # JSON obligatoire : un site tiers ne peut pas poster ici sans requête préalable CORS,
        # que ce serveur refuse (protection contre les envois venant d'autres pages).
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("Content-Type application/json attendu")
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
        try:
            body = self._body() if url.path != "/api/ingest" else {}
        except ValueError as exc:
            return self._json({"erreur": str(exc)}, 415)
        with _LOCK:
            conn = db.connect()
            if url.path == "/api/listings/bulk":  # envoyé par le bouton du navigateur
                return self._json(import_bulk(conn, body))
            if url.path == "/api/listings":  # ajout manuel (copier-coller d'une annonce)
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


BULK_FIELDS = ("url", "title", "text", "price", "surface", "land", "rooms", "bedrooms", "city",
               "postcode", "lat", "lon", "photo", "source")


def import_bulk(conn, body: dict) -> dict:
    """Enregistre les annonces envoyées par le bouton « Envoyer au Nichoir »."""
    from .normalize import parse_price
    stats = {"ajoutées": 0, "fusionnées": 0, "ignorées": 0, "titres": []}
    for raw in (body.get("listings") or [])[:300]:
        if not isinstance(raw, dict):
            continue
        item = {k: raw.get(k) for k in BULK_FIELDS if raw.get(k) not in (None, "")}
        for k in ("price", "surface", "land", "rooms", "lat", "lon"):
            try:
                item[k] = float(item[k]) if k in item else None
            except (TypeError, ValueError):
                item[k] = None
        if item.get("price"):
            item["price"] = int(item["price"])
        if not (item.get("price") or parse_price(f"{item.get('title', '')} {item.get('text', '')}")):
            stats["ignorées"] += 1
            continue
        action, _ = db.upsert(conn, item)
        stats[action + "s"] += 1
        stats["titres"].append(f"{action} : {(item.get('title') or '')[:90]}")
    return stats


def serve(port: int | None = None, host: str = "127.0.0.1") -> None:
    port = port or config.PORT
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Le Nichoir : http://{host}:{port}")
    httpd.serve_forever()
