"""
Replay result contract.

Three, and only three, outcome shapes -- deliberately not a generic
success/failure boolean, because collapsing "no such member" and "the app
threw a 500" into the same bucket is exactly the mistake the brief calls out
as the most common design error here.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel


class ReplayResult(BaseModel):
    status: Literal["success", "business_outcome", "failure", "escalated"]
    capability_id: str
    run_id: str

    # SUCCESS: declared outputs, populated per the artifact's `outputs` schema
    outputs: dict = {}

    # BUSINESS_OUTCOME: a named, expected non-success result (e.g. "member_not_found").
    # The caller should treat this as a legitimate answer, not an error to retry.
    outcome_name: Optional[str] = None
    outcome_detail: Optional[str] = None

    # FAILURE: enough to debug without needing to reproduce.
    failed_step_id: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None
    error_detail: Optional[str] = None

    # ESCALATED: handed to a human; see escalation/handoff.py
    escalation_id: Optional[str] = None

    # always present
    evidence_dir: Optional[str] = None
    recovered_conditions: list[str] = []
