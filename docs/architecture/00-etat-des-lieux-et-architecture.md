# 00 — État des lieux et choix d'architecture

Statut : **proposition, en attente de validation** (23 septembre 2026).

## 1. État du dépôt et de l'environnement

### Dépôt

Aucun code OpenPlan Studio n'existait avant ce document, ni dans WSL ni sur le disque Windows.
Le projet a été créé le 23 septembre 2026 avec `./setup-sandbox.sh openplan-studio`
(template du workspace, `git init`, commit initial `3d60913`).

Le template apporte : `Makefile` générique, `docker-compose.yml` PostgreSQL + Redis,
`.claude/settings.json` (sandbox natif), `.gitignore`, `.env.example`.
Pour le MVP, PostgreSQL et Redis ne sont pas nécessaires (SQLite suffit) : le `docker-compose.yml`
sera réduit à `api` + `web` lors du bootstrap.

### Environnement constaté

| Composant | Hôte WSL (Debian 13) | Image `sandbox-base` | Remarque |
|---|---|---|---|
| Python | 3.13.5 système, aucun paquet scientifique | 3.13.5 dans `/opt/venv` | Voir contrainte de version ci-dessous |
| Node | absent | 24.20 LTS, npm 11 | Front à développer dans le conteneur ou installer nvm sur l'hôte |
| uv / poetry | absents | absents | `uv` recommandé (binaire unique, installation utilisateur) |
| Blender | absent | absent (prévu dans `sandbox-3d`, **jamais buildée**, épingle 4.2.5 LTS) | À builder ou installer sur l'hôte |
| PyTorch | absent | absent (prévu dans `sandbox-ml`, jamais buildée) | CPU par défaut, CUDA via `build-arg` |
| Docker | 29.8, Engine natif WSL | client | OK |
| git / make | 2.47 / 4.4 | OK | OK |
| GPU | NVIDIA RTX PRO 500 Blackwell (laptop), **6 Go VRAM**, pilote 596.58, `/dev/dxg` visible | — | CUDA possible sous WSL, VRAM limitée |
| RAM / disque | 16 Go alloués à WSL (32 Go physiques), 940 Go libres | — | OK |
| Windows | aucun outil de dev (python = stub Store) | — | Tout se fait dans WSL |

### Contraintes qui découlent de l'environnement

1. **6 Go de VRAM.** Modèles de profondeur « small/base » et segmentation légère tiennent ;
   diffusion SDXL/FLUX non quantisée ne tient pas. Tout doit avoir un chemin CPU.
2. **Version de Python.** Blender 4.2 et 4.5 LTS embarquent Python **3.11**. Le projet ne doit
   jamais importer ses paquets depuis Blender : le script Blender ne dépend que de `bpy` et de la
   bibliothèque standard, et communique par fichiers JSON. Pour les services, Python **3.12** est
   le choix le plus sûr (roues PyTorch, OpenCV, Shapely, trimesh, Open3D disponibles ; Open3D n'a
   pas de roue 3.13 garantie). Version épinglée par `uv` dans `.python-version`, indépendante du
   3.13 du conteneur.
3. **Pas de Node sur l'hôte.** Deux options : développer le front dans `sandbox-base` (Node 24
   présent) ou installer nvm sur l'hôte. Décision à prendre (voir doc 04).

## 2. Trois architectures candidates

### Option A — Monolithe modulaire Python + TypeScript + Blender en sous-processus

- Backend : un seul paquet Python installable (`openplan`), FastAPI, Pydantic v2, NumPy, Shapely,
  trimesh, OpenCV, PyTorch optionnel. SQLite pour l'index des projets, JSON pour les scènes.
- Front : React + Vite + TypeScript, éditeur 2D en SVG, visualisation 3D en Three.js.
- Blender : appelé en `blender -b --python`, entrée JSON, sortie `.blend`, GLB, PNG.
- Frontières internes garanties par des règles d'import (import-linter), pas par des processus.

Avantages : itération la plus rapide, écosystème CV/géométrie complet, un seul environnement,
tests unitaires simples.
Inconvénients : mémoire Python élevée si les modèles restent chargés ; un seul processus ; la
géométrie en Python pur sera lente sur des scènes très denses (non pertinent avant 0.4).

### Option B — Python (vision) + Rust (noyau géométrique) + TypeScript

- Noyau géométrique (graphe de murs, contraintes, offsets, extrusion) en Rust, exposé à Python
  via PyO3 et au navigateur via wasm-bindgen : le même code tourne côté serveur et côté éditeur.
- Python réduit aux adaptateurs de modèles et à l'orchestration.

Avantages : éditeur 2D réactif sans aller-retour serveur, un seul noyau géométrique testé une
fois, base solide pour une distribution desktop (Tauri).
Inconvénients : deux toolchains dès le départ, Rust absent de l'environnement, temps de
développement du MVP multiplié par deux à trois, débogage FFI. Aucun bénéfice mesurable tant que
les scènes font moins de quelques centaines de murs.

### Option C — Cœur TypeScript local-first, Python comme « worker vision » détachable

- Format de scène, calibration, éditeur 2D, extrusion et export GLB entièrement en TypeScript
  dans le navigateur (ou Tauri). Aucun serveur nécessaire pour éditer.
- Python n'existe que comme service optionnel pour les modèles IA (profondeur, segmentation).
- Blender optionnel, uniquement pour le rendu.

Avantages : confidentialité maximale (tout dans le navigateur), déploiement trivial (site
statique), pas de synchronisation 2D/3D via réseau.
Inconvénients : deux implémentations de la géométrie (TS pour l'éditeur, Python pour la
reconstruction à partir des images) qui divergeront ; écosystème géométrie/CV TypeScript bien
plus pauvre (pas d'équivalent Shapely, trimesh, Open3D) ; les tests géométriques quittent Python
où vivent les fixtures de vision.

## 3. Recommandation MVP (option A, avec deux garde-fous)

Je recommande l'**option A** pour les versions 0.1 à 0.3, avec deux garde-fous empruntés à B et C.

**Pourquoi A.** La priorité absolue est géométrie, calibration, éditabilité, validation. Ce sont
des problèmes d'algorithmique et de modèle de données, pas de performance. Python avec Shapely,
NumPy et trimesh les couvre en quelques centaines de lignes testables, et c'est le même langage
que les adaptateurs de modèles et les fixtures d'évaluation. Un seul `pyproject.toml`, une seule
suite de tests, un seul environnement à installer pour un contributeur. Le vertical slice
(image, mesure, murs, plan, extrusion, GLB) tient en Python pur plus un front léger.

**Pourquoi pas B tout de suite.** Rust n'apporte rien tant que les scènes sont petites, et son
coût d'entrée (toolchain absente, FFI, double build) retarderait le premier résultat de
plusieurs semaines. Le passage en Rust reste possible plus tard **si** le noyau géométrique est
isolé dès le départ.

**Pourquoi pas C.** Faire vivre la géométrie en TypeScript coupe l'éditeur de la reconstruction
qui, elle, ne peut être qu'en Python. La divergence des deux implémentations est le risque de
dette le plus élevé du projet ; le confort d'un site statique ne le compense pas.

**Garde-fou 1 (emprunté à B) : noyau géométrique pur.** `openplan.geom` et `openplan.schemas`
n'importent ni FastAPI, ni torch, ni OpenCV, ni Blender. Ils sont purs NumPy/Shapely et sans
état. Une règle import-linter l'impose en CI. C'est la condition pour un portage Rust ultérieur
ou pour une compilation en WebAssembly via Pyodide, sans réécriture du reste.

**Garde-fou 2 (emprunté à C) : le format de scène est le contrat.** Les modèles Pydantic
génèrent un JSON Schema, dont sont générés les types TypeScript du front. L'éditeur 2D applique
les modifications localement (déplacement de sommet, longueur de mur) sur sa copie typée et
envoie des patches JSON (RFC 6902) au serveur, qui les valide, les rejoue et les historise. Le
front ne réimplémente pas la géométrie : il ne fait que des transformations affines et délègue
les recalculs (aires, offsets d'épaisseur, extrusion) à l'API, qui répond en moins de 50 ms sur
une pièce.

**Compromis acceptés.** Latence réseau locale sur les recalculs (négligeable en local). Blender
externe, donc un binaire de plus à installer ; en contrepartie, un chemin de secours trimesh
produit le GLB sans Blender, avec fonctionnalités réduites (pas de rendu, pas de `.blend`).
Mémoire : les modèles IA sont chargés à la demande et déchargés après l'étape, jamais résidents.

## 4. Structure de dépôt retenue et écarts justifiés

La structure proposée dans le brief séparait `services/` (10 dossiers) et `packages/` (7
dossiers). Je propose de **conserver ces noms comme sous-paquets d'un paquet Python unique**
plutôt que comme paquets distribuables séparés, pour trois raisons :

1. Des paquets top-level nommés `math`, `validation` ou `schemas` **masqueraient la bibliothèque
   standard** (`math`) ou entreraient en collision avec des paquets PyPI. `openplan.geom` ne
   pose pas ce problème.
2. Dix services signifient dix `pyproject.toml`, dix versions, dix chemins d'import à maintenir
   pour un MVP développé par une personne. Les frontières sont imposées par import-linter, ce qui
   donne la même isolation sans le coût de packaging. Extraire un sous-paquet en service
   séparé plus tard reste un déplacement de dossier.
3. Les tests d'intégration du pipeline traversent toutes les étapes ; un seul paquet permet de
   les écrire sans installer le monde.

```text
openplan-studio/
├── apps/
│   └── web/                     React + Vite + TypeScript (desktop/ et cli/ : plus tard ;
│                                la CLI vit dans openplan/cli pour l'instant)
├── openplan/                    paquet Python unique (modular monolith)
│   ├── schemas/                 = packages/schemas      Pydantic v2, source de vérité du format
│   ├── scene/                   = packages/scene_format chargement, migration, historique, store
│   ├── geom/                    = packages/math         primitives 2D/3D pures (NumPy, Shapely)
│   ├── adapters/                = packages/model_adapters Protocols + implémentations de modèles
│   ├── validation/              = packages/validation   moteur de contraintes
│   ├── pipeline/                = services/*            une étape par module, même interface
│   │   ├── ingestion.py, quality.py, detection.py, calibration.py,
│   │   ├── floorplan.py, scene3d.py, interior_design.py (0.3), rendering.py, export.py
│   ├── api/                     = services/api          FastAPI, écoute 127.0.0.1 uniquement
│   └── cli/                     = apps/cli
├── blender/
│   ├── scripts/                 bpy + stdlib uniquement, licence propre (voir doc 04)
│   ├── templates/  materials/  assets/
├── models/                      registry.yaml + poids téléchargés (gitignorés)
├── tests/
│   ├── unit/  integration/  geometry/  fixtures/
├── docs/
│   ├── architecture/  models/  licenses/  privacy/  contributing/  evaluation/
├── pyproject.toml  .python-version  Makefile  docker-compose.yml
├── README.md  LICENSE  CONTRIBUTING.md
```

`packages/ui` et `packages/test_fixtures` du brief deviennent respectivement `apps/web/src/ui`
et `tests/fixtures` : un paquet UI séparé n'a de sens qu'avec plusieurs applications front.

Règles d'import (import-linter, couches de bas en haut) :

```text
openplan.schemas  <  openplan.geom  <  openplan.scene  <  openplan.validation
                  <  openplan.adapters  <  openplan.pipeline  <  openplan.api | openplan.cli
```

`schemas` et `geom` n'ont aucune dépendance interne et aucune dépendance lourde (pas de torch,
cv2, fastapi). `adapters` peut importer torch/cv2 **à l'intérieur des fonctions**, jamais au
niveau module, pour que l'API démarre sans modèle installé.
