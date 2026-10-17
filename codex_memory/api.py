"""Small dependency-free client for OpenAI-compatible model endpoints."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .redaction import redact_text


@dataclass(frozen=True)
class APIConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5.5"
    api_mode: str = "responses"
    timeout_seconds: int = 180
    max_retries: int = 1

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key is required")
        if self.api_mode not in {"responses", "chat_completions"}:
            raise ValueError("api_mode must be responses or chat_completions")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")


class ModelAPIError(RuntimeError):
    pass


class ModelClient:
    def __init__(self, config: APIConfig):
        self.config = config

    def create_structured(
        self,
        *,
        developer_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        if self.config.api_mode == "responses":
            path = "/responses"
            payload = {
                "model": self.config.model,
                "input": [
                    {"role": "developer", "content": developer_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
                "store": False,
            }
        else:
            path = "/chat/completions"
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "developer", "content": developer_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
                },
            }
        response = self._post(path, payload)
        text = self._extract_text(response)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelAPIError(f"model returned invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ModelAPIError("model output must be a JSON object")
        return value

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "codex-memory-reproduction/0.1.0",
            },
        )
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    decoded = response.read().decode("utf-8")
                value = json.loads(decoded)
                if not isinstance(value, dict):
                    raise ModelAPIError("API response must be a JSON object")
                return value
            except urllib.error.HTTPError as exc:
                body = exc.read(8192).decode("utf-8", errors="replace")
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if retryable and attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise ModelAPIError(
                    f"HTTP {exc.code} from model API: {redact_text(body)[:1000]}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise ModelAPIError(f"model API connection failed: {redact_text(str(exc))}") from exc
            except json.JSONDecodeError as exc:
                raise ModelAPIError(f"API returned invalid JSON: {exc}") from exc
        raise AssertionError("unreachable")

    def _extract_text(self, response: dict[str, Any]) -> str:
        if self.config.api_mode == "chat_completions":
            try:
                content = response["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ModelAPIError("chat completion response has no message content") from exc
            if not isinstance(content, str):
                raise ModelAPIError("chat completion content is not text")
            return content

        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        texts: list[str] = []
        for item in response.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        texts.append(text)
                elif isinstance(content, dict) and content.get("type") == "refusal":
                    raise ModelAPIError(f"model refused request: {content.get('refusal', '')}")
        if not texts:
            status = response.get("status")
            error = response.get("error")
            raise ModelAPIError(f"Responses API returned no output text; status={status}, error={error}")
        return "".join(texts)

