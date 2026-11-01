"""Couverture fibre (FTTH) par commune, d'après « Ma connexion internet » (Arcep)."""
from __future__ import annotations

import csv
import io
from functools import lru_cache

from . import http

ARCEP_COMMUNES = ("https://data.arcep.fr/fixe/maconnexioninternet/statistiques/last/commune/"
                  "commune_meilleure_techno_thd.csv")
CHECK_URL = "https://maconnexioninternet.arcep.fr/"


@lru_cache(maxsize=1)
def _table() -> dict[str, float]:
    raw = http.fetch(ARCEP_COMMUNES, ttl=86400 * 30, timeout=180).decode("utf-8-sig")
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(raw), delimiter=";"):
        if row.get("type") != "all":
            continue
        try:
            total, ftth = int(row["nbr"]), int(row["elig_ftth"])
        except (KeyError, ValueError):
            continue
        if total:
            out[row["code_insee"]] = ftth / total
    return out


def commune_ftth_rate(citycode: str | None) -> float | None:
    """Part des locaux éligibles à la fibre dans la commune (0 à 1), ou None si inconnue."""
    if not citycode:
        return None
    try:
        table = _table()
    except RuntimeError:
        return None
    # Lyon : les arrondissements (69381-69389) sont publiés sous le code de chaque arrondissement.
    return table.get(citycode)
