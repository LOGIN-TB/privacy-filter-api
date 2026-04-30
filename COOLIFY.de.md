<sub>[English](COOLIFY.md) | **Deutsch**</sub>

# privacy-filter-api auf Coolify deployen

[Coolify](https://coolify.io) ist eine selbst gehostete PaaS- bzw. Heroku-Alternative. Diese Anleitung führt durch das Deployment von `privacy-filter-api` als Coolify-**Service**, sodass andere Container im selben Coolify-Projekt (typischerweise deine n8n-Instanz) den Sidecar über das interne Netzwerk erreichen können.

> Du suchst nach Portainer? Siehe [README.de.md](README.de.md#deploy-via-portainer-stack).

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#voraussetzungen)
2. [Pfad A — Coolify-Application aus Git (empfohlen)](#pfad-a--coolify-application-aus-git-empfohlen)
3. [Pfad B — Coolify-Service mit eingefügtem Docker Compose](#pfad-b--coolify-service-mit-eingefügtem-docker-compose)
4. [Anbindung an deinen n8n-Workflow](#anbindung-an-deinen-n8n-workflow)
5. [Optional: öffentlicher HTTPS-Zugang via Traefik](#optional-öffentlicher-https-zugang-via-traefik)
6. [Deployment verifizieren](#deployment-verifizieren)
7. [Troubleshooting](#troubleshooting)

---

## Voraussetzungen

- Coolify v4 oder neuer, läuft und ist erreichbar.
- Ein Projekt in Coolify, in dem dein n8n bereits läuft (oder wo es laufen wird). Geschwister-Services im selben Projekt teilen sich ein Docker-Netzwerk.
- Etwa 3 GB freier Festplattenspeicher für den Modell-Cache plus 4 GB+ freier RAM.

---

## Pfad A — Coolify-Application aus Git (empfohlen)

Der sauberste Weg: Coolify klont das Repo, baut das Image und deployt bei jedem Git-Push neu.

1. **Coolify-Dashboard → Projekt → + Add new resource → Public Repository**
2. **Repository URL:** `https://github.com/LOGIN-TB/privacy-filter-api`
3. **Branch:** `main`
4. **Build pack:** **Docker Compose** auswählen
5. **Docker Compose file location:** `docker-compose.coolify.yml`
6. **Service name:** `privacy-filter` (das wird der DNS-Name im internen Netzwerk)
7. **Persistent storage:** das Volume `opf_data` bestätigen; es muss Redeploys überleben (sonst lädt das 3-GB-Modell jedes Mal neu).
8. **Deploy.**

Erstes Deploy dauert 3-5 Minuten: Coolify holt das Repo, baut das Image (CPU-only PyTorch ist groß), startet den Container, danach lädt der Container das OPF-Modell ins Volume. Folgende Restarts dauern Sekunden.

> **Hinweis zum Build-Context:** Die mitgelieferte `docker-compose.coolify.yml` nutzt `build: .`, sodass Coolify aus dem geklonten Working-Tree baut. Wenn du das Repo forkst und `app/main.py` anpasst, gehen deine Änderungen beim nächsten Coolify-Deploy live.

---

## Pfad B — Coolify-Service mit eingefügtem Docker Compose

Verwende diesen Pfad, wenn du Coolify die Git-Beziehung **nicht** verwalten lassen willst (z. B. weil du eine fest gepinnte Version willst, oder das Compose lokal modifizierst).

1. **Coolify-Dashboard → Projekt → + Add new resource → Docker Compose Empty**
2. **Folgendes YAML einfügen:**

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

Coolify reicht das Compose an seine Docker-Engine weiter, die mit BuildKit die URL inline klont und baut. Selbes Endresultat wie Pfad A, weniger Coolify-Magie.

---

## Anbindung an deinen n8n-Workflow

Wenn deine n8n-Instanz **im selben Coolify-Projekt** läuft, teilt sie sich bereits ein Docker-Netzwerk mit dem neuen Service. Aus einem n8n-HTTP-Request-Node verwendest du:

```
http://privacy-filter:9090/redact
http://privacy-filter:9090/rehydrate
http://privacy-filter:9090/healthz
```

Wenn deine n8n in einem **anderen Projekt auf demselben Coolify-Host** läuft, gibt es zwei Optionen:

- **n8n ins selbe Projekt verschieben** (empfohlen). Coolify lässt dich Resourcen leicht reorganisieren.
- **Netzwerke manuell verbinden** über Coolifys Network-Manager: den n8n-Container an das privacy-filter-Netzwerk attachen.

Wenn deine n8n auf einem **komplett anderen Host** läuft (separater VPS, Portainer, on-prem, …):

- privacy-filter öffentlich via HTTPS über Traefik freigeben (siehe nächster Abschnitt).
- **Authentifizierung ergänzen**, bevor du exponierst — siehe Warnhinweis dort.

Die Workflow-Konfiguration der drei Nodes (Redact PII, Apply Redaction, Rehydrate) findest du in [INTEGRATION.de.md](INTEGRATION.de.md).

---

## Optional: öffentlicher HTTPS-Zugang via Traefik

Coolify bringt Traefik mit und kann automatisch Let's-Encrypt-Zertifikate ausstellen. Um `privacy-filter` z. B. unter `https://privacy-filter.deine-domain.de` erreichbar zu machen:

1. **FQDN-Umgebungsvariable** in den Coolify-Service-Settings setzen:
   - Variable: `FQDN_PRIVACY_FILTER`
   - Wert: `privacy-filter.deine-domain.de`

2. **Compose anpassen** (Coolify → Service → Configuration): den `labels:`-Block in `docker-compose.coolify.yml` einkommentieren (oder einfügen, falls du Pfad B genutzt hast).

3. **DNS:** Einen A-/AAAA-Record für die FQDN auf die öffentliche IP deines Coolify-Hosts zeigen lassen.

4. **Redeploy.** Coolify konfiguriert Traefik, fordert das Let's-Encrypt-Zertifikat an, und der Endpunkt ist erreichbar.

> ⚠ **Authentifizierung** — der FastAPI-Service hat **keine eingebaute Auth**. Wer die URL findet, kann Texte zur Redaktion einsenden (= Compute verbrauchen). Bevor du das öffentlich machst:
>
> - Entweder eine Traefik-Basic-Auth- oder Forward-Auth-Middleware auf der Route ergänzen.
> - Oder einen Bearer-Token-Check direkt in `app/main.py` einbauen (wenige Zeilen mit FastAPIs `Depends(security)`).
>
> Ein öffentlich erreichbarer, unauthentifizierter PII-Service kann mindestens für Cost-Abuse (CPU/GPU) missbraucht werden, im schlimmsten Fall zur Span-Extraktion aus eingesammelten Texten.

---

## Deployment verifizieren

Aus einem beliebigen Container im selben Coolify-Projekt (z. B. `exec` in deine n8n):

```bash
docker exec <n8n-container-name> curl -s http://privacy-filter:9090/healthz
```

Erwartete Ausgabe:
```json
{"ok":true}
```

Wenn du öffentlich exponiert hast:
```bash
curl -s https://privacy-filter.deine-domain.de/healthz
```

Funktionstest:
```bash
docker exec <n8n-container-name> curl -s -X POST http://privacy-filter:9090/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Alice was born on 1990-01-02."}'
```

Erwartet: `redacted`-Feld mit Platzhalter `<PRIVATE_PERSON_1>`, plus `mapping` und `spans`.

---

## Troubleshooting

### Container in Restart-Loop, Log zeigt `Killed`
Das OPF-Modell braucht ca. 3-4 GB RAM. Coolify-Service-Settings → Resource Limits: prüfen, dass kein zu enger Memory-Cap gesetzt ist. Wenn der Host selbst zu wenig RAM hat, killt der Kernel-OOM-Killer den Container.

### Healthcheck schlägt für ca. 2 Minuten nach dem ersten Start fehl
Normal. Das Modell wird beim ersten Start von HuggingFace geladen (~26 s reiner Download plus PyTorch-Warmup). Der Healthcheck im Dockerfile hat ein 180-s-`start-period`, das genau das abdeckt. Nach dem ersten Start liegen die Gewichte im Volume — Restarts dauern Sekunden.

### Build schlägt fehl mit `failed to read dockerfile`
Nur Pfad A: sicherstellen, dass `Docker Compose file location` auf `docker-compose.coolify.yml` steht, nicht auf der Default-`docker-compose.yml`. Die Coolify-Variante baut mit relativem Context.

### `n8n` erreicht `http://privacy-filter:9090` nicht
- Prüfen, dass beide Services im selben Coolify-Projekt liegen (selbes Docker-Netzwerk).
- Verifizieren, dass der Service-Name in Coolifys UI exakt `privacy-filter` ist (passt dann zum Hostnamen).
- Falls nicht: den Coolify-internen Container-Namen (sichtbar in der Coolify-UI) als Fallback probieren.

### Modell wird bei jedem Redeploy neu geladen
Das Volume `opf_data` ist nicht persistent. In Coolifys Storage-Konfiguration prüfen, dass `opf_data` als *Named Volume* eingerichtet ist und nicht als Bind-Mount auf einen flüchtigen Pfad.
