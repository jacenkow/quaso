"""The CI workflow parses.

GitHub is the authority on this and rejects a malformed file outright,
so this test is not the safety net: it is the fast one. A quoting
mistake here otherwise costs a push, a wait, and a run that fails
before it starts.

Skipped where PyYAML is absent rather than made a dependency, since it
would be one carried by everyone to protect a file only maintainers
touch.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML not installed")

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github"


def _files():
    return sorted((WORKFLOWS / "workflows").glob("*.yml"))


def test_there_is_a_workflow():
    assert _files(), "no workflows found; has the path moved?"


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_it_parses(path):
    yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_every_step_has_something_to_run(path):
    """A step with neither `run` nor `uses` is a typo that GitHub only
    complains about once the job reaches it."""
    document = yaml.safe_load(path.read_text())
    for name, job in document["jobs"].items():
        for step in job.get("steps", []):
            assert "run" in step or "uses" in step, (
                f"{path.name}: step {step.get('name', '?')!r} in {name}"
            )
