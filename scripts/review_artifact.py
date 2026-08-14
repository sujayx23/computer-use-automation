#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artifacts.schema import CapabilityArtifact, BusinessOutcome, RecoverableCondition


def main():
    if len(sys.argv) != 2:
        print("usage: python3 scripts/review_artifact.py <artifact.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    artifact = CapabilityArtifact.model_validate_json(path.read_text())

    artifact.checkpoint.expected_text_contains = "Savings Balance"

    artifact.known_business_outcomes = [
        BusinessOutcome(
            name="member_not_found",
            match_text="No member found",
            description="the given member id does not exist -- a legitimate answer, not an error",
        ),
        BusinessOutcome(
            name="permission_denied",
            match_text="Access Denied",
            description="the record is access-restricted for the caller's role",
        ),
    ]

    for step in artifact.steps:
        if step.extract_key == "savings_balance":
            step.recoverable_on = [
                RecoverableCondition(
                    name="session_timeout",
                    match_text="Session Expired",
                    strategy="reload",
                    max_attempts=2,
                )
            ]

    artifact.review_status = "approved"
    artifact.version += 1

    path.write_text(artifact.model_dump_json(indent=2))
    print(f"Reviewed and approved: {path}")
    print(f"  checkpoint fixed: expected_text_contains -> 'Savings Balance'")
    print(f"  added {len(artifact.known_business_outcomes)} known business outcomes")
    print(f"  added recoverable_on for session_timeout")
    print(f"  review_status -> approved, version -> {artifact.version}")


if __name__ == "__main__":
    main()