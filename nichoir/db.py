"""Stockage SQLite des annonces (fusionnées et dédoublonnées)."""
from __future__ import annotations

import json
import sqlite3
import time

from . import config, dedupe, geo, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    url TEXT, url_key TEXT UNIQUE,
    links TEXT NOT NULL DEFAULT '[]',       -- [{source, url}] : toutes les publications du bien
    source TEXT, title TEXT, description TEXT,
    price INTEGER, surface REAL, land REAL, rooms INTEGER, bedrooms INTEGER, type TEXT,
    postcode TEXT, city TEXT, citycode TEXT, address TEXT,
    lat REAL, lon REAL, geo_precision TEXT,  -- 'exacte', 'adresse' ou 'commune'
    photo TEXT, features TEXT NOT NULL DEFAULT '{}',
    price_history TEXT NOT NULL DEFAULT '[]',
    demo INTEGER NOT NULL DEFAULT 0,
    first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS seen_messages (id TEXT PRIMARY KEY, seen_at REAL);
"""

JSON_FIELDS = ("links", "features", "price_history")


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in JSON_FIELDS:
        d[f] = json.loads(d.get(f) or ("{}" if f == "features" else "[]"))
    return d


def all_listings(conn: sqlite3.Connection, include_demo: bool = True) -> list[dict]:
    sql = "SELECT * FROM listings" + ("" if include_demo else " WHERE demo = 0")
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY last_seen DESC")]


def locate(listing: dict) -> dict:
    """Ajoute lat/lon/citycode : adresse si connue, sinon centre de la commune."""
    if listing.get("lat") and listing.get("lon"):
        listing.setdefault("geo_precision", "exacte")
        if not listing.get("citycode"):
            hit = geo.geocode(f"{listing.get('city') or ''} {listing.get('postcode') or ''}".strip(),
                              municipality=True) if listing.get("city") else None
            listing["citycode"] = hit and hit.get("citycode")
        return listing
    if listing.get("address"):
        hit = geo.geocode(f"{listing['address']} {listing.get('postcode') or ''} {listing.get('city') or ''}")
        if hit and hit.get("score", 0) >= 0.5:
            listing.update(lat=hit["lat"], lon=hit["lon"], citycode=hit.get("citycode"), geo_precision="adresse")
            return listing
    if listing.get("city") or listing.get("postcode"):
        hit = geo.geocode(f"{listing.get('city') or ''} {listing.get('postcode') or ''}".strip(), municipality=True)
        if hit:
            listing.update(lat=hit["lat"], lon=hit["lon"], citycode=hit.get("citycode"),
                           city=listing.get("city") or hit.get("city"), geo_precision="commune")
    return listing


def upsert(conn: sqlite3.Connection, raw: dict) -> tuple[str, int]:
    """Normalise, localise, dédoublonne et enregistre une annonce. Renvoie (action, id)."""
    item = locate(normalize.normalize(raw))
    now = time.time()
    key = dedupe.url_key(item.get("url"))
    existing = all_listings(conn)
    match = next((e for e in existing if key and e.get("url_key") == key), None)
    match = match or dedupe.find_duplicate(item, existing)
    link = {"source": item["source"], "url": item.get("url")}
    if match:
        links = match["links"] + ([link] if link not in match["links"] else [])
        history = match["price_history"]
        if item.get("price") and item["price"] != match.get("price"):
            history.append({"price": item["price"], "at": now})
        merged = {k: (match.get(k) if match.get(k) not in (None, "") else item.get(k))
                  for k in ("title", "description", "surface", "land", "rooms", "bedrooms", "type",
                            "postcode", "city", "citycode", "address", "photo")}
        # une mention positive sur l'un des sites l'emporte
        features = {k: bool(item["features"].get(k)) or bool(match["features"].get(k))
                    if (item["features"].get(k) or match["features"].get(k)) else
                    item["features"].get(k, match["features"].get(k))
                    for k in {*item["features"], *match["features"]}}
        # préférer la localisation la plus précise
        rank = {"exacte": 3, "adresse": 2, "commune": 1, None: 0}
        loc = item if rank.get(item.get("geo_precision"), 0) > rank.get(match.get("geo_precision"), 0) else match
        conn.execute(
            """UPDATE listings SET links=?, price=?, price_history=?, features=?, last_seen=?,
               lat=?, lon=?, geo_precision=?, title=?, description=?, surface=?, land=?, rooms=?,
               bedrooms=?, type=?, postcode=?, city=?, citycode=?, address=?, photo=? WHERE id=?""",
            (json.dumps(links, ensure_ascii=False), item.get("price") or match.get("price"),
             json.dumps(history), json.dumps(features), now, loc.get("lat"), loc.get("lon"),
             loc.get("geo_precision"), *merged.values(), match["id"]))
        conn.commit()
        return "fusionnée", match["id"]
    cur = conn.execute(
        """INSERT INTO listings (url, url_key, links, source, title, description, price, surface, land,
           rooms, bedrooms, type, postcode, city, citycode, address, lat, lon, geo_precision, photo,
           features, price_history, demo, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item.get("url"), key, json.dumps([link], ensure_ascii=False), item["source"], item["title"],
         item["description"], item.get("price"), item.get("surface"), item.get("land"), item.get("rooms"),
         item.get("bedrooms"), item.get("type"), item.get("postcode"), item.get("city"), item.get("citycode"),
         item.get("address"), item.get("lat"), item.get("lon"), item.get("geo_precision"), item.get("photo"),
         json.dumps(item["features"]),
         json.dumps([{"price": item["price"], "at": now}] if item.get("price") else []),
         int(item.get("demo", False)), now, now))
    conn.commit()
    return "ajoutée", cur.lastrowid


def message_seen(conn: sqlite3.Connection, msg_id: str) -> bool:
    return conn.execute("SELECT 1 FROM seen_messages WHERE id=?", (msg_id,)).fetchone() is not None


def mark_message(conn: sqlite3.Connection, msg_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO seen_messages VALUES (?, ?)", (msg_id, time.time()))
    conn.commit()
