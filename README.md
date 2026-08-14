# Computer-Use Automation System

A small, real implementation of: **goal → LLM-driven discovery run → saved
capability artifact → deterministic replay (no LLM) with typed
success/business-outcome/failure handling → human escalation that takes
over the live session → evidence for both runs.**

Built for the interface.ai take-home assignment. See `/REPORT.md` for the
design write-up (architecture, schema rationale, error taxonomy,
heterogeneity/multi-tenant story, escalation model, safety model, and cuts).

## What's here

- `target_app/` — a deliberately legacy-styled mock banking servicing app
  (Flask, server-rendered, table-based layout, no test IDs) used as the
  proxy target. Not a real bank; all data is fake and in-memory.
- `artifacts/schema.py` — the typed, versioned capability artifact schema.
- `core/` — shared observation and locator-resolution logic used
  *identically* by discovery and replay.
- `llm/` — provider-agnostic LLM client interface, with Gemini and Claude
  implementations. The agent loop doesn't know which one it's talking to.
- `agent/discovery.py` — the LLM-driven observe → decide → act loop that
  produces a capability artifact.
- `replay/executor.py` — the deterministic replay engine (no LLM), with the
  business-outcome / recoverable-condition / hard-failure taxonomy.
- `guardrails/` — the allowlist + risk policy engine.
- `escalation/handoff.py` — human intervention request + same-session
  control transfer.
- `evidence/` — discovery and replay run evidence (screenshots, structured
  logs, saved artifacts).
- `tests/` — unit tests (schema, policy) and integration tests (replay
  engine against the live target app).

## Setup

Requires Python 3.11+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
```

### LLM provider

This defaults to **Gemini** (Google AI Studio has a genuine free tier — no
credit card, doesn't expire — https://aistudio.google.com/apikey). 

```bash
export GEMINI_API_KEY=your-key-here
```

Sanity-check the LLM connection before running the full agent:

```bash
python3 test_llm_connection.py
```

## Running it — the full demo path

**1. Start the target app** (in its own terminal, leave it running):

```bash
python3 target_app/app.py
# serves on http://127.0.0.1:5055/
```

**2. Run discovery** — a real LLM-driven session that produces a capability
artifact:

```bash
python3 run_discovery.py \
  --goal "look up member 12345 and read their current savings balance" \
  --capability-name lookup_member_balance \
  --member-id 12345
```

This saves the artifact to `artifacts/saved/lookup_member_balance.json` and
evidence (per-step screenshots + structured log) to
`evidence/discovery/lookup_member_balance/`.

**3. Replay the artifact deterministically** — no LLM involved:

```bash
# happy path
python3 run_replay.py --artifact artifacts/saved/lookup_member_balance.json \
  --param member_id=12345

# a legitimate business outcome, not a crash
python3 run_replay.py --artifact artifacts/saved/lookup_member_balance.json \
  --param member_id=00000

# a permission-denied business outcome
python3 run_replay.py --artifact artifacts/saved/lookup_member_balance.json \
  --param member_id=99999
```

Each replay prints a structured `ReplayResult` (status: `success` /
`business_outcome` / `failure` / `escalated`) and writes evidence
(screenshot + JSONL log) to `evidence/replay/`.

**4. Optional — discovery + replay for the escalation/irreversible-action
path** (opening a sub-account, stopping at confirmation, then requiring
human approval to finalize):

```bash
python3 run_discovery.py \
  --goal "open a new sub-account for this member and reach the confirmation screen" \
  --capability-name open_subaccount --member-id 67890

# without an operator attached: automation stops and escalates
python3 run_replay.py --artifact artifacts/saved/open_subaccount.json \
  --param member_id=67890 --param nickname="Vacation Fund" \
  --param account_type=holiday --param deposit=100

# with the mock operator attached: human takes over the SAME live
# session, performs the irreversible step, hands control back
python3 run_replay.py --artifact artifacts/saved/open_subaccount.json \
  --param member_id=67890 --param nickname="Vacation Fund" \
  --param account_type=holiday --param deposit=100 --with-operator
```

## Running the tests

```bash
# unit tests only
python3 -m pytest tests/test_policy.py tests/test_schema.py -v

# integration tests (target app must be running on :5055)
python3 -m pytest tests/ -v
```

## Running without live services

The unit tests (`test_policy.py`, `test_schema.py`) need no external
services at all. The replay engine and its integration tests need only the
local mock target app (no LLM, no internet) — replay never calls an LLM by
design. Only `run_discovery.py` needs a live LLM API key.
