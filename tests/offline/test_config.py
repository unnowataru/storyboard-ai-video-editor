"""設定の読み込み (scripts/common.py) のテスト。Resolve と FFmpeg は要らない。"""
import itertools
import json
import os

import pytest

import common as C
import settings

ENV_KEYS = ["FFMPEG_DIR", "RESOLVE_DOWNLOADS", "RESOLVE_WORK", "RESOLVE_OUT_DIR", "RESOLVE_PREVIEW_DIR", "RESOLVE_TRIAL_SOURCE", "RESOLVE_CONFIG",
            "RESOLVE_SCRIPT_API", "RESOLVE_SCRIPT_LIB"]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """環境変数を外し、個人の設定ファイルを空の一時ファイルに向ける (手元の config/local.json の影響を受けない)。"""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    local = tmp_path / "local.json"
    monkeypatch.setenv("RESOLVE_CONFIG", str(local))
    return local


def test_env_wins_over_local_and_defaults(clean_env, monkeypatch, tmp_path):
    ff = tmp_path / "ffbin"
    ff.mkdir()
    (ff / ("ffmpeg" + C.EXE)).write_bytes(b"")
    other = tmp_path / "other"
    other.mkdir()
    clean_env.write_text(json.dumps({"paths": {"ffmpeg_dir": str(other)}}), encoding="utf-8")
    monkeypatch.setenv("FFMPEG_DIR", str(ff))
    info = C.Config().path_info("ffmpeg_dir")
    assert info["value"] == os.path.normpath(str(ff))
    assert info["source"] == "環境変数 FFMPEG_DIR"


def test_local_wins_over_defaults(clean_env, tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    clean_env.write_text(json.dumps({"paths": {"downloads_dir": str(dl)}}), encoding="utf-8")
    info = C.Config().path_info("downloads_dir")
    assert info["value"] == os.path.normpath(str(dl))
    assert info["source"].startswith("設定ファイル")


def test_defaults_search_used_last(clean_env):
    info = C.Config().path_info("work_dir")
    assert info["source"].startswith("既定の探し方")
    assert info["value"] == os.path.normpath(os.path.expandvars("%USERPROFILE%/Videos/storyboard-work")) or os.name != "nt"


def test_preview_dir_default(clean_env, monkeypatch, tmp_path):
    """仮組みの書き出し先の既定は %USERPROFILE%/Videos/storyboard-preview。作業フォルダーとは別に決まり、環境変数で変えられる。"""
    monkeypatch.setenv("RESOLVE_WORK", str(tmp_path / "work"))
    info = C.Config().path_info("preview_dir")
    assert info["source"].startswith("既定の探し方")
    assert info["value"] == os.path.normpath(os.path.expandvars("%USERPROFILE%/Videos/storyboard-preview")) or os.name != "nt"
    assert not _under(info["value"], tmp_path / "work")
    spec = C.Config().defaults["paths"]["preview_dir"]
    assert spec["env"] == "RESOLVE_PREVIEW_DIR" and spec["create"] is True
    monkeypatch.setenv("RESOLVE_PREVIEW_DIR", str(tmp_path / "pv"))
    info = C.Config().path_info("preview_dir")
    assert info["value"] == os.path.normpath(str(tmp_path / "pv"))
    assert info["source"] == "環境変数 RESOLVE_PREVIEW_DIR"


def _under(path, folder):
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def test_default_out_dir_is_outside_repo(clean_env, monkeypatch, tmp_path):
    """出力先の既定は作業フォルダーの下の out。リポジトリ (クラウドで同期しているフォルダーの下にあることがある) の下には書かない。
    出力先には機密を含む画面の録画から作ったフレームの画像も入るので、クラウドに同期させない。"""
    work = tmp_path / "work"
    monkeypatch.setenv("RESOLVE_WORK", str(work))
    cfg = C.Config()
    info = cfg.path_info("out_dir")
    assert info["source"].startswith("既定の探し方")
    assert info["value"] == os.path.normpath(str(work / "out"))
    assert not _under(cfg.out_dir("check-final"), C.REPO)
    # RESOLVE_WORK も無いとき (既定の作業フォルダーの下)
    monkeypatch.delenv("RESOLVE_WORK")
    info = C.Config().path_info("out_dir")
    assert info["value"] == os.path.join(C.Config().path("work_dir"), "out")
    assert not _under(info["value"], C.REPO)


def test_no_default_place_is_under_repo():
    """書き込む場所 (create のある項目) の既定の探し方に、リポジトリの下 ({repo}) を書かない。"""
    for key, spec in C.Config().defaults["paths"].items():
        if spec.get("create"):
            assert not any("{repo}" in pat for pat in spec.get("search", [])), key


def test_default_out_dir_in_powershell_is_outside_repo(clean_env, monkeypatch, tmp_path):
    """PowerShell 側 (scripts/config.ps1) も同じ既定 (作業フォルダーの下の out) になる。"""
    import shutil
    import subprocess
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh が無い")
    work = tmp_path / "work"
    monkeypatch.setenv("RESOLVE_WORK", str(work))
    ps1 = os.path.join(C.REPO, "scripts", "config.ps1")
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", f". '{ps1}'; Get-ResolvePath out_dir"],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    got = r.stdout.strip()
    assert os.path.normcase(os.path.normpath(got)) == os.path.normcase(os.path.normpath(str(work / "out")))
    assert not _under(got, C.REPO)


def test_storyboard_overrides_local_and_defaults(clean_env):
    clean_env.write_text(json.dumps({"storyboard": {"caption": {"size": 90}, "fps": 25}}), encoding="utf-8")
    cfg = C.Config()
    values, sources = cfg.storyboard_values({"caption": {"size": 100}, "rows": []})
    assert values["caption"]["size"] == 100 and sources["caption.size"] == "構成表"
    assert values["fps"] == 25 and sources["fps"] == "local.json"
    assert values["caption"]["font_name"] == "Meiryo" and sources["caption.font_name"] == "defaults.json"


def test_storyboard_source_placeholder(clean_env, monkeypatch, tmp_path):
    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"source": "{trial_source}", "output": "o.mp4", "rows": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        C.Config().load_storyboard(str(sb))
    media = tmp_path / "m.mp4"
    monkeypatch.setenv("RESOLVE_TRIAL_SOURCE", str(media))
    assert C.Config().load_storyboard(str(sb))["source"] == os.path.normpath(str(media))
    other = tmp_path / "x.mp4"
    assert C.Config().load_storyboard(str(sb), str(other))["source"] == os.path.normpath(str(other))


def test_relative_source_is_from_storyboard_folder(clean_env, tmp_path):
    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"source": "rec/a.mp4", "output": "o.mp4", "rows": []}), encoding="utf-8")
    assert C.Config().load_storyboard(str(sb))["source"] == os.path.normpath(str(tmp_path / "rec" / "a.mp4"))


def test_relative_output_is_from_storyboard_folder(clean_env, tmp_path, monkeypatch):
    """書き出し (output) も構成表のフォルダーから決まる。実行したフォルダー (cwd) には左右されない。"""
    sub = tmp_path / "sb"
    sub.mkdir()
    sb = sub / "sb.json"
    sb.write_text(json.dumps({"source": "a.mp4", "output": "out/o.mp4", "rows": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert C.Config().load_storyboard(str(sb))["output"] == os.path.normpath(str(sub / "out" / "o.mp4"))


def test_row_spans_use_row_seconds():
    """行ごとの seconds を使う (既定 6 秒、2 行目だけ 4 秒)。"""
    sb = {"fps": 30, "seconds_per_row": 6, "rows": [{}, {"seconds": 4}, {}]}
    assert C.row_spans(sb) == [(0, 180), (180, 120), (300, 180)]


def test_row_spans_source_is_same_speed():
    """seconds_per_row が "source" なら、seconds の無い行は使う範囲の長さ (等倍)。seconds を書いた行はその秒数。"""
    sb = {"fps": 30, "seconds_per_row": "source",
          "rows": [{"start": "1:52", "end": "2:46"}, {"start": 10, "end": 70, "seconds": 6}, {"start": 0, "end": 2.5}]}
    assert C.row_spans(sb) == [(0, 54 * 30), (54 * 30, 180), (54 * 30 + 180, 75)]


@pytest.mark.parametrize("per", ["source", 4, 0.5], ids=["source", "int", "float"])
def test_seconds_per_row_accepts_seconds_or_source(clean_env, tmp_path, per):
    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"source": "a.mp4", "seconds_per_row": per, "rows": []}), encoding="utf-8")
    assert C.Config().load_storyboard(str(sb))["seconds_per_row"] == per


@pytest.mark.parametrize("per", [0, -1, "src", True, None], ids=["zero", "negative", "other-text", "bool", "null"])
def test_seconds_per_row_rejects_others(clean_env, tmp_path, per):
    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"source": "a.mp4", "seconds_per_row": per, "rows": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="seconds_per_row"):
        C.Config().load_storyboard(str(sb))


def test_make_test_source_accepts_bare_file_name(clean_env, tmp_path, monkeypatch):
    """--out にファイル名だけを渡しても (親のフォルダーが空の文字列でも) 失敗しない。FFmpeg は動かさない。"""
    import make_test_source as M
    ran = []
    monkeypatch.setattr(C, "run_tool", lambda name, args: ran.append(args))
    monkeypatch.chdir(tmp_path)
    settings._CFG = None
    try:
        assert M.make("testsrc.mp4", 1) == "testsrc.mp4"
    finally:
        settings._CFG = None
    assert ran and ran[0][-1] == "testsrc.mp4"


def look(**over):
    values, _ = C.Config().storyboard_values(over)
    return values


@pytest.mark.parametrize("w,h", [(1920, 1080), (3840, 2160), (1280, 720)])
def test_safe_area_default_is_whole_frame(clean_env, w, h):
    """safe_area.aspect を指定しないときは、タイムラインの比率 (画面の全体)。"""
    sa = C.safe_area(look(timeline={"width": w, "height": h}))
    assert (sa["x0"], sa["width"], sa["y0"], sa["height"]) == (0, w, 0, h)


@pytest.mark.parametrize("w,h,x0,width", [(1920, 1080, 150, 1620), (3840, 2160, 300, 3240), (1280, 720, 100, 1080)])
def test_safe_area_3_2(clean_env, w, h, x0, width):
    sa = C.safe_area(look(timeline={"width": w, "height": h}, safe_area={"aspect": "3:2"}))
    assert (sa["x0"], sa["width"], sa["y0"], sa["height"]) == (x0, width, 0, h)


def test_safe_area_wider_than_frame(clean_env):
    sa = C.safe_area(look(safe_area={"aspect": "21:9"}))
    assert sa["x0"] == 0 and sa["width"] == 1920 and sa["height"] < 1080 and sa["y0"] > 0


def test_badge_inside_safe_area(clean_env):
    lk = look()
    box, sa = C.badge_box(lk), C.safe_area(lk)
    right = (box["center"][0] + box["width"] / 2) * sa["W"]
    top = (1 - box["center"][1] - box["height"] / 2) * sa["H"]
    margin = lk["badge"]["margin"] * sa["H"]
    assert right == pytest.approx(sa["x1"] - margin)
    assert top == pytest.approx(margin)


def test_zoom_warning(clean_env):
    lk = look(safe_area={"aspect": "3:2"})
    assert C.zoom_safe_area_warning(lk, {"x": 1200, "y": 100, "w": 640, "h": 360}) is not None   # 16:9 のズーム範囲は 3:2 からはみ出す
    assert C.zoom_safe_area_warning(lk, {"x": 1200, "y": 100, "w": 540, "h": 360}) is None       # 3:2 のズーム範囲は収まる
    assert C.zoom_safe_area_warning(lk, None) is None
    assert C.zoom_safe_area_warning(look(), {"x": 1200, "y": 100, "w": 640, "h": 360}) is None   # 既定は画面の全体


def test_source_to_timeline(clean_env):
    """素材の画素の四角を、素材を全体に収めて置いた画面 (1920x1080) の画素に直す。ほかの項目と keys の t は残る。"""
    lk = look()
    box = {"x": 2400, "y": 240, "w": 1280, "h": 720, "seconds": 3, "type": "mosaic"}
    assert C.source_to_timeline(box, 3840, 2160, lk) == {"x": 1200, "y": 120, "w": 640, "h": 360, "seconds": 3,
                                                         "type": "mosaic"}
    assert C.source_to_timeline(box, 1920, 1080, lk) == box          # 同じ解像度なら変わらない
    assert C.source_to_timeline(box, None, None, lk) == box          # 素材の大きさが分からないときもそのまま
    moving = dict(box, keys=[{"t": 0, "x": 0, "y": 0, "w": 400, "h": 200}, {"t": 1.5, "x": 3840, "y": 2160, "w": 2, "h": 2}])
    got = C.source_to_timeline(moving, 3840, 2160, lk)["keys"]
    assert got == [{"t": 0, "x": 0, "y": 0, "w": 200, "h": 100}, {"t": 1.5, "x": 1920, "y": 1080, "w": 1, "h": 1}]
    # 縦長の素材 (1080x1920) は高さで収まり、左右に余りが出る
    tall = C.source_to_timeline({"x": 0, "y": 0, "w": 1080, "h": 1920}, 1080, 1920, lk)
    assert tall == pytest.approx({"x": (1920 - 607.5) / 2, "y": 0, "w": 607.5, "h": 1080})


def test_zoom_warning_uses_timeline_pixels(clean_env):
    """セーフエリアの警告は、タイムラインの画素に直した後のズーム範囲で判定する (4K の 1080x720 は 1080p の 540x360)。"""
    lk = look(safe_area={"aspect": "3:2"})
    z = {"x": 2400, "y": 200, "w": 1080, "h": 720, "seconds": 1}
    assert C.scene_values(lk, {"zoom": z}, source_size=(3840, 2160))["zoom"]["warning"] is None
    assert C.scene_values(lk, {"zoom": z})["zoom"]["warning"] is None   # 倍率で決まるので同じ (1080x720 は 3:2)
    z16 = dict(z, w=1280)
    assert C.scene_values(lk, {"zoom": z16}, source_size=(3840, 2160))["zoom"]["warning"] is not None


def test_caption_fit(clean_env):
    cfg = C.Config()
    lk = look()
    font = cfg.font_file(lk["caption"]["font_name"])
    if not font:
        pytest.skip("Meiryo が無い")
    pytest.importorskip("PIL")
    short = C.fit_caption(lk, "短いテロップ", font)
    assert short["size"] == lk["caption"]["size"] and short["lines"] == ["短いテロップ"] and not short["warnings"]
    long = C.fit_caption(lk, "とても長いテロップの文がここに入ってセーフエリアの幅を超えてしまう場合の試験の文章です", font)
    assert long["size"] == lk["caption"]["min_size"] and len(long["lines"]) == 2
    too_long = C.fit_caption(lk, "あ" * 200, font)
    assert too_long["warnings"]
    # テロップの背景の下端は caption.lift だけ上がる
    assert short["bar_center"] - short["bar_height"] / 2 == pytest.approx(lk["caption"]["lift"])


def _cols(t):
    """全角を 2、半角を 1 と数える幅 (書体が無くても折り返しを試せるように)。"""
    import unicodedata
    return sum(1 if unicodedata.east_asian_width(c) in "NaH" else 2 for c in t)


def _check_wrap(text, lines, cols, words=()):
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")   # 字が抜けたり増えたりしない
    assert all(_cols(x) <= cols for x in lines), lines
    for a, b in itertools.pairwise(lines):
        assert b[0] not in C.CAPTION_NO_LINE_START, (a, b)   # 行の頭に句読点・閉じ括弧・小書きの仮名・長音を置かない
        assert a[-1] not in C.CAPTION_NO_LINE_END, (a, b)    # 行の末に開き括弧を置かない
    for w in words:   # 言葉 (英数字の語を含む) が行をまたがない
        assert any(w in x for x in lines), (w, lines)


@pytest.mark.parametrize("text, cols, max_lines, words", [
    ("画面に収まらない長いテロップは縮めて、書き出しの前に確かめる", 32, 2, ["テロップは", "縮めて", "書き出し"]),
    ("画面に収まらない長いテロップは縮めてから折り返し、それでも収まらなければ警告を出す", 30, 3,
     ["テロップは", "縮めて", "折り返し", "収まらなければ"]),
    ("Python 3.13 と x86-64 の 192.0.2.10 に接続する", 20, 3, ["Python", "x86-64", "192.0.2.10"]),
    ("お使いの「設定画面」を確認してから、ショートカットキーで操作する", 30, 3, ["お使い", "「設定画面」", "ショートカットキー"]),
    ("ファイルサーバーのバックアップ（スナップショット）をとる", 26, 3, ["バックアップ", "（スナップショット）"]),
    ("ちょっとしたキャッシュ、ジョブ。ログ「設定」を確認し、ポップアップを閉じる", 12, None, []),
])
def test_wrap_caption_line_keeps_words_and_kinsoku(text, cols, max_lines, words):
    lines = C.wrap_caption_line(text, lambda t: _cols(t) <= cols, max_lines)
    _check_wrap(text, lines, cols, words)
    if max_lines:
        assert len(lines) <= max_lines, lines


def test_wrap_caption_line_examples():
    def fits(n):
        return lambda t: _cols(t) <= n
    # 「…長いテロップ」の次の行が「は縮めて…」にならない
    assert C.wrap_caption_line("画面に収まらない長いテロップは縮めて、書き出しの前に確かめる", fits(32), 2) == \
        ["画面に収まらない長いテロップは", "縮めて、書き出しの前に確かめる"]
    assert C.wrap_caption_line("短い", fits(10)) == ["短い"]
    # 一つの語が幅を超えるときだけ、語の途中で切る
    assert C.wrap_caption_line("ABCDEFGHIJKLMNOPQRSTUVWXYZ", fits(10)) == ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ"]
    # 上限の行数に収まらないときは、行を減らすより言葉を切らない
    assert C.wrap_caption_line("お使いの「設定画面」を確認してから、ショートカットキーで操作する", fits(20), 2) == \
        ["お使いの「設定画面」", "を確認してから、", "ショートカットキーで", "操作する"]


def test_caption_fit_wraps_at_word_breaks(clean_env):
    """書体で測る fit_caption でも言葉の途中で切らない。明示した改行 (改行の字と |) はそのまま使う。"""
    cfg = C.Config()
    lk = look()
    font = cfg.font_file(lk["caption"]["font_name"])
    if not font:
        pytest.skip("Meiryo が無い")
    pytest.importorskip("PIL")
    text = "画面に収まらない長いテロップは縮めてから書き出し、仕上がりの画面で幅を確かめる"
    fit = C.fit_caption(lk, text, font)
    assert len(fit["lines"]) == 2 and not fit["warnings"], fit
    _check_wrap(text, fit["lines"], 10 ** 6, ["テロップ", "縮めて", "書き出し", "仕上がり"])
    assert C.fit_caption(lk, "一行目の|テロップ", font)["lines"] == ["一行目の", "テロップ"]
    assert C.fit_caption(lk, "一行目の\nテロップ", font)["lines"] == ["一行目の", "テロップ"]


def test_appdata_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    assert C.appdata_warning(str(tmp_path / "AppData" / "Local" / "storyboard-work"))
    assert C.appdata_warning(str(tmp_path / "Videos" / "storyboard-work")) is None


def test_fusion_filter_names():
    assert C.fusion_filter("Linear") == 2 and C.fusion_filter("Lanczos") == 8 and C.fusion_filter("Catmull-Rom") == 5
    assert C.fusion_filter(8) == 8


def test_section_merges_local(clean_env):
    """storyboard 以外の項目 (track など) も local.json で一部だけ上書きできる。"""
    clean_env.write_text(json.dumps({"track": {"pad": 10}}), encoding="utf-8")
    t = C.Config().section("track")
    assert t["pad"] == 10 and t["stride_sec"] == 0.5


def test_report_one_line_when_ok(capsys):
    """標準出力のルール: 正常なら 1 行 (要約と詳しい結果のパス)、失敗なら続けて該当箇所だけ。"""
    assert C.report(True, "x: 3/3", "out/x.json") == 0
    assert capsys.readouterr().out == "OK x: 3/3  詳細: out/x.json\n"
    assert C.report(False, "x: 2/3", "out/x.json", ["NG a\nb"]) == 1
    assert capsys.readouterr().out.splitlines() == ["NG x: 2/3  詳細: out/x.json", "  NG a b"]


def test_check_env_version_compare():
    import check_env
    assert not check_env.version_differs("21.1.1.10", "21.1.1.10")
    assert check_env.version_differs("21.1.1b.10", "21.1.1.10")   # suffix (ベータを示す文字など) も違いとみなす
    assert check_env.version_differs("21.2.0.5", "21.1.1.10") and check_env.version_differs("", "21.1.1.10")
    assert C.Config().section("resolve")["verified_version"]


def test_sec_to_mss_rounds_before_split():
    assert C.sec_to_mss(59.96) == "1:00.0" and C.sec_to_mss(75.04) == "1:15.0" and C.sec_to_mss(0) == "0:00.0"


def test_report_limits_lines(capsys):
    """見るべき箇所は report.max_lines 行まで出し、残りは件数の 1 行にまとめる。"""
    assert C.Config().section("report")["max_lines"] == 10
    C.report(False, "x", "out/x.json", [f"NG {i}" for i in range(25)], max_lines=3)
    assert capsys.readouterr().out.splitlines() == ["NG x  詳細: out/x.json", "  NG 0", "  NG 1", "  NG 2",
                                                    "  ほか 22 件 (詳細は JSON)"]
    C.report(False, "x", "out/x.json", [f"NG {i}" for i in range(25)])
    assert len(capsys.readouterr().out.splitlines()) == 12
