"""区間の書き出し (SetRenderSettings の MarkIn・MarkOut) が、全編の書き出しの同じフレームと画素まで一致するか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/render_range.py
(Resolve Studio を起動しておく。結果は 出力先/tests/render_range.json。書き出した動画は 作業フォルダー/render_range に残る)

組むもの: 機密の無い合成の素材 (FFmpeg の testsrc2。タイムラインと同じ大きさとフレームレートで 10 秒。絵が 1 フレームごとに
変わる) を、sb2resolve.py で 3 行の構成表に組む (書き出しはしない)。1 行目 2 秒の後ろに 1 秒止める (hold)、2 行目 2 秒、3 行目 1 秒。
テロップは全部の行に出す。作ったプロジェクト (test-render_range-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

書き出しの形式: 全編と区間を同じ形式で書き出し、FFmpeg の framemd5 (デコードした画素の MD5) でフレームごとに比べる。
比べるには、フレームごとに独立して符号化され、同じ入力なら同じ値になる形式が要る。H.264 (構成表の既定の mp4) は区間の頭が
キーフレームになるなど前後のフレームで符号が変わるので、画素の一致を比べられない。
候補は resolve_helpers.LOSSLESS に並べた形式と codec の識別子 (先頭は実機で通った mov / RGB8) のうち、
GetRenderFormats・GetRenderCodecs が返すもの。SetCurrentRenderFormatAndCodec が真を返した候補で
全編を書き出し、FFmpeg が読めた最初のものを使う (最大 4 つ試す。読めなかった書き出しは消す。選び方は resolve_helpers の
lossless_candidates と render_lossless で、native_*.py も同じものを使う)。調べた形式と codec の全部、
試したもの、選んだものは JSON の info の formats と lossless に残る。

区間 (タイムラインの頭を 0 としたフレーム。両端を含む):
- mid  1 行目の途中 (10〜20)
- head タイムラインの頭から (0〜5)
- one  1 フレームだけ (2 行目の 8 フレーム目)
- cut  2 行目と 3 行目の境目をまたぐ (前後 5 フレーム)
- hold 1 行目の終わり → 止めたクリップ → 2 行目の頭 (前後 5 フレーム)
- tail タイムラインの最後のフレームで終わる (最後の 10 フレーム)
MarkIn・MarkOut には GetStartFrame を足した値 (タイムラインの絶対フレーム) を渡し、SelectAllFrames は偽にする。

sb2resolve.py の --rows (構成表の行を指定した部分の書き出し): 同じ構成表の render を選んだ可逆の形式にした写しを、
--rows 1 (1 行目と止めたクリップ) と --rows 2-3 (カットの境目をまたぎ、最後のフレームで終わる) で、それぞれ新しいプロジェクトに
組んで書き出す (sb2resolve.run。CLI と同じ入口)。書き出し先は 作業フォルダー/render_range/rows-<時刻> の下 (--output)。
記録の range のフレームが構成表の行から決まる値と同じか、書き出しが全編の同じフレームと画素一致するかを見る。

確認する項目 (どれも pass で合格。確認できないものは unknown で、合格にしない):
- sb2resolve で組める
- 可逆の形式を選べ、FFmpeg が全編を読める
- 全編を書き出せる。フレーム数が構成表の合計と GetEndFrame - GetStartFrame に合う
- 全編で、止めたクリップの中の隣り合うフレームは同じ画素、それ以外 (止めたクリップに入る所を除く) は違う画素
  (区間の比較で位置を見分けられることの確認)
- 区間ごとに: 書き出しのフレーム数が MarkOut - MarkIn + 1 (両端を含む)。各フレームが全編の同じフレームと同じ画素
- MarkIn・MarkOut はタイムラインの絶対フレーム: 絶対の値の mid が全編と一致し、GetStartFrame を足さない値 (relative) では
  一致しないなら pass、逆なら fail、どちらも一致・どちらも不一致・GetStartFrame が 0 なら unknown
- --rows ごとに: 組んで書き出せ、記録の range のフレームと MarkIn・MarkOut が行から決まる値と同じ。
  書き出しのフレーム数が range の count。各フレームが全編の同じフレームと同じ画素
- 作ったプロジェクトを消し、始めのプロジェクトを開き直せる

記録だけで合否を付けないもの (info の observations): SelectAllFrames を真にして MarkIn・MarkOut も渡したとき (selectall)、
MarkOut がタイムラインの外のとき (past_end)、MarkIn が MarkOut より後のとき (reversed。作業は追加するだけで書き出さずに消す)。
どの書き出しも、SetRenderSettings の戻り値、GetRenderJobList で読み戻した作業、書き出しの秒数・ファイルの大きさ・
フレーム数、合わなかったときに全編のどのフレームと同じだったか (full_frames・offsets) を info の ranges に残す。
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
import sb2resolve as S  # noqa: E402
from resolve_helpers import (finish_projects, frame_md5, make_check, make_source, render_job,  # noqa: E402
                             render_lossless, require_lock, run_storyboard)

NAME = "render_range"


def compare(full, part, a, b):
    """区間 a〜b (両端を含む) の書き出しのフレームの MD5 part を、全編の MD5 full と比べる。
    full_frames は区間の各フレームと同じ画素の全編のフレーム (同じものが幾つかあれば a + i に近いもの。無ければ None)。"""
    found = []
    for i, h in enumerate(part):
        js = [j for j, x in enumerate(full) if x == h]
        found.append(min(js, key=lambda j: abs(j - (a + i))) if js else None)
    return {"frames": len(part), "want_frames": b - a + 1, "same": part == full[a:b + 1], "full_frames": found,
            "offsets": sorted({j - (a + i) for i, j in enumerate(found) if j is not None}),
            "unmatched": sum(j is None for j in found)}


def neighbours(full, hold_at, hold_n):
    """全編の隣り合うフレームが同じか。止めたクリップ [hold_at, hold_at + hold_n) の中は同じ、それ以外は違うはず。
    止めたクリップに入る所 (直前のフレームと止めたクリップの頭) は記録だけ。"""
    inside, outside, entering = [], [], None
    for j in range(len(full) - 1):
        same = full[j] == full[j + 1]
        if hold_at is not None and j + 1 == hold_at:
            entering = same
        elif hold_at is not None and hold_at <= j and j + 1 < hold_at + hold_n:
            inside.append(same)
        else:
            outside.append(same)
    return {"inside_same": f"{sum(inside)}/{len(inside)}", "outside_differ": f"{len(outside) - sum(outside)}/{len(outside)}",
            "entering_same": entering, "ok": all(inside) and not any(outside)}


def storyboard(src, W, H, fps):
    return {"source": src, "output": os.path.join(DIR, f"{NAME}-final.mp4"), "fps": fps, "seconds_per_row": 2,
            "timeline": {"width": W, "height": H},
            "rows": [{"start": 0, "end": 2, "caption": "1 行目 (後ろで止める)", "hold": {"seconds": 1}},
                     {"start": 3, "end": 5, "caption": "2 行目"},
                     {"start": 6, "end": 7, "seconds": 1, "caption": "3 行目"}]}


def rows_render(spec, want, sb_full):
    """sb2resolve.run に --rows spec を渡し、可逆の形式で部分を書き出して全編と比べる。want は行から決まる (最初, 最後)。"""
    key = f"rows{spec}"
    sbp2 = os.path.join(OUT, f"{NAME}.{key}.storyboard.json")
    C.write_json(sbp2, sb_full | {"render": {"format": fmt, "codec": codec}})
    out_dir = os.path.join(DIR, f"rows-{int(time.time())}")
    args = argparse.Namespace(storyboard=sbp2, source=None, name=f"test-{NAME}-{key}-{int(time.time())}", project=None,
                              timeline=None, output=os.path.join(out_dir, f"{NAME}-{key}.{fmt}"), render=False,
                              render_anyway=False, rows=spec, overwrite=False, dry_run=False)
    log2 = {}
    try:
        ok, summary, lines = S.run(args, log2)
    finally:
        created.append(log2.get("project"))
    rr, rg = log2.get("render") or {}, log2.get("range") or {}
    obs = {"ok": ok, "summary": summary, "lines": lines, "range": rg, "render": rr, "want": want}
    if rr.get("done") and rr.get("path") and os.path.isfile(rr["path"]):
        obs["compare"] = compare(full, frame_md5(rr["path"]), *want)
    info["rows"][key] = obs
    # 新しいプロジェクトのタイムラインも、頭は全編と同じ GetStartFrame のはず (MarkIn - 頭を 0 とした最初のフレーム)
    marks = [rg.get("mark_in"), rg.get("mark_out")]
    same_marks = None not in marks and marks == [t0 + want[0], t0 + want[1]]
    check(f"--rows {spec}: 組んで書き出せ、記録の range が行から決まるフレームと MarkIn・MarkOut (GetStartFrame を足す) と同じ",
          bool(ok and rr.get("done") and rg.get("frames") == list(want) and rg.get("count") == want[1] - want[0] + 1
               and same_marks), {"range": rg, "summary": summary}, {"frames": list(want), "start_frame": t0})
    cmp = obs.get("compare")
    check(f"--rows {spec}: 書き出しのフレーム数が range の count", cmp["frames"] == cmp["want_frames"] if cmp else None,
          cmp and cmp["frames"], cmp and cmp["want_frames"])
    check(f"--rows {spec}: 各フレームが全編の同じフレームと同じ画素", cmp["same"] if cmp else None,
          cmp and {"full_frames": cmp["full_frames"], "offsets": cmp["offsets"]}, list(want))


def mark_in_out(tl):
    """タイムラインの In・Out の点 (Timeline.GetMarkInOut)。呼べなければ None。"""
    try:
        return tl.GetMarkInOut()
    except Exception as e:   # 21.1.1 に無い関数なら記録だけ
        return f"呼べない ({e})"


def range_render(key, a, b, absolute=True, select_all=False):
    """区間 a〜b を書き出して全編と比べる。記録は info の ranges[key]。"""
    mi, mo = (t0 + a, t0 + b) if absolute else (a, b)
    settings = {"SelectAllFrames": select_all, "MarkIn": mi, "MarkOut": mo, "TargetDir": DIR, "CustomName": f"{NAME}-{key}"}
    rr = render_job(proj, settings, fmt, codec, limit)
    rr["range"] = [a, b]
    rr["timeline_mark"] = mark_in_out(tl)
    if rr["done"] and rr["written"]:
        part = frame_md5(rr["path"])
        rr["compare"] = compare(full, part, a, b)
    info["ranges"][key] = rr
    return rr


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    W, H, FPS = look["timeline"]["width"], look["timeline"]["height"], int(look["fps"])
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    checks, info, created = [], {"ranges": {}, "observations": {}, "rows": {}}, []
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
    try:
        src = make_source(cfg, W, H, FPS)
        info["source"] = C.probe_video(src)
        sbp = os.path.join(OUT, f"{NAME}.storyboard.json")
        C.write_json(sbp, storyboard(src, W, H, FPS))
        log = {}
        ok, summary, lines = run_storyboard(sbp, f"test-{NAME}-{int(time.time())}", created, log, render=False)
        info["build"] = {"ok": ok, "summary": summary, "lines": lines, "rows": log.get("rows"),
                         "timeline_seconds": (log.get("timeline") or {}).get("seconds"),
                         "fusion_seconds": (log.get("fusion") or {}).get("seconds")}
        # 書き出しの設定を触るのは、このテストが作ったプロジェクトだけ (組めずに始めのプロジェクトが開いたままなら触らない)
        proj = pm.GetCurrentProject()
        if not (ok and proj and proj.GetName() == log.get("project")):
            proj = None
        tl = proj.GetCurrentTimeline() if proj else None

        check("sb2resolve で組める", ok and tl is not None, summary, lines)
        rows = log.get("rows") or []
        total = sum(p["frames"] + p["hold_frames"] for p in rows)
        t0 = tl.GetStartFrame() if tl else 0
        info["timeline"] = {"start": t0, "end": tl.GetEndFrame() if tl else None, "plan_total": total}
        limit = max(600, total / float(FPS) * 20)

        # 可逆の形式を選ぶ: 選べた候補で全編を書き出し、FFmpeg が読めた最初のものを使う (resolve_helpers.render_lossless)
        chosen, full, tried = None, [], []
        if proj and tl:
            lz = render_lossless(proj, DIR, f"{NAME}-all", limit)
            info["formats"] = lz["formats"]
            chosen, full, tried = lz["chosen"], lz["md5"], lz["tried"]
            if chosen:
                info["full"] = lz["render"]
            info["lossless"] = {"candidates": lz["candidates"], "tried": tried, "chosen": chosen,
                                "current": proj.GetCurrentRenderFormatAndCodec()}
        check("可逆の形式を選べ、FFmpeg が全編を読める", bool(chosen) if proj and tl else None, chosen, tried)
        fmt, codec = (chosen["format"], chosen["codec"]) if chosen else (None, None)
        check("全編を書き出せる", bool(full) if proj and tl else None, info.get("full", {}).get("status"))
        span = (info["timeline"]["end"] - t0) if tl else None
        check("全編のフレーム数が構成表の合計と GetEndFrame - GetStartFrame に合う",
              (len(full) == total == span) if full else None, {"frames": len(full), "plan_total": total, "end-start": span})
        r1 = rows[0] if rows else {}
        nb = neighbours(full, r1.get("hold_rec"), r1.get("hold_frames") or 0) if full else None
        info["neighbours"] = nb
        check("全編で、止めたクリップの中は同じ画素、それ以外の隣り合うフレームは違う画素", nb["ok"] if nb else None, nb)

        # 区間 (タイムラインの頭を 0 としたフレーム。両端を含む)
        r2, r3 = (rows[1], rows[2]) if len(rows) == 3 else ({}, {})
        cases = {}
        if len(rows) == 3:
            cases = {"mid": (r1["rec"] + 10, r1["rec"] + 20), "head": (0, 5), "one": (r2["rec"] + 7, r2["rec"] + 7),
                     "cut": (r3["rec"] - 5, r3["rec"] + 4), "hold": (r1["hold_rec"] - 5, r2["rec"] + 4),
                     "tail": (total - 10, total - 1)}
        info["cases"] = cases
        for key in ("mid", "head", "one", "cut", "hold", "tail"):
            a, b = cases.get(key, (None, None))
            cmp = range_render(key, a, b).get("compare") if full and key in cases else None
            check(f"{key}: 書き出しのフレーム数が MarkOut - MarkIn + 1 (両端を含む)",
                  cmp["frames"] == cmp["want_frames"] if cmp else None,
                  cmp and cmp["frames"], cmp and cmp["want_frames"])
            check(f"{key}: 各フレームが全編の同じフレームと同じ画素", cmp["same"] if cmp else None,
                  cmp and {"full_frames": cmp["full_frames"], "offsets": cmp["offsets"]}, [a, b])

        # 絶対か相対か: GetStartFrame を足さない値で mid を書き出して比べる
        abs_same = (info["ranges"].get("mid", {}).get("compare") or {}).get("same")
        rel_same = None
        if full and "mid" in cases:
            rel = range_render("relative", *cases["mid"], absolute=False)
            rel_same = bool((rel.get("compare") or {}).get("same"))
        verdict = None
        if t0 and abs_same is not None and rel_same is not None and abs_same != rel_same:
            verdict = bool(abs_same)
        check("MarkIn・MarkOut はタイムラインの絶対フレーム (GetStartFrame を足した値で区間になり、足さない値ではならない)",
              verdict, {"absolute_same": abs_same, "relative_same": rel_same, "start_frame": t0})

        # 記録だけ (合否を付けない)
        if full and "mid" in cases:
            sa = range_render("selectall", *cases["mid"], select_all=True)
            info["observations"]["selectall"] = {"set_settings": sa["set_settings"], "frames": (sa.get("compare") or {}).get("frames"),
                                                 "full_frames": len(full), "job_entry": sa.get("job_entry")}
            pe = range_render("past_end", total - 5, total + 30)
            info["observations"]["past_end"] = {"set_settings": pe["set_settings"], "done": pe["done"],
                                                "frames": (pe.get("compare") or {}).get("frames"), "job_entry": pe.get("job_entry")}
            proj.SetCurrentRenderFormatAndCodec(fmt, codec)
            rv = {"set_settings": bool(proj.SetRenderSettings({"SelectAllFrames": False, "MarkIn": t0 + 20, "MarkOut": t0 + 10,
                                                               "TargetDir": DIR, "CustomName": f"{NAME}-reversed"}))}
            job = proj.AddRenderJob()
            rv["job"] = job
            rv["job_entry"] = next((j for j in (proj.GetRenderJobList() or []) if j.get("JobId") == job), None) if job else None
            rv["deleted"] = bool(proj.DeleteRenderJob(job)) if job else None
            info["observations"]["reversed"] = rv
        # sb2resolve --rows: 新しいプロジェクトに組むので、上の書き出しが全部終わってから流す
        for spec, want in (("1", (r1.get("rec"), (r1.get("hold_rec") or 0) + (r1.get("hold_frames") or 0) - 1)),
                           ("2-3", (r2.get("rec"), total - 1))):
            if full and len(rows) == 3:
                rows_render(spec, want, storyboard(src, W, H, FPS))
            else:
                for label in ("組んで書き出せ", "書き出しのフレーム数", "各フレームが全編"):
                    check(f"--rows {spec}: {label}", None, "全編か構成表の行が無い")
        info["timing"] = {k: {"frames": (v.get("compare") or {}).get("frames", v.get("frames")), "seconds": v.get("seconds"),
                              "bytes": v.get("bytes")}
                          for k, v in (info["ranges"] | {"all": info.get("full", {})}).items()}
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=25)
    sys.exit(code)
