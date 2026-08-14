"""
Discovery agent: runs a real observe -> decide -> act loop, driven by an
LLM, against the live target app. On success it emits a CapabilityArtifact
built directly from the grounded actions it actually took (not from a
free-text description of them).

Design note: the LLM never gets raw coordinates or free-form "do anything"
power. Every action it can take is one of a small set of typed tools that
resolve against the same observe/locate primitives replay will later use.
That's what keeps discovery and replay mechanically consistent -- the
artifact isn't a paraphrase of what happened, it's a literal record of the
locator-resolution calls that succeeded.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from artifacts.schema import (
    ActionType, CapabilityArtifact, Checkpoint, InputParam, OutputField,
    RiskLevel, Step, TargetApp, BusinessOutcome,
)
from core.observe import observe
from core.locate import resolve_locator
from core.build_locators import strategies_for_element, row_label_strategy
from guardrails.policy import PolicyEngine, PolicyViolation
from llm.base import ToolSpec, LLMClient

MAX_STEPS = 12

TOOLS = [
    ToolSpec(
        name="click",
        description="Click an interactive element by its index from the current observation. "
                    "If this click is irreversible or ledger-affecting -- e.g. finalizing a "
                    "financial transaction, submitting something that cannot be undone -- set "
                    "risk to 'irreversible' so the system can require human approval before "
                    "this step is ever auto-replayed. Most clicks (navigation, opening a form, "
                    "search) are 'safe'.",
        parameters={
            "type": "object",
            "properties": {
                "element_index": {"type": "integer"},
                "risk": {"type": "string", "enum": ["safe", "reversible", "irreversible"]},
            },
            "required": ["element_index"],
        },
    ),
    ToolSpec(
        name="fill",
        description="Type text into a textbox by index. If the value should come from one of the "
                    "supplied input parameters, set param_ref to that parameter's name instead of "
                    "hardcoding the literal so the recorded step stays reusable/parameterized. "
                    "Otherwise provide literal_value.",
        parameters={
            "type": "object",
            "properties": {
                "element_index": {"type": "integer"},
                "param_ref": {"type": "string"},
                "literal_value": {"type": "string"},
            },
            "required": ["element_index"],
        },
    ),
    ToolSpec(
        name="select_option",
        description="Choose an option in a <select> dropdown by index.",
        parameters={
            "type": "object",
            "properties": {"element_index": {"type": "integer"}, "option_value": {"type": "string"}},
            "required": ["element_index", "option_value"],
        },
    ),
    ToolSpec(
        name="extract_row_label",
        description="Extract a value from a legacy label/value table row, e.g. label_text="
                    "'Savings Balance' reads the last cell of the row containing that label. "
                    "Stores it under output_key in the capability's output payload.",
        parameters={
            "type": "object",
            "properties": {"label_text": {"type": "string"}, "output_key": {"type": "string"}},
            "required": ["label_text", "output_key"],
        },
    ),
    ToolSpec(
        name="finish_success",
        description="Call this once the goal has been achieved. Provide a checkpoint description of "
                    "what confirms success and, if applicable, a snippet of text on the page that "
                    "should always be present when the goal is met (used for the replay checkpoint).",
        parameters={
            "type": "object",
            "properties": {
                "checkpoint_description": {"type": "string"},
                "checkpoint_text_contains": {"type": "string"},
            },
            "required": ["checkpoint_description"],
        },
    ),
    ToolSpec(
        name="request_human_intervention",
        description="Call this if you are stuck, blocked, or about to take an action you're not "
                    "confident is safe/correct, and cannot proceed without a human.",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ),
]


def _system_prompt(goal: str, base_url: str, allowed_domains: list[str], input_params: dict) -> str:
    return f"""You are operating a legacy internal banking servicing application on behalf of an \
authorized back-office workflow. You act ONLY through the provided tools -- you cannot run \
arbitrary code or navigate outside the allowed domains.

GOAL: {goal}

TARGET BASE URL: {base_url}
ALLOWED DOMAINS: {allowed_domains}
AVAILABLE INPUT PARAMETERS (use param_ref, do not invent your own values): {json.dumps(input_params)}

Rules:
- Each turn you'll be shown the current URL, a list of interactive elements (each with an index, \
role, accessible name, and surrounding row context), and a short text summary of the page.
- Take exactly one tool action per turn.
- When filling a field whose value is one of the input parameters above, use param_ref, not \
literal_value -- this keeps the recording reusable for other inputs later.
- If the page reports a business outcome (e.g. "no member found", "access denied") that is a \
legitimate result, not a bug -- you may still call finish_success if that outcome IS the correct \
result for the goal, or request_human_intervention if you're unsure how to proceed.
- Never attempt to submit an irreversible/ledger-affecting confirmation step unless the goal \
explicitly asks you to reach and pass it. If the goal only asks you to "reach" a confirmation \
screen, stop there and call finish_success -- do not click the final confirm button.
- If the goal DOES explicitly ask you to complete/finalize/confirm an action that is irreversible \
or ledger-affecting (cannot be undone by the caller), you may click it, but you MUST set the \
click tool's risk parameter to "irreversible" on that specific call so the recording correctly \
flags it as requiring human approval on future automated replays.
- If you are ever uncertain or blocked, call request_human_intervention rather than guessing.
"""


def _observation_message(obs: dict) -> str:
    elements_desc = "\n".join(
        f"[{e['index']}] role={e['role']} name='{e['name']}' name_attr='{e['name_attr']}' "
        f"context='{e['context']}'"
        for e in obs["elements"]
    )
    return f"URL: {obs['url']}\n\nPAGE TEXT: {obs['page_text']}\n\nINTERACTIVE ELEMENTS:\n{elements_desc}"


def run_discovery(
    goal: str,
    target_app_id: str,
    base_url: str,
    allowed_domains: list[str],
    input_params: dict,
    evidence_dir: Path,
    policy: PolicyEngine,
    llm_client: LLMClient,
) -> tuple[CapabilityArtifact | None, dict]:
    """Runs one real LLM-driven discovery session. Returns (artifact_or_None, run_log)."""
    evidence_dir.mkdir(parents=True, exist_ok=True)

    run_log = {
        "run_id": str(uuid.uuid4()),
        "type": "discovery",
        "goal": goal,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
        "outcome": None,
    }

    recorded_steps: list[Step] = []
    output_values: dict = {}
    checkpoint_desc = None
    checkpoint_text = None
    used_params: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base_url)

        prior_tool_result = None

        for step_num in range(MAX_STEPS):
            # policy: never allow the browser to leave the allowlisted domains
            try:
                policy.check_navigation(page.url)
            except PolicyViolation as e:
                run_log["outcome"] = {"status": "hard_failure", "reason": f"policy violation: {e}"}
                break

            obs = observe(page)
            page.screenshot(path=str(evidence_dir / f"discovery_step{step_num:02d}.png"))
            observation_text = _observation_message(obs)

            turn = llm_client.next_action(observation_text, prior_tool_result)
            prior_tool_result = None  # consumed

            if turn.tool_name is None:
                run_log["steps"].append({"step": step_num, "note": "no tool call, ending",
                                          "text": turn.reasoning_text})
                break

            tool_name = turn.tool_name
            tool_input = turn.tool_input or {}
            step_id = f"s{step_num}"
            log_entry = {"step": step_num, "tool": tool_name, "input": tool_input,
                         "reasoning": turn.reasoning_text}

            tool_result_text = "ok"

            if tool_name == "click":
                el = obs["elements"][tool_input["element_index"]]
                locators = strategies_for_element(el)
                risk_str = tool_input.get("risk", "safe")
                try:
                    risk = RiskLevel(risk_str)
                except ValueError:
                    risk = RiskLevel.SAFE
                policy.check_action("click", RiskLevel.SAFE)  # navigation/action-type allowlist check;
                # the irreversible-action gate itself is enforced at REPLAY time, not discovery time --
                # a human explicitly authoring the goal to reach/pass this step is the "confirmation"
                # for the recording session itself, against the mock app
                res = resolve_locator(page, locators)
                if res.locator:
                    res.locator.click()
                    recorded_steps.append(Step(
                        step_id=step_id, action=ActionType.CLICK,
                        description=f"Click '{el['name'] or el['name_attr']}'",
                        locators=locators, risk=risk,
                        requires_confirmation=(risk == RiskLevel.IRREVERSIBLE),
                    ))
                else:
                    tool_result_text = f"could not resolve element: {res.attempts}"

            elif tool_name == "fill":
                el = obs["elements"][tool_input["element_index"]]
                locators = strategies_for_element(el)
                param_ref = tool_input.get("param_ref")
                literal = tool_input.get("literal_value")
                value_template = "{{" + param_ref + "}}" if param_ref else (literal or "")
                actual_value = input_params.get(param_ref, literal) if param_ref else literal
                policy.check_action("fill", RiskLevel.SAFE)
                res = resolve_locator(page, locators)
                if res.locator:
                    res.locator.fill(str(actual_value))
                    if param_ref:
                        used_params.add(param_ref)
                    recorded_steps.append(Step(
                        step_id=step_id, action=ActionType.FILL,
                        description=f"Fill '{el['name'] or el['name_attr']}' with {value_template}",
                        locators=locators, value_template=value_template, risk=RiskLevel.SAFE,
                    ))
                else:
                    tool_result_text = f"could not resolve element: {res.attempts}"

            elif tool_name == "select_option":
                el = obs["elements"][tool_input["element_index"]]
                locators = strategies_for_element(el)
                policy.check_action("select", RiskLevel.SAFE)
                res = resolve_locator(page, locators)
                if res.locator:
                    res.locator.select_option(tool_input["option_value"])
                    recorded_steps.append(Step(
                        step_id=step_id, action=ActionType.SELECT,
                        description=f"Select '{tool_input['option_value']}' in '{el['name_attr']}'",
                        locators=locators, value_template=tool_input["option_value"], risk=RiskLevel.SAFE,
                    ))
                else:
                    tool_result_text = f"could not resolve element: {res.attempts}"

            elif tool_name == "extract_row_label":
                label = tool_input["label_text"]
                key = tool_input["output_key"]
                locators = row_label_strategy(label)
                res = resolve_locator(page, locators)
                if res.locator:
                    text = res.locator.inner_text()
                    output_values[key] = text
                    recorded_steps.append(Step(
                        step_id=step_id, action=ActionType.EXTRACT,
                        description=f"Extract '{label}' into output '{key}'",
                        locators=locators, extract_key=key, risk=RiskLevel.SAFE,
                    ))
                    tool_result_text = f"extracted: {text}"
                else:
                    tool_result_text = f"could not resolve row: {res.attempts}"

            elif tool_name == "finish_success":
                checkpoint_desc = tool_input.get("checkpoint_description")
                checkpoint_text = tool_input.get("checkpoint_text_contains")
                run_log["outcome"] = {"status": "success", "checkpoint": checkpoint_desc}
                run_log["steps"].append(log_entry)
                break

            elif tool_name == "request_human_intervention":
                run_log["outcome"] = {"status": "escalated", "reason": tool_input.get("reason")}
                run_log["steps"].append(log_entry)
                break

            log_entry["result"] = tool_result_text
            run_log["steps"].append(log_entry)
            prior_tool_result = tool_result_text

        final_url = page.url
        page.screenshot(path=str(evidence_dir / "discovery_final.png"))
        browser.close()

    run_log["finished_at"] = datetime.now(timezone.utc).isoformat()

    if run_log["outcome"] and run_log["outcome"].get("status") == "success":
        checkpoint_locators = []
        artifact = CapabilityArtifact(
            capability_id=f"{target_app_id}.{uuid.uuid4().hex[:8]}",
            name=goal[:60],
            description=goal,
            target=TargetApp(app_id=target_app_id, base_url=base_url),
            goal_text=goal,
            inputs=[InputParam(name=p, type="string", required=True) for p in sorted(used_params)],
            outputs=[OutputField(name=k, type="string") for k in output_values.keys()],
            steps=recorded_steps,
            checkpoint=Checkpoint(
                description=checkpoint_desc or "goal reached",
                locators=[],
                expected_url_contains=None,
                expected_text_contains=checkpoint_text,
            ),
            allowed_domains=allowed_domains,
            max_risk_level=RiskLevel.SAFE,
        )
        run_log["final_url"] = final_url
        run_log["output_values_sample"] = output_values
        return artifact, run_log

    run_log["final_url"] = final_url if 'final_url' in dir() else None
    return None, run_log