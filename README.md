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
- **Stockage** : un snapshot JSONL par jour dans `data/snapshots/YYYY-MM-DD.jsonl`, versionné dans le dépôt (pas de base de données).
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
PYTHONPATH=backend python3 -m rouenparis collect            # 1 snapshot
PYTHONPATH=backend python3 -m rouenparis collect --count 4 --interval-seconds 900

# Générer les rapports (aucun token requis)
ROUENPARIS_DATA_DIR=data ROUENPARIS_SITE_DIR=site-build PYTHONPATH=backend python3 -m rouenparis report

# Collecte + rapports en une commande
PYTHONPATH=backend python3 -m rouenparis all

# Résoudre les identifiants stop_area depuis les codes UIC (utile si la SNCF les change)
PYTHONPATH=backend python3 -m rouenparis resolve-stations
```

Le site généré peut être servi localement, par exemple avec `python3 -m http.server -d site-build`.

## Configuration (variables d'environnement)

| Variable | Défaut | Description |
|---|---|---|
| `SNCF_API_TOKEN` | — (obligatoire pour `collect`) | Token de l'API SNCF |
| `ROUENPARIS_ORIGIN_STOP_AREA` | `stop_area:SNCF:87411017` | Gare de départ (Rouen Rive Droite) |
| `ROUENPARIS_DESTINATION_STOP_AREA` | `stop_area:SNCF:87271007` | Gare d'arrivée (Paris Saint-Lazare) |
| `ROUENPARIS_LOOKAHEAD_MINUTES` | `240` | Fenêtre de collecte des départs |
| `ROUENPARIS_MIN_DELAY_MINUTES` | `5` | Seuil de comptabilisation d'un retard |
| `ROUENPARIS_LINES` | (toutes) | Lignes à suivre, ex. `line:SNCF:K22,line:SNCF:K24` |
| `ROUENPARIS_DATA_DIR` | `data` | Répertoire des snapshots |
| `ROUENPARIS_SITE_DIR` | `site-build` | Répertoire de sortie des rapports |

## Arborescence

```
backend/rouenparis/   # package Python (bibliothèque standard uniquement)
  config.py           #   configuration (env)
  stations.py         #   résolution UIC → stop_area
  sncf_client.py      #   client API SNCF (départs temps réel)
  storage.py          #   snapshots JSONL + déduplication
  reports.py          #   agrégation J/W/M + génération du site
  __main__.py         #   CLI : collect | report | all | resolve-stations
site/                 # frontend statique (GitHub Pages)
.github/workflows/    # collect.yml (cron), pages.yml, test.yml
tests/                # tests unitaires (fixtures, pas de réseau)
data/snapshots/       # données collectées (créé par le workflow)
```

## Licence des données

Les données proviennent de l'[API SNCF](https://numerique.sncf.com/startup/api/) (CGU SNCF Numérique). Les horaires théoriques sont sous licence ODbL.
