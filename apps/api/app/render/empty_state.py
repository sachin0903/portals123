"""Empty-state vocabulary for the rendered visuals (charts + tables).

An empty visual has three distinct causes and each gets its own words, because the reader's next
action differs: the dataset returned no rows (nothing is broken — this client has none of that
thing), the component has no fields/columns chosen (an authoring omission, fixable in the editor),
or the dataset could not be resolved at all (a blocking problem, reported upstream by
``resolve.resolve_datasets_reporting`` — deliberately no message here).

The dataset code is read the way ``components._dataset_code`` reads it (``binding._resolved_data``
= ``"{{ code }}"``). It lives here rather than there because ``components`` imports the chart and
table renderers, so a renderer importing ``components`` would be a cycle.
"""

from __future__ import annotations

import re
from typing import Any

CHART_NO_FIELDS = "This chart has no fields selected"
TABLE_NO_COLUMNS = "This table has no columns"

# What an unnamed dataset is called in prose — a component may carry manual rows or no binding at
# all, and "No data to show" still reads as a sentence.
FALLBACK_LABEL = "data"

# The catalogue's reserved prefix: `book_valuations` is read aloud as "valuations".
_RESERVED_PREFIX = "book_"
_RESOLVED = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def dataset_code(comp: dict[str, Any]) -> str:
    """The dataset code a component binds — from ``binding._resolved_data`` (``{{ code }}``)."""
    resolved = str((comp.get("binding") or {}).get("_resolved_data") or "")
    m = _RESOLVED.search(resolved)
    return m.group(1).strip() if m else ""


def label_for_code(code: str) -> str:
    """A dataset code as the words a reader knows it by: drop the reserved prefix, underscores
    become spaces (``book_valuations`` → ``valuations``)."""
    words = code.strip().removeprefix(_RESERVED_PREFIX).replace("_", " ").strip()
    return words or FALLBACK_LABEL


def dataset_label(comp: dict[str, Any]) -> str:
    """The words for whatever this component binds (``data`` when it binds nothing nameable)."""
    return label_for_code(dataset_code(comp))


def no_rows_message(label: str) -> str:
    """Cause 1: the dataset resolved and held nothing for this client."""
    return f"No {label} to show"
