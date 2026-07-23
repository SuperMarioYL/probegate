"""Schema probe — validates a span's output against an expected shape.

m1 minimal-but-real: a span may declare the schema it must satisfy by
embedding a JSON object in a fenced ``probegate:schema`` block, e.g.::

    ```probegate:schema
    {"required": ["endpoint", "method"]}
    ```

The probe then checks that the span's *payload* (a JSON object in a
``probegate:payload`` block, or the whole span content if none) contains
every ``required`` key with the declared type. A missing key or a type
mismatch fails the probe with the offending field. This is the machine-
checkable guard against an agent that hallucinates an SDK call: the schema
says ``endpoint`` must be a string, the span returns a dict without it, and
the probe fails before the agent commits.

m3 will add JSON-Schema full validation; the seam stays identical.
"""
from __future__ import annotations

import json
import re

from ..models import ProbeResult, Span
from .base import Probe

_FENCE = re.compile(
    r"```probegate:(schema|payload)\n(.*?)```", re.DOTALL
)

# python type name -> checker
_TYPE_CHECK = {
    "string": lambda v: isinstance(v, str),
    "str": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "list": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "dict": lambda v: isinstance(v, dict),
}


class SchemaProbe(Probe):
    name = "schema"

    def run(self, span: Span) -> ProbeResult:
        blocks: dict[str, str] = {}
        for kind, body in _FENCE.findall(span.content):
            blocks[kind] = body.strip()

        schema_src = blocks.get("schema")
        if not schema_src:
            return ProbeResult(
                probe="schema",
                passed=True,
                evidence="no `probegate:schema` block — nothing to validate against",
            )

        try:
            schema = json.loads(schema_src)
        except json.JSONDecodeError as exc:
            return ProbeResult(
                probe="schema",
                passed=False,
                evidence=f"schema block is not valid JSON: {exc.msg}",
            )
        if not isinstance(schema, dict):
            return ProbeResult(
                probe="schema",
                passed=False,
                evidence="schema block must be a JSON object",
            )

        payload_src = blocks.get("payload")
        if payload_src is not None:
            try:
                payload = json.loads(payload_src)
            except json.JSONDecodeError as exc:
                return ProbeResult(
                    probe="schema",
                    passed=False,
                    evidence=f"payload block is not valid JSON: {exc.msg}",
                )
        else:
            # fall back to the whole span content as JSON
            try:
                payload = json.loads(span.content)
            except json.JSONDecodeError:
                return ProbeResult(
                    probe="schema",
                    passed=False,
                    evidence=(
                        "no `probegate:payload` block and span content is not JSON; "
                        "cannot check schema"
                    ),
                )

        if not isinstance(payload, dict):
            return ProbeResult(
                probe="schema",
                passed=False,
                evidence="payload must be a JSON object to check required keys",
            )

        required = schema.get("required", [])
        types = schema.get("types", {})
        problems: list[str] = []
        for key in required:
            if key not in payload:
                problems.append(f"missing required key `{key}`")
                continue
            expected = types.get(key)
            if expected and expected in _TYPE_CHECK:
                if not _TYPE_CHECK[expected](payload[key]):
                    problems.append(
                        f"`{key}` expected {expected}, got {type(payload[key]).__name__}"
                    )

        if problems:
            return ProbeResult(probe="schema", passed=False, evidence="; ".join(problems))
        return ProbeResult(
            probe="schema",
            passed=True,
            evidence=f"{len(required)} required key(s) present and typed",
        )
