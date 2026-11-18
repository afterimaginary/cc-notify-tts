"""Builds the demo video from scratch: `make demo`.

1. Lays out a throwaway home under /tmp as `setup` would, with the plugin's
   hooks pointing at the real shim and a daemon that is the real one, model
   wording included, except that Kokoro's audio goes to files and presence comes
   from a file (`demo/daemon.py`).
2. Records each scene with VHS, in a clean shell inside that home, so no real
   path, user name, host name, notification or other app can appear.
3. Finds the frame where each spoken sentence appears on screen (the scene's
   last change) and mixes that sentence's Kokoro audio in there with ffmpeg.
4. Adds a caption under each scene and title and end cards (ImageMagick, since
   ffmpeg's text filters aren't in every build), then writes the full cut to
   `build/demo/` and a short silent GIF to `docs/demo.gif`.

Needs `vhs`, `ffmpeg` and `magick` on PATH and the speech model (`cc-notify-tts
setup` fetches it; otherwise this does). With Ollama running and the default
wording model pulled, the daemon words with it as usual, and it's unloaded when
the build ends; without, it uses templates.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from cc_notify_tts.client import request
from cc_notify_tts.config import Config
from cc_notify_tts.models import FILES, default_models_dir, fetch_all, is_present
from cc_notify_tts.ollama import Ollama, OllamaError

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "build" / "demo"
VIDEO = OUT / "cc-notify-tts-demo.mp4"
GIF = REPO / "docs" / "demo.gif"

WIDTH, HEIGHT = 1920, 1080
TERMINAL_HEIGHT = 900
CAPTION_HEIGHT = HEIGHT - TERMINAL_HEIGHT
FPS = 30
CARD_SECONDS = 3.5
# How long a scene holds after its sentence has been spoken.
TAIL_SECONDS = 1.2
GIF_WIDTH = 960
GIF_FPS = 10

BACKGROUND = "#1e1e2e"
TEXT = "#cdd6f4"
MUTED = "#a6adc8"
FONT = "/System/Library/Fonts/Helvetica.ttc"

TOOLS = ("vhs", "ffmpeg", "magick")
# A daemon stopping unloads the wording model first, which can take a few seconds.
STOP_TIMEOUT = 15.0


@dataclass(frozen=True)
class Scene:
    name: str
    caption: str
    # The tape's visible part. It starts hidden, at a cleared prompt.
    tape: str


def _play(name: str) -> str:
    return f"""Type "clear; demo-scene {name}"
Enter
Sleep 500ms
Show
Wait@60s /\\$\\s*$/
Sleep 1s
"""


SCENES = (
    Scene(
        "finished",
        "Claude Code finishes a turn while you're in another app. You hear what got done.",
        _play("finished"),
    ),
    Scene(
        "permission",
        "It needs your permission. You hear what it wants to do.",
        _play("permission"),
    ),
    Scene(
        "silent",
        "You're at the keyboard, so it stays quiet, and cc-notify-tts status says why.",
        _play("silent")
        + """Type "cc-notify-tts status"
Sleep 600ms
Enter
Sleep 8s
""",
    ),
)


def tape_header(clip: Path, home: Path) -> str:
    """Settings, then a hidden switch to a clean shell in the throwaway home."""
    shell = (
        f"exec env -i HOME={home} PATH={home}/bin:/usr/bin:/bin TERM=xterm-256color "
        "LANG=en_US.UTF-8 BASH_SILENCE_DEPRECATION_WARNING=1 PS1='weather-app $ ' "
        "/bin/bash --noprofile --norc"
    )
    return f"""Output "{clip}"
Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 26
Set Width {WIDTH}
Set Height {TERMINAL_HEIGHT}
Set Padding 40
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set CursorBlink false
Set Framerate {FPS}
Hide
Type `{shell}`
Enter
Sleep 500ms
Type "cd ~/weather-app && clear"
Enter
Sleep 300ms
"""


# --- timeline ------------------------------------------------------------------------


@dataclass(frozen=True)
class Shot:
    """A recorded scene and the sentence spoken in it, if any."""

    clip_seconds: float
    # When the spoken sentence appears on screen, in the clip's own time.
    speech_at: float | None = None
    speech_seconds: float = 0.0

    @property
    def seconds(self) -> float:
        """The scene's length in the cut: the clip, held until the sentence has been said."""
        if self.speech_at is None:
            return self.clip_seconds
        return max(self.clip_seconds, self.speech_at + self.speech_seconds + TAIL_SECONDS)


@dataclass(frozen=True)
class Timeline:
    starts: list[float]
    speech_starts: list[float | None]
    total: float


def timeline(shots: list[Shot], card_seconds: float = CARD_SECONDS) -> Timeline:
    """Where each scene and sentence starts in the cut: a title card, the scenes, an end card."""
    starts: list[float] = []
    speech: list[float | None] = []
    at = card_seconds
    for shot in shots:
        starts.append(at)
        speech.append(None if shot.speech_at is None else at + shot.speech_at)
        at += shot.seconds
    return Timeline(starts, speech, at + card_seconds)


_FREEZE_START = re.compile(r"freeze_start: ([0-9.]+)")


def last_freeze_start(ffmpeg_log: str) -> float:
    """When the screen last changed, from ffmpeg's freezedetect output."""
    starts = _FREEZE_START.findall(ffmpeg_log)
    if not starts:
        raise ValueError("the clip never stops changing")
    return float(starts[-1])


# --- steps ---------------------------------------------------------------------------


def check_tools() -> None:
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}: brew install vhs ffmpeg imagemagick")
    if subprocess.run(["ffmpeg", "-version"], capture_output=True, check=False).returncode != 0:
        raise SystemExit("ffmpeg on PATH doesn't run: reinstall it or put a working one first")


def wording_model_ready() -> bool:
    """Whether the daemon can word with the default model; otherwise it uses templates."""
    model = Config().wording.model
    try:
        if Ollama().installed(model):
            return True
        print(f"The wording model isn't pulled, so this uses templates: ollama pull {model}")
    except OllamaError:
        print("Ollama isn't running, so this uses templates.")
    return False


def models_dir() -> Path:
    installed = default_models_dir()
    if all(is_present(installed / f.name, f) for f in FILES):
        return installed
    cached = OUT / "models"
    fetch_all(cached)
    return cached


def make_home(models: Path) -> Path:
    """A home laid out as `setup` leaves it, with demo stand-ins for the daemon and CLI."""
    # A Unix socket path must fit in 104 bytes, so keep it short.
    home = Path(tempfile.mkdtemp(prefix="cnt-demo-", dir="/tmp"))
    data = home / "Library" / "Application Support" / "cc-notify-tts"
    (data / "bin").mkdir(parents=True)
    (data / "bin" / "python").symlink_to(sys.executable)
    # The shim starts `<venv>/bin/python -I -m cc_notify_tts.daemon`; run the demo's instead.
    _script(data / "venv" / "bin" / "python", f'"{REPO}/demo/daemon.py" "{home}" "{models}"')
    _script(home / "bin" / "cc-notify-tts", f'"{REPO}/demo/cli.py" "$@"')
    _script(home / "bin" / "demo-scene", f'"{REPO}/demo/session.py" "$@"')
    (home / "weather-app").mkdir()
    return home


def _script(path: Path, args: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" {args}\n')
    path.chmod(0o755)


def run_scene_step(home: Path, step: str) -> None:
    env = {**os.environ, "HOME": str(home)}
    subprocess.run([sys.executable, str(REPO / "demo" / "session.py"), step], env=env, check=True)


def record(scene: Scene, home: Path, work: Path) -> Path:
    clip = work / f"{scene.name}.mp4"
    tape = work / f"{scene.name}.tape"
    tape.write_text(tape_header(clip, home) + scene.tape)
    subprocess.run(["vhs", str(tape)], cwd=work, check=True)
    return clip


def seconds(media: Path) -> float:
    # Without an output ffmpeg exits non-zero, but it has printed the input's duration.
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", media], capture_output=True, text=True, check=False
    )
    return parse_duration(done.stderr)


_DURATION = re.compile(r"Duration: (\d+):(\d+):([0-9.]+)")


def parse_duration(ffmpeg_log: str) -> float:
    match = _DURATION.search(ffmpeg_log)
    if match is None:
        raise ValueError("ffmpeg reported no duration")
    hours, minutes, secs = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def speech_frame(clip: Path) -> float:
    done = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            clip,
            "-map",
            "0:v",
            "-vf",
            "freezedetect=n=0.0001:d=0.4",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return last_freeze_start(done.stderr)


def shot(scene: Scene, clip: Path, home: Path) -> tuple[Shot, Path | None]:
    result = json.loads((home / "scenes" / f"{scene.name}.json").read_text())
    if result["wav"] is None:
        return Shot(seconds(clip)), None
    wav = Path(result["wav"])
    return Shot(seconds(clip), speech_frame(clip), seconds(wav)), wav


def card(path: Path, title: str, lines: list[str]) -> None:
    args = [
        "magick",
        "-size",
        f"{WIDTH}x{HEIGHT}",
        f"xc:{BACKGROUND}",
        "-font",
        FONT,
        "-gravity",
        "center",
        "-fill",
        TEXT,
        "-pointsize",
        "120",
        "-annotate",
        "+0-110",
        title,
        "-fill",
        MUTED,
        "-pointsize",
        "46",
    ]
    for i, line in enumerate(lines):
        args += ["-annotate", f"+0+{40 + i * 80}", line]
    subprocess.run([*args, str(path)], check=True)


def caption(path: Path, text: str) -> None:
    subprocess.run(
        [
            "magick",
            "-background",
            BACKGROUND,
            "-fill",
            TEXT,
            "-font",
            FONT,
            "-pointsize",
            "44",
            "-size",
            f"{WIDTH - 200}x",
            "-gravity",
            "center",
            f"caption:{text}",
            "-extent",
            f"{WIDTH}x{CAPTION_HEIGHT}",
            str(path),
        ],
        check=True,
    )


def assemble(work: Path, clips: list[Path], shots: list[Shot], wavs: list[Path | None]) -> Timeline:
    plan = timeline(shots)
    title, end = work / "title.png", work / "end.png"
    card(
        title,
        "cc-notify-tts",
        ["Spoken, summarized notifications for Claude Code on macOS", "Sound on"],
    )
    card(
        end,
        "cc-notify-tts",
        [
            "Local speech with Kokoro. Optional local wording with Ollama.",
            "github.com/afterimaginary/cc-notify-tts",
        ],
    )
    inputs: list[str] = [
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-t",
        str(CARD_SECONDS),
        "-i",
        str(title),
    ]
    graph: list[str] = [f"[0:v]fps={FPS},format=yuv420p,setsar=1,fade=in:d=0.5[v0]"]
    n = 1
    for i, (clip, s) in enumerate(zip(clips, shots, strict=True)):
        cap = work / f"caption-{i}.png"
        caption(cap, SCENES[i].caption)
        inputs += [
            "-i",
            str(clip),
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{s.seconds:.3f}",
            "-i",
            str(cap),
        ]
        hold = max(0.0, s.seconds - s.clip_seconds) + 0.1
        graph.append(
            f"[{n}:v]fps={FPS},tpad=stop_mode=clone:stop_duration={hold:.3f},"
            f"trim=duration={s.seconds:.3f},setpts=PTS-STARTPTS[t{i}];"
            f"[t{i}][{n + 1}:v]vstack,format=yuv420p,setsar=1[v{i + 1}]"
        )
        n += 2
    inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(CARD_SECONDS), "-i", str(end)]
    last = len(clips) + 1
    fade_at = CARD_SECONDS - 0.6
    graph.append(f"[{n}:v]fps={FPS},format=yuv420p,setsar=1,fade=out:st={fade_at}:d=0.6[v{last}]")
    n += 1
    graph.append("".join(f"[v{i}]" for i in range(last + 1)) + f"concat=n={last + 1}:v=1:a=0[v]")
    mixed: list[str] = []
    for wav, start in zip(wavs, plan.speech_starts, strict=True):
        if wav is None or start is None:
            continue
        inputs += ["-i", str(wav)]
        ms = round(start * 1000)
        graph.append(
            f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}:all=1[a{n}]"
        )
        mixed.append(f"[a{n}]")
        n += 1
    graph.append("".join(mixed) + f"amix=inputs={len(mixed)}:normalize=0,apad[a]")
    VIDEO.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{plan.total:.3f}",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(VIDEO),
        ],
        check=True,
    )
    return plan


def silent_gif(plan: Timeline) -> None:
    """The scenes alone, without cards or sound, small enough for the README."""
    start = plan.starts[0]
    length = plan.total - CARD_SECONDS - start
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(VIDEO),
            "-filter_complex",
            f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none",
            "-loop",
            "0",
            str(GIF),
        ],
        check=True,
    )


def stop_daemon(home: Path) -> None:
    socket = home / "Library" / "Application Support" / "cc-notify-tts" / "daemon.sock"
    try:
        pid = int(request(socket, {"type": "ping"}, 1.0)["pid"])
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError, KeyError):
        return
    # Wait for it to exit, so the wording model is unloaded before the build ends.
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    print(f"the demo daemon (pid {pid}) didn't stop within {STOP_TIMEOUT:g} s")


def main() -> int:
    check_tools()
    wording_model_ready()
    models = models_dir()
    work = OUT / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    home = make_home(models)
    try:
        run_scene_step(home, "start")
        clips: list[Path] = []
        shots: list[Shot] = []
        wavs: list[Path | None] = []
        for scene in SCENES:
            clip = record(scene, home, work)
            s, wav = shot(scene, clip, home)
            kept = None
            if wav is not None:
                kept = work / f"{scene.name}.wav"
                shutil.copyfile(wav, kept)
            clips.append(clip)
            shots.append(s)
            wavs.append(kept)
        run_scene_step(home, "end")
    finally:
        stop_daemon(home)
        shutil.rmtree(home, ignore_errors=True)
    plan = assemble(work, clips, shots, wavs)
    silent_gif(plan)
    print(f"Full cut: {VIDEO.relative_to(REPO)} ({plan.total:.1f} s)")
    print(f"Silent cut: {GIF.relative_to(REPO)} ({GIF.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
