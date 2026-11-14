"""scripts/check_final.py のテスト。Resolve の API は模擬のオブジェクト (Fusion は tests/conftest.py) で置き換える。
合成は組む側 (sb2resolve.add_fusion・common.build_fusion) で作り、テストでは崩す所だけを書く。

- 構成表どおりのタイムラインなら、タイムラインの項目は全部 pass (examples/ の構成表も)
- わざと 1 フレームずらす・肩テロップを残す・マスクを小さくする・ズームを 1 フレーム遅らせると fail
- 読めないもの (タイムラインを読んでいない、動画が無い、肩テロップの作りが分からない) は unknown で、成功にしない
- 合成した動画 (FFmpeg) で、肩テロップを 1 フレーム長く残すと画素の検査で fail、フレーム数のずれで fail、区切りのフレームの画像ができる
"""
import copy
import json
import os
import shutil
import subprocess

import pytest

from conftest import Comp, Inp, Item, Spline, Tool, patch_common

import check_final as CF
import common as C
import sb2resolve as S

HEAD = 108000
K4 = (3840, 2160)


class MPI:
    def __init__(self, path, fps="30", resolution="1920x1080"):
        self.path, self.fps, self.resolution = path, fps, resolution

    def GetClipProperty(self, k):
        return {"FPS": self.fps, "File Path": self.path, "Resolution": self.resolution}[k]


class Timeline:
    def __init__(self, items, size=(1920, 1080)):
        self.items, self.size = items, size

    def GetSetting(self, k):
        W, H = self.size
        return {"timelineFrameRate": "30", "timelineResolutionWidth": str(W), "timelineResolutionHeight": str(H)}[k]

    def GetItemListInTrack(self, kind, i):
        return self.items

    def GetStartFrame(self):
        return HEAD

    def GetName(self):
        return "tl"


@pytest.fixture
def sb():
    return C.Config().load_storyboard(os.path.join(C.REPO, "tests", "trial-storyboard.json"), "testsrc.mp4")


def build(sb, size=(1920, 1080)):
    """構成表どおりのタイムライン。クリップの位置は sb2resolve.plan、合成は組む側の sb2resolve.add_fusion で作る
    (止めたクリップは行の後ろ)。size は素材の解像度。値を作れない行には合成を追加しない。"""
    rows, fps = S.plan(sb), float(sb["fps"])
    mpi = MPI(sb["source"], resolution="%dx%d" % size)
    items, holds = [], []
    for p in rows:
        s0, n = round(p["start"] * fps), round((p["end"] - p["start"]) * fps)
        items.append(Item(HEAD + p["rec"], HEAD + p["rec"] + p["frames"], (s0, s0 + n), p["pct"], mpi))
        holds.append(Item(HEAD + p["hold_rec"], HEAD + p["hold_rec"] + p["hold_frames"], (s0 + n - 1,) * 2, 0.0, mpi)
                     if p["hold_frames"] else None)
    S.add_fusion(items, sb, rows, {p["source"]: {"size": size, "fps": fps} for p in rows}, C.caption_font(sb), holds)
    return Timeline([it for pair in zip(items, holds) for it in pair if it], (sb["timeline"]["width"], sb["timeline"]["height"]))


def made(sb, i, **row):
    """組む側に渡す、sb の i 行目 (0 始まり) を row で書き換えた写し (値が None の項目は消す)。"""
    b = copy.deepcopy(sb)
    b["rows"][i] = {k: v for k, v in dict(b["rows"][i], **row).items() if v is not None}
    return b


def row_comp(sb, row, size=(1920, 1080), font_file=None):
    """1 行の合成 (組む側の common.scene_values と build_fusion)。"""
    it = Item()
    C.build_fusion(it, C.scene_values(sb, row, font_file or C.caption_font(sb), source_size=size))
    return it.comp


def detach(comp, name):
    """部品 name の出力を、つながった先の入力から全部外す。"""
    for inp in comp[name].GetConnectedInputs().values():
        delattr(inp.tool, inp.ID)


def passed(tool):
    """素通し (PassThrough) にする。テンプレートの使わない部品はこの形でつながったまま残る。"""
    tool.SetAttrs({"TOOLB_PassThrough": True})


def rekey(tool, name, f):
    """入力 name につながったスプライン (XYPath なら X と Y) のキーの時刻を f(時刻) に移す。"""
    for sp in list(tool.links[name].links.values()) or [tool.links[name]]:
        sp.keys = {f(t): v for t, v in sp.keys.items()}


def shown(off):
    """off フレーム目で消える Blend。"""
    return lambda t: 1.0 if t < off else 0.0


def late_zoom(f):
    """3 行目のズームのキーの時刻を f(時刻) に移す。"""
    return lambda tl: [rekey(tl.items[2].comp["Zoom"], k, f) for k in ("Size", "Center")]


def status(report, item, row=None):
    return [c["status"] for c in report["checks"] if c["item"] == item and (row is None or c["row"] == row)]


@pytest.mark.meiryo
def test_good_timeline_all_pass(sb):
    rep = CF.run(sb, None, build(sb))
    tl_checks = [c for c in rep["checks"] if not c["item"].startswith("video_")]
    assert all(c["status"] == "pass" for c in tl_checks), [c for c in tl_checks if c["status"] != "pass"]
    # 動画が無いので動画の項目は unknown。unknown は成功ではない
    assert rep["summary"]["unknown"] == 4 and rep["summary"]["fail"] == 0


@pytest.mark.parametrize("name", ["sample1", "sample2"])
@pytest.mark.meiryo
def test_examples_built_fusion_pass(name):
    """examples/ の構成表 (素材は 4K) を組む側 (sb2resolve.add_fusion) で組むと、照らし合わせる側の動画以外の項目が全部 pass。"""
    sb = C.Config().load_storyboard(os.path.join(C.REPO, "examples", name + "-storyboard.json"), "testsrc.mp4")
    rep = CF.run(sb, None, build(sb, K4))
    bad = [c for c in rep["checks"] if not c["item"].startswith("video_") and c["status"] != "pass"]
    assert not bad and len(rep["checks"]) > 200, bad


def test_one_frame_shift_fails(sb):
    tl = build(sb)
    tl.items[0].end += 1
    for it in tl.items[1:]:
        it.start, it.end = it.start + 1, it.end + 1
    rep = CF.run(sb, None, tl)
    assert status(rep, "position", 1) == ["fail"] and status(rep, "position", 2) == ["fail"]
    assert {1, 2, 3} <= set(rep["sheet"]["rows"])


@pytest.mark.parametrize("change,expected", [
    pytest.param(lambda tl: tl.items[0].comp["BadgeOver"].put(Blend=shown(61)), [("badge", 1, "fail")],
                 id="badge-one-frame-late"),
    pytest.param(lambda tl: (setattr(tl.items[1], "comp", tl.items[0].comp), setattr(tl.items[0], "comp", None)),
                 [("badge", 1, "unknown"), ("badge", 2, "fail")], id="badge-missing-and-extra"),
    pytest.param(lambda tl: tl.items[2].comp["Mask1"].put(Width=1030 / 1920), [("mask1", 3, "fail")], id="mask-too-small"),
    pytest.param(late_zoom(lambda t: t + 1 if t else 0), [("zoom_size", 3, "fail"), ("zoom_center", 3, "fail")],
                 id="zoom-one-frame-late"),
    pytest.param(lambda tl: setattr(tl.items[0].mpi, "fps", ""), [("source_range", 1, "unknown")], id="source-fps-unreadable"),
])
def test_changed_timeline_status(sb, change, expected):
    tl = build(sb)
    change(tl)
    rep = CF.run(sb, None, tl)
    for item, row, want in expected:
        assert status(rep, item, row) == [want]


@pytest.mark.parametrize("blend,want", [(lambda t: 1.0 if t < 60 else None, "unknown"),
                                        (lambda t: 0.0 if 60 <= t < 140 else 1.0, "fail")])
def test_badge_unreadable_is_unknown_and_reappearing_fails(sb, blend, want):
    tl = build(sb)
    tl.items[0].comp["BadgeOver"].put(Blend=blend)
    assert status(CF.run(sb, None, tl), "badge", 1) == [want]


def test_zoom_late_start_fails_and_small_zoom_passes(sb):
    tl = build(sb)
    late_zoom(lambda t: t or 1)(tl)
    assert status(CF.run(sb, None, tl), "zoom_size", 3) == ["fail"]
    sb["rows"][2]["zoom"] = {"x": 40, "y": 20, "w": 1840, "h": 1035, "seconds": 3}   # 約 1.04 倍へ 90 フレームで寄る
    rep = CF.run(sb, None, build(sb))
    assert status(rep, "zoom_size", 3) == ["pass"]


def test_detached_mask_is_not_used(sb):
    # 正しい大きさのマスクがどこにもつながっていなければ、つながった小さいマスクで判定する
    tl = build(sb)
    comp = tl.items[2].comp
    comp.AddTool("RectangleMask", **comp["Mask1"].inputs)
    comp["Mask1"].put(Width=1000 / 1920)
    assert status(CF.run(sb, None, tl), "mask1", 3) == ["fail"]


def test_passed_through_tools_are_not_used(sb):
    # 正しい値のマスクでも素通しなら使わない (マスクは無い扱いで fail)
    tl = build(sb)
    passed(tl.items[2].comp["Mask1"])
    assert status(CF.run(sb, None, tl), "mask1", 3) == ["fail"]
    # 素通しの Merge の Blend が時間で変わっても、肩テロップとはみなさない
    passed(tl.items[0].comp["BadgeOver"])
    assert status(CF.run(sb, None, tl), "badge", 1) == ["unknown"]


def test_source_pixels_are_converted_and_unknown_size_is_unknown(sb):
    """構成表のマスクとズーム範囲は素材の画素。4K の素材なら 2 倍の座標で書いたものが 1080p の Fusion の値と合う。
    素材の解像度を読めなければ、マスクとズームは unknown (黙って pass にしない)。"""
    good = build(sb)
    for r in sb["rows"]:
        for k in ("zoom",):
            if r.get(k):
                r[k] = dict(r[k], **{c: r[k][c] * 2 for c in "xywh"})
        if r.get("masks"):
            r["masks"] = [dict(m, **{c: m[c] * 2 for c in "xywh"}) for m in r["masks"]]
    for it in good.items:
        it.mpi.resolution = "3840x2160"
    rep = CF.run(sb, None, good)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "zoom_size", 3) == ["pass"]
    for it in good.items:
        it.mpi.resolution = ""
    rep = CF.run(sb, None, good)
    assert status(rep, "mask1", 3) == ["unknown"] and status(rep, "zoom", 3) == ["unknown"]


def test_no_timeline_is_unknown(sb):
    rep = CF.run(sb, None, None)
    assert status(rep, "timeline") == ["unknown"] and rep["summary"]["pass"] == 0


@pytest.mark.parametrize("row, badge, per, want", [
    pytest.param(2, "4 倍速", None, "pass", id="4x-says-4x"),
    pytest.param(2, "4倍速", None, "pass", id="4x-says-4x-no-space"),
    pytest.param(2, "4.0 倍速", None, "pass", id="4x-says-4.0x"),
    pytest.param(2, "5 倍速", None, "fail", id="4x-says-5x"),
    pytest.param(2, "等倍", None, "fail", id="4x-says-same"),
    pytest.param(1, "等倍", None, "pass", id="same-says-same"),
    pytest.param(2, "9 倍速", "source", "fail", id="source-storyboard-says-9x"),
    pytest.param(2, "等倍", "source", "pass", id="source-storyboard-says-same"),
])
def test_badge_speed(sb, row, badge, per, want):
    """肩テロップの文の速さと行の速さ (テストの構成表の 2 行目は 20 秒を 5 秒 = 4 倍、1 行目は等倍)。
    タイムラインを読まなくても出る。fail の行は区切りの画像の行に入る。"""
    sb["rows"][row - 1]["badge"] = badge
    if per is not None:
        sb["seconds_per_row"] = per
    rep = CF.run(sb, None, None)
    assert status(rep, "badge_speed", row) == [want]
    assert (row in rep["sheet"]["rows"]) == (want == "fail")


def test_badge_speed_absent_without_speed_words(sb):
    """肩テロップに速さの表記が無い行 (テストの構成表の 1 行目「肩テロップのテスト」) と肩テロップの無い行は項目を出さない。"""
    assert status(CF.run(sb, None, build(sb)), "badge_speed") == []


def test_badge_speed_excludes_hold(sb):
    """止めた区間は速さに入れない (2 行目は止めても 4 倍)。"""
    sb["rows"][1] |= {"badge": "4 倍速", "hold": {"seconds": 2}}
    assert status(CF.run(sb, None, None), "badge_speed", 2) == ["pass"]


def test_boundary_frames(sb):
    exp = CF.expected_rows(sb)
    got = [f for f, _ in CF.boundary_frames(exp[0], 480)]
    assert got == [0, 1, 59, 60, 61, 149, 150, 151]
    got = [f for f, _ in CF.boundary_frames(exp[2], 480)]
    assert got == [299, 300, 301, 389, 390, 391, 479]   # 動画の最後のフレームの次 (480) からは入れない


# ---------- 合成した動画 ----------
def _ffmpeg():
    try:
        return C.Config().tool("ffmpeg")
    except SystemExit:
        return None


@pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg が無い")
@pytest.mark.meiryo
def test_pixels_and_frames_on_synthetic_video(sb, monkeypatch, offline_work):
    work = os.path.join(offline_work, "check_final")
    os.makedirs(work)
    b = C.badge_box(sb, sb["badge"]["prefix"] + sb["rows"][0]["badge"], C.badge_font(sb))
    x, y = round((b["center"][0] - b["width"] / 2) * 1920), round((1 - b["center"][1] - b["height"] / 2) * 1080)
    w, h = round(b["width"] * 1920), round(b["height"] * 1080)
    r, g, bl = sb["badge"]["rgba"][:3]

    def make(name, off, frames):
        out = os.path.join(work, name)
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=1920x1080:rate=30", "-frames:v", str(frames),
                        "-vf", f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{r:02X}{g:02X}{bl:02X}@0.92:t=fill:"
                               f"enable='lt(n,{off})'",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", out], check=True)
        return out

    try:
        good = CF.run(sb, make("good.mp4", 60, 480), build(sb), pixel=True)
        assert good["summary"]["fail"] == 0 and good["summary"]["unknown"] == 0, good["checks"]
        assert good["sheet"]["frames"] == [] and good["border_pixels_skipped"] == []
        assert good["color_read"][good["video"]]["matrix"]   # 書き出しを rgb24 に直したときの読み方
        # タイムラインを読んでいない unknown だけなら、区切りの画像は作らない (画像を見ても判定できない)
        no_tl = CF.run(sb, os.path.join(work, "good.mp4"), None, pixel=True)
        assert status(no_tl, "timeline") == ["unknown"] and no_tl["sheet"] == {"rows": [], "frames": []}
        bad = CF.run(sb, make("bad.mp4", 61, 481), build(sb), pixel=True)
        assert status(bad, "badge_pixels_60", 1) == ["fail"] and status(bad, "badge_pixels_59", 1) == ["pass"]
        assert status(bad, "video_frames") == ["fail"]
        monkeypatch.setenv("RESOLVE_OUT_DIR", work)
        line = CF.write_report(C.Config(), bad, "bad")
        assert line.startswith("failed=2 unknown=0 report=") and "sheet=" in line
        from PIL import Image
        assert Image.open(os.path.join(work, "check-final", "bad.png")).width > 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- 見逃しやすい 3 件 (素通しのモザイク・肩テロップの出直し・短い行の肩テロップ) と、マスクの形 ----------
def test_passed_mosaic_with_live_mask_fails(sb):
    """(a) マスクの値とつながりが残っていても、つながった先のモザイクが素通しなら隠れていないので fail。"""
    tl = build(sb)
    passed(tl.items[2].comp["Mask1Mosaic"])
    rep = CF.run(sb, None, tl)
    # 正しい大きさのマスクが選ばれ、位置は pass でも、隠す部品が素通しなので種類の項目が fail
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask1_type", 3) == ["fail"]


def test_badge_reappearing_mid_row_fails(sb):
    """(b) 肩テロップが行の途中 (100〜110 フレーム) で出直すのを見逃さない。"""
    tl = build(sb)
    tl.items[0].comp["BadgeOver"].put(Blend=lambda t: 1.0 if t < 60 or 100 <= t < 110 else 0.0)
    assert status(CF.run(sb, None, tl), "badge", 1) == ["fail"]


def test_short_row_badge_passes(sb):
    """(c) 2 秒より短い行 (1.5 秒) の正しい肩テロップ (行の後ろの 60 フレーム目で消える) は pass。"""
    sb["rows"][0]["seconds"] = 1.5
    sb["rows"][0]["end"] = 1.5
    tl = build(sb)
    assert status(CF.run(sb, None, tl), "badge", 1) == ["pass"]
    tl.items[0].comp["BadgeOver"].put(Blend=shown(40))   # 行の中で早く消えるのは fail
    assert status(CF.run(sb, None, tl), "badge", 1) == ["fail"]


def test_moving_blur_and_black_masks(sb):
    """keys の付いたマスク (素材の画素、4K) と、ぼかし・黒塗りの種類を読む。読めない書式は unknown。"""
    sb["rows"][2]["masks"] = [
        {"type": "blur", "shape": "ellipse", "x": 0, "y": 0, "w": 200, "h": 100,
         "keys": [{"t": 0, "x": 0, "y": 0, "w": 200, "h": 100}, {"t": 2, "x": 2000, "y": 1000, "w": 400, "h": 200}]},
        {"type": "black", "x": 100, "y": 100, "w": 100, "h": 100}]
    tl, moved = build(sb, K4), build(sb, K4)
    sb["rows"][2]["masks"].append({"type": "pixel", "x": 0, "y": 0, "w": 1, "h": 1})
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask1_type", 3) == ["pass"]
    assert status(rep, "mask2", 3) == ["pass"] and status(rep, "mask2_type", 3) == ["pass"]
    assert status(rep, "mask3", 3) == ["unknown"]
    # 動きが key と違う (途中で止まる) と fail。種類が違う (ぼかしの所がモザイク) と fail
    for k in ("Center", "Width", "Height"):
        rekey(moved.items[2].comp["Mask1"], k, lambda t: t / 2)
    assert status(CF.run(sb, None, moved), "mask1", 3) == ["fail"]
    tl.items[2].comp["Mask1Blur"].ID = CF.EFFECTS["mosaic"]
    assert status(CF.run(sb, None, tl), "mask1_type", 3) == ["fail"]


def test_fast_row_reads_fusion_at_clip_speed(sb):
    """4 倍速の行の合成の時刻は素材の速さで進む。肩テロップは合成の 240 で消えるのが正しく、60 で消えるのは fail。"""
    sb["rows"][1]["badge"] = "4 倍速"
    tl = build(sb)
    assert status(CF.run(sb, None, tl), "badge", 2) == ["pass"]
    tl.items[1].comp["BadgeOver"].put(Blend=shown(60))
    assert status(CF.run(sb, None, tl), "badge", 2) == ["fail"]


FOCUS = {"x": 1600, "y": 600, "w": 1200, "h": 800, "dim": 0.5, "seconds": 0.5}


def test_focus_position_dim_and_time(sb):
    """周りを暗くする: 範囲 (素材 4K の画素)、濃さ、暗くなる時間を読む。違えば fail、部品が素通しなら fail、書式が読めなければ unknown。"""
    sb["rows"][0]["focus"] = dict(FOCUS)

    def run(change=lambda c: None, **focus):
        tl = build(made(sb, 0, focus=dict(sb["rows"][0]["focus"], **focus)), K4)
        change(tl.items[0].comp)
        rep = CF.run(sb, None, tl)
        return [(status(rep, k, 1) or [None])[0] for k in ("focus", "focus_dim", "focus_time")]
    assert run() == ["pass", "pass", "pass"]
    assert run(dim=0.6)[1] == "fail"
    assert run(seconds=1)[2] == "fail"
    assert run(lambda c: passed(c["FocusDim"]))[0] == "fail"
    sb["rows"][0]["focus"]["dim"] = 3
    assert run()[0] == "unknown"


def test_effect_not_reaching_media_out_fails(sb):
    """マスクのつながり先のモザイクが MediaOut まで届かない (切り離された) なら、隠れていないので fail。"""
    tl = build(sb)
    detach(tl.items[2].comp, "Mask1Mosaic")
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask1_type", 3) == ["fail"]


def test_fast_row_keys_use_comp_time(sb):
    """1.5 倍速の行の key (書き出しの 31 フレーム目) は合成の 46 に入る。期待する位置も合成の時刻どうしで補間する。"""
    sb["rows"][2].update(start=30, end=39, seconds=6)   # 9 秒を 6 秒 = 1.5 倍
    sb["rows"][2].pop("zoom")
    sb["rows"][2]["masks"] = [{"type": "blur", "shape": "ellipse", "x": 0, "y": 0, "w": 100, "h": 100,
                               "keys": [{"t": 0, "x": 0, "y": 0, "w": 100, "h": 100},
                                        {"t": 31 / 30, "x": 1000, "y": 0, "w": 100, "h": 100}]}]
    tl = build(sb)
    assert tl.items[2].speed == 150.0 and sorted(tl.items[2].comp["Mask1"].links["Width"].keys) == [0, C.comp_frame(31, 1.5)]
    assert status(CF.run(sb, None, tl), "mask1", 3) == ["pass"]


def test_unreadable_zoom_does_not_crash(sb):
    """書式を読めないズーム (w が 0) は unknown にし、区切りのフレームの計算で落ちない。"""
    tl = build(sb)
    sb["rows"][2]["zoom"] = {"x": 0, "y": 0, "w": 0, "h": 100, "seconds": 1}
    rep = CF.run(sb, None, tl)
    assert status(rep, "zoom", 3) == ["unknown"] and 3 in rep["sheet"]["rows"]


def test_focus_dim_one_and_detached(sb):
    """dim 1 (暗くしない) は途中の値の条件を見ないので pass。FocusDim が MediaOut に届かなければ fail。"""
    sb["rows"][0]["focus"] = dict(FOCUS, dim=1)
    tl = build(sb, K4)
    rep = CF.run(sb, None, tl)
    assert status(rep, "focus_dim", 1) == ["pass"] and status(rep, "focus_time", 1) == ["pass"]
    detach(tl.items[0].comp, "FocusDim")
    assert status(CF.run(sb, None, tl), "focus", 1) == ["fail"]


def test_badge_box_follows_text_and_closing(sb):
    """肩テロップの板は文の長さに合わせて広がる。板の幅が計算と違えば fail。結びのテロップは ClosingText の文・大きさ・中央。"""
    if C.badge_font(sb) is None:
        pytest.skip("書体のファイルが無い")
    long_text = sb["badge"]["prefix"] + "約 3 分 20 秒の待ち時間をスキップ"
    assert C.badge_box(sb, long_text, C.badge_font(sb))["width"] > sb["badge"]["width"]
    tl = build(sb)
    assert status(CF.run(sb, None, tl), "badge_box", 1) == ["pass"]
    comp = tl.items[0].comp
    comp["BadgeMask"].put(Width=comp["BadgeMask"].inputs["Width"] * 0.9)
    assert status(CF.run(sb, None, tl), "badge_box", 1) == ["fail"]
    comp.tools.remove(comp["BadgeMask"])
    assert status(CF.run(sb, None, tl), "badge_box", 1) == ["unknown"]
    # 結びのテロップ (2 行目に書く)
    sb["rows"][1].update(caption="結びの 1 行目|結びの 2 行目", caption_style="closing")
    tl = build(sb)
    comp = tl.items[1].comp
    assert status(CF.run(sb, None, tl), "closing", 2) == ["pass"]
    comp["ClosingText"].put(StyledText="x", Size=0.06, Center={1: 0.5, 2: 0.1})
    assert status(CF.run(sb, None, tl), "closing", 2) == ["fail"]
    comp.tools.remove(comp["ClosingText"])
    assert status(CF.run(sb, None, tl), "closing", 2) == ["fail"]


def test_caption_checks(sb):
    """通常のテロップ: 組む側と同じ部品なら 3 項目とも pass。文字違い・改行違い・消去・出口からの切断は caption、
    帯の色・つながりの異常は caption_bar、行の途中で消えるものは caption_time が fail。値を読めなければ unknown。
    テロップの無い行に MediaOut に届くテロップがあれば caption が fail。"""
    keys = ("caption", "caption_bar", "caption_time")

    def run(change=None, row=0):
        tl = build(sb)
        if change:
            change(tl.items[row].comp)
        rep = CF.run(sb, None, tl)
        return [(status(rep, k, row + 1) or [None])[0] for k in keys]

    def put(name, **kv):
        return lambda c: c[name].put(**kv)

    def wire(name, **inputs):
        """部品 name の入力に、別の部品 (名前か部品) の出力をつなぎ直す (None は外す)。"""
        def change(c):
            for k, v in inputs.items():
                delattr(c[name], k) if v is None else setattr(c[name], k, c[v] if isinstance(v, str) else v)
        return change

    def nameless(c):   # 前景の部品の名前を読めない
        c["CaptionOver"].Foreground = c.AddTool("TextPlus")
        c["CaptionOver"].Foreground.Name = None
    assert run() == ["pass", "pass", "pass"]
    assert run(put("CaptionText", StyledText="1 行目: 普通の速さ (6 秒)"))[0] == "fail"   # 文字違い
    assert run(put("CaptionText", Size=0.05))[0] == "fail"
    assert run(lambda c: c.tools.remove(c["CaptionText"]))[0] == "fail"                  # テロップの消去
    assert run(lambda c: detach(c, "CaptionOver"))[0] == "fail"                          # 出口から切断
    assert run(lambda c: wire("CaptionOver", Foreground=c.AddTool("Merge"))(c))[0] == "fail"   # 別の部品が前景
    # 前景と背景を入れ替える (不透明な背景の帯が文を隠す)
    assert run(wire("CaptionOver", Foreground="CaptionBarOver", Background="CaptionText"))[0] == "fail"
    assert run(wire("CaptionOver", Foreground=object()))[0] == "unknown"                # つながりを読めない
    assert run(nameless)[0] == "unknown"
    assert run(put("CaptionBar", TopLeftAlpha=1.0)) == ["pass", "fail", "pass"]              # 帯の不透明度
    assert run(put("CaptionBarMask", Height=0.05))[1] == "fail"                             # 帯の高さ
    assert run(wire("CaptionBar", EffectMask=None))[1] == "fail"                            # 帯の四角が背景に効いていない
    assert run(wire("CaptionBarOver", Foreground=None, Background="CaptionBar"))[1] == "fail"   # 帯を背景の側につないだ
    assert run(lambda c: passed(c["CaptionBarOver"]))[1] == "fail"
    assert run(put("CaptionOver", Blend=lambda t: 0.0 if t < 15 else 1.0)) == ["pass", "pass", "fail"]   # 表示区間のずれ
    assert run(put("CaptionBarOver", Blend=lambda t: 1.0 if t < 140 else 0.0))[2] == "fail"
    assert run(put("CaptionText", StyledText=None))[0] == "unknown"
    assert run(put("CaptionOver", Blend=lambda t: None))[2] == "unknown"
    # 改行は common.fit_caption と同じ (| で区切る)
    sb["rows"][0]["caption"] = "1 行目|2 行目"
    assert run() == ["pass", "pass", "pass"]
    assert run(put("CaptionText", StyledText="1 行目 2 行目"))[0] == "fail"
    # 構成表にテロップが無い行に、MediaOut に届くテロップがある
    tl = build(sb)
    sb["rows"][1].pop("caption")
    rep = CF.run(sb, None, tl)
    assert status(rep, "caption", 2) == ["fail"] and status(rep, "caption_time", 2) == []
    assert status(CF.run(sb, None, build(sb)), "caption", 2) == []


def test_hold_checks(sb):
    """止めたクリップの位置・長さ・速さ・映すフレーム。後ろの行は hold の分だけずれる。無ければ fail、書式を読めなければ unknown。"""
    sb["rows"][0]["hold"] = {"seconds": 2}
    tl = build(sb)
    hold, mpi = tl.items[1], tl.items[1].mpi
    assert (hold.start, hold.speed) == (HEAD + 150, 0.0)
    rep = CF.run(sb, None, tl)
    assert status(rep, "hold", 1) == ["pass"] and status(rep, "items") == ["pass"]
    assert status(rep, "position", 2) == ["pass"] and status(rep, "position", 3) == ["pass"]
    assert [status(rep, k, 1) for k in ("hold_caption", "hold_caption_bar", "hold_caption_time")] == [["pass"]] * 3
    assert rep["summary"]["fail"] == 0
    comp, hold.comp = hold.comp, None   # 止めた区間にもテロップは出続ける (common.hold_values)
    assert status(CF.run(sb, None, tl), "hold_caption", 1) == ["fail"]
    # 素材の解像度を読めなくても (マスクなどは見ない)、止めたクリップのテロップは見る
    hold.comp = comp
    mpi.resolution = ""
    rep = CF.run(sb, None, tl)
    assert [status(rep, k, 1) for k in ("hold_caption", "hold_caption_bar", "hold_caption_time")] == [["pass"]] * 3
    hold.comp = None
    assert status(CF.run(sb, None, tl), "hold_caption", 1) == ["fail"]
    mpi.resolution = "1920x1080"
    hold.src = (140, 140)          # 違うフレームで止まっている
    assert status(CF.run(sb, None, tl), "hold", 1) == ["fail"]
    hold.src = (148, 148)          # 1 手前。組んだ記録の near が無ければ fail
    rep = CF.run(sb, None, tl)
    assert status(rep, "hold", 1) == ["fail"] and rep["warnings"] == []
    rep = CF.run(sb, None, tl, near={1: 148})   # 記録の near と同じフレームなら pass で、警告に出る
    assert status(rep, "hold", 1) == ["pass"] and len(rep["warnings"]) == 1 and "148" in rep["warnings"][0]
    assert status(CF.run(sb, None, tl, near={1: 150}), "hold", 1) == ["fail"]   # 記録と違うフレーム
    hold.src = (147, 147)          # 2 違えば記録があっても fail
    assert status(CF.run(sb, None, tl, near={1: 147}), "hold", 1) == ["fail"]
    sb["rows"][0]["hold"] = {"seconds": 2, "at": "x"}
    assert status(CF.run(sb, None, tl), "hold", 1) == ["unknown"]


def test_read_near(tmp_path, monkeypatch):
    """組んだ記録から隣のフレームで止めた行を読む。別のタイムラインの記録・記録が無いときは空。"""
    class Cfg:
        def out_dir(self, name):
            return str(tmp_path / name)
    class TL:
        def __init__(self, name):
            self.name = name
        def GetName(self):
            return self.name
    (tmp_path / "sb2resolve").mkdir()
    log = {"timeline_name": "t-v1", "timeline": {"rows": [{"row": 1, "hold": {"near": {"want": 149, "got": 148}}},
                                                          {"row": 2, "hold": {"match": True}}, {"row": 3}]}}
    (tmp_path / "sb2resolve" / "x.json").write_text(json.dumps(log), encoding="utf-8")
    assert CF.read_near(Cfg(), "x.json", TL("t-v1")) == {1: 148}
    assert CF.read_near(Cfg(), "x.json", TL("t-v2")) == {}
    assert CF.read_near(Cfg(), "y.json", TL("t-v1")) == {}
    assert CF.read_near(Cfg(), "x.json", None) == {}


def _one_row(sb, W, H, **row):
    """W x H のタイムラインと素材で、2 秒の行のあとに 1 秒止める 1 行だけの構成表。"""
    one = copy.deepcopy(sb)
    one["timeline"]["width"], one["timeline"]["height"] = W, H
    one["rows"] = [dict({"start": "0:00", "end": "0:02", "seconds": 2, "hold": {"seconds": 1}}, **row)]
    return one


def test_hold_mask_follows_each_timeline_size(sb):
    """違う解像度のタイムラインを続けて見ても、止めたクリップのマスクはその回の幅と高さで画素に戻る。"""
    for W, H in ((1920, 1080), (1280, 720)):
        one = _one_row(sb, W, H, masks=[{"type": "mosaic", "x": 100, "y": 50, "w": 200, "h": 80}])
        rep = CF.run(one, None, build(one, (W, H)))
        assert status(rep, "hold", 1) == ["pass"], (W, H)
        assert status(rep, "mask1", 1) == ["pass"], (W, H, status(rep, "mask1", 1))
        assert status(rep, "hold_mask1", 1) == ["pass"], (W, H, [c for c in rep["checks"] if c["item"] == "hold_mask1"])
    assert not hasattr(CF, "W_H")


def test_hold_focus_follows_each_timeline_size(sb):
    """違う解像度のタイムラインを続けて見ても、止めたクリップの focus はその回の幅と高さで画素に戻る。

    1920x1080 の次に 1280x720。素材の解像度はタイムラインと同じ (画素は 1:1)。
    その回の幅と高さで正規化した座標は hold_focus が pass。1280 の正しい座標は、前の 1920 が残ると fail になる。
    座標をずらしたものは、どちらの大きさでも fail。"""
    focus = {"x": 100, "y": 50, "w": 200, "h": 80}
    for W, H in ((1920, 1080), (1280, 720)):
        one = _one_row(sb, W, H, focus=focus)
        good = CF.run(one, None, build(one, (W, H)))
        assert status(good, "hold_focus", 1) == ["pass"], (W, H, [c for c in good["checks"] if c["item"] == "hold_focus"])
        bad = CF.run(one, None, build(made(one, 0, focus=dict(focus, x=140)), (W, H)))
        assert status(bad, "hold_focus", 1) == ["fail"], (W, H, [c for c in bad["checks"] if c["item"] == "hold_focus"])
    assert not hasattr(CF, "W_H")


def test_focus_border(sb):
    """枠線: 色・外の四角・現れる時刻が合えば pass。色が違えば fail、部品が無ければ fail。"""
    sb["rows"][0]["focus"] = dict(FOCUS, border={"color": "#FFC800", "width": 6})

    def run(change=lambda c: None, border=sb["rows"][0]["focus"]["border"]):
        tl = build(made(sb, 0, focus=dict(FOCUS, border=border)), K4)
        change(tl.items[0].comp)
        return status(CF.run(sb, None, tl), "focus_border", 1)
    assert run() == ["pass"]
    assert run(border={"color": "#FFFFFF", "width": 6}) == ["fail"]
    assert run(border=None) == ["fail"]
    # 最初から出ている枠線 (Blend が頭で 1) と、輪になっていない (Multiply でない) 枠線は fail
    assert run(lambda c: c["FocusBorderOver"].put(Blend=1.0)) == ["fail"]
    assert run(lambda c: c["FocusBorderOuter"].put(PaintMode="Merge")) == ["fail"]


CALLOUT = {"src": {"x": 2600, "y": 380, "w": 360, "h": 280}, "dst": {"x": 300, "y": 600, "w": 540}, "start": 1, "in": 0.4}


def test_callout_checks(sb):
    """callout: 切り抜き・置き場所・拡大・現れる時刻が合えば pass。拡大が違えば fail、部品が無ければ fail、書式が読めなければ unknown。"""
    sb["rows"][0]["callout"] = dict(CALLOUT)
    tl = build(sb, K4)
    rep = CF.run(sb, None, tl)
    assert status(rep, "callout", 1) == ["pass"] and status(rep, "callout_time", 1) == ["pass"]
    tl.items[0].comp["Callout"].put(Size=2.0)
    assert status(CF.run(sb, None, tl), "callout", 1) == ["fail"]
    tl = build(made(sb, 0, callout=None), K4)
    assert status(CF.run(sb, None, tl), "callout", 1) == ["fail"]
    sb["rows"][0]["callout"]["dst"] = {"x": 0}
    assert status(CF.run(sb, None, tl), "callout", 1) == ["unknown"]


def test_border_pixels_before_it_appears(sb):
    """--pixel: 枠線が現れる前 (行の頭) の輪の画素が素材と同じなら pass。輪の色が足されて明るければ fail。"""
    import numpy as np
    sb["rows"][0]["focus"] = {"x": 1600, "y": 600, "w": 1200, "h": 800, "seconds": 0.5, "border": True}
    exp = CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})
    src = np.full((1080, 1920, 3), 128, np.uint8)
    good = CF.check_border_pixels(sb, exp, None, reader=lambda n: src, source_reader=lambda p, t: src)
    assert [c["status"] for c in good] == ["pass"]
    bright = src.copy()
    bright[495:506, 795:806] = (253, 253, 128)    # 左の辺の真ん中 (800, 500) に枠線の色が足されている
    bad = CF.check_border_pixels(sb, exp, None, reader=lambda n: bright, source_reader=lambda p, t: src)
    assert [c["status"] for c in bad] == ["fail"]


def test_callout_several_sources(sb):
    """callout の src の list: 切り抜きごとに Callout・Callout2 を見る。2 つ目の位置が違えば callout_2 が fail。"""
    srcs = [{"x": 2600, "y": 380, "w": 360, "h": 280}, {"x": 2600, "y": 700, "w": 180, "h": 100}]
    sb["rows"][0]["callout"] = {"src": srcs, "dst": {"x": 300, "y": 200, "w": 540}, "gap": 4, "start": 1, "in": 0.4}
    tl = build(sb, K4)
    rep = CF.run(sb, None, tl)
    assert [status(rep, k, 1) for k in ("callout", "callout_time", "callout_2", "callout_time_2")] == [["pass"]] * 4
    tl.items[0].comp["Callout2"].put(Center={1: (300 + 135) / 1920, 2: 0.5})
    rep = CF.run(sb, None, tl)
    assert status(rep, "callout", 1) == ["pass"] and status(rep, "callout_2", 1) == ["fail"]


def test_focus_start_time(sb):
    """focus の start: 30 フレーム目から 15 フレームで暗くなる部品なら pass。行の頭から暗くなる部品は fail。"""
    sb["rows"][0]["focus"] = dict(FOCUS, start=1)
    assert status(CF.run(sb, None, build(sb, K4)), "focus_time", 1) == ["pass"]
    assert status(CF.run(sb, None, build(made(sb, 0, focus=FOCUS), K4)), "focus_time", 1) == ["fail"]


# ---------- 行の途中で消える・すぐに全部出る部品と、止めたクリップの部品 ----------
def test_black_blend_zero_mid_row_fails(sb):
    """黒塗りの Merge の Blend が行の途中 (100〜110) で 0 になると fail。時刻 0 だけを読むと見逃す。
    キーを読めない部品は行の全部のフレームで読み、キーを読める部品はキーの時刻とその前後で読む。"""
    sb["rows"][2]["masks"] = [{"type": "black", "x": 100, "y": 100, "w": 100, "h": 100}]
    reads = []

    class Counted(Spline):   # 読んだ時刻を覚えるスプライン
        def at(self, t):
            reads.append(t)
            return super().at(t)

    def run(blend=None, keys=None):
        tl = build(sb)
        merge, spline = tl.items[2].comp["Mask1Over"], Counted("BezierSpline")
        if blend:
            merge.put(Blend=blend)
        if keys:
            spline.keys = keys
            merge.ConnectInput("Blend", spline)
        return status(CF.run(sb, None, tl), "mask1_type", 3), spline
    assert run()[0] == ["pass"]
    assert run(lambda t: 0.0 if 100 <= t <= 110 else 1.0)[0] == ["fail"]
    assert run(keys={0: 1.0, 99: 1.0, 100: 0.0, 110: 0.0, 111: 1.0})[0] == ["fail"]
    # キーの間で値がずっと 1 なら、キーの時刻だけを読んで pass (行の全部は読まない)
    reads.clear()
    got, flat = run(keys={0: 1.0, 179: 1.0})
    assert got == ["pass"] and len(reads) < 10
    # 実機と違う形 ({番号: 時刻}) が返ったら、キーを読めないものとして行の全部のフレームを読む
    reads.clear()
    Counted.GetKeyFrames = lambda self: {1: 0.0, 2: 179.0}
    assert run(keys={0: 1.0, 179: 1.0})[0] == ["pass"] and len(reads) > 100


def _focus_row(sb, border=False):
    sb["rows"][0]["focus"] = dict(FOCUS)
    if border:
        sb["rows"][0]["focus"]["border"] = {"color": "#FFC800", "width": 6}
    return build(sb, K4)


def test_focus_dim_blend_zero_fails(sb):
    """FocusDim の Blend が 0 (行の途中から 0 も) なら、FocusShade の不透明度が正しくても focus_dim が fail。"""
    tl = _focus_row(sb)
    for blend, want in ((1.0, "pass"), (0.0, "fail"), (lambda t: 0.0 if t >= 90 else 1.0, "fail")):
        tl.items[0].comp["FocusDim"].put(Blend=blend)
        assert status(CF.run(sb, None, tl), "focus_dim", 1) == [want], blend


def test_focus_time_between_keys(sb):
    """暗くなる割合をキーの間も読む: 頭と終わりのキーの値が合っていても、1 フレーム目で暗くなりきるものは fail。
    暗くなった後に行の途中で明るく戻るものも fail。"""
    tl = _focus_row(sb)
    for alpha in (lambda t: 0.0 if t == 0 else 0.5, lambda t: 0.0 if 100 <= t < 110 else 0.5 * min(t, 15) / 15):
        tl.items[0].comp["FocusShade"].put(TopLeftAlpha=alpha)
        assert status(CF.run(sb, None, tl), "focus_time", 1) == ["fail"]


def test_callout_appearing_at_once_fails(sb):
    """現れ始めと現れ終わりのキーの値が合っていても、現れ始めてすぐに全部出る callout は fail。行の途中で消えるものも fail。"""
    sb["rows"][0]["callout"] = dict(CALLOUT)
    tl = build(sb, K4)
    for ramp, want in ((lambda t: 0.0 if t <= 30 else min(1.0, (t - 30) / 12), "pass"),
                       (lambda t: 0.0 if t <= 30 else 1.0, "fail"),
                       (lambda t: 0.0 if t <= 30 or t > 100 else min(1.0, (t - 30) / 12), "fail")):
        tl.items[0].comp["Callout"].put(Blend=ramp)
        assert status(CF.run(sb, None, tl), "callout_time", 1) == [want]


def test_hold_checks_focus_border_and_callout(sb):
    """止めたクリップでも focus (枠線も) と callout を見る。抜けていれば hold_focus・hold_focus_border・hold_callout が fail。
    暗くなる・現れるのが行の中で終わっていれば、止めたクリップでは最初から出たまま。"""
    sb["rows"][0]["hold"] = {"seconds": 2}
    sb["rows"][0]["callout"] = {"src": {"x": 2600, "y": 380, "w": 360, "h": 280}, "dst": {"x": 300, "y": 600, "w": 540}}
    tl = _focus_row(sb, border=True)
    hold = tl.items[1]
    names = ("hold_focus", "hold_focus_dim", "hold_focus_time", "hold_focus_border", "hold_callout", "hold_callout_time")
    rep = CF.run(sb, None, tl)
    assert [status(rep, k, 1) for k in names] == [["pass"]] * 6, [c for c in rep["checks"] if c["status"] != "pass"]
    comp, hold.comp = hold.comp, None
    rep = CF.run(sb, None, tl)
    assert [status(rep, k, 1) for k in ("hold_focus", "hold_callout")] == [["fail"]] * 2
    # 止めたクリップの枠線が抜けている・消えている (Blend が 0) と fail
    hold.comp = comp
    comp["FocusBorderOver"].put(Blend=0.0)
    assert status(CF.run(sb, None, tl), "hold_focus_border", 1) == ["fail"]
    comp["FocusBorderOver"].put(Blend=1.0)   # Blend は元に戻し、名前だけで探せなくする (つながりは残す)
    comp["FocusBorderOver"].Name = "RenamedBorderOver"
    assert status(CF.run(sb, None, tl), "hold_focus_border", 1) == ["fail"]


def test_border_pixels_under_caption_bar(sb):
    """枠線の点がテロップの背景 (帯) に重なる行。帯の色を重ねた色と比べるので、正しい書き出しは pass。
    行の頭から寄っている行 (ズームの秒数が 0) は見ない。"""
    import numpy as np
    if C.caption_font(sb) is None:
        pytest.skip("書体のファイルが無い")
    sb["rows"][0]["caption"] = "短い"
    fit = C.fit_caption(sb, "短い", C.caption_font(sb))
    top = (1 - fit["bar_center"] - fit["bar_height"] / 2) * 1080
    bottom = (1 - fit["bar_center"] + fit["bar_height"] / 2) * 1080
    # 4K の素材で、左の辺が帯の中 (文字の外)、上の辺が帯の上にかかる範囲
    sb["rows"][0]["focus"] = {"x": 200, "y": round((top - 40) * 2), "w": 400, "h": 200, "seconds": 0.5, "border": True}
    exp = CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})
    keep, over, gone = CF.border_points(sb, exp[0])
    assert any(over[k] for k in keep) and any(over[k] is None for k in keep), (keep, gone)
    src = np.full((1080, 1920, 3), 200, np.uint8)
    out = src.copy()
    y0, y1 = round(top), round(bottom)
    rgba = sb["caption"]["bar_rgba"]
    out[y0:y1] = (src[y0:y1] * (1 - rgba[3] / 255) + np.array(rgba[:3]) * rgba[3] / 255).astype(np.uint8)
    good = CF.check_border_pixels(sb, exp, None, reader=lambda n: out, source_reader=lambda p, t: src)
    assert [c["status"] for c in good] == ["pass"], good
    # 帯の中の点に枠線の色が出ていれば fail
    k = next(k for k in keep if over[k])
    px, py = (round(v) for v in keep[k])
    bad = out.copy()
    bad[py - 3:py + 4, px - 3:px + 4] = (253, 200, 0)
    assert [c["status"] for c in CF.check_border_pixels(sb, exp, None, reader=lambda n: bad,
                                                        source_reader=lambda p, t: src)] == ["fail"]
    # 行の頭から寄っている行は見ない
    sb["rows"][0]["zoom"] = {"x": 0, "y": 0, "w": 1920, "h": 1080, "seconds": 0}
    exp = CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})
    assert CF.check_border_pixels(sb, exp, None, reader=lambda n: bad, source_reader=lambda p, t: src) == []
    assert CF.border_rows(sb, exp)[1] == [{"row": 1, "reason": "頭から寄っている"}]


def _skip_case(sb):
    """1 行目: 枠線があり頭から寄っている。2 行目: 枠線の無い focus。3 行目: 枠線があり比べられる。"""
    sb["rows"][0]["focus"] = {"x": 1600, "y": 600, "w": 1200, "h": 800, "seconds": 0.5, "border": True}
    sb["rows"][0]["zoom"] = {"x": 0, "y": 0, "w": 1920, "h": 1080, "seconds": 0}
    sb["rows"][1]["focus"] = {"x": 1600, "y": 600, "w": 1200, "h": 800, "seconds": 0.5}
    sb["rows"][2].pop("zoom", None)
    sb["rows"][2]["focus"] = {"x": 1600, "y": 600, "w": 1200, "h": 800, "seconds": 0.5, "border": True}
    return CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})


def test_border_pixels_skipped_rows(sb, monkeypatch):
    """--pixel で枠線の画素を比べなかった行と理由。枠線の無い行は入れない。比べなかった行は fail にも unknown にもしない。"""
    import numpy as np
    exp = _skip_case(sb)
    rows, skipped = CF.border_rows(sb, exp)
    assert [r[0]["row"] for r in rows] == [3]
    assert skipped == [{"row": 1, "reason": "頭から寄っている"}]
    src = np.full((1080, 1920, 3), 128, np.uint8)
    got = CF.check_border_pixels(sb, exp, None, reader=lambda n: src, source_reader=lambda p, t: src)
    assert [(c["row"], c["status"]) for c in got] == [(3, "pass")]
    # 比べる点が全部ほかの部品に重なる行
    orig = CF._overlays
    monkeypatch.setattr(CF, "_overlays", lambda s, row: ([[-1e5, -1e5, 1e5, 1e5]], []) if row["row"] == 3 else orig(s, row))
    assert CF.border_rows(sb, exp) == ([], [{"row": 1, "reason": "頭から寄っている"},
                                            {"row": 3, "reason": "比べる点が残らない"}])


def test_border_rows_unreadable_focus(sb):
    """書式を読めない focus は、元の構成表の行が枠線を求めているときだけ比べなかった行に入れる。
    枠線の無い focus は入れない (偽の border_unchecked を出さない)。"""
    sb["rows"][0]["focus"] = {"x": 1600, "y": 600, "w": -5, "h": 800, "seconds": 0.5}                   # 枠線なし
    sb["rows"][1]["focus"] = {"x": 1600, "y": 600, "w": -5, "h": 800, "seconds": 0.5, "border": True}   # 枠線あり
    exp = CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})
    assert exp[0]["focus"].get("unreadable") and exp[1]["focus"].get("unreadable")
    assert CF.border_rows(sb, exp) == ([], [{"row": 2, "reason": "範囲の書式を読めない"}])


def test_border_unchecked_in_report_and_line(sb, monkeypatch, tmp_path):
    """比べなかった行は、結果の JSON の border_pixels_skipped と、1 行の border_unchecked=<行>:<理由> に出る。"""
    exp = _skip_case(sb)
    skipped = CF.border_rows(sb, exp)[1]
    rep = {"summary": {"pass": 1, "fail": 0, "unknown": 0}, "video": None, "checks": [],
           "sheet": {"rows": [], "frames": []}, "border_pixels_skipped": skipped}
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))
    line = CF.write_report(C.Config(), rep, "skip")
    assert line.startswith("failed=0 unknown=0 border_unchecked=1:頭から寄っている report=")
    with open(os.path.join(tmp_path, "check-final", "skip.json"), encoding="utf-8") as f:
        assert json.load(f)["border_pixels_skipped"] == [{"row": 1, "reason": "頭から寄っている"}]
    rep["border_pixels_skipped"] = []
    assert CF.write_report(C.Config(), rep, "skip").startswith("failed=0 unknown=0 report=")
    # --pixel でなければ項目そのものが無い
    assert "border_pixels_skipped" not in CF.run(sb, None, None)


def test_border_points_skip_text_and_badge(sb):
    """帯の文字の範囲と肩テロップの板に重なる点は比べない。"""
    sb["rows"][0]["focus"] = {"x": 0, "y": 120, "w": 3840, "h": 1820, "seconds": 0.5, "border": True}
    exp = CF.expected_rows(sb, {CF._key(sb["source"]): (3840, 2160)})
    keep, over, gone = CF.border_points(sb, exp[0])
    assert "下 1/2" in gone   # 画面の中央の下 (書き出しの y 970) は帯の文字
    assert "上 3/4" in gone   # 右上は肩テロップの板
    assert set(gone) == {"下 1/4", "下 1/2", "下 3/4", "上 3/4"} and not any(over.values())
    cut, _ = CF._overlays(sb, exp[0])
    assert len(cut) == 2   # 帯の文字と肩テロップの板


class Wrapper:
    """Resolve の API のように、GetToolList を呼ぶたびに同じ部品を別の Python のオブジェクトで返す (id() で部品を見分けられない)。"""

    def __init__(self, tool):
        self._tool = tool

    def __getattr__(self, name):
        return getattr(self._tool, name)


def fresh(comp):
    """合成の GetToolList が、呼ぶたびに部品を別のオブジェクト (Wrapper) で返すようにする。"""
    comp.GetToolList = lambda selected: {i: Wrapper(t) for i, t in enumerate(comp.tools, 1)}


def _blink(t0, t1, box):
    """t0 秒まで隠れていて (左上の 2 画素)、t1 秒の 1 フレームだけ box に出て、その次のフレームでまた隠れる。"""
    hid = {"x": 0, "y": 0, "w": 2, "h": 2}
    return {"type": "mosaic", "shape": "rect", **hid,
            "keys": [{"t": 0, **hid}, {"t": t0, **hid}, {"t": t1, **box}, {"t": t1 + 1 / 30, **hid}]}


@pytest.mark.parametrize("wrap", [False, True])
def test_several_keyed_masks_hidden_at_start(sb, wrap):
    """出たり消えたりするマスク (行の頭では左上の 2 画素に隠れている) が 1 行に 3 つ。行の頭の位置はどれも同じなので、
    頭の位置だけで部品を選ぶと隣のマスクの部品を読む。部品が構成表と別の順に並んでいても、
    Resolve のように呼ぶたびに別のオブジェクトで返っても、構成表の各マスクに合う部品を 1 対 1 で選ぶ。"""
    boxes = [{"x": 900, "y": 600, "w": 400, "h": 70}, {"x": 900, "y": 700, "w": 200, "h": 70},
             {"x": 90, "y": 185, "w": 260, "h": 335}]
    sb["rows"][2]["masks"].extend([_blink(6 / 30, 7 / 30, boxes[0]), _blink(6 / 30, 7 / 30, boxes[1]),
                                   _blink(5 / 30, 6 / 30, boxes[2])])
    tl = build(sb)
    tl.items[2].comp.tools.reverse()   # 組む側と逆の順に並べる
    if wrap:
        fresh(tl.items[2].comp)
    rep = CF.run(sb, None, tl)
    for i in range(1, 5):
        assert status(rep, f"mask{i}", 3) == ["pass"], (i, [c for c in rep["checks"] if c["item"] == f"mask{i}"])
        assert status(rep, f"mask{i}_type", 3) == ["pass"]


@pytest.mark.parametrize("wrap", [False, True])
def test_same_node_is_not_used_twice_with_fresh_wrappers(sb, wrap):
    """構成表に同じマスクが 2 つあって、部品が 1 つしかなければ、2 つ目は fail (同じ部品を 2 回使わない)。
    Resolve のように呼ぶたびに別のオブジェクトで返っても同じ。"""
    tl = build(sb)
    sb["rows"][2]["masks"].append(dict(sb["rows"][2]["masks"][0]))
    if wrap:
        fresh(tl.items[2].comp)
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask2", 3) == ["fail"]


def test_reaches_with_fresh_wrappers_and_reused_ids(sb, monkeypatch):
    """Resolve の API は同じ部品を呼ぶたびに別のオブジェクトで返し、捨てたオブジェクトの id() は使い回されうる。
    id() がどれも同じ値になっても、たどっていない部品を飛ばさずに MediaOut (名前を渡せばその部品) まで届く
    (部品の名前で覚える)。途中の部品の名前を読めなければ None。"""
    comp = Comp()
    blur, mid, last = (comp.AddTool(tid) for tid in ("Blur", "Merge", "Merge"))
    mid.Background, last.Background, comp["MediaOut1"].Input = blur, mid, last
    real = Tool.GetConnectedInputs   # つながった先の部品も、呼ぶたびに別のオブジェクトで返す
    monkeypatch.setattr(Tool, "GetConnectedInputs", lambda t: {k: Inp(Wrapper(i.tool), i.ID) for k, i in real(t).items()})
    monkeypatch.setattr(CF, "id", lambda o: 1, raising=False)   # 捨てたオブジェクトの id() が使い回される
    assert CF.reaches(Wrapper(blur)) is True
    assert CF.reaches(Wrapper(blur), last.Name) is True   # 名前を渡せば、その部品に届くか
    del comp["MediaOut1"].Input
    assert CF.reaches(Wrapper(blur)) is False
    assert CF.reaches(Wrapper(blur), "無い部品") is False
    mid.Name = None
    assert CF.reaches(Wrapper(blur)) is None and CF.reaches(Wrapper(blur), last.Name) is None
    # check_final のマスクの照合でも同じ (隠す部品の先にズームとテロップの Merge をはさむ)
    tl = build(sb)
    fresh(tl.items[2].comp)
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask1_type", 3) == ["pass"]


def _masks(sb, *boxes, types=("mosaic", "mosaic")):
    """3 行目のマスクを、(x, y, w, h) の四角と種類 types で書き換えた写し。"""
    return made(sb, 2, masks=[{"type": t, "x": x, "y": y, "w": w, "h": h} for t, (x, y, w, h) in zip(types, boxes)])


@pytest.mark.parametrize("wrap", [False, True])
def test_close_masks_are_assigned_together(sb, monkeypatch, wrap):
    """近い位置のマスクが 2 つ。前から順に選ぶと、mask1 (100, 100) が差の小さい mask2 の部品 (100.3, 100) を取り、mask2 に
    残った部品は 0.7 画素ずれて fail になる。割り当てを行の中でまとめて選べば、どちらも 0.5 画素以内で pass。"""
    sb = _masks(sb, (100, 100, 100, 100), (100.3, 100, 100, 100))
    tl = build(_masks(sb, (99.6, 100, 100.4, 100), (100.3, 100, 100, 100)))
    if wrap:
        fresh(tl.items[2].comp)
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask2", 3) == ["pass"]
    # マスクが多すぎるときは構成表の順に選ぶ (そう記録に出る)
    monkeypatch.setattr(CF, "MAX_ASSIGN", 1)
    rep = CF.run(sb, None, tl)
    assert status(rep, "mask1", 3) == ["pass"] and status(rep, "mask2", 3) == ["fail"]
    assert all("構成表の順に選んだ" in c["note"] for c in rep["checks"] if c["item"] in ("mask1", "mask2"))


@pytest.mark.parametrize("wrap", [False, True])
def test_assignment_counts_effect_type(sb, wrap):
    """同じ所に種類の違うマスク (ぼかし x=100、モザイク x=100.2)。正しい種類の部品は x=100.1 (ぼかし) と x=99.8 (モザイク)。
    位置の差だけで選ぶと入れ替わり、種類の照合が 2 つとも fail になる。位置と種類の両方で数えれば全部 pass。"""
    kinds = ("blur", "mosaic")
    sb = _masks(sb, (100, 100, 100, 100), (100.2, 100, 100, 100), types=kinds)
    tl = build(_masks(sb, (100.1, 100, 100, 100), (99.8, 100, 100, 100), types=kinds))
    if wrap:
        fresh(tl.items[2].comp)
    rep = CF.run(sb, None, tl)
    for item in ("mask1", "mask1_type", "mask2", "mask2_type"):
        assert status(rep, item, 3) == ["pass"], item


def test_fewer_tools_leave_later_masks_without_tool(sb):
    """部品がマスクより少ないときは、構成表の前のマスクから部品を取り、足りない分は後ろのマスクが「無い」(fail)。
    部品が後ろのマスクの位置に合っていても、前のマスクを「無い」にして後ろに回さない。"""
    sb["rows"][2].pop("caption")   # テロップの帯の四角 (CaptionBarMask) も四角のマスクの部品に数えるので外す
    sb = _masks(sb, (100, 100, 100, 100), (500, 500, 100, 100))
    rep = CF.run(sb, None, build(_masks(sb, (500, 500, 100, 100))))   # 四角のマスクは 1 つだけ
    m1, m2 = ([c for c in rep["checks"] if c["item"] == i and c["row"] == 3][0] for i in ("mask1", "mask2"))
    assert m1["status"] == "fail" and m1["actual"] is not None   # 部品を読んで位置が違う
    assert m2["status"] == "fail" and "無い" in m2["note"]


# ---------- 枠線の色 auto と callout の order・自動の置き場所 ----------
@pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg が無い")
def test_focus_border_auto_color(sb, offline_work):
    """枠線の色 auto: 照らし合わせる側も素材の画素から色を決め直す。黒い素材は dark の色、白い素材は light の色の部品なら pass、
    逆の色なら fail。決めた色と測った明るさは期待する値 (expected.auto) に残る。素材を読めなければ unknown。"""
    work = os.path.join(offline_work, "auto_color")
    os.makedirs(work, exist_ok=True)
    auto = sb["focus_border"]["auto"]
    sb["rows"][0]["focus"] = {"x": 800, "y": 300, "w": 600, "h": 400, "dim": 0.5, "seconds": 0.5,
                              "border": {"color": "auto", "width": 6}}

    def source(color):
        path = os.path.join(work, color + ".mp4")
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c={color}:s=1920x1080:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-preset", "veryfast", path], check=True)
        return path

    def check(color):   # 組む側だけ枠線の色を color にする
        f = dict(sb["rows"][0]["focus"], border={"color": color, "width": 6})
        rep = CF.run(sb, None, build(made(sb, 0, focus=f)))
        return status(rep, "focus_border", 1), next(c for c in rep["checks"] if c["item"] == "focus_border")
    for color, want, other in (("black", auto["dark"], auto["light"]), ("white", auto["light"], auto["dark"])):
        sb["source"] = source(color)
        got, c = check(want)
        assert got == ["pass"], c
        assert c["expected"]["auto"]["color"] in (auto["dark"], auto["light"])
        assert c["expected"]["auto"]["luma"] == (0.0 if color == "black" else 255.0)
        assert check(other)[0] == ["fail"]
    sb["source"] = os.path.join(work, "no-such.mp4")
    got, c = check(auto["dark"])
    assert got == ["unknown"] and "決められない" in c["note"]


def test_callout_order_and_auto_dst(sb):
    """callout の order と dst の省略: 照らし合わせる側も組む側と同じ callout_values で、上から 2 番目の src → 1 番目の src の順と、
    テロップの帯の上の自動の置き場所を期待する。書いた順に組んだもの・別の置き場所に組んだものは fail。"""
    srcs = [{"x": 2600, "y": 380, "w": 360, "h": 280}, {"x": 2600, "y": 700, "w": 180, "h": 100}]
    co = {"src": srcs, "order": [2, 1], "start": 1, "in": 0.4}
    sb["rows"][0]["callout"] = co
    tl = build(sb, K4)

    def built(spec, v=None):
        """組む側 (common.scene_values。v を渡せばその callout の値) で 1 行目の合成を組み直す。"""
        vals = C.scene_values(sb, dict(sb["rows"][0], callout=spec), C.caption_font(sb), source_size=K4)
        vals["callout"] = v or vals["callout"]
        it = Item()
        C.build_fusion(it, vals)
        tl.items[0].comp = it.comp
        return vals["callout"]
    v = built(co)
    assert v["pieces"][0]["crop"][2] == 180 and v["pieces"][1]["crop"][2] == 360   # 上は 2 番目の src
    # 手で計算した値: テロップの帯の上の端 916 から margin 24・縁 3・影 10 を空けた 879 が下の端。倍率は max_scale 4
    # (素材の 1 画素が書き出しの 2 画素) で、幅 360 → 720、高さ (100 + 280) × 2 + 間 4 = 764。横は中央 (1920 - 720) / 2
    assert C.caption_band(sb, sb["rows"][0], C.caption_font(sb)) == (pytest.approx(916), pytest.approx(1026))
    assert v["dst"] == [600, 879 - 764, 720, pytest.approx(764)] and v["size"] == pytest.approx(4.0)
    rep = CF.run(sb, None, tl)
    assert [status(rep, k, 1) for k in ("callout", "callout_time", "callout_2", "callout_time_2")] == [["pass"]] * 4
    built({k: x for k, x in co.items() if k != "order"})            # order を見落として書いた順に組んだ
    rep = CF.run(sb, None, tl)
    assert status(rep, "callout", 1) == ["fail"] and status(rep, "callout_2", 1) == ["fail"]
    built(dict(co, dst={"x": 300, "y": 200, "w": 540}))              # 自動の置き場所でない所に組んだ
    assert status(CF.run(sb, None, tl), "callout", 1) == ["fail"]
    built(co, C.callout_values(sb, co, 3840, 2160, 30))               # テロップの帯を渡し忘れた (画面の下に置いた)
    assert status(CF.run(sb, None, tl), "callout", 1) == ["fail"]
    sb["rows"][0]["callout"] = dict(co, order=[1, 1])
    assert status(CF.run(sb, None, tl), "callout", 1) == ["unknown"]


def test_empty_badge_is_prefix_only_badge(sb):
    """badge が空の文の行は、組む側 (scene_values) と同じく接頭辞だけの肩テロップとして照らし合わせる (無いとみなさない)。"""
    sb["rows"][0]["badge"] = ""
    exp = CF.expected_rows(sb)[0]
    assert exp["badge_text"] == sb["badge"]["prefix"] and exp["badge"] == round(sb["badge"]["seconds"] * 30)
    assert C.scene_values(sb, sb["rows"][0], source_size=(1920, 1080))["badge"]["text"] == exp["badge_text"]
    rep = CF.run(sb, None, build(sb))
    assert status(rep, "badge", 1) == ["pass"] and status(rep, "badge_box", 1) in (["pass"], ["unknown"])


def test_unknown_keys_are_warned_not_judged(sb, tmp_path):
    """構成表の知らない項目は、結果の warnings と 1 行の warnings=N に出す。照らし合わせの判定には入れない。"""
    sb["rows"][0]["callout_"] = 1
    rep = CF.run(sb, None, build(sb))
    assert any("知らない項目 1 行目の callout_" in w for w in rep["warnings"])
    assert rep["summary"]["fail"] == 0
    cfg = type("Cfg", (), {"out_dir": lambda self, *a: str(tmp_path)})()
    assert " warnings=1 " in CF.write_report(cfg, rep, "w")


def test_each_frame_with_no_frames_does_not_build_the_index(tmp_path):
    """読むフレームが無ければ、パケットの表 (index) を作らず、色の読み方も集めない。"""
    video = str(tmp_path / "none.mp4")
    colors = {}
    assert list(CF.each_frame(video, [], 1920, 1080, colors=colors)) == []
    assert colors == {} and C.frame_reader(video).index_table is None


# ---------- 途中で止まったときに前回の結果を残す ----------
def _report(video, rows=(1,)):
    return {"summary": {"pass": 0, "fail": 1, "unknown": 0}, "video": video, "checks": [],
            "sheet": {"rows": list(rows), "frames": [{"frame": 0, "reason": "1 行目 行の始まり"}] if rows else []}}


def _files(out):
    assert not [f for f in os.listdir(out) if f.startswith((".run-", ".old-"))]   # 一時と退避のフォルダーは残らない
    return {f: open(os.path.join(out, f), "rb").read() for f in sorted(os.listdir(out))}


@pytest.mark.parametrize("how", ["interrupt", "json"])
def test_stopped_report_keeps_previous_result(tmp_path, monkeypatch, how):
    """画像を作る途中の中断や JSON を書けないときは、前回の JSON と画像が残る。fail の報告は最後まで済めば移す。"""
    import contact_sheet
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))

    def fake(v, frames, png, *a, **k):
        open(png, "wb").write(b"png-" + str(len(frames)).encode())
        return png
    monkeypatch.setattr(contact_sheet, "frames_sheet", fake)
    line = CF.write_report(C.Config(), _report(str(video)), "r")
    out = os.path.join(tmp_path, "check-final")
    assert line.endswith(f"report={os.path.join(out, 'r.json')} sheet={os.path.join(out, 'r.png')}")
    before = _files(out)
    assert json.loads(before["r.json"])["sheet"]["image"] == os.path.join(out, "r.png")
    if how == "interrupt":
        def stop(v, frames, png, *a, **k):
            open(png, "wb").write(b"half")
            raise KeyboardInterrupt
        monkeypatch.setattr(contact_sheet, "frames_sheet", stop)
        err = KeyboardInterrupt
    else:
        def broken(path, obj):
            raise OSError("書けない")
        patch_common(monkeypatch, "write_json", broken)
        err = OSError
    with pytest.raises(err):
        CF.write_report(C.Config(), _report(str(video)), "r")
    assert _files(out) == before


def test_report_without_image_removes_previous_image(tmp_path, monkeypatch):
    """今回画像を作らない (作れない) ときは、前回の画像を消し、理由を JSON に残す。書きかけの画像は移さない。"""
    import contact_sheet
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"p") and png)
    CF.write_report(C.Config(), _report(str(video)), "r")
    out = os.path.join(tmp_path, "check-final")

    def fails(v, frames, png, *a, **k):
        open(png, "wb").write(b"half")
        raise RuntimeError("読めない")
    monkeypatch.setattr(contact_sheet, "frames_sheet", fails)
    CF.write_report(C.Config(), _report(str(video)), "r")
    files = _files(out)
    assert list(files) == ["r.json"] and json.loads(files["r.json"])["sheet"]["error"] == "読めない"
    CF.write_report(C.Config(), _report(str(video), rows=()), "r")   # 区切りのフレームが無い報告
    assert list(_files(out)) == ["r.json"]


def test_unplaced_report_keeps_previous_result(tmp_path, monkeypatch):
    """画像を置いたあとに JSON を置き換えられないときは、前回の JSON と画像に戻す。"""
    import contact_sheet
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"old") and png)
    CF.write_report(C.Config(), _report(str(video)), "r")
    out = os.path.join(tmp_path, "check-final")
    before = _files(out)
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"new") and png)
    real = os.replace

    def replace(src, dst):
        if ".run-" in src and dst.endswith("r.json"):
            raise PermissionError(dst)
        real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PermissionError):
        CF.write_report(C.Config(), _report(str(video)), "r")
    assert _files(out) == before


@pytest.mark.parametrize("phase", [".old-", "check-final" + os.sep + "r."])
def test_interrupt_right_after_a_move_restores_previous_result(tmp_path, monkeypatch, phase):
    """退避・置き換えの os.replace が済んだ直後に Ctrl+C が入っても、前回の JSON と画像に戻り、退避のフォルダーも残らない。"""
    import contact_sheet
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"old") and png)
    CF.write_report(C.Config(), _report(str(video)), "r")
    out = os.path.join(tmp_path, "check-final")
    before = _files(out)
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"new") and png)
    real, hit = os.replace, []

    def replace(src, dst):
        real(src, dst)
        if phase in dst and not hit and ".old-" not in src:   # 最初の 1 回だけ、移した後に止める
            hit.append(dst)
            raise KeyboardInterrupt
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        CF.write_report(C.Config(), _report(str(video)), "r")
    assert hit and _files(out) == before


def test_temp_folder_left_after_publish_is_not_a_failure(tmp_path, monkeypatch):
    """全部置き換えた後に一時のフォルダーを消せなくても、失敗にしない (今回の結果が置かれている)。"""
    import contact_sheet
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path))
    monkeypatch.setattr(contact_sheet, "frames_sheet", lambda v, f, png, *a, **k: open(png, "wb").write(b"new") and png)
    real = os.rmdir

    def rmdir(path, *a, **k):
        if ".run-" in str(path):
            raise PermissionError(path)
        real(path, *a, **k)
    monkeypatch.setattr(os, "rmdir", rmdir)
    line = CF.write_report(C.Config(), _report(str(video)), "r")
    out = os.path.join(tmp_path, "check-final")
    assert line.startswith("failed=1 ") and open(os.path.join(out, "r.png"), "rb").read() == b"new"
    assert json.load(open(os.path.join(out, "r.json"), encoding="utf-8"))["sheet"]["image"] == os.path.join(out, "r.png")
    assert not [f for f in os.listdir(out) if f.startswith(".old-")]


def test_point_with_hold_keeps_row_position(sb):
    """指すマークと止める (hold) が同じ行にあっても、行の位置を指す点で上書きしない (止めたクリップの位置が数のまま)。"""
    sb["rows"][0]["point"] = {"x": 900, "y": 500}
    sb["rows"][0]["hold"] = {"seconds": 1}
    exp = CF.expected_rows(sb)
    assert isinstance(exp[0]["hold"]["start"], int) and exp[0]["point"]["target"] == (900, 500)


@pytest.mark.parametrize("name,inputs,expected", [
    pytest.param("PointOver", {"Blend": 1.0}, "pass", id="blend-visible"),
    pytest.param("PointOver", {"Blend": 0.0}, "fail", id="blend-zero"),
    pytest.param("PointOver", {"Blend": lambda t: 0.0 if t >= 100 else 1.0}, "fail", id="blend-disappears-mid-row"),
    pytest.param("PointOver", {"Blend": lambda t: None}, "unknown", id="blend-unreadable"),
    pytest.param("PointText", {}, "pass", id="font-matches"),
    pytest.param("PointText", {"Font": "Arial"}, "fail", id="font-mismatch"),
    pytest.param("PointText", {"Style": "Bold"}, "fail", id="style-mismatch"),
    pytest.param("PointText", {"Font": None}, "unknown", id="font-unreadable"),
])
def test_point_tool_values(sb, name, inputs, expected):
    """指すマークは Blend が 0 なら見えず、指の先のずれは書体ごとの値なので Font・Style が違っても fail。読めなければ unknown。"""
    sb["rows"][0]["point"] = {"x": 900, "y": 500}
    exp = CF.expected_rows(sb)
    assert (exp[0]["point"]["font"], exp[0]["point"]["style"]) == (sb["point"]["fonts"][sb["point"]["style"]], "Regular")
    comp = row_comp(sb, sb["rows"][0])
    comp[name].put(**inputs)
    checks = []
    CF.check_point(checks, sb, exp[0], comp)
    assert [c["status"] for c in checks if c["item"] == "point"] == [expected]


@pytest.mark.parametrize("point", [
    {"y": 500},                                  # x が無い
    {"x": 900, "y": 500, "from": "up"},          # from の誤り
    {"x": 900, "y": 500, "style": "emoj"},       # style の誤り
    "900,500",                                   # 形の誤り
])
def test_unreadable_point_is_unknown(sb, point):
    """point の書式が読めないとき、照らし合わせの全体を例外で止めずに、指すマークを unknown にする。"""
    tl = build(sb)
    sb["rows"][1]["point"] = point
    exp = CF.expected_rows(sb)
    assert exp[1]["point"]["unreadable"].startswith("point の書式を読めない")
    rep = CF.run(sb, None, tl)
    assert status(rep, "point", 2) == ["unknown"]
    cut, _ = CF._overlays(sb, exp[1])   # 画素の照らし合わせで外す四角も作れる
    assert cut


@pytest.mark.parametrize("side", [5, [{"body": "x"}], "説明"])
def test_unreadable_side_is_unknown(sb, side):
    """side の書式が読めない (数・形の誤り) か、layout が無いのに side があるとき、例外で止めずに unknown にする。"""
    assert not sb.get("layout")
    tl = build(sb)
    sb["rows"][1]["side"] = side
    rep = CF.run(sb, None, tl)
    assert status(rep, "side", 2) == ["unknown"]


def test_side_blend_zero_fails():
    """解説の欄の *OverN がつながっていても、Blend が 0 なら欄は見えないので fail。読めなければ unknown。"""
    import copy
    sb = copy.deepcopy(C._read_json(C.DEFAULTS)["storyboard"])
    sb.update({"layout": {"screen": {"x": 600, "y": 40, "w": 2240, "h": 1260}, "anchor": "top-right",
                          "side": [{"title": "項目", "text": "設定の値"}, {"text": "一行目|二行目"}]},
               "rows": [{"start": 0, "end": 6}]})
    sb = C.apply_layout(sb)
    row = {"row": 1, "side": sb["layout"]["side"], "frames": 180, "speed": 100.0, "layout": CF.layout_expect(sb, 1920, 1080)}

    def run(blend={}):
        comp = row_comp(sb, sb["rows"][0])
        for name, b in blend.items():
            comp[name].put(Blend=b)
        checks = []
        CF.check_side(checks, sb, row, comp)
        return [c["status"] for c in checks if c["item"] == "side"]
    assert run() == ["pass"]
    assert run({"SideTextOver2": 0.0}) == ["fail"]
    assert run({"SideBorderOver1": lambda t: 0.0 if t >= 90 else 1.0}) == ["fail"]   # 行の途中で消える
    assert run({"SideTitleOver1": lambda t: None}) == ["unknown"]


def test_side_size_shrunk_section_passes_and_rewritten_size_fails(monkeypatch):
    """欄ごとの中身の字の大きさ (side_size)。幅に収まらない欄だけ縮めた組み方は pass、SideTextN の Size を書き換えると fail。
    書体のファイルが無くて測れなければ unknown。文の幅は 1 字 = 字の大きさ (px) の模擬で測る。"""
    import copy
    sb = copy.deepcopy(C._read_json(C.DEFAULTS)["storyboard"])
    sb.update({"layout": {"screen": {"x": 600, "y": 40, "w": 2240, "h": 1260}, "anchor": "top-right",
                          "side": [{"title": "対象", "text": "検証用|テンプレート"},
                                   {"title": "名前", "text": "検証用テンプレート"}, {"text": "一言"}]},
               "rows": [{"start": 0, "end": 6}]})
    sb = C.apply_layout(sb)
    patch_common(monkeypatch, "text_width", lambda ff, size, text: size * len(text) if ff else None)
    monkeypatch.setattr(C.config(), "font_file", lambda *a: "fake.ttf")
    row = {"row": 1, "side": sb["layout"]["side"], "frames": 180, "speed": 100.0, "layout": CF.layout_expect(sb, 1920, 1080)}

    def run(comp):
        checks = []
        CF.check_side(checks, sb, row, comp)
        return {c["item"]: c for c in checks}
    comp = row_comp(sb, sb["rows"][0], font_file="fake.ttf")
    got = run(comp)
    assert got["side"]["status"] == "pass" and got["side_size"]["status"] == "pass"
    fts = 1920 * sb["fusion_text_scale"]
    assert [round(s * fts, 3) for s in got["side_size"]["expected"]] == [44.0, 36.0, 44.0]   # 2 番目の欄だけ縮む (width_scale 1.08 込み)
    comp["SideText2"].put(Size=44 / fts)
    assert run(comp)["side_size"]["status"] == "fail"
    monkeypatch.setattr(C.config(), "font_file", lambda *a: None)
    assert run(row_comp(sb, sb["rows"][0], font_file="fake.ttf"))["side_size"]["status"] == "unknown"


SIDE_BASE = [{"title": "対象", "text": "設定画面 A"}, {"title": "コメント", "text": "一言"},
             {"title": "備考", "text": "・手順は 3 つ|・待ち長め", "align": "left"}]


def side_sb(sb, base=SIDE_BASE, anchor="top-right"):
    """テストの構成表に、全体の解説の欄 (layout.side) のある layout を追加する。"""
    del sb["rows"][2]["zoom"]
    sb["layout"] = {"screen": {"x": 600, "y": 40, "w": 2240, "h": 1260}, "anchor": anchor, "side": base}
    return C.apply_layout(sb)


def _move_top(px):
    """いちばん上の欄の縁 (SideBorder1Mask) の上の端だけを px 下へずらす (下の端はそのまま。欄が縦に縮む)。"""
    def change(named):
        m = named["SideBorder1Mask"]
        h, c = m.inputs["Height"], m.inputs["Center"]
        m.inputs.update(Height=h - px / 1080, Center={1: c[1], 2: c[2] - px / 1080 / 2})
    return change


@pytest.mark.parametrize("prefix", ["", "hold_"])
@pytest.mark.parametrize("anchor", ["top-left", "top", "top-right"])
@pytest.mark.parametrize("change,expected", [
    pytest.param(None, "pass", id="as-built"),
    pytest.param(_move_top(3), "fail", id="top-down-3px"),
    pytest.param(_move_top(-3), "fail", id="top-up-3px"),
])
def test_side_top_edge(sb, prefix, anchor, change, expected):
    """いちばん上の欄の上の端は、余白の上の端 (top-right は margin、top-left と top は肩テロップの下) に 1 px まで合う。
    上の端だけをずらす (欄は重ならずに並び、いちばん下の欄の下の端は画面の下の端のまま) と fail。止めたクリップ (hold_) も同じ。
    照合の期待の値 (layout_expect の side_area) は、組む側の余白 (apply_layout の side.area) と同じ所を指す。"""
    sb = side_sb(sb, anchor=anchor)
    row = layout_exp(sb)[0]
    assert row["layout"]["side_area"] == pytest.approx(sb["side"]["area"])
    comp = row_comp(sb, sb["rows"][0], WIDE)
    if change:
        change(comp)
    checks = []
    CF.check_side(checks, sb, row, comp, prefix)
    assert [c["status"] for c in checks if c["item"] == prefix + "side"] == [expected]


def test_row_side_dict_replaces_only_that_section(sb):
    """行の side の辞書 ({見出し: 中身}) は、全体の欄のうちその見出しの欄の中身だけを差し替える。ほかの欄・並び・title は
    そのまま。align は全体の欄に書いてあれば残し、無ければ差し替えた中身の行数で決まる。文と list は今までどおり全部を差し替え、
    行に side が無ければ全体の欄。"""
    sb = side_sb(sb)
    sb["rows"][0]["side"] = {"コメント": "注意|確認を待つ", "備考": "一行だけ"}
    sb["rows"][1]["side"] = "その行だけ"
    exp = layout_exp(sb)
    merged = [{"title": "対象", "text": "設定画面 A"}, {"title": "コメント", "text": "注意|確認を待つ"},
              {"title": "備考", "text": "一行だけ", "align": "left"}]
    assert exp[0]["side"] == merged
    assert [s["align"] for s in C.side_sections(exp[0]["side"])] == ["center", "left", "left"]   # 2 行は左寄せ、備考は書いた left
    assert exp[1]["side"] == "その行だけ" and exp[2]["side"] == SIDE_BASE
    assert sb["layout"]["side"] == SIDE_BASE   # 全体の欄は書き換えない

    def run(side, row=exp[0], prefix=""):
        checks = []
        CF.check_side(checks, sb, row, row_comp(sb, {"side": side}, WIDE), prefix)
        return [c["status"] for c in checks if c["item"] == prefix + "side"]
    assert run(merged) == ["pass"]
    assert run(merged, prefix="hold_") == ["pass"]   # 止めたクリップも同じ欄
    assert run(SIDE_BASE) == ["fail"]                # 差し替えていない
    assert run(merged[1:]) == ["fail"]               # 差し替えていない欄を落とした
    assert run(SIDE_BASE, row=exp[2]) == ["pass"]


@pytest.mark.parametrize("base,side", [
    (SIDE_BASE, {"無い見出し": "x"}),            # 全体の欄の見出しに無い
    (SIDE_BASE, {}),                             # 空の辞書
    (SIDE_BASE, {"コメント": 5}),                # 中身が文でない
    (SIDE_BASE, {"コメント": ["a", "b"]}),
    ("全体は文", {"コメント": "x"}),             # 全体に list の layout.side が無い
])
def test_row_side_dict_unreadable_is_unknown(sb, base, side):
    """行の side の辞書の書式の誤りは、例外で止めずに unknown (今の読めない書式と同じ)。止めたクリップも同じ。"""
    sb = side_sb(sb, base)
    sb["rows"][1]["side"] = side   # 読み込み (apply_layout) の確認を通らない形を、照らし合わせに直に渡す
    exp = layout_exp(sb)
    assert exp[1]["side"]["unreadable"]
    for prefix in ("", "hold_"):
        checks = []
        CF.check_side(checks, sb, exp[1], Comp(), prefix)
        assert [c["status"] for c in checks] == ["unknown"], prefix
    sb["layout"].pop("side")   # 全体に layout.side が無い
    assert layout_exp(sb)[1]["side"]["unreadable"]


@pytest.fixture
def window_sb(sb):
    sb["caption"] |= {"bar_inset": 40, "border_width": 6, "end_mark": "▼"}
    return sb


def _caption_bar(sb, change=lambda c: None):
    """1 行目の合成を change(合成) で崩して、テロップの帯の項目 (caption_bar) を照らし合わせる。"""
    comp = row_comp(sb, sb["rows"][0])
    change(comp)
    checks = []
    CF.check_caption(checks, sb, CF.expected_rows(sb)[0], comp)
    return [c["status"] for c in checks if c["item"] == "caption_bar"]


def test_caption_border_and_mark_blend_zero_fails(window_sb):
    """テロップの縁 (CaptionBorderOver) と ▼ (CaptionMarkOver) がつながっていても、Blend が 0 なら fail。読めなければ unknown。"""
    sb = window_sb
    def blend(name, b):
        return _caption_bar(sb, lambda c: c[name].put(Blend=b))
    assert _caption_bar(sb) == ["pass"]
    assert blend("CaptionBorderOver", 0.0) == ["fail"]
    assert blend("CaptionMarkOver", 0.0) == ["fail"]
    assert blend("CaptionMarkOver", lambda t: 0.0 if t >= 100 else 1.0) == ["fail"]
    assert blend("CaptionBorderOver", lambda t: None) == ["unknown"]


def test_caption_border_hole_values(window_sb):
    """縁の内側の抜き: CaptionBorderHole の幅と高さが板 (CaptionBar) と合い、Invert が 1、CaptionBorderMask の PaintMode が
    Multiply でなければ fail。抜きの部品が無ければ fail、値を読めなければ unknown。"""
    sb = window_sb

    def run(change):
        return _caption_bar(sb, change)
    assert run(lambda n: None) == ["pass"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(Width=n["CaptionBorderMask"].inputs["Width"])) == ["fail"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(Height=0.01)) == ["fail"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(Invert=0)) == ["fail"]
    assert run(lambda n: n["CaptionBorderMask"].inputs.update(PaintMode="Merge")) == ["fail"]
    assert run(lambda n: n.tools.remove(n["CaptionBorderHole"])) == ["fail"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(Invert=None)) == ["unknown"]


def test_caption_border_and_mark_place(window_sb):
    """縁 (CaptionBorderMask) と穴 (CaptionBorderHole) の Center と CornerRadius、行の終わりのマーク (CaptionMark) の Center と Size が
    期待の値と違えば fail。マークの Size か Center を読めなければ unknown。"""
    sb = window_sb

    def run(change):
        return _caption_bar(sb, change)

    def shift(name, dx=0.0, dy=0.0):
        def f(n):
            c = n[name].inputs["Center"]
            n[name].inputs["Center"] = {1: c[1] + dx, 2: c[2] + dy}
        return f
    assert run(lambda n: None) == ["pass"]
    assert run(shift("CaptionBorderMask", dy=0.05)) == ["fail"]
    assert run(shift("CaptionBorderHole", dx=0.05)) == ["fail"]
    assert run(lambda n: n["CaptionBorderMask"].inputs.update(CornerRadius=0.2)) == ["fail"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(CornerRadius=0.2)) == ["fail"]
    assert run(lambda n: n["CaptionMark"].inputs.update(Size=0.0)) == ["fail"]
    assert run(shift("CaptionMark", dx=-0.02)) == ["fail"]
    assert run(shift("CaptionMark", dy=0.02)) == ["fail"]
    assert run(lambda n: n["CaptionMark"].inputs.update(Size=None)) == ["unknown"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.update(Center=None)) == ["unknown"]
    # 角を丸めた見た目: 縁と穴の CornerRadius も caption.corner_radius
    sb["caption"] |= {"corner_radius": 0.1}
    assert run(lambda n: None) == ["pass"]
    assert run(lambda n: n["CaptionBorderHole"].inputs.pop("CornerRadius")) == ["fail"]


def test_point_size_zero_at_target_fails(sb):
    """PointText の Size を 0 にして Center を指す点に置くと、指の先の計算は指す点に合うがマークは見えない。Size も比べて fail にする。"""
    sb["rows"][0]["point"] = {"x": 900, "y": 500}
    exp = CF.expected_rows(sb)

    def run(change=None):
        comp = row_comp(sb, sb["rows"][0])
        if change:
            change(comp["PointText"].inputs)
        checks = []
        CF.check_point(checks, sb, exp[0], comp)
        return [c["status"] for c in checks if c["item"] == "point"]
    assert run() == ["pass"]
    assert run(lambda i: i.update(Size=0.0, Center={1: 900 / 1920, 2: 1 - 500 / 1080})) == ["fail"]
    assert run(lambda i: i.update(Size=i["Size"] * 1.5)) == ["fail"]


def test_point_size_follows_zoom_end(sb):
    """ズームのある行の指すマークは、寄せ終わった後に point.size px に見える大きさ (Size を終わりの倍率で割る)。
    引く向き (direction out) なら倍率は 1。"""
    sb["rows"][2]["point"] = {"x": 1500, "y": 250}
    W = sb["timeline"]["width"]
    for direction, end in (("in", min(1920 / 640, 1080 / 360)), ("out", 1.0)):
        sb["rows"][2]["zoom"]["direction"] = direction
        exp = CF.expected_rows(sb)
        want = sb["point"]["size"] / end / (W * sb["fusion_text_scale"])
        assert exp[2]["point"]["fusion_size"] == pytest.approx(want)
        v = C.scene_values(sb, sb["rows"][2], C.caption_font(sb))["point"]   # 組む側と同じ値になる
        assert v["size"] == pytest.approx(want)


def test_unreadable_zoom_with_point_is_unknown(sb):
    """point のある行のズームの書式が読めない (h が無い) とき、落ちずにズームと指すマークを unknown にする。"""
    tl = build(sb)
    sb["rows"][2]["point"] = {"x": 900, "y": 500}
    sb["rows"][2]["zoom"] = {"x": 0, "y": 0, "w": 100}
    exp = CF.expected_rows(sb)
    assert exp[2]["zoom"]["unreadable"] and exp[2]["point"]["unreadable"] and exp[2]["point"]["target"] == (900, 500)
    rep = CF.run(sb, None, tl)
    assert status(rep, "zoom", 3) == ["unknown"] and status(rep, "point", 3) == ["unknown"]
    CF._overlays(sb, exp[2])   # 画素の照らし合わせで外す四角も、指す点から作れる


def test_overlays_cut_caption_border_top_and_bottom(sb):
    """テロップの縁の上下の帯 (border_width px) の点は、素材の色と比べずに外す (左右の縁と同じ)。"""
    sb["caption"] |= {"border_width": 20}
    sb["rows"][0]["caption"] = "a"
    exp = CF.expected_rows(sb)
    cut, tint = CF._overlays(sb, exp[0])
    fit = C.fit_caption(sb, "a", C.caption_font(sb))
    y0 = (1 - fit["bar_center"] - fit["bar_height"] / 2) * 1080
    y1 = (1 - fit["bar_center"] + fit["bar_height"] / 2) * 1080
    for py in (y0 + 10, y1 - 10):   # 上と下の縁の真ん中。文字の範囲と左右の縁からは外れた x
        p = [480, py, 480, py]
        assert any(CF._inside(p, c) for c in cut), py
        assert not any(CF._inside(p, box) for box, _, _ in tint)


# ---------- layout (画面を切り抜いて置き、余白をぼかした背景で埋める) ----------
WIDE = (3440, 1440)   # 横長の素材 (comp は 3440x1935 に広がる)


def layout_sb(sb, anchor="top-right"):
    """テストの構成表に layout を追加する (3 行目のズームは layout と一緒に使えないので外す)。"""
    del sb["rows"][2]["zoom"]
    sb["layout"] = {"screen": {"x": 600, "y": 40, "w": 2240, "h": 1260}, "anchor": anchor}
    return C.apply_layout(sb)


def layout_comp(sb, size=WIDE, focus=False, mask=True, change=None):
    """layout の 3 行目の合成 (組む側の build_fusion)。mask なら行のマスク (モザイク Mask1Mosaic)、focus なら周りを暗くする
    部品 (FocusDim) も、画面の切り抜きの前に入る。change(合成) で値やつながりを崩せる。"""
    row = dict(sb["rows"][2], **({"focus": {"x": 1600, "y": 600, "w": 1200, "h": 800}} if focus else {}))
    if not mask:
        row.pop("masks")
    comp = row_comp(sb, row, size)
    if change:
        change(comp)
    return comp


def layout_check(sb, exp_row, comp, prefix=""):
    checks = []
    CF.check_layout(checks, sb, exp_row, comp, prefix)
    return {c["item"]: c["status"] for c in checks}


def layout_exp(sb, size=WIDE):
    return CF.expected_rows(sb, {CF._key(sb["source"]): size})


def test_layout_expect_numbers(sb):
    """3440x1440 の素材を 1920x1080 に置く例 (画面は x 410.4〜1908・y 12〜854.4 に出る)。
    期待の値は構成表から別に計算し、組む側 (common.layout_values) と同じ所を指す。"""
    sb = layout_sb(sb)
    L = CF.layout_expect(sb, *WIDE)
    assert L["comp"] == [3440, 1935] and L["canvas"]
    assert L["crop"] == pytest.approx([600, 387.5, 2240, 1260])
    x0, y0, w, h = L["rect"]
    assert (x0, y0, x0 + w, y0 + h) == pytest.approx((410.4, 12, 1908, 854.4), abs=0.05)
    # 背景の既定は config の storyboard.layout_background (値そのものは config が正なので、ここに写さない)
    bg = sb["layout_background"]
    assert (L["blur"], L["dim"]) == (float(bg["blur"]), float(bg["dim"])) and L["filter"] == 8
    assert L["screen_size"] == pytest.approx(L["scale"] * 3440 / 1920)   # 画面を置く Merge の Size (k × cw ÷ W)
    v = C.layout_values(sb, *WIDE)   # 組む側と食い違わない (Crop は組む側が整数に丸めるので 0.5 まで)
    assert v["screen_crop"] == [600, 388, 2240, 1260]
    assert all(abs(a - b) <= 0.5 for a, b in zip(v["screen_crop"], L["crop"]))
    assert v["back_size"] == pytest.approx(L["back_size"]) and v["view"] == pytest.approx(L["view"], abs=0.5)
    assert v["size"] == pytest.approx(L["end"], rel=1e-3)   # マークの大きさを割る倍率は、画面を置く倍率と同じ
    assert v["size"] == pytest.approx(L["screen_size"], rel=1e-3)
    # 比が同じ素材では comp は素材のまま。左上に寄せれば x0 は margin
    sb["layout"]["anchor"] = "top-left"
    L = CF.layout_expect(sb, 1920, 1080)
    assert L["comp"] == [1920, 1080] and not L["canvas"] and L["rect"][0] == 12
    assert L["crop"] == pytest.approx([600, 1080 - 40 - 1260, 2240, 1260])


def test_layout_rows_have_no_zoom_and_black_masks(sb):
    """layout の行に、ズームと画面の外を黒く塗るマスクの期待の値を入れない (今の作りは置かない)。"""
    sb = layout_sb(sb)
    exp = layout_exp(sb)
    assert [r["zoom"] for r in exp] == [None, None, None]
    assert [len(r["masks"]) for r in exp] == [0, 0, 1]   # 3 行目に書いたモザイクだけ
    assert all(r["layout"] and not r["layout"].get("unreadable") for r in exp)


@pytest.mark.parametrize("focus", [False, True])
def test_layout_tools_pass(sb, focus):
    sb = layout_sb(sb)
    exp = layout_exp(sb)
    got = layout_check(sb, dict(exp[2], focus={"x": 1} if focus else None), layout_comp(sb, focus=focus))
    assert got == {"layout": "pass", "layout_back": "pass", "layout_dim": "pass", "layout_links": "pass"}
    got = layout_check(sb, exp[2], layout_comp(sb), prefix="hold_")
    assert got == {"hold_layout": "pass", "hold_layout_back": "pass", "hold_layout_dim": "pass", "hold_layout_links": "pass"}
    # 比が同じ素材 (comp は素材のまま。単色の大きさは見ない)
    exp = layout_exp(sb, (1920, 1080))
    got = layout_check(sb, exp[2], layout_comp(sb, (1920, 1080)))
    assert set(got.values()) == {"pass"}


def _set(name, **inputs):
    return lambda n: n[name].put(**inputs)


def _scale(name, key, f=None, add=None):
    def change(n):
        v = n[name].inputs[key]
        n[name].inputs[key] = v * f if f is not None else v + add
    return change


class Broken:
    """読めない入力 (GetConnectedOutput で例外)。"""

    def GetConnectedOutput(self):
        raise RuntimeError("読めない")


@pytest.mark.parametrize("item,change,expected", [
    pytest.param("layout", _set("LayoutScreen", Size=None), "unknown", id="screen-size-unreadable"),
    pytest.param("layout_back", _set("LayoutBackBlur", XBlurSize=None), "unknown", id="back-blur-unreadable"),
    pytest.param("layout_dim", _set("LayoutBackShade", TopLeftAlpha=None), "unknown", id="shade-alpha-unreadable"),
    pytest.param("layout_links", lambda n: setattr(n["LayoutBack"], "Foreground", Broken()), "unknown", id="back-input-unreadable"),
    pytest.param("layout", _scale("LayoutScreen", "Size", f=1.01), "fail", id="screen-one-percent-larger"),
    pytest.param("layout", _scale("LayoutScreenCrop", "XOffset", add=2), "fail", id="crop-shift-two-pixels"),
    pytest.param("layout", _set("LayoutScreen", Center={1: 0.5, 2: 0.5}), "fail", id="screen-center"),
    pytest.param("layout", _set("LayoutScreen", FilterMethod=2), "fail", id="screen-filter"),
    pytest.param("layout", _set("LayoutScreen", Blend=lambda t: 0.0 if t >= 90 else 1.0), "fail", id="screen-disappears-mid-row"),
    pytest.param("layout_back", _set("LayoutBackBlur", XBlurSize=5.0), "fail", id="back-blur"),
    pytest.param("layout_back", _set("LayoutBack", Size=1.0), "fail", id="back-does-not-cover-margin"),
    pytest.param("layout_back", _set("LayoutBackCrop", YOffset=0), "fail", id="back-crop"),
    pytest.param("layout_back", _set("LayoutBase", TopLeftAlpha=0.0), "fail", id="base-transparent"),
    pytest.param("layout_back", _set("LayoutBase", Width=1920, Height=1080), "fail", id="base-too-small"),
    pytest.param("layout_dim", _set("LayoutBackShade", TopLeftAlpha=0.0), "fail", id="shade-transparent"),
    pytest.param("layout_dim", _set("LayoutBackShade", TopLeftRed=1.0), "fail", id="shade-red"),
    pytest.param("layout_dim", _set("LayoutBackShade", Width=1920, Height=1080), "fail", id="shade-too-small"),
    pytest.param("layout_dim", _set("LayoutBackDim", Blend=0.0), "fail", id="dim-blend-zero"),
    pytest.param("layout_links", lambda n: setattr(n["LayoutScreen"], "Foreground", n["LayoutBackDim"]), "fail", id="screen-wrong-input"),
    pytest.param("layout_links", lambda n: setattr(n["LayoutBackDim"], "Foreground", n["LayoutBack"]), "fail", id="dim-wrong-input"),
    pytest.param("layout_links", lambda n: detach(n, "LayoutScreen"), "fail", id="screen-output-detached"),
])
def test_layout_changed_tools(sb, item, change, expected):
    sb = layout_sb(sb)
    got = layout_check(sb, layout_exp(sb)[2], layout_comp(sb, change=change))
    assert got[item] == expected, got


def test_layout_links_masks_focus_and_zoom(sb):
    """行のマスクの効果が背景の切り抜きに届かない (背景を隠す前の画から切る)、focus の暗さが背景に入る、
    ズームの Transform が残っている、のどれも layout_links の fail。"""
    sb = layout_sb(sb)
    row = layout_exp(sb)[2]
    comp = layout_comp(sb)   # マスクの効果が画面にだけ届き、背景はマスクの前の画から切っている
    comp["LayoutBackCrop"].Input = comp["Canvas"]
    assert layout_check(sb, row, comp)["layout_links"] == "fail"
    comp = layout_comp(sb, focus=True)   # focus の暗さが背景の切り抜きにも届く
    comp["LayoutBackCrop"].Input = comp["FocusDim"]
    assert layout_check(sb, dict(row, focus={"x": 1}), comp)["layout_links"] == "fail"
    comp = layout_comp(sb)   # ズームの Transform がつながったまま
    comp.AddTool("Merge").Foreground = comp.AddTool("Transform")
    assert layout_check(sb, row, comp)["layout_links"] == "fail"
    del comp.tools[-1].Foreground   # つながっていない Transform は書き出しに効かない
    assert layout_check(sb, row, comp)["layout_links"] == "pass"


@pytest.mark.parametrize("size,mask,focus", [(WIDE, True, False), (WIDE, False, True), ((1920, 1080), False, False)])
def test_layout_links_from_media_in(sb, size, mask, focus):
    """素材の入口 (MediaIn1) の出力が、背景と画面の両方の切り抜きの Input に届く (Canvas・マスク・focus を通ってよい)。
    切り抜きの Input を外す・MediaIn1 の出力を外す・MediaIn1 が無いと fail、Input を読めなければ unknown。
    止めたクリップ (hold_) も同じ。"""
    sb = layout_sb(sb)
    row = dict(layout_exp(sb, size)[2], focus={"x": 1} if focus else None)

    def run(change=None, prefix=""):
        return layout_check(sb, row, layout_comp(sb, size, focus=focus, mask=mask, change=change), prefix)[prefix + "layout_links"]
    assert run() == "pass"
    assert run(prefix="hold_") == "pass"

    def cut(name):   # 切り抜きの Input を外す (MediaIn1 からの線は残っていても、画は入らない)
        return lambda n: delattr(n[name], "Input")
    assert run(cut("LayoutBackCrop")) == "fail"
    assert run(cut("LayoutScreenCrop")) == "fail"
    assert run(cut("LayoutScreenCrop"), prefix="hold_") == "fail"
    assert run(lambda n: detach(n, "MediaIn1")) == "fail"   # 素材の入口が切り離されている
    assert run(lambda n: n.tools.remove(n["MediaIn1"])) == "fail"
    # 素材と関係のない部品 (単色) から切り抜いている
    assert run(lambda n: setattr(n["LayoutScreenCrop"], "Input", n["LayoutBase"])) == "fail"
    assert run(lambda n: setattr(n["LayoutBackCrop"], "Input", Broken())) == "unknown"


def _to_mask(src, dst, inp):
    """src の出力を、dst の画の入力 inp から外して dst の EffectMask にだけつなぐ。"""
    def change(n):
        delattr(n[dst], inp)
        n[dst].EffectMask = n[src]
    return change


def _no_ids(name):
    """name の出力のつながりから、入った入力の ID を消す (どの入力に入ったかを読めない)。"""
    def change(n):
        real = n[name].GetConnectedInputs
        n[name].GetConnectedInputs = lambda: {k: Inp(i.tool, None) for k, i in real().items()}
    return change


@pytest.mark.parametrize("prefix", ["", "hold_"])
@pytest.mark.parametrize("size,mask,focus,change,expected", [
    pytest.param(WIDE, True, False, None, "pass", id="as-built-mosaic"),
    pytest.param(WIDE, False, True, None, "pass", id="as-built-focus"),
    pytest.param(WIDE, True, False, _to_mask("MediaIn1", "Canvas", "Input"), "fail", id="media-in-to-canvas-mask"),
    pytest.param(WIDE, True, False, _to_mask("Canvas", "Mask1Mosaic", "Source"), "fail", id="canvas-to-mosaic-mask"),
    pytest.param(WIDE, False, True, _to_mask("Canvas", "FocusDim", "Background"), "fail", id="canvas-to-focus-mask"),
    pytest.param((1920, 1080), False, True, _to_mask("MediaIn1", "FocusDim", "Background"), "fail", id="media-in-to-focus-mask"),
    pytest.param(WIDE, True, False, _no_ids("Canvas"), "unknown", id="input-id-unreadable"),
])
def test_layout_links_image_inputs_only(sb, prefix, size, mask, focus, change, expected):
    """MediaIn1 から両方の切り抜きの Input へは、途中の部品の画の入力 (Crop の Input・モザイクの Source・Merge の
    Background) を通って届くときだけ pass。途中の部品のマスクの入力 (EffectMask) にだけつながる道は、線がつながっていても
    画が入らないので fail。入った入力の ID を読めなければ unknown。止めたクリップ (hold_) も同じ。"""
    sb = layout_sb(sb)
    row = dict(layout_exp(sb, size)[2], focus={"x": 1} if focus else None)
    comp = layout_comp(sb, size, focus=focus, mask=mask, change=change)
    assert layout_check(sb, row, comp, prefix)[prefix + "layout_links"] == expected


def test_layout_missing_or_unknown_size(sb):
    """layout の部品が無ければ fail、素材の解像度を読めなければ unknown、layout の書式を読めなければ unknown。"""
    sb = layout_sb(sb)
    exp = layout_exp(sb)
    assert layout_check(sb, exp[2], Comp()) == {"layout": "fail"}
    assert layout_check(sb, exp[2], None) == {"layout": "fail"}
    exp = CF.expected_rows(sb, {})
    assert exp[2]["size_unknown"] and layout_check(sb, exp[2], layout_comp(sb)) == {"layout": "unknown"}
    sb["layout"]["background"] = {"blur": -1, "dim": 0.6}
    assert layout_check(sb, layout_exp(sb)[2], Comp()) == {"layout": "unknown"}


@pytest.mark.meiryo
def test_layout_in_run_and_border_pixels_skipped(sb):
    """タイムラインを読む照らし合わせで、layout の項目が行ごとに出る (ズームの項目は出ない)。
    --pixel の枠線の照らし合わせは、画面を縮めて動かしているので比べない行にする。"""
    sb = layout_sb(sb)
    rep = CF.run(sb, None, build(sb, WIDE))
    for item in ("layout", "layout_back", "layout_dim", "layout_links"):
        assert status(rep, item) == ["pass"] * 3, item
    assert all(c["status"] == "pass" for c in rep["checks"] if not c["item"].startswith("video_"))
    assert not status(rep, "zoom") and not status(rep, "zoom_size")
    sb["rows"][0]["focus"] = {"x": 600, "y": 200, "w": 800, "h": 500, "border": True, "start": 1}
    rows, skipped = CF.border_rows(sb, layout_exp(sb))
    assert not rows and skipped == [{"row": 1, "reason": "layoutで画面を縮めて動かしている"}]


def test_layout_point(sb):
    """layout の行の指すマーク: 画面と一緒に縮めて置いた指の先が、画面が出力に映る四角の中の指す点に来る。字の大きさは
    画面を置く倍率で割った大きさ、傾ける向きは画面の真ん中へ。LayoutPoint が無い・ずれている・回っていれば fail。"""
    sb = layout_sb(sb)
    sb["rows"][2]["point"] = {"x": 1500, "y": 250}
    exp = layout_exp(sb)
    pt, L = exp[2]["point"], exp[2]["layout"]
    k = L["scale"]
    assert pt["out_target"] == pytest.approx((410.4 + (1500 - 600) * k, 12 + (250 - 40) * k), abs=0.05)
    assert pt["view"] == pytest.approx(L["view"])
    want = sb["point"]["size"] / L["end"] / (1920 * sb["fusion_text_scale"])
    assert pt["fusion_size"] == pytest.approx(want)
    v = C.scene_values(sb, sb["rows"][2], C.caption_font(sb), source_size=WIDE)["point"]   # 組む側と同じ大きさ
    assert v["size"] == pytest.approx(want, rel=1e-3)

    def run(change=None, drop=None):
        comp = row_comp(sb, sb["rows"][2], WIDE)
        if change:
            change(comp)
        if drop:
            comp.tools.remove(comp[drop])
        checks = []
        CF.check_point(checks, sb, exp[2], comp)
        return [c["status"] for c in checks if c["item"] == "point"]
    assert run() == ["pass"]
    assert run(_set("LayoutPoint", Center={1: 0.5, 2: 0.5})) == ["fail"]   # 画面と違う所へずれる
    assert run(_set("LayoutPoint", Size=1.0)) == ["fail"]                 # 縮めていない
    assert run(_set("LayoutPoint", Angle=10.0)) == ["fail"]
    assert run(_set("LayoutPoint", Size=None)) == ["unknown"]
    assert run(lambda n: setattr(n["PointOver"], "Foreground", n["PointTilt"])) == ["fail"]   # 縮めずに重ねる
    assert run(lambda n: setattr(n["PointOver"], "Background", n["LayoutBackDim"])) == ["fail"]
    assert run(drop="LayoutPoint") == ["fail"]
    # LayoutPoint の Size は画面を置く倍率 (組む側の layout_values の size と同じ)。Size だけ 5 % 大きくし、指の先が
    # 出力の指す点に残るよう Center を直しても、マークが point.size px と違う大きさに映るので fail
    assert pt["layout_size"] == pytest.approx(L["screen_size"])
    assert pt["layout_size"] == pytest.approx(C.layout_values(sb, *WIDE)["size"], rel=1e-3)

    def bigger(n):
        lp, f = n["LayoutPoint"], 1.05
        s, c = lp.inputs["Size"], lp.inputs["Center"]
        tip = (pt["target"][0] / 1920, 1 - pt["target"][1] / 1080)   # 縮める前の指の先 (画面を 1 とした値)
        lp.inputs.update(Size=s * f, Center={i: c[i] + (s - s * f) * (tip[i - 1] - 0.5) for i in (1, 2)})
    checks = []
    comp = row_comp(sb, sb["rows"][2], WIDE)
    bigger(comp)
    CF.check_point(checks, sb, exp[2], comp)
    got = next(c for c in checks if c["item"] == "point")
    assert got["status"] == "fail"
    assert got["actual"]["out_target"] == pytest.approx(got["expected"]["out_target"], abs=1.0)   # 指の先は合っている
    assert got["expected"]["layout_point_size"] == pytest.approx(L["screen_size"])
    # layout の書式を読めなければ、指すマークも unknown
    sb["layout"]["background"] = {"blur": 20, "dim": 2}
    exp = layout_exp(sb)
    assert exp[2]["point"]["unreadable"] and exp[2]["layout"]["unreadable"]
