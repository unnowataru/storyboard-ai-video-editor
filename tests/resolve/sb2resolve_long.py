"""sb2resolve.py が、長い素材 (4K、30 fps、108001 フレーム = 約 60 分の素材に 50 行) を現実的な時間で組めるか。

機密の無い 4K のテストパターン (sb2resolve_4k.py が作る市松の 45 秒) を繰り返して 108001 フレームの素材を作業フォルダーに作り、
素材の頭から最後までに散らした 50 行 (等速と 10 倍速、テロップ、肩テロップ、モザイク、keys で動く黒塗り。最後の行は素材の
最後まで) を組んで書き出し、check_final.py で照らし合わせる。かかった時間 (組む・Fusion・書き出し) を記録する。
作ったプロジェクトは最後に閉じて消し、始めに開いていたプロジェクトを開き直す。

使い方: python scripts/resolve_lock.py run --owner 名前 -- python tests/resolve/sb2resolve_long.py   (素材が無ければ先に python tests/resolve/sb2resolve_4k.py を流す)
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import check_final as CF  # noqa: E402
import common as C  # noqa: E402
import sb2resolve as S  # noqa: E402
from resolve_helpers import require_lock  # noqa: E402

cfg = C.config()
OUT = cfg.out_dir("tests")
NAME = "sb2resolve_long"
FRAMES, FPS = 108001, 30


def make_source():
    short = os.path.join(cfg.work_dir("test-media"), "testsrc-4k-checker-bt709.mp4")
    path = os.path.join(cfg.work_dir("test-media"), "testsrc-4k-long-bt709.mp4")   # 色は BT.709 (short のまま符号化し直す)
    if not os.path.isfile(path):
        if not os.path.isfile(short):
            sys.exit(f"{short} が無い。先に python tests/resolve/sb2resolve_4k.py を流す")
        C.run_tool("ffmpeg", ["-stream_loop", "-1", "-i", short, "-frames:v", str(FRAMES), "-c:v", "libx264", "-preset",
                              "ultrafast", "-tune", "stillimage", "-crf", "20", "-pix_fmt", "yuv420p", *C.BT709_TAGS, path])
    return path


def storyboard(src):
    end_all = FRAMES / FPS
    rows = []
    for i in range(50):
        start = round(i * 70.0, 3)
        fast = i % 3 == 1
        end = min(start + (40 if fast else 4), end_all)
        r = {"start": start, "end": end, "caption": f"{i + 1} 行目", "seconds": 4,
             "masks": [{"type": "mosaic", "x": 200, "y": 200, "w": 600, "h": 200}]}
        if fast:
            r["badge"] = "10 倍速"
        if i % 5 == 0:
            r["masks"].append({"type": "black", "x": 1000, "y": 1000, "w": 300, "h": 100,
                               "keys": [{"t": 0, "x": 1000, "y": 1000, "w": 300, "h": 100},
                                        {"t": 3, "x": 2000, "y": 1200, "w": 300, "h": 100}]})
        rows.append(r)
    # 最後の行は素材の最後まで (GetSourceEndFrame が少なく出る場合も含めて照らし合わせる)
    rows[-1].update(start=round(end_all - 4, 3), end=end_all)
    rows[-1].pop("badge", None)
    return {"source": src, "output": os.path.join(OUT, "sb2resolve_long.mp4"), "fps": FPS, "seconds_per_row": 4,
            "rows": rows}


if __name__ == "__main__":
    info, checks = {}, []
    require_lock()
    resolve = C.connect_resolve(quiet=True)
    path = os.path.join(OUT, f"{NAME}.json")
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    prev = cur.GetName() if cur else None
    log = {}
    try:
        src = make_source()
        info["source"] = C.probe_video(src)
        sbp = os.path.join(OUT, f"{NAME}.storyboard.json")
        C.write_json(sbp, storyboard(src))
        args = argparse.Namespace(storyboard=sbp, source=None, name=f"test-{NAME}-{int(time.time())}", render=True, dry_run=False,
                                  overwrite=True)
        t0 = time.time()
        ok, summary, lines = S.run(args, log)
        info["run"] = {"ok": ok, "summary": summary, "lines": lines, "seconds": round(time.time() - t0, 1),
                       "timeline_seconds": (log.get("timeline") or {}).get("seconds"),
                       "fusion_seconds": (log.get("fusion") or {}).get("seconds"),
                       "render_seconds": (log.get("render") or {}).get("seconds")}
        checks.append({"label": "50 行を組んで書き出せた", "ok": ok, "actual": summary, "expected": lines})
        if ok:
            sb = cfg.load_storyboard(sbp)
            sb["_path"] = sbp
            rep = CF.run(sb, log["render"]["path"], pm.GetCurrentProject().GetCurrentTimeline())
            bad = {c["item"] + (f"@{c['row']}" if c["row"] else ""): c["status"] for c in rep["checks"]
                   if c["status"] != "pass"}
            info["check_final"] = {"summary": rep["summary"], "not_pass": bad}
            checks.append({"label": "check_final.py の fail と unknown が 0", "ok": not bad, "actual": bad, "expected": {}})
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        checks.append({"label": "途中で例外が出ない", "ok": False, "actual": str(e), "expected": None})
    finally:
        done = {}
        name = log.get("project")
        if name:
            p = pm.GetCurrentProject()
            if p and p.GetName() == name:
                pm.CloseProject(p)
            done[name] = bool(pm.DeleteProject(name))
        if prev:
            done["reopened " + prev] = bool(pm.LoadProject(prev))
        info["cleanup"] = done
        C.write_json(path, {"name": NAME, "info": info, "checks": checks})
    bad = [c for c in checks if not c["ok"]]
    out = [f"NG {c['label']}  (結果 {c['actual']!r}、期待 {c['expected']!r})" for c in bad]
    if not all(done.values()):
        out.append(f"片付けか元のプロジェクトの開き直しに失敗した ({done})")
    r = info.get("run") or {}
    sys.exit(C.report(not bad and all(done.values()),
                      f"{NAME}: {len(checks) - len(bad)}/{len(checks)} が期待どおり (全体 {r.get('seconds')} 秒、"
                      f"タイムライン {r.get('timeline_seconds')} 秒、Fusion {r.get('fusion_seconds')} 秒、"
                      f"書き出し {r.get('render_seconds')} 秒)", path, out))
