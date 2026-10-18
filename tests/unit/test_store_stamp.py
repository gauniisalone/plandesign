"""`updated_at` strictement croissant d'une écriture à l'autre du même projet (lot stabilité) :
l'écran ne revient jamais à un projet plus ancien que celui qu'il affiche, donc un résultat de
tâche ou un réglage enregistré sans avancer l'horodatage ne doit jamais paraître « plus vieux »,
et deux écritures dans la même microseconde ne doivent pas s'égaler."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from openplan.scene.factory import new_project
from openplan.scene.store import ProjectStore


def test_each_save_moves_updated_at_forward(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = new_project("Grenier")
    store.save(project)
    first = store.load(project.id).updated_at
    # Même objet réenregistré (horodatage inchangé : réglage, résultat de tâche) : il avance.
    store.save(project)
    second = store.load(project.id).updated_at
    assert second > first
    # Une copie plus ancienne (tâche partie d'une base d'avant) : toujours plus récente que la
    # dernière écriture.
    stale = project.model_copy(update={"updated_at": first - timedelta(hours=1)})
    store.save(stale)
    assert store.load(project.id).updated_at > second
    # Un horodatage déjà plus récent est gardé tel quel.
    ahead = project.model_copy(update={"updated_at": second + timedelta(days=1)})
    store.save(ahead)
    assert store.load(project.id).updated_at == ahead.updated_at


def test_order_survives_a_restart(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = new_project("Salon")
    store.save(project.model_copy(update={"updated_at": project.updated_at + timedelta(days=1)}))
    last = store.load(project.id).updated_at
    restarted = ProjectStore(tmp_path)  # plus de mémoire : l'index sur disque fait foi
    restarted.save(project)
    assert restarted.load(project.id).updated_at > last
