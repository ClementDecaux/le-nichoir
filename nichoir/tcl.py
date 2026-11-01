"""Modèle du réseau TCL à partir des données ouvertes de la Métropole de Lyon.

Les horaires GTFS officiels demandent un compte sur data.grandlyon.com ; en attendant,
on construit un modèle sans horaires à partir des couches publiques (WFS) :
arrêts (avec les lignes qui les desservent), tracés des lignes et parcs relais.

- Les arrêts d'une ligne sont ordonnés en les projetant sur le tracé.
- Le temps de trajet entre deux arrêts = distance le long du tracé / vitesse commerciale du mode.
- L'attente au départ = demi-intervalle typique du mode en heure de pointe.
- Les correspondances se font à pied (≤ 300 m) avec une pénalité.

C'est une estimation : elle est calibrée sur des trajets connus (métro B notamment) et
documentée dans le README. Le résultat pourra être remplacé par un calcul GTFS exact.
"""
from __future__ import annotations

import heapq
import json
import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path

from . import config, http
from .geo import haversine_km, walk_minutes

WFS = "https://data.grandlyon.com/geoserver/sytral/ows"
LAYERS = {
    "arrets": "sytral:tcl_sytral.tclarret",
    "metro": "sytral:tcl_sytral.tcllignemf_2_0_0",
    "tram": "sytral:tcl_sytral.tcllignetram_2_0_0",
    "bus": "sytral:tcl_sytral.tcllignebus_2_0_0",
    "parcs_relais": "sytral:tcl_sytral.tclparcrelaisst",
}

# Vitesse commerciale (km/h, arrêts compris) et attente moyenne (min) en heure de pointe.
MODE_SPEED = {"MET": 34.0, "FUN": 12.0, "TRA": 19.0, "BUS_C": 17.0, "BUS": 18.0}
MODE_WAIT = {"MET": 2.0, "FUN": 4.0, "TRA": 3.5, "BUS_C": 4.0, "BUS": 7.5}
TRANSFER_PENALTY = 1.5  # min, en plus de la marche
MAX_TRANSFER_KM = 0.3
STOP_SNAP_KM = 0.08  # distance max entre un arrêt et le tracé de sa ligne
MODEL_VERSION = 2


WFS_PAGE = 500  # par pages, pour éviter les téléchargements de 20 Mo coupés en route


def _wfs(layer: str, ttl: float = 86400 * 7) -> list[dict]:
    features: list[dict] = []
    start = 0
    while True:
        data = http.fetch_json(WFS, ttl=ttl, timeout=180, params={
            "SERVICE": "WFS", "VERSION": "2.0.0", "request": "GetFeature",
            "typename": LAYERS[layer], "outputFormat": "application/json", "SRSNAME": "EPSG:4326",
            "count": WFS_PAGE, "startIndex": start, "sortBy": "gid",
        })
        page = data.get("features") or []
        features.extend(page)
        if len(page) < WFS_PAGE:
            return features
        start += WFS_PAGE


def parcs_relais() -> list[dict]:
    """Parcs relais TCL : [{id, nom, capacite, lat, lon}]."""
    out = []
    for f in _wfs("parcs_relais"):
        lon, lat = f["geometry"]["coordinates"][0]
        p = f["properties"]
        out.append({"id": p["id"], "nom": p["nom"], "capacite": p.get("capacite"),
                    "horaires": p.get("horaires"), "lat": lat, "lon": lon})
    return out


@dataclass
class Network:
    stops: dict[int, tuple[str, float, float]] = field(default_factory=dict)  # id -> (nom, lat, lon)
    # arêtes inverses (pour un Dijkstra depuis la destination) : node -> [(node_prec, minutes)]
    redges: dict[tuple, list[tuple[tuple, float]]] = field(default_factory=dict)
    lines: dict[str, str] = field(default_factory=dict)  # code_ligne -> mode
    grid: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    # --- index spatial simple (mailles d'environ 1 km) ---
    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(lat / 0.009), int(lon / 0.0128))

    def index(self) -> None:
        self.grid = {}
        for sid, (_, lat, lon) in self.stops.items():
            self.grid.setdefault(self._cell(lat, lon), []).append(sid)

    def stops_near(self, lat: float, lon: float, km: float) -> list[tuple[int, float]]:
        ci, cj = self._cell(lat, lon)
        r = int(math.ceil(km)) + 1
        out = []
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                for sid in self.grid.get((i, j), ()):
                    _, slat, slon = self.stops[sid]
                    d = haversine_km(lat, lon, slat, slon)
                    if d <= km:
                        out.append((sid, d))
        return sorted(out, key=lambda x: x[1])

    def add(self, a: tuple, b: tuple, minutes: float) -> None:
        """Arête a -> b (stockée à l'envers)."""
        self.redges.setdefault(b, []).append((a, minutes))

    # --- calcul ---
    def times_to(self, lat: float, lon: float, max_walk_km: float = 1.0,
                 max_minutes: float = 120) -> dict[int, tuple[float, str]]:
        """Temps (min) depuis chaque arrêt jusqu'au point donné, et résumé de l'itinéraire."""
        dist: dict[tuple, float] = {}
        via: dict[tuple, str] = {}
        heap: list = []
        for sid, d in self.stops_near(lat, lon, max_walk_km):
            node = ("P", sid)
            t = walk_minutes(d)
            if t < dist.get(node, 1e9):
                dist[node] = t
                via[node] = f"{round(t)} min à pied"
                heapq.heappush(heap, (t, node))
        while heap:
            t, node = heapq.heappop(heap)
            if t > dist.get(node, 1e9) or t > max_minutes:
                continue
            for prev, w in self.redges.get(node, ()):
                nt = t + w
                if nt < dist.get(prev, 1e9):
                    dist[prev] = nt
                    # résumé lisible : ligne empruntée au départ
                    if prev[0] == "P" and node[0] == "L":
                        via[prev] = f"{node[1]} → {via.get(node, '')}".rstrip(" →")
                    else:
                        via[prev] = via.get(node, "")
                    heapq.heappush(heap, (nt, prev))
        return {n[1]: (t, via.get(n, "")) for n, t in dist.items() if n[0] == "P"}


def _mode(family: str, code: str) -> str:
    if family == "MET":
        return "MET"
    if family == "FUN":
        return "FUN"
    if family == "TRA":
        return "TRA"
    return "BUS_C" if code.startswith("C") else "BUS"


def _project(lat: float, lon: float, line: list[tuple[float, float]], cum: list[float]):
    """Projette un point sur une polyligne (lon, lat) : (distance au tracé, abscisse) en km."""
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    best = (1e9, 0.0)
    for k in range(len(line) - 1):
        x1, y1 = (line[k][0] - lon) * kx, (line[k][1] - lat) * ky
        x2, y2 = (line[k + 1][0] - lon) * kx, (line[k + 1][1] - lat) * ky
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        u = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg2))
        px, py = x1 + u * dx, y1 + u * dy
        d = math.hypot(px, py)
        if d < best[0]:
            best = (d, cum[k] + u * math.sqrt(seg2))
    return best


def _polyline(geom: dict) -> list[tuple[float, float]]:
    coords = geom["coordinates"]
    if geom["type"] == "LineString":
        return [tuple(c) for c in coords]
    pts: list[tuple[float, float]] = []
    for part in coords:  # MultiLineString : on enchaîne les morceaux
        pts.extend(tuple(c) for c in part)
    return pts


def build_network() -> Network:
    net = Network()
    serving: dict[tuple[str, str], list[int]] = {}
    for f in _wfs("arrets"):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        sid = int(p["id"])
        net.stops[sid] = (p.get("nom") or "", lat, lon)
        for item in (p.get("desserte") or "").split(","):
            if ":" in item:
                code, sens = item.split(":", 1)
                serving.setdefault((code, sens), []).append(sid)
    net.index()

    for layer in ("metro", "tram", "bus"):
        for f in _wfs(layer):
            p = f["properties"]
            if p.get("nom_type_ligne") != "Régulière" or p.get("type_trace") not in ("NOM", "EXP"):
                continue
            code = p["code_ligne"]
            sens = {"Aller": "A", "Retour": "R"}.get(p.get("sens") or "")
            if not sens or not f.get("geometry"):
                continue
            mode = _mode(p["famille_transport"], code)
            net.lines[code] = mode
            line = _polyline(f["geometry"])
            if len(line) < 2:
                continue
            cum = [0.0]
            for a, b in zip(line, line[1:]):
                cum.append(cum[-1] + haversine_km(a[1], a[0], b[1], b[0]))
            placed = []
            for sid in serving.get((code, sens), []):
                _, lat, lon = net.stops[sid]
                d, s = _project(lat, lon, line, cum)
                if d <= STOP_SNAP_KM:
                    placed.append((s, sid))
            placed.sort()
            speed, wait = MODE_SPEED[mode], MODE_WAIT[mode]
            for (s1, a), (s2, b) in zip(placed, placed[1:]):
                if a == b:
                    continue
                _, la, loa = net.stops[a]
                _, lb, lob = net.stops[b]
                km = max(s2 - s1, haversine_km(la, loa, lb, lob))
                net.add(("L", code, a), ("L", code, b), km / speed * 60)
            for _, sid in placed:
                net.add(("P", sid), ("L", code, sid), wait)  # monter : attendre
                net.add(("L", code, sid), ("P", sid), 0.0)  # descendre

    # Correspondances à pied entre arrêts proches.
    for sid, (_, lat, lon) in net.stops.items():
        for other, d in net.stops_near(lat, lon, MAX_TRANSFER_KM):
            if other != sid:
                net.add(("P", sid), ("P", other), walk_minutes(d) + TRANSFER_PENALTY)
    return net


_NET: Network | None = None


def network() -> Network:
    """Réseau en mémoire, reconstruit au plus une fois par semaine."""
    global _NET
    if _NET is not None:
        return _NET
    path: Path = config.CACHE_DIR / f"tcl_network_v{MODEL_VERSION}.pickle"
    if path.exists():
        import time
        if time.time() - path.stat().st_mtime < 86400 * 7:
            with path.open("rb") as fh:
                _NET = Network(**pickle.load(fh))
                return _NET
    _NET = build_network()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(vars(_NET), fh)  # données brutes : indépendant du module chargé
    return _NET


if __name__ == "__main__":  # diagnostic rapide
    net = network()
    print(len(net.stops), "arrêts,", sum(len(v) for v in net.redges.values()), "arêtes")
    times = net.times_to(45.738152, 4.84107)
    for pr in parcs_relais():
        near = net.stops_near(pr["lat"], pr["lon"], 0.4)
        best = min((walk_minutes(d) + times.get(s, (1e9, ""))[0], times.get(s, (0, ""))[1], net.stops[s][0])
                   for s, d in near) if near else None
        print(json.dumps(pr["nom"], ensure_ascii=False), best)
