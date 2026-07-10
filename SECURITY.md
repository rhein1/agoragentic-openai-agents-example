# Security Policy

Report security issues privately to `security@agoragentic.com`.

Do not open public issues containing API keys, wallet-private data, payment payloads, private prompts, raw tool outputs, raw receipts, provider credentials, OAuth tokens, or private ECF data.

This example calls public Agoragentic APIs only when run by the user with their own environment variables. It must not include committed secrets, hidden provider credentials, wallet mutation helpers, x402 settlement logic, marketplace publication logic, or deployment automation.

Paid execution must remain fail-closed behind host-side operator authorization: the `AGORAGENTIC_ALLOW_PAID_EXECUTION` environment gate, a finite positive `AGORAGENTIC_MAX_COST_USDC` operator ceiling, a single-use approval recorded by host code (`--authorize-payment` / `authorize_payment()`), and a one-attempt-per-process guard. The model-visible tool schemas must never carry `payment_authorized`, `max_cost`, `idempotency_key`, or any other parameter that influences authorization, the spend ceiling, or the key — the model may supply task content only, and without a live host approval the execute tool must make zero network calls. Idempotency keys are generated or validated host-side and stay client-local; do not send them on the wire or claim router-level retry deduplication for `POST /api/execute`. Direct invoke remains omitted until it can be pre-bound to a caller-selected price ceiling.
