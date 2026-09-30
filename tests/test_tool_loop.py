from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any

import pytest

EXAMPLE_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"
EXAMPLE_MODEL = "gpt-6-luna"


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def function_call(call_id: str, name: str, arguments: str = "{}") -> dict[str, object]:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def response_summary(
    text: str = "",
    *output_items: dict[str, object],
) -> Any:
    models = load_module("models")
    return models.ResponseSummary(
        response_id="resp_EXAMPLE_ONLY_NOT_A_REAL_ID",
        model=EXAMPLE_MODEL,
        text=text,
        output_items=tuple(output_items),
        usage=models.UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2),
        event_types=("response.output_item.done", "response.completed"),
        request_id=None,
        rate_limit_metadata={},
        streamed_function_call_item_done_count=sum(
            item.get("type") == "function_call" for item in output_items
        ),
    )


@dataclass
class FakeResponsesClient:
    responses: list[Any]
    requests: list[dict[str, object]]

    def stream_response(
        self,
        access_token: str,
        model: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
    ) -> Any:
        self.requests.append(
            {
                "access_token": access_token,
                "model": model,
                "input": list(input_items),
                "tools": tools,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra response request")
        return self.responses.pop(0)


def test_tool_loop_executes_one_call_then_continues_to_final_text() -> None:
    tool_loop = load_module("tool_loop")
    client = FakeResponsesClient(
        responses=[
            response_summary("", function_call("call_context", "research_context_get")),
            response_summary("The research context is ready."),
        ],
        requests=[],
    )

    result = tool_loop.run_tool_loop(
        client,
        EXAMPLE_TOKEN,
        EXAMPLE_MODEL,
        "Read the research context.",
    )

    assert result.final_text == "The research context is ready."
    assert result.tool_call_order == ("research_context_get",)
    assert len(client.requests) == 2
    second_input = client.requests[1]["input"]
    assert second_input[0] == {"role": "user", "content": "Read the research context."}
    assert second_input[1] == function_call("call_context", "research_context_get")
    assert second_input[2] == {
        "type": "function_call_output",
        "call_id": "call_context",
        "output": (
            '{"research_question":"Can a minimal Responses harness safely call local research '
            'tools?","status":"SPIKE_CONTEXT_OK"}'
        ),
    }


def test_multi_tool_loop_keeps_history_and_executes_context_before_budget() -> None:
    tool_loop = load_module("tool_loop")
    trace = tool_loop.ToolLoopTrace()
    client = FakeResponsesClient(
        responses=[
            response_summary("", function_call("call_context", "research_context_get")),
            response_summary("", function_call("call_budget", "research_budget_get")),
            response_summary("Context and budget checked."),
        ],
        requests=[],
    )

    result = tool_loop.run_tool_loop(
        client,
        EXAMPLE_TOKEN,
        EXAMPLE_MODEL,
        "Check the context and remaining budget.",
        trace=trace,
    )

    assert result.tool_call_order == ("research_context_get", "research_budget_get")
    assert result.final_text == "Context and budget checked."
    assert len(client.requests) == 3
    second_input = client.requests[1]["input"]
    assert second_input[-1]["type"] == "function_call_output"
    assert second_input[-1]["call_id"] == "call_context"
    third_input = client.requests[2]["input"]
    assert third_input[-2] == function_call("call_budget", "research_budget_get")
    assert third_input[-1] == {
        "type": "function_call_output",
        "call_id": "call_budget",
        "output": '{"remaining_iterations":3}',
    }
    assert [item.get("type", item.get("role")) for item in third_input] == [
        "user",
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
    ]
    assert trace.request_count == 3
    assert trace.observed_tool_call_order == ["research_context_get", "research_budget_get"]
    assert trace.tool_call_order == ["research_context_get", "research_budget_get"]
    assert trace.tool_arguments_exactly_empty_object == [True, True]
    assert trace.tool_execution_counts == {"research_context_get": 1, "research_budget_get": 1}
    assert trace.duplicate_tool_call_count == 0
    assert trace.function_output_continuations == [True, True]
    assert trace.responses == list(result.responses)
    assert trace.phase == "complete"


@pytest.mark.parametrize(
    "duplicate_call",
    [
        function_call("reused_call", "research_context_get"),
        function_call("reused_call", "research_context_get", '{"unexpected":true}'),
    ],
)
def test_duplicate_call_id_never_executes_the_local_tool_twice(
    duplicate_call: dict[str, object],
) -> None:
    tool_loop = load_module("tool_loop")
    tools = load_module("tools")
    trace = tool_loop.ToolLoopTrace()
    calls: list[str] = []
    original = tools.TOOLS["research_context_get"]

    def counted() -> dict[str, object]:
        calls.append("context")
        return original()

    tools.TOOLS["research_context_get"] = counted
    client = FakeResponsesClient(
        responses=[
            response_summary("", function_call("reused_call", "research_context_get")),
            response_summary("", duplicate_call),
        ],
        requests=[],
    )

    try:
        with pytest.raises(tool_loop.ToolLoopError) as raised:
            tool_loop.run_tool_loop(
                client, EXAMPLE_TOKEN, EXAMPLE_MODEL, "Read context.", trace=trace
            )
    finally:
        tools.TOOLS["research_context_get"] = original

    assert raised.value.category == "tool_validation"
    assert calls == ["context"]
    assert trace.tool_execution_counts == {"research_context_get": 1, "research_budget_get": 0}
    assert trace.duplicate_tool_call_count == 1
    assert trace.function_output_continuations == [True]


def test_unknown_function_call_stops_before_tool_execution() -> None:
    tool_loop = load_module("tool_loop")
    client = FakeResponsesClient(
        responses=[response_summary("", function_call("call_unknown", "shell_execute"))],
        requests=[],
    )

    with pytest.raises(tool_loop.ToolLoopError) as raised:
        tool_loop.run_tool_loop(client, EXAMPLE_TOKEN, EXAMPLE_MODEL, "Run a shell command.")

    assert raised.value.category == "tool_validation"
    assert len(client.requests) == 1


def test_tool_loop_trace_keeps_event_names_observed_before_a_stream_failure() -> None:
    tool_loop = load_module("tool_loop")
    errors = load_module("errors")
    trace = tool_loop.ToolLoopTrace()

    class FailingResponsesClient:
        def stream_response(self, access_token, model, input_items, tools=None):
            raise errors.ResponsesError(
                "The stream failed safely.",
                category="transport",
                observed_event_types=("response.created", "response.output_item.added"),
            )

    with pytest.raises(errors.ResponsesError):
        tool_loop.run_tool_loop(
            FailingResponsesClient(), EXAMPLE_TOKEN, EXAMPLE_MODEL, "Read context.", trace=trace
        )

    assert trace.request_count == 1
    assert trace.requested_models == [EXAMPLE_MODEL]
    assert trace.partial_event_types == ["response.created", "response.output_item.added"]


@pytest.mark.parametrize(
    "item",
    [
        {"type": "file_search_call", "id": "fs_EXAMPLE"},
        {"type": "function_call", "call_id": "", "name": "research_context_get", "arguments": "{}"},
        function_call("call_bad_arguments", "research_context_get", "[]"),
    ],
)
def test_malformed_or_unsupported_output_items_stop_loop(item: dict[str, object]) -> None:
    tool_loop = load_module("tool_loop")
    client = FakeResponsesClient(responses=[response_summary("", item)], requests=[])

    with pytest.raises(tool_loop.ToolLoopError):
        tool_loop.run_tool_loop(client, EXAMPLE_TOKEN, EXAMPLE_MODEL, "Inspect output.")
