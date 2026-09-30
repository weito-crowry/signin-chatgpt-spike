"""A short client-side continuation loop for the two explicit research tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .errors import ErrorCategory, SpikeError
from .models import ResponseSummary
from .tools import TOOL_DEFINITIONS, ToolDispatchError, dispatch_tool


class ResponsesClientProtocol(Protocol):
    def stream_response(
        self,
        access_token: str,
        model: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
    ) -> ResponseSummary: ...


class ToolLoopError(SpikeError):
    def __init__(self, message: str, *, category: ErrorCategory = "tool_validation") -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ToolLoopResult:
    final_text: str
    tool_call_order: tuple[str, ...]
    response_count: int
    final_response: ResponseSummary
    responses: tuple[ResponseSummary, ...]


@dataclass
class ToolLoopTrace:
    """Safe execution facts collected for a single ordered tool-loop run."""

    request_count: int = 0
    requested_models: list[str] = field(default_factory=list)
    responses: list[ResponseSummary] = field(default_factory=list)
    partial_event_types: list[str] = field(default_factory=list)
    observed_tool_call_order: list[str] = field(default_factory=list)
    tool_call_order: list[str] = field(default_factory=list)
    tool_arguments_exactly_empty_object: list[bool] = field(default_factory=list)
    tool_execution_counts: dict[str, int] = field(
        default_factory=lambda: {"research_context_get": 0, "research_budget_get": 0}
    )
    duplicate_tool_call_count: int = 0
    duplicate_tool_execution_count: int = 0
    function_output_continuations: list[bool] = field(default_factory=list)
    phase: str = "not_started"


def run_tool_loop(
    client: ResponsesClientProtocol,
    access_token: str,
    model: str,
    prompt: str,
    max_steps: int = 6,
    *,
    trace: ToolLoopTrace | None = None,
) -> ToolLoopResult:
    if max_steps < 1:
        raise ToolLoopError("The tool loop step limit must be positive.")

    input_items: list[dict[str, object]] = [{"role": "user", "content": prompt}]
    tool_call_order: list[str] = []
    seen_call_ids: dict[str, tuple[str, str]] = {}
    responses: list[ResponseSummary] = []

    for response_count in range(1, max_steps + 1):
        if trace is not None:
            trace.phase = "responses_request"
            trace.request_count += 1
            trace.requested_models.append(model)
        try:
            response = client.stream_response(
                access_token,
                model,
                input_items,
                tools=TOOL_DEFINITIONS,
            )
        except Exception as error:
            if trace is not None:
                observed = getattr(error, "observed_event_types", ())
                if isinstance(observed, (tuple, list)):
                    trace.partial_event_types.extend(
                        event_type for event_type in observed if isinstance(event_type, str)
                    )
            raise
        responses.append(response)
        if trace is not None:
            for index, continued in enumerate(trace.function_output_continuations):
                if not continued:
                    trace.function_output_continuations[index] = True
            trace.responses.append(response)
            trace.phase = "output_item_processing"
        function_calls: list[dict[str, object]] = []
        for item in response.output_items:
            item_type = item.get("type")
            if item_type not in {"message", "reasoning", "function_call"}:
                raise ToolLoopError("The model returned an unsupported output item.")
            input_items.append(dict(item))
            if item_type == "function_call":
                function_calls.append(item)

        if not function_calls:
            if trace is not None:
                trace.phase = "complete"
            return ToolLoopResult(
                final_text=response.text,
                tool_call_order=tuple(tool_call_order),
                response_count=response_count,
                final_response=response,
                responses=tuple(responses),
            )

        for item in function_calls:
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if not isinstance(call_id, str) or not call_id:
                raise ToolLoopError("The model returned a tool call without an ID.")
            if not isinstance(name, str) or not isinstance(arguments, str):
                if trace is not None:
                    trace.phase = "tool_validation"
                raise ToolLoopError("The model returned an invalid function call.")
            if trace is not None:
                trace.observed_tool_call_order.append(name)
                trace.tool_arguments_exactly_empty_object.append(arguments == "{}")
            fingerprint = (name, arguments)
            previous_call = seen_call_ids.get(call_id)
            if previous_call is not None:
                if trace is not None:
                    trace.duplicate_tool_call_count += 1
                    trace.phase = "tool_validation"
                # A repeated ID could duplicate effects or make a forged call look fulfilled.
                message = (
                    "The model reused a function call ID with different contents."
                    if previous_call != fingerprint
                    else "The model repeated a function call ID."
                )
                raise ToolLoopError(message)
            seen_call_ids[call_id] = fingerprint
            try:
                if trace is not None:
                    trace.phase = "local_tool_dispatch"
                output = dispatch_tool(name, arguments)
            except ToolDispatchError as error:
                if trace is not None:
                    trace.phase = "tool_validation"
                raise ToolLoopError(str(error), category=error.category) from None
            if trace is not None:
                if name in trace.tool_execution_counts:
                    trace.tool_execution_counts[name] += 1
                trace.tool_call_order.append(name)
                trace.function_output_continuations.append(False)
                trace.phase = "function_output_append"
            input_items.append(
                {"type": "function_call_output", "call_id": call_id, "output": output}
            )
            tool_call_order.append(name)

    raise ToolLoopError(
        "The model exceeded the local tool loop step limit.", category="tool_execution"
    )
