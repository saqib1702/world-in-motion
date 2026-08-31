"""Offline regression suite for news -> actor entity matching.

The ingestion pipeline decides which nation agents react to an article. Getting
this wrong is expensive in both directions: a miss means a major story moves no
relations, and a false positive spends a Gemini call and shifts scores based on
an article about a Thanksgiving bird.

Runs with only the standard library — tests/_fakes.py stands in for pymongo, and
nothing here touches the network.

Run:  python -m tests.test_entity_matching
Exit code 0 = all green.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import _fakes  # noqa: E402

_fakes.install_stub_modules()

os.environ.setdefault("MONGO_URI", "mongodb://fake-local/testdb")
os.environ.setdefault("MONGO_DB_NAME", "world_in_motion_test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-a-real-secret")

from db.seed import STARTER_NATIONS  # noqa: E402
from ingestion import fetcher  # noqa: E402

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


def _match(title, body=""):
    """Run one headline through the real relevance filter."""
    return fetcher._relevant_agents(
        {"title": title, "body": body, "url": "", "raw": {}}
    )


#: (headline, actors that must be found). Written the way wire copy actually
#: writes them, including the bare "US" form.
POSITIVES = [
    ("US and China agree to pause tariff escalation after Geneva talks",
     ["nation_usa", "nation_china"]),
    ("U.S. and EU launch trade talks on steel quotas",
     ["nation_usa", "nation_eu"]),
    ("EU announces new sanctions package targeting Russian energy exports",
     ["nation_eu", "nation_russia"]),
    ("India and Japan sign semiconductor supply chain pact",
     ["nation_india", "nation_japan"]),
    ("Turkey brokers grain corridor deal with Russia amid Black Sea talks",
     ["nation_turkey", "nation_russia"]),
    ("Saudi Arabia signals OPEC output shift as oil prices slide",
     ["nation_gulf"]),
    ("Brazil hosts climate summit on Amazon emissions financing",
     ["nation_brazil"]),
    ("China and Japan hold defence talks amid territorial dispute",
     ["nation_china", "nation_japan"]),
    ("Washington and Beijing resume military dialogue",
     ["nation_usa", "nation_china"]),
    ("Britain summons ambassador over airspace violation",
     ["nation_uk"]),
]

#: Must match nothing. The first three name a real actor but carry no
#: geopolitical term; the rest exercise the short-alias case-sensitivity guard,
#: where a naive lowercase match would fire on "us", "bus", "Plus", "eu".
NEGATIVES = [
    "Turkey recipes for a perfect Thanksgiving dinner",
    "Taylor Swift announces UK tour dates",
    "China Garden restaurant reopens after renovation",
    "us and our friends reached the summit of the mountain",
    "The bus and train tariff changes take effect Monday",
    "Plus and minus signs confuse new trade students",
    "Local council approves new bike lane downtown",
]


def test_positives():
    print("\n[entity match] headlines that must reach the right actors")
    for title, expected in POSITIVES:
        got = _match(title)
        missing = [a for a in expected if a not in got]
        check(
            f"{title[:52]}",
            not missing,
            f"missing {missing}, got {got}",
        )


def test_negatives():
    print("\n[entity match] headlines that must move nothing")
    for title in NEGATIVES:
        got = _match(title)
        check(f"{title[:52]}", not got, f"unexpectedly matched {got}")


def test_short_alias_case_sensitivity():
    """Aliases of <=3 alphanumerics must match uppercase only.

    Without this, "us"/"eu"/"uk" match inside ordinary words and every article
    in the feed looks relevant to three actors at once.
    """
    print("\n[aliases] short forms are case-sensitive, long forms are not")
    pattern_us, cs_us = fetcher._alias_pattern("us")
    check("'us' is treated case-sensitively", cs_us is True)
    check("'US' matches", bool(pattern_us.search("US imposes controls")))
    check("lowercase 'us' does not match", not pattern_us.search("us and them"))
    check("'bus' does not match", not pattern_us.search("The BUS arrived"))

    pattern_long, cs_long = fetcher._alias_pattern("united states")
    check("'united states' is case-insensitive", cs_long is False)
    check(
        "matches regardless of case",
        bool(pattern_long.search("the united states said")),
    )


def test_every_actor_is_reachable():
    """Each seeded actor must be findable by at least one of its own aliases.

    Catches an actor added to the roster with an alias list that cannot actually
    be hit — it would then never react to anything.
    """
    print("\n[coverage] every seeded actor can be matched by name")
    for nation in STARTER_NATIONS:
        agent_id = nation["agent_id"]
        name = nation["name"]
        got = _match(f"{name} announces new trade sanctions")
        check(f"{name} is reachable", agent_id in got, f"got {got}")


def test_geopolitical_gate_is_required():
    print("\n[gate] a named actor without a geopolitical term is ignored")
    check(
        "actor alone does not qualify",
        not _match("China Garden restaurant reopens"),
    )
    check(
        "actor plus a geopolitical term does",
        "nation_china" in _match("China announces export controls"),
    )


def test_body_text_is_searched():
    print("\n[fields] the article body counts, not just the headline")
    got = _match(
        "Markets rally on trade news",
        body="Officials in Tokyo and New Delhi signed the accord.",
    )
    check("japan found in body", "nation_japan" in got, f"got {got}")
    check("india found in body", "nation_india" in got, f"got {got}")


def main():
    print("=" * 68)
    print("Entity matching regression suite")
    print("=" * 68)

    test_positives()
    test_negatives()
    test_short_alias_case_sensitivity()
    test_every_actor_is_reachable()
    test_geopolitical_gate_is_required()
    test_body_text_is_searched()

    print("\n" + "=" * 68)
    print(f"RESULT: {_PASS} passed, {_FAIL} failed")
    print("=" * 68)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
