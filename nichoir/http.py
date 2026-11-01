"""Petit client HTTP avec cache disque, pour ménager les services publics utilisés."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config


_LAST_CALL: dict[str, float] = {}


def fetch(url: str, *, params: dict | None = None, ttl: float = 86400 * 7,
          timeout: float = 60, retries: int = 3, cache: bool = True,
          min_interval: float = 0.0, validate=None) -> bytes:
    """GET avec cache disque (durée de vie `ttl` en secondes, -1 = illimitée) et quelques
    tentatives. `min_interval` espace les appels réseau vers un même hôte.
    `validate(body)` doit lever une exception si la réponse est inutilisable (ex. tronquée) :
    elle n'est alors ni renvoyée ni mise en cache."""

    def usable(body: bytes) -> bool:
        if validate is None:
            return True
        try:
            validate(body)
            return True
        except Exception:
            return False

    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    path: Path | None = None
    if cache:
        key = hashlib.sha1(url.encode()).hexdigest()
        path = config.CACHE_DIR / "http" / key[:2] / key
        if path.exists() and (ttl < 0 or time.time() - path.stat().st_mtime < ttl):
            body = path.read_bytes()
            if usable(body):
                return body
            path.unlink()  # cache abîmé (téléchargement coupé) : on le jette
    last: Exception | None = None
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(retries):
        wait = _LAST_CALL.get(host, 0) + min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                expected = resp.headers.get("Content-Length")
            if expected and expected.isdigit() and len(body) < int(expected):
                raise IOError(f"réponse tronquée ({len(body)} octets sur {expected})")
            if not usable(body):
                raise IOError("réponse illisible ou tronquée")
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            return body
        except Exception as exc:  # réseau capricieux : on réessaie
            last = exc
            time.sleep(1.5 * (attempt + 1))
    if path is not None and path.exists():  # dernier recours : cache périmé
        body = path.read_bytes()
        if usable(body):
            return body
    raise RuntimeError(f"Échec du téléchargement de {url}: {last}")


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, validate=json.loads, **kw))
