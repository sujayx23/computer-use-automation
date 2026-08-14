# REPORT

## 1. Architecture

Single Python process, synchronous, no queues or services — the brief is
explicit that premature scaling infrastructure isn't rewarded, and a
record-once/replay-many system doesn't need concurrency to prove the design
works. Five pieces, each with one job:

- **`core/observe.py`** turns the live page into a compact, grounded
  representation: a list of interactive elements (role, accessible name,
  `name` attribute, surrounding row text) extracted via JS, not a screenshot
  or raw DOM dump. This is deliberate — it's the one representation that
  degrades gracefully on a surface with no clean DOM, since it depends on
  role/text/structure rather than CSS classes or test IDs.
- **`core/locate.py`** resolves a *ranked list* of locator strategies against
  the live page. This module is called by **both** discovery and replay —
  same function, same fallback order. That's the actual mechanism behind
  "record once, replay many": replay isn't reinterpreting a recording, it's
  re-running the identical resolution procedure discovery already proved
  works.
- **`llm/`** is a small LLM client interface (`LLMClient.next_action`) with
  a Gemini implementation behind it. The agent loop never touches the
  Gemini SDK directly -- it only knows the interface. That separation is
  what lets the observe/decide/act loop, the artifact-building logic, and
  the tool schema stay identical regardless of which model is behind the
  interface, and it costs nothing to keep even with a single provider.
- **`agent/discovery.py`** is the observe → decide → act loop. The LLM only
  ever gets a small fixed set of typed tools (click / fill / select /
  extract_row_label / finish_success / request_human_intervention) — never
  raw coordinates or "run arbitrary code" power. Every tool call resolves
  through `core/locate.py`, so the artifact it produces is a literal record
  of the locator calls that actually succeeded, not a paraphrase of a
  transcript.
- **`replay/executor.py`** replays an artifact with zero LLM involvement,
  classifying every outcome into success / business_outcome / failure /
  escalated (see §3).

**Key trade-off:** the observation layer is grounded (role + name + `name`
attribute), not vision-based (screenshot + coordinates). This is more work
up front and doesn't handle canvas/image-only UIs, but it's the choice that
matches the environment described in the brief — legacy server-rendered
apps with no clean DOM are explicitly the target, and grounded locators
survive layout/CSS changes far better than pixel coordinates do.

## 2. Artifact schema

`artifacts/schema.py`. Design goals, in order of priority:

1. **Decoupled from the transcript.** An artifact has no prompts, no
   chain-of-thought, no raw model output — only steps, locators, and typed
   I/O. A reviewer or calling agent shouldn't need to read an LLM transcript
   to understand what a capability does.
2. **Every locator is a ranked fallback chain, not a single selector.**
   Each `Step.locators` is a list of `LocatorStrategy` objects tried in
   order (`css_name_attr` → `role` → `text`, or `row_label` for label/value
   table extraction). This is the single biggest lever for graceful
   degradation across tenant variants: if a redesign drops the `name`
   attribute but keeps the visible label, replay still works — it just
   falls back one level, and that fallback is visible in the replay log for
   drift monitoring.
3. **Explicit typed contract**, not just a step list: `inputs` (typed,
   flagged `sensitive`), `outputs`, and a `checkpoint`. A calling agent
   should be able to invoke a capability knowing exactly what it needs to
   supply and what it gets back, without reading the steps at all.
4. **Risk is a first-class field per step** (`safe` / `reversible` /
   `irreversible`), not inferred at replay time. Discovery decides this while
   it has full context; replay and the policy engine just enforce it.
5. **Versioned and reviewable.** `schema_version`, `version`,
   `review_status` (`draft`/`approved`), `created_by`. An artifact produced
   by discovery starts as `draft` — nothing in this system auto-promotes an
   artifact to trusted, unattended production use.

I chose *not* to make the schema executable-code-like (e.g. embedding
Python/JS snippets per step). Every step is declarative data, which is what
makes "a human reviewer can understand what the capability does" true
without needing to execute or read code.

## 3. Determinism & error handling

Determinism comes from two things: (a) replay uses the exact same
`resolve_locator()` code discovery used, with the exact same ranked
strategies recorded during discovery, and (b) replay never asks a model to
decide anything — every branch is a lookup against artifact data
(`known_business_outcomes`, `recoverable_on`, `checkpoint`).

At every step, and at the final checkpoint, results are classified into
exactly three buckets, checked in this order:

1. **Business outcome** — is the current page text a match for one of the
   artifact's `known_business_outcomes` (e.g. "no member found", "access
   denied")? If so, that's the answer, returned as `status=business_outcome`
   with a name and detail — never conflated with a system failure.
2. **Recoverable condition** — does the page match a step's declared
   `recoverable_on` pattern (e.g. "Session Expired")? If so, apply the
   declared strategy (`reload` / `wait_and_retry`), bounded by
   `max_attempts`, and retry the same step. The session-timeout scenario in
   the demo app exercises this end to end: first hit expires, `reload()`
   re-requests the same clean URL, second attempt succeeds.
3. **Hard failure** — anything else. The result carries the failed step id,
   what was expected, and what was actually observed (locator resolution
   attempts, or a checkpoint mismatch), plus a screenshot — enough to debug
   without reproducing.

Locator ambiguity (a strategy matching more than one element) is treated as
a drift signal, not silently papered over: it's logged with which strategy
index resolved and how many matches it found, so repeated ambiguity on a
given step is visible evidence that the surface has changed.

**Secondary, not primary:** UI drift. The brief is right that this
environment's UIs are comparatively stable — the fallback-chain locator
strategy and the ambiguity logging exist mainly to make drift *detectable*
and *gracefully degraded*, not to solve it outright. A full confidence-score
/ approval-gate system (stretch goal 8) would be the next layer here.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** The seam is exactly `core/observe.py` +
`core/locate.py` vs. everything else. Extending to a different surface means
swapping the *observation* mechanism (what "elements" and "role/name" mean)
and the *locator resolution* backend, while the artifact schema, the agent
loop's tool contract, and the replay engine's error taxonomy stay
unchanged:
- **Legacy web (framesets, iframes):** `observe()` needs to walk frames and
  qualify locators with a frame path; `LocatorStrategy` would gain an
  optional `frame_path` field. No other layer changes.
- **Desktop app:** swap Playwright for an OS accessibility API (e.g. UIA on
  Windows, AT-SPI on Linux) behind the same `observe()`/`resolve_locator()`
  contract — role + accessible name is a concept both browsers and desktop
  accessibility trees expose, which is exactly why the schema uses `role`
  and not `css_selector` as the *primary* strategy kind.

**Multi-tenant reuse.** Two things would need to be added on top of the
current schema (not built here, per the brief's "design, not necessarily
build" scope):
1. **Canonicalization.** An artifact recorded against one tenant's instance
   should store *route/value patterns*, not concrete instance values — e.g.
   `/member/:id` rather than `/member/12345`. The `TargetApp.vendor_product`
   field is the anchor for this: artifacts would key off vendor product +
   version, not tenant, with tenant-specific `base_url`/branding supplied at
   invocation time.
2. **Per-tenant override layer.** Rather than re-recording per tenant, a
   tenant-specific overlay could patch individual `LocatorStrategy` entries
   or `value_template`s (e.g. tenant B renamed a field's `name` attribute) on
   top of the base artifact, versioned separately. Drift detection is the
   replay log's ambiguity/fallback signal from §3, aggregated across tenants
   running the same `vendor_product` — a spike in fallback-strategy usage or
   ambiguous resolutions for one tenant is the trigger to review that
   tenant's overlay, without needing to touch the base artifact.

## 5. Escalation & handoff

Detection happens at exactly the points where the system can't safely act
alone: the discovery agent can call `request_human_intervention` when it's
uncertain, and replay escalates automatically whenever it hits a step
flagged `requires_confirmation`/`irreversible` that the policy engine blocks
(see §6). Both paths converge on `escalation/handoff.py`.

`raise_intervention_request()` is the "detect and route" half: it captures
capability id, step id, reason, current URL, and a live screenshot, writes
it to a durable JSONL log plus a per-run JSON record, and returns an
intervention id. That record is everything a real operator console would
need to render.

The control-transfer model is the part I made sure was *mechanically real*,
even though the operator UI itself is mocked (per the brief's explicit scope
note): the replay executor keeps the same Playwright `page` object open
across the entire escalation. `operator_callback` receives that live page —
not a fresh session, not a rehydrated cookie jar — performs the action, and
returns control to the automation loop, which resumes on the very next
artifact step. Every transition (`control_transfer: human`,
`operator_action`, `control_transfer: automation`) is logged, and the
human's own action is recorded as a distinct evidence entry
(`human_action.json`), separately from automation-performed steps.

**What's mocked, explicitly:** the operator is a scripted function
(`mock_operator_approve_and_perform`) standing in for a person looking at
the intervention record and deciding to act — there's no real-time
co-browsing UI, per the brief's scope note. What's real: the pause, the
context capture, the same-session takeover, the resume, and the audit
trail. A production version would replace only `operator_callback` with an
actual console surface; nothing else in the control-transfer model would
need to change.

## 6. Safety

**Allowlist enforcement** (`guardrails/policy.py`) has two independent
checks, both consulted by discovery *and* replay: `check_navigation()`
hard-stops on any URL outside the configured domain allowlist, and
`check_action()` hard-stops on any action type not in the configured
allowlist. Neither is advisory — both raise and the caller must handle the
resulting `PolicyViolation`.

**Risk handling.** Every step carries a `RiskLevel`. `irreversible` steps
are blocked by default (`block_irreversible_by_default=True`) unless
explicitly pre-authorized (`--allow-irreversible`) or escalated to and
approved by a human operator. In the demo, finalizing a new sub-account
(a ledger-affecting, non-undoable action) is the irreversible step —
discovery itself is instructed never to click past a confirmation screen
unless the goal explicitly asks it to, and replay refuses to auto-execute
it without either explicit pre-authorization or a resolved human handoff.

**Data handling.** Input parameters can be flagged `sensitive` on the
artifact (e.g. member id, in this demo); the policy engine's `redact()`
partially masks them in every structured log line before it's written —
logs never contain the raw value. No credentials, tokens, or session
material are logged or persisted into artifacts at all; the schema has no
field for them by construction.

**Limits, honestly:** this is a small, explicit, file-based policy — there's
no anomaly detection, no rate limiting, no cross-run behavioral analysis.
For a real deployment across hundreds of tenants, I'd want the policy
itself to be tenant-scoped and centrally managed rather than a single local
JSON file, and I'd want the redaction rule set to be driven by a proper PII
classifier rather than a per-field `sensitive` flag a human has to remember
to set.

## 7. Cuts

**Left out entirely:**
- A real operator console — mocked per the brief's own scope note (§5).
- Multi-tenant/desktop support — designed for (§4), not built, per the
  brief's explicit "design, not necessarily build" instruction.
- Any of the optional stretch goals (agent-facing capability catalog,
  confidence scoring, assisted LLM fallback, cross-tenant canonicalization
  demo, multi-run stability reporting) — the brief asks for at most one or
  two and prioritizes depth on the core requirements over any of them.

- **Partial parameterization on the `open_subaccount` capability, found
  during evidence capture, not designed in.** That discovery run was
  invoked with only `member_id` supplied as a named input parameter
  (`--member-id`); `nickname`, `account_type`, and `deposit` were values the
  LLM chose on the spot with no `param_ref` to bind them to, since the CLI
  only exposed one parameter slot at record time. The resulting artifact
  therefore always opens a "Vacation Fund" holiday sub-account with a $50
  deposit on replay, regardless of what's passed for those fields — the
  `lookup_member_balance` capability, by contrast, fully parameterizes
  `member_id` and generalizes correctly to inputs never seen during
  discovery (verified in `/evidence/`). I'd rather report this honestly
  than paper over it: it's a real gap between what the artifact schema
  supports (full parameterization) and what one specific discovery
  invocation happened to produce. The fix is mechanical, not a design
  change -- `run_discovery.py` would need a generic `--param key=value` flag
  (matching `run_replay.py`'s existing convention) so a future recording of
  this same goal could bind all four fields as reusable parameters.

**What I'd build next with more time:** the confidence/approval stretch
goal (score artifacts by replay reliability, gate unattended use on
`review_status=approved`) is the natural next layer on top of the
`draft`/`approved` field that already exists in the schema but isn't
enforced anywhere yet — replay currently runs `draft` artifacts exactly the
same as `approved` ones, which a production system should not do.
