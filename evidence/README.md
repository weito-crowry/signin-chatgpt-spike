# Public evidence

`smoke-result.json` is created by the explicit `smoke --evidence evidence/smoke-result.json` command after live checks. The generator uses a fixed field allowlist and stores only sanitized metadata: environment versions, Git SHA, account-visible model IDs, request/response IDs, event names, usage counts, selected rate-limit values, tool order, elapsed time, capability names, and limitations.

`auth-attempt.json` records a live sign-in attempt that stopped before any model-list or Responses inference request. It contains only coarse stage results, environment versions, the code revision under evaluation, and the capability allowlist. It includes no registration IDs, subject digests, token lengths, auth response fields, or browser details.

It does not store OAuth tokens, authorization codes, account identifiers, request headers, cookies, raw authentication/API payloads, or model response text. Unit tests and fake credentials are not live evidence. Unknown observations remain `UNKNOWN`.
