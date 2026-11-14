"""枠線の色 auto (背景の明るさで黄と赤を決める) と、callout の order (重ねる順)・dst の省略 (自動の置き場所) を、
Resolve で組んで書き出したフレームの色と位置で確認する。

機密の無い 4K のテストの画 (3840x2160、30 fps、12 秒) を作業フォルダーに作る。左半分は黒い画面に白い字、右半分は白い画面に
黒い字。左半分に色の帯 (緑・青・赤紫)、右半分に水色の帯を置き、callout の切り抜きの元にする。構成表は 4 行。
- 1 行目: 左 (暗い所) の focus に border {"color": "auto"}。黄 (focus_border.auto.dark) で書き出されること
- 2 行目: 右 (明るい所) の focus に border true。構成表の全体の focus_border.color が auto。赤 (auto.light) で書き出されること
- 3 行目: callout の src に 3 つの帯 (緑・青・赤紫) を書いた順に置き、order [3, 1, 2] と dst の省略。
  上から赤紫・緑・青の順に重なり、画面の横の中央で、テロップの背景のすぐ上 (margin を空けて) に出ること
- 4 行目: テロップの無い行の callout (水色の帯 1 つ、dst の省略)。画面の横の中央で、セーフエリアの下に出ること
sb2resolve.py で組んで書き出し、記録の auto (決めた色・置き場所・重ねる順)、check_final.py (--pixel。関数と、
コマンドとして呼んだもの) と、書き出したフレームの色で確認する。
作ったプロジェクト (test-auto_border_callout-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/auto_border_callout.py
(Resolve Studio を起動しておく。結果は 出力先/tests/auto_border_callout.json)
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts")
sys.path.insert(0, SCRIPTS)
import check_final as CF  # noqa: E402
import common as C  # noqa: E402
from resolve_helpers import finish_projects, make_check, require_lock, run_storyboard  # noqa: E402

cfg = C.config()
OUT = cfg.out_dir("tests")
NAME = "auto_border_callout"
SRC_W, SRC_H, FPS, SECONDS = 3840, 2160, 30, 12
TL_W, TL_H = 1920, 1080
# 素材の画素の四角と色。focus の範囲は字のまわり。帯は focus の範囲のまわりの帯 (24 画素) に掛からない所
DARK_FOCUS = (240, 400, 1300, 320)
LIGHT_FOCUS = (2140, 400, 1300, 320)
STRIPS = [((300, 1150, 800, 80), (0, 200, 0)),       # src 1: 緑
          ((300, 1300, 600, 80), (40, 80, 255)),     # src 2: 青
          ((300, 1450, 700, 80), (230, 0, 230))]     # src 3: 赤紫
CYAN = ((2300, 1200, 600, 100), (0, 200, 200))
ORDER = [3, 1, 2]
checks, info = [], {}
check = make_check(checks)


# ---------- 素材 ----------
def make_source():
    """左半分が黒い画面に白い字、右半分が白い画面に黒い字の 4K の 12 秒の動画。あれば作り直さない。"""
    path = os.path.join(cfg.work_dir("test-media"), "testsrc-4k-dark-light-bt709.mp4")   # 色は BT.709
    if os.path.isfile(path):
        return path
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img = np.zeros((SRC_H, SRC_W, 3), np.uint8)
    img[:, SRC_W // 2:] = 255
    for (x, y, w, h), rgb in STRIPS + [CYAN]:
        img[y:y + h, x:x + w] = rgb
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    font_file = C.caption_font(cfg.storyboard_values()[0])
    font = ImageFont.truetype(font_file, 150) if font_file else ImageFont.load_default(150)
    # 字は focus の範囲の辺から 40 画素以上内側 (枠線の色を測る帯の 24 画素に掛けない)
    d.text((DARK_FOCUS[0] + 60, DARK_FOCUS[1] + 70), "DARK 0123", fill=(255, 255, 255), font=font)
    d.text((LIGHT_FOCUS[0] + 60, LIGHT_FOCUS[1] + 70), "LIGHT 4567", fill=(0, 0, 0), font=font)
    png = path[:-4] + ".png"
    im.save(png)
    C.run_tool("ffmpeg", ["-loop", "1", "-framerate", str(FPS), "-i", png, "-t", str(SECONDS), "-vf", C.BT709_VF,
                          *C.BT709_TAGS, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", path])
    os.remove(png)
    return path


def box(b):
    return dict(zip("xywh", b))


def storyboard(src):
    focus = {"dim": 0.5, "feather": 0, "start": 0.5, "seconds": 0.3}
    return {"source": src, "output": os.path.join(OUT, f"{NAME}.mp4"), "fps": FPS, "seconds_per_row": 3,
            "focus_border": {"color": "auto", "width": 8},
            "rows": [
                {"start": 0, "end": 3, "caption": "1 行目: 暗い所の枠線 (auto)",
                 "focus": dict(box(DARK_FOCUS), **focus, border={"color": "auto"})},
                {"start": 3, "end": 6, "caption": "2 行目: 明るい所の枠線 (全体の auto)",
                 "focus": dict(box(LIGHT_FOCUS), **focus, border=True)},
                {"start": 6, "end": 9, "caption": "3 行目: 重ねる順と自動の置き場所",
                 "callout": {"src": [box(b) for b, _ in STRIPS], "order": ORDER, "start": 0.3, "in": 0.2}},
                {"start": 9, "end": 12,
                 "callout": {"src": box(CYAN[0]), "start": 0.3, "in": 0.2}}]}


# ---------- 書き出したフレームの色 ----------
def patch(img, x, y, r=2):
    return img[int(y) - r:int(y) + r + 1, int(x) - r:int(x) + r + 1].reshape(-1, 3).mean(axis=0)


def near(px, rgb, tol=50):
    return all(abs(float(a) - b) <= tol for a, b in zip(px, rgb))


def rgb_list(px):
    return [round(float(v)) for v in px]


def border_colors(img, b):
    """focus の範囲 b (素材の画素) の 4 辺の中点 (書き出しの画素) の色。枠線は辺の上に幅 8 px で乗る。"""
    x, y, w, h = [v / 2 for v in b]
    pts = {"左": (x, y + h / 2), "右": (x + w, y + h / 2), "上": (x + w / 2, y), "下": (x + w / 2, y + h)}
    return {k: patch(img, px, py, 1) for k, (px, py) in pts.items()}


def run_extent(img, y, x0, x1, rgb, tol=50):
    """y の行で、x0〜x1 の間の rgb に近い画素の左端と右端。無ければ None。"""
    xs = [x for x in range(int(x0), int(x1)) if near(img[int(y), x], rgb, tol)]
    return (xs[0], xs[-1]) if xs else None


def col_extent(img, x, y0, y1, rgb, tol=50):
    """x の列で、y0〜y1 の間の rgb に近い画素の上端と下端。無ければ None。"""
    ys = [y for y in range(int(y0), int(y1)) if near(img[y, int(x)], rgb, tol)]
    return (ys[0], ys[-1]) if ys else None


def pixel_checks(video, exp, auto, look):
    starts = {r["row"]: r["start"] for r in exp}

    def frame(row, f):
        return CF.read_frame(video, starts[row] + f, TL_W, TL_H)
    a = look["focus_border"]["auto"]
    dark, light = C.hex_rgb(a["dark"]), C.hex_rgb(a["light"])
    by_row = {d["row"]: d for d in auto}
    # 1・2 行目: 枠線の色 (記録の auto と、書き出しの画素)
    for row, b, want, other, side in ((1, DARK_FOCUS, dark, light, "暗い所"), (2, LIGHT_FOCUS, light, dark, "明るい所")):
        rec = (by_row.get(row) or {}).get("focus_border") or {}
        check(f"{row} 行目: 記録の auto の枠線の色が {side} の色", C.hex_rgb(rec.get("color", "#000000")) == want,
              rec, a["light" if want == light else "dark"])
        cols = border_colors(frame(row, 45), b)
        ok = all(near(p, want) and not near(p, other) for p in cols.values())
        check(f"{row} 行目 45 フレーム: 枠線の 4 辺が {side} の色 {want}", ok, {k: rgb_list(p) for k, p in cols.items()}, want)
        cols0 = border_colors(frame(row, 0), b)
        check(f"{row} 行目 0 フレーム: 枠線はまだ出ていない (start 0.5 秒)",
              not any(near(p, want, 40) for p in cols0.values()), {k: rgb_list(p) for k, p in cols0.items()})
    # 3 行目: 重ねる順と自動の置き場所
    rec = by_row.get(3) or {}
    check("3 行目: 記録の auto の callout_order が order のとおり", rec.get("callout_order") == ORDER,
          rec.get("callout_order"), ORDER)
    d = rec.get("callout_dst")
    check("3 行目: 記録の auto に callout_dst (自動の置き場所) がある", bool(d), d)
    sa = C.safe_area(look)
    co = look["callout"]
    bw = float(co["border"]["width"])
    below = float(co["shadow_offset"]) if co.get("shadow") else 0.0
    margin = float(co["auto_dst"]["margin"])
    if d:
        img = frame(3, 45)
        m = d["w"] / max(b[2] for b, _ in STRIPS)
        gap = float(co["gap"])
        tops, y = [], d["y"]
        for i in ORDER:   # 上から order の順
            h = STRIPS[i - 1][0][3] * m
            tops.append((i, y, h))
            y += h + gap
        seen = []
        for i, y0, h in tops:
            p = patch(img, d["x"] + 40, y0 + h / 2)
            seen.append(rgb_list(p))
            check(f"3 行目: 上から {len(seen)} 番目に src {i} の帯 {STRIPS[i - 1][1]} が重なる", near(p, STRIPS[i - 1][1]),
                  rgb_list(p), STRIPS[i - 1][1])
        info["row3_pieces"] = {"tops": tops, "colors": seen}
        # 位置は計算 (common.callout_values) を使わずに画素で測る: 緑 (いちばん幅の広い切り抜き) の左右の端と、青 (いちばん下) の下の端
        gy = tops[ORDER.index(1)][1] + tops[ORDER.index(1)][2] / 2
        ext = run_extent(img, gy, 0, TL_W, STRIPS[0][1])
        mid = None if not ext else (ext[0] + ext[1] + 1) / 2
        check("3 行目: 重ねた切り抜きが画面の横の中央 (緑の帯の左右の端の中央が 960 から 2 px 以内)",
              mid is not None and abs(mid - TL_W / 2) <= 2, ext, TL_W / 2)
        ve = col_extent(img, d["x"] + 40, d["y"], TL_H, STRIPS[1][1])
        # テロップの背景の上の端: 右の端 (白い画面。callout と影に掛からない) を下に向かって見て、背景で暗くなる所
        bar_top = next((y for y in range(int(d["y"]), TL_H) if float(patch(img, TL_W - 30, y, 1).mean()) < 225), None)
        space = None if not ve or bar_top is None else bar_top - (ve[1] + 1)
        lo, hi = margin + bw - 2, margin + bw + below + 2
        info["row3_measured"] = {"green_x": ext, "blue_y": ve, "caption_bar_top": bar_top, "space": space}
        check(f"3 行目: 重ねた全体がテロップの背景のすぐ上 (青の帯の下の端から背景まで {lo:g}〜{hi:g} px)",
              space is not None and lo <= space <= hi, space, [lo, hi])
        check("3 行目: 重ねた全体がセーフエリアの中", ext is not None and ext[0] >= sa["x0"] and ext[1] < sa["x1"]
              and d["y"] >= sa["y0"], ext, [sa["x0"], sa["x1"]])
    # 4 行目: テロップの無い行。セーフエリアの下の端から margin・縁・影の分を空けた所
    d4 = (by_row.get(4) or {}).get("callout_dst")
    check("4 行目: 記録の auto に callout_dst がある", bool(d4), d4)
    if d4:
        img = frame(4, 45)
        cx = d4["x"] + 40
        ve = col_extent(img, cx, 0, TL_H, CYAN[1])
        he = None if not ve else run_extent(img, (ve[0] + ve[1]) / 2, 0, TL_W, CYAN[1])
        mid = None if not he else (he[0] + he[1] + 1) / 2
        want_bottom = sa["y1"] - margin - bw - below
        info["row4_measured"] = {"cyan_x": he, "cyan_y": ve, "want_bottom": want_bottom}
        check("4 行目: 水色の帯が画面の横の中央 (左右の端の中央が 960 から 2 px 以内)",
              mid is not None and abs(mid - TL_W / 2) <= 2, he, TL_W / 2)
        check("4 行目: 水色の帯の下の端がセーフエリアの下から margin・縁・影の分だけ上 (2 px 以内)",
              ve is not None and abs((ve[1] + 1) - want_bottom) <= 2, ve, want_bottom)
        b = CYAN[0]
        m4 = d4["w"] / b[2]
        check("4 行目: 倍率がタイムラインに映る大きさの max_scale 倍か max_width の小さいほう",
              abs(m4 - min(float(co["auto_dst"]["max_scale"]) * TL_W / SRC_W, float(co["auto_dst"]["max_width"]) / b[2]))
              < 0.01, round(m4, 4))


# ---------- 組む・照らし合わせる・片付ける ----------
def build_and_check(sb):
    path = os.path.join(OUT, f"{NAME}.storyboard.json")
    C.write_json(path, sb)
    log = {}
    t0 = time.time()
    ok, summary, lines = run_storyboard(path, f"test-{NAME}-{int(time.time())}", created, log)
    info["run"] = {"ok": ok, "summary": summary, "lines": lines, "seconds": round(time.time() - t0, 1),
                   "warnings": log.get("warnings"), "auto": log.get("auto"), "timeline": log.get("timeline"),
                   "fusion_seconds": (log.get("fusion") or {}).get("seconds")}
    check("sb2resolve で組んで書き出せた", ok, summary, lines)
    if not ok:
        return
    loaded = C.config().load_storyboard(path)
    loaded["_path"] = path
    tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    # 部品の名前: 3 行目は order の順に Callout (いちばん上)・Callout2・Callout3
    comp = tl.GetItemListInTrack("video", 1)[2].GetFusionCompByIndex(1)
    names = {n: bool(comp.FindTool(n)) for n in ("Callout", "Callout2", "Callout3", "CalloutCrop3")}
    check("3 行目: 切り抜きの部品が 3 つ (Callout・Callout2・Callout3)", all(names.values()), names)
    video = log["render"]["path"]
    rep = CF.run(loaded, video, tl, pixel=True)
    bad = {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"] if c["status"] != "pass"}
    info["check_final"] = {"summary": rep["summary"], "not_pass": bad,
                           "skipped": rep.get("border_pixels_skipped")}
    check("check_final.run (--pixel) の fail と unknown が 0", not bad, bad, {})
    # コマンドとして呼ぶ (今のプロジェクトの今のタイムラインを読む)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "check_final.py"), path, "--video", video, "--pixel",
                        "--name", f"{NAME}-cli"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = (r.stdout or "").strip().splitlines()[-1:] or [r.stderr[-300:]]
    info["check_final_cli"] = {"code": r.returncode, "line": line[0]}
    m = re.search(r"failed=(\d+) unknown=(\d+)", line[0])
    check("check_final.py --pixel が failed=0 unknown=0", r.returncode == 0 and m and m.groups() == ("0", "0"), line[0])
    pixel_checks(video, CF.expected_rows(loaded, CF.source_sizes(loaded, tl)), log.get("auto") or [], loaded)


if __name__ == "__main__":
    require_lock()
    resolve = C.connect_resolve(quiet=True)
    path = os.path.join(OUT, f"{NAME}.json")
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    cur = resolve.GetProjectManager().GetCurrentProject()
    prev = cur.GetName() if cur else None
    info["previous_project"] = prev
    created = []
    try:
        src = make_source()
        info["source"] = C.probe_video(src)
        build_and_check(storyboard(src))
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        code = finish_projects(resolve, NAME, path, created, prev, info, checks)
    sys.exit(code)
