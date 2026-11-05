from __future__ import annotations

import os
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecuritySettings


@dataclass(frozen=True)
class VCFOpsLogsConfig:
    base_url: str
    api_token: str
    # VCF 9.1's fleet-wide API token isn't used directly on calls: it's
    # exchanged for a short-lived bearer access token via an OAuth2-style
    # grant against the OIDC token endpoint (a *different host* from
    # base_url — copy the "Token Endpoint URL" shown on VCF Operations'
    # "Your API Tokens" page, e.g. https://<sso-fqdn>/acs/t/CUSTOMER/token).
    token_endpoint_url: str
    verify_ssl: bool = True
    timeout: float = 30.0
    # VCF 9.1.1 "Log Management API": POST /api/v2/logs/search (synchronous,
    # Elasticsearch-style query DSL) and GET /api/v2/fields/extractedFields.
    search_path: str = "/api/v2/logs/search"
    fields_path: str = "/api/v2/fields/extractedFields"

    @classmethod
    def from_env(cls) -> "VCFOpsLogsConfig":
        base_url = os.environ.get("VCF_OPS_LOGS_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "VCF_OPS_LOGS_BASE_URL environment variable is required, e.g. https://ops-logs.vcf.lab"
            )
        api_token = os.environ.get("VCF_OPS_LOGS_API_TOKEN")
        if not api_token:
            raise RuntimeError("VCF_OPS_LOGS_API_TOKEN environment variable is required")
        token_endpoint_url = os.environ.get("VCF_OPS_LOGS_TOKEN_ENDPOINT_URL")
        if not token_endpoint_url:
            raise RuntimeError(
                "VCF_OPS_LOGS_TOKEN_ENDPOINT_URL environment variable is required, e.g. "
                "https://<sso-fqdn>/acs/t/CUSTOMER/token (the 'Token Endpoint URL' shown "
                "on VCF Operations' 'Your API Tokens' page)"
            )

        verify_ssl = os.environ.get("VCF_OPS_LOGS_VERIFY_SSL", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        timeout = float(os.environ.get("VCF_OPS_LOGS_TIMEOUT_SECONDS", "30"))

        return cls(
            base_url=base_url.rstrip("/"),
            api_token=api_token,
            token_endpoint_url=token_endpoint_url,
            verify_ssl=verify_ssl,
            timeout=timeout,
            search_path=os.environ.get("VCF_OPS_LOGS_SEARCH_PATH", cls.search_path),
            fields_path=os.environ.get("VCF_OPS_LOGS_FIELDS_PATH", cls.fields_path),
        )


@dataclass(frozen=True)
class MonitorConfig:
    enabled: bool = True
    interval_seconds: float = 60.0
    initial_lookback_minutes: float = 5.0
    error_terms: tuple[str, ...] = ("ERROR", "Exception", "CRITICAL", "FATAL")
    max_findings: int = 200
    sample_size: int = 3

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        enabled = os.environ.get("MONITOR_ENABLED", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        terms = _split_csv(os.environ.get("MONITOR_ERROR_TERMS"))
        return cls(
            enabled=enabled,
            interval_seconds=float(os.environ.get("MONITOR_INTERVAL_SECONDS", "60")),
            initial_lookback_minutes=float(os.environ.get("MONITOR_INITIAL_LOOKBACK_MINUTES", "5")),
            error_terms=tuple(terms) if terms else cls.error_terms,
            max_findings=int(os.environ.get("MONITOR_MAX_FINDINGS", "200")),
            sample_size=int(os.environ.get("MONITOR_SAMPLE_SIZE", "3")),
        )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def transport_security_from_env() -> TransportSecuritySettings:
    """Build the Streamable HTTP Host/Origin allowlist from the environment.

    Off by default (see pais-mcp's README for the full "421 Misdirected
    Request" explanation) since this server is typically reached over a
    private lab network rather than from a browser.
    """
    enable_protection = os.environ.get(
        "MCP_ENABLE_DNS_REBINDING_PROTECTION", "false"
    ).strip().lower() in ("1", "true", "yes")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enable_protection,
        allowed_hosts=_split_csv(os.environ.get("MCP_ALLOWED_HOSTS")),
        allowed_origins=_split_csv(os.environ.get("MCP_ALLOWED_ORIGINS")),
    )
