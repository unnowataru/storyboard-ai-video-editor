"""簡易版を描く部分: 組み立て内容の JSON (sb2ffmpeg.py が書く) を FFmpeg で動画にする。

組み立て内容だけで動く。構成表を組む部分 (sb2resolve・resolve_stand_in・sb2ffmpeg) は直接は読み込まない
(tests/offline/test_sb2ffmpeg.py が確認する)。使う共通の部品は common の value_at・comp_frame・fusion_canvas・
config (font_file・work_dir・tool)・run_tool だけ。ただし common は fusion・layout・schema などの計算の部品も
読み込むので (value_at は fusion.py にある)、間接的にはそれらにも依存する。Resolve の API は呼ばない。
"""
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageChops, ImageDraw, ImageFont

import common as C

X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]   # CPU で速さを優先
MASK_PAD = 0.01   # 黒塗りのマスクを広げる幅 (画面の短い辺を 1 とした値。漏れない側に倒す)
MASK_TOOLS = {"ofx.com.blackmagicdesign.resolvefx.MosaicBlur", "Blur"}   # 範囲を黒塗りにする部品
IMAGE_INPUTS = ("Input", "Source", "Background")   # 本線の画がつながる入力
# 描く側が名前で見分ける部品。名前は Resolve 側の fusion.py が付ける (変わったら tests/offline/test_sb2ffmpeg.py の
# test_part_names_match_fusion が落ちる)
CANVAS = "Canvas"                          # 素材の比をタイムラインに合わせる余白 (fusion.py の Crop)
LAYOUT_SCREEN = "LayoutScreen"             # 画面を縮めて置く Merge (fusion.py の layout)
LAYOUT_SCREEN_CROP = "LayoutScreenCrop"    # 置く画面の切り抜き (同上)
BLACK_MASK = re.compile(r"Mask\d+Black")   # 黒塗りのマスクの Background (fusion.py の _mask)


def keys_of(tool, name, default=None):
    """入力の値を [(合成の時刻, 値)] で返す。動かない値は 1 つだけ。"""
    link = tool["links"].get(name)
    if link and "keys" in link:
        return link["keys"]
    v = tool["inputs"].get(name, default)
    return None if v is None else [[0, v]]


def center_keys(tool):
    """Center の X と Y の [(合成の時刻, 値)]。"""
    link = tool["links"].get("Center")
    if link and "path" in link:
        return link["path"]["X"], link["path"]["Y"]
    x, y = tool["inputs"].get("Center", [0.5, 0.5])
    return [[0, x]], [[0, y]]


class Clip:
    """1 つのクリップを描く。合成の時刻は、出力のフレーム n で comp_frame(n, k) (k は速さの倍率、止めたクリップは 1)。"""

    def __init__(self, c, spec, text_scale, tmp, warns, threads=None):
        """text_scale は Text+ の Size を画素にする係数 (構成表の fusion_text_scale)。threads は parse_threads の値"""
        self.c, self.tmp, self.warns, self.threads = c, tmp, warns, threads
        self.W, self.H, self.fps = spec["timeline"]["width"], spec["timeline"]["height"], spec["timeline"]["fps"]
        self.frames = c["end"] - c["start"]
        self.k = c["speed"] / 100 or 1.0
        self.text_scale = text_scale
        self.tools = {t["name"]: t for t in c["fusion"]["tools"]} if c["fusion"] else {}
        self.pngs = []   # 重ねる画像のパス (graph が書く)

    def warn(self, text):
        self.warns.append(f"{self.c['start']} フレーム目のクリップ: {text}")

    def comp_time(self, n):
        """出力のフレーム n の合成の時刻 (Resolve 側の comp_frame。割り切れない倍速は切り捨て)。"""
        return C.comp_frame(n, self.k)

    def expr(self, keys):
        """[(合成の時刻, 値)] を、zoompan の入力の時刻 it (秒) での値にする FFmpeg の式にする。
        Resolve 側の C.value_at(keys, C.comp_frame(n, k)) と同じ計算を式で書く: 出力のフレーム n = round(it × fps) を
        comp_frame と同じく切り捨てて合成の時刻にし、キーの間は直線、最初のキーより前は最初の値、最後より後は最後の値。
        Python でフレームごとに値を出して渡さないのは、長いクリップでフレーム数ぶんの値を FFmpeg に渡すことになるため"""
        T = f"floor(round(it*{self.fps:.17g})*{self.k:.17g}+1e-6)"
        pts = [(float(t), float(v)) for t, v in keys]
        e = f"{pts[-1][1]:.17g}"
        for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
            if t1 > t0:   # value_at と同じく、時刻が同じキーの間は補間しない
                e = f"if(lte({T},{t1:.17g}),{v0:.17g}+({v1 - v0:.17g})*({T}-{t0:.17g})/{t1 - t0:.17g},{e})"
        return f"if(lte({T},{pts[0][0]:.17g}),{pts[0][1]:.17g},{e})"

    def still(self, tool, name, default):
        """動く値は最後の値で描く (動きは描かない)。"""
        keys = keys_of(tool, name, default)
        if len(keys) > 1:
            self.warn(f"{tool['name']} の {name} の動きは描かない (最後の値)")
        return keys[-1][1]

    # 本線の部品 → フィルター
    def chain(self):
        """MediaIn1 から MediaOut1 までの本線の部品。layout (画面を縮めて置く) は描かず、画面の切り抜きの入力へ飛ぶ。"""
        out, name = [], self.tools["MediaOut1"]["links"]["Input"]["tool"]
        while name != "MediaIn1":
            if name == LAYOUT_SCREEN:
                self.warn("layout (画面を縮めて置く) は描かない")
                name = self.tools[LAYOUT_SCREEN_CROP]["links"]["Input"]["tool"]
                continue
            t = self.tools[name]
            out.append(t)
            name = next(t["links"][k]["tool"] for k in IMAGE_INPUTS if "tool" in t["links"].get(k, {}))
        return out[::-1]

    def box(self, mask):
        """マスクを黒で塗る。drawbox は時刻で動かせないので、動くマスクは全部のキーフレームの範囲を覆う四角にし、
        MASK_PAD だけ広げる (漏れない側。キーの間は直線なので、端はキーの時刻に来る)。"""
        X, Y = center_keys(mask)
        Wk, Hk = keys_of(mask, "Width", 0), keys_of(mask, "Height", 0)
        rects = [[C.value_at(k, t) for k in (X, Y, Wk, Hk)] for t in sorted({t for k in (X, Y, Wk, Hk) for t, _ in k})]
        p = MASK_PAD * min(self.W, self.H)
        x0, x1 = min(x - w / 2 for x, y, w, h in rects) * self.W - p, max(x + w / 2 for x, y, w, h in rects) * self.W + p
        y0, y1 = min(1 - y - h / 2 for x, y, w, h in rects) * self.H - p, max(1 - y + h / 2 for x, y, w, h in rects) * self.H + p
        return f"drawbox=x={x0:.1f}:y={y0:.1f}:w={x1 - x0:.1f}:h={y1 - y0:.1f}:color=black:t=fill"

    def zoom(self, t):
        """Transform の Size と Center を zoompan にする。素材の点 u は画面の Center + (u − 0.5) × Size に来る。
        zoompan の倍率は 1〜10 に限られるので、外れるものは描かない。"""
        sizes = keys_of(t, "Size", 1.0)
        if not all(1 <= v <= 10 for _, v in sizes):
            self.warn(f"{t['name']} の倍率 (1〜10 の外) は描かない")
            return None
        X, Y = center_keys(t)
        # zoompan は切り抜く位置を画の中に押し戻すので、画の外 (余りの黒) まで映すズームは位置がずれる。描かない。
        # 画面に映る画の範囲は、横が 0.5 - Center.X / Size 〜 0.5 + (1 - Center.X) / Size (縦も同じ)。
        # 出力の 1 画素までのはみ出しは押し戻されても絵がほぼ変わらないので許す (ズーム範囲の比が 16:9 ちょうどでないときの
        # 端数)。拡大するほど画の 1 画素は出力で大きくなるので、許す幅は 1 / (出力の画素 × Size)
        for n in range(self.frames):
            f = self.comp_time(n)
            s, cx, cy = (C.value_at(k, f) for k in (sizes, X, Y))
            if any(0.5 - c / s < -1 / (d * s) or 0.5 + (1 - c) / s > 1 + 1 / (d * s)
                   for c, d in ((cx, self.W), (cy, self.H))):
                self.warn(f"{t['name']} は画の外まで映す (zoompan では位置がずれる) ので描かない")
                return None
        s = self.expr(sizes)
        x, y = self.expr(X), self.expr(Y)
        return (f"zoompan=z='{s}':x='(0.5-({x})/({s}))*iw':y='(0.5-(1-({y}))/({s}))*ih'"
                f":d=1:s={self.W}x{self.H}:fps={self.fps:g}")

    def enable(self, t):
        """Merge の Blend が 0 より大きい出力の時刻の範囲 (overlay の enable) と、重ねる濃さ (Blend のいちばん大きい値)。
        範囲がずっとなら ""、出ていなければ None。現れ方・消え方 (Blend の途中の値) は描かず、出ている間は同じ濃さ"""
        keys = keys_of(t, "Blend", 1.0)
        vals = [C.value_at(keys, self.comp_time(n)) for n in range(self.frames)]
        on = [v > 1e-6 for v in vals]
        alpha = min(1.0, max(vals, default=0.0))
        if all(on):
            return "", alpha
        spans, a = [], None
        for n, v in enumerate(on + [False]):
            if v and a is None:
                a = n
            elif not v and a is not None:
                spans.append(f"between(n,{a},{n - 1})")
                a = None
        return (f":enable='{'+'.join(spans)}'" if spans else None), alpha

    def graph(self):
        """フィルターの文の部品と、最後の出力の名前を返す。重ねる画像は作ったらすぐ書き、パスを self.pngs に積む
        (画像をメモリに持ち続けない)。"""
        parts, cur = [], "[base]"
        for i, t in enumerate(self.chain() if self.tools else []):
            fg = self.tools.get(t["links"].get("Foreground", {}).get("tool"), {})
            if t["id"] in MASK_TOOLS and "EffectMask" in t["links"]:   # モザイク・ぼかしのマスク
                f = self.box(self.tools[t["links"]["EffectMask"]["tool"]])
            elif BLACK_MASK.fullmatch(fg.get("name", "")):   # 黒塗りのマスク
                f = self.box(self.tools[fg["links"]["EffectMask"]["tool"]])
            elif t["name"] == CANVAS:   # 素材の比をタイムラインに合わせる余白は、render の canvas が同じ値で作る
                continue
            elif t["id"] == "Transform":
                f = self.zoom(t)
                if f is None:
                    continue
            elif t["id"] == "Merge":
                en, alpha = self.enable(t)
                if en is None:
                    continue
                img = self.layer(self.tools[t["links"]["Foreground"]["tool"]])
                if alpha < 1:   # Blend は重ねる画の濃さ (拡大の重ね表示の板は 0.7)
                    img.putalpha(img.getchannel("A").point(lambda a: round(a * alpha)))
                self.pngs.append(os.path.join(self.tmp, f"c{self.c['start']}-{i}.png"))
                img.save(self.pngs[-1])
                # 重ねる画像は 1 フレームだけ読み、overlay が最後の画を出し続ける (eof_action=repeat)。-loop 1 で
                # 毎フレーム読み直すと、1080p の PNG を毎回デコードして重ね合わせの時間の大半になる
                parts.append(f"{cur}[{len(self.pngs)}:v]overlay=eof_action=repeat{en}[s{i}]")
                cur = f"[s{i}]"
                continue
            else:
                self.warn(f"{t['name']} ({t['id']}) は描かない")
                continue
            parts.append(f"{cur}{f}[s{i}]")
            cur = f"[s{i}]"
        return parts, cur

    # 重ねる部品 → 透明な画像 (動く値は最後の値)
    def layer(self, t):
        blank = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        if t["id"] == "Merge":
            img = self.layer(self.tools[t["links"]["Background"]["tool"]]) if "Background" in t["links"] else blank
            return Image.alpha_composite(img, self.layer(self.tools[t["links"]["Foreground"]["tool"]]))
        if t["id"] == "Background":
            rgba = [self.still(t, k, d) for k, d in (("TopLeftRed", 0), ("TopLeftGreen", 0), ("TopLeftBlue", 0),
                                                      ("TopLeftAlpha", 1))]
            img = Image.new("RGBA", (self.W, self.H), tuple(round(v * 255) for v in rgba))
            if "EffectMask" in t["links"]:
                img.putalpha(ImageChops.multiply(img.getchannel("A"), self.mask(self.tools[t["links"]["EffectMask"]["tool"]])))
            return img
        if t["id"] == "TextPlus":
            return self.text(t, blank)
        self.warn(f"{t['name']} ({t['id']}) は描かない")
        return blank

    def mask(self, t):
        X, Y = center_keys(t)
        if len(X) > 1 or len(Y) > 1:
            self.warn(f"{t['name']} の Center の動きは描かない (最後の値)")
        cx, cy = X[-1][1], Y[-1][1]
        w, h = self.still(t, "Width", 0) * self.W, self.still(t, "Height", 0) * self.H
        x0, y0 = cx * self.W - w / 2, (1 - cy) * self.H - h / 2
        m = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(m)
        if t["id"] == "EllipseMask":
            d.ellipse((x0, y0, x0 + w, y0 + h), fill=255)
        else:
            d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=t["inputs"].get("CornerRadius", 0) * min(w, h) / 2,
                                fill=255)
        if t["inputs"].get("Invert"):
            m = ImageChops.invert(m)
        if "EffectMask" in t["links"] and t["inputs"].get("PaintMode") == "Multiply":
            m = ImageChops.multiply(m, self.mask(self.tools[t["links"]["EffectMask"]["tool"]]))
        return m

    def text(self, t, img):
        i = t["inputs"]
        path = C.config().font_file(i.get("Font"), i.get("Style"))
        size = max(1, round(i.get("Size", 0.1) * self.W * self.text_scale))
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
        if not path:
            self.warn(f"{t['name']} の書体 {i.get('Font')} {i.get('Style')} が見つからない")
        cx, cy = i.get("Center", [0.5, 0.5])
        left = i.get("HorizontalLeftCenterRight") == -1
        color = tuple(round(i.get(k, 1.0) * 255) for k in ("Red1", "Green1", "Blue1"))
        ImageDraw.Draw(img).multiline_text((cx * self.W, (1 - cy) * self.H), i.get("StyledText", ""), font=font,
                                           fill=color, anchor="lm" if left else "mm", align="left" if left else "center")
        return img

    def canvas(self, m):
        """素材をタイムラインの大きさにするフィルター。素材の比がタイムラインと違えば、組み立て内容の Canvas (Crop) の
        大きさとずらしで素材の画素のまま画面を広げ (余りは黒)、それをタイムラインの大きさにする。Canvas の無いクリップは
        Resolve 側と同じ layout.fusion_canvas で決める。Crop のずらしは素材の左下から (広げる分は負)"""
        cv = self.tools.get(CANVAS)
        if cv:
            i = cv["inputs"]
            cw, ch, ox, oy = i["XSize"], i["YSize"], i["XOffset"], i["YOffset"]
        else:
            d = C.fusion_canvas(m["width"], m["height"], self.W, self.H)
            if d is None:
                return f"scale={self.W}:{self.H}"
            cw, ch, ox, oy = d["width"], d["height"], d["x_offset"], d["y_offset"]
        # YUV420 のまま pad すると、大きさと置き場所が偶数に切り下がる (横長の素材、例 3440x1440 を 16:9 に置くと高さ 1935 が 1934 になる)
        pad = f"pad={round(cw)}:{round(ch)}:{round(-ox)}:{round(ch - m['height'] + oy)}:color=black"
        return f"format=yuv444p,{pad},scale={self.W}:{self.H}"

    def render(self, media, out):
        """このクリップを out に書く。素材の範囲を切り出して速さを変え、タイムラインの大きさにしてから部品を描く。"""
        m, c = media[self.c["media"]], self.c
        ss = c["source_start"] / m["fps"]
        fit = self.canvas(m)
        if c["speed"] == 0:   # 止めたクリップ: 1 フレームをタイムラインの大きさにしてから繰り返す (繰り返してから縮めると毎フレーム縮める)
            src = ["-ss", f"{ss:.6f}", "-i", m["path"]]
            head = f"trim=end_frame=1,{fit},setsar=1,loop=loop={self.frames}:size=1,setpts=N/{self.fps:g}/TB"
        else:
            dur = (c["source_end"] - c["source_start"] + 1) / m["fps"]
            # 出力のフレーム n が素材の comp_frame(n, k) 番目を映すようにする (Resolve と同じ切り捨て)。時刻は素材の
            # フレーム番号から細かい単位 (AVTB) で作る (素材のミリ秒の時刻からだと遅くするときにずれる)。fps の既定の near もずれる
            src = ["-ss", f"{ss:.6f}", "-t", f"{dur:.6f}", "-i", m["path"]]
            head = f"settb=AVTB,setpts=N/({self.k * m['fps']:.12g})/TB,fps={self.fps:g}:round=up,{fit},setsar=1"
        parts, cur = self.graph()
        g = ";".join([f"[0:v]{head}[base]"] + parts
                     + [f"{cur}trim=end_frame={self.frames},format=yuv420p[out]"])
        pngs = [a for p in self.pngs for a in ("-i", p)]
        t = self.threads
        dec, mid, enc = (([], [], []) if t is None else
                         (["-threads", str(t["decode"])], ["-filter_complex_threads", str(t["filter"])],
                          ["-threads", str(t["encode"])]))
        C.run_tool("ffmpeg", dec + src + pngs + mid + ["-filter_complex", g, "-map", "[out]", "-an", "-r", f"{self.fps:g}"]
                   + X264 + enc + [out])


def parse_threads(text):
    """--threads・設定の threads の値。auto は FFmpeg に任せる (None)。「読み込み,フィルター,書き出し」は 1 本の
    FFmpeg のスレッドの数 (例 3,1,1)。値は環境ごとに sb2ffmpeg_bench.py で測って決める。読めなければ ValueError"""
    if str(text) == "auto":
        return None
    try:
        d, f, e = (int(x) for x in str(text).split(","))
    except ValueError:
        raise ValueError(f"auto か「読み込み,フィルター,書き出し」の数 (例 3,1,1): {text}") from None
    if min(d, f, e) < 1:
        raise ValueError(f"数は 1 以上: {text}")
    return {"decode": d, "filter": f, "encode": e}


def render(spec, text_scale, out, jobs=1, threads=None):
    """組み立て内容を簡易版の動画にする。クリップごとに書いてからつなぐ。返す値は警告の list。
    jobs はクリップを同時に書く数。素材の読み込みは 1 本の FFmpeg で CPU をほぼ使い切るが、重ね合わせとズームは
    1 つの芯で動く所が多いので、何本か並べるとその分が隠れる。警告はクリップの順に並べる"""
    warns = ["音は入れない (簡易版)"]
    clips = spec["clips"]
    for a, b in zip(clips, clips[1:]):
        if a["end"] != b["start"]:
            warns.append(f"{a['end']}〜{b['start']} フレームに空きか重なりがある (詰めて描く)")
    with tempfile.TemporaryDirectory(dir=C.config().work_dir()) as tmp:
        files = [os.path.join(tmp, f"c{c['start']:07d}.mp4") for c in clips]
        clip_warns = [[] for _ in clips]

        def one(i):
            Clip(clips[i], spec, text_scale, tmp, clip_warns[i], threads).render(spec["media"], files[i])

        # どれかのクリップが失敗したら、まだ始まっていないクリップは取り消して例外を出す。走っている FFmpeg
        # (多くても jobs - 1 本) は止めずに終わるのを待つ (止めるには共通の部品の run_tool を変えることになるため)
        ex = ThreadPoolExecutor(max_workers=max(1, jobs))
        try:
            for f in as_completed([ex.submit(one, i) for i in range(len(clips))]):
                f.result()
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
        warns += [w for ws in clip_warns for w in ws]
        # 一覧はクリップのファイル名だけ (一覧と同じフォルダーにある)。つないだ動画も一時フォルダーに書いてから移すので、
        # 同じ書き出し先へ同時に流しても途中のファイルを取り合わない
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.writelines(f"file {os.path.basename(p)}\n" for p in files)
        joined = os.path.join(tmp, "joined.mp4")
        C.run_tool("ffmpeg", ["-f", "concat", "-i", lst, "-c", "copy", joined])
        shutil.move(joined, out)
    return warns
