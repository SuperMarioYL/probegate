"""Probe base class.

A probe is a *machine-checkable* verifier: it returns ``passed=True`` only
when it can prove the span is OK by running real tooling (a compiler, a test
runner, a linter, a schema validator). Self-reported reasoning does not count
— that is the whole point of the dual-signal gate.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ProbeResult, Span


class Probe(ABC):
    """Abstract probe. Subclasses set ``name`` and implement :meth:`run`."""

    name: str = "base"

    @abstractmethod
    def run(self, span: Span) -> ProbeResult:
        """Return a machine-verifiable verdict for ``span``."""
        raise NotImplementedError
