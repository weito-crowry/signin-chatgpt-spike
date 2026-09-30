# Sign in with ChatGPT Spike

A small Windows-first Python reference implementation that signs in to ChatGPT, discovers the signed-in account's available models, streams Responses API output, and executes two explicitly allowlisted local functions.

## What this is

This is a technical spike for the official Sign in with ChatGPT flow and ChatGPT plan usage route. It shows the OAuth/token lifecycle, direct HTTPX requests, streaming event assembly, local function dispatch, and client-managed tool continuation in code that can be read end to end.

## What this is not

- A production agent framework, SDK, or Codex clone.
- A generic shell, filesystem, network, browser, or MCP agent.
- An FX-LLM integration or a production research runtime.

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

The access token, refresh token, ID token, scopes, and expiry are stored together in `%LOCALAPPDATA%\signin-chatgpt-spike\tokens.dpapi`, protected by Windows DPAPI for the current user. The file is outside the repository and contains only the DPAPI-protected blob. This uses the current user's Windows logon protection and cannot be decrypted by another Windows user. Credential Manager is used only for the small identity records because its generic credential blob limit is 2,560 bytes.

`auth logout` attempts refresh-token revocation and deletes the complete local token file even if remote revocation cannot be confirmed. It preserves the host ID and registration metadata. Run `auth login` to sign in again with that registration. Because logout removes the ID token, reauthentication may show the ChatGPT account selector instead of using `id_token_hint`. To intentionally change the ChatGPT account, remove only the `registration` entry in Windows Credential Manager before signing in again; keep `host-id`.

Never paste an authorization code, token, callback URL, cookie, or authorization header into source files, logs, README text, issues, screenshots, or evidence.

## Model discovery

List the models exposed to the signed-in account:

```powershell
python -m signin_chatgpt_spike.cli models
```

The client calls `GET https://api.openai.com/v1/models`, keeps entries with `visibility == "list"`, and preserves the returned order for display. The formal/default target is `gpt-6-luna`; an omitted model always requests that exact slug and fails closed if it is absent. Catalog order never selects a model. The explicit `gpt-5.6-luna` exception is restricted to the ordered `demo-tools` verification path; it is a spike verification model, not a replacement default or an FX-LLM adoption decision. All other model IDs are rejected.

## Minimal streaming inference

```powershell
python -m signin_chatgpt_spike.cli infer
```

The fixed prompt asks `gpt-6-luna` to return `SIGNIN_CHATGPT_SPIKE_OK`; the same model is used when `--model` is omitted. You may pass `--model "gpt-6-luna"` explicitly. This command does not accept the verification fallback. It stops if the current catalog does not contain the exact slug. Requests go only to the public `/v1/responses` endpoint and contain `model`, local `input`, `store: false`, `stream: true`, and—when applicable—the declared namespace function tools. The stream is successful only after `response.completed`.

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

## Capability boundary

The model receives only Responses inference and these two local functions:

```text
research_context_get
research_budget_get
```

No model tool is provided for shell, PowerShell, `cmd.exe`, filesystem access, arbitrary Python, raw TCP, generic HTTP, browser or Computer Use, generic MCP, Git/GitHub, databases, Broker, Protected OOS, arbitrary connectors, or sub-agents. The dispatcher uses exact static name lookup; it has no `eval`, `exec`, reflection, dynamic imports, or shell fallback.

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

## Explicit live smoke

After `auth login`, run this one command to discover the signed-in account's model catalog, send one fixed streaming inference, exercise one single-tool continuation, and exercise the ordered two-tool loop using `gpt-6-luna`. Omitting `--model` still uses that fixed model; if its exact slug is absent, smoke stops before inference and tool execution with no fallback. `--evidence` writes a sanitized public JSON summary and never includes final response text or credentials.

```powershell
python -m signin_chatgpt_spike.cli smoke --evidence evidence/smoke-result.json
```

When `gpt-6-luna` is present, this smoke sequence makes six streamed Responses requests in the expected path: one simple request, two requests for the single-tool continuation, and three requests for the two-tool continuation. It makes one model-list request. Unknown-tool denial is checked locally and does not spend an inference request. No retries are performed. `auth logout` can be run afterward to revoke and clear the local token set. Add `--model "gpt-6-luna"` only after confirming that slug appears in the `models` output.

The smoke command returns a nonzero exit code if either tool loop does not complete and uses the formal/default model only. A model may emit tool-call events while a preview response omits items from the completed response body; the client also reads `response.output_item.done` so these completed calls are available to the local dispatcher. A failed smoke is recorded as observed and is not retried automatically.

## Errors and preview limitations

Safe error categories are `authentication`, `transport`, `rate_limit`, `model`, `tool_validation`, `tool_execution`, `timeout`, and `unknown`. CLI errors never include upstream response bodies or authorization data.

The current ChatGPT plan-usage preview requires `store: false`, `stream: true`, and a complete local `input` history on each HTTP request. This spike omits `previous_response_id` and unsupported top-level fields including `background`, `conversation`, `max_output_tokens`, `max_tool_calls`, `metadata`, `moderation`, `multi_agent`, `prompt`, `prompt_cache_retention`, `safety_identifier`, `temperature`, `top_logprobs`, `top_p`, `truncation`, and `user`. Function tools are grouped in a `research` namespace. The preview and available models can change; check the current official documentation before reproducing the experiment.

This spike has no automatic retry policy, persistent conversation database, tool sandbox, multi-account UI, cross-process refresh coordination, or production credential lifecycle. The two local tools are fixed mocks. Windows Credential Manager and DPAPI depend on the current Windows user profile. Live model and usage results can differ by account and date.

## Results

Local verification on 2026-09-30 used Windows 10 and Python 3.11.9. The offline suite passed 55 tests, including OAuth validation with fake responses, DPAPI roundtrip with a fake payload, the explicit tool allowlist, denial of unknown tools, Responses request-contract checks, and regressions for streamed completed output items. Ruff checks passed.

The browser sign-in succeeded. The CLI reported `authenticated: true`, `refresh_available: true`, and `plan_usage_enabled: true`; it printed no token values. Logout then removed the token set and remote refresh-token revocation was confirmed. Stable host identity and registration metadata remain stored.

Model discovery succeeded and returned these account-visible IDs: `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and `gpt-5.5`. `gpt-6-luna` was not listed. The smoke selected `gpt-6-astra`. Its fixed inference returned `SIGNIN_CHATGPT_SPIKE_OK` over streaming. The simple response used 27 tokens (16 input, 11 output); the smoke total across three Responses requests was 209 tokens. Response IDs were captured in the sanitized evidence, while request ID and rate-limit metadata were not available.

That earlier exploratory smoke selected the first visible model, `gpt-6-astra`. Its evidence remains historical and unchanged. Current live selection ignores catalog order and is pinned to `gpt-6-luna`.

The live smoke observed function-call argument stream events, but the completed response body did not deliver a function-call item to the local tool loop. Therefore neither local tool was dispatched and both tool-loop checks failed. The client now supplements the completed body's output with final items from `response.output_item.done`, following the documented streaming event contract. The new offline regression passed, but the live smoke was not repeated; live single-tool continuation and the ordered two-tool loop remain unverified. The unknown-tool denial was a local allowlist check and passed. See the sanitized live record at [`evidence/smoke-result.json`](evidence/smoke-result.json). The earlier authentication attempts are retained in [`evidence/auth-attempt.json`](evidence/auth-attempt.json).

Plan usage accounting and rate-limit metadata remain `UNKNOWN` because this smoke did not expose those values. The account-visible catalog did not include `gpt-6-luna`.

Final runtime decision: **PROCEED_WITH_GAPS**. Authentication, discovery, inference, and streaming worked. Before considering FX-LLM adoption, perform a separately authorized live smoke against the event-item fix, confirm single and ordered multi-tool continuation, and assess the preview route's stability and usage limits.

### GPT-6 Luna live tool-loop recheck (2026-09-30)

Sign-in succeeded with refresh and plan usage enabled, but the account-visible catalog did not contain the exact `gpt-6-luna` slug. Per the stop rule, this run made zero Responses requests and executed zero local tools; its result is **BLOCKED_MODEL_UNAVAILABLE**. Logout removed the local token set, remote refresh-token revocation was confirmed, host ID and registration metadata remain present, and the final auth status is unauthenticated. The ordered live tool loop remains unverified. See the separate sanitized record at [`evidence/gpt-6-luna-tool-loop.json`](evidence/gpt-6-luna-tool-loop.json).

### GPT-5.6 Luna live ordered tool-loop verification (2026-09-30)

The account-visible catalog contained `gpt-5.6-luna` and still did not contain `gpt-6-luna`. An explicit `--model gpt-5.6-luna` ran the ordered tool loop once: all three Responses requests used that model, `research_context_get({})` ran once and continued successfully, then `research_budget_get({})` ran once and continued successfully. The final request reached `response.completed` with a final response. The stream included `response.output_item.added`, function-call argument delta and done events, and `response.output_item.done`; the completed function-call items reached the dispatcher in the required order. No duplicate executions occurred. The run used 497 tokens and took 12.459 seconds. Sanitized response IDs and event types are in [`evidence/gpt-5.6-luna-tool-loop.json`](evidence/gpt-5.6-luna-tool-loop.json).

The formal/default target remains `gpt-6-luna`; `gpt-5.6-luna` is allowed only as an explicit spike verification model. This result does not adopt GPT-5.6 Luna for FX-LLM. Logout removed the local token file, remote refresh-token revocation was confirmed, host ID and registration metadata remained present, and final auth status is unauthenticated.

Harness viability: **PROCEED**. GPT-6 Luna availability: **BLOCKED_MODEL_UNAVAILABLE**.

### Live refresh rotation verification (2026-09-30)

A single-session live check confirmed that access-token expiry triggers the refresh grant without browser sign-in, both access and refresh tokens rotate, the new token set is persisted through DPAPI, and the refreshed access token retrieves a non-empty account model catalog. The expiry condition was forced only in an in-memory wrapper; the persisted expiry was not changed. Multi-process refresh serialization was outside this check and is planned for verification in FX-LLM. See the sanitized record at [`evidence/live-refresh-rotation-retry-1.json`](evidence/live-refresh-rotation-retry-1.json).

## Why this exists

This experiment informs whether a restricted Responses-based research runtime is worth designing separately for FX-LLM. It is not intended to be copied directly into that production codebase.

## Official references

- [Sign in with ChatGPT: registration and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Sign in with ChatGPT: models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Sign in with ChatGPT: preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Responses API function calling and streaming events](https://developers.openai.com/api/docs/guides/function-calling)
- [Microsoft DPAPI: CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- [Microsoft Credential Manager: CREDENTIALW limits](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
