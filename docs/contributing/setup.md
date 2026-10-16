# Installation de l'environnement de développement

Environnement de référence : **Debian sous WSL2**, dépôt dans le système de fichiers Linux
(`~/plandesign`). Ne pas cloner dans `/mnt/c/` : accès
fichiers 5 à 10 fois plus lents et surveillance de fichiers (Vite, reload) non fiable.

## 1. Outils

```bash
# uv : Python et environnements virtuels, installation utilisateur
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12

# nvm + Node LTS pour apps/web
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.nvm/nvm.sh && nvm install --lts && nvm alias default 'lts/*'
```

Blender (optionnel, pour `.blend`, aperçus et rendu) : version LTS 4.2 ou 4.5 depuis
blender.org, extraite dans `~/opt/blender` (emplacement cherché par défaut, avec le `PATH`).
Sous Debian minimal, le binaire a besoin de quelques bibliothèques X11/GL :

```bash
mkdir -p ~/opt && cd ~/opt
wget https://download.blender.org/release/Blender4.2/blender-4.2.5-linux-x64.tar.xz
tar -xf blender-4.2.5-linux-x64.tar.xz && ln -sfn blender-4.2.5-linux-x64 blender
sudo apt-get install -y --no-install-recommends libxi6 libxrender1 libxkbcommon0 libgl1 \
    libglu1-mesa libsm6 libxfixes3 libxxf86vm1 libegl1
~/opt/blender/blender -b --version
```

Un chemin explicite dans `OPENPLAN_BLENDER_BIN` fait autorité : s'il est faux, aucun autre
Blender n'est cherché. Sans Blender, le GLB est produit par trimesh (case « Matières » du
viewer pour les teintes bois / pierre / enduit) ; le rendu depuis la photo répond 409 avec un
message explicite. Vérifier : `curl http://127.0.0.1:8000/api/blender`.

Exposer l'API à une autre machine (`openplan serve --host 0.0.0.0`) exige un jeton : mettre
`OPENPLAN_API_TOKEN` dans `.env`, sinon `serve` en génère un et l'affiche une fois. Les clients
l'envoient en en-tête `Authorization: Bearer …` (le front le demande au premier refus) ; jamais
dans une URL. Sur `127.0.0.1`, rien ne change.

`OPENPLAN_DOTENV=chemin` choisit le fichier `.env` lu au démarrage ; `OPENPLAN_DOTENV=` (vide)
n'en lit aucun : c'est ce que font les tests navigateur, pour qu'une clé d'API distante
présente dans votre `.env` ne soit jamais utilisée par un serveur de test.

Rendu depuis la photo (`POST .../levels/{id}/render?samples=64&gpu=true`, bouton « Rendre
depuis la photo ») : Cycles essaie OptiX puis CUDA quand `gpu=true`, sinon CPU ; le
périphérique retenu est écrit dans le rapport (`device`) et dans la description de l'aperçu.
Sous WSL2, le premier rendu GPU peut compiler les noyaux CUDA pendant plusieurs minutes
(une seule fois par version de Blender et de pilote).

## 2. Projet

```bash
cd ~/plandesign
cp .env.example .env
make setup      # uv sync --all-extras + npm ci
make check      # lint + typecheck + test + build
```

## 2 bis. Commandes utiles

```bash
make start                                  # front construit + API sur http://localhost:8000/ (une commande)
uv run openplan serve --web                 # idem sans reconstruire le front (apps/web/dist)
uv run openplan serve                       # API locale, 127.0.0.1:8000
OPENPLAN_PORT=8010 OPENPLAN_WEB_PORT=5183 make e2e   # e2e sur d'autres ports si 8000/5173 sont pris (make start qui tourne)
uv run openplan schemas apps/web/schemas    # JSON Schema (puis make web-types)
uv run openplan export p.json --format svg --out plan.svg    # svg | pdf | dxf | json | glb | bake
uv run openplan export p.json --format json --share --out partage.json   # sans historique ni chemins d'images
uv run openplan evaluate data/eval                           # photos réelles mesurées → rapport.md (voir protocole §2 ; photo2.jpg… = vues supplémentaires)
uv run openplan blender p.json --out-dir out/               # .blend, GLB, aperçu (Blender requis)
uv run openplan run photo.jpg --out out/demo --measure 3.85 --camera-height 1.4
#   photo → proposition de plan (heuristique) → calibration sur le mur de face → exports ;
#   tout reste dans --out (images/, runs/<id>/detection/overlay.png, project.json, plan.svg…)

uv run pytest -m "not blender and not slow"  # tests rapides
make e2e                                     # scénarios navigateur (Chromium headless) ; une fois :
#   cd apps/web && npx playwright install chromium && sudo env "PATH=$PATH" npx playwright install-deps chromium
uv run python -m tests.fixtures.rect_room.render   # régénère la fixture synthétique (Blender, ~30 s)
```

## 2 bis bis. Modèles neuronaux locaux (optionnel, lourd)

```bash
make setup-neural        # torch + torchvision + transformers + diffusers : ≈ 5 Go dans .venv
uv run openplan models list
uv run openplan models pull depth_anything_v2_metric_indoor_small@1.0   # 99 Mo, Apache-2.0, explicite
# Image d'inspiration (facultatif) : poids OpenRAIL-M, licence affichée puis acceptée explicitement
uv run openplan models pull stable_diffusion_1_5@1.5 --accept-license      # ≈ 2,1 Go (fp16)
uv run openplan models pull controlnet_depth_sd15@1.1 --accept-license     # ≈ 0,7 Go
```

Les poids vont dans `OPENPLAN_MODELS_DIR` (défaut `models/weights`, ignoré par git). Sans eux,
la route de profondeur répond 409 avec la commande à lancer. `OPENPLAN_DEVICE` vaut `auto` par
défaut : le GPU est pris dès que torch le voit (profondeur 0,1 s, inspiration ≈ 10 s), sinon
processeur (profondeur quelques secondes, inspiration plusieurs minutes). `cpu` force le
processeur, `cuda` retombe sur `cpu` sans GPU. Le périphérique retenu est affiché au démarrage
de `openplan serve` et par `GET /api/device`. Les modèles chargés sur la carte graphique sont
relâchés après `OPENPLAN_GPU_IDLE_S` secondes sans calcul (600 par défaut, 0 pour les garder) ;
le calcul suivant les recharge (quelques secondes de plus). L'image d'inspiration est une tâche de fond
(`POST …/inspire` répond 202, suivi par `GET /projects/{id}/jobs/{job}`).

## 2 ter. Assistant IA (optionnel)

Sans configuration, il n'y a pas de modèle et le panneau IA le dit. Dans `.env` :

```bash
# Local (Ollama avec un modèle vision) : rien ne quitte la machine, pas de consentement demandé
OPENPLAN_AI_BASE_URL=http://127.0.0.1:11434/v1
OPENPLAN_AI_MODEL=llava
# OpenAI
OPENPLAN_AI_BASE_URL=https://api.openai.com/v1
OPENPLAN_AI_MODEL=gpt-4o-mini
OPENAI_API_KEY=...
# Gemini (point d'entrée compatible OpenAI)
OPENPLAN_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
OPENPLAN_AI_MODEL=gemini-3.5-flash-lite   # les gemini-2.x sont retirés (404) ; lister : GET <base_url>/models
GEMINI_API_KEY=...
```

Gratuité et vie privée, en clair :

- **Local (Ollama)** : gratuit et privé. Installation utilisateur sans root :
  `curl -L https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst | tar --zstd -x -C ~/opt/ollama`
  (créer le dossier avant ; `zstd` requis), puis `~/opt/ollama/bin/ollama serve` et
  `ollama pull qwen2.5vl:7b`. Les modèles de 1,6 B et 3 B testés ne donnent rien d'utile ;
  `qwen2.5vl:7b` (6 Go) donne un premier résultat utile en ≈ 3 min sur la machine de
  référence (GPU 6 Go) : voir `docs/evaluation/protocol.md` §3 bis. Laisser
  `OPENPLAN_AI_TIMEOUT_S` à 600.
- **Gemini, palier gratuit** : clé sans carte bancaire sur aistudio.google.com. Sur ce palier,
  Google peut utiliser les envois pour améliorer ses produits : les photos partent chez Google
  sous ces conditions. À réserver aux photos non sensibles, après la confirmation par projet.
- **OpenAI** : payant à l'usage ; non requis.

Un hôte distant n'est appelé que si le projet a coché « Autoriser l'envoi pour ce projet »
(`privacy.allow_remote_models` et `privacy.remote_consent`). Chaque appel est journalisé dans
`<projet>/network_log.jsonl`. Vérifier : `curl http://127.0.0.1:8000/api/ai`.

## 2 quater. Conteneur (optionnel)

```bash
docker compose up --build                 # image légère : API + front sur http://localhost:8000/
OPENPLAN_PORT=8010 docker compose up      # si le port 8000 est déjà pris
docker compose down
```

`docker-compose.yml` utilise le réseau hôte : l'API écoute `127.0.0.1` de la machine, sans jeton,
comme `make start`. Les projets restent dans `./data` (volume), les poids dans `./models/weights`
(lecture seule). L'image (2,3 Go) n'a ni torch ni Blender : profondeur, combles depuis la
profondeur, image d'inspiration et rendu Blender répondent par un message clair ; pour eux,
installation locale (`make setup-neural`). Variante sans réseau hôte (port publié + jeton
`OPENPLAN_API_TOKEN`) en commentaire dans le fichier.

## 3. VS Code

Ouvrir le dossier via l'extension Remote-WSL (« WSL: Open Folder in WSL »), ou depuis un
terminal Debian : `code .`. Lancer Claude Code dans ce terminal, pas sous Windows : le
`.claude/settings.json` (sandbox natif) n'a de sens que côté Linux.

## 4. GPU (optionnel)

Sous WSL2, `nvidia-smi` doit répondre ; le pilote est celui de Windows. PyTorch CUDA sera
installé via une extra dédiée quand les premiers adaptateurs neuronaux arriveront (0.1.x).
La machine de référence a 6 Go de VRAM : les modèles « small/base » sont la cible.
