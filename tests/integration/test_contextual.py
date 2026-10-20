"""A1 : rendu contextualisé. Le prompt est enrichi par le plan (pièce, dimensions avec « ≈ »
quand elles sont estimées, ouvertures, lumière, contraintes), affiché avant la génération ;
les variantes partagent la graine et diffèrent par leur force ; chaque image porte ses
métadonnées ; le plan et la 3D ne bougent pas ; les cartes entrent dans le cadre Moodboard."""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from openplan.adapters.protocols import DepthMap, GeneratedImage, ImageArray
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.contextual import (
    PHOTO_VARIANTS,
    TOKEN_BUDGET,
    VARIANTS,
    approx_clip_tokens,
    context_lines,
)
from openplan.scene.ops import AddOpening, apply_op
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Source
from openplan.schemas.elements import OpeningKind, Wall
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


class _Inspirer:
    def __init__(self) -> None:
        self.model_ids = ["fake-sd15"]
        self.requests: list[Any] = []

    def finish(self, request: Any) -> GeneratedImage:  # pragma: no cover - non appelé
        raise NotImplementedError

    def inspire(self, request: Any) -> GeneratedImage:
        self.requests.append(request)
        return GeneratedImage(
            pixels=np.full(request.image.shape, 180, np.uint8),
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=0.2,
        )


class _Depth:
    model_id = "fake_depth"

    def predict(self, image: ImageArray) -> DepthMap:
        rows = np.linspace(6.0, 1.0, image.height, dtype=np.float32)
        return DepthMap(
            depth=np.repeat(rows[:, None], image.width, axis=1), metric=True, model_id=self.model_id
        )


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (96, 72), (170, 150, 130)).save(buf, format="PNG")
    return buf.getvalue()


def _project_with_window() -> Project:
    project = rect_room_project(4.1, 3.9)
    level = only_level(project)
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    return apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=wall.id,
            kind=OpeningKind.WINDOW,
            offset=1.0,
            width=1.2,
            height=1.2,
            sill_height=0.9,
        ),
    )


def _client(
    tmp_path: Path, project: Project, inspirer: _Inspirer | None = None
) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    app = create_app(
        store=store,
        settings=Settings(data_dir=tmp_path / "data"),
        image_finisher=inspirer,
        depth_estimator=_Depth(),
    )
    return TestClient(app), store


def _photo(client: TestClient, project: Project) -> str:
    r = client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["image"]["id"])


def test_prompt_is_enriched_from_the_plan_and_shown_before_generating(tmp_path: Path) -> None:
    project = _project_with_window()
    client, _ = _client(tmp_path, project)
    lid = only_level(project).id
    image = _photo(client, project)
    r = client.post(
        f"/api/projects/{project.id}/levels/{lid}/contextual/prompt",
        json={
            "intention": "chambre chaleureuse, bois clair et lin, lumière du soir",
            "budget": "haut_de_gamme",
            "source": {"kind": "photo", "id": image},
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    prompt = out["prompt"]
    # Emprise intérieure de la pièce (4,1 × 3,9 m d'axe en axe, murs de 15 cm).
    assert re.search(r"room about (3\.9|4\.0) by (3\.7|3\.8) metres", prompt), prompt
    # La pièce de test a déjà une porte et une fenêtre ; la fenêtre ajoutée fait deux.
    for word in ("2 windows", "1 door", "premium", "evening"):
        assert word in prompt, (word, prompt)
    assert "same walls doors and windows in place" in prompt
    assert out["reader"] == "phrases" and "chaleureux" in " ".join(out["understood"])
    # A2 : ce que le lecteur a compris, en mots simples et en français, et la traduction
    # française du prompt construit (lecture seule à l'écran ; le modèle lit l'anglais).
    assert "chaleureux" in out["summary"], out["summary"]
    fr = out["prompt_fr"]
    assert fr.startswith("photographie d'intérieur réaliste d'une chambre"), fr
    for word in ("2 fenêtres", "1 porte", "haut de gamme", "lampes du soir", "mêmes murs"):
        assert word in fr, (word, fr)
    assert "metres" not in fr and "premium" not in fr
    context = "\n".join(out["context"])
    assert "Ouverture : fenêtre de 1,20 m côté" in context and "allège 0,90 m" in context
    assert "Lumière naturelle" in context and "Contraintes : murs, portes et fenêtres" in context
    assert out["estimated"] == ("≈" in context)
    # Sans intention : le prompt reste construit (pièce, géométrie), lecteur « none ».
    empty = client.post(
        f"/api/projects/{project.id}/levels/{lid}/contextual/prompt",
        json={"source": {"kind": "photo", "id": image}},
    ).json()
    assert empty["reader"] == "none" and "metres" in empty["prompt"]


def test_prompt_stays_under_the_clip_budget_and_keeps_the_user_text(tmp_path: Path) -> None:
    """A4 : le modèle coupe à 77 jetons sans prévenir ; le prompt construit reste sous le
    budget estimé, le texte de l'utilisateur vient juste après la pièce et le style, et c'est
    la fin (finale, géométrie) qui saute, jamais lui."""
    project = _project_with_window()
    client, _ = _client(tmp_path, project)
    lid = only_level(project).id
    image = _photo(client, project)
    long_text = (
        "chambre chaleureuse, chalet, vieux bois, pierre apparente, plaids en laine, lin, "
        "chêne clair, lit double, deux tables de nuit, banquette sous la fenêtre, tapis "
        "berbère, lampe en rotin, lumière du soir"
    )
    out = client.post(
        f"/api/projects/{project.id}/levels/{lid}/contextual/prompt",
        json={"intention": long_text, "source": {"kind": "photo", "id": image}},
    ).json()
    prompt = out["prompt"]
    assert out["tokens"] == approx_clip_tokens(prompt) <= TOKEN_BUDGET, prompt
    said = " ".join(long_text.split()[:25])
    assert said in prompt
    # L'utilisateur d'abord, la géométrie ensuite ; les matières par surface ont sauté.
    assert "metres" not in prompt or prompt.index(said) < prompt.index("metres")
    assert "floorboards" not in prompt and "sloped ceiling" not in prompt
    # Court : le texte, la géométrie et la finale tiennent.
    short = client.post(
        f"/api/projects/{project.id}/levels/{lid}/contextual/prompt",
        json={"intention": "scandinave", "source": {"kind": "photo", "id": image}},
    ).json()
    assert "architectural digest" in short["prompt"] and short["tokens"] <= TOKEN_BUDGET
    assert short["prompt"].index("scandinave") < short["prompt"].index("metres")


def test_estimated_walls_are_marked_with_approx() -> None:
    """Murs estimés (photo, plan généré) : « ≈ » sur les cotes ; murs saisis : rien."""
    project = rect_room_project(5.0, 4.0)
    level = only_level(project)

    def with_walls(source: Source) -> Project:
        elements = {
            eid: (e.model_copy(update={"source": source}) if isinstance(e, Wall) else e)
            for eid, e in level.elements.items()
        }
        return project.model_copy(
            update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
        )

    lines, flagged, _ = context_lines(with_walls(Source.ESTIMATED), level.id, None)
    assert flagged and any("≈ 4,85 m" in line for line in lines), lines
    lines, flagged, _ = context_lines(with_walls(Source.USER_DEFINED), level.id, None)
    assert not flagged and not any("≈ 4.85" in line for line in lines), lines


def _wait(client: TestClient, pid: str, job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(100):
        if job["status"] != "running":
            return job
        time.sleep(0.05)
        job = client.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
    raise AssertionError("tâche trop longue")


def test_three_variants_share_the_seed_and_never_touch_the_plan(tmp_path: Path) -> None:
    project = _project_with_window()
    inspirer = _Inspirer()
    client, store = _client(tmp_path, project, inspirer)
    pid, lid = str(project.id), only_level(project).id
    image = _photo(client, project)
    before = store.load(project.id)
    body = {
        "intention": "scandinave, bois clair",
        "budget": "economique",
        "source": {"kind": "photo", "id": image, "label": "Photo 1"},
        "prompt": "photorealistic interior photograph of a bedroom, scandinavian, light oak",
        "seed": 7,
    }
    with client:
        job = client.post(f"/api/projects/{pid}/levels/{lid}/contextual", json=body)
        assert job.status_code == 202, job.text
        done = _wait(client, pid, job.json())
    assert done["status"] == "done", done.get("error")
    entries = done["result"]["entries"]
    assert [e["variant"] for e in entries] == ["conservatrice", "creative", "radicale"]
    # Source photo (A4) : réglages « photo », profondeur seule, pas d'arêtes.
    assert [(e["strength"], e["control_scale"]) for e in entries] == [
        PHOTO_VARIANTS[v] for v in PHOTO_VARIANTS
    ]
    assert PHOTO_VARIANTS != VARIANTS and all(e["guided_by"] == ["profondeur"] for e in entries)
    assert {e["seed"] for e in entries} == {7} and all(e["estimated"] is not None for e in entries)
    assert all(e["prompt"] == body["prompt"] and e["source"]["label"] == "Photo 1" for e in entries)
    assert all(
        r.mode == "img2img" and r.depth is not None and r.edges is None for r in inspirer.requests
    )
    # A4b : 640 px, pour tenir sous 5,5 Go de VRAM avec l'IP-Adapter.
    assert all(r.max_side == 640 for r in inspirer.requests)
    # Le projet n'a pas bougé : même historique, mêmes éléments.
    after = store.load(project.id)
    assert after.history_cursor == before.history_cursor and after.levels == before.levels
    # Liste, image, et cadre Moodboard du tableau.
    listed = client.get(f"/api/projects/{pid}/contextual").json()
    assert len(listed) == 3 and listed[0]["label"].endswith("proportions estimées")
    run = entries[0]["run_id"]
    assert client.get(f"/api/projects/{pid}/runs/{run}/contextual/creative.png").status_code == 200
    assert client.get(f"/api/projects/{pid}/runs/{run}/contextual/x.png").status_code == 404
    board = client.get(f"/api/projects/{pid}/board").json()
    frame = next(i for i in board["items"] if i["role"] == "moodboard")
    cards = [i for i in board["items"] if i["kind"] == "contextual"]
    assert len(cards) == 3 and all(c["frame"] == frame["id"] for c in cards)
    assert {c["ref"] for c in cards} == {f"{run}:{v}" for v in VARIANTS}
    assert cards[0]["text"].startswith("Conservatrice · Photo 1")


def test_references_guide_the_look_and_are_recorded(tmp_path: Path) -> None:
    """A2 : les images du tableau cochées « Référence » (photo du projet, image jointe)
    arrivent au modèle avec leur poids, sont nommées dans les métadonnées ; une référence
    inconnue ou un fichier joint qui n'est pas une image sont refusés avant la tâche."""
    project = _project_with_window()
    inspirer = _Inspirer()
    client, _ = _client(tmp_path, project, inspirer)
    pid, lid = str(project.id), only_level(project).id
    image = _photo(client, project)
    joined = client.post(
        f"/api/projects/{pid}/media", files={"file": ("inspi.png", _png(), "image/png")}
    )
    assert joined.status_code == 201, joined.text
    media_id = joined.json()["id"]
    text_file = client.post(
        f"/api/projects/{pid}/media", files={"file": ("notes.txt", b"pas une image", "text/plain")}
    ).json()["id"]
    # Le tableau garde la coche « Référence » sur l'élément.
    board = client.get(f"/api/projects/{pid}/board").json()
    photo_item = next(i for i in board["items"] if i["kind"] == "photo")
    assert photo_item["reference"] is False
    photo_item["reference"] = True
    # A3 : « À conserver » et le commentaire attaché voyagent avec la carte.
    photo_item["kept"] = True
    photo_item["comment"] = "garder le lit"
    saved = client.put(f"/api/projects/{pid}/board", json=board)
    assert saved.status_code == 200, saved.text
    again = client.get(f"/api/projects/{pid}/board").json()
    photo_again = next(i for i in again["items"] if i["kind"] == "photo")
    assert photo_again["reference"] is True and photo_again["kept"] is True
    assert photo_again["comment"] == "garder le lit"
    too_long = {**photo_item, "comment": "x" * 2001}
    assert (
        client.put(f"/api/projects/{pid}/board", json={**again, "items": [too_long]}).status_code
        == 422
    )

    base = {
        "source": {"kind": "photo", "id": image, "label": "Photo 1"},
        "prompt": "photorealistic interior photograph of a bedroom, warm chalet",
        "variants": ["creative"],
        "seed": 3,
    }
    for bad in (
        {"kind": "photo", "ref": "00000000-0000-4000-8000-000000000009"},
        {"kind": "file", "ref": text_file, "label": "notes.txt"},
        {"kind": "render", "ref": "00000000-0000-4000-8000-000000000008"},
    ):
        r = client.post(
            f"/api/projects/{pid}/levels/{lid}/contextual", json={**base, "references": [bad]}
        )
        assert r.status_code == 409 and "référence" in r.json()["detail"], (bad, r.text)
    assert inspirer.requests == []

    body = {
        **base,
        "variants": ["creative", "radicale"],
        "references": [
            {"kind": "photo", "ref": image, "label": "Photo 1"},
            {"kind": "file", "ref": media_id, "label": "inspi.png"},
        ],
        "reference_scale": 0.7,
    }
    with client:
        job = client.post(f"/api/projects/{pid}/levels/{lid}/contextual", json=body)
        assert job.status_code == 202, job.text
        done = _wait(client, pid, job.json())
    assert done["status"] == "done", done.get("error")
    request = inspirer.requests[0]
    assert len(request.references) == 2 and request.reference_scale == 0.7
    assert request.references[1].shape == (72, 96, 3)
    creative, radical = done["result"]["entries"]
    assert creative["references"] == ["Photo 1", "inspi.png"] and creative["reference_scale"] == 0.7
    # La radicale écoute davantage les références (A4 : +0,2, borné à 1).
    assert radical["reference_scale"] == 0.9 and inspirer.requests[1].reference_scale == 0.9
    # Sans référence : rien n'est envoyé, rien n'est noté.
    with client:
        job = client.post(f"/api/projects/{pid}/levels/{lid}/contextual", json=base)
        done = _wait(client, pid, job.json())
    assert inspirer.requests[-1].references == []
    assert done["result"]["entries"][0]["references"] == []
    assert done["result"]["entries"][0]["reference_scale"] is None


def test_a_view_without_render_is_refused(tmp_path: Path) -> None:
    project = rect_room_project(4.0, 3.0)
    client, _ = _client(tmp_path, project, _Inspirer())
    pid, lid = str(project.id), only_level(project).id
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/contextual",
        json={
            "source": {"kind": "render", "id": "00000000-0000-4000-8000-000000000001"},
            "prompt": "a room",
        },
    )
    assert r.status_code == 409 and "rendez-la d'abord" in r.json()["detail"]
    bad = client.post(
        f"/api/projects/{pid}/levels/{lid}/contextual",
        json={
            "source": {"kind": "photo", "id": "00000000-0000-4000-8000-000000000002"},
            "prompt": "a",
        },
    )
    assert bad.status_code == 409
