"""KAN-105 (L0) : la matrice route × règle déclarée de l'API, figée dans `access_matrix.md`.

Un changement de droit (route nouvelle, règle changée, décision ajoutée dans le corps d'une
route) se voit ainsi en revue : `test_kan105_access_matrix` régénère la matrice et la compare au
fichier de référence. Si le changement est voulu, régénérer le fichier depuis la racine :

    uv run python -m tests.accounts.access_matrix > tests/accounts/access_matrix.md

Ce qui est relevé, sans envoyer une seule requête : les deux applications (mode local, mode
comptes) parcourues comme l'audit `test_b1c_guards` (mêmes routeurs inclus, mêmes gardes
héritées) plus les routes WebSocket ; pour chaque méthode × chemin, la règle déclarée
(`PROJECT_ROUTES`, `GLOBAL_ROUTES`, ou une dépendance `project_member`), les maillons en plus
(bail du plan, place du propriétaire, porte et débit du poste) et les décisions écrites dans le
corps de la route, repérées dans son source.
"""

from __future__ import annotations

import inspect
import re
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

from openplan.api.accounts.db import make_engine, upgrade
from openplan.api.accounts.extension import EXTENSION_PATH
from openplan.api.accounts.lease import IMAGE_LEASED, LEASED_ROUTES, PROJECT_WIDE
from openplan.api.accounts.quota import LAUNCHES, UPLOADS
from openplan.api.app import create_app
from openplan.api.guards import EXTENSION_ROLES, GLOBAL_ROUTES, PROJECT_ROUTES, ProjectMember
from openplan.api.policy.registry import (
    AUTH_RATE,
    BY_KEY,
    FEATURE_SHARE_LINKS,
    OFFER_PRO,
    OFFER_PRO_OPS,
    SPACE_LINK,
)
from openplan.api.settings import Settings
from openplan.api.worker_auth import RATE_LIMITED, WORKER_PATH
from tests.accounts.helpers import accounts_settings
from tests.accounts.test_b1c_guards import _guards, _walk

REFERENCE = Path(__file__).with_name("access_matrix.md")
BODY_CHECKS = {
    "allowed_user": r"\ballowed_user\(",
    # KAN-99 : rendus visibles de tout membre, refusés seulement si le rendu est désactivé.
    "member_user": r"\bmember_user\(",
    "require_role": r"\brequire_role\(",
    "role_of": r"\brole_of\(",
    "signed_in_user": r"\bsigned_in_user\(",
    "propriétaire": r"_own_done\(|\.user_id != user\.id|\.author != me|\.owner != ",
    "place": r"\bfull\(|used_bytes\(",
}
# KAN-105 L2 : un refus s'écrit aussi `Refusal(403, …)` (avec sa raison) ; 503 = service absent.
REFUSAL = re.compile(r"(?:status_code=|Refusal\()(40[134]|409|410|413|429|503)")
FIELDS = ("méthode", "chemin", "applis", "règle déclarée", "maillons en plus", "corps", "refus")
HEADER = """# Matrice des droits de l'API (KAN-105, L0)

Fichier généré par `tests/accounts/access_matrix.py`, comparé par `test_kan105_access_matrix` :
ne pas l'éditer à la main. Colonnes : la règle déclarée (rôle minimal du membre, ou politique
globale), les maillons en plus (fonction activée, bail du plan B2d, place KAN-12, débit des
tentatives, porte et débit du poste), les
décisions repérées dans le corps de la route, et les codes de refus qui y sont écrits.
"""


def _rule(key: tuple[str, str], guards: list[object]) -> str:
    if key in PROJECT_ROUTES:
        return f"membre ≥ {PROJECT_ROUTES[key].name.lower()}"
    if key in GLOBAL_ROUTES:
        return GLOBAL_ROUTES[key]
    members = [g for g in guards if isinstance(g, ProjectMember)]
    if members:
        return f"membre ≥ {members[0].role.name.lower()} (dépendance)"
    return "AUCUNE (refusée avec les comptes)" if guards else "SANS GARDE"


def _links(key: tuple[str, str]) -> str:
    _method, path = key
    declared = BY_KEY[key].links if key in BY_KEY else ()
    out = []
    if FEATURE_SHARE_LINKS in declared:
        out.append("fonction activée (404 pour tous)")
    # KAN-126 : offres Essentiel et Pro (offre de la personne qui agit).
    if OFFER_PRO in declared:
        out.append("offre Pro (403 offer_pro)")
    if OFFER_PRO_OPS in declared:
        out.append("offre Pro pour les ajouts Pro et Pousser/Tirer (403 offer_pro)")
    if key in LEASED_ROUTES or key in IMAGE_LEASED or key in PROJECT_WIDE:
        out.append("bail du plan (409)")
    if key in UPLOADS or key in LAUNCHES:
        out.append("place (403)")
    if SPACE_LINK in declared and not path.startswith("/api/extension/"):
        out.append("place : image par lien (403)")
    if AUTH_RATE in declared:
        out.append("débit des tentatives 10 / min (429)")
    if path.startswith("/api/worker/"):
        sample = path.replace("{request_id}", "00000000-0000-0000-0000-000000000000")
        # Exemples de valeurs des paramètres : un fichier (`{name}`), une image de style (KAN-97).
        sample = sample.replace("{name}", "bake").replace("{index}", "0")
        exact = WORKER_PATH.fullmatch(sample) is not None
        out.append("porte du poste" if exact else "HORS porte du poste")
    if path in RATE_LIMITED:
        out.append("débit 1 / 5 s (429)")
    if path.startswith("/api/extension/"):
        # KAN-102 : porte du module navigateur (son jeton), rôle et place exigés par la garde.
        sample = path.replace("{project_id}", "00000000-0000-0000-0000-000000000000")
        exact = EXTENSION_PATH.fullmatch(sample) is not None
        out.append("porte de l'extension" if exact else "HORS porte de l'extension")
        if key in EXTENSION_ROLES:
            out.append(f"membre ≥ {EXTENSION_ROLES[key].name.lower()} ; place (403)")
    return " ; ".join(out)


def _body(endpoint: object) -> tuple[str, str]:
    try:
        source = inspect.getsource(endpoint)  # type: ignore[arg-type]
    except (OSError, TypeError):
        return "", ""
    found = [name for name, pattern in BODY_CHECKS.items() if re.search(pattern, source)]
    return ", ".join(found), " ".join(sorted(set(REFUSAL.findall(source))))


def _websockets(routes: list[object]) -> list[APIWebSocketRoute]:
    """Les routes WebSocket, routeurs inclus compris (hors de l'audit `_walk`, HTTP seulement)."""
    out: list[APIWebSocketRoute] = []
    for r in routes:
        if isinstance(r, APIWebSocketRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out.extend(_websockets(list(r.original_router.routes)))
        elif hasattr(r, "routes"):
            out.extend(_websockets(list(r.routes)))
    return out


def rows(apps: dict[str, FastAPI]) -> list[dict[str, str]]:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for label, app in apps.items():
        for path, route, inherited in _walk(list(app.routes), "", []):
            for method in sorted(route.methods or ()):
                key = (method, path)
                if key in seen:
                    seen[key]["applis"] += f"+{label}"
                    continue
                body, refused = _body(route.endpoint)
                seen[key] = dict(
                    zip(
                        FIELDS,
                        (
                            method,
                            path,
                            label,
                            _rule(key, _guards(route, inherited)),
                            _links(key),
                            body,
                            refused,
                        ),
                        strict=True,
                    )
                )
        for socket in _websockets(list(app.routes)):
            if ("WS", socket.path) not in seen:
                body, refused = _body(socket.endpoint)
                values = ("WS", socket.path, label, "dans la route", "", body, refused)
                seen[("WS", socket.path)] = dict(zip(FIELDS, values, strict=True))
    kept = [seen[k] for k in sorted(seen, key=lambda k: (k[1], k[0]))]
    return [r for r in kept if r["chemin"].startswith(("/api", "/ws"))]


def render(apps: dict[str, FastAPI]) -> str:
    table = rows(apps)
    counts: dict[str, int] = {}
    for r in table:
        head = r["règle déclarée"].split(" (")[0]
        counts[head] = counts.get(head, 0) + 1
    lines = [HEADER, f"{len(table)} routes ; par règle : "]
    lines[-1] += ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ".\n"
    lines.append("| " + " | ".join(FIELDS) + " |")
    lines.append("|" + "---|" * len(FIELDS))
    lines += ["| " + " | ".join(r[f].replace("|", "\\|") for f in FIELDS) + " |" for r in table]
    return "\n".join(lines) + "\n"


def build(root: Path, url: str) -> dict[str, FastAPI]:
    """Les deux applications, comme l'audit : mode local, et mode comptes sur `url` (migrée)."""
    local = create_app(settings=Settings(data_dir=root / "local"))
    accounts = create_app(settings=accounts_settings(root / "comptes", url))
    return {"local": local, "comptes": accounts}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kan105-") as tmp:
        root = Path(tmp)
        url = f"sqlite:///{root / 'comptes.db'}"
        engine = make_engine(url)
        upgrade(engine)
        engine.dispose()
        apps = build(root, url)
        sys.stdout.write(render(apps))
        apps["comptes"].state.accounts.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
