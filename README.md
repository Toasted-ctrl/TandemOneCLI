# Tandem One CLI

An interactive command-line client for SynapseAPI. Tandem One CLI connects to your
SynapseAPI account, lists the chat models you can use, and lets you chat with them
from an interactive prompt.

> **Status:** early development (v0.0.1).

## Acknowledgements

This project was realized in part with the help of AI.

## Features

- Authenticates against SynapseAPI with an API key and greets you by name
- Fetches the chat-completion models available to you, grouped by provider
- Interactive prompt with slash commands to select a provider and model
- Streams replies to the terminal as they're generated, with a spinner while you wait,
  and formats them as Markdown: headings, lists, tables and syntax-highlighted code
- Remembers the conversation, so the model can refer back to earlier messages
- Asks for approval before running any shell command the model requests, showing the exact command first
- Runs read-only commands and commands you've allowed without asking
- Runs those commands in a sandbox that can only change the directory you started in
- Formatted terminal output via [Rich](https://github.com/Textualize/rich)

## Quick start

Requires Linux, Python 3.14+, [uv](https://docs.astral.sh/uv/) (recommended),
[bubblewrap](https://github.com/containers/bubblewrap) (to run the model's commands), and a
SynapseAPI account with an API key.

```bash
git clone <repo-url> tandemonecli
cd tandemonecli
uv sync
cp .env.example .env   # then set SYNAPSE_API_KEY and SYNAPSE_API_URL
uv run tandemone
```

To use `tandemone` from any directory, install it as a tool and move your settings to
the user config directory:

```bash
uv tool install --editable .
mkdir -p ~/.config/tandemone && cp .env ~/.config/tandemone/.env
cd ~/some/project && tandemone
```

The directory you start `tandemone` in is the project root: the model is told about it,
and every command it runs starts there.

For step-by-step setup, configuration details, slash commands, and troubleshooting, see
[GETTING_STARTED.md](GETTING_STARTED.md).

## How it works with SynapseAPI

Tandem One CLI is a client for SynapseAPI. It sends your key in the `X-API-Key`
header with every request. It currently uses these endpoints:

| Endpoint                                     | Purpose                                  |
| -------------------------------------------- | ---------------------------------------- |
| `GET /api/v1/user/me`                        | Fetch the authenticated user's profile   |
| `GET /api/v1/providers/models`               | List available models per provider       |
| `POST /api/v1/inference/conversation/stream` | Send a message or command results, and stream the reply |

The CLI only shows models of type `chat_completion`.

Replies are streamed as server-sent events. The first event of each reply contains the
conversation's `thread_id`, which the CLI sends with your next message to continue the
conversation.

Every request includes a `bash` tool in `client_tools`. This is a tool that the CLI runs
itself, not SynapseAPI. When the model calls it, SynapseAPI pauses the model and ends the
stream with a `tool_request` event that lists the calls. The CLI runs them and sends the
results back as `tool_results` (instead of a `prompt`) on the same thread, so the model
continues where it stopped.

## Running commands

The model can ask to run shell commands on your machine through its `bash` tool. Each
command is shown to you exactly as written, and it runs only if you approve it with `y`.
The default is no, so pressing Enter declines. The command's output is sent back to the
model. If you decline, the model is told right away that the command didn't run, and it
replies before you type your next message.

Commands run in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox:

- The project root (the directory you started `tandemone` in) is the only directory a
  command can change. It starts there.
- `/usr`, `/etc` and `/opt` are read-only, so system programs, DNS and TLS work.
- Your home directory and `/tmp` are empty inside the sandbox, and anything written
  there is thrown away after the command. The rest of your files aren't visible, and
  symlinks pointing out of the project lead nowhere.
- The command can't see or signal your other processes.
- The network is available, so installs, `git fetch` and API calls work.
- Only a few environment variables (such as `LANG` and `TERM`) are passed in, so
  secrets in your environment don't reach commands.

[uv](https://docs.astral.sh/uv/) works in the sandbox even if it's installed in your
home directory, so the model can run commands like `uv run pytest`:

- The `uv` and `uvx` programs are available read-only. Nothing else in their directory
  (such as `~/.local/bin`) is visible.
- uv's cache and the Pythons it installed are available through a throwaway overlay.
  Commands use what's already there, but anything they add or change is thrown away
  after the command. A command can't plant a package that uv would use later, outside
  the sandbox. Packages installed into the project's `.venv` are kept, because the
  `.venv` is in the project.
- The cache and Python directories are uv's defaults. `UV_CACHE_DIR`,
  `UV_PYTHON_INSTALL_DIR`, `XDG_CACHE_HOME` and `XDG_DATA_HOME` are respected. Settings
  in uv's config files (`uv.toml`) outside the project aren't.

Other tools installed in your home directory (for example in `~/.cargo` or nvm) and
your personal config (such as `~/.gitconfig`) aren't available inside the sandbox.

If the sandbox can't be used, commands don't run at all, and the model is told why.
This happens if bubblewrap isn't installed, if the kernel doesn't allow it, or if you
start `tandemone` in `/` or your home directory. The CLI shows this at startup.

The model needs to support tool calling. Models without it reply in text only and can't
run commands.

### Allowed commands

Some commands run without asking. They're still shown, marked `Running allowed command:`.

- **Built-in read-only commands:** `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`,
  `find`, `tree`, `du`, `stat`, `diff`, `sort`, `cut`, `tr`, `pwd`, `cd`, `echo` and a
  few more, and `git status`, `log`, `diff`, `show`, `blame`, `ls-files` and
  `rev-parse`. Options that write files or run other programs need approval, such as
  `find -exec` or `-delete`, `rg --pre`, `sort -o`, `tree -o` and `git diff --output`.
- **Your own rules:** when you're asked to approve a command, answer `a` to run it and
  always allow commands that start the same way in this project. For
  `uv run pytest -q tests`, the rule is `uv run pytest`, and the prompt shows it before
  you answer.
  `a` isn't offered for commands made of several parts, or for built-in commands with
  blocked options (such as `find . -delete`), because the rule would allow those
  options from then on.

A command made of several parts, like `ls | grep x` or `cd src && ls`, runs without
asking only if every part is allowed. Commands that use `$`, backticks, redirects
(other than `2>&1` and `>/dev/null`), subshells, background jobs or several lines
always need approval, because what they run can't be checked reliably.

The rules are saved in `~/.config/tandemone/permissions.json`, which you can edit:

```json
{
  "allow_read_only_commands": true,
  "allowed_commands": ["make test"],
  "projects": {
    "/home/you/projects/app": {
      "allowed_commands": ["uv run pytest", "npm test"]
    }
  }
}
```

- `allow_read_only_commands`: set to `false` to approve the built-in read-only
  commands yourself.
- `allowed_commands`: rules for every project.
- `projects`: rules per project, by the project root's full path.

A rule matches whole words at the start of a command: `npm test` allows
`npm test --watch`, but not `npm tests`. Commands can't see or change this file,
because it's outside the sandbox. If the file isn't valid, the CLI says why and asks
about every command until you fix it.

Be careful which commands you allow. A rule like `uv run pytest`, `npm test` or `make`
runs code from the project, which the model can change in another command, so allowing
it lets the model run anything inside the sandbox.

## Project structure

```text
src/tandemonecli/
├── cli.py              # Entry point and interactive loop
├── config/
│   ├── paths.py        # Where tandemone's config lives (~/.config/tandemone)
│   └── settings.py     # Settings from ~/.config/tandemone/.env or .env
├── startup/
│   ├── user.py         # Fetches the current user from SynapseAPI
│   └── models.py       # Fetches the available chat models
├── prompts/
│   └── system.py       # System prompt sent with every message
├── slash/
│   └── handler.py      # Slash commands, selection and conversation state
├── stream/
│   ├── client.py       # Streams replies and sends tool results to SynapseAPI
│   └── markdown.py     # Prints streamed replies as Markdown
├── permissions.py      # Which commands run without asking (permissions.json)
├── text.py             # Helpers for safely printing untrusted text
├── workspace.py        # The project root (the directory tandemone started in)
└── tools/
    ├── bash.py         # The bash tool: its definition, running commands
    ├── sandbox.py      # The bubblewrap sandbox commands run in
    └── shell.py        # Splits commands into parts, to check them against rules

tests/                  # Test suite
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development environment
and the commands to lint, format, type-check and test the project.

## Roadmap

- [x] Send chat messages to the selected model through SynapseAPI
- [x] Stream responses to the terminal
- [ ] Show a clear message when the API key is missing or invalid
- [x] Make the SynapseAPI base URL configurable
- [x] Allow commands that can run without approval
- [x] Add a test suite

## License

Tandem One CLI is released under the [MIT License](LICENSE).
