"""Nation Agent implementation.

Represents a sovereign nation with a persona (stored in MongoDB), memory log,
diplomatic relations, perception of events, Gemini-powered decision making
using structured JSON outputs, and direct speak capabilities.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from db import schema, helpers
from db.mongo import get_db
from llm import gemini
from agents.base import Agent, Observation

log = logging.getLogger(__name__)

# Structured output schema for Gemini generate content
DECIDE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "action_type": {
            "type": "STRING",
            "enum": [
                "propose_alliance",
                "issue_statement",
                "impose_sanction",
                "trade_agreement",
                "military_warning",
                "ignore",
            ],
        },
        "target_country": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
        "relation_delta": {"type": "NUMBER"},
    },
    "required": ["action_type", "target_country", "reasoning", "relation_delta"],
}


class NationAgent(Agent):
    """Sovereign nation agent operating in the world simulation."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        persona: Optional[dict[str, Any]] = None,
        recent_memory: Optional[list[dict[str, Any]]] = None,
        agent_type: str = "nation",
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            persona=persona or {},
        )
        self.recent_memory: list[dict[str, Any]] = recent_memory or []

    # --- Convenience Persona Properties ---

    @property
    def government_type(self) -> str:
        return self.persona.get("government_type", "Unknown Sovereign State")

    @property
    def core_interests(self) -> list[str]:
        return self.persona.get("core_interests", [])

    @property
    def allies(self) -> list[str]:
        return self.persona.get("allies", [])

    @property
    def rivals(self) -> list[str]:
        return self.persona.get("rivals", [])

    @property
    def relations(self) -> dict[str, float]:
        if "relations" not in self.persona:
            self.persona["relations"] = {}
        return self.persona["relations"]

    # --- Perception & Memory ---

    def perceive(self, event: dict[str, Any] | Observation) -> dict[str, Any]:
        """Perceive a news item or observation and record it in memory collections.

        Args:
            event: Either a news/event dict or an Observation object.

        Returns:
            The formatted memory entry added to recent_memory.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        if isinstance(event, Observation):
            entry = {
                "timestamp": timestamp,
                "type": "observation",
                "tick": event.tick,
                "events": event.events,
                "world_state_summary": event.world_state,
            }
        elif isinstance(event, dict):
            entry = {
                "timestamp": timestamp,
                "type": "event",
                "content": event,
            }
        else:
            entry = {
                "timestamp": timestamp,
                "type": "raw",
                "content": str(event),
            }

        self.recent_memory.append(entry)

        # Cap memory to avoid prompt bloat (keep last 20 items)
        if len(self.recent_memory) > 20:
            self.recent_memory = self.recent_memory[-20:]

        log.info("Agent [%s] perceived event: %s", self.name, entry.get("type"))

        # Save to memory collection via helpers
        try:
            helpers.add_agent_memory(
                agent_id=self.agent_id,
                memory_type=entry["type"],
                content=entry,
                importance_score=1.0,
                max_retained=20,
            )
        except Exception as exc:
            log.warning("Failed to store memory for [%s]: %s", self.agent_id, exc)

        self.save_to_db()
        return entry

    # --- Decision Making ---

    def decide(self, observation: Optional[Observation] = None) -> dict[str, Any]:
        """Choose actions based on persona, recent memory, and relations.

        Args:
            observation: Optional current tick observation. If provided, perceived first.

        Returns:
            Structured decision dictionary with action_type, target_country, reasoning, relation_delta.
        """
        if observation is not None:
            self.perceive(observation)

        system_prompt = self._build_decide_system_prompt()
        user_prompt = self._build_decide_user_prompt(observation)

        raw_response = gemini.generate(
            prompt=user_prompt,
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=DECIDE_SCHEMA,
            temperature=0.3,
        )

        decision = self.parse_decision(raw_response)
        return self.apply_decision(decision)

    def parse_decision(self, raw_response: str) -> dict[str, Any]:
        """Parse a raw JSON decision, falling back to an inert action."""
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            log.error("Failed to parse JSON decision from Gemini for %s: %s", self.name, exc)
            return {
                "action_type": "ignore",
                "target_country": "None",
                "reasoning": f"Fallback due to response parsing error: {raw_response[:100]}",
                "relation_delta": 0.0,
            }
        if not isinstance(parsed, dict):
            return {
                "action_type": "ignore",
                "target_country": "None",
                "reasoning": "Fallback: decision payload was not an object.",
                "relation_delta": 0.0,
            }
        return parsed

    def apply_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Commit an already-made decision to relations, memory, and the DB.

        Split out from `decide()` so the batched deliberation path (one Gemini
        call for the whole world, see engine/deliberation.py) commits results
        through exactly the same code as the per-agent path — including the
        agent_id canonicalisation below, which is the guard that keeps display
        names out of the `relations` collection.
        """
        # Apply relation shift if target country is specified
        target = decision.get("target_country", "None")
        delta = float(decision.get("relation_delta", 0.0))
        reasoning = decision.get("reasoning", "")
        if target and target.lower() != "none" and delta != 0.0:
            # Gemini answers with a display name ("Solaria Federation"); the
            # persona.relations dict is intentionally keyed that way because it
            # is rendered straight into prompts. The `relations` collection, by
            # contrast, must be keyed by canonical agent_id so the frontend can
            # join edges to nodes — resolve before persisting.
            target_id = helpers.resolve_agent_id(target)
            if target_id is None:
                log.warning(
                    "Agent [%s] chose unknown target_country %r; skipping relation write",
                    self.name,
                    target,
                )
            else:
                current_standing = self.relations.get(target, 0.0)
                new_standing = max(-100.0, min(100.0, current_standing + delta))
                self.relations[target] = new_standing

                # Update pairwise relation collection via helpers (keyed by id)
                try:
                    helpers.update_relation(
                        source_agent_id=self.agent_id,
                        target_agent_id=target_id,
                        delta=delta,
                        reasoning=reasoning,
                    )
                except Exception as exc:
                    log.warning("Failed to update relation collection for [%s -> %s]: %s", self.agent_id, target_id, exc)

                log.info(
                    "Agent [%s] updated relation with [%s]: %.1f -> %.1f (delta: %.1f)",
                    self.name,
                    target,
                    current_standing,
                    new_standing,
                    delta,
                )

        # Record action in memory log & memory collection
        action_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "action",
            "decision": decision,
        }
        self.recent_memory.append(action_record)

        try:
            helpers.add_agent_memory(
                agent_id=self.agent_id,
                memory_type="action",
                content=action_record,
                importance_score=1.0,
                max_retained=20,
            )
        except Exception as exc:
            log.warning("Failed to add memory record for [%s]: %s", self.agent_id, exc)

        # Persist agent state to MongoDB
        self.save_to_db()

        # Log decision to ACTIONS collection in DB
        self._record_action_to_db(decision)

        return decision

    # --- Direct Chat / Observer Interaction ---

    def speak(self, user_message: str) -> str:
        """Direct diplomatic communication interface with a human observer.

        Args:
            user_message: Input message or inquiry from the human observer.

        Returns:
            In-character diplomatic response string.
        """
        system_prompt = (
            f"You are representing the leadership of {self.name}, a {self.government_type}.\n"
            f"Core Interests: {', '.join(self.core_interests)}\n"
            f"Allies: {', '.join(self.allies) if self.allies else 'None'}\n"
            f"Rivals: {', '.join(self.rivals) if self.rivals else 'None'}\n"
            f"Current Relations: {json.dumps(self.relations)}\n\n"
            f"Speak directly, diplomatically, and authentically as the official spokesperson/leader of {self.name}. "
            f"Respond to the observer's inquiry while defending your national interests and staying strictly in character."
        )

        recent_context = ""
        if self.recent_memory:
            recent_summary = json.dumps(self.recent_memory[-5:], indent=2)
            recent_context = f"\nYour recent memory context:\n{recent_summary}\n"

        user_prompt = f"{recent_context}\nHuman Observer asks: \"{user_message}\"\n\nYour response:"

        response = gemini.generate(
            prompt=user_prompt,
            system_instruction=system_prompt,
            temperature=0.7,
        )
        return response.strip()

    # --- Prompt Construction Helpers ---

    def _build_decide_system_prompt(self) -> str:
        allies_str = ", ".join(self.allies) if self.allies else "None"
        rivals_str = ", ".join(self.rivals) if self.rivals else "None"
        interests_str = ", ".join(self.core_interests) if self.core_interests else "General Sovereignty"
        relations_str = json.dumps(self.relations, indent=2)

        return (
            f"You are modelling the strategic decision-making of {self.name}, a {self.government_type}.\n"
            f"Structural interests: {interests_str}.\n"
            f"Alignment:\n"
            f"- Closest partners: {allies_str}\n"
            f"- Principal strategic competitors: {rivals_str}\n\n"
            f"Current relation standings (scale: -100 hostile to +100 aligned):\n"
            f"{relations_str}\n\n"
            f"Reason from this actor's structural incentives, stated policy priorities, and "
            f"strategic self-interest. This is an analytical simulation: produce a plausible "
            f"projection of how this actor would respond, not a claim about actual policy, and "
            f"never fabricate quotations from named real officials."
        )

    def persona_brief(self) -> dict[str, Any]:
        """Compact persona summary for the batched world-deliberation prompt.

        Deliberately smaller than the single-agent system prompt: ten of these
        share one context window, so each actor gets its incentives and current
        standings but not its full memory log.
        """
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "government_type": self.government_type,
            "core_interests": self.core_interests,
            "closest_partners": self.allies,
            "principal_competitors": self.rivals,
            "relations": self.relations,
        }

    def _known_nation_names(self) -> list[str]:
        """Names of every other nation, for constraining target_country.

        Sourced from the DB roster so it stays correct as nations are added or
        renamed; falls back to the persona's own relation keys if the DB is
        unreachable.
        """
        try:
            names = [
                doc["name"]
                for doc in helpers.list_agents(agent_type="nation")
                if doc.get("name") and doc.get("agent_id") != self.agent_id
            ]
            if names:
                return sorted(names)
        except Exception as exc:
            log.debug("Falling back to persona relations for nation roster: %s", exc)
        return sorted(n for n in self.relations if n != self.name)

    def _build_decide_user_prompt(self, observation: Optional[Observation] = None) -> str:
        memory_str = json.dumps(self.recent_memory[-10:], indent=2) if self.recent_memory else "No recent memory."
        obs_str = ""
        if observation:
            obs_str = f"\nCurrent Tick: {observation.tick}\nEvents: {json.dumps(observation.events)}\nWorld State: {json.dumps(observation.world_state)}"

        known = self._known_nation_names()
        roster = "\n".join(f"- {n}" for n in known) if known else "- (no other nations known)"

        return (
            f"### GEOPOLITICAL CONTEXT & RECENT MEMORY\n"
            f"Recent developments in your nation's memory log:\n"
            f"{memory_str}\n"
            f"{obs_str}\n\n"
            f"### TASK\n"
            f"Analyze the recent events and current situation. Select the single best strategic diplomatic action for {self.name} to take right now.\n\n"
            f"### VALID TARGETS\n"
            f"target_country MUST be copied verbatim from this list, or be exactly \"None\":\n"
            f"{roster}\n"
            f"Never target yourself ({self.name}). Do not invent nations, abbreviate names, or use adjectives.\n\n"
            f"Assign relation_delta (numerical score from -20.0 to +20.0) reflecting how your relationship with target_country changes as a result of this action."
        )

    # --- MongoDB Persistence ---

    def to_doc(self) -> dict[str, Any]:
        """Convert agent to MongoDB document dict."""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "persona": self.persona,
            "recent_memory": self.recent_memory,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "NationAgent":
        """Instantiate a NationAgent from a MongoDB document."""
        return cls(
            agent_id=doc["agent_id"],
            name=doc["name"],
            persona=doc.get("persona", {}),
            recent_memory=doc.get("recent_memory", []),
            agent_type=doc.get("agent_type", "nation"),
        )

    def save_to_db(self) -> bool:
        """Persist state to MongoDB `agents` collection."""
        try:
            helpers.upsert_agent(
                agent_id=self.agent_id,
                name=self.name,
                persona=self.persona,
                agent_type=self.agent_type,
            )
            return True
        except Exception as exc:
            log.warning("Failed to save agent [%s] to MongoDB: %s", self.agent_id, exc)
            return False

    @classmethod
    def load_from_db(cls, agent_id: str) -> Optional["NationAgent"]:
        """Load agent by agent_id from MongoDB `agents` collection."""
        try:
            doc = helpers.get_agent(agent_id)
            if doc:
                return cls.from_doc(doc)
            return None
        except Exception as exc:
            log.warning("Failed to load agent [%s] from MongoDB: %s", agent_id, exc)
            return None

    def _record_action_to_db(self, decision: dict[str, Any]) -> None:
        """Record decision to the `actions` collection."""
        try:
            db = get_db()
            db[schema.ACTIONS].insert_one({
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "decision": decision,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            log.warning("Failed to record action for agent [%s]: %s", self.agent_id, exc)

