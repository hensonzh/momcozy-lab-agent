# Runtime Authentication

## Public Agent API

- Product Backend is the only RS256 private-key owner and issues one access
  token with `momcozy-product-api` and `momcozy-agent-runtime` audiences.
- Runtime downloads public keys only from its configured `AUTH_JWKS_URL`,
  caches them locally, and never follows token-supplied key URLs or algorithms.
- Runtime requires `iss`, runtime `aud`, UUID `sub`, UUID `sid`, `jti`, `iat`,
  `exp`, `token_version=1`, and string-array `roles` and `permissions`; `sub`
  is the authoritative owner user ID.
- Valid cache hits perform no Product Backend request. Unknown key IDs trigger
  a single bounded refresh; unavailable keys return a retryable `503`.
- Product API requests also check active device sessions. Runtime does not
  introspect sessions per request, so logout and revocation reach Runtime no
  later than the 15-minute access-token expiry.
- Flutter sends the same opaque bearer token to the single Agent origin
  configured by `MOMCOZY_AGENT_API_BASE_URL`.
- Readiness checks PostgreSQL, Redis, and a valid cached or freshly fetched
  JWKS. Runtime remains unready when signing keys cannot be established;
  authenticated requests also fail closed with a retryable `503`.

## Product Backend Internal API

- Runtime calls typed `/v1/internal/agent/*` APIs with `X-Service-Key` and
  `X-Request-ID`; writes also carry `Idempotency-Key`.
- `PRODUCT_BACKEND_SERVICE_KEY` must match Product Backend
  `AGENT_RUNTIME_SERVICE_API_KEY`, be stored as a deployment secret, and never
  be accepted from a public client.
- Runtime never forwards a caller-supplied owner identity. Public ownership
  comes from the verified token `sub`; internal requests remain explicitly
  owner-scoped.

## Replay and Eval Administration

Replay export and eval management require the `admin` role or
`agent:admin`/`agent.runtime.admin` permission. These operations are audited;
message content is omitted unless explicitly requested.
