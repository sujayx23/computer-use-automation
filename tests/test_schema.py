from artifacts.schema import (
    CapabilityArtifact, TargetApp, InputParam, OutputField, Step, ActionType,
    LocatorStrategy, LocatorStrategyKind, Checkpoint, RiskLevel,
)


def _minimal_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id="test.cap.1",
        name="test capability",
        description="a test capability",
        target=TargetApp(app_id="test_app", base_url="http://127.0.0.1:5055/"),
        goal_text="do the thing",
        inputs=[InputParam(name="member_id", type="string")],
        outputs=[OutputField(name="balance", type="string")],
        steps=[
            Step(step_id="s0", action=ActionType.FILL, description="fill",
                 locators=[LocatorStrategy(kind=LocatorStrategyKind.CSS_NAME_ATTR, value="member_id")],
                 value_template="{{member_id}}"),
        ],
        checkpoint=Checkpoint(description="done", locators=[], expected_text_contains="Balance"),
        allowed_domains=["127.0.0.1"],
    )


def test_artifact_round_trips_through_json():
    artifact = _minimal_artifact()
    dumped = artifact.model_dump_json()
    restored = CapabilityArtifact.model_validate_json(dumped)
    assert restored.capability_id == artifact.capability_id
    assert restored.steps[0].value_template == "{{member_id}}"


def test_default_risk_is_safe():
    artifact = _minimal_artifact()
    assert artifact.steps[0].risk == RiskLevel.SAFE or artifact.steps[0].risk == "safe"


def test_draft_review_status_default():
    artifact = _minimal_artifact()
    assert artifact.review_status == "draft"
