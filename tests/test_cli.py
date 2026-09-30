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


def summary(text: str = "", *items: dict[str, object]):
    models = load_module("models")
    return models.ResponseSummary(
        response_id="resp_EXAMPLE_ONLY_NOT_A_REAL_ID",
        model=EXAMPLE_MODEL,
        text=text,
        output_items=tuple(items),
        usage=models.UsageSummary(input_tokens=2, output_tokens=1, total_tokens=3),
        event_types=("response.created", "response.output_text.delta", "response.completed"),
        request_id="req_EXAMPLE_ONLY_NOT_A_REAL_ID",
        rate_limit_metadata={"x-ratelimit-remaining-requests": "99"},
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


def test_explicit_luna_is_allowed() -> None:
    cli = load_module("cli")

    assert cli._select_model([model_info(EXAMPLE_MODEL)], EXAMPLE_MODEL) == EXAMPLE_MODEL


@pytest.mark.parametrize("requested_model", ["gpt-6-astra", "gpt-5.6-luna"])
def test_explicit_non_luna_models_are_rejected_even_when_available(requested_model) -> None:
    cli = load_module("cli")
    errors = load_module("errors")

    with pytest.raises(errors.ResponsesError) as error:
        cli._select_model([model_info(requested_model)], requested_model)

    assert error.value.category == "model"


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
