# Le Nichoir

Trouver une maison ou un appartement en regroupant les annonces de plusieurs sites, avec une recherche
en langage naturel du type :

> Maison à 350 000 € max, sans vis-à-vis, avec la fibre, à moins de 45 min de la Grande Halle à Lyon
> en combo transport voiture. Ça peut être en pleine nature, dans le sud-ouest de Lyon.

La page traduit la phrase en contraintes précises (affichées sous la barre de recherche), calcule les
temps de trajet porte à porte (voiture seule, TCL seuls, ou voiture jusqu'à un parc relais puis TCL),
filtre les annonces, puis explique pour chacune ce qui est vérifié (✓) et ce qui reste à vérifier (?).

## Démarrer

Python 3.10 ou plus, sans autre dépendance obligatoire.

```bash
cp .env.example .env          # facultatif : boîte mail des alertes, clé Claude
python -m nichoir warmup      # télécharge les données TCL et Arcep (une fois par semaine)
python -m nichoir demo        # charge 12 annonces d'exemple fictives
python -m nichoir serve       # puis ouvrir http://127.0.0.1:8000
```

En ligne de commande :

```bash
python -m nichoir parse  "maison à moins de 30 min de Bellecour en transport, budget 400k"
python -m nichoir search "maison à moins de 45 min de la Grande Halle, sans vis-à-vis"
python -m nichoir ingest      # relève les alertes e-mail et les flux RSS
python -m unittest discover -s tests
```

## D'où viennent les annonces

LeBonCoin, SeLoger, PAP, Logic-Immo et la plupart des portails bloquent les robots, et presque tous
(Figaro Immobilier, Laforêt, Immobilier.notaires…) interdisent la lecture automatique dans leurs
conditions d'utilisation. Le Nichoir ne les aspire donc pas.

1. **Bouton « Envoyer au Nichoir »** (source principale). Sur la page du Nichoir, glisser le bouton
   dans la barre de favoris. Ensuite, sur n'importe quelle page de résultats ou d'annonce
   (LeBonCoin, SeLoger, PAP, Bien'ici, agences…), un clic envoie les annonces affichées au Nichoir.
   Le script tourne dans ton navigateur, sur ce que tu as déjà sous les yeux, sans aucune requête
   vers le site. Il lit en priorité les données structurées de la page (LeBonCoin, schema.org), puis
   les cartes d'annonces, puis la page d'une annonce seule.
2. **Ajout manuel** : copier-coller le texte d'une annonce dans la page.
3. *Facultatif* : alertes e-mail lues en IMAP (`NICHOIR_IMAP_*`) et flux RSS (`NICHOIR_RSS_FEEDS`),
   via `python -m nichoir ingest`.

Chaque annonce est normalisée (prix, surface, terrain, pièces, chambres, commune, caractéristiques
comme « sans vis-à-vis » ou « fibre »), localisée (adresse si connue, sinon centre de la commune),
puis comparée aux annonces déjà connues : la même maison publiée sur deux sites est fusionnée, avec
les deux liens et l'historique des prix.

## Comment sont calculés les trajets

| Brique | Source | Remarque |
|---|---|---|
| Géocodage | Géoplateforme IGN (BAN + points d'intérêt) | « Grande Halle » est fixée rue Pierre Bourdeix (Lyon 7e) |
| Voiture | OSRM (serveur public) | temps sans trafic × `NICHOIR_CAR_FACTOR` (1,25 par défaut) + 5 min pour se garer |
| TCL | couches ouvertes du Grand Lyon : arrêts, tracés, parcs relais | voir ci-dessous |
| Voiture + TCL | les 22 parcs relais TCL | route jusqu'au P+R + 3 min + trajet TCL |
| Fibre | Arcep, « Ma connexion internet », par commune | « probable » si ≥ 90 % des locaux de la commune sont éligibles |

Les horaires GTFS officiels des TCL demandent un compte data.grandlyon.com. En attendant, le réseau
est modélisé sans horaires : les arrêts sont ordonnés le long du tracé de chaque ligne, avec une vitesse
commerciale par mode (métro 34 km/h, tram 19, bus 17-18) et une attente moyenne d'heure de pointe
(métro 2 min, tram 3,5, Chrono 4, bus 7,5). Exemples obtenus vers la Grande Halle :
P+R Saint-Genis-Laval Hôpital Sud 18 min (métro B), P+R Oullins 14 min, Gorge de Loup 24 min.
C'est une estimation à ± 5 min ; un calcul exact sur GTFS est la prochaine étape.

Quand l'adresse exacte d'une annonce est inconnue (cas fréquent dans les alertes), le trajet part du
centre de la commune, avec 5 min de tolérance, et l'annonce le signale.

## Recherche en langage naturel

- Avec `ANTHROPIC_API_KEY` et le paquet `anthropic` : Claude traduit la phrase en contraintes
  (sortie JSON structurée).
- Sinon : un analyseur à règles en français gère budget, type de bien, surfaces, chambres, temps de
  trajet vers un ou plusieurs lieux (avec le mode), distance à un lieu ou à un parc relais TCL,
  caractéristiques exigées ou souhaitées (« ça peut être… » = bonus) et secteur (« sud-ouest de Lyon »).

Les caractéristiques exigées qu'une annonce ne mentionne pas ne l'écartent pas : elles baissent son
score et apparaissent en « à vérifier ». Une annonce qui dit le contraire (« vis-à-vis sur l'arrière »)
est fortement pénalisée.

## Organisation du code

```
nichoir/
  query.py        langage naturel -> contraintes (Claude ou règles)
  search.py       filtrage, score et explications
  travel.py       voiture / TCL / voiture + P+R
  tcl.py          modèle du réseau TCL (données ouvertes Grand Lyon)
  geo.py          géocodage IGN, distances
  fibre.py        couverture fibre Arcep
  normalize.py    extraction des champs d'une annonce
  dedupe.py       dédoublonnage entre sites
  db.py           stockage SQLite (data/nichoir.sqlite3)
  ingest/         alertes e-mail (IMAP) et flux RSS
  server.py       page web + API JSON
web/index.html    la page (liste + carte Leaflet)
samples/          annonces d'exemple fictives
```

## Pistes suivantes

- Horaires TCL exacts (GTFS) avec un compte data.grandlyon.com, et trains TER (SNCF) pour les P+R de gare.
- Fibre à l'adresse (base des immeubles de l'Arcep) quand l'adresse est connue.
- Estimer le vis-à-vis à partir des bâtiments voisins (cadastre / BD TOPO).
- Catégories de lieux génériques (« près d'une école », « d'une forêt ») via OpenStreetMap.
- Relève automatique des alertes à intervalle régulier et notification des nouvelles annonces.
