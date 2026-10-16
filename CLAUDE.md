# Projet : OpenPlan Studio

Tu es Claude Code, agissant comme :
- Principal Software Engineer ;
- Computer Vision Engineer ;
- 3D Graphics Engineer ;
- Product Architect ;
- spécialiste des logiciels open-source et auto-hébergeables.

Tu dois m'aider à construire un logiciel open-source, gratuit et respectueux de la vie privée permettant de transformer des photos d'intérieur en plan 2D, modèle 3D éditable et propositions d'aménagement générées par IA.

Le projet s'appelle provisoirement « OpenPlan Studio ».

> État du projet et décisions prises : voir `docs/architecture/` (00 à 05) et `docs/STATUS.md`
> (avancement, tâches). Les décisions D1 à D7 de `docs/architecture/04-risques-et-decisions.md`
> ont été validées le 23 septembre 2026 ; la 0.1 (incréments 0 à 7) est livrée ; la roadmap
> 0.2 → 0.4 (`05-roadmap-0.2-0.4.md`) est une proposition.
> Environnement de développement : `docs/contributing/setup.md`.

---

# 1. Vision du produit

L'utilisateur doit pouvoir photographier :

- une chambre ;
- un salon ;
- une cuisine ;
- un grenier ;
- des combles ;
- un appartement ;
- une maison complète ;
- plusieurs pièces reliées entre elles.

À partir de ces photos, le logiciel doit progressivement permettre de :

1. comprendre la géométrie de l'espace ;
2. détecter les murs, portes, fenêtres, sols et plafonds ;
3. produire un plan 2D ;
4. calibrer le plan à partir d'une ou plusieurs mesures connues ;
5. générer une scène 3D paramétrique et éditable ;
6. détecter les éléments fixes et les meubles ;
7. proposer plusieurs aménagements ;
8. respecter les contraintes physiques et fonctionnelles ;
9. produire des rendus réalistes ;
10. exporter les résultats dans des formats ouverts.

Le produit doit être une aide à la conception intérieure. Il ne doit jamais présenter une reconstruction estimée comme un relevé architectural certifié.

---

# 2. Principes fondamentaux

Respecte les principes suivants :

- Le projet doit être open-source.
- Le logiciel doit être gratuit pour l'utilisateur final.
- Le logiciel doit pouvoir fonctionner en local.
- Les photos doivent rester sur la machine de l'utilisateur par défaut.
- Aucune donnée ne doit être envoyée à un service distant sans consentement explicite.
- Les modèles IA doivent être interchangeables.
- Les dépendances propriétaires doivent être optionnelles.
- Les formats de données doivent être documentés.
- L'utilisateur doit pouvoir corriger manuellement les résultats.
- L'IA doit proposer, jamais imposer.
- Toute mesure doit être identifiée comme mesurée, estimée ou déduite.
- Toute incertitude importante doit être visible.
- Les licences des modèles, assets 3D, textures et bibliothèques doivent être vérifiées.
- Aucun asset dont la licence est inconnue ne doit être inclus par défaut.
- Le logiciel doit privilégier l'interopérabilité et éviter le verrouillage propriétaire.
- Le système doit fonctionner sans cloud dans la mesure du possible.
- Les utilisateurs doivent pouvoir supprimer entièrement leurs projets et leurs images.

---

# 3. Limites à prendre en compte

Une photo unique ne permet pas toujours de connaître avec précision :

- la profondeur réelle ;
- les dimensions métriques ;
- les parties cachées par des meubles ;
- la géométrie derrière la caméra ;
- les hauteurs exactes ;
- les pentes de toiture ;
- la position exacte des ouvertures ;
- la continuité entre plusieurs pièces.

Le logiciel doit donc :

- demander une ou plusieurs mesures de référence ;
- suggérer de prendre des photos supplémentaires si nécessaire ;
- détecter les zones ambiguës ;
- calculer un niveau de confiance ;
- distinguer les éléments observés des éléments inférés ;
- signaler les contradictions entre plusieurs images ;
- permettre une correction manuelle ;
- ne jamais inventer silencieusement une dimension ;
- conserver la provenance de chaque donnée.

---

# 4. Utilisateurs cibles

Le produit doit pouvoir être utilisé par :

- des particuliers ;
- des personnes qui rénovent leur logement ;
- des propriétaires ;
- des architectes d'intérieur ;
- des décorateurs ;
- des artisans ;
- des étudiants en architecture ;
- des professionnels qui souhaitent préparer une première proposition ;
- des utilisateurs intéressés par la visualisation 3D.

Le premier MVP doit cibler les particuliers capables de fournir :

- une photo ou plusieurs photos ;
- au moins une mesure connue ;
- éventuellement un croquis ;
- une description de leur besoin.

---

# 5. Cas d'usage principaux

Le logiciel doit progressivement supporter les demandes suivantes :

- « Transforme cette photo en plan 2D. »
- « Utilise cette longueur de mur de 3,10 m pour calibrer la pièce. »
- « Transforme ce grenier en chambre avec un bureau. »
- « Ajoute des rangements sous la pente. »
- « Conserve les fenêtres, la porte et les poutres. »
- « Ne place aucun meuble devant le radiateur. »
- « Propose trois styles : japandi, contemporain chaleureux et industriel. »
- « Optimise cette petite pièce sans bloquer la circulation. »
- « Prévois un lit double, un bureau et des rangements. »
- « Compare les variantes dans un rendu réaliste. »
- « Exporte le plan en PDF et la scène en GLB. »

---

# 6. Stack technique retenue

Décision validée (voir `docs/architecture/00-etat-des-lieux-et-architecture.md`) :
option A, monolithe modulaire Python + TypeScript + Blender en sous-processus.

## Backend et IA

- Python 3.12 (épinglé par uv) ;
- FastAPI ;
- Pydantic v2 ;
- PyTorch (optionnel, adaptateurs) ;
- OpenCV ;
- NumPy ;
- Shapely ;
- trimesh ;
- Open3D (0.4, optionnel) ;
- SQLite pour l'index des projets, JSON pour les scènes ;
- Blender en mode headless pour le `.blend`, les aperçus et le rendu (optionnel).

## Frontend

- React ;
- TypeScript ;
- Vite ;
- CSS modulaire ;
- SVG pour l'éditeur 2D ;
- Three.js pour la visualisation 3D.

## Infrastructure

- uv, ruff, mypy strict, pytest, import-linter ;
- Docker Compose (à partir de l'incrément 3) ;
- exécution CPU avec fonctionnalités réduites ;
- accélération CUDA lorsque disponible (6 Go de VRAM sur la machine de référence).

---

# 7. Architecture du dépôt

Structure validée (D1) : un paquet Python unique `openplan` avec des sous-paquets plutôt que
des services séparés. Justification dans `docs/architecture/00`, §4.

```text
openplan-studio/
├── apps/web/                React + Vite + TypeScript
├── openplan/
│   ├── schemas/             Pydantic v2, source de vérité du format de scène
│   ├── scene/               store, historique (JSON Patch), migrations
│   ├── geom/                noyau géométrique pur (NumPy, Shapely)
│   ├── validation/          invariants et moteur de contraintes
│   ├── adapters/            protocoles + implémentations de modèles
│   ├── pipeline/            une étape par module, même interface
│   ├── api/                 FastAPI, 127.0.0.1
│   └── cli/
├── blender/scripts/         bpy + stdlib uniquement, GPL-3.0-or-later
├── models/                  registry.yaml ; poids jamais commités
├── tests/                   unit / geometry / integration / fixtures
├── docs/                    architecture / models / licenses / privacy / contributing / evaluation
├── pyproject.toml  Makefile  README.md  LICENSE  CONTRIBUTING.md
```

Couches (import-linter) : `cli > api > pipeline > adapters > validation > scene > geom > schemas`.
`schemas` et `geom` n'importent ni torch, ni cv2, ni fastapi, ni bpy, ni trimesh.

---

# 8. Format de scène interne

Spécification complète : `docs/architecture/01-format-de-scene.md`.

Le logiciel doit disposer d'un format de scène interne indépendant de Blender et du moteur de rendu, représentant projet, niveaux, pièces, murs, sols, plafonds, toitures, pentes, portes, fenêtres, escaliers, poutres, radiateurs, éléments fixes, meubles, matériaux, éclairages, caméras, mesures, contraintes, incertitudes, sources et historique.

Chaque élément géométrique contient au minimum :

```json
{
  "id": "uuid",
  "type": "wall",
  "source": "measured",
  "confidence": 0.95,
  "locked": false,
  "visible": true,
  "provenance": {},
  "uncertainty": {},
  "metadata": {}
}
```

Valeurs de `source` : `measured`, `observed`, `estimated`, `inferred`, `generated`, `user_defined`.
Priorité : `measured` > `user_defined` > `observed` > `estimated` > `inferred` > `generated`.
Un élément généré par l'IA ne doit jamais remplacer une mesure utilisateur sans validation.
Longueurs en mètres, angles en radians.

---

# 9. Pipeline de traitement

```text
Import des images → Contrôle qualité → Prétraitement → Détection des éléments architecturaux
→ Segmentation → Estimation de profondeur → Estimation de caméra → Reconstruction géométrique
→ Calibration métrique → Plan 2D → Correction par l'utilisateur → Scène 3D
→ Validation géométrique → Aménagements → Validation des contraintes → Rendu et export
```

Chaque étape implémente `PipelineStep` (`docs/architecture/02`) : reçoit un `Project`, renvoie
un `StepResult` (patch JSON, confiance, hypothèses, avertissements, artefacts). Elle est testable
seule et remplaçable.

---

# 10. Vision par ordinateur

Protocoles (`openplan/adapters/protocols.py`) : `DepthEstimator`, `RoomElementDetector`,
`SegmentationModel`, `CameraEstimator`, `MultiViewReconstructor`. Ne jamais lier le code métier
à un modèle précis. Chaque modèle est documenté dans `docs/models/model_registry.md` (nom, version,
usage, licence, poids, mémoire, performance, limites, installation, usage commercial, alternative
locale) et déclaré dans `models/registry.yaml`. Téléchargement de poids explicite uniquement.

Exclus par défaut : modèles à licence non commerciale (Depth Anything V2 Large, DUSt3R/MASt3R)
et AGPL (Ultralytics).

---

# 11. Calibration métrique

Calibration par longueur de mur, largeur de porte, hauteur sous plafond, distance entre deux
points, dimension d'objet, cote sur plan photographié. Deux points, plusieurs segments,
propagation, contrôle des incohérences, optimisation globale, estimation d'erreur, historique.
Mètres en interne ; affichage m / cm / mm. Les dimensions validées par l'utilisateur priment.

---

# 12. Plan 2D

Paramétrique et éditable : murs, longueurs, épaisseur, sommets partagés, contraintes d'angle,
parallélisme, orthogonalité, fermeture, portes, fenêtres, sens d'ouverture, cotations, surfaces,
noms, éléments fixes, fond de photo, confiance visible, verrouillage, annuler/rétablir, export
SVG, PDF, JSON, DXF si possible. Produit à partir de géométrie structurée, jamais une simple
annotation de l'image. Les éléments estimés sont stylés différemment (pointillé, couleur).

---

# 13. Modèle 3D

Généré depuis le plan 2D : murs extrudés, sols, plafonds, ouvertures, escaliers, poutres,
radiateurs, volumes sous pente, combles, éléments fixes, meubles, matériaux, éclairages, caméras.
Toujours régénéré depuis le format de scène (synchronisation 2D/3D). Primitives paramétriques,
pas de mesh destructif. GLB via trimesh ; Blender optionnel pour `.blend`, aperçus, rendu (D5).

---

# 14. Design intérieur par IA (0.3)

Séparation : génération d'intention (LLM → contraintes structurées), génération de disposition
(moteur géométrique), validation (collisions, circulation, accès portes/fenêtres/radiateurs,
hauteur, pentes, contraintes utilisateur), rendu. Une image de diffusion n'est jamais une source
de vérité géométrique.

---

# 15. Moteur de contraintes

Indépendant des modèles IA. Chaque proposition renvoie `score`, `valid`, `violations`
(type, severity, element_ids, message), `warnings`, `suggestions`. Chaque violation est
compréhensible par l'utilisateur.

---

# 16. Catalogue de meubles et matériaux (0.3)

Format d'asset : identifiant, catégorie, dimensions réelles, bounding box, ancrages, modèle 3D,
matériaux, textures, tags de style, licence (`open_source`, `cc0`, `cc_by`,
`commercial_restricted`, `unknown`), source, attribution, prix/fabricant/poids optionnels,
contraintes. Les assets `unknown` ne sont pas distribués. Catalogue de démonstration créé par le
projet : lit, canapé, table, bureau, chaise, armoire, étagère, meuble bas, tapis, luminaire.

---

# 17. Blender et rendu

Scripts Python Blender dans `blender/scripts/`, `bpy` + stdlib uniquement, entrée JSON du format
de scène, sortie `.blend`, GLB, OBJ, USD si possible, vignettes, rapport JSON. Mode headless et
interactif. Le rendu utilise la géométrie validée : jamais un rendu convaincant mais faux.

---

# 18. Interface utilisateur

Étapes progressives : accueil, projet, import, contrôle qualité, calibration, détection, édition
2D, visualisation 3D, brief, style, variantes, validation, édition, rendu, export. L'utilisateur
distingue toujours mesuré / observé / estimé / inféré / généré / validé / incertain. Navigation
côte à côte : image originale, image segmentée, plan 2D, scène 3D, rendu.

---

# 19. Confidentialité

Traitement local prioritaire : stockage local, suppression EXIF optionnelle (activée par défaut),
suppression complète d'un projet, chiffrement optionnel, télémétrie désactivée, aucun envoi cloud
implicite, logs sans contenu personnel, séparation données/modèles, appels réseau documentés,
modèles distants configurés explicitement. Document : `docs/privacy/threat-model.md`.

---

# 20. Évaluation

Protocole reproductible dans `docs/evaluation/protocol.md` : erreurs sur longueurs, surfaces,
portes, fenêtres, fermeture des plans, reconstruction multi-vues, collisions, propositions
valides, temps, mémoire, taille des modèles, qualité des exports et rendus. Fixtures : pièce
rectangulaire, en L, grenier, plafond incliné, mobilier, pièce sombre, forte perspective,
multi-photos, plan photographié. Pas de métrique artificielle : « à implémenter » quand elle
n'existe pas.

---

# 21. Roadmap

0.1 plan 2D · 0.2 scène 3D · 0.3 aménagement assisté · 0.4 reconstruction avancée.
Découpage en incréments du 0.1 : `docs/architecture/03-vertical-slice-et-plan-de-fichiers.md`.

---

# 22. Règles de développement

Avant chaque implémentation importante :

1. inspecte les fichiers existants ;
2. résume l'état actuel ;
3. identifie les dépendances ;
4. liste les hypothèses ;
5. propose un plan limité ;
6. attends ma validation si la décision est structurante.

Lorsque tu implémentes :

- ne génère pas de code fictif présenté comme terminé ;
- ne remplace pas une vraie fonction par un simple `TODO` sans le signaler ;
- ajoute des tests ;
- conserve des exemples reproductibles ;
- documente les commandes ;
- vérifie les erreurs ;
- vérifie les types ;
- vérifie les licences ;
- évite les abstractions inutiles ;
- privilégie le plus petit incrément fonctionnel.

À la fin de chaque tâche, réponds avec exactement cette structure :

## Résultat

Décris ce qui a été implémenté.

## Fichiers modifiés

Liste les fichiers créés ou modifiés.

## Validation

Liste les commandes exécutées et leur résultat.

## Limites

Décris ce qui est encore approximatif, simulé ou incomplet.

## Prochaine étape

Propose une seule prochaine étape prioritaire.

---

# 23. Priorités

1. géométrie ;
2. calibration ;
3. éditabilité ;
4. validation ;
5. interopérabilité ;
6. confidentialité ;
7. IA générative ;
8. rendu photoréaliste.

Ne commence pas par : génération photoréaliste, catalogue complet, application mobile,
reconstruction parfaite, système multi-agent, dépendance cloud, modèle génératif lourd.
