"""Offline regression: Gemini model resolution + quota fallback.

Runs the REAL `llm.gemini.generate()` against a scriptable fake SDK client, so
these assertions cover the actual retry/fallback control flow rather than a
re-implementation of it.

Why this matters: a retired or mistyped GEMINI_MODEL used to 404 on every call,
retry the same dead ID twice, then silently return mock text. The simulation kept
running and looked alive while no agent was doing real reasoning. These tests
pin down that (a) 404s skip straight to the next model instead of burning
retries, (b) the first working model is cached for the process, (c) rate-limit
errors retry the same model first (quota is per-model), and (d) exhausting the
chain is loud.

Run: python3 tests/test_gemini_fallback.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests._fakes as _fakes  # noqa: E402

_fakes.install_stub_modules()

import config  # noqa: E402
from llm import gemini  # noqa: E402

_PASS = 0
_FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


class FakeResponse:
    def __init__(self, text):
        self.text = text


class ScriptedClient:
    """Fake genai.Client whose per-model behaviour is scripted.

    `behaviour` maps model id -> either a string (returned as response text) or
    an Exception instance (raised). A model absent from the map raises 404, which
    mirrors the real SDK's response for an unknown model id.
    """

    def __init__(self, behaviour, available=None):
        self.behaviour = behaviour
        self.calls = []          # every (model) generate_content attempt, in order
        self.list_calls = 0
        self._available = available or ["gemini-2.5-flash", "gemini-2.0-flash"]
        self.models = self

    # --- models.generate_content -------------------------------------------
    def generate_content(self, *, model, contents, config=None):
        self.calls.append(model)
        outcome = self.behaviour.get(model)
        if outcome is None:
            raise RuntimeError(
                f"404 NOT_FOUND models/{model} is not found for API version v1beta"
            )
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    # --- models.list --------------------------------------------------------
    def list(self):
        self.list_calls += 1
        return [type("M", (), {"name": f"models/{m}"})() for m in self._available]


def install(behaviour, available=None):
    """Point llm.gemini at a scripted client and reset resolution state."""
    client = ScriptedClient(behaviour, available)
    gemini._client = client
    gemini._resolved_model = None
    return client


def setup_module_state():
    """Neutralise sleeps and the 5-RPM limiter so tests run instantly."""
    os.environ["GEMINI_API_KEY"] = "test-key-not-a-real-secret"
    gemini.time.sleep = lambda _s: None
    gemini.GEMINI_RATE_LIMITER.acquire = lambda: None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path():
    print("\n[generate] configured model answers -> no fallback, no extra calls")
    config.GEMINI_MODEL = "gemini-2.5-flash"
    config.GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash"]
    client = install({"gemini-2.5-flash": "hello world"})

    out = gemini.generate("hi")

    check("returned live text", out == "hello world", f"got {out!r}")
    check("called exactly one model once", client.calls == ["gemini-2.5-flash"],
          f"calls={client.calls}")
    check("active_model() is the configured model",
          gemini.active_model() == "gemini-2.5-flash", gemini.active_model())
    check("did not need models.list()", client.list_calls == 0)


def test_dead_model_skips_to_fallback():
    print("\n[generate] a 404 model is abandoned immediately, not retried")
    config.GEMINI_MODEL = "gemini-3.1-flash-lite"   # the suspect ID from the project notes
    config.GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]
    client = install({"gemini-2.5-flash": "live reasoning"})

    out = gemini.generate("hi")

    check("recovered live text from the fallback", out == "live reasoning", f"got {out!r}")
    check("dead model tried ONCE, not max_retries+1 times",
          client.calls.count("gemini-3.1-flash-lite") == 1,
          f"calls={client.calls}")
    check("walked the chain in order",
          client.calls == ["gemini-3.1-flash-lite", "gemini-2.5-flash"],
          f"calls={client.calls}")
    check("cached the working model",
          gemini.active_model() == "gemini-2.5-flash", gemini.active_model())

    # Second call must go straight to the survivor — no re-probing the dead ID.
    client.calls.clear()
    out2 = gemini.generate("again")
    check("subsequent call skips the dead model entirely",
          client.calls == ["gemini-2.5-flash"], f"calls={client.calls}")
    check("second call still live", out2 == "live reasoning", f"got {out2!r}")


def test_rate_limit_retries_same_model_then_moves_on():
    print("\n[generate] 429 retries the same model, then tries a sibling")
    config.GEMINI_MODEL = "gemini-2.5-flash"
    config.GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash"]
    client = install({
        "gemini-2.5-flash": RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded"),
        "gemini-2.0-flash": "sibling answered",
    })

    out = gemini.generate("hi", max_retries=2)

    check("fell through to the sibling model", out == "sibling answered", f"got {out!r}")
    check("retried the throttled model max_retries+1 times",
          client.calls.count("gemini-2.5-flash") == 3,
          f"calls={client.calls}")
    check("then tried the sibling once",
          client.calls.count("gemini-2.0-flash") == 1, f"calls={client.calls}")


def test_transient_error_recovers_on_same_model():
    print("\n[generate] a transient 503 is retried, not treated as a dead model")
    config.GEMINI_MODEL = "gemini-2.5-flash"
    config.GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash"]

    class FlakyClient(ScriptedClient):
        def generate_content(self, *, model, contents, config=None):
            self.calls.append(model)
            if len(self.calls) == 1:
                raise RuntimeError("503 UNAVAILABLE backend overloaded")
            return FakeResponse("recovered")

    client = FlakyClient({"gemini-2.5-flash": "recovered"})
    gemini._client = client
    gemini._resolved_model = None

    out = gemini.generate("hi", max_retries=2)

    check("recovered after retry", out == "recovered", f"got {out!r}")
    check("stayed on the configured model",
          set(client.calls) == {"gemini-2.5-flash"}, f"calls={client.calls}")
    check("never escalated to a fallback model",
          "gemini-2.0-flash" not in client.calls, f"calls={client.calls}")


def test_exhausted_chain_falls_back_to_mock_and_lists_models():
    print("\n[generate] every model dead -> valid mock JSON + diagnostic model list")
    config.GEMINI_MODEL = "nope-1"
    config.GEMINI_FALLBACK_MODELS = ["nope-2", "nope-3"]
    client = install({}, available=["gemini-2.5-flash", "gemini-2.0-flash"])

    out = gemini.generate(
        "Tensions rise over tariffs",
        system_instruction="You are the leadership of Solaria Federation, a proud nation.",
        response_mime_type="application/json",
    )

    check("tried every candidate exactly once",
          client.calls == ["nope-1", "nope-2", "nope-3"], f"calls={client.calls}")
    check("consulted models.list() for the fix-it hint", client.list_calls == 1,
          f"list_calls={client.list_calls}")

    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = None
    check("mock fallback is still schema-valid JSON", isinstance(payload, dict),
          f"got {out[:80]!r}")
    if isinstance(payload, dict):
        check("mock payload has the 4 required keys",
              set(payload) == {"action_type", "target_country", "reasoning", "relation_delta"},
              f"keys={sorted(payload)}")
        check("mock never targets itself",
              payload.get("target_country") != "Solaria Federation",
              f"target={payload.get('target_country')!r}")


def test_explicit_model_arg_is_not_overridden():
    print("\n[generate] an explicit model= argument bypasses the fallback chain")
    config.GEMINI_MODEL = "gemini-2.5-flash"
    config.GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash"]
    client = install({"gemini-2.0-flash": "should not be reached"})

    out = gemini.generate("hi", model="pinned-model", response_mime_type=None)

    check("only the pinned model was attempted", client.calls == ["pinned-model"],
          f"calls={client.calls}")
    check("did not silently substitute a fallback",
          "gemini-2.0-flash" not in client.calls, f"calls={client.calls}")
    check("returned mock rather than another model's answer",
          out != "should not be reached", f"got {out[:60]!r}")
    check("explicit failure did not poison the cached model",
          gemini._resolved_model is None, f"resolved={gemini._resolved_model!r}")


def test_missing_api_key_short_circuits():
    print("\n[generate] no API key -> mock immediately, zero network attempts")
    saved = os.environ.pop("GEMINI_API_KEY", None)
    client = install({"gemini-2.5-flash": "unreachable"})
    try:
        out = gemini.generate("hi")
        check("no SDK call attempted", client.calls == [], f"calls={client.calls}")
        check("returned mock text", bool(out.strip()))
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


def main():
    print("=" * 68)
    print("Offline regression: Gemini model resolution + quota fallback")
    print("=" * 68)

    setup_module_state()

    for test in (
        test_happy_path,
        test_dead_model_skips_to_fallback,
        test_rate_limit_retries_same_model_then_moves_on,
        test_transient_error_recovers_on_same_model,
        test_exhausted_chain_falls_back_to_mock_and_lists_models,
        test_explicit_model_arg_is_not_overridden,
        test_missing_api_key_short_circuits,
    ):
        test()

    print("\n" + "=" * 68)
    print(f"RESULT: {_PASS} passed, {_FAIL} failed")
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
