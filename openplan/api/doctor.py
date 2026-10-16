"""`openplan doctor` (cadrage 0.4, 4.10a) : ce qui marche sur cette installation, et **la
commande pour chaque manque**. Même bilan en ligne de commande, par `GET /api/doctor` et sur
l'accueil du front. Aucun téléchargement, aucune modification, aucune donnée envoyée : les
seules sondes sont locales (dossier inscriptible, port libre) ou une connexion TCP d'une
seconde vers le serveur de modèle configuré (sans requête HTTP)."""

from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field

from openplan import __version__, monitoring
from openplan.adapters import materials
from openplan.adapters.neural import weights
from openplan.adapters.registry import load_registry, resolve_device
from openplan.api.settings import LOCAL_HOSTS, Settings
from openplan.brand import APP_NAME
from openplan.pipeline.blender_bridge import find_blender
from openplan.schemas.common import StrictModel

CheckStatus = Literal["ok", "missing", "info"]
PYTHON_MIN = (3, 12)
PROBE_TIMEOUT_S = 1.0


class DoctorCheck(StrictModel):
    key: str
    label: str
    status: CheckStatus
    detail: str
    fix: str | None = None
    """La commande ou le réglage qui répare, en une ligne ; None quand tout va bien."""
    required: bool = False
    """Sans cela OpenPlan ne démarre pas (Python, dossier des données)."""


class DoctorReport(StrictModel):
    version: str
    ok: bool
    """Aucun manque parmi les vérifications requises."""
    checks: list[DoctorCheck] = Field(default_factory=list)

    @property
    def missing(self) -> list[DoctorCheck]:
        return [c for c in self.checks if c.status == "missing"]


def _python() -> DoctorCheck:
    v = sys.version_info
    ok = (v.major, v.minor) >= PYTHON_MIN
    return DoctorCheck(
        key="python",
        label="Python",
        status="ok" if ok else "missing",
        detail=f"Python {v.major}.{v.minor}.{v.micro}",
        fix=None if ok else "installer Python 3.12 : `uv python install 3.12`",
        required=True,
    )


def _data_dir(path: Path) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return DoctorCheck(
            key="data",
            label="Dossier des projets",
            status="missing",
            detail=f"{path} : {exc.strerror or exc}",
            fix="choisir un dossier inscriptible : OPENPLAN_DATA_DIR=/chemin (ou --data-dir)",
            required=True,
        )
    return DoctorCheck(
        key="data", label="Dossier des projets", status="ok", detail=str(path), required=True
    )


def _web(web_dir: Path | None) -> DoctorCheck:
    if web_dir is not None and (web_dir / "index.html").is_file():
        return DoctorCheck(key="web", label="Front construit", status="ok", detail=str(web_dir))
    return DoctorCheck(
        key="web",
        label="Front construit",
        status="missing",
        detail=f"{web_dir or 'apps/web/dist'} sans index.html",
        fix="`make start` (construit puis sert) ou `cd apps/web && npm run build`",
    )


def _port(host: str, port: int) -> DoctorCheck:
    bind_host = "127.0.0.1" if host in LOCAL_HOSTS else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
    except OSError:
        return DoctorCheck(
            key="port",
            label="Port",
            status="info",
            detail=f"{port} déjà utilisé : {APP_NAME} tourne peut-être déjà",
            fix=f"ouvrir http://localhost:{port}/ ; sinon OPENPLAN_PORT=8010 (ou --port)",
        )
    return DoctorCheck(key="port", label="Port", status="ok", detail=f"{port} libre")


def _device(requested: str) -> DoctorCheck:
    device, label = resolve_device(requested)
    torch_missing = "torch absent" in label
    return DoctorCheck(
        key="device",
        label="Calcul des modèles locaux",
        status="ok" if device == "cuda" else "info",
        detail=label,
        fix=(
            "`make setup-neural` installe torch (profondeur, combles, ouvertures, inspiration)"
            if torch_missing
            else None
        ),
    )


def _blender(hint: str) -> DoctorCheck:
    found = find_blender(hint)
    if found is not None:
        return DoctorCheck(key="blender", label="Blender", status="ok", detail=str(found))
    return DoctorCheck(
        key="blender",
        label="Blender",
        status="missing",
        detail="introuvable (rendus, vignettes 3D et .blend indisponibles)",
        fix="installer Blender 4.2 (https://www.blender.org/download/) puis "
        "OPENPLAN_BLENDER_BIN=/chemin/vers/blender",
    )


def _weights(models_dir: Path) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    for entry in load_registry():
        if entry.kind != "weights":
            continue
        installed = weights.is_installed(entry.id, models_dir, entry.sentinel)
        accepted = (
            weights.license_accepted(entry.id, models_dir)
            if entry.requires_license_acceptance
            else True
        )
        size = f" ({entry.weights_mb:.0f} Mo)" if entry.weights_mb else ""
        command = f"uv run openplan models pull {entry.id}"
        if entry.requires_license_acceptance:
            command += " --accept-license"
        if installed and accepted:
            out.append(
                DoctorCheck(
                    key=f"weights:{entry.id}",
                    label=f"Poids {entry.role}",
                    status="ok",
                    detail=f"{entry.id} installé",
                )
            )
        else:
            why = "poids absents" if not installed else "licence à accepter"
            out.append(
                DoctorCheck(
                    key=f"weights:{entry.id}",
                    label=f"Poids {entry.role}",
                    status="missing",
                    detail=f"{entry.id}{size} : {why} ({entry.license})",
                    fix=command,
                )
            )
    return out


def _materials(materials_dir: Path) -> DoctorCheck:
    """Matières de rendu CC0 (D2) : facultatives, le rendu a ses matières procédurales."""
    registry = materials.load_materials()
    installed = [e for e in registry.materials if materials.is_installed(e, materials_dir)]
    total = len(registry.materials)
    size = sum(e.megabytes for e in registry.materials)
    if len(installed) == total:
        return DoctorCheck(
            key="materials",
            label="Matières de rendu",
            status="ok",
            detail=f"{total} matières CC0 installées (bois, pierre, enduit, ciel)",
        )
    return DoctorCheck(
        key="materials",
        label="Matières de rendu",
        status="info",
        detail=(
            f"{len(installed)}/{total} matières CC0 installées : le rendu utilise sinon ses "
            f"matières dessinées ({size:.0f} Mo en tout, Poly Haven)"
        ),
        fix="uv run openplan materials pull --all",
    )


def _ai(settings: Settings, probe: bool) -> DoctorCheck:
    ai = settings.ai
    if not ai.configured:
        return DoctorCheck(
            key="ai",
            label="Assistant IA",
            status="info",
            detail="non configuré (facultatif : brief, critique, perception)",
            fix="gratuit et local : Ollama puis OPENPLAN_AI_BASE_URL=http://127.0.0.1:11434/v1 "
            "et OPENPLAN_AI_MODEL=qwen2.5vl:3b dans .env",
        )
    parsed = urlparse(ai.base_url or "")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    where = "local" if host in LOCAL_HOSTS else "distant, consentement par projet"
    detail = f"{ai.model} @ {host} ({where})"
    if probe and host:
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_S):
                pass
        except OSError:
            return DoctorCheck(
                key="ai",
                label="Assistant IA",
                status="missing",
                detail=f"{detail} : serveur injoignable",
                fix=(
                    "démarrer le serveur du modèle (ex. `~/opt/ollama/bin/ollama serve`)"
                    if host in LOCAL_HOSTS
                    else "vérifier la connexion réseau et OPENPLAN_AI_BASE_URL"
                ),
            )
    return DoctorCheck(key="ai", label="Assistant IA", status="ok", detail=detail)


def _access(settings: Settings) -> DoctorCheck:
    if settings.local_only:
        return DoctorCheck(
            key="access",
            label="Accès",
            status="ok",
            detail=f"API sur {settings.host} seulement : sans jeton, injoignable du réseau",
        )
    count = len(settings.tokens)
    if count == 0:
        return DoctorCheck(
            key="access",
            label="Accès",
            status="missing",
            detail=f"API exposée sur {settings.host} sans jeton : le serveur refusera de démarrer",
            fix="`uv run openplan token <nom>` puis OPENPLAN_API_TOKENS dans .env",
            required=True,
        )
    return DoctorCheck(
        key="access",
        label="Accès",
        status="ok",
        detail=f"exposée sur {settings.host}, {count} jeton(s) nommé(s), journal "
        f"{settings.data_dir / 'access.jsonl'}",
    )


def _monitoring() -> DoctorCheck:
    """Suivi des erreurs (D4) : inactif sans OPENPLAN_SENTRY_DSN, et alors rien ne part."""
    import importlib.util

    value = monitoring.dsn()
    if value is None:
        return DoctorCheck(
            key="monitoring",
            label="Suivi des erreurs",
            status="info",
            detail="inactif : aucune erreur n'est envoyée hors de cette machine",
        )
    host = monitoring.dsn_host(value)
    if importlib.util.find_spec("sentry_sdk") is None:
        return DoctorCheck(
            key="monitoring",
            label="Suivi des erreurs",
            status="missing",
            detail=f"{monitoring.DSN_ENV} défini (vers {host}) mais sentry-sdk absent",
            fix="installer l'extra monitoring : `uv sync --extra monitoring`",
        )
    return DoctorCheck(
        key="monitoring",
        label="Suivi des erreurs",
        status="ok",
        detail=f"actif, vers {host}",
    )


def diagnose(
    settings: Settings, *, web_dir: Path | None = None, probe_ai: bool = True
) -> DoctorReport:
    """Le bilan complet, dans l'ordre où un nouvel utilisateur en a besoin."""
    checks = [
        _python(),
        _data_dir(settings.data_dir),
        _web(web_dir),
        _port(settings.host, settings.port),
        _device(settings.device),
        _blender(settings.blender_bin),
        *_weights(settings.models_dir),
        _materials(settings.materials_dir),
        _ai(settings, probe_ai),
        _access(settings),
        _monitoring(),
    ]
    ok = not any(c.required and c.status == "missing" for c in checks)
    return DoctorReport(version=__version__, ok=ok, checks=checks)


def render_text(report: DoctorReport) -> str:
    """Le bilan lisible en console : un état par ligne, la commande dessous quand il manque."""
    marks = {"ok": "[OK]", "missing": "[!!]", "info": "[--]"}
    lines = [f"{APP_NAME} {report.version} : bilan de l'installation"]
    for check in report.checks:
        lines.append(f"{marks[check.status]} {check.label} : {check.detail}")
        if check.fix:
            lines.append(f"       → {check.fix}")
    missing = report.missing
    if report.ok and not missing:
        lines.append("Tout est là.")
    elif report.ok:
        lines.append(
            f"{APP_NAME} démarre ; {len(missing)} option(s) à installer si vous en avez besoin."
        )
    else:
        lines.append(f"{APP_NAME} ne peut pas démarrer tant que les lignes [!!] requises restent.")
    return "\n".join(lines)
