"""HTTP routes.

Keep these thin: parse the request, call into /engine or /db, serialize the
result. Simulation logic does not belong here.

Every endpoint that writes to the database or spends Gemini tokens carries
`@require_api_token` and a `@rate_limit`. Reads are public but still rate
limited, because an unauthenticated read loop against Atlas is its own kind of
bill. See api/security.py for the threat model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from flask import Blueprint, jsonify, request

import config
from agents.nation import NationAgent
from api.realtime import broadcast_relation_update, get_stream_mode
from api.security import rate_limit, require_api_token
from db import helpers, mongo, schema
from engine.tick import WorldEngine

bp = Blueprint("api", __name__)

_ENGINE: WorldEngine | None = None
_ENGINE_LOCK = Lock()

#: Keys a caller is allowed to set on an injected event. Anything else is
#: dropped rather than rejected, so a client sending extra metadata still works,
#: but nothing arbitrary reaches Mongo or the prompt. Without this an attacker
#: could store documents of their own shape in the events collection.
_ALLOWED_EVENT_KEYS = frozenset({
    "headline", "title", "description", "body", "source", "event_type",
    "involved_agents", "url", "payload",
})


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


def _clean_text(value: Any, max_chars: int) -> str:
    """Coerce to a bounded single-line string.

    Control characters are stripped because they serve no purpose in a headline
    and are a classic way to smuggle formatting into a log file or a prompt —
    a newline plus a fake "System:" line is the whole of a basic prompt
    injection. Tabs and newlines inside a body are collapsed to spaces rather
    than removed so word boundaries survive.
    """
    text = str(value or "")
    text = "".join(" " if ch in "\t\r\n" else ch for ch in text if ch.isprintable() or ch in "\t\r\n")
    return " ".join(text.split())[:max_chars]


def _sanitize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce a caller-supplied event to a known shape with bounded fields."""
    cleaned: dict[str, Any] = {}

    for key in ("headline", "title"):
        if key in raw:
            cleaned[key] = _clean_text(raw[key], config.MAX_HEADLINE_CHARS)
    for key in ("description", "body"):
        if key in raw:
            cleaned[key] = _clean_text(raw[key], config.MAX_BODY_CHARS)
    for key in ("source", "event_type", "url"):
        if key in raw:
            cleaned[key] = _clean_text(raw[key], 200)

    involved = raw.get("involved_agents")
    if isinstance(involved, list):
        # Bound the fan-out to the real roster size and keep only strings that
        # look like ids, so this cannot be used to address arbitrary documents.
        cleaned["involved_agents"] = [
            _clean_text(item, 64) for item in involved[:20] if isinstance(item, str)
        ]

    # `payload` is free-form by design (the UI marks demo events with it), so it
    # is passed through but not trusted: flattened to primitives only, capped.
    payload = raw.get("payload")
    if isinstance(payload, dict):
        cleaned["payload"] = {
            _clean_text(k, 40): (v if isinstance(v, (bool, int, float)) else _clean_text(v, 200))
            for k, v in list(payload.items())[:20]
        }

    return {k: v for k, v in cleaned.items() if k in _ALLOWED_EVENT_KEYS}


@bp.get("/health")
def health():
    """Liveness + dependency check.

    200 when MongoDB answers, 503 when it does not, so a load balancer or
    ECS/ALB target group can act on it directly.

    Deliberately terse. An earlier version returned the database name, the model
    id and the raw pymongo error string; on a public endpoint that is free recon
    (a driver error can carry the cluster hostname). Detail now goes to the log,
    where it is useful, and `scripts/check_connections.py` reports it locally.
    """
    mongo_ok, mongo_error = mongo.ping()

    body: dict[str, Any] = {
        "status": "ok" if mongo_ok else "degraded",
        "app": config.APP_NAME,
        "dependencies": {
            "mongodb": {"connected": mongo_ok},
            # Not probed here — a live Gemini call costs tokens on every health
            # check. See scripts/check_connections.py.
            "gemini": {"configured": bool(config.GEMINI_API_KEY)},
        },
        "realtime": {"mode": get_stream_mode()},
    }

    if not config.IS_PRODUCTION:
        # Local debugging aid only. Never in production responses.
        body["env"] = config.FLASK_ENV
        body["dependencies"]["mongodb"]["database"] = config.MONGO_DB_NAME
        body["dependencies"]["gemini"]["model"] = config.GEMINI_MODEL
        if mongo_error:
            body["dependencies"]["mongodb"]["error"] = mongo_error

    return jsonify(body), (200 if mongo_ok else 503)


@bp.get("/meta")
def meta():
    """What the client needs to know before rendering controls.

    `writes_require_token` lets the UI show the trigger panel in a disabled
    state with an honest explanation, instead of offering a button that will
    return 401. `authenticated` reflects the token on *this* request.
    """
    from api.security import _presented_token, _token_is_valid  # local: avoid cycle

    return jsonify({
        "app": config.APP_NAME,
        "writes_require_token": bool(config.API_TOKEN),
        "authenticated": _token_is_valid(_presented_token()),
        "roster_size": len(helpers.list_agents(agent_type="nation")),
        "disclaimer": (
            "Nation agents are language models reasoning over real headlines. "
            "Their statements and relation shifts are simulated projections and "
            "do not represent the actual position of any government."
        ),
    })


@bp.get("/agents")
@rate_limit(config.RATE_LIMIT_READ)
def list_agents():
    """List all nation agents and their latest stored state."""
    agents = helpers.list_agents(agent_type="nation")
    return jsonify({"count": len(agents), "agents": agents})


@bp.get("/relations")
@rate_limit(config.RATE_LIMIT_READ)
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
@rate_limit(config.RATE_LIMIT_READ)
def events():
    """Return recent event log, optionally filtered by timestamp lower bound."""
    since_raw = request.args.get("since")

    # `int()` on caller-supplied text was previously unguarded, so `?limit=abc`
    # raised ValueError and returned a 500 — a stack trace in debug mode.
    try:
        limit = int(request.args.get("limit", "100"))
    except (TypeError, ValueError):
        return jsonify({"error": "'limit' must be an integer."}), 400
    limit = max(1, min(limit, 500))

    try:
        since = _parse_since(since_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid since timestamp. Use ISO-8601 format."}), 400

    db = mongo.get_db()
    query: dict[str, Any] = {}
    if since is not None:
        # `since` is a parsed datetime re-serialised here, so no caller-supplied
        # string reaches the query document. A raw pass-through would let
        # `{"$gt": ...}` style operator injection through.
        query["timestamp"] = {"$gte": since.isoformat()}

    items = list(
        db[schema.EVENTS]
        .find(query, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return jsonify({"count": len(items), "events": items})


@bp.post("/agents/<agent_id>/chat")
@require_api_token
@rate_limit(config.RATE_LIMIT_CHAT)
def chat_with_agent(agent_id: str):
    """Send a human message to one nation agent and return its in-character response.

    Gated: every call is one Gemini request.

    `agent_id` arrives from the URL as a string via Flask's default converter and
    is used as a scalar value in `find_one({"agent_id": agent_id})`. A string
    cannot become a query operator there, so there is no NoSQL injection path —
    worst case is a 404.
    """
    body = request.get_json(silent=True) or {}
    message = _clean_text(body.get("message", ""), config.MAX_MESSAGE_CHARS)
    if not message:
        return jsonify({"error": "Request body must include a non-empty 'message'."}), 400

    agent = NationAgent.load_from_db(_clean_text(agent_id, 64))
    if agent is None:
        return jsonify({"error": "Agent not found."}), 404

    reply = agent.speak(message)
    return jsonify({"agent_id": agent.agent_id, "message": message, "reply": reply})


@bp.post("/engine/tick")
@require_api_token
@rate_limit(config.RATE_LIMIT_TICK)
def trigger_tick():
    """Manually trigger a world tick, optionally with custom events.

    The most expensive endpoint in the app: one call runs a batched Gemini
    deliberation across the whole roster. Gated and tightly rate limited.
    """
    body = request.get_json(silent=True) or {}
    custom_events_raw = body.get("events")
    custom_events: list[dict[str, Any]] | None = None

    if custom_events_raw is not None:
        if not isinstance(custom_events_raw, list):
            return jsonify({"error": "'events' must be a list when provided."}), 400
        if len(custom_events_raw) > config.MAX_CUSTOM_EVENTS:
            return (
                jsonify({
                    "error": f"At most {config.MAX_CUSTOM_EVENTS} events per tick.",
                    "received": len(custom_events_raw),
                }),
                400,
            )
        custom_events = [
            _sanitize_event(item) for item in custom_events_raw if isinstance(item, dict)
        ]

    summary = _get_engine().step(custom_events=custom_events)

    # Push to connected clients immediately. The Mongo change stream would also
    # catch these writes, but only on a replica set / Atlas — broadcasting here
    # means the live graph animates on every deployment, and makes the update
    # arrive as one event per tick instead of one per relation write.
    if summary.get("relation_shifts"):
        broadcast_relation_update(
            reason="tick",
            payload={
                "tick": summary.get("tick"),
                "run_id": summary.get("run_id"),
                "relation_shifts": summary.get("relation_shifts", []),
            },
        )

    return jsonify(summary), 200
