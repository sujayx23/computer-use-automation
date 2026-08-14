#!/usr/bin/env python3
"""
Run a real, LLM-driven discovery session against the live target app and
save the resulting capability artifact.

Usage:
  python3 run_discovery.py --goal "look up member 12345 and read their current savings balance" \\
      --capability-name lookup_member_balance --member-id 12345

Requires GEMINI_API_KEY to be set in the environment (free tier via
https://aistudio.google.com/apikey -- no credit card required).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from agent.discovery import run_discovery, _system_prompt, TOOLS
from guardrails.policy import PolicyEngine
from llm.gemini_client import GeminiClient

BASE_URL = "http://127.0.0.1:5055/"
TARGET_APP_ID = "meridian_servicing_console"
MODEL = "gemini-flash-lite-latest"


def build_llm_client(goal: str, base_url: str, allowed_domains: list[str], input_params: dict):
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: set GEMINI_API_KEY in your environment first.", file=sys.stderr)
        sys.exit(1)
    system_prompt = _system_prompt(goal, base_url, allowed_domains, input_params)
    return GeminiClient(model=MODEL, system_prompt=system_prompt, tools=TOOLS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--capability-name", required=True, help="filename to save the artifact under")
    ap.add_argument("--member-id", default=None, help="member id to use as an input parameter, if relevant")
    ap.add_argument("--param", action="append", default=[],
                     help="additional key=value input parameter, repeatable "
                          "(e.g. --param nickname='Vacation Fund' --param deposit=100)")
    args = ap.parse_args()

    input_params = {}
    if args.member_id:
        input_params["member_id"] = args.member_id
    for p in args.param:
        k, _, v = p.partition("=")
        input_params[k] = v

    policy = PolicyEngine.from_file(Path("guardrails/policy.json"))
    evidence_dir = Path("evidence") / "discovery" / args.capability_name

    llm_client = build_llm_client(args.goal, BASE_URL, policy.allowed_domains, input_params)

    artifact, run_log = run_discovery(
        goal=args.goal,
        target_app_id=TARGET_APP_ID,
        base_url=BASE_URL,
        allowed_domains=policy.allowed_domains,
        input_params=input_params,
        evidence_dir=evidence_dir,
        policy=policy,
        llm_client=llm_client,
    )

    (evidence_dir / "discovery_log.json").write_text(json.dumps(run_log, indent=2, default=str))

    if artifact is None:
        print(f"Discovery did NOT complete successfully. Outcome: {run_log.get('outcome')}")
        print(f"Full log written to {evidence_dir / 'discovery_log.json'}")
        sys.exit(2)

    out_path = Path("artifacts") / "saved" / f"{args.capability_name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(artifact.model_dump_json(indent=2))

    print(f"Discovery succeeded.")
    print(f"Artifact saved to: {out_path}")
    print(f"Evidence saved to: {evidence_dir}")
    print(json.dumps(json.loads(artifact.model_dump_json()), indent=2))


if __name__ == "__main__":
    main()