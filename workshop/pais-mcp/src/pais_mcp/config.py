from __future__ import annotations

import os
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecuritySettings


@dataclass(frozen=True)
class PAISConfig:
    base_url: str
    api_token: str
    verify_ssl: bool = True
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "PAISConfig":
        base_url = os.environ.get("PAIS_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "PAIS_BASE_URL environment variable is required, e.g. https://pais.pse.lab"
            )
        api_token = os.environ.get("PAIS_API_TOKEN")
        if not api_token:
            raise RuntimeError("PAIS_API_TOKEN environment variable is required")

        verify_ssl = os.environ.get("PAIS_VERIFY_SSL", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        timeout = float(os.environ.get("PAIS_TIMEOUT_SECONDS", "30"))

        return cls(
            base_url=base_url.rstrip("/"),
            api_token=api_token,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def transport_security_from_env() -> TransportSecuritySettings:
    """Build the Streamable HTTP Host/Origin allowlist from the environment.

    The SDK rejects any request whose Host header isn't localhost unless told
    otherwise (DNS-rebinding protection) - that's what a "421 Misdirected
    Request" / "Invalid Host header" log line means. Since this server is
    typically reached by MCP clients over a private lab network rather than
    from a browser, protection is off by default here; set
    MCP_ENABLE_DNS_REBINDING_PROTECTION=true plus MCP_ALLOWED_HOSTS /
    MCP_ALLOWED_ORIGINS to lock it down.
    """
    enable_protection = os.environ.get(
        "MCP_ENABLE_DNS_REBINDING_PROTECTION", "false"
    ).strip().lower() in ("1", "true", "yes")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enable_protection,
        allowed_hosts=_split_csv(os.environ.get("MCP_ALLOWED_HOSTS")),
        allowed_origins=_split_csv(os.environ.get("MCP_ALLOWED_ORIGINS")),
    )
