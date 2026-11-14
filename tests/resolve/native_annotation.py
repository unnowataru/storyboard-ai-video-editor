"""Fusion の標準の部品で矢印とラベル「確認」を重ね、指す先の位置・色・表示の時刻が書き出しで合うか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/native_annotation.py
(Resolve Studio を起動しておく。結果は 出力先/tests/native_annotation.json。書き出した動画と .comp は 作業フォルダー/native_annotation に残る)

組むもの: プロジェクト test-native-annotation (1920x1080、30fps) を作り直し (あれば消してから作る)、人が GUI で確認するために消さずに残す。
素材は make_source の patch (testsrc2 の右上の 4 分の 1 が動かない灰色)。どのタイムラインも V1 に素材の頭から 150 フレーム。
注釈は V1 のクリップの Fusion に組む: 矢印は PolylineMask (AnnotArrowMask。左向きの 7 点、先端 (1200, 250)) で切った黄色の
Background (AnnotArrow)、ラベルは Text+ (AnnotText。「確認」、中心 (1560, 250)、水色、書体は config の caption)。2 つを Merge
(AnnotLabel) でまとめ、Merge (AnnotOver) で素材に重ねる。AnnotOver の Blend のキー 0: 0、29: 0、30: 1、119: 1、120: 0 で [30,120) だけ
出す (キーは common._animate で入れ、0 フレームの値を書く。書かないと 0 フレームに 1 のキーが残った。21.1.1)。
- annot-none 効果なし (V1 だけ)。比べる元
- annot-comp 部品と矢印の点を書いた .comp を ImportFusionComp で読み込み、色・文・大きさ・
             Blend のキーは読み込んだ後に入れる
矢印の点の座標は、中心 (0.5, 0.5) からのずれで X = x/W − 0.5、Y = 0.5 − y/H (y は画面の高さで割る。先端は (0.125, 0.268519))。
部品を AddTool で置いて点を SetInput("Polyline", 辞書か list) で入れるやり方は、21.1.1 で戻り値も GetInput も None で、
矢印が描かれなかったので試さない。
書き出しは、どれも同じ可逆の形式で全編 (形式は annot-none を書き出すときに resolve_helpers.render_lossless で選ぶ)。

合格の条件 (どれも pass。確認できないものは unknown で、合格にしない):
- 素材を取り込め、可逆の形式を選べて効果なしの全編 (150 フレーム) を FFmpeg で読める
- annot-comp で: 名前を付けた部品が全部そろった。AnnotOver の Blend のキーの読み戻しが渡した値。書き出せた (150 フレーム)。
  効果なしと違うのは 30〜119 だけ。30・75・119 フレームで: 先端 (矢印の色の差が 80 を超える所の左端の列と、その 4 列の縦の中央) が
  (1200, 250) から ±2 px。胴の内側 (x 1300〜1420、y 244〜256) の平均が黄色と各色 ±2。ラベルの所 (x 1470〜1650、y 200〜300) に
  水色 (各色 ±6) の画素が 150 以上。30〜119 で、矢印とラベルの所 (3 px 広げた所) の外が効果なしと各色 ±2 で同じ
- 残すプロジェクトを保存でき、始めに開いていたプロジェクトを開き直せた
記録 (info): 組み方と読み戻し (methods の comp の build。Polyline の GetInput の値、Blend のキー)、測った先端・描かれた所の矩形
(drawn_box)・ラベルの画素の数 (result)。

GUI で確認すること (人。JSON では unknown で、合格に数えない):
1. プロジェクト test-native-annotation を開き、タイムライン annot-comp の V1 のクリップを Fusion ページで開く
2. AnnotText を選び、Inspector で文・位置・色を変える
3. AnnotArrowMask を選び、ビューアーで矢印の点を動かす
4. AnnotOver の Blend のキーをスプラインの画面で動かす
5. デリバーページで書き出し、変えたものが出ているか、「確認」が読めるかを見る
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from resolve_helpers import (animated_keys, append_at, box_mean, changed_box, differing, diff_outside, finish_projects,  # noqa: E402
                             frames_of, fresh_project, make_check, make_source, render_set,
                             require_lock, spans)

NAME = "native_annotation"
PROJECT = "test-native-annotation"
W, H, FPS, L = 1920, 1080, 30, 150    # テストの位置とフレームの番号はこの大きさとフレームレートで決めてある
SHOW, END = 30, 120
TIP = (1200, 250)
# 左向きの矢印の 7 点 (画素)。先端 → 頭の上 → 首の上 → 尾の上 → 尾の下 → 首の下 → 頭の下
ARROW = [TIP, (1260, 215), (1260, 240), (1440, 240), (1440, 260), (1260, 260), (1260, 285)]
ARROW_BOX = (1200, 215, 1441, 286)
SHAFT = (1300, 244, 1420, 256)        # 胴の内側 (測る所)
LABEL_TEXT, LABEL_CENTER, LABEL_SIZE = "確認", (1560, 250), 0.0625   # Size 1 は画面の幅の半分の字 (60 px の字)
LABEL_ROI = (1470, 200, 1650, 300)
YELLOW, CYAN = (255, 200, 0), (80, 220, 255)
BLEND_KEYS = {0: 0.0, SHOW - 1: 0.0, SHOW: 1.0, END - 1: 1.0, END: 0.0}
TOOLS = ("AnnotArrowMask", "AnnotArrow", "AnnotText", "AnnotLabel", "AnnotOver")
LOOK = (SHOW, 75, END - 1)
TOL = 2


def poly_points():
    """ARROW を Polyline の点にする。X = x/W - 0.5、Y = 0.5 - y/H (中心からのずれ。y は画面の高さで割る)。
    直線の点の LX・LY・RX・RY は前後の点へ 3 分の 1 の向き (Fusion が直線の点に付ける値)。"""
    pts = [(x / W - 0.5, 0.5 - y / H) for x, y in ARROW]
    out = []
    for i, (x, y) in enumerate(pts):
        (px, py), (nx, ny) = pts[i - 1], pts[(i + 1) % len(pts)]
        out.append({"Linear": True, "X": x, "Y": y, "LX": (px - x) / 3, "LY": (py - y) / 3, "RX": (nx - x) / 3,
                    "RY": (ny - y) / 3})
    return out


def comp_text():
    """annot-comp で読み込む .comp の中身 (部品とつなぎ方と矢印の点。値は読み込んだ後に fill で入れる)。"""
    pts = "".join("\t\t\t\t\t\t\t{ " + ", ".join(f"{k} = {str(v).lower() if isinstance(v, bool) else f'{v:.6f}'}"
                                               for k, v in p.items()) + " },\n" for p in poly_points())
    return f"""Composition {{
	Tools = {{
		MediaIn1 = Loader {{
			ViewInfo = OperatorInfo {{ Pos = {{ 0, 0 }} }},
			Inputs = {{
			}},
		}},
		AnnotArrowMask = PolylineMask {{
			DrawMode = "ClickAppend",
			ViewInfo = OperatorInfo {{ Pos = {{ 110, -132 }} }},
			Inputs = {{
				UseFrameFormatSettings = Input {{ Value = 1, }},
				Polyline = Input {{
					Value = Polyline {{
						Closed = true,
						Points = {{
{pts}						}},
					}},
				}},
			}},
		}},
		AnnotArrow = Background {{
			ViewInfo = OperatorInfo {{ Pos = {{ 110, -66 }} }},
			Inputs = {{
				UseFrameFormatSettings = Input {{ Value = 1, }},
				EffectMask = Input {{ SourceOp = "AnnotArrowMask", Source = "Mask", }},
			}},
		}},
		AnnotText = TextPlus {{
			ViewInfo = OperatorInfo {{ Pos = {{ 220, -132 }} }},
			Inputs = {{
				UseFrameFormatSettings = Input {{ Value = 1, }},
				StyledText = Input {{ Value = "Label", }},
			}},
		}},
		AnnotLabel = Merge {{
			ViewInfo = OperatorInfo {{ Pos = {{ 220, -66 }} }},
			Inputs = {{
				Background = Input {{ SourceOp = "AnnotArrow", Source = "Output", }},
				Foreground = Input {{ SourceOp = "AnnotText", Source = "Output", }},
				PerformDepthMerge = Input {{ Value = 0, }},
			}},
		}},
		AnnotOver = Merge {{
			ViewInfo = OperatorInfo {{ Pos = {{ 220, 0 }} }},
			Inputs = {{
				Background = Input {{ SourceOp = "MediaIn1", Source = "Output", }},
				Foreground = Input {{ SourceOp = "AnnotLabel", Source = "Output", }},
				PerformDepthMerge = Input {{ Value = 0, }},
			}},
		}},
		MediaOut1 = Saver {{
			ViewInfo = OperatorInfo {{ Pos = {{ 330, 0 }} }},
			Inputs = {{
				Index = Input {{ Value = "0", }},
				Input = Input {{ SourceOp = "AnnotOver", Source = "Output", }},
			}},
		}},
	}},
}}
"""


def build_comp(item, path):
    """部品と点を書いた .comp を読み込む。返す値は (合成, 記録)。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(comp_text())
    comp = item.ImportFusionComp(path)
    return comp, {"comp": bool(comp), "path": path}


def fill(comp, look):
    """色・文・書体・大きさ・位置と、AnnotOver の Blend のキーを入れる (Lock は使わない)。返す値は記録。"""
    T = {n: comp.FindTool(n) for n in TOOLS}
    rec = {"found": {n: bool(v) for n, v in T.items()}}
    if not all(T.values()):
        return rec
    for k, v in zip(("TopLeftRed", "TopLeftGreen", "TopLeftBlue", "TopLeftAlpha"), C.rgba01(YELLOW)):
        T["AnnotArrow"].SetInput(k, v)
    cap = look["caption"]
    tx = T["AnnotText"]
    tx.SetInput("StyledText", LABEL_TEXT)
    tx.SetInput("Font", cap["font_name"])
    tx.SetInput("Style", cap["font_style"])
    tx.SetInput("Size", LABEL_SIZE)
    tx.SetInput("Center", {1: LABEL_CENTER[0] / W, 2: 1 - LABEL_CENTER[1] / H})
    for k, v in zip(("Red1", "Green1", "Blue1"), C.rgba01(CYAN)):
        tx.SetInput(k, v)
    C._animate(comp, T["AnnotOver"], "Blend", sorted(BLEND_KEYS.items()))
    rec["keys"] = animated_keys(T["AnnotOver"], "Blend")
    rec["polyline"] = str(T["AnnotArrowMask"].GetInput("Polyline"))[:600]
    rec["text"] = T["AnnotText"].GetInput("StyledText")
    return rec


def tip_of(r, b):
    """書き出しの先端: 矢印の色の差が 80 を超える画素の、先端の近く (±40 px) での左端の列と、その 4 列の縦の中央。"""
    import numpy as np
    x0, y0 = TIP
    d = np.abs(r.astype(np.int16) - b.astype(np.int16)).max(axis=2)[y0 - 40:y0 + 41, x0 - 40:x0 + 80] > 80
    ys, xs = np.nonzero(d)
    if not len(xs):
        return None
    col = ys[xs <= xs.min() + 3]
    return [int(x0 - 40 + xs.min()), round(float(y0 - 40 + col.mean()), 1)]


def analyse(key):
    """annot-<key> の書き出しを効果なしと比べる。返す値は記録 (書き出しが無ければ None)。"""
    import numpy as np
    m5 = md5.get(key)
    if not m5:
        return None
    d = differing(m5, md5["none"])
    res = {"frames": len(m5), "differ_from_none": spans(d), "shown_ok": d == list(range(SHOW, END)),
           "edges": {f: f in d for f in (SHOW - 1, SHOW, END - 1, END)}, "at": {}}
    outside = {}
    for n, fr in frames_of({"r": renders[key]["path"], "b": renders["none"]["path"]}, range(SHOW, END)):
        r, b = fr["r"], fr["b"]
        if r is None or b is None:
            continue
        outside[n] = diff_outside(r, b, [ARROW_BOX, LABEL_ROI], pad=3)
        if n not in LOOK:
            continue
        x0, y0, x1, y1 = LABEL_ROI
        near = (np.abs(r[y0:y1, x0:x1].astype(np.int16) - np.array(CYAN)).max(axis=2) <= 6).sum()
        res["at"][n] = {"tip": tip_of(r, b), "shaft": box_mean(r, SHAFT), "label_pixels": int(near),
                        "drawn_box": changed_box(r, b, 40)}
    at = [res["at"].get(n) for n in LOOK]
    res["tip_ok"] = all(a and a["tip"] and abs(a["tip"][0] - TIP[0]) <= 2 and abs(a["tip"][1] - TIP[1]) <= 2 for a in at)
    res["shaft_ok"] = all(a and all(abs(x - y) <= TOL for x, y in zip(a["shaft"], YELLOW)) for a in at)
    res["label_ok"] = all(a and a["label_pixels"] >= 150 for a in at)
    res["outside_max"] = max(outside.values()) if outside else None
    res["outside_ok"] = len(outside) == END - SHOW and res["outside_max"] <= TOL
    return res


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    look, _ = cfg.storyboard_values()
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
    try:
        src = make_source(cfg, W, H, FPS, patch=True)
        proj, info["fresh"] = fresh_project(pm, PROJECT, W, H, FPS)
        if not proj:
            raise RuntimeError(f"プロジェクト {PROJECT} を作れない ({info['fresh']})")
        mp = proj.GetMediaPool()
        src_clip = (mp.ImportMedia([src]) or [None])[0]
        check("素材を取り込めた", bool(src_clip))
        if not src_clip:
            raise RuntimeError("素材を取り込めない")
        tls = {}
        for key in ("none", "comp"):
            tl = mp.CreateEmptyTimeline(f"annot-{key}")
            proj.SetCurrentTimeline(tl)
            v1, _ = append_at(mp, tl, src_clip, 0, L)
            tls[key] = tl
        rec = {}
        try:
            comp, rec = build_comp(v1, os.path.join(DIR, "annotation.comp"))
            if comp:
                rec["fill"] = fill(comp, look)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        fl = rec.get("fill") or {}
        made = bool(fl.get("found")) and all(fl["found"].values())
        info["methods"]["comp"] = {"build": rec}

        rs = render_set(proj, tls, "none", ["comp"] if made else [], DIR, NAME)
        info.update(rs["info"])
        renders, md5 = rs["renders"], rs["md5"]
        check("可逆の形式を選べ、効果なしの全編を FFmpeg で読める (150 フレーム)", bool(rs["chosen"]) and len(md5["none"]) == L,
              {"chosen": rs["chosen"], "frames": len(md5["none"])}, L)

        res = analyse("comp")
        info["methods"]["comp"]["result"] = res
        label = "annot-comp (ImportFusionComp)"
        check(f"{label}: 名前を付けた部品が全部そろった", made, fl.get("found"))
        check(f"{label}: AnnotOver の Blend のキーの読み戻しが渡した値", (fl.get("keys") == BLEND_KEYS) if made else None,
              fl.get("keys"), BLEND_KEYS)
        check(f"{label}: 書き出せた (150 フレーム)", (res["frames"] == L) if res else None, res and res["frames"], L)
        check(f"{label}: 効果なしと違うのは 30〜119 だけ", res["shown_ok"] if res else None,
              res and {"differ": res["differ_from_none"], "edges": res["edges"]}, [[SHOW, END - 1]])
        check(f"{label}: 30・75・119 フレームの先端が (1200, 250) から ±2 px", res["tip_ok"] if res else None,
              res and {n: a["tip"] for n, a in res["at"].items()}, list(TIP))
        check(f"{label}: 胴の内側が黄色 (各色 ±2)", res["shaft_ok"] if res else None,
              res and {n: a["shaft"] for n, a in res["at"].items()}, YELLOW)
        check(f"{label}: ラベルの所に水色の画素が 150 以上", res["label_ok"] if res else None,
              res and {n: a["label_pixels"] for n, a in res["at"].items()}, 150)
        check(f"{label}: 30〜119 で矢印とラベルの外が効果なしと同じ (各色 ±2)", res["outside_ok"] if res else None,
              res and res["outside_max"], TOL)
        check("GUI (人): Fusion ページで AnnotText の文・位置・色、AnnotArrowMask の点、AnnotOver の Blend のキーを直せ、書き出しに出る",
              None, None, "人が GUI で確認する")
        check("GUI (人): 書き出しで「確認」が読める", None, None, "人が GUI で確認する")
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        cur = pm.GetCurrentProject()
        saved = bool(pm.SaveProject()) if cur and cur.GetName() == PROJECT else None
        check(f"残すプロジェクト {PROJECT} を保存できた", saved, cur.GetName() if cur else None)
        code = finish_projects(resolve, NAME, path, [], prev, info, checks, min_checks=14)
    sys.exit(code)
