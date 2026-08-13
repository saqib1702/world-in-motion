"""Shared agent interface.

Establishes the contract every agent type implements. Intentionally minimal:
the persona logic, prompt construction, and decision schemas come later.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """What an agent can see on a given tick."""

    tick: int
    world_state: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Agent(ABC):
    """Base class for all agent types.

    Subclasses live in sibling modules, one per agent type, and set
    `agent_type` to a stable string used as the discriminator in the `agents`
    collection.
    """

    agent_id: str
    name: str
    agent_type: str = "base"
    persona: dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    def decide(self, observation: Observation) -> dict[str, Any]:
        """Choose actions for this tick.

        Returns a serializable dict destined for the `actions` collection —
        the chosen action plus the reasoning that produced it.
        """
        raise NotImplementedError
