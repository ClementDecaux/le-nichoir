"""Géocodage (Géoplateforme IGN / BAN) et calculs de distance."""
from __future__ import annotations

import math
import unicodedata

from . import http

GEOCODER = "https://data.geopf.fr/geocodage"

# Lieux connus, pour éviter les ambiguïtés du géocodeur.
KNOWN_PLACES = {
    "grande halle": ("La Grande Halle, rue Pierre Bourdeix, Lyon 7e", 45.738152, 4.84107),
}


def norm(text: str) -> str:
    """Minuscules sans accents, pour comparer des noms."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(km: float) -> float:
    """Temps de marche : détour de 30 % par rapport au vol d'oiseau, 4,5 km/h."""
    return km * 1.3 / 4.5 * 60


def geocode(query: str, *, near: tuple[float, float] = (45.75, 4.83),
            municipality: bool = False) -> dict | None:
    """Renvoie {label, lat, lon, citycode, city} ou None. Recherche biaisée vers Lyon."""
    key = norm(query)
    for alias, (label, lat, lon) in KNOWN_PLACES.items():
        if alias in key:
            return {"label": label, "lat": lat, "lon": lon, "citycode": "69387", "city": "Lyon"}
    params = {"q": query, "limit": 1, "lat": near[0], "lon": near[1]}
    if municipality:
        params["type"] = "municipality"
    try:
        data = http.fetch_json(f"{GEOCODER}/search", params=params, ttl=-1)
    except RuntimeError:
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    p = feats[0]["properties"]
    return {"label": p.get("label"), "lat": lat, "lon": lon, "score": p.get("score", 0),
            "citycode": p.get("citycode"), "city": p.get("city"), "postcode": p.get("postcode")}


def geocode_poi(query: str, near: tuple[float, float] = (45.75, 4.83)) -> dict | None:
    """Recherche d'un point d'intérêt nommé (parc, gare, lac...)."""
    try:
        data = http.fetch_json(f"{GEOCODER}/search", ttl=-1,
                               params={"q": query, "index": "poi", "limit": 1,
                                       "lat": near[0], "lon": near[1]})
    except RuntimeError:
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    p = feats[0]["properties"]
    city = p.get("city")
    return {"label": p.get("toponym") or query, "lat": lat, "lon": lon,
            "citycode": (p.get("citycode") or [None])[0], "city": city[0] if isinstance(city, list) else city}


def resolve_place(query: str) -> dict | None:
    """Adresse, commune ou lieu-dit : essaie l'index adresse puis l'index POI."""
    hit = geocode(query)
    if hit and hit.get("score", 1) >= 0.6:
        return hit
    return geocode_poi(query) or hit
