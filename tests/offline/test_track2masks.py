"""track2masks.py (位置の JSON → 構成表のマスク) のテスト。Resolve も FFmpeg も要らない。

合成の位置の JSON (素材 3840x2160、30 fps) と構成表 (タイムライン 1920x1080、30 fps) を一時フォルダーに作って変換する。
座標は素材の画素のまま書く (タイムラインの画素に直すのは Resolve に組む側)。
余白・しきい値・種類と形は config/defaults.json の track の値 (pad 6、key_step 2、mosaic、rect)。
"""
import json
import subprocess
import sys

import pytest

import common as C
import settings
import track2masks as T

PAD = 6


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("RESOLVE_CONFIG", str(tmp_path / "none.json"))   # 手元の config/local.json の影響を受けない
    settings._CFG = None
    yield tmp_path
    settings._CFG = None


def box(x, y, w=100, h=40, label="ip-1"):
    return {"label": label, "x": x, "y": y, "w": w, "h": h}


def seg(start, end, *boxes):
    return {"start": start, "end": end, "start_sec": start / 30, "end_sec": end / 30, "boxes": list(boxes)}


def write(tmp, segments, rows, video="rec.mp4"):
    tj = {"version": 1, "video": str(tmp / video), "width": 3840, "height": 2160, "fps": 30.0, "frames": 3000,
          "pad": PAD, "segments": segments}
    sb = {"source": "rec.mp4", "output": "out.mp4", "seconds_per_row": 5, "fps": 30, "rows": rows}
    (tmp / "rec.track.json").write_text(json.dumps(tj), encoding="utf-8")
    (tmp / "sb.json").write_text(json.dumps(sb), encoding="utf-8")
    return str(tmp / "sb.json"), str(tmp / "rec.track.json")


def tl(x, y, w=100, h=40):
    """素材の画素の四角に余白を足した四角 (素材の画素のまま)。"""
    return T.padded({"x": x, "y": y, "w": w, "h": h}, PAD)


def test_padding_in_source_pixels():
    # 素材の画素のまま余白 6 を足し、外側へ整数に丸める (タイムラインの画素には直さない)
    assert T.padded({"x": 101, "y": 201, "w": 51, "h": 21}, PAD) == {"x": 95, "y": 195, "w": 63, "h": 33}
    assert T.padded({"x": 10.5, "y": 0.2, "w": 10, "h": 10}, 0) == {"x": 10, "y": 0, "w": 11, "h": 11}


def test_overlap_fixed_mask_and_unused_segment(cfg):
    # 行 1: 素材 10〜15 秒 (フレーム 300〜450)。segment はフレーム 270〜330 (行の頭にかかる) と 600〜630 (どの行にも当たらない)
    sb, tj = write(cfg, [seg(270, 330, box(1000, 500)), seg(600, 630, box(10, 10))],
                   [{"start": 10, "end": 15}])
    doc, r = T.convert(sb, tj)
    assert doc["rows"][0]["masks"] == [{"type": "mosaic", "shape": "rect", **tl(1000, 500)}]
    assert r["unused_segments"] == 1 and r["masks"] == 1 and r["moving"] == 0
    assert "ip-1" not in json.dumps(doc)                     # label は書かない
    assert "seconds_per_row" in doc and "timeline" not in doc   # 設定の値を写し込まない


def test_moving_keys_and_fast_row(cfg):
    # 行 1: 素材 20〜40 秒を 5 秒で (4 倍速)。24 秒 (フレーム 720) で四角が 400 px 下へ動く
    sb, tj = write(cfg, [seg(600, 720, box(1000, 500)), seg(720, 900, box(1000, 900))],
                   [{"start": "0:20", "end": "0:40", "seconds": 5}])
    doc, _ = T.convert(sb, tj, kind="black", shape="ellipse")
    m = doc["rows"][0]["masks"][0]
    t = 1.0                                                  # 4 秒 ÷ 4 倍 = 書き出しの 1 秒
    a, b = tl(1000, 500), tl(1000, 900)
    assert m == {"type": "black", "shape": "ellipse", **a,
                 "keys": [{"t": 0.0, **a}, {"t": round(t - 1 / 30, 6), **a}, {"t": t, **b}]}


def test_change_time_rounds_up(cfg):
    # 4 倍速で素材のフレーム 5 に変わると書き出しの 1.25 フレーム目。1 フレーム目はまだ前の素材が映るので 2 フレーム目で切り替える
    sb, tj = write(cfg, [seg(0, 5, box(1000, 500)), seg(5, 60, box(1000, 900))],
                   [{"start": 0, "end": 20, "seconds": 5}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"][0]
    assert [k["t"] for k in m["keys"]] == [0.0, round(1 / 30, 6), round(2 / 30, 6)]


def test_edge_move_is_not_thinned():
    # x と w は 1 ずつしか変わらなくても、右端は 2 動く。key_step (2) に届くので key にする
    a, b = {"x": 0, "y": 0, "w": 27, "h": 10}, {"x": 1, "y": 0, "w": 28, "h": 10}
    m = T.to_mask([(0.0, a), (1.0, b)], 2, 30, "mosaic", "rect")
    assert m["keys"][-1] == {"t": 1.0, **b}


def test_small_moves_are_thinned(cfg):
    # 素材で 1 px ずつ動く (最初から 1 px 以内に戻る) segment は keys にしない。最初から 2 px で key_step (2) に届く
    segs = [seg(0, 30, box(1000, 500)), seg(30, 60, box(1001, 500)), seg(60, 90, box(1000, 500)),
            seg(90, 120, box(1002, 500))]
    sb, tj = write(cfg, segs[:3], [{"start": 0, "end": 5}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"]
    assert m == [{"type": "mosaic", "shape": "rect", **tl(1000, 500)}]
    sb, tj = write(cfg, segs, [{"start": 0, "end": 5}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"][0]
    assert [k["t"] for k in m["keys"]] == [0.0, round(3 - 1 / 30, 6), 3.0]
    assert m["keys"][-1]["x"] == tl(1002, 500)["x"]


def test_separate_targets_and_breaks(cfg):
    # 同じ segment の 2 つの四角は別のマスク。間の空いた segment はつながず、別の固定のマスクになる
    sb, tj = write(cfg, [seg(0, 30, box(100, 100), box(2000, 1000, label="host-1")),
                         seg(60, 90, box(500, 100))], [{"start": 0, "end": 5}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"]
    assert [x["x"] for x in m] == [tl(100, 100)["x"], tl(2000, 1000)["x"], tl(500, 100)["x"]]
    assert all("keys" not in x for x in m)


def test_rows_of_other_source_and_existing_masks(cfg):
    sb, tj = write(cfg, [seg(0, 30, box(100, 100))],
                   [{"start": 0, "end": 5, "masks": [{"x": 1, "y": 2, "w": 3, "h": 4}]},
                    {"start": 0, "end": 5, "source": "other.mp4"}])
    doc, r = T.convert(sb, tj)
    assert doc["rows"][0]["masks"][0] == {"x": 1, "y": 2, "w": 3, "h": 4}   # 元のマスクは残す
    assert len(doc["rows"][0]["masks"]) == 2
    assert "masks" not in doc["rows"][1] and r["skipped_rows"] == [2]


def test_command_writes_new_storyboard(cfg):
    sb, tj = write(cfg, [seg(0, 30, box(100, 100))], [{"start": 0, "end": 5}])
    before = (cfg / "sb.json").read_text(encoding="utf-8")
    p = subprocess.run([sys.executable, T.__file__, sb, tj], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("OK track2masks: マスク 1")
    assert (cfg / "sb.json").read_text(encoding="utf-8") == before            # 元の構成表は書き換えない
    assert json.loads((cfg / "sb-masks.json").read_text(encoding="utf-8"))["rows"][0]["masks"]


def test_rate_uses_rounded_row_length(cfg):
    """seconds 5.01 は 150 フレームに丸まる (組む側と同じ) ので 4 倍速。素材の 4 秒目の変化は書き出しの 1 秒 (30 フレーム目)。"""
    sb, tj = write(cfg, [seg(0, 120, box(1000, 500)), seg(120, 600, box(1000, 900))],
                   [{"start": 0, "end": 20, "seconds": 5.01}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"][0]
    assert m["keys"][-1]["t"] == 1.0


def test_start_uses_rounded_source_frame(cfg):
    """行の start 0.02 秒は、組む側では素材のフレーム 1 から置く。フレーム 1 で位置が変わるなら、行の頭から新しい位置にする
    (丸める前の 0.02 秒を基準にすると、最初の 1 フレームだけ古い位置になり隠し漏れる)。"""
    sb, tj = write(cfg, [seg(0, 1, box(1000, 500)), seg(1, 60, box(1000, 900))], [{"start": 0.02, "end": 1.02, "seconds": 1}])
    m = T.convert(sb, tj)[0]["rows"][0]["masks"]
    assert len(m) == 1 and (m[0]["x"], m[0]["y"]) == (tl(1000, 900)["x"], tl(1000, 900)["y"])
    assert all((k["x"], k["y"]) == (m[0]["x"], m[0]["y"]) for k in m[0].get("keys", []))
