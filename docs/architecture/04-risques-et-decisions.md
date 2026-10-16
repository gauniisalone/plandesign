# 04 — Risques principaux et décisions à prendre

Statut : **proposition, en attente de validation**.

## 1. Risques

| # | Risque | Impact | Probabilité | Mitigation prévue |
|---|---|---|---|---|
| R1 | **Ambiguïté métrique d'une image unique.** Une photo ne donne ni la profondeur absolue ni ce qui est hors champ. Les utilisateurs attendront un plan « exact ». | Élevé (confiance produit) | Certaine | Calibration par mesure obligatoire ; toute dimension non mesurée affichée en pointillé avec son incertitude ; message d'avertissement permanent « reconstruction estimée, pas un relevé » ; suggestion de photos complémentaires quand un mur n'est pas vu. |
| R2 | **Détection heuristique faible** (points de fuite instables sur photos sombres, grand-angle, pièces encombrées). | Moyen | Élevée | Le slice ne dépend pas de la détection : la saisie manuelle est un chemin de premier rang, pas un secours. Les modèles neuronaux arrivent derrière le même protocole. |
| R3 | **Licences des modèles.** Depth Anything V2 : Small/Base Apache-2.0, **Large/Giant CC-BY-NC**. DUSt3R/MASt3R : **CC-BY-NC-SA**. Modèles Ultralytics (YOLO) : **AGPL-3.0**. Certains poids Detectron2/Mask2Former : CC-BY-NC. | Élevé (redistribution, usage pro) | Certaine si non surveillée | Registre de modèles avec champ licence et `commercial_ok` ; le registre refuse par défaut les modèles non commerciaux sauf opt-in explicite ; `docs/licenses/README.md` tenu à jour ; aucun poids dans le dépôt. |
| R4 | **Blender et GPL.** La Blender Foundation considère les scripts qui importent `bpy` comme des œuvres dérivées (GPL). | Moyen (choix de licence) | Certaine | Scripts Blender dans `blender/` sous GPL-3.0-or-later, isolés par sous-processus et échange JSON ; le cœur peut rester sous licence permissive. Décision D4 ci-dessous. |
| R5 | **Versions de Python.** Blender 4.2/4.5 LTS embarquent 3.11 ; hôte WSL et conteneur en 3.13 ; Open3D sans roue 3.13 garantie. | Moyen (installation) | Élevée | Projet épinglé en 3.12 via `uv` ; script Blender sans dépendance projet ; Open3D repoussé à 0.4 et rendu optionnel. |
| R6 | **6 Go de VRAM, 16 Go de RAM WSL.** | Moyen (fonctionnalités IA) | Certaine | Chemin CPU pour chaque étape ; modèles Small/Base par défaut ; chargement à la demande et déchargement après l'étape ; diffusion hors périmètre avant 0.3 et uniquement en modèles légers ou distants sur consentement. |
| R7 | **Complexité de l'éditeur 2D.** C'est le plus gros morceau UI (snapping, contraintes, cotations, undo). | Élevé (délai) | Élevée | Incréments : sommets/murs → ouvertures → cotations → contraintes. Toute la géométrie côté serveur ; le front ne fait que des transformations affines et des patches. |
| R8 | **Divergence 2D / 3D.** | Élevé (cohérence) | Moyenne | Une seule source de vérité (format de scène) ; le 3D est toujours régénéré depuis le plan, jamais édité directement en 0.1/0.2. |
| R9 | **Environnement de dev fragmenté** (Node absent de l'hôte, images `3d`/`ml` jamais buildées, compte Windows non administrateur). | Moyen (démarrage) | Certaine | Décision D2 ; `docs/contributing/setup.md` documente les deux chemins (hôte WSL avec uv + nvm, ou conteneur). |
| R10 | **Confidentialité par accident** : téléchargement de poids depuis Hugging Face au premier appel, télémétrie de bibliothèques tierces, EXIF avec GPS conservé. | Élevé (promesse du produit) | Moyenne | Téléchargement explicite uniquement ; `HF_HUB_OFFLINE=1` après pull ; EXIF retiré par défaut ; audit des appels réseau documenté dans `docs/privacy/threat-model.md` ; API liée à `127.0.0.1`. |
| R11 | **Dérive de périmètre** (catalogue, IA générative, mobile). | Élevé (délai MVP) | Élevée | Roadmap 0.1 → 0.4 ; tout ce qui n'est pas dans le slice est refusé jusqu'à 0.1.0 livré. |
| R12 | **Ouvertures dans les murs en 3D** : les booléens 3D sont fragiles (trimesh nécessite un moteur externe, Blender est lent sur des cas dégénérés). | Faible | Moyenne | Découpe en 2D avant extrusion (le mur devient un polygone avec trou projeté), pas de CSG 3D côté trimesh. |

## 2. Décisions à valider avant d'écrire du code

**D1 — Structure de dépôt.** Paquet Python unique `openplan` avec sous-paquets (doc 00 §4) plutôt
que `services/` + `packages/` séparés. *Recommandation : oui.*

**D2 — Environnement de développement.** (a) Installer `uv` et `nvm` sur l'hôte Debian WSL et
travailler hors conteneur (le plus simple pour VS Code Remote-WSL et pour Blender) ; ou (b) tout
faire dans `sandbox-base` (Node 24 présent, Python 3.13, mais Blender et GPU absents, et `uv`
à ajouter). *Recommandation : (a), en gardant `docker-compose.yml` fonctionnel pour la
reproductibilité.*

**D3 — Version de Python : 3.12 épinglée par `uv`.** *Recommandation : oui.*

**D4 — Licence du projet.** (a) Apache-2.0 pour le cœur + GPL-3.0-or-later pour `blender/` ; ou
(b) tout en AGPL-3.0 (copyleft fort, protège l'open-source, freine l'adoption professionnelle) ;
ou (c) tout en MIT. *Recommandation : (a). Ce choix conditionne aussi les modèles acceptés
(AGPL d'Ultralytics exclu du cœur dans tous les cas).*

**D5 — GLB en 0.1.0 : trimesh d'abord, Blender en option.** Le brief dit « extrusion simple
dans Blender ». Je propose que trimesh produise le GLB de visualisation et d'export (aucune
dépendance externe, testable en CI), et que Blender produise `.blend`, GLB « officiel » et aperçu
PNG quand il est installé. Les deux lisent le même JSON. *Recommandation : oui, sinon la CI et
l'installation minimale dépendent d'un binaire de 300 Mo.*

**D6 — Détection initiale en 0.1.0 : heuristique sans réseau de neurones**, modèles Depth
Anything V2 Small et SAM 2 en 0.1.x. *Recommandation : oui, pour livrer la chaîne complète
avant d'y brancher des modèles.*

**D7 — Premier incrément à coder : 0 (bootstrap) puis 1 (schémas + scène).** Les schémas de la
doc 01 deviennent `openplan/schemas/*` avec leurs tests. *Recommandation : oui.*

## 3. Hypothèses posées

- Un seul utilisateur local, pas d'authentification en 0.1 (API sur `127.0.0.1`).
- Un projet = un dossier sur disque, supprimable d'un bloc.
- Les scènes tiennent en mémoire (quelques centaines d'éléments) ; pas de base relationnelle
  pour le contenu, SQLite sert d'index (liste des projets, dates, tailles).
- Les angles de murs sont libres ; l'orthogonalité est une contrainte optionnelle, pas une
  hypothèse du format. L'heuristique de détection, elle, suppose Manhattan et le déclare.
- La hauteur sous plafond par défaut est 2,50 m avec `height_source=estimated` tant que
  l'utilisateur ne l'a pas mesurée.
- Blender 4.2 LTS ou 4.5 LTS ; toute version ≥ 4.2 doit fonctionner avec le script.

## 4. Hors périmètre jusqu'à validation de 0.1.0

Rendu photoréaliste, catalogue complet, application mobile, reconstruction multi-vues, agents
multiples, dépendance cloud, modèles génératifs lourds. Roadmap détaillée 0.1 → 0.4 (fonctions,
fichiers, dépendances, critères, tests, risques, dette) : à rédiger après validation de
l'architecture, pour ne pas planifier sur des bases qui changeraient.
