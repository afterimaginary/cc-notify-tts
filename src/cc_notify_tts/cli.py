"""Command-line entry point."""

import sys

from cc_notify_tts import __version__, setup

USAGE = "usage: cc-notify-tts --version | setup"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(__version__)
        return 0
    if args == ["setup"]:
        return setup.run()
    print(USAGE, file=sys.stderr)
    return 2
