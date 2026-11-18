"""The demo build's timing: where each scene and spoken sentence lands in the cut."""

import pytest

from demo.build import TAIL_SECONDS, Shot, last_freeze_start, parse_duration, timeline


def test_a_scene_holds_its_last_frame_until_the_sentence_has_been_said() -> None:
    assert Shot(10.0, speech_at=8.0, speech_seconds=6.0).seconds == 8.0 + 6.0 + TAIL_SECONDS


def test_a_scene_longer_than_its_sentence_keeps_its_own_length() -> None:
    assert Shot(20.0, speech_at=8.0, speech_seconds=3.0).seconds == 20.0


def test_a_silent_scene_is_as_long_as_its_clip() -> None:
    assert Shot(12.5).seconds == 12.5


def test_sentences_start_where_they_appear_after_the_title_card_and_earlier_scenes() -> None:
    first = Shot(10.0, speech_at=8.0, speech_seconds=6.0)
    second = Shot(12.0, speech_at=7.5, speech_seconds=1.0)
    silent = Shot(12.0)

    plan = timeline([first, second, silent], card_seconds=3.0)

    assert plan.starts == [3.0, 3.0 + first.seconds, 3.0 + first.seconds + 12.0]
    assert plan.speech_starts == [3.0 + 8.0, 3.0 + first.seconds + 7.5, None]
    assert plan.total == plan.starts[2] + 12.0 + 3.0


FREEZEDETECT_LOG = """\
[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 1.2
[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 0.6
[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 1.8
[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 9.433333
"""


def test_the_spoken_frame_is_the_start_of_the_last_still_stretch() -> None:
    assert last_freeze_start(FREEZEDETECT_LOG) == pytest.approx(9.433333)


def test_a_clip_that_never_settles_is_an_error() -> None:
    with pytest.raises(ValueError):
        last_freeze_start("frame=  300 fps=0.0")


def test_a_duration_is_read_from_ffmpegs_description_of_its_input() -> None:
    log = "Input #0, wav, from 'a.wav':\n  Duration: 00:01:06.59, bitrate: 384 kb/s\n"
    assert parse_duration(log) == pytest.approx(66.59)
