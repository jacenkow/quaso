"""Project instructions discovery.

AGENTS.md is the cross-tool convention, so a repository that already
carries instructions for another agent works here without changes.
"""

from __future__ import annotations

from quaso.agent.prompts import build_system_prompt, find_project_instructions


def test_no_instructions_file_is_fine(tmp_path):
    assert find_project_instructions(tmp_path) is None
    prompt = build_system_prompt(tmp_path)
    assert "Project instructions" not in prompt


def test_agents_md_is_picked_up(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use tabs, obviously.")
    assert find_project_instructions(tmp_path).name == "AGENTS.md"
    prompt = build_system_prompt(tmp_path)
    assert "Use tabs, obviously." in prompt
    assert "AGENTS.md" in prompt


def test_quaso_md_still_works(tmp_path):
    (tmp_path / "QUASO.md").write_text("Quaso-only guidance.")
    assert find_project_instructions(tmp_path).name == "QUASO.md"
    assert "Quaso-only guidance." in build_system_prompt(tmp_path)


def test_quaso_md_wins_when_both_exist(tmp_path):
    """The agent-specific file is the more deliberate choice."""
    (tmp_path / "AGENTS.md").write_text("For any agent.")
    (tmp_path / "QUASO.md").write_text("For quaso only.")
    prompt = build_system_prompt(tmp_path)
    assert "For quaso only." in prompt
    assert "For any agent." not in prompt


def test_a_directory_named_like_the_file_is_ignored(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    assert find_project_instructions(tmp_path) is None
