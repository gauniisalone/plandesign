# 03 — Premier vertical slice et plan de fichiers

Statut : **proposition, en attente de validation**. Rien de ce qui suit n'est implémenté.

## 1. Le slice, tel qu'il fonctionnera réellement en 0.1.0

```text
Une image d'une pièce (JPEG/PNG local)
        ↓  ingestion : copie dans le projet, hash, EXIF retiré (option), contrôle qualité (flou, exposition)
Une mesure connue (ex. « ce mur fait 3,10 m »)
        ↓  calibration : l'utilisateur désigne un mur du plan (ou deux points image) et saisit la valeur
Détection ou saisie des murs
        ↓  HeuristicBoxDetector propose un rectangle (estimated, ≤ 0,6) OU l'utilisateur trace les murs
Plan 2D paramétrique corrigible
        ↓  éditeur SVG : sommets déplaçables, longueur de mur éditable, portes/fenêtres, cotations, undo/redo
Extrusion simple
        ↓  trimesh (toujours) + Blender headless si installé (.blend, GLB, aperçu PNG)
Visualisation 3D
        ↓  Three.js, GLB rechargé après chaque validation de patch
Export GLB + JSON (+ SVG du plan, quasi gratuit)
```

Ce qui est **honnêtement limité** dans ce slice : la détection initiale est heuristique (lignes
et points de fuite, hypothèse de pièce rectangulaire) et ne fait que proposer un point de départ.
La valeur du slice est dans la chaîne mesure → plan paramétrique → 3D → export, qui est le socle
de tout le reste. Les modèles de profondeur et de segmentation arrivent en 0.1.x, derrière les
mêmes protocoles.

## 2. Découpage en incréments (chacun livrable, testé, mergeable)

| # | Incrément | Contenu | Critère d'acceptation |
|---|---|---|---|
| 0 | Bootstrap | `pyproject.toml` (uv, Python 3.12, ruff, mypy strict, pytest, import-linter), `apps/web` (Vite + React + TS + ESLint), `Makefile` adapté, `docker-compose.yml` réduit, CI locale `make check` | `make check` passe à vide ; `import openplan` ; `npm run build` |
| 1 | Schémas + scène | `openplan/schemas/*`, `openplan/scene/{store,history,migrate}`, export JSON Schema, types TS générés | Aller-retour JSON sans perte ; patch + inverse = identité ; invariants refusent un mur orphelin |
| 2 | Noyau géométrique | `openplan/geom/{wallgraph,polygon,scale}` | Fixture 4 × 3 : aire 12,00 m² ± 1e-6 ; boucle fermée détectée ; déplacer un sommet met à jour 2 murs ; 1 mesure 3,10 m sur un mur de 4 u → facteur 0,775 ; 3 mesures incohérentes → l'une marquée |
| 3 | API + éditeur 2D | FastAPI (projets, scène, patch, undo/redo, dérivés), éditeur SVG, calibration par mur | Créer un projet, tracer 4 murs, calibrer, éditer une longueur, undo : le JSON et l'UI concordent |
| 4 | 3D | `scene3d.build_mesh`, export GLB via trimesh, `blender/scripts/build_scene.py`, viewer Three.js | GLB valide (validateur glTF), un nœud par UUID, ouverture percée dans le mur ; Blender produit le même volume à 1 % près quand installé |
| 5 | Image | ingestion, EXIF, contrôle qualité, `HeuristicBoxDetector`, `ExifCameraEstimator`, fond de photo dans l'éditeur | Sur la fixture synthétique « pièce rectangulaire », les 4 murs proposés sont à source `estimated` et l'utilisateur peut les corriger ; l'image floue déclenche un avertissement |
| 6 | Docs et évaluation | `docs/models/model_registry.md`, `docs/privacy/threat-model.md`, `docs/evaluation/protocol.md`, fixtures listées comme « à produire » quand elles n'existent pas | Aucune métrique inventée ; chaque métrique porte « implémentée » ou « à implémenter » |

Ordre : 0 → 1 → 2 → 4 → 3 → 5 → 6 est aussi possible (3D avant UI) ; je propose 0 → 1 → 2 → 3 →
4 → 5 → 6 pour avoir un plan éditable visible dès l'incrément 3.

## 3. Plan de fichiers du slice

```text
openplan-studio/
├── pyproject.toml                    uv ; deps : pydantic>=2, numpy, shapely>=2, trimesh, pillow,
│                                     opencv-python-headless, fastapi, uvicorn, jsonpatch, pyyaml
│                                     dev : pytest, pytest-cov, ruff, mypy, import-linter, httpx
├── .python-version                   3.12
├── Makefile                          setup / dev (api + web) / test / lint / typecheck / check / web-types
├── docker-compose.yml                api (uvicorn) + web (vite) ; volumes projets ; pas de DB
├── .env.example                      OPENPLAN_DATA_DIR, OPENPLAN_BLENDER_BIN, OPENPLAN_DEVICE
├── openplan/
│   ├── __init__.py                   __version__
│   ├── schemas/
│   │   ├── common.py                 Source, SOURCE_PRIORITY, Provenance, Uncertainty, ElementBase, Point2/3, BBox3, Transform3
│   │   ├── elements.py               Vertex, Wall, Opening, Room, Floor, Ceiling, RoofPlane, Beam, Radiator, Stairs, Fixture, Furniture, Light, Camera
│   │   ├── scene.py                  ImageRef, Measurement, Calibration, Constraint, HistoryEntry, Level, Project, PrivacySettings
│   │   ├── results.py                StepResult, Artifact, Assumption, RunContext, ValidationReport, Violation
│   │   └── jsonschema_export.py      écrit schemas/*.json (source des types TS)
│   ├── scene/
│   │   ├── store.py                  <data>/<project_id>/{project.json, images/, runs/} + index SQLite
│   │   ├── history.py                apply_patch, invert, undo, redo (jsonpatch)
│   │   └── migrate.py                registre de migrations par schema_version
│   ├── geom/
│   │   ├── wallgraph.py
│   │   ├── polygon.py                aire, orientation, emprise de mur, intérieur de pièce (Shapely)
│   │   └── scale.py                  solve_scale
│   ├── validation/
│   │   ├── engine.py                 ValidationReport, validate_geometry
│   │   └── invariants.py             règles listées en doc 01 §7
│   ├── adapters/
│   │   ├── protocols.py
│   │   ├── registry.py
│   │   └── heuristic/
│   │       ├── lines.py              OpenCV LSD
│   │       ├── vanishing.py          RANSAC points de fuite
│   │       ├── box_detector.py       HeuristicBoxDetector
│   │       └── exif_camera.py        ExifCameraEstimator
│   ├── pipeline/
│   │   ├── base.py                   PipelineStep, runner, application de patch avec garde de priorité
│   │   ├── ingestion.py
│   │   ├── quality.py                variance du Laplacien, histogramme, résolution minimale
│   │   ├── detection.py
│   │   ├── calibration.py
│   │   ├── floorplan.py
│   │   ├── scene3d.py                build_mesh (trimesh), appel Blender optionnel
│   │   └── export.py                 JsonExporter, GlbExporter, SvgExporter
│   ├── api/
│   │   ├── app.py                    create_app(), CORS localhost, bind 127.0.0.1
│   │   ├── deps.py                   store, settings
│   │   └── routes/{projects,images,measurements,runs,scene,exports,models}.py
│   └── cli/
│       └── main.py                   openplan serve | run | export | schemas export | models list
├── blender/
│   ├── LICENSE                       GPL-3.0-or-later (décision en doc 04)
│   └── scripts/build_scene.py
├── apps/web/
│   ├── package.json  vite.config.ts  tsconfig.json  index.html
│   └── src/
│       ├── api/{client.ts, types.gen.ts}
│       ├── state/sceneStore.ts
│       ├── editor2d/{PlanCanvas.tsx, WallLayer.tsx, OpeningLayer.tsx, DimensionLayer.tsx, PhotoLayer.tsx, tools/*.ts, sourceStyle.ts}
│       ├── viewer3d/Viewer3D.tsx
│       ├── calibration/Calibrate.tsx
│       ├── pages/{Home,NewProject,Import,Calibrate,Plan,View3D,Export}.tsx
│       └── ui/  (Units, ConfidenceBadge, SourceLegend, HistoryBar)
├── tests/
│   ├── unit/{test_schemas.py, test_history.py, test_wallgraph.py, test_scale.py, test_validation.py, test_quality.py}
│   ├── geometry/{test_polygon.py, test_extrusion.py, test_openings.py}
│   ├── integration/{test_api_slice.py, test_cli_slice.py, test_blender_bridge.py (skip si Blender absent)}
│   └── fixtures/
│       ├── rect_room/  scene.json + image synthétique rendue (à produire avec Blender) + vérité terrain
│       └── l_room/     scene.json (vérité terrain) ; image : à produire
├── models/
│   ├── registry.yaml
│   └── .gitignore                    poids exclus
├── docs/
│   ├── architecture/00..04           (ce dossier)
│   ├── models/model_registry.md
│   ├── privacy/threat-model.md
│   ├── evaluation/protocol.md
│   ├── licenses/README.md            inventaire des licences (code, modèles, assets)
│   └── contributing/setup.md         commandes d'installation WSL / conteneur
├── README.md  LICENSE  CONTRIBUTING.md
```

## 4. Commandes prévues

```bash
# WSL, hors conteneur (après installation de uv et nvm) ou dans sandbox-base
make setup          # uv sync --all-extras ; cd apps/web && npm ci
make web-types      # uv run openplan schemas export → apps/web/src/api/types.gen.ts
make dev            # uvicorn 127.0.0.1:8000 + vite 127.0.0.1:5173
make test           # pytest -q --cov=openplan
make lint           # ruff check . ; eslint apps/web
make typecheck      # mypy --strict openplan ; tsc --noEmit
make check          # lint + typecheck + test + build
uv run openplan run tests/fixtures/rect_room/image.png --measure wall:3.10 --out out/   # slice en CLI
```

## 5. Tests du slice (ce qui sera écrit, pas simulé)

- **Unitaires géométrie** : aire, périmètre, fermeture, orientation, offset d'épaisseur,
  déplacement de sommet, réglage de longueur avec ancre, facteur d'échelle 1 et n mesures,
  détection d'incohérence.
- **Unitaires format** : sérialisation, validation des invariants, migration 0.1.0 → 0.1.0
  (identité), patch/inverse.
- **Géométrie 3D** : volume des murs = Σ(longueur × épaisseur × hauteur) − volume des ouvertures,
  à 1e-6 près ; GLB relu par trimesh avec les mêmes noms de nœuds ; validateur glTF sans erreur.
- **Intégration API** : scénario complet du slice via `httpx` sur `create_app()`, sans réseau.
- **Pont Blender** : `pytest.mark.skipif(shutil.which("blender") is None)` ; compare le nombre
  d'objets et le volume total à la version trimesh.
- **Heuristique** : image synthétique de pièce rectangulaire (rendue par Blender depuis
  `rect_room/scene.json`, avec pose caméra connue) → points de fuite retrouvés à ±2° ; proportions
  du rectangle à ±15 %. Ce seuil est un objectif, pas une promesse : il sera ajusté en fonction
  du résultat réel et documenté dans `docs/evaluation/protocol.md`.
