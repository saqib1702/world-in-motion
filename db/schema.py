"""Collection names and index setup.

MongoDB is schemaless, so this module is the single place that records what
collections exist and what shape documents are expected to have. Document
shapes will firm up as the engine lands; the names and indexes here are the
starting contract.
"""

import logging

from pymongo.database import Database

log = logging.getLogger(__name__)

# --- Collection names (import these, never hardcode strings) ---
AGENTS = "agents"            # one doc per agent: persona, allegiances, state
RELATIONS = "relations"      # pairwise relation scores between nations over time
EVENTS = "events"            # append-only log of world events and agent reactions
MEMORY = "memory"            # short-term memory items per agent (last N events/decisions)
WORLD_STATE = "world_state"  # current snapshot of the world, one doc per sim run
TICKS = "ticks"              # append-only log: what happened on each tick
ACTIONS = "actions"          # agent decisions, with reasoning and relation deltas

# --- Indexes, keyed by collection ---
# Each entry: (keys, kwargs) passed to create_index.
INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    AGENTS: [
        ([("agent_id", 1)], {"unique": True, "name": "agent_id_unique"}),
        ([("agent_type", 1)], {"name": "agent_type"}),
    ],
    RELATIONS: [
        ([("source_agent_id", 1), ("target_agent_id", 1)],
         {"unique": True, "name": "pairwise_relation_unique"}),
        ([("source_agent_id", 1)], {"name": "source_agent_id"}),
        ([("target_agent_id", 1)], {"name": "target_agent_id"}),
        ([("updated_at", -1)], {"name": "updated_at_desc"}),
    ],
    EVENTS: [
        ([("event_id", 1)],
         {"unique": True, "sparse": True, "name": "event_id_unique"}),
        ([("timestamp", -1)], {"name": "timestamp_desc"}),
        ([("involved_agents", 1), ("timestamp", -1)],
         {"name": "involved_agents_timestamp"}),
        ([("source", 1), ("external_id", 1)],
         {"unique": True, "sparse": True, "name": "source_external_id_unique"}),
    ],
    MEMORY: [
        ([("agent_id", 1), ("timestamp", -1)],
         {"name": "agent_memory_timestamp"}),
        ([("memory_id", 1)],
         {"unique": True, "sparse": True, "name": "memory_id_unique"}),
    ],
    WORLD_STATE: [
        ([("run_id", 1)], {"unique": True, "name": "run_id_unique"}),
    ],
    TICKS: [
        ([("run_id", 1), ("tick", -1)], {"name": "run_tick"}),
    ],
    ACTIONS: [
        ([("run_id", 1), ("tick", -1)], {"name": "run_tick"}),
        ([("agent_id", 1)], {"name": "agent_id"}),
        ([("created_at", -1)], {"name": "created_at_desc"}),
    ],
}



def ensure_indexes(db: Database) -> None:
    """Create any missing indexes. Idempotent — safe to call on every boot."""
    for collection, specs in INDEXES.items():
        for keys, kwargs in specs:
            db[collection].create_index(keys, **kwargs)
            log.debug("ensured index %s.%s", collection, kwargs.get("name"))
    log.info("indexes ensured on %d collections", len(INDEXES))
