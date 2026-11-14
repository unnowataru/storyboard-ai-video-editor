"""Resolve のプロジェクトの一覧を出す・.drp に書き出す・素材のファイルがあるかを確認する。消す・名前を変える・つなぎ直す操作はしない。

使い方 (Resolve を使うので resolve_lock.py run で包む):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/projects.py list
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/projects.py export <名前>... --out <フォルダー>
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/projects.py check <名前>...

- <名前> は list が出す形 (ルートのフォルダーからの「フォルダー/プロジェクト」。ルートの直下ならプロジェクトの名前だけ)
- list: ルートのフォルダーから順にたどり、プロジェクトを 1 行ずつ出す。今開いているものに「(開いている)」を付ける。
  API からは開いているもののフォルダーが分からないので、同じ名前がほかのフォルダーにもあるときは付けずに、そのことを 1 行出す
- export: ProjectManager.ExportProject(名前, パス, True) で <フォルダー>/<プロジェクトの名前>-<YYYYMMDD-HHMM>.drp に書き出し
  (区切りごとに控えを残すため、名前に書き出した日時を付ける)、ファイルがあって大きさが 0 でないことを確認する。
  同じ名前のファイルが 1 つでも既にあれば、何も書き出さずに 1 で止める (上書きしない)。
  書き出されるのは保存済みの内容なので、開いているプロジェクトは先に Resolve で保存しておく
- check: 切り替える前に今のプロジェクトを保存する (保存できなければ切り替えずに 1 で止める)。プロジェクトを開き、メディアプールの全クリップ (ビンの下も) と、全タイムラインの映像・音声のトラックのクリップについて、
  素材のファイル (クリップの File Path) が実在するかを数える。見つからないものがあれば 1。File Path が空のもの
  (タイムライン、タイトル、生成したクリップ) は数えない。連番画像の素材は対象外 (File Path がファイルとして実在しない)。終わったら (途中で止まっても)、始めに開いていたプロジェクトを開き直す。開き直せなければ 1。
  始めに開いていたものと同じ名前がほかのフォルダーにもあるときは、開き直す先が決まらないので、どれも開かずに 1 で止める。
  Resolve は開いた直後のプロジェクトに古い「メディアオフライン」の表示を残すことがあるので、画面の表示ではなくこの結果で判断する
- どの下位コマンドも、終わるとプロジェクトマネージャーのフォルダーの位置は一番上 (ルート) に戻る

結果: 画面に common.report の 1 行と、見るべき行。check は出力先 (config の out_dir) の projects/check.json に全件を書く。
"""
import argparse
import os
import sys
import time

import common as C

KINDS = ("video", "audio")


def walk(pm, prefix=""):
    """今のフォルダーから下のプロジェクトを「フォルダー/名前」の並びで返す。終わったら同じフォルダーに戻る。"""
    out = [prefix + n for n in pm.GetProjectListInCurrentFolder() or []]
    for f in pm.GetFolderListInCurrentFolder() or []:
        if pm.OpenFolder(f):
            out += walk(pm, f"{prefix}{f}/")
            pm.GotoParentFolder()
    return out


def goto(pm, name):
    """名前 (「フォルダー/プロジェクト」) のフォルダーへ移り、プロジェクトの名前を返す。フォルダーが無ければ None。"""
    pm.GotoRootFolder()
    *folders, base = name.split("/")
    for f in folders:
        if not pm.OpenFolder(f):
            return None
    return base


def current_name(pm):
    p = pm.GetCurrentProject()
    return p.GetName() if p else None


def same_name(names, cur):
    """今開いているプロジェクトの名前 cur と同じ名前のものを、list の形の並びから返す (API からはフォルダーが分からない)。"""
    return [n for n in names if cur is not None and n.split("/")[-1] == cur]


def cmd_list(pm):
    cur = current_name(pm)
    pm.GotoRootFolder()
    names = walk(pm)
    hits = same_name(names, cur)
    lines = [n + (" (開いている)" if len(hits) == 1 and n == hits[0] else "") for n in names]
    if len(hits) > 1:
        lines.append(f"開いているのは「{cur}」だが、同じ名前が {len(hits)} つあり、どれかは分からない")
    return C.report(True, f"projects list: {len(names)} 件、データベース {pm.GetCurrentDatabase()}", lines=lines,
                    max_lines=len(lines))


def cmd_export(pm, names, out):
    stamp = time.strftime("%Y%m%d-%H%M")
    targets = [(n, os.path.join(out, f"{n.split('/')[-1]}-{stamp}.drp")) for n in names]
    paths = [p for _, p in targets]
    keys = [os.path.normcase(os.path.abspath(p)) for p in paths]   # Windows は大文字と小文字を区別しない
    stop = [f"既にある: {p}" for p in paths if os.path.exists(p)]
    stop += [f"同じファイルに書き出す名前が重なる: {', '.join(n for (n, _), k2 in zip(targets, keys) if k2 == k)}"
             for k in sorted(set(keys)) if keys.count(k) > 1]
    if stop:
        return C.report(False, "projects export: 書き出さずに止めた (上書きしない)", lines=stop, max_lines=len(stop))
    os.makedirs(out, exist_ok=True)
    lines, done = [], 0
    for n, path in targets:
        base = goto(pm, n)
        ok = bool(base) and bool(pm.ExportProject(base, path, True))
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        if ok and size > 0:
            done += 1
            lines.append(f"{n}: {path} ({size} バイト)")
        else:
            lines.append(f"NG {n}: " + ("フォルダーが無い" if not base else f"書き出しを確認できない (戻り値 {ok}、大きさ {size})"))
    pm.GotoRootFolder()
    return C.report(done == len(targets), f"projects export: {done}/{len(targets)} 件を {out} に書き出した",
                    lines=lines, max_lines=len(lines))


def pool_clips(folder, prefix=""):
    """メディアプールのフォルダーの下の全クリップを (ビンの場所, クリップ) で返す。"""
    out = [(prefix, c) for c in folder.GetClipList() or []]
    for sub in folder.GetSubFolderList() or []:
        out += pool_clips(sub, f"{prefix}{sub.GetName()}/")
    return out


def file_path(mpi):
    return (mpi.GetClipProperty("File Path") or "") if mpi else ""


def check_project(p):
    """プロジェクトの素材を数える。{media: {total, missing}, timeline: {total, missing}, missing: [...]} を返す。"""
    res = {"media": {"total": 0, "missing": 0}, "timeline": {"total": 0, "missing": 0}, "missing": []}

    def count(key, where, path):
        if not path:
            return
        res[key]["total"] += 1
        if not os.path.exists(path):
            res[key]["missing"] += 1
            res["missing"].append({"where": where, "path": path})

    for where, clip in pool_clips(p.GetMediaPool().GetRootFolder()):
        count("media", f"メディアプール {where}{clip.GetName()}", file_path(clip))
    for i in range(1, (p.GetTimelineCount() or 0) + 1):
        tl = p.GetTimelineByIndex(i)
        for kind in KINDS:
            for tr in range(1, (tl.GetTrackCount(kind) or 0) + 1):
                for it in tl.GetItemListInTrack(kind, tr) or []:
                    count("timeline", f"タイムライン {tl.GetName()} {kind} {tr}", file_path(it.GetMediaPoolItem()))
    return res


def cmd_check(pm, names):
    cur = current_name(pm)
    pm.GotoRootFolder()
    hits = same_name(walk(pm), cur)
    if len(hits) > 1:
        return C.report(False, f"projects check: 開いている「{cur}」と同じ名前のプロジェクトが {len(hits)} つあり、"
                               "終わった後に開き直す先が決まらないので、切り替えずに止めた", lines=hits, max_lines=len(hits))
    cur_path = hits[0] if hits else None
    if cur is not None and not pm.SaveProject():
        pm.GotoRootFolder()
        return C.report(False, f"projects check: 開いている「{cur}」を保存できないので、切り替えずに止めた")
    results, lines = {}, []
    reopened = False
    try:
        for n in names:
            base = goto(pm, n)
            p = pm.LoadProject(base) if base else None
            if not p:
                results[n] = None
                lines.append(f"NG {n}: 開けない (名前は list の形で渡す)")
                continue
            r = results[n] = check_project(p)
            m, t = r["media"], r["timeline"]
            lines.append(f"{'NG' if r['missing'] else 'OK'} {n}: メディアプール {m['total'] - m['missing']}/{m['total']}、"
                         f"タイムラインのクリップ {t['total'] - t['missing']}/{t['total']}")
            lines += [f"  見つからない: {x['where']}: {x['path']}" for x in r["missing"]]
    finally:   # 途中で例外が出ても、始めに開いていたプロジェクトに戻す
        if cur_path:   # 開き直せたかは、LoadProject の戻り値と開いたプロジェクトの名前で確認する
            base = goto(pm, cur_path)
            p = pm.LoadProject(base) if base else None
            reopened = bool(p) and p.GetName() == base
        pm.GotoRootFolder()
    bad = [n for n, r in results.items() if r is None or r["missing"]]
    if cur_path and not reopened:
        lines.insert(0, f"NG 始めに開いていた {cur_path} を開き直せなかった")
    path = os.path.join(C.config().out_dir("projects"), "check.json")
    C.write_json(path, {"reopened": cur_path if reopened else None,
                        "reopen_failed": cur_path if cur_path and not reopened else None, "projects": results})
    return C.report(not bad and (reopened or not cur_path),
                    f"projects check: {len(names) - len(bad)}/{len(names)} 件で素材がそろっている", path,
                    lines, max_lines=50)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="プロジェクトの一覧")
    e = sub.add_parser("export", help=".drp に書き出す (既にあるファイルは上書きしない)")
    e.add_argument("names", nargs="+")
    e.add_argument("--out", required=True, help="書き出し先のフォルダー")
    c = sub.add_parser("check", help="素材のファイルがあるかを確認する")
    c.add_argument("names", nargs="+")
    a = ap.parse_args(argv)

    pm = C.connect_or_exit().GetProjectManager()
    if a.cmd == "list":
        return cmd_list(pm)
    if a.cmd == "export":
        return cmd_export(pm, a.names, a.out)
    return cmd_check(pm, a.names)


if __name__ == "__main__":
    sys.exit(main())
