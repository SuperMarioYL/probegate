"""v0.7.0 regression — the three version-reporting surfaces agree.

Bug fixed (fix-version-drift-v060, high confidence, severity medium): the
v0.6.0 commit (9f22432) shipped the fetch-degrade fixes but forgot to bump
any of the three version-reporting surfaces — pyproject.toml
``[project] version``, ``probegate.__version__``, and the FastAPI
``app.version`` all stayed stale at ``0.5.0``. So the v0.6.0 release
masqueraded as 0.5.0 to ``probegate --version``, ``GET /api/health``,
``pip show probegate``, and ``import probegate; probegate.__version__``.
Every prior iteration bumped the version on ship; v0.6.0 was the first to
ship with stale strings.

This test pins the single-source-of-truth invariant the v0.6.0 ship broke:
all three surfaces MUST report the same version string, so a future ship
cannot silently regress one surface while bumping the others.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import probegate
from probegate.ui.web import app


def _pyproject_version() -> str:
    """Read the ``[project] version`` from pyproject.toml (the packaging metadata)."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def test_all_three_version_surfaces_agree() -> None:
    # The v0.6.0 ship left these three stale at "0.5.0" while the tag said
    # v0.6.0. They MUST now agree on a single string.
    import_version = probegate.__version__
    packaging_version = _pyproject_version()
    app_version = app.version
    assert import_version == packaging_version, (
        f"probegate.__version__ ({import_version}) != pyproject.toml version "
        f"({packaging_version}) — a version-bump surface was left stale"
    )
    assert import_version == app_version, (
        f"probegate.__version__ ({import_version}) != FastAPI app.version "
        f"({app_version}) — a version-bump surface was left stale"
    )


def test_shipped_version_is_v0_7_0() -> None:
    # The v0.7.0 release tag MUST be reflected in every reporting surface
    # (the v0.6.0 release reported 0.5.0 — the exact drift this fix closes).
    assert probegate.__version__ == "0.7.0"
    assert _pyproject_version() == "0.7.0"
    assert app.version == "0.7.0"
