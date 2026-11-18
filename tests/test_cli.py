import pytest

from cc_notify_tts import __version__
from cc_notify_tts.cli import main


def test_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_arguments_exits_with_usage() -> None:
    assert main([]) == 2
