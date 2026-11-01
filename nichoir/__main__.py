"""Ligne de commande : python -m nichoir <commande>."""
from __future__ import annotations

import argparse
import json
import sys

from . import config, db, query, search


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nichoir", description="Agrégateur d'annonces immobilières")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="lancer la page web")
    s.add_argument("--port", type=int)
    s.add_argument("--host", default="127.0.0.1")
    sub.add_parser("ingest", help="lire les alertes e-mail et les flux RSS")
    sub.add_parser("demo", help="charger les annonces d'exemple (fictives)")
    q = sub.add_parser("search", help="chercher en langage naturel")
    q.add_argument("texte")
    p = sub.add_parser("parse", help="afficher les contraintes comprises")
    p.add_argument("texte")
    sub.add_parser("warmup", help="pré-télécharger les données TCL et Arcep")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        serve(args.port, args.host)
    elif args.cmd == "ingest":
        from .ingest import email_alerts, rss
        conn = db.connect()
        print("E-mail :", email_alerts.fetch_imap(conn))
        feeds = config.get("NICHOIR_RSS_FEEDS").split()
        if feeds:
            print("RSS :", rss.fetch_feeds(conn, feeds))
    elif args.cmd == "demo":
        conn = db.connect()
        items = json.loads((config.ROOT / "samples" / "demo_listings.json").read_text(encoding="utf-8"))
        for item in items:
            action, lid = db.upsert(conn, {**item, "demo": True})
            print(f"{action:10} #{lid} {item['title']} ({item['city']})")
    elif args.cmd == "parse":
        print(json.dumps(query.parse(args.texte), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        res = search.search(db.all_listings(db.connect()), query.parse(args.texte))
        print(f"{len(res['resultats'])} annonce(s), {res['exclues']} exclue(s)")
        for r in res["resultats"]:
            ev = r["evaluation"]
            trip = ev["trajets"][0]["best"] if ev["trajets"] else None
            print(f"- [{ev['score']:5.1f}] {r['title']} — {r.get('city')} — {r.get('price')} €"
                  + (f" — {trip['minutes']} min ({trip['mode']})" if trip else ""))
            for line in ev["a_verifier"]:
                print(f"      ? {line}")
    elif args.cmd == "warmup":
        from . import fibre, tcl
        net = tcl.network()
        print(len(net.stops), "arrêts TCL ;", len(tcl.parcs_relais()), "parcs relais ;",
              fibre.commune_ftth_rate("69204"), "fibre à Saint-Genis-Laval")
    return 0


if __name__ == "__main__":
    sys.exit(main())
