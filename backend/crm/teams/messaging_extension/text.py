"""Text formatting shared by Teams messaging-extension features."""

import re


def plain_text(value):
    """Escape Markdown-sensitive characters before displaying text in a Teams card."""
    return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", value)
