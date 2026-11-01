"""Temps de trajet porte à porte : voiture, TCL, ou voiture jusqu'à un parc relais puis TCL."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from . import config, http, tcl
from .geo import haversine_km, walk_minutes

OSRM = "https://router.project-osrm.org"
PARK_DIRECT_MIN = 5.0  # se garer à destination
PARK_RELAY_MIN = 3.0  # se garer au parc relais et rejoindre le quai
MAX_WALK_TO_STOP_KM = 1.0


@dataclass
class Trip:
    minutes: float
    mode: str  # "voiture", "tcl", "voiture+tcl"
    detail: str

    def as_dict(self) -> dict:
        return {"minutes": round(self.minutes), "mode": self.mode, "detail": self.detail}


def car_minutes(origin: tuple[float, float], targets: list[tuple[float, float]]) -> list[float | None]:
    """Durées voiture (min, facteur trafic appliqué) depuis `origin` vers chaque cible (OSRM)."""
    if not targets:
        return []
    out: list[float | None] = []
    for start in range(0, len(targets), 90):  # limite d'URL du serveur public
        chunk = targets[start:start + 90]
        coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in [origin, *chunk])
        try:
            data = http.fetch_json(f"{OSRM}/table/v1/driving/{coords}", ttl=-1, min_interval=1.0,
                                   params={"sources": 0, "annotations": "duration"})
            row = data["durations"][0][1:]
        except (RuntimeError, KeyError, IndexError):
            # repli : vol d'oiseau × 1,4 à 50 km/h
            row = [haversine_km(*origin, *t) * 1.4 / 50 * 3600 for t in chunk]
        out.extend(None if d is None else d / 60 * config.CAR_FACTOR for d in row)
    return out


def _network():
    """Réseau TCL, ou None si les données n'ont pas pu être téléchargées (trajets voiture seuls)."""
    try:
        return tcl.network()
    except RuntimeError as exc:
        print("Données TCL indisponibles, trajets en voiture seulement :", exc)
        return None


@lru_cache(maxsize=32)
def _transit_index(dest: tuple[float, float]):
    """Pour une destination : temps TCL depuis chaque arrêt, et depuis chaque parc relais."""
    net = _network()
    if net is None:
        return {}, []
    times = net.times_to(*dest)
    relays = []
    for pr in tcl.parcs_relais():
        best = None
        for sid, d in net.stops_near(pr["lat"], pr["lon"], 0.4):
            if sid in times:
                t = walk_minutes(d) + times[sid][0]
                if best is None or t < best[0]:
                    best = (t, times[sid][1])
        if best:
            relays.append((pr, best[0], best[1]))
    return times, relays


def transit_only(origin: tuple[float, float], dest: tuple[float, float]) -> Trip | None:
    net = _network()
    if net is None:
        return None
    times, _ = _transit_index(dest)
    best = None
    for sid, d in net.stops_near(*origin, MAX_WALK_TO_STOP_KM):
        if sid in times:
            t = walk_minutes(d) + times[sid][0]
            if best is None or t < best.minutes:
                best = Trip(t, "tcl", f"{round(walk_minutes(d))} min à pied jusqu'à {net.stops[sid][0]}, "
                                       f"puis {times[sid][1]}")
    return best


def best_trip(origin: tuple[float, float], dest: tuple[float, float],
              modes: tuple[str, ...] = ("voiture", "tcl", "voiture+tcl")) -> dict:
    """Meilleur temps porte à porte, avec le détail de chaque mode demandé."""
    _, relays = _transit_index(dest) if ("voiture+tcl" in modes) else (None, [])
    targets = [dest] + [(pr["lat"], pr["lon"]) for pr, _, _ in relays]
    car = car_minutes(origin, targets) if ({"voiture", "voiture+tcl"} & set(modes)) else []
    options: list[Trip] = []
    if "voiture" in modes and car and car[0] is not None:
        options.append(Trip(car[0] + PARK_DIRECT_MIN, "voiture",
                            f"{round(car[0])} min de route + {PARK_DIRECT_MIN:.0f} min pour se garer"))
    if "voiture+tcl" in modes:
        for (pr, transit, via), drive in zip(relays, car[1:]):
            if drive is None:
                continue
            t = drive + PARK_RELAY_MIN + transit
            options.append(Trip(t, "voiture+tcl",
                                f"{round(drive)} min jusqu'au P+R {pr['nom']}, puis {round(transit)} min ({via})"))
    if "tcl" in modes:
        trip = transit_only(origin, dest)
        if trip:
            options.append(trip)
    by_mode: dict[str, Trip] = {}
    for o in options:
        if o.mode not in by_mode or o.minutes < by_mode[o.mode].minutes:
            by_mode[o.mode] = o
    best = min(by_mode.values(), key=lambda o: o.minutes) if by_mode else None
    return {"best": best.as_dict() if best else None,
            "modes": {m: o.as_dict() for m, o in by_mode.items()}}


def nearest_relay(origin: tuple[float, float]) -> dict | None:
    """Parc relais TCL le plus proche en voiture."""
    relays = tcl.parcs_relais()
    times = car_minutes(origin, [(p["lat"], p["lon"]) for p in relays])
    best = min(((t, p) for t, p in zip(times, relays) if t is not None), key=lambda x: x[0], default=None)
    if not best:
        return None
    t, p = best
    return {"nom": p["nom"], "minutes_voiture": round(t),
            "km": round(haversine_km(*origin, p["lat"], p["lon"]), 1)}
