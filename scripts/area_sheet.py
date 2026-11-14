"""書き出した動画の決まった範囲 (見る範囲) が変わったフレームだけを、番号と時刻を付けて縦に並べた画像にする。

完成版のマスクの漏れを目で確認するために使う。全部のフレームを見るので、1〜数フレームだけの漏れも残る。
文字認識は使わない。並べた画像を人が見て判定する。

使い方:
  python scripts/area_sheet.py 書き出した動画.mp4 --area address=1700,95,900,40 --area list=200,400,600,700
  python scripts/area_sheet.py 書き出した動画.mp4 --areas 見る範囲.json [--out 出力先]
  python scripts/area_sheet.py 書き出した動画.mp4 --area address=1700,95,900,40 --max-sheets 20 --max-frames 500 --max-output-mb 200
見る範囲は書き出しの画素の x,y,w,h (左上が 0,0)。名前を付けるなら 名前=x,y,w,h。
JSON は {"areas": [{"name": "address", "x": 1700, "y": 95, "w": 900, "h": 40}, ...]} (list だけでもよい)。
出力先の既定は 作業フォルダー\\<動画の名前>\\area-sheet。範囲ごとに <名前>_01.png ... と、詳細の area-sheet.json。
変わったとみなす閾値・1 枚に並べる数・縮める幅・1 枚の高さの上限の既定は config の area_sheet。
--max-sheets (画像の枚数。見る範囲の合計)・--max-frames (残すフレームの合計)・--max-output-mb (PNG の合計 MB) は省略すると上限が無い。
0 以下は無制限ではなく誤り。枚数は PNG を書く直前に見て、超える 1 枚は書かない。上限に達したら検査は未完了 (終了コード 1)。
ちょうど上限まで書いて、それ以上の変化が無いときは完了。容量を超えたあとの PNG は、先に別の上限が当たっていても書かない。
画素の比べ方は変えない。
area_sheet.py は見る範囲の外を見ない。範囲はマスクより一回り広く取る。
"""
import argparse
import json
import math
import os
import re
import sys
import threading
import time

import common as C
from contact_sheet import frame_label

NAME_OK = re.compile(r"[A-Za-z0-9_-]+")
LABEL_SAMPLE = "000000  0:00:00.000"   # 番号と時刻の字の幅を決めるための見本
GAP = 2                                # 並べたフレームの間の線の太さ (px)
_MB = 1024 * 1024                      # --max-output-mb の 1 MB


def _as_positive_int(name, value):
    """上限の整数。None は上限なし。0 以下と整数でない値は誤り (0 は無制限ではない)。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} は 1 以上の整数: {value}")
    return value


def _as_positive_mb(value):
    """--max-output-mb。None は上限なし。0 以下・数でない・1 バイト未満に切り捨てられる値は誤り。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"上限 max_output_mb は 0 より大きい数: {value}")
    if int(value * _MB) < 1:
        raise ValueError(f"上限 max_output_mb が 1 バイト未満になる: {value}")
    return value


def _cli_positive_int(text):
    try:
        value = int(str(text).strip(), 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"上限は 1 以上の整数: {text}") from None
    try:
        _as_positive_int("上限", value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"上限は 1 以上の整数: {text}") from None
    return value


def _cli_positive_mb(text):
    try:
        value = float(str(text).strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"上限は 0 より大きい数: {text}") from None
    try:
        _as_positive_mb(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return value


class _OutputCap:
    """画像の枚数・残すフレーム・PNG の合計バイト。None は上限なし。達した上限の名前を limit_hit に残す。

    複数に同時に達したときは、残すフレーム、画像の枚数、書いた後のバイト、の順で先に当たったものを残す。
    枚数は見る範囲の合計で、PNG を書く直前に見る。超える 1 枚は残さない。ちょうど上限まで書いて、
    それ以上の変化が無いときは limit_hit を立てない。
    バイトは画像を書いた後でないと分からないので、上限をまたいだ 1 枚までは残る。
    超えたあとは limit_hit と別に output_bytes を見て、次の PNG は書かない。
    """

    def __init__(self, max_sheets, max_frames, max_output_mb):
        self.max_sheets = _as_positive_int("max_sheets", max_sheets)
        self.max_frames = _as_positive_int("max_frames", max_frames)
        self.max_output_mb = _as_positive_mb(max_output_mb)
        self.max_bytes = None if self.max_output_mb is None else int(self.max_output_mb * _MB)
        self.images = 0
        self.output_bytes = 0
        self.changed = 0
        self.limit_hit = None

    def frame_full(self):
        if self.max_frames is None or self.changed < self.max_frames:
            return False
        if self.limit_hit is None:
            self.limit_hit = "max_frames"
        return True

    def sheet_full(self):
        if self.max_sheets is None or self.images < self.max_sheets:
            return False
        if self.limit_hit is None:
            self.limit_hit = "max_sheets"
        return True

    def add_sheet(self, nbytes):
        self.images += 1
        self.output_bytes += nbytes
        # limit_hit は先に当たった上限だけ残す。超えたかは output_bytes で別途見る。
        if self.limit_hit is None and self.max_bytes is not None and self.output_bytes > self.max_bytes:
            self.limit_hit = "max_output_mb"

    def bytes_over(self):
        """書いた PNG の合計が容量を超えているか。limit_hit とは別に見る。"""
        return self.max_bytes is not None and self.output_bytes > self.max_bytes

    def can_write_sheet(self):
        """次の 1 枚を書いてよいか。呼び出し側が PNG を保存する直前に見る。

        枚数の上限に達しているときは書かない (limit_hit は max_sheets)。
        容量は limit_hit と別に、output_bytes が max_bytes を超えていれば書かない。
        バイトをまたぐ 1 枚目は、書いた後の add_sheet で判定するので、ここでは拒まない。
        """
        if self.bytes_over():
            return False
        return not self.sheet_full()


def _drop_open_cells(writer, kept, cap):
    """まだ画像にしていない段を捨て、それに対応する残したフレームの記録も捨てる (上限を超えて覚えない)。"""
    drop = {n for n, _im in writer.cells}
    if drop:
        before = len(kept)
        kept[:] = [row for row in kept if row["frame"] not in drop]
        cap.changed -= before - len(kept)
    writer.cells.clear()


def _flush_pending(writers, kept, cap):
    """読み終えたあと、開きの段を 1 枚に書く。枚数かバイトの上限を超える分は書かない。"""
    for writer, rows in zip(writers, kept):
        if not writer.cells:
            continue
        if not cap.can_write_sheet():
            _drop_open_cells(writer, rows, cap)
            continue
        writer.flush()
        if writer.sheets:
            cap.add_sheet(os.path.getsize(writer.sheets[-1]))


# ---------- 見る範囲 ----------
def parse_area(s, i):
    """--area の 1 つ (「x,y,w,h」か「名前=x,y,w,h」) を dict にする。i は名前が無いときの番号。"""
    name, _, box = s.rpartition("=")
    try:
        x, y, w, h = (int(v) for v in box.split(","))
    except ValueError:
        raise ValueError(f"見る範囲は x,y,w,h か 名前=x,y,w,h で書く: {s}")
    return {"name": name or f"area{i}", "x": x, "y": y, "w": w, "h": h}


def load_areas(specs=(), path=None):
    """--area の list と JSON のファイルから、見る範囲の list を作る。"""
    areas = []
    if path:
        with open(path, encoding="utf-8-sig") as f:
            j = json.load(f)
        for a in (j.get("areas", []) if isinstance(j, dict) else j):
            try:
                areas.append({"name": str(a.get("name") or f"area{len(areas) + 1}"),
                              **{k: int(a[k]) for k in ("x", "y", "w", "h")}})
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"見る範囲に x・y・w・h が無い: {a}")
    for s in specs or ():
        areas.append(parse_area(s, len(areas) + 1))
    return areas


def area_problems(areas, W, H):
    """見る範囲の書き方の誤り (名前の字・重なった名前・画面の外・大きさ 0) を list で返す。

    名前の重なりは大文字小文字を区別せずに見る (Windows ではファイル名の addr_01.png と ADDR_01.png が上書きし合う)。
    """
    out, seen = [], set()
    for a in areas:
        if not NAME_OK.fullmatch(a["name"]):
            out.append(f"{a['name']}: 名前は英数字と _ - だけ (画像のファイル名になる)")
        if a["name"].casefold() in seen:
            out.append(f"{a['name']}: 名前が重なっている (大文字小文字だけの違いも重なりとみなす)")
        seen.add(a["name"].casefold())
        if a["w"] <= 0 or a["h"] <= 0:
            out.append(f"{a['name']}: 幅と高さは 1 以上")
        elif a["x"] < 0 or a["y"] < 0 or a["x"] + a["w"] > W or a["y"] + a["h"] > H:
            out.append(f"{a['name']}: 動画 ({W}x{H}) の外にはみ出す ({a['x']},{a['y']},{a['w']},{a['h']})")
    if not areas:
        out.append("見る範囲が無い (--area か --areas で渡す)")
    return out


def build_filter(areas):
    """見る範囲だけを切り出し、縦に積んだ 1 つの RGB の画にする filter_complex の文字列。

    4K のフレームを丸ごと RGB に直すと重いので、YUV のまま偶数にそろえた少し広い範囲を切り、
    RGB に直してから正確な範囲を切る (4:2:0 の色差は 2 画素単位なので、奇数の位置は YUV のままでは切れない)。
    幅は最も広い範囲にそろえ (右を黒で埋める)、上から areas の順に積む。
    """
    wmax = max(a["w"] for a in areas)
    chains = []
    for i, a in enumerate(areas):
        x0, y0 = a["x"] - a["x"] % 2, a["y"] - a["y"] % 2
        w0, h0 = a["x"] + a["w"] - x0, a["y"] + a["h"] - y0
        w0, h0 = w0 + w0 % 2, h0 + h0 % 2
        chains.append(f"crop={w0}:{h0}:{x0}:{y0},format=rgb24,crop={a['w']}:{a['h']}:{a['x'] - x0}:{a['y'] - y0},"
                      f"pad={wmax}:{a['h']}:0:0")
    if len(areas) == 1:
        return f"[0:v:0]{chains[0]}[out]"
    head = f"[0:v:0]split={len(areas)}" + "".join(f"[s{i}]" for i in range(len(areas)))
    body = ";".join(f"[s{i}]{c}[c{i}]" for i, c in enumerate(chains))
    return f"{head};{body};" + "".join(f"[c{i}]" for i in range(len(areas))) + f"vstack=inputs={len(areas)}[out]"


def probe(video):
    """動画を ffprobe で 1 回だけ測る。返す値は (info, stream)。
    info は common.probe_video と同じ {"width", "height", "fps", "duration"} (読み方は common.video_info で同じ)。
    stream は映像の stream の項目の dict (nb_frames・duration。入れ物によっては無い)。expected_frames に渡す。
    入れ物に長さが無い動画は、probe_video と同じく止まる。"""
    entries = "stream=width,height,r_frame_rate,nb_frames,duration:format=duration"
    r = C.run_tool("ffprobe", ["-select_streams", "v:0", "-show_entries", entries, "-of", "json", video])
    j = json.loads(r.stdout)
    return C.video_info(j), j["streams"][0]


def expected_frames(stream, fps, fallback_duration):
    """映像の stream のフレームの数の見込み。nb_frames があればそれ、無ければ映像の stream の長さ x fps。

    stream は probe が返す映像の stream の dict。
    入れ物全体の長さ (format=duration) は音声が映像より長いとその分だけ長くなるので、見込みには使わない。
    映像の stream の長さも取れないときだけ fallback_duration (入れ物全体の長さ) を使う。
    """
    st = stream or {}
    for key, conv in (("nb_frames", int), ("duration", lambda v: round(float(v) * fps))):
        try:
            v = conv(st[key])
            if v > 0:
                return v
        except (KeyError, TypeError, ValueError):
            pass
    return round(fallback_duration * fps)


def changed_pixels(a, b, level):
    """2 つの画 (高さ, 幅, 3) で、色のどれかが level より大きく違う画素の数。"""
    import numpy as np
    d = np.maximum(a, b)
    d -= np.minimum(a, b)          # uint8 のまま差の大きさ (int16 に広げて max を取るより約 5 倍速い)
    m = (d > level).view(np.uint8).reshape(-1, 3)
    return int(np.count_nonzero(m[:, 0] | m[:, 1] | m[:, 2]))


# ---------- 並べた画像 ----------
class SheetWriter:
    """1 つの見る範囲の、残したフレームを縦に並べた画像を書く。rows x cols 個たまるたびに 1 枚書き、元の大きさのフレームは持たない。

    1 つの段は、左に番号と時刻の欄、右に見る範囲 (幅が max_width を超えるときだけ縮める)。字は範囲の上に重ねない (漏れを隠さないため)。
    段は上から下へ並べ、rows 段で次の列へ移る。1 枚の高さが max_height を超えないよう、段の数を減らす (少なくとも 1 段)。
    """

    def __init__(self, area, out_dir, rows, cols, max_width, fps, font, text_color, box_color, max_height=None):
        from PIL import Image, ImageDraw
        self.area, self.out_dir, self.rows, self.cols, self.fps = area, out_dir, rows, cols, fps
        self.font, self.text_color, self.box_color = font, text_color, box_color
        self.scale = min(1.0, max_width / area["w"])
        self.cw, self.ch = max(1, round(area["w"] * self.scale)), max(1, round(area["h"] * self.scale))
        box = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), LABEL_SAMPLE, font=font)
        self.label_w, self.text_h = box[2] + 16, box[3]
        self.row_h = max(self.ch, self.text_h + 8)
        if max_height:
            self.rows = max(1, min(rows, (max_height + GAP) // (self.row_h + GAP)))
        self.cells, self.sheets = [], []

    def add(self, n, img):
        from PIL import Image
        im = Image.fromarray(img)
        if self.scale < 1:
            im = im.resize((self.cw, self.ch), Image.BICUBIC)
        self.cells.append((n, im))
        if len(self.cells) >= self.rows * self.cols:
            self.flush()

    def flush(self):
        if not self.cells:
            return
        from PIL import Image, ImageDraw
        k = len(self.cells)
        cols = min(self.cols, math.ceil(k / self.rows))
        rows = min(self.rows, k)
        cell_w = self.label_w + self.cw
        sheet = Image.new("RGB", (cols * cell_w + (cols - 1) * GAP, rows * (self.row_h + GAP) - GAP), (96, 96, 96))
        d = ImageDraw.Draw(sheet)
        for i, (n, im) in enumerate(self.cells):
            x, y = (i // self.rows) * (cell_w + GAP), (i % self.rows) * (self.row_h + GAP)
            d.rectangle((x, y, x + cell_w - 1, y + self.row_h - 1), fill=self.box_color)
            d.text((x + 8, y + (self.row_h - self.text_h) // 2), frame_label(n, self.fps), font=self.font,
                   fill=self.text_color)
            sheet.paste(im, (x + self.label_w, y))
        path = os.path.join(self.out_dir, f"{self.area['name']}_{len(self.sheets) + 1:02d}.png")
        # 枚数と容量は呼び出し側が見る。ここは渡された段をそのまま書く。
        sheet.save(path)
        self.sheets.append(path)
        self.cells = []


# ---------- 本体 ----------
def scan(video, areas, out_dir, change_level, change_pixels, rows, cols, max_width, hwaccel="", progress=None,
         max_height=None, max_sheets=None, max_frames=None, max_output_mb=None, json_name=None):
    """動画を頭から 1 回だけ読み、見る範囲ごとに、最後に残したフレームから変わったフレームを残して並べる。

    最初のフレームは比べる元として必ず残す。比べる相手を 1 つ前のフレームではなく最後に残したフレームにするのは、
    少しずつ変わるもの (フェード) も、変わった量がたまった所で残すため。結果の dict を返す (JSON に書く中身)。
    max_height は 1 枚の高さの上限 (px)。None なら config の area_sheet.max_height。
    max_sheets・max_frames・max_output_mb は出力の上限。None は上限なし。0 以下は ValueError。
    max_sheets は見る範囲をまたいだ PNG の合計。書く直前に見て、超える 1 枚は残さない。
    容量を既に超えているときも次の PNG は書かない。上限に達したら、そのフレーム以降は残さず読みをやめる。
    画素の比べ方は変えない。戻りの incomplete が true。
    ちょうど上限まで書いて、落とせたフレームが無ければ incomplete は false。
    画像 (と json_name を渡せば結果の JSON) は一時のフォルダーに書き、最後まで読めたときだけ out_dir へ移す
    (上限で止めた未完了も移す)。読めなかったとき (read_problems) は RuntimeError で、out_dir の前回の結果を残す。
    """
    cap = _OutputCap(max_sheets, max_frames, max_output_mb)
    from PIL import ImageFont
    cfg = C.config()
    cs = cfg.section("contact_sheet")
    fontfile = cfg.font_file(cs["font_name"]) or cfg.font_file(cs["fallback_font_name"])
    if not fontfile:
        raise RuntimeError(f"番号と時刻の字の書体が見つからない ({cs['font_name']} / {cs['fallback_font_name']})")
    font = ImageFont.truetype(fontfile, 18)
    if max_height is None:
        max_height = cfg.section("area_sheet")["max_height"]
    info, stream = probe(video)
    bad = area_problems(areas, info["width"], info["height"])
    if bad:
        raise ValueError("; ".join(bad))
    temp = C.run_dir(out_dir)   # 画像と JSON はこの実行の一時のフォルダーに書き、最後まで済んだら out_dir へ移す
    try:
        res = _scan(video, areas, temp, info, stream, font, cs, change_level, change_pixels, rows, cols, max_width,
                    hwaccel, progress, max_height, cap)
        for a in res["areas"]:
            a["sheets"] = [os.path.join(out_dir, os.path.basename(p)) for p in a["sheets"]]
        if json_name:
            C.write_json(os.path.join(temp, json_name), res)
        # 前回の画像 (大文字小文字だけ違う名前、前回 ADDR も) は、今回作らなかった名前を消す。混ざらないように
        names = {a["name"].casefold() for a in areas}
        stale = [f for f in os.listdir(out_dir) if (m := re.fullmatch(r"(.+)_\d{2,}\.png", f, re.IGNORECASE))
                 and m.group(1).casefold() in names]
        C.publish_run(temp, out_dir, stale, json_name)
    except BaseException:
        C.discard_run(temp)   # 中断・FFmpeg の失敗・書けない画像・置き換えの失敗。前回の画像と JSON はそのまま残す
        raise
    return res


def read_problems(frames_read, frames_expected, returncode, errors):
    """動画を最後まで読めなかった理由 (文の list)。上限で読むのをやめたときは呼ばない (FFmpeg を止めたため)。"""
    out = []
    if frames_read == 0:
        out.append("フレームを 1 つも読めない")
    elif abs(frames_read - frames_expected) > 1:
        out.append(f"読めたフレームの数 {frames_read} が、映像の長さとフレームレートからの見込み {frames_expected} と違う")
    if returncode != 0 or errors:
        out.append(f"FFmpeg が終了コード {returncode} で終わった: " + " / ".join(errors))
    return out


def _scan(video, areas, out_dir, info, stream, font, cs, change_level, change_pixels, rows, cols, max_width, hwaccel,
          progress, max_height, cap):
    """scan の本体。画像は out_dir (この実行の一時のフォルダー) に書く。info・stream は probe の戻り。"""
    import numpy as np
    W, H, fps = info["width"], info["height"], info["fps"]
    writers = [SheetWriter(a, out_dir, rows, cols, max_width, fps, font, cs["text_color"], cs["box_color"],
                           max_height) for a in areas]
    wmax = max(a["w"] for a in areas)
    offs = [sum(a["h"] for a in areas[:i]) for i in range(len(areas))]
    size = wmax * sum(a["h"] for a in areas) * 3
    args = (["-hwaccel", hwaccel] if hwaccel else []) + [
        "-i", video, "-filter_complex", build_filter(areas), "-map", "[out]", "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p, th, err = None, None, []
    last = [None] * len(areas)
    kept = [[] for _ in areas]
    n, t0, shown, stop = 0, time.monotonic(), time.monotonic(), False
    try:
        p = C.ffmpeg_popen(args, stderr=True)

        def pump():   # 標準エラーを読み続ける (止めるとパイプが詰まる)。最後の数行だけ残す
            try:
                for line in p.stderr:
                    err.append(line.decode("utf-8", "replace").rstrip())
                    del err[:-20]
            except (ValueError, OSError):
                pass
        th = threading.Thread(target=pump, name="area-sheet-ffmpeg-stderr", daemon=True)
        th.start()
        while True:
            raw = p.stdout.read(size)
            if len(raw) < size:   # 読み終えた。FFmpeg の終わりとエラーの行を待つ
                p.wait()
                th.join(timeout=10)
                break
            frame = np.frombuffer(raw, np.uint8).reshape(-1, wmax, 3)
            for i, a in enumerate(areas):
                if cap.limit_hit:
                    stop = True
                    break
                img = frame[offs[i]:offs[i] + a["h"], :a["w"]]
                c = None if last[i] is None else changed_pixels(img, last[i], change_level)
                if c is None or c >= change_pixels:
                    # 残す前に上限を見る。達したフレームは記録にも画像にも残さない。
                    # 枚数は範囲の合計。開きの段が残っていても、次の画像を書けないならこのフレームは残さない。
                    # 容量を既に超えているときも書かない。どちらの確認も、段が空かどうかとは別に行う。
                    if cap.frame_full() or not cap.can_write_sheet():
                        stop = True
                        break
                    last[i] = img.copy()
                    kept[i].append({"frame": n, "time": round(n / fps, 3), "changed_pixels": c})
                    cap.changed += 1
                    before = len(writers[i].sheets)
                    writers[i].add(n, last[i])
                    if len(writers[i].sheets) > before:
                        cap.add_sheet(os.path.getsize(writers[i].sheets[-1]))
                    if cap.limit_hit:
                        stop = True
                        break
            n += 1
            if stop:
                break
            if progress and time.monotonic() - shown >= progress:
                shown = time.monotonic()
                print(f"  {n / fps:.0f} / {info['duration']:.0f} 秒", file=sys.stderr, flush=True)
    finally:
        if p is not None:
            C.stop_ffmpeg(p)
        if th is not None and th.ident is not None:
            th.join(timeout=10)
    if p is not None and p.returncode != 0 and n == 0 and cap.limit_hit is None:
        raise RuntimeError("FFmpeg が失敗した: " + " / ".join(err[-5:]))
    expected = expected_frames(stream, fps, info["duration"])
    if cap.limit_hit is None:   # 上限で読むのをやめていなければ、最後の段を書く前に読み終え方を確認する
        bad = read_problems(n, expected, None if p is None else p.returncode, err[-5:])
        if bad:   # 途中まで読めて FFmpeg が失敗した・フレームが足りない。前回の結果を置き換えない
            raise RuntimeError("; ".join(bad))
    _flush_pending(writers, kept, cap)
    sheet_paths = [path for w in writers for path in w.sheets]
    return {
        "video": os.path.abspath(video), "width": W, "height": H, "fps": fps, "duration": info["duration"],
        "frames_read": n, "frames_expected": expected,
        "settings": {"change_level": change_level, "change_pixels": change_pixels, "rows": rows, "cols": cols,
                     "max_width": max_width, "max_height": max_height, "hwaccel": hwaccel,
                     "max_sheets": cap.max_sheets, "max_frames": cap.max_frames,
                     "max_output_mb": cap.max_output_mb},
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "incomplete": cap.limit_hit is not None, "limit_hit": cap.limit_hit,
        "images_written": len(sheet_paths),
        "output_bytes": sum(os.path.getsize(path) for path in sheet_paths),
        "changed_frames": sum(len(k) for k in kept),
        "areas": [{**a, "kept_count": len(k), "sheets": w.sheets, "kept": k} for a, k, w in zip(areas, kept, writers)],
        "ffmpeg_returncode": None if p is None else p.returncode, "ffmpeg_errors": err[-5:],
    }


def summary(res):
    """画面の 1 行の要約。最後まで読めなかったときは頭に「未完了。」を付ける。"""
    per = "・".join(f"{a['name']} {a['kept_count']}" for a in res["areas"])
    sheets = sum(len(a["sheets"]) for a in res["areas"])
    head = "area_sheet: 未完了。" if res.get("incomplete") else "area_sheet: "
    return (f"{head}{res['frames_read']} フレームを見て、残したフレーム {per} (画像 {sheets} 枚)、"
            f"{res['elapsed_sec']:.1f} 秒")


def main():
    cfg = C.config()
    s = cfg.section("area_sheet")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--area", action="append", default=[], help="見る範囲 x,y,w,h か 名前=x,y,w,h (書き出しの画素)。何度でも")
    ap.add_argument("--areas", help="見る範囲の JSON ({\"areas\": [{\"name\", \"x\", \"y\", \"w\", \"h\"}, ...]})")
    ap.add_argument("--out", help="出力先のフォルダー (既定は 作業フォルダー\\<動画の名前>\\area-sheet)")
    ap.add_argument("--change-level", type=int, default=s["change_level"], help="画素が変わったとみなす色の差 (0〜255)")
    ap.add_argument("--change-pixels", type=int, default=s["change_pixels"], help="フレームを残す、変わった画素の数")
    ap.add_argument("--rows", type=int, default=s["rows"], help="1 列に並べる数")
    ap.add_argument("--cols", type=int, default=s["cols"], help="1 枚の列の数")
    ap.add_argument("--max-width", type=int, default=s["max_width"], help="見る範囲をこの幅 (px) より広ければ縮める")
    ap.add_argument("--max-height", type=int, default=s["max_height"], help="1 枚の高さの上限 (px)。超えるなら段を減らして画像を分ける")
    ap.add_argument("--max-sheets", type=_cli_positive_int, default=None,
                    help="書く PNG の枚数の上限 (見る範囲の合計)。省略すると上限は無い。1 以上。0 は無制限ではない")
    ap.add_argument("--max-frames", type=_cli_positive_int, default=None,
                    help="残すフレームの数の上限 (見る範囲をまたいで数える)。省略すると上限は無い。1 以上。0 は無制限ではない")
    ap.add_argument("--max-output-mb", type=_cli_positive_mb, default=None,
                    help="書いた PNG の合計の上限 (MB。1 MB = 1024×1024 バイト。正の小数も可)。省略すると上限は無い。0 以下は無制限ではない")
    ap.add_argument("--hwaccel", default=s["hwaccel"], help="FFmpeg の -hwaccel に渡す値 (空なら使わない)")
    a = ap.parse_args()
    try:
        areas = load_areas(a.area, a.areas)
    except (OSError, ValueError) as e:
        return C.report(False, f"area_sheet: 見る範囲を読めない ({e})")
    stem = os.path.splitext(os.path.basename(a.video))[0]
    out_dir = a.out or cfg.work_dir(stem, "area-sheet")
    try:
        res = scan(a.video, areas, out_dir, a.change_level, a.change_pixels, a.rows, a.cols, a.max_width, a.hwaccel,
                   progress=30, max_height=a.max_height, max_sheets=a.max_sheets, max_frames=a.max_frames,
                   max_output_mb=a.max_output_mb, json_name="area-sheet.json")
    except (RuntimeError, ValueError) as e:
        return C.report(False, f"area_sheet: 動かせない ({e})")
    path = os.path.join(out_dir, "area-sheet.json")
    if res["incomplete"]:   # 読めなかったとき (read_problems) は scan が RuntimeError にする
        return C.report(False, summary(res), path, [
            f"上限 {res['limit_hit']} に達したため未完了 "
            f"(読んだフレーム {res['frames_read']}、画像 {res['images_written']} 枚、{res['output_bytes']} バイト)"])
    return C.report(True, summary(res), path)


if __name__ == "__main__":
    sys.exit(main())
