import subprocess
import sys

import pytest

from cc_notify_tts import __version__


@pytest.mark.integration
def test_module_entry_point_runs_in_a_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cc_notify_tts", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__
