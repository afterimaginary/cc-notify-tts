"""Entry point for every hook in hooks.json: `<python> -I -S shim.py <HookEvent>`.

`-I -S` keeps the user's PYTHONPATH and site-packages out (ADR-0004), so the
package is imported from this plugin's own `src/`. The work is in
`cc_notify_tts.shim`. Whatever happens, this prints nothing and exits 0.
"""

import os
import sys
import time


def _run() -> None:
    fired_at = time.time()  # the protocol's `fired_at`: when the hook started
    # Nothing the shim or a library prints may reach Claude Code.
    sys.stdout = sys.stderr = open(os.devnull, "w")  # noqa: SIM115
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from cc_notify_tts import shim

    shim.main(sys.argv[1:], sys.stdin.buffer.read(), os.environ, fired_at)


if __name__ == "__main__":
    try:  # noqa: SIM105 - contextlib would be one more import per event
        _run()
    except BaseException:
        pass
    os._exit(0)
