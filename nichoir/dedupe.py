"""Dédoublonnage : une même maison est souvent publiée sur plusieurs sites."""
from __future__ import annotations

import re

from .geo import haversine_km, norm


def url_key(url: str | None) -> str | None:
    """URL sans paramètres de suivi ni fragment."""
    if not url:
        return None
    url = url.split("#", 1)[0]
    url = re.sub(r"[?&](utm_[^=&]+|xtor|at_[^=&]+|campaign|source|origin)=[^&]*", "", url)
    return url.rstrip("?&/").lower()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", norm(text))}


def similarity(a: dict, b: dict) -> float:
    """Score de 0 à 1 : même bien publié deux fois ?"""
    if a.get("url") and url_key(a.get("url")) == url_key(b.get("url")):
        return 1.0
    score, weight = 0.0, 0.0
    if a.get("price") and b.get("price"):
        weight += 3
        gap = abs(a["price"] - b["price"]) / max(a["price"], b["price"])
        score += 3 * (1.0 if gap <= 0.02 else 0.5 if gap <= 0.06 else 0.0)
    if a.get("surface") and b.get("surface"):
        weight += 3
        gap = abs(a["surface"] - b["surface"])
        score += 3 * (1.0 if gap <= 2 else 0.5 if gap <= 6 else 0.0)
    if a.get("postcode") and b.get("postcode"):
        weight += 2
        score += 2 * (a["postcode"] == b["postcode"])
    elif a.get("lat") and b.get("lat"):
        weight += 2
        score += 2 * (haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) < 2)
    if a.get("land") and b.get("land"):
        weight += 1
        score += 1 * (abs(a["land"] - b["land"]) / max(a["land"], b["land"]) <= 0.05)
    ta, tb = _tokens(a.get("description", "") + a.get("title", "")), _tokens(b.get("description", "") + b.get("title", ""))
    if len(ta) > 8 and len(tb) > 8:
        weight += 2
        score += 2 * len(ta & tb) / len(ta | tb) * 2  # le texte est souvent recopié d'un site à l'autre
    return min(1.0, score / weight) if weight >= 6 else 0.0


def find_duplicate(listing: dict, existing: list[dict], threshold: float = 0.85) -> dict | None:
    best, best_score = None, threshold
    for other in existing:
        s = similarity(listing, other)
        if s >= best_score:
            best, best_score = other, s
    return best
