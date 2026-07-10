# Contributing

Keep this repository a minimal, safe OpenAI Agents SDK example.

- Prefer `execute()` over hardcoded provider IDs.
- Keep direct `invoke()` omitted until the example can pre-bind it to a caller-selected price ceiling.
- Keep paid execution behind the operator environment gate, hard operator ceiling, host-side single-use `authorize_payment()` approval, and one-attempt guard. Never expose authorization, spend-ceiling, or idempotency-key parameters in a model-visible tool schema.
- Do not commit secrets, API keys, raw private payloads, wallet-private data, or raw receipts.
- Do not add deployment automation, public execute routes, x402 settlement mutation, marketplace publication, or provider-specific hidden credentials.
- Keep examples honest about what is live, optional, paid, or policy-gated.

Before submitting changes, run:

```bash
python -m py_compile example_openai_agents.py
python test_example_openai_agents.py
```
