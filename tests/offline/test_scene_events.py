"""scene_events.py (録画の後に出来事の候補を出す) のテスト。前半は合成の配列で判定だけを見る。
後半は FFmpeg で作った機密の無い合成の動画で scene_events.py を動かす (FFmpeg が無ければ飛ばす)。
合成の動画と結果は作業フォルダーの offline-tests (conftest の offline_work) に置き、終わったら消す。"""
import json
import os
import subprocess

import pytest

np = pytest.importorskip("numpy")

import common as C  # noqa: E402
import scene_events as E  # noqa: E402

S = {"level": 24, "change_ratio": 0.2, "settle_ratio": 0.01, "still_ratio": 0.002, "still_sec": 5, "ocr_ratio": 0.003}


def img(v, box=None, bv=255):
    a = np.full((90, 160), v, np.uint8)
    if box:
        y0, y1, x0, x1 = box
        a[y0:y1, x0:x1] = bv
    return a


def kinds(events):
    return [(e["t"], e["kind"]) for e in events]


def test_change_is_one_event_at_its_start_after_scrolling_settles():
    seq = [img(0)] * 3 + [img(200), img(100), img(150)] + [img(150)] * 3
    ev, _ = E.detect(list(enumerate(seq)), S)
    assert kinds(ev) == [(3, "change")]


def test_small_change_is_not_a_change_but_is_read():
    seq = [img(0)] * 3 + [img(0, (10, 20, 10, 40))] * 3
    ev, ocr = E.detect(list(enumerate(seq)), S)
    assert ev == []
    assert ocr == [4]       # 現れて (3)、落ち着いたフレーム (4) を読む


def test_still_start_is_back_dated_and_end_follows():
    moving = [img(0), img(100), img(200)]
    seq = moving + [img(50)] * 8 + [img(200)] + [img(200)] * 2
    ev, _ = E.detect(list(enumerate(seq)), S)
    st = [e for e in ev if e["kind"].startswith("still")]
    assert kinds(st) == [(3, "still-start"), (11, "still-end")]
    assert "秒以上" in st[0]["note"]


def test_uint8_difference_does_not_wrap():
    assert E.ratio(img(10), img(250), 24) == 1.0
    assert E.ratio(img(250), img(10), 24) == 1.0


def test_change_until_end_is_reported():
    seq = [img(0)] * 2 + [img(70 + i * 60) for i in range(4)]
    ev, _ = E.detect(list(enumerate(seq)), S)
    assert ev[-1]["kind"] == "change" and "終わりまで" in ev[-1]["note"]


def test_word_events_only_new_words():
    found = [(1, ["ok"]), (2, ["Error:", "x"]), (3, ["Error:"]), (4, ["fine"]), (5, ["エラー", "Error"])]
    ev = E.word_events(found, ["Error", "エラー"])
    assert kinds(ev) == [(2, "error-word"), (5, "error-word"), (5, "error-word")]
    assert {e["note"] for e in ev if e["t"] == 5} == {"語: Error", "語: エラー"}


def test_ordered_by_time_then_kind():
    ev = [{"t": 2, "kind": "error-word", "note": ""}, {"t": 2, "kind": "change", "note": ""},
          {"t": 1, "kind": "still-end", "note": ""}]
    assert kinds(E.ordered(ev)) == [(1, "still-end"), (2, "change"), (2, "error-word")]


# ---------- 合成の動画で動かす ----------
@pytest.fixture(scope="module")
def ffmpeg_ok():
    if not C.config().path_info("ffmpeg_dir")["exists"]:
        pytest.skip("FFmpeg が無い")


def test_cli_on_synthetic_video(ffmpeg_ok, offline_work):
    d = os.path.join(offline_work, "scene_events")
    os.makedirs(d, exist_ok=True)
    video = os.path.join(d, "synthetic.mp4")
    # 0〜4 秒は黒、4 秒で白に切り替わり、その後 8 秒止まる
    subprocess.run([C.config().tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=4",
                    "-f", "lavfi", "-i", "color=c=white:s=320x180:r=10:d=8",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p", video],
                   check=True)
    out = os.path.join(d, "synthetic.events.json")
    E.main([video, "--out", out])
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["ocr"] is False
    ch = [e for e in doc["events"] if e["kind"] == "change"]
    assert len(ch) == 1 and abs(ch[0]["t"] - 4) <= 1
