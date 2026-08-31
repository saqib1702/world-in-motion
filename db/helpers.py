"""Helper functions for reading/writing MongoDB collections.

Provides query and mutation interfaces for:
1. `agents` — nation personas & internal state
2. `relations` — pairwise relation scores, history log, and change streams
3. `events` — append-only log of world events and agent reactions
4. `memory` — short-term memory per agent (last N items)
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from db import schema
from db.mongo import get_db

log = logging.getLogger(__name__)


def _is_duplicate_key_error(exc: BaseException) -> bool:
    """True when `exc` is Mongo's unique-index violation.

    Checked by type when pymongo is importable, with a name/code fallback so the
    same code path works under mongomock and the offline test doubles, which
    raise their own DuplicateKeyError classes.
    """
    try:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            return True
    except ImportError:  # pragma: no cover - pymongo is a hard dependency
        pass
    if type(exc).__name__ == "DuplicateKeyError":
        return True
    return getattr(exc, "code", None) == 11000


# ============================================================================
# 0. AGENT IDENTITY RESOLUTION
# ============================================================================
# The LLM reasons in human-readable nation names ("Solaria Federation") because
# that is what reads well in a prompt, but every document in `relations` must be
# keyed by canonical agent_id ("nation_solaria") or the frontend cannot join a
# relation edge to a node. Resolution therefore happens here, at the single
# read/write boundary to the `relations` collection, so no caller — present or
# future — is able to persist a display name by mistake.

_agent_index_lock = threading.Lock()
_agent_index_cache: Optional[dict[str, str]] = None


def build_agent_index(force: bool = False) -> dict[str, str]:
    """Return a mapping of every known alias -> canonical agent_id.

    Keys include the agent_id itself (so resolution is idempotent), the exact
    display name, and the lower-cased display name. Cached process-wide because
    the agent roster is tiny and changes rarely; pass force=True or call
    `invalidate_agent_index()` to rebuild.
    """
    global _agent_index_cache
    with _agent_index_lock:
        if _agent_index_cache is not None and not force:
            return _agent_index_cache

        index: dict[str, str] = {}
        try:
            cursor = get_db()[schema.AGENTS].find(
                {}, {"_id": 0, "agent_id": 1, "name": 1}
            )
            for doc in cursor:
                agent_id = doc.get("agent_id")
                if not agent_id:
                    continue
                # Identity mapping keeps resolve_agent_id() idempotent.
                index[agent_id] = agent_id
                index[agent_id.lower()] = agent_id
                name = doc.get("name")
                if name:
                    index[name] = agent_id
                    index[name.lower()] = agent_id
        except Exception as exc:
            # A resolver failure must never take down a tick; callers fall back
            # to skipping the relation write and log it.
            log.warning("Failed to build agent index: %s", exc)

        _agent_index_cache = index
        return index


def invalidate_agent_index() -> None:
    """Drop the cached agent index. Call after inserting or renaming agents."""
    global _agent_index_cache
    with _agent_index_lock:
        _agent_index_cache = None


def resolve_agent_id(value: Optional[str]) -> Optional[str]:
    """Resolve a display name or agent_id to a canonical agent_id.

    Returns None when the value is empty, the literal string "none" (the LLM's
    sentinel for "no target"), or cannot be matched to any known agent. On a
    miss the index is rebuilt once before giving up, so agents seeded after the
    cache was populated still resolve.
    """
    if not value or not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate or candidate.lower() == "none":
        return None

    index = build_agent_index()
    hit = index.get(candidate) or index.get(candidate.lower())
    if hit:
        return hit

    index = build_agent_index(force=True)
    hit = index.get(candidate) or index.get(candidate.lower())
    if hit:
        return hit

    log.warning("Could not resolve %r to a known agent_id", value)
    return None


def _canonical_pair(source_agent_id: str, target_agent_id: str) -> tuple[str, str]:
    """Best-effort canonicalisation of a relation's endpoints.

    Falls back to the raw value when resolution fails so that a write is never
    silently dropped at the DB layer — callers that care (see NationAgent.decide)
    check `resolve_agent_id` themselves and skip instead.
    """
    resolved_source = resolve_agent_id(source_agent_id) or source_agent_id
    resolved_target = resolve_agent_id(target_agent_id) or target_agent_id
    if (resolved_source, resolved_target) != (source_agent_id, target_agent_id):
        log.debug(
            "Canonicalised relation endpoints [%s -> %s] to [%s -> %s]",
            source_agent_id,
            target_agent_id,
            resolved_source,
            resolved_target,
        )
    return resolved_source, resolved_target


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
    # Only a brand-new agent (or a rename) can invalidate the alias index. Agents
    # re-save themselves on every tick, so invalidating unconditionally would
    # rebuild the index six times per tick for no reason.
    if _agent_index_cache is None or _agent_index_cache.get(name) != agent_id:
        invalidate_agent_index()
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

    Both endpoints are canonicalised to agent_ids first, so passing a display
    name such as "Solaria Federation" still stores "nation_solaria".
    """
    db = get_db()
    source_agent_id, target_agent_id = _canonical_pair(source_agent_id, target_agent_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = db[schema.RELATIONS].find_one(
        {"source_agent_id": source_agent_id, "target_agent_id": target_agent_id},
        {"score": 1},
    )

    current_score = existing["score"] if (existing and "score" in existing) else 0.0
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
    """Get relation document between source and target agent.

    Endpoints are canonicalised to agent_ids so a lookup by display name finds
    the same document that update_relation() wrote.
    """
    db = get_db()
    source_agent_id, target_agent_id = _canonical_pair(source_agent_id, target_agent_id)
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
    agent_id = resolve_agent_id(agent_id) or agent_id
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

def find_event_by_external_id(source: str, external_id: str) -> Optional[dict[str, Any]]:
    """Return the existing event for this upstream identity, if any."""
    if not external_id:
        return None
    return get_db()[schema.EVENTS].find_one(
        {"source": source, "external_id": external_id}, {"_id": 0}
    )


def log_event_once(
    headline: str,
    description: str,
    event_type: str = "news_headline",
    source: str = "simulation",
    involved_agents: Optional[list[str]] = None,
    payload: Optional[dict[str, Any]] = None,
    external_id: Optional[str] = None,
) -> tuple[str, bool]:
    """Log a world event, deduplicating on the upstream `(source, external_id)`.

    Returns ``(event_id, created)``. ``created`` is False when this exact
    upstream item was already ingested, which lets the caller skip re-running
    agent reasoning over news it has already reacted to.

    `external_id` is the identity assigned by the *upstream source* (the sha1 of
    the article URL, for GDELT/Google News). Passing it is what activates the
    unique sparse index on `(source, external_id)` declared in db/schema.py.
    When it is omitted the event is treated as inherently unique — correct for
    fabricated demo events, which are meant to fire every time.
    """
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    if external_id:
        existing = find_event_by_external_id(source, external_id)
        if existing:
            log.debug(
                "Skipping already-ingested event (%s/%s): %s",
                source,
                external_id,
                headline[:60],
            )
            return str(existing.get("event_id", "")), False

    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    doc = {
        "event_id": event_id,
        # Fall back to the internal id so the unique index still has a value for
        # simulation-generated events, but never clobber a real upstream id.
        "external_id": external_id or event_id,
        "timestamp": now_iso,
        "event_type": event_type,
        "source": source,
        "headline": headline,
        "description": description,
        "involved_agents": involved_agents or [],
        "agent_reactions": [],
        "payload": payload or {},
    }

    try:
        db[schema.EVENTS].insert_one(doc)
    except Exception as exc:
        # Two ingestion workers can race between the find_one above and this
        # insert. The unique index is the real guard; losing that race is a
        # successful dedupe, not an error.
        if _is_duplicate_key_error(exc) and external_id:
            existing = find_event_by_external_id(source, external_id)
            if existing:
                return str(existing.get("event_id", "")), False
        raise

    log.info("Logged event [%s]: %s", event_id, headline[:60])
    return event_id, True


def log_event(
    headline: str,
    description: str,
    event_type: str = "news_headline",
    source: str = "simulation",
    involved_agents: Optional[list[str]] = None,
    payload: Optional[dict[str, Any]] = None,
    external_id: Optional[str] = None,
) -> str:
    """Log a world event to the append-only `events` collection.

    Thin wrapper over `log_event_once` kept for callers that do not care whether
    the event was new.
    """
    event_id, _created = log_event_once(
        headline=headline,
        description=description,
        event_type=event_type,
        source=source,
        involved_agents=involved_agents,
        payload=payload,
        external_id=external_id,
    )
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


def log_agent_reactions_bulk(reactions: list[dict[str, Any]]) -> int:
    """Write many reactions in one round trip. Returns docs modified.

    A tick fans every actor's decision across every event it reacted to, so the
    naive loop is len(agents) * len(events) separate `update_one` calls — 60
    sequential round trips to Atlas for a 10-actor, 6-event tick, which is
    several seconds of pure network latency. `bulk_write` collapses that into
    one. Falls back to per-reaction writes if the driver has no bulk support
    (the offline test doubles).

    Each item in `reactions` needs: event_id, agent_id, action_type, reasoning,
    and optionally relation_delta and target_country.
    """
    if not reactions:
        return 0

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    def _op_body(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "$push": {
                "agent_reactions": {
                    "agent_id": item["agent_id"],
                    "action_type": item.get("action_type", "ignore"),
                    "target_country": item.get("target_country"),
                    "reasoning": item.get("reasoning", ""),
                    "relation_delta": float(item.get("relation_delta", 0.0)),
                    "timestamp": now,
                }
            },
            "$addToSet": {"involved_agents": item["agent_id"]},
        }

    try:
        from pymongo import UpdateOne

        ops = [
            UpdateOne({"event_id": item["event_id"]}, _op_body(item))
            for item in reactions
            if item.get("event_id")
        ]
        if not ops:
            return 0
        # ordered=False: one failed event id must not abandon the rest.
        result = db[schema.EVENTS].bulk_write(ops, ordered=False)
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception as exc:
        log.warning("Bulk reaction write unavailable (%s); falling back to per-item writes.", exc)
        modified = 0
        for item in reactions:
            if not item.get("event_id"):
                continue
            try:
                res = db[schema.EVENTS].update_one(
                    {"event_id": item["event_id"]}, _op_body(item)
                )
                if getattr(res, "modified_count", 0):
                    modified += 1
            except Exception as inner:
                log.warning("Failed to log reaction for %s: %s", item.get("agent_id"), inner)
        return modified


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
    # Find memory_ids of memories to keep
    keep_docs = list(
        db[schema.MEMORY]
        .find({"agent_id": agent_id}, {"memory_id": 1})
        .sort("timestamp", -1)
        .limit(keep_limit + 1)
    )
    if len(keep_docs) <= keep_limit:
        return 0

    keep_ids = [d["memory_id"] for d in keep_docs[:keep_limit]]

    delete_result = db[schema.MEMORY].delete_many({
        "agent_id": agent_id,
        "memory_id": {"$nin": keep_ids},
    })
    return delete_result.deleted_count
