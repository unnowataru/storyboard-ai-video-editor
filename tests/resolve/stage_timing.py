"""構成表から完成版を確認するまでの工程を外から順に流し、工程ごとの秒数を測る。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/stage_timing.py
(Resolve Studio を起動しておく。結果は 出力先/tests/stage_timing.json。書き出しは 出力先/tests/stage_timing.mp4、
照合の報告は 出力先/check-final/stage_timing-*.json、area_sheet の画像は 作業フォルダー/stage_timing/area-sheet)

構成表: 見本の examples/sample1-storyboard.json (17 行。テロップ・肩テロップ・ズーム・マスク・focus・callout・hold) の行をそのまま使い、
素材のパスだけをここで作る合成の素材に替える。素材は 4K (3840x2160、30 fps) の testsrc2 を 60 秒作り、構成表の最後の行まで届く長さに
つないだもの (作業フォルダー/test-media。あれば作り直さない)。
作ったプロジェクト (test-stage_timing-*) は最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

工程 (この順に流す。前の工程が終わらなければ後ろは not_run):
- build: sb2resolve.run で組む (書き出しなし)。sb2resolve の記録の timeline.seconds と fusion.seconds も残す
- render: 全編を書き出す (sb2resolve.render。--render と同じ呼び方)
- range_render: 区間だけ書き出す (render_range.py と同じ MarkIn・MarkOut。タイムラインの真ん中から 6 秒、形式は全編と同じ)。
  別の欄で、全編の秒数とは足さない
- check_final: scripts/check_final.py を子プロセスで流す (--pixel なし)
- check_final_pixel: 同じく --pixel あり
- area_sheet: scripts/area_sheet.py を子プロセスで流す (見る範囲は 1 行目のマスクをタイムラインの画素に直したもの)
工程ごとに status (pass・fail・error・not_run)、秒数、終了コードか戻り値、画面の 1 行目を info の stages に残す。
check_final は、この実行で報告の JSON が新しく書かれたら完了とし、status は終了コード 0 なら pass、それ以外は fail
(照合の fail・unknown。合成の素材なので出うる。記録だけ)。area_sheet は終了コード 0 と報告の JSON で完了 (pass)。
完了しなかった工程 (報告が書かれない、area_sheet が 0 以外で終わる) は error。

確認する項目 (合格): 6 つの工程がどれも完了し (組む・書き出しは done が真、check_final は pass か fail、area_sheet は pass)、
秒数が取れたこと。error と not_run は不合格。
照合の fail や unknown の数は記録するが、このテストの合否にはしない。秒数は PC の性能と負荷で変わるので合否に使わない。
"""
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts")
sys.path.insert(0, SCRIPTS)
import common as C  # noqa: E402
import sb2resolve as S  # noqa: E402
from resolve_helpers import finish_projects, make_check, make_source as helper_source, render_job, require_lock, run_storyboard  # noqa: E402

NAME = "stage_timing"
SRC_W, SRC_H, FPS = 3840, 2160, 30
STAGES = ("build", "render", "range_render", "check_final", "check_final_pixel", "area_sheet")


def make_source(seconds):
    """4K の testsrc2 を 60 秒作り、seconds 秒までつなぐ (つなぐときは符号化し直さない)。あれば作り直さない。
    60 秒は resolve_helpers.make_source (色は BT.709) で作る。"""
    short = helper_source(cfg, SRC_W, SRC_H, FPS, seconds=60)
    path = short.replace("-60s-", f"-{seconds}s-loop-")
    if not os.path.isfile(path):
        C.run_tool("ffmpeg", ["-stream_loop", "-1", "-i", short, "-t", str(seconds), "-c", "copy", path])
    return path


def storyboard(src):
    with open(os.path.join(C.REPO, "examples", "sample1-storyboard.json"), encoding="utf-8") as f:
        sb = json.load(f)
    sb["source"] = src
    sb["output"] = os.path.join(OUT, f"{NAME}.mp4")
    return sb


def tool(stage, args, report, need_zero=False):
    """scripts のスクリプトを子プロセスで流す (同じロックの中。環境はそのまま引き継ぐ)。記録は stages[stage]。
    完了とみなすのは、この実行で report (スクリプトが書く報告の JSON) が新しく書かれたとき (need_zero なら終了コード 0 も要る)。
    捕まえられなかった例外や報告を書く前の失敗も終了コード 1 になりうるので、終了コードだけでは決めない。
    完了したときの status は終了コード 0 なら pass、それ以外は fail (照合の fail・unknown。記録だけ)。完了しなければ error。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if os.path.isfile(report):   # 前の実行の報告を、この実行の報告と取り違えないよう先に消す
        os.remove(report)
    t = time.time()
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "").strip().splitlines()
    fresh = os.path.isfile(report)
    done = fresh and (r.returncode == 0 or not need_zero)
    stages[stage] = {"status": ("pass" if r.returncode == 0 else "fail") if done else "error",
                     "seconds": round(time.time() - t, 1), "exit_code": r.returncode, "report": report,
                     "report_written": fresh, "first_line": out[0] if out else None, "lines": out[1:20],
                     "stderr": (r.stderr or "")[-1000:] or None, "command": [os.path.basename(a) for a in args[:1]] + args[1:]}
    return done

if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    OUT = cfg.out_dir("tests")
    checks, created = [], []
    stages = {s: {"status": "not_run"} for s in STAGES}
    info = {"stages": stages}
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
        base = storyboard(None)
        src = make_source(math.ceil(max(C.tc_to_sec(r["end"]) for r in base["rows"])) + 2)
        info["source"] = C.probe_video(src)
        sbp = os.path.join(OUT, f"{NAME}.storyboard.json")
        C.write_json(sbp, storyboard(src))
        info["rows"] = len(base["rows"])

        # build
        log = {}
        t = time.time()
        ok, summary, lines = run_storyboard(sbp, f"test-{NAME}-{int(time.time())}", created, log, render=False)
        proj = pm.GetCurrentProject()
        mine = bool(ok and proj and proj.GetName() == log.get("project"))
        stages["build"] = {"status": "pass" if mine else "fail", "seconds": round(time.time() - t, 1), "summary": summary,
                           "lines": lines[:20], "timeline_seconds": (log.get("timeline") or {}).get("seconds"),
                           "fusion_seconds": (log.get("fusion") or {}).get("seconds")}
        if mine:
            sb = cfg.load_storyboard(sbp)
            rd = sb["render"]
            tl = proj.GetCurrentTimeline()
            total = sum(p["frames"] + p["hold_frames"] for p in log["rows"])
            info["frames"] = total
            limit = max(600, total / float(FPS) * 20)

            # render (完成版と同じ呼び方)
            t = time.time()
            rr = S.render(proj, sb, os.path.dirname(sb["output"]), S.render_stem(sb["output"]), limit)
            stages["render"] = {"status": "pass" if rr.get("done") else "fail", "seconds": round(time.time() - t, 1),
                                "render_seconds": rr.get("seconds"), "frames": total, "path": rr.get("path"),
                                "bytes": os.path.getsize(rr["path"]) if rr.get("done") and os.path.isfile(rr["path"]) else None,
                                "status_detail": rr.get("status")}

            # range_render (区間だけ。全編と同じ形式)
            t0 = tl.GetStartFrame()
            a = total // 2
            b = min(a + 6 * FPS - 1, total - 1)
            t = time.time()
            pr = render_job(proj, {"SelectAllFrames": False, "MarkIn": t0 + a, "MarkOut": t0 + b,
                                   "TargetDir": cfg.work_dir(NAME), "CustomName": f"{NAME}-range"},
                            rd["format"], rd["codec"], limit, interval=2)
            stages["range_render"] = {"status": "pass" if pr["done"] and pr.get("written") else "fail",
                                      "seconds": round(time.time() - t, 1), "render_seconds": pr.get("seconds"),
                                      "range": [a, b], "frames": b - a + 1, "path": pr["path"], "bytes": pr.get("bytes"),
                                      "set_settings": pr["set_settings"], "job_entry": pr.get("job_entry"),
                                      "status_detail": pr.get("status")}

            if stages["render"]["status"] == "pass":
                video = rr["path"]
                cf = [os.path.join(SCRIPTS, "check_final.py"), sbp, "--video", video]
                reports = cfg.out_dir("check-final")
                tool("check_final", cf + ["--name", f"{NAME}-nopixel"], os.path.join(reports, f"{NAME}-nopixel.json"))
                tool("check_final_pixel", cf + ["--pixel", "--name", f"{NAME}-pixel"],
                     os.path.join(reports, f"{NAME}-pixel.json"))
                m = (sb["rows"][0].get("masks") or [{}])[0]
                box = C.source_to_timeline(m, info["source"]["width"], info["source"]["height"], sb)
                area = ",".join(str(round(box[k])) for k in ("x", "y", "w", "h"))
                sheets = cfg.work_dir(NAME, "area-sheet")
                tool("area_sheet", [os.path.join(SCRIPTS, "area_sheet.py"), video, "--area", f"mask1={area}", "--out", sheets],
                     os.path.join(sheets, "area-sheet.json"), need_zero=True)
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        for s in STAGES:
            st = stages[s]
            # 組む・書き出し・area_sheet は pass、照合 (check_final) は報告が書かれた pass か fail を完了とみなす
            done = st["status"] == "pass" or (s.startswith("check_final") and st["status"] == "fail")
            check(f"{s}: 最後まで流れて秒数が取れた", done and st.get("seconds") is not None,
                  {k: st.get(k) for k in ("status", "seconds", "exit_code", "report_written", "first_line") if k in st})
        info["summary"] = {s: {"status": stages[s]["status"], "seconds": stages[s].get("seconds")} for s in STAGES} | {
            "timeline_seconds": stages["build"].get("timeline_seconds"), "fusion_seconds": stages["build"].get("fusion_seconds")}
        code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=len(STAGES) + 1)
    sys.exit(code)
