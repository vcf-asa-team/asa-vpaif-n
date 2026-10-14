# Advanced Services Workshop AI Kubernetes Cluster & NVIDIA Blueprints

This directory contains VCF Automation Cloud Templates for GPU-accelerated AI workloads on
vSphere Supervisor: a two-phase Kubernetes + NVIDIA Run:ai deployment, and a standalone GPU VM
blueprint for running an NVIDIA NIM container directly.

## Blueprints

| File | Purpose |
|---|---|
| `AI Kubernetes cluster with NVIDIA Run-ai Control Plane.yaml` | **Phase 1** of the Run:ai setup. Creates a Kubernetes cluster (control-plane + general-purpose worker pool, no GPU) and installs a self-hosted NVIDIA Run:ai **control plane**. |
| `AI Kubernetes cluster with NVIDIA Run-ai.yaml` | **Phase 2** of the Run:ai setup. Creates its own GPU-enabled Kubernetes cluster, installs the GPU Operator, and registers that cluster as a Run:ai **compute cluster** against an existing control plane (from Phase 1). Optionally installs Knative Serving + LeaderWorkerSet so the Run:ai "Inference" workload type works. |
| `NVIDIA Nemotron-OCR.yaml` | Standalone single-VM blueprint (no Kubernetes). Deploys a GPU-enabled Deep Learning VM that runs NVIDIA's Nemotron-OCR NIM container directly via Docker. See [NVIDIA Nemotron-OCR VM](#nvidia-nemotron-ocr-vm) below. |

The Run:ai control plane (Phase 1) and the Run:ai compute cluster (Phase 2) are two
**separate Kubernetes clusters** — this matches Run:ai's normal multi-cluster architecture,
where one control plane can manage multiple registered compute clusters.

## Why the control plane has no GPU nodes

The Run:ai control plane is pure management/UI/API (Postgres, NATS, Keycloak, Traefik, ~25
microservices) — no GPU scheduling happens there. GPU nodes only belong on whichever
cluster(s) actually run AI workloads, i.e. the Phase 2 compute cluster(s) registered against
the control plane.

## Before you deploy (required)

Every blueprint in this directory needs these three things addressed first — none of them
are safe to skip, and deployments will fail or silently misconfigure without them.

### 1. Create the `vpaif_dlvm_properties` property group

All three blueprints read from `${propgroup.vpaif_dlvm_properties.*}`:
- `AI Kubernetes cluster with NVIDIA Run-ai Control Plane.yaml` and
  `AI Kubernetes cluster with NVIDIA Run-ai.yaml` use `.NGC_API_KEY` (GPU Operator install,
  Run:ai Helm repo auth, `nvcr.io` image pull secret).
- `NVIDIA Nemotron-OCR.yaml` uses `.harbor`, `.NGC_API_KEY`, `.hfToken`, `.paisApiToken`,
  `.storageClass`, and `.vmiImageName`.

This property group does not exist by default in a new environment/org — create it once
before deploying any blueprint here by running
[`pais/scripts/create-dlvm-property-group.sh`](../../pais/scripts/create-dlvm-property-group.sh).
See that script's header comments for how to obtain the VCF Automation API token it needs.

### 2. Point `namespaceName` at your target namespace

Each blueprint hardcodes a `variables.namespaceName` value (e.g. `ai-workshop-m3fmp`) left
over from whichever Supervisor namespace it was last tested against. Before deploying, update
that variable in the blueprint to the actual namespace you're deploying into:

```yaml
variables:
  namespaceName: <your-supervisor-namespace>
```

The VCF Automation designer will sometimes auto-correct this when you open/save a blueprint
bound to a specific deployment, but don't rely on that — set it explicitly before deploying
to a new namespace.

### 3. Update the GPU worker VM class list to match your environment

The GPU VM class `oneOf` lists are specific to *this* environment's Supervisor namespace and
will not necessarily exist elsewhere:
- `workerVmClassName` in `AI Kubernetes cluster with NVIDIA Run-ai.yaml`
- `vmClassName` in `NVIDIA Nemotron-OCR.yaml`

Before deploying elsewhere, check what's actually available
(`kubectl get virtualmachineclassbindings -n <namespace>`, or the VM Class list in your
Supervisor namespace's settings) and update the `oneOf` entries accordingly — a stale list
either blocks the request form or lets someone pick a class that doesn't exist in that
namespace.

### 4. Update the vGPU license property group reference

Separately from `vpaif_dlvm_properties`, the GPU Operator install step in each
GPU-provisioning blueprint reads an NVIDIA vGPU license token from its own
namespace/workshop-specific property group:
- `AI Kubernetes cluster with NVIDIA Run-ai.yaml` →
  `${propgroup.paif_dl_vgpu_ai_workshop2_g5pxh.vgpu_license}`
- `NVIDIA Nemotron-OCR.yaml` →
  `${propgroup.paif_dl_vgpu_ai_workshop_9w4np.vgpu_license}`

These property group names are tied to whichever namespace/workshop binding the blueprint was
last tested against — they are **not** created by `create-dlvm-property-group.sh` and are not
portable to a different environment as-is. Before deploying to a new namespace or environment,
replace `paif_dl_vgpu_ai_workshop2_g5pxh` / `paif_dl_vgpu_ai_workshop_9w4np` with whatever vGPU
licensing property group actually exists there (check with your platform/infrastructure team
or the VCF Automation property groups list) — otherwise the GPU Operator install job will fail
trying to resolve a property group that doesn't exist in that namespace.

## Deployment order

1. **Deploy Phase 1** (`... Control Plane.yaml`) into a namespace.
2. Wait for the `cciRunaiControlPlaneJob` to complete. Check its logs for the line
   `Status: Run:ai control plane domain is ...` — if you left `runaiControlPlaneDomain` blank,
   a domain is auto-derived from the assigned ingress IP via `nip.io` (no manual DNS needed
   for this path). If you set a custom domain, point DNS for it at the printed IP.
3. Log into the Run:ai UI at that domain with the admin email/password you supplied.
4. In the UI, go to **Clusters > New Cluster**, name it after the Kubernetes cluster you intend
   to register (see Phase 2's `cciRunaiInstallJob` logs), and complete the wizard. Copy down the
   generated `cluster.uid` and `controlPlane.clientSecret` — **the secret is shown once**.
5. **Deploy Phase 2** (`AI Kubernetes cluster with NVIDIA Run-ai.yaml`) into the **same
   namespace**, supplying:
   - `runaiControlPlaneUrl` — the Phase 1 domain from step 2 (with scheme, e.g. `https://runai.pse.lab`)
   - `runaiClusterUid` / `runaiClientSecret` — from step 4
   - `runaiClusterUrl`, `runaiClusterIngressClass`, `runaiVersion` — compare against the exact
     `helm upgrade -i runai-cluster ...` command the wizard shows in step 4 (see
     [Verifying wizard flags](#verifying-wizard-flags-before-deploying) below)
6. Wait for `cciRunaiInstallJob` to complete, then verify in the Run:ai UI that the cluster
   shows as **Connected**.

## Verifying wizard flags before deploying

The exact Helm install command Run:ai's "New Cluster" wizard generates can change between
versions (chart version, flag names, defaults). Before running Phase 2, open the wizard's
generated command and diff it against `install_runai_cluster()` in the blueprint's job script.
Specifically check:
- `controlPlane.url` has **no scheme** (unlike `cluster.url`, which does)
- `clusterConfig.global.ingress.ingressClass` — whatever the wizard shows, point
  `runaiClusterIngressClass` at an ingress class that **actually exists** on the compute cluster
- `global.fipsMode` and any other `--set` flags not currently in the blueprint

This exact mismatch (missing `clusterConfig.global.ingress.ingressClass` and
`global.fipsMode=off`, plus the scheme difference) is what we hit and fixed during initial
testing — it's worth re-checking on every new NVIDIA Run:ai release.

## What's automated vs. what isn't

Both blueprints are self-contained Jobs that run unattended once deployed, but a few things
can't be known at blueprint-authoring time and need verification/action per environment:

- **Ingress controller**: neither cluster in this environment ships with a pre-installed
  ingress controller. Both blueprints self-install `ingress-nginx` (exposed via a
  `LoadBalancer` Service) rather than assuming one exists. If your environment already has
  a real ingress controller, you can skip this by adjusting the job, but as shipped it always
  installs its own.
- **Storage class**: hardcoded to `vsan-default-storage-policy` — this environment only has
  one storage class. If yours has more (e.g. a `-latebinding` variant), update the `cluster`
  resource's `vsphereOptions`/volume definitions accordingly.
- **Worker VM classes, namespace, and the `vpaif_dlvm_properties` property group**: see
  [Before you deploy](#before-you-deploy-required) above — these are required setup, not
  optional tuning.
- **vGPU license propgroup**: see
  [Before you deploy](#before-you-deploy-required) above — required setup, not optional
  tuning. Note the platform may also silently rewrite this value when a blueprint is
  opened/saved in the designer bound to a specific deployment, so if the GPU Operator job
  fails on this line, re-check it even if you already updated it once.

## Post-deployment steps (not handled by either blueprint)

### DNS

- **Control plane domain** (Phase 1): single A record → the IP printed in
  `cciRunaiControlPlaneJob`'s logs (or none needed if using the auto-derived `nip.io` domain).
- **Compute cluster domain** (Phase 2, `runaiClusterUrl` host): single A record → the
  `ingress-nginx-controller` Service's `EXTERNAL-IP` in the compute cluster
  (`kubectl -n ingress-nginx get svc ingress-nginx-controller`).
- **Inference workloads** (if `enableInference` is on): add a **wildcard** record
  (`*.<compute cluster domain>`) pointing at the same IP. Run:ai gives every Inference
  workload its own subdomain (`<workload-name>-<project-namespace>.<domain>`), so a
  single exact-match record isn't enough once you start creating workloads.

### TLS

Both blueprints generate self-signed certificates (for the control plane's ingress and, if
Inference is enabled, a wildcard cert for the compute cluster's inference subdomains). Clients
(browsers, `curl`) need to accept/ignore the self-signed warning (`curl -k`, or click through
in a browser). If you have real certificates from an internal CA, swap the `openssl req -x509`
steps for `kubectl create secret tls` from your own cert/key files instead.

### Pod Security on new Run:ai projects

This cluster enforces the Kubernetes `restricted` Pod Security Standard by default on new
namespaces. Both blueprints relax this to `baseline` on the system namespaces they create
(`knative-operator`, `knative-serving`, `lws-system`). However, **Run:ai creates a new
namespace per project** (e.g. `runai-<project>-project`) dynamically when you create a project
in the UI — these are not known at blueprint-deploy time and are not automatically relabeled.

Run:ai's own inference workload pod template (the `runai-oauth2-proxy` sidecar + a `hostPath`
volume for shared storage) does **not** comply with `restricted` or even `baseline` (hostPath
volumes are blocked at both levels), so every new project namespace needs:

```bash
kubectl label namespace <project-namespace> pod-security.kubernetes.io/enforce=privileged --overwrite
```

This is a deliberate decision to leave as a manual step rather than something the blueprint
automates — doing it automatically would require either a cluster-wide Pod Security default
change (untested whether the VKS ClusterClass exposes this as a topology variable) or an
ongoing namespace-watching controller, both bigger changes than a one-line `kubectl label`
run once per new project.

### Getting an API token for programmatic access

Inference endpoints are protected by Run:ai's OIDC auth (via the `runai-oauth2-proxy`
sidecar). To call them from `curl`/scripts rather than a browser, generate an API token:

```bash
curl -X POST 'https://<control-plane-domain>/api/v1/token' \
  --header 'Accept: */*' \
  --header 'Content-Type: application/json' \
  --data-raw '{
    "grantType": "client_credentials",
    "clientId": "<CLIENT ID>",
    "clientSecret": "<CLIENT SECRET>"
  }'
```

Create the `clientId`/`clientSecret` pair in the Run:ai UI under **Settings** (Applications or
Access Keys, depending on version). Use the returned `accessToken` as a Bearer token:

```bash
curl -k https://<workload-name>-<project-namespace>.<compute-cluster-domain>/v1/chat/completions \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -d '{"model": "<model-name>", "messages": [{"role": "user", "content": "Hello"}]}'
```

Note the exact external URL follows Knative's `domainTemplate`
(`<ksvc-name>-<k8s-namespace>.<domain>`) — check `kubectl get ksvc -n <project-namespace>`
or the workload's page in the Run:ai UI for the authoritative URL rather than guessing it.

### Redeploying after blueprint changes

All three blueprints set `disableUpdateDay2Action: true`. Structural changes (new worker node
pools, cluster topology changes) generally can't be applied as an in-place Update to an
existing deployment — expect to destroy and redeploy when the blueprint itself changes in a
way that affects the `cluster` resource.

## NVIDIA Nemotron-OCR VM

`NVIDIA Nemotron-OCR.yaml` is unrelated to the Run:ai cluster blueprints above — it's a
single-VM deployment (no Kubernetes) using the same `VirtualMachine`/`VirtualMachineService`
VM Operator pattern as the other DL workload blueprints in this repo (`open-webui`,
`librechat`).

### What it deploys

- One GPU-enabled Deep Learning VM (`vmClassName` input: `gpu-a100-small-8c`,
  `gpu-l40s-large-48c`, `gpu-rtx6-large-48c`, `gpu-rtx6-med-24c`, or `gpu-rtx6-small-8c`)
  plus an additional data disk (`dataStorageSize`, 32Gi–512Gi) mounted at `/opt/data`.
- On first boot, cloud-init (`dl_app.sh`) trusts the internal Harbor registry, then runs
  NVIDIA's `nemotron-ocr-v1` NIM container directly via `docker run --gpus all`, serving its
  OpenAI-style inference API on port 8000. The NIM model cache persists to the mounted data
  disk (`/opt/data/model-store`) across container restarts.
- A `VirtualMachineService` (LoadBalancer) exposes:
  - SSH (22) — always
  - the NIM inference API (8000) — always
  - DCGM Exporter / GPU telemetry (9400) — only if `exposeDCGM` is left on (default `true`)

### Inputs

| Input | Purpose |
|---|---|
| `vmClassName` | GPU VM class/size |
| `dataStorageSize` | Size of the `/opt/data` data disk (NIM model cache lives here) |
| `password` | Password for the `vmware` user |
| `sshPublicKeys` | SSH public key(s) for the `vmware` user |
| `exposeDCGM` | Whether DCGM GPU metrics are reachable externally (port 9400) vs. localhost-only |

### Accessing it

The deployment overview output gives the VM's LoadBalancer IP and per-service ports directly
(`ssh vmware@<ip>`, inference API at `<ip>:8000`, DCGM at `<ip>:9400` if enabled). No DNS, TLS,
or Run:ai-specific setup is required — unlike the Kubernetes/Run:ai blueprints above, this one
has no dependency on the other two and can be deployed completely independently.

### Things specific to this environment

- Depends on the `vpaif_dlvm_properties` property group (`.harbor` for the registry
  address/image path, plus `.NGC_API_KEY`, `.hfToken`, `.paisApiToken`, `.storageClass`,
  `.vmiImageName`) and on `namespaceName`/`vmClassName` matching your environment — see
  [Before you deploy](#before-you-deploy-required) at the top of this file.
- Like the GPU Operator job in the other blueprints, the vGPU license reference
  (`${propgroup.paif_dl_vgpu_ai_workshop_9w4np.vgpu_license}`) is tied to a specific
  namespace/workshop binding and may need correcting per deployment.
