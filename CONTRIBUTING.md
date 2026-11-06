# Contributing to Tandem One CLI

Thanks for your interest in contributing! This guide explains how to set up a
development environment and the checks to run before you open a pull request.

## Development setup

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo-url> tandemonecli
cd tandemonecli
uv sync --group dev
```

To run the CLI against SynapseAPI, create a `.env` file from the example and fill in
your API key and server URL (see [Configuration](README.md#configuration)):

```bash
cp .env.example .env
```

If you add a setting, add it to `.env.example` too. A test checks that the two match.

Then start it with:

```bash
uv run tandemone
```

## Development commands

| Command                  | Purpose                        |
| ------------------------ | ------------------------------ |
| `uv run ruff check .`    | Lint the code                  |
| `uv run ruff format .`   | Format the code                |
| `uv run mypy src`        | Type-check the code            |
| `uv run pytest`          | Run the tests in `tests/`      |

Run these before you open a pull request, and make sure they all pass.

## Code style

- Formatting and linting use [Ruff](https://docs.astral.sh/ruff/), with a line
  length of 88. The configuration is in `pyproject.toml`.
- Add type hints to new code. It should pass `mypy`.
- Put new code under `src/tandemonecli/`, in the module that fits it (see
  [Project structure](README.md#project-structure)).

## Tests

Tests live in `tests/` and run with pytest. They don't need a `.env` file: `tests/conftest.py`
sets dummy values for every setting. `pytest-asyncio` and `pytest-dotenv` are
available if you need them. Please add tests for new behaviour. Mock any calls to
SynapseAPI so the tests don't need a real API key or network access.

## Pull requests

1. Create a branch from `main`.
2. Keep each pull request focused on one change.
3. Make sure lint, format, type-check and tests all pass.
4. Describe what you changed and why in the pull request description.

Never commit your `.env` file or API keys.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
