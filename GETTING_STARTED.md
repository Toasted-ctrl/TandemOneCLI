# Getting Started with Tandem One CLI

This guide takes you from a fresh clone to your first chat with a model through
SynapseAPI. For a full reference, see the [README](README.md). If you want to
contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).

## 1. Check the prerequisites

- **Linux** with **[bubblewrap](https://github.com/containers/bubblewrap)** (`bwrap`),
  which the model's commands run in. Install it with your package manager, for
  example `sudo pacman -S bubblewrap` or `sudo apt install bubblewrap`.
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

The CLI reads its settings from `~/.config/tandemone/.env` (or
`$XDG_CONFIG_HOME/tandemone/.env` if you set `XDG_CONFIG_HOME`). This works wherever
you run the CLI from. Create it from the example file:

```bash
mkdir -p ~/.config/tandemone
cp .env.example ~/.config/tandemone/.env
```

Open `~/.config/tandemone/.env` and set your own values:

| Setting           | Description                                                            |
| ----------------- | ---------------------------------------------------------------------- |
| `SYNAPSE_API_KEY` | Your SynapseAPI API key                                                |
| `SYNAPSE_API_URL` | Base URL of your SynapseAPI server, e.g. `https://synapse.example.com` |

Both settings are required. Environment variables with the same names take precedence
over the config file.

For development, a `.env` file in the repository root also works. Settings in the user
config take precedence over it.

> **Keep your key private.** The user config is outside the repository, and `.env` is
> listed in `.gitignore`, so neither will be committed. Never paste your API key into
> commits, issues, or screenshots.

## 4. Start the CLI

### From anywhere (recommended)

Install the CLI as a tool from the repository:

```bash
uv tool install --editable .
```

This puts `tandemone` (and `tandemonecli`) in `~/.local/bin`. Then start it in the
project you want to work on:

```bash
cd ~/some/project
tandemone
```

The directory you start it in is the project root: the model is told about it, and
every command it runs starts there and can only change files there (see step 7). Don't
start it in `/` or your home directory: the CLI won't run commands there, because
they could change all your files.

- If your shell can't find `tandemone`, add `~/.local/bin` to your `PATH` with
  `uv tool update-shell` (in fish: `fish_add_path ~/.local/bin`), then open a new
  terminal.
- `--editable` runs the code in your checkout, so changes to the source apply right
  away. After you change the dependencies in `pyproject.toml`, reinstall with
  `uv tool install --editable --reinstall .`
- To remove it: `uv tool uninstall tandemonecli`.

### From the repository

```bash
uv run tandemone
# or
uv run tandemonecli
```

This uses the repository as the project root.

On startup, the CLI:

1. Looks up your user profile, greets you by name, and shows the project root.
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

Type a message and press Enter. The reply streams into the terminal as it is generated,
formatted as Markdown: headings, lists, tables and syntax-highlighted code blocks. Each
part of the reply is formatted once it's complete. Links show their full address.

The CLI keeps track of the conversation, so the model can refer back to earlier
messages. This still works if you switch providers or models mid-conversation.

## 7. Approve commands the model runs

The model can ask to run shell commands on your machine. When it does, the CLI shows the
exact command and asks `Run it?`. Type `y` to run it. The default is no, so pressing
Enter declines.

The command's output is sent back to the model so it can continue. You see the first
5 lines, each cut to the width of your terminal, followed by `✓ Succeeded` or `✗` and
what went wrong. If you decline, the
model is told straight away that the command didn't run, and it replies before you type
your next message.

Commands run in a sandbox. They can only change files in the project root. The rest
of your files aren't visible, and system directories are read-only. The network is
available. uv works, so the model can run commands like `uv run pytest`. Other tools
installed in your home directory, such as those in `~/.cargo/bin`, aren't available
inside the sandbox. See
[Running commands](README.md#running-commands) for the details.

If the sandbox can't be used, the CLI says so at startup and doesn't run any commands.

Running commands needs a model that supports tool calling.

Read-only commands such as `ls`, `cat`, `grep` and `git status` run without asking.
They're still shown, marked `Running allowed command:`. For other commands, answer:

- `y` to run it once,
- `n` (or Enter) to decline,
- `a` to run it and always allow commands that start the same way in this project.
  The prompt shows the rule first, for example `uv run pytest` for
  `uv run pytest -q tests`.

Rules are saved in `~/.config/tandemone/permissions.json`, which you can also edit,
for example to remove a rule or add one for every project. See
[Allowed commands](README.md#allowed-commands) for the format.

Only approve commands you have read and trust. Allowing a command that runs project
code, such as `uv run pytest` or `npm test`, lets the model run anything inside the
sandbox, because it can change that code.

## Slash commands

| Command                | What it does                                          |
| ---------------------- | ----------------------------------------------------- |
| `/provider <name>`     | Select a provider and list its models                 |
| `/model <name>`        | Select a model from the current provider              |
| `/new`                 | Start a new conversation                              |
| `/clear`               | Clear the current provider and model selection        |
| `/help`                | Show each command and what it does                    |
| `/web`                 | Turn web search for the model on or off               |
| `/max-tokens <tokens>` | Limit the reply length in tokens; no number resets it |
| `exit` or `Ctrl+C`     | Quit the CLI                                          |

## Troubleshooting

- **The CLI exits immediately or reports a missing setting:** check that
  `~/.config/tandemone/.env` exists and that both `SYNAPSE_API_KEY` and
  `SYNAPSE_API_URL` are set. The message lists which settings are missing or invalid.
- **`tandemone: command not found`:** run `uv tool install --editable .` in the
  repository and make sure `~/.local/bin` is on your `PATH` (see step 4).
- **"The model can't run commands here":** install bubblewrap, and start `tandemone`
  in a project directory rather than `/` or your home directory. If it says the sandbox
  couldn't start, your kernel may block unprivileged user namespaces (on Ubuntu 24.04
  and later, AppArmor does this by default).
- **A command can't find a tool you have installed:** apart from uv, tools in your
  home directory (such as `~/.cargo/bin`) aren't visible in the sandbox. Install the
  tool system-wide, or run that command yourself.
- **You can't select a model:** select a provider first with `/provider <name>`.
- **Authentication errors:** confirm that your API key is correct and still active on
  your SynapseAPI account.

## Next steps

- Read the [README](README.md) for the full feature list and how the CLI talks to
  SynapseAPI.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) to set up a development environment and
  learn the lint, format, type-check, and test commands.
