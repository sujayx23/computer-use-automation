import pytest
from artifacts.schema import RiskLevel
from guardrails.policy import PolicyEngine, PolicyViolation


def make_policy():
    return PolicyEngine(allowed_domains=["127.0.0.1", "localhost"])


def test_navigation_allowed():
    make_policy().check_navigation("http://127.0.0.1:5055/member/12345")


def test_navigation_blocked():
    with pytest.raises(PolicyViolation):
        make_policy().check_navigation("http://evil.example.com/phish")


def test_action_type_not_permitted():
    p = PolicyEngine(allowed_domains=["127.0.0.1"], allowed_action_types=["navigate"])
    with pytest.raises(PolicyViolation):
        p.check_action("click", RiskLevel.SAFE)


def test_irreversible_blocked_by_default():
    p = make_policy()
    with pytest.raises(PolicyViolation):
        p.check_action("click", RiskLevel.IRREVERSIBLE)


def test_irreversible_allowed_when_confirmed():
    p = make_policy()
    p.check_action("click", RiskLevel.IRREVERSIBLE, confirmed=True)  # should not raise


def test_redact_masks_sensitive_values():
    p = make_policy()
    assert p.redact("123456789", sensitive=True) == "12*****89"
    assert p.redact("123456789", sensitive=False) == "123456789"


def test_redact_short_sensitive_value():
    p = make_policy()
    assert p.redact("12", sensitive=True) == "***"
