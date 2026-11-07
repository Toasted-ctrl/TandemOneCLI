"""The project root: the directory tandemone was started in.

It's recorded once at startup, so commands keep running there and the model is told
where it is, wherever the CLI was launched from.
"""

from pathlib import Path

ROOT = Path.cwd()
