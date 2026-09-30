"""The only local capabilities exposed to the model in this spike."""

from __future__ import annotations

import json
from collections.abc import Callable

from .errors import ErrorCategory, SpikeError


class ToolDispatchError(SpikeError):
    def __init__(self, message: str, *, category: ErrorCategory = "tool_validation") -> None:
        super().__init__(message)
        self.category = category


def research_context_get() -> dict[str, str]:
    return {
        "research_question": "Can a minimal Responses harness safely call local research tools?",
        "status": "SPIKE_CONTEXT_OK",
    }


def research_budget_get() -> dict[str, int]:
    return {"remaining_iterations": 3}


TOOLS: dict[str, Callable[[], dict[str, str] | dict[str, int]]] = {
    "research_context_get": research_context_get,
    "research_budget_get": research_budget_get,
}

TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "namespace",
        "name": "research",
        "description": "Read the local mock research context and remaining iteration budget.",
        "tools": [
            {
                "type": "function",
                "name": "research_context_get",
                "description": "Return the fixed mock research question and status.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "research_budget_get",
                "description": "Return the fixed mock remaining iteration budget.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ],
    }
]


def dispatch_tool(name: str, arguments_json: str) -> str:
    function = TOOLS.get(name)
    if function is None:
        raise ToolDispatchError("The requested local tool is not allowlisted.")
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        raise ToolDispatchError("The local tool arguments are not valid JSON.") from None
    if not isinstance(arguments, dict) or arguments:
        raise ToolDispatchError("The local tool accepts only an empty JSON object.")
    try:
        result = function()
    except Exception:
        raise ToolDispatchError("The local tool failed.", category="tool_execution") from None
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
