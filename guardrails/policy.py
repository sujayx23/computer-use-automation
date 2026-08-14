"""
Guardrail policy engine.

Two enforcement points, both used by discovery AND replay so the same rules
apply regardless of who's driving:

  1. check_navigation(url) -- the agent/replay may never navigate outside an
     explicit domain allowlist. This is a hard stop, not a warning.
  2. check_action(action_type, risk) -- action types are allowlisted per
     policy; RiskLevel.IRREVERSIBLE actions are blocked by default unless
     explicitly allowed with confirmation, per the policy config.

This is intentionally a small, explicit, file-based policy -- no inference,
no "the model decides what's safe." The policy is data, versioned alongside
the code, and reviewable independently of any run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from artifacts.schema import RiskLevel


class PolicyViolation(Exception):
    pass


@dataclass
class PolicyEngine:
    allowed_domains: list[str]
    allowed_action_types: list[str] = field(default_factory=lambda: [
        "navigate", "click", "fill", "select", "wait_for", "extract", "assert_text",
    ])
    # irreversible actions are never auto-executed; they require an explicit
    # confirmation flag to be set true by the caller (see replay/executor.py)
    block_irreversible_by_default: bool = True

    @classmethod
    def from_file(cls, path: Path) -> "PolicyEngine":
        data = json.loads(Path(path).read_text())
        return cls(**data)

    def check_navigation(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
            raise PolicyViolation(f"navigation to disallowed domain: {host} (allowed: {self.allowed_domains})")

    def check_action(self, action_type: str, risk: RiskLevel, confirmed: bool = False) -> None:
        if action_type not in self.allowed_action_types:
            raise PolicyViolation(f"action type not permitted by policy: {action_type}")
        if risk == RiskLevel.IRREVERSIBLE and self.block_irreversible_by_default and not confirmed:
            raise PolicyViolation(
                "irreversible action blocked by policy: requires explicit confirmation clearance"
            )

    def redact(self, value: str, sensitive: bool) -> str:
        if not sensitive:
            return value
        if len(value) <= 4:
            return "***"
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
