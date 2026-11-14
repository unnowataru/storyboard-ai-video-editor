"""check_final.py の止めたクリップの検証 (check_hold は幅と高さを引数で受け取る) が、実機の止めたクリップを正しく照らすか。

check_timeline は毎回タイムラインの幅と高さを渡してから check_hold を呼ぶので、1080p と 4K を続けて照らす流れだけでは
渡した幅と高さが使われているかを見分けられない。そこで、実機のクリップと期待の行を使って check_hold を直接呼び、それも見る。
機密の無い 4K の合成の素材 (FFmpeg の testsrc2、3840x2160、30 fps、10 秒) を作業フォルダーに作る。構成表は 2 行。
1 行目にマスク (黒塗り) と focus を置き、行の後ろで 2 秒止める (hold)。マスクと focus の範囲は素材の画素で書く。
最後の行 (2 行目) も後ろで 1 秒止める。止める前に再生ヘッドを置く位置 (クリップの直後) がタイムラインの終わりの直後になる場合で、
sb2resolve の記録の hold が合う (match) ことを見る。
同じ構成表を、タイムラインの大きさだけ変えて (1920x1080 と 3840x2160) sb2resolve.py で 2 つのプロジェクトに組んで書き出す。
照らす順と期待:
- 1080p を照らす → fail 0・unknown 0 (hold_mask1@1・hold_focus@1 が pass)
- 4K を照らす → 同じく fail 0・unknown 0
- 4K の止めたクリップのマスク (Mask1) の Center を横に 0.05 ずらして照らす → hold_mask1@1 が fail、mask1@1 と hold_focus@1 は pass。
  戻して照らす → fail 0・unknown 0
- 1080p のプロジェクトを開き直して照らす (4K の後) → fail 0・unknown 0。同じようにずらす → hold_mask1@1 が fail。
  戻して照らす → fail 0・unknown 0
- check_hold を直接呼ぶ (4K と、開き直した 1080p のタイムライン): そのタイムラインの幅と高さを渡すと hold_mask1・hold_focus が pass、
  もう一方の大きさを渡すと両方 fail
照らすのは check_final.run (--pixel と同じ。書き出した動画も見る)。
作ったプロジェクト (test-hold_check_final-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/hold_check_final.py
(Resolve Studio を起動しておく。結果は 出力先/tests/hold_check_final.json)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts")
sys.path.insert(0, SCRIPTS)
import check_final as CF  # noqa: E402
import common as C  # noqa: E402
from resolve_helpers import finish_projects, make_check, make_source, require_lock, run_storyboard  # noqa: E402

cfg = C.config()
OUT = cfg.out_dir("tests")
NAME = "hold_check_final"
SRC_W, SRC_H, FPS, SECONDS = 3840, 2160, 30, 10
SIZES = {"1080p": (1920, 1080), "4k": (3840, 2160)}
SHIFT = 0.05            # マスクの Center を横にずらす量 (画面の幅を 1 とした値)
WATCH = ("hold_mask1@1", "hold_focus@1", "mask1@1")
checks, info = [], {}
check = make_check(checks)


def storyboard(src, label):
    W, H = SIZES[label]
    return {"source": src, "output": os.path.join(OUT, f"{NAME}-{label}.mp4"), "fps": FPS, "seconds_per_row": 3,
            "timeline": {"width": W, "height": H},
            "rows": [
                {"start": 0, "end": 3, "caption": "止める行 (マスクと focus)", "hold": {"seconds": 2},
                 "masks": [{"x": 400, "y": 300, "w": 600, "h": 300, "type": "black"}],
                 "focus": {"x": 1600, "y": 800, "w": 1200, "h": 700, "dim": 0.5, "feather": 0, "seconds": 0.3}},
                {"start": 3, "end": 5, "seconds": 2, "caption": "等速の行 (最後の行の hold)", "hold": {"seconds": 1}}]}


def statuses(rep):
    return {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"]}


def look(case, sb, video, tl):
    """check_final.run (--pixel) で照らす。返す値は 項目@行 → pass・fail・unknown。"""
    rep = CF.run(sb, video, tl, pixel=True)
    st = statuses(rep)
    bad = {k: v for k, v in st.items() if v != "pass"}
    info["cases"][case] = {"summary": rep["summary"], "not_pass": bad, "watch": {k: st.get(k) for k in WATCH},
                           "timeline": tl.GetName(), "size": [tl.GetSetting("timelineResolutionWidth"),
                                                              tl.GetSetting("timelineResolutionHeight")]}
    return st, bad


def hold_mask(tl):
    """止めたクリップ (1 行目の後ろ。トラック 1 の 2 番目) の Fusion の Mask1。"""
    items = sorted(tl.GetItemListInTrack("video", 1) or [], key=lambda x: x.GetStart())
    comp = items[1].GetFusionCompByIndex(1) if len(items) > 1 and items[1].GetFusionCompCount() else None
    return comp.FindTool("Mask1") if comp else None


def pass_case(case, sb, video, tl):
    st, bad = look(case, sb, video, tl)
    check(f"{case}: fail と unknown が 0 (hold_mask1@1・hold_focus@1 が pass)",
          not bad and st.get("hold_mask1@1") == "pass" and st.get("hold_focus@1") == "pass",
          {"not_pass": bad, "watch": {k: st.get(k) for k in WATCH}}, {})


def shift_case(case, sb, video, tl):
    """止めたクリップのマスクを横にずらして照らし、戻す。"""
    mk = hold_mask(tl)
    if not check(f"{case}: 止めたクリップの Mask1 がある", mk is not None):
        return
    x, y = CF._xy(mk.GetInput("Center", 0))
    if not check(f"{case}: Mask1 の Center を読める", x is not None, [x, y]):
        return
    mk.SetInput("Center", {1: x + SHIFT, 2: y})
    try:
        st, bad = look(case, sb, video, tl)
        check(f"{case}: hold_mask1@1 が fail、mask1@1 と hold_focus@1 は pass",
              (st.get("hold_mask1@1"), st.get("mask1@1"), st.get("hold_focus@1")) == ("fail", "pass", "pass"),
              {k: st.get(k) for k in WATCH}, {"hold_mask1@1": "fail", "mask1@1": "pass", "hold_focus@1": "pass"})
    finally:
        mk.SetInput("Center", {1: x, 2: y})


def direct_case(case, sb, tl, own, other):
    """実機のクリップと期待の行で check_hold を直接呼ぶ。own (そのタイムラインの幅と高さ) なら pass、other なら fail。"""
    row = CF.expected_rows(sb, CF.source_sizes(sb, tl))[0]
    items = sorted(tl.GetItemListInTrack("video", 1) or [], key=lambda x: x.GetStart())
    if not check(f"{case}: 止めたクリップがある", len(items) > 1, len(items), 3):
        return
    got = {}
    for wh in (own, other):
        out = []
        CF.check_hold(out, sb, row, items[1], tl.GetStartFrame(), *wh)
        got["x".join(map(str, wh))] = {c["item"]: c["status"] for c in out if c["item"] in ("hold_mask1", "hold_focus")}
    info["cases"][case] = got
    want = {"x".join(map(str, own)): {"hold_mask1": "pass", "hold_focus": "pass"},
            "x".join(map(str, other)): {"hold_mask1": "fail", "hold_focus": "fail"}}
    check(f"{case}: check_hold に渡した幅と高さで照らす ({own[0]}x{own[1]} で pass、{other[0]}x{other[1]} で fail)",
          got == want, got, want)


def build(label, src):
    path = os.path.join(OUT, f"{NAME}-{label}.storyboard.json")
    C.write_json(path, storyboard(src, label))
    log = {}
    t0 = time.time()
    ok, summary, lines = run_storyboard(path, f"test-{NAME}-{label}-{int(time.time())}", created, log)
    info[f"{label}_run"] = {"ok": ok, "summary": summary, "lines": lines, "seconds": round(time.time() - t0, 1),
                            "settings": log.get("settings"), "timeline": log.get("timeline")}
    check(f"{label}: sb2resolve で組んで書き出せた", ok, summary, lines)
    last = ((log.get("timeline") or {}).get("rows") or [{}])[-1].get("hold") or {}
    check(f"{label}: 最後の行の止めたクリップが期待のフレームを映す (再生ヘッドはタイムラインの終わりの直後)",
          last.get("match") is True, {k: last.get(k) for k in ("result", "playhead", "matched_at", "tries", "error")},
          {"match": True})
    if not ok:
        return None
    sb = C.config().load_storyboard(path)
    sb["_path"] = path
    return {"sb": sb, "video": log["render"]["path"], "project": log.get("project")}


def current_timeline():
    return resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()


if __name__ == "__main__":
    require_lock()
    resolve = C.connect_resolve(quiet=True)
    path = os.path.join(OUT, f"{NAME}.json")
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    cur = resolve.GetProjectManager().GetCurrentProject()
    prev = cur.GetName() if cur else None
    info["previous_project"] = prev
    info["cases"] = {}
    info["why"] = ("check_timeline は毎回幅と高さを渡してから check_hold を呼ぶので、続けて照らす流れだけでは"
                   "渡した幅と高さが使われるかを見分けられない。それは check_hold を直接呼ぶ項目で見る")
    created = []
    try:
        src = make_source(cfg, SRC_W, SRC_H, FPS, seconds=SECONDS)
        info["source"] = C.probe_video(src)
        hd = build("1080p", src)
        if hd:
            pass_case("1080p", hd["sb"], hd["video"], current_timeline())
        uhd = build("4k", src)
        if uhd:
            tl = current_timeline()
            pass_case("4k", uhd["sb"], uhd["video"], tl)
            shift_case("4k ずらす", uhd["sb"], uhd["video"], tl)
            pass_case("4k 戻す", uhd["sb"], uhd["video"], tl)
            direct_case("4k の check_hold を直接", uhd["sb"], tl, SIZES["4k"], SIZES["1080p"])
        if hd:
            # 4K を照らした後に、1080p のプロジェクトを開き直して照らす (同じプロセスの中)
            resolve.GetProjectManager().SaveProject()
            proj = resolve.GetProjectManager().LoadProject(hd["project"])
            if check("1080p のプロジェクトを開き直せた", bool(proj), hd["project"]):
                tl = proj.GetCurrentTimeline()
                pass_case("1080p (4K の後)", hd["sb"], hd["video"], tl)
                shift_case("1080p ずらす (4K の後)", hd["sb"], hd["video"], tl)
                pass_case("1080p 戻す (4K の後)", hd["sb"], hd["video"], tl)
                direct_case("1080p の check_hold を直接 (4K の後)", hd["sb"], tl, SIZES["1080p"], SIZES["4k"])
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=21)
    sys.exit(code)
