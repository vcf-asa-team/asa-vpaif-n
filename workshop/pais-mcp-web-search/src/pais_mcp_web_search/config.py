from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecuritySettings


@dataclass(frozen=True)
class WebSearchConfig:
    timeout: float = 10.0
    verify_ssl: bool = True
    proxy: str | None = None
    user_agent: str = "pais-mcp-web-search/0.1 (+https://modelcontextprotocol.io)"
    default_num_results: int = 5
    max_num_results: int = 20
    default_fetch_max_chars: int = 5000
    max_fetch_chars: int = 20000
    github_token: str | None = None

    @classmethod
    def from_env(cls) -> "WebSearchConfig":
        verify_ssl = os.environ.get("WEB_SEARCH_VERIFY_SSL", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        return cls(
            timeout=float(os.environ.get("WEB_SEARCH_TIMEOUT_SECONDS", "10")),
            verify_ssl=verify_ssl,
            proxy=os.environ.get("WEB_SEARCH_PROXY") or None,
            user_agent=os.environ.get(
                "WEB_SEARCH_USER_AGENT", "pais-mcp-web-search/0.1 (+https://modelcontextprotocol.io)"
            ),
            default_num_results=int(os.environ.get("WEB_SEARCH_DEFAULT_NUM_RESULTS", "5")),
            max_num_results=int(os.environ.get("WEB_SEARCH_MAX_NUM_RESULTS", "20")),
            default_fetch_max_chars=int(os.environ.get("WEB_SEARCH_DEFAULT_FETCH_MAX_CHARS", "5000")),
            max_fetch_chars=int(os.environ.get("WEB_SEARCH_MAX_FETCH_CHARS", "20000")),
            github_token=os.environ.get("GITHUB_TOKEN") or None,
        )


def configure_logging() -> None:
    """Set up logging from WEB_SEARCH_LOG_LEVEL (default INFO; set DEBUG to see every
    scraped title/url and page-fetch preview). Safe to call more than once.

    The root logger stays at WARNING so DEBUG only surfaces this package's own
    detail, not the verbose internals of httpx/httpcore/h2/ddgs's HTTP client.
    """
    level_name = os.environ.get("WEB_SEARCH_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    logging.getLogger("pais_mcp_web_search").setLevel(level)


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
