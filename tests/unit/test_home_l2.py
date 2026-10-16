"""Accueil L2 : questions guidées → intention (elles priment sur le texte), garde-fou de
surface (recalage borné, annulable), contrôle « respect de la demande »."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openplan.adapters.gateway import ConsentRequiredError, ModelGateway
from openplan.adapters.protocols import LlmMessage, LlmRequest, LlmResponse
from openplan.adapters.remote.openai_compatible import ModelCallError
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline import autopilot as ap
from openplan.pipeline import home_settings
from openplan.pipeline.autopilot import HomeRequest, Workspace, run_autopilot
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.guided import (
    AREA_GUARD_KEY,
    answer_fields,
    area_guard,
    check_request,
    merge_fields,
    undo_area_guard,
)
from openplan.pipeline.instruction import furniture_words, translate_phrases
from openplan.pipeline.starter import STARTER_KEY, starter_plan
from openplan.scene.catalog import load_catalog
from openplan.scene.factory import new_project
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Room
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level

CATEGORIES = load_catalog().categories


def _room(project: Project) -> Room:
    return next(e for e in only_level(project).elements.values() if isinstance(e, Room))


def _area(project: Project) -> float:
    level = only_level(project)
    return compute_derived(level).rooms[_room(project).id].area_m2


def _rectangle(area: float) -> Project:
    """Pièce rectangulaire « mesurée » (pas un plan de départ) de cette surface."""
    project = new_project("Essai")
    after = starter_plan(project, only_level(project).id, area, actor="pipeline:starter")
    level = only_level(after)
    room = _room(after)
    metadata = {k: v for k, v in room.metadata.items() if k != STARTER_KEY}
    elements = {**level.elements, room.id: room.model_copy(update={"metadata": metadata})}
    return after.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )


def test_answers_become_intent_fields() -> None:
    answers = GuidedAnswers(
        space="combles",
        style="scandinave",
        bedrooms=2,
        aspect="4:5",
        light_time="soir",
        mood=["cosy"],
        furniture=["desk"],
        avoid=["rug", "inconnu"],
    )
    fields = answer_fields(answers, CATEGORIES)
    assert fields["room_type"] == "attic"
    assert fields["style"] == "scandinave"
    assert fields["views"] == {"aspect": "4:5"}
    assert fields["light"] == {"time": "soir"}
    assert fields["zones"] == ["couchage"], "N chambres : une zone couchage dans la pièce"
    assert {"category": "bed_double", "count": 2} in fields["furniture"]  # type: ignore[operator]
    assert fields["avoid"] == ["rug"], "une catégorie inconnue est écartée"
    # « Laisser choisir » : pas de style imposé.
    assert "style" not in answer_fields(GuidedAnswers(style=None), CATEGORIES)


def test_answers_win_over_the_text_and_furniture_adds_up() -> None:
    text = translate_phrases("salon chalet avec deux fauteuils", CATEGORIES).fields
    chosen = answer_fields(GuidedAnswers(style="contemporain", furniture=["bookshelf"]), CATEGORIES)
    merged = merge_fields(text, chosen)
    assert merged["style"] == "contemporain"
    categories = {f["category"] for f in merged["furniture"]}
    assert {"armchair", "bookshelf"} <= categories


def test_area_guard_rescales_beyond_15_percent_and_undoes_in_one_click() -> None:
    project = _rectangle(12.0)
    level_id = only_level(project).id
    same, kept = area_guard(project, level_id, 13.0, actor="pipeline:area-guard")
    assert not kept.applied and same is project, "écart de moins de 15 % : rien ne change"
    after, result = area_guard(project, level_id, 20.0, actor="pipeline:area-guard")
    assert result.applied and result.factor == pytest.approx((20 / 12) ** 0.5, abs=0.01)
    assert _area(after) == pytest.approx(20.0, rel=0.06)
    assert after.history[-1].actor == "pipeline:area-guard"
    assert AREA_GUARD_KEY in _room(after).metadata
    undone = undo_area_guard(after, level_id, actor="user")
    assert undone is not None and _area(undone) == pytest.approx(_area(project), rel=0.01)
    assert AREA_GUARD_KEY not in _room(undone).metadata
    # Borné : jamais plus de ×1,6 en longueur.
    _, big = area_guard(project, level_id, 400.0, actor="pipeline:area-guard")
    assert big.factor == 1.6


def test_area_guard_leaves_a_starter_plan_alone() -> None:
    project = new_project("Essai")
    level_id = only_level(project).id
    starter = starter_plan(project, level_id, 30.0, actor="pipeline:starter")
    _, result = area_guard(starter, level_id, 60.0, actor="pipeline:area-guard")
    assert not result.applied


def _with_furniture(project: Project, categories: list[str]) -> Project:
    level = only_level(project)
    centre = compute_derived(level).rooms[_room(project).id].polygon[0]
    for i, category in enumerate(categories):
        project = apply_op(
            project,
            level.id,
            AddFurniture(
                asset_id=next(a.id for a in load_catalog().assets if a.category == category),
                position=Point2(x=centre.x + 1.0 + i * 0.1, y=centre.y + 1.0),
            ),
        )
    return project


def test_request_check_lists_each_demand_with_a_reason() -> None:
    project = _with_furniture(_rectangle(30.0), ["bed_double", "bed_double", "sofa"])
    level_id = only_level(project).id
    translation = translate_phrases("salon chalet avec cheminée, sans canapé", CATEGORIES)
    answers = GuidedAnswers(bedrooms=2, area_m2=30.0, furniture=["bookshelf"], aspect="4:5")
    items = {(i.label, i.wanted): i for i in check_request(project, level_id, answers, translation)}
    rooms = items[("Chambres", "2")]
    assert rooms.status == "partial" and "seule pièce" in rooms.detail, "honnête avant L3"
    assert items[("Surface", "30 m²")].status == "ok"
    assert items[("Meuble", furniture_words("bookshelf", 1))].status == "missing"
    assert items[("À éviter", furniture_words("sofa", 1))].status == "missing", "posé malgré tout"
    assert items[("Demande", "cheminée")].detail == "pas au catalogue"
    assert items[("Format", "portrait 4:5")].status == "missing", "l'intention n'a pas ce format"


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")

    monkeypatch.setattr(ap, "render_camera_view", render)


def test_guided_answers_reach_the_room_and_the_run_reports_them(
    tmp_path: Path, fake_render: None
) -> None:
    project = attic_project(camera=False)
    saved = {"p": project}
    answers = GuidedAnswers(style="industriel", aspect="4:5", avoid=["rug", "rug_round"])
    run = run_autopilot(
        Workspace(lambda: saved["p"], lambda p: saved.update(p=p)),
        only_level(project).id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        home=HomeRequest(answers=answers),
    )
    intent = _room(saved["p"]).intent
    assert intent is not None and intent.style == "industriel" and intent.chosen_by_user("style")
    assert intent.views.aspect == "4:5"
    placed = {
        e.category for e in only_level(saved["p"]).elements.values() if hasattr(e, "category")
    }
    assert not placed & {"rug", "rug_round"}
    status = {(i.label, i.wanted): i.status for i in run.request}
    assert status[("Style", "Industriel")] == "ok"
    assert status[("Format", "portrait 4:5")] == "ok"
    assert status[("À éviter", furniture_words("rug", 1))] == "ok"


class RemoteText:
    """Modèle distant simulé (Gemini) : rend une traduction, ou échoue comme un quota épuisé."""

    model_id = "fake:gemini"
    remote = True
    supports_images = True
    destination = "generativelanguage.googleapis.com"

    def __init__(self, answer: str = "", *, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if self.fail:
            msg = "HTTP 429"
            raise ModelCallError(msg)
        return LlmResponse(text=self.answer, model_id=self.model_id)


GEMINI_READS = (
    '{"room_type": "attic", "style": "scandinave", "mood": ["cosy"], '
    '"furniture": [{"category": "desk", "count": 1}]}'
)


def _home_client(tmp_path: Path, model: RemoteText | None) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data")
    return TestClient(create_app(settings=settings, language_model=model))


def test_gemini_box_is_offered_only_with_a_remote_model_and_starts_unchecked(
    tmp_path: Path,
) -> None:
    with _home_client(tmp_path, None) as c:
        view = c.get("/api/home/settings").json()
        assert view == {"gemini_text": False, "remote_model": False, "destination": None}
    with _home_client(tmp_path, RemoteText(GEMINI_READS)) as c:
        assert c.get("/api/home/settings").json()["gemini_text"] is False, "décochée"
        assert c.put("/api/home/settings", json={"gemini_text": True}).json()["gemini_text"]
    # Retenue pour la machine : un nouveau démarrage la retrouve.
    with _home_client(tmp_path, RemoteText(GEMINI_READS)) as c:
        assert c.get("/api/home/settings").json()["gemini_text"] is True


def test_preview_reads_with_gemini_only_when_checked_and_logs_without_content(
    tmp_path: Path,
) -> None:
    text = "combles scandinave cosy avec un bureau"
    model = RemoteText(GEMINI_READS)
    with _home_client(tmp_path, model) as c:
        # Case cochée côté page mais accord non retenu : rien ne part.
        r = c.post("/api/instruction/preview", json={"text": text, "use_model": True}).json()
        assert r["understanding"] == "phrases" and model.requests == []
        c.put("/api/home/settings", json={"gemini_text": True})
        r = c.post("/api/instruction/preview", json={"text": text, "use_model": True}).json()
        assert r["understanding"] == "model" and r["notice"] == ""
        assert "scandinave" in r["translation"]["summary"]
        assert furniture_words("desk", 1) in r["translation"]["summary"]
        assert len(model.requests) == 1 and not model.requests[0].images
    log = (tmp_path / "data" / home_settings.NETWORK_LOG).read_text(encoding="utf-8")
    assert '"remote":true' in log and "scandinave" not in log, "jamais le contenu"


def test_preview_falls_back_to_phrases_and_says_so_when_gemini_fails(tmp_path: Path) -> None:
    with _home_client(tmp_path, RemoteText(fail=True)) as c:
        c.put("/api/home/settings", json={"gemini_text": True})
        r = c.post(
            "/api/instruction/preview", json={"text": "chambre chalet", "use_model": True}
        ).json()
    assert r["understanding"] == "phrases" and "429" in r["notice"]
    assert "chalet" in r["translation"]["summary"]


def test_checked_box_gives_projects_a_text_only_consent(tmp_path: Path) -> None:
    with _home_client(tmp_path, RemoteText(GEMINI_READS)) as c:
        before = c.post("/api/projects", json={"remote_text_consent": True}).json()
        assert before["privacy"]["remote_consent"] is False, "accord non retenu : rien"
        c.put("/api/home/settings", json={"gemini_text": True})
        project = c.post("/api/projects", json={"remote_text_consent": True}).json()
    privacy = project["privacy"]
    assert privacy["allow_remote_models"] and privacy["remote_consent"]
    assert privacy["remote_text_only"]


def test_text_only_consent_never_lets_an_image_leave() -> None:
    project = new_project("Essai")
    privacy = project.privacy.model_copy(
        update={"allow_remote_models": True, "remote_consent": True, "remote_text_only": True}
    )
    project = project.model_copy(update={"privacy": privacy})
    model = RemoteText("ok")
    gateway = ModelGateway(None)
    text = LlmRequest(purpose="intention", messages=[LlmMessage(role="user", content="lit")])
    assert gateway.complete(model, text, project=project).text == "ok"
    photo = text.model_copy(update={"images": [np.zeros((4, 4, 3), dtype=np.uint8)]})
    with pytest.raises(ConsentRequiredError):
        gateway.complete(model, photo, project=project)
    with pytest.raises(ConsentRequiredError):
        gateway.bind_text_only(model)(photo)
    assert len(model.requests) == 1
