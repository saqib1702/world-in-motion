"""Socket.IO realtime event streaming for relation updates.

Two independent push paths feed the frontend, because each covers the other's
blind spot:

1. **Change stream** (`_relation_change_publisher`) — tails MongoDB's oplog and
   pushes any write to `relations`, including ones this process did not make
   (the scheduled ingestion job, a second worker, a manual `mongosh` edit).
   Requires a replica set or Atlas; a standalone mongod or mongomock raises,
   which is why path 2 exists.

2. **Direct broadcast** (`broadcast_relation_update`) — called explicitly after
   a tick completes. Works on any deployment, so the demo still animates live
   when change streams are unavailable.

Duplicate deliveries are harmless: the frontend refetches `/relations` on any
`relation_update`, which is idempotent.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from flask_socketio import SocketIO, emit
from pymongo.errors import PyMongoError

from db import helpers

log = logging.getLogger(__name__)

socketio = SocketIO(async_mode="threading")

_watch_started = False
_watch_lock = threading.Lock()

# Set once we know change streams are unsupported here, so the frontend can show
# "live via tick broadcast" rather than pretending the oplog tail is running.
_stream_mode = "starting"

# Retry policy for the change-stream tail. Without a ceiling a standalone mongod
# produces an error emit every 5s forever, which floods both the log and every
# connected client.
_RETRY_BASE_SECONDS = 5
_RETRY_MAX_SECONDS = 300
_MAX_UNSUPPORTED_RETRIES = 3


def init_socketio(app: Any, cors_allowed_origins: str) -> None:
    socketio.init_app(app, cors_allowed_origins=cors_allowed_origins)


def get_stream_mode() -> str:
    """Current push mode: starting | change_stream | broadcast_only | error."""
    return _stream_mode


def start_relation_stream() -> None:
    global _watch_started
    with _watch_lock:
        if _watch_started:
            return
        socketio.start_background_task(_relation_change_publisher)
        _watch_started = True
        log.info("SocketIO relation stream background task started")


def broadcast_relation_update(reason: str = "tick", payload: Optional[dict] = None) -> None:
    """Push a relation_update to all clients regardless of change-stream support.

    Safe to call from a request thread — Socket.IO handles the fan-out.
    """
    body = {
        "operation": "broadcast",
        "reason": reason,
        "documentKey": {},
        "fullDocument": payload or {},
    }
    try:
        socketio.emit("relation_update", body)
        log.debug("Broadcast relation_update (reason=%s)", reason)
    except Exception as exc:
        # A failed push must never break the HTTP response that triggered it.
        log.warning("Failed to broadcast relation_update: %s", exc)


def _relation_change_publisher() -> None:
    global _stream_mode
    unsupported_retries = 0
    backoff = _RETRY_BASE_SECONDS

    while True:
        try:
            for change in helpers.watch_relations(full_document="updateLookup"):
                if _stream_mode != "change_stream":
                    _stream_mode = "change_stream"
                    log.info("Relation change stream is live")
                # A successful read means the connection is healthy again.
                unsupported_retries = 0
                backoff = _RETRY_BASE_SECONDS

                payload = {
                    "operation": change.get("operationType"),
                    "documentKey": change.get("documentKey", {}),
                    "fullDocument": change.get("fullDocument", {}),
                    "clusterTime": str(change.get("clusterTime")) if change.get("clusterTime") else None,
                }
                socketio.emit("relation_update", payload)

        except (PyMongoError, NotImplementedError, TypeError, ValueError) as exc:
            unsupported_retries += 1
            log.warning(
                "Relation change stream unavailable (attempt %d): %s",
                unsupported_retries,
                exc,
            )

            if unsupported_retries >= _MAX_UNSUPPORTED_RETRIES:
                # Stop tailing. Ticks still push via broadcast_relation_update,
                # so the UI stays live — it just won't see out-of-process writes.
                _stream_mode = "broadcast_only"
                socketio.emit("relation_stream_error", {
                    "error": str(exc),
                    "mode": "broadcast_only",
                    "detail": (
                        "MongoDB change streams need a replica set or Atlas. "
                        "Falling back to per-tick broadcasts."
                    ),
                })
                log.warning(
                    "Giving up on change stream after %d attempts; "
                    "realtime continues via per-tick broadcasts.",
                    unsupported_retries,
                )
                return

            socketio.emit("relation_stream_error", {"error": str(exc), "mode": "retrying"})
            time.sleep(backoff)
            backoff = min(backoff * 2, _RETRY_MAX_SECONDS)

        except Exception as exc:
            _stream_mode = "error"
            log.exception("Unexpected relation stream error: %s", exc)
            time.sleep(backoff)
            backoff = min(backoff * 2, _RETRY_MAX_SECONDS)


@socketio.on("connect")
def _on_connect() -> None:
    emit("connected", {"ok": True, "channel": "relations", "mode": _stream_mode})
