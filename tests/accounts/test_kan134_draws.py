"""KAN-134 : les tirages d'une même vue (« 1 à 3 rendus par pièce »). Le corps de
POST …/realistic porte `draw` (0 à 2) : une vue garde au plus `draw + 1` demandes en attente ;
au-delà c'est un double clic, fusionné (200). Chaque demande nouvelle passe au quota du jour."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.accounts.test_kan89_realistic import World


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def _ask(world: World, draw: int | None) -> tuple[int, dict[str, object]]:
    body: dict[str, object] = {"camera_id": str(world.camera_id)}
    if draw is not None:
        body["draw"] = draw
    done = world.client.post(
        f"/api/projects/{world.project_id}/levels/{world.level_id}/realistic", json=body
    )
    return done.status_code, done.json()


def _used(world: World) -> int:
    used: int = world.client.get("/api/realistic/status").json()["used_today"]
    return used


def test_double_click_is_still_merged(world: World) -> None:
    """Sans `draw` (ou draw 0), comme avant : la même vue en attente est rendue telle quelle."""
    first_status, first = _ask(world, None)
    again_status, again = _ask(world, 0)
    assert (first_status, again_status) == (201, 200)
    assert again["id"] == first["id"]
    assert _used(world) == 1
    world.close()


def test_two_draws_give_two_requests_and_a_resend_is_merged(world: World) -> None:
    """« 2 rendus par pièce » : draw 0 puis draw 1, deux demandes, deux au quota. Le même envoi
    refait (double clic sur « Rendre ») est fusionné : rien de plus ne part. Draw 2 : une 3e."""
    s0, d0 = _ask(world, 0)
    s1, d1 = _ask(world, 1)
    assert (s0, s1) == (201, 201)
    assert d0["id"] != d1["id"]
    assert _used(world) == 2
    r0, again0 = _ask(world, 0)
    r1, again1 = _ask(world, 1)
    assert (r0, r1) == (200, 200)
    assert {again0["id"], again1["id"]} <= {d0["id"], d1["id"]}
    assert _used(world) == 2
    s2, d2 = _ask(world, 2)
    assert s2 == 201 and d2["id"] not in (d0["id"], d1["id"])
    assert _used(world) == 3
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    assert [r["state"] for r in listing["requests"]] == ["pending"] * 3
    world.close()


def test_draw_is_bounded_by_the_server(world: World) -> None:
    for bad in (-1, 3, 99):
        status, _ = _ask(world, bad)
        assert status == 422, bad
    assert _used(world) == 0
    world.close()


def test_partial_quota_takes_what_fits(tmp_path: Path, migrated_url: str) -> None:
    """Quota de 1 : le 1er tirage part, le 2e est refusé (429 « daily_quota ») ; une seule
    demande existe et le compteur dit 1."""
    world = World(tmp_path, migrated_url, daily_quota=1)
    s0, _ = _ask(world, 0)
    assert s0 == 201
    over = world.client.post(
        f"/api/projects/{world.project_id}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id), "draw": 1},
    )
    assert over.status_code == 429
    assert over.json()["reason"] == "daily_quota"
    assert _used(world) == 1
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    assert len(listing["requests"]) == 1
    world.close()
