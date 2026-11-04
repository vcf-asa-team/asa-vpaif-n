from __future__ import annotations

import time
import urllib.parse
from typing import Any

import httpx

from .config import VCFOpsLogsConfig


class VCFOpsLogsAPIError(Exception):
    """Raised when the VCF Operations for Logs API returns an error or cannot be reached."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_ALLOWED_OPERATORS = {
    "CONTAINS",
    "NOT_CONTAINS",
    "EQ",
    "NOT_EQ",
    "GT",
    "GTE",
    "LT",
    "LTE",
}


class VCFOpsLogsClient:
    """Thin async HTTP client for the VCF Operations for Logs (Aria Operations
    for Logs / Log Insight) query API.

    Queries use the long-standing Log Insight REST API v1 "constraint path"
    convention: GET /api/v1/events/<field>/<op>/<value>/<field>/<op>/<value>/...
    Constraints are ANDed together server-side — there's no OR, so matching
    several error terms means one query per term (see monitor.py / the
    count_errors tool). If your instance exposes a different query shape
    (e.g. a newer aggregation API), this is the one place to adapt.
    """

    def __init__(self, config: VCFOpsLogsConfig) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_token}",
                "Accept": "application/json",
            },
            verify=config.verify_ssl,
            timeout=config.timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def query_events(
        self, constraints: list[tuple[str, str, str]], *, limit: int | None = None
    ) -> dict[str, Any]:
        path = "/api/v1/events" + _constraint_path(constraints)
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        return await self._request("GET", path, params=params or None)

    async def list_fields(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/fields")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise VCFOpsLogsAPIError(
                0, f"Failed to reach VCF Operations for Logs API at {path}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise VCFOpsLogsAPIError(response.status_code, _extract_error_detail(response))

        if not response.content:
            return {}
        return response.json()


def _constraint_path(constraints: list[tuple[str, str, str]]) -> str:
    segments: list[str] = []
    for field, operator, value in constraints:
        op = operator.upper()
        if op not in _ALLOWED_OPERATORS:
            raise ValueError(
                f"Unsupported operator {operator!r}; must be one of {sorted(_ALLOWED_OPERATORS)}"
            )
        segments.extend(
            [
                urllib.parse.quote(field, safe=""),
                op,
                urllib.parse.quote(str(value), safe=""),
            ]
        )
    return "/" + "/".join(segments) if segments else ""


def time_range_constraints(
    since_minutes: float, *, until_minutes_ago: float = 0.0
) -> list[tuple[str, str, str]]:
    """Build timestamp GT/LT constraints covering [now - since_minutes, now - until_minutes_ago]."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(since_minutes * 60_000)
    end_ms = now_ms - int(until_minutes_ago * 60_000)
    return [("timestamp", "GT", str(start_ms)), ("timestamp", "LT", str(end_ms))]


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("message", "detail", "error", "errorMessage"):
            if key in body:
                return str(body[key])
    return str(body)
