from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .client import VCFOpsLogsAPIError, VCFOpsLogsClient
from .config import MonitorConfig


@dataclass
class Finding:
    detected_at_ms: int
    term: str
    window_start_ms: int
    window_end_ms: int
    match_count: int
    samples: list[str]


class Monitor:
    """Background task that periodically polls VCF Operations for Logs for
    configured error terms and keeps a rolling buffer of what it finds.

    Each poll only looks at the window since the end of the previous poll,
    so the same event is never counted twice across polls.
    """

    def __init__(self, client: VCFOpsLogsClient, config: MonitorConfig) -> None:
        self._client = client
        self._config = config
        self._findings: deque[Finding] = deque(maxlen=config.max_findings)
        self._task: asyncio.Task | None = None
        self._last_poll_ms = int(time.time() * 1000) - int(
            config.initial_lookback_minutes * 60_000
        )

    @property
    def error_terms(self) -> list[str]:
        return list(self._config.error_terms)

    def recent_findings(self, limit: int = 20) -> list[dict[str, Any]]:
        items = list(self._findings)[-limit:]
        return [
            {
                "detected_at": _iso(f.detected_at_ms),
                "term": f.term,
                "window_start": _iso(f.window_start_ms),
                "window_end": _iso(f.window_end_ms),
                "match_count": f.match_count,
                "samples": f.samples,
            }
            for f in reversed(items)
        ]

    def start(self) -> None:
        if not self._config.enabled:
            print("[vcf-ops-logs-mcp] monitor disabled (MONITOR_ENABLED=false)")
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        print(
            f"[vcf-ops-logs-mcp] monitor started: terms={self.error_terms} "
            f"interval={self._config.interval_seconds}s"
        )
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[vcf-ops-logs-mcp] monitor poll failed: {exc}")
            await asyncio.sleep(self._config.interval_seconds)

    async def _poll_once(self) -> None:
        window_start_ms = self._last_poll_ms
        window_end_ms = int(time.time() * 1000)
        if window_end_ms <= window_start_ms:
            return

        for term in self._config.error_terms:
            constraints = [
                ("text", "CONTAINS", term),
                ("timestamp", "GT", str(window_start_ms)),
                ("timestamp", "LT", str(window_end_ms)),
            ]
            try:
                result = await self._client.query_events(
                    constraints, limit=self._config.query_limit
                )
            except VCFOpsLogsAPIError as exc:
                print(
                    f"[vcf-ops-logs-mcp] monitor query for {term!r} failed "
                    f"({exc.status_code}): {exc.message}"
                )
                continue

            events = result.get("events") or []
            if not events:
                continue

            samples = [str(e.get("text", ""))[:200] for e in events[: self._config.sample_size]]
            self._findings.append(
                Finding(
                    detected_at_ms=int(time.time() * 1000),
                    term=term,
                    window_start_ms=window_start_ms,
                    window_end_ms=window_end_ms,
                    match_count=len(events),
                    samples=samples,
                )
            )
            print(
                f"[vcf-ops-logs-mcp] monitor: {len(events)} new match(es) for {term!r} "
                f"between {_iso(window_start_ms)} and {_iso(window_end_ms)}"
            )

        self._last_poll_ms = window_end_ms


def _iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))
