"""Flux RSS / Atom d'annonces (quand un site ou une agence en propose un)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .. import http
from ..normalize import strip_html

ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):  # RSS 2.0
        enclosure = it.find("enclosure")
        items.append({"url": (it.findtext("link") or "").strip(), "title": it.findtext("title") or "",
                      "text": strip_html(it.findtext("description") or ""),
                      "photo": enclosure.get("url") if enclosure is not None else None})
    for entry in root.iter(f"{ATOM}entry"):  # Atom
        link = entry.find(f"{ATOM}link")
        items.append({"url": link.get("href") if link is not None else "",
                      "title": entry.findtext(f"{ATOM}title") or "",
                      "text": strip_html(entry.findtext(f"{ATOM}summary") or entry.findtext(f"{ATOM}content") or "")})
    return items


def fetch_feeds(conn_db, urls: list[str]) -> dict:
    from .. import db
    from ..normalize import parse_price
    stats = {"flux": 0, "annonces": 0, "ajoutées": 0, "fusionnées": 0, "ignorées": 0}
    for url in urls:
        try:
            items = parse_feed(http.fetch(url, ttl=3600))
        except (RuntimeError, ET.ParseError):
            continue
        stats["flux"] += 1
        for item in items:
            if not parse_price(f"{item['title']} {item['text']}"):  # article de blog, pas une annonce
                stats["ignorées"] += 1
                continue
            action, _ = db.upsert(conn_db, item)
            stats["annonces"] += 1
            stats[action] += 1
    return stats
