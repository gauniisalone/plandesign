# Installer PlanDesign en dix minutes

Pour quelqu'un qui n'a jamais vu le dépôt : Linux, macOS ou Windows avec WSL2 (Debian ou
Ubuntu). Rien ne quitte votre machine ; aucun compte, aucune carte, aucun paiement.

## 1. uv (une minute)

`uv` installe Python et le paquet dans un dossier à vous, sans droits d'administrateur :

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Fermez et rouvrez le terminal (ou suivez la ligne affichée) pour que `uv` soit trouvé.

## 2. PlanDesign (deux à quatre minutes selon la connexion)

```bash
uv tool install "openplan-studio[app]"
```

Le paquet contient l'interface web déjà construite : pas besoin de Node ni de cloner le dépôt.
Tant que le paquet n'est pas publié sur PyPI, la même commande accepte le fichier `.whl` produit
par `make build` : `uv tool install "dist/openplan_studio-0.1.0.dev0-py3-none-any.whl[app]"`.

## 3. Le bilan, puis le lien (une minute)

```bash
openplan doctor
openplan serve --web
```

`doctor` dit ce qui est là et **la commande pour chaque manque** ; `serve --web` affiche le lien
à ouvrir, `http://localhost:8000/`. L'API n'écoute que sur votre machine. Le même bilan est
replié en bas de l'accueil (« État de l'installation »). `Ctrl+C` arrête le serveur ; vos
projets restent dans le dossier `data/` du répertoire courant (`OPENPLAN_DATA_DIR` pour en
choisir un autre).

## 4. Ce que vous pouvez faire tout de suite, et ce qui demande plus

| Tout de suite (cœur) | Après une installation optionnelle |
|---|---|
| tracer et éditer un plan, importer un plan DXF / PDF / SVG / image, proposer un plan depuis une photo (lignes et points de fuite), calibrer, pièces, meubles du catalogue, brief à la main, dispositions, remarques des règles, exports SVG / PDF / DXF / GLB / JSON, aperçu 3D dans le navigateur | **Blender** (rendus, vignettes 3D, .blend) : installer Blender 4.2 et `OPENPLAN_BLENDER_BIN=/chemin/blender` ; **modèles locaux** (profondeur, combles depuis la photo, portes et fenêtres, image d'inspiration) : torch puis `openplan models pull <id>` (les poids se téléchargent explicitement, jamais tout seuls) ; **assistant IA** (brief depuis une phrase, avis du modèle sur une variante) : Ollama local gratuit ou un service distant avec consentement par projet |

`openplan doctor` donne la commande exacte de chacun.

## 5. Mise à jour, désinstallation

```bash
uv tool upgrade openplan-studio
uv tool uninstall openplan-studio    # vos projets (data/) ne sont pas touchés
```

## Chronomètre

Mesuré sur la machine de développement (WSL2 Debian, connexion domestique) à partir d'un
environnement Python vierge, le 28 septembre 2026 : wheel de 552 Ko avec le front dedans ;
installation des dépendances dans le nouvel environnement en 1 s avec le cache d'uv (compter
1 à 3 minutes de téléchargement la première fois : FastAPI, OpenCV, Pillow, Shapely, trimesh,
reportlab, ezdxf…) ; `doctor` immédiat ; `serve --web` répond en 3 s, accueil et catalogue
servis. Le téléchargement de Blender (≈ 300 Mo) et des poids (95 Mo à 2,7 Go) n'y est pas compté.
