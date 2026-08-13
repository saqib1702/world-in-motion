"""Helper functions for reading/writing MongoDB collections.

Provides query and mutation interfaces for:
1. `agents` — nation personas & internal state
2. `relations` — pairwise relation scores, history log, and change streams
3. `events` — append-only log of world events and agent reactions
4. `memory` — short-term memory per agent (last N items)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from db import schema
from db.mongo import get_db

log = logging.getLogger(__name__)


# ============================================================================
# 1. AGENTS COLLECTION HELPERS
# ============================================================================

def upsert_agent(
    agent_id: str,
    name: str,
    persona: dict[str, Any],
    state: Optional[dict[str, Any]] = None,
    agent_type: str = "nation",
) -> dict[str, Any]:
    """Insert or update an agent document in `agents` collection."""
    db = get_db()
    doc = {
        "agent_id": agent_id,
        "name": name,
        "agent_type": agent_type,
        "persona": persona,
        "state": state or {"status": "active"},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db[schema.AGENTS].update_one(
        {"agent_id": agent_id},
        {"$set": doc},
        upsert=True,
    )
    log.debug("Upserted agent [%s] in MongoDB", agent_id)
    return doc


def get_agent(agent_id: str) -> Optional[dict[str, Any]]:
    """Retrieve an agent document by agent_id."""
    db = get_db()
    return db[schema.AGENTS].find_one({"agent_id": agent_id}, {"_id": 0})


def list_agents(agent_type: Optional[str] = None) -> list[dict[str, Any]]:
    """List all agents, optionally filtered by agent_type."""
    db = get_db()
    query = {"agent_type": agent_type} if agent_type else {}
    return list(db[schema.AGENTS].find(query, {"_id": 0}))


# ============================================================================
# 2. RELATIONS COLLECTION HELPERS
# ============================================================================

def update_relation(
    source_agent_id: str,
    target_agent_id: str,
    score: Optional[float] = None,
    delta: Optional[float] = None,
    reasoning: str = "",
) -> dict[str, Any]:
    """Update pairwise relation score between source and target agent.

    If score is not given directly, delta is added to the current score.
    Score is clamped between -100.0 (hostile) and +100.0 (allied).
    Appends a historical record to the document's `history` array.
    """
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = db[schema.RELATIONS].find_one(
        {"source_agent_id": source_agent_id, "target_agent_id": target_agent_id}
    )

    current_score = existing["score"] if existing else 0.0
    effective_delta = delta if delta is not None else 0.0

    if score is not None:
        new_score = score
    else:
        new_score = current_score + effective_delta

    # Clamp score to [-100.0, 100.0]
    new_score = max(-100.0, min(100.0, float(new_score)))

    history_entry = {
        "timestamp": now_iso,
        "score": new_score,
        "delta": effective_delta,
        "reasoning": reasoning,
    }

    db[schema.RELATIONS].update_one(
        {"source_agent_id": source_agent_id, "target_agent_id": target_agent_id},
        {
            "$set": {
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
                "score": new_score,
                "last_delta": effective_delta,
                "updated_at": now_iso,
            },
            "$push": {
                "history": {
                    "$each": [history_entry],
                    "$slice": -50,  # Keep last 50 history entries
                }
            },
        },
        upsert=True,
    )

    log.info(
        "Relation updated [%s -> %s]: %.1f (delta: %.1f)",
        source_agent_id,
        target_agent_id,
        new_score,
        effective_delta,
    )

    return {
        "source_agent_id": source_agent_id,
        "target_agent_id": target_agent_id,
        "score": new_score,
        "last_delta": effective_delta,
        "updated_at": now_iso,
    }


def get_relation(source_agent_id: str, target_agent_id: str) -> dict[str, Any]:
    """Get relation document between source and target agent."""
    db = get_db()
    doc = db[schema.RELATIONS].find_one(
        {"source_agent_id": source_agent_id, "target_agent_id": target_agent_id},
        {"_id": 0},
    )
    if not doc:
        return {
            "source_agent_id": source_agent_id,
            "target_agent_id": target_agent_id,
            "score": 0.0,
            "last_delta": 0.0,
            "history": [],
            "updated_at": None,
        }
    return doc


def get_all_relations_for_agent(agent_id: str) -> list[dict[str, Any]]:
    """Retrieve all relations involving an agent (as source or target)."""
    db = get_db()
    query = {
        "$or": [
            {"source_agent_id": agent_id},
            {"target_agent_id": agent_id},
        ]
    }
    return list(db[schema.RELATIONS].find(query, {"_id": 0}))


def list_all_relations() -> list[dict[str, Any]]:
    """List all pairwise relation documents in the database."""
    db = get_db()
    return list(db[schema.RELATIONS].find({}, {"_id": 0}))


def watch_relations(
    pipeline: Optional[list[dict[str, Any]]] = None,
    *,
    full_document: Optional[str] = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield real-time MongoDB Change Stream events on the `relations` collection.

    Note: MongoDB Change Streams require a replica set or Atlas cluster.
    """
    db = get_db()
    collection = db[schema.RELATIONS]
    kwargs: dict[str, Any] = {}
    if full_document:
        kwargs["full_document"] = full_document

    with collection.watch(pipeline=pipeline, **kwargs) as stream:
        for change in stream:
            yield change


# ============================================================================
# 3. EVENTS COLLECTION HELPERS
# ============================================================================

def log_event(
    headline: str,
    description: str,
    event_type: str = "news_headline",
    source: str = "simulation",
    involved_agents: Optional[list[str]] = None,
    payload: Optional[dict[str, Any]] = None,
) -> str:
    """Log a world event to the append-only `events` collection."""
    db = get_db()
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    doc = {
        "event_id": event_id,
        "external_id": event_id,
        "timestamp": now_iso,
        "event_type": event_type,
        "source": source,
        "headline": headline,
        "description": description,
        "involved_agents": involved_agents or [],
        "agent_reactions": [],
        "payload": payload or {},
    }


    db[schema.EVENTS].insert_one(doc)
    log.info("Logged event [%s]: %s", event_id, headline[:60])
    return event_id


def log_agent_reaction(
    event_id: str,
    agent_id: str,
    action_type: str,
    reasoning: str,
    relation_delta: float = 0.0,
    target_country: Optional[str] = None,
) -> bool:
    """Append an agent's reaction/decision to an existing event doc in `events`."""
    db = get_db()
    reaction = {
        "agent_id": agent_id,
        "action_type": action_type,
        "target_country": target_country,
        "reasoning": reasoning,
        "relation_delta": relation_delta,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    result = db[schema.EVENTS].update_one(
        {"event_id": event_id},
        {
            "$push": {"agent_reactions": reaction},
            "$addToSet": {"involved_agents": agent_id},
        },
    )
    return result.modified_count > 0


def get_recent_events(limit: int = 20, agent_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Retrieve recent events sorted by timestamp descending."""
    db = get_db()
    query = {}
    if agent_id:
        query["involved_agents"] = agent_id

    cursor = (
        db[schema.EVENTS]
        .find(query, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return list(cursor)


# ============================================================================
# 4. MEMORY COLLECTION HELPERS
# ============================================================================

def add_agent_memory(
    agent_id: str,
    memory_type: str,
    content: dict[str, Any],
    importance_score: float = 1.0,
    max_retained: int = 20,
) -> dict[str, Any]:
    """Add a short-term memory document for an agent, retaining last `max_retained` items."""
    db = get_db()
    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    doc = {
        "memory_id": memory_id,
        "agent_id": agent_id,
        "timestamp": now_iso,
        "memory_type": memory_type,
        "importance_score": importance_score,
        "content": content,
    }

    db[schema.MEMORY].insert_one(doc)
    prune_agent_memories(agent_id, keep_limit=max_retained)
    return doc


def get_agent_memories(agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent memory items for an agent, ordered newest first."""
    db = get_db()
    cursor = (
        db[schema.MEMORY]
        .find({"agent_id": agent_id}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return list(cursor)


def prune_agent_memories(agent_id: str, keep_limit: int = 20) -> int:
    """Retain latest `keep_limit` memory documents for an agent and prune older ones."""
    db = get_db()
    # Find timestamps of memories to keep
    keep_docs = list(
        db[schema.MEMORY]
        .find({"agent_id": agent_id}, {"memory_id": 1})
        .sort("timestamp", -1)
        .limit(keep_limit)
    )
    keep_ids = [d["memory_id"] for d in keep_docs]

    delete_result = db[schema.MEMORY].delete_many({
        "agent_id": agent_id,
        "memory_id": {"$nin": keep_ids},
    })
    return delete_result.deleted_count
