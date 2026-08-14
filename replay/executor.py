"""
Deterministic replay engine.

Given a saved CapabilityArtifact and a set of input parameters, this
executes the recorded steps with NO LLM involved -- pure locator resolution
(core/locate.py, the exact same code discovery used) plus explicit checks
for the runtime conditions the brief calls out: validation errors, not-found
results, permission denials, session timeouts, transient slowness, and
outright app errors.

Outcome classification order at each failure point:
  1. Is the current page a known, named business outcome (declared on the
     artifact)? -> status=business_outcome. Not a bug, a legitimate answer.
  2. Does the page match a step's declared recoverable_on condition? ->
     attempt the declared recovery (retry/reload/wait_and_retry), bounded by
     max_attempts, then re-resolve.
  3. Otherwise -> status=failure, with the specific step, what was expected,
     and what was actually observed, captured as evidence.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from artifacts.schema import CapabilityArtifact, ActionType, RiskLevel
from core.locate import resolve_locator
from guardrails.policy import PolicyEngine, PolicyViolation
from replay.results import ReplayResult


class ReplayExecutor:
    def __init__(self, policy: PolicyEngine, evidence_dir: Path, headless: bool = True,
                 operator_callback=None):
        self.policy = policy
        self.evidence_dir = evidence_dir
        self.headless = headless
        # called as operator_callback(page, artifact, step, evidence_dir) -> bool.
        # if it returns True, replay resumes on the same session; if None or
        # it returns False, replay stops with status="escalated".
        self.operator_callback = operator_callback

    def run(
        self,
        artifact: CapabilityArtifact,
        input_values: dict,
        allow_irreversible: bool = False,
    ) -> ReplayResult:
        run_id = str(uuid.uuid4())
        run_evidence_dir = self.evidence_dir / f"replay_{run_id[:8]}"
        run_evidence_dir.mkdir(parents=True, exist_ok=True)

        structured_log: list[dict] = []
        outputs: dict = {}
        all_recovered: list[str] = []

        def log(event: dict):
            event["ts"] = datetime.now(timezone.utc).isoformat()
            structured_log.append(event)

        # 1. validate inputs against the artifact's declared contract
        for p in artifact.inputs:
            if p.required and p.name not in input_values:
                result = ReplayResult(
                    status="failure", capability_id=artifact.capability_id, run_id=run_id,
                    error_detail=f"missing required input parameter: {p.name}",
                    evidence_dir=str(run_evidence_dir),
                )
                self._flush_log(run_evidence_dir, structured_log)
                return result

        redacted_inputs = {
            p.name: self.policy.redact(str(input_values.get(p.name, "")), p.sensitive)
            for p in artifact.inputs
        }
        log({"event": "replay_start", "capability_id": artifact.capability_id,
             "inputs_redacted": redacted_inputs})

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            page = browser.new_page()

            try:
                self.policy.check_navigation(artifact.target.base_url)
            except PolicyViolation as e:
                log({"event": "policy_violation", "detail": str(e)})
                browser.close()
                return self._fail(artifact, run_id, run_evidence_dir, structured_log,
                                   step_id="navigate", expected="allowed domain",
                                   observed=str(e))

            page.goto(artifact.target.base_url)

            for step in artifact.steps:
                step_result = self._execute_step(
                    page, artifact, step, input_values, log, run_evidence_dir, allow_irreversible
                )
                if step_result is not None:  # non-None means we're terminating early
                    self._flush_log(run_evidence_dir, structured_log)
                    browser.close()
                    step_result.evidence_dir = str(run_evidence_dir)
                    return step_result
                if step.action == ActionType.EXTRACT and step.extract_key:
                    outputs[step.extract_key] = self._last_extracted_value
                all_recovered.extend(self._last_recovered_conditions)

            # 2. verify checkpoint
            checkpoint_ok, checkpoint_detail = self._verify_checkpoint(page, artifact)
            log({"event": "checkpoint_check", "ok": checkpoint_ok, "detail": checkpoint_detail})

            page.screenshot(path=str(run_evidence_dir / "final_state.png"))

            if not checkpoint_ok:
                self._flush_log(run_evidence_dir, structured_log)
                browser.close()
                return ReplayResult(
                    status="failure", capability_id=artifact.capability_id, run_id=run_id,
                    failed_step_id="checkpoint",
                    expected=artifact.checkpoint.description,
                    observed=checkpoint_detail,
                    evidence_dir=str(run_evidence_dir),
                )

            browser.close()

        log({"event": "replay_success", "outputs": outputs})
        self._flush_log(run_evidence_dir, structured_log)
        return ReplayResult(
            status="success", capability_id=artifact.capability_id, run_id=run_id,
            outputs=outputs, evidence_dir=str(run_evidence_dir), recovered_conditions=all_recovered,
        )

    # ------------------------------------------------------------------

    def _execute_step(self, page, artifact, step, input_values, log, evidence_dir, allow_irreversible):
        recovered: list[str] = []
        self._last_recovered_conditions = recovered
        attempts_left = 1
        for cond in step.recoverable_on:
            attempts_left = max(attempts_left, cond.max_attempts)

        for attempt in range(attempts_left):
            page_text = self._safe_text(page)

            # business outcome check happens BEFORE we try to act further --
            # if we've already landed on a known outcome page, don't fight it
            biz = self._match_business_outcome(page_text, artifact)
            if biz:
                log({"event": "business_outcome", "step_id": step.step_id, "outcome": biz.name})
                return ReplayResult(
                    status="business_outcome", capability_id=artifact.capability_id,
                    run_id="", outcome_name=biz.name, outcome_detail=biz.description,
                )

            # recoverable condition check
            recovered_this_round = False
            for cond in step.recoverable_on:
                if cond.match_text in page_text:
                    log({"event": "recoverable_condition_detected", "step_id": step.step_id,
                         "condition": cond.name, "strategy": cond.strategy, "attempt": attempt})
                    recovered.append(cond.name)
                    if cond.strategy == "reload":
                        page.reload()
                    elif cond.strategy == "wait_and_retry":
                        page.wait_for_timeout(1500)
                        page.reload()
                    else:
                        page.wait_for_timeout(500)
                    recovered_this_round = True
                    break
            if recovered_this_round:
                continue  # re-observe and retry this same step

            try:
                risk = RiskLevel(step.risk) if not isinstance(step.risk, RiskLevel) else step.risk
            except ValueError:
                risk = RiskLevel.SAFE

            if step.requires_confirmation or risk == RiskLevel.IRREVERSIBLE:
                try:
                    self.policy.check_action(step.action.value if hasattr(step.action, "value") else step.action,
                                              risk, confirmed=allow_irreversible)
                except PolicyViolation as e:
                    log({"event": "escalation_required", "step_id": step.step_id, "reason": str(e)})
                    esc_id = self._raise_intervention(artifact, step, page, evidence_dir, reason=str(e))
                    log({"event": "control_transfer", "control": "human", "escalation_id": esc_id})

                    resolved = False
                    if self.operator_callback:
                        resolved = self.operator_callback(page, artifact, step, evidence_dir)
                    log({"event": "operator_action", "escalation_id": esc_id, "resolved": resolved})

                    if resolved:
                        log({"event": "control_transfer", "control": "automation",
                             "escalation_id": esc_id, "note": "resumed on same live session"})
                        return None  # human performed the step; move on to the next one
                    return ReplayResult(
                        status="escalated", capability_id=artifact.capability_id, run_id="",
                        escalation_id=esc_id,
                    )

            ok, detail = self._act(page, step, input_values)
            log({"event": "step_executed", "step_id": step.step_id, "action": str(step.action),
                 "ok": ok, "detail": detail})

            if ok:
                return None  # success, continue to next step

            # resolution failed this attempt -- loop will retry if a
            # recoverable condition matched next time around; otherwise fall
            # through to hard failure below
            if attempt == attempts_left - 1:
                page.screenshot(path=str(evidence_dir / f"failure_{step.step_id}.png"))
                page_text_snapshot = self._safe_text(page)[:800]
                biz = self._match_business_outcome(page_text_snapshot, artifact)
                if biz:
                    return ReplayResult(
                        status="business_outcome", capability_id=artifact.capability_id, run_id="",
                        outcome_name=biz.name, outcome_detail=biz.description,
                    )
                return ReplayResult(
                    status="failure", capability_id=artifact.capability_id, run_id="",
                    failed_step_id=step.step_id, expected=step.description,
                    observed=detail, recovered_conditions=recovered,
                )

        return None

    def _act(self, page, step, input_values) -> tuple[bool, str]:
        action = step.action
        try:
            if action == ActionType.NAVIGATE:
                url = self._resolve_template(step.url_template, input_values)
                page.goto(url)
                return True, f"navigated to {url}"

            res = resolve_locator(page, step.locators)
            if res.locator is None:
                return False, f"no locator resolved: {res.attempts}"

            if action == ActionType.CLICK:
                res.locator.click()
                return True, "clicked"
            if action == ActionType.FILL:
                value = self._resolve_template(step.value_template, input_values)
                res.locator.fill(value)
                return True, f"filled '{value}'"
            if action == ActionType.SELECT:
                value = self._resolve_template(step.value_template, input_values)
                res.locator.select_option(value)
                return True, f"selected '{value}'"
            if action == ActionType.EXTRACT:
                text = res.locator.inner_text()
                self._last_extracted_value = text
                return True, f"extracted '{text}'"
            if action == ActionType.ASSERT_TEXT:
                text = res.locator.inner_text()
                return True, f"observed '{text}'"
            if action == ActionType.WAIT_FOR:
                page.wait_for_timeout(step.timeout_ms)
                return True, "waited"

            return False, f"unhandled action type: {action}"
        except Exception as e:  # noqa: BLE001
            return False, f"exception: {e}"

    def _verify_checkpoint(self, page, artifact) -> tuple[bool, str]:
        cp = artifact.checkpoint
        page_text = self._safe_text(page)

        if cp.locators:
            res = resolve_locator(page, cp.locators)
            if res.locator is None:
                return False, f"checkpoint element not found: {res.attempts}"
            text = res.locator.inner_text()
            if cp.expected_text_contains and cp.expected_text_contains not in text:
                return False, f"checkpoint element text was '{text}'"
            return True, f"checkpoint element text: '{text}'"

        if cp.expected_text_contains:
            if cp.expected_text_contains in page_text:
                return True, "expected text found on page"
            return False, f"expected text '{cp.expected_text_contains}' not found; page had: {page_text[:200]}"

        if cp.expected_url_contains:
            if cp.expected_url_contains in page.url:
                return True, f"url matched: {page.url}"
            return False, f"url was {page.url}, expected to contain {cp.expected_url_contains}"

        # no explicit checkpoint assertion configured -- treat reaching the
        # end of the step list without error as success, but flag it
        return True, "no explicit checkpoint assertion configured; steps completed without error"

    def _match_business_outcome(self, page_text: str, artifact: CapabilityArtifact):
        for outcome in artifact.known_business_outcomes:
            if outcome.match_text in page_text:
                return outcome
        return None

    def _raise_intervention(self, artifact, step, page, evidence_dir, reason: str) -> str:
        from escalation.handoff import raise_intervention_request
        return raise_intervention_request(
            capability_id=artifact.capability_id,
            step_id=step.step_id,
            reason=reason,
            page=page,
            evidence_dir=evidence_dir,
        )

    @staticmethod
    def _resolve_template(template: str | None, input_values: dict) -> str:
        if not template:
            return ""
        def repl(m):
            key = m.group(1)
            return str(input_values.get(key, ""))
        return re.sub(r"\{\{(\w+)\}\}", repl, template)

    @staticmethod
    def _safe_text(page) -> str:
        try:
            return page.inner_text("body")
        except Exception:
            return ""

    def _fail(self, artifact, run_id, evidence_dir, structured_log, step_id, expected, observed) -> ReplayResult:
        self._flush_log(evidence_dir, structured_log)
        return ReplayResult(
            status="failure", capability_id=artifact.capability_id, run_id=run_id,
            failed_step_id=step_id, expected=expected, observed=observed,
            evidence_dir=str(evidence_dir),
        )

    @staticmethod
    def _flush_log(evidence_dir: Path, structured_log: list[dict]):
        (evidence_dir / "replay_log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in structured_log)
        )

    _last_extracted_value: str = ""
    _last_recovered_conditions: list = []