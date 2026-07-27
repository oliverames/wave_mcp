"""Rendering helpers shared by every tool.

Each tool takes a ``response_format`` of ``"markdown"`` (default, compact and
readable) or ``"json"`` (complete, for programmatic use). Markdown deliberately
drops empty fields so a list of twenty invoices does not bury the useful
columns under nulls.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

ResponseFormat = str  # "markdown" | "json"


def render(
    data: Any,
    response_format: ResponseFormat,
    markdown_fn: Callable[[], str],
) -> str:
    """Return either pretty JSON or the caller's markdown rendering."""
    if response_format == "json":
        return json.dumps(data, indent=2, default=str)
    return markdown_fn()


def as_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


# --------------------------------------------------------------------- scalars


def money(value: Optional[Dict[str, Any]]) -> str:
    """Format a Wave ``Money`` object as e.g. ``$1,234.56 CAD``."""
    if not value:
        return "-"
    display = value.get("value")
    currency = (value.get("currency") or {}).get("code", "")
    symbol = (value.get("currency") or {}).get("symbol", "")
    if display is None:
        return "-"
    prefix = symbol or ""
    return f"{prefix}{display} {currency}".strip()


def yes_no(value: Optional[bool]) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def address(value: Optional[Dict[str, Any]]) -> str:
    """Flatten an Address into a single comma-separated line."""
    if not value:
        return ""
    parts = [
        value.get("addressLine1"),
        value.get("addressLine2"),
        value.get("city"),
        (value.get("province") or {}).get("name") if value.get("province") else None,
        value.get("postalCode"),
        (value.get("country") or {}).get("name") if value.get("country") else None,
    ]
    return ", ".join(p for p in parts if p)


# ------------------------------------------------------------------- structures


def kv_block(pairs: Sequence[tuple], *, skip_empty: bool = True) -> str:
    """Render ``(label, value)`` pairs as a markdown bullet list."""
    lines = []
    for label, value in pairs:
        if skip_empty and (value is None or value == "" or value == "-"):
            continue
        lines.append(f"- **{label}:** {value}")
    return "\n".join(lines)


def table(rows: List[Dict[str, Any]], columns: Sequence[tuple]) -> str:
    """Render rows as a markdown table.

    ``columns`` is a sequence of ``(header, accessor)`` where accessor is either
    a key name or a callable taking the row.
    """
    if not rows:
        return "_No records._"

    headers = [c[0] for c in columns]
    body = []
    for row in rows:
        cells = []
        for _, accessor in columns:
            value = accessor(row) if callable(accessor) else row.get(accessor)
            cells.append(_cell(value))
        body.append("| " + " | ".join(cells) + " |")

    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *body,
        ]
    )


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return yes_no(value)
    text = str(value)
    # Pipes would break the table; newlines would break the row.
    return text.replace("|", "\\|").replace("\n", " ")


def pagination_footer(result: Dict[str, Any]) -> str:
    """Describe where the caller is in a paginated set, and how to advance."""
    total = result.get("total_count")
    count = result.get("count", 0)

    if result.get("fetched_all"):
        return f"\n_Returned all {count} record(s)._"

    page = result.get("page", 1)
    total_pages = result.get("total_pages") or 1
    line = f"\n_Page {page} of {total_pages} - showing {count}"
    if total is not None:
        line += f" of {total}"
    line += " record(s)._"

    if result.get("has_more"):
        line += (
            f" Pass `page={result['next_page']}` for the next page, "
            "or `fetch_all=true` to retrieve every record."
        )
    return line


def listing(
    result: Dict[str, Any],
    title: str,
    columns: Sequence[tuple],
    *,
    empty_hint: str = "",
) -> str:
    """Standard markdown rendering for a paginated list of records."""
    items = result.get("items") or []
    if not items:
        message = f"**{title}**\n\nNo records found."
        return f"{message} {empty_hint}".rstrip()
    return f"**{title}**\n\n{table(items, columns)}\n{pagination_footer(result)}"


def success(message: str, pairs: Sequence[tuple] = ()) -> str:
    """Standard confirmation for a successful mutation."""
    block = kv_block(pairs)
    return f"{message}\n\n{block}" if block else message
