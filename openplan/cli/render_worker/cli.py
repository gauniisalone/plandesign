"""`openplan worker …` : le poste de rendu d'augus (KAN-89).

openplan worker token [--url https://app.plandesign.fr] [--force]
openplan worker run [--once]
openplan worker pause | resume | stop | status
"""

from __future__ import annotations

import argparse
from pathlib import Path

from openplan.cli.render_worker import config


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    worker = sub.add_parser(
        "worker", help="poste de rendu : calcule ici les rendus réalistes demandés en ligne"
    )
    worker.add_argument("--config", type=Path, help="défaut : ~/.config/plandesign/worker.env")
    commands = worker.add_subparsers(dest="worker_command", required=True)
    token = commands.add_parser(
        "token", help="crée le jeton du poste (fichier 600) et affiche son empreinte pour l'Infra"
    )
    token.add_argument("--url", default=config.DEFAULT_URL, help="adresse de l'application")
    token.add_argument("--force", action="store_true", help="remplace un jeton existant")
    run = commands.add_parser("run", help="lance le poste (Ctrl+C pour l'arrêter)")
    run.add_argument("--once", action="store_true", help="un seul appel, puis s'arrête")
    commands.add_parser("pause", help="met le poste en pause (fichier drapeau)")
    commands.add_parser("resume", help="reprend après une pause")
    commands.add_parser("stop", help="arrête le poste (après le calcul en cours)")
    commands.add_parser("status", help="configuration et drapeaux, sans jamais le jeton")


def command(args: argparse.Namespace) -> int:
    name: str = args.worker_command
    path: Path = args.config or config.env_file()
    flags = path.parent
    pause, stop = flags / "worker.pause", flags / "worker.stop"
    if name in ("pause", "resume", "stop"):
        return _flags(name, pause, stop)
    try:
        if name == "token":
            digest = config.create_token_file(path, args.url, force=args.force)
            print(f"Jeton du poste écrit dans {path} (droits 600, jamais affiché).")
            print("Empreinte à donner à l'Infra (variable OPENPLAN_WORKER_TOKEN_SHA256) :")
            print(digest)
            return 0
        cfg = config.load_config(path)
        if name == "status":
            print(f"Application : {cfg.url} ; moteur : {cfg.engine}")
            print(
                f"Pause : {'oui' if pause.exists() else 'non'} ; arrêt demandé : "
                f"{'oui' if stop.exists() else 'non'}"
            )
            return 0
        return _run(cfg, pause, stop, once=args.once)
    except config.WorkerConfigError as exc:
        print(f"Poste de rendu : {exc}")
        return 2


def _flags(name: str, pause: Path, stop: Path) -> int:
    """Pause, reprise, arrêt : des fichiers drapeaux que le poste lit à chaque tour."""
    pause.parent.mkdir(parents=True, exist_ok=True)
    if name == "pause":
        pause.touch()
        print("Poste en pause : il reste en ligne mais ne prend plus de rendu.")
    elif name == "stop":
        stop.touch()
        print(
            "Arrêt demandé : le poste s'arrête après le calcul en cours (Ctrl+C : tout de suite)."
        )
    else:
        pause.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
        print("Poste repris.")
    return 0


def _run(cfg: config.WorkerConfig, pause: Path, stop: Path, *, once: bool) -> int:
    from openplan.cli.render_worker.client import WorkerClient
    from openplan.cli.render_worker.loop import Worker

    # Retours de la MEP 14 : un seul poste à la fois (verrou pris avant tout, y compris avant
    # d'effacer le drapeau d'arrêt, qui appartient au poste déjà lancé).
    with config.single_worker(stop.parent):
        stop.unlink(missing_ok=True)  # un ancien drapeau ne doit pas arrêter ce lancement
        client = WorkerClient(cfg.url, cfg.token)
        print(f"Poste de rendu vers {cfg.url} (moteur {cfg.engine}) ; Ctrl+C pour arrêter.")
        try:
            return Worker(client=client, engine=cfg.engine, pause_flag=pause, stop_flag=stop).run(
                once=once
            )
        finally:
            client.close()
