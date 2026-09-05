# Security

## What this tool does

quaso lets a language model read and write files, run shell commands, and
fetch pages from the internet, on your machine, with your permissions.
That is the point of it, and it is worth being clear-eyed about what it
means.

**`bash` runs confined where the platform allows it.** On macOS through
Seatbelt, on Linux through bubblewrap when it is installed. Writes are
limited to the working directory and the temporary directory, credential
paths such as `~/.ssh` and `~/.aws` are unreadable, and the confinement
applies to whatever the command itself spawns. `[sandbox] mode = "off"`
disables it, and a machine with neither mechanism available runs
unconfined and says so at startup.

**The sandbox covers shell commands and nothing else.** The file tools
run inside quaso's own process, so there is no child process to confine.
For those the workspace boundary in the permission layer is the only
control.

That boundary holds in every mode. `--mode yolo` stops the questions
about your project, not the question about leaving it: a read or a write
outside the working directory is still asked, because nothing else would
catch it. What `yolo` costs you is the prompt before something inside
the project is changed, which is recoverable, rather than the prompt
before your keys are read, which is not.

**Nothing leaves the machine without being asked.** The model runs
locally, so what you paste stays with you, and the two tools that reach
the internet, `web_search` and `fetch_url`, ask before the first one in
a session. Neither changes anything on disk, which is why they were
waved through before: a search mutates nothing and is still the way a
private thing gets out, inside a query string. Answer "always" to stop
being asked for the rest of the session, or put `allow = ["web_search"]`
in the config to stop being asked at all.

**Web content is untrusted.** Pages fetched by `fetch_url` are labelled as
data in the prompt, and the model is told not to act on instructions found
in them, but prompt injection is not a solved problem. Treat anything the
agent read from the internet as something it may have been influenced by.

**Reads are bounded to the working directory.** Anything touching a path
outside it asks first, even for a tool that only reads, because reading a
file and sending it somewhere are otherwise both silent operations.
`bash` can still reach anywhere, but `bash` always asks.

**MCP servers are third-party code.** They run as subprocesses with your
permissions. `quaso mcp list` shows what a configured server exposes
before you rely on it.

## Reducing the blast radius

- `--mode readonly` refuses every mutating tool. Reads outside the working
  directory still ask.
- `deny` rules in `[permissions]` are checked before anything else. Use
  `deny = ["bash"]` to disable shell execution reliably. Command-scoped shell
  patterns are lexical safeguards, not a sandbox; compound syntax is refused
  when such a deny rule is configured.
- `require_read_before_edit` (on by default) stops the model overwriting a
  file it has not read, or one that changed underneath it.
- Run it in a container or VM if you want a real boundary. Nothing in this
  project provides one.

## Reporting something

This is a spare-time project with no security team behind it. Open an
issue, or email the address on the commits if you would rather not do so
publicly. Please do not expect a fast response.
