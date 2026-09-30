"""Direct HTTPX access to the documented ChatGPT account model and Responses APIs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx

from .errors import ErrorCategory, ResponsesError
from .models import ModelInfo, ResponseSummary, UsageSummary

API_BASE_URL = "https://api.openai.com/v1"
MODEL_LIST_URL = f"{API_BASE_URL}/models"
RESPONSES_URL = f"{API_BASE_URL}/responses"
RATE_LIMIT_HEADERS = frozenset(
    {
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
    }
)
SAFE_HEADER_VALUE = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:ms|s|m|h)?$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ResponsesClient:
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.http = http_client or httpx.Client(
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )

    def list_models(self, access_token: str) -> list[ModelInfo]:
        try:
            response = self.http.get(
                MODEL_LIST_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TimeoutException:
            raise ResponsesError("Model discovery timed out.", category="timeout") from None
        except httpx.HTTPError:
            raise ResponsesError(
                "Model discovery failed to reach OpenAI.", category="transport"
            ) from None

        if response.is_error:
            raise ResponsesError(
                "Model discovery was rejected by OpenAI.",
                category=self._http_error_category(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            raise ResponsesError("OpenAI returned an invalid model catalog.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ResponsesError("OpenAI returned an invalid model catalog.")

        models: list[ModelInfo] = []
        for model in payload["models"]:
            if not isinstance(model, dict) or model.get("visibility") != "list":
                continue
            slug = model.get("slug")
            display_name = model.get("display_name")
            if isinstance(slug, str) and slug and isinstance(display_name, str) and display_name:
                models.append(ModelInfo(slug=slug, display_name=display_name))
        return models

    def stream_response(
        self,
        access_token: str,
        model: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
    ) -> ResponseSummary:
        if not model:
            raise ResponsesError("Select a model returned by account discovery.", category="model")
        body: dict[str, object] = {
            "model": model,
            "input": input_items,
            "store": False,
            "stream": True,
        }
        if tools is not None:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {access_token}"}
        event_types: list[str] = []
        text_parts: list[str] = []
        streamed_output_items: dict[int, dict[str, Any]] = {}
        streamed_function_call_item_done_count = 0
        completed: dict[str, Any] | None = None

        try:
            with self.http.stream("POST", RESPONSES_URL, headers=headers, json=body) as response:
                if response.is_error:
                    raise ResponsesError(
                        "Responses request was rejected by OpenAI.",
                        category=self._response_error_category(response.status_code),
                    )
                request_id = self._safe_request_id(response.headers.get("x-request-id"))
                rate_limit_metadata = self._safe_rate_limit_metadata(response.headers)
                for event_data in self._iter_sse_data(response.iter_lines()):
                    event = self._parse_event(event_data)
                    event_type = event.get("type")
                    if not isinstance(event_type, str):
                        raise ResponsesError("OpenAI sent an event without a type.")
                    event_types.append(event_type)
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise ResponsesError("OpenAI sent an invalid text delta.")
                        text_parts.append(delta)
                    elif event_type == "response.output_item.done":
                        output_index = event.get("output_index")
                        item = event.get("item")
                        if type(output_index) is not int or not isinstance(item, dict):
                            raise ResponsesError("OpenAI sent an invalid completed output item.")
                        streamed_output_items[output_index] = item
                        if item.get("type") == "function_call":
                            streamed_function_call_item_done_count += 1
                    elif event_type == "response.failed":
                        raise ResponsesError(
                            "The Responses request failed.",
                            category=self._event_error_category(event),
                        )
                    elif event_type == "response.incomplete":
                        raise ResponsesError("The Responses request ended incomplete.")
                    elif event_type == "response.completed":
                        completed = event.get("response")
                        if not isinstance(completed, dict):
                            raise ResponsesError("OpenAI sent an invalid completed response.")
                        if completed.get("status", "completed") != "completed":
                            raise ResponsesError("The Responses request did not complete.")
                        break
                if completed is None:
                    raise ResponsesError(
                        "The stream ended before response.completed.", category="transport"
                    )
        except ResponsesError as error:
            error.observed_event_types = tuple(event_types)
            raise
        except httpx.TimeoutException:
            raise ResponsesError(
                "The Responses stream timed out.",
                category="timeout",
                observed_event_types=tuple(event_types),
            ) from None
        except httpx.HTTPError:
            raise ResponsesError(
                "The Responses stream was interrupted.",
                category="transport",
                observed_event_types=tuple(event_types),
            ) from None
        except (UnicodeDecodeError, ValueError):
            raise ResponsesError(
                "OpenAI sent a malformed event stream.", observed_event_types=tuple(event_types)
            ) from None

        output_items = completed.get("output", [])
        if not isinstance(output_items, list) or any(
            not isinstance(item, dict) for item in output_items
        ):
            raise ResponsesError("OpenAI sent invalid output items.")
        merged_output_items = dict(enumerate(output_items))
        # Final stream items remain authoritative when the preview body differs.
        for output_index, item in streamed_output_items.items():
            merged_output_items[output_index] = item
        output_items = [merged_output_items[index] for index in sorted(merged_output_items)]
        usage_data = completed.get("usage")
        if not isinstance(usage_data, dict):
            usage_data = {}
        response_id = completed.get("id")
        response_model = completed.get("model")
        return ResponseSummary(
            response_id=response_id if isinstance(response_id, str) else None,
            model=response_model if isinstance(response_model, str) else None,
            text="".join(text_parts),
            output_items=tuple(output_items),
            usage=UsageSummary(
                input_tokens=self._usage_count(usage_data.get("input_tokens")),
                output_tokens=self._usage_count(usage_data.get("output_tokens")),
                total_tokens=self._usage_count(usage_data.get("total_tokens")),
            ),
            event_types=tuple(event_types),
            request_id=request_id,
            rate_limit_metadata=rate_limit_metadata,
            streamed_function_call_item_done_count=streamed_function_call_item_done_count,
        )

    @staticmethod
    def _iter_sse_data(lines: Iterator[str]) -> Iterator[str]:
        data_lines: list[str] = []
        for line in lines:
            if line == "":
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines.clear()
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if field == "data":
                data_lines.append(value[1:] if separator and value.startswith(" ") else value)
        if data_lines:
            yield "\n".join(data_lines)

    @staticmethod
    def _parse_event(event_data: str) -> dict[str, Any]:
        try:
            event = json.loads(event_data)
        except (TypeError, ValueError):
            raise ResponsesError("OpenAI sent a malformed event.") from None
        if not isinstance(event, dict):
            raise ResponsesError("OpenAI sent a malformed event.")
        return event

    @staticmethod
    def _event_error_category(event: dict[str, Any]) -> ErrorCategory:
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str):
            normalized = code.lower()
            if "usage_limit" in normalized or "rate_limit" in normalized:
                return "rate_limit"
            if "model" in normalized:
                return "model"
            if "auth" in normalized or "token" in normalized:
                return "authentication"
        return "unknown"

    @staticmethod
    def _http_error_category(status_code: int) -> ErrorCategory:
        if status_code in (401, 403):
            return "authentication"
        if status_code == 429:
            return "rate_limit"
        if status_code in (400, 404, 422):
            return "model"
        return "transport"

    @classmethod
    def _response_error_category(cls, status_code: int) -> ErrorCategory:
        return cls._http_error_category(status_code)

    @staticmethod
    def _usage_count(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    @staticmethod
    def _safe_request_id(value: str | None) -> str | None:
        return value if value is not None and SAFE_IDENTIFIER.fullmatch(value) else None

    @staticmethod
    def _safe_rate_limit_metadata(headers: httpx.Headers) -> dict[str, str]:
        return {
            name: value
            for name in RATE_LIMIT_HEADERS
            if (value := headers.get(name)) is not None and SAFE_HEADER_VALUE.fullmatch(value)
        }
