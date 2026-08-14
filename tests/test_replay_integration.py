"""
Integration tests -- require the target app running locally:
    python3 target_app/app.py
Skipped automatically if it's not reachable.
"""
import socket
from pathlib import Path

import pytest

from artifacts.schema import (
    CapabilityArtifact, TargetApp, InputParam, OutputField, Step, ActionType,
    LocatorStrategy, LocatorStrategyKind, Checkpoint, RiskLevel,
    RecoverableCondition, BusinessOutcome,
)
from guardrails.policy import PolicyEngine
from replay.executor import ReplayExecutor


def _app_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5055), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _app_reachable(), reason="target app not running on :5055")


def _balance_lookup_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id="meridian.lookup_balance.test",
        name="Look up member and read savings balance",
        description="integration test artifact",
        target=TargetApp(app_id="meridian", base_url="http://127.0.0.1:5055/"),
        goal_text="look up member and read savings balance",
        inputs=[InputParam(name="member_id", type="string", required=True, sensitive=True)],
        outputs=[OutputField(name="savings_balance", type="string")],
        steps=[
            Step(step_id="s0", action=ActionType.FILL, description="fill member id",
                 locators=[LocatorStrategy(kind=LocatorStrategyKind.CSS_NAME_ATTR, value="member_id")],
                 value_template="{{member_id}}"),
            Step(step_id="s1", action=ActionType.CLICK, description="click search",
                 locators=[LocatorStrategy(kind=LocatorStrategyKind.ROLE, role="button", value="Search")]),
            Step(step_id="s2", action=ActionType.EXTRACT, description="extract savings balance",
                 locators=[LocatorStrategy(kind=LocatorStrategyKind.ROW_LABEL, value="Savings Balance")],
                 extract_key="savings_balance",
                 recoverable_on=[RecoverableCondition(name="session_timeout", match_text="Session Expired",
                                                       strategy="reload", max_attempts=2)]),
        ],
        checkpoint=Checkpoint(description="savings balance visible", locators=[],
                               expected_text_contains="Savings Balance"),
        known_business_outcomes=[
            BusinessOutcome(name="member_not_found", match_text="No member found", description="no such member"),
            BusinessOutcome(name="permission_denied", match_text="Access Denied", description="restricted record"),
        ],
        allowed_domains=["127.0.0.1"],
    )


@pytest.fixture
def executor(tmp_path):
    policy = PolicyEngine.from_file(Path("guardrails/policy.json"))
    return ReplayExecutor(policy=policy, evidence_dir=tmp_path)


def test_happy_path_extracts_balance(executor):
    result = executor.run(_balance_lookup_artifact(), {"member_id": "12345"})
    assert result.status == "success"
    assert "15230.10" in result.outputs["savings_balance"]


def test_member_not_found_is_business_outcome_not_failure(executor):
    result = executor.run(_balance_lookup_artifact(), {"member_id": "00000"})
    assert result.status == "business_outcome"
    assert result.outcome_name == "member_not_found"


def test_permission_denied_is_business_outcome(executor):
    result = executor.run(_balance_lookup_artifact(), {"member_id": "99999"})
    assert result.status == "business_outcome"
    assert result.outcome_name == "permission_denied"


def test_missing_required_input_fails_cleanly(executor):
    result = executor.run(_balance_lookup_artifact(), {})
    assert result.status == "failure"
    assert "member_id" in result.error_detail
