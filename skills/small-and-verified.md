---
name: small-and-verified
description: Make the smallest change, then confirm rather than assume
---

# Small and verified

Two habits fix most of what goes wrong in an agent-written change.

## Make it small

- Change the least that solves the stated problem. Nothing else.
- Do not reformat, rename or reorganise code you were not asked to touch.
  It buries the real change and makes review impossible.
- If a fix touches more than a couple of files, say why before doing it.
- Resist improving things you notice in passing. Mention them instead.

## Verify it

- Run the thing. A test, the command, the script. "Should work" is not a
  result and must never be reported as one.
- Verify with the *same* command that showed the problem. A narrower one
  that passes proves nothing.
- Check the symbol exists before calling it. Inferring a function's
  signature from its name is the most common way to produce code that
  reads correctly and does not run.
- If you could not run it, say so plainly rather than implying you did.

## When you are unsure

Say so, pick the option you think is better, and name the trade-off. A
stated assumption can be corrected. A silent one cannot.
