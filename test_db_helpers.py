"""Verification script for MongoDB schema, helper functions, and NationAgent integration."""

import os
import sys

from db.mongo import get_db
from db.schema import ensure_indexes
from db import helpers, schema
from agents.nation import NationAgent


def run_tests():
    print("--- 1. Testing Index Creation ---")
    db = get_db()
    ensure_indexes(db)
    print("Indexes created successfully.")

    print("\n--- 2. Testing Agents Collection Helpers ---")
    agent_doc = helpers.upsert_agent(
        agent_id="nation_testland",
        name="Testlandia",
        persona={"government_type": "Democracy", "core_interests": ["Trade"]},
        state={"status": "active"},
    )
    fetched_agent = helpers.get_agent("nation_testland")
    assert fetched_agent is not None, "Failed to retrieve agent"
    assert fetched_agent["name"] == "Testlandia"
    print("Agent helper tests passed.")

    print("\n--- 3. Testing Relations Collection Helpers ---")
    rel_doc = helpers.update_relation(
        source_agent_id="nation_testland",
        target_agent_id="nation_eldoria",
        score=25.0,
        reasoning="Signed alliance agreement",
    )
    fetched_rel = helpers.get_relation("nation_testland", "nation_eldoria")
    assert fetched_rel["score"] == 25.0, "Failed relation score check"

    # Test relation delta update
    updated_rel = helpers.update_relation(
        source_agent_id="nation_testland",
        target_agent_id="nation_eldoria",
        delta=-10.0,
        reasoning="Minor dispute",
    )
    assert updated_rel["score"] == 15.0, f"Expected 15.0, got {updated_rel['score']}"
    print("Relations helper tests passed.")

    print("\n--- 4. Testing Events Collection Helpers ---")
    evt_id = helpers.log_event(
        headline="Testlandia enters trade pact",
        description="A major trade agreement was announced today.",
        involved_agents=["nation_testland", "nation_eldoria"],
    )
    assert evt_id.startswith("evt_"), "Invalid event ID"
    recent_evts = helpers.get_recent_events(limit=5)
    assert len(recent_evts) > 0
    print("Events helper tests passed.")

    print("\n--- 5. Testing Memory Collection Helpers ---")
    mem_doc = helpers.add_agent_memory(
        agent_id="nation_testland",
        memory_type="observation",
        content={"summary": "Observed trade shift"},
    )
    memories = helpers.get_agent_memories("nation_testland", limit=5)
    assert len(memories) > 0
    print("Memory helper tests passed.")

    print("\n--- 6. Testing NationAgent perceive() Integration ---")
    agent = NationAgent(
        agent_id="nation_testland",
        name="Testlandia",
        persona={"government_type": "Democracy", "core_interests": ["Trade"], "relations": {"nation_eldoria": 15.0}},
    )
    agent.perceive({"headline": "Global Market Shift", "description": "Prices spike globally."})
    assert len(agent.recent_memory) > 0
    print("NationAgent perceive integration passed.")

    print("\nALL MONGO SCHEMA & HELPER TESTS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
