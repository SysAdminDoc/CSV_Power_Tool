"""GUI startup adapter with no tkinter import at package import time."""

from __future__ import annotations


def launch(app_factory):
    """Construct and run an injected application class."""

    app = app_factory()
    app.run()
    return 0

