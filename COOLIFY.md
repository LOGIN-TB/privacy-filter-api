<sub>**English** | [Deutsch](COOLIFY.de.md)</sub>

# Deploying privacy-filter-api on Coolify

[Coolify](https://coolify.io) is a self-hosted PaaS / Heroku alternative. This guide walks through deploying `privacy-filter-api` as a Coolify **Service** so other containers in the same Coolify Project (typically your n8n instance) can reach it on the internal network.

> Looking for Portainer instead? See [README.md](README.md#deploy-via-portainer-stack).

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Path A — Coolify Application from Git (recommended)](#path-a--coolify-application-from-git-recommended)
3. [Path B — Coolify Service with pasted Docker Compose](#path-b--coolify-service-with-pasted-docker-compose)
4. [Connecting your n8n workflow](#connecting-your-n8n-workflow)
5. [Optional: public HTTPS access via Traefik](#optional-public-https-access-via-traefik)
6. [Verifying the deployment](#verifying-the-deployment)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Coolify v4 or later, running and accessible.
- A Project in Coolify where your n8n already runs (or where it will run). Sibling services in the same Project share a Docker network.
- About 3 GB of free disk space for the model cache, plus 4 GB+ free RAM.

---

## Path A — Coolify Application from Git (recommended)

This is the cleanest path: Coolify clones the repo, builds the image, and re-deploys on every git push.

1. **Coolify dashboard → Project → + Add new resource → Public Repository**
2. **Repository URL:** `https://github.com/LOGIN-TB/privacy-filter-api`
3. **Branch:** `main`
4. **Build pack:** select **Docker Compose**
5. **Docker Compose file location:** `docker-compose.coolify.yml`
6. **Service name:** `privacy-filter` (this becomes the DNS name on the internal network)
7. **Persistent storage:** confirm the `opf_data` volume; it must survive redeploys (otherwise the 3 GB model re-downloads every time).
8. **Deploy.**

First deploy takes 3-5 min: Coolify pulls the repo, builds the image (CPU-only PyTorch is large), starts the container, then the container downloads the OPF model to the volume. Subsequent restarts are seconds.

> **Note on the build context:** the bundled `docker-compose.coolify.yml` uses `build: .` so Coolify builds from the cloned working tree. If you fork the repo and customise `app/main.py`, your changes go live on the next Coolify deploy.

---

## Path B — Coolify Service with pasted Docker Compose

Use this if you don't want Coolify to manage the Git relationship (e.g. you want a fixed pinned version, or you'll modify the compose locally).

1. **Coolify dashboard → Project → + Add new resource → Docker Compose Empty**
2. **Paste this YAML:**

   ```yaml
   services:
     privacy-filter:
       container_name: privacy-filter
       build:
         context: https://github.com/LOGIN-TB/privacy-filter-api.git#main
       restart: unless-stopped
       volumes:
         - opf_data:/data
       expose:
         - "9090"

   volumes:
     opf_data:
   ```

3. **Deploy.**

Coolify hands the compose to its Docker engine, which uses BuildKit to clone the URL inline and build. Same end result as Path A, less Coolify magic.

---

## Connecting your n8n workflow

If your n8n instance runs in the **same Coolify Project**, it already shares a Docker network with the new service. From any n8n HTTP Request node, use:

```
http://privacy-filter:9090/redact
http://privacy-filter:9090/rehydrate
http://privacy-filter:9090/healthz
```

If your n8n runs in a **different Project on the same Coolify host**, you have two options:

- **Move n8n into the same Project** (recommended). Coolify lets you reorganise resources easily.
- **Connect networks manually** via Coolify's network manager, attaching n8n's container to the privacy-filter network.

If your n8n runs **on a different host entirely** (separate VPS, Portainer, on-prem, etc.):

- Expose privacy-filter publicly with HTTPS via Traefik (see next section).
- **Add authentication** before exposing — see warning in that section.

For workflow-level configuration of the three nodes (Redact PII, Apply Redaction, Rehydrate), see [INTEGRATION.md](INTEGRATION.md).

---

## Optional: public HTTPS access via Traefik

Coolify ships with Traefik and can auto-provision Let's Encrypt certificates. To make `privacy-filter` reachable at e.g. `https://privacy-filter.your-domain.com`:

1. **Set the FQDN environment variable** in Coolify's Service settings:
   - Variable name: `FQDN_PRIVACY_FILTER`
   - Value: `privacy-filter.your-domain.com`

2. **Edit the compose** (Coolify → Service → Configuration) and uncomment the `labels:` block in `docker-compose.coolify.yml` (or paste it if you used Path B).

3. **DNS:** Point an A/AAAA record for the FQDN at your Coolify host's public IP.

4. **Redeploy.** Coolify configures Traefik, requests the Let's Encrypt cert, and the endpoint becomes reachable.

> ⚠ **Authentication** — the FastAPI service has **no built-in auth**. Anyone who finds the URL can submit text to be redacted (= use compute). Before going public:
>
> - Either add a Traefik basic-auth or forward-auth middleware on the route.
> - Or add a bearer-token check inside `app/main.py` (a few lines with FastAPI's `Depends(security)`).
>
> A public, unauthenticated PII service can be abused to incur GPU/CPU costs at minimum, and to extract spans from harvested texts at worst.

---

## Verifying the deployment

From any container in the same Coolify Project (e.g. exec into your n8n):

```bash
docker exec <n8n-container-name> curl -s http://privacy-filter:9090/healthz
```

Expected output:
```json
{"ok":true}
```

If you exposed it publicly:
```bash
curl -s https://privacy-filter.your-domain.com/healthz
```

Functional test:
```bash
docker exec <n8n-container-name> curl -s -X POST http://privacy-filter:9090/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Alice was born on 1990-01-02."}'
```

Expected: `redacted` field with `<PRIVATE_PERSON_1>` placeholder, plus a `mapping` and `spans` array.

---

## Troubleshooting

### Container restart loop, log shows `Killed`
The OPF model needs ~3-4 GB RAM. Coolify Service settings → Resource Limits — ensure no tight memory cap. If the host itself is short on RAM, the kernel OOM-killer terminates the container.

### Healthcheck fails for ~2 minutes after first start
Normal. The model downloads from HuggingFace on first start (~26 s for the download itself, plus PyTorch warmup). The Dockerfile's healthcheck has a 180 s `start-period` to allow for this. After the first start the volume holds the weights and restarts are fast.

### Build fails with `failed to read dockerfile`
Path A only: ensure `Docker Compose file location` is `docker-compose.coolify.yml`, not the default `docker-compose.yml`. The Coolify variant is the one that builds with relative context.

### `n8n` cannot reach `http://privacy-filter:9090`
- Check both services are in the same Coolify Project (same Docker network).
- Verify the service name in Coolify's UI is exactly `privacy-filter` (matches the host name).
- Try the container's Coolify-internal name (visible in Coolify UI) as a fallback if it differs.

### Model re-downloads on every redeploy
Volume `opf_data` is not persistent. Open Coolify's Storage configuration and confirm `opf_data` is set as a *named volume*, not bind-mounted to an ephemeral path.
