"""止めたクリップ (SetSpeed の Percentage 0、Freeze Frame) が、どの素材のフレームを映し、その中の Fusion の時刻が進むか。

フレームの番号を色にした合成の素材 (R = 番号 mod 16 × 16、G = 番号 ÷ 16 × 16) を作り、等速のクリップ A (素材 0〜59)・
C (120〜149)・E (200〜229) と、まとめて置く止めたクリップ G (260〜289) を 1 回の AppendToTimeline で置く。
その後に止めたクリップ B (素材 118 で終わる 60 フレーム)・D (179 で終わる 30)・F (249 で終わる 30) を 1 つずつ置いて
すぐに Percentage 0 にし (sb2resolve.py と同じ置き方)、最後に G を Percentage 0 にする。
B の左半分には、合成の 0〜30 フレームで不透明度が 0 → 1 になる黒を重ねる (黒なら書き出しの色は 下の画 × (1 − 不透明度)
になるので、左右の明るさの比で不透明度が読める。赤などの色は不透明度が色に掛からないまま重なるので使わない)。
見ること: B の長さ (60 フレーム) と後ろの位置、B・D・F の各フレームが映す素材の番号 (置いた範囲の最後のフレーム) と
GetSourceStartFrame の読み戻しが同じか、黒の不透明度が時間で変わるか。G (まとめて置いたもの) が映すフレームは記録だけ。
21.1.1.10 での結果: 1 つずつ置いてすぐ止めると、どれも置いた範囲の最後のフレームを映し、読み戻しも同じ。
まとめて置いて止めると、最初に止めたものとタイムラインの先頭のもの以外は範囲の頭のフレームを映す。
止めたクリップの中の Fusion の時刻は等速で進む (不透明度は 0・15・30 フレーム目で 0・0.5・1)。
作ったプロジェクトは最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/hold_freeze.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import C, Trial, require_lock  # noqa: E402
import check_final as CF  # noqa: E402

prev = None
t = None


def make_source():
    path = os.path.join(C.config().work_dir("test-media"), "frame-index.mp4")
    if not os.path.isfile(path):
        C.run_tool("ffmpeg", ["-f", "lavfi", "-i", "color=black:s=640x360:r=30:d=10", "-vf",
                              "format=rgb24,geq=r='mod(N,16)*16':g='floor(N/16)*16':b=0",
                              "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p", path])
    return path


def index_of(img, x, y):
    """書き出したフレームの (x, y) の色から、素材のフレームの番号を読む。"""
    p = img[y - 4:y + 4, x - 4:x + 4].reshape(-1, 3).mean(axis=0)
    return int(round(p[0] / 16)) + 16 * int(round(p[1] / 16))


require_lock()   # 接続の前に、resolve_lock.py run で包まれているかを確認する
cur = C.connect_or_exit().GetProjectManager().GetCurrentProject()
prev = cur.GetName() if cur else None
t = Trial("hold_freeze", need_source=False)
try:
    t.source = make_source()
    clip = t.clip()
    tl = t.timeline("hold")
    t0 = tl.GetStartFrame()
    got = t.mp.AppendToTimeline([
        {"mediaPoolItem": clip, "startFrame": 0, "endFrame": 60, "mediaType": 1, "recordFrame": t0},
        {"mediaPoolItem": clip, "startFrame": 120, "endFrame": 150, "mediaType": 1, "recordFrame": t0 + 120},
        {"mediaPoolItem": clip, "startFrame": 200, "endFrame": 230, "mediaType": 1, "recordFrame": t0 + 180},
        {"mediaPoolItem": clip, "startFrame": 260, "endFrame": 290, "mediaType": 1, "recordFrame": t0 + 240}])
    at = {g.GetStart() - t0: g for g in got}
    # 止めたクリップ: (置く位置, 範囲の最後のフレーム, フレーム数)。1 つずつ置いてすぐ止める
    HOLDS = {"B": (60, 118, 60), "D": (150, 179, 30), "F": (210, 249, 30)}
    holds, readback = {}, {}
    for n, (rec, last, hn) in HOLDS.items():
        it = (t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": last + 1 - hn, "endFrame": last + 1,
                                      "mediaType": 1, "recordFrame": t0 + rec}]) or [None])[0]
        t.check(f"{n} を Percentage 0 にできる", bool(it) and it.SetSpeed({"Percentage": 0.0, "RippleTimeline": False}),
                it.GetSpeed() if it else None)
        holds[n] = it
        readback[n] = it.GetSourceStartFrame() if it else None
    at[240].SetSpeed({"Percentage": 0.0, "RippleTimeline": False})
    readback["G"] = at[240].GetSourceStartFrame()
    t.note("readback", readback)
    hold = holds["B"]
    t.check("止めたクリップの長さは 60 のまま、後ろは動かない",
            (hold.GetEnd() - hold.GetStart(), at[120].GetStart() - t0) == (60, 120),
            (hold.GetEnd() - hold.GetStart(), at[120].GetStart() - t0), (60, 120))
    t.note("hold_source", (hold.GetSourceStartFrame(), hold.GetSourceEndFrame(), hold.GetSpeed()))
    comp = hold.AddFusionComp()
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    bg = comp.AddTool("Background", -32768, -32768)
    for k in ("TopLeftRed", "TopLeftGreen", "TopLeftBlue"):
        bg.SetInput(k, 0.0)
    bg.ConnectInput("TopLeftAlpha", comp.BezierSpline())
    bg.SetInput("TopLeftAlpha", 0.0, 0)
    bg.SetInput("TopLeftAlpha", 1.0, 30)
    mk = comp.AddTool("RectangleMask", -32768, -32768)
    mk.SetInput("Center", {1: 0.25, 2: 0.5})
    mk.SetInput("Width", 0.5)
    mk.SetInput("Height", 1.0)
    bg.EffectMask = mk.Mask
    mg = comp.AddTool("Merge", -32768, -32768)
    mg.Background = mi.Output
    mg.Foreground = bg.Output
    mo.Input = mg.Output
    video, st = t.render(tl, "hold_freeze")
    t.check("書き出せた", video is not None, st)
    if video:
        frames = {f: CF.read_frame(video, f, 1920, 1080) for f in (59, 60, 75, 90, 119, 120, 150, 179, 210, 239, 240, 269)}
        idx = {f: index_of(img, 1440, 540) for f, img in frames.items()}
        # 左 (黒を重ねた側) と右の明るさの比から不透明度を読む
        op = {f: round(1 - float(img[530:550, 470:490].mean()) / float(img[530:550, 1430:1450].mean()), 3)
              for f, img in frames.items() if f in (60, 75, 90, 119)}
        t.note("source_index", idx)
        t.note("opacity", op)
        t.check("A の最後は素材の 59、C の頭は素材の 120", (idx[59], idx[120]) == (59, 120), idx)
        shown = {"B": {idx[f] for f in (60, 75, 90, 119)}, "D": {idx[f] for f in (150, 179)},
                 "F": {idx[f] for f in (210, 239)}, "G": {idx[f] for f in (240, 269)}}
        t.note("held_frame", {n: sorted(v) for n, v in shown.items()})
        for n, (rec, last, hn) in HOLDS.items():
            t.check(f"止めたクリップ {n} は置いた範囲の最後のフレーム ({last}) を映す", shown[n] == {last}, sorted(shown[n]), last)
            t.check(f"{n} の GetSourceStartFrame は映すフレームと同じ", {readback[n]} == shown[n], readback[n], sorted(shown[n]))
        t.check("止めたクリップの中の Fusion の時刻は等速で進む (黒の不透明度が 0・0.5・1・1)",
                abs(op[60]) < 0.05 and abs(op[75] - 0.5) < 0.05 and op[90] > 0.95 and op[119] > 0.95, op)
finally:
    closed = t.pm.CloseProject(t.proj)
    t.note("deleted", bool(closed) and bool(t.pm.DeleteProject(t.project_name)))
    if prev:
        t.note("reopened", bool(t.pm.LoadProject(prev)))
    path = os.path.join(t.out, f"{t.name}.json")
    C.write_json(path, {"name": t.name, "info": t.info, "checks": t.checks})
bad = [c for c in t.checks if not c["ok"]]
sys.exit(C.report(not bad, f"{t.name}: {len(t.checks) - len(bad)}/{len(t.checks)} が期待どおり (止めたフレーム "
                  f"{t.info.get('held_frame')})", path,
                  [f"NG {c['label']}  (結果 {c['actual']!r}、期待 {c['expected']!r})" for c in bad]))
