"""KAN-124 (phase 1) : l'assistant de calage, en API seulement, derrière OPENPLAN_ASSISTANT.

Deux routes, déclarées au registre (KAN-105) :

- `GET …/levels/{level_id}/calage/missing` (membre ≥ lecteur) : les questions qui manquent,
  calculées par l'app, dans l'ordre, chacune avec ce que l'app suppose si on la passe ;
- `POST …/levels/{level_id}/calage/answer` (membre ≥ éditeur) : la réponse libre à UNE de ces
  questions ; le modèle l'extrait (ou l'app passe au formulaire classique) et l'app rend sa
  proposition, avec ses boutons. Rien n'est modifié ici : une action est un cas d'usage existant
  du registre, que le front appelle après le clic (la route existante revalide et fait son pas
  d'annulation).

Drapeau éteint (défaut) : 404, comme si les routes n'existaient pas.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, JsonValue

from openplan.adapters.protocols import AssistantBackend
from openplan.api.deps import ProjectDep
from openplan.pipeline.calage.extraction import Outcome, Reason, extract
from openplan.pipeline.calage.missing import (
    Question,
    QuestionField,
    missing,
    question_for,
    summary,
)
from openplan.pipeline.calage.proposal import Proposal, propose, skipped
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import Project


def assistant_enabled(request: Request) -> None:
    """OPENPLAN_ASSISTANT éteint : les routes n'existent pas (404)."""
    if not request.app.state.settings.assistant:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(tags=["calage"], dependencies=[Depends(assistant_enabled)])


def assistant_backend(request: Request) -> AssistantBackend | None:
    """Le modèle de l'assistant (OPENPLAN_ASSISTANT_URL), construit une fois ; sans URL, None
    (le formulaire classique). Les tests posent `app.state.assistant_backend`."""
    state = request.app.state
    if not hasattr(state, "assistant_backend"):
        settings = state.settings
        backend: AssistantBackend | None = None
        if settings.assistant_url:
            from openplan.adapters.remote.assistant_backend import OpenAICompatibleAssistant

            backend = OpenAICompatibleAssistant(
                settings.assistant_url, settings.assistant_model, settings.assistant_key
            )
        state.assistant_backend = backend
    found: AssistantBackend | None = state.assistant_backend
    return found


BackendDep = Annotated[AssistantBackend | None, Depends(assistant_backend)]


class MissingResult(StrictModel):
    questions: list[Question]


class AnswerBody(StrictModel):
    field: QuestionField
    message: str = Field(default="", max_length=500)
    skip: bool = False
    """« Passer » : l'app dit ce qu'elle suppose, sans appeler le modèle."""
    previous: JsonValue = None
    """La réponse précédente à ce champ (une correction : « non, plutôt 2,60 »)."""


class AnswerResult(StrictModel):
    mode: Literal["assistant", "formulaire"]
    reason: Reason = ""
    """Pourquoi le formulaire : modèle absent, indisponible, trop lent, sortie invalide."""
    question: Question
    proposal: Proposal | None = None
    """Ce que l'app propose (mode « assistant ») ; None : le formulaire de `question`."""
    attempts: int = 0
    elapsed_ms: int = 0


def _level(project: Project, level_id: UUID) -> None:
    if level_id not in project.levels:
        raise HTTPException(status_code=404, detail=f"niveau inconnu : {level_id}")


@router.get("/projects/{project_id}/levels/{level_id}/calage/missing")
def calage_missing(level_id: UUID, project: ProjectDep) -> MissingResult:
    _level(project, level_id)
    return MissingResult(questions=missing(project, level_id))


@router.post("/projects/{project_id}/levels/{level_id}/calage/answer")
def calage_answer(
    level_id: UUID, body: AnswerBody, project: ProjectDep, backend: BackendDep
) -> AnswerResult:
    _level(project, level_id)
    question = question_for(project, level_id, body.field)
    if body.skip:
        return AnswerResult(mode="assistant", question=question, proposal=skipped(question))
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="réponse vide : répondez, ou passez")
    contexte: dict[str, Any] = summary(project, level_id, body.previous)
    outcome: Outcome = extract(backend, body.field, contexte, body.message)
    proposal = (
        propose(project, level_id, question, outcome.extraction)
        if outcome.mode == "assistant" and outcome.extraction is not None
        else None
    )
    return AnswerResult(
        mode="assistant" if proposal is not None else "formulaire",
        reason=outcome.reason,
        question=question,
        proposal=proposal,
        attempts=outcome.attempts,
        elapsed_ms=outcome.elapsed_ms,
    )
