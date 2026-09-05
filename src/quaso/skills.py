"""Skills: instructions the model loads only when it needs them.

A skill's body can be thousands of tokens, which is unaffordable in every
request. Only the name and description sit in the system prompt; the body
arrives when the model calls the skill tool, and its directory can carry
scripts and references the model then reads with ordinary tools.

A skill is either `<name>/SKILL.md` or a flat `<name>.md`, with
frontmatter between `---` lines:

    ---
    name: adding-a-tool
    description: What this is for, in one line
    ---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quaso.tools.base import contained_by

SKILLS_DIR = "skills"
MAIN_FILE = "SKILL.md"

# Only scalar `key: value` pairs, which is all skill frontmatter needs.
# Anything richer is a signal to take on a YAML dependency, not to grow
# this into one.
_FENCE = "---"
_LISTING_LIMIT = 10


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    models: tuple[str, ...] = ()

    def applies_to(self, model: str) -> bool:
        """Whether this skill is meant for the model in use.

        A skill with no `models:` field applies everywhere. One that names
        models matches on prefix, so `qwen3` covers `qwen3.6:latest`.
        """
        if not self.models:
            return True
        lowered = model.lower()
        return any(lowered.startswith(m.lower()) for m in self.models)

    @property
    def directory(self) -> Path:
        return self.path.parent

    def resources(self) -> list[Path]:
        """Files shipped alongside a directory-style skill."""
        if self.path.name != MAIN_FILE:
            return []
        found = [
            item
            for item in sorted(self.directory.rglob("*"))
            if item.is_file() and item.name != MAIN_FILE
        ]
        return found[:_LISTING_LIMIT]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading `---` frontmatter from the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return {}, text
    try:
        end = next(
            i for i, line in enumerate(lines[1:], 1) if line.strip() == _FENCE
        )
    except StopIteration:
        return {}, text

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator and key.strip():
            fields[key.strip()] = value.strip().strip("\"'")
    return fields, "\n".join(lines[end + 1 :]).lstrip("\n")


def _load(path: Path) -> Skill | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    fields, body = parse_frontmatter(text)
    default = path.parent.name if path.name == MAIN_FILE else path.stem
    name = fields.get('name') or default
    description = fields.get('description', "").strip()
    if not description:
        # Without one the model has nothing to choose on, so the skill
        # would cost prompt space and never be picked.
        return None
    declared = fields.get('models', "")
    models = tuple(m.strip() for m in declared.split(",") if m.strip())
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        models=models,
    )


class SkillStore:
    """Skills found across the search roots, nearest root winning."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def list(self, model: str = "") -> list[Skill]:
        """Skills available, filtered to the model in use when given."""
        found: dict[str, Skill] = {}
        for root in reversed(self.roots):
            for path in self._candidates(root):
                skill = _load(path)
                if skill is not None:
                    found[skill.name] = skill
        chosen = [
            s for s in found.values() if not model or s.applies_to(model)
        ]
        return sorted(chosen, key=lambda skill: skill.name)

    def get(self, name: str) -> Skill | None:
        """By name, unfiltered: an explicit request is its own answer."""
        return next((s for s in self.list() if s.name == name), None)

    def index(self, model: str = "") -> str:
        """The always-present half: what exists, not what it says."""
        skills = self.list(model)
        if not skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in skills]
        return (
            "Available skills. Call the skill tool with a name to load "
            "one when the task matches:\n" + "\n".join(lines)
        )

    @staticmethod
    def _candidates(root: Path) -> list[Path]:
        """Skill files under this root, ignoring any that lead out of it.

        A skill body is quoted to the model on request, so a symlink
        pointing outside the root would read a file nobody authorised.
        The personal root sits outside the project by design, which is
        why containment is judged against the root, not the workspace.
        """
        if not root.is_dir():
            return []
        directories = [
            item / MAIN_FILE
            for item in sorted(root.iterdir())
            if (item / MAIN_FILE).is_file()
        ]
        flat = [
            item
            for item in sorted(root.glob("*.md"))
            if item.name != MAIN_FILE
        ]
        return [p for p in directories + flat if contained_by(p, root)]


def default_roots(cwd: Path, home: Path | None = None) -> list[Path]:
    """Project skills first, so a project can override a personal one."""
    home = home or Path.home()
    return [
        cwd / ".quaso" / SKILLS_DIR,
        home / ".config" / "quaso" / SKILLS_DIR,
    ]
