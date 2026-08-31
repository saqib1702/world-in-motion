"""Development entrypoint: `python app.py`.

For production (AWS), point gunicorn at the factory instead — see
gunicorn.conf.py, which also explains why the worker count is fixed at one:
    gunicorn --config gunicorn.conf.py "api:create_app()"
"""

import logging

import config
from api import create_app
from api.realtime import socketio

log = logging.getLogger(__name__)

app = create_app()


def _debugger_is_safe() -> bool:
    """Whether it is safe to enable the Werkzeug debugger.

    The interactive debugger executes arbitrary Python from the browser on any
    unhandled exception. That is a remote code execution hole the moment the
    server is reachable by anyone else, so it is allowed only when the app is
    both in development mode *and* bound to loopback.

    `FLASK_ENV` defaults to "development", so without this check a single
    `FLASK_HOST=0.0.0.0` — the natural thing to type when testing from a phone
    on the same wifi — would expose a Python console to the whole network.
    """
    if config.IS_PRODUCTION:
        return False
    return config.FLASK_HOST in {"127.0.0.1", "localhost", "::1"}


if __name__ == "__main__":
    debug = _debugger_is_safe()

    if not debug and not config.IS_PRODUCTION:
        log.warning(
            "Debugger disabled: FLASK_HOST=%s is not loopback. The Werkzeug "
            "console would be remote code execution for anyone who can reach "
            "this port.",
            config.FLASK_HOST,
        )

    socketio.run(
        app,
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=debug,
        # Must stay False. Flask's reloader spawns a second process, and two
        # processes sharing one MongoClient produced the Atlas SSL handshake
        # failures that looked like a TLS problem. Cost: no auto-restart on save.
        use_reloader=False,
        allow_unsafe_werkzeug=False,
    )
