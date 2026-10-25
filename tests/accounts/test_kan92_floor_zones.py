"""KAN-92 avec les comptes : un éditeur dessine une zone de sol et l'élève, tire un mur ; le
propriétaire et le lecteur relisent le même projet (collaboration : l'avis « projet changé »
fait relire ce que le serveur garde) ; lecteur et commentateur ne peuvent rien en changer ;
Ctrl+Z d'un autre membre annule le dernier geste."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.accounts.test_kan51_comments import _team


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _room(owner: Any, pid: str) -> str:
    """Une pièce de 4 × 3 m tracée par le propriétaire ; renvoie l'id du niveau."""
    level = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    ops = f"/api/projects/{pid}/levels/{level}/ops"
    corners = [(0, 0), (4, 0), (4, 3), (0, 3)]
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        wall = {"op": "add_wall", "start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}}
        assert owner.post(ops, json=wall).status_code == 200
    assert owner.post(ops, json={"op": "detect_rooms"}).status_code == 200
    return str(level)


def _of(client: Any, pid: str, level: str, kind: str) -> list[dict[str, Any]]:
    elements = client.get(f"/api/projects/{pid}").json()["levels"][level]["elements"]
    return [e for e in elements.values() if e["type"] == kind]


def test_an_editor_raises_a_zone_and_pulls_a_wall_everyone_reads_it(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    level = _room(owner, pid)
    ops = f"/api/projects/{pid}/levels/{level}/ops"
    square = [{"x": 1, "y": 1}, {"x": 2, "y": 1}, {"x": 2, "y": 2}, {"x": 1, "y": 2}]
    for role in ("viewer", "commenter"):
        denied = m[role].post(ops, json={"op": "add_floor_zone", "polygon": square})
        assert denied.status_code == 403, role
    drawn = m["editor"].post(ops, json={"op": "add_floor_zone", "polygon": square})
    assert drawn.status_code == 200, drawn.text
    (zone,) = _of(m["viewer"], pid, level, "floor_zone")
    raised = m["editor"].post(
        ops, json={"op": "update_element", "element_id": zone["id"], "fields": {"elevation": 0.2}}
    )
    assert raised.status_code == 200, raised.text
    wall = _of(owner, pid, level, "wall")[0]
    too_thick = m["editor"].post(
        ops, json={"op": "update_element", "element_id": wall["id"], "fields": {"thickness": 0.72}}
    )
    assert too_thick.status_code == 409 and "60 cm" in too_thick.text
    pulled = m["editor"].post(
        ops, json={"op": "update_element", "element_id": wall["id"], "fields": {"thickness": 0.3}}
    )
    assert pulled.status_code == 200, pulled.text
    for who in (owner, m["viewer"]):
        assert _of(who, pid, level, "floor_zone")[0]["elevation"] == 0.2
        assert any(w["thickness"] == 0.3 for w in _of(who, pid, level, "wall"))
    # Ctrl+Z du propriétaire : le dernier geste (le mur), un pas.
    assert owner.post(f"/api/projects/{pid}/undo").status_code == 200
    assert not any(w["thickness"] == 0.3 for w in _of(m["viewer"], pid, level, "wall"))
    assert _of(m["viewer"], pid, level, "floor_zone")[0]["elevation"] == 0.2
