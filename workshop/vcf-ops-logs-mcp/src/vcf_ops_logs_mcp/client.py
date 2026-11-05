from __future__ import annotations

import time
from typing import Any

import httpx

from .config import VCFOpsLogsConfig


class VCFOpsLogsAPIError(Exception):
    """Raised when the VCF Operations for Logs API returns an error or cannot be reached."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_API_TOKEN_GRANT_TYPE = "urn:custom:vcf:params:oauth:grant-type:api-token"


class VCFOpsLogsClient:
    """Thin async HTTP client for VCF 9.1.1's Log Management API
    (POST /api/v2/logs/search), an Elasticsearch-style query DSL — a single
    synchronous call, no submit/poll cycle.

    Auth is three-step:
    1. The long-lived api_token is exchanged for a short-lived fleet-wide
       bearer access token via an OAuth2-style "api-token" grant against
       config.token_endpoint_url (POST, form-urlencoded, on a *different
       host* than base_url — the SSO/identity broker).
    2. That access token authenticates a second exchange, scoped to the
       "ops-li" (Operations for Logs) component: POST
       config.suite_api_exchange_path with "Authorization: Bearer
       <access_token>" and body {"serviceKeys": [config.ops_li_service_key]},
       returning a component JWT. The Log Management API is gated on this
       JWT specifically — without it, calls 403 with "This API endpoint is
       not available on this port" even though the fleet access token alone
       authenticates fine.
    3. Both tokens are sent on every actual log query: "Authorization:
       Bearer <access_token>" and "X-JWT-Token: <ops_li_jwt>". Both are
       cached and re-exchanged once automatically on a 401.

    A query's `query` field is one Elasticsearch-DSL fragment — bool/match_phrase
    /term/terms/range/exists/match_all/prefix/regexp, e.g.
    {"match_phrase": {"originalText": "error"}} — see build_time_range_query
    and the search_logs/query_logs tools in server.py for how time bounding
    is ANDed in via a bool.must.
    """

    def __init__(self, config: VCFOpsLogsConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            verify=config.verify_ssl,
            timeout=config.timeout,
        )
        self._access_token: str | None = None
        self._ops_li_jwt: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: dict[str, Any], *, size: int | None = None) -> dict[str, Any]:
        """POST a QueryRequest to the log search endpoint and return the raw QueryResponse."""
        body: dict[str, Any] = {"query": query, "trackTotalHits": True}
        if size is not None:
            body["size"] = size
        result = await self._authed_request("POST", self._config.search_path, json=body)

        if result.get("failureMessage"):
            raise VCFOpsLogsAPIError(
                0,
                f"Log search failed ({result.get('failureReason')}): {result['failureMessage']}",
            )
        return result

    async def list_fields(self) -> dict[str, Any]:
        return await self._authed_request("GET", self._config.fields_path)

    async def _authed_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        access_token, ops_li_jwt = await self._ensure_tokens()
        response = await self._send(method, path, headers=_auth_headers(access_token, ops_li_jwt), **kwargs)
        if response.status_code == 401:
            # Either token may have expired; re-exchange both once and retry.
            self._access_token = None
            self._ops_li_jwt = None
            access_token, ops_li_jwt = await self._ensure_tokens()
            response = await self._send(
                method, path, headers=_auth_headers(access_token, ops_li_jwt), **kwargs
            )
        return _parse_response(response)

    async def _ensure_tokens(self) -> tuple[str, str]:
        access_token = await self._ensure_access_token()
        ops_li_jwt = await self._ensure_ops_li_jwt(access_token)
        return access_token, ops_li_jwt

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

    async def _ensure_ops_li_jwt(self, access_token: str) -> str:
        if self._ops_li_jwt is not None:
            return self._ops_li_jwt

        url = self._config.suite_api_base_url + self._config.suite_api_exchange_path
        response = await self._send(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"serviceKeys": [self._config.ops_li_service_key]},
        )
        body = _parse_response(response)
        jwt = _extract_token(body)
        if not jwt:
            raise VCFOpsLogsAPIError(
                response.status_code,
                f"ops-li token exchange at {url} did not return a recognizable token "
                f"field; response was: {body!r}",
            )
        self._ops_li_jwt = jwt
        return jwt

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise VCFOpsLogsAPIError(
                0, f"Failed to reach VCF Operations for Logs API at {path}: {exc}"
            ) from exc


def _auth_headers(access_token: str, ops_li_jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "X-JWT-Token": ops_li_jwt}


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    # >=300, not >=400: a redirect here means the wrong host/path (e.g. to a
    # login page), not success — none of these APIs legitimately redirect,
    # and silently following one into an HTML body then crashes on .json().
    if response.status_code >= 300:
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


def _extract_error_detail(response: httpx.Response) -> str:
    if 300 <= response.status_code < 400:
        location = response.headers.get("location")
        if location:
            return f"redirected to {location} — likely the wrong host/path for this call"
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("message", "detail", "error", "errorMessage"):
            if key in body:
                return str(body[key])
    return str(body)


# Confirmed field names (VCF 9.1.1 Log Management API data structures):
# MessageContent.originalText holds the raw log line; MessageContent.logTimestamp
# is the log's own epoch-millisecond timestamp (vs. ingestTimestamp, when it was
# received). RangeQueryValue's gte/gt/lte/lt are typed as strings, not numbers.
TEXT_FIELD = "originalText"
TIMESTAMP_FIELD = "logTimestamp"


def time_range_ms(since_minutes: float, *, until_minutes_ago: float = 0.0) -> tuple[int, int]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(since_minutes * 60_000)
    end_ms = now_ms - int(until_minutes_ago * 60_000)
    return start_ms, end_ms


def build_time_range_query(start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"range": {TIMESTAMP_FIELD: {"gte": str(start_ms), "lte": str(end_ms)}}}


def with_time_range(query: dict[str, Any], start_ms: int, end_ms: int) -> dict[str, Any]:
    """AND an arbitrary query DSL fragment with a time-range constraint."""
    return {"bool": {"must": [query, build_time_range_query(start_ms, end_ms)]}}


def match_phrase_query(field: str, value: str) -> dict[str, Any]:
    return {"match_phrase": {field: value}}


def extract_records(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a QueryResponse's hits down to their msgContent maps (originalText,
    logTimestamp, ingestTimestamp, incomingAddress, fields — the actual log data).
    """
    hits = (response.get("events") or {}).get("hits") or []
    return [h.get("msgContent", h) if isinstance(h, dict) else h for h in hits]


def total_matched(response: dict[str, Any]) -> int:
    total = (response.get("events") or {}).get("total")
    return total if isinstance(total, int) else 0
