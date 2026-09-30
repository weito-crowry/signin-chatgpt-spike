"""Small explicit command line entry point; only ``smoke`` makes inference calls."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .auth import AuthManager
from .client import ResponsesClient
from .credentials import DpapiTokenStore, KeyringRegistrationStore
from .errors import ResponsesError, SpikeError
from .evidence import (
    SmokeSummary,
    ToolLoopEvidenceSummary,
    build_evidence,
    build_tool_loop_evidence,
)
from .host_identity import KeyringHostIdentityStore
from .models import ModelInfo, ResponseSummary
from .tool_loop import ToolLoopResult, ToolLoopTrace, run_tool_loop
from .tools import ToolDispatchError, dispatch_tool

DEFAULT_MODEL_ID = "gpt-6-luna"
VERIFICATION_MODEL_ID = "gpt-5.6-luna"
ALLOWED_MODEL_IDS = frozenset({DEFAULT_MODEL_ID, VERIFICATION_MODEL_ID})
SIMPLE_PROMPT = "Return exactly:\nSIGNIN_CHATGPT_SPIKE_OK"
SIMPLE_EXPECTED_TEXT = "SIGNIN_CHATGPT_SPIKE_OK"
SINGLE_TOOL_PROMPT = "Call research_context_get once. Then summarize the returned research context."
MULTI_TOOL_PROMPT = (
    "Call research_context_get first, then research_budget_get. "
    "After both results, summarize the research context and remaining budget."
)


def create_auth_manager() -> AuthManager:
    return AuthManager(
        host_identity_store=KeyringHostIdentityStore(),
        registration_store=KeyringRegistrationStore(),
        token_store=DpapiTokenStore(),
    )


def create_responses_client() -> ResponsesClient:
    return ResponsesClient()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signin-chatgpt-spike",
        description="A small Sign in with ChatGPT and Responses API reference spike.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    auth_parser = commands.add_parser("auth", help="Manage the local ChatGPT sign-in.")
    auth_parser.add_argument("action", choices=("login", "status", "logout"))

    commands.add_parser("models", help="List models visible to the signed-in account.")
    infer_parser = commands.add_parser("infer", help="Run one fixed streaming inference.")
    infer_parser.add_argument("--model", help="Exact formal target model slug (gpt-6-luna).")

    demo_parser = commands.add_parser("demo-tools", help="Run the two-tool local research demo.")
    demo_parser.add_argument(
        "--model",
        help="Exact model slug; default is gpt-6-luna; gpt-5.6-luna is for tool-loop verification.",
    )
    demo_parser.add_argument(
        "--evidence",
        type=Path,
        help="Write a public-safe JSON summary of this ordered tool-loop run.",
    )

    smoke_parser = commands.add_parser(
        "smoke", help="Run the explicit live inference and local tool smoke sequence."
    )
    smoke_parser.add_argument("--model", help="Exact formal target model slug (gpt-6-luna).")
    smoke_parser.add_argument(
        "--evidence",
        type=Path,
        help=(
            "Optional path for a public-safe JSON summary; raw output and credentials are excluded."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            return _run_auth(args.action)
        if args.command == "models":
            return _run_models()
        if args.command == "infer":
            return _run_infer(args.model)
        if args.command == "demo-tools":
            return _run_demo_tools(args.model, args.evidence)
        if args.command == "smoke":
            return _run_smoke(args.model, args.evidence)
        print("ERROR category=unknown message=Unsupported command.", file=sys.stderr)
        return 2
    except SpikeError as error:
        print(
            f"ERROR category={error.category} message={error}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Never show an unexpected traceback; upstream libraries may include request data.
        print("ERROR category=unknown message=Unexpected local failure.", file=sys.stderr)
        return 1


def _run_auth(action: str) -> int:
    manager = create_auth_manager()
    if action == "login":
        status = manager.login()
        _print_auth_status(status)
    elif action == "status":
        _print_auth_status(manager.status())
    else:
        status = manager.logout()
        print(f"tokens_removed: {str(status.tokens_removed).lower()}")
        revocation = "PASS" if status.remote_revocation_confirmed else "UNKNOWN"
        print(f"remote_revocation: {revocation}")
    return 0


def _print_auth_status(status: object) -> None:
    print(f"authenticated: {str(status.authenticated).lower()}")
    print(f"expires_at: {status.expires_at or 'UNKNOWN'}")
    print(f"refresh_available: {str(status.refresh_available).lower()}")
    print(f"plan_usage_enabled: {str(status.plan_usage_enabled).lower()}")


def _authorized_runtime() -> tuple[str, ResponsesClient]:
    manager = create_auth_manager()
    if not manager.status().authenticated:
        raise ResponsesError(
            "Sign in with ChatGPT before making a request.", category="authentication"
        )
    access_token = manager.access_token()
    return access_token, create_responses_client()


def _discover_models(client: ResponsesClient, access_token: str) -> list[ModelInfo]:
    models = client.list_models(access_token)
    if not models:
        raise ResponsesError("No account-visible models were returned.", category="model")
    return models


def _select_model(
    models: list[ModelInfo],
    requested_model: str | None,
    *,
    allow_verification_model: bool = False,
) -> str:
    available = {item.slug for item in models}
    selected_model = requested_model or DEFAULT_MODEL_ID
    if selected_model not in ALLOWED_MODEL_IDS or (
        selected_model == VERIFICATION_MODEL_ID and not allow_verification_model
    ):
        raise ResponsesError("This CLI does not allow the requested model slug.", category="model")
    if selected_model not in available:
        raise ResponsesError(
            "The requested model is not present in the account model catalog.", category="model"
        )
    return selected_model


def _run_models() -> int:
    access_token, client = _authorized_runtime()
    for model in _discover_models(client, access_token):
        print(f"{model.slug}\t{model.display_name}")
    return 0


def _run_infer(requested_model: str | None) -> int:
    access_token, client = _authorized_runtime()
    models = _discover_models(client, access_token)
    model = _select_model(models, requested_model)
    started = time.perf_counter()
    response = client.stream_response(
        access_token,
        model,
        [{"role": "user", "content": SIMPLE_PROMPT}],
    )
    elapsed = time.perf_counter() - started
    print(f"Model               {model}")
    print(
        f"Streaming           {'PASS' if 'response.completed' in response.event_types else 'FAIL'}"
    )
    print(f"Response ID         {response.response_id or 'UNKNOWN'}")
    print(f"Request ID          {response.request_id or 'UNKNOWN'}")
    print(f"Usage               {_format_usage(response)}")
    print(f"Elapsed             {elapsed:.2f}s")
    print(f"Final response      {response.text}")
    return 0 if response.text == SIMPLE_EXPECTED_TEXT else 1


def _run_demo_tools(requested_model: str | None, evidence_path: Path | None = None) -> int:
    started = time.perf_counter()
    auth_manager = create_auth_manager()
    auth_status = auth_manager.status()
    if not auth_status.authenticated:
        raise ResponsesError(
            "Sign in with ChatGPT before running the tool-loop demo.", category="authentication"
        )
    access_token = auth_manager.access_token()
    client = create_responses_client()
    trace = ToolLoopTrace()
    models: list[ModelInfo] = []
    catalog_loaded = False
    model: str | None = None
    result: ToolLoopResult | None = None
    failure: SpikeError | None = None
    try:
        models = client.list_models(access_token)
        catalog_loaded = True
        model = _select_model(models, requested_model, allow_verification_model=True)
        result = run_tool_loop(
            client, access_token, model, MULTI_TOOL_PROMPT, max_steps=3, trace=trace
        )
    except SpikeError as error:
        failure = error
    except Exception:
        failure = ResponsesError(
            "The ordered tool-loop verification failed locally.", category="unknown"
        )

    elapsed = time.perf_counter() - started
    expected_order = ("research_context_get", "research_budget_get")
    required_call_events = {
        "response.output_item.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    }
    completed_successfully = (
        failure is None
        and result is not None
        and result.tool_call_order == expected_order
        and bool(result.final_text)
        and trace.request_count == 3
        and trace.requested_models == [model, model, model]
        and trace.tool_arguments_exactly_empty_object == [True, True]
        and trace.tool_execution_counts == {"research_context_get": 1, "research_budget_get": 1}
        and trace.duplicate_tool_call_count == 0
        and trace.duplicate_tool_execution_count == 0
        and trace.function_output_continuations == [True, True]
        and [response.streamed_function_call_item_done_count for response in trace.responses]
        == [1, 1, 0]
        and all(
            required_call_events.issubset(response.event_types) for response in trace.responses[:2]
        )
    )
    if failure is not None and requested_model == VERIFICATION_MODEL_ID:
        if catalog_loaded and VERIFICATION_MODEL_ID not in {item.slug for item in models}:
            evidence_result = "BLOCKED_VERIFICATION_MODEL_UNAVAILABLE"
        else:
            evidence_result = "PROCEED_WITH_GAPS"
    elif failure is not None and (requested_model or DEFAULT_MODEL_ID) == DEFAULT_MODEL_ID:
        if catalog_loaded and DEFAULT_MODEL_ID not in {item.slug for item in models}:
            evidence_result = "BLOCKED_MODEL_UNAVAILABLE"
        else:
            evidence_result = "PROCEED_WITH_GAPS"
    else:
        evidence_result = "PROCEED" if completed_successfully else "PROCEED_WITH_GAPS"

    if evidence_path is not None:
        _write_tool_loop_evidence(
            evidence_path,
            auth_status=auth_status,
            models=models,
            selected_model=model,
            trace=trace,
            result=evidence_result,
            failure=failure,
            elapsed_seconds=elapsed,
        )
        print(f"Evidence            {evidence_path}")

    if failure is not None:
        raise failure
    if result is None:
        raise ResponsesError("The ordered tool-loop produced no result.", category="unknown")

    print(f"Model               {model}")
    print(f"Responses requests  {trace.request_count}")
    print(f"Tool calls          {len(result.tool_call_order)}")
    print(f"Tool order          {' -> '.join(result.tool_call_order) or 'NONE'}")
    argument_check = trace.tool_arguments_exactly_empty_object == [True, True]
    print(f"Arguments           {'PASS' if argument_check else 'FAIL'}")
    continuation_checks = [
        "PASS" if value else "FAIL" for value in trace.function_output_continuations
    ]
    print(f"Function outputs    {continuation_checks}")
    print(f"Duplicate executions {trace.duplicate_tool_execution_count}")
    print(f"Final response      {'PASS' if result.final_text else 'FAIL'}")
    print(f"Usage               {_format_total_usage(trace.responses)}")
    print(f"Elapsed             {elapsed:.2f}s")
    return 0 if completed_successfully else 1


def _write_tool_loop_evidence(
    path: Path,
    *,
    auth_status: object,
    models: list[ModelInfo],
    selected_model: str | None,
    trace: ToolLoopTrace,
    result: str,
    failure: SpikeError | None,
    elapsed_seconds: float,
) -> None:
    context_arguments: list[bool] = []
    budget_arguments: list[bool] = []
    for name, is_empty_object in zip(
        trace.observed_tool_call_order,
        trace.tool_arguments_exactly_empty_object,
        strict=False,
    ):
        if name == "research_context_get":
            context_arguments.append(is_empty_object)
        elif name == "research_budget_get":
            budget_arguments.append(is_empty_object)

    responses = trace.responses
    usage = [response.usage for response in responses]
    failure_cause = "NONE"
    if failure is not None:
        if trace.duplicate_tool_call_count:
            failure_cause = "duplicate_tool_call"
        elif trace.phase == "local_tool_dispatch":
            failure_cause = "dispatcher"
        elif trace.phase == "function_output_append":
            failure_cause = "function_call_output"
        elif trace.phase == "output_item_processing":
            failure_cause = "output_item_extraction"
        elif trace.request_count and getattr(failure, "category", "unknown") == "model":
            failure_cause = "api_validation"
        else:
            failure_cause = "unknown"

    summary = ToolLoopEvidenceSummary(
        timestamp_utc=datetime.now(UTC).isoformat(),
        git_sha_before_evidence_commit=_git_sha(),
        default_model=DEFAULT_MODEL_ID,
        verification_model=VERIFICATION_MODEL_ID,
        model_catalog=tuple(item.slug for item in models),
        selected_model=selected_model,
        model_discovery_request_count=1,
        responses_request_count=trace.request_count,
        responses_request_models=tuple(trace.requested_models),
        response_ids=tuple(
            response.response_id for response in responses if response.response_id is not None
        ),
        request_ids=tuple(
            response.request_id for response in responses if response.request_id is not None
        ),
        event_types=(
            tuple(event for response in responses for event in response.event_types)
            + tuple(trace.partial_event_types)
        ),
        response_output_item_done_function_call_counts=tuple(
            response.streamed_function_call_item_done_count for response in responses
        ),
        observed_tool_call_order=tuple(trace.observed_tool_call_order),
        tool_call_order=tuple(trace.tool_call_order),
        context_arguments_exactly_empty_object=tuple(context_arguments),
        budget_arguments_exactly_empty_object=tuple(budget_arguments),
        context_execution_count=trace.tool_execution_counts["research_context_get"],
        budget_execution_count=trace.tool_execution_counts["research_budget_get"],
        duplicate_tool_call_count=trace.duplicate_tool_call_count,
        duplicate_tool_execution_count=trace.duplicate_tool_execution_count,
        function_output_continuations=tuple(trace.function_output_continuations),
        final_response_present=bool(responses and responses[-1].text.strip()),
        usage_input_tokens=_sum_usage(usage, "input_tokens"),
        usage_output_tokens=_sum_usage(usage, "output_tokens"),
        usage_total_tokens=_sum_usage(usage, "total_tokens"),
        elapsed_seconds=elapsed_seconds,
        authenticated=bool(auth_status.authenticated),
        refresh_available=bool(auth_status.refresh_available),
        plan_usage_enabled=bool(auth_status.plan_usage_enabled),
        result=result,
        failure_category=getattr(failure, "category", "NONE") if failure is not None else "NONE",
        failure_cause=failure_cause,
    )
    record = build_tool_loop_evidence(summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _sum_usage(usage: list[object], field: str) -> int | None:
    values = [getattr(item, field) for item in usage if getattr(item, field) is not None]
    return sum(values) if values else None


def _format_total_usage(responses: tuple[ResponseSummary, ...] | list[ResponseSummary]) -> str:
    usage = [response.usage for response in responses]
    return " ".join(
        f"{name}={_sum_usage(usage, field) if _sum_usage(usage, field) is not None else 'UNKNOWN'}"
        for name, field in (
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("total", "total_tokens"),
        )
    )


def _run_smoke(requested_model: str | None, evidence_path: Path | None) -> int:
    started = time.perf_counter()
    auth_manager = create_auth_manager()
    auth_status = auth_manager.status()
    if not auth_status.authenticated:
        raise ResponsesError(
            "Sign in with ChatGPT before running live smoke.", category="authentication"
        )
    access_token = auth_manager.access_token()
    client = create_responses_client()
    models = _discover_models(client, access_token)
    model = _select_model(models, requested_model)

    simple = client.stream_response(
        access_token,
        model,
        [{"role": "user", "content": SIMPLE_PROMPT}],
    )
    single = run_tool_loop(client, access_token, model, SINGLE_TOOL_PROMPT)
    multiple = run_tool_loop(client, access_token, model, MULTI_TOOL_PROMPT)
    unknown_tool_denial = _check_unknown_tool_denial()
    elapsed = time.perf_counter() - started
    all_responses = [simple, *single.responses, *multiple.responses]
    response_ids = tuple(
        response.response_id for response in all_responses if response.response_id is not None
    )
    request_ids = tuple(
        response.request_id for response in all_responses if response.request_id is not None
    )
    event_types = tuple(event for response in all_responses for event in response.event_types)
    usage_values = [
        response.usage for response in all_responses if response.usage.total_tokens is not None
    ]
    rate_limit_metadata = {
        name: value
        for response in all_responses
        for name, value in response.rate_limit_metadata.items()
    }
    summary = SmokeSummary(
        timestamp_utc=datetime.now(UTC).isoformat(),
        operating_system=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
        git_sha=_git_sha(),
        authenticated=auth_status.authenticated,
        model_discovery_success=True,
        available_models=tuple(item.slug for item in models),
        selected_model=model,
        simple_response_success=simple.text == SIMPLE_EXPECTED_TEXT,
        single_tool_success=single.tool_call_order == ("research_context_get",)
        and bool(single.final_text),
        single_tool_call_order=single.tool_call_order,
        multi_tool_success=multiple.tool_call_order
        == ("research_context_get", "research_budget_get")
        and bool(multiple.final_text),
        multi_tool_call_order=multiple.tool_call_order,
        response_ids=response_ids,
        request_ids=request_ids,
        event_types=event_types,
        usage_input_tokens=sum(item.input_tokens or 0 for item in usage_values),
        usage_output_tokens=sum(item.output_tokens or 0 for item in usage_values),
        usage_total_tokens=sum(item.total_tokens or 0 for item in usage_values),
        rate_limit_metadata=rate_limit_metadata,
        elapsed_seconds=elapsed,
        unknown_tool_denial=unknown_tool_denial,
    )
    record = build_evidence(summary)
    if evidence_path is not None:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Evidence            {evidence_path}")
    _print_smoke_summary(summary, simple, single, multiple)
    return (
        0
        if all(
            (
                summary.authenticated,
                summary.model_discovery_success,
                summary.simple_response_success,
                summary.single_tool_success,
                summary.multi_tool_success,
                summary.unknown_tool_denial,
            )
        )
        else 1
    )


def _check_unknown_tool_denial() -> bool:
    try:
        dispatch_tool("shell_execute", "{}")
    except ToolDispatchError as error:
        return error.category == "tool_validation"
    return False


def _print_smoke_summary(
    summary: SmokeSummary,
    simple: ResponseSummary,
    single: ToolLoopResult,
    multiple: ToolLoopResult,
) -> None:
    print(f"Authentication       {'PASS' if summary.authenticated else 'FAIL'}")
    print(f"Model discovery      {'PASS' if summary.model_discovery_success else 'FAIL'}")
    print(f"Selected model       {summary.selected_model}")
    streaming = "response.completed" in simple.event_types
    print(f"Streaming            {'PASS' if streaming else 'FAIL'}")
    print(f"Simple response      {'PASS' if summary.simple_response_success else 'FAIL'}")
    print(f"Response ID          {simple.response_id or 'UNKNOWN'}")
    print(f"Request ID          {simple.request_id or 'UNKNOWN'}")
    final_result = SIMPLE_EXPECTED_TEXT if summary.simple_response_success else "FAIL"
    print(f"Final response       {final_result}")
    print(f"Simple usage         {_format_usage(simple)}")
    print(f"Single tool calls    {len(single.tool_call_order)}")
    print(f"Single tool order    {' -> '.join(single.tool_call_order) or 'NONE'}")
    print(f"Multi-tool calls     {len(multiple.tool_call_order)}")
    print(f"Multi-tool order     {' -> '.join(multiple.tool_call_order) or 'NONE'}")
    print(f"Unknown tool denial  {'PASS' if summary.unknown_tool_denial else 'FAIL'}")
    print(f"Elapsed              {summary.elapsed_seconds:.2f}s")
    print("Plan usage accounting UNKNOWN")


def _format_usage(response: ResponseSummary) -> str:
    usage = response.usage
    return (
        f"input={usage.input_tokens if usage.input_tokens is not None else 'UNKNOWN'} "
        f"output={usage.output_tokens if usage.output_tokens is not None else 'UNKNOWN'} "
        f"total={usage.total_tokens if usage.total_tokens is not None else 'UNKNOWN'}"
    )


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            cwd=Path.cwd(),
            shell=False,
            timeout=2,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
