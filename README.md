# Tandem One CLI

An interactive command-line client for SynapseAPI.
Tandem One CLI connects to your SynapseAPI account, lists the chat models you can use,
and lets you chat with them from an interactive prompt.

> **Status:** early development (v0.0.1).

## Features

- Authenticates against SynapseAPI with an API key and greets you by name
- Fetches the chat-completion models available to you, grouped by provider
- Interactive prompt with slash commands to select a provider and model
- Streams replies to the terminal as they're generated, with a spinner while you wait
- Remembers the conversation, so the model can refer back to earlier messages
- Formatted terminal output via [Rich](https://github.com/Textualize/rich)

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended)
- A SynapseAPI account and API key

## Installation

Clone the repository and install the dependencies:

```bash
git clone <repo-url> tandemonecli
cd tandemonecli
uv sync
```

## Configuration

Tandem One CLI reads its settings from a `.env` file in the project root. Copy the
example file and fill in your own values:

```bash
cp .env.example .env
```

| Setting           | Description                                                  |
| ----------------- | ------------------------------------------------------------ |
| `SYNAPSE_API_KEY` | Your SynapseAPI API key                                      |
| `SYNAPSE_API_URL` | Base URL of your SynapseAPI server, e.g. `https://synapse.example.com` |

Both settings are required. You can also set them as environment variables. If a
setting is in both places, the environment variable takes precedence.

> Don't commit your `.env` file. It is already listed in `.gitignore`.

## Usage

Start the CLI with either entry point:

```bash
uv run tandemone
# or
uv run tandemonecli
```

When it starts, the CLI looks up your user profile, fetches the available models,
lists the providers and then opens an interactive prompt:

```text
Hello <User>! Welcome to Tandem One CLI!

Available providers:
  • openai
  • anthropic

Select one with /provider <name>

Press Ctrl+C to exit, or type exit to exit.
Commands: /provider <name>, /model <name>, /new, /clear, /help. Type /help for details.
>
```

### Slash commands

| Command             | Description                                              |
| ------------------- | -------------------------------------------------------- |
| `/provider <name>`  | Select a provider and list its models                    |
| `/model <name>`     | Select a model from the current provider                 |
| `/new`              | Start a new conversation                                 |
| `/clear`            | Clear the current provider and model selection           |
| `/help`             | Show each command with a description of what it does     |
| `exit` or `Ctrl+C`  | Quit the CLI                                             |

You must select a provider before you can select a model. The prompt shows your
current selection, for example `> [ openai / gpt-... ]`.

### Chatting

Once you've selected a model, type a message and press Enter. The reply streams in as
it's generated.

The CLI remembers your conversation: each message continues the same conversation, so
the model can refer back to what you said earlier. This still works if you switch to a
different provider or model. Use `/new` to start a fresh conversation.

## How it works with SynapseAPI

Tandem One CLI is a client for SynapseAPI. It sends your key in the `X-API-Key`
header with every request. It currently uses these endpoints:

| Endpoint                         | Purpose                                  |
| -------------------------------- | ---------------------------------------- |
| `GET /api/v1/user/me`            | Fetch the authenticated user's profile   |
| `GET /api/v1/providers/models`   | List available models per provider       |
| `POST /api/v1/inference/conversation/stream` | Send a message and stream the reply |

The CLI only shows models of type `chat_completion`.

Replies are streamed as server-sent events. The first event of each reply contains the
conversation's `thread_id`, which the CLI sends with your next message to continue the
conversation.

## Project structure

```text
src/tandemonecli/
├── cli.py              # Entry point and interactive loop
├── config/
│   └── settings.py     # Settings loaded from .env (pydantic-settings)
├── startup/
│   ├── user.py         # Fetches the current user from SynapseAPI
│   └── models.py       # Fetches the available chat models
├── slash/
│   └── handler.py      # Slash commands, selection and conversation state
└── stream/
    └── client.py       # Streams replies from SynapseAPI
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development environment
and the commands to lint, format, type-check and test the project.

## Roadmap

- [x] Send chat messages to the selected model through SynapseAPI
- [x] Stream responses to the terminal
- [ ] Show a clear message when the API key is missing or invalid
- [ ] Make the SynapseAPI base URL configurable
- [x] Add a test suite

## License

Tandem One CLI is released under the [MIT License](LICENSE).
