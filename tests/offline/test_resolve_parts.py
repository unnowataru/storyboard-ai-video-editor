"""Resolve に渡す部品のテスト。Resolve の API は模擬のオブジェクトで置き換えるので、Resolve は要らない。

- 書き出しの待機 (common.render_jobs) と、テストの補助の書き出し (tests/resolve/resolve_helpers.Trial.render)
- 段階の成否 (scripts/api_trial.py の step / run_steps)
- 素材のフレームレート (common.source_fps)
- Fusion のテロップの背景と文字 (common.solid / text_tool) と、ズームの向き (common.zoom_ends)
"""
import os
import sys
import time

import pytest

import common as C
from conftest import Comp, Spline

sys.path.insert(0, os.path.join(C.REPO, "tests", "resolve"))
import api_trial  # noqa: E402
import resolve_helpers as RH  # noqa: E402


# ---------- 模擬の Resolve ----------
class FakeProject:
    """書き出しまわりだけの模擬。started と status で StartRendering の戻り値と作業の状態を決める。"""

    def __init__(self, started=True, status="Complete", busy=0):
        self.started, self.status, self.busy = started, status, busy
        self.calls = []

    def StartRendering(self, jobs, isInteractiveMode=False):
        self.calls.append("StartRendering")
        return self.started

    def IsRenderingInProgress(self):
        self.calls.append("IsRenderingInProgress")
        self.busy -= 1
        return self.busy >= 0

    def GetRenderJobStatus(self, job):
        self.calls.append("GetRenderJobStatus")
        return {"JobStatus": self.status}

    def SetCurrentTimeline(self, tl):
        return True

    def SetCurrentRenderFormatAndCodec(self, f, c):
        return True

    def SetRenderSettings(self, d):
        return True

    def AddRenderJob(self):
        return "job1"


# ---------- 書き出しの待機 ----------
def test_render_jobs_done_only_when_complete():
    r = C.render_jobs(FakeProject(busy=2), ["job1"], interval=0, limit=10)
    assert r["done"] and r["status"]["job1"]["JobStatus"] == "Complete"
    assert not C.render_jobs(FakeProject(status="Failed"), ["job1"], interval=0, limit=10)["done"]
    assert not C.render_jobs(FakeProject(started=False, status="Ready"), ["job1"], interval=0, limit=10)["done"]


def test_render_jobs_call_counts():
    """呼ぶ回数: 開始 1 回、進行中の確認は待った回数 + 1、状態は作業ごとに 1 回。"""
    p = FakeProject(busy=3)
    C.render_jobs(p, ["job1"], interval=0, limit=10)
    assert p.calls.count("StartRendering") == 1
    assert p.calls.count("IsRenderingInProgress") == 4
    assert p.calls.count("GetRenderJobStatus") == 1


def test_render_jobs_timeout_is_not_done():
    r = C.render_jobs(FakeProject(busy=10 ** 6, status="Rendering"), ["job1"], interval=0, limit=0.01)
    assert not r["done"]


def _trial(tmp_path, proj):
    t = RH.Trial.__new__(RH.Trial)   # 接続しない (__init__ を通さない)
    t.proj, t.out, t.look = proj, str(tmp_path), {"render": {"format": "mp4", "codec": "H264"}}
    return t


def test_trial_render_does_not_return_old_video_on_failure(tmp_path):
    old = tmp_path / "case.mp4"
    old.write_bytes(b"old")
    video, st = _trial(tmp_path, FakeProject(started=False, status="Failed")).render(None, "case")
    assert video is None and st["JobStatus"] == "Failed"


def test_trial_render_ignores_stale_file_even_if_complete(tmp_path):
    old = tmp_path / "case.mp4"
    old.write_bytes(b"old")
    past = time.time() - 3600
    os.utime(old, (past, past))
    video, _ = _trial(tmp_path, FakeProject()).render(None, "case")
    assert video is None


@pytest.mark.parametrize("extra_files", [{}, {"case.comp": b"comp"}], ids=["video-only", "same-name-comp"])
def test_trial_render_returns_new_video(tmp_path, extra_files):
    # 同名の .comp が一覧で先に来ても動画を返す (fusion_lock_render が書き出し前に置く)
    for name, content in extra_files.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / "case.mp4").write_bytes(b"new")
    video, _ = _trial(tmp_path, FakeProject()).render(None, "case")
    assert video == str(tmp_path / "case.mp4")


# ---------- api_trial の段階の成否 ----------
@pytest.fixture
def fresh_log(monkeypatch):
    monkeypatch.setattr(api_trial, "log", {"steps": {}})
    monkeypatch.setattr(api_trial, "OUT", None)
    monkeypatch.setattr(api_trial, "WARNINGS", [])
    return api_trial.log


def test_step_false_return_is_ng(fresh_log):
    assert api_trial.step("speed", lambda: (False, {"set": False})) is False
    assert fresh_log["steps"]["speed"]["ok"] is False


def test_step_exception_is_ng(fresh_log):
    def boom():
        raise RuntimeError("x")
    assert api_trial.step("markers", boom) is False
    assert "RuntimeError" in fresh_log["steps"]["markers"]["error"]


def test_run_steps_stops_after_required_and_fails(fresh_log):
    ran = []
    code = api_trial.run_steps([("build", lambda: (False, {})), ("speed", lambda: ran.append(1) or (True, {}))])
    assert code == 1 and ran == [] and fresh_log["stopped_after"] == "build"


def test_run_steps_nonzero_when_later_step_ng(fresh_log):
    code = api_trial.run_steps([("build", lambda: (True, {})), ("render", lambda: (False, {}))])
    assert code == 1 and "stopped_after" not in fresh_log


def test_run_steps_zero_when_all_ok(fresh_log, capsys):
    assert api_trial.run_steps([("build", lambda: (True, {})), ("render", lambda: (True, {}))]) == 0
    assert capsys.readouterr().out == "" and api_trial.failure_lines() == []   # 段階ごとには出さない


def test_failure_lines_show_only_ng_steps(fresh_log):
    def boom():
        raise RuntimeError("x")
    api_trial.run_steps([("build", boom), ("speed", lambda: (True, {}))])
    assert api_trial.failure_lines() == ["NG build: RuntimeError: x", "build が NG なので後続の段階は動かしていない"]


# ---------- 素材のフレームレート ----------
class FakeItem:
    def __init__(self, props):
        self.props = props

    def GetClipProperty(self, k):
        return self.props.get(k)


def test_source_fps_reads_clip_property():
    assert C.source_fps(FakeItem({"FPS": 24.0}), 30) == 24.0
    assert C.source_fps(FakeItem({"FPS": "29.97"}), 30) == pytest.approx(29.97)
    assert C.source_fps(FakeItem({"FPS": ""}), 30) == 30.0
    assert C.source_fps(FakeItem({}), 30) == 30.0


# ---------- Fusion の部品とズームの向き ----------
def test_text_tool_sets_color():
    comp = Comp()
    t = C.text_tool(comp, "a", 0.06, (0.5, 0.1), "Meiryo", "Bold", [255, 0, 0])
    assert (t.inputs["Red1"], t.inputs["Green1"], t.inputs["Blue1"]) == (1.0, 0.0, 0.0)


def test_solid_sets_color_and_mask():
    comp = Comp()
    bg = C.solid(comp, [0, 0, 0, 128], (0.5, 0.1), 1.0, 0.2, corner=0.3)
    mask = comp.tools[3]
    assert bg.inputs["TopLeftAlpha"] == pytest.approx(128 / 255)
    assert mask.inputs["Height"] == 0.2 and mask.inputs["CornerRadius"] == 0.3
    assert bg.EffectMask == mask.Mask


def test_zoom_ends_direction():
    assert C.zoom_ends({}, 1.0, 3.0) == (1.0, 3.0)
    assert C.zoom_ends({"direction": "in"}, 1.0, 3.0) == (1.0, 3.0)
    assert C.zoom_ends({"direction": "out"}, 1.0, 3.0) == (3.0, 1.0)


# ---------- 1 行分の Fusion の値 ----------
LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けないよう既定値だけで見る
ROW3 = C._read_json(os.path.join(C.REPO, "tests", "trial-storyboard.json"))["rows"][2]


def test_scene_values_trial_row():
    v = C.scene_values(dict(LOOK, safe_area={"aspect": "3:2"}), ROW3, badge="スキップ")   # 警告はセーフエリア 3:2 で確かめる
    m = v["masks"][0]
    assert m["center"] == pytest.approx(((440 + 520) / 1920, 1 - (370 + 50) / 1080))
    assert (m["width"], m["height"]) == pytest.approx((1040 / 1920, 100 / 1080))   # 幅は画面の幅、高さは画面の高さ基準
    assert m["pixel_frequency"] == LOOK["mask"]["pixel_frequency"]
    z = v["zoom"]
    assert z["size"] == (1.0, 3.0) and z["frames"] == 90 and z["filter"] == 8 and z["warning"]
    # 寄せた後の中心: 0.5 + 倍率 × (0.5 − ズーム範囲の中心)。ズーム範囲の中心は (1200+320)/1920 と 1 − (100+180)/1080
    assert z["x"] == pytest.approx((0.5, 0.5 + 3 * (0.5 - 1520 / 1920)))
    assert z["y"] == pytest.approx((0.5, 0.5 + 3 * (0.5 - (1 - 280 / 1080))))
    assert C.scene_values(LOOK, dict(ROW3, zoom=dict(ROW3["zoom"], direction="out")))["zoom"]["x"] == pytest.approx(
        (0.5 + 3 * (0.5 - 1520 / 1920), 0.5))
    assert v["caption"]["text"] == ROW3["caption"] and v["caption"]["font_name"] == LOOK["caption"]["font_name"]
    assert v["badge"]["text"] == LOOK["badge"]["prefix"] + "スキップ" and v["badge"]["frames"] == 60
    assert C.scene_values(LOOK, {}) == {"masks": [], "focus": None, "zoom": None, "caption": None, "badge": None,
                                        "callout": None, "canvas": None, "point": None, "side": None,
                                        "layout": None}
    # 空の肩テロップの文は接頭辞だけの肩テロップ (badge に "" を渡しても落ちない)
    assert C.scene_values(LOOK, {}, badge="")["badge"]["text"] == LOOK["badge"]["prefix"]


def test_scene_values_source_pixels():
    """4K の素材で座標を 2 倍にした行は、1080p の素材の行と同じ Fusion の値になる (素材の画素からタイムラインの画素へ直す)。"""
    def double(b):
        return dict(b, **{k: b[k] * 2 for k in "xywh"})
    row4k = dict(ROW3, zoom=double(ROW3["zoom"]), masks=[double(m) for m in ROW3["masks"]])
    v4 = C.scene_values(LOOK, row4k, source_size=(3840, 2160))
    v1 = C.scene_values(LOOK, ROW3)
    assert v4["masks"] == v1["masks"] and v4["zoom"] == v1["zoom"]


# ---------- 区間の書き出しのテストの部品 (tests/resolve/render_range.py・resolve_helpers) ----------
def test_make_check_unknown_is_not_pass():
    checks = []
    check = RH.make_check(checks)
    check("a", True)
    check("b", False)
    check("c", None)
    assert [(c["status"], c["ok"]) for c in checks] == [("pass", True), ("fail", False), ("unknown", False)]


def test_render_job_does_not_add_job_when_settings_fail():
    class P:
        added = False

        def SetCurrentRenderFormatAndCodec(self, f, c):
            return True

        def SetRenderSettings(self, s):
            return False

        def AddRenderJob(self):
            P.added = True
            return "job"
    rr = RH.render_job(P(), {"TargetDir": "d", "CustomName": "n"}, "mov", "x", limit=1)
    assert not rr["done"] and not rr["set_settings"] and not P.added


def test_lossless_candidates_use_identifiers_in_order():
    """説明の文言ではなく形式と codec の識別子で選び、LOSSLESS の順に並べる。返ってこない形式・codec は飛ばす。"""
    formats = {"QuickTime": "mov", "AVI": "avi", "MP4": "mp4"}
    codecs = {"mov": {"Apple ProRes 422": "ProRes422", "非圧縮 YUV 422 8-bit": "YUV422_8", "別の説明": "RGB8",
                      "Uncompressed ARGB 8-bit": "ARGB8"},
              "avi": {"Uncompressed RGB 10-bit": "RGB10"}, "mp4": {"H.264": "H264"}}
    cands, seen = RH.lossless_candidates(formats, lambda f: codecs.get(f))
    assert [(c["format"], c["codec"]) for c in cands] == [("mov", "RGB8"), ("mov", "YUV422_8"), ("avi", "RGB10")]
    assert cands[0]["description"] == "別の説明" and set(seen) == {"mov", "avi"}
    assert RH.lossless_candidates({"MP4": "mp4"}, lambda f: codecs.get(f)) == ([], {})


def test_range_compare_reports_offset_and_inclusive_count():
    import render_range as RR
    full = [f"h{i}" for i in range(10)]
    ok = RR.compare(full, full[3:6], 3, 5)
    assert ok["same"] and ok["frames"] == ok["want_frames"] == 3 and ok["offsets"] == [0]
    late = RR.compare(full, full[4:7], 3, 5)            # 1 フレーム後ろから書かれた
    assert not late["same"] and late["offsets"] == [1] and late["full_frames"] == [4, 5, 6]
    short = RR.compare(full, full[3:5], 3, 5)           # 終わりを含まない
    assert short["frames"] == 2 and short["want_frames"] == 3
    assert RR.compare(full, ["x"], 0, 0)["unmatched"] == 1


def test_range_neighbours_hold():
    import render_range as RR
    full = ["a", "b", "b", "b", "b", "c", "d"]          # 2〜4 が止めたクリップ (1 は止める元のフレーム)
    nb = RR.neighbours(full, 2, 3)
    assert nb["ok"] and nb["entering_same"] is True and nb["inside_same"] == "2/2"
    assert not RR.neighbours(["a", "a", "b"], None, 0)["ok"]


def test_render_lossless_skips_unreadable_and_stops_while_rendering(monkeypatch, tmp_path):
    """FFmpeg が読めない書き出しは消して次の候補へ。選んだら止める。書き出しが続いていたら次を試さない。"""
    formats = {"QuickTime": "mov"}
    codecs = {"mov": {"Uncompressed RGB 8-bit": "RGB8", "Uncompressed RGB 10-bit": "RGB10", "Uncompressed YUV 422 8-bit": "YUV422_8"}}

    class P:
        busy = False

        def GetRenderFormats(self):
            return formats

        def GetRenderCodecs(self, f):
            return codecs.get(f)

        def SetCurrentRenderFormatAndCodec(self, f, c):
            return True

        def IsRenderingInProgress(self):
            return P.busy

        def StopRendering(self):
            P.stopped = True
            P.busy = False   # 止めた後は書き出し中でない

    order = []

    def fake_job(proj, settings, fmt, codec, limit):
        order.append(codec)
        path = tmp_path / f"{codec}.mov"
        path.write_bytes(b"x")
        return {"done": True, "written": True, "path": str(path), "status": {"JobStatus": "Complete"}}

    def fake_md5(path):
        if path.endswith("RGB10.mov"):
            return ["a", "b"]
        raise RuntimeError("読めない\ncodec_tag 0x20000000")

    monkeypatch.setattr(RH, "render_job", fake_job)
    monkeypatch.setattr(RH, "frame_md5", fake_md5)
    monkeypatch.setattr(RH, "video_stream", lambda p: {"codec_name": "rawvideo"})
    lz = RH.render_lossless(P(), str(tmp_path), "n", limit=1)
    assert order == ["RGB8", "RGB10"] and lz["chosen"]["codec"] == "RGB10" and lz["md5"] == ["a", "b"]
    assert not (tmp_path / "RGB8.mov").exists() and (tmp_path / "RGB10.mov").exists()
    assert lz["render"]["frames"] == 2 and lz["render"]["stream"] == {"codec_name": "rawvideo"}
    codecs["mov"].pop("Uncompressed RGB 10-bit")
    order.clear()
    lz = RH.render_lossless(P(), str(tmp_path), "n", limit=1)
    assert order == ["RGB8", "YUV422_8"] and lz["chosen"] is None and not (tmp_path / "YUV422_8.mov").exists()
    assert "codec_tag" in lz["tried"][0]["ffmpeg"]
    P.busy = True
    order.clear()
    lz = RH.render_lossless(P(), str(tmp_path), "n", limit=1)
    assert order == ["RGB8"] and lz["tried"][0]["still_rendering"] and lz["chosen"] is None
    assert P.stopped   # 続いている書き出しは止める


def test_fresh_project_closes_deletes_and_creates():
    calls = []

    class Proj:
        def __init__(self, name):
            self.name = name

        def GetName(self):
            return self.name

        def SetSettings(self, s):
            calls.append(("settings", s))
            return True

    class PM:
        def GetCurrentProject(self):
            return Proj("test-native-fade")

        def CloseProject(self, p):
            calls.append(("close", p.GetName()))
            return True

        def DeleteProject(self, n):
            calls.append(("delete", n))
            return True

        def CreateProject(self, n):
            calls.append(("create", n))
            return Proj(n)

    proj, rec = RH.fresh_project(PM(), "test-native-fade", 1920, 1080, 30)
    assert proj.GetName() == "test-native-fade" and rec == {"closed": True, "deleted_old": True, "created": True, "settings": True}
    assert [c[0] for c in calls] == ["close", "delete", "create", "settings"]
    assert calls[-1][1] == {"timelineFrameRate": "30", "timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"}


def test_append_at_adds_track_and_places_at_frame():
    class Item:
        def __init__(self, info):
            self.info = info

        def GetStart(self):
            return self.info["recordFrame"]

        def GetDuration(self):
            return self.info["endFrame"] - self.info["startFrame"]

        def GetTrackTypeAndIndex(self):
            return ["video", self.info["trackIndex"]]

    class TL:
        tracks = 1

        def GetTrackCount(self, kind):
            return self.tracks

        def AddTrack(self, kind):
            self.tracks += 1
            return True

        def GetStartFrame(self):
            return 108000

    class MP:
        def AppendToTimeline(self, infos):
            self.infos = infos
            return [Item(i) for i in infos]

    tl, mp = TL(), MP()
    item, rec = RH.append_at(mp, tl, "clip", 90, 60, track=2)
    assert tl.tracks == 2 and rec == {"start": 90, "duration": 60, "track": ["video", 2]}
    assert mp.infos == [{"mediaPoolItem": "clip", "startFrame": 0, "endFrame": 60, "mediaType": 1, "trackIndex": 2,
                         "recordFrame": 108090}]


def test_pixel_regions():
    np = pytest.importorskip("numpy")
    a = np.full((40, 60, 3), 96, np.uint8)
    b = a.copy()
    b[10:20, 30:50] = (255, 200, 0)    # 四角 (30, 10, 50, 20)
    b[35, 5] = (99, 96, 96)            # 四角の外の小さな差 (3)
    assert RH.changed_box(a, b, 8) == [30, 10, 50, 20]
    assert RH.changed_box(a, a, 8) is None
    assert RH.diff_outside(a, b, [(30, 10, 50, 20)], pad=2) == 3
    b[35, 5] = 96
    assert RH.diff_outside(a, b, [(30, 10, 50, 20)], pad=0) == 0
    assert RH.diff_outside(a, b, [(31, 10, 50, 20)], pad=0) == 159   # 1 列はみ出す
    assert RH.box_mean(b, (30, 10, 50, 20)) == [255.0, 200.0, 0.0]


def test_differing_spans_and_spline_keys():
    assert RH.differing(["a", "b", "c"], ["a", "x", "c", "d"]) == [1, 3]
    assert RH.spans([5, 3, 4, 9, 10, 1]) == [[1, 1], [3, 5], [9, 10]] and RH.spans([]) == []
    kf = {89.0: {1.0: 0.0}, 90.0: {1: 1.0, "RH": {1: 90.3}}, 150.0: {1: 0}}
    assert RH.spline_keys(kf) == {89: 0.0, 90: 1.0, 150: 0.0} and RH.spline_keys(None) == {}


def test_append_at_stops_when_track_cannot_be_added():
    """AddTrack が失敗したら置かない (無い番号で置くとトラック 1 に置かれる)。"""
    class TL:
        def GetTrackCount(self, kind):
            return 1

        def AddTrack(self, kind):
            return False

    class MP:
        called = False

        def AppendToTimeline(self, infos):
            MP.called = True
            return []

    assert RH.append_at(MP(), TL(), "clip", 30, 90, track=2) == (None, None) and not MP.called


def test_render_set_renders_others_in_chosen_format(monkeypatch):
    class P:
        busy = False

        def __init__(self):
            self.current = None

        def IsRenderingInProgress(self):
            return P.busy

        def StopRendering(self):
            P.stopped = True
            P.busy = False   # 止めた後は書き出し中でない

        def SetCurrentTimeline(self, tl):
            self.current = tl

    jobs = []
    monkeypatch.setattr(RH, "render_lossless", lambda proj, d, name, limit: {
        "formats": {"QuickTime (mov)": {}}, "candidates": [], "tried": [], "chosen": {"format": "mov", "codec": "RGB8"},
        "render": {"path": "none.mov"}, "md5": ["a", "b"]})

    def fake_job(proj, settings, fmt, codec, limit):
        jobs.append((proj.current, settings["CustomName"], fmt, codec))
        return {"done": settings["CustomName"] != "t-bad", "written": True, "path": settings["CustomName"] + ".mov"}

    monkeypatch.setattr(RH, "render_job", fake_job)
    monkeypatch.setattr(RH, "frame_md5", lambda p: ["a", "x"])
    rs = RH.render_set(P(), {"none": "TL0", "full": "TL1", "bad": "TL2"}, "none", ["full", "bad"], "d", "t")
    assert jobs == [("TL1", "t-full", "mov", "RGB8"), ("TL2", "t-bad", "mov", "RGB8")]
    assert rs["md5"] == {"none": ["a", "b"], "full": ["a", "x"]} and set(rs["renders"]) == {"none", "full", "bad"}
    assert set(rs["info"]) == {"formats", "lossless", "renders"}
    # 2 本目以降が時間切れで書き出しが続いていたら、次のタイムラインに進まない
    P.busy = True
    jobs.clear()
    rs = RH.render_set(P(), {"none": "TL0", "full": "TL1", "bad": "TL2"}, "none", ["full", "bad"], "d", "t")
    assert jobs == [("TL1", "t-full", "mov", "RGB8")] and rs["renders"]["full"]["still_rendering"] and "full" not in rs["md5"]
    assert P.stopped   # 続いている書き出しは止めてから戻る


def test_native_fade_rejects_abrupt_switch():
    """140〜149 が 1 のまま 150 で 0 になる切り替え (フェードを無視) は通さない。直線で消えるものは通す。"""
    import native_fade as NF
    linear = [1.0, 1.0] + [round((150 - f) / 9, 4) for f in range(142, 151)]
    assert NF.fade_judge(linear)
    assert not NF.fade_judge([1.0] * 10 + [0.0])
    assert not NF.fade_judge([1.0] * 9 + [0.0, 0.0])          # 149 に残らない
    assert not NF.fade_judge(linear[:5] + [0.8] + linear[6:])  # 途中で増える


def test_still_source_puts_still_on_helper_timeline_and_restores_current():
    """静止画を補助のタイムラインの V1 に置き、そのタイムラインの項目を返す。今のタイムラインは元に戻す。"""
    class Item:
        def GetClipProperty(self, k):
            return {"Frames": "150"}.get(k, "")

    class TL:
        def __init__(self, name):
            self.name, self.infos = name, []

        def GetTrackCount(self, kind):
            return 1

        def GetStartFrame(self):
            return 108000

        def GetMediaPoolItem(self):
            return Item()

    class Placed:
        def GetStart(self):
            return 108000

        def GetDuration(self):
            return 150

        def GetTrackTypeAndIndex(self):
            return ["video", 1]

    class MP:
        ok = True

        def CreateEmptyTimeline(self, name):
            self.tl = TL(name)
            return self.tl

        def AppendToTimeline(self, infos):
            self.infos = infos
            return [Placed()] if self.ok else []

    class P:
        current = "TARGET"

        def GetCurrentTimeline(self):
            return self.current

        def SetCurrentTimeline(self, tl):
            self.current = tl

    mp, proj = MP(), P()
    item, rec = RH.still_source(mp, proj, "png", "label-still")
    assert isinstance(item, Item) and mp.tl.name == "label-still" and proj.current == "TARGET"
    assert mp.infos == [{"mediaPoolItem": "png", "startFrame": 0, "endFrame": 1, "mediaType": 1, "trackIndex": 1,
                         "recordFrame": 108000}]
    assert rec["item"]["Frames"] == "150" and rec["still"] == {"start": 0, "duration": 150, "track": ["video", 1]}
    mp.ok = False   # 静止画を置けなければ、空のタイムラインの項目を返さず、今のタイムラインを戻す
    item, rec = RH.still_source(mp, proj, "png", "label-still-2")
    assert item is None and rec["still"] is None and proj.current == "TARGET"


def test_animated_keys_reads_connected_spline():
    tool, spline = Comp().AddTool("Merge"), Spline("BezierSpline")
    spline.keys = {0: 0.0, 90: 1.0}
    tool.ConnectInput("Blend", spline)
    assert RH.animated_keys(tool, "Blend") == {0: 0.0, 90: 1.0}
    assert RH.animated_keys(tool, "Size") is None


def test_render_set_with_chosen_skips_base(monkeypatch):
    class P:
        def IsRenderingInProgress(self):
            return False

        def SetCurrentTimeline(self, tl):
            self.current = tl

    monkeypatch.setattr(RH, "render_lossless", lambda *a, **k: pytest.fail("基準は書き出さない"))
    monkeypatch.setattr(RH, "render_job", lambda proj, settings, fmt, codec, limit: {
        "done": True, "written": True, "path": f"{settings['CustomName']}.{fmt}"})
    monkeypatch.setattr(RH, "frame_md5", lambda p: ["h"])
    rs = RH.render_set(P(), {"none": 0, "a": 1}, "none", ["a"], "d", "t", chosen={"format": "mov", "codec": "RGB8"})
    assert rs["md5"] == {"a": ["h"]} and set(rs["renders"]) == {"a"} and rs["chosen"]["codec"] == "RGB8"
