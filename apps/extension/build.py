"""KAN-102 : construit les zips du module « Ajouter à PlanDesign » (bibliothèque standard de
Python seulement : aucun outil absent du dépôt).

    python3 apps/extension/build.py                       # Chrome et Firefox, app.plandesign.fr
    python3 apps/extension/build.py --target chrome
    python3 apps/extension/build.py --origin http://localhost:5173   # essai local

Sortie : apps/extension/dist/plandesign-<cible>-<version>.zip. Chaque zip a un manifeste propre
à son navigateur (service worker pour Chrome, script d'arrière-plan et identifiant pour
Firefox) ; `--origin` remplace l'hôte de l'application dans le manifeste (`host_permissions`)
et dans config.js. Rien n'est publié : la publication sur les stores est une décision d'augus.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
FILES = (
    "background.js",
    "choose.html",
    "choose.js",
    "config.js",
    "lib.js",
    "options.html",
    "options.js",
    "style.css",
)
DEFAULT_ORIGIN = "https://app.plandesign.fr"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
"""Date posée sur chaque entrée : deux constructions des mêmes sources donnent le même zip."""


def checked_origin(raw: str) -> str:
    parts = urlsplit(raw.strip())
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local):
        msg = "origine : https://… (http:// seulement pour localhost ou 127.0.0.1)"
        raise SystemExit(msg)
    if not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        msg = "origine : schéma, hôte et port seulement (https://app.plandesign.fr)"
        raise SystemExit(msg)
    return f"{parts.scheme}://{parts.netloc}"


def manifest_for(target: str, origin: str) -> dict[str, object]:
    manifest: dict[str, object] = json.loads((HERE / "manifest.json").read_text("utf-8"))
    manifest["host_permissions"] = [f"{origin}/*"]
    if target == "chrome":
        manifest["background"] = {"service_worker": "background.js"}
        manifest.pop("browser_specific_settings", None)
    else:
        manifest["background"] = {"scripts": ["background.js"]}
    return manifest


def config_for(origin: str) -> str:
    source = (HERE / "config.js").read_text("utf-8")
    replaced, count = re.subn(r'APP_ORIGIN = "[^"]*"', f'APP_ORIGIN = "{origin}"', source, count=1)
    if count != 1:
        msg = "config.js : APP_ORIGIN introuvable"
        raise SystemExit(msg)
    return replaced


def _write(archive: zipfile.ZipFile, name: str, data: str) -> None:
    info = zipfile.ZipInfo(name, FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data.encode("utf-8"))


def build(target: str, origin: str, out_dir: Path) -> Path:
    manifest = manifest_for(target, origin)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"plandesign-{target}-{manifest['version']}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        _write(archive, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for name in FILES:
            text = config_for(origin) if name == "config.js" else (HERE / name).read_text("utf-8")
            _write(archive, name, text)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--target", choices=("chrome", "firefox", "all"), default="all")
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    args = parser.parse_args(argv)
    origin = checked_origin(args.origin)
    targets = ("chrome", "firefox") if args.target == "all" else (args.target,)
    for target in targets:
        sys.stdout.write(f"{build(target, origin, args.out)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
