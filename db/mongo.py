"""MongoDB connection management.

One lazily-created MongoClient is shared process-wide — MongoClient owns an
internal connection pool and is thread-safe, so creating more than one per
process wastes connections against the Atlas cluster limit.
"""

import logging
import os
from typing import Optional


import certifi
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

import config

log = logging.getLogger(__name__)

_client: Optional[MongoClient] = None


def _uses_tls(uri: str) -> bool:
    """Atlas SRV URIs are TLS by default; a plain local mongod usually is not.

    We only pass tlsCAFile when TLS is actually in play — pymongo rejects the
    option otherwise.
    """
    lowered = uri.lower()
    return (
        lowered.startswith("mongodb+srv://")
        or "tls=true" in lowered
        or "ssl=true" in lowered
    )


def get_client() -> MongoClient:
    """Return the shared MongoClient, creating it on first use.

    If MONGO_URI is not set, falls back to in-memory `mongomock.MongoClient`
    so tests and local runs work seamlessly without requiring a live Atlas cluster.
    """
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI", "")
        if not uri:
            try:
                import mongomock
                log.info("MONGO_URI not set. Using in-memory `mongomock` client.")
                _client = mongomock.MongoClient()
                return _client
            except ImportError:
                uri = config.require("MONGO_URI")

        kwargs = {
            "serverSelectionTimeoutMS": config.MONGO_TIMEOUT_MS,
            "appname": config.APP_NAME,
        }
        if _uses_tls(uri):
            kwargs["tlsCAFile"] = certifi.where()
        _client = MongoClient(uri, **kwargs)
        log.info("MongoClient created (db=%s)", config.MONGO_DB_NAME)
    return _client



def get_db() -> Database:
    """Return the application database handle."""
    return get_client()[config.MONGO_DB_NAME]


def ping() -> tuple[bool, Optional[str]]:
    """Round-trip the server. Returns (ok, error_message).

    Never raises — callers such as the health check want a status, not an
    exception.
    """
    try:
        get_client().admin.command("ping")
        return True, None
    except (PyMongoError, config.MissingConfig) as exc:
        log.warning("MongoDB ping failed: %s", exc)
        return False, str(exc)


def close_client() -> None:
    """Close the shared client. Mainly for tests and clean shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
