"""Tests sans réseau : extraction, dédoublonnage, analyse des requêtes, alertes e-mail, filtrage."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("NICHOIR_DATA_DIR", tempfile.mkdtemp())
os.environ["ANTHROPIC_API_KEY"] = ""  # toujours l'analyseur à règles dans les tests

from nichoir import dedupe, normalize, query, search  # noqa: E402
from nichoir.ingest import email_alerts, rss  # noqa: E402

ALERT_HTML = """
<html><body><p>Bonjour, 2 nouvelles annonces correspondent à votre recherche</p>
<table>
 <tr><td><a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2876543210?utm_source=alerte">
   <img src="https://img.example/1.jpg"></a></td>
   <td><a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2876543210?utm_source=alerte">Maison 5 pièces 112 m²</a><br>
   339 000 €<br>Brindas 69126</td></tr>
 <tr><td><a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2876543999">Maison de ville</a><br>
   329 000 €<br>Saint-Genis-Laval 69230</td></tr>
</table>
<a href="https://www.leboncoin.fr/compte/alertes/unsubscribe">Se désabonner</a>
</body></html>"""


class NormalizeTest(unittest.TestCase):
    def test_fields(self):
        r = normalize.normalize({"title": "Maison 5 pièces avec jardin",
                                 "text": "339 000 € - Brindas (69126). Maison de 112 m² sur un terrain de 900 m², "
                                         "4 chambres. Aucun vis-à-vis. Fibre."})
        self.assertEqual(r["price"], 339000)
        self.assertEqual(r["surface"], 112)
        self.assertEqual(r["land"], 900)
        self.assertEqual(r["bedrooms"], 4)
        self.assertEqual(r["rooms"], 5)
        self.assertEqual(r["type"], "maison")
        self.assertEqual((r["postcode"], r["city"]), ("69126", "Brindas"))
        self.assertTrue(r["features"]["sans_vis_a_vis"])
        self.assertTrue(r["features"]["fibre"])

    def test_vis_a_vis_contradicted(self):
        self.assertIs(normalize.detect_features("Léger vis-à-vis sur l'arrière")["sans_vis_a_vis"], False)
        self.assertIs(normalize.detect_features("Maison sans vis à vis")["sans_vis_a_vis"], True)

    def test_hectares(self):
        self.assertEqual(normalize.parse_land("terrain de 1,5 ha"), 15000)


class DedupeTest(unittest.TestCase):
    def test_same_house_two_sites(self):
        a = {"price": 339000, "surface": 112, "postcode": "69126", "land": 900,
             "description": "Maison sur terrain de 900 m², aucun vis-à-vis, vue dégagée sur les monts du Lyonnais"}
        b = {"price": 340000, "surface": 112, "postcode": "69126", "land": 900,
             "description": "Belle maison, terrain de 900 m², aucun vis-à-vis, vue dégagée sur les monts du Lyonnais"}
        self.assertGreaterEqual(dedupe.similarity(a, b), 0.85)

    def test_different_houses(self):
        a = {"price": 339000, "surface": 112, "postcode": "69126"}
        b = {"price": 329000, "surface": 88, "postcode": "69230"}
        self.assertLess(dedupe.similarity(a, b), 0.5)

    def test_url_key_strips_tracking(self):
        self.assertEqual(dedupe.url_key("https://x.fr/a/1?utm_source=mail#top"), "https://x.fr/a/1")


class QueryTest(unittest.TestCase):
    TEXT = ("Maison à 350 000 € max, sans vis-à-vis, avec la fibre, à moins de 45 min de la Grande Halle "
            "à Lyon en combo transport voiture. Ça peut être en pleine nature, dans le sud-ouest de Lyon.")

    def test_clement_criteria(self):
        c = query.parse(self.TEXT)
        self.assertEqual(c["moteur"], "règles")
        self.assertEqual(c["budget_max"], 350000)
        self.assertEqual(c["types"], ["maison"])
        self.assertEqual(len(c["trajets"]), 1)
        self.assertEqual(c["trajets"][0]["max_minutes"], 45)
        self.assertEqual(set(c["trajets"][0]["modes"]), {"voiture", "tcl", "voiture+tcl"})
        self.assertIn("grande halle", c["trajets"][0]["lieu"].lower())
        self.assertEqual(set(c["exigences"]), {"sans_vis_a_vis", "fibre"})
        self.assertEqual(c["preferences"], ["nature"])
        self.assertEqual(c["secteur"], "sud-ouest")

    def test_several_places_and_relay(self):
        c = query.parse("appart 3 chambres à moins de 20 min de Bellecour en transport, "
                        "à moins de 5 km d'un parc relais TCL, budget 400k")
        self.assertEqual(c["budget_max"], 400000)
        self.assertEqual(c["trajets"][0]["modes"], ["tcl"])
        self.assertEqual(c["proximites"][0]["lieu"], "parc relais TCL")
        self.assertEqual(c["proximites"][0]["max_km"], 5)
        self.assertEqual(c["chambres_min"], 3)

    def test_schema_is_strict_compatible(self):
        props = query.CONSTRAINTS_SCHEMA["properties"]
        self.assertEqual(set(props), set(query.CONSTRAINTS_SCHEMA["required"]))


class EmailTest(unittest.TestCase):
    def test_split_alert(self):
        items = email_alerts.split_listings(ALERT_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], "LeBonCoin")
        self.assertEqual(items[0]["photo"], "https://img.example/1.jpg")
        self.assertIn("339 000", items[0]["text"])
        first = normalize.normalize(items[0])
        self.assertEqual((first["price"], first["city"]), (339000, "Brindas"))

    def test_rss(self):
        xml = b"""<rss><channel><item><title>Maison 4 pieces - 320 000 EUR</title>
        <link>https://exemple.invalid/a</link><description>&lt;p&gt;Mornant (69440)&lt;/p&gt;</description>
        </item></channel></rss>"""
        items = rss.parse_feed(xml)
        self.assertEqual(items[0]["url"], "https://exemple.invalid/a")
        self.assertIn("Mornant", items[0]["text"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.listing = {"id": 1, "title": "Maison", "price": 339000, "type": "maison", "surface": 112,
                        "lat": 45.7197, "lon": 4.7042, "citycode": "69028", "city": "Brindas",
                        "geo_precision": "commune", "features": {"sans_vis_a_vis": True}}
        self.places = {"Grande Halle": {"label": "Grande Halle", "lat": 45.738, "lon": 4.841}}

    def _constraints(self, **kw):
        c = {"budget_max": 350000, "types": ["maison"],
             "trajets": [{"lieu": "Grande Halle", "max_minutes": 45, "modes": ["voiture", "tcl", "voiture+tcl"]}],
             "proximites": [], "exigences": ["sans_vis_a_vis", "fibre"], "preferences": [], "secteur": "sud-ouest"}
        c.update(kw)
        return c

    def _trip(self, minutes):
        best = {"minutes": minutes, "mode": "voiture+tcl", "detail": "test"}
        return {"best": best, "modes": {"voiture+tcl": best}}

    def test_kept_and_explained(self):
        with mock.patch("nichoir.travel.best_trip", return_value=self._trip(39)), \
                mock.patch("nichoir.fibre.commune_ftth_rate", return_value=0.97):
            ev = search.evaluate(self.listing, self._constraints(), self.places)
        self.assertIsNotNone(ev)
        self.assertIn("sans vis-à-vis", ev["ok"])
        self.assertTrue(any("fibre probable" in x for x in ev["ok"]))

    def test_too_far(self):
        with mock.patch("nichoir.travel.best_trip", return_value=self._trip(70)):
            self.assertIsNone(search.evaluate(self.listing, self._constraints(), self.places))

    def test_budget_and_sector(self):
        self.assertIsNone(search.evaluate({**self.listing, "price": 400000}, self._constraints(trajets=[]), {}))
        villeurbanne = {**self.listing, "lat": 45.771, "lon": 4.89}
        self.assertIsNone(search.evaluate(villeurbanne, self._constraints(trajets=[], exigences=[]), {}))

    def test_sector_geometry(self):
        self.assertTrue(search.in_sector((45.7197, 4.7042), "sud-ouest"))  # Brindas
        self.assertFalse(search.in_sector((45.771, 4.89), "sud-ouest"))  # Villeurbanne


if __name__ == "__main__":
    unittest.main()
