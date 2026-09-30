# Sign in with ChatGPT Spike

A small Windows-first Python reference implementation that signs in to ChatGPT, discovers the signed-in account's available models, streams Responses API output, executes explicitly allowlisted local functions, and verifies renewable OAuth sessions.

## Companion article

This repository contains the reference implementation and sanitized evidence used for the following article:

- [ChatGPTの契約で自作ハーネスからResponses APIを使ってみた](https://note.com/juicy_daphne2674/n/nd5280473dfc0)

The article explains the motivation, the current Sign in with ChatGPT preview limitations, the live verification steps, and the practical differences from a normal metered Responses API integration.

## What this is

This is a technical spike for the official Sign in with ChatGPT flow and ChatGPT plan usage route. It shows the OAuth/token lifecycle, direct HTTPX requests, streaming event assembly, local function dispatch, client-managed tool continuation, token refresh/rotation, and logout/revocation in code that can be read end to end.

## What this is not

- A production agent framework, SDK, or Codex clone.
- A generic shell, filesystem, network, browser, or MCP agent.
- A production research runtime.
- A claim that Sign in with ChatGPT exposes every feature available through the normal metered OpenAI API.

## Architecture

```text
Local Python CLI
      │
      ├── Sign in with ChatGPT (loopback OAuth + PKCE)
      │       ├── Windows Credential Manager: stable host ID
      │       ├── Windows Credential Manager: registration metadata
      │       └── Current-user DPAPI file: refreshable token set
      │
      ├── GET /v1/models ── account-visible model catalog
      │
      └── POST /v1/responses (store=false, stream=true)
              │
              ├── simple streaming response
              └── local tool loop
                      ├── research_context_get
                      └── research_budget_get
```

For each tool continuation, the application sends the accumulated input history and the local function output in a new Responses request. It does not use server-side conversation storage or `previous_response_id`.

## Requirements

- Windows 10 or newer with Windows Credential Manager.
- Python 3.11 or newer.
- A browser that can open a loopback sign-in callback on `127.0.0.1:1455`.
- A ChatGPT account and plan eligible for the current Sign in with ChatGPT plan-usage preview.
- Network access to the official OpenAI authentication and API endpoints.

Model availability and plan access are account-specific. An eligible sign-in does not guarantee that a particular model or usage route will be available.

## Setup

Clone the review branch, create a virtual environment, and install the package with its offline test tools:

```powershell
git clone --branch spike/reference-implementation https://github.com/weito-crowry/signin-chatgpt-spike.git
cd signin-chatgpt-spike
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

There is no API key or `.env` file. Runtime dependencies are HTTPX, PyJWT with cryptography support, and `keyring`. The default `pytest` suite uses only local fixtures and fake credentials.

## Authentication and credential storage

Run the explicit browser sign-in command:

```powershell
python -m signin_chatgpt_spike.cli auth login
python -m signin_chatgpt_spike.cli auth status
```

The first sign-in creates and stores a stable `ext_agent_host_id` before opening the browser. The next sign-in reuses the issued client ID and the same host ID. OAuth uses the official loopback authorization-code and PKCE flow; the CLI displays authentication metadata only.

On Windows, the active `keyring` backend must be `WinVaultKeyring`. Windows Credential Manager stores the two small identity records under service name `signin-chatgpt-spike`:

- `host-id`: stable `urn:uuid:` host identity; kept across logout.
- `registration`: issued client ID and a SHA-256 digest of the validated subject; kept across logout so the same registration can be reused and account mixups can be rejected.

The access token, refresh token, ID token, scopes, and expiry are stored together in `%LOCALAPPDATA%\signin-chatgpt-spike\tokens.dpapi`, protected by Windows DPAPI for the current user. The file is outside the repository and contains only the DPAPI-protected blob. Credential Manager is used only for the small identity records because its generic credential blob limit is 2,560 bytes.

`auth logout` attempts refresh-token revocation and deletes the complete local token file even if remote revocation cannot be confirmed. It preserves the host ID and registration metadata. Run `auth login` to sign in again with that registration.

Never paste an authorization code, token, callback URL, cookie, or authorization header into source files, logs, README text, issues, screenshots, or evidence.

## Model discovery

List the models exposed to the signed-in account:

```powershell
python -m signin_chatgpt_spike.cli models
```

The client calls `GET https://api.openai.com/v1/models`, keeps entries with `visibility == "list"`, and preserves the returned order for display.

The formal/default target is `gpt-6-luna`; an omitted model always requests that exact slug and fails closed if it is absent. Catalog order never selects a model.

The explicit `gpt-5.6-luna` exception is restricted to the ordered `demo-tools` verification path. It is a spike verification model, not a replacement default. All other model IDs are rejected.

During the final live checks on 2026-09-30, the authenticated account's visible catalog contained:

- `gpt-6-astra`
- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.5`

The raw `/v1/models` payload contained seven entries in total. Two had `visibility == "hide"`. An exact raw-payload search confirmed that `gpt-6-luna` was not present at all for this account at that time, so the local `visibility == "list"` filter was not the reason it was missing.

## Minimal streaming inference

```powershell
python -m signin_chatgpt_spike.cli infer
```

The fixed prompt asks `gpt-6-luna` to return `SIGNIN_CHATGPT_SPIKE_OK`; the same model is used when `--model` is omitted. This command does not accept the verification fallback. It stops if the current catalog does not contain the exact slug.

Requests go only to the public `/v1/responses` endpoint and contain `model`, local `input`, `store: false`, `stream: true`, and—when applicable—the declared namespace function tools. The stream is successful only after `response.completed`.

The client records event types, final response ID/model, usage counts, request ID, and a small allowlist of numeric rate-limit headers. It never stores or prints raw response bodies or authorization headers. Plan-level quota/accounting is `UNKNOWN` unless OpenAI exposes it directly.

## Tool loop demo

```powershell
python -m signin_chatgpt_spike.cli demo-tools
```

For a verification-only run of the ordered two-tool loop on an account-visible `gpt-5.6-luna`, explicitly select that slug and write sanitized evidence:

```powershell
python -m signin_chatgpt_spike.cli demo-tools --model gpt-5.6-luna --evidence evidence/gpt-5.6-luna-tool-loop.json
```

This command performs model discovery and one ordered tool loop. If `gpt-5.6-luna` is absent, it records `BLOCKED_VERIFICATION_MODEL_UNAVAILABLE` and sends no Responses request. The formal/default model remains `gpt-6-luna`; this fallback is limited to protocol verification.

The static `TOOLS` mapping contains exactly:

- `research_context_get` returns a fixed mock research question and `SPIKE_CONTEXT_OK` status.
- `research_budget_get` returns a fixed mock budget of three iterations.

Both accept only `{}`. The tool loop appends completed output items and function outputs to a small process-local input list. Each call ID is executed at most once. An unknown function, malformed arguments, duplicate call ID, or unsupported output item stops the loop without a generic executor fallback.

A preview response may emit a completed function-call item through `response.output_item.done` even when that item is absent from the final completed response body. The client therefore captures completed output items from the stream and supplements the completed response output before local dispatch.

## Capability boundary

The model receives only Responses inference and these two local functions:

```text
research_context_get
research_budget_get
```

No model tool is provided for shell, PowerShell, `cmd.exe`, filesystem access, arbitrary Python, raw TCP, generic HTTP, browser or Computer Use, generic MCP, Git/GitHub, databases, Broker, protected evaluation data, arbitrary connectors, or sub-agents.

The dispatcher uses exact static name lookup; it has no `eval`, `exec`, reflection, dynamic imports, or shell fallback.

The purpose is to demonstrate a narrow capability boundary: do not merely instruct the model not to use a capability; omit that capability from the harness entirely.

## Offline verification

```powershell
python -m pytest
ruff check .
ruff format --check .
git diff --check
python -m signin_chatgpt_spike.cli --help
python -m signin_chatgpt_spike.cli auth status
```

These commands do not send Responses API requests. The unit suite mocks HTTP and uses synthetic credentials marked `EXAMPLE_ONLY_NOT_A_REAL_...`; real requests are reachable only through explicit CLI commands after sign-in.

Final offline verification passed **86 tests**. The focused auth suite passed **16 tests**. Ruff check, Ruff format check, and diff check passed.

## Explicit live smoke

After `auth login`, this command discovers the signed-in account's model catalog and exercises the formal/default `gpt-6-luna` path:

```powershell
python -m signin_chatgpt_spike.cli smoke --evidence evidence/smoke-result.json
```

If the exact `gpt-6-luna` slug is absent, the current implementation stops before inference and tool execution. It does not silently fall back to another model.

An earlier exploratory version did select `models[0].slug`; that produced a historical smoke on `gpt-6-astra`. The corresponding evidence is intentionally retained as observed historical evidence and is not rewritten. The selection bug was fixed so catalog order can no longer choose the runtime model.

## Live verification results

### 1. Initial exploratory live smoke

See [`evidence/smoke-result.json`](evidence/smoke-result.json).

The initial implementation successfully completed browser authentication, model discovery, and simple streaming inference. Because the old selection logic used the first visible catalog entry, that historical run used `gpt-6-astra`.

The simple inference returned `SIGNIN_CHATGPT_SPIKE_OK`.

The smoke also exposed a real tool-loop bug: the stream contained function-call events, but the implementation looked only at the final response body for dispatchable function-call items. The fix was to preserve completed items from `response.output_item.done` and merge them into the effective completed output.

The historical evidence remains unchanged.

### 2. GPT-6 Luna availability check

See [`evidence/gpt-6-luna-tool-loop.json`](evidence/gpt-6-luna-tool-loop.json).

Browser sign-in succeeded, but the exact `gpt-6-luna` slug was absent from the authenticated account's raw model catalog.

Result:

```text
BLOCKED_MODEL_UNAVAILABLE
```

This check made:

- Responses requests: 0
- local tool executions: 0

Logout and remote refresh-token revocation succeeded afterward.

This result is account- and time-specific. It is not a claim that `gpt-6-luna` is unavailable through every OpenAI product or account.

### 3. GPT-5.6 Luna ordered two-tool live verification

See [`evidence/gpt-5.6-luna-tool-loop.json`](evidence/gpt-5.6-luna-tool-loop.json).

The explicit verification model `gpt-5.6-luna` was present in the account-visible catalog.

One ordered tool loop completed successfully:

```text
Responses request 1
  -> research_context_get({})
  -> local execution
  -> function_call_output

Responses request 2
  -> research_budget_get({})
  -> local execution
  -> function_call_output

Responses request 3
  -> final response
```

Observed result:

- all 3 Responses requests used `gpt-5.6-luna`
- `research_context_get({})`: exactly once
- `research_budget_get({})`: exactly once
- tool order: correct
- duplicate call IDs executed: 0
- final `response.completed`: present
- total input tokens: 411
- total output tokens: 86
- total tokens: 497
- elapsed time: 12.459 seconds

The stream included `response.output_item.added`, function-call argument delta/done events, `response.output_item.done`, content/text deltas, and `response.completed`.

Harness viability after this check: **PROCEED**.

GPT-6 Luna availability for this account at verification time: **BLOCKED_MODEL_UNAVAILABLE**.

### 4. Live refresh-token rotation verification

The first live refresh attempt is retained in [`evidence/live-refresh-rotation.json`](evidence/live-refresh-rotation.json).

That attempt ended before refresh because the authorization-code token exchange timed out. No token set was produced and no refresh grant was attempted, so this is not evidence of a refresh failure.

The successful retry is recorded in [`evidence/live-refresh-rotation-retry-1.json`](evidence/live-refresh-rotation-retry-1.json).

The successful live check confirmed:

- one normal browser login established a renewable session
- the expiry condition was forced only in memory; persisted token state was not modified before refresh
- `access_token()` triggered exactly one real refresh grant
- browser reauthentication during refresh: 0
- access token rotated
- refresh token rotated
- ID token rotated
- required scopes, including `offline_access`, were retained
- the new TokenSet was persisted through DPAPI
- the refreshed access token successfully called `GET /v1/models`
- an immediate second `access_token()` call did not trigger another refresh
- logout revoked the latest persisted refresh token and removed the local token set
- host ID and registration metadata were intentionally retained

Result:

```text
LIVE_REFRESH_VERIFIED
```

## Final spike status

The following behaviors were verified against the live service on 2026-09-30:

| Capability | Result |
| --- | --- |
| Sign in with ChatGPT | PASS |
| API key not required for this route | PASS |
| ChatGPT plan usage authorization | PASS |
| `GET /v1/models` | PASS |
| Raw model catalog inspection | PASS |
| Streaming Responses | PASS |
| `store:false` | PASS |
| Local function calling | PASS |
| Function-output continuation | PASS |
| Ordered two-tool loop | PASS |
| Duplicate local tool execution | 0 |
| Access-token refresh | PASS |
| Access-token rotation | PASS |
| Refresh-token rotation | PASS |
| Refreshed-token persistence | PASS |
| Refresh without browser reauthentication | PASS |
| Logout / remote revoke | PASS |
| GPT-5.6 Luna verification path | PASS |
| GPT-6 Luna on this account at this time | BLOCKED_MODEL_UNAVAILABLE |

Final technical-spike decision: **PROCEED**.

The spike demonstrates that Sign in with ChatGPT plus the Responses API can support a small self-hosted harness with client-managed context and a narrow local function allowlist.

## Errors and preview limitations

Safe error categories are `authentication`, `transport`, `rate_limit`, `model`, `tool_validation`, `tool_execution`, `timeout`, and `unknown`. CLI errors never include upstream response bodies or authorization data.

The current ChatGPT plan-usage preview requires `store: false`, `stream: true`, and a complete local `input` history on each HTTP request. This spike omits `previous_response_id` and unsupported top-level fields including `background`, `conversation`, `max_output_tokens`, `max_tool_calls`, `metadata`, `moderation`, `multi_agent`, `prompt`, `prompt_cache_retention`, `safety_identifier`, `temperature`, `top_logprobs`, `top_p`, `truncation`, and `user`.

The preview does not provide feature parity with the normal metered Responses API. In particular, current preview limitations include unsupported built-in capabilities such as Code Interpreter, image generation, file search, native Computer Use, hosted MCP/connectors, and related features documented by OpenAI.

ChatGPT plan usage should not be treated as access to every OpenAI API endpoint. The current open-source Sign in with ChatGPT documentation describes eligible Responses API requests; dedicated APIs such as TTS, transcription, or Images should not be assumed to use the same plan-usage route unless OpenAI explicitly documents that support.

The preview and available models can change. Check the current official documentation before reproducing the experiment.

## Deliberately out of scope

This spike has no:

- automatic production retry policy
- persistent conversation database
- generic tool sandbox
- multi-account UI
- cross-process refresh coordination
- production credential lifecycle
- large-history management
- production observability
- state-changing tool idempotency framework

Those concerns belong in the real runtime that adopts this pattern rather than in this small reference implementation.

## Why this exists

The purpose of this repository is to verify the technical building blocks behind a restricted Responses-based runtime:

- user authentication through Sign in with ChatGPT
- ChatGPT plan usage
- client-controlled context
- explicit model selection
- streaming Responses
- explicit local function tools
- renewable OAuth sessions
- a narrow capability boundary

It is intended as a readable reference and as the source code/evidence companion to the note article linked above.

## Official references

- [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source)
- [Sign in with ChatGPT: registration and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Sign in with ChatGPT: models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Sign in with ChatGPT: preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Responses API function calling and streaming events](https://developers.openai.com/api/docs/guides/function-calling)
- [Microsoft DPAPI: CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- [Microsoft Credential Manager: CREDENTIALW limits](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
