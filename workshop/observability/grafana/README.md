# vLLM Inference Monitoring Dashboard

`vllm-inference-dashboard.json` is a Grafana dashboard for vLLM's built-in
Prometheus metrics (`/metrics` endpoint), following the latency/throughput/
saturation/errors model commonly used for LLM-serving SLOs:

- **Overview** — running/waiting requests, GPU KV cache usage, completed
  requests/sec, and error rate. Thresholds flag queueing (waiting > 5) and
  cache pressure (> 80% / 95%) before they turn into user-facing latency.
- **Latency (SLOs)** — Time to First Token (TTFT), Time per Output Token
  (TPOT), end-to-end request latency, and queue time, each as p50/p95/p99.
  These are the four numbers that map to what a user actually feels.
- **Throughput** — prompt vs. generation tokens/sec, and completed
  requests/sec split by finish reason (`stop` / `length` / `abort` / `error`).
- **Scheduler & Cache** — running/waiting over time, GPU KV cache usage over
  time, and preemption rate (should be ~0 under healthy load).
- **Request Token Distributions** — heatmaps of prompt and generation token
  counts, useful for right-sizing `max_model_len` and batch/throughput tuning.
- **GPU Hardware** — GPU utilization, memory used, SM clock, memory clock,
  and memory bandwidth utilization, from `dcgm-exporter`. Not part of
  vLLM's own metrics; joined to the model name via Kubernetes node labels
  (see below).

## Requirements

- vLLM started with metrics enabled (default) and scraped by Prometheus at
  its `/metrics` endpoint.
- Prometheus datasource configured in Grafana.

## Metric name notes

Built against the metric names shipped since vLLM ~0.5 (`vllm:num_requests_running`,
`vllm:time_to_first_token_seconds`, `vllm:request_queue_time_seconds`, etc., all
labeled with `model_name`). If you're on an older or newer vLLM release and a
panel shows "No data," check `curl <vllm-host>:<port>/metrics` for the exact
metric names/labels in your version and adjust the panel query accordingly —
vLLM has renamed and added metrics across releases.

This dashboard assumes a **V1-engine** vLLM build: it has no "Requests Swapped"
or "CPU KV Cache" panels because `vllm:num_requests_swapped` and
`vllm:cpu_cache_usage_perc` don't exist once CPU swapping was removed. If
you're on an older V0 engine that still supports swapping, those metrics will
exist and are worth adding back.

The TPOT (Time per Output Token) panel uses
`vllm:request_time_per_output_token_seconds_bucket`, not the more commonly
documented `vllm:time_per_output_token_seconds_bucket` — this build renamed
it into the same `request_*` per-request-histogram family as
`request_prompt_tokens`/`request_generation_tokens`/`request_queue_time_seconds`.
If a panel comes up empty on a different vLLM build, check `/metrics` for the
exact name rather than assuming — this deployment has diverged from upstream
naming in at least three places now (`kv_cache_usage_perc`, the missing
swap/CPU-cache metrics, and this one).

## Rate window: fixed `[5m]`, not `$__rate_interval`

Every `rate()`/`increase()` query uses a fixed `[5m]` window instead of
Grafana's `$__rate_interval` macro. `$__rate_interval` derives its lookback
from the Prometheus datasource's configured "Scrape interval" (Connections →
Data sources → Prometheus → Interval behaviour) — if that setting doesn't
match what Prometheus is actually scraping at, the computed window can end up
too short to ever contain two samples, and every `rate()`-based panel
(latency histograms, token throughput, request throughput by finish reason)
silently returns "No data" while plain gauges keep working fine. That's
exactly what happened during development here, so the dashboard was switched
to a fixed window to avoid depending on that datasource metadata at all.

If you fix the datasource's scrape interval setting and want faster-reacting
panels, you can tighten `[5m]` to something like `[2m]` or `[1m]` — just make
sure it stays comfortably above your actual Prometheus scrape interval (at
least 4x) or you'll reproduce the same "No data" symptom.

## GPU Hardware row: joining DCGM to the model name via Kubernetes node labels

This is a Kubernetes deployment: GPU nodes carry a custom node label,
`pais.vmware.com/modelendpoint-name` (and `-engine`), identifying which model
is scheduled on that node. `kube-state-metrics` exposes all node labels as
`kube_node_labels{label_pais_vmware_com_modelendpoint_name="...", ...} 1` —
one series per node, value always `1`. `dcgm-exporter`'s own `Hostname`
label is the literal Kubernetes node name, which is the correct join key
against `kube_node_labels`'s `node` label — **not** vLLM's `instance` label,
which is a pod/service IP:port that has no fixed relationship to the
underlying node.

Two new template variables drive this, independent of `$model_name`/
`$instance`: `node` (from DCGM's own `Hostname` label) and `gpu` (from
DCGM's `gpu` index label). Every GPU Hardware panel uses the same pattern:

```
max(
  label_replace(DCGM_FI_DEV_GPU_UTIL{Hostname=~"$node", gpu=~"$gpu"}, "node", "$1", "Hostname", "(.*)")
) by (node, gpu)
* on(node) group_left(label_pais_vmware_com_modelendpoint_name, label_pais_vmware_com_modelendpoint_engine)
kube_node_labels{label_pais_vmware_com_modelendpoint_name=~"$model_name"}
```

- `label_replace(..., "node", "$1", "Hostname", "(.*)")` copies `Hostname`
  into a `node` label so it can be joined against `kube_node_labels`'s
  `node` label with `on(node)`.
- The outer `max(...) by (node, gpu)` collapses any duplicate series per
  node/gpu before the join (defends against relabeling producing more than
  one series per device).
- `kube_node_labels` already reports a stable `1` per node, so multiplying
  by it (via `group_left`) only *attaches* the
  `label_pais_vmware_com_modelendpoint_name`/`-engine` labels — it doesn't
  alter DCGM's actual value, the same safety property as the old IP-based
  join attempt, just without needing an extra `group()` wrapper since
  `kube_node_labels` is already one-row-per-node.
- `kube_node_labels{label_pais_vmware_com_modelendpoint_name=~"$model_name"}`
  reuses the dashboard's existing `$model_name` variable as the filter —
  vLLM's `model_name` label value and the node label's value are the same
  string (e.g. `gemma-4-26b-it`) in this deployment.

Legend format: `{{label_pais_vmware_com_modelendpoint_name}}: {{node}} : {{gpu}}`.

A node not labeled with a `model_endpoint_name` matching the current
`$model_name` selection drops out of the join entirely, same caveat as
before — it won't show up unlabeled, it just won't show up.

### Clock panels: unit left as a plain number

`DCGM_FI_DEV_SM_CLOCK` and `DCGM_FI_DEV_MEM_CLOCK` report MHz as a raw
number. Rather than guess at Grafana's Hz-based unit ID (risking a
1,000,000x display error if it assumes the raw value is in Hz, not MHz),
these two panels use `unit: short` and put "(MHz)" in the title instead.

## Import

Grafana UI → Dashboards → New → Import → upload `vllm-inference-dashboard.json`
→ select your Prometheus datasource for the `datasource` variable.
