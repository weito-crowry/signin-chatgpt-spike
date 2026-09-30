# Sign in with ChatGPT Spike Implementation Plan

> **For agentic workers:** Use the native execution method in this session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a small Windows-first Python reference implementation that signs in with ChatGPT, discovers account-eligible models, streams Responses API results, and executes only two explicit local research functions.

**Architecture:** Use the official open-source Sign in with ChatGPT OAuth flow with loopback PKCE, ID-token validation, refresh/revocation, Windows Credential Manager for small registration/host metadata, and a current-user DPAPI-encrypted Local AppData file for the token set. Windows Credential Manager has a 2,560-byte credential blob limit, so it cannot reliably hold the combined token JSON. Use direct HTTPX calls for model discovery and streamed Responses so request fields, SSE parsing, error classification, and the local allowlist remain visible. Keep stateless conversation items in a process-local list and send `store: false` plus `stream: true` on every inference request.

**Tech Stack:** Python 3.11+, HTTPX, PyJWT with cryptography support, `keyring` Windows Credential Manager backend, built-in `ctypes` wrappers for Windows DPAPI, pytest, and Ruff.

**Spec:** User-provided implementation brief in this task (sections 0–36, received 2026-09-30).

## Global Constraints

- The repository is public; never persist a credential in the repository or print/log tokens, auth codes, callback URLs, authorization headers, cookies, or raw auth payloads.
- Tests use only clearly synthetic fixtures such as `EXAMPLE_ONLY_NOT_A_REAL_TOKEN`; no live credential is ever a fixture.
- Create `.gitignore` before writing credential-handling code; include all specified cache, secret, credential, and private-evidence patterns.
- Support one locally selected ChatGPT registration in this small spike; do not build an account-picker framework.
- Keep FX-LLM, Broker, databases, shell, filesystem tools, generic network tools, browser tools, MCP, sub-agents, and arbitrary execution outside the model capability set.
- Expose only `research_context_get` and `research_budget_get` as local functions, through a Responses function namespace supported by the current plan-usage route.
- Every Responses request uses the public `/v1/responses` endpoint, `store: false`, `stream: true`, and locally supplied `input`; omit `previous_response_id` and unsupported preview fields.
- Keep real API calls behind an explicit `smoke` command. Default pytest uses only local fixtures and fake credentials.
- Preserve UNKNOWN for unavailable plan-usage accounting or unrun live checks; never present mock results as live evidence.
- Classify failures as `authentication`, `transport`, `rate_limit`, `model`, `tool_validation`, `tool_execution`, `timeout`, or `unknown`; never print upstream exception/body text.
- Do not create a worktree, merge, rewrite history, add a tag, or release. Push the finished review branch only after repository hygiene checks.

## Review Focus

- OAuth callback with mismatched state, missing code, denial, or wrong issued client ID must stop before exchanging credentials.
- Invalid, expired, wrong-audience, or wrong-nonce ID tokens and missing `chatgpt.tokens.use.direct` scope must not activate inference.
- Malformed SSE, response failure/incomplete status, timeout, or a stream ending before `response.completed` must never be reported as success.
- Duplicate call IDs with changed content and repeated identical call IDs must not execute a local function twice.
- Unknown names, extra arguments, malformed JSON, and non-object arguments must fail before tool execution or any subprocess, filesystem, or external-network side effect.

---

### Task 1: Seed the safe Python project surface

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `LICENSE`
- Create: `README.md`
- Create: `src/signin_chatgpt_spike/__init__.py`
- Create: `tests/`

**Interfaces:**
- Produces: installable `signin-chatgpt-spike` package; `signin-chatgpt-spike` console entry point will target `signin_chatgpt_spike.cli:main` in Task 5.

- [x] **Step 1: Write `.gitignore` first** with `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.env`, `.env.*`, `!.env.example`, `*.token`, `*.tokens`, `*.secret`, `*.secrets`, `credentials/`, `secrets/`, `private/`, `local_state/`, `auth_state/`, and `evidence/private/`; do this before any auth or credential code.
- [x] **Step 2: Add minimal package metadata** for Python 3.11+, runtime dependencies `httpx`, `PyJWT[crypto]`, and `keyring`, plus `pytest` and `ruff` as development extras.
- [x] **Step 3: Add the MIT license and a README skeleton** identifying the spike, its non-goals, the two-tool capability boundary, and that real API checks are opt-in.
- [x] **Step 4: Verify project setup** with `python -m pip install -e ".[test]"`, `python -c "import signin_chatgpt_spike"`, and `ruff check .`; defer pytest until Task 2 adds the first behavior tests, since an empty test suite exits as “no tests collected.” No credential or real API action is part of this task.

### Task 2: Implement official OAuth and protected credential lifecycle

**Files:**
- Create: `src/signin_chatgpt_spike/auth.py`
- Create: `src/signin_chatgpt_spike/credentials.py`
- Create: `src/signin_chatgpt_spike/host_identity.py`
- Create: `src/signin_chatgpt_spike/models.py`
- Test: `tests/test_auth.py`
- Test: `tests/test_credentials.py`
- Test: `tests/test_host_identity.py`

**Interfaces:**
- Produces: `HostIdentityStore.get_or_create_host_id() -> str`, persisted independently from account registration and tokens.
- Produces: `RegistrationStore.load() -> RegistrationIdentity | None` and `RegistrationStore.save(identity: RegistrationIdentity) -> None`.
- Produces: `TokenStore.load() -> TokenSet | None`, `TokenStore.save(tokens: TokenSet) -> None`, and `TokenStore.delete() -> None`.
- Produces: `AuthManager.login() -> AuthStatus`, `AuthManager.status() -> AuthStatus`, `AuthManager.access_token() -> str`, and `AuthManager.logout() -> LogoutStatus`.
- `RegistrationIdentity` stores only the issued client ID and a stable digest of the validated subject needed to reject account/registration mixups; it does not contain the host ID or any tokens.
- `TokenSet` stores access/refresh/ID tokens, granted scopes, and expiry; its repr must never reveal token values.

- [x] **Step 1: Write failing tests** for PKCE S256/state/nonce construction, callback state and client-ID binding, verified issuer/audience/nonce/expiry, identity-only success without plan scope (no inference), token refresh rotation, expiry metadata, independent host-ID creation/persistence, registration persistence, token-only deletion on logout, protected large-token persistence without plaintext files, and redacted repr/status.
- [x] **Step 2: Run the targeted tests** and confirm they fail on missing behavior, using fake HTTP responses and a fake credential store.
- [x] **Step 3: Implement the loopback flow** on `127.0.0.1` with official authorize/token endpoints, `dynamic_agent_client` only for first registration, `agent_name_hint` only on registration, stable `ext_agent_host_id`, `resource=https://api.openai.com/v1`, scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`, callback state/client-ID validation, PKCE S256, and OpenAI JWKS ID-token validation against issuer, audience, expiry, and nonce.
- [x] **Step 4: Implement three independent protected stores**: keep the small stable `urn:uuid:` host ID and registration identity (client ID plus subject digest) in Windows Credential Manager; save the token set as a current-user DPAPI-protected blob under Local AppData because Credential Manager credential blobs are limited to 2,560 bytes; create/persist the host ID before any OAuth browser flow; refresh near expiry and save rotated tokens together; logout attempts refresh-token revocation and deletes access/refresh/ID tokens and expiry only, while retaining registration identity and host ID even when remote revocation is unconfirmed.
- [x] **Step 5: Make tests pass** and verify failures and status output contain only safe categories/metadata.

### Task 3: Add account model discovery and a safe streamed Responses client

**Files:**
- Modify: `src/signin_chatgpt_spike/models.py`
- Create: `src/signin_chatgpt_spike/client.py`
- Create: `src/signin_chatgpt_spike/errors.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `ModelInfo(slug: str, display_name: str)` and `ResponsesClient.list_models(access_token: str) -> list[ModelInfo]`.
- Produces: `ResponsesClient.stream_response(access_token: str, model: str, input_items: list[dict], tools: list[dict] | None = None) -> ResponseSummary`.
- `ResponseSummary` contains only parsed response ID/model/text/output items/usage/safe rate-limit metadata/event types; it does not retain headers or raw response bodies.

- [x] **Step 1: Write failing tests** for `/v1/models` account-catalog filtering (`visibility == "list"`), exact slug/display name, SSE deltas/completion/usage/response ID, malformed events, `response.failed`, `response.incomplete`, HTTP status classification, timeout, and stream interruption. Capture the outbound Responses request body and assert `store` is false, `stream` is true, and the body omits `previous_response_id`, `conversation`, `max_output_tokens`, `temperature`, `metadata`, and `user` (also omit the other fields listed as unsupported by the current preview route).
- [x] **Step 2: Run the targeted tests** and confirm failures are due to missing client behavior.
- [x] **Step 3: Implement HTTPX requests** to only `https://api.openai.com/v1/models` and `/responses`; send bearer authorization without logging it, disable retries, classify errors without printing upstream bodies, and parse SSE explicitly through `response.completed`.
- [x] **Step 3a: Allowlist response metadata** to request ID, known `x-ratelimit-*` header values, response ID/model, usage counts, and event types; report ChatGPT plan usage/accounting as `UNKNOWN` unless the service directly returns it.
- [x] **Step 4: Use the supported plan-usage request contract:** `store: false`, `stream: true`, full local `input`, and function tools grouped in a namespace; omit `previous_response_id` and fields excluded by current preview documentation.
- [x] **Step 5: Make the targeted tests pass** and check that status/error summaries expose no request headers, token payloads, or raw API messages.

### Task 4: Build the explicit local allowlist and stateless tool loop

**Files:**
- Create: `src/signin_chatgpt_spike/tools.py`
- Create: `src/signin_chatgpt_spike/tool_loop.py`
- Test: `tests/test_tools.py`
- Test: `tests/test_tool_loop.py`

**Interfaces:**
- Produces: `TOOLS = {"research_context_get": research_context_get, "research_budget_get": research_budget_get}`.
- Produces: `dispatch_tool(name: str, arguments_json: str) -> str` with exact empty-object validation and deterministic unknown-tool rejection.
- Produces: `run_tool_loop(client: ResponsesClient, access_token: str, model: str, prompt: str, max_steps: int = 6) -> ToolLoopResult`.

- [x] **Step 1: Write failing tests** for both exact mock outputs, unknown names (`shell_execute`, `broker_direct_connect`, `filesystem_read`, `http_request`), invalid JSON/extra arguments, one-call and two-step sequences in context-then-budget order, local history item ordering, final text, malformed output items, and duplicate-call idempotence.
- [x] **Step 2: Run the targeted tests** and verify unknown-tool cases fail before any dispatcher function or external side effect runs.
- [x] **Step 3: Implement only the two static functions and visible `TOOLS` mapping**; do not use reflection, `eval`, `exec`, imports from model text, shells, generic HTTP, files, MCP, or other tools.
- [x] **Step 4: Implement continuation** by appending completed output items and `function_call_output` items to a process-local input list, preserving the namespace/tool history required by the preview route; execute each call ID at most once and stop on unsupported items or step limit.
- [x] **Step 5: Make the targeted tests pass** and verify tool order, exact serialized results, and final response from recorded fake stream fixtures.

### Task 5: Add explicit CLI commands and sanitized evidence output

**Files:**
- Create: `src/signin_chatgpt_spike/cli.py`
- Create: `src/signin_chatgpt_spike/evidence.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Produces: `auth login`, `auth status`, `auth logout`, `models`, `infer`, `demo-tools`, and explicit `smoke` commands.
- Produces: `build_evidence(summary: SmokeSummary) -> dict[str, object]` containing only allowlisted public fields, including sanitized request/response IDs.

- [x] **Step 1: Write failing tests** for command routing, explicit opt-in to real API calls, safe auth/status summaries, typed error categories, fixed simple prompt result, and evidence redaction/field allowlisting.
- [x] **Step 2: Run the targeted tests** and confirm no command displays account IDs, callback query values, bearer tokens, or raw auth/API payloads.
- [x] **Step 3: Implement concise CLI output** for authentication metadata, model listing, streaming text, tool summaries, usage and latency; leave plan-level usage accounting as `UNKNOWN` unless directly observable.
- [x] **Step 4: Implement sanitized evidence** with timestamp, OS/Python/Git SHA, authentication state, model-list summary, selected model, safe request/response IDs, stream event types, tool sequence, final-result status, token usage, elapsed time, unknown-tool denial, capability list, and limitations; never persist raw model output, personal account metadata, or credentials.
- [x] **Step 5: Make the targeted tests pass** and verify evidence rejects unrecognized fields.

### Task 6: Complete the public reference documentation and live smoke

**Files:**
- Modify: `README.md`
- Create: `evidence/README.md`
- Create: `evidence/auth-attempt.json` sanitized auth-stage result; create `smoke-result.json` only after inference checks complete
- Test: `tests/` (existing offline unit suite)

- [x] **Step 1: Finish README** with architecture, Windows/Python/browser requirements, clone-to-venv commands, official sign-in, independent host identity and registration storage, token-only logout/revocation, model discovery (including exact discovered IDs and GPT-6 Luna availability when observable), simple inference, one/multi-tool demo, capability boundary, error classes, preview constraints, known limitations, measured results, final `PROCEED` / `PROCEED_WITH_GAPS` / `BLOCKED` decision, and reproduction commands. Exact live model IDs remain `UNKNOWN` because sign-in did not persist.
- [x] **Step 2: Run offline verification** with `python -m pytest`, `ruff check .`, and every README setup/demo command that does not require account consent; confirm default tests cannot issue real API calls.
- [x] **Step 3: Attempt only the explicit minimum live checks** after `auth login`; the browser callback timed out after token storage was corrected, so model discovery and Responses requests were not run. Record those observations as `NOT_RUN`/`UNKNOWN`; unknown-tool denial is covered by deterministic offline tests and spent no inference request.
- [x] **Step 4: Review secret safety** with `git status`, `git diff`, `git diff --cached`, searches for bearer/token/auth-code patterns, and inspection of all tracked/staged files; confirm OS credentials, `.env`, caches, and private evidence are untracked/ignored. Three credential-shaped scanner matches were all synthetic fixtures with the explicit `EXAMPLE_ONLY_NOT_A_REAL_TOKEN` marker; diagnostic OS credential entries were removed.
- [ ] **Step 5: Make the initial public commit** after all required checks, push `spike/reference-implementation` to `origin`, verify remote SHA, and stop without merge, PR creation, tag, or release.

## Self-Review

- Coverage: OAuth/token lifecycle is Task 2; model discovery/SSE/errors/usage are Task 3; allowlist/tool continuation/unknown denial are Task 4; CLI/evidence are Task 5; reproduction/live checks/Git hygiene/publication are Task 6.
- Preview contract: local stateless input with `store: false` and `stream: true`, no HTTP `previous_response_id`, and namespace-grouped functions are explicit.
- Type flow: Task 2 defines `RegistrationIdentity`, `TokenSet`, and `AuthStatus`; Task 3 defines `ResponseSummary`; Task 4 consumes `ResponsesClient` and defines `ToolLoopResult`; Task 5 consumes these summaries for CLI/evidence.
- User-approved lifecycle additions: `HostIdentityStore`, `RegistrationStore`, and `TokenStore` are separate interfaces; logout deletes only tokens/expiry and keeps host/registration metadata.
- Storage adjustment from the live login: an OAuth attempt returned a protected credential write failure. A separate fake-value size probe showed this Credential Manager backend's practical ceiling at about 1,280 characters, consistent with Microsoft's 2,560-byte credential blob limit. Keep host/registration metadata in Credential Manager and move only the token set to a current-user DPAPI-protected Local AppData file; offline roundtrip, no-plaintext-file, and token-only-delete coverage pass. Do not record token sizes or token contents in evidence.
- Live result: sign-in did not leave a local token set; the post-fix callback timed out. Local logout confirmed token absence while retaining host and registration metadata; remote revocation remains `UNKNOWN`. No model-list or Responses requests were sent, so final decision is `BLOCKED` until live smoke completes.
- User-approved request-contract test explicitly checks both required `store`/`stream` values and absence of preview-unsupported body fields.
- Review Focus cases are assigned to the auth, client, and tool-loop tests in the owning tasks.
- Evidence and README results do not store a final model-output transcript; public metadata remains sufficient for reproduction without exposing account data.
- The plan describes files and observable assertions without embedding implementation bodies; the project remains a single integrated runtime flow.
