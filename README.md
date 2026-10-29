# Retards Rouen Rive Droite → Paris Saint-Lazare

Suivi automatique des retards des trains SNCF entre **Rouen Rive Droite** et **Paris Saint-Lazare**, avec des rapports **journaliers, hebdomadaires et mensuels** publiés sur GitHub Pages, et une **vérification croisée** des retards annoncés via le flux GTFS-RT TripUpdates.

🔗 **Site en ligne** : [joubertdamien.github.io/retardsrouenparis](https://joubertdamien.github.io/retardsrouenparis/)

## Architecture

```
┌─────────────────────┐      ┌──────────────────────────────┐      ┌───────────────────┐
│ GitHub Actions (cron)│      │ Backend Python (stdlib only) │      │ GitHub Pages      │
│ toutes les 15 min    │ ───► │ collecte API SNCF temps réel │ ───► │ site statique     │
│                      │      │ + rapports JSON              │      │ (rapports J/W/M)  │
└─────────────────────┘      └──────────────────────────────┘      └───────────────────┘
         │ commits les snapshots JSONL dans data/
         ▼
   ┌─────────────────────────────────────────────┐
   │ Deux sources de données indépendantes :     │
   │  1. API SNCF (api.sncf.com, Navitia)        │
   │     → retards annoncés (token requis)       │
   │  2. GTFS-RT TripUpdates (data.gouv.fr)      │
   │     → retards par gare (sans token)         │
   │     → vérification croisée des retards      │
   └─────────────────────────────────────────────┘
```

- **Source principale** : [API SNCF temps réel](https://numerique.sncf.com/startup/api/) (`api.sncf.com`, moteur Navitia) — départs de Rouen Rive Droite, retards mesurés à l'arrivée à Paris Saint-Lazare.
- **Mode dégradé (sans token)** : si `SNCF_API_TOKEN` n'est pas défini, la page affiche les trains Rouen→Paris directement depuis le flux GTFS-RT TripUpdates, par **correspondance de corridor** : un train arrivant à Paris Saint-Lazare est identifié comme train Rouen→Paris si sa liste d'arrêts contient une gare du corridor Rouen–Paris (Oissel, Val-de-Reuil, Vernon, Mantes-la-Jolie, Poissy) avant Paris. Le retard affiché est le **retard à l'arrivée à Paris**. Une bannière jaune indique ce mode.
- **Vérification croisée** : le flux [GTFS-RT TripUpdates](https://transport.data.gouv.fr/datasets/horaires-sncf) de la SNCF (**sans token**) est collecté en parallèle. Il contient les prédictions d'arrivée/départ **par gare** pour chaque train en circulation. Le retard à l'arrivée à Paris Saint-Lazare extrait de ce flux est comparé au retard annoncé par l'API SNCF (tolérance : 60 secondes). **Note** : la SNCF ne publie pas de positions GPS publiques pour ses trains ; le flux VehiclePositions initialement envisagé n'existe pas sur ce jeu de données. TripUpdates est l'alternative disponible.
- **Détection de holds** : quand le retard à l'arrivée à une gare dépasse le retard au départ de la gare précédente, le train a été retenu en ligne — c'est la signature d'un arrêt/hold détecté sans GPS.
- **Stockage** : un snapshot JSONL par jour dans `data/snapshots/YYYY-MM-DD.jsonl` et les profils TripUpdates dans `data/trip_updates/YYYY-MM-DD.jsonl`, versionnés dans le dépôt (pas de base de données).
- **Rapports** : générés en JSON dans `site-build/` (`index.json`, `series.json`, `crosscheck.json`, `reports/daily-*.json`, `reports/weekly-*.json`, `reports/monthly-*.json`) puis fusionnés avec le site statique `site/` et déployés sur GitHub Pages.
- **Retard** : un train est compté « en retard » à partir de **5 minutes** de retard à l'arrivée (seuil SNCF), configurable via `ROUENPARIS_MIN_DELAY_MINUTES`.

## Mise en route

### 1. Obtenir un token API SNCF (gratuit)

1. Créez un compte sur [SNCF Numérique — API](https://numerique.sncf.com/startup/api/).
2. Créez une application et copiez votre token.
3. Dans le dépôt GitHub : **Settings → Secrets and variables → Actions → New repository secret**, nommez-le `SNCF_API_TOKEN`.

> Si le portail numerique.sncf.com est indisponible, le token existant continue de fonctionner. Seule la création de nouveaux tokens est bloquée. La collecte GTFS-RT TripUpdates ne nécessite **aucun** token.

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
PYTHONPATH=backend python3 -m rouenparis collect            # 1 snapshot (+ TripUpdates)
PYTHONPATH=backend python3 -m rouenparis collect --count 4 --interval-seconds 900

# Collecter uniquement les profils GTFS-RT TripUpdates (aucun token requis)
PYTHONPATH=backend python3 -m rouenparis collect-trip-updates

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

1. configuration, 2. client API SNCF (départs temps réel), 3. stockage et déduplication, 4. décodage du flux GTFS-RT TripUpdates (protobuf, sans dépendance), 5. détection de holds et vérification croisée des retards, 6. rapports journalier/hebdomadaire/mensuel, 7. génération du site.

```bash
jupyter notebook notebooks/tutorial.ipynb
```

## Configuration (variables d'environnement)

| Variable | Défaut | Description |
|---|---|---|
| `SNCF_API_TOKEN` | — (recommandé ; sans token, mode dégradé GTFS-RT TripUpdates) | Token de l'API SNCF |
| `ROUENPARIS_ORIGIN_STOP_AREA` | `stop_area:SNCF:87411017` | Gare de départ (Rouen Rive Droite) |
| `ROUENPARIS_DESTINATION_STOP_AREA` | `stop_area:SNCF:87271007` | Gare d'arrivée (Paris Saint-Lazare) |
| `ROUENPARIS_ORIGIN_UIC8` | `87411017` | UIC 8 chiffres de Rouen (pour TripUpdates) |
| `ROUENPARIS_DESTINATION_UIC8` | `87271007` | UIC 8 chiffres de Paris St-Lazare (pour TripUpdates) |
| `ROUENPARIS_CORRIDOR_UIC8` | `87411017,87411207,87415877,87415620,87415604,87381509,87317586,87317263,87342014` | UIC 8 chiffres du corridor Rouen–Paris (mode dégradé) |
| `ROUENPARIS_LOOKAHEAD_MINUTES` | `240` | Fenêtre de collecte des départs |
| `ROUENPARIS_MIN_DELAY_MINUTES` | `5` | Seuil de comptabilisation d'un retard |
| `ROUENPARIS_LINES` | (toutes) | Lignes à suivre, ex. `line:SNCF:K22,line:SNCF:K24` |
| `ROUENPARIS_DATA_DIR` | `data` | Répertoire des snapshots et profils |
| `ROUENPARIS_SITE_DIR` | `site-build` | Répertoire de sortie des rapports |
| `ROUENPARIS_TRACK_TRIP_UPDATES` | `1` | `0` désactive la collecte GTFS-RT TripUpdates |
| `ROUENPARIS_TRACK_POSITIONS` | `1` | `0` désactive la collecte VehiclePositions (GPS) |
| `ROUENPARIS_GTFS_RT_TRIP_UPDATES_URL` | `…/sncf-gtfs-rt-trip-updates` | URL du flux TripUpdates |
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
  gtfsrt_proto.py     #   décodage GTFS-RT protobuf sans dépendance (TripUpdates + VehiclePositions)
  trip_updates.py     #   client flux GTFS-RT TripUpdates + détection de holds
  trip_storage.py     #   stockage profils TripUpdates + rapprochement + vérification croisée
  vehicle_positions.py#   client flux GTFS-RT VehiclePositions (odométrie GPS, optionnel)
  odometry.py         #   distance, vitesse, détection d'arrêts, arrivée estimée (GPS)
  positions.py        #   stockage positions + rapprochement course ↔ train (GPS)
  storage.py          #   snapshots JSONL + déduplication
  reports.py          #   agrégation J/W/M + vérification croisée + génération du site
  __main__.py         #   CLI : collect | collect-trip-updates | collect-positions | report | all | resolve-stations
site/                 # frontend statique (GitHub Pages)
notebooks/            # tutoriel interactif (toutes les étapes du pipeline)
.github/workflows/    # collect.yml (cron), pages.yml, test.yml
tests/                # tests unitaires (fixtures, pas de réseau)
data/snapshots/       # données collectées (créé par le workflow)
data/trip_updates/    # profils TripUpdates collectés (créé par le workflow)
data/positions/       # positions GPS collectées (créé par le workflow, optionnel)
```

## Licence des données

Les données proviennent de l'[API SNCF](https://numerique.sncf.com/startup/api/) (CGU SNCF Numérique) et du flux GTFS-RT TripUpdates publié sur [transport.data.gouv.fr](https://transport.data.gouv.fr/datasets/horaires-sncf) (licence ODbL). Le décodage protobuf est assuré par `backend/rouenparis/gtfsrt_proto.py` (stdlib uniquement).
