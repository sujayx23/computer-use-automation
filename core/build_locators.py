from __future__ import annotations

from artifacts.schema import LocatorStrategy, LocatorStrategyKind


def strategies_for_element(el: dict) -> list[LocatorStrategy]:
    """Build a ranked fallback chain for an interactive element observed
    during discovery. Order reflects robustness, most-stable first:

      1. the form `name` attribute -- required by the browser for form
         submission, so it survives most redesigns even on legacy apps that
         have no test ids at all.
      2. accessibility role + accessible name -- survives markup/CSS churn,
         breaks if the visible label text changes.
      3. plain text match -- weakest, kept only as a last-resort fallback.
    """
    strategies: list[LocatorStrategy] = []
    name_attr = el.get("name_attr") or ""
    role = el.get("role") or ""
    name = el.get("name") or ""

    if name_attr:
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.CSS_NAME_ATTR,
            value=name_attr,
            rationale="form 'name' attribute; required for submission so it's stable across redesigns",
        ))
    if role and name:
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.ROLE,
            value=name,
            role=role,
            rationale="accessibility role + accessible name",
        ))
    if name and not (role and name):
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.TEXT,
            value=name,
            rationale="visible text fallback",
        ))
    if not strategies and name:
        strategies.append(LocatorStrategy(kind=LocatorStrategyKind.TEXT, value=name, rationale="last resort"))

    return strategies


def row_label_strategy(label_text: str) -> list[LocatorStrategy]:
    return [LocatorStrategy(
        kind=LocatorStrategyKind.ROW_LABEL,
        value=label_text,
        rationale="legacy label/value table row -- read the last cell in the row containing this label",
    )]
