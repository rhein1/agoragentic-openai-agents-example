# AGENTS.md

This repository is a public OpenAI Agents SDK example for Agoragentic's execute-first Router / Marketplace path.

## Product Boundary

- Keep the example small, runnable, and honest.
- Do not deploy the agent, publish marketplace listings, enable x402 settlement, expose public execute routes, mutate hosted policy, or bypass Agoragentic receipt controls from this repo.
- Do not commit API keys, wallet material, private prompts, or production data.
- Use `execute(task, input, constraints)` instead of hardcoding provider IDs unless the example intentionally demonstrates direct invocation.

## Fable / ECF Workflow Discipline

- Use [docs/agent-workflow-contracts.md](docs/agent-workflow-contracts.md) for Fable-5-style audits, deep reviews, fact checks, repo sweeps, and governed multi-agent runs.
- Use [docs/fable-review-contract.md](docs/fable-review-contract.md) when writing PR-review findings.
- Do not claim multi-subagent execution unless the runtime provides real subagent IDs. If no subagent runtime is available, report `subagents: none_available`.
- Main agents own final synthesis, edits, commits, pushes, PRs, release actions, and completion claims.

## Validation

Prefer a lightweight local smoke before publishing changes:

```bash
python -m py_compile example_openai_agents.py
python test_example_openai_agents.py
```
