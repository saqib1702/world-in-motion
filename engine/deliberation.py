"""Batched world deliberation: one Gemini call decides for every actor.

Why this exists
---------------
The original design gave each nation its own Gemini call, run through a thread
pool. That is conceptually tidy but collides with the rate limiter: at
`GEMINI_MAX_RPM = 5`, ten actors cost ten requests, so a single tick took over
two minutes and a continuous run spent its whole quota re-deciding the same
world. Free-tier quota is the binding constraint on this project, not model
throughput.

Batching all actors into one structured response makes a tick cost exactly one
request. Tick latency drops from minutes to seconds and the quota supports
continuous operation.

The tradeoff, stated plainly: actors now reason in a shared context rather than
in isolation, so they can see each other's situation. In practice this reads as
*better* geopolitics — real foreign ministries also act with knowledge of what
their counterparts are facing — but it is a genuine departure from independent
simulation, and it means one malformed response affects every actor. Hence the
per-actor fallback in `deliberate()`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from agents.base import Observation
from agents.nation import DECIDE_SCHEMA, NationAgent
from llm import gemini

log = logging.getLogger(__name__)

ACTION_TYPES = [
    "propose_alliance",
    "issue_statement",
    "impose_sanction",
    "trade_agreement",
    "military_warning",
    "ignore",
]

#: One object per actor, keyed back to the roster by agent_id.
BATCH_DECIDE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "agent_id": {"type": "STRING"},
                    "action_type": {"type": "STRING", "enum": ACTION_TYPES},
                    "target_country": {"type": "STRING"},
                    "reasoning": {"type": "STRING"},
                    "relation_delta": {"type": "NUMBER"},
                },
                "required": [
                    "agent_id",
                    "action_type",
                    "target_country",
                    "reasoning",
                    "relation_delta",
                ],
            },
        }
    },
    "required": ["decisions"],
}

_SYSTEM_PROMPT = (
    "You are a geopolitical analysis engine driving a multi-actor simulation.\n\n"
    "You will be given a roster of real state and bloc actors, each with its structural "
    "interests, current alignment, and current pairwise relation standings, plus a set of "
    "real news events from the last cycle.\n\n"
    "For EVERY actor in the roster, decide the single action that actor would most "
    "plausibly take in response to these events, reasoning from that actor's own "
    "incentives rather than from any global notion of what is desirable.\n\n"
    "Rules:\n"
    "1. Return exactly one decision object per actor, and use the exact agent_id given.\n"
    "2. target_country must be copied verbatim from the roster's display names, or be "
    "exactly \"None\". Never target the acting actor itself. Never invent an actor.\n"
    "3. relation_delta is between -20.0 and +20.0 and describes how the acting actor's "
    "relationship with target_country changes. Use 0.0 with target \"None\" for inaction.\n"
    "4. Ground each reasoning string in the specific events supplied. Two or three "
    "sentences, analytical in tone.\n"
    "5. This is a projection, not reporting. Do not fabricate quotations from real named "
    "officials and do not state simulated decisions as though they were announced policy.\n"
    "6. Actors should not all react to everything. Inaction (\"ignore\") is the correct "
    "answer for an actor with no material stake in the events shown."
)


def _build_user_prompt(agents: list[NationAgent], observation: Observation) -> str:
    roster = [agent.persona_brief() for agent in agents]
    valid_targets = sorted(agent.name for agent in agents)

    events_lines = []
    for idx, evt in enumerate(observation.events, start=1):
        headline = evt.get("headline", "Untitled event")
        description = str(evt.get("description", ""))[:400]
        involved = evt.get("involved_agents", [])
        events_lines.append(
            f"{idx}. {headline}\n"
            f"   detail: {description or '(no summary provided)'}\n"
            f"   actors named in the source: {', '.join(involved) if involved else '(none identified)'}"
        )
    events_block = "\n".join(events_lines) if events_lines else "(no events this cycle)"

    return (
        f"### CYCLE {observation.tick}\n\n"
        f"### REAL NEWS EVENTS THIS CYCLE\n{events_block}\n\n"
        f"### ACTOR ROSTER\n{json.dumps(roster, indent=2)}\n\n"
        f"### VALID target_country VALUES\n"
        + "\n".join(f"- {name}" for name in valid_targets)
        + '\nor exactly "None".\n\n'
        f"### TASK\n"
        f"Return a decisions array containing exactly {len(agents)} objects, one for each "
        f"agent_id in the roster above."
    )


def _index_by_agent(agents: list[NationAgent]) -> dict[str, NationAgent]:
    """Resolve incoming ids/names to agents, tolerantly.

    The model is told to echo agent_id, but a batched response occasionally
    returns a display name instead. Accepting both here is cheaper than
    discarding a whole cycle of otherwise-valid reasoning.
    """
    index: dict[str, NationAgent] = {}
    for agent in agents:
        index[agent.agent_id.lower()] = agent
        index[agent.name.lower()] = agent
    return index


def _inert_decision(reason: str) -> dict[str, Any]:
    return {
        "action_type": "ignore",
        "target_country": "None",
        "reasoning": reason,
        "relation_delta": 0.0,
    }


def parse_batch_response(
    raw_response: str,
    agents: list[NationAgent],
) -> dict[str, dict[str, Any]]:
    """Map a raw batch response to {agent_id: decision}.

    Missing or unmatched actors are simply absent from the result; the caller
    decides whether to fall back for them.
    """
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        log.error("Batched deliberation returned unparseable JSON: %s", exc)
        return {}

    if isinstance(payload, list):
        # Tolerate a bare array in place of {"decisions": [...]}.
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("decisions", [])
    else:
        rows = []

    if not isinstance(rows, list):
        log.error("Batched deliberation 'decisions' was not an array.")
        return {}

    index = _index_by_agent(agents)
    out: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("agent_id", "")).strip().lower()
        agent = index.get(key)
        if agent is None:
            log.warning("Batched decision referenced unknown actor %r; dropping.", row.get("agent_id"))
            continue
        if agent.agent_id in out:
            continue  # first response for an actor wins

        action_type = str(row.get("action_type", "ignore"))
        if action_type not in ACTION_TYPES:
            action_type = "ignore"

        try:
            delta = float(row.get("relation_delta", 0.0))
        except (TypeError, ValueError):
            delta = 0.0
        delta = max(-20.0, min(20.0, delta))

        target = str(row.get("target_country", "None")).strip() or "None"
        # An actor targeting itself is a modelling error, not a diplomatic act.
        if target.lower() == agent.name.lower():
            target, delta = "None", 0.0

        out[agent.agent_id] = {
            "action_type": action_type,
            "target_country": target,
            "reasoning": str(row.get("reasoning", "")).strip(),
            "relation_delta": delta,
        }

    return out


def deliberate(
    agents: list[NationAgent],
    observation: Observation,
    *,
    allow_single_agent_fallback: bool = True,
    temperature: float = 0.4,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Decide for every actor in one call. Returns ({agent_id: decision}, mode).

    `mode` is "batch" when the single call covered everyone, "batch+fallback"
    when some actors needed an individual call, or "fallback" when the batch
    produced nothing usable.
    """
    if not agents:
        return {}, "batch"

    raw = gemini.generate(
        prompt=_build_user_prompt(agents, observation),
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=BATCH_DECIDE_SCHEMA,
        temperature=temperature,
    )

    decisions = parse_batch_response(raw, agents)
    missing = [a for a in agents if a.agent_id not in decisions]

    if not missing:
        log.info("Batched deliberation covered all %d actors in one call.", len(agents))
        return decisions, "batch"

    log.warning(
        "Batched deliberation missed %d/%d actors: %s",
        len(missing),
        len(agents),
        ", ".join(a.name for a in missing),
    )

    if not allow_single_agent_fallback:
        for agent in missing:
            decisions[agent.agent_id] = _inert_decision(
                "No decision returned for this actor in the batched response."
            )
        return decisions, "batch" if len(missing) < len(agents) else "fallback"

    # Only the stragglers pay for an individual call, so a partially malformed
    # response costs a few requests rather than the whole tick.
    for agent in missing:
        try:
            raw_single = gemini.generate(
                prompt=agent._build_decide_user_prompt(observation),
                system_instruction=agent._build_decide_system_prompt(),
                response_mime_type="application/json",
                response_schema=DECIDE_SCHEMA,
                temperature=0.3,
            )
            decisions[agent.agent_id] = agent.parse_decision(raw_single)
        except Exception as exc:
            log.error("Per-actor fallback failed for %s: %s", agent.name, exc)
            decisions[agent.agent_id] = _inert_decision(f"Fallback decision failed: {exc}")

    mode = "fallback" if len(missing) == len(agents) else "batch+fallback"
    return decisions, mode
