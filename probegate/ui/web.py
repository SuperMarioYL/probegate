"""ProbeGate local web UI — FastAPI per-span card view.

The web view is the m2 surface, but it ships in m1 so the same
:class:`~probegate.gate.ProbeGate` can be inspected visually. It serves a
static ``gate.html`` (no Jinja2 dep) that calls two JSON endpoints:

* ``GET  /api/demo``  — run the built-in 5-step agent, return spans + decisions.
* ``POST /api/guard`` — run the gate over caller-supplied spans.

Run with ``probegate ui`` (which calls ``uvicorn`` on this module's ``app``).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..gate import ProbeGate
from ..models import ProbeGateConfig, Span

app = FastAPI(
    title="ProbeGate",
    version="0.1.0",
    description="Per-span probe-validation gate for 国产模型 autonomous agents.",
)

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "gate.html"


class GuardRequest(BaseModel):
    """A POST body for ``/api/guard``."""

    spans: list[Span]
    uncertainty_threshold: float = 0.5
    probe: str = "compile"
    model_target: str = "deepseek-coder"


@app.get("/")
def index() -> FileResponse:
    """Serve the per-span web view (vanilla JS, no server-side templating)."""
    return FileResponse(_TEMPLATE)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/config")
def get_config() -> dict[str, object]:
    cfg = ProbeGateConfig()
    return cfg.model_dump()


@app.get("/api/demo")
def demo() -> dict[str, object]:
    """Run the built-in 5-step demo agent and return spans + decisions."""
    # local import avoids an import-time cycle (cli -> web -> cli)
    from ..cli import build_demo_spans

    spans = build_demo_spans()
    gate = ProbeGate()
    decisions = [gate.guard(s).model_dump() for s in spans]
    return {"spans": [s.model_dump() for s in spans], "decisions": decisions}


@app.post("/api/guard")
def guard(req: GuardRequest) -> dict[str, object]:
    """Run the gate over caller-supplied spans."""
    gate = ProbeGate(
        uncertainty_threshold=req.uncertainty_threshold,
        probe=req.probe,
        model_target=req.model_target,
    )
    decisions = [gate.guard(s).model_dump() for s in req.spans]
    return {"decisions": decisions}


@app.post("/api/approve")
def approve(span_id: str) -> JSONResponse:
    """Record an operator approval of a handoff span (m2: persists audit log)."""
    # m1: acknowledge only; the m2 audit-log store plugs in here.
    return JSONResponse({"span_id": span_id, "action": "approved", "rule": "proceed"})


@app.post("/api/reject")
def reject(span_id: str) -> JSONResponse:
    """Record an operator rejection — the agent should rewind this span."""
    return JSONResponse({"span_id": span_id, "action": "rejected", "rule": "rewind"})
