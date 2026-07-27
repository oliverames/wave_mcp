"""Exception types for the Wave MCP server.

Wave's GraphQL API reports problems in two different places, and they mean very
different things to a caller:

* Transport/document level -- the top-level ``errors`` array. Authentication
  failures, malformed documents, and rate limiting land here.
* Business level -- ``inputErrors`` inside a mutation payload, alongside
  ``didSucceed: false``. The request was well formed; Wave rejected the values.

Both are surfaced as exceptions so tool handlers can convert them into a single
consistent, actionable message.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class WaveError(Exception):
    """Base class for every error raised by this server."""


class WaveConfigError(WaveError):
    """Raised when the server is misconfigured (missing token, no business)."""


class WaveAuthError(WaveError):
    """Raised when Wave rejects the access token."""


class WaveRateLimitError(WaveError):
    """Raised when Wave returns HTTP 429."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class WaveGraphQLError(WaveError):
    """Raised when Wave returns a top-level ``errors`` array.

    ``errors`` holds the raw entries so callers can inspect
    ``extensions.code`` (``GRAPHQL_VALIDATION_FAILED``, ``BAD_USER_INPUT``,
    ``UNAUTHENTICATED``, ...) rather than string-matching on messages.
    """

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []

    @property
    def codes(self) -> List[str]:
        return [
            (e.get("extensions") or {}).get("code", "")
            for e in self.errors
            if (e.get("extensions") or {}).get("code")
        ]


class WaveMutationError(WaveError):
    """Raised when a mutation returns ``didSucceed: false``.

    ``input_errors`` mirrors Wave's ``InputError`` shape: ``path``, ``message``,
    ``code``.
    """

    def __init__(self, mutation: str, input_errors: Optional[List[Dict[str, Any]]] = None):
        self.mutation = mutation
        self.input_errors = input_errors or []
        super().__init__(self._format())

    def _format(self) -> str:
        if not self.input_errors:
            return (
                f"{self.mutation} failed and Wave returned no field-level detail. "
                "This usually means a referenced ID belongs to a different business."
            )
        parts = []
        for err in self.input_errors:
            path = err.get("path")
            if isinstance(path, list):
                path = ".".join(str(p) for p in path)
            code = err.get("code")
            label = path or "input"
            suffix = f" [{code}]" if code else ""
            parts.append(f"{label}: {err.get('message', 'invalid')}{suffix}")
        return f"{self.mutation} failed -- " + "; ".join(parts)
