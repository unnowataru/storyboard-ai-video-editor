"""構成表から、Resolve を使わずに簡易版を作るツール (試作)。CPU だけで動く。

使い方: python scripts/sb2ffmpeg.py 構成表.json [--source 素材] [--out 書き出し先の JSON]

- 構成表の点検は sb2resolve.py と同じ (sb2resolve.problems・auto_dst_problems)。誤りがあれば何も書かない
- 組み立ては sb2resolve.build_and_render をそのまま流し、Resolve の代わりに resolve_stand_in が呼び出しを受ける。
  Resolve には接続しないので、Resolve の排他 (resolve_lock.py) で包まなくてよい
- 組み立て内容の JSON (既定は 出力先 (config の out_dir) の sb2ffmpeg\\<構成表の名前>.build.json) を書き、
  その組み立て内容を FFmpeg で描いた簡易版 (sb2ffmpeg_render.py) を同じ場所の <構成表の名前>.mp4 に書く
- 簡易版は完成版に近いが同じではない: マスクは種類によらず少し広げた黒塗り、テロップは Pillow で描いた文字、
  ズームは FFmpeg の zoompan。描けない部品は描かずに警告する。音は入れない
"""
import argparse
import os
import sys
import time
from types import SimpleNamespace

import common as C
import resolve_stand_in as R
import sb2ffmpeg_render as D
import sb2resolve as S


def build(sb, rows, info, name):
    """組み立てを流し、(成否, 行, 組み立て内容) を返す。info は素材ごとの ffprobe の値 (sb2resolve.probe_sources)。
    build_and_render の要約は組み立て内容の build に残す"""
    proj = R.Project(info, sb["fps"])
    log = {"settings": {"warnings": []}}   # build_and_render が読むのは設定の警告だけ (Resolve の設定は無い)
    args = SimpleNamespace(render=False, render_anyway=False, rows_sel=None, overwrite=False)
    ok, summary, lines = S.build_and_render(args, log, R.ProjectManager(), proj, proj.GetMediaPool(), sb, rows, name, name)
    spec = R.dump(proj, sb)
    spec["build"] = {"ok": ok, "summary": summary, "lines": lines, "warnings": log.get("warnings", []),
                     "auto": log.get("auto", []), "fusion": log.get("fusion", {}).get("rows", {})}
    return ok, lines, spec


def clobbers(outs, inputs):
    """書き出し先 (outs) のうち、構成表か素材 (inputs) と同じファイルの list。それらは書き換えない。
    同じかは samefile で決める (書き方の違い・junction・ハードリンクも同じとみなす)。まだ無い書き出し先は当たらない"""
    ins = [p for p in inputs if os.path.exists(p)]
    return [p for p in outs if os.path.exists(p) and any(os.path.samefile(p, i) for i in ins)]


def out_paths(storyboard, out=None):
    """(組み立て内容の JSON, 簡易版の動画) の書き出し先。動画は JSON の .build.json を .mp4 に変えた名前"""
    path = out or os.path.join(C.config().out_dir("sb2ffmpeg"),
                               os.path.splitext(os.path.basename(storyboard))[0] + ".build.json")
    return path, (path[:-len(".build.json")] if path.endswith(".build.json") else path) + ".mp4"


def run(args):
    """点検して組み立て内容を作る。(成否, 要約, 行, 組み立て内容 (誤りなら None)、構成表) を返す。"""
    cfg = C.config()
    sb = cfg.load_storyboard(args.storyboard, args.source)
    rows = S.plan(sb)
    info = S.probe_sources(rows)
    bad = S.problems(sb, rows, {k: v["duration"] for k, v in info.items()})
    bad += S.auto_dst_problems(sb, rows, {k: (v["width"], v["height"]) for k, v in info.items()}, C.caption_font(sb))
    if bad:
        return False, f"sb2ffmpeg: 構成表に誤り {len(bad)} 件 (何も書いていない)", bad, None, sb
    ok, lines, spec = build(sb, rows, info, S.base_name(sb, args.storyboard))
    spec["storyboard"] = os.path.abspath(args.storyboard)
    spec["made"] = time.strftime("%Y-%m-%d %H:%M:%S")
    n = sum(1 for c in spec["clips"] if c["fusion"])
    head = f"sb2ffmpeg: 組み立て内容 クリップ {len(spec['clips'])}、合成 {n}、{spec['timeline']['frames']} フレーム"
    return ok, head + ("" if ok else " (組み立てに NG がある)"), lines, spec, sb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("storyboard")
    ap.add_argument("--source", help="素材 (構成表の source より優先)")
    ap.add_argument("--jobs", type=int,
                    help="クリップを同時に書く数 (既定は config の sb2ffmpeg.jobs)")
    ap.add_argument("--threads",
                    help="1 本の FFmpeg のスレッドの数。auto (FFmpeg に任せる) か「読み込み,フィルター,書き出し」(例 3,1,1)。"
                         "既定は config の sb2ffmpeg.threads (sb2ffmpeg_bench.py --apply が環境ごとに書く)")
    ap.add_argument("--out", help="組み立て内容の JSON の書き出し先 (既定は 出力先の sb2ffmpeg\\<構成表の名前>.build.json)。"
                                  "簡易版は同じ場所に .build.json を .mp4 に変えた名前で書く")
    a = ap.parse_args()
    cfg = C.config().section("sb2ffmpeg")
    jobs, jobs_from = (a.jobs, "--jobs") if a.jobs is not None else (cfg["jobs"], "config の sb2ffmpeg.jobs")
    threads, threads_from = (a.threads, "--threads") if a.threads else (cfg["threads"], "config の sb2ffmpeg.threads")
    if not isinstance(jobs, int) or jobs < 1:
        return C.report(False, f"sb2ffmpeg: {jobs_from} は 1 以上の整数: {jobs}")
    try:
        plan = D.parse_threads(threads)
    except ValueError as e:
        return C.report(False, f"sb2ffmpeg: {threads_from} は {e}")
    ok, summary, lines, spec, sb = run(a)
    if spec is None:
        return C.report(False, summary, None, lines)
    path, video = out_paths(a.storyboard, a.out)
    same = clobbers([path, video], [a.storyboard] + [m["path"] for m in spec["media"]])
    if same:
        return C.report(False, f"sb2ffmpeg: 書き出し先が構成表か素材と同じ (何も書いていない): {'、'.join(same)}")
    C.write_json(path, spec)
    if ok:
        t = time.time()
        spec["render"] = {"video": video, "jobs": jobs, "threads": str(threads),
                          "warnings": D.render(spec, float(sb["fusion_text_scale"]), video, jobs, plan)}
        took = spec["render"]["seconds"] = round(time.time() - t, 1)
        C.write_json(path, spec)   # 描いたときの警告も JSON に残す (画面には先頭の数行しか出ない)
        lines = lines + ["警告 " + w for w in spec["render"]["warnings"]]
        summary += (f"、簡易版 {video} ({took:.1f} 秒、動画の長さの {took / (spec['timeline']['frames'] / spec['timeline']['fps']):.2f} 倍、"
                    f"同時に {jobs} 本、スレッド {threads})")
    return C.report(ok, summary, path, lines)


if __name__ == "__main__":
    sys.exit(main())
