"""構成表 → FCPXML / OTIO / EDL の変換 (scripts/sb2nle.py) のテスト。Resolve は要らない。

素材の長さと解像度を返す部分 (ffprobe) は差し替えるので、FFmpeg も素材も要らない。
最後のテストだけ、FFmpeg で機密の無いテスト用の素材を作って、コマンドとして動かす (FFmpeg が無ければ飛ばす)。
"""
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import common as C
import sb2nle
import settings

REPO = C.REPO
TRIAL = os.path.join(REPO, "tests", "trial-storyboard.json")


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setenv("RESOLVE_CONFIG", str(tmp_path / "none.json"))   # 手元の config/local.json の影響を受けない
    settings._CFG = None
    yield tmp_path
    settings._CFG = None


def convert(out, source=None):
    return sb2nle.convert(TRIAL, out, source or str(out / "trial.mp4"), probe_fn=lambda p: (1920, 1080, 40.0))


def test_frames_and_speed(out):
    sb, rows, total, _ = convert(out)
    assert [(r["in_f"], r["src_len"], r["out_f"], r["rec"]) for r in rows] == [(0, 150, 150, 0), (150, 600, 150, 150),
                                                                             (900, 180, 180, 300)]
    assert [round(r["speed"], 3) for r in rows] == [1.0, 4.0, 1.0]
    assert total == 480


def test_fcpxml(out):
    sb, rows, total, paths = convert(out)
    root = ET.parse(paths["fcpxml"]).getroot()
    clips = root.findall(".//asset-clip")
    assert len(clips) == 3
    assert clips[1].find("timeMap") is not None and clips[0].find("timeMap") is None
    ts = root.findall(".//text-style[@font]")
    assert ts and all(t.get("font") == sb["caption"]["font_name"] for t in ts)
    assert all(t.get("fontSize") == str(sb["caption"]["size"]) for t in ts)
    assert clips[2].find("adjust-transform/param[@name='scale']") is not None
    notes = [m.get("note") or "" for m in root.findall(".//marker")]
    assert "マスク" in notes[2] and "肩テロップ" in notes[0]
    assert b"\r\n" not in paths["fcpxml"].read_bytes()


def test_fcpxml_sequence_size_from_storyboard(out):
    """素材が 4K でも、sequence (タイムライン) の解像度は構成表の timeline。素材の format は別に持つ。"""
    sb, _, _, paths = sb2nle.convert(TRIAL, out, str(out / "trial.mp4"), probe_fn=lambda p: (3840, 2160, 40.0))
    root = ET.parse(paths["fcpxml"]).getroot()
    seq = root.find(f".//format[@id='{root.find('.//sequence').get('format')}']")
    assert (int(seq.get("width")), int(seq.get("height"))) == (sb["timeline"]["width"], sb["timeline"]["height"])
    asset = root.find(f".//format[@id='{root.find('.//asset').get('format')}']")
    assert (asset.get("width"), asset.get("height")) == ("3840", "2160")
    assert all(c.get("format") == asset.get("id") for c in root.findall(".//asset-clip"))


def test_fcpxml_zoom_out_reverses_keyframes(out):
    """構成表の zoom.direction が "out" なら、寄った状態から全体 (倍率 1) へ引く。"""
    sbj = json.loads(open(TRIAL, encoding="utf-8").read())
    sbj["rows"][2]["zoom"]["direction"] = "out"
    p = out / "out-sb.json"
    p.write_text(json.dumps(sbj), encoding="utf-8")
    _, _, _, paths = sb2nle.convert(str(p), out, str(out / "trial.mp4"), probe_fn=lambda p: (1920, 1080, 40.0))
    root = ET.parse(paths["fcpxml"]).getroot()
    scale = [k.get("value") for k in root.findall(".//param[@name='scale']//keyframe")]
    pos = [k.get("value") for k in root.findall(".//param[@name='position']//keyframe")]
    assert scale[-1] == "1 1" and scale[0] != "1 1"
    assert pos[-1] == "0 0" and pos[0] != "0 0"
    _, _, _, paths = convert(out)   # 既定 (direction なし) は全体から寄る
    root = ET.parse(paths["fcpxml"]).getroot()
    assert [k.get("value") for k in root.findall(".//param[@name='scale']//keyframe")][0] == "1 1"


def test_fcpxml_zoom_without_seconds_is_one_value(out):
    """seconds 0 (最初から寄った状態) は、同じ時刻に 2 つの値を書かず、寄った値だけを置く。"""
    sbj = json.loads(open(TRIAL, encoding="utf-8").read())
    sbj["rows"][2]["zoom"]["seconds"] = 0
    p = out / "zero-sb.json"
    p.write_text(json.dumps(sbj), encoding="utf-8")
    _, _, _, paths = sb2nle.convert(str(p), out, str(out / "trial.mp4"), probe_fn=lambda p: (1920, 1080, 40.0))
    tr = ET.parse(paths["fcpxml"]).getroot().find(".//asset-clip[@name='row3']/adjust-transform")
    assert tr is not None and not tr.findall(".//keyframe")
    assert tr.get("scale") != "1 1" and tr.get("position") != "0 0"


def test_fcpxml_zoom_in_source_pixels(out):
    """ズーム範囲は素材の画素。4K の素材の (2400, 200, 1280, 720) は、1080p の素材の (1200, 100, 640, 360) と同じ寄せ方になる。"""
    def keys(paths):
        root = ET.parse(paths["fcpxml"]).getroot()
        return [[k.get("value") for k in root.findall(f".//param[@name='{n}']//keyframe")] for n in ("scale", "position")]

    sbj = json.loads(open(TRIAL, encoding="utf-8").read())
    sbj["rows"][2]["zoom"].update({"x": 2400, "y": 200, "w": 1280, "h": 720})
    p = out / "sb4k.json"
    p.write_text(json.dumps(sbj), encoding="utf-8")
    sb4, rows4, _, k4 = sb2nle.convert(str(p), out / "4k", str(out / "trial.mp4"), probe_fn=lambda p: (3840, 2160, 40.0))
    _, _, _, k1 = convert(out / "hd")   # tests/trial-storyboard.json のズーム範囲 (1200, 100, 640, 360) を 1080p の素材で
    assert keys(k4) == keys(k1)
    assert any("ズーム範囲 640x360" in w for w in sb2nle.warnings_of(sb4, rows4))   # 警告は直した後の値で出す


def test_otio(out):
    _, _, _, paths = convert(out)
    j = json.loads(paths["otio"].read_text(encoding="utf-8"))
    ch = j["tracks"]["children"][0]["children"]
    assert len(ch) == 3
    assert ch[1]["effects"][0]["time_scalar"] == pytest.approx(4.0)
    assert ch[0]["source_range"]["duration"]["value"] == 150
    assert "%20" not in ch[0]["media_references"]["DEFAULT_MEDIA"]["target_url"]


def test_edl(out):
    _, _, _, paths = convert(out)
    text = paths["edl"].read_bytes().decode("utf-8")
    assert "\r\n" in text   # EDL は CRLF
    assert "M2   AX       120.0" in text
    assert text.count("|C:ResolveColorBlue") == 3


def test_warnings_for_safe_area(out):
    sb, rows, _, _ = convert(out)
    w = sb2nle.warnings_of(sb, rows)
    assert any(x.startswith("row3:") and "ズーム" in x for x in w)


def test_source_from_env(out, monkeypatch):
    media = out / "env.mp4"
    monkeypatch.setenv("RESOLVE_TRIAL_SOURCE", str(media))
    settings._CFG = None
    sb, rows, _, _ = sb2nle.convert(TRIAL, out, None, probe_fn=lambda p: (1920, 1080, 40.0))
    assert rows[0]["src"] == os.path.normpath(str(media))


def test_cli_with_generated_source(out):
    cfg = C.Config()
    if not cfg.path_info("ffmpeg_dir")["exists"]:
        pytest.skip("FFmpeg が無い")
    src = out / "testsrc.mp4"
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "make_test_source.py"), "--seconds", "40", "--out",
                    str(src)], check=True, capture_output=True)
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sb2nle.py"), TRIAL, str(out / "nle"),
                        "--source", str(src)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "total 480 frames" in r.stdout
    log = out / "nle" / "trial-storyboard.sb2nle.json"
    assert r.stdout.splitlines()[0].endswith(f"詳細: {log}")            # 画面の詳細は記録の JSON (出力フォルダーではない)
    assert json.loads(log.read_text(encoding="utf-8"))["total_frames"] == 480
    assert {p.suffix for p in (out / "nle").iterdir()} == {".fcpxml", ".otio", ".edl", ".json"}


def test_cli_error_is_one_line_and_json(out):
    """変換中の例外 (素材が無く ffprobe で読めない) は NG の 1 行と記録の JSON にする。"""
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sb2nle.py"), TRIAL, str(out / "nle"),
                        "--source", str(out / "none.mp4")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and r.stdout.startswith("NG sb2nle: 変換できない")
    log = json.loads((out / "nle" / "trial-storyboard.sb2nle.json").read_text(encoding="utf-8"))
    assert log["ok"] is False and "Traceback" in log["error"]


def test_warnings_for_unknown_keys(out):
    """知らない項目 (書き間違い) は sb2resolve.py と同じ文で警告する。"""
    sb, rows, _, _ = convert(out)
    sb["rows"][0]["fucus"] = {}
    w = sb2nle.warnings_of(sb, rows)
    assert any(x.startswith("知らない項目 1 行目の fucus") for x in w)


def test_non_integer_fps_is_rejected(out):
    """形式の時間の単位は 1/fps 秒なので、整数でない fps (29.97) は読み込みで止める (row_clips とずれないように)。"""
    sb = json.loads(open(TRIAL, encoding="utf-8").read())
    sb["fps"] = 29.97
    path = out / "fps.json"
    path.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="整数"):
        sb2nle.load(str(path), str(out / "none.mp4"))


def test_layout_is_rejected_without_writing(out):
    """layout (画面を縮めて寄せ、余白をぼかした背景にする配置) は FCPXML に書けないので、何も書き出さずに止める。
    画面を全体のまま出して成功と報告しない。layout の無い構成表は今までどおり書き出す。"""
    sb = json.loads(open(TRIAL, encoding="utf-8").read())
    for r in sb["rows"]:
        r.pop("zoom", None)   # layout は行の zoom と一緒に使えない (読み込みの誤りになる) ので外す
    sb["layout"] = {"screen": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "height": 0.78, "anchor": "top-left"}
    path = out / "layout.json"
    path.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    dest = out / "lay"
    with pytest.raises(SystemExit, match="layout .*FCPXML.*sb2resolve.py"):
        sb2nle.convert(str(path), dest, str(out / "trial.mp4"), probe_fn=lambda p: (1920, 1080, 40.0))
    assert not dest.exists() or not any(dest.iterdir())

    # コマンドでも NG の 1 行に理由が出て、変換したファイルは書かない
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sb2nle.py"), str(path), str(out / "nle"),
                        "--source", str(out / "trial.mp4")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and r.stdout.startswith("NG sb2nle: 変換できない")
    assert "sb2resolve.py" in r.stdout
    assert {p.suffix for p in (out / "nle").iterdir()} == {".json"}

    del sb["layout"]   # layout を外せば同じ構成表で書き出せる
    path.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    _, rows, _, paths = sb2nle.convert(str(path), dest, str(out / "trial.mp4"), probe_fn=lambda p: (1920, 1080, 40.0))
    assert len(rows) == 3 and all(p.exists() for p in paths.values())
    assert len(ET.parse(paths["fcpxml"]).getroot().findall(".//asset-clip")) == 3
