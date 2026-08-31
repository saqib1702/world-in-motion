"""Gemini API client.

Thin wrapper over the google-genai SDK so the rest of the codebase never
imports the vendor SDK directly — swapping models or providers stays a
one-file change. Agent-specific prompting belongs in /agents, not here.
"""

import collections
import json
import logging
import os
import re
import threading
import time
from typing import Optional

from google import genai
from google.genai import types

import config

log = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


class RateLimiter:
    """Sliding window thread-safe rate limiter ensuring max_calls within period_seconds."""

    def __init__(self, max_calls: int = 5, period_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.timestamps: collections.deque[float] = collections.deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a request slot becomes available within the sliding window."""
        while True:
            with self.lock:
                now = time.time()
                while self.timestamps and (now - self.timestamps[0]) >= self.period_seconds:
                    self.timestamps.popleft()

                if len(self.timestamps) < self.max_calls:
                    self.timestamps.append(now)
                    return

                wait_time = self.period_seconds - (now - self.timestamps[0]) + 0.1
                log.info(
                    "Gemini rate limit threshold reached (%d/%d calls in %.0fs window). Throttling next API call for %.1fs...",
                    len(self.timestamps),
                    self.max_calls,
                    self.period_seconds,
                    wait_time,
                )
            time.sleep(wait_time)


GEMINI_RATE_LIMITER = RateLimiter(
    max_calls=getattr(config, "GEMINI_MAX_RPM", 5),
    period_seconds=60.0,
)


def get_client() -> genai.Client:
    """Return the shared Gemini client, creating it on first use."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            log.warning("GEMINI_API_KEY is not set. API calls will use mock response fallback.")
            api_key = "mock_key"
        _client = genai.Client(api_key=api_key)
        log.info("Gemini client created (model=%s)", config.GEMINI_MODEL)
    return _client


# --- Model resolution -------------------------------------------------------
# A wrong GEMINI_MODEL is the single most common way this app "runs" while doing
# no real reasoning: every call 404s, falls through to the mock generator, and
# the simulation looks alive. So the first NOT_FOUND triggers a one-time walk of
# the fallback chain, and the winner is cached for the process.

_resolved_model: Optional[str] = None
_model_lock = threading.Lock()


def _is_not_found(err: str) -> bool:
    lowered = err.lower()
    return "404" in lowered or "not_found" in lowered or "not found" in lowered


def _is_rate_limited(err: str) -> bool:
    lowered = err.lower()
    return (
        "429" in lowered
        or "resource_exhausted" in lowered
        or "rate limit" in lowered
        or "quota" in lowered
    )


def active_model() -> str:
    """The model currently in use (post-fallback), for diagnostics."""
    return _resolved_model or config.GEMINI_MODEL


def list_available_models() -> list[str]:
    """Model IDs this API key can actually call. Raises on transport failure."""
    return [
        getattr(m, "name", str(m)).replace("models/", "")
        for m in get_client().models.list()
    ]


def _candidate_models(explicit: Optional[str]) -> list[str]:
    """Configured model first, then the fallback chain, de-duplicated."""
    if explicit:
        return [explicit]
    ordered = [_resolved_model or config.GEMINI_MODEL]
    ordered += [m for m in config.GEMINI_FALLBACK_MODELS]
    seen: set[str] = set()
    unique = []
    for m in ordered:
        if m and m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


def _remember_model(model: str) -> None:
    global _resolved_model
    with _model_lock:
        if _resolved_model != model:
            _resolved_model = model
            if model != config.GEMINI_MODEL:
                log.warning(
                    "GEMINI_MODEL=%r is not available to this API key. "
                    "Falling back to %r for the rest of this process — "
                    "update your .env to make this permanent.",
                    config.GEMINI_MODEL,
                    model,
                )


def _mock_roster() -> list[tuple[str, str]]:
    """(agent_id, display_name) for the seeded roster.

    Imported lazily: db.seed imports agents.nation, which imports this module,
    so a module-level import would be circular.
    """
    try:
        from db.seed import STARTER_NATIONS

        return [(n["agent_id"], n["name"]) for n in STARTER_NATIONS]
    except Exception:  # pragma: no cover - only if seed data is unavailable
        return []


def _mock_actor_name(system_instruction: str) -> str:
    """Best-effort extraction of which actor a single-agent prompt is for."""
    patterns = (
        r"strategic decision-making of ([^,\n]+)",  # _build_decide_system_prompt
        r"leadership of ([^,\n]+)",                 # speak()
    )
    for pattern in patterns:
        match = re.search(pattern, system_instruction)
        if match:
            return match.group(1).strip()
    return "Nation"


def _mock_decision_for(actor_name: str, prompt: str, roster: list[str]) -> dict:
    """One plausible mock decision, guaranteed to name a real roster target.

    The target must resolve through helpers.resolve_agent_id or the relation
    write is silently dropped, so this only ever picks from the live roster —
    never a hardcoded name that a reseed could retire.
    """
    others = [n for n in roster if n.lower() != actor_name.lower()]

    target_country = "None"
    if others:
        # Prefer an actor the events actually mention; that makes demo mode
        # trace back to the headline the same way live mode does.
        mentioned = [n for n in others if n.lower() in prompt.lower()]
        pool = mentioned or others
        # Deterministic per actor, so a demo run is reproducible but not uniform.
        target_country = pool[hash(actor_name) % len(pool)]

    lowered = prompt.lower()
    if "tariff" in lowered or "sanction" in lowered or "export control" in lowered:
        action_type, delta = "impose_sanction", -15.0
        reasoning = (
            f"{actor_name} weighs retaliatory trade measures against {target_country} "
            f"to protect exposed domestic sectors."
        )
    elif "treaty" in lowered or "alliance" in lowered or "summit" in lowered or "accord" in lowered:
        action_type, delta = "propose_alliance", 15.0
        reasoning = (
            f"{actor_name} seeks to convert the current opening into a durable "
            f"arrangement with {target_country}."
        )
    elif "military" in lowered or "naval" in lowered or "missile" in lowered or "troops" in lowered:
        action_type, delta = "military_warning", -12.0
        reasoning = (
            f"{actor_name} signals that continued escalation by {target_country} "
            f"would draw a proportionate response."
        )
    elif "energy" in lowered or "oil" in lowered or "gas" in lowered or "opec" in lowered:
        action_type, delta = "trade_agreement", 10.0
        reasoning = (
            f"{actor_name} moves to secure supply terms with {target_country} while "
            f"prices remain favourable."
        )
    else:
        action_type, delta = "issue_statement", -5.0
        reasoning = (
            f"{actor_name} issues a measured statement on the reported developments "
            f"involving {target_country}."
        )

    if target_country == "None":
        action_type, delta = "ignore", 0.0
        reasoning = f"{actor_name} sees no material stake in the reported developments."

    return {
        "action_type": action_type,
        "target_country": target_country,
        "reasoning": f"[DEMO MODE — not live model reasoning] {reasoning}",
        "relation_delta": delta,
    }


def _generate_mock_fallback(
    prompt: str,
    system_instruction: Optional[str] = None,
    response_mime_type: Optional[str] = None,
    response_schema: Optional[dict] = None,
) -> str:
    """Structured mock JSON or text, for when Gemini is unconfigured or offline.

    Every reasoning string is prefixed "[DEMO MODE ...]" so simulated-but-fake
    output is never mistaken for live model reasoning in the UI or the DB.
    """
    sys_str = system_instruction or ""

    if response_mime_type != "application/json":
        return (
            f"[DEMO MODE — not live model reasoning] As the representative of "
            f"{_mock_actor_name(sys_str)}, we remain committed to our core interests, "
            f"regional stability, and continued diplomatic engagement."
        )

    roster = _mock_roster()
    names = [name for _agent_id, name in roster]

    # The batched world-deliberation call expects {"decisions": [...]}, one entry
    # per actor. Detected from the prompt's roster marker or the schema shape so
    # demo mode exercises the same code path as live mode.
    wants_batch = "### ACTOR ROSTER" in prompt or (
        isinstance(response_schema, dict)
        and "decisions" in (response_schema.get("properties") or {})
    )

    if wants_batch and roster:
        decisions = []
        for agent_id, name in roster:
            decision = _mock_decision_for(name, prompt, names)
            decision["agent_id"] = agent_id
            decisions.append(decision)
        return json.dumps({"decisions": decisions})

    return json.dumps(_mock_decision_for(_mock_actor_name(sys_str), prompt, names))


def generate(
    prompt: str,
    *,
    system_instruction: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    response_mime_type: Optional[str] = None,
    response_schema: Optional[dict] = None,
    max_retries: int = 2,
    initial_backoff: float = 2.0,
) -> str:
    """Send one prompt, return response text.

    Two distinct failure modes are handled differently, because retrying the
    wrong one wastes the whole rate-limit budget:

    * **Model unavailable (404/NOT_FOUND)** — retrying is pointless, the ID will
      never appear. Move straight to the next model in
      ``config.GEMINI_FALLBACK_MODELS`` and cache whichever one answers.
    * **Rate limit / transport error (429, 5xx, timeout)** — the model is fine,
      so back off exponentially and retry the *same* model.

    Only after every candidate is exhausted does this fall back to the mock
    generator, so a continuous run degrades loudly rather than silently.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return _generate_mock_fallback(
            prompt, system_instruction, response_mime_type, response_schema
        )

    cfg = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        response_mime_type=response_mime_type,
        response_schema=response_schema,
    )

    candidates = _candidate_models(model)
    last_error: Optional[Exception] = None

    for candidate in candidates:
        backoff = initial_backoff
        for attempt in range(max_retries + 1):
            try:
                GEMINI_RATE_LIMITER.acquire()
                response = get_client().models.generate_content(
                    model=candidate,
                    contents=prompt,
                    config=cfg,
                )
                if model is None:
                    _remember_model(candidate)
                return response.text or ""
            except Exception as exc:
                last_error = exc
                err_str = str(exc)

                if _is_not_found(err_str):
                    # Dead model ID — no amount of retrying resurrects it.
                    log.warning("Gemini model %r unavailable (404/NOT_FOUND).", candidate)
                    break

                if attempt < max_retries:
                    log.warning(
                        "Gemini call to %r failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        candidate,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue

                if _is_rate_limited(err_str):
                    # Quota is per-model on the free tier, so a sibling model may
                    # still have headroom. Worth trying the next candidate.
                    log.warning(
                        "Gemini model %r is rate limited/quota exhausted after %d attempts. "
                        "Trying next fallback model.",
                        candidate,
                        max_retries + 1,
                    )
                    break

                log.warning(
                    "Gemini call to %r failed after %d attempts: %s.",
                    candidate,
                    max_retries + 1,
                    exc,
                )
                break

    if _is_not_found(str(last_error or "")):
        # Every candidate 404'd, which almost always means the key is scoped to a
        # different model family. Show what it can actually reach so the fix is
        # a one-line .env edit rather than a guessing game.
        try:
            available = list_available_models()
            log.error(
                "No configured Gemini model is available to this API key. "
                "Models this key CAN call: %s. Set GEMINI_MODEL in .env to one of these.",
                ", ".join(available) or "(none returned)",
            )
        except Exception as list_exc:
            log.warning("Could not list available Gemini models: %s", list_exc)

    log.warning(
        "All Gemini candidates exhausted (%s). Falling back to mock generator — "
        "agent output is NOT live reasoning.",
        ", ".join(candidates),
    )
    return _generate_mock_fallback(
        prompt, system_instruction, response_mime_type, response_schema
    )



def healthcheck() -> tuple[bool, Optional[str]]:
    """Confirm the API key works with a minimal live call. (ok, error).

    Costs a few tokens, so this is not wired into the /health route — it is
    called on demand by scripts/check_connections.py.
    """
    try:
        text = generate("Reply with the single word: ok")
        return bool(text.strip()), None
    except Exception as exc:  # SDK raises a variety of transport/API errors
        log.warning("Gemini healthcheck failed: %s", exc)
        return False, str(exc)
