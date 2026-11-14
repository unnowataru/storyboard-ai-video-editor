"""track.py (隠す位置を見つけて JSON に書く) のテスト。Resolve は要らない。

前半は FFmpeg も文字認識も使わない部品のテスト。後半は FFmpeg で作った機密の無い合成の動画で track.py を動かす
(FFmpeg が無ければ飛ばす。文字認識を使う 1 件は、Windows の文字認識が使えなければ飛ばす)。
合成の動画と結果は作業フォルダー (config の work_dir) の offline-tests\\run-<番号>\\track (実行ごとに別の名前。conftest の offline_work) に置き、終わったら消す。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")

import check_env  # noqa: E402
import common as C  # noqa: E402
import contact_sheet  # noqa: E402
import track  # noqa: E402

S = {"lookahead": 3, "ocr_scale": 2, "still_level": 8}


def words(*ws):
    """文字認識の結果の形 (ocr_scale 倍の画像での座標)。"""
    return [{"text": t, "l": l, "x": x, "y": y, "w": w, "h": h} for t, l, x, y, w, h in ws]


def args(**kw):
    a = {"ip": False, "host": [], "text": []}
    a.update(kw)
    return argparse.Namespace(**a)


# ---------- 部品 ----------
def test_find_ip_host_text_and_scale():
    ws = words(("192.0.2.10", 1, 100, 40, 200, 30), ("lab-node01", 2, 100, 80, 220, 30),
               ("Save", 3, 10, 120, 80, 30), ("File", 3, 100, 120, 40, 30), ("other", 4, 0, 0, 10, 10))
    got = track.find(ws, args(ip=True, host=["lab-*"], text=["save file"]), 2)
    assert [b[4] for b in got] == [("ip", "192.0.2.10"), ("host", "lab-node01"), ("text", "save file"), ("text", "save file")]
    assert got[0][:4] == [50, 20, 100, 15]   # 2 倍で読んだ座標を元の解像度に戻す


def test_find_text_allows_one_misread():
    ws = words(("adrnin", 1, 0, 0, 60, 20))   # admin を 1 文字読み誤り (m → rn は置き換え 1 と余分 1 なので 2)
    assert track.find(ws, args(text=["admin"]), 2) == []
    ws = words(("admln", 1, 0, 0, 60, 20))     # 置き換え 1 文字は一致とみなす
    assert len(track.find(ws, args(text=["admin"]), 2)) == 1


def test_labels_hide_text():
    lb = track.Labels()
    assert [lb(("ip", "192.0.2.10")), lb(("ip", "192.0.2.11")), lb(("host", "a")), lb(("ip", "192.0.2.10"))] == \
        ["ip-1", "ip-2", "host-1", "ip-1"]


def test_fill_misses_bridges_a_gap():
    """1 回だけ認識が抜けた読み取りに、前後の位置を含む範囲を追加する。"""
    shots = [{"i": 0, "boxes": [[10, 10, 50, 10, "ip-1"]]},
             {"i": 5, "boxes": []},
             {"i": 9, "boxes": [[20, 10, 50, 10, "ip-1"]]}]
    track.fill_misses(shots, S)
    assert shots[1]["boxes"] == [[10, 10, 60, 10, "ip-1"]]


def test_segments_shape_and_stride():
    """読み取りの結果をフレームごとに広げ、同じ隠す範囲の続きを 1 つの segment にまとめる。定期の読み取りで初めて見つかった範囲は、
    直前の読み取りのフレームまでさかのぼる。隠す範囲の無いフレームは出さない。"""
    shots = [{"i": 0, "boxes": []},
             {"i": 10, "boxes": [[1, 2, 3, 4, "ip-1"]], "stride": True},
             {"i": 20, "boxes": [], "stride": False},
             {"i": 25, "boxes": [[5, 6, 7, 8, "template-0"], [5, 6, 7, 8, "template-0"]], "stride": False}]
    segs = track.segments(shots, 30, 10.0)
    assert segs == [
        {"start": 0, "end": 20, "start_sec": 0.0, "end_sec": 2.0,
         "boxes": [{"label": "ip-1", "x": 1, "y": 2, "w": 3, "h": 4}]},
        {"start": 25, "end": 30, "start_sec": 2.5, "end_sec": 3.0,
         "boxes": [{"label": "template-0", "x": 5, "y": 6, "w": 7, "h": 8}]}]


# ---------- 合成の動画で動かす ----------
@pytest.fixture
def work(offline_work, request):
    cfg = C.config()
    if not cfg.path_info("ffmpeg_dir")["exists"]:
        pytest.skip("FFmpeg が無い")
    d = os.path.join(offline_work, "track", request.node.name)
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def ffmpeg(*a):
    subprocess.run([C.config().tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


def run_track(*a):
    r = subprocess.run([sys.executable, os.path.join(C.SCRIPTS, "track.py"), *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("OK track: "), r.stdout   # 正常なら 1 行
    return lines[0]


def boxes_x(doc):
    return [(g["start"], g["end"], [b["x"] for b in g["boxes"]]) for g in doc["segments"]]


def test_track_template_on_synthetic_video(work):
    """色の四角 (80x60) が 1.5 秒で右へ動く 3 秒の動画。0.5 秒のフレームで囲んだ領域を見本にして追う。文字認識は使わない。"""
    video = os.path.join(work, "bars.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=3", "-f", "lavfi", "-i", "smptebars=s=80x60:r=30",
           "-filter_complex", "[0][1]overlay=x='if(lt(t,1.5),50,200)':y=100:shortest=1",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
    marks = os.path.join(work, "bars.marks.json")
    with open(marks, "w", encoding="utf-8") as f:
        json.dump({"video": "bars.mp4", "width": 320, "height": 240, "pins": [],
                   "regions": [{"shape": "rect", "t": 0.5, "x": 50, "y": 100, "w": 80, "h": 60}]}, f)
    out = os.path.join(work, "bars.track.json")
    summary = run_track(video, "--template", marks + ":0", "--out", out)
    assert summary.endswith("詳細: " + out) and "/90、" in summary and "見つけたもの template-0  " in summary
    with open(out, encoding="utf-8") as f:
        doc = json.load(f)
    assert {k: doc[k] for k in ("version", "width", "height", "frames", "pad")} == \
        {"version": 1, "width": 320, "height": 240, "frames": 90, "pad": C.config().section("track")["pad"]}
    assert doc["templates"] == [{"label": "template-0", "shape": "rect"}]
    segs = boxes_x(doc)
    assert segs[0][0] == 0 and segs[0][2] == [50] and segs[-1][1] == 90 and segs[-1][2] == [200]
    assert segs[0][1] == segs[-1][0] == 45   # 動いたフレームで切り替わる (1.5 秒 × 30)
    assert not os.path.exists(os.path.join(work, "track-frames"))   # 文字認識に渡す画像の置き場所は消える


def test_track_ip_with_windows_ocr(work):
    """文書用の IP アドレス (192.0.2.0/24) を 1 秒目に出し、2 秒目に動かす。Windows の文字認識で見つける。"""
    ok, _ = check_env.ocr_available(C.config())
    if not ok:
        pytest.skip("Windows の文字認識が使えない")
    font = C.config().font_file("Arial") or C.config().font_file("Meiryo")
    if not font:
        pytest.skip("drawtext に使う書体が無い")
    video = os.path.join(work, "ip.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=white:s=640x360:r=30:d=3", "-vf",
           f"drawtext=fontfile='{contact_sheet.esc_font(font)}':text='192.0.2.10':fontsize=32:fontcolor=black:"
           "x='if(lt(t,2),40,300)':y=150:enable='gte(t,1)'", "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
    out = os.path.join(work, "ip.track.json")
    summary = run_track(video, "--ip", "--out", out)
    assert "見つけたもの ip-1  " in summary and "192.0.2.10" not in summary   # 画面にも見つけた文字は出さない
    with open(out, encoding="utf-8") as f:
        text = f.read()
    assert "192.0.2.10" not in text   # 見つけた文字そのものは JSON に書かない
    segs = boxes_x(json.loads(text))
    assert 29 <= segs[0][0] <= 31 and all(35 <= x <= 45 for x in segs[0][2])
    assert segs[-1][1] == 90 and all(295 <= x <= 305 for x in segs[-1][2])
