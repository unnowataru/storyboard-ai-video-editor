"""音声の配置のテスト (tests/resolve/audio_place.py) の測り方の部品。サンプルの列は手で作るので、FFmpeg と Resolve は要らない。"""
import math
import os
import sys

import common as C

sys.path.insert(0, os.path.join(C.REPO, "tests", "resolve"))
import audio_place as A  # noqa: E402

RATE, FPS = 48000, 30
WANT = [(30, 60), (120, 195)]


def tone(spans, seconds=10, gap=None):
    """spans [(最初のフレーム, 終わりのフレーム)] の所だけ 440Hz (振幅 1/8) が鳴るサンプルの列。gap (秒, 秒) は鳴らさない。"""
    out = [0] * (RATE * seconds)
    for a, b in spans:
        for i in range(a * RATE // FPS, b * RATE // FPS):
            if gap and gap[0] * RATE <= i < gap[1] * RATE:
                continue
            out[i] = int(4096 * math.sin(2 * math.pi * 440 * i / RATE))
    return out


def test_expected_spans_pass():
    sp = A.sound_spans(tone(WANT), RATE, FPS)
    assert [(round(r["start"], 2), round(r["end"], 2)) for r in sp["runs"]] == [(1.0, 2.0), (4.0, 6.5)]
    assert A.judge_spans(sp, WANT, FPS) == {"times": True, "inside": True, "outside": True, "quiet_inside": [],
                                             "loud_outside": []}


def test_shift_by_two_frames_fails_times():
    sp = A.sound_spans(tone([(32, 62), (120, 195)]), RATE, FPS)
    r = A.judge_spans(sp, WANT, FPS)
    assert not r["times"] and not r["inside"] and not r["outside"]


def test_gap_inside_fails():
    # 2 本目の途中 5.0〜5.1 秒 (3 フレーム) を鳴らさない
    r = A.judge_spans(A.sound_spans(tone(WANT, gap=(5.0, 5.1)), RATE, FPS), WANT, FPS)
    assert not r["times"] and not r["inside"] and r["quiet_inside"] == [150, 151, 152]


def test_num_and_timecode():
    assert A.num("48000") == 48000 and A.sample_rate("48 kHz") == 48000 and A.sample_rate("48000") == 48000 and A.num("") is None
    assert C.frame_to_timecode(75, 30) == "00:00:02:15" and C.frame_to_timecode(30, 30) == "00:00:01:00"
