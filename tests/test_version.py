"""Version lockstep regression — the three version-reporting surfaces agree.

v0.7.0 (fix-version-drift-v060): the v0.6.0 commit (9f22432) shipped the
fetch-degrade fixes but forgot to bump any of the three version-reporting
surfaces — pyproject.toml ``[project] version``, ``probegate.__version__``,
and the FastAPI ``app.version`` all stayed stale at ``0.5.0``. So the v0.6.0
release masqueraded as 0.5.0 to ``probegate --version``, ``GET /api/health``,
``pip show probegate``, and ``import probegate; probegate.__version__``.

v0.8.0 (fix-release-branch-drift): the v0.7.0 release itself then shipped
from an unmerged ``v0.7.0-dev`` branch, so ``main`` never received the
version bump or this lockstep test — a default-branch install still reported
``0.5.0``. This test is re-landed on ``main`` as part of the v0.8.0 ship.

These tests pin the single-source-of-truth invariant: all three surfaces
MUST report the same version string, so a future ship cannot silently
regress one surface while bumping the others.
"""
from __future__ import annotations

from pathlib import Path

# tomllib is stdlib on >=3.11; the pyproject floor is 3.10 (tomli is a
# declared dependency there), so mirror the guarded import probegate/cli.py
# uses. An unguarded ``import tomllib`` broke the 3.10 CI matrix — the exact
# defect the v0.8.0 re-landing of this file exposed on main.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — py3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

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


def test_shipped_version_is_v0_8_0() -> None:
    # The v0.8.0 release tag MUST be reflected in every reporting surface
    # (v0.6.0 reported 0.5.0 and v0.7.0 never reached the default branch —
    # the exact drift this lineage of fixes closes).
    assert probegate.__version__ == "0.8.0"
    assert _pyproject_version() == "0.8.0"
    assert app.version == "0.8.0"
