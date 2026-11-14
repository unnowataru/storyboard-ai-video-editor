"""動画のフレームを、時刻付きで 1 枚の画像に並べる (場面探しと、書き出したあとの確認に使う)。

使い方:
  python scripts/contact_sheet.py 録画.mp4 [出力先]                      # 10 秒ごと、5x4 を何枚でも
  python scripts/contact_sheet.py 録画.mp4 [出力先] --step 2 --from 14:58 --to 15:30 --cols 4 --rows 4
  python scripts/contact_sheet.py 動画.mp4 [出力先] --frames 149,150,209,210   # 決まったフレーム (0 始まり) を 1 枚に
出力先の既定は 作業フォルダー\\<動画の名前>\\sheets。画像の左上に、元の動画の中の時刻 (h:mm:ss) が入る。
--from を付けても、その時刻からの通し時刻で出る。間隔・並べ方・1 フレームの幅・時刻の字の書体と色の既定は config の contact_sheet。
"""
import argparse
import math
import os
import re
import sys

import common as C


def esc_font(p):
    """drawtext の fontfile に渡すパス。区切りを / にし、: を \\: にする (フィルターの区切りと読まれないように)。"""
    return p.replace("\\", "/").replace(":", "\\:")


def build_filter(cs, start, fontfile, step, width, cols, rows):
    # round=up: 既定 (near) だと「0 秒」の位置に 1 フレーム手前 (0.97 秒) の画が入り、焼いた時刻とずれる
    return (f"fps=1/{step}:round=up,scale={width}:-1,"
            f"drawtext=fontfile='{esc_font(fontfile)}':text='%{{pts\\:hms\\:{start}}}':x=5:y=5:"
            f"fontsize={max(18, width // 16)}:fontcolor={cs['text_color']}:box=1:boxcolor={cs['box_color']},"
            f"tile={cols}x{rows}")


def frame_label(n, fps):
    """フレームの左上に入れる字: 番号と、番号 / fps の時刻 (h:mm:ss.mmm)。"""
    if not fps:
        return str(n)
    ms = round(n / fps * 1000)
    return f"{n}  {ms // 3600000}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def frames_sheet(video, frames, out_png, cols=None, width=None):
    """video の frames のフレームを 1 枚の画像 out_png に並べる (左上にフレーム番号と時刻)。作った画像のパスを返す。

    書き出したあとの確認で、区切りのフレームを人が見るために使う (scripts/check_final.py)。
    フレームは common.frame_reader で取り出す (近いフレームをまとめてシークで取り出し、check_final.py が先に取り出したものは使い回す)。
    並べるのと字を入れるのは Pillow。動画に無い番号は並べない。1 つも無ければ RuntimeError。
    """
    from PIL import Image, ImageDraw, ImageFont
    cfg = C.config()
    cs = cfg.section("contact_sheet")
    cols, width = cols or cs["cols"], width or cs["width"]
    fontfile = cfg.font_file(cs["font_name"]) or cfg.font_file(cs["fallback_font_name"])
    if not fontfile:
        sys.exit(f"時刻の字の書体が見つからない ({cs['font_name']} / {cs['fallback_font_name']})")
    font = ImageFont.truetype(fontfile, max(18, width // 16))
    reader = C.frame_reader(video)
    fps = reader.index().get("fps")
    cells = []
    for n, img in reader.each(frames, keep=False):   # 縮めたものだけを持つ (元の大きさのフレームを溜めない)
        if img is None:
            continue
        im = Image.fromarray(img)
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.BICUBIC)
        d = ImageDraw.Draw(im)
        text = frame_label(n, fps)
        box = d.textbbox((5, 5), text, font=font)
        d.rectangle(box, fill=cs["box_color"])
        d.text((5, 5), text, font=font, fill=cs["text_color"])
        cells.append((n, im))
    cells = [im for _, im in sorted(cells, key=lambda c: c[0])]   # 取り出せた順ではなく番号の順に並べる
    if not cells:
        raise RuntimeError(f"並べるフレームを取り出せない ({video})")
    cols = min(cols, len(cells))
    h = cells[0].height
    sheet = Image.new("RGB", (width * cols, h * math.ceil(len(cells) / cols)), "black")
    for i, im in enumerate(cells):
        sheet.paste(im, (i % cols * width, i // cols * h))
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    sheet.save(out_png)
    return out_png


def main():
    cfg = C.config()
    cs = cfg.section("contact_sheet")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--step", type=float, default=cs["step"], help="何秒ごとに 1 フレーム取るか")
    ap.add_argument("--from", dest="start", default="0")
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--cols", type=int, default=cs["cols"])
    ap.add_argument("--rows", type=int, default=cs["rows"])
    ap.add_argument("--width", type=int, default=cs["width"], help="1 フレームの幅 (px)")
    ap.add_argument("--name", default="sheet")
    ap.add_argument("--frames", help="並べるフレームの番号 (0 始まり、カンマ区切り)。左上にフレーム番号と時刻が入る")
    a = ap.parse_args()
    stem = os.path.splitext(os.path.basename(a.video))[0]
    out_dir = a.out_dir or cfg.work_dir(stem, "sheets")
    if a.frames:
        print("作った:", frames_sheet(a.video, [int(x) for x in a.frames.split(",")], os.path.join(out_dir, a.name + ".png"),
                                     a.cols, a.width))
        return
    os.makedirs(out_dir, exist_ok=True)
    s = C.tc_to_sec(a.start)
    e = C.tc_to_sec(a.end) if a.end else C.probe_video(a.video)["duration"]
    fontfile = cfg.font_file(cs["font_name"]) or cfg.font_file(cs["fallback_font_name"])
    if not fontfile:
        sys.exit(f"時刻の字の書体が見つからない ({cs['font_name']} / {cs['fallback_font_name']})。config の fonts と paths.fonts_dirs を確認する")
    vf = build_filter(cs, s, fontfile, a.step, a.width, a.cols, a.rows)
    out = os.path.join(out_dir, a.name + "_%02d.png")
    ours = lambda f: re.fullmatch(re.escape(a.name) + r"_\d{2,}\.png", f)   # このスクリプトが作る連番の画像だけ
    for f in filter(ours, os.listdir(out_dir)):   # 前回の画像が残ると、今回の確認に混ざる
        os.remove(os.path.join(out_dir, f))
    C.run_tool("ffmpeg", ["-ss", str(s), "-t", str(e - s), "-i", a.video, "-vf", vf, out])
    made = sorted(filter(ours, os.listdir(out_dir)))
    print("作った:", ", ".join(os.path.join(out_dir, f) for f in made))


if __name__ == "__main__":
    main()
