"""Central configuration. Every env var the app reads is declared here."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent."""


# --- MongoDB ---
MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "world_in_motion")
MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "5000"))

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Flask ---
FLASK_ENV = os.getenv("FLASK_ENV", "development")
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

APP_NAME = "world-in-motion"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Current event ingestion ---
GDELT_QUERY = os.getenv(
    "GDELT_QUERY",
    "(trade OR sanctions OR tariff OR cybersecurity OR semiconductor OR military OR energy OR climate OR diplomacy)",
)
GOOGLE_NEWS_QUERY = os.getenv(
    "GOOGLE_NEWS_QUERY",
    "geopolitics OR sanctions OR trade OR cybersecurity OR climate treaty OR military alliance OR semiconductor",
)
EVENT_FETCH_MAX_RECORDS = int(os.getenv("EVENT_FETCH_MAX_RECORDS", "40"))
EVENT_FETCH_MAX_ITEMS = int(os.getenv("EVENT_FETCH_MAX_ITEMS", "6"))

# --- Scheduled world tick ingestion job ---
ENABLE_SCHEDULED_INGESTION = _env_bool("ENABLE_SCHEDULED_INGESTION", False)
EVENT_FETCH_INTERVAL_MINUTES = int(os.getenv("EVENT_FETCH_INTERVAL_MINUTES", "15"))
SCHEDULED_RUN_ID = os.getenv("SCHEDULED_RUN_ID", "scheduled_world_run")

# --- Realtime updates (Socket.IO + Mongo Change Stream) ---
ENABLE_RELATION_STREAM = _env_bool("ENABLE_RELATION_STREAM", True)
SOCKETIO_CORS_ORIGINS = os.getenv("SOCKETIO_CORS_ORIGINS", "*")


def require(name: str) -> str:
    """Return env var `name`, or raise with a pointer to .env.example."""
    value = os.getenv(name, "")
    if not value:
        raise MissingConfig(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
