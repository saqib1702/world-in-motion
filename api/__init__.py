"""Flask application factory."""

import logging
import os

from flask import Flask

import config
from api.realtime import init_socketio, start_relation_stream
from api.routes import bp as api_bp

log = logging.getLogger(__name__)


def create_app() -> Flask:
    config.configure_logging()

    app = Flask(__name__)
    app.config["ENV_NAME"] = config.FLASK_ENV
    app.register_blueprint(api_bp)
    init_socketio(app, cors_allowed_origins=config.SOCKETIO_CORS_ORIGINS)

    should_start_scheduler = config.ENABLE_SCHEDULED_INGESTION and (
        config.FLASK_ENV != "development" or os.getenv("WERKZEUG_RUN_MAIN") == "true"
    )
    if should_start_scheduler:
        from ingestion.scheduler import get_or_start_runner

        get_or_start_runner()
        log.info(
            "Scheduled ingestion enabled (interval=%d min, run_id=%s)",
            config.EVENT_FETCH_INTERVAL_MINUTES,
            config.SCHEDULED_RUN_ID,
        )

    should_start_relation_stream = config.ENABLE_RELATION_STREAM and (
        config.FLASK_ENV != "development" or os.getenv("WERKZEUG_RUN_MAIN") == "true"
    )
    if should_start_relation_stream:
        start_relation_stream()
        log.info("Relation stream enabled for SocketIO clients")

    log.info("%s app created (env=%s)", config.APP_NAME, config.FLASK_ENV)
    return app
