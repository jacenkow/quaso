"""Bounding tool output.

Command output puts its verdict last. A head-only cut drops the pytest
summary line and the exit code, which are the two things the model most
needs, so bounding keeps both ends.
"""

from __future__ import annotations

import re

import pytest

from quaso.tools.base import truncate

PYTEST_RUN = (
    "\n".join(f"tests/test_mod.py::test_{i} PASSED" for i in range(400))
    + "\n\n=== 3 failed, 397 passed in 12.4s ===\n[exit code: 1]"
)


class TestShortInput:
    def test_text_under_the_limit_is_unchanged(self):
        assert truncate("hello", 100) == "hello"

    def test_text_exactly_at_the_limit_is_unchanged(self):
        text = "x" * 50
        assert truncate(text, 50) == text

    def test_empty_text_is_unchanged(self):
        assert truncate("", 100) == ""


class TestBothEndsSurvive:
    def test_the_verdict_at_the_end_is_kept(self):
        """The regression this change exists for."""
        out = truncate(PYTEST_RUN, 2_000)
        assert "3 failed, 397 passed" in out
        assert "[exit code: 1]" in out

    def test_the_beginning_is_kept(self):
        out = truncate(PYTEST_RUN, 2_000)
        assert "test_0 PASSED" in out

    def test_the_middle_is_what_goes(self):
        out = truncate(PYTEST_RUN, 2_000)
        assert "test_200 PASSED" not in out

    def test_the_elision_reports_exactly_what_was_dropped(self):
        out = truncate(PYTEST_RUN, 2_000)
        elision = re.search(
            r"\n\.\.\. \[truncated (\d+) chars\] \.\.\.\n", out
        )
        assert elision, out
        kept = len(out) - len(elision.group(0))
        assert int(elision.group(1)) == len(PYTEST_RUN) - kept


class TestBounded:
    @pytest.mark.parametrize("limit", [40, 100, 500, 2_000, 10_000])
    def test_output_never_exceeds_the_limit(self, limit):
        """A budget the caller cannot rely on is not a budget."""
        assert len(truncate(PYTEST_RUN, limit)) <= limit

    def test_a_limit_too_small_for_an_elision_still_bounds(self):
        assert len(truncate(PYTEST_RUN, 10)) <= 10

    def test_zero_limit_yields_nothing(self):
        assert truncate(PYTEST_RUN, 0) == ""

    def test_negative_limit_yields_nothing(self):
        assert truncate(PYTEST_RUN, -5) == ""


class TestReadability:
    def test_cuts_land_on_line_boundaries_when_possible(self):
        out = truncate(PYTEST_RUN, 2_000)
        body = [line for line in out.splitlines() if "truncated" not in line]
        partial = [
            line
            for line in body
            if line and not line.startswith(("tests/", "===", "[exit"))
        ]
        assert partial == [], f"cut mid-line: {partial[:3]}"

    def test_text_without_newlines_is_still_bounded(self):
        blob = "x" * 10_000
        out = truncate(blob, 500)
        assert len(out) <= 500
        assert out.startswith("x")
        assert out.endswith("x")

    def test_unicode_is_not_broken(self):
        text = "héllo wörld ✨\n" * 500
        out = truncate(text, 300)
        assert len(out) <= 300
        out.encode("utf-8")  # would raise on a damaged surrogate


class TestHeadBias:
    def test_more_is_kept_from_the_front_than_the_back(self):
        """The start carries what was run; the end carries the verdict."""
        lines = "\n".join(f"line-{i:04d}" for i in range(2_000))
        out = truncate(lines, 1_000)
        head, _, tail = out.partition("truncated")
        assert len(head) > len(tail)
        assert "line-0000" in head
        assert "line-1999" in tail
