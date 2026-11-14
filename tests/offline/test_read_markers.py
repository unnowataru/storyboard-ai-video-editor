"""マーカーを構成表の行に当てる (scripts/read_markers.py) のテスト。Resolve は使わない。"""
import json
import os
import sys

import pytest

import common as C
import read_markers as R
import sb2resolve as S
from conftest import patch_common


def test_row_of_clip_reads_row_tag():
    assert R.row_of_clip({0: {"customData": "row8", "name": "x"}}) == 8
    assert R.row_of_clip({0: {"customData": ""}}) is None
    assert R.row_of_clip({}) is None


def test_untagged_clip_belongs_to_previous_row():
    # 行 2 は「止める」で、目印の無いクリップが後ろに続く
    clips = R.assign_rows([{"start": 30, "end": 144, "row": 2}, {"start": 0, "end": 30, "row": 1},
                           {"start": 144, "end": 261, "row": None}, {"start": 261, "end": 321, "row": 3}])
    assert [(c["start"], c["row"], c["tagged"]) for c in clips] == [(0, 1, True), (30, 2, True), (144, 2, False),
                                                                    (261, 3, True)]


def test_locate_marker_in_hold_and_outside():
    clips = R.assign_rows([{"start": 0, "end": 30, "row": 1}, {"start": 30, "end": 60, "row": None}])
    found = R.locate({45: {"name": "m", "note": "長く", "color": "Blue"}, 90: {"name": "外"}}, clips, 30.0)
    assert found[0]["row"] == 1 and found[0]["in_hold"] and found[0]["into_clip"] == 0.5
    assert found[0]["seconds"] == 1.5 and found[0]["note"] == "長く"
    assert found[1]["row"] is None and found[1]["into_clip"] is None


def test_row_info_from_storyboard():
    sb = {"rows": [{"caption": "a|b", "start": 1, "end": 2, "zoom": {}}]}
    assert R.row_info(sb, 1) == {"caption": "a|b", "start": 1, "end": 2}
    assert R.row_info(sb, 2) == {} and R.row_info(None, 1) == {}


# ---------- sb2resolve の記録から行を決める ----------
def record(project="p", timeline="t", started="2026-11-05 10:00:00", hold_at=None):
    """sb2resolve の記録の形。行 1 は 0〜30、行 2 は 30〜60 で、hold_at があれば行 2 の後ろに 30 フレーム止める。"""
    rows = [{"row": 1, "result": {"start": 0, "end": 30}}, {"row": 2, "result": {"start": 30, "end": 60}}]
    if hold_at is not None:
        rows[1]["hold"] = {"result": {"start": hold_at, "frames": 30, "src": 99, "speed": 0.0}}
    return {"project": project, "timeline_name": timeline, "started": started, "timeline": {"rows": rows}}


def test_record_clips_put_hold_in_its_row():
    clips = R.record_clips(record(hold_at=60))
    assert [(c["start"], c["end"], c["row"], c["tagged"]) for c in clips] == [
        (0, 30, 1, True), (30, 60, 2, True), (60, 90, 2, False)]
    assert R.record_clips({}) == []


def test_find_record_picks_newest_with_same_project_and_timeline(tmp_path):
    for name, d in (("old.json", record(started="2026-11-04 10:00:00")), ("new.json", record()),
                    ("other-tl.json", record(timeline="t2", started="2026-11-06 09:00:00")),
                    ("other-proj.json", record(project="q", started="2026-11-06 09:00:00"))):
        (tmp_path / name).write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert os.path.basename(R.find_record(str(tmp_path), "p", "t")) == "new.json"
    assert R.find_record(str(tmp_path), "p", "none") is None


class _Item:
    def __init__(self, start, end, markers=None):
        self.start, self.end, self.markers = start, end, markers or {}

    def GetStart(self):
        return 108000 + self.start

    def GetEnd(self):
        return 108000 + self.end

    def GetMarkers(self):
        return self.markers


class _Timeline:
    def __init__(self, items, markers):
        self.items, self.markers = items, markers

    def GetSetting(self, k):
        return "30"

    def GetStartFrame(self):
        return 108000

    def GetItemListInTrack(self, kind, n):
        return self.items

    def GetMarkers(self):
        return self.markers

    def GetName(self):
        return "t"

    def GetCurrentTimecode(self):
        return "01:00:00:00"


def run_main(monkeypatch, tmp_path, items, args=()):
    """模擬の Resolve で read_markers.main を流し、(終了コード, 結果の JSON, 画面の出力) を返す。"""
    tl = _Timeline(items, {45: {"name": "m1", "note": "", "color": "Blue"}, 75: {"name": "m2", "note": "", "color": "Blue"}})
    proj = type("P", (), {"GetName": lambda self: "p", "GetCurrentTimeline": lambda self: tl})()
    pm = type("PM", (), {"GetCurrentProject": lambda self: proj})()
    monkeypatch.setattr(R.C, "connect_or_exit", lambda *a: type("R", (), {"GetProjectManager": lambda self: pm})())
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path / "out"))
    patch_common(monkeypatch, "_CFG", None)
    monkeypatch.setattr(sys, "argv", ["read_markers.py", *args])
    code = R.main()
    with open(tmp_path / "out" / "read_markers" / "t.json", encoding="utf-8") as f:
        return code, json.load(f)


def write_record(tmp_path, d):
    folder = tmp_path / "out" / "sb2resolve"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sb.json").write_text(json.dumps(d), encoding="utf-8")


def test_main_uses_record_when_clips_have_no_tags(monkeypatch, tmp_path, capsys):
    """素材にマーカーがあって目印を付けられなかったタイムラインでも、記録の位置で行が決まる。止めたクリップは止めている所。"""
    write_record(tmp_path, record(hold_at=60))
    pin = {0: {"name": "pin 1", "customData": ""}}   # 素材のピン (目印ではない)
    code, out = run_main(monkeypatch, tmp_path, [_Item(0, 30, pin), _Item(30, 60, pin), _Item(60, 90, pin)])
    assert code == 0 and out["rows_from"] == "record"
    assert [(m["frame"], m["row"], m["in_hold"]) for m in out["markers"]] == [(45, 2, False), (75, 2, True)]
    assert "行は sb2resolve の記録から" in capsys.readouterr().out


def test_main_falls_back_to_tags_when_record_does_not_match(monkeypatch, tmp_path, capsys):
    """人がクリップを動かして記録と位置が合わなければ、目印で決め、その旨を出す。"""
    write_record(tmp_path, record())
    items = [_Item(0, 40, {0: {"customData": "row1"}}), _Item(40, 90, {0: {"customData": "row2"}})]
    code, out = run_main(monkeypatch, tmp_path, items)
    assert code == 0 and out["rows_from"] == "tags"
    assert [(m["frame"], m["row"]) for m in out["markers"]] == [(45, 2), (75, 2)]
    assert "位置が今のタイムラインと合わない" in capsys.readouterr().out


def test_main_fails_without_tags_or_record(monkeypatch, tmp_path, capsys):
    code, out = run_main(monkeypatch, tmp_path, [_Item(0, 30), _Item(30, 90)])
    assert code == 1 and out["rows_from"] == "tags" and out["record"] is None
    assert "位置の合う sb2resolve.py の記録も無い" in capsys.readouterr().out


def test_main_warns_when_only_some_rows_are_tagged(monkeypatch, tmp_path, capsys):
    """目印が一部の行にしか無ければ、目印の無い行が直前の行に数えられてずれうるので警告する。"""
    sbp = tmp_path / "sb.json"
    sbp.write_text(json.dumps({"rows": [{"caption": "a"}, {"caption": "b"}, {"caption": "c"}]}), encoding="utf-8")
    items = [_Item(0, 30, {0: {"customData": "row1"}}), _Item(30, 60), _Item(60, 90, {0: {"customData": "row3"}})]
    code, out = run_main(monkeypatch, tmp_path, items, ["--storyboard", str(sbp)])
    assert code == 0 and "目印のある行は 2 / 3" in capsys.readouterr().out


def test_sb2resolve_warns_rows_without_tag():
    assert S.marker_warnings({1: True, 2: True}) == []
    w = S.marker_warnings({n: n < 3 for n in range(1, 15)})
    assert len(w) == 1 and w[0].startswith("12 行 (3・4・5・6・7・8・9・10・11・12 ほか 行目) に行の目印のマーカーを付けられない")


def test_tag_conflicts_find_swapped_clips():
    """位置が全部合っても、同じ長さのクリップを入れ替えると目印の行が記録と食い違う。止めている所に目印があるのも食い違い。"""
    rec = R.record_clips(record(hold_at=60))   # 行 1 は 0〜30、行 2 は 30〜60、行 2 の止めている所は 60〜90
    assert R.tag_conflicts(rec, [{"start": 0, "end": 30, "row": 1}, {"start": 30, "end": 60, "row": 2},
                                 {"start": 60, "end": 90, "row": None}]) == []
    assert R.tag_conflicts(rec, [{"start": 0, "end": 30, "row": 2}, {"start": 30, "end": 60, "row": 1},
                                 {"start": 60, "end": 90, "row": None}]) == [(0, 30), (30, 60)]
    assert R.tag_conflicts(rec, [{"start": 0, "end": 30, "row": 1}, {"start": 30, "end": 60, "row": None},
                                 {"start": 60, "end": 90, "row": 2}]) == [(60, 90)]


def test_main_prefers_tags_when_record_conflicts(monkeypatch, tmp_path, capsys):
    """記録と位置は合うが、目印の行が食い違う (人が同じ長さのクリップを入れ替えた) ときは目印で決める。"""
    write_record(tmp_path, record(hold_at=60))
    items = [_Item(0, 30, {0: {"customData": "row1"}}), _Item(30, 60), _Item(60, 90, {0: {"customData": "row2"}})]
    code, out = run_main(monkeypatch, tmp_path, items)
    assert code == 0 and out["rows_from"] == "tags"
    assert [(m["frame"], m["row"], m["in_hold"]) for m in out["markers"]] == [(45, 1, True), (75, 2, False)]
    assert "記録の行がクリップの目印と食い違う (1 か所)" in capsys.readouterr().out
