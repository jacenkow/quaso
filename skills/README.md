# Starter skills

A small set of general-purpose skills, written for the failure modes of
local models rather than adapted from collections aimed at frontier
ones: over-reading, guessing an interface, changing more than asked, and
reporting success that was never observed.

They apply to any language and any project. Install them with:

```bash
mkdir -p ~/.config/quaso/skills
cp skills/*.md ~/.config/quaso/skills/
```

Project-specific skills go in `.quaso/skills/` instead, where they
shadow these.

## Writing one

A skill is `<name>.md`, or `<name>/SKILL.md` if it ships resources
alongside, with frontmatter:

```markdown
---
name: exploring-a-codebase
description: One line; this is what the model chooses on
models: qwen3, gemma4      # optional, omit to apply everywhere
---
```

The description is the only part present in every prompt, so it carries
the whole discovery burden. The body loads only when the model calls the
skill tool, so it can afford to be thorough.

`models` filters by prefix, so `qwen3` matches `qwen3.6:latest`. Use it
only where a skill genuinely depends on a model's behaviour; a skill
that would help any model should not name one.

## Provenance

Original, written for this project. If you add a skill adapted from
elsewhere, record its source and licence in the frontmatter and keep the
original licence file. Attribution is not a licence: content from a
repository with no licence cannot be redistributed at all.
