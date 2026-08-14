"""
Shared element-resolution logic.

Both the discovery agent (while acting) and the replay engine (while
replaying) call resolve_locator() with the *same* ranked strategy list for a
given step. This is what makes "record once, replay many" meaningful: replay
isn't reinterpreting the recording, it's re-running the exact same resolution
procedure discovery already proved works.

Strategies are tried in order; the first one that resolves to exactly one
visible, actionable element wins. If a strategy matches zero or multiple
elements, we fall through to the next one and note the miss (surfaced in
replay evidence for debugging drift).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, Locator, TimeoutError as PWTimeout

from artifacts.schema import LocatorStrategy, LocatorStrategyKind


@dataclass
class ResolveResult:
    locator: Optional[Locator]
    used_strategy_index: Optional[int]
    attempts: list[str]  # human-readable trail of what was tried and why it failed/succeeded


def resolve_locator(page: Page, strategies: list[LocatorStrategy], timeout_ms: int = 4000) -> ResolveResult:
    attempts: list[str] = []
    ambiguous_fallback: Optional[tuple[int, Locator]] = None

    # First pass: prefer any strategy that resolves unambiguously. An
    # earlier strategy matching multiple elements should NOT win over a
    # later strategy that matches exactly one -- ambiguity is a signal to
    # keep trying, not a reason to guess.
    for i, strat in enumerate(strategies):
        try:
            loc = _build(page, strat)
            count = loc.count()
            if count == 1:
                attempts.append(f"[{i}] {strat.kind}='{strat.value}' -> 1 match (used)")
                return ResolveResult(locator=loc, used_strategy_index=i, attempts=attempts)
            elif count > 1:
                attempts.append(f"[{i}] {strat.kind}='{strat.value}' -> {count} matches (ambiguous, deferred)")
                if ambiguous_fallback is None:
                    ambiguous_fallback = (i, loc.first)
            else:
                attempts.append(f"[{i}] {strat.kind}='{strat.value}' -> 0 matches")
        except Exception as e:  # noqa: BLE001 - broad on purpose, this is a probe loop
            attempts.append(f"[{i}] {strat.kind}='{strat.value}' -> error: {e}")

    # No strategy resolved unambiguously -- fall back to the first visible
    # match of the first strategy that was at least ambiguous (better than
    # nothing, and clearly logged as a fallback rather than a clean hit).
    if ambiguous_fallback is not None:
        idx, loc = ambiguous_fallback
        attempts.append(f"using ambiguous fallback from strategy [{idx}]")
        return ResolveResult(locator=loc, used_strategy_index=idx, attempts=attempts)

    return ResolveResult(locator=None, used_strategy_index=None, attempts=attempts)


def _build(page: Page, strat: LocatorStrategy) -> Locator:
    kind = strat.kind
    if kind == LocatorStrategyKind.ROLE or kind == "role":
        return page.get_by_role(strat.role, name=strat.value, exact=False)
    if kind == LocatorStrategyKind.CSS_NAME_ATTR or kind == "css_name_attr":
        return page.locator(f'[name="{strat.value}"]')
    if kind == LocatorStrategyKind.TEXT or kind == "text":
        return page.get_by_text(strat.value, exact=False)
    if kind == LocatorStrategyKind.ROW_LABEL or kind == "row_label":
        # find the element containing the label text itself (not an ancestor
        # that merely contains it somewhere in its subtree -- with nested
        # tables, `tr[has_text=X]` matches every ancestor row too, which is
        # the wrong anchor), then walk up to its row and read the last
        # *direct* child cell of that row.
        label_el = page.get_by_text(strat.value, exact=False).first
        return label_el.locator("xpath=ancestor::tr[1]/td[last()]")
    if kind == LocatorStrategyKind.CSS_SELECTOR or kind == "css_selector":
        return page.locator(strat.value)
    raise ValueError(f"Unknown locator kind: {kind}")
