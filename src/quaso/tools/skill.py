"""Loading a skill on demand.

The system prompt carries only names and descriptions. This is how the
body gets into the conversation, and it is a tool rather than an
automatic injection so that the cost is paid once, deliberately, for the
one skill that matches the task.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quaso.tools.base import Tool, ToolContext, ToolError

STORE_KEY = "skills"


class SkillParams(BaseModel):
    name: str = Field(description="Name of the skill from the available list")


class LoadSkill(Tool):
    name = "skill"
    description = (
        "Load a skill's full instructions when the task matches one of the "
        "available skills listed in the system prompt. The result may refer "
        "to files in the skill's directory, which you can read as usual."
    )
    Params = SkillParams
    mutates = False

    async def run(self, params: SkillParams, ctx: ToolContext) -> str:
        store = ctx.extra.get(STORE_KEY)
        if store is None:
            raise ToolError("Skills are not available in this session")
        skill = store.get(params.name)
        if skill is None:
            available = ", ".join(s.name for s in store.list()) or "none"
            raise ToolError(
                f"No skill named {params.name!r}. Available: {available}"
            )

        parts = [f"# Skill: {skill.name}", "", skill.body.strip()]
        resources = skill.resources()
        if resources:
            parts += [
                "",
                f"Files in this skill's directory ({skill.directory}):",
                *(f"  {path}" for path in resources),
            ]
        return "\n".join(parts)
