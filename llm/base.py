"""
Provider-agnostic LLM interface.

The discovery agent loop (agent/discovery.py) only knows this interface --
it doesn't know or care whether the underlying model is Claude or Gemini.
Tool specs are declared once, in plain JSON-schema-flavored dicts, and each
concrete client adapts them to its provider's tool-calling format.

This exists specifically because the assignment brief treats LLM provider
choice as an open decision (Section 4) -- and, practically, because
Anthropic API access on the machine this was developed on turned out to be
gated behind billing while Google's free tier wasn't. Being able to swap
providers without touching the agent loop is the point.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON-schema-ish: {"type": "object", "properties": {...}, "required": [...]}


@dataclass
class LLMTurn:
    tool_name: Optional[str]
    tool_input: Optional[dict]
    reasoning_text: str


class LLMClient(ABC):
    """One instance = one conversation. Call next_action() repeatedly."""

    @abstractmethod
    def __init__(self, model: str, system_prompt: str, tools: list[ToolSpec]):
        ...

    @abstractmethod
    def next_action(self, observation_text: str, prior_tool_result: Optional[str]) -> LLMTurn:
        """
        Send the current observation (and, after the first call, the result
        of the previously requested tool) to the model, and return the next
        action it wants to take.
        """
        ...
