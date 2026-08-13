"""Agent personas.

One module per agent type (e.g. state_actor.py, market.py, media.py), each
subclassing Agent from base.py. Reasoning goes through /llm — agents should
not import a vendor SDK directly.

No agent logic implemented yet.
"""

from agents.base import Agent, Observation
from agents.nation import NationAgent

__all__ = ["Agent", "Observation", "NationAgent"]

