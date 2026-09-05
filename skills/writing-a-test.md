---
name: writing-a-test
description: Write a test that survives the implementation changing
---

# Writing a test

The failure mode is testing how the code works rather than what it
guarantees. Such a test breaks on every refactor and catches no bugs.

## Shape

Arrange, act, assert. One behaviour per test, named for that behaviour:
`test_reading_outside_the_workspace_prompts`, not `test_read_2`.

## Rules

- **Assert the property, not the mechanism.** That a secret does not
  appear in output, not that a particular exception was raised. The
  first survives a rewrite; the second does not.
- **For a bug fix, write the failing test first.** If you cannot make it
  fail, you have not found the bug.
- **Prefer real objects to mocks.** Mock at the process or network
  boundary and nowhere else. A test full of mocks tests the mocks.
- **Use a temporary directory** for anything touching files. A test that
  writes into the project will pass once and then lie.
- **Test the edges you can name**: empty, one, many, absent, malformed,
  too large. Most bugs live there.

## Check yourself

Would this test fail if the bug came back? Would it survive renaming a
private function? If the answers are not yes and yes, rewrite it.
