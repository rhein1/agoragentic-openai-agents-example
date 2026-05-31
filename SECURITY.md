# Security Policy

Report security issues privately to `security@agoragentic.com`.

Do not open public issues containing API keys, wallet-private data, payment payloads, private prompts, raw tool outputs, raw receipts, provider credentials, OAuth tokens, or private ECF data.

This example calls public Agoragentic APIs only when run by the user with their own environment variables. It must not include committed secrets, hidden provider credentials, wallet mutation helpers, x402 settlement logic, marketplace publication logic, or deployment automation.
