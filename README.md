# privacy-filter-api

Small FastAPI sidecar around [openai/privacy-filter](https://github.com/openai/privacy-filter) that exposes two endpoints for use from n8n (or any other HTTP client):

- `POST /redact` — detect PII in text, return redacted text + a placeholder→original mapping
- `POST /rehydrate` — replace placeholders in a text with their original values, using a provided mapping
- `GET  /healthz` — readiness probe (returns `{"ok": true}` once the model is loaded)

The model auto-downloads (~1.5B params) on first start to a Docker volume, so subsequent restarts are fast. Runs CPU-only — no GPU required.

---

## API

### `POST /redact`

```json
{ "text": "Hans Müller schreibt an hans@firma.de und Anna an anna@firma.de" }
```

Response — placeholders are **indexed and de-duplicated** so the same original value always maps to the same placeholder, and rehydration is unambiguous:

```json
{
  "redacted": "<PRIVATE_PERSON_1> schreibt an <PRIVATE_EMAIL_1> und <PRIVATE_PERSON_2> an <PRIVATE_EMAIL_2>",
  "mapping": {
    "<PRIVATE_PERSON_1>": "Hans Müller",
    "<PRIVATE_EMAIL_1>":  "hans@firma.de",
    "<PRIVATE_PERSON_2>": "Anna",
    "<PRIVATE_EMAIL_2>":  "anna@firma.de"
  },
  "spans": [ ... ]
}
```

### `POST /rehydrate`

```json
{
  "text": "Hallo <PRIVATE_PERSON_1>, ich antworte an <PRIVATE_EMAIL_1>.",
  "mapping": {
    "<PRIVATE_PERSON_1>": "Hans Müller",
    "<PRIVATE_EMAIL_1>":  "hans@firma.de"
  }
}
```

Response:

```json
{ "text": "Hallo Hans Müller, ich antworte an hans@firma.de." }
```

---

## Deploy via Portainer (Stack)

1. **Find the n8n Docker network name:**
   ```bash
   docker inspect <n8n-container-name> \
     --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```
   Typical names: `n8n_default`, `proxy`, `traefik`, ...

2. **In Portainer → Stacks → Add stack:**
   - Build method: **Repository**
   - Repository URL: `https://github.com/LOGIN-TB/privacy-filter-api`
   - Compose path: `docker-compose.yml`
   - Environment variables: set `N8N_NETWORK` to the network name from step 1.

3. **Deploy.** First start takes 1-2 min (model download). The healthcheck has a 180 s grace period.

4. **From n8n** (same Docker network), reach the service at:
   ```
   http://privacy-filter:9090/redact
   http://privacy-filter:9090/rehydrate
   http://privacy-filter:9090/healthz
   ```

   No port is published to the host — the service is only reachable inside the Docker network.

---

## n8n Integration Pattern

Insert two HTTP Request nodes around the LLM:

```
Webhook ──► HTTP POST /redact ──► [Set: store mapping] ──► If ──► AI Agent / LLM
                                                                       │
                                                                       ▼
                                  HTTP POST /rehydrate (with stored mapping)
                                                                       │
                                                                       ▼
                                                           Edit Fields → Respond
```

The LLM only ever sees redacted text. Add a system-prompt instruction telling the model to **preserve placeholders verbatim** (do not paraphrase `<PRIVATE_PERSON_1>` to "the person mentioned").

---

## Local development

```bash
docker compose up --build
curl -s -X POST http://localhost:9090/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Alice was born on 1990-01-02."}'
```

To expose the port for local testing, add to `docker-compose.yml`:
```yaml
    ports:
      - "9090:9090"
```

---

## Detected categories

`opf` recognises 8 PII span types — placeholders follow `<LABEL_N>`:

- `private_person` — names
- `private_email`
- `private_phone`
- `private_address`
- `private_url`
- `private_date`
- `account_number`
- `secret` — credentials, tokens

---

## License

Project glue: see `LICENSE`. The underlying `opf` library is governed by its own license (see [openai/privacy-filter](https://github.com/openai/privacy-filter)).
