"""
Observation layer: turns the live page into a compact, grounded
representation the LLM can reason over and act against.

Deliberately NOT screenshot+coordinates as the primary channel -- we extract
an accessibility-flavored element list (role, accessible name, the raw
`name` attribute if present, and surrounding row context) directly from the
page. This is the mechanism that would still work with no clean DOM: it
doesn't depend on CSS classes or test ids, only on role/text/structure, which
degrade far more gracefully on legacy markup. A screenshot is captured
alongside purely as an evidence artifact for humans, not as the model's
primary input.
"""
from __future__ import annotations

from playwright.sync_api import Page

_EXTRACT_JS = """
() => {
  const out = [];
  const nodes = document.querySelectorAll('input, select, button, a, textarea');
  nodes.forEach((el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;
    let role = el.tagName.toLowerCase();
    if (el.tagName === 'INPUT') {
      role = (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox';
    }
    if (el.tagName === 'SELECT') role = 'combobox';
    if (el.tagName === 'A') role = 'link';
    if (el.tagName === 'BUTTON') role = 'button';

    let name = el.getAttribute('aria-label') || '';
    if (!name && (el.tagName === 'INPUT' || el.tagName === 'BUTTON')) name = el.value || '';
    if (!name) name = (el.innerText || '').trim();
    if (!name) name = el.getAttribute('placeholder') || '';
    name = (name || '').trim().slice(0, 60);

    let context = '';
    const row = el.closest('tr');
    if (row) context = row.innerText.trim().replace(/\\s+/g, ' ').slice(0, 100);

    out.push({
      index: out.length,
      tag: el.tagName.toLowerCase(),
      role,
      name,
      name_attr: el.getAttribute('name') || '',
      value_attr: (el.tagName === 'INPUT' || el.tagName === 'SELECT') ? (el.value || '') : '',
      context,
    });
  });
  return out;
}
"""


def extract_elements(page: Page) -> list[dict]:
    return page.evaluate(_EXTRACT_JS)


def page_text_summary(page: Page, max_chars: int = 1500) -> str:
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    text = " ".join(text.split())
    return text[:max_chars]


def observe(page: Page) -> dict:
    return {
        "url": page.url,
        "elements": extract_elements(page),
        "page_text": page_text_summary(page),
    }
