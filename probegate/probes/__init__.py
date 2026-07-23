"""Probe layer — machine-checkable probes the gate *calls* (not reinvents).

This package owns the "Autohand Code" probe layer: each probe takes a
:class:`~probegate.models.Span` and returns a
:class:`~probegate.models.ProbeResult` that is machine-verifiable — compile
errors, failing tests, lint violations, schema mismatches. The gate ANDs
these with the model's uncertainty; it never trusts the probe alone either.
"""
from .base import Probe

__all__ = ["Probe"]
