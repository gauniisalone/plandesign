"""Client « API compatible OpenAI » : ChatGPT, Gemini (point d'entrée compatible), serveurs
locaux Ollama / llama.cpp / vLLM. Un seul protocole HTTP, `POST <base_url>/chat/completions`.

`remote` est déduit de l'hôte : boucle locale = non distant. Les images partent en data URL
JPEG (EXIF déjà retiré par l'import). Le client mémorise la taille du dernier échange pour le
journal réseau ; il ne conserve jamais le contenu.
"""

from __future__ import annotations

import base64
import io
from typing import Any
from urllib.parse import urlparse

import httpx

from openplan.adapters.protocols import ImageArray, LlmRequest, LlmResponse

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
JPEG_QUALITY = 90
MAX_IMAGE_SIDE = 1600
"""Les images sont réduites avant envoi : moins d'octets, et la plupart des API
rééchantillonnent de toute façon."""
REGISTRY_ID = "openai_compatible_vlm@0.1"
"""Entrée correspondante de models/registry.yaml."""


class ModelCallError(RuntimeError):
    """Réponse HTTP en erreur, réseau injoignable ou réponse mal formée."""


class OpenAICompatibleModel:
    supports_images = True

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        remote: bool | None = None,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        self.destination = f"{host}:{parsed.port}" if parsed.port else host
        self.remote = remote if remote is not None else host not in LOCAL_HOSTS
        self.model_id = f"openai_compatible:{host}/{model}"
        self.timeout_s = timeout_s
        self._transport = transport
        self.last_request_bytes: int | None = None
        self.last_response_bytes: int | None = None

    def complete(self, request: LlmRequest) -> LlmResponse:
        body = build_body(request, self.model)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout_s,
                transport=self._transport,
            ) as client:
                response = client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            msg = f"{self.destination} injoignable : {exc.__class__.__name__}"
            raise ModelCallError(msg) from exc
        self.last_request_bytes = len(response.request.content)
        self.last_response_bytes = len(response.content)
        if response.status_code >= 400:
            msg = f"{self.destination} a répondu {response.status_code} : {response.text[:200]}"
            raise ModelCallError(msg)
        return parse_response(response.json(), self.model_id)


def build_body(request: LlmRequest, model: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in request.messages
    ]
    if request.images:
        last_user = next(
            (i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "user"), None
        )
        if last_user is None:
            msg = "des images sont jointes mais aucun message utilisateur ne peut les porter"
            raise ValueError(msg)
        parts: list[dict[str, Any]] = [{"type": "text", "text": messages[last_user]["content"]}]
        parts.extend(
            {"type": "image_url", "image_url": {"url": image_data_url(img)}}
            for img in request.images
        )
        messages[last_user]["content"] = parts
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def image_data_url(image: ImageArray) -> str:
    from PIL import Image

    pil = Image.fromarray(image.pixels)
    if max(pil.size) > MAX_IMAGE_SIDE:
        pil.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def parse_response(data: Any, model_id: str) -> LlmResponse:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        msg = "réponse sans choices[0].message.content"
        raise ModelCallError(msg) from exc
    if isinstance(content, list):  # certains serveurs renvoient des « parts »
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    if not isinstance(content, str):
        msg = f"contenu de réponse inattendu : {type(content).__name__}"
        raise ModelCallError(msg)
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return LlmResponse(
        text=content,
        model_id=model_id,
        input_tokens=_int_or_none(usage.get("prompt_tokens")),
        output_tokens=_int_or_none(usage.get("completion_tokens")),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None
