from __future__ import annotations

from typing import Any

import httpx

from .config import PAISConfig


class PAISAPIError(Exception):
    """Raised when the PAIS API returns an error or cannot be reached."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class PAISClient:
    """Thin async HTTP client for the Private AI Services (PAIS) control-plane API.

    Only generic list/delete verbs live here; per-resource paths live in
    resources.py so adding a new PAIS endpoint never requires touching this file.
    """

    def __init__(self, config: PAISConfig) -> None:
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

    async def list_resource(
        self, path: str, *, limit: int | None = None, after: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        return await self._request("GET", path, params=params or None)

    async def delete_resource(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise PAISAPIError(0, f"Failed to reach PAIS API at {path}: {exc}") from exc

        if response.status_code >= 400:
            raise PAISAPIError(response.status_code, _extract_error_detail(response))

        if not response.content:
            return {}
        return response.json()


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("message", "detail", "error"):
            if key in body:
                return str(body[key])
    return str(body)
