"""Mesure du parcours (P0, avant les onglets Rendus · Plan · 3D) : les indicateurs d'un projet,
calculés sur la machine à partir de ce qu'il garde déjà (historique, rendus, journal local V2).
Rien n'est envoyé ; journal coupé, les indicateurs d'interface restent à zéro.

- **Temps jusqu'au premier rendu** : de la création du projet à la fin du premier rendu d'une
  vue (pas une vignette de variante).
- **Gestes** : pas d'historique faits à la main (ni `pipeline:…` ni `ai:…`) et annulations. Le
  dépôt n'en est pas : ce que l'accueil applique (réglages, consigne) et le plan construit
  depuis la photo, enregistrés au nom de l'utilisateur, jusqu'au dernier pas du chemin
  automatique d'avant le premier rendu, et au moins `DEPOSIT_WINDOW_S` après la création.
- **Onglets, « Affiner », rendus demandés** : événements d'interface du journal (atelier).
  Depuis B3 (plus d'onglets), les passages sont détaillés : allers-retours résultats ↔
  éditeur, entrées dans le plan, changements de vue du plan.
- **Désignation dans un rendu (P4c)** : clics, élément trouvé, décor sans porteur, rien ;
  taux de résolution (part des clics qui désignent un élément).
- **Vues périmées** : vues rendues dont ce qu'on voit a changé après leur dernier rendu
  (règle de `openplan.pipeline.stale`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from openplan.pipeline import journal
from openplan.pipeline.render import RenderEntry, list_renders
from openplan.pipeline.stale import stale_cameras
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import HistoryEntry, Project

DEPOSIT_WINDOW_S = 10.0
AUTOMATIC_PREFIXES = ("pipeline:", "ai:")
AUTOPILOT_ACTOR = "pipeline:autopilot"
RESULTS_EDITOR = frozenset({"renders>board", "board>renders"})
"""B3 : passages notés au journal (`de>vers`) entre résultats et éditeur."""
PLAN_SPACES = ("plan>", "3d>", "vue:")
"""Ce qui part déjà du plan (ou change sa vue) n'est pas une entrée dans le plan."""


class ProjectMetrics(StrictModel):
    """Indicateurs d'un projet (P0), tous calculés localement."""

    project_id: UUID
    first_render_at: datetime | None
    ttfr_s: float | None
    """Secondes de la création du projet à la fin du premier rendu ; None sans rendu."""
    gestures_before_first_render: int
    gestures_after_first_render: int
    tab_changes: int
    """Passages d'un espace à l'autre faits à la main (tous), et vues du plan changées."""
    results_editor_trips: int = 0
    """B3 : allers et retours entre l'écran de résultats et l'éditeur. Ancien : l'écran de
    résultats n'existe plus depuis T7d2 (visionneuse) ; toujours 0 pour les projets récents,
    gardé pour relire les journaux d'avant."""
    viewer_opens: int = 0
    """T7d2 : visionneuse des rendus ouverte (double-clic, « Voir en grand », bande) ; ce n'est
    pas un geste."""
    plan_entries: int = 0
    """B3 : entrées dans le plan (depuis son cadre du tableau ou depuis les résultats)."""
    view_changes: int = 0
    """Vues du plan changées (Plan, Plan + 3D, 3D)."""
    refine_clicks: int
    generate_clicks: int = 0
    """T4 : « Générer le plan » / « Générer maintenant » cliqués ; chacun est un geste (sans la
    bascule « générer au dépôt d'une photo », le chemin coûte un geste)."""
    render_requests: int
    """Rendus demandés à la main (un point de vue, « Rendre toutes les vues »)."""
    renders: int
    """Rendus de vues réalisés (sans les vignettes de variantes)."""
    rendered_views: int
    stale_views: int
    picks: int = 0
    """Clics sur un rendu (P4c) ; les suivants en détaillent l'issue."""
    picks_resolved: int = 0
    """…qui ont désigné un élément du projet (meuble, mur, sol…)."""
    picks_decor: int = 0
    """…tombés sur du décor sans porteur (plante, tapis, rideau…)."""
    picks_none: int = 0
    """…sur rien (fond, ou élément retiré depuis le rendu)."""
    pick_resolution: float | None = None
    """Part des clics qui désignent un élément ; None sans clic (cible du challenge : ≥ 0,8)."""
    journal_enabled: bool


def deposit_end(project: Project, first_render: datetime | None) -> datetime:
    """Fin du dépôt : le dernier pas du chemin automatique d'avant le premier rendu, et au
    moins `DEPOSIT_WINDOW_S` après la création du projet."""
    end = project.created_at + timedelta(seconds=DEPOSIT_WINDOW_S)
    for entry in project.history:
        if entry.actor == AUTOPILOT_ACTOR and (
            first_render is None or entry.timestamp < first_render
        ):
            end = max(end, entry.timestamp)
    return end


def is_gesture(entry: HistoryEntry, deposit: datetime) -> bool:
    if entry.actor.startswith(AUTOMATIC_PREFIXES):
        return False
    return entry.timestamp > deposit


def _view_renders(runs_dir: Path) -> list[RenderEntry]:
    return sorted(
        (e for e in list_renders(runs_dir) if e.variant is None), key=lambda e: e.created_at
    )


def project_metrics(project: Project, project_dir: Path, runs_dir: Path) -> ProjectMetrics:
    renders = _view_renders(runs_dir)
    first = renders[0].created_at if renders else None
    events = journal.read(project_dir)
    deposit = deposit_end(project, first)
    gestures = [e.timestamp for e in project.history if is_gesture(e, deposit)]
    gestures += [e.at for e in events if e.kind in ("undo", "generate_clicked")]
    before = sum(1 for at in gestures if first is None or at < first)

    def count(kind: str) -> int:
        return sum(1 for e in events if e.kind == kind)

    picked = [(e.note or "").split(":", 1)[0] for e in events if e.kind == "render_picked"]
    resolved = picked.count("element")
    moves = [e.note or "" for e in events if e.kind == "tab_changed"]

    return ProjectMetrics(
        project_id=project.id,
        first_render_at=first,
        ttfr_s=round((first - project.created_at).total_seconds(), 1) if first else None,
        gestures_before_first_render=before,
        gestures_after_first_render=len(gestures) - before,
        tab_changes=count("tab_changed"),
        results_editor_trips=sum(1 for m in moves if m in RESULTS_EDITOR),
        plan_entries=sum(
            1 for m in moves if m.endswith((">plan", ">3d")) and not m.startswith(PLAN_SPACES)
        ),
        view_changes=sum(1 for m in moves if m.startswith("vue:")),
        viewer_opens=count("viewer_opened"),
        refine_clicks=count("refine_clicked"),
        generate_clicks=count("generate_clicked"),
        render_requests=count("render_requested"),
        renders=len(renders),
        rendered_views=len({e.camera_id for e in renders if e.camera_id is not None}),
        stale_views=len(stale_cameras(project, renders)),
        picks=len(picked),
        picks_resolved=resolved,
        picks_decor=picked.count("decor"),
        picks_none=picked.count("none"),
        pick_resolution=round(resolved / len(picked), 3) if picked else None,
        journal_enabled=project.privacy.design_journal,
    )
