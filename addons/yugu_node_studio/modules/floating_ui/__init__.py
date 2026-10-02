"""PR #12 native display service adapted to Studio's public feature API."""

from .adapter import (
    create_spec, create_example, make_example_template, send_message, get_service,
)

__all__ = (
    "create_spec", "create_example", "make_example_template", "send_message", "get_service",
)
