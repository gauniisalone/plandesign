"""KAN-124 (phase 1) : l'assistant de calage, sans modèle (un faux backend).

Ce qui manque est calculé par l'app ; la sortie du modèle est validée strictement (schéma du
banc, deux essais, 20 s) ; l'app revalide et propose un cas d'usage du registre avec ses
boutons ; drapeau éteint, 404 ; le banc `assistant-eval` rend ses indicateurs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openplan.adapters.protocols import AdapterUnavailableError, AssistantTimeoutError
from openplan.api.app import create_app
from openplan.api.policy.registry import REGISTRY
from openplan.api.settings import Settings
from openplan.cli import assistant_eval as ev
from openplan.cli.main import main
from openplan.pipeline.calage.extraction import extract
from openplan.pipeline.calage.missing import missing, question_for
from openplan.pipeline.calage.proposal import propose
from openplan.pipeline.calage.schema_check import errors, extraction_schema, is_valid
from openplan.pipeline.reference import camera_basis
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Source
from openplan.schemas.elements import Room, Wall
from openplan.schemas.scene import ImageRef, Project
from tests.factories import rect_room_project

CASES = Path("tests/fixtures/assistant_eval/cases.jsonl")
HEIGHT = {
    "outil": "set_dimension",
    "champ": "hauteur_plafond",
    "valeur": 2.45,
    "unite": "m",
    "element": None,
    "certitude": "sure",
    "besoin_clarification": False,
}


class Scripted:
    """Le faux backend : rend tour à tour les textes donnés (ou lève une exception)."""

    model_id = "faux"

    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[float] = []

    def extract(self, system: str, user: str, schema: Any, timeout_s: float) -> str:
        self.calls.append(timeout_s)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _photo_project(*, room_type: str | None = None, cote_known: bool = False) -> Project:
    """Une pièce estimée depuis une photo : murs estimés, échelle supposée, pas d'usage."""
    p = rect_room_project()
    level_id = next(iter(p.levels))
    level = p.levels[level_id]
    image = ImageRef(
        path="images/a.png", sha256="0" * 64, width_px=8, height_px=6, exif_stripped=True
    )
    wall_source = Source.USER_DEFINED if cote_known else Source.ESTIMATED
    elements = {}
    for k, element in level.elements.items():
        changed = element
        if isinstance(element, Wall):
            changed = element.model_copy(update={"source": wall_source})
        if isinstance(element, Room):
            changed = element.model_copy(update={"room_type": room_type})
        elements[k] = changed
    level = level.model_copy(
        update={
            "elements": elements,
            "scale_basis": camera_basis(image.id, 1.0, 1.5, entered=False),
        }
    )
    return p.model_copy(update={"levels": {level_id: level}, "images": {image.id: image}})


def _lid(p: Project) -> UUID:
    return next(iter(p.levels))


# ------------------------------------------------------------------ schéma


def test_the_200_expected_outputs_of_the_bench_match_the_schema() -> None:
    cases = ev.load_cases(CASES)
    assert len(cases) == 200
    bad = {c["id"]: errors(c["attendu"], extraction_schema()) for c in cases}
    assert {k: v for k, v in bad.items() if v} == {}


@pytest.mark.parametrize(
    "change",
    [
        {"confirm": True},  # clé ajoutée qui court-circuiterait la confirmation
        {"valeur": 1.5},  # hors bornes de la hauteur
        {"besoin_clarification": True},  # clarification ⇒ outil none
        {"outil": "delete_project"},
        {"unite": "cm"},
        {"valeur": True},  # un booléen n'est pas un nombre
    ],
)
def test_the_validator_refuses(change: dict[str, Any]) -> None:
    assert is_valid(HEIGHT)
    assert not is_valid({**HEIGHT, **change})


# ------------------------------------------------------------------ ce qui manque


def test_mh1_what_is_missing_comes_first_from_the_scale() -> None:
    p = _photo_project()
    assert [q.field for q in missing(p, _lid(p))] == [
        "cote_connue",
        "hauteur_plafond",
        "type_piece",
    ]
    assert all(q.skippable and q.assumption for q in missing(p, _lid(p)))


def test_mh1_a_known_answer_is_never_asked_again() -> None:
    p = _photo_project(room_type="bedroom", cote_known=True)
    lid = _lid(p)
    level = p.levels[lid].model_copy(update={"height_source": Source.USER_DEFINED})
    p = p.model_copy(update={"levels": {lid: level}})
    assert missing(p, lid) == []


def test_mh1_attic_is_not_asked_its_ceiling_height() -> None:
    p = _photo_project(room_type="attic")
    lid = _lid(p)
    p = p.model_copy(update={"levels": {lid: p.levels[lid].model_copy(update={"kind": "attic"})}})
    assert "hauteur_plafond" not in [q.field for q in missing(p, lid)]


# ------------------------------------------------------------------ extraction et repli


def test_mh2_a_valid_answer_is_kept() -> None:
    out = extract(Scripted(json.dumps(HEIGHT)), "hauteur_plafond", {}, "2m45")
    assert out.mode == "assistant" and out.extraction == HEIGHT and out.attempts == 1


def test_mh2_two_tries_at_most_then_the_form() -> None:
    backend = Scripted("pas du JSON", json.dumps({**HEIGHT, "valeur": 9.0}), json.dumps(HEIGHT))
    out = extract(backend, "hauteur_plafond", {}, "2m45")
    assert out.mode == "formulaire" and out.reason == "sortie_invalide" and out.attempts == 2
    assert len(backend.calls) == 2


def test_mh2_a_second_try_can_succeed() -> None:
    out = extract(Scripted("```json\n{oups", json.dumps(HEIGHT)), "hauteur_plafond", {}, "2m45")
    assert out.mode == "assistant" and out.attempts == 2


@pytest.mark.parametrize(
    ("backend", "reason"),
    [
        (None, "absent"),
        (Scripted(AssistantTimeoutError("lent")), "trop_lent"),
        (Scripted(AdapterUnavailableError("en panne")), "indisponible"),
    ],
)
def test_mh3_without_a_working_model_the_form(backend: Scripted | None, reason: str) -> None:
    out = extract(backend, "hauteur_plafond", {}, "2m45")
    assert out.mode == "formulaire" and out.reason == reason and out.extraction is None


def test_mh3_the_20_s_budget_is_shared_by_both_tries() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    class Slow(Scripted):
        def extract(self, system: str, user: str, schema: Any, timeout_s: float) -> str:
            now[0] += 15.0
            return super().extract(system, user, schema, timeout_s)

    backend = Slow("invalide", json.dumps(HEIGHT))
    out = extract(backend, "hauteur_plafond", {}, "2m45", clock=clock)
    assert backend.calls[0] == pytest.approx(20.0)
    assert backend.calls[1] == pytest.approx(5.0)  # ce qui reste du budget, pas 20 s de plus
    assert out.mode == "formulaire" and out.reason == "trop_lent"


# ------------------------------------------------------------------ propositions


def _template(use_case: str) -> str:
    return next(u.path for u in REGISTRY if u.name == use_case)


def _matches(use_case: str, path: str) -> bool:
    pattern = re.sub(r"\{[^}]+\}", "[^/]+", _template(use_case))
    return re.fullmatch(pattern, path) is not None


def test_mh4_a_dimension_becomes_a_registry_use_case_with_app_buttons() -> None:
    p = _photo_project()
    lid = _lid(p)
    prop = propose(p, lid, question_for(p, lid, "hauteur_plafond"), HEIGHT)
    assert prop.kind == "action" and prop.value == pytest.approx(2.45)
    confirm = next(b for b in prop.buttons if b.kind == "confirm")
    assert confirm.action is not None
    assert confirm.action.use_case == "plan.recalibrate.dimensions:post"
    assert _matches(confirm.action.use_case, confirm.action.path)
    assert confirm.action.body == {"height_m": 2.45}
    assert {b.kind for b in prop.buttons} == {"confirm", "correct", "skip"}


def test_mh4_the_server_revalidates_the_bounds() -> None:
    p = _photo_project()
    lid = _lid(p)
    prop = propose(p, lid, question_for(p, lid, "hauteur_plafond"), {**HEIGHT, "valeur": 5.0})
    assert prop.kind == "precision" and all(b.action is None for b in prop.buttons)


def test_mh4_a_cote_of_a_door_is_not_used_to_rescale() -> None:
    p = _photo_project()
    lid = _lid(p)
    e = {**HEIGHT, "champ": "cote_connue", "valeur": 0.83, "element": "la porte"}
    prop = propose(p, lid, question_for(p, lid, "cote_connue"), e)
    assert prop.kind == "precision"
    e = {**e, "valeur": 3.9, "element": None}
    prop = propose(p, lid, question_for(p, lid, "cote_connue"), e)
    assert prop.buttons[0].action is not None and prop.buttons[0].action.body == {"width_m": 3.9}


def test_mh4_the_project_decides_generate_or_regenerate() -> None:
    p = _photo_project()
    lid = _lid(p)
    action = {**HEIGHT, "outil": "generate_plan", "champ": None, "valeur": None, "unite": None}
    prop = propose(p, lid, question_for(p, lid, "hauteur_plafond"), action)
    confirm = prop.buttons[0].action
    assert confirm is not None and confirm.use_case == "plan.images.autoplan:post"
    assert confirm.query == {"replace": "true"}  # le plan existe : régénérer
    assert _matches(confirm.use_case, confirm.path)
    renders = propose(
        p, lid, question_for(p, lid, "hauteur_plafond"), {**action, "outil": "launch_renders"}
    )
    assert renders.buttons[0].action is not None
    assert renders.buttons[0].action.use_case == "plan.render.all:post"


def test_mh5_skipping_says_what_the_app_supposes() -> None:
    p = _photo_project()
    lid = _lid(p)
    e = {**HEIGHT, "outil": "skip", "valeur": None, "unite": None, "certitude": "inconnue"}
    prop = propose(p, lid, question_for(p, lid, "hauteur_plafond"), e)
    assert prop.kind == "supposition" and prop.text == "Hauteur supposée : 2,50 m."


def test_mh4_a_room_type_is_a_value_not_an_action() -> None:
    p = _photo_project()
    lid = _lid(p)
    e = {**HEIGHT, "outil": "none", "champ": "type_piece", "valeur": "salon", "unite": None}
    prop = propose(p, lid, question_for(p, lid, "type_piece"), e)
    assert prop.kind == "valeur" and prop.value == "living_room"
    assert all(b.action is None for b in prop.buttons)


# ------------------------------------------------------------------ API


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Any]:
    clients: list[TestClient] = []

    def make(*, flag: bool, backend: Scripted | None = None) -> tuple[TestClient, Project]:
        store = ProjectStore(tmp_path / f"data{len(clients)}")
        project = _photo_project()
        store.save(project)
        app = create_app(store=store, settings=Settings(data_dir=store.root, assistant=flag))
        if backend is not None:
            app.state.assistant_backend = backend
        client = TestClient(app)
        clients.append(client)
        return client, project

    yield make
    for c in clients:
        c.close()


def test_mh6_flag_off_the_routes_do_not_exist(make_client: Any) -> None:
    client, p = make_client(flag=False)
    base = f"/api/projects/{p.id}/levels/{_lid(p)}/calage"
    assert client.get(f"{base}/missing").status_code == 404
    assert (
        client.post(f"{base}/answer", json={"field": "hauteur_plafond", "skip": True}).status_code
        == 404
    )


def test_mh6_flag_on_missing_then_answer(make_client: Any) -> None:
    backend = Scripted(json.dumps(HEIGHT))
    client, p = make_client(flag=True, backend=backend)
    base = f"/api/projects/{p.id}/levels/{_lid(p)}/calage"
    asked = client.get(f"{base}/missing").json()["questions"]
    assert [q["field"] for q in asked] == ["cote_connue", "hauteur_plafond", "type_piece"]
    done = client.post(f"{base}/answer", json={"field": "hauteur_plafond", "message": "2m45"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["mode"] == "assistant" and body["proposal"]["kind"] == "action"
    # Rien n'a été modifié : l'action attend le clic.
    after = client.get(f"/api/projects/{p.id}").json()
    assert after["levels"][str(_lid(p))]["height_source"] == "estimated"


def test_mh3_without_a_model_url_the_form(make_client: Any) -> None:
    client, p = make_client(flag=True)
    base = f"/api/projects/{p.id}/levels/{_lid(p)}/calage"
    body = client.post(
        f"{base}/answer", json={"field": "hauteur_plafond", "message": "2m45"}
    ).json()
    assert body["mode"] == "formulaire" and body["reason"] == "absent" and body["proposal"] is None
    assert body["question"]["minimum"] == 1.8 and body["question"]["maximum"] == 4.0


def test_mh5_skip_does_not_call_the_model(make_client: Any) -> None:
    backend = Scripted()
    client, p = make_client(flag=True, backend=backend)
    base = f"/api/projects/{p.id}/levels/{_lid(p)}/calage"
    body = client.post(f"{base}/answer", json={"field": "cote_connue", "skip": True}).json()
    assert body["proposal"]["kind"] == "supposition" and backend.calls == []


# ------------------------------------------------------------------ banc


def test_mh5_the_bench_measures_a_perfect_model_as_perfect() -> None:
    cases = ev.load_cases(CASES)
    replies = [json.dumps(c["attendu"], ensure_ascii=False) for c in cases]
    report = ev.run(Scripted(*replies), cases)
    assert report.rate("json_ok") == 1.0 and report.rate("champ") == 1.0
    assert report.rate("outil") == 1.0 and report.forbidden == 0 and report.passed
    assert len(report.categories()) == 8
    assert ev.lines(report)[-1] == "RÉSULTAT : seuils tenus"


def test_mh5_the_bench_counts_a_forbidden_action() -> None:
    case = next(c for c in ev.load_cases(CASES) if c["attendu"]["outil"] == "none")
    report = ev.run(Scripted(json.dumps(HEIGHT)), [case])
    assert report.forbidden == 1 and not report.passed


def test_mh5_the_cli_says_when_the_cases_are_missing(tmp_path: Path) -> None:
    code = main(
        ["assistant-eval", "--endpoint", "http://127.0.0.1:9", "--cases", str(tmp_path / "x")]
    )
    assert code == 2


def test_the_model_is_constrained_without_the_cross_rules_but_validated_with_them() -> None:
    from openplan.pipeline.calage.schema_check import constraint_schema

    full, sent = extraction_schema(), constraint_schema()
    assert "allOf" in full and "allOf" not in sent and "$schema" not in sent
    assert sent["required"] == full["required"] and sent["properties"] == full["properties"]
    assert not is_valid({**HEIGHT, "valeur": 1.5})  # la règle croisée reste vérifiée par l'app


# ------------------------------------------------------------------ phase 2 : faux modèle, santé


def _fake(field: str, message: str) -> dict[str, Any]:
    from openplan.pipeline.calage.extraction import SYSTEM, user_message
    from openplan.pipeline.calage.fake_backend import FakeAssistant

    out = FakeAssistant().extract(SYSTEM, user_message(field, {}, message), None, 20.0)
    data: dict[str, Any] = json.loads(out)
    assert is_valid(data), data
    return data


@pytest.mark.parametrize(
    ("message", "value"), [("2m45", 2.45), ("plutôt 2,60", 2.6), ("245 cm", 2.45), ("250", 2.5)]
)
def test_phase2_the_fake_model_reads_a_height(message: str, value: float) -> None:
    out = _fake("hauteur_plafond", message)
    assert out["outil"] == "set_dimension" and out["valeur"] == pytest.approx(value)


def test_phase2_the_fake_model_skips_asks_and_is_slow_on_demand() -> None:
    assert _fake("hauteur_plafond", "je passe")["outil"] == "skip"
    assert _fake("hauteur_plafond", "aucune idée")["besoin_clarification"] is True
    assert _fake("hauteur_plafond", "1m20")["besoin_clarification"] is True
    with pytest.raises(AssistantTimeoutError):
        _fake("hauteur_plafond", "trop lent")


def test_phase2_health_says_whether_the_assistant_is_on(tmp_path: Path) -> None:
    for flag in (False, True):
        store = ProjectStore(tmp_path / f"d{flag}")
        app = create_app(store=store, settings=Settings(data_dir=store.root, assistant=flag))
        with TestClient(app) as client:
            assert client.get("/api/health").json()["assistant"] is flag


def test_phase2_the_fake_model_is_refused_on_an_exposed_api(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from openplan.api.routes.calage import assistant_backend

    exposed = Settings(data_dir=tmp_path, assistant=True, assistant_url="fake", host="0.0.0.0")
    local = Settings(data_dir=tmp_path, assistant=True, assistant_url="fake")
    for settings, expected in ((exposed, None), (local, "assistant:fake")):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        backend = assistant_backend(request)  # type: ignore[arg-type]
        assert (backend.model_id if backend else None) == expected
