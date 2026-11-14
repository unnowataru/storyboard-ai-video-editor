"""テストで作った `test-` で始まるプロジェクトを消す。開いているものを消すときは、--reopen のプロジェクトを開いてから消す。

使い方 (Resolve を使うので resolve_lock.py run で包む):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/cleanup_projects.py <名前か型>... [--reopen 名前] [--dry-run]

- <名前か型> はルートのフォルダーのプロジェクトの名前か、* ? [] を使った型 (例: test-sample*)。
  名前も型も `test-` で始まるものだけを受け付ける (テスト以外のプロジェクトを消さないため)。フォルダーの下のプロジェクトは扱わない
- --reopen のプロジェクトは、型に当たっても消さない。既定は無い
- 開いているプロジェクトは消せないので、それが消す対象なら、先に --reopen のプロジェクトを開いてから消す (保存はしない)。
  --reopen を指定していないか、そのプロジェクトがルートに無ければ、何も消さずに 1 で止める。
  開いているのが消す対象でなければ、プロジェクトを切り替えない
- 消した後に一覧を読み直し、残っていないことを確認する
- --dry-run: 消す対象を出すだけで、何も変えない
- どの名前・型にも当たらないものは、その旨を 1 行出す (消す物が無いのは失敗にしない)

結果: 画面に common.report の 1 行と、消した (--dry-run では消す) プロジェクト・当たらなかった型・終わりに開いているプロジェクト。
"""
import argparse
import fnmatch
import sys

import common as C

PREFIX = "test-"


def targets(names, patterns, keep):
    """ルートのプロジェクトの名前 names のうち、型 patterns のどれかに当たり、keep でないものを、names の順で返す。
    当たらなかった型の list も返す。"""
    hit = [n for n in names if n != keep and any(fnmatch.fnmatchcase(n, p) for p in patterns)]
    miss = [p for p in patterns if not any(n != keep and fnmatch.fnmatchcase(n, p) for n in names)]
    return hit, miss


def cleanup(pm, patterns, reopen=None, dry_run=False):
    pm.GotoRootFolder()
    names = list(pm.GetProjectListInCurrentFolder() or [])
    hit, miss = targets(names, patterns, reopen)
    cur = pm.GetCurrentProject()
    cur = cur.GetName() if cur else None
    lines = [f"当たらない: {p}" for p in miss]
    if dry_run:
        lines += [f"消す: {n}" for n in hit] + [f"開いている: {cur}"]
        return C.report(True, f"cleanup_projects --dry-run: {len(hit)} 件を消す (何も変えていない)", lines=lines,
                        max_lines=len(lines))
    if cur in hit:   # 開いているものは消せないので、先に --reopen を開く
        if not reopen:
            lines.append(f"開いている {cur} を消すには、代わりに開くプロジェクトを --reopen で指定する。何も消していない")
            return C.report(False, "cleanup_projects: 何も消さずに止めた", lines=lines, max_lines=len(lines))
        if reopen not in names or not pm.LoadProject(reopen):
            lines.append(f"{reopen} を開けない (ルートに無いか、開くのに失敗)。開いている {cur} を消せないので、何も消していない")
            return C.report(False, "cleanup_projects: 何も消さずに止めた", lines=lines, max_lines=len(lines))
        pm.GotoRootFolder()
    failed = [n for n in hit if not pm.DeleteProject(n)]
    left = set(pm.GetProjectListInCurrentFolder() or [])
    failed += [n for n in hit if n in left and n not in failed]   # 消せたと返しても残っているもの
    done = [n for n in hit if n not in failed]
    now = pm.GetCurrentProject()
    lines += [f"消した: {n}" for n in done] + [f"NG 消せない: {n}" for n in failed]
    lines.append(f"開いている: {now.GetName() if now else None}")
    return C.report(not failed, f"cleanup_projects: {len(done)}/{len(hit)} 件を消した", lines=lines, max_lines=len(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patterns", nargs="+", help=f"消すプロジェクトの名前か型 ({PREFIX} で始まる)")
    ap.add_argument("--reopen", help="開いているものを消すときに代わりに開く、消さないプロジェクト (既定は無し)")
    ap.add_argument("--dry-run", action="store_true", help="消す対象を出すだけ")
    a = ap.parse_args(argv)
    bad = [p for p in a.patterns if not p.startswith(PREFIX)]
    if bad:   # 接続する前に止める
        ap.error(f"{PREFIX} で始まらない名前・型は受け付けない: {', '.join(bad)}")
    pm = C.connect_or_exit().GetProjectManager()
    return cleanup(pm, a.patterns, a.reopen, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
