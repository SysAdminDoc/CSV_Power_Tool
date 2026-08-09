"""CLI adapter preserving the launcher command-line contract."""

from __future__ import annotations

import sys


def main(argv=None):
    """Dispatch to the compatibility CLI with an optional argv list.

    Keeping argv injectable makes subprocess and contract tests independent of
    the process-global ``sys.argv`` while preserving the existing launcher.
    """

    cli_main = getattr(sys.modules.get("__main__"), "cli_main", None)
    if cli_main is None:
        from CSV_Consolidator import cli_main

    return cli_main(argv)
