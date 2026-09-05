---
name: refactoring-safely
description: Change structure without changing behaviour, staying green
---

# Refactoring safely

The failure mode is a large rewrite that is 90% right, with no way to
find the missing 10%.

## Order of operations

1. **Get to green first.** Run the tests before touching anything. A
   refactor that starts on a red suite has no baseline to compare to.
2. **Cover what you are about to move.** If the behaviour is not tested,
   test it before changing it, not after.
3. **One transformation at a time**, tests green between each. Rename,
   then extract, then inline: not all three at once.
4. **Keep behaviour identical.** If you find a bug, stop and fix it as a
   separate change. A refactor commit that also fixes something is
   impossible to review and impossible to revert.

## Rules

- If the tests cannot run between steps, the steps are too big.
- Move code before rewriting it. A pure move shows up as a rename in the
  diff; a move plus a rewrite shows up as unreviewable churn.
- Delete rather than comment out. History remembers.
- Stop when the original goal is met. "While I was in there" is how a
  refactor becomes a rewrite.

## Done when

The tests pass, the diff shows only the structural change you set out to
make, and you could explain to a reviewer why each hunk is behaviour
preserving.
