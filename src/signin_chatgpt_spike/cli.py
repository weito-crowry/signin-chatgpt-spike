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
from .evidence import SmokeSummary, build_evidence
from .host_identity import KeyringHostIdentityStore
from .models import ModelInfo, ResponseSummary
from .tool_loop import ToolLoopResult, run_tool_loop
from .tools import ToolDispatchError, dispatch_tool

DEFAULT_MODEL_ID = "gpt-6-luna"
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
    infer_parser.add_argument(
        "--model", help=f"Exact pinned model slug (only {DEFAULT_MODEL_ID} is supported)."
    )

    demo_parser = commands.add_parser("demo-tools", help="Run the two-tool local research demo.")
    demo_parser.add_argument(
        "--model", help=f"Exact pinned model slug (only {DEFAULT_MODEL_ID} is supported)."
    )

    smoke_parser = commands.add_parser(
        "smoke", help="Run the explicit live inference and local tool smoke sequence."
    )
    smoke_parser.add_argument(
        "--model", help=f"Exact pinned model slug (only {DEFAULT_MODEL_ID} is supported)."
    )
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
            return _run_demo_tools(args.model)
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


def _select_model(models: list[ModelInfo], requested_model: str | None) -> str:
    available = {item.slug for item in models}
    if requested_model is not None and requested_model != DEFAULT_MODEL_ID:
        raise ResponsesError(
            f"This CLI only supports the pinned model {DEFAULT_MODEL_ID}.", category="model"
        )
    if DEFAULT_MODEL_ID not in available:
        raise ResponsesError(
            "The pinned model is not present in the account model catalog.", category="model"
        )
    return DEFAULT_MODEL_ID


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


def _run_demo_tools(requested_model: str | None) -> int:
    access_token, client = _authorized_runtime()
    models = _discover_models(client, access_token)
    model = _select_model(models, requested_model)
    result = run_tool_loop(client, access_token, model, MULTI_TOOL_PROMPT)
    print(f"Model               {model}")
    print(f"Tool calls          {len(result.tool_call_order)}")
    print(f"Tool order          {' -> '.join(result.tool_call_order) or 'NONE'}")
    print(f"Response ID         {result.final_response.response_id or 'UNKNOWN'}")
    print(f"Usage               {_format_usage(result.final_response)}")
    print(f"Final response      {result.final_text}")
    expected = ("research_context_get", "research_budget_get")
    return 0 if result.tool_call_order == expected and result.final_text else 1


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
