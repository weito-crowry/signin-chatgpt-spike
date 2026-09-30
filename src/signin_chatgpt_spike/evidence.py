"""Build a public-safe smoke record from a deliberately narrow metadata type."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .client import RATE_LIMIT_HEADERS, SAFE_HEADER_VALUE

CAPABILITIES = [
    "Responses inference",
    "research_context_get",
    "research_budget_get",
]
ALLOWED_TOOL_NAMES = frozenset({"research_context_get", "research_budget_get"})
ALLOWED_EVENT_TYPES = frozenset(
    {
        "response.created",
        "response.in_progress",
        "response.output_text.delta",
        "response.output_text.done",
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.reasoning_summary_text.delta",
        "response.completed",
    }
)
SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SAFE_GIT_SHA = re.compile(r"^[a-fA-F0-9]{7,40}$")
SAFE_RESPONSE_ID = re.compile(r"^resp_[A-Za-z0-9_-]{1,120}$")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_PLATFORM = re.compile(r"^(?:Windows|Linux|Darwin)(?: [0-9.]{1,32})?$")
SAFE_PYTHON_VERSION = re.compile(r"^[0-9.]{1,32}$")


@dataclass(frozen=True)
class SmokeSummary:
    timestamp_utc: str
    operating_system: str
    python_version: str
    git_sha: str
    authenticated: bool
    model_discovery_success: bool
    available_models: tuple[str, ...]
    selected_model: str
    simple_response_success: bool
    single_tool_success: bool
    single_tool_call_order: tuple[str, ...]
    multi_tool_success: bool
    multi_tool_call_order: tuple[str, ...]
    response_ids: tuple[str, ...]
    request_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    usage_input_tokens: int | None
    usage_output_tokens: int | None
    usage_total_tokens: int | None
    rate_limit_metadata: dict[str, str]
    elapsed_seconds: float
    unknown_tool_denial: bool


@dataclass(frozen=True)
class ToolLoopEvidenceSummary:
    timestamp_utc: str
    git_sha_before_evidence_commit: str
    default_model: str
    verification_model: str
    model_catalog: tuple[str, ...]
    selected_model: str | None
    model_discovery_request_count: int
    responses_request_count: int
    responses_request_models: tuple[str, ...]
    response_ids: tuple[str, ...]
    request_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    response_output_item_done_function_call_counts: tuple[int, ...]
    observed_tool_call_order: tuple[str, ...]
    tool_call_order: tuple[str, ...]
    context_arguments_exactly_empty_object: tuple[bool, ...]
    budget_arguments_exactly_empty_object: tuple[bool, ...]
    context_execution_count: int
    budget_execution_count: int
    duplicate_tool_call_count: int
    duplicate_tool_execution_count: int
    function_output_continuations: tuple[bool, ...]
    final_response_present: bool
    usage_input_tokens: int | None
    usage_output_tokens: int | None
    usage_total_tokens: int | None
    elapsed_seconds: float
    authenticated: bool
    refresh_available: bool
    plan_usage_enabled: bool
    result: str
    failure_category: str
    failure_cause: str


def build_tool_loop_evidence(summary: ToolLoopEvidenceSummary) -> dict[str, object]:
    catalog = _safe_identifiers(summary.model_catalog, SAFE_MODEL_ID)
    selected_model = summary.selected_model
    if (
        not isinstance(selected_model, str)
        or selected_model not in catalog
        or selected_model not in {"gpt-6-luna", "gpt-5.6-luna"}
        or not _safe_model_id(selected_model)
    ):
        selected_model = None

    safe_result = (
        summary.result
        if summary.result
        in {
            "PROCEED",
            "PROCEED_WITH_GAPS",
            "BLOCKED_MODEL_UNAVAILABLE",
            "BLOCKED_VERIFICATION_MODEL_UNAVAILABLE",
        }
        else "UNKNOWN"
    )
    safe_failure_category = (
        summary.failure_category
        if summary.failure_category
        in {
            "authentication",
            "transport",
            "rate_limit",
            "model",
            "tool_validation",
            "tool_execution",
            "timeout",
            "unknown",
        }
        else "NONE"
    )
    safe_failure_cause = (
        summary.failure_cause
        if summary.failure_cause
        in {
            "output_item_extraction",
            "namespace_function_name_parsing",
            "arguments_parsing",
            "dispatcher",
            "function_call_output",
            "continuation_history",
            "api_validation",
            "model_behavior",
            "preview_protocol_mismatch",
            "duplicate_tool_call",
            "unknown",
            "NONE",
        }
        else "unknown"
    )
    context_arguments = _safe_boolean_values(summary.context_arguments_exactly_empty_object)
    budget_arguments = _safe_boolean_values(summary.budget_arguments_exactly_empty_object)
    continuations = _safe_boolean_values(summary.function_output_continuations)
    observed_order = _safe_tool_order(summary.observed_tool_call_order)
    tool_order = _safe_tool_order(summary.tool_call_order)

    return {
        "schema_version": 1,
        "timestamp_utc": _safe_timestamp(summary.timestamp_utc),
        "git_sha_before_evidence_commit": summary.git_sha_before_evidence_commit.lower()
        if isinstance(summary.git_sha_before_evidence_commit, str)
        and SAFE_GIT_SHA.fullmatch(summary.git_sha_before_evidence_commit)
        else "UNKNOWN",
        "default_model": summary.default_model
        if _safe_model_id(summary.default_model)
        else "UNKNOWN",
        "verification_model": summary.verification_model
        if _safe_model_id(summary.verification_model)
        else "UNKNOWN",
        "authentication": {
            "authenticated": bool(summary.authenticated),
            "refresh_available": bool(summary.refresh_available),
            "plan_usage_enabled": bool(summary.plan_usage_enabled),
        },
        "model_discovery_request_count": _safe_count(summary.model_discovery_request_count),
        "model_catalog": catalog,
        "selected_model": selected_model,
        "responses_request_count": _safe_count(summary.responses_request_count),
        "responses_request_models": [
            model for model in summary.responses_request_models if _safe_model_id(model)
        ],
        "response_ids": _safe_identifiers(summary.response_ids, SAFE_RESPONSE_ID),
        "request_ids": _safe_identifiers(summary.request_ids, SAFE_REQUEST_ID),
        "event_types": [event for event in summary.event_types if event in ALLOWED_EVENT_TYPES],
        "response_output_item_done_function_call_counts": [
            _safe_count(count) for count in summary.response_output_item_done_function_call_counts
        ],
        "observed_tool_call_order": observed_order,
        "tool_call_order": tool_order,
        "tool_executions": {
            "research_context_get": {
                "count": _safe_count(summary.context_execution_count),
                "arguments_exactly_empty_object": context_arguments,
            },
            "research_budget_get": {
                "count": _safe_count(summary.budget_execution_count),
                "arguments_exactly_empty_object": budget_arguments,
            },
        },
        "duplicate_tool_call_count": _safe_count(summary.duplicate_tool_call_count),
        "duplicate_tool_execution_count": _safe_count(summary.duplicate_tool_execution_count),
        "function_output_continuations": ["PASS" if value else "FAIL" for value in continuations],
        "final_response_present": bool(summary.final_response_present),
        "usage": {
            "input": _safe_count(summary.usage_input_tokens),
            "output": _safe_count(summary.usage_output_tokens),
            "total": _safe_count(summary.usage_total_tokens),
        },
        "elapsed_seconds": _safe_elapsed(summary.elapsed_seconds),
        "capability_boundary": {
            "available_local_functions": ["research_context_get", "research_budget_get"],
            "prohibited_capabilities_exposed": [],
            "changed": False,
        },
        "failure_category": safe_failure_category,
        "failure_cause": safe_failure_cause,
        "secret_exposure": "NONE",
        "result": safe_result,
        "limitations": [
            "Only the two fixed local research functions were available to the model.",
            (
                "Only event type names are retained from incomplete streams; "
                "response payloads are omitted."
            ),
            "ChatGPT plan usage accounting is not exposed by this spike.",
        ],
    }


def build_evidence(summary: SmokeSummary) -> dict[str, object]:
    available_models = _safe_identifiers(summary.available_models, SAFE_MODEL_ID)
    selected_model = (
        summary.selected_model
        if _safe_model_id(summary.selected_model) and summary.selected_model in available_models
        else "UNKNOWN"
    )
    single_order = _safe_tool_order(summary.single_tool_call_order)
    multi_order = _safe_tool_order(summary.multi_tool_call_order)
    single_tool_success = summary.single_tool_success and single_order == ["research_context_get"]
    multi_tool_success = summary.multi_tool_success and multi_order == [
        "research_context_get",
        "research_budget_get",
    ]

    return {
        "timestamp_utc": _safe_timestamp(summary.timestamp_utc),
        "operating_system": (
            summary.operating_system
            if isinstance(summary.operating_system, str)
            and SAFE_PLATFORM.fullmatch(summary.operating_system)
            else "UNKNOWN"
        ),
        "python_version": (
            summary.python_version
            if isinstance(summary.python_version, str)
            and SAFE_PYTHON_VERSION.fullmatch(summary.python_version)
            else "UNKNOWN"
        ),
        "git_sha": summary.git_sha.lower()
        if isinstance(summary.git_sha, str) and SAFE_GIT_SHA.fullmatch(summary.git_sha)
        else "UNKNOWN",
        "authentication": "PASS" if summary.authenticated else "FAIL",
        "model_discovery": "PASS" if summary.model_discovery_success else "FAIL",
        "available_models": available_models,
        "selected_model": selected_model,
        "simple_response": "PASS" if summary.simple_response_success else "FAIL",
        "streaming": "PASS" if "response.completed" in summary.event_types else "FAIL",
        "tool_calls": {
            "single": single_order,
            "single_result": "PASS" if single_tool_success else "FAIL",
            "multi": multi_order,
            "multi_result": "PASS" if multi_tool_success else "FAIL",
            "unknown_tool_denial": bool(summary.unknown_tool_denial),
        },
        "response_ids": _safe_identifiers(summary.response_ids, SAFE_RESPONSE_ID),
        "request_ids": _safe_identifiers(summary.request_ids, SAFE_REQUEST_ID),
        "event_types": [event for event in summary.event_types if event in ALLOWED_EVENT_TYPES],
        "usage": {
            "input": _safe_count(summary.usage_input_tokens),
            "output": _safe_count(summary.usage_output_tokens),
            "total": _safe_count(summary.usage_total_tokens),
        },
        "rate_limit_metadata": _safe_rate_limit_metadata(summary.rate_limit_metadata),
        "plan_usage_accounting": "UNKNOWN",
        "elapsed_seconds": (_safe_elapsed(summary.elapsed_seconds)),
        "capabilities": list(CAPABILITIES),
        "known_limitations": [
            "ChatGPT plan usage accounting is not exposed by this spike.",
            "Only the two local mock research tools are available.",
            "Responses preview behavior can change; consult the official documentation.",
        ],
    }


def _safe_timestamp(value: str) -> str:
    try:
        if not isinstance(value, str):
            return "UNKNOWN"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return "UNKNOWN"
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return "UNKNOWN"


def _safe_identifiers(values: tuple[str, ...], pattern: re.Pattern[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for value in values
            if isinstance(value, str) and pattern.fullmatch(value) and not _looks_sensitive(value)
        )
    )


def _safe_model_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(SAFE_MODEL_ID.fullmatch(value))
        and not _looks_sensitive(value)
    )


def _looks_sensitive(value: str) -> bool:
    lowered = value.casefold()
    return any(
        marker in lowered
        for marker in ("bearer", "token", "secret", "authorization", "cookie", "code=")
    )


def _safe_tool_order(values: tuple[str, ...]) -> list[str]:
    return [name for name in values if name in ALLOWED_TOOL_NAMES]


def _safe_boolean_values(values: tuple[bool, ...]) -> list[bool]:
    return [value for value in values if type(value) is bool]


def _safe_count(value: int | None) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _safe_elapsed(value: float) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return round(value, 3)
    return None


def _safe_rate_limit_metadata(values: dict[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in values.items()
        if name in RATE_LIMIT_HEADERS
        and isinstance(value, str)
        and SAFE_HEADER_VALUE.fullmatch(value)
    }
