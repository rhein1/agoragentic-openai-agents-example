"""Minimal contract test for example_openai_agents.py.

py_compile catches syntax rot but NOT endpoint/payload drift on /api/execute
or the `from agents import ...` SDK-rename risk. This test monkeypatches
requests.post and invokes the execute tool's underlying function to assert the
request goes to /api/execute with a body shaped {task, input, constraints.max_cost}.

Run standalone:
    python test_example_openai_agents.py
Or under pytest:
    pytest -q
"""

import json

import example_openai_agents as ex


class _FakeResponse:
    status_code = 200

    def json(self):
        return {
            "status": "success",
            "provider": {"name": "Fake Provider"},
            "output": {"ok": True},
            "cost": 0.15,
            "invocation_id": "test-id",
        }


def test_execute_posts_expected_contract(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(ex.requests, "post", fake_post)

    result = ex._execute(
        task="summarize",
        input_json='{"text": "hello"}',
        max_cost=0.25,
    )

    # Endpoint contract.
    assert captured["url"].endswith("/api/execute"), captured["url"]

    # Payload contract: task / input / constraints.max_cost.
    body = captured["json"]
    assert body["task"] == "summarize", body
    assert body["input"] == {"text": "hello"}, body
    assert "constraints" in body and body["constraints"]["max_cost"] == 0.25, body

    # Auth header present (Bearer scheme).
    assert captured["headers"]["Authorization"].startswith("Bearer "), captured["headers"]

    # Response is mapped into the unified result shape.
    parsed = json.loads(result)
    assert parsed["status"] == "success", parsed
    assert parsed["provider"] == "Fake Provider", parsed
    assert parsed["cost_usdc"] == 0.15, parsed


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
        test_execute_posts_expected_contract(mp)
    finally:
        mp.undo()
    print("OK: /api/execute contract test passed")
