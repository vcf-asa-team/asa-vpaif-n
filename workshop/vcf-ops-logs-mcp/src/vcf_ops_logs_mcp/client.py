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


_API_TOKEN_GRANT_TYPE = "urn:custom:vcf:params:oauth:grant-type:api-token"


class VCFOpsLogsClient:
    """Thin async HTTP client for the VCF Operations for Logs (Aria Operations
    for Logs / Log Insight) query API, as surfaced through VCF Operations 9.1's
    unified gateway.

    Auth is two-step, per VCF 9.1's fleet-wide API token model: the long-lived
    api_token is first exchanged for a short-lived bearer access token via an
    OAuth2-style "api-token" grant against config.token_endpoint_url (POST,
    form-urlencoded, on a *different host* than base_url — the SSO/identity
    broker, not the Operations-for-Logs appliance). That access token is then
    sent as "Authorization: Bearer <token>" on actual queries, cached, and
    re-exchanged automatically on a 401 (it's short-lived — ~30 minutes).

    Queries use the Log Insight REST API "constraint path" convention:
    GET <events_path>/<field>/<op>/<value>/<field>/<op>/<value>/... Constraints
    are ANDed together server-side — there's no OR, so matching several error
    terms means one query per term (see monitor.py / the count_errors tool).
    """

    def __init__(self, config: VCFOpsLogsConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Accept": "application/json"},
            verify=config.verify_ssl,
            timeout=config.timeout,
        )
        self._access_token: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def query_events(
        self, constraints: list[tuple[str, str, str]], *, limit: int | None = None
    ) -> dict[str, Any]:
        path = self._config.events_path + _constraint_path(constraints)
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        return await self._authed_request("GET", path, params=params or None)

    async def list_fields(self) -> dict[str, Any]:
        return await self._authed_request("GET", self._config.fields_path)

    async def _authed_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._ensure_access_token()
        response = await self._send(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if response.status_code == 401:
            # Access token may have expired (~30 min TTL); re-exchange once and retry.
            self._access_token = None
            token = await self._ensure_access_token()
            response = await self._send(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        return _parse_response(response)

    async def _ensure_access_token(self) -> str:
        if self._access_token is not None:
            return self._access_token

        response = await self._send(
            "POST",
            self._config.token_endpoint_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": _API_TOKEN_GRANT_TYPE,
                "api_token": self._config.api_token,
            },
        )
        body = _parse_response(response)
        token = _extract_token(body)
        if not token:
            raise VCFOpsLogsAPIError(
                response.status_code,
                f"Token exchange at {self._config.token_endpoint_url} did not return a "
                f"recognizable access_token field; response was: {body!r}",
            )
        self._access_token = token
        return token

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise VCFOpsLogsAPIError(
                0, f"Failed to reach VCF Operations for Logs API at {path}: {exc}"
            ) from exc


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise VCFOpsLogsAPIError(response.status_code, _extract_error_detail(response))
    if not response.content:
        return {}
    return response.json()


def _extract_token(body: dict[str, Any]) -> str | None:
    for key in ("token", "access_token", "jwt", "accessToken", "value"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None


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
