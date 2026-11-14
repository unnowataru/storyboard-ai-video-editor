"""area_sheet.py (見る範囲が変わったフレームだけを並べる) のテスト。

合成した動画 (灰色の地に白い板。見る範囲の中に 1〜2 フレームだけ色の四角が出て、範囲の外では四角が動き続ける) で、
短い漏れが画像と JSON に出て、範囲の外の変化は拾わないことを確認する。
動画は作業フォルダーの offline-tests\\run-<番号>\\area_sheet (実行ごとに別の名前。conftest の offline_work) に作って消す。FFmpeg が無ければ動画のテストは飛ばす。
"""
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

from conftest import patch_common

import area_sheet as A
import common as C

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

W, H, TOTAL = 640, 360, 120
ADDR = {"name": "addr", "x": 21, "y": 21, "w": 399, "h": 29}      # 奇数の位置と大きさ (YUV のままでは切れない)
PANEL = {"name": "panel", "x": 400, "y": 180, "w": 200, "h": 100}
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts",
                      "area_sheet.py")


def _ffmpeg():
    try:
        return C.Config().tool("ffmpeg")
    except SystemExit:
        return None


need_ffmpeg = pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg が無い")
# どの PNG より小さい。1 MB = 1024×1024 バイトなので、約 2 バイトになる
TINY_MB = 0.000002


def _fallback_font_src():
    """Arial / Meiryo が無い環境で、代わりに使う書体ファイル。"""
    return next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
                 if os.path.isfile(p)), None)


def _link_fallback_fonts(dest_dir):
    """代替の書体を dest_dir の中だけに置く。固定の場所は作らない。"""
    src = _fallback_font_src()
    if src is None:
        return False
    os.makedirs(dest_dir, exist_ok=True)
    for name in ("arial.ttf", "meiryo.ttc"):
        dest = os.path.join(dest_dir, name)
        if not os.path.lexists(dest):
            os.symlink(src, dest)
    return True


@pytest.fixture(autouse=True)
def _fonts_for_sheets(monkeypatch, tmp_path):
    if C.Config().font_file("Arial") or C.Config().font_file("Meiryo"):
        return
    d = tmp_path / "fonts"
    if _link_fallback_fonts(str(d)):
        monkeypatch.setenv("RESOLVE_FONTS_DIR", str(d))
        patch_common(monkeypatch, "_CFG", None)


def _ffmpeg_still_running(pid):
    """/proc/<pid> があれば cmdline に ffmpeg が残っているか。/proc が無ければ None (失敗にはしない)。"""
    if not os.path.isdir("/proc"):
        return None
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read()
    except FileNotFoundError:
        return False
    return b"ffmpeg" in cmd


def _assert_ffmpeg_reaped(proc):
    assert proc.poll() is not None
    assert proc.stdout is None or proc.stdout.closed
    if proc.stderr is not None:
        assert proc.stderr.closed
    alive = _ffmpeg_still_running(proc.pid)
    # /proc が無い環境では None。poll・パイプ・スレッドの確認は上で済んでいる。
    if alive is not None:
        assert alive is False
    leaked = [t.name for t in threading.enumerate() if t.name == "area-sheet-ffmpeg-stderr" and t.is_alive()]
    assert leaked == []


class _DoneProc:
    def __init__(self, pid=1, code=0, stdout=None, stderr=None):
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self._code = code

    def poll(self):
        return self._code


def _hide_proc(monkeypatch):
    real = os.path.isdir
    monkeypatch.setattr(os.path, "isdir", lambda p: False if p == "/proc" else real(p))


def test_missing_proc_is_not_a_reap_failure(monkeypatch):
    """/proc が無いときは、回収済みの子を失敗にしない。Linux で /proc/<pid> があるときの確認は残す。"""
    _hide_proc(monkeypatch)
    assert _ffmpeg_still_running(os.getpid()) is None
    _assert_ffmpeg_reaped(_DoneProc())


def test_reap_checks_still_fail_without_proc(monkeypatch, tmp_path):
    """/proc が無くても、終わっていない子・開いたパイプ・標準エラーのスレッドは失敗にする。"""
    _hide_proc(monkeypatch)
    with pytest.raises(AssertionError):
        _assert_ffmpeg_reaped(_DoneProc(code=None))
    live = open(tmp_path / "stdout", "wb")
    try:
        with pytest.raises(AssertionError):
            _assert_ffmpeg_reaped(_DoneProc(stdout=live))
    finally:
        live.close()
    release = threading.Event()
    started = threading.Event()

    def run():
        started.set()
        release.wait(5)

    th = threading.Thread(target=run, name="area-sheet-ffmpeg-stderr")
    th.start()
    assert started.wait(5)
    try:
        with pytest.raises(AssertionError):
            _assert_ffmpeg_reaped(_DoneProc())
    finally:
        release.set()
        th.join(5)


def test_proc_cmdline_is_checked_when_present():
    """/proc/<pid> があるときは cmdline を読む。プロセスが無ければ残っていない。"""
    pid = os.getpid()
    if not os.path.isdir(f"/proc/{pid}"):
        pytest.skip("/proc が無い")
    cmd = open(f"/proc/{pid}/cmdline", "rb").read()
    assert _ffmpeg_still_running(pid) is (b"ffmpeg" in cmd)
    assert _ffmpeg_still_running(1 << 30) is False


def _watch_ffmpeg(monkeypatch):
    opened = []
    real = A.C.ffmpeg_popen

    def wrapped(*args, **kwargs):
        proc = real(*args, **kwargs)
        opened.append(proc)
        return proc

    monkeypatch.setattr(A.C, "ffmpeg_popen", wrapped)
    return opened


# ---------- 部品 ----------
def test_parse_and_load_areas(tmp_path):
    assert A.parse_area("10,20,30,40", 1) == {"name": "area1", "x": 10, "y": 20, "w": 30, "h": 40}
    assert A.parse_area("addr=1,2,3,4", 2)["name"] == "addr"
    with pytest.raises(ValueError):
        A.parse_area("1,2,3", 1)
    p = tmp_path / "areas.json"
    p.write_text(json.dumps({"areas": [{"name": "list", "x": 1, "y": 2, "w": 3, "h": 4}, {"x": 5, "y": 6, "w": 7, "h": 8}]}),
                 encoding="utf-8")
    areas = A.load_areas(["9,9,9,9"], str(p))
    assert [a["name"] for a in areas] == ["list", "area2", "area3"]
    p.write_text(json.dumps([{"x": 1, "y": 2, "w": 3}]), encoding="utf-8")
    with pytest.raises(ValueError):
        A.load_areas([], str(p))


def test_area_problems():
    assert A.area_problems([ADDR, PANEL], W, H) == []
    assert A.area_problems([], W, H)
    bad = A.area_problems([{**ADDR, "x": 300}, {**ADDR, "name": "a b"}, {**PANEL, "name": "addr", "w": 0}], W, H)
    assert len(bad) == 4   # はみ出し・名前の字・重なった名前・大きさ 0


def test_area_names_differing_only_in_case_are_rejected():
    """Windows ではファイル名が大文字小文字を区別しないので、addr と ADDR は同じ名前として拒む。"""
    bad = A.area_problems([ADDR, {**PANEL, "name": "ADDR"}], W, H)
    assert len(bad) == 1 and bad[0].startswith("ADDR: 名前が重なっている")


def test_filter_crops_even_then_exact():
    f = A.build_filter([ADDR])
    # YUV のまま偶数の位置と大きさ (20,20 から 400x30) で切り、RGB に直してから 1,1 ずらして 399x29 を切る
    assert f == "[0:v:0]crop=400:30:20:20,format=rgb24,crop=399:29:1:1,pad=399:29:0:0[out]"
    f2 = A.build_filter([ADDR, PANEL])
    assert f2.startswith("[0:v:0]split=2[s0][s1];") and f2.endswith("[c0][c1]vstack=inputs=2[out]")
    assert "pad=399:100:0:0" in f2   # 狭い範囲は広い範囲の幅にそろえる


def test_changed_pixels():
    a = np.zeros((4, 4, 3), np.uint8)
    b = a.copy()
    b[0, 0] = (30, 0, 0)
    b[1, 1] = (0, 10, 0)
    assert A.changed_pixels(a, b, 24) == 1
    assert A.changed_pixels(a, b, 5) == 2


# ---------- 合成の動画 ----------
@pytest.fixture(scope="module")
def work(offline_work):
    d = os.path.join(offline_work, "area_sheet")
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def video(work):
    """灰色の地に白い板。40〜41 フレームに赤い四角 (addr の中)、90 フレームに黄色の四角 (panel の中)。
    範囲の外 (y=310) では水色の四角が毎フレーム 5 px ずつ動く (drawbox の位置の式はフレームごとには変わらないので overlay で動かす)。"""
    out = os.path.join(work, "leak.mp4")
    fc = (f"color=c=0x404040:size={W}x{H}:rate=30,"
          "drawbox=x=20:y=20:w=400:h=30:color=white:t=fill,"
          "drawbox=x=100:y=25:w=80:h=20:color=red:t=fill:enable='between(n,40,41)',"
          "drawbox=x=420:y=200:w=60:h=20:color=yellow:t=fill:enable='eq(n,90)'[bg];"
          "color=c=cyan:size=40x40:rate=30[box];"
          "[bg][box]overlay=x='mod(n*5,300)':y=310:shortest=1[v]")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", fc, "-map", "[v]",
                    "-frames:v", str(TOTAL), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", out], check=True)
    return out


def _settings(**kw):
    s = C.Config().section("area_sheet")
    s.update(kw)
    return dict(change_level=s["change_level"], change_pixels=s["change_pixels"], rows=s["rows"], cols=s["cols"],
                max_width=s["max_width"])


LEFT = {"name": "left", "x": 10, "y": 10, "w": 100, "h": 40}
RIGHT = {"name": "right", "x": 180, "y": 10, "w": 100, "h": 40}


def _png_names(out):
    return sorted(f for f in os.listdir(out) if f.endswith(".png"))


def _assert_image_accounting(res, out):
    """JSON の枚数・バイトが、ディスクの PNG と一致する。"""
    names = _png_names(out)
    sheets = [os.path.basename(p) for a in res["areas"] for p in a["sheets"]]
    assert res["images_written"] == len(sheets) == len(names)
    assert sorted(sheets) == names
    assert res["output_bytes"] == sum(os.path.getsize(os.path.join(out, f)) for f in names)


def _write_clip(path, draws, frames=60):
    """灰色の地に色の板を出す合成動画。draws は (x, 色, enable 式)。可逆に近い crf 0 で、圧縮のにじみを避ける。"""
    parts = ["color=c=0x808080:size=320x180:rate=30"]
    for x, color, enable in draws:
        parts.append(f"drawbox=x={x}:y=10:w=100:h=40:color={color}:t=fill:enable='{enable}'")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", ",".join(parts) + "[v]",
                    "-map", "[v]", "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                    "-pix_fmt", "yuv420p", path], check=True)
    return path


def _kept(res):
    return {a["name"]: [k["frame"] for k in a["kept"]] for a in res["areas"]}


@need_ffmpeg
def test_short_leaks_are_kept_and_outside_ignored(work, video):
    out = os.path.join(work, "scan")
    res = A.scan(video, [dict(ADDR), dict(PANEL)], out, **_settings())
    assert res["frames_read"] == TOTAL
    kept = {a["name"]: [k["frame"] for k in a["kept"]] for a in res["areas"]}
    # 最初のフレームと、漏れが出たフレーム・消えたフレームだけ。外で動く四角は拾わない
    # (既定の閾値では 41 フレーム目の圧縮のにじみ (色の差 32 を超える画素 16) は残らない)
    assert kept == {"addr": [0, 40, 42], "panel": [0, 90, 91]}
    lower = A.scan(video, [dict(ADDR)], os.path.join(work, "scan1"), **_settings(change_pixels=1))
    assert [k["frame"] for k in lower["areas"][0]["kept"]][:2] == [0, 40]   # 閾値を下げても範囲の外は拾わない
    assert all(40 <= k["frame"] <= 42 for k in lower["areas"][0]["kept"][1:])
    addr = res["areas"][0]
    assert addr["kept"][1]["time"] == round(40 / 30, 3) and addr["kept"][0]["changed_pixels"] is None
    assert len(addr["sheets"]) == 1
    sheet = np.asarray(Image.open(addr["sheets"][0]).convert("RGB")).astype(int)
    red = (sheet[..., 0] > 200) & (sheet[..., 1] < 60) & (sheet[..., 2] < 60)
    assert red.sum() > 1000   # 赤い四角 (80x20) が画像に出ている
    vm = np.asarray(Image.open(res["areas"][1]["sheets"][0]).convert("RGB")).astype(int)
    yellow = (vm[..., 0] > 200) & (vm[..., 1] > 200) & (vm[..., 2] < 60)
    assert yellow.sum() > 800    # 黄色の四角 (60x20) が画像に出ている
    cyan = (vm[..., 0] < 60) & (vm[..., 1] > 200) & (vm[..., 2] > 200)
    assert cyan.sum() == 0       # 範囲の外の水色の四角は入らない


@need_ffmpeg
def test_area_on_moving_box_keeps_every_frame(work, video):
    """範囲の中が毎フレーム変われば、全部のフレームが残る (変化を見落とさない)。"""
    box = {"name": "moving", "x": 0, "y": 300, "w": 400, "h": 60}
    res = A.scan(video, [box], os.path.join(work, "moving"), **_settings(rows=50, cols=2))
    assert res["areas"][0]["kept_count"] == TOTAL
    assert len(res["areas"][0]["sheets"]) == 2   # 1 枚に 100 まで


@need_ffmpeg
def test_command_writes_json_and_splits_sheets(work, video):
    out = os.path.join(work, "cmd")
    os.makedirs(out)
    open(os.path.join(out, "addr_07.png"), "wb").close()   # 前回の画像は消える
    r = subprocess.run([sys.executable, SCRIPT, video, "--area", "addr=21,21,399,29", "--area", "panel=400,180,200,100",
                        "--out", out, "--rows", "2"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    line = r.stdout.strip().splitlines()
    assert len(line) == 1 and line[0].startswith("OK area_sheet: 120 フレームを見て") and "addr 3・panel 3" in line[0]
    j = json.load(open(os.path.join(out, "area-sheet.json"), encoding="utf-8"))
    assert [a["kept_count"] for a in j["areas"]] == [3, 3] and j["elapsed_sec"] >= 0
    pngs = ["addr_01.png", "addr_02.png", "panel_01.png", "panel_02.png"]
    assert sorted(f for f in os.listdir(out) if f.endswith(".png")) == pngs
    assert j["incomplete"] is False and j["limit_hit"] is None
    assert j["images_written"] == 4 and j["changed_frames"] == 6
    assert j["output_bytes"] == sum(os.path.getsize(os.path.join(out, f)) for f in pngs) and j["output_bytes"] > 0


@need_ffmpeg
def test_previous_sheets_with_other_case_are_removed(work, video):
    """前回 ADDR で作った画像は、今回 addr で同じ出力先に流した後に残らない。範囲と関係の無いファイルは消さない。"""
    out = os.path.join(work, "case")
    A.scan(video, [{**ADDR, "name": "ADDR"}], out, **_settings(rows=1))
    before = sorted(f for f in os.listdir(out) if f.endswith(".png"))
    assert len(before) == 3 and all(f.startswith("ADDR_") for f in before)
    for f in ("panel_01.png", "addr.png", "ADDR_notes.txt"):
        open(os.path.join(out, f), "wb").close()
    res = A.scan(video, [dict(ADDR)], out, **_settings())
    pngs = sorted(f for f in os.listdir(out) if f.endswith(".png"))
    assert [f.casefold() for f in pngs] == ["addr.png", "addr_01.png", "panel_01.png"]
    assert len(res["areas"][0]["sheets"]) == 1 and os.path.exists(os.path.join(out, "ADDR_notes.txt"))


@need_ffmpeg
def test_command_rejects_area_outside_video(work, video):
    r = subprocess.run([sys.executable, SCRIPT, video, "--area", "600,300,100,100", "--out", os.path.join(work, "bad")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and r.stdout.startswith("NG area_sheet: 動かせない") and "はみ出す" in r.stdout


def test_sheet_height_is_capped(tmp_path):
    """高い範囲は、1 枚の高さが max_height を超えないよう段を減らして画像を分ける (少なくとも 1 段)。"""
    from PIL import ImageFont
    font = ImageFont.load_default()
    area = {"name": "tall", "x": 0, "y": 0, "w": 50, "h": 750}
    w = A.SheetWriter(area, str(tmp_path), 40, 1, 1600, 30, font, "yellow", "black", max_height=4000)
    assert w.rows == 5    # (4000 + 2) // (750 + 2)
    for n in range(12):
        w.add(n, np.zeros((750, 50, 3), np.uint8))
    w.flush()
    assert len(w.sheets) == 3 and max(Image.open(p).size[1] for p in w.sheets) <= 4000
    low = A.SheetWriter(area, str(tmp_path), 40, 1, 1600, 30, font, "yellow", "black", max_height=100)
    assert low.rows == 1  # 1 段で上限を超えても 1 段は並べる
    small = A.SheetWriter({**area, "h": 20}, str(tmp_path), 40, 1, 1600, 30, font, "yellow", "black", max_height=4000)
    assert small.rows == 40   # 低い範囲は rows のまま


@need_ffmpeg
def test_audio_longer_than_video_is_ok(work):
    """音声が映像より長い動画でも、見込みは映像の stream のフレームの数で数え、OK になる。"""
    out = os.path.join(work, "av.mp4")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
    assert C.probe_video(out)["duration"] > 2.5   # 入れ物全体の長さは音声に引きずられる
    info, stream = A.probe(out)
    assert info == C.probe_video(out)   # 幅・高さ・fps・長さは probe_video と同じ値
    assert A.expected_frames(stream, 30, 3.0) == 60
    r = subprocess.run([sys.executable, SCRIPT, out, "--area", "10,10,100,50", "--out", os.path.join(work, "av")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and r.stdout.startswith("OK area_sheet: 60 フレームを見て"), r.stdout + r.stderr


def test_expected_frames_order():
    """見込みは nb_frames、無ければ映像の stream の長さ x fps、どちらも無ければ入れ物全体の長さ x fps。"""
    assert A.expected_frames({"nb_frames": "61", "duration": "2.0"}, 30, 9.0) == 61
    assert A.expected_frames({"nb_frames": "N/A", "duration": "2.0"}, 30, 9.0) == 60
    assert A.expected_frames({"nb_frames": "0", "duration": "2.0"}, 30, 9.0) == 60
    assert A.expected_frames({}, 30, 2.5) == 75   # mkv など、映像の stream に数も長さも無い入れ物
    assert A.expected_frames(None, 30, 2.5) == 75


@need_ffmpeg
def test_scan_probes_the_video_once(work, video, monkeypatch):
    """scan は同じ動画に ffprobe を 1 回だけ呼ぶ (幅・高さ・fps・長さと、見込みの nb_frames・映像の長さを同時に読む)。"""
    calls = []
    real = C.run_tool

    def run_tool(name, args):
        calls.append(name)
        return real(name, args)
    patch_common(monkeypatch, "run_tool", run_tool)
    res = A.scan(video, [dict(ADDR)], os.path.join(work, "probe-once"), **_settings())
    assert calls.count("ffprobe") == 1, calls
    assert res["frames_read"] == res["frames_expected"] == TOTAL


def test_non_positive_limits_are_rejected(monkeypatch):
    """0 と負と数でない上限は誤り。0 は無制限ではない。検査より前に FFmpeg は起動しない。"""
    def _no_ffmpeg(*_a, **_k):
        raise AssertionError("FFmpeg を起動した")

    monkeypatch.setattr(A.C, "ffmpeg_popen", _no_ffmpeg)
    for kwargs in ({"max_sheets": 0}, {"max_sheets": -2}, {"max_frames": 0}, {"max_frames": False},
                   {"max_output_mb": 0}, {"max_output_mb": -0.5}, {"max_output_mb": float("nan")},
                   {"max_output_mb": 1e-12}):
        with pytest.raises(ValueError):
            A.scan("unused.mp4", [dict(ADDR)], "out", **_settings(), **kwargs)
    for args in (["--max-sheets", "0"], ["--max-sheets", "-3"], ["--max-frames", "0"], ["--max-frames", "x"],
                 ["--max-output-mb", "0"], ["--max-output-mb", "-1"], ["--max-output-mb", "no"],
                 ["--max-output-mb", "1e-12"]):
        r = subprocess.run([sys.executable, SCRIPT, "unused.mp4", "--area", "1,1,1,1", *args],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode != 0, args
        assert "上限" in (r.stdout + r.stderr), (args, r.stdout, r.stderr)


@need_ffmpeg
def test_three_frame_change_is_kept(work):
    """3 フレームだけの変化は、出たフレームと消えたフレームが残る。上限を大きくしても未完了にしない。"""
    outv = os.path.join(work, "three.mp4")
    fc = ("color=c=0x404040:size=320x180:rate=30,"
          "drawbox=x=10:y=10:w=80:h=20:color=red:t=fill:enable='between(n,20,22)'[v]")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", fc, "-map", "[v]",
                    "-frames:v", "60", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", outv], check=True)
    res = A.scan(outv, [{"name": "box", "x": 10, "y": 10, "w": 80, "h": 20}], os.path.join(work, "three"),
                 **_settings(), max_sheets=20, max_frames=50, max_output_mb=20)
    frames = [k["frame"] for k in res["areas"][0]["kept"]]
    assert frames[0] == 0 and 20 in frames and 23 in frames
    assert res["frames_read"] == 60 and res["incomplete"] is False and res["limit_hit"] is None
    assert res["images_written"] == len(res["areas"][0]["sheets"]) >= 1
    assert res["output_bytes"] > 0 and res["elapsed_sec"] >= 0 and res["changed_frames"] == len(frames)


@need_ffmpeg
def test_high_limits_match_unlimited_detection(work, video):
    """上限が足りているときは、上限なしと同じフレームが残る。"""
    area = [dict(ADDR)]
    base = A.scan(video, area, os.path.join(work, "high-base"), **_settings())
    capped = A.scan(video, area, os.path.join(work, "high-cap"), **_settings(),
                    max_sheets=100, max_frames=1000, max_output_mb=100)
    assert [k["frame"] for k in capped["areas"][0]["kept"]] == [k["frame"] for k in base["areas"][0]["kept"]]
    assert capped["incomplete"] is False and capped["frames_read"] == base["frames_read"] == TOTAL
    assert capped["changed_frames"] == base["areas"][0]["kept_count"]
    assert capped["images_written"] == len(capped["areas"][0]["sheets"])
    assert capped["output_bytes"] == sum(os.path.getsize(p) for p in capped["areas"][0]["sheets"])
    assert capped["elapsed_sec"] >= 0


@need_ffmpeg
@pytest.mark.parametrize("limit_name,kwargs", [
    ("max_sheets", {"max_sheets": 1, "rows": 1, "cols": 1}),
    ("max_frames", {"max_frames": 2}),
    ("max_output_mb", {"max_output_mb": TINY_MB, "rows": 1, "cols": 1}),
])
def test_every_frame_change_is_incomplete(work, video, monkeypatch, limit_name, kwargs):
    """全フレームが変わる動画は上限で止まり、成功でも漏れなしでもない。FFmpeg は残らない。"""
    opened = _watch_ffmpeg(monkeypatch)
    extra = dict(kwargs)
    settings = _settings(rows=extra.pop("rows", 40), cols=extra.pop("cols", 1))
    out = os.path.join(work, "every-" + limit_name)
    res = A.scan(video, [{"name": "moving", "x": 0, "y": 300, "w": 400, "h": 60}], out, **settings, **extra)
    assert res["incomplete"] is True and res["limit_hit"] == limit_name
    assert res["frames_read"] < TOTAL
    assert 1 <= res["images_written"] == len(res["areas"][0]["sheets"]) == len(_png_names(out))
    assert res["output_bytes"] == sum(os.path.getsize(p) for p in res["areas"][0]["sheets"]) > 0
    assert res["changed_frames"] == res["areas"][0]["kept_count"]
    assert res["elapsed_sec"] >= 0
    if limit_name == "max_frames":
        assert res["changed_frames"] == 2
    if limit_name == "max_sheets":
        assert res["images_written"] == 1 and res["changed_frames"] == 1
    if limit_name == "max_output_mb":
        assert res["output_bytes"] > int(TINY_MB * 1024 * 1024)
    text = json.dumps(res, ensure_ascii=False)
    assert "漏れなし" not in text and "漏れ0" not in text
    assert opened, "FFmpeg を起動していない"
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


def test_byte_cap_blocks_a_later_sheet_after_another_limit(tmp_path):
    """先に max_frames が当たったあと、別の範囲の開きの段が容量より大きい PNG になっても書かない。

    枚数の上限はまだ残っている。止めるのは output_bytes と max_bytes の比較だけで、limit_hit は max_frames のまま。
    書けなかった段は捨てる。容量を超えたバイト数は、完了した出力にはしない。
    """
    from PIL import ImageFont
    font = ImageFont.load_default()
    cap = A._OutputCap(max_sheets=5, max_frames=2, max_output_mb=TINY_MB)
    cap.changed = 2
    assert cap.frame_full() is True and cap.limit_hit == "max_frames"
    writers, kept = [], []
    for area in (dict(LEFT), dict(RIGHT)):
        w = A.SheetWriter(area, str(tmp_path), 2, 1, 1600, 30, font, "yellow", "black")
        img = np.zeros((area["h"], area["w"], 3), np.uint8)
        img[..., 0] = 200
        w.add(0, img)
        assert w.cells and not w.sheets
        writers.append(w)
        kept.append([{"frame": 0, "time": 0.0, "changed_pixels": None}])
    A._flush_pending(writers, kept, cap)
    assert cap.limit_hit == "max_frames" and cap.bytes_over()
    assert cap.can_write_sheet() is False
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["left_01.png"]
    assert kept[0] and kept[1] == [] and writers[1].cells == []
    assert cap.output_bytes == os.path.getsize(next(tmp_path.glob("*.png"))) > cap.max_bytes


def test_substitute_font_is_inside_the_test_tmp(tmp_path):
    """代替の書体はテストの一時ディレクトリに置き、固定の名前では残さない。"""
    font_dir = tmp_path / "fonts"
    link = font_dir / "arial.ttf"
    d = os.environ.get("RESOLVE_FONTS_DIR", "")
    if link.is_symlink():
        assert os.path.abspath(d) == str(font_dir)
    assert "area-sheet-test-fonts" not in d


@need_ffmpeg
def test_byte_cap_after_full_read_is_incomplete(work, video):
    """全部のフレームを見ても、最後の画像が容量を超えたら未完了。短い変化の検出自体は落とさない。"""
    out = os.path.join(work, "bytes-end")
    res = A.scan(video, [dict(ADDR)], out, **_settings(), max_output_mb=TINY_MB)
    assert res["frames_read"] == TOTAL
    assert [k["frame"] for k in res["areas"][0]["kept"]] == [0, 40, 42]
    assert res["incomplete"] is True and res["limit_hit"] == "max_output_mb"
    assert res["images_written"] >= 1 and res["output_bytes"] > int(TINY_MB * 1024 * 1024)
    _assert_image_accounting(res, out)


@need_ffmpeg
def test_frame_limit_then_open_sheet_stops_at_the_byte_cap(work, monkeypatch):
    """2 つの範囲で先に max_frames が当たり、もう一方に開きの段が残る。その段を容量より大きい PNG にはしない。

    枚数の上限は残しているので、枚数では止まらない。limit_hit は max_frames のまま、incomplete は true。
    容量を超えた output_bytes を完了とは扱わず、漏れなしとは出さない。
    """
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "bytes-after-frames.mp4"),
                        [(10, "red", "gte(n,8)"), (180, "blue", "gte(n,8)")])
    out = os.path.join(work, "bytes-after-frames")
    res = A.scan(video, [dict(LEFT), dict(RIGHT)], out, **_settings(rows=2, cols=1),
                 max_frames=2, max_sheets=5, max_output_mb=TINY_MB)
    assert res["incomplete"] is True and res["limit_hit"] == "max_frames"
    assert res["frames_read"] == 9 and res["images_written"] == 1
    assert _png_names(out) == ["left_01.png"]
    assert _kept(res) == {"left": [0], "right": []}
    assert res["output_bytes"] > int(TINY_MB * 1024 * 1024)
    _assert_image_accounting(res, out)
    text = json.dumps(res, ensure_ascii=False)
    assert "漏れなし" not in text and "漏れ0" not in text
    assert A.summary(res).startswith("area_sheet: 未完了。")
    cmd = os.path.join(work, "bytes-after-frames-cmd")
    r = subprocess.run([sys.executable, SCRIPT, video, "--area", "left=10,10,100,40", "--area", "right=180,10,100,40",
                        "--out", cmd, "--rows", "2", "--cols", "1", "--max-frames", "2", "--max-sheets", "5",
                        "--max-output-mb", str(TINY_MB)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("NG area_sheet: 未完了。")
    assert "OK area_sheet" not in r.stdout and "漏れなし" not in r.stdout
    j = json.load(open(os.path.join(cmd, "area-sheet.json"), encoding="utf-8"))
    assert j["incomplete"] is True and j["limit_hit"] == "max_frames"
    assert j["images_written"] == len(_png_names(cmd)) == 1 and _png_names(cmd) == ["left_01.png"]
    assert j["output_bytes"] > int(TINY_MB * 1024 * 1024)
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_max_frames_counts_across_areas(work, video, monkeypatch):
    """残すフレームの上限は見る範囲をまたいで数える。達した先の範囲は覚えない。"""
    opened = _watch_ffmpeg(monkeypatch)
    res = A.scan(video, [dict(ADDR), {"name": "moving", "x": 0, "y": 300, "w": 400, "h": 60}],
                 os.path.join(work, "cross"), **_settings(), max_frames=1)
    assert res["incomplete"] is True and res["limit_hit"] == "max_frames"
    assert res["changed_frames"] == 1 and sum(len(a["kept"]) for a in res["areas"]) == 1
    assert res["frames_read"] < TOTAL
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_command_over_limit_is_incomplete(work, video):
    """コマンドは上限超過を NG・未完了にする。漏れなしとは出さない。"""
    out = os.path.join(work, "capcmd")
    r = subprocess.run([sys.executable, SCRIPT, video, "--area", "moving=0,300,400,60", "--out", out,
                        "--rows", "1", "--cols", "1", "--max-sheets", "1"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode != 0, r.stdout + r.stderr
    assert r.stdout.startswith("NG area_sheet: 未完了。")
    assert "漏れなし" not in r.stdout and "OK area_sheet" not in r.stdout
    assert "max_sheets" in r.stdout and "読んだフレーム" in r.stdout and "画像" in r.stdout
    j = json.load(open(os.path.join(out, "area-sheet.json"), encoding="utf-8"))
    assert j["incomplete"] is True and j["limit_hit"] == "max_sheets"
    assert j["frames_read"] < TOTAL and j["images_written"] == len(_png_names(out)) == 1
    assert j["output_bytes"] > 0 and j["elapsed_sec"] >= 0
    assert "漏れなし" not in json.dumps(j, ensure_ascii=False)


@need_ffmpeg
def test_same_frame_areas_share_one_sheet_cap(work, monkeypatch):
    """2 つの範囲が同じフレームで変わると、上限なしでは 2 枚になる。合計 1 枚なら 2 枚目は書かず未完了。"""
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "same.mp4"), [(10, "red", "gte(n,8)"), (180, "blue", "gte(n,8)")])
    base = A.scan(video, [dict(LEFT), dict(RIGHT)], os.path.join(work, "same-base"), **_settings(rows=2, cols=1))
    assert _kept(base) == {"left": [0, 8], "right": [0, 8]}
    assert base["incomplete"] is False and base["limit_hit"] is None and base["images_written"] == 2
    assert base["frames_read"] == 60
    out = os.path.join(work, "same-cap")
    res = A.scan(video, [dict(LEFT), dict(RIGHT)], out, **_settings(rows=2, cols=1), max_sheets=1)
    assert res["incomplete"] is True and res["limit_hit"] == "max_sheets"
    assert res["images_written"] == 1 and res["frames_read"] == 9
    assert _kept(res) == {"left": [0, 8], "right": []}
    _assert_image_accounting(res, out)
    assert _png_names(out) == ["left_01.png"]
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_staggered_second_sheet_stops_at_the_cap(work, monkeypatch):
    """先の範囲が 1 枚を確定したあと、別の範囲は開きの段のまま。次の変化で 2 枚目を書こうとしても書かない。

    その後は EOF まで変化が無い。images_written は 1 のまま、incomplete と limit_hit で上限に達したことが分かる。
    """
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "stagger.mp4"), [(10, "red", "gte(n,8)"), (180, "blue", "gte(n,20)")])
    out = os.path.join(work, "stagger-cap")
    res = A.scan(video, [dict(LEFT), dict(RIGHT)], out, **_settings(rows=2, cols=1), max_sheets=1)
    assert res["incomplete"] is True and res["limit_hit"] == "max_sheets"
    assert res["images_written"] == 1 and res["frames_read"] == 21
    assert _kept(res) == {"left": [0, 8], "right": []}
    _assert_image_accounting(res, out)
    assert _png_names(out) == ["left_01.png"]
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_staggered_sheet_cap_command_is_incomplete(work):
    """ずれて埋まる 2 範囲をコマンドで流すと、枚数の上限で終了コード 1。JSON と PNG の枚数が一致する。"""
    video = _write_clip(os.path.join(work, "stagger-cmd.mp4"), [(10, "red", "gte(n,8)"), (180, "blue", "gte(n,20)")])
    out = os.path.join(work, "stagger-cmd")
    r = subprocess.run([sys.executable, SCRIPT, video, "--area", "left=10,10,100,40", "--area", "right=180,10,100,40",
                        "--out", out, "--rows", "2", "--cols", "1", "--max-sheets", "1"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("NG area_sheet: 未完了。")
    assert "OK area_sheet" not in r.stdout and "漏れなし" not in r.stdout
    j = json.load(open(os.path.join(out, "area-sheet.json"), encoding="utf-8"))
    assert j["incomplete"] is True and j["limit_hit"] == "max_sheets"
    assert j["images_written"] == len(_png_names(out)) == 1
    assert _png_names(out) == ["left_01.png"]
    assert j["output_bytes"] == os.path.getsize(os.path.join(out, "left_01.png"))


@need_ffmpeg
def test_exact_sheet_cap_completes(work, monkeypatch):
    """ちょうど上限の枚数まで書き、落とせたフレームが無ければ未完了にしない。"""
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "exact.mp4"), [(10, "red", "gte(n,8)"), (180, "blue", "gte(n,20)")])
    one = A.scan(video, [dict(LEFT)], os.path.join(work, "exact-one"), **_settings(rows=2, cols=1), max_sheets=1)
    assert one["incomplete"] is False and one["limit_hit"] is None
    assert one["frames_read"] == 60 and _kept(one) == {"left": [0, 8]} and one["images_written"] == 1
    _assert_image_accounting(one, os.path.join(work, "exact-one"))
    base = A.scan(video, [dict(LEFT), dict(RIGHT)], os.path.join(work, "exact-base"), **_settings(rows=2, cols=1))
    out = os.path.join(work, "exact-cap")
    res = A.scan(video, [dict(LEFT), dict(RIGHT)], out, **_settings(rows=2, cols=1), max_sheets=2)
    assert _kept(res) == _kept(base) == {"left": [0, 8], "right": [0, 20]}
    assert res["frames_read"] == base["frames_read"] == 60
    assert res["incomplete"] is False and res["limit_hit"] is None
    assert res["images_written"] == base["images_written"] == 2
    assert res["changed_frames"] == 4
    _assert_image_accounting(res, out)
    assert _png_names(out) == ["left_01.png", "right_01.png"]
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_another_sheet_after_the_cap_is_incomplete(work, monkeypatch):
    """1 範囲が上限の 1 枚を書いたあと、もう 1 枚が要る変化が来たら未完了。そのフレームは残さない。"""
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "return.mp4"), [(10, "red", "between(n,8,19)")])
    base = A.scan(video, [dict(LEFT)], os.path.join(work, "return-base"), **_settings(rows=2, cols=1))
    assert _kept(base) == {"left": [0, 8, 20]}
    assert base["incomplete"] is False and base["images_written"] == 2 and base["frames_read"] == 60
    out = os.path.join(work, "return-cap")
    res = A.scan(video, [dict(LEFT)], out, **_settings(rows=2, cols=1), max_sheets=1)
    assert res["incomplete"] is True and res["limit_hit"] == "max_sheets"
    assert res["images_written"] == 1 and res["frames_read"] == 21
    assert _kept(res) == {"left": [0, 8]}
    _assert_image_accounting(res, out)
    assert _png_names(out) == ["left_01.png"]
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


@need_ffmpeg
def test_open_cells_after_sheet_cap_are_not_written(work, monkeypatch):
    """先の範囲が上限の 1 枚を書いたあと、別の範囲に開きの段だけが残るときは、その段を書かずに未完了。"""
    opened = _watch_ffmpeg(monkeypatch)
    video = _write_clip(os.path.join(work, "hold.mp4"), [(10, "red", "gte(n,8)")])
    out = os.path.join(work, "hold-cap")
    res = A.scan(video, [dict(LEFT), dict(RIGHT)], out, **_settings(rows=2, cols=1), max_sheets=1)
    assert res["frames_read"] == 60
    assert res["incomplete"] is True and res["limit_hit"] == "max_sheets"
    assert _kept(res) == {"left": [0, 8], "right": []}
    assert res["images_written"] == 1
    _assert_image_accounting(res, out)
    assert _png_names(out) == ["left_01.png"]
    for proc in opened:
        _assert_ffmpeg_reaped(proc)


# ---------- 途中で止まったときに前回の結果を残す ----------
def _snapshot(out):
    """出力先のファイルの名前と中身。一時のフォルダー (.run-) が残っていないことも見る。"""
    assert not [f for f in os.listdir(out) if f.startswith((".run-", ".old-"))]
    return {f: open(os.path.join(out, f), "rb").read() for f in sorted(os.listdir(out))}


@need_ffmpeg
@pytest.mark.parametrize("how", ["interrupt", "ffmpeg", "image"])
def test_failed_run_keeps_previous_result(work, video, monkeypatch, how):
    """中断・FFmpeg の失敗・画像を書けないとき、前回の成功の画像と JSON はそのまま残り、今回の書きかけは残らない。"""
    out = os.path.join(work, f"keep-{how}")
    A.scan(video, [dict(ADDR), dict(PANEL)], out, **_settings(rows=1), json_name="area-sheet.json")
    before = _snapshot(out)
    assert "area-sheet.json" in before and "addr_03.png" in before
    calls = []
    if how == "interrupt":
        real = A.changed_pixels

        def stop(*a):   # 40 フレームほど読んだところで Ctrl+C
            calls.append(1)
            if len(calls) > 80:
                raise KeyboardInterrupt
            return real(*a)
        monkeypatch.setattr(A, "changed_pixels", stop)
        err = KeyboardInterrupt
    elif how == "ffmpeg":
        monkeypatch.setattr(A, "build_filter", lambda areas: "nosuchfilter[out]")
        err = RuntimeError
    else:
        from PIL import Image as PILImage

        def broken(self, *a, **k):
            raise OSError("書けない")
        monkeypatch.setattr(PILImage.Image, "save", broken)
        err = OSError
    with pytest.raises(err):
        A.scan(video, [dict(ADDR)], out, **_settings(), json_name="area-sheet.json")
    assert _snapshot(out) == before


@need_ffmpeg
def test_finished_run_replaces_previous_result(work, video):
    """最後まで済んだら、JSON の画像のパスは出力先のもので、前回の画像のうち今回作らなかった名前は消える。"""
    out = os.path.join(work, "replace")
    A.scan(video, [dict(ADDR)], out, **_settings(rows=1), json_name="area-sheet.json")
    assert _png_names(out) == ["addr_01.png", "addr_02.png", "addr_03.png"]
    res = A.scan(video, [dict(ADDR)], out, **_settings(), json_name="area-sheet.json")
    assert _png_names(out) == ["addr_01.png"]
    j = json.load(open(os.path.join(out, "area-sheet.json"), encoding="utf-8"))
    assert j["areas"][0]["sheets"] == res["areas"][0]["sheets"] == [os.path.join(out, "addr_01.png")]
    _assert_image_accounting(res, out)
    _snapshot(out)


@need_ffmpeg
@pytest.mark.parametrize("how", ["returncode", "frames", "publish"])
def test_partly_read_or_unplaced_run_keeps_previous_result(work, video, monkeypatch, how):
    """途中まで読めて FFmpeg が失敗した・読めたフレームが見込みと違う・JSON を置き換えられないときも、前回の結果が残る。"""
    out = os.path.join(work, f"partly-{how}")
    A.scan(video, [dict(ADDR), dict(PANEL)], out, **_settings(rows=1), json_name="area-sheet.json")
    before = _snapshot(out)
    if how == "returncode":
        real = C.stop_ffmpeg

        def stop(p):   # 全部読めたあとに FFmpeg が誤りで終わった
            real(p)
            p.returncode = 1
        patch_common(monkeypatch, "stop_ffmpeg", stop)
    elif how == "frames":
        monkeypatch.setattr(A, "expected_frames", lambda *a: TOTAL + 10)
    else:
        real = os.replace

        def replace(src, dst):   # 宛先の JSON が開かれていて置き換えられない
            if ".run-" in src and dst.endswith("area-sheet.json"):
                raise PermissionError(dst)
            real(src, dst)
        monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PermissionError if how == "publish" else RuntimeError):
        A.scan(video, [dict(ADDR)], out, **_settings(), json_name="area-sheet.json")
    assert _snapshot(out) == before


@need_ffmpeg
def test_failed_read_is_not_hidden_by_the_last_sheet_cap(work, video, monkeypatch):
    """FFmpeg が誤りで終わった後、最後の段で容量の上限に当たっても、未完了として置かず失敗にする (前回の結果を残す)。
    終わり方の誤りを見ないのは、上限が読むのを止めたときだけ。"""
    out = os.path.join(work, "flush-cap")
    A.scan(video, [dict(ADDR)], out, **_settings(rows=1), json_name="area-sheet.json")
    before = _snapshot(out)
    real = C.stop_ffmpeg

    def stop(p):
        real(p)
        p.returncode = 1
    patch_common(monkeypatch, "stop_ffmpeg", stop)
    with pytest.raises(RuntimeError, match="FFmpeg が終了コード 1"):
        # 既定の段の数 (40) では読む間に画像を書かず、最後の段の 1 枚で 1 KB の上限を超える
        A.scan(video, [dict(ADDR)], out, **_settings(), max_output_mb=0.001, json_name="area-sheet.json")
    assert _snapshot(out) == before
