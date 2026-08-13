"""HTTP routes.

Keep these thin: parse the request, call into /engine or /db, serialize the
result. Simulation logic does not belong here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from flask import Blueprint, jsonify, request

import config
from agents.nation import NationAgent
from db import helpers, mongo, schema
from engine.tick import WorldEngine

bp = Blueprint("api", __name__)

_ENGINE: WorldEngine | None = None
_ENGINE_LOCK = Lock()


def _get_engine() -> WorldEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = WorldEngine(run_id="api_manual_run")
        return _ENGINE


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@bp.get("/health")
def health():
    """Liveness + dependency check.

    200 when MongoDB answers, 503 when it does not, so a load balancer or
    ECS/ALB target group can act on it directly.
    """
    mongo_ok, mongo_error = mongo.ping()

    body = {
        "status": "ok" if mongo_ok else "degraded",
        "app": config.APP_NAME,
        "env": config.FLASK_ENV,
        "dependencies": {
            "mongodb": {
                "connected": mongo_ok,
                "database": config.MONGO_DB_NAME,
                **({"error": mongo_error} if mongo_error else {}),
            },
            # Not probed here — a live Gemini call costs tokens on every
            # health check. See scripts/check_connections.py.
            "gemini": {
                "configured": bool(config.GEMINI_API_KEY),
                "model": config.GEMINI_MODEL,
            },
        },
    }
    return jsonify(body), (200 if mongo_ok else 503)


@bp.get("/agents")
def list_agents():
    """List all nation agents and their latest stored state."""
    agents = helpers.list_agents(agent_type="nation")
    return jsonify({"count": len(agents), "agents": agents})


@bp.get("/relations")
def relations():
    """Return pairwise relations and a source->target score matrix."""
    rows = helpers.list_all_relations()
    matrix: dict[str, dict[str, float]] = {}
    for row in rows:
        source = str(row.get("source_agent_id", ""))
        target = str(row.get("target_agent_id", ""))
        score = float(row.get("score", 0.0))
        if source not in matrix:
            matrix[source] = {}
        matrix[source][target] = score

    return jsonify({"count": len(rows), "matrix": matrix, "relations": rows})


@bp.get("/events")
def events():
    """Return recent event log, optionally filtered by timestamp lower bound."""
    since_raw = request.args.get("since")
    limit = int(request.args.get("limit", "100"))
    limit = max(1, min(limit, 500))

    try:
        since = _parse_since(since_raw)
    except ValueError:
        return jsonify({"error": "Invalid since timestamp. Use ISO-8601 format."}), 400

    db = mongo.get_db()
    query: dict[str, Any] = {}
    if since is not None:
        query["timestamp"] = {"$gte": since.isoformat()}

    items = list(
        db[schema.EVENTS]
        .find(query, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return jsonify({"count": len(items), "events": items})


@bp.post("/agents/<agent_id>/chat")
def chat_with_agent(agent_id: str):
    """Send a human message to one nation agent and return its in-character response."""
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Request body must include a non-empty 'message'."}), 400

    agent = NationAgent.load_from_db(agent_id)
    if agent is None:
        return jsonify({"error": f"Agent not found: {agent_id}"}), 404

    reply = agent.speak(message)
    return jsonify({"agent_id": agent_id, "message": message, "reply": reply})


@bp.post("/engine/tick")
def trigger_tick():
    """Manually trigger a world tick for demos, optionally with custom events."""
    body = request.get_json(silent=True) or {}
    custom_events_raw = body.get("events")
    custom_events: list[dict[str, Any]] | None = None
    if custom_events_raw is not None:
        if not isinstance(custom_events_raw, list):
            return jsonify({"error": "'events' must be a list when provided."}), 400
        normalized: list[dict[str, Any]] = []
        for item in custom_events_raw:
            if isinstance(item, dict):
                normalized.append(item)
        custom_events = normalized

    summary = _get_engine().step(custom_events=custom_events)
    return jsonify(summary), 200
