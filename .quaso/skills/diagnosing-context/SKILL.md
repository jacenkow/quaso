---
name: diagnosing-context
description: Work out where the context went and if compaction is behaving
---

# Diagnosing context

The window is the binding constraint. When a session degrades, the
question is always the same: what is filling it, and is compaction
firing when it should.

## Where you are now

`/context` reports usage against the limit. The banner shows the
effective window and the model's maximum. In code:

```python
from quaso.config import load_config, context_limit
from quaso.context import estimate_messages
```

Two numbers can disagree and only one is real. `context_limit(config)`
is what quaso compacts against *and* what the provider allocates, since
it is sent as `num_ctx` on every request. If they ever differ, that is
the bug.

## What is filling it

Tool results, almost always. Check the transcript rather than guessing:

```python
import json
from pathlib import Path
from quaso.messages import Message
from quaso.context import estimate_tokens

path = sorted(Path(".quaso/sessions").glob("*.jsonl"))[-1]
messages = [
    Message.model_validate(json.loads(line))
    for line in path.read_text().splitlines() if line.strip()
]
for m in sorted(messages, key=lambda m: len(m.content), reverse=True)[:5]:
    print(m.tool_name or m.role, estimate_tokens(m.content))
```

If one tool dominates, lower its budget in `agent.tool_output_chars`
rather than the global cap. The full text still survives in
`.quaso/tool-output/`.

## Is compaction working

Three things stop it firing:

- `auto_compact = false`.
- Usage has not grown enough since the last compaction. There is a
  deliberate guard against re-summarising every iteration.
- The turn ended before the next provider call. Compaction runs at the
  start of an iteration, so a session can sit above the threshold until
  you send something.

When it does fire and the window stays full, the kept tail is too big:
lower `keep_recent_messages`.

## Things that look like bugs and are not

- Estimates are 4 chars per token, optimistic for code and JSON. A real
  `prompt_eval_count` from the provider overrides it once a turn lands.
- Compaction rewrites the transcript in place, so an old session file
  shrinks. The summary is the durable record.
- Usage above 100% means the estimate and the real window disagree, not
  that something overflowed.
