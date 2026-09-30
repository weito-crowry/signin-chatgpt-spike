from __future__ import annotations

import importlib
import importlib.util
from datetime import UTC, datetime


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def test_evidence_contains_only_allowlisted_sanitized_metadata() -> None:
    evidence = load_module("evidence")
    summary = evidence.SmokeSummary(
        timestamp_utc=datetime(2026, 9, 30, tzinfo=UTC).isoformat(),
        operating_system="Windows 11",
        python_version="3.11.9",
        git_sha="0123456789abcdef0123456789abcdef01234567",
        authenticated=True,
        model_discovery_success=True,
        available_models=("gpt-6-luna", "gpt-6.1-sol"),
        selected_model="gpt-6-luna",
        simple_response_success=True,
        single_tool_success=True,
        single_tool_call_order=("research_context_get",),
        multi_tool_success=True,
        multi_tool_call_order=("research_context_get", "research_budget_get"),
        response_ids=("resp_EXAMPLE_ONLY_NOT_A_REAL_ID",),
        request_ids=("req_EXAMPLE_ONLY_NOT_A_REAL_ID",),
        event_types=("response.created", "response.output_text.delta", "response.completed"),
        usage_input_tokens=20,
        usage_output_tokens=10,
        usage_total_tokens=30,
        rate_limit_metadata={"x-ratelimit-remaining-requests": "99"},
        elapsed_seconds=4.2,
        unknown_tool_denial=True,
    )

    public_record = evidence.build_evidence(summary)

    assert public_record["selected_model"] == "gpt-6-luna"
    assert public_record["request_ids"] == ["req_EXAMPLE_ONLY_NOT_A_REAL_ID"]
    assert public_record["usage"] == {"input": 20, "output": 10, "total": 30}
    assert public_record["rate_limit_metadata"] == {"x-ratelimit-remaining-requests": "99"}
    assert public_record["capabilities"] == [
        "Responses inference",
        "research_context_get",
        "research_budget_get",
    ]
    assert public_record["plan_usage_accounting"] == "UNKNOWN"
    assert "final_response_text" not in public_record
    assert not any(
        "token" in key.lower() and key != "plan_usage_accounting" for key in public_record
    )


def test_evidence_rejects_identifiers_that_do_not_match_safe_formats() -> None:
    evidence = load_module("evidence")
    summary = evidence.SmokeSummary(
        timestamp_utc="not-a-timestamp",
        operating_system="Windows\nEXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",
        python_version="3.11.9",
        git_sha="Bearer EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",
        authenticated=True,
        model_discovery_success=True,
        available_models=("Bearer_EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",),
        selected_model="Bearer_EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",
        simple_response_success=True,
        single_tool_success=False,
        single_tool_call_order=("shell_execute",),
        multi_tool_success=False,
        multi_tool_call_order=("filesystem_read",),
        response_ids=("Bearer_EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",),
        request_ids=("Bearer_EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS",),
        event_types=("response.created", "secret_payload"),
        usage_input_tokens=-1,
        usage_output_tokens=None,
        usage_total_tokens=None,
        rate_limit_metadata={"x-secret": "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"},
        elapsed_seconds=-1,
        unknown_tool_denial=False,
    )

    public_record = evidence.build_evidence(summary)
    serialized = repr(public_record)

    assert public_record["git_sha"] == "UNKNOWN"
    assert public_record["available_models"] == []
    assert public_record["selected_model"] == "UNKNOWN"
    assert public_record["response_ids"] == []
    assert public_record["request_ids"] == []
    assert public_record["tool_calls"]["single"] == []
    assert public_record["tool_calls"]["multi"] == []
    assert public_record["tool_calls"]["unknown_tool_denial"] is False
    assert public_record["rate_limit_metadata"] == {}
    assert "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS" not in serialized
    assert "secret_payload" not in serialized
