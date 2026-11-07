# Getting Started with Tandem One CLI

This guide takes you from a fresh clone to your first chat with a model through
SynapseAPI. For a full reference, see the [README](README.md). If you want to
contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).

## 1. Check the prerequisites

- **Python 3.14 or newer**
- **[uv](https://docs.astral.sh/uv/)** (recommended for installing dependencies and running the CLI)
- **A SynapseAPI account**, with an API key and the URL of your SynapseAPI server

You can check your Python version with:

```bash
python3 --version
```

## 2. Install the project

Clone the repository and install its dependencies:

```bash
git clone <repo-url> tandemonecli
cd tandemonecli
uv sync
```

## 3. Configure your credentials

The CLI reads its settings from a `.env` file in the project root. Create one from the
example file:

```bash
cp .env.example .env
```

Open `.env` and set your own values:

| Setting           | Description                                                            |
| ----------------- | ---------------------------------------------------------------------- |
| `SYNAPSE_API_KEY` | Your SynapseAPI API key                                                |
| `SYNAPSE_API_URL` | Base URL of your SynapseAPI server, e.g. `https://synapse.example.com` |

Both settings are required. Environment variables with the same names take precedence
over the `.env` file.

> **Keep your key private.** `.env` is listed in `.gitignore`, so it won't be committed.
> Never paste your API key into commits, issues, or screenshots.

## 4. Start the CLI

Run either entry point:

```bash
uv run tandemone
# or
uv run tandemonecli
```

On startup, the CLI:

1. Looks up your user profile and greets you by name.
2. Fetches the chat models available to you.
3. Lists the providers, for example `openai` and `anthropic`.
4. Opens an interactive prompt.

## 5. Pick a provider and model

You must choose a provider before you can choose a model:

```text
> /provider openai
> /model <model-name>
```

Once both are set, the prompt shows your selection, for example `> [ openai / gpt-... ]`.

## 6. Send your first message

Type a message and press Enter. The reply streams into the terminal as it is generated.

The CLI keeps track of the conversation, so the model can refer back to earlier
messages. This still works if you switch providers or models mid-conversation.

## 7. Approve commands the model runs

The model can ask to run shell commands on your machine. When it does, the CLI shows the
exact command and asks `Run it?`. Type `y` to run it. The default is no, so pressing
Enter declines.

The command's output is sent back to the model so it can continue. If you decline, the
model is told straight away that the command didn't run, and it replies before you type
your next message.

Running commands needs a model that supports tool calling.

Every command needs your approval. Only approve commands you have read and trust.
Whitelisting commands that can run without approval is planned for a future release.

## Slash commands

| Command             | What it does                                          |
| ------------------- | ----------------------------------------------------- |
| `/provider <name>`  | Select a provider and list its models                 |
| `/model <name>`     | Select a model from the current provider              |
| `/new`              | Start a new conversation                              |
| `/clear`            | Clear the current provider and model selection        |
| `/help`             | Show each command and what it does                    |
| `exit` or `Ctrl+C`  | Quit the CLI                                          |

## Troubleshooting

- **The CLI exits immediately or reports a missing setting:** check that `.env` exists
  in the project root and that both `SYNAPSE_API_KEY` and `SYNAPSE_API_URL` are set.
- **You can't select a model:** select a provider first with `/provider <name>`.
- **Authentication errors:** confirm that your API key is correct and still active on
  your SynapseAPI account.

## Next steps

- Read the [README](README.md) for the full feature list and how the CLI talks to
  SynapseAPI.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) to set up a development environment and
  learn the lint, format, type-check, and test commands.
