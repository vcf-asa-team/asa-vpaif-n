# PAIS LLM Tracing Dashboard

`pais-llm-tracing-dashboard.json` is a Grafana dashboard built against a
**Tempo** datasource (TraceQL), for the LLM traces Private AI Services (PAIS)
sends via OpenTelemetry — the trace tree visible in Grafana's Trace view
(`pais-api-<uuid>` service → `InferenceGateway.chat_completions` →
`OpenAIProvider.chat_stream` spans) and in the Tempo search table.

This is **not** a Prometheus dashboard like the vLLM/llama.cpp ones in this
folder — PAIS's LLM traces aren't documented as feeding a span-metrics /
service-graph pipeline into Prometheus (see "What this assumes" below), so
every panel queries Tempo directly with TraceQL.

## Sections

- **Trace Explorer** — Recent Traces, Errored Traces, and Slow Traces
  (duration over a configurable threshold), as Tempo search tables — the
  same shape as the "Table - Traces" view in your screenshot.
- **Trace Detail** — paste a Trace ID into the `$trace_id` variable to render
  its full span waterfall inline, equivalent to opening it in Grafana's
  Trace view.
- **Span Explorer** — individual spans (not grouped by trace), with an
  `$extra_select` variable so you can pull specific span attributes in as
  extra columns via TraceQL's `select()`, e.g.
  `| select(span.gen_ai.request.model, span.gen_ai.usage.output_tokens)`.
- **Latency & Throughput (TraceQL metrics)** — span counts, p50/p95/p99
  duration stats, latency percentiles and span rate by span name, an
  error-vs-total rate comparison, and a duration histogram — all computed
  with TraceQL metrics functions (`rate()`, `quantile_over_time()`,
  `count_over_time()`, `histogram_over_time()`) run directly against Tempo.

## What this assumes — verify before relying on it

- **Service name is a regex, not an exact match.** The traces shown in your
  screenshot have a service name like `pais-api-9a6e8db7-2f37-46a9-ac00-...`
  — a UUID suffix, not a fixed name — so every query filters with
  `resource.service.name=~"$service_name"` (default `pais-api.*`) rather than
  `=`. If your deployment uses a stable service name instead, tighten this to
  an exact match.
- **TraceQL metrics (the whole "Latency & Throughput" row) require Tempo's
  `metrics-generator` `local-blocks` processor to be enabled.** This wasn't
  confirmed against your deployment — the PAIS tracing docs describe spans
  and attributes but don't mention a metrics-generator. If these panels show
  "No data" while the Trace Explorer tables above them work fine, that's very
  likely why — either enable `local-blocks` in Tempo's config, or delete this
  row and rely on the trace-table panels alone.
- **Span attribute names for model/token data are unconfirmed.** The PAIS
  tracing docs confirm a `com.vmware.pais.*` attribute namespace for agent
  operation and knowledge-base indexing traces (`agent_id`, `index_id`,
  `indexing_id`, `document_id`, `knowledge_base_id`) but don't document
  equivalent attributes for LLM inference spans (model name, input/output
  token counts, etc.). Open a single `OpenAIProvider.chat_stream` or
  `InferenceGateway.chat_completions` span in Grafana's Trace view and check
  its attribute list for the real key names, then use the Span Explorer
  panel's `$extra_select` variable to surface them — don't assume
  OpenTelemetry GenAI semantic convention names (`gen_ai.request.model`,
  etc.) apply here without checking.
- **"Slow Traces" is not duration-sorted.** Tempo's search API returns
  results time-ordered, not duration-ordered — there's no TraceQL "order by."
  The panel still filters by `duration > $min_duration`, but click the
  Duration column header in the table to sort what comes back.
- **The "Spans (window, approx.)" stat is a bucketed approximation**, not an
  exact count — it sums `count_over_time()`'s per-step buckets across the
  dashboard time range, same caveat as the vLLM dashboard's rate-window
  bucketing.
- **Table format (`tableType: "traces"` / `"spans"`) assumes a Grafana
  version new enough to have that Tempo query option** (≈10.3+). If a table
  panel renders raw/unexpected columns instead of a trace or span list after
  import, open the panel's query editor and set the table format manually.
- **Trace Detail lookup** sets the query to the bare `$trace_id` value
  (no TraceQL braces), relying on Grafana's Tempo datasource treating a
  hex string as a direct trace-ID lookup rather than a search. If it doesn't
  render, paste the ID into Explore against the Tempo datasource instead.

## Template variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `datasource` | Tempo datasource picker | (select on import) |
| `service_name` | Regex filter on `resource.service.name` | `pais-api.*` |
| `span_name` | Regex filter on span `name` | `.*` |
| `min_duration` | Threshold for the Slow Traces panel | `3s` |
| `extra_select` | Raw TraceQL appended to the Span Explorer query | *(empty)* |
| `trace_id` | Trace ID rendered by the Trace Detail panel | *(empty)* |

## Import

Grafana UI → Dashboards → New → Import → upload
`pais-llm-tracing-dashboard.json` → select your Tempo datasource for the
`datasource` variable.
