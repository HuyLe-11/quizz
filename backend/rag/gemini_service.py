"""Small, Supabase-independent client for Gemini text and embeddings."""

from __future__ import annotations

import math
import os
from typing import Any

import httpx
from dotenv import load_dotenv


DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GENERATION_MODEL = "gemini-3.5-flash-lite"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS = 1536
MAX_EMBEDDING_DIMENSIONS = 3072
SUPPORTED_TASK_TYPES = frozenset(
    {"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"}
)


class GeminiServiceError(RuntimeError):
    """Base error for Gemini service failures."""


class GeminiConfigurationError(GeminiServiceError):
    """Raised when required Gemini configuration is missing or invalid."""


class GeminiValidationError(GeminiServiceError, ValueError):
    """Raised when an input or Gemini response fails validation."""


class GeminiAPIError(GeminiServiceError):
    """Raised for Gemini API, network, timeout, and response failures."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(magnitude) or magnitude == 0:
        raise GeminiValidationError(
            "Gemini returned an embedding that cannot be normalized."
        )
    return [value / magnitude for value in vector]


class GeminiService:
    """Synchronous Gemini REST client, independent from application storage.

    The API key is sent only in the ``x-goog-api-key`` request header. It is
    never included in exceptions or logs. An ``httpx.Client`` may be injected
    to support deterministic tests and caller-managed connections.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        timeout: float = 30.0,
        embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        # The application entrypoints already load backend/.env. This also
        # makes the module usable on its own without exposing file contents.
        load_dotenv(
            os.path.join(os.path.dirname(__file__), "..", ".env"),
            override=False,
        )
        key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured."
            )
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise GeminiConfigurationError("timeout must be a positive number.")
        if timeout <= 0 or not math.isfinite(float(timeout)):
            raise GeminiConfigurationError("timeout must be a positive number.")
        if (
            type(embedding_dimensions) is not int
            or not 1 <= embedding_dimensions <= MAX_EMBEDDING_DIMENSIONS
        ):
            raise GeminiConfigurationError(
                f"embedding_dimensions must be between 1 and {MAX_EMBEDDING_DIMENSIONS}."
            )

        self._api_key = key.strip()
        self.generation_model = self._validate_model(
            generation_model or os.getenv("GEMINI_GENERATION_MODEL")
            or DEFAULT_GENERATION_MODEL
        )
        self.embedding_model = self._validate_model(
            embedding_model or os.getenv("GEMINI_EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        self.embedding_dimensions = embedding_dimensions
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise GeminiConfigurationError("base_url must use HTTPS.")
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=float(timeout))

    @staticmethod
    def _validate_model(model: str) -> str:
        if not isinstance(model, str) or not model.strip():
            raise GeminiConfigurationError("Model name must not be empty.")
        model = model.strip()
        if "/" in model or "?" in model or "#" in model:
            raise GeminiConfigurationError("Invalid model name.")
        return model

    @staticmethod
    def _validate_text(text: str, field: str = "text") -> str:
        if not isinstance(text, str) or not text.strip():
            raise GeminiValidationError(f"{field} must be a non-empty string.")
        return text.strip()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "GeminiService":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _post(self, model: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/models/{model}:{method}"
        try:
            response = self.client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise GeminiAPIError("Gemini request timed out.") from exc
        except httpx.RequestError as exc:
            raise GeminiAPIError(
                f"Gemini request failed ({type(exc).__name__})."
            ) from exc

        if response.status_code >= 400:
            raise GeminiAPIError(
                f"Gemini API returned HTTP {response.status_code}.",
                status_code=response.status_code,
            )
        try:
            result = response.json()
        except (ValueError, httpx.DecodingError) as exc:
            raise GeminiAPIError("Gemini returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise GeminiAPIError("Gemini returned an invalid response shape.")
        return result

    def generate_text(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_instruction: str | None = None,
    ) -> str:
        """Generate text from one user prompt and return concatenated text parts."""
        prompt = self._validate_text(prompt, "prompt")
        selected_model = self._validate_model(model or self.generation_model)
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]
        }
        if system_instruction is not None:
            system_instruction = self._validate_text(
                system_instruction, "system_instruction"
            )
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        result = self._post(
            selected_model,
            "generateContent",
            payload,
        )
        try:
            candidates = result["candidates"]
            parts = candidates[0]["content"]["parts"]
            generated = "".join(
                part["text"] for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise GeminiAPIError("Gemini response contained no generated text.") from exc
        if not generated.strip():
            raise GeminiAPIError("Gemini response contained no generated text.")
        return generated

    def embed_text(
        self,
        text: str,
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
        output_dimensionality: int | None = None,
        model: str | None = None,
    ) -> list[float]:
        """Embed text and return a finite, L2-normalized vector."""
        text = self._validate_text(text)
        if not isinstance(task_type, str) or task_type not in SUPPORTED_TASK_TYPES:
            raise GeminiValidationError(
                "task_type must be RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY."
            )
        dimensions = (
            self.embedding_dimensions
            if output_dimensionality is None
            else output_dimensionality
        )
        if type(dimensions) is not int or not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
            raise GeminiValidationError(
                f"output_dimensionality must be between 1 and {MAX_EMBEDDING_DIMENSIONS}."
            )
        selected_model = self._validate_model(model or self.embedding_model)
        result = self._post(
            selected_model,
            "embedContent",
            {
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "output_dimensionality": dimensions,
            },
        )
        try:
            raw_values = result["embedding"]["values"]
        except (KeyError, TypeError) as exc:
            raise GeminiAPIError("Gemini response contained no embedding.") from exc
        if not isinstance(raw_values, list) or len(raw_values) != dimensions:
            actual = len(raw_values) if isinstance(raw_values, list) else "invalid"
            raise GeminiValidationError(
                f"Gemini returned {actual} dimensions; expected {dimensions}."
            )
        vector: list[float] = []
        for value in raw_values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GeminiValidationError("Gemini returned a non-numeric embedding value.")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise GeminiValidationError("Gemini returned a non-finite embedding value.")
            vector.append(numeric)
        return _normalize(vector)
