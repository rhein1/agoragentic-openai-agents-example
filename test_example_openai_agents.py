"""Contract tests for example_openai_agents.py.

py_compile catches syntax rot but NOT endpoint/payload drift on /api/execute,
the `from agents import ...` SDK-rename risk, or the authorization boundary.
These tests monkeypatch requests.post and assert:
  - no tool exposes payment_authorized / max_cost / idempotency_key to the model
  - the execute path fails closed with zero network calls without host approval
  - with host approval the request goes to /api/execute shaped
    {task, input, constraints.max_cost} with no approval fields leaked
  - host-side authorize_payment validates ceilings and keys

Run standalone:
    python test_example_openai_agents.py
Or under pytest:
    pytest -q
"""

import json
import math
import os

import example_openai_agents as ex

_FORBIDDEN_MODEL_PARAMS = ("payment_authorized", "max_cost", "idempotency_key")


class _FakeResponse:
    status_code = 200

    def json(self):
        return {
            "status": "success",
            "provider": {"name": "Fake Provider"},
            "output": {"ok": True},
            "cost": 0.15,
            "invocation_id": "test-id",
            "receipt_id": "rcpt-test",
            "settlement": "settled",
        }


def _assert_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001 - report the wrong exception type
        raise AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
    raise AssertionError(f"expected {exc_type.__name__}, nothing raised")


def _reset_approval_state(monkeypatch):
    monkeypatch.setattr(ex, "_EXECUTION_ATTEMPTED", False)
    monkeypatch.setattr(ex, "_PAYMENT_APPROVAL", None)


def test_tool_schemas_expose_no_payment_controls():
    # Runtime inspection of every model-visible tool schema: authorization,
    # spend ceiling, and idempotency key must not be model-supplied anywhere.
    tools = ex.agent.tools
    assert len(tools) == 2, [t.name for t in tools]
    for tool in tools:
        serialized = json.dumps(tool.params_json_schema)
        for forbidden in _FORBIDDEN_MODEL_PARAMS:
            assert forbidden not in serialized, (tool.name, forbidden, serialized)

    by_name = {t.name: t for t in tools}
    execute_props = set(by_name["agoragentic_execute"].params_json_schema["properties"])
    assert execute_props == {"task", "input_json"}, execute_props
    match_props = set(by_name["agoragentic_match"].params_json_schema["properties"])
    assert match_props == {"task"}, match_props


def test_execute_without_host_approval_makes_no_network_call(monkeypatch):
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    monkeypatch.setattr(ex.requests, "post", fake_post)
    monkeypatch.setattr(ex, "API_KEY", "amk_test")
    _reset_approval_state(monkeypatch)
    # Even with both operator env vars set, model-visible arguments alone must
    # never reach the network: there is no live host approval to consume.
    os.environ["AGORAGENTIC_ALLOW_PAID_EXECUTION"] = "1"
    os.environ["AGORAGENTIC_MAX_COST_USDC"] = "0.50"
    try:
        parsed = json.loads(ex._execute("summarize", '{"text":"hello"}'))
    finally:
        os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
        os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)
    assert parsed["error"] == "paid_execution_not_authorized", parsed
    assert called is False
    assert ex._EXECUTION_ATTEMPTED is False


def test_execute_with_host_approval_posts_expected_contract(monkeypatch):
    captured = {}
    calls = 0

    def fake_post(url, json=None, headers=None, timeout=None):
        nonlocal calls
        calls += 1
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(ex.requests, "post", fake_post)
    monkeypatch.setattr(ex, "API_KEY", "amk_test")
    _reset_approval_state(monkeypatch)
    os.environ["AGORAGENTIC_ALLOW_PAID_EXECUTION"] = "1"
    os.environ["AGORAGENTIC_MAX_COST_USDC"] = "0.50"
    try:
        approval = ex.authorize_payment(0.25, "openai-example-test-1")
        result = ex._execute(task="summarize", input_json='{"text": "hello"}')
    finally:
        os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
        os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)

    # Exactly one request, to the execute endpoint.
    assert calls == 1
    assert captured["url"].endswith("/api/execute"), captured["url"]

    # Payload contract: task / input / constraints.max_cost from the approval.
    body = captured["json"]
    assert body["task"] == "summarize", body
    assert body["input"] == {"text": "hello"}, body
    assert "constraints" in body and body["constraints"]["max_cost"] == 0.25, body

    # The client-local key and authorization flag must not leak on the wire.
    serialized_body = json.dumps(body)
    assert "payment_authorized" not in serialized_body, body
    assert "idempotency_key" not in serialized_body, body
    assert approval.idempotency_key not in serialized_body, body
    assert "Idempotency-Key" not in captured["headers"], captured["headers"]
    for value in captured["headers"].values():
        assert approval.idempotency_key not in value, captured["headers"]

    # Auth header present (Bearer scheme).
    assert captured["headers"]["Authorization"].startswith("Bearer "), captured["headers"]

    # The single-use approval is consumed.
    assert ex._PAYMENT_APPROVAL is None

    # Response is mapped into the unified result shape.
    parsed = json.loads(result)
    assert parsed["status"] == "success", parsed
    assert parsed["provider"] == "Fake Provider", parsed
    assert parsed["cost_usdc"] == 0.15, parsed
    assert parsed["receipt_id"] == "rcpt-test", parsed
    assert parsed["settlement"] == "settled", parsed


def test_execute_allows_only_one_attempt_per_process(monkeypatch):
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _FakeResponse()

    monkeypatch.setattr(ex.requests, "post", fake_post)
    monkeypatch.setattr(ex, "API_KEY", "amk_test")
    _reset_approval_state(monkeypatch)
    os.environ["AGORAGENTIC_ALLOW_PAID_EXECUTION"] = "1"
    os.environ["AGORAGENTIC_MAX_COST_USDC"] = "0.50"
    try:
        ex.authorize_payment(0.25, "one-shot-key")
        first = json.loads(ex._execute("summarize", "{}"))
        second = json.loads(ex._execute("summarize", "{}"))
        # After the attempt, the host cannot re-arm this process either.
        _assert_raises(RuntimeError, ex.authorize_payment, 0.25, "one-shot-key-2")
    finally:
        os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
        os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)

    assert first["status"] == "success", first
    assert second["error"] == "execution_already_attempted", second
    assert calls == 1


def test_authorize_payment_rejects_invalid_requests(monkeypatch):
    _reset_approval_state(monkeypatch)
    os.environ["AGORAGENTIC_ALLOW_PAID_EXECUTION"] = "1"
    os.environ["AGORAGENTIC_MAX_COST_USDC"] = "0.10"
    try:
        for value in (0, -0.01, math.nan, math.inf, -math.inf):
            _assert_raises(ValueError, ex.authorize_payment, value)
        # Over the operator ceiling.
        _assert_raises(ValueError, ex.authorize_payment, 999)
        # Type confusion: booleans and numeric strings are not spend ceilings.
        _assert_raises(ValueError, ex.authorize_payment, True)
        _assert_raises(ValueError, ex.authorize_payment, "0.05")
        # Malformed caller key.
        _assert_raises(ValueError, ex.authorize_payment, 0.05, "bad\nkey")
        assert ex._PAYMENT_APPROVAL is None

        # A valid request generates a client-local key host-side.
        approval = ex.authorize_payment(0.05)
        assert approval.max_cost_usdc == 0.05, approval
        assert ex._IDEMPOTENCY_KEY_RE.fullmatch(approval.idempotency_key), approval
        # Single pending approval per process.
        _assert_raises(RuntimeError, ex.authorize_payment, 0.05)
    finally:
        os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
        os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)

    _reset_approval_state(monkeypatch)
    # Operator env gate off: the host cannot authorize at all.
    os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
    os.environ["AGORAGENTIC_MAX_COST_USDC"] = "0.10"
    try:
        _assert_raises(RuntimeError, ex.authorize_payment, 0.05)
    finally:
        os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)

    # Missing operator ceiling: nothing to validate against, refuse.
    os.environ["AGORAGENTIC_ALLOW_PAID_EXECUTION"] = "1"
    os.environ.pop("AGORAGENTIC_MAX_COST_USDC", None)
    try:
        _assert_raises(RuntimeError, ex.authorize_payment, 0.05)
    finally:
        os.environ.pop("AGORAGENTIC_ALLOW_PAID_EXECUTION", None)
    assert ex._PAYMENT_APPROVAL is None


if __name__ == "__main__":
    # Tiny standalone monkeypatch shim so this file runs without pytest.
    class _MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    mp = _MonkeyPatch()
    try:
        test_tool_schemas_expose_no_payment_controls()
        test_execute_without_host_approval_makes_no_network_call(mp)
        test_execute_with_host_approval_posts_expected_contract(mp)
        test_execute_allows_only_one_attempt_per_process(mp)
        test_authorize_payment_rejects_invalid_requests(mp)
    finally:
        mp.undo()
    print("OK: /api/execute contract and authorization-boundary tests passed")
