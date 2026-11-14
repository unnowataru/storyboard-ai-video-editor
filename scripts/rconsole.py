"""Resolve Studio に外から接続して、渡した Python を実行する小さなスクリプト。調べ物用。

使い方 (Resolve を使うので resolve_lock.py run で包む):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/rconsole.py <実行する .py>
resolve / pm / proj / mp / tl と、設定 (cfg、scripts/common.py の Config) を用意してから exec する。
渡したファイルの __file__ はそのファイル自身になる。

注意: proj / tl は Resolve で今開いているプロジェクトとタイムラインそのもの。渡したファイルが
値を変えると、そのプロジェクトが書き換わる (保存の操作をしなくても Resolve の中では変わる)。
調べるときは読むだけにするか、テスト用の新しいプロジェクトを開いてから動かす。
"""
import argparse
import os
import sys

import common as C


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="実行する .py")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="渡した .py の sys.argv[1:] にする引数")
    a = ap.parse_args(argv)
    # 引数を読んでから接続する (--help と引数の誤りでは Resolve に接続せず、排他のロックも見ない)
    resolve = C.connect_or_exit()
    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject()
    mp = proj.GetMediaPool() if proj else None
    tl = proj.GetCurrentTimeline() if proj else None
    path = os.path.abspath(a.script)
    sys.argv = [a.script, *a.args]
    with open(path, encoding="utf-8") as f:
        src = f.read()
    exec(compile(src, path, "exec"), {"__name__": "__main__", "__file__": path, "C": C, "cfg": C.config(),
                                      "resolve": resolve, "pm": pm, "proj": proj, "mp": mp, "tl": tl})


if __name__ == "__main__":
    main()
