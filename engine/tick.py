"""The world tick and simulation engine.

Drives the simulation loop:
1. Pulls latest events (from ingestion, DB, or manual triggers).
2. Runs perception and Gemini decision-making for each nation agent in parallel.
3. Applies relation deltas to the `relations` collection in MongoDB.
4. Logs full turn to `ticks` and `events`.
5. Returns a structured summary of what changed during the tick.

Can be run as a single manual tick or as a continuous loop on an interval.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agents.base import Observation
from agents.nation import NationAgent
from db import helpers, schema
from db.mongo import get_db

log = logging.getLogger(__name__)


def _process_single_agent(agent: NationAgent, obs: Observation) -> dict[str, Any]:
    """Helper executed in worker thread: run perceive and decide for one agent."""
    try:
        agent.perceive(obs)
        decision = agent.decide()
        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "decision": decision,
            "status": "success",
        }
    except Exception as exc:
        log.error("Error processing agent [%s] in tick: %s", agent.name, exc, exc_info=True)
        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "decision": {
                "action_type": "ignore",
                "target_country": "None",
                "reasoning": f"Execution error: {exc}",
                "relation_delta": 0.0,
            },
            "status": "error",
        }


@dataclass
class WorldEngine:
    """Drives the simulation loop for a single run."""

    db: Optional[Any] = None
    run_id: str = "default_run"
    tick: int = 0
    agents: list[NationAgent] = field(default_factory=list)
    max_workers: int = 6

    def __post_init__(self):
        if self.db is None:
            self.db = get_db()

    def load_agents_from_db(self) -> list[NationAgent]:

        """Load active nation agents from MongoDB, or seed starter nations if empty."""
        agent_docs = helpers.list_agents(agent_type="nation")
        if not agent_docs:
            log.info("No nation agents found in DB. Seeding starter nations...")
            from db.seed import seed_nations
            self.agents = seed_nations()
        else:
            self.agents = [NationAgent.from_doc(doc) for doc in agent_docs]
        log.info("Loaded %d nation agents for run [%s]", len(self.agents), self.run_id)
        return self.agents

    def step(self, custom_events: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Advance the world by one tick and return a summary of what changed.

        Args:
            custom_events: Optional list of event dicts (e.g. fabricated headline dicts) to process.

        Returns:
            Structured tick summary dict.
        """
        self.tick += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        log.info("=== Starting Tick %d (run_id=%s) ===", self.tick, self.run_id)

        # Ensure agents are loaded
        if not self.agents:
            self.load_agents_from_db()

        # 1. Process & log new events
        events_for_tick: list[dict[str, Any]] = []
        if custom_events:
            for evt in custom_events:
                headline = evt.get("headline", evt.get("title", "World Event"))
                desc = evt.get("description", evt.get("body", ""))
                involved = evt.get("involved_agents", [a.agent_id for a in self.agents])
                evt_id = helpers.log_event(
                    headline=headline,
                    description=desc,
                    event_type=evt.get("event_type", "manual_trigger"),
                    source=evt.get("source", "manual"),
                    involved_agents=involved,
                    payload=evt,
                )
                events_for_tick.append({
                    "event_id": evt_id,
                    "headline": headline,
                    "description": desc,
                    "involved_agents": involved,
                })
        else:
            recent_db_events = helpers.get_recent_events(limit=3)
            events_for_tick = recent_db_events or [{"headline": f"Tick {self.tick} Geopolitical Standstill", "description": "No major news event."}]

        # 2. Build Observation
        obs = Observation(
            tick=self.tick,
            world_state={"run_id": self.run_id, "active_agents": len(self.agents)},
            events=events_for_tick,
        )

        # 3. Parallel perception & Gemini decision across all agents
        log.info("Dispatching decision calls across %d agents (max_workers=%d)...", len(self.agents), self.max_workers)
        agent_results: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_agent = {
                executor.submit(_process_single_agent, agent, obs): agent
                for agent in self.agents
            }
            for future in as_completed(future_to_agent):
                res = future.result()
                agent_results.append(res)

        # Sort results by agent name for deterministic order
        agent_results.sort(key=lambda x: x["agent_name"])

        # 4. Process decisions, aggregate relation shifts, and log reactions
        actions_taken = []
        relation_shifts = []

        for res in agent_results:
            agent_id = res["agent_id"]
            agent_name = res["agent_name"]
            decision = res["decision"]

            action_type = decision.get("action_type", "ignore")
            target = decision.get("target_country", "None")
            reasoning = decision.get("reasoning", "")
            delta = float(decision.get("relation_delta", 0.0))

            actions_taken.append({
                "agent_id": agent_id,
                "agent_name": agent_name,
                "action_type": action_type,
                "target_country": target,
                "reasoning": reasoning,
                "relation_delta": delta,
            })

            # Record relation shift details if target specified
            if target and target.lower() != "none" and delta != 0.0:
                rel_info = helpers.get_relation(agent_id, target)
                relation_shifts.append({
                    "source": agent_name,
                    "source_id": agent_id,
                    "target": target,
                    "delta": delta,
                    "new_score": rel_info.get("score", 0.0),
                    "reasoning": reasoning,
                })

            # Attach reactions to event docs in MongoDB
            for evt in events_for_tick:
                evt_id = evt.get("event_id")
                if evt_id:
                    helpers.log_agent_reaction(
                        event_id=evt_id,
                        agent_id=agent_id,
                        action_type=action_type,
                        reasoning=reasoning,
                        relation_delta=delta,
                        target_country=target,
                    )

        # 5. Compile & persist tick summary
        summary = {
            "run_id": self.run_id,
            "tick": self.tick,
            "timestamp": now_iso,
            "events_processed": [e.get("headline") for e in events_for_tick],
            "actions_taken": actions_taken,
            "relation_shifts": relation_shifts,
        }

        try:
            self.db[schema.TICKS].insert_one(dict(summary))
        except Exception as exc:
            log.warning("Failed to insert tick summary to MongoDB: %s", exc)

        log.info(
            "=== Tick %d Complete: %d events, %d actions, %d relation shifts ===",
            self.tick,
            len(events_for_tick),
            len(actions_taken),
            len(relation_shifts),
        )
        return summary

    def run(self, ticks: int, custom_events: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """Run `ticks` consecutive manual steps synchronously."""
        return [self.step(custom_events=custom_events) for _ in range(ticks)]

    def run_loop(
        self,
        interval_seconds: float = 5.0,
        max_ticks: Optional[int] = None,
        stop_event: Optional[Any] = None,
    ) -> list[dict[str, Any]]:
        """Run continuous simulation loop on an interval.

        Args:
            interval_seconds: Delay between ticks in seconds.
            max_ticks: Optional upper limit on number of ticks to run.
            stop_event: Optional threading.Event instance to signal termination.

        Returns:
            List of summaries produced during the run loop.
        """
        log.info("Starting simulation loop (interval=%.1fs, max_ticks=%s)", interval_seconds, max_ticks)
        summaries = []
        ticks_run = 0

        while True:
            if stop_event and stop_event.is_set():
                log.info("Stop event set. Halting simulation loop.")
                break
            if max_ticks is not None and ticks_run >= max_ticks:
                log.info("Reached max_ticks (%d). Halting loop.", max_ticks)
                break

            summary = self.step()
            summaries.append(summary)
            ticks_run += 1

            time.sleep(interval_seconds)

        return summaries
