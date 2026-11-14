"""構成表の書式の点検と、構成表に書ける項目 (scripts/common.py から読み込み直す部品)。

マスク・focus・callout・side・layout・point の書式の誤りの文 (mask_problems・focus_problems・callout_problems・side_sections・
merge_side・layout_problems・point_problems)、構成表に書ける項目の形 (ROW_KEYS・STORYBOARD_KEYS・storyboard_keys) と
知らない項目の点検 (unknown_keys)、構成表と config の名前を Fusion の値にする対応 (MASK_SHAPES・FUSION_FILTERS・fusion_filter・hex_rgb)。
"""
from settings import config


# Transform の FilterMethod の番号と名前 (Fusion のルール。既定は 2 の Linear)
FUSION_FILTERS = ["Nearest", "Box", "Linear", "Quadratic", "Cubic", "Catmull-Rom", "Gaussian", "Mitchell",
                  "Lanczos", "Sinc", "Bessel"]


def fusion_filter(name_or_index):
    """config の zoom.filter_method (名前か番号) を FilterMethod の番号にする。"""
    if isinstance(name_or_index, int):
        return name_or_index
    return FUSION_FILTERS.index(name_or_index)


MASK_TYPES = ("mosaic", "blur", "black")
MASK_SHAPES = {"rect": "RectangleMask", "ellipse": "EllipseMask"}   # 構成表の shape と Fusion のマスクの部品


def mask_problems(m):
    """構成表のマスク 1 件の書式の誤り (文の list。無ければ空)。"""
    out = []
    if m.get("type", "blur") not in MASK_TYPES:
        out.append(f"type {m.get('type')!r} は {'・'.join(MASK_TYPES)} のどれか")
    if m.get("shape", "rect") not in MASK_SHAPES:
        out.append(f"shape {m.get('shape')!r} は rect か ellipse")
    for b in [m] + list(m.get("keys") or []):
        if not all(isinstance(b.get(k), (int, float)) for k in "xywh") or b["w"] <= 0 or b["h"] <= 0:
            out.append("x y w h は数で、w と h は 0 より大きい")
            break
    if any(not isinstance(k.get("t"), (int, float)) or k["t"] < 0 for k in m.get("keys") or []):
        out.append("keys の t は 0 以上の秒")
    return out


def hex_rgb(s):
    """"#FFC800" を 0〜255 の [R, G, B] に。読めなければ ValueError。"""
    s = str(s).lstrip("#")
    if len(s) != 6:
        raise ValueError(f"色は #RRGGBB: {s}")
    return [int(s[i:i + 2], 16) for i in (0, 2, 4)]


AUTO_COLOR = "auto"   # 枠線の色を素材の明るさで決めることを示す値 (focus.border.color、config の focus_border.color)


def focus_problems(f):
    """構成表の focus (周りを暗くする) の書式の誤り (文の list)。範囲と keys はマスクと同じルール。"""
    out = [x for x in mask_problems({k: v for k, v in f.items() if k not in ("type", "shape", "border")})]
    b = f.get("border")
    if b is not None and b is not True:
        if not isinstance(b, dict):
            out.append("border は true か {color, width, radius, glow}")
        else:
            try:
                if "color" in b and b["color"] != AUTO_COLOR:
                    hex_rgb(b["color"])
            except ValueError:
                out.append("border.color は #RRGGBB か auto")
            for k in ("width", "radius"):
                if k in b and (not isinstance(b[k], (int, float)) or b[k] < 0 or (k == "width" and b[k] == 0)):
                    out.append(f"border.{k} は 0 以上の数" + (" (width は 0 より大きい)" if k == "width" else ""))
    for k in ("dim", "feather", "seconds", "start"):
        if k in f and (not isinstance(f[k], (int, float)) or f[k] < 0 or (k == "dim" and f[k] > 1)):
            out.append(f"{k} は 0 以上の数" + (" で 1 以下" if k == "dim" else ""))
    return out


def callout_sources(c):
    """callout の src を四角の list にする ({x, y, w, h} 1 つでも、その list でもよい)。書いた順。"""
    s = c.get("src")
    return [s] if isinstance(s, dict) else list(s) if isinstance(s, list) else []


def callout_order(c):
    """callout の切り抜きを上から重ねる順 (src の番号。1 から)。order が無ければ書いた順。"""
    o = c.get("order")
    return list(o) if isinstance(o, list) else list(range(1, len(callout_sources(c)) + 1))


def callout_shown(c):
    """callout の切り抜きを、上から重ねる順 (callout_order) に並べた四角の list。"""
    srcs = callout_sources(c)
    return [srcs[i - 1] for i in callout_order(c)]


def callout_problems(c):
    """構成表の callout (拡大の重ね表示) の書式の誤り (文の list)。dst は省略できる (callout_auto_dst で置く)。
    dst が無く、知らない項目 (dts・dest など) があれば、dst の書き間違いとみなして誤りにする (黙って自動の置き場所にしない)。"""
    out = []
    s, d = c.get("src"), c.get("dst")
    if "dst" not in c:
        odd = [k for k in c if not str(k).startswith("_") and k not in ROW_KEYS["callout"]]
        if odd:
            out.append(f"dst が無く、知らない項目 {'・'.join(map(str, odd))} がある (dst の書き間違いでないか。"
                       "自動の置き場所にするなら消す)")
    if (not isinstance(s, (dict, list)) or not callout_sources(c)
            or not all(isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in "xywh")
                       and b["w"] > 0 and b["h"] > 0 for b in callout_sources(c))):
        out.append("src は {x, y, w, h} か、その list (素材の画素。w と h は 0 より大きい)")
    if "dst" in c and (not isinstance(d, dict) or not all(isinstance(d.get(k), (int, float)) for k in ("x", "y", "w"))
                       or d["w"] <= 0):
        out.append("dst は {x, y, w} (書き出しの画面の画素。w は 0 より大きい)。省略すると自動の置き場所")
    if "order" in c:
        o, n = c["order"], len(callout_sources(c))
        if (not isinstance(o, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in o)
                or sorted(o) != list(range(1, n + 1))):
            out.append(f"order は src の番号 (1〜{n}) を 1 回ずつ並べた list (例: {list(range(n, 0, -1)) or [1]})")
    for k in ("in", "start", "gap"):
        if k in c and (not isinstance(c[k], (int, float)) or c[k] < 0):
            out.append(f"{k} は 0 以上の" + ("数 (書き出しの画面の画素)" if k == "gap" else "秒"))
    b = c.get("border")
    if isinstance(b, dict):
        try:
            if "color" in b:
                hex_rgb(b["color"])
        except ValueError:
            out.append("border.color は #RRGGBB")
        if "width" in b and (not isinstance(b["width"], (int, float)) or b["width"] <= 0):
            out.append("border.width は 0 より大きい数")
    elif b not in (None, True, False):
        out.append("border は true / false か {color, width}")
    f = c.get("fill")
    if isinstance(f, dict):
        try:
            if "color" in f:
                hex_rgb(f["color"])
        except ValueError:
            out.append("fill.color は #RRGGBB")
        if "opacity" in f and (not isinstance(f["opacity"], (int, float)) or not 0 <= f["opacity"] <= 1):
            out.append("fill.opacity は 0〜1 の数")
    elif f not in (None, True, False):
        out.append("fill は true / false か {color, opacity}")
    return out


def is_num(v):
    """数か (True / False は数に数えない)。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# layout.anchor: 縮めた画面を左右のどこに寄せるか (出力の幅から画面の幅と左右の余白を引いた残りの割合)
LAYOUT_ANCHORS = {"top-left": 0.0, "top": 0.5, "top-right": 1.0}


def layout_problems(screen, height, anchor, margin, bg):
    """layout の書式の誤り (文の list)。height・anchor・margin・bg ({blur, dim}) は既定を重ねた値。"""
    out = []
    if not (isinstance(screen, dict) and all(is_num(screen.get(k)) for k in "xywh") and screen["w"] > 0 and screen["h"] > 0):
        out.append("screen は {x, y, w, h} (素材の画素、w h は 0 より大きい)")
    if not (is_num(height) and 0 < height <= 1):
        out.append("height は 0 より大きく 1 以下 (出力の高さに対する画面の高さ)")
    if anchor not in LAYOUT_ANCHORS:
        out.append(f"anchor は {'・'.join(LAYOUT_ANCHORS)} のどれか")
    if not (is_num(margin) and margin >= 0):
        out.append("margin は 0 以上の px")
    if not (is_num(bg.get("blur")) and bg["blur"] >= 0):
        out.append("background.blur は 0 以上の数 (Fusion の Blur の XBlurSize)")
    if not (is_num(bg.get("dim")) and 0 <= bg["dim"] <= 1):
        out.append("background.dim は 0〜1 (背景の明るさの倍率。0 で真っ黒)")
    return out


# point.from (マークを指す点のどちら側に置くか) と、指す向き
POINT_FROM = {"below": "up", "above": "down", "left": "right", "right": "left"}


def point_problems(p):
    """指すマーク (config の point に行の point を重ねたもの) の書式の誤り (文の list)。point.glyphs は見ない。"""
    out = []
    if not all(is_num(p.get(k)) for k in "xy"):
        out.append("x y は数 (素材の画素)")
    if not isinstance(p.get("from"), str) or p["from"] not in POINT_FROM:
        out.append(f"from は {'・'.join(POINT_FROM)} のどれか")
    if not is_num(p.get("tilt")) or not 0 <= p["tilt"] <= 90:
        out.append("tilt は 0〜90 の度")
    fonts = p.get("fonts") if isinstance(p.get("fonts"), dict) else {}
    if not isinstance(p.get("style"), str) or p["style"] not in fonts:
        out.append(f"style は {'・'.join(fonts)} のどれか")
    if not is_num(p.get("size")) or p["size"] <= 0:
        out.append("size は 0 より大きい px")
    return out


# ---------- 構成表に書ける項目 ----------
# 構成表の項目の形。dict はその下の項目 (list で書く項目は、list の各要素の項目)、None は値そのもの。
# 全体の項目は STORYBOARD_KEYS に、config の storyboard の全部の節 (構成表で上書きできる見た目と出力の値) を追加したもの
# (storyboard_keys)。sb2resolve.py の点検 (知らない項目の警告、unknown_keys) と、機能の一覧のテスト
# (tests/offline/test_features_catalog.py) が使う。読む項目を追加したら、ここにも追加する。
_BOX = dict.fromkeys(("x", "y", "w", "h"))
_MOVES = {"t": None, **_BOX}
MASK_KEYS = {**_BOX, "type": None, "shape": None, "keys": _MOVES,
             "pixel_frequency": None, "blur_size": None, "black_rgba": None}
ROW_KEYS = {
    "start": None, "end": None, "seconds": None, "source": None,
    "caption": None, "caption_style": None, "badge": None,
    "masks": MASK_KEYS,
    "zoom": {**_BOX, "seconds": None, "direction": None},
    "focus": {**_BOX, "dim": None, "feather": None, "start": None, "seconds": None, "keys": _MOVES,
              "border": dict.fromkeys(("color", "width", "radius", "glow", "glow_blur"))},
    "hold": {"seconds": None, "at": None},
    "point": dict.fromkeys(("x", "y", "from", "tilt", "style", "size", "color")), "side": None,
    "callout": {"src": _BOX, "dst": dict.fromkeys(("x", "y", "w")), "order": None,
                "fill": dict.fromkeys(("color", "opacity")), "border": dict.fromkeys(("color", "width")),
                "shadow": None, "shadow_rgba": None, "shadow_offset": None, "shadow_blur": None,
                "in": None, "start": None, "gap": None},
}
LAYOUT_KEYS = {"screen": _BOX, "height": None, "anchor": None, "margin": None, "side": None,
               "background": dict.fromkeys(("blur", "dim"))}
STORYBOARD_KEYS = {"source": None, "output": None, "project": None, "rows": ROW_KEYS, "masks": MASK_KEYS,
                   "layout": LAYOUT_KEYS}
# 結びのテロップは caption に closing を重ねて作る (caption_look) ので、closing には caption の項目も書ける。
# ただし位置 (position・lift・band) は中央に決まり、字の大きさは closing.size から決まる (fusion_size は使わない)
_CLOSING_FIXED = ("position", "lift", "fusion_size", "band")


def _shape(v):
    return {k: _shape(x) for k, x in v.items()} if isinstance(v, dict) else None


def storyboard_keys(defaults=None):
    """構成表に書ける項目の形 (全体)。defaults は config/defaults.json の中身 (省略すると読み込んだ設定)。"""
    look = _shape((defaults or config().defaults)["storyboard"])
    look["closing"] = {**{k: v for k, v in look["caption"].items() if k not in _CLOSING_FIXED}, **look["closing"]}
    return {**look, **STORYBOARD_KEYS}


def key_paths(shape, prefix=""):
    """項目の形の、全部の項目の名前 ("rows.focus.border.color" のように . でつなぐ) と、下に項目が無いかの [(名前, 葉か)]。"""
    out = []
    for k, v in shape.items():
        p = prefix + k
        out.append((p, not isinstance(v, dict)))
        if isinstance(v, dict):
            out += key_paths(v, p + ".")
    return out


def unknown_keys(sb, shape=None, local=None):
    """構成表 (読み込んで設定を重ねた後でもよい) の知らない項目の list ("3 行目の focus.bordr"、"全体の captoin")。
    _ で始まる項目 (メモなど) は見ない。値が dict でも list でもない所 (border: true など) はその下を見ない。
    local (config/local.json の storyboard) を渡すと、そこにもある知らない項目は「config/local.json の storyboard の captoin」と出す
    (設定を重ねた後の構成表では、構成表に書いたものと local.json から来たものの区別が付かないため)。"""
    shape = shape or storyboard_keys()
    if local:
        mine = set(unknown_keys(local, shape))
        return [f"config/local.json の storyboard の {k[len('全体の '):]}" if k in mine else k
                for k in unknown_keys(sb, shape)]
    out = []

    def walk(v, sh, where, label):
        for x in (v if isinstance(v, list) else [v]):
            if not isinstance(x, dict):
                continue
            for k, y in x.items():
                if str(k).startswith("_"):
                    continue
                p = f"{where}.{k}" if where else str(k)
                if k not in sh:
                    out.append(f"{label}{p}")
                elif isinstance(sh[k], dict):
                    walk(y, sh[k], p, label)
    walk({k: v for k, v in sb.items() if k != "rows"}, shape, "", "全体の ")
    for n, r in enumerate(sb.get("rows") or [], 1):
        walk(r, shape["rows"], "", f"{n} 行目の ")
    return out


def unknown_key_warnings(sb):
    """読み込んだ構成表の知らない項目の警告の文の list (sb2resolve.py・check_final.py・sb2nle.py が同じ文で出す)。"""
    return [f"知らない項目 {k} (読まない。書き間違いでないか確認する。項目は docs/features.md)"
            for k in unknown_keys(sb, local=config().local.get("storyboard"))]


def side_sections(side):
    """layout.side / 行の side を、欄の list ([{title, lines, align}]) にする。文 1 つは見出しの無い欄 1 つ。書式の誤りは ValueError。
    align (left・center) を書かなければ、2 行以上の中身は左寄せ、1 行は中央。"""
    secs = [{"text": side}] if isinstance(side, str) else side
    if not isinstance(secs, list) or not secs or not all(
            isinstance(s, dict) and isinstance(s.get("text"), str) and isinstance(s.get("title", ""), str)
            and s.get("align", "left") in ("left", "center") and set(s) <= {"title", "text", "align"} for s in secs):
        raise ValueError("side は文か、{title, text, align} の list (title と align は省ける。align は left か center)")
    out = []
    for s in secs:
        lines = s["text"].replace("|", "\n").split("\n")
        out.append({"title": s.get("title") or None, "lines": lines,
                    "align": s.get("align") or ("left" if len(lines) > 1 else "center")})
    return out


def merge_side(base, side):
    """行の side を、全体の欄 (layout.side。base) と合わせた side にする (side_values と side_sections に渡す形)。
    - 文か list (と None) は、その行の欄を全部差し替える。そのまま返す
    - 辞書 ({見出し: 中身}) は、base の欄のうち見出し (title) が同じ欄の text だけを差し替える。ほかの欄・並び・title は
      そのまま。align は base の欄に書いてあれば残し、無ければ差し替えた中身の行数で決まる (side_sections)
    書式の誤り (base が list でない・知らない見出し・中身が文でない・空の辞書) は ValueError。
    行に side が無いときは base をそのまま使う (呼び出し元が決める。scene_values)。"""
    if not isinstance(side, dict):
        return side
    if not side:
        raise ValueError("side の辞書が空 (差し替える欄の見出しと中身を書く)")
    if not isinstance(base, list):
        raise ValueError("side を見出しごとに書くには、layout.side に {title, text} の list が要る")
    titles = [s.get("title") for s in base if isinstance(s, dict)]
    unknown = [k for k in side if k not in titles]
    if unknown:
        raise ValueError(f"side の見出し {'・'.join(map(str, unknown))} は layout.side に無い")
    bad = [k for k, v in side.items() if not isinstance(v, str)]
    if bad:
        raise ValueError(f"side の {'・'.join(bad)} の中身は文 (| で改行)")
    return [dict(s, text=side[s["title"]]) if isinstance(s, dict) and s.get("title") in side else s for s in base]
