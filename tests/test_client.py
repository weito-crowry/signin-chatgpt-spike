from __future__ import annotations

import importlib
import importlib.util
import json

import httpx
import pytest

EXAMPLE_ACCESS_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"
EXAMPLE_MODEL = "gpt-6-luna"
EXAMPLE_RESPONSE_ID = "resp_EXAMPLE_ONLY_NOT_A_REAL_ID"
UNSUPPORTED_FIELDS = {
    "background",
    "conversation",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "moderation",
    "multi_agent",
    "prompt",
    "prompt_cache_retention",
    "previous_response_id",
    "safety_identifier",
    "temperature",
    "top_logprobs",
    "top_p",
    "truncation",
    "user",
}


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def sse(*events: dict[str, object]) -> bytes:
    return b"".join(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events)


def completed_response(*, output: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "id": EXAMPLE_RESPONSE_ID,
        "model": EXAMPLE_MODEL,
        "status": "completed",
        "output": output or [],
        "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
    }


def test_lists_only_account_models_marked_for_display() -> None:
    client_module = load_module("client")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-6-luna", "display_name": "GPT-6 Luna", "visibility": "list"},
                    {"slug": "hidden-model", "display_name": "Hidden", "visibility": "internal"},
                    {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
                ]
            },
        )

    client = client_module.ResponsesClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    models = client.list_models(EXAMPLE_ACCESS_TOKEN)

    assert [(item.slug, item.display_name) for item in models] == [
        ("gpt-6-luna", "GPT-6 Luna"),
        ("gpt-6.1-sol", "GPT-6.1 Sol"),
    ]
    assert seen[0].url == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {EXAMPLE_ACCESS_TOKEN}"


def test_response_request_uses_only_preview_supported_fields() -> None:
    client_module = load_module("client")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_EXAMPLE_ONLY_NOT_A_REAL_ID"},
            content=sse(
                {"type": "response.created", "response": {"id": EXAMPLE_RESPONSE_ID}},
                {"type": "response.output_text.delta", "delta": "SIGNIN_"},
                {"type": "response.output_text.delta", "delta": "CHATGPT_SPIKE_OK"},
                {"type": "response.completed", "response": completed_response()},
            ),
        )

    client = client_module.ResponsesClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    input_items = [{"role": "user", "content": "Return exactly the smoke marker."}]
    tools = [
        {
            "type": "namespace",
            "name": "research",
            "description": "Read local research context.",
            "tools": [
                {
                    "type": "function",
                    "name": "research_context_get",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }
            ],
        }
    ]

    result = client.stream_response(
        EXAMPLE_ACCESS_TOKEN,
        EXAMPLE_MODEL,
        input_items,
        tools=tools,
    )

    body = captured["body"]
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["authorization"] == f"Bearer {EXAMPLE_ACCESS_TOKEN}"
    assert isinstance(body, dict)
    assert body["store"] is False
    assert body["stream"] is True
    assert body["model"] == EXAMPLE_MODEL
    assert body["input"] == input_items
    assert body["tools"] == tools
    assert not (UNSUPPORTED_FIELDS & body.keys())
    assert result.text == "SIGNIN_CHATGPT_SPIKE_OK"
    assert result.response_id == EXAMPLE_RESPONSE_ID
    assert result.model == EXAMPLE_MODEL
    assert result.usage.total_tokens == 16
    assert result.event_types == (
        "response.created",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.completed",
    )
    assert result.request_id == "req_EXAMPLE_ONLY_NOT_A_REAL_ID"


@pytest.mark.parametrize(
    "completed_output",
    [[], [{"type": "reasoning", "summary": []}]],
)
def test_streamed_function_call_item_is_preserved_from_final_stream_event(
    completed_output: list[dict[str, object]],
) -> None:
    client_module = load_module("client")
    function_call = {
        "type": "function_call",
        "id": "fc_EXAMPLE_ONLY_NOT_A_REAL_ID",
        "call_id": "call_EXAMPLE_ONLY_NOT_A_REAL_ID",
        "name": "research_context_get",
        "arguments": "{}",
    }
    client = client_module.ResponsesClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=sse(
                        {
                            "type": "response.output_item.added",
                            "output_index": 0,
                            "item": {**function_call, "arguments": ""},
                        },
                        {
                            "type": "response.function_call_arguments.done",
                            "output_index": 0,
                            "arguments": "{}",
                        },
                        {
                            "type": "response.output_item.done",
                            "output_index": 0,
                            "item": function_call,
                        },
                        {
                            "type": "response.completed",
                            "response": completed_response(output=completed_output),
                        },
                    ),
                )
            )
        )
    )

    result = client.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [], tools=[])

    assert result.output_items == (function_call,)
    assert result.streamed_function_call_item_done_count == 1


@pytest.mark.parametrize(
    ("status_code", "error_body", "category"),
    [
        (401, {"error": {"message": "EXAMPLE_ONLY_NOT_A_REAL_SECRET"}}, "authentication"),
        (429, {"error": {"code": "rate_limit_exceeded"}}, "rate_limit"),
        (400, {"error": {"code": "model_not_found"}}, "model"),
    ],
)
def test_http_errors_are_classified_without_upstream_body(
    status_code: int, error_body: dict[str, object], category: str
) -> None:
    client_module = load_module("client")

    client = client_module.ResponsesClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, json=error_body)
            )
        )
    )

    with pytest.raises(client_module.ResponsesError) as raised:
        client.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [])

    assert raised.value.category == category
    assert "EXAMPLE_ONLY_NOT_A_REAL_SECRET" not in str(raised.value)


def test_failed_and_incomplete_events_never_report_success() -> None:
    client_module = load_module("client")

    for event, category in (
        (
            {
                "type": "response.failed",
                "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
            },
            "rate_limit",
        ),
        (
            {
                "type": "response.incomplete",
                "response": {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            },
            "unknown",
        ),
    ):
        client = client_module.ResponsesClient(
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, event=event: httpx.Response(
                        200,
                        content=sse(
                            {"type": "response.created", "response": {"id": EXAMPLE_RESPONSE_ID}},
                            event,
                        ),
                    )
                )
            )
        )
        with pytest.raises(client_module.ResponsesError) as raised:
            client.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [])
        assert raised.value.category == category
        if event["type"] == "response.failed":
            assert raised.value.observed_event_types == (
                "response.created",
                "response.failed",
            )


def test_malformed_or_interrupted_sse_is_not_returned_as_success() -> None:
    client_module = load_module("client")

    for content in (
        b"data: {not-json}\n\n",
        sse({"type": "response.created", "response": {"id": EXAMPLE_RESPONSE_ID}}),
    ):
        client = client_module.ResponsesClient(
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, content=content: httpx.Response(200, content=content)
                )
            )
        )
        with pytest.raises(client_module.ResponsesError):
            client.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [])


def test_timeout_and_invalid_model_have_explicit_categories() -> None:
    client_module = load_module("client")

    timed_out = client_module.ResponsesClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("EXAMPLE_SECRET"))
            )
        )
    )
    with pytest.raises(client_module.ResponsesError) as timeout_error:
        timed_out.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [])
    assert timeout_error.value.category == "timeout"
    assert "EXAMPLE_SECRET" not in str(timeout_error.value)

    client = client_module.ResponsesClient(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    )
    with pytest.raises(client_module.ResponsesError) as model_error:
        client.stream_response(EXAMPLE_ACCESS_TOKEN, "missing-model", [])
    assert model_error.value.category == "model"


def test_rate_limit_headers_are_reduced_to_known_safe_metadata() -> None:
    client_module = load_module("client")
    client = client_module.ResponsesClient(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={
                        "x-ratelimit-limit-requests": "100",
                        "x-ratelimit-remaining-requests": "99",
                        "x-secret-debug": "EXAMPLE_ONLY_NOT_A_REAL_SECRET",
                    },
                    content=sse({"type": "response.completed", "response": completed_response()}),
                )
            )
        )
    )

    result = client.stream_response(EXAMPLE_ACCESS_TOKEN, EXAMPLE_MODEL, [])

    assert result.rate_limit_metadata == {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "99",
    }
    assert "EXAMPLE_ONLY_NOT_A_REAL_SECRET" not in repr(result)
