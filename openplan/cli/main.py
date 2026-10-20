"""Ligne de commande : `openplan serve`, `openplan schemas`, `openplan export`."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from openplan import __version__
from openplan.api.settings import Settings
from openplan.brand import APP_NAME
from openplan.pipeline.blender_bridge import run_blender
from openplan.pipeline.export import export_dxf, export_json, export_pdf, export_svg
from openplan.pipeline.scene3d import export_bake, export_glb
from openplan.scene.migrate import load_project
from openplan.schemas.jsonschema_export import export_json_schemas
from openplan.schemas.scene import Project

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import Engine


def _add_bench_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    bench = sub.add_parser(
        "bench",
        help="banc KAN-33 : scène de contrôle + photos réelles (règle connue) → erreurs",
    )
    bench.add_argument(
        "root", type=Path, nargs="?", default=Path("data/eval"), help="défaut : data/eval"
    )
    bench.add_argument(
        "--compute-depth",
        action="store_true",
        help="calcule la profondeur manquante (modèle, GPU si présent) et l'enregistre",
    )
    bench.add_argument("--models-dir", type=Path, help="défaut : OPENPLAN_MODELS_DIR")


def _add_materials_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    materials = sub.add_parser(
        "materials", help="matières de rendu CC0 : liste, téléchargement (action explicite)"
    )
    materials_sub = materials.add_subparsers(dest="materials_command", required=True)
    materials_sub.add_parser("list", help="matières du registre et matières installées")
    mpull = materials_sub.add_parser("pull", help="télécharge des matières (empreinte vérifiée)")
    mpull.add_argument("ids", nargs="*", help="identifiants du registre (défaut : aucun)")
    mpull.add_argument("--all", action="store_true", help="toutes les matières du registre")


def _add_journal_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    journal = sub.add_parser(
        "journal", help="journal local des demandes (V2) : export JSONL, rien ne quitte la machine"
    )
    journal_sub = journal.add_subparsers(dest="journal_command", required=True)
    jexport = journal_sub.add_parser(
        "export", help="écrit les lignes du journal sur la sortie standard (JSONL)"
    )
    jexport.add_argument("project", nargs="?", help="identifiant du projet")
    jexport.add_argument("--all", action="store_true", help="tous les projets du dossier")
    jexport.add_argument("--data-dir", type=Path, help="défaut : OPENPLAN_DATA_DIR")
    jmetrics = journal_sub.add_parser(
        "metrics", help="indicateurs du parcours (P0), un projet par ligne (JSONL)"
    )
    jmetrics.add_argument("project", nargs="?", help="identifiant du projet")
    jmetrics.add_argument("--all", action="store_true", help="tous les projets du dossier")
    jmetrics.add_argument("--data-dir", type=Path, help="défaut : OPENPLAN_DATA_DIR")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openplan", description=APP_NAME)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="lance l'API locale (127.0.0.1 par défaut)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--data-dir", type=Path)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--web",
        action="store_true",
        help="sert aussi le front construit (apps/web/dist) sur / : un seul port, un lien",
    )
    serve.add_argument(
        "--web-dir", type=Path, help="dossier du front construit (défaut : apps/web/dist)"
    )

    schemas = sub.add_parser("schemas", help="exporte les JSON Schema des modèles publics")
    schemas.add_argument("out_dir", type=Path)

    export = sub.add_parser("export", help="exporte un projet .json en SVG, JSON, GLB ou bake")
    export.add_argument("project_file", type=Path)
    export.add_argument(
        "--format", choices=["svg", "pdf", "dxf", "json", "glb", "bake"], default="svg"
    )
    export.add_argument("--level", type=UUID, help="identifiant du niveau (défaut : le premier)")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument(
        "--share",
        action="store_true",
        help="JSON à partager : sans historique ni chemins d'images (format json seulement)",
    )

    blender = sub.add_parser("blender", help="construit .blend, GLB et aperçu via Blender headless")
    blender.add_argument("project_file", type=Path)
    blender.add_argument("--level", type=UUID)
    blender.add_argument("--out-dir", type=Path, required=True)
    blender.add_argument("--blender-bin")
    blender.add_argument("--no-preview", action="store_true")

    run = sub.add_parser(
        "run", help="photo → proposition de plan (heuristique) → calibration → exports"
    )
    run.add_argument("image", type=Path, help="JPEG, PNG ou WebP local")
    run.add_argument("--out", type=Path, required=True, help="dossier du projet créé")
    run.add_argument("--name", help="nom du projet (défaut : nom du fichier)")
    run.add_argument(
        "--measure",
        type=float,
        help="longueur réelle (m) du mur de face détecté, pour calibrer le plan",
    )
    run.add_argument("--camera-height", type=float, default=1.5, help="hauteur de prise de vue (m)")

    evaluate = sub.add_parser(
        "evaluate",
        help="photos réelles mesurées (dossier par pièce) → erreurs mesurées, pièce par pièce",
    )
    evaluate.add_argument("root", type=Path, help="racine : un sous-dossier par pièce")
    evaluate.add_argument("--no-depth", action="store_true", help="sans profondeur métrique")
    evaluate.add_argument(
        "--no-openings", action="store_true", help="sans détection des portes et fenêtres"
    )
    evaluate.add_argument("--models-dir", type=Path, help="défaut : OPENPLAN_MODELS_DIR")

    _add_bench_parser(sub)

    models = sub.add_parser("models", help="registre des modèles : liste, téléchargement des poids")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    listing = models_sub.add_parser("list", help="modèles du registre et poids installés")
    listing.add_argument("--models-dir", type=Path, help="défaut : OPENPLAN_MODELS_DIR")
    pull = models_sub.add_parser("pull", help="télécharge les poids d'un modèle (action explicite)")
    pull.add_argument("model_id")
    pull.add_argument("--models-dir", type=Path, help="défaut : OPENPLAN_MODELS_DIR")
    pull.add_argument(
        "--accept-license",
        action="store_true",
        help="accepte la licence des poids (OpenRAIL-M…) ; exigé pour certains modèles",
    )

    _add_materials_parser(sub)

    token = sub.add_parser(
        "token", help="génère un jeton d'accès nommé pour l'API exposée (à mettre dans .env)"
    )
    token.add_argument(
        "name", help="nom de la personne (lettres, chiffres, . _ -) : acteur user:<nom>"
    )

    _add_journal_parser(sub)
    _add_diagnostic_parsers(sub)
    _add_accounts_parser(sub)
    return parser


def _add_accounts_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    accounts = sub.add_parser(
        "accounts", help="base des comptes (OPENPLAN_DATABASE_URL, extra comptes) : migrations"
    )
    accounts_sub = accounts.add_subparsers(dest="accounts_command", required=True)
    accounts_sub.add_parser("migrate", help="applique les migrations (alembic upgrade head)")
    accounts_sub.add_parser("status", help="révision de la base et dernière migration")
    imp = accounts_sub.add_parser(
        "import",
        help="copie des projets d'un dossier local vers le serveur à comptes (propriétaire donné)",
    )
    imp.add_argument("source", type=Path, help="dossier des projets (un sous-dossier par projet)")
    imp.add_argument("--owner", required=True, help="adresse du propriétaire (compte existant)")
    imp.add_argument(
        "--only", nargs="+", default=[], metavar="ID", help="seulement ces projets (id ou 8 car.)"
    )
    imp.add_argument("--yes", action="store_true", help="sans confirmation, projet par projet")


def _accounts(args: argparse.Namespace) -> int:
    """`openplan accounts migrate|status` : jamais l'URL de la base sur la sortie."""
    settings = Settings.from_env()
    if not settings.database_url:
        print("OPENPLAN_DATABASE_URL n'est pas défini : mode local, sans base des comptes.")
        return 2
    try:
        from openplan.api.accounts import db
    except ImportError:
        print("extra « comptes » absent : uv sync --extra comptes")
        return 2
    engine = db.make_engine(settings.database_url)
    if args.accounts_command == "import":
        try:
            return _accounts_import(args, settings, engine)
        finally:
            engine.dispose()
    try:
        if args.accounts_command == "migrate":
            db.upgrade(engine)
        current, head = db.current_revision(engine), db.head_revision()
    finally:
        engine.dispose()
    print(f"base des comptes : révision {current or 'vide'} (dernière migration : {head})")
    return 0 if current == head else 1


def _accounts_import(args: argparse.Namespace, settings: Settings, engine: Engine) -> int:
    """`openplan accounts import` : base migrée exigée, rien sans confirmation."""
    from openplan.api.accounts import db
    from openplan.api.accounts.importer import ImportRefusedError, run_import
    from openplan.api.accounts.repo import AccountsRepo
    from openplan.api.settings import project_root

    try:
        db.check_schema(engine)
        run_import(
            AccountsRepo(engine),
            args.owner,
            args.source,
            project_root(settings),
            only=args.only,
            yes=args.yes,
        )
    except (db.AccountsSchemaError, ImportRefusedError) as exc:
        print(f"import refusé : {exc}")
        return 2
    return 0


def _add_diagnostic_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = sub.add_parser(
        "doctor", help="bilan de l'installation : ce qui marche, et la commande pour chaque manque"
    )
    doctor.add_argument("--json", action="store_true", help="sortie JSON (même contenu)")
    doctor.add_argument(
        "--no-probe", action="store_true", help="sans sonde réseau vers le serveur de modèle"
    )
    sub.add_parser(
        "sentry-test",
        help="envoie une erreur volontaire au suivi des erreurs (exige OPENPLAN_SENTRY_DSN)",
    )


def _sentry_test(_: argparse.Namespace) -> int:
    """Vérifie la réception côté GlitchTip après un déploiement : une erreur volontaire, sans
    aucune donnée de projet."""
    from openplan import monitoring

    value = monitoring.dsn()
    if value is None:
        print(f"{monitoring.DSN_ENV} n'est pas défini : rien n'est envoyé.")
        return 2
    event_id = monitoring.send_test_error()
    if event_id is None:
        print("Suivi des erreurs inactif (sentry-sdk absent ?) : rien n'est parti.")
        return 1
    print(f"Erreur de test envoyée vers {monitoring.dsn_host(value)} : événement {event_id}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from openplan.api.doctor import diagnose, render_text

    settings = Settings.from_env()
    report = diagnose(
        settings, web_dir=settings.web_dir or default_web_dir(), probe_ai=not args.no_probe
    )
    print(report.model_dump_json(indent=2) if args.json else render_text(report))
    return 0 if report.ok else 1


class ExposedWithoutTokenError(ValueError):
    """`serve --host` hors boucle locale sans aucun jeton : refusé (4.6)."""


def _token(args: argparse.Namespace) -> int:
    """Un jeton aléatoire, jamais écrit ailleurs que sur la sortie standard."""
    from openplan.api.settings import _TOKEN_NAME

    name: str = args.name
    if not _TOKEN_NAME.match(name):
        print("nom invalide : 1 à 32 caractères parmi lettres, chiffres, . _ -")
        return 2
    value = secrets.token_urlsafe(24)
    print(f"Jeton pour « {name} » (à transmettre par un canal sûr, jamais dans une URL) :")
    print(f"OPENPLAN_API_TOKENS={name}:{value}")
    print(
        "Plusieurs personnes : séparer par des virgules (augus:…,marie:…). Révoquer = retirer "
        "l'entrée puis relancer `openplan serve`. L'historique du projet notera user:" + name
    )
    return 0


def _schemas(args: argparse.Namespace) -> int:
    for path in export_json_schemas(args.out_dir):
        print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "serve": _serve,
        "schemas": _schemas,
        "export": _export,
        "blender": _blender,
        "run": _run,
        "models": _models,
        "materials": _materials,
        "evaluate": _evaluate,
        "bench": _bench,
        "token": _token,
        "doctor": _doctor,
        "journal": _journal,
        "sentry-test": _sentry_test,
        "accounts": _accounts,
    }
    return commands[args.command](args)


def _journal(args: argparse.Namespace) -> int:
    """`openplan journal export <projet|--all> > demandes.jsonl` : les lignes du journal local,
    telles qu'écrites (schéma `JournalEvent` v1) ; aucune image, aucune coordonnée."""
    from openplan.pipeline.journal import export_lines

    root: Path = args.data_dir or Settings.from_env().data_dir
    if args.all:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    elif args.project:
        dirs = [root / args.project]
        if not dirs[0].is_dir():
            print(f"projet introuvable : {args.project}", file=sys.stderr)
            return 2
    else:
        print("donnez un identifiant de projet, ou --all", file=sys.stderr)
        return 2
    if args.journal_command == "metrics":
        return _journal_metrics(root, dirs)
    for line in export_lines(dirs):
        print(line)
    return 0


def _journal_metrics(root: Path, dirs: list[Path]) -> int:
    """`openplan journal metrics <projet|--all>` : temps jusqu'au premier rendu, gestes,
    onglets, « Affiner », rendus demandés, vues périmées (P0) ; calculé sur la machine."""
    from uuid import UUID

    from openplan.pipeline.metrics import project_metrics
    from openplan.scene.store import ProjectStore

    store = ProjectStore(root)
    for directory in dirs:
        try:
            project = store.load(UUID(directory.name))
        except (ValueError, FileNotFoundError):
            continue  # pas un dossier de projet
        print(
            project_metrics(
                project, store.project_dir(project.id), store.runs_dir(project.id)
            ).model_dump_json()
        )
    return 0


def _bench(args: argparse.Namespace) -> int:
    from openplan.pipeline.bench import run_bench, write_report

    root: Path = args.root
    if not root.is_dir():
        print(f"racine introuvable : {root}")
        return 2
    estimator = None
    if args.compute_depth:
        from openplan.adapters.registry import build_depth_estimator

        settings = Settings.from_env()
        estimator = build_depth_estimator(args.models_dir or settings.models_dir, settings.device)
    report = run_bench(root, estimator=estimator, progress=print)
    md, js = write_report(report, root)
    print(md)
    print(js)
    for key, value in report.medians.items():
        print(f"  médiane |erreur| {key} : {value * 100:.0f} %")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    import tempfile

    from openplan.adapters.neural.depth_anything import MODEL_ID as DEPTH_ID
    from openplan.adapters.neural.openings import MODEL_ID as OPENINGS_ID
    from openplan.adapters.neural.weights import is_installed
    from openplan.adapters.registry import build_depth_estimator, build_opening_detector
    from openplan.pipeline.evaluate import evaluate_root, write_report

    root: Path = args.root
    if not root.is_dir():
        print(f"racine introuvable : {root}")
        return 2
    settings = Settings.from_env()
    models_dir: Path = args.models_dir or settings.models_dir
    estimator = None
    if args.no_depth:
        print("profondeur désactivée (--no-depth)")
    elif is_installed(DEPTH_ID, models_dir):
        estimator = build_depth_estimator(models_dir, settings.device)
    else:
        print(f"poids de profondeur absents : uv run openplan models pull {DEPTH_ID}")
    detector = None
    if args.no_openings:
        print("portes et fenêtres désactivées (--no-openings)")
    elif is_installed(OPENINGS_ID, models_dir):
        detector = build_opening_detector(models_dir, settings.device)
    else:
        print(
            f"poids du détecteur d'ouvertures absents : uv run openplan models pull {OPENINGS_ID}"
        )
    with tempfile.TemporaryDirectory() as tmp:
        report = evaluate_root(root, Path(tmp), estimator=estimator, detector=detector)
    md, js = write_report(report, root)
    print(md)
    print(js)
    for room in report.rooms:
        print(
            f"{room.name}: {len(room.rows)} comparaison(s), {len(room.warnings)} avertissement(s)"
        )
    for key, value in sorted(report.medians.items()):
        print(f"  médiane |erreur| {key}: {value:.0f} %")
    return 0


def _materials(args: argparse.Namespace) -> int:
    """Matières de rendu (D2) : rien n'est téléchargé sans `pull` explicite."""
    from openplan.adapters import materials as store

    root = Settings.from_env().materials_dir
    registry = store.load_materials()
    if args.materials_command == "list":
        for entry in registry.materials:
            state = "installée" if store.is_installed(entry, root) else "absente"
            what = entry.finish or "ciel"
            print(f"{entry.id:40s} {what:13s} {entry.megabytes:5.1f} Mo  {entry.license}  {state}")
        print(f"dossier : {root} ; télécharger : uv run openplan materials pull --all")
        return 0
    chosen = registry.materials if args.all else [e for e in registry.materials if e.id in args.ids]
    unknown = set(args.ids) - {e.id for e in registry.materials}
    if unknown or not chosen:
        print(f"matière inconnue ou aucune choisie : {sorted(unknown)} (voir `materials list`)")
        return 2
    for entry in chosen:
        if store.is_installed(entry, root):
            print(f"{entry.id} : déjà installée")
            continue
        print(f"{entry.id} ({entry.megabytes:.1f} Mo, {entry.license}, {entry.page}) …")
        store.pull(entry, root)
    print(f"installées dans {root}")
    return 0


def _models(args: argparse.Namespace) -> int:
    from openplan.adapters.neural import weights
    from openplan.adapters.neural.download import hf_download
    from openplan.adapters.registry import load_registry, registry_entry

    models_dir: Path = (
        args.models_dir if getattr(args, "models_dir", None) else Settings.from_env().models_dir
    )
    if args.models_command == "list":
        for entry in load_registry():
            if entry.kind == "weights":
                installed = weights.is_installed(entry.id, models_dir, entry.sentinel)
                state = "installé" if installed else "à télécharger"
                if entry.requires_license_acceptance:
                    accepted = weights.license_accepted(entry.id, models_dir)
                    state += ", licence acceptée" if accepted else ", licence à accepter"
            elif entry.remote:
                state = "distant (consentement requis)"
            else:
                state = entry.status
            print(f"{entry.id:48s} {entry.role:24s} {entry.license:22s} {state}")
        return 0
    entry = registry_entry(args.model_id)
    if entry.kind != "weights" or not entry.hf_repo:
        print(f"{entry.id} n'a pas de poids à télécharger ({entry.kind})")
        return 2
    if not entry.commercial_ok:
        print(f"{entry.id} : usage commercial non autorisé, téléchargement refusé par défaut")
        return 2
    size = f"{entry.weights_mb:.0f} Mo" if entry.weights_mb else "taille inconnue"
    if entry.requires_license_acceptance and not args.accept_license:
        print(f"{entry.id} : poids sous licence {entry.license}, à lire et accepter avant tout")
        print(f"  licence : {entry.license_url or '(voir la fiche du modèle)'}")
        print(f"  {entry.notes}")
        print(f"  puis : uv run openplan models pull {entry.id} --accept-license")
        return 3
    print(f"téléchargement de {entry.hf_repo} ({size}, {entry.license}) → {models_dir}")
    target = weights.pull(
        entry.id,
        entry.hf_repo,
        models_dir,
        downloader=hf_download,
        sentinel=entry.sentinel,
        allow_patterns=entry.allow_patterns,
        license_name=entry.license if entry.requires_license_acceptance else None,
        accept_license=args.accept_license,
    )
    print(target)
    return 0


def serve_settings(args: argparse.Namespace, base: Settings | None = None) -> Settings:
    """Réglages du serveur : l'environnement (et .env), surchargé par les options de la ligne
    de commande. Les autres champs (Blender, IA...) sont conservés tels quels."""
    base = base or Settings.from_env()
    settings = replace(
        base,
        data_dir=args.data_dir or base.data_dir,
        host=args.host or base.host,
        port=args.port or base.port,
    )
    if not settings.local_only and not settings.tokens and not settings.accounts_enabled:
        # Hors boucle locale, jamais sans jeton, et jamais un jeton improvisé : refus dit.
        # Avec les comptes (note 17), la session remplace le jeton.
        msg = (
            f"API exposée sur {settings.host} sans jeton : refusé. Générer un jeton nommé avec "
            "`openplan token <nom>` puis le mettre dans OPENPLAN_API_TOKENS (.env) et relancer."
        )
        raise ExposedWithoutTokenError(msg)
    web_dir: Path | None = getattr(args, "web_dir", None)
    if web_dir is None and getattr(args, "web", False):
        web_dir = default_web_dir()
    if web_dir is not None:
        settings = replace(settings, web_dir=web_dir)
    return settings


def default_web_dir() -> Path:
    """`apps/web/dist` dans un dépôt (développement), sinon le front embarqué dans le paquet
    installé (`openplan/_bundled/web`, copié par `make build`)."""
    from openplan.resources import bundled_web_dir

    return bundled_web_dir()


def _serve(args: argparse.Namespace) -> int:
    import os

    from openplan.api.app import WebBuildMissingError, create_app

    base = Settings.from_env()
    try:
        settings = serve_settings(args, base)
    except ExposedWithoutTokenError as exc:
        print(str(exc))
        return 2
    # Dit une fois, au démarrage, où tourneront les modèles locaux (profondeur, inspiration).
    from openplan.adapters.registry import resolve_device

    print(f"Périphérique de calcul des modèles locaux : {resolve_device(settings.device)[1]}")
    if settings.accounts_enabled:
        print(
            f"Comptes actifs : connexion par lien e-mail (envoi « {settings.mailer} », "
            f"inscription « {settings.signup} »), sessions pd_session ; origine acceptée "
            f"{settings.public_origin}."
        )
        os.environ["OPENPLAN_HOST"] = settings.host
    elif not settings.local_only:
        names = ", ".join(sorted(settings.tokens))
        print(
            f"API exposée sur {settings.host} : jeton d'accès exigé (en-tête Authorization) ; "
            f"{len(settings.tokens)} jeton(s) nommé(s) : {names}. Journal des accès : "
            f"{settings.data_dir / 'access.jsonl'} (sans jeton ni contenu)."
        )
        # Le mode --reload reconstruit l'application depuis l'environnement.
        os.environ["OPENPLAN_HOST"] = settings.host
        os.environ["OPENPLAN_API_TOKENS"] = ",".join(f"{n}:{t}" for n, t in settings.tokens.items())
    if settings.web_dir is not None:
        if not (settings.web_dir / "index.html").is_file():
            print(
                f"front non construit : {settings.web_dir} ne contient pas index.html. "
                "Lancer `make start` (construit puis sert), ou `cd apps/web && npm run build`."
            )
            return 2
        os.environ["OPENPLAN_WEB_DIR"] = str(settings.web_dir)
        shown_host = "localhost" if settings.host in ("127.0.0.1", "::1") else settings.host
        print(f"{APP_NAME} : ouvrir http://{shown_host}:{settings.port}/ (Ctrl+C pour arrêter)")
    try:
        _run_server(args, settings, create_app)
    except WebBuildMissingError as exc:
        print(str(exc))
        return 2
    return 0


def _run_server(
    args: argparse.Namespace, settings: Settings, create_app: Callable[..., FastAPI]
) -> None:
    import uvicorn

    if args.reload:
        uvicorn.run(
            "openplan.api.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=True,
        )
    else:
        uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port)


def _load(path: Path) -> Project:
    return load_project(json.loads(path.read_text(encoding="utf-8")))


def _export(args: argparse.Namespace) -> int:
    project = _load(args.project_file)
    level_id = args.level or next(iter(project.levels))
    if args.format == "json":
        artifact = export_json(project, args.out, share=args.share)
    elif args.format == "glb":
        artifact = export_glb(project, level_id, args.out)
    elif args.format == "bake":
        artifact = export_bake(project, level_id, args.out)
    elif args.format == "pdf":
        artifact = export_pdf(project, level_id, args.out)
    elif args.format == "dxf":
        artifact = export_dxf(project, level_id, args.out)
    else:
        artifact = export_svg(project, level_id, args.out)
    print(artifact.path)
    return 0


def _blender(args: argparse.Namespace) -> int:
    project = _load(args.project_file)
    level_id = args.level or next(iter(project.levels))
    result = run_blender(
        project,
        level_id,
        args.out_dir,
        blender_bin=args.blender_bin or Settings.from_env().blender_bin,
        preview=not args.no_preview,
    )
    for artifact in result.artifacts:
        print(artifact.path)
    for warning in result.warnings:
        print(f"avertissement : {warning}")
    print(f"{result.model_ids[0]} en {result.duration_s:.1f} s")
    return 0


def _run(args: argparse.Namespace) -> int:
    """Le slice en une commande : tout reste dans --out, rien ne part de la machine."""
    from openplan.pipeline.calibration import calibrate_level
    from openplan.pipeline.detection import detect_and_propose
    from openplan.pipeline.ingestion import import_image
    from openplan.scene import history
    from openplan.scene.factory import new_project
    from openplan.schemas.common import Source
    from openplan.schemas.elements import Wall
    from openplan.schemas.scene import Measurement, MeasurementKind

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    project = new_project(args.name or args.image.stem)
    project, ref, warnings = import_image(
        project, out, args.image.read_bytes(), original_name=args.image.name
    )
    for warning in warnings:
        print(f"qualité : {warning}")
    level_id = next(iter(project.levels))
    run = detect_and_propose(
        project, out, level_id, ref.id, out / "runs", camera_height_m=args.camera_height
    )
    project = run.project
    for step in (run.detection, run.floorplan):
        for assumption in step.assumptions:
            print(f"hypothèse ({assumption.impact}) : {assumption.text}")
        for warning in step.warnings:
            print(f"avertissement : {warning}")
        for artifact in step.artifacts:
            print(artifact.path)
    level = project.levels[level_id]
    estimated = [
        e for e in level.elements.values() if isinstance(e, Wall) and e.source is Source.ESTIMATED
    ]
    if args.measure is not None and estimated:
        # Le mur « de face » (parallèle à l'axe X du plan) est celui que l'on mesure le plus
        # naturellement sur la photo ; à défaut, le premier mur estimé.
        target = next((w for w in estimated if w.name in ("Mur haut", "Mur bas")), estimated[0])
        measurement = Measurement(
            kind=MeasurementKind.WALL_LENGTH, value=args.measure, element_id=target.id
        )
        with_measure = project.model_copy(
            update={"measurements": {**project.measurements, measurement.id: measurement}}
        )
        project = history.commit(project, with_measure, actor="user", label="Ajouter une mesure")
        project, calibration = calibrate_level(project, level_id)
        print(f"calibré sur « {target.name} » : facteur ×{calibration.scale_factor:.4f}")
    elif args.measure is not None:
        print("avertissement : aucun mur estimé, mesure ignorée")
    (out / "project.json").write_text(project.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(out / "project.json")
    for artifact in (
        export_svg(project, level_id, out / "plan.svg"),
        export_pdf(project, level_id, out / "plan.pdf"),
        export_dxf(project, level_id, out / "plan.dxf"),
        export_glb(project, level_id, out / "scene.glb"),
        export_json(project, out / "export.json"),
    ):
        print(artifact.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
