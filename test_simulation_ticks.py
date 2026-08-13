"""Test script for running 3 manual simulation ticks with fabricated events.

Demonstrates parallel Gemini agent reasoning, action generation, decision logging,
and dynamic pairwise relation shifts across 6 starter nation agents.
"""

import json
import logging
from engine.tick import WorldEngine
from db.seed import seed_nations
from db import helpers

# Configure clean console logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


FABRICATED_EVENTS = [
    [
        {
            "headline": "Republic of Eldoria announces 25% trade tariffs on Ironreach Dominion maritime cargo.",
            "description": "In response to border mineral disputes, Eldoria's parliament passed sweeping maritime tariffs.",
            "involved_agents": ["nation_eldoria", "nation_ironreach"],
        }
    ],
    [
        {
            "headline": "Ironreach Dominion conducts emergency naval deployment near Solaria Federation waters.",
            "description": "Citing defense posture and cyber intelligence threats, Ironreach battleships mobilised in international channels.",
            "involved_agents": ["nation_ironreach", "nation_solaria"],
        }
    ],
    [
        {
            "headline": "Aethelgard Confederation proposes neutral peace treaty and economic zone for Eldoria and Ironreach.",
            "description": "Aethelgard's High Arbiter submitted a 10-point diplomatic roadmap offering banking tax exemptions.",
            "involved_agents": ["nation_aethelgard", "nation_eldoria", "nation_ironreach"],
        }
    ],
]


def run_tick_demo():
    print("=" * 80)
    print("      WORLD IN MOTION SIMULATION ENGINE — 3-TICK SIMULATION DEMO      ")
    print("=" * 80)

    print("\n[Step 1] Seeding starter nations into MongoDB...")
    seed_nations()

    print("\n[Step 2] Initializing WorldEngine...")
    engine = WorldEngine(run_id="demo_tariffs_run", max_workers=6)
    engine.load_agents_from_db()

    print(f"Engine ready with {len(engine.agents)} agents.\n")

    for idx, events in enumerate(FABRICATED_EVENTS, start=1):
        print("-" * 80)
        print(f"   >>> EXECUTING TICK {idx} <<<")
        print("-" * 80)
        print(f"EVENT: {events[0]['headline']}")

        # Run single manual tick with the fabricated event
        summary = engine.step(custom_events=events)

        print(f"\n--- TICK {summary['tick']} AGENT ACTIONS ---")
        for action in summary["actions_taken"]:
            print(f"• Agent:        {action['agent_name']}")
            print(f"  Action Type:  {action['action_type']}")
            print(f"  Target:       {action['target_country']}")
            print(f"  Relation Shift: {action['relation_delta']:+.1f}")
            print(f"  Reasoning:    {action['reasoning']}\n")

        print(f"--- TICK {summary['tick']} RELATION SHIFTS ---")
        if summary["relation_shifts"]:
            for shift in summary["relation_shifts"]:
                print(
                    f"  {shift['source']} -> {shift['target']}: "
                    f"delta {shift['delta']:+.1f} (New standing: {shift['new_score']:.1f})"
                )
        else:
            print("  No relation shifts recorded this tick.")
        print("\n")

    print("=" * 80)
    print("                  FINAL DIPLOMATIC RELATIONS SUMMARY                 ")
    print("=" * 80)
    all_relations = helpers.list_all_relations()
    for rel in all_relations[:15]:
        print(f"  [{rel.get('source_agent_id')}] -> [{rel.get('target_agent_id')}]: score={rel.get('score', 0.0):.1f} (last_delta={rel.get('last_delta', 0.0):+.1f})")

    print("\n3-Tick Simulation Test Completed Successfully!")


if __name__ == "__main__":
    run_tick_demo()
