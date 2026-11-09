"""Fake providers with known accuracy (AGE-18). See mocks/README.md for the table.

uv run python -m mocks.fake_providers --port 8101
"""

from .app import a2a_card, card, create_app
from .corruption import INVALID_OUTPUT, MOCKS, MockSpec, corrupt, mock_output

__all__ = [
    "INVALID_OUTPUT",
    "MOCKS",
    "MockSpec",
    "a2a_card",
    "card",
    "corrupt",
    "create_app",
    "mock_output",
]
