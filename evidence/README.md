# Public evidence

`smoke-result.json` is created by the explicit `smoke --evidence evidence/smoke-result.json` command after live checks. The generator uses a fixed field allowlist and stores only sanitized metadata: environment versions, Git SHA, account-visible model IDs, request/response IDs, event names, usage counts, selected rate-limit values, tool order, elapsed time, capability names, and limitations.

`auth-attempt.json` records a live sign-in attempt that stopped before any model-list or Responses inference request. It contains only coarse stage results, environment versions, the code revision under evaluation, and the capability allowlist. It includes no registration IDs, subject digests, token lengths, auth response fields, or browser details.

`smoke-result.json` records a later successful sign-in and model discovery, a successful streamed text response, and a failed live tool-loop attempt. Function-call argument event types were observed, but no local tool call was dispatched in that run. Its result describes only the requests and outcomes actually observed; the offline event-assembly regression is not live smoke evidence.

`gpt-5.6-luna-tool-loop.json` is written only by the explicit `demo-tools --model gpt-5.6-luna --evidence ...` verification path. It records the account-visible catalog, requested model for each Responses call, response and request IDs, allowlisted stream event types, tool-call order and execution counts, empty-argument checks, continuation outcomes, usage, elapsed time, and the fixed capability boundary. The live record also includes safe logout results and confirms the final unauthenticated state. It omits all response text and credential material; failures are recorded without automatic retry.

It does not store OAuth tokens, authorization codes, account identifiers, request headers, cookies, raw authentication/API payloads, or model response text. Unit tests and fake credentials are not live evidence. Unknown observations remain `UNKNOWN`.
