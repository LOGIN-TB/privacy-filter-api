# Integrating privacy-filter-api into n8n Workflows

A drop-in pattern for redacting PII **before** an LLM call and rehydrating it **after**, so the model never sees personal data while the end user still gets a natural response.

> Prerequisite: the `privacy-filter` container is already running and reachable from n8n at `http://privacy-filter:9090` (see [README](README.md) for deployment).

---

## Table of Contents

1. [Concept](#concept)
2. [The three nodes you need](#the-three-nodes-you-need)
3. [Pattern A — Filter every request](#pattern-a--filter-every-request)
4. [Pattern B — Conditional filter (skip cheap meta-requests)](#pattern-b--conditional-filter-skip-cheap-meta-requests)
5. [Node configuration reference](#node-configuration-reference)
6. [System-prompt addition for the LLM](#system-prompt-addition-for-the-llm)
7. [Common pitfalls](#common-pitfalls)
8. [Performance notes](#performance-notes)

---

## Concept

```
              ┌──────────────────┐                    ┌──────────────────┐
   user  ───► │   Redact (POST   │  redacted text     │                  │
   text       │   /redact)       │ ─────────────────► │   LLM / Agent    │
              │   + mapping      │                    │ (sees no PII)    │
              └──────────────────┘                    └──────────┬───────┘
                       │                                         │
                       │ mapping kept in workflow state          │ response w/ placeholders
                       │                                         │
                       └──────────────────┬──────────────────────┘
                                          ▼
                                ┌──────────────────┐
                                │  Rehydrate       │  natural response
                                │  (POST           │ ────────────────────►  user
                                │  /rehydrate)     │
                                └──────────────────┘
```

The contract:

- `POST /redact` returns `{ redacted, mapping, spans }`. Placeholders are **indexed** (`<PRIVATE_PERSON_1>`, `<PRIVATE_PERSON_2>`, …) so even multiple PIIs of the same type can be rehydrated unambiguously.
- `POST /rehydrate` is the inverse: takes `{ text, mapping }` and returns `{ text }` with placeholders replaced.
- The mapping must travel from before the LLM call to after it — typically by storing it on the workflow item and reading it via `$('NodeName').item.json` later.

---

## The three nodes you need

| # | Node type | Name (suggestion) | What it does |
|---|---|---|---|
| 1 | **HTTP Request** | `Redact PII` | Calls `POST /redact` with the user's text. |
| 2 | **Set** (Edit Fields) | `Apply Redaction` | Replaces the original text with the redacted version on the workflow item, and stores the mapping for later. |
| 3 | **HTTP Request** | `Rehydrate` | Calls `POST /rehydrate` with the LLM's output and the stored mapping. |

That's it. No Code node, no Function node — just three vanilla nodes.

---

## Pattern A — Filter every request

Use this when **every** request the workflow receives may contain PII (e.g. all user messages flow through one webhook with no fanout).

```
   Trigger ─► Redact PII ─► Apply Redaction ─► LLM / Agent ─► Rehydrate ─► Respond
```

Pros: simplest topology, single code path.
Cons: every request pays the redaction cost — even ones that don't need it (e.g. system prompts, internal tasks).

---

## Pattern B — Conditional filter (skip cheap meta-requests)

Use this when your trigger receives **two kinds** of traffic:

- Real user messages → must be filtered.
- Internal/meta requests (e.g. tag-generation, title-generation, system tasks) → typically short-lived, low-stakes, often contain only material that's already been filtered. Skipping them avoids burning seconds of CPU on the redactor.

```
                    ┌─► Cheap LLM ──────────────────────────────────────┐
                    │   (meta-tasks)                                    │
   Trigger ─► If ───┤                                                   ├─► Respond
                    │                                                   │
                    └─► Redact PII ─► Apply Redaction ─► Main LLM ─► Rehydrate
                        (real user queries)
```

Pros: meta-requests are 20-30× faster (no model inference on long histories).
Cons: **the meta-branch sees PII**. Acceptable when the LLM provider is the same one already handling the main answer (you don't expose to a *new* third party), unacceptable for hard-isolation requirements.

The `If` condition decides which branch to take — usually a `startsWith` check on a marker like `### Task:` or a flag in the request payload.

---

## Node configuration reference

Below: each node with the parameters you set in the n8n UI. Fields not listed should remain at defaults.

### 1. `Redact PII` — HTTP Request

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `http://privacy-filter:9090/redact` |
| Send Body | ✅ enabled |
| Body Content Type | `JSON` |
| Specify Body | `Using JSON` |
| JSON | see below |

**JSON body** (replace `body.chatInput` with the path to the user text in your trigger payload):

```
={
  "text": {{ JSON.stringify($json.body.chatInput) }}
}
```

> The `JSON.stringify()` wrapper is **important** — it escapes quotes, newlines, and other special characters so the rendered JSON stays valid even when the user input contains them.

**Output of this node** (what flows out as `$json`):

```json
{
  "redacted": "...text with placeholders...",
  "mapping":  { "<PRIVATE_PERSON_1>": "Hans Müller", "<PRIVATE_EMAIL_1>": "hans@firma.de" },
  "spans":    [ /* per-span detail */ ]
}
```

### 2. `Apply Redaction` — Set (Edit Fields)

The Set node has one job: take the `redacted` text from the previous node and **put it back where the LLM expects to find it** (e.g. `body.chatInput`), while preserving any other request fields (sessionId, etc.) and stashing the mapping for later.

| Assignment | Type | Value |
|---|---|---|
| `body.chatInput` | string | `={{ $json.redacted }}` |
| `body.sessionId` | string | `={{ $('Trigger').item.json.body.sessionId }}` |
| `_privacy_mapping` | object | `={{ $json.mapping }}` |

Adapt the field names to your trigger's payload:

- The first assignment **must** put the redacted text where downstream nodes expect the user text.
- The second assignment (and any further "passthrough" assignments) restore fields the original trigger had that you still need (session IDs, message IDs, user IDs, …). Use `$('Trigger Node Name').item.json.body.fieldName` to reach back to the original payload.
- The third assignment names the mapping field — the convention here is `_privacy_mapping` (leading underscore signals "internal field"), but any name works as long as you reference the same name in the Rehydrate node.

> ⚠ **Why explicit passthrough?** The Set node v3.x by default does **not** include unset fields. If you don't list `sessionId` here, it's gone after this node — and any downstream node that needs it (e.g. a memory node keyed by sessionId) will break.

### 3. `Rehydrate` — HTTP Request

Identical structure to `Redact PII`, with two changes: different URL and a richer body that carries both the LLM's output and the saved mapping.

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `http://privacy-filter:9090/rehydrate` |
| Send Body | ✅ enabled |
| Body Content Type | `JSON` |
| Specify Body | `Using JSON` |

**JSON body**:

```
={
  "text": {{ JSON.stringify($json.output || $json.text) }},
  "mapping": {{ JSON.stringify($('Apply Redaction').item.json._privacy_mapping) }}
}
```

The `$json.output || $json.text` pattern handles two cases: `output` is what an n8n AI Agent returns; `text` is what an LLM Chain returns. If your LLM node uses a different output key, adjust accordingly.

**Output of this node**:

```json
{ "text": "...natural response with original PII restored..." }
```

After this you typically have a Set/Edit Fields node that maps `$json.text` to whatever shape your responder expects.

---

## System-prompt addition for the LLM

The redact-then-rehydrate dance only works if the LLM **preserves placeholders verbatim** in its output. If the model paraphrases `<PRIVATE_PERSON_1>` to "the person mentioned", the rehydration step has nothing to replace.

Add a clause like this to your AI Agent's `systemMessage` (or to the prompt of an LLM Chain):

```
**Privacy placeholders:** The input text and chat history may contain
placeholders such as <PRIVATE_PERSON_1>, <PRIVATE_EMAIL_1>,
<PRIVATE_PHONE_1>, <PRIVATE_DATE_1>, <PRIVATE_ADDRESS_1>,
<PRIVATE_URL_1>, <ACCOUNT_NUMBER_1>, <SECRET_1>. Use them verbatim in
your response where appropriate — do not paraphrase them (e.g. don't
write "the person mentioned"), and do not invent new placeholders.
```

The full set of label prefixes that `opf` produces:

- `PRIVATE_PERSON` — names
- `PRIVATE_EMAIL`
- `PRIVATE_PHONE`
- `PRIVATE_ADDRESS`
- `PRIVATE_URL`
- `PRIVATE_DATE`
- `ACCOUNT_NUMBER`
- `SECRET` — credentials, tokens

---

## Common pitfalls

### 1. LLM paraphrasing placeholders
**Symptom:** Rehydrated response still contains placeholders, or worse, contains "the person mentioned" with no PII at all.
**Fix:** Strengthen the system-prompt clause above. For weaker models, also include 1-2 few-shot examples showing placeholder preservation.

### 2. Lost session ID / context after `Apply Redaction`
**Symptom:** Memory node throws "session key undefined" or chat memory doesn't persist.
**Fix:** Add an explicit assignment in the Set node restoring the field from the trigger via `$('Trigger Node Name').item.json.body.sessionId`. The Set node does not pass through unset fields by default.

### 3. Mapping reference fails in Rehydrate
**Symptom:** `Cannot read property '_privacy_mapping' of undefined` or the mapping is empty.
**Fix:** Ensure you reference the **Apply Redaction** node by its exact name with `$('Apply Redaction').item.json._privacy_mapping`. Renaming the node breaks this reference.

### 4. Invalid JSON when user input contains quotes/newlines
**Symptom:** `Bad Request` from `/redact`, n8n shows malformed JSON in the request body.
**Fix:** Always wrap dynamic strings in `JSON.stringify()` — see the Redact PII body example. Don't write `"text": "{{ $json.body.chatInput }}"` (quotes embedded literally), do write `"text": {{ JSON.stringify($json.body.chatInput) }}` (no surrounding quotes).

### 5. Workflow uses `n8n-nodes-base.set` v2.x or older
**Symptom:** Different parameter shape; `assignments` field doesn't exist.
**Fix:** Use Set node v3.x (Edit Fields). The `keepOnlySet` / `assignments` semantics changed in v3.

---

## Performance notes

The `opf` model is a 1.5B-parameter PyTorch model running on CPU. **Inference time scales roughly linearly with input length.**

Approximate timing on a 4-8 core x86_64 server (no GPU), based on observed runs:

| Input length | `/redact` time |
|---|---|
| ~100 chars (typical user message) | 500 ms – 2 s |
| ~1000 chars | 3 – 6 s |
| ~5000 chars (long chat history) | 25 – 30 s |
| First call after container start | +5-10 s warmup |

Implications:

- **Filter only what needs filtering.** Pattern B (conditional filter) is usually worth the extra `If` node when meta-requests carry full chat histories.
- **Keep inputs short on the filter path.** If your LLM step receives a long context (system prompt + history + user message), filter only the new user portion, not the full assembled context.
- **GPU is a 10-50× win** if available. Change `device="cpu"` to `device="cuda"` in `app/main.py` and rebuild the image with CUDA-enabled PyTorch.
- **Subsequent calls are faster than the first.** The container keeps the model in memory; no per-request reload.

---

## Worked example: minimal Pattern A workflow

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
   │  out: { output: "...with placeholders..." }
   ▼
[Rehydrate] ── HTTP POST /rehydrate,
   │           body { text: $json.output, mapping: $('Apply Redaction').item.json._privacy_mapping }
   │  out: { text: "...with original PII..." }
   ▼
[Respond to Webhook]
```

That's the whole pattern. Reuse it across as many workflows as you like — the sidecar scales by simply running multiple replicas behind a load balancer if needed.
