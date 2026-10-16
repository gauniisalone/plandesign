# 02 — Interfaces entre modules

Statut : **proposition, en attente de validation**.

## 1. Étape de pipeline : interface commune

Chaque étape du pipeline (ingestion, qualité, détection, calibration, plan, scène 3D, export…)
implémente la même interface. Elle est pure du point de vue du projet : elle reçoit un `Project`
et un contexte, renvoie un `StepResult` contenant un **patch** à appliquer, jamais un projet muté.
C'est ce qui rend chaque étape testable seule et historisable.

```python
from typing import Protocol, TypeVar, Generic
from pathlib import Path

class Artifact(BaseModel):
    """Fichier inspectable produit par une étape (masque, carte de profondeur, SVG, GLB…)."""
    name: str
    path: Path                       # sous <projet>/runs/<run_id>/<step>/
    media_type: str
    description: str = ""

class Assumption(BaseModel):
    text: str                        # "Pièce supposée rectangulaire (hypothèse Manhattan)"
    impact: Literal["low", "medium", "high"]

class StepResult(BaseModel):
    step: str
    patch: list[dict]                # JSON Patch à appliquer au Project
    confidence: Confidence
    assumptions: list[Assumption] = []
    warnings: list[str] = []
    artifacts: list[Artifact] = []
    duration_s: float
    model_ids: list[str] = []        # identifiants du registre de modèles utilisés

class RunContext(BaseModel):
    run_id: UUID
    project_dir: Path
    artifacts_dir: Path
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    settings: dict[str, object] = {}

class PipelineStep(Protocol):
    name: str
    def run(self, project: Project, ctx: RunContext) -> StepResult: ...
```

Le runner (`openplan.pipeline.runner`) applique le patch, vérifie les invariants
(`openplan.validation`), refuse tout patch qui abaisserait une source de priorité supérieure,
ajoute un `HistoryEntry` avec `actor="pipeline:<step>"` et persiste.

## 2. Adaptateurs de modèles (`openplan.adapters.protocols`)

Les cinq protocoles du brief, complétés par les types d'entrée/sortie. Aucun de ces protocoles
ne mentionne torch, ONNX ou un modèle précis.

```python
import numpy as np
from numpy.typing import NDArray

class ImageArray(BaseModel):
    """Image RGB uint8 HxWx3 + identifiant. Jamais un chemin : l'adaptateur ne lit pas le disque."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    image_id: UUID
    pixels: NDArray[np.uint8]

class DepthMap(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    depth: NDArray[np.float32]       # HxW ; relative (disparité normalisée) ou métrique
    metric: bool                     # False = à l'échelle près
    confidence: NDArray[np.float32] | None = None
    model_id: str

class DetectedElement(BaseModel):
    """Élément architectural détecté en 2D image."""
    category: Literal["wall", "floor", "ceiling", "door", "window", "radiator", "beam",
                      "stairs", "outlet", "fixture", "furniture"]
    bbox_px: tuple[int, int, int, int]        # x0, y0, x1, y1
    polygon_px: list[tuple[float, float]] | None = None
    score: Confidence
    label: str | None = None                  # sous-catégorie libre du modèle
    model_id: str

class SegmentationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    labels: NDArray[np.int32]                 # HxW, index dans `classes`
    classes: list[str]
    scores: NDArray[np.float32] | None = None
    model_id: str

class CameraParameters(BaseModel):
    focal_px: float
    principal_point: tuple[float, float]
    pitch: Radians                            # inclinaison
    roll: Radians
    yaw_hint: Radians | None = None           # relatif à la direction dominante des murs
    height_m: float | None = None             # hauteur caméra si estimable
    vanishing_points_px: list[tuple[float, float]] = []
    confidence: Confidence
    model_id: str

class ReconstructionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    points: NDArray[np.float32]               # Nx3
    colors: NDArray[np.uint8] | None = None
    cameras: list[CameraParameters]
    metric: bool
    model_id: str

class DepthEstimator(Protocol):
    model_id: str
    def predict(self, image: ImageArray) -> DepthMap: ...

class RoomElementDetector(Protocol):
    model_id: str
    def detect(self, image: ImageArray) -> list[DetectedElement]: ...

class SegmentationModel(Protocol):
    model_id: str
    def segment(self, image: ImageArray) -> SegmentationResult: ...

class CameraEstimator(Protocol):
    model_id: str
    def estimate(self, image: ImageArray) -> CameraParameters: ...

class MultiViewReconstructor(Protocol):
    model_id: str
    def reconstruct(self, images: list[ImageArray]) -> ReconstructionResult: ...
```

### Registre d'adaptateurs

```python
# openplan/adapters/registry.py
def get_depth_estimator(name: str | None = None, device: str = "cpu") -> DepthEstimator: ...
# name=None → valeur de config ; les imports torch/cv2 sont faits dans la fonction,
# l'échec (paquet absent, poids absents) lève AdapterUnavailable avec l'instruction d'installation.
```

Chaque implémentation est documentée dans `docs/models/model_registry.md` (nom, version, licence,
poids, mémoire, limites, usage commercial, alternative locale) et déclarée dans
`models/registry.yaml`. Le téléchargement de poids est **une action explicite** (`openplan models
pull <id>`), jamais implicite au premier appel.

Implémentations prévues pour le premier slice (sans réseau de neurones) :

- `ManualRoomDetector` : renvoie ce que l'utilisateur a tracé (source `user_defined`).
- `HeuristicBoxDetector` : lignes (OpenCV LSD) → points de fuite (RANSAC) → hypothèse Manhattan →
  proposition d'une pièce rectangulaire aux proportions estimées (source `estimated`, confiance
  ≤ 0,6). Aucun poids à télécharger, tourne sur CPU.
- `ExifCameraEstimator` : focale depuis l'EXIF si présent, sinon 0,8 × largeur en pixels avec
  confiance basse.

Implémentations prévues ensuite (0.1.x / 0.2) : Depth Anything V2 Small/Base (Apache-2.0),
SAM 2 (Apache-2.0) pour la segmentation guidée, un détecteur portes/fenêtres à licence permissive
(voir doc 04 : les modèles Ultralytics sont AGPL, à éviter).

## 3. Modules géométriques et métier

```python
# openplan/geom/wallgraph.py
class WallGraph:
    """Vue calculée d'un Level : sommets partagés, murs, boucles de pièces. Sans état persistant."""
    @classmethod
    def from_level(cls, level: Level) -> "WallGraph": ...
    def wall_length(self, wall_id: UUID) -> float: ...
    def wall_polygon(self, wall_id: UUID) -> Polygon: ...          # emprise avec épaisseur (Shapely)
    def room_polygon(self, room_id: UUID) -> Polygon: ...          # côté intérieur des murs
    def room_area(self, room_id: UUID) -> float: ...
    def closed_loops(self) -> list[list[UUID]]: ...                # détection automatique des pièces
    def move_vertex(self, vertex_id: UUID, to: Point2) -> list[dict]: ...   # renvoie un patch
    def set_wall_length(self, wall_id: UUID, length: float, anchor: Literal["start", "end", "center"]) -> list[dict]: ...

# openplan/geom/scale.py
def solve_scale(level: Level, measurements: list[Measurement]) -> Calibration: ...
# 1 mesure → facteur direct ; n mesures → moindres carrés sur log(scale), résidus par mesure,
# marquage des incohérences (résidu > 3 × tolérance).

# openplan/pipeline/calibration.py
class CalibrationStep:                     # applique solve_scale, met source=measured sur les murs mesurés
    name = "calibration"
    def run(self, project: Project, ctx: RunContext) -> StepResult: ...

# openplan/pipeline/floorplan.py
class FloorplanStep:                       # DetectedElement[] + CameraParameters → sommets/murs/ouvertures (patch)
    name = "floorplan"
    def run(self, project: Project, ctx: RunContext) -> StepResult: ...

# openplan/pipeline/scene3d.py
def build_mesh(level: Level, graph: WallGraph) -> trimesh.Scene: ...
# murs extrudés (emprise × hauteur) moins les ouvertures (booléen 2D avant extrusion, pas de CSG 3D),
# sol et plafond = polygone de pièce ; noms des nœuds = UUID des éléments (traçabilité dans le GLB).

# openplan/pipeline/export.py
class Exporter(Protocol):
    format: Literal["json", "glb", "svg", "pdf", "obj", "dxf"]
    def export(self, project: Project, level_id: UUID, out: Path) -> Artifact: ...

# openplan/validation/engine.py
class Violation(BaseModel):
    type: str; severity: Literal["low", "medium", "high"]; element_ids: list[UUID]; message: str
class ValidationReport(BaseModel):
    score: Confidence; valid: bool; violations: list[Violation]; warnings: list[str]; suggestions: list[str]
def validate_geometry(project: Project, level_id: UUID) -> ValidationReport: ...   # 0.1
def validate_layout(project: Project, level_id: UUID) -> ValidationReport: ...     # 0.3
```

## 4. Pont Blender

Contrat de ligne de commande, indépendant de la version de Python du projet :

```bash
blender -b --python blender/scripts/build_scene.py -- \
    --scene  <projet>/scene.json \
    --level  <level_id> \
    --out-blend <run>/scene.blend \
    --out-glb   <run>/scene.glb \
    --preview   <run>/preview.png \
    --report    <run>/blender_report.json
```

- `build_scene.py` n'importe que `bpy`, `bmesh`, `json`, `argparse`, `pathlib`, `math`.
  Il relit le JSON du format de scène (doc 01) et reconstruit les primitives : un objet par mur,
  modificateur booléen par ouverture, sol et plafond par pièce, caméra et lumières.
- `blender_report.json` : version de Blender, durée, objets créés, avertissements, hachage du
  JSON d'entrée. Le runner le convertit en `StepResult`.
- Chemin de secours sans Blender : `openplan.pipeline.scene3d.build_mesh` (trimesh) produit le
  GLB de visualisation. `rendering` n'a pas de secours : sans Blender, pas de rendu.

## 5. API HTTP (FastAPI, `127.0.0.1` uniquement par défaut)

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/projects` | créer (nom) → `Project` |
| `GET` / `DELETE` | `/projects/{id}` | lire / **supprimer entièrement** (images, runs, index) |
| `POST` | `/projects/{id}/images` | importer un fichier local (multipart), EXIF selon `privacy` |
| `GET` | `/projects/{id}/images/{img}/quality` | rapport de contrôle qualité |
| `POST` | `/projects/{id}/measurements` | ajouter une mesure |
| `POST` | `/projects/{id}/runs` | lancer une liste d'étapes `["quality","detection","calibration","floorplan"]` |
| `GET` | `/projects/{id}/runs/{run}` | état, `StepResult[]`, artefacts |
| `PATCH` | `/projects/{id}/scene` | JSON Patch utilisateur → validation, historique, réponse = projet + dérivés |
| `POST` | `/projects/{id}/undo`, `/redo` | historique |
| `GET` | `/projects/{id}/levels/{lvl}/derived` | longueurs, aires, polygones (pour l'UI) |
| `POST` | `/projects/{id}/exports` | `{format, level_id}` → artefact téléchargeable |
| `GET` | `/models` | registre, installés ou non, licence, mémoire |

Les types TypeScript du front sont générés depuis le JSON Schema de `Project`, `StepResult`,
`ValidationReport` (`openplan schemas export` → `apps/web/src/api/types.gen.ts`). Le front ne
déclare aucun type de scène à la main.

## 6. Front (`apps/web`)

- `state/sceneStore.ts` : copie typée du `Project`, application optimiste des patches, envoi au
  serveur, remplacement par la réponse validée.
- `editor2d/` : SVG, un calque par famille (fond photo, murs, ouvertures, cotations, incertitude).
  Style visuel par `source` : trait plein (`measured`, `user_defined`), pointillé (`estimated`,
  `inferred`), teinte différente (`generated`), halo proportionnel à `uncertainty.sigma`.
- `viewer3d/` : Three.js + `GLTFLoader`, sélection par nom de nœud = UUID, surbrillance
  synchronisée avec l'éditeur 2D.
- `calibration/` : pose de deux points sur l'image ou sélection d'un mur, saisie de la valeur,
  choix de l'unité d'affichage.
