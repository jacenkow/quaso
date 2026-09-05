---
name: reviewing-a-change
description: Review a diff in a useful order, catching the things that hide
---

# Reviewing a change

Read the diff in an order that surfaces problems, not top to bottom.

## Order of operations

1. **What is it for?** If you cannot state the purpose in a sentence,
   the change is too large or the description is too thin.
2. **Does it do only that?** Unrelated reformatting, renaming or
   restructuring buries the real change. Flag it.
3. **The dangerous parts first.** Anything touching authentication,
   permissions, file paths, shell commands, or user input. Bugs there
   cost more than bugs anywhere else.
4. **The error paths.** Most reviews read the happy path. Most bugs are
   not on it.
5. **The tests.** Do they fail if the change is reverted? A test that
   passes either way is decoration.

## Things that hide

- A condition inverted in one branch out of several.
- An `except` broad enough to swallow the failure it was meant to report.
- A default argument that is mutable, or evaluated once.
- Output that is bounded in one path and unbounded in another.
- A check applied to one caller but not its sibling.

## Rules

- Say what you verified, not just what you think. "I ran the tests and
  the new one fails on the old code" beats "looks good".
- Distinguish a defect from a preference, and say which you are raising.
