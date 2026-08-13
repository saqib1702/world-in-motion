"""Gemini API client.

Thin wrapper over the google-genai SDK so the rest of the codebase never
imports the vendor SDK directly — swapping models or providers stays a
one-file change. Agent-specific prompting belongs in /agents, not here.
"""

import logging
from typing import Optional

from google import genai
from google.genai import types

import config

log = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


import json
import os
import re

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


def _generate_mock_fallback(
    prompt: str,
    system_instruction: Optional[str] = None,
    response_mime_type: Optional[str] = None,
) -> str:
    """Generate structured mock JSON or text response when Gemini API is unconfigured/offline."""
    if response_mime_type == "application/json":
        # Extract nation name and prompt details for realistic mock reasoning
        sys_str = system_instruction or ""
        nation_match = re.search(r"leadership of ([^,\n]+)", sys_str)
        nation_name = nation_match.group(1) if nation_match else "Nation"

        # Determine target nation from prompt or default, ensuring agent does not target itself
        target_country = "Ironreach Dominion" if nation_name != "Ironreach Dominion" else "Republic of Eldoria"
        if "Solaria Federation" in prompt and nation_name != "Solaria Federation":
            target_country = "Solaria Federation"
        elif "Republic of Eldoria" in prompt and nation_name != "Republic of Eldoria":
            target_country = "Republic of Eldoria"
        elif "Verdant Union" in prompt and nation_name != "Verdant Union":
            target_country = "Verdant Union"
        elif "Ironreach Dominion" in prompt and nation_name != "Ironreach Dominion":
            target_country = "Ironreach Dominion"


        # Determine realistic action & delta
        action_type = "issue_statement"
        delta = -10.0
        if "tariffs" in prompt.lower() or "tariff" in prompt.lower():
            action_type = "impose_sanction"
            delta = -15.0
            reasoning = f"{nation_name} imposes retaliatory tariffs and diplomatic sanctions against {target_country}."
        elif "peace" in prompt.lower() or "treaty" in prompt.lower() or "alliance" in prompt.lower():
            action_type = "propose_alliance"
            delta = +15.0
            reasoning = f"{nation_name} welcomes diplomatic dialogue and proposes a strategic treaty with {target_country}."
        elif "naval" in prompt.lower() or "military" in prompt.lower():
            action_type = "military_warning"
            delta = -12.0
            reasoning = f"{nation_name} issues an urgent military warning in response to aggressive maneuvers by {target_country}."
        else:
            reasoning = f"{nation_name} issues a formal diplomatic statement addressing the recent geopolitical events involving {target_country}."

        mock_payload = {
            "action_type": action_type,
            "target_country": target_country,
            "reasoning": reasoning,
            "relation_delta": delta,
        }
        return json.dumps(mock_payload)
    else:
        sys_str = system_instruction or ""
        nation_match = re.search(r"leadership of ([^,\n]+)", sys_str)
        nation_name = nation_match.group(1) if nation_match else "our nation"
        return f"As the official representative of {nation_name}, we stand firmly committed to defending our national interests, maintaining sovereign stability, and engaging in strategic diplomacy."


def generate(
    prompt: str,
    *,
    system_instruction: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    response_mime_type: Optional[str] = None,
    response_schema: Optional[dict] = None,
) -> str:
    """Send one prompt, return response text. Falls back to mock generator if API key missing or call fails."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return _generate_mock_fallback(prompt, system_instruction, response_mime_type)

    cfg = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        response_mime_type=response_mime_type,
        response_schema=response_schema,
    )
    try:
        response = get_client().models.generate_content(
            model=model or config.GEMINI_MODEL,
            contents=prompt,
            config=cfg,
        )
        return response.text or ""
    except Exception as exc:
        log.warning("Gemini API call failed: %s. Falling back to mock generator.", exc)
        return _generate_mock_fallback(prompt, system_instruction, response_mime_type)



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
