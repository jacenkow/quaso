"""Instructions and skills are read without asking anyone's permission.

Both are loaded straight off disk during startup, before the model has
said anything, so nothing routes them through PermissionPolicy. That is
fine while they come from inside the tree they were found in, and a
symlink is the way out of it.
"""

from __future__ import annotations

from pathlib import Path

from quaso.agent.prompts import build_system_prompt, find_project_instructions
from quaso.skills import SkillStore

SECRET = "PRIVATE-KEY-CONTENTS-abc123"


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / "id_rsa"
    target.write_text(SECRET)
    return target


class TestProjectInstructions:
    def test_a_symlink_out_of_the_tree_is_not_read(self, tmp_path):
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "AGENTS.md").symlink_to(_outside(tmp_path))

        assert find_project_instructions(workspace) is None
        assert SECRET not in build_system_prompt(workspace)

    def test_a_real_file_is_still_read(self, tmp_path):
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text("project rules here")
        assert find_project_instructions(workspace) is not None
        assert "project rules here" in build_system_prompt(workspace)

    def test_a_symlink_inside_the_tree_is_fine(self, tmp_path):
        """Pointing at a file in the same project is not an escape."""
        workspace = tmp_path / "project"
        (workspace / "docs").mkdir(parents=True)
        real = workspace / "docs" / "rules.md"
        real.write_text("project rules here")
        (workspace / "AGENTS.md").symlink_to(real)
        assert "project rules here" in build_system_prompt(workspace)


class TestSkillDiscovery:
    def test_a_symlinked_skill_out_of_the_root_is_skipped(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.md").write_text(
            f"---\nname: leak\ndescription: d\n---\n{SECRET}"
        )
        root = tmp_path / "project" / ".quaso" / "skills"
        root.mkdir(parents=True)
        (root / "leak.md").symlink_to(outside / "leak.md")

        assert SkillStore([root]).list() == []

    def test_a_symlink_within_the_root_is_fine(self, tmp_path):
        root = tmp_path / "skills"
        (root / "shared").mkdir(parents=True)
        real = root / "shared" / "real.md"
        real.write_text("---\nname: real\ndescription: d\n---\nbody")
        (root / "linked.md").symlink_to(real)
        assert [s.name for s in SkillStore([root]).list()] == ["real"]

    def test_a_user_root_outside_the_project_still_works(self, tmp_path):
        """The personal skills directory is meant to be elsewhere."""
        home_root = tmp_path / "home" / ".config" / "quaso" / "skills"
        home_root.mkdir(parents=True)
        (home_root / "mine.md").write_text(
            "---\nname: mine\ndescription: d\n---\nbody"
        )
        assert [s.name for s in SkillStore([home_root]).list()] == ["mine"]
