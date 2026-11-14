"""scripts/check_final.py が、実機の Resolve のタイムラインと書き出しで、わざと入れたずれを見つけるか。

sb2resolve.py の部品 (import_media / build_timeline / add_fusion) でテスト用の構成表のタイムラインを組み、次の 8 つを照らし合わせる。
- 0 そのまま                         → fail 0、unknown 0
- 1 肩テロップを 1 フレーム長く残す (Blend のキー) → badge と、画素の badge_pixels (肩テロップが消えるはずのフレーム) が fail
- 2 マスクの幅を 1 割小さくする        → mask1 が fail
- 3 ズームの終わりを 1 フレーム遅らせる    → zoom_size が fail
- 4 1 行目を 1 フレーム長くした別のタイムライン → position と video_frames が fail
- 5 1 行目のテロップの文を変える (CaptionText の StyledText) → caption が fail
- 6 1 行目のテロップの背景を不透明にする (CaptionBar の TopLeftAlpha) → caption_bar が fail
- 7 1 行目のテロップを頭の 15 フレームだけ消す (CaptionOver の Blend のキー) → caption_time が fail
構成表は tests/trial-storyboard.json のまま (肩テロップは 1 行目、マスクとズームは 3 行目のクリップの Fusion。テロップは全部の行)。
作ったプロジェクトは最後に閉じて消し、始めに開いていたプロジェクトを開き直す。書き出した動画と結果は 出力先/tests/ に残る。
3 で動かしたズームのキーは 3 を照らした後に戻す (5〜7 に zoom_size の fail を残さない)。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/check_final_cases.py [--source 素材]
合格: 8 つの場合がどれも期待どおりで、片付けと開き直しができる (項目 9 件)。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import C, Trial, finish_projects  # noqa: E402
import check_final as CF  # noqa: E402
import sb2resolve as S  # noqa: E402

t = Trial("check_final_cases")
SB = t.cfg.load_storyboard(os.path.join(C.REPO, "tests", "trial-storyboard.json"), t.source)
FONT_FILE = C.caption_font(SB)
results = {}


def assemble(sb, name, fusion=True):
    """sb2resolve.py と同じ部品で組む。返す値は (タイムライン, 行ごとのクリップ)。"""
    rows = S.plan(sb)
    media = S.import_media(t.mp, rows, t.fps)
    tl, items, _, res = S.build_timeline(t.proj, t.mp, sb, rows, media, name)
    if tl is None or not all(r.get("match") for r in res):
        t.note("stopped", res)
        raise SystemExit(f"組めない: {name}")
    if fusion and not S.add_fusion(items, sb, rows, media, FONT_FILE)[0]:
        raise SystemExit(f"Fusion を組めない: {name}")
    return tl, items


def check(case, tl, video, sb=SB):
    rep = CF.run(sb, video, tl, pixel=video is not None)
    bad = {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"] if c["status"] != "pass"}
    results[case] = {"summary": rep["summary"], "not_pass": bad, "video": video}
    return bad


try:
    tl, items = assemble(SB, "base")
    comp1, comp = items[0].GetFusionCompByIndex(1), items[2].GetFusionCompByIndex(1)   # 肩テロップは 1 行目、マスクとズームは 3 行目
    tools1, tools = list(comp1.GetToolList(False).values()), list(comp.GetToolList(False).values())
    video, st = t.render(tl, "check_final-0")
    bad = check("0", tl, video)
    t.check("0 そのまま: fail と unknown が無い", not bad and video, bad, {})

    # 1 肩テロップを 1 フレーム長く残す
    last = round(SB["badge"]["seconds"] * t.fps)
    badge = next(m for m in tools1 if m.ID == "Merge" and m.GetInput("Blend", 0) != m.GetInput("Blend", last + 1))

    def blend_keys(off):
        sp = comp1.BezierSpline()
        badge.ConnectInput("Blend", sp)
        sp.SetKeyFrames({0: {1: 1.0}, off - 1: {1: 1.0}, off: {1: 0.0}}, True)
    blend_keys(last + 1)
    video, st = t.render(tl, "check_final-1")
    bad = check("1", tl, video)
    t.check("1 肩テロップを残す: badge と画素の検査が fail", bad.get("badge@1") == "fail"
            and bad.get(f"badge_pixels_{last}@1") == "fail", bad, {"badge@1": "fail", f"badge_pixels_{last}@1": "fail"})
    blend_keys(last)

    # 2 マスクを小さくする (動画は 0 のものを使う。見るのは API の値)
    m = SB["rows"][2]["masks"][0]
    mask = next(x for x in tools if x.ID == "RectangleMask" and abs(x.GetInput("Width", 0) - m["w"] / t.W) < 1e-6)
    mask.SetInput("Width", m["w"] * 0.9 / t.W)
    bad = check("2", tl, None)
    t.check("2 マスクを小さくする: mask1 が fail", bad.get("mask1@3") == "fail", bad, {"mask1@3": "fail"})
    mask.SetInput("Width", m["w"] / t.W)

    # 3 ズームの終わりを 1 フレーム遅らせる (倍率のキーだけ動かす)
    xf = next(x for x in tools if x.ID == "Transform")
    exp = CF.expected_rows(SB)[2]["zoom"]
    zf = exp["frames"]
    sp = comp.BezierSpline()
    xf.ConnectInput("Size", sp)
    sp.SetKeyFrames({0: {1: exp["size"][0]}, zf + 1: {1: exp["size"][1]}}, True)
    bad = check("3", tl, None)
    t.check("3 ズームを 1 フレーム遅らせる: zoom_size が fail", bad.get("zoom_size@3") == "fail", bad, {"zoom_size@3": "fail"})
    sp.SetKeyFrames({0: {1: exp["size"][0]}, zf: {1: exp["size"][1]}}, True)   # 戻す (5〜7 に zoom_size の fail を残さない)

    # 5〜7 1 行目のテロップを崩す (動画は使わない。見るのは API の値)。崩したものは戻す
    text, bar, over = (comp1.FindTool(n) for n in ("CaptionText", "CaptionBar", "CaptionOver"))
    words = text.GetInput("StyledText", 0)
    text.SetInput("StyledText", words + "x")
    bad = check("5", tl, None)
    t.check("5 テロップの文を変える: caption が fail", bad.get("caption@1") == "fail", bad, {"caption@1": "fail"})
    text.SetInput("StyledText", words)
    alpha = bar.GetInput("TopLeftAlpha", 0)
    bar.SetInput("TopLeftAlpha", 1.0)
    bad = check("6", tl, None)
    t.check("6 テロップの背景を不透明にする: caption_bar が fail", bad.get("caption_bar@1") == "fail", bad,
            {"caption_bar@1": "fail"})
    bar.SetInput("TopLeftAlpha", alpha)
    sp = comp1.BezierSpline()
    over.ConnectInput("Blend", sp)
    sp.SetKeyFrames({0: {1: 0.0}, 14: {1: 0.0}, 15: {1: 1.0}}, True)
    bad = check("7", tl, None)
    t.check("7 テロップを頭の 15 フレームだけ消す: caption_time が fail", bad.get("caption_time@1") == "fail", bad,
            {"caption_time@1": "fail"})
    sp.SetKeyFrames({0: {1: 1.0}}, True)

    # 4 1 行目を 1 フレーム長くした別のタイムライン
    shifted = json.loads(json.dumps(SB))
    shifted["rows"][0].update(end=5 + 1 / t.fps, seconds=5 + 1 / t.fps)   # 等速のまま 1 フレーム長い行
    tl4, _ = assemble(shifted, "shift", fusion=False)
    video, st = t.render(tl4, "check_final-4")
    bad = check("4", tl4, video)
    t.check("4 1 フレームずらす: position と video_frames が fail",
            bad.get("position@1") == "fail" and bad.get("video_frames") == "fail", bad,
            {"position@1": "fail", "video_frames": "fail"})
finally:
    t.note("cases", results)
    # 作ったプロジェクトを閉じて消し、始めに開いていたプロジェクトを開き直す (finish の SaveProject は使わない)
    code = finish_projects(t.resolve, t.name, os.path.join(t.out, f"{t.name}.json"), [t.project_name], t.prev, t.info,
                           t.checks, min_checks=9)
sys.exit(code)
