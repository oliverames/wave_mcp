"""Async GraphQL client for Wave's public API.

Wave exposes a single endpoint, ``https://gql.waveapps.com/graphql/public``,
authenticated with an OAuth2 bearer token. Everything the API can do goes
through this one client.

Two Wave-specific behaviours shape the design:

* Pagination is offset-based (``page``/``pageSize``) rather than cursor-based,
  and every connection returns an ``OffsetPageInfo``. :meth:`paginate` walks
  those pages with a hard ceiling so a runaway loop cannot hang a tool call.
* Mutations report business-level failures inside the payload
  (``didSucceed``/``inputErrors``) rather than as GraphQL errors, so
  :meth:`mutate` checks the payload and raises rather than returning a
  success-shaped object that actually failed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

import httpx

from .errors import (
    WaveAuthError,
    WaveConfigError,
    WaveGraphQLError,
    WaveMutationError,
    WaveRateLimitError,
)

logger = logging.getLogger("wave-mcp-server.client")

WAVE_ENDPOINT = "https://gql.waveapps.com/graphql/public"

# Wave caps pageSize at 200 on its offset-paginated connections.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

# Ceiling for paginate(). At the 200 max page size this is 100k records, far
# past anything a tool response should return, so hitting it means something
# is wrong rather than merely large.
MAX_PAGES = 500


class WaveClient:
    """Thin, well-instrumented wrapper around Wave's GraphQL endpoint."""

    def __init__(
        self,
        access_token: str,
        *,
        endpoint: str = WAVE_ENDPOINT,
        business_id: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        if not access_token:
            raise WaveConfigError(
                "A Wave access token is required. Set WAVE_ACCESS_TOKEN in the "
                "environment or in a .env file next to the server."
            )
        self.access_token = access_token
        self.endpoint = endpoint
        self.business_id = business_id
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    # ---------------------------------------------------------------- lifecycle

    @classmethod
    def from_env(cls) -> "WaveClient":
        token = os.getenv("WAVE_ACCESS_TOKEN", "").strip()
        if not token:
            raise WaveConfigError(
                "WAVE_ACCESS_TOKEN is not set. Create a token at "
                "https://developer.waveapps.com/ and put it in your .env file "
                "or MCP client config."
            )
        return cls(token, business_id=os.getenv("WAVE_BUSINESS_ID") or None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": "wave-mcp-server",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ----------------------------------------------------------------- requests

    async def execute(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        *,
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a GraphQL document and return its ``data`` payload.

        Retries on 429 and 5xx with exponential backoff. Raises
        :class:`WaveGraphQLError` when Wave reports document-level errors.
        """
        payload: Dict[str, Any] = {"query": query, "variables": _strip_none(variables or {})}
        if operation_name:
            payload["operationName"] = operation_name

        client = await self._get_client()
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = await client.post(self.endpoint, json=payload)
            except httpx.TimeoutException as exc:
                last_exc = exc
                await self._backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                raise WaveGraphQLError(f"Could not reach the Wave API: {exc}") from exc

            if response.status_code == 429:
                retry_after = _retry_after_seconds(response)
                if attempt == self.max_retries - 1:
                    raise WaveRateLimitError(
                        "Wave rate limit reached. Wait a moment and retry, or "
                        "request fewer records per call.",
                        retry_after=retry_after,
                    )
                await asyncio.sleep(retry_after or _backoff_seconds(attempt))
                continue

            if response.status_code in (401, 403):
                raise WaveAuthError(
                    "Wave rejected the access token (HTTP "
                    f"{response.status_code}). Tokens expire, so generate a fresh "
                    "one at https://developer.waveapps.com/ and update "
                    "WAVE_ACCESS_TOKEN."
                )

            if response.status_code >= 500:
                last_exc = WaveGraphQLError(
                    f"Wave returned HTTP {response.status_code}."
                )
                if attempt == self.max_retries - 1:
                    raise last_exc
                await self._backoff(attempt)
                continue

            if response.status_code != 200:
                raise WaveGraphQLError(
                    f"Wave returned HTTP {response.status_code}: {response.text[:500]}"
                )

            body = response.json()
            self._raise_for_graphql_errors(body)
            return body.get("data") or {}

        raise WaveGraphQLError(
            f"Wave API did not respond after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _raise_for_graphql_errors(body: Dict[str, Any]) -> None:
        errors = body.get("errors")
        if not errors:
            return

        codes = {
            (e.get("extensions") or {}).get("code", "") for e in errors
        }
        messages = "; ".join(e.get("message", "unknown error") for e in errors)

        if "UNAUTHENTICATED" in codes:
            raise WaveAuthError(
                "Wave rejected the access token: "
                f"{messages}. Generate a fresh token at "
                "https://developer.waveapps.com/ and update WAVE_ACCESS_TOKEN."
            )
        if "GRAPHQL_VALIDATION_FAILED" in codes:
            raise WaveGraphQLError(
                f"Wave rejected the query as invalid: {messages}. This is a bug "
                "in the MCP server, not in your input.",
                errors,
            )
        if "NOT_FOUND" in codes:
            raise WaveGraphQLError(
                f"Wave could not find the requested record: {messages}. Check "
                "that the ID belongs to the selected business.",
                errors,
            )
        raise WaveGraphQLError(f"Wave API error: {messages}", errors)

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(_backoff_seconds(attempt))

    # ---------------------------------------------------------------- mutations

    async def mutate(
        self,
        query: str,
        variables: Dict[str, Any],
        *,
        root_field: str,
    ) -> Dict[str, Any]:
        """Run a mutation and return its payload, raising if it did not succeed.

        Wave signals business-level failure with ``didSucceed: false`` and a
        populated ``inputErrors`` list while still returning HTTP 200 and no
        GraphQL ``errors``, so the payload has to be inspected explicitly.
        """
        data = await self.execute(query, variables)
        payload = data.get(root_field)
        if payload is None:
            raise WaveGraphQLError(
                f"Wave returned no payload for {root_field}. The record may not "
                "exist, or the token may lack permission for this business."
            )
        if payload.get("didSucceed") is False:
            raise WaveMutationError(root_field, payload.get("inputErrors"))
        return payload

    # --------------------------------------------------------------- pagination

    async def paginate(
        self,
        query: str,
        variables: Dict[str, Any],
        *,
        path: Iterable[str],
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        fetch_all: bool = False,
    ) -> Dict[str, Any]:
        """Fetch one page, or every page, of an offset-paginated connection.

        ``path`` locates the connection inside the response, e.g.
        ``("business", "invoices")``.

        Returns a dict with ``items`` plus pagination metadata
        (``page``, ``page_size``, ``total_count``, ``total_pages``,
        ``has_more``, ``next_page``).
        """
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        collected: List[Dict[str, Any]] = []
        current = page
        page_info: Dict[str, Any] = {}
        pages_walked = 0

        while True:
            data = await self.execute(
                query, {**variables, "page": current, "pageSize": page_size}
            )
            connection = _dig(data, path)
            if connection is None:
                raise WaveGraphQLError(
                    f"Wave returned no data at {'.'.join(path)}. Check that the "
                    "business ID is correct and the token can access it."
                )

            collected.extend(
                edge["node"]
                for edge in (connection.get("edges") or [])
                if edge and edge.get("node") is not None
            )
            page_info = connection.get("pageInfo") or {}
            pages_walked += 1

            total_pages = page_info.get("totalPages") or 1
            if not fetch_all or current >= total_pages or pages_walked >= MAX_PAGES:
                if fetch_all and pages_walked >= MAX_PAGES and current < total_pages:
                    logger.warning(
                        "Stopped paginating %s at %d pages (ceiling reached); "
                        "results are truncated.",
                        ".".join(path),
                        MAX_PAGES,
                    )
                break
            current += 1

        total_pages = page_info.get("totalPages") or 1
        last_page = page if fetch_all else current
        has_more = (not fetch_all) and current < total_pages

        return {
            "items": collected,
            "page": page,
            "page_size": page_size,
            "count": len(collected),
            "total_count": page_info.get("totalCount"),
            "total_pages": total_pages,
            "has_more": has_more,
            "next_page": (current + 1) if has_more else None,
            "fetched_all": fetch_all,
            "_last_page": last_page,
        }

    # ------------------------------------------------------------------ helpers

    def require_business_id(self, business_id: Optional[str] = None) -> str:
        """Resolve the business to operate on, preferring an explicit argument."""
        resolved = business_id or self.business_id
        if not resolved:
            raise WaveConfigError(
                "No business selected. Call wave_list_businesses to see the "
                "available IDs, then either pass business_id explicitly or call "
                "wave_set_default_business to set one for the session."
            )
        return resolved


def _dig(data: Dict[str, Any], path: Iterable[str]) -> Optional[Dict[str, Any]]:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if node is None:
            return None
    return node


def _strip_none(value: Any) -> Any:
    """Drop ``None`` values so Wave sees omitted fields rather than explicit nulls.

    This matters for patch mutations, where an explicit null would clear a
    field that the caller simply did not mention.
    """
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _backoff_seconds(attempt: int) -> float:
    return min(2.0 ** attempt, 8.0)


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
