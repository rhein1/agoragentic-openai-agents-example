"""
Agoragentic × OpenAI Agents SDK — Execute-First Example
=========================================================

Route a task to an eligible provider on the Agoragentic Router /
Marketplace using a single execute() call. The router scores and selects
a provider under the operator-approved max_cost and returns receipt-backed
output when paid execution succeeds.

Install:
    pip install -r requirements.txt

Run (no-spend preview):
    export AGORAGENTIC_API_KEY="amk_your_key"
    export OPENAI_API_KEY="sk-..."   # required: the agent loop runs on an OpenAI model
    python example_openai_agents.py

Run (one operator-authorized paid execution):
    export AGORAGENTIC_ALLOW_PAID_EXECUTION=1
    export AGORAGENTIC_MAX_COST_USDC=0.25
    python example_openai_agents.py --authorize-payment 0.25 'Execute summarize once.'

No API key? Get a free one in ~60s (no card); the response returns an api_key:
    curl -X POST https://agoragentic.com/api/quickstart \
      -H "Content-Type: application/json" \
      -d '{"name":"my-agent"}'
Illustrative prices in this example are fixtures.
Full docs: https://agoragentic.com/skill.md
"""

import json
import math
import os
import re
import threading
import uuid
from typing import NamedTuple

import requests

# ── OpenAI Agents SDK imports ─────────────────────────────
from agents import Agent, Runner, function_tool

AGORAGENTIC_API = os.environ.get("AGORAGENTIC_BASE_URL", "https://agoragentic.com")
API_KEY = os.environ.get("AGORAGENTIC_API_KEY", "")
_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
_DEFAULT_MATCH_BUDGET = 1.0
_EXECUTION_LOCK = threading.Lock()
_EXECUTION_ATTEMPTED = False


class _PaymentApproval(NamedTuple):
    max_cost_usdc: float
    idempotency_key: str


# Single-use host approval. Only authorize_payment() writes it and only the
# execute path consumes it; no model-visible tool parameter can reach it.
_PAYMENT_APPROVAL: _PaymentApproval | None = None


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }


def _operator_max_cost() -> float | None:
    raw = os.environ.get("AGORAGENTIC_MAX_COST_USDC", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


# ─── Host-side payment approval — never exposed to the model ──
def authorize_payment(
    max_cost_usdc: float,
    idempotency_key: str | None = None,
) -> _PaymentApproval:
    """Record a single-use spend approval from host code (not from the model).

    Called by main() behind the --authorize-payment CLI flag. Requires the
    AGORAGENTIC_ALLOW_PAID_EXECUTION operator gate, validates the ceiling
    against AGORAGENTIC_MAX_COST_USDC, and generates or validates a
    client-local idempotency key. Raises on any invalid or duplicate request.
    """
    global _PAYMENT_APPROVAL
    if not _env_enabled("AGORAGENTIC_ALLOW_PAID_EXECUTION"):
        raise RuntimeError(
            "Set AGORAGENTIC_ALLOW_PAID_EXECUTION=1 before authorizing paid execution."
        )
    if isinstance(max_cost_usdc, bool) or not isinstance(max_cost_usdc, (int, float)):
        raise ValueError("max_cost_usdc must be a number, not a truthy stand-in.")
    max_cost_usdc = float(max_cost_usdc)
    if not math.isfinite(max_cost_usdc) or max_cost_usdc <= 0:
        raise ValueError(
            "max_cost_usdc must be finite and positive; the deployed router treats zero as an absent ceiling."
        )
    operator_ceiling = _operator_max_cost()
    if operator_ceiling is None:
        raise RuntimeError(
            "Set AGORAGENTIC_MAX_COST_USDC to a finite positive operator ceiling."
        )
    if max_cost_usdc > operator_ceiling:
        raise ValueError(
            f"max_cost_usdc exceeds the operator ceiling of {operator_ceiling} USDC."
        )
    if idempotency_key is None:
        idempotency_key = f"openai-example-{uuid.uuid4()}"
    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(
        idempotency_key.strip()
    ):
        raise ValueError(
            "idempotency_key must be a 1-200 character client-local intent key."
        )
    idempotency_key = idempotency_key.strip()
    with _EXECUTION_LOCK:
        if _EXECUTION_ATTEMPTED:
            raise RuntimeError(
                "This process already attempted its single marketplace execution; start a new process to authorize again."
            )
        if _PAYMENT_APPROVAL is not None:
            raise RuntimeError(
                "A payment approval is already pending; this process holds at most one."
            )
        _PAYMENT_APPROVAL = _PaymentApproval(max_cost_usdc, idempotency_key)
        return _PAYMENT_APPROVAL


# ─── Primary tool: execute() — Router / Marketplace ─────
def _execute(task: str, input_json: str = "{}") -> str:
    """Underlying implementation for the execute tool (plain, unit-testable).

    Kept separate from the @function_tool wrapper so tests can invoke it
    directly and assert the request body sent to /api/execute. Consumes the
    single-use host approval; with no live approval it fails closed before
    any network call.
    """
    if not API_KEY:
        return json.dumps({"error": "missing_agoragentic_api_key"})
    try:
        parsed_input = json.loads(input_json)
    except (TypeError, ValueError) as exc:
        return json.dumps({"error": "invalid_input_json", "message": str(exc)})

    global _EXECUTION_ATTEMPTED, _PAYMENT_APPROVAL
    with _EXECUTION_LOCK:
        if _EXECUTION_ATTEMPTED:
            return json.dumps({
                "error": "execution_already_attempted",
                "message": "This process permits one marketplace execution. Inspect receipts or account activity before starting a newly authorized process.",
            })
        if _PAYMENT_APPROVAL is None or not _env_enabled(
            "AGORAGENTIC_ALLOW_PAID_EXECUTION"
        ):
            return json.dumps({
                "error": "paid_execution_not_authorized",
                "message": "The operator must authorize spending outside the model: set AGORAGENTIC_ALLOW_PAID_EXECUTION=1 and run with --authorize-payment (host-side authorize_payment).",
            })
        approval = _PAYMENT_APPROVAL
        _PAYMENT_APPROVAL = None
        _EXECUTION_ATTEMPTED = True

    try:
        resp = requests.post(
            f"{AGORAGENTIC_API}/api/execute",
            json={
                "task": task,
                "input": parsed_input,
                "constraints": {"max_cost": approval.max_cost_usdc},
            },
            headers=_headers(),
            timeout=60,
        )
        data = resp.json()
        if resp.status_code == 200:
            receipt = data.get("receipt")
            if not isinstance(receipt, dict):
                receipt = {}
            return json.dumps({
                "status": data.get("status"),
                "provider": data.get("provider", {}).get("name"),
                "output": data.get("output"),
                "cost_usdc": data.get("cost"),
                "invocation_id": data.get("invocation_id"),
                "receipt_id": data.get("receipt_id") or receipt.get("receipt_id"),
                "receipt_url": data.get("receipt_url") or receipt.get("receipt_url"),
                "settlement": data.get("settlement") or receipt.get("settlement"),
            }, indent=2)
        return json.dumps({"error": data.get("error"), "message": data.get("message")})
    except Exception as e:
        return json.dumps({"error": str(e)})


@function_tool
def agoragentic_execute(task: str, input_json: str = "{}") -> str:
    """Route a task to an eligible provider on the Agoragentic marketplace.

    Describe what you need in plain English. The router finds, scores, and
    invokes an eligible provider subject to the operator-approved cost ceiling
    and account policy. Payment authorization, the spend ceiling, and the
    idempotency key are bound by the operator outside this tool; without a
    live operator approval the call fails closed with no network request.
    Paid calls use USDC on Base L2 and return receipt-backed metadata.

    Args:
        task: What you need done (e.g., "summarize", "translate", "analyze sentiment").
        input_json: JSON string with the input payload for the provider.
    """
    return _execute(task, input_json)


# ─── Optional: match() — preview providers before committing ──
@function_tool
def agoragentic_match(task: str) -> str:
    """Preview which providers the router would select — dry run, no charge.

    The preview budget is the operator's AGORAGENTIC_MAX_COST_USDC ceiling
    when configured, otherwise a 1.0 USDC default. No money moves.

    Args:
        task: What you need done.
    """
    if not API_KEY:
        return json.dumps({"error": "missing_agoragentic_api_key"})
    preview_max_cost = _operator_max_cost()
    if preview_max_cost is None:
        preview_max_cost = _DEFAULT_MATCH_BUDGET
    try:
        resp = requests.get(
            f"{AGORAGENTIC_API}/api/execute/match",
            params={"task": task, "max_cost": preview_max_cost},
            headers=_headers(),
            timeout=15,
        )
        data = resp.json()
        providers = [
            {"name": p["name"], "price": p["price"], "score": p["score"]["composite"]}
            for p in data.get("providers", [])[:5]
        ]
        return json.dumps({
            "task": task,
            "matches": data.get("matches"),
            "top_providers": providers,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Agent definition ────────────────────────────────────
agent = Agent(
    name="marketplace-agent",
    instructions=(
        "You are an AI agent with access to the Agoragentic Router / Marketplace. "
        "Preview providers with agoragentic_match before any execution. Only call "
        "agoragentic_execute when the user explicitly asks to execute. You cannot "
        "authorize spending: the operator pre-approves the cost ceiling outside "
        "the model, the tool fails closed without that approval, and only one "
        "execution attempt is permitted per process. Never retry an execution "
        "automatically. Direct invoke is intentionally omitted."
    ),
    tools=[agoragentic_execute, agoragentic_match],
)


# ─── Run ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(
        description="Agoragentic x OpenAI Agents SDK execute-first example.",
    )
    parser.add_argument(
        "--authorize-payment",
        type=float,
        default=None,
        metavar="MAX_COST_USDC",
        help=(
            "Operator-only: approve at most this USDC spend for a single "
            "marketplace execution. Also requires AGORAGENTIC_ALLOW_PAID_EXECUTION=1 "
            "and an AGORAGENTIC_MAX_COST_USDC ceiling at or above it."
        ),
    )
    parser.add_argument("prompt", nargs="*", help="Prompt for the agent loop.")
    args = parser.parse_args()

    # The agent loop runs on an OpenAI model, so the OpenAI Agents SDK needs
    # OPENAI_API_KEY. Fail fast with a clear message instead of a raw OpenAIError.
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is required to run this example - the agent loop is "
            "driven by an OpenAI model. Set it with: export OPENAI_API_KEY=\"sk-...\"\n"
            "(The free Agoragentic quickstart key is separate and does not cover the "
            "OpenAI dependency.)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.authorize_payment is not None:
        try:
            approval = authorize_payment(args.authorize_payment)
        except (RuntimeError, ValueError) as exc:
            print(f"Payment authorization refused: {exc}", file=sys.stderr)
            sys.exit(1)
        print(
            f"Operator approval recorded: at most {approval.max_cost_usdc} USDC for "
            f"one execution (client-local intent key {approval.idempotency_key}).",
            file=sys.stderr,
        )

    async def main():
        prompt = " ".join(args.prompt).strip() or (
            "Preview the best providers for summarization under $0.25. Do not execute."
        )
        result = await Runner.run(agent, input=prompt)
        print(result.final_output)

    asyncio.run(main())
