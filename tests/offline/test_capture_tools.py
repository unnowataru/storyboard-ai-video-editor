"""録画まわりのスクリプト (pins.py、wallclock_to_tc.py、contact_sheet.py、marker.html) のテスト。OBS と Resolve は要らない。"""
import datetime as dt
import json
import re
import shutil
import subprocess

import pytest

import common as C
import contact_sheet
import pins
import settings
import wallclock_to_tc as W


# ---------- pins.py ----------
def test_pins_order_and_kinds():
    chapters = [{"start_time": "0.000000", "tags": {"title": "開始"}},
                {"start_time": "12.5", "tags": {"title": "名称未設定 1"}},
                {"start_time": "3.0", "tags": {"title": "名称未設定 2"}}]
    marks = {"pins": [{"t": 12.5, "name": "ピン1"}, {"t": 1.0, "name": "ピン2"}],
             "regions": [{"shape": "ellipse", "t": 3.0, "x": 1, "y": 2, "w": 3, "h": 4},
                         {"shape": "rect", "t": 0.5, "x": 5, "y": 6, "w": 7, "h": 8}]}
    rows = pins.ordered(pins.chapters_to_rows(chapters) + pins.marks_to_rows(marks))
    assert [(r["t"], r["kind"]) for r in rows] == [
        (0.0, "ピン (OBS)"), (0.5, "四角"), (1.0, "ピン"), (3.0, "ピン (OBS)"), (3.0, "丸"), (12.5, "ピン (OBS)"),
        (12.5, "ピン")]
    # 領域は marks.json の中の番号を持つ (track.py --template に渡す番号)
    assert {r["index"] for r in rows if r["kind"] in ("丸", "四角")} == {0, 1}


def test_pins_keeps_every_chapter():
    """0 秒のチャプターも、録画の終わりをまたぐ最後のチャプターも、全部出す。"""
    def ch(start, end, title):
        return {"start_time": start, "end_time": end, "tags": {"title": title}}
    chapters = [ch("0.000000", "0.000000", "a"), ch("0.000000", "60.1", "b"), ch("60.1", "200.2", "c"),
                ch("200.2", "215.8", "d")]
    assert [(r["t"], r["note"]) for r in pins.chapters_to_rows(chapters)] == [
        (0.0, "a"), (0.0, "b"), (60.1, "c"), (200.2, "d")]
    assert pins.chapters_to_rows([]) == []


def test_pins_candidates_order(monkeypatch, tmp_path):
    monkeypatch.setenv("RESOLVE_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("RESOLVE_DOWNLOADS", str(tmp_path / "dl"))
    settings._CFG = None
    try:
        c = pins.marks_candidates(str(tmp_path / "rec" / "a.mp4"))
    finally:
        settings._CFG = None
    assert [p.replace("\\", "/").split("/")[-2:] for p in c] == [["rec", "a.marks.json"], ["a", "a.marks.json"],
                                                                 ["dl", "a.marks.json"]]


# ---------- wallclock_to_tc.py と時刻の記録 ----------
def test_log_line_round_trip():
    """録画ツールの時刻の記録の行を wallclock_to_tc.py が読める。一時停止中の時刻は録画に入らない。

    行の形は docs/recording.md。stop の行は録画の時刻が空で、操作の後ろにパスが続く。
    """
    lines = ["2026-10-14 10:00:00\t00:00:02.000\tstart\t場面 1\n",
             "2026-10-14 10:05:00\t00:05:02.000\tpause\t\n",
             "2026-10-14 10:10:00\t00:05:02.000\tresume\t\n",
             "2026-10-14 10:12:00\t\tstop x.mp4\t\n"]
    sp, day = W.spans(lines)
    assert day == dt.date(2026, 10, 14) and len(sp) == 2
    assert W.to_tc(sp, day, "10:00:00") == pytest.approx(2.0)
    assert W.to_tc(sp, day, "10:01:30") == pytest.approx(92.0)
    assert W.to_tc(sp, day, "10:07:00") is None            # 一時停止中
    assert W.to_tc(sp, day, "10:11:00") == pytest.approx(302.0 + 60)
    assert W.to_tc(sp, day, "10:13:00") is None            # 停止の後
    assert C.sec_to_mss(W.to_tc(sp, day, "10:11:00")) == "6:02.0"


@pytest.mark.parametrize("tc, sec", [("00:00:02.000", 2.0), ("00:05:02.500", 302.5), ("01:02:03.250", 3723.25),
                                     ("10:09:30.000", 10 * 3600 + 9 * 60 + 30.0)])
def test_record_timecode_reads_with_tc_to_sec(tc, sec):
    """記録の録画の時刻 'hh:mm:ss.sss' は common.tc_to_sec で秒にする。"""
    h, m, s = tc.split(":")
    assert C.tc_to_sec(tc) == int(h) * 3600 + int(m) * 60 + float(s) == sec


def test_wallclock_open_span_and_crlf():
    """停止の行が無ければ、最後の開始・再開から先は録画が続いているとみなす。CRLF の記録も読める。"""
    sp, day = W.spans(["2026-10-14 09:00:00\t\tstart\t\r\n", "壊れた行\r\n"])
    assert W.to_tc(sp, day, "10:00:00") == pytest.approx(3600.0)


# ---------- contact_sheet.py ----------
def test_contact_sheet_filter_escapes_font_path():
    cs = C.Config().section("contact_sheet")
    vf = contact_sheet.build_filter(cs, 30.0, "X:" + chr(92) + "f" + chr(92) + "a.ttf", 2, 480, 5, 4)
    assert "fontfile='X\\:/f/a.ttf'" in vf
    assert "%{pts\\:hms\\:30.0}" in vf and vf.endswith("tile=5x4") and vf.startswith("fps=1/2:round=up,")
    assert f"fontcolor={cs['text_color']}" in vf


def test_marker_html_saves_marks_json_shape():
    """marker.html が保存する形 (pins.py と track.py --template が読む項目) が変わっていないか。"""
    with open(C.SCRIPTS + "/marker.html", encoding="utf-8") as f:
        html = f.read()
    for key in ('pins: []', 'regions: []', 'name: `ピン${', 'shape, t:', '".marks.json"'):
        assert key in html, key


def test_marker_html_pinkey():
    """?pinkey= で足すキーの読み方。修飾キーはちょうど合うときだけ、大文字小文字は区別しない、URL の + は空白で来る。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js が無い")
    with open(C.SCRIPTS + "/marker.html", encoding="utf-8") as f:
        html = f.read()
    src = re.search(r"function parsePinKey\(s\) \{.*?\n\}\n", html, re.S).group(0)
    src += re.search(r"const matchesPinKey = .*?;\n", html, re.S).group(0)
    cases = [("ctrl+alt+shift+F2", {"key": "F2", "ctrlKey": True, "altKey": True, "shiftKey": True}),
             ("ctrl alt shift f2", {"key": "F2", "ctrlKey": True, "altKey": True, "shiftKey": True}),
             ("ctrl+alt+shift+F2", {"key": "F2", "ctrlKey": True, "altKey": True}),
             ("ctrl+alt+shift+F2", {"key": "F2", "ctrlKey": True, "altKey": True, "shiftKey": True, "metaKey": True}),
             ("Enter", {"key": "Enter"}), ("enter", {"key": "Enter", "shiftKey": True}),
             ("shift+a", {"key": "A", "shiftKey": True}), ("ctrl +", {"key": "+", "ctrlKey": True}),
             (None, {"key": "p"})]
    js = src + "const cases = " + json.dumps(cases) + ";\n" + """
const out = cases.map(([s, ev]) => {
  const e = Object.assign({ ctrlKey: false, altKey: false, shiftKey: false, metaKey: false }, ev);
  return matchesPinKey(parsePinKey(s), e);
});
console.log(JSON.stringify(out));"""
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(r.stdout) == [True, True, False, False, True, False, True, True, False]
