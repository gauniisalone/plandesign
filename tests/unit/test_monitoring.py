"""Suivi des erreurs (D4) : rien ne s'active sans DSN, et un événement chargé de données
personnelles ressort de `before_send` sans aucune d'entre elles."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from openplan import monitoring

CONSIGNE = "Un salon cosy pour ma fille Léa avec un canapé vert sapin"
PROJECT = "Maison de Marc à Vincennes"
PHOTO = "IMG_20261014_salon_lea.jpg"
HOME_PATH = f"/home/camille/Documents/maison-camille/{PHOTO}"
DATA_PATH = "/app/data/3f2b9c1e-8d4a-4b6f-9e2a-1c5d7e9f0a3b/images/originals/photo.png"
TOKEN = "Zk3q9XyT_2vLmN8pQr4sWu6tYb1cDe"  # faux jeton de test (filtrage) gitleaks:allow
PROJECT_ID = "3f2b9c1e-8d4a-4b6f-9e2a-1c5d7e9f0a3b"
EMAIL = "camille@example.org"
SECRETS = (CONSIGNE, PROJECT, PHOTO, "camille", "Documents", TOKEN, PROJECT_ID, EMAIL, "Léa")


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitoring, "_state", {"active": False})


def _dirty_event() -> dict[str, Any]:
    return {
        "level": "error",
        "server_name": "poste-camille",
        "transaction": f"/api/projects/{PROJECT_ID}/contextual",
        "user": {"id": "camille", "email": EMAIL, "ip_address": "203.0.113.7"},
        "extra": {"consigne": CONSIGNE, "project": PROJECT},
        "tags": {"projet": PROJECT},
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.12.4"},
            "projet": {"nom": PROJECT, "consigne": CONSIGNE},
        },
        "request": {
            "method": "POST",
            "url": f"https://app.plandesign.fr/api/projects/{PROJECT_ID}/images?name={PHOTO}",
            "query_string": f"name={PHOTO}&token={TOKEN}",
            "data": {"instruction": CONSIGNE, "name": PROJECT},
            "cookies": {"pd_session": TOKEN},
            "headers": {
                "Authorization": f"Bearer {TOKEN}",
                "Cookie": f"pd_session={TOKEN}",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                "X-Forwarded-For": "203.0.113.7",
            },
            "env": {"REMOTE_ADDR": "203.0.113.7"},
        },
        "exception": {
            "values": [
                {
                    "type": "OSError",
                    "value": (
                        f"échec pour « {PROJECT} » (consigne « {CONSIGNE} ») : "
                        f"{HOME_PATH} et {DATA_PATH}, jeton Bearer {TOKEN}, contact {EMAIL} "
                        + "x"
                        * 400
                    ),
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "openplan/pipeline/contextual.py",
                                "abs_path": "/home/camille/ai-agent-workspace/projects/"
                                "openplan-studio/openplan/pipeline/contextual.py",
                                "function": "generate",
                                "context_line": f"    open('{HOME_PATH}')",
                                "vars": {"instruction": CONSIGNE, "name": PROJECT},
                            }
                        ]
                    },
                }
            ]
        },
        "breadcrumbs": {
            "values": [
                {"category": "logging", "message": f"projet « {PROJECT} » : {CONSIGNE}"},
                {
                    "category": "httplib",
                    "message": f"GET {PHOTO}",
                    "data": {
                        "method": "GET",
                        "url": f"https://api.example/{PROJECT_ID}?q={CONSIGNE}",
                        "status_code": 500,
                        "body": CONSIGNE,
                    },
                },
            ]
        },
    }


def test_before_send_leaves_nothing_personal() -> None:
    event = monitoring.before_send(_dirty_event())
    dumped = json.dumps(event, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in dumped, secret
    assert "203.0.113.7" not in dumped
    # Ce qui aide à corriger reste.
    exception = event["exception"]["values"][0]
    assert exception["type"] == "OSError"
    assert len(exception["value"]) <= monitoring.MAX_MESSAGE
    frame = exception["stacktrace"]["frames"][0]
    assert frame["abs_path"] == "openplan/pipeline/contextual.py"
    assert frame["function"] == "generate"
    assert "vars" not in frame
    assert event["request"] == {
        "method": "POST",
        "url": "https://app.plandesign.fr/api/projects/<id>/images",
        "headers": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
    }
    assert set(event["contexts"]) == {"runtime"}
    crumbs = event["breadcrumbs"]["values"]
    assert [c["category"] for c in crumbs] == ["httplib"]
    assert "message" not in crumbs[0]
    assert crumbs[0]["data"] == {
        "method": "GET",
        "url": "https://api.example/<id>",
        "status_code": 500,
    }


def test_before_breadcrumb_drops_logs_and_cleans_http() -> None:
    assert monitoring.before_breadcrumb({"category": "logging", "message": CONSIGNE}) is None
    assert monitoring.before_breadcrumb({"type": "log", "category": "openplan"}) is None
    crumb = monitoring.before_breadcrumb(
        {"category": "httplib", "message": PHOTO, "data": {"url": f"/x/{PROJECT_ID}?a=1"}}
    )
    assert crumb == {"category": "httplib", "data": {"url": "/x/<id>"}}


def test_no_dsn_no_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(monitoring.DSN_ENV, raising=False)
    calls: list[int] = []

    def _never() -> Any:
        calls.append(1)
        raise AssertionError("sentry_sdk importé sans DSN")

    assert monitoring.init_monitoring(_never) is False
    monkeypatch.setenv(monitoring.DSN_ENV, "   ")
    assert monitoring.init_monitoring(_never) is False
    assert calls == []


def test_app_without_dsn_never_imports_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from openplan.api.app import create_app
    from openplan.api.settings import Settings

    monkeypatch.delenv(monitoring.DSN_ENV, raising=False)
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)
    app = create_app(settings=Settings(data_dir=tmp_path))
    assert "sentry_sdk" not in sys.modules
    # T6 : la mention du front ne parle de rapports d'erreur que s'ils partent vraiment.
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert client.get("/api/health").json()["error_reports"] is False


def test_init_options_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    fake = SimpleNamespace(init=lambda **kw: seen.update(kw))
    monkeypatch.setenv(monitoring.DSN_ENV, "https://cle@errors.example/1")
    monkeypatch.setenv(monitoring.SHA_ENV, "abcdef1234567890")
    monkeypatch.delenv(monitoring.ENV_ENV, raising=False)
    assert monitoring.init_monitoring(lambda: fake) is True
    assert seen["send_default_pii"] is False
    assert seen["include_local_variables"] is False
    assert seen["max_request_body_size"] == "never"
    assert seen["traces_sample_rate"] == 0
    assert seen["environment"] == "production"
    assert seen["release"].endswith("+abcdef123456")
    assert seen["before_send"] is monitoring.before_send
    assert seen["before_breadcrumb"] is monitoring.before_breadcrumb
    assert monitoring.dsn_host("https://cle@errors.example/1") == "errors.example"


def test_send_test_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[BaseException] = []
    fake = SimpleNamespace(
        init=lambda **_: None,
        capture_exception=lambda exc: sent.append(exc) or "evt123",
        flush=lambda timeout: None,
    )
    monkeypatch.delenv(monitoring.DSN_ENV, raising=False)
    assert monitoring.send_test_error(lambda: fake) is None
    monkeypatch.setenv(monitoring.DSN_ENV, "https://cle@errors.example/1")
    assert monitoring.send_test_error(lambda: fake) == "evt123"
    assert str(sent[0]) == monitoring.TEST_MESSAGE
