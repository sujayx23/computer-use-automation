#!/usr/bin/env python3
"""
Replay a saved capability artifact deterministically -- no LLM involved.

Usage:
  python3 run_replay.py --artifact artifacts/saved/lookup_member_balance.json --param member_id=12345
  python3 run_replay.py --artifact artifacts/saved/lookup_member_balance.json --param member_id=00000
  python3 run_replay.py --artifact artifacts/saved/open_subaccount.json --param member_id=12345 \\
      --param nickname="Vacation Fund" --param account_type=holiday --param deposit=100 --allow-irreversible
"""
import argparse
import json
from pathlib import Path

from artifacts.schema import CapabilityArtifact
from guardrails.policy import PolicyEngine
from replay.executor import ReplayExecutor
from escalation.handoff import mock_operator_approve_and_perform


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--param", action="append", default=[], help="key=value, repeatable")
    ap.add_argument("--allow-irreversible", action="store_true",
                     help="pre-authorize irreversible steps (skips human escalation)")
    ap.add_argument("--with-operator", action="store_true",
                     help="attach the mock human operator so escalations for irreversible "
                          "steps get resolved on the same live session instead of stopping")
    args = ap.parse_args()

    input_values = {}
    for p in args.param:
        k, _, v = p.partition("=")
        input_values[k] = v

    artifact = CapabilityArtifact.model_validate_json(Path(args.artifact).read_text())
    policy = PolicyEngine.from_file(Path("guardrails/policy.json"))

    operator_cb = mock_operator_approve_and_perform if args.with_operator else None
    executor = ReplayExecutor(policy=policy, evidence_dir=Path("evidence") / "replay",
                               operator_callback=operator_cb)

    result = executor.run(artifact, input_values, allow_irreversible=args.allow_irreversible)

    print(json.dumps(json.loads(result.model_dump_json()), indent=2))


if __name__ == "__main__":
    main()
