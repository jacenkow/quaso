# AGENTS.md

Instructions for any coding agent working in this repository. The first
section is general and worth copying into other projects; the second is
specific to quaso.

## Working on a codebase

- Make the smallest change that solves the problem. Do not reformat,
  rename or restructure code you were not asked to touch: it buries the
  real change in noise.
- Do not report success you have not observed. Run the test, run the
  command, read the output. "Should work" is not a result.
- Check a symbol exists before calling it. Guessing a function signature
  from its name is the most common way to produce code that looks right
  and does not run.
- Match the conventions of the file you are editing, including its
  quoting, naming and comment density, even where they differ from your
  own habits.
- When two approaches are both reasonable, say so and pick one, rather
  than choosing silently. Being told the trade-off is useful; discovering
  it later is not.
- New dependencies need a real justification. Prefer the standard
  library, and prefer a few lines of code to a package.

## This project

quaso is a terminal coding agent for self-hosted models, written in
Python. Its binding constraint is a small context window, so prefer
solutions that spend fewer tokens per turn.

### Invariants

- The agent loop depends only on interfaces: `Provider`, `Tool`, `UI`,
  `PermissionPolicy`, `ContextManager`. Never import a concrete provider,
  tool or frontend into `agent/`.
- Everything reaches a frontend as a typed event from `events.py`. Add an
  event rather than teaching the loop about a UI.
- Tool parameters are pydantic models; schemas are generated from them.
- Invalid tool arguments are returned to the model as tool output so it
  can correct itself. Do not raise.
- `context_limit(config)` is the only source of truth for the window, and
  the provider is sent exactly that as `num_ctx`.
- A tool that touches files must implement `paths()`, or the permission
  layer cannot see where it reaches.

### Style

- PEP 8, 79 columns. `ruff check` and `ruff format` must both pass.
- Double quotes for strings, single quotes for dict access:
  `config['model']` and `{"model": "qwen3.6"}`. `ruff format` runs with
  `quote-style = "preserve"` so this survives formatting, but nothing
  enforces it.
- Write a comment only for a constraint the code cannot show. Never
  restate what the next line does, and never explain why a change is
  correct: that belongs in the commit message.
- No banner comments. Docstrings are one or two lines, or absent.

### Gotchas that have already bitten

- rich parses `[...]` as markup and eats it. Pass `markup=False` and
  `highlight=False` when printing file contents, model output or a model
  name.
- Compaction must not separate a tool call from its result, and must not
  need the context it is reclaiming.
- Never compact while iterating an assistant turn's tool calls; defer
  until every result is recorded, or the calls are orphaned.
- Truncation keeps both ends. A command's verdict is its last line.
- Anything reaching outside the working directory needs approval, even a
  read.

### Tests and commits

- `tests/` is offline. Mock HTTP with respx; drive the loop with the fake
  provider in `conftest.py`. Write the failing test first for a bug fix.
- Assert behaviour, not mechanism: that a secret does not appear in
  output, not that a particular exception was raised.
- `pytest` and `ruff check src/ tests/` clean before committing.
- Conventional Commits, scope mirrors the package: `fix(tools):`,
  `feat(context):`. Mark breaking changes with `!`. Say what the change
  guards against, not which lines moved.

Keep this file short: it is in the prompt on every turn.
