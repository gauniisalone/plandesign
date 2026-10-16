"""Passerelle vers les modèles de langage : consentement, puis journal réseau.

Règle (brief §3, docs/privacy/threat-model.md) : un modèle distant n'est appelé que si
`privacy.allow_remote_models` **et** `privacy.remote_consent` sont vrais pour le projet.
Chaque appel, local ou distant, réussi ou non, laisse une ligne dans
`<projet>/network_log.jsonl` : quoi (rôle), vers où, combien d'octets, combien de temps.
Jamais le contenu des messages ni des images.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import Field

from openplan.adapters.protocols import (
    LanguageModel,
    LlmMessage,
    LlmPurpose,
    LlmRequest,
    LlmResponse,
)
from openplan.schemas.common import StrictModel, now_utc
from openplan.schemas.scene import Project

NETWORK_LOG = "network_log.jsonl"
PROBE_TTL_S = 600.0
"""Une sonde réussie vaut pour dix minutes : au-delà, le modèle est revérifié avant image."""
PROBE_MESSAGE = LlmMessage(role="user", content="Réponds « ok ».")


class ConsentRequiredError(PermissionError):
    """Le projet n'autorise pas l'envoi de données à un service distant."""


class ModelUnavailableError(RuntimeError):
    """Le service distant refuse le modèle (retiré, saturé, quota) : aucune image envoyée."""


class NetworkLogEntry(StrictModel):
    timestamp: datetime = Field(default_factory=now_utc)
    model_id: str
    destination: str
    remote: bool
    purpose: LlmPurpose
    images: int
    request_bytes: int | None = None
    response_bytes: int | None = None
    duration_s: float = Field(ge=0.0)
    ok: bool
    error: str | None = None
    """Classe et statut de l'erreur, jamais le corps de la réponse."""
    probe: bool = False
    """Sonde texte sans image, envoyée avant une requête avec image à un service distant."""


def check_consent(model: LanguageModel, project: Project) -> None:
    if not model.remote:
        return
    privacy = project.privacy
    if not privacy.allow_remote_models:
        msg = (
            f"le modèle {model.model_id} est distant ({model.destination}) et le projet "
            "n'autorise pas les modèles distants (privacy.allow_remote_models)"
        )
        raise ConsentRequiredError(msg)
    if not privacy.remote_consent:
        msg = (
            f"envoi vers {model.destination} : confirmation par projet requise "
            "(privacy.remote_consent)"
        )
        raise ConsentRequiredError(msg)


def check_images(model: LanguageModel, project: Project | None, request: LlmRequest) -> None:
    """Un accord « texte seul » (case de l'accueil) ne laisse partir aucune image."""
    text_only = project is None or project.privacy.remote_text_only
    if model.remote and request.images and text_only:
        msg = (
            f"envoi d'image vers {model.destination} refusé : l'accord donné sur l'accueil "
            "vaut pour le texte seul"
        )
        raise ConsentRequiredError(msg)


_VERIFIED: dict[str, float] = {}
"""Modèles distants sondés avec succès (par identifiant), partagés par toutes les passerelles
du processus : l'API en crée une par requête."""


class ModelGateway:
    def __init__(self, log_path: Path | None, *, probe_ttl_s: float = PROBE_TTL_S) -> None:
        self.log_path = log_path
        self.probe_ttl_s = probe_ttl_s
        self._verified = _VERIFIED

    def complete(
        self, model: LanguageModel, request: LlmRequest, *, project: Project
    ) -> LlmResponse:
        check_consent(model, project)
        check_images(model, project, request)
        if model.remote and request.images:
            self._ensure_available(model, request.purpose)
        return self._call(model, request, probe=False)

    def _ensure_available(self, model: LanguageModel, purpose: LlmPurpose) -> None:
        """Avant d'envoyer une image à un service distant, une requête texte minuscule vérifie
        que le modèle répond (modèle retiré, saturé, quota épuisé, clé refusée) : un refus est
        journalisé sans qu'aucune image n'ait quitté la machine."""
        verified_at = self._verified.get(model.model_id)
        if verified_at is not None and time.monotonic() - verified_at < self.probe_ttl_s:
            return
        probe = LlmRequest(purpose=purpose, messages=[PROBE_MESSAGE], max_tokens=5, temperature=0.0)
        try:
            self._call(model, probe, probe=True)
        except Exception as exc:
            msg = (
                f"{model.destination} refuse le modèle avant tout envoi d'image "
                f"({type(exc).__name__}: {str(exc)[:200]})"
            )
            raise ModelUnavailableError(msg) from exc
        self._verified[model.model_id] = time.monotonic()

    def _call(self, model: LanguageModel, request: LlmRequest, *, probe: bool) -> LlmResponse:
        started = time.perf_counter()
        error: str | None = None
        response: LlmResponse | None = None
        try:
            response = model.complete(request)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
            raise
        finally:
            self._log(
                NetworkLogEntry(
                    model_id=model.model_id,
                    destination=model.destination,
                    remote=model.remote,
                    purpose=request.purpose,
                    images=len(request.images),
                    request_bytes=_optional_int(getattr(model, "last_request_bytes", None)),
                    response_bytes=_optional_int(getattr(model, "last_response_bytes", None)),
                    duration_s=time.perf_counter() - started,
                    ok=error is None,
                    error=error,
                    probe=probe,
                )
            )
        assert response is not None
        return response

    def bind(self, model: LanguageModel, project: Project) -> Callable[[LlmRequest], LlmResponse]:
        """Fonction d'appel liée à un projet, à confier à un adaptateur (détecteur VLM...)."""
        check_consent(model, project)
        return lambda request: self.complete(model, request, project=project)

    def bind_text_only(self, model: LanguageModel) -> Callable[[LlmRequest], LlmResponse]:
        """Fonction d'appel hors projet (aperçu de l'accueil, L2) : l'accord est la case
        « Mieux comprendre mon texte », retenue pour la machine et vérifiée par l'appelant ;
        le texte seul, jamais une image, et une ligne au journal réseau sans contenu."""

        def send(request: LlmRequest) -> LlmResponse:
            check_images(model, None, request)
            return self._call(model, request, probe=False)

        return send

    def _log(self, entry: NetworkLogEntry) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")


def read_network_log(path: Path) -> list[NetworkLogEntry]:
    if not path.is_file():
        return []
    return [
        NetworkLogEntry.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
