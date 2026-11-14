"""sb2ffmpeg.py (簡易版を作るツール)・sb2ffmpeg_render.py (描く部分)・resolve_stand_in.py (Resolve の代わりに
呼び出しを受ける部分) のテスト。

- 依存の向き: Resolve 側のスクリプトは簡易版のファイルを読み込まない。描く部分は組む部分 (sb2resolve・resolve_stand_in) を読み込まない
- 組み立て内容: テスト用と見本の構成表を sb2resolve.build_and_render に流し、Resolve に組むときと同じ部品のつながりになる。
  期待する組み立て内容は tests/offline/build-spec/ の JSON。Resolve 側の組み立てを意図して変えたら、
  環境変数 UPDATE_BUILD_SPEC=1 で流して JSON を作り直し、差分を見てから入れる (意図しない差分は Resolve 側の劣化)
"""
import ast
import glob
import json
import math
import os
import time

import pytest
from PIL import Image

import common as C
import resolve_stand_in as R
import sb2ffmpeg as F
import sb2ffmpeg_render as D
import sb2resolve as S

SPEC_DIR = os.path.join(C.REPO, "tests", "offline", "build-spec")
TRIAL = os.path.join(C.REPO, "tests", "trial-storyboard.json")
EXAMPLES = os.path.join(C.REPO, "examples")
OURS = {"sb2ffmpeg", "sb2ffmpeg_render", "sb2ffmpeg_bench", "resolve_stand_in"}
LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値


def imports(path):
    """ファイルが読み込むモジュールの名前 (import と from ... import の頭)。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_resolve_side_does_not_import_simple_version_tools():
    files = [p for p in glob.glob(os.path.join(C.REPO, "scripts", "*.py"))
             if os.path.splitext(os.path.basename(p))[0] not in OURS]
    files += glob.glob(os.path.join(C.REPO, "tests", "resolve", "*.py"))
    bad = {os.path.relpath(p, C.REPO): sorted(imports(p) & OURS) for p in files if imports(p) & OURS}
    assert not bad
    assert not OURS & {m.__name__ for m in C.PARTS}


def spec_for(path, size=(1920, 1080), seconds=40.0):
    """構成表を読み、素材を ffprobe の代わりに決めた値で組み立てる。素材のパスはファイル名だけにする (PC によらない)。"""
    sb = C.config().load_storyboard(path, "testsrc.mp4" if path == TRIAL else None)
    rows = S.plan(sb)
    info = {p["source"]: {"width": size[0], "height": size[1], "fps": 30.0, "duration": seconds} for p in rows}
    ok, lines, spec = F.build(sb, rows, info, S.base_name(sb, path))
    for m in spec["media"]:
        m["path"] = os.path.basename(m["path"])
    return ok, lines, json.loads(json.dumps(spec, ensure_ascii=False))


CASES = [("trial-storyboard", TRIAL, (1920, 1080), 40.0),
         ("sample1-storyboard", os.path.join(EXAMPLES, "sample1-storyboard.json"), (3840, 2160), 3600.0),
         ("sample2-storyboard", os.path.join(EXAMPLES, "sample2-storyboard.json"), (3840, 2160), 3600.0)]


@pytest.mark.parametrize("name,path,size,seconds", CASES, ids=[c[0] for c in CASES])
@pytest.mark.meiryo
def test_build_spec_matches_expected(name, path, size, seconds):
    ok, lines, spec = spec_for(path, size, seconds)
    assert ok, lines
    want_path = os.path.join(SPEC_DIR, name + ".build.json")
    if os.environ.get("UPDATE_BUILD_SPEC") == "1":
        os.makedirs(SPEC_DIR, exist_ok=True)
        C.write_json(want_path, spec)
    with open(want_path, encoding="utf-8") as f:
        want = json.load(f)
    assert spec == want


def test_trial_storyboard_clips_and_parts():
    ok, lines, spec = spec_for(TRIAL)
    clips = spec["clips"]
    # 1 行目は等速 5 秒、2 行目は 20 秒を 5 秒 (4 倍速)、3 行目は 6 秒
    assert [(c["start"], c["end"], c["source_start"], c["source_end"], c["speed"]) for c in clips] == [
        (0, 150, 0, 150, 100.0), (150, 300, 150, 750, 400.0), (300, 480, 900, 1080, 100.0)]
    assert [c["markers"][0]["custom"] for c in clips] == ["row1", "row2", "row3"]
    tools = {t["name"]: t for t in clips[2]["fusion"]["tools"]}
    # マスク (モザイク) → ズーム → テロップの順につながり、ズームの大きさと位置はキーフレーム
    assert tools["Mask1Mosaic"]["links"]["Source"] == {"tool": "MediaIn1", "output": "Output"}
    assert tools["Zoom"]["links"]["Input"]["tool"] == "Mask1Mosaic"
    assert "keys" in tools["Zoom"]["links"]["Size"] and set(tools["Zoom"]["links"]["Center"]["path"]) == {"X", "Y"}
    assert tools["MediaOut1"]["links"]["Input"]["tool"] == "CaptionOver"
    # 肩テロップは 1 行目だけ
    assert [any(t["name"] == "BadgeOver" for t in c["fusion"]["tools"]) for c in clips] == [True, False, False]


def test_hold_clip_shows_last_frame():
    """止めたクリップは、置いた範囲の最後のフレームを映す (sb2resolve.place_hold が再生ヘッドを直後に置いてから止める)。
    止めた後の素材の範囲は、始まりも終わりもそのフレーム (21.1.1 の実機、docs/scripting-api.md の「速さ」)。"""
    ok, lines, spec = spec_for(os.path.join(EXAMPLES, "sample1-storyboard.json"), (3840, 2160), 3600.0)
    held = [c for c in spec["clips"] if c["speed"] == 0]
    assert held
    for c in held:
        prev = next(p for p in spec["clips"] if p["end"] == c["start"])
        assert c["source_end"] == c["source_start"]
        # 直前の行のクリップと同じ素材の、止める瞬間のフレーム
        assert prev["source_start"] <= c["source_start"] <= prev["source_end"]


@pytest.mark.parametrize("name", ["sample1", "sample2"])
def test_example_wait_badges_match_skipped_time(name):
    """見本の肩テロップの「N秒後」「N分後」「N分M秒後」は、直前の行の素材の終わりからその行の素材の始まりまでの空き
    (見せない時間) を 10 秒単位で四捨五入した長さ。速さの表記 (N倍速・等倍) は check_final の badge_speed が見る。"""
    with open(os.path.join(EXAMPLES, name + "-storyboard.json"), encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    seen = 0
    for prev, row in zip(rows, rows[1:]):
        badge = row.get("badge") or ""
        if not badge.endswith("後"):
            continue
        gap = C.tc_to_sec(row["start"]) - C.tc_to_sec(prev["end"])
        minutes, secs = divmod(int(round(gap / 10.0) * 10), 60)
        want = f"{secs}秒後" if minutes == 0 else (f"{minutes}分後" if secs == 0 else f"{minutes}分{secs}秒後")
        assert gap > 0 and badge == want, (row["caption"], gap, badge)
        seen += 1
    assert seen


def test_unknown_ripple_is_refused():
    """組み立てのコードが使わない呼び方 (RippleTimeline True) は失敗を返し、黙って別の動きをしない。"""
    tl = R.Timeline("t", 30)
    mpi = R.MediaPoolItem("a.mp4", {"width": 1920, "height": 1080, "fps": 30, "duration": 10})
    it = R.TimelineItem(tl, mpi, tl.start, tl.start + 30, 0, 30)
    assert it.SetSpeed({"Percentage": 200.0}) is False
    assert it.SetSpeed({"Percentage": 200.0, "RippleTimeline": False}) is True
    assert (it.GetSourceStartFrame(), it.GetSourceEndFrame(), it.GetEnd() - it.GetStart()) == (0, 60, 30)


@pytest.mark.meiryo
def test_render_trial_storyboard(offline_work):
    """テスト用の構成表を小さなタイムライン (768x432) で簡易版にし、フレーム数・肩テロップ・マスクの黒塗りを画素で見る。
    素材は構成表の座標に合わせた 1920x1080 の灰色。見る位置は組み立て内容のマスクの値 (画面を 1 とした値) から読む。"""
    try:
        C.config().tool("ffmpeg")
    except SystemExit:
        pytest.skip("FFmpeg が無い")
    W, H = 768, 432
    src = os.path.join(offline_work, "sb2ffmpeg-src.mp4")
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", "color=c=gray:size=1920x1080:rate=30:duration=40", "-pix_fmt", "yuv420p", src])
    sb = C.config().load_storyboard(TRIAL, src)
    sb["timeline"] = {"width": W, "height": H}
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {src: {"width": 1920, "height": 1080, "fps": 30.0, "duration": 40.0}}, "t")
    masks = {t["name"]: t["inputs"] for c in spec["clips"] for t in c["fusion"]["tools"] if t["name"] in ("BadgeMask", "Mask1")}
    out = os.path.join(offline_work, "sb2ffmpeg.mp4")
    warns = D.render(spec, float(sb["fusion_text_scale"]), out)
    assert warns == ["音は入れない (簡易版)"]
    assert C.probe_video(out, count_frames=True)["frames"] == 480
    # クリップを同時に書いても、フレームの並びは同じ (フレームごとの MD5 で比べる)
    out3 = os.path.join(offline_work, "sb2ffmpeg-j3.mp4")
    assert D.render(spec, float(sb["fusion_text_scale"]), out3, jobs=3) == warns

    def md5s(path):
        p = os.path.join(offline_work, os.path.basename(path) + ".md5")
        C.run_tool("ffmpeg", ["-i", path, "-f", "framemd5", p])
        return [ln.rsplit(",", 1)[-1].strip() for ln in open(p, encoding="utf-8") if not ln.startswith("#")]
    assert md5s(out) == md5s(out3)

    def pixel(n, x, y):
        png = os.path.join(offline_work, f"f{n}.png")
        C.run_tool("ffmpeg", ["-i", out, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", png])
        return Image.open(png).convert("RGB").getpixel((round(x), round(y)))

    def near(a, b, d=40):
        return all(abs(p - q) <= d for p, q in zip(a, b))

    b = masks["BadgeMask"]   # 板の右の余白 (文字の外): 右の端から 8 px 内側、高さは中央
    plate = ((b["Center"][0] + b["Width"] / 2) * W - 8, (1 - b["Center"][1]) * H)
    orange = tuple(LOOK["badge"]["rgba"][:3])
    assert near(pixel(0, *plate), orange) and near(pixel(59, *plate), orange)
    assert not near(pixel(60, *plate), orange)   # 肩テロップは 2 秒 (60 フレーム) で消える
    m = masks["Mask1"]["Center"]
    assert near(pixel(300, m[0] * W, (1 - m[1]) * H), (0, 0, 0), 16)   # 3 行目の頭 (寄る前): マスクの中心が黒


def test_moving_black_mask_covers_all_keys(tmp_path):
    """動く黒塗りのマスクは、全部のキーフレームの範囲を覆う四角を MASK_PAD だけ広げて塗る (drawbox は時刻で動かせない)。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    sb["rows"][2]["masks"] = [{"type": "black", "x": 100, "y": 100, "w": 200, "h": 100,
                               "keys": [{"t": 0, "x": 100, "y": 100, "w": 200, "h": 100},
                                        {"t": 2, "x": 900, "y": 500, "w": 200, "h": 100}]}]
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    clip = D.Clip(spec["clips"][2], spec, float(LOOK["fusion_text_scale"]), str(tmp_path), [])
    boxes = [p for p in clip.graph()[0] if "drawbox" in p]
    p = D.MASK_PAD * 1080
    assert len(boxes) == 1 and f"x={100 - p:.1f}:y={100 - p:.1f}:w={1000 + 2 * p:.1f}:h={500 + 2 * p:.1f}" in boxes[0]


def test_render_frame_choice_and_fit(offline_work):
    """遅くする・倍速のクリップは素材の comp_frame(n, 倍率) 番目を映す。縦横比の違う素材は真ん中に置き、余りは黒。"""
    try:
        C.config().tool("ffmpeg")
    except SystemExit:
        pytest.skip("FFmpeg が無い")
    src = os.path.join(offline_work, "numbered.mp4")   # 4:3、フレーム i の明るさが 8i
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", "color=black:s=48x36:r=30:d=1,format=gray,geq=lum='N*8'",
                          "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", src])
    for pct, frames in ((50.0, 20), (250.0, 10)):
        n_src = round(frames * pct / 100)
        spec = {"timeline": {"width": 64, "height": 36, "fps": 30.0}, "media": [{"path": src, "width": 48, "height": 36, "fps": 30.0}],
                "clips": [{"start": 0, "end": frames, "media": 0, "source_start": 0, "source_end": n_src,
                           "speed": pct, "fusion": None}]}
        out = os.path.join(offline_work, f"speed{pct:g}.mp4")
        D.render(spec, float(LOOK["fusion_text_scale"]), out)
        C.run_tool("ffmpeg", ["-i", out, "-fps_mode", "passthrough", os.path.join(offline_work, f"s{pct:g}-%03d.png")])
        for n in range(frames):
            img = Image.open(os.path.join(offline_work, f"s{pct:g}-{n + 1:03d}.png")).convert("L")
            assert round(img.getpixel((32, 18)) / 8) == C.comp_frame(n, pct / 100), (pct, n)
            # 4:3 の 48x36 を 16:9 の 64x36 に収めると、左右 8 px ずつが黒
            assert img.getpixel((2, 18)) < 20 and img.getpixel((61, 18)) < 20


def test_storyboard_problems_stop_before_build(tmp_path, monkeypatch):
    sb = {"source": "missing.mp4", "output": "x.mp4", "rows": [{"start": "0:00", "end": "0:05", "caption": "a"}]}
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(sb), encoding="utf-8")
    monkeypatch.setattr(S, "probe_sources", lambda rows: {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                         "duration": 2.0} for r in rows})
    ok, summary, lines, spec, sb = F.run(type("A", (), {"storyboard": str(p), "source": None})())
    assert not ok and spec is None and "誤り" in summary


def ffmpeg_expr_value(e, it):
    """sb2ffmpeg の式 (if・lte・floor・round と四則だけ) を Python で計算する。FFmpeg の round は 0.5 を 0 から遠い方へ丸める。"""
    env = {"_if": lambda c, a, b: a if c else b, "lte": lambda a, b: a <= b, "floor": math.floor,
           "round": lambda x: math.floor(abs(x) + 0.5) * (1 if x >= 0 else -1), "it": it}
    return eval(e.replace("if(", "_if("), {"__builtins__": {}}, env)


@pytest.mark.parametrize("pct", [100.0, 150.0, 400.0, 66.66666666666667, 333.33333333333337])
def test_zoom_expr_matches_value_at_and_comp_frame(tmp_path, pct):
    """ズームの式は、Resolve 側の value_at(keys, comp_frame(n, 倍率)) と同じ値になる (割り切れない倍速の切り捨ても)。
    時刻が同じキーが並んでも、value_at と同じく後ろの値に飛ぶ。"""
    keys = [[0, 1.0], [45, 2.0], [45, 2.5], [100, 3.0]]
    clip = D.Clip({"start": 0, "end": 120, "speed": pct, "fusion": None},
                  {"timeline": {"width": 64, "height": 36, "fps": 30.0}}, float(LOOK["fusion_text_scale"]), str(tmp_path), [])
    e = clip.expr(keys)
    for n in range(120):
        want = C.value_at(keys, C.comp_frame(n, pct / 100))
        assert ffmpeg_expr_value(e, n / 30.0) == pytest.approx(want, abs=1e-9), (pct, n)


def test_speed_is_not_rounded():
    """組み立て内容の速さは丸めない。60 秒を 18 秒にする 333.333...% で、303 フレーム目が素材の 1010 を映す。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    sb["rows"] = [{"start": "0:00", "end": "1:00", "seconds": 18}]
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 70.0} for r in rows}, "t")
    k = spec["clips"][0]["speed"] / 100
    assert C.comp_frame(303, k) == 1010


def test_zoom_outside_the_picture_is_not_drawn(tmp_path):
    """画の外 (余りの黒) まで映すズームは、zoompan では位置がずれるので描かずに警告する。画の中に収まるズームは描く。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    sb["rows"] = [{"start": "0:00", "end": "0:05", "zoom": {"x": 0, "y": 0, "w": 400, "h": 1080, "seconds": 0}},
                  {"start": "0:05", "end": "0:10", "zoom": {"x": 1500, "y": 0, "w": 420, "h": 236, "seconds": 2}}]
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    warns = []
    graphs = [D.Clip(c, spec, float(LOOK["fusion_text_scale"]), str(tmp_path), warns).graph()[0] for c in spec["clips"]]
    assert not any("zoompan" in p for p in graphs[0]) and any("zoompan" in p for p in graphs[1])
    assert warns == ["0 フレーム目のクリップ: Zoom は画の外まで映す (zoompan では位置がずれる) ので描かない"]


def test_merge_blend_is_opacity(tmp_path):
    """Merge の Blend は重ねる画の濃さ。0.7 の板は 0.7 の濃さで、Blend が 0 の間は出さない。"""
    W, H = 64, 36
    bg = {"name": "Fill", "id": "Background", "links": {},
          "inputs": {"TopLeftRed": 0.0, "TopLeftGreen": 0.0, "TopLeftBlue": 0.0, "TopLeftAlpha": 1.0}}
    over = {"name": "FillOver", "id": "Merge", "inputs": {},
            "links": {"Background": {"tool": "MediaIn1"}, "Foreground": {"tool": "Fill"},
                      "Blend": {"keys": [[0, 0.0], [10, 0.0], [12, 0.7]]}}}
    tools = [{"name": "MediaIn1", "id": "MediaIn", "inputs": {}, "links": {}}, bg, over,
             {"name": "MediaOut1", "id": "MediaOut", "inputs": {}, "links": {"Input": {"tool": "FillOver"}}}]
    clip = D.Clip({"start": 0, "end": 30, "speed": 100.0, "fusion": {"tools": tools}},
                  {"timeline": {"width": W, "height": H, "fps": 30.0}}, float(LOOK["fusion_text_scale"]), str(tmp_path), [])
    parts, _ = clip.graph()
    assert len(parts) == 1 and "between(n,11,29)" in parts[0]
    assert Image.open(clip.pngs[0]).getpixel((0, 0))[3] == round(255 * 0.7)


def test_canvas_keeps_odd_size(tmp_path):
    """縦横比の違う素材の余白は、Resolve 側の fusion_canvas の大きさのまま作る。横長の素材 (例 3440x1440) を 16:9 にすると高さ 1935
    (奇数) で、YUV420 のまま pad すると 1934 に切り下がるので、先に 4:4:4 にする。比が同じなら大きさを変えるだけ。"""
    clip = D.Clip({"start": 0, "end": 1, "speed": 100.0, "fusion": None},
                  {"timeline": {"width": 1920, "height": 1080, "fps": 30.0}}, float(LOOK["fusion_text_scale"]), str(tmp_path), [])
    assert clip.canvas({"width": 3440, "height": 1440}) == "format=yuv444p,pad=3440:1935:0:248:color=black,scale=1920:1080"
    assert clip.canvas({"width": 3840, "height": 2160}) == "scale=1920:1080"


def test_overlay_images_are_read_once(tmp_path, monkeypatch):
    """重ねる画像は 1 フレームだけ読み、overlay が最後の画を出し続ける (-loop 1 で毎フレーム読み直さない)。
    止めたクリップは、1 フレームをタイムラインの大きさにしてから繰り返す。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    sb["rows"][0]["hold"] = {"seconds": 1, "at": "end"}
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    calls = []
    monkeypatch.setattr(D.C, "run_tool", lambda name, args: calls.append(args))
    for c in spec["clips"]:
        D.Clip(c, spec, float(LOOK["fusion_text_scale"]), str(tmp_path), []).render(spec["media"], str(tmp_path / "x.mp4"))
    assert calls and all("-loop" not in a for a in calls)
    graphs = [a[a.index("-filter_complex") + 1] for a in calls]
    assert all("overlay=eof_action=repeat" in g for g in graphs if "overlay" in g)
    held = [g for g, c in zip(graphs, spec["clips"]) if c["speed"] == 0]
    assert held and all(g.index("scale=") < g.index("loop=") for g in held)


def test_render_stops_queued_clips_after_a_failure(tmp_path, monkeypatch):
    """同時に書く途中でクリップが失敗したら、まだ始まっていないクリップは書かずに例外を出す。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    sb["rows"] = [{"start": f"0:{i:02d}", "end": f"0:{i + 1:02d}"} for i in range(8)]
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    started = []

    def fail_first(self, media, out):
        started.append(self.c["start"])
        if self.c["start"] == 0:
            raise RuntimeError("失敗")
        time.sleep(0.2)
    monkeypatch.setattr(D.Clip, "render", fail_first)
    with pytest.raises(RuntimeError):
        D.render(spec, float(LOOK["fusion_text_scale"]), str(tmp_path / "x.mp4"), jobs=2)
    assert len(started) < len(spec["clips"])


def test_clobbers_matches_same_file_by_other_spelling(tmp_path, monkeypatch):
    """書き出し先と構成表・素材は、相対パス・大文字小文字・ハードリンクで書き方が違っても、同じファイルなら同じとみなす。"""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "rec.mp4"
    src.write_bytes(b"keep")
    (tmp_path / "other.mp4").write_bytes(b"x")
    os.link(src, tmp_path / "alias.mp4")
    rel = os.path.join(".", "rec.mp4")
    assert F.clobbers([rel, "other.mp4", "alias.mp4"], [str(src)]) == [rel, "alias.mp4"]
    if os.name == "nt":
        assert F.clobbers([str(src).upper()], [str(src)]) == [str(src).upper()]
    assert F.clobbers(["a.build.json", "a.mp4"], [str(src), "sb.json"]) == []


@pytest.mark.parametrize("out", ["rec.build.json", "sb.json"])
def test_main_refuses_to_write_over_storyboard_or_source(tmp_path, monkeypatch, capsys, out):
    """--out の JSON か、そこから決まる簡易版の動画が構成表か素材と同じなら、何も書かずに止まる。"""
    for name in ("rec.mp4", "sb.json"):
        (tmp_path / name).write_bytes(b"keep")
    spec = {"media": [{"path": str(tmp_path / "rec.mp4")}]}
    monkeypatch.setattr(F, "run", lambda a: (True, "sb2ffmpeg: 組み立て内容", [], spec, {}))
    monkeypatch.setattr(D, "render", lambda *a: pytest.fail("描いてはいけない"))
    monkeypatch.setattr("sys.argv", ["sb2ffmpeg.py", str(tmp_path / "sb.json"), "--out", str(tmp_path / out)])
    assert F.main() == 1
    assert "構成表か素材と同じ" in capsys.readouterr().out
    assert (tmp_path / "rec.mp4").read_bytes() == b"keep" and (tmp_path / "sb.json").read_bytes() == b"keep"
    assert not (tmp_path / "rec.build.json").exists()


def test_render_joins_inside_its_temp_folder(tmp_path, monkeypatch):
    """つなぐ一覧はクリップのファイル名だけを書き、つないだ動画は一時フォルダーに書いてから書き出し先へ移す。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    monkeypatch.setattr(D.Clip, "render", lambda self, media, out: open(out, "wb").close())
    seen = {}

    def concat(name, args):
        lst = args[args.index("-i") + 1]
        seen["list"] = open(lst, encoding="utf-8").read().splitlines()
        seen["args"], seen["dir"] = args, os.path.dirname(lst)
        with open(args[-1], "wb") as f:
            f.write(b"joined")
    monkeypatch.setattr(D.C, "run_tool", concat)
    out = tmp_path / "x.mp4"
    D.render(spec, float(LOOK["fusion_text_scale"]), str(out), jobs=1)
    assert seen["list"] == [f"file c{c['start']:07d}.mp4" for c in spec["clips"]]
    assert "-safe" not in seen["args"] and os.path.dirname(seen["args"][-1]) == seen["dir"]
    assert out.read_bytes() == b"joined" and not os.path.exists(str(out) + ".part.mp4")


def test_parse_threads():
    assert D.parse_threads("auto") is None
    assert D.parse_threads("3,1,2") == {"decode": 3, "filter": 1, "encode": 2}
    for bad in ("3,1", "a,b,c", "0,1,1"):
        with pytest.raises(ValueError):
            D.parse_threads(bad)


def test_thread_options_go_to_input_filter_and_output(tmp_path, monkeypatch):
    """読み込みのスレッドは素材の -i より前、フィルターは -filter_complex_threads、書き出しは出力の直前。auto は何も付けない。"""
    sb = C.config().load_storyboard(TRIAL, "testsrc.mp4")
    rows = S.plan(sb)
    ok, lines, spec = F.build(sb, rows, {r["source"]: {"width": 1920, "height": 1080, "fps": 30.0,
                                                                 "duration": 40.0} for r in rows}, "t")
    calls = []
    monkeypatch.setattr(D.C, "run_tool", lambda name, args: calls.append(args))
    c = spec["clips"][0]
    D.Clip(c, spec, float(LOOK["fusion_text_scale"]), str(tmp_path), [], D.parse_threads("3,1,2")).render(spec["media"], "o.mp4")
    D.Clip(c, spec, float(LOOK["fusion_text_scale"]), str(tmp_path), []).render(spec["media"], "o.mp4")
    a, plain = calls
    src = a.index(spec["media"][c["media"]]["path"]) - 1
    assert a[a.index("-threads") + 1] == "3" and a.index("-threads") < src
    assert a[a.index("-filter_complex_threads") + 1] == "1"
    assert a[-3:] == ["-threads", "2", "o.mp4"]
    assert "-threads" not in plain and "-filter_complex_threads" not in plain


def test_render_part_does_not_import_the_build_part():
    """描く部分は組み立て内容だけで動く。組む部分と Resolve の代わりの部分を直接は読み込まない (common 経由の
    fusion・layout などの計算の部品への依存は見ない)。"""
    got = imports(os.path.join(C.REPO, "scripts", "sb2ffmpeg_render.py"))
    assert not got & {"sb2resolve", "resolve_stand_in", "sb2ffmpeg"}


def test_part_names_match_fusion(tmp_path):
    """描く側が名前で見分ける部品 (余白・配置・黒塗りのマスク) が、Resolve 側の組み立てで同じ名前で出る。"""
    sb = {"source": "wide.mp4", "fps": 30, "seconds_per_row": 4,
          "layout": {"screen": {"x": 100, "y": 60, "w": 2400, "h": 1300}, "anchor": "top-right"},
          "rows": [{"start": "0:00", "end": "0:04", "caption": "配置と黒塗り",
                    "masks": [{"type": "black", "x": 400, "y": 400, "w": 300, "h": 60}]}]}
    p = tmp_path / "parts.json"
    p.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    ok, lines, spec = spec_for(str(p), size=(3440, 1440))
    assert ok, lines
    names = {t["name"] for c in spec["clips"] if c["fusion"] for t in c["fusion"]["tools"]}
    assert {D.CANVAS, D.LAYOUT_SCREEN, D.LAYOUT_SCREEN_CROP} <= names
    assert any(D.BLACK_MASK.fullmatch(n) for n in names)


@pytest.mark.parametrize("argv,local,expect", [
    (["--jobs", "0"], {}, "--jobs は 1 以上の整数: 0"),
    (["--threads", "3,1"], {}, "--threads は auto か"),
    ([], {"sb2ffmpeg": {"threads": "0,1,1"}}, "config の sb2ffmpeg.threads は 数は 1 以上"),
    ([], {"sb2ffmpeg": {"jobs": -1}}, "config の sb2ffmpeg.jobs は 1 以上の整数: -1"),
])
def test_main_rejects_bad_jobs_and_threads(tmp_path, monkeypatch, capsys, argv, local, expect):
    """--jobs・--threads と設定の値が不正なら、どこの値かを書いた 1 行で止まり、組み立てに進まない。"""
    import settings
    cfg = tmp_path / "local.json"
    cfg.write_text(json.dumps(local), encoding="utf-8")
    monkeypatch.setenv("RESOLVE_CONFIG", str(cfg))
    monkeypatch.setattr(settings, "_CFG", None)
    monkeypatch.setattr(F, "run", lambda a: pytest.fail("組み立てに進んではいけない"))
    monkeypatch.setattr("sys.argv", ["sb2ffmpeg.py", "sb.json", *argv])
    assert F.main() == 1
    assert expect in capsys.readouterr().out
    monkeypatch.setattr(settings, "_CFG", None)
