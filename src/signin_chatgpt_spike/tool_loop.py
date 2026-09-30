"""A short client-side continuation loop for the two explicit research tools."""

from __future__ import annotations

from dataclasses import dataclass
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


def run_tool_loop(
    client: ResponsesClientProtocol,
    access_token: str,
    model: str,
    prompt: str,
    max_steps: int = 6,
) -> ToolLoopResult:
    if max_steps < 1:
        raise ToolLoopError("The tool loop step limit must be positive.")

    input_items: list[dict[str, object]] = [{"role": "user", "content": prompt}]
    tool_call_order: list[str] = []
    seen_call_ids: dict[str, tuple[str, str]] = {}
    responses: list[ResponseSummary] = []

    for response_count in range(1, max_steps + 1):
        response = client.stream_response(
            access_token,
            model,
            input_items,
            tools=TOOL_DEFINITIONS,
        )
        responses.append(response)
        function_calls: list[dict[str, object]] = []
        for item in response.output_items:
            item_type = item.get("type")
            if item_type not in {"message", "reasoning", "function_call"}:
                raise ToolLoopError("The model returned an unsupported output item.")
            input_items.append(dict(item))
            if item_type == "function_call":
                function_calls.append(item)

        if not function_calls:
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
                raise ToolLoopError("The model returned an invalid function call.")
            fingerprint = (name, arguments)
            previous_call = seen_call_ids.get(call_id)
            if previous_call is not None:
                # A repeated ID could duplicate effects or make a forged call look fulfilled.
                message = (
                    "The model reused a function call ID with different contents."
                    if previous_call != fingerprint
                    else "The model repeated a function call ID."
                )
                raise ToolLoopError(message)
            seen_call_ids[call_id] = fingerprint
            try:
                output = dispatch_tool(name, arguments)
            except ToolDispatchError as error:
                raise ToolLoopError(str(error), category=error.category) from None
            input_items.append(
                {"type": "function_call_output", "call_id": call_id, "output": output}
            )
            tool_call_order.append(name)

    raise ToolLoopError(
        "The model exceeded the local tool loop step limit.", category="tool_execution"
    )
