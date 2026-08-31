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
GEMINI_MAX_RPM = int(os.getenv("GEMINI_MAX_RPM", "5"))

# Ordered fallback chain tried when GEMINI_MODEL returns 404/NOT_FOUND — a
# mistyped or retired model ID would otherwise send every agent to the mock
# generator for the whole run, which looks like the simulation working while
# no real reasoning is happening. First model that answers is cached for the
# process. Override with a comma-separated list.
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash,gemini-1.5-flash",
    ).split(",")
    if m.strip()
]

# --- Flask ---
FLASK_ENV = os.getenv("FLASK_ENV", "development")
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

APP_NAME = "world-in-motion"

#: Anything that is not explicitly "development" is treated as production, so a
#: typo in FLASK_ENV fails toward the stricter behaviour rather than away from it.
IS_PRODUCTION = FLASK_ENV.strip().lower() != "development"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Security ---
# Shared secret for the endpoints that write to the database or spend Gemini
# tokens. Reads stay public so the deployed demo is linkable; writes do not,
# because /engine/tick costs money on every call.
#
# Unset means: allowed with a warning in development, refused in production.
# See api/security.py for the reasoning behind failing closed.
API_TOKEN = os.getenv("API_TOKEN", "")

#: Largest request body Flask will buffer. Generous for a headline plus a
#: description, small enough that a flood cannot exhaust container memory.
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(64 * 1024)))

#: Per-client sliding-window caps, requests per minute. The tick limit is low on
#: purpose: one tick is one batched Gemini call across ten actors, and the free
#: tier allows five requests per minute in total.
RATE_LIMIT_TICK = int(os.getenv("RATE_LIMIT_TICK", "6"))
RATE_LIMIT_CHAT = int(os.getenv("RATE_LIMIT_CHAT", "20"))
RATE_LIMIT_READ = int(os.getenv("RATE_LIMIT_READ", "240"))

#: Input caps. These bound both the Mongo document size and the prompt token
#: cost of a single request.
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
MAX_HEADLINE_CHARS = int(os.getenv("MAX_HEADLINE_CHARS", "300"))
MAX_BODY_CHARS = int(os.getenv("MAX_BODY_CHARS", "4000"))
MAX_CUSTOM_EVENTS = int(os.getenv("MAX_CUSTOM_EVENTS", "5"))


# --- Current event ingestion ---
# Bias the upstream feeds toward stories that name the modelled actors AND carry
# a geopolitical verb. ingestion/fetcher.py still entity-matches every result, so
# these queries are a bandwidth filter rather than the relevance decision.
GDELT_QUERY = os.getenv(
    "GDELT_QUERY",
    "(sanctions OR tariff OR trade OR diplomacy OR military OR semiconductor OR energy OR summit) "
    "(china OR russia OR india OR japan OR brazil OR turkey OR \"united states\" OR "
    "\"european union\" OR \"united kingdom\" OR \"saudi arabia\")",
)
GOOGLE_NEWS_QUERY = os.getenv(
    "GOOGLE_NEWS_QUERY",
    "geopolitics OR sanctions OR tariffs OR trade war OR diplomatic summit OR "
    "military alliance OR semiconductor export controls OR OPEC",
)
EVENT_FETCH_MAX_RECORDS = int(os.getenv("EVENT_FETCH_MAX_RECORDS", "75"))
EVENT_FETCH_MAX_ITEMS = int(os.getenv("EVENT_FETCH_MAX_ITEMS", "6"))

# --- Scheduled world tick ingestion job ---
ENABLE_SCHEDULED_INGESTION = _env_bool("ENABLE_SCHEDULED_INGESTION", False)
EVENT_FETCH_INTERVAL_MINUTES = int(os.getenv("EVENT_FETCH_INTERVAL_MINUTES", "15"))
SCHEDULED_RUN_ID = os.getenv("SCHEDULED_RUN_ID", "scheduled_world_run")

# --- Realtime updates (Socket.IO + Mongo Change Stream) ---
ENABLE_RELATION_STREAM = _env_bool("ENABLE_RELATION_STREAM", True)

# Socket.IO origin allow-list. Defaults to the two local dev origins rather than
# "*", because a wildcard lets any page on the internet open a socket against a
# deployed instance. In production set this to the real site origin, e.g.
#   SOCKETIO_CORS_ORIGINS=https://worldinmotion.saqibshahbaz.me
# A literal "*" is still honoured if explicitly asked for, so the escape hatch
# exists — it is just no longer the default nobody chose.
SOCKETIO_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "SOCKETIO_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if o.strip()
]

# Collapse a lone "*" back to the bare string. python-engineio only treats the
# *string* "*" as "allow everything"; given the list ["*"] it compares the
# request Origin against the literal character and refuses every connection —
# so passing the list through would silently break realtime for anyone who set
# the wildcard on purpose.
if SOCKETIO_CORS_ORIGINS == ["*"]:
    SOCKETIO_CORS_ORIGINS = "*"  # type: ignore[assignment]


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
