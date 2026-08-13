"""Development entrypoint: `python app.py`.

For production (AWS), point gunicorn at the factory instead:
    gunicorn "api:create_app()" --bind 0.0.0.0:8080
"""

import config
from api import create_app
from api.realtime import socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_ENV == "development",
    )
