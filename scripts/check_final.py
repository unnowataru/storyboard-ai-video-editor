"""構成表と、Resolve のタイムライン (API の数値) と、書き出した動画 (ffprobe) を照らし合わせ、項目ごとに pass / fail / unknown を付ける。

使い方 (Resolve を使うので resolve_lock.py run で包む。--no-resolve は接続しないので包まなくてよい):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/check_final.py 構成表.json [--video 書き出した動画] [--timeline 名前] [--no-resolve] [--pixel]
- タイムラインは、今開いているプロジェクトの今のタイムライン (--timeline で同じプロジェクトの別のタイムライン)。読むだけで書き換えない
- 動画の既定は構成表の output。無ければ動画の項目は unknown
- --pixel: 肩テロップが消える前後のフレームの色と、focus の枠線が現れる前の輪の画素 (素材と比べる。テロップの背景に重なる点は
  背景の色を重ねた色と比べ、上に重なる文字・板・callout・マスクの点は外す) も見る。実際の録画の書き出しでも、合成した素材のテストでも使う。
  実際の録画では、板の場所に肩テロップに近い色が映る・枠線の点の上で画面が変わる (カーソル、時刻のずれ) と誤ることがある (README.md)
結果: 出力先 (config の out_dir) の check-final\\<名前>.json。fail と unknown の行があれば、その行の区切りのフレーム
(行の始まり・終わり、肩テロップが消える前後、ズームの始まり・終わりの前後 1 フレーム) を並べた <名前>.png も作る (人が見る画像)。
Resolve に接続していない・動画が無いなど、画像を見ても判定できない unknown だけなら画像は作らない。
画面には failed=N unknown=N report=<パス> の 1 行。fail か unknown が 1 つでもあれば終了コード 1 (unknown は成功ではない)。
--pixel で枠線の画素を比べなかった行 (枠線はあるが、頭から寄っている・比べる点が残らないなど) は、JSON の border_pixels_skipped と
1 行の border_unchecked=<行>:<理由>,... に出す。fail にも unknown にもしない (目で見る候補)。

見るもの: 行の位置と長さ、素材の範囲、速さ、素材のファイル、肩テロップが出る長さ、肩テロップの文の速さ (「9 倍速」「等倍」) と行の速さ、テロップ (文・大きさ・位置・書体・色・背景・つながり・出ている区間)、ズームの倍率と中心とキーフレームの時刻、
マスクの位置と大きさ (クリップの Fusion の部品の値)、layout (画面の切り抜きと置き場所、背景のぼかしと暗さ、つながり)、focus と枠線 (色が auto なら組む側と同じ関数で素材の明るさから
決め直した色)、callout (order の順、dst を省略した行は組む側と同じ自動の置き場所)、止めたクリップ、
動画の解像度・フレームレート・フレーム数。
肩テロップ・ズーム・マスクは api_trial.py と同じ作り (クリップの Fusion) だけを読む。
実際の録画で IP やホスト名の隠し漏れが無いことまでは保証しない (README.md の「書き出しを構成表と照らし合わせる」)。
"""
import argparse
import itertools
import json
import math
import os
import sys

import common as C

TOL_SIZE = 1e-3     # ズームの倍率と中心 (Fusion の値) の許容の差
TOL_PX = 0.5        # マスクの位置と大きさ (画素) の許容の差
TOL_SPEED = 0.5     # 速さ (%) の許容の差

# 照らし合わせの項目の名前 (結果の JSON の item)。<N> は番号、<項目> は動画の値の名前、<フレーム> は書き出しのフレーム。
# hold_ で始まるものは止めたクリップの項目。項目を追加したらここと機能の一覧 (docs/features.json の check_final) に追加する
# (tests/offline/test_features_catalog.py が、_c に書いた名前と一覧がここと合うかを検査する)
ITEMS = (
    "timeline", "timeline_settings", "items", "position", "source_range", "speed", "source_file", "video_<項目>",
    "badge", "badge_box", "badge_pixels", "badge_speed", "badge_pixels_<フレーム>", "closing", "caption", "caption_bar", "caption_time", "zoom", "zoom_size", "zoom_center",
    "mask<N>", "mask<N>_type", "focus", "focus_dim", "focus_time", "focus_border", "focus_border_pixels_0",
    "callout", "callout_time", "callout_<N>", "callout_time_<N>",
    "hold", "hold_caption", "hold_caption_bar", "hold_caption_time", "hold_mask<N>", "hold_mask<N>_type", "hold_focus", "hold_focus_dim", "hold_focus_time", "hold_focus_border",
    "hold_callout", "hold_callout_time", "hold_callout_<N>", "hold_callout_time_<N>",
    "point", "side", "side_size", "hold_point", "hold_side", "hold_side_size",
    "layout", "layout_back", "layout_dim", "layout_links", "hold_layout", "hold_layout_back", "hold_layout_dim", "hold_layout_links",
)


# ---------- 構成表から期待する値 ----------
def _key(path):
    return os.path.normcase(os.path.realpath(path)) if path else path


def source_sizes(sb, tl=None):
    """素材ごとの解像度 {素材のパス: (幅, 高さ)}。タイムラインのクリップの属性 (Resolution) を先に、無ければ ffprobe で読む。
    読めない素材は入れない (その素材の行のマスクとズームは unknown になる)。"""
    out = {}
    for it in ((tl.GetItemListInTrack("video", 1) or []) if tl else []):
        mpi = it.GetMediaPoolItem()
        fp = mpi.GetClipProperty("File Path") if mpi else None
        wh = C.clip_resolution(mpi) if mpi else (None, None)
        if fp and wh[0]:
            out[_key(fp)] = wh
    for r in sb["rows"]:
        src = r.get("source") or sb["source"]
        if _key(src) not in out and src and os.path.isfile(src):
            try:
                v = C.probe_video(src)
                out[_key(src)] = (v["width"], v["height"])
            except (RuntimeError, KeyError, IndexError, ValueError, SystemExit):
                pass
    return out


# point.from (マークを指す点のどちら側に置くか) から、指す向き。組む側 (schema.POINT_FROM) とは別に持つ
POINT_WAY = {"below": "up", "above": "down", "left": "right", "right": "left"}


def _point_problems(pt):
    """point (config の point に行の point を重ねたもの) の書式の誤り。組む側 (common.point_values) が ValueError にするものと、
    point.glyphs に向きのマークが無いもの。空なら読める。"""
    bad = C.point_problems(pt)
    if not bad:
        g = (pt.get("glyphs") or {}).get(f"{pt['style']}-{POINT_WAY[pt['from']]}")
        if not (isinstance(g, dict) and isinstance(g.get("glyph"), str) and isinstance(g.get("tip"), (list, tuple))
                and len(g["tip"]) == 2 and all(C.is_num(v) for v in g["tip"])):
            bad.append(f"glyphs に {pt['style']}-{POINT_WAY[pt['from']]} のマーク (glyph と tip) が無い")
    return bad


def side_expect(sb, r):
    """行 r の解説の欄の期待の書式 (common.side_sections が読む形)。欄が無ければ None。読めなければ {"unreadable": 理由}。
    組む側 (common.merge_side) を呼ばずに、構成表のルールからここで合わせる。

    - 行に side が無ければ、全体の layout.side
    - 行の side が文か list なら、その行の欄を全部差し替える
    - 行の side が辞書 ({見出し: 中身}) なら、全体の layout.side の欄のうち見出し (title) が同じ欄の text だけを差し替える。
      ほかの欄・欄の並び・title はそのまま。align は全体の欄に書いてあれば残し、無ければ書かない (中身の行数で決まる)
    - 辞書の誤り (空・見出しが全体の欄に無い・中身が文でない・全体の layout.side が list でない) は読めない書式"""
    lay = sb.get("layout")
    base = lay.get("side") if isinstance(lay, dict) else None
    if "side" not in r:
        return base or None
    side = r["side"]
    if not isinstance(side, dict):
        return side or None
    bad = []
    if not side:
        bad.append("辞書が空 (差し替える欄の見出しと中身を書く)")
    if not isinstance(base, list):
        bad.append("見出しごとに書くには、layout.side に {title, text} の list が要る")
    else:
        titles = [s.get("title") for s in base if isinstance(s, dict)]
        unknown = [str(k) for k in side if k not in titles]
        if unknown:
            bad.append(f"見出し {'・'.join(unknown)} は layout.side に無い")
    loose = [str(k) for k, v in side.items() if not isinstance(v, str)]
    if loose:
        bad.append(f"{'・'.join(loose)} の中身は文 (| で改行)")
    if bad:
        return {"unreadable": "、".join(bad)}
    return [dict(s, text=side[s["title"]]) if isinstance(s, dict) and s.get("title") in side else s for s in base]


# layout.anchor と、余った幅のどこに画面を置くか (0 が左、1 が右)。組む側 (schema.LAYOUT_ANCHORS) とは別に持つ
LAYOUT_SIDE = {"top-left": 0.0, "top": 0.5, "top-right": 1.0}


def layout_expect(sb, sw, sh):
    """構成表の layout から、画面を置く場所と背景の期待の値を計算する (組む側の common.layout_values を呼ばない)。
    layout が無ければ None。読めなければ {"unreadable": 理由}。sw, sh は素材の幅と高さ。

    - rect: 画面 (layout.screen、素材の画素) が出力に映る四角 [x0, y0, w, h] (出力の画素)。高さは height × 出力の高さ、
      幅は screen の縦横の比のまま。y0 は margin、x0 は余った幅を anchor で分けた所。scale は素材の 1 画素が出力の何画素か
    - comp: Fusion の画面の大きさ。素材の縦横の比がタイムラインと同じなら素材の大きさ、違えば素材の画素のまま
      タイムラインと同じ比に広げた大きさ (素材は真ん中)
    - crop: 画面の四角を comp の画素で表した Crop の値 [XOffset, YOffset, XSize, YSize] (YOffset は下から)
    - back_size: 切り抜いた画面で comp の全体を覆う倍率 (縦と横の大きいほう)
    - view: 画面の真ん中 (タイムラインの画素。指すマークを傾ける向き)。end: comp の画を出力に置く倍率 (マークの大きさを割る)
    - screen_size: 画面を出力に置く Merge の Size (k × cw ÷ W。comp の 1 画素は出力の W ÷ cw 画素)。指すマークを画面と一緒に
      置く LayoutPoint の Size も同じ値
    - side_area: 解説の欄を出す余白 [x0, y0, x1, y1] (出力の画素)。top-right は画面の左で、上の端は margin。top-left と top は
      画面の右で、上の端は肩テロップ (出力の右上の角) の板の下の端から margin 下。板の下の端は badge.margin × 出力の高さ
      + 板の高さ (badge.height × 出力の高さと、字の大きさ badge.fusion_size × 出力の幅 × fusion_text_scale に上下の pad_y
      を足した高さの大きいほう。文の長さでは変わらない)。下の端はどれも画面の下の端
    """
    lay = sb.get("layout")
    if lay is None:
        return None
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    sc = lay.get("screen") if isinstance(lay, dict) else None
    # 既定 (config の layout_place) は構成表を読むときに重なっている。読み方は common.layout_place と同じ
    height, anchor, margin = ((lay.get(k, sb["layout_place"][k]) for k in ("height", "anchor", "margin"))
                              if isinstance(lay, dict) else (None, None, None))
    bg = dict(sb.get("layout_background") or {}) | dict((lay.get("background") if isinstance(lay, dict) else None) or {})
    bad = C.layout_problems(sc, height, anchor, margin, bg)
    if bad:
        return {"unreadable": "layout の書式を読めない: " + "、".join(bad)}
    h = float(height) * H
    k = h / sc["h"]
    w = sc["w"] * k
    x0 = margin + (W - w - 2 * margin) * LAYOUT_SIDE[anchor]
    if sw * H == sh * W:
        cw, ch = sw, sh
    elif sw * H > sh * W:   # 素材がタイムラインより横長: 高さを足す
        cw, ch = sw, round(sw * H / W)
    else:
        cw, ch = round(sh * W / H), sh
    ox, oy = (cw - sw) / 2, (ch - sh) / 2
    kt = min(W / sw, H / sh)   # 素材をタイムラインに収める倍率 (comp の画はこの倍率でタイムラインに映る)
    if anchor == "top-right":
        side_area = [float(margin), float(margin), x0 - margin, margin + h]
    else:
        b = sb["badge"]
        plate = max(b["height"] * H, b["fusion_size"] * W * float(sb["fusion_text_scale"]) + 2 * b["pad_y"])
        side_area = [x0 + w + margin, b["margin"] * H + plate + margin, W - margin, margin + h]
    return {"screen": dict(sc), "rect": [x0, float(margin), w, h], "side_area": side_area,
            "scale": k, "comp": [cw, ch], "screen_size": k * cw / W,
            "crop": [sc["x"] + ox, ch - (sc["y"] + oy) - sc["h"], sc["w"], sc["h"]],
            "back_size": max(cw / sc["w"], ch / sc["h"]), "blur": float(bg["blur"]), "dim": float(bg["dim"]),
            "filter": C.fusion_filter(sb["zoom"]["filter_method"]), "canvas": (cw, ch) != (sw, sh),
            "view": (kt * (sc["x"] + sc["w"] / 2) + (W - sw * kt) / 2, kt * (sc["y"] + sc["h"] / 2) + (H - sh * kt) / 2),
            "end": k / kt}


def _point_expect(sb, r, size, lay, layout, z, zoom_bad, W, H):
    """行 r の指すマークの期待の値 (expected_rows の point)。size は素材の解像度、lay は layout_expect の読めた値 (無ければ None)、
    layout は行の layout の期待の値 (読めないものも含む。無ければ None)、z は行のズーム範囲 (素材の画素)。
    読めない書式は {"unreadable": 理由} (指す点が読めれば target も。画素の照らし合わせで外す四角に使う)。"""
    pt = dict(sb["point"]) | (r["point"] if isinstance(r["point"], dict) else {})
    bad = _point_problems(pt) if isinstance(r["point"], dict) else ["point は {x, y, ...} の形"]
    if bad:   # 読めない書式は unknown にする (黙って pass にも例外にもしない)
        out = {"unreadable": "point の書式を読めない: " + "、".join(bad)}
        if all(C.is_num(pt.get(c)) for c in "xy"):
            spot = C.source_to_timeline({"x": pt["x"], "y": pt["y"], "w": 0, "h": 0}, *size, sb)
            out["target"] = (spot["x"], spot["y"])
        return out
    spot = C.source_to_timeline({"x": pt["x"], "y": pt["y"], "w": 0, "h": 0}, *size, sb)
    way = POINT_WAY[pt["from"]]
    g = pt["glyphs"][f"{pt['style']}-{way}"]
    # 指の先のずれ (tip) は書体ごとに測った値なので、書体 (Font と Style) も照らし合わせる (組む側は Regular)
    out = {"target": (spot["x"], spot["y"]), "glyph": g["glyph"], "tip": g["tip"], "tilt": float(pt["tilt"]),
           "way": way, "size": float(pt["size"]), "view": None,
           "font": pt["fonts"][pt["style"]], "style": "Regular"}
    if layout:
        # layout の行: マークは画面と一緒に縮めて置く (LayoutPoint)。傾ける向きは画面の真ん中へ。ズームの終わりの倍率の
        # 代わりに、comp (素材をタイムラインに収めた画) から出力への倍率 end で割った大きさで描く
        if lay is None:
            out["unreadable"] = "layout の書式を読めない (マークを置く位置と大きさを決められない)"
        else:
            sc = lay["screen"]
            out["view"] = lay["view"]
            out["layout_size"] = lay["screen_size"]   # LayoutPoint の Size (画面を置く倍率と同じ)
            out["fusion_size"] = out["size"] / lay["end"] / (W * float(sb["fusion_text_scale"]))
            # 指の先が出力に映る点: 画面を置く四角の左上 (rect の x0 y0) から、素材の画素を scale 倍した所
            out["out_target"] = (lay["rect"][0] + (pt["x"] - sc["x"]) * lay["scale"],
                                 lay["rect"][1] + (pt["y"] - sc["y"]) * lay["scale"])
    elif zoom_bad:   # ズームの書式を読めないと、傾きの向きを決める「見えている範囲の真ん中」が決まらない
        out["unreadable"] = "ズーム範囲の書式を読めない (見えている範囲の真ん中を決められない)"
    else:
        # 見えている範囲の真ん中: ズーム (寄る向き) の終わりの範囲の真ん中。ズームが無いか引くなら画面の真ん中
        zb = C.source_to_timeline(z, *size, sb) if z and z.get("direction", "in") != "out" else None
        out["view"] = (zb["x"] + zb["w"] / 2, zb["y"] + zb["h"] / 2) if zb else (W / 2, H / 2)
        # マークはズームの前に置くので、寄せ終わった後に point.size px に見える大きさで描く。ズームの終わりの倍率は
        # 寄る向きならズームの範囲 (タイムラインの画素) が画面いっぱいになる倍率、引くか無ければ 1。
        # Text+ の Size 1 は画面の幅の fusion_text_scale 倍の字
        end = min(W / zb["w"], H / zb["h"]) if zb else 1.0
        out["fusion_size"] = out["size"] / end / (W * float(sb["fusion_text_scale"]))
    return out


def _callout_expect(sb, r, size, fps):
    """行 r の callout の期待の値 (common.callout_values。dst の省略と order は組む側と同じ決め方)。
    読めない書式と、自動の置き場所に空きが無いときは {"unreadable": 理由}。"""
    bad = C.callout_problems(r["callout"])
    if bad:
        return {"unreadable": "、".join(bad)}
    band = C.caption_band(sb, r, C.caption_font(sb)) if "dst" not in r["callout"] else None
    try:
        return C.callout_values(sb, r["callout"], size[0], size[1], fps, band=band)
    except ValueError as ex:   # 自動の置き場所に空きが無い
        return {"unreadable": str(ex)}


def _hold_expect(r, clip):
    """行 r の止めたクリップの期待の値 (行の終わりの次から hold_frames)。読めない書式は {"unreadable": 理由}。"""
    bad = C.hold_problems(r["hold"])
    return ({"unreadable": "、".join(bad)} if bad else
            {"start": clip["rec"] + clip["frames"], "frames": clip["hold_frames"], "spec": r})


def _focus_expect(sb, r, fo, src, n, speed, size, fps, reader):
    """行 r の focus の期待の値: 範囲 (タイムラインの画素) と keys、濃さ、枠線 (色が auto なら組む側と同じ関数
    common.with_auto_border で素材の明るさから決め直す。決められなければ rgb は None)、現れ方のキー。
    読めない書式は {"unreadable": 理由}。"""
    bad = C.focus_problems(fo)
    if bad:
        return {"unreadable": "、".join(bad)}
    spec, auto = C.with_auto_border(sb, r, src, n, speed / 100 if n else 1.0, reader)
    f = C.source_to_timeline(dict(sb["focus"]) | spec["focus"], *size, sb)
    keys = {fr: [k["x"], k["y"], k["w"], k["h"]] for fr, k in C.keys_by_frame(f.get("keys"), fps)}
    border = None
    if f.get("border"):
        bd = dict(sb["focus_border"]) | (f["border"] if isinstance(f["border"], dict) else {})
        border = {"rgb": None if auto and auto.get("error") else C.hex_rgb(bd["color"]),
                  "width": float(bd["width"])}
        if auto:
            border["auto"] = auto   # 素材の明るさで決めた色 (組む側と同じ関数)
    return {"box": [f["x"], f["y"], f["w"], f["h"]], "keys": sorted(keys.items()),
            "dim": float(f["dim"]), "border": border,
            "ramp_frames": C.appear_keys(*C.appear_frames(fo, sb["focus"], "seconds", fps))}


def _zoom_expect(sb, z, size, fps, W, H):
    """ズーム範囲 z (素材の画素。読める書式) の期待の値: 寄り終わるフレーム、Size と Center の始まりと終わり。
    z が無ければ None。倍率と中心の式は意図して組む側 (common.zoom_geometry) と別に持つ。"""
    z = C.source_to_timeline(z, *size, sb)
    if not z:
        return None
    k = min(W / z["w"], H / z["h"])
    u, v = (z["x"] + z["w"] / 2) / W, 1 - (z["y"] + z["h"] / 2) / H
    s0, s1 = C.zoom_ends(z, 1.0, k)
    x0, x1 = C.zoom_ends(z, 0.5, 0.5 + k * (0.5 - u))
    y0, y1 = C.zoom_ends(z, 0.5, 0.5 + k * (0.5 - v))
    return {"frames": round(float(z.get("seconds", 0)) * fps), "size": (s0, s1), "center": ((x0, y0), (x1, y1))}


def _masks_expect(sb, r, size, fps):
    """行 r のマスク (全体の masks の後ろに行の masks) の期待の値: 形の部品、種類、四角 (タイムラインの画素) と keys。
    読めない書式のマスクは {"unreadable": 理由}。"""
    out = []
    for m in list(sb.get("masks") or []) + list(r.get("masks") or []):
        bad = C.mask_problems(m)
        if bad:   # 読めない書式は unknown にする (黙って pass にしない)
            out.append({"unreadable": "、".join(bad)})
            continue
        m = C.source_to_timeline(m, *size, sb)
        keys = {fr: [k["x"], k["y"], k["w"], k["h"]] for fr, k in C.keys_by_frame(m.get("keys"), fps)}
        out.append({"tool": C.MASK_SHAPES[m.get("shape", "rect")], "type": m.get("type", "blur"),
                    "box": [m["x"], m["y"], m["w"], m["h"]], "keys": sorted(keys.items())})
    return out


def expected_rows(sb, sizes=None, reader=None):
    """行ごとの期待する値。フレームはタイムラインの頭を 0 とした書き出しのフレーム。

    構成表のマスクとズーム範囲は素材の画素なので、sizes (source_sizes の値) の素材の解像度でタイムラインの画素に直す。
    sizes を省略すると素材とタイムラインは同じ解像度とみなす。sizes にその素材が無ければ、行の size_unknown を True にする。
    focus の枠線の色が auto なら、組む側 (sb2resolve.py) と同じ関数 (common.with_auto_border) で素材の画素から決め直す
    (決められなければ枠線の rgb は None で、照合は unknown)。callout の dst の省略と order も組む側と同じ callout_values で決める。
    reader は素材のフレームを読む関数の差し替え (テスト用)。
    組む側と共有するのは構成表の行の読み方 (common.row_clips: 素材・使う範囲・位置・フレーム数) だけで、
    速さ・止めたクリップの位置・肩テロップの長さなどの期待の値は、組む側と同じ誤りを持たないようにここで別に計算する。
    項目ごとの値は _point_expect・_callout_expect・_hold_expect・_focus_expect・_zoom_expect・_masks_expect で計算する。
    """
    fps = float(sb["fps"])
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    badge_len = round(float(sb["badge"]["seconds"]) * fps)   # 肩テロップの長さ (フレーム)。check_badge もこの値を読む
    out = []
    for r, clip in zip(sb["rows"], C.row_clips(sb)):
        s, e, at, n, src = clip["start"], clip["end"], clip["rec"], clip["frames"], clip["source"]
        size = (W, H) if sizes is None else sizes.get(_key(src))
        row = {"row": clip["row"], "start": at, "frames": n, "src_start": s, "src_end": e,
               "speed": (e - s) / (n / fps) * 100 if n else None, "source": src,
               # 空の文 ("") は接頭辞だけの肩テロップ (組む側の common.scene_values と同じ)
               "badge": min(n, badge_len) if r.get("badge") is not None else None, "badge_frames": badge_len,
               "badge_text": sb["badge"]["prefix"] + r["badge"] if r.get("badge") is not None else None,
               # 肩テロップの文の速さの表記 (読み方だけ common と共有。合うかは check_badge_speed で決める)
               "badge_speed": C.badge_speed_label(r.get("badge")),
               "closing": r["caption"] if r.get("caption") and r.get("caption_style") == "closing" else None,
               "caption": r["caption"] if r.get("caption") and r.get("caption_style") != "closing" else None,
               "hold": None, "callout": None, "point": None,
               "side": side_expect(sb, r),
               "zoom": None, "masks": [], "focus": None, "size_unknown": size is None}
        size = size or (W, H)
        row["layout"] = layout_expect(sb, *size)
        lay = row["layout"] if row["layout"] and not row["layout"].get("unreadable") else None
        z = r.get("zoom")
        zoom_bad = bool(z) and not (isinstance(z, dict) and all(isinstance(z.get(c), (int, float)) for c in "xywh")
                                    and z["w"] > 0 and z["h"] > 0)
        if r.get("point") is not None:
            row["point"] = _point_expect(sb, r, size, lay, row["layout"], z, zoom_bad, W, H)
        if r.get("callout"):
            row["callout"] = _callout_expect(sb, r, size, fps)
        if r.get("hold") is not None:
            row["hold"] = _hold_expect(r, clip)
        fo = r.get("focus")
        if fo:
            row["focus"] = _focus_expect(sb, r, fo, src, n, row["speed"], size, fps, reader)
        if zoom_bad:
            row["zoom"] = {"unreadable": True, "frames": None}   # 読めない書式は unknown
        else:
            row["zoom"] = _zoom_expect(sb, z, size, fps, W, H)
        row["masks"] = _masks_expect(sb, r, size, fps)
        out.append(row)
    return out


def _c(checks, row, item, status, expected=None, actual=None, note=None):
    d = {"row": row, "item": item, "status": status, "expected": expected, "actual": actual}
    if note:
        d["note"] = note
    checks.append(d)


def _near(a, b, tol):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# ---------- タイムライン (Resolve の API) ----------
def _passed(tool):
    """素通し (PassThrough) の部品か。素通しの部品は効いていないので、ある部品として数えない。読めなければ False。"""
    try:
        return bool((tool.GetAttrs() or {}).get("TOOLB_PassThrough"))
    except Exception:
        return False


def _tools(comp, kind):
    """種類が kind の部品。素通しの部品は書き出しに効かないので外す。"""
    return [t for t in (comp.GetToolList(False) or {}).values() if t.ID == kind and not _passed(t)]


def _k(row):
    """行のクリップの速さの倍率。Fusion の合成の中の時刻は素材の速さで進む (common.comp_frame)。"""
    return (row["speed"] or 100.0) / 100.0


def _xy(v):
    return (v[1], v[2]) if isinstance(v, dict) else (None, None)


def _rgba(tool, t=0):
    """単色 (Background) の色と不透明度 [R, G, B, A] (0〜1)。読めない値は None。"""
    return [tool.GetInput(c, t) for c in ("TopLeftRed", "TopLeftGreen", "TopLeftBlue", "TopLeftAlpha")]


def _has_none(got):
    """値の辞書 got (値は数か数の list) に、読めない値 (None) があるか。"""
    return any(v is None for x in got.values() for v in (x if isinstance(x, list) else [x]))


def _among(users, tool, name):
    """部品の list users に、部品 tool (名前 name) があるか。users が None (つながりを読めない) なら None。
    Resolve の API は呼ぶたびに別のオブジェクトを返すので、同じ物か名前のどちらかで見る。"""
    return None if users is None else any(u is tool or getattr(u, "Name", None) == name for u in users)


def _source(tool, name):
    """部品の入力 name につながった出力の部品。つながっていなければ False、読めなければ None。"""
    try:
        out = getattr(tool, name).GetConnectedOutput()
        return out.GetTool() if out else False
    except Exception:
        return None


def _input_keys(tool, name):
    """部品の入力 name につながったスプラインのキーの時刻 (合成の時刻) の list。動かない入力は []。読めなければ None。

    スプラインの GetKeyFrames() は {時刻: {1: 値, "LH": {...}, "RH": {...}}} を返す (時刻は鍵の側。Resolve 21.1.1 で確認)。
    この形でなければ None (呼ぶ側が行の全部のフレームを読む)。"""
    src = _source(tool, name)
    if not src:
        return None if src is None else []
    try:
        keys = src.GetKeyFrames()
        if not isinstance(keys, dict) or not all(isinstance(v, dict) for v in keys.values()):
            return None
        return sorted({float(t) for t in keys})
    except Exception:
        return None


def _times(tool, name, n, k, lo=0):
    """入力 name を読む合成の時刻。書き出しのフレーム lo〜n − 1 に当たる範囲の頭と終わりと、その中のキーの時刻とその前後。
    キーを読めなければ、範囲の書き出しのフレーム全部 (行の途中の変化を見逃さないため)。"""
    hi = max(n - 1, lo)
    c0, c1 = C.comp_frame(lo, k), C.comp_frame(hi, k)
    keys = _input_keys(tool, name)
    if keys is None:
        return sorted({C.comp_frame(f, k) for f in range(lo, hi + 1)})
    ts = {c0, c1}
    for t in keys:
        ts |= {u for u in (math.floor(t) - 1, math.floor(t), math.ceil(t), math.ceil(t) + 1) if c0 <= u <= c1}
    return sorted(ts)


def _lowest(tool, name, n, k, lo=0):
    """入力 name の、書き出しのフレーム lo〜n − 1 での最小の値と、その合成の時刻。読めない時刻があれば (None, その時刻)。"""
    v = {t: tool.GetInput(name, t) for t in _times(tool, name, n, k, lo)}
    bad = [t for t, x in v.items() if x is None]
    if bad:
        return None, bad[0]
    t = min(v, key=v.get)
    return v[t], t


def _shown(tool, n, k):
    """重ねる Merge の Blend が、書き出しのフレーム 0〜n − 1 (キーの時刻とその前後) で 1 のままか。読めなければ None。
    Blend を下げると、つながっていても重ねた物が薄くなるか消える。"""
    low, _ = _lowest(tool, "Blend", n, k)
    return None if low is None else low > 0.999


def _at(pairs, t):
    """時刻の順の [(時刻, 値)] の時刻 t での値 (間は直線、最初の前と最後の後ろは端の値)。値は数か、数の list (四角)。
    折れ線を補間する所 (現れ方の ramp・マスクと focus の keys) はどれもここで補間する。組む側 (common.value_at) は使わない。"""
    if t <= pairs[0][0]:
        return pairs[0][1]
    for (t0, v0), (t1, v1) in zip(pairs, pairs[1:]):
        if t <= t1:
            a = (t - t0) / (t1 - t0)
            return [p + (q - p) * a for p, q in zip(v0, v1)] if isinstance(v0, (list, tuple)) else v0 + (v1 - v0) * a
    return pairs[-1][1]


def _ramp_ok(tool, name, ramp_frames, full, n, k):
    """現れ方 (ramp_frames [(書き出しのフレーム, 割合)] に full を掛けた値) を、行の頭から現れ終わりの次のフレームまで
    書き出しのフレームごとに読んで比べる (キーの間も読む)。その後ろは行の終わりまで full のままか (キーの時刻と前後で読む)。
    返す値: (True / False、読めなければ None, 記録に残す値)。"""
    ramp = sorted({C.comp_frame(f, k): u * full for f, u in ramp_frames}.items())   # 同じ時刻に落ちたキーは後の値 (組む側と同じ)
    end = min(n, ramp_frames[-1][0] + 2)
    got = {f: tool.GetInput(name, C.comp_frame(f, k)) for f in range(end)}
    rest = {t: tool.GetInput(name, t) for t in _times(tool, name, n, k, end)} if end < n else {}
    if None in got.values() or None in rest.values():
        return None, {"読めない": True}
    bad = [[f, round(_at(ramp, C.comp_frame(f, k)), 4), round(v, 4)] for f, v in got.items()
           if not _near(v, _at(ramp, C.comp_frame(f, k)), 1e-3)]
    bad += [[f"合成の時刻 {t}", round(ramp[-1][1], 4), round(v, 4)] for t, v in rest.items() if not _near(v, ramp[-1][1], 1e-3)]
    return not bad, {"読んだフレーム": f"0〜{end - 1}" + (f" と合成の時刻 {sorted(rest)}" if rest else ""),
                     "違うもの [フレーム, 期待, 実際]": bad[:5], "違う数": len(bad)}


def check_badge(checks, row, comp):
    """肩テロップ: 名前 BadgeOver の Merge の Blend で、消えるフレーム (Blend が 0 になる最初のフレーム) を比べる。

    読むのは行の全部のフレーム (行の途中で出直すものも見逃さない) と、行が肩テロップの長さ (default。expected_rows が
    計算した badge_frames) より短いときは消えるはずのフレームまで (2 秒より短い行でも、消える所が見える)。
    期待する消えるフレームは行の頭から default。
    """
    n, default = row["frames"], row["badge_frames"]
    ts = range(max(n, default + 1))
    found = []
    named = _find(comp, "BadgeOver")   # 肩テロップは名前で読む (時間で変わる別の Merge と取り違えない)
    for m in ([named] if named and not _passed(named) else []):
        v = {t: m.GetInput("Blend", C.comp_frame(t, _k(row))) for t in ts}
        if None in v.values():
            _c(checks, row["row"], "badge", "unknown", default, None, "BadgeOver の Blend を読めない")
            return
        if len({v[t] for t in (ts if row["badge"] is not None else range(n))}) > 1:
            found.append(v)
    if row["badge"] is None:
        if found:
            _c(checks, row["row"], "badge", "fail", None, "時間で変わる Merge がある", "構成表に無い肩テロップ")
        return
    if not found:
        _c(checks, row["row"], "badge", "unknown", default, None, "肩テロップの部品 (BadgeOver) が見つからないか、時間で消えない")
        return
    v = found[0]
    off = next((t for t in ts if not v[t]), None)
    ok = off == default and all(not v[t] for t in ts if t >= default)   # 消えた後に出直さない
    _c(checks, row["row"], "badge", "pass" if ok else "fail", default, off,
       "肩テロップが消える最初のフレーム (行の頭から。行より短い行では行の後ろ)")


def check_badge_speed(checks, row):
    """肩テロップの文に書いた速さ (「N 倍速」「等倍」) と、行の速さ (expected_rows がここで計算した値。止めた区間は入れない)。
    「N 倍速」は速さを N を書いた桁で四捨五入して N と同じなら pass (差が桁の半分まで)。「等倍」は 1 との差が
    common.SAME_SPEED_TOL まで。組む側の sb2resolve.badge_speed_problem を呼ばずに同じ決め方をここで持つ。
    肩テロップに速さの表記が無い行は項目を出さない。タイムラインを読まなくても出る (構成表の中の食い違い)。"""
    lab = row["badge_speed"]
    if lab is None:
        return
    if row["speed"] is None:
        _c(checks, row["row"], "badge_speed", "unknown", lab["text"], None, "行の長さが 0 で速さが決まらない")
        return
    sp = row["speed"] / 100
    if lab["places"] is None:
        ok, places = abs(sp - 1) <= C.SAME_SPEED_TOL, 2
    else:
        ok, places = abs(sp - lab["speed"]) <= 0.5 * 10 ** -lab["places"] + 1e-9, max(1, lab["places"])
    _c(checks, row["row"], "badge_speed", "pass" if ok else "fail", lab["text"], f"{sp:.{places}f} 倍",
       None if ok else "肩テロップの文の速さと、使う範囲の長さ ÷ 行の秒数が合わない")


def _connected(tool, out):
    """部品の出力 out が、どこかの入力につながっているか。読めなければ None。"""
    try:
        return bool(getattr(tool, out).GetConnectedInputs())
    except Exception:
        return None


def _live(checks, row, item, tools, out, expected):
    """出力がつながっている部品だけを返す。つながりを読めなければ unknown を書いて None。"""
    conn = [(t, _connected(t, out)) for t in tools]
    if any(c is None for _, c in conn):
        _c(checks, row["row"], item, "unknown", expected, None, "部品のつながりを読めない")
        return None
    return [t for t, c in conn if c]


def check_zoom(checks, row, comp):
    """ズーム: つながった Transform の Size を、行の頭・1 フレーム目・寄り終わるフレームの前後で読む (直線で動く前提)。
    指すマークを傾ける Transform (PointTilt) はズームではないので外す。"""
    z = row["zoom"]
    if not z:
        return
    if row["size_unknown"] or z.get("unreadable"):
        _c(checks, row["row"], "zoom", "unknown", z["frames"], None,
           "ズーム範囲の書式を読めない" if z.get("unreadable") else "素材の解像度を読めない (ズーム範囲を直せない)")
        return
    xfs = _live(checks, row, "zoom", [t for t in _tools(comp, "Transform") if t.Name != "PointTilt"] if comp else [],
                "Output", z["frames"])
    if xfs is None:
        return
    if not xfs:
        _c(checks, row["row"], "zoom", "fail", z["frames"], None, "つながった Transform が無い")
        return
    xf, zf = xfs[0], z["frames"]
    s0, s1 = z["size"]
    (x0, y0), (x1, y1) = z["center"]
    k = _k(row)
    got = {t: xf.GetInput("Size", C.comp_frame(t, k)) for t in sorted({0, 1, max(zf - 1, 0), zf, zf + 1})}
    if None in got.values():
        _c(checks, row["row"], "zoom_size", "unknown", s1, None, "Size を読めない")
        return
    ok = _near(got[0], s0 if zf else s1, TOL_SIZE) and _near(got[zf], s1, TOL_SIZE) and _near(got[zf + 1], s1, TOL_SIZE)
    if zf > 0 and abs(s1 - s0) > 1e-9:
        step = abs(s1 - s0) / zf / 4   # 1 フレームで動く量の 4 分の 1 より小さい差は「動いていない」
        ok = ok and not _near(got[zf - 1], s1, step) and not _near(got[1], s0, step)   # 早く止まらない・遅れて始まらない
    _c(checks, row["row"], "zoom_size", "pass" if ok else "fail",
       {"0": s0 if zf else s1, str(zf): s1, "寄り終わるフレーム": zf}, {str(t): v for t, v in got.items()})
    c0, c1 = _xy(xf.GetInput("Center", 0)), _xy(xf.GetInput("Center", C.comp_frame(zf, k)))
    exp0 = (x0, y0) if zf else (x1, y1)
    ok = all(_near(a, b, TOL_SIZE) for a, b in zip(c0 + c1, exp0 + (x1, y1)))
    _c(checks, row["row"], "zoom_center", "pass" if ok else "fail", [exp0, (x1, y1)], [c0, c1])


def expected_box(m, f):
    """マスクのフレーム f での四角 (タイムラインの画素)。keys の間は直線 (_at)、最初の key の前と最後の key の後はその位置。"""
    return _at(m["keys"], f) if m["keys"] else m["box"]


def _in_comp(m, k):
    """keys のフレームを合成の時刻にしたマスク。速さを変えた行では、組む側 (common.scene_values) と同じく key ごとに
    comp_frame で切り捨てるので、期待する位置も合成の時刻どうしで補間する。"""
    keys = {}
    for f, b in m["keys"]:
        keys[C.comp_frame(f, k)] = b   # 遅くした行で同じ時刻に落ちたキーは、組む側と同じく後の値
    return dict(m, keys=sorted(keys.items()))


def _box_frames(m, n, mids=False):
    """四角を比べる書き出しのフレーム (昇順): 行の頭と終わり、行の中の keys のフレーム。mids なら keys の間の真ん中も
    (マスクの割り当てで、頭の位置が同じマスクを取り違えないため)。"""
    frames = {0, max(n - 1, 0)} | {f for f, _ in m["keys"] if 0 <= f < n}
    if mids:
        frames |= {(f0 + f1) // 2 for (f0, _), (f1, _) in zip(m["keys"], m["keys"][1:]) if (f0 + f1) // 2 < n}
    return sorted(frames)


def _want_boxes(m, frames, k, grow=0.0):
    """フレームごとの期待の四角 {書き出しのフレーム: [x, y, w, h]} (0.1 画素に丸める)。
    速さを変えた行では、書き出しのフレーム f は合成の時刻 comp_frame(f) の画なので、期待する位置もその時刻で計算する。
    grow は四方に広げる画素 (枠線の外の四角は範囲を枠線の幅の半分だけ広げる)。"""
    mc = _in_comp(m, k)
    out = {}
    for f in frames:
        e = expected_box(mc, C.comp_frame(f, k))
        out[f] = ([round(e[0] - grow, 1), round(e[1] - grow, 1), round(e[2] + 2 * grow, 1), round(e[3] + 2 * grow, 1)]
                  if grow else [round(v, 1) for v in e])
    return out


def _got_boxes(tool, frames, k, W, H):
    """Fusion のマスクの部品のフレームごとの四角 {書き出しのフレーム: [x, y, w, h] か None} (_box_at)。"""
    return {f: _box_at(tool, C.comp_frame(f, k), W, H) for f in frames}


def _box_at(t, f, W, H):
    """Fusion のマスクのフレーム f の四角 (タイムラインの画素)。読めなければ None。"""
    x, y = _xy(t.GetInput("Center", f))
    w, h = t.GetInput("Width", f), t.GetInput("Height", f)
    if None in (x, y, w, h):
        return None
    return [round(x * W - w * W / 2, 1), round((1 - y) * H - h * H / 2, 1), round(w * W, 1), round(h * H, 1)]


EFFECTS = {"mosaic": "ofx.com.blackmagicdesign.resolvefx.MosaicBlur", "blur": "Blur", "black": "Background"}


def _targets(tool, out, ids=False):
    """部品の出力 out がつながった先の部品の list。ids なら (部品, 入った入力の ID) の list (ID を読めなければ None)。
    読めなければ None。"""
    try:
        ins = (getattr(tool, out).GetConnectedInputs() or {}).values()
        return [(inp.GetTool(), _input_id(inp)) for inp in ins] if ids else [inp.GetTool() for inp in ins]
    except Exception:
        return None


def _input_id(inp):
    """入力の ID (Input・EffectMask など)。Input の ID か、GetAttrs の INPS_ID。読めなければ None。"""
    def read(f):
        try:
            v = f()
        except Exception:
            return None
        return v if isinstance(v, str) and v else None
    return read(lambda: inp.ID) or read(lambda: (inp.GetAttrs() or {}).get("INPS_ID"))


# 画の入力の ID。組む側 (build_fusion) が MediaIn1 から切り抜きまでに通す部品の入力: Crop (Canvas・切り抜き) と Blur の
# Input、Resolve FX のモザイクの Source、Merge (黒塗りの MaskNOver・FocusDim・枠線の Merge) の Background と Foreground。
# マスクの入力 (EffectMask など) は画を運ばないので入れない
IMAGE_INPUTS = ("Input", "Source", "Background", "Foreground")


def reaches(tool, name=None, out="Output", inputs=None):
    """部品の出力 out が、つながりをたどって名前 name の部品に届くか。name が無ければ MediaOut (合成の出口) に届くか。
    inputs (入力の ID の組) があれば、その入力に入るつながりだけをたどる (IMAGE_INPUTS なら画の入力だけ。マスクの入力を
    通る道は数えない)。読めなければ None。

    たどった部品は名前 (Name。1 つの合成の中で一意) で覚える。Resolve の API は呼ぶたびに別の Python のオブジェクトを返し、
    捨てたオブジェクトの id() は使い回されうるので、id() で覚えると、まだたどっていない部品を飛ばして False を返しうる。"""
    def step(t, o):
        got = _targets(t, o, ids=inputs is not None)
        if got is None or inputs is None:
            return got
        if any(i is None for _, i in got):
            return None
        return [u for u, i in got if i in inputs]
    first = step(tool, out)
    if first is None:
        return None
    seen, todo = set(), list(first)
    while todo:
        t = todo.pop()
        mo = getattr(t, "ID", None) == "MediaOut"
        if name is None and mo:
            return True
        got = getattr(t, "Name", None)
        if not got:
            return None
        if got == name:
            return True
        if got in seen or mo:   # MediaOut は合成の出口 (その先は無い)
            continue
        seen.add(got)
        nxt = step(t, "Output")
        if nxt is None:
            return None
        todo += nxt
    return False


def _reached(checks, row, item, tool, expected, note, unread=None):
    """部品 tool の出力が MediaOut まで届けば True。届かなければ fail (理由は note)、つながりを読めなければ unknown
    (理由は unread、無ければ note) を書いて False。"""
    reach = reaches(tool)
    if not reach:
        _c(checks, row, item, "unknown" if reach is None else "fail", expected, None,
           unread if reach is None and unread else note)
    return bool(reach)


def _reaches_input(src, tool, name):
    """部品 src の画が、つながりをたどって部品 tool の入力 name に届くか (name につながった部品が src か、src から届く部品)。
    src が無ければ False、読めなければ None。どの入力に届いたかまで見る (EffectMask などほかの入力に届いても画は入らない)。
    途中の部品も画の入力 (IMAGE_INPUTS) に入るつながりだけをたどる (途中の部品のマスクの入力にだけつながっていても画は入らない)。"""
    if src is None:
        return False
    s = _source(tool, name)
    if not s:
        return s
    try:
        got, mine, dst = s.Name, src.Name, tool.Name
    except Exception:
        return None
    if not got or not mine or not dst:
        return None
    # src が直につながっているときも、src の出力の側から tool に届くことを確認する (入力と出力の読みが食い違えば届かない)
    return reaches(src, dst if got == mine else got, inputs=IMAGE_INPUTS)


def mask_effect(mask, kind, n=1, k=1.0):
    """マスクが、種類 kind の隠す部品に効いているか。("pass" / "fail" / "unknown", 説明)。

    マスクの出力がつながった先の部品が、素通しでない kind の部品であること (黒塗りは、その単色の出力が素通しでない Merge に
    つながっていること)。テンプレートの隠す部品を素通しにしても、マスクの値とつながりは残るので、ここで見る。
    その部品 (黒塗りは Merge) の出力が MediaOut まで届くことも見る (切り離された部品は書き出しに出ない)。
    黒塗りの Merge の Blend は、行 (n フレーム、速さの倍率 k) の頭と終わりと、その中のキーの時刻とその前後で読み、
    どこかで 0.999 以下なら効いていないとみなす (行の途中で薄くなる黒塗りも隠し漏れになる)。
    """
    users = _targets(mask, "Mask")
    if users is None:
        return "unknown", "マスクのつながり先を読めない"
    live = [u for u in users if not _passed(u)]
    ids = sorted({u.ID for u in live})
    hits = [u for u in live if u.ID == EFFECTS[kind]]
    if kind == "black":
        outs = [_targets(u, "Output") for u in hits]
        if any(o is None for o in outs):
            return "unknown", "黒塗りの単色のつながり先を読めない"
        hits = [v for o in outs for v in o if v.ID == "Merge" and not _passed(v)]
        low = [_lowest(v, "Blend", n, k) for v in hits]
        if any(x is None for x, _ in low):
            return "unknown", "黒塗りの Merge の Blend を読めない"
        thin = [(x, t) for x, t in low if x <= 0.999]   # Blend を下げると黒塗りが薄くなる
        hits = [v for v, (x, _) in zip(hits, low) if x > 0.999]
        if thin and not hits:
            return "fail", f"黒塗りの Merge の Blend が合成の時刻 {thin[0][1]} で {round(thin[0][0], 4)} (黒塗りが薄くなる)"
    reach = [reaches(u) for u in hits]
    if any(r is None for r in reach):
        return "unknown", "隠す部品の出力のつながりを読めない"
    if any(reach):
        return "pass", EFFECTS[kind]
    if hits:
        return "fail", "隠す部品の出力が MediaOut まで届かない (書き出しに出ない)"
    if not live:
        return "fail", "マスクがつながった隠す部品が無いか、素通し"
    return "fail", f"マスクがつながった部品の種類が違う: {ids}"


MAX_ASSIGN = 8          # 1 行の同じ形のマスクがこれより多いと、部品の割り当てを全部は比べない (順に選ぶ)
MAX_PERMS = 200000      # 比べる割り当ての数の上限 (部品が多いとき)
_UNREAD = 1e12          # 位置を読めない部品の差 (割り当てで一番後回しにする)


def _assign(cost, ok, n_tools):
    """マスク i に部品 j を割り当てた位置の差 cost[i][j] と、位置と種類の両方が pass になるか ok[i][j] から、行の中の割り当てを
    まとめて選ぶ。基準は「位置と種類の両方が pass になるマスクの数が最も多い → 位置の差の合計が最も小さい」(同じなら構成表の
    順に近いもの)。部品がマスクより少なければ、前から部品の数だけのマスクの間で選び、足りない分は構成表の後ろのマスクを
    部品の無いマスク (None) にする。返す値: (マスクごとの部品の番号か None, 順に選んだか)。
    マスクか割り当ての数が多すぎるときは、構成表の順に差の一番小さい部品を選ぶ (順に選ぶと、近い位置のマスクが 2 つあれば
    先のマスクが後のマスクの部品を取りうる)。"""
    m = len(cost)
    mm = min(m, n_tools)
    if m > MAX_ASSIGN or math.perm(n_tools, mm) > MAX_PERMS:
        pick, used = [], set()
        for i in range(m):
            free = [j for j in range(n_tools) if j not in used]
            j = min(free, key=lambda j: cost[i][j]) if free else None
            pick.append(j)
            if j is not None:
                used.add(j)
        return pick, True
    best = None
    for p in itertools.permutations(range(n_tools), mm):
        key = (-sum(ok[i][j] for i, j in enumerate(p)), sum(cost[i][j] for i, j in enumerate(p)))
        if best is None or key < best[0]:
            best = (key, p)
    return list(best[1]) + [None] * (m - mm), False


def check_masks(checks, row, comp, W, H, prefix="mask"):
    """マスク: 同じ形でつながっている Fusion のマスクを構成表のマスクに 1 対 1 で割り当て、位置と大きさ (keys があれば
    各 key のフレームと key の間、行の最後のフレーム) と、効いている隠す部品の種類を比べる。

    割り当ては形ごとに行の中でまとめて選ぶ (_assign)。差は比べるフレーム全部での位置と大きさの差の合計。行の頭の位置だけで
    選ぶと、行の頭で同じ所に隠れている、出たり消えたりするマスクどうしを取り違える。前から順に選ぶと、近い位置のマスクが
    2 つあるとき、先のマスクが後のマスクの部品を取る。部品の一覧は形ごとに 1 回だけ読み、その一覧の中の番号で割り当てる。
    Resolve の API は呼ぶたびに別の Python のオブジェクトを返すので、id() では同じ部品を 2 回使ってしまう。"""
    n = row["frames"]
    k = _k(row)
    plan = {}   # マスクの番号 → ("unknown" / "fail" / "ok", 中身)
    groups = {}
    for i, m in enumerate(row["masks"]):
        if m.get("unreadable"):
            plan[i] = ("unknown", None, None, "マスクの書式を読めない: " + m["unreadable"])
        elif row["size_unknown"]:
            plan[i] = ("unknown", m["box"], None, "素材の解像度を読めない (マスクの位置を直せない)")
        else:
            groups.setdefault(m["tool"], []).append(i)
    for kind, idx in groups.items():
        pool = _tools(comp, kind) if comp else []
        conn = [(t, _connected(t, "Mask")) for t in pool]
        if any(c is None for _, c in conn):
            for i in idx:
                plan[i] = ("unknown", row["masks"][i]["box"], None, "部品のつながりを読めない")
            continue
        live = [t for t, c in conn if c]
        wants, reads, cost, ok, effs = [], [], [], [], []
        effect = {}   # (部品の番号, 種類) → mask_effect の結果
        for i in idx:
            m = row["masks"][i]
            frames = _box_frames(m, n, mids=True)
            want = _want_boxes(m, frames, k)
            got = [_got_boxes(t, frames, k, W, H) for t in live]
            wants.append(want)
            reads.append(got)
            cost.append([_UNREAD if None in g.values() else sum(abs(p - q) for f in g for p, q in zip(g[f], want[f]))
                         for g in got])
            for j, t in enumerate(live):
                if (j, m["type"]) not in effect:
                    effect[j, m["type"]] = mask_effect(t, m["type"], n, k)
            effs.append([effect[j, m["type"]] for j in range(len(live))])
            # 割り当ての点数は位置と種類の両方で数える (同じ所に種類の違うマスクが並ぶと、位置だけでは入れ替わる)。
            # 種類が unknown の部品は pass に数えない
            ok.append([None not in g.values() and all(_near(a, b, TOL_PX) for f in g for a, b in zip(g[f], want[f]))
                       and effs[-1][j][0] == "pass" for j, g in enumerate(got)])
        pick, greedy = _assign(cost, ok, len(live))
        for a, (i, j) in enumerate(zip(idx, pick)):
            if j is None:
                plan[i] = ("fail", row["masks"][i]["box"], None, f"つながった {kind} が無い (マスクが無い)")
            else:
                plan[i] = ("ok", wants[a], reads[a][j], effs[a][j], greedy)
    for i, m in enumerate(row["masks"]):
        item = f"{prefix}{i + 1}"
        p = plan[i]
        if p[0] != "ok":
            _c(checks, row["row"], item, *p)
            continue
        _, want, got, (status, note), greedy = p
        how = " (マスクが多いので、部品は構成表の順に選んだ)" if greedy else ""
        if None in got.values():
            _c(checks, row["row"], item, "unknown", want, got, "マスクの位置と大きさを読めない" + how)
        else:
            good = all(_near(a, b, TOL_PX) for f in got for a, b in zip(got[f], want[f]))
            _c(checks, row["row"], item, "pass" if good else "fail", want, got,
               "フレームごとの x, y, w, h (タイムラインの画素。構成表の素材の画素から直した値)" + how)
        _c(checks, row["row"], f"{item}_type", status, m["type"], note, "マスクが効いている隠す部品")


def _find(comp, name):
    """名前の付いた部品。無いか読めなければ None。"""
    try:
        return comp.FindTool(name) if comp else None
    except Exception:
        return None


def check_focus(checks, row, comp, W, H, prefix=""):
    """周りを暗くする: 名前の付いた部品 (FocusArea・FocusShade・FocusDim) の範囲 (keys も)、濃さ (1 − 黒の不透明度。
    FocusDim の Blend が行の中で 1 のままであることも)、暗くなる割合 (書き出しのフレームごと) を比べる。
    部品が無いか素通しなら fail、値を読めなければ unknown。prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    fo = row["focus"]
    if not fo:
        return
    r = row["row"]
    item = prefix + "focus"
    if fo.get("unreadable"):
        _c(checks, r, item, "unknown", None, None, "focus の書式を読めない: " + fo["unreadable"])
        return
    if row["size_unknown"]:
        _c(checks, r, item, "unknown", fo["box"], None, "素材の解像度を読めない (範囲を直せない)")
        return
    area, shade, over = (_find(comp, n) for n in ("FocusArea", "FocusShade", "FocusDim"))
    if not (area and shade and over) or any(_passed(t) for t in (area, shade, over)):
        _c(checks, r, item, "fail", fo["box"], None, "FocusArea・FocusShade・FocusDim のどれかが無いか、素通し")
        return
    if not _reached(checks, r, item, over, fo["box"], "FocusDim の出力が MediaOut まで届かない", "FocusDim の出力のつながりを読めない"):
        return
    used = _among(_targets(area, "Mask"), shade, "FocusShade")
    if not used:
        _c(checks, r, item, "unknown" if used is None else "fail", fo["box"], None,
           "FocusArea のつながりを読めない" if used is None else "FocusArea が FocusShade に効いていない")
        return
    k, n = _k(row), row["frames"]
    m = {"keys": fo["keys"], "box": fo["box"]}
    frames = _box_frames(m, n)
    got = _got_boxes(area, frames, k, W, H)
    want = _want_boxes(m, frames, k)
    inv = area.GetInput("Invert", 0)
    if None in got.values() or inv is None:
        _c(checks, r, item, "unknown", want, got, "範囲を読めない")
    else:
        ok = bool(inv) and all(_near(a, b, TOL_PX) for f in got for a, b in zip(got[f], want[f]))
        _c(checks, r, item, "pass" if ok else "fail", want, {"box": got, "invert": inv},
           "範囲の x, y, w, h (タイムラインの画素) と、外側を暗くする (Invert)")
    ramp = fo["ramp_frames"]
    zf = ramp[-1][0]   # 暗くなり終わるフレーム (行の頭から。0 は最初から暗い)
    alpha = {t: shade.GetInput("TopLeftAlpha", C.comp_frame(t, k)) for t in sorted({zf, max(n - 1, zf)})}
    low, at = _lowest(over, "Blend", n, k)   # FocusDim の Blend を下げると、黒が薄くなり暗くならない
    if None in alpha.values() or low is None:
        _c(checks, r, prefix + "focus_dim", "unknown", fo["dim"], None, "FocusShade の不透明度か FocusDim の Blend を読めない")
        return
    a = 1 - fo["dim"]
    ok = _near(alpha[zf], a, 1e-3) and _near(alpha[max(n - 1, zf)], a, 1e-3) and low > 0.999
    _c(checks, r, prefix + "focus_dim", "pass" if ok else "fail", {"dim": fo["dim"], "FocusDim の Blend": 1.0},
       {"dim": round(1 - alpha[zf], 4), "FocusDim の Blend の最小": round(low, 4), "その合成の時刻": at},
       "外側の明るさの倍率 (1 − FocusShade の不透明度) と、FocusDim の Blend が行の中で 1 のままか")
    # 現れ始める前は明るいまま、start から seconds をかけて暗くなり、その後は行の終わりまで暗いまま (キーの間も読む)
    ok, got = _ramp_ok(shade, "TopLeftAlpha", ramp, a, n, k)
    _c(checks, r, prefix + "focus_time", "unknown" if ok is None else ("pass" if ok else "fail"), ramp, got,
       "暗くなる割合 (行の頭からの書き出しのフレームごと。start から seconds をかけて暗くなる)")
    check_focus_border(checks, row, comp, W, H, m, k, prefix)


def check_focus_border(checks, row, comp, W, H, m, k, prefix=""):
    """focus の枠線 (FocusBorder・FocusBorderOuter・FocusBorderOver): 色、現れ方 (暗くなるのと同じ。書き出しのフレームごと)、
    外の四角の位置と大きさ (範囲を枠線の幅の半分だけ広げたもの)、出力が MediaOut に届くこと。"""
    fo = row["focus"]
    b = fo.get("border")
    if not b:
        return
    r = row["row"]
    item = prefix + "focus_border"
    if b["rgb"] is None:   # 色が auto で、素材を読めず色を決められない
        _c(checks, r, item, "unknown", {"auto": b.get("auto")}, None, b["auto"]["error"])
        return
    color, outer, inner, over = (_find(comp, n) for n in ("FocusBorder", "FocusBorderOuter", "FocusBorderInner",
                                                          "FocusBorderOver"))
    if not (color and outer and inner and over) or any(_passed(t) for t in (color, outer, inner, over)):
        _c(checks, r, item, "fail", b, None,
           "枠線の部品 (FocusBorder・FocusBorderOuter・FocusBorderInner・FocusBorderOver) が無いか、素通し")
        return
    if not _reached(checks, r, item, over, b, "枠線の出力が MediaOut まで届かない"):
        return
    ramp, d, n = fo["ramp_frames"], b["width"] / 2, row["frames"]
    rgb = _rgba(color)[:3]
    # 現れ方は FocusBorderOver の Blend (単色の不透明度は色に掛からないので使わない)
    timed, blend = _ramp_ok(over, "Blend", ramp, 1.0, n, k)
    ring = (inner.GetInput("Invert", 0), outer.GetInput("PaintMode", 0), _targets(inner, "Mask"))
    frames = _box_frames(m, n)
    boxes = _got_boxes(outer, frames, k, W, H)
    if None in rgb or timed is None or None in boxes.values() or ring[0] is None or ring[2] is None:
        _c(checks, r, item, "unknown", b, None, "枠線の値を読めない")
        return
    want = _want_boxes(m, frames, k, grow=d)   # 外の四角: 範囲を枠線の幅の半分だけ広げる
    is_ring = bool(ring[0]) and ring[1] == "Multiply" and _among(ring[2], outer, "FocusBorderOuter")
    ok = (all(_near(x * 255, y, 1.0) for x, y in zip(rgb, b["rgb"])) and timed and is_ring
          and all(_near(x, y, TOL_PX) for f in frames for x, y in zip(boxes[f], want[f])))
    _c(checks, r, item, "pass" if ok else "fail",
       {"rgb": b["rgb"], "outer": want, "blend": {str(f): u for f, u in ramp}, "ring": True,
        **({"auto": b["auto"]} if b.get("auto") else {})},
       {"rgb": [round(x * 255, 1) for x in rgb], "outer": boxes, "blend": blend, "ring": is_ring},
       "枠線の色 (auto なら素材の明るさで決め直した色)、外の四角 (範囲を枠線の幅の半分だけ広げる。タイムラインの画素、"
       "keys のフレームも)、FocusBorderOver の Blend で現れる時刻、輪 (反転した内の四角を外の四角に Multiply)")


def check_badge_box(checks, sb, row, comp, W, H):
    """肩テロップの板 (名前 BadgeMask) の位置と大きさ。板は文の長さに合わせて広がる (common.badge_box) ので、同じ計算と比べる。
    書体のファイルが無く文の幅を測れないとき、板の部品が見つからないときは unknown。"""
    if row["badge_text"] is None:
        return
    box = C.badge_box(sb, row["badge_text"], C.badge_font(sb))
    want = [round(v, 1) for v in (box["center"][0] * W, (1 - box["center"][1]) * H, box["width"] * W, box["height"] * H)]
    if not box["measured"]:
        _c(checks, row["row"], "badge_box", "unknown", want, None, "書体のファイルか Pillow が無く、文の幅を測れない")
        return
    t = _find(comp, "BadgeMask")
    b = _box_at(t, 0, W, H) if t else None
    if b is None:
        _c(checks, row["row"], "badge_box", "unknown", want, None, "肩テロップの板の部品 (BadgeMask) を読めない")
        return
    got = [round(b[0] + b[2] / 2, 1), round(b[1] + b[3] / 2, 1), b[2], b[3]]
    _c(checks, row["row"], "badge_box", "pass" if all(_near(a, c, TOL_PX) for a, c in zip(got, want)) else "fail",
       want, got, "肩テロップの板の中心 x, y と幅・高さ (タイムラインの画素。文の長さに合わせて広がる)")


def _text_want(fit):
    """テロップ (通常と結び) の文・Size・Center の期待の値。fit は common.fit_caption の値 (横は画面の真ん中)。"""
    return {"text": "\n".join(fit["lines"]), "size": round(fit["fusion_size"], 4), "center": (0.5, round(fit["bar_center"], 4))}


def check_closing(checks, sb, row, comp):
    """結びのテロップ (名前 ClosingText): 文・大きさ・位置 (画面の中央) を common.fit_caption の closing と比べる。"""
    if row["closing"] is None:
        return
    want = _text_want(C.fit_caption(sb, row["closing"], C.caption_font(sb), "closing"))
    t = _find(comp, "ClosingText")
    if t is None or _passed(t):
        _c(checks, row["row"], "closing", "fail", want, None, "結びのテロップの部品 (ClosingText) が無いか、素通し")
        return
    if not _reached(checks, row["row"], "closing", t, want, "ClosingText の出力が MediaOut まで届かない"):
        return
    got = {"text": t.GetInput("StyledText", 0), "size": t.GetInput("Size", 0), "center": _xy(t.GetInput("Center", 0))}
    if got["text"] is None or got["size"] is None or None in got["center"]:
        _c(checks, row["row"], "closing", "unknown", want, got, "結びのテロップの値を読めない")
        return
    ok = (got["text"] == want["text"] and _near(got["size"], want["size"], 1e-3)
          and all(_near(a, b, 1e-3) for a, b in zip(got["center"], want["center"])))
    _c(checks, row["row"], "closing", "pass" if ok else "fail", want, got, "結びのテロップの文・Size・Center")


def _fed(tool, name, src):
    """部品の入力 name に、名前 src の部品の出力がつながっているか。読めなければ None。
    どの入力につながったかまで見る (Merge の Foreground と Background を入れ替えると、不透明な前景が文を隠しうる)。"""
    s = _source(tool, name)
    if not s:
        return s
    try:
        got = s.Name
    except Exception:
        return None
    return None if not got else got == src


def window_links(comp, look, fit, W, H, want, got, n=1, k=1.0):
    """テロップの板の縁 (CaptionBorder) と行の終わりのマーク (CaptionMark) を want・got に追加し、つながりの list を返す。
    縁は板より border_width px 外の、border_rgb の不透明な額縁。内側は CaptionBorderHole (板 CaptionBar と同じ幅と高さ、
    Invert 1) を CaptionBorderMask の EffectMask につなぎ、CaptionBorderMask の PaintMode を Multiply にして抜く
    (common.solid の hole)。マークは end_mark の文が CaptionMarkOver を通って MediaOut に届くこと。
    CaptionBorderOver と CaptionMarkOver の Blend は、行 (n フレーム、速さの倍率 k) の全部で 1 のままか見る。
    縁と穴の Center は板の Center、CornerRadius は caption.corner_radius (入れていなければ Fusion の既定の 0)。
    マークの Size はテロップの Size の end_mark_scale 倍、Center は板の内側の右下 (右に margin_x / 2、下に pad_y / 2 を空ける)。
    期待の値は組む側 (common.caption_window) を呼ばずにここで計算する。
    want は板の値 (center・width・height) を持っていること。"""
    out = []
    bw = float(look.get("border_width", 0))
    inset = float(look.get("bar_inset", 0))
    corner = look.get("corner_radius") or 0
    if bw:
        want["border"] = {"rgba": list(look["border_rgb"][:3]) + [255], "width": round(1 - 2 * inset / W, 4),
                          "height": round(fit["bar_height"], 4), "center": want["center"], "corner": corner,
                          "hole": {"width": want["width"], "height": want["height"], "invert": 1, "paint": "Multiply",
                                   "center": want["center"], "corner": corner},
                          "blend": 1.0}
        b, m, o = _find(comp, "CaptionBorder"), _find(comp, "CaptionBorderMask"), _find(comp, "CaptionBorderOver")
        hole = _find(comp, "CaptionBorderHole")
        if not (b and m and o and hole) or any(_passed(x) for x in (b, m, o, hole)):
            got["border"] = None
            return [False]
        shown = _shown(o, n, k)
        got["border"] = {"rgba": [round((v or 0) * 255, 1) for v in _rgba(b)],
                         "width": m.GetInput("Width", 0), "height": m.GetInput("Height", 0),
                         "center": _xy(m.GetInput("Center", 0)), "corner": m.GetInput("CornerRadius", 0),
                         "hole": {"width": hole.GetInput("Width", 0), "height": hole.GetInput("Height", 0),
                                  "invert": hole.GetInput("Invert", 0), "paint": m.GetInput("PaintMode", 0),
                                  "center": _xy(hole.GetInput("Center", 0)), "corner": hole.GetInput("CornerRadius", 0)},
                         "blend": shown}
        g, w = got["border"], want["border"]
        gh, wh = g["hole"], w["hole"]
        # 抜きの値を読めなければ None (unknown)。値の読み方は focus の枠線の輪と同じ
        # CornerRadius は入れていなければ既定の 0 (組む側は corner_radius が 0 なら入れない)。板 (CaptionBarMask) と同じ読み方
        hole_ok = (None if None in (gh["width"], gh["height"], gh["invert"]) or None in gh["center"] else
                   bool(gh["invert"]) and gh["paint"] == "Multiply" and _near(gh["width"], wh["width"], 1e-3)
                   and _near(gh["height"], wh["height"], 1e-3)
                   and all(_near(a, c, 1e-3) for a, c in zip(gh["center"], wh["center"]))
                   and _near(gh["corner"] or 0, wh["corner"], 1e-3))
        frame_ok = (None if None in g["center"] else
                    all(_near(x, y, 1.0) for x, y in zip(g["rgba"], w["rgba"])) and _near(g["width"] or 0, w["width"], 1e-3)
                    and _near(g["height"] or 0, w["height"], 1e-3)
                    and all(_near(a, c, 1e-3) for a, c in zip(g["center"], w["center"]))
                    and _near(g["corner"] or 0, w["corner"], 1e-3))
        out += [_fed(b, "EffectMask", "CaptionBorderMask"), _fed(o, "Foreground", "CaptionBorder"), reaches(o),
                _fed(m, "EffectMask", "CaptionBorderHole"), hole_ok, shown, frame_ok]
    if look.get("end_mark"):
        # マークの字はテロップの字 (fit の size px) の end_mark_scale 倍 (config の caption.end_mark_scale)。外の板 (縁を含む) の
        # 右の端から縁・margin_x / 2・字の半分だけ内側、外の板の下の端から縁・pad_y / 2・字の半分だけ上 (Fusion の座標は下が 0)
        scale = float(look["end_mark_scale"])
        mpx = fit["size"] * scale
        x = (W / 2 + (W - 2 * inset) / 2 - bw - look["margin_x"] / 2 - mpx / 2) / W
        y = fit["bar_center"] - fit["bar_height"] / 2 + (bw + look["pad_y"] / 2 + mpx / 2) / H
        want["mark"] = look["end_mark"]
        want["mark_size"] = round(fit["fusion_size"] * scale, 6)
        want["mark_center"] = (round(x, 4), round(y, 4))
        t, o = _find(comp, "CaptionMark"), _find(comp, "CaptionMarkOver")
        tl = bool(t) and not _passed(t)
        got["mark"] = t.GetInput("StyledText", 0) if tl else None
        got["mark_size"] = t.GetInput("Size", 0) if tl else None
        got["mark_center"] = _xy(t.GetInput("Center", 0)) if tl else (None, None)
        live = bool(o) and not _passed(o)
        shown = _shown(o, n, k) if live else False
        got["mark_blend"] = shown
        # マークの部品が無いか素通しなら fail。あるのに Size か Center を読めなければ unknown
        place = (False if not tl else None if got["mark_size"] is None or None in got["mark_center"] else
                 _near(got["mark_size"], fit["fusion_size"] * scale, 1e-4)
                 and _near(got["mark_center"][0], x, 1e-3) and _near(got["mark_center"][1], y, 1e-3))
        out += [live and _fed(o, "Foreground", "CaptionMark"), reaches(o) if o else False, shown,
                got["mark"] == look["end_mark"], place]
    return out


def check_caption(checks, sb, row, comp, prefix=""):
    """通常のテロップ (下の帯。名前 CaptionText・CaptionOver と、背景の CaptionBar・CaptionBarMask・CaptionBarOver)。

    - caption: 文 (common.fit_caption の改行)・Size・Center・書体・字の色と、CaptionText が CaptionOver の Foreground に
      つながり、CaptionOver の出力が MediaOut に届くこと
    - caption_bar: 背景のある見た目 (caption.bar) のとき、背景の色と不透明度、四角 (CaptionBarMask) の中心・幅・高さと、
      CaptionBarMask が CaptionBar の EffectMask に、CaptionBar が CaptionBarOver の Foreground につながり、
      CaptionBarOver の出力が MediaOut に届くこと (つなぎ方は組む側の common._caption とテンプレートと同じ)
    - caption_time: 行の全部で出ていること (CaptionOver と CaptionBarOver の Blend が、行の頭と終わりとキーの時刻とその前後で 1 のまま)
    構成表にテロップが無い行に、MediaOut に届くテロップ (素通しでない CaptionOver) があれば caption を fail にする。
    部品が無いか素通しなら fail、値やつながりを読めなければ unknown。Fusion の値を比べるだけで、書き出しの字の画素は見ない。
    prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    r, item = row["row"], prefix + "caption"
    over, bover = _find(comp, "CaptionOver"), _find(comp, "CaptionBarOver")
    if row["caption"] is None:
        reach = reaches(over) if over and not _passed(over) else False
        if reach is not False:
            _c(checks, r, item, "unknown" if reach is None else "fail", None, "MediaOut に届く CaptionOver",
               "CaptionOver のつながりを読めない" if reach is None else "構成表に無いテロップ")
        return
    look = C.caption_look(sb)
    fit = C.fit_caption(sb, row["caption"], C.caption_font(sb))
    want = _text_want(fit) | {"font": look["font_name"], "style": look["font_style"], "rgb": list(look["text_rgb"][:3])}
    t = _find(comp, "CaptionText")
    if not (t and over) or _passed(t) or _passed(over):
        _c(checks, r, item, "fail", want, None, "テロップの部品 (CaptionText・CaptionOver) が無いか、素通し")
        return
    reach, feeds = reaches(over), _fed(over, "Foreground", "CaptionText")
    if not (reach and feeds):
        _c(checks, r, item, "unknown" if reach is None or feeds is None else "fail", want, None,
           "CaptionText・CaptionOver のつながりを読めない" if reach is None or feeds is None
           else "CaptionOver の Foreground に CaptionText がつながっていない" if not feeds else "CaptionOver の出力が MediaOut まで届かない")
    else:
        got = {"text": t.GetInput("StyledText", 0), "size": t.GetInput("Size", 0), "center": _xy(t.GetInput("Center", 0)),
               "font": t.GetInput("Font", 0), "style": t.GetInput("Style", 0),
               "rgb": [t.GetInput(x, 0) for x in ("Red1", "Green1", "Blue1")]}
        if None in (got["text"], got["size"], got["font"], got["style"]) or None in got["center"] or None in got["rgb"]:
            _c(checks, r, item, "unknown", want, got, "テロップの値を読めない")
        else:
            got["rgb"] = [round(x * 255, 1) for x in got["rgb"]]
            ok = (got["text"] == want["text"] and _near(got["size"], want["size"], 1e-3)
                  and all(_near(a, b, 1e-3) for a, b in zip(got["center"], want["center"]))
                  and (got["font"], got["style"]) == (want["font"], want["style"])
                  and all(_near(a, b, 1.0) for a, b in zip(got["rgb"], want["rgb"])))
            _c(checks, r, item, "pass" if ok else "fail", want, got, "テロップの文・Size・Center・書体・字の色 (0〜255)")
    bar_on = look.get("bar", True)
    if bar_on:
        bar, mask = _find(comp, "CaptionBar"), _find(comp, "CaptionBarMask")
        W, H = sb["timeline"]["width"], sb["timeline"]["height"]
        inset, bw = look.get("bar_inset", 0), look.get("border_width", 0)
        bwant = {"rgba": list(look["bar_rgba"]), "center": want["center"], "width": round(1 - 2 * (inset + bw) / W, 4),
                 "height": round(fit["bar_height"] - 2 * bw / H, 4), "corner": look.get("corner_radius") or 0}
        if not (bar and mask and bover) or any(_passed(x) for x in (bar, mask, bover)):
            _c(checks, r, prefix + "caption_bar", "fail", bwant, None,
               "テロップの背景の部品 (CaptionBar・CaptionBarMask・CaptionBarOver) が無いか、素通し")
        else:
            links = [_fed(bar, "EffectMask", "CaptionBarMask"), _fed(bover, "Foreground", "CaptionBar"), reaches(bover)]
            got = {"rgba": _rgba(bar),
                   "center": _xy(mask.GetInput("Center", 0)), "width": mask.GetInput("Width", 0),
                   "height": mask.GetInput("Height", 0), "corner": mask.GetInput("CornerRadius", 0)}
            links += window_links(comp, look, fit, W, H, bwant, got, row["frames"], _k(row))
            if None in links or None in got["rgba"] or None in got["center"] or None in (got["width"], got["height"]):
                _c(checks, r, prefix + "caption_bar", "unknown", bwant, got, "テロップの背景の値かつながりを読めない")
            else:
                got["rgba"] = [round(x * 255, 1) for x in got["rgba"]]
                got["つながり"] = all(links)
                ok = (all(links) and all(_near(a, b * 255, 1.0) for a, b in zip(got["rgba"], C.rgba01(bwant["rgba"])))
                      and all(_near(a, b, 1e-3) for a, b in zip(got["center"], bwant["center"]))
                      and _near(got["width"], bwant["width"], 1e-3) and _near(got["height"], bwant["height"], 1e-3)
                      and _near(got["corner"] or 0, bwant["corner"], 1e-3))
                _c(checks, r, prefix + "caption_bar", "pass" if ok else "fail", bwant, got,
                   "テロップの背景の色と不透明度 (0〜255)、四角の中心・幅・高さ (Fusion の座標)、"
                   "CaptionBar の EffectMask・CaptionBarOver の Foreground と MediaOut へのつながり")
    blends = {"CaptionOver": over} | ({"CaptionBarOver": bover} if bar_on and bover and not _passed(bover) else {})
    low = {name: _lowest(m, "Blend", row["frames"], _k(row)) for name, m in blends.items()}
    if any(v is None for v, _ in low.values()):
        _c(checks, r, prefix + "caption_time", "unknown", dict.fromkeys(blends, 1.0), None, "テロップの Merge の Blend を読めない")
        return
    _c(checks, r, prefix + "caption_time", "pass" if all(v > 0.999 for v, _ in low.values()) else "fail",
       dict.fromkeys(blends, 1.0), {name: {"最小": round(v, 4), "その合成の時刻": at} for name, (v, at) in low.items()},
       "行の全部でテロップが出ているか (Merge の Blend が行の頭と終わりとキーの時刻とその前後で 1 のまま)")


def check_hold(checks, sb, row, hit, head, W, H, near=None):
    """止めたクリップ: 位置と長さ、速さが 0 (Freeze Frame)、映す素材のフレーム (置いた範囲の最後。21.1.1 の実機)。

    W, H はそのタイムラインの幅と高さ。止めたクリップのマスクと focus の画素への換算に使う。
    near は組んだ記録で隣のフレームで止めた行 {行: 映すフレーム} (read_near)。
    """
    h = row["hold"]
    if not h:
        return
    r = row["row"]
    if h.get("unreadable"):
        _c(checks, r, "hold", "unknown", None, None, "hold の書式を読めない: " + h["unreadable"])
        return
    if hit is None:
        _c(checks, r, "hold", "fail", [h["start"], h["frames"]], None, "止めたクリップが無い")
        return
    mpi = hit.GetMediaPoolItem()
    sfps = C.source_fps(mpi, 0) if mpi else 0
    want = {"start": h["start"], "frames": h["frames"], "speed": 0.0,
            "src": C.hold_source_frame(h["spec"], sfps, row["frames"], _k(row)) if sfps else None}
    got = {"start": hit.GetStart() - head, "frames": hit.GetEnd() - hit.GetStart(),
           "speed": (hit.GetSpeed() or {}).get("Percentage"), "src": hit.GetSourceStartFrame()}
    if want["src"] is None or got["speed"] is None:
        _c(checks, r, "hold", "unknown", want, got, "素材のフレームレートか速さを読めない")
        return
    fp = mpi.GetClipProperty("File Path") if mpi else None
    same = bool(fp) and _key(fp) == _key(row["source"])
    # Resolve が止められないフレームでは、sb2resolve.py が隣のフレーム (±1) で止め、組んだ記録の hold に near を残す
    # (docs/scripting-api.md の「速さ」)。その記録があり、映すフレームが記録の got と同じときだけ、1 の違いを認める
    noted = (near or {}).get(r)
    by_near = (got["src"] is not None and got["src"] == noted and abs(got["src"] - want["src"]) == 1)
    ok = ((got["start"], got["frames"]) == (want["start"], want["frames"]) and (got["src"] == want["src"] or by_near)
          and _near(got["speed"], 0, 1e-6) and same)
    note = "止めたクリップの位置・長さ・速さ・映す素材のフレーム・素材のファイル"
    if ok and by_near:
        note += " (Resolve が止められないフレームの隣。sb2resolve.py の記録の near と同じ)"
    _c(checks, r, "hold", "pass" if ok else "fail", dict(want, file=os.path.basename(row["source"])),
       dict(got, file=os.path.basename(fp) if fp else None), note)
    if ok and by_near:
        checks[-1]["near"] = True
    comp = hit.GetFusionCompByIndex(1) if hit.GetFusionCompCount() else None
    # テロップは素材の解像度に関わらない (common.scene_values のテロップは構成表とタイムラインの寸法だけで決まる) ので先に見る
    # 重ねる Merge の Blend は止めたクリップの長さ (合成の時刻は等速) で読む
    still = dict(row, frames=h["frames"], speed=100.0)
    check_caption(checks, sb, still, comp, prefix="hold_")
    check_side(checks, sb, still, comp, prefix="hold_")
    check_point(checks, sb, still, comp, prefix="hold_")
    check_layout(checks, sb, still, comp, prefix="hold_")   # 素材の解像度を読めなければ unknown (中で見る)
    if row["size_unknown"]:
        return
    # 止めたクリップの Fusion (common.hold_values と同じ考え): マスクと focus の範囲・枠線は止めた瞬間の位置で動かない
    # (隠すものが止めた区間で出ないように)。暗くなる・枠線・callout の現れ方は、行の頭からの時刻のまま止めた区間へ続く
    # (止めたクリップの合成の時刻は等速で進む)。肩テロップは置かない
    k = _k(row)
    moment = C.hold_time(h["spec"], sfps, row["frames"], k) if sfps else 0
    fo, co = row["focus"], row["callout"]
    if fo and not fo.get("unreadable"):
        fo = dict(fo, keys=[], box=[round(v, 3) for v in expected_box(_in_comp(fo, k), moment)],
                  ramp_frames=C.shift_keys(fo["ramp_frames"], row["frames"]))
    if co and not co.get("unreadable"):
        co = dict(co, ramp_frames=C.shift_keys(co["ramp_frames"], row["frames"]))
    held = dict(row, frames=h["frames"], speed=100.0, focus=fo, callout=co,
                masks=[dict(m, keys=[], box=[round(v, 3) for v in expected_box(_in_comp(m, k), moment)])
                       for m in row["masks"] if not m.get("unreadable")])
    check_masks(checks, held, comp, W, H, prefix="hold_mask")
    if fo and not fo.get("unreadable"):
        check_focus(checks, held, comp, W, H, prefix="hold_")
    if co and not co.get("unreadable"):
        check_callout(checks, held, comp, prefix="hold_")


def check_point(checks, sb, row, comp, prefix=""):
    """指すマーク (名前 PointText・PointTilt・PointOver)。Fusion の値から指の先の行き先を計算し、指す点と比べる。

    - Text+ の Center と Size (fusion_text_scale で px に直す) と、config の point.glyphs の tip (字の中心から指の先までの
      ずれ) から、傾ける前の指の先を出す。それを PointTilt の Pivot を軸に Angle (左回りが正) だけ回した点が、構成表の
      x y (素材の画素をタイムラインの画素に直した点) から 1 px 以内。PointTilt は動かさず大きさも変えない (Center 0.5・Size 1)
    - PointText の Size が point.size ÷ ズームの終わりの倍率 ÷ (タイムラインの幅 × fusion_text_scale) (expected_rows で計算)
    - Angle の大きさが tilt で、向きは手の側が見えている範囲 (ズームの終わりの範囲) の真ん中へ寄る向き。字は from に合うマーク
    - PointText が PointTilt の Input に、PointTilt が PointOver の Foreground につながり、PointOver が MediaOut に届く。
      PointOver の Blend は行の全部で 1 のまま (下げるとマークが薄くなるか消える)
    - layout の行: マークは画面と一緒に縮めて置く (PointTilt → LayoutPoint → PointOver の Foreground、PointOver の Background は
      LayoutScreen)。上の「ズームの終わりの倍率」は comp の画を出力に置く倍率 (layout_expect の end)、「見えている範囲の
      真ん中」は画面の真ん中。LayoutPoint で動かした指の先が、画面が出力に映る四角の中の指す点から 1 px 以内。
      LayoutPoint の Size が画面を置く倍率 (layout_expect の screen_size)
    prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    pt, item = row["point"], prefix + "point"
    if not pt:
        return
    if pt.get("unreadable"):   # 書式を読めない point は、読めた所 (指す点・字) だけを期待の値に出す
        want = {k: [round(v, 1) for v in pt[k]] if k == "target" else pt[k] for k in ("target", "glyph") if k in pt}
        _c(checks, row["row"], item, "unknown", want or None, None, pt["unreadable"])
        return
    want = {"target": [round(v, 1) for v in pt["target"]], "glyph": pt["glyph"], "font": pt["font"], "style": pt["style"]}
    if row["size_unknown"]:
        _c(checks, row["row"], item, "unknown", want, None, "素材の解像度を読めない (指す点を直せない)")
        return
    t, tilt, over = _find(comp, "PointText"), _find(comp, "PointTilt"), _find(comp, "PointOver")
    if not (t and tilt and over) or any(_passed(x) for x in (t, tilt, over)):
        _c(checks, row["row"], item, "fail", want, None, "指すマークの部品 (PointText・PointTilt・PointOver) が無いか、素通し")
        return
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    lp = None
    if "out_target" in pt:   # layout の行: PointTilt → LayoutPoint → PointOver の Foreground、PointOver の Background は画面
        lp = _find(comp, "LayoutPoint")
        if not lp or _passed(lp):
            _c(checks, row["row"], item, "fail", want, None, "layout の行の指すマークを画面と一緒に置く部品 (LayoutPoint) が無いか、素通し")
            return
    links = [_fed(tilt, "Input", "PointText"), _fed(over, "Foreground", "LayoutPoint" if lp else "PointTilt"), reaches(over),
             _shown(over, row["frames"], _k(row))]
    if lp:
        links[:0] = [_fed(lp, "Input", "PointTilt"), _fed(over, "Background", "LayoutScreen")]
        lp_center, lp_pivot = _xy(lp.GetInput("Center", 0)), _xy(lp.GetInput("Pivot", 0))
        lp_size, lp_angle = lp.GetInput("Size", 0), lp.GetInput("Angle", 0)
        if None in lp_center or None in lp_pivot or None in (lp_size, lp_angle):
            _c(checks, row["row"], item, "unknown", want, None, "LayoutPoint の値を読めない")
            return
    text, size, center = t.GetInput("StyledText", 0), t.GetInput("Size", 0), _xy(t.GetInput("Center", 0))
    pivot, angle = _xy(tilt.GetInput("Pivot", 0)), tilt.GetInput("Angle", 0)
    move, scale = _xy(tilt.GetInput("Center", 0)), tilt.GetInput("Size", 0)
    font, style = t.GetInput("Font", 0), t.GetInput("Style", 0)
    if (None in links or None in (text, size, angle, scale, font, style) or None in center or None in pivot
            or None in move):
        _c(checks, row["row"], item, "unknown", want, None, "指すマークの値かつながりを読めない")
        return
    px = size * W * sb["fusion_text_scale"]
    dx = center[0] * W + pt["tip"][0] * px - pivot[0] * W
    dy = (1 - center[1]) * H + pt["tip"][1] * px - (1 - pivot[1]) * H
    a = math.radians(angle)
    tip = (pivot[0] * W + dx * math.cos(a) + dy * math.sin(a), (1 - pivot[1]) * H - dx * math.sin(a) + dy * math.cos(a))
    # 手の側 (指す向きの逆) を左回りに少し回したときの動き (下が正の画素で (b_y, -b_x)) が、見えている範囲の真ん中を向くなら左回り
    bx, by = {"up": (0, 1), "down": (0, -1), "right": (-1, 0), "left": (1, 0)}[pt["way"]]
    tx, ty = pt["target"]
    vx, vy = pt["view"]
    lean = by * (vx - tx) - bx * (vy - ty)
    want["angle"] = pt["tilt"] * (-1 if lean < 0 else 1)
    want |= {"size": round(pt["fusion_size"], 6), "tilt_center": [0.5, 0.5], "tilt_size": 1.0,
             "PointOver の Blend が 1 のまま": True}
    got = {"target": [round(v, 1) for v in tip], "glyph": text, "font": font, "style": style, "angle": angle, "size": size,
           "tilt_center": list(move), "tilt_size": scale, "つながり": all(links[:-1]), "PointOver の Blend が 1 のまま": links[-1]}
    # Size も比べる (Size を 0 にして Center を指す点に置くと、指の先の計算は合ってもマークは見えない)
    ok = (all(links) and text == pt["glyph"] and (font, style) == (pt["font"], pt["style"])
          and _near(size, pt["fusion_size"], 1e-4) and _near(angle, want["angle"], 1e-6)
          and all(_near(v, 0.5, 1e-6) for v in move) and _near(scale, 1.0, 1e-6)
          and math.hypot(tip[0] - tx, tip[1] - ty) <= 1.0)
    note = ("指の先の行き先 (Fusion の値から計算、タイムラインの画素、1 px まで)・字・字の大きさ (Size)・書体 (Font と Style)・"
            "傾き (度、左回りが正)・MediaOut へのつながり・PointOver の Blend が行の全部で 1 のままか")
    if lp:
        # LayoutPoint (Transform) は、画面を 1 とした点 p を Center + Size × (p − 0.5) に動かす (Pivot が真ん中で、回さないとき)。
        # 指の先をこの式で動かした点が、画面が出力に映る四角の中の指す点 (expected_rows の out_target) から 1 px 以内
        ox = (lp_center[0] + lp_size * (tip[0] / W - 0.5)) * W
        oy = (1 - (lp_center[1] + lp_size * ((1 - tip[1] / H) - 0.5))) * H
        # LayoutPoint の Size は画面を置く倍率 (layout_expect の screen_size)。指の先が合っても Size が違えば、マークは
        # point.size px と違う大きさに映る。comp の大きさの丸め (組む側と 1 画素違いうる) を見込んで 0.1 % まで
        want |= {"out_target": [round(v, 1) for v in pt["out_target"]], "layout_point_pivot": [0.5, 0.5], "layout_point_angle": 0.0,
                 "layout_point_size": round(pt["layout_size"], 6)}
        got |= {"out_target": [round(ox, 1), round(oy, 1)], "layout_point_pivot": list(lp_pivot), "layout_point_angle": lp_angle,
                "layout_point_size": lp_size}
        ok = (ok and all(_near(v, 0.5, 1e-6) for v in lp_pivot) and _near(lp_angle, 0.0, 1e-6)
              and _near(lp_size, pt["layout_size"], 1e-3 * pt["layout_size"])
              and math.hypot(ox - pt["out_target"][0], oy - pt["out_target"][1]) <= 1.0)
        note += ("・layout の行は、LayoutPoint で動かした指の先が出力の指す点 (画面を置く四角の中、1 px まで) に来るか、"
                 "LayoutPoint の Size が画面を置く倍率か (0.1 % まで)、Pivot が真ん中で回していないか、"
                 "PointOver の Background が LayoutScreen か")
    _c(checks, row["row"], item, "pass" if ok else "fail", want, got, note)


LAYOUT_TOOLS = ("LayoutBase", "LayoutBackCrop", "LayoutBackBlur", "LayoutBack", "LayoutBackShade", "LayoutBackDim",
                "LayoutScreenCrop", "LayoutScreen")
# 行のマスクではない、名前の付いたマスクの部品 (テロップの板・肩テロップ・解説の欄・focus など) の名前の頭
NOT_ROW_MASK = ("Focus", "Caption", "Closing", "Side", "Badge", "Callout", "Layout", "Point")


CROP_KEYS = ("XOffset", "YOffset", "XSize", "YSize")   # Crop の値の名前 (layout の切り抜きと callout)


def _layout_screen(checks, r, item, L, tools, n, k, W, H):
    """layout: 画面の切り抜き (LayoutScreenCrop) と、LayoutScreen の Center と Size から計算した画面が出力に映る四角、
    FilterMethod、LayoutScreen の Blend。"""
    cw, ch = L["comp"]
    scr, sc_crop = tools["LayoutScreen"], tools["LayoutScreenCrop"]
    crop = [sc_crop.GetInput(c, 0) for c in CROP_KEYS]
    center, size, filt = _xy(scr.GetInput("Center", 0)), scr.GetInput("Size", 0), scr.GetInput("FilterMethod", 0)
    shown = _shown(scr, n, k)
    want = {"crop": [round(v, 3) for v in L["crop"]], "rect": [round(v, 1) for v in L["rect"]], "filter": L["filter"],
            "LayoutScreen の Blend が 1 のまま": True}
    if None in crop or None in center or None in (size, filt, shown):
        _c(checks, r, item, "unknown", want, None, "LayoutScreenCrop か LayoutScreen の値を読めない")
        return
    # Merge は前景 (切り抜いた画面、comp の画素) を Size 倍して Center に置く。comp の 1 画素は出力の W / cw 画素
    w, h = crop[2] * size * W / cw, crop[3] * size * H / ch
    got_rect = [center[0] * W - w / 2, (1 - center[1]) * H - h / 2, w, h]
    ok = (all(_near(a, b, TOL_PX) for a, b in zip(crop, L["crop"]))
          and all(_near(a, b, 1.0) for a, b in zip(got_rect, L["rect"])) and filt == L["filter"] and shown)
    _c(checks, r, item, "pass" if ok else "fail", want,
       {"crop": crop, "rect": [round(v, 1) for v in got_rect], "center": list(center), "size": size, "filter": filt,
        "LayoutScreen の Blend が 1 のまま": shown},
       "画面の切り抜き (comp の画素、YOffset は下から、0.5 まで)・画面が出力に映る四角 (Center と Size から計算、1 px まで)・"
       "FilterMethod・LayoutScreen の Blend が行の全部で 1 のままか")


def _layout_back(checks, r, item, L, tools, n, k):
    """layout_back: 背景 (画面を広げてぼかしたもの)。切り抜き・ぼかし・LayoutBack の置き方・下の黒 (LayoutBase)・Blend。"""
    cw, ch = L["comp"]
    base, bcrop, blur, back = tools["LayoutBase"], tools["LayoutBackCrop"], tools["LayoutBackBlur"], tools["LayoutBack"]
    got = {"crop": [bcrop.GetInput(c, 0) for c in CROP_KEYS], "blur": blur.GetInput("XBlurSize", 0),
           "center": list(_xy(back.GetInput("Center", 0))), "size": back.GetInput("Size", 0),
           "filter": back.GetInput("FilterMethod", 0), "base_rgba": _rgba(base),
           "LayoutBack の Blend が 1 のまま": _shown(back, n, k)}
    want = {"crop": [round(v, 3) for v in L["crop"]], "blur": L["blur"], "center": [0.5, 0.5], "size": round(L["back_size"], 6),
            "filter": L["filter"], "base_rgba": [0.0, 0.0, 0.0, 1.0], "LayoutBack の Blend が 1 のまま": True}
    if L["canvas"]:   # 比が違う素材では、単色は comp の大きさで描く (既定は素材の大きさ)
        got["base_size"] = [base.GetInput("Width", 0), base.GetInput("Height", 0)]
        want["base_size"] = [cw, ch]
    if _has_none(got):
        _c(checks, r, item, "unknown", want, got, "背景の部品の値を読めない")
        return
    ok = (all(_near(a, b, TOL_PX) for a, b in zip(got["crop"], L["crop"])) and _near(got["blur"], L["blur"], 1e-6)
          and all(_near(v, 0.5, 1e-6) for v in got["center"]) and _near(got["size"], L["back_size"], 1e-3)
          and got["filter"] == L["filter"] and all(_near(a, b, 1e-6) for a, b in zip(got["base_rgba"], want["base_rgba"]))
          and got["LayoutBack の Blend が 1 のまま"]
          and (not L["canvas"] or all(_near(a, b, 0.5) for a, b in zip(got["base_size"], want["base_size"]))))
    _c(checks, r, item, "pass" if ok else "fail", want, got,
       "背景の切り抜き (画面と同じ四角)・ぼかし (XBlurSize)・真ん中に comp の全体を覆う倍率で置くか・FilterMethod・"
       "下の黒が不透明か (比が違う素材では大きさも)・LayoutBack の Blend が行の全部で 1 のままか")


def _layout_dim(checks, r, item, L, tools, n, k):
    """layout_dim: 背景を暗くする黒 (LayoutBackShade) の不透明度と色、LayoutBackDim の Blend。"""
    cw, ch = L["comp"]
    shade, dimmed = tools["LayoutBackShade"], tools["LayoutBackDim"]
    got = {"shade_rgba": _rgba(shade), "LayoutBackDim の Blend が 1 のまま": _shown(dimmed, n, k)}
    want = {"shade_rgba": [0.0, 0.0, 0.0, round(1 - L["dim"], 6)], "dim": L["dim"], "LayoutBackDim の Blend が 1 のまま": True}
    if L["canvas"]:
        got["shade_size"] = [shade.GetInput("Width", 0), shade.GetInput("Height", 0)]
        want["shade_size"] = [cw, ch]
    if _has_none(got):
        _c(checks, r, item, "unknown", want, got, "暗くする黒 (LayoutBackShade) か LayoutBackDim の値を読めない")
        return
    ok = (all(_near(a, b, 1e-4) for a, b in zip(got["shade_rgba"], [0.0, 0.0, 0.0, 1 - L["dim"]]))
          and got["LayoutBackDim の Blend が 1 のまま"]
          and (not L["canvas"] or all(_near(a, b, 0.5) for a, b in zip(got["shade_size"], want["shade_size"]))))
    _c(checks, r, item, "pass" if ok else "fail", want, got,
       "黒の不透明度 1 − dim (背景の明るさは dim 倍)・色は 0・LayoutBackDim の Blend が行の全部で 1 のままか")


def _layout_links(checks, row, item, comp, tools):
    """layout_links: 部品のつながり、MediaOut に届くか、MediaIn1 が両方の切り抜きに届くか、行のマスクの効果が両方に届くか、
    focus の暗さが画面にだけ届くか、ズームの Transform が無いか。"""
    r = row["row"]
    scr, blur, back, dimmed = tools["LayoutScreen"], tools["LayoutBackBlur"], tools["LayoutBack"], tools["LayoutBackDim"]
    links = {"LayoutBackBlur ← LayoutBackCrop": _fed(blur, "Input", "LayoutBackCrop"),
             "LayoutBack の Background ← LayoutBase": _fed(back, "Background", "LayoutBase"),
             "LayoutBack の Foreground ← LayoutBackBlur": _fed(back, "Foreground", "LayoutBackBlur"),
             "LayoutBackDim の Background ← LayoutBack": _fed(dimmed, "Background", "LayoutBack"),
             "LayoutBackDim の Foreground ← LayoutBackShade": _fed(dimmed, "Foreground", "LayoutBackShade"),
             "LayoutScreen の Background ← LayoutBackDim": _fed(scr, "Background", "LayoutBackDim"),
             "LayoutScreen の Foreground ← LayoutScreenCrop": _fed(scr, "Foreground", "LayoutScreenCrop"),
             "LayoutScreen → MediaOut": reaches(scr)}
    # 素材の入口 (MediaIn1。組む側の build_fusion が使う合成の既定の部品) の画が、画の入力だけを通って両方の切り抜きの Input に届く
    mi = _find(comp, "MediaIn1")
    for dst in ("LayoutBackCrop", "LayoutScreenCrop"):
        links[f"MediaIn1 → {dst} の Input"] = _reaches_input(mi, tools[dst], "Input")
    # 行のマスク (隠す範囲) の効果が、背景と画面の両方の切り抜きより前にある。背景だけ前の画から切ると、隠したものがぼけて映る
    if row["masks"]:
        masks = [t for t in (comp.GetToolList(False) or {}).values()
                 if t.ID in C.MASK_SHAPES.values() and not _passed(t)
                 and not str(getattr(t, "Name", "") or "").startswith(NOT_ROW_MASK)]
        for m in masks:
            for dst in ("LayoutBackCrop", "LayoutScreenCrop"):
                links[f"{m.Name} の効果 → {dst}"] = reaches(m, dst, "Mask")
    # focus の暗さと枠線は画面にだけ掛かる (背景に入れると余白まで暗くなる)
    fo = _find(comp, "FocusDim") if row["focus"] else None
    if fo is not None and not _passed(fo):
        links["FocusDim → LayoutScreenCrop"] = reaches(fo, "LayoutScreenCrop")
        to_back = reaches(fo, "LayoutBackCrop")
        links["FocusDim が LayoutBackCrop に届かない"] = None if to_back is None else not to_back
    xfs = [t for t in _tools(comp, "Transform") if getattr(t, "Name", None) not in ("PointTilt", "LayoutPoint")]
    live = [t for t in xfs if _connected(t, "Output")]
    links["ズームの Transform が無い"] = not live if all(_connected(t, "Output") is not None for t in xfs) else None
    if None in links.values():
        _c(checks, r, item, "unknown", True, links, "layout の部品のつながりを読めない")
        return
    bad = [name for name, v in links.items() if not v]
    _c(checks, r, item, "fail" if bad else "pass", True, links,
       "つながりが違う: " + "、".join(bad) if bad else
       "背景と画面の部品のつながり・MediaOut に届くか・MediaIn1 が両方の切り抜きの Input に届くか・行のマスクの効果が背景と画面の両方に届くか・"
       "focus の暗さが画面にだけ届くか・ズームの Transform が無いか")


def check_layout(checks, sb, row, comp, prefix=""):
    """layout (画面を縮めて寄せ、余白を、画面を広げてぼかし暗くしたもので埋める。名前 Layout* の部品)。

    - layout (_layout_screen): 画面の切り抜き (LayoutScreenCrop の XOffset・YOffset・XSize・YSize。comp の画素で 0.5 まで) と、
      LayoutScreen (Merge) の Center と Size から計算した、画面が出力に映る四角 (出力の画素で 1 px まで)。FilterMethod、
      LayoutScreen の Blend が行の全部で 1 のまま
    - layout_back (_layout_back): 背景の切り抜き (LayoutBackCrop) が画面と同じ四角、ぼかし (LayoutBackBlur の XBlurSize) が background.blur、
      LayoutBack (Merge) が真ん中 (Center 0.5) に comp の全体を覆う倍率 (Size) で置き、FilterMethod が同じ。下の黒
      (LayoutBase) が不透明。比が違う素材では LayoutBase の大きさが comp の大きさ。LayoutBack の Blend が 1 のまま
    - layout_dim (_layout_dim): 暗くする黒 (LayoutBackShade) の不透明度が 1 − background.dim (色は 0)。比が違う素材では大きさが comp。
      LayoutBackDim の Blend が 1 のまま
    - layout_links (_layout_links): 部品のつながり (Crop → Blur → LayoutBack の Foreground、LayoutBase → Background、LayoutBack と
      LayoutBackShade → LayoutBackDim、LayoutBackDim と LayoutScreenCrop → LayoutScreen)、LayoutScreen が MediaOut に届く、
      素材の入口 (MediaIn1) の出力が背景と画面の両方の切り抜きの Input に届く (Canvas・マスク・focus を通ってよい。途中の部品も
      画の入力 IMAGE_INPUTS に入るつながりだけをたどり、マスクの入力にだけつながる道は数えない。切り離すと切り抜きに素材が
      入らない)、行のマスクの効果が背景と画面の両方の切り抜きに届く (隠したものが背景から透けない)、focus の暗さは画面の
      切り抜きにだけ届く、ズームの Transform が無い
    layout の部品のどれかが無いか素通しなら layout を fail にして、ほかの項目は出さない。
    prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    L, r = row.get("layout"), row["row"]
    if not L:
        return
    item, back_item, dim_item, link_item = (prefix + x for x in ("layout", "layout_back", "layout_dim", "layout_links"))
    if L.get("unreadable"):
        _c(checks, r, item, "unknown", None, None, L["unreadable"])
        return
    rect = [round(v, 1) for v in L["rect"]]
    if row["size_unknown"]:
        _c(checks, r, item, "unknown", {"rect": rect}, None, "素材の解像度を読めない (画面を切り抜く四角を直せない)")
        return
    tools = {n: _find(comp, n) for n in LAYOUT_TOOLS}
    gone = [n for n, t in tools.items() if t is None or _passed(t)]
    if gone:
        _c(checks, r, item, "fail", {"rect": rect}, None, f"layout の部品 ({'・'.join(gone)}) が無いか、素通し")
        return
    n, k = row["frames"], _k(row)
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    _layout_screen(checks, r, item, L, tools, n, k, W, H)
    _layout_back(checks, r, back_item, L, tools, n, k)
    _layout_dim(checks, r, dim_item, L, tools, n, k)
    _layout_links(checks, row, link_item, comp, tools)


def check_side(checks, sb, row, comp, prefix=""):
    """解説の欄 (欄ごとに SideBorderN・SidePanelN・SideTitleN・SideRuleN・SideTextN と、それぞれの *OverN)。

    - 欄を出す余白は行の layout の期待の値 (layout_expect の side_area。top-right は画面の左、ほかは右)。どの欄の縁
      (SideBorderNMask) も左右が余白の左右に合い (1 px まで)、上から重ならずに並び、いちばん上の欄の上の端が余白の上の端
      (top-right は margin、top-left と top は肩テロップの下) に、いちばん下の欄の下の端が画面の下の端に合う
    - 中身と見出しの文が構成表どおり (行の side が辞書なら、全体の欄のその見出しの中身だけを差し替えた形。side_expect)。2 行以上 (または align left) の中身は左寄せ (HorizontalLeftCenterRight -1) で、
      行の左の端 (Center) が縁の左から border_width + pad + indent の所。それ以外は欄の真ん中
    - 縁・板・見出し・区切りの線・中身が、それぞれの *OverN の Foreground につながって MediaOut に届き、縁と板は自分のマスクで切る
    prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    side, item = row["side"], prefix + "side"
    if not side:
        return
    sd = sb["side"]
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    # 読めない書式は unknown にする (組む側の common.side_sections・merge_side・side_values が ValueError にするもの)
    if isinstance(side, dict) and "unreadable" in side:   # 行の side の辞書 (見出しごとの差し替え) の誤り (side_expect)
        _c(checks, row["row"], item, "unknown", None, None, f"side の書式を読めない: {side['unreadable']}")
        return
    try:
        secs = C.side_sections(side)
    except ValueError as ex:
        _c(checks, row["row"], item, "unknown", None, None, f"side の書式を読めない: {ex}")
        return
    L = row.get("layout")
    if not L or L.get("unreadable"):
        _c(checks, row["row"], item, "unknown", None, None,
           "side の書式を読めない: side は layout (screen の w h・margin・height・anchor) と一緒に使う"
           + (f" ({L['unreadable']})" if L else ""))
        return
    area = L["side_area"]
    want = {"area": [round(v, 1) for v in area],
            "texts": ["\n".join(s["lines"]) for s in secs], "titles": [s["title"] for s in secs]}
    got, bad, prev, sizes = {"boxes": [], "texts": [], "titles": []}, [], None, []
    for i, s in enumerate(secs, 1):
        parts = ("Border", "Panel", "Text") + (("Title", "Rule") if s["title"] else ())
        tools = {k: _find(comp, f"Side{k}{i}") for k in parts} | {k + "Over": _find(comp, f"Side{k}Over{i}") for k in parts}
        tools |= {"BorderMask": _find(comp, f"SideBorder{i}Mask")}
        if any(v is None or _passed(v) for v in tools.values()):
            _c(checks, row["row"], item, "fail", want, None, f"{i} 番目の欄の部品が無いか、素通し")
            return
        m, t = tools["BorderMask"], tools["Text"]
        c, bw_, bh_ = _xy(m.GetInput("Center", 0)), m.GetInput("Width", 0), m.GetInput("Height", 0)
        tc, lcr = _xy(t.GetInput("Center", 0)), t.GetInput("HorizontalLeftCenterRight", 0)
        # どの部品も自分の Over の Foreground につながって MediaOut に届く。縁と板は自分のマスクで切る
        links = [x for k in parts for x in (_fed(tools[k + "Over"], "Foreground", f"Side{k}{i}"), reaches(tools[k + "Over"]))]
        links += [_fed(tools["Border"], "EffectMask", f"SideBorder{i}Mask"), _fed(tools["Panel"], "EffectMask", f"SidePanel{i}Mask")]
        # どの *OverN の Blend も行の全部で 1 のまま (下げると、つながっていても欄の部品が薄くなるか消える)
        shown = {f"Side{k}Over{i}": _shown(tools[k + "Over"], row["frames"], _k(row)) for k in parts}
        if None in c or None in tc or None in (bw_, bh_, lcr) or None in links or None in shown.values():
            _c(checks, row["row"], item, "unknown", want, None, f"{i} 番目の欄の値かつながりを読めない")
            return
        box = [(c[0] - bw_ / 2) * W, (1 - c[1] - bh_ / 2) * H, (c[0] + bw_ / 2) * W, (1 - c[1] + bh_ / 2) * H]
        got["boxes"].append([round(v, 1) for v in box])
        got["texts"].append(t.GetInput("StyledText", 0))
        got["titles"].append(tools["Title"].GetInput("StyledText", 0) if s["title"] else None)
        sizes.append(t.GetInput("Size", 0))
        if not (_near(box[0], area[0], 1.0) and _near(box[2], area[2], 1.0)):
            bad.append(f"{i} 番目の欄の左右が余白に合わない")
        if prev is not None and box[1] < prev - 0.5:
            bad.append(f"{i} 番目の欄が上の欄に重なる")
        if not all(links):
            bad.append(f"{i} 番目の欄の部品のつながりが違う (Over の Foreground・マスク・MediaOut)")
        thin = [name for name, v in shown.items() if not v]
        if thin:
            bad.append(f"{i} 番目の欄の Blend が行の中で 1 より下がる ({'・'.join(thin)})")
        if s["align"] == "left":
            ok = lcr == -1 and _near(tc[0] * W, box[0] + sd["border_width"] + sd["pad"] + sd["indent"], 1.0)
        else:
            ok = lcr == 0 and _near(tc[0] * W, (box[0] + box[2]) / 2, 1.0)
        if not ok:
            bad.append(f"{i} 番目の中身の寄せ方 ({s['align']}) が違う")
        prev = box[3]
    first, last = got["boxes"][0], got["boxes"][-1]
    if not _near(first[1], area[1], 1.0):
        bad.append("いちばん上の欄の上の端が余白の上の端 (top-right は margin、ほかは肩テロップの下) に合わない")
    if not _near(last[3], area[3], 1.0):
        bad.append("いちばん下の欄の下の端が画面の下の端に合わない")
    if got["texts"] != want["texts"] or got["titles"] != want["titles"]:
        bad.append("見出しか中身の文が違う")
    _c(checks, row["row"], item, "fail" if bad else "pass", want, got, "、".join(bad) if bad else
       "欄の縁の位置 (余白の左右と上下)・並び・見出しと中身の文・中身の寄せ方・MediaOut へのつながり・"
       "*OverN の Blend が行の全部で 1 のままか")
    # 中身の字の大きさ (SideTextN の Size) を、欄ごとに幅に収まる大きさ (side_size_expect) と比べる
    px = side_size_expect(sb, secs, area[2] - area[0])
    fts = W * float(sb["fusion_text_scale"])
    if px is None:
        _c(checks, row["row"], prefix + "side_size", "unknown", None, sizes,
           "書体のファイルか Pillow が無く、解説の欄の文の幅を測れない")
        return
    want_size = [round(p / fts, 6) for p in px]
    if None in sizes:
        _c(checks, row["row"], prefix + "side_size", "unknown", want_size, sizes, "SideTextN の Size を読めない")
        return
    off = [i for i, (g, p) in enumerate(zip(sizes, px), 1) if not _near(g, p / fts, p / fts * 0.001)]
    _c(checks, row["row"], prefix + "side_size", "fail" if off else "pass", want_size, [round(g, 6) for g in sizes],
       f"{'・'.join(map(str, off))} 番目の欄の中身の字の大きさが違う" if off else
       "欄ごとの中身の字の大きさ (いちばん長い行が欄の幅に収まる大きさ。side.size から side.min_size まで)")


def side_size_expect(sb, secs, width):
    """解説の欄ごとの中身の字の大きさ (px) の期待。組む側 (common.side_body_sizes) を呼ばずに同じ決め方で計算する。
    使える幅は width - 2 × (border_width + pad)、左寄せの欄はさらに indent を引く。いちばん長い行を side.size で測って
    side.width_scale (Fusion が Pillow の見積もりより広く描く分) を掛け、収まれば side.size。超えれば size × 使える幅 ÷ 測った幅 を 0.5 px 刻みに切り下げ、side.min_size (size より大きければ size)
    を下限にする。書体のファイルか Pillow が無くて測れなければ None。"""
    sd = sb["side"]
    cap = C.caption_look(sb)
    font, style = sd.get("font_name") or cap["font_name"], sd.get("font_style") or cap["font_style"]
    ff = C.config().font_file(font, style)
    size = float(sd["size"])
    low = min(float(sd.get("min_size") or size), size)
    if not ff or C.text_width(ff, size, "あ") is None:
        return None
    inner = width - 2 * (float(sd["border_width"]) + float(sd["pad"]))
    k = float(sd.get("width_scale") or 1.0)
    out = []
    for s in secs:
        room = inner - (float(sd["indent"]) if s["align"] == "left" else 0)
        widest = k * max(C.text_width(ff, size, x) for x in s["lines"])
        out.append(size if widest <= room else max(math.floor(size * room / widest * 2) / 2, low))
    return out


def check_callout(checks, row, comp, prefix=""):
    """callout (切り抜きごとに、名前 Callout の Merge と CalloutCrop。2 つ目からは Callout2・CalloutCrop2 と番号付き):
    切り抜く範囲 (素材の画素)、置く位置 (Center) と拡大 (Size)、
    現れ方 (Merge の Blend。現れ終わりの次まで書き出しのフレームごと、その後は行の終わりまで 1 のまま)、出力が MediaOut に届くこと。
    読めなければ unknown。prefix は項目の名前の前に付ける (止めたクリップは hold_)。"""
    c = row["callout"]
    if not c:
        return
    r = row["row"]
    if c.get("unreadable"):
        _c(checks, r, prefix + "callout", "unknown", None, None, "callout の書式を読めない: " + c["unreadable"])
        return
    if row["size_unknown"]:
        _c(checks, r, prefix + "callout", "unknown", c["dst"], None, "素材の解像度を読めない (置き場所を直せない)")
        return
    k = _k(row)
    for i, piece in enumerate(c["pieces"], 1):
        n = "" if i == 1 else str(i)
        item, time_item = ("callout", "callout_time") if i == 1 else (f"callout_{i}", f"callout_time_{i}")
        item, time_item = prefix + item, prefix + time_item
        mg, crop = _find(comp, "Callout" + n), _find(comp, "CalloutCrop" + n)
        if not (mg and crop) or _passed(mg) or _passed(crop):
            _c(checks, r, item, "fail", piece["dst"], None, f"callout の部品 (Callout{n}・CalloutCrop{n}) が無いか、素通し")
            continue
        if not _reached(checks, r, item, mg, piece["dst"], f"Callout{n} の出力が MediaOut まで届かない"):
            continue
        used = _among(_targets(crop, "Output"), mg, "Callout" + n)
        if not used:
            _c(checks, r, item, "unknown" if used is None else "fail", piece["dst"], None,
               f"CalloutCrop{n} のつながりを読めない" if used is None
               else f"CalloutCrop{n} が Callout{n} につながっていない (別の画を重ねている)")
            continue
        got = {"crop": [crop.GetInput(x, 0) for x in CROP_KEYS],
               "center": _xy(mg.GetInput("Center", 0)), "size": mg.GetInput("Size", 0)}
        if None in got["crop"] or None in got["center"] or got["size"] is None:
            _c(checks, r, item, "unknown", piece["dst"], got, "callout の値を読めない")
            continue
        ok = (all(_near(a, b, TOL_PX) for a, b in zip(got["crop"], piece["crop"])) and _near(got["size"], c["size"], 1e-3)
              and all(_near(a, b, 1e-3) for a, b in zip(got["center"], piece["center"])))
        _c(checks, r, item, "pass" if ok else "fail",
           {"crop": list(piece["crop"]), "center": piece["center"], "size": round(c["size"], 4), "dst": piece["dst"]}, got,
           "切り抜く範囲 (素材の画素、YOffset は下から)、置く位置 (Center) と拡大 (Size)")
        ok, got = _ramp_ok(mg, "Blend", c["ramp_frames"], 1.0, row["frames"], k)
        if ok is None:
            _c(checks, r, time_item, "unknown", c["ramp_frames"], None, f"Callout{n} の Blend を読めない")
            continue
        _c(checks, r, time_item, "pass" if ok else "fail", c["ramp_frames"], got,
           "現れる割合 (行の頭からの書き出しのフレームごと。キーの間も読む)")


def check_timeline(sb, exp, tl, near=None):
    checks = []
    fps = float(sb["fps"])
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    want = {"timelineFrameRate": fps, "timelineResolutionWidth": W, "timelineResolutionHeight": H}
    got = {k: tl.GetSetting(k) for k in want}
    if not all(got.values()):
        status = "unknown"
    else:
        status = "pass" if all(_near(got[k], v, 1e-3) for k, v in want.items()) else "fail"
    _c(checks, None, "timeline_settings", status, want, got)
    items = sorted(tl.GetItemListInTrack("video", 1) or [], key=lambda x: x.GetStart())
    head = tl.GetStartFrame()
    n_want = len(exp) + sum(1 for r in exp if r["hold"])
    _c(checks, None, "items", "pass" if len(items) == n_want else "fail", n_want, len(items),
       "映像のトラック 1 のクリップの数 (行と、止めたクリップ)")
    # 並んだ順に、行のクリップ、行に hold があればその後ろの止めたクリップ、と対応させる
    pairs, rest = [], list(items)
    for row in exp:
        if not rest:
            break
        it = rest.pop(0)
        hit = rest.pop(0) if row["hold"] and rest else None
        pairs.append((row, it, hit))
    for row, it, hit in pairs:
        check_hold(checks, sb, row, hit, head, W, H, near)
        r = row["row"]
        pos = (it.GetStart() - head, it.GetEnd() - it.GetStart())
        _c(checks, r, "position", "pass" if pos == (row["start"], row["frames"]) else "fail",
           [row["start"], row["frames"]], list(pos), "始まりのフレーム, フレーム数")
        mpi = it.GetMediaPoolItem()
        sfps = C.source_fps(mpi, 0) if mpi else 0   # 読めなければ 0 (タイムラインの値で代えない)
        got_src = [it.GetSourceStartFrame(), it.GetSourceEndFrame()]
        if sfps:
            want_src = [round(row["src_start"] * sfps), round(row["src_end"] * sfps)]
            # 割り切れない倍速では素材側が 1 フレームずれることがあり、素材の最後まで届くクリップは GetSourceEndFrame が
            # 1〜2 少なく出る (docs/scripting-api.md)。始まりと終わりは 1 フレーム、右の余りが 0 なら終わりは 2 フレームまで許す
            try:
                end_tol = 2 if it.GetRightOffset() == 0 else 1
            except Exception:
                end_tol = 1
            ok = abs(got_src[0] - want_src[0]) <= 1 and abs(got_src[1] - want_src[1]) <= end_tol
            _c(checks, r, "source_range", "pass" if ok else "fail", want_src, got_src,
               "素材のフレーム (始まり 1、終わり 1 か 2 フレームまでの差を許す)")
        else:
            _c(checks, r, "source_range", "unknown", None, got_src, "素材のフレームレートを読めない")
        sp = (it.GetSpeed() or {}).get("Percentage")
        _c(checks, r, "speed", "pass" if _near(sp, row["speed"], TOL_SPEED) else ("unknown" if sp is None else "fail"),
           round(row["speed"], 2), sp)
        fp = mpi.GetClipProperty("File Path") if mpi else None
        same = bool(fp) and _key(fp) == _key(row["source"])
        _c(checks, r, "source_file", "pass" if same else ("unknown" if not fp else "fail"),
           os.path.basename(row["source"]), os.path.basename(fp) if fp else None)
        comp = it.GetFusionCompByIndex(1) if it.GetFusionCompCount() else None
        check_badge(checks, row, comp)
        check_zoom(checks, row, comp)
        check_masks(checks, row, comp, W, H)
        check_focus(checks, row, comp, W, H)
        check_badge_box(checks, sb, row, comp, W, H)
        check_closing(checks, sb, row, comp)
        check_caption(checks, sb, row, comp)
        check_callout(checks, row, comp)
        check_point(checks, sb, row, comp)
        check_layout(checks, sb, row, comp)
        check_side(checks, sb, row, comp)
    for row in exp[len(pairs):]:
        _c(checks, row["row"], "position", "fail", [row["start"], row["frames"]], None, "クリップが無い")
    return checks


# ---------- 書き出した動画 (ffprobe と画素) ----------
def probe(video):
    """書き出した動画の幅・高さ・フレームレート・フレーム数 (パケットを数える)。測る関数は common.probe_video。"""
    return C.probe_video(video, count_frames=True)


def check_video(sb, exp, info, why="書き出した動画が無い"):
    checks = []
    want = {"width": sb["timeline"]["width"], "height": sb["timeline"]["height"], "fps": float(sb["fps"]),
            "frames": sum(r["frames"] + ((r["hold"] or {}).get("frames") or 0) for r in exp)}
    for k, v in want.items():
        if info is None:
            _c(checks, None, f"video_{k}", "unknown", v, None, why)
        else:
            _c(checks, None, f"video_{k}", "pass" if _near(info[k], v, 1e-3) else "fail", v, info[k])
    return checks


def each_frame(video, frames, W, H, reader=None, colors=None):
    """動画の frames のフレーム (0 始まりの番号) を、(番号, RGB の配列 (高さ, 幅, 3)) で 1 つずつ返す。無いフレームと、大きさが W x H でないものは None。
    取り出しは common.frame_reader (近いフレームをまとめてシークで取り出し、覚えているフレームは使い回す)。
    1 つずつ渡すので、4K で多くのフレームを見ても全部を一度には持たない。reader (番号 → 配列) を渡せばそれを使う (テスト用)。
    colors (dict) を渡せば、rgb24 に直したときの読み方 (common.rgb_read) を colors[video] に入れる。読み方はフレームを読むために
    パケットの表 (index) を作ったときだけ入れる (読むフレームが無ければ表を作らず、入れない)。"""
    if reader is not None:
        for n in sorted(set(frames)):
            yield n, reader(n)
        return
    r = C.frame_reader(video)
    try:
        for n, img in r.each(frames):
            yield n, (img if img is not None and img.shape[:2] == (H, W) else None)
    finally:
        if colors is not None and r.index_table is not None:
            colors[video] = r.index_table.get("color")


def read_frame(video, n, W, H):
    """動画の n フレーム目 (0 始まり) を RGB の配列で返す。無ければ None。多くのフレームを見るときは each_frame を使う。"""
    return next(each_frame(video, [n], W, H))[1]


def each_source_frame(path, secs, W, H, colors=None):
    """素材の各秒 (secs) のフレームを、タイムラインの大きさに全体を収めて (縦横比を保ち、余りは黒)、(秒, RGB の配列) で 1 つずつ返す。
    秒は FFmpeg の入力側のシーク (-ss) と同じく、ファイルの開始時刻 (全ストリームの最初の時刻) からの秒。
    番号へは、PTS の表で「開始時刻 + 秒」より前でない最初のフレームを選ぶ (映像が音声より遅れて始まる素材でも、旧来のシークと同じフレーム)。
    動画の最後より後の秒と、フレームの間隔が一定でない素材など番号と時刻の対応を保証できない素材は None。
    色は素材の色の情報に従い、無ければ高さ 720 以上は BT.709 で読む (common.rgb_read。Resolve と同じ読み方)。
    colors (dict) を渡せば、その読み方を colors[path] に入れる。"""
    import bisect
    import numpy as np
    from PIL import Image
    r = C.frame_reader(path)
    ix = r.index()
    if colors is not None:
        colors[path] = ix.get("color")
    if "pts" not in ix:
        for s in secs:
            yield s, None
        return
    by_n = {}
    for s in secs:
        n = bisect.bisect_left(ix["pts"], ix["start"] + s - 1e-6)
        if n >= len(ix["pts"]):
            yield s, None
        else:
            by_n.setdefault(n, []).append(s)
    for n, img in r.each(list(by_n), keep=False):
        if img is not None and img.shape[:2] != (H, W):
            h, w = img.shape[:2]
            k = min(W / w, H / h)
            im = Image.fromarray(img).resize((max(1, round(w * k)), max(1, round(h * k))), Image.BICUBIC)
            pad = Image.new("RGB", (W, H), "black")
            pad.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
            img = np.asarray(pad)
        for s in by_n[n]:
            yield s, img


def _patches(img, pts):
    """img の各点 (x, y) のまわり 5x5 の平均の色。"""
    out = {}
    for k, (px, py) in pts.items():
        px, py = int(round(px)), int(round(py))
        out[k] = img[max(py - 2, 0):py + 3, max(px - 2, 0):px + 3].reshape(-1, 3).astype(float).mean(axis=0)
    return out


def _overlays(sb, row):
    """行の頭のフレームで、枠線より上に重なっている部品の四角 (タイムラインの画素の [x0, y0, x1, y1])。
    返す値: (外す四角の list, (四角, RGB, 不透明度) の list)。外す四角の点は比べない。2 つ目の四角の中の点は、
    素材の色にその色を不透明度で重ねた色と比べる。

    - テロップの背景 (帯): 色と不透明度が分かるので、重ねた色と比べる。文字の範囲 (書体で測った幅に余裕を足したもの) は外す。
      書体のファイルが無ければ帯の全部を外す
    - 肩テロップの板・行の頭から出ている callout (縁と影も含めて広めに)・マスク (素材の画素が変わる) は外す
    """
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    spec = sb["rows"][row["row"] - 1]
    cut, tint = [], []
    if spec.get("caption"):
        style = "closing" if spec.get("caption_style") == "closing" else None
        look = C.caption_look(sb, style)
        font = C.caption_font(sb)
        fit = C.fit_caption(sb, spec["caption"], font, style)
        y0 = (1 - fit["bar_center"] - fit["bar_height"] / 2) * H
        y1 = (1 - fit["bar_center"] + fit["bar_height"] / 2) * H
        widths = [C.text_width(font, fit["size"], x) for x in fit["lines"]] if font else [None]
        if None in widths or not look.get("bar", True):
            # 書体で測れない、または背景が無く文字だけのとき: 帯の高さの全部を外す
            tw = W if None in widths else max(widths) * 1.25 + 40
            cut.append([W / 2 - tw / 2, y0, W / 2 + tw / 2, y1])
        else:
            tw = max(widths) * 1.25 + 40   # Fusion の字は Pillow の見積もりより広いことがある (common.badge_box) ので余裕を見る
            cut.append([W / 2 - tw / 2, y0, W / 2 + tw / 2, y1])
            rgba = look["bar_rgba"]
            bw = look.get("border_width", 0)
            x0 = look.get("bar_inset", 0) + bw + ((y1 - y0) / 2 if look.get("corner_radius") else 0)
            tint.append(([x0, y0 + bw, W - x0, y1 - bw], rgba[:3], rgba[3] / 255))
            if bw or look.get("bar_inset", 0) or look.get("corner_radius"):   # 縁と、角の丸みの外は外す
                cut += [[0, y0, x0, y1], [W - x0, y0, W, y1]]
            if bw:   # 縁の上下の帯 (帯の上と下から border_width px)。縁の色は不透明なので素材の色と比べない
                cut += [[0, y0, W, y0 + bw], [0, y1 - bw, W, y1]]
            if look.get("end_mark"):
                cut.append([W - x0 - fit["size"] * 1.5 - look["margin_x"], y0, W - x0, y1])
    if row["badge_text"] is not None and row["badge"]:
        b = C.badge_box(sb, row["badge_text"], C.badge_font(sb))
        cx, cy = b["center"][0] * W, (1 - b["center"][1]) * H
        cut.append([cx - b["width"] * W / 2, cy - b["height"] * H / 2, cx + b["width"] * W / 2, cy + b["height"] * H / 2])
    pt = row.get("point")
    if pt and "target" in pt and "size" in pt:   # 指すマークは指す点から字の大きさの 1.5 倍の範囲に収まる (傾けても)
        tx, ty = pt["target"]
        rr = pt["size"] * 1.5
        cut.append([tx - rr, ty - rr, tx + rr, ty + rr])
    elif pt:   # 書式を読めずマークの場所か大きさが決まらない: 画面の全部を外す (マークの上の点を素材の色と比べない)
        cut.append([0, 0, W, H])
    c = row["callout"]
    if c and not c.get("unreadable") and c["ramp_frames"][0][1] > 0:
        x, y, w, h = c["dst"]
        cut.append([x - 60, y - 60, x + w + 60, y + h + 60])   # 縁・影・影のぼかしの分
    for m in row["masks"]:
        if not m.get("unreadable"):
            x, y, w, h = expected_box(m, 0)
            cut.append([x - 4, y - 4, x + w + 4, y + h + 4])
    return cut, tint


def _meets(a, b, pad=0):
    """四角 a と、四角 b を pad だけ広げた四角が重なるか。"""
    return a[0] <= b[2] + pad and a[2] >= b[0] - pad and a[1] <= b[3] + pad and a[3] >= b[1] - pad


def _inside(a, b, pad=0):
    """四角 a が、四角 b を pad だけ縮めた四角に収まるか。"""
    return a[0] >= b[0] + pad and a[2] <= b[2] - pad and a[1] >= b[1] + pad and a[3] <= b[3] - pad


def border_points(sb, row):
    """枠線の 4 辺の 1/4・1/2・3/4 の点のうち、比べる点。返す値: ({名前: (x, y)}, {名前: (RGB, 不透明度) か None}, 外した点の名前)。
    重なる部品 (_overlays) の縁にかかる点 (5x5 のまわりが縁から 4 画素以内) も外す。"""
    fo = row["focus"]
    x, y, w, h = expected_box({"keys": fo["keys"], "box": fo["box"]}, 0)
    pts = {}
    for f, name in ((0.25, "1/4"), (0.5, "1/2"), (0.75, "3/4")):
        pts[f"左 {name}"] = (x, y + h * f)
        pts[f"右 {name}"] = (x + w, y + h * f)
        pts[f"上 {name}"] = (x + w * f, y)
        pts[f"下 {name}"] = (x + w * f, y + h)
    cut, tint = _overlays(sb, row)
    keep, over, gone = {}, {}, []
    for k, (px, py) in pts.items():
        p = [round(px) - 2, round(py) - 2, round(px) + 2, round(py) + 2]
        if any(_meets(p, c, 4) for c in cut):
            gone.append(k)
            continue
        hit = [t for t in tint if _meets(p, t[0], 4)]
        if hit and not all(_inside(p, t[0], 4) for t in hit):
            gone.append(k)   # 帯の縁にかかる
            continue
        keep[k], over[k] = (px, py), (hit[0][1], hit[0][2]) if hit else None
    return keep, over, gone


def border_rows(sb, exp):
    """--pixel で枠線の画素を比べる行と、比べない行。返す値: ([(行, 点, 重なる色, 外した点の名前)], [{"row", "reason"}])。
    比べない行は、枠線のある focus を持つのに比べられない行だけ (枠線の無い行は入れない)。fail にも unknown にもしない。
    理由の文は 1 行の要約にもそのまま出すので、空白を入れない。"""
    rows, skipped = [], []
    for row in exp:
        fo = row["focus"]
        if not fo:
            continue
        if fo.get("unreadable"):
            # 読めない focus は、元の構成表の行が枠線を求めているときだけ入れる (枠線の有無も読めなければ入れない)
            spec = sb["rows"][row["row"] - 1].get("focus")
            if not (isinstance(spec, dict) and spec.get("border")):
                continue
        elif not fo.get("border"):
            continue
        z = row["zoom"]
        if fo.get("unreadable"):
            why = "範囲の書式を読めない"
        elif fo["ramp_frames"][0][1] > 0:
            why = "頭から枠線が出ている"
        elif row["size_unknown"]:
            why = "素材の解像度を読めない"
        elif row.get("layout"):
            why = "layoutで画面を縮めて動かしている"   # 素材のフレームと同じ場所の画素で比べられない
        elif z and z.get("unreadable"):
            why = "ズームの書式を読めない"
        elif z and abs(z["size"][0 if z["frames"] else 1] - 1.0) > 1e-6:
            why = "頭から寄っている"   # ズームの秒数が 0 なら、頭から寄り終わった大きさ
        else:
            pts, over, gone = border_points(sb, row)
            if pts:
                rows.append((row, pts, over, gone))
                continue
            why = "比べる点が残らない"   # 帯の文字・肩テロップの板・callout・マスクに全部の点が重なる
        skipped.append({"row": row["row"], "reason": why})
    return rows, skipped


def check_border_pixels(sb, exp, video, reader=None, source_reader=None, colors=None):
    """focus の枠線が、現れる前 (行の頭のフレーム) には出ていないことを画素で見る (--pixel のとき)。
    書き出しの行の頭のフレームと、素材の同じ時刻のフレームを、枠線の 4 辺の 1/4・1/2・3/4 の点で比べる (差が 24 以下なら出ていない)。
    テロップの背景 (帯) に重なる点は、素材の色に帯の色を重ねた色と比べる。帯の文字・肩テロップの板・行の頭から出ている callout・
    マスクに重なる点は外す (枠線の上に重なり、素材の色と比べられない)。比べる点が 1 つも残らない行と、
    行の頭で寄っている (ズームが 1 倍でない) 行は比べられないので見ない。
    フレームは 1 つずつ取り出し、比べる点のまわりの色だけを残す。reader と source_reader はテスト用の差し替え。
    colors (dict) を渡せば、書き出しと素材を rgb24 に直したときの読み方をファイルごとに入れる。"""
    import numpy as np
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    rows, _ = border_rows(sb, exp)
    out = {}
    for n, img in each_frame(video, [row["start"] for row, *_ in rows], W, H, reader, colors):
        for row, pts, *_ in rows:
            if row["start"] == n:
                out[row["row"]] = None if img is None else _patches(img, pts)
    src = {}
    for path in sorted({row["source"] for row, *_ in rows}):
        mine = [(row, pts) for row, pts, *_ in rows if row["source"] == path]
        if source_reader is not None:
            frames = ((s, source_reader(path, s)) for s in sorted({row["src_start"] for row, _ in mine}))
        else:
            frames = each_source_frame(path, sorted({row["src_start"] for row, _ in mine}), W, H, colors)
        for s, img in frames:
            for row, pts in mine:
                if row["src_start"] == s:
                    src[row["row"]] = None if img is None else _patches(img, pts)
    checks = []
    for row, _, over, gone in rows:
        a, b = out.get(row["row"]), src.get(row["row"])
        if a is None or b is None:
            _c(checks, row["row"], "focus_border_pixels_0", "unknown", None, None, "書き出しか素材のフレームを読めない")
            continue
        diff = {}
        for k in a:
            want = b[k] if over[k] is None else b[k] * (1 - over[k][1]) + np.array(over[k][0], float) * over[k][1]
            diff[k] = round(float(np.abs(a[k] - want).max()), 1)
        _c(checks, row["row"], "focus_border_pixels_0", "pass" if max(diff.values()) <= 24 else "fail",
           "素材と同じ (差 24 以下)", dict(diff, **({"外した点": gone} if gone else {})),
           "行の頭 (枠線が現れる前) の、枠線の辺の点の画素と素材の画素 (帯に重なる点は帯の色を重ねた色) の差 (0〜255)")
    return checks


def badge_share(sb, img, text=None):
    """肩テロップの板の内側 (縁を 1 割ずつ外す) で、肩テロップの色に近い画素の割合。text は板の幅を決める文 (接頭辞つき)。"""
    import numpy as np
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    b = C.badge_box(sb, text, C.badge_font(sb))
    x0, x1 = (b["center"][0] - b["width"] * 0.4) * W, (b["center"][0] + b["width"] * 0.4) * W
    y0, y1 = (1 - b["center"][1] - b["height"] * 0.4) * H, (1 - b["center"][1] + b["height"] * 0.4) * H
    part = img[int(y0):int(y1), int(x0):int(x1)].astype(int)
    rgb = np.array(sb["badge"]["rgba"][:3])
    return float((np.abs(part - rgb).max(axis=2) <= 48).mean())


def check_badge_pixels(sb, exp, video, reader=None, colors=None):
    """肩テロップが消える前後のフレームで、肩テロップの色が出ているかを見る (--pixel のとき)。
    見るフレームはまとめて取り出し、1 つずつ割合を出して捨てる。reader はテスト用の差し替え。"""
    looks = [(row, t, want) for row in exp if row["badge"] is not None
             for t, want in ((row["badge"] - 1, True), (row["badge"], False)) if t < row["frames"]]
    share = {}
    for n, img in each_frame(video, [row["start"] + t for row, t, _ in looks],
                             sb["timeline"]["width"], sb["timeline"]["height"], reader, colors):
        for row, t, _ in looks:
            if row["start"] + t == n:
                share[(row["row"], t)] = None if img is None else badge_share(sb, img, row["badge_text"])
    checks = []
    for row, t, want in looks:
        s = share.get((row["row"], t))
        seen = None if s is None or 0.1 < s < 0.5 else s >= 0.5
        status = "unknown" if seen is None else ("pass" if seen == want else "fail")
        _c(checks, row["row"], f"badge_pixels_{t}", status, "出ている" if want else "消えている", s,
           "肩テロップの板の中で肩テロップの色に近い画素の割合 (0.5 以上で出ている、0.1 以下で消えている)")
    return checks


# ---------- 区切りのフレームと画像 ----------
def boundary_frames(row, total):
    """行の始まり・終わり、肩テロップが消える、ズームの始まり・終わりのフレームと、その前後 1 フレーム。"""
    s, n = row["start"], row["frames"]
    marks = [(s, "行の始まり"), (s + n, "行の終わりの次")]
    if row["hold"] and row["hold"].get("frames"):
        marks.append((s + n + row["hold"]["frames"], "止めた区間の終わりの次"))
    if row["badge"] is not None:
        marks.append((s + row["badge"], "肩テロップが消えるフレーム"))
    if row["zoom"] and row["zoom"]["frames"] is not None:   # 書式を読めないズームは時刻が無い
        marks.append((s + row["zoom"]["frames"], "ズームが止まるフレーム"))
    out = {}
    for b, why in marks:
        for f, label in ((b - 1, f"{why}の 1 フレーム前"), (b, why), (b + 1, f"{why}の 1 フレーム後")):
            if 0 <= f < total:
                out.setdefault(f, f"{row['row']} 行目 {label}")
    return sorted(out.items())


def _not_visible(check):
    """画像を見ても判定できない項目か。動画全体の項目 (行の無い timeline・video_*・badge_pixels) の unknown は、
    接続できない・読めないことが理由で、区切りのフレームを見ても答えが出ない。"""
    return check["row"] is None and (check["item"] in ("timeline", "badge_pixels") or check["item"].startswith("video_"))


def read_near(cfg, storyboard, tl):
    """sb2resolve.py の記録 (出力先の sb2resolve\\<構成表の名前>.json) から、隣のフレームで止めた行 {行: 映すフレーム} を読む。
    記録が無い・読めない・別のタイムラインの記録なら空 (1 の違いは認めない)。"""
    if tl is None or not storyboard:
        return {}
    path = os.path.join(cfg.out_dir("sb2resolve"), os.path.splitext(os.path.basename(storyboard))[0] + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            log = json.load(f)
    except (OSError, ValueError):
        return {}
    if log.get("timeline_name") != tl.GetName():
        return {}
    rows = (log.get("timeline") or {}).get("rows") or []
    return {r["row"]: r["hold"]["near"]["got"] for r in rows if ((r.get("hold") or {}).get("near") or {}).get("got") is not None}


def run(sb, video=None, tl=None, pixel=False, reader=None, tl_note="タイムラインを読んでいない", sizes=None, near=None):
    """照らし合わせて、報告の dict を返す。tl が None ならタイムラインの項目は unknown (理由は tl_note)。
    sizes は素材ごとの解像度 (省略すると source_sizes で読む)。near は組んだ記録の隣のフレームで止めた行 (read_near)。"""
    exp = expected_rows(sb, source_sizes(sb, tl) if sizes is None else sizes)
    checks, skipped, color = [], None, None
    for row in exp:
        check_badge_speed(checks, row)
    if tl is None:
        _c(checks, None, "timeline", "unknown", "Resolve のタイムライン", None, tl_note)
    else:
        checks += check_timeline(sb, exp, tl, near)
    info, why = None, "書き出した動画が無い"
    if video and os.path.isfile(video):
        try:
            info = probe(video)
        except (RuntimeError, KeyError, IndexError, ValueError) as e:
            why = f"ffprobe で読めない: {str(e)[-300:]}"
    checks += check_video(sb, exp, info, why)
    if pixel:
        if info is None:
            _c(checks, None, "badge_pixels", "unknown", None, None, why)
        else:
            color = {}   # 書き出しと素材を rgb24 に直したときの読み方 (common.rgb_read)。読んだファイルだけ
            checks += check_badge_pixels(sb, exp, video, reader, color)
            checks += check_border_pixels(sb, exp, video, reader, colors=color)
            skipped = border_rows(sb, exp)[1]
            if reader is None and video not in color:
                color[video] = "読むフレームが無いので、読み方を決めていない"
    # 画像を見ても判定できない unknown (Resolve に接続していない、動画が無い・読めない) は、区切りの画像を作る理由にしない
    look = [c for c in checks if c["status"] == "fail" or (c["status"] == "unknown" and not _not_visible(c))]
    rows = sorted({c["row"] for c in look if c["row"]})
    if any(c["row"] is None for c in look) and exp:
        rows = sorted(set(rows) | {exp[-1]["row"]})   # 全体の項目が合わないときは、最後の行の区切りを見る
    total = info["frames"] if info else 0
    frames = []
    for r in rows:
        frames += boundary_frames(exp[r - 1], total)
    report = {"storyboard": sb.get("_path"), "video": video, "timeline": tl.GetName() if tl else None,
              "summary": {s: sum(c["status"] == s for c in checks) for s in ("pass", "fail", "unknown")},
              "checks": checks, "sheet": {"rows": rows, "frames": [{"frame": f, "reason": w} for f, w in frames]},
              # 構成表の知らない項目 (書き間違い。sb2resolve.py と同じ文)。照らし合わせの判定には入れない
              "warnings": C.unknown_key_warnings(sb)
              + [f"{c['row']} 行目: 止めたクリップは期待のフレーム {c['expected']['src']} の隣の {c['actual']['src']} を映す"
                 " (Resolve が止められないフレーム。sb2resolve.py の記録の near)" for c in checks if c.get("near")]}
    if color is not None:
        # ファイルごとの YUV から RGB への式と範囲、どの情報で決めたか (--pixel のとき)
        report["color_read"] = color
    if skipped is not None:
        # 枠線の画素を比べなかった行と理由 (--pixel のときだけ)。fail・unknown ではないが、目で見る候補になる
        report["border_pixels_skipped"] = skipped
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("storyboard")
    ap.add_argument("--video", help="書き出した動画 (既定は構成表の output)")
    ap.add_argument("--source", help="素材 (構成表の source より優先)")
    ap.add_argument("--timeline", help="今のプロジェクトの中のタイムラインの名前 (既定は今のタイムライン)")
    ap.add_argument("--no-resolve", action="store_true", help="Resolve に接続しない (タイムラインの項目は unknown)")
    ap.add_argument("--pixel", action="store_true", help="肩テロップの色と、focus の枠線が現れる前の画素も見る (実際の録画の書き出しでも使う)")
    ap.add_argument("--name", help="報告のファイル名 (既定は動画の名前)")
    a = ap.parse_args()
    cfg = C.config()
    sb = cfg.load_storyboard(a.storyboard, a.source)
    sb["_path"] = os.path.abspath(a.storyboard)
    video = a.video or sb.get("output")
    tl, note = None, "--no-resolve (タイムラインを読んでいない)"
    if not a.no_resolve:
        r = C.connect_resolve(quiet=True)
        proj = r.GetProjectManager().GetCurrentProject() if r else None
        tl = proj.GetCurrentTimeline() if proj else None
        if proj and a.timeline:
            tls = [proj.GetTimelineByIndex(i + 1) for i in range(int(proj.GetTimelineCount()))]
            tl = next((t for t in tls if t.GetName() == a.timeline), None)
        note = (f"{C.NO_RESOLVE} ({C.RESOLVE_HINT})" if not r else "プロジェクトが開いていない" if not proj
                else "タイムラインが見つからない (今のタイムラインが無いか、--timeline の名前が違う)")
    report = run(sb, video, tl, a.pixel, tl_note=note, near=read_near(cfg, a.storyboard, tl))
    print(write_report(cfg, report, a.name or os.path.splitext(os.path.basename(video or "check"))[0]))
    sys.exit(0 if report["summary"]["fail"] == 0 and report["summary"]["unknown"] == 0 else 1)


def write_report(cfg, report, name):
    """JSON と、区切りのフレームの画像 (fail と unknown の行があり、動画があるとき) を書き、画面に出す 1 行を返す。

    どちらもこの実行の一時のフォルダーに書き、書き終えてから出力先へ移す (common.run_dir)。fail があっても移す。
    画像を作れないときは理由を JSON に残して移す。途中で止まったとき (中断・JSON を書けない) は、前回の JSON と画像を残す。
    今回画像を作らなければ、前回の画像は消す (今回の確認に混ざらないように)。
    """
    import contact_sheet
    out = cfg.out_dir("check-final")
    png = os.path.join(out, name + ".png")
    path = os.path.join(out, name + ".json")
    temp = C.run_dir(out)
    try:
        frames = [f["frame"] for f in report["sheet"]["frames"]]
        if frames and report["video"] and os.path.isfile(report["video"]):
            part = os.path.join(temp, name + ".png")
            try:
                contact_sheet.frames_sheet(report["video"], frames, part)
                report["sheet"]["image"] = png
            except (RuntimeError, SystemExit, OSError) as e:
                report["sheet"]["error"] = str(e)[-500:]   # 画像を作れなくても判定は残す
                if os.path.exists(part):
                    os.remove(part)   # 書きかけの画像は移さない
        C.write_json(os.path.join(temp, name + ".json"), report)
        C.publish_run(temp, out, [name + ".png"], name + ".json")
    except BaseException:
        C.discard_run(temp)
        raise
    s = report["summary"]
    line = f"failed={s['fail']} unknown={s['unknown']}"
    skipped = report.get("border_pixels_skipped")
    if skipped:   # 例: border_unchecked=3:頭から寄っている,5:比べる点が残らない
        line += " border_unchecked=" + ",".join(f"{k['row']}:{k['reason']}" for k in skipped)
    if report.get("warnings"):
        line += f" warnings={len(report['warnings'])}"
    line += f" report={path}"
    return line + (f" sheet={report['sheet']['image']}" if report["sheet"].get("image") else "")


if __name__ == "__main__":
    main()
