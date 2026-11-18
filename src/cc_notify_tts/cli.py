"""Command-line entry point: `status`, `fire`, `mute`, `unmute`, `doctor` and `setup`.

Usage and exit codes are in docs/specs/cli.md.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from cc_notify_tts import __version__, client, config
from cc_notify_tts.events import EventKind
from cc_notify_tts.mute import MuteFile, parse_duration

# Exit codes.
OK = 0
FAILED = 1
USAGE = 2

# How long `fire` waits for the daemon's answer, on top of the wording timeout.
FIRE_TIMEOUT = 5.0


def main(argv: list[str] | None = None) -> int:
    # Modules log for the daemon's log file. In the CLI, `doctor` and `status`
    # report the same problems in their own words, so keep stderr quiet.
    logging.getLogger("cc_notify_tts").addHandler(logging.NullHandler())
    parser = _parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else USAGE
    match args.command:
        case "status":
            from cc_notify_tts import status

            return status.run()
        case "fire":
            return fire(EventKind(args.event), always=args.always)
        case "mute":
            return mute(args.duration)
        case "unmute":
            return unmute()
        case "doctor":
            from cc_notify_tts import doctor

            return doctor.run()
        case "setup":
            from cc_notify_tts import setup

            return setup.run()
        case _:
            parser.print_usage(sys.stderr)
            return USAGE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-notify-tts",
        description="Spoken notifications for Claude Code.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_parser("status", help="whether it would speak now for each event, and why")
    fire_cmd = commands.add_parser("fire", help="speak a sample notification of one event")
    fire_cmd.add_argument("event", choices=[str(kind) for kind in EventKind])
    fire_cmd.add_argument(
        "--always", action="store_true", help="speak whatever the when-to-speak settings say"
    )
    mute_cmd = commands.add_parser("mute", help="stay silent until `unmute`, or for a while")
    mute_cmd.add_argument(
        "--for",
        dest="duration",
        metavar="DURATION",
        help="how long, such as 30m, 2h or 1h30m",
    )
    commands.add_parser("unmute", help="end a mute")
    commands.add_parser("doctor", help="check everything a notification needs")
    commands.add_parser("setup", help="link Python, fetch the speech model, install the plugin")
    return parser


def fire(kind: EventKind, *, always: bool, data: Path | None = None) -> int:
    data = client.data_dir() if data is None else data
    socket = data / "daemon.sock"
    try:
        # The CLI starts the daemon with its own interpreter (ADR-0014).
        pinged, started = client.deliver({"type": "ping"}, socket, Path(sys.executable))
    except OSError as e:
        print(f"Couldn't start the daemon: {e}", file=sys.stderr)
        return FAILED
    if pinged is None:
        print(
            "The daemon didn't answer. Run `cc-notify-tts doctor` to find out why.",
            file=sys.stderr,
        )
        return FAILED
    if started:
        print("Started the daemon.")
    from cc_notify_tts import host_app

    message = {
        "type": "fire",
        "event": str(kind),
        "always": always,
        "cwd": os.getcwd(),
        "host_app": host_app.identify(os.environ, os.getpid()) or "",
    }
    timeout = FIRE_TIMEOUT + config.load().config.wording.timeout_seconds
    try:
        reply = client.request(socket, message, timeout)
    except (OSError, ValueError) as e:
        print(f"The daemon didn't answer: {e}", file=sys.stderr)
        return FAILED
    if reply.get("ok") is not True:
        print(f"The daemon refused it: {reply.get('error')}", file=sys.stderr)
        return FAILED
    if reply.get("speak") is not True:
        print(f"Not spoken. {_sentence(reply.get('reason'))}")
        print(f"To hear it anyway: cc-notify-tts fire {kind} --always")
        return OK
    print(f'Speaking: "{reply.get("text")}"', flush=True)
    speech = reply.get("speech")
    if speech == "unavailable":
        print(
            "But speech is unavailable: the model didn't load. Run `cc-notify-tts doctor`.",
            file=sys.stderr,
        )
        return FAILED
    if speech == "loading":
        print("The speech model is still loading, so it plays in a few seconds.")
    return OK


def mute(duration: str | None, file: MuteFile | None = None) -> int:
    file = MuteFile() if file is None else file
    try:
        seconds = None if duration is None else parse_duration(duration)
    except ValueError as e:
        print(f"cc-notify-tts mute: {e}", file=sys.stderr)
        return USAGE
    muted = file.mute(seconds)
    if muted.until is None:
        print("Muted until `cc-notify-tts unmute`.")
    else:
        print(f"Muted until {datetime.fromtimestamp(muted.until).strftime('%H:%M')}.")
    return OK


def unmute(file: MuteFile | None = None) -> int:
    file = MuteFile() if file is None else file
    was_muted = file.is_muted()
    file.unmute()
    print("Unmuted." if was_muted else "Wasn't muted.")
    return OK


def _sentence(reason: object) -> str:
    """A gate reason ("silent: you were active…") as a sentence."""
    text = str(reason).removeprefix("silent: ").removeprefix("speaking: ")
    return f"{text[:1].upper()}{text[1:]}."
