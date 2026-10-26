# PlanDesign

[![ci](https://github.com/gauniisalone/plandesign/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gauniisalone/plandesign/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=gauniisalone_plandesign&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=gauniisalone_plandesign)
[![Couverture](https://sonarcloud.io/api/project_badges/measure?project=gauniisalone_plandesign&metric=coverage)](https://sonarcloud.io/component_measures?id=gauniisalone_plandesign&metric=coverage)
[![Sécurité](https://sonarcloud.io/api/project_badges/measure?project=gauniisalone_plandesign&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=gauniisalone_plandesign)

*Anciennement OpenPlan Studio. La commande reste `openplan` (alias `plandesign`), comme le paquet
Python, les variables `OPENPLAN_*` et le dossier des données.*

Logiciel open-source, gratuit et local-first qui transforme des photos d'intérieur en
plan 2D paramétrique, modèle 3D éditable et propositions d'aménagement.

> Aide à la conception intérieure. Une reconstruction estimée n'est **jamais** un relevé
> architectural certifié : chaque dimension est marquée mesurée, observée, estimée, inférée
> ou générée, et l'incertitude reste visible.

**État : préversion (0.x), en développement actif.** Une photo ou un programme de pièces donne
un plan 2D éditable, une scène 3D, des propositions d'aménagement validées par un moteur de
contraintes et des rendus sur un tableau de projet. Les estimations depuis une photo restent un
point de départ, pas un relevé. Avancement détaillé : [docs/STATUS.md](docs/STATUS.md) ;
conception : [docs/architecture](docs/architecture/).

**Modèles et poids non fournis.** Ce dépôt ne contient aucun poids de réseau de neurones. Les
fonctions qui en ont besoin (profondeur, détection d'ouvertures, finition et inspiration par
diffusion) sont optionnelles : `openplan models list` les décrit avec leur licence, et
`openplan models pull <id>` télécharge un poids sur demande explicite. Sans eux, le plan, la 3D,
l'aménagement et les exports fonctionnent.

```bash
make start   # construit le front et sert tout sur http://localhost:8000/ (une commande, un lien)
uv run openplan run photo.jpg --out out/demo --measure 3.85 --camera-height 1.4   # sans interface
```

**Installer sans cloner** (paquet avec le front dedans, sans Node) : une fois
[uv](https://docs.astral.sh/uv/) installé,

```bash
uv tool install "openplan-studio[app]"   # dès la publication PyPI ; en attendant, le wheel de `make build` : uv tool install "dist/openplan_studio-*.whl[app]"
openplan doctor        # ce qui marche ici, et la commande pour chaque manque
openplan serve --web   # http://localhost:8000/
```

Guide de dix minutes : [docs/guide/installer.md](docs/guide/installer.md). Blender, les modèles
locaux (torch, poids) et l'assistant IA restent optionnels et s'ajoutent après.

Depuis Windows avec WSL2 : double-cliquer `start-openplan.cmd`, ou dans PowerShell
`wsl -d Debian -- bash -lc "cd <dossier du dépôt> && make start"`,
puis ouvrir <http://localhost:8000/>. Premier essai guidé pour non-développeur :
[docs/guide/premier-essai.md](docs/guide/premier-essai.md). En développement :
`make api` (API, rechargement) et `make dev` (Vite, 127.0.0.1:5173). En conteneur :
`docker compose up --build` (image légère : sans profondeur, inspiration ni Blender), même lien.

## Principes

- Les photos restent sur la machine. Aucun appel réseau sans consentement explicite.
- Les modèles IA sont interchangeables, documentés avec leur licence, téléchargés à la demande.
- Le plan 2D et la scène 3D dérivent d'un seul format de scène paramétrique, éditable et versionné.
- L'IA propose, l'utilisateur corrige et valide.

## Stack

| Couche | Choix |
|---|---|
| Noyau géométrie / format | Python 3.12, Pydantic v2, NumPy, Shapely, trimesh |
| API locale | FastAPI, `127.0.0.1` uniquement par défaut |
| Vision (0.1) | OpenCV (LSD), Pillow, NumPy ; aucun réseau de neurones, aucun poids téléchargé |
| Front | React, TypeScript, Vite, SVG (plan), Three.js (3D) |
| Rendu | Blender headless, optionnel |
| Outillage | uv, ruff, mypy strict, pytest, import-linter |

## Démarrage

```bash
make setup      # uv sync + npm ci
make check      # lint + types + tests + build
```

Installation détaillée (WSL, uv, nvm, Blender) : [docs/contributing/setup.md](docs/contributing/setup.md).

## Structure

```
openplan/          paquet Python (schemas, geom, scene, validation, adapters, pipeline, api, cli)
apps/web/          application web
blender/           scripts Blender (bpy + stdlib uniquement, licence GPL-3.0-or-later)
models/            registre des modèles ; les poids ne sont jamais commités
tests/             unit / geometry / integration / fixtures
docs/              architecture, modèles, licences, confidentialité, évaluation
```

## Licences

Code du dépôt : [Apache-2.0](LICENSE), sauf `blender/` : [GPL-3.0-or-later](blender/LICENSE)
(les scripts qui importent `bpy` sont des œuvres dérivées de Blender). Inventaire des licences
des dépendances, modèles et assets : [docs/licenses/README.md](docs/licenses/README.md) ;
registre des modèles : [docs/models/model_registry.md](docs/models/model_registry.md) ;
confidentialité : [docs/privacy/threat-model.md](docs/privacy/threat-model.md) ;
évaluation : [docs/evaluation/protocol.md](docs/evaluation/protocol.md).
