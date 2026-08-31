"""Authentication and hardening for the HTTP layer.

The threat model is specific and worth stating, because it drives every choice
here. This app is a portfolio piece meant to be publicly linkable, and two of its
endpoints spend money: `/engine/tick` runs a batched Gemini call, and
`/agents/<id>/chat` runs one per message. Deployed without a gate, a single
`curl` loop drains the API quota and the graph fills with junk.

So the split is: **reads are public, writes need a key.** Anyone can look at the
world; only the holder of `API_TOKEN` can move it.

Fail-closed matters more than convenience. With no token configured, writes are
refused outright in production and allowed with a loud warning in development —
never the other way round, because the failure mode of guessing wrong is an
unmetered bill.
"""

from __future__ import annotations

import functools
import hmac
import logging
import time
from collections import deque
from threading import Lock
from typing import Callable

from flask import Flask, Response, jsonify, request

import config

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Token check
# --------------------------------------------------------------------------

def _presented_token() -> str:
    """Pull the caller's token from either accepted location.

    `Authorization: Bearer <t>` is the convention most HTTP clients already
    support; `X-API-Key: <t>` is easier to type in a browser console or a fetch
    from the demo UI. Both are read so neither audience is inconvenienced.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def _token_is_valid(presented: str) -> bool:
    """Constant-time comparison against the configured token.

    `hmac.compare_digest` rather than `==` because a plain string comparison
    returns as soon as it finds a mismatched byte, and that timing difference
    leaks the token prefix a byte at a time to anyone patient enough to measure.
    """
    expected = config.API_TOKEN
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def require_api_token(view: Callable) -> Callable:
    """Gate a route behind `API_TOKEN`.

    Applied to every endpoint that writes to the database or calls Gemini.
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not config.API_TOKEN:
            if config.IS_PRODUCTION:
                # Fail closed. An unconfigured deployment must not be an open one.
                log.error(
                    "Refused %s %s: API_TOKEN is unset in production",
                    request.method,
                    request.path,
                )
                return (
                    jsonify(
                        {
                            "error": "This deployment has no API token configured, "
                            "so write endpoints are disabled.",
                            "fix": "Set API_TOKEN in the environment and restart.",
                        }
                    ),
                    503,
                )
            # Development convenience, stated out loud every single time so it
            # cannot quietly become the deployed behaviour.
            log.warning(
                "API_TOKEN is unset — allowing unauthenticated %s %s because "
                "FLASK_ENV=%s. Set API_TOKEN before deploying.",
                request.method,
                request.path,
                config.FLASK_ENV,
            )
            return view(*args, **kwargs)

        if not _token_is_valid(_presented_token()):
            log.warning("Rejected unauthenticated %s %s", request.method, request.path)
            return (
                jsonify(
                    {
                        "error": "Missing or invalid API token.",
                        "hint": "Send it as 'Authorization: Bearer <token>' or 'X-API-Key: <token>'.",
                    }
                ),
                401,
            )

        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
#
# This is not the same thing as the Gemini RPM limiter in llm/. That one paces
# *outbound* calls so Google does not refuse them; it makes a flood of inbound
# requests queue up and hold worker threads, which turns a cost problem into an
# availability problem. This limiter rejects the flood at the door instead.

_rate_lock = Lock()
_hits: dict[str, deque[float]] = {}

#: Requests older than this leave the window.
_WINDOW_SECONDS = 60.0

#: Stop tracking clients that have gone quiet, so a long uptime under scattered
#: traffic cannot grow this dict without bound.
_MAX_TRACKED_CLIENTS = 2048


def _client_key() -> str:
    """Identify the caller for rate-limiting purposes.

    Behind an ALB or any reverse proxy, `remote_addr` is the proxy, so every
    caller would share one bucket. The left-most entry of `X-Forwarded-For` is
    the original client.

    This is spoofable by design — `X-Forwarded-For` is caller-controlled — which
    is acceptable here because the limiter is a cost guard on top of the token
    check, not an authentication boundary. Nothing is authorised by this value.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_rate(key: str, limit: int) -> tuple[bool, int]:
    """Sliding-window counter. Returns (allowed, seconds_until_retry)."""
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS

    with _rate_lock:
        if len(_hits) > _MAX_TRACKED_CLIENTS:
            for stale_key in [k for k, v in _hits.items() if not v or v[-1] < cutoff]:
                del _hits[stale_key]

        window = _hits.setdefault(key, deque())
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(_WINDOW_SECONDS - (now - window[0])) + 1)
            return False, retry_after

        window.append(now)
        return True, 0


def rate_limit(limit_per_minute: int) -> Callable:
    """Reject a caller that exceeds `limit_per_minute` requests in any 60s window."""

    def decorator(view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            allowed, retry_after = _check_rate(
                f"{view.__name__}:{_client_key()}", limit_per_minute
            )
            if not allowed:
                response = jsonify(
                    {
                        "error": "Rate limit exceeded.",
                        "limit": f"{limit_per_minute} requests per minute",
                        "retry_after_seconds": retry_after,
                    }
                )
                response.headers["Retry-After"] = str(retry_after)
                return response, 429
            return view(*args, **kwargs)

        return wrapper

    return decorator


def reset_rate_limits() -> None:
    """Clear all windows. For tests — never call this from a request path."""
    with _rate_lock:
        _hits.clear()


# --------------------------------------------------------------------------
# Response hardening
# --------------------------------------------------------------------------

def _security_headers(response: Response) -> Response:
    """Defence-in-depth headers applied to every response.

    The CSP is the load-bearing one. This app renders model-generated text
    (agent reasoning) into the DOM; React escapes it, so there is no injection
    path today, but a CSP means a future `dangerouslySetInnerHTML` cannot
    silently become a stored-XSS hole.

    `'unsafe-inline'` is present for styles only, because Vite inlines a small
    critical-CSS block into index.html. Scripts get no such exemption.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        # ws:/wss: are required for the Socket.IO upgrade.
        "connect-src 'self' ws: wss:; "
        "worker-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'",
    )

    if config.IS_PRODUCTION:
        # Only meaningful over TLS, and actively harmful on a plain-HTTP dev
        # server: a browser that caches this pins localhost to HTTPS.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    return response


def init_security(app: Flask) -> None:
    """Install request/response hardening on the app."""
    # Cap request bodies. Without this, Flask buffers an arbitrarily large body
    # into memory before any view code runs, so a single POST can exhaust the
    # container. 64 KiB is generous for the largest legitimate payload here
    # (a headline plus a description).
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_REQUEST_BYTES

    app.after_request(_security_headers)

    @app.errorhandler(413)
    def _too_large(_error):
        return (
            jsonify(
                {
                    "error": "Request body too large.",
                    "limit_bytes": config.MAX_REQUEST_BYTES,
                }
            ),
            413,
        )

    @app.errorhandler(500)
    def _server_error(error):
        # Log the detail, return a generic message. A stack trace or a raw
        # exception string in the response can disclose paths, driver versions,
        # or fragments of the Mongo URI.
        log.exception("Unhandled error on %s %s: %s", request.method, request.path, error)
        return jsonify({"error": "Internal server error."}), 500

    if config.IS_PRODUCTION and not config.API_TOKEN:
        log.error(
            "API_TOKEN is not set and FLASK_ENV=%s — write endpoints will refuse "
            "every request until it is configured.",
            config.FLASK_ENV,
        )

    log.info(
        "Security initialised (auth=%s, max_body=%dB)",
        "token" if config.API_TOKEN else "OPEN-dev-only",
        config.MAX_REQUEST_BYTES,
    )
