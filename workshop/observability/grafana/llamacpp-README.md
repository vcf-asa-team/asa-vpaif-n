# llama.cpp CPU Inference Monitoring Dashboard

`llamacpp-inference-dashboard.json` is a Grafana dashboard for llama.cpp
server's built-in Prometheus metrics (`llama-server --metrics`, scraped at
`/metrics`).

**Verify the metric names before relying on this.** Unlike vLLM, llama.cpp's
metrics set is based on recollection of the stock `llamacpp:*` names
(`requests_processing`, `requests_deferred`, `kv_cache_usage_ratio`,
`kv_cache_tokens`, `prompt_tokens_total`, `tokens_predicted_total`,
`n_decode_total`, `n_busy_slots_per_decode`) and hasn't been checked against
a live `/metrics` scrape. Run `curl http://<host>:<port>/metrics | grep llamacpp`
first and fix any panel whose name doesn't match — the vLLM dashboard in this
same folder needed three rounds of exactly this fix.

## Why this looks different from the vLLM dashboard

llama.cpp's server does **not** expose per-request latency histograms — no
TTFT, no TPOT, no end-to-end latency percentiles. Its metrics are aggregate
counters/gauges only: token throughput, queue depth, KV cache usage, and
decode/batching stats. So this dashboard can't reproduce the vLLM dashboard's
"Latency (SLOs)" row with p50/p95/p99 — that data simply isn't emitted. If
you need real per-request latency percentiles for a llama.cpp deployment,
you'd have to add your own instrumentation (e.g. a reverse proxy in front of
llama-server with a request-duration histogram).

## Sections

- **Overview** — requests processing/deferred, KV cache usage %, KV cache
  tokens, prompt/generation throughput. "Deferred" is the CPU-server
  equivalent of a queue: on a fixed-slot server (`--parallel`), any sustained
  nonzero value means you're out of capacity.
- **Throughput** — prompt vs. generation tokens/sec.
- **Batching & Decode Efficiency** — decode calls/sec, and average busy
  slots per decode call (how well concurrent requests are batching together
  — the main lever for CPU throughput via `--parallel`/`--cont-batching`).
- **Queue** — processing vs. deferred requests over time.
- **KV Cache** — usage % and token count over time.
- **Host CPU & Memory** (requires `node_exporter`) — not part of llama.cpp's
  own metrics. llama.cpp doesn't report CPU utilization itself, so this is
  the only way to see whether the host is actually CPU-bound. **Not**
  filtered by the dashboard's `$instance` variable — node_exporter's
  `instance` label is its own scrape target (typically `:9100`) and won't
  match llama.cpp's scrape target address. Add your own label matcher if you
  need to isolate a specific host and your Prometheus setup doesn't already
  give node_exporter and llama.cpp a shared correlating label.

## Rate window

Like the vLLM dashboard, this uses a fixed `[5m]` window for all
`rate()` calls instead of Grafana's `$__rate_interval` macro, since that
macro depends on the Prometheus datasource's configured scrape-interval
metadata matching reality — see the vLLM dashboard's README for the full
story of what breaks when it doesn't.

## Import

Grafana UI → Dashboards → New → Import → upload
`llamacpp-inference-dashboard.json` → select your Prometheus datasource for
the `datasource` variable.
