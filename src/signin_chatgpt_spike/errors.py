"""Safe error categories shared by the small spike runtime."""

from __future__ import annotations

from typing import Literal

ErrorCategory = Literal[
    "authentication",
    "transport",
    "rate_limit",
    "model",
    "tool_validation",
    "tool_execution",
    "timeout",
    "unknown",
]


class SpikeError(RuntimeError):
    """An error with a safe category and message, never an upstream payload."""

    category: ErrorCategory = "unknown"


class ResponsesError(SpikeError):
    """A safe failure from model discovery or a streamed Responses request."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = "unknown",
        observed_event_types: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.category = category
        self.observed_event_types = tuple(
            event_type for event_type in observed_event_types if isinstance(event_type, str)
        )
