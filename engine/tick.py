"""The world tick and simulation engine.

Drives the simulation loop:
1. Pulls latest events (from ingestion, DB, or manual triggers), skipping any
   already ingested on a previous cycle.
2. Records perception for each nation agent, then decides for every agent in a
   single batched Gemini call (see engine/deliberation.py).
3. Applies relation deltas to the `relations` collection in MongoDB.
4. Logs full turn to `ticks` and `events`.
5. Returns a structured summary of what changed during the tick.

Can be run as a single manual tick or as a continuous loop on an interval.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agents.base import Observation
from agents.nation import NationAgent
from db import helpers, schema
from db.mongo import get_db
from engine import deliberation

log = logging.getLogger(__name__)


def _commit_agent_decision(
    agent: NationAgent, decision: dict[str, Any]
) -> dict[str, Any]:
    """Commit one already-made decision, isolating per-agent failures.

    The decision itself comes from the batched Gemini call (see
    engine/deliberation.py); this only persists it. `apply_decision` is the
    same commit path the per-agent mode used, so relation writes,
    agent_id canonicalisation, memory, and the actions log are identical
    regardless of how the decision was produced.
    """
    try:
        committed = agent.apply_decision(decision)
        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "decision": committed,
            "status": "success",
        }
    except Exception as exc:
        log.error("Error committing decision for [%s]: %s", agent.name, exc, exc_info=True)
        return {
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "decision": {
                "action_type": "ignore",
                "target_country": "None",
                "reasoning": f"Commit error: {exc}",
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
    #: Retained for API compatibility with existing callers. Decisions are no
    #: longer fanned out across threads — one batched Gemini call covers every
    #: actor, so there is nothing to parallelise and the rate limiter no longer
    #: serialises ten competing requests.
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
        duplicate_events: list[str] = []
        if custom_events:
            for evt in custom_events:
                headline = evt.get("headline", evt.get("title", "World Event"))
                desc = evt.get("description", evt.get("body", ""))
                involved = evt.get("involved_agents", [a.agent_id for a in self.agents])
                # Identity assigned by the upstream source (sha1 of the article
                # URL for GDELT / Google News). Ingested events carry it inside
                # `payload`; fabricated demo events have none and so always fire.
                external_id = evt.get("external_id") or (evt.get("payload") or {}).get("external_id")
                evt_id, created = helpers.log_event_once(
                    headline=headline,
                    description=desc,
                    event_type=evt.get("event_type", "manual_trigger"),
                    source=evt.get("source", "manual"),
                    involved_agents=involved,
                    payload=evt,
                    external_id=external_id,
                )
                if not created:
                    # Already ingested on an earlier cycle. Reacting again would
                    # duplicate the relation shifts and spend one Gemini call per
                    # agent on news the world has already responded to.
                    duplicate_events.append(headline)
                    continue
                events_for_tick.append({
                    "event_id": evt_id,
                    "headline": headline,
                    "description": desc,
                    "involved_agents": involved,
                })

            if not events_for_tick:
                log.info(
                    "Tick %d: all %d incoming events were already ingested. "
                    "Skipping agent reasoning.",
                    self.tick,
                    len(duplicate_events),
                )
                return {
                    "run_id": self.run_id,
                    "tick": self.tick,
                    "timestamp": now_iso,
                    "events_processed": [],
                    "duplicate_events": duplicate_events,
                    "actions_taken": [],
                    "relation_shifts": [],
                    "skipped": True,
                    "skip_reason": "all_events_already_ingested",
                }
        else:
            recent_db_events = helpers.get_recent_events(limit=3)
            events_for_tick = recent_db_events or [{"headline": f"Tick {self.tick} Geopolitical Standstill", "description": "No major news event."}]

        # 2. Build Observation
        obs = Observation(
            tick=self.tick,
            world_state={"run_id": self.run_id, "active_agents": len(self.agents)},
            events=events_for_tick,
        )

        # 3. Decide for the whole world in one Gemini call, then commit.
        #    Perception is recorded per-agent first so each actor's memory log
        #    still reflects this cycle's events; the decision itself is batched
        #    (engine/deliberation.py) so a 10-actor tick costs one request, not
        #    ten. A malformed batch falls back to per-actor calls internally.
        for agent in self.agents:
            try:
                agent.perceive(obs)
            except Exception as exc:
                log.warning("Perception failed for [%s]: %s", agent.name, exc)

        decisions_by_id, mode = deliberation.deliberate(self.agents, obs)
        log.info(
            "Deliberation for tick %d completed in mode=%s (%d decisions).",
            self.tick,
            mode,
            len(decisions_by_id),
        )

        agent_results: list[dict[str, Any]] = []
        for agent in self.agents:
            decision = decisions_by_id.get(agent.agent_id)
            if decision is None:
                # deliberate() guarantees a decision per agent, but stay defensive.
                decision = {
                    "action_type": "ignore",
                    "target_country": "None",
                    "reasoning": "No decision produced for this actor.",
                    "relation_delta": 0.0,
                }
            agent_results.append(_commit_agent_decision(agent, decision))

        # Sort results by agent name for deterministic order
        agent_results.sort(key=lambda x: x["agent_name"])

        # 4. Process decisions, aggregate relation shifts, and log reactions
        actions_taken = []
        relation_shifts = []
        pending_reactions: list[dict[str, Any]] = []

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
                # `target` is a display name straight off the decision; resolve
                # it so the lookup hits the same doc update_relation() wrote.
                target_id = helpers.resolve_agent_id(target)
                current_score = agent.relations.get(target)
                if current_score is None:
                    rel_info = helpers.get_relation(agent_id, target_id or target)
                    current_score = rel_info.get("score", 0.0)
                relation_shifts.append({
                    "source": agent_name,
                    "source_id": agent_id,
                    "target": target,
                    "target_id": target_id,
                    "delta": delta,
                    "new_score": float(current_score),
                    "reasoning": reasoning,
                })

            # Queue reactions against every event doc; written in one bulk call
            # below rather than agents x events separate round trips.
            for evt in events_for_tick:
                evt_id = evt.get("event_id")
                if evt_id:
                    pending_reactions.append({
                        "event_id": evt_id,
                        "agent_id": agent_id,
                        "action_type": action_type,
                        "reasoning": reasoning,
                        "relation_delta": delta,
                        "target_country": target,
                    })

        if pending_reactions:
            try:
                helpers.log_agent_reactions_bulk(pending_reactions)
            except Exception as exc:
                log.warning("Failed to persist agent reactions for tick %d: %s", self.tick, exc)

        # 5. Compile & persist tick summary
        summary = {
            "run_id": self.run_id,
            "tick": self.tick,
            "timestamp": now_iso,
            "events_processed": [e.get("headline") for e in events_for_tick],
            "duplicate_events": duplicate_events,
            # "batch" = one Gemini call covered every actor; "batch+fallback" =
            # some actors needed an individual call; "fallback" = the batch was
            # unusable. Surfaced so a degrading model shows up in the tick log
            # rather than only in stderr.
            "deliberation_mode": mode,
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
