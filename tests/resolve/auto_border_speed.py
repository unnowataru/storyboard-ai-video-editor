"""倍速の行で keys で動く focus の枠線の色 auto が、描く枠線と同じ位置 (合成の時刻で補間した位置) の背景で決まるか。

機密の無い 4K の合成の画 (3840x2160、30 fps、8 秒) を作業フォルダーに作る。左 (x が 1450 より左) は黒、右は白。
構成表は 1 行。素材の 0〜6 秒を 4 秒に詰めた 1.5 倍速の行に、keys で動く focus (border.color が auto) を置く。
- keys は 0 秒 (x 0) と 2/30 秒 (x 3000)。幅と高さは 400。枠線は 1/30 秒 (書き出しの 1 フレーム目) に現れ始める
- 描く側 (scene_values) は keys を合成の時刻 (0 と floor(1.5 × 2) = 3) に直し、書き出しの 1 フレーム目の合成の時刻
  floor(1.5 × 1) = 1 で補間する。範囲の x は 3000 × 1/3 = 1000。辺のまわりの帯 (内側 24・外側 24 画素) は x 976〜1424 で、
  全部が黒い所。明るさ 0 で、色は focus_border.auto.dark
- 誤った測り方 (keys を書き出しのフレーム 0 と 2 のまま、1 フレーム目で補間) では x は 3000 × 1/2 = 1500。帯は x 1476〜1924 で、
  全部が白い所。色は auto.light になり、このテストの色の項目は落ちる
sb2resolve.py で組んで書き出し、記録の auto (決めた色と測った範囲)、check_final.py (--pixel) と、書き出したフレームの
枠線の 4 辺の色で確認する。位置の補間は common の関数を使わずに計算する (色の設定は config から読む)。
作ったプロジェクト (test-auto_border_speed-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/auto_border_speed.py
(Resolve Studio を起動しておく。結果は 出力先/tests/auto_border_speed.json)
"""
import math
import os
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
NAME = "auto_border_speed"
SRC_W, SRC_H, FPS, SECONDS = 3840, 2160, 30, 8
TL_W, TL_H = 1920, 1080
EDGE = 1450                 # 素材の画素で、ここより左が黒、ここから右が白
SPEED = 1.5                 # 素材の 0〜6 秒を 4 秒に詰める
KEY_FRAMES = (0, 2)         # keys の時刻 (書き出しのフレーム)
KEY_X = (0, 3000)           # keys の x (素材の画素)。y・幅・高さは動かさない
BOX_Y, BOX_W, BOX_H = 800, 400, 400
APPEAR = 1                  # 枠線が現れ始める書き出しのフレーム
checks, info = [], {}
check = make_check(checks)


# ---------- 期待の値 (このテストの中で計算する) ----------
def expected():
    """描く枠線の位置 (合成の時刻で補間) と、誤った測り方の位置 (書き出しのフレームで補間)、それぞれの帯が黒か白か。"""
    a = cfg.storyboard_values()[0]["focus_border"]["auto"]
    inside, outside = float(a["inside"]), float(a["outside"])

    def x_at(f, k0, k1):
        return KEY_X[0] + (KEY_X[1] - KEY_X[0]) * (f - k0) / (k1 - k0)
    comp = [math.floor(k * SPEED) for k in KEY_FRAMES]          # keys の合成の時刻 (0 と 3)
    now = x_at(math.floor(APPEAR * SPEED), *comp)                # 1 フレーム目の合成の時刻 1 → x 1000
    old = x_at(APPEAR, *KEY_FRAMES)                              # 誤った測り方: x 1500

    def side(x):
        lo, hi = x - outside, x + BOX_W + outside                # 帯の左の端と右の端 (素材の画素)
        return "dark" if hi <= EDGE else "light" if lo >= EDGE else "mixed"
    return {"comp_keys": comp, "appear_comp": math.floor(APPEAR * SPEED), "inside": inside, "outside": outside,
            "box_x": now, "side": side(now), "color": a[side(now)] if side(now) in a else None,
            "before_fix": {"box_x": old, "side": side(old), "color": a[side(old)] if side(old) in a else None}}


# ---------- 素材 ----------
def make_source():
    """左が黒、右が白の 4K の 8 秒の動画。あれば作り直さない。"""
    path = os.path.join(cfg.work_dir("test-media"), "testsrc-4k-black-white-1450-bt709.mp4")   # 色は BT.709
    if os.path.isfile(path):
        return path
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", f"color=white:s={SRC_W}x{SRC_H}:r={FPS}:d={SECONDS}",
                          "-vf", f"drawbox=x=0:y=0:w={EDGE}:h={SRC_H}:color=black:t=fill," + C.BT709_VF,
                          *C.BT709_TAGS, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", path])
    return path


def storyboard(src):
    keys = [{"t": f / FPS, "x": x, "y": BOX_Y, "w": BOX_W, "h": BOX_H} for f, x in zip(KEY_FRAMES, KEY_X)]
    return {"source": src, "output": os.path.join(OUT, f"{NAME}.mp4"), "fps": FPS, "seconds_per_row": 4,
            "timeline": {"width": TL_W, "height": TL_H},   # 画素の検査は 1920x1080 (素材の座標の半分) で見る
            "rows": [{"start": 0, "end": 4 * SPEED, "seconds": 4, "caption": "1.5 倍速で動く focus の枠線 (auto)",
                      "focus": {"x": KEY_X[0], "y": BOX_Y, "w": BOX_W, "h": BOX_H, "keys": keys, "dim": 0.5,
                                "feather": 0, "start": APPEAR / FPS, "seconds": 0.3, "border": {"color": "auto"}}}]}


# ---------- 書き出したフレームの色 ----------
def patch(img, x, y, r=1):
    return img[int(y) - r:int(y) + r + 1, int(x) - r:int(x) + r + 1].reshape(-1, 3).mean(axis=0)


def near(px, rgb, tol=50):
    return all(abs(float(a) - b) <= tol for a, b in zip(px, rgb))


def border_colors(img, x, y, w, h):
    """範囲 (素材の画素) の 4 辺の中点 (書き出しの画素) の色。"""
    x, y, w, h = x / 2, y / 2, w / 2, h / 2
    pts = {"左": (x, y + h / 2), "右": (x + w, y + h / 2), "上": (x + w / 2, y), "下": (x + w / 2, y + h)}
    return {k: [round(float(v)) for v in patch(img, px, py)] for k, (px, py) in pts.items()}


def record_checks(rec, exp):
    a = cfg.storyboard_values()[0]["focus_border"]["auto"]
    check("期待の計算: 描く位置の帯は全部が黒い所、直す前の位置の帯は全部が白い所",
          exp["side"] == "dark" and exp["before_fix"]["side"] == "light",
          [exp["side"], exp["before_fix"]["side"]], ["dark", "light"])
    check("記録の auto に誤りが無い", rec and not rec.get("error"), (rec or {}).get("error"))
    if not rec:
        return
    check(f"記録の auto の枠線が現れるフレームが {APPEAR}", rec.get("appear_frame") == APPEAR, rec.get("appear_frame"), APPEAR)
    box = rec.get("box") or {}
    want = {"x": exp["box_x"], "y": BOX_Y, "w": BOX_W, "h": BOX_H}
    check(f"記録の auto の測った範囲が合成の時刻 {exp['appear_comp']} で補間した位置 (x {exp['box_x']:g}。"
          f"直す前なら {exp['before_fix']['box_x']:g})",
          all(abs(float(box.get(k, -1e9)) - v) < 0.01 for k, v in want.items()), box, want)
    check("記録の auto の枠線の色が暗い所の色 (直す前なら明るい所の色)",
          C.hex_rgb(rec.get("color") or "#000000") == C.hex_rgb(a["dark"]), rec.get("color"),
          {"want": a["dark"], "before_fix": a["light"]})
    lum = rec.get("luma")
    check("記録の auto の明るさがしきい値より near 以上低い (黒い所だけを測った)",
          lum is not None and lum < float(a["threshold"]) - float(a["near"]), lum, f"< {a['threshold'] - a['near']}")


def pixel_checks(video, start):
    a = cfg.storyboard_values()[0]["focus_border"]["auto"]
    dark, light = C.hex_rgb(a["dark"]), C.hex_rgb(a["light"])
    # 45 フレーム目は keys の後 (合成の 67) で、範囲は最後の位置 (x 3000、白い所) に止まり、枠線は現れ終わっている
    cols = border_colors(CF.read_frame(video, start + 45, TL_W, TL_H), KEY_X[1], BOX_Y, BOX_W, BOX_H)
    check(f"45 フレーム目: 枠線の 4 辺が暗い所の色 {list(dark)} (明るい所の色 {list(light)} ではない)",
          all(near(p, dark) and not near(p, light) for p in cols.values()), cols, list(dark))


# ---------- 組む・照らし合わせる・片付ける ----------
def build_and_check(sb, exp):
    path = os.path.join(OUT, f"{NAME}.storyboard.json")
    C.write_json(path, sb)
    log = {}
    t0 = time.time()
    ok, summary, lines = run_storyboard(path, f"test-{NAME}-{int(time.time())}", created, log)
    rec = next((d.get("focus_border") for d in log.get("auto") or [] if d.get("row") == 1), None)
    info["run"] = {"ok": ok, "summary": summary, "lines": lines, "seconds": round(time.time() - t0, 1),
                   "warnings": log.get("warnings"), "auto": log.get("auto"), "timeline": log.get("timeline")}
    check("sb2resolve で組んで書き出せた", ok, summary, lines)
    record_checks(rec, exp)
    if not ok:
        return
    loaded = C.config().load_storyboard(path)
    loaded["_path"] = path
    tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    video = log["render"]["path"]
    rep = CF.run(loaded, video, tl, pixel=True)
    bad = {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"] if c["status"] != "pass"}
    info["check_final"] = {"summary": rep["summary"], "not_pass": bad, "skipped": rep.get("border_pixels_skipped")}
    check("check_final.run (--pixel) の fail と unknown が 0", not bad, bad, {})
    pixel_checks(video, CF.expected_rows(loaded, CF.source_sizes(loaded, tl))[0]["start"])


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
        exp = expected()
        info["expected"] = exp
        info["why"] = ("keys が 0 と 2 フレーム目、枠線が 1 フレーム目に現れる 1.5 倍速の行。描く枠線は合成の時刻 (keys 0 と 3、"
                       "1 フレーム目は 1) で補間して x 1000 (帯は黒い所)。直す前は書き出しのフレームで補間して x 1500 (帯は白い所) で、"
                       "記録の範囲と色、書き出しの枠線の色の項目が落ちる")
        src = make_source()
        info["source"] = C.probe_video(src)
        build_and_check(storyboard(src), exp)
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        code = finish_projects(resolve, NAME, path, created, prev, info, checks)
    sys.exit(code)
