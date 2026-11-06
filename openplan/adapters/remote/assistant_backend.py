"""KAN-124 : le modèle de l'assistant de calage, derrière une API compatible OpenAI.

llama.cpp `llama-server` en local (décision d'augus du 6 nov. : modèle local léger, sans frais),
ou plus tard une API payante : changer de fournisseur ne touche que l'URL et la clé
(OPENPLAN_ASSISTANT_URL, OPENPLAN_ASSISTANT_KEY). Un appel : `POST <url>/chat/completions`, à
température 0, la sortie contrainte par le schéma (`response_format` de type `json_schema`) si
`schema` est donné. Le contenu n'est jamais journalisé ni gardé.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from openplan.adapters.protocols import AdapterUnavailableError, AssistantTimeoutError
from openplan.adapters.remote.openai_compatible import ModelCallError, parse_response

MAX_TOKENS = 256
"""La sortie est un petit objet JSON (sept clés) : de quoi la finir, pas davantage."""


class OpenAICompatibleAssistant:
    """`AssistantBackend` sur une API compatible OpenAI (llama-server, vLLM, Ollama…)."""

    def __init__(
        self,
        base_url: str,
        model: str = "local",
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        host = urlparse(self.base_url).hostname or ""
        self.model_id = f"assistant:{host}/{model}"
        self._transport = transport

    def extract(
        self, system: str, user: str, schema: dict[str, Any] | None, timeout_s: float
    ) -> str:
        """Le texte brut de la réponse (le JSON à valider par l'appelant).
        AssistantTimeoutError au-delà de `timeout_s` ; AdapterUnavailableError sinon."""
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema, "strict": True},
            }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout_s,
                transport=self._transport,
            ) as client:
                response = client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            msg = f"assistant : pas de réponse en {timeout_s:.0f} s"
            raise AssistantTimeoutError(msg) from exc
        except httpx.HTTPError as exc:
            msg = f"assistant injoignable : {exc.__class__.__name__}"
            raise AdapterUnavailableError(msg) from exc
        if response.status_code >= 400:
            msg = f"assistant : réponse {response.status_code}"
            raise AdapterUnavailableError(msg)
        try:
            return parse_response(response.json(), self.model_id).text
        except (ModelCallError, ValueError) as exc:
            msg = "assistant : réponse mal formée"
            raise AdapterUnavailableError(msg) from exc
