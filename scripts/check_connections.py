#!/usr/bin/env python
"""Live preflight for the two external dependencies: MongoDB Atlas and Gemini.

Referenced from api/routes.py and llm/gemini.py. Unlike /health, this spends
real network calls and a handful of Gemini tokens, so it is run on demand
rather than on every request.

Run from the repo root with the venv active:

    python scripts/check_connections.py

Exit code 0 means both dependencies are usable for a live (non-demo) run.
Exit code 1 means at least one check failed; each failure prints the specific
misconfiguration rather than a stack trace.

No credential value is ever printed — only whether a variable is set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python scripts/check_connections.py` from the repo root without
# installing the project as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from db import mongo, schema  # noqa: E402
from llm import gemini  # noqa: E402

PASS = "  PASS"
FAIL = "  FAIL"
WARN = "  WARN"


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def check_env() -> bool:
    """Confirm the required variables are present before touching the network."""
    _rule("1. Environment")

    ok = True
    for name in ("MONGO_URI", "GEMINI_API_KEY"):
        if os.getenv(name):
            print(f"{PASS} {name} is set")
        else:
            print(f"{FAIL} {name} is missing — add it to .env (never commit .env)")
            ok = False

    print(f"       database:      {config.MONGO_DB_NAME}")
    print(f"       gemini model:  {config.GEMINI_MODEL}")
    print(f"       gemini rpm:    {config.GEMINI_MAX_RPM}")
    print(f"       relation push: {config.ENABLE_RELATION_STREAM}")
    print(f"       scheduled job: {config.ENABLE_SCHEDULED_INGESTION}")
    return ok


def check_mongo() -> bool:
    """Ping Atlas, then confirm the seeded collections are actually populated."""
    _rule("2. MongoDB Atlas")

    ok, error = mongo.ping()
    if not ok:
        print(f"{FAIL} cannot reach Atlas: {error}")
        print("       checklist: SRV string correct, password URL-encoded,")
        print("       and this machine's IP allowed under Atlas Network Access.")
        return False
    print(f"{PASS} ping succeeded")

    db = mongo.get_db()
    agents = db[schema.AGENTS].count_documents({})
    relations = db[schema.RELATIONS].count_documents({})
    events = db[schema.EVENTS].count_documents({})
    print(f"       agents={agents}  relations={relations}  events={events}")

    if agents == 0:
        print(f"{FAIL} no agents — run: python -m db.seed")
        return False

    # Relations are directed, so a fully seeded roster of N actors has N*(N-1)
    # rows. A short count means seeding was interrupted partway through.
    expected = agents * (agents - 1)
    if relations != expected:
        print(f"{WARN} expected {expected} directed relations for {agents} agents")
        print("       re-run `python -m db.seed` to rebuild the relation matrix")
    else:
        print(f"{PASS} relation matrix complete ({expected} directed rows)")

    return True


def check_relation_ids() -> bool:
    """Guard against the display-name-as-agent_id regression.

    Both endpoints of every relation must be a canonical agent_id present in the
    agents collection. A display name here is the bug that was root-caused in
    agents/nation.py; this check keeps it from silently coming back.
    """
    _rule("3. Relation id integrity")

    db = mongo.get_db()
    valid = {doc["agent_id"] for doc in db[schema.AGENTS].find({}, {"agent_id": 1})}
    if not valid:
        print(f"{WARN} no agents to validate against — skipped")
        return True

    bad: list[str] = []
    for rel in db[schema.RELATIONS].find({}, {"source_agent_id": 1, "target_agent_id": 1}):
        for field in ("source_agent_id", "target_agent_id"):
            value = rel.get(field)
            if value not in valid:
                bad.append(f"{field}={value!r}")

    if bad:
        print(f"{FAIL} {len(bad)} relation endpoint(s) are not canonical agent_ids")
        for item in bad[:8]:
            print(f"       {item}")
        print("       these are display names where an agent_id belongs")
        return False

    print(f"{PASS} every relation endpoint resolves to a known agent_id")
    return True


def check_gemini() -> bool:
    """Confirm the key calls a real model — not the demo-mode fallback."""
    _rule("4. Gemini API")

    if not os.getenv("GEMINI_API_KEY"):
        print(f"{FAIL} no API key, so agent reasoning would be mock output")
        return False

    try:
        models = gemini.list_available_models()
    except Exception as exc:
        print(f"{FAIL} cannot list models: {exc}")
        return False

    print(f"{PASS} key authenticated ({len(models)} models visible)")

    if config.GEMINI_MODEL in models:
        print(f"{PASS} GEMINI_MODEL={config.GEMINI_MODEL} is callable")
    else:
        usable = [m for m in config.GEMINI_FALLBACK_MODELS if m in models]
        print(f"{WARN} GEMINI_MODEL={config.GEMINI_MODEL} not available to this key")
        if usable:
            print(f"       will fall back to {usable[0]} — set that in .env to make it permanent")
        else:
            print(f"{FAIL} no configured fallback is available either")
            print(f"       pick one of: {', '.join(models[:6])}")
            return False

    # generate() falls back to a mock generator rather than raising, and the mock
    # is prefixed "[DEMO MODE". Without this check a successful-looking call
    # could still mean no live reasoning happened.
    try:
        reply = gemini.generate("Reply with the single word: ok")
    except Exception as exc:
        print(f"{FAIL} live generation failed: {exc}")
        return False

    if "[DEMO MODE" in reply:
        print(f"{FAIL} generation fell through to the demo generator")
        print("       agent output would be fabricated, not live reasoning")
        return False

    print(f"{PASS} live generation returned real model output")
    print(f"       active model: {gemini.active_model()}")
    return True


def main() -> int:
    print("=" * 68)
    print("World in Motion — live connection check")
    print("=" * 68)

    env_ok = check_env()
    mongo_ok = check_mongo()
    ids_ok = check_relation_ids() if mongo_ok else False
    gemini_ok = check_gemini()

    _rule("Summary")
    for label, ok in (
        ("environment", env_ok),
        ("mongodb atlas", mongo_ok),
        ("relation ids", ids_ok),
        ("gemini api", gemini_ok),
    ):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")

    if env_ok and mongo_ok and ids_ok and gemini_ok:
        print("\nReady for a live run: python app.py")
        return 0

    print("\nFix the FAIL lines above before running live.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
