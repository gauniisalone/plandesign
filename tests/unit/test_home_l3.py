"""Accueil L3 : programme d'un logement (règles, Gemini consenti), plan généré par découpage
(surfaces, accès, placards, bandeau), chemin automatique sur plusieurs pièces (ambiance de
chaque pièce, coin bureau, vues réparties), respect de la demande avec de vraies chambres."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon

from openplan.adapters.protocols import LlmRequest, LlmResponse
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline import autopilot as ap
from openplan.pipeline.autopilot import HomeRequest, Workspace, run_autopilot
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.generated import generate_plan
from openplan.pipeline.mainroom import GENERATED_KEY, main_room
from openplan.pipeline.programme import describes_dwelling, read_programme, read_rules
from openplan.pipeline.views import AUTO_KEY
from openplan.scene.factory import new_project
from openplan.schemas.elements import DOOR_KINDS, Camera, Furniture, Opening, Room
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import Level, Project
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level

T3 = "Un appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau"
PROGRAMMES = {
    "studio": ("Studio de 30 m²", 30.0),
    "t2": ("T2 de 45 m², une chambre, cuisine ouverte", 45.0),
    "t3": (T3, 60.0),
    "t4": ("T4 de 80 m², trois chambres, wc séparés", 80.0),
    "maison": ("Maison de plain-pied de 110 m², trois chambres, un bureau, une buanderie", 110.0),
}


# ------------------------------------------------------------------ programme
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (T3, {"kind": "appartement", "area_m2": 60.0, "bedrooms": 2, "office": "coin"}),
        ("Studio de 28 m2 lumineux", {"kind": "studio", "bedrooms": 0}),
        ("T2 de 45 m² avec cuisine fermée", {"bedrooms": 1, "kitchen": "fermee"}),
        (
            "un T3 de 62 m2, wc séparés, un cellier",
            {"bedrooms": 2, "wc": True, "extras": ["cellier"]},
        ),
        ("F4 80 m², deux salles de bain", {"bedrooms": 3, "bathrooms": 2}),
        (
            "maison de 110 m² avec trois chambres et un bureau",
            {"kind": "maison", "office": "piece"},
        ),
        ("deux chambres d'enfants et une suite parentale, 85 m2", {"bedrooms": 3}),
        ("loft de 70 m² avec une seule chambre", {"bedrooms": 1}),
        ("un 2 pièces de 42 m²", {"bedrooms": 1, "area_m2": 42.0}),
        ("appartement familial de 90 m²", {"bedrooms": 3}),
    ],
)
def test_rule_reader(text: str, expected: dict[str, Any]) -> None:
    got = read_rules(text).model_dump()
    assert {k: got[k] for k in expected} == expected


def test_answers_win_and_what_is_assumed_is_said() -> None:
    p = read_rules("appartement lumineux", GuidedAnswers(space="maison", bedrooms=2))
    assert p.kind == "maison" and p.bedrooms == 2
    assert p.notes == ["surface supposée : 60 m²"]


@pytest.mark.parametrize(
    ("text", "answers", "dwelling"),
    [
        (T3, None, True),
        ("deux chambres et un séjour", None, True),
        ("une chambre de 12 m² cosy", None, False),
        ("grenier chaleureux, bois et lin, deux lits", None, False),
        ("", GuidedAnswers(space="piece", room_type="bedroom", area_m2=12), False),
        ("", GuidedAnswers(space="appartement"), True),
    ],
)
def test_a_dwelling_is_told_apart_from_a_single_room(
    text: str, answers: GuidedAnswers | None, dwelling: bool
) -> None:
    assert describes_dwelling(text, answers) is dwelling


# ------------------------------------------------------------------ plan généré
def _generated(key: str) -> tuple[Project, UUID, list[str]]:
    text, _ = PROGRAMMES[key]
    reading = read_programme(text, None)
    assert reading.programme is not None
    project = new_project("Essai")
    level_id = only_level(project).id
    after, notes = generate_plan(project, level_id, reading.programme)
    return after, level_id, notes


def _reachable(level: Level) -> tuple[int, int]:
    rooms = {e.id: e for e in level.elements.values() if isinstance(e, Room)}
    by_wall: dict[UUID, list[UUID]] = {}
    for rid, room in rooms.items():
        for wid in room.boundary_walls:
            by_wall.setdefault(wid, []).append(rid)
    links: dict[UUID, set[UUID]] = {rid: set() for rid in rooms}
    entry: set[UUID] = set()
    for o in level.elements.values():
        if isinstance(o, Opening) and o.kind in DOOR_KINDS:
            sides = by_wall.get(o.host_wall, [])
            if len(sides) == 2:
                links[sides[0]].add(sides[1])
                links[sides[1]].add(sides[0])
            elif len(sides) == 1 and o.kind.value == "door":
                entry.add(sides[0])
    seen, todo = set(entry), list(entry)
    while todo:
        for n in links[todo.pop()] - seen:
            seen.add(n)
            todo.append(n)
    return len(seen), len(rooms)


@pytest.mark.parametrize("key", list(PROGRAMMES))
def test_generated_plans_are_valid_reachable_and_on_target(key: str) -> None:
    project, level_id, notes = _generated(key)
    level = project.levels[level_id]
    assert validate_invariants(project).valid
    assert len(project.history) == 1, "un seul pas d'historique, annulable d'un coup"
    reached, rooms = _reachable(level)
    assert reached == rooms, "chaque pièce est desservie par une porte"
    derived = compute_derived(level)
    areas = {
        e.name: derived.rooms[e.id].area_m2 for e in level.elements.values() if isinstance(e, Room)
    }
    _, total = PROGRAMMES[key]
    habitable = sum(areas.values())
    assert abs(habitable - total) / total < 0.12, areas
    if key in ("t2", "t3", "t4"):
        assert areas["Chambre 1"] == pytest.approx(12.0, rel=0.03)
    if key == "maison":
        assert "plan simplifié, une seule circulation" in notes
    else:
        assert "plan simplifié, une seule circulation" not in notes


def test_a_wide_wc_gives_its_surplus_to_a_closet() -> None:
    project, level_id, notes = _generated("t3")
    level = project.levels[level_id]
    derived = compute_derived(level)
    areas = {
        e.name: derived.rooms[e.id].area_m2 for e in level.elements.values() if isinstance(e, Room)
    }
    assert areas["WC"] == pytest.approx(1.5, rel=0.15)
    assert "Placard" in areas
    assert any("placard au fond du WC" in n for n in notes)
    assert any(
        isinstance(o, Opening) and o.kind.value == "folding_door" for o in level.elements.values()
    )


# ------------------------------------------------------------------ chemin automatique
@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")

    monkeypatch.setattr(ap, "render_camera_view", render)


def _room_at(level: Level, x: float, y: float) -> Room | None:
    derived = compute_derived(level)
    for e in level.elements.values():
        if isinstance(e, Room) and e.id in derived.rooms:
            polygon = Polygon([(p.x, p.y) for p in derived.rooms[e.id].polygon])
            if polygon.contains(Point(x, y)):
                return e
    return None


def test_autopilot_on_a_generated_flat(tmp_path: Path, fake_render: None) -> None:
    project, level_id, _ = _generated("t3")
    saved = {"p": project}
    run = run_autopilot(
        Workspace(lambda: saved["p"], lambda p: saved.update(p=p)),
        level_id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        home=HomeRequest(instruction=T3),
    )
    level = saved["p"].levels[level_id]
    main = main_room(level)
    assert main is not None and main.name == "Séjour et cuisine" and GENERATED_KEY in main.metadata
    # Ambiance : le séjour par l'étape Ambiance, les autres pièces habitables avec le même style.
    rooms = {e.name: e for e in level.elements.values() if isinstance(e, Room)}
    assert main.intent is not None and main.intent.style
    for name in ("Chambre 1", "Chambre 2"):
        intent = rooms[name].intent
        assert intent is not None and intent.style == main.intent.style
    assert {"cuisine", "repas"} <= set(main.intent.zones)
    # Coin bureau posé (séjour ou chambre selon la place), chambres meublées.
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture)]
    desks = [f for f in furniture if f.category in ("desk", "desk_corner")]
    assert desks
    beds = {
        (_room_at(level, f.transform.position.x, f.transform.position.y) or main).name
        for f in furniture
        if f.category.startswith("bed")
    }
    assert {"Chambre 1", "Chambre 2"} <= beds
    # Trois vues : le séjour, la chambre principale, et une vue au plus par pièce au-delà.
    cameras = [
        e for e in level.elements.values() if isinstance(e, Camera) and AUTO_KEY in e.metadata
    ]
    assert len(cameras) == 3
    where = [(_room_at(level, c.position.x, c.position.y) or main).name for c in cameras]
    assert where[0] == "Séjour et cuisine" or "Séjour et cuisine" in where
    assert "Chambre 1" in where
    others = [w for w in where if w != "Séjour et cuisine"]
    assert len(others) == len(set(others)), where
    # Respect de la demande : de vraies chambres, le coin bureau ✓, accords sans « (s) ».
    status = {(i.label, i.wanted): i for i in run.request}
    rooms_item = status[("Chambres", "2")]
    assert rooms_item.status == "ok" and rooms_item.detail == "2 chambres, un lit dans chacune"
    assert status[("Zone", "coin bureau")].status == "ok"
    assert all("(s)" not in i.detail for i in run.request)


def test_request_details_never_use_s_in_brackets() -> None:
    from openplan.pipeline.guided import _bedrooms_item, _furniture_items

    item = _bedrooms_item({"bed_double": 1}, 2)
    assert item.detail == "1 lit posé sur 2 : place insuffisante"
    items = _furniture_items({"sofa": 2}, {"desk": 2}, ["sofa"])
    assert [i.detail for i in items] == ["0 posé sur 2 : place insuffisante", "2 posés malgré tout"]


# ------------------------------------------------------------------ route
class RemoteProgramme:
    model_id = "fake:gemini"
    remote = True
    supports_images = True
    destination = "generativelanguage.googleapis.com"

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        answer = {"kind": "appartement", "area_m2": 70, "bedrooms": 3, "office": "piece"}
        return LlmResponse(text=json.dumps(answer), model_id=self.model_id)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings=Settings(data_dir=tmp_path / "data"))) as c:
        yield c


def _new(c: TestClient, **body: Any) -> tuple[str, str]:
    project = c.post("/api/projects", json={"name_auto": True, **body}).json()
    return project["id"], next(iter(project["levels"]))


def test_route_generates_starts_or_keeps(client: TestClient) -> None:
    pid, lid = _new(client)
    r = client.post(f"/api/projects/{pid}/levels/{lid}/programme", json={"text": T3}).json()
    assert r["kind"] == "generated" and r["programme"]["bedrooms"] == 2
    assert r["understanding"] == "phrases"
    names = {
        e.get("name") for e in next(iter(r["project"]["levels"].values()))["elements"].values()
    }
    assert {"Séjour et cuisine", "Chambre 1", "Chambre 2", "Entrée et dégagement"} <= names
    pid, lid = _new(client)
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/programme", json={"text": "une chambre de 12 m² cosy"}
    ).json()
    assert r["kind"] == "starter"
    pid, lid = _new(client)
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/programme",
        json={"text": "grenier chaleureux, bois et lin"},
    ).json()
    assert r["kind"] == "none"


def test_route_reads_with_gemini_only_with_the_home_consent(tmp_path: Path) -> None:
    model = RemoteProgramme()
    app = create_app(settings=Settings(data_dir=tmp_path / "data"), language_model=model)
    with TestClient(app) as c:
        pid, lid = _new(c)
        r = c.post(f"/api/projects/{pid}/levels/{lid}/programme", json={"text": T3}).json()
        assert r["understanding"] == "phrases" and model.requests == [], "sans accord, rien ne part"
        c.put("/api/home/settings", json={"gemini_text": True})
        pid, lid = _new(c, remote_text_consent=True)
        r = c.post(f"/api/projects/{pid}/levels/{lid}/programme", json={"text": T3}).json()
    assert r["understanding"] == "model" and r["programme"]["bedrooms"] == 3
    assert r["programme"]["office"] == "piece" and len(model.requests) == 1


def test_tight_rooms_drop_extras_only_with_two_zones_or_more() -> None:
    from openplan.pipeline.zones import zone_brief
    from openplan.schemas.intent import DesignIntent

    one = zone_brief(DesignIntent(zones=["detente"]), "living_room", 9.0, sloped=False)
    assert any(i.priority == "nice_to_have" for i in one.items), (
        "une zone : les « en plus » restent"
    )
    two = zone_brief(DesignIntent(zones=["couchage", "bureau"]), "bedroom", 12.0, sloped=False)
    assert all(i.priority == "required" for i in two.items), "deux zones sur 12 m² : serrée"


def test_studio_reports_its_request_and_keeps_the_bed_apart(
    tmp_path: Path, fake_render: None
) -> None:
    project, level_id, _ = _generated("studio")
    saved = {"p": project}
    run = run_autopilot(
        Workspace(lambda: saved["p"], lambda p: saved.update(p=p)),
        level_id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        home=HomeRequest(instruction="Studio de 30 m²"),
    )
    status = {(i.label, i.wanted): i for i in run.request}
    assert status[("Type", "studio")].status == "ok"
    surface = status[("Surface", "30 m²")]
    assert surface.status == "ok" and surface.detail.startswith("plan ") and "," in surface.detail
    # Coin nuit et coin détente séparés : 80 cm entre le lit (et ses chevets) et le reste.
    level = saved["p"].levels[level_id]
    from openplan.pipeline.layouts import ZONE_KEY
    from openplan.pipeline.views import _footprint

    furniture = [e for e in level.elements.values() if isinstance(e, Furniture)]
    night = [f for f in furniture if f.metadata.get(ZONE_KEY) == "couchage"]
    day = [
        f
        for f in furniture
        if f.metadata.get(ZONE_KEY) not in (None, "couchage") and not f.category.startswith("rug")
    ]
    assert any(f.category.startswith("bed") for f in night)
    gap = min(_footprint(a).distance(_footprint(b)) for a in night for b in day)
    assert gap >= 0.79, gap
    # Repas et détente composés, même quand leur baie est trop étroite (porte-fenêtre, lit) :
    # chaque chaise contre la table, la table basse devant le canapé ; pas de chaise seule.
    table = next(f for f in furniture if f.category == "table")
    chairs = [f for f in furniture if f.category == "dining_chair"]
    assert chairs and all(_footprint(c).distance(_footprint(table)) < 0.05 for c in chairs)
    sofa = next(f for f in furniture if f.category == "sofa")
    coffee = next(f for f in furniture if f.category == "coffee_table")
    assert _footprint(sofa).distance(_footprint(coffee)) == pytest.approx(0.45, abs=0.02)
