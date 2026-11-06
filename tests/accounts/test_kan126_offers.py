"""KAN-126 (126a, sans migration) : offres Essentiel et Pro, déclarées dans le registre (KAN-105).

- Par défaut, tout compte est en Pro : rien ne change pour personne.
- En Essentiel (offre injectée : la colonne du compte vient au lot 126b) : les exports pro
  (dxf, section-pdf ; le glb reste Essentiel, la vue 3D le lit) et l'AJOUT à la main d'un
  élément Pro (poteau, ferme, fenêtre de toit, escalier, zone de sol) ou Pousser/Tirer sont
  refusés en 403 `offer_pro`, avec un message clair.
- L'archive, le json, le svg et le pdf restent Essentiels ; un élément Pro existant reste
  modifiable (nom) et supprimable ; le calcul du plan (combles) crée ses éléments Pro quelle
  que soit l'offre.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.api.policy.offers import OFFER_PRO_TEXT
from openplan.api.policy.registry import BY_KEY, PRO_OPS, PRO_ROUTES, P
from tests.accounts.helpers import ADMIN, accounts_client, sign_in

ZOE = ADMIN
"""Le compte des réglages (la connexion seule n'inscrit personne) ; son offre ne tient pas à
son rôle d'administrateur."""
ATTIC = {"length": 6.0, "width": 5.0, "ridge_height": 3.2, "eave_height": 1.0}


def _est(value: float) -> dict[str, Any]:
    return {"value": value, "sigma": 0.0, "source": "user_defined"}


def _attic(client: TestClient, roof_windows: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    created = client.post("/api/projects", json={"name": "Combles"})
    assert created.status_code == 201, created.text
    project = created.json()
    level = next(iter(project["levels"]))
    body: dict[str, Any] = {k: _est(v) for k, v in ATTIC.items()}
    body |= {"camera_height": _est(1.45), "gable_distance": _est(4.5)}
    if roof_windows is not None:
        body["roof_windows"] = roof_windows
    done = client.post(f"/api/projects/{project['id']}/levels/{level}/attic", json=body)
    assert done.status_code == 200, done.text
    return project["id"], level


def _elements(client: TestClient, pid: str, level: str) -> dict[str, dict[str, Any]]:
    got = client.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    return got.json()["levels"][level]["elements"]  # type: ignore[no-any-return]


def _essentiel(client: TestClient) -> None:
    client.app.state.offer_of = lambda _user: "essentiel"  # type: ignore[attr-defined]


def _refused(r: Any) -> None:
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": OFFER_PRO_TEXT, "reason": "offer_pro"}


@pytest.fixture
def zoe(tmp_path: Path, migrated_url: str) -> TestClient:
    client, _service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(client, mailer, ZOE)
    return client


def test_the_pro_routes_are_declared_in_the_registry() -> None:
    names = {BY_KEY[k].name for k in PRO_ROUTES}
    assert names == {"project.exports.dxf:get", "project.exports.section-pdf:get"}
    assert ("GET", P + "/exports/archive") not in PRO_ROUTES, "la portabilité reste Essentielle"
    assert ("GET", P + "/exports/glb") not in PRO_ROUTES, "la vue 3D de l'éditeur le lit"
    expected = {"add_truss", "add_post", "add_roof_window", "add_stairs", "add_floor_zone"}
    assert expected == PRO_OPS


def test_every_account_is_pro_by_default_nothing_changes(zoe: TestClient) -> None:
    assert zoe.get("/api/auth/me").json()["offer"] == "pro"
    pid, level = _attic(zoe)
    for name in ("dxf", "glb", "section-pdf"):
        r = zoe.get(f"/api/projects/{pid}/exports/{name}")
        assert r.status_code != 403, f"{name} : {r.text}"
    added = zoe.post(
        f"/api/projects/{pid}/levels/{level}/ops",
        json={"op": "add_post", "position": {"x": 3.0, "y": 2.5}},
    )
    assert added.status_code == 200, added.text


def test_essentiel_is_refused_the_pro_exports_only(zoe: TestClient) -> None:
    pid, _level = _attic(zoe)
    _essentiel(zoe)
    assert zoe.get("/api/auth/me").json()["offer"] == "essentiel"
    for name in ("dxf", "section-pdf"):
        _refused(zoe.get(f"/api/projects/{pid}/exports/{name}"))
    for name in ("json", "svg", "pdf", "archive", "glb"):
        r = zoe.get(f"/api/projects/{pid}/exports/{name}")
        assert r.status_code != 403, f"{name} : {r.text}"


def test_essentiel_cannot_add_a_pro_element_by_hand(zoe: TestClient) -> None:
    pid, level = _attic(zoe)
    ops = f"/api/projects/{pid}/levels/{level}/ops"
    _essentiel(zoe)
    _refused(zoe.post(ops, json={"op": "add_post", "position": {"x": 3.0, "y": 2.5}}))
    _refused(zoe.post(ops, json={"op": "add_truss"}))
    _refused(zoe.post(ops, json={"op": "add_stairs", "position": {"x": 3.0, "y": 2.5}}))
    assert not any(
        e["type"] in ("post", "truss", "stairs") for e in _elements(zoe, pid, level).values()
    )
    # Un geste Essentiel passe : un mur.
    wall = zoe.post(
        ops, json={"op": "add_wall", "start": {"x": 1.0, "y": 1.0}, "end": {"x": 2.0, "y": 1.0}}
    )
    assert wall.status_code == 200, wall.text


def test_an_existing_pro_element_stays_editable_and_removable(zoe: TestClient) -> None:
    pid, level = _attic(zoe)
    ops = f"/api/projects/{pid}/levels/{level}/ops"
    assert (
        zoe.post(ops, json={"op": "add_post", "position": {"x": 3.0, "y": 2.5}}).status_code == 200
    )
    post = next(e for e in _elements(zoe, pid, level).values() if e["type"] == "post")
    wall = next(e for e in _elements(zoe, pid, level).values() if e["type"] == "wall")
    _essentiel(zoe)
    renamed = zoe.post(
        ops,
        json={"op": "update_element", "element_id": post["id"], "fields": {"name": "Poteau"}},
    )
    assert renamed.status_code == 200, renamed.text
    # Pousser/Tirer : Pro.
    _refused(
        zoe.post(
            ops,
            json={"op": "update_element", "element_id": wall["id"], "fields": {"thickness": 0.3}},
        )
    )
    # Exporté dans les formats Essentiels, et supprimable : rien n'est retiré d'office.
    assert zoe.get(f"/api/projects/{pid}/exports/json").status_code == 200
    removed = zoe.post(ops, json={"op": "remove_element", "element_id": post["id"]})
    assert removed.status_code == 200, removed.text


def test_stairs_placed_by_the_assistant_stay_essentiel(zoe: TestClient) -> None:
    """Décision du PO : l'assistant fait partie du parcours guidé Essentiel ; un escalier qu'il
    pose compte comme le calcul du plan (assistant_plan.add_stairs), pas comme un ajout à la
    main."""
    pid, level = _attic(zoe)
    _essentiel(zoe)
    said = zoe.post(
        f"/api/projects/{pid}/levels/{level}/assistant/say",
        json={"text": "Pose un escalier droit"},
    )
    assert said.status_code == 200, said.text
    assert any(e["type"] == "stairs" for e in _elements(zoe, pid, level).values())


def test_the_plan_computation_creates_pro_elements_whatever_the_offer(zoe: TestClient) -> None:
    """Décision du PO : la génération et la régénération sont Essentielles ; si le calcul des
    combles pose lui-même une fenêtre de toit, elle est créée en Essentiel."""
    _essentiel(zoe)
    sighting = {
        "forward_m": 2.0,
        "lateral_m": -1.4,
        "width_m": 0.78,
        "z_bottom_m": 1.4,
        "z_top_m": 2.1,
        "score": 0.9,
        "bbox_px": [10, 10, 60, 80],
        "model_id": "test",
        "standard_size": True,
    }
    pid, level = _attic(zoe, roof_windows=[sighting])
    kinds = [e["type"] for e in _elements(zoe, pid, level).values()]
    assert "roof_window" in kinds
