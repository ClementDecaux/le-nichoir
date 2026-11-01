"""Extraction des champs d'une annonce à partir de texte libre (alertes e-mail, flux RSS)."""
from __future__ import annotations

import html
import re

from .geo import norm

SOURCES = {
    "leboncoin.fr": "LeBonCoin", "seloger.com": "SeLoger", "pap.fr": "PAP",
    "bienici.com": "Bien'ici", "logic-immo.com": "Logic-Immo",
    "espaces-atypiques.com": "Espaces Atypiques", "figaroimmo": "Figaro Immobilier",
    "superimmo": "Superimmo", "ouestfrance-immo": "Ouest-France Immo",
}

# Mots-clés -> caractéristiques. Chaque entrée : (caractéristique, motif, valeur).
FEATURE_PATTERNS = [
    ("sans_vis_a_vis", r"sans (aucun )?vis[- ]?a[- ]?vis|pas de vis[- ]?a[- ]?vis|aucun vis[- ]?a[- ]?vis", True),
    ("sans_vis_a_vis", r"vue (degagee|imprenable|panoramique)|au calme absolu|sans voisin|isolee?\b", True),
    ("sans_vis_a_vis", r"\bvis[- ]?a[- ]?vis\b(?! *(nul|inexistant))", False),  # vis-à-vis mentionné
    ("fibre", r"\bfibre\b|\bftth\b|tres haut debit", True),
    ("jardin", r"\bjardin\b|\bterrain\b|\bparc arbore\b", True),
    ("nature", r"\bnature\b|\bcampagne\b|\bbois\b|\bforet\b|\bchamps?\b|\bverdure\b|\bcalme\b", True),
    ("garage", r"\bgarage\b|\bbox\b|\bstationnement\b|\bparking\b", True),
    ("atypique", r"atypique|loft|grange|corps de ferme|ancienne? (usine|ecole|chapelle)|architecte", True),
    ("piscine", r"\bpiscine\b", True),
    ("travaux", r"travaux|a renover|a rafraichir|renovation complete", True),
]

TYPE_PATTERNS = [
    ("maison", r"\bmaison\b|\bvilla\b|\bpavillon\b|\bferme\b|\bchalet\b|\bmas\b|\bgrange\b|longere"),
    ("appartement", r"\bappartement\b|\bappart\b|\bstudio\b|\bduplex\b|\btriplex\b|\bt[1-6]\b|\bf[1-6]\b"),
    ("loft", r"\bloft\b|atelier|plateau"),
    ("terrain", r"\bterrain (a batir|constructible)\b"),
]


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def _num(s: str) -> float:
    return float(re.sub(r"[\s  .]", "", s).replace(",", "."))


def parse_price(text: str) -> int | None:
    m = re.search(r"(\d{1,3}(?:[\s  .]\d{3})+|\d{4,7})\s*(?:€|euros?|eur\b)", text, re.I)
    if not m:
        return None
    value = _num(m.group(1))
    return int(value) if 10_000 <= value <= 20_000_000 else None


def parse_surface(text: str) -> float | None:
    """Surface habitable en m² (la plus plausible, hors terrain)."""
    best = None
    for m in re.finditer(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*m(?:²|2|\b)", text):
        before = re.split(r"[\n.;]", norm(text[max(0, m.start() - 25):m.start()]))[-1]
        if "terrain" in before or "parcelle" in before or "jardin" in before:
            continue
        v = _num(m.group(1))
        if 9 <= v <= 1000:
            best = v
            break
    return best


def parse_land(text: str) -> float | None:
    """Surface du terrain en m² (accepte « terrain de 800 m² » ou « 1,2 ha »)."""
    t = norm(text)
    m = re.search(r"(?:terrain|parcelle|jardin)[^.,;\n\d]{0,25}?(\d[\d\s.,]*)\s*(m2|m²|ha|hectares?)", t)
    if not m:
        m2 = re.search(r"(\d[\d\s.,]*)\s*(m2|m²)\s*de (?:terrain|jardin|parcelle)", t)
        if not m2:
            return None
        m = m2
    v = _num(m.group(1))
    return v * 10_000 if m.group(2).startswith("ha") else v


def parse_rooms(text: str) -> int | None:
    t = norm(text)
    m = re.search(r"(\d{1,2})\s*(?:pieces|pces|p\.)\b", t) or re.search(r"\b[tf]([1-9])\b", t)
    return int(m.group(1)) if m else None


def parse_bedrooms(text: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*chambres?", norm(text))
    return int(m.group(1)) if m else None


def parse_postcode_city(text: str) -> tuple[str | None, str | None]:
    """Code postal et commune, du type « Brindas (69126) » ou « 69126 Brindas »."""
    m = re.search(r"([A-ZÉÈa-zéèêëàâîïôûç' -]{2,40}?)\s*\((\d{5})\)", text)
    if m:
        return m.group(2), m.group(1).strip(" -")
    m = re.search(r"\b(69\d{3}|01\d{3}|38\d{3}|42\d{3})[ \t]+([A-ZÉÈ][\w'éèêëàâîïôûç -]{1,40})", text)
    if m:
        return m.group(1), m.group(2).strip(" -").split("\n")[0]
    m = re.search(r"([A-ZÉÈ][\w'éèêëàâîïôûç]+(?:[ -](?:[A-ZÉÈ]|sur|en|de|du|la|le|les|d'|l')[\w'éèêëàâîïôûç]*){0,4})"
                  r"[ \t,]+(69\d{3}|01\d{3}|38\d{3}|42\d{3})\b", text)
    if m:
        return m.group(2), m.group(1).strip(" -")
    return None, None


def detect_type(text: str) -> str | None:
    t = norm(text)
    for kind, pattern in TYPE_PATTERNS:
        if re.search(pattern, t):
            return kind
    return None


def detect_features(text: str) -> dict[str, bool]:
    """{caractéristique: True (mentionnée) ou False (contredite, ex. « vis-à-vis sur l'arrière »)}."""
    t = norm(text)
    found: dict[str, bool] = {}
    for feat, pattern, value in FEATURE_PATTERNS:
        if found.get(feat) is True:
            continue  # une mention positive l'emporte (« sans vis-à-vis » contient « vis-à-vis »)
        if re.search(pattern, t):
            found[feat] = value
    return found


def source_from_url(url: str) -> str:
    for domain, name in SOURCES.items():
        if domain in (url or ""):
            return name
    return "Autre"


def normalize(raw: dict) -> dict:
    """Complète une annonce brute {url, title, text, ...} avec les champs extraits."""
    text = " \n".join(str(raw.get(k) or "") for k in ("title", "text"))
    postcode, city = parse_postcode_city(text)
    listing = {
        "url": raw.get("url"),
        "source": raw.get("source") or source_from_url(raw.get("url") or ""),
        "title": (raw.get("title") or "").strip()[:200],
        "description": (raw.get("text") or "").strip()[:4000],
        "price": raw.get("price") or parse_price(text),
        "surface": raw.get("surface") or parse_surface(text),
        "land": raw.get("land") or parse_land(text),
        "rooms": raw.get("rooms") or parse_rooms(text),
        "bedrooms": raw.get("bedrooms") or parse_bedrooms(text),
        "type": raw.get("type") or detect_type(text),
        "postcode": raw.get("postcode") or postcode,
        "city": raw.get("city") or city,
        "address": raw.get("address"),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "photo": raw.get("photo"),
        "features": {**detect_features(text), **(raw.get("features") or {})},
        "demo": bool(raw.get("demo")),
    }
    return listing
