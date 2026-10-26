"""Appels HTTPS sortants du poste vers app.plandesign.fr (jeton du poste en en-tête).

Tout ce qui revient du serveur est validé : la prise par `WorkerJob`, les fichiers par leur
schéma (`Project`, `Bake`, `RenderCamera`) après lecture plafonnée. Les fichiers écrits sur le
poste sont la version revalidée (re-sérialisée), sous des noms fixes choisis par le poste.

KAN-96 : le poste annonce ses capacités dans `WorkerClaim.version` ; il ne bat que si le serveur
a confirmé « heartbeat » (un ancien serveur ne connaît pas la route du battement : jamais
d'appel refusé en boucle). Le battement passe par une connexion à part : un envoi lent sur un
lien montant saturé ne le retient pas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.render_queue import (
    BEAT_EVERY_S,
    CAPABILITIES_HEADER,
    HEARTBEAT,
    JSON_FILES,
    WorkerClaim,
    WorkerFailure,
    WorkerJob,
    WorkerResult,
    parse_capabilities,
)
from openplan.schemas.scene import Project

FileName = Literal["project", "bake", "camera"]
SCHEMAS: dict[str, type[BaseModel]] = {"project": Project, "bake": Bake, "camera": RenderCamera}
USER_AGENT = "plandesign-worker/1"
CAPABILITIES = (HEARTBEAT,)
"""Ce que ce poste sait faire, annoncé dans chaque prise (jetons joints par « + »)."""
WRITE_TIMEOUT_S = 120.0
"""KAN-96 : un envoi de 2 à 3 Mo a pris 25,7 s sur le lien montant du domicile ; une écriture
bloquée plus longtemps coupe l'essai, qui est réessayé sans recalcul."""


class ApiRefusedError(Exception):
    """401 ou 403 : jeton refusé ou révoqué (le poste temporise longuement)."""


class ApiBusyError(Exception):
    """429, 5xx, ou réseau injoignable : le poste temporise (recul exponentiel plafonné)."""


class LeaseLostError(Exception):
    """La demande n'est plus à ce poste (404) : on l'abandonne sans rien signaler."""


class InvalidJobError(Exception):
    """Un fichier trop gros, qui ne passe pas son schéma, ou une vue disparue (410) : la
    demande échoue."""


class WorkerClient:
    def __init__(self, url: str, token: str, *, http: httpx.Client | None = None) -> None:
        """`http` : un client déjà construit (tests : le TestClient de l'application), qui sert
        aussi au battement."""
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
        if http is None:
            http = httpx.Client(
                base_url=url,
                timeout=httpx.Timeout(30.0, connect=10.0, write=WRITE_TIMEOUT_S),
                follow_redirects=False,
                verify=True,
            )
            beat_http = httpx.Client(
                base_url=url,
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=False,
                verify=True,
            )
        else:
            beat_http = http
        http.headers.update(headers)
        beat_http.headers.update(headers)
        self.http = http
        self.beat_http = beat_http
        self.beat_every_s: float | None = None
        """La cadence du battement annoncée par le serveur (None : il ne sait pas tenir le bail,
        le poste ne bat pas)."""

    def close(self) -> None:
        self.http.close()
        if self.beat_http is not self.http:
            self.beat_http.close()

    def _send(
        self, method: str, url: str, *, http: httpx.Client | None = None, **kwargs: object
    ) -> httpx.Response:
        try:
            response = (http or self.http).request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            raise ApiBusyError(type(exc).__name__) from exc
        self._check(response)
        return response

    @staticmethod
    def _check(response: httpx.Response) -> None:
        code = response.status_code
        if code in (401, 403):
            raise ApiRefusedError(str(code))
        if code == 429 or code >= 500:
            raise ApiBusyError(str(code))
        if code == 410:  # la vue n'existe plus : la demande échoue (« invalid_input »)
            raise InvalidJobError(str(code))
        if code == 404:
            raise LeaseLostError(str(code))
        if code >= 300:
            raise ApiBusyError(str(code))

    def claim(self, state: Literal["idle", "paused", "waiting"]) -> WorkerJob | None:
        response = self._send(
            "POST",
            "/api/worker/claim",
            json=WorkerClaim(state=state, version="+".join(CAPABILITIES)).model_dump(),
        )
        served = parse_capabilities(response.headers.get(CAPABILITIES_HEADER, ""))
        self.beat_every_s = float(BEAT_EVERY_S) if HEARTBEAT in served else None
        if response.status_code == 204:
            return None
        try:
            return WorkerJob.model_validate_json(response.content)
        except ValidationError as exc:
            raise ApiBusyError("réponse invalide") from exc

    def fetch(self, job: WorkerJob, name: FileName, target: Path) -> None:
        """Lecture plafonnée, validation par le schéma, écriture de la version revalidée."""
        cap = JSON_FILES[name]
        url = f"/api/worker/jobs/{UUID(str(job.request_id))}/files/{name}"
        headers = {"X-Render-Lease": str(job.lease)}
        try:
            with self.http.stream("GET", url, headers=headers) as response:
                self._check(response)
                declared = response.headers.get("content-length", "0")
                if declared.isdigit() and int(declared) > cap:
                    raise InvalidJobError(name)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > cap:
                        raise InvalidJobError(name)
        except httpx.HTTPError as exc:
            raise ApiBusyError(type(exc).__name__) from exc
        try:
            model = SCHEMAS[name].model_validate_json(bytes(body))
        except ValidationError as exc:
            raise InvalidJobError(name) from exc
        target.write_text(model.model_dump_json(), encoding="utf-8")

    def upload(self, job: WorkerJob, result: WorkerResult, image: bytes) -> None:
        self._send(
            "POST",
            f"/api/worker/jobs/{job.request_id}/result",
            headers={"X-Render-Lease": str(job.lease)},
            data={"meta": result.model_dump_json()},
            files={"image": ("render.png", image, "image/png")},
        )

    def beat(self, job: WorkerJob) -> None:
        """KAN-96 : la prise est toujours en main (calcul ou envoi), par la connexion à part."""
        self._send(
            "POST",
            f"/api/worker/jobs/{job.request_id}/beat",
            http=self.beat_http,
            headers={"X-Render-Lease": str(job.lease)},
            content=b"",
        )

    def fail(self, job: WorkerJob, failure: WorkerFailure) -> None:
        self._send(
            "POST",
            f"/api/worker/jobs/{job.request_id}/fail",
            headers={"X-Render-Lease": str(job.lease), "Content-Type": "application/json"},
            content=json.dumps(failure.model_dump()).encode(),
        )
