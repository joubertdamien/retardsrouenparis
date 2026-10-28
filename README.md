# Retards Rouen Rive Droite → Paris Saint-Lazare

Suivi automatique des retards des trains SNCF entre **Rouen Rive Droite** et **Paris Saint-Lazare**, avec des rapports **journaliers, hebdomadaires et mensuels** publiés sur GitHub Pages.

## Architecture

```
┌──────────────────────────┐      ┌─────────────────────────────┐      ┌──────────────────┐
│ GitHub Actions (cron)    │      │ Backend Python (stdlib only) │      │ GitHub Pages     │
│ toutes les 15 min        │ ───► │ collecte API SNCF temps réel │ ───► │ site statique    │
│                          │      │ + rapports JSON              │      │ (rapports J/W/M) │
└──────────────────────────┘      └─────────────────────────────┘      └──────────────────┘
         │ commits les snapshots JSONL dans data/
```

- **Source de données** : [API SNCF temps réel](https://numerique.sncf.com/startup/api/) (`api.sncf.com`, moteur Navitia) — départs de Rouen Rive Droite, retards mesurés à l'arrivée à Paris Saint-Lazare.
- **Odyssée des trains (odométrie)** : le flux [GTFS-RT VehiclePositions](https://transport.data.gouv.fr/datasets/horaires-sncf) de la SNCF (positions GPS temps réel, **sans token**) est collecté en parallèle. La détection d'arrêts sur la trace GPS estime indépendamment l'arrivée réelle à Paris Saint-Lazare, ce qui permet de **croiser le retard annoncé par l'API avec le retard observé** (colonnes « Arrêt odométrie » / « Retard odométrie » sur le site).
- **Stockage** : un snapshot JSONL par jour dans `data/snapshots/YYYY-MM-DD.jsonl` et les positions brutes dans `data/positions/YYYY-MM-DD.jsonl`, versionnés dans le dépôt (pas de base de données).
- **Rapports** : générés en JSON dans `site-build/` (`index.json`, `series.json`, `reports/daily-*.json`, `reports/weekly-*.json`, `reports/monthly-*.json`) puis fusionnés avec le site statique `site/` et déployés sur GitHub Pages.
- **Retard** : un train est compté « en retard » à partir de **5 minutes** de retard à l'arrivée (seuil SNCF), configurable via `ROUENPARIS_MIN_DELAY_MINUTES`.

## Mise en route

### 1. Obtenir un token API SNCF (gratuit)

1. Créez un compte sur [SNCF Numérique — API](https://numerique.sncf.com/startup/api/).
2. Créez une application et copiez votre token.
3. Dans le dépôt GitHub : **Settings → Secrets and variables → Actions → New repository secret**, nommez-le `SNCF_API_TOKEN`.

### 2. Activer GitHub Pages

**Settings → Pages → Source : « GitHub Actions »**. Le workflow `pages.yml` se déclenche à chaque push sur `main` et après chaque collecte.

### 3. Lancer la collecte

Le workflow `collect.yml` tourne automatiquement toutes les 15 minutes (05:00–23:45, heure de Paris). Déclenchez-le manuellement via l'onglet **Actions → Collect SNCF departures → Run workflow**.

## Utilisation en local

```bash
# Tests (aucun accès réseau requis, fixtures enregistrées)
PYTHONPATH=backend:tests python3 -m unittest discover -s tests -v

# Collecter un snapshot (nécessite SNCF_API_TOKEN)
export SNCF_API_TOKEN=...
PYTHONPATH=backend python3 -m rouenparis collect            # 1 snapshot (+ positions GPS)
PYTHONPATH=backend python3 -m rouenparis collect --count 4 --interval-seconds 900

# Collecter uniquement les positions GPS GTFS-RT (aucun token requis)
PYTHONPATH=backend python3 -m rouenparis collect-positions

# Générer les rapports (aucun token requis)
ROUENPARIS_DATA_DIR=data ROUENPARIS_SITE_DIR=site-build PYTHONPATH=backend python3 -m rouenparis report

# Collecte + rapports en une commande
PYTHONPATH=backend python3 -m rouenparis all

# Résoudre les identifiants stop_area depuis les codes UIC (utile si la SNCF les change)
PYTHONPATH=backend python3 -m rouenparis resolve-stations
```

Le site généré peut être servi localement, par exemple avec `python3 -m http.server -d site-build`.

## Tutoriel — comprendre le code étape par étape

Le notebook [`notebooks/tutorial.ipynb`](notebooks/tutorial.ipynb) déroule tout le pipeline de manière interactive et **sans token API** (fixtures enregistrées + flux GTFS-RT synthétique) :

1. configuration, 2. client API SNCF (départs temps réel), 3. stockage et déduplication, 4. décodage du flux GTFS-RT VehiclePositions (protobuf, sans dépendance), 5. odométrie et détection d'arrêts (estimation de l'arrivée réelle), 6. rapports journalier/hebdomadaire/mensuel, 7. génération du site.

```bash
jupyter notebook notebooks/tutorial.ipynb
```

## Configuration (variables d'environnement)

| Variable | Défaut | Description |
|---|---|---|
| `SNCF_API_TOKEN` | — (obligatoire pour `collect`) | Token de l'API SNCF |
| `ROUENPARIS_ORIGIN_STOP_AREA` | `stop_area:SNCF:87411017` | Gare de départ (Rouen Rive Droite) |
| `ROUENPARIS_DESTINATION_STOP_AREA` | `stop_area:SNCF:87271007` | Gare d'arrivée (Paris Saint-Lazare) |
| `ROUENPARIS_LOOKAHEAD_MINUTES` | `240` | Fenêtre de collecte des départs |
| `ROUENPARIS_MIN_DELAY_MINUTES` | `5` | Seuil de comptabilisation d'un retard |
| `ROUENPARIS_LINES` | (toutes) | Lignes à suivre, ex. `line:SNCF:K22,line:SNCF:K24` |
| `ROUENPARIS_DATA_DIR` | `data` | Répertoire des snapshots et positions |
| `ROUENPARIS_SITE_DIR` | `site-build` | Répertoire de sortie des rapports |
| `ROUENPARIS_TRACK_POSITIONS` | `1` | `0` désactive la collecte GTFS-RT |
| `ROUENPARIS_GTFS_RT_VEHICLE_POSITIONS_URL` | `…/sncf-gtfs-rt-vehicle-positions` | URL du flux VehiclePositions |
| `ROUENPARIS_GTFS_RT_ROUTE_IDS` | (tous) | Restreindre aux route_id donnés, ex. `OCE:Train:1,OCE:Train:2` |
| `ROUENPARIS_STOP_RADIUS_M` | `150` | Rayon de détection d'un arrêt (mètres) |
| `ROUENPARIS_MIN_STOP_DURATION_S` | `60` | Durée minimale d'un arrêt détecté (secondes) |
| `ROUENPARIS_STATION_LATITUDE` / `ROUENPARIS_STATION_LONGITUDE` | `48.8809` / `2.3206` | Coordonnées de la gare d'arrivée (Paris Saint-Lazare) |

## Arborescence

```
backend/rouenparis/   # package Python (bibliothèque standard uniquement)
  config.py           #   configuration (env)
  stations.py         #   résolution UIC → stop_area
  sncf_client.py      #   client API SNCF (départs temps réel)
  gtfsrt_proto.py     #   décodage GTFS-RT protobuf sans dépendance
  vehicle_positions.py#   client flux GTFS-RT VehiclePositions (odométrie)
  odometry.py         #   distance, vitesse, détection d'arrêts, arrivée estimée
  positions.py        #   stockage positions + rapprochement course ↔ train
  storage.py          #   snapshots JSONL + déduplication
  reports.py          #   agrégation J/W/M + génération du site
  __main__.py         #   CLI : collect | collect-positions | report | all | resolve-stations
site/                 # frontend statique (GitHub Pages)
notebooks/            # tutoriel interactif (toutes les étapes du pipeline)
.github/workflows/    # collect.yml (cron), pages.yml, test.yml
tests/                # tests unitaires (fixtures, pas de réseau)
data/snapshots/       # données collectées (créé par le workflow)
data/positions/       # positions GPS collectées (créé par le workflow)
```

## Licence des données

Les données proviennent de l'[API SNCF](https://numerique.sncf.com/startup/api/) (CGU SNCF Numérique) et du flux GTFS-RT VehiclePositions publié sur [transport.data.gouv.fr](https://transport.data.gouv.fr/datasets/horaires-sncf) (licence ODbL). Le décodage protobuf est assuré par `backend/rouenparis/gtfsrt_proto.py` (stdlib uniquement).
