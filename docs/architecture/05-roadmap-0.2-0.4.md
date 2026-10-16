# 05 — Roadmap 0.2 → 0.4

Statut : **proposition**, écrite le 23 septembre 2026 après la livraison de la 0.1 (incréments
0 à 7). Chaque version est découpée en incréments livrables, testés, mergeables, dans l'ordre
des priorités du brief : géométrie, calibration, éditabilité, validation, interopérabilité,
confidentialité, IA générative, rendu. Les critères d'acceptation sont mesurables ou ne sont
pas écrits.

## 0. Point de départ (0.1, livré)

Photo → proposition heuristique de pièce rectangulaire → édition 2D → calibration → 3D
(trimesh, Blender optionnel) → exports SVG/JSON/GLB. Assistant IA opt-in (rôle Perception)
par API compatible OpenAI, sans modification de scène. 168 tests, une fixture synthétique.

Ce qui manque le plus, vu du produit : la détection réelle des ouvertures, la profondeur, la
pièce non rectangulaire, le multi-photos, le PDF, et une évaluation sur photos réelles.

## 1. Version 0.2 — scène 3D fiable et pièces réelles

Objectif : qu'une pièce réelle, photographiée deux ou trois fois, donne un plan corrigible avec
ses ouvertures, et une scène 3D exportable, sans IA distante.

| # | Incrément | Contenu | Fichiers principaux | Dépendances | Critère d'acceptation | Tests |
|---|---|---|---|---|---|---|
| 2.1 | Photos réelles et protocole | Dix pièces réelles mesurées au mètre (consentement, sans personne), vérité terrain `mesures.txt`, script de comparaison | `data/eval/*` (hors git), `docs/evaluation/protocol.md`, `openplan evaluate` (`pipeline/evaluate.py`) | appareil photo, mètre | Erreur médiane des longueurs de murs publiée, quelle qu'elle soit ; aucune métrique sans mesure | **script livré le 24 sept. 2026 (2.1a)** ; photos à fournir par augus |
| 2.2 | Profondeur relative locale | `DepthEstimator` avec Depth Anything V2 Small (Apache-2.0, CPU/CUDA), `openplan models pull`, cache local, carte de profondeur comme artefact | `adapters/neural/depth_anything.py`, `adapters/registry.py`, `cli` (`models pull/list`) | extra `neural` (torch, timm) ; ≈ 1,2 Go ; poids téléchargés explicitement | Sur la fixture rendue : corrélation de Spearman ≥ 0,9 entre profondeur estimée et profondeur Blender (rendue en passe Z) ; temps < 10 s CPU 1280 px | `tests/integration/test_depth.py`, ignoré sans poids — **livré** (8a, 23 sept. : variante métrique intérieure) ; **critère mesuré le 25 sept. 2026** : Spearman 0,998, échelle ×1,50 sur la pièce en L rendue (protocole §3 septies) |
| 2.3 | Plan depuis la profondeur | Sol = plan dominant sous l'horizon ; murs = plans verticaux ; polygone de pièce non rectangulaire (L, biais) ; fusion avec les points de fuite | `pipeline/floorplan.py`, `geom/planes.py` (RANSAC de plans NumPy) | 2.2 | Fixture « pièce en L » : IoU du polygone ≥ 0,7 après calibration sur un mur | fixture L rendue par Blender — **2.3a livré le 25 sept. 2026** (`pipeline/depthplan.py` : plans → murs → polygone rectiligne avec renfoncements, `run_depthplan`, lignes « profondeur plan » dans `openplan evaluate` ; pièce en L synthétique : IoU 0,97). **2.3b livré le 25 sept. 2026** : route + bouton « Plan depuis la profondeur », passe Z Blender, fixture en L rendue + Depth Anything : IoU 0,77, Spearman 0,998 (critère 2.2 mesuré, échelle ×1,5). **Reste** : murs obliques (biais), fusion avec les points de fuite |
| 2.4 | Ouvertures | `RoomElementDetector` portes/fenêtres à licence permissive (candidats : DETR/RT-DETR Apache-2.0 fine-tuné, ou SAM 2 guidé par les rectangles verticaux des familles de points de fuite) ; projection sur le mur hôte → `Opening` `observed` | `adapters/neural/openings.py` ou `adapters/heuristic/openings.py`, `pipeline/detection.py` | licence vérifiée avant tout ; AGPL exclu | Fixture : porte et fenêtre trouvées, largeur à ±15 % ; photos réelles : rappel publié | tests sur fixture + réel — **2.4a livré le 25 sept. 2026** : OWLv2 base (Apache-2.0) zéro-shot, porte et fenêtre trouvées sur des rendus habillés (IoU 0,88 et 0,84) ; **2.4b livré le 25 sept. 2026** : projection par la caméra de la photo sur la face du mur hôte → `Opening observed` corrigeable, filtres géométriques expliqués un par un, route + bouton « Portes et fenêtres », `fenetre_largeur`/`porte_largeur` dans `evaluate` ; **critère fixture atteint** : largeur +8 %, position −5 cm, appui −4 cm (protocole §3 octies). **Reste** : rappel sur photos réelles (`data/eval`), largeur hors chambranle |
| 2.5 | Multi-photos d'une même pièce | Plusieurs images par niveau ; caméra estimée par image ; contradictions signalées (`Violation` `inconsistent_views`) ; l'utilisateur choisit ; pas de reconstruction dense | `schemas` (Camera existe déjà), `pipeline/multiview.py`, `validation/consistency.py`, front : sélecteur de photo | 2.3 | Deux vues de la fixture donnent le même mur de face à ±10 % ; une vue incohérente déclenche l'avertissement | fixture multi-photos — **2.5a livré le 24 sept. 2026** : une photo de plus sur un niveau muni de murs ajoute sa caméra « Photo n », confirme un mur inféré vu avec son vis-à-vis, consigne ce qu'elle observe et déclenche `inconsistent_views` ; `openplan evaluate` accepte `photo2.jpg`… ; mur de face à −2 % sur la fixture, vue incohérente signalée. **2.5b livré le 24 sept. 2026** : caméras dans le plan, contradiction en clair (murs, photo, écart en cm) avec « Garder cette photo » (le mur le moins sûr suit) / « Écarter cette photo » (conservée, sans effet, réintégrable). **2.5c livré le 25 sept. 2026** : « Tourner la vue » (route `views/{id}/rotate`, caméra replacée et visée tournée d'un quart de tour, murs intacts, cohérence recontrôlée). **2.5 terminé** ; reconstruction dense hors périmètre |
| 2.6 | Exports plan | PDF (cotations, échelle, légende des sources, mention « estimé »), DXF simple (lignes de murs, calques par source) | `pipeline/export.py`, dépendances : `reportlab` (BSD), `ezdxf` (MIT) | — | PDF ouvert par un lecteur standard, échelle exacte à 1 % ; DXF relu par `ezdxf` avec le bon nombre de lignes | tests unitaires — **livré le 24 sept. 2026 (8f, 8g)** |
| 2.7 | Robustesse produit | Index SQLite des projets (nom, date, vignette), jeton local si `--host` ≠ boucle locale, `docker-compose.yml` (api + web), navigation par photos dans l'UI, vérification du front dans un navigateur (Playwright) | `scene/store.py`, `api/app.py`, `docker-compose.yml`, `apps/web/tests/` | — | `docker compose up` sert l'UI ; un test Playwright fait le scénario de la démo | CI locale `make check` étendue — **fait** : Playwright (23 sept.), jeton hors boucle locale (24 sept.), `make start` / `serve --web` un seul port (24 sept.), index SQLite + vignettes (2.7a, 25 sept. 2026), navigation par photos (2.7b, 25 sept. 2026 : vue précédente/suivante, « Voir dans le plan », champ de vision), `Dockerfile` + `docker-compose.yml` en réseau hôte (2.7c, 25 sept. 2026, image légère sans torch ni Blender). **2.7 terminé** |

Risques 0.2 : licence du détecteur d'ouvertures (le plus incertain) ; profondeur relative sans
échelle (la calibration reste obligatoire, il faut le dire dans l'UI) ; temps CPU sur machines
sans GPU (prévoir le mode « heuristique seulement »).

## 2. Version 0.3 — aménagement assisté

Objectif : « transforme ce grenier en chambre avec un bureau » donne trois dispositions
valides, comparables, corrigibles, rendues.

| # | Incrément | Contenu | Fichiers | Dépendances | Critère | Tests |
|---|---|---|---|---|---|---|
| 3.1 | Catalogue de démonstration | Format d'asset (brief §16), dix meubles paramétriques créés par le projet (boîtes cotées, CC0), licence obligatoire, `unknown` refusé | `openplan/catalog/`, `assets/catalog.yaml`, `docs/licenses/` | — | Chaque asset a licence, dimensions, ancrages ; un asset `unknown` est rejeté par un test | unitaires — **livré le 25 sept. 2026** : onze boîtes CC0 (dont le rangement bas sous pente demandé par le PO), `add_furniture`, plan + 3D + inspecteur, `GET /api/catalog` ; ordre PO ensuite : 3.3 → 3.2 → 3.4 |
| 3.2 | Contraintes | `ConstraintKind` déjà défini : implémenter collisions (Shapely), circulation (largeur de passage ≥ 0,8 m par graphe de visibilité), accès portes/fenêtres/radiateurs, hauteur sous pente | `validation/layout.py` | 3.1 | Rapport avec violations compréhensibles ; jeu de cas vrai/faux | unitaires géométriques — **livré le 25 sept. 2026** : collisions, murs, débattement de porte (dures), fenêtre, dégagements (radiateur, `keep_clear`, catalogue), passage 0,80 m (souples), modulables par `Constraint` ; cas vrai / faux par règle |
| 3.3 | Intention (LLM) | Rôle **Intention** : texte → `LayoutBrief` structuré (pièce, meubles requis, à conserver, à éviter, style, budget) validé par Pydantic ; refus si le modèle invente une dimension ; local ou distant via la passerelle existante | `adapters/remote/intention.py`, `schemas/layout.py` | passerelle 0.1 | Dix briefs de test → JSON valides ; un brief piégé (« la pièce fait 8 m ») ne produit aucune dimension | unitaires avec modèle simulé, essai réel documenté — **livré le 25 sept. 2026** : `schemas/layout.py`, `adapters/remote/intention.py`, `pipeline/intention.py`, routes aperçu + brief, panneau IA ; essai réel `qwen2.5vl:3b` : 10/10 valides, piège tenu (protocole §3 nonies) |
| 3.4 | Générateur de dispositions | Moteur géométrique (pas l'IA) : placement par heuristiques et recherche locale sous contraintes, N variantes scorées | `openplan/layout/` | 3.1, 3.2 | Sur la fixture : trois variantes valides pour « lit double + bureau + rangements » en < 5 s | unitaires + propriété (aucune collision) — **livré le 27 sept. 2026** : `pipeline/layouts.py` (glouton + amélioration locale, graine, variantes distinctes, score et phrase de synthèse), routes `POST …/layouts` et `…/layouts/apply`, fantôme sur le plan, « Appliquer cette variante » ; trois variantes valides en moins d'une seconde sur la chambre de la fabrique |
| 3.5 | Critique (LLM, optionnel) | Rôle **Critique** : le JSON d'une variante → remarques et suggestions structurées ; jamais de modification directe | `adapters/remote/critique.py` | 3.3 | Sortie validée, marquée `generated` | modèle simulé |
| 3.6 | UI brief → variantes | Étapes brief, style, variantes, validation, édition ; comparaison côte à côte ; meubles déplaçables dans le plan et la 3D | `apps/web/src/layout/` | 3.4 | Scénario Playwright : brief → 3 variantes → édition → export | e2e |
| 3.7 | Rendu Blender des variantes | Matériaux du catalogue, éclairage, vignette par variante, rendu final optionnel ; toujours la géométrie validée | `blender/scripts/`, `pipeline/rendering.py` | Blender | Une vignette par variante en < 60 s CPU | test Blender marqué |

Risques 0.3 : qualité perçue des dispositions (heuristiques simples au début, à assumer) ;
tentation de laisser le LLM placer les meubles (interdit par le brief : il propose des
contraintes, pas des coordonnées) ; assets externes aux licences floues.

## 3. Version 0.4 — reconstruction avancée

| # | Incrément | Contenu | Fichiers | Dépendances | Critère | Tests |
|---|---|---|---|---|---|---|
| 4.1 | Pentes et combles | `RoofPlane` dans le bake (intersection mur/pente), hauteur utile sous pente, contrainte `max_height_under_slope` | `pipeline/scene3d.py`, `blender/scripts/`, `validation/layout.py` | — | Fixture « grenier » : volume 3D exact à 1 % ; zone < 1,8 m affichée | géométrie + Blender — **livré le 24 sept. 2026 (8c, 4.1c)** : bake en polyèdres, murs découpés, ligne 1,80 m, surface utile par pièce, contrainte `max_height_under_slope` |
| 4.2 | Escaliers et niveaux | `Stairs` dans le bake, plusieurs niveaux reliés, trémie | `schemas` (existe), `scene3d`, UI niveaux | — | Deux niveaux exportés en un GLB, escalier percé | géométrie |
| 4.3 | Plusieurs pièces | Portes partagées entre pièces, alignement de pièces adjacentes, plan d'appartement | `geom/wallgraph.py` (graphe multi-pièces existe), `pipeline/assembly.py` | 2.5 | Deux pièces reliées par une porte : cohérence des murs partagés | géométrie |
| 4.4 | Reconstruction multi-vues | `MultiViewReconstructor` (candidats à licence permissive : VGGT/Mickey selon licence réelle au moment du choix ; DUSt3R/MASt3R exclus, non commerciaux), Open3D optionnel pour le nuage | `adapters/neural/mvs.py` | GPU conseillé | Fixture multi-photos : erreur de position des murs ≤ 10 % sans mesure autre que l'échelle | intégration, ignoré sans poids |
| 4.5 | Plan photographié | Lecture d'un plan papier : redressement, cotes OCR (Tesseract, Apache-2.0), calibration par cote | `pipeline/plan_photo.py` | tesseract | Fixture « plan photographié » : deux cotes lues, échelle à 2 % | intégration |

## 4. Dette technique connue (à traiter au fil des versions)

| Dette | Impact | Quand |
|---|---|---|
| ~~Le script Blender ignore les trous des polygones de sol (pièce avec îlot)~~ fait le 24 sept. 2026 : une dalle percée est convertie en polyèdre par trimesh avant Blender | — | — |
| ~~Le front n'a jamais été vérifié dans un navigateur~~ fait le 23 sept. 2026 (Playwright, `make e2e`) | — | — |
| ~~Pas d'authentification sur l'API si exposée hors boucle locale~~ fait le 24 sept. 2026 : jeton Bearer exigé hors boucle locale, généré par `openplan serve` | — | — |
| ~~L'export JSON contient tout l'historique et les chemins d'images~~ fait le 24 sept. 2026 : export « JSON à partager » (`?share=true`, `--share`) sans historique ni chemins ; la sauvegarde complète est signalée comme telle | — | — |
| ~~Seuil de netteté empirique (40), non validé~~ fait le 24 sept. 2026 : rapport de re-floutage indépendant de la texture, seuil 0,30 mesuré sur photos réelles nettes et floutées (protocole §3 quinquies) ; reste à confirmer sur de vraies photos floues | — | — |
| Une seule fixture synthétique ; mur latéral sous-estimé de 10 % | pas de garantie sur photos réelles | 0.2 (2.1) |
| ~~Roulis ignoré dans la caméra de fixture Blender~~ fait le 24 sept. 2026 : rendu à la volée avec roulis 5°, retrouvé à 5,00° avec le bon signe (`tests/integration/test_fixture_roll.py`) | — | — |
| ~~Chevauchement d'ouvertures absorbé silencieusement (`wall_segments`)~~ fait le 24 sept. 2026 : violation `openings_overlap` (sévérité moyenne) dans le rapport de validation | — | — |
| ~~Assistant IA testé uniquement avec un modèle simulé~~ essais réels Ollama (23 sept.) et Gemini (24 sept.), protocole §3 bis | — | — |
| `GET /api/ai` expose modèle et hôte, pas la clé ; pas de rotation de clé | acceptable en local | 0.2 |
| Perception IA : `MAX_SCORE` 0,6 arbitraire | confiance non calibrée | 0.3 après mesures |

## 5. Ce qui ne sera pas fait

Génération d'images photoréalistes comme source géométrique, application mobile, dépendance à
un cloud, modèle génératif lourd embarqué, système multi-agent. Conforme au brief §23.
