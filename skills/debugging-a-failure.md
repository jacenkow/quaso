---
name: debugging-a-failure
description: Work from a failing test or error to a fix without guessing
---

# Debugging a failure

The failure mode is changing code before understanding the error. That
produces a second bug on top of the first and loses the evidence.

## Order of operations

1. **Reproduce it.** Run the failing command yourself and read the real
   output. A described failure is not a failure you have seen.
2. **Read the error properly.** The last line names the symptom. The
   frame that matters is usually the deepest one inside the project,
   not the deepest one overall, which is normally library code.
3. **Locate before editing.** Open the file at the line the trace names.
   Confirm the code says what you assumed.
4. **Form one hypothesis** and state it. "The path is relative when it
   should be absolute" is a hypothesis. "Something is wrong with paths"
   is not.
5. **Make the smallest change** that tests it.
6. **Re-run the same command.** Same command, not a narrower one: a
   passing subset proves nothing about the failure you started with.

## Rules

- One change at a time. Two changes and a passing test tells you nothing
  about which mattered.
- If the error mentions a value, print it before assuming it. Guessing
  what a variable held is the most common wasted step.
- If two runs disagree, suspect state on disk or ordering before
  suspecting the code.
- If the fix is not obvious after three hypotheses, the problem is
  probably not where you are looking. Go back to step 2.

## Done when

The original command passes and you can say in one sentence why it
failed.
