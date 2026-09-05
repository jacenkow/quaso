---
name: adding-a-tool
description: Add a tool to quaso, including the easily missed registry steps
---

# Adding a tool

Six steps. Steps 3 and 4 are the ones that get forgotten, and both fail
quietly: a tool missing from the registry is simply invisible, and a
tool without `paths()` is invisible to the permission layer instead,
which is worse.

## 1. Define the parameters

A pydantic model. Every field needs a `description`: it is what the model
reads to decide how to call you.

```python
class ReadThingParams(BaseModel):
    path: str = Field(description="Path to the thing")
    limit: int = Field(default=100, gt=0, description="Maximum entries")
```

## 2. Write the tool

Subclass `Tool` in `src/quaso/tools/`. Set `mutates = True` if it writes
anything or runs a command, and give it an output budget if the default
is wrong for it.

```python
class ReadThing(Tool):
    name = "read_thing"
    description = "One sentence the model will act on."
    Params = ReadThingParams
    mutates = False
    max_output_chars = 6_000
```

Raise `ToolError` for expected failures; the message goes back to the
model. Do not catch and return a string.

## 3. Register it

Add the class to `_BUILTINS` in `src/quaso/tools/registry.py`. Nothing
else discovers it.

## 4. Declare the paths it touches

If any parameter is a filesystem path, implement `paths()`:

```python
    def paths(self, params: ReadThingParams) -> list[str]:
        return [params.path]
```

Without this the permission layer cannot tell that a call reaches
outside the working directory, and it will silently allow reads that
should have prompted. This is a security step, not a formality.

## 5. Do not bound your own output

Return the full text. `Agent._execute` applies the budget and keeps the
overflow in `.quaso/tool-output/`. Bounding inside the tool discards the
text before anything can keep a copy of it.

Shaping is still yours: line windows, match caps, sensible ordering.

## 6. Test it

In `tests/`, offline. Cover the happy path, a `ToolError` case, and the
workspace boundary if it touches files. See `template.py` in this skill's
directory for a starting point.

## Check yourself

- `quaso` then `/tools`, is it listed?
- Does a call reaching outside the project prompt?
- `pytest` and `ruff check src/ tests/` clean.
