"""Offline suite for the batched world deliberation (engine/deliberation.py).

Covers the paths that matter for quota and data integrity:
  * one call really does cover every actor (the whole point of batching)
  * a display name in place of an agent_id is still matched
  * malformed / partial / self-targeting responses degrade predictably
  * demo mode (no API key) produces a valid batch response
  * relation deltas are clamped so one bad number cannot wreck the graph

Run:  python tests/test_batched_deliberation.py
Exit code 0 = all green.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import _fakes  # noqa: E402

_fakes.install_stub_modules()

os.environ.setdefault("MONGO_URI", "mongodb://fake-local/testdb")
os.environ.setdefault("MONGO_DB_NAME", "world_in_motion_test")

from db import mongo  # noqa: E402

mongo._client = _fakes.FakeMongoClient()

from agents.base import Observation  # noqa: E402
from db import helpers  # noqa: E402
from db.seed import seed_nations, STARTER_NATIONS  # noqa: E402
from engine import deliberation  # noqa: E402
from llm import gemini  # noqa: E402


_PASS = 0
_FAIL = 0


def check(label, condition, detail=""):
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def _reset_db():
    mongo._client = _fakes.FakeMongoClient()
    helpers.invalidate_agent_index()
    return seed_nations()


def _observation(tick=1):
    return Observation(
        tick=tick,
        world_state={"run_id": "batch_test", "active_agents": len(STARTER_NATIONS)},
        events=[{
            "headline": "New export controls announced on advanced semiconductors",
            "description": "Measures target advanced node equipment.",
            "involved_agents": ["nation_usa", "nation_china"],
        }],
    )


class _Recorder:
    """Swaps in a scripted gemini.generate and counts how often it is called."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0
        self.prompts = []
        self._original = None

    def __enter__(self):
        self._original = gemini.generate

        def _fake(prompt="", **kwargs):
            self.calls += 1
            self.prompts.append(prompt)
            return self.responder(prompt, self.calls, **kwargs)

        gemini.generate = _fake
        return self

    def __exit__(self, *_exc):
        gemini.generate = self._original
        return False


def _full_batch(target="China", delta=-4.0, identify_by="agent_id"):
    """A well-formed batch response covering every actor.

    `identify_by` selects what goes *in* the agent_id field — "agent_id" for the
    canonical id, or "name" to simulate the model echoing a display name there
    instead, which is the mistake the tolerant index in `_index_by_agent` exists
    to absorb.
    """
    def responder(prompt, call_n, **kwargs):
        rows = []
        for nation in STARTER_NATIONS:
            identifier = nation["agent_id"] if identify_by == "agent_id" else nation["name"]
            if nation["name"] == target:
                rows.append({
                    "agent_id": identifier,
                    "action_type": "ignore",
                    "target_country": "None",
                    "reasoning": "Subject of the event.",
                    "relation_delta": 0.0,
                })
                continue
            rows.append({
                "agent_id": identifier,
                "action_type": "issue_statement",
                "target_country": target,
                "reasoning": "Reacting to the export controls.",
                "relation_delta": delta,
            })
        return json.dumps({"decisions": rows})

    return responder


# ---------------------------------------------------------------------------

def test_single_call_covers_every_actor():
    print("\n[batch] one call decides for all ten actors")
    agents = _reset_db()

    with _Recorder(_full_batch()) as rec:
        decisions, mode = deliberation.deliberate(agents, _observation())

    check("exactly one Gemini call", rec.calls == 1, f"calls={rec.calls}")
    check("mode is 'batch'", mode == "batch", f"mode={mode}")
    check("a decision for every actor", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")
    check("keys are canonical agent_ids",
          set(decisions) == {a.agent_id for a in agents})
    check("prompt carried the roster marker",
          "### ACTOR ROSTER" in rec.prompts[0])
    check("prompt listed valid targets",
          "### VALID target_country VALUES" in rec.prompts[0])


def test_display_name_key_is_matched():
    print("\n[batch] a display name in place of agent_id still resolves")
    agents = _reset_db()

    with _Recorder(_full_batch(identify_by="name")) as rec:
        decisions, mode = deliberation.deliberate(agents, _observation())

    check("still a single call", rec.calls == 1, f"calls={rec.calls}")
    check("mode is 'batch'", mode == "batch", f"mode={mode}")
    check("all actors matched by display name", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")
    check("results are keyed by canonical agent_id",
          set(decisions) == {a.agent_id for a in agents})
    # Guards against a false pass: a garbage decision can still fill the dict,
    # so confirm the payload is a real decision and not an echoed batch envelope.
    check("decisions carry real action fields",
          all(d.get("action_type") in deliberation.ACTION_TYPES
              for d in decisions.values()),
          f"got {[d.get('action_type') for d in list(decisions.values())[:3]]}")
    check("reasoning came from the batch response",
          all(d.get("reasoning") in
              {"Reacting to the export controls.", "Subject of the event."}
              for d in decisions.values()))


def test_missing_actor_triggers_only_stragglers():
    print("\n[batch] an omitted actor costs one extra call, not ten")
    agents = _reset_db()
    dropped = agents[0]

    def responder(prompt, call_n, **kwargs):
        if call_n == 1:
            rows = [
                {
                    "agent_id": n["agent_id"],
                    "action_type": "issue_statement",
                    "target_country": "China" if n["name"] != "China" else "None",
                    "reasoning": "Reacting.",
                    "relation_delta": -2.0 if n["name"] != "China" else 0.0,
                }
                for n in STARTER_NATIONS
                if n["agent_id"] != dropped.agent_id
            ]
            return json.dumps({"decisions": rows})
        # Per-actor fallback shape: a single decision object.
        return json.dumps({
            "action_type": "issue_statement",
            "target_country": "China",
            "reasoning": "Individual fallback call.",
            "relation_delta": -1.0,
        })

    with _Recorder(responder) as rec:
        decisions, mode = deliberation.deliberate(agents, _observation())

    check("batch + exactly one straggler call", rec.calls == 2, f"calls={rec.calls}")
    check("mode is 'batch+fallback'", mode == "batch+fallback", f"mode={mode}")
    check("dropped actor recovered", dropped.agent_id in decisions)
    check("recovered via the fallback call",
          decisions[dropped.agent_id]["reasoning"] == "Individual fallback call.")
    check("every actor has a decision", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")


def test_unparseable_batch_falls_back_completely():
    print("\n[batch] unparseable JSON degrades to per-actor calls")
    agents = _reset_db()

    def responder(prompt, call_n, **kwargs):
        if call_n == 1:
            return "not json at all {{{"
        return json.dumps({
            "action_type": "ignore",
            "target_country": "None",
            "reasoning": "Per-actor recovery.",
            "relation_delta": 0.0,
        })

    with _Recorder(responder) as rec:
        decisions, mode = deliberation.deliberate(agents, _observation())

    check("mode is 'fallback'", mode == "fallback", f"mode={mode}")
    check("one batch attempt plus one call per actor",
          rec.calls == 1 + len(agents), f"calls={rec.calls}")
    check("no actor left without a decision", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")


def test_fallback_can_be_disabled():
    print("\n[batch] fallback off means the batch is the entire quota spend")
    agents = _reset_db()

    with _Recorder(lambda p, n, **k: "garbage") as rec:
        decisions, mode = deliberation.deliberate(
            agents, _observation(), allow_single_agent_fallback=False
        )

    check("exactly one call", rec.calls == 1, f"calls={rec.calls}")
    check("mode is 'fallback'", mode == "fallback", f"mode={mode}")
    check("every actor got an inert decision", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")
    check("inert decisions are all 'ignore'",
          all(d["action_type"] == "ignore" for d in decisions.values()))
    check("inert decisions move nothing",
          all(d["relation_delta"] == 0.0 for d in decisions.values()))


def test_sanitisation():
    print("\n[batch] bad fields are sanitised, not trusted")
    agents = _reset_db()
    first, second = agents[0], agents[1]

    def responder(prompt, call_n, **kwargs):
        rows = [
            {   # out-of-range delta and an unknown action
                "agent_id": first.agent_id,
                "action_type": "declare_total_war",
                "target_country": second.name,
                "reasoning": "Extreme values.",
                "relation_delta": 999.0,
            },
            {   # targets itself
                "agent_id": second.agent_id,
                "action_type": "impose_sanction",
                "target_country": second.name,
                "reasoning": "Self-targeting.",
                "relation_delta": -30.0,
            },
            {   # unknown actor entirely
                "agent_id": "nation_atlantis",
                "action_type": "issue_statement",
                "target_country": first.name,
                "reasoning": "Should be dropped.",
                "relation_delta": -5.0,
            },
            "not even an object",
        ]
        for nation in STARTER_NATIONS[2:]:
            rows.append({
                "agent_id": nation["agent_id"],
                "action_type": "ignore",
                "target_country": "None",
                "reasoning": "No stake.",
                "relation_delta": 0.0,
            })
        return json.dumps({"decisions": rows})

    with _Recorder(responder):
        decisions, mode = deliberation.deliberate(
            agents, _observation(), allow_single_agent_fallback=False
        )

    check("unknown action coerced to 'ignore'",
          decisions[first.agent_id]["action_type"] == "ignore",
          f"got {decisions[first.agent_id]['action_type']}")
    check("delta clamped to +20", decisions[first.agent_id]["relation_delta"] == 20.0,
          f"got {decisions[first.agent_id]['relation_delta']}")
    check("self-target rewritten to 'None'",
          decisions[second.agent_id]["target_country"] == "None",
          f"got {decisions[second.agent_id]['target_country']}")
    check("self-target delta zeroed",
          decisions[second.agent_id]["relation_delta"] == 0.0)
    check("invented actor dropped", "nation_atlantis" not in decisions)
    check("non-object row ignored without crashing", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")


def test_demo_mode_returns_valid_batch():
    print("\n[demo mode] no API key still yields a usable batch response")
    agents = _reset_db()

    saved_key = os.environ.pop("GEMINI_API_KEY", None)
    try:
        decisions, mode = deliberation.deliberate(agents, _observation())
    finally:
        if saved_key is not None:
            os.environ["GEMINI_API_KEY"] = saved_key

    check("mode is 'batch' (mock filled every actor)", mode == "batch", f"mode={mode}")
    check("a decision per actor", len(decisions) == len(agents),
          f"{len(decisions)}/{len(agents)}")

    roster_names = {n["name"] for n in STARTER_NATIONS} | {"None"}
    check("every mock target is a real roster name or 'None'",
          all(d["target_country"] in roster_names for d in decisions.values()),
          f"offenders={[d['target_country'] for d in decisions.values() if d['target_country'] not in roster_names][:3]}")
    check("no actor targets itself",
          all(decisions[a.agent_id]["target_country"] != a.name for a in agents))
    check("mock output is labelled as demo mode",
          all("[DEMO MODE" in d["reasoning"] for d in decisions.values()))

    # And the targets must survive canonicalisation, or relation writes vanish.
    unresolvable = [
        d["target_country"]
        for d in decisions.values()
        if d["target_country"] != "None" and helpers.resolve_agent_id(d["target_country"]) is None
    ]
    check("every mock target resolves to an agent_id", not unresolvable,
          f"offenders={unresolvable[:3]}")


def test_empty_roster_is_safe():
    print("\n[batch] an empty roster spends nothing")
    with _Recorder(lambda p, n, **k: "should not be called") as rec:
        decisions, mode = deliberation.deliberate([], _observation())

    check("no Gemini call made", rec.calls == 0, f"calls={rec.calls}")
    check("no decisions returned", decisions == {})
    check("mode is 'batch'", mode == "batch", f"mode={mode}")


def main():
    print("=" * 68)
    print("Offline suite: batched world deliberation")
    print("=" * 68)

    for test in (
        test_single_call_covers_every_actor,
        test_display_name_key_is_matched,
        test_missing_actor_triggers_only_stragglers,
        test_unparseable_batch_falls_back_completely,
        test_fallback_can_be_disabled,
        test_sanitisation,
        test_demo_mode_returns_valid_batch,
        test_empty_roster_is_safe,
    ):
        test()

    print("\n" + "=" * 68)
    print(f"RESULT: {_PASS} passed, {_FAIL} failed")
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
