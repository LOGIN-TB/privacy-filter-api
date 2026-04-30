import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from opf import OPF

log = logging.getLogger("uvicorn.error")
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Loading OPF model (first start downloads ~1.5B parameters from HuggingFace)...")
    state["opf"] = OPF(device="cpu", output_text_only=False)
    log.info("OPF model ready.")
    yield
    state.clear()


app = FastAPI(title="privacy-filter-api", version="0.1.0", lifespan=lifespan)


class RedactRequest(BaseModel):
    text: str


class RehydrateRequest(BaseModel):
    text: str
    mapping: dict[str, str]


@app.get("/healthz")
def healthz():
    return {"ok": "opf" in state}


@app.post("/redact")
def redact(req: RedactRequest):
    if "opf" not in state:
        raise HTTPException(503, "model not yet loaded")

    result = state["opf"].redact(req.text)
    spans = sorted(result.detected_spans, key=lambda s: s.start)

    counters: dict[str, int] = defaultdict(int)
    key_to_ph: dict[tuple[str, str], str] = {}
    for span in spans:
        key = (span.label, span.text)
        if key not in key_to_ph:
            counters[span.label] += 1
            key_to_ph[key] = f"<{span.label.upper()}_{counters[span.label]}>"

    redacted = req.text
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        ph = key_to_ph[(span.label, span.text)]
        redacted = redacted[: span.start] + ph + redacted[span.end :]

    mapping = {ph: original for (_, original), ph in key_to_ph.items()}

    return {
        "redacted": redacted,
        "mapping": mapping,
        "spans": [
            {
                "label": s.label,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "placeholder": key_to_ph[(s.label, s.text)],
            }
            for s in spans
        ],
    }


@app.post("/rehydrate")
def rehydrate(req: RehydrateRequest):
    out = req.text
    for placeholder, original in sorted(
        req.mapping.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        out = out.replace(placeholder, original)
    return {"text": out}
