"""sb2resolve.py (構成表から Resolve のタイムラインを組む) と common.build_fusion のテスト。Resolve の API は模擬のオブジェクト。

- マスクの値: 種類と形、keys (行の頭からの秒 → フレーム)、強さの上書き、構成表の全体の masks、書式の誤り
- Fusion の組み方: マスク (モザイク・ぼかし・黒塗り) を書いた順につなぎ、keys は BezierSpline と XYPath のキーフレーム
- タイムライン: 全部の行を 1 回の AppendToTimeline で置き、戻り値の項目をそのまま使い、倍速は等速で置いてから
  RippleTimeline=False で変える。50 行・108001 フレームの素材でもトラックを読み直さない
- 組む前の点検、プロジェクトの名前、プロジェクトの設定の読み戻し
"""
import copy
import os

import pytest

from conftest import FRAME_INPUTS, Item, patch_common

import api_trial
import common as C
import sb2resolve as S

LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値
TRIAL = os.path.join(C.REPO, "tests", "trial-storyboard.json")


def chain(comp):
    """MediaOut から入力をたどった部品の ID の列 (MediaIn まで)。"""
    out, cur = [], comp.tools[1].Input
    while cur is not comp.tools[0]:
        out.append(cur.ID)
        cur = getattr(cur, "Source", None) or getattr(cur, "Input", None) or cur.Background
    return out[::-1]


# ---------- マスクの値 ----------
def test_mask_values_types_keys_and_strength():
    look = dict(LOOK, masks=[{"type": "black", "x": 0, "y": 0, "w": 100, "h": 50}])   # 全体の masks も入る
    row = {"masks": [{"type": "mosaic", "shape": "ellipse", "x": 200, "y": 100, "w": 400, "h": 200, "pixel_frequency": 5},
                     {"x": 2000, "y": 1000, "w": 200, "h": 100,
                      "keys": [{"t": 1.0, "x": 3000, "y": 1000, "w": 200, "h": 100},
                               {"t": 0, "x": 2000, "y": 1000, "w": 200, "h": 100}]}]}
    v = C.scene_values(look, row, source_size=(3840, 2160))["masks"]
    assert [(m["type"], m["shape"]) for m in v] == [("black", "rect"), ("mosaic", "ellipse"), ("blur", "rect")]
    assert v[0]["black_rgba"] == LOOK["mask"]["black_rgba"] and v[1]["pixel_frequency"] == 5
    assert v[2]["blur_size"] == LOOK["mask"]["blur_size"]
    # keys は t の順にフレームへ。素材 4K の (3000, 1000) は 1080p の (1500, 500)
    k = v[2]["keys"]
    assert [x["frame"] for x in k] == [0, 30]
    assert k[1]["center"] == pytest.approx(((1500 + 50) / 1920, 1 - (500 + 25) / 1080))
    assert (k[1]["width"], k[1]["height"]) == pytest.approx((100 / 1920, 50 / 1080))
    assert v[0]["keys"] == []


@pytest.mark.parametrize("m", [{"type": "pixel"}, {"shape": "star"}, {"w": 0}, {"keys": [{"t": -1, "x": 0, "y": 0, "w": 1, "h": 1}]},
                               {"keys": [{"t": 0, "x": 0, "y": 0}]}])
def test_mask_format_errors(m):
    mask = {"x": 0, "y": 0, "w": 10, "h": 10} | m
    assert C.mask_problems(mask)
    with pytest.raises(ValueError):
        C.scene_values(LOOK, {"masks": [mask]})


# ---------- Fusion の組み方 ----------
@pytest.mark.parametrize("src, want", [
    ((3440, 1440), {"width": 3440, "height": 1935, "x_offset": 0, "y_offset": -247.5}),   # 横長は高さを足す
    ((1920, 1200), {"width": 2133, "height": 1200, "x_offset": -106.5, "y_offset": 0}),   # 縦長は幅を足す
    ((3840, 2160), None), ((1920, 1080), None), ((0, 0), None)])                          # 比が同じ・分からない
def test_fusion_canvas(src, want):
    assert C.fusion_canvas(*src, 1920, 1080) == want


def test_hold_keeps_canvas():
    # 止めたクリップも同じ広げ方で組む (無いと、止めた所でマスクがずれて隠していた所が見える)
    row = {"masks": [{"type": "black", "x": 1400, "y": 500, "w": 600, "h": 300}], "caption": "a"}
    v = C.scene_values(LOOK, row, source_size=(3440, 1440))
    hv = C.hold_values(v, 10, 30)
    assert hv["canvas"] == v["canvas"] and hv["canvas"]["height"] == 1935
    it = Item()
    ok, res = C.build_fusion(it, hv)
    assert ok and res["canvas"] == [3440, 1935] and chain(it.comp)[0] == "Crop"


def test_callout_values_on_widened_canvas():
    co = {"src": {"x": 1000, "y": 300, "w": 400, "h": 100}, "dst": {"x": 200, "y": 200, "w": 800}}
    wide = C.callout_values(LOOK, co, 3440, 1440, 30)
    # 切り抜きは広げた画面の画素 (下から数える YOffset に余白 247.5 を足す)
    assert wide["pieces"][0]["crop"] == (1000, 1440 - 300 - 100 + 247.5, 400, 100)
    # 置き場所は、広げた画面がタイムラインと同じ比なので、タイムラインの画素を画面の大きさで割った値
    h = 100 * 800 / 400
    assert wide["pieces"][0]["center"] == pytest.approx(((200 + 400) / 1920, 1 - (200 + h / 2) / 1080))
    assert wide["fill"]["width"] == pytest.approx(800 / 1920) and wide["fill"]["height"] == pytest.approx(h / 1080)
    # 比が同じ素材は余白なし
    std = C.callout_values(LOOK, co, 3840, 2160, 30)
    assert std["pieces"][0]["crop"] == (1000, 2160 - 300 - 100, 400, 100)
    assert std["pieces"][0]["center"] == pytest.approx(((200 + 400) / 1920, 1 - (200 + h / 2) / 1080))


def test_build_fusion_widens_canvas_only_for_other_aspect():
    row = {"zoom": {"x": 568, "y": 46, "w": 2304, "h": 1296, "seconds": 0}, "caption": "a"}
    it = Item()
    ok, res = C.build_fusion(it, C.scene_values(LOOK, row, source_size=(3440, 1440)))
    assert ok and res["canvas"] == [3440, 1935]
    assert chain(it.comp)[0] == "Crop" and it.comp["Canvas"].inputs == {"XSize": 3440, "YSize": 1935, "XOffset": 0, "YOffset": -247.5}
    # 単色・Text+・マスクの部品は、既定の大きさに従わず、広げた画面の大きさで描く
    sized = [t for t in it.comp.tools if t.ID in FRAME_INPUTS]
    assert {t.ID for t in sized} >= {"Background", "TextPlus", "RectangleMask"}
    for t in sized:
        wk, hk = FRAME_INPUTS[t.ID]
        assert (t.inputs["UseFrameFormatSettings"], t.inputs[wk], t.inputs[hk]) == (0, 3440, 1935)
        if wk == "MaskWidth":
            assert t.inputs["Width"] <= 1   # マスクの形の大きさ (画面を 1 とした値) は触らない
    it = Item()
    ok, res = C.build_fusion(it, C.scene_values(LOOK, row, source_size=(3840, 2160)))
    assert ok and "canvas" not in res and "Crop" not in chain(it.comp)
    assert all(t.inputs["UseFrameFormatSettings"] == 1 for t in it.comp.tools if t.ID in FRAME_INPUTS)   # 比が同じなら触らない


def test_build_fusion_masks_in_order_and_keyframes():
    row = {"caption": "テロップ", "badge": "倍速", "zoom": {"x": 1200, "y": 100, "w": 540, "h": 360, "seconds": 2},
           "masks": [{"type": "mosaic", "x": 0, "y": 0, "w": 100, "h": 100},
                     {"type": "blur", "shape": "ellipse", "x": 100, "y": 0, "w": 100, "h": 100, "blur_size": 12},
                     {"type": "black", "x": 0, "y": 0, "w": 100, "h": 100,
                      "keys": [{"t": 0, "x": 0, "y": 0, "w": 100, "h": 100}, {"t": 2, "x": 960, "y": 540, "w": 200, "h": 100}]}]}
    it = Item()
    ok, res = C.build_fusion(it, C.scene_values(LOOK, row))
    comp = it.comp
    assert ok and [m["type"] for m in res["masks"]] == ["mosaic", "blur", "black"]
    # MediaIn → モザイク → ぼかし → 黒塗り (Merge) → ズーム → テロップの背景 → 文字 → 肩テロップ
    assert chain(comp) == ["ofx.com.blackmagicdesign.resolvefx.MosaicBlur", "Blur", "Merge", "Transform",
                           "Merge", "Merge", "Merge"]
    assert comp["Mask2"].ID == "EllipseMask" and comp["Mask2Blur"].inputs["XBlurSize"] == 12
    assert comp["Mask2Blur"].EffectMask is comp["Mask2"]
    black = comp["Mask3Black"]
    assert black.EffectMask is comp["Mask3"] and black.inputs["TopLeftAlpha"] == 1.0
    # 動くマスク: 大きさは BezierSpline、中心は XYPath の X と Y。0 と 60 フレームにキー
    m3 = comp["Mask3"]
    assert m3.links["Width"].keys == {0: 100 / 1920, 60: 200 / 1920}
    assert m3.links["Height"].keys == {0: 100 / 1080, 60: 100 / 1080}
    assert m3.links["Center"].ID == "XYPath"
    assert m3.links["Center"].links["X"].keys == pytest.approx({0: 50 / 1920, 60: 1060 / 1920})
    assert m3.links["Center"].links["Y"].keys == pytest.approx({0: 1 - 50 / 1080, 60: 1 - 590 / 1080})
    assert "Center" not in comp["Mask1"].links    # 動かないマスクは値だけ
    assert comp["BadgeOver"].links["Blend"].keys == {0: 1.0, 59: 1.0, 60: 0.0}
    assert comp["Zoom"].links["Size"].keys[60] == pytest.approx(1080 / 360)
    assert comp["CaptionText"].inputs["StyledText"] == "テロップ"


def test_fast_row_fusion_times_follow_clip_speed():
    """速さを変えたクリップの合成の時刻は素材の速さで進む。4 倍速の行の肩テロップは合成の 239 / 240、keys の 1 秒は 120。"""
    row = {"badge": "x", "zoom": {"x": 0, "y": 0, "w": 960, "h": 540, "seconds": 1},
           "masks": [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10,
                      "keys": [{"t": 0, "x": 0, "y": 0, "w": 10, "h": 10}, {"t": 1, "x": 100, "y": 0, "w": 10, "h": 10}]}]}
    v = C.scene_values(LOOK, row, speed=4.0)
    assert v["badge"]["blend"] == [(0, 1.0), (239, 1.0), (240, 0.0)]
    assert [k["comp"] for k in v["masks"][0]["keys"]] == [0, 120] and v["zoom"]["comp_frames"] == 120
    it = Item()
    C.build_fusion(it, v)
    assert it.comp["BadgeOver"].links["Blend"].keys[240] == 0.0
    # 1.5 倍: 書き出しの 59 フレーム目 (合成の 88.5) は出ていて、60 フレーム目 (合成の 90) で消える
    assert C.badge_blend_keys(60, 1.5) == [(0, 1.0), (89, 1.0), (90, 0.0)]
    assert C.badge_blend_keys(60) == [(0, 1.0), (59, 1.0), (60, 0.0)]


# ---------- タイムライン ----------
class MPI:
    def __init__(self, fps=30.0, frames=108001, res="3840x2160"):
        self.p = {"FPS": fps, "Frames": str(frames), "Resolution": res}

    def GetClipProperty(self, k):
        return self.p.get(k)


class TItem:
    def __init__(self, info, t0, fps_ratio, tl=None):
        self.info, self.t0, self.ratio, self.speed = info, t0, fps_ratio, 100.0
        self.set_speed, self.alone, self.tl, self.frozen = None, False, tl, None

    def GetStart(self):
        return self.info["recordFrame"]

    def GetEnd(self):
        return self.info["recordFrame"] + round((self.info["endFrame"] - self.info["startFrame"]) / self.ratio)

    def SetSpeed(self, o):
        self.set_speed, self.speed = o, o["Percentage"]
        if self.speed == 0:
            # 止めたクリップ (Percentage 0) は、止めるときに再生ヘッドがクリップの直後にあれば置いた範囲の最後のフレーム、
            # 上か前にあれば頭のフレームを映す (21.1.1 の実機)。ほかのクリップとまとめて置いたものは頭のフレーム
            # (まとめた中の最初に止めたものは最後のフレームだが、模擬では分けない)
            last = self.alone and self.tl is not None and self.tl.playhead == self.GetEnd()
            self.frozen = self.info["endFrame"] - 1 if last else self.info["startFrame"]
            if self.tl is not None and self.frozen in self.tl.unfreezable:   # Resolve が止められないフレームは手前を映す
                self.frozen -= 1
            if self.tl is not None and self.tl.speed_fail:   # 止める操作が False を返す (画は止まっている)
                self.tl.speed_fail -= 1
                return False
        return True

    def GetLinkedItems(self):
        return []

    def GetSpeed(self):
        return {"Percentage": self.speed}

    def GetSourceStartFrame(self):
        if self.speed == 0:
            return self.frozen
        return self.info["startFrame"]

    def GetSourceEndFrame(self):
        n = self.info["endFrame"] - self.info["startFrame"]
        return self.info["startFrame"] + round(n * self.speed / 100)

    def GetRightOffset(self):
        return 10

    def AddMarker(self, *a):
        return True


class MediaPool:
    def __init__(self, fps=30.0):
        self.fps, self.calls, self.imports, self.track_reads = fps, [], [], 0

    def ImportMedia(self, paths):
        self.imports += paths
        return [MPI(self.fps)]

    def CreateEmptyTimeline(self, name):
        self.tl = Timeline(self)
        return self.tl

    def AppendToTimeline(self, infos):
        self.calls.append(infos)
        tl = getattr(self, "tl", None)
        out = [TItem(i, 108000, self.fps / 30, tl) for i in reversed(infos)]   # 順番に頼らないことも見る
        for it in out:
            it.alone = len(infos) == 1
        if tl is not None and not tl.stuck_append:   # 置いた後、再生ヘッドは置いたクリップの直後へ動く
            tl.playhead = max(it.GetEnd() for it in out)
        return out


class Timeline:
    """再生ヘッドを持つ。stuck_append なら AppendToTimeline の後に再生ヘッドが動かず、
    stuck_set の回数だけ SetCurrentTimecode が True を返しても動かない (実機で動かないことがあった形)。"""
    def __init__(self, mp):
        self.mp, self.playhead, self.stuck_append, self.stuck_set, self.deleted = mp, 108000, False, 0, []
        self.unfreezable = set()   # 止めると手前のフレームを映す素材のフレーム (21.1.1 の実機で 1 つ見つかった形)
        self.speed_fail = 0        # この回数だけ、止める SetSpeed が False を返す

    def GetStartFrame(self):
        return 108000

    def GetStartTimecode(self):
        return "01:00:00:00"

    def SetCurrentTimecode(self, tc):
        if self.stuck_set:
            self.stuck_set -= 1
        else:
            self.playhead = C.timecode_to_frame(tc, 30)
        return True

    def GetCurrentTimecode(self):
        return C.frame_to_timecode(self.playhead, 30)

    def DeleteClips(self, items, ripple=False):
        self.deleted.append((items, ripple))
        return True

    def GetItemListInTrack(self, *a):
        self.mp.track_reads += 1
        return []


class Proj:
    def SetCurrentTimeline(self, tl):
        return True


def sb_rows(n, tmp_path, fps=30):
    src = tmp_path / "rec.mp4"
    src.write_bytes(b"")
    rows = [{"start": i * 80, "end": i * 80 + (60 if i % 2 else 5), "seconds": 5, "caption": f"{i}"} for i in range(n)]
    sb = dict(LOOK, source=str(src), output=str(tmp_path / "o.mp4"), rows=rows, fps=fps)
    return sb


def test_build_timeline_50_rows_one_append_and_exact_lengths(tmp_path):
    sb = sb_rows(50, tmp_path)
    rows = S.plan(sb)
    mp = MediaPool()
    media = S.import_media(mp, rows, 30)
    assert mp.imports == [sb["source"]]                     # 同じ素材は 1 回だけ取り込む
    tl, items, _, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    assert len(mp.calls) == 1 and len(mp.calls[0]) == 50 and mp.track_reads == 0
    assert all(r["match"] for r in res), [r for r in res if not r["match"]]
    assert [it.GetStart() - 108000 for it in items] == [i * 150 for i in range(50)]
    # 等速で 150 フレーム分だけ置き、12 倍の行 (60 秒 → 5 秒) は RippleTimeline=False で速さを変える
    assert mp.calls[0][1]["endFrame"] - mp.calls[0][1]["startFrame"] == 150
    assert items[1].set_speed == {"Percentage": pytest.approx(1200), "RippleTimeline": False}
    assert items[0].set_speed is None
    assert res[1]["result"]["src_end"] == 140 * 30           # 素材の 80〜140 秒


def test_build_timeline_source_frames_use_source_fps(tmp_path):
    """タイムライン 30 fps、素材 60 fps。素材の 80 秒目は素材の 4800 フレーム目。"""
    sb = sb_rows(2, tmp_path)
    rows = S.plan(sb)
    mp = MediaPool(fps=60.0)
    media = S.import_media(mp, rows, 30)
    _, items, _, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    assert mp.calls[0][1]["startFrame"] == 4800 and mp.calls[0][1]["endFrame"] == 4800 + 300
    assert all(r["match"] for r in res)


def test_problems_before_resolve(tmp_path):
    sb = sb_rows(3, tmp_path)
    sb["rows"][0].update(start=10, end=5)
    sb["rows"][1]["masks"] = [{"type": "pixel", "x": 0, "y": 0, "w": 1, "h": 1}]
    sb["rows"][2].update(start=100, end=200)
    bad = S.problems(sb, S.plan(sb), {sb["source"]: 150.0})
    assert any("1 行目: end" in b for b in bad) and any("2 行目のマスク 1: type" in b for b in bad)
    assert any("3 行目: 素材の長さ" in b for b in bad)
    sb["rows"][2]["source"] = str(tmp_path / "none.mp4")
    assert any("3 行目: 素材が無い" in b for b in S.problems(sb, S.plan(sb), {}))


@pytest.mark.parametrize("text, want", [
    pytest.param("9 倍速", {"text": "9 倍速", "speed": 9.0, "places": 0}, id="integer-with-space"),
    pytest.param("22倍速", {"text": "22 倍速", "speed": 22.0, "places": 0}, id="integer-no-space"),
    pytest.param("約 5.4 倍速", {"text": "5.4 倍速", "speed": 5.4, "places": 1}, id="decimal"),
    pytest.param("１２倍速", {"text": "12 倍速", "speed": 12.0, "places": 0}, id="fullwidth-digits"),
    pytest.param("２．５　倍速", {"text": "2.5 倍速", "speed": 2.5, "places": 1}, id="fullwidth-decimal-and-space"),
    pytest.param("ここから等倍", {"text": "等倍", "speed": 1.0, "places": None}, id="same-speed"),
    pytest.param("待ち時間 (約 1 分) をスキップ", None, id="no-speed"),
    pytest.param("倍速中", None, id="speed-without-number"),
    pytest.param("2 倍に拡大", None, id="zoom-not-speed"),
    pytest.param("1/4 倍速", None, id="fraction-not-read"),
    pytest.param("", None, id="empty"),
    pytest.param(None, None, id="no-badge"),
])
def test_badge_speed_label(text, want):
    assert C.badge_speed_label(text) == want


def speed_sb(tmp_path, badge, seconds=None, per="source", start=0, end=54):
    """1 行の構成表。使う範囲は start〜end 秒、行の秒数は seconds (無ければ seconds_per_row)。"""
    sb = sb_rows(1, tmp_path)
    sb["seconds_per_row"] = per
    row = {"start": start, "end": end, "badge": badge}
    if seconds is not None:
        row["seconds"] = seconds
    sb["rows"] = [row]
    return sb


@pytest.mark.parametrize("badge, seconds, ok", [
    pytest.param("9 倍速", None, False, id="source-row-says-9x"),
    pytest.param("等倍", None, True, id="source-row-says-same"),
    pytest.param("5 倍速", 10, True, id="5.4x-says-5x"),
    pytest.param("5.4 倍速", 10, True, id="5.4x-says-5.4x"),
    pytest.param("6 倍速", 10, False, id="5.4x-says-6x"),
    pytest.param("5.5 倍速", 10, False, id="5.4x-says-5.5x"),
    pytest.param("等倍", 10, False, id="5.4x-says-same"),
    pytest.param("等倍", 53.6, True, id="within-1-percent-says-same"),
    pytest.param("等倍", 53, False, id="over-1-percent-says-same"),
    pytest.param("肩テロップのテスト", 10, True, id="no-speed-not-compared"),
])
def test_badge_speed_before_resolve(tmp_path, badge, seconds, ok):
    """肩テロップの速さの表記と、使う範囲 (54 秒) ÷ 行の秒数。合わなければ組む前の点検の誤り。"""
    sb = speed_sb(tmp_path, badge, seconds)
    bad = [b for b in S.problems(sb, S.plan(sb), {sb["source"]: 150.0}) if "肩テロップ" in b]
    assert (bad == []) == ok, bad


def test_badge_speed_message_and_hold(tmp_path):
    """文言は行番号つき。止めた区間 (hold) は速さに入れない。"""
    sb = speed_sb(tmp_path, "9 倍速")
    sb["rows"].insert(0, {"start": 0, "end": 5, "badge": "等倍"})
    assert S.problems(sb, S.plan(sb), {})[-1:] == ["2 行目: 肩テロップは 9 倍速だが、実際の速さは 1.0 倍 (等倍)"]
    sb = speed_sb(tmp_path, "9 倍速", seconds=6)
    sb["rows"][0]["hold"] = {"seconds": 3}
    assert not [b for b in S.problems(sb, S.plan(sb), {}) if "肩テロップ" in b]


def test_badge_speed_stops_dry_run(tmp_path, monkeypatch):
    """--dry-run でも、肩テロップの速さが合わなければ誤りで止める (Resolve に触らない)。"""
    sb = speed_sb(tmp_path, "9 倍速")
    path = tmp_path / "sb.json"
    C.write_json(str(path), {k: sb[k] for k in ("source", "output", "rows", "fps", "seconds_per_row")})
    monkeypatch.setattr(S, "probe_sources", lambda rows: {})
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: pytest.fail("接続した"))
    import argparse
    ok, summary, lines = S.run(argparse.Namespace(storyboard=str(path), source=None, name=None, render=False,
                                                  dry_run=True), {})
    assert not ok and "誤り 1 件" in summary
    assert lines == ["1 行目: 肩テロップは 9 倍速だが、実際の速さは 1.0 倍 (等倍)"]


class PM:
    def __init__(self, names):
        self.names = names

    def GetProjectListInCurrentFolder(self):
        return self.names


def test_project_name_never_overwrites(tmp_path):
    sb = {"output": str(tmp_path / "sample-v3.mp4")}
    assert S.project_name(PM([]), sb) == "sample-v3"
    assert S.project_name(PM(["sample-v3"]), sb).startswith("sample-v3-")
    assert S.project_name(PM([]), dict(sb, project="完成")) == "完成"
    assert S.project_name(PM(["test-a"]), sb, "test-a") is None


class SetProj:
    def __init__(self, stuck=()):
        self.v, self.stuck = {"timelinePlaybackFrameRate": "24", "videoMonitorFormat": "HD 1080p 24"}, stuck

    def SetSettings(self, d):
        ok = not any(k in self.stuck for k in d)
        if ok:
            self.v.update(d)
        return ok

    def GetSetting(self, k):
        return self.v.get(k)


def test_project_settings_reports_playback_rate():
    r = S.project_settings(SetProj(), 1920, 1080, 30)
    assert r["warnings"] == [] and r["read"]["videoMonitorFormat"] == "HD 1080p 30"
    r = S.project_settings(SetProj(stuck=("timelinePlaybackFrameRate",)), 1920, 1080, 30)
    assert len(r["warnings"]) == 1 and "再生フレームレート" in r["warnings"][0] and "マスター設定" in r["warnings"][0]


def test_api_trial_uses_sb2resolve_parts(monkeypatch, tmp_path):
    """api_trial.py の Fusion は sb2resolve.add_fusion: 肩テロップは 1 行目、マスクとズームは 3 行目。"""
    sb = C._read_json(TRIAL)
    sb.update({k: v for k, v in LOOK.items() if k not in sb})
    items = [Item() for _ in sb["rows"]]
    rows = S.plan(dict(sb, source=str(tmp_path / "x.mp4")))
    media = {rows[0]["source"]: {"size": (1920, 1080)}}
    monkeypatch.setattr(api_trial, "SB", sb, raising=False)
    monkeypatch.setattr(api_trial, "FONT_FILE", None, raising=False)
    monkeypatch.setattr(api_trial, "ctx", {"items": items, "rows": rows, "media": media})
    monkeypatch.setattr(api_trial, "WARNINGS", [])
    ok, res = api_trial.fusion_effects()
    ids = [[t.ID for t in it.comp.tools] for it in items]
    assert ok and sorted(res) == ["row1", "row2", "row3"]
    assert "BadgeOver" in [t.Name for t in items[0].comp.tools]
    assert "BadgeOver" not in [t.Name for t in items[1].comp.tools + items[2].comp.tools]
    assert "Transform" in ids[2] and "ofx.com.blackmagicdesign.resolvefx.MosaicBlur" in ids[2]
    assert "Transform" not in ids[0] + ids[1]
    assert all(it.props["DynamicZoomEnabled"] is False for it in items)


# ---------- 周りを暗くする (focus) ----------
def test_focus_values_and_fusion_order():
    """範囲は素材の画素、feather も素材の画素で、SoftEdge は画面の幅を 1 とした値。マスクの後・ズームの前につなぐ。"""
    row = {"focus": {"x": 1600, "y": 600, "w": 1200, "h": 800, "dim": 0.4},
           "zoom": {"x": 0, "y": 0, "w": 1920, "h": 1080, "seconds": 1}, "caption": "テロップ",
           "masks": [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10}]}
    v = C.scene_values(LOOK, row, source_size=(3840, 2160), speed=2.0)["focus"]
    assert v["center"] == pytest.approx(((800 + 300) / 1920, 1 - (300 + 200) / 1080))
    assert v["soft"] == pytest.approx(LOOK["focus"]["feather"] / 2 / 1920) and v["dim"] == 0.4
    f = round(LOOK["focus"]["seconds"] * 30)
    assert v["ramp_frames"] == [(0, 0.0), (f, 1.0)] and v["ramp"] == [(0, 0.0), (2 * f, 1.0)]
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, row))
    comp = it.comp
    assert chain(comp) == ["Merge", "Merge", "Transform", "Merge", "Merge"]   # 黒塗り → FocusDim → ズーム → テロップ
    assert comp["FocusDim"].Foreground is comp["FocusShade"]
    area = comp["FocusArea"]
    assert area.inputs["Invert"] == 1 and comp["FocusShade"].EffectMask is area
    f = round(LOOK["focus"]["seconds"] * 30)
    assert comp["FocusShade"].links["TopLeftAlpha"].keys == pytest.approx({0: 0.0, f: 0.6})
    with pytest.raises(ValueError):
        C.scene_values(LOOK, {"focus": {"x": 0, "y": 0, "w": 10, "h": 10, "dim": 2}})


# ---------- 遅い行の肩テロップ・素材の解像度・書き出しの待ち・--dry-run ----------
def test_badge_keys_slow_row():
    """0.5 倍速: 書き出しの 59 フレーム目は合成の 29、60 フレーム目は 30。30 で消える (余分に繰り上げない)。"""
    assert C.badge_blend_keys(60, 0.5) == [(0, 1.0), (29, 1.0), (30, 0.0)]


def test_add_fusion_refuses_unknown_source_size(tmp_path):
    """素材の解像度が分からない行のマスクは、違う座標で黙って組まずに失敗にする。座標を使わない行は組む。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0]["masks"] = [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10}]
    rows = S.plan(sb)
    items = [Item(), Item()]
    ok, res, msgs = S.add_fusion(items, sb, rows, {sb["source"]: {"size": (None, None)}}, None)
    assert not ok and items[0].comp is None and items[1].comp is not None
    assert any("解像度を読めない" in m for m in msgs)


def test_add_fusion_builds_point_only_row(tmp_path):
    """指すマーク (point) だけの行も Fusion を組む (止めた区間も)。素材の解像度が分からなければ組まずに失敗にする。"""
    sb = sb_rows(2, tmp_path)
    for r in sb["rows"]:
        r.pop("caption")
    sb["rows"][0]["point"] = {"x": 500, "y": 600}
    sb["rows"][0]["hold"] = {"seconds": 1}
    rows = S.plan(sb)
    items, holds = [Item(), Item()], [Item(), None]
    ok, res, msgs = S.add_fusion(items, sb, rows, {sb["source"]: {"size": (1920, 1080), "fps": 30.0}}, None, holds)
    assert ok, msgs
    assert items[0].comp is not None and "row1" in res and holds[0].comp is not None and "row1_hold" in res
    assert items[1].comp is None   # 何も書いていない行には追加しない
    items, holds = [Item(), Item()], [Item(), None]
    ok, res, msgs = S.add_fusion(items, sb, rows, {sb["source"]: {"size": (None, None), "fps": 30.0}}, None, holds)
    assert not ok and items[0].comp is None and holds[0].comp is None
    assert any("解像度を読めない" in m for m in msgs)


def test_add_fusion_builds_side_only_row(tmp_path):
    """解説の欄 (layout.side・行の side) だけの行も Fusion を組む (止めた区間も)。
    layout は全部の行に画面を置く部品 (Layout*) を入れるので、layout の無い行は余白 (side.area) を直接置いて作る。"""
    sb = sb_rows(1, tmp_path)
    sb["rows"][0].pop("caption")
    sb["rows"][0]["hold"] = {"seconds": 1}
    sb["layout"] = {"screen": {"x": 600, "y": 40, "w": 2240, "h": 1260}, "anchor": "top-left",
                    "side": [{"title": "種類", "text": "ドキュメント"}]}
    bare = copy.deepcopy(sb)
    laid = C.apply_layout(sb)
    del bare["layout"]
    bare["side"], bare["rows"][0]["side"] = dict(laid["side"]), "ドキュメント"
    for s in (laid, bare):
        rows = S.plan(s)
        items, holds = [Item()], [Item()]
        ok, res, msgs = S.add_fusion(items, s, rows, {s["source"]: {"size": (3440, 1440), "fps": 30.0}}, None, holds)
        assert ok, msgs
        assert items[0].comp is not None and "row1" in res and holds[0].comp is not None and "row1_hold" in res


def test_source_end_tolerance_is_bounded(tmp_path):
    """素材の最後まで届く行でも、終わりの差は 2 フレームまで。"""
    sb = sb_rows(1, tmp_path)
    rows = S.plan(sb)
    mp = MediaPool()
    media = S.import_media(mp, rows, 30)
    media[sb["source"]]["frames"] = 150
    TItem.GetRightOffset = lambda self: 0
    TItem.GetSourceEndFrame = lambda self: self.info["endFrame"] - 5   # 5 フレーム足りない
    try:
        _, _, _, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    finally:
        del TItem.GetRightOffset, TItem.GetSourceEndFrame
        TItem.GetRightOffset = lambda self: 10
        TItem.GetSourceEndFrame = lambda self: self.info["startFrame"] + round(
            (self.info["endFrame"] - self.info["startFrame"]) * self.speed / 100)
    assert not res[0]["match"]


class RenderProj:
    def __init__(self, fmt=True, settings=True):
        self.fmt, self.settings, self.started = fmt, settings, False

    def SetCurrentRenderFormatAndCodec(self, f, c):
        return self.fmt

    def SetRenderSettings(self, d):
        return self.settings

    def AddRenderJob(self):
        return "job"

    def StartRendering(self, jobs, isInteractiveMode=False):
        self.started = True
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, j):
        return {"JobStatus": "Complete"}


def test_render_via_temp_keeps_temp_dir_while_resolve_is_still_rendering(tmp_path):
    """上限で待つのをやめても Resolve が書き出し中 (JobStatus が Rendering のまま) なら、一時フォルダーを消さずに失敗にする。"""
    class Busy(RenderProj):
        def IsRenderingInProgress(self):
            return True

        def GetRenderJobStatus(self, j):
            return {"JobStatus": "Rendering"}
    dest = str(tmp_path / "final.mp4")
    rr = S.render_via_temp(Busy(), LOOK, dest, limit=0)
    assert rr["done"] is False and rr["still_rendering"] and os.path.isdir(rr["temp_dir"])
    assert rr["path"] and os.path.dirname(rr["path"]) == rr["temp_dir"] and not os.path.exists(dest)
    # 書き出しが終わっていれば (Failed など)、空の一時フォルダーは消す
    class Failed(RenderProj):
        def GetRenderJobStatus(self, j):
            return {"JobStatus": "Failed"}
    rr = S.render_via_temp(Failed(), LOOK, str(tmp_path / "f2.mp4"), limit=0)
    assert rr["done"] is False and rr["path"] is None and "still_rendering" not in rr


@pytest.mark.parametrize("settings,expected", [
    pytest.param({"fmt": False}, False, id="format-rejected"),
    pytest.param({"settings": False}, False, id="settings-rejected"),
    pytest.param({}, True, id="settings-accepted"),
])
def test_render_stops_when_settings_fail(tmp_path, settings, expected):
    proj = RenderProj(**settings)
    r = S.render(proj, LOOK, str(tmp_path), "x", limit=5)
    assert r["done"] == expected and proj.started == expected


def test_dry_run_does_not_touch_resolve(tmp_path, monkeypatch):
    """--dry-run は点検と値の計算だけ。Resolve に接続しない。ズームの警告は素材の解像度で直した後の値で出る。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0]["zoom"] = {"x": 0, "y": 0, "w": 1280, "h": 720, "seconds": 1}   # 4K の 1280x720 は 16:9 で、3:2 からはみ出す
    sb["rows"][1]["focus"] = {"x": 0, "y": 0, "w": 100, "h": 100}
    sb["safe_area"] = {"aspect": "3:2"}
    path = tmp_path / "sb.json"
    C.write_json(str(path), {k: sb[k] for k in ("source", "output", "rows", "fps", "safe_area")})
    monkeypatch.setattr(S, "probe_sources", lambda rows: {sb["source"]: {"width": 3840, "height": 2160, "fps": 30.0,
                                                                          "duration": 9999.0}})
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: pytest.fail("接続した"))
    import argparse
    log = {}
    ok, summary, lines = S.run(argparse.Namespace(storyboard=str(path), source=None, name=None, render=False,
                                                  dry_run=True), log)
    assert ok and "--dry-run" in summary and "誤り 0" in summary
    assert any("ズーム範囲 640x360" in x for x in lines)


def test_badge_box_grows_left_and_closing_caption():
    """肩テロップの板は右上の角を動かさずに左と下へ広がる。結びのテロップは画面の中央に、名前 Closing* の部品で組む。"""
    f = C.badge_font(LOOK)
    if f is None:
        pytest.skip("書体のファイルが無い")
    small, big = C.badge_box(LOOK), C.badge_box(LOOK, LOOK["badge"]["prefix"] + "約 4 分 10 秒の待ち時間をスキップ", f)
    assert big["measured"] and big["width"] > small["width"] and big["height"] > small["height"]
    right = lambda b: b["center"][0] + b["width"] / 2   # noqa: E731
    top = lambda b: b["center"][1] + b["height"] / 2    # noqa: E731
    assert right(big) == pytest.approx(right(small)) and top(big) == pytest.approx(top(small))
    row = {"caption": "1 行目|2 行目", "caption_style": "closing", "badge": "約 4 分 10 秒の待ち時間をスキップ"}
    v = C.scene_values(LOOK, row, C.caption_font(LOOK))
    assert v["caption"]["center"] == (0.5, 0.5) and v["caption"]["name"] == "Closing"
    # 結びのテロップの大きさは書き出される字の px: 96 px は Size 96 ÷ (1920 × 0.5) = 0.1
    assert v["caption"]["size"] == pytest.approx(LOOK["closing"]["size"] / (1920 * LOOK["fusion_text_scale"]))
    # 下のテロップの Size を上げても、結びのテロップの大きさは変わらない
    look2 = dict(LOOK, caption=dict(LOOK["caption"], fusion_size=0.0833))
    assert C.scene_values(look2, row, C.caption_font(LOOK))["caption"]["size"] == pytest.approx(v["caption"]["size"])
    it = Item()
    C.build_fusion(it, v)
    names = [t.Name for t in it.comp.tools]
    assert "ClosingText" in names and "ClosingBar" in names and "CaptionText" not in names
    assert "BadgeMask" in names
    assert it.comp["BadgeMask"].inputs["Width"] == pytest.approx(big["width"])


# ---------- 止める (hold) ----------
def test_hold_layout_and_values():
    """hold の分だけ後ろの行がずれる。止めた瞬間の Fusion の値は動かない (keys・ズーム・暗くなる途中をその瞬間で止める)。"""
    sb = dict(LOOK, rows=[{"start": 0, "end": 4, "seconds": 4, "hold": {"seconds": 3}}, {"start": 10, "end": 14, "seconds": 4}])
    assert C.row_spans(sb) == [(0, 120), (210, 120)]
    assert C.hold_source_frame(sb["rows"][0], 30, 120) == 119
    assert C.hold_source_frame({"start": 0, "end": 4, "hold": {"seconds": 1, "at": 2.5}}, 30, 120) == 75
    assert C.hold_time({"start": 0, "end": 4, "hold": {"seconds": 1, "at": 2}}, 30, 120) == 60
    # 倍速の行の at end は、その行で最後に映るフレーム (8 倍速、素材 0〜40 秒を 5 秒: 最後に映るのは 149 × 8 = 1192)
    fast = {"start": 0, "end": 40, "hold": {"seconds": 1}}
    assert C.hold_source_frame(fast, 30, 150, 8.0) == 1192 and C.hold_time(fast, 30, 150, 8.0) == 1192
    row = {"zoom": {"x": 0, "y": 0, "w": 960, "h": 540, "seconds": 4},
           "focus": {"x": 0, "y": 0, "w": 100, "h": 100, "dim": 0.5, "seconds": 4},
           "masks": [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10,
                      "keys": [{"t": 0, "x": 0, "y": 0, "w": 10, "h": 10}, {"t": 2, "x": 200, "y": 0, "w": 10, "h": 10}]}],
           "badge": "x", "caption": "テロップ"}
    V = C.scene_values(LOOK, row)
    HV = C.hold_values(V, 59, 60)   # 2 秒 (60 フレーム、等速) の行の最後 (合成の 59) で止める。暗くなる・寄るのは 4 秒かけて
    assert HV["badge"] is None and HV["caption"] == V["caption"]
    # 画に付く位置は止めた瞬間の位置 (keys の 59 フレーム目)
    assert HV["masks"][0]["keys"] == [] and HV["masks"][0]["center"][0] == pytest.approx((200 * 59 / 60 + 5) / 1920)
    # 現れる動きは行の頭からの時刻のまま止めた区間へ続く: 暗くなるのは半分から、残りの 60 フレームで 1 へ
    assert HV["focus"]["ramp"] == [(0, pytest.approx(0.5)), (60, 1.0)]
    assert HV["zoom"]["size"] == (pytest.approx(1.5), pytest.approx(2.0)) and HV["zoom"]["comp_frames"] == 60
    it = Item()
    C.build_fusion(it, HV)
    assert "Center" not in it.comp["Mask1"].links           # 止めたクリップのマスクは動かない
    assert it.comp["FocusShade"].links["TopLeftAlpha"].keys == {0: pytest.approx(0.25), 60: 0.5}
    assert C.shift_keys([(0, 0.0), (30, 1.0)], 45) == [(0, 1.0)]  # 行の中で終わった動きは最後の値のまま


def test_build_timeline_with_hold(tmp_path):
    """止めたクリップは、止めたいフレームで終わる範囲を hold の長さだけ置いて Percentage 0 にする。Fusion は止めた瞬間の値。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0]["hold"] = {"seconds": 2.5}
    sb["rows"][0]["masks"] = [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10}]
    rows = S.plan(sb)
    assert rows[0]["hold_rec"] == 150 and rows[0]["hold_frames"] == 75 and rows[1]["rec"] == 225
    mp = MediaPool()
    media = S.import_media(mp, rows, 30)
    _, items, holds, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    assert len(mp.calls) == 2 and len(mp.calls[0]) == 2 and len(mp.calls[1]) == 1   # 止めたクリップは 1 つずつ置く
    hold_info = mp.calls[1][0]
    assert (hold_info["startFrame"], hold_info["endFrame"], hold_info["recordFrame"]) == (150 - 75, 150, 108000 + 150)
    assert holds[0].set_speed == {"Percentage": 0.0, "RippleTimeline": False} and holds[1] is None
    assert res[0]["hold"]["match"] and res[0]["match"] and all(r["match"] for r in res)
    hold_items = [Item(), None]
    ok, fu, _ = S.add_fusion([Item(), Item()], sb, rows, media, None, hold_items)
    assert ok and "row1_hold" in fu and hold_items[0].comp is not None


def hold_build(tmp_path, hold_seconds=2.5, **timeline_attrs):
    """1 行目に hold (既定 2.5 秒)。模擬のタイムラインの属性・メソッドを差し替えてから組む。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0]["hold"] = {"seconds": hold_seconds}
    rows = S.plan(sb)
    mp = MediaPool()
    media = S.import_media(mp, rows, 30)
    real = mp.CreateEmptyTimeline

    def changed(name):
        tl = real(name)
        for key, value in timeline_attrs.items():
            setattr(tl, key, value)
        return tl
    mp.CreateEmptyTimeline = changed
    _, _, holds, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    return mp, holds, res


def test_hold_moves_playhead_after_clip(tmp_path):
    """再生ヘッドが置いたクリップの上に残っていても、止める前にクリップの直後 (hold_rec + hold の長さ) へ動かす。"""
    mp, holds, res = hold_build(tmp_path, stuck_append=True, stuck_set=0)
    h = res[0]["hold"]
    assert h["match"] and h["matched_at"] == 1 and h["playhead"] == 150 + 75 and mp.tl.deleted == []
    assert holds[0].GetSourceStartFrame() == 149


def test_hold_replaces_clip_when_playhead_does_not_move(tmp_path):
    """SetCurrentTimecode が 1 回効かず頭のフレームを映したら、消して置き直し、2 回目で合う。"""
    mp, holds, res = hold_build(tmp_path, stuck_append=True, stuck_set=1)
    h = res[0]["hold"]
    assert h["match"] and res[0]["match"] and h["matched_at"] == 2 and "error" not in h
    assert [t["playhead"] for t in h["tries"]] == [0, 225] and h["tries"][0]["src"] == 150 - 75
    assert len(mp.tl.deleted) == 1 and mp.tl.deleted[0][1] is False and len(mp.calls) == 3
    assert holds[0].GetSourceStartFrame() == 149


def test_hold_gives_up_after_three_tries(tmp_path):
    """3 回置いて、どれも再生ヘッドが動かなければ、置き直しをやめて match は False。"""
    mp, _, res = hold_build(tmp_path, stuck_append=True, stuck_set=3)
    h = res[0]["hold"]
    assert h["match"] is False and res[0]["match"] is False and "matched_at" not in h
    assert h["error"] == "3 回置いても期待のフレームを映さない"
    assert len(h["tries"]) == 3 and len(mp.tl.deleted) == 2 and len(mp.calls) == 4


def test_hold_accepts_neighbor_when_frame_cannot_freeze(tmp_path):
    """止めたいフレームを Resolve が止められず、2 回続けて同じ手前のフレームを映したら、それで止めて near に残す。"""
    mp, holds, res = hold_build(tmp_path, unfreezable={149})
    h = res[0]["hold"]
    assert h["match"] and res[0]["match"] and h["matched_at"] == 2 and "error" not in h
    assert h["near"] == {"want": 149, "got": 148} and [t["src"] for t in h["tries"]] == [148, 148]
    assert len(mp.tl.deleted) == 1 and holds[0].GetSourceStartFrame() == 148


def test_hold_does_not_accept_far_frame(tmp_path):
    """再生ヘッドが動かず頭のフレームを映し続けるのは、隣のフレームではないので受け入れない。"""
    _, _, res = hold_build(tmp_path, stuck_append=True, stuck_set=3)
    assert res[0]["hold"]["match"] is False and "near" not in res[0]["hold"]


def test_hold_short_range_stuck_playhead_is_not_near(tmp_path):
    """範囲が 2 フレームだと、再生ヘッドが動かないときの頭のフレームが手前の隣になる。それは受け入れない。"""
    _, _, res = hold_build(tmp_path, hold_seconds=2 / 30, stuck_append=True, stuck_set=3)
    h = res[0]["hold"]
    assert [t["src"] for t in h["tries"]] == [148, 148, 148]
    assert h["match"] is False and "near" not in h


def test_hold_near_ignores_failed_set_speed(tmp_path):
    """前回の止める操作が失敗した回は数えない。2 回続けて成功して同じ隣を映した 3 回目で受け入れる。"""
    _, _, res = hold_build(tmp_path, unfreezable={149}, speed_fail=1)
    h = res[0]["hold"]
    assert [t["set_speed"] for t in h["tries"]] == [False, True, True] and h["matched_at"] == 3
    assert h["near"] == {"want": 149, "got": 148}


@pytest.mark.parametrize("tc", [None, "", "x"], ids=["timecode-none", "timecode-empty", "timecode-invalid"])
def test_hold_unreadable_timecode_goes_on_to_the_source_frame(tmp_path, tc):
    """GetCurrentTimecode が読めなくても落ちずに止め、GetSourceStartFrame で照らす。記録に読めないことを残す。"""
    _, _, res = hold_build(tmp_path, GetCurrentTimecode=lambda: tc)
    h = res[0]["hold"]
    assert h["match"] and h["playhead"] is None and "読めない" in h["tries"][0]["playhead_error"]


def test_timecode_frames():
    assert C.timecode_to_frame("01:00:00:00", 30) == 108000
    assert C.frame_to_timecode(108000 + 225, 30) == "01:00:07:15"
    assert C.timecode_to_frame(C.frame_to_timecode(123456, 30), 30) == 123456
    assert C.timecode_to_frame("00:00:01;05", 30) == 35


def test_hold_problems():
    assert C.hold_problems({"seconds": 0})
    assert C.hold_problems({"seconds": 2, "at": "x"})
    assert C.hold_problems({"seconds": 2, "at": 50}, length=40)
    assert C.hold_problems({"seconds": 2, "at": "0:03"}, length=40) == []



# ---------- focus の枠線 ----------
def test_focus_border_ring():
    """枠線は、外の四角から反転した内の四角を Multiply で抜いた輪で色を切る。幅は書き出しの画面の画素。glow なら下にぼかし。"""
    row = {"focus": {"x": 800, "y": 300, "w": 600, "h": 400, "dim": 0.25, "seconds": 0.5,
                     "border": {"color": "#FFC800", "width": 6, "radius": 8, "glow": True}}}
    v = C.scene_values(LOOK, row)
    b = v["focus"]["border"]
    assert b["rgb"] == [255, 200, 0] and b["width_px"] == 6 and b["glow"]
    it = Item()
    C.build_fusion(it, v)
    comp = it.comp
    outer, inner = comp["FocusBorderOuter"], comp["FocusBorderInner"]
    assert inner.inputs["Invert"] == 1 and outer.EffectMask is inner and outer.inputs["PaintMode"] == "Multiply"
    assert outer.inputs["Width"] == pytest.approx((600 + 6) / 1920) and inner.inputs["Height"] == pytest.approx((400 - 6) / 1080)
    color = comp["FocusBorder"]
    assert color.EffectMask is outer and color.inputs["TopLeftAlpha"] == 1.0
    assert comp["FocusBorderOver"].links["Blend"].keys == {0: 0.0, 15: 1.0}   # 現れ方は Merge の Blend
    assert comp["FocusGlowOver"].links["Blend"].keys == {0: 0.0, 15: 1.0}
    assert comp["FocusBorderGlow"].Input is color
    # 黒塗り → 暗くする → glow → 枠線の順につながる
    assert chain(comp)[-3:] == ["Merge", "Merge", "Merge"]
    assert comp["FocusBorderOver"].Background is comp["FocusGlowOver"]
    # border: true は config の focus_border の値
    v2 = C.scene_values(LOOK, {"focus": {"x": 0, "y": 0, "w": 100, "h": 100, "border": True}})
    assert v2["focus"]["border"]["rgb"] == C.hex_rgb(LOOK["focus_border"]["color"])
    assert C.focus_problems({"x": 0, "y": 0, "w": 1, "h": 1, "border": {"color": "red"}})
    # 止めたクリップでも枠線は行の頭からの時刻のまま現れ続ける (1 秒の行、2 秒で現れる)
    hv = C.hold_values(C.scene_values(LOOK, {"focus": dict(row["focus"], seconds=2)}), 29, 30)
    it = Item()
    C.build_fusion(it, hv)
    assert it.comp["FocusBorderOver"].links["Blend"].keys == {0: pytest.approx(0.5), 30: 1.0}



# ---------- callout ----------
def test_callout_values_and_fusion():
    """4K の素材の (2600, 380, 360, 280) を、書き出しの (100, 600) に幅 540 px で重ねる (下調べで確認した値)。
    切り抜きはマスクを掛けた後の画から。影 → 縁 → 切り抜いた画の順で、同じ時刻で現れる。"""
    row = {"callout": {"src": {"x": 2600, "y": 380, "w": 360, "h": 280}, "dst": {"x": 100, "y": 600, "w": 540},
                       "start": 1, "in": 0.4},
           "masks": [{"type": "black", "x": 0, "y": 0, "w": 10, "h": 10}], "caption": "テロップ"}
    v = C.scene_values(dict(LOOK, safe_area={"aspect": "3:2"}), row, source_size=(3840, 2160))["callout"]
    assert len(v["pieces"]) == 1 and v["pieces"][0]["crop"] == (2600, 2160 - 380 - 280, 360, 280)
    assert v["size"] == pytest.approx(3.0) and v["dst"] == [100, 600, 540, pytest.approx(420)]
    assert v["pieces"][0]["center"] == pytest.approx(((100 + 270) / 1920, 1 - (600 + 210) / 1080))
    assert v["ramp_frames"] == [(0, 0.0), (30, 0.0), (42, 1.0)] and v["warning"] is not None   # x 100 はセーフエリア (3:2) の外
    assert v["border"]["line_px"] == LOOK["callout"]["border"]["width"] and v["shadow"]
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, row, source_size=(3840, 2160)))
    comp = it.comp
    crop, mg = comp["CalloutCrop"], comp["Callout"]
    assert crop.Input is comp["Mask1Over"] and mg.Foreground is crop
    assert mg.inputs["Size"] == pytest.approx(3.0) and mg.links["Blend"].keys == {0: 0.0, 30: 0.0, 42: 1.0}
    assert mg.Background is comp["CalloutBorderOver"]
    assert comp["CalloutBorderOver"].Background is comp["CalloutFillOver"]
    assert comp["CalloutFillOver"].Background is comp["CalloutShadowOver"]
    assert comp["CaptionBarOver"].Background is mg     # callout の後にテロップ
    assert C.callout_problems({"src": {"x": 0, "y": 0, "w": 0, "h": 1}, "dst": {"x": 0, "y": 0, "w": 10}})
    # 止めた区間に入っても現れる (1 秒の行、2 秒目から現れる callout)
    hv = C.hold_values(C.scene_values(LOOK, {"callout": dict(row["callout"], start=2)}, source_size=(3840, 2160)), 29, 30)
    assert hv["callout"]["ramp"] == [(0, 0.0), (30, 0.0), (42, 1.0)]


def test_callout_several_sources_stack():
    """src の list: 書いた順に上から重ね、左をそろえ、同じ倍率で拡大する。dst.w はいちばん幅の広い切り抜きの書き出しの幅。
    間は gap (書き出しの画素)。縁と影は全体を囲む。2 つ目からの部品は Callout2・CalloutCrop2 と番号付きで、順につなぐ。"""
    srcs = [{"x": 900, "y": 785, "w": 800, "h": 44}, {"x": 1700, "y": 785, "w": 400, "h": 44},
            {"x": 900, "y": 900, "w": 600, "h": 50}]
    row = {"callout": {"src": srcs, "dst": {"x": 200, "y": 300, "w": 1200}, "gap": 6, "start": 1, "in": 0.4}}
    v = C.scene_values(LOOK, row, source_size=(3840, 2160))["callout"]
    m = 1200 / 800                       # 素材の 1 画素が書き出しの 1.5 画素
    assert v["size"] == pytest.approx(m / 0.5)
    boxes = [p["dst"] for p in v["pieces"]]
    assert boxes[0] == pytest.approx([200, 300, 1200, 44 * m])
    assert boxes[1] == pytest.approx([200, 300 + 44 * m + 6, 400 * m, 44 * m])
    assert boxes[2] == pytest.approx([200, 300 + 2 * (44 * m + 6), 600 * m, 50 * m])
    assert v["dst"] == pytest.approx([200, 300, 1200, (44 + 44 + 50) * m + 12])
    assert v["pieces"][1]["crop"] == (1700, 2160 - 785 - 44, 400, 44)
    b = v["pieces"][2]["dst"]
    assert v["pieces"][2]["center"] == pytest.approx(((b[0] + b[2] / 2) / 1920, 1 - (b[1] + b[3] / 2) / 1080))
    bw = LOOK["callout"]["border"]["width"]
    assert v["border"]["height"] == pytest.approx((v["dst"][3] + 2 * bw) / 1080)
    # gap を書かなければ config の callout.gap
    v0 = C.scene_values(LOOK, {"callout": {k: x for k, x in row["callout"].items() if k != "gap"}},
                        source_size=(3840, 2160))["callout"]
    assert v0["pieces"][1]["dst"][1] == pytest.approx(300 + 44 * m + LOOK["callout"]["gap"])
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, row, source_size=(3840, 2160)))
    comp = it.comp
    mgs = [comp[n] for n in ("Callout", "Callout2", "Callout3")]
    assert [mg.Foreground for mg in mgs] == [comp[n] for n in ("CalloutCrop", "CalloutCrop2", "CalloutCrop3")]
    assert mgs[1].Background is mgs[0] and mgs[2].Background is mgs[1]
    assert all(mg.inputs["Size"] == pytest.approx(3.0) for mg in mgs)
    assert all(mg.links["Blend"].keys == {0: 0.0, 30: 0.0, 42: 1.0} for mg in mgs)
    assert comp["CalloutCrop2"].inputs["XOffset"] == 1700
    # 書式の誤り: 空の list・中に w が 0 の四角・gap が負
    for bad in ([], [srcs[0], {"x": 0, "y": 0, "w": 0, "h": 1}], "x"):
        assert C.callout_problems({"src": bad, "dst": {"x": 0, "y": 0, "w": 10}})
    assert C.callout_problems({"src": srcs, "dst": {"x": 0, "y": 0, "w": 10}, "gap": -1})
    assert C.callout_problems({"src": srcs, "dst": {"x": 0, "y": 0, "w": 10}}) == []


def test_callout_fill_and_border_line():
    """全体の四角を fill (既定は黒、不透明度 0.7) で埋め、縁は四角の外側に沿う幅 3 px (書き出しの画素) の白い線。
    埋める色の不透明度は Merge の Blend に掛ける。縁は外の四角から反転した内の四角を Multiply で抜いた輪。
    構成表の fill と border で上書きでき、false で消せる。書式の誤りは点検で見つかる。"""
    assert LOOK["callout"]["fill"] == {"color": "#000000", "opacity": 0.7}
    assert LOOK["callout"]["border"] == {"color": "#FFFFFF", "width": 3}
    co = {"src": {"x": 2600, "y": 380, "w": 360, "h": 280}, "dst": {"x": 300, "y": 300, "w": 540}, "start": 1, "in": 0.4}
    v = C.scene_values(LOOK, {"callout": co}, source_size=(3840, 2160))["callout"]
    dst = v["dst"]
    assert v["fill"]["rgb"] == [0, 0, 0] and v["fill"]["opacity"] == pytest.approx(0.7)
    assert v["fill"]["width"] == pytest.approx(dst[2] / 1920) and v["fill"]["height"] == pytest.approx(dst[3] / 1080)
    assert v["border"]["rgb"] == [255, 255, 255] and v["border"]["line_px"] == 3
    assert v["border"]["width"] == pytest.approx((dst[2] + 6) / 1920)
    assert v["border"]["inner"]["width"] == pytest.approx(dst[2] / 1920)
    assert v["border"]["inner"]["center"] == pytest.approx(v["border"]["center"])
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, {"callout": co}, source_size=(3840, 2160)))
    comp = it.comp
    fill_over = comp["CalloutFillOver"]
    assert fill_over.links["Blend"].keys == {0: 0.0, 30: 0.0, 42: pytest.approx(0.7)}
    assert comp["CalloutFill"].inputs["TopLeftAlpha"] == 1.0
    outer, inner = comp["CalloutBorderOuter"], comp["CalloutBorderInner"]
    assert comp["CalloutBorder"].EffectMask is outer and outer.EffectMask is inner
    assert inner.inputs["Invert"] == 1 and outer.inputs["PaintMode"] == "Multiply"
    assert comp["CalloutBorderOver"].links["Blend"].keys == {0: 0.0, 30: 0.0, 42: 1.0}
    # 構成表で上書きする・消す
    v = C.scene_values(LOOK, {"callout": dict(co, fill={"opacity": 0.5}, border={"width": 5})},
                       source_size=(3840, 2160))["callout"]
    assert v["fill"]["opacity"] == 0.5 and v["fill"]["rgb"] == [0, 0, 0] and v["border"]["line_px"] == 5
    v = C.scene_values(LOOK, {"callout": dict(co, fill=False, border=False)}, source_size=(3840, 2160))["callout"]
    assert v["fill"] is None and v["border"] is None
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, {"callout": dict(co, fill=False, border=False)}, source_size=(3840, 2160)))
    assert comp["Callout"] and not any(t.Name in ("CalloutFillOver", "CalloutBorderOver") for t in it.comp.tools)
    for bad in ({"opacity": 1.5}, {"color": "black"}, "x"):
        assert C.callout_problems(dict(co, fill=bad))
    assert C.callout_problems(dict(co, fill={"color": "#000000", "opacity": 0.7})) == []


def test_focus_start():
    """focus の start: 暗くなるのと枠線は start から seconds をかけて出る。止めたクリップでも行の頭からの時刻のまま続く。"""
    fo = {"x": 0, "y": 0, "w": 100, "h": 100, "dim": 0.5, "start": 2, "seconds": 1, "border": True}
    v = C.scene_values(LOOK, {"focus": fo}, speed=2.0)["focus"]
    assert v["ramp_frames"] == [(0, 0.0), (60, 0.0), (90, 1.0)] and v["ramp"] == [(0, 0.0), (120, 0.0), (180, 1.0)]
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, {"focus": fo}))
    assert it.comp["FocusShade"].links["TopLeftAlpha"].keys == {0: 0.0, 60: 0.0, 90: pytest.approx(0.5)}
    assert it.comp["FocusBorderOver"].links["Blend"].keys == {0: 0.0, 60: 0.0, 90: 1.0}
    # 1 秒 (30 フレーム) の行の後ろに止めた区間: 止めたクリップの合成の 30 で暗くなり始め、60 で暗くなり終わる
    hv = C.hold_values(C.scene_values(LOOK, {"focus": fo}), 29, 30)
    assert hv["focus"]["ramp"] == [(0, 0.0), (30, 0.0), (60, 1.0)]
    it = Item()
    C.build_fusion(it, hv)
    assert it.comp["FocusBorderOver"].links["Blend"].keys == {0: 0.0, 30: 0.0, 60: 1.0}
    # start を書かなければ config の focus.start (0) で、行の頭から暗くなる
    no_start = {k: x for k, x in fo.items() if k != "start"}
    assert C.scene_values(LOOK, {"focus": no_start})["focus"]["ramp_frames"] == [(0, 0.0), (30, 1.0)]
    assert C.focus_problems(dict(fo, start=-1))


def test_appear_time_checked_against_row_length(tmp_path):
    """--dry-run の点検: start が行の長さ (止めた区間を含む) 以降なら誤り、現れ終わりが行の後ろなら警告。"""
    sb = sb_rows(2, tmp_path)   # 1 行 5 秒
    box = {"x": 0, "y": 0, "w": 100, "h": 100}
    sb["rows"][0]["focus"] = dict(box, start=5, seconds=1)
    sb["rows"][1]["callout"] = {"src": box, "dst": {"x": 300, "y": 300, "w": 200}, "start": 6, "in": 0.4}
    sb["rows"][1]["hold"] = {"seconds": 2}
    rows = S.plan(sb)
    bad = S.problems(sb, rows, {})
    assert any("1 行目の focus: start" in b for b in bad)
    assert not any("2 行目の callout" in b for b in bad)          # 止めた区間の中で現れ始めるのはよい
    sb["rows"][0]["focus"] = dict(box, start=4.5, seconds=1)
    rows = S.plan(sb)
    assert not any("focus" in b for b in S.problems(sb, rows, {}))
    _, warns, _ = S.row_values(sb, rows, {sb["source"]: (1920, 1080)}, None)
    assert any("1 行目の focus: 現れ終わる" in w for w in warns)
    assert not any("2 行目の callout: 現れ終わる" in w for w in warns)


def test_several_holds_each_placed_alone(tmp_path):
    """止めたクリップが 2 つ以上でも、どれも止めたいフレームを映す (1 つずつ置いてすぐ止める)。
    映すフレームが違えば (まとめて置いたときの形)、その行の match が False になる。"""
    sb = sb_rows(3, tmp_path)
    for r in sb["rows"]:
        r["hold"] = {"seconds": 1}
    sb["rows"][1]["hold"]["at"] = sb["rows"][1]["start"] + 2
    rows = S.plan(sb)
    mp = MediaPool()
    media = S.import_media(mp, rows, 30)
    _, items, holds, res = S.build_timeline(Proj(), mp, sb, rows, media, "t")
    assert [len(c) for c in mp.calls] == [3, 1, 1, 1]
    assert all(r["hold"]["match"] and r["hold"]["result"]["src"] == r["hold"]["want"]["src"] for r in res)
    assert all(h.set_speed == {"Percentage": 0.0, "RippleTimeline": False} for h in holds)
    # まとめて置いたときと同じく範囲の頭を映す Resolve なら、食い違いとして報告する
    TItem.GetSourceStartFrame, orig = (lambda self: self.info["startFrame"]), TItem.GetSourceStartFrame
    try:
        mp = MediaPool()
        _, _, _, res = S.build_timeline(Proj(), mp, sb, rows, S.import_media(mp, rows, 30), "t")
    finally:
        TItem.GetSourceStartFrame = orig
    assert not any(r["hold"]["match"] or r["match"] for r in res)



# ---------- 既存のプロジェクトに組む・書き出し先・NG を無視した書き出し ----------
class NamedTL:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return 108000


class FolderPool(MediaPool):
    """今のフォルダーを持つメディアプール。作ったタイムラインは proj に追加する。"""
    def __init__(self, proj):
        super().__init__()
        self.proj, self.folder, self.created = proj, "sub", []

    def GetRootFolder(self):
        return "root"

    def GetCurrentFolder(self):
        return self.folder

    def SetCurrentFolder(self, f):
        self.folder = f
        return True

    def ImportMedia(self, paths):
        assert self.folder == "root"            # 取り込みはいちばん上のフォルダー
        return super().ImportMedia(paths)

    def CreateEmptyTimeline(self, name):
        assert self.folder == "root"
        if name in [t.name for t in self.proj.tls]:
            return None                          # 21.1.1 の実機: 同じ名前は作れない
        self.created.append(name)
        self.proj.tls.append(NamedTL(name))
        return self.proj.tls[-1]


class RunProj(Proj):
    def __init__(self, name, settings=None, timelines=()):
        self.name, self.tls, self.set_calls = name, [NamedTL(n) for n in timelines], []
        self.stuck = ("timelinePlaybackFrameRate", "videoMonitorFormat")
        self.v = {"timelineFrameRate": 30.0, "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080",
                  "timelinePlaybackFrameRate": "30", "videoMonitorFormat": "HD 1080p 30"} | (settings or {})
        self.mp = FolderPool(self)

    def GetName(self):
        return self.name

    def GetSetting(self, k):
        return self.v.get(k)

    def SetSettings(self, d):
        self.set_calls.append(d)
        if any(k in self.stuck for k in d):
            return False                         # 21.1.1 の実機: 再生フレームレートは API で変わらない
        self.v.update(d)
        return True

    def GetMediaPool(self):
        return self.mp

    def GetTimelineCount(self):
        return len(self.tls)

    def GetTimelineByIndex(self, i):
        return self.tls[i - 1]


class RunPM:
    def __init__(self, projects, current=None):
        self.projects, self.current, self.created, self.saved = projects, current, [], 0

    def GetCurrentProject(self):
        return self.projects.get(self.current)

    def GetProjectListInCurrentFolder(self):
        return list(self.projects)

    def LoadProject(self, n):
        self.current = n
        return self.projects.get(n)

    def CreateProject(self, n):
        self.projects[n] = RunProj(n, {"timelinePlaybackFrameRate": "24", "videoMonitorFormat": "HD 1080p 24"})
        self.created.append(n)
        self.current = n
        return self.projects[n]

    def SaveProject(self):
        self.saved += 1
        return True


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Resolve を模擬に置き換えた run の環境。Fusion とマーカーは別のテストで見るので素通しにする。"""
    # --render-anyway の既定の書き出し先が、利用者の出力先と仮組みのフォルダーにならないようにする
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("RESOLVE_PREVIEW_DIR", str(tmp_path / "previews"))
    patch_common(monkeypatch, "_CFG", None)
    sb = sb_rows(2, tmp_path)
    path = tmp_path / "sample.json"
    C.write_json(str(path), {k: sb[k] for k in ("source", "rows", "fps")} | {"output": str(tmp_path / "sample.mp4")})
    pm = RunPM({"test-gui30": RunProj("test-gui30", timelines=["sample"]), "test-open": RunProj("test-open")},
               current="test-open")
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: type("R", (), {"GetProjectManager": lambda self: pm})())
    monkeypatch.setattr(S, "probe_sources", lambda rows: {sb["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                          "duration": 9999.0}})
    monkeypatch.setattr(S, "add_fusion", lambda *a, **k: (True, {}, []))
    monkeypatch.setattr(S, "add_markers", lambda *a, **k: {})
    patch_common(monkeypatch, "caption_font", lambda look: None)
    renders = []

    def fake_render(proj, sb, d, n, limit, marks=None):
        # 仮組みは一時ファイルを link で置くので、模擬もそのパスにバイトを書く
        path = os.path.join(d, n + ".mp4")
        os.makedirs(d, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"rendered-by-mock")
        renders.append((d, n))
        return {"done": True, "status": "Complete", "path": path}

    monkeypatch.setattr(S, "render", fake_render)

    def go(**kw):
        import argparse
        a = dict(storyboard=str(path), source=None, name=None, project=None, timeline=None, output=None, render=False,
                 render_anyway=False, dry_run=False) | kw
        log = {}
        ok, summary, lines = S.run(argparse.Namespace(**a), log)
        return ok, summary, lines, log
    return pm, go, renders, tmp_path


def test_existing_project_new_timeline(run_env):
    """--project: 既存のプロジェクトを開き、設定は変えず、新しいタイムラインとして組む。同じ名前があれば番号を追加する。
    取り込みとタイムラインはいちばん上のフォルダーに入れ、終わったら今のフォルダーを元に戻す。"""
    pm, go, _, _ = run_env
    ok, summary, lines, log = go(project="test-gui30")
    proj = pm.projects["test-gui30"]
    assert ok, (summary, lines)
    assert pm.created == [] and proj.set_calls == []                   # プロジェクトを作らず、設定も変えない
    assert proj.mp.created == ["sample-2"] and log["timeline_name"] == "sample-2"
    assert log["mode"] == "existing" and log["previous_project"] == "test-open"
    assert proj.mp.folder == "sub" and pm.saved >= 1
    assert "プロジェクト test-gui30、タイムライン sample-2" in summary and log["settings"]["warnings"] == []
    ok, summary, _, log = go(project="test-gui30")                     # もう 1 回組むと -3
    assert ok and log["timeline_name"] == "sample-3"
    ok, summary, _, log = go(project="test-gui30", timeline="仮組み")   # --timeline の名前はそのまま
    assert ok and log["timeline_name"] == "仮組み" and proj.mp.created[-1] == "仮組み"


def test_existing_project_refuses_without_changes(run_env):
    """--timeline の名前が既にある・プロジェクトが無い・フレームレートか解像度が違うときは、何も追加せずに止める。
    再生フレームレートが違うだけなら、新しいプロジェクトのときと同じ警告を出して組む。"""
    pm, go, _, _ = run_env
    proj = pm.projects["test-gui30"]
    ok, summary, _, _ = go(project="test-gui30", timeline="sample")
    assert not ok and "タイムライン sample は既にある" in summary and proj.mp.imports == []
    ok, summary, _, _ = go(project="test-none")
    assert not ok and "test-none が無い" in summary
    proj.v["timelineFrameRate"] = 24.0
    ok, summary, lines, _ = go(project="test-gui30")
    assert not ok and "設定が構成表と違う" in summary and any("timelineFrameRate" in x and "24" in x for x in lines)
    proj.v.update(timelineFrameRate=30.0, timelineResolutionWidth="3840", timelineResolutionHeight="2160")
    ok, _, lines, _ = go(project="test-gui30")
    assert not ok and len(lines) == 2
    assert proj.mp.imports == [] and proj.mp.created == [] and proj.set_calls == []
    proj.v.update(timelineResolutionWidth="1920", timelineResolutionHeight="1080",
                  timelinePlaybackFrameRate="24", videoMonitorFormat="HD 1080p 24")
    ok, summary, lines, log = go(project="test-gui30")
    assert ok and any("再生フレームレート" in x and "マスター設定" in x for x in lines) and proj.set_calls == []


def test_new_project_with_timeline_name(run_env):
    """--project が無ければ今と同じく新しいプロジェクトを作る。タイムラインの名前は --timeline、無ければプロジェクトの名前。"""
    pm, go, _, _ = run_env
    ok, _, lines, log = go(name="test-new", timeline="tl-a")
    assert pm.created == ["test-new"] and pm.projects["test-new"].mp.created == ["tl-a"] and log["mode"] == "new"
    assert any("再生フレームレート" in x for x in lines)                   # 新しいプロジェクトは 24 のまま
    go(name="test-new2")
    assert pm.projects["test-new2"].mp.created == ["test-new2"]


def test_output_overrides_storyboard(run_env):
    """--output は構成表の output より優先する。"""
    pm, go, renders, tmp_path = run_env
    out = tmp_path / "renders" / "sample-v5.mp4"
    ok, summary, _, log = go(project="test-gui30", output=str(out), render=True)
    assert ok and len(renders) == 1 and log["output"] == str(out)
    assert os.path.dirname(renders[0][0]) == str(tmp_path / "renders") and renders[0][1] == "sample-v5"
    assert "書き出し " + str(out) in summary and log["render"]["ignored_ng"] == []


def test_render_anyway_records_ignored_ng(run_env, monkeypatch):
    """NG の行があれば --render は書き出さず、--render-anyway は書き出して、無視した NG を記録と要約に残す。"""
    pm, go, renders, tmp_path = run_env
    real = S.build_timeline

    def broken(*a, **k):
        tl, items, holds, res = real(*a, **k)
        res[1]["match"] = False
        return tl, items, holds, res
    monkeypatch.setattr(S, "build_timeline", broken)
    ok, summary, lines, log = go(project="test-gui30", render=True)
    assert not ok and renders == [] and "render" not in log and any("--render-anyway" in x for x in lines)
    ok, summary, lines, log = go(project="test-gui30", render_anyway=True)
    assert not ok and len(renders) == 1
    preview_root = os.path.normpath(str(tmp_path / "previews"))
    asked = os.path.normpath(renders[0][0])
    assert asked == preview_root or asked.startswith(preview_root + os.sep)
    assert len(log["render"]["ignored_ng"]) == 1 and log["render"]["ignored_ng"][0].startswith("NG 2 行目")
    assert "NG 1 件を無視して書き出した" in summary


def test_project_and_name_are_exclusive(monkeypatch):
    """--name (新しいプロジェクト) と --project (既存のプロジェクト) は一緒に使えない。"""
    monkeypatch.setattr("sys.argv", ["sb2resolve.py", "x.json", "--name", "a", "--project", "b"])
    with pytest.raises(SystemExit):
        S.main()


def test_callout_shadow_only_outside_the_box():
    """影は四角の外側だけに置く: 影の Merge (CalloutShadowOver) の EffectMask に、全体の四角 (fill と同じ) を反転したマスク
    (CalloutShadowInner) が付く。四角の内側に影が掛かると、fill と重なって下の画が暗くなりすぎる (fill が無いと暗い斑になる)。"""
    co = {"src": {"x": 2600, "y": 380, "w": 360, "h": 280}, "dst": {"x": 300, "y": 300, "w": 540}, "start": 1, "in": 0.4}
    for c in (co, dict(co, fill=False)):
        v = C.scene_values(LOOK, {"callout": c}, source_size=(3840, 2160))["callout"]
        dst = v["dst"]
        inner = v["shadow"]["inner"]
        assert inner["width"] == pytest.approx(dst[2] / 1920) and inner["height"] == pytest.approx(dst[3] / 1080)
        assert inner["center"] == pytest.approx(((dst[0] + dst[2] / 2) / 1920, 1 - (dst[1] + dst[3] / 2) / 1080))
        it = Item()
        C.build_fusion(it, C.scene_values(LOOK, {"callout": c}, source_size=(3840, 2160)))
        over, cut = it.comp["CalloutShadowOver"], it.comp["CalloutShadowInner"]
        assert over.EffectMask is cut and cut.ID == "RectangleMask" and cut.inputs["Invert"] == 1
        assert cut.inputs["Width"] == pytest.approx(inner["width"]) and cut.inputs["Height"] == pytest.approx(inner["height"])
        assert cut.inputs["Center"] == {1: pytest.approx(inner["center"][0]), 2: pytest.approx(inner["center"][1])}
        # 影そのもの (ずらした四角のぼかし) は前と同じ
        assert it.comp["CalloutShadow"].EffectMask is it.comp["CalloutShadowMask"]


# ---------- 枠線の色 auto (背景の明るさで決める) ----------
AUTO = LOOK["focus_border"]["auto"]


def _auto_row(color="auto", **focus):
    """素材の 10〜20 秒を 5 秒に (2 倍速) する行。focus の範囲は素材 (4K) の (1100, 1100, 800, 200)。"""
    return {"start": 10, "end": 20, "seconds": 5,
            "focus": dict({"x": 1100, "y": 1100, "w": 800, "h": 200, "border": {"color": color}}, **focus)}


def _plain(value, h=2160, w=3840):
    import numpy as np
    return np.full((h, w, 3), value, np.uint8)


def _bright_box():
    """黒い 4K の画の、(1000, 1000)〜(2000, 1400) だけ白い (focus の範囲 (1100, 1100, 800, 200) のまわりも白い)。"""
    img = _plain(0)
    img[1000:1400, 1000:2000] = 255
    return img


def test_auto_border_dark_light_and_near():
    """黒い背景は dark の色 (#FFC800)、白い背景は light の色 (#FF3B30)。しきい値に近いと警告して色を書くよう勧める。"""
    assert (AUTO["dark"], AUTO["light"]) == ("#FFC800", "#FF3B30")
    dark = C.auto_border(LOOK, _auto_row(), "x", 150, 2.0, reader=lambda p, n: _plain(0), sfps=30)
    light = C.auto_border(LOOK, _auto_row(), "x", 150, 2.0, reader=lambda p, n: _plain(255), sfps=30)
    assert (dark["color"], dark["side"], dark["luma"]) == ("#FFC800", "dark", 0.0) and "warning" not in dark
    assert (light["color"], light["side"], light["luma"]) == ("#FF3B30", "light", 255.0) and "warning" not in light
    th, near = AUTO["threshold"], AUTO["near"]

    def at(v):
        return C.auto_border(LOOK, _auto_row(), "x", 150, 2.0, reader=lambda p, n: _plain(v), sfps=30)
    hi, lo = at(th + near - 2), at(th - near + 2)
    assert hi["color"] == AUTO["light"] and hi["near"] and "border.color に色を書く" in hi["warning"]
    assert lo["color"] == AUTO["dark"] and lo["near"] and "warning" in lo
    assert "warning" not in at(th + near + 1) and "warning" not in at(th - near - 1)
    # 色を書いた枠線と、枠線の無い focus は決めない (素材を読まない)
    assert C.auto_border(LOOK, _auto_row("#00FF00"), "x", 150, reader=lambda p, n: pytest.fail("読んだ")) is None
    assert C.auto_border(LOOK, {"focus": {"x": 0, "y": 0, "w": 9, "h": 9}}, "x", 150) is None
    # 構成表の全体の focus_border.color を auto にすると、border: true の行も決める
    look = dict(LOOK, focus_border=dict(LOOK["focus_border"], color="auto"))
    row = dict(_auto_row(), focus=dict(_auto_row()["focus"], border=True))
    row2, info = C.with_auto_border(look, row, "x", 150, 1.0, reader=lambda p, n: _plain(255), sfps=30)
    assert row2["focus"]["border"] == {"color": "#FF3B30"} and info["color"] == "#FF3B30"
    assert row["focus"]["border"] is True   # 元の行は書き換えない


def test_auto_border_looks_at_ring_in_source_pixels_and_the_right_frame():
    """見るのは素材の画素の範囲の辺のまわり (内側 inside・外側 outside)。見る素材のフレームは枠線が現れ始めるフレーム:
    行の中なら行の頭の素材のフレーム + comp_frame(f, 速さ)、止めた区間ならその止めたフレーム。"""
    img = _bright_box()
    got = C.auto_border(LOOK, _auto_row(), "x", 150, 1.0, reader=lambda p, n: img, sfps=30)
    assert got["color"] == AUTO["light"] and got["box"] == {"x": 1100.0, "y": 1100.0, "w": 800.0, "h": 200.0}
    # 同じ範囲をタイムライン (1920x1080) の画素で書いたとみなして読むと、黒い所になる (素材の画素で見ていることの確認)
    half = C.auto_border(LOOK, _auto_row(x=550, y=550, w=400, h=100), "x", 150, 1.0, reader=lambda p, n: img, sfps=30)
    assert half["color"] == AUTO["dark"]
    # 帯: 白い四角にぴったりの範囲なら、内側 24 画素の帯は白、外側 24 画素の帯は黒
    ring = C.ring_luma(img, {"x": 1000, "y": 1000, "w": 1000, "h": 400}, 24, 24)
    white = 1000 * 400 - 952 * 352
    assert ring["pixels"] == 1048 * 448 - 952 * 352 and ring["mean"] == pytest.approx(255 * white / ring["pixels"])
    assert C.ring_luma(img, {"x": 5000, "y": 0, "w": 10, "h": 10}, 24, 24)["pixels"] == 0
    seen = []

    def reader(path, n):
        seen.append(n)
        return img
    # 2 倍速の行 (素材の 10 秒から)、focus.start 1 秒 → 素材の 300 + comp_frame(30, 2) = 360
    C.auto_border(LOOK, _auto_row(start=1), "x", 150, 2.0, reader=reader, sfps=30)
    # 行 (5 秒) の後ろの止めた区間 (focus.start 6 秒) → 止めたフレーム 300 + comp_frame(149, 2) = 598
    r = C.auto_border(LOOK, dict(_auto_row(start=6), hold={"seconds": 3}), "x", 150, 2.0, reader=reader, sfps=30)
    assert seen == [360, 598] and r["source_frame"] == 598 and r["appear_frame"] == 180
    # keys で動く範囲は、現れ始めるフレームの位置で見る (0 秒の (0, 0) から 2 秒の (1100, 1100) へ。1 秒は半分)
    moving = _auto_row(start=1, keys=[{"t": 0, "x": 0, "y": 0, "w": 800, "h": 200},
                                      {"t": 2, "x": 1100, "y": 1100, "w": 800, "h": 200}])
    assert C.auto_border(LOOK, moving, "x", 150, 1.0, reader=reader, sfps=30)["box"]["x"] == pytest.approx(550)
    # 読めない素材は error (色は決めない)
    bad = C.auto_border(LOOK, _auto_row(), "x", 150, 1.0, reader=lambda p, n: None, sfps=30)
    assert bad["color"] is None and "決められない" in bad["error"]
    assert "素材が無い" in C.auto_border(LOOK, _auto_row(), "no-such-file.mp4", 150)["error"]


def test_auto_border_moving_box_at_fractional_speed_matches_the_drawn_border():
    """割り切れない倍速の行で keys が動くとき、色を測る範囲は描く枠線と同じ位置 (合成の時刻で補間した位置)。
    1.5 倍速、keys は 0 と 2 フレーム目 (合成の 0 と 3)、枠線は 1 フレーム目 (合成の 1) に現れる → 移動の 1/3。"""
    row = _auto_row(start=1 / 30, keys=[{"t": 0, "x": 0, "y": 1100, "w": 800, "h": 200},
                                        {"t": 2 / 30, "x": 300, "y": 1100, "w": 800, "h": 200}])
    row2, info = C.with_auto_border(LOOK, row, "x", 150, 1.5, reader=lambda p, n: _plain(0), sfps=30)
    assert info["appear_frame"] == 1 and info["box"]["x"] == pytest.approx(100)
    # 描く側: scene_values の keys (合成の時刻) を、書き出しの 1 フレーム目の合成の時刻で補間した中心
    V = C.scene_values(LOOK, row2, source_size=(3840, 2160), speed=1.5)
    drawn = C.keys_at(V["focus"]["keys"], C.comp_frame(info["appear_frame"], 1.5))
    W, k = LOOK["timeline"]["width"], LOOK["timeline"]["width"] / 3840
    assert drawn["center"][0] == pytest.approx((info["box"]["x"] + info["box"]["w"] / 2) * k / W)


def test_auto_border_keys_on_the_same_frame_use_the_later_one_like_the_drawn_border():
    """30 fps で t=0 と t=0.01 の keys は同じフレーム 0 に丸まる。描く側 (scene_values) は後の keys を使うので、
    色を測る範囲も後の keys の位置 (x 3000) で、描く枠線の位置と同じ。枠線は行の頭 (0 フレーム目) に現れる。"""
    keys = [{"t": 0, "x": 0, "y": 1100, "w": 800, "h": 200}, {"t": 0.01, "x": 3000, "y": 1100, "w": 800, "h": 200},
            {"t": 2 / 30, "x": 600, "y": 1100, "w": 800, "h": 200}]
    assert C.keys_by_frame(keys, 30) == [(0, keys[1]), (2, keys[2])]
    row = _auto_row(start=0, keys=keys)
    row2, info = C.with_auto_border(LOOK, row, "x", 150, 1.0, reader=lambda p, n: _plain(0), sfps=30)
    assert info["appear_frame"] == 0 and info["box"]["x"] == pytest.approx(3000)
    V = C.scene_values(LOOK, row2, source_size=(3840, 2160), speed=1.0)
    drawn = C.keys_at(V["focus"]["keys"], C.comp_frame(info["appear_frame"], 1.0))
    W, k = LOOK["timeline"]["width"], LOOK["timeline"]["width"] / 3840
    assert drawn["center"][0] == pytest.approx((info["box"]["x"] + info["box"]["w"] / 2) * k / W)


def test_auto_border_format_and_unresolved_auto():
    """border.color は #RRGGBB か auto。素材を読まずに auto のまま scene_values に渡すと止める (黙って色を選ばない)。"""
    assert C.focus_problems(_auto_row()["focus"]) == []
    assert C.focus_problems(_auto_row("AUTO")["focus"])
    with pytest.raises(ValueError, match="auto"):
        C.scene_values(LOOK, _auto_row())


def test_row_values_decides_border_color_and_logs_it(tmp_path):
    """組む側 (row_values) は素材を読んで色を決め、Fusion の FocusBorder にその色を入れる。決めた色と明るさは focus.border.auto と
    記録の auto に残る。しきい値に近ければ警告、読めなければ誤り。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0]["focus"] = {"x": 100, "y": 100, "w": 400, "h": 100, "border": {"color": "auto"}}
    sb["rows"][1]["focus"] = {"x": 100, "y": 100, "w": 400, "h": 100, "border": {"color": "auto", "width": 4}}
    rows = S.plan(sb)
    frames = {round(sb["rows"][0]["start"] * 30): 255, round(sb["rows"][1]["start"] * 30): AUTO["threshold"] + 3}
    vals, warns, errs = S.row_values(sb, rows, {sb["source"]: (1920, 1080)}, None,
                                     reader=lambda p, n: _plain(frames[n], 1080, 1920))
    near = [w for w in warns if "しきい値" in w]
    assert errs == [] and len(near) == 1 and near[0].startswith("2 行目")
    assert vals[0]["focus"]["border"]["rgb"] == C.hex_rgb(AUTO["light"])
    assert vals[1]["focus"]["border"]["rgb"] == C.hex_rgb(AUTO["light"]) and vals[1]["focus"]["border"]["width_px"] == 4
    auto = S.auto_values(rows, vals)
    assert [a["row"] for a in auto] == [1, 2] and auto[0]["focus_border"]["luma"] == 255.0
    it = Item()
    C.build_fusion(it, vals[0])
    color = it.comp["FocusBorder"].inputs
    assert [round(color[k] * 255) for k in ("TopLeftRed", "TopLeftGreen", "TopLeftBlue")] == C.hex_rgb(AUTO["light"])
    _, _, errs = S.row_values(sb, rows, {sb["source"]: (1920, 1080)}, None, reader=lambda p, n: None)
    assert len(errs) == 2 and all("決められない" in e for e in errs)


def _ffmpeg_exe():
    try:
        return C.Config().tool("ffmpeg")
    except SystemExit:
        return None


@pytest.mark.skipif(_ffmpeg_exe() is None, reason="FFmpeg が無い")
def test_dry_run_decides_auto_color_from_synthetic_source(monkeypatch, offline_work):
    """--dry-run (Resolve に触らない) でも、合成の素材 (1280x720 の黒に白い四角) の画素から色が決まり、記録の auto に残る。
    範囲は素材の画素で書く (タイムラインの画素とみなした所は黒い)。callout の order と自動の置き場所も記録に残る。"""
    import argparse
    import subprocess
    work = os.path.join(offline_work, "auto_border")
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "src.mp4")
    subprocess.run([_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=1280x720:r=30:d=3", "-vf", "drawbox=x=600:y=300:w=300:h=200:color=white:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", src], check=True)
    sb = {"source": src, "output": os.path.join(work, "o.mp4"), "fps": 30, "seconds_per_row": 1,
          "rows": [{"start": 0, "end": 1, "caption": "白い所",
                    "focus": {"x": 620, "y": 320, "w": 260, "h": 160, "border": {"color": "auto"}},
                    "callout": {"src": [{"x": 620, "y": 320, "w": 200, "h": 40}, {"x": 620, "y": 400, "w": 100, "h": 40}],
                                "order": [2, 1]}},
                   {"start": 1, "end": 2, "focus": {"x": 100, "y": 100, "w": 200, "h": 100, "border": {"color": "auto"}}}]}
    path = os.path.join(work, "sb.json")
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: pytest.fail("接続した"))

    def dry():
        C.write_json(path, sb)
        log = {}
        ok, _, lines = S.run(argparse.Namespace(storyboard=path, source=None, name=None, render=False, dry_run=True), log)
        assert ok, lines
        return {a["row"]: a for a in log["auto"]}
    auto = dry()
    assert auto[1]["focus_border"]["color"] == AUTO["light"] and auto[2]["focus_border"]["color"] == AUTO["dark"]
    assert auto[1]["focus_border"]["source_frame"] == 0 and auto[2]["focus_border"]["source_frame"] == 30
    assert auto[1]["callout_order"] == [2, 1] and auto[1]["callout_dst"]["w"] == 1200   # 200 px x 1.5 x 4 倍
    # 同じ範囲をタイムライン (1920x1080) の画素で書いた値 (1.5 倍) は、素材では白い四角の外 (黒い所)
    sb["rows"][0]["focus"].update(x=930, y=480, w=390, h=240)
    assert dry()[1]["focus_border"]["color"] == AUTO["dark"]


# ---------- callout の order と自動の置き場所 ----------
SRC2 = [{"x": 900, "y": 785, "w": 800, "h": 44}, {"x": 900, "y": 840, "w": 520, "h": 44}]


def test_callout_order_reorders_pieces():
    """order は src の番号 (1 から) の並び。並べた順に上から重ねる (Callout が 1 つ目)。省略は書いた順。並べ替えでなければ誤り。"""
    co = {"src": SRC2 + [{"x": 0, "y": 0, "w": 400, "h": 44}], "dst": {"x": 300, "y": 200, "w": 1200}, "order": [3, 1, 2]}
    v = C.scene_values(LOOK, {"callout": co}, source_size=(3840, 2160))["callout"]
    assert [p["src"] for p in v["pieces"]] == [3, 1, 2] and v["order"] == [3, 1, 2]
    assert [p["crop"][0] for p in v["pieces"]] == [0, 900, 900]
    m = 1200 / 800
    assert [p["dst"][1] for p in v["pieces"]] == pytest.approx([200, 200 + 44 * m + 4, 200 + 2 * (44 * m + 4)])
    plain = C.scene_values(LOOK, {"callout": {k: x for k, x in co.items() if k != "order"}},
                           source_size=(3840, 2160))["callout"]
    assert [p["crop"][0] for p in plain["pieces"]] == [900, 900, 0] and "order" not in plain
    assert "src" not in plain["pieces"][0]
    it = Item()
    C.build_fusion(it, C.scene_values(LOOK, {"callout": co}, source_size=(3840, 2160)))
    assert it.comp["CalloutCrop"].inputs["XOffset"] == 0 and it.comp["CalloutCrop2"].inputs["XOffset"] == 900
    for bad in ([1, 2], [1, 2, 2], [0, 1, 2], [1, 2, 4], "321", [1.0, 2, 3], [True, 2, 3]):
        assert any("order" in x for x in C.callout_problems(dict(co, order=bad))), bad
    assert C.callout_problems({"src": SRC2[0], "dst": co["dst"], "order": [1]}) == []


def test_callout_problems_in_sb2resolve(tmp_path):
    """誤りの order は Resolve に触る前の点検で止まる。dst の省略は誤りにしない (書いた dst の書式の誤りは誤り)。"""
    sb = sb_rows(1, tmp_path)
    sb["rows"][0]["callout"] = {"src": SRC2, "order": [2, 2]}
    assert any("1 行目の callout: order" in b for b in S.problems(sb, S.plan(sb), {}))
    sb["rows"][0]["callout"] = {"src": SRC2}
    assert not any("callout" in b for b in S.problems(sb, S.plan(sb), {}))
    sb["rows"][0]["callout"] = {"src": SRC2, "dst": {"x": 0}}
    assert any("1 行目の callout: dst" in b for b in S.problems(sb, S.plan(sb), {}))


def test_callout_auto_dst():
    """dst を省略すると、画面の横の中央、テロップの帯 (無ければ画面の下) の上に、縁と影と margin を空けて置く。
    倍率は max_scale (タイムラインに映る大きさの何倍) と max_width の小さいほう。縦に収まらなければ縮める。"""
    a = LOOK["callout"]["auto_dst"]
    assert (a["max_scale"], a["max_width"], a["margin"], a["valign"]) == (4, 1540, 24, "bottom")
    bottom = 1080 - 24 - 3 - 10     # margin、縁 3 px、影のずらし 10 px
    one = {"src": {"x": 0, "y": 0, "w": 200, "h": 40}}
    # 4K の素材の 200 px は画面で 100 px。4 倍で 400 px
    assert C.callout_auto_dst(LOOK, one, 3840, 2160) == {"x": 760, "y": bottom - 80, "w": 400}
    v = C.callout_values(LOOK, one, 3840, 2160, 30)
    assert v["dst"] == [760, bottom - 80, 400, pytest.approx(80)] and v["size"] == pytest.approx(4.0)
    assert v["dst_auto"] == {"x": 760, "y": bottom - 80, "w": 400, "caption_band": None} and v["warning"] is None
    # 幅の広い切り抜きは max_width で決まる (1540 / 1000 = 1.54)
    wide = {"src": [{"x": 0, "y": 0, "w": 1000, "h": 40}, {"x": 0, "y": 50, "w": 600, "h": 40}]}
    assert C.callout_auto_dst(LOOK, wide, 3840, 2160) == {"x": 190, "y": int(bottom - (80 * 1.54 + 4)), "w": 1540}
    # テロップの帯の上に置く
    assert C.callout_auto_dst(LOOK, one, 3840, 2160, (900, 1000))["y"] == 900 - 37 - 80
    # 縦に収まらなければ縮める (10 個の 400x200 → 空き 1016 - 間 36 = 980 に 2000 px で 0.49 倍)
    d = C.callout_auto_dst(LOOK, {"src": [{"x": 0, "y": 0, "w": 400, "h": 200}] * 10}, 3840, 2160)
    assert d["w"] == 196 and d["y"] == 27
    # 空きの中央
    look = dict(LOOK, callout=dict(LOOK["callout"], auto_dst=dict(a, valign="center")))
    assert C.callout_auto_dst(look, one, 3840, 2160)["y"] == 27 + (bottom - 27 - 80) // 2
    with pytest.raises(ValueError, match="空き"):
        C.callout_auto_dst(LOOK, one, 3840, 2160, (40, 1060))
    # 極端に細長い切り抜きで幅や高さが 0 画素に切り捨てられるなら誤り (幅 1・高さ 2160 は縦に収めると幅 0 px。
    # 幅 3840・高さ 1 は max_width で 0.4 倍になり高さ 0.2 px)
    with pytest.raises(ValueError, match="0 画素"):
        C.callout_auto_dst(LOOK, {"src": {"x": 0, "y": 0, "w": 1, "h": 2160}}, 3840, 2160)
    with pytest.raises(ValueError, match="0 画素"):
        C.callout_auto_dst(LOOK, {"src": {"x": 0, "y": 0, "w": 3840, "h": 1}}, 3840, 2160)
    # 複数の切り抜きのうち細い 1 枚だけが 0 画素になるときも誤り (幅 3840 に合わせた倍率 約 0.401 で、幅 1 の 2 枚目は 0.4 px)
    with pytest.raises(ValueError, match="上から 2 枚目の切り抜き"):
        C.callout_auto_dst(LOOK, {"src": [{"x": 0, "y": 0, "w": 3840, "h": 40}, {"x": 0, "y": 50, "w": 1, "h": 40}]},
                           3840, 2160)
    # 縁と影が無ければ、その分だけ下へ
    assert C.callout_auto_dst(LOOK, dict(one, border=False, shadow=False), 3840, 2160)["y"] == 1080 - 24 - 80
    # scene_values は行のテロップの帯の上に置く
    font = C.caption_font(LOOK)
    row = {"caption": "テロップ", "callout": one}
    top, low = C.caption_band(LOOK, row, font)
    got = C.scene_values(LOOK, row, font, source_size=(3840, 2160))["callout"]
    assert got["dst"][1] + got["dst"][3] <= top - 37 + 1e-6
    assert got["dst_auto"]["caption_band"] == [pytest.approx(top, abs=0.1), pytest.approx(low, abs=0.1)]


# ---------- 自動の置き場所と枠線 auto の境目 ----------
def test_callout_auto_dst_follows_caption_position():
    """自動の置き場所は、テロップの帯の上と下の空きの高いほう。帯が上 (caption.position top) なら帯の下、
    中央 (結びのテロップ) なら広いほう。valign が bottom・center 以外なら誤り。"""
    font = C.caption_font(LOOK)
    one = {"src": {"x": 0, "y": 0, "w": 200, "h": 40}}
    top_look = dict(LOOK, caption=dict(LOOK["caption"], position="top"))
    row = {"caption": "上のテロップ", "callout": one}
    band = C.caption_band(top_look, row, font)
    assert band[1] < 300   # 帯は画面の上
    got = C.scene_values(top_look, row, font, source_size=(3840, 2160))["callout"]
    # 帯の下の空き (帯の下の端〜画面の下) の下の端 (valign bottom) に置く。帯には掛からない
    assert got["dst"][1] == 1080 - 24 - 3 - 10 - 80 and got["dst"][1] - 3 > band[1]
    assert got["dst_auto"]["caption_band"] == [pytest.approx(band[0], abs=0.1), pytest.approx(band[1], abs=0.1)]
    # 結びのテロップ (画面の中央の帯): 上と下の広いほう。どちらでも帯に掛からない
    closing = {"caption": "結び", "caption_style": "closing", "callout": one}
    b0, b1 = C.caption_band(LOOK, closing, font)
    d = C.scene_values(LOOK, closing, font, source_size=(3840, 2160))["callout"]["dst"]
    assert d[1] + d[3] <= b0 or d[1] >= b1
    want_above = b0 - 0 >= 1080 - b1
    assert (d[1] + d[3] <= b0) == want_above
    # valign の書き間違いは誤り (黙って中央にしない)
    bad = dict(LOOK, callout=dict(LOOK["callout"], auto_dst=dict(LOOK["callout"]["auto_dst"], valign="top")))
    with pytest.raises(ValueError, match="valign"):
        C.callout_auto_dst(bad, one, 3840, 2160)


def test_callout_auto_dst_warns_when_shrunk_below_timeline_size():
    """縦に収まらず、タイムラインに映る大きさより小さく (Size 1 未満) 縮んだら警告する。dst を書いた行は警告しない。"""
    many = {"src": [{"x": 0, "y": i * 50, "w": 1000, "h": 48} for i in range(60)]}
    v = C.callout_values(LOOK, many, 3840, 2160, 30)
    assert v["size"] < 1 and "小さく縮んだ" in v["warning"]
    assert C.callout_values(LOOK, {"src": {"x": 0, "y": 0, "w": 200, "h": 40}}, 3840, 2160, 30)["warning"] is None
    fixed = dict(many, dst={"x": 300, "y": 0, "w": 300})
    assert "小さく縮んだ" not in (C.callout_values(LOOK, fixed, 3840, 2160, 30)["warning"] or "")


def test_callout_dst_typo_is_an_error(tmp_path):
    """dst が無く、知らない項目 (dts など) がある callout は誤り (自動の置き場所で黙って組まない)。"""
    co = {"src": SRC2, "dts": {"x": 100, "y": 100, "w": 800}}
    assert any("dts" in x and "dst" in x for x in C.callout_problems(co))
    assert not C.callout_problems({"src": SRC2, "_memo": "読まない"})
    assert not C.callout_problems({"src": SRC2, "dst": {"x": 0, "y": 0, "w": 10}})
    sb = sb_rows(1, tmp_path)
    sb["rows"][0]["callout"] = co
    assert any("1 行目の callout: dst が無く" in b for b in S.problems(sb, S.plan(sb), {}))


def _no_room_storyboard(tmp_path):
    """テロップの帯が高く (11 行)、帯の上にも下にも callout の自動の置き場所の空きが無い行の構成表。"""
    src = tmp_path / "rec.mp4"
    src.write_bytes(b"")
    path = tmp_path / "sb.json"
    C.write_json(str(path), {"source": str(src), "output": str(tmp_path / "o.mp4"), "fps": 30,
                             "rows": [{"start": 0, "end": 5, "seconds": 5,
                                       "caption": "一|二|三|四|五|六|七|八|九|十|十一",
                                       "callout": {"src": {"x": 100, "y": 100, "w": 400, "h": 40}}}]})
    return src, path


def test_dry_run_reports_no_room_for_auto_dst(tmp_path, monkeypatch):
    """自動の置き場所に空きが無い行は、例外で落ちずに組む前の点検の誤りになる (Resolve に触らない。--dry-run でも同じ)。"""
    import argparse
    src, path = _no_room_storyboard(tmp_path)
    monkeypatch.setattr(S, "probe_sources", lambda rows: {str(src): {"width": 1920, "height": 1080, "fps": 30.0,
                                                                      "duration": 60.0}})
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: pytest.fail("接続した"))
    for dry in (True, False):
        log = {}
        ok, summary, lines = S.run(argparse.Namespace(storyboard=str(path), source=None, name=None, render=False,
                                                      dry_run=dry), log)
        assert not ok and "構成表に誤り 1 件" in summary
        assert len(log["problems"]) == 1 and log["problems"][0].startswith("1 行目の callout: callout の自動の置き場所")


def test_row_values_collects_value_errors_and_add_fusion_skips_the_row(tmp_path):
    """row_values は値を作れない行 (自動の置き場所に空きが無い) を誤りに集め、add_fusion はその行を組まない。ほかの行は組む。"""
    sb = sb_rows(2, tmp_path)
    sb["rows"][0].update(caption="一|二|三|四|五|六|七|八|九|十|十一", callout={"src": {"x": 0, "y": 0, "w": 400, "h": 40}})
    rows = S.plan(sb)
    vals, _, errs = S.row_values(sb, rows, {sb["source"]: (1920, 1080)}, None)
    assert len(errs) == 1 and errs[0].startswith("1 行目: callout の自動の置き場所") and vals[0]["error"]
    items = [Item(), Item()]
    ok, res, msgs = S.add_fusion(items, sb, rows, {sb["source"]: {"size": (1920, 1080)}}, None)
    assert not ok and items[0].comp is None and items[1].comp is not None and res["errors"] == errs


def test_undecided_auto_border_row_is_built_with_dark_color(tmp_path):
    """枠線の色 auto を決められない行は誤りにするが、Fusion の値は auto.dark の色で作る (--render-anyway ではその色で書き出す)。"""
    sb = sb_rows(1, tmp_path)
    sb["rows"][0]["focus"] = {"x": 100, "y": 100, "w": 400, "h": 100, "border": {"color": "auto"}}
    vals, _, errs = S.row_values(sb, S.plan(sb), {sb["source"]: (1920, 1080)}, None, reader=lambda p, n: None)
    assert errs and vals[0]["focus"]["border"]["rgb"] == C.hex_rgb(AUTO["dark"])


def test_auto_border_in_hold_looks_at_the_frozen_box():
    """hold.at が時刻で focus が keys で動く行の、止めた区間で現れる枠線: 測る範囲は止めたクリップに描く位置
    (hold_values と同じ止めた瞬間の位置) で、行の最後のフレームの位置ではない。"""
    look = dict(LOOK, fps=30)
    row = {"start": "0:01", "end": "0:04", "hold": {"seconds": 2, "at": "0:03"},
           "focus": {"x": 100, "y": 100, "w": 400, "h": 200, "start": 3.5, "border": {"color": "auto"},
                     "keys": [{"t": 0, "x": 0, "y": 0, "w": 100, "h": 100}, {"t": 3, "x": 1500, "y": 900, "w": 100, "h": 100}]}}
    assert C.hold_time(row, 30, 90) == 60
    got = C.auto_border(look, row, "x", 90, 1.0, reader=lambda p, n: _plain(0), sfps=30)
    assert got["box"]["x"] == pytest.approx(1000) and got["box"]["y"] == pytest.approx(600)
    assert got["source_frame"] == 90
    # 組む側の止めたクリップの位置と同じ (scene_values → hold_values。中心はタイムラインの値)
    V = C.scene_values(look, dict(row, focus=dict(row["focus"], border=True)), source_size=(1920, 1080))
    HV = C.hold_values(V, C.hold_time(row, 30, 90), 90)
    assert HV["focus"]["center"][0] == pytest.approx((1000 + 50) / 1920)


def test_auto_border_grayscale_reader_and_fps_mismatch():
    """reader が 2 次元 (グレースケール) の配列を返しても輝度とみなして決める。素材と構成表のフレームレートが違えば警告する。"""
    import numpy as np
    gray = C.auto_border(LOOK, _auto_row(), "x", 150, 1.0, reader=lambda p, n: np.full((2160, 3840), 250, np.uint8), sfps=30)
    assert gray["color"] == AUTO["light"] and "error" not in gray
    odd = C.auto_border(LOOK, _auto_row(), "x", 150, 1.0, reader=lambda p, n: _plain(0), sfps=60)
    assert odd["color"] == AUTO["dark"] and "フレームレートが違う" in odd["warning"]
    assert "warning" not in C.auto_border(LOOK, _auto_row(), "x", 150, 1.0, reader=lambda p, n: _plain(0), sfps=30)


def test_unknown_keys_from_local_json_are_labelled():
    """config/local.json の storyboard の書き間違いは「config/local.json の storyboard の …」と出す (全体の項目と区別する)。"""
    sb = {"captoin": {}, "focsu": 1, "rows": [{"fucus": {}}]}
    assert C.unknown_keys(sb, local={"captoin": {}}) == ["config/local.json の storyboard の captoin", "全体の focsu",
                                                          "1 行目の fucus"]


def test_row_clips_fixed_values():
    """構成表の行の読み方 (common.row_clips) を、手で計算した値と比べる。
    行の秒数・既定の秒数・行ごとの素材・止めた区間で後ろの行がずれること・0 と 1 フレームの行。"""
    sb = {"fps": 30, "seconds_per_row": 2, "source": "a.mp4", "rows": [
        {"start": "0:01", "end": 3},
        {"start": 10, "end": "0:14.5", "seconds": 1, "source": "b.mp4", "hold": {"seconds": 0.5}},
        {"start": "1:00:00", "end": "1:00:01", "seconds": 0.02},   # 0.6 フレームは 1 フレーム
        {"start": 5, "end": 6, "seconds": 0.01},                      # 0.3 フレームは 0 フレーム
        {"start": 7, "end": 8, "hold": {"seconds": "x"}},             # 読めない hold は 0 (点検で誤りにする)
    ]}
    got = [(c["row"], c["source"], c["start"], c["end"], c["rec"], c["frames"], c["hold_frames"])
           for c in C.row_clips(sb)]
    assert got == [(1, "a.mp4", 1.0, 3.0, 0, 60, 0), (2, "b.mp4", 10.0, 14.5, 60, 30, 15),
                   (3, "a.mp4", 3600.0, 3601.0, 105, 1, 0), (4, "a.mp4", 5.0, 6.0, 106, 0, 0),
                   (5, "a.mp4", 7.0, 8.0, 106, 60, 0)]
    # 組む側の速さ (%) と止めたクリップの位置は plan が決める。0 フレームの行の速さは None
    p = S.plan(sb)
    assert [r["pct"] for r in p][:2] == [100.0, 450.0] and p[3]["pct"] is None
    assert p[1]["hold_rec"] == 90 and p[0]["hold_rec"] is None
