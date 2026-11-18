"""Command-line entry point."""

import sys

from cc_notify_tts import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(__version__)
        return 0
    print("usage: cc-notify-tts --version", file=sys.stderr)
    return 2
