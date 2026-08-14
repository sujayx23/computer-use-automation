"""
Escalation & handoff.

Scope note (matches REPORT.md section 5 / the assignment's own scope note):
a full real-time co-browsing operator console is out of scope. What's real
here is the mechanism: automation detects it can't safely proceed, raises an
intervention request carrying enough context to act on, control transfers to
an operator who acts on the SAME live Playwright session/page (not a fresh
one), and control is handed back so the run can resume. The "operator" in
this repo is a scripted stand-in for a human clicking through a console --
that part is mocked and documented as such. The handoff mechanism and
control-transfer model (pause / intervention record / same-session takeover
/ resume, all logged) are real.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

INTERVENTIONS_LOG = Path("evidence") / "interventions.jsonl"

def raise_intervention_request(
    capability_id: str,
    step_id: str,
    reason: str,
    page,
    evidence_dir: Path,
) -> str:
    """Detect+route: records everything a human operator needs to act --
    which capability/step, why it stopped, and a screenshot of the live
    session at the moment of the pause."""
    intervention_id = str(uuid.uuid4())
    screenshot_path = evidence_dir / f"intervention_{intervention_id[:8]}.png"
    try:
        page.screenshot(path=str(screenshot_path))
    except Exception:
        screenshot_path = None

    record = {
        "intervention_id": intervention_id,
        "capability_id": capability_id,
        "step_id": step_id,
        "reason": reason,
        "current_url": getattr(page, "url", None),
        "screenshot": str(screenshot_path) if screenshot_path else None,
        "raised_at": datetime.now(timezone.utc).isoformat(),
        "control": "human",
    }
    INTERVENTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(INTERVENTIONS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    (evidence_dir / f"intervention_{intervention_id[:8]}.json").write_text(
        json.dumps(record, indent=2)
    )
    return intervention_id


def mock_operator_approve_and_perform(page, artifact, step, evidence_dir: Path) -> bool:
    """Stand-in for a human operator taking control of the live session.

    In a real deployment this is a console where a person sees the same
    screenshot/context captured above, reviews it, and manually performs the
    step (or something equivalent) themselves in the live session. Here we
    simulate the *decision + action* deterministically: the operator reviews
    the pending irreversible action and, if approved, performs the exact
    step that was blocked -- but this happens on the SAME `page` object the
    automation was driving, which is the property that matters (state,
    cookies, and session are preserved across the handoff, not reset).
    """
    from core.locate import resolve_locator

    res = resolve_locator(page, step.locators)
    if res.locator is None:
        return False
    try:
        res.locator.click()
    except Exception:
        return False

    (evidence_dir / "human_action.json").write_text(json.dumps({
        "performed_by": "human_operator (mocked)",
        "step_id": step.step_id,
        "action": "click",
        "note": "manually approved and performed the irreversible step that automation was blocked from taking",
        "at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    try:
        page.screenshot(path=str(evidence_dir / "human_action.png"))
    except Exception:
        pass
    return True
