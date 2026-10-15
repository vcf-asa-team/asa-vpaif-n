# Open-WebUI on VCF Automation

Cloud Template that deploys [Open WebUI](https://github.com/open-webui/open-webui) behind an nginx TLS reverse proxy on a Deep Learning VM (DLVM), backed by a VCF Data Services Manager Postgres cluster for chat history and RAG (pgvector).

## Files

| File | Role |
|---|---|
| `Secure Open-WebUI AI Interface.yaml` | The Cloud Template (blueprint) requested from the Service Broker catalog. Provisions all infrastructure and the VM's cloud-init. |
| `Secure Open-WebUI AI Interface.json` | Custom Form for the blueprint above — groups the request-time inputs into pages (Deep Learning VM / Open-Webui / Database / OIDC) and hides the OIDC detail fields unless "Enable OIDC with vIDM" is checked. |
| `docker-compose-webui-rev-proxy.yaml` | Runtime compose file `dl_app.sh` downloads and runs on the VM: the `open-webui` container plus an `nginx` reverse proxy terminating TLS. |
| `nginx.conf` | Config consumed by the `reverse-proxy` container — TLS termination, websocket/streaming headers, proxies to `open-webui:8080`. |
| `.env` | Template consumed by the compose file, with `<placeholder>` tokens that `dl_app.sh` fills in at boot (app name, DB connection, image tag, and — only if OIDC is enabled — the vIDM OAuth vars). |
| `function-pais_agent_v2.json`, `function-ocr_interceptor.json` | Open WebUI function/model definitions available to import once the app is running (not consumed by the blueprint automatically). |
| `Dockerfile` | Reference build for a custom Open WebUI image, if you need to rebuild rather than use the pre-built harbor image. |

## Architecture

The `.yaml` blueprint is the only thing VCF Automation itself understands. Everything else is a plain runtime artifact fetched by the deployed VM's own boot script (`dl_app.sh`, embedded in the blueprint's cloud-init) and is never touched by VCF Automation directly — it just lives in this repo so `dl_app.sh` can `wget` it from GitHub raw at boot.

**Resources provisioned by the blueprint** (`CCI.Supervisor.*`, all scoped to the target namespace):

- `cciNamespace` — the existing Supervisor namespace to deploy into.
- `dlVmService` — a `LoadBalancer` `VirtualMachineService` exposing SSH (22) and Open-WebUI (443) externally.
- `dlVmDataStorage` — a PVC for the VM's `/opt/data` disk.
- `DBConfig` + `Postgres_DB` — a tuned `PostgresCluster` (work_mem/shared_buffers/etc.) backing Open WebUI's database and its pgvector RAG store.
- `DSMDBSecret` + `PsqlJob` — reads the generated DB admin password and runs a one-shot `Job` that creates the Open WebUI database, assigns ownership, and grants privileges.
- `dlVmCloudInit` — a `Secret` holding the VM's cloud-init `user-data` (hostname, SSH key, password, vGPU license, harbor/NGC credentials, and the Postgres connection string assembled from the resources above).
- `dlVm` — the `VirtualMachine` itself, built from the DL/vGPU-enabled image, bootstrapped with that cloud-init secret.

**What the VM does at first boot** (`dl_app.sh`, guarded by `/opt/data/.file_download` so it only runs once):

1. Adds the harbor registry as an insecure registry and trusts its CA cert.
2. Parses the Postgres connection string out of the cloud-init `config-json` guestinfo.
3. Downloads `docker-compose-webui-rev-proxy.yaml`, `.env`, and `nginx.conf` from this repo.
4. Patches `.env` with the request-time inputs: app name, DB connection string, LLM gateway URL, image tag.
5. If `enableOIDC` is checked: flips `WEBUI_AUTH=True`, appends the vIDM OAuth env vars (`OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OPENID_PROVIDER_URL`, etc.) to `.env`, and fetches the OIDC provider's TLS certificate chain into `open-webui/certs/oidc-ca-chain.crt` (mounted read-only into the container so Open WebUI trusts vIDM over TLS). If OIDC is left disabled, that same file path is `touch`ed empty so the compose bind mount doesn't break.
6. Generates a self-signed cert for the nginx reverse proxy.
7. Runs `docker compose -f open-webui/docker-compose-webui-rev-proxy.yaml up -d`.
8. On first successful start, calls Open WebUI's own admin API once to enable signup and set sane defaults (admin role, community sharing, message rating).

**Outputs**: `outputs.__deploymentOverview` renders the post-deploy summary — the app URL, exposed services, the OIDC redirect URI to register in vIDM (only shown when OIDC is enabled), and SSH access info.

## Deploying from VCF Automation

### Prerequisites

Sections in the blueprint yaml must be updated to match your environment.  Ensure the namespace is updated along with creating a new property group `vpaif_dlvm_properties`  and updating the DSM deployment policy.
- The Open WebUI image mirrored into your harbor registry at `paif-n/open-webui:v0.11.1` (or update the tag in `dl_app.sh` to match what you've mirrored).
- These property groups populated for the target project: `vpaif_dlvm_properties` (harbor host, storage class, NGC API key, DLVM image name), `paif_dl_vgpu_ai_workshop_9w4np` (vGPU license/URL).
- A VCF Data Services Manager `workshop-infra-policy` infrastructure policy available for the Postgres cluster.
- The blueprint and its custom form imported as a catalog item in Service Broker (`Secure Open-WebUI AI Interface.yaml` + `.json`).

### Requesting the catalog item

The request form is grouped into four pages:

1. **Deep Learning VM Configuration** — target namespace, VM class, extra data disk size, the `vmware` user's password and SSH public key.
2. **Open-Webui Configuration** — the application name and the LLM/API gateway FQDN Open WebUI should call out to.
3. **Database Configuration** — the Open WebUI database name plus the Postgres cluster's storage policy, VM class, and disk size.
4. **OIDC Configuration** — check "Enable OIDC with vIDM" to reveal the vIDM discovery URL, OAuth client ID, and client secret fields; leave it unchecked to skip SSO entirely.

> **Known limitation:** the `namespaceName` input is currently pinned to a single value (`ai-workshop2-g5pxh`) via a static `oneOf` left over from testing. For a real live dropdown of available namespaces you need a Service Broker Custom Form field bound to an external data source — the platform doesn't support that purely from the Cloud Template YAML. Until that's wired up, either edit the `oneOf` list to the namespace(s) you actually want offered, or remove it entirely so requesters can type a namespace name directly.

### After deployment

- The `__deploymentOverview` output gives you the app URL and, if OIDC is enabled, the exact redirect URI to register as an OAuth client in vIDM (Workspace ONE Access): `https://<load-balancer-ip>/oauth/oidc/callback`.
- SSH access: `ssh vmware@<load-balancer-ip>` using the password/key supplied at request time.
- Data persists at `/opt/data` on the VM across redeploys of the compose stack (but not across VM deletion).

### Known caveats

- The OIDC cert-fetch step (`sed -ne '/-BEGIN CERTIFICATE-/,/-END CERTIFICATE-/p'` over full `openssl s_client -showcerts` output) captures the leaf certificate too, not just the intermediate/root chain — harmless as a trust bundle, just wider than the name implies.
- `AIOHTTP_CLIENT_SESSION_SSL=False` and `REQUEST_VERIFY=False` are already set in `.env`, which disables TLS verification for some of Open WebUI's own outbound calls regardless of the fetched CA chain — worth confirming the cert bundle is actually the thing enforcing trust for the OIDC token exchange before relying on it.
