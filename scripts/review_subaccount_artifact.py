#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artifacts.schema import CapabilityArtifact, BusinessOutcome


def main():
    if len(sys.argv) != 2:
        print("usage: python3 scripts/review_subaccount_artifact.py <artifact.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    artifact = CapabilityArtifact.model_validate_json(path.read_text())

    artifact.checkpoint.expected_text_contains = "Sub-Account Opened Successfully"

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

    artifact.review_status = "approved"
    artifact.version += 1

    path.write_text(artifact.model_dump_json(indent=2))
    print(f"Reviewed and corrected: {path}")
    print(f"  checkpoint corrected: expected_text_contains -> 'Sub-Account Opened Successfully'")
    print(f"  added {len(artifact.known_business_outcomes)} known business outcomes")
    print(f"  review_status -> approved, version -> {artifact.version}")


if __name__ == "__main__":
    main()