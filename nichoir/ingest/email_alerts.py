"""Lecture des alertes e-mail des sites d'annonces.

Principe : on crée sur chaque site (LeBonCoin, SeLoger, PAP, Bien'ici, Logic-Immo,
Espaces Atypiques...) une alerte large (secteur, budget), envoyée à une boîte dédiée.
Le Nichoir lit cette boîte en IMAP et découpe chaque e-mail en annonces. Aucun site
n'est aspiré : on ne lit que ce qu'ils nous envoient.
"""
from __future__ import annotations

import email
import imaplib
import re
import urllib.parse
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser

from .. import config
from ..normalize import parse_price, source_from_url, strip_html

# Liens qui pointent vers une annonce (et non vers l'accueil, le désabonnement...).
LISTING_LINK = re.compile(
    r"leboncoin\.fr/(ad/|vi/|ventes_immobilieres/)\S*\d{6,}"
    r"|seloger\.com/annonces/"
    r"|pap\.fr/annonces?/\S*r\d{6,}"
    r"|bienici\.com/annonce/"
    r"|logic-immo\.com/(detail-vente|annonces?/)"
    r"|espaces-atypiques\.com/\S*\d{4,}"
    r"|figaroimmo\S*/annonces/"
    , re.I)
NOISE_LINK = re.compile(r"unsubscribe|desinscri|desabonn|preferences|mentions|privacy|confidentialite|"
                        r"aide|help|mailto:|facebook|twitter|instagram|apple\.com|google\.com", re.I)


class _Segmenter(HTMLParser):
    """Transforme le HTML en suite de segments (texte, lien courant, image)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.segments: list[tuple[str, str | None, str | None]] = []
        self._href: list[str | None] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag == "a":
            self._href.append(a.get("href"))
        elif tag == "img" and a.get("src"):
            self.segments.append(("", self._href[-1] if self._href else None, a["src"]))
        elif tag in ("br", "p", "div", "td", "tr", "li"):
            self.segments.append(("\n", self._href[-1] if self._href else None, None))

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href:
            self._href.pop()

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.segments.append((data.strip(), self._href[-1] if self._href else None, None))


def _clean_link(href: str) -> str:
    """Retire le suivi publicitaire ; décode les redirections qui portent l'URL en paramètre."""
    parsed = urllib.parse.urlsplit(href)
    for key, values in urllib.parse.parse_qs(parsed.query).items():
        for v in values:
            if v.startswith("http") and LISTING_LINK.search(v):
                return v
    return href


def split_listings(html: str) -> list[dict]:
    """Découpe le HTML d'une alerte en annonces brutes {url, title, text, photo}."""
    parser = _Segmenter()
    parser.feed(html)
    segs = [(t, _clean_link(h) if h else None, img) for t, h, img in parser.segments]

    def is_listing(h: str | None) -> bool:
        return bool(h) and bool(LISTING_LINK.search(h)) and not NOISE_LINK.search(h)

    known = any(is_listing(h) for _, h, _ in segs)
    if not known:  # site inconnu ou liens de suivi : tout lien "non bruit" peut être une annonce
        def is_listing(h):  # noqa: F811
            return bool(h) and h.startswith("http") and not NOISE_LINK.search(h)

    def key(h: str) -> str:
        return h.split("?", 1)[0].rstrip("/")

    blocks: list[dict] = []
    current = None
    seen: set[str] = set()
    for text, href, img in segs:
        if is_listing(href) and key(href) not in seen:
            seen.add(key(href))
            current = {"url": href, "title": "", "parts": [], "photo": None}
            blocks.append(current)
        if current is None:
            continue
        if img and not current["photo"] and href and key(href) == key(current["url"]):
            current["photo"] = img
        if text.strip():
            if not current["title"] and href and key(href) == key(current["url"]) and len(text) > 8:
                current["title"] = text
            current["parts"].append(text)
    out = []
    for b in blocks:
        body = re.sub(r"\s*\n\s*", "\n", " ".join(b["parts"])).strip()
        if parse_price(body):
            out.append({"url": b["url"], "title": b["title"] or body[:80], "text": body,
                        "photo": b["photo"], "source": source_from_url(b["url"])})
    return out


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


def message_html(msg: Message) -> str:
    """Corps HTML d'un e-mail (ou texte brut converti)."""
    html_part, text_part = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="replace")
        if ctype == "text/html" and html_part is None:
            html_part = body
        elif ctype == "text/plain" and text_part is None:
            text_part = body
    if html_part:
        return html_part
    if text_part:  # liens nus en texte brut -> pseudo-HTML
        return re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', text_part).replace("\n", "<br>")
    return ""


def parse_message(raw: bytes) -> tuple[str, str, list[dict]]:
    msg = email.message_from_bytes(raw)
    msg_id = msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}"
    listings = split_listings(message_html(msg))
    return msg_id, _decode(msg.get("Subject")), listings


def fetch_imap(conn_db, days: int = 30, limit: int = 300) -> dict:
    """Lit les alertes récentes dans la boîte configurée et enregistre les annonces."""
    from .. import db

    host, user = config.get("NICHOIR_IMAP_HOST"), config.get("NICHOIR_IMAP_USER")
    password, folder = config.get("NICHOIR_IMAP_PASSWORD"), config.get("NICHOIR_IMAP_FOLDER", "INBOX")
    if not (host and user and password):
        return {"erreur": "IMAP non configuré (voir .env.example)"}
    import datetime
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
    stats = {"messages": 0, "annonces": 0, "ajoutées": 0, "fusionnées": 0}
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select(folder, readonly=True)
        _, data = imap.search(None, "SINCE", since)
        ids = data[0].split()[-limit:]
        for num in ids:
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            raw = msg_data[0][1]
            msg_id, subject, listings = parse_message(raw)
            if db.message_seen(conn_db, msg_id):
                continue
            stats["messages"] += 1
            for item in listings:
                action, _ = db.upsert(conn_db, item)
                stats["annonces"] += 1
                stats[action + "s"] += 1
            db.mark_message(conn_db, msg_id)
    return stats
