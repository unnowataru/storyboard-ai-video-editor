"""Fusion の値と組み立て (scripts/common.py から読み込み直す部品)。

構成表の 1 行に付ける Fusion の値を出す scene_values (部品の名前 PART_KEYS、止めたクリップの値 hold_values、折れ線の補間
value_at・keys_at・shift_keys、ズームの倍率と中心 zoom_geometry、focus の枠線の色を素材の明るさで決める auto_border) と、
その値でクリップに Fusion の合成を組む build_fusion (部品ごとの _mask・_focus・_zoom・_caption・_badge など、
Text+ と単色の置き方 text_tool・solid・set_rect・set_fill・rgba01)。
"""
import math
import os

from layout import (badge_box, badge_font, caption_band, caption_look, caption_window, callout_values, fit_caption,
                    fusion_canvas, layout_values, point_values, side_values, source_to_timeline, zoom_safe_area_warning)
from resolve import appear_frames, appear_keys, comp_frame, hold_source_frame, hold_time, tc_to_sec
from schema import (AUTO_COLOR, MASK_SHAPES, callout_problems, focus_problems, fusion_filter, hex_rgb, mask_problems,
                    merge_side)
from video import frame_reader


def _lerp(a, b, u):
    """a と b の間を u (0〜1) で直線に補間する。tuple / list は要素ごと、dict は項目ごと。"""
    if isinstance(a, dict):
        return {k: _lerp(a[k], b[k], u) for k in a}
    if isinstance(a, (tuple, list)):
        return tuple(_lerp(x, y, u) for x, y in zip(a, b))
    return a + (b - a) * u


def value_at(pairs, f):
    """時刻の順の [(時刻, 値)] の、時刻 f での値。キーの間は直線 (_lerp)。最初のキーより前は最初の値、最後のキーより後は最後の値。
    折れ線のキーを読む所 (keys_at・shift_keys・focus_box_at) はどれもここで補間する。"""
    if f <= pairs[0][0]:
        return pairs[0][1]
    for (f0, v0), (f1, v1) in zip(pairs, pairs[1:]):
        if f <= f1 and f1 > f0:
            return _lerp(v0, v1, (f - f0) / (f1 - f0))
    return pairs[-1][1]


def keys_at(keys, f):
    """scene_values の keys の、行のクリップの合成の時刻 f での中心・幅・高さ。
    行のクリップは合成の時刻のキーで補間するので、止めた瞬間の位置も合成の時刻で求める。"""
    return value_at([(k["comp"], {n: k[n] for n in ("center", "width", "height")}) for k in keys], f)


def shift_keys(pairs, n):
    """[(書き出しのフレーム, 値)] を n フレーム後ろから始まるように直す。n の瞬間の値を 0 に置き、n より後のキーを残す。"""
    pairs = sorted(pairs)
    return [(0, value_at(pairs, n))] + [(f - n, v) for f, v in pairs if f > n]


# scene_values が返す部品の名前 (値の dict の項目)。masks は list、ほかは無ければ None
PART_KEYS = ("masks", "focus", "zoom", "caption", "badge", "callout", "canvas", "point", "side", "layout")
# 素材の画素の座標を使う部品 (素材の解像度が分からないと組めない)
COORD_PARTS = ("masks", "focus", "zoom", "callout", "point", "layout")
# 置くものがあるかを見る部品 (canvas は画面の広げ方で、部品ではない)
SHOWN_PARTS = tuple(k for k in PART_KEYS if k != "canvas")


def empty_values():
    """部品の無い値 (scene_values の形)。sb2resolve.py は値を作れなかった行にこれを使う。"""
    return {k: ([] if k == "masks" else None) for k in PART_KEYS}


def needs_coords(values):
    """座標の要る部品 (COORD_PARTS) があるか。"""
    return any(values.get(k) for k in COORD_PARTS)


def has_parts(values):
    """置く部品 (SHOWN_PARTS) が 1 つでもあるか。無ければ Fusion の合成を追加しない。"""
    return any(values.get(k) for k in SHOWN_PARTS)


def hold_values(values, moment, n):
    """止めたクリップに付ける Fusion の値。

    止めたクリップ (SetSpeed の Percentage 0) は 1 枚の画を映し続けるが、その中の Fusion の時刻は等速で進む
    (21.1.1 の実機、tests/resolve/hold_freeze.py)。そこで:
    - 画に付く位置 (マスクと focus の範囲・枠線) は、止めた瞬間 (行のクリップの合成の時刻 moment。hold_time) の位置で動かさない
    - 時間で現れる動き (暗くなる・枠線・callout・ズーム) は、行の頭からの時刻のまま止めた区間へ続ける (行のフレーム数 n だけずらす)
    - 肩テロップは置かない。テロップはそのまま
    """
    out = empty_values()
    out.update({k: values.get(k) for k in ("caption", "canvas", "point", "side", "layout")})
    for m in values["masks"]:
        out["masks"].append(dict(m, **(keys_at(m["keys"], moment) if m["keys"] else {}), keys=[]))
    fo = values.get("focus")
    if fo:
        out["focus"] = dict(fo, **(keys_at(fo["keys"], moment) if fo["keys"] else {}), keys=[],
                            ramp=shift_keys(fo["ramp_frames"], n), ramp_frames=shift_keys(fo["ramp_frames"], n))
    z = values["zoom"]
    if z:
        f = z["frames"]
        if f > n:   # 止めた区間に入っても寄り続ける
            u = n / f
            v = {k: _lerp(z[k][0], z[k][1], u) for k in ("size", "x", "y")}
            out["zoom"] = dict(z, size=(v["size"], z["size"][1]), x=(v["x"], z["x"][1]), y=(v["y"], z["y"][1]),
                               frames=f - n, comp_frames=f - n)
        else:
            out["zoom"] = dict(z, size=(z["size"][1],) * 2, x=(z["x"][1],) * 2, y=(z["y"][1],) * 2, frames=0, comp_frames=1)
    co = values.get("callout")
    if co:
        out["callout"] = dict(co, ramp=shift_keys(co["ramp_frames"], n), ramp_frames=shift_keys(co["ramp_frames"], n))
    return out


def rgba01(v):
    """0〜255 の RGB(A) を Fusion の 0〜1 の RGBA に。A が無ければ不透明。"""
    v = list(v) + [255] * (4 - len(v))
    return [x / 255 for x in v]


def text_tool(comp, text, size, center, font_name, font_style, color, align=None):
    """Fusion の Text+ を置く。color (0〜255 の RGB) を字の色 (1 番目の塗り) に入れる。
    align が "left" なら、どの行も左の端を center に揃える (HorizontalLeftCenterRight -1。21.1.1 の実機で確認した)。"""
    t = comp.AddTool("TextPlus", -32768, -32768)
    if align == "left":
        t.SetInput("HorizontalLeftCenterRight", -1)
    t.SetInput("StyledText", text)
    t.SetInput("Font", font_name)
    t.SetInput("Style", font_style)
    t.SetInput("Size", size)
    t.SetInput("Center", {1: center[0], 2: center[1]})
    r, g, b, _ = rgba01(color)
    for k, v in (("Red1", r), ("Green1", g), ("Blue1", b)):  # Resolve での動作は未確認
        t.SetInput(k, v)
    return t


def set_rect(tool, center, width, height):
    """マスク (RectangleMask / EllipseMask) の形を入れる。Center は Fusion の座標、Width は画面の幅、Height は画面の高さを 1 とした値。"""
    tool.SetInput("Center", {1: center[0], 2: center[1]})
    tool.SetInput("Width", width)
    tool.SetInput("Height", height)
    return tool


def set_fill(bg, rgba):
    """単色 (Background) の色を入れる。rgba は 0〜1 の値 (rgba01)。3 つだけ渡せば色だけを入れ、不透明度は触らない。"""
    for k, v in zip(("TopLeftRed", "TopLeftGreen", "TopLeftBlue", "TopLeftAlpha"), rgba):
        bg.SetInput(k, v)
    return bg


def solid(comp, rgba, center, width, height, corner=None, name=None, mask_name=None, hole=None):
    """RectangleMask で切った単色 (Background) を置く。テロップの背景と肩テロップの板に使う。座標は Fusion の座標。
    name を渡すと、単色に name、マスクに mask_name (省略すると name + "Mask") の名前を付ける (テンプレートと同じ名前)。
    hole (幅と高さ、中心は同じ) を渡すと、その内側を抜いた額縁にする (縁。中の板が半透明でも縁の色が透けない)。
    抜き方は枠線の輪と同じ (内のマスク name + "Hole" を反転して外のマスクの EffectMask につなぎ、外の PaintMode を Multiply)。"""
    bg = set_fill(comp.AddTool("Background", -32768, -32768), rgba01(rgba))
    m = set_rect(comp.AddTool("RectangleMask", -32768, -32768), center, width, height)
    if corner is not None:
        m.SetInput("CornerRadius", corner)
    if hole:
        inner = set_rect(comp.AddTool("RectangleMask", -32768, -32768), center, hole[0], hole[1])
        if corner is not None:
            inner.SetInput("CornerRadius", corner)
        inner.SetInput("Invert", 1)
        m.EffectMask = inner.Mask
        m.SetInput("PaintMode", "Multiply")
        if name:
            _named(inner, name + "Hole")
    bg.EffectMask = m.Mask
    if name:
        _named(bg, name)
        _named(m, mask_name or name + "Mask")
    return bg


def zoom_ends(zoom, full, zoomed):
    """ズームの始めと終わりの値。構成表の zoom.direction が "out" ならズーム範囲から全体へ引くので入れ替える。"""
    return (zoomed, full) if zoom.get("direction", "in") == "out" else (full, zoomed)


def zoom_geometry(z, W, H):
    """ズーム範囲 z (タイムラインの画素の x, y, w, h) を画面 (W x H) いっぱいに広げるときの倍率と、範囲の中心 (タイムラインの画素)。
    返す値は {"scale", "center": (cx, cy)}。Resolve に組む側 (scene_values) と FCPXML (sb2nle.fcpxml) が同じ決め方を使う。
    check_final.py は意図して自分で同じ式を計算する (組む側の誤りを同じように持たないため)。"""
    return {"scale": min(W / z["w"], H / z["h"]), "center": (z["x"] + z["w"] / 2, z["y"] + z["h"] / 2)}


def _box01(b, W, H):
    """タイムラインの画素の四角を Fusion の値 (中心は左下が 0,0。幅は画面の幅、高さは画面の高さを 1) にする。"""
    return {"center": ((b["x"] + b["w"] / 2) / W, 1 - (b["y"] + b["h"] / 2) / H), "width": b["w"] / W, "height": b["h"] / H}


def badge_blend_keys(frames, speed=1.0):
    """肩テロップの Merge の Blend のキー [(合成の時刻, 値)]。書き出しの frames - 1 フレーム目まで出て frames フレーム目で消える。

    隣り合う時刻にキーを置き、間をなだらかにしない。速さを変えたクリップでは合成の時刻に直す (comp_frame)。
    """
    b = comp_frame(frames, speed)   # 書き出しの frames フレーム目の合成の時刻
    if b <= comp_frame(frames - 1, speed):
        b += 1   # 遅くした行で 2 つのフレームが同じ時刻になるときは、1 フレーム遅れて消える (分けられない)
    return [(0, 1.0), (b - 1, 1.0), (b, 0.0)]


# ---------- 枠線の色を素材の明るさで決める (focus.border.color が auto) ----------
def luma709(img):
    """RGB の配列 (0〜255) の Rec.709 の輝度 (0〜255。ガンマの掛かった値のまま重みを掛ける)。2 次元の配列はそのまま返す。"""
    import numpy as np
    a = np.asarray(img, dtype=float)
    if a.ndim == 2:   # 1 チャンネル (グレースケール) は、その値を輝度とみなす
        return a
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def ring_luma(img, box, inside, outside):
    """素材のフレーム img (高さ, 幅, 3) の、四角 box (素材の画素) の辺から内側 inside 画素・外側 outside 画素の帯の輝度。
    帯は画面の中に切り詰める。内側の幅が四角の半分以上なら四角の内側の全部。
    返す値 {"median", "mean", "pixels"}。帯が画面の外なら pixels は 0 で、median と mean は None。"""
    import numpy as np
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]
    ox0, oy0 = max(math.floor(x0 - outside), 0), max(math.floor(y0 - outside), 0)
    ox1, oy1 = min(math.ceil(x1 + outside), w), min(math.ceil(y1 + outside), h)
    if ox1 <= ox0 or oy1 <= oy0:
        return {"median": None, "mean": None, "pixels": 0}
    keep = np.ones((oy1 - oy0, ox1 - ox0), bool)
    ix0, iy0 = math.ceil(x0 + inside) - ox0, math.ceil(y0 + inside) - oy0
    ix1, iy1 = math.floor(x1 - inside) - ox0, math.floor(y1 - inside) - oy0
    if ix1 > ix0 and iy1 > iy0:
        keep[max(iy0, 0):max(iy1, 0), max(ix0, 0):max(ix1, 0)] = False
    v = luma709(img[oy0:oy1, ox0:ox1])[keep]
    if not v.size:
        return {"median": None, "mean": None, "pixels": 0}
    return {"median": float(np.median(v)), "mean": float(v.mean()), "pixels": int(v.size)}


def keys_by_frame(keys, fps):
    """keys を書き出しのフレーム (round(t × fps)) の順に並べた [(フレーム, key), ...]。
    同じフレームに丸まる keys は、時刻の順で後のものを使う。描く側 (scene_values) と測る側 (focus_box_at、check_final) が
    同じ keys を使うため、そろえ方はここだけで決める。"""
    out = {}
    for k in sorted(keys or [], key=lambda k: float(k["t"])):
        out[round(float(k["t"]) * fps)] = k
    return sorted(out.items())


def focus_box_at(fo, f, fps, speed=None):
    """focus の範囲 (素材の画素) の四角 {x, y, w, h}。keys の間は直線 (value_at)。

    keys は描く側と同じく keys_by_frame でそろえる (同じフレームに丸まる keys は後のもの)。
    f は行の頭からの書き出しのフレーム。speed を渡すと f は行のクリップの合成の時刻で、keys の時刻も合成の時刻
    (comp_frame) に直して補間する (行のクリップに描く scene_values と、止めたクリップの位置を決める hold_values と同じ)。"""
    pairs = keys_by_frame(fo.get("keys"), fps)
    if not pairs:
        return {k: float(fo[k]) for k in "xywh"}
    box = value_at([(fr if speed is None else comp_frame(fr, speed), {k: k_[k] for k in "xywh"}) for fr, k_ in pairs], f)
    return {k: float(v) for k, v in box.items()}


def auto_border(look, row, source, frames, speed=1.0, reader=None, sfps=None):
    """行の focus の枠線の色が auto なら、素材の明るさで決めた色と測った値の dict。auto でなければ None。

    色の値と判定の値は config の focus_border.auto (dark・light の色、threshold・near・inside・outside・measure)。
    見るフレーム: 枠線が現れ始める書き出しのフレーム f (focus.start。0 なら行の頭) に映る素材のフレーム。行の中なら
    行の頭の素材のフレーム + comp_frame(f, 速さ)、止めた区間に入っていれば止めたフレーム (hold_source_frame)。
    見る範囲: そのときの focus の範囲 (keys があれば f の合成の時刻 comp_frame(f, 速さ) の位置。keys も合成の時刻に直して
    補間するので、割り切れない倍速でも行のクリップに描く枠線と同じ位置。止めた区間では止めた瞬間 (hold_time) の位置で、
    止めたクリップに描く枠線と同じ位置) の辺から
    内側 inside・外側 outside 画素の帯 (素材の画素)。暗くする前の素材の画素のまま測る (ring_luma)。
    明るさは帯の Rec.709 の輝度の measure (median か mean。0〜255)。threshold 以上なら light の色、未満なら dark の色。
    しきい値との差が near より小さければ warning に、色を書くよう勧める文を入れる。
    素材を読めなければ error に理由を入れ、color は None。frames は行のフレーム数 (止めた区間を含まない)、speed は倍率。
    reader (素材, フレーム番号) → 配列 と sfps (素材のフレームレート) はテスト用の差し替え。無ければ frame_reader で読む
    (sfps は ffprobe の r_frame_rate)。素材と構成表のフレームレートが違えば、見るフレームは近似なので warning に書く
    (行の中と止めた区間で換算の式が違い、どちらが Resolve の表示に合うかは実機で確認していない)。
    """
    fo = row.get("focus")
    if not isinstance(fo, dict) or not fo.get("border"):
        return None
    b = dict(look["focus_border"]) | (fo["border"] if isinstance(fo["border"], dict) else {})
    if b.get("color") != AUTO_COLOR:
        return None
    a = look["focus_border"]["auto"]
    fps = float(look["fps"])
    measure = a.get("measure", "median")
    info = {"color": None, "threshold": float(a["threshold"]), "measure": measure}
    try:
        f0, _ = appear_frames(fo, look["focus"], "seconds", fps)
        info["appear_frame"] = f0
        if reader is None:
            if not source or not os.path.isfile(source):
                raise ValueError(f"素材が無い ({source})")
            fr = frame_reader(source)
            sfps = sfps or fr.index().get("fps")
            reader = lambda path, n: fr.read([n]).get(n)   # noqa: E731
        sfps = sfps or fps   # reader を差し替えて sfps を省いたときは、素材も構成表の fps とみなす (テスト用)
        if not sfps:
            raise ValueError("素材のフレームレートを読めない")
        if f0 < frames:
            n = round(tc_to_sec(row["start"]) * sfps) + comp_frame(f0, speed * sfps / fps)
            # 描く側 (scene_values) と同じく、keys を合成の時刻に直し、f0 の合成の時刻で補間する
            box = focus_box_at(fo, comp_frame(f0, speed), fps, speed)
        else:   # 止めた区間: 止めたフレームと、止めたクリップの位置 (hold_values と同じ止めた瞬間)
            n = hold_source_frame(row, sfps, frames, speed)
            box = focus_box_at(fo, hold_time(row, sfps, frames, speed), fps, speed)
        info.update(source_frame=n, source_time=round(n / sfps, 3), box=box)
        img = reader(source, n)
        if img is None:
            raise ValueError(f"素材のフレーム {n} を読めない")
        lum = ring_luma(img, box, float(a["inside"]), float(a["outside"]))
        if not lum["pixels"]:
            raise ValueError("focus の範囲のまわりが素材の画面の外")
    except (ValueError, TypeError, KeyError, IndexError, RuntimeError, OSError, SystemExit) as e:
        info["error"] = "枠線の色 (auto) を決められない: " + (str(e) or type(e).__name__)
        return info
    v = lum[measure]
    light = v >= info["threshold"]
    info.update(color=a["light"] if light else a["dark"], side="light" if light else "dark", luma=round(v, 1),
                luma_median=round(lum["median"], 1), luma_mean=round(lum["mean"], 1), pixels=lum["pixels"],
                near=abs(v - info["threshold"]) < float(a["near"]))
    warn = []
    if info["near"]:
        warn.append(f"focus の枠線の色 (auto) を {info['color']} ({'明るい' if light else '暗い'}背景) にしたが、"
                    f"明るさ {v:.0f} がしきい値 {info['threshold']:g} に近い。画を見て border.color に色を書く")
    if abs(float(sfps) - fps) > 1e-3:
        warn.append(f"素材 ({float(sfps):g} fps) と構成表 ({fps:g} fps) のフレームレートが違うので、focus の枠線の色 (auto) で"
                    f"見た素材のフレーム {n} は近似。画を見て border.color に色を書く")
    if warn:
        info["warning"] = "。".join(warn)
    return info


def with_auto_border(look, row, source, frames, speed=1.0, reader=None, sfps=None):
    """(行, auto_border の記録)。枠線の色が auto なら、決めた色を border.color に書いた行の写しを返す
    (決められなければ focus_border.auto.dark の色を書き、記録の error に理由。呼ぶ側が誤りにする)。auto でなければ (行, None)。
    sb2resolve.py (組む) と check_final.py (照らし合わせる) が同じ色を使うため、色はここだけで決める。"""
    info = auto_border(look, row, source, frames, speed, reader, sfps)
    if info is None:
        return row, None
    fo = dict(row["focus"])
    fo["border"] = dict(fo["border"]) if isinstance(fo["border"], dict) else {}
    fo["border"]["color"] = info["color"] or look["focus_border"]["auto"]["dark"]
    return dict(row, focus=fo), info


def _require_format(label, bad):
    """構成表の項目の書式の誤り (mask_problems などの文の list) があれば、「<label>: 誤り、誤り」の ValueError にする。"""
    if bad:
        raise ValueError(f"{label}: " + "、".join(bad))


def scene_values(look, row, font_file=None, badge=None, source_size=None, speed=1.0):
    """構成表の 1 行に付ける Fusion の値 (マスク・ズーム・テロップ・肩テロップ) を Fusion の座標で返す。

    構成表と config から Fusion の値を出す所を、build_fusion (部品を Python で組む) の外のここ 1 か所にするため。
    マスクは構成表の全体の masks と行の masks。構成表のマスクとズーム範囲は素材の画素。source_size (素材の幅, 高さ) で
    タイムラインの画素に直してから Fusion の座標 (左下が 0,0。幅は画面の幅、高さは画面の高さを 1 とした値) にする。
    省略すると素材とタイムラインは同じ解像度とみなす。セーフエリアの警告は直した後の値で判定する。
    マスクの keys は {"frame": 行の頭からの書き出しのフレーム, "comp": 合成の時刻, center, width, height} の list
    (フレームの順。同じフレームは後の値)。speed はクリップの速さの倍率で、合成の時刻 (comp・comp_frames・blend) の換算に使う。
    強さ (pixel_frequency・blur_size・black_rgba) は config の mask をマスクの項目で上書きしたもの。
    badge は肩テロップの文 (省略すると row の badge。"" なら接頭辞だけの肩テロップ)。無い部品は None (masks は空の list)。
    構成表に layout (screen あり) があれば、どの行にも layout の値 (layout_values) が入る。指すマークは画面と一緒に縮む。
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    sw, sh = source_size or (W, H)
    fps = int(look["fps"])
    out = empty_values()
    out["canvas"] = fusion_canvas(sw, sh, W, H)
    for m in list(look.get("masks") or []) + list(row.get("masks") or []):
        _require_format("マスクの書式", mask_problems(m))
        m = source_to_timeline(m, sw, sh, look)
        keys = {fr: _box01(k, W, H) for fr, k in keys_by_frame(m.get("keys"), fps)}
        out["masks"].append({"shape": m.get("shape", "rect"), "type": m.get("type", "blur"), **_box01(m, W, H),
                             "keys": [dict(v, frame=f, comp=comp_frame(f, speed)) for f, v in sorted(keys.items())],
                             **{k: m.get(k, look["mask"][k]) for k in ("pixel_frequency", "blur_size", "black_rgba")}})
    if row.get("focus"):
        _require_format("focus の書式", focus_problems(row["focus"]))
        f = dict(look["focus"]) | row["focus"]
        k = min(W / sw, H / sh) if sw and sh else 1.0
        f = source_to_timeline(f, sw, sh, look)
        keys = {fr: _box01(x, W, H) for fr, x in keys_by_frame(f.get("keys"), fps)}
        # 暗くなる割合 (0〜1) のキー。start から seconds をかけて暗くなる (枠線も同じ)。
        # ramp は合成の時刻、ramp_frames は行の頭からの書き出しのフレーム
        ramp = appear_keys(*appear_frames(row["focus"], look["focus"], "seconds", fps))
        out["focus"] = {**_box01(f, W, H), "dim": float(f["dim"]),
                        # 境目のぼかし (素材の画素) をタイムラインの画素に直し、画面の幅を 1 とした値にする (Fusion の SoftEdge)
                        "soft": float(f["feather"]) * k / W,
                        "keys": [dict(v, frame=fr, comp=comp_frame(fr, speed)) for fr, v in sorted(keys.items())],
                        "border": None, "ramp": [(comp_frame(fr, speed), u) for fr, u in ramp], "ramp_frames": ramp}
        if f.get("border"):
            # 枠線の幅と角の丸みは、書き出しの画面の画素 (ズームの前の大きさ。寄せるとその倍率で太くなる)
            b = dict(look["focus_border"]) | (f["border"] if isinstance(f["border"], dict) else {})
            if b["color"] == AUTO_COLOR:
                raise ValueError("focus の枠線の色 auto は、素材を読んで色を決めてから渡す (common.with_auto_border)")
            out["focus"]["border"] = {"rgb": hex_rgb(b["color"]), "width_px": float(b["width"]),
                                      "radius_px": float(b["radius"]), "glow": bool(b["glow"]),
                                      "glow_blur": float(b["glow_blur"]), "W": W, "H": H}
    if row.get("callout"):
        _require_format("callout の書式", callout_problems(row["callout"]))
        band = caption_band(look, row, font_file) if "dst" not in row["callout"] else None
        out["callout"] = callout_values(look, row["callout"], sw, sh, fps, speed, band)
    z = source_to_timeline(row.get("zoom"), sw, sh, look)
    if z:
        g = zoom_geometry(z, W, H)
        s, (cx, cy) = g["scale"], g["center"]
        u, v = cx / W, 1 - cy / H
        out["zoom"] = {"size": zoom_ends(z, 1.0, s), "x": zoom_ends(z, 0.5, 0.5 + s * (0.5 - u)),
                       "y": zoom_ends(z, 0.5, 0.5 + s * (0.5 - v)), "frames": round(z["seconds"] * fps),
                       "comp_frames": comp_frame(round(z["seconds"] * fps), speed),
                       "filter": fusion_filter(look["zoom"]["filter_method"]), "warning": zoom_safe_area_warning(look, z)}
    if (look.get("layout") or {}).get("screen"):
        out["layout"] = layout_values(look, sw, sh)
    if row.get("point") is not None:
        L = out["layout"]
        if L:   # 画面と一緒に縮む。傾ける向きは画面の真ん中へ
            out["point"] = point_values(look, row["point"], sw, sh, L["size"], L["view"])
        else:
            view = zoom_geometry(z, W, H)["center"] if z and z.get("direction", "in") != "out" else None
            scale = out["zoom"]["size"][1] if out["zoom"] else 1.0
            out["point"] = point_values(look, row["point"], sw, sh, scale, view)
    side = (look.get("layout") or {}).get("side")   # 行に無ければ layout の欄。辞書なら見出しの欄だけ差し替える
    if "side" in row:
        side = merge_side(side, row["side"])
    if side:
        out["side"] = side_values(look, side, font_file)
    if row.get("caption"):
        style = "closing" if row.get("caption_style") == "closing" else None
        c = caption_look(look, style)
        fit = fit_caption(look, row["caption"], font_file, style)
        out["caption"] = {"text": "\n".join(fit["lines"]), "size": fit["fusion_size"], "center": (0.5, fit["bar_center"]),
                          "bar_rgba": c["bar_rgba"], "bar": c.get("bar", True), "name": "Closing" if style else "Caption",
                          "text_rgb": c["text_rgb"], "font_name": c["font_name"], "font_style": c["font_style"],
                          "fit": fit} | caption_window(look, c, fit)
    badge = row.get("badge") if badge is None else badge
    if badge is not None:   # 空の文 ("") は接頭辞だけの肩テロップにする (api_trial.py の元の動き)
        b = look["badge"]
        box = badge_box(look, b["prefix"] + badge, badge_font(look))
        out["badge"] = {"text": b["prefix"] + badge, "size": b["fusion_size"], "center": box["center"],
                        "width": box["width"], "height": box["height"], "corner_radius": b["corner_radius"],
                        "rgba": b["rgba"], "text_rgb": b["text_rgb"], "font_name": b["font_name"],
                        "font_style": b["font_style"], "frames": round(b["seconds"] * fps),
                        "blend": badge_blend_keys(round(b["seconds"] * fps), speed)}
    return out


def _named(tool, name):
    """部品に名前を付ける (Fusion ページのノードの名前。人が探して直し、check_final.py が周りを暗くする部品を探す)。"""
    tool.SetAttrs({"TOOLS_Name": name})
    return tool


def _animate(comp, tool, inp, pairs):
    """tool の入力 inp に BezierSpline をつなぎ、(フレーム, 値) を SetInput(値, 時刻) で入れる (直線で補間。最後の値のまま止まる)。"""
    tool.ConnectInput(inp, comp.BezierSpline())
    for f, v in pairs:
        tool.SetInput(inp, v, f)


def _animate_center(comp, tool, pairs):
    """tool の Center に XYPath をつなぎ、X と Y の BezierSpline に (フレーム, (x, y)) を入れる。"""
    path = comp.XYPath()
    tool.ConnectInput("Center", path)
    _animate(comp, path, "X", [(f, c[0]) for f, c in pairs])
    _animate(comp, path, "Y", [(f, c[1]) for f, c in pairs])


def _ramp(comp, tool, inp, ramp, full):
    """現れる割合のキー ramp [(合成の時刻, 0〜1)] に full を掛けて入れる。キーが 1 つなら動かない値。"""
    if len(ramp) == 1:
        tool.SetInput(inp, ramp[0][1] * full)
    else:
        _animate(comp, tool, inp, [(t, u * full) for t, u in ramp])


def _animate_rect(comp, tool, box):
    """マスクの形を入れる。box の keys ([{comp, center, width, height}]) があれば、位置 (Center) は XYPath、
    大きさ (Width・Height) は BezierSpline のキーフレームにし、無ければ box の center・width・height を値で入れる (set_rect)。"""
    if box["keys"]:
        _animate_center(comp, tool, [(k["comp"], k["center"]) for k in box["keys"]])
        _animate(comp, tool, "Width", [(k["comp"], k["width"]) for k in box["keys"]])
        _animate(comp, tool, "Height", [(k["comp"], k["height"]) for k in box["keys"]])
    else:
        set_rect(tool, box["center"], box["width"], box["height"])
    return tool


def _mask(comp, m, src, n, res):
    """マスク 1 件を src の後ろにつなぐ。四角か楕円のマスクで、モザイク (Resolve FX)・ぼかし (Blur)・黒塗り (Background + Merge) の範囲を切る。

    keys があれば、位置 (Center) は XYPath、大きさ (Width・Height) は BezierSpline のキーフレームにする (_animate_rect)。
    Width は画面の幅、Height は画面の高さを 1 とした値 (実測。docs/scripting-api.md)。
    """
    mk = _animate_rect(comp, _named(comp.AddTool(MASK_SHAPES[m["shape"]], -32768, -32768), f"Mask{n}"), m)
    if m["type"] == "mosaic":
        # Fusion 単体の MosaicBlur は無い。Resolve FX のモザイクを Fusion の部品として置く
        fx = _named(comp.AddTool("ofx.com.blackmagicdesign.resolvefx.MosaicBlur", -32768, -32768), f"Mask{n}Mosaic")
        fx.Source = src
        fx.SetInput("PixelFrequency", float(m["pixel_frequency"]))
    elif m["type"] == "blur":
        fx = _named(comp.AddTool("Blur", -32768, -32768), f"Mask{n}Blur")
        fx.Input = src
        fx.SetInput("XBlurSize", float(m["blur_size"]))
    else:   # black: マスクで切った単色を上に重ねる
        bg = set_fill(_named(comp.AddTool("Background", -32768, -32768), f"Mask{n}Black"), rgba01(m["black_rgba"]))
        bg.EffectMask = mk.Mask
        res.append({"type": m["type"], "shape": m["shape"], "keys": len(m["keys"])})
        return _over(comp, src, bg.Output, f"Mask{n}Over")
    fx.EffectMask = mk.Mask
    res.append({"type": m["type"], "shape": m["shape"], "keys": len(m["keys"])})
    return fx.Output


def _focus(comp, f, src):
    """周りを暗くする: 範囲の外側の明るさを dim 倍にする。start から seconds をかけて暗くなる (ramp)。

    反転した四角のマスク (FocusArea、境目は SoftEdge でぼかす) で切った黒 (FocusShade) を、不透明度 1 − dim で重ねる (FocusDim)。
    黒は色が 0 なので、書き出しの色は 下の画 × (1 − 不透明度) = 下の画 × dim。暗くなる時間は FocusShade の不透明度のキーフレーム。
    ズームの前につなぐので、範囲は素材の座標のままズームと一緒に動き、テロップと肩テロップは暗くならない。
    """
    mk = _animate_rect(comp, _named(comp.AddTool("RectangleMask", -32768, -32768), "FocusArea"), f)
    mk.SetInput("Invert", 1)
    mk.SetInput("SoftEdge", f["soft"])
    bg = set_fill(_named(comp.AddTool("Background", -32768, -32768), "FocusShade"), (0.0, 0.0, 0.0))
    _ramp(comp, bg, "TopLeftAlpha", f["ramp"], 1.0 - f["dim"])
    bg.EffectMask = mk.Mask
    return _over(comp, src, bg.Output, "FocusDim")


def _ring_box(c, w, h, d, W, H, radius_px):
    """中心 c、幅 w・高さ h (Fusion の値) の四角を、d px だけ外へ (負なら内へ) 広げた四角と、角の丸み (CornerRadius)。

    CornerRadius は、短い辺の半分を 1 とした値として直す (radius_px は画素)。
    """
    w2, h2 = max(w + 2 * d / W, 1e-6), max(h + 2 * d / H, 1e-6)
    r = max(radius_px + d, 0.0) / (min(w2 * W, h2 * H) / 2)
    return c, w2, h2, min(r, 1.0)


def _focus_border(comp, f, src):
    """focus の範囲の枠線。外の四角のマスク (FocusBorderOuter) から内の四角 (FocusBorderInner、反転) を
    PaintMode Multiply で抜いた輪で、色 (FocusBorder) を切って重ねる (FocusBorderOver)。glow なら、ぼかした同じ色
    (FocusBorderGlow) を下に重ねる (FocusGlowOver)。暗くなるのと同じ時間で、2 つの Merge の Blend で現れる
    (単色の不透明度を動かしても、色には不透明度が掛からないので現れ方にならない)。ズームの前なので一緒に動く。
    輪の作り方は 21.1.1 の実機で確認した (内のマスクを反転して外のマスクの EffectMask につなぎ、外の PaintMode を Multiply)。
    """
    b = f["border"]
    W, H, d = b["W"], b["H"], b["width_px"] / 2
    outer = _named(comp.AddTool("RectangleMask", -32768, -32768), "FocusBorderOuter")
    inner = _named(comp.AddTool("RectangleMask", -32768, -32768), "FocusBorderInner")
    for m, sign in ((outer, 1), (inner, -1)):
        boxes = ([(k["comp"], _ring_box(k["center"], k["width"], k["height"], sign * d, W, H, b["radius_px"]))
                  for k in f["keys"]] if f["keys"] else
                 [(None, _ring_box(f["center"], f["width"], f["height"], sign * d, W, H, b["radius_px"]))])
        if f["keys"]:
            _animate_center(comp, m, [(t, x[0]) for t, x in boxes])
            _animate(comp, m, "Width", [(t, x[1]) for t, x in boxes])
            _animate(comp, m, "Height", [(t, x[2]) for t, x in boxes])
            # 角の丸みは短い辺に対する割合なので、範囲の大きさが変わるなら key ごとに直す
            _animate(comp, m, "CornerRadius", [(t, x[3]) for t, x in boxes])
        else:
            c, w, h, r = boxes[0][1]
            set_rect(m, c, w, h)
            m.SetInput("CornerRadius", r)
    inner.SetInput("Invert", 1)
    outer.EffectMask = inner.Mask
    outer.SetInput("PaintMode", "Multiply")
    # 色に不透明度は掛からないので不透明 (rgba01 は A が無ければ 1.0)。現れ方は下の Merge の Blend で付ける
    color = set_fill(_named(comp.AddTool("Background", -32768, -32768), "FocusBorder"), rgba01(b["rgb"]))
    color.EffectMask = outer.Mask
    if b["glow"]:
        glow = _named(comp.AddTool("Blur", -32768, -32768), "FocusBorderGlow")
        glow.Input = color.Output
        glow.SetInput("XBlurSize", b["glow_blur"])
        gm = _named(comp.AddTool("Merge", -32768, -32768), "FocusGlowOver")
        gm.Background = src
        gm.Foreground = glow.Output
        _ramp(comp, gm, "Blend", f["ramp"], 1.0)
        src = gm.Output
    mg = _named(comp.AddTool("Merge", -32768, -32768), "FocusBorderOver")
    mg.Background = src
    mg.Foreground = color.Output
    _ramp(comp, mg, "Blend", f["ramp"], 1.0)
    return mg.Output


def _callout(comp, c, image, src):
    """callout を src の上に重ねる。image はマスクを掛けた後の素材の解像度の画 (隠したものは callout でも隠れる)。
    影 (CalloutShadow、ぼかした半透明の黒。CalloutShadowOver の EffectMask に全体の四角を反転したマスク CalloutShadowInner を
    つなぎ、四角の外側だけに置く) → 埋める色 (CalloutFill、全体の四角) → 縁 (CalloutBorder、全体の四角の外側の輪)
    → 切り抜いた画の順に重ね、どれも同じ時刻で現れる (Merge の Blend)。埋める色の不透明度は Merge の Blend に掛ける
    (単色の不透明度は色に掛からないので、黒以外で明るく出ないように)。縁の輪は focus の枠線と同じ作り
    (内のマスク CalloutBorderInner を反転して外のマスク CalloutBorderOuter の EffectMask につなぎ、外の PaintMode を Multiply)。
    切り抜いた画は、切り抜きごとに Crop (CalloutCrop) と、Center と Size で置く Merge (Callout)。
    2 つ目からは名前に番号を付ける (CalloutCrop2・Callout2)。"""
    def box_mask(name, box):
        return set_rect(_named(comp.AddTool("RectangleMask", -32768, -32768), name), box["center"], box["width"], box["height"])

    def layer(name, rgba, mask, blur=None):
        bg = set_fill(_named(comp.AddTool("Background", -32768, -32768), name), rgba01(rgba))
        bg.EffectMask = mask.Mask
        out = bg.Output
        if blur:
            bl = _named(comp.AddTool("Blur", -32768, -32768), name + "Blur")
            bl.Input = out
            bl.SetInput("XBlurSize", blur)
            out = bl.Output
        return out

    def over(name, fg, bg, full=1.0):
        mg = _named(comp.AddTool("Merge", -32768, -32768), name)
        mg.Background = bg
        mg.Foreground = fg
        _ramp(comp, mg, "Blend", c["ramp"], full)
        return mg
    if c["shadow"]:
        sh = c["shadow"]
        shadow = over("CalloutShadowOver", layer("CalloutShadow", sh["rgba"], box_mask("CalloutShadowMask", sh), sh["blur"]),
                      src)
        # 影は四角の外側だけ。四角の内側は、下の画に fill だけが掛かる (影と fill が重なると暗くなりすぎる)
        cut = box_mask("CalloutShadowInner", sh["inner"])
        cut.SetInput("Invert", 1)
        shadow.EffectMask = cut.Mask
        src = shadow.Output
    if c.get("fill"):
        f = c["fill"]
        src = over("CalloutFillOver", layer("CalloutFill", f["rgb"], box_mask("CalloutFillMask", f)), src,
                   f["opacity"]).Output
    if c["border"]:
        b = c["border"]
        outer, inner = box_mask("CalloutBorderOuter", b), box_mask("CalloutBorderInner", b["inner"])
        inner.SetInput("Invert", 1)
        outer.EffectMask = inner.Mask
        outer.SetInput("PaintMode", "Multiply")
        src = over("CalloutBorderOver", layer("CalloutBorder", b["rgb"], outer), src).Output
    for i, p in enumerate(c["pieces"], 1):
        n = "" if i == 1 else str(i)
        crop = _named(comp.AddTool("Crop", -32768, -32768), "CalloutCrop" + n)
        crop.Input = image
        for k, v in zip(("XOffset", "YOffset", "XSize", "YSize"), p["crop"]):
            crop.SetInput(k, v)
        mg = over("Callout" + n, crop.Output, src)
        mg.SetInput("Center", {1: p["center"][0], 2: p["center"][1]})
        mg.SetInput("Size", c["size"])
        src = mg.Output
    return src


def _canvas(comp, cv, src):
    # 素材の縦横の比がタイムラインと違うとき、Fusion の画面を素材の画素のままタイムラインと同じ比に広げる (fusion_canvas)
    crop = _named(comp.AddTool("Crop", -32768, -32768), "Canvas")
    crop.Input = src
    for k, v in (("XSize", cv["width"]), ("YSize", cv["height"]), ("XOffset", cv["x_offset"]), ("YOffset", cv["y_offset"])):
        crop.SetInput(k, v)
    return crop.Output


def _fit_to_canvas(comp, cv):
    # 単色 (Background)・Text+・マスクの部品は、既定では合成の既定の大きさ (Comp.FrameFormat) で描く。既定の大きさは
    # Resolve が素材の大きさに戻す (SetPrefs で広げても、組んだ後に素材の大きさに戻っていた。21.1.1 の実機) ので、
    # 部品ごとに既定に従うのをやめさせ、広げた画面の大きさを入れる。マスクの部品は MaskWidth・MaskHeight が描く大きさで、
    # Width・Height は形の大きさ (画面を 1 とした値) なので触らない
    for t in comp.GetToolList(False).values():
        if t.GetInput("UseFrameFormatSettings") is None:
            continue
        t.SetInput("UseFrameFormatSettings", 0)
        wk, hk = ("MaskWidth", "MaskHeight") if t.GetInput("MaskWidth") is not None else ("Width", "Height")
        t.SetInput(wk, cv["width"])
        t.SetInput(hk, cv["height"])


def _zoom(comp, z, src):
    # Python で作ったアニメーションの部品を Transform の Size と Center につなぐ
    # (Lua の Execute で部品を付ける方法は、時刻つきの値が最後の 1 つしか残らなかった)
    xf = _named(comp.AddTool("Transform", -32768, -32768), "Zoom")
    xf.Input = src
    # 拡大の補間。Resolve の既定の Linear は拡大するとぼけるので、config の zoom.filter_method (既定 Lanczos) を入れる
    xf.SetInput("FilterMethod", z["filter"])
    # 構成表の zoom.direction が "out" なら、ズーム範囲から全体へ引く (scene_values が始めと終わりを入れ替える)
    zf = z["comp_frames"]
    _animate(comp, xf, "Size", [(0, z["size"][0]), (zf, z["size"][1])])
    _animate_center(comp, xf, [(0, (z["x"][0], z["y"][0])), (zf, (z["x"][1], z["y"][1]))])
    return xf.Output


def _caption(comp, cp, src):
    # テロップの背景は画面の幅いっぱい、文字はセーフエリアの幅に収める (fit_caption)。
    # 部品の名前は Caption* (下のテロップ) か Closing* (結びのテロップ)。結びのテロップの背景は config の closing.bar
    n = cp["name"]
    if cp["bar"] and cp["border"]:
        b = cp["border"]
        src = _over(comp, src, solid(comp, b["rgba"], cp["center"], b["width"], b["height"], cp["corner_radius"],
                                     name=n + "Border", hole=(cp["bar_width"], cp["bar_height"])).Output, n + "BorderOver")
    if cp["bar"]:
        bg = solid(comp, cp["bar_rgba"], cp["center"], cp["bar_width"], cp["bar_height"], cp["corner_radius"], name=n + "Bar")
        src = _over(comp, src, bg.Output, n + "BarOver")
    txt = _named(text_tool(comp, cp["text"], cp["size"], cp["center"], cp["font_name"], cp["font_style"], cp["text_rgb"]),
                 n + "Text")
    src = _over(comp, src, txt.Output, n + "Over")
    if cp["mark"]:
        mk = cp["mark"]
        t = _named(text_tool(comp, mk["text"], mk["size"], mk["center"], cp["font_name"], cp["font_style"], cp["text_rgb"]),
                   n + "Mark")
        return _over(comp, src, t.Output, n + "MarkOver")
    return src


def _over(comp, src, fg, name):
    """fg を src の上に重ねる Merge (名前 name) を置き、その Output を返す。Center・Size・Blend を触らない重ね方はこれを使う。"""
    m = _named(comp.AddTool("Merge", -32768, -32768), name)
    m.Background = src
    m.Foreground = fg
    return m.Output


def _point(comp, pt, src, layout=None):
    # 指すマーク。ズームの前に置くので、画と一緒に寄る (point_values)。PointTilt が指の先 (Pivot) を軸に傾ける。
    # layout があれば、マークだけを画面と同じ倍率と位置に動かして (LayoutPoint) から、置いた画面の上に重ねる
    # (画面の四角で切らないので、マークが画面の外にはみ出しても切れない)
    txt = _named(text_tool(comp, pt["text"], pt["size"], pt["center"], pt["font_name"], pt["font_style"], pt["rgb"]),
                 "PointText")
    tilt = _named(comp.AddTool("Transform", -32768, -32768), "PointTilt")
    tilt.Input = txt.Output
    tilt.SetInput("Pivot", {1: pt["pivot"][0], 2: pt["pivot"][1]})
    tilt.SetInput("Angle", pt["angle"])
    out = tilt.Output
    if layout:
        mv = _named(comp.AddTool("Transform", -32768, -32768), "LayoutPoint")
        mv.Input = out
        mv.SetInput("Center", {1: layout["point_center"][0], 2: layout["point_center"][1]})
        mv.SetInput("Size", layout["size"])
        out = mv.Output
    return _over(comp, src, out, "PointOver")


def _layout(comp, L, image, src):
    """layout: 画面を縮めて寄せ、余白を、画面を広げてぼかし暗くしたもので埋める (layout_values)。

    背景: 黒 (LayoutBase) の上に、image (マスクを掛けた後の画) から画面の四角を切り抜き (LayoutBackCrop)、ぼかし
    (LayoutBackBlur。Blur の XBlurSize)、真ん中に comp の全体を覆う倍率で重ねる (LayoutBack。Merge の Size)。
    その上に黒 (LayoutBackShade、不透明度 1 − dim) を重ねて暗くする (LayoutBackDim。明るさは dim 倍)。
    画面: src (focus などを掛けた後の画) から画面の四角を切り抜き (LayoutScreenCrop)、Merge の Center と Size で置く
    (LayoutScreen)。切り抜くので、画面の外 (窓の題名・タスクバー・黒い帯) はどこにも映らない。
    """
    base = set_fill(_named(comp.AddTool("Background", -32768, -32768), "LayoutBase"), (0.0, 0.0, 0.0, 1.0))

    def cut(name, img):
        c = _named(comp.AddTool("Crop", -32768, -32768), name)
        c.Input = img
        for k, v in zip(("XOffset", "YOffset", "XSize", "YSize"), L["screen_crop"]):
            c.SetInput(k, v)
        return c.Output

    bl = _named(comp.AddTool("Blur", -32768, -32768), "LayoutBackBlur")
    bl.Input = cut("LayoutBackCrop", image)
    bl.SetInput("XBlurSize", L["blur"])
    back = _named(comp.AddTool("Merge", -32768, -32768), "LayoutBack")
    back.Background = base.Output
    back.Foreground = bl.Output
    back.SetInput("Center", {1: 0.5, 2: 0.5})
    back.SetInput("Size", L["back_size"])
    back.SetInput("FilterMethod", L["filter"])
    shade = set_fill(_named(comp.AddTool("Background", -32768, -32768), "LayoutBackShade"), (0.0, 0.0, 0.0, 1.0 - L["dim"]))
    dimmed = _over(comp, back.Output, shade.Output, "LayoutBackDim")
    scr = _named(comp.AddTool("Merge", -32768, -32768), "LayoutScreen")
    scr.Background = dimmed
    scr.Foreground = cut("LayoutScreenCrop", src)
    scr.SetInput("Center", {1: L["center"][0], 2: L["center"][1]})
    scr.SetInput("Size", L["size"])
    scr.SetInput("FilterMethod", L["filter"])
    return scr.Output


def _side(comp, sd, src):
    # 解説の欄。layout の余白に、欄ごとに白い縁 (SideBorderN)・板 (SidePanelN)・見出し (SideTitleN)・区切りの線 (SideRuleN)・
    # 中身 (SideTextN) を重ねる。画面に固定 (ズームの後)
    edge = list(sd["border_rgb"][:3]) + [255]
    for i, s in enumerate(sd["sections"], 1):
        b, p = s["border"], s["panel"]
        src = _over(comp, src, solid(comp, edge, b["center"], b["width"], b["height"], sd["corner_radius"],
                                     name=f"SideBorder{i}", hole=(p["width"], p["height"])).Output, f"SideBorderOver{i}")
        src = _over(comp, src, solid(comp, sd["panel_rgba"], p["center"], p["width"], p["height"], sd["corner_radius"],
                                     name=f"SidePanel{i}").Output, f"SidePanelOver{i}")
        if s["title"]:
            t = _named(text_tool(comp, s["title"]["text"], sd["title_size"], s["title"]["center"], sd["font_name"],
                                 sd["font_style"], sd["text_rgb"]), f"SideTitle{i}")
            src = _over(comp, src, t.Output, f"SideTitleOver{i}")
            r = s["rule"]
            src = _over(comp, src, solid(comp, edge, r["center"], r["width"], r["height"], name=f"SideRule{i}").Output,
                        f"SideRuleOver{i}")
        # 中身の字の大きさは欄ごと (幅に収まらない欄だけ縮めてある。side_body_sizes)
        t = _named(text_tool(comp, s["text"]["text"], s["text"]["size"], s["text"]["center"], sd["font_name"],
                             sd["font_style"], sd["text_rgb"], s["text"]["align"]), f"SideText{i}")
        src = _over(comp, src, t.Output, f"SideTextOver{i}")
    return src


def _badge(comp, bd, src):
    # セーフエリアの右上の角を基準に置き、最初の badge.seconds 秒だけ出す (Merge の Blend を隣り合うフレームで切り替え)
    bb = solid(comp, bd["rgba"], bd["center"], bd["width"], bd["height"], bd["corner_radius"], name="BadgeBox",
               mask_name="BadgeMask")
    bt = _named(text_tool(comp, bd["text"], bd["size"], bd["center"], bd["font_name"], bd["font_style"], bd["text_rgb"]),
                "BadgeText")
    label = _over(comp, bb.Output, bt.Output, "BadgeLabel")
    m4 = _named(comp.AddTool("Merge", -32768, -32768), "BadgeOver")
    m4.Background = src
    m4.Foreground = label
    # 書き出しの frames - 1 フレーム目までは 1、frames フレーム目で 0 (badge_blend_keys)
    _animate(comp, m4, "Blend", bd["blend"])
    return m4.Output


def build_fusion(item, values):
    """クリップに Fusion の合成を追加し、部品を Python で置いて組む。values は scene_values の値。無い部品は置かない。

    つなぐ順は マスク (書いた順) → 周りを暗くする (と枠線) → 指すマーク → ズーム → callout → テロップの背景と文字 → 解説の欄
    → 肩テロップ (マスクと暗くする範囲はズームと一緒に動き、テロップと肩テロップは画面に固定される)。
    layout があれば、ズームの代わりに画面を切り抜いて置き、余白をぼかした背景で埋める (_layout)。指すマークは置いた画面の
    上に、画面と同じ倍率と位置で重ねる。部品には決まった名前を付ける (Zoom、CaptionText、BadgeOver など。
    マスクは Mask1、Mask1Mosaic など通し番号。人が Fusion ページで探し、check_final.py が読む)。comp.Lock() は使わない (Lock の中で入れた値は書き出しに出ない。実測)。
    返す値は (成否, 詳細)。合成を追加できなければ成否は False。
    """
    comp = item.AddFusionComp()
    if not comp:
        return False, {"comp": False}
    res = {"comp": True, "masks": []}
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    cur = mi.Output
    if values.get("canvas"):
        cur = _canvas(comp, values["canvas"], cur)
        res["canvas"] = [values["canvas"]["width"], values["canvas"]["height"]]
    for n, m in enumerate(values["masks"], 1):
        cur = _mask(comp, m, cur, n, res["masks"])
    masked = cur   # callout はマスクを掛けた後の画から切り抜く
    if values.get("focus"):
        cur = _focus(comp, values["focus"], cur)
        res["focus"] = True
        if values["focus"].get("border"):
            cur = _focus_border(comp, values["focus"], cur)
    lay = values.get("layout")
    if values.get("point") and not lay:
        cur = _point(comp, values["point"], cur)
    if values["zoom"]:
        cur = _zoom(comp, values["zoom"], cur)
        res["zoom_warning"] = values["zoom"]["warning"]
    if lay:
        cur = _layout(comp, lay, masked, cur)
        res["layout"] = True
        if values.get("point"):
            cur = _point(comp, values["point"], cur, lay)
    if values.get("callout"):
        cur = _callout(comp, values["callout"], masked, cur)
    if values["caption"]:
        cur = _caption(comp, values["caption"], cur)
        res["caption_warnings"] = values["caption"]["fit"]["warnings"]
    if values.get("side"):
        cur = _side(comp, values["side"], cur)
    if values["badge"]:
        cur = _badge(comp, values["badge"], cur)
    if values.get("canvas"):
        _fit_to_canvas(comp, values["canvas"])
    mo.Input = cur
    return True, res
