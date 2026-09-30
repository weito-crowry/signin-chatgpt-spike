from __future__ import annotations

import builtins
import importlib
import importlib.util
import json
import pathlib
import socket
import subprocess

import pytest


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def test_static_allowlist_and_tool_results_are_explicit() -> None:
    tools = load_module("tools")

    assert set(tools.TOOLS) == {"research_context_get", "research_budget_get"}
    assert json.loads(tools.dispatch_tool("research_context_get", "{}")) == {
        "research_question": "Can a minimal Responses harness safely call local research tools?",
        "status": "SPIKE_CONTEXT_OK",
    }
    assert json.loads(tools.dispatch_tool("research_budget_get", "{}")) == {
        "remaining_iterations": 3
    }


@pytest.mark.parametrize(
    "name",
    ["shell_execute", "broker_direct_connect", "filesystem_read", "http_request"],
)
def test_unknown_tool_is_denied_without_capability_fallback(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = load_module("tools")
    calls: list[str] = []

    def denied(operation: str):
        def fail(*args: object, **kwargs: object) -> None:
            calls.append(operation)
            raise AssertionError(f"unexpected capability: {operation}")

        return fail

    monkeypatch.setattr(subprocess, "Popen", denied("subprocess"))
    monkeypatch.setattr(subprocess, "run", denied("subprocess"))
    monkeypatch.setattr(builtins, "open", denied("filesystem"))
    monkeypatch.setattr(pathlib.Path, "open", denied("filesystem"))
    monkeypatch.setattr(socket, "socket", denied("network"))
    monkeypatch.setattr(socket, "create_connection", denied("network"))

    with pytest.raises(tools.ToolDispatchError) as raised:
        tools.dispatch_tool(name, "{}")

    assert raised.value.category == "tool_validation"
    assert calls == []


@pytest.mark.parametrize("arguments", ["{", "[]", "null", '{"unexpected": 1}'])
def test_tool_arguments_must_be_valid_empty_objects(arguments: str) -> None:
    tools = load_module("tools")

    with pytest.raises(tools.ToolDispatchError) as raised:
        tools.dispatch_tool("research_context_get", arguments)

    assert raised.value.category == "tool_validation"


def test_tool_definitions_are_grouped_in_one_research_namespace() -> None:
    tools = load_module("tools")

    assert tools.TOOL_DEFINITIONS[0]["type"] == "namespace"
    assert tools.TOOL_DEFINITIONS[0]["name"] == "research"
    assert {definition["name"] for definition in tools.TOOL_DEFINITIONS[0]["tools"]} == {
        "research_context_get",
        "research_budget_get",
    }
