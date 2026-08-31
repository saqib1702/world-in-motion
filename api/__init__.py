"""Flask application factory."""

import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

import config
from api.realtime import init_socketio, start_relation_stream
from api.routes import bp as api_bp
from api.security import init_security

log = logging.getLogger(__name__)

#: Vite build output. Present in the Docker image and after `npm run build`;
#: absent during local development, when the Vite dev server serves the UI.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

#: First path segments owned by the API. A request under one of these that
#: reached the SPA catch-all is a mistyped endpoint, and should get a JSON 404
#: rather than an HTML page — returning index.html there makes a typo look like
#: a parse error in the client.
API_PREFIXES = frozenset({
    "health", "meta", "agents", "relations", "events", "engine", "socket.io",
})


def _register_frontend(app: Flask) -> None:
    """Serve the built SPA from the same origin as the API.

    Same-origin means no CORS and no proxy in production, so the deployed app is
    one container behind one URL. In development this is skipped entirely and
    Vite serves the UI on :5173, proxying API calls to Flask.
    """
    if not FRONTEND_DIST.is_dir():
        log.info("No frontend build at %s — API-only mode", FRONTEND_DIST)
        return

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIST, "index.html")

    # Catch-all for hashed asset files and client-side routes. Werkzeug ranks
    # static rules above converter rules, so the blueprint's explicit API routes
    # still win over this pattern; the API_PREFIXES guard below makes that
    # independent of routing precedence rather than reliant on it.
    @app.get("/<path:requested>")
    def static_or_index(requested: str):
        candidate = (FRONTEND_DIST / requested).resolve()
        # Containment check: reject traversal like ../../etc/passwd before it
        # reaches the filesystem.
        if FRONTEND_DIST in candidate.parents and candidate.is_file():
            return send_from_directory(FRONTEND_DIST, requested)

        if requested.split("/", 1)[0] in API_PREFIXES:
            return jsonify({"error": f"No such endpoint: /{requested}"}), 404

        # Unknown non-API path: hand back the SPA shell so client-side routing
        # works on a hard refresh of a deep link.
        return send_from_directory(FRONTEND_DIST, "index.html")

    log.info("Serving frontend build from %s", FRONTEND_DIST)


def create_app() -> Flask:
    config.configure_logging()

    app = Flask(__name__)
    app.config["ENV_NAME"] = config.FLASK_ENV

    # Before anything else registers a route: installs the body-size cap, the
    # security headers and the error handlers that keep internals out of
    # responses.
    init_security(app)

    app.register_blueprint(api_bp)
    _register_frontend(app)
    init_socketio(app, cors_allowed_origins=config.SOCKETIO_CORS_ORIGINS)

    # NOTE: these used to be additionally gated on
    # `os.getenv("WERKZEUG_RUN_MAIN") == "true"` to avoid double-starting under
    # Flask's debug reloader. That guard became dead code when the reloader was
    # disabled (use_reloader=False, the fix for the Atlas SSL handshake issue):
    # WERKZEUG_RUN_MAIN is then never set, so in development the scheduler and
    # the relation stream silently never started. Both start-up helpers are
    # already idempotent via their own module-level flags, so the feature flag
    # alone is the correct condition.
    if config.ENABLE_SCHEDULED_INGESTION:
        from ingestion.scheduler import get_or_start_runner

        get_or_start_runner()
        log.info(
            "Scheduled ingestion enabled (interval=%d min, run_id=%s)",
            config.EVENT_FETCH_INTERVAL_MINUTES,
            config.SCHEDULED_RUN_ID,
        )

    if config.ENABLE_RELATION_STREAM:
        start_relation_stream()
        log.info("Relation stream enabled for SocketIO clients")

    log.info("%s app created (env=%s)", config.APP_NAME, config.FLASK_ENV)
    return app
