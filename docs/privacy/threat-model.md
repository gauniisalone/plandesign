# Modèle de menace : confidentialité

Version 0.1, 23 septembre 2026. Ce document liste ce que le logiciel manipule de sensible,
par quel canal une fuite pourrait se produire, et ce qui est **réellement en place** dans le
code (avec le fichier concerné) ou seulement prévu. Il est mis à jour à chaque incrément.

## 1. Ce que l'on protège

| Donnée | Pourquoi c'est sensible |
|---|---|
| Photos de l'intérieur d'un logement | Révèlent le lieu de vie, les biens, le niveau de vie, parfois des personnes |
| Visages, objets personnels, documents visibles sur les photos | Identification directe |
| Métadonnées EXIF | Position GPS, date, modèle d'appareil, parfois le nom du propriétaire |
| Plan et dimensions du logement | Configuration des lieux (sécurité physique) |
| Briefs d'aménagement (texte) | Projets, budget, situation familiale |
| Clés d'API des services distants | Accès facturé au compte de l'utilisateur |

## 2. Canaux et mesures

Légende : ✅ en place et testé · 🟨 en place, non testé · ⬜ prévu · ❌ hors périmètre.

### Images et métadonnées

| Menace | Mesure | État | Où |
|---|---|---|---|
| Une photo quitte la machine | Aucun appel réseau dans le code de traitement ; les adaptateurs reçoivent des tableaux en mémoire, jamais d'URL | ✅ | `openplan/adapters/protocols.py` ; test d'import-linter : `schemas` et `geom` sans réseau ni framework |
| EXIF (GPS, nom) conservé dans la copie stockée | Réencodage sans métadonnées par défaut (`privacy.strip_exif = true`) ; seule la focale 35 mm est lue avant, si `privacy.keep_exif_focal` | ✅ | `openplan/pipeline/ingestion.py`, `tests/unit/test_ingestion.py` (vérifie `getexif() == {}`) |
| Le nom de fichier d'origine (parfois parlant) est stocké | Fichier renommé `<uuid>.<ext>` ; le nom d'origine ne sert qu'aux messages d'erreur | ✅ | `ingestion.import_image` |
| Une image trop grosse ou piégée fait tomber le serveur | Taille max 50 Mo ; formats JPEG/PNG/WebP seulement ; erreurs PIL capturées → 422 | ✅ | `ingestion.py`, `api/routes/images.py` |
| Les artefacts (superposition, segments) copient l'image | Ils restent dans `<projet>/runs/<id>/`, supprimés avec le projet | ✅ | `pipeline/detection.py`, `scene/store.py` |

### Stockage et suppression

| Menace | Mesure | État | Où |
|---|---|---|---|
| Des données subsistent après suppression | `DELETE /api/projects/{id}` fait `shutil.rmtree` du dossier (scène, images, runs) | ✅ | `scene/store.py`, `tests/integration/test_api.py` |
| Chemin traversant construit depuis une entrée utilisateur | Dossiers nommés par UUID ; noms d'artefacts filtrés par expression régulière stricte | ✅ | `store.py`, `api/routes/scene3d.py`, `api/routes/detection.py` |
| Données écrites hors du dossier de données | Racine unique `OPENPLAN_DATA_DIR` ; `openplan run --out` écrit uniquement sous `--out` | ✅ | `api/settings.py`, `cli/main.py` |
| Sauvegardes ou synchronisation cloud du dossier de données | Hors du contrôle du logiciel ; à documenter pour l'utilisateur (choisir un `OPENPLAN_DATA_DIR` non synchronisé) | ⬜ | docs utilisateur |
| Chiffrement au repos | Non prévu en 0.1 (repose sur le disque de l'utilisateur) | ❌ | — |

### Réseau et API

| Menace | Mesure | État | Où |
|---|---|---|---|
| L'API est atteignable depuis le réseau local | Écoute sur `127.0.0.1` par défaut ; l'utilisateur doit passer `--host` explicitement | ✅ | `api/settings.py`, `cli/main.py` |
| Un site web tiers appelle l'API depuis le navigateur | CORS limité aux origines de développement locales (5173, 4173) | 🟨 | `api/app.py` |
| Pas d'authentification sur l'API locale | Sur `127.0.0.1`, `localhost` ou `::1` : aucun jeton (même machine, même utilisateur). Dès que `--host` sort de la boucle locale : jeton exigé sur toute route `/api` sauf `/api/health`, en en-tête `Authorization: Bearer` (jamais dans l'URL, jamais renvoyé, comparé en temps constant) ; `OPENPLAN_API_TOKEN` ou jeton généré et affiché une fois par `openplan serve` ; démarrage refusé sans jeton ; le front le demande une fois et le garde dans l'onglet | ✅ | `api/app.py::_require_token`, `cli/main.py::serve_settings`, `tests/integration/test_api.py::test_token_required_outside_loopback` |
| Télémétrie | Aucune ; `privacy.telemetry` existe et vaut `false`, rien ne le lit | ✅ | `schemas/scene.py` |

### Modèles et services distants

| Menace | Mesure | État | Où |
|---|---|---|---|
| Un modèle télécharge ses poids à l'insu de l'utilisateur | Téléchargement uniquement par `openplan models pull <id>` ; l'adaptateur charge en `local_files_only` et échoue proprement sans poids ; contrat import-linter : seul `adapters.neural.download` importe `huggingface_hub` | ✅ | `openplan/adapters/neural/{weights,download}.py`, `pyproject.toml` (contrat « Poids »), `tests/unit/test_depth_step.py` |
| Un service distant (Gemini, ChatGPT) reçoit une photo à l'insu de l'utilisateur | Double drapeau par projet : `privacy.allow_remote_models` (défaut `false`) **et** `privacy.remote_consent`, vérifiés avant tout appel par `check_consent` ; refus → HTTP 403 sans appel ; la boîte de dialogue du panneau IA dit ce qui part et ce qui ne part pas | ✅ | `openplan/adapters/gateway.py`, `tests/unit/test_ai_adapters.py`, `tests/integration/test_api.py::test_ai_perception_requires_consent_and_logs`, `apps/web/src/panels/AiPanel.tsx` |
| Ce qui part est plus que nécessaire | Une seule image par appel, réduite à 1600 px, réencodée en JPEG (donc sans EXIF), deux prompts fixes ; ni le plan, ni les autres images, ni le nom du projet | ✅ | `openplan/adapters/remote/openai_compatible.py::image_data_url`, `vlm_detector.py` |
| Une image générée est prise pour une photo ou un relevé | Génération locale seulement (aucun réseau) ; sortie de source `generated`, badge « générée », consigne et graine consignées (`prompt.json`) ; l'image ne modifie jamais la scène ; filtre de sécurité non chargé, dit explicitement | ✅ | `openplan/pipeline/inspiration.py`, `tests/unit/test_inspiration.py` |
| Une image part vers un modèle qui va la refuser (modèle retiré, saturé, quota, clé refusée) | Avant toute requête avec image à un service distant, une sonde texte de quelques jetons vérifie que le modèle répond ; un refus est journalisé (`probe: true`, 0 image) et l'image ne part pas (HTTP 502 « refuse le modèle avant tout envoi d'image ») ; une sonde réussie vaut dix minutes. Constaté le 24 sept. 2026 : six appels Gemini refusés (404, 503) avaient chacun emporté la photo | ✅ | `gateway.py::ModelGateway._ensure_available`, `tests/unit/test_ai_adapters.py::test_gateway_probes_remote_before_sending_images` |
| Aucun moyen de savoir ce qui a été envoyé | Journal `<projet>/network_log.jsonl` : horodatage, rôle, destination, local/distant, nombre d'images, octets, durée, succès ou classe d'erreur ; jamais le contenu ; route `GET /api/projects/{id}/network-log` et panneau IA | ✅ | `gateway.py::NetworkLogEntry`, test « jamais le contenu des messages » |
| Du code réseau se glisse ailleurs que dans l'adaptateur | Contrat import-linter : `httpx`, `requests`, `aiohttp`, `urllib`, `socket` interdits hors `openplan.adapters.remote` (couches schemas, geom, scene, validation, heuristique, passerelle) | ✅ | `pyproject.toml` (contrat « Réseau »), `make lint` |
| Un serveur « local » est en fait distant | `remote` déduit de l'hôte de `OPENPLAN_AI_BASE_URL` : seuls `localhost`, `127.0.0.1`, `::1`, `0.0.0.0` sont locaux | ✅ | `openai_compatible.py::LOCAL_HOSTS` |
| La sortie du modèle écrase des données | La perception ne produit aucun patch de scène ; ses suggestions sont appliquées par l'utilisateur, en source `estimated`, score plafonné à 0,6 | ✅ | `openplan/pipeline/perception.py`, `vlm_detector.py::MAX_SCORE` |
| Clés d'API commitées | `.env` ignoré par git ; `.env.example` sans valeur ; la clé n'apparaît ni dans le journal ni dans les réponses d'API (`GET /api/ai` ne renvoie que modèle et hôte) | ✅ | `.gitignore`, `api/routes/ai.py` |
| Extension tierce (plugin) qui exfiltre | Pas de système d'extensions en 0.1 | ❌ | — |

### Journaux et sorties

| Menace | Mesure | État | Où |
|---|---|---|---|
| Les journaux contiennent des données | Le serveur ne journalise que les lignes d'accès uvicorn (méthode, chemin avec UUID, code) ; aucun contenu d'image ni de scène | 🟨 | comportement uvicorn par défaut |
| Un export (SVG, GLB, JSON) contient plus que prévu | Deux exports JSON : la sauvegarde complète (historique et chemins relatifs des images, jamais les pixels), signalée comme telle dans la barre d'outils, et la copie « à partager » (`?share=true`, `--share`) sans historique ni chemin d'image, contenu métier intact | ✅ | `pipeline/export.py::shareable`, `tests/unit/test_export_share.py` |
| Le GLB ou le SVG embarque la photo | Non : uniquement la géométrie et les noms d'éléments (UUID) | ✅ | `pipeline/scene3d.py`, `pipeline/export.py` |
| L'image Docker embarque des données ou expose l'API | `.dockerignore` exclut `data/`, `models/weights/`, `.env`, tests ; `docker-compose.yml` en réseau hôte : l'API écoute 127.0.0.1 de la machine, sans jeton, injoignable du réseau ; la variante à port publié exige `OPENPLAN_API_TOKEN` ; `HF_HUB_OFFLINE=1`, aucune sortie réseau au démarrage | ✅ | `Dockerfile`, `docker-compose.yml`, `.dockerignore` |
| L'index des projets ou la vignette survivent à la suppression | L'index `data/index.sqlite` ne contient que nom, dates, compteurs et un drapeau de vignette (pas de chemin d'image ni de pixel) ; la vignette (`<projet>/thumbnail.jpg`, 240 px, sans métadonnées) vit dans le dossier du projet : supprimer le projet efface les deux, supprimer la dernière photo retire la vignette | ✅ | `scene/store.py`, `pipeline/ingestion.py` |

## 3. Risques résiduels acceptés en 0.1

- L'utilisateur peut désactiver `strip_exif` ; l'interface l'indique dans le panneau Photos.
- Un tiers ayant accès à la session de l'utilisateur a accès aux données (pas de chiffrement).

## 4. Vérification

```bash
uv run pytest tests/unit/test_ingestion.py tests/integration/test_api.py -q --no-cov
uv run pytest tests/unit/test_ai_adapters.py -q --no-cov     # consentement et journal
uv run lint-imports        # frontières : le réseau n'existe que sous openplan/adapters/remote
grep -rln "httpx\|requests\|aiohttp\|urllib" openplan/ | grep -v adapters/remote   # doit être vide
```
