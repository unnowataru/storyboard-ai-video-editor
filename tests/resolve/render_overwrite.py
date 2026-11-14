"""書き出し先に既にファイルがあるとき、完成版と仮組みの書き出しが実際にどう動くか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/render_overwrite.py
(Resolve Studio を起動しておく。結果は 出力先/tests/render_overwrite.json。使い捨てのファイルと書き出しは
作業フォルダー/render_overwrite/run-<時刻> に残る)

組むもの: 機密の無い合成の素材 (render_range.py と同じ testsrc2 の 10 秒) を、sb2resolve.py で 1 行 2 秒の構成表に組む
(書き出しはしない)。書き出しの形式は構成表の既定 (config の storyboard.render)。作ったプロジェクト (test-render_overwrite-*) は
最後に閉じて消し、始めに開いていたプロジェクトを開き直す。
既存のファイルは、このテストがその場で作る使い捨てのもの (FFmpeg の単色の 1 秒の動画か、0 バイトのファイル)。
作った後に更新時刻を 1 日前にして、書き出しの前後で 有無・大きさ・更新時刻・作成時刻・SHA-256 と、フォルダーの中身を記録する。

場合と、docs に書いてある動き (sb2resolve.py の --render・--render-anyway・--overwrite と render_via_temp() の説明):
- a1 完成版 (sb2resolve.run に --render と --output を渡す。CLI と同じ入口)、宛先に動画がある → 偽を返し、Resolve に接続する前に
  止まり (common.connect_resolve が呼ばれない)、宛先は変わらない
- a2 完成版の入口、宛先に 0 バイトのファイルがある → 同じく接続する前に止まり、宛先は変わらない
- a3 完成版の書き出し (sb2resolve.render_via_temp に overwrite。--render --overwrite と同じ呼び方)、宛先に動画がある →
  宛先が今回の書き出しに置き換わり (フレーム数がタイムラインと同じ)、別名のファイルも一時ディレクトリも残らない
- a4 a3 と同じで、宛先が 0 バイトのファイル → 同じく置き換わる
- b0 仮組み (sb2resolve.run に --render-anyway と --output を渡す)、宛先がある → 接続する前に止まり、宛先は変わらない
- b1 仮組みの書き出し (sb2resolve.render_via_temp) を、宛先がある状態で直接呼ぶ (b0 の確認の後に宛先ができた場合) →
  done が偽、既存の宛先は変わらない、書き出しは一時ディレクトリに残り path がそれを指す
- b2 仮組みの書き出し、宛先が無い → 宛先に置かれ (done が真、path が宛先)、一時ディレクトリは消える (消せなくてもよい)
期待を決め打ちにせず、各場合の観測 (outcome: 上書き・別名・失敗・変わらない・置いた など) を info の cases に残し、
それが上の docs の動きと一致するかを check にする。合格は、組めること・7 つの場合が docs と一致すること・片付けができること。
"""
import argparse
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
import sb2resolve as S  # noqa: E402
from resolve_helpers import (finish_projects, frame_md5, make_check, make_source, require_lock,  # noqa: E402
                             run_storyboard)

NAME = "render_overwrite"
DAY = 86400


def snap(path):
    """ファイルの有無・大きさ・更新時刻・作成時刻 (Windows の st_ctime)・SHA-256。"""
    if not os.path.isfile(path):
        return {"exists": False}
    st = os.stat(path)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {"exists": True, "bytes": st.st_size, "mtime": st.st_mtime, "ctime": st.st_ctime, "sha256": digest}


def listing(d):
    """フォルダーの下の全部のファイル (d からの相対パス → 大きさ)。"""
    out = {}
    for root, _, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, d)] = os.path.getsize(p)
    return out


def make_existing(path, kind):
    """使い捨ての既存のファイルを作り、更新時刻を 1 日前にする。kind は video (単色の 1 秒の動画) か empty (0 バイト)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if kind == "video":
        C.run_tool("ffmpeg", ["-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                              "-f", "mp4", path])
    else:
        open(path, "wb").close()
    old = time.time() - DAY
    os.utime(path, (old, old))
    return snap(path)


def frames_of(path):
    try:
        return len(frame_md5(path))
    except RuntimeError as e:
        return f"読めない ({str(e)[-200:]})"


def outcome(before, after, new_files, done):
    """観測をまとめた言葉: 上書き (宛先の中身と更新時刻が変わり、ほかにファイルができない)・別名 (宛先は変わらず、別のファイルができた)・
    変わらない (宛先も変わらず、ほかにもできない)・失敗 (書き出しが終わらない)・その他。"""
    changed = after.get("sha256") != before.get("sha256") or after.get("mtime") != before.get("mtime")
    if not done:
        return "失敗" + ("、宛先が変わった" if changed else "")
    if changed and not new_files:
        return "上書き"
    if not changed and new_files:
        return "別名"
    if not changed:
        return "変わらない"
    return "その他"


def overwrite_case(key, kind):
    """完成版の書き出し (sb2resolve.render_via_temp に overwrite) を、宛先に kind の既存のファイルがある状態で流す。"""
    d = os.path.join(DIR, key)
    dest = os.path.join(d, f"{key}.{fmt}")
    before = make_existing(dest, kind)
    files0 = listing(d)
    rr = S.render_via_temp(proj, sb, dest, limit, "完成版", overwrite=True)
    after = snap(dest)
    new_files = sorted(set(listing(d)) - set(files0))
    left_dirs = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
    obs = {"kind": kind, "dest": dest, "before": before, "after": after, "new_files": new_files, "temp_dirs_left": left_dirs,
           "render": rr, "frames": frames_of(dest) if after["exists"] else None, "timeline_frames": total,
           "outcome": outcome(before, after, new_files, rr.get("done"))}
    obs["outcome"] += "" if obs["frames"] == total or obs["outcome"] != "上書き" else " (フレーム数が合わない)"
    obs["outcome"] += "、一時ディレクトリが残った" if left_dirs else ""
    info["cases"][key] = obs
    check(f"{key} 完成版を --overwrite で、宛先に{'動画' if kind == 'video' else '0 バイトのファイル'}がある: 今回の書き出しに置き換わり、"
          "別名のファイルも一時ディレクトリも残らない (sb2resolve.render_via_temp の説明)",
          obs["outcome"] == "上書き" and rr.get("path") == dest, obs["outcome"], "上書き")


def refused_case(key, kind, mode):
    """入口 (sb2resolve.run に --render か --render-anyway と --output) を、宛先に kind の既存のファイルがある状態で流す。"""
    final = mode == "render"
    dest = os.path.join(DIR, key, f"{key}.{fmt}" if final else f"{key}-preview.{fmt}")
    before = make_existing(dest, kind)
    args = argparse.Namespace(storyboard=sbp, source=None, name=f"test-{NAME}-{key}-{int(time.time())}", render=final,
                              render_anyway=not final, output=dest, dry_run=False)
    log = {}
    # 接続する前に止まったかは、connect_resolve が呼ばれたかで見る (この間だけ差し替え、finally で戻す)
    called = []
    real = C.connect_resolve

    def watch(*a, **k):
        called.append(True)
        return real(*a, **k)
    C.connect_resolve = watch
    try:
        ok, summary, lines = S.run(args, log)
    finally:
        C.connect_resolve = real
        created.append(log.get("project"))   # 拒否しなかったら作られるので、消す側に入れておく
    after = snap(dest)
    refused = not ok and not called
    obs = {"dest": dest, "before": before, "after": after, "ok": ok, "summary": summary, "lines": lines,
           "project": log.get("project"), "connected": bool(called),
           "outcome": ("接続する前に止まった" if refused else "接続した" if called else "止まらない")
           + ("" if after == before else "、宛先が変わった")}
    info["cases"][key] = obs
    what = "完成版 (--render --output)" if final else "仮組み (--render-anyway --output)"
    check(f"{key} {what}、宛先に{'動画' if kind == 'video' else '0 バイトのファイル'}がある: Resolve に接続する前に拒否し、"
          f"宛先は変わらない (--{mode.replace('_', '-')} の説明)", refused and after == before, obs["outcome"], "接続する前に止まった")


def preview_case(key, existing):
    """仮組みの書き出し (sb2resolve.render_via_temp) を直接呼ぶ。existing なら宛先に動画を作っておく。"""
    d = os.path.join(DIR, key)
    dest = os.path.join(d, f"{key}-preview.{fmt}")
    before = make_existing(dest, "video") if existing else (os.makedirs(d, exist_ok=True) or snap(dest))
    files0 = listing(d)
    rr = S.render_via_temp(proj, sb, dest, limit)
    after = snap(dest)
    files1 = listing(d)
    temp = rr.get("path") if rr.get("path") and rr.get("path") != dest else None
    temp_dirs = sorted({x.split(os.sep)[0] for x in files1 if os.sep in x} |
                       {x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))})
    obs = {"dest": dest, "before": before, "after": after, "new_files": sorted(set(files1) - set(files0)), "render": rr,
           "temp_file": temp, "temp_exists": bool(temp) and os.path.isfile(temp),
           "temp_frames": frames_of(temp) if temp and os.path.isfile(temp) else None,
           "dest_frames": frames_of(dest) if after["exists"] and after != before else None,
           "temp_dirs_left": temp_dirs, "timeline_frames": total}
    if existing:
        kept = after == before
        obs["outcome"] = ("宛先は変わらない" if kept else "宛先が変わった") + ("、一時ファイルに残した" if obs["temp_exists"] else "")
        check(f"{key} 仮組みの書き出し、宛先がある: done が偽、宛先は変わらない、書き出しは一時ファイルに残り path がそれを指す "
              "(sb2resolve.render_via_temp の説明)", not rr.get("done") and kept and obs["temp_exists"]
              and obs["temp_frames"] == total, obs["outcome"], "宛先は変わらない、一時ファイルに残した")
    else:
        placed = rr.get("done") and rr.get("path") == dest and after.get("exists")
        obs["outcome"] = ("置いた" if placed else "置けない") + ("、一時ディレクトリが残った" if temp_dirs else "")
        check(f"{key} 仮組みの書き出し、宛先が無い: 宛先に置かれる (done が真、path が宛先、フレーム数がタイムラインと同じ) "
              "(sb2resolve.render_via_temp の説明。一時ディレクトリは消せなくてもよい)",
              placed and obs["dest_frames"] == total, obs["outcome"], "置いた")
    info["cases"][key] = obs


if __name__ == "__main__":
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    W, H, FPS = look["timeline"]["width"], look["timeline"]["height"], int(look["fps"])
    OUT = cfg.out_dir("tests")
    DIR = cfg.work_dir(NAME, f"run-{int(time.time())}")
    checks, info, created = [], {"cases": {}}, []
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
    info["dir"] = DIR
    try:
        src = make_source(cfg, W, H, FPS)
        sbp = os.path.join(OUT, f"{NAME}.storyboard.json")
        C.write_json(sbp, {"source": src, "output": os.path.join(DIR, "final.mp4"), "fps": FPS,
                           "timeline": {"width": W, "height": H},
                           "rows": [{"start": 0, "end": 2, "seconds": 2, "caption": "上書きの試験"}]})
        log = {}
        ok, summary, lines = run_storyboard(sbp, f"test-{NAME}-{int(time.time())}", created, log, render=False)
        proj = pm.GetCurrentProject()
        mine = bool(ok and proj and proj.GetName() == log.get("project"))
        check("sb2resolve で組める", mine, summary, lines)
        if mine:
            sb = cfg.load_storyboard(sbp)
            fmt = sb["render"]["format"]
            total = sum(p["frames"] + p["hold_frames"] for p in log["rows"])
            limit = max(600, total / float(FPS) * 20)
            info["render_format"] = sb["render"]
            refused_case("a1", "video", "render")
            refused_case("a2", "empty", "render")
            refused_case("b0", "video", "render_anyway")
            # 止まらずに別のプロジェクトが開いていても、組んだプロジェクトで書き出す
            if pm.GetCurrentProject().GetName() != proj.GetName():
                proj = pm.LoadProject(log["project"])
            overwrite_case("a3", "video")
            overwrite_case("a4", "empty")
            preview_case("b1", existing=True)
            preview_case("b2", existing=False)
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=9)
    sys.exit(code)
