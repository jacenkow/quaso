# Contributing

Pull requests would genuinely make my day. This is a hobby project, so
replies may be slow, but nothing gets ignored.

## Getting set up

```bash
git clone git@github.com:jacenkow/quaso.git && cd quaso
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Or `nix develop`, which also brings bubblewrap on Linux so the sandbox
tests have something to exercise.

Before pushing:

```bash
pytest -q
ruff check src/ tests/ && ruff format --check src/ tests/
```

[AGENTS.md](AGENTS.md) has the invariants, the house style and the
mistakes that have already been made once. It is short, and worth the
two minutes.

## What makes a change easy to accept

**A test that fails before and passes after.** More persuasive than any
description of what the change does.

**Verified rather than asserted.** The most useful lesson this project
has produced: a sandbox test that only ever runs sandboxed passes just
as well when the sandbox failed to start and the command never ran. Ask
what else would make your test pass, and rule it out. Anything guarding
a boundary wants a pair, one showing the thing is refused and one
showing ordinary work still goes through.

**Measured, if it is a claim about behaviour.** "The model will call
this tool" is a hypothesis. `evals/` runs tasks against a real model and
grades what the model actually did, including which tools it called.
Two claims in this repository turned out to be false when measured, and
one of those had already been acted on.

**Small.** A change that does one thing gets read properly. A change
that does four gets read once and left.

## Working on particular things

**Tools.** Parameters are a pydantic model, and anything touching files
implements `paths()` or the permission layer cannot see where it
reaches. `.quaso/skills/adding-a-tool/` walks through it.

**The sandbox.** `tests/test_escapes.py` runs the same commands with and
without confinement. The macOS backend can be exercised locally; the
Linux one only runs in CI, so expect to iterate through pull request
runs rather than locally.

**Skills.** A skill adapted from somewhere else needs its source and
licence recorded in `skills/README.md`. A repository without a licence
cannot be redistributed, however small the file.

**Prompts.** Changing what the model is told is a behavioural change, so
it wants an eval rather than an argument. Prompt wording has twice
failed to move a number here that reasoning said it should.

## Commits

Conventional Commits: `fix(security): ...`, `feat(tools): ...`. Say why
in the body, wrapped at 72. What changed is visible in the diff; why it
changed is not, and is what someone will want in a year.

## Reporting things

Bugs and ideas both go in the issues, and there is a template for each.
Security problems have their own path in [SECURITY.md](SECURITY.md).
