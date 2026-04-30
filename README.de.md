<sub>[English](README.md) | **Deutsch**</sub>

# privacy-filter-api

Kleiner FastAPI-Sidecar um [openai/privacy-filter](https://github.com/openai/privacy-filter), der zwei Endpunkte für die Nutzung aus n8n (oder einem beliebigen anderen HTTP-Client) bereitstellt:

- `POST /redact` — erkennt PII im Text und liefert redigierten Text + ein Mapping Platzhalter→Original
- `POST /rehydrate` — ersetzt Platzhalter in einem Text durch ihre Originalwerte anhand eines Mappings
- `GET  /healthz` — Readiness-Check (gibt `{"ok": true}` zurück, sobald das Modell geladen ist)

Das Modell wird beim ersten Start automatisch heruntergeladen (~1.5 Mrd Parameter) und in einem Docker-Volume abgelegt — nachfolgende Restarts sind dadurch schnell. Läuft CPU-only, keine GPU nötig.

---

## API

### `POST /redact`

```json
{ "text": "Hans Müller schreibt an hans@firma.de und Anna an anna@firma.de" }
```

Antwort — Platzhalter sind **indexiert und dedupliziert**, sodass derselbe Originalwert immer auf denselben Platzhalter mappt und die Rehydration eindeutig bleibt:

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

Antwort:

```json
{ "text": "Hallo Hans Müller, ich antworte an hans@firma.de." }
```

---

## Deploy via Portainer (Stack)

1. **Den Docker-Network-Namen von n8n finden:**
   ```bash
   docker inspect <n8n-container-name> \
     --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```
   Typische Namen: `n8n_default`, `proxy`, `traefik`, …

2. **In Portainer → Stacks → Add stack:**
   - Build method: **Repository**
   - Repository URL: `https://github.com/LOGIN-TB/privacy-filter-api`
   - Compose path: `docker-compose.yml`
   - Environment variables: `N8N_NETWORK` auf den Network-Namen aus Schritt 1 setzen.

3. **Deploy.** Erste Startphase dauert 1-2 Min (Modell-Download). Der Healthcheck hat eine 180-Sekunden-Karenzzeit.

4. **Aus n8n** (im selben Docker-Network) erreichbar unter:
   ```
   http://privacy-filter:9090/redact
   http://privacy-filter:9090/rehydrate
   http://privacy-filter:9090/healthz
   ```

   Es wird kein Port nach außen veröffentlicht — der Service ist nur innerhalb des Docker-Networks erreichbar.

---

## Deploy via Coolify

Wenn dein Stack auf [Coolify](https://coolify.io) statt Portainer läuft, siehe **[COOLIFY.de.md](COOLIFY.de.md)** — beschreibt Application-aus-Git, Service-mit-eingefügtem-Compose, Netzwerk-Verbindung zu einer Geschwister-n8n und optional öffentlicher HTTPS-Zugang via Coolifys Traefik.

---

## n8n-Integration

Schritt-für-Schritt-Anleitung mit Node-Konfiguration, ASCII-Schemas der typischen Patterns und häufigen Stolperfallen: siehe **[INTEGRATION.de.md](INTEGRATION.de.md)**.

Die Kurzfassung — drei Nodes ummanteln den LLM-Aufruf:

```
Trigger ─► [Redact PII (HTTP)] ─► [Apply Redaction (Set)] ─► LLM ─► [Rehydrate (HTTP)] ─► Respond
```

Der LLM sieht ausschließlich redigierten Text. Ergänze einen System-Prompt-Hinweis, der das Modell anweist, Platzhalter **wörtlich beizubehalten** (also nicht `<PRIVATE_PERSON_1>` zu „die genannte Person" paraphrasieren).

---

## Lokale Entwicklung

```bash
docker compose up --build
curl -s -X POST http://localhost:9090/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Alice was born on 1990-01-02."}'
```

Um den Port lokal nach außen zu legen, in `docker-compose.yml` ergänzen:
```yaml
    ports:
      - "9090:9090"
```

---

## Erkannte Kategorien

`opf` erkennt 8 PII-Span-Typen — Platzhalter folgen dem Schema `<LABEL_N>`:

- `private_person` — Namen
- `private_email`
- `private_phone`
- `private_address`
- `private_url`
- `private_date`
- `account_number`
- `secret` — Credentials, Tokens

---

## Lizenz

Glue-Code dieses Projekts: siehe `LICENSE`. Die zugrunde liegende `opf`-Library hat ihre eigene Lizenz (siehe [openai/privacy-filter](https://github.com/openai/privacy-filter)).
