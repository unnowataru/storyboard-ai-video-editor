"""配置の計算 (scripts/common.py から読み込み直す部品)。

素材の画素からタイムラインの画素への換算 (source_to_timeline・fusion_canvas)、セーフエリア (safe_area)、テロップの折り返しと
収め方 (wrap_caption_line・fit_caption・caption_window)、テロップの行の読点 (caption_comma)、肩テロップの板 (badge_box)、callout の置き場所と値 (callout_auto_dst・
callout_values)、layout の画面の置き方 (apply_layout・layout_values)、指すマーク (point_values)、解説の欄 (side_values)。
文の幅は書体のファイルを Pillow で測る (text_width)。
"""
import math
import re
import sys

from resolve import appear_frames, appear_keys, comp_frame
from schema import (LAYOUT_ANCHORS, POINT_FROM, callout_order, callout_shown, fusion_filter, hex_rgb, layout_problems,
                    merge_side, point_problems, side_sections)
from settings import config


# ---------- 配置 (セーフエリア・テロップ・肩テロップ・ズーム) ----------
def parse_aspect(a):
    """'3:2' / '16:9' / 1.5 を数に。"""
    if isinstance(a, (int, float)):
        return float(a)
    w, h = str(a).split(":")
    return float(w) / float(h)


def source_to_timeline(box, src_w, src_h, look):
    """素材の画素 (左上が 0,0) で書いた四角 (x, y, w, h) を、タイムラインの画素に直す。ほかの項目はそのまま残す。

    構成表のマスクとズーム範囲は素材の画素で書く (docs/storyboard.md)。keys があれば、各 key の x, y, w, h も直す。
    素材はタイムラインに全体を収めて置く (Resolve の既定) 前提で、倍率は縦横の小さいほう、余りは上下か左右に均等。
    素材とタイムラインが同じ解像度なら値は変わらない。素材の大きさが分からないとき (0 か None) もそのまま返す。
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    if not box or not src_w or not src_h:
        return box
    k = min(W / src_w, H / src_h)
    ox, oy = (W - src_w * k) / 2, (H - src_h * k) / 2

    def conv(b):
        return dict(b) | {"x": b["x"] * k + ox, "y": b["y"] * k + oy, "w": b["w"] * k, "h": b["h"] * k}
    out = conv(box)
    if box.get("keys"):
        out["keys"] = [conv(x) for x in box["keys"]]
    return out


def fusion_canvas(src_w, src_h, W, H):
    """素材の縦横の比がタイムライン (W x H) と違うときの、Fusion の画面の大きさと素材の置き場所。比が同じか、大きさが分からなければ None。

    クリップの Fusion の合成の画面は素材の大きさになる (Studio 21.1.1 の実機、横長の素材で確認)。部品の位置と大きさは
    タイムラインの画素から画面を 1 とした値に直しているので、比が違うと合わない (ズームで上下が切れ、テロップが絵の帯の下に付く)。
    そこで画面を素材の画素のまま (拡大も縮小もしない) タイムラインと同じ比に広げ、素材を真ん中に置く (余りは黒)。
    広げた画面は source_to_timeline の「全体を収め、余りは上下か左右に均等」と同じ置き方なので、部品の値はそのまま合う。
    返す値の x_offset・y_offset は Fusion の Crop に渡すずらし (素材の左下からの画素。広げる分は負)。
    """
    if not src_w or not src_h:
        return None
    if src_w * H > src_h * W:   # 素材がタイムラインより横長: 幅は素材のまま、高さを足す
        cw, ch = src_w, round(src_w * H / W)
    else:
        cw, ch = round(src_h * W / H), src_h
    if (cw, ch) == (src_w, src_h):
        return None
    return {"width": cw, "height": ch, "x_offset": -(cw - src_w) / 2, "y_offset": -(ch - src_h) / 2}


def safe_area(look):
    """書き出しの画面の中で、大事なものを収める中央の範囲 (px)。比率 (safe_area.aspect) から計算する。

    比率を指定しないとき (null) は、タイムラインの比率 (画面の全体) になる。
    画面より横長の比率を指定したときは、上下を外した範囲になる。
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    a = parse_aspect((look.get("safe_area") or {}).get("aspect") or W / H)
    if a <= W / H:
        w = round(H * a)
        x0 = (W - w) // 2
        return {"x0": x0, "x1": x0 + w, "y0": 0, "y1": H, "width": w, "height": H, "W": W, "H": H}
    h = round(W / a)
    y0 = (H - h) // 2
    return {"x0": 0, "x1": W, "y0": y0, "y1": y0 + h, "width": W, "height": h, "W": W, "H": H}


_FONTS = {}


def text_width(font_file, size, text):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    if not font_file:
        return None
    f = _FONTS.get((font_file, size))
    if f is None:
        f = _FONTS[(font_file, size)] = ImageFont.truetype(font_file, size)   # 折り返しで何度も測るので覚えておく
    return f.getlength(text)


def caption_font(look):
    """テロップの書体のファイル (caption.font_name と font_style。無ければ None)。テロップの幅を測るのに使う。"""
    c = look["caption"]
    return config().font_file(c["font_name"], c.get("font_style"))


def badge_font(look):
    """肩テロップの書体のファイル (badge.font_name と font_style)。板の幅を測るのに使う。"""
    b = look["badge"]
    return config().font_file(b["font_name"], b.get("font_style"))


def caption_look(look, style=None):
    """テロップの見た目。style が "closing" (結びのテロップ) なら caption に closing を重ね、画面の中央に置く。"""
    c = dict(look["caption"])
    if style == "closing":
        c |= look["closing"]
        c.update(position="center", lift=0)
    return c


# テロップの折り返しのルール (日本語の禁則)。行の頭に置かない字と、行の末に置かない字
CAPTION_NO_LINE_START = set("、。，．,.・：:；;？?！!‼⁇⁈⁉ー〜～…‥）)」』】〕〉》］]｝}”’\"'%％"
                            "ゝゞヽヾ々〻ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ")
CAPTION_NO_LINE_END = set("（(「『【〔〈《［[｛{“‘")
_CAPTION_BREAK_AFTER = set("、。，．,！!？?")
_CAPTION_CLOSE = set("）)」』】〕〉》］]｝}”’")
# 途中で切らない英数字の語 (Python、x86-64、3.13、192.0.2.10、10:30 など。記号は英数字に挟まれたときだけ語の中とみなす)
_CAPTION_WORD = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]+(?:[-._:/+@#&'’][0-9A-Za-z０-９Ａ-Ｚａ-ｚ]+)*")


# テロップの 1 行の中の読点 (「、」「，」と、数字に挟まれていない「,」。1,000 のような桁の区切りは読点にしない)
_CAPTION_COMMA = re.compile(r"[、，]|(?<![0-9０-９]),|,(?![0-9０-９])")


def caption_comma(line):
    """テロップの 1 行 (`|` か改行で区切った行) に読点があるか。fit_caption が警告に使う。"""
    return bool(_CAPTION_COMMA.search(line))


def _char_kind(ch):
    o = ord(ch)
    if ch.isspace():
        return "space"
    if ch in "々〆ヶ〇" or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
        return "kanji"
    if 0x3041 <= o <= 0x309F:
        return "hira"
    if 0x30A0 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9F:
        return "kata"
    if (ch.isascii() and ch.isalnum()) or 0xFF10 <= o <= 0xFF19 or 0xFF21 <= o <= 0xFF3A or 0xFF41 <= o <= 0xFF5A:
        return "alnum"
    return "other"


def _break_score(text, i):
    """text[:i] と text[i:] の間で折り返すときの点 (大きいほど切りやすい)。禁則に当たるなら None。"""
    a, b = text[i - 1], text[i]
    if b in CAPTION_NO_LINE_START or a in CAPTION_NO_LINE_END:
        return None
    if a.isspace() or b.isspace() or a in _CAPTION_BREAK_AFTER:
        return 100
    if a in _CAPTION_CLOSE:
        return 80
    if b in CAPTION_NO_LINE_END:
        return 70
    ka, kb = _char_kind(a), _char_kind(b)
    if ka == "hira" and kb in ("kanji", "kata", "alnum"):
        # 「お使い」「ご確認」の接頭の「お」「ご」の後では切らない
        if a in "おご" and (i < 2 or _char_kind(text[i - 2]) != "hira"):
            return 0
        # 漢字に挟まれた 1 字のかなは、助詞でなければ送り仮名とみて切りにくくする (「折り|返し」「書き|出し」)
        if kb == "kanji" and i >= 2 and _char_kind(text[i - 2]) == "kanji" and a not in "のをはがにでとへもやか":
            return 10
        return 60   # 助詞・送り仮名の後 (「テロップは|縮めて」)
    if ka != kb and ka in ("kanji", "kata", "alnum") and kb in ("kanji", "kata", "alnum"):
        return 30
    return 0


def _break_penalty(text, i, inner):
    """i の位置で折り返すときの悪さ。英数字の語の途中は 400、禁則に当たるなら 1000。"""
    if i in inner:
        return 400
    s = _break_score(text, i)
    return 1000 if s is None else 110 - s


def wrap_caption_line(text, fits, max_lines=None):
    """テロップの 1 行 (`|` や改行で区切った後の行) を、fits(文) が真になる幅に折り返す。形態素解析は使わない。

    折り返す位置ごとに悪さを決め、行の切り方の全部の組み合わせから、行数が max_lines 以内で、悪さの合計が最も小さく、
    次に行数が少ないものを選ぶ。max_lines 以内に収まらないときは、行数を減らすより悪さの合計を小さくする
    (言葉を切らない。上限を超えた警告は fit_caption が出す)。悪さは小さい順に:
    空白・句読点の後 < 閉じ括弧の後 < 開き括弧の前 < ひらがなから漢字・カタカナ・英数字に変わる所 (助詞や送り仮名の後)
    < 漢字・カタカナ・英数字の間の変わり目 < 漢字に挟まれた助詞でない 1 字のかなの後 (「折り|返し」)
    < それ以外 (言葉の途中になりやすい)
    < 英数字の語 (Python、x86-64、IP アドレスなど) の途中 < 禁則 (行の頭に句読点・閉じ括弧・小書きの仮名・長音など、
    行の末に開き括弧) に当たる所。一つの語が幅を超えるときだけ、語の途中や禁則に当たる所で切る。
    """
    if fits(text):
        return [text]
    n = len(text)
    inner = {i for m in _CAPTION_WORD.finditer(text) for i in range(m.start() + 1, m.end())}
    pen = [0] + [_break_penalty(text, i, inner) for i in range(1, n)] + [0]
    reach = []   # reach[j]: text[j:i] が収まる i の一覧 (幅は文が長いほど広いので、収まらなくなったら止める)
    for j in range(n):
        r = []
        for i in range(j + 1, n + 1):
            seg = text[j:i].strip()
            if not seg:
                continue
            if not fits(seg):
                if not r and i == j + 1:
                    r.append(i)   # 1 字でも収まらない: その字だけで 1 行にする
                break
            r.append(i)
        reach.append(r)
    dp = [{} for _ in range(n + 1)]   # dp[i][行数] = (悪さの合計, 前の位置)
    dp[0][0] = (0, None)
    for j in range(n):
        for k, (p, _) in list(dp[j].items()):
            for i in reach[j]:
                cost = p + pen[i]
                cur = dp[i].get(k + 1)
                if cur is None or cost < cur[0] or (cost == cur[0] and j > cur[1]):
                    dp[i][k + 1] = (cost, j)
    limit = max_lines or n

    def key(k):
        return (k > limit, dp[n][k][0], k)
    k = min(dp[n], key=key)
    out, i = [], n
    while i:
        j = dp[i][k][1]
        out.append(text[j:i].strip())
        i, k = j, k - 1
    return out[::-1]


def layout_background(sb):
    """背景 (画面を広げてぼかし、暗くしたもの) の強さ {blur, dim}。config の layout_background に構成表の layout.background を重ねる。
    構成表の全体の layout_background も config と同じく上書きできる (load_storyboard が重ねる)。"""
    base = sb.get("layout_background") or config().defaults["storyboard"]["layout_background"]
    return dict(base) | dict((sb.get("layout") or {}).get("background") or {})


def layout_place(sb):
    """layout の画面の置き方 {height, anchor, margin}。構成表の layout の値、無ければ config の layout_place
    (storyboard_values が defaults.json → local.json → 構成表の全体の layout_place の順に重ねた値)。
    組む側 (layout_geometry) と照らし合わせ (check_final.py) が同じ既定を読む。"""
    lay = sb.get("layout") or {}
    base = sb["layout_place"]
    return {k: lay.get(k, base[k]) for k in ("height", "anchor", "margin")}


def layout_geometry(sb):
    """layout の画面を置く場所 (出力の画素)。返す値は {x0, y0, w, h, k, anchor, margin}。
    k は素材の 1 画素が出力の何画素になるか (高さ height × 出力の高さ ÷ screen.h)。左上 (x0, y0) は y0 = margin。
    書式は apply_layout が読み込みのときに確認する。縮めた画面が出力に収まらなければ sys.exit。"""
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    sc = sb["layout"]["screen"]
    height, anchor, margin = map(layout_place(sb).__getitem__, ("height", "anchor", "margin"))
    k = height * H / sc["h"]           # 素材の 1 画素が出力の何画素か
    w, h = sc["w"] * k, height * H
    if w + 2 * margin > W or h + margin > H:
        sys.exit("layout: 縮めた画面が出力に収まらない (height か margin を小さくする)")
    x0 = margin + (W - w - 2 * margin) * LAYOUT_ANCHORS[anchor]
    return {"x0": x0, "y0": margin, "w": w, "h": h, "k": k, "anchor": anchor, "margin": margin}


def apply_layout(sb):
    """構成表の layout を確認し、テロップ・肩テロップ・解説の欄の置き場所を決める。画面 (layout.screen、素材の画素) を
    出力の高さの layout.height に縮めて上に寄せ (anchor が top-left なら左上、top-right なら右上、top なら上の真ん中。
    端から margin px)、空いた余白にテロップと肩テロップを出す。

    - 画面の置き方 (Fusion の値) は scene_values が layout_values で行ごとに作る。余白は、画面を出力いっぱいに広げて
      ぼかし、暗くしたもので埋める (窓の題名・タスクバー・黒い帯はどこにも映らない)。行の zoom とは一緒に使えない (誤り)
    - セーフエリアを出力の全体にする (肩テロップは出力の右上の角に付く。top-left なら右の余白に入る)
    - テロップの帯は、下の余白の上下の真ん中に置く (caption.band)
    - 背景の強さ (layout_background に layout.background を重ねた値) を layout.background に入れる
    - layout.side と、行の side の辞書 (見出しごとの差し替え。merge_side) の書式を確認する (誤りは行番号つきで止める)
    読み込みのとき (load_storyboard) に行うので、組む側 (sb2resolve) と照らし合わせ (check_final) は同じ値を使う。
    """
    lay = sb.get("layout")
    if lay is None:   # 書いていない。空の {} は screen が無いので誤りにする
        return sb
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    bg = layout_background(sb)
    bad = layout_problems((lay or {}).get("screen"), bg=bg, **layout_place(sb))
    if bad:
        sys.exit("layout の書式の誤り: " + "、".join(bad))
    g = layout_geometry(sb)
    x0, w, h, anchor, margin = g["x0"], g["w"], g["h"], g["anchor"], g["margin"]
    sb["layout"] = dict(lay, background={"blur": float(bg["blur"]), "dim": float(bg["dim"])})
    for i, r in enumerate(sb.get("rows", []), 1):
        if r.get("zoom"):
            sys.exit(f"{i} 行目: layout と zoom は一緒に使えない (layout が画面の寄せ方を決める)")
    sb["safe_area"] = {"aspect": f"{W}:{H}"}
    sb["caption"] = dict(sb["caption"], band=[0, 1 - (margin + h) / H])
    # 解説の欄 (side) の余白: top-left と top は画面の右、top-right は左。右の余白は肩テロップ (右上の角) の下から
    b = sb["badge"]
    if anchor == "top-right":
        ax0, ax1, ay0 = margin, x0 - margin, margin
    else:
        ax0, ax1 = x0 + w + margin, W - margin
        ay0 = b["margin"] * H + max(b["height"] * H, badge_px(sb) + 2 * b["pad_y"]) + margin
    sb["side"] = dict(sb["side"], area=[ax0, ay0, ax1, margin + h] if ax1 - ax0 >= 80 else None)
    if lay.get("side") is not None:
        try:
            side_sections(lay["side"])
        except ValueError as e:
            sys.exit(f"layout.{e}")
    for i, r in enumerate(sb.get("rows", []), 1):   # 行の side の辞書 (見出しごとの差し替え) は読むときに確認する
        if isinstance(r.get("side"), dict):
            try:
                side_sections(merge_side(lay.get("side"), r["side"]))
            except ValueError as e:
                sys.exit(f"{i} 行目: {e}")
    return sb


def layout_values(look, sw, sh):
    """layout の Fusion の値 (build_fusion の _layout が使う)。sw, sh は素材の幅と高さ (分からなければ出力と同じとみなす)。

    Fusion の画面 (comp) は素材の画素のまま。縦横の比がタイムラインと違えば fusion_canvas で広げた大きさ (cw x ch)、
    同じなら素材の大きさ。画面の四角 (layout.screen) を comp の画素で切り抜き (Crop。XOffset・YOffset は左下から)、
    Merge の Center と Size (前景の画素の倍率) で置く。
    - screen_crop: [XOffset, YOffset, XSize, YSize] (comp の画素。半分を切り上げた整数)。x = screen.x + (cw − sw) / 2、
      y = ch − (screen.y + (ch − sh) / 2) − screen.h
    - center: 画面を置く四角の真ん中 ((x0 + w / 2) / W, 1 − (y0 + h / 2) / H)。size: k × cw / W
      (k は素材の 1 画素が出力の何画素か。comp の 1 画素は出力の W / cw 画素)
    - back_size: 背景の倍率 max(cw / screen.w, ch / screen.h) (切り抜いた画面で comp の全体を覆う。真ん中に置く)
    - blur: 背景の Blur の XBlurSize。dim: 背景の明るさの倍率 (黒を不透明度 1 − dim で重ねる)
    - point_center: 指すマークを画面と一緒に置く Transform の Center (Pivot は既定の真ん中、Size は size)。
      Transform は点 p (画面を 1 とした値) を center + size × (p − 0.5) に動かすので、画面の真ん中 (u, v) が center に来る
      Center は center + size × (0.5 − (u, v))
    - view: 画面の真ん中 (タイムラインの画素。指すマークを傾ける向きを決める)
    - rect: 画面が出力に映る四角 [x0, y0, w, h] (出力の画素)。scale: k
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    sw, sh = (sw, sh) if sw and sh else (W, H)
    g = layout_geometry(look)
    sc = look["layout"]["screen"]
    cv = fusion_canvas(sw, sh, W, H)
    cw, ch = (cv["width"], cv["height"]) if cv else (sw, sh)
    x, ytop = sc["x"] + (cw - sw) / 2, sc["y"] + (ch - sh) / 2
    size = g["k"] * cw / W
    center = ((g["x0"] + g["w"] / 2) / W, 1 - (g["y0"] + g["h"] / 2) / H)
    u, v = (x + sc["w"] / 2) / cw, 1 - (ytop + sc["h"] / 2) / ch
    bg = layout_background(look)
    # Crop の値は Resolve が整数に丸める (YOffset 343.5 を入れると 344 が読み戻る。21.1.1 の実機)。
    # 入れる値と読み戻す値をそろえるため、ここで半分を切り上げて整数にする (ずれは comp の 0.5 画素まで)
    crop = [math.floor(v + 0.5) for v in (x, ch - ytop - sc["h"], sc["w"], sc["h"])]
    return {"screen_crop": crop, "center": center, "size": size,
            "back_size": max(cw / sc["w"], ch / sc["h"]), "blur": float(bg["blur"]), "dim": float(bg["dim"]),
            "filter": fusion_filter(look["zoom"]["filter_method"]),
            "point_center": (center[0] + size * (0.5 - u), center[1] + size * (0.5 - v)), "view": (u * W, (1 - v) * H),
            "rect": [g["x0"], g["y0"], g["w"], g["h"]], "scale": g["k"], "comp": [cw, ch]}


def point_values(look, p, sw, sh, scale=1.0, view=None):
    """行の point (指すマーク) の Fusion の値。指の先 (point.glyphs の tip。字の中心からのずれを、字の大きさ px を 1 とした値。
    21.1.1 の実機で測った値) が、素材の画素の点 (x, y) に来るように Text+ の中心を決める。
    マークはズームの前に置く (画と一緒に寄る) ので、寄せた後 (scale。ズームの終わりの倍率か layout の画面の倍率。
    どちらも無ければ 1.0) に point.size px の字に見える大きさで描く。
    style が emoji のマークは書体の色で出る (point.color は効かない)。line は point.color で塗る。
    tilt (度、0〜90) だけ指の先を軸に傾ける。向きは、手の側が見えている範囲の真ん中 (view、タイムラインの画素。
    省くと画面の真ん中) へ寄る向き (左寄りの点を下から指すなら左上を指す)。angle は Fusion の Transform の Angle (左回りが正)。"""
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    if not isinstance(p, dict):
        raise ValueError("point は {x, y, ...} の形")
    c = dict(look["point"]) | p
    bad = point_problems(c)
    if bad:
        raise ValueError("point の書式の誤り: " + "、".join(bad))
    g = c["glyphs"].get(f"{c['style']}-{POINT_FROM[c['from']]}")
    if not g:
        raise ValueError(f"point.glyphs に {c['style']}-{POINT_FROM[c['from']]} のマークが無い")
    pt = source_to_timeline({"x": c["x"], "y": c["y"], "w": 0, "h": 0}, sw, sh, look)
    px = float(c["size"]) / scale
    cx, cy = pt["x"] - g["tip"][0] * px, pt["y"] - g["tip"][1] * px
    # 手の側 (指す向きの逆。下が正の画素) を左回りに少し回したときの動き (b_y, -b_x) が、見えている範囲の真ん中
    # (ズームの終わりの範囲の真ん中。ズームが無ければ画面の真ん中) を向くなら左回り
    bx, by = {"up": (0, 1), "down": (0, -1), "right": (-1, 0), "left": (1, 0)}[POINT_FROM[c["from"]]]
    vx, vy = view or (W / 2, H / 2)
    lean = by * (vx - pt["x"]) - bx * (vy - pt["y"])
    return {"text": g["glyph"], "size": px / (W * look["fusion_text_scale"]), "center": (cx / W, 1 - cy / H),
            "font_name": c["fonts"][c["style"]], "font_style": "Regular", "rgb": list(c["color"]),
            "angle": float(c["tilt"]) * (-1 if lean < 0 else 1), "pivot": (pt["x"] / W, 1 - pt["y"] / H),
            "target": (pt["x"], pt["y"])}


def side_body_sizes(sd, secs, width, font_file):
    """解説の欄ごとの中身の字の大きさ (px) と、その大きさで欄の幅に収まるか。[(px, 収まるか)] を返す。
    sd は config の side、secs は side_sections の欄、width は欄の外の幅 (余白の幅、px)。
    使える幅は width - 2 × (border_width + pad)、左寄せの欄はさらに indent を引く。
    1. いちばん長い行の幅を side.size で測り (text_width)、side.width_scale を掛ける。使える幅以下なら side.size のまま
    2. 超えるなら、字の幅は字の大きさに比例するとして size × 使える幅 ÷ 測った幅 を 0.5 px 刻みに切り下げた大きさにする
    3. それが side.min_size (size より大きければ size) より小さければ min_size にし、収まらない (False) とする
    width_scale (既定 1.08) は、Fusion の Text+ が Pillow の見積もりより広く描く分。21.1.1 の実機で測った例では、36 px の字が
    1.06〜1.074 倍の幅に描かれた (書き出した画の字の左右の端から測った)。
    書体のファイルか Pillow が無くて測れなければ None。check_final.py も同じ決め方で期待の大きさを計算する。"""
    bpx = float(sd["size"])
    low = min(float(sd.get("min_size") or bpx), bpx)
    if text_width(font_file, bpx, "あ") is None:
        return None
    inner = width - 2 * (float(sd["border_width"]) + float(sd["pad"]))
    k = float(sd.get("width_scale") or 1.0)
    out = []
    for s in secs:
        room = inner - (float(sd["indent"]) if s["align"] == "left" else 0)
        widest = k * max(text_width(font_file, bpx, x) for x in s["lines"])
        if widest <= room:
            out.append((bpx, True))
            continue
        px = math.floor(bpx * room / widest * 2) / 2
        out.append((px, True) if px >= low else (low, False))
    return out


def side_values(look, side, font_file=None):
    """解説の欄 (layout.side か行の side) の Fusion の値。layout が決めた余白 (side.area、出力の画素 [x0, y0, x1, y1]) に、欄を上から並べる。
    欄は白い縁 (side.border_rgb・border_width) の角の丸い板 (panel_rgba) に、見出し (title_size)・区切りの線・中身 (size) を置く。
    中身の字の大きさは欄ごとに決める (side_body_sizes)。いちばん長い行が欄の幅に収まれば side.size、収まらなければその欄だけ
    side.min_size まで縮める。欄の高さは中身の量 (見出しと、行の数 × その欄の字の大きさ) の割合で分け、余白の高さいっぱいに
    並べる。入りきらない・min_size でも幅を超える・見出しが幅を超えるときは警告 (見出しは縮めない)。
    文の幅は欄の書体と太さ (side.font_name・font_style。書かなければ caption の書体) のファイルで測る。
    font_file はテロップの書体のファイル (呼び出し元が caption_font で引いたもの。テストでは None で書体を外せる) で、
    欄の書体と太さが caption と同じときだけそれを使う。違うときは config().font_file で欄の書体を引く。
    返す値の sections[i]["text"] の px と size が欄ごとの中身の大きさ (px と Text+ の Size)。上の size は side.size の Size。"""
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    sd, c = look["side"], caption_look(look)
    if not sd.get("area"):
        raise ValueError("side は layout と一緒に使う (解説の欄を出す余白が無い)")
    secs = side_sections(side)
    x0, y0, x1, y1 = sd["area"]
    font = sd.get("font_name") or c["font_name"]
    style = sd.get("font_style") or c["font_style"]
    bw, pad, gap, indent = float(sd["border_width"]), float(sd["pad"]), float(sd["gap"]), float(sd["indent"])
    tpx, bpx = float(sd["title_size"]), float(sd["size"])
    tband = tpx * float(sd["title_band"])   # 見出しの帯の高さ (見出しの字の大きさの倍率。config の side.title_band)
    line = float(sd["line_height"])         # 中身の 1 行の高さ (字の大きさの倍率。config の side.line_height)
    warns = []
    ff = font_file if (font, style) == (c["font_name"], c["font_style"]) else config().font_file(font, style)
    sizes = side_body_sizes(sd, secs, x1 - x0, ff)   # 欄ごとの中身の字の大きさ (px)。収まらない欄だけ縮める
    if sizes is None:
        warns.append("書体のファイルか Pillow が無いので、解説の欄の文の幅を測っていない")
        sizes = [(bpx, True)] * len(secs)
    else:
        if not all(ok for _, ok in sizes):
            warns.append("解説の欄の文が side.min_size まで縮めても余白の幅を超える (行を分けるか side.min_size を小さくする)")
        k = float(sd.get("width_scale") or 1.0)   # 中身と同じく、Fusion が広く描く分を掛けて比べる
        if any(s["title"] and k * text_width(ff, tpx, s["title"]) > x1 - x0 - 2 * (bw + pad) for s in secs):
            warns.append("解説の欄の見出しが余白の幅を超える (見出しを短くするか side.title_size を小さくする)")
    need = [2 * bw + 2 * pad + (tband if s["title"] else 0) + len(s["lines"]) * px * line
            for s, (px, _) in zip(secs, sizes)]
    room = (y1 - y0) - gap * (len(secs) - 1)
    if sum(need) > room:
        warns.append("解説の欄が余白の高さに入りきらない (欄か行を減らすか side.size を小さくする)")

    def box(left, top, right, bottom):   # 出力の画素の四角を Fusion の値に
        return {"center": ((left + right) / 2 / W, 1 - (top + bottom) / 2 / H),
                "width": (right - left) / W, "height": (bottom - top) / H}
    out, top = [], float(y0)
    for s, n, (px, _) in zip(secs, need, sizes):
        h = n * room / sum(need)
        bottom = top + h
        sec = {"border": box(x0, top, x1, bottom), "panel": box(x0 + bw, top + bw, x1 - bw, bottom - bw),
               "title": None, "rule": None}
        body_top = top + bw
        if s["title"]:
            sec["title"] = {"text": s["title"], "center": box(x0, top + bw, x1, top + bw + tband)["center"]}
            sec["rule"] = box(x0 + bw + pad, top + bw + tband - bw / 2, x1 - bw - pad, top + bw + tband + bw / 2)
            body_top = top + bw + tband
        at = box(x0, body_top, x1, bottom - bw)["center"]
        if s["align"] == "left":   # 左寄せは Center が行の左の端 (text_tool)
            at = ((x0 + bw + pad + indent) / W, at[1])
        sec["text"] = {"text": "\n".join(s["lines"]), "center": at, "align": s["align"],
                       "px": px, "size": px / (W * look["fusion_text_scale"])}
        out.append(sec)
        top = bottom + gap
    return {"sections": out, "size": bpx / (W * look["fusion_text_scale"]), "title_size": tpx / (W * look["fusion_text_scale"]),
            "corner_radius": sd["corner_radius"], "panel_rgba": sd["panel_rgba"], "border_rgb": sd["border_rgb"],
            "text_rgb": sd["text_rgb"], "font_name": font, "font_style": style,
            "warnings": warns}


def caption_mark_room(c, size):
    """テロップの板の右下の行の終わりのマーク (end_mark) の字の大きさ (px) と、板の内側 (縁の内側) の右の端からマークの左の端までの幅 (px)。
    マークはテロップ (size px) の end_mark_scale 倍 (config の caption.end_mark_scale、既定 0.6) の字で、右に margin_x / 2 の余白を
    空けて置く。マークが無ければ (0, 0)。caption_window (マークの位置) と fit_caption (文の幅の上限) が同じ値を使う。"""
    if not c.get("end_mark"):
        return 0.0, 0.0
    px = size * float(c["end_mark_scale"])
    return px, c["margin_x"] / 2 + px


def caption_window(look, c, fit):
    """テロップの板の形 (caption の bar_inset・corner_radius・border_width・border_rgb・end_mark)。Fusion の値。
    板 (CaptionBar) は左右を bar_inset px 内側に寄せ、縁 (border_width px、border_rgb) があればその内側に置く。縁は板の後ろの
    一回り大きい板 (CaptionBorder)。end_mark (▼ など) は板の右下の内側に、テロップの end_mark_scale 倍の字で置く (CaptionMark)。"""
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    inset, bw = float(c.get("bar_inset", 0)), float(c.get("border_width", 0))
    ow, oh = 1 - 2 * inset / W, fit["bar_height"]
    out = {"bar_width": ow - 2 * bw / W, "bar_height": oh - 2 * bw / H,
           "corner_radius": c.get("corner_radius") or None, "border": None, "mark": None}
    if bw:
        out["border"] = {"width": ow, "height": oh, "rgba": list(c["border_rgb"][:3]) + [255]}
    if c.get("end_mark"):
        px, room = caption_mark_room(c, fit["size"])
        x = W / 2 + ow * W / 2 - bw - room + px / 2
        y = fit["bar_center"] - oh / 2 + (bw + c["pad_y"] / 2 + px / 2) / H
        out["mark"] = {"text": c["end_mark"], "size": fit["fusion_size"] * float(c["end_mark_scale"]), "center": (x / W, y)}
    return out


def fit_caption(look, text, font_file=None, style=None):
    """テロップをセーフエリアの幅に収める (caption.min_size まで縮め、max_lines を超えたら警告)。
    style が "closing" なら結びのテロップの大きさ (closing) で、画面の中央に置く。

    1. `|` か改行で区切った行のまま、caption.size から min_size まで縮めて収まるかを見る
    2. 収まらなければ min_size で、行ごとに折り返す (wrap_caption_line。`|` と改行で区切った行はそのまま使い、つながない)
    3. それでも max_lines を超えるか、はみ出すなら warnings に書く
    返す値: lines, size (px), fusion_size (Text+ の Size)、テロップの背景の高さと中心 (画面の高さを 1、下が 0)、warnings
    """
    c = caption_look(look, style)
    sa = safe_area(look)
    H = sa["H"]
    inner = look["timeline"]["width"] - 2 * (c.get("bar_inset", 0) + c.get("border_width", 0))
    base_avail = min(sa["width"], inner) - 2 * c["margin_x"]

    def avail(s):
        # 文は板の中央に置くので、マークの幅と余白を左右の両方で空け、マークの左にも margin_x / 2 を空ける
        if not c.get("end_mark"):
            return base_avail
        return min(base_avail, inner - 2 * (caption_mark_room(c, s)[1] + c["margin_x"] / 2))
    lines = [x for x in str(text).replace("|", "\n").split("\n")]
    given = lines   # `|` と改行で区切った行 (折り返す前)。読点の警告はこの行ごとに見る
    warnings = []
    size = c["size"]
    if text_width(font_file, size, "あ") is None:
        warnings.append("書体のファイルか Pillow が無いので、テロップの幅を測っていない")
    else:
        def fits(ls, s):
            return all(text_width(font_file, s, x) <= avail(s) for x in ls)
        s = c["size"]
        while s > c["min_size"] and not fits(lines, s):
            s -= 2
        size = max(s, c["min_size"])
        if not fits(lines, size):
            rest = max(1, c["max_lines"] - len(lines) + 1)   # 明示した行の数を除いた、折り返しに使える行の数
            lines = [y for x in lines for y in wrap_caption_line(
                x, lambda t: text_width(font_file, size, t) <= avail(size), rest)]
            if not fits(lines, size):
                warnings.append("テロップがセーフエリアの幅を超える")
    if len(lines) > c["max_lines"]:
        warnings.append(f"テロップが {len(lines)} 行になり、上限の {c['max_lines']} 行を超える。文を短くする")
    warnings += [f"テロップの行「{x}」に読点がある。区切りは | の改行で示す" for x in given if caption_comma(x)]
    n = len(lines)
    bar_px = n * size + (n - 1) * c["line_gap"] + 2 * c["pad_y"]
    bar_h = bar_px / H
    lift = c.get("lift", 0)
    pos = c.get("position", "bottom")
    center = 0.5 if pos == "center" else (lift + bar_h / 2 if pos == "bottom" else 1 - lift - bar_h / 2)
    band = c.get("band") if style != "closing" else None   # layout の下の余白 (下が 0)。帯をその上下の真ん中に置く
    if band:
        center = (band[0] + band[1]) / 2
        if bar_h > band[1] - band[0] + 1e-9:
            warnings.append("テロップの帯が下の余白の高さを超え、画面に重なる (文を短くするか layout.height を小さくする)")
    # Text+ の Size: 下のテロップは caption.size px が caption.fusion_size になる比で決める (caption.size は名目の値)。
    # 結びのテロップは closing.size を書き出される字の px として、fusion_text_scale (Size 1 が画面の幅の何倍の字か) で直す
    base = look["caption"]
    fusion = (size / (look["timeline"]["width"] * look["fusion_text_scale"]) if style == "closing"
              else base["fusion_size"] * size / base["size"])
    return {"lines": lines, "size": size, "fusion_size": fusion,
            "bar_height": bar_h, "bar_center": center, "warnings": warnings}


def badge_px(look):
    """肩テロップの字の大きさ (タイムラインの画素。Pillow で測るときの書体の大きさ)。

    Text+ の Size 1 は、書き出しの画面の幅の fusion_text_scale (実測 0.5) 倍の大きさの字になる (Size 0.045 の
    「▶▶ 6分30秒後」が 1080p で幅 322 px。Pillow で 43 px の Meiryo Bold として測ると 319 px。2026-10-15、
    tests/resolve/sb2resolve_4k.py)。
    """
    return look["badge"]["fusion_size"] * look["timeline"]["width"] * look["fusion_text_scale"]


def badge_box(look, text=None, font_file=None):
    """肩テロップの板の位置と大きさ。セーフエリアの右上の角から badge.margin (画面の高さを 1 とした値) だけ内側に置く。

    板の大きさは badge.width / height を下限に、文 (接頭辞つき) の幅と高さに余白 (badge.pad_x / pad_y、px) を足した大きさまで、
    右上の角を動かさずに左と下へ広げる (幅はセーフエリアの幅まで)。文の大きさは書体のファイルを Pillow で測る
    (Text+ の文の大きさは API で読めない)。書体のファイルか Pillow が無ければ下限の大きさのまま (measured が False)。
    返す値は Fusion の座標 (左下が 0,0、右上が 1,1)。width は画面の幅、height は画面の高さを 1 とした値。
    """
    b = look["badge"]
    sa = safe_area(look)
    W, H = sa["W"], sa["H"]
    w, h, measured = b["width"], b["height"], False
    if text is not None and text_width(font_file, 10, "あ") is not None:
        from PIL import ImageFont
        px = round(badge_px(look))
        x0, y0, x1, y1 = ImageFont.truetype(font_file, px).getbbox(text)
        # Fusion の字は Pillow の見積もりより 1〜6% 広かった (半角の空白が多い文ほど広い。実測) ので text_allowance 倍で見込む
        tw = (x1 - x0) * b.get("text_allowance", 1.0)
        w = max(w, min((tw + 2 * b["pad_x"]) / W, (sa["width"] - 2 * b["margin"] * H) / W))
        h = max(h, (max(y1 - y0, px) + 2 * b["pad_y"]) / H)
        measured = True
    m = b["margin"] * H
    cx = (sa["x1"] - m) / W - w / 2
    cy = 1 - (sa["y0"] + m) / H - h / 2
    return {"center": (cx, cy), "width": w, "height": h, "measured": measured}


def zoom_safe_area_warning(look, zoom):
    """ズーム範囲が、寄せた後にセーフエリアからはみ出すかを調べる。はみ出すなら警告の文を返す。

    ズーム範囲は画面いっぱいまで拡大されるので、拡大後の幅 (と高さ) をセーフエリアと比べる。自動では動かさない。
    """
    if not zoom:
        return None
    sa = safe_area(look)
    s = min(sa["W"] / zoom["w"], sa["H"] / zoom["h"])
    shown_w, shown_h = zoom["w"] * s, zoom["h"] * s
    if shown_w > sa["width"] + 0.5 or shown_h > sa["height"] + 0.5:
        need_h = zoom["w"] / (sa["width"] / sa["H"])
        return (f"ズーム範囲 {zoom['w']:g}x{zoom['h']:g} (タイムラインの画素) は寄せた後の幅が {shown_w:.0f}px になり、セーフエリア "
                f"({sa['width']}x{sa['height']}) を超える。ズーム範囲の高さを {need_h:.0f} 以上にすると収まる")
    return None


def caption_band(look, row, font_file=None):
    """行のテロップの背景 (帯) の上の端と下の端 (書き出しの画面の画素、上が 0) の tuple。テロップが無ければ None。
    callout の自動の置き場所 (callout_auto_dst) が帯に掛からないようにするのに使う。帯の位置は caption.position
    (bottom・top・center) と結びのテロップ (画面の中央) のどれでもよい。"""
    if not row.get("caption"):
        return None
    fit = fit_caption(look, row["caption"], font_file, "closing" if row.get("caption_style") == "closing" else None)
    H = look["timeline"]["height"]
    return ((1 - fit["bar_center"] - fit["bar_height"] / 2) * H, (1 - fit["bar_center"] + fit["bar_height"] / 2) * H)


AUTO_DST_VALIGN = ("bottom", "center")


def callout_auto_dst(look, c, sw, sh, band=None):
    """dst を省略した callout の置き場所 {x, y, w} (書き出しの画面の画素)。

    倍率は config の callout.auto_dst の max_scale (タイムラインに映る大きさの何倍まで) と max_width (いちばん幅の広い
    切り抜きの書き出しの幅の上限。セーフエリアの幅から左右の margin を引いた幅も超えない) の小さいほう。
    縦の空きは、セーフエリアの上から下まで。band (その行のテロップの帯の上の端と下の端。caption_band) があれば、
    帯の上 (セーフエリアの上〜帯の上の端) と帯の下 (帯の下の端〜セーフエリアの下) の高いほう。テロップが画面の下なら帯の上、
    上なら帯の下、中央なら広いほう (同じなら上) になる。その空きに縁の線・影の分と margin を空けて収め、収まらなければ
    倍率を下げる (タイムラインに映る大きさより小さくなることもある。callout_values が警告する)。
    valign が bottom なら空きの下の端、center なら空きの中央 (bottom・center 以外は ValueError)。横は画面の中央。
    x・y・w は整数の画素に丸める (書いた dst と同じ計算で組む)。空きが無いとき、切り抜きの幅か高さが 0 画素に
    なるとき (極端に細長い切り抜き) は ValueError。
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    base = look["callout"]
    a = base["auto_dst"]
    valign = a.get("valign", "bottom")
    if valign not in AUTO_DST_VALIGN:
        raise ValueError(f"config の callout.auto_dst.valign は {' か '.join(AUTO_DST_VALIGN)} ({valign!r} は使えない)")
    c = dict(base) | c
    srcs = callout_shown(c)
    k = min(W / sw, H / sh)
    sa = safe_area(look)
    margin, gap = float(a["margin"]), float(c["gap"])
    bd = c.get("border")
    bw = float((dict(base["border"]) | (bd if isinstance(bd, dict) else {}))["width"]) if bd else 0.0
    below = float(c["shadow_offset"]) if c.get("shadow") else 0.0   # 影は右下にずれる
    widest, tall = max(s["w"] for s in srcs), sum(s["h"] for s in srcs)
    if band is None:
        r0, r1 = sa["y0"], sa["y1"]
    else:   # 帯の上と帯の下の、高いほう (同じなら上)
        r0, r1 = max(((sa["y0"], min(band[0], sa["y1"])), (max(band[1], sa["y0"]), sa["y1"])), key=lambda r: r[1] - r[0])
    top = r0 + margin + bw
    bottom = r1 - margin - bw - below
    room = bottom - top - gap * (len(srcs) - 1)
    m = min(float(a["max_scale"]) * k, float(a["max_width"]) / widest, (sa["width"] - 2 * (margin + bw)) / widest)
    if room <= 0:
        raise ValueError("callout の自動の置き場所: " + ("テロップの帯の上にも下にも" if band else "画面に")
                         + "空きが無い (dst を書く)")
    m = min(m, room / tall)
    dw = math.floor(widest * m + 1e-6)   # 整数の画素に切り捨てる (計算の誤差で 1 画素減らさない)
    m = dw / widest
    for i, s in enumerate(srcs, 1):   # 切り抜きを 1 枚ずつ、幅か高さが 0 画素に丸まらないか (極端に細長い切り抜き)
        w1, h1 = (dw if s["w"] == widest else s["w"] * m), s["h"] * m
        if w1 < 0.5 or h1 < 0.5:
            raise ValueError(f"callout の自動の置き場所: 上から {i} 枚目の切り抜き ({s['w']:g}x{s['h']:g}) の幅か高さが "
                             f"0 画素になる (倍率 {m:.3f} で {w1:.1f}x{h1:.1f} px。dst を書く)")
    dh = tall * m + gap * (len(srcs) - 1)
    y = bottom - dh if valign == "bottom" else top + (bottom - top - dh) / 2
    return {"x": round((W - dw) / 2), "y": math.floor(y + 1e-6), "w": dw}


def callout_values(look, co, sw, sh, fps, speed=1.0, band=None):
    """callout (素材の一部を切り抜いて拡大し、画面の上に重ねる) の Fusion の値。

    切り抜き (src) は素材の画素の四角 1 つか、その list。list は画面で折り返された文の行ごとの切り抜きで、書いた順
    (order があればその src の番号の順) に上から重ねる (左をそろえ、間は gap の書き出しの画素。横にはつながない)。
    dst を省略すると callout_auto_dst で置き場所を決める (band はその行のテロップの帯の上の端と下の端。caption_band)。
    自動の置き場所で縮んでタイムラインに映る大きさより小さくなったら (Size が 1 未満) 警告する。
    どの切り抜きも同じ倍率で拡大し、
    いちばん幅の広い切り抜きの書き出しの幅が dst.w になる。置き場所 (dst の x, y) は書き出しの画面の画素で、全体の左上。
    Fusion の合成は素材の解像度で動くので (縦横の比がタイムラインと違う素材は、素材の画素のまま広げた画面。fusion_canvas)、
    Crop で素材の画素のまま切り抜き、Merge の Size で拡大して重ねる
    (1080p に縮めてから拡大しないので字がくっきりする)。Size = dst.w ÷ (いちばん広い切り抜きの幅 × 素材からタイムラインへの倍率)。
    Crop の YOffset は下から数える。21.1.1 の実機の下調べで、書き出しの (100, 600) に幅 540 px でぴったり重なった。
    重ねた全体を 1 つの四角として扱う。fill はその四角を埋める半透明の色 (幅の狭い切り抜きの右と、切り抜きの間に見える)。
    縁 (border) は四角の外側に沿う幅 width (書き出しの画素) の線。影は四角を shadow_offset だけずらした半透明の黒で、
    四角の外側だけに置く (inner は四角そのもの。四角の内側に影が掛かると、fill と重なって下の画が暗くなりすぎる)。
    """
    W, H = look["timeline"]["width"], look["timeline"]["height"]
    c = dict(look["callout"]) | co
    auto = "dst" not in co
    srcs = callout_shown(c)
    d = callout_auto_dst(look, co, sw, sh, band) if auto else c["dst"]
    k = min(W / sw, H / sh)
    cv = fusion_canvas(sw, sh, W, H)
    fw, fh = (cv["width"], cv["height"]) if cv else (sw, sh)   # 合成の画面の大きさ
    px, py = (fw - sw) / 2, (fh - sh) / 2                       # 広げた画面の中の素材の左下
    ox, oy = (W - fw * k) / 2, (H - fh * k) / 2
    dw = float(d["w"])
    m = dw / max(s["w"] for s in srcs)   # 素材の 1 画素が書き出しの何画素になるか (全部の切り抜きで同じ)
    gap = float(c["gap"])

    def comp01(x, y, w, h):
        """書き出しの画面の画素の四角を、合成 (素材の解像度。広げたなら広げた画面) の Fusion の値にする。"""
        cx, cy = ((x + w / 2 - ox) / k) / fw, 1 - ((y + h / 2 - oy) / k) / fh
        return {"center": (cx, cy), "width": (w / k) / fw, "height": (h / k) / fh}
    pieces, y = [], float(d["y"])
    for s in srcs:
        box = [float(d["x"]), y, s["w"] * m, s["h"] * m]
        pieces.append({"crop": (float(s["x"] + px), float(sh - s["y"] - s["h"] + py), float(s["w"]), float(s["h"])),
                       "center": comp01(*box)["center"], "dst": box})
        y += box[3] + gap
    dh = y - gap - float(d["y"])
    dst = [float(d["x"]), float(d["y"]), dw, dh]
    bd = c.get("border")
    border = None
    if bd:
        bd = dict(look["callout"]["border"]) | (bd if isinstance(bd, dict) else {})
        bw = float(bd["width"])
        # 外の四角 (center・width・height) から内の四角 (inner、全体の四角) を抜いた輪
        border = {"rgb": hex_rgb(bd["color"]), "line_px": bw, **comp01(dst[0] - bw, dst[1] - bw, dw + 2 * bw, dh + 2 * bw),
                  "inner": comp01(*dst)}
    fl = c.get("fill")
    fill = None
    if fl:
        fl = dict(look["callout"]["fill"]) | (fl if isinstance(fl, dict) else {})
        fill = {"rgb": hex_rgb(fl["color"]), "opacity": float(fl["opacity"]), **comp01(*dst)}
    shadow = None
    if c.get("shadow"):
        o = float(c["shadow_offset"])
        shadow = {"rgba": c["shadow_rgba"], "blur": float(c["shadow_blur"]), **comp01(dst[0] + o, dst[1] + o, dw, dh),
                  "inner": comp01(*dst)}
    frames = appear_keys(*appear_frames(co, look["callout"], "in", fps))
    sa = safe_area(look)
    warn = []
    if dst[0] < sa["x0"] or dst[0] + dw > sa["x1"] or dst[1] < sa["y0"] or dst[1] + dh > sa["y1"]:
        warn.append(f"callout の置き場所 ({dst[0]:g}, {dst[1]:g}, {dw:g}x{dh:.0f}) がセーフエリア "
                    f"({sa['x0']}〜{sa['x1']}, {sa['y0']}〜{sa['y1']}) からはみ出す")
    if auto and m / k < 1 - 1e-9:
        warn.append(f"callout の自動の置き場所で、タイムラインに映る大きさより小さく縮んだ (Size {m / k:.2f})。"
                    "切り抜きを減らすか dst を書く")
    out = {"pieces": pieces, "size": m / k, "dst": dst, "border": border, "fill": fill, "shadow": shadow,
           "ramp": [(comp_frame(f, speed), u) for f, u in frames], "ramp_frames": frames,
           "warning": "。".join(warn) or None}
    if "order" in co:   # 書いた順と違う順に重ねた (pieces は上からの順。src は構成表の src の番号)
        out["order"] = callout_order(c)
        for p, i in zip(pieces, out["order"]):
            p["src"] = i
    if auto:
        out["dst_auto"] = {"x": d["x"], "y": d["y"], "w": d["w"],
                           "caption_band": None if band is None else [round(band[0], 1), round(band[1], 1)]}
    return out
