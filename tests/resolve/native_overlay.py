"""透過 PNG を V2 に重ね、位置・大きさ・不透明度を TimelineItem.SetProperties で決められるか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/native_overlay.py
(Resolve Studio を起動しておく。結果は 出力先/tests/native_overlay.json。書き出した動画と PNG は 作業フォルダー/native_overlay に残る)

組むもの: プロジェクト test-native-overlay (1920x1080、30fps) を作り直し (あれば消してから作る)、人が GUI で確認するために消さずに残す。
素材は make_source の patch (testsrc2 の右上の 4 分の 1 が動かない灰色)。どのタイムラインも V1 に素材の頭から 150 フレームで、
V1 のクリップの Fusion に架空の機密を隠す黒塗り (x 1400〜1520、y 120〜200。SecretMaskOver) を置く (マスクのある行)。
重ねる PNG は 800x400 を Pillow で作る: 白い縁 (16 px、不透明)、その内を 3 つに分け、左が赤 (α 255)、中が青 (α 128)、右が緑 (α 0。
α を無視すると緑が出る)。左上に黒い四角 (向きのマーク)。
PNG は Alpha mode を Straight にし、静止画をそのまま置くと既定の 150 フレームになる (21.1.1) ので、補助のタイムライン
overlay-patch-still に置いて、そのタイムラインの項目を startFrame 0・endFrame 90 で置く (resolve_helpers.still_source。
環境設定の Standard still duration は変えない)。V2 は AddTrack で作る。どの置き方も Pan 680、ZoomX・ZoomY 400/1920 で、
合成の方法 Normal・拡大の仕方 Fit・基準点 (0, 0) を別の SetProperties で入れる。
- overlay-none    効果なし (V1 と黒塗りだけ)。比べる元
- overlay-tilt0   Tilt の換算を測る: Tilt 0、Opacity 100
- overlay-tilt100 同じで Tilt 100。75 フレーム目の矩形の中心の y の動きから Tilt 1 あたりの画素を出し、中心を y 160 にする Tilt を
                  決める (Tilt は上が正。21.1.1 の実機では Tilt 380 で中心が 202 になり、幅に合わせた 960/1080 の倍率と合う。
                  その倍率なら 427.5 で、info の calibration に並べて残す)
- overlay-100     換算した Tilt と Opacity 100 で [30,120) に置く (予定の矩形は x 1440〜1840、y 60〜260)
- overlay-50      同じで Opacity 50
書き出しは、どれも同じ可逆の形式で全編 (形式は overlay-none を書き出すときに resolve_helpers.render_lossless で選ぶ)。

合格の条件 (どれも pass。確認できないものは unknown で、合格にしない):
- 素材と PNG を取り込め、5 つのタイムラインの V1 に黒塗りを組めた
- Tilt 0 と 100 の試しの V2 を置けて値が入り、可逆の形式を選べて効果なしの全編 (150 フレーム) を FFmpeg で読め、
  Tilt 100 で中心が上へ動いて換算を出せた
- overlay-100・overlay-50 のそれぞれで: V2 (["video", 2]) の 30 フレーム目から 90 フレームに置けた。SetProperties が真で、
  読み戻しが渡した値 (±0.01)。書き出せた (150 フレーム)。効果なしと違うのは 30〜119 だけ (29・30・119・120 は info の edges)。
  30・75・119 フレームで、差が 40 を超える画素を囲む矩形が予定の矩形と ±1 px。同じ 3 フレームで、各パッチの中央の点
  (赤 (1512, 230)、青 (1640, 160)、緑 (1768, 160)、向きのマーク (1464, 84)) が a × PNG の色 + (1 − a) × 効果なしの色 と各色 ±2
  (赤と向きのマークは a = 不透明度、青は 128/255 × 不透明度、緑は 0)。30〜119 で、矩形の外 (2 px 広げた外) が効果なしと各色 ±2 で同じ
- 残すプロジェクトを保存でき、始めに開いていたプロジェクトを開き直せた
記録 (info): 置き方と読み戻し (variants の build)、Tilt の換算 (calibration)、Alpha mode の読み戻し (alpha_mode)、補助の
タイムラインの項目の属性 (still)、黒塗りの点 (1500, 170) に V2 の赤が出るか (over_mask。V2 は V1 の Fusion の出力の上に重なる
はずで、重ねる素材にも別にマスクが要るかを見る観測。合否には入れない)。

GUI で確認すること (人。JSON では unknown で、合格に数えない):
1. プロジェクト test-native-overlay を開き、タイムライン overlay-100 を開く
2. V2 のクリップを選び、Inspector の変形 (位置・ズーム) と合成 (不透明度) の値を変える
3. デリバーページで書き出し、変えた位置・大きさ・不透明度が出ているか見る
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from resolve_helpers import (append_at, box_mean, changed_box, differing, diff_outside, finish_projects,  # noqa: E402
                             frames_of, fresh_project, make_check, make_source, render_set,
                             require_lock, solid_box, spans, still_source)

NAME = "native_overlay"
PROJECT = "test-native-overlay"
W, H, FPS, L = 1920, 1080, 30, 150    # テストの位置とフレームの番号はこの大きさとフレームレートで決めてある
SHOW, END = 30, 120                   # 1〜4 秒 = [30,120)
RECT = (1440, 60, 1840, 260)          # 予定の矩形 (x0, y0, x1, y1。右と下は含まない)。灰色の所 (x 960〜1920、y 0〜540) の中
PNG_W, PNG_H, BORDER = 800, 400, 16   # PNG の大きさと縁 (PNG の画素)。書き出しでは半分
ZOOM = (RECT[2] - RECT[0]) / W        # 自動で画面の幅に合わせた 1920x960 を 400x200 に
CENTER = ((RECT[0] + RECT[2]) / 2, (RECT[1] + RECT[3]) / 2)                     # (1640, 160)
PAN = CENTER[0] - W / 2               # 680。Pan は右が正で、画素のまま (21.1.1 の実機で x が合った)
CAL_TILT = 100                        # Tilt の換算は、Tilt 0 と 100 で中心の y を測って決める
SECRET = (1400, 120, 1520, 200)       # V1 の黒塗り。赤のパッチの左上と矩形の外にかかる
RED, BLUE, GREEN, WHITE, BLACK = (220, 40, 40), (40, 80, 220), (0, 255, 0), (255, 255, 255), (0, 0, 0)
# パッチ: (書き出しで測る点 (1 画素の四角), PNG の色, PNG の α)。各パッチの中央 (赤は向きのマークと黒塗りを避けた下の所)
PATCHES = {"red": ((1512, 230, 1513, 231), RED, 255), "blue": ((1640, 160, 1641, 161), BLUE, 128),
           "green": ((1768, 160, 1769, 161), GREEN, 0), "marker": ((1464, 84, 1465, 85), BLACK, 255)}
OVER_MASK = (1500, 170, 1501, 171)    # 黒塗りと赤のパッチが重なる点 (向きのマークの混ざらない所)
VARIANTS = {"100": 100.0, "50": 50.0}
LOOK = (SHOW, 75, END - 1)
TOL = 2


def overlay_png(path):
    """重ねる PNG (PNG_W x PNG_H、RGBA)。ImageDraw は RGBA の画素をそのまま置き換える (混ぜない)。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (PNG_W, PNG_H), GREEN + (0,))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, PNG_W - 1, PNG_H - 1], fill=WHITE + (255,))
    inner = (PNG_W - 2 * BORDER) // 3
    x = [BORDER + i * inner for i in range(4)]
    d.rectangle([BORDER, BORDER, PNG_W - BORDER - 1, PNG_H - BORDER - 1], fill=GREEN + (0,))
    d.rectangle([x[0], BORDER, x[1] - 1, PNG_H - BORDER - 1], fill=RED + (255,))
    d.rectangle([x[1], BORDER, x[2] - 1, PNG_H - BORDER - 1], fill=BLUE + (128,))
    d.rectangle([BORDER + 8, BORDER + 8, BORDER + 55, BORDER + 55], fill=BLACK + (255,))
    img.save(path)


def secret_mask(item):
    """V1 のクリップの Fusion に黒塗りを置く。返す値は組めたか。"""
    comp = item.AddFusionComp()
    if not comp:
        return False
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    x0, y0, x1, y1 = SECRET
    m = solid_box(comp, mi.Output, BLACK, ((x0 + x1) / 2 / W, 1 - (y0 + y1) / 2 / H), (x1 - x0) / W, (y1 - y0) / H)
    m.SetAttrs({"TOOLS_Name": "SecretMaskOver"})
    mo.Input = m.Output
    return True


def overlay_props(tilt, opacity):
    """V2 のクリップに入れる値 (読み戻して比べるもの)。"""
    return {"Pan": PAN, "Tilt": tilt, "ZoomX": ZOOM, "ZoomY": ZOOM, "Opacity": opacity}


def place_overlay(tl, tilt, opacity):
    """補助のタイムラインの PNG を V2 の [SHOW, END) に置き、overlay_props を入れる。合成の方法 (Normal)・拡大の仕方 (Fit)・
    基準点 (0, 0) は別の SetProperties で入れる (どれかの名前か値が違うと全部が入らないため)。
    返す値は (置けたか, 読み戻しが合うか, 記録)。"""
    item, place = append_at(mp, tl, still, SHOW, END - SHOW, track=2)
    want = overlay_props(tilt, opacity)
    rec = {"place": place, "tracks": tl.GetTrackCount("video"), "want": want}
    placed = place == {"start": SHOW, "duration": END - SHOW, "track": ["video", 2]}
    if not item:
        return placed, None, rec
    rec["set"] = bool(item.SetProperties(want))
    fixed = {"AnchorPointX": 0.0, "AnchorPointY": 0.0}
    for k, name in (("CompositeMode", "COMPOSITE_NORMAL"), ("Scaling", "SCALE_FIT")):
        if hasattr(resolve, name):
            fixed[k] = getattr(resolve, name)
    rec["set_fixed"] = {"values": {k: str(v) for k, v in fixed.items()}, "set": bool(item.SetProperties(fixed))}
    props = item.GetProperties() or {}
    got = {k: props.get(k) for k in want}
    rec["readback"] = dict(got, **{k: props.get(k) for k in fixed})
    same = rec["set"] and all(isinstance(got.get(k), (int, float)) and abs(got[k] - v) <= 0.01 for k, v in want.items())
    return placed, bool(same), rec


def center_y(key):
    """書き出し key の 75 フレーム目で、効果なしとの差が 40 を超える所の矩形の中心の y と矩形 (読めなければ None)。"""
    fr = dict(frames_of({"r": renders[key]["path"], "b": renders["none"]["path"]}, [75]))[75] if key in md5 else None
    rc = changed_box(fr["r"], fr["b"], 40) if fr and fr["r"] is not None and fr["b"] is not None else None
    return ((rc[1] + rc[3]) / 2 if rc else None), rc


def mix(b, roi, rgb, a):
    """不透明度 a で rgb を効果なしの画 b に重ねた色 (測る所の平均)。"""
    return [round(a * c + (1 - a) * v, 1) for c, v in zip(rgb, box_mean(b, roi))]


def analyse(key, opacity):
    """overlay-<key> の書き出しを効果なしと比べる。返す値は記録 (書き出しが無ければ None)。"""
    m5 = md5.get(key)
    if not m5:
        return None
    d = differing(m5, md5["none"])
    res = {"frames": len(m5), "differ_from_none": spans(d), "shown_ok": d == list(range(SHOW, END)),
           "edges": {f: f in d for f in (SHOW - 1, SHOW, END - 1, END)}, "rect": {}, "patches": {}}
    outside = {}
    for n, fr in frames_of({"r": renders[key]["path"], "b": renders["none"]["path"]}, range(SHOW, END)):
        r, b = fr["r"], fr["b"]
        if r is None or b is None:
            continue
        outside[n] = diff_outside(r, b, [RECT], pad=2)
        if n not in LOOK:
            continue
        res["rect"][n] = changed_box(r, b, 40)
        res["patches"][n] = {p: {"mean": box_mean(r, roi), "expected": mix(b, roi, rgb, alpha / 255 * opacity / 100)}
                             for p, (roi, rgb, alpha) in PATCHES.items()}
        if n == LOOK[1]:
            a = opacity / 100
            res["over_mask"] = {"mean": box_mean(r, OVER_MASK), "if_v2_on_top": mix(b, OVER_MASK, RED, a),
                                "under": box_mean(b, OVER_MASK)}
            om = res["over_mask"]
            res["over_mask"]["v2_on_top"] = all(abs(x - y) <= TOL for x, y in zip(om["mean"], om["if_v2_on_top"]))   # 点なので ±2
    rects = [res["rect"].get(n) for n in LOOK]
    res["rect_ok"] = all(rc and all(abs(a - b) <= 1 for a, b in zip(rc, RECT)) for rc in rects)
    res["patch_ok"] = {p: len(res["patches"]) == len(LOOK) and all(
        all(abs(x - y) <= TOL for x, y in zip(v[p]["mean"], v[p]["expected"])) for v in res["patches"].values())
        for p in PATCHES}
    res["outside_max"] = max(outside.values()) if outside else None
    res["outside_ok"] = len(outside) == END - SHOW and res["outside_max"] <= TOL
    return res


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    checks, info = [], {"project": PROJECT, "variants": {}}
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
    try:
        src = make_source(cfg, W, H, FPS, patch=True)
        png = os.path.join(DIR, "overlay-patch.png")   # 名前に数字を入れない (連番の画像と読まれないように)
        overlay_png(png)
        proj, info["fresh"] = fresh_project(pm, PROJECT, W, H, FPS)
        if not proj:
            raise RuntimeError(f"プロジェクト {PROJECT} を作れない ({info['fresh']})")
        info["input_mismatch"] = (proj.GetSettings() or {}).get("timelineInputResMismatchBehavior")
        mp = proj.GetMediaPool()
        src_clip = (mp.ImportMedia([src]) or [None])[0]
        png_clip = (mp.ImportMedia([png]) or [None])[0]
        check("素材と PNG を取り込めた", bool(src_clip and png_clip), [bool(src_clip), bool(png_clip)])
        if not (src_clip and png_clip):
            raise RuntimeError("素材か PNG を取り込めない")
        info["png_clip"] = {k: png_clip.GetClipProperty(k) for k in ("Resolution", "Frames", "Duration", "Type")}
        info["alpha_mode"] = {"set": bool(png_clip.SetClipProperty("Alpha mode", "Straight")),
                              "readback": png_clip.GetClipProperty("Alpha mode")}
        still, info["still"] = still_source(mp, proj, png_clip, "overlay-patch-still")
        if not still:
            raise RuntimeError(f"PNG を置いた補助のタイムラインを作れない ({info['still']})")

        tls, masked = {}, []
        for key in ["none", "tilt0", "tilt100"] + list(VARIANTS):
            tl = mp.CreateEmptyTimeline(f"overlay-{key}")
            proj.SetCurrentTimeline(tl)
            v1, _ = append_at(mp, tl, src_clip, 0, L)
            masked.append(bool(v1) and secret_mask(v1))
            tls[key] = tl
        check("5 つのタイムラインの V1 に黒塗りを組めた", all(masked), masked)

        # Tilt の換算: Tilt 0 と 100 で置いて書き出し、中心の y の動きから Tilt 1 あたりの画素を出す
        cal = {}
        for key, t in (("tilt0", 0.0), ("tilt100", float(CAL_TILT))):
            proj.SetCurrentTimeline(tls[key])
            cal[key] = place_overlay(tls[key], t, 100.0)
        info["calibration"] = {k: v[2] for k, v in cal.items()}
        check("Tilt 0 と 100 の試しの V2 を置け、値が入った", all(v[0] and v[1] for v in cal.values()),
              {k: [v[0], v[1]] for k, v in cal.items()})
        rs = render_set(proj, tls, "none", ["tilt0", "tilt100"], DIR, NAME)
        info.update(rs["info"])
        renders, md5 = rs["renders"], rs["md5"]
        check("可逆の形式を選べ、効果なしの全編を FFmpeg で読める (150 フレーム)", bool(rs["chosen"]) and len(md5["none"]) == L,
              {"chosen": rs["chosen"], "frames": len(md5["none"])}, L)
        (y0, box0), (y1, box1) = center_y("tilt0"), center_y("tilt100")
        per = (y0 - y1) / CAL_TILT if y0 is not None and y1 is not None else None   # Tilt 1 あたり上へ動く画素
        tilt = (y0 - CENTER[1]) / per if per else None
        info["calibration"]["measured"] = {"center_y": [y0, y1], "boxes": [box0, box1], "px_per_tilt": per, "tilt": tilt,
                                           "tilt_if_960_rule": (H / 2 - CENTER[1]) * H / (W / 2)}
        check("Tilt 0 と 100 で中心が上へ動き、Tilt の換算を出せた", bool(per and per > 0), info["calibration"]["measured"])

        for key, op in VARIANTS.items():
            tl = tls[key]
            proj.SetCurrentTimeline(tl)
            placed, same, rec = place_overlay(tl, tilt if tilt is not None else 0.0, op)
            info["variants"][key] = {"build": rec}
            check(f"Opacity {key}: V2 の 30 フレーム目から 90 フレームに置けた", placed, rec["place"])
            check(f"Opacity {key}: SetProperties が真で、読み戻しが渡した値 (±0.01。Tilt は換算した値)",
                  same if tilt is not None else None, {"set": rec.get("set"), "readback": rec.get("readback")}, rec["want"])
        if rs["chosen"] and tilt is not None:
            rs2 = render_set(proj, tls, "none", list(VARIANTS), DIR, NAME, chosen=rs["chosen"])
            renders.update(rs2["renders"])
            md5.update(rs2["md5"])
            info["renders"].update(rs2["info"]["renders"])

        for key, op in VARIANTS.items():
            res = analyse(key, op)
            info["variants"][key]["result"] = res
            check(f"Opacity {key}: 書き出せた (150 フレーム)", (res["frames"] == L) if res else None, res and res["frames"], L)
            check(f"Opacity {key}: 効果なしと違うのは 30〜119 だけ", res["shown_ok"] if res else None,
                  res and {"differ": res["differ_from_none"], "edges": res["edges"]}, [[SHOW, END - 1]])
            check(f"Opacity {key}: 換算した Tilt で、30・75・119 フレームの矩形が予定と ±1 px", res["rect_ok"] if res else None,
                  res and res["rect"], RECT)
            for p, what in (("red", "赤 (α 255)"), ("blue", "青 (α 128)"), ("green", "緑 (α 0、下の画のまま)"),
                            ("marker", "向きのマーク (左上の黒)")):
                check(f"Opacity {key}: {what} のパッチの中央の点が合成の期待と各色 ±2", res["patch_ok"][p] if res else None,
                      res and {n: v[p] for n, v in res["patches"].items()})
            check(f"Opacity {key}: 30〜119 で矩形の外が効果なしと同じ (各色 ±2)", res["outside_ok"] if res else None,
                  res and res["outside_max"], TOL)
        info["over_mask"] = {k: (v.get("result") or {}).get("over_mask") for k, v in info["variants"].items()}
        check("GUI (人): 編集ページの Inspector で overlay-100 の V2 のクリップの位置・大きさ・不透明度を直せ、書き出しに出る",
              None, None, "人が GUI で確認する")
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        cur = pm.GetCurrentProject()
        saved = bool(pm.SaveProject()) if cur and cur.GetName() == PROJECT else None
        check(f"残すプロジェクト {PROJECT} を保存できた", saved, cur.GetName() if cur else None)
        code = finish_projects(resolve, NAME, path, [], prev, info, checks, min_checks=28)
    sys.exit(code)
