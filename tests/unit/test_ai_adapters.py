"""Client compatible OpenAI (transport simulé), passerelle de consentement, journal réseau."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import pytest

from openplan.adapters.gateway import (
    ConsentRequiredError,
    ModelGateway,
    check_consent,
    read_network_log,
)
from openplan.adapters.protocols import ImageArray, LlmMessage, LlmRequest, LlmResponse
from openplan.adapters.registry import AiConfig, build_language_model
from openplan.adapters.remote.openai_compatible import (
    ModelCallError,
    OpenAICompatibleModel,
    build_body,
    parse_response,
)
from openplan.api.settings import Settings, load_env_file
from openplan.scene.factory import new_project
from openplan.schemas import PrivacySettings, Project


def image() -> ImageArray:
    return ImageArray(image_id=uuid4(), pixels=np.full((2000, 1500, 3), 120, dtype=np.uint8))


def request(*, with_image: bool = False, json_mode: bool = False) -> LlmRequest:
    return LlmRequest(
        purpose="test",
        messages=[
            LlmMessage(role="system", content="Tu es sobre."),
            LlmMessage(role="user", content="Décris."),
        ],
        images=[image()] if with_image else [],
        json_mode=json_mode,
    )


def fake_server(captured: list[httpx.Request], *, status: int = 200) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        if status != 200:
            return httpx.Response(status, text="quota exceeded, please retry later")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    return httpx.MockTransport(handler)


def test_client_sends_openai_shape_and_parses_usage() -> None:
    captured: list[httpx.Request] = []
    model = OpenAICompatibleModel(
        "https://api.example.com/v1/", "vision-x", "sk-test", transport=fake_server(captured)
    )
    assert model.remote and model.destination == "api.example.com"
    assert model.model_id == "openai_compatible:api.example.com/vision-x"
    response = model.complete(request(with_image=True, json_mode=True))
    assert response == LlmResponse(
        text='{"ok": true}', model_id=model.model_id, input_tokens=12, output_tokens=3
    )
    (req,) = captured
    assert req.url == "https://api.example.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["model"] == "vision-x" and body["response_format"] == {"type": "json_object"}
    user = body["messages"][1]
    assert user["content"][0] == {"type": "text", "text": "Décris."}
    url = user["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert model.last_request_bytes == len(req.content) and model.last_response_bytes


def test_client_errors_are_clean() -> None:
    model = OpenAICompatibleModel(
        "http://localhost:11434/v1", "llava", transport=fake_server([], status=429)
    )
    assert not model.remote and model.destination == "localhost:11434"
    with pytest.raises(ModelCallError, match="429"):
        model.complete(request())

    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    offline = OpenAICompatibleModel(
        "http://127.0.0.1:1/v1", "x", transport=httpx.MockTransport(down)
    )
    with pytest.raises(ModelCallError, match="injoignable"):
        offline.complete(request())
    with pytest.raises(ModelCallError, match="choices"):
        parse_response({"error": "nope"}, "m")
    parts = {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}]}
    assert parse_response(parts, "m").text == "ab"
    with pytest.raises(ValueError, match="message utilisateur"):
        build_body(
            LlmRequest(
                purpose="test", messages=[LlmMessage(role="system", content="s")], images=[image()]
            ),
            "m",
        )


class FakeModel:
    model_id = "fake:test"
    supports_images = True
    destination = "example.invalid"

    def __init__(self, *, remote: bool, fail: bool = False) -> None:
        self.remote = remote
        self.fail = fail
        self.calls = 0
        self.last_request_bytes = 100
        self.last_response_bytes = 40

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.fail:
            msg = "panne simulée avec un contenu secret"
            raise RuntimeError(msg)
        return LlmResponse(text="réponse", model_id=self.model_id)


def consenting(project: Project) -> Project:
    return project.model_copy(
        update={"privacy": PrivacySettings(allow_remote_models=True, remote_consent=True)}
    )


def test_gateway_refuses_remote_without_double_consent(tmp_path: Path) -> None:
    project = new_project("p")
    remote = FakeModel(remote=True)
    gateway = ModelGateway(tmp_path / "network_log.jsonl")
    with pytest.raises(ConsentRequiredError, match="allow_remote_models"):
        gateway.complete(remote, request(), project=project)
    half = project.model_copy(update={"privacy": PrivacySettings(allow_remote_models=True)})
    with pytest.raises(ConsentRequiredError, match="remote_consent"):
        check_consent(remote, half)
    with pytest.raises(ConsentRequiredError):
        gateway.bind(remote, half)
    assert remote.calls == 0 and read_network_log(tmp_path / "network_log.jsonl") == []
    # Un modèle local ne demande rien.
    local = FakeModel(remote=False)
    assert gateway.complete(local, request(), project=project).text == "réponse"
    # Avec le double consentement, le distant passe : une sonde texte, puis l'image.
    assert gateway.bind(remote, consenting(project))(request(with_image=True)).text == "réponse"
    entries = read_network_log(tmp_path / "network_log.jsonl")
    assert [(e.remote, e.probe, e.images) for e in entries] == [
        (False, False, 0),
        (True, True, 0),
        (True, False, 1),
    ]
    assert entries[2].destination == "example.invalid" and entries[2].request_bytes == 100
    assert remote.calls == 2
    # Sonde encore valable : la requête suivante ne la refait pas.
    gateway.complete(remote, request(with_image=True), project=consenting(project))
    assert remote.calls == 3 and len(read_network_log(tmp_path / "network_log.jsonl")) == 4


class FlakyModel(FakeModel):
    """Distant qui refuse tout : la sonde échoue, l'image ne part jamais."""

    model_id = "fake:flaky"

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        assert not request.images, "aucune image ne doit être envoyée à un modèle qui refuse"
        msg = "503 high demand"
        raise RuntimeError(msg)


def test_gateway_probes_remote_before_sending_images(tmp_path: Path) -> None:
    from openplan.adapters.gateway import ModelUnavailableError

    gateway = ModelGateway(tmp_path / "log.jsonl", probe_ttl_s=0.0)
    flaky = FlakyModel(remote=True)
    with pytest.raises(ModelUnavailableError, match="avant tout envoi d'image"):
        gateway.complete(flaky, request(with_image=True), project=consenting(new_project("p")))
    (entry,) = read_network_log(tmp_path / "log.jsonl")
    assert entry.probe and entry.images == 0 and not entry.ok and flaky.calls == 1
    # Sans image, pas de sonde : l'appel part directement (et échoue normalement).
    with pytest.raises(RuntimeError):
        gateway.complete(flaky, request(), project=consenting(new_project("p")))
    assert flaky.calls == 2
    # Un modèle local n'est jamais sondé.
    local = FakeModel(remote=False)
    gateway.complete(local, request(with_image=True), project=new_project("p"))
    assert local.calls == 1


def test_gateway_logs_failures_without_content(tmp_path: Path) -> None:
    gateway = ModelGateway(tmp_path / "log.jsonl")
    with pytest.raises(RuntimeError):
        gateway.complete(FakeModel(remote=False, fail=True), request(), project=new_project("p"))
    (entry,) = read_network_log(tmp_path / "log.jsonl")
    assert entry.ok is False and entry.error is not None
    assert entry.error.startswith("RuntimeError")
    raw = (tmp_path / "log.jsonl").read_text(encoding="utf-8")
    assert "Décris" not in raw and "Tu es sobre" not in raw, "jamais le contenu des messages"
    assert ModelGateway(None).complete(FakeModel(remote=False), request(), project=new_project("q"))


def test_config_and_env_file(tmp_path: Path) -> None:
    assert not AiConfig().configured and build_language_model(AiConfig()) is None
    env = {"OPENPLAN_AI_BASE_URL": "http://127.0.0.1:11434/v1", "OPENPLAN_AI_MODEL": "llava"}
    model = build_language_model(AiConfig.from_env(env))
    assert model is not None and not model.remote
    assert AiConfig.from_env({**env, "GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}).api_key == "o"
    assert AiConfig.from_env(env).timeout_s == 600.0
    assert AiConfig.from_env({**env, "OPENPLAN_AI_TIMEOUT_S": "45"}).timeout_s == 45.0
    assert AiConfig.from_env({**env, "OPENPLAN_AI_TIMEOUT_S": "abc"}).timeout_s == 600.0
    assert AiConfig.from_env({**env, "OPENPLAN_AI_TIMEOUT_S": "-1"}).timeout_s == 600.0
    slow = build_language_model(AiConfig.from_env({**env, "OPENPLAN_AI_TIMEOUT_S": "45"}))
    assert getattr(slow, "timeout_s", None) == 45.0
    assert (
        AiConfig.from_env({**env, "OPENPLAN_AI_API_KEY": "k", "OPENAI_API_KEY": "o"}).api_key == "k"
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# commentaire\n\nexport OPENPLAN_AI_BASE_URL='https://api.openai.com/v1'\n"
        'OPENPLAN_AI_MODEL="gpt-4o-mini"\nOPENPLAN_PORT=9000\nligne sans egal\n',
        encoding="utf-8",
    )
    assert load_env_file(dotenv)["OPENPLAN_AI_BASE_URL"] == "https://api.openai.com/v1"
    settings = Settings.from_env({"OPENPLAN_PORT": "8001"}, dotenv=dotenv)
    assert settings.port == 8001, "l'environnement prime sur .env"
    assert settings.ai.model == "gpt-4o-mini" and settings.ai.configured
    assert Settings.from_env({}, dotenv=tmp_path / "absent").ai == AiConfig()
    # OPENPLAN_DOTENV : vide = aucun fichier lu (serveurs de test) ; sinon, ce fichier-là.
    assert not Settings.from_env({"OPENPLAN_DOTENV": ""}, dotenv=dotenv).ai.configured
    other = tmp_path / "autre.env"
    other.write_text("OPENPLAN_PORT=7007\n", encoding="utf-8")
    assert Settings.from_env({"OPENPLAN_DOTENV": str(other)}, dotenv=dotenv).port == 7007
