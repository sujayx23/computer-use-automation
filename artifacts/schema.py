"""
Capability artifact schema.

This is the contract between:
  - the discovery run (LLM) that produces it,
  - a human reviewer who has to sign off on it,
  - the deterministic replay engine that executes it with no LLM involved,
  - and the AI agent that invokes it as a callable capability in production.

Design goals (see REPORT.md section 2 for the full rationale):
  1. Steps are decoupled from the raw model transcript -- an artifact contains
     only what's needed to replay, not chain-of-thought or prompts.
  2. Every locator is a *ranked list of strategies*, not a single selector --
     replay tries them in order and records which one worked. This is what
     lets the same artifact degrade gracefully instead of hard-failing the
     moment one attribute changes.
  3. Steps declare a risk level. Replay treats "irreversible" steps
     differently (see replay/executor.py + guardrails).
  4. Outcomes are typed as success / business_outcome / failure -- see
     replay/results.py. The schema itself stays focused on *how to act and
     verify*, the taxonomy is applied at replay time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"
    ASSERT_TEXT = "assert_text"


class RiskLevel(str, Enum):
    SAFE = "safe"              # read-only / trivially reversible (navigate, read)
    REVERSIBLE = "reversible"  # changes state but can be undone/redone safely
    IRREVERSIBLE = "irreversible"  # e.g. submitting a ledger-affecting action


class LocatorStrategyKind(str, Enum):
    ROLE = "role"              # accessibility role + accessible name (preferred)
    CSS_NAME_ATTR = "css_name_attr"  # form "name" attribute -- common even in
                                      # legacy apps, since it's required for
                                      # submission, unlike test ids
    TEXT = "text"               # visible text match
    ROW_LABEL = "row_label"     # label/value table row -- "find the row
                                 # containing this label, read the last cell".
                                 # common pattern in legacy label:value tables
    CSS_SELECTOR = "css_selector"  # last resort, most brittle


class LocatorStrategy(BaseModel):
    """One way to find an element. Steps carry a ranked list of these."""
    kind: LocatorStrategyKind
    value: str
    # for ROLE kind: the ARIA/accessibility role (e.g. "textbox", "button")
    role: Optional[str] = None
    # human-readable note on why this strategy was chosen / how robust it is
    rationale: Optional[str] = None


class Step(BaseModel):
    step_id: str
    action: ActionType
    description: str = Field(..., description="Human-readable summary for reviewers")
    locators: list[LocatorStrategy] = Field(
        default_factory=list,
        description="Ranked fallback chain, tried in order at replay time. "
                     "Empty for actions like NAVIGATE/WAIT_FOR that don't target an element.",
    )
    # For FILL/SELECT: value to enter. May reference an input parameter via
    # "{{param_name}}" templating, resolved at replay time.
    value_template: Optional[str] = None
    # For NAVIGATE
    url_template: Optional[str] = None
    # For EXTRACT: where the captured value should land in the output payload
    extract_key: Optional[str] = None
    # For ASSERT_TEXT / EXTRACT: how to pull text out of the located element
    text_source: Literal["inner_text", "value", "url"] = "inner_text"
    risk: RiskLevel = RiskLevel.SAFE
    # if true, replay must obtain explicit confirmation-gate clearance
    # (see guardrails.py) before executing this step
    requires_confirmation: bool = False
    timeout_ms: int = 8000
    # known recoverable conditions this step might hit, and how to react.
    # kept intentionally simple: a text pattern to look for on the page,
    # and what to do if seen.
    recoverable_on: list["RecoverableCondition"] = Field(default_factory=list)


class RecoverableCondition(BaseModel):
    name: str  # e.g. "session_timeout", "transient_slow_load"
    match_text: str  # substring/pattern searched for in page text
    strategy: Literal["retry", "reload", "wait_and_retry"] = "retry"
    max_attempts: int = 2


class InputParam(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"]
    required: bool = True
    description: str = ""
    sensitive: bool = False  # if true: never logged/persisted in the clear


class OutputField(BaseModel):
    name: str
    type: Literal["string", "number", "boolean"]
    description: str = ""


class Checkpoint(BaseModel):
    """Asserts the flow actually reached the expected end state."""
    description: str
    locators: list[LocatorStrategy]
    text_source: Literal["inner_text", "value", "url"] = "inner_text"
    expected_text_contains: Optional[str] = None
    expected_url_contains: Optional[str] = None


class BusinessOutcome(BaseModel):
    """A named, expected non-success result the caller needs to know about
    (e.g. 'member not found') -- NOT a system failure."""
    name: str
    match_text: str
    description: str


class TargetApp(BaseModel):
    app_id: str
    base_url: str
    vendor_product: Optional[str] = Field(
        default=None,
        description="Underlying vendor product identifier, for cross-tenant "
                     "reuse -- see REPORT.md section 4.",
    )


class CapabilityArtifact(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    capability_id: str
    name: str
    description: str
    version: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: Literal["llm_discovery", "human_authored"] = "llm_discovery"
    review_status: Literal["draft", "approved"] = "draft"

    target: TargetApp
    goal_text: str = Field(..., description="Original natural-language goal used during discovery")

    inputs: list[InputParam] = Field(default_factory=list)
    outputs: list[OutputField] = Field(default_factory=list)

    steps: list[Step]
    checkpoint: Checkpoint
    known_business_outcomes: list[BusinessOutcome] = Field(default_factory=list)

    # safety
    allowed_domains: list[str] = Field(default_factory=list)
    max_risk_level: RiskLevel = RiskLevel.SAFE

    model_config = ConfigDict(use_enum_values=True)
