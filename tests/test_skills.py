"""Skill discovery and on-demand loading.

The point of the design is that a skill costs a line in the prompt until
the model asks for it, so the tests care most about what is in the index
versus what is in the body.
"""

from __future__ import annotations

import pytest

from quaso.agent.prompts import build_system_prompt
from quaso.messages import user
from quaso.session import Session
from quaso.skills import SkillStore, default_roots, parse_frontmatter
from quaso.tools.base import ToolContext, ToolError
from quaso.tools.skill import STORE_KEY, LoadSkill, SkillParams

BODY = "A very long body. " * 200


def _write(root, name, description, body=BODY, flat=False):
    if flat:
        path = root / f"{name}.md"
        root.mkdir(parents=True, exist_ok=True)
    else:
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    front = f"---\nname: {name}\ndescription: {description}\n---\n"
    path.write_text(front + body)
    return path


class TestFrontmatter:
    def test_fields_and_body_are_separated(self):
        fields, body = parse_frontmatter(
            "---\nname: a\ndescription: b\n---\nthe body\n"
        )
        assert fields == {"name": "a", "description": "b"}
        assert body.strip() == "the body"

    def test_quotes_are_stripped(self):
        fields, _ = parse_frontmatter('---\nname: "a b"\n---\nx')
        assert fields['name'] == "a b"

    def test_text_without_frontmatter_is_all_body(self):
        fields, body = parse_frontmatter("no frontmatter here")
        assert fields == {}
        assert body == "no frontmatter here"

    def test_an_unterminated_fence_is_not_frontmatter(self):
        fields, body = parse_frontmatter("---\nname: a\nbody goes on")
        assert fields == {}
        assert body.startswith("---")


class TestDiscovery:
    def test_directory_style_skills_are_found(self, tmp_path):
        _write(tmp_path, "alpha", "does alpha things")
        assert [s.name for s in SkillStore([tmp_path]).list()] == ["alpha"]

    def test_flat_files_are_found(self, tmp_path):
        _write(tmp_path, "beta", "does beta things", flat=True)
        assert [s.name for s in SkillStore([tmp_path]).list()] == ["beta"]

    def test_a_skill_without_a_description_is_ignored(self, tmp_path):
        path = tmp_path / "gamma" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: gamma\n---\nbody")
        assert SkillStore([tmp_path]).list() == []

    def test_nearest_root_wins(self, tmp_path):
        project, user = tmp_path / "project", tmp_path / "user"
        _write(project, "shared", "the project version")
        _write(user, "shared", "the personal version")
        skills = SkillStore([project, user]).list()
        assert len(skills) == 1
        assert skills[0].description == "the project version"

    def test_missing_roots_are_fine(self, tmp_path):
        assert SkillStore([tmp_path / "absent"]).list() == []

    def test_project_root_comes_first(self, tmp_path):
        roots = default_roots(tmp_path, home=tmp_path / "home")
        assert roots[0] == tmp_path / ".quaso" / "skills"


class TestIndexIsCheap:
    def test_the_index_carries_descriptions_not_bodies(self, tmp_path):
        _write(tmp_path, "alpha", "does alpha things")
        index = SkillStore([tmp_path]).index()
        assert "alpha: does alpha things" in index
        assert BODY.strip()[:40] not in index

    def test_an_empty_store_adds_nothing_to_the_prompt(self, tmp_path):
        assert SkillStore([tmp_path]).index() == ""
        assert "Available skills" not in build_system_prompt(tmp_path)

    def test_the_index_reaches_the_system_prompt(self, tmp_path):
        _write(tmp_path / ".quaso" / "skills", "alpha", "does alpha things")
        store = SkillStore(default_roots(tmp_path, home=tmp_path / "home"))
        prompt = build_system_prompt(tmp_path, store.index())
        assert "alpha: does alpha things" in prompt
        assert BODY.strip()[:40] not in prompt


class TestLoading:
    @pytest.mark.asyncio
    async def test_the_body_arrives_on_demand(self, tmp_path):
        _write(tmp_path, "alpha", "does alpha things")
        ctx = ToolContext(cwd=tmp_path)
        ctx.extra[STORE_KEY] = SkillStore([tmp_path])
        out = await LoadSkill().run(SkillParams(name="alpha"), ctx)
        assert "# Skill: alpha" in out
        assert BODY.strip()[:40] in out

    @pytest.mark.asyncio
    async def test_resources_are_listed_not_inlined(self, tmp_path):
        _write(tmp_path, "alpha", "does alpha things")
        (tmp_path / "alpha" / "template.py").write_text("SECRET = 1\n")
        ctx = ToolContext(cwd=tmp_path)
        ctx.extra[STORE_KEY] = SkillStore([tmp_path])

        out = await LoadSkill().run(SkillParams(name="alpha"), ctx)
        assert "template.py" in out
        assert "SECRET" not in out, "resources are read separately, not dumped"

    @pytest.mark.asyncio
    async def test_an_unknown_skill_lists_the_real_ones(self, tmp_path):
        _write(tmp_path, "alpha", "does alpha things")
        ctx = ToolContext(cwd=tmp_path)
        ctx.extra[STORE_KEY] = SkillStore([tmp_path])
        with pytest.raises(ToolError, match="alpha"):
            await LoadSkill().run(SkillParams(name="nope"), ctx)

    @pytest.mark.asyncio
    async def test_no_store_reports_clearly(self, tmp_path):
        with pytest.raises(ToolError, match="not available"):
            await LoadSkill().run(
                SkillParams(name="alpha"), ToolContext(cwd=tmp_path)
            )


class TestModelTargeting:
    def _skill(self, tmp_path, name, models):
        path = tmp_path / f"{name}.md"
        front = f"---\nname: {name}\ndescription: d\nmodels: {models}\n---\n"
        path.write_text(front + "body")
        return path

    def test_a_skill_without_models_applies_everywhere(self, tmp_path):
        _write(tmp_path, "general", "for anyone", flat=True)
        store = SkillStore([tmp_path])
        assert len(store.list("qwen3.6:latest")) == 1
        assert len(store.list("gemma4:31b")) == 1

    def test_a_named_model_matches_on_prefix(self, tmp_path):
        self._skill(tmp_path, "qwen-only", "qwen3")
        store = SkillStore([tmp_path])
        assert [s.name for s in store.list("qwen3.6:latest")] == ["qwen-only"]
        assert store.list("gemma4:31b") == []

    def test_several_models_may_be_named(self, tmp_path):
        self._skill(tmp_path, "both", "qwen3, gemma4")
        store = SkillStore([tmp_path])
        assert store.list("qwen3.6:latest")
        assert store.list("gemma4:31b")

    def test_matching_ignores_case(self, tmp_path):
        self._skill(tmp_path, "shouty", "QWEN3")
        assert SkillStore([tmp_path]).list("qwen3.6:latest")

    def test_no_model_given_means_no_filtering(self, tmp_path):
        self._skill(tmp_path, "qwen-only", "qwen3")
        assert len(SkillStore([tmp_path]).list()) == 1

    def test_the_index_hides_skills_for_other_models(self, tmp_path):
        self._skill(tmp_path, "qwen-only", "qwen3")
        store = SkillStore([tmp_path])
        assert "qwen-only" in store.index("qwen3.6:latest")
        assert store.index("gemma4:31b") == ""

    @pytest.mark.asyncio
    async def test_loading_by_name_ignores_the_filter(self, tmp_path):
        """An explicit request is its own justification."""
        self._skill(tmp_path, "qwen-only", "qwen3")
        ctx = ToolContext(cwd=tmp_path)
        ctx.extra[STORE_KEY] = SkillStore([tmp_path])
        out = await LoadSkill().run(SkillParams(name="qwen-only"), ctx)
        assert "# Skill: qwen-only" in out


class TestPromptFollowsTheModel:
    def test_switching_model_reindexes_the_skills(self, tmp_path):
        """Targeting is pointless if the prompt keeps the old model's set."""
        root = tmp_path / ".quaso" / "skills"
        root.mkdir(parents=True)
        for name, models in (("for-qwen", "qwen3"), ("for-gemma", "gemma4")):
            (root / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: d\nmodels: {models}\n---\nb"
            )
        store = SkillStore([root])
        session = Session(
            build_system_prompt(tmp_path, store.index("qwen3.6:latest")),
            root=tmp_path,
            persist=False,
        )
        assert "for-qwen" in session.messages[0].content
        assert "for-gemma" not in session.messages[0].content

        session.set_system(
            build_system_prompt(tmp_path, store.index("gemma4:31b"))
        )
        assert "for-gemma" in session.messages[0].content
        assert "for-qwen" not in session.messages[0].content

    def test_set_system_keeps_the_conversation(self, tmp_path):
        session = Session("first", root=tmp_path, persist=False)
        session.append(user("hello"))
        session.set_system("second")
        assert session.messages[0].content == "second"
        assert session.messages[1].content == "hello"
        assert len(session.messages) == 2
