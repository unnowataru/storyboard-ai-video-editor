"""sb2resolve.py が、4K の素材と 1080p のタイムラインで、構成表どおりに組んで書き出せるか。

機密の無い 4K のテストパターン (3840x2160、30 fps。灰と白の市松模様に、既知の位置の赤い四角) を作業フォルダーに作り、
2 つの構成表を組んで書き出し、check_final.py (--pixel) と、書き出したフレームの色で確認する。
- A: 手で書いた 5 行 (普通の速さ、4 倍速、ズーム、1.5 倍速で keys で動く 3 点のマスク、1.5 秒の短い行)。
  速さを変えた行の肩テロップと動くマスクが、書き出しの秒どおりに出ること (Fusion の時刻の換算) も見る。
  周りを暗くする (focus): 1 行目は 0.5 秒で外側を 0.5 倍に (内側・テロップ・肩テロップは変わらない)、3 行目はズームと一緒に動く
  肩テロップの板が文の長さに合わせて広がり、文 (「▶▶ 4分10秒後」「▶▶ 30倍速」と長い文) が板からはみ出さないこと、
  6 行目の結びのテロップ (caption_style closing) がセーフエリアに収まり画面の中央に出ることも見る。マスクは 3 種類
  (モザイク・ぼかし・黒塗り)、1 行に 2 件、keys で動くもの (4 倍速の行にも)、ズームの行に黒塗り
- B: track2masks.py で合成の位置の JSON からマスクを追加した構成表 (黒塗り、動くものは keys)
マスクとズーム範囲は素材の画素で書き、書き出し (1080p) では半分の位置に出ることを見る。
作ったプロジェクト (test-sb2resolve_4k-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/sb2resolve_4k.py   (Resolve Studio を起動しておく。結果は 出力先/tests/sb2resolve_4k.json)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import check_final as CF  # noqa: E402
import common as C  # noqa: E402
import track2masks as T2M  # noqa: E402
from resolve_helpers import finish_projects, make_check, require_lock, run_storyboard  # noqa: E402

cfg = C.config()
OUT = cfg.out_dir("tests")
NAME = "sb2resolve_4k"
SRC_W, SRC_H, FPS = 3840, 2160, 30
RED = (2600, 380, 360, 280)          # 素材の画素の赤い四角
checks, info = [], {}
check = make_check(checks)


# ---------- 素材 ----------
def make_source():
    """市松模様 (16 px、輝度 128 と 235) に赤い四角を置いた 4K の 45 秒の動画。あれば作り直さない。"""
    path = os.path.join(cfg.work_dir("test-media"), "testsrc-4k-checker-bt709.mp4")   # 色は BT.709
    if os.path.isfile(path):
        return path
    import numpy as np
    from PIL import Image
    y, x = np.mgrid[0:SRC_H, 0:SRC_W]
    g = np.where(((x // 16) + (y // 16)) % 2 == 0, 128, 235).astype(np.uint8)
    img = np.stack([g, g, g], axis=2)
    rx, ry, rw, rh = RED
    img[ry:ry + rh, rx:rx + rw] = (220, 20, 20)
    png = path[:-4] + ".png"
    Image.fromarray(img).save(png)
    C.run_tool("ffmpeg", ["-loop", "1", "-framerate", str(FPS), "-i", png, "-t", "45", "-vf", C.BT709_VF,
                          *C.BT709_TAGS, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", path])
    os.remove(png)
    return path


def storyboard_a(src):
    def box(x, y, w, h, **kw):
        return {"x": x, "y": y, "w": w, "h": h, **kw}
    return {"source": src, "output": os.path.join(OUT, "sb2resolve_4k-a.mp4"), "fps": FPS, "seconds_per_row": 4,
            "rows": [
                {"start": 0, "end": 4, "caption": "1 行目: マスク 2 件", "badge": "4分10秒後",
                 "focus": {"x": 1600, "y": 600, "w": 1200, "h": 800, "dim": 0.5, "feather": 40, "seconds": 0.5},
                 "masks": [box(200, 200, 400, 200, type="black"), box(800, 200, 600, 400, type="mosaic", shape="ellipse")]},
                {"start": 4, "end": 20, "badge": "約 4 分 10 秒の待ち時間をスキップ",
                 "masks": [box(200, 1200, 800, 400, type="blur"),
                           box(1600, 1200, 400, 200, type="black",
                               keys=[box(1600, 1200, 400, 200, t=0), box(2800, 1600, 400, 200, t=3)])]},
                {"start": 20, "end": 26, "seconds": 6, "zoom": {"x": 2240, "y": 160, "w": 1080, "h": 720, "seconds": 2},
                 "focus": {"x": 2600, "y": 380, "w": 360, "h": 280, "dim": 0.3, "feather": 0, "seconds": 0},
                 "masks": [box(2700, 450, 160, 140, type="black")]},
                {"start": 30, "end": 36, "seconds": 4, "caption": "4 行目: 1.5 倍速で 3 点で動くマスク", "badge": "30倍速",
                 "masks": [box(400, 400, 300, 300, type="black",
                               keys=[box(400, 400, 300, 300, t=0), box(1400, 400, 300, 300, t=1),
                                     box(1400, 1400, 600, 300, t=2)])]},
                {"start": 40, "end": 41.5, "seconds": 1.5, "badge": "短い行"},
                {"start": 42, "end": 45, "seconds": 3, "caption": "結びのテロップの 1 行目|2 行目はここ",
                 "caption_style": "closing"}]}


def track_json(src):
    """合成の位置の JSON。素材のフレーム 0〜90 は (1000, 1000)、90〜300 は (1400, 1100) に 300x100 の四角。"""
    def seg(a, b, x, y):
        return {"start": a, "end": b, "start_sec": a / FPS, "end_sec": b / FPS,
                "boxes": [{"label": "ip-1", "x": x, "y": y, "w": 300, "h": 100}]}
    return {"version": 1, "video": src, "width": SRC_W, "height": SRC_H, "fps": float(FPS), "frames": 1350, "pad": 6,
            "segments": [seg(0, 90, 1000, 1000), seg(90, 300, 1400, 1100)]}


# ---------- 書き出したフレームの色 ----------
def region(img, x0, y0, x1, y1):
    return img[int(y0):int(y1), int(x0):int(x1)].astype(float)


def is_black(img, cx, cy, r=6):
    return float(region(img, cx - r, cy - r, cx + r, cy + r).mean()) < 40


def is_red(img, cx, cy, r=6):
    p = region(img, cx - r, cy - r, cx + r, cy + r).mean(axis=(0, 1))
    return p[0] > 150 and p[1] < 80 and p[2] < 80


def flat(img, x0, y0, x1, y1):
    """市松模様が消えて (ぼかし・モザイク) 平らか。市松のままなら標準偏差は 50 前後。"""
    return float(region(img, x0, y0, x1, y1).std()) < 20


def mean(img, x0, y0, x1, y1):
    return float(region(img, x0, y0, x1, y1).mean())


def white_box(img, x0, y0, x1, y1):
    """範囲の中の白い画素 (文の字。市松の明るい側は 235 なので 248 で分ける) の外接の四角 (x0, y0, x1, y1)。無ければ None。"""
    import numpy as np
    part = img[int(y0):int(y1), int(x0):int(x1)]
    ys, xs = np.nonzero(part.min(axis=2) > 248)
    if not len(xs):
        return None
    return (int(xs.min() + x0), int(ys.min() + y0), int(xs.max() + x0), int(ys.max() + y0))


def tl_box(b):
    return [v / 2 for v in b]   # 4K → 1080p


def center(b):
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def black_box_edges(img, b, label, f):
    """黒塗りの四角 b (タイムラインの画素) の中が黒く、外が黒くない (左右上下の 4 px 内側と 6 px 外側)。"""
    x, y, w, h = b
    cy, cx = y + h / 2, x + w / 2
    inside = all(is_black(img, px, py, 2) for px, py in ((x + 4, cy), (x + w - 4, cy), (cx, y + 4), (cx, y + h - 4)))
    outside = not any(is_black(img, px, py, 2) for px, py in ((x - 6, cy), (x + w + 6, cy), (cx, y - 6), (cx, y + h + 6)))
    check(f"{label} フレーム {f}: 黒塗りが {[round(v, 1) for v in b]} に出る (内側は黒、外側は黒くない)", inside and outside,
          {"inside": inside, "outside_clear": outside})


def pixel_checks_a(video, exp):
    starts = {r["row"]: r["start"] for r in exp}

    def frame(row, f):
        return CF.read_frame(video, starts[row] + f, 1920, 1080)
    # 1 行目: 黒塗り (200,200,400,200) → (100,100,200,100)、楕円のモザイク (800,200,600,400) → (400,100,300,200)
    img = frame(1, 10)
    black_box_edges(img, tl_box((200, 200, 400, 200)), "1 行目", 10)
    check("1 行目: 楕円のモザイクの中が平ら、楕円の外 (右に 40 px) は市松", flat(img, 510, 160, 590, 240)
          and not flat(img, 740, 160, 780, 240))
    # 1 行目の周りを暗くする: 範囲 (1600,600,1200,800) → (800,300,600,400)。0.5 秒 (15 フレーム) で外側が 0.5 倍
    f0, f30 = frame(1, 0), frame(1, 30)
    inside = (mean(f0, 1080, 480, 1120, 520), mean(f30, 1080, 480, 1120, 520))
    outside = (mean(f0, 1500, 480, 1540, 520), mean(f30, 1500, 480, 1540, 520))
    check("1 行目: 範囲の内側の明るさは変わらない", abs(inside[1] - inside[0]) < 4, inside)
    check("1 行目: 範囲の外側は 0.5 倍", abs(outside[1] - outside[0] * 0.5) < 5, outside)
    check("1 行目: 外側は 0.5 秒かけて暗くなる (7 フレーム目は途中)",
          outside[1] + 5 < mean(frame(1, 7), 1500, 480, 1540, 520) < outside[0] - 5, mean(frame(1, 7), 1500, 480, 1540, 520))
    look = cfg.load_storyboard(os.path.join(OUT, f"{NAME}-a.storyboard.json"))
    fit = C.fit_caption(look, "x")
    by = (1 - fit["bar_center"]) * 1080
    # テロップの背景と肩テロップの板は半透明なので、下の画 (暗くした外側) が (1 − 不透明度) だけ透ける。
    # 板そのものが暗くなっていなければ、暗くしない行の色から 透ける分 × 下の画の落ち幅 だけ低い (板ごと暗くなれば半分になる)
    drop = outside[0] - outside[1]
    bars = (mean(f30, 20, by - 4, 60, by + 4), mean(frame(4, 30), 20, by - 4, 60, by + 4))
    want = bars[1] - (1 - look["caption"]["bar_rgba"][3] / 255) * drop
    check("1 行目: テロップの背景は暗くならない (暗くしない 4 行目の色から、透ける下の画の分だけ低い)",
          abs(bars[0] - want) < 4, bars, round(want, 1))
    # 板の色は、字の無い所 (板の中央の下の帯) で比べる。板の幅は文で違うので、それぞれの板で測る
    def plate_color(row):
        b = C.badge_box(look, look["badge"]["prefix"] + storyboard_a("")["rows"][row - 1]["badge"], C.badge_font(look))
        cx, y1 = b["center"][0] * 1920, (1 - b["center"][1] + b["height"] / 2) * 1080
        return mean(frame(row, 10), cx - 20, y1 - 10, cx + 20, y1 - 5)
    badges = (plate_color(1), plate_color(2))
    want = badges[1] - (1 - look["badge"]["rgba"][3] / 255) * drop
    check("1 行目: 肩テロップの板は暗くならない (暗くしない 2 行目の色から、透ける下の画の分だけ低い)",
          abs(badges[0] - want) < 4, badges, round(want, 1))
    # 肩テロップの文が板に収まる (板の外 60 px までに白い字が無く、板の中に字がある)
    for row in (1, 2, 4, 5):
        text = look["badge"]["prefix"] + storyboard_a("")["rows"][row - 1]["badge"]
        b = C.badge_box(look, text, C.badge_font(look))
        x0, x1 = (b["center"][0] - b["width"] / 2) * 1920, (b["center"][0] + b["width"] / 2) * 1920
        y0, y1 = (1 - b["center"][1] - b["height"] / 2) * 1080, (1 - b["center"][1] + b["height"] / 2) * 1080
        wb = white_box(frame(row, 10), max(x0 - 60, 0), max(y0 - 30, 0), min(x1 + 60, 1920), y1 + 30)
        ok = wb is not None and wb[0] >= x0 + 2 and wb[2] <= x1 - 2 and wb[1] >= y0 + 2 and wb[3] <= y1 - 2
        info[f"badge_row{row}"] = {"plate": [round(v) for v in (x0, y0, x1, y1)], "text": wb,
                                   "measured_width": round(b["width"] * 1920)}
        check(f"{row} 行目: 肩テロップの文が板の中に収まる (板 {round(x1 - x0)}x{round(y1 - y0)} px)", ok,
              wb, [round(v) for v in (x0, y0, x1, y1)])
    # 6 行目: 結びのテロップは画面の中央に、セーフエリア (safe_area) の内側に収まる
    sa = C.safe_area(look)
    wb = white_box(frame(6, 30), 0, 0, 1920, 1080)
    info["closing_text"] = wb
    ok = (wb is not None and wb[0] >= sa["x0"] and wb[2] <= sa["x1"]
          and abs((wb[1] + wb[3]) / 2 - 540) < 40 and wb[3] - wb[1] > 150)
    check("6 行目: 結びのテロップ (2 行) が画面の中央でセーフエリアに収まる", ok, wb, [sa["x0"], sa["x1"]])
    # 2 行目 (4 倍速、120 フレーム): ぼかし (200,1200,800,400) → (100,600,400,200)。動く黒塗りは書き出しの秒で 0〜3 秒
    img = frame(2, 30)
    check("2 行目: ぼかしの中が平ら、外 (右に 30 px) は市松", flat(img, 130, 630, 470, 770) and not flat(img, 530, 630, 600, 770))
    for f in (0, 45, 110):
        t = min(f / FPS, 3.0)
        b = tl_box((1600 + 1200 * t / 3, 1200 + 400 * t / 3, 400, 200))
        black_box_edges(frame(2, f), b, "2 行目 (4 倍速) の動く黒塗り", f)
    check("2 行目: 110 フレーム目には最初の位置に黒塗りが残らない", not is_black(frame(2, 110), *center(tl_box((1600, 1200, 400, 200)))))
    # 3 行目: ズーム (2240,160,1080,720) → 3 倍。最初は赤い四角が (1300,190) 付近、寄り終わり (60) から画面の中央
    img = frame(3, 0)
    check("3 行目 0 フレーム: 赤い四角が素材の半分の位置 (1390, 260)", is_red(img, 1390 - 60, 260) and not is_red(img, 960, 540))
    black_box_edges(img, tl_box((2700, 450, 160, 140)), "3 行目 (ズーム前)", 0)
    for f in (60, 120):
        img = frame(3, f)
        # 赤い四角 (中心 2780,520) は 3 倍で 540x420、中央に。黒塗り (160x140) は 240x210 で中央に
        ok = (is_black(img, 960, 540) and is_red(img, 960 + 200, 540) and is_red(img, 960, 540 + 160)
              and not is_red(img, 960 + 300, 540) and not is_black(img, 960 + 140, 540))
        check(f"3 行目 {f} フレーム: ズームで赤い四角が中央に 3 倍、黒塗りも一緒に 3 倍", ok)
        # 周りを暗くする範囲 (赤い四角) もズームと一緒に動く: 赤い四角の外 (右に 30〜70 px) は 0.3 倍の市松
        out3 = mean(img, 1260, 520, 1300, 560)
        check(f"3 行目 {f} フレーム: 暗くする範囲がズームと一緒に動く (赤は残り、外は 0.3 倍)",
              is_red(img, 960 + 200, 540) and abs(out3 - 181.5 * 0.3) < 12, out3)
    # 4 行目 (1.5 倍速): 3 点の keys (書き出しの 0 秒、1 秒、2 秒)。書き出しのフレーム f は合成の時刻 floor(1.5 f) の画
    # なので、位置はその時刻 (書き出しの秒に直すと floor(1.5 f) / 1.5 / 30) で計算する。3 秒は最後の位置
    keys4 = [(0, (400, 400, 300, 300)), (1, (1400, 400, 300, 300)), (2, (1400, 1400, 600, 300))]
    for f in (0, 5, 15, 45, 90):
        t = C.comp_frame(f, 1.5) / 1.5 / FPS
        b = CF.expected_box({"keys": [(a, list(x)) for a, x in keys4], "box": None}, t)
        black_box_edges(frame(4, f), tl_box(b), "4 行目 (1.5 倍速) の 3 点で動く黒塗り", f)


def pixel_checks_b(video, sb_b, exp):
    """track2masks の構成表: 1 行目 (素材 0〜3 秒、等速) と 2 行目 (素材 3〜11 秒、4 倍速で 2 秒)。"""
    starts = {r["row"]: r["start"] for r in exp}
    pad = cfg.section("track")["pad"]

    def padded(x, y):
        return tl_box((x - pad, y - pad, 300 + 2 * pad, 100 + 2 * pad))
    m = sb_b["rows"][0]["masks"]
    info["track2masks_row1"] = m
    # 素材のフレーム 90 (3 秒) で動くので、1 行目は最後まで最初の位置
    black_box_edges(CF.read_frame(video, starts[1] + 45, 1920, 1080), padded(1000, 1000), "B 1 行目", 45)
    # 2 行目は素材の 3 秒目から。最初から 2 つ目の位置
    black_box_edges(CF.read_frame(video, starts[2] + 10, 1920, 1080), padded(1400, 1100), "B 2 行目", 10)


# ---------- 組む・照らし合わせる・片付ける ----------
def build_and_check(sb, label, pixel_fn):
    path = os.path.join(OUT, f"{NAME}-{label}.storyboard.json")
    C.write_json(path, sb)
    log = {}
    t0 = time.time()
    ok, summary, lines = run_storyboard(path, f"test-{NAME}-{label}-{int(time.time())}", created, log)
    info[f"{label}_run"] = {"ok": ok, "summary": summary, "lines": lines, "seconds": round(time.time() - t0, 1),
                            "settings": log.get("settings"), "timeline": log.get("timeline"),
                            "fusion_seconds": (log.get("fusion") or {}).get("seconds")}
    check(f"{label}: sb2resolve で組んで書き出せた", ok, summary, lines)
    if not ok:
        return
    loaded = C.config().load_storyboard(path)
    loaded["_path"] = path
    tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    if label == "a":   # 部品の名前 (人が Fusion ページで探して直す): 1 行目のテロップと 2 件のマスク
        comp = tl.GetItemListInTrack("video", 1)[0].GetFusionCompByIndex(1)
        names = {n: bool(comp.FindTool(n)) for n in ("CaptionText", "BadgeText", "BadgeOver", "Mask1", "Mask2Mosaic")}
        check("a: 部品に名前が付く (CaptionText・BadgeText・Mask1 など)", all(names.values()), names)
    video = log["render"]["path"]
    rep = CF.run(loaded, video, tl, pixel=True)
    bad = {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"] if c["status"] != "pass"}
    info[f"{label}_check_final"] = {"summary": rep["summary"], "not_pass": bad}
    check(f"{label}: check_final.py (--pixel) の fail と unknown が 0", not bad, bad, {})
    pixel_fn(video, CF.expected_rows(loaded, CF.source_sizes(loaded, tl)))


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
        build_and_check(storyboard_a(src), "a", pixel_checks_a)
        # B: track2masks でマスクを追加した構成表
        base = {"source": src, "output": os.path.join(OUT, "sb2resolve_4k-b.mp4"), "fps": FPS, "seconds_per_row": 3,
                "rows": [{"start": 0, "end": 3}, {"start": 3, "end": 11, "seconds": 2}]}
        bp, tp = os.path.join(OUT, f"{NAME}-b-base.json"), os.path.join(OUT, f"{NAME}.track.json")
        C.write_json(bp, base)
        C.write_json(tp, track_json(src))
        doc, r = T2M.convert(bp, tp, kind="black")
        info["track2masks"] = r
        build_and_check(doc, "b", lambda v, e: pixel_checks_b(v, doc, e))
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        code = finish_projects(resolve, NAME, path, created, prev, info, checks)
    sys.exit(code)
