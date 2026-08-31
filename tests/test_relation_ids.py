"""Offline regression suite for the target_agent_id canonicalisation fix.

Runs with only the standard library — a fake in-memory Mongo (tests/_fakes.py)
is injected in place of pymongo, and llm.gemini.generate is monkeypatched, so
the real helpers / seed / engine code runs end to end with no network and no
third-party packages.

Baselines are read out of `STARTER_NATIONS` rather than hardcoded. An earlier
version of this file asserted literal scores against a fictional roster, so
swapping the roster silently invalidated every number in it. Deriving them
keeps the suite meaningful across reseeds.

Run:  python tests/test_relation_ids.py
Exit code 0 = all green.
"""

import json
import os
import sys

# Make the repo root importable when run directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import _fakes  # noqa: E402

_fakes.install_stub_modules()

# Point config at the fake before anything reads it.
os.environ.setdefault("MONGO_URI", "mongodb://fake-local/testdb")
os.environ.setdefault("MONGO_DB_NAME", "world_in_motion_test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-a-real-secret")

from db import mongo  # noqa: E402

# Force db.mongo to use the in-memory fake client for the whole run.
mongo._client = _fakes.FakeMongoClient()

from db import helpers, schema  # noqa: E402
from db.seed import seed_nations, STARTER_NATIONS  # noqa: E402


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


KNOWN_IDS = {n["agent_id"] for n in STARTER_NATIONS}
NAME_TO_ID = {n["name"]: n["agent_id"] for n in STARTER_NATIONS}
ID_TO_NAME = {n["agent_id"]: n["name"] for n in STARTER_NATIONS}
_BY_ID = {n["agent_id"]: n for n in STARTER_NATIONS}


def baseline(source_id: str, target_name: str) -> float:
    """Seeded starting score for source_id -> target_name."""
    return float(_BY_ID[source_id]["persona"]["relations"][target_name])


def _pick_pair() -> tuple[str, str, float]:
    """A (source_id, target_name, baseline) triple that exists in the seed."""
    for nation in STARTER_NATIONS:
        relations = nation["persona"].get("relations", {})
        for target_name, score in relations.items():
            if target_name in NAME_TO_ID:
                return nation["agent_id"], target_name, float(score)
    raise AssertionError("seed data has no usable relation pair")


def stray_relation_docs() -> list[dict]:
    """Relation docs whose endpoints are not canonical agent_ids."""
    return [
        r
        for r in helpers.list_all_relations()
        if r["source_agent_id"] not in KNOWN_IDS or r["target_agent_id"] not in KNOWN_IDS
    ]


# ---------------------------------------------------------------------------

def test_resolver():
    print("\n[resolver] name/id/none resolution")
    _reset_db()
    seed_nations()

    check("agent_id resolves to itself",
          helpers.resolve_agent_id("nation_china") == "nation_china")
    check("display name resolves to id",
          helpers.resolve_agent_id("China") == "nation_china")
    check("lower-cased name resolves",
          helpers.resolve_agent_id("united states") == "nation_usa")
    check("whitespace tolerated",
          helpers.resolve_agent_id("  United Kingdom  ") == "nation_uk")
    check("multi-word bloc name resolves",
          helpers.resolve_agent_id("European Union") == "nation_eu")
    check("'None' sentinel -> None", helpers.resolve_agent_id("None") is None)
    check("empty -> None", helpers.resolve_agent_id("") is None)
    check("unknown -> None", helpers.resolve_agent_id("Atlantis") is None)
    check("retired fictional name no longer resolves",
          helpers.resolve_agent_id("Ironreach Dominion") is None)


def test_seed_is_canonical():
    print("\n[seed] every relation endpoint is a canonical agent_id")
    _reset_db()
    seed_nations()

    rels = helpers.list_all_relations()
    check("relations were written", len(rels) > 0, f"count={len(rels)}")
    check("no display names survived seeding", not stray_relation_docs(),
          f"offenders={json.dumps(stray_relation_docs()[:2])}")

    source_id, target_name, expected = _pick_pair()
    doc = helpers.get_relation(source_id, NAME_TO_ID[target_name])
    check(f"{source_id}->{target_name} baseline == {expected}",
          doc["score"] == expected, f"got {doc['score']}")

    # And that it is reachable by display name too (read-side resolution).
    by_name = helpers.get_relation(ID_TO_NAME[source_id], target_name)
    check("same doc reachable via display names", by_name["score"] == expected,
          f"got {by_name['score']}")


def test_all_seeded_targets_resolve():
    print("\n[seed] no persona names a nation outside the roster")
    unresolvable = []
    for nation in STARTER_NATIONS:
        for target_name in nation["persona"].get("relations", {}):
            if target_name not in NAME_TO_ID:
                unresolvable.append(f"{nation['agent_id']} -> {target_name}")
        for key in ("allies", "rivals"):
            for other in nation["persona"].get(key, []):
                if other not in NAME_TO_ID:
                    unresolvable.append(f"{nation['agent_id']}.{key} -> {other}")

    check("every relations/allies/rivals name is a real roster member",
          not unresolvable, f"offenders={unresolvable[:4]}")


def test_purge_removes_previous_roster():
    print("\n[seed] a stale agent from a previous roster is purged")
    _reset_db()
    db = mongo._client[os.environ["MONGO_DB_NAME"]]

    # Plant a retired fictional nation and a relation pointing at it.
    db[schema.AGENTS].insert_one({
        "agent_id": "nation_ironreach",
        "name": "Ironreach Dominion",
        "agent_type": "nation",
        "persona": {"relations": {}},
    })
    db[schema.RELATIONS].insert_one({
        "source_agent_id": "nation_ironreach",
        "target_agent_id": "nation_eldoria",
        "score": 10.0,
    })
    helpers.invalidate_agent_index()

    seed_nations()

    surviving = {d["agent_id"] for d in helpers.list_agents(agent_type="nation")}
    check("retired agent removed", "nation_ironreach" not in surviving,
          f"surviving={sorted(surviving)}")
    check("current roster fully present", KNOWN_IDS.issubset(surviving),
          f"missing={sorted(KNOWN_IDS - surviving)}")
    check("relations pointing at retired agent removed", not stray_relation_docs(),
          f"offenders={json.dumps(stray_relation_docs()[:2])}")


def test_update_relation_boundary():
    print("\n[update_relation] display-name args are canonicalised at the DB boundary")
    _reset_db()
    seed_nations()

    source_name, target_name = "United States", "China"
    source_id, target_id = NAME_TO_ID[source_name], NAME_TO_ID[target_name]
    start = baseline(source_id, target_name)

    # Simulate the OLD buggy caller passing a display name straight in.
    helpers.update_relation(
        source_agent_id=source_name,
        target_agent_id=target_name,
        delta=-5.0,
        reasoning="offline test",
    )
    check("no display-name doc created", not stray_relation_docs(),
          f"offenders={json.dumps(stray_relation_docs()[:2])}")

    doc = helpers.get_relation(source_id, target_id)
    check(f"delta applied to canonical doc ({start} -> {start - 5.0})",
          doc["score"] == start - 5.0, f"got {doc['score']}")


def test_decide_persists_ids():
    print("\n[NationAgent.decide] a live-style decision writes an id-keyed relation")
    _reset_db()
    seed_nations()

    from agents.nation import NationAgent
    from llm import gemini

    actor_id, target_name = "nation_india", "China"
    start = baseline(actor_id, target_name)

    # Patch Gemini to return a decision that targets a DISPLAY NAME, exactly
    # like the real model does.
    decision = {
        "action_type": "impose_sanction",
        "target_country": target_name,
        "reasoning": "Response to reported border activity.",
        "relation_delta": -12.0,
    }
    original = gemini.generate
    gemini.generate = lambda *a, **k: json.dumps(decision)
    try:
        actor = NationAgent.load_from_db(actor_id)
        result = actor.decide()
    finally:
        gemini.generate = original

    check("decision returned target display name",
          result["target_country"] == target_name)

    doc = helpers.get_relation(actor_id, NAME_TO_ID[target_name])
    check(f"relation persisted under canonical id ({start} -> {start - 12.0})",
          doc["score"] == start - 12.0, f"got {doc['score']}")

    # persona dict still keyed by display name (feeds prompts) — that's intended
    check("persona.relations still keyed by display name",
          target_name in actor.relations)

    check("still zero display-name docs after decide()", not stray_relation_docs(),
          f"offenders={json.dumps(stray_relation_docs()[:2])}")


def test_unknown_target_skipped():
    print("\n[NationAgent.decide] an invented target is skipped, not persisted")
    _reset_db()
    seed_nations()

    from agents.nation import NationAgent
    from llm import gemini

    decision = {
        "action_type": "military_warning",
        "target_country": "Atlantis",  # not a real roster member
        "reasoning": "Testing unknown target handling.",
        "relation_delta": -8.0,
    }
    before = len(helpers.list_all_relations())
    original = gemini.generate
    gemini.generate = lambda *a, **k: json.dumps(decision)
    try:
        NationAgent.load_from_db("nation_china").decide()
    finally:
        gemini.generate = original

    after = helpers.list_all_relations()
    check("no new relation doc for invented target", len(after) == before,
          f"before={before} after={len(after)}")
    check("no 'Atlantis' key leaked",
          not any("Atlantis" in (r["source_agent_id"], r["target_agent_id"]) for r in after))


def test_engine_tick():
    print("\n[WorldEngine.step] a full tick keeps relations id-keyed")
    _reset_db()

    from engine.tick import WorldEngine
    from llm import gemini

    target_name = "Russia"
    target_id = NAME_TO_ID[target_name]

    # The engine now asks for every actor in one batched call, so the double
    # must answer in batch shape. It returns an entry for ALL ten actors --
    # including an inert one for the actor being targeted -- because the batch
    # prompt requires one object per actor and omitting any of them is what
    # triggers the straggler fallback (covered in test_batched_deliberation.py).
    def fake_generate(prompt="", **kwargs):
        decisions = []
        for n in STARTER_NATIONS:
            if n["name"] == target_name:
                decisions.append({
                    "agent_id": n["agent_id"],
                    "action_type": "ignore",
                    "target_country": "None",
                    "reasoning": "Subject of the event; no outbound action.",
                    "relation_delta": 0.0,
                })
            else:
                decisions.append({
                    "agent_id": n["agent_id"],
                    "action_type": "issue_statement",
                    "target_country": target_name,
                    "reasoning": "Coordinated response.",
                    "relation_delta": -3.0,
                })
        return json.dumps({"decisions": decisions})

    original = gemini.generate
    gemini.generate = fake_generate
    try:
        engine = WorldEngine(run_id="offline_test_run")
        engine.load_agents_from_db()
        summary = engine.step(custom_events=[{
            "headline": f"{target_name} masses troops near a contested border",
            "description": "Satellite imagery shows a buildup.",
            "source": "offline_test",
            "event_type": "manual_trigger",
        }])
    finally:
        gemini.generate = original

    check("tick produced a summary", summary["tick"] == 1)
    check("one batched call covered every actor",
          summary["deliberation_mode"] == "batch",
          f"mode={summary['deliberation_mode']}")
    check("relation_shifts recorded", len(summary["relation_shifts"]) > 0,
          f"shifts={len(summary['relation_shifts'])}")
    check("every shift carries a resolved target_id",
          all(s.get("target_id") == target_id for s in summary["relation_shifts"]))
    check("the targeted actor did not act against itself",
          all(s["source_id"] != target_id for s in summary["relation_shifts"]))
    check("no display-name relation docs after a tick", not stray_relation_docs(),
          f"offenders={json.dumps(stray_relation_docs()[:2])}")


def test_duplicate_event_skips_reasoning():
    print("\n[WorldEngine.step] a re-ingested article does not re-trigger reasoning")
    _reset_db()

    from engine.tick import WorldEngine
    from llm import gemini

    calls = {"n": 0}

    def counting_generate(prompt="", **kwargs):
        calls["n"] += 1
        return json.dumps({
            "decisions": [
                {
                    "agent_id": n["agent_id"],
                    "action_type": "ignore",
                    "target_country": "None",
                    "reasoning": "No material stake.",
                    "relation_delta": 0.0,
                }
                for n in STARTER_NATIONS
            ]
        })

    event = {
        "headline": "Trade ministers meet over semiconductor export controls",
        "description": "Talks continue.",
        "source": "offline_test",
        "event_type": "current_event",
        "external_id": "article-abc-123",
    }

    original = gemini.generate
    gemini.generate = counting_generate
    try:
        engine = WorldEngine(run_id="offline_dupe_run")
        engine.load_agents_from_db()
        first = engine.step(custom_events=[event])
        calls_after_first = calls["n"]
        second = engine.step(custom_events=[event])
    finally:
        gemini.generate = original

    check("first tick processed the event", not first.get("skipped"))
    check("second tick was skipped", second.get("skipped") is True,
          f"summary={second.get('skip_reason')}")
    check("second tick spent no Gemini calls", calls["n"] == calls_after_first,
          f"before={calls_after_first} after={calls['n']}")
    check("duplicate headline reported", second.get("duplicate_events"),
          f"got {second.get('duplicate_events')}")


def main():
    print("=" * 68)
    print("Offline regression: target_agent_id canonicalisation")
    print("=" * 68)

    for test in (
        test_resolver,
        test_seed_is_canonical,
        test_all_seeded_targets_resolve,
        test_purge_removes_previous_roster,
        test_update_relation_boundary,
        test_decide_persists_ids,
        test_unknown_target_skipped,
        test_engine_tick,
        test_duplicate_event_skips_reasoning,
    ):
        test()

    print("\n" + "=" * 68)
    print(f"RESULT: {_PASS} passed, {_FAIL} failed")
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
