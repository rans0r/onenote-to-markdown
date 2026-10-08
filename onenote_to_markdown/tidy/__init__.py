"""Clean up an onenote-to-markdown export for Markdown note apps.

Run it with the exporter (`python -m onenote_to_markdown NOTEBOOK -o notes --tidy`)
or on an existing export (`python -m onenote_to_markdown.tidy notes/My Notebook`).
"""
from .cli import TidyError, main, run  # noqa: F401
from .steps import STEP_NAMES, Options  # noqa: F401
