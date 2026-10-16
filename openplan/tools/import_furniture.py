"""Importe les modèles et vignettes du catalogue depuis le zip officiel de Kenney.

    uv run python -m openplan.tools.import_furniture <chemin/kenney_furniture-kit.zip>

Vérifie l'empreinte du zip (celle qui a été contrôlée avec sa licence), copie pour chaque
meuble du catalogue le GLB et la vignette isométrique (vue de face, « SW ») dans
`assets/furniture/`, puis réécrit la liste des fichiers du registre des sources. Rien n'est
téléchargé : le zip est fourni à la main, après lecture de la licence sur la page officielle.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

import yaml

from openplan.scene.catalog import CATALOG_PATH, FURNITURE_DIR, SOURCES_PATH

SOURCE_ID = "kenney_furniture_kit"
GLB_DIR = "Models/GLTF format/"
PREVIEW_SUFFIX = "_SW.png"
DECOR_MODELS = ("lampRoundTable", "plantSmall1", "pottedPlant", "pillow", "books")
"""Habillage du rendu (D4, C3) : lampe de chevet, plantes, coussin, livres ; pas au catalogue."""
"""Orientation des vignettes isométriques de Kenney qui montre la face avant du meuble."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    sources = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    entry = next(s for s in sources["sources"] if s["id"] == SOURCE_ID)
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if digest != entry["archive_sha256"]:
        print(f"empreinte inattendue : {digest} (attendue {entry['archive_sha256']})")
        return 1
    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    files: list[str] = []
    with zipfile.ZipFile(args.archive) as zf:
        names = zf.namelist()
        for item in catalog["assets"]:
            for key, pick in (("model", _glb), ("preview", _preview)):
                relative = item.get(key)
                if not relative:
                    continue
                member = pick(names, Path(relative).stem)
                target = FURNITURE_DIR / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
                files.append(relative)
        for stem in DECOR_MODELS:
            relative = f"models/{stem}.glb"
            target = FURNITURE_DIR / relative
            target.write_bytes(zf.read(_glb(names, stem)))
            files.append(relative)
        licence = next(n for n in names if Path(n).name.lower() == "license.txt")
        (FURNITURE_DIR / "LICENSE-kenney-furniture-kit.txt").write_bytes(zf.read(licence))
    entry["files"] = sorted(set(files))
    SOURCES_PATH.write_text(
        yaml.safe_dump(sources, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
    )
    print(f"{len(entry['files'])} fichiers copiés dans {FURNITURE_DIR}")
    return 0


def _glb(names: list[str], stem: str) -> str:
    return next(n for n in names if n.startswith(GLB_DIR) and Path(n).name == f"{stem}.glb")


def _preview(names: list[str], stem: str) -> str:
    return next(n for n in names if Path(n).name == f"{stem}{PREVIEW_SUFFIX}")


if __name__ == "__main__":
    sys.exit(main())
