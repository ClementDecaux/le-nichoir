"""Traduction d'une recherche en langage naturel en contraintes précises.

Deux moteurs :
- Claude (si ANTHROPIC_API_KEY est défini et le paquet `anthropic` installé), qui comprend
  les formulations libres ;
- un analyseur à règles, en français, toujours disponible et utilisé en repli.

Le résultat est un dictionnaire au format CONSTRAINTS_SCHEMA, affiché tel quel sur la page
pour que l'on puisse vérifier ce qui a été compris.
"""
from __future__ import annotations

import json
import re

from . import config
from .geo import norm

FEATURES = ["sans_vis_a_vis", "fibre", "jardin", "nature", "garage", "atypique", "piscine", "sans_travaux"]
MODES = ["voiture", "tcl", "voiture+tcl"]
SECTEURS = ["nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest"]

_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}

CONSTRAINTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["budget_max", "budget_min", "types", "surface_min", "pieces_min", "chambres_min",
                 "terrain_min", "trajets", "proximites", "exigences", "preferences", "secteur", "communes"],
    "properties": {
        "budget_max": _num, "budget_min": _num,
        "types": {"type": "array", "items": {"type": "string", "enum": ["maison", "appartement", "loft"]}},
        "surface_min": _num, "pieces_min": _num, "chambres_min": _num, "terrain_min": _num,
        "trajets": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["lieu", "max_minutes", "modes"],
            "properties": {
                "lieu": {"type": "string"},
                "max_minutes": {"type": "number"},
                "modes": {"type": "array", "items": {"type": "string", "enum": MODES}},
            }}},
        "proximites": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["lieu", "max_km", "max_minutes_voiture"],
            "properties": {"lieu": {"type": "string"}, "max_km": _num, "max_minutes_voiture": _num}}},
        "exigences": {"type": "array", "items": {"type": "string", "enum": FEATURES}},
        "preferences": {"type": "array", "items": {"type": "string", "enum": FEATURES}},
        "secteur": {"anyOf": [{"type": "string", "enum": SECTEURS}, {"type": "null"}]},
        "communes": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = f"""Tu traduis une recherche immobilière en français (région lyonnaise) en contraintes JSON.

Règles :
- budget_max / budget_min en euros (« 350k » = 350000).
- trajets : temps porte à porte maximum vers un lieu. `lieu` doit être géocodable (adresse, nom de lieu
  connu, commune). Modes : "voiture", "tcl" (transports en commun lyonnais seuls), "voiture+tcl"
  (voiture jusqu'à un parc relais puis TCL). « combo transport voiture », « en combinant » ou une
  absence de précision = les trois modes ; « en voiture » = ["voiture"] ; « en transport » = ["tcl"].
- proximites : distance à un lieu ou à une catégorie. Pour les parcs relais TCL, utilise exactement
  le lieu "parc relais TCL". max_km à vol d'oiseau, max_minutes_voiture en voiture.
- exigences : caractéristiques indispensables ; preferences : souhaitées sans être indispensables.
  Valeurs possibles : {", ".join(FEATURES)}. « en pleine nature » / « à la campagne » = nature ;
  « sans vis-à-vis » = sans_vis_a_vis ; « avec la fibre » = fibre.
  Si l'utilisateur dit que quelque chose « peut être » vrai, c'est une préférence.
- secteur : direction par rapport au centre de Lyon si elle est indiquée (« sud-ouest de Lyon »).
- communes : communes citées comme zone de recherche (pas les lieux de trajet).
- Laisse null ou [] ce qui n'est pas demandé. N'invente aucune contrainte."""


# ---------------------------------------------------------------- Claude
def parse_with_claude(text: str) -> dict | None:
    if not config.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": CONSTRAINTS_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.AuthenticationError:
        print("Clé ANTHROPIC_API_KEY refusée : analyse par règles.")
        return None
    except anthropic.RateLimitError:
        return None
    except anthropic.APIStatusError:
        return None
    except anthropic.APIConnectionError:
        return None
    except TypeError:  # version du SDK trop ancienne pour un paramètre
        return None
    if response.stop_reason == "refusal":
        return None
    raw = next((b.text for b in response.content if b.type == "text"), None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- règles
def _amount(value: str, unit: str | None) -> float:
    v = float(re.sub(r"[\s  .]", "", value).replace(",", "."))
    unit = (unit or "").lower()
    if unit.startswith("k") or (v < 5000 and not unit.startswith("m")):
        v *= 1000
    if unit.startswith("m") and v < 100:
        v *= 1_000_000
    return v


def _modes(fragment: str) -> list[str]:
    f = norm(fragment)
    car = bool(re.search(r"voiture|auto\b|en caisse", f))
    tc = bool(re.search(r"transports?( en commun)?|\btcl\b|\btc\b|metro|tram|bus", f))
    if re.search(r"combo|combin|mixte|et/ou|ou ", f) or (car and tc) or not (car or tc):
        return list(MODES)
    return ["voiture"] if car else ["tcl"]


FEATURE_WORDS = [
    ("sans_vis_a_vis", r"sans vis[- ]?a[- ]?vis|pas de vis[- ]?a[- ]?vis|vue degagee"),
    ("fibre", r"\bfibre\b|tres haut debit"),
    ("jardin", r"\bjardin\b|exterieur|\bterrain\b"),
    ("nature", r"pleine nature|nature|campagne|verdure|au calme"),
    ("garage", r"\bgarage\b|parking|stationnement"),
    ("atypique", r"atypique|loft|grange|corps de ferme|caractere"),
    ("piscine", r"\bpiscine\b"),
    ("sans_travaux", r"sans travaux|pas de travaux|refait a neuf|aucun travaux"),
]
SOFT = r"(peut|pourrait|eventuellement|idealement|si possible|bonus|j'aime|pourquoi pas|de preference|un plus)"


def parse_with_rules(text: str) -> dict:
    t = norm(text)
    c: dict = {k: None for k in ("budget_max", "budget_min", "surface_min", "pieces_min", "chambres_min",
                                  "terrain_min", "secteur")}
    c.update(types=[], trajets=[], proximites=[], exigences=[], preferences=[], communes=[])

    # Budget : « 350 000 € », « 350k », « budget de 350000 », « max 350 k€ ».
    for m in re.finditer(r"(\d[\d\s.,]*\d|\d)\s*(k€|k\b|keur|m€|millions?|€|euros?)?(?!\s*(?:min|mn|km|m2|m²|pieces|chambres|h\b))", t):
        unit = (m.group(2) or "").strip()
        context = t[max(0, m.start() - 30):m.start()]
        digits = re.sub(r"\D", "", m.group(1))
        if not unit and not (re.search(r"budget|prix|max|jusqu'a|moins de|pas plus de", context) and len(digits) >= 5):
            continue
        amount = _amount(m.group(1), unit)
        if 30_000 <= amount <= 20_000_000:
            c["budget_max"] = amount
            break

    if re.search(r"\bmaisons?\b|\bvilla\b|\bpavillon\b|\bferme\b", t):
        c["types"].append("maison")
    if re.search(r"\bappartements?\b|\bappart\b|\bduplex\b|\bt[2-6]\b", t):
        c["types"].append("appartement")
    if re.search(r"\bloft\b", t):
        c["types"].append("loft")

    m = re.search(r"(?:au moins|minimum|mini|plus de|>)\s*(\d{2,4})\s*m(?:2|²)(?! de (?:terrain|jardin))", t) \
        or re.search(r"(\d{2,4})\s*m(?:2|²)\s*(?:minimum|mini|ou plus|habitables?)", t)
    if m:
        c["surface_min"] = float(m.group(1))
    m = re.search(r"(?:terrain|jardin)[^.,;]{0,25}?(\d[\d\s]*)\s*m(?:2|²)", t) \
        or re.search(r"(\d[\d\s]*)\s*m(?:2|²) de (?:terrain|jardin)", t)
    if m:
        c["terrain_min"] = float(re.sub(r"\s", "", m.group(1)))
    m = re.search(r"(\d)\s*chambres?", t)
    if m:
        c["chambres_min"] = int(m.group(1))
    m = re.search(r"(\d)\s*pieces", t)
    if m:
        c["pieces_min"] = int(m.group(1))

    # Trajets : « à moins de 45 min de la Grande Halle en combo transport voiture ».
    trip_re = re.compile(
        r"(?:a|en)\s+(?:moins\s+de\s+|max(?:imum)?\s+|pas plus de\s+)?(\d{1,3})\s*(?:min(?:utes)?|mn)\b"
        r"(?:\s*max(?:imum)?)?\s*(?:de|du|des|d')\s*(.+?)"
        r"(?=(?:,|;|\.|\bet\b(?! vous)|\bavec\b|\bsans\b|$))")
    for m in trip_re.finditer(t):
        minutes, rest = int(m.group(1)), m.group(2).strip()
        mode_m = re.search(r"\s+(en|par|via)\s+(.+)$", rest)
        modes = _modes(mode_m.group(2)) if mode_m else _modes(t[m.end():m.end() + 40])
        place = rest[:mode_m.start()] if mode_m else rest
        place = re.sub(r"^(la|le|les|l')\s*", "", place).strip()
        place = re.sub(r"\s+a lyon$", ", Lyon", place)
        if re.search(r"parc[s]? (relais|tcl)|p\s?\+\s?r", place):
            c["proximites"].append({"lieu": "parc relais TCL", "max_km": None, "max_minutes_voiture": float(minutes)})
        elif place:
            orig = _original_span(text, place)
            c["trajets"].append({"lieu": orig or place, "max_minutes": float(minutes), "modes": modes})

    # Proximité en km : « à moins de 5 km d'un parc TCL ».
    for m in re.finditer(r"(?:a|en)\s+(?:moins\s+de\s+)?(\d{1,3}(?:[.,]\d)?)\s*km\s*(?:de|du|des|d'un|d'une|d')\s*(.+?)"
                         r"(?=(?:,|;|\.|\bet\b|\bavec\b|\bsans\b|$))", t):
        km, place = float(m.group(1).replace(",", ".")), m.group(2).strip()
        place = re.sub(r"^(un|une|la|le|les|l')\s+", "", place)
        if re.search(r"parc[s]? (relais|tcl)|p\s?\+\s?r", place):
            place = "parc relais TCL"
        else:
            place = _original_span(text, place) or place
        c["proximites"].append({"lieu": place, "max_km": km, "max_minutes_voiture": None})
    if not any(p["lieu"] == "parc relais TCL" for p in c["proximites"]) and \
            re.search(r"(proche|pres|a cote)\s+d'un\s+(parc (relais|tcl)|p\s?\+\s?r)", t):
        c["proximites"].append({"lieu": "parc relais TCL", "max_km": None, "max_minutes_voiture": 15.0})

    # Caractéristiques : exigence, ou préférence si la phrase est au conditionnel.
    sentences = re.split(r"[.;!?\n]|, (?=mais|et)", t)
    for feat, pattern in FEATURE_WORDS:
        for s in sentences:
            if re.search(pattern, s):
                target = "preferences" if re.search(SOFT, s) else "exigences"
                if feat == "jardin" and c["terrain_min"]:
                    target = "exigences"
                if feat not in c[target]:
                    c[target].append(feat)
                break
    c["preferences"] = [f for f in c["preferences"] if f not in c["exigences"]]

    for sect in sorted(SECTEURS, key=len, reverse=True):
        if re.search(rf"\b{sect}\b(?: de| du)? (?:lyon|l'agglo|la metropole)", t) or \
                re.search(rf"\bdans le {sect}\b", t):
            c["secteur"] = sect
            break
    return c


def _original_span(original: str, normalized: str) -> str | None:
    """Retrouve dans le texte d'origine (avec accents et majuscules) un fragment normalisé."""
    o = norm(original)
    i = o.find(normalized)
    if i < 0 or len(o) != len(original):
        return None
    return original[i:i + len(normalized)].strip()


def parse(text: str) -> dict:
    """Contraintes + moteur utilisé."""
    result = parse_with_claude(text)
    engine = "claude"
    if result is None:
        result, engine = parse_with_rules(text), "règles"
    result["moteur"] = engine
    result["texte"] = text
    return result
