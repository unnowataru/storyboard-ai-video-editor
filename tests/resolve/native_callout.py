"""Resolve に付いている Fusion のタイトル「Call Out」を、V2 の独立したタイトルのクリップとして API で置き、長さ・指す先・文の位置・
文言・色を決めて書き出しで合うか、保存して開き直した後も残るか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/native_callout.py
(Resolve Studio を起動しておく。結果は 出力先/tests/native_callout.json。書き出した動画と確認用のフレームの PNG は
作業フォルダー/native_callout に残る)

組むもの: プロジェクト test-native-callout (1920x1080、30fps) を作り直し (あれば消してから作る)、消さずに残す
(人が GUI で確認しないなら、終わった後に scripts/cleanup_projects.py で消す)。
素材は make_source の patch (testsrc2 の右上の 4 分の 1 が動かない灰色)。V1 に素材の頭から 150 フレーム。
タイトルは Templates.drfx の Edit/Titles/Call Out.setting (GroupOperator「CallOut」。公開された入力は Input1〜32 と Line)。
InsertFusionTitleIntoTimeline は名前しか受けないので、置く所と長さは次で決める (21.1.1 の下調べ):
- 長さ: タイムラインのイン点・アウト点 SetMarkInOut(30, 119) (タイムラインの頭を 0 とした番号。両端を含む) を付けて差し込むと、イン点からアウト点までに 90 フレームで入る。
  イン点・アウト点が無いと再生ヘッドの位置に 150 フレーム
- トラック: 差し込みは、ロックしていない全部のトラックを後ろへずらす (V1 の素材が切られ、間が空く)。V1 をロックするとそのままでは
  差し込めない (None)。差し込む先のトラックは、最後に AppendToTimeline で置いたトラックに移る
- callout-none   効果なし (V1 だけ)。比べる元
- callout-lock   V1 に素材、V2 を追加し、V2 の後ろ (200 フレーム目) に素材を 10 フレーム置いて消す (差し込む先を V2 にする)。
                 V1 をロックし、イン点・アウト点を付け、再生ヘッドを 30 に置いて差し込み、イン点・アウト点を消してロックを外す
- callout-index  空のタイムラインにイン点・アウト点を付けて差し込み (V1 に入る)、AddTrack("video", {"index": 1}) で下にトラックを差し込んで
                 タイトルを V2 に押し上げ、V1 に素材を置く (.pyi の AddTrack の 2 つ目の引数は subTrackType の文字列だが、辞書が通った)
- 記録だけ: plain (ロックもイン点・アウト点も無しで差し込む)、lock-only (V1 をロックして差し込む。差し込む先の移しは無し)
値は、置いたクリップの合成 (GetFusionCompByIndex(1)) の CallOut の公開された入力に SetInput で入れ、GetInput で読み戻す
(SetInput の戻り値は None)。指す先 Line Position (Input26) は出力の (1200, 250)、文の位置 Text Position (Input23) は (1560, 420)、
文言「確認」、書体は config の caption、字の大きさ 0.0625、線の色 (Input28〜30) は黄色、字の色 (Input6〜8) は水色。
座標は画面の左下を (0, 0)、右上を (1, 1) とした値 (x/W, 1 − y/H)。
Call Out は頭に出てくる動きが付いている (クリップの頭から 4〜10 フレームで文が出て、10〜32 で線が伸び、32〜36 で指す先の点が出る。
KeyStretcher の StretchStart 36・StretchEnd 120。消える動きは無い)。

合格の条件 (どれも pass。確認できないものは unknown で、合格にしない):
- 素材を取り込め、可逆の形式を選べて効果なしの全編 (150 フレーム) を FFmpeg で読める
- 方法ごとに: タイトルを V2 の [30,120) (90 フレーム) に置けた。V1 は素材 1 つで [0,150)・素材の 0〜150 のまま。
  公開された入力の読み戻しが入れた値。書き出せた (150 フレーム)。0〜29 と 120〜149 は効果なしと同じ画素で、違うのは 30〜119 の中だけ。
  75・119 フレームで: 指す先の点 (黄色の画素を 7x7 で削って残った所の中心) が (1200, 250) から ±3 px。線の途中 (指す先から文の位置への
  15〜60% の所。線から 4 px 以内) に黄色の画素が 40 以上。文の所 (x 1470〜1650、y 370〜470) に水色 (各色 ±40) の画素が 150 以上
- 保存して閉じ (SaveProject・CloseProject)、開き直した (LoadProject) 後: タイトルが V2 の [30,120) に残り、入れた値が読める。
  3 つのどれかが偽なら、これらの項目は unknown (合格にしない)。
  callout-lock を同じ形式で書き出し直すと、開き直す前の書き出しと全フレームが各色 ±2 で同じ (21.1.1 では 35〜119 に 1 の差が出た)
- 残すプロジェクトを保存でき、始めに開いていたプロジェクトを開き直せた
書き出しの待ちが時間切れになったら書き出しを止め (resolve_helpers.stop_rendering)、止められなければその後の項目を進めず、
保存も始めのプロジェクトの開き直しもせずに終える。
記録 (info): 置き方ごとのトラックの中身 (placement)、CallOut の公開された入力の一覧と読んだ値 (inputs_before・inputs_after)、
測った点・線・文の画素 (result)、確認用のフレームの PNG のパス (frames)。

GUI で確認すること (人。JSON では unknown で、合格に数えない):
1. test-native-callout の callout-lock を開き、V2 の Call Out を選び、インスペクタで Line Position・Text Position・文言・色を変える
2. 編集ページでクリップの端を引いて長さを変え、出てくる動きが頭に残るか (KeyStretcher) を見る
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from resolve_helpers import (append_at, changed_box, differing, finish_projects, frames_of, fresh_project,  # noqa: E402
                             make_check, make_source, render_job, render_set, require_lock, spans, frame_md5, stop_rendering)

NAME = "native_callout"
PROJECT = "test-native-callout"
TITLE = "Call Out"
W, H, FPS, L = 1920, 1080, 30, 150   # テストの位置とフレームの番号はこの大きさとフレームレートで決めてある
SHOW, END = 30, 120
TIP, TEXT_AT = (1200, 250), (1560, 420)
LABEL_TEXT, LABEL_SIZE = "確認", 0.0625
LABEL_ROI = (1470, 370, 1650, 470)
YELLOW, CYAN = (255, 200, 0), (80, 220, 255)
LOOK = (75, END - 1)
SAVE_FRAMES = (SHOW - 1, SHOW, SHOW + 5, SHOW + 20, SHOW + 40, 75, END - 1, END)
METHODS = ("lock", "index")


class RenderStuck(RuntimeError):
    """書き出しの待ちが時間切れで、止めても書き出しが続いている (その後の項目を進めず、プロジェクトを保存も切り替えもしない)。"""


def ensure_stopped(proj, rr):
    """書き出しの待ちが時間切れで Resolve がまだ書き出していれば止める (stop_rendering)。止まらなければ RenderStuck を出す。"""
    if proj.IsRenderingInProgress():
        rr["still_rendering"] = True
        stop_rendering(proj)
        rr["stopped"] = not proj.IsRenderingInProgress()
        if not rr["stopped"]:
            raise RenderStuck("書き出しの待ちが時間切れで、StopRendering の後も書き出しが続いている")


def pt(xy):
    return {1: xy[0] / W, 2: 1 - xy[1] / H}


def values(look):
    """CallOut の公開された入力に入れる値 {入力の ID: 値}。"""
    cap = look["caption"]
    y, c = C.rgba01(YELLOW), C.rgba01(CYAN)
    return {"Input26": pt(TIP), "Input23": pt(TEXT_AT), "Input3": LABEL_TEXT, "Input4": cap["font_name"],
            "Input5": cap["font_style"], "Input10": LABEL_SIZE,
            "Input28": y[0], "Input29": y[1], "Input30": y[2], "Input6": c[0], "Input7": c[1], "Input8": c[2]}


def plain(v):
    """GetInput の値を JSON に書ける形にする (点は [x, y])。"""
    if isinstance(v, dict):
        return [round(float(v.get(1, v.get(1.0, 0))), 6), round(float(v.get(2, v.get(2.0, 0))), 6)]
    if isinstance(v, float):
        return round(v, 6)
    return v if isinstance(v, (int, str, type(None))) else str(v)


def same(a, b):
    if isinstance(b, dict):
        b = plain(b)
    if isinstance(b, list):
        return isinstance(a, list) and all(abs(x - y) < 1e-4 for x, y in zip(a, b))
    if isinstance(b, float):
        return isinstance(a, (int, float)) and abs(a - b) < 1e-4
    return a == b


def macro(item):
    comp = item.GetFusionCompByIndex(1) if item else None
    return comp, (comp.FindTool("CallOut") if comp else None)


def input_list(g):
    """CallOut の公開された入力の一覧 [{id, name, type, value}] (value は 0 フレームの値)。"""
    out = []
    for i in (g.GetInputList() or {}).values():
        a = i.GetAttrs() or {}
        out.append({"id": i.ID, "name": a.get("INPS_Name"), "type": a.get("INPS_DataType"), "value": plain(g.GetInput(i.ID))})
    return out


def fill(item, want):
    """公開された入力に値を入れて読み戻す。返す値は記録 {set: SetInput の戻り値, got: 読み戻し, ok}。"""
    comp, g = macro(item)
    if not g:
        return {"macro": False}
    rec = {"macro": True, "tool_id": (g.GetAttrs() or {}).get("TOOLS_RegID"), "before": input_list(g), "set": {}, "got": {}}
    for k, v in want.items():
        rec["set"][k] = g.SetInput(k, v)
    rec["got"] = {k: plain(g.GetInput(k)) for k in want}
    rec["ok"] = all(same(rec["got"][k], v) for k, v in want.items())
    rec["animated"] = {k: {f: plain(g.GetInput(k, f)) for f in (0, 4, 10, 20, 32, 36, 60, 89)}
                       for k in ("Input24", "Input25", "Input27")}   # Opacity・Length・Zoom (動きの付いた入力)
    return rec


def track_items(tl, t0):
    return {f"V{n}": [{"name": x.GetName(), "start": x.GetStart() - t0, "end": x.GetEnd() - t0,
                       "src": [x.GetSourceStartFrame(), x.GetSourceEndFrame()]}
                      for x in (tl.GetItemListInTrack("video", n) or [])]
            for n in range(1, tl.GetTrackCount("video") + 1)}


def placed_ok(tracks, src_name):
    v1, v2 = tracks.get("V1") or [], tracks.get("V2") or []
    return (len(v1) == 1 and v1[0]["name"] == src_name and v1[0]["start"] == 0 and v1[0]["end"] == L and v1[0]["src"] == [0, L]
            and len(v2) == 1 and v2[0]["name"] == TITLE and v2[0]["start"] == SHOW and v2[0]["end"] == END)


def tc(t0, f):
    return C.frame_to_timecode(t0 + f, FPS)


def insert_marked(tl, t0, rec):
    """イン点・アウト点 [SHOW, END - 1] を付け、再生ヘッドを SHOW に置いて Call Out を差し込み、イン点・アウト点を消す。返す値はタイトルのクリップか None。"""
    rec["mark"] = bool(tl.SetMarkInOut(SHOW, END - 1))
    rec["marks"] = tl.GetMarkInOut()
    rec["playhead"] = bool(tl.SetCurrentTimecode(tc(t0, SHOW)))
    title = tl.InsertFusionTitleIntoTimeline(TITLE)
    rec["inserted"] = bool(title)
    rec["clear_mark"] = bool(tl.ClearMarkInOut())
    return title


def build_lock(mp, proj, clip):
    tl = mp.CreateEmptyTimeline("callout-lock")
    proj.SetCurrentTimeline(tl)
    t0 = tl.GetStartFrame()
    rec = {}
    _, rec["v1"] = append_at(mp, tl, clip, 0, L)
    dummy, rec["dummy"] = append_at(mp, tl, clip, 200, 10, track=2)
    rec["dummy_deleted"] = bool(tl.DeleteClips([dummy])) if dummy else None
    rec["lock"] = bool(tl.SetTrackLock("video", 1, True))
    title = insert_marked(tl, t0, rec)
    rec["unlock"] = bool(tl.SetTrackLock("video", 1, False))
    rec["tracks"] = track_items(tl, t0)
    return tl, title, rec


def build_index(mp, proj, clip):
    tl = mp.CreateEmptyTimeline("callout-index")
    proj.SetCurrentTimeline(tl)
    t0 = tl.GetStartFrame()
    rec = {}
    title = insert_marked(tl, t0, rec)
    rec["title_track_before"] = title.GetTrackTypeAndIndex() if title else None
    rec["add_track"] = bool(tl.AddTrack("video", {"index": 1}))
    rec["title_track_after"] = title.GetTrackTypeAndIndex() if title else None
    _, rec["v1"] = append_at(mp, tl, clip, 0, L, track=1)
    rec["tracks"] = track_items(tl, t0)
    return tl, title, rec


def probe(mp, proj, clip, name, lock):
    """記録だけ: イン点・アウト点を付けずに再生ヘッド 30 で差し込む (lock なら V1 をロックして)。返す値はトラックの中身。"""
    tl = mp.CreateEmptyTimeline(name)
    proj.SetCurrentTimeline(tl)
    t0 = tl.GetStartFrame()
    append_at(mp, tl, clip, 0, L)
    tl.AddTrack("video")
    rec = {"lock": bool(tl.SetTrackLock("video", 1, True)) if lock else None,
           "playhead": bool(tl.SetCurrentTimecode(tc(t0, SHOW)))}
    title = tl.InsertFusionTitleIntoTimeline(TITLE)
    rec["inserted"] = bool(title)
    rec["title"] = ({"track": title.GetTrackTypeAndIndex(), "start": title.GetStart() - t0, "duration": title.GetDuration()}
                    if title else None)
    rec["tracks"] = track_items(tl, t0)
    return rec


# ---------- 書き出しの測り方 ----------
def near_mask(img, rgb, tol):
    import numpy as np
    return (np.abs(img.astype(np.int16) - np.array(rgb)).max(axis=2) <= tol)


def point_center(img):
    """指す先の点: 先の近く (±40 px) の黄色 (各色 ±60) の画素を 7x7 で削って残った所の中心 [x, y]。残らなければ None。"""
    import numpy as np
    x0, y0 = TIP[0] - 40, TIP[1] - 40
    m = near_mask(img[y0:y0 + 81, x0:x0 + 81], YELLOW, 60).astype(np.int32)
    k = 7
    s = np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    full = (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) == k * k
    ys, xs = np.nonzero(full)
    if not len(xs):
        return None
    return [round(float(x0 + xs.mean() + k // 2), 1), round(float(y0 + ys.mean() + k // 2), 1)]


def line_pixels(img):
    """指す先から文の位置への 15〜60% の所で、線から 4 px 以内にある黄色 (各色 ±60) の画素の数。"""
    import numpy as np
    m = near_mask(img, YELLOW, 60)
    (ax, ay), (bx, by) = TIP, TEXT_AT
    ys, xs = np.mgrid[min(ay, by):max(ay, by) + 1, min(ax, bx):max(ax, bx) + 1]
    dx, dy = bx - ax, by - ay
    t = ((xs - ax) * dx + (ys - ay) * dy) / float(dx * dx + dy * dy)
    dist = np.abs((xs - ax) * dy - (ys - ay) * dx) / np.hypot(dx, dy)
    sel = (t >= 0.15) & (t <= 0.6) & (dist <= 4)
    return int(m[ys[sel], xs[sel]].sum())


def analyse(key, renders, md5, frames_dir):
    m5 = md5.get(key)
    if not m5:
        return None
    from PIL import Image
    d = differing(m5, md5["none"])
    res = {"frames": len(m5), "differ_from_none": spans(d), "first_differ": d[0] if d else None,
           "off_same": all(SHOW <= f < END for f in d) and len(m5) == L, "at": {}, "png": {}}
    for n, fr in frames_of({"r": renders[key]["path"], "b": renders["none"]["path"]}, sorted(set(LOOK + SAVE_FRAMES))):
        r, b = fr["r"], fr["b"]
        if r is None or b is None:
            continue
        if n in SAVE_FRAMES:
            p = os.path.join(frames_dir, f"{NAME}-{key}-{n:03d}.png")
            Image.fromarray(r).save(p)
            res["png"][n] = p
        if n in LOOK:
            x0, y0, x1, y1 = LABEL_ROI
            res["at"][n] = {"point": point_center(r), "line_pixels": line_pixels(r),
                            "label_pixels": int(near_mask(r[y0:y1, x0:x1], CYAN, 40).sum()), "drawn_box": changed_box(r, b, 40)}
    at = [res["at"].get(n) for n in LOOK]
    res["point_ok"] = all(a and a["point"] and abs(a["point"][0] - TIP[0]) <= 3 and abs(a["point"][1] - TIP[1]) <= 3 for a in at)
    res["line_ok"] = all(a and a["line_pixels"] >= 40 for a in at)
    res["label_ok"] = all(a and a["label_pixels"] >= 150 for a in at)
    return res


def reread(proj, want, src_name):
    """開き直した後: タイムラインごとに V2 のタイトルの位置と、公開された入力の値を読む。"""
    out = {}
    tls = {proj.GetTimelineByIndex(i).GetName(): proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)}
    for key in METHODS:
        tl = tls.get(f"callout-{key}")
        if not tl:
            out[key] = {"timeline": False}
            continue
        t0 = tl.GetStartFrame()
        tracks = track_items(tl, t0)
        v2 = tl.GetItemListInTrack("video", 2) or []
        _, g = macro(v2[0]) if v2 else (None, None)
        got = {k: plain(g.GetInput(k)) for k in want} if g else None
        out[key] = {"timeline": True, "tracks": tracks, "placed_ok": placed_ok(tracks, src_name), "macro": bool(g), "got": got,
                    "values_ok": bool(got) and all(same(got[k], v) for k, v in want.items()),
                    "inputs_after": input_list(g) if g else None}
    return out, tls


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    checks, info = [], {"project": PROJECT, "placement": {}, "methods": {}}
    check = make_check(checks)
    path = os.path.join(OUT, f"{NAME}.json")
    require_lock()
    resolve = C.connect_resolve(quiet=True)
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    prev = cur.GetName() if cur else None
    info["previous_project"] = prev
    info["product"] = f"{resolve.GetProductName()} {resolve.GetVersionString()}"
    want = values(look)
    info["want"] = {k: plain(v) for k, v in want.items()}
    try:
        src = make_source(cfg, W, H, FPS, patch=True)
        proj, info["fresh"] = fresh_project(pm, PROJECT, W, H, FPS)
        if not proj:
            raise RuntimeError(f"プロジェクト {PROJECT} を作れない ({info['fresh']})")
        mp = proj.GetMediaPool()
        src_clip = (mp.ImportMedia([src]) or [None])[0]
        check("素材を取り込めた", bool(src_clip))
        if not src_clip:
            raise RuntimeError("素材を取り込めない")
        src_name = src_clip.GetName()
        tls = {}
        tls["none"] = mp.CreateEmptyTimeline("callout-none")
        proj.SetCurrentTimeline(tls["none"])
        append_at(mp, tls["none"], src_clip, 0, L)
        made = {}
        for key, build in (("lock", build_lock), ("index", build_index)):
            rec = {}
            try:
                tl, title, rec = build(mp, proj, src_clip)
                tls[key] = tl
                rec["fill"] = fill(title, want) if title else None
            except Exception as e:
                title = None
                rec["error"] = f"{type(e).__name__}: {e}"
            info["placement"][key] = rec
            ok = bool(title) and placed_ok(rec.get("tracks") or {}, src_name)
            made[key] = ok and bool((rec.get("fill") or {}).get("ok"))
            check(f"callout-{key}: タイトルを V2 の [30,120) に置け、V1 の素材は [0,150) のまま", ok, rec.get("tracks"))
            check(f"callout-{key}: 公開された入力の読み戻しが入れた値", (rec.get("fill") or {}).get("ok") if title else None,
                  (rec.get("fill") or {}).get("got"), info["want"])
        for name, lock in (("plain", False), ("lock-only", True)):
            try:
                info["placement"][name] = probe(mp, proj, src_clip, f"callout-probe-{name}", lock)
            except Exception as e:
                info["placement"][name] = {"error": f"{type(e).__name__}: {e}"}

        rs = render_set(proj, tls, "none", [k for k in METHODS if made[k]], DIR, NAME)
        info.update(rs["info"])
        info["render_stop"] = {}
        ensure_stopped(proj, info["render_stop"])   # render_set は時間切れで止めるが、止まったかは見ない
        renders, md5 = rs["renders"], rs["md5"]
        check("可逆の形式を選べ、効果なしの全編を FFmpeg で読める (150 フレーム)", bool(rs["chosen"]) and len(md5["none"]) == L,
              {"chosen": rs["chosen"], "frames": len(md5["none"])}, L)
        for key in METHODS:
            res = analyse(key, renders, md5, DIR)
            info["methods"][key] = res
            label = f"callout-{key}"
            check(f"{label}: 書き出せた (150 フレーム)", (res["frames"] == L) if res else None, res and res["frames"], L)
            check(f"{label}: 0〜29 と 120〜149 は効果なしと同じ (違うのは 30〜119 の中だけ)", res["off_same"] if res else None,
                  res and res["differ_from_none"], [[SHOW, END - 1]])
            check(f"{label}: 75・119 フレームの指す先の点が (1200, 250) から ±3 px", res["point_ok"] if res else None,
                  res and {n: a["point"] for n, a in res["at"].items()}, list(TIP))
            check(f"{label}: 75・119 フレームで線の途中に黄色の画素が 40 以上", res["line_ok"] if res else None,
                  res and {n: a["line_pixels"] for n, a in res["at"].items()}, 40)
            check(f"{label}: 75・119 フレームで文の所に水色の画素が 150 以上", res["label_ok"] if res else None,
                  res and {n: a["label_pixels"] for n, a in res["at"].items()}, 150)

        # 保存して閉じ、開き直す (どれか 1 つでも偽なら、開き直しの項目は合格にしない)
        reo = {"saved": bool(pm.SaveProject())}
        reo["closed"] = bool(reo["saved"] and pm.CloseProject(proj))   # CloseProject は保存しないので、保存できたときだけ閉じる
        cur2 = pm.GetCurrentProject()
        reo["current_after_close"] = cur2.GetName() if cur2 else None
        if reo["closed"]:
            proj = pm.LoadProject(PROJECT)
            reo["reloaded"] = bool(proj) and proj.GetName() == PROJECT
        info["reopen"] = reo
        if all(reo.get(k) for k in ("saved", "closed", "reloaded")):
            after, tls2 = reread(proj, want, src_name)
            info["after_reopen"] = after
            for key in METHODS:
                a = after.get(key) or {}
                check(f"callout-{key}: 開き直した後もタイトルが V2 の [30,120) に残る", a.get("placed_ok"), a.get("tracks"))
                check(f"callout-{key}: 開き直した後も入れた値が読める", a.get("values_ok"), a.get("got"), info["want"])
            # 開き直した後に書き出し直し、前の書き出しと比べる
            chosen = rs["chosen"]
            again = None
            if chosen and md5.get("lock") and tls2.get("callout-lock"):
                proj.SetCurrentTimeline(tls2["callout-lock"])
                rr = render_job(proj, {"SelectAllFrames": True, "TargetDir": DIR, "CustomName": f"{NAME}-lock-reopened"},
                                chosen["format"], chosen["codec"], 600)
                ensure_stopped(proj, rr)
                again = frame_md5(rr["path"]) if rr.get("done") and rr.get("written") else None
                info["reopen"]["render"] = {k: rr.get(k) for k in ("path", "done", "written", "status")}
                info["reopen"]["differ_from_before"] = spans(differing(again, md5["lock"])) if again else None
            max_diff = None
            if again and len(again) == L:
                import numpy as np
                diff_frames = differing(again, md5["lock"])
                max_diff = 0
                for _, fr in frames_of({"a": renders["lock"]["path"], "b": rr["path"]}, diff_frames):
                    if fr["a"] is None or fr["b"] is None:
                        max_diff = None
                        break
                    max_diff = max(max_diff, int(np.abs(fr["a"].astype(np.int16) - fr["b"].astype(np.int16)).max()))
            info["reopen"]["max_diff_from_before"] = max_diff
            check("callout-lock: 開き直した後の書き出しが、前の書き出しと全フレーム同じ (各色 ±2)",
                  (max_diff <= 2) if max_diff is not None else None,
                  {"differ": info["reopen"].get("differ_from_before"), "max": max_diff}, 2)
        else:
            for key in METHODS:
                check(f"callout-{key}: 開き直した後もタイトルが V2 の [30,120) に残る", None, reo)
                check(f"callout-{key}: 開き直した後も入れた値が読める", None, reo)
            check("callout-lock: 開き直した後の書き出しが、前の書き出しと全フレーム同じ (各色 ±2)", None, reo)
        check("GUI (人): インスペクタで Line Position・Text Position・文言・色を直せ、端を引いて長さを変えられる", None, None,
              "人が GUI で確認する")
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        if isinstance(e, RenderStuck):
            info["render_stuck"] = str(e)
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        if info.get("render_stuck"):   # 書き出しが止まらない。保存もプロジェクトの切り替えもせずに終える
            C.write_json(path, {"name": NAME, "info": info, "checks": checks})
            code = C.report(False, f"{NAME}: 書き出しを止められず、途中で終えた", path, [info["render_stuck"]])
        else:
            cur = pm.GetCurrentProject()
            saved = bool(pm.SaveProject()) if cur and cur.GetName() == PROJECT else None
            check(f"残すプロジェクト {PROJECT} を保存できた", saved, cur.GetName() if cur else None)
            code = finish_projects(resolve, NAME, path, [], prev, info, checks, min_checks=24)
    sys.exit(code)
