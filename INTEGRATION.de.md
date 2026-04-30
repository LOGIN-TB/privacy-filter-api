<sub>[English](INTEGRATION.md) | **Deutsch**</sub>

# privacy-filter-api in n8n-Workflows integrieren

Ein wiederverwendbares Pattern, um PII **vor** einem LLM-Aufruf zu redigieren und **danach** wieder einzusetzen — so bekommt das Modell keine personenbezogenen Daten zu sehen, der Endnutzer aber trotzdem eine natürliche Antwort.

> Voraussetzung: der `privacy-filter`-Container läuft und ist aus n8n unter `http://privacy-filter:9090` erreichbar (siehe [README.de.md](README.de.md) für Deployment).

---

## Inhaltsverzeichnis

1. [Konzept](#konzept)
2. [Die drei Nodes, die du brauchst](#die-drei-nodes-die-du-brauchst)
3. [Pattern A — Filter für jede Anfrage](#pattern-a--filter-für-jede-anfrage)
4. [Pattern B — Bedingter Filter (günstige Meta-Anfragen überspringen)](#pattern-b--bedingter-filter-günstige-meta-anfragen-überspringen)
5. [Node-Konfigurations-Referenz](#node-konfigurations-referenz)
6. [System-Prompt-Ergänzung für den LLM](#system-prompt-ergänzung-für-den-llm)
7. [Häufige Stolperfallen](#häufige-stolperfallen)
8. [Performance-Hinweise](#performance-hinweise)

---

## Konzept

```
              ┌──────────────────┐                    ┌──────────────────┐
   User  ───► │  Redact (POST    │  redigierter Text  │                  │
   Text       │  /redact)        │ ─────────────────► │   LLM / Agent    │
              │  + Mapping       │                    │ (sieht keine PII)│
              └──────────────────┘                    └──────────┬───────┘
                       │                                         │
                       │ Mapping bleibt im Workflow              │ Antwort mit Platzhaltern
                       │                                         │
                       └──────────────────┬──────────────────────┘
                                          ▼
                                ┌──────────────────┐
                                │  Rehydrate       │ natürliche Antwort
                                │  (POST           │ ──────────────────►  User
                                │  /rehydrate)     │
                                └──────────────────┘
```

Der Vertrag:

- `POST /redact` liefert `{ redacted, mapping, spans }`. Platzhalter sind **durchnummeriert** (`<PRIVATE_PERSON_1>`, `<PRIVATE_PERSON_2>`, …), damit auch mehrere PII-Werte desselben Typs eindeutig zurückgemappt werden können.
- `POST /rehydrate` ist die Umkehrung: nimmt `{ text, mapping }` und liefert `{ text }` mit ersetzten Platzhaltern.
- Das Mapping muss vom Punkt vor dem LLM-Call bis zum Punkt nach dem LLM-Call mitgeführt werden — typischerweise indem es auf dem Workflow-Item gespeichert und später per `$('NodeName').item.json` ausgelesen wird.

---

## Die drei Nodes, die du brauchst

| # | Node-Typ | Name (Vorschlag) | Funktion |
|---|---|---|---|
| 1 | **HTTP Request** | `Redact PII` | Ruft `POST /redact` mit dem User-Text auf. |
| 2 | **Set** (Edit Fields) | `Apply Redaction` | Ersetzt den Originaltext durch die redigierte Version auf dem Workflow-Item und legt das Mapping zur späteren Nutzung ab. |
| 3 | **HTTP Request** | `Rehydrate` | Ruft `POST /rehydrate` mit dem LLM-Output und dem gespeicherten Mapping auf. |

Mehr nicht. Kein Code-Node, keine Function — drei vanilla Nodes reichen.

---

## Pattern A — Filter für jede Anfrage

Verwende dieses Pattern, wenn **jede** Anfrage, die der Workflow empfängt, PII enthalten kann (z. B. alle User-Nachrichten laufen über einen Webhook ohne Verzweigung).

```
   Trigger ─► Redact PII ─► Apply Redaction ─► LLM / Agent ─► Rehydrate ─► Respond
```

**Pro:** einfachste Topologie, ein einziger Code-Pfad.
**Contra:** jede Anfrage zahlt die Redaction-Kosten — auch solche, die das gar nicht bräuchten (z. B. interne System-Tasks).

---

## Pattern B — Bedingter Filter (günstige Meta-Anfragen überspringen)

Verwende dieses Pattern, wenn dein Trigger **zwei Arten** von Verkehr bekommt:

- Echte User-Nachrichten → müssen gefiltert werden.
- Interne / Meta-Anfragen (z. B. Tag-Generation, Title-Generation, System-Tasks) → typisch kurzlebig, niedrige Sensibilität, enthalten oft nur Material, das ohnehin schon gefiltert wurde. Diese zu überspringen vermeidet Sekunden CPU-Zeit im Redactor.

```
                    ┌─► Günstiger LLM ──────────────────────────────────┐
                    │   (Meta-Tasks)                                    │
   Trigger ─► If ───┤                                                   ├─► Respond
                    │                                                   │
                    └─► Redact PII ─► Apply Redaction ─► Haupt-LLM ─► Rehydrate
                        (echte User-Anfragen)
```

**Pro:** Meta-Anfragen sind 20-30× schneller (keine Modell-Inferenz auf langen Historien).
**Contra:** **der Meta-Branch sieht PII**. Akzeptabel, wenn der LLM-Provider sowieso schon die Hauptantwort verarbeitet (kein neuer Dritter), nicht akzeptabel bei harten Isolationsanforderungen.

Die `If`-Bedingung entscheidet über den Pfad — meist ein `startsWith`-Check auf einen Marker wie `### Task:` oder ein Flag in der Request-Payload.

---

## Node-Konfigurations-Referenz

Pro Node die Parameter, die du in der n8n-UI setzt. Felder, die nicht aufgeführt sind, bleiben auf Default.

### 1. `Redact PII` — HTTP Request

| Feld | Wert |
|---|---|
| Method | `POST` |
| URL | `http://privacy-filter:9090/redact` |
| Send Body | ✅ aktiviert |
| Body Content Type | `JSON` |
| Specify Body | `Using JSON` |
| JSON | siehe unten |

**JSON-Body** (ersetze `body.chatInput` durch den Pfad zum User-Text in deiner Trigger-Payload):

```
={
  "text": {{ JSON.stringify($json.body.chatInput) }}
}
```

> Der `JSON.stringify()`-Wrapper ist **wichtig** — er escapet Anführungszeichen, Zeilenumbrüche und andere Spezialzeichen, sodass das gerenderte JSON auch dann gültig bleibt, wenn die User-Eingabe so etwas enthält.

**Output dieses Nodes** (was als `$json` weiterfließt):

```json
{
  "redacted": "...Text mit Platzhaltern...",
  "mapping":  { "<PRIVATE_PERSON_1>": "Hans Müller", "<PRIVATE_EMAIL_1>": "hans@firma.de" },
  "spans":    [ /* Span-Details pro Treffer */ ]
}
```

### 2. `Apply Redaction` — Set (Edit Fields)

Der Set-Node hat eine Aufgabe: den `redacted`-Text aus dem vorigen Node nehmen und **dort wieder ablegen, wo der LLM den User-Text erwartet** (z. B. `body.chatInput`), wobei alle anderen Request-Felder (sessionId etc.) erhalten bleiben und das Mapping für später abgelegt wird.

| Assignment | Type | Value |
|---|---|---|
| `body.chatInput` | string | `={{ $json.redacted }}` |
| `body.sessionId` | string | `={{ $('Trigger').item.json.body.sessionId }}` |
| `_privacy_mapping` | object | `={{ $json.mapping }}` |

Passe die Feldnamen an die Payload deines Triggers an:

- Das erste Assignment **muss** den redigierten Text dort hinschreiben, wo Downstream-Nodes den User-Text erwarten.
- Das zweite Assignment (und ggf. weitere Passthrough-Assignments) stellt Felder wieder her, die der ursprüngliche Trigger hatte und die du noch brauchst (Session-IDs, Message-IDs, User-IDs, …). Verwende `$('Trigger Node Name').item.json.body.fieldName`, um auf die Original-Payload zuzugreifen.
- Das dritte Assignment benennt das Mapping-Feld — Konvention hier ist `_privacy_mapping` (führender Unterstrich signalisiert „internes Feld"), aber jeder Name funktioniert, solange er im Rehydrate-Node konsistent referenziert wird.

> ⚠ **Warum explizites Passthrough?** Der Set-Node v3.x reicht standardmäßig **keine** ungesetzten Felder durch. Wenn du `sessionId` hier nicht aufführst, ist es nach diesem Node weg — und jeder Downstream-Node, der es braucht (z. B. ein Memory-Node mit sessionId als Key) bricht.

### 3. `Rehydrate` — HTTP Request

Identische Struktur wie `Redact PII`, mit zwei Änderungen: andere URL und ein erweiterter Body, der sowohl den LLM-Output als auch das gespeicherte Mapping mitführt.

| Feld | Wert |
|---|---|
| Method | `POST` |
| URL | `http://privacy-filter:9090/rehydrate` |
| Send Body | ✅ aktiviert |
| Body Content Type | `JSON` |
| Specify Body | `Using JSON` |

**JSON-Body**:

```
={
  "text": {{ JSON.stringify($json.output || $json.text) }},
  "mapping": {{ JSON.stringify($('Apply Redaction').item.json._privacy_mapping) }}
}
```

Das `$json.output || $json.text`-Pattern deckt zwei Fälle ab: `output` ist das, was ein n8n AI Agent zurückgibt; `text` ist das, was eine LLM Chain liefert. Falls dein LLM-Node einen anderen Output-Key nutzt, entsprechend anpassen.

**Output dieses Nodes**:

```json
{ "text": "...natürliche Antwort mit eingesetztem Original-PII..." }
```

Danach folgt typischerweise ein Set/Edit-Fields-Node, der `$json.text` in das Format mappt, das dein Responder erwartet.

---

## System-Prompt-Ergänzung für den LLM

Das Redact-Then-Rehydrate-Spiel funktioniert nur, wenn der LLM **Platzhalter wörtlich** in seinem Output beibehält. Wenn das Modell `<PRIVATE_PERSON_1>` zu „die genannte Person" paraphrasiert, hat der Rehydrate-Schritt nichts mehr zum Ersetzen.

Ergänze in der `systemMessage` deines AI Agent (oder im Prompt einer LLM Chain) eine Klausel wie diese:

```
**Privacy-Platzhalter:** Im Eingabetext und in der Chat-Historie können
Platzhalter wie <PRIVATE_PERSON_1>, <PRIVATE_EMAIL_1>,
<PRIVATE_PHONE_1>, <PRIVATE_DATE_1>, <PRIVATE_ADDRESS_1>,
<PRIVATE_URL_1>, <ACCOUNT_NUMBER_1>, <SECRET_1> erscheinen. Verwende
sie wörtlich in deiner Antwort, wo sie passen — paraphrasiere sie
nicht (z. B. nicht „die genannte Person" schreiben), und erfinde
keine eigenen Platzhalter.
```

Die kompletten Label-Präfixe, die `opf` produziert:

- `PRIVATE_PERSON` — Namen
- `PRIVATE_EMAIL`
- `PRIVATE_PHONE`
- `PRIVATE_ADDRESS`
- `PRIVATE_URL`
- `PRIVATE_DATE`
- `ACCOUNT_NUMBER`
- `SECRET` — Credentials, Tokens

---

## Häufige Stolperfallen

### 1. LLM paraphrasiert Platzhalter
**Symptom:** Rehydrierte Antwort enthält noch Platzhalter, oder schlimmer: enthält „die genannte Person" ohne PII.
**Fix:** Die System-Prompt-Klausel oben verstärken. Bei schwächeren Modellen zusätzlich 1-2 Few-Shot-Beispiele zeigen, wie Platzhalter erhalten bleiben.

### 2. Session-ID / Kontext geht nach `Apply Redaction` verloren
**Symptom:** Memory-Node wirft „session key undefined" oder Chat-Memory persistiert nicht.
**Fix:** Im Set-Node ein explizites Assignment ergänzen, das das Feld via `$('Trigger Node Name').item.json.body.sessionId` aus dem Trigger zurückholt. Der Set-Node reicht ungesetzte Felder nicht automatisch durch.

### 3. Mapping-Referenz scheitert in Rehydrate
**Symptom:** `Cannot read property '_privacy_mapping' of undefined` oder das Mapping ist leer.
**Fix:** Den **Apply-Redaction**-Node mit exakt dem konfigurierten Namen referenzieren via `$('Apply Redaction').item.json._privacy_mapping`. Den Node umzubenennen bricht diese Referenz.

### 4. Ungültiges JSON, wenn User-Eingabe Quotes/Newlines enthält
**Symptom:** `Bad Request` von `/redact`, n8n zeigt malformed JSON im Request-Body.
**Fix:** Dynamische Strings immer in `JSON.stringify()` wrappen — siehe Redact-PII-Body-Beispiel. Schreibe **nicht** `"text": "{{ $json.body.chatInput }}"` (Quotes literal eingebettet), schreibe `"text": {{ JSON.stringify($json.body.chatInput) }}` (ohne umgebende Quotes).

### 5. Workflow nutzt `n8n-nodes-base.set` v2.x oder älter
**Symptom:** Andere Parameter-Form; das `assignments`-Feld existiert nicht.
**Fix:** Set-Node v3.x verwenden (Edit Fields). Die Semantik von `keepOnlySet` / `assignments` hat sich in v3 geändert.

---

## Performance-Hinweise

Das `opf`-Modell ist ein 1.5-Mrd-Parameter-PyTorch-Modell auf CPU. **Inferenzzeit skaliert ungefähr linear mit Input-Länge.**

Beobachtete Richtwerte auf einem 4-8-Core-x86_64-Server (keine GPU):

| Input-Länge | `/redact`-Zeit |
|---|---|
| ~100 Zeichen (typische User-Nachricht) | 500 ms – 2 s |
| ~1000 Zeichen | 3 – 6 s |
| ~5000 Zeichen (lange Chat-Historie) | 25 – 30 s |
| Erster Call nach Container-Start | +5-10 s Warmup |

Konsequenzen:

- **Nur das filtern, was gefiltert werden muss.** Pattern B (bedingter Filter) lohnt den zusätzlichen `If`-Node fast immer, wenn Meta-Anfragen ganze Chat-Historien mitschleppen.
- **Inputs auf dem Filter-Pfad kurz halten.** Wenn dein LLM-Schritt einen langen Kontext bekommt (System-Prompt + Historie + User-Nachricht), filtere nur den neuen User-Anteil, nicht den vollen zusammengesetzten Kontext.
- **GPU = 10-50× schneller**, falls verfügbar. In `app/main.py` `device="cpu"` auf `device="cuda"` ändern und das Image mit CUDA-fähigem PyTorch neu bauen.
- **Folge-Calls sind schneller als der erste.** Der Container hält das Modell im Speicher; kein Reload pro Request.

---

## Vollständiges Beispiel: minimaler Pattern-A-Workflow

```
[Webhook]
   │  body: { chatInput, sessionId }
   ▼
[Redact PII] ── HTTP POST /redact, body { text: chatInput }
   │  out: { redacted, mapping, spans }
   ▼
[Apply Redaction] ── Set:
   │     body.chatInput     = $json.redacted
   │     body.sessionId     = $('Webhook').item.json.body.sessionId
   │     _privacy_mapping   = $json.mapping
   │  out: { body: {chatInput, sessionId}, _privacy_mapping }
   ▼
[LLM / AI Agent] ── prompt: $json.body.chatInput
   │  out: { output: "...mit Platzhaltern..." }
   ▼
[Rehydrate] ── HTTP POST /rehydrate,
   │           body { text: $json.output, mapping: $('Apply Redaction').item.json._privacy_mapping }
   │  out: { text: "...mit eingesetzter Original-PII..." }
   ▼
[Respond to Webhook]
```

Das ist das gesamte Pattern. Wiederverwendbar in beliebig vielen Workflows — der Sidecar skaliert, indem du bei Bedarf einfach mehrere Replikate hinter einen Load-Balancer stellst.
