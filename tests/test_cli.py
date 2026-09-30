from __future__ import annotations

import importlib
import importlib.util
import json

import pytest

EXAMPLE_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"
EXAMPLE_MODEL = "gpt-6-luna"


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def summary(
    text: str = "",
    *items: dict[str, object],
    event_types: tuple[str, ...] | None = None,
    response_id: str = "resp_EXAMPLE_ONLY_NOT_A_REAL_ID",
    request_id: str = "req_EXAMPLE_ONLY_NOT_A_REAL_ID",
):
    models = load_module("models")
    return models.ResponseSummary(
        response_id=response_id,
        model=EXAMPLE_MODEL,
        text=text,
        output_items=tuple(items),
        usage=models.UsageSummary(input_tokens=2, output_tokens=1, total_tokens=3),
        event_types=event_types
        or ("response.created", "response.output_text.delta", "response.completed"),
        request_id=request_id,
        rate_limit_metadata={"x-ratelimit-remaining-requests": "99"},
        streamed_function_call_item_done_count=(
            sum(item.get("type") == "function_call" for item in items)
            if event_types is not None and "response.output_item.done" in event_types
            else 0
        ),
    )


class FakeAuthManager:
    def __init__(self, status=None) -> None:
        models = load_module("models")
        self._status = status or models.AuthStatus(True, "2030-01-01T00:00:00Z", True, True)

    def login(self):
        return self._status

    def status(self):
        return self._status

    def access_token(self) -> str:
        return EXAMPLE_TOKEN

    def logout(self):
        return load_module("models").LogoutStatus(True, True)


class FakeResponsesClient:
    def __init__(self, responses: list[object], model_catalog=None) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []
        self.model_catalog = (
            model_catalog
            if model_catalog is not None
            else [
                load_module("models").ModelInfo(EXAMPLE_MODEL, "GPT-6 Luna"),
                load_module("models").ModelInfo("gpt-6.1-sol", "GPT-6.1 Sol"),
            ]
        )

    def list_models(self, access_token: str):
        assert access_token == EXAMPLE_TOKEN
        return self.model_catalog

    def stream_response(self, access_token, model, input_items, tools=None):
        self.requests.append({"input": list(input_items), "tools": tools, "model": model})
        if not self.responses:
            raise AssertionError("unexpected additional inference request")
        return self.responses.pop(0)


def tool_call(call_id: str, name: str) -> dict[str, object]:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": "{}",
        "status": "completed",
    }


def model_info(slug: str):
    return load_module("models").ModelInfo(slug, slug)


@pytest.mark.parametrize(
    "catalog",
    [
        ["gpt-6-astra", "gpt-6-luna", "gpt-5.6-luna"],
        ["gpt-5.6-luna", "gpt-6-luna", "gpt-6-astra"],
        ["gpt-6-astra", "gpt-5.6-sol", "gpt-6-luna"],
        ["gpt-6-luna", "gpt-5.6-sol", "gpt-6-astra"],
    ],
)
def test_unspecified_model_is_exact_luna_independent_of_catalog_order(catalog) -> None:
    cli = load_module("cli")

    assert cli._select_model([model_info(slug) for slug in catalog], None) == EXAMPLE_MODEL


def test_unspecified_model_does_not_fall_back_when_default_is_unavailable() -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info("gpt-5.6-luna"), model_info("gpt-6-astra")], None)

    assert error.value.category == "model"


def test_explicit_luna_is_allowed() -> None:
    cli = load_module("cli")

    assert cli._select_model([model_info(EXAMPLE_MODEL)], EXAMPLE_MODEL) == EXAMPLE_MODEL


def test_explicit_default_luna_is_rejected_when_unavailable() -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info("gpt-5.6-luna")], EXAMPLE_MODEL)

    assert error.value.category == "model"


def test_explicit_verification_luna_is_allowed_when_available() -> None:
    cli = load_module("cli")

    assert (
        cli._select_model(
            [model_info("gpt-5.6-luna")], "gpt-5.6-luna", allow_verification_model=True
        )
        == "gpt-5.6-luna"
    )


def test_explicit_verification_luna_is_rejected_when_unavailable() -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info("gpt-6-luna")], "gpt-5.6-luna", allow_verification_model=True)

    assert error.value.category == "model"


@pytest.mark.parametrize(
    "requested_model",
    ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5", "gpt-7-anything"],
)
def test_explicit_disallowed_models_are_rejected_even_when_available(requested_model) -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info(requested_model)], requested_model)

    assert error.value.category == "model"


@pytest.mark.parametrize(
    ("catalog", "requested_model"),
    [
        (["gpt-5.6-luna", "gpt-6-luna", "gpt-6-astra"], None),
        (["gpt-6-astra", "gpt-6-luna", "gpt-5.6-luna"], None),
        (["gpt-6-luna", "gpt-5.6-luna", "gpt-6-astra"], "gpt-5.6-luna"),
        (["gpt-5.6-luna", "gpt-6-astra", "gpt-6-luna"], "gpt-5.6-luna"),
    ],
)
def test_explicit_selection_is_independent_of_catalog_order(catalog, requested_model) -> None:
    cli = load_module("cli")

    expected = requested_model or EXAMPLE_MODEL
    assert (
        cli._select_model(
            [model_info(slug) for slug in catalog],
            requested_model,
            allow_verification_model=requested_model == "gpt-5.6-luna",
        )
        == expected
    )


@pytest.mark.parametrize(
    "catalog",
    [
        ["gpt-6-astra", "gpt-5.6-luna", "gpt-5.6-sol"],
        [],
    ],
)
def test_missing_luna_or_empty_catalog_is_a_model_error(catalog) -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info(slug) for slug in catalog], None)

    assert error.value.category == "model"


@pytest.mark.parametrize("command", ["infer", "demo-tools", "smoke"])
def test_live_commands_fail_closed_without_luna_before_any_inference(
    command, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    catalog = [model_info(slug) for slug in ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-luna")]
    client = FakeResponsesClient([], model_catalog=catalog)
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)

    assert cli.main([command]) != 0
    assert client.requests == []
    assert "category=model" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["infer", "smoke"])
def test_verification_model_is_rejected_by_non_verification_commands(
    command, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    catalog = [model_info("gpt-5.6-luna"), model_info("gpt-6-astra")]
    client = FakeResponsesClient([], model_catalog=catalog)
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)

    assert cli.main([command, "--model", "gpt-5.6-luna"]) != 0
    assert client.requests == []
    assert "category=model" in capsys.readouterr().err


def test_models_command_preserves_catalog_display_order(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    catalog = [model_info(slug) for slug in ("gpt-6-astra", "gpt-5.6-sol", "gpt-6-luna")]
    client = FakeResponsesClient([], model_catalog=catalog)
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)

    assert cli.main(["models"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "gpt-6-astra\tgpt-6-astra",
        "gpt-5.6-sol\tgpt-5.6-sol",
        "gpt-6-luna\tgpt-6-luna",
    ]


def test_demo_tools_records_one_sanitized_verification_loop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    cli = load_module("cli")
    model = "gpt-5.6-luna"
    tool_events = (
        "response.created",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    )
    client = FakeResponsesClient(
        [
            summary(
                "",
                tool_call("call_context", "research_context_get"),
                event_types=tool_events,
                response_id="resp_context",
                request_id="req_context",
            ),
            summary(
                "",
                tool_call("call_budget", "research_budget_get"),
                event_types=tool_events,
                response_id="resp_budget",
                request_id="req_budget",
            ),
            summary(
                "Both checks complete.",
                event_types=(
                    "response.created",
                    "response.output_text.delta",
                    "response.completed",
                ),
                response_id="resp_final",
                request_id="req_final",
            ),
        ],
        model_catalog=[model_info("gpt-6-astra"), model_info(model)],
    )
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)
    evidence_file = tmp_path / "tool-loop.json"

    result = cli.main(["demo-tools", "--model", model, "--evidence", str(evidence_file)])

    assert result == 0
    assert len(client.requests) == 3
    assert [request["model"] for request in client.requests] == [model, model, model]
    output = capsys.readouterr().out
    assert "Tool order          research_context_get -> research_budget_get" in output
    assert "Both checks complete." not in output
    record = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert record["default_model"] == "gpt-6-luna"
    assert record["verification_model"] == model
    assert record["selected_model"] == model
    assert record["model_catalog"] == ["gpt-6-astra", model]
    assert record["model_discovery_request_count"] == 1
    assert record["responses_request_count"] == 3
    assert record["response_ids"] == ["resp_context", "resp_budget", "resp_final"]
    assert record["request_ids"] == ["req_context", "req_budget", "req_final"]
    assert "response.output_item.added" in record["event_types"]
    assert "response.function_call_arguments.delta" in record["event_types"]
    assert "response.function_call_arguments.done" in record["event_types"]
    assert "response.output_item.done" in record["event_types"]
    assert "response.completed" in record["event_types"]
    assert record["response_output_item_done_function_call_counts"] == [1, 1, 0]
    assert record["tool_call_order"] == ["research_context_get", "research_budget_get"]
    assert record["tool_executions"] == {
        "research_context_get": {"count": 1, "arguments_exactly_empty_object": [True]},
        "research_budget_get": {"count": 1, "arguments_exactly_empty_object": [True]},
    }
    assert record["duplicate_tool_execution_count"] == 0
    assert record["function_output_continuations"] == ["PASS", "PASS"]
    assert record["final_response_present"] is True
    assert record["usage"] == {"input": 6, "output": 3, "total": 9}
    assert record["capability_boundary"]["available_local_functions"] == [
        "research_context_get",
        "research_budget_get",
    ]
    assert record["capability_boundary"]["prohibited_capabilities_exposed"] == []
    assert record["result"] == "PROCEED"
    serialized = evidence_file.read_text(encoding="utf-8")
    assert "Both checks complete." not in serialized
    assert EXAMPLE_TOKEN not in serialized


def test_demo_tools_saves_observed_failure_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    cli = load_module("cli")
    duplicate = tool_call("call_context", "research_context_get")
    client = FakeResponsesClient(
        [
            summary("", tool_call("call_context", "research_context_get")),
            summary("", duplicate),
        ],
        model_catalog=[model_info("gpt-5.6-luna")],
    )
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)
    evidence_file = tmp_path / "tool-loop-failure.json"

    result = cli.main(["demo-tools", "--model", "gpt-5.6-luna", "--evidence", str(evidence_file)])

    assert result != 0
    assert len(client.requests) == 2
    record = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert record["responses_request_count"] == 2
    assert record["tool_executions"]["research_context_get"]["count"] == 1
    assert record["duplicate_tool_call_count"] == 1
    assert record["duplicate_tool_execution_count"] == 0
    assert record["function_output_continuations"] == ["PASS"]
    assert record["final_response_present"] is False
    assert record["result"] == "PROCEED_WITH_GAPS"
    assert record["failure_cause"] == "duplicate_tool_call"
    assert EXAMPLE_TOKEN not in evidence_file.read_text(encoding="utf-8")
    capsys.readouterr()


def test_demo_tools_blocks_missing_verification_model_before_responses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    cli = load_module("cli")
    client = FakeResponsesClient(
        [], model_catalog=[model_info("gpt-6-astra"), model_info("gpt-5.6-sol")]
    )
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)
    evidence_file = tmp_path / "model-unavailable.json"

    result = cli.main(["demo-tools", "--model", "gpt-5.6-luna", "--evidence", str(evidence_file)])

    assert result != 0
    assert client.requests == []
    record = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert record["selected_model"] is None
    assert record["responses_request_count"] == 0
    assert record["result"] == "BLOCKED_VERIFICATION_MODEL_UNAVAILABLE"
    capsys.readouterr()


def test_demo_tools_evidence_keeps_only_allowlisted_event_types_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    cli = load_module("cli")
    errors = load_module("errors")
    client = FakeResponsesClient([], model_catalog=[model_info("gpt-5.6-luna")])

    def fail_during_stream(access_token, model, input_items, tools=None):
        raise errors.ResponsesError(
            "Safe transport failure.",
            category="transport",
            observed_event_types=("response.created", "secret_payload"),
        )

    client.stream_response = fail_during_stream
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)
    evidence_file = tmp_path / "partial-stream.json"

    result = cli.main(["demo-tools", "--model", "gpt-5.6-luna", "--evidence", str(evidence_file)])

    assert result != 0
    record = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert record["responses_request_count"] == 1
    assert record["event_types"] == ["response.created"]
    assert record["failure_category"] == "transport"
    assert "secret_payload" not in evidence_file.read_text(encoding="utf-8")
    capsys.readouterr()


def test_auth_status_is_safe_and_does_not_create_responses_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(
        cli,
        "create_responses_client",
        lambda: (_ for _ in ()).throw(AssertionError("status must be offline")),
    )

    assert cli.main(["auth", "status"]) == 0
    output = capsys.readouterr().out
    assert "authenticated: true" in output
    assert "expires_at: 2030-01-01T00:00:00Z" in output
    assert EXAMPLE_TOKEN not in output


def test_smoke_requires_authentication_before_any_api_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    models = load_module("models")
    manager = FakeAuthManager(models.AuthStatus(False, None, False, False))
    client = FakeResponsesClient([])
    monkeypatch.setattr(cli, "create_auth_manager", lambda: manager)
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)

    result = cli.main(["smoke"])

    assert result != 0
    assert client.requests == []
    captured = capsys.readouterr()
    assert "authentication" in captured.err


def test_smoke_runs_model_discovery_and_only_the_requested_live_demo_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    cli = load_module("cli")
    client = FakeResponsesClient(
        [
            summary("SIGNIN_CHATGPT_SPIKE_OK"),
            summary("", tool_call("single_context", "research_context_get")),
            summary("Context loaded."),
            summary("", tool_call("multi_context", "research_context_get")),
            summary("", tool_call("multi_budget", "research_budget_get")),
            summary("Both checks complete."),
        ]
    )
    monkeypatch.setattr(cli, "create_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli, "create_responses_client", lambda: client)
    evidence_file = tmp_path / "smoke.json"

    result = cli.main(["smoke", "--model", EXAMPLE_MODEL, "--evidence", str(evidence_file)])

    assert result == 0
    assert len(client.requests) == 6
    assert all(request["model"] == EXAMPLE_MODEL for request in client.requests)
    assert all(request["input"][0]["role"] == "user" for request in client.requests)
    output = capsys.readouterr().out
    assert "Authentication       PASS" in output
    assert "Request ID          req_EXAMPLE_ONLY_NOT_A_REAL_ID" in output
    assert "Final response       SIGNIN_CHATGPT_SPIKE_OK" in output
    saved = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert saved["selected_model"] == EXAMPLE_MODEL
    assert saved["request_ids"] == ["req_EXAMPLE_ONLY_NOT_A_REAL_ID"]
    assert saved["simple_response"] == "PASS"
    assert saved["tool_calls"]["single"] == ["research_context_get"]
    assert saved["tool_calls"]["multi"] == [
        "research_context_get",
        "research_budget_get",
    ]
    assert EXAMPLE_TOKEN not in evidence_file.read_text(encoding="utf-8")


def test_login_and_logout_print_only_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = load_module("cli")
    manager = FakeAuthManager()
    monkeypatch.setattr(cli, "create_auth_manager", lambda: manager)

    assert cli.main(["auth", "login"]) == 0
    assert cli.main(["auth", "logout"]) == 0
    output = capsys.readouterr().out
    assert "authenticated: true" in output
    assert "remote_revocation: PASS" in output
    assert EXAMPLE_TOKEN not in output
    assert "EXAMPLE_ONLY_NOT_A_REAL" not in output
