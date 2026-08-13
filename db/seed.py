"""Database seeding script for starter nation agents.

Populates the MongoDB `agents` collection with 5-6 starter nations featuring
distinct geopolitical personas, interests, allegiances, and relation baselines.
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
        "agent_id": "nation_eldoria",
        "name": "Republic of Eldoria",
        "persona": {
            "government_type": "Democratic Republic",
            "core_interests": [
                "Maritime trade routes",
                "Renewable energy infrastructure",
                "Democratic alliance solidarity",
                "Free press and open diplomacy",
            ],
            "allies": ["Solaria Federation", "Aethelgard Confederation"],
            "rivals": ["Ironreach Dominion", "Valerius Empire"],
            "relations": {
                "Solaria Federation": 50.0,
                "Aethelgard Confederation": 25.0,
                "Verdant Union": 30.0,
                "Valerius Empire": -15.0,
                "Ironreach Dominion": -45.0,
            },
        },
    },
    {
        "agent_id": "nation_ironreach",
        "name": "Ironreach Dominion",
        "persona": {
            "government_type": "Military Autocracy",
            "core_interests": [
                "Territorial defense and expansion",
                "Mineral and raw metal security",
                "Military tech superiority",
                "Strict border control and sovereignty",
            ],
            "allies": ["Valerius Empire"],
            "rivals": ["Republic of Eldoria", "Solaria Federation"],
            "relations": {
                "Valerius Empire": 45.0,
                "Aethelgard Confederation": 0.0,
                "Verdant Union": -20.0,
                "Republic of Eldoria": -45.0,
                "Solaria Federation": -55.0,
            },
        },
    },
    {
        "agent_id": "nation_solaria",
        "name": "Solaria Federation",
        "persona": {
            "government_type": "Technocratic High Council",
            "core_interests": [
                "Artificial Intelligence dominance",
                "Cybersecurity and quantum infrastructure",
                "Global semiconductor supply chains",
                "Intellectual property protection",
            ],
            "allies": ["Republic of Eldoria"],
            "rivals": ["Ironreach Dominion"],
            "relations": {
                "Republic of Eldoria": 50.0,
                "Aethelgard Confederation": 35.0,
                "Verdant Union": 20.0,
                "Valerius Empire": -30.0,
                "Ironreach Dominion": -55.0,
            },
        },
    },
    {
        "agent_id": "nation_verdant",
        "name": "Verdant Union",
        "persona": {
            "government_type": "Environmental Eco-State",
            "core_interests": [
                "Carbon emission neutrality",
                "Agricultural sustainability",
                "Biodiversity protection",
                "Global demilitarization treaties",
            ],
            "allies": ["Aethelgard Confederation"],
            "rivals": ["Valerius Empire", "Ironreach Dominion"],
            "relations": {
                "Aethelgard Confederation": 45.0,
                "Republic of Eldoria": 30.0,
                "Solaria Federation": 20.0,
                "Ironreach Dominion": -20.0,
                "Valerius Empire": -50.0,
            },
        },
    },
    {
        "agent_id": "nation_valerius",
        "name": "Valerius Empire",
        "persona": {
            "government_type": "Imperial Monarchy",
            "core_interests": [
                "Fossil & nuclear fuel monopolies",
                "Import tariff protectionism",
                "Historical imperial sovereignty",
                "Deep-sea drilling rights",
            ],
            "allies": ["Ironreach Dominion"],
            "rivals": ["Verdant Union", "Solaria Federation"],
            "relations": {
                "Ironreach Dominion": 45.0,
                "Aethelgard Confederation": -10.0,
                "Republic of Eldoria": -15.0,
                "Solaria Federation": -30.0,
                "Verdant Union": -50.0,
            },
        },
    },
    {
        "agent_id": "nation_aethelgard",
        "name": "Aethelgard Confederation",
        "persona": {
            "government_type": "Non-Aligned Mercantile Confederation",
            "core_interests": [
                "Neutral international banking",
                "Rare earth element trade arbitration",
                "Geopolitical non-alignment",
                "Cross-border logistics hubs",
            ],
            "allies": ["Verdant Union", "Republic of Eldoria"],
            "rivals": [],
            "relations": {
                "Verdant Union": 45.0,
                "Solaria Federation": 35.0,
                "Republic of Eldoria": 25.0,
                "Ironreach Dominion": 0.0,
                "Valerius Empire": -10.0,
            },
        },
    },
]


def seed_nations() -> list[NationAgent]:
    """Seed starter nations and initial pairwise relations into MongoDB."""
    db = get_db()
    ensure_indexes(db)

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

        # Seed pairwise relations in `relations` collection
        source_id = data["agent_id"]
        relations = data["persona"].get("relations", {})
        for target_name, score in relations.items():
            # Find target agent_id by name if available, or use target_name
            helpers.update_relation(
                source_agent_id=source_id,
                target_agent_id=target_name,
                score=score,
                reasoning="Initial baseline relation standing",
            )

    print(f"Successfully seeded {len(seeded_agents)} starter nation agents and relations into MongoDB.")
    return seeded_agents


if __name__ == "__main__":
    import config
    from db import helpers
    config.configure_logging()
    seed_nations()

