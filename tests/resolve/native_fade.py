"""Resolve の標準のフェードで、30fps の [90,150) に出したラベルを終わりの 9 フレーム (141〜149) で消せるか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/native_fade.py
(Resolve Studio を起動しておく。結果は 出力先/tests/native_fade.json。書き出した動画と PNG は 作業フォルダー/native_fade に残る)

組むもの: プロジェクト test-native-fade (1920x1080、30fps) を作り直し (あれば消してから作る)、人が GUI で確認するために消さずに残す。
素材は make_source の patch (testsrc2 の右上の 4 分の 1 が動かない灰色)。どのタイムラインも V1 に素材の頭から 180 フレーム。
ラベルは灰色の所に置く黄色の板 (x 1200〜1600、y 300〜420)。消え方を板の内側の均一な所で測るので、文字は入れない。
- fade-none        効果なし (V1 だけ)。比べる元
- fade-full        完全表示の基準: 板だけを描いた透過 PNG (1920x1080、Pillow で作る。Alpha mode は Straight) を V2 の [90,150) に置く。
                   フェードなし。静止画をそのまま置くと既定の 150 フレームになる (21.1.1) ので、PNG を補助のタイムライン
                   fade-label-still に置き、そのタイムラインの項目を startFrame 0・endFrame 60 で置く (resolve_helpers.still_source)。
                   環境設定 (Standard still duration) は変えない
- fade-setfades    (1) 同じ置き方の V2 のクリップに TimelineItem.SetFades({"FadeIn": 0, "FadeOut": 9})
- fade-blend       (2) V1 のクリップの Fusion に同じ板 (Background + RectangleMask の LabelBox) を Merge (LabelOver) で重ね、
                   Blend のキーを 0: 0、89: 0、90: 1、141: 1、150: 0 (直線。149 は 1/9)。キーは common._animate で入れ、0 フレームの
                   値を書く (書かないと 0 フレームに 1 のキーが残り、0〜88 に薄く出た。21.1.1)
- fade-transition  (3) 同じ置き方の V2 のクリップの終わりに AddTransition (Cross Dissolve、simple、end、left、9 フレーム)
- fade-title-probe 記録だけ: 再生ヘッドを 90 フレーム目に置いて InsertFusionTitleIntoTimeline("Text+") を呼び、置かれた位置・長さ・
                   トラックと、SetFades の読み戻しを残す (位置と長さを渡す引数が無いので、[90,150) の判定には使わない)
書き出しは、どれも同じ可逆の形式で全編 (形式は fade-none を書き出すときに resolve_helpers.render_lossless で選ぶ)。

測り方: フレームが同じかは framemd5 で全フレームを比べる。表示の割合 u は、板の内側 (6 px 内) で
u = (R - 効果なし)・E / E・E (E は完全表示の 100 フレーム目と効果なしの差)。u は 85〜155 フレームで測る。

合格の条件 (どれも pass。確認できないものは unknown で、合格にしない):
- 素材と PNG を取り込め、可逆の形式を選べて効果なしの全編 (180 フレーム) を FFmpeg で読める
- 完全表示の基準: PNG を V2 の [90,150) (60 フレーム) に置け、90〜149 だけが効果なしと違い、板の内側が黄色 (各色 ±2)
- (1)〜(3) のそれぞれで: API で作れた。読み戻しが渡した値 ((1) GetFades、(2) Blend のキー、(3) トランジションの長さ 9 で
  150 で終わり、クリップは [90,150) のまま。(1) と (3) はクリップが [90,150) に置けたことも含む)。書き出せた (180 フレーム)。0〜89 と 150〜179 は効果なしと同じ画素。
  90〜140 は板の内側が完全表示と各色 ±2 で同じ。140〜150 で u が増えず、149 の u が 0.01 より大きく 0.99 より小さい
  (150 の前に消え始め、149 には残る)。
  85〜155 で、板の外 (2 px 広げた外) が効果なしと各色 ±2 で同じ
- 残すプロジェクトを保存でき、始めに開いていたプロジェクトを開き直せた
記録 (info): 方法ごとの組み方と読み戻し (methods の build)、89・90・140・141・149・150 の u と効果なしとの一致 (boundary)、
85〜155 の u (curve)、全部の項目が pass の方法 (usable)、タイトルの記録 (title_probe。タイトルも既定の 150 フレームになった)、
補助のタイムラインの項目の属性 (still)。補助のタイムラインで長さが合わなければ、置いた後に長さを変える関数は .pyi に無いので、
(1)・(3) と完全表示の基準は fail になり、環境設定は変えずに止める。

GUI で確認すること (人。JSON では unknown で、合格に数えない):
1. プロジェクト test-native-fade を開く
2. タイムライン fade-setfades の V2 のクリップ ([90,150)) の右上のフェードのハンドルを動かし、デリバーページで書き出して変わるか見る
3. タイムライン fade-blend の V1 のクリップを Fusion ページで開き、LabelOver の Blend のキーをスプラインの画面で動かす
4. タイムライン fade-transition のトランジションの端をドラッグして長さを変える
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from resolve_helpers import (animated_keys, append_at, box_mean, differing, diff_outside, finish_projects,  # noqa: E402
                             frames_of, fresh_project, make_check, make_source, render_set, require_lock, spans,
                             still_source)

NAME = "native_fade"
PROJECT = "test-native-fade"
W, H, FPS, L = 1920, 1080, 30, 180    # テストのフレームの番号 (30fps の [90,150) など) はこの大きさとフレームレートで決めてある
SHOW, END, FADE = 90, 150, 9          # [90,150) に出し、終わりの 9 フレーム (141〜149) で消す
LABEL = (1200, 300, 1600, 420)        # 板 (x0, y0, x1, y1。右と下は含まない)。灰色の所 (x 960〜1920、y 0〜540) の中
INNER = (1206, 306, 1594, 414)        # 測る所 (板の 6 px 内)
YELLOW = (255, 200, 0)
BLEND_KEYS = {0: 0.0, SHOW - 1: 0.0, SHOW: 1.0, END - FADE: 1.0, END: 0.0}
TRANSITION = {"type": "Cross Dissolve", "category": "simple", "position": "end", "alignment": "left", "duration": FADE}
METHODS = {"setfades": "(1) SetFades", "blend": "(2) Fusion の Blend のキー", "transition": "(3) AddTransition"}
LOOK = range(SHOW - 5, END + 6)       # 画素で見るフレーム (85〜155)
BOUNDARY = (89, 90, 140, 141, 149, 150)
TOL = 2


def label_png(path):
    """板だけを描いた透過 PNG (W x H)。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([LABEL[0], LABEL[1], LABEL[2] - 1, LABEL[3] - 1], fill=YELLOW + (255,))
    img.save(path)


def blend_label(item):
    """V1 のクリップの Fusion に板を重ね、LabelOver の Blend に BLEND_KEYS を入れる。返す値は記録。"""
    comp = item.AddFusionComp()
    if not comp:
        return {"comp": False}
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    x0, y0, x1, y1 = LABEL
    box = C.solid(comp, YELLOW + (255,), ((x0 + x1) / 2 / W, 1 - (y0 + y1) / 2 / H), (x1 - x0) / W, (y1 - y0) / H,
                  name="LabelBox")
    over = comp.AddTool("Merge", -32768, -32768)
    over.SetAttrs({"TOOLS_Name": "LabelOver"})
    over.Background = mi.Output
    over.Foreground = box.Output
    C._animate(comp, over, "Blend", sorted(BLEND_KEYS.items()))
    mo.Input = over.Output
    return {"comp": True, "keys": animated_keys(over, "Blend")}


def item_span(item, t0):
    return {"start": item.GetStart() - t0, "end": item.GetEnd() - t0, "duration": item.GetDuration()} if item else None


def build(key, tl, v1, t0):
    """方法 key で板を出す。返す値は (作れたか, 読み戻しが渡した値か, 記録)。"""
    rec = {}
    if key == "blend":
        rec["fusion"] = blend_label(v1)
        made = bool(rec["fusion"].get("comp"))
        return made, (rec["fusion"].get("keys") == BLEND_KEYS) if made else None, rec
    item, rec["place"] = append_at(mp, tl, still, SHOW, END - SHOW, track=2)
    placed = rec["place"] == {"start": SHOW, "duration": END - SHOW, "track": ["video", 2]}
    if not item:
        return False, None, rec
    if key == "setfades":
        rec["set"] = bool(item.SetFades({"FadeIn": 0, "FadeOut": FADE}))
        fades = item.GetFades() or {}
        rec["readback"] = fades
        same = {k: float(v) for k, v in fades.items()} == {"FadeIn": 0.0, "FadeOut": float(FADE)}
        return rec["set"], (placed and same) if rec["set"] else None, rec
    tr = item.AddTransition(TRANSITION)
    rec["transition"] = (dict(item_span(tr, t0), type=tr.GetType(), name=tr.GetName())) if tr else None
    rec["clip_after"] = item_span(item, t0)
    rec["track_items"] = [dict(item_span(x, t0), type=x.GetType()) for x in (tl.GetItemListInTrack("video", 2) or [])]
    if not tr:
        return False, None, rec
    t = rec["transition"]
    ok = (placed and t["duration"] == FADE and t["end"] == END
          and rec["clip_after"] == {"start": SHOW, "end": END, "duration": END - SHOW})
    return True, ok, rec


def title_probe(tl, t0):
    """再生ヘッドを SHOW に置いて Text+ のタイトルを差し込み、置かれた所と SetFades の読み戻しを記録する (判定はしない)。"""
    rec = {"set_timecode": bool(tl.SetCurrentTimecode(C.frame_to_timecode(t0 + SHOW, FPS))), "timecode": tl.GetCurrentTimecode()}
    title = tl.InsertFusionTitleIntoTimeline("Text+")
    rec["inserted"] = bool(title)
    if title:
        rec.update(item_span(title, t0), track=title.GetTrackTypeAndIndex(), type=title.GetType())
        rec["set_fades"] = bool(title.SetFades({"FadeIn": 0, "FadeOut": FADE}))
        rec["fades"] = title.GetFades()
    return rec


def crop(img, box):
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1].astype(float)


def fade_judge(tail):
    """140〜150 の表示の割合 tail (11 個) で、消え方が合っているか: 増えず、149 は完全表示より小さく 0 より大きい
    (0.01 < u < 0.99。150 の前に消え始め、149 に残る)。140〜149 が 1 のまま 150 で 0 になる切り替えは通さない。"""
    return (None not in tail and all(b <= a + 0.005 for a, b in zip(tail, tail[1:]))
            and 0.01 < tail[-2] < 0.99)


def analyse(key):
    """方法 key の書き出しを、効果なしと完全表示の書き出しと比べる。返す値は記録 (書き出しが無ければ None)。"""
    import numpy as np
    m5 = md5.get(key)
    if not m5:
        return None
    res = {"frames": len(m5), "differ_from_none": spans(differing(m5, md5["none"]))}
    res["off_same"] = len(m5) == L and all(m5[f] == md5["none"][f] for f in range(L) if not SHOW <= f < END)
    u, vs_full, outside = {}, {}, {}
    for n, fr in frames_of({"r": renders[key]["path"], "b": renders["none"]["path"], "f": renders["full"]["path"]}, LOOK):
        r, b, f = fr["r"], fr["b"], fr["f"]
        if r is None or b is None or f is None:
            continue
        u[n] = round(float(((crop(r, INNER) - crop(b, INNER)) * E).sum() / (E * E).sum()), 4)
        if SHOW <= n < END - FADE:
            vs_full[n] = float(np.abs(crop(r, INNER) - crop(f, INNER)).max())
        outside[n] = diff_outside(r, b, [LABEL], pad=2)
    res["curve"] = u
    res["boundary"] = {f: {"u": u.get(f), "same_as_none": f < len(m5) and m5[f] == md5["none"][f]} for f in BOUNDARY}
    res["full_max_diff"] = max(vs_full.values()) if vs_full else None
    res["full_ok"] = len(vs_full) == END - FADE - SHOW and res["full_max_diff"] <= TOL
    tail = [u.get(f) for f in range(END - FADE - 1, END + 1)]   # 140〜150
    res["fade_tail"] = tail
    res["fade_ok"] = fade_judge(tail)
    res["outside_max"] = max(outside.values()) if outside else None
    res["outside_ok"] = len(outside) == len(LOOK) and res["outside_max"] <= TOL
    return res


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    checks, info = [], {"project": PROJECT, "methods": {}}
    check = make_check(checks)
    path = os.path.join(OUT, f"{NAME}.json")
    require_lock()
    resolve = C.connect_resolve(quiet=True)
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    prev = cur.GetName() if cur else None
    info["previous_project"] = prev
    info["product"] = f"{resolve.GetProductName()} {resolve.GetVersionString()}"
    proj = None
    try:
        src = make_source(cfg, W, H, FPS, patch=True)
        png = os.path.join(DIR, "fade-label.png")
        label_png(png)
        proj, info["fresh"] = fresh_project(pm, PROJECT, W, H, FPS)
        if not proj:
            raise RuntimeError(f"プロジェクト {PROJECT} を作れない ({info['fresh']})")
        mp = proj.GetMediaPool()
        src_clip = (mp.ImportMedia([src]) or [None])[0]
        png_clip = (mp.ImportMedia([png]) or [None])[0]
        check("素材と PNG を取り込めた", bool(src_clip and png_clip), [bool(src_clip), bool(png_clip)])
        if not (src_clip and png_clip):
            raise RuntimeError("素材か PNG を取り込めない")
        info["alpha_mode"] = {"set": bool(png_clip.SetClipProperty("Alpha mode", "Straight")),
                              "readback": png_clip.GetClipProperty("Alpha mode")}
        still, info["still"] = still_source(mp, proj, png_clip, "fade-label-still")
        if not still:
            raise RuntimeError(f"PNG を置いた補助のタイムラインを作れない ({info['still']})")

        def timeline(name):
            tl = mp.CreateEmptyTimeline(name)
            proj.SetCurrentTimeline(tl)
            v1, _ = append_at(mp, tl, src_clip, 0, L)
            return tl, v1, tl.GetStartFrame()

        tls = {}
        tls["none"], _, t0 = timeline("fade-none")
        tls["full"], _, t0 = timeline("fade-full")
        _, rec = append_at(mp, tls["full"], still, SHOW, END - SHOW, track=2)
        info["full_place"] = rec
        check("完全表示の基準: PNG を V2 の [90,150) (60 フレーム) に置けた", rec == {"start": SHOW, "duration": END - SHOW,
                                                               "track": ["video", 2]}, rec)
        made = {}
        for key, label in METHODS.items():
            tl, v1, t0 = timeline(f"fade-{key}")
            tls[key] = tl
            try:
                ok, same, rec = build(key, tl, v1, t0)
            except Exception as e:   # 1 つの方法が例外で止まっても、ほかの方法は試す
                ok, same, rec = False, None, {"error": f"{type(e).__name__}: {e}"}
            made[key] = ok
            info["methods"][key] = {"build": rec}
            check(f"{label}: API で作れた", ok, rec)
            check(f"{label}: 読み戻しが渡した値", same, rec, {"fades": {"FadeIn": 0, "FadeOut": FADE}, "blend": BLEND_KEYS,
                                                             "transition": TRANSITION}.get(key, key))
        tl, _, t0 = timeline("fade-title-probe")
        try:
            info["title_probe"] = title_probe(tl, t0)
        except Exception as e:
            info["title_probe"] = {"error": f"{type(e).__name__}: {e}"}

        # 書き出し: 効果なしで可逆の形式を選び、同じ形式でほかを書き出す
        rs = render_set(proj, tls, "none", ["full"] + [k for k in METHODS if made[k]], DIR, NAME)
        info.update(rs["info"])
        renders, md5 = rs["renders"], rs["md5"]
        check("可逆の形式を選べ、効果なしの全編を FFmpeg で読める (180 フレーム)", bool(rs["chosen"]) and len(md5["none"]) == L,
              {"chosen": rs["chosen"], "frames": len(md5["none"])}, L)

        # 完全表示の基準
        E = None
        if md5.get("full"):
            d = differing(md5["full"], md5["none"])
            check("完全表示の基準: 90〜149 だけが効果なしと違う", d == list(range(SHOW, END)), spans(d), [[SHOW, END - 1]])
            fr = dict(frames_of({"b": renders["none"]["path"], "f": renders["full"]["path"]}, [100]))[100]
            if fr["b"] is not None and fr["f"] is not None:
                E = crop(fr["f"], INNER) - crop(fr["b"], INNER)
                mean = box_mean(fr["f"], INNER)
                check("完全表示の基準: 板の内側が黄色 (各色 ±2)", all(abs(a - b) <= TOL for a, b in zip(mean, YELLOW)), mean, YELLOW)
            else:
                check("完全表示の基準: 板の内側が黄色 (各色 ±2)", None, "100 フレーム目を取り出せない")
        else:
            check("完全表示の基準: 90〜149 だけが効果なしと違う", None, "書き出せない")
            check("完全表示の基準: 板の内側が黄色 (各色 ±2)", None, "書き出せない")

        usable = []
        for key, label in METHODS.items():
            res = analyse(key) if E is not None else None
            info["methods"][key]["result"] = res
            n0 = len(checks)
            check(f"{label}: 書き出せた (180 フレーム)", (res["frames"] == L) if res else None, res and res["frames"], L)
            check(f"{label}: 0〜89 と 150〜179 は効果なしと同じ画素", res["off_same"] if res else None,
                  res and res["differ_from_none"], [[SHOW, END - 1]])
            check(f"{label}: 90〜140 は板の内側が完全表示と同じ (各色 ±2)", res["full_ok"] if res else None,
                  res and res["full_max_diff"], TOL)
            check(f"{label}: 140〜150 で表示の割合が増えず、149 は消え始めていて残る", res["fade_ok"] if res else None,
                  res and res["fade_tail"], "140〜150 が増えない、0.01 < 149 < 0.99")
            check(f"{label}: 85〜155 で板の外が効果なしと同じ (各色 ±2)", res["outside_ok"] if res else None,
                  res and res["outside_max"], TOL)
            if made[key] and all(c["ok"] for c in checks[n0:]) and all(
                    c["ok"] for c in checks if c["label"].startswith(f"{label}: 読み戻し")):
                usable.append(key)
        info["usable"] = usable
        for label, what in (("(1) SetFades", "編集ページで fade-setfades の V2 のクリップのフェードのハンドルを動かせ、書き出しに出る"),
                            ("(2) Fusion の Blend のキー", "Fusion ページで fade-blend の LabelOver の Blend のキーをスプラインの画面で動かせる"),
                            ("(3) AddTransition", "編集ページで fade-transition のトランジションの長さを変えられる")):
            check(f"GUI (人) {label}: {what}", None, None, "人が GUI で確認する")
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        cur = pm.GetCurrentProject()
        saved = bool(pm.SaveProject()) if cur and cur.GetName() == PROJECT else None
        check(f"残すプロジェクト {PROJECT} を保存できた", saved, cur.GetName() if cur else None)
        code = finish_projects(resolve, NAME, path, [], prev, info, checks, min_checks=31)
    sys.exit(code)
