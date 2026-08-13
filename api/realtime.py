"""Socket.IO realtime event streaming for relation updates."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from flask_socketio import SocketIO, emit
from pymongo.errors import PyMongoError

from db import helpers

log = logging.getLogger(__name__)

socketio = SocketIO(async_mode="threading")

_watch_started = False
_watch_lock = threading.Lock()


def init_socketio(app: Any, cors_allowed_origins: str) -> None:
    socketio.init_app(app, cors_allowed_origins=cors_allowed_origins)


def start_relation_stream() -> None:
    global _watch_started
    with _watch_lock:
        if _watch_started:
            return
        socketio.start_background_task(_relation_change_publisher)
        _watch_started = True
        log.info("SocketIO relation stream background task started")


def _relation_change_publisher() -> None:
    while True:
        try:
            for change in helpers.watch_relations(full_document="updateLookup"):
                payload = {
                    "operation": change.get("operationType"),
                    "documentKey": change.get("documentKey", {}),
                    "fullDocument": change.get("fullDocument", {}),
                    "clusterTime": str(change.get("clusterTime")) if change.get("clusterTime") else None,
                }
                socketio.emit("relation_update", payload)
        except (PyMongoError, NotImplementedError, TypeError, ValueError) as exc:
            # Change streams require replica set/Atlas; retry loop keeps service alive.
            log.warning("Relation change stream unavailable: %s", exc)
            socketio.emit("relation_stream_error", {"error": str(exc)})
            time.sleep(5)
        except Exception as exc:
            log.exception("Unexpected relation stream error: %s", exc)
            time.sleep(5)


@socketio.on("connect")
def _on_connect() -> None:
    emit("connected", {"ok": True, "channel": "relations"})
