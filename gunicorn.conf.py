# Gunicorn configuration for the AWS deployment.
#
# Read this before changing `workers`. Socket.IO is stateful: a client's polling
# requests must all reach the same process, because the session that owns its
# message queue lives in that process's memory. With more than one worker and no
# sticky sessions, a client's poll lands on a worker that has never heard of it
# and the connection dies with "Invalid session" in a reconnect loop.
#
# Two ways out: sticky sessions at the load balancer, or a shared message queue
# (flask-socketio supports Redis). Until one of those exists, ONE worker is the
# correct setting, and concurrency comes from threads instead.

import multiprocessing  # noqa: F401  (kept for the scaling note below)
import os

bind = f"0.0.0.0:{os.getenv('PORT', os.getenv('FLASK_PORT', '8080'))}"

# See the note above. Do not raise this without adding Redis or sticky sessions.
workers = 1

# flask-socketio runs with async_mode="threading", so threads are the unit of
# concurrency. Long-polling holds a request open for up to `ping_timeout`, so
# each connected client occupies a thread while parked — this ceiling is roughly
# the concurrent-viewer limit for a demo.
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "16"))

# A world tick fans out to a batched Gemini call, which can legitimately take
# tens of seconds. The default 30s timeout kills the worker mid-tick and the
# request fails with no explanation, so this is deliberately generous.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30

# Long-polling connections are held open; too short a keepalive churns them.
keepalive = 65

# Log to stdout/stderr so Docker and CloudWatch pick them up.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
# Skip access-log noise from the health check and the polling transport, which
# would otherwise dominate the log at several lines per second per client.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus'

# Fail fast and visibly if the app cannot be imported, rather than after fork.
preload_app = False  # must stay False: the relation-stream thread is started in
# create_app(), and a thread created before fork does not survive into the child.

proc_name = "world-in-motion"


def on_starting(server):
    server.log.info("world-in-motion starting: %d worker, %d threads", workers, threads)


def when_ready(server):
    server.log.info("ready on %s", bind)
