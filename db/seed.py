"""Database seeding for the ten real-world actors the simulation models.

These are real states and blocs, so the persona fields below describe *structural
and stated policy interests* in neutral terms — the things that reliably shape a
government's behaviour (export dependencies, alliance commitments, supply-chain
exposure). They are deliberately not value judgements, and the `relations`
baselines are coarse starting positions for a simulation, not a scored index of
real diplomatic sentiment.

What the agents then produce is a *projection*: an LLM reasoning in character
about how a real actor might respond to a real headline. That output is
simulated commentary and must never be presented as actual government policy or
attributed as a real quote. The frontend carries a standing disclaimer to that
effect.

`aliases` is the entity-matching vocabulary used by ingestion/fetcher.py to
decide which actors a news article actually concerns. Matching on real names
("Beijing", "the White House", "Brussels") is what makes the pipeline
news-driven rather than keyword-guessed.
"""

import logging
from typing import Any

from agents.nation import NationAgent
from db import helpers, schema
from db.mongo import get_db
from db.schema import ensure_indexes


log = logging.getLogger(__name__)

STARTER_NATIONS: list[dict[str, Any]] = [
    {
        "agent_id": "nation_usa",
        "name": "United States",
        "persona": {
            "government_type": "Federal Presidential Republic",
            "core_interests": [
                "Dollar primacy and centrality in global finance",
                "Semiconductor and AI technology leadership",
                "Freedom of navigation and treaty alliance network",
                "Energy exports and critical mineral supply security",
            ],
            "allies": ["United Kingdom", "Japan", "European Union"],
            "rivals": ["Russia", "China"],
            "aliases": [
                # "us" is here deliberately: aliases of three or fewer
                # alphanumerics are matched case-sensitively in uppercase by
                # ingestion/fetcher.py, so this catches "US and China agree..."
                # (the most common wire-copy form) without matching the pronoun.
                "united states", "us", "u.s.", "u.s.a.", "usa", "america", "american",
                "washington", "white house", "state department", "pentagon",
                "federal reserve", "congress", "biden", "trump",
            ],
            "relations": {
                "United Kingdom": 85.0,
                "Japan": 80.0,
                "European Union": 75.0,
                "India": 45.0,
                "Gulf States": 40.0,
                "Brazil": 25.0,
                "Turkey": 15.0,
                "China": -45.0,
                "Russia": -70.0,
            },
        },
    },
    {
        "agent_id": "nation_china",
        "name": "China",
        "persona": {
            "government_type": "Single-Party Socialist Republic",
            "core_interests": [
                "Manufacturing scale and export competitiveness",
                "Semiconductor self-sufficiency and AI capability",
                "Belt and Road infrastructure and trade corridors",
                "Territorial claims and regional maritime influence",
            ],
            "allies": ["Russia"],
            "rivals": ["United States", "Japan"],
            "aliases": [
                "china", "chinese", "beijing", "prc", "people's republic of china",
                "xi jinping", "shanghai", "shenzhen", "hong kong", "taiwan strait",
                "communist party of china", "cpc",
            ],
            "relations": {
                "Russia": 60.0,
                "Gulf States": 40.0,
                "Brazil": 35.0,
                "Turkey": 15.0,
                "European Union": -15.0,
                "United Kingdom": -25.0,
                "India": -30.0,
                "Japan": -35.0,
                "United States": -45.0,
            },
        },
    },
    {
        "agent_id": "nation_russia",
        "name": "Russia",
        "persona": {
            "government_type": "Federal Semi-Presidential Republic",
            "core_interests": [
                "Hydrocarbon export revenue and pipeline leverage",
                "Near-abroad security buffer and regional influence",
                "Sanctions resilience and alternative payment rails",
                "Arms exports and strategic nuclear parity",
            ],
            "allies": ["China"],
            "rivals": ["United States", "European Union", "United Kingdom"],
            "aliases": [
                "russia", "russian", "moscow", "kremlin", "putin",
                "russian federation", "gazprom", "rosneft", "st petersburg",
            ],
            "relations": {
                "China": 60.0,
                "India": 40.0,
                "Gulf States": 25.0,
                "Brazil": 20.0,
                "Turkey": 10.0,
                "Japan": -45.0,
                "United States": -70.0,
                "European Union": -75.0,
                "United Kingdom": -80.0,
            },
        },
    },
    {
        "agent_id": "nation_eu",
        "name": "European Union",
        "persona": {
            "government_type": "Supranational Union of Member States",
            "core_interests": [
                "Single market regulatory power and standards-setting",
                "Energy diversification away from single suppliers",
                "Climate transition and carbon border adjustment",
                "Strategic autonomy in defence and technology",
            ],
            "allies": ["United States", "Japan", "United Kingdom"],
            "rivals": ["Russia"],
            "aliases": [
                "european union", "eu", "brussels", "european commission",
                "european parliament", "eurozone", "european central bank", "ecb",
                "germany", "german", "berlin", "france", "french", "paris",
                "italy", "italian", "rome", "spain", "spanish", "madrid",
                "netherlands", "poland", "warsaw",
            ],
            "relations": {
                "United States": 75.0,
                "Japan": 70.0,
                "United Kingdom": 55.0,
                "India": 35.0,
                "Brazil": 30.0,
                "Gulf States": 25.0,
                "Turkey": 5.0,
                "China": -15.0,
                "Russia": -75.0,
            },
        },
    },
    {
        "agent_id": "nation_india",
        "name": "India",
        "persona": {
            "government_type": "Federal Parliamentary Republic",
            "core_interests": [
                "Strategic autonomy and deliberate multi-alignment",
                "Manufacturing scale-up and digital public infrastructure",
                "Affordable energy imports",
                "Border security and Indian Ocean influence",
            ],
            "allies": ["Japan", "United States"],
            "rivals": ["China"],
            "aliases": [
                "india", "indian", "new delhi", "delhi", "modi", "mumbai",
                "bengaluru", "bangalore", "reserve bank of india",
            ],
            "relations": {
                "Japan": 60.0,
                "Gulf States": 50.0,
                "United States": 45.0,
                "United Kingdom": 45.0,
                "Brazil": 40.0,
                "Russia": 40.0,
                "European Union": 35.0,
                "Turkey": -15.0,
                "China": -30.0,
            },
        },
    },
    {
        "agent_id": "nation_japan",
        "name": "Japan",
        "persona": {
            "government_type": "Constitutional Monarchy with Parliamentary Democracy",
            "core_interests": [
                "Sea lane security and stable energy imports",
                "Advanced manufacturing, robotics and materials leadership",
                "Demographic decline and economic revitalisation",
                "Alliance with the United States and regional deterrence",
            ],
            "allies": ["United States", "European Union", "United Kingdom"],
            "rivals": ["China", "Russia"],
            "aliases": [
                "japan", "japanese", "tokyo", "bank of japan", "nikkei",
                "kishida", "yen",
            ],
            "relations": {
                "United States": 80.0,
                "European Union": 70.0,
                "India": 60.0,
                "United Kingdom": 60.0,
                "Gulf States": 30.0,
                "Brazil": 25.0,
                "Turkey": 15.0,
                "China": -35.0,
                "Russia": -45.0,
            },
        },
    },
    {
        "agent_id": "nation_uk",
        "name": "United Kingdom",
        "persona": {
            "government_type": "Constitutional Monarchy with Parliamentary Democracy",
            "core_interests": [
                "Financial services and the City of London",
                "Post-Brexit trade agreements and market access",
                "NATO commitment and intelligence partnerships",
                "Defence technology and the nuclear deterrent",
            ],
            "allies": ["United States", "Japan", "European Union"],
            "rivals": ["Russia"],
            "aliases": [
                "united kingdom", "uk", "britain", "british", "london",
                "downing street", "westminster", "bank of england", "whitehall",
                "england", "scotland", "wales",
            ],
            "relations": {
                "United States": 85.0,
                "Japan": 60.0,
                "European Union": 55.0,
                "India": 45.0,
                "Gulf States": 35.0,
                "Brazil": 25.0,
                "Turkey": 20.0,
                "China": -25.0,
                "Russia": -80.0,
            },
        },
    },
    {
        "agent_id": "nation_brazil",
        "name": "Brazil",
        "persona": {
            "government_type": "Federal Presidential Republic",
            "core_interests": [
                "Agricultural commodity exports",
                "Amazon sovereignty and climate finance",
                "Non-aligned South-South diplomacy and BRICS",
                "Industrial policy and energy self-sufficiency",
            ],
            "allies": ["India"],
            "rivals": [],
            "aliases": [
                "brazil", "brazilian", "brasilia", "sao paulo", "são paulo",
                "lula", "petrobras", "amazon rainforest", "mercosur",
            ],
            "relations": {
                "India": 40.0,
                "China": 35.0,
                "European Union": 30.0,
                "United States": 25.0,
                "Japan": 25.0,
                "United Kingdom": 25.0,
                "Russia": 20.0,
                "Gulf States": 20.0,
                "Turkey": 15.0,
            },
        },
    },
    {
        "agent_id": "nation_turkey",
        "name": "Turkey",
        "persona": {
            "government_type": "Presidential Republic",
            "core_interests": [
                "Straits control and regional power projection",
                "NATO membership alongside an independent foreign policy",
                "Defence industry exports and drone technology",
                "Currency stability and inflation management",
            ],
            "allies": ["Gulf States"],
            "rivals": [],
            "aliases": [
                "turkey", "türkiye", "turkiye", "turkish", "ankara", "istanbul",
                "erdogan", "erdoğan", "bosphorus", "dardanelles", "lira",
            ],
            "relations": {
                "Gulf States": 25.0,
                "United Kingdom": 20.0,
                "United States": 15.0,
                "China": 15.0,
                "Japan": 15.0,
                "Brazil": 15.0,
                "Russia": 10.0,
                "European Union": 5.0,
                "India": -15.0,
            },
        },
    },
    {
        "agent_id": "nation_gulf",
        "name": "Gulf States",
        "persona": {
            "government_type": "Bloc of Gulf Cooperation Council Monarchies",
            "core_interests": [
                "Hydrocarbon revenue and OPEC+ production policy",
                "Sovereign wealth diversification and post-oil economies",
                "Regional security and maritime chokepoint stability",
                "Logistics, aviation and financial hub development",
            ],
            "allies": ["United States", "India"],
            "rivals": [],
            "aliases": [
                "gulf states", "gcc", "gulf cooperation council",
                "saudi arabia", "saudi", "riyadh", "aramco",
                "united arab emirates", "uae", "abu dhabi", "dubai",
                "qatar", "doha", "kuwait", "bahrain", "oman",
                "opec", "strait of hormuz",
            ],
            "relations": {
                "India": 50.0,
                "United States": 40.0,
                "China": 40.0,
                "United Kingdom": 35.0,
                "Japan": 30.0,
                "European Union": 25.0,
                "Russia": 25.0,
                "Turkey": 25.0,
                "Brazil": 20.0,
            },
        },
    },
]

#: Canonical roster ids, used to detect and purge agents from a previous roster.
ROSTER_IDS: set[str] = {n["agent_id"] for n in STARTER_NATIONS}

#: Display name -> agent_id, for callers that need the mapping without a DB hit.
NAME_TO_ID: dict[str, str] = {n["name"]: n["agent_id"] for n in STARTER_NATIONS}


def purge_retired_agents() -> int:
    """Delete agents (and their relations) that are not in the current roster.

    The project previously modelled six fictional nations. Because seeding is an
    upsert, without this step switching rosters would leave the retired nations
    in the graph alongside the new ones and the frontend would render both sets
    as if they coexisted. Safe to call repeatedly.
    """
    db = get_db()
    stale = [
        doc["agent_id"]
        for doc in db[schema.AGENTS].find({}, {"agent_id": 1, "_id": 0})
        if doc.get("agent_id") and doc["agent_id"] not in ROSTER_IDS
    ]
    if not stale:
        return 0

    db[schema.AGENTS].delete_many({"agent_id": {"$in": stale}})
    db[schema.RELATIONS].delete_many({"source_agent_id": {"$in": stale}})
    db[schema.RELATIONS].delete_many({"target_agent_id": {"$in": stale}})
    db[schema.MEMORY].delete_many({"agent_id": {"$in": stale}})
    helpers.invalidate_agent_index()

    log.warning("Purged %d retired agents from a previous roster: %s", len(stale), ", ".join(stale))
    return len(stale)


def seed_nations(purge_retired: bool = True) -> list[NationAgent]:
    """Seed the roster and its initial pairwise relations into MongoDB.

    Deliberately multi-pass: every agent document must exist before any relation
    is written, otherwise the name -> agent_id resolver cannot canonicalise
    targets that have not been inserted yet (the first nation's relations all
    point at nations seeded after it).
    """
    db = get_db()
    ensure_indexes(db)

    # --- Pass 0: clear out any previous roster ---
    if purge_retired:
        purge_retired_agents()

    # --- Pass 1: persist every agent so the alias index is complete ---
    seeded_agents = []
    for data in STARTER_NATIONS:
        agent = NationAgent(
            agent_id=data["agent_id"],
            name=data["name"],
            persona=data["persona"],
            recent_memory=[],
        )
        agent.save_to_db()
        seeded_agents.append(agent)
        log.info("Seeded nation agent: %s (%s)", agent.name, agent.agent_id)

    # Force the resolver to pick up the roster we just wrote.
    helpers.invalidate_agent_index()

    # --- Pass 2: seed pairwise relations keyed by canonical agent_id ---
    relations_written = 0
    for data in STARTER_NATIONS:
        source_id = data["agent_id"]
        relations = data["persona"].get("relations", {})
        for target_name, score in relations.items():
            target_id = helpers.resolve_agent_id(target_name)
            if target_id is None:
                log.warning(
                    "Seed: target %r for %s does not match any agent; skipping",
                    target_name,
                    source_id,
                )
                continue
            helpers.update_relation(
                source_agent_id=source_id,
                target_agent_id=target_id,
                score=score,
                reasoning="Initial baseline relation standing",
            )
            relations_written += 1

    print(
        f"Successfully seeded {len(seeded_agents)} nation agents "
        f"and {relations_written} relations into MongoDB."
    )
    return seeded_agents


if __name__ == "__main__":
    import config
    config.configure_logging()
    seed_nations()
