---
name: exploring-a-codebase
description: Explore an unfamiliar project without filling the context
---

# Exploring a codebase

The failure mode is reading too much. Ten full files will exhaust a small
window before you have learned anything, and what you learn will be
crowded out by what you did not need.

## Order of operations

1. `list_dir` at the root. Names tell you the layout: `src/`, `tests/`,
   a manifest, a README.
2. Read the manifest, not the README. `pyproject.toml`, `package.json`,
   `Cargo.toml` name the entry points, the dependencies and the test
   command in a fraction of the space.
3. `grep` for the thing you actually want. A class name, a route, an
   error string. Searching is cheap; reading is not.
4. Only then `read_file`, and only the range the search pointed at.

## Rules

- Never read a file to find out whether it is relevant. Grep for a symbol
  in it instead.
- A directory listing beats a recursive glob when you do not yet know the
  shape.
- If a search returns more than about twenty hits, the query is too
  broad. Narrow it rather than reading the results.
- Delegate wide, open-ended questions to a subagent so its reading does
  not land in your context.

## Done when

You can name the entry point, the test command, and the file you need to
change. Not before, and not after: further exploration without a target
is where context goes to die.
