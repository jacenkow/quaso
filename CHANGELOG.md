# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `web_search` and `fetch_url` ask before the first one in a session.
  Neither mutates anything, which is why they ran freely, and neither
  needed to: a search is how something you pasted leaves the machine,
  inside a query string. Answering "always" settles it for the session.

### Added

- `/prompt` shows the system prompt the model was actually given, and
  `/prompt all` the whole conversation. It is the one thing a closed
  harness cannot show you, and until now this one could not either.


## [0.1.0]

First release.

### The agent

- A loop that depends only on interfaces, communicating through a typed
  event stream, so providers, tools and frontends are interchangeable.
- Ollama through its native API, with `num_ctx` sent on every request so
  the window the server allocates and the window the agent compacts
  against are the same number.
- Fourteen tools: files, search, a shell, the web, a todo list, a
  subagent, self-compaction, skills, and asking the user a question.
- Independent tool calls in a turn run together. About a third of
  tool-calling turns ask for more than one, and those are almost all
  reads and fetches, which wait on disk or the network rather than the
  model.
- Context compaction that keeps tool calls beside their results, with a
  token-based guard against compacting repeatedly to no effect.
- Tool output bounded from both ends rather than the head alone, since a
  head-only cut loses whatever a command says last, which for a test
  runner is the summary and the exit code. Anything over the budget is
  written under `.quaso/tool-output/` and the elision says where.

### Confinement

- Shell commands run confined where the platform allows it: Seatbelt on
  macOS, bubblewrap on Linux. Writes are limited to the working
  directory and the temporary directory, credential paths such as
  `~/.ssh` are unreachable, and the confinement covers whatever the
  command spawns rather than only the command itself. A machine with
  neither mechanism runs unconfined and says so at startup.
- Reads and writes outside the working directory are refused without
  asking, in every mode. `--mode yolo` stops the questions about your
  project, not the question about leaving it.
- Transcripts, spilled tool output, the config and the prompt history
  are readable only by their owner.
- The sandbox covers shell commands. The file tools run inside quaso's
  own process, where there is no child to confine, so for those the
  workspace boundary is the only control.

### Extending it

- Skills: instructions the model loads on demand. Only a name and a
  description sit in the prompt; the body arrives when the model asks
  for it. They can be targeted at a model family by name prefix.
- Tools and providers can be added as entry points. A broken plugin is
  set aside rather than allowed to stop the session.
- MCP servers, and shell hooks around tool calls.
- A Nix flake, so `nix run github:jacenkow/quaso` needs nothing
  installed, and `install.sh` for everyone else.

### Known limits

- `task` and `compact` are rarely called by the models measured, so
  their prompt guidance costs tokens for little return.
- `fetch_url` validates a host and then connects again, leaving a window
  for DNS rebinding.
- On Linux, bubblewrap needs unprivileged user namespaces, which Ubuntu
  23.10 and later restrict. quaso detects this and falls back to running
  unconfined rather than failing every command.
