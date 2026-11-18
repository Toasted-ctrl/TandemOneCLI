"""Completion for slash commands and their arguments, in the input line.

Typing ``/`` lists the commands with what they do. After ``/provider `` come the
available providers, and after ``/model `` the models of the selected provider.
Names match whatever their case, and are completed in the case the API uses,
which the commands need.
"""

from collections.abc import Callable, Iterable, Iterator

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples

from tandemonecli.slash.handler import COMMANDS, SlashState
from tandemonecli.startup.models import AvailableModels
from tandemonecli.text import strip_control_chars


class SlashCompleter(Completer):
    """Completes slash commands, and the provider or model they select.

    ``models`` and ``state`` are called for each completion, so it always uses
    the current models and selection.
    """

    def __init__(
        self,
        models: Callable[[], AvailableModels],
        state: Callable[[], SlashState],
    ) -> None:
        self.models = models
        self.state = state

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        command, space, argument = text.partition(" ")
        if not space:
            yield from self._commands(command)
        # One argument only, so a finished one isn't completed again
        elif " " not in argument:
            yield from self._arguments(command.lower(), argument)

    def _commands(self, typed: str) -> Iterator[Completion]:
        for info in COMMANDS:
            name, _, arguments = info.usage.partition(" ")
            if not name.startswith(typed.lower()):
                continue
            display: StyleAndTextTuples = [("class:completion.command", name)]
            if arguments:
                display += [("", " "), ("class:completion.argument", arguments)]
            yield Completion(
                # With a space after a command that needs an argument, so its
                # choices are listed right away
                name + " " if arguments else name,
                start_position=-len(typed),
                display=display,
                display_meta=info.description,
            )

    def _arguments(self, command: str, typed: str) -> Iterator[Completion]:
        names: Iterable[str] = ()
        if command == "/provider":
            names = self.models().providers
        elif command == "/model":
            provider = self.state().selected_provider
            names = self.models().providers.get(provider or "", [])
        for name in names:
            if name.lower().startswith(typed.lower()):
                yield Completion(
                    name,
                    start_position=-len(typed),
                    # Names come from the API, so they can't control the terminal
                    display=[("class:completion.argument", strip_control_chars(name))],
                )
