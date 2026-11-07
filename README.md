# Tandem One CLI

An interactive command-line client for SynapseAPI. Tandem One CLI connects to your
SynapseAPI account, lists the chat models you can use, and lets you chat with them
from an interactive prompt.

> **Status:** early development (v0.0.1).

## Features

- Authenticates against SynapseAPI with an API key and greets you by name
- Fetches the chat-completion models available to you, grouped by provider
- Interactive prompt with slash commands to select a provider and model
- Streams replies to the terminal as they're generated, with a spinner while you wait
- Remembers the conversation, so the model can refer back to earlier messages
- Asks for approval before running any shell command the model requests, showing the exact command first
- Formatted terminal output via [Rich](https://github.com/Textualize/rich)

## Quick start

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/) (recommended), and a SynapseAPI
account with an API key.

```bash
git clone <repo-url> tandemonecli
cd tandemonecli
uv sync
cp .env.example .env   # then set SYNAPSE_API_KEY and SYNAPSE_API_URL
uv run tandemone
```

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

The model needs to support tool calling. Models without it reply in text only and can't
run commands.

Every command needs its own approval for now. Whitelisting commands that can run without
approval is planned for a future release.

## Project structure

```text
src/tandemonecli/
├── cli.py              # Entry point and interactive loop
├── config/
│   └── settings.py     # Settings loaded from .env (pydantic-settings)
├── startup/
│   ├── user.py         # Fetches the current user from SynapseAPI
│   └── models.py       # Fetches the available chat models
├── prompts/
│   └── system.py       # System prompt sent with every message
├── slash/
│   └── handler.py      # Slash commands, selection and conversation state
├── stream/
│   └── client.py       # Streams replies and sends tool results to SynapseAPI
├── text.py             # Helpers for safely printing untrusted text
└── tools/
    └── bash.py         # The bash tool: its definition, running commands

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
- [ ] Whitelist commands that can run without approval
- [x] Add a test suite

## License

Tandem One CLI is released under the [MIT License](LICENSE).
