"""録画の中の IP アドレス・ホスト名・指定の文字列・画面の部品 (URL バーなど) を見つけて追いかけ、隠す位置を JSON に書く。

元の録画は加工しない。隠すのは Resolve の中 (Fusion のマスク) で行い、この JSON はその入力になる。
JSON の形と読み方は docs/track.md。

使い方:
  python scripts/track.py 録画.mp4 --ip --host "srv-*" --text admin --template 録画.marks.json:0
  --out 位置.json      既定は 作業フォルダー\\<録画の名前>\\<録画の名前>.track.json
  --keep-frames        文字認識に渡したフレームの画像を消さずに残す (作業フォルダーの track-frames)

しくみ:
  画面が変わったフレームと、少なくとも track.stride_sec ごとに 1 フレームを取り出し、Windows 標準の文字認識 (ocr_windows.ps1) で読む。
  止まっている間は読まない (前の結果がそのまま続く)。部品は marker.html で囲んだ領域を見本にして、OpenCV で照合する。
  認識が抜けたフレームは、前後の読み取りと、その場所の画が変わったかどうかで補う (fill_misses)。
  文字認識は手元の Windows で動き、画面の中身を外に送らない。JSON には見つけた文字そのものは書かず、種類と番号 (ip-1 など) だけを書く。
数の値 (読む間隔、変化の判定、照合の一致度、余白など) は config/defaults.json の track。
"""
import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image

import common as C

IP = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
HOSTISH = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
FORMAT_VERSION = 1


def settings():
    return C.config().section("track")


def frames(video, w, h):
    """フレームを 1 枚ずつ (高さ, 幅, 3) の配列で返す。"""
    p = subprocess.Popen([C.config().tool("ffmpeg"), "-v", "error", "-i", video, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, creationflags=C.LOW_PRIORITY)
    size, n = w * h * 3, 0
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        n += 1
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    if p.wait() != 0 or n == 0:   # 読めなかった動画を「0 フレームで何も無い」にしない
        raise RuntimeError(f"動画を読めなかった: {video} (FFmpeg の終了コード {p.returncode}、{n} フレーム)")


def grab(video, t, w, h):
    """t 秒のフレームを 1 枚、灰色で返す (部品の見本を先に取るため)。"""
    buf = subprocess.run([C.config().tool("ffmpeg"), "-v", "error", "-ss", str(t), "-i", video, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(buf[:w * h], np.uint8).reshape(h, w)


def changed(p, q, s):
    """原寸の灰色の画で、明るさが change_level を超えて変わった画素が change_pixels 個以上あるか。
    縮めた画や画面全体の平均だと、細い文字や小さな通知が 1 か所だけ現れたのを見落とす。"""
    return int((cv2.absdiff(p, q) > s["change_level"]).sum()) >= s["change_pixels"]


def sample(video, info, templates, tmp, need_ocr, s):
    """読み取るフレームを選び、PNG (文字認識用に ocr_scale 倍) と部品の照合結果を返す。
    画面が 1 フレーム前から変わったフレームは全部読む。最後に読んだフレームからの変化が積み重なったとき (ゆっくり現れる文字) も読む。
    背景と明るさの差が小さい文字は変化として拾えないので、変化が無くても stride_sec ごとに 1 回は読む
    (stride_sec より短く出て消える、背景と見分けにくい文字は見逃しうる)。戻り値は (読んだフレーム, 総フレーム数)。"""
    w, h, k = info["width"], info["height"], s["ocr_scale"]
    stride = max(1, round(info["fps"] * s["stride_sec"]))
    shots, total, prev, last_seen, last_i = [], 0, None, None, 0
    for i, f in enumerate(frames(video, w, h)):
        gray = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        mv = prev is not None and changed(gray, prev, s)
        drift = last_seen is not None and changed(gray, last_seen, s)
        prev, total = gray, i + 1
        if not (i == 0 or mv or drift or i - last_i >= stride):
            continue
        last_seen, last_i = gray, i
        # stride: 目に見える変化は無く、定期の読み取りで読んだ。ここで見つかった文字は前からあったとみなす
        shot = {"i": i, "boxes": [], "stride": not (i == 0 or mv or drift)}
        if need_ocr:
            shot["png"] = os.path.join(tmp, f"f{i:07d}.png")
            Image.fromarray(f).resize((w * k, h * k), Image.LANCZOS).save(shot["png"])
        for n, t in enumerate(templates):
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, t["img"], cv2.TM_CCOEFF_NORMED))
            if score >= s["template_score"]:
                shot["boxes"].append([x, y, t["region"]["w"], t["region"]["h"], f"template-{n}"])
        shots.append(shot)
    return shots, total


def ocr(shots, tmp):
    """Windows 標準の文字認識で、読んだフレームの画像をまとめて読む。{画像のパス: [単語, ...]} を返す。"""
    lst = os.path.join(tmp, "list.txt")
    out = os.path.join(tmp, "ocr.json")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(s["png"] for s in shots))
    ps1 = os.path.join(C.SCRIPTS, "ocr_windows.ps1")
    ps = C.config().section("apps")["windows_powershell"]   # WinRT を呼ぶので Windows PowerShell 5.1 で動かす
    subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-List", lst, "-Out", out],
                   check=True, stdin=subprocess.DEVNULL, creationflags=C.LOW_PRIORITY)
    with open(out, encoding="utf-8-sig") as f:
        return json.load(f)


def edits(x, y):
    """2 つの文字列の編集距離 (1 文字の欠け・余分・置き換えを 1 と数える)。"""
    d = list(range(len(y) + 1))
    for i, cx in enumerate(x, 1):
        prev, d[0] = d[0], i
        for j, cy in enumerate(y, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (cx != cy))
    return d[-1]


def find(words, a, scale):
    """文字認識の単語から、隠す範囲 (元の解像度の四角) と、見つけた文字列を返す。一致した単語は単語の四角全体を隠す (文字幅は
    均等でないので、単語の一部だけを割合で切るとはみ出す)。--text は行の中で探し、一致した範囲にかかる単語をまとめて隠す。
    戻り値の範囲は [x, y, w, h, (種類, 見つけた文字列)]。文字列は呼ぶ側で番号に置き換える (JSON に文字そのものを残さない)。"""
    boxes = []
    box = lambda wd, key: [wd["x"] / scale, wd["y"] / scale, wd["w"] / scale, wd["h"] / scale, key]
    lines = {}
    for wd in words:
        lines.setdefault(wd.get("l", 0), []).append(wd)
        text = wd["text"]
        if a.ip and IP.search(text):
            boxes.append(box(wd, ("ip", IP.search(text).group())))
        hit = next((m.group() for m in HOSTISH.finditer(text)
                    if any(fnmatch.fnmatch(m.group().lower(), p.lower()) for p in a.host)), None)
        if hit:
            boxes.append(box(wd, ("host", hit)))
    norm = lambda s: [c.lower() for c in s if c.isalnum()]
    for ws in lines.values():
        # 行の文字から記号と空白を除いてつなぐ (文字認識は「Save File」を 1 語に読んだり、r を「 に読んだりする)
        chars, owner = [], []
        for n, wd in enumerate(ws):
            for c in norm(wd["text"]):
                chars.append(c)
                owner.append(n)
        line = "".join(chars)
        for t in a.text:
            want = "".join(norm(t))
            if not want:
                continue
            hits = [(m.start(), m.end()) for m in re.finditer(re.escape(want), line)]
            if len(want) >= 5:   # 読み誤り 1 文字 (欠け・余分・置き換え) までは一致とみなす。完全一致があっても探す
                for L in (len(want) - 1, len(want), len(want) + 1):
                    hits += [(s, s + L) for s in range(0, len(line) - L + 1) if edits(want, line[s:s + L]) <= 1]
            for n in sorted({owner[k] for s, e in hits for k in range(s, e)}):
                boxes.append(box(ws[n], ("text", t)))
    return boxes


class Labels:
    """見つけた文字列を、種類ごとの通し番号 (ip-1、host-2、text-1 など) に置き換える。同じ文字列は同じ番号になる。"""

    def __init__(self):
        self.ids = {}

    def __call__(self, key):
        kind, value = key
        if (kind, value) not in self.ids:
            self.ids[(kind, value)] = f"{kind}-{sum(1 for k in self.ids if k[0] == kind) + 1}"
        return self.ids[(kind, value)]


def boxes_at(i, shots, pos):
    """フレーム i の隠す範囲 = 直前に読んだフレームの隠す範囲 (読んでいないフレームは、前のフレームから画面が変わっていない)。
    次の読み取りが定期の読み取り (目に見える変化なし) なら、そこで見つかった範囲も追加する (背景と見分けにくい文字は、
    現れても変化として拾えず、定期の読み取りで初めて見つかるので、それまでのフレームにもさかのぼって隠す)。"""
    while pos + 1 < len(shots) and shots[pos + 1]["i"] <= i:
        pos += 1
    nxt = shots[pos + 1] if pos + 1 < len(shots) else None
    if nxt is not None and nxt.get("stride"):   # 直前の読み取りのフレーム自体も含めて隠す
        return shots[pos]["boxes"] + nxt["boxes"], pos
    return shots[pos]["boxes"], pos


def hull(p, q):
    x0, y0 = min(p[0], q[0]), min(p[1], q[1])
    x1, y1 = max(p[0] + p[2], q[0] + q[2]), max(p[1] + p[3], q[1] + q[3])
    return [x0, y0, x1 - x0, y1 - y0, p[4]]


def covered(p, boxes):
    """p がどれかの範囲にすっぽり収まるか (少し重なるだけでは隠れていない)。"""
    return any(b[0] <= p[0] and b[1] <= p[1] and p[0] + p[2] <= b[0] + b[2] and p[1] + p[3] <= b[1] + b[3] for b in boxes)


def fill_misses(shots, s):
    """読み取ったフレームで認識が抜けた対象を、出現の場所ごとに補う。
    1. ある読み取りの対象が、次の読み取りで抜け、lookahead 回先までに同じ文字列 (同じ番号) が読めていれば、間の読み取りに
       2 つの位置を含む四角を追加する。同じ番号が複数あるときは、近いものから 1 対 1 で対応させる
    2. 前の読み取りの範囲が、次の読み取りのどの範囲にもすっぽり収まらず、その場所の画が変わっていなければ
       (ocr_scale 倍の画像で比べる)、文字はまだそこにあるとみなして範囲を引き継ぐ (続けて抜けた場合、最後の読み取りで抜けた場合)
    3. 2 と同じ判定を逆の向きにも行う。次の読み取りの範囲が前の読み取りに無く、その場所の画が変わっていなければ、前にも追加する"""
    add = [[] for _ in shots]
    for k, sh in enumerate(shots):
        left = list(range(len(sh["boxes"])))   # まだ先の出現と対応していない範囲
        for j in range(k + 1, min(k + 1 + s["lookahead"], len(shots))):
            nb = shots[j]["boxes"]
            pairs = sorted((abs(nb[n][0] - sh["boxes"][i][0]) + abs(nb[n][1] - sh["boxes"][i][1]), i, n)
                           for i in left for n in range(len(nb)) if nb[n][4] == sh["boxes"][i][4])
            done_i, done_n = set(), set()
            for _, i, n in pairs:   # 近い組から 1 対 1 で決める
                if i in done_i or n in done_n:
                    continue
                done_i.add(i)
                done_n.add(n)
                h = hull(sh["boxes"][i], nb[n])
                for m in range(k + 1, j):
                    if not covered(h, shots[m]["boxes"]):
                        add[m].append(h)
            left = [i for i in left if i not in done_i]
            if not left:
                break
    for sh, extra in zip(shots, add):
        sh["boxes"] += extra
    k_ = s["ocr_scale"]
    img = lambda sh: cv2.imread(sh["png"], cv2.IMREAD_GRAYSCALE) if sh.get("png") else None

    def carry(src, dst, src_img, dst_img):
        """src の範囲のうち、dst のどの範囲にも収まらず、その場所の画が src と dst で変わっていないものを dst に追加する。"""
        if src_img is None or dst_img is None:
            return
        for p in list(src["boxes"]):
            if covered(p, dst["boxes"]):
                continue
            x0, y0 = max(0, int(p[0] * k_)), max(0, int(p[1] * k_))
            x1, y1 = int((p[0] + p[2]) * k_) + 1, int((p[1] + p[3]) * k_) + 1
            a, b = src_img[y0:y1, x0:x1], dst_img[y0:y1, x0:x1]
            if a.size and float(cv2.absdiff(a, b).mean()) < s["still_level"]:
                dst["boxes"].append(list(p))

    # 2. 後ろへ引き継ぐ
    prev_img = img(shots[0]) if shots else None
    for k in range(1, len(shots)):
        cur_img = img(shots[k])
        carry(shots[k - 1], shots[k], prev_img, cur_img)
        prev_img = cur_img
    # 3. 前へさかのぼる: 文字が現れた直後のフレームは圧縮で崩れて読み誤ることがある (合成の動画で、出た最初のフレームの
    #    IP アドレスが 1 文字欠けて読めなかった)。その場所の画が変わっていなければ、前の読み取りにも範囲を追加する
    nxt_img = img(shots[-1]) if shots else None
    for k in range(len(shots) - 2, -1, -1):
        cur_img = img(shots[k])
        carry(shots[k + 1], shots[k], nxt_img, cur_img)
        nxt_img = cur_img


def _box_dict(b):
    return {"label": b[4], "x": round(b[0], 1), "y": round(b[1], 1), "w": round(b[2], 1), "h": round(b[3], 1)}


def segments(shots, total, fps):
    """読み取りの結果を、フレームごとの隠す範囲に広げ、範囲の組が同じフレームの続きを 1 つの segment にまとめる。
    範囲が 1 つも無いフレームの続きは出さない。end は含まない (start <= フレーム < end)。"""
    out, pos, cur = [], 0, None
    for i in range(total):
        if not shots:
            break
        bx, pos = boxes_at(i, shots, pos)
        uniq = []
        for b in (_box_dict(b) for b in bx):
            if b not in uniq:
                uniq.append(b)
        uniq.sort(key=lambda d: (d["label"], d["y"], d["x"], d["w"], d["h"]))
        if cur is not None and cur["boxes"] == uniq:
            cur["end"] = i + 1
            continue
        if cur is not None and cur["boxes"]:
            out.append(cur)
        cur = {"start": i, "end": i + 1, "boxes": uniq}
    if cur is not None and cur["boxes"]:
        out.append(cur)
    for seg in out:
        seg["start_sec"] = round(seg["start"] / fps, 3)
        seg["end_sec"] = round(seg["end"] / fps, 3)
    return out


def load_templates(specs, video, w, h):
    """marks.json:番号 から部品の見本を取る (見本の時刻のフレームを、元の録画から切り出す)。"""
    out = []
    for spec in specs:
        path, idx = spec.rsplit(":", 1)
        with open(path, encoding="utf-8") as f:
            r = json.load(f)["regions"][int(idx)]
        g = {k: int(r[k]) for k in ("x", "y", "w", "h")}
        img = grab(video, r["t"], w, h)[g["y"]:g["y"] + g["h"], g["x"]:g["x"] + g["w"]].copy()
        out.append({"region": g, "img": img, "shape": r.get("shape", "rect")})
    return out


def default_out(video):
    stem = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(C.config().work_dir(stem), stem + ".track.json")


def run(a):
    s = settings()
    info = C.probe_video(a.video)
    w, h = info["width"], info["height"]
    a.template = [os.path.abspath(p) + ":" + i for p, i in (x.rsplit(":", 1) for x in a.template)]
    templates = load_templates(a.template, a.video, w, h)   # 見本は先に取る (見本の時刻より前のフレームでも照合する)
    out = os.path.abspath(a.out or default_out(a.video))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 文字認識に渡す画像の置き場所。%TEMP% (AppData の下) はパッケージ化されたアプリ (Microsoft Store から入れた Python など) から書くと別の場所に振り替えられ、
    # 文字認識の PowerShell から見えないことがあるので、作業フォルダーの下に置く
    tmp = os.path.join(os.path.dirname(out), "track-frames")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    need_ocr = bool(a.ip or a.host or a.text)   # 部品の照合だけなら文字認識は要らない
    labels = Labels()
    try:
        shots, total = sample(a.video, info, templates, tmp, need_ocr, s)
        if need_ocr:
            words = ocr(shots, tmp)
            for sh in shots:
                sh["boxes"] += [b[:4] + [labels(b[4])] for b in find(words.get(sh["png"], []), a, s["ocr_scale"])]
        fill_misses(shots, s)   # 画像を比べるので、画像が残っている間に行う
    finally:
        if not a.keep_frames:
            shutil.rmtree(tmp, ignore_errors=True)
    segs = segments(shots, total, info["fps"])
    doc = {
        "version": FORMAT_VERSION,
        "video": os.path.abspath(a.video),
        "width": w, "height": h, "fps": info["fps"], "frames": total,
        "search": {"ip": a.ip, "host": len(a.host), "text": len(a.text), "templates": a.template},
        "templates": [{"label": f"template-{n}", "shape": t["shape"]} for n, t in enumerate(templates)],
        "pad": s["pad"],
        "frames_read": len(shots),
        "segments": segs,
    }
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    found = sorted({b["label"] for g in segs for b in g["boxes"]})
    return {"output": out, "frames": total, "frames_read": len(shots), "segments": len(segs), "labels": found,
            "note": "見つけた位置だけを書いた。文字認識が読めない文字は見逃すので、完成版は人が目で確認する"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--ip", action="store_true", help="IP アドレスを探す")
    ap.add_argument("--host", action="append", default=[], help="探すホスト名の形 (例: srv-*)。何度でも")
    ap.add_argument("--text", action="append", default=[], help="探す文字列 (大文字小文字を区別しない)。何度でも")
    ap.add_argument("--template", action="append", default=[], help="marks.json:番号 で、marker.html で囲んだ領域を部品として追う")
    ap.add_argument("--out", help="位置の JSON の保存先 (既定は作業フォルダーの <録画の名前>.track.json)")
    ap.add_argument("--keep-frames", action="store_true", help="文字認識に渡したフレームの画像を残す")
    a = ap.parse_args()
    if not (a.ip or a.host or a.text or a.template):
        ap.error("--ip / --host / --text / --template のどれかを指定する")
    r = run(a)
    # 見つけた文字そのものは出さない (番号だけ)。見逃しうることの注意は docs/track.md にある
    C.report(True, f"track: segment {r['segments']}、読んだフレーム {r['frames_read']}/{r['frames']}、"
                   f"見つけたもの {', '.join(r['labels']) or 'なし'}", r["output"])


if __name__ == "__main__":
    main()
