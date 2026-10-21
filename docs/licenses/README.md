# Inventaire des licences

Inventaire vérifié le 23 septembre 2026 à partir des métadonnées installées (`uv run python`
et `node_modules/*/package.json`). Règle : rien n'entre avec une licence inconnue ou non
commerciale ; les composants AGPL sont exclus des adaptateurs (doc 04).

## Code du dépôt

| Périmètre | Licence | Fichier |
|---|---|---|
| Tout le dépôt sauf `blender/` | Apache-2.0 | `LICENSE` |
| `blender/` (scripts qui importent `bpy`) | GPL-3.0-or-later | `blender/LICENSE` |

Le noyau Python appelle Blender en **sous-processus** avec un fichier JSON comme seul contrat
(`openplan/schemas/bake.py`) ; il n'importe jamais `bpy`, ce qui garde le noyau sous Apache-2.0.

## Dépendances Python d'exécution

| Paquet | Version installée | Licence |
|---|---|---|
| pydantic | 2.13 | MIT |
| numpy | 2.5 | BSD-3-Clause (et 0BSD, MIT, Zlib, CC0-1.0 pour des parties) |
| shapely | 2.1 | BSD-3-Clause |
| trimesh | 5.1 | MIT |
| scipy | 1.18 | BSD-3-Clause |
| mapbox-earcut | 2.1 | ISC |
| jsonpatch / jsonpointer | 1.33 / 3.1 | BSD (modifiée) |
| PyYAML | 6.0 | MIT |
| fastapi / starlette | 0.141 / 1.7 | MIT / BSD-3-Clause |
| uvicorn | 0.53 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| opencv-python-headless | 5.0 | Apache-2.0 (OpenCV) ; le détecteur LSD utilisé est celui d'OpenCV ≥ 4.5.1, réimplémenté sous la même licence |
| Pillow | 12.3 | MIT-CMU (HPND) |
| reportlab (export PDF) | 5.0 | BSD-3-Clause (la version libre ; l'extension commerciale « rlextra » n'est pas utilisée) |
| charset-normalizer (dépendance de reportlab) | 3.5 | MIT |
| ezdxf (export et import DXF) | 1.4 | MIT |
| pdfplumber (import de plan PDF vectoriel) | 0.11 | MIT |
| pdfminer.six, pypdfium2 (dépendances de pdfplumber) | 2026.01 / 5.13 | MIT / BSD-3-Clause ou Apache-2.0 (au choix) |
| fonttools, pyparsing (dépendances d'ezdxf) | 4.66 / 3.3 | MIT / MIT |
| diffusers, accelerate (extra `neural`, image d'inspiration) | 0.40 / 1.15 | Apache-2.0 / Apache-2.0 |

## Dépendances Python de développement

pytest, pytest-cov, ruff, mypy : MIT · import-linter : BSD-2-Clause · httpx : BSD-3-Clause ·
types-PyYAML : Apache-2.0.

## Dépendances JavaScript (`apps/web`)

| Paquet | Version | Licence |
|---|---|---|
| react, react-dom | 19.3 | MIT |
| three, @types/three | 0.186 | MIT |
| vite, @vitejs/plugin-react | 7.3 / 5.2 | MIT |
| typescript | 5.9 | Apache-2.0 |
| eslint, @eslint/js, typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals | 9.39 / 8.70 / 6.1 / 0.4 / 16.5 | MIT |
| json-schema-to-typescript | 15.0 | MIT |
| @playwright/test (dev, e2e) | 1.63 | Apache-2.0 ; Chromium headless téléchargé par l'outil (BSD-3-Clause) |
| onnxruntime-web (KAN-79, profondeur dans le navigateur ; servi par le front sous /ort/) | 1.30.0 (figée) | MIT (Microsoft, https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| dépendances d'onnxruntime-web : onnxruntime-common, platform / flatbuffers, long / guid-typescript / protobufjs | 1.30.0, 1.3 / 25.x, 5.x / 1.0 / 7.x | MIT / Apache-2.0 / ISC / BSD-3-Clause |

## Outils externes

| Outil | Licence | Mode d'utilisation |
|---|---|---|
| Blender 4.2 LTS | GPL-3.0-or-later | Binaire installé par l'utilisateur, appelé en sous-processus ; non redistribué |
| uv, Node.js, nvm | MIT / MIT / MIT | Outillage, hors produit |
| onnx 1.23, onnx-ir 1.1, ml_dtypes 0.6 / onnxscript 0.7 / protobuf 7.36 | Apache-2.0 / MIT / BSD-3-Clause | KAN-79 : conversion du modèle de profondeur pour le navigateur (`uv run --with … openplan models export-web-depth`), sur la machine ; pas des dépendances du serveur |

## Modèles et poids

Aucun poids de modèle n'est présent ni téléchargé en 0.1. Les algorithmes implémentés
(`exif_camera`, `vp_camera`, `heuristic_box`) sont du code du dépôt (Apache-2.0). Les modèles
prévus et leurs licences sont dans [`docs/models/model_registry.md`](../models/model_registry.md).

### Poids sous CreativeML OpenRAIL-M (image d'inspiration, 8e)

Stable Diffusion 1.5 et ControlNet 1.1 « profondeur » sont distribués sous **CreativeML
OpenRAIL-M** (texte : https://huggingface.co/spaces/CompVis/stable-diffusion-license).
Ce n'est pas une licence libre au sens OSI : elle est gratuite, autorise l'usage commercial et
la redistribution, mais impose des **restrictions d'usage** (annexe A) que chaque utilisateur
doit respecter et transmettre : pas de contenu illégal, haineux, harcelant, discriminatoire,
trompeur (fausses informations, usurpation), sexuel non consenti, ni d'exploitation de
vulnérabilités, ni d'atteinte aux droits d'autrui. Décision d'augus (24 sept. 2026) :
acceptée à ces conditions : les poids ne sont **jamais livrés** avec OpenPlan, l'utilisateur les
télécharge lui-même par `openplan models pull <id> --accept-license` après affichage de la
licence, et l'acceptation est consignée dans le dossier des poids (`LICENSE_ACCEPTED`). Toute
image produite est de source `generated`, reste sur la machine et n'est pas filtrée (le filtre
de sécurité du dépôt n'est pas téléchargé).

## Assets (textures, mobilier, polices)

Le champ `Material.license` du format de scène
(`open_source | cc0 | cc_by | commercial_restricted | unknown`) est partagé avec le catalogue.

### Polices de l'application (KAN-77, 21 octobre 2026)

Les fichiers du site (`apps/site/public/fonts`), copiés dans `apps/web/public/fonts` et servis
par le front : aucune requête externe.

| Police | Fichier | Licence | Preuve |
|---|---|---|---|
| Outfit (variable 100–900, latin) | `outfit-latin-wght.woff2` | SIL Open Font License 1.1 | `apps/web/public/fonts/OFL-Outfit.txt` |
| JetBrains Mono 400 (latin) | `jetbrains-mono-latin-400.woff2` | SIL Open Font License 1.1 | `apps/web/public/fonts/OFL-JetBrainsMono.txt` |

Outfit a la fonction OpenType `tnum` (chiffres de même chasse), utilisée pour les cotes.

### Icônes de l'interface

`apps/web/src/ui/icons.tsx` : icônes au trait (24 × 24, trait 1,8) dessinées dans le projet,
sans dépendance. Certains tracés reprennent ceux de Lucide (licence ISC,
https://lucide.dev/license), au moins la main (`IconHand`), Annuler et Rétablir
(`IconUndo`, `IconRedo`) : avis de licence ci-dessous. KAN-77 ajoute Post-it, Cadre et Lien,
dessinés par le projet.

> ISC License — Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
> of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.
> Permission to use, copy, modify, and/or distribute this software for any purpose with or
> without fee is hereby granted, provided that the above copyright notice and this permission
> notice appear in all copies.

### Catalogue de meubles (`assets/catalog.yaml`, 3.1, 25 septembre 2026)

Onze meubles paramétriques **créés par le projet** : des boîtes cotées (plages de dimensions,
ancrage, dégagements), sans modèle 3D ni texture externe. Licence **CC0 1.0** (domaine public),
auteur « OpenPlan Studio », déclarée asset par asset dans le fichier. Le chargeur
(`openplan/scene/catalog.py`) refuse toute licence hors `open_source`, `cc0`, `cc_by`
(`unknown` et `commercial_restricted` compris), tout identifiant en double et toute plage
incohérente ; un test le vérifie (`tests/unit/test_catalog.py`). Aucun asset externe n'est prévu
en 0.3.

### Bibliothèque de mobilier : modèles 3D et vignettes (`assets/furniture/`, 3 octobre 2026)

| Source | Licence | Vérification | Contenu dans le dépôt |
|---|---|---|---|
| Kenney, « Furniture Kit » — <https://kenney.nl/assets/furniture-kit> | **CC0 1.0** (domaine public), attribution non obligatoire, usage commercial libre | 2026-10-03 : page de l'asset, page d'aide <https://kenney.nl/support>, fichier `License.txt` du zip (copié dans `assets/furniture/LICENSE-kenney-furniture-kit.txt`) ; zip SHA-256 `e67652d0…46b0` | 34 modèles GLB (`models/`) et 34 vignettes isométriques (`previews/`), ≈ 1 Mo |

- Le registre `assets/furniture/SOURCES.yaml` liste chaque fichier, sa source, la licence, les
  preuves de lecture et l'empreinte du zip. Un modèle tiers n'est accepté qu'en **CC0**
  (`ModelSource.license`). Le test `tests/unit/test_catalog.py` refuse un modèle ou une vignette
  sans source, un fichier du dossier non déclaré et un chemin invalide.
- Import reproductible : `uv run python -m openplan.tools.import_furniture <zip>` (vérifie
  l'empreinte, ne télécharge rien).
- Les cotes, plages, dégagements et ancrages du catalogue restent l'œuvre du projet (CC0). Le
  logo et la marque Kenney ne sont pas utilisés ; le crédit, facultatif, figure ici et dans le
  catalogue (`license_note`).
- Aucune obligation n'est transmise à l'utilisateur : un plan, un export ou un rendu contenant
  ces meubles peut être diffusé librement.

## Vérifier

```bash
uv run python - <<'EOF'
import importlib.metadata as m
for d in sorted(m.distributions(), key=lambda d: d.metadata["Name"].lower()):
    lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
    cls = [c for c in d.metadata.get_all("Classifier", []) if c.startswith("License")]
    print(d.metadata["Name"], d.version, lic[:40] or cls)
EOF
cd apps/web && node -e 'for (const p of Object.keys({...require("./package.json").dependencies, ...require("./package.json").devDependencies})) console.log(p, JSON.parse(require("fs").readFileSync("node_modules/"+p+"/package.json")).license)'
```


## Matières de rendu (D2, 4 octobre 2026)

Textures PBR (couleur, normales, rugosité, 1K) et un ciel HDRI de **Poly Haven**, sous
**CC0** : licence vérifiée le 4 octobre 2026 sur https://polyhaven.com/license (« You can use
our assets for any purpose, including commercial work », aucune attribution exigée). Elles ne
sont **pas dans le dépôt** : `assets/materials/registry.yaml` en donne la source, la page, la
preuve de licence, la date de vérification, l'URL et l'empreinte SHA-256 de chaque fichier ;
`uv run openplan materials pull --all` les télécharge dans `models/materials/` en vérifiant
chaque empreinte (≈ 14 Mo). `openplan doctor` dit combien sont installées. Sans elles, le rendu
garde ses matières dessinées (procédurales, sans licence tierce).

| Finition | Matière Poly Haven |
|---|---|
| Enduit | plaster_grey_04 |
| Parquet clair (pin) | hinoki_planks |
| Parquet ancien | old_wood_floor |
| Lambris (chêne) | oak_wood_planks |
| Pierre | old_stone_wall |
| Moellons | coral_stone_wall |
| Béton | grey_plaster |
| Ciel (HDRI) | kloofendal_48d_partly_cloudy_puresky |
