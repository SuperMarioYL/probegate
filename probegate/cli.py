"""ProbeGate CLI — ``probegate``.

Subcommands:
* ``init``     — write a ``.probegate.toml`` config.
* ``demo``     — run a built-in 5-step coding agent where step 4 deliberately
                 edits a wrong function signature; the gate fires ``handoff``.
* ``gate``     — run the gate over a jsonl of fake spans (or the built-in
                 demo spans via ``--demo``) and print per-span proceed/abstain/
                 handoff.
* ``ui``       — serve the local FastAPI per-span web view (m2 surface, m1
                 renders a working card list over the same GateDecision).

Designed for argparse + rich only (no extra deps beyond the plan).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .gate import ProbeGate
from .models import ProbeGateConfig, Span

console = Console()

# --------------------------------------------------------------------------
# Built-in demo spans — a deliberately-wrong 5-step coding agent.
# --------------------------------------------------------------------------

# Step 4 is the cascade seed: the model hallucinates a function signature
# with mismatched parens (compile fails) AND self-reports high uncertainty.
# Step 5 is the cascade — the unguarded agent would build on the broken span.

_DEMO_SPANS = [
    Span(
        id="span-1",
        agent_step=1,
        content=(
            "def add(a, b):\n"
            "    return a + b\n"
        ),
        uncertainty=0.08,
    ),
    Span(
        id="span-2",
        agent_step=2,
        content=(
            "def to_json(obj):\n"
            "    return json.dumps(obj)\n"
        ),
        uncertainty=0.12,
    ),
    Span(
        id="span-3",
        agent_step=3,
        content=(
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "    assert add(-1, 1) == 0\n"
        ),
        uncertainty=0.21,
    ),
    Span(
        id="span-4",
        agent_step=4,
        # deliberate signature mismatch — parens never close
        content="def render(payload, options:\n    return str(payload)\n",
        uncertainty=0.82,
    ),
    Span(
        id="span-5",
        agent_step=5,
        # the cascade — builds on the broken step 4; probe still fails
        content="def render_all(items):\n    return [render(i, for i in items]\n",
        uncertainty=0.74,
    ),
]


def build_demo_spans() -> list[Span]:
    """Return the built-in 5-step demo agent trajectory (step 4 is the seed)."""
    return [s.model_copy() for s in _DEMO_SPANS]


# --------------------------------------------------------------------------
# Config I/O
# --------------------------------------------------------------------------

def _toml_dump(cfg: ProbeGateConfig) -> str:
    """Tiny TOML writer (avoids a tomli-w dep for this 6-key config)."""
    lines = [
        "# ProbeGate config — per-span dual-signal abstention gate",
        "# handoff <=> uncertainty > threshold AND probe failed",
        "",
        f"uncertainty_threshold = {cfg.uncertainty_threshold}",
        f'probe = "{cfg.probe}"',
        f'model_target = "{cfg.model_target}"',
        f'handoff_mode = "{cfg.handoff_mode}"',
        f'api_key = "{cfg.api_key or ""}"',
        f'base_url = "{cfg.base_url or ""}"',
    ]
    return "\n".join(lines) + "\n"


def cmd_init(args: argparse.Namespace) -> int:
    cfg = ProbeGateConfig(
        uncertainty_threshold=args.threshold,
        probe=args.probe,  # type: ignore[arg-type]
        model_target=args.model,
    )
    path = Path(".probegate.toml")
    if path.exists() and not args.force:
        console.print(f"[yellow]{path} already exists — use --force to overwrite[/]")
        return 1
    path.write_text(_toml_dump(cfg), encoding="utf-8")
    console.print(f"[green]wrote[/] {path}")
    console.print(
        Panel(
            "[dim]edit the api_key / base_url lines to point at your 国产模型 serving tier.[/]",
            title="next step",
            border_style="blue",
        )
    )
    return 0


# --------------------------------------------------------------------------
# Span loading (jsonl)
# --------------------------------------------------------------------------

def _load_spans(path: str) -> list[Span]:
    spans: list[Span] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            spans.append(Span(**obj))
    if not spans:
        raise ValueError(f"no spans parsed from {path}")
    return spans


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_RULE_STYLE = {
    "proceed": "green",
    "abstain": "yellow",
    "handoff": "bold red",
}


def _print_decisions_table(decisions: list[Any], *, interactive: bool) -> int:
    """Print one row per span; returns count of handoffs needing action."""
    table = Table(title="ProbeGate — per-span decisions", show_lines=True)
    table.add_column("step", justify="right", style="cyan", no_wrap=True)
    table.add_column("span id", style="dim")
    table.add_column("uncertainty", justify="right")
    table.add_column("probe", justify="center")
    table.add_column("rule", justify="center")
    table.add_column("rationale")

    handoffs = 0
    for d in decisions:
        style = _RULE_STYLE.get(d.rule, "white")
        probe = d.probe.probe if d.probe else "-"
        passed = "PASS" if (d.probe and d.probe.passed) else "FAIL"
        unc = f"{d.uncertainty:.2f}" if d.uncertainty is not None else "-"
        table.add_row(
            "—",
            d.span_id,
            unc,
            f"{probe} {passed}",
            f"[{style}]{d.rule}[/{style}]",
            d.rationale,
        )
        if d.rule == "handoff":
            handoffs += 1

    console.print(table)
    return handoffs


def _print_decision_panel(decision: Any) -> None:
    style = _RULE_STYLE.get(decision.rule, "white")
    probe = decision.probe
    body = [
        f"[bold]span[/]      {decision.span_id}",
        f"[bold]uncertainty[/] {decision.uncertainty:.2f}"
        if decision.uncertainty is not None
        else "[bold]uncertainty[/] —",
        f"[bold]probe[/]     {probe.probe} → "
        f"{'PASS' if probe.passed else 'FAIL'}"
        if probe
        else "[bold]probe[/]     —",
        f"[bold]rule[/]      [{style}]{decision.rule}[/{style}]",
        "",
        f"[dim]{decision.rationale}[/]",
    ]
    console.print(Panel("\n".join(body), title=f"span {decision.span_id}", border_style=style))


def _interactive_handoff(decision: Any) -> bool:
    """Surface a handoff to the operator; returns True = approve (proceed)."""
    _print_decision_panel(decision)
    console.print(
        Align.center(
            f"[bold red]handoff[/]: span {decision.span_id} flagged for review"
        )
    )
    return Confirm.ask("approve edit? (y=proceed, N=abstain+rewind)", default=False)


# --------------------------------------------------------------------------
# Subcommands: demo / gate / ui
# --------------------------------------------------------------------------

def cmd_demo(args: argparse.Namespace) -> int:
    spans = build_demo_spans()
    gate = ProbeGate(
        uncertainty_threshold=args.threshold,
        probe=args.probe,
        model_target=args.model,
    )
    console.print(
        Panel(
            f"[bold]ProbeGate demo[/]\n"
            f"model={args.model}  probe={args.probe}  tau={args.threshold}\n"
            f"5-step coding agent — step 4 deliberately edits a broken signature.",
            border_style="magenta",
        )
    )
    handoffs = 0
    for span in spans:
        decision = gate.guard(span)
        _print_decision_panel(decision)
        if decision.rule == "handoff":
            handoffs += 1
            if args.interactive:
                approved = _interactive_handoff(decision)
                console.print(
                    f"  → operator {'approved' if approved else 'rejected'} span {span.id}"
                )
    console.print(
        f"\n[bold]summary:[/] {len(spans)} spans, {handoffs} handoff(s)."
    )
    if handoffs and not args.interactive:
        console.print(
            "[dim]re-run with --interactive to approve/reject each handoff span.[/]"
        )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    if args.demo:
        spans = build_demo_spans()
    elif args.fake_spans:
        spans = _load_spans(args.fake_spans)
    else:
        console.print("[red]error:[/] provide --fake-spans FILE or --demo")
        return 2
    gate = ProbeGate(
        uncertainty_threshold=args.threshold,
        probe=args.probe,
        model_target=args.model,
    )
    decisions = gate.guard_many(spans)
    handoffs = _print_decisions_table(decisions, interactive=args.interactive)
    if args.interactive:
        for d in decisions:
            if d.rule == "handoff":
                approved = _interactive_handoff(d)
                console.print(
                    f"  → operator {'approved' if approved else 'rejected'} span {d.span_id}"
                )
    console.print(
        f"\n[bold]summary:[/] {len(spans)} spans, "
        f"{sum(1 for d in decisions if d.rule == 'proceed')} proceed, "
        f"{sum(1 for d in decisions if d.rule == 'abstain')} abstain, "
        f"{handoffs} handoff."
    )
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    # import lazily so the CLI doesn't drag FastAPI/uvicorn into `gate`/`demo`
    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        console.print("[red]uvicorn not installed — `pip install probegate[web]`[/]")
        return 1
    from .ui import web  # noqa: F401 — importing registers routes

    console.print(
        Panel(
            f"[bold]ProbeGate web UI[/]\n"
            f"serving on http://{args.host}:{args.port}\n"
            f"open the page and watch the per-span gate cards render.",
            border_style="blue",
        )
    )
    uvicorn.run(
        "probegate.ui.web:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level=args.log_level,
    )
    return 0


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="probegate",
        description=(
            "ProbeGate — per-span probe-validation gate for 国产模型 agents. "
            "Routes to a human ONLY when self-reported uncertainty AND a "
            "machine-checkable probe both fire."
        ),
    )
    p.add_argument("--version", action="version", version=f"probegate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="write a .probegate.toml config")
    p_init.add_argument("--threshold", type=float, default=0.5, help="uncertainty tau (0..1)")
    p_init.add_argument("--probe", default="compile", choices=["compile", "test", "lint", "schema"])
    p_init.add_argument("--model", default="deepseek-coder")
    p_init.add_argument("--force", action="store_true", help="overwrite existing config")
    p_init.set_defaults(func=cmd_init)

    # demo
    p_demo = sub.add_parser("demo", help="run the built-in 5-step demo agent")
    p_demo.add_argument("--model", default="deepseek-coder")
    p_demo.add_argument("--probe", default="compile", choices=["compile", "test", "lint", "schema"])
    p_demo.add_argument("--threshold", type=float, default=0.5)
    p_demo.add_argument("--interactive", action="store_true", help="prompt on every handoff span")
    p_demo.set_defaults(func=cmd_demo)

    # gate
    p_gate = sub.add_parser("gate", help="run the gate over fake spans")
    src = p_gate.add_mutually_exclusive_group(required=True)
    src.add_argument("--fake-spans", metavar="FILE", help="jsonl of spans")
    src.add_argument("--demo", action="store_true", help="use built-in demo spans")
    p_gate.add_argument("--probe", default="compile", choices=["compile", "test", "lint", "schema"])
    p_gate.add_argument("--threshold", type=float, default=0.5)
    p_gate.add_argument("--model", default="deepseek-coder")
    p_gate.add_argument("--interactive", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    # ui
    p_ui = sub.add_parser("ui", help="serve the local per-span web view")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8000)
    p_ui.add_argument("--log-level", default="info")
    p_ui.set_defaults(func=cmd_ui)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        return 130
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]error:[/] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
