"""Application des contraintes aux annonces stockées, avec explication pour chaque annonce."""
from __future__ import annotations

import math

from . import fibre, geo, travel

LYON_CENTER = (45.7578, 4.8320)  # place Bellecour
SECTOR_BEARING = {"nord": 0, "nord-est": 45, "est": 90, "sud-est": 135, "sud": 180,
                  "sud-ouest": 225, "ouest": 270, "nord-ouest": 315}
FEATURE_LABELS = {
    "sans_vis_a_vis": "sans vis-à-vis", "fibre": "fibre", "jardin": "jardin / terrain",
    "nature": "cadre nature", "garage": "garage / parking", "atypique": "bien atypique",
    "piscine": "piscine", "sans_travaux": "sans travaux",
}


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dl) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def in_sector(point: tuple[float, float], sector: str) -> bool:
    """Secteur large (± 50°) autour de la direction demandée, à moins de 50 km du centre."""
    if geo.haversine_km(*LYON_CENTER, *point) > 50:
        return False
    if geo.haversine_km(*LYON_CENTER, *point) < 2:
        return True
    diff = abs((bearing(LYON_CENTER, point) - SECTOR_BEARING[sector] + 180) % 360 - 180)
    return diff <= 50


def resolve_places(constraints: dict) -> dict[str, dict | None]:
    places = {}
    for t in constraints.get("trajets", []):
        places[t["lieu"]] = geo.resolve_place(t["lieu"])
    for p in constraints.get("proximites", []):
        if p["lieu"] != "parc relais TCL":
            places[p["lieu"]] = geo.resolve_place(p["lieu"])
    return places


def _check_min(listing, field, minimum, label, unit, out) -> bool:
    if not minimum:
        return True
    value = listing.get(field)
    if value is None:
        out["a_verifier"].append(f"{label} non précisé(e)")
        out["score"] -= 5
        return True
    if value < minimum:
        return False
    out["ok"].append(f"{label} {value:g} {unit}".strip())
    return True


def evaluate(listing: dict, c: dict, places: dict) -> dict | None:
    """Renvoie l'évaluation (score, critères remplis, points à vérifier) ou None si exclue."""
    out = {"score": 100.0, "ok": [], "a_verifier": [], "trajets": [], "proximites": []}
    price = listing.get("price")
    if c.get("budget_max") and price and price > c["budget_max"]:
        return None
    if c.get("budget_min") and price and price < c["budget_min"]:
        return None
    if c.get("budget_max"):
        if price:
            out["ok"].append(f"{price:,} €".replace(",", " "))
            out["score"] += 5 * (1 - price / c["budget_max"])
        else:
            out["a_verifier"].append("prix non précisé")
    if c.get("types") and listing.get("type") and listing["type"] not in c["types"]:
        return None
    for field, label, unit in (("surface", "surface", "m²"), ("pieces", "pièces", ""),
                               ("chambres", "chambres", ""), ("terrain", "terrain", "m²")):
        src = {"pieces": "rooms", "chambres": "bedrooms", "terrain": "land"}.get(field, field)
        if not _check_min(listing, src, c.get(f"{field}_min"), label, unit, out):
            return None

    point = (listing["lat"], listing["lon"]) if listing.get("lat") else None
    if point is None:
        out["a_verifier"].append("localisation inconnue")
        out["score"] -= 30
    if c.get("communes"):
        wanted = {geo.norm(x) for x in c["communes"]}
        if geo.norm(listing.get("city") or "") not in wanted:
            return None
    if c.get("secteur") and point and not in_sector(point, c["secteur"]):
        return None

    approx = listing.get("geo_precision") == "commune"
    for t in c.get("trajets", []):
        place = places.get(t["lieu"])
        if not place or not point:
            out["a_verifier"].append(f"trajet vers {t['lieu']} non calculé")
            continue
        trip = travel.best_trip(point, (place["lat"], place["lon"]), tuple(t.get("modes") or travel_modes()))
        best = trip["best"]
        if not best:
            out["a_verifier"].append(f"aucun itinéraire trouvé vers {t['lieu']}")
            continue
        if best["minutes"] > t["max_minutes"] + (5 if approx else 0):
            return None
        out["trajets"].append({"lieu": place["label"], "max": t["max_minutes"], **trip,
                               "approx": approx})
        out["score"] += min(10, (t["max_minutes"] - best["minutes"]) / 2)
    for p in c.get("proximites", []):
        if not point:
            continue
        if p["lieu"] == "parc relais TCL":
            relay = travel.nearest_relay(point)
            if not relay:
                continue
            if (p.get("max_km") and relay["km"] > p["max_km"]) or \
                    (p.get("max_minutes_voiture") and relay["minutes_voiture"] > p["max_minutes_voiture"]):
                return None
            out["proximites"].append(f"P+R {relay['nom']} à {relay['km']} km ({relay['minutes_voiture']} min en voiture)")
            continue
        place = places.get(p["lieu"])
        if not place:
            out["a_verifier"].append(f"lieu « {p['lieu']} » introuvable")
            continue
        km = geo.haversine_km(*point, place["lat"], place["lon"])
        if p.get("max_km") and km > p["max_km"]:
            return None
        text = f"{place['label']} à {km:.1f} km"
        if p.get("max_minutes_voiture"):
            minutes = travel.car_minutes(point, [(place["lat"], place["lon"])])[0]
            if minutes is not None and minutes > p["max_minutes_voiture"]:
                return None
            text += f" ({round(minutes or 0)} min en voiture)"
        out["proximites"].append(text)

    features = dict(listing.get("features") or {})
    for feat in c.get("exigences", []):
        label = FEATURE_LABELS.get(feat, feat)
        if features.get(feat) is True:
            out["ok"].append(label)
        elif feat == "fibre":
            rate = fibre.commune_ftth_rate(listing.get("citycode"))
            if rate is not None and rate >= 0.9:
                out["ok"].append(f"fibre probable ({rate:.0%} des locaux de la commune éligibles)")
                out["score"] -= 2
            elif rate is not None:
                out["a_verifier"].append(f"fibre : {rate:.0%} des locaux de la commune éligibles, à vérifier à l'adresse")
                out["score"] -= 12
            else:
                out["a_verifier"].append("fibre à vérifier")
                out["score"] -= 12
        elif feat == "sans_travaux" and features.get("travaux"):
            return None
        elif features.get(feat) is False:
            out["a_verifier"].append(f"{label} : l'annonce semble dire le contraire")
            out["score"] -= 25
        else:
            out["a_verifier"].append(f"{label} : non mentionné dans l'annonce")
            out["score"] -= 12
    for feat in c.get("preferences", []):
        if features.get(feat) is True:
            out["ok"].append(FEATURE_LABELS.get(feat, feat))
            out["score"] += 6
    if approx and c.get("trajets"):
        out["a_verifier"].append("adresse exacte inconnue : trajet calculé depuis le centre de la commune")
    out["score"] = round(out["score"], 1)
    return out


def travel_modes() -> list[str]:
    return ["voiture", "tcl", "voiture+tcl"]


def search(listings: list[dict], constraints: dict) -> dict:
    places = resolve_places(constraints)
    results = []
    excluded = 0
    for listing in listings:
        ev = evaluate(listing, constraints, places)
        if ev is None:
            excluded += 1
            continue
        results.append({**listing, "evaluation": ev})
    results.sort(key=lambda r: (-r["evaluation"]["score"], r.get("price") or 1e12))
    return {
        "contraintes": constraints,
        "lieux": {k: v for k, v in places.items()},
        "resultats": results,
        "exclues": excluded,
    }
